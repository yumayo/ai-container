"""コンテナ共通指示の注入を、APIやDockerを使わずCLIの差し替えで検証する。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "docker/aicontainer"


class AiCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aicontainer prompt ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        for name in ("AGENTS.md", "CLAUDE.md"):
            (self.workspace / name).write_text("Project-specific instructions\n")
        self.session = self.root / "session"
        self.session.mkdir()
        (self.session / "config.toml").write_text('model = "existing-model"\n')
        self.prompt = self.root / "system-prompt.md"
        # 引用符・改行・シェル展開文字を含む独立した指示も、正確に渡す。
        self.content = (SOURCE / "system-prompt.md").read_text() + '\n"quotes" \\ path\n$(touch injected) `touch injected` 日本語\n'
        self.prompt.write_text(self.content)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        real_bin = self.root / "real bin"
        real_bin.mkdir()
        mock = f"#!{sys.executable}\n" + '''
import json, pathlib, sys
args = sys.argv[1:]
record = {"args": args, "cwd": str(pathlib.Path.cwd()), "stdin": sys.stdin.read()}
if "--append-system-prompt-file" in args:
    record["prompt"] = pathlib.Path(args[args.index("--append-system-prompt-file") + 1]).read_text()
print(json.dumps(record))
print("tool stderr", file=sys.stderr)
sys.exit(17)
'''
        launcher = (SOURCE / "ai-cli").read_text().replace(
            "prompt_file=/etc/aicontainer/system-prompt.md",
            f"prompt_file='{self.prompt}'",
        ).replace("/usr/local/lib/aicontainer/bin/", f'"{real_bin}/"')
        wrapper = self.root / "ai-cli"
        wrapper.write_text(launcher)
        wrapper.chmod(0o755)
        for tool in ("claude", "codex"):
            (real_bin / tool).write_text(mock)
            (real_bin / tool).chmod(0o755)
            (self.bin / tool).symlink_to(wrapper)
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        CODEX_HOME=str(self.session), CLAUDE_CONFIG_DIR=str(self.session))

    def run_cli(self, tool, *args, shell=False):
        cmd = [tool, *args]
        if shell:
            cmd = ["bash", "--noprofile", "--norc", "-c", 'exec "$@"', "test", *cmd]
        return subprocess.run(cmd, cwd=self.workspace, env=self.env,
                              input="stdin payload\n", text=True, capture_output=True)

    def test_both_tools_receive_container_instructions_before_user_arguments(self):
        for tool, args in (("claude", ["--continue", "--print", "two words", ""]),
                           ("codex", ["exec", "--json", "two words", ""]),
                           ("codex", ["resume", "--last"])):
            with self.subTest(tool=tool, args=args):
                result = self.run_cli(tool, *args)
                self.assertEqual(result.returncode, 17, result.stderr)
                self.assertEqual(result.stderr, "tool stderr\n")
                record = json.loads(result.stdout)
                self.assertEqual(record["args"][2:], args)
                self.assertEqual(record["cwd"], str(self.workspace))
                self.assertEqual(record["stdin"], "stdin payload\n")
                if tool == "claude":
                    self.assertEqual(record["args"][:2], ["--append-system-prompt-file", str(self.prompt)])
                    self.assertEqual(record["prompt"], self.content)
                else:
                    self.assertEqual(record["args"][0], "-c")
                    self.assertEqual(tomllib.loads(record["args"][1]), {"developer_instructions": self.content})
        self.assertFalse((self.workspace / "injected").exists())
        self.assertEqual((self.session / "config.toml").read_text(), 'model = "existing-model"\n')
        self.assertEqual(sorted(p.name for p in self.session.iterdir()), ["config.toml"])
        for name in ("AGENTS.md", "CLAUDE.md"):
            self.assertEqual((self.workspace / name).read_text(), "Project-specific instructions\n")

    def test_shell_launch_also_injects_instructions_without_workspace_instruction_files(self):
        for name in ("AGENTS.md", "CLAUDE.md"):
            (self.workspace / name).unlink()
        for tool in ("claude", "codex"):
            with self.subTest(tool=tool):
                result = self.run_cli(tool, "--version", shell=True)
                self.assertEqual(result.returncode, 17, result.stderr)
                self.assertEqual(json.loads(result.stdout)["args"][2:], ["--version"])

    def test_missing_prompt_prevents_launch_without_instructions(self):
        self.prompt.unlink()
        self.assert_prompt_error()

    def test_directory_instead_of_prompt_prevents_launch(self):
        self.prompt.unlink()
        self.prompt.mkdir()
        self.assert_prompt_error()

    @unittest.skipIf(os.geteuid() == 0, "rootはファイルの読み取り権限を迂回するため")
    def test_unreadable_prompt_prevents_launch(self):
        self.prompt.chmod(0o000)
        self.addCleanup(self.prompt.chmod, 0o644)
        self.assert_prompt_error()

    @unittest.skipIf(os.geteuid() == 0, "rootはディレクトリの通過権限を迂回するため")
    def test_parent_directory_requires_search_permission(self):
        directory = self.root / "prompt directory"
        directory.mkdir()
        target = directory / "system-prompt.md"
        self.prompt.rename(target)
        self.prompt.symlink_to(target)
        # ファイル自体が0644でも、親が0644ではCLIユーザーから読み取れない。
        target.chmod(0o644)
        directory.chmod(0o644)
        self.addCleanup(directory.chmod, 0o755)
        self.assert_prompt_error()
        directory.chmod(0o755)
        for tool in ("claude", "codex"):
            with self.subTest(tool=tool):
                result = self.run_cli(tool)
                self.assertEqual(result.returncode, 17, result.stderr)

    def assert_prompt_error(self):
        for tool in ("claude", "codex"):
            with self.subTest(tool=tool):
                result = self.run_cli(tool)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn("共通システムプロンプトを読み込めません", result.stderr)
                self.assertIn(str(self.prompt), result.stderr)
                self.assertIn("bash install.sh main", result.stderr)
                self.assertIn(".aimount", result.stderr)


if __name__ == "__main__":
    unittest.main()
