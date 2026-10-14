# ADR 001: One sharing engine for every interface

Status: Accepted

## Context

Sharing modifies multiple independent folders. Duplicating link/copy/archive logic
in the web interface and CLI would allow them to diverge on ownership checks,
previews and recovery. Model-driven assistance must not become another installer.

## Decision

`config.py` owns settings and low-level path/lock helpers. `engine.py` owns plans,
conflicts and filesystem transactions, and may import only `config` from this
package. `integrations.py` may use those two modules for a Kiro handoff or Captain
connection import. CLI and web are callers of this engine. `repositories.py` uses config and engine
for explicit Git operations and status; it does not depend on either interface.
`imports.py` stages zips and folders from outside (ADR 006) and uses config, engine and
repositories; it does not depend on either interface either. `personal.py` creates the
personal library and promotes assets from it (ADR 008) with the same three modules.

`scripts/check_repository.py` enforces these static Python import directions and
runs in full and fast verification. Its tests permit the real graph, plant a
forbidden dependency, and distinguish a parse failure from an import violation.
Dynamic imports are outside this static check's scope.

## Consequences

UI and CLI share the same behavior and tests. Another interface can call the engine
without depending on either existing interface. Schema/runtime installation tests
remain separate from actual agent skill activation. This boundary does not make
filesystem operations crash-atomic or provide isolation from a hostile local user.
