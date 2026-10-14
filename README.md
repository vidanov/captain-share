# Captain Share

A standalone, local tool for sharing skills, agents, prompts, steering, and Kiro
powers between independent repositories, for Kiro, Claude Code, and GitHub
Copilot. Use a CLI, a small web interface, or the bundled management skill. Keep
originals in libraries with their own access rights; each project chooses what it
receives and contributes.

The public tool contains the sharing mechanics. Your libraries and their private
content stay in separate repositories. Captain is not a runtime dependency.

See [agents and projects](docs/agents-and-projects.md) for which agent reads what
and how to install globally, a group of projects, or one project.

## Start

Python 3.9+, Git, and macOS or Linux. No third-party runtime packages or frontend
build step. Run directly from this checkout:

```bash
./bin/captain-share --help
./bin/captain-share --target /path/to/project serve --open
```

Or install into a virtual environment:

```bash
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/captain-share --target /path/to/project serve --open
```

Without `--target`, every command uses the **current working directory**:

```bash
cd /path/to/your-project
captain-share status
captain-share serve --open
```

The exact folder is the target, even when it is a subfolder of a Git repository.
Use `--target` to choose another folder. The web window stays attached to the target
chosen at startup; navigating the UI or changing another terminal's directory does
not change it. Global and ancestor settings are inherited into that target.

