"""The academy command: lesson checks, course preview, and setup health for M74 Academy modules."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


PYTEST_NO_TESTS = 5
_console = Console(markup=False, highlight=False)
_errors = Console(stderr=True, markup=False, highlight=False)


@dataclass(frozen=True)
class Course:
    """One module, read from [tool.academy] in its pyproject.toml."""

    root: Path
    version: str
    course_repo: str
    chapters: dict[int, dict[int, str]]
    written: frozenset[tuple[int, int]]
    checks: tuple[str, ...]


def _read_project(folder: Path) -> dict | None:
    path = folder / "pyproject.toml"
    if not path.is_file():
        return None
    with path.open("rb") as file:
        return tomllib.load(file)


def find_root(start: Path | None = None) -> Path:
    """Return the nearest folder at or above start whose pyproject.toml has [tool.academy]."""
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        project = _read_project(folder)
        if project is not None and "academy" in project.get("tool", {}):
            return folder
    raise ValueError("No course pyproject.toml here or in a parent folder; "
                     "run academy inside your module folder")


def load_course(root: Path) -> Course:
    """Read and check the module's [tool.academy] table."""
    path = root / "pyproject.toml"
    try:
        project = _read_project(root)
        data = project["tool"]["academy"]
        chapters = {int(chapter): dict(enumerate(titles, start=1))
                    for chapter, titles in data["chapters"].items()}
        written = frozenset(tuple(int(n) for n in item.split(".")) for item in data.get("written", []))
        checks = tuple(data.get("checks", []))
        course = Course(root, project["project"]["version"], data["course-repo"],
                        chapters, written, checks)
    except (KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"Invalid [tool.academy] in {path}: {error!r}") from error
    unknown = set(checks) - set(EXTRA_CHECKS)
    if unknown:
        raise ValueError(f"Invalid [tool.academy] in {path}: unknown checks {sorted(unknown)}")
    return course


def _cli_version() -> str:
    try:
        return version("m74-academy-cli")
    except PackageNotFoundError:
        return "unreleased"


GUIDES = "https://github.com/m74-academy/community/blob/main/guides/"


def _test(course: Course, chapter: int, lesson: int | None) -> int:
    """Run the matching pytest files from this editable checkout."""
    ROOT = course.root
    _console.print("Project:", str(ROOT), style="dim", soft_wrap=True)
    lessons = course.chapters[chapter]
    folder = f"chapter_{chapter:02}"
    if (chapter, lesson) in course.written:
        answer = ROOT / "answers" / folder / f"lesson_{lesson:02}.md"
        if not answer.is_file():
            raise ValueError(f"Missing project file: {answer}")
        _console.print(Panel(
            Text(f"{lessons[lesson]}\n\nEdit: {answer.relative_to(ROOT)}\n"
                 "Open this Markdown file, write under each heading, and save.\n"
                 "Use the lesson's self-check to review your answer.\n"
                 "This command does not read or grade your answer."),
            title="WRITTEN ACTIVITY", border_style="cyan", expand=False,
        ))
        return 0

    numbers = (lesson,) if lesson is not None else tuple(n for n in lessons if (chapter, n) not in course.written)
    test_folder = ROOT / "tests" / folder
    failed = False
    for number in numbers:
        source = ROOT / "src" / folder / f"lesson_{number:02}.py"
        test = test_folder / f"test_lesson_{number:02}.py"
        for path in (source, test):
            if not path.is_file():
                raise ValueError(f"Missing project file: {path}")

        _console.rule(Text(f"Chapter {chapter} / Lesson {number} — {lessons[number]}",
                           style="bold cyan"), align="left")
        paths = Table.grid(padding=(0, 2))
        paths.add_column(style="dim")
        paths.add_column()
        paths.add_row("Edit", Text(str(source.relative_to(ROOT))))
        paths.add_row("Tests", Text(str(test.relative_to(ROOT))))
        _console.print(paths)

        command = [sys.executable, "-m", "pytest", str(test.relative_to(ROOT)),
                   "-v", "--tb=short", "--no-header", "-p", "no:cacheprovider"]
        code = subprocess.run(command, cwd=ROOT, check=False).returncode
        if code == PYTEST_NO_TESTS:
            raise ValueError(f"No tests were found for lesson {number}: {test}")
        failed = failed or code != 0

    if failed:
        _console.print(Panel(
            "Read the failed check above, edit the lesson, save, and rerun.",
            title="TRY AGAIN", border_style="yellow", expand=False,
        ))
        return 1
    _console.print(Panel(
        "Supplied checks passed.\n"
        "Answer the lesson's transfer question next.",
        title="PASS", border_style="green", expand=False,
    ))
    return 0


