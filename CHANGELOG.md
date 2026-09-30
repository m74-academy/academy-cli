# Changelog

What changed in each release of `m74-academy-cli`. Upgrade with `academy update`.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
