"""Layered portable selections and machine-local source bindings."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


class ShareError(Exception):
    pass


# A kind names a library folder and format; each tool that reads it gets its own link.
DESTINATIONS = {
    "skills": {"kiro": ".kiro/skills", "claude": ".claude/skills"},
    "agents": {"kiro": ".kiro/agents"},
    "prompts": {"kiro": ".kiro/prompts"},
    "steering": {"kiro": ".kiro/steering"},
    "copilot": {"copilot": ".github"},
    "copilot-instructions": {"copilot": ".github/instructions"},
    "copilot-prompts": {"copilot": ".github/prompts"},
    # Kiro installs powers user-wide by copying them (ADR 003); no project link exists.
    "powers": {},
}
KINDS = tuple(DESTINATIONS)
TOOLS = ("kiro", "claude", "copilot")
DEFAULT_TOOLS = ["kiro", "copilot"]
SUFFIXES = {
    "agents": ".json",
    "prompts": ".md",
    "steering": ".md",
    "copilot-instructions": ".instructions.md",
    "copilot-prompts": ".prompt.md",
}
NAME = re.compile(r"[a-zA-Z0-9_-][a-zA-Z0-9_.-]*\Z")
LOCAL_IGNORE = "sharing.local.json\nsharing-state.json\nrepositories.local.json\narchive/\n"


def name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ShareError(f"Invalid name: {value!r}")
    return value


def asset_name(kind, value):
    name(value)
    if kind not in KINDS:
        raise ShareError(f"Unknown asset kind: {kind}")
    if kind == "copilot" and value != "copilot-instructions.md":
        raise ShareError("Copilot repository instructions must be named copilot-instructions.md")
    if kind in SUFFIXES and not value.endswith(SUFFIXES[kind]):
        raise ShareError(f"{kind} names must end with {SUFFIXES[kind]}")
    return value


def destinations(kind, tools=TOOLS):
    """Project folders for one kind, limited to the enabled tools."""
    return [(tool, folder) for tool, folder in DESTINATIONS[kind].items() if tool in tools]


def folders(kind=None):
    kinds = [kind] if kind else KINDS
    return [folder for k in kinds for folder in DESTINATIONS[k].values()]


def tools_list(value, path="tools"):
    if (
        not isinstance(value, list)
        or not value
        or len(set(value)) != len(value)
        or any(tool not in TOOLS for tool in value)
    ):
        raise ShareError(f"tools must be a non-empty list of {', '.join(TOOLS)} at {path}")
    return value


def root(value):
    p = Path(value).expanduser().resolve()
    if not p.is_dir():
        raise ShareError(f"Folder does not exist: {p}")
    return p


def safe_path(base, rel, *, leaf_link=False):
    """Lexical containment plus refusal to follow any intermediate symlink."""
    rel = Path(rel)
    if rel.is_absolute() or not rel.parts or any(x in ("..", "") for x in rel.parts):
        raise ShareError(f"Expected a relative path inside {base}: {rel}")
    p = Path(base)
    for i, part in enumerate(rel.parts):
        p = p / part
        if p.is_symlink() and not (leaf_link and i == len(rel.parts) - 1):
            raise ShareError(f"Refusing to follow symlink: {p}")
    return p


def read_json(p, default=None):
    if p.is_symlink():
        raise ShareError(f"Configuration cannot be a symlink: {p}")
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except (ValueError, OSError) as exc:
        raise ShareError(f"Cannot read JSON at {p}: {exc}") from exc


def atomic_json(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_symlink():
        raise ShareError(f"Refusing to replace symlink: {p}")
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".sharing-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def global_dir():
    p = (
        Path(os.environ.get("CAPTAIN_SHARE_HOME", str(Path.home() / ".captain")))
        .expanduser()
        .absolute()
    )
    return p.parent.resolve() / p.name


def config_dir(target=None, scope="repo"):
    if scope == "global":
        d = global_dir()
        # A global configuration root must not redirect writes through a symlink.
        safe_path(Path(d.anchor), str(d.relative_to(d.anchor)))
        return d
    return safe_path(root(target or "."), ".captain")


@contextlib.contextmanager
def locks(paths):
    """Ordered per-user advisory locks shared by CLI and web processes."""
    lockdir = Path(tempfile.gettempdir()) / f"captain-share-locks-{os.getuid()}"
    if lockdir.is_symlink():
        raise ShareError("Lock directory is a symlink")
    lockdir.mkdir(mode=0o700, exist_ok=True)
    if lockdir.stat().st_uid != os.getuid() or lockdir.stat().st_mode & 0o077:
        raise ShareError("Lock directory must be private and owned by the current user")
    with contextlib.ExitStack() as stack:
        for key in sorted(set(str(Path(p).absolute()) for p in paths)):
            token = hashlib.sha256(key.encode()).hexdigest()
            fd = os.open(lockdir / token, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            f = stack.enter_context(os.fdopen(fd, "a"))
            fcntl.flock(f, fcntl.LOCK_EX)
        yield


def blank() -> dict[str, Any]:
    return {"version": 1, "sources": {}, "inbound": {}, "outbound": {}}


def validate(data, path, local=False):
    allowed = {"version", "sources"} if local else set(blank())
    if not isinstance(data, dict) or data.get("version") != 1 or set(data) - allowed - {"tools"}:
        raise ShareError(f"Unsupported sharing configuration at {path}")
    if "tools" in data:
        if local:
            raise ShareError(f"tools belong in the portable sharing.json, not {path}")
        tools_list(data["tools"], path)
    for field in allowed - {"version"}:
        data.setdefault(field, {})
        if not isinstance(data[field], dict):
            raise ShareError(f"{field} must be an object at {path}")
    for sid, spec in data.get("sources", {}).items():
        name(sid)
        keys = {"path", "layout", "enabled"} if local else {"url", "description", "enabled"}
        if not isinstance(spec, dict) or set(spec) - keys:
            raise ShareError(f"Invalid source {sid} at {path}")
        if "enabled" in spec and not isinstance(spec["enabled"], bool):
            raise ShareError(f"Source enabled must be boolean at {path}")
        if local and spec.get("enabled", True):
            if not isinstance(spec.get("path"), str) or not spec["path"]:
                raise ShareError(f"Source {sid} needs a local path at {path}")
            if spec.get("layout", "ip") not in ("ip", "flat"):
                raise ShareError(f"Unknown source layout at {path}")
    for direction in ("inbound", "outbound"):
        for key, row in data.get(direction, {}).items():
            if not isinstance(row, dict):
                raise ShareError(f"Invalid selection {key}")
            if "enabled" in row and not isinstance(row["enabled"], bool):
                raise ShareError(f"Selection enabled must be boolean: {key}")
            if row == {"enabled": False}:
                continue
            if set(row) - {"source", "kind", "name", "alias", "path", "enabled"}:
                raise ShareError(f"Unknown selection fields: {key}")
            for required in ("source", "kind", "name"):
                if required not in row:
                    raise ShareError(f"Selection {key} lacks {required}")
            name(row["source"])
            asset_name(row["kind"], row["name"])
            if direction == "inbound" and not DESTINATIONS[row["kind"]]:
                raise ShareError(
                    f"{row['kind']} are installed by their tool, not linked: {key}. "
                    "Use `captain-share powers install` for Kiro powers."
                )
            if key != selection_key(row):
                raise ShareError(f"Selection key does not match asset: {key}")
            if "alias" in row:
                asset_name(row["kind"], row["alias"])
            if direction == "outbound" and not isinstance(row.get("path"), str):
                raise ShareError(f"Outbound selection needs a relative path: {key}")


def layers(target):
    target = root(target)
    dirs = [config_dir(scope="global")]
    dirs += [p / ".captain" for p in [*reversed(target.parents), target]]
    seen = set()
    for d in dirs:
        if d in seen:
            continue
        seen.add(d)
        if d.exists() or d.is_symlink():
            safe_path(Path(d.anchor), str(d.relative_to(d.anchor)))
        for file, local in [("sharing.json", False), ("sharing.local.json", True)]:
            p = d / file
            data = read_json(p)
            if data is not None:
                validate(data, p, local)
                yield p, data, local


def effective(target) -> dict[str, Any]:
    result = blank()
    result["layers"] = []
    result["tools"] = list(DEFAULT_TOOLS)
    for p, data, local in layers(target):
        result["layers"].append(str(p))
        if "tools" in data:
            # The deepest layer that names tools replaces the inherited list.
            result["tools"] = list(data["tools"])
        for sid, spec in data.get("sources", {}).items():
            result["sources"].setdefault(sid, {}).update(spec)
            if local and spec.get("path"):
                binding = Path(spec["path"]).expanduser()
                if not binding.is_absolute():
                    binding = p.parent.parent / binding
                result["sources"][sid]["path"] = str(binding.resolve())
        if not local:
            for direction in ("inbound", "outbound"):
                result[direction].update(data.get(direction, {}))
    for direction in ("inbound", "outbound", "sources"):
        result[direction] = {k: v for k, v in result[direction].items() if v.get("enabled", True)}
    return result


def init(target=None, scope="repo", library=False):
    d = config_dir(target, scope)
    with locks([d]):
        d.mkdir(parents=True, exist_ok=True)
        path = d / "sharing.json"
        if not path.exists():
            atomic_json(path, blank())
        else:
            validate(read_json(path), path)
        ig = safe_path(d, ".gitignore")
        existing = ig.read_text() if ig.exists() else ""
        missing = [line for line in LOCAL_IGNORE.splitlines() if line not in existing.splitlines()]
        if missing:
            ig.write_text(
                existing.rstrip("\n") + ("\n" if existing else "") + "\n".join(missing) + "\n"
            )
        if library:
            for kind in KINDS:
                safe_path(root(target), f"ip/{kind}").mkdir(parents=True, exist_ok=True)
    return {"config": str(path), "library": library}


def add_source(target, sid, path, layout="ip", scope="repo", url=None):
    name(sid)
    location = root(path)
    if layout not in ("ip", "flat"):
        raise ShareError("Layout must be ip or flat")
    base = safe_path(location, "ip") if layout == "ip" else location
    if not base.is_dir():
        raise ShareError(f"Library root does not exist: {base}")
    if url and (not isinstance(url, str) or re.search(r"https?://[^/]*@", url)):
        raise ShareError("Use a repository URL without embedded credentials")
    init(target, scope)
    d = config_dir(target, scope)
    with locks([d]):
        local = read_json(d / "sharing.local.json", {"version": 1, "sources": {}})
        validate(local, d / "sharing.local.json", True)
        local["sources"][sid] = {"path": str(location), "layout": layout, "enabled": True}
        portable = read_json(d / "sharing.json")
        validate(portable, d / "sharing.json")
        portable["sources"][sid] = {"enabled": True, **({"url": url} if url else {})}
        atomic_json(d / "sharing.local.json", local)
        atomic_json(d / "sharing.json", portable)
    return {"source": sid, "path": str(location), "scope": scope}


def remove_source(target, sid, scope="repo"):
    name(sid)
    init(target, scope)
    d = config_dir(target, scope)
    with locks([d]):
        cfg = read_json(d / "sharing.json")
        validate(cfg, d / "sharing.json")
        cfg["sources"][sid] = {"enabled": False}
        local = read_json(d / "sharing.local.json", {"version": 1, "sources": {}})
        local["sources"][sid] = {"enabled": False}
        atomic_json(d / "sharing.json", cfg)
        atomic_json(d / "sharing.local.json", local)
    return {"source": sid, "disabled": True}


def set_tools(target, tools, scope="repo"):
    tools_list(tools)
    init(target, scope)
    d = config_dir(target, scope)
    with locks([d]):
        cfg = read_json(d / "sharing.json")
        validate(cfg, d / "sharing.json")
        cfg["tools"] = [tool for tool in TOOLS if tool in tools]
        atomic_json(d / "sharing.json", cfg)
    return {"tools": cfg["tools"], "scope": scope, "config": str(d / "sharing.json")}


def selection_key(row):
    return "/".join(row[k] for k in ("source", "kind", "name"))


def select(target, direction, source, kind, asset, alias=None, path=None, enabled=True):
    if direction not in ("inbound", "outbound"):
        raise ShareError("Direction must be inbound or outbound")
    row = {"source": name(source), "kind": kind, "name": asset_name(kind, asset)}
    key = selection_key(row)
    if alias and direction != "inbound":
        raise ShareError("Aliases apply to inbound installations")
    if alias:
        row["alias"] = asset_name(kind, alias)
    if direction == "outbound":
        if enabled:
            safe_path(root(target), path or "", leaf_link=False)
        row["path"] = path
    if not enabled:
        row = {"enabled": False}
    init(target)
    d = config_dir(target)
    with locks([d]):
        cfg = read_json(d / "sharing.json")
        validate(cfg, d / "sharing.json")
        cfg[direction][key] = row
        validate(cfg, d / "sharing.json")
        atomic_json(d / "sharing.json", cfg)
    return {"direction": direction, "key": key, "selection": row}
