# ADR 007: Extended local copies of library assets

Status: Accepted

## Context

People extend a team skill in their project. Adopting (ADR 005) treats any
difference as a conflict to resolve by replacing the copy. Two different intentions
hide behind an extended copy: the change is good for everyone, or it is personal or
project-specific. Keeping a copy under the team name also gives agents two skills
with the same name, and a kept copy silently falls behind the team version.

## Decision

When a local copy differs from its library original, the window and CLI offer three
explicit choices; each one previews, applies through a plan, and keeps archives.

- **Show changes.** A file-by-file unified diff between the local copy and the
  library original (text files; binary files are named only; output is bounded).
- **Send my changes to the team (propose).** The local version replaces the library
  original, committed with the user's note and only those paths.
  - *Directly*: commit on the checkout's branch and push (as in ADR 005), then adopt
    so the project links to the library version.
  - *For review*: build the commit from a temporary index on a new branch
    `share/NAME-USER` based on the checkout's HEAD, push that branch, and return a
    review link when the remote host is recognisable (GitHub compare, GitLab merge
    request). The library working tree and its current branch are not touched, and
    the project keeps its copy until the change is merged.
- **Keep as my version.** The copy is renamed (default `NAME-PROJECT`): the original
  folder or file is archived, a renamed copy is written, and the skill's
  frontmatter `name` is updated so agents see distinct names. The project's portable
  `.captain/sharing.json` records the relation under `versions` (new name → source,
  kind, original name, base commit). Optionally the team version is linked too.
  Library and Share then report when the team original changed after the base
  commit, with the same diff view.
- **Use team version** stays as in ADR 005 (replace, archived).

## Consequences

Divergence becomes a choice the user makes once instead of a recurring conflict.
Review-branch proposals work with protected default branches. Captain Share writes
renamed copies and frontmatter names only through previewed plans; originals stay
recoverable in the archive.
