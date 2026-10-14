# ADR 010: Disconnecting a library

Status: Accepted

## Context

`source remove` only masks a library in one configuration layer. Someone who no
longer wants a library had to find every place that selects it, remove links by
hand, and leave inherited bindings and selections behind.

## Decision

Disconnecting a library is one previewed plan across every place a window manages
(ADR 004: the user, the startup project, the folders above it, registered
projects). For each place that has the library's selections, links or bindings,
its managed links are archived (recoverable), its selections from the library are
removed from its own `sharing.json`, and the binding is removed from its
`sharing.json` and `sharing.local.json`. All places apply in one transaction under
the usual locks; a handled failure rolls every place back.

The library checkout is never touched, and neither are unmanaged copies (reported
as kept) or Kiro powers installed from it (kept, with the `powers uninstall`
command to run separately; ADR 003). A managed link that changed or was
retargeted, a Git-tracked destination, or a configuration folder this window does
not manage that still connects or selects the library is a conflict. The personal
library (ADR 008) disconnects the same way; creating it again reconnects the
folder.

## Consequences

Removing a library leaves no dangling links or selections in the places the
window knows. Places it does not know are reported, not edited. The checkout stays
on disk until the user deletes it.