def _run_quiet(command: list[str], root: Path, timeout: float = 30,
               env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str] | None:
    """Run a helper command in the project; return None if it cannot start or times out."""
    try:
        return subprocess.run(command, cwd=root, capture_output=True, text=True,
                              timeout=timeout, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _repo_slug(url: str) -> str:
    """Return "owner/name" from a GitHub HTTPS or SSH remote URL."""
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url.strip())
    return match.group(1).lower() if match else ""


def _check_qt(root: Path) -> tuple[str, str, str]:
    """Start a hidden Qt application with the project's PySide6."""
    probe = "from PySide6.QtWidgets import QApplication; QApplication([])"
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    result = _run_quiet([sys.executable, "-c", probe], root, timeout=60, env=env)
    if result is not None and result.returncode == 0:
        return "OK", "Qt (PySide6) starts", ""
    detail = (result.stderr.strip().splitlines() or [""])[-1] if result is not None else "timed out"
    return "FAIL", f"Qt (PySide6) could not start: {detail}", "uv sync --locked"


# Module-specific health checks named in [tool.academy] checks.
EXTRA_CHECKS: dict[str, Callable[[Path], tuple[str, str, str]]] = {"qt": _check_qt}


def _health(course: Course) -> int:
    """Check this computer's course setup; print one line per check with a fix."""
    ROOT, COURSE_REPO = course.root, course.course_repo
    results: list[tuple[str, str, str]] = []

    def check(status: str, name: str, fix: str = "") -> None:
        results.append((status, name, fix))

    git = shutil.which("git")
    if git is None:
        check("FAIL", "Git is not installed", GUIDES + "install-uv-and-git.md")
    else:
        for key in ("user.name", "user.email"):
            value = _run_quiet(["git", "config", key], ROOT)
            if value is not None and value.stdout.strip():
                check("OK", f"Git {key} is set")
            else:
                check("FAIL", f"Git {key} is not set", f'git config --global {key} "..."')

    top = _run_quiet(["git", "rev-parse", "--show-toplevel"], ROOT) if git else None
    inside = top is not None and top.returncode == 0 and Path(top.stdout.strip()).resolve() == ROOT
    if git is not None and not inside:
        check("FAIL", "This folder is not a Git clone of your fork", GUIDES + "fork-clone-setup.md")
    remotes = {}
    if inside:
        for remote in ("origin", "upstream"):
            url = _run_quiet(["git", "remote", "get-url", remote], ROOT)
            remotes[remote] = _repo_slug(url.stdout) if url is not None and url.returncode == 0 else ""
        if remotes["origin"] and remotes["origin"] != COURSE_REPO:
            check("OK", f"origin is your fork ({remotes['origin']})")
        elif remotes["origin"] == COURSE_REPO:
            check("FAIL", "origin is the course, not your fork", GUIDES + "fork-clone-setup.md")
        else:
            check("FAIL", "origin remote is missing", GUIDES + "fork-clone-setup.md")
        if remotes["upstream"] == COURSE_REPO:
            check("OK", f"upstream is the course ({COURSE_REPO})")
        else:
            check("FAIL", f"upstream is not {COURSE_REPO}",
                  f"git remote add upstream https://github.com/{COURSE_REPO}.git")

    wanted = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    if running == wanted or running.startswith(wanted + "."):
        check("OK", f"Python {running} matches .python-version")
    else:
        check("FAIL", f"Python {running} does not match .python-version ({wanted})", "uv sync --locked")

    synced = _run_quiet(["uv", "sync", "--locked", "--check", "--inexact", "--quiet"], ROOT, timeout=60)
    if synced is None:
        check("FAIL", "uv is not installed or did not respond", GUIDES + "install-uv-and-git.md")
    elif synced.returncode == 0:
        check("OK", "Project environment is up to date")
    else:
        check("FAIL", "Project environment is out of date", "uv sync --locked")

    installed = course.version
    tags = _run_quiet(["git", "ls-remote", "--tags", "--refs", "upstream"], ROOT, timeout=15) \
        if remotes.get("upstream") == COURSE_REPO else None
    releases = sorted(tuple(int(n) for n in found)
                      for found in re.findall(r"refs/tags/v(\d+)\.(\d+)\.(\d+)$",
                                              tags.stdout if tags else "", re.MULTILINE))
    if tags is None or tags.returncode != 0 or not releases:
        check("WARN", f"Course version {installed}; could not reach upstream to compare")
    elif tuple(int(n) for n in installed.split(".")[:3]) < releases[-1]:
        latest = ".".join(map(str, releases[-1]))
        check("WARN", f"Course version {installed}; {latest} is available", GUIDES + "course-updates.md")
    else:
        check("OK", f"Course version {installed} is the latest")

    if inside:
        status = _run_quiet(["git", "status", "--porcelain"], ROOT)
        if status is not None and status.stdout.strip():
            check("INFO", "You have uncommitted changes; commit before getting updates")
    for name in course.checks:
        check(*EXTRA_CHECKS[name](ROOT))
    for tool, label in (("gh", "GitHub CLI (gh)"), ("code", "VS Code 'code' command")):
        if shutil.which(tool) is None:
            check("INFO", f"Optional: {label} not found")

    for status, name, fix in results:
        _console.print(f"{status:<5} {name}", soft_wrap=True)
        if fix:
            _console.print(f"      fix: {fix}", soft_wrap=True)
    failed = any(status == "FAIL" for status, _, _ in results)
    _console.print("\nSetup needs attention." if failed else "\nSetup looks good.")
    return 1 if failed else 0


