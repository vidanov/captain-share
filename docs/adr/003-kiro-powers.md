# ADR 003: Kiro powers are shared through libraries and installed by Kiro

Status: Accepted

## Context

A Kiro power is a folder with either an Agent Plugins 1.0.0 `plugin.json` (current
format) or a legacy `POWER.md`, plus optional `skills/`, `mcp.json`, and (legacy)
`steering/`. Teams want to keep powers beside their skills and install the same
versions everywhere.

Observed with `kiro-cli` 2.29.1 in a disposable `HOME`:

- `kiro-cli powers install PATH` copies the folder to
  `~/.kiro/powers/installed/NAME/` and records only `NAME` in
  `~/.kiro/powers/installed.json`. Installing again replaces the copy.
- `NAME` comes from `plugin.json`, not the folder name, so installing another
  folder with the same manifest name silently replaces an installed power. A legacy
  `POWER.md` power takes the folder name.
- `kiro-cli powers uninstall NAME` removes the copy.

Installation is user-wide and copied, so a live per-project link is impossible, and
the installed folder and ledger belong to Kiro.

## Decision

`powers` is a library kind (`ip/powers/NAME/`). It can be listed, validated and
contributed like other kinds, but it has no project destination and cannot be
selected inbound.

Validation requires `plugin.json` with `name`, `version`, `description`,
`author.name` and `keywords`, or a `POWER.md` with frontmatter. The manifest name
must equal the folder name, which prevents the silent replacement above. An
`mcp.json`, when present, must contain an `mcpServers` object. Outbound
contribution also requires every MCP `env` value to be a `${VAR}` reference.

Installing, updating and uninstalling call Kiro's own installer
(`kiro-cli powers install|uninstall`) from `integrations.py`, after a preview that
names the exact command. Captain Share never writes `~/.kiro/powers`. It reads the
installed copy to report `not installed`, `current` (same content as the library),
or `differs` (an update is available or the installed copy came from elsewhere).
Applying rechecks the preview fingerprint, as file plans do.

A power can start programs on the user's computer: MCP servers (`mcp.json`,
`.mcp.json`, or `mcpServers` in `plugin.json`) and hooks (`hooks` in `plugin.json`,
`hooks.json`, `*.kiro.hook`, anything under `hooks/`). The install or update preview
lists each one with what it runs, and is a conflict until the user allows it
(`--allow-code` on `powers install` and `get`, or "I trust these" in the window).
The approval belongs to that preview's fingerprint, so a changed power needs a new
one. Uninstalling needs none.

## Consequences

One library holds skills and powers. Library edits reach Kiro only after an
explicit update, unlike live skill links. Uninstalling relies on Kiro's own
removal, so it is not archived by Captain Share. Whether Kiro IDE lists a power
installed by `kiro-cli` was not verified here; both use `~/.kiro/powers`. Claude
Code plugins use `.claude-plugin/plugin.json`, not Agent Plugins, and are out of
scope.
