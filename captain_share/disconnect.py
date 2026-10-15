"""Disconnect a library from every place a window manages (ADR 010).

Each place's links from the library are archived and its selections and binding
removed through the engine's `disconnect` plan; all places are applied in one
transaction. The library checkout, Kiro's installed powers and unmanaged copies
are never touched; the last two are reported as kept."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import config as c
from . import engine as e
from . import integrations as i


def enabled_rows(data, prefix):
    return [
        key
        for direction in ("inbound", "outbound")
        for key, row in data.get(direction, {}).items()
        if key.startswith(prefix) and row.get("enabled", True)
    ]


def defines(data, source):
    spec = data.get("sources", {}).get(source)
    return spec is not None and (spec.get("enabled", True) or spec.get("path"))


def library_assets(folder, layout):
    """(kind, name) of every asset in a library checkout."""
    if folder is None:
        return []
    base = Path(folder) / "ip" if layout != "flat" else Path(folder)
    found = []
    for kind in c.KINDS:
        directory = base / kind
        if directory.is_dir() and not directory.is_symlink():
            found += [(kind, p.name) for p in e.kind_items(directory, kind)]
    return found


def planned(source, places):
    """(preview, [(place, engine plan)]) for disconnecting `source`."""
    c.name(source)
    prefix = f"{source}/"
    # Only these configuration folders are edited; any other one that connects or
    # selects the library (e.g. a global folder outside home) is "elsewhere".
    known = {Path(p["path"]) / ".captain" for p in places}
    changes: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    rows, steps, seen_layers = [], [], set()
    folder, layout = None, None

    def conflict(message, reason, label, path=None):
        conflicts.append(
            {"message": message, "path": path, "key": None, "reason": reason, "place_label": label}
        )

    for place in places:
        try:
            bound = c.effective(Path(place["path"]))["sources"].get(source, {})
        except (c.ShareError, OSError):
            continue
        if bound.get("path"):
            folder, layout = bound["path"], bound.get("layout", "ip")
            break
    for place in places:
        target, label = Path(place["path"]), place["label"]
        try:
            c.effective(target)  # Validates every layer before anything is planned.
            installed = e.state(target)["inbound"]
            layers = list(c.layers(target))
        except (c.ShareError, OSError) as exc:
            conflict(f"{label}: {exc}", "error", label)
            continue
        own = local = None
        for layer, data, is_local in layers:
            if layer.parent == target / ".captain":
                if is_local:
                    local = data
                else:
                    own = data
            if layer in seen_layers:
                continue
            seen_layers.add(layer)
            if layer.parent in known:
                continue
            if defines(data, source) or (not is_local and enabled_rows(data, prefix)):
                conflict(
                    f"{layer} also connects or selects {source}; it is not a place this window "
                    "manages, so disconnect it there",
                    "elsewhere",
                    label,
                    str(layer),
                )
        links = sorted(
            rel for rel, row in installed.items() if row.get("key", "").startswith(prefix)
        )
        selected = sorted(
            key
            for direction in ("inbound", "outbound")
            for key in (own or {}).get(direction, {})
            if key.startswith(prefix)
        )
        has_binding = source in (own or {}).get("sources", {}) or source in (local or {}).get(
            "sources", {}
        )
        for kind, name in library_assets(folder, layout):
            for dest in c.DESTINATIONS[kind].values():
                rel = f"{dest}/{name}"
                try:
                    path = c.safe_path(target, rel, leaf_link=True)
                except c.ShareError:
                    continue
                if os.path.lexists(path) and rel not in installed:
                    kept.append(
                        {"reason": "copy", "path": rel, "place": str(target), "place_label": label}
                    )
        if not (links or selected or has_binding):
            continue
        step = e.plan(target, action="disconnect", source=source)
        steps.append((target, step))
        for change in step["changes"]:
            changes.append(
                {
                    "op": "unlink",
                    "path": change["path"],
                    "place": str(target),
                    "place_label": label,
                    "archive": True,
                }
            )
        for key in selected:
            changes.append(
                {"op": "deselect", "key": key, "place": str(target), "place_label": label}
            )
        for name, has in (
            ("sharing.json", source in (own or {}).get("sources", {})),
            ("sharing.local.json", source in (local or {}).get("sources", {})),
        ):
            if has:
                changes.append(
                    {
                        "op": "disconnect",
                        "path": str(target / ".captain" / name),
                        "place": str(target),
                        "place_label": label,
                    }
                )
        for row in step["conflicts"]:
            conflicts.append({**row, "message": f"{label}: {row['message']}", "place_label": label})
        rows.append(
            {
                "path": str(target),
                "label": label,
                "links": [str(target / rel) for rel in links],
                "selections": selected,
            }
        )
    if not steps and not conflicts:
        raise c.ShareError(f"No library named {source} is connected in the places of this window")
    if folder is not None:
        for kind, name in library_assets(folder, layout):
            if kind != "powers":
                continue
            bound = {"sources": {source: {"path": folder, "layout": layout}}}
            state = i.power_state(Path(e.source_path(bound, source, kind, name)))
            if state != "not-installed":
                kept.append(
                    {
                        "reason": "power",
                        "name": name,
                        "path": str(i.kiro_powers_dir() / name),
                        "state": state,
                        "command": f"captain-share powers uninstall {source} {name}",
                    }
                )
    result: dict[str, Any] = {
        "version": 1,
        "spec": {"type": "disconnect", "action": "disconnect", "source": source},
        "disconnect": {
            "source": source,
            "places": [{"path": p["path"], "label": p["label"]} for p in places],
        },
        "changes": changes,
        "errors": [row["message"] for row in conflicts],
        "conflicts": conflicts,
        "can_apply": not conflicts,
        "places": rows,
        "kept": kept,
        "folder": folder,
    }
    result["fingerprint"] = e.digest([result, [(str(t), step["fingerprint"]) for t, step in steps]])
    return result, steps


def plan(source, places):
    """Preview disconnecting a library from every place in `places`."""
    return planned(source, places)[0]


def apply(saved):
    """Apply a fresh disconnect preview at every place in one transaction."""
    spec = saved.get("disconnect") if isinstance(saved, dict) else None
    if not isinstance(spec, dict) or not isinstance(spec.get("places"), list):
        raise c.ShareError("Preview the disconnect before applying it")
    source, places = spec["source"], spec["places"]
    roots: list[Path] = []
    for place in places:
        try:
            roots += e.apply_roots(c.root(place["path"]))
        except (c.ShareError, OSError):
            continue
    with c.locks(roots):
        current, steps = planned(source, places)
        if current["fingerprint"] != saved.get("fingerprint"):
            raise c.ShareError("Preview is stale: files or configuration changed. Preview again.")
        if not current["can_apply"]:
            raise c.ShareError("; ".join(current["errors"]))
        tx, archives = e.Transaction(), []  # type: tuple[e.Transaction, list[dict]]
        try:
            for target, step in steps:
                e.perform(tx, step, target, archives)
        except Exception as exc:
            issues = tx.rollback()
            raise c.ShareError(
                f"Disconnecting failed and was rolled back: {exc}"
                + (f"; recovery needed: {issues}" if issues else "")
            ) from exc
    return {
        "disconnected": source,
        "places": [row["path"] for row in current["places"]],
        "archives": archives,
        "kept": current["kept"],
    }
