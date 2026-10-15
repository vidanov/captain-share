"""Loopback-only UI with per-launch token, origin checks and the same engine."""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import config as c
from . import disconnect as dc
from . import engine as e
from . import imports as im
from . import integrations as i
from . import personal as me
from . import repositories as r
from . import selfupdate as su

WEB = Path(__file__).parent / "web"
# The guided tour is static: its page, script, style and images directly in web/tour/.
TOUR_FILES = {
    "/tour": ("tour.html", "text/html; charset=utf-8"),
    "/tour.js": ("tour.js", "text/javascript"),
    "/tour.css": ("tour.css", "text/css"),
}
TOUR_IMAGE = re.compile(r"[a-z0-9-]+\.(jpg|png|webp)\Z")
IMAGE_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
# Finding libraries on this machine: bounded, cached briefly per window session.
DISCOVER_DEPTH = 3
DISCOVER_BUDGET = 3000
DISCOVER_SECONDS = 2.0
DISCOVER_CACHE = 60.0
DISCOVER_SKIP = {
    "Library",
    "Applications",
    "Pictures",
    "Movies",
    "Music",
    "node_modules",
    "venv",
    "__pycache__",
}


def folder_listing(target, value="", show_hidden=False):
    path = Path(value).expanduser() if value else target
    if not path.is_absolute():
        path = target / path
    path = c.root(path)
    layout = c.detect_layout(path)
    entries = []
    for item in path.iterdir():
        if item.name.startswith(".") and not show_hidden:
            continue
        try:
            if item.is_dir():
                # Inside a library, subfolders are its asset folders, not libraries.
                found = None if layout else c.detect_layout(item)
                entries.append({"name": item.name, "path": str(item), "library": found})
        except OSError:
            continue
    entries.sort(key=lambda item: item["name"].casefold())
    return {
        "path": str(path),
        "library": layout,
        "parent": str(path.parent),
        "home": str(Path.home()),
        "project": str(target),
        "folders": entries[:1000],
        "truncated": len(entries) > 1000,
    }


