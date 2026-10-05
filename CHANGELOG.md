# Changelog

What changed in each release of `m74-academy-cli`. Upgrade with `academy update`.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.4.2] — 2026-10-05

### Changed

- Setup links in `academy health`, `academy update`, and error messages point to the public Module 0 setup
  guides, the single source for every module. The community repository's copies are retired.
- `academy update` no longer stops when the course changed a lesson you already solved. Files the module
  marks `merge=ours` in `.gitattributes` keep your version, and the update lists them with the next step
  (DEC-0053). It fetches the release first and applies the release's own `.gitattributes`, so the update that
  first adds the file already keeps your code, and it lists only files it really left unchanged.
- `academy update` messages no longer link to a course-updates guide; each one says what to do. An older course
  release that carries its own command prints the update commands.
- A passing `academy test` says only `Checks passed.`; it no longer tells you to answer Think questions,
  which most lessons don't have.

## [0.4.1] — 2026-10-05

### Changed

- A lesson listed in `written` without an answer file is a reading lesson: `academy test N M` says there is
  nothing to check instead of reporting a missing project file.

## [0.4.0] — 2026-10-04

### Added

- A module can list Gold lessons in `[tool.academy]` with `gold = ["1.3", ...]` (DEC-0052).
  `academy test CHAPTER` then checks only the Core lessons and names the Gold ones it skipped.
- `academy test --gold` also checks the Gold lessons, and runs each lesson's Gold section checks,
  `tests/chapter_0N/test_lesson_0M_gold.py`, together with its Core checks.

## [0.3.2] — 2026-10-03

### Added

- A module can set `guides` in `[tool.academy]` to the base URL of setup guides its students can open.
  `academy health` fixes and the `academy update` guide link then point there, so a public module
  never links to the private course-wide guides.

## [0.3.1] — 2026-09-30

### Fixed

- On Windows, `academy update` prints the `uv tool upgrade` step last, after the course
  update, instead of "Close this command" before it.
- "Could not reach GitHub" now says to check the connection and Git's GitHub sign-in
  (`gh auth status`, `gh auth setup-git`); `academy health` gives the same fix on its WARN lines.
- The PASS box says "Think questions", as the lessons do.
- `academy health` names a non-GitHub `origin` and a missing `upstream` correctly, fixes a wrong
  `upstream` with `git remote set-url`, and links the fork guide's remote repair steps.

### Added

- `academy test CHAPTER` says how many of the chapter's lessons pass.

## [0.3.0] — 2026-09-30

### Added

- This changelog.

- `academy` alone prints the help, and the help ends with a typical session.

### Changed

- `academy health` colours each status and closes with a verdict panel; the words
  `OK`, `FAIL`, `WARN`, `INFO`, and "Setup looks good." are unchanged.
- `academy test` stops at the first failing check and ends with the lesson page and the
  `--all` command; `--all` shows every failing check as before. A file that does not
  load shows only its syntax error line, not pytest's internals.
- `academy docs` starts on the next free port when 8000 is in use, and says a course
  preview may already be running there, instead of failing with "Address already in use".
- `academy update` inside a module now gets a newer course release itself: it
  pulls from `upstream` and syncs the project. Uncommitted changes stop it first,
  and a merge conflict lists the files to fix. `--check` still only reports.

## [0.2.1] — 2026-09-30

### Fixed

- In a course release from before 0.2.0 (Module 1 up to 0.7.5, Module 2 0.1.0),
  `academy` names that release and points to the course-updates guide instead of
  saying there is no course here.
- Tests no longer break `pathlib` on Windows with Python 3.11.

## [0.2.0] — 2026-09-30

### Added

- Install once per computer with `uv tool install git+https://github.com/m74-academy/academy-cli`;
  one installation serves every module. Modules no longer depend on the package.
- `academy update` upgrades the command and prints the steps for a newer course
  release; `academy update --check` only reports.
- `academy health` reports the `academy` version.

### Changed

- Lesson checks, the Python check, and the Qt probe run in the module's own
  environment through `uv run --locked`.
- The first lesson check after a lockfile change says it is preparing the environment.

### Fixed

- `academy update` reports a broken `[tool.academy]` instead of treating the folder
  as outside a module.

## [0.1.0] — 2026-09-30

### Added

- First release: `academy test`, `academy docs`, and `academy health`, configured
  by `[tool.academy]` in each module's `pyproject.toml`.

[Unreleased]: https://github.com/m74-academy/academy-cli/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/m74-academy/academy-cli/tree/v0.2.1
[0.2.0]: https://github.com/m74-academy/academy-cli/tree/v0.2.0
[0.1.0]: https://github.com/m74-academy/academy-cli/tree/v0.1.0
