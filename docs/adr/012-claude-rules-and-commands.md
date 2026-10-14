# ADR 012: Steering and prompts for Claude Code as generated files

Status: Accepted

## Context

Libraries keep steering and prompts in Kiro's format (ADR 002 left other tools out).
Claude Code reads equivalents in other formats:

- Rules in `.claude/rules/*.md` (and `~/.claude/rules/`). `paths:` (a YAML list of
  globs) is the only frontmatter field Claude reads; a rule without it always loads.
  Rule folders follow symlinks; a link outside the project needs a one-time approval
  in Claude Code. (code.claude.com/docs/en/memory)
- Commands in `.claude/commands/NAME.md`, merged into skills but still read. Arguments
  are `$ARGUMENTS` and `$ARGUMENTS[N]`, numbered from 0. (code.claude.com/docs/en/skills)

Kiro steering has `inclusion: always | fileMatch | manual | auto` with
`fileMatchPattern` (a glob or a list); Kiro prompts use `${1}`…`${10}`, `${@}` and
`$ARGUMENTS`. (kiro.dev/docs/steering, kiro.dev/docs/cli/chat/manage-prompts)

Linking a steering file as a rule would make every `fileMatch`, `manual` and `auto`
file load always, so the content must be converted.

## Decision

`claude` reads `steering` (`.claude/rules`) and `prompts` (`.claude/commands`).
These destinations get a **generated file**, never the original:

- `formats.py` converts text only. Always-included steering (also without
  frontmatter) becomes a rule without frontmatter; `fileMatch` becomes `paths:` with
  the same globs. `manual` and `auto` steering, and `fileMatch` without a pattern,
  have no rule equivalent: they stay Kiro-only and the preview says so. Kiro prompt
  arguments are renumbered (`${1}` → `$ARGUMENTS[0]`, `${@}` → `$ARGUMENTS`).
- The plan writes the result to `GLOBAL/rendered/claude/SOURCE/KIND/NAME` (a `render`
  change with its content, covered by the preview fingerprint) and links the
  destination to it. Every existing link rule applies: installation state records
  the generated file as the link's target, status checks it, removal archives the
  link, and the generated file is shared by all places that select the asset.
- Each preview converts again. When the generated file differs (the library changed,
  or someone edited it through the link), it is archived in the global folder and
  rewritten. Apply locks the global folder.
- Notes name what a conversion could not carry over: dropped frontmatter fields,
  `#[[file:…]]` references (Claude does not resolve them), and inclusions that stay
  Kiro-only. They show in the preview and the CLI.
- Libraries keep Kiro's format: files in `.claude/rules` and `.claude/commands` are
  not offered in Share, and an outbound selection from them is refused.
- `why` names the library asset behind a generated file.

Agents stay Kiro-only: Kiro JSON agents and Claude Markdown subagents differ in tools,
permissions and resources beyond a faithful text conversion.

## Consequences

One steering file or prompt reaches both tools, with scoping kept. Turning on
`claude` adds these links at the next sync; existing files with the same names are
conflicts as before. `rendered/index.json` (machine-local) records which places
link each generated file; applying a plan updates it, and the plan that removes the
last link archives the generated file in the global archive. `captain-share
rendered` lists generated files and the places that still link them (checked
against each place's installation state); `--clean` archives files nothing links,
such as ones left from before the index. Not verified here:
whether Claude Code follows a symlinked file in `.claude/commands/` and whether it
still reads `~/.claude/commands/`; the docs confirm symlinks for rules and skills
only. Manual and auto steering could later become Claude skills; that is a separate
decision because skills share `.claude/skills` with skill assets.