def library_info(target, value):
    """What connecting this folder would do: layout, asset counts, name, duplicates."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise c.ShareError("Choose an absolute folder path")
    path = c.root(path)
    if path.name == "ip" and c.detect_layout(path.parent) == "ip":
        path = path.parent  # The checkout root, not its ip/ folder, is the library.
    layout = c.detect_layout(path)
    counts = {}
    if layout:
        base = path / "ip" if layout == "ip" else path
        for kind in c.KINDS:
            folder = base / kind
            if folder.is_dir():
                found = len(e.kind_items(folder, kind))
                if found:
                    counts[kind] = found
    sources = c.effective(target)["sources"]
    connected = next((sid for sid, spec in sources.items() if spec.get("path") == str(path)), None)
    return {
        "path": str(path),
        "layout": layout,
        "assets": counts,
        "name": connected or c.suggest_name(path, taken=set(sources)),
        "connected_as": connected,
    }


def library_assets(path, layout):
    """Asset counts per kind of a library folder, and whether one asset is valid."""
    base = path / "ip" if layout == "ip" else path
    counts: dict[str, int] = {}
    valid = False
    for kind in c.KINDS:
        folder = base / kind
        if folder.is_symlink() or not folder.is_dir():
            continue
        for item in e.kind_items(folder, kind):
            try:
                c.asset_name(kind, item.name)
            except c.ShareError:
                continue
            counts[kind] = counts.get(kind, 0) + 1
            if not valid:
                try:
                    e.validate_asset(item, kind)
                    valid = True
                except (c.ShareError, OSError):
                    pass
    return counts, valid


def discover(startup):
    """Libraries on this machine: the home folder and folders up to DISCOVER_DEPTH
    below it, skipping hidden, system and generated folders, within a directory
    budget and a time limit. Read-only; nothing is connected."""
    home = c.global_dir().parent
    deadline = time.monotonic() + DISCOVER_SECONDS
    connected: dict[str, str] = {}
    for place in (home, startup):
        try:
            sources = c.effective(place)["sources"]
        except c.ShareError:
            continue
        for sid, spec in sorted(sources.items()):
            if spec.get("path"):
                connected.setdefault(str(Path(spec["path"]).expanduser().resolve()), sid)
    taken = set(connected.values())
    found: list[dict[str, Any]] = []
    queue, visited, truncated = deque([(home, 0)]), 0, False
    while queue:
        if visited >= DISCOVER_BUDGET or time.monotonic() > deadline:
            truncated = True
            break
        path, depth = queue.popleft()
        visited += 1
        if path != home:
            layout = c.detect_layout(path)
            if layout:
                try:
                    counts, valid = library_assets(path, layout)
                except OSError:
                    counts, valid = {}, False
                if valid:
                    found.append({"path": path, "layout": layout, "counts": counts})
                    continue  # A library's folders are its assets, not more libraries.
        if depth >= DISCOVER_DEPTH:
            continue
        try:
            children = sorted(path.iterdir())
        except OSError:
            continue
        for child in children:
            name = child.name
            if name.startswith(".") or name in DISCOVER_SKIP:
                continue
            try:
                if child.is_symlink() or not child.is_dir():
                    continue
            except OSError:
                continue
            queue.append((child, depth + 1))
    libraries = []
    for row in found:
        path = row["path"]
        source = connected.get(str(path))
        if source is None:
            name = c.suggest_name(path, taken=taken)
            taken.add(name)
        else:
            name = source
        repo = e.git_root(path)
        remote_url = None
        if repo is not None:
            remotes = r.quiet(repo, "remote").split()
            remote = "origin" if "origin" in remotes else (remotes[0] if remotes else "")
            if remote:
                remote_url = r.quiet(repo, "remote", "get-url", remote).strip() or None
        libraries.append(
            {
                "path": str(path),
                "name": name,
                "layout": row["layout"],
                "is_repo": repo is not None,
                "remote_url": remote_url,
                "counts": row["counts"],
                "total": sum(row["counts"].values()),
                "connected": source,
            }
        )
    libraries.sort(key=lambda x: (x["connected"] is not None, -x["total"], x["path"]))
    return {"libraries": libraries, "searched": [str(home)], "truncated": truncated}


PREVIEW_BYTES = 60_000
PREVIEW_FILES = 200
MAIN_FILES = ("SKILL.md", "POWER.md", "README.md")


def asset_preview(target, source, kind, name, local=None):
    """Read-only look inside one library asset, or with `local` one asset in the
    project (a path inside it): its files and its main text."""
    if kind not in c.KINDS:
        raise c.ShareError("Unknown asset kind")
    c.asset_name(kind, name)
    if local:
        path = c.safe_path(c.root(target), local)
        if not os.path.lexists(path):
            raise c.ShareError(f"{local} is no longer here. Refresh to check.")
    else:
        path = e.source_path(c.effective(target), source, kind, name)
        if not os.path.lexists(path):
            raise c.ShareError(f"{source} has no {kind} named {name}")
    if path.is_dir():
        inside = path.resolve()

        def contained(item):
            # Never list through a link that leads out of the asset folder.
            try:
                item.resolve().relative_to(inside)
            except ValueError:
                return False
            return True

        files = sorted(
            p
            for p in path.rglob("*")
            if p.is_file() and not p.is_symlink() and contained(p) and not e.ignored(p, path)
        )
        main = next((path / m for m in MAIN_FILES if (path / m).is_file()), None)
    else:
        files, main = [path], path
    text, truncated = None, False
    if main is not None:
        raw = main.read_bytes()[: PREVIEW_BYTES + 1]
        truncated = len(raw) > PREVIEW_BYTES
        if b"\0" not in raw[:PREVIEW_BYTES]:
            text = raw[:PREVIEW_BYTES].decode("utf-8", errors="replace")
    base = path if path.is_dir() else path.parent
    return {
        "path": str(path),
        "files": [
            {"path": f.relative_to(base).as_posix(), "size": f.stat().st_size}
            for f in files[:PREVIEW_FILES]
        ],
        "more_files": max(0, len(files) - PREVIEW_FILES),
        "main": main.relative_to(base).as_posix() if main is not None else None,
        "text": text,
        "truncated": truncated,
    }


def restart(server, port, token):
    """Start this window again with the code now on disk, keeping address and token."""
    handle, path = tempfile.mkstemp(prefix="captain-share-restart-", suffix=".json")
    with os.fdopen(handle, "w") as file:  # mkstemp: readable by this user only
        json.dump({"port": port, "token": token}, file)
    server.server_close()
    argv = su.restart_argv()
    os.execve(argv[0], argv, {**os.environ, RESTART: path})


def why_inside(target, value):
    """`why` for a path in this window's folder or the home folder's agent folders."""
    path = Path(os.path.abspath(Path(value).expanduser())) if value else None
    roots = [c.root(target), c.global_dir().parent]
    if path is None or not any(path == base or base in path.parents for base in roots):
        raise c.ShareError("Choose a file in this project or your home folder")
    return e.why(path)


def reveal_path(target, value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise c.ShareError("Choose an absolute asset path")
    path = Path(os.path.abspath(path))
    roots = [c.root(target)]
    roots.extend(
        Path(row["path"]).resolve()
        for row in c.effective(target)["sources"].values()
        if row.get("path")
    )
    # Resolve ancestors but preserve the leaf, so Finder selects a managed symlink itself.
    candidate = path.parent.resolve() / path.name
    if not any(candidate == base or base in candidate.parents for base in roots):
        raise c.ShareError("The file must belong to this workspace or a connected library")
    if not os.path.lexists(candidate):
        raise c.ShareError("This file is no longer here. Refresh to check its current status.")
    if sys.platform == "darwin":
        command = ["/usr/bin/open", "-R", str(candidate)]
    elif sys.platform.startswith("linux"):
        directory = (
            candidate if candidate.is_dir() and not candidate.is_symlink() else candidate.parent
        )
        command = ["xdg-open", str(directory)]
    else:
        raise c.ShareError("Opening a file manager is supported on macOS and Linux")
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise c.ShareError("The file manager did not respond. Try again.") from exc
    if result.returncode:
        raise c.ShareError(result.stderr.strip() or "Could not open the file manager")
    return {"revealed": str(candidate)}


def places(startup):
    """Folders this window may act on (ADR 004): the startup folder, the folders
    above it inside the home folder, the home folder itself (user-wide), and
    projects the user registered."""
    home = c.global_dir().parent
    rows: list[dict[str, str]] = []

    def add(path, kind, label):
        if all(row["path"] != str(path) for row in rows):
            rows.append({"path": str(path), "kind": kind, "label": label})

    add(startup, "user" if startup == home else "project", startup.name)
    for parent in startup.parents:
        if parent == home or home not in parent.parents:
            break
        add(parent, "folder", parent.name)
    add(home, "user", "Your user")
    for label, path in sorted(i.repositories()["repositories"].items()):
        if Path(path).is_dir():
            add(Path(path).resolve(), "project", label)
    return rows


def place(startup, value):
    if not value:
        return startup
    path = Path(value).expanduser().resolve()
    if any(row["path"] == str(path) for row in places(startup)):
        return path
    raise c.ShareError("This window does not manage that folder. Register it as a project first.")


def project_summary(row):
    target = Path(row["path"])
    try:
        current = e.status(target)
        preview = e.plan(target)
        return {
            **row,
            "linked": sum(1 for x in current["installed"] if x["status"] == "linked"),
            "drift": sum(1 for x in current["installed"] if x["status"] != "linked"),
            "changes": len(preview["changes"]),
            "conflicts": len(preview["conflicts"]),
        }
    except (c.ShareError, OSError) as exc:
        return {**row, "error": str(exc)}


def matrix(startup, target):
    """Library assets as rows and places as columns, for the patch bay view."""
    home = c.global_dir().parent
    allowed = {row["path"]: row for row in places(startup)}
    columns = [target]
    for parent in target.parents:
        if parent == home or home not in parent.parents:
            break
        if str(parent) in allowed:
            columns.append(parent)
    if target != home:
        columns.append(home)
    info = []
    for place in columns:
        cfg = c.effective(place)
        own_file = c.read_json(place / ".captain/sharing.json", c.blank()) or c.blank()
        installed: dict[str, list[str]] = {}
        for rel, row in e.state(place)["inbound"].items():
            installed.setdefault(row.get("key"), []).append(rel)
        kind = "user" if place == home else "project" if place == target else "folder"
        info.append(
            {
                "path": place,
                "cfg": cfg,
                "own": own_file.get("inbound", {}),
                "installed": installed,
                "column": {
                    "path": str(place),
                    "kind": kind,
                    "label": "Your user" if kind == "user" else place.name,
                    "tools": cfg["tools"],
                },
            }
        )
    rows = []
    for source in sorted(c.effective(target)["sources"]):
        listings: dict[str, dict[tuple[str, str], dict]] = {}
        for col in info:
            if col["column"]["kind"] == "folder" or source not in col["cfg"]["sources"]:
                continue
            try:
                listings[str(col["path"])] = {
                    (r["kind"], r["name"]): r for r in e.catalog(col["path"], source)
                }
            except c.ShareError:
                continue
        base = listings.get(str(target)) or next(iter(listings.values()), None) or {}
        for (kind, name), item in sorted(
            base.items(), key=lambda x: (c.KINDS.index(x[0][0]), x[0][1])
        ):
            key = f"{source}/{kind}/{name}"
            cells = {}
            for col in info:
                place = str(col["path"])
                origin = col["cfg"]["origins"]["inbound"].get(key)
                cell = {
                    "on": key in col["cfg"]["inbound"],
                    "own": origin == place,
                    "from": origin if origin and origin != place else None,
                    "masked": col["own"].get(key) == {"enabled": False},
                    "linked": key in col["installed"],
                }
                listed = listings.get(place, {}).get((kind, name))
                if listed and listed.get("destinations"):
                    states = [d["state"] for d in listed["destinations"]]
                    for state in ("blocked", "contributed", "changed", "absent", "installed"):
                        if state in states:
                            cell["dest"] = state
                            cell["paths"] = [d["path"] for d in listed["destinations"]]
                            break
                if (
                    kind == "powers"
                    and col["column"]["kind"] == "user"
                    and item["status"] == "available"
                ):
                    cell["power"] = i.power_state(Path(item["path"]))
                cells[place] = cell
            rows.append(
                {
                    "key": key,
                    "source": source,
                    "kind": kind,
                    "name": name,
                    "description": item.get("description", ""),
                    "status": item["status"],
                    "error": item.get("error"),
                    "path": item["path"],
                    "cells": cells,
                }
            )
    return {"columns": [col["column"] for col in info], "rows": rows}


def error_body(exc):
    """JSON for a failure: the message plus, for explained Git failures (ADR 009),
    `hint`, `command` and the original Git text as `detail`."""
    body = {"error": str(exc)}
    if isinstance(exc, c.ShareError):
        body.update(exc.fields())
    return body


def mode_of(data):
    mode = data.get("mode", "direct")
    if mode not in ("direct", "review"):
        raise c.ShareError("mode must be direct or review")
    return mode


def seen_path():
    # Machine-local record of what the user last saw per library (ADR 005).
    return c.config_dir(scope="global") / "seen.json"


def read_seen():
    try:
        data = c.read_json(seen_path(), {})
    except c.ShareError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        sid: row
        for sid, row in data.items()
        if isinstance(row, dict) and isinstance(row.get("assets", []), list)
    }


def record_seen(current):
    """Store these libraries' current heads and asset keys; other rows are kept."""
    path = seen_path()
    with c.locks([path]):
        data = read_seen()
        changed = False
        for sid, row in current.items():
            old = data.get(sid, {})
            if old.get("head") != row["head"] or old.get("assets") != row["assets"]:
                data[sid] = {**row, "at": r.now()}
                changed = True
        if changed:
            c.atomic_json(path, data)


