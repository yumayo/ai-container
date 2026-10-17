""".aicontainerのDNS・IP許可・ネットワーク分離・プロキシ設定を、Dockerを起動せずに検証する。"""

import json
import os
from datetime import datetime, timedelta, timezone
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
if args[0] == "version":
    if os.environ.get("TEST_DOCKER_VERSION_FAIL"):
        print("Cannot connect to the Docker daemon", file=sys.stderr)
        sys.exit(1)
    api = os.environ.get("DOCKER_API_VERSION", "1.48")
    print(os.environ.get("TEST_DOCKER_VERSION", "28.0.0|28.0.0|" + api))
    sys.exit(0)
network_state = root / "networks.json"
networks = json.loads(network_state.read_text()) if network_state.exists() else []
details_file = root / "network-details.json"
network_details = json.loads(details_file.read_text()) if details_file.exists() else {}
if args[:2] == ["network", "ls"]:
    assert args[2:] == ["--no-trunc", "--filter", "name=^yumayo-ai-", "--format", "{{.ID}}"]
    if os.environ.get("TEST_NETWORK_LIST_FAIL"):
        sys.exit(1)
    for network_id, details in network_details.items():
        if details["name"] in networks and details["name"].startswith("yumayo-ai-"):
            print(network_id)
    sys.exit(0)
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
    if "--format" in args:
        details = network_details.get(args[-1])
        if details is None or os.environ.get("TEST_NETWORK_INSPECT_FAIL"):
            sys.exit(1)
        assert "{{json .Created}}" in args[args.index("--format") + 1]
        print("|".join(json.dumps(details[field]) if field == "created" else str(details[field]) for field in
                       ("name", "driver", "icc", "endpoints", "created", "label")))
        sys.exit(0)
    exists = args[-1] == os.environ.get("TEST_EXISTING_NETWORK") or args[-1] in networks
    sys.exit(0 if exists else 1)
if args[:2] == ["network", "rm"]:
    details = network_details.get(args[-1])
    if details and (details["endpoints"] or details["references"] or
                    os.environ.get("TEST_NETWORK_REMOVE_FAIL")):
        print("network has active endpoints", file=sys.stderr)
        sys.exit(1)
    networks.remove(details["name"] if details else args[-1])
    network_state.write_text(json.dumps(networks))
    sys.exit(0)
if args[:2] == ["ps", "-aq"]:
    assert args[2] == "--filter" and args[3].startswith("network=")
    if os.environ.get("TEST_NETWORK_PS_FAIL"):
        sys.exit(1)
    details = network_details[args[3].removeprefix("network=")]
    print("\\n".join(details["references"]))
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
    if "target=/etc/aicontainer/ips" in option:
        fields = dict(part.split("=", 1) for part in option.split(",") if "=" in part)
        snapshot = Path(fields["source"])
        record["ip_snapshot"] = str(snapshot)
        record["ips"] = snapshot.read_text().splitlines()
        record["ip_readonly"] = "readonly" in option.split(",")
        record["ip_mode"] = snapshot.stat().st_mode & 0o777
        if os.environ.get("DNS_CONFIG_TEST_MUTATE"):
            (root / ".aicontainer").write_text("allow-ip=0.0.0.0/0\\n")
            record["ips_after_edit"] = snapshot.read_text().splitlines()
