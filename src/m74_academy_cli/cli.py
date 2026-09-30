"""The academy command: lesson checks, course preview, and setup health for M74 Academy modules.

Installed once as a uv tool (DEC-0044), it runs in its own environment and runs each
module's checks in that module's environment through `uv run`.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
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
    older = None
    for folder in (here, *here.parents):
        project = _read_project(folder)
        if project is None:
            continue
        if "academy" in project.get("tool", {}):
            return folder
        if older is None and str(project.get("project", {}).get("name", "")).startswith("m74-academy-module-"):
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


def _cli_version() -> str:
    try:
        return version("m74-academy-cli")
    except PackageNotFoundError:
        return "unreleased"


GUIDES = "https://github.com/m74-academy/community/blob/main/guides/"
CLI_REPO = "m74-academy/academy-cli"
CLI_DIST = "m74-academy-cli"
CLI_URL = f"https://github.com/{CLI_REPO}.git"
WINDOWS = os.name == "nt"
# The module's own locked environment, not this tool's.
MODULE_PYTHON = ["uv", "run", "--locked", "python"]
# Health only inspects the environment; `uv sync --check` reports drift separately.
INSPECT_PYTHON = ["uv", "run", "--no-sync", "python"]


def _module_env(**extra: str) -> dict[str, str]:
    """Environment for commands in the module: drop any other activated venv so uv uses .venv."""
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    return {**env, **extra}


def _ensure_environment(root: Path) -> None:
    """Sync the module's locked environment once, and explain a failure as setup, not as a lesson."""
    if shutil.which("uv") is None:
        raise ValueError(f"uv is not installed; see {GUIDES}install-uv-and-git.md")
    venv, lock = root / ".venv", root / "uv.lock"
    if not venv.is_dir() or (lock.is_file() and lock.stat().st_mtime > venv.stat().st_mtime):
        _console.print("Preparing the project environment; this may download packages.", style="dim")
    ready = _run_quiet([*MODULE_PYTHON, "-c", ""], root, timeout=900)
    if ready is None or ready.returncode != 0:
        detail = (ready.stderr.strip().splitlines() or [""])[-1] if ready is not None else "timed out"
        raise ValueError(f"The project environment is not ready: {detail}\nRun: uv sync --locked")


def _test(course: Course, chapter: int, lesson: int | None) -> int:
    """Run the matching pytest files in the module's environment."""
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
    if not numbers:
        raise ValueError(f"Chapter {chapter} has no coding lessons to check")
    _ensure_environment(ROOT)
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

        command = [*MODULE_PYTHON, "-m", "pytest", str(test.relative_to(ROOT)),
                   "-v", "--tb=short", "--no-header", "-p", "no:cacheprovider"]
        code = subprocess.run(command, cwd=ROOT, check=False, env=_module_env()).returncode
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
                              timeout=timeout, check=False, env=env or _module_env())
    except (OSError, subprocess.TimeoutExpired):
        return None


def _repo_slug(url: str) -> str:
    """Return "owner/name" from a GitHub HTTPS or SSH remote URL."""
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url.strip())
    return match.group(1).lower() if match else ""


def _latest_release(remote: str, root: Path) -> str | None:
    """Return the newest X.Y.Z release tag on a Git remote or URL; None if unreachable or untagged."""
    tags = _run_quiet(["git", "ls-remote", "--tags", "--refs", remote], root, timeout=15)
    if tags is None or tags.returncode != 0:
        return None
    releases = [tuple(int(n) for n in found)
                for found in re.findall(r"refs/tags/v(\d+)\.(\d+)\.(\d+)$", tags.stdout, re.MULTILINE)]
    return ".".join(map(str, max(releases))) if releases else None


def _newer(latest: str, installed: str) -> bool:
    """True when release latest is newer than installed; an unreadable installed version counts as older."""
    try:
        return tuple(int(n) for n in latest.split(".")) > tuple(int(n) for n in installed.split(".")[:3])
    except ValueError:
        return True