def library_home(startup, baseline):
    """The everyday view: libraries visible from the startup project and their assets,
    marked against the session baseline. Returns the view and the seen state to record."""
    home = c.global_dir().parent
    user_cfg = c.effective(home)
    project_cfg = user_cfg if startup == home else c.effective(startup)
    linked = {
        "user": {row.get("key") for row in e.state(home)["inbound"].values()},
        "project": {row.get("key") for row in e.state(startup)["inbound"].values()},
    }
    libraries, assets, seen = [], [], {}
    # Kept versions (ADR 007) of library assets, at the project and for the user.
    versions: dict[str, list[dict[str, Any]]] = {}
    for where, folder in (("project", startup), ("user", home)):
        if where == "user" and folder == startup:
            continue
        try:
            rows = r.version_rows(folder)
        except (c.ShareError, OSError):
            continue
        for row in rows:
            versions.setdefault(f"{row['source']}/{row['kind']}/{row['of']}", []).append(
                {
                    "key": row["key"],
                    "name": row["name"],
                    "place": where,
                    "team_changed": row["team_changed"],
                }
            )
    hidden_keys = c.hidden_assets()
    for source in sorted(project_cfg["sources"]):
        if source == i.BUILTIN:
            continue  # the bundled agent skill is not a library people browse
        spec = project_cfg["sources"][source]
        info: dict[str, Any] = {
            "source": source,
            "path": spec.get("path"),
            "url": spec.get("url") or None,
            "is_repo": False,
            "branch": None,
            "behind": None,
            "ahead": None,
            "last_commit_at": None,
            "new": 0,
            "updated": 0,
            "starter": [],
            "error": None,
            # The personal library (ADR 008) has no remote and is never marked new.
            "personal": me.is_personal(source, spec.get("path")),
            "sign": bool(spec.get("sign")),
            "checked_at": None,
            "checked_fresh": False,
        }
        libraries.append(info)
        try:
            lib = r.library(startup, source)
            rows = e.catalog(startup, source)
        except (c.ShareError, OSError) as exc:
            info["error"] = str(exc)
            continue
        try:
            starter = i.import_profile(startup, source, "starter")["selections"]
            info["starter"] = [c.selection_key(row) for row in starter]
        except (c.ShareError, OSError):
            pass
        head, made, changed, edited, own_email = None, {}, set(), {}, ""
        if lib["repo"] is not None:
            git = r.status(startup, source)
            head = git["head"] or None
            info.update(
                is_repo=True,
                branch=git["branch"] or None,
                behind=git["behind"],
                ahead=git["ahead"],
                last_commit_at=r.last_commit_at(lib["repo"]),
            )
            info["url"] = info["url"] or git["remote_url"] or None
            if info["personal"]:
                info.update(behind=None, ahead=None, url=None)
            else:
                # A fetch or pull in the last six hours at this head is still current.
                at, fresh, kept = r.freshness(source, head)
                info.update(checked_at=at, checked_fresh=fresh)
                if fresh and kept:
                    info.update(behind=kept.get("behind"), ahead=kept.get("ahead"))
            made = r.history(lib["repo"], lib["prefix"])
            # What you shared yourself is not news to you.
            own_email = r.quiet(lib["repo"], "config", "user.email").strip().lower()
            edited = r.edits(lib["repo"], lib["prefix"], git["changes"])
        before = None if info["personal"] else baseline.get(source)
        if before is not None and lib["repo"] is not None:
            changed = r.changed_between(lib["repo"], lib["prefix"], before.get("head"), head)
        keys = []
        for row in rows:
            kind, name = row["kind"], row["name"]
            key = f"{source}/{kind}/{name}"
            keys.append(key)
            user_origin = user_cfg["origins"]["inbound"].get(key)
            have_user = key in user_cfg["inbound"] and user_origin == str(home)
            have_project = key in project_cfg["inbound"]
            origin = project_cfg["origins"]["inbound"].get(key)
            last = made.get((kind, name)) or {}
            mine = bool(own_email) and last.get("email") == own_email
            # Hidden assets (not installed) are left out of "new" too.
            hidden = key in hidden_keys and not (have_user or have_project)
            new = (
                before is not None
                and key not in before.get("assets", [])
                and not mine
                and not hidden
            )
            updated = (have_user or have_project) and (kind, name) in changed and not mine
            info["new"] += int(new)
            info["updated"] += int(updated)
            author = made.get((kind, name))
            power = None
            if kind == "powers" and row["status"] == "available":
                power = i.power_state(Path(row["path"]))
            assets.append(
                {
                    "key": key,
                    "source": source,
                    "kind": kind,
                    "name": name,
                    "description": row.get("description", ""),
                    "status": row["status"],
                    "error": row.get("error"),
                    "shared_by": author["by"] if author else None,
                    "shared_at": author["at"] if author else None,
                    "new": new,
                    "updated": updated,
                    "hidden": hidden,
                    "have": {
                        "user": have_user,
                        "project": have_project,
                        "from": origin if have_project and origin != str(startup) else None,
                    },
                    "linked": {
                        "user": key in linked["user"],
                        "project": key in linked["project"],
                    },
                    "power": power,
                    "edited": bool(edited.get((kind, name))),
                    "versions": versions.get(key, []),
                }
            )
        seen[source] = {"head": head, "assets": sorted(keys)}
    view = {
        "user": str(home),
        "project": str(startup),
        "agents": user_cfg["tools"],
        "libraries": libraries,
        "assets": assets,
    }
    return view, seen


