---
name: captain-share
description: Get, share and manage agent skills, steering, prompts, agents and Kiro powers with the captain-share CLI. Use when the user wants to install a skill a colleague mentioned, install skills from a Git repository, share a skill they made with the team, see what is new or out of date, find where an installed file came from, connect or clone a skill library, or fix a Captain Share problem.
---

# Captain Share

`captain-share` links a team's Git libraries of skills, steering, prompts and agents
into Kiro, Claude Code, Codex and Copilot, and shares the user's work back. It is
the authority for these folders: never hand-copy, link or delete files in
`.kiro/`, `.claude/`, `.agents/` or `.github/` yourself.

## Rules

1. **Preview, then ask.** Every changing command previews without `--apply`. Show
   the user what it will do, and add `--apply` only after they agree. Share,
   commit and push only what the user asked to share, with their wording for the
   note.
2. **Ask before** `--push`, `--replace` (archives a different copy), and
   `--allow-code` (lets a Kiro power start MCP servers or hooks: show them first).
3. **Relay hints.** When a command fails it prints what happened, a hint and often
   the exact command to fix it. Pass these on; do not retry the same failing
   command, force a push, store credentials or change signing settings.
4. **Options go first:** `captain-share --target FOLDER --json COMMAND …`. Without
   `--target` the current folder is the target. Use `--json` when you read results.
5. Placing a file proves nothing about activation: the user checks the skill in a
   new agent session.

## Tasks

| The user wants to | Run |
| --- | --- |
| see what is available | `captain-share list --source LIBRARY` (`config` lists libraries) |
| get an asset a colleague mentioned | `captain-share get NAME` (user-wide), `--here` for this project only; `SOURCE/NAME` when ambiguous |
| get skills from a Git repository (like `npx skills add`) | `captain-share add OWNER/REPO` lists them; `--skill NAME` (repeatable) previews; they go into the private `my-skills` library |
| try a zip or folder | `captain-share install PATH` |
| share a skill they made | `captain-share share KIND NAME --source LIBRARY --note "…"`, then `--push --apply`; add `--review` for a protected branch (opens a merge request) |
| move a tried skill to the team | `captain-share promote KIND NAME --to LIBRARY --note "…"` |
| send changes to a team asset | `captain-share diff KIND NAME --source LIBRARY`, then `propose … --note "…"`; or `keep … --as NEWNAME` to keep their own version |
| use the team version instead of a copy | `captain-share adopt KIND NAME --source LIBRARY` |
| see what is behind | `captain-share outdated --fetch` |
| find where a file came from | `captain-share why PATH` |
| remove an asset | `captain-share remove KIND NAME` (archives; `archive` and `restore ID` bring it back) |
| connect a library | clone with Git, then `captain-share source add NAME PATH --scope global`; in the window, Settings → Clone a library from Git takes just the address |
| choose agents | `captain-share tools kiro claude codex copilot` (`--target "$HOME"` for every project) |
| install a Kiro power | `captain-share powers install LIBRARY POWER` |
| update Captain Share itself | `captain-share update`, then `--apply` if they agree |

Kinds are `skills`, `steering`, `prompts`, `agents`, `powers` and the Copilot kinds;
prompts and steering names end in `.md`, agents in `.json`.

## Good to know

- **Places.** Global (the home folder) reaches every project; a folder's
  `.captain/sharing.json` reaches projects below it; a project's reaches only it.
- **Links.** Installed assets link to the library original, so editing one edits
  the library: offer `share` (Send changes) instead of copying.
- **Claude Code** gets steering, prompts and agents as generated rules, skills,
  commands and subagents. Edit the library original, never the generated file;
  the preview names what a conversion leaves out.
- **Safety checks** refuse hidden characters, credentials and personal paths. A
  library can run its own check before a share commits (`captain-share check
  LIBRARY`); if it fails, nothing is committed: show the output.
- **The window**: `captain-share serve --open` offers the same with previews, for
  people who prefer clicking.
