# Changelog

What changed for people using Captain Share. Newest first. `captain-share update`
and About in the window show the entries newer than your version.

## Unreleased

- Profiles in the window: Settings → a library's Options → Profiles lists the named
  sets it offers, gets one globally or in this project, and edits or adds one
  (`profiles/NAME.manifest`). On the command line, `captain-share profiles LIBRARY`
  lists them and `starter LIBRARY --profile NAME` edits one.

## 0.3.0 — 2026-10-15

- Ask your agents: `captain-share skill install` (or first setup) installs the
  captain-share skill, so Kiro and Claude Code can get, add and share skills for you.
- Get skills from any Git repository, like `npx skills add`:
  `captain-share add OWNER/REPO --skill NAME`, or Library → From Git….
- Clone a library by pasting its address; it goes to your Libraries folder (a new
  setting). Repositories that group skills in category folders work as libraries.
- Library and Share: search, type and library filters, and Hide for assets you will
  not use. Compact view is the default.
- One window per folder; About offers Restart window whenever the code on disk is
  newer, also after a `git pull`.
- Show in Finder works for global skills in Share.
- A shorter README with a video, a comparison with other tools, a code of conduct,
  a security policy, and issue and pull request templates.

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
