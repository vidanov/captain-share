# Contributing

Captain Share is a Python standard-library application with a small vanilla web
interface. Python 3.9+ and Git run it; development additionally needs Node 22+.

```bash
make bootstrap                 # isolated Python tools and locked frontend formatter
make verify-fast               # formatting, lint, types, boundaries, web syntax
make test                      # disposable filesystem, Git, HTTP and CLI regressions
make verify                    # all of the above plus building/installing a wheel
```

The full verification command is the same one CI calls. Without Make, run
`.venv/bin/python scripts/verify.py` after setup. Missing tools fail explicitly;
a blocked loopback test is not a successful verification.

`make format` applies Python/web formatting; `make lint-fix` fixes supported lint
issues. Run both when the formatter alone leaves import-order findings. Development
tool versions are in `requirements-dev.txt` and `package-lock.json`; the installed
application has no third-party runtime dependency.

Use temporary repositories in tests. For conflict and guard changes, include a
legal case and a violation rejected for the intended reason. Read
[the engine boundary decision](docs/adr/001-sharing-engine.md) before moving file
operations between modules. Keep UI messages understandable without knowing the
Python implementation.

A contribution should explain the observable behavior, what was tested, and any
remaining limitation. Do not commit personal source bindings, private skill content,
archives, screenshots of real customer projects, or generated build output. Public
hosting/required-review settings are configured separately from files in this repo.
