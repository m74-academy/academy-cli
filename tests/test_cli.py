from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from m74_academy_cli import cli


PROJECT = """[project]
name = "m74-academy-module-9"
version = "1.2.3"

[tool.academy]
course-repo = "m74-academy/module-9"
written = ["1.1"]
{checks}

[tool.academy.chapters]
1 = ["Reading", "Coding"]
2 = ["More"]
"""


class CourseTest(unittest.TestCase):
    def _module(self, root: Path, checks: str = "") -> Path:
        (root / "pyproject.toml").write_text(PROJECT.format(checks=checks), encoding="utf-8")
        (root / "src").mkdir()
        return root

    def test_finds_module_from_a_subfolder_and_reads_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._module(Path(temporary).resolve(), 'checks = ["qt"]')
            course = cli.load_course(cli.find_root(root / "src"))
            self.assertEqual(course.root, root)
            self.assertEqual(course.version, "1.2.3")
            self.assertEqual(course.course_repo, "m74-academy/module-9")
            self.assertEqual(course.chapters, {1: {1: "Reading", 2: "Coding"}, 2: {1: "More"}})
            self.assertEqual(course.written, {(1, 1)})
            self.assertEqual(course.checks, ("qt",))

    def test_skips_pyproject_without_academy_table(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._module(Path(temporary).resolve())
            inner = root / "src" / "other"
            inner.mkdir()
            (inner / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
            self.assertEqual(cli.find_root(inner), root)

    def test_outside_a_module_is_a_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "run academy inside your module folder"):
                cli.find_root(Path(temporary))

    def test_rejects_unknown_checks_and_missing_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._module(Path(temporary), 'checks = ["blender"]')
            with self.assertRaisesRegex(ValueError, "unknown checks"):
                cli.load_course(root)
            (root / "pyproject.toml").write_text("[tool.academy]\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid"):
                cli.load_course(root)

    def test_qt_check_reports_the_failure_line(self) -> None:
        failed = cli.subprocess.CompletedProcess([], 1, "", "Traceback\nImportError: no PySide6\n")
        with patch.object(cli, "_run_quiet", return_value=failed):
            status, name, fix = cli._check_qt(Path("."))
        self.assertEqual(status, "FAIL")
        self.assertIn("ImportError: no PySide6", name)
        self.assertEqual(fix, "uv sync --locked")


def _git_answers(tags: dict[str, str], dirty: bool = False):
    """Fake _run_quiet: upstream remote, ls-remote tags per remote, and git status."""
    def run(command, root, timeout=30, env=None):
        if command[:3] == ["git", "remote", "get-url"]:
            return cli.subprocess.CompletedProcess(command, 0, "git@github.com:m74-academy/module-9.git\n", "")
        if command[:2] == ["git", "ls-remote"]:
            listing = "".join(f"abc\trefs/tags/v{tag}\n" for tag in tags.get(command[-1], "").split())
            return cli.subprocess.CompletedProcess(command, 0, listing, "")
        if command[:2] == ["git", "status"]:
            return cli.subprocess.CompletedProcess(command, 0, " M src/a.py\n" if dirty else "", "")
        raise AssertionError(command)
    return run


class UpdateTest(unittest.TestCase):
    CLI_URL = "https://github.com/m74-academy/academy-cli.git"

    def _update(self, tags: dict[str, str], dirty: bool = False, check_only: bool = False,
                upgrade_code: int = 0, in_module: bool = True, system: str = "posix",
                ) -> tuple[str, list[list[str]]]:
        """Run _update against fake Git answers; return its output and the commands it ran directly."""
        course = cli.Course(Path("."), "1.2.3", "m74-academy/module-9", {}, frozenset(), ()) if in_module else None
        output = io.StringIO()
        ran: list[list[str]] = []

        def run(command, check=False):
            ran.append(command)
            return cli.subprocess.CompletedProcess(command, upgrade_code)

        with patch.object(cli, "_run_quiet", side_effect=_git_answers(tags, dirty)), \
                patch.object(cli, "_cli_version", return_value="0.1.0"), \
                patch.object(cli.subprocess, "run", side_effect=run), \
                patch.object(cli.os, "name", system), \
                patch.object(cli, "_console", Console(file=output, width=200)):
            self.assertEqual(cli._update(course, check_only=check_only), 0)
        return output.getvalue(), ran

    def test_latest_release_ignores_non_release_tags(self) -> None:
        with patch.object(cli, "_run_quiet", side_effect=_git_answers({"upstream": "1.10.0 1.9.2 2.0.0-rc1"})):
            self.assertEqual(cli._latest_release("upstream", Path(".")), "1.10.0")
        with patch.object(cli, "_run_quiet", side_effect=_git_answers({})):
            self.assertIsNone(cli._latest_release("upstream", Path(".")))

    def test_course_update_prints_commit_pull_and_sync(self) -> None:
        text, _ = self._update({"upstream": "1.2.3 1.3.0", self.CLI_URL: "0.1.0 0.2.0"}, dirty=True)
        self.assertIn("Course   1.2.3 (1.3.0 is available)", text)
        self.assertIn("academy  0.1.0 (0.2.0 is available)", text)
        self.assertIn("git commit", text)
        self.assertLess(text.index("git commit"), text.index("git pull --no-rebase --no-edit upstream main"))
        self.assertIn("uv sync --locked", text)

    def test_newer_academy_upgrades_the_tool_without_a_course_release(self) -> None:
        text, ran = self._update({"upstream": "1.2.3", self.CLI_URL: "0.2.0"})
        self.assertEqual(ran, [["uv", "tool", "upgrade", "m74-academy-cli"]])
        self.assertIn("academy is upgraded", text)
        self.assertNotIn("git pull", text)

    def test_check_only_and_failed_upgrade_print_the_command(self) -> None:
        text, ran = self._update({"upstream": "1.2.3", self.CLI_URL: "0.2.0"}, check_only=True)
        self.assertEqual(ran, [])
        self.assertIn("Run academy update", text)
        text, _ = self._update({"upstream": "1.2.3", self.CLI_URL: "0.2.0"}, upgrade_code=2)
        self.assertIn("uv tool upgrade m74-academy-cli", text)

    def test_upgrades_the_tool_outside_a_module(self) -> None:
        text, ran = self._update({self.CLI_URL: "0.2.0"}, in_module=False)
        self.assertEqual(ran, [["uv", "tool", "upgrade", "m74-academy-cli"]])
        self.assertIn("inside a module folder", text)
        self.assertNotIn("Course", text)

    def test_windows_prints_the_upgrade_instead_of_running_it(self) -> None:
        text, ran = self._update({"upstream": "1.2.3", self.CLI_URL: "0.2.0"}, system="nt")
        self.assertEqual(ran, [])
        self.assertIn("uv tool upgrade m74-academy-cli", text)

    def test_up_to_date(self) -> None:
        text, ran = self._update({"upstream": "1.2.3", self.CLI_URL: "0.1.0"})
        self.assertEqual(ran, [])
        self.assertIn("Everything is up to date.", text)


class EnvironmentTest(unittest.TestCase):
    def setUp(self) -> None:
        quiet = patch.object(cli, "_console", Console(file=io.StringIO()))
        quiet.start()
        self.addCleanup(quiet.stop)

    def test_setup_failure_is_not_reported_as_a_lesson_failure(self) -> None:
        stale = cli.subprocess.CompletedProcess([], 2, "", "error: The lockfile needs to be updated\n")
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(cli.shutil, "which", return_value="/bin/uv"), \
                patch.object(cli, "_run_quiet", return_value=stale):
            with self.assertRaisesRegex(ValueError, "not ready: error: The lockfile.*\n.*uv sync --locked"):
                cli._ensure_environment(Path(temporary))

    def test_missing_uv_points_to_the_install_guide(self) -> None:
        with patch.object(cli.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "install-uv-and-git"):
                cli._ensure_environment(Path("."))

    def test_module_commands_ignore_another_activated_environment(self) -> None:
        with patch.dict(cli.os.environ, {"VIRTUAL_ENV": "/elsewhere/.venv"}):
            env = cli._module_env(QT_QPA_PLATFORM="offscreen")
        self.assertNotIn("VIRTUAL_ENV", env)
        self.assertEqual(env["QT_QPA_PLATFORM"], "offscreen")

    def test_chapter_without_coding_lessons_is_an_error(self) -> None:
        course = cli.Course(Path("."), "1.0.0", "m74-academy/module-9", {1: {1: "Reading"}},
                            frozenset({(1, 1)}), ())
        with self.assertRaisesRegex(ValueError, "no coding lessons"):
            cli._test(course, 1, None)


if __name__ == "__main__":
    unittest.main()
