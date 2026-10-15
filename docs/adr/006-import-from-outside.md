# ADR 006: Add assets from outside (zip or folder)

Status: Accepted

## Context

Skills also arrive without Captain Share: a zip downloaded from a website, a folder a
colleague sent, a checkout of someone else's repository. Copying them into a library
by hand skips validation and leaves no record of who added them and why.

## Decision

The window and the CLI can **add assets from a zip file or a folder** into a
connected library, through the same preview, copy, commit and optional push used for
sharing (ADR 005).

- **Staging.** A folder is read in place. A zip is uploaded as raw bytes to a
  dedicated endpoint (authenticated like every API call, at most 20 MB) and extracted
  into a private temporary folder owned by the server session, removed when the
  server stops.
- **Untrusted archives.** Extraction refuses absolute paths, `..` segments, drive
  letters, symlinks and other non-regular entries; it limits entries (2,000) and
  total uncompressed size (100 MB); it ignores `__MACOSX` and `.DS_Store`; it writes
  only files and folders, without executable bits. Nothing from an archive is
  executed or opened.
- **Finding assets.** The staged folder may itself be an asset (a skill folder with
  `SKILL.md`, a power with `POWER.md`), or contain assets one level down, or use the
  library layouts (`ip/<kind>/`, `<kind>/`). Each candidate is validated with the
  normal outbound rules and reported with its kind, name, description and any error.
  Single Markdown files default to prompts; the user may choose steering instead.
- **Adding.** The user picks candidates, may rename them, picks the library, and
  writes a note. The plan copies each asset to `<library>/<layout>/<kind>/<name>`,
  refuses to overwrite an existing asset, and commits only those paths with the note.
  Push is optional and as safe as in ADR 005.
- **Finding libraries.** To help people who already have a checkout, the window
  lists library folders found up to three levels below the home folder (skipping
  hidden and system folders, bounded by a folder budget and a time limit). It
  only reads; adding one is the normal Add library action.

## Consequences

People can adopt a skill from anywhere in one step, and it then appears in Library
for everyone like any other asset. Upload size and archive limits bound memory and
disk use. Staged files never leave the temporary folder except through an applied
plan.

## Addendum: from a Git repository

`captain-share add REPOSITORY --skill NAME` (like `npx skills add`) and **From Git…**
in the window stage a repository instead of a zip or folder: a shallow clone into a
private temporary folder, HTTPS or SSH only (`GIT_ALLOW_PROTOCOL`), never prompting,
with `OWNER/REPO` meaning GitHub; addresses with credentials, local paths and other
protocols are refused. A repository is searched for library layouts and for skill
folders at any depth (`skills/<category>/<name>/`), never inside a skill folder;
loose Markdown is documentation there and is not offered. Chosen skills go into the
personal library (ADR 008) like any other import, through the same checks, and the
commit names the repository and commit (`Add tdd from github.com/OWNER/REPO@abc1234`).
In the window a long list starts with nothing picked and has a filter. A stopped
window (Ctrl-C or SIGTERM) removes its downloads.
