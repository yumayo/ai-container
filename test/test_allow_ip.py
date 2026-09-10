"""Run with: python3 -m unittest discover -s test -p 'test_*.py'.

Docker, sudo and network commands are replaced with recording stubs so these
tests exercise the real shell scripts without modifying host firewall rules.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
FIREWALL = REPO / "docker/aicontainer/init-firewall.sh"

MOCK_COMMAND = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["MOCK_LOG"], "a") as log:
    log.write(json.dumps([name, *args]) + "\n")

repo = Path(os.environ["TEST_REPO"])
if name == "docker" and args[:1] == ["run"]:
    env = os.environ.copy()
    for i, arg in enumerate(args):
        if arg == "-e":
            key, value = args[i + 1].split("=", 1)
            env[key] = value
    sys.exit(subprocess.call(
        ["bash", str(repo / "docker/aicontainer/entrypoint.sh"), "payload"], env=env))
elif name == "sudo":
    assert args[0] == "/usr/local/bin/init-firewall.sh", args
    env = os.environ.copy()
    # sudo normally filters environment variables. Configuration must survive
    # through positional arguments, even when this variable is removed.
    env.pop("AICONTAINER_ALLOWED_IPS", None)
    sys.exit(subprocess.call(
        ["bash", str(repo / "docker/aicontainer/init-firewall.sh"), *args[1:]], env=env))
elif name == "ip":
    print("default via 172.20.0.1 dev eth0")
elif name == "dig":
    print(args[-1] + ". 60 IN A 198.51.100.10")
elif name == "curl":
    if args[-1] == "https://example.com":
        remote = os.environ.get("VERIFY_REMOTE_IP", "")
        if not remote:
            sys.exit(7)
        print(remote, end="")
    elif os.environ.get("FAIL_API"):
        sys.exit(7)
'''


class AllowIPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        self.log = self.work / "commands.jsonl"
        stub = self.bin / "mock-command"
        stub.write_text(MOCK_COMMAND)
        stub.chmod(0o755)
        for name in ("docker", "sudo", "ip", "iptables", "iptables-save",
                     "ipset", "dig", "curl", "payload"):
            (self.bin / name).symlink_to(stub)
        self.env = os.environ.copy()
        for name in ("AICONTAINER_ALLOWED_IPS", "FIREWALL_MODE", "OPENAI_API_KEY",
                     "VERIFY_REMOTE_IP", "FAIL_API"):
            self.env.pop(name, None)
        self.env.update(PATH=f"{self.bin}:{self.env['PATH']}",
                        MOCK_LOG=str(self.log), TEST_REPO=str(REPO))

    def commands(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def run_script(self, args, extra_env=None):
        self.log.unlink(missing_ok=True)
        return subprocess.run(args, cwd=self.work,
                              env={**self.env, **(extra_env or {})},
                              text=True, capture_output=True, timeout=20)

    def launch(self, config, suffix="", extra_env=None):
        (self.work / ".aicontainer").write_text(config)
        return self.run_script(
            ["bash", "-c", 'source "$TEST_REPO/.bash_ai_container"\naicontainer\n' + suffix],
            extra_env)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_config_reaches_firewall_in_both_modes(self):
        for mode in ("claude", "codex"):
            with self.subTest(mode=mode):
                result = self.launch(
                    f"tool={mode}\nnetwork=myproject\n"
                    'allow-ip="172.20.0.0/16" # Docker subnet\r\n'
                    "allow-ip=192.168.1.10   \r\n"
                    "allow-ip=172.20.0.0/16")
                self.assert_success(result)
                commands = self.commands()
                docker = next(c for c in commands if c[:2] == ["docker", "run"])
                self.assertEqual(docker[docker.index("--network") + 1], "yumayo-ai-myproject")
                self.assertIn("AICONTAINER_ALLOWED_IPS=172.20.0.0/16,192.168.1.10,172.20.0.0/16", docker)
                self.assertIn(["sudo", "/usr/local/bin/init-firewall.sh", mode,
                               "172.20.0.0/16,192.168.1.10,172.20.0.0/16"], commands)
                reject = commands.index(["iptables", "-A", "OUTPUT", "-j", "REJECT",
                                         "--reject-with", "icmp-admin-prohibited"])
                for ip in ("172.20.0.0/24", "172.20.0.0/16", "192.168.1.10"):
                    for chain, flag in (("INPUT", "-s"), ("OUTPUT", "-d")):
                        rule = ["iptables", "-A", chain, flag, ip, "-j", "ACCEPT"]
                        self.assertLess(commands.index(rule), reject)
                self.assertIn(["iptables", "-P", "OUTPUT", "DROP"], commands)
                self.assertIn(["payload"], commands)

    def test_no_config_preserves_default_rules(self):
        result = self.launch("")
        self.assert_success(result)
        commands = self.commands()
        docker = next(c for c in commands if c[:2] == ["docker", "run"])
        self.assertFalse(any(c.startswith("AICONTAINER_ALLOWED_IPS=") for c in docker))
        self.assertIn(["sudo", "/usr/local/bin/init-firewall.sh", "claude", ""], commands)
        destinations = [c[4] for c in commands if c[:4] == ["iptables", "-A", "OUTPUT", "-d"]]
        self.assertEqual(destinations, ["172.20.0.0/24"])

    def test_settings_do_not_leak_to_next_launch(self):
        result = self.launch("allow-ip=172.20.0.0/16\n", ': > .aicontainer\naicontainer\n')
        self.assert_success(result)
        sudo = [c for c in self.commands() if c[0] == "sudo"]
        self.assertEqual([c[-1] for c in sudo], ["172.20.0.0/16", ""])

    def test_dump_includes_settings_without_starting_docker(self):
        for mode in ("claude", "codex"):
            with self.subTest(mode=mode):
                (self.work / ".aicontainer").write_text(
                    f"tool={mode}\nnetwork=myproject\nallow-ip=172.20.0.0/16\n")
                result = self.run_script(
                    ["bash", "-c", 'source "$TEST_REPO/.bash_ai_container"\naicontainer dump'])
                self.assert_success(result)
                self.assertIn("--network yumayo-ai-myproject", result.stdout)
                self.assertIn("-e AICONTAINER_ALLOWED_IPS=172.20.0.0/16", result.stdout)
                self.assertEqual(self.commands(), [])

    def test_invalid_config_never_changes_firewall_or_runs_payload(self):
        for value in ("", "256.1.1.1", "172.20.0.0/33", "1.2.3", "1.2.3.4/",
                      "01.2.3.4", "1.2.3.4/00", "mcp", "::1", "1.2.3.4,5.6.7.8",
                      "$(touch injected)", "`touch injected`", "1.2.3.4; touch injected"):
            with self.subTest(value=value):
                result = self.launch("allow-ip=" + value + "\n")
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("allow-ip", result.stderr)
                self.assertFalse(any(c[0] in ("iptables", "iptables-save", "ipset", "payload")
                                     for c in self.commands()))
                self.assertFalse((self.work / "injected").exists())

    def test_firewall_validates_entire_list_before_changing_rules(self):
        for value in ("172.20.0.0/16,256.1.1.1", "1.2.3.4,", ",1.2.3.4",
                      "1.2.3.4,,5.6.7.8", "1.2.3.4\n5.6.7.8", "$(touch injected)"):
            with self.subTest(value=value):
                result = self.run_script(["bash", str(FIREWALL), "claude", value])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.commands(), [])

    def test_block_check_honors_explicitly_allowed_ips_and_cidrs(self):
        for allowed, remote in (("203.0.113.42", "203.0.113.42"),
                                ("203.0.113.42/32", "203.0.113.42"),
                                ("203.0.113.99/24", "203.0.113.42"),
                                ("0.0.0.0/0", "203.0.113.42"),
                                ("128.0.0.0/1", "203.0.113.42")):
            with self.subTest(allowed=allowed):
                result = self.launch("allow-ip=" + allowed,
                                     extra_env={"VERIFY_REMOTE_IP": remote})
                self.assert_success(result)
                self.assertIn("is in an allowed IP range", result.stdout)
                self.assertIn(["payload"], self.commands())

    def test_block_check_still_rejects_unexpected_access(self):
        for allowed in ("172.20.0.0/16", "203.0.112.0/24", "203.0.113.41/32", "0.0.0.0/1"):
            with self.subTest(allowed=allowed):
                result = self.launch("allow-ip=" + allowed,
                                     extra_env={"VERIFY_REMOTE_IP": "203.0.113.42"})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("was able to reach https://example.com", result.stderr)
                self.assertNotIn(["payload"], self.commands())

    def test_default_block_check_is_not_relaxed(self):
        result = self.launch("", extra_env={"VERIFY_REMOTE_IP": "172.20.0.1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("was able to reach https://example.com", result.stderr)
        self.assertNotIn(["payload"], self.commands())

    def test_api_check_still_fails_when_api_is_unreachable(self):
        result = self.launch("allow-ip=172.20.0.0/16", extra_env={"FAIL_API": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unable to reach https://api.anthropic.com", result.stderr)
        self.assertNotIn(["payload"], self.commands())


if __name__ == "__main__":
    unittest.main()