The server binds only to `127.0.0.1`, chooses a free port, and prints a URL with a
per-launch access token. The UI supports adding a library, choosing which
agents get links, installing assets per project, folder or user, sharing local
assets back, previewing/applying changes, resolving conflicts, installing Kiro
powers, and archiving and restoring local assets. Each server is tied to the target folder it
was started for; start another instance for another project. See
[Web interface](#web-interface) for details.

## Receive assets

A library can use `ip/skills/`, `ip/agents/`, etc. (`--layout ip`), or
`skills/`, `agents/`, etc. at its root (`--layout flat`); without `--layout` the
layout is detected from the folder. Clone it normally with
your own Git credentials, then register the local checkout:

```bash
captain-share --target /path/to/project source add team /path/to/library
captain-share --target /path/to/project list --source team
captain-share --target /path/to/project choose inbound --source team
# Or select precisely, including an optional filesystem alias:
captain-share --target /path/to/project select inbound team skills review-code --alias review
captain-share --target /path/to/project plan --save /tmp/sharing-plan.json
captain-share apply /tmp/sharing-plan.json
captain-share --target /path/to/project status
```

To get one asset quickly, name it. `get` resolves `NAME`, `SOURCE/NAME` or
`SOURCE/KIND/NAME` across connected libraries, installs globally (for your user) by default
(`--here` installs into `--target` or the current folder), shows the preview and
applies after you confirm or with `--apply`. A preview with conflicts changes nothing.

```bash
captain-share get review-code              # user-wide; asks before applying
captain-share get team/review-code --apply
captain-share --target /path/to/project get team/skills/review-code --here
```

Inbound assets are live symlinks. Editing the shared original changes every linked
project. Aliases change the local directory/file name, not skill frontmatter or
agent resource references. Select dependencies referenced by an agent alongside
that agent; this release does not automatically resolve those references.

Supported kinds: `skills`, `agents`, `prompts`, `steering`, `copilot`,
`copilot-instructions`, and `copilot-prompts`. Kiro destinations are under `.kiro/`;
Copilot destinations are under `.github/`. Skill folders require `SKILL.md`, agents
use `.json`, and prompts/steering use `.md`. Installed files still need validation
in the consuming CLI/IDE; placement does not prove activation or discovery.

Housekeeping files (`.DS_Store`, `.gitkeep`, `__pycache__/`) are hidden from discovery and
excluded from shared copies and content comparisons, including inside skills.
Unrelated file types and prompt subfolders are omitted from the asset list.
Original files remain untouched; unsafe content such as `.env` still blocks sharing.

### Kiro, Claude Code, and Copilot

One library original can be linked into several agent tools. Choose the tools per
target (or globally); the deepest configuration that names tools wins:

```bash
captain-share --target /path/to/project tools            # show: kiro copilot (default)
captain-share --target /path/to/project tools kiro claude codex copilot
captain-share --target /path/to/project sync --apply
```

| Kind | Kiro | Claude Code | Codex | Copilot |
| --- | --- | --- | --- | --- |
| `skills` | `.kiro/skills/` | `.claude/skills/` | `.agents/skills/` | |
| `steering` | `.kiro/steering/` | `.claude/rules/` or `.claude/skills/` (generated) | | |
| `prompts` | `.kiro/prompts/` | `.claude/commands/` (generated) | | |
| `agents` | `.kiro/agents/` | `.claude/agents/` (generated) | | |
| `copilot*` | | | | `.github/…` |

Each enabled tool gets its own live link to the same original; nothing is copied
between tool folders. Turning a tool off archives its links at the next apply.
`scan` also finds skills in `.claude/skills/`, so a Claude-only skill can be
contributed to a library and then received for every tool.
See [ADR 002](docs/adr/002-agent-tool-destinations.md).

Steering and prompts stay in Kiro's format in the library. For Claude Code they are
converted: always-included steering becomes a rule, `fileMatch` steering a rule with
`paths:` for the same globs, and prompt arguments are renumbered for commands.
Claude's folders link to these generated files, which every preview checks and
regenerates when the library changes. Manual steering becomes a Claude skill you
call as `/name`, auto steering a skill Claude loads when its description fits, and
agents that list their tools become Claude subagents; the preview lists what a
conversion leaves out ([ADR 013](docs/adr/013-claude-skills-and-subagents.md)). A
generated file is archived with the last link to it; `captain-share rendered` lists
them and `--clean` archives any nothing links. Claude rules and commands that no
library has can be shared too; they are converted to Kiro's format on the way in
([ADR 014](docs/adr/014-share-claude-files-back.md)). See
[ADR 012](docs/adr/012-claude-rules-and-commands.md).

### Kiro powers

Keep powers in the library under `ip/powers/NAME/`: an Agent Plugins 1.0.0
`plugin.json` (with `name`, `version`, `description`, `author.name`, `keywords`)
or a legacy `POWER.md`, plus optional `skills/` and `mcp.json`. The manifest name
must equal the folder name, because Kiro installs by manifest name and would
otherwise replace a different power.

Kiro installs powers user-wide by copying them, so powers are not linked into
projects. Captain Share previews and then runs Kiro's own installer:

```bash
captain-share --target /path/to/project powers list --source team
captain-share --target /path/to/project powers install team review-power          # preview
captain-share --target /path/to/project powers install team review-power --apply  # install or update
captain-share --target /path/to/project powers uninstall team review-power --apply
```

`list` compares Kiro's copy in `~/.kiro/powers/installed/` with the library:
`not-installed`, `current`, or `differs` (update available, or installed from
elsewhere). Library edits reach Kiro only after another install. Contributing a
power requires MCP `env` values to be `${VAR}` references. The web UI offers the
same Install, Update, and Uninstall actions on power rows. Requires `kiro-cli` on
`PATH`. See [ADR 003](docs/adr/003-kiro-powers.md).

## Contribute assets

```bash
captain-share --target /path/to/project scan
captain-share --target /path/to/project select outbound team skills review-code --path skills/review-code
captain-share --target /path/to/project plan --direction outbound
captain-share --target /path/to/project sync --direction outbound --apply
```

To share a single asset in one step, `share` previews the copy; with `--apply` it
copies the asset into the library checkout and commits **only that asset's paths**
with your note as the message (other staged changes stay staged). `--push` then
pushes without forcing; a refused push keeps the commit. It prints the `get`
command teammates can run. No outbound selection is saved.

```bash
captain-share --target /path/to/project share skills review-code --source team
captain-share --target /path/to/project share skills review-code --source team \
  --note "Add review-code skill" --push --apply
```

To add assets that arrived without Captain Share, `import` reads a zip file or a
folder: the folder itself as an asset (`SKILL.md`, `POWER.md`/`plugin.json`), the
assets one level down, the library layouts (`ip/<kind>/`, `<kind>/`), or else its
Markdown files as prompts (`--kind steering` for steering). It previews all valid
assets, or only the one `--name` selects (with one asset, `--name` renames it); an
existing library asset is never overwritten. With `--note TEXT --apply` it copies
them and commits only their paths, like `share`. Zips are extracted into a temporary
folder with strict checks (no absolute or `..` paths, links or special entries; at
most 20 MB, 2,000 entries and 100 MB unpacked). See
[ADR 006](docs/adr/006-import-from-outside.md).

```bash
captain-share --target /path/to/project import ~/Downloads/review-code.zip --source team
captain-share --target /path/to/project import ./notes --source team --kind steering \
  --name house-style.md --note "Add house style steering" --push --apply
```

With `select outbound` and `sync`, outbound copies go into the local library
checkout. Review that diff, run the library's checks, and commit normally. Push is available as an explicit, confirmed
UI action. Sharing never copies whole projects. It refuses nested symlinks, local/generated folders,
recognizable credentials, and personal absolute paths in selected content. Sharing,
importing and installing all refuse text with hidden characters (bidirectional
overrides, zero-width characters, Unicode tags), which can change what an agent reads
without showing in an editor; the message names the file, line and character. These
checks are heuristics, not a confidentiality or licensing certification. Review
what you select before sharing it with a broader audience.

A previously published copy can be updated only while it matches the tool's last
recorded version. Independent edits cause a conflict; edit or reconcile the shared
original. Identical pre-existing content is left untouched without claiming its
ownership. Deselecting outbound content stops sharing it; it does not delete the
library copy. To make the library the only maintained original, archive the old
local installation and receive the library version inbound.

## Check a library before sharing

```bash
captain-share check team --set "python3 scripts/check_library.py --refresh"
```

When a library has its own checks (allowed file types, a catalog, CI rules), set the
command once per machine (or in Settings). Sharing runs it after copying and before
committing; if it fails, nothing is committed and the window shows its output and
the command to run again. Files the check changes are committed with the asset.

## What is out of date?

```bash
captain-share outdated          # compares with the commits last fetched
captain-share outdated --fetch  # checks each library's server first
```

It lists linked assets with newer commits on the server, library edits not shared
yet, copies of older team versions, copies you changed, and kept versions whose team
original changed, each with what to do next. The window's Library view shows the
same list as "Behind the team", and Where installed has "Where from?" for each file.
See [ADR 011](docs/adr/011-outdated-report.md).

## Configuration scopes

1. `~/.captain/sharing.json`: global defaults.
2. Ancestor `.captain/sharing.json`: project-folder defaults, outer to inner.
3. Target `.captain/sharing.json`: repository/folder overrides.

Each level can have an ignored `sharing.local.json` for machine-local checkout
paths. `CAPTAIN_SHARE_HOME` overrides the global `.captain` directory for testing
or an independent setup. The deepest source/kind/name selection wins. A disabled
selection masks inheritance. Folder/global configuration does not automatically
modify descendants: each target is explicitly synced.

```bash
captain-share init --global
captain-share source add personal /path/to/library --scope global
captain-share --target /path/to/projects source add team /path/to/team-library --scope folder
captain-share --target /path/to/project config
# Install in your personal Kiro folder by choosing your home as the target:
captain-share --target "$HOME" select inbound personal skills review-code
captain-share --target "$HOME" sync --apply
```

Selections saved for your home folder are user-wide installs into `~/.kiro`,
`~/.claude`, or `~/.agents`, which agent tools already read in every project. Projects inherit the
home layer's sources and tools, but not its selections, so a user-wide skill is
not linked again into each project. Use an ancestor folder's `.captain` (for
example `~/Documents/PROJECTS`) for selections that projects should inherit.

