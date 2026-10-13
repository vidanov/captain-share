# ADR 004: One web window, several places

Status: Accepted

## Context

A web window was tied to the folder it started for. Installing user-wide, saving a
baseline for a folder of projects, or syncing several projects after a library
update meant restarting `serve` once per folder, which few people did.

## Decision

A window acts on a fixed set of places, computed by the server on every request:

- the startup folder;
- each folder above it up to, but not including, the home folder (templates that
  projects inside inherit);
- the home folder (user-wide installs into `~/.kiro` and `~/.claude`);
- projects the user registered (`captain-share repo add` or Add project…).

Every API request names its place (`target`); any other path is refused. Plans are
created by the server for that place and keep their fingerprints and locks, so
apply semantics are unchanged. A batch preview keeps several projects' plans until
they are applied. Revealing files in the file manager stays limited to the current
place and its libraries.

The state reports, per selection, which folder's configuration provides it, and
which assets are already installed for the user, so a project does not install a
user-wide skill a second time. A folder place is a template: it shows no link
destinations of its own, and saving there previews the registered projects inside
it rather than linking into the folder.

## Consequences

One window can change more folders than before, but only ones the user started it
in, sits under, or registered. Registration is an explicit action. There is still no
background sync: every change is previewed and applied by the user.
