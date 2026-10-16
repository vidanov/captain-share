# The Captain Share window

Start it with `captain-share serve --open` (the current folder) or
`captain-share --target FOLDER serve --open`. The server binds only to `127.0.0.1`,
picks a free port and prints a link with a per-launch token. A window belongs to
the folder it was started for; `serve` for a folder whose window is already open
opens that window (`--new` starts another). It opens on **Library**; **Share**,
**Where installed** and **Settings** are the other pages, and **About** has the
version, updates and a glossary. See [ADR 005](adr/005-library-home-and-sharing.md).

The window follows your system's light or dark appearance; the switch in the top
bar chooses Auto, Light or Dark for this browser. Compact view (one line per row,
details behind the row's arrow) is the default; Detailed view is remembered once
chosen.

## First setup

With no library yet, the window asks three things once: which agents you use (Kiro,
Claude Code, Codex, Copilot), your team library's Git address (cloned into
`~/Libraries/NAME`, or choose an existing checkout), and whether to take the
library's starter set (`profiles/starter.manifest`, if it has one). It also lists
libraries it finds on this machine with an Add button. The one-minute tour is at
`/tour`.

## Library: get what you need

Library lists each connected library on one line (up to date, updates waiting, or
new since you last looked), then the assets: what is new (with who shared it and
when, from Git history), what you have, and everything else. Search and the type
filters (Skills, Agents, Prompts, Steering, Powers) narrow the lists.

- **Check for updates** fetches; **Update library** fast-forwards the checkout and
  marks new and changed assets. The last check is remembered for six hours.
- **Get** asks where the asset should work, Global (all your projects) or only in
  this project, shows the links it will create, and applies after **Apply
  changes**; Cancel puts the choice back. **Remove** archives the links.
- **Preview** shows an asset's files and main text; hovering a row offers **Copy get
  command** (`captain-share get NAME`) for a teammate.
- **Behind the team** lists installs with newer versions on the server, library
  edits not shared yet, copies of an older team version (with **Update to team
  version**) and kept versions the team changed since ([ADR 011](adr/011-outdated-report.md)).

### Install from a zip, folder or Git

For a skill that arrived without Captain Share: **Upload a zip…**, **Choose a
folder…** or **From Git…** (an address or `OWNER/REPO`, downloaded with your Git
access). The content is checked first (no paths outside it, no links, size limits,
no hidden characters; nothing is run). With many candidates nothing is picked and a
filter helps. You choose Global or Only in this project; the skill goes into your
own library (`~/Libraries/my-skills`, a local Git repository that is never pushed)
and is linked like any library asset, so you can try it. When it proves useful,
Share → In your own library → **Share with team** moves it to the team library and
switches your links. See [ADR 006](adr/006-import-from-outside.md) and
[ADR 008](adr/008-personal-library.md).

## Share: pass a skill on

Share lists, with search and type filters:

- skills in this project and in your global folders (`~/.kiro`, `~/.claude`) that
  no library has, with **Preview** and **Show in Finder**;
- assets in your own library;
- library assets you edited in place;
- copies of team assets: an older team version (**Update to team version**), or
  your extension of it (**Send my changes**, **Keep as my version**, **Use team
  version**, **Show changes**; [ADR 007](adr/007-extended-copies.md)).

**Share…** takes a short note ("what is it good for?"), shows the copy, the commit
and the links, then copies the asset into the library, commits only its files with
your note, and pushes if Push is checked. The result says where the commit landed
("Commit a94e79c in team on main · pushed"), and gives you a line to paste in chat.
With **Use the team version here afterwards** (on by default) your copy is archived
and every agent links to the shared version. **Ask for review** pushes a new branch
and opens a merge request instead, for protected branches
([ADR 009](adr/009-git-hosts.md)). A refused push keeps the commit. A Claude rule or
command is converted to Kiro's format on the way in ([ADR 014](adr/014-share-claude-files-back.md)).

If a library has a check before sharing (Settings), it runs after the copy and
before the commit; when it fails, nothing is committed and you see its output and
the command to run again.

## Where installed: one grid for every place

The grid is for maintenance. Rows are library assets grouped by library; columns
are places: this project, the folders above it inside your home folder, and Global.
Click a circle to install an asset there or remove it. A folder shares its assets
with every project inside it; a dashed circle marks an asset already installed
globally. Each column head names the agents that get links in that place; click it
to switch them. Changes wait in the bar at the bottom; **Review and apply** opens
one review for every affected place. See [ADR 004](adr/004-web-places.md).

**Projects** lists this project and registered ones with their sync state. **Local
files** lists files in agent folders that Captain Share does not manage, each with
Archive and **Where from?** (which library and asset a file came from).
**Archive** restores what was archived.

A review with conflicts applies nothing in that place. Conflicts in the window's
own place offer a fix, for example **Archive local copy** for a file in the way.
Kiro powers install globally through `kiro-cli`; a power that brings MCP servers or
hooks lists them and needs **I trust these, allow them** first
([ADR 003](adr/003-kiro-powers.md)).

## Settings

- **Agents you use**: Kiro, Claude Code, Codex, Copilot.
- **Libraries**: each connected library with Show in Finder and Disconnect…
  ([ADR 010](adr/010-disconnect-library.md)). A team library's **Options** hold
  signing, the check before sharing, and **Profiles**: the named sets the library
  offers (`profiles/NAME.manifest`), the starter set for new teammates first. Get
  one globally or in this project (reviewed like any Get), or edit or add one;
  saving commits only that manifest, directly or for review.
  "New libraries go to …" sets the Libraries folder (`~/Libraries` by default).
- **Add a library folder** finds an existing checkout and its layout.
- **Clone a library from Git** needs only the address (`OWNER/REPO` means GitHub):
  it becomes `OWNER-REPO` in the Libraries folder; name, folder and layout can be
  changed ([ADR 016](adr/016-clone-by-address-and-category-folders.md)).
- **Advanced**: repository status with fast-forward pull and push, and the
  configuration layers in effect.

## About

About shows the version and how Captain Share is installed. **Check for updates**
lists what is new (from the changelog), **Update Captain Share** updates it the way
it was installed, and **Restart window** starts the window again on the same link,
also after an update from the command line or a `git pull`
([ADR 015](adr/015-update-captain-share.md)). The glossary explains the words used
in the window.