Portable selections can be committed. Machine paths, installation records, and
archives are locally ignored. Git repositories also get exact local exclusions
for managed links; existing hooks are left alone. `guard` reports forcibly tracked
local paths. This is a convenience boundary, not access control or IP isolation.

See [configuration format](docs/configuration.md) for examples and [migration](docs/migration.md)
for existing Captain and toolkit installations.

## Where did this come from?

```bash
captain-share why ~/.kiro/skills/review/SKILL.md
```

`why` names the asset a file belongs to, the library original it links to, whether
Captain Share installed that link, and which `.captain/sharing.json` selected it. For
a copy it says whether it matches the library version; it also recognizes library
originals and powers installed by Kiro. It reads only.

## Remove, archive, restore

```bash
captain-share --target /path/to/project remove skills review
captain-share --target /path/to/project remove skills review --apply
captain-share --target /path/to/project archive
captain-share --target /path/to/project restore ARCHIVE_ID --apply
```

Removal archives the local file, directory, or symlink. For a managed link it also
disables the inbound selection, including inherited selections, and archives that
selection's links for every tool. `remove KIND NAME --path .claude/skills/NAME`
archives only that unmanaged local copy and keeps the selection, so the library
version can be received in its place. The shared original
is unaffected. Restore refuses occupied or Git-tracked destinations. Archives live
under the owning folder's `.captain/archive/`; replaced outbound copies are archived
in the **library**, so private originals are not moved into a consuming project.
The tool intentionally has no permanent-delete command.

