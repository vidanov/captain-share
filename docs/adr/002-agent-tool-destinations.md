# ADR 002: One original, several agent tool destinations

Status: Accepted

## Context

Asset kinds were bound to exactly one destination (`skills` → `.kiro/skills`).
Claude Code reads the same skill format (a folder with `SKILL.md` and `name`/
`description` frontmatter) from `.claude/skills/`. Teams using both Kiro and Claude
would otherwise keep two library folders or two copies, which then drift.

Two-way synchronization between `.kiro/` and `.claude/` was rejected. It makes both
installations editable originals, so conflicts have no owner, and it bypasses the
library as the maintained source of truth.

## Decision

A **kind** names a library folder and its format. A **tool** names a consumer.
`config.DESTINATIONS` maps each kind to the tools that read it and their project
folder. Initially:

| Kind | Tools and destinations |
| --- | --- |
| `skills` | `kiro` → `.kiro/skills`, `claude` → `.claude/skills`, `codex` → `.agents/skills` |
| `agents`, `prompts`, `steering` | `kiro` → `.kiro/…` |
| `copilot*` | `copilot` → `.github/…` |

Portable configuration may contain `"tools": ["kiro", "claude", "codex", "copilot"]`. The
deepest layer that sets `tools` wins; the default is `["kiro", "copilot"]`, which
keeps existing installations unchanged. An inbound selection installs one live link
per enabled tool that reads its kind. Selecting a kind that no enabled tool reads is
a preview conflict, not a silent no-op.

Installation state remains keyed by destination path. Disabling a tool therefore
archives its managed links on the next sync through the existing removal path.
Removing a managed asset archives every managed destination of that selection and
disables the selection; removing an unmanaged local asset archives only that path.

Discovery (`scan`) includes every tool folder, so a Claude-only skill can be
contributed into the library and then received for both tools.

Formats that differ between tools (Kiro steering vs. Claude rules, Kiro JSON agents
vs. Claude Markdown agents, prompts vs. commands) are out of scope here. If added,
they are generated copies owned by this tool and regenerated from the original,
never links, and they need their own ADR.

## Consequences

One edit to a shared skill reaches Kiro and Claude. Previews may list two links per
selection. Configuration without `tools` behaves as before. Placement in
`.claude/skills` does not prove Claude Code loads the skill; activation still needs
checking in the consuming tool.
