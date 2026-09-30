from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
