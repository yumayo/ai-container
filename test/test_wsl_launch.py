"""WSL用のホスト側起動処理を、既存Dockerモックで検証する。"""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_aicontainer_dns as existing


REPOSITORY = Path(__file__).resolve().parents[1]
RUN_ID = "0123456789abcdef0123456789abcdef"
LABEL = f"io.yumayo.aicontainer.wsl-run={RUN_ID}"


class WslLaunchTest(unittest.TestCase):
    def setUp(self):
        existing.DnsConfigTest.setUp(self)
        # WSL maps even Windows paths containing spaces to a VM share path.
        # Model that mapping instead of passing a Windows path to the launcher.
        mapped = tempfile.TemporaryDirectory(prefix="ai-wsl-")
        self.addCleanup(mapped.cleanup)
        self.work_path = Path(mapped.name) / "workspace"
        self.work_path.symlink_to(self.root, target_is_directory=True)

    def launch(self, *args, run_id=RUN_ID):
        return subprocess.run(
            ["bash", str(REPOSITORY / "wsl-containers/wsl-launch.sh"),
             str(REPOSITORY), str(self.work_path), run_id, *args],
            env=self.env, text=True, capture_output=True,
        )

    def events(self):
        return existing.DnsConfigTest.docker_events(self)

    def test_legacy_firewall_settings_and_proxy_are_preserved(self):
        self.config.write_text(
            "tool=codex\nallow-dns=api.example.org\nallow-ip=192.168.1.2\n"
            "docker-proxy-name=example\ndocker-proxy-allow=python\n"
            "docker-proxy-containers=app\n"
        )
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = json.loads(self.calls.read_text())
        args = record["args"]
        self.assertIn(LABEL, args)
        self.assertIn("--cap-add=NET_ADMIN", args)
        self.assertIn("--cap-add=NET_RAW", args)
        self.assertNotIn("--privileged", args)
        self.assertNotIn("--entrypoint", args)
        self.assertNotIn("/var/run/docker.sock:/var/run/docker.sock", " ".join(args))
        self.assertIn("DOCKER_HOST=unix:///var/run/docker-proxy/docker.sock", args)
        self.assertEqual(record["names"], ["api.example.org"])
        self.assertEqual(record["ips"], ["192.168.1.2"])
        self.assertTrue(record["readonly"] and record["ip_readonly"])
        self.assertFalse(Path(record["snapshot"]).exists())
        self.assertFalse(Path(record["ip_snapshot"]).exists())
        proxy = json.loads((self.root / "proxy-calls.json").read_text())
        self.assertNotIn(LABEL, proxy["args"])

    def test_labels_only_the_dedicated_network_and_preserves_exit_status(self):
        self.config.write_text("append-network=shared\n")
        self.env["DNS_CONFIG_TEST_EXIT"] = "17"
        result = self.launch("codex")
        self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
        networks = [e for e in self.events() if e[:2] == ["network", "create"]]
        self.assertEqual(len(networks), 2)
        public = next(e for e in networks if e[-1] != "shared")
        shared = next(e for e in networks if e[-1] == "shared")
        self.assertIn(LABEL, public)
        self.assertNotIn(LABEL, shared)
        self.assertIn(["network", "rm", public[-1]], self.events())

    def test_aiignore_and_bash_mode_still_apply(self):
        (self.root / "private.txt").write_text("not for the AI")
        (self.root / ".aiignore").write_text("private.txt\n")
        self.config.write_text("tool=codex\n")
        result = self.launch("bash")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = json.loads(self.calls.read_text())["args"]
        self.assertIn(f"type=bind,source=/dev/null,target={self.work_path}/private.txt,readonly", args)
        self.assertIn("FIREWALL_MODE=codex", args)
        self.assertEqual(args[-3:], ["bash", "-ci", "bash"])

    def test_invalid_invocation_stops_before_docker(self):
        result = self.launch(run_id="bad;id")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "docker-events.jsonl").exists())

    def test_codex_modes_are_passed_through_wsl_launches(self):
        for mode in ("minimal", "restricted"):
            self.config.write_text(f"tool=codex\ncodex_mode={mode}\n")
            for tool in ("codex", "bash"):
                with self.subTest(mode=mode, tool=tool):
                    result = self.launch(tool)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    args = json.loads(self.calls.read_text())["args"]
                    self.assertIn(f"AICONTAINER_CODEX_MODE={mode}", args)

    def test_launch_profile_is_readonly_and_contents_never_enter_docker_arguments(self):
        profile = self.root / 'profile 日本語 $(touch injected).json'
        secret = "secret-for-wsl-profile-test"
        profile.write_text(json.dumps({"arguments": ["--continue", "two words"],
                                       "environment": {"TOKEN": secret}}))
        self.env["AICONTAINER_LAUNCH_PROFILE"] = str(profile)
        self.config.write_text("allow-ip=192.168.1.2\n")
        result = self.launch("codex")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = json.loads(self.calls.read_text())["args"]
        self.assertIn(f"type=bind,source={profile},target=/etc/aicontainer/launch-profile.json,readonly", args)
        self.assertIn(f"type=bind,source={REPOSITORY}/docker/aicontainer/launch-profile.cjs,target=/etc/aicontainer/launch-profile.cjs,readonly", args)
        self.assertEqual(args[-1], "node /etc/aicontainer/launch-profile.cjs /etc/aicontainer/launch-profile.json codex --yolo -c web_search=disabled")
        self.assertNotIn(secret, result.stdout + result.stderr + " ".join(args))
        self.assertFalse((self.root / "injected").exists())

    def test_invalid_launch_profile_fails_before_docker_without_changing_configuration(self):
        self.config.write_text("tool=codex\nallow-ip=192.168.1.2\n")
        original = self.config.read_bytes()
        self.env["AICONTAINER_LAUNCH_PROFILE"] = str(self.root / "missing.json")
        result = self.launch("codex")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())
        self.assertEqual(self.config.read_bytes(), original)

    def test_private_runtime_volume_preserves_snapshots_and_cleanup(self):
        # Model a separate VM volume with a path that needs shell quoting.
        runtime = self.root / 'runtime $(touch injected)'
        runtime.mkdir(mode=0o700)
        self.env["AICONTAINER_RUNTIME_ROOT"] = str(runtime)
        self.env["DNS_CONFIG_TEST_EXIT"] = "17"
        self.config.write_text("allow-dns=api.example.org\nallow-ip=192.168.1.2\n")
        result = self.launch("codex")
        self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
        record = json.loads(self.calls.read_text())
        self.assertEqual(record["names"], ["api.example.org"])
        self.assertEqual(record["ips"], ["192.168.1.2"])
        self.assertTrue(record["readonly"] and record["ip_readonly"])
        for key in ("snapshot", "ip_snapshot"):
            path = Path(record[key])
            self.assertEqual(path.parent.parent, runtime)
            self.assertFalse(path.parent.exists())
        self.assertEqual(list(runtime.iterdir()), [])
        self.assertFalse((self.root / "injected").exists())