def _docs(course: Course, *, open_browser: bool) -> int:
    """Prepare the optional docs dependencies and serve from the module root."""
    ROOT = course.root
    config = ROOT / "mkdocs.yml"
    if not config.is_file():
        raise ValueError(f"Missing project file: {config}")
    _console.print(Panel(
        "Preparing your course preview; the first run may download its tools.\n"
        + ("Your browser will open when the preview is ready.\n" if open_browser else
           "Open the local address printed below in your browser.\n")
        + "Keep this terminal open. Use a second terminal for lesson commands.\n"
        "Press Ctrl+C here to stop the preview.",
        title="COURSE DOCS", border_style="cyan", expand=False,
    ))
    command = ["uv", "run", "--locked", "--group", "docs", "zensical", "serve"]
    if open_browser:
        command.append("--open")
    try:
        return subprocess.run(command, cwd=ROOT, check=False).returncode
    except KeyboardInterrupt:
        _console.print("\nCourse preview stopped.", style="dim")
        return 0


def main() -> int:
    """Expose lesson checks as the installed academy command."""
    parser = argparse.ArgumentParser(description="Read your M74 Academy course and check lesson exercises")
    parser.add_argument("--version", action="version", version=f"academy {_cli_version()}")
    commands = parser.add_subparsers(dest="command", required=True)
    docs = commands.add_parser("docs", help="open the course preview in your browser")
    docs.add_argument("--no-open", action="store_true", help="show the address without opening a browser")
    commands.add_parser("health", help="check that your computer is set up for the course")
    test = commands.add_parser("test", help="check a chapter or one lesson")
    test.add_argument("chapter", type=int)
    test.add_argument("lesson", type=int, nargs="?")
    args = parser.parse_args()
    try:
        course = load_course(find_root())
        if args.command == "docs":
            return _docs(course, open_browser=not args.no_open)
        if args.command == "health":
            return _health(course)
        if args.chapter not in course.chapters:
            parser.error(f"chapter must be one of {sorted(course.chapters)}")
        if args.lesson is not None and args.lesson not in course.chapters[args.chapter]:
            parser.error(f"chapter {args.chapter} has lessons 1-{max(course.chapters[args.chapter])}")
        return _test(course, args.chapter, args.lesson)
    except (OSError, ValueError) as error:
        _errors.print("PROJECT ERROR", style="bold red")
        _errors.print(str(error), soft_wrap=True)
        parser.exit(2)


if __name__ == "__main__":
    raise SystemExit(main())
