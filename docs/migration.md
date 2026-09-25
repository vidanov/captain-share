# Migrating existing connections

Captain Share uses the `.captain` namespace with new, independently versioned
`sharing.json`, `sharing.local.json`, and `sharing-state.json` files. It does not
claim Captain's existing plan, pack, item, or tooling-store formats are identical.
Captain can integrate with this CLI later; no Captain code changes are required.

Import source and repository connections from a Captain tooling directory:

```bash
captain-share --target /path/to/project import-captain /path/to/captain-state/tooling
captain-share --target /path/to/project import-captain /path/to/captain-state/tooling --apply
```

The importer reads `sources.json` and `repositories.json`. It imports validated local
checkout paths and labels; it never executes recorded Python programs, saved plans,
recipes, or auto-publish jobs. It does not modify Captain's records or adopt its links.
Unavailable sources are reported and omitted. For typical Captain installations,
the tooling directory is under `~/.osa-kiro/captain/tooling`.

Libraries using `ip/` can be connected directly. Existing named profile manifests
can be imported as inbound selections:

```bash
captain-share --target /path/to/project source add team /path/to/toolkit
captain-share --target /path/to/project import-profile team pilot
captain-share --target /path/to/project import-profile team pilot --apply
```

This only saves selections. Existing links, `.kiro-ip` selectors, hooks, and installer
ledgers remain owned by their original installer. Inspect them there and disconnect
that installation using its own supported command before applying a replacement.
Captain Share refuses unowned collisions, even when a link already points to the
same source. Do not delete the old ledger to bypass the check.

For previously unmanaged local skills under `.kiro/skills`, copy a reviewed skill
outbound, archive its local folder with `remove skills NAME --apply`, select the
shared version inbound, and preview/apply. The original stays in the archive for
recovery. No bulk migration of the user's existing projects happens at setup time.
