""".aicontainerのdns設定を、Dockerを起動せずに検証する。"""

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
if args[:2] == ["network", "inspect"]:
    sys.exit(0)
if args[0] != "run":
    raise AssertionError(args)
record = {"args": args}
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

    def test_repeated_dns_settings_are_passed_in_a_readonly_snapshot(self):
        self.config.write_text("network=project\ndns=postgres\ndns='web_api'\ndns=service.local   \n")
        self.env["DNS_CONFIG_TEST_MUTATE"] = "1"
        result = self.run_launcher("codex")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = json.loads(self.calls.read_text())
        self.assertEqual(record["names"], ["postgres", "web_api", "service.local"])
        self.assertEqual(record["names_after_edit"], record["names"])
        self.assertTrue(record["readonly"])
        self.assertIn("yumayo-ai-project", record["args"])
        self.assertIn("FIREWALL_MODE=codex", record["args"])
        self.assertFalse(Path(record["snapshot"]).exists())

    def test_dump_produces_an_executable_command_without_creating_a_snapshot(self):
        self.config.write_text("dns=postgres\ndns=redis\n")
        result = self.run_launcher("dump")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.calls.exists())
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

    def test_snapshot_is_removed_if_docker_fails(self):
        self.config.write_text("dns=postgres\n")
        self.env["DNS_CONFIG_TEST_EXIT"] = "7"
        result = self.run_launcher()
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        record = json.loads(self.calls.read_text())
        self.assertFalse(Path(record["snapshot"]).exists())

    def test_no_dns_setting_needs_no_snapshot(self):
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("snapshot", json.loads(self.calls.read_text()))

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