Missing managed links are repaired by sync. Replaced/retargeted links are conflicts.
Apply recomputes the preview under locks and refuses stale plans. An explicit
`--target` must match a saved plan’s target. `status` exits nonzero when managed
links have drifted. Completed changes
are rolled back on handled failures; power loss or forced process termination can
require inspecting `status` and the archives manually. Live content is not pinned
to a Git revision, and the tool does not auto-fetch or clone sources.

## Management skill and Kiro CLI

The reusable [manage-shared-assets skill](captain_share/skills/manage-shared-assets/SKILL.md)
explains the workflow. Link or copy that complete directory into your chosen agent's
skill location after checking for an existing installation. The CLI must be on PATH
or invoked using this checkout's `bin/captain-share`.

```bash
captain-share --target /path/to/project assist "Help select the right shared skills"
# Explicitly start an interactive Kiro session with normal approval behavior:
captain-share --target /path/to/project assist "Help select the right shared skills" --run
```

The first form shows the handoff; it does not call a model. The tool does not add
blanket trust flags. Kiro can help interpret intent and content, while the CLI
performs the mechanical work.

## Web interface

Start it with `captain-share --target FOLDER serve --open`. It opens on **Library**;
**Share**, **Where installed** and **Settings** are the other pages. See
[ADR 005](docs/adr/005-library-home-and-sharing.md).

### First setup

With no library yet, the window asks three things once: which agents you use (Kiro,
Claude Code, Codex, Copilot), your team library's Git URL (cloned to
`~/Libraries/NAME`, or choose an existing checkout), and whether to take the
library's starter set (`profiles/starter.manifest`, if it has one).
It also lists libraries it finds on this machine (up to three folders below your
home folder) with an Add button, so an existing checkout needs no path. Settings
shows the same list. Watch the one-minute tour at `/tour` (linked from setup and
About).

The window follows your system's light or dark appearance; the switch in the top
bar chooses Auto, Light, or Dark for this browser.

### Library: get what you need

