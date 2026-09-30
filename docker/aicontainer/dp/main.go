// dp executes commands through the restricted Docker Socket Proxy.
package main

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"
)

const requestTimeout = 10 * time.Second

var errMissingExecutable = errors.New("PATH に実行ファイルが見つかりません")

type dockerError struct {
	status  int
	message string
}

func (e *dockerError) Error() string {
	return fmt.Sprintf("Docker プロキシでエラーが発生しました（HTTP %d）。詳細: %s", e.status, e.message)
}

type signalExit struct{ code int }

func (e *signalExit) Error() string { return fmt.Sprintf("処理が中断されました（終了コード %d）", e.code) }

type dockerClient struct{ socketPath string }

// Use a fresh Unix connection for each request. Keeping the buffered reader is
// essential: an HTTP upgrade can arrive together with the first output frames.
func (c dockerClient) open(method, path string, body any, upgrade bool) (*http.Response, *net.UnixConn, *bufio.Reader, error) {
	var payload []byte
	var err error
	if body != nil {
		payload, err = json.Marshal(body)
		if err != nil {
			return nil, nil, nil, fmt.Errorf("Docker へのリクエストを作成できませんでした。詳細: %w", err)
		}
	}
	req, err := http.NewRequest(method, "http://docker"+path, bytes.NewReader(payload))
	if err != nil {
		return nil, nil, nil, fmt.Errorf("Docker へのリクエストが不正です。詳細: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	if upgrade {
		req.Header.Set("Connection", "Upgrade")
		req.Header.Set("Upgrade", "tcp")
	} else {
		req.Close = true
	}
	connection, err := net.DialTimeout("unix", c.socketPath, requestTimeout)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("Docker プロキシに接続できませんでした。プロキシの起動状態とソケットの設定を確認してください。詳細: %w", err)
	}
	conn := connection.(*net.UnixConn)
	// Limit only the HTTP exchange, never the remote command's execution time.
	if err = conn.SetDeadline(time.Now().Add(requestTimeout)); err == nil {
		err = req.Write(conn)
	}
	reader := bufio.NewReader(conn)
	var res *http.Response
	if err == nil {
		res, err = http.ReadResponse(reader, req)
	}
	if err != nil {
		conn.Close()
		return nil, nil, nil, fmt.Errorf("Docker プロキシとの通信に失敗しました。詳細: %w", err)
	}
	return res, conn, reader, nil
}

func responseError(res *http.Response) error {
	data, err := io.ReadAll(res.Body)
	if err != nil {
		return fmt.Errorf("Docker からの応答を読み取れませんでした。詳細: %w", err)
	}
	var result struct{ Message string }
	if json.Unmarshal(data, &result) != nil || result.Message == "" {
		result.Message = strings.TrimSpace(string(data))
	}
	if result.Message == "" {
		result.Message = "エラーの詳細は返されませんでした。"
	}
	return &dockerError{status: res.StatusCode, message: result.Message}
}

func (c dockerClient) request(method, path string, body, result any) error {
	res, conn, _, err := c.open(method, path, body, false)
	if err != nil {
		return err
	}
	defer conn.Close()
	defer res.Body.Close()
	if res.StatusCode < 200 || res.StatusCode >= 300 {
		return responseError(res)
	}
	data, err := io.ReadAll(res.Body)
	if err != nil {
		return fmt.Errorf("Docker からの応答を読み取れませんでした。詳細: %w", err)
	}
	if result != nil {
		if err := json.Unmarshal(data, result); err != nil {
			return fmt.Errorf("Docker からの応答が不正です。詳細: %w", err)
		}
	}
	return nil
}

