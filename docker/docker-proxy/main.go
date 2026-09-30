package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"os"
	"os/signal"
	"path"
	"regexp"
	"strings"
	"syscall"
)

type allowRule []string

type proxy struct {
	routes                []route
	allowRules            []allowRule
	allowedContainers     []string
	dockerSocket          string
	containerNamePattern  *regexp.Regexp
}

type route struct {
	pattern *regexp.Regexp
	handler http.HandlerFunc
}

func loadAllowList(filePath string) []allowRule {
	data, err := os.ReadFile(filePath)
	if err != nil {
		return nil
	}
	trimmed := strings.TrimSpace(string(data))
	if trimmed == "" {
		return nil
	}
	trimmed = strings.ReplaceAll(trimmed, "\r\n", "\n")
	trimmed = strings.ReplaceAll(trimmed, "\r", "\n")
	var rules []allowRule
	for _, part := range strings.Split(trimmed, "\n") {
		tokens := strings.Fields(strings.TrimSpace(part))
		if len(tokens) > 0 {
			rules = append(rules, allowRule(tokens))
		}
	}
	return rules
}

func matchRule(rule allowRule, cmd []string) bool {
	if len(rule) == 0 || len(cmd) == 0 || cmd[0] == "" {
		return false
	}
	for i, expected := range rule {
		if i >= len(cmd) {
			return false
		}
		actual := cmd[i]
		if i == 0 {
			actual = path.Base(actual)
		}
		if !matchToken(expected, actual) {
			return false
		}
	}
	return true
}

// matchToken treats only '*' as a wildcard, matching zero or more characters.
// Tokens are matched separately so wildcards cannot cross argument boundaries.
func matchToken(pattern, actual string) bool {
	parts := strings.Split(pattern, "*")
	if len(parts) == 1 {
		return pattern == actual
	}
	if !strings.HasPrefix(actual, parts[0]) {
		return false
	}
	actual = actual[len(parts[0]):]
	for _, part := range parts[1 : len(parts)-1] {
		index := strings.Index(actual, part)
		if index < 0 {
			return false
		}
		actual = actual[index+len(part):]
	}
	return strings.HasSuffix(actual, parts[len(parts)-1])
}

func loadContainerList(filePath string) []string {
	data, err := os.ReadFile(filePath)
	if err != nil {
		return nil
	}
	trimmed := strings.TrimSpace(string(data))
	if trimmed == "" {
		return nil
	}
	trimmed = strings.ReplaceAll(trimmed, "\r\n", "\n")
	trimmed = strings.ReplaceAll(trimmed, "\r", "\n")
	var containers []string
	for _, part := range strings.Split(trimmed, "\n") {
		s := strings.TrimSpace(part)
		if s != "" {
			containers = append(containers, s)
		}
	}
	return containers
}

func newProxy(rules []allowRule, allowedContainers []string, dockerSocket string) *proxy {
	p := &proxy{
		allowRules:        rules,
		allowedContainers: allowedContainers,
		dockerSocket:      dockerSocket,
	}

	prefix := `(?:v[\d.]+/)?`
	p.routes = []route{
		{regexp.MustCompile(`^/` + prefix + `_ping$`), p.passthrough},
		{regexp.MustCompile(`^/` + prefix + `version$`), p.passthrough},
		// Compose ps needs both the list and inspect endpoints, including unregistered containers.
		{regexp.MustCompile(`^/` + prefix + `containers/json$`), p.passthrough},
		{regexp.MustCompile(`^/` + prefix + `containers/[a-zA-Z0-9_.-]+/json$`), p.passthrough},
		{regexp.MustCompile(`^/` + prefix + `containers/[a-zA-Z0-9_.-]+/exec$`), p.handleExecCreate},
		{regexp.MustCompile(`^/` + prefix + `exec/[a-zA-Z0-9]+/start$`), p.handleExecStart},
		{regexp.MustCompile(`^/` + prefix + `exec/[a-zA-Z0-9]+/json$`), p.passthrough},
		{regexp.MustCompile(`^/` + prefix + `exec/[a-zA-Z0-9]+/resize$`), p.passthrough},
	}
	p.containerNamePattern = regexp.MustCompile(`/containers/([a-zA-Z0-9_.-]+)/`)
	return p
}

func (p *proxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	for _, rt := range p.routes {
		if rt.pattern.MatchString(r.URL.Path) {
			rt.handler(w, r)
			return
		}
	}
	writeJSONError(w, http.StatusForbidden, "この操作は許可されていません。コンテナの一覧・詳細の参照と、許可されたコマンドの実行のみ利用できます。")
}

func writeJSONError(w http.ResponseWriter, status int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(map[string]string{"message": message})
}

func (p *proxy) dialDocker() (net.Conn, error) {
	return net.Dial("unix", p.dockerSocket)
}

