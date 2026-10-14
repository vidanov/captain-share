"""Loopback-only UI with per-launch token, origin checks and the same engine."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import config as c
from . import engine as e
from . import integrations as i
from . import repositories as r

WEB = Path(__file__).parent / "web"


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
                found = sum(1 for item in folder.iterdir() if e.candidate(item, kind))
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
    for source in sorted(project_cfg["sources"]):
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
        head, made, changed, edited = None, {}, set(), {}
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
            made = r.history(lib["repo"], lib["prefix"])
            edited = r.edits(lib["repo"], lib["prefix"], git["changes"])
        before = baseline.get(source)
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
            new = before is not None and key not in before.get("assets", [])
            updated = (have_user or have_project) and (kind, name) in changed
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


def share_candidates(target):
    """What this place could share: local originals not in a library, library assets
    with uncommitted edits, and the libraries that can record a share."""
    cfg = c.effective(target)
    names, local, edited = [], [], []
    for source in sorted(cfg["sources"]):
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
        local.append(
            {
                "kind": row["kind"],
                "name": row["name"],
                "path": row["path"],
                "description": e.describe(target / row["path"], row["kind"]),
                "exists_in": found[0][0] if found else None,
            }
        )
    return {"local": local, "edited": edited, "libraries": names}


def make_server(target, port=0):
    startup = c.root(target)
    token = secrets.token_urlsafe(32)
    plans: dict[str, dict[str, Any]] = {}
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
                elif parsed.path == "/api/share-candidates":
                    self.respond(share_candidates(target))
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
                self.respond({"error": str(exc)}, 400)

        def do_POST(self):
            if not self.authorized():
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
                elif path == "/api/clone":
                    result = r.clone(
                        target,
                        data["name"],
                        data["url"],
                        data["path"],
                        data.get("layout") or None,
                        create_parent=data.get("create_parent") is True,
                    )
                    plans.clear()
                elif path == "/api/git":
                    result = r.operate(
                        target, data.get("source", ""), data["action"], data["fingerprint"]
                    )
                    plans.clear()
                elif path == "/api/fetch":
                    result = r.fetch(target, data["source"])
                elif path == "/api/share-plan":
                    result = r.share_plan(
                        target, data["source"], data["kind"], data["name"], data["path"]
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
                    result = r.share_apply(saved, data["note"], bool(data.get("push")))
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
                    result = i.power_plan(target, data["action"], data["source"], data["name"])
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
                    if "share" in saved:
                        # A share also commits; it is applied only with its note.
                        raise c.ShareError("Apply a share preview with its note")
                    plans.pop(fingerprint)
                    is_power = saved["spec"].get("type") == "power"
                    result = i.power_apply(saved) if is_power else e.apply(saved)
                else:
                    self.respond({"error": "Not found"}, 404)
                    return
                self.respond(result)
            except (c.ShareError, OSError, ValueError, KeyError, TypeError) as exc:
                self.respond({"error": str(exc)}, 400)

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 1
    return server, token


def serve(target, port=0, open_browser=False):
    server, token = make_server(target, port)
    url = f"http://127.0.0.1:{server.server_port}/#token={token}"
    print(f"Captain Share\nTarget: {c.root(target)}\n{url}\nPress Ctrl-C to stop.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
