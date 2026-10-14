# ADR 013: Manual and auto steering as Claude skills; agents as subagents

Status: Accepted

## Context

ADR 012 left manual and auto steering and all agents Kiro-only. Claude Code has close
equivalents (code.claude.com/docs/en/skills, /sub-agents):

- A skill with `disable-model-invocation: true` runs only when called as `/name`,
  like manual steering referenced with `#name`. A skill with a `description` is
  loaded when the description fits the request, like auto steering.
- A subagent is Markdown with `name`, `description`, optional `tools` (omitted means
  all tools) and the system prompt as the body. Kiro agents are JSON with `prompt`
  (inline or `file://`), `tools`, `mcpServers`, `hooks`, `resources` and permissions.

## Decision

Conversions may place their result outside the kind's usual folder or under another
name; the engine links that destination, and installation state accepts exactly
those destinations (`formats.destinations`), nothing else.

- **Manual steering** becomes `.claude/skills/NAME/SKILL.md` with `name`,
  `description` (the steering's, else "Team guidance: NAME") and
  `disable-model-invocation: true`. **Auto steering** becomes a skill with its
  required description. NAME is the file name lowercased to letters, digits and
  hyphens. The generated folder is `rendered/claude/SOURCE/steering/NAME.md.skill/`.
- **Agents** become `.claude/agents/NAME.md`: `name`, `description`, the prompt
  (inline, or a `file://` path beside the agent in the library), and `tools`
  mapped (`read` → Read, Grep, Glob; `write` → Write, Edit; `shell` → Bash; web
  fetch and search). `*` keeps all tools by omitting `tools`. An agent that lists no
  tools is not converted, so a subagent never gets more tools than the agent had.
  Tools without a match, MCP servers, hooks, resources and permissions are dropped
  and named in the notes.
- Removing a selection by its library name removes converted destinations too.

## Consequences

Steering that changes inclusion moves between `.claude/rules` and `.claude/skills` at
the next sync. A generated skill or subagent can collide with a skill or subagent of
the same name; that is the usual unmanaged-file conflict. Whether Claude invokes the
generated skills and subagents as intended is not verified here.