def _check_qt(root: Path) -> tuple[str, str, str]:
    """Start a hidden Qt application with the project's PySide6."""
    probe = "from PySide6.QtWidgets import QApplication; QApplication([])"
    result = _run_quiet([*INSPECT_PYTHON, "-c", probe], root, timeout=60,
                        env=_module_env(QT_QPA_PLATFORM="offscreen"))
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

    synced = _run_quiet(["uv", "sync", "--locked", "--check", "--inexact", "--quiet"], ROOT, timeout=60)
    if synced is None:
        check("FAIL", "uv is not installed or did not respond", GUIDES + "install-uv-and-git.md")
    elif synced.returncode == 0:
        check("OK", "Project environment is up to date")
    else:
        check("FAIL", "Project environment is out of date", "uv sync --locked")

    ready = synced is not None and synced.returncode == 0
    if ready:
        wanted = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
        found = _run_quiet([*INSPECT_PYTHON, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                           ROOT)
        running = found.stdout.strip() if found is not None and found.returncode == 0 else "unknown"
        if running == wanted or running.startswith(wanted + "."):
            check("OK", f"Python {running} matches .python-version")
        else:
            check("FAIL", f"Python {running} does not match .python-version ({wanted})", "uv sync --locked")

    installed = course.version
    latest = _latest_release("upstream", ROOT) if remotes.get("upstream") == COURSE_REPO else None
    if latest is None:
        check("WARN", f"Course version {installed}; could not reach upstream to compare")
    elif _newer(latest, installed):
        check("WARN", f"Course version {installed}; {latest} is available", GUIDES + "course-updates.md")
    else:
        check("OK", f"Course version {installed} is the latest")

    cli_installed, cli_latest = _cli_version(), _latest_release(CLI_URL, ROOT)
    if cli_latest is None:
        check("WARN", f"academy {cli_installed}; could not reach GitHub to compare")
    elif _newer(cli_latest, cli_installed):
        check("WARN", f"academy {cli_installed}; {cli_latest} is available", "academy update")
    else:
        check("OK", f"academy {cli_installed} is the latest")

    if inside:
        status = _run_quiet(["git", "status", "--porcelain"], ROOT)
        if status is not None and status.stdout.strip():
            check("INFO", "You have uncommitted changes; commit before getting updates")
    for name in course.checks:
        if ready:
            check(*EXTRA_CHECKS[name](ROOT))
        else:
            check("INFO", f"The {name} check runs once the project environment is up to date")
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
    else:
        _console.print("\nacademy is upgraded; the next command uses it.")


def _state(latest: str | None, behind: bool) -> str:
    return "could not check" if latest is None else f"{latest} is available" if behind else "latest"


def _update(course: Course | None, *, check_only: bool) -> int:
    """Upgrade the academy tool and show how to take a newer course release.

    The tool updates itself because it is installed once, outside every module (DEC-0044),
    so this works in any folder. The course update pulls into the student's own repository,
    so inside a module it stays a printed step.
    """
    where = course.root if course is not None else Path.cwd()
    cli_installed, cli_latest = _cli_version(), _latest_release(CLI_URL, where)
    cli_behind = cli_latest is not None and _newer(cli_latest, cli_installed)
    _console.print(f"{'academy':<8} {cli_installed} ({_state(cli_latest, cli_behind)})", soft_wrap=True)

    course_latest, course_behind, note = None, False, ""
    if course is None:
        note = "Run academy update inside a module folder to check for course updates too."
    else:
        url = _run_quiet(["git", "remote", "get-url", "upstream"], course.root)
        if url is None or url.returncode != 0 or _repo_slug(url.stdout) != course.course_repo:
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
    if course_behind:
        steps = []
        status = _run_quiet(["git", "status", "--porcelain"], course.root)
        if status is None or status.stdout.strip():
            steps += ["git add -A", 'git commit -m "Save my work before the course update"']
        steps += ["git pull --no-rebase --no-edit upstream main", "uv sync --locked"]
        _console.print(Panel(
            Text("Run these in this folder:\n\n" + "\n".join(steps)
                 + f"\n\nGuide: {GUIDES}course-updates.md"),
            title="COURSE UPDATE", border_style="yellow", expand=False,
        ))
    if note:
        _console.print("\n" + note, soft_wrap=True)
    elif not (course_behind or cli_behind):
        if course_latest is None or cli_latest is None:
            _console.print("\nCould not reach GitHub to compare; check your connection and try again.")
        else:
            _console.print("\nEverything is up to date.")
    return 0


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
        return subprocess.run(command, cwd=ROOT, check=False, env=_module_env()).returncode
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
    update = commands.add_parser("update", help="upgrade academy and show how to get a newer course release")
    update.add_argument("--check", action="store_true", help="only report available updates")
    test = commands.add_parser("test", help="check a chapter or one lesson")
    test.add_argument("chapter", type=int)
    test.add_argument("lesson", type=int, nargs="?")
    args = parser.parse_args()
    try:
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
        return _test(course, args.chapter, args.lesson)
    except (OSError, ValueError) as error:
        _errors.print("PROJECT ERROR", style="bold red")
        _errors.print(str(error), soft_wrap=True)
        parser.exit(2)


if __name__ == "__main__":
    raise SystemExit(main())
