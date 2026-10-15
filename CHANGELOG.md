# Changelog

What changed for people using Captain Share. Newest first. `captain-share update`
and About in the window show the entries newer than your version.

## 0.2.0 — 2026-10-15

- The window opens on your libraries: what is new since you last looked, what you
  have, and everything else, with one Get per asset. Behind the team lists copies
  and installs that are older than the team version.
- Share skills from a project or from your global folders, with a preview first; a
  share says which library and branch it landed on and can ask for review on a
  protected branch.
- Install a skill from a zip or folder into your own library, try it, and share it
  with the team later.
- Steering, prompts and agents reach Claude Code too: rules, commands, skills and
  subagents generated from the Kiro originals. Claude rules and commands can be
  shared back.
- `captain-share why PATH` and `outdated` explain where a file came from and what is
  behind; a library can run its own check before a share commits.
- Safety: hidden characters are refused, powers that run code need your approval,
  and a commit whose signature does not verify is not pushed.
- Update Captain Share from About or with `captain-share update`; Restart window
  keeps the same link.
- One-line install with pipx, a replayable tour, light and dark themes, and a
  compact view.

## 0.1.0

- First version: libraries, links into agent folders, previews, archives, and the
  web window.
