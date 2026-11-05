# ADR 018: Windows, and managed copies where links are unavailable

Status: Accepted

## Context

Captain Share ran on macOS and Linux only. On Windows it failed at import (`fcntl`),
its lock folder used `os.getuid()` and POSIX mode bits, the open-window check used
`os.kill(pid, 0)` (on Windows signal 0 is `CTRL_C_EVENT`), and text files were read
and written in the console code page. Installs are symbolic links (ADR 001, ADR 002),
and Windows lets a user create them only in Developer Mode or an elevated process.

Directory junctions need no privilege, but `is_symlink()` is false for them, so they
would pass every check that refuses to follow or replace a link. They were rejected.

## Decision

- **Locks.** `config.lock_file` uses `fcntl.flock` on POSIX and `msvcrt.locking` on
  byte 0 on Windows (polling while waiting, since `LK_LOCK` gives up after ten
  seconds). On Windows the lock folder is `captain-share-locks-USER` in the user's
  own temporary folder; its access is in ACLs, so mode and owner bits are not
  checked there.
- **Processes.** A recorded window counts while `OpenProcess` and
  `GetExitCodeProcess` report it running. Restart window starts the new process and
  exits, because Windows has no exec that keeps the process.
- **Text.** Text files are read and written as UTF-8; generated files are written as
  bytes so their hashes do not change with CRLF translation. Git output is decoded
  as UTF-8.
- **Paths.** `safe_path` also refuses relative paths with a drive or root
  (`C:x`, `\x`). Link targets lose Windows' `\\?\` prefix before comparison.
- **Managed copies.** `config.links_supported()` probes once whether a symlink can
  be created (always true elsewhere; `CAPTAIN_SHARE_LINKS=0` or `1` overrides it).
  Where it cannot, an inbound install is a `copy` change instead of a `link`, and
  installation state records the copy's snapshot under `copy`. Every check that
  compared a destination to "a link to its original" compares it to
  `engine.installed_as(row)`: the link, or that snapshot. So an edited copy is a
  changed managed destination, exactly like a replaced link, and is never
  overwritten. Sync compares a copy with what it would install (the original or the
  generated file); when the library changed, it archives the old copy and writes the
  new one. When links become available, the next sync archives copies and links;
  the reverse works the same way.
- **Explorer.** Show in file manager runs `explorer /select,PATH`.
- **CI** runs the same checks on `windows-latest`, without blocking until it has
  been green for a while.

## Consequences

Windows users without Developer Mode can use Captain Share, but a library update
reaches a project only on its next sync, and every refresh adds an archive entry.
Enabling Developer Mode gives live links like on macOS and Linux.

Known gaps: the outdated report hashes files with `git hash-object --no-filters`, so
a checkout with `core.autocrlf=true` reports CRLF copies as different from every
library version. `pip` cannot replace the `captain-share.exe` launcher while a
window runs from it, so update with the window closed. Fake `kiro-cli` tests are
POSIX scripts and are skipped on Windows. Kiro, Claude Code, Codex and Copilot
discovery of copied assets on Windows is assumed, not measured.
