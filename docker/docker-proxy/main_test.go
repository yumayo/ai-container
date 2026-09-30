package main

import (
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestMatchRule(t *testing.T) {
	tests := []struct {
		name string
		rule allowRule
		cmd  []string
		want bool
	}{
		{"all commands", allowRule{"*"}, []string{"python", "script.py"}, true},
		{"all commands with full path", allowRule{"*"}, []string{"/usr/bin/python"}, true},
		{"empty command list", allowRule{"*"}, nil, false},
		{"empty executable", allowRule{"*"}, []string{""}, false},
		{"empty rule", nil, []string{"python"}, false},
		{"literal command", allowRule{"node"}, []string{"node", "script.js"}, true},
		{"literal full path", allowRule{"node"}, []string{"/usr/bin/node", "script.js"}, true},
		{"literal remains exact", allowRule{"node"}, []string{"nodejs"}, false},
		{"literal prefix arguments", allowRule{"npx", "playwright"}, []string{"npx", "playwright", "test"}, true},
		{"literal argument denial", allowRule{"npx", "playwright"}, []string{"npx", "webpack"}, false},
		{"command suffix wildcard", allowRule{"python*"}, []string{"/usr/bin/python3.12", "script.py"}, true},
		{"wildcard matches zero characters", allowRule{"python*"}, []string{"python"}, true},
		{"command prefix wildcard", allowRule{"*test"}, []string{"pytest"}, true},
		{"command middle wildcard", allowRule{"py*on"}, []string{"python"}, true},
		{"multiple wildcards", allowRule{"p*t*n*"}, []string{"python3"}, true},
		{"repeated wildcards", allowRule{"py**on"}, []string{"python"}, true},
		{"wildcard prefix denial", allowRule{"python*"}, []string{"cpython"}, false},
		{"wildcard suffix denial", allowRule{"*test"}, []string{"pytest-extra"}, false},
		{"literal segments cannot overlap", allowRule{"py*py"}, []string{"py"}, false},
		{"wildcard argument", allowRule{"npx", "playwright*"}, []string{"npx", "playwright@latest", "test"}, true},
		{"any argument", allowRule{"git", "*", "--help"}, []string{"git", "status", "--help"}, true},
		{"wildcard does not skip arguments", allowRule{"git", "*", "--help"}, []string{"git", "--help"}, false},
		{"wildcard does not consume arguments", allowRule{"git", "*", "--help"}, []string{"git", "status", "extra", "--help"}, false},
		{"wildcard requires an argument", allowRule{"python", "*"}, []string{"python"}, false},
		{"wildcard matches an empty argument", allowRule{"python", "*"}, []string{"python", ""}, true},
		{"argument includes slashes", allowRule{"python", "*.py"}, []string{"python", "tests/test_api.py"}, true},
		{"argument includes whitespace", allowRule{"tool", "value*end"}, []string{"tool", "value with\nspaces end"}, true},
		{"question mark is literal", allowRule{"tool?*"}, []string{"tool?name"}, true},
		{"question mark is not a wildcard", allowRule{"tool?*"}, []string{"tool-name"}, false},
		{"brackets are literal", allowRule{"tool[ab]*"}, []string{"toolaname"}, false},
		{"regex punctuation is literal", allowRule{"tool.v*"}, []string{"tool-v1"}, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := matchRule(tt.rule, tt.cmd); got != tt.want {
				t.Errorf("matchRule(%q, %q) = %v, want %v", tt.rule, tt.cmd, got, tt.want)
			}
		})
	}
}

func TestLoadWildcardAllowList(t *testing.T) {
	file := filepath.Join(t.TempDir(), "allow.txt")
	if err := os.WriteFile(file, []byte(" python*\r npx playwright*\n tool --items=a,b\r\n *\r\n\n "), 0600); err != nil {
		t.Fatal(err)
	}
	want := []allowRule{{"python*"}, {"npx", "playwright*"}, {"tool", "--items=a,b"}, {"*"}}
	if got := loadAllowList(file); !reflect.DeepEqual(got, want) {
		t.Fatalf("loadAllowList() = %q, want %q", got, want)
	}
}

func TestLoadContainerList(t *testing.T) {
	tests := []struct {
		name string
		data string
		want []string
	}{
		{"ordered lines", " tools\r\n\n app \n db ", []string{"tools", "app", "db"}},
		{"carriage returns", "tools\rapp\rdb", []string{"tools", "app", "db"}},
		{"mixed line endings", "tools\rapp\ndb\r\nextra", []string{"tools", "app", "db", "extra"}},
		{"empty list", " \r\n\n ", nil},
		{"commas are not separators", "tools,app", []string{"tools,app"}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			file := filepath.Join(t.TempDir(), "containers.txt")
			if err := os.WriteFile(file, []byte(tt.data), 0600); err != nil {
				t.Fatal(err)
			}
			if got := loadContainerList(file); !reflect.DeepEqual(got, tt.want) {
				t.Fatalf("loadContainerList() = %q, want %q", got, tt.want)
			}
		})
	}
}

