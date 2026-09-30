"""The academy command: lesson checks, course preview, and setup health for M74 Academy modules.

Installed once as a uv tool (DEC-0044), it runs in its own environment and runs each
module's checks in that module's environment through `uv run`.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


GUIDES = "https://github.com/m74-academy/community/blob/main/guides/"
CLI_REPO = "m74-academy/academy-cli"
CLI_DIST = "m74-academy-cli"
CLI_URL = f"https://github.com/{CLI_REPO}.git"
WINDOWS = os.name == "nt"
PYTEST_NO_TESTS = 5
DOCS_PORT = 8000
STATUS_STYLES = {"OK": "green", "FAIL": "bold red", "WARN": "yellow", "INFO": "dim"}
EXAMPLES = """\
typical session, inside your module folder:
  academy docs        open the course in your browser; keep it running
  academy test 1 2    check chapter 1, lesson 2 (in a second terminal)
  academy test 1      check every coding lesson in chapter 1
  academy health      check your setup; each problem comes with a fix
  academy update      get the newest academy and course release
"""

# The module's own locked environment, not this tool's.
MODULE_PYTHON = ["uv", "run", "--locked", "python"]
# Health only inspects the environment; `uv sync --check` reports drift separately.
INSPECT_PYTHON = ["uv", "run", "--no-sync", "python"]

_console = Console(markup=False, highlight=False)
_errors = Console(stderr=True, markup=False, highlight=False)


# === COURSE === #

@dataclass(frozen=True)
class Course:
    """One module, read from [tool.academy] in its pyproject.toml."""

    root: Path
    version: str
    course_repo: str
    chapters: dict[int, dict[int, str]]
    written: frozenset[tuple[int, int]]
    checks: tuple[str, ...]


def _read_project(folder: Path) -> dict[str, Any] | None:
    path = folder / "pyproject.toml"
    if not path.is_file():
        return None
    with path.open("rb") as file:
        return tomllib.load(file)


def find_root(start: Path | None = None) -> Path:
    """Return the nearest folder at or above start whose pyproject.toml has [tool.academy]."""
    here = (start or Path.cwd()).resolve()
    older = None
    for folder in (here, *here.parents):
        project = _read_project(folder)
        if project is None:
            continue
        if "academy" in project.get("tool", {}):
            return folder
        name = str(project.get("project", {}).get("name", ""))
        if older is None and name.startswith("m74-academy-module-"):
            older = project["project"]

    if older is not None:
        # A course release from before DEC-0044 carries its own command.
        raise ValueError(f"This is {older['name']} {older.get('version', '')}, an older course release "
                         "that has its own command.\nGet the course update to use the installed academy: "
                         f"{GUIDES}course-updates.md\nUntil then, run: uv run academy …")
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


# === SUBPROCESSES === #

def _module_env(**extra: str) -> dict[str, str]:
    """Environment for commands in the module: drop any other activated venv so uv uses .venv."""
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    return {**env, **extra}


def _run_quiet(command: list[str], root: Path, timeout: float = 30,
               env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str] | None:
    """Run a helper command in the project; return None if it cannot start or times out."""
    try:
        return subprocess.run(command, cwd=root, capture_output=True, text=True,
                              timeout=timeout, check=False, env=env or _module_env())
    except (OSError, subprocess.TimeoutExpired):
        return None


def _last_error_line(result: subprocess.CompletedProcess[str] | None) -> str:
    """Return the last stderr line of a helper command, or why it did not run."""
    if result is None:
        return "timed out"
    lines = result.stderr.strip().splitlines()
    return lines[-1] if lines else ""


def _repo_slug(url: str) -> str:
    """Return "owner/name" from a GitHub HTTPS or SSH remote URL."""
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url.strip())
    return match.group(1).lower() if match else ""


def _remote_slug(remote: str, root: Path) -> str:
    """Return the "owner/name" of a Git remote, or "" when it is missing."""
    url = _run_quiet(["git", "remote", "get-url", remote], root)
    if url is None or url.returncode != 0:
        return ""
    return _repo_slug(url.stdout)


def _latest_release(remote: str, root: Path) -> str | None:
    """Return the newest X.Y.Z release tag on a Git remote or URL; None if unreachable or untagged."""
    tags = _run_quiet(["git", "ls-remote", "--tags", "--refs", remote], root, timeout=15)
    if tags is None or tags.returncode != 0:
        return None
    found = re.findall(r"refs/tags/v(\d+)\.(\d+)\.(\d+)$", tags.stdout, re.MULTILINE)
    releases = [tuple(int(n) for n in numbers) for numbers in found]
    return ".".join(map(str, max(releases))) if releases else None


def _newer(latest: str, installed: str) -> bool:
    """True when release latest is newer than installed; an unreadable installed version counts as older."""
    try:
        return tuple(int(n) for n in latest.split(".")) > tuple(int(n) for n in installed.split(".")[:3])
    except ValueError:
        return True


def _cli_version() -> str:
    try:
        return version(CLI_DIST)
    except PackageNotFoundError:
        return "unreleased"


def _ensure_environment(root: Path) -> None:
    """Sync the module's locked environment once, and explain a failure as setup, not as a lesson."""
    if shutil.which("uv") is None:
        raise ValueError(f"uv is not installed; see {GUIDES}install-uv-and-git.md")

    venv, lock = root / ".venv", root / "uv.lock"
    if not venv.is_dir() or (lock.is_file() and lock.stat().st_mtime > venv.stat().st_mtime):
        _console.print("Preparing the project environment; this may download packages.", style="dim")

    ready = _run_quiet([*MODULE_PYTHON, "-c", ""], root, timeout=900)
    if ready is None or ready.returncode != 0:
        raise ValueError(f"The project environment is not ready: {_last_error_line(ready)}\n"
                         "Run: uv sync --locked")


