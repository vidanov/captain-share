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


def make_server(target, port=0):
    startup = c.root(target)
    token = secrets.token_urlsafe(32)
    plans: dict[str, dict[str, Any]] = {}

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
                        target, data["name"], data["url"], data["path"], data.get("layout") or None
                    )
                    plans.clear()
                elif path == "/api/git":
                    result = r.operate(
                        target, data.get("source", ""), data["action"], data["fingerprint"]
                    )
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
                        local[direction].update(rows)
                        c.validate(local, directory / "sharing.json")
                        c.atomic_json(directory / "sharing.json", local)
                    plans.clear()
                    result = {"saved": len(rows)}
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
                    saved = plans.pop(fingerprint, None)
                    if saved is None:
                        raise c.ShareError("Preview again before applying")
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
