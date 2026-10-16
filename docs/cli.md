# Command reference

Every command works on a **target** folder: the current folder, or `--target
FOLDER`. Put `--target` and `--json` before the command. Commands that change files
preview first and change nothing without `--apply` (or a yes at the prompt). Global
and ancestor settings are inherited into the target; the exact folder is the
target, even inside a larger Git repository.

The window offers the same actions; see [the window guide](web-interface.md).

## Receive assets

A library can use `ip/skills/`, `ip/agents/`, etc. (`--layout ip`), or
`skills/`, `agents/`, etc. at its root (`--layout flat`); without `--layout` the
layout is detected from the folder. Clone it normally with
your own Git credentials (for AWS CodeCommit with `git-remote-codecommit`, the part
before `@` in `codecommit::REGION://PROFILE@REPO` is your own AWS profile name from
`~/.aws/config`), then register the local checkout:

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

`apply` takes a saved plan file. To preview and apply the current selections in one
step, run `captain-share --target /path/to/project sync --apply` (Preview and
Apply in the window do the same).

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

### Agents and formats

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
See [ADR 002](adr/002-agent-tool-destinations.md).

Steering and prompts stay in Kiro's format in the library. For Claude Code they are
converted: always-included steering becomes a rule, `fileMatch` steering a rule with
`paths:` for the same globs, and prompt arguments are renumbered for commands.
Claude's folders link to these generated files, which every preview checks and
regenerates when the library changes. Manual steering becomes a Claude skill you
call as `/name`, auto steering a skill Claude loads when its description fits, and
agents that list their tools become Claude subagents; the preview lists what a
conversion leaves out ([ADR 013](adr/013-claude-skills-and-subagents.md)). A
generated file is archived with the last link to it; `captain-share rendered` lists
them and `--clean` archives any nothing links. Claude rules and commands that no
library has can be shared too; they are converted to Kiro's format on the way in
([ADR 014](adr/014-share-claude-files-back.md)). See
[ADR 012](adr/012-claude-rules-and-commands.md).

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
`PATH`. See [ADR 003](adr/003-kiro-powers.md).

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
[ADR 006](adr/006-import-from-outside.md).

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

## Get skills from any Git repository

```bash
captain-share add mattpocock/skills                 # lists the skills in it
captain-share add mattpocock/skills --skill tdd     # preview, then install
captain-share add https://github.com/OWNER/REPO --skill a --skill b --apply
```

Like `npx skills add`: the chosen skills are copied into your own library
(`~/Libraries/my-skills`, only on this machine) and installed for you (`--here`
for the project). The commit records where they came from. In the window, use
Library → From Git…. Read a skill from outside before you use it: it can run
commands.

## Your own library

To try a skill before sharing it, `captain-share install PATH --apply` adds a zip's
or folder's assets to your private library `my-skills` (`~/Libraries/my-skills`, a
local Git repository that is never pushed) and installs them for your user (`--here`
for the project). `captain-share promote KIND NAME --to TEAM --note TEXT --apply`
later moves one to a team library and relinks every place that used it
([ADR 008](adr/008-personal-library.md)).

## Protected hosts, reviews and the starter set

Team libraries on protected or signed-only hosts work too
([ADR 009](adr/009-git-hosts.md)): add `--review` to `share`, `import`, `promote`
or `starter` to commit on a new `share/NAME-USER` branch (the checkout is not
touched) and, with `--push`, open a merge request. Git failures come with a hint
and, where it helps, the exact command to run (for example switching an HTTPS
remote to SSH). Signing follows your Git settings; a library can also opt in to
signed commits in Settings.

A library's starter set (`profiles/starter.manifest`, offered to new teammates) is
edited with `captain-share starter SOURCE` (lists it) and `--set kind/name,…`,
`--add kind/name`, `--remove kind/name` or `--clear`, previewed as a diff and, with
`--note TEXT --apply [--push]`, committed alone. Add `--profile NAME` to do the same
for another named profile (`profiles/NAME.manifest`); `captain-share profiles SOURCE`
lists every profile a library offers.

## Copies that extend the team version

When your copy extends the team version, choose what it is
([ADR 007](adr/007-extended-copies.md)). `captain-share diff KIND NAME --source S`
shows the changes file by file. `propose KIND NAME --source S --note TEXT --apply`
sends them to the team: directly (commit only that asset, `--push`, then link the
project to the library version), or with `--review` as a commit on a new
`share/NAME-USER` branch built without touching the library checkout, pushed with
`--push` and returned with a GitHub/GitLab review link when the host is recognised.
`keep KIND NAME --source S --as NEWNAME --apply` keeps your copy as your own version
under a new name (the skill's `name:` is updated, the old folder archived, the team
version linked unless `--no-team`), and `captain-share versions` reports when the
team version changed since.

## Profiles and the starter set

A library can offer named sets in `profiles/NAME.manifest` (for example
`skills: plan review`). The starter set is `profiles/starter.manifest`, offered in
first setup. In the window, Settings → a library's Options → Profiles lists them,
gets one globally or in this project, and edits or adds one.

```bash
captain-share --target /path/to/project import-profile team pilot          # preview
captain-share --target /path/to/project import-profile team pilot --apply  # save the selections
captain-share --target /path/to/project sync --apply                       # create the links
```

`choose inbound --source team` picks interactively instead. Toolkit manifests from
an existing Captain installation import the same way; see [migration](migration.md).

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
See [ADR 011](adr/011-outdated-report.md).

## Where did this come from?

```bash
captain-share why ~/.kiro/skills/review/SKILL.md
```

`why` names the asset a file belongs to, the library original it links to, whether
Captain Share installed that link, and which `.captain/sharing.json` selected it. For
a copy it says whether it matches the library version; it also recognizes library
originals and powers installed by Kiro. It reads only.

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

See [configuration format](configuration.md) for examples and [migration](migration.md)
for existing Captain and toolkit installations.

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

## Disconnect a library

`captain-share source disconnect NAME` previews disconnecting a library from every
place (links archived, selections and bindings removed, the checkout folder kept);
`--apply` does it ([ADR 010](adr/010-disconnect-library.md)).

## Update Captain Share

```bash
captain-share update          # version, how it is installed, what is new
captain-share update --apply  # update the same way it was installed
```

A checkout is fast-forwarded only when it has no local changes; a pip install is
reinstalled from its source. Restart open windows afterwards (the window's About
offers Restart window). See [ADR 015](adr/015-update-captain-share.md).

## The agent skill and Kiro CLI

```bash
captain-share skill                  # is it installed?
captain-share skill install          # preview; --apply links it for your agents
captain-share skill install --here   # for this project only
captain-share skill remove --apply
```

The [captain-share skill](../captain_share/skills/captain-share/SKILL.md) teaches
agents to use these commands safely. It is linked from your installation through a
built-in library (hidden from library lists), so updates reach it
([ADR 017](adr/017-agent-skill.md)).

```bash
captain-share --target /path/to/project assist "Help select the right shared skills"
# Explicitly start an interactive Kiro session with normal approval behavior:
captain-share --target /path/to/project assist "Help select the right shared skills" --run
```

The first form shows the handoff; it does not call a model. The tool does not add
blanket trust flags. Kiro can help interpret intent and content, while the CLI
performs the mechanical work.
