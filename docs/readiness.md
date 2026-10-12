# Repository readiness

Applied the relevant practices from [ai-ready-repo](https://github.com/vidanov/ai-ready-repo):
clear setup, executable constraints, one local/CI verification entrypoint, real
failure checks, and explicit evidence limits. Its domain example and research
framework are not dependencies of this tool.

| Constraint | Executable evidence |
| --- | --- |
| CLI and web use the sharing engine | Static import-boundary checker with permitted and planted-violation cases |
| Preview must be current | Source-change and config-change regressions reject stale previews |
| Existing files retain ownership | Unmanaged collision and independent-publication-edit tests |
| Removal must be recoverable | Local-directory and managed-alias archive/restore round trips |
| Failed writes preserve originals | Simulated outbound-copy failure exercises rollback |
| Installation stays scoped | Default-current-folder, explicit target and Git-worktree regressions |
| Web access stays local | Token, origin and target-bound plan tests |
| Distribution must work | Build/install the wheel in a temporary folder and exercise its CLI/resources |
| Checks must report failures | Verification-runner tests preserve nonzero results and missing-tool diagnostics |
| One original serves several tools | Two-tool link, tool-disable archive, cross-tool removal and default-unchanged tests |
| Conflicts stay resolvable | Structured conflict, path-scoped archive and "this folder's original" tests |
| Kiro owns installed powers | Fake `kiro-cli` copy/update/uninstall, stale-preview and missing-CLI tests; manifest/folder name checks |

`make verify-fast` runs inexpensive formatting, lint, type checking, boundary/link,
JavaScript syntax and web-formatting checks. `make verify` adds the existing unittest
suite and an installed-wheel smoke test. CI invokes `make verify`; it does not keep
an independently drifting command list.

Python checking includes untyped function bodies; the JSON-heavy configuration and
plan structures are not a fully statically typed schema. Browser interaction is
also checked manually for selection, aliasing, filtering, preview/apply, tool
toggles, conflict fixes, power install/update/uninstall and archive/restore;
JavaScript syntax/API tests alone do not prove the rendered UI works.
Hosted CI results and branch protection require a remote repository. No agent speed,
context-saving, confidentiality or universal failure-detection claim follows from
these checks.