func newDockerTestSocket(t *testing.T, handler http.HandlerFunc) string {
	t.Helper()
	dir, err := os.MkdirTemp("", "proxy-test-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	socketPath := filepath.Join(dir, "docker.sock")
	listener, err := net.Listen("unix", socketPath)
	if err != nil {
		t.Fatal(err)
	}
	upstream := &http.Server{Handler: handler}
	go upstream.Serve(listener)
	t.Cleanup(func() { upstream.Close() })
	return socketPath
}

func TestWildcardExecAccess(t *testing.T) {
	// Use a fake Docker Unix socket to verify API forwarding and access control.
	forwarded := make(chan []string, 1)
	socketPath := newDockerTestSocket(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/containers/tools/exec" {
			http.Error(w, "unexpected upstream request", http.StatusBadRequest)
			return
		}
		var body struct {
			Cmd []string `json:"Cmd"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		forwarded <- body.Cmd
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		io.WriteString(w, `{"Id":"exec123"}`)
	})

	tests := []struct {
		name      string
		rules     []allowRule
		container string
		cmd       []string
		want      int
	}{
		{"wildcard forwards allowed command", []allowRule{{"python*"}}, "tools", []string{"/usr/bin/python3", "script.py"}, http.StatusCreated},
		{"global wildcard forwards any command", []allowRule{{"*"}}, "tools", []string{"git", "status"}, http.StatusCreated},
		{"argument wildcard forwards matching command", []allowRule{{"npx", "playwright*"}}, "tools", []string{"npx", "playwright@latest", "test"}, http.StatusCreated},
		{"nonmatching command denied", []allowRule{{"python*"}}, "tools", []string{"ruby", "script.rb"}, http.StatusForbidden},
		{"nonmatching argument denied", []allowRule{{"npx", "playwright*"}}, "tools", []string{"npx", "webpack"}, http.StatusForbidden},
		{"denied command with JSON metacharacters", []allowRule{{"python"}}, "tools", []string{"ruby", "\"quoted\"\nC:\\temp"}, http.StatusForbidden},
		{"global wildcard keeps container restriction", []allowRule{{"*"}}, "other", []string{"git", "status"}, http.StatusForbidden},
		{"unconfigured commands denied", nil, "tools", []string{"git", "status"}, http.StatusForbidden},
		{"missing command rejected", []allowRule{{"*"}}, "tools", nil, http.StatusBadRequest},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			p := newProxy(tt.rules, []string{"tools"}, socketPath)
			data, err := json.Marshal(map[string]interface{}{"Cmd": tt.cmd})
			if err != nil {
				t.Fatal(err)
			}
			req := httptest.NewRequest(http.MethodPost, "/containers/"+tt.container+"/exec", strings.NewReader(string(data)))
			response := httptest.NewRecorder()
			p.ServeHTTP(response, req)
			if response.Code != tt.want {
				t.Fatalf("status = %d, want %d; body = %s", response.Code, tt.want, response.Body.String())
			}
			if tt.want == http.StatusForbidden {
				var body struct {
					Message string `json:"message"`
				}
				if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil {
					t.Fatalf("invalid JSON error response: %v", err)
				}
				setting := "docker-proxy-allow"
				if tt.container != "tools" {
					setting = "docker-proxy-containers=" + tt.container
				}
				if !strings.Contains(body.Message, setting) || !strings.Contains(body.Message, "してください") {
					t.Fatalf("missing Japanese configuration guidance: %q", body.Message)
				}
			}
			select {
			case got := <-forwarded:
				if tt.want != http.StatusCreated {
					t.Fatalf("denied command was forwarded: %q", got)
				}
				if !reflect.DeepEqual(got, tt.cmd) {
					t.Fatalf("forwarded Cmd = %q, want %q", got, tt.cmd)
				}
			default:
				if tt.want == http.StatusCreated {
					t.Fatal("allowed command was not forwarded")
				}
			}
		})
	}
}

func TestContainerListingAndInspectIncludeUnregisteredContainers(t *testing.T) {
	for _, allowed := range [][]string{nil, {"tools"}} {
		for _, prefix := range []string{"/", "/v1.55/"} {
			for _, endpoint := range []struct {
				path string
				body string
			}{
				{"containers/json?all=1&filters=%7B%22label%22%3A%5B%22com.docker.compose.project%3Dsample%22%5D%7D", `[{"Id":"abc123","Names":["/tools"]},{"Id":"def456","Names":["/sample-web-1"]}]`},
				// Compose ps inspects each listed container to read state and health.
				{"containers/def456/json", `{"Name":"/sample-web-1","State":{"Status":"running","Health":{"Status":"healthy"}}}`},
			} {
				t.Run(strings.Join(allowed, ",")+prefix+endpoint.path, func(t *testing.T) {
					uri := prefix + endpoint.path
					socketPath := newDockerTestSocket(t, func(w http.ResponseWriter, r *http.Request) {
						if r.Method != http.MethodGet || r.URL.RequestURI() != uri {
							t.Errorf("upstream request = %s %s, want GET %s", r.Method, r.URL.RequestURI(), uri)
						}
						w.Header().Set("Content-Type", "application/json")
						io.WriteString(w, endpoint.body)
					})
					p := newProxy(nil, allowed, socketPath)
					response := httptest.NewRecorder()
					p.ServeHTTP(response, httptest.NewRequest(http.MethodGet, uri, nil))
					if response.Code != http.StatusOK || response.Body.String() != endpoint.body {
						t.Fatalf("response = %d %s, want 200 %s", response.Code, response.Body.String(), endpoint.body)
					}
				})
			}
		}
	}
}

func TestExecContainerAccessByNameAndID(t *testing.T) {
	fullID := strings.Repeat("a", 64)
	shortID := fullID[:12]
	for _, allowed := range [][]string{nil, {"tools"}, {"sample-web-1"}} {
		for _, nameOrID := range []string{"sample-web-1", shortID, fullID} {
			t.Run(strings.Join(allowed, ",")+"/"+nameOrID, func(t *testing.T) {
				forwarded := make(chan string, 1)
				socketPath := newDockerTestSocket(t, func(w http.ResponseWriter, r *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					switch {
					case r.Method == http.MethodGet && (r.URL.Path == "/containers/"+shortID+"/json" || r.URL.Path == "/containers/"+fullID+"/json"):
						io.WriteString(w, `{"Name":"/sample-web-1"}`)
					case r.Method == http.MethodPost && r.URL.Path == "/v1.55/containers/"+nameOrID+"/exec":
						forwarded <- r.URL.Path
						w.WriteHeader(http.StatusCreated)
						io.WriteString(w, `{"Id":"exec123"}`)
					default:
						t.Errorf("unexpected upstream request: %s %s", r.Method, r.URL.Path)
						w.WriteHeader(http.StatusNotFound)
					}
				})
				p := newProxy([]allowRule{{"*"}}, allowed, socketPath)
				response := httptest.NewRecorder()
				p.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/v1.55/containers/"+nameOrID+"/exec", strings.NewReader(`{"Cmd":["python"]}`)))
				if len(allowed) == 1 && allowed[0] == "sample-web-1" {
					if response.Code != http.StatusCreated || len(forwarded) != 1 {
						t.Fatalf("allowed exec was not forwarded: %d %s", response.Code, response.Body.String())
					}
					return
				}
				if response.Code != http.StatusForbidden || len(forwarded) != 0 {
					t.Fatalf("unregistered exec was not denied: %d %s", response.Code, response.Body.String())
				}
				var body struct {
					Message string `json:"message"`
				}
				if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil {
					t.Fatal(err)
				}
				for _, want := range []string{"許可されていません", "docker-proxy-containers=sample-web-1", "追加し", "起動し直してください"} {
					if !strings.Contains(body.Message, want) {
						t.Errorf("error %q does not contain %q", body.Message, want)
					}
				}
			})
		}
	}
}

func TestUnknownContainerIDFailsClosed(t *testing.T) {
	socketPath := newDockerTestSocket(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("unexpected upstream method: %s", r.Method)
		}
		w.WriteHeader(http.StatusNotFound)
	})
	p := newProxy([]allowRule{{"*"}}, []string{"tools"}, socketPath)
	response := httptest.NewRecorder()
	p.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/containers/abcdef123456/exec", strings.NewReader(`{"Cmd":["python"]}`)))
	if response.Code != http.StatusForbidden || !strings.Contains(response.Body.String(), "docker-proxy-containers に対象コンテナの実際の名前を追加") {
		t.Fatalf("missing guidance for unknown ID: %d %s", response.Code, response.Body.String())
	}
}

func TestOtherOperationsRemainForbidden(t *testing.T) {
	p := newProxy([]allowRule{{"*"}}, []string{"tools"}, "unused.sock")
	for _, request := range []struct{ method, path string }{
		{http.MethodPost, "/containers/create"},
		{http.MethodDelete, "/containers/tools"},
	} {
		response := httptest.NewRecorder()
		p.ServeHTTP(response, httptest.NewRequest(request.method, request.path, nil))
		if response.Code != http.StatusForbidden || !strings.Contains(response.Body.String(), "この操作は許可されていません") {
			t.Errorf("response = %d %s, want Japanese 403 error", response.Code, response.Body.String())
		}
	}
}