# === TEST === #

def _written_activity(course: Course, chapter: int, lesson: int) -> int:
    """Point to the Markdown file of a written lesson; nothing is graded."""
    folder = f"chapter_{chapter:02}"
    answer = course.root / "answers" / folder / f"lesson_{lesson:02}.md"
    if not answer.is_file():
        raise ValueError(f"Missing project file: {answer}")

    message = Text(f"{course.chapters[chapter][lesson]}\n\n"
                   f"Edit: {answer.relative_to(course.root)}\n"
                   "Open this Markdown file, write under each heading, and save.\n"
                   "Use the lesson's self-check to review your answer.\n"
                   "This command does not read or grade your answer.")
    _console.print(Panel(message, title="WRITTEN ACTIVITY", border_style="cyan", expand=False))
    return 0


def _lesson_page(course: Course, chapter: int, lesson: int) -> str:
    """Return the lesson's Markdown page relative to the module, or "" when it has none."""
    pages = sorted(course.root.glob(f"docs/chapters/{chapter:02}-*/{lesson:02}-*.md"))
    return pages[0].relative_to(course.root).as_posix() if pages else ""


def _file_loads(source: Path, root: Path) -> bool:
    """Compile the student's file first, so a syntax error shows only its line, not pytest internals."""
    compiled = _run_quiet([*MODULE_PYTHON, "-m", "py_compile", str(source.relative_to(root))], root)
    if compiled is None or compiled.returncode == 0:
        return True
    message = Text(compiled.stderr.strip() + "\n\nFix this line, save, and run the check again.")
    _console.print(Panel(message, title="YOUR FILE DOES NOT LOAD", border_style="red", expand=False))
    return False


def _check_lesson(course: Course, chapter: int, lesson: int, *, show_all: bool) -> bool | None:
    """Run one coding lesson's pytest file; True when it passes, None when the file does not load."""
    root = course.root
    folder = f"chapter_{chapter:02}"
    source = root / "src" / folder / f"lesson_{lesson:02}.py"
    test = root / "tests" / folder / f"test_lesson_{lesson:02}.py"
    for path in (source, test):
        if not path.is_file():
            raise ValueError(f"Missing project file: {path}")

    title = Text(f"Chapter {chapter} / Lesson {lesson} — {course.chapters[chapter][lesson]}", style="bold cyan")
    _console.rule(title, align="left")
    paths = Table.grid(padding=(0, 2))
    paths.add_column(style="dim")
    paths.add_column()
    paths.add_row("Edit", Text(str(source.relative_to(root))))
    paths.add_row("Tests", Text(str(test.relative_to(root))))
    _console.print(paths)

    if not _file_loads(source, root):
        return None

    command = [*MODULE_PYTHON, "-m", "pytest", str(test.relative_to(root)),
               "-v", "--tb=short", "--no-header", "-p", "no:cacheprovider"]
    if not show_all:
        # One failure at a time: stop at the first, and skip pytest's repeated summary.
        command += ["-x", "-rN"]
    code = subprocess.run(command, cwd=root, check=False, env=_module_env()).returncode
    if code == PYTEST_NO_TESTS:
        raise ValueError(f"No tests were found for lesson {lesson}: {test}")
    return code == 0


