"""One deterministic plan/apply engine for CLI and loopback UI."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config as c

MAX_ASSET_BYTES = 64 * 1024 * 1024
# Regenerable local files: never listed, compared, or copied, and never blocking.
HOUSEKEEPING = {".DS_Store", ".gitkeep", "__pycache__"}
FOLDER_KINDS = {"skills", "powers"}
POWER_FIELDS = ("name", "version", "description", "keywords")


def ignored(path, base):
    return any(part in HOUSEKEEPING for part in path.relative_to(base).parts)


def candidate(path, kind):
    if path.name.startswith("."):
        return False
    if kind in FOLDER_KINDS:
        return path.is_dir() or path.is_symlink()
    matches = (
        path.name == "copilot-instructions.md"
        if kind == "copilot"
        else path.name.endswith(c.SUFFIXES[kind])
    )
    return matches and (path.is_file() or path.is_symlink())


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def snapshot(path, modes=True):
    if path.is_symlink():
        return {"type": "link", "target": os.readlink(path)}
    if not path.exists():
        return None
    files = [path] if path.is_file() else sorted(path.rglob("*"))
    rows, size = [], 0
    for p in files:
        if p != path and ignored(p, path):
            continue
        rel = "." if p == path else p.relative_to(path).as_posix()
        if p.is_symlink():
            rows.append([rel, "link", os.readlink(p)])
        elif p.is_file():
            size += p.stat().st_size
            if size > MAX_ASSET_BYTES:
                raise c.ShareError(f"Asset exceeds the 64 MiB review limit: {path}")
            rows.append(
                [
                    rel,
                    "file",
                    stat.S_IMODE(p.stat().st_mode) if modes else None,
                    hashlib.sha256(p.read_bytes()).hexdigest(),
                ]
            )
        elif p.is_dir():
            rows.append([rel, "dir"])
        else:
            raise c.ShareError(f"Special files are not shareable: {p}")
    return {"type": "directory" if path.is_dir() else "file", "hash": digest(rows)}


def validate_asset(path, kind, outbound=False):
    if path.is_symlink():
        raise c.ShareError(f"Use the original asset, not a symlink: {path}")
    if kind == "skills":
        if not path.is_dir() or not (path / "SKILL.md").is_file():
            raise c.ShareError(f"Skill lacks SKILL.md: {path}")
    elif kind == "powers":
        validate_power(path, outbound)
    elif not path.is_file():
        raise c.ShareError(f"Asset is not a file: {path}")
    files = sorted(path.rglob("*")) if path.is_dir() else [path]
    for p in files:
        if p != path and ignored(p, path):
            continue
        if p.is_symlink():
            raise c.ShareError(f"Asset contains a symlink: {p}")
        if p.name in {".git", ".env", "node_modules"}:
            raise c.ShareError(f"Remove local/generated material before sharing: {p.name}")
        if outbound and p.is_file():
            if p.stat().st_size > MAX_ASSET_BYTES:
                raise c.ShareError("File exceeds 64 MiB limit")
            data = p.read_bytes()
            patterns = [
                rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
                rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
                rb"\b(?:ghp_|github_pat_|glpat-)[A-Za-z0-9_-]{16,}",
                rb"(?:/Users/|/home/)[A-Za-z0-9_.-]+/",
            ]
            if any(re.search(pat, data) for pat in patterns):
                raise c.ShareError(
                    f"Outbound review found a credential or personal path in {p.name}; edit the asset before sharing"
                )
    snapshot(path)


def frontmatter(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---" or "---" not in [x.strip() for x in lines[1:]]:
        return None
    fields = {}
    for line in lines[1 : 1 + [x.strip() for x in lines[1:]].index("---")]:
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith((" ", "\t")):
            fields[key.strip()] = value.strip().strip("\"'")
    return fields


def describe(path, kind):
    """One-line description of an asset for people browsing a library."""
    try:
        if kind in ("skills", "powers") and path.is_dir():
            manifest = path / "plugin.json"
            if kind == "powers" and manifest.is_file():
                return str(json.loads(manifest.read_text()).get("description", ""))[:300]
            path = path / ("SKILL.md" if kind == "skills" else "POWER.md")
        if path.suffix == ".json":
            return str(json.loads(path.read_text()).get("description", ""))[:300]
        text = path.read_text(errors="replace")
    except (OSError, ValueError, AttributeError):
        return ""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---" and "---" in [x.strip() for x in lines[1:]]:
        head = lines[1 : 1 + [x.strip() for x in lines[1:]].index("---")]
        for index, line in enumerate(head):
            if line.startswith("description:"):
                value = line.partition(":")[2].strip()
                if value in ("", ">", ">-", "|", "|-"):
                    # Folded or literal block: join the indented lines that follow.
                    parts = []
                    for follow in head[index + 1 :]:
                        if follow and not follow.startswith((" ", "\t")):
                            break
                        parts.append(follow.strip())
                    value = " ".join(p for p in parts if p)
                return value.strip("\"'")[:300]
        lines = lines[1 + len(head) + 1 :]
    for line in lines:
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:300]
    return ""


def validate_power(path, outbound=False):
    """Agent Plugins 1.0.0 plugin.json, or a legacy POWER.md, named like its folder."""
    if not path.is_dir():
        raise c.ShareError(f"A power is a folder: {path}")
    manifest, legacy = path / "plugin.json", path / "POWER.md"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text())
        except ValueError as exc:
            raise c.ShareError(f"Invalid plugin.json in {path.name}: {exc}") from exc
        missing = [f for f in POWER_FIELDS if not data.get(f)] if isinstance(data, dict) else []
        if not isinstance(data, dict) or missing:
            raise c.ShareError(
                f"plugin.json in {path.name} lacks {', '.join(missing) or 'an object'}"
            )
        if not isinstance(data.get("author"), dict) or not data["author"].get("name"):
            raise c.ShareError(f"plugin.json in {path.name} needs author.name")
        if not isinstance(data["keywords"], list):
            raise c.ShareError(f"plugin.json keywords must be a list in {path.name}")
        named = data["name"]
    elif legacy.is_file():
        fields = frontmatter(legacy.read_text())
        if fields is None:
            raise c.ShareError(f"POWER.md in {path.name} needs YAML frontmatter")
        named = fields.get("name", path.name)
    else:
        raise c.ShareError(f"Power lacks plugin.json or POWER.md: {path}")
    if named != path.name:
        # Kiro installs by manifest name; a mismatch silently replaces another power.
        raise c.ShareError(f"Power name {named!r} must match its folder {path.name!r}")
    servers = path / "mcp.json"
    if servers.is_file():
        try:
            mcp = json.loads(servers.read_text())
        except ValueError as exc:
            raise c.ShareError(f"Invalid mcp.json in {path.name}: {exc}") from exc
        if not isinstance(mcp, dict) or not isinstance(mcp.get("mcpServers"), dict):
            raise c.ShareError(f"mcp.json in {path.name} needs an mcpServers object")
        if outbound:
            for server, spec in mcp["mcpServers"].items():
                env = spec.get("env", {}) if isinstance(spec, dict) else {}
                literal = [k for k, v in env.items() if not re.fullmatch(r"\$\{\w+\}", str(v))]
                if literal:
                    raise c.ShareError(
                        f"mcp.json server {server} sets {', '.join(literal)} literally; "
                        "use ${VAR} references before sharing"
                    )


def git(target, *args, check=True):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    proc = subprocess.run(
        ["git", "-C", str(target), *args], capture_output=True, env=env, timeout=15
    )
    if check and proc.returncode:
        raise c.ShareError(proc.stderr.decode(errors="replace").strip())
    return proc


def git_root(target):
    p = git(target, "rev-parse", "--show-toplevel", check=False)
    return Path(os.fsdecode(p.stdout).strip()).resolve() if p.returncode == 0 else None


def tracked(target, path):
    repo = git_root(target)
    if repo is None:
        return False
    try:
        rel = path.relative_to(repo).as_posix()
    except ValueError:
        return False
    return bool(git(repo, "ls-files", "-z", "--", rel).stdout)


def source_path(cfg, sid, kind=None, asset=None):
    spec = cfg["sources"].get(sid)
    if not spec or not spec.get("path"):
        raise c.ShareError(f"Source {sid!r} is not bound locally; add its checkout path")
    base = c.root(spec["path"])
    if spec.get("layout", "ip") == "ip":
        base = c.safe_path(base, "ip")
    if not base.is_dir():
        raise c.ShareError(f"Library root is missing: {base}")
    return c.safe_path(base, f"{kind}/{asset}") if kind else base


def locate(cfg, asset, source=None, kind=None):
    """Library assets named `asset` (a suffix such as .md may be omitted), as
    (source, kind, name), across the bound sources of an effective configuration."""
    found = []
    for sid in sorted(cfg["sources"]):
        if source is not None and sid != source:
            continue
        try:
            base = source_path(cfg, sid)
        except c.ShareError:
            continue
        for k in c.KINDS:
            if kind is not None and k != kind:
                continue
            names = [asset]
            if k in c.SUFFIXES and not asset.endswith(c.SUFFIXES[k]):
                names.append(asset + c.SUFFIXES[k])
            for value in names:
                try:
                    c.asset_name(k, value)
                    item = c.safe_path(base, f"{k}/{value}")
                except c.ShareError:
                    continue
                if candidate(item, k):
                    found.append((sid, k, value))
    return found


def destination_state(target, rel, src, managed):
    """Read-only answer to: what would receiving src at rel find there now?"""
    p = c.safe_path(target, rel, leaf_link=True)
    if not os.path.lexists(p):
        return "absent"
    if managed is None:
        return "blocked"
    if p.is_symlink() and os.readlink(p) == str(src) == managed.get("source_path"):
        return "installed"
    return "changed"


def catalog(target, source):
    target = c.root(target)
    cfg = c.effective(target)
    base = source_path(cfg, source)
    installed = state(target)["inbound"]
    contributed = {row["path"] for row in cfg["outbound"].values()}
    rows = []
    for kind in c.KINDS:
        directory = c.safe_path(base, kind)
        if not directory.is_dir():
            continue
        for item in sorted(directory.iterdir()):
            if not candidate(item, kind):
                continue
            row = {"source": source, "kind": kind, "name": item.name, "path": str(item)}
            row["description"] = describe(item, kind)
            try:
                c.asset_name(kind, item.name)
                validate_asset(item, kind)
                row["status"] = "available"
            except c.ShareError as exc:
                row.update(status="invalid", error=str(exc))
            selected = cfg["inbound"].get(f"{source}/{kind}/{item.name}", {})
            alias = selected.get("alias", item.name)
            row["destinations"] = []
            for tool, folder in c.destinations(kind, cfg["tools"]):
                rel = f"{folder}/{alias}"
                try:
                    found = destination_state(target, rel, item, installed.get(rel))
                except c.ShareError as exc:
                    found, row["error"] = "blocked", str(exc)
                if found == "blocked" and rel in contributed:
                    found = "contributed"
                row["destinations"].append({"tool": tool, "path": rel, "state": found})
            rows.append(row)
    return rows


def scan(target):
    target = c.root(target)
    rows, seen = [], set()
    for kind in c.KINDS:
        tool_folders = {folder: tool for tool, folder in c.DESTINATIONS[kind].items()}
        directories = [*tool_folders, f"ip/{kind}"]
        if kind in ("skills", "agents", "prompts", "steering", "powers"):
            directories.append(kind)
        for directory in directories:
            folder = c.safe_path(target, directory)
            if not folder.is_dir():
                continue
            for p in sorted(folder.iterdir()):
                if p in seen or not candidate(p, kind):
                    continue
                try:
                    c.asset_name(kind, p.name)
                except c.ShareError:
                    continue
                seen.add(p)
                row = {"kind": kind, "name": p.name, "path": p.relative_to(target).as_posix()}
                if directory in tool_folders:
                    row["tool"] = tool_folders[directory]
                if p.is_symlink():
                    row.update(status="linked", target=os.readlink(p))
                else:
                    try:
                        validate_asset(p, kind, outbound=True)
                        row["status"] = "local"
                    except c.ShareError as exc:
                        row.update(status="invalid", error=str(exc))
                rows.append(row)
    return rows


def state(target):
    p = c.safe_path(target, ".captain/sharing-state.json")
    data = c.read_json(p, {"version": 1, "target": str(target), "inbound": {}, "outbound": {}})
    if not isinstance(data, dict) or data.get("version") != 1 or data.get("target") != str(target):
        raise c.ShareError(
            "Installation state belongs to another folder or has an unsupported version"
        )
    for direction in ("inbound", "outbound"):
        if not isinstance(data.get(direction), dict):
            raise c.ShareError("Invalid installation state")
    for rel, row in data["inbound"].items():
        c.asset_name(row["kind"], row["name"])
        if not any(
            rel == f"{dest}/{row.get('alias', row.get('name', ''))}"
            for dest in c.folders(row["kind"])
        ):
            raise c.ShareError(f"Invalid managed destination: {rel}")
        if not isinstance(row.get("source_path"), str):
            raise c.ShareError("Invalid managed source path")
    return data


def exclude_change(target, destinations):
    repo = git_root(target)
    if repo is None:
        return None
    proc = git(repo, "rev-parse", "--git-path", "info/exclude")
    p = Path(os.fsdecode(proc.stdout).strip())
    if not p.is_absolute():
        p = repo / p
    p = p.absolute()
    c.safe_path(Path(p.anchor), str(p.relative_to(p.anchor)))
    prefix = target.relative_to(repo).as_posix()
    prefix = "" if prefix == "." else prefix + "/"
    rules = [
        f"/{prefix}.captain/sharing.local.json",
        f"/{prefix}.captain/sharing-state.json",
        f"/{prefix}.captain/archive/",
    ]
    rules += [f"/{prefix}{rel}" for rel in destinations]
    old = p.read_text() if p.exists() else ""
    missing = [r for r in rules if r not in old.splitlines()]
    if not missing:
        return {"path": str(p), "before": snapshot(p), "text": old, "changed": False}
    return {
        "path": str(p),
        "before": snapshot(p),
        "text": old.rstrip("\n") + "\n# captain-share local entries\n" + "\n".join(missing) + "\n",
        "changed": True,
    }


def one_off(selections):
    """Validate explicit outbound selections that replace the saved ones for one plan."""
    if not isinstance(selections, dict) or not selections:
        raise c.ShareError("Expected at least one explicit outbound selection")
    c.validate({"version": 1, "outbound": dict(selections)}, "explicit selection")
    if any(row.get("enabled", True) is False for row in selections.values()):
        raise c.ShareError("Explicit selections must be enabled")
    return selections


def plan(
    target,
    direction="inbound",
    action="sync",
    kind=None,
    asset=None,
    archive_id=None,
    path=None,
    selections=None,
    source=None,
    replace=False,
):
    """Preview changes. `selections` replaces the saved outbound selections for a
    one-off copy (sharing one asset) without writing them to configuration.
    `adopt` swaps local copies of one asset for links to its library original."""
    target = c.root(target)
    if direction not in ("inbound", "outbound", "both"):
        raise c.ShareError("Unknown direction")
    if action not in ("sync", "remove", "restore", "adopt"):
        raise c.ShareError("Unknown plan action")
    if action == "adopt":
        c.asset_name(kind, asset)
        c.name(source)
        if not isinstance(replace, bool):
            raise c.ShareError("replace must be true or false")
        if path is not None:
            c.safe_path(target, path, leaf_link=True)
    elif source is not None or replace:
        raise c.ShareError("source and replace apply to adopt plans only")
    if selections is not None:
        if direction != "outbound" or action != "sync":
            raise c.ShareError("Explicit selections apply to outbound copies only")
        one_off(selections)
    c.safe_path(target, ".captain/archive")
    c.safe_path(target, ".captain/.gitignore")
    cfg, old = c.effective(target), state(target)
    next_state = json.loads(json.dumps(old))
    changes, observations, errors, conflicts = [], [], [], []
    config_update = None

    def conflict(message, path=None, key=None, reason="error"):
        # Strings stay the stable CLI contract; structured rows let interfaces offer fixes.
        errors.append(message)
        conflicts.append({"message": message, "path": path, "key": key, "reason": reason})

    cfg_path = c.safe_path(target, ".captain/sharing.json")
    local_cfg = c.read_json(cfg_path, c.blank())
    c.validate(local_cfg, cfg_path)
    for p, _, _ in c.layers(target):
        observations.append({"path": str(p), "snapshot": snapshot(p)})
    observations.append({"path": str(cfg_path), "snapshot": snapshot(cfg_path)})
    observations.append(
        {
            "path": str(target / ".captain/sharing-state.json"),
            "snapshot": snapshot(target / ".captain/sharing-state.json"),
        }
    )

    def observe(p):
        snap = snapshot(p)
        observations.append({"path": str(p), "snapshot": snap})
        return snap

    def archive(p, row=None):
        changes.append({"op": "archive", "path": str(p), "selection": row})

    if action == "sync":
        if direction in ("inbound", "both"):
            desired = {}
            contributed = {r["path"]: k for k, r in cfg["outbound"].items()}
            for key, row in cfg["inbound"].items():
                try:
                    alias = row.get("alias", row["name"])
                    targets = c.destinations(row["kind"], cfg["tools"])
                    if not targets:
                        readers = ", ".join(c.DESTINATIONS[row["kind"]])
                        raise c.ShareError(
                            f"No enabled tool reads {row['kind']} ({key}); enable {readers} or deselect it"
                        )
                    src = source_path(cfg, row["source"], row["kind"], row["name"])
                    validate_asset(src, row["kind"])
                    observe(src)
                except (c.ShareError, OSError) as exc:
                    conflict(str(exc), key=key)
                    continue
                for tool, folder in targets:
                    rel = f"{folder}/{alias}"
                    try:
                        if rel in desired:
                            raise c.ShareError(f"Two selections target {rel}")
                        dest = c.safe_path(target, rel, leaf_link=True)
                        if tracked(target, dest):
                            raise c.ShareError(f"Destination is Git-tracked: {rel}")
                        existing = observe(dest)
                        previous = old["inbound"].get(rel)
                        if existing and not previous:
                            if rel in contributed:
                                conflict(
                                    f"{rel} is this folder's original, contributed as {contributed[rel]}; "
                                    "keep contributing it or archive it before receiving the library copy",
                                    rel,
                                    key,
                                    "contributed",
                                )
                            else:
                                conflict(
                                    f"Existing unmanaged asset at {rel}; archive it explicitly before connecting",
                                    rel,
                                    key,
                                    "unmanaged",
                                )
                            continue
                        if (
                            previous
                            and existing is not None
                            and existing != {"type": "link", "target": previous["source_path"]}
                        ):
                            raise c.ShareError(
                                f"Managed link changed or disappeared: {rel}; restore it before syncing"
                            )
                        if existing != {"type": "link", "target": str(src)}:
                            if existing:
                                archive(dest, previous)
                            changes.append({"op": "link", "path": str(dest), "source": str(src)})
                        desired[rel] = {**row, "source_path": str(src), "key": key, "tool": tool}
                    except (c.ShareError, OSError) as exc:
                        conflict(str(exc), rel, key)
            for rel, previous in old["inbound"].items():
                if rel in desired:
                    continue
                try:
                    dest = c.safe_path(target, rel, leaf_link=True)
                    if tracked(target, dest):
                        raise c.ShareError(f"Managed link is now Git-tracked: {rel}")
                    observed = observe(dest)
                    if observed is None:
                        continue
                    if observed != {"type": "link", "target": previous["source_path"]}:
                        raise c.ShareError(f"Managed link changed: {rel}")
                    archive(dest, previous)
                except (c.ShareError, OSError) as exc:
                    conflict(str(exc), rel, previous.get("key"))
            next_state["inbound"] = desired
        if direction in ("outbound", "both"):
            destinations = set()
            outbound = cfg["outbound"] if selections is None else selections
            for key, row in outbound.items():
                try:
                    src = c.safe_path(target, row["path"])
                    validate_asset(src, row["kind"], outbound=True)
                    dest = source_path(cfg, row["source"], row["kind"], row["name"])
                    if dest in destinations:
                        raise c.ShareError(f"Duplicate outbound destination: {dest}")
                    destinations.add(dest)
                    if src == dest or src in dest.parents or dest in src.parents:
                        raise c.ShareError("Outbound source and destination overlap")
                    src_snap, dest_snap = observe(src), observe(dest)
                    previous = old["outbound"].get(key)
                    if dest_snap != src_snap:
                        if dest_snap and (
                            not previous
                            or previous.get("path") != str(dest)
                            or previous.get("snapshot") != dest_snap
                        ):
                            raise c.ShareError(
                                f"Shared original already exists or was edited independently: {row['name']}"
                            )
                        if dest_snap:
                            owner = c.root(cfg["sources"][row["source"]]["path"])
                            c.safe_path(owner, ".captain/archive")
                            c.safe_path(owner, ".captain/.gitignore")
                            if tracked(owner, owner / ".captain/archive"):
                                raise c.ShareError("Library archive is Git-tracked")
                            # Backups live beside the shared original, not in the publishing project.
                            changes.append(
                                {
                                    "op": "archive",
                                    "path": str(dest),
                                    "archive_root": str(
                                        c.root(cfg["sources"][row["source"]]["path"])
                                    ),
                                    "selection": None,
                                }
                            )
                        changes.append({"op": "copy", "path": str(dest), "source": str(src)})
                    if dest_snap != src_snap or (
                        previous
                        and previous.get("path") == str(dest)
                        and previous.get("snapshot") == dest_snap
                    ):
                        next_state["outbound"][key] = {"path": str(dest), "snapshot": src_snap}
                except (c.ShareError, OSError) as exc:
                    conflict(str(exc), row.get("path"), key)
    elif action == "adopt":
        key = f"{source}/{kind}/{asset}"
        existing_row = cfg["inbound"].get(key, {})
        row = {"source": source, "kind": kind, "name": asset}
        if existing_row.get("alias"):
            row["alias"] = existing_row["alias"]
        src = None
        try:
            src = source_path(cfg, source, kind, asset)
            if not os.path.lexists(src):
                raise c.ShareError(f"The library {source} has no {kind} named {asset}")
            validate_asset(src, kind)
            observe(src)
        except (c.ShareError, OSError) as exc:
            conflict(str(exc), key=key, reason="missing")
        targets, kept = adopt_targets(target, cfg, kind, row.get("alias", asset), path)
        if not c.destinations(kind, cfg["tools"]):
            readers = ", ".join(c.DESTINATIONS[kind]) or "its own installer"
            conflict(f"No enabled tool reads {kind}; enable {readers}", key=key, reason="tool")
        for rel, tool in targets if src is not None else []:
            try:
                dest = c.safe_path(target, rel, leaf_link=True)
                if src == dest or src in dest.parents or dest in src.parents:
                    raise c.ShareError("The local copy and the library original overlap")
                existing = observe(dest)
                previous = old["inbound"].get(rel)
                if existing is not None and tracked(target, dest):
                    conflict(
                        f"{rel} is tracked by Git; remove it through its repository first",
                        rel,
                        key,
                        "tracked",
                    )
                    continue
                if previous:
                    if existing is not None and existing != {
                        "type": "link",
                        "target": previous["source_path"],
                    }:
                        raise c.ShareError(f"Managed link changed: {rel}; restore it first")
                    if previous["source_path"] != str(src):
                        conflict(
                            f"{rel} is linked to {previous.get('key')}; remove it first",
                            rel,
                            key,
                            "managed",
                        )
                        continue
                elif existing is not None:
                    if not same_content(dest, src) and not replace:
                        conflict(
                            f"Your copy of {asset} differs from the library version. "
                            "Share your changes, or replace yours (it is archived).",
                            rel,
                            key,
                            "differs",
                        )
                        continue
                    archive(dest, None)
                if tool is None:
                    continue  # The original the share came from is archived, not linked.
                if existing != {"type": "link", "target": str(src)}:
                    changes.append({"op": "link", "path": str(dest), "source": str(src)})
                next_state["inbound"][rel] = {
                    **row,
                    "source_path": str(src),
                    "key": key,
                    "tool": tool,
                }
            except (c.ShareError, OSError) as exc:
                conflict(str(exc), rel, key)
        if key not in cfg["inbound"]:
            config_update = local_cfg
            config_update["inbound"][key] = row
    elif action == "remove":
        c.asset_name(kind, asset)
        candidates = [f"{folder}/{asset}" for folder in c.folders(kind)]
        if path is not None:
            if path not in candidates:
                raise c.ShareError(f"Not a {kind} destination for {asset}: {path}")
            candidates = [path]
        # A managed selection is removed from every tool it reaches; disabling it
        # would otherwise leave its other links to be archived on the next sync.
        keys = {old["inbound"][rel]["key"] for rel in candidates if rel in old["inbound"]}
        rels = list(candidates)
        rels += [rel for rel, row in old["inbound"].items() if row.get("key") in keys]
        found = False
        for rel in dict.fromkeys(rels):
            dest = c.safe_path(target, rel, leaf_link=True)
            if observe(dest) is None:
                next_state["inbound"].pop(rel, None)
                continue
            found = True
            if tracked(target, dest):
                conflict(
                    f"Asset is Git-tracked; remove it through its owning repository: {rel}", rel
                )
                continue
            archive(dest, old["inbound"].get(rel))
            next_state["inbound"].pop(rel, None)
        if not found:
            conflict(f"Asset is absent: {' or '.join(candidates)}", candidates[0])
        if keys:
            config_update = local_cfg
            for key in keys:
                config_update["inbound"][key] = {"enabled": False}
    else:
        c.name(archive_id)
        folder = c.safe_path(target, f".captain/archive/{archive_id}")
        metadata = c.read_json(folder / "record.json")
        if not metadata or metadata.get("version") != 1:
            raise c.ShareError("Archive entry does not exist or is invalid")
        dest = c.safe_path(target, metadata["relative_path"], leaf_link=True)
        # Archives cannot be edited to restore arbitrary paths outside asset directories.
        allowed = (
            [f"{directory}/" for directory in c.folders()]
            + [f"ip/{k}/" for k in c.KINDS]
            + [f"{k}/" for k in c.KINDS]
        )
        if not any(
            metadata["relative_path"].startswith(prefix)
            and "/" not in metadata["relative_path"][len(prefix) :]
            for prefix in allowed
        ):
            raise c.ShareError("Archive destination is not an asset path")
        payload = c.safe_path(folder, "payload", leaf_link=True)
        observe(folder / "record.json")
        archived = observe(payload)
        if archived is None or archived != metadata.get("snapshot"):
            conflict("Archive payload changed or is missing", metadata["relative_path"])
        if observe(dest) is not None:
            conflict("Restore destination already exists", metadata["relative_path"])
        if tracked(target, dest):
            conflict("Restore destination is Git-tracked", metadata["relative_path"])
        changes.append(
            {
                "op": "restore",
                "path": str(dest),
                "source": str(payload),
                "archive_record": str(folder / "record.json"),
            }
        )
        previous = metadata.get("selection")
        if previous:
            next_state["inbound"][metadata["relative_path"]] = previous
            config_update = local_cfg
            config_update["inbound"][previous["key"]] = {
                k: v for k, v in previous.items() if k not in ("source_path", "key", "tool")
            }
    exclusion = exclude_change(target, next_state["inbound"])
    # Local records, archives and bindings must never be quietly written over tracked state.
    for rel in (".captain/sharing.local.json", ".captain/sharing-state.json", ".captain/archive"):
        if tracked(target, target / rel):
            conflict(f"Local installation state is Git-tracked: {rel}", rel)
    spec = {
        "target": str(target),
        "direction": direction,
        "action": action,
        "kind": kind,
        "asset": asset,
        "archive_id": archive_id,
        "path": path,
    }
    if selections is not None:
        # Kept in the spec so apply re-plans exactly the same one-off copy.
        spec["selections"] = selections
    if action == "adopt":
        spec.update(source=source, replace=replace)
    result = {
        "version": 1,
        "spec": spec,
        "changes": changes,
        "errors": errors,
        "conflicts": conflicts,
        "tools": cfg["tools"],
        "can_apply": not errors,
        "observations": observations,
        "next_state": next_state,
        "config_update": config_update,
        "exclusion": exclusion,
        "sources": cfg["sources"],
    }
    if action == "adopt":
        result["kept"] = kept
    result["fingerprint"] = digest(result)
    return result


def same_content(local, original):
    """A local copy matches the library original: a link to it, or equal content."""
    if local.is_symlink():
        return os.path.realpath(local) == os.path.realpath(original)
    return snapshot(local, modes=False) == snapshot(original, modes=False)


def adopt_targets(target, cfg, kind, name, path=None):
    """Destinations an adopt plan handles, as (rel, tool): every enabled tool folder
    that reads `kind`, plus the shared-from path (tool None). Copies in folders of
    tools that are not enabled are kept, so those tools do not lose the asset."""
    enabled = {folder: tool for tool, folder in c.destinations(kind, cfg["tools"])}
    rows = [(f"{folder}/{name}", tool) for folder, tool in enabled.items()]
    kept = []
    if path is not None and path not in {rel for rel, _ in rows}:
        folder = path.rpartition("/")[0]
        # Only folders a restore accepts, so the archived copy stays recoverable.
        restorable = set(c.folders()) | {f"ip/{k}" for k in c.KINDS} | set(c.KINDS)
        if not os.path.lexists(c.safe_path(target, path, leaf_link=True)):
            pass
        elif folder in c.DESTINATIONS.get(kind, {}).values() and folder not in enabled:
            kept.append({"path": path, "reason": "Its tool is not enabled for this place"})
        elif folder not in restorable:
            kept.append({"path": path, "reason": "Not an asset folder; left in place"})
        else:
            rows.append((path, None))
    return rows, kept


def adopt_preview(target, kind, name, path=None):
    """Predicted adopt operations ({op, path} relative to target) for an asset that
    is about to be copied into a library, so its content will match."""
    target = c.root(target)
    cfg, old = c.effective(target), state(target)
    rows, _ = adopt_targets(target, cfg, kind, name, path)
    ops = []
    for rel, tool in rows:
        dest = c.safe_path(target, rel, leaf_link=True)
        managed = old["inbound"].get(rel)
        if os.path.lexists(dest) and not managed:
            ops.append({"op": "archive", "path": rel})
        if tool is not None and not managed:
            ops.append({"op": "link", "path": rel})
    return ops


class Transaction:
    """Undo completed filesystem operations on handled failure; no crash-atomic claim."""

    def __init__(self):
        self.undo = []

    def parents(self, p):
        if not p.parent.exists():
            self.parents(p.parent)
            p.parent.mkdir()
            self.undo.append(lambda q=p.parent: q.rmdir())

    def write(self, p, data):
        self.parents(p)
        old = p.read_bytes() if p.exists() else None
        mode = stat.S_IMODE(p.stat().st_mode) if p.exists() else 0o600
        self.undo.append(
            lambda: (p.write_bytes(old), p.chmod(mode))
            if old is not None
            else p.unlink(missing_ok=True)
        )
        if isinstance(data, dict):
            c.atomic_json(p, data)
        else:
            p.write_text(data)

    def move(self, src, dst):
        self.parents(dst)
        if os.path.lexists(dst):
            raise c.ShareError(f"Destination appeared during operation: {dst}")
        os.rename(src, dst)
        self.undo.append(lambda: os.rename(dst, src))

    def link(self, src, dst):
        self.parents(dst)
        dst.symlink_to(src)
        self.undo.append(lambda: dst.unlink())

    def copy(self, src, dst):
        self.parents(dst)
        if os.path.lexists(dst):
            raise c.ShareError(f"Destination appeared during operation: {dst}")
        # Staging beside destination makes the final rename atomic and cross-device safe.
        temp = Path(tempfile.mkdtemp(prefix=".share-copy-", dir=dst.parent))
        payload = temp / "payload"
        try:
            if src.is_dir():
                shutil.copytree(src, payload, ignore=shutil.ignore_patterns(*HOUSEKEEPING))
            else:
                shutil.copy2(src, payload)
            os.rename(payload, dst)
            self.undo.append(lambda: shutil.rmtree(dst) if dst.is_dir() else dst.unlink())
        finally:
            shutil.rmtree(temp)

    def rollback(self):
        issues = []
        for fn in reversed(self.undo):
            try:
                fn()
            except OSError as exc:
                issues.append(str(exc))
        return issues


def apply(saved):
    if (
        not isinstance(saved, dict)
        or saved.get("version") != 1
        or not isinstance(saved.get("spec"), dict)
    ):
        raise c.ShareError("Invalid plan document")
    target = c.root(saved["spec"]["target"])
    cfg = c.effective(target)
    roots = [target / ".captain"]
    roots += [
        Path(s["path"]).expanduser().resolve() / ".captain"
        for s in cfg["sources"].values()
        if s.get("path")
    ]
    repo = git_root(target)
    if repo:
        # Worktrees sharing an exclude file share a lock too.
        info = exclude_change(target, [])
        roots.append(Path(info["path"]))
    with c.locks(roots):
        current = plan(**saved["spec"])
        if current["fingerprint"] != saved.get("fingerprint"):
            raise c.ShareError("Plan is stale: files or configuration changed. Preview again.")
        if not current["can_apply"]:
            raise c.ShareError("; ".join(current["errors"]))
        tx, archives = Transaction(), []
        try:
            for change in current["changes"]:
                dest = Path(change["path"])
                if change["op"] == "archive":
                    owner = Path(change.get("archive_root", str(target)))
                    archive_id = (
                        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                        + "-"
                        + uuid.uuid4().hex[:10]
                    )
                    folder = c.safe_path(owner, f".captain/archive/{archive_id}")
                    metadata = {
                        "version": 1,
                        "id": archive_id,
                        "relative_path": dest.relative_to(owner).as_posix(),
                        "snapshot": snapshot(dest),
                        "selection": change.get("selection"),
                        "created": datetime.now(timezone.utc).isoformat(),
                    }
                    tx.move(dest, folder / "payload")
                    tx.write(folder / "record.json", metadata)
                    # Archive contents and machine records stay local to their owning repo.
                    ig = c.safe_path(owner, ".captain/.gitignore")
                    text = ig.read_text() if ig.exists() else ""
                    tx.write(
                        ig,
                        text.rstrip("\n")
                        + "\n"
                        + "\n".join(
                            x for x in c.LOCAL_IGNORE.splitlines() if x not in text.splitlines()
                        )
                        + "\n",
                    )
                    archives.append({"id": archive_id, "root": str(owner)})
                elif change["op"] == "link":
                    tx.link(change["source"], dest)
                elif change["op"] == "copy":
                    tx.copy(Path(change["source"]), dest)
                elif change["op"] == "restore":
                    tx.move(Path(change["source"]), dest)
                    record = Path(change["archive_record"])
                    meta = c.read_json(record)
                    meta["restored"] = datetime.now(timezone.utc).isoformat()
                    tx.write(record, meta)
            if current["config_update"] is not None:
                tx.write(c.safe_path(target, ".captain/sharing.json"), current["config_update"])
            tx.write(c.safe_path(target, ".captain/sharing-state.json"), current["next_state"])
            ex = current["exclusion"]
            if ex and ex["changed"]:
                tx.write(Path(ex["path"]), ex["text"])
            ig = c.safe_path(target, ".captain/.gitignore")
            original = ig.read_text() if ig.exists() else ""
            extra = [x for x in c.LOCAL_IGNORE.splitlines() if x not in original.splitlines()]
            if extra:
                tx.write(ig, original.rstrip("\n") + "\n" + "\n".join(extra) + "\n")
        except Exception as exc:
            issues = tx.rollback()
            raise c.ShareError(
                f"Apply failed and was rolled back: {exc}"
                + (f"; recovery needed: {issues}" if issues else "")
            ) from exc
    return {
        "ok": True,
        "changes": len(current["changes"]),
        "archives": archives,
        "target": str(target),
    }


def archives(target):
    folder = c.safe_path(c.root(target), ".captain/archive")
    result = []
    if folder.is_dir():
        for p in sorted(folder.iterdir()):
            if p.is_symlink():
                continue
            data = c.read_json(p / "record.json")
            if data:
                result.append(data)
    return result


def status(target):
    target = c.root(target)
    current = state(target)
    rows = []
    for rel, row in current["inbound"].items():
        p = c.safe_path(target, rel, leaf_link=True)
        ok = p.is_symlink() and os.readlink(p) == row["source_path"] and p.exists()
        rows.append(
            {
                "path": rel,
                "source": row["source"],
                "name": row["name"],
                "kind": row["kind"],
                "status": "linked" if ok else "drift",
                "shared_original": row["source_path"],
                "tool": row.get("tool"),
            }
        )
    return {
        "ok": all(row["status"] == "linked" for row in rows),
        "target": str(target),
        "installed": rows,
        "archives": archives(target),
        "config": c.effective(target),
        "legacy_selector": (target / ".kiro-ip").exists(),
    }


def guard(target):
    target = c.root(target)
    problems = []
    for rel in state(target)["inbound"]:
        if tracked(target, target / rel):
            problems.append(rel)
    for rel in (".captain/sharing.local.json", ".captain/sharing-state.json", ".captain/archive"):
        if tracked(target, target / rel):
            problems.append(rel)
    return {"ok": not problems, "tracked_local_paths": problems}