func copyOutput(input io.Reader, stdout, stderr io.Writer, tty bool) error {
	if tty {
		_, err := io.Copy(stdout, input)
		if err != nil {
			return fmt.Errorf("コマンドの出力を転送できませんでした。詳細: %w", err)
		}
		return nil
	}
	var header [8]byte
	for {
		_, err := io.ReadFull(input, header[:])
		if err == io.EOF {
			return nil
		}
		if err == io.ErrUnexpectedEOF {
			return errors.New("Docker からの出力データが途中で途切れました")
		}
		if err != nil {
			return fmt.Errorf("Docker からの出力を読み取れませんでした。詳細: %w", err)
		}
		if (header[0] != 1 && header[0] != 2) || header[1] != 0 || header[2] != 0 || header[3] != 0 {
			return errors.New("Docker からの出力データの形式が不正です")
		}
		output := stdout
		if header[0] == 2 {
			output = stderr
		}
		_, err = io.CopyN(output, input, int64(binary.BigEndian.Uint32(header[4:])))
		if err == io.EOF {
			return errors.New("Docker からの出力データが途中で途切れました")
		}
		if err != nil {
			return fmt.Errorf("コマンドの出力を転送できませんでした。詳細: %w", err)
		}
	}
}

func (c dockerClient) resize(id string) {
	rows, columns := terminalSize(os.Stdout.Fd())
	// Resizing is best effort, and must not block input/output on a slow proxy.
	_ = c.request("POST", fmt.Sprintf("/exec/%s/resize?h=%d&w=%d", id, rows, columns), nil, nil)
}

func (c dockerClient) stream(id string, conn *net.UnixConn, output io.Reader, tty bool) error {
	signals := make(chan os.Signal, 8)
	signal.Notify(signals, syscall.SIGINT, syscall.SIGTERM, syscall.SIGWINCH)
	defer signal.Stop(signals)
	if tty {
		restore, err := makeRaw(os.Stdin.Fd())
		if err != nil {
			return fmt.Errorf("端末を対話用モードに切り替えられませんでした。詳細: %w", err)
		}
		defer restore()
		go c.resize(id)
	}
	// Start reading stdin only after exec/start succeeds, so a missing executable
	// can be retried in the next container without losing any input.
	go func() {
		_, _ = io.Copy(conn, os.Stdin)
		// EOF closes only the write side: the remote command can still send output.
		_ = conn.CloseWrite()
	}()
	done := make(chan error, 1)
	go func() { done <- copyOutput(output, os.Stdout, os.Stderr, tty) }()
	for {
		select {
		case err := <-done:
			return err
		case sig := <-signals:
			switch sig {
			case syscall.SIGWINCH:
				if tty {
					go c.resize(id)
				}
			case syscall.SIGINT:
				return &signalExit{130}
			case syscall.SIGTERM:
				return &signalExit{143}
			}
		}
	}
}

func (c dockerClient) execute(id string, tty bool) (int, error) {
	res, conn, reader, err := c.open("POST", "/exec/"+id+"/start", struct{ Detach, Tty bool }{Tty: tty}, true)
	if err != nil {
		return 0, err
	}
	defer conn.Close()
	if res.StatusCode != http.StatusSwitchingProtocols && res.StatusCode != http.StatusOK {
		defer res.Body.Close()
		err := responseError(res)
		var dockerErr *dockerError
		if errors.As(err, &dockerErr) && dockerErr.status >= 400 && strings.Contains(dockerErr.message, "executable file not found in $PATH") {
			return 0, errMissingExecutable
		}
		return 0, err
	}
	if err := conn.SetDeadline(time.Time{}); err != nil {
		return 0, fmt.Errorf("Docker プロキシ接続のタイムアウト設定を解除できませんでした。詳細: %w", err)
	}
	var output io.Reader = reader
	if res.StatusCode == http.StatusOK {
		output = res.Body
	}
	if err := c.stream(id, conn, output, tty); err != nil {
		return 0, err
	}
	conn.Close()
	var info struct {
		Running  bool
		ExitCode *int
	}
	if err := c.request("GET", "/exec/"+id+"/json", nil, &info); err != nil {
		return 0, err
	}
	if info.Running || info.ExitCode == nil || *info.ExitCode < 0 || *info.ExitCode > 255 {
		return 0, errors.New("Docker から実行完了後の終了コードを取得できませんでした")
	}
	return *info.ExitCode, nil
}

func validExecID(id string) bool {
	if id == "" {
		return false
	}
	for _, char := range id {
		if !(char >= 'a' && char <= 'z' || char >= 'A' && char <= 'Z' || char >= '0' && char <= '9') {
			return false
		}
	}
	return true
}