def _test(course: Course, chapter: int, lesson: int | None, *, show_all: bool = False) -> int:
    """Check one lesson, or every coding lesson of a chapter."""
    _console.print("Project:", str(course.root), style="dim", soft_wrap=True)
    if (chapter, lesson) in course.written:
        return _written_activity(course, chapter, lesson)

    if lesson is not None:
        lessons = [lesson]
    else:
        lessons = [n for n in course.chapters[chapter] if (chapter, n) not in course.written]
    if not lessons:
        raise ValueError(f"Chapter {chapter} has no coding lessons to check")

    _ensure_environment(course.root)
    results = {number: _check_lesson(course, chapter, number, show_all=show_all) for number in lessons}
    failed = [number for number, passed in results.items() if not passed]

    if None in results.values():
        # The load error above already says what to fix.
        return 1
    if failed:
        lines = ["Read the failed check above, edit the lesson, save, and rerun."]
        page = _lesson_page(course, chapter, failed[0])
        if page:
            lines.append(f"Lesson: {page}")
        if not show_all:
            lines.append(f"Every failing check:  academy test {chapter} {failed[0]} --all")
        _console.print(Panel(Text("\n".join(lines)), title="TRY AGAIN", border_style="yellow", expand=False))
        return 1
    _console.print(Panel("Supplied checks passed.\nAnswer the lesson's transfer question next.",
                         title="PASS", border_style="green", expand=False))
    return 0


# === HEALTH === #

# One health result: status (OK, FAIL, WARN, INFO), what was checked, and a fix or "".
Check = tuple[str, str, str]


def _check_qt(root: Path) -> Check:
    """Start a hidden Qt application with the project's PySide6."""
    probe = "from PySide6.QtWidgets import QApplication; QApplication([])"
    result = _run_quiet([*INSPECT_PYTHON, "-c", probe], root, timeout=60,
                        env=_module_env(QT_QPA_PLATFORM="offscreen"))
    if result is not None and result.returncode == 0:
        return "OK", "Qt (PySide6) starts", ""
    return "FAIL", f"Qt (PySide6) could not start: {_last_error_line(result)}", "uv sync --locked"


# Module-specific health checks named in [tool.academy] checks.
EXTRA_CHECKS: dict[str, Callable[[Path], Check]] = {"qt": _check_qt}


def _git_checks(course: Course) -> tuple[list[Check], bool]:
    """Check Git, its identity, and the fork remotes; also return whether the folder is the clone."""
    root = course.root
    if shutil.which("git") is None:
        return [("FAIL", "Git is not installed", GUIDES + "install-uv-and-git.md")], False

    results: list[Check] = []
    for key in ("user.name", "user.email"):
        value = _run_quiet(["git", "config", key], root)
        if value is not None and value.stdout.strip():
            results.append(("OK", f"Git {key} is set", ""))
        else:
            results.append(("FAIL", f"Git {key} is not set", f'git config --global {key} "..."'))

    top = _run_quiet(["git", "rev-parse", "--show-toplevel"], root)
    inside = top is not None and top.returncode == 0 and Path(top.stdout.strip()).resolve() == root
    if not inside:
        results.append(("FAIL", "This folder is not a Git clone of your fork", GUIDES + "fork-clone-setup.md"))
        return results, False

    origin, upstream = _remote_slug("origin", root), _remote_slug("upstream", root)
    if origin == course.course_repo:
        results.append(("FAIL", "origin is the course, not your fork", GUIDES + "fork-clone-setup.md"))
    elif origin:
        results.append(("OK", f"origin is your fork ({origin})", ""))
    else:
        results.append(("FAIL", "origin remote is missing", GUIDES + "fork-clone-setup.md"))

    if upstream == course.course_repo:
        results.append(("OK", f"upstream is the course ({course.course_repo})", ""))
    else:
        results.append(("FAIL", f"upstream is not {course.course_repo}",
                        f"git remote add upstream https://github.com/{course.course_repo}.git"))
    return results, True