Library lists what is new since you last looked (with who shared it and when, from
the library's Git history), what you have, and everything else. Check for updates
fetches; Update library fast-forwards the checkout and marks new and changed
assets. Get asks where it should work, globally (all your projects) or only in this project,
shows the links it will create, and applies after Apply changes; Cancel puts the
choice back. Remove archives the links. Hover a row for Copy get command, a line a
teammate can run: `captain-share get NAME`.

### Install from a zip or folder

Library takes a skill that arrived without Captain Share: Install from a zip or
folder (a download, a colleague's folder). The archive is checked first (no paths
outside it, no links, size and file-count limits; nothing is run). You choose
Global or Only in this project; the skill goes into your own library
(`~/Libraries/my-skills`, a local Git repository that is never pushed) and is
linked like any library asset, so you can try it. When it proves useful, Share →
In your own library → Share with team copies it into the team library, commits and
pushes, switches your links to the team version, and moves it out of your own
library. See [ADR 006](docs/adr/006-import-from-outside.md) and
[ADR 008](docs/adr/008-personal-library.md).

### Share: pass a skill on

Share lists skills in this project that are not in a library, and library assets
you edited in place. Share… takes a short note ("what is it good for?"), shows the
copy and the commit, then copies the asset into the library, commits only that
asset's files with your note, and pushes if you leave Push checked. It ends with a
line to paste in chat. A refused push keeps the commit; push it later from
Settings.

With **Use the team version here afterwards** (on by default) the project then
swaps its own copy for a link to the library original: the copy is archived and
every agent you use links to the shared version. Skills that are already in a
library show **Use team version…** instead; if your copy differs, the review says so
and offers Replace mine (archived) or Keep mine. CLI: `captain-share adopt KIND NAME
--source S [--replace] [--apply]`, or `captain-share share … --adopt`.

To try a skill before sharing it, `captain-share install PATH --apply` adds a zip's
or folder's assets to your private library `my-skills` (`~/Libraries/my-skills`, a
local Git repository that is never pushed) and installs them for your user (`--here`
for the project). `captain-share promote KIND NAME --to TEAM --note TEXT --apply`
later moves one to a team library and relinks every place that used it
([ADR 008](docs/adr/008-personal-library.md)).

`captain-share source disconnect NAME` previews disconnecting a library from every
place (links archived, selections and bindings removed, the checkout folder kept);
`--apply` does it ([ADR 010](docs/adr/010-disconnect-library.md)).

Team libraries on protected or signed-only hosts work too
([ADR 009](docs/adr/009-git-hosts.md)): add `--review` to `share`, `import`, `promote`
or `starter` to commit on a new `share/NAME-USER` branch (the checkout is not
touched) and, with `--push`, open a merge request. Git failures come with a hint
and, where it helps, the exact command to run (for example switching an HTTPS
remote to SSH). Signing follows your Git settings; a library can also opt in to
signed commits in Settings.

A library's starter set (`profiles/starter.manifest`, offered to new teammates) is
edited with `captain-share starter SOURCE` (lists it) and `--set kind/name,…`,
`--add kind/name`, `--remove kind/name` or `--clear`, previewed as a diff and, with
`--note TEXT --apply [--push]`, committed alone.

When your copy extends the team version, choose what it is
([ADR 007](docs/adr/007-extended-copies.md)). `captain-share diff KIND NAME --source S`
shows the changes file by file. `propose KIND NAME --source S --note TEXT --apply`
sends them to the team: directly (commit only that asset, `--push`, then link the
project to the library version), or with `--review` as a commit on a new
`share/NAME-USER` branch built without touching the library checkout, pushed with
`--push` and returned with a GitHub/GitLab review link when the host is recognised.
`keep KIND NAME --source S --as NEWNAME --apply` keeps your copy as your own version
under a new name (the skill's `name:` is updated, the old folder archived, the team
version linked unless `--no-team`), and `captain-share versions` reports when the
team version changed since.

### Where installed: one grid for every place

The grid is for maintenance. Rows are library assets, grouped by library, with each
asset's description. Columns are places: this project, the folders above it inside
your home folder, and Global (your home folder; installs into `~/.kiro`, `~/.claude` and
`~/.agents` for every project). Click a circle to install an asset in that place or
remove it. A folder shares its assets with every project inside it; a line from
the folder's circle marks each project that gets the asset from it, and a dashed
circle marks an asset already installed globally, so each skill lives in one
place. Only the nearest folder is shown until another folder has selections;
"+N folders above" shows the rest. A one-line key under the grid explains each state.