func (p *proxy) reverseProxy() *httputil.ReverseProxy {
	return &httputil.ReverseProxy{
		Director: func(req *http.Request) {
			req.URL.Scheme = "http"
			req.URL.Host = "docker"
		},
		Transport: &http.Transport{
			DialContext: func(_ context.Context, _, _ string) (net.Conn, error) {
				return p.dialDocker()
			},
		},
	}
}

func (p *proxy) passthrough(w http.ResponseWriter, r *http.Request) {
	p.reverseProxy().ServeHTTP(w, r)
}

// containerAccess returns the resolved name and whether execution is allowed.
// Resolve IDs even with an empty allow list so errors can suggest the actual name.
func (p *proxy) containerAccess(nameOrID string) (string, bool) {
	// Direct match by name
	for _, allowed := range p.allowedContainers {
		if nameOrID == allowed {
			return nameOrID, true
		}
	}
	// If it looks like a container ID, resolve to name via Docker API
	if isHexString(nameOrID) && len(nameOrID) >= 12 {
		if name := p.resolveContainerName(nameOrID); name != "" {
			for _, allowed := range p.allowedContainers {
				if name == allowed {
					return name, true
				}
			}
			return name, false
		}
	}
	return nameOrID, false
}

func isHexString(s string) bool {
	for _, c := range s {
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')) {
			return false
		}
	}
	return len(s) > 0
}

