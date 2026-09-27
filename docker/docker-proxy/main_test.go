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
	if err := os.WriteFile(file, []byte(" python*, npx playwright*, *, , "), 0600); err != nil {
		t.Fatal(err)
	}
	want := []allowRule{{"python*"}, {"npx", "playwright*"}, {"*"}}
	if got := loadAllowList(file); !reflect.DeepEqual(got, want) {
		t.Fatalf("loadAllowList() = %q, want %q", got, want)
	}
}

func TestWildcardExecAccess(t *testing.T) {
	// Use a fake Docker Unix socket to verify API forwarding and access control.
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
	forwarded := make(chan []string, 1)
	upstream := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
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
	})}
	go upstream.Serve(listener)
	t.Cleanup(func() { upstream.Close() })

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
