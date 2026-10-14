---
name: manage-shared-assets
description: Configure and maintain shared skills, agents, prompts, and steering across independent repositories with captain-share. Use for inbound installs, outbound sharing, aliases, layered .captain setup, archives, and migrating Captain connections.
---

# Manage shared assets

Use the `captain-share` CLI as the filesystem authority. It must be installed or
available as `bin/captain-share` in its checkout. Work with the user's chosen
folders and libraries; a source repository's access rights remain independent.

Inspect the target with `captain-share --target FOLDER status` and `config`;
`why PATH` explains where one file in an agent folder came from.
These read merged global, ancestor-folder, and repository settings. Portable
selections live in `.captain/sharing.json`; checkout paths live separately in
`sharing.local.json`. A deeper selection overrides the same source/kind/name;
a disabled selection masks an inherited one. Folder settings apply to descendants
when they are explicitly synced, not automatically to every child repository.

## Choose the operation

- **Connect a library:** `source add NAME CHECKOUT --layout ip` (or `flat` for
  `skills/`, `agents/`, etc. at the root). `--scope global` registers defaults;
  `--target PROJECT_FOLDER source add … --scope folder` scopes a group.
- **Receive:** `list --source NAME`, then `select inbound NAME skills SKILL`
  with optional `--alias LOCAL_NAME`. Use the matching kind and file extension
  for agents, prompts, steering, or Copilot assets.
- **Get one asset:** `get NAME` (or `SOURCE/NAME`, `SOURCE/KIND/NAME`) saves the
  selection for the user and previews; `--here` targets the project, `--apply`
  applies. An ambiguous name lists the full references to choose from.
- **Share one asset:** `share KIND NAME --source NAME` previews; adding
  `--note TEXT --apply` copies it and commits only its paths in the library
  (`--push` to push; a refused push keeps the commit). Share only when the user
  asked to, with their wording for the note. Add `--adopt` to swap the project's
  own copy for a link to the library original afterwards.
- **Use the library version:** `adopt KIND NAME --source NAME` previews archiving
  the local copies and linking the original; `--replace` only when the user agrees
  to archive a copy that differs; `--apply` to apply.
- **Try before sharing:** `install PATH` previews a zip or folder; `--apply` adds it
  to the private `my-skills` library (local commits, never pushed) and installs it
  for the user (`--here` for the project). `promote KIND NAME --to TEAM --note TEXT
  --apply` moves it to a team library when the user wants to share it.
- **Disconnect a library:** `source disconnect NAME` previews archiving its links
  and removing its selections and bindings everywhere; `--apply` when the user
  confirms. The checkout, unmanaged copies and installed Kiro powers stay (listed).
- **Protected or signed-only libraries:** add `--review` to `share`, `import`,
  `promote` or `starter` to commit on a `share/NAME-USER` branch and open a merge
  request with `--push`. When Git fails, relay the printed hint and command to the
  user; do not retry pushes, store credentials or change signing settings for them.
- **Starter set:** `starter SOURCE` lists a library's starter set; `--set`,
  `--add`, `--remove` (each `kind/name`) or `--clear` preview the new manifest, and
  `--note TEXT --apply` commits only that file (`--push` to push).
- **A copy that extends the team version:** `diff KIND NAME --source NAME` shows
  the changes. Ask the user which they mean: `propose … --note TEXT --apply`
  updates the library version (add `--review` for a pushed review branch when the
  default branch is protected or others should review), or `keep … --as NEWNAME
  --apply` keeps it as their own renamed version. `versions` reports kept versions
  whose team original changed since.
- **Add from outside:** `import PATH --source NAME` previews the assets in a zip
  file or folder (all valid ones; `--name` picks or renames one, `--kind steering`
  for Markdown). `--note TEXT --apply` copies them into the library and commits only
  their paths; existing library assets are never overwritten. Same consent as share.
- **Contribute:** `scan`, inspect the chosen local originals, then
  `select outbound NAME skills SKILL --path skills/SKILL`. Outbound copies are
  written to the library checkout; existing shared edits cause a conflict.
  Generated outputs, private project context, fonts and licensed material need
  their own distribution decision. The built-in credential/path scan is only a
  heuristic, never a confidentiality or licensing verdict.
- **Choose agent tools:** `tools` shows the enabled list; `tools kiro claude codex
  copilot` sets it for the target (`--target "$HOME"` for every project). Skills then
  link into `.kiro/skills/`, `.claude/skills/`, and `.agents/skills/` (Codex) from one
  original. Steering, agents, and prompts stay Kiro-only; do not hand-copy them.
- **Kiro powers:** library powers live in `ip/powers/NAME/` (`plugin.json` or
  `POWER.md`, name equal to the folder). `powers list --source NAME` shows Kiro's
  install state; `powers install SOURCE POWER` previews and `--apply` runs
  `kiro-cli powers install` (also updates). Powers are user-wide copies, never
  inbound selections. Do not edit `~/.kiro/powers` directly.
- **Change an alias:** select the same inbound source/kind/name with a new
  `--alias`. The old shortcut is archived when synced. A filesystem alias does
  not rename the skill's frontmatter or an agent's internal resource references.
- **Remove:** `remove skills LOCAL_NAME` previews archiving. `--apply` performs
  it, archives that selection's links for every tool, and disables the
  corresponding inherited/local inbound selection. When a preview reports an
  existing unmanaged asset, `remove KIND NAME --path DESTINATION` archives only
  that local copy and keeps the selection.
  Removing a shortcut does not delete its shared original.
- **Restore:** `archive`, then `restore ID` to preview, and `restore ID --apply`
  to restore. Existing destinations are never overwritten.

Put `--target FOLDER` and `--json` **before** the subcommand. For sequential
operations use `plan --direction inbound|outbound|both --save /tmp/share-plan.json`
and `apply /tmp/share-plan.json`. Apply rechecks the preview; when it is stale,
inspect the changed files and produce a fresh preview. `sync --apply` computes
and applies a fresh plan within the already authorized scope. Do not force
through a conflict or repeatedly retry unchanged errors.

An outbound copy leaves the original in its project. To make the library the
source of truth, review the copy, archive the old local installation if it
occupies the desired destination, and select the library asset inbound. Keep
all supporting resources in a skill folder. If an agent references other
skills or steering files, select those dependencies too; aliases do not rewrite
its JSON resources.

## Existing Captain/toolkit installations

`import-captain /path/to/tooling` previews source/repository connections from
Captain's `sources.json` and `repositories.json`; `--apply` imports connections
only. `import-profile SOURCE PROFILE` previews a toolkit manifest selection.
Neither command migrates installation ownership. Leave existing `.kiro-ip`,
hooks, and toolkit ledgers to their original installer. Inspect any collision
and disconnect it with that installer before installing a replacement.

For a graphical chooser use `captain-share --target FOLDER serve --open`.
`assist "REQUEST"` prepares a Kiro CLI handoff; `--run` starts an interactive
chat with the user's normal approvals. Avoid granting blanket tool trust.

After applying, inspect `status` and `guard`. Report links/copies/archives and
any conflicts. File installation checks do not prove skill activation or IDE
compatibility; verify that separately in the consuming agent session. Commit
and push library changes only when requested or already authorized. Public tool
source and private shared content remain separate repositories.
