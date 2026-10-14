"""起動プロファイルの引数・環境変数を、ネットワークなしで実 CLI の代役へ渡す。"""

import json
import os
import pty
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "docker/aicontainer/launch-profile.cjs"


class LaunchProfileTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ai profile 日本語 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "tool bin"
        self.bin.mkdir()
        self.profile = self.root / "profile with spaces.json"
        self.record = self.root / "received.json"
        self.secret = "secret-token-for-profile-tests"
        mock = f"#!{sys.executable}\n" + '''
import json, os, pathlib, sys
pathlib.Path(os.environ["PROFILE_TEST_RECORD"]).write_text(json.dumps({
    "arguments": sys.argv[1:], "token": os.environ.get("MASTERDATAEDITOR_MCP_TOKEN"),
    "cwd": os.getcwd(), "stdin": sys.stdin.read(), "existing": os.environ["PROFILE_TEST_EXISTING"]
}))
print("tool stdout")
print("tool stderr", file=sys.stderr)
sys.exit(17)
'''
        for tool in ("claude", "codex"):
            path = self.bin / tool
            path.write_text(mock)
            path.chmod(0o755)
        self.runner = self.root / "launch-profile.cjs"
        self.runner.write_text(SOURCE.read_text().replace(
            "const toolDirectory = '/opt/aicontainer/bin';",
            f"const toolDirectory = {json.dumps(str(self.bin))};",
        ))
        self.env = dict(os.environ, PROFILE_TEST_RECORD=str(self.record),
                        PROFILE_TEST_EXISTING="kept")

    def run_profile(self, profile, tool="codex", *arguments):
        self.profile.write_text(json.dumps(profile))
        return subprocess.run(["node", str(self.runner), str(self.profile), tool, *arguments],
                              cwd=self.root, env=self.env, input="stdin payload\n",
                              text=True, capture_output=True)

    def test_arguments_environment_stdio_and_exit_status_are_preserved(self):
        extra = ["--mcp-config", "two words", "", '"quoted"',
                 "$(touch injected)", "`touch injected`", "日本語\nnext"]
        for tool in ("claude", "codex"):
            with self.subTest(tool=tool):
                result = self.run_profile({"arguments": extra, "environment": {
                    "MASTERDATAEDITOR_MCP_TOKEN": self.secret}}, tool, "--continue")
                self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
                self.assertEqual(result.stdout, "tool stdout\n")
                self.assertEqual(result.stderr, "tool stderr\n")
                record = json.loads(self.record.read_text())
                self.assertEqual(record["arguments"], ["--continue", *extra])
                self.assertEqual(record["token"], self.secret)
                self.assertEqual(record["existing"], "kept")
                self.assertEqual(record["cwd"], str(self.root))
                self.assertEqual(record["stdin"], "stdin payload\n")
        self.assertFalse((self.root / "injected").exists())

    def test_invalid_profiles_and_tools_do_not_launch_or_print_secret_values(self):
        invalid = [None, [], {}, {"arguments": [], "environment": {}, "extra": self.secret},
                   {"arguments": [42], "environment": {}},
                   {"arguments": ["\0"], "environment": {}},
                   {"arguments": [], "environment": {"NOT VALID": self.secret}},
                   {"arguments": [], "environment": {"TOKEN": 42}},
                   {"arguments": [], "environment": {"TOKEN": "\0"}},
                   {"arguments": [], "environment": []}]
        for profile in invalid:
            with self.subTest(profile=profile):
                result = self.run_profile(profile)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.record.exists())
                self.assertNotIn(self.secret, result.stdout + result.stderr)
        result = self.run_profile({"arguments": [], "environment": {}}, "bash")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.record.exists())

    def test_malformed_and_missing_files_do_not_echo_contents(self):
        self.profile.write_text('{"arguments": ["' + self.secret + '"]')
        for existing in (True, False):
            if not existing:
                self.profile.unlink()
            result = subprocess.run(["node", str(self.runner), str(self.profile), "codex"],
                                    env=self.env, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(self.secret, result.stdout + result.stderr)
            self.assertFalse(self.record.exists())

    def run_terminal_signal(self, signal_to_parent):
        tool = self.bin / "codex"
        tool.write_text(f"#!{sys.executable}\n" + '''
import signal, sys
def interrupt(signum, frame):
    print("TOOL_INTERRUPT", flush=True)
def terminate(signum, frame):
    print("TOOL_TERMINATE", flush=True)
    sys.exit(17)
signal.signal(signal.SIGINT, interrupt)
signal.signal(signal.SIGTERM, terminate)
print("TOOL_READY", flush=True)
while True:
    signal.pause()
''')
        tool.chmod(0o755)
        self.profile.write_text(json.dumps({"arguments": [], "environment": {}}))
        pid, terminal = pty.fork()
        if pid == 0:
            os.chdir(self.root)
            os.execvpe("node", ["node", str(self.runner), str(self.profile), "codex"], self.env)
        received = bytearray()

        def read_until(marker, seconds):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if marker is not None and marker in received:
                    return
                ready, _, _ = select.select([terminal], [], [], min(0.05, max(0, deadline - time.monotonic())))
                if ready:
                    try:
                        value = os.read(terminal, 4096)
                    except OSError:
                        return
                    if not value:
                        return
                    received.extend(value)

        try:
            read_until(b"TOOL_READY", 5)
            self.assertIn(b"TOOL_READY", received)
            if signal_to_parent:
                os.kill(pid, signal.SIGTERM)
                read_until(b"TOOL_TERMINATE", 5)
                self.assertEqual(received.count(b"TOOL_TERMINATE"), 1)
            else:
                # 親のイベントループが遅れた状態でも、端末の SIGINT は子へ直接届く。
                os.kill(pid, signal.SIGSTOP)
                os.write(terminal, b"\x03")
                read_until(b"TOOL_INTERRUPT", 5)
                self.assertIn(b"TOOL_INTERRUPT", received)
                os.kill(pid, signal.SIGCONT)
                read_until(None, 0.5)
                self.assertEqual(received.count(b"TOOL_INTERRUPT"), 1, received.decode(errors="replace"))
        finally:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.close(terminal)
            os.waitpid(pid, 0)

    def test_terminal_control_c_reaches_tool_once(self):
        self.run_terminal_signal(False)

    def test_sigterm_sent_only_to_parent_reaches_tool(self):
        self.run_terminal_signal(True)


if __name__ == "__main__":
    unittest.main()
