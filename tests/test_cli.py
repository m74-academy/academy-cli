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
        """A subfolder finds its module, and [tool.academy] is read in full."""
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
        """A nested pyproject.toml without [tool.academy] is not a module."""
        with tempfile.TemporaryDirectory() as temporary:
            root = self._module(Path(temporary).resolve())
            inner = root / "src" / "other"
            inner.mkdir()
            (inner / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
            self.assertEqual(cli.find_root(inner), root)

    def test_older_course_release_says_to_update(self) -> None:
        """A release from before DEC-0044 is named, with the update guide."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "m74-academy-module-1"\nversion = "0.7.5"\n', encoding="utf-8")
            (root / "src").mkdir()
            with self.assertRaisesRegex(ValueError, "m74-academy-module-1 0.7.5, an older course release"
                                                    "(.|\n)*course-updates(.|\n)*uv run academy"):
                cli.find_root(root / "src")

    def test_outside_a_module_is_a_clear_error(self) -> None:
        """Outside any module the error says where to run academy."""
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "run academy inside your module folder"):
                cli.find_root(Path(temporary))

    def test_rejects_unknown_checks_and_missing_fields(self) -> None:
        """Unknown extra checks and a missing table are invalid."""
        with tempfile.TemporaryDirectory() as temporary:
            root = self._module(Path(temporary), 'checks = ["blender"]')
            with self.assertRaisesRegex(ValueError, "unknown checks"):
                cli.load_course(root)
            (root / "pyproject.toml").write_text("[tool.academy]\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid"):
                cli.load_course(root)

    def test_qt_check_reports_the_failure_line(self) -> None:
        """A failed Qt probe reports its last error line and the sync fix."""
        failed = cli.subprocess.CompletedProcess([], 1, "", "Traceback\nImportError: no PySide6\n")
        with patch.object(cli, "_run_quiet", return_value=failed):
            status, name, fix = cli._check_qt(Path("."))
        self.assertEqual(status, "FAIL")
        self.assertIn("ImportError: no PySide6", name)
        self.assertEqual(fix, "uv sync --locked")


def _git_answers(tags: dict[str, str], dirty: bool = False, unmerged: str = ""):
    """Fake _run_quiet: upstream remote, ls-remote tags per remote, git status, and unmerged files."""
    def run(command, root, timeout=30, env=None):
        if command[:3] == ["git", "remote", "get-url"]:
            return cli.subprocess.CompletedProcess(command, 0, "git@github.com:m74-academy/module-9.git\n", "")
        if command[:2] == ["git", "ls-remote"]:
            listing = "".join(f"abc\trefs/tags/v{tag}\n" for tag in tags.get(command[-1], "").split())
            return cli.subprocess.CompletedProcess(command, 0, listing, "")
        if command[:2] == ["git", "status"]:
            return cli.subprocess.CompletedProcess(command, 0, " M src/a.py\n" if dirty else "", "")
        if command[:2] == ["git", "diff"]:
            return cli.subprocess.CompletedProcess(command, 0, unmerged, "")
        raise AssertionError(command)
    return run


class UpdateTest(unittest.TestCase):
    CLI_URL = "https://github.com/m74-academy/academy-cli.git"

    def _update(self, tags: dict[str, str], dirty: bool = False, check_only: bool = False,
                upgrade_code: int = 0, in_module: bool = True, windows: bool = False,
                pull_code: int = 0, unmerged: str = "", expect: int = 0,
                ) -> tuple[str, list[list[str]]]:
        """Run _update against fake Git answers; return its output and the commands it ran directly."""
        course = cli.Course(Path("."), "1.2.3", "m74-academy/module-9", {}, frozenset(), ()) if in_module else None
        output = io.StringIO()
        ran: list[list[str]] = []

        def run(command, check=False, **options):
            ran.append(command)
            code = pull_code if command[:2] == ["git", "pull"] else upgrade_code if command[0] == "uv" else 0
            return cli.subprocess.CompletedProcess(command, code)

        with patch.object(cli, "_run_quiet", side_effect=_git_answers(tags, dirty, unmerged)), \
                patch.object(cli, "_cli_version", return_value="0.1.0"), \
                patch.object(cli.subprocess, "run", side_effect=run), \
                patch.object(cli, "WINDOWS", windows), \
                patch.object(cli, "_console", Console(file=output, width=200)):
            self.assertEqual(cli._update(course, check_only=check_only), expect)
        return output.getvalue(), ran

    def test_latest_release_ignores_non_release_tags(self) -> None:
        """Only vX.Y.Z tags count, compared as numbers."""
        with patch.object(cli, "_run_quiet", side_effect=_git_answers({"upstream": "1.10.0 1.9.2 2.0.0-rc1"})):
            self.assertEqual(cli._latest_release("upstream", Path(".")), "1.10.0")
        with patch.object(cli, "_run_quiet", side_effect=_git_answers({})):
            self.assertIsNone(cli._latest_release("upstream", Path(".")))

    PULL = ["git", "pull", "--no-rebase", "--no-edit", "upstream", "main"]

    def test_course_update_pulls_then_syncs(self) -> None:
        """A newer course release is pulled, then the project is synced."""
        text, ran = self._update({"upstream": "1.2.3 1.3.0", self.CLI_URL: "0.1.0"})
        self.assertIn("Course   1.2.3 (1.3.0 is available)", text)
        self.assertEqual(ran, [self.PULL, ["uv", "sync", "--locked"]])
        self.assertIn("Course updated to 1.3.0", text)
        self.assertIn("git push", text)

    def test_uncommitted_work_stops_the_course_update(self) -> None:
        """Uncommitted changes stop the update before any pull."""
        text, ran = self._update({"upstream": "1.2.3 1.3.0", self.CLI_URL: "0.1.0"}, dirty=True, expect=1)
        self.assertEqual(ran, [])
        self.assertIn("COMMIT FIRST", text)
        self.assertIn("git commit", text)

    def test_conflict_names_the_files_and_skips_sync(self) -> None:
        """A merge conflict lists its files and leaves the sync to the student."""
        text, ran = self._update({"upstream": "1.2.3 1.3.0", self.CLI_URL: "0.1.0"},
                                 pull_code=1, unmerged="README.md\n", expect=1)
        self.assertEqual(ran, [self.PULL])
        self.assertIn("README.md", text)
        self.assertIn("git commit --no-edit", text)
        self.assertIn("uv sync --locked", text)

    def test_check_only_reports_a_course_release_without_pulling(self) -> None:
        """--check reports a course release and runs nothing."""
        text, ran = self._update({"upstream": "1.2.3 1.3.0", self.CLI_URL: "0.1.0"}, check_only=True)
        self.assertEqual(ran, [])
        self.assertIn("Run academy update to get course 1.3.0", text)

    def test_upgrades_the_tool_and_the_course_together(self) -> None:
        """The tool upgrade runs before the course pull and sync."""
        _, ran = self._update({"upstream": "1.2.3 1.3.0", self.CLI_URL: "0.1.0 0.2.0"})
        self.assertEqual(ran, [["uv", "tool", "upgrade", "m74-academy-cli"], self.PULL, ["uv", "sync", "--locked"]])

    def test_newer_academy_upgrades_the_tool_without_a_course_release(self) -> None:
        """A newer academy is installed when the course is current."""
        text, ran = self._update({"upstream": "1.2.3", self.CLI_URL: "0.2.0"})
        self.assertEqual(ran, [["uv", "tool", "upgrade", "m74-academy-cli"]])
        self.assertIn("academy is upgraded", text)
        self.assertNotIn("git pull", text)

    def test_check_only_and_failed_upgrade_print_the_command(self) -> None:
        """--check and a failed upgrade both show the upgrade command."""
        text, ran = self._update({"upstream": "1.2.3", self.CLI_URL: "0.2.0"}, check_only=True)
        self.assertEqual(ran, [])
        self.assertIn("Run academy update", text)
        text, _ = self._update({"upstream": "1.2.3", self.CLI_URL: "0.2.0"}, upgrade_code=2)
        self.assertIn("uv tool upgrade m74-academy-cli", text)

    def test_upgrades_the_tool_outside_a_module(self) -> None:
        """Outside a module only the tool is upgraded."""
        text, ran = self._update({self.CLI_URL: "0.2.0"}, in_module=False)
        self.assertEqual(ran, [["uv", "tool", "upgrade", "m74-academy-cli"]])
        self.assertIn("inside a module folder", text)
        self.assertNotIn("Course", text)

    def test_windows_prints_the_upgrade_instead_of_running_it(self) -> None:
        """Windows prints the upgrade command instead of running it."""
        text, ran = self._update({"upstream": "1.2.3", self.CLI_URL: "0.2.0"}, windows=True)
        self.assertEqual(ran, [])
        self.assertIn("uv tool upgrade m74-academy-cli", text)

    def test_windows_prints_the_upgrade_after_the_course_update(self) -> None:
        """On Windows the upgrade step comes last, after the course pull and sync."""
        text, ran = self._update({"upstream": "1.2.3 1.3.0", self.CLI_URL: "0.1.0 0.2.0"}, windows=True)
        self.assertEqual(ran, [self.PULL, ["uv", "sync", "--locked"]])
        self.assertGreater(text.index("When this command has finished"), text.index("Course updated to 1.3.0"))

    def test_up_to_date(self) -> None:
        """Nothing runs when both the tool and the course are current."""
        text, ran = self._update({"upstream": "1.2.3", self.CLI_URL: "0.1.0"})
        self.assertEqual(ran, [])
        self.assertIn("Everything is up to date.", text)


class EnvironmentTest(unittest.TestCase):
    def setUp(self) -> None:
        quiet = patch.object(cli, "_console", Console(file=io.StringIO()))
        quiet.start()
        self.addCleanup(quiet.stop)

    def test_setup_failure_is_not_reported_as_a_lesson_failure(self) -> None:
        """A broken environment is a setup error, not a failed lesson."""
        stale = cli.subprocess.CompletedProcess([], 2, "", "error: The lockfile needs to be updated\n")
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(cli.shutil, "which", return_value="/bin/uv"), \
                patch.object(cli, "_run_quiet", return_value=stale):
            with self.assertRaisesRegex(ValueError, "not ready: error: The lockfile.*\n.*uv sync --locked"):
                cli._ensure_environment(Path(temporary))

    def test_missing_uv_points_to_the_install_guide(self) -> None:
        """Missing uv points to the install guide."""
        with patch.object(cli.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "install-uv-and-git"):
                cli._ensure_environment(Path("."))

    def test_module_commands_ignore_another_activated_environment(self) -> None:
        """Module commands drop another activated VIRTUAL_ENV."""
        with patch.dict(cli.os.environ, {"VIRTUAL_ENV": "/elsewhere/.venv"}):
            env = cli._module_env(QT_QPA_PLATFORM="offscreen")
        self.assertNotIn("VIRTUAL_ENV", env)
        self.assertEqual(env["QT_QPA_PLATFORM"], "offscreen")

    def test_chapter_without_coding_lessons_is_an_error(self) -> None:
        """A chapter of written lessons only has nothing to check."""
        course = cli.Course(Path("."), "1.0.0", "m74-academy/module-9", {1: {1: "Reading"}},
                            frozenset({(1, 1)}), ())
        with self.assertRaisesRegex(ValueError, "no coding lessons"):
            cli._test(course, 1, None)



class LessonCheckTest(unittest.TestCase):
    def test_lesson_page_is_found_by_number(self) -> None:
        """The lesson page is found from its chapter and lesson numbers, or "" without one."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            page = root / "docs/chapters/01-names/02-f-strings.md"
            page.parent.mkdir(parents=True)
            page.write_text("# Lesson 2", encoding="utf-8")
            course = cli.Course(root, "1.0.0", "m74-academy/module-9", {1: {2: "f-strings"}}, frozenset(), ())
            self.assertEqual(cli._lesson_page(course, 1, 2), "docs/chapters/01-names/02-f-strings.md")
            self.assertEqual(cli._lesson_page(course, 1, 3), "")

    def test_syntax_error_is_shown_without_running_the_checks(self) -> None:
        """A file that does not compile prints its error line and returns False."""
        output = io.StringIO()
        broken = cli.subprocess.CompletedProcess([], 1, "", 'File "src/a.py", line 2\nSyntaxError: invalid syntax\n')
        with patch.object(cli, "_run_quiet", return_value=broken), \
                patch.object(cli, "_console", Console(file=output, width=100)):
            self.assertFalse(cli._file_loads(Path("/m/src/a.py"), Path("/m")))
        self.assertIn("YOUR FILE DOES NOT LOAD", output.getvalue())
        self.assertIn("SyntaxError: invalid syntax", output.getvalue())

class HealthRemotesTest(unittest.TestCase):
    def _checks(self, origin: str, upstream: str) -> list[cli.Check]:
        """Run the Git checks against fake remotes inside a clone at the course root."""
        root = Path(".").resolve()
        course = cli.Course(root, "1.0.0", "m74-academy/module-9", {}, frozenset(), ())
        remotes = {"origin": origin, "upstream": upstream}

        def run(command, root_arg, timeout=30, env=None):
            if command[:2] == ["git", "config"]:
                return cli.subprocess.CompletedProcess(command, 0, "set\n", "")
            if command[:2] == ["git", "rev-parse"]:
                return cli.subprocess.CompletedProcess(command, 0, f"{root}\n", "")
            url = remotes[command[-1]]
            return cli.subprocess.CompletedProcess(command, 0 if url else 2, url + "\n", "")

        with patch.object(cli.shutil, "which", return_value="/usr/bin/git"), \
                patch.object(cli, "_run_quiet", side_effect=run):
            return cli._git_checks(course)[0]

    def test_non_github_origin_and_missing_upstream_are_named(self) -> None:
        """A local origin is 'not a GitHub fork', and an absent upstream is 'missing'."""
        names = [name for _, name, _ in self._checks("/tmp/fork.git", "")]
        self.assertIn("origin is not a GitHub fork (/tmp/fork.git)", names)
        self.assertIn("upstream remote is missing", names)

    def test_wrong_upstream_gets_set_url(self) -> None:
        """An upstream pointing elsewhere is fixed with set-url, not add."""
        checks = self._checks("https://github.com/me/module-9.git", "https://github.com/other/x.git")
        self.assertIn(("OK", "origin is your fork (me/module-9)", ""), checks)
        self.assertIn("git remote set-url upstream https://github.com/m74-academy/module-9.git",
                      [fix for _, _, fix in checks])

class DocsPortTest(unittest.TestCase):
    def setUp(self) -> None:
        quiet = patch.object(cli, "_console", Console(file=io.StringIO()))
        quiet.start()
        self.addCleanup(quiet.stop)

    def test_busy_port_moves_to_the_next_free_one(self) -> None:
        """A taken 8000 moves the preview to the first free port after it."""
        with patch.object(cli, "_port_is_free", side_effect=lambda port: port == 8002):
            self.assertEqual(cli._docs_port(), 8002)

    def test_no_free_port_is_a_clear_error(self) -> None:
        """When every nearby port is taken, the error says to stop another preview."""
        with patch.object(cli, "_port_is_free", return_value=False):
            with self.assertRaisesRegex(ValueError, "Stop another course preview"):
                cli._docs_port()

if __name__ == "__main__":
    unittest.main()