// resolveContainerName queries Docker API to get the container name from an ID.
func (p *proxy) resolveContainerName(id string) string {
	conn, err := p.dialDocker()
	if err != nil {
		return ""
	}
	defer conn.Close()

	reqStr := fmt.Sprintf("GET /containers/%s/json HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n", id)
	conn.Write([]byte(reqStr))

	req, _ := http.NewRequest("GET", "/", nil)
	resp, err := http.ReadResponse(bufio.NewReader(conn), req)
	if err != nil || resp.StatusCode != http.StatusOK {
		return ""
	}
	defer resp.Body.Close()

	var info struct {
		Name string `json:"Name"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&info); err != nil {
		return ""
	}
	return strings.TrimPrefix(info.Name, "/")
}

// extractContainerName extracts the container name/ID from a URL path like /containers/{name}/exec
func (p *proxy) extractContainerName(path string) string {
	matches := p.containerNamePattern.FindStringSubmatch(path)
	if len(matches) >= 2 {
		return matches[1]
	}
	return ""
}

func (p *proxy) handleExecCreate(w http.ResponseWriter, r *http.Request) {
	// Check container access
	nameOrID := p.extractContainerName(r.URL.Path)
	name, containerAllowed := p.containerAccess(nameOrID)
	if !containerAllowed {
		guidance := fmt.Sprintf(".aicontainer に docker-proxy-containers=%s を追加し、aicontainer を起動し直してください。", name)
		if isHexString(name) && len(name) >= 12 {
			guidance = ".aicontainer の docker-proxy-containers に対象コンテナの実際の名前を追加し、aicontainer を起動し直してください。"
		}
		writeJSONError(w, http.StatusForbidden, fmt.Sprintf("コンテナ %q でのコマンド実行は許可されていません。%s", name, guidance))
		return
	}

	if len(p.allowRules) == 0 {
		writeJSONError(w, http.StatusForbidden, "実行できるコマンドが設定されていません。.aicontainer の docker-proxy-allow に許可するコマンドを追加し、aicontainer を起動し直してください。")
		return
	}

	body, err := io.ReadAll(r.Body)
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		fmt.Fprint(w, `{"message":"Bad Request: failed to read body"}`)
		return
	}

	var parsed struct {
		Cmd []string `json:"Cmd"`
	}
	if err := json.Unmarshal(body, &parsed); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		fmt.Fprint(w, `{"message":"Bad Request: invalid JSON body"}`)
		return
	}

	if len(parsed.Cmd) == 0 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		fmt.Fprint(w, `{"message":"Bad Request: missing Cmd"}`)
		return
	}

	allowed := false
	for _, rule := range p.allowRules {
		if matchRule(rule, parsed.Cmd) {
			allowed = true
			break
		}
	}

	if !allowed {
		cmdStr := path.Base(parsed.Cmd[0])
		if len(parsed.Cmd) > 1 {
			cmdStr += " " + strings.Join(parsed.Cmd[1:], " ")
		}
		var ruleStrs []string
		for _, rule := range p.allowRules {
			ruleStrs = append(ruleStrs, strings.Join(rule, " "))
		}
		writeJSONError(w, http.StatusForbidden, fmt.Sprintf("コマンド %q は許可されていません。.aicontainer の docker-proxy-allow を確認してください。許可されたコマンド: %s", cmdStr, strings.Join(ruleStrs, ", ")))
		return
	}

	// Allowed: forward to Docker with reconstructed body
	r.Body = io.NopCloser(strings.NewReader(string(body)))
	r.ContentLength = int64(len(body))
	p.reverseProxy().ServeHTTP(w, r)
}

func (p *proxy) handleExecStart(w http.ResponseWriter, r *http.Request) {
	// Read request body BEFORE hijacking (after hijack, r.Body is invalid)
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, `{"message":"Failed to read request body"}`, http.StatusBadRequest)
		return
	}

	// Hijack client connection to get raw TCP access
	hijacker, ok := w.(http.Hijacker)
	if !ok {
		http.Error(w, `{"message":"Server does not support hijacking"}`, http.StatusInternalServerError)
		return
	}
	clientConn, clientBuf, err := hijacker.Hijack()
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"message":"Hijack failed: %s"}`, err), http.StatusInternalServerError)
		return
	}
	defer clientConn.Close()

	// Connect to Docker socket
	dockerConn, err := p.dialDocker()
	if err != nil {
		fmt.Fprintf(clientConn, "HTTP/1.1 502 Bad Gateway\r\nContent-Type: application/json\r\n\r\n{\"message\":\"Failed to connect to Docker\"}")
		return
	}
	defer dockerConn.Close()

	// Manually write HTTP request to Docker (can't use r.Write after hijack)
	reqLine := fmt.Sprintf("%s %s HTTP/1.1\r\n", r.Method, r.URL.RequestURI())
	dockerConn.Write([]byte(reqLine))
	r.Header.Set("Host", r.Host)
	r.Header.Write(dockerConn)
	fmt.Fprintf(dockerConn, "Content-Length: %d\r\n", len(body))
	dockerConn.Write([]byte("\r\n"))
	dockerConn.Write(body)

	// Pure bidirectional byte copy — no HTTP response parsing at all.
	// Docker's response (headers + stream) flows as raw bytes to the client.
	// The Docker CLI handles parsing on its end.
	done := make(chan struct{}, 2)

	// Docker -> Client
	go func() {
		defer func() { done <- struct{}{} }()
		n, err := io.Copy(clientConn, dockerConn)
		log.Printf("exec/start docker->client: %d bytes, err=%v", n, err)
		// Docker finished sending; half-close client write side
		if c, ok := clientConn.(interface{ CloseWrite() error }); ok {
			c.CloseWrite()
		}
	}()

	// Client -> Docker
	go func() {
		defer func() { done <- struct{}{} }()
		n, err := io.Copy(dockerConn, clientBuf.Reader)
		log.Printf("exec/start client->docker: %d bytes, err=%v", n, err)
		// Client finished sending; half-close Docker write side
		if c, ok := dockerConn.(interface{ CloseWrite() error }); ok {
			c.CloseWrite()
		}
	}()

	// Wait for BOTH directions to complete
	<-done
	<-done
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func main() {
	allowPath := getEnv("DOCKER_PROXY_ALLOW_FILE", "/etc/docker-proxy/allow.txt")
	containersPath := getEnv("DOCKER_PROXY_CONTAINERS_FILE", "/etc/docker-proxy/containers.txt")
	listenPath := getEnv("DOCKER_PROXY_LISTEN", "/var/run/docker-proxy/docker.sock")
	dockerSocket := getEnv("DOCKER_PROXY_UPSTREAM", "/var/run/docker.sock")

	rules := loadAllowList(allowPath)
	log.Printf("Loaded %d allow rules from %s", len(rules), allowPath)
	for i, rule := range rules {
		log.Printf("  rule[%d]: %s", i, strings.Join(rule, " "))
	}

	containers := loadContainerList(containersPath)
	if len(containers) > 0 {
		log.Printf("Loaded %d allowed containers from %s", len(containers), containersPath)
		for i, c := range containers {
			log.Printf("  container[%d]: %s", i, c)
		}
	} else {
		log.Printf("No allowed containers configured (command execution denied for all containers)")
	}

	// Remove stale socket
	os.Remove(listenPath)

	listener, err := net.Listen("unix", listenPath)
	if err != nil {
		log.Fatalf("Failed to listen on %s: %v", listenPath, err)
	}
	defer listener.Close()

	// Make socket accessible
	if err := os.Chmod(listenPath, 0666); err != nil {
		log.Fatalf("Failed to chmod socket: %v", err)
	}

	log.Printf("Listening on %s, upstream %s", listenPath, dockerSocket)

	p := newProxy(rules, containers, dockerSocket)
	server := &http.Server{Handler: p}

	// Graceful shutdown
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)
		<-sigCh
		log.Println("Shutting down...")
		server.Shutdown(context.Background())
	}()

	if err := server.Serve(listener); err != nil && err != http.ErrServerClosed {
		log.Fatalf("Server error: %v", err)
	}
}
