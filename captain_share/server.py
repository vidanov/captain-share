"""Loopback-only UI with per-launch token, origin checks and the same engine."""

from __future__ import annotations

import json
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from . import config as c
from . import engine as e

WEB = Path(__file__).parent / "web"


def make_server(target, port=0):
    target = c.root(target)
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
                if parsed.path == "/api/state":
                    result = e.status(target)
                    result["candidates"] = e.scan(target)
                    result["kinds"] = list(c.KINDS)
                    self.respond(result)
                elif parsed.path == "/api/catalog":
                    source = parse_qs(parsed.query).get("source", [""])[0]
                    self.respond(e.catalog(target, source))
                elif parsed.path in ("/", "/app.js", "/style.css"):
                    filename = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[
                        parsed.path
                    ]
                    mime = {
                        "/": "text/html; charset=utf-8",
                        "/app.js": "text/javascript",
                        "/style.css": "text/css",
                    }[parsed.path]
                    self.respond((WEB / filename).read_bytes(), content_type=mime)
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
                if path == "/api/source":
                    result = c.add_source(
                        target, data["name"], data["path"], data.get("layout", "ip")
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
                elif path == "/api/plan":
                    result = e.plan(
                        target,
                        direction=data.get("direction", "inbound"),
                        action=data.get("action", "sync"),
                        kind=data.get("kind"),
                        asset=data.get("name"),
                        archive_id=data.get("id"),
                    )
                    plans.clear()
                    plans[result["fingerprint"]] = result
                elif path == "/api/apply":
                    fingerprint = data.get("fingerprint")
                    if not isinstance(fingerprint, str):
                        raise c.ShareError("A preview fingerprint is required")
                    saved = plans.pop(fingerprint, None)
                    if saved is None:
                        raise c.ShareError("Preview again before applying")
                    result = e.apply(saved)
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