def starter_view(target, source):
    """A library's current starter set (profiles/starter.manifest)."""
    lib = r.library(target, source)
    file = c.safe_path(lib["path"], r.STARTER)
    view = {
        "source": source,
        "path": r.STARTER,
        "exists": file.is_file(),
        "assets": [],
        "error": None,
    }
    if view["exists"]:
        try:
            rows = i.import_profile(target, source, "starter")["selections"]
            view["assets"] = [f"{row['kind']}/{row['name']}" for row in rows]
        except (c.ShareError, OSError) as exc:
            view["error"] = str(exc)
    return view


def share_candidates(target):
    """What this place could share: local originals not in a library, library assets
    with uncommitted edits, and the libraries that can record a share."""
    cfg = c.effective(target)
    names, local, edited = [], [], []
    for source in sorted(cfg["sources"]):
        if source == i.BUILTIN:
            continue
        try:
            lib = r.library(target, source)
        except c.ShareError:
            continue
        if lib["repo"] is None:
            continue
        names.append(source)
        changes = r.status(target, source)["changes"]
        for (kind, name), paths in sorted(r.edits(lib["repo"], lib["prefix"], changes).items()):
            files = []
            for rel in paths:
                full = lib["repo"] / rel
                try:
                    files.append(full.relative_to(lib["path"]).as_posix())
                except ValueError:
                    files.append(rel)
            edited.append({"source": source, "kind": kind, "name": name, "files": files})
    for row in e.scan(target):
        if row["status"] != "local":
            continue
        found = e.locate(cfg, row["name"], kind=row["kind"])
        # A Claude rule or command is offered only while no library has it; it is
        # shared converted to Kiro's format (ADR 014). Other Claude files are not.
        if e.converted_folder(row["path"]) and (found or not e.shares_back(row["path"])):
            continue
        differs, older = None, None
        if found:
            original = e.source_path(cfg, found[0][0], row["kind"], row["name"])
            try:
                differs = not e.same_content(target / row["path"], original)
            except (c.ShareError, OSError):
                differs = True
            if differs:
                # An old team version is not the user's change (for example a copy
                # left by an earlier installer): say so instead of offering to send it.
                try:
                    lib = r.library(target, found[0][0])
                    if lib["repo"] is not None:
                        older = r.older_version(
                            lib["repo"],
                            lib["prefix"],
                            row["kind"],
                            row["name"],
                            target / row["path"],
                        )
                except (c.ShareError, OSError):
                    older = None
        local.append(
            {
                "kind": row["kind"],
                "name": row["name"],
                "path": row["path"],
                "description": e.describe(target / row["path"], row["kind"]),
                "exists_in": found[0][0] if found else None,
                "differs": differs,
                "older": older,
            }
        )
    # Assets installed for every project (~/.kiro, ~/.claude) that no library has yet,
    # shared from the home folder; only when this window is not already there.
    home = c.global_dir().parent
    shared_from_home = []
    if c.root(target) != home:
        try:
            home_cfg = c.effective(home)
            for row in e.scan(home):
                if row["status"] != "local":
                    continue
                if e.converted_folder(row["path"]) and not e.shares_back(row["path"]):
                    continue
                if e.locate(home_cfg, row["name"], kind=row["kind"]):
                    continue
                shared_from_home.append(
                    {
                        "kind": row["kind"],
                        "name": row["name"],
                        "path": row["path"],
                        "description": e.describe(home / row["path"], row["kind"]),
                        "target": str(home),
                    }
                )
        except c.ShareError:
            shared_from_home = []
    personal = []
    if me.SOURCE in cfg["sources"]:
        try:
            base = r.library(target, me.SOURCE)["path"]
            rows = e.catalog(target, me.SOURCE)
        except (c.ShareError, OSError):
            rows = []
        for row in rows:
            if row["status"] != "available":
                continue
            others = [
                found
                for found in e.locate(cfg, row["name"], kind=row["kind"])
                if found[0] != me.SOURCE
            ]
            personal.append(
                {
                    "kind": row["kind"],
                    "name": row["name"],
                    "description": row.get("description", ""),
                    "path": Path(row["path"]).relative_to(base).as_posix(),
                    "exists_in": others[0][0] if others else None,
                }
            )
    return {
        "local": local,
        "edited": edited,
        "libraries": names,
        "versions": r.version_rows(target),
        "personal": personal,
        "global": shared_from_home,
    }


