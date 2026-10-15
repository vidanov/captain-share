# ADR 016: Clone a library by its address; skills in category folders

Status: Accepted

## Context

Cloning a library asked for a name, an address, a new folder and a layout. Most
people only have the address. Many public skill repositories also group skills in
category folders (`skills/engineering/tdd/SKILL.md`), which the library layouts did
not read, so such a repository showed no skills when connected.

## Decision

- **Libraries folder.** A machine setting (`settings.json` in the global folder,
  Settings → Libraries, "New libraries go to …") says where new checkouts go;
  `~/Libraries` by default. Existing libraries stay where they are, and the personal
  library keeps its own place.
- **Clone by address.** Only the address is required (`OWNER/REPO` means GitHub).
  The name is the owner and repository (`mattpocock-skills`, with `-2` when taken),
  the folder is the Libraries folder plus the name, and the layout is detected. Name,
  folder and layout can still be changed under "Change name, folder or layout".
- **Category folders.** Under `skills/`, a folder without its own `SKILL.md` that
  holds skill folders is a category: its skills are library assets under their own
  names (`kind_items`, `source_path`, `locate`, `catalog`), Git history and edits map
  to them (`asset_of` with the repository), and `why` names them. Two skills with one
  name in different categories are reported, not merged. A folder holding only
  Markdown notes (a README) is not listed as a broken skill.

## Consequences

A public skill repository can be cloned and kept up to date like a team library,
in one step. Sharing into such a library still writes `skills/NAME` at the top.
Categories are read one level deep.