def _environment_checks(course: Course) -> tuple[list[Check], bool]:
    """Check the locked project environment and its Python; also return whether it is ready."""
    root = course.root
    synced = _run_quiet(["uv", "sync", "--locked", "--check", "--inexact", "--quiet"], root, timeout=60)
    if synced is None:
        return [("FAIL", "uv is not installed or did not respond", GUIDES + "install-uv-and-git.md")], False
    if synced.returncode != 0:
        return [("FAIL", "Project environment is out of date", "uv sync --locked")], False

    wanted = (root / ".python-version").read_text(encoding="utf-8").strip()
    probe = "import sys; print('%d.%d' % sys.version_info[:2])"
    found = _run_quiet([*INSPECT_PYTHON, "-c", probe], root)
    running = found.stdout.strip() if found is not None and found.returncode == 0 else "unknown"
    if running == wanted or running.startswith(wanted + "."):
        python = ("OK", f"Python {running} matches .python-version", "")
    else:
        python = ("FAIL", f"Python {running} does not match .python-version ({wanted})", "uv sync --locked")
    return [("OK", "Project environment is up to date", ""), python], True


def _version_checks(course: Course, *, can_reach_upstream: bool) -> list[Check]:
    """Compare the course and the academy command with their newest releases."""
    installed = course.version
    latest = _latest_release("upstream", course.root) if can_reach_upstream else None
    if latest is None:
        course_check = ("WARN", f"Course version {installed}; could not reach upstream to compare", "")
    elif _newer(latest, installed):
        course_check = ("WARN", f"Course version {installed}; {latest} is available", "academy update")
    else:
        course_check = ("OK", f"Course version {installed} is the latest", "")

    cli_installed, cli_latest = _cli_version(), _latest_release(CLI_URL, course.root)
    if cli_latest is None:
        cli_check = ("WARN", f"academy {cli_installed}; could not reach GitHub to compare", "")
    elif _newer(cli_latest, cli_installed):
        cli_check = ("WARN", f"academy {cli_installed}; {cli_latest} is available", "academy update")
    else:
        cli_check = ("OK", f"academy {cli_installed} is the latest", "")
    return [course_check, cli_check]


def _print_health(results: list[Check]) -> None:
    """Print one coloured line per check, its fix below it, and a closing verdict."""
    for status, name, fix in results:
        _console.print(Text.assemble((f"{status:<5}", STATUS_STYLES[status]), " ", name), soft_wrap=True)
        if fix:
            _console.print(Text.assemble("      fix: ", (fix, "cyan")), soft_wrap=True)

    failures = sum(status == "FAIL" for status, _, _ in results)
    if failures:
        verdict = Panel(f"Setup needs attention: {failures} to fix.\nRun each fix above, then academy health again.",
                        border_style="red", expand=False)
    else:
        verdict = Panel("Setup looks good.", border_style="green", expand=False)
    _console.print()
    _console.print(verdict)


def _health(course: Course) -> int:
    """Check this computer's course setup; print one line per check with a fix."""
    git_results, inside = _git_checks(course)
    environment_results, ready = _environment_checks(course)
    reaches_upstream = inside and _remote_slug("upstream", course.root) == course.course_repo
    results = [*git_results, *environment_results,
               *_version_checks(course, can_reach_upstream=reaches_upstream)]

    if inside:
        status = _run_quiet(["git", "status", "--porcelain"], course.root)
        if status is not None and status.stdout.strip():
            results.append(("INFO", "You have uncommitted changes; commit before getting updates", ""))
    for name in course.checks:
        if ready:
            results.append(EXTRA_CHECKS[name](course.root))
        else:
            results.append(("INFO", f"The {name} check runs once the project environment is up to date", ""))
    for tool, label in (("gh", "GitHub CLI (gh)"), ("code", "VS Code 'code' command")):
        if shutil.which(tool) is None:
            results.append(("INFO", f"Optional: {label} not found", ""))

    _print_health(results)
    return 1 if any(status == "FAIL" for status, _, _ in results) else 0


# === UPDATE === #