class Server(HTTPServer):
    """Removes the session's staged uploads when it closes."""

    stages: im.Stages | None = None

    def server_close(self):
        try:
            super().server_close()
        finally:
            if self.stages is not None:
                self.stages.close()


def make_server(target, port=0, token=None):
    startup = c.root(target)
    token = token or secrets.token_urlsafe(32)
    # The code this window runs; About offers Restart window once disk differs.
    started = su.running_version()
    plans: dict[str, dict[str, Any]] = {}
    # Zips and folders staged for "add from outside" (ADR 006).
    stages = im.Stages()
    # "New since last looked" compares against seen.json as it was when this
    # window first asked, so marks stay put while the window is open.
    session: dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, value, code=200, content_type="application/json"):
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )
            self.end_headers()
            self.wfile.write(data)

        def authorized(self, api=True):
            expected = f"127.0.0.1:{cast(HTTPServer, self.server).server_port}"
            if self.headers.get("Host") != expected:
                self.respond({"error": "Invalid host"}, 403)
                return False
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + expected:
                self.respond({"error": "Invalid origin"}, 403)
                return False
            if api and not secrets.compare_digest(self.headers.get("X-Captain-Token", ""), token):
                self.respond(
                    {"error": "Open the URL printed by captain-share serve to connect."}, 403
                )
                return False
            return True

        def do_GET(self):
            parsed = urlparse(self.path)
            if not self.authorized(parsed.path.startswith("/api/")):
                return
            try:
                target = place(startup, parse_qs(parsed.query).get("target", [""])[0])
                if parsed.path == "/api/state":
                    result = e.status(target)
                    result["startup"] = str(startup)
                    result["agent_skill"] = i.skill_installed(target)
                    result["libraries_folder"] = str(c.libraries_folder())
                    result["places"] = places(startup)
                    home = c.global_dir().parent
                    # Assets already installed user-wide, so a project need not add them.
                    result["user_installed"] = (
                        []
                        if target == home
                        else sorted({row["key"] for row in e.state(home)["inbound"].values()})
                    )
                    result["candidates"] = e.scan(target)
                    result["kinds"] = list(c.KINDS)
                    result["destinations"] = c.DESTINATIONS
                    result["file_manager"] = (
                        "Finder" if sys.platform == "darwin" else "file manager"
                    )
                    self.respond(result)
                elif parsed.path == "/api/folders":
                    query = parse_qs(parsed.query)
                    self.respond(
                        folder_listing(
                            target, query.get("path", [""])[0], query.get("hidden", [""])[0] == "1"
                        )
                    )
                elif parsed.path == "/api/library":
                    self.respond(library_info(target, parse_qs(parsed.query).get("path", [""])[0]))
                elif parsed.path == "/api/repository":
                    source = parse_qs(parsed.query).get("source", [""])[0]
                    self.respond(r.status(target, source))
                elif parsed.path == "/api/home":
                    if "baseline" not in session:
                        session["baseline"], session["recorded"] = read_seen(), set()
                    view, seen = library_home(startup, session["baseline"])
                    # Each library is recorded once per session, when first shown, so
                    # what arrives while the window is open is marked next time too.
                    fresh = {k: v for k, v in seen.items() if k not in session["recorded"]}
                    # A library first seen now (for example cloned in this window) uses
                    # that sight as its baseline, so later arrivals are marked new.
                    for sid, row in fresh.items():
                        session["baseline"].setdefault(sid, row)
                    if fresh:
                        record_seen(fresh)
                        session["recorded"].update(fresh)
                    self.respond(view)
                elif parsed.path == "/api/asset":
                    query = parse_qs(parsed.query)
                    self.respond(
                        asset_preview(
                            target,
                            query.get("source", [""])[0],
                            query.get("kind", [""])[0],
                            query.get("name", [""])[0],
                            query.get("local", [""])[0] or None,
                        )
                    )
                elif parsed.path == "/api/self":
                    found = su.status()
                    found["running"] = started
                    found["restart_needed"] = su.running_version() != started
                    self.respond(found)
                elif parsed.path == "/api/outdated":
                    # Read-only: compares with the commits last fetched (ADR 011).
                    self.respond(r.outdated(target))
                elif parsed.path == "/api/why":
                    self.respond(why_inside(target, parse_qs(parsed.query).get("path", [""])[0]))
                elif parsed.path == "/api/share-candidates":
                    self.respond(share_candidates(target))
                elif parsed.path == "/api/diff":
                    query = parse_qs(parsed.query)
                    self.respond(
                        e.diff(
                            target,
                            query.get("source", [""])[0],
                            query.get("kind", [""])[0],
                            query.get("name", [""])[0],
                            query.get("path", [None])[0] or None,
                        )
                    )
                elif parsed.path == "/api/starter":
                    source = parse_qs(parsed.query).get("source", [""])[0]
                    self.respond(starter_view(target, source))
                elif parsed.path == "/api/version-diff":
                    key = parse_qs(parsed.query).get("key", [""])[0]
                    self.respond(r.version_diff(target, key))
                elif parsed.path == "/api/matrix":
                    self.respond(matrix(startup, target))
                elif parsed.path == "/api/projects":
                    self.respond(
                        [
                            project_summary(row)
                            for row in places(startup)
                            if row["kind"] == "project"
                        ]
                    )
                elif parsed.path == "/api/catalog":
                    source = parse_qs(parsed.query).get("source", [""])[0]
                    rows = e.catalog(target, source)
                    for row in rows:
                        if row["kind"] == "powers" and row["status"] == "available":
                            row["kiro"] = i.power_state(Path(row["path"]))
                    self.respond(rows)
                elif parsed.path == "/api/discover":
                    cached = session.get("discover")
                    if cached is None or time.monotonic() - cached[0] > DISCOVER_CACHE:
                        cached = (time.monotonic(), discover(startup))
                        session["discover"] = cached
                    self.respond(cached[1])
                elif parsed.path in TOUR_FILES:
                    filename, mime = TOUR_FILES[parsed.path]
                    self.static(WEB / filename, mime)
                elif parsed.path.startswith("/tour/"):
                    match = TOUR_IMAGE.fullmatch(parsed.path[len("/tour/") :])
                    if match:
                        self.static(WEB / "tour" / match.group(0), IMAGE_TYPES[match.group(1)])
                    else:
                        self.respond({"error": "Not found"}, 404)
                elif parsed.path in ("/", "/about", "/app.js", "/style.css"):
                    filename = {
                        "/": "index.html",
                        "/about": "index.html",
                        "/app.js": "app.js",
                        "/style.css": "style.css",
                    }[parsed.path]
                    mime = {
                        "/": "text/html; charset=utf-8",
                        "/about": "text/html; charset=utf-8",
                        "/app.js": "text/javascript",
                        "/style.css": "text/css",
                    }[parsed.path]
                    content = (WEB / filename).read_bytes()
                    if filename == "index.html":
                        content = content.replace(b"__APP_VERSION__", __version__.encode())
                    self.respond(content, content_type=mime)
                else:
                    self.respond({"error": "Not found"}, 404)
            except (c.ShareError, OSError, ValueError) as exc:
                self.respond(error_body(exc), 400)

        def static(self, path, mime):
            if path.is_symlink() or not path.is_file():
                self.respond({"error": "Not found"}, 404)
                return
            content = path.read_bytes()
            if mime.startswith("text/html"):
                content = content.replace(b"__APP_VERSION__", __version__.encode())
            self.respond(content, content_type=mime)

        def upload(self):
            """A zip as the raw request body, staged in this session's temporary folder."""
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if self.headers.get("Content-Type") != "application/zip":
                self.close_connection = True
                self.respond({"error": "Expected a zip file (application/zip)"}, 400)
                return
            if length <= 0 or length > im.MAX_UPLOAD:
                # The body is not read; close rather than reuse the connection.
                self.close_connection = True
                self.respond({"error": "The zip file must be at most 20 MB"}, 400)
                return
            try:
                data = self.rfile.read(length)
                if len(data) != length:
                    raise c.ShareError("The upload was incomplete")
                name = parse_qs(urlparse(self.path).query).get("name", [""])[0]
                self.respond(stages.upload(data, name))
            except (c.ShareError, OSError, ValueError) as exc:
                self.respond(error_body(exc), 400)

        def do_POST(self):
            if not self.authorized():
                return
            if urlparse(self.path).path == "/api/import-upload":
                self.upload()
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if (
                    length <= 0
                    or length > 262144
                    or self.headers.get("Content-Type") != "application/json"
                ):
                    self.respond({"error": "Expected a bounded JSON request"}, 400)
                    return
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise c.ShareError("Expected a JSON object")
                path = urlparse(self.path).path
                target = place(startup, data.get("target", ""))
                if path == "/api/reveal":
                    result = reveal_path(target, data["path"])
                elif path == "/api/skill-plan":
                    result = i.skill_plan(target, remove=data.get("remove") is True)
                    plans.clear()
                    plans[result["fingerprint"]] = result
                elif path == "/api/self-check":
                    result = su.status(fetch=True)
                elif path == "/api/self-update":
                    result = su.update()
                elif path == "/api/restart":
                    # Answer first, then replace this process with a fresh one on the
                    # same port and token (ADR 015).
                    self.respond({"restarting": True})
                    port = cast(HTTPServer, self.server).server_port
                    threading.Timer(0.3, restart, (self.server, port, token)).start()
                    return
                elif path == "/api/clone":
                    result = r.clone(
                        target,
                        data.get("name"),
                        data.get("url"),
                        data.get("path") or None,
                        data.get("layout") or None,
                        create_parent=data.get("create_parent") is True,
                    )
                elif path == "/api/hide":
                    result = c.set_hidden(data.get("key"), data.get("hidden") is not False)
                elif path == "/api/libraries-folder":
                    result = c.set_libraries_folder(data.get("path"))
                    plans.clear()
                elif path == "/api/git":
                    result = r.operate(
                        target, data.get("source", ""), data["action"], data["fingerprint"]
                    )
                    plans.clear()
                elif path == "/api/disconnect-plan":
                    result = dc.plan(data["source"], places(startup))
                    plans.clear()
                    plans[result["fingerprint"]] = result
                elif path == "/api/disconnect-apply":
                    fingerprint = data.get("fingerprint")
                    if not isinstance(fingerprint, str):
                        raise c.ShareError("A preview fingerprint is required")
                    saved = plans.get(fingerprint)
                    if saved is None or "disconnect" not in saved:
                        raise c.ShareError("Preview the disconnect again before applying")
                    plans.pop(fingerprint)
                    result = dc.apply(saved)
                elif path == "/api/library-settings":
                    if "check" in data:
                        text = data["check"]
                        if not isinstance(text, str):
                            raise c.ShareError("check must be a command line or empty")
                        try:
                            command = shlex.split(text) or None
                        except ValueError as exc:
                            raise c.ShareError(f"Could not read the command: {exc}") from exc
                        result = c.set_check(target, data["source"], command)
                    else:
                        result = c.set_sign(target, data["source"], data["sign"])
                    plans.clear()
                elif path == "/api/fetch":
                    result = r.fetch(target, data["source"])
                elif path == "/api/share-plan":
                    result = r.share_plan(
                        target,
                        data["source"],
                        data["kind"],
                        data["name"],
                        data["path"],
                        adopt=data.get("adopt") is True,
                        mode=mode_of(data),
                    )
                    plans.clear()
                    plans[result["fingerprint"]] = result
                elif path == "/api/personal-library":
                    result = me.ensure()
                    plans.clear()
                elif path == "/api/promote-plan":
                    result = me.promote_plan(
                        target,
                        data["kind"],
                        data["name"],
                        data["team"],
                        places(startup),
                        mode_of(data),
                    )
                    plans.clear()
                    plans[result["fingerprint"]] = result
                elif path == "/api/promote-apply":
                    fingerprint = data.get("fingerprint")
                    if not isinstance(fingerprint, str):
                        raise c.ShareError("A preview fingerprint is required")
                    r.note_text(data.get("note"))
                    saved = plans.get(fingerprint)
                    if saved is None or "promote" not in saved:
                        raise c.ShareError("Preview the move again before applying")
                    plans.pop(fingerprint)
                    result = me.promote_apply(saved, data["note"], data.get("push") is True)
                elif path == "/api/starter-plan":
                    result = r.starter_plan(
                        target,
                        data["source"],
                        data.get("assets", []),
                        data.get("clear", False),
                        mode_of(data),
                    )
                    plans.clear()
                    plans[result["fingerprint"]] = result
                elif path == "/api/starter-apply":
                    fingerprint = data.get("fingerprint")
                    if not isinstance(fingerprint, str):
                        raise c.ShareError("A preview fingerprint is required")
                    r.note_text(data.get("note"))
                    saved = plans.get(fingerprint)
                    if saved is None or "starter" not in saved:
                        raise c.ShareError("Preview the starter set again before applying")
                    plans.pop(fingerprint)
                    result = r.starter_apply(saved, data["note"], data.get("push") is True)
                elif path == "/api/propose-plan":
                    result = r.propose_plan(
                        target,
                        data["source"],
                        data["kind"],
                        data["name"],
                        data.get("path") or None,
                        data.get("mode", "direct"),
                    )
                    plans.clear()
                    plans[result["fingerprint"]] = result
                elif path == "/api/propose-apply":
                    fingerprint = data.get("fingerprint")
                    if not isinstance(fingerprint, str):
                        raise c.ShareError("A preview fingerprint is required")
                    r.note_text(data.get("note"))
                    saved = plans.get(fingerprint)
                    if saved is None or "propose" not in saved:
                        raise c.ShareError("Preview the proposal again before applying")
                    plans.pop(fingerprint)
                    result = r.propose_apply(saved, data["note"], data.get("push") is True)
                elif path == "/api/keep-plan":
                    link_team = data.get("link_team", True)
                    if not isinstance(link_team, bool):
                        raise c.ShareError("link_team must be true or false")
                    result = e.plan(
                        target,
                        action="keep",
                        kind=data["kind"],
                        asset=data["name"],
                        source=data["source"],
                        path=data.get("path") or None,
                        new_name=data["new_name"],
                        link_team=link_team,
                    )
                    plans.clear()
                    plans[result["fingerprint"]] = result
                elif path == "/api/adopt-plan":
                    replace = data.get("replace", False)
                    if not isinstance(replace, bool):
                        raise c.ShareError("replace must be true or false")
                    result = e.plan(
                        target,
                        action="adopt",
                        kind=data["kind"],
                        asset=data["name"],
                        source=data["source"],
                        replace=replace,
                        path=data.get("path"),
                    )
                    plans.clear()
                    plans[result["fingerprint"]] = result
                elif path == "/api/share-apply":
                    fingerprint = data.get("fingerprint")
                    if not isinstance(fingerprint, str):
                        raise c.ShareError("A preview fingerprint is required")
                    r.note_text(data.get("note"))
                    saved = plans.get(fingerprint)
                    if saved is None or "share" not in saved:
                        raise c.ShareError("Preview the share again before applying")
                    plans.pop(fingerprint)
                    result = r.share_apply(
                        saved,
                        data["note"],
                        bool(data.get("push")),
                        adopt=data.get("adopt") is True,
                    )
                elif path == "/api/import-folder":
                    result = stages.open_folder(data.get("path"))
                elif path == "/api/import-git":
                    result = stages.clone(data.get("url"), data.get("ref") or None)
                elif path == "/api/import-plan":
                    stage = stages.get(data.get("stage"))
                    result = im.plan(
                        target,
                        data["source"],
                        stage["root"],
                        data["items"],
                        stage=data["stage"],
                        label=stage["label"],
                        mode=mode_of(data),
                        repository=bool(stage.get("repository")),
                    )
                    plans.clear()
                    plans[result["fingerprint"]] = result
                elif path == "/api/import-apply":
                    fingerprint = data.get("fingerprint")
                    if not isinstance(fingerprint, str):
                        raise c.ShareError("A preview fingerprint is required")
                    r.note_text(data.get("note"))
                    saved = plans.get(fingerprint)
                    if saved is None or "import" not in saved:
                        raise c.ShareError("Preview the import again before applying")
                    # The staged files must still be the ones this session previewed.
                    if stages.get(saved["import"]["stage"])["root"] != saved["import"]["root"]:
                        raise c.ShareError("Preview the import again before applying")
                    plans.pop(fingerprint)
                    result = im.apply(saved, data["note"], bool(data.get("push")))
                elif path == "/api/commit-edits":
                    result = r.commit_edits(
                        target,
                        data["source"],
                        data["kind"],
                        data["name"],
                        data.get("note"),
                        bool(data.get("push")),
                        data["fingerprint"],
                    )
                    plans.clear()
                elif path == "/api/profile":
                    result = i.import_profile(target, data["source"], data["profile"], apply=True)
                    plans.clear()
                elif path == "/api/projects":
                    folder = c.root(data["path"])
                    taken = set(i.repositories()["repositories"])
                    result = i.register(data.get("name") or c.suggest_name(folder, taken), folder)
                elif path == "/api/tools":
                    result = c.set_tools(target, data["tools"])
                    plans.clear()
                elif path == "/api/source":
                    result = c.add_source(
                        target, data["name"], data["path"], data.get("layout") or None
                    )
                    plans.clear()
                elif path == "/api/selections":
                    selections = data["selections"]
                    if not isinstance(selections, list) or len(selections) > 500:
                        raise c.ShareError("Expected at most 500 selections")
                    # Validate the full batch before the first configuration write.
                    cfg = c.effective(target)
                    source = data["source"]
                    e.source_path(cfg, source)
                    direction = data["direction"]
                    if direction not in ("inbound", "outbound"):
                        raise c.ShareError("Unknown direction")
                    rows: dict[str, dict[str, Any]] = {}
                    for row in selections:
                        kind, name = row["kind"], row["name"]
                        c.asset_name(kind, name)
                        if row.get("alias"):
                            c.asset_name(kind, row["alias"])
                        key = f"{source}/{kind}/{name}"
                        entry = {"source": source, "kind": kind, "name": name}
                        if row.get("alias"):
                            entry["alias"] = row["alias"]
                        if direction == "outbound":
                            asset = c.safe_path(target, row["path"])
                            e.validate_asset(asset, kind, outbound=True)
                            entry["path"] = row["path"]
                        if not row.get("enabled", True):
                            entry = {"enabled": False}
                        if key in rows:
                            prior = rows[key]
                            if prior.get("enabled", True) and entry.get("enabled", True):
                                raise c.ShareError(f"Choose only one original for {key}")
                            if prior.get("enabled", True):
                                continue
                        rows[key] = entry
                    c.init(target)
                    directory = c.config_dir(target)
                    with c.locks([directory]):
                        local = c.read_json(directory / "sharing.json")
                        c.validate(local, directory / "sharing.json")
                        # The prior entries let the window undo a save the user cancels.
                        previous = {key: local[direction].get(key) for key in rows}
                        local[direction].update(rows)
                        c.validate(local, directory / "sharing.json")
                        c.atomic_json(directory / "sharing.json", local)
                    plans.clear()
                    result = {"saved": len(rows), "previous": previous}
                elif path == "/api/restore-selections":
                    direction = data["direction"]
                    if direction not in ("inbound", "outbound"):
                        raise c.ShareError("Unknown direction")
                    previous = data["previous"]
                    if not isinstance(previous, dict) or len(previous) > 500:
                        raise c.ShareError("Expected at most 500 selections")
                    directory = c.config_dir(target)
                    with c.locks([directory]):
                        local = c.read_json(directory / "sharing.json")
                        c.validate(local, directory / "sharing.json")
                        for key, entry in previous.items():
                            if entry is None:
                                local[direction].pop(key, None)
                            else:
                                local[direction][key] = entry
                        c.validate(local, directory / "sharing.json")
                        c.atomic_json(directory / "sharing.json", local)
                    plans.clear()
                    result = {"restored": len(previous)}
                elif path == "/api/plan" and data.get("power"):
                    result = i.power_plan(
                        target,
                        data["action"],
                        data["source"],
                        data["name"],
                        bool(data.get("allow_code")),
                    )
                    if not data.get("batch"):
                        plans.clear()
                    plans[result["fingerprint"]] = result
                elif path == "/api/plan":
                    result = e.plan(
                        target,
                        direction=data.get("direction", "inbound"),
                        action=data.get("action", "sync"),
                        kind=data.get("kind"),
                        asset=data.get("name"),
                        archive_id=data.get("id"),
                        path=data.get("path"),
                    )
                    # A batch previews several projects; keep the earlier plans of it.
                    if not data.get("batch"):
                        plans.clear()
                    plans[result["fingerprint"]] = result
                elif path == "/api/apply":
                    fingerprint = data.get("fingerprint")
                    if not isinstance(fingerprint, str):
                        raise c.ShareError("A preview fingerprint is required")
                    saved = plans.get(fingerprint)
                    if saved is None:
                        raise c.ShareError("Preview again before applying")
                    if any(
                        k in saved
                        for k in ("share", "import", "propose", "starter", "promote", "disconnect")
                    ):
                        # A share or import also commits; it is applied only with its note.
                        raise c.ShareError("Apply a share or import preview with its note")
                    plans.pop(fingerprint)
                    is_power = saved["spec"].get("type") == "power"
                    result = i.power_apply(saved) if is_power else e.apply(saved)
                else:
                    self.respond({"error": "Not found"}, 404)
                    return
                self.respond(result)
            except (c.ShareError, OSError, ValueError, KeyError, TypeError) as exc:
                self.respond(error_body(exc), 400)

    server = Server(("127.0.0.1", port), Handler)
    server.timeout = 1
    server.stages = stages
    return server, token


