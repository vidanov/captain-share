"""One deterministic plan/apply engine for CLI and loopback UI."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config as c
from . import formats

MAX_ASSET_BYTES = 64 * 1024 * 1024
# Regenerable local files: never listed, compared, or copied, and never blocking.
HOUSEKEEPING = {".DS_Store", ".gitkeep", "__pycache__"}
# Characters an editor does not show but an agent reads: bidirectional embeddings,
# overrides and isolates (which reorder what is displayed), zero-width and invisible
# characters, Hangul fillers, Unicode tags (which can spell out hidden instructions)
# and supplementary variation selectors. Joiners (U+200C/D) and ordinary variation
# selectors stay allowed: emoji and many scripts need them.
HIDDEN_CHARACTERS = (
    set(range(0x202A, 0x202F))
    | set(range(0x2066, 0x206A))
    | {0x200B, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0x180E}
    | {0x115F, 0x1160, 0x3164, 0xFFA0}
    | set(range(0xE0000, 0xE0080))
    | set(range(0xE0100, 0xE01F0))
)
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


def hidden_characters(data):
    """(line, column, character) of each hidden character in UTF-8 text; [] for other
    data. A byte order mark is allowed only at the start of the file."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return []
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for column, char in enumerate(line, 1):
            code = ord(char)
            if code in HIDDEN_CHARACTERS or (code == 0xFEFF and (number, column) != (1, 1)):
                found.append((number, column, char))
    return found


