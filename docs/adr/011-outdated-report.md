# ADR 011: Outdated report instead of a lock file

Status: Accepted

## Context

People want to know which team version they have and what is behind. Package
managers answer this with a lock file of resolved versions and file hashes. Captain
Share installs by linking each tool folder to the library checkout (ADR 001), so a
linked asset always shows the checkout's current files: its version is the
checkout's commit, and a pinned record would only repeat it or go stale. Copies made
by other installers carry no record, so a lock cannot say which team version they
came from either; only the library's Git history can (ADR 007).

## Decision

No lock file and no new installation state. `captain-share outdated` reads what is
already known, for the user-wide (global) installs and the target folder:

- **Linked assets:** the version you have is the newest commit touching the asset at
  the checkout's HEAD. `newer` when the upstream branch has commits touching it
  (as of the last fetch; `--fetch` fetches each library first and records the time
  as Update library does, ADR 009). `edited` when the checkout has uncommitted
  changes in it, which a linked skill edited in place produces.
- **Copies** in agent folders with the name of a library asset: `copy-current` when
  equal to the library version, `older-copy` when equal to an earlier team version
  (ADR 007's history comparison), otherwise `changed-copy`.
- **Kept versions** whose team original changed since they were kept
  (`kept-behind`).

Each group names its next step (Update library, Share, adopt, diff). The report
only reads; `--fetch` changes only remote-tracking refs.

## Consequences

Nothing new to keep in sync or migrate. The answer is only as fresh as the last
fetch, which the report states. A copy that matches no recorded team version is
reported as changed even if another tool produced it.