(root / "docker-calls.json").write_text(json.dumps(record))
sys.exit(int(os.environ.get("DNS_CONFIG_TEST_EXIT", "0")))
''')
        mock.chmod(0o755)
        self.env = dict(os.environ, DNS_CONFIG_TEST_DIR=str(self.root),
                        OPENAI_API_KEY="", PATH=str(self.root) + os.pathsep + os.environ["PATH"])
        self.env.pop("DOCKER_API_VERSION", None)

    def run_launcher(self, *args):
        return subprocess.run(["bash", "-c", 'source "$1"; aicontainer "${@:2}"',
                               "test", str(LAUNCHER), *args], cwd=self.root, env=self.env,
                              text=True, capture_output=True)

    def docker_events(self):
        return [json.loads(line) for line in (self.root / "docker-events.jsonl").read_text().splitlines()]

    def test_codex_mode_reaches_normal_bash_and_dump_launches(self):
        for mode in ("minimal", "restricted"):
            self.config.write_text(f"tool=codex\ncodex_mode={mode}\n")
            for command in ((), ("codex",), ("bash",), ("dump", "codex"), ("dump", "bash")):
                with self.subTest(mode=mode, command=command):
                    result = self.run_launcher(*command)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    if command[:1] == ("dump",):
                        script = result.stdout[result.stdout.index("(\n"):]
                        execution = subprocess.run(["bash"], input=script, cwd=self.root,
                                                   env=self.env, text=True, capture_output=True)
                        self.assertEqual(execution.returncode, 0, execution.stdout + execution.stderr)
                    args = json.loads(self.calls.read_text())["args"]
                    self.assertIn(f"AICONTAINER_CODEX_MODE={mode}", args)
                    self.assertIn("FIREWALL_MODE=codex", args)
                    expected = "bash" if command[-1:] == ("bash",) else "codex --yolo -c web_search=disabled"
                    self.assertEqual(args[-1], expected)

    def test_codex_mode_defaults_to_minimal_and_accepts_quotes_comments_and_crlf(self):
        for setting, mode in (("", "minimal"), ("codex_mode=minimal\n", "minimal"),
                              ("codex_mode= 'restricted'   # comparison\r\n", "restricted")):
            with self.subTest(setting=setting):
                self.config.write_text(setting)
                result = self.run_launcher("codex")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"AICONTAINER_CODEX_MODE={mode}", json.loads(self.calls.read_text())["args"])

    def test_invalid_codex_mode_fails_before_hooks_or_docker(self):
        for mode in ("", "unknown", "minimal restricted", "$(touch injected)"):
            with self.subTest(mode=mode):
                self.config.write_text(f"codex_mode={mode}\nbefore-start-up=touch hook-ran\n"
                                       "docker-proxy-name=example\n")
                result = self.run_launcher("codex")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("codex_mode", result.stderr)
                self.assertFalse((self.root / "docker-events.jsonl").exists())
                self.assertFalse((self.root / "hook-ran").exists())
                self.assertFalse((self.root / "injected").exists())

    def test_old_docker_versions_fail_before_hook_proxy_or_network_creation(self):
        self.config.write_text("append-network=project\ndocker-proxy-name=project\n"
                               "before-start-up=touch hook-ran\n")
        for versions in ("27.5.1|28.0.0|1.47", "28.0.0|27.5.1|1.47", "27.5.1|27.5.1|1.47"):
            with self.subTest(versions=versions):
                self.env["TEST_DOCKER_VERSION"] = versions
                result = self.run_launcher()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Docker CLI と Docker Engine の両方が 28.0 以降", result.stderr)
                client, server, _ = versions.split("|")
                self.assertIn(f"CLI {client} / Engine {server}", result.stderr)
                self.assertTrue(all(event[0] == "version" for event in self.docker_events()))
                self.assertFalse((self.root / "hook-ran").exists())

    def test_supported_docker_versions_are_checked_before_creating_resources(self):
        self.config.write_text("append-network=project\ndocker-proxy-name=project\n")
        for versions in ("28.0.0|28.0.0|1.48", "29.8.1|28.5.2|1.51", "28.0.1+vendor|28.0.0|1.48"):
            with self.subTest(versions=versions):
                self.env["TEST_DOCKER_VERSION"] = versions
                events_file = self.root / "docker-events.jsonl"
                if events_file.exists():
                    events_file.unlink()
                result = self.run_launcher()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                events = self.docker_events()
                self.assertEqual(events[0][0], "version")
                self.assertEqual(sum(event[0] == "version" for event in events), 1)
                self.assertTrue(self.calls.exists())

    def test_docker_version_lookup_failure_has_a_japanese_error(self):
        self.config.write_text("append-network=project\n")
        self.env["TEST_DOCKER_VERSION_FAIL"] = "1"
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker のバージョンを取得できませんでした", result.stderr)
        self.assertNotIn("Cannot connect", result.stderr)
        self.assertEqual(len(self.docker_events()), 1)
        self.assertFalse(self.calls.exists())

    def test_unrecognized_docker_versions_have_a_japanese_error(self):
        self.config.write_text("append-network=project\n")
        for versions in ("unknown|28.0.0|1.48", "28.0.0||1.48", "28.0.0|28.0.0|unknown"):
            with self.subTest(versions=versions):
                self.env["TEST_DOCKER_VERSION"] = versions
                result = self.run_launcher()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("バージョンを判定できません", result.stderr)
                self.assertTrue(all(event[0] == "version" for event in self.docker_events()))
                self.assertFalse(self.calls.exists())

    def test_old_api_version_from_dotenv_is_rejected_before_network_creation(self):
        self.config.write_text("append-network=project\n")
        (self.root / ".env").write_text("DOCKER_API_VERSION=1.47\n")
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker API 1.48 以降が必要", result.stderr)
        self.assertIn("現在: 1.47", result.stderr)
        self.assertIn("DOCKER_API_VERSION", result.stderr)
        self.assertEqual(len(self.docker_events()), 1)
        self.assertFalse(self.calls.exists())

    def test_dump_checks_docker_versions_when_the_output_is_executed(self):
        self.config.write_text("append-network=project\n")
        self.env["TEST_DOCKER_VERSION"] = "27.5.1|28.0.0|1.47"
        result = self.run_launcher("dump")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "docker-events.jsonl").exists())
        command = result.stdout[result.stdout.index("(\n"):]
        execution = subprocess.run(["bash"], input=command, cwd=self.root, env=self.env,
                                   text=True, capture_output=True)
        self.assertNotEqual(execution.returncode, 0)
        self.assertIn("Docker CLI と Docker Engine の両方が 28.0 以降", execution.stderr)
        self.assertEqual(len(self.docker_events()), 1)
        self.assertFalse(self.calls.exists())

    def test_without_append_network_docker_28_is_not_required(self):
        self.env["TEST_DOCKER_VERSION"] = "27.5.1|27.5.1|1.47"
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any(event[0] == "version" for event in self.docker_events()))

    def assert_dedicated_network_lifecycle(self, name):
        events = self.docker_events()
        creates = [event for event in events if event[:2] == ["network", "create"] and event[-1] == name]
        self.assertEqual(len(creates), 1)
        create = creates[0]
        self.assertEqual(create[-1], name)
        self.assertEqual(create[create.index("--driver") + 1], "bridge")
        self.assertIn("com.docker.network.bridge.enable_icc=false", create)
        self.assertIn("io.yumayo.aicontainer.network=public", create)
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

    def seed_networks(self, *overrides):
        created = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        details = {}
        for index, override in enumerate(overrides, start=1):
            details[f"{index:064x}"] = dict(
                {"name": f"yumayo-ai-{index:010d}", "driver": "bridge", "icc": "false",
                 "endpoints": 0, "created": created, "label": "public", "references": []},
                **override)
        (self.root / "network-details.json").write_text(json.dumps(details))
        (self.root / "networks.json").write_text(json.dumps([n["name"] for n in details.values()]))
        return details

    def test_startup_removes_stale_dedicated_networks_including_unlabelled_ones(self):
        details = self.seed_networks({}, {"label": ""}, {"label": "<no value>"})
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        events = self.docker_events()
        create_index = next(i for i, event in enumerate(events) if event[:2] == ["network", "create"])
        for network_id, network in details.items():
            self.assertLess(events.index(["network", "rm", network_id]), create_index)
            self.assertIn(network["name"], result.stdout)
        self.assertEqual(json.loads((self.root / "networks.json").read_text()), [])

    def test_startup_preserves_shared_unrelated_and_referenced_networks(self):
        details = self.seed_networks(
            {"name": "project"}, {"name": "yumayo-ai-project"},
            {"name": "yumayo-ai-0123456789-extra"}, {"icc": "true"}, {"icc": "<no value>"},
            {"driver": "overlay"}, {"label": "shared"},
            {"endpoints": 1}, {"references": ["stopped-container"]},
            {"references": ["created-container"]})
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for network_id in details:
            self.assertNotIn(["network", "rm", network_id], self.docker_events())
        self.assertEqual(json.loads((self.root / "networks.json").read_text()),
                         [n["name"] for n in details.values()])

    def test_startup_preserves_networks_that_are_recent_or_have_unknown_creation_time(self):
        now = datetime.now(timezone.utc)
        details = self.seed_networks(
            {"created": now.isoformat()},
            {"created": (now - timedelta(minutes=4)).isoformat()},
            {"created": (now + timedelta(hours=1)).isoformat()},
            {"created": "unknown"}, {"created": ""}, {"created": "2026-99-99T00:00:00Z"})
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for network_id in details:
            self.assertNotIn(["network", "rm", network_id], self.docker_events())

    def test_startup_preserves_explicit_append_network_even_if_it_looks_dedicated(self):
        details = self.seed_networks({})
        network_id, network = next(iter(details.items()))
        self.config.write_text(f"append-network={network['name']}\n")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(["network", "rm", network_id], self.docker_events())
        self.assert_shared_network_usage(network["name"])
        self.assert_attached_networks(json.loads(self.calls.read_text()), network["name"])

    def test_startup_skips_cleanup_if_it_cannot_establish_that_a_network_is_unused(self):
        for failure in ("TEST_NETWORK_LIST_FAIL", "TEST_NETWORK_INSPECT_FAIL", "TEST_NETWORK_PS_FAIL"):
            with self.subTest(failure=failure):
                details = self.seed_networks({})
                self.env[failure] = "1"
                try:
                    result = self.run_launcher()
                finally:
                    del self.env[failure]
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                network_id, network = next(iter(details.items()))
                self.assertNotIn(["network", "rm", network_id], self.docker_events())
                self.assertIn(network["name"], json.loads((self.root / "networks.json").read_text()))
                self.assertTrue(self.calls.exists())

    def test_cleanup_removal_failure_continues_startup_and_preserves_ai_exit_status(self):
        details = self.seed_networks({})
        self.env.update(TEST_NETWORK_REMOVE_FAIL="1", DNS_CONFIG_TEST_EXIT="7")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        network_id, network = next(iter(details.items()))
        self.assertIn(["network", "rm", network_id], self.docker_events())
        self.assertIn("continuing startup", result.stderr)
        self.assertEqual(json.loads((self.root / "networks.json").read_text()), [network["name"]])
        self.assertTrue(self.calls.exists())

    def test_dump_cleans_up_stale_networks_only_when_executed(self):
        details = self.seed_networks({})
        result = self.run_launcher("dump")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "docker-events.jsonl").exists())
        command = result.stdout[result.stdout.index("(\n"):]
        execution = subprocess.run(["bash"], input=command, cwd=self.root, env=self.env,
                                   text=True, capture_output=True)
        self.assertEqual(execution.returncode, 0, execution.stdout + execution.stderr)
        for network_id in details:
            self.assertIn(["network", "rm", network_id], self.docker_events())
        self.assertEqual(json.loads((self.root / "networks.json").read_text()), [])

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
        self.assertNotIn("ip_snapshot", record)
        network = record["args"][record["args"].index("--network") + 1]
        self.assertRegex(network, r"^yumayo-ai-[a-z0-9]{10}$")
        self.assert_dedicated_network_lifecycle(network)

    def test_allow_ip_snapshot_coexists_with_dns_and_is_cleaned_up_on_exit(self):
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code):
                self.config.write_text("allow-dns=postgres\r\n"
                                       "allow-ip=192.168.1.10\r\n"
                                       "allow-ip = '172.20.0.0/16'   # shared network\r\n"
                                       'allow-ip="10.0.0.7/32"   \r\n'
                                       "allow-ip=192.168.1.10")
                self.env["DNS_CONFIG_TEST_MUTATE"] = "1"
                self.env["DNS_CONFIG_TEST_EXIT"] = str(exit_code)
                result = self.run_launcher("codex")
                self.assertEqual(result.returncode, exit_code, result.stdout + result.stderr)
                record = json.loads(self.calls.read_text())
                self.assertEqual(record["ips"], ["192.168.1.10", "172.20.0.0/16",
                                                 "10.0.0.7/32", "192.168.1.10"])
                self.assertEqual(record["ips_after_edit"], record["ips"])
                self.assertEqual(record["names"], ["postgres"])
                self.assertTrue(record["ip_readonly"])
                self.assertEqual(record["ip_mode"], 0o444)
                self.assertIn("FIREWALL_MODE=codex", record["args"])
                for field in ("snapshot", "ip_snapshot"):
                    self.assertFalse(Path(record[field]).exists())
                    self.assertFalse(Path(record[field]).parent.exists())

    def test_allow_ip_dump_can_be_executed_repeatedly_without_dns_settings(self):
        self.config.write_text("allow-ip=0.0.0.0/0\nallow-ip=128.0.0.0/1\n"
                               "allow-ip=192.168.1.3/31\nallow-ip=255.255.255.255/32\n")
        result = self.run_launcher("dump")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "docker-events.jsonl").exists())
        command = result.stdout[result.stdout.index("(\n"):]
        # dump時点の設定を使い、実行時のワークスペース編集は反映しない。
        self.config.write_text("allow-ip=10.0.0.1\n")
        snapshots = []
        for _ in range(2):
            execution = subprocess.run(["bash"], input=command, cwd=self.root, env=self.env,
                                       text=True, capture_output=True)
            self.assertEqual(execution.returncode, 0, execution.stdout + execution.stderr)
            record = json.loads(self.calls.read_text())
            self.assertEqual(record["ips"], ["0.0.0.0/0", "128.0.0.0/1", "192.168.1.3/31",
                                             "255.255.255.255/32"])
            self.assertNotIn("snapshot", record)
            self.assertTrue(record["ip_readonly"])
            self.assertFalse(Path(record["ip_snapshot"]).parent.exists())
            snapshots.append(record["ip_snapshot"])
        self.assertNotEqual(*snapshots)

    def test_allow_ip_settings_do_not_leak_between_launcher_calls(self):
        self.config.write_text("allow-ip=192.168.1.10\n")
        result = subprocess.run(["bash", "-c",
                                 'source "$1"; aicontainer || exit; '
                                 ': > .aicontainer; aicontainer', "test", str(LAUNCHER)],
                                cwd=self.root, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("ip_snapshot", json.loads(self.calls.read_text()))

    def test_invalid_allow_ip_is_rejected_before_hooks_or_docker(self):
        for value in ("", "# empty", "256.1.2.3", "1.2.3", "1.2.3.4.5", "01.2.3.4",
                      "1.2.3.4/33", "1.2.3.4/-1", "1.2.3.4/01", "1.2.3.4/",
                      "1.2.3.4/24/24", "::1", "example.com", "1.2.3.4:80",
                      "1.2.3.4,5.6.7.8", "1.2.3.4 5.6.7.8",
                      "$(touch injected)", "`touch injected`", "1.2.3.4;touch injected"):
            for args in ((), ("dump",)):
                with self.subTest(value=value, args=args):
                    self.config.write_text(f"allow-ip=192.168.1.10\nallow-ip={value}\n"
                                           "before-start-up=touch hook-ran\n"
                                           "append-network=project\ndocker-proxy-name=project\n")
                    result = self.run_launcher(*args)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("allow-ip にはIPv4アドレスまたはCIDR", result.stderr)
                    self.assertFalse((self.root / "injected").exists())
                    self.assertFalse((self.root / "hook-ran").exists())
                    self.assertFalse((self.root / "docker-events.jsonl").exists())

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
        public_network = next(event[-1] for event in events if event[:2] == ["network", "create"])
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