def hidden_message(name, found):
    number, column, char = found[0]
    label = unicodedata.name(char, "UNNAMED CHARACTER")
    more = f" and {len(found) - 1} more" if len(found) > 1 else ""
    return (
        f"Hidden character in {name} line {number}, column {column}: "
        f"U+{ord(char):04X} {label}{more}. It can change what an agent reads without "
        "showing in an editor; remove it first."
    )


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
        if p.is_file() and p.stat().st_size <= MAX_ASSET_BYTES:
            found = hidden_characters(p.read_bytes())
            if found:
                name = p.name if p == path else p.relative_to(path).as_posix()
                raise c.ShareError(hidden_message(name, found))
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
        alias = row.get("alias", row.get("name", ""))
        allowed = [f"{dest}/{alias}" for dest in c.folders(row["kind"])]
        allowed += formats.destinations(row["kind"], alias)
        if rel not in allowed:
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
    new_name=None,
    link_team=False,
    from_source=None,
):
    """Preview changes. `selections` replaces the saved outbound selections for a
    one-off copy (sharing one asset) without writing them to configuration.
    `adopt` swaps local copies of one asset for links to its library original;
    `keep` renames a local copy into the user's own version (ADR 007);
    `relink` moves one selection and its managed links from `from_source` to
    `source` (promoting an asset from the personal library, ADR 008)."""
    target = c.root(target)
    if direction not in ("inbound", "outbound", "both"):
        raise c.ShareError("Unknown direction")
    if action not in ("sync", "remove", "restore", "adopt", "keep", "relink", "disconnect"):
        raise c.ShareError("Unknown plan action")
    if action == "disconnect":
        c.name(source)
    if action == "relink":
        c.asset_name(kind, asset)
        c.name(source)
        c.name(from_source)
        if from_source == source:
            raise c.ShareError("Choose a different library to move the selection to")
    elif from_source is not None:
        raise c.ShareError("from_source applies to relink plans only")
    if action in ("adopt", "keep", "relink"):
        c.asset_name(kind, asset)
        c.name(source)
        for flag in (replace, link_team):
            if not isinstance(flag, bool):
                raise c.ShareError("replace and link_team must be true or false")
        if path is not None:
            c.safe_path(target, path, leaf_link=True)
    elif (source is not None and action != "disconnect") or replace:
        raise c.ShareError("source and replace apply to adopt and keep plans only")
    if action == "keep":
        if not isinstance(new_name, str) or not new_name:
            raise c.ShareError("Choose a new name for your version")
        if not c.DESTINATIONS.get(kind):
            raise c.ShareError(f"{kind} are installed by their tool; keep a copy is not available")
        if kind in c.SUFFIXES and not new_name.endswith(c.SUFFIXES[kind]):
            new_name += c.SUFFIXES[kind]
    elif new_name is not None or link_team:
        raise c.ShareError("new_name and link_team apply to keep plans only")
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
    local_update = None

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

    renders: dict[str, str] = {}
    notes: list[str] = []

    def link_source(src, kind, tool, sid, name, key, rel):
        """(what one tool's destination links to, the destination). The original at
        `rel`, or a generated file for a tool that reads another format (ADR 012),
        which may land in another folder or under another name (ADR 013); (None,
        None) when that tool has no equivalent. A generated file that differs is
        archived, then rewritten."""
        if tool is None or (kind, tool) not in formats.CONVERTERS:
            return src, rel

        def beside(ref):
            # Files an asset refers to (an agent's prompt file) from its own folder.
            try:
                found = c.safe_path(src.parent, ref)
                return found.read_text(encoding="utf-8") if found.is_file() else None
            except (c.ShareError, OSError, UnicodeDecodeError):
                return None

        result = formats.convert(kind, tool, src.read_text(encoding="utf-8"), name, beside)
        notes.extend(f"{key} for {tool}: {note}" for note in result.notes if note not in notes)
        if result.content is None:
            return None, None
        folder, _, leaf = rel.rpartition("/")
        rel = f"{result.folder or folder}/{result.leaf or leaf}"
        out = rendered_path(tool, sid, kind, name)
        link = out
        if result.directory:
            link = out.parent / (out.name + ".skill")
            out = link / "SKILL.md"
        if str(out) in renders:
            return link, rel
        renders[str(out)] = result.content
        existing = observe(out)
        if existing is not None and (out.is_symlink() or not out.is_file()):
            raise c.ShareError(f"Generated file was replaced by something else: {out}")
        if existing is None or out.read_text(encoding="utf-8") != result.content:
            if existing is not None:
                changes.append(
                    {
                        "op": "archive",
                        "path": str(out),
                        "selection": None,
                        "archive_root": str(c.global_dir().parent),
                    }
                )
            changes.append(
                {"op": "render", "path": str(out), "source": str(src), "content": result.content}
            )
        return link, rel

    def same_as(dest, src, linked):
        """Whether a local copy equals what the destination would show."""
        if linked == src:
            return same_content(dest, src)
        try:
            if linked.name.endswith(".skill"):
                want, dest = renders[str(linked / "SKILL.md")], dest / "SKILL.md"
            else:
                want = renders[str(linked)]
            return dest.is_file() and dest.read_text(encoding="utf-8") == want
        except (KeyError, OSError, UnicodeDecodeError):
            return False

    def link_original(freed=()):
        """Adopt: archive local copies of the asset and link the library original into
        every enabled tool folder. Paths in `freed` are being archived by this plan."""
        nonlocal config_update
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
        if action == "keep":
            targets = [(rel, tool) for rel, tool in targets if tool is not None]
        if not c.destinations(kind, cfg["tools"]):
            readers = ", ".join(c.DESTINATIONS[kind]) or "its own installer"
            conflict(f"No enabled tool reads {kind}; enable {readers}", key=key, reason="tool")
        for rel, tool in targets:
            if src is None:
                break
            try:
                linked, moved = link_source(src, kind, tool, source, asset, key, rel)
                if linked is None:
                    continue
                if moved != rel and moved in [x for x, _ in targets]:
                    continue  # Another target already covers that destination.
                rel = moved
                dest = c.safe_path(target, rel, leaf_link=True)
                if src == dest or src in dest.parents or dest in src.parents:
                    raise c.ShareError("The local copy and the library original overlap")
                existing = None if rel in freed else observe(dest)
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
                    if previous["source_path"] != str(linked):
                        conflict(
                            f"{rel} is linked to {previous.get('key')}; remove it first",
                            rel,
                            key,
                            "managed",
                        )
                        continue
                elif existing is not None:
                    if not same_as(dest, src, linked) and not replace:
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
                if existing != {"type": "link", "target": str(linked)}:
                    changes.append({"op": "link", "path": str(dest), "source": str(linked)})
                next_state["inbound"][rel] = {
                    **row,
                    "source_path": str(linked),
                    "key": key,
                    "tool": tool,
                }
            except (c.ShareError, OSError) as exc:
                conflict(str(exc), rel, key)
        if key not in cfg["inbound"]:
            config_update = local_cfg
            config_update["inbound"][key] = row
        return kept

    kept: list = []
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
                        linked, rel = link_source(
                            src, row["kind"], tool, row["source"], row["name"], key, rel
                        )
                        if linked is None:
                            continue
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
                        if existing != {"type": "link", "target": str(linked)}:
                            if existing:
                                archive(dest, previous)
                            changes.append({"op": "link", "path": str(dest), "source": str(linked)})
                        desired[rel] = {**row, "source_path": str(linked), "key": key, "tool": tool}
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
                    if converted_folder(row["path"]):
                        raise c.ShareError(
                            f"{row['path']} is in Claude's format; libraries keep the Kiro "
                            "format. Share the Kiro original instead (ADR 012)."
                        )
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
        kept = link_original()
    elif action == "disconnect":
        # One place's part of disconnecting a library (ADR 010): archive its managed
        # links, drop its selections and its binding; the checkout is not touched.
        prefix = f"{source}/"
        for rel, previous in old["inbound"].items():
            if not str(previous.get("key", "")).startswith(prefix):
                continue
            try:
                dest = c.safe_path(target, rel, leaf_link=True)
                existing = observe(dest)
                if existing is None:
                    next_state["inbound"].pop(rel, None)
                    continue
                if tracked(target, dest):
                    conflict(
                        f"{rel} is tracked by Git; remove it through its repository first",
                        rel,
                        previous.get("key"),
                        "tracked",
                    )
                    continue
                if existing != {"type": "link", "target": previous["source_path"]}:
                    conflict(
                        f"{rel} changed since Captain Share linked it; restore or archive it first",
                        rel,
                        previous.get("key"),
                        "changed",
                    )
                    continue
                archive(dest, previous)
                next_state["inbound"].pop(rel, None)
            except (c.ShareError, OSError) as exc:
                conflict(str(exc), rel, previous.get("key"))
        for key in [k for k in next_state["outbound"] if k.startswith(prefix)]:
            next_state["outbound"].pop(key)
        updated = json.loads(json.dumps(local_cfg))
        for direction in ("inbound", "outbound"):
            for key in [k for k in updated[direction] if k.startswith(prefix)]:
                updated[direction].pop(key)
        updated["sources"].pop(source, None)
        if updated != local_cfg:
            config_update = updated
        local_path = c.safe_path(target, ".captain/sharing.local.json")
        bindings = c.read_json(local_path)
        if isinstance(bindings, dict) and source in bindings.get("sources", {}):
            c.validate(bindings, local_path, True)
            observe(local_path)
            local_update = json.loads(json.dumps(bindings))
            local_update["sources"].pop(source)
    elif action == "relink":
        old_key, key = f"{from_source}/{kind}/{asset}", f"{source}/{kind}/{asset}"
        src = None
        try:
            src = source_path(cfg, source, kind, asset)
            if not os.path.lexists(src):
                raise c.ShareError(f"The library {source} has no {kind} named {asset}")
            validate_asset(src, kind)
            observe(src)
        except (c.ShareError, OSError) as exc:
            conflict(str(exc), key=key, reason="missing")
        for rel, previous in old["inbound"].items():
            if previous.get("key") != old_key or src is None:
                continue
            try:
                dest = c.safe_path(target, rel, leaf_link=True)
                existing = observe(dest)
                if existing is not None:
                    if existing != {"type": "link", "target": previous["source_path"]}:
                        raise c.ShareError(f"Managed link changed: {rel}; restore it first")
                    archive(dest, previous)
                linked, moved = link_source(
                    src, kind, previous.get("tool"), source, asset, key, rel
                )
                if linked is None or moved != rel:
                    # The new library's version lands elsewhere; the next sync links it.
                    next_state["inbound"].pop(rel, None)
                    continue
                changes.append({"op": "link", "path": str(dest), "source": str(linked)})
                kept_fields = {
                    k: v for k, v in previous.items() if k not in ("source_path", "key", "tool")
                }
                next_state["inbound"][rel] = {
                    **kept_fields,
                    "source": source,
                    "source_path": str(linked),
                    "key": key,
                    "tool": previous.get("tool"),
                }
            except (c.ShareError, OSError) as exc:
                conflict(str(exc), rel, old_key)
        if old_key in cfg["inbound"] or old_key in local_cfg["inbound"]:
            config_update = local_cfg
            selected = cfg["inbound"].get(old_key)
            local_cfg["inbound"].pop(old_key, None)
            # Mask the old selection only when another layer still provides it.
            inherited = None
            for layer, data, local in c.layers(target):
                if local or layer == cfg_path or old_key not in data.get("inbound", {}):
                    continue
                if target != c.global_dir().parent and layer.parent == c.global_dir():
                    continue
                inherited = data["inbound"][old_key]
            if inherited is not None and inherited.get("enabled", True):
                local_cfg["inbound"][old_key] = {"enabled": False}
            if selected and key not in cfg["inbound"]:
                row = {k: v for k, v in selected.items() if k != "enabled"}
                local_cfg["inbound"][key] = {**row, "source": source}
    elif action == "keep":
        rel = path if path is not None else local_copy(target, cfg, old, kind, asset)
        vkey, chosen = f"{kind}/{new_name}", new_name
        try:
            c.asset_name(kind, new_name)
        except c.ShareError as exc:
            conflict(str(exc), rel, vkey, "invalid")
            chosen = None
        local = c.safe_path(target, rel, leaf_link=True)
        if not os.path.lexists(local) or local.is_symlink() or rel in old["inbound"]:
            raise c.ShareError(f"{rel} is not a local copy of {asset}")
        folder = rel.rpartition("/")[0]
        restorable = set(c.folders()) | {f"ip/{k}" for k in c.KINDS} | set(c.KINDS)
        if folder not in restorable:
            conflict(f"{rel} is not in an asset folder", rel, vkey, "invalid")
        try:
            validate_asset(local, kind)
        except c.ShareError as exc:
            conflict(str(exc), rel, vkey, "invalid")
        observe(local)
        if tracked(target, local):
            conflict(
                f"{rel} is tracked by Git; rename it through its repository", rel, vkey, "tracked"
            )
        if chosen is not None:
            new_rel = f"{folder}/{chosen}" if folder else chosen
            new_dest = c.safe_path(target, new_rel, leaf_link=True)
            taken = observe(new_dest) is not None or vkey in local_cfg.get("versions", {})
            taken = taken or any(
                os.path.lexists(c.safe_path(target, f"{f}/{chosen}", leaf_link=True))
                for _, f in c.destinations(kind, cfg["tools"])
            )
            if chosen == asset:
                conflict("Choose a name different from the team's", new_rel, vkey, "invalid")
            elif taken:
                conflict(
                    f"{chosen} is already taken here; choose another name", new_rel, vkey, "taken"
                )
            edit = None
            if kind == "skills" and (local / "SKILL.md").is_file():
                renamed = rename_frontmatter((local / "SKILL.md").read_text(), chosen)
                if renamed is not None:
                    edit = f"name: {renamed[1]} -> {chosen}"
            changes.append(
                {
                    "op": "rename",
                    "path": str(new_dest),
                    "source": str(local),
                    "name": chosen,
                    "edit": edit,
                }
            )
            archive(local, None)
            base = None
            try:
                base = head_of(source_path(cfg, source))
            except c.ShareError as exc:
                conflict(str(exc), None, vkey, "missing")
            config_update = local_cfg
            config_update.setdefault("versions", {})[vkey] = {
                "source": source,
                "kind": kind,
                "name": asset,
                "base": base,
            }
        if link_team:
            kept = [row for row in link_original(freed={rel}) if row["path"] != rel]
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
        if path is None:
            # A converted destination can carry another name (ADR 013).
            keys |= {
                row["key"]
                for row in old["inbound"].values()
                if row.get("kind") == kind and row.get("alias", row.get("name")) == asset
            }
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
    if action == "keep":
        spec.update(source=source, new_name=new_name, link_team=link_team)
    if action == "relink":
        spec.update(source=source, from_source=from_source)
    if action == "disconnect":
        spec.update(source=source)
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
        "notes": notes,
        "config_update": config_update,
        "exclusion": exclusion,
        "sources": cfg["sources"],
    }
    if action in ("adopt", "keep"):
        result["kept"] = kept
    if action == "disconnect":
        result["local_update"] = local_update
    result["fingerprint"] = digest(result)
    return result


