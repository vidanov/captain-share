# ADR 014: Share Claude rules and commands back in Kiro's format

Status: Accepted

## Context

ADR 012 keeps libraries in Kiro's format and refused to share files from Claude's
folders. People who write rules or commands in Claude Code first had no way to
contribute them.

## Decision

A file in `.claude/rules` or `.claude/commands` that no library has is offered in
Share as steering or a prompt, and sharing converts it (`formats.to_kiro`):

- A rule's `paths:` (a YAML list or a comma-separated string) becomes
  `inclusion: fileMatch` with `fileMatchPattern` as a list; a rule without paths
  becomes `inclusion: always`. Other fields are dropped and named in the notes.
- A command's `$ARGUMENTS[N]` and `$N` become `${N+1}`; `$ARGUMENTS` and the
  frontmatter stay (Kiro prompts use name and description too).

The plan writes the converted text into the library (`render`, covered by the
fingerprint). Its outbound record keeps a hash of what it wrote, so a library file
edited independently afterwards is a conflict as for copies, and a replaced original
is archived in the library. Converting forward again (ADR 012) gives back the same
rule or command for the cases the tests cover, so "use the team version here"
afterwards links both tools without a conflict.

Subagents (`.claude/agents`) and generated skills are not shared back; Share does
not offer them and an outbound selection from them is refused.

## Consequences

Claude-first rules and commands reach Kiro users through the library. A rule whose
paths use a comma-separated string comes back from the library as a list, so
adopting it afterwards reports that the copy differs; replacing it is the user's
choice as usual.
