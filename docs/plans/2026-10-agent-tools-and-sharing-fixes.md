# Plan: Claude destinations, sharing UI fixes, library consolidation

Prepared 2026-10-12. Decision record: [ADR 002](../adr/002-agent-tool-destinations.md).

## Evidence: end-to-end findings

Disposable library (`ip/` layout, all seven kinds, one invalid skill) and Git project
(unmanaged local skill and steering). `CAPTAIN_SHARE_HOME` pointed at a scratch folder.
Baseline `make verify`: exit 0, 50 tests.

CLI: `source add`, `list`, `scan`, `select`/`deselect` (with alias), `plan`,
`sync --apply`, outbound copy, outbound re-edit (archive + copy), deselect → archive,
`guard`, `remove`/`restore` all behaved as documented.

Web UI at 1280×1000, Chromium:

| # | Finding | Effect |
| --- | --- | --- |
| A | Save, Preview, Apply and all status/error messages sit at the ends of a long page. An alias error rendered 1,511 px above the viewport; success after Apply too. | Checking an asset shows no reachable action; clicks appear to do nothing. |
| B | Install full pack selects assets whose destination holds an unmanaged local file, saves them, then the whole preview is blocked. | Nothing installs; the blocked selections stay saved, so CLI `sync` is blocked too. |
| C | The conflict says "archive it explicitly", but the UI lists only managed links under Installed. | Dead end in the UI; only CLI `remove` resolves it. |
| D | A project can receive and contribute the same source/kind/name. | Contradictory configuration; reported only as a generic unmanaged-file conflict. |
| E | Pasting the tokenized URL into an open tab changes only the fragment. | The page stays "not connected" until reloaded. |
| F | Receive rows do not show whether an asset is installed or blocked. | Users cannot predict what Preview will do. |
| G | Preview changes saves the selection first; Dismiss does not undo that. | Surprising persistence; label does not say so. |

## Status (2026-10-12)

Phases 1 and 2 are implemented on branch `claude/repo-summary-073b0c`. `make verify`
exits 0 (60 tests, wheel smoke test). Browser re-test against fresh disposable
fixtures confirmed A–G fixed, plus two pre-existing layout faults found on the way:
the tab bar widened the page at phone width, and long error paths overflowed rows.
Phase 3 is not started.

Kiro powers ([ADR 003](../adr/003-kiro-powers.md)) were added after the first two
phases: a `powers` library kind and previewed `kiro-cli powers install|uninstall`,
verified with the real `kiro-cli` 2.29.1 against a disposable `HOME`.

## Phase 1: agent tool destinations (ADR 002)

1. `config.DESTINATIONS` kind → tool → folder; `tools` validated in portable layers,
   resolved deepest-wins, default `["kiro", "copilot"]`.
2. Engine: inbound plan creates one link per enabled tool; removal expands managed
   selections to all their destinations; restore/state validation accept every tool
   folder; `scan` discovers `.claude/skills`.
3. CLI `tools [TOOL ...] [--scope repo|global]` shows or sets tools; `remove` keeps
   its signature. UI shows/sets tools for the target.
4. Docs: README, configuration, About table, bundled skill.
5. Tests: two-tool install, disabling a tool archives its links, removal across tools,
   no-tool-for-kind conflict, `.claude/skills` contribution, default unchanged.

## Phase 2: sharing correctness and UI fixes

1. Catalog rows report per-destination state (`installed`, `blocked`, `contributed`).
   Full pack selects only rows that can install and reports how many were skipped. (B, F)
2. Plan returns structured `conflicts` beside the existing `errors` strings: path,
   selection key, reason. Inbound+outbound on one key is a distinct conflict. (D)
3. Installed view lists unmanaged local assets with an Archive action that previews
   removal of that exact path. Conflicts offer "Archive local copy" and "Deselect". (C)
4. Sticky action bar with selection count, unsaved-change marker, Save, and
   "Save and preview"; status messages in a fixed live region; review scrolled to
   its start. (A, G)
5. Reconnect on `hashchange` when the fragment carries a token. (E)
6. Browser re-test of every row in the findings table.

## Phase 3: library consolidation (separate repositories, not this branch)

Private libraries keep their own inventories and migration notes; none are
recorded in this public repository.

1. A library with its own legacy installer: commit or tag its pending work, then
   unlink each live installation with that installer, and only then retire it
   ([migration](../migration.md#retire-a-legacy-toolkit-installation)). Keep
   `ip/`, profiles, and asset docs; point install docs at captain-share.
2. A plain asset library: review pending changes for personal data, remove
   generated folders from `ip/`, point install docs at captain-share, and connect
   it as another source.
3. Measure in a disposable repository: link counts and `guard` before/after.

## Later

Generated (non-link) copies for steering → Claude rules, prompts → commands, and
Kiro JSON → Claude Markdown agents, under their own ADR. Claude handoff for `assist`.

## Gaps that remain after this plan

Installed links do not prove a tool loads a skill. Symlink handling by each tool's
discovery is assumed, not measured here. Windows remains unsupported.