def converted_folder(rel):
    """Whether a target-relative path lies in a folder whose tool reads another format."""
    return any(
        rel.startswith(c.DESTINATIONS[kind][tool] + "/") for kind, tool in formats.CONVERTERS
    )


def rendered_path(tool, source, kind, name):
    """Where the generated file for one asset in another tool's format lives."""
    return c.safe_path(c.global_dir(), f"rendered/{c.name(tool)}/{c.name(source)}/{kind}/{name}")


def archive_into(tx, dest, owner, selection=None):
    """Move dest into owner's recoverable archive within a transaction; returns its id."""
    archive_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:10]
    folder = c.safe_path(owner, f".captain/archive/{archive_id}")
    metadata = {
        "version": 1,
        "id": archive_id,
        "relative_path": dest.relative_to(owner).as_posix(),
        "snapshot": snapshot(dest),
        "selection": selection,
        "created": datetime.now(timezone.utc).isoformat(),
    }
    tx.move(dest, folder / "payload")
    tx.write(folder / "record.json", metadata)
    # Archive contents and machine records stay local to their owning repo.
    ig = c.safe_path(owner, ".captain/.gitignore")
    text = ig.read_text() if ig.exists() else ""
    missing = [x for x in c.LOCAL_IGNORE.splitlines() if x not in text.splitlines()]
    if missing:
        tx.write(ig, text.rstrip("\n") + ("\n" if text.strip() else "") + "\n".join(missing) + "\n")
    return archive_id


