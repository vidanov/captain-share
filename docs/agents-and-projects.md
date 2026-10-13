# Using Captain Share with agents and projects

One library holds the originals. Captain Share links them into the places each
agent tool reads, for one project, for a group of projects, or for your whole user.

## Which agents read what

| Agent | Project folder | User-wide folder | Kinds it reads |
| --- | --- | --- | --- |
| Kiro IDE and Kiro CLI (one setting: `kiro`) | `.kiro/` | `~/.kiro/` | skills, prompts, steering; agents (`.json`) are Kiro CLI agent configs |
| Kiro powers (IDE and CLI) | – | `~/.kiro/powers/installed/` | powers, installed by `kiro-cli` |
| Claude Code | `.claude/skills/` | `~/.claude/skills/` | skills |
| Codex | `.agents/skills/` (and parent folders up to the repository root) | `~/.agents/skills/` | skills |
| GitHub Copilot | `.github/` | – | `copilot-instructions.md`, `*.instructions.md`, `*.prompt.md` |

Choose the agents for a target with `tools`. The default is `kiro copilot`:

```bash
captain-share --target /path/to/project tools kiro claude codex copilot
captain-share --target /path/to/project sync --apply
```

Each enabled agent gets its own live link to the same original; nothing is copied
between `.kiro/`, `.claude/`, and `.agents/`. Turning an agent off archives its links at the
next sync. Kiro steering, Kiro JSON agents, and prompts are not converted for Claude
Code; those stay Kiro-only. Agents not in the table (for example Cursor)
are not targets today. Codex custom prompts are deprecated by OpenAI, and its
`AGENTS.md` instruction files are not managed here.

Kiro powers are installed for your user, not linked:
`captain-share powers install LIBRARY NAME --apply` runs `kiro-cli powers install`.
Library edits reach Kiro after another install.

## Three places to install

| Install for | Command target | Use it for |
| --- | --- | --- |
| Your user | `--target "$HOME"` | Everyday personal skills, available in every project |
| A group of projects | `--target /path/to/parent-folder` | A baseline every project in that folder inherits |
| One project | `--target /path/to/project` | Project- or customer-specific assets |

**Your user.** Selections saved for your home folder link into `~/.kiro`,
`~/.claude`, and `~/.agents`, which the agents read in every project. Projects inherit your home
libraries and `tools` setting, but not these selections, so a user-wide skill is
never linked a second time into a project.

**A group of projects.** Selections in a parent folder's `.captain/sharing.json` are
inherited by every project below it. Save them once, then sync each project:

```bash
captain-share --target ~/work/customer-a source add team /path/to/team-library
captain-share --target ~/work/customer-a import-profile team pilot --apply
captain-share --target ~/work/customer-a/backend sync --apply
captain-share --target ~/work/customer-a/frontend sync --apply
```

A project can drop one inherited asset with `deselect inbound LIBRARY KIND NAME`.

**One project.** Run the same commands with the project as target. Its
`.captain/sharing.json` can be committed so teammates get the same selection;
checkout paths stay in the ignored `sharing.local.json`.

## In the web interface

`captain-share --target /path/to/project serve --open` opens one window for all three
places ([ADR 004](adr/004-web-places.md)):

- **Working on** switches between the project, the folders above it (templates for
  the projects inside), and Your user. Agent checkboxes show where links go for the
  chosen place.
- **Receive** marks assets "inherited from …" or "installed for your user", so you
  install each skill in one place.
- Saving at a folder previews the registered projects inside it; the folder itself
  gets no links.
- **Projects** lists this project and registered ones with their sync state. Add
  project… registers a folder; Preview sync for all previews every project for one
  Apply.

## Connect libraries once

Connect each library for your user; every project then lists it:

```bash
captain-share --target "$HOME" source add team /path/to/team-library
```

A project that needs a library only it uses can connect it with its own target.

## Daily use

- `git pull` in a library checkout updates every linked project and agent at once.
- New assets in a library are not selected automatically: select them where needed.
- After changing selections or `tools`, run `sync --apply` (or Preview and Apply in
  `serve`) in each affected target. Captain Share never syncs projects you did not
  name.
- `captain-share --target TARGET status` shows what is linked and what drifted.
- Agents load skills when a session starts; open a new session after a sync.

## Avoid duplicates

An agent that sees the same skill name in its user-wide and project folders uses
one of them, and which one is not always obvious. Install a skill in one place:
user-wide for personal tools, per project for project-specific ones. The Receive
view shows each destination's state before you apply.
