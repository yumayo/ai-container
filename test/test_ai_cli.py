"""コンテナ共通指示の注入を、APIやDockerを使わずCLIの差し替えで検証する。"""

import json
import os
from pathlib import Path
import shlex
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
        self.config_content = 'model = "existing-model"\n[features]\napps = true\nworktrees = true\nimage_generation = true\n'
        (self.session / "config.toml").write_text(self.config_content)
        self.feature_list = self.root / "features.txt"
        self.feature_list.write_text(
            "apps stable true\nworktrees stable true\nimage_generation stable true\n"
            "future_remote under development true\nfuture_disabled experimental false\n"
            "shell_tool stable true\nunified_exec stable true\nunified_exec_tty stable true\n"
            "view_image stable true\nhooks stable true\ncode_mode_host stable true\n"
            "fast_mode stable false\nsleep_tool stable false\n"
            "old_flag removed true\nlegacy_flag deprecated false\n"
        )
        self.disabled_features = {"apps", "worktrees", "image_generation", "future_remote", "future_disabled"}
        self.prompt = self.root / "system-prompt.md"
        # 引用符・改行・シェル展開文字を含む独立した指示も、正確に渡す。
        self.content = (SOURCE / "system-prompt.md").read_text() + '\n"quotes" \\ path\n$(touch injected) `touch injected` 日本語\n'
        self.prompt.write_text(self.content)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        real_bin = self.root / "real bin"
        real_bin.mkdir()
        mock = f"#!{sys.executable}\n" + '''
import json, os, pathlib, sys
args = sys.argv[1:]
if args == ["--no-daemon", "features", "list"]:
    print(pathlib.Path(os.environ["AICONTAINER_TEST_FEATURE_LIST"]).read_text(), end="")
    sys.exit(int(os.environ.get("AICONTAINER_TEST_FEATURE_LIST_EXIT", "0")))
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
                        CODEX_HOME=str(self.session), CLAUDE_CONFIG_DIR=str(self.session),
                        AICONTAINER_TEST_FEATURE_LIST=str(self.feature_list))

    def run_cli(self, tool, *args, shell=False):
        cmd = [tool, *args]
        if shell:
            cmd = ["bash", "--noprofile", "--norc", "-c", 'exec "$@"', "test", *cmd]
        return subprocess.run(cmd, cwd=self.workspace, env=self.env,
                              input="stdin payload\n", text=True, capture_output=True)

    def assert_codex_arguments(self, arguments, user_arguments):
        self.assertEqual(arguments[0], "-c")
        self.assertEqual(tomllib.loads(arguments[1]), {"developer_instructions": self.content})
        self.assertEqual(arguments[2], "-c")
        self.assertEqual(tomllib.loads(arguments[3]), {"web_search": "disabled"})
        end = arguments.index("--no-daemon")
        switches = arguments[4:end]
        self.assertEqual(len(switches) % 2, 0)
        pairs = list(zip(switches[::2], switches[1::2]))
        self.assertTrue(all(option in ("--enable", "--disable") for option, _ in pairs))
        self.assertEqual({name for option, name in pairs if option == "--enable"},
                         {"shell_tool", "unified_exec", "unified_exec_tty", "view_image", "hooks", "code_mode_host",
                          "fast_mode", "sleep_tool"})
        self.assertEqual({name for option, name in pairs if option == "--disable"}, self.disabled_features)
        self.assertEqual(arguments[end + 1:], user_arguments)

    def test_both_tools_receive_container_instructions_before_user_arguments(self):
        for tool, args in (("claude", ["--continue", "--print", "two words", ""]),
                           ("codex", []),
                           ("codex", ["exec", "--json", "two words", ""]),
                           ("codex", ["resume", "--last"])):
            with self.subTest(tool=tool, args=args):
                result = self.run_cli(tool, *args)
                self.assertEqual(result.returncode, 17, result.stderr)
                log, tool_stderr = result.stderr.splitlines()
                self.assertTrue(log.startswith("aicontainer: exec: "), log)
                self.assertEqual(tool_stderr, "tool stderr")
                record = json.loads(result.stdout)
                self.assertEqual(record["cwd"], str(self.workspace))
                self.assertEqual(record["stdin"], "stdin payload\n")
                if tool == "claude":
                    self.assertEqual(record["args"][2:], args)
                    self.assertEqual(record["args"][:2], ["--append-system-prompt-file", str(self.prompt)])
                    self.assertEqual(record["prompt"], self.content)
                else:
                    self.assert_codex_arguments(record["args"], args)
                    displayed = shlex.split(log.removeprefix("aicontainer: exec: "))
                    self.assertTrue(displayed[0].endswith("/codex"))
                    injected_end = record["args"].index("--no-daemon") + 1
                    expected = record["args"][:injected_end]
                    expected[1] = "developer_instructions=<redacted>"
                    self.assertEqual(displayed[1:injected_end + 1], expected)
        self.assertFalse((self.workspace / "injected").exists())
        self.assertEqual((self.session / "config.toml").read_text(), self.config_content)
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
                arguments = json.loads(result.stdout)["args"]
                if tool == "codex":
                    self.assert_codex_arguments(arguments, ["--version"])
                else:
                    self.assertEqual(arguments[2:], ["--version"])

    def test_new_features_are_disabled_without_changing_the_wrapper(self):
        with self.feature_list.open("a") as stream:
            stream.write("new_remote_tool under development true\n")
        self.disabled_features.add("new_remote_tool")
        result = self.run_cli("codex", "exec", "two words")
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assert_codex_arguments(json.loads(result.stdout)["args"], ["exec", "two words"])

    def test_launch_log_redacts_configs_prompts_and_environment_without_changing_arguments(self):
        prompt = 'private prompt\n$(touch injected)\x1b[31m'
        config = 'mcp_servers.private={bearer_token="private-token-for-log-test"}'
        self.env["PRIVATE_API_TOKEN"] = "private-environment-token-for-log-test"
        for tool, args in (("codex", ["exec", "--json", "-c", config, "--", prompt, ""]),
                           ("claude", ["--print", prompt, "--mcp-config", config])):
            with self.subTest(tool=tool):
                result = self.run_cli(tool, *args)
                self.assertEqual(result.returncode, 17, result.stderr)
                log, tool_stderr = result.stderr.splitlines()
                self.assertEqual(tool_stderr, "tool stderr")
                for private in (self.content, prompt, config, "private-token-for-log-test",
                                self.env["PRIVATE_API_TOKEN"], "\x1b"):
                    self.assertNotIn(private, log)
                self.assertIn("redacted", log)
                displayed = shlex.split(log.removeprefix("aicontainer: exec: "))
                record = json.loads(result.stdout)
                if tool == "codex":
                    self.assert_codex_arguments(record["args"], args)
                    self.assertEqual(displayed[-7:], ["exec", "--json", "-c", "<redacted>", "--", "<redacted>", "<redacted>"])
                else:
                    self.assertEqual(record["args"][2:], args)
                    self.assertEqual(displayed[-4:], ["--print", "<redacted>", "--mcp-config", "<redacted>"])
        self.assertFalse((self.workspace / "injected").exists())

    def test_launch_log_redacts_unknown_and_equals_form_arguments(self):
        args = ["--config=private-config", "--unknown-private-value", "private-positional-value"]
        result = self.run_cli("codex", *args)
        self.assertEqual(result.returncode, 17, result.stderr)
        log = result.stderr.splitlines()[0]
        displayed = shlex.split(log.removeprefix("aicontainer: exec: "))
        self.assertEqual(displayed[-3:], ["<redacted>"] * 3)
        self.assert_codex_arguments(json.loads(result.stdout)["args"], args)

    def test_feature_list_failure_prevents_launch(self):
        self.env["AICONTAINER_TEST_FEATURE_LIST_EXIT"] = "23"
        result = self.run_cli("codex", "exec", "two words")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("機能一覧を取得できない", result.stderr)

    def test_empty_or_invalid_feature_list_prevents_launch(self):
        for contents in ("", "\n", "unexpected\n", "future under development unknown\n",
                         "bad$(touch injected) stable true\n"):
            with self.subTest(contents=contents):
                self.feature_list.write_text(contents)
                result = self.run_cli("codex", "exec", "two words")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn("起動を中止", result.stderr)
                self.assertNotIn("tool stderr", result.stderr)
        self.assertFalse((self.workspace / "injected").exists())

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