def replace_original(local, original, owner):
    """Overwrite a library original with a local copy: the original is archived beside
    the library, and a handled failure rolls both steps back."""
    tx = Transaction()
    try:
        archive_id = archive_into(tx, original, owner)
        tx.copy(local, original)
    except Exception as exc:
        issues = tx.rollback()
        raise c.ShareError(
            f"Updating the library failed and was rolled back: {exc}"
            + (f"; recovery needed: {issues}" if issues else "")
        ) from exc
    return archive_id


def head_of(path):
    """The commit checked out in the Git repository holding path, or None."""
    repo = git_root(path)
    if repo is None:
        return None
    proc = git(repo, "rev-parse", "HEAD", check=False)
    value = os.fsdecode(proc.stdout).strip()
    return value if proc.returncode == 0 and c.SHA.fullmatch(value) else None


def rename_frontmatter(text, new):
    """(text with the frontmatter `name:` set to new, previous value), or None."""
    lines = text.splitlines(True)
    if not lines or lines[0].strip() != "---":
        return None
    for index, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return None
        if line.startswith("name:"):
            previous = line.partition(":")[2].strip().strip("\"'")
            ending = "\n" if line.endswith("\n") else ""
            lines[index] = f"name: {new}{ending}"
            return "".join(lines), previous
    return None