def _upgrade_tool() -> None:
    """Upgrade this uv tool, or print the command where it cannot replace itself."""
    command = ["uv", "tool", "upgrade", CLI_DIST]
    # ponytail: Windows locks the running academy.exe, so the student runs the upgrade there.
    if WINDOWS:
        _console.print(f"\nClose this command, then run:  {' '.join(command)}", soft_wrap=True)
        return

    try:
        result = subprocess.run(command, check=False)
    except OSError:
        result = None
    if result is None or result.returncode != 0:
        _console.print(f"\nCould not upgrade academy. Run:  {' '.join(command)}\n"
                       f"If academy was not installed with uv tool, see {GUIDES}install-uv-and-git.md",
                       soft_wrap=True)
        return
    _console.print("\nacademy is upgraded; the next command uses it.")


def _pull_course(course: Course, latest: str) -> int:
    """Merge the newer course release from upstream into this repository, then sync it.

    It never commits, discards, or pushes the student's work: uncommitted changes stop it
    before the pull, and a merge conflict is left for the student to resolve.
    """
    root = course.root
    guide = f"Guide: {GUIDES}course-updates.md"
    status = _run_quiet(["git", "status", "--porcelain", "--untracked-files=no"], root)
    if status is None or status.stdout.strip():
        message = Text("You have uncommitted changes. Commit your work, then run academy update again:\n\n"
                       'git add -A\ngit commit -m "Save my work before the course update"\n\n' + guide)
        _console.print(Panel(message, title="COMMIT FIRST", border_style="yellow", expand=False))
        return 1

    _console.print(f"\nGetting course {latest} from upstream…")
    pull = ["git", "pull", "--no-rebase", "--no-edit", "upstream", "main"]
    if subprocess.run(pull, cwd=root, check=False, env=_module_env()).returncode != 0:
        unmerged = _run_quiet(["git", "diff", "--name-only", "--diff-filter=U"], root)
        files = unmerged.stdout.split() if unmerged is not None else []
        if files:
            message = ("You and the course changed the same lines. Nothing is lost.\n"
                       "Open each file, keep your work and the course's change, and save:\n\n"
                       + "\n".join(files)
                       + "\n\nThen run:\n\ngit add -A\ngit commit --no-edit\nuv sync --locked\ngit push")
        else:
            message = "The course update did not finish; read the Git message above."
        _console.print(Panel(Text(message + "\n\n" + guide), title="COURSE UPDATE STOPPED",
                             border_style="yellow", expand=False))
        return 1

    sync = ["uv", "sync", "--locked"]
    if subprocess.run(sync, cwd=root, check=False, env=_module_env()).returncode != 0:
        _console.print("\nThe course is merged, but the project environment did not update. "
                       "Run:  uv sync --locked", soft_wrap=True)
        return 1
    _console.print(Panel(f"Course updated to {latest}. Save it to your fork:  git push",
                         title="COURSE UPDATED", border_style="green", expand=False))
    return 0


def _state(latest: str | None, behind: bool) -> str:
    if latest is None:
        return "could not check"
    return f"{latest} is available" if behind else "latest"


def _update(course: Course | None, *, check_only: bool) -> int:
    """Upgrade the academy tool and, inside a module, merge a newer course release.

    The tool updates itself because it is installed once, outside every module (DEC-0044),
    so this works in any folder. Inside a module it also pulls the course update into the
    student's repository; see _pull_course for what it never does.
    """
    where = course.root if course is not None else Path.cwd()
    cli_installed, cli_latest = _cli_version(), _latest_release(CLI_URL, where)
    cli_behind = cli_latest is not None and _newer(cli_latest, cli_installed)
    _console.print(f"{'academy':<8} {cli_installed} ({_state(cli_latest, cli_behind)})", soft_wrap=True)

    course_latest, course_behind, note = None, False, ""
    if course is None:
        note = "Run academy update inside a module folder to check for course updates too."
    elif _remote_slug("upstream", course.root) != course.course_repo:
        note = f"upstream is not {course.course_repo}; run academy health to check course updates."
    else:
        course_latest = _latest_release("upstream", course.root)
        course_behind = course_latest is not None and _newer(course_latest, course.version)
        _console.print(f"{'Course':<8} {course.version} ({_state(course_latest, course_behind)})",
                       soft_wrap=True)

    if cli_behind:
        if check_only:
            _console.print(f"\nRun academy update to install academy {cli_latest}.")
        else:
            _upgrade_tool()

    code = 0
    if course_behind:
        if check_only:
            _console.print(f"\nRun academy update to get course {course_latest}.")
        else:
            code = _pull_course(course, course_latest)

    if note:
        _console.print("\n" + note, soft_wrap=True)
    elif not (course_behind or cli_behind):
        if course_latest is None or cli_latest is None:
            _console.print("\nCould not reach GitHub to compare; check your connection and try again.")
        else:
            _console.print("\nEverything is up to date.")
    return code


