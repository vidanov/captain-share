# Captain Share

**Steer once, every agent follows.** Your team keeps agent skills, prompts, steering
and agents in one Git library. Captain Share links them into Kiro, Claude Code,
Codex and GitHub Copilot, for every project on your machine or just one, and shares
what you make back to the team. It runs only on your computer; nothing leaves it
except your own Git pushes.

[![Captain Share - steer once, every agent follows (video)](https://img.youtube.com/vi/EIDyhAD57N8/hqdefault.jpg)](https://www.youtube.com/watch?v=EIDyhAD57N8)

▶ [Watch the video](https://www.youtube.com/watch?v=EIDyhAD57N8), or open the
one-minute tour in the window (`/tour`, linked from setup and About).

## What it does

- **Get** a skill a colleague recommends with one click or `captain-share get NAME`:
  it is linked into every agent you use, globally or for one project.
- **Share** a skill you made: Captain Share copies it into the team library, commits
  only that skill with your note, pushes, and gives you a line to paste in chat.
- **One original, every agent.** Kiro steering becomes Claude Code rules and skills,
  prompts become commands, agents become subagents, generated from the same source.
- **Stay current.** See what is new since you last looked, what is behind the team
  version, and where each file came from.
- **Stay in control.** Every change is previewed first; nothing is deleted (removed
  files go to an archive you can restore from); hidden characters, credentials and
  code-running powers are caught before they reach you or the team.

## Why not another tool?

`npx skills`, skillshare, Microsoft APM and Claude Code plugin marketplaces each cover
part of this. What only Captain Share does, as of October 2026:

- **Kiro steering, prompts, agents and powers**, with Claude Code rules, commands,
  skills and subagents generated from the same originals. The others handle skills
  only, leave Kiro out, or need their own package layout.
- **Sharing back in one step**: one small commit with your note, signed if required,
  or a review branch with a merge request. The others leave this to manual Git.
- **Your repository as it is**: no package format to adopt; existing team libraries
  and public skill repositories work unchanged.

If you only use Claude Code, a plugin marketplace may be enough; if you only consume
public skills, `npx skills` is quick. The full comparison, with sources, is in
[docs/comparison.md](docs/comparison.md).

## Install and start

Python 3.9+, Git, and macOS or Linux. No other runtime packages.

```bash
pipx install git+https://github.com/vidanov/captain-share
captain-share serve --open
```

`serve` opens the window for the current folder (`--target FOLDER` for another). If
a window for that folder is already open, it opens that one. To update later, use
About → **Update Captain Share** in the window, or `captain-share update --apply`;
**Restart window** then keeps the same link. What changed is in
[CHANGELOG.md](CHANGELOG.md).

<details>
<summary>Run from a checkout instead</summary>

```bash
git clone https://github.com/vidanov/captain-share
./captain-share/bin/captain-share serve --open
```

Or install into a virtual environment with `python3 -m venv .venv && .venv/bin/pip
install .`. A checkout updates itself the same way (a fast-forward `git pull`, only
when it has no local changes).
</details>

## The window

The first start asks three things once: which agents you use, your team library's
Git address (cloned into `~/Libraries`), and whether to take the library's starter
set. After that the window has four pages:

- **Library**: what is new since you last looked, what you have, and everything
  else, with Get or Remove on each. **Behind the team** lists copies and installs
  older than the team version. **Install from a zip, folder or Git** takes a skill
  that arrived without Captain Share (or `OWNER/REPO` from GitHub) into your own
  library to try it first.
- **Share**: skills in this project or your global folders that no library has yet,
  library assets you edited, and copies that differ from the team version. Preview,
  then Share; a protected branch gets a merge request instead.
- **Where installed**: one grid of every asset and every place (this project, the
  folders above it, Global), for maintenance.
- **Settings**: your agents, your libraries (clone one by pasting its address),
  where new libraries go, signing, and a library's own check before sharing.

Search and type filters (Skills, Agents, Prompts, Steering, Powers) work on Library
and Share; compact view is the default. The full guide is in
[docs/web-interface.md](docs/web-interface.md).

## Command line

Everything the window does is also a command. The ones you will use most:

```bash
captain-share get review-code                    # get one asset for your user (--here: this project)
captain-share add mattpocock/skills --skill tdd  # like npx skills add: from any Git repository
captain-share share skills review-code --source team --note "Add review-code" --push --apply
captain-share install ~/Downloads/skill.zip --apply   # try it in your own library first
captain-share outdated --fetch                   # what is behind the team version
captain-share why ~/.kiro/skills/review          # where a file came from
captain-share update                             # what is new in Captain Share itself
```

Put `--target FOLDER` and `--json` before the command. Every command that changes
files previews first and changes nothing without `--apply` (or your yes). The full
reference is in [docs/cli.md](docs/cli.md).

## How it works

**Libraries** are ordinary Git repositories with assets in `ip/skills/`,
`ip/agents/`, … or `skills/`, `agents/`, … at the root; skills may also sit in
category folders (`skills/engineering/tdd/`), as in many public repositories. You
clone them with your own Git access; Captain Share never stores credentials. Your
own library `~/Libraries/my-skills` is local and never pushed.

**Links, not copies.** An installed asset is a symlink to the library original, so
Update library reaches every project at once. Where another agent needs another
format, Captain Share generates it from the original and links that instead:

| Kind | Kiro | Claude Code | Codex | Copilot |
| --- | --- | --- | --- | --- |
| skills | `.kiro/skills/` | `.claude/skills/` | `.agents/skills/` | |
| steering | `.kiro/steering/` | `.claude/rules/` or `.claude/skills/` (generated) | | |
| prompts | `.kiro/prompts/` | `.claude/commands/` (generated) | | |
| agents | `.kiro/agents/` | `.claude/agents/` (generated) | | |
| copilot | | | | `.github/…` |
| powers | `~/.kiro/powers/` via `kiro-cli` | | | |

**Places.** Install globally (your home folder: every project sees it), for a folder
of projects, or for one project. Settings are layered the same way in `.captain/`
folders: portable choices in `sharing.json`, machine paths in the ignored
`sharing.local.json`. See [agents and projects](docs/agents-and-projects.md) and the
[configuration format](docs/configuration.md).

**Safety.** Every change is a preview first and is applied under locks with a
freshness check and rollback. Nothing is deleted: replaced and removed files go to
an archive you can restore. Sharing commits only the shared asset's files, never
forces a push, refuses hidden characters, credentials and personal paths, can run
the library's own check first, and keeps a commit whose signature does not verify
local. A Kiro power that would start MCP servers or hooks needs your explicit
approval. These are safeguards, not a confidentiality or licensing review.

## More

- [Web interface guide](docs/web-interface.md) and [command reference](docs/cli.md)
- [How Captain Share compares](docs/comparison.md) with `npx skills`, skillshare, APM and plugin marketplaces
- [Agents and projects](docs/agents-and-projects.md): who reads what, global vs project
- [Configuration format](docs/configuration.md), [migration from Captain](docs/migration.md)
- [Design and boundaries](docs/design.md) and the decision records in [docs/adr](docs/adr/)
- The bundled [management skill](captain_share/skills/manage-shared-assets/SKILL.md)
  lets an agent run Captain Share for you

## Development

Development needs Python 3.9+, Git and Node 22+ (for formatting); using Captain
Share needs only Python and Git.

```bash
make bootstrap
make verify-fast
make verify
```

See [contributing](CONTRIBUTING.md) and [readiness evidence](docs/readiness.md).
Tests use disposable repositories and loopback servers. MIT licensed.
