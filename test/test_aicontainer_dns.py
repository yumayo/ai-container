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
network_state = root / "networks.json"
networks = json.loads(network_state.read_text()) if network_state.exists() else []
if args[:2] == ["network", "create"]:
    failure = os.environ.get("TEST_NETWORK_CREATE_FAIL")
    if (failure in ("1", args[-1]) or
            args[-1] == os.environ.get("TEST_EXISTING_NETWORK") or
            args[-1] in networks):
        sys.exit(1)
    networks.append(args[-1])
    network_state.write_text(json.dumps(networks))
    sys.exit(0)
if args[:2] == ["network", "inspect"]:
    exists = args[-1] == os.environ.get("TEST_EXISTING_NETWORK") or args[-1] in networks
    sys.exit(0 if exists else 1)
if args[:2] == ["network", "rm"]:
    networks.remove(args[-1])
    network_state.write_text(json.dumps(networks))
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
attached_networks = []
for index, arg in enumerate(args):
    if arg == "--network":
        network = args[index + 1]
        if network.startswith("name="):
            network = network.split(",")[0].removeprefix("name=")
            # 実際のDocker CLIは、name=...形式の名前を小文字に変換してAPIへ渡す。
            network = network.lower()
        assert network == os.environ.get("TEST_EXISTING_NETWORK") or network in networks
        assert network not in attached_networks
        attached_networks.append(network)
