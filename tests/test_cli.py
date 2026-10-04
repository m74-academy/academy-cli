from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

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

    def test_reads_gold_lessons(self) -> None:
        """Gold lessons are read like written ones; a module without them has none."""
        with tempfile.TemporaryDirectory() as temporary:
            root = self._module(Path(temporary), 'gold = ["1.2", "2.1"]')
            self.assertEqual(cli.load_course(root).gold, {(1, 2), (2, 1)})
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(cli.load_course(self._module(Path(temporary))).gold, frozenset())

    def test_guides_default_and_module_override(self) -> None:
        """Setup guides default to the course-wide ones; a module can point at its own."""
        with tempfile.TemporaryDirectory() as temporary:
            root = self._module(Path(temporary).resolve())
            self.assertEqual(cli.load_course(root).guides, cli.GUIDES)
            own = 'guides = "https://github.com/m74-academy/module-0/blob/main/docs/setup/"'
            (root / "pyproject.toml").write_text(PROJECT.format(checks=own), encoding="utf-8")
            self.assertEqual(cli.load_course(root).guides, "https://github.com/m74-academy/module-0/blob/main/docs/setup/")

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
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = io.StringIO()
        for patcher in (patch.object(cli, "_console", Console(file=self.output)),
                        patch.object(cli, "_ensure_environment"),
                        patch.object(cli, "_run_quiet", return_value=cli.subprocess.CompletedProcess([], 0, "", ""))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _lessons(self, *numbers: int, gold: frozenset[tuple[int, int]] = frozenset()) -> cli.Course:
        """Write a starter and its test file for each coding lesson of chapter 1; lesson 1 is written."""
        for number in numbers:
            for path in (self.root / f"src/chapter_01/lesson_{number:02}.py",
                         self.root / f"tests/chapter_01/test_lesson_{number:02}.py"):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
        return cli.Course(self.root, "1.0.0", "m74-academy/module-9",
                          {1: {1: "Reading", 2: "Coding", 3: "More"}}, frozenset({(1, 1)}), (), gold=gold)

    def _pytest_codes(self, *codes: int):
        """Patch the lesson pytest runs to exit with these codes, in order."""
        results = [cli.subprocess.CompletedProcess([], code) for code in codes]
        return patch.object(cli.subprocess, "run", side_effect=results)

    def test_missing_answer_file_is_a_project_error(self) -> None:
        """A written lesson without its answer file names the missing file."""
        with self.assertRaisesRegex(ValueError, "Missing project file: .*lesson_01.md"):
            cli._test(self._lessons(), 1, 1)

    def test_missing_test_file_is_a_project_error(self) -> None:
        """A coding lesson without its test file names the missing file and does not pass."""
        course = self._lessons(2)
        (self.root / "tests/chapter_01/test_lesson_02.py").unlink()
        with self.assertRaisesRegex(ValueError, "Missing project file: .*test_lesson_02.py"):
            cli._test(course, 1, 2)
        self.assertNotIn("Supplied checks passed", self.output.getvalue())

    def test_lesson_without_tests_is_an_error(self) -> None:
        """A lesson whose test file collects nothing is a project error, not a pass."""
        with self._pytest_codes(cli.PYTEST_NO_TESTS), self.assertRaisesRegex(ValueError, "No tests"):
            cli._test(self._lessons(2), 1, 2)

    def test_chapter_with_a_lesson_without_tests_does_not_pass(self) -> None:
        """One lesson without tests stops a whole-chapter check before PASS."""
        with self._pytest_codes(0, cli.PYTEST_NO_TESTS), self.assertRaisesRegex(ValueError, "No tests"):
            cli._test(self._lessons(2, 3), 1, None)
        self.assertNotIn("Supplied checks passed", self.output.getvalue())

    def test_chapter_skips_gold_lessons_and_names_them(self) -> None:
        """A chapter run checks Core lessons only and says which Gold lessons it skipped."""
        with self._pytest_codes(0) as run:
            self.assertEqual(cli._test(self._lessons(2, 3, gold=frozenset({(1, 3)})), 1, None), 0)
        self.assertEqual(run.call_count, 1)
        self.assertIn("tests/chapter_01/test_lesson_02.py", run.call_args.args[0])
        self.assertIn("Gold lessons not checked: 1.3", self.output.getvalue())

    def test_gold_adds_gold_lessons_and_gold_sections(self) -> None:
        """--gold checks Gold lessons too, and runs a lesson's Gold section file with its Core checks."""
        course = self._lessons(2, 3, gold=frozenset({(1, 3)}))
        (self.root / "tests/chapter_01/test_lesson_02_gold.py").write_text("", encoding="utf-8")
        with self._pytest_codes(0, 0) as run:
            self.assertEqual(cli._test(course, 1, None, gold=True), 0)
        first, second = (call.args[0] for call in run.call_args_list)
        self.assertIn("tests/chapter_01/test_lesson_02_gold.py", first)
        self.assertIn("tests/chapter_01/test_lesson_03.py", second)

    def test_lesson_without_gold_flag_skips_its_gold_section(self) -> None:
        """Without --gold a lesson runs only its Core checks, even when it has a Gold section."""
        course = self._lessons(2)
        (self.root / "tests/chapter_01/test_lesson_02_gold.py").write_text("", encoding="utf-8")
        with self._pytest_codes(0) as run:
            cli._test(course, 1, 2)
        self.assertNotIn("tests/chapter_01/test_lesson_02_gold.py", run.call_args.args[0])

    def test_chapter_of_gold_lessons_needs_the_flag(self) -> None:
        """A chapter whose coding lessons are all Gold says to add --gold."""
        with self.assertRaisesRegex(ValueError, "only Gold lessons.*--gold"):
            cli._test(self._lessons(2, 3, gold=frozenset({(1, 2), (1, 3)})), 1, None)

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
    def _git(self, origin: str, upstream: str, cloned: bool = True,
             guides: str = cli.GUIDES) -> tuple[list[cli.Check], bool]:
        """Run the Git checks against fake remotes, inside a clone at the course root or outside any clone."""
        root = Path(".").resolve()
        course = cli.Course(root, "1.0.0", "m74-academy/module-9", {}, frozenset(), (), guides)
        remotes = {"origin": origin, "upstream": upstream}

        def run(command, root_arg, timeout=30, env=None):
            if command[:2] == ["git", "config"]:
                return cli.subprocess.CompletedProcess(command, 0, "set\n", "")
            if command[:2] == ["git", "rev-parse"]:
                return cli.subprocess.CompletedProcess(command, 0 if cloned else 128, f"{root}\n" if cloned else "", "")
            url = remotes[command[-1]]
            return cli.subprocess.CompletedProcess(command, 0 if url else 2, url + "\n", "")

        with patch.object(cli.shutil, "which", return_value="/usr/bin/git"), \
                patch.object(cli, "_run_quiet", side_effect=run):
            return cli._git_checks(course)

    def _checks(self, origin: str, upstream: str) -> list[cli.Check]:
        """Return only the Git checks of a clone at the course root."""
        return self._git(origin, upstream)[0]

    def test_folder_outside_a_clone_fails(self) -> None:
        """A downloaded folder that is not a clone fails and is not treated as one."""
        checks, inside = self._git("", "", cloned=False)
        self.assertFalse(inside)
        self.assertEqual(checks[-1][:2], ("FAIL", "This folder is not a Git clone of your fork"))

    def test_fixes_link_to_the_module_guides(self) -> None:
        """A remote problem's fix links to the module's own guides when it sets them."""
        checks, _ = self._git("", "", guides="https://example.test/setup/")
        fixes = [fix for status, name, fix in checks if name == "origin remote is missing"]
        self.assertEqual(fixes, ["https://example.test/setup/fork-clone-setup.md#fix-the-remotes-of-an-existing-clone"])

    def test_course_as_origin_fails(self) -> None:
        """Cloning the course instead of the fork is named as a failure."""
        checks = self._checks("git@github.com:m74-academy/module-9.git", "https://github.com/m74-academy/module-9.git")
        self.assertIn("FAIL", [status for status, name, _ in checks if name == "origin is the course, not your fork"])

    def test_any_failure_makes_health_exit_one(self) -> None:
        """Health exits 1 when a check fails and 0 when none does."""
        course = cli.Course(Path("."), "1.0.0", "m74-academy/module-9", {}, frozenset(), ())
        for checks, expected in (([("OK", "fine", "")], 0), ([("OK", "fine", ""), ("FAIL", "broken", "fix")], 1)):
            with patch.object(cli, "_git_checks", return_value=(checks, False)), \
                    patch.object(cli, "_environment_checks", return_value=([], False)), \
                    patch.object(cli, "_version_checks", return_value=[]), \
                    patch.object(cli.shutil, "which", return_value="/usr/bin/tool"), \
                    patch.object(cli, "_console", Console(file=io.StringIO())):
                self.assertEqual(cli._health(course), expected)

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


class DocsCommandTest(unittest.TestCase):
    SERVE = ["uv", "run", "--locked", "--group", "docs", "zensical", "serve", "-a", "localhost:8000"]

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "pyproject.toml").write_text(PROJECT.format(checks=""), encoding="utf-8")
        (self.root / "mkdocs.yml").write_text("site_name: Module 9\n", encoding="utf-8")
        self.errors = io.StringIO()
        for patcher in (patch.object(cli, "_console", Console(file=io.StringIO())),
                        patch.object(cli, "_errors", Console(file=self.errors)),
                        patch.object(cli, "find_root", return_value=self.root),
                        patch.object(cli, "_docs_port", return_value=8000)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _main(self, *args: str, **run_options) -> tuple[int, cli.subprocess.CompletedProcess]:
        """Run `academy docs` with args and a fake subprocess.run; return the exit code and the fake."""
        with patch("sys.argv", ["academy", "docs", *args]), \
                patch.object(cli.subprocess, "run", **run_options) as run:
            return cli.main(), run

    def test_docs_opens_the_browser_and_returns_the_server_code(self) -> None:
        """academy docs serves from the module root with --open and returns the server's exit code."""
        code, run = self._main(return_value=cli.subprocess.CompletedProcess([], 7))
        self.assertEqual(code, 7)
        run.assert_called_once_with([*self.SERVE, "--open"], cwd=self.root, check=False, env=ANY)

    def test_ctrl_c_stops_the_preview_without_an_error(self) -> None:
        """Ctrl+C ends the preview with exit code 0, and --no-open drops --open."""
        code, run = self._main("--no-open", side_effect=KeyboardInterrupt)
        self.assertEqual(code, 0)
        run.assert_called_once_with(self.SERVE, cwd=self.root, check=False, env=ANY)

    def test_missing_mkdocs_is_a_project_error(self) -> None:
        """Without mkdocs.yml the command exits 2 and names the missing file."""
        (self.root / "mkdocs.yml").unlink()
        with self.assertRaises(SystemExit) as stopped:
            self._main(return_value=cli.subprocess.CompletedProcess([], 0))
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("Missing project file", self.errors.getvalue())


if __name__ == "__main__":
    unittest.main()