def local_copy(target, cfg, installed, kind, name):
    """The asset's destination in the first enabled tool folder holding an unmanaged copy."""
    for _, folder in c.destinations(kind, cfg["tools"]):
        rel = f"{folder}/{name}"
        p = c.safe_path(target, rel, leaf_link=True)
        if os.path.lexists(p) and not p.is_symlink() and rel not in installed["inbound"]:
            return rel
    raise c.ShareError(f"No local copy of {name} found here; choose its path")


MAX_DIFF_BYTES = 200 * 1024
MAX_DIFF_FILES = 500
MAX_DIFF_FILE = 1024 * 1024


def bounded(data):
    """File content for comparison; a large file becomes a binary hash marker."""
    if len(data) > MAX_DIFF_FILE:
        return b"\0large:" + hashlib.sha256(data).hexdigest().encode()
    return data


def asset_files(path, name):
    """Relative path -> content of an asset on disk, without housekeeping files. A
    single-file asset is keyed by its asset name."""
    if path.is_symlink():
        raise c.ShareError(f"{path} is a link, not a copy")
    if path.is_file():
        return {name: bounded(path.read_bytes())}
    files = {}
    for p in sorted(path.rglob("*")):
        if ignored(p, path):
            continue
        rel = p.relative_to(path).as_posix()
        if p.is_symlink():
            files[rel] = ("symlink -> " + os.readlink(p)).encode()
        elif p.is_file():
            files[rel] = bounded(p.read_bytes())
    return files


def is_binary(data):
    if b"\0" in data[:8192]:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def compare(old, new, old_label="library", new_label="yours"):
    """File-by-file unified diff from old to new, bounded in files and text."""
    files: list[dict] = []
    used, truncated = 0, False
    for rel in sorted(set(old) | set(new)):
        a, b = old.get(rel), new.get(rel)
        if a == b:
            continue
        if len(files) >= MAX_DIFF_FILES:
            truncated = True
            break
        status = "added" if a is None else "removed" if b is None else "changed"
        binary = any(is_binary(x) for x in (a, b) if x is not None)
        text = None
        if not binary and not truncated:
            lines = difflib.unified_diff(
                (a or b"").decode().splitlines(),
                (b or b"").decode().splitlines(),
                fromfile=f"{old_label}/{rel}" if a is not None else "/dev/null",
                tofile=f"{new_label}/{rel}" if b is not None else "/dev/null",
                lineterm="",
            )
            text = "\n".join(lines) + "\n"
            if used + len(text.encode()) > MAX_DIFF_BYTES:
                text, truncated = None, True
            else:
                used += len(text.encode())
        files.append({"path": rel, "status": status, "diff": text, "binary": binary})
    return {"files": files, "truncated": truncated}