RESTART = "CAPTAIN_SHARE_RESTART"


def restarted():
    """(port, token) handed over by "Restart window" (ADR 015), or (None, None). They
    come in a private file that is read once and deleted, so neither the process
    environment nor Git and other programs this server starts ever hold the token."""
    path = os.environ.pop(RESTART, None)
    if not path:
        return None, None
    file = Path(path)
    try:
        if file.parent != Path(tempfile.gettempdir()) or file.is_symlink():
            return None, None
        data = json.loads(file.read_text())
    except (OSError, ValueError):
        return None, None
    finally:
        file.unlink(missing_ok=True)
    port, token = data.get("port"), data.get("token")
    if isinstance(port, int) and isinstance(token, str) and token:
        return port, token
    return None, None


def windows_file():
    """Open windows by folder (port, process, token), in the private lock folder."""
    return c.lock_dir() / "windows.json"


def open_window(folder):
    """The link of a window already serving `folder`, or None. A window counts only
    while its process lives and answers with its token."""
    try:
        row = (c.read_json(windows_file(), {}) or {}).get(str(folder))
    except c.ShareError:
        return None
    if not isinstance(row, dict) or not isinstance(row.get("port"), int):
        return None
    try:
        os.kill(int(row.get("pid", 0)), 0)
    except (OSError, ValueError):
        return None
    port, token = row["port"], str(row.get("token", ""))
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/self",
        headers={"X-Captain-Token": token, "Host": f"127.0.0.1:{port}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as reply:
            if reply.status != 200:
                return None
    except (OSError, ValueError):
        return None
    return f"http://127.0.0.1:{port}/#token={token}"


def remember_window(folder, port, token, forget=False):
    path = windows_file()
    with c.locks([path]):
        try:
            data = c.read_json(path, {}) or {}
        except c.ShareError:
            data = {}
        if forget:
            if data.get(str(folder), {}).get("pid") == os.getpid():
                data.pop(str(folder))
        else:
            data[str(folder)] = {"port": port, "pid": os.getpid(), "token": token}
        c.atomic_json(path, data)
        path.chmod(0o600)


def serve(target, port=0, open_browser=False, new=False):
    folder = c.root(target)
    # After "Restart window" the same address and token come back, so the open
    # page reconnects.
    again, token = restarted()
    if again is not None:
        port, open_browser = again, False
    elif not new:
        # One window per folder: open the one that runs already.
        url = open_window(folder)
        if url:
            print(f"Captain Share is already open for {folder}\n{url}", flush=True)
            webbrowser.open(url)
            return
    server, token = make_server(target, port, token)
    url = f"http://127.0.0.1:{server.server_port}/#token={token}"
    print(f"Captain Share\nTarget: {folder}\n{url}\nPress Ctrl-C to stop.", flush=True)
    remember_window(folder, server.server_port, token)
    if open_browser:
        webbrowser.open(url)

    def stop(*_):
        # pkill sends SIGTERM: stop like Ctrl-C, so staged downloads are removed and
        # the window is taken off the list of open windows.
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        remember_window(folder, server.server_port, token, forget=True)
        server.server_close()