# === DOCS === #

def _port_is_free(port: int) -> bool:
    with socket.socket() as probe:
        try:
            probe.bind(("localhost", port))
        except OSError:
            return False
    return True


def _docs_port() -> int:
    """Return DOCS_PORT, or the next free port when a preview (or anything else) already uses it."""
    if _port_is_free(DOCS_PORT):
        return DOCS_PORT

    _console.print(f"Port {DOCS_PORT} is in use: a course preview may already be running at "
                   f"http://localhost:{DOCS_PORT}\nOpen it there, or stop it with Ctrl+C in its terminal.",
                   soft_wrap=True)
    last = DOCS_PORT + 20
    for port in range(DOCS_PORT + 1, last):
        if _port_is_free(port):
            _console.print(f"Starting this preview at http://localhost:{port} instead.\n", soft_wrap=True)
            return port
    raise ValueError(f"Ports {DOCS_PORT}-{last - 1} are all in use. "
                     "Stop another course preview with Ctrl+C in its terminal, then try again.")


def _docs(course: Course, *, open_browser: bool) -> int:
    """Prepare the optional docs dependencies and serve from the module root."""
    config = course.root / "mkdocs.yml"
    if not config.is_file():
        raise ValueError(f"Missing project file: {config}")

    where = ("Your browser will open when the preview is ready.\n" if open_browser
             else "Open the local address printed below in your browser.\n")
    message = ("Preparing your course preview; the first run may download its tools.\n" + where
               + "Keep this terminal open. Use a second terminal for lesson commands.\n"
               "Press Ctrl+C here to stop the preview.")
    _console.print(Panel(message, title="COURSE DOCS", border_style="cyan", expand=False))

    port = _docs_port()
    command = ["uv", "run", "--locked", "--group", "docs", "zensical", "serve", "-a", f"localhost:{port}"]
    if open_browser:
        command.append("--open")
    try:
        return subprocess.run(command, cwd=course.root, check=False, env=_module_env()).returncode
    except KeyboardInterrupt:
        _console.print("\nCourse preview stopped.", style="dim")
        return 0


# === CLI === #

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read your M74 Academy course and check lesson exercises",
                                     epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"academy {_cli_version()}")
    commands = parser.add_subparsers(dest="command")

    docs = commands.add_parser("docs", help="open the course preview in your browser")
    docs.add_argument("--no-open", action="store_true", help="show the address without opening a browser")
    commands.add_parser("health", help="check that your computer is set up for the course")
    update = commands.add_parser("update", help="upgrade academy and get a newer course release")
    update.add_argument("--check", action="store_true", help="only report available updates; change nothing")
    test = commands.add_parser("test", help="check a chapter or one lesson")
    test.add_argument("chapter", type=int)
    test.add_argument("lesson", type=int, nargs="?")
    test.add_argument("--all", action="store_true", dest="show_all",
                      help="show every failing check, not only the first")
    return parser


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Dispatch one parsed command."""
    if args.command == "update":
        # Outside a module the tool still updates itself; a broken module still reports why.
        try:
            root = find_root()
        except ValueError:
            return _update(None, check_only=args.check)
        return _update(load_course(root), check_only=args.check)

    course = load_course(find_root())
    if args.command == "docs":
        return _docs(course, open_browser=not args.no_open)
    if args.command == "health":
        return _health(course)

    if args.chapter not in course.chapters:
        parser.error(f"chapter must be one of {sorted(course.chapters)}")
    if args.lesson is not None and args.lesson not in course.chapters[args.chapter]:
        parser.error(f"chapter {args.chapter} has lessons 1-{max(course.chapters[args.chapter])}")
    return _test(course, args.chapter, args.lesson, show_all=args.show_all)


def main() -> int:
    """Expose lesson checks as the installed academy command."""
    parser = _parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return _run(args, parser)
    except (OSError, ValueError) as error:
        _errors.print("PROJECT ERROR", style="bold red")
        _errors.print(str(error), soft_wrap=True)
        parser.exit(2)


if __name__ == "__main__":
    raise SystemExit(main())