Each column head names the agents that get links in that place (for example "Kiro,
Copilot"); click it to switch Kiro, Claude, Codex or Copilot on or off there. Kiro powers install globally
only and show Kiro's install state.

Changes wait in the bar at the bottom ("Add reviewer.json to Global"); status
messages appear just above it and never cover it. Review
and apply saves the choices and opens one review for every place they affect,
including registered projects inside a changed folder. Apply changes creates the
links; Cancel puts the saved choices back as they were. See
[ADR 004](docs/adr/004-web-places.md).

Under Where installed, Projects lists this project and registered ones with their sync state.
Add project… registers a folder, Preview sync previews one project, and Preview
sync for all previews every project before one Apply.

### Conflicts, local files, and powers

A review with conflicts applies nothing in that place. Conflicts in the window's
own place offer a fix: Archive local copy previews archiving the unmanaged file and,
once applied, previews the original changes again; Stop receiving deselects the
asset. A circle with a red stroke marks a file in the way. Local files lists files
in agent folders that Captain Share does not manage, each with Archive; Archive
restores what was archived.

The review names the exact `kiro-cli` command before Apply runs it for a Kiro
power.

### Adding a library

Add a library folder starts with Choose library folder…. The picker opens one
level above the project and marks folders that contain `ip/` or asset folders as
library. After a folder is chosen (or a path typed), the card shows what it found,
for example "Found 39 skills, 3 prompts under ip/", fills the name from the folder
name (editable), and detects the layout. Choosing a library's `ip/` folder selects
the library itself. Add library is refused for folders without asset folders and for
folders already added, with the reason shown. "Make it available to" chooses every
project (Global) or this project only. Clone and add fills the library name from
the repository URL.

### Git, cloning, and folders

Clone and connect accepts an HTTPS/SSH URL (or absolute local Git repository path)
and a new absolute checkout path with an existing parent. Authentication uses your
existing Git setup, with interactive credential prompts disabled. It does not
clone submodules. A successful clone remains on disk if registration fails.

Repository status switches between the selected library and current project's
Git root. It lists changed/untracked files, branch, and ahead/behind counts from
the last fetch. Pull and Push show their destination for confirmation. Pull uses
fast-forward only and requires a clean working tree. Pulling a shared library
updates its live linked assets immediately. Push sends the current committed HEAD
to the displayed branch without force; commit local file changes in your Git client
first. A changed repository invalidates the pending confirmation. These actions
use the existing Git hooks. They do not automatically resolve conflicts or commit.

Folder fields include a local folder picker with Up, Home, Current folder, and
Show hidden folders. For cloning, select a parent and enter the new checkout
folder name. Browsing or cancelling does not create folders or change connections.
The picker lists directories on the computer running Captain Share, using the
same authenticated localhost API as the rest of the interface.

### Archive and About

The About page in the web header includes the running version, quick start, asset
formats, configuration scopes, repository controls, troubleshooting, and the MIT
license. Switching between About and Workspace preserves unsaved selections.

Archive lists assets still available to restore. Completed restores appear in a
collapsed Restore history, grouped by local path with readable restore dates.
Show in Finder reveals workspace files, shared originals, and archive payloads on
macOS; on Linux it opens the containing folder in the desktop file manager.

## Development

Development needs Python 3.9+, Git and Node 22+; installed CLI/web use still requires
only Python and Git.

```bash
make bootstrap
make verify-fast
make verify
```

See [contributing](CONTRIBUTING.md) for formatting and targeted tests, and
[readiness evidence](docs/readiness.md) for the checks adapted from
[ai-ready-repo](https://github.com/vidanov/ai-ready-repo). Tests use disposable
repositories and loopback servers. See [design and boundaries](docs/design.md).
MIT licensed.