def diff(target, source, kind, name, path=None):
    """Changes a local copy makes to its library original (ADR 007 "Show changes")."""
    target = c.root(target)
    c.asset_name(kind, name)
    cfg = c.effective(target)
    original = source_path(cfg, c.name(source), kind, name)
    if not os.path.lexists(original):
        raise c.ShareError(f"The library {source} has no {kind} named {name}")
    rel = path if path is not None else local_copy(target, cfg, state(target), kind, name)
    local = c.safe_path(target, rel, leaf_link=True)
    if not os.path.lexists(local):
        raise c.ShareError(f"Nothing at {rel}")
    result = compare(asset_files(original, name), asset_files(local, name))
    result["path"] = rel
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
    with c.locks(apply_roots(target)):
        current = plan(**saved["spec"])
        if current["fingerprint"] != saved.get("fingerprint"):
            raise c.ShareError("Plan is stale: files or configuration changed. Preview again.")
        if not current["can_apply"]:
            raise c.ShareError("; ".join(current["errors"]))
        tx, archives = Transaction(), []  # type: tuple[Transaction, list[dict]]
        try:
            perform(tx, current, target, archives)
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


def apply_roots(target):
    """Lock keys for applying a plan at target: its .captain, every bound library's
    .captain (archives), and a shared Git exclude file."""
    cfg = c.effective(target)
    # Generated files for other tools' formats live in the global folder (ADR 012).
    roots = [target / ".captain", c.global_dir()]
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
    return roots


def perform(tx, current, target, archives):
    """Carry out a fresh plan's changes inside a transaction the caller rolls back."""
    for change in current["changes"]:
        dest = Path(change["path"])
        if change["op"] == "archive":
            owner = Path(change.get("archive_root", str(target)))
            archive_id = archive_into(tx, dest, owner, change.get("selection"))
            archives.append({"id": archive_id, "root": str(owner)})
        elif change["op"] == "link":
            tx.link(change["source"], dest)
        elif change["op"] == "render":
            tx.write(dest, change["content"])
        elif change["op"] == "copy":
            tx.copy(Path(change["source"]), dest)
        elif change["op"] == "rename":
            tx.copy(Path(change["source"]), dest)
            if change.get("edit"):
                skill = dest / "SKILL.md"
                renamed = rename_frontmatter(skill.read_text(), change["name"])
                if renamed is None:
                    raise c.ShareError(f"Could not rename {skill}")
                tx.write(skill, renamed[0])
        elif change["op"] == "restore":
            tx.move(Path(change["source"]), dest)
            record = Path(change["archive_record"])
            meta = c.read_json(record)
            meta["restored"] = datetime.now(timezone.utc).isoformat()
            tx.write(record, meta)
    if current["config_update"] is not None:
        tx.write(c.safe_path(target, ".captain/sharing.json"), current["config_update"])
    if current.get("local_update") is not None:
        tx.write(c.safe_path(target, ".captain/sharing.local.json"), current["local_update"])
    tx.write(c.safe_path(target, ".captain/sharing-state.json"), current["next_state"])
    ex = current["exclusion"]
    if ex and ex["changed"]:
        tx.write(Path(ex["path"]), ex["text"])
    ig = c.safe_path(target, ".captain/.gitignore")
    original = ig.read_text() if ig.exists() else ""
    extra = [x for x in c.LOCAL_IGNORE.splitlines() if x not in original.splitlines()]
    if extra:
        tx.write(ig, original.rstrip("\n") + "\n" + "\n".join(extra) + "\n")


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


