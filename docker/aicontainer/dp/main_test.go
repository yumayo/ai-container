package main

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"testing/iotest"
	"time"
)

func frame(channel byte, text string) []byte {
	data := make([]byte, 8+len(text))
	data[0] = channel
	binary.BigEndian.PutUint32(data[4:], uint32(len(text)))
	copy(data[8:], text)
	return data
}

func TestCopyOutput(t *testing.T) {
	cases := []struct {
		name   string
		data   []byte
		tty    bool
		stdout string
		stderr string
		err    string
	}{
		{"multiplexed", append(frame(1, "out\n"), frame(2, "err\n")...), false, "out\n", "err\n", ""},
		{"empty frame", append(frame(1, ""), frame(2, "err")...), false, "", "err", ""},
		{"tty", []byte("raw terminal\r\n"), true, "raw terminal\r\n", "", ""},
		{"empty output", nil, false, "", "", ""},
		{"truncated header", frame(1, "out")[:3], false, "", "", "途中で途切れました"},
		{"truncated payload", frame(1, "output")[:10], false, "ou", "", "途中で途切れました"},
		{"invalid channel", frame(3, "out"), false, "", "", "形式が不正です"},
	}
	for _, tt := range cases {
		t.Run(tt.name, func(t *testing.T) {
			var stdout, stderr bytes.Buffer
			// Split both headers and payloads at every possible byte boundary.
			err := copyOutput(iotest.OneByteReader(bytes.NewReader(tt.data)), &stdout, &stderr, tt.tty)
			if tt.err == "" && err != nil || tt.err != "" && (err == nil || !strings.Contains(err.Error(), tt.err)) {
				t.Fatalf("error = %v, want %q", err, tt.err)
			}
			if stdout.String() != tt.stdout || stderr.String() != tt.stderr {
				t.Fatalf("output = (%q, %q), want (%q, %q)", stdout.String(), stderr.String(), tt.stdout, tt.stderr)
			}
		})
	}
}

func TestConfiguredContainers(t *testing.T) {
	got := configuredContainers(" second \r third \r\n\n first \nsecond\n")
	if want := []string{"second", "third", "first"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestValidExecID(t *testing.T) {
	for _, id := range []string{"", "../other", "id?detach=true", "id/other", "id\n", "実行"} {
		if validExecID(id) {
			t.Errorf("accepted invalid exec ID %q", id)
		}
	}
	if !validExecID("exec0123AbC") {
		t.Fatal("rejected valid exec ID")
	}
}

func TestRequestErrors(t *testing.T) {
	listener, err := net.Listen("unix", filepath.Join(t.TempDir(), "docker.sock"))
	if err != nil {
		t.Fatal(err)
	}
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/forbidden" {
			w.WriteHeader(http.StatusForbidden)
			fmt.Fprint(w, `{"message":"docker-proxy-allow を確認してください"}`)
			return
		}
		fmt.Fprint(w, `invalid json`)
	})}
	go server.Serve(listener)
	t.Cleanup(func() { server.Close() })
	client := dockerClient{socketPath: listener.Addr().String()}
	var result any
	err = client.request("POST", "/forbidden", nil, &result)
	var dockerErr *dockerError
	if !errors.As(err, &dockerErr) || dockerErr.status != 403 || !strings.Contains(dockerErr.message, "docker-proxy-allow") {
		t.Fatalf("permission error was lost: %v", err)
	}
	if err := client.request("GET", "/invalid", nil, &result); err == nil {
		t.Fatal("accepted invalid JSON")
	}
}

func TestUpgradePreservesBufferedOutputAndHalfClose(t *testing.T) {
	for _, status := range []int{http.StatusSwitchingProtocols, http.StatusOK} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			listener, err := net.Listen("unix", filepath.Join(t.TempDir(), "docker.sock"))
			if err != nil {
				t.Fatal(err)
			}
			defer listener.Close()
			serverDone := make(chan error, 1)
			go func() {
				serverDone <- func() error {
					conn, err := listener.Accept()
					if err != nil {
						return err
					}
					defer conn.Close()
					conn.SetDeadline(time.Now().Add(5 * time.Second))
					reader := bufio.NewReader(conn)
					req, err := http.ReadRequest(reader)
					if err != nil {
						return err
					}
					if _, err := io.Copy(io.Discard, req.Body); err != nil {
						return err
					}
					req.Body.Close()
					if req.Header.Get("Upgrade") != "tcp" || req.URL.Path != "/exec/test/start" {
						return fmt.Errorf("unexpected request: %s %v", req.URL, req.Header)
					}
					headers := "Connection: close\r\n"
					if status == http.StatusSwitchingProtocols {
						headers = "Connection: Upgrade\r\nUpgrade: tcp\r\n"
					}
					// Put headers and the first output frame in one write.
					response := fmt.Sprintf("HTTP/1.1 %d %s\r\n%s\r\n", status, http.StatusText(status), headers)
					if _, err := conn.Write(append([]byte(response), frame(1, "ready\n")...)); err != nil {
						return err
					}
					input, err := io.ReadAll(reader)
					if err != nil {
						return err
					}
					_, err = conn.Write(frame(2, string(input)))
					return err
				}()
			}()
			client := dockerClient{socketPath: listener.Addr().String()}
			res, conn, reader, err := client.open("POST", "/exec/test/start", struct{ Tty bool }{}, true)
			if err != nil {
				t.Fatal(err)
			}
			defer conn.Close()
			if res.StatusCode != status {
				t.Fatalf("status = %d, want %d", res.StatusCode, status)
			}
			if _, err := io.WriteString(conn, "stdin after upgrade\n"); err != nil {
				t.Fatal(err)
			}
			if err := conn.CloseWrite(); err != nil {
				t.Fatal(err)
			}
			var output io.Reader = reader
			if status == http.StatusOK {
				output = res.Body
			}
			var stdout, stderr bytes.Buffer
			if err := copyOutput(output, &stdout, &stderr, false); err != nil {
				t.Fatal(err)
			}
			if stdout.String() != "ready\n" || stderr.String() != "stdin after upgrade\n" {
				t.Fatalf("unexpected output: stdout=%q stderr=%q", stdout.String(), stderr.String())
			}
			if err := <-serverDone; err != nil {
				t.Fatal(err)
			}
		})
	}
}
