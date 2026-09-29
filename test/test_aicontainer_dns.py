""".aicontainerのDNS・ネットワーク分離・プロキシ設定を、Dockerを起動せずに検証する。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


LAUNCHER = Path(__file__).resolve().parents[1] / ".bash_ai_container"


class DnsConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aicontainer test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / ".aicontainer"
        self.calls = self.root / "docker-calls.json"
        mock = self.root / "docker"
        mock.write_text(f"#!{sys.executable}\n" + '''
import json
import os
from pathlib import Path
import sys

root = Path(os.environ["DNS_CONFIG_TEST_DIR"])
args = sys.argv[1:]
with (root / "docker-events.jsonl").open("a") as events:
    events.write(json.dumps(args) + "\\n")
if args[:2] == ["network", "create"]:
    if (os.environ.get("TEST_NETWORK_CREATE_FAIL") or
            args[-1] == os.environ.get("TEST_EXISTING_NETWORK") or
            (root / "active-network").exists()):
        sys.exit(1)
    (root / "active-network").write_text(args[-1])
    sys.exit(0)
if args[:2] == ["network", "inspect"]:
    exists = (args[-1] == os.environ.get("TEST_EXISTING_NETWORK") or
              ((root / "active-network").exists() and
               (root / "active-network").read_text() == args[-1]))
    sys.exit(0 if exists else 1)
if args[:2] == ["network", "rm"]:
    assert (root / "active-network").read_text() == args[-1]
    (root / "active-network").unlink()
    sys.exit(0)
if args[0] == "inspect":
    print(os.environ.get("TEST_PROXY_NETWORK", "bridge"))
    sys.exit(0)
if args[0] == "ps" and os.environ.get("TEST_PROXY_NETWORK"):
    print("proxy-id")
if args[0] == "exec" and "cat" in args:
    print(os.environ.get("TEST_PROXY_ALLOW" if args[-1].endswith("allow.txt")
                         else "TEST_PROXY_CONTAINERS", ""))
if args[0] in ("ps", "rm", "volume", "exec", "stop"):
    sys.exit(0)
if args[0] != "run":
    raise AssertionError(args)
record = {"args": args}
if args[:2] == ["run", "-d"]:
    (root / "proxy-calls.json").write_text(json.dumps(record))
    sys.exit(0)
network = args[args.index("--network") + 1]
assert (network == os.environ.get("TEST_EXISTING_NETWORK") or
        network == (root / "active-network").read_text())
for option in args:
    if "target=/etc/aicontainer/dns" in option:
        fields = dict(part.split("=", 1) for part in option.split(",") if "=" in part)
        snapshot = Path(fields["source"])
        record["snapshot"] = str(snapshot)
        record["names"] = snapshot.read_text().splitlines()
        record["readonly"] = "readonly" in option.split(",")
        # 元の設定を書き換えても、起動時のコピーは変わらないことを確認する。
        if os.environ.get("DNS_CONFIG_TEST_MUTATE"):
            (root / ".aicontainer").write_text("dns=unexpected.example\\n")
            record["names_after_edit"] = snapshot.read_text().splitlines()
(root / "docker-calls.json").write_text(json.dumps(record))
sys.exit(int(os.environ.get("DNS_CONFIG_TEST_EXIT", "0")))
''')
        mock.chmod(0o755)
        self.env = dict(os.environ, DNS_CONFIG_TEST_DIR=str(self.root),
                        OPENAI_API_KEY="", PATH=str(self.root) + os.pathsep + os.environ["PATH"])

    def run_launcher(self, *args):
        return subprocess.run(["bash", "-c", 'source "$1"; aicontainer "${@:2}"',
                               "test", str(LAUNCHER), *args], cwd=self.root, env=self.env,
                              text=True, capture_output=True)

    def docker_events(self):
        return [json.loads(line) for line in (self.root / "docker-events.jsonl").read_text().splitlines()]

    def assert_dedicated_network_lifecycle(self, name):
        events = self.docker_events()
        creates = [event for event in events if event[:2] == ["network", "create"]]
        self.assertEqual(len(creates), 1)
        create = creates[0]
        self.assertEqual(create[-1], name)
        self.assertEqual(create[create.index("--driver") + 1], "bridge")
        self.assertIn("com.docker.network.bridge.enable_icc=false", create)
        self.assertNotIn("--internal", create)  # 外部APIへの経路は維持する。
        run = next(event for event in events if event[:2] == ["run", "-ti"])
        remove = ["network", "rm", name]
        self.assertLess(events.index(create), events.index(run))
        self.assertLess(events.index(run), events.index(remove))
        self.assertFalse((self.root / "active-network").exists())

    def assert_shared_network_usage(self, name, created=False):
        events = self.docker_events()
        self.assertIn(["network", "inspect", name], events)
        self.assertFalse(any(event[:2] == ["network", "rm"] for event in events))
        creates = [event for event in events if event[:2] == ["network", "create"]]
        if created:
            self.assertEqual(len(creates), 1)
            self.assertEqual(creates[0][-1], name)
            self.assertNotIn("com.docker.network.bridge.enable_icc=false", creates[0])
            self.assertEqual((self.root / "active-network").read_text(), name)
        else:
            self.assertEqual(creates, [])

    def test_repeated_dns_settings_are_passed_in_a_readonly_snapshot(self):
        self.config.write_text("network=project\ndns=postgres\ndns='web_api'\ndns=service.local   \n")
        self.env["DNS_CONFIG_TEST_MUTATE"] = "1"
        self.env["TEST_EXISTING_NETWORK"] = "project"
        result = self.run_launcher("codex")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = json.loads(self.calls.read_text())
        self.assertEqual(record["names"], ["postgres", "web_api", "service.local"])
        self.assertEqual(record["names_after_edit"], record["names"])
        self.assertTrue(record["readonly"])
        self.assertEqual(record["args"][record["args"].index("--network") + 1], "project")
        self.assertIn("FIREWALL_MODE=codex", record["args"])
        self.assertFalse(Path(record["snapshot"]).exists())
        self.assertFalse(Path(record["snapshot"]).parent.exists())
        self.assert_shared_network_usage("project")

    def test_dump_produces_an_executable_command_without_creating_a_snapshot(self):
        self.config.write_text("dns=postgres\ndns=redis\n")
        result = self.run_launcher("dump")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.root / "docker-events.jsonl").exists())
        command = result.stdout[result.stdout.index("(\n"):]
        check = subprocess.run(["bash", "-n"], input=command, text=True, capture_output=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        execution = subprocess.run(["bash"], input=command, cwd=self.root, env=self.env,
                                   text=True, capture_output=True)
        self.assertEqual(execution.returncode, 0, execution.stdout + execution.stderr)
        record = json.loads(self.calls.read_text())
        self.assertEqual(record["names"], ["postgres", "redis"])
        self.assertTrue(record["readonly"])
        self.assertFalse(Path(record["snapshot"]).exists())
        network = record["args"][record["args"].index("--network") + 1]
        self.assert_dedicated_network_lifecycle(network)

    def test_snapshot_is_removed_if_docker_fails(self):
        self.config.write_text("dns=postgres\n")
        self.env["DNS_CONFIG_TEST_EXIT"] = "7"
        result = self.run_launcher()
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        record = json.loads(self.calls.read_text())
        self.assertFalse(Path(record["snapshot"]).exists())
        self.assertFalse(Path(record["snapshot"]).parent.exists())
        network = record["args"][record["args"].index("--network") + 1]
        self.assert_dedicated_network_lifecycle(network)

    def test_no_dns_setting_needs_no_snapshot(self):
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = json.loads(self.calls.read_text())
        self.assertNotIn("snapshot", record)
        network = record["args"][record["args"].index("--network") + 1]
        self.assertRegex(network, r"^yumayo-ai-[A-Za-z0-9]{10}$")
        self.assert_dedicated_network_lifecycle(network)

    def test_configured_network_is_created_and_preserved_with_the_exact_name(self):
        self.config.write_text("network=project\n")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = json.loads(self.calls.read_text())["args"]
        self.assertEqual(args[args.index("--network") + 1], "project")
        self.assert_shared_network_usage("project", created=True)

    def test_existing_mcp_network_is_joined_and_preserved_while_proxy_has_no_network(self):
        self.config.write_text("network=project\ndns=mcp-server\ndocker-proxy-name=project\n")
        self.env["TEST_EXISTING_NETWORK"] = "project"
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = json.loads(self.calls.read_text())
        self.assertEqual(record["args"][record["args"].index("--network") + 1], "project")
        self.assertEqual(record["names"], ["mcp-server"])
        self.assertTrue(record["readonly"])
        self.assertFalse(Path(record["snapshot"]).parent.exists())
        self.assert_shared_network_usage("project")
        proxy_args = json.loads((self.root / "proxy-calls.json").read_text())["args"]
        self.assertEqual(proxy_args[proxy_args.index("--network") + 1], "none")

    def test_shared_network_is_preserved_if_ai_container_fails(self):
        self.config.write_text("network=project\ndns=mcp-server\n")
        self.env.update(TEST_EXISTING_NETWORK="project", DNS_CONFIG_TEST_EXIT="7")
        result = self.run_launcher("codex")
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assert_shared_network_usage("project")
        record = json.loads(self.calls.read_text())
        self.assertFalse(Path(record["snapshot"]).parent.exists())

    def test_network_creation_failure_prevents_ai_start_and_network_removal(self):
        self.env["TEST_NETWORK_CREATE_FAIL"] = "1"
        for config in ("", "network=project\n"):
            with self.subTest(config=config):
                self.config.write_text(config)
                result = self.run_launcher()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.calls.exists())
                self.assertFalse(any(event[:2] == ["network", "rm"] for event in self.docker_events()))

    def test_reserved_and_invalid_network_names_are_rejected(self):
        for name in ("host", "none", "bridge", "container:other", "project --network host",
                     "$(touch injected)", "`touch injected`"):
            with self.subTest(name=name):
                self.config.write_text(f"network={name}\n")
                result = self.run_launcher("dump")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "injected").exists())
                self.assertFalse((self.root / "docker-events.jsonl").exists())

    def test_failed_hook_creates_no_network(self):
        self.config.write_text("before-start-up=false\n")
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "docker-events.jsonl").exists())

    def test_each_execution_of_dump_creates_a_new_network(self):
        result = self.run_launcher("dump", "codex")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "docker-events.jsonl").exists())
        command = result.stdout[result.stdout.index("(\n"):]
        networks = []
        for _ in range(2):
            execution = subprocess.run(["bash"], input=command, cwd=self.root, env=self.env,
                                       text=True, capture_output=True)
            self.assertEqual(execution.returncode, 0, execution.stdout + execution.stderr)
            args = json.loads(self.calls.read_text())["args"]
            networks.append(args[args.index("--network") + 1])
            self.assertFalse((self.root / "active-network").exists())
        self.assertNotEqual(*networks)

    def test_dump_creates_a_shared_network_once_and_reuses_it(self):
        self.config.write_text("network=project\ndns=mcp-server\n")
        result = self.run_launcher("dump", "codex")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "docker-events.jsonl").exists())
        command = result.stdout[result.stdout.index("(\n"):]
        for _ in range(2):
            execution = subprocess.run(["bash"], input=command, cwd=self.root, env=self.env,
                                       text=True, capture_output=True)
            self.assertEqual(execution.returncode, 0, execution.stdout + execution.stderr)
            record = json.loads(self.calls.read_text())
            self.assertEqual(record["args"][record["args"].index("--network") + 1], "project")
            self.assertEqual(record["names"], ["mcp-server"])
            self.assertFalse(Path(record["snapshot"]).parent.exists())
        self.assert_shared_network_usage("project", created=True)

    def test_ollama_mode_remains_disabled(self):
        for args in (("ollama", "model"), ("dump", "ollama", "model"), ()):
            with self.subTest(args=args):
                self.config.write_text("network=project\ntool=claude-ollama\nmodel=model\n")
                result = self.run_launcher(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Ollama mode is no longer supported", result.stderr)
                self.assertFalse((self.root / "docker-events.jsonl").exists())

    def test_proxy_container_order_is_passed_to_the_ai_container(self):
        self.config.write_text("docker-proxy-name=project\ndocker-proxy-allow=pytest\n"
                               "docker-proxy-containers=tools\ndocker-proxy-containers=app\n")
        result = self.run_launcher("dump")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        command = result.stdout[result.stdout.index("(\n"):]
        execution = subprocess.run(["bash"], input=command, cwd=self.root, env=self.env,
                                   text=True, capture_output=True)
        self.assertEqual(execution.returncode, 0, execution.stdout + execution.stderr)
        args = json.loads(self.calls.read_text())["args"]
        self.assertIn("DOCKER_HOST=unix:///var/run/docker-proxy/docker.sock", args)
        self.assertIn("DOCKER_PROXY_CONTAINERS=tools\napp", args)
        self.assertIn("yumayo-ai-proxy-project-sock:/var/run/docker-proxy", args)

    def test_repeated_proxy_definitions_reach_both_containers_without_shell_expansion(self):
        self.config.write_text("docker-proxy-name=project\r\n"
                               "docker-proxy-allow=python*\r\n"
                               "docker-proxy-allow='npx playwright*'\r\n"
                               "docker-proxy-allow=tool --items=a,b\r\n"
                               "docker-proxy-allow=tool $(touch injected) `touch injected`\r\n"
                               "docker-proxy-allow=   \r\n"
                               "docker-proxy-containers='tools'\r\n"
                               "docker-proxy-containers= \r\n"
                               "docker-proxy-containers=app   ")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        proxy_args = json.loads((self.root / "proxy-calls.json").read_text())["args"]
        self.assertEqual(proxy_args[proxy_args.index("--name") + 1], "yumayo-ai-proxy-project")
        self.assertEqual(proxy_args[proxy_args.index("--network") + 1], "none")
        self.assertIn("yumayo-ai-proxy-project-sock:/var/run/docker-proxy", proxy_args)
        self.assertIn("DOCKER_PROXY_ALLOW=python*\nnpx playwright*\ntool --items=a,b\n"
                      "tool $(touch injected) `touch injected`", proxy_args)
        self.assertIn("DOCKER_PROXY_CONTAINERS=tools\napp", proxy_args)
        ai_args = json.loads(self.calls.read_text())["args"]
        self.assertIn("yumayo-ai-proxy-project-sock:/var/run/docker-proxy", ai_args)
        self.assertIn("DOCKER_PROXY_CONTAINERS=tools\napp", ai_args)
        self.assertFalse((self.root / "injected").exists())

    def test_running_proxy_on_bridge_is_recreated_without_network(self):
        self.config.write_text("docker-proxy-name=project\ndocker-proxy-allow=pytest\n"
                               "docker-proxy-containers=tools\n")
        self.env.update(TEST_PROXY_NETWORK="bridge", TEST_PROXY_ALLOW="pytest",
                        TEST_PROXY_CONTAINERS="tools")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(["stop", "yumayo-ai-proxy-project"], self.docker_events())
        proxy_args = json.loads((self.root / "proxy-calls.json").read_text())["args"]
        self.assertEqual(proxy_args[proxy_args.index("--network") + 1], "none")
        self.assertIn("yumayo-ai-proxy-project-sock:/var/run/docker-proxy", proxy_args)

    def test_running_proxy_without_network_is_reused(self):
        self.config.write_text("docker-proxy-name=project\ndocker-proxy-allow=pytest\n"
                               "docker-proxy-containers=tools\n")
        self.env.update(TEST_PROXY_NETWORK="none", TEST_PROXY_ALLOW="pytest",
                        TEST_PROXY_CONTAINERS="tools")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "proxy-calls.json").exists())
        self.assertNotIn(["stop", "yumayo-ai-proxy-project"], self.docker_events())
        self.assertIn("DOCKER_HOST=unix:///var/run/docker-proxy/docker.sock",
                      json.loads(self.calls.read_text())["args"])

    def test_undefined_proxy_lists_are_passed_as_empty_values(self):
        self.config.write_text("docker-proxy-name=project\n")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        proxy_args = json.loads((self.root / "proxy-calls.json").read_text())["args"]
        self.assertIn("DOCKER_PROXY_ALLOW=", proxy_args)
        self.assertIn("DOCKER_PROXY_CONTAINERS=", proxy_args)
        self.assertIn("DOCKER_PROXY_CONTAINERS=", json.loads(self.calls.read_text())["args"])

    def test_proxy_definitions_are_reset_between_launches_in_the_same_shell(self):
        self.config.write_text("docker-proxy-name=project\ndocker-proxy-allow=*\n"
                               "docker-proxy-containers=old\n")
        updated = ("docker-proxy-name=project\ndocker-proxy-allow=pytest\n"
                   "docker-proxy-containers=tools\n")
        result = subprocess.run(["bash", "-c",
                                 'source "$1"; aicontainer || exit; '
                                 'printf "%s" "$2" > .aicontainer; aicontainer',
                                 "test", str(LAUNCHER), updated], cwd=self.root, env=self.env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        proxy_args = json.loads((self.root / "proxy-calls.json").read_text())["args"]
        self.assertIn("DOCKER_PROXY_ALLOW=pytest", proxy_args)
        self.assertIn("DOCKER_PROXY_CONTAINERS=tools", proxy_args)
        ai_args = json.loads(self.calls.read_text())["args"]
        self.assertIn("DOCKER_PROXY_CONTAINERS=tools", ai_args)

    def test_invalid_names_are_rejected_without_shell_evaluation(self):
        for name in ("", "postgres:5432", "postgres redis", "$(touch injected)",
                     "`touch injected`", "a" * 254):
            with self.subTest(name=name):
                self.config.write_text(f"dns={name}\n")
                result = self.run_launcher("dump")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "injected").exists())
                self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
