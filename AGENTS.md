# Repository guidance

Captain Share is a public, standalone sharing manager. Keep private assets,
customer data, source bindings and installation records out of Git. The bundled
management skill and fictional fixtures are public product code.

## Code map

- `captain_share/config.py`: layered `.captain` settings, paths and locks.
- `captain_share/engine.py`: preview/apply, ownership, links/copies and archives.
- `captain_share/cli.py`, `server.py`, `web/`: interfaces to the same engine.
- `captain_share/integrations.py`: Captain connection import and explicit Kiro handoff.
- `captain_share/repositories.py`, `imports.py`, `personal.py`, `disconnect.py`: library Git
  operations, sharing and disconnecting;
  staging zips/folders from outside and adding their assets to a library.
- `captain_share/skills/manage-shared-assets/`: reusable management instructions.
- `scripts/verify.py`: the local and CI verification entrypoint.

## Commands

`make bootstrap` sets up development tools. `make verify-fast` runs cheap checks;
`make verify` adds tests and an installed-package smoke test. `make format` formats
Python/web files; `make lint-fix` fixes imports and supported lint issues. Use
`make test` for the disposable regression suite.

## Changes and evidence

Read [ADR 001](docs/adr/001-sharing-engine.md) before changing module boundaries and
[ADR 002](docs/adr/002-agent-tool-destinations.md) before changing tool destinations, and
[ADR 003](docs/adr/003-kiro-powers.md) before changing Kiro power handling, and
[ADR 004](docs/adr/004-web-places.md) before changing which folders a web window may change.
Preserve preview freshness, ownership checks, path containment, rollback on handled
failure, and recoverable archives. Existing hooks and other installers' ledgers
remain owned by their original tools.

Test mutations only in disposable folders/repos. Do not call a real model to prove
a mechanical operation. After behavioral changes run `make verify`; after UI
changes also exercise the changed flow in a browser. Report commands, exit codes,
observed behavior and remaining gaps. Installation checks do not establish skill
activation, confidentiality, or faster agent performance.
