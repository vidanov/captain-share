# Configuration format, version 1

`.captain/sharing.json` contains portable choices:

```json
{
  "version": 1,
  "tools": ["kiro", "claude", "codex", "copilot"],
  "sources": {"team": {"description": "Shared team tooling"}},
  "inbound": {
    "team/skills/review-code": {
      "source": "team", "kind": "skills", "name": "review-code", "alias": "review"
    }
  },
  "outbound": {
    "team/prompts/summary.md": {
      "source": "team", "kind": "prompts", "name": "summary.md", "path": "prompts/summary.md"
    }
  }
}
```

Machine-local `.captain/sharing.local.json` binds names to checkouts:

```json
{
  "version": 1,
  "sources": {"team": {"path": "/path/to/team-library", "layout": "ip"}}
}
```

Library-only kinds such as `powers` cannot be inbound selections; Kiro installs
them user-wide (see the README).

`tools` is optional and lists the agent tools that receive inbound links: `kiro`,
`claude`, `codex`, `copilot`. The deepest layer that sets it replaces inherited lists; without
it the default is `["kiro", "copilot"]`. It belongs only in portable `sharing.json`.
Installation state records the tool of every managed link.

Relative checkout paths resolve against the folder containing `.captain`.
Outbound paths always resolve against the target being synced; inherited selections
therefore work as folder templates. Absolute paths and `..` are rejected there.
Unknown versions, fields, asset kinds, and invalid names are rejected.

The global folder (`~/.captain`, the home target's own `.captain`) is special:
its sources, bindings, and tools are inherited by every target, but its inbound and
outbound selections apply only to the home target. Those are user-wide installs
into `~/.kiro` or `~/.claude`.

To exclude one inherited selection, save the same key with `{"enabled": false}`.
The CLI `deselect` writes this mask. To resume inheritance, remove the local override
from `sharing.json`; selecting the asset explicitly writes an explicit local choice.
Removing a source with `source remove` disables that source at the chosen level;
existing selections then report an unbound source until you deselect or rebind them.
Source removal alone does not uninstall assets.

`.captain/sharing-state.json` records only this tool's installations/publications.
It is machine-local, tied to one canonical target path, and is never copied as a
project template. `.captain/archive/ID/record.json` records a recoverable original
and `payload` stores it. Saved preview JSON also contains local paths; keep it outside
tracked content, for example in your temporary directory.

`repo add NAME PATH` and `repo list` maintain a machine-local directory of targets
at the global `repositories.local.json`. Registering a repo does not install anything.
The UI shows the target and contributing config layers; advanced scope settings and
imports use the CLI. Both interfaces operate on the same configuration.