record["networks"] = attached_networks
for option in args:
    if "target=/etc/aicontainer/dns" in option:
        fields = dict(part.split("=", 1) for part in option.split(",") if "=" in part)
        snapshot = Path(fields["source"])
        record["snapshot"] = str(snapshot)
        record["names"] = snapshot.read_text().splitlines()
        record["readonly"] = "readonly" in option.split(",")
        # 元の設定を書き換えても、起動時のコピーは変わらないことを確認する。
        if os.environ.get("DNS_CONFIG_TEST_MUTATE"):
            (root / ".aicontainer").write_text("allow-dns=unexpected.example\\n")
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
        creates = [event for event in events if event[:2] == ["network", "create"] and event[-1] == name]
        self.assertEqual(len(creates), 1)
        create = creates[0]
        self.assertEqual(create[-1], name)
        self.assertEqual(create[create.index("--driver") + 1], "bridge")
        self.assertIn("com.docker.network.bridge.enable_icc=false", create)
        self.assertNotIn("--internal", create)  # 外部APIへの経路は維持する。
        run = next(event for event in events if event[:2] == ["run", "-ti"] and
                   (name in event or f"name={name},gw-priority=1" in event))
        remove = ["network", "rm", name]
        self.assertLess(events.index(create), events.index(run))
        self.assertLess(events.index(run), events.index(remove))
        self.assertNotIn(name, json.loads((self.root / "networks.json").read_text()))

    def assert_attached_networks(self, record, *appended):
        public_network = record["networks"][0]
        self.assertRegex(public_network, r"^yumayo-ai-[a-z0-9]{10}$")
        self.assertEqual(record["networks"][1:], list(appended))
        if appended:
            self.assertIn(f"name={public_network},gw-priority=1", record["args"])
        self.assert_dedicated_network_lifecycle(public_network)

    def assert_shared_network_usage(self, name, created=False):
        events = self.docker_events()
        self.assertIn(["network", "inspect", name], events)
        self.assertNotIn(["network", "rm", name], events)
        creates = [event for event in events if event[:2] == ["network", "create"] and event[-1] == name]
        if created:
            self.assertEqual(len(creates), 1)
            self.assertEqual(creates[0][-1], name)
            self.assertNotIn("com.docker.network.bridge.enable_icc=false", creates[0])
            self.assertIn(name, json.loads((self.root / "networks.json").read_text()))
        else:
            self.assertEqual(creates, [])

    def test_repeated_allow_dns_settings_are_passed_in_a_readonly_snapshot(self):
        self.config.write_text("append-network=project\nallow-dns=postgres\nallow-dns='web_api'\nallow-dns=service.local   \n")
        self.env["DNS_CONFIG_TEST_MUTATE"] = "1"
        self.env["TEST_EXISTING_NETWORK"] = "project"
        result = self.run_launcher("codex")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = json.loads(self.calls.read_text())
        self.assertEqual(record["names"], ["postgres", "web_api", "service.local"])
        self.assertEqual(record["names_after_edit"], record["names"])
        self.assertTrue(record["readonly"])
        self.assert_attached_networks(record, "project")
        self.assertIn("FIREWALL_MODE=codex", record["args"])
        self.assertFalse(Path(record["snapshot"]).exists())
        self.assertFalse(Path(record["snapshot"]).parent.exists())
        self.assert_shared_network_usage("project")

    def test_dump_produces_an_executable_command_without_creating_a_snapshot(self):
        self.config.write_text("allow-dns=postgres\nallow-dns=redis\n")
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
        self.config.write_text("allow-dns=postgres\n")
        self.env["DNS_CONFIG_TEST_EXIT"] = "7"
        result = self.run_launcher()
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        record = json.loads(self.calls.read_text())
        self.assertFalse(Path(record["snapshot"]).exists())
        self.assertFalse(Path(record["snapshot"]).parent.exists())
        network = record["args"][record["args"].index("--network") + 1]
        self.assert_dedicated_network_lifecycle(network)

    def test_no_allow_dns_setting_needs_no_snapshot(self):
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = json.loads(self.calls.read_text())
        self.assertNotIn("snapshot", record)
        network = record["args"][record["args"].index("--network") + 1]
        self.assertRegex(network, r"^yumayo-ai-[a-z0-9]{10}$")
        self.assert_dedicated_network_lifecycle(network)

    def test_generated_network_name_survives_docker_cli_lowercasing(self):
        mock = self.root / "mktemp"
        mock.write_text(f"#!{sys.executable}\n" + '''
import os
from pathlib import Path
directory = Path(os.environ["DNS_CONFIG_TEST_DIR"]) / "aicontainer.AbCd0123Ef"
directory.mkdir()
print(directory)
''')
        mock.chmod(0o755)
        for config in ("", "append-network=project\n"):
            with self.subTest(config=config):
                self.config.write_text(config)
                result = self.run_launcher()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                record = json.loads(self.calls.read_text())
                self.assertEqual(record["networks"][0], "yumayo-ai-abcd0123ef")
                creates = [event for event in self.docker_events()
                           if event[:2] == ["network", "create"]]
                self.assertTrue(all(event[-1] == event[-1].lower() for event in creates))
                self.assertNotIn("yumayo-ai-abcd0123ef",
                                 json.loads((self.root / "networks.json").read_text()))
                self.assertFalse((self.root / "aicontainer.AbCd0123Ef").exists())

    def test_configured_network_is_created_and_preserved_with_the_exact_name(self):
        self.config.write_text("append-network=project\n")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = json.loads(self.calls.read_text())
        self.assert_attached_networks(record, "project")
        self.assert_shared_network_usage("project", created=True)

    def test_existing_mcp_network_is_joined_and_preserved_while_proxy_has_no_network(self):
        self.config.write_text("append-network=project\nallow-dns=mcp-server\ndocker-proxy-name=project\n")
        self.env["TEST_EXISTING_NETWORK"] = "project"
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = json.loads(self.calls.read_text())
        self.assert_attached_networks(record, "project")
        self.assertEqual(record["names"], ["mcp-server"])
        self.assertTrue(record["readonly"])
        self.assertFalse(Path(record["snapshot"]).parent.exists())
        self.assert_shared_network_usage("project")
        proxy_args = json.loads((self.root / "proxy-calls.json").read_text())["args"]
        self.assertEqual(proxy_args[proxy_args.index("--network") + 1], "none")

    def test_shared_network_is_preserved_if_ai_container_fails(self):
        self.config.write_text("append-network=project\nallow-dns=mcp-server\n")
        self.env.update(TEST_EXISTING_NETWORK="project", DNS_CONFIG_TEST_EXIT="7")
        result = self.run_launcher("codex")
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assert_shared_network_usage("project")
        record = json.loads(self.calls.read_text())
        self.assert_attached_networks(record, "project")
        self.assertFalse(Path(record["snapshot"]).parent.exists())

    def test_network_creation_failure_prevents_ai_start_and_network_removal(self):
        self.env["TEST_NETWORK_CREATE_FAIL"] = "1"
        for config in ("", "append-network=project\n"):
            with self.subTest(config=config):
                self.config.write_text(config)
                result = self.run_launcher()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.calls.exists())
                self.assertFalse(any(event[:2] == ["network", "rm"] for event in self.docker_events()))

    def test_append_network_creation_failure_cleans_up_only_the_public_network(self):
        self.config.write_text("append-network=project\nappend-network=unavailable\nallow-dns=mcp-server\n")
        self.env["TEST_NETWORK_CREATE_FAIL"] = "unavailable"
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())
        events = self.docker_events()
        public_network = events[0][-1]
        self.assertRegex(public_network, r"^yumayo-ai-[a-z0-9]{10}$")
        self.assertIn(["network", "rm", public_network], events)
        self.assertEqual(json.loads((self.root / "networks.json").read_text()), ["project"])

    def test_reserved_and_invalid_network_names_are_rejected(self):
        for name in ("host", "none", "bridge", "container:other", "project --network host",
                     "project,other", "name=project,gw-priority=2", "$(touch injected)", "`touch injected`"):
            with self.subTest(name=name):
                self.config.write_text(f"append-network={name}\n")
                result = self.run_launcher("dump")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "injected").exists())
                self.assertFalse((self.root / "docker-events.jsonl").exists())

    def test_legacy_network_setting_reports_migration_before_start(self):
        self.config.write_text("network=project\n")
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("network は append-network に変更されました", result.stderr)
        self.assertFalse((self.root / "docker-events.jsonl").exists())

    def test_multiple_append_networks_are_attached_once_and_preserved(self):
        self.config.write_text("append-network=project\r\nappend-network='other'   \r\n"
                               "append-network=project\r\nappend-network=   \r\n")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = json.loads(self.calls.read_text())
        self.assert_attached_networks(record, "project", "other")
        self.assert_shared_network_usage("project", created=True)
        self.assert_shared_network_usage("other", created=True)

    def test_append_network_settings_are_reset_between_launches_in_the_same_shell(self):
        self.config.write_text("append-network=project\n")
        result = subprocess.run(["bash", "-c",
                                 'source "$1"; aicontainer || exit; '
                                 'printf "%s" "" > .aicontainer; aicontainer',
                                 "test", str(LAUNCHER)], cwd=self.root, env=self.env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = json.loads(self.calls.read_text())
        self.assertEqual(len(record["networks"]), 1)
        self.assertNotIn("project", record["networks"])

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
            self.assertEqual(json.loads((self.root / "networks.json").read_text()), [])
        self.assertNotEqual(*networks)

    def test_dump_creates_a_shared_network_once_and_reuses_it(self):
        self.config.write_text("append-network=project\nallow-dns=mcp-server\n")
        result = self.run_launcher("dump", "codex")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "docker-events.jsonl").exists())
        command = result.stdout[result.stdout.index("(\n"):]
        for _ in range(2):
            execution = subprocess.run(["bash"], input=command, cwd=self.root, env=self.env,
                                       text=True, capture_output=True)
            self.assertEqual(execution.returncode, 0, execution.stdout + execution.stderr)
            record = json.loads(self.calls.read_text())
            self.assert_attached_networks(record, "project")
            self.assertEqual(record["names"], ["mcp-server"])
            self.assertFalse(Path(record["snapshot"]).parent.exists())
        self.assert_shared_network_usage("project", created=True)

    def test_ollama_mode_remains_disabled(self):
        for args in (("ollama", "model"), ("dump", "ollama", "model"), ()):
            with self.subTest(args=args):
                self.config.write_text("append-network=project\ntool=claude-ollama\nmodel=model\n")
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
                self.config.write_text(f"allow-dns={name}\n")
                result = self.run_launcher("dump")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "injected").exists())
                self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
