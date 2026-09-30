# m74-academy-cli

The `academy` command used in M74 Academy course modules. It runs a module's
lesson checks, opens its course preview, and checks the student's setup.
Course material itself is private; this tool is MIT-licensed.

## Commands

Run these inside a module folder:

```console
uv run academy test 1 2     # check chapter 1, lesson 2
uv run academy test 1       # check every coding lesson in chapter 1
uv run academy docs         # open the course preview
uv run academy health       # check Git, remotes, Python, uv, and module extras
```

## Module configuration

A module declares itself in `[tool.academy]` of its `pyproject.toml`:

```toml
[tool.academy]
course-repo = "m74-academy/module-2"
written = ["1.1", "3.9"]    # lessons answered in answers/, not by code
checks = ["qt"]             # extra health checks

[tool.academy.chapters]
1 = ["Why Tools Need Context", "Environment and Path Templates"]
```

Lesson `N.M` expects `src/chapter_0N/lesson_0M.py` and
`tests/chapter_0N/test_lesson_0M.py`; written lessons expect
`answers/chapter_0N/lesson_0M.md`. Available extra checks: `qt` (PySide6 starts).
