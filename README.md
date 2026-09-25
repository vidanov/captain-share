# Captain Share

A standalone, local tool for sharing skills, agents, prompts, and steering between
independent repositories. Use a CLI, a small web interface, or the bundled
management skill. Keep originals in libraries with their own access rights; each
project chooses what it receives and contributes.

The public tool contains the sharing mechanics. Your libraries and their private
content stay in separate repositories. Captain is not a runtime dependency.

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
per-launch access token. The UI supports connecting a checkout, choosing inbound
and outbound assets, setting aliases, previewing/applying changes, and archiving
and restoring local assets. Each server is tied to the target folder it was started
for; start another instance for another project.

## Receive assets

A library can use `ip/skills/`, `ip/agents/`, etc. (`--layout ip`, default), or
`skills/`, `agents/`, etc. at its root (`--layout flat`). Clone it normally with
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

Inbound assets are live symlinks. Editing the shared original changes every linked
project. Aliases change the local directory/file name, not skill frontmatter or
agent resource references. Select dependencies referenced by an agent alongside
that agent; this release does not automatically resolve those references.

Supported kinds: `skills`, `agents`, `prompts`, `steering`, `copilot`,
`copilot-instructions`, and `copilot-prompts`. Kiro destinations are under `.kiro/`;
Copilot destinations are under `.github/`. Skill folders require `SKILL.md`, agents
use `.json`, and prompts/steering use `.md`. Installed files still need validation
in the consuming CLI/IDE; placement does not prove activation or discovery.

## Contribute assets

```bash
captain-share --target /path/to/project scan
captain-share --target /path/to/project select outbound team skills review-code --path skills/review-code
captain-share --target /path/to/project plan --direction outbound
captain-share --target /path/to/project sync --direction outbound --apply
```

Outbound copies go into the local library checkout. Review that diff, run the
library's checks, and commit normally. Push is available as an explicit, confirmed
UI action. Sharing never copies whole projects. It refuses nested symlinks, local/generated folders,
recognizable credentials, and personal absolute paths in selected content. These
checks are heuristics, not a confidentiality or licensing certification. Review
what you select before sharing it with a broader audience.

A previously published copy can be updated only while it matches the tool's last
recorded version. Independent edits cause a conflict; edit or reconcile the shared
original. Identical pre-existing content is left untouched without claiming its
ownership. Deselecting outbound content stops sharing it; it does not delete the
library copy. To make the library the only maintained original, archive the old
local installation and receive the library version inbound.

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

Portable selections can be committed. Machine paths, installation records, and
archives are locally ignored. Git repositories also get exact local exclusions
for managed links; existing hooks are left alone. `guard` reports forcibly tracked
local paths. This is a convenience boundary, not access control or IP isolation.

See [configuration format](docs/configuration.md) for examples and [migration](docs/migration.md)
for existing Captain and toolkit installations.

## Remove, archive, restore

```bash
captain-share --target /path/to/project remove skills review
captain-share --target /path/to/project remove skills review --apply
captain-share --target /path/to/project archive
captain-share --target /path/to/project restore ARCHIVE_ID --apply
```

Removal archives the local file, directory, or symlink. For a managed link it also
disables the inbound selection, including inherited selections. The shared original
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

Housekeeping files (`.DS_Store`, `.gitkeep`) are hidden from discovery and
excluded from shared copies and content comparisons, including inside skills.
Unrelated file types and prompt subfolders are omitted from the asset list.
Original files remain untouched; unsafe content such as `.env` still blocks sharing.

### Bulk install and Git in the web interface

Select all / Deselect all affect every eligible asset in the chosen library and
view, including search results currently hidden. Existing aliases are preserved.
Install full pack selects every available asset in the library and opens the
normal preview; Apply changes installs it. Invalid assets are skipped. Selection
is a snapshot: new assets added to a library later require selection again.

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

The About page in the web header includes the running version, quick start, asset
formats, configuration scopes, repository controls, troubleshooting, and the MIT
license. Switching between About and Workspace preserves unsaved selections.

Archive lists assets still available to restore. Completed restores appear in a
collapsed Restore history, grouped by local path with readable restore dates.
Show in Finder reveals workspace files, shared originals, and archive payloads on
macOS; on Linux it opens the containing folder in the desktop file manager.
