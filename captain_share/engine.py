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
HOUSEKEEPING = {".DS_Store", ".gitkeep"}


def ignored(path, base):
    return any(part in HOUSEKEEPING for part in path.relative_to(base).parts)


def candidate(path, kind):
    if path.name.startswith("."):
        return False
    if kind == "skills":
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


def snapshot(path):
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
                    stat.S_IMODE(p.stat().st_mode),
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
    elif not path.is_file():
        raise c.ShareError(f"Asset is not a file: {path}")
    files = sorted(path.rglob("*")) if path.is_dir() else [path]
    for p in files:
        if p != path and ignored(p, path):
            continue
        if p.is_symlink():
            raise c.ShareError(f"Asset contains a symlink: {p}")
        if p.name in {".git", ".env", "node_modules", "__pycache__"}:
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


def catalog(target, source):
    cfg = c.effective(target)
    base = source_path(cfg, source)
    rows = []
    for kind in c.KINDS:
        directory = c.safe_path(base, kind)
        if not directory.is_dir():
            continue
        for item in sorted(directory.iterdir()):
            if not candidate(item, kind):
                continue
            row = {"source": source, "kind": kind, "name": item.name, "path": str(item)}
            try:
                c.asset_name(kind, item.name)
                validate_asset(item, kind)
                row["status"] = "available"
            except c.ShareError as exc:
                row.update(status="invalid", error=str(exc))
            rows.append(row)
    return rows


def scan(target):
    target = c.root(target)
    rows, seen = [], set()
    for kind, dest in c.KINDS.items():
        directories = [dest, f"ip/{kind}"]
        if kind in ("skills", "agents", "prompts", "steering"):
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
        if not any(
            rel == f"{dest}/{row.get('alias', row.get('name', ''))}" for dest in c.KINDS.values()
        ):
            raise c.ShareError(f"Invalid managed destination: {rel}")
        c.asset_name(row["kind"], row["name"])
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


def plan(target, direction="inbound", action="sync", kind=None, asset=None, archive_id=None):
    target = c.root(target)
    if direction not in ("inbound", "outbound", "both"):
        raise c.ShareError("Unknown direction")
    if action not in ("sync", "remove", "restore"):
        raise c.ShareError("Unknown plan action")
    c.safe_path(target, ".captain/archive")
    c.safe_path(target, ".captain/.gitignore")
    cfg, old = c.effective(target), state(target)
    next_state = json.loads(json.dumps(old))
    changes, observations, errors = [], [], []
    config_update = None
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
            for key, row in cfg["inbound"].items():
                try:
                    alias = row.get("alias", row["name"])
                    rel = f"{c.KINDS[row['kind']]}/{alias}"
                    if rel in desired:
                        raise c.ShareError(f"Two selections target {rel}")
                    src = source_path(cfg, row["source"], row["kind"], row["name"])
                    validate_asset(src, row["kind"])
                    observe(src)
                    dest = c.safe_path(target, rel, leaf_link=True)
                    if tracked(target, dest):
                        raise c.ShareError(f"Destination is Git-tracked: {rel}")
                    existing = observe(dest)
                    previous = old["inbound"].get(rel)
                    if existing and not previous:
                        raise c.ShareError(
                            f"Existing unmanaged asset at {rel}; archive it explicitly before connecting"
                        )
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
                    desired[rel] = {**row, "source_path": str(src), "key": key}
                except (c.ShareError, OSError) as exc:
                    errors.append(str(exc))
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
                    errors.append(str(exc))
            next_state["inbound"] = desired
        if direction in ("outbound", "both"):
            destinations = set()
            for key, row in cfg["outbound"].items():
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
                    errors.append(str(exc))
    elif action == "remove":
        c.asset_name(kind, asset)
        rel = f"{c.KINDS[kind]}/{asset}"
        dest = c.safe_path(target, rel, leaf_link=True)
        existing = observe(dest)
        if existing is None:
            errors.append(f"Asset is absent: {rel}")
        elif tracked(target, dest):
            errors.append(f"Asset is Git-tracked; remove it through its owning repository: {rel}")
        else:
            previous = old["inbound"].get(rel)
            archive(dest, previous)
            next_state["inbound"].pop(rel, None)
            config_update = local_cfg
            for key, row in cfg["inbound"].items():
                if f"{c.KINDS[row['kind']]}/{row.get('alias', row['name'])}" == rel:
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
            [f"{directory}/" for directory in c.KINDS.values()]
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
            errors.append("Archive payload changed or is missing")
        if observe(dest) is not None:
            errors.append("Restore destination already exists")
        if tracked(target, dest):
            errors.append("Restore destination is Git-tracked")
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
                k: v for k, v in previous.items() if k not in ("source_path", "key")
            }
    exclusion = exclude_change(target, next_state["inbound"])
    # Local records, archives and bindings must never be quietly written over tracked state.
    for rel in (".captain/sharing.local.json", ".captain/sharing-state.json", ".captain/archive"):
        if tracked(target, target / rel):
            errors.append(f"Local installation state is Git-tracked: {rel}")
    spec = {
        "target": str(target),
        "direction": direction,
        "action": action,
        "kind": kind,
        "asset": asset,
        "archive_id": archive_id,
    }
    result = {
        "version": 1,
        "spec": spec,
        "changes": changes,
        "errors": errors,
        "can_apply": not errors,
        "observations": observations,
        "next_state": next_state,
        "config_update": config_update,
        "exclusion": exclusion,
        "sources": cfg["sources"],
    }
    result["fingerprint"] = digest(result)
    return result


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
