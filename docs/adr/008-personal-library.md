# ADR 008: Personal library

Status: Accepted

## Context

A skill found in a zip or folder (ADR 006) is usually something to try before
recommending it to a team. Adding it straight to a team library publishes an untried
asset; installing it by hand bypasses previews, archives and later updates.

## Decision

Captain Share keeps one private library per user, `my-skills`, at
`~/Libraries/my-skills`:

- Created on demand and idempotently: a local Git repository (`main`, flat layout
  with `skills/`, `agents/`, `prompts/`), a first commit with the user's own Git
  name and email (refused without them), connected as a global source. Only that
  fixed folder is created; an existing non-empty folder is used only when it already
  is a Git repository with a library layout. It has no remote and is never pushed.
- Imports from a zip or folder can go into it with local commits only; the CLI
  `install PATH` imports and then gets the assets for the user (`--here` for the
  project). The library home marks it `personal`: it never reports behind, offers
  no update, and marks nothing new.
- **Promote** moves one asset to a team library in one previewed plan: copy and
  commit into the team library (pushed when asked), switch every place that selects
  or links `my-skills/KIND/NAME` (startup project, folders above it, the user,
  registered projects) to the team asset and relink it, then archive the personal
  copy with a local commit "Moved NAME to TEAM". The plan refuses when the team has
  that name already (the ADR 007 choices apply), when the team library cannot
  commit, when the personal copy has uncommitted edits, or when a place that uses
  it does not have the team library connected.
- Later steps never undo the team commit. If any place cannot be relinked, the
  personal copy is kept so no link breaks, and the result lists what is unfinished.

## Consequences

Trying a skill no longer requires a team library or a remote. The personal library
is ordinary Git history on the user's machine; backing it up is the user's choice.
Promotion changes selections in several places at once, but only places this
window manages, through the same relink plans, archives and locks as everything
else.
