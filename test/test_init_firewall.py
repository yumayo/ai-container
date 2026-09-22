"""実行方法: python3 -m unittest discover -s test -p 'test_init_firewall.py'

テスト専用のhostsファイルと模擬ネットワークコマンドでスクリプトを検証する。
管理者権限やDNS通信は不要で、実環境のファイアウォールも変更しない。
"""

import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "docker/aicontainer/init-firewall.sh"
ANSWERS = {
    "api.anthropic.com": ["203.0.113.10", "203.0.113.11"],
    "api.openai.com": ["203.0.113.20", "203.0.113.21"],
    "chatgpt.com": ["203.0.113.20"],
    "auth0.openai.com": ["203.0.113.22"],
    "auth.openai.com": ["203.0.113.23"],
    "example.com": ["198.51.100.10"],
}


def verdict(state, address, port, protocol="tcp", established=False, chain="OUTPUT", interface=None):
    """生成されたフィルタールールと既定のポリシーでパケットを判定する。"""
    family = "ip6tables" if ":" in address else "iptables"
    if interface is None:
        local = ipaddress.ip_address(address).is_loopback or address == "172.18.0.2"
        interface = "lo" if local else "eth0"
    address_flag, interface_flag = ("-d", "-o") if chain == "OUTPUT" else ("-s", "-i")
    port_flag = "--dport" if chain == "OUTPUT" else "--sport"
    for rule in state.get(family, {}).get(chain, []):
        if "-p" in rule and rule[rule.index("-p") + 1] != protocol:
            continue
        if port_flag in rule and int(rule[rule.index(port_flag) + 1]) != port:
            continue
        if address_flag in rule and ipaddress.ip_address(address) not in ipaddress.ip_network(
            rule[rule.index(address_flag) + 1], strict=False
        ):
            continue
        if interface_flag in rule and rule[rule.index(interface_flag) + 1] != interface:
            continue
        if "--state" in rule and not established:
            continue
        if "--match-set" in rule and address not in state.get("allowed", []):
            continue
        return rule[rule.index("-j") + 1]
    return state.get(family + "_policy", {}).get(chain, "ACCEPT")


def mock_main():
    root = Path(os.environ["FIREWALL_TEST_DIR"])
    state = json.loads((root / "state.json").read_text())
    hosts = (root / "hosts").read_text()
    command, args = Path(sys.argv[0]).name, sys.argv[1:]
    event = {"command": command, "args": args, "hosts": hosts}
    state.setdefault("events", []).append(event)
    result = 0
    if command == "getent":
        domain = args[-1]
        ips = [line.split()[0] for line in hosts.splitlines()
               if line and not line.startswith("#") and domain in line.split()[1:]]
        event["source"] = "hosts" if ips else "dns"
        if not ips and verdict(state, "127.0.0.11", 53, "udp") == "ACCEPT":
            ips = state["answers"].get(domain, [])
        for address in ips:
            # NSSは同じアドレスをソケット種別ごとに返す。
            for kind in ("STREAM", "DGRAM", "RAW"):
                print(address, kind, domain)
        result = 0 if ips else 2
    elif command in ("iptables", "ip6tables"):
        if "-t" not in args:
            if args[0] == "-F":
                state[command] = {}
            elif args[0] == "-P":
                state.setdefault(command + "_policy", {})[args[1]] = args[2]
            elif args[0] in ("-A", "-I"):
                rules = state.setdefault(command, {}).setdefault(args[1], [])
                rules.insert(0 if args[0] == "-I" else len(rules), args[2:])
    elif command == "ipset":
        if args[0] in ("destroy", "create"):
            state["allowed"] = []
        elif args[0] == "add":
            if args[2] in state["allowed"]:
                result = 0 if "-exist" in args else 1
            else:
                state["allowed"].append(args[2])
        elif args[0] == "test":
            result = 0 if args[2] in state["allowed"] else 1
    elif command == "curl":
        domain = args[-1].removeprefix("https://")
        ips = [line.split()[0] for line in hosts.splitlines()
               if line and not line.startswith("#") and domain in line.split()[1:]]
        if "--resolve" in args:
            ips = [args[args.index("--resolve") + 1].rsplit(":", 1)[1]]
        event["resolved"] = ips
        result = 6 if not ips else (0 if verdict(state, ips[0], 443) == "ACCEPT" else 7)
        if state.get("allow_blocked") and domain == "example.com":
            result = 0
        if state.get("deny_allowed") and domain != "example.com":
            result = 7
    (root / "state.json").write_text(json.dumps(state))
    sys.exit(result)


class FirewallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hosts = self.root / "hosts"
        self.original_hosts = "127.0.0.1 localhost\n172.18.0.2 container custom-alias\n"
        self.hosts.write_text(self.original_hosts)
        self.dns = self.root / "dns"
        self.script = self.root / "init-firewall.sh"
        self.script.write_text(SCRIPT.read_text().replace("/etc/hosts", str(self.hosts))
                               .replace("/etc/aicontainer/dns", str(self.dns)))
        mock = self.root / "mock"
        mock.write_text(
            f"#!{sys.executable}\nimport sys\n"
            f"sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
            "from test_init_firewall import mock_main\nmock_main()\n"
        )
        mock.chmod(0o755)
        for command in ("iptables", "ip6tables", "ipset", "getent", "curl"):
            (self.root / command).symlink_to(mock)
        self.save_state({"answers": ANSWERS})

    def save_state(self, state):
        (self.root / "state.json").write_text(json.dumps(state))

    def state(self):
        return json.loads((self.root / "state.json").read_text())

    def run_script(self, mode="claude", success=True):
        env = dict(os.environ, FIREWALL_TEST_DIR=str(self.root), PYTHONDONTWRITEBYTECODE="1",
                   PATH=str(self.root) + os.pathsep + os.environ["PATH"])
        result = subprocess.run(["bash", str(self.script), mode], env=env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return self.state()

    def test_required_names_are_saved_before_firewall_changes_in_both_modes(self):
        for mode, domains in (("claude", ["api.anthropic.com"]),
                              ("codex", list(ANSWERS)[1:5])):
            with self.subTest(mode=mode):
                self.hosts.write_text(self.original_hosts)
                inode = self.hosts.stat().st_ino
                self.save_state({"answers": ANSWERS})
                state = self.run_script(mode)
                first_rule = next(e for e in state["events"] if e["command"] == "iptables")
                for domain in domains + ["example.com"]:
                    for address in ANSWERS[domain]:
                        self.assertIn(f"{address}\t{domain}\n", first_rule["hosts"])
                self.assertEqual(self.hosts.stat().st_ino, inode)
                self.assertTrue(self.hosts.read_text().startswith(self.original_hosts))
                self.assertEqual(set(state["allowed"]),
                                 {ip for domain in domains for ip in ANSWERS[domain]})
                blocked_probe = next(e for e in state["events"]
                                     if e["command"] == "curl" and e["args"][-1] == "https://example.com")
                self.assertEqual(blocked_probe["resolved"], ANSWERS["example.com"])

    def test_dns_is_rejected_even_on_otherwise_permitted_paths(self):
        self.dns.write_text("postgres\n")
        self.save_state({"answers": {**ANSWERS, "postgres": ["172.18.0.3"]}})
        state = self.run_script()
        for address in ("8.8.8.8", "127.0.0.1", "127.0.0.11", "172.18.0.1", "172.18.0.3",
                        "203.0.113.10", "::1", "2001:4860:4860::8888"):
            for protocol in ("udp", "tcp"):
                for established in (False, True):
                    with self.subTest(address=address, protocol=protocol, established=established):
                        self.assertEqual(verdict(state, address, 53, protocol, established), "REJECT")
        for port in (53, 34567):
            for protocol in ("udp", "tcp"):
                self.assertEqual(verdict(state, "127.0.0.11", port, protocol), "REJECT")
        self.assertEqual(verdict(state, "203.0.113.10", 443), "ACCEPT")
        self.assertEqual(verdict(state, "127.0.0.1", 8080), "REJECT")
        self.assertEqual(verdict(state, "172.18.0.1", 8080), "REJECT")
        self.assertEqual(verdict(state, "198.51.100.10", 443), "REJECT")

    def test_only_api_addresses_and_docker_peers_are_allowed(self):
        self.dns.write_text("postgres\n")
        self.save_state({"answers": {**ANSWERS, "postgres": ["172.18.0.3", "172.18.42.10"]}})
        state = self.run_script()
        for established in (False, True):
            for address in ("127.0.0.1", "127.0.0.11", "172.18.0.1", "172.18.0.2",
                            "192.168.1.10", "198.51.100.10", "172.19.0.3", "::1",
                            "2606:4700:4700::1111"):
                with self.subTest(address=address, established=established):
                    self.assertEqual(verdict(state, address, 443, established=established), "REJECT")
                    self.assertNotEqual(verdict(state, address, 443, established=established,
                                                chain="INPUT"), "ACCEPT")
            # サブネットの推測を使わず、指定した名前から解決したIPだけを許可する。
            for address in ("172.18.0.3", "172.18.42.10"):
                self.assertEqual(verdict(state, address, 8080, established=established), "ACCEPT")
                self.assertEqual(verdict(state, address, 8080, established=established, chain="INPUT"),
                                 "ACCEPT" if established else "DROP")
            for protocol in ("tcp", "udp"):
                for port in (22, 80, 443, 8443, 65535):
                    with self.subTest(protocol=protocol, port=port, established=established):
                        self.assertEqual(verdict(state, "203.0.113.10", port, protocol,
                                                 established=established), "ACCEPT")
                        self.assertEqual(verdict(state, "203.0.113.10", port, protocol,
                                                 established=established, chain="INPUT"),
                                         "ACCEPT" if established else "DROP")
                        self.assertEqual(verdict(state, "198.51.100.10", port, protocol,
                                                 established=established), "REJECT")
        self.assertEqual(verdict(state, "203.0.113.10", 443), "ACCEPT")
        self.assertEqual(verdict(state, "203.0.113.10", 443, established=True, chain="INPUT"), "ACCEPT")
        self.assertNotEqual(verdict(state, "203.0.113.10", 443, chain="INPUT"), "ACCEPT")
        self.assertEqual(verdict(state, "172.18.0.4", 8080), "REJECT")

    def test_docker_peers_are_not_allowed_without_dns_configuration(self):
        state = self.run_script()
        for address in ("172.18.0.3", "172.18.42.10", "10.42.9.10"):
            self.assertEqual(verdict(state, address, 8080), "REJECT")

    def test_invalid_dns_names_leave_hosts_and_firewall_untouched(self):
        for name in ("", "-postgres", "postgres:5432", "postgres redis", "$(touch injected)", "a" * 254):
            with self.subTest(name=name):
                self.dns.write_text(name + "\n")
                self.save_state({"answers": ANSWERS})
                state = self.run_script(success=False)
                self.assertEqual(self.hosts.read_text(), self.original_hosts)
                self.assertFalse(state.get("events"))

    def test_configured_names_are_saved_once_and_work_without_dns_on_rerun(self):
        self.dns.write_text("postgres\napi.anthropic.com\nPOSTGRES\nredis\n")
        self.save_state({"answers": {**ANSWERS, "postgres": ["172.18.0.3", "172.18.0.4"],
                                     "redis": ["172.18.0.5"]}})
        state = self.run_script()
        hosts = self.hosts.read_text()
        first_rule = next(e for e in state["events"] if e["command"] == "iptables")
        for address, name in (("172.18.0.3", "postgres"), ("172.18.0.4", "postgres"),
                              ("172.18.0.5", "redis"), ("203.0.113.10", "api.anthropic.com")):
            self.assertEqual(first_rule["hosts"].count(f"{address}\t{name}\n"), 1)
            self.assertEqual(verdict(state, address, 5432), "ACCEPT")
        self.assertEqual(sum(e["command"] == "getent" and e["args"][-1] == "postgres"
                             for e in state["events"]), 1)
        state["answers"] = {}
        state["events"] = []
        self.save_state(state)
        state = self.run_script()
        self.assertEqual(self.hosts.read_text(), hosts)
        self.assertTrue(all(e["source"] == "hosts" for e in state["events"]
                            if e["command"] == "getent"))

    def test_missing_configured_name_stops_before_firewall_changes(self):
        self.dns.write_text("missing-container\n")
        state = self.run_script(success=False)
        self.assertEqual(self.hosts.read_text(), self.original_hosts)
        self.assertTrue(all(e["command"] == "getent" for e in state["events"]))

    def test_verification_domain_can_also_be_explicitly_allowed(self):
        self.dns.write_text("example.com\n")
        state = self.run_script()
        self.assertEqual(self.hosts.read_text().count("198.51.100.10\texample.com\n"), 1)
        self.assertEqual(verdict(state, "198.51.100.10", 443), "ACCEPT")

    def test_verification_uses_an_address_outside_the_allowlist(self):
        self.dns.write_text("service.local\n")
        self.save_state({"answers": {**ANSWERS, "service.local": ["198.51.100.10"],
                                     "example.com": ["198.51.100.10", "198.51.100.11"]}})
        state = self.run_script()
        probe = next(e for e in state["events"] if e["command"] == "curl"
                     and e["args"][-1] == "https://example.com")
        self.assertEqual(probe["resolved"], ["198.51.100.11"])

    def test_rerun_uses_hosts_without_dns_or_duplicate_entries(self):
        self.run_script("codex")
        first_hosts = self.hosts.read_text()
        state = self.state()
        state["answers"] = {}
        state["events"] = []
        self.save_state(state)
        state = self.run_script("codex")
        self.assertEqual(self.hosts.read_text(), first_hosts)
        self.assertTrue(all(e["source"] == "hosts" for e in state["events"]
                            if e["command"] == "getent"))

    def test_old_managed_entries_are_replaced_and_custom_entries_preserved(self):
        self.hosts.write_text(self.original_hosts + "# BEGIN ai-container firewall\n"
                             "203.0.113.99 stale.example\n# END ai-container firewall\n"
                             "172.18.0.3 another-container\n")
        self.run_script()
        hosts = self.hosts.read_text()
        self.assertNotIn("stale.example", hosts)
        self.assertIn("172.18.0.3 another-container\n", hosts)
        self.assertEqual(hosts.count("# BEGIN ai-container firewall"), 1)

    def test_resolution_failure_leaves_hosts_and_firewall_untouched(self):
        for domain, answer in (("api.anthropic.com", []), ("example.com", []),
                               ("api.anthropic.com", ["999.1.2.3"]),
                               ("api.anthropic.com", ["bad-address"])):
            with self.subTest(domain=domain, answer=answer):
                self.save_state({"answers": {**ANSWERS, domain: answer}})
                state = self.run_script(success=False)
                self.assertEqual(self.hosts.read_text(), self.original_hosts)
                self.assertTrue(all(e["command"] == "getent" for e in state["events"]))

    def test_verification_rejects_unrestricted_or_broken_connectivity(self):
        for failure in ("allow_blocked", "deny_allowed"):
            with self.subTest(failure=failure):
                self.save_state({"answers": ANSWERS, failure: True})
                self.run_script(success=False)


if __name__ == "__main__":
    unittest.main()
