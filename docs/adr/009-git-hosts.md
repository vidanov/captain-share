# ADR 009: Working with team Git hosts

Status: Accepted

## Context

Team libraries live on hosted Git (GitLab, GitHub) with rules Captain Share did not
anticipate. Observed with a team's GitLab library: an HTTPS remote without a stored
login failed with "could not read Username … terminal prompts disabled" and "HTTP
Basic: Access denied"; the project only accepts signed commits; and `main` is
protected, so changes arrive through merge requests. Raw Git text did not tell
people what to do, and a direct commit could never be pushed.

## Decision

- **Explained failures.** Git failures are classified into an HTTPS login that
  Captain Share cannot type, an SSH key or host key problem, a signing failure
  (local key or a host rule requiring signed commits), a protected branch, and a
  branch that moved on. Each gets a plain message, a `hint` for the next step and,
  where one exists, an exact `command` (for example the `remote set-url` to the
  SSH address derived from the HTTPS one). The original Git text stays in `detail`,
  always with credentials removed from URLs. A refused push keeps the commit and
  reports `push_error_hint` and `push_error_command`.
- **Signing.** Git applies the user's `commit.gpgsign` itself; Captain Share never
  turns it off. A library can also opt in on this machine (`sign` beside the
  checkout path in `sharing.local.json`): commits then use `-S`, and review commits
  are signed as well (also when `commit.gpgsign` is set, which `commit-tree` does
  not read). Before changing files, a signing check creates an unreferenced test
  commit; if the key cannot sign, nothing changes and the hint names the setting.
  Git can also produce a signature that does not verify (for example a
  `gpg.program` wrapper that feeds the passphrase on standard input, where Git
  sends the data to sign). Before pushing, Captain Share asks Git for the commit's
  signature status and keeps a commit with a bad signature local, with a hint and
  the `verify-commit` command.
- **Library check.** A library can have a check command on this machine (`check`
  beside the checkout path in `sharing.local.json`, set in Settings or with
  `captain-share check SOURCE --set "…"`). Captain Share never runs a command the
  library itself asks for. Direct commits run it in the repository after the files
  are in place: when it fails, nothing is committed and the result carries its last
  output, a hint and the command to run again; files it changed (a refreshed
  catalog, for example) are committed with the asset. Review commits do not run it;
  the host's CI checks the merge request.
- **Where it landed.** Every result that commits names the library and branch
  (`library`, `landed_on`: the checkout's branch, or the review branch), so the
  window says "Commit abc1234 in team on main · pushed".
- **Review mode** for share, import, promote, starter set and proposals: the commit
  is built from a temporary index on a new branch `share/NAME-USER` from the
  checkout's HEAD; the working tree and the current branch are never touched.
  Pushing it to GitLab adds push options that open the merge request; the link the
  host prints (or a computed GitHub or GitLab link) is returned. Nothing local
  switches until the change is merged: a shared asset is not adopted, a promoted
  asset stays in the personal library with its links, and results say
  `waiting_for_review`.
- **Remembered status.** The last successful fetch or pull of each team library is
  kept in the global `library-status.json` (time, behind, ahead, head). The library
  home shows it as current while it is under six hours old and the head has not
  moved. Personal libraries have no remote and are excluded.

## Consequences

People see what to do instead of Git's text, and protected or signed-only
libraries work without a terminal. Captain Share still never stores or types
credentials, never forces a push, and never changes a host's settings. A review
branch left over from an earlier attempt blocks a new one with the same name until
it is removed. Whether a host accepts a signature (key registered there) is the
host's decision.