def why(value):
    """Where a file or folder in an agent's folders came from: the asset it belongs
    to, the library original it links to, and the layer that selected it."""
    path = Path(os.path.abspath(Path(value).expanduser()))
    if not os.path.lexists(path):
        raise c.ShareError(f"Nothing at {path}")
    folders = sorted(
        (
            (kind, tool, folder)
            for kind, tools in c.DESTINATIONS.items()
            for tool, folder in tools.items()
        ),
        key=lambda row: -len(Path(row[2]).parts),
    )
    for asset in [path, *path.parents]:
        parts = asset.parent.parts
        if parts[-3:] == (".kiro", "powers", "installed"):
            return {
                "path": str(path),
                "asset": str(asset),
                "kind": "powers",
                "name": asset.name,
                "status": "kiro-power",
                "note": "Installed by kiro-cli (Kiro owns this copy); update it from its library.",
            }
        for kind, tool, folder in folders:
            depth = len(Path(folder).parts)
            if parts[-depth:] != Path(folder).parts:
                continue
            try:
                c.asset_name(kind, asset.name)
            except c.ShareError:
                # A converted destination may use another suffix (ADR 013).
                if (kind, tool) not in formats.CONVERTERS or not c.NAME.fullmatch(asset.name):
                    continue
            place = Path(*parts[:-depth])
            return why_asset(path, place, kind, tool, f"{folder}/{asset.name}")
        library = library_of(asset)
        if library is not None:
            return {
                "path": str(path),
                "asset": str(asset),
                "kind": library[1],
                "name": asset.name,
                "status": "library",
                "source": library[0],
                "note": f"The original in the {library[0]} library; installs link here.",
            }
    raise c.ShareError(f"{path} is not in an agent folder or a connected library")


def library_of(asset):
    """(source, kind) when `asset` is an asset folder or file inside a connected library."""
    try:
        cfg = c.effective(c.global_dir().parent)
    except c.ShareError:
        return None
    for sid in sorted(cfg["sources"]):
        try:
            base = source_path(cfg, sid)
        except c.ShareError:
            continue
        if asset.parent.parent == base and asset.parent.name in c.KINDS:
            return sid, asset.parent.name
    return None


def why_asset(path, place, kind, tool, rel):
    asset = place / rel
    result = {
        "path": str(path),
        "asset": str(asset),
        "place": str(place),
        "kind": kind,
        "tool": tool,
        "name": asset.name,
    }
    try:
        cfg = c.effective(place)
    except c.ShareError:
        cfg = {"sources": {}, "origins": {"inbound": {}}}
    if asset.is_symlink():
        original = Path(os.path.abspath(asset.parent / os.readlink(asset)))
        result["original"] = str(original)
        result["status"] = "linked"
        for sid in sorted(cfg["sources"]):
            try:
                base = source_path(cfg, sid)
            except c.ShareError:
                continue
            if original.parent.parent == base and original.parent.name == kind:
                result.update(source=sid, library_name=original.name)
                key = f"{sid}/{kind}/{original.name}"
                saved = cfg["origins"]["inbound"].get(key)
                if saved:
                    result["selected_in"] = saved
        rendered = c.global_dir() / "rendered"
        if rendered in original.parents:
            # A generated file for this tool's format (ADR 012): name its library asset.
            parts = original.relative_to(rendered).parts
            if len(parts) == 4:
                _, sid, from_kind, name = parts
                name = name[: -len(".skill")] if name.endswith(".skill") else name
                result.update(source=sid, library_name=name, generated=True, from_kind=from_kind)
                saved = cfg["origins"]["inbound"].get(f"{sid}/{from_kind}/{name}")
                if saved:
                    result["selected_in"] = saved
        try:
            row = state(place)["inbound"].get(rel)
        except c.ShareError:
            row = None
        result["managed"] = row is not None
        if not os.path.exists(original):
            result["note"] = "The link is broken: its original is gone."
        elif "source" not in result:
            result["note"] = "Links to a folder that is not a connected library."
        elif result.get("generated"):
            result["note"] = (
                "A generated copy in this tool's format; edit the library original instead."
            )
        return result
    result["status"] = "local"
    result["managed"] = False
    found = locate(cfg, asset.name, kind=kind)
    if found:
        sid, _, name = found[0]
        try:
            same = same_content(asset, source_path(cfg, sid, kind, name))
        except (c.ShareError, OSError):
            same = False
        result.update(source=sid, library_name=name, same_as_library=same)
        result["note"] = (
            f"A copy, the same as {sid}'s version."
            if same
            else f"A copy that differs from {sid}'s version."
        )
    else:
        result["note"] = "A local asset; no connected library has it."
    return result