func configuredContainers(value string) []string {
	value = strings.ReplaceAll(strings.ReplaceAll(value, "\r\n", "\n"), "\r", "\n")
	var names []string
	seen := make(map[string]bool)
	for _, line := range strings.Split(value, "\n") {
		name := strings.TrimSpace(line)
		if name != "" && !seen[name] {
			names = append(names, name)
			seen[name] = true
		}
	}
	return names
}

func run(cmd []string) (int, error) {
	const usage = "使い方: dp コマンド [引数 ...]\n例: dp npx playwright --version"
	if len(cmd) == 0 {
		fmt.Fprintln(os.Stderr, usage)
		return 2, nil
	}
	if len(cmd) == 1 && cmd[0] == "--help" {
		fmt.Fprintln(os.Stdout, usage)
		return 0, nil
	}
	host := os.Getenv("DOCKER_HOST")
	if !strings.HasPrefix(host, "unix:///") {
		return 0, errors.New("Docker プロキシが設定されていません。.aicontainer に docker-proxy-name を設定し、aicontainer を起動し直してください（DOCKER_HOST は unix:///... 形式で指定します）。")
	}
	client := dockerClient{socketPath: strings.TrimPrefix(host, "unix://")}
	var containers []struct{ Names []string }
	if err := client.request("GET", "/containers/json", nil, &containers); err != nil {
		return 0, err
	}
	if containers == nil {
		return 0, errors.New("Docker から取得したコンテナ一覧の形式が不正です")
	}
	visible := make(map[string]bool)
	for _, container := range containers {
		for _, name := range container.Names {
			visible[strings.TrimPrefix(name, "/")] = true
		}
	}
	names := configuredContainers(os.Getenv("DOCKER_PROXY_CONTAINERS"))
	if len(names) == 0 {
		return 0, errors.New("実行先のコンテナが登録されていません。.aicontainer に docker-proxy-containers を設定し、aicontainer を起動し直してください。")
	}
	cwd, err := os.Getwd()
	if err != nil {
		return 0, fmt.Errorf("現在の作業ディレクトリを取得できませんでした。詳細: %w", err)
	}
	tty := isTerminal(os.Stdin.Fd()) && isTerminal(os.Stdout.Fd()) && isTerminal(os.Stderr.Fd())
	attempted := false
	for _, name := range names {
		if !visible[name] {
			continue
		}
		attempted = true
		body := struct {
			Cmd          []string
			WorkingDir   string
			AttachStdin  bool
			AttachStdout bool
			AttachStderr bool
			Tty          bool
		}{
			Cmd: cmd, WorkingDir: cwd,
			AttachStdin: true, AttachStdout: true, AttachStderr: true, Tty: tty,
		}
		var exec struct{ Id string }
		if err := client.request("POST", "/containers/"+url.PathEscape(name)+"/exec", body, &exec); err != nil {
			return 0, err
		}
		if !validExecID(exec.Id) {
			return 0, errors.New("Docker から取得した実行 ID が不正です")
		}
		code, err := client.execute(exec.Id, tty)
		// Never retry an executed command, a missing working directory, or a
		// denied request. Only a missing executable reported by Docker qualifies.
		if errors.Is(err, errMissingExecutable) {
			continue
		}
		return code, err
	}
	if !attempted {
		fmt.Fprintln(os.Stderr, "dp: 登録済みのコンテナが起動していません。.aicontainer の docker-proxy-containers を確認し、対象のコンテナを起動してください。")
	} else {
		fmt.Fprintf(os.Stderr, "dp: 登録済みの起動中コンテナにコマンド %q が見つかりません\n", cmd[0])
	}
	return 127, nil
}

func main() {
	code, err := run(os.Args[1:])
	if err != nil {
		var interrupted *signalExit
		if errors.As(err, &interrupted) {
			os.Exit(interrupted.code)
		}
		fmt.Fprintln(os.Stderr, "dp:", err)
		code = 125
		var dockerErr *dockerError
		if errors.As(err, &dockerErr) && dockerErr.status == http.StatusForbidden {
			code = 126
		}
	}
	os.Exit(code)
}
