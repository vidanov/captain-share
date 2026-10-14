"""Explicit Git operations for library checkouts.

Captain Share commits only when a user shares one asset (ADR 005): it stages and
commits exactly that asset's paths with the user's note, never forces a push, and
keeps the commit when a push is refused."""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import config as c
from . import engine as e


def run(path, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND="ssh -oBatchMode=yes")
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "-c", "protocol.ext.allow=never", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise c.ShareError("Git timed out. Check repository status before retrying.") from exc
    if result.returncode:
        raise c.ShareError(result.stderr.strip() or result.stdout.strip() or "Git failed")
    return result.stdout.strip()


def checkout(target, source=""):
    if not source:
        return c.root(target)
    cfg = c.effective(target)
    if source not in cfg["sources"] or not cfg["sources"][source].get("path"):
        raise c.ShareError("Choose a connected library")
    return c.root(cfg["sources"][source]["path"])


def status(target, source=""):
    path = checkout(target, source)
    repo = e.git_root(path)
    if repo is None:
        return {"path": str(path), "is_repo": False}
    raw = e.git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    parts = raw.decode(errors="replace").split("\0")
    changes = []
    index = 0
    while index < len(parts) and parts[index]:
        item = parts[index]
        row = {"status": item[:2], "path": item[3:]}
        if "R" in item[:2] or "C" in item[:2]:
            index += 1
            row["from"] = parts[index]
        changes.append(row)
        index += 1

    def optional(*args):
        result = e.git(repo, *args, check=False)
        return result.stdout.decode(errors="replace").strip() if result.returncode == 0 else ""

    branch = optional("symbolic-ref", "--quiet", "--short", "HEAD")
    upstream = optional("rev-parse", "--abbrev-ref", "@{upstream}")
    remote = optional("config", "--get", f"branch.{branch}.remote") or "origin"
    remote_ref = optional("config", "--get", f"branch.{branch}.merge") or f"refs/heads/{branch}"
    remote_url = optional("remote", "get-url", remote)
    push_url = optional("remote", "get-url", "--push", "--all", remote)
    counts = optional("rev-list", "--left-right", "--count", "HEAD...@{upstream}").split()
    result = {
        "path": str(repo),
        "is_repo": True,
        "branch": branch,
        "upstream": upstream,
        "remote": remote,
        "remote_url": remote_url,
        "push_url": push_url,
        "remote_ref": remote_ref,
        "head": optional("rev-parse", "HEAD"),
        "changes": changes,
        "clean": not changes,
        "ahead": int(counts[0]) if len(counts) == 2 else None,
        "behind": int(counts[1]) if len(counts) == 2 else None,
    }
    result["fingerprint"] = e.digest(result)
    return result


def push_checked(path, before):
    """Push the current branch to its own upstream branch: never forced, one URL."""
    if not before.get("branch") or not before.get("head") or not before.get("remote_url"):
        raise c.ShareError("A branch, commit and remote are required")
    remote, ref = before["remote"], before["remote_ref"]
    if remote.startswith("-") or not ref.startswith("refs/heads/"):
        raise c.ShareError("Unsupported remote or branch")
    if "\n" in before["push_url"]:
        raise c.ShareError("Multiple push destinations require your Git client")
    return run(path, "push", "--no-force", "--no-mirror", "--", remote, "HEAD:" + ref)


def operate(target, source, action, fingerprint):
    if action not in ("pull", "push"):
        raise c.ShareError("Unknown Git operation")
    path = checkout(target, source)
    with c.locks([path]):
        before = status(target, source)
        if not before.get("is_repo") or before.get("fingerprint") != fingerprint:
            raise c.ShareError("Repository changed. Refresh and review again.")
        if not before["branch"] or not before["head"] or not before["remote_url"]:
            raise c.ShareError("A branch, commit and remote are required")
        remote, ref = before["remote"], before["remote_ref"]
        if remote.startswith("-") or not ref.startswith("refs/heads/"):
            raise c.ShareError("Unsupported remote or branch")
        if action == "pull":
            if not before["clean"]:
                raise c.ShareError("Commit or stash local changes before pulling")
            output = run(path, "pull", "--ff-only", "--no-rebase", remote, ref)
        else:
            output = push_checked(path, before)
        return {
            "output": output or f"{action.capitalize()} completed",
            "repository": status(target, source),
        }


def clone(target, name, url, destination, layout=None, create_parent=False):
    # Without a layout, the cloned checkout decides (see config.detect_layout).
    if layout not in (None, "ip", "flat"):
        raise c.ShareError("Unknown layout")
    if name in c.effective(target)["sources"]:
        raise c.ShareError("A library with that name is already connected")
    parsed = urlparse(url)
    valid_url = (
        parsed.scheme in ("https", "ssh")
        and parsed.hostname
        and not parsed.password
        and not (parsed.scheme == "https" and parsed.username)
    )
    if not (valid_url or re.fullmatch(r"[\w.-]+@[\w.-]+:[^\s]+", url) or Path(url).is_absolute()):
        raise c.ShareError("Use an HTTPS/SSH repository URL or an absolute local repository path")
    dest = Path(destination).expanduser()
    if not dest.is_absolute():
        raise c.ShareError("Choose an absolute destination folder")
    # First setup clones into ~/Libraries/NAME; create that one folder, never a chain.
    if create_parent and not dest.parent.exists() and dest.parent.parent.is_dir():
        dest.parent.mkdir()
    dest = c.safe_path(c.root(dest.parent), dest.name)
    with c.locks([dest]):
        if os.path.lexists(dest):
            raise c.ShareError("Clone destination must not exist")
        run(dest.parent, "clone", "--", url, str(dest))
        # Keep a successful checkout if registration fails; never delete user data.
        c.add_source(target, name, dest, layout)
    return {"path": str(dest), "source": name}


SHA = re.compile(r"[0-9a-f]{40,64}\Z")
MAX_NOTE = 10000


def quiet(repo, *args):
    """Read-only Git output, or "" when Git declines (e.g. an empty repository)."""
    proc = e.git(repo, "-c", "core.quotePath=false", *args, check=False)
    return proc.stdout.decode(errors="replace") if proc.returncode == 0 else ""


def library(target, source):
    """A connected library's checkout, asset root, Git root and asset prefix in Git."""
    cfg = c.effective(target)
    path = checkout(target, source)
    base = e.source_path(cfg, source)
    repo = e.git_root(path)
    prefix = None
    if repo is not None:
        try:
            prefix = base.relative_to(repo).as_posix()
        except ValueError as exc:
            raise c.ShareError(f"Library assets are outside its repository: {base}") from exc
        prefix = "" if prefix == "." else prefix
    return {"cfg": cfg, "path": path, "base": base, "repo": repo, "prefix": prefix}


def asset_of(prefix, rel):
    """(kind, name) for a repository-relative path inside a library asset, else None."""
    if prefix:
        if not rel.startswith(prefix + "/"):
            return None
        rel = rel[len(prefix) + 1 :]
    parts = rel.split("/")
    if len(parts) < 2 or parts[0] not in c.KINDS or parts[1] in e.HOUSEKEEPING:
        return None
    if parts[1].startswith(".") or not c.NAME.fullmatch(parts[1]):
        return None
    return parts[0], parts[1]


def history(repo, prefix):
    """Author and date of the newest commit touching each asset: one git log."""
    marker = "\x1e"
    out = quiet(
        repo,
        "log",
        "--no-renames",
        f"--format={marker}%an\x1f%aI",
        "--name-only",
        "--",
        prefix or ".",
    )
    found: dict[tuple[str, str], dict[str, str]] = {}
    who: dict[str, str] = {}
    # Not splitlines(): it also splits on the record and unit separators used here.
    for line in out.split("\n"):
        if line.startswith(marker):
            author, _, when = line[1:].partition("\x1f")
            who = {"by": author, "at": when}
            continue
        key = asset_of(prefix, line) if line and who else None
        if key and key not in found:
            found[key] = who
    return found


def changed_between(repo, prefix, old, new):
    """Assets whose files differ between two commits; empty if either is unknown."""
    if not (isinstance(old, str) and SHA.fullmatch(old) and isinstance(new, str)):
        return set()
    if not SHA.fullmatch(new) or old == new:
        return set()
    out = quiet(repo, "diff", "--no-renames", "--name-only", old, new, "--", prefix or ".")
    return {key for key in (asset_of(prefix, line) for line in out.split("\n")) if key}


def edits(repo, prefix, changes):
    """Uncommitted repository paths grouped by the asset they belong to."""
    grouped: dict[tuple[str, str], list[str]] = {}
    for row in changes:
        for rel in (row.get("path"), row.get("from")):
            key = asset_of(prefix, rel) if rel else None
            if key and rel not in grouped.setdefault(key, []):
                grouped[key].append(rel)
    return grouped


def last_commit_at(repo):
    return quiet(repo, "log", "-1", "--format=%cI").strip() or None


def fetch(target, source):
    """Read-only: download the upstream remote's commits without changing files."""
    path = checkout(target, source)
    with c.locks([path]):
        before = status(target, source)
        if not before.get("is_repo"):
            raise c.ShareError("This library is not a Git repository")
        if not before["remote_url"]:
            raise c.ShareError("This library has no remote to fetch from")
        if before["remote"].startswith("-"):
            raise c.ShareError("Unsupported remote")
        run(path, "fetch", "--", before["remote"])
    return status(target, source)


def commit_blockers(repo):
    """Reasons a partial commit of shared paths cannot be made in this checkout."""
    problems = []
    if e.git(repo, "symbolic-ref", "--quiet", "HEAD", check=False).returncode:
        problems.append("Check out a branch in the library before sharing")
    if e.git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode == 0:
        problems.append("The library has an unfinished merge; finish it first")
    if e.git(repo, "var", "GIT_COMMITTER_IDENT", check=False).returncode:
        problems.append("Git needs your name and email (git config user.name/user.email)")
    return problems


def note_text(note):
    if not isinstance(note, str) or not note.strip():
        raise c.ShareError("Write a short note about what you are sharing")
    if len(note) > MAX_NOTE or "\0" in note:
        raise c.ShareError("The note is too long or contains invalid characters")
    return note.strip()


def commit(repo, paths, note):
    """Commit exactly `paths` with `note`; other staged changes stay staged."""
    run(repo, "add", "--all", "--", *paths)
    if e.git(repo, "diff", "--cached", "--quiet", "--", *paths, check=False).returncode == 0:
        raise c.ShareError("Nothing to commit: the library already has this version")
    run(repo, "commit", "--quiet", "-m", note, "--", *paths)
    return run(repo, "rev-parse", "HEAD")


def publish(target, source, path, push):
    pushed, error = False, None
    if push:
        try:
            push_checked(path, status(target, source))
            pushed = True
        except c.ShareError as exc:
            # The commit stays; the user can push later or resolve the remote.
            error = str(exc)
    return pushed, error


def get_command(target, source, kind, name):
    """The shortest `captain-share get` reference that is unique for this asset."""
    sources: dict[str, Any] = {}
    for place in (c.global_dir().parent, c.root(target)):
        try:
            sources.update(c.effective(place)["sources"])
        except c.ShareError:
            continue
    found = e.locate({"sources": sources}, name)
    if len(found) <= 1:
        ref = name
    elif len([row for row in found if row[0] == source]) == 1:
        ref = f"{source}/{name}"
    else:
        ref = f"{source}/{kind}/{name}"
    return f"captain-share get {ref}"


def share_plan(target, source, kind, name, path):
    """Preview copying one project asset into a library and committing only it."""
    target = c.root(target)
    c.name(source)
    c.asset_name(kind, name)
    if not isinstance(path, str):
        raise c.ShareError("Choose the asset's path in this folder")
    c.safe_path(target, path)
    lib = library(target, source)
    row = {"source": source, "kind": kind, "name": name, "path": path}
    result = e.plan(target, "outbound", selections={c.selection_key(row): row})
    problems, commit_paths, head, pending = [], [], None, ""
    if lib["repo"] is None:
        problems.append(f"Library {source} is not a Git repository, so it cannot record a share")
    else:
        dest = e.source_path(lib["cfg"], source, kind, name)
        commit_paths = [dest.relative_to(lib["repo"]).as_posix()]
        problems += commit_blockers(lib["repo"])
        head = quiet(lib["repo"], "rev-parse", "HEAD").strip() or None
        pending = quiet(lib["repo"], "status", "--porcelain=v1", "--", *commit_paths)
        if not result["changes"] and not pending.strip():
            problems.append(f"The library already has this version of {name}")
    for message in problems:
        result["errors"].append(message)
        result["conflicts"].append(
            {"message": message, "path": None, "key": None, "reason": "repository"}
        )
    result["can_apply"] = not result["errors"]
    result["plan_fingerprint"] = result["fingerprint"]
    result["commit_paths"] = commit_paths
    result["share"] = {
        "target": str(target),
        "source": source,
        "kind": kind,
        "name": name,
        "path": path,
        "repository": str(lib["repo"]) if lib["repo"] else None,
        "head": head,
    }
    result["fingerprint"] = e.digest(
        [result["plan_fingerprint"], result["share"], commit_paths, pending, problems]
    )
    return result


def share_apply(saved, note, push=False):
    """Apply a fresh share preview, commit only its paths, and push when asked."""
    note = note_text(note)
    spec = saved.get("share") if isinstance(saved, dict) else None
    if not isinstance(spec, dict):
        raise c.ShareError("Preview the share before applying it")
    args = {k: spec[k] for k in ("target", "source", "kind", "name", "path")}
    path = checkout(args["target"], args["source"])
    with c.locks([path]):
        current = share_plan(**args)
        if current["fingerprint"] != saved.get("fingerprint"):
            raise c.ShareError(
                "Share preview is stale: files or the library changed. Preview again."
            )
        if not current["can_apply"]:
            raise c.ShareError("; ".join(current["errors"]))
        e.apply({**current, "fingerprint": current["plan_fingerprint"]})
        repo = Path(current["share"]["repository"])
        try:
            sha = commit(repo, current["commit_paths"], note)
        except c.ShareError as exc:
            raise c.ShareError(
                f"Copied into the library, but the commit failed: {exc}. "
                "The copy stays in the library; commit it there or share again."
            ) from exc
        pushed, error = publish(args["target"], args["source"], path, push)
    return {
        "commit": sha,
        "pushed": pushed,
        "push_error": error,
        "get_command": get_command(args["target"], args["source"], args["kind"], args["name"]),
    }


def commit_edits(target, source, kind, name, note, push, fingerprint):
    """Commit uncommitted edits of one library asset (e.g. a linked skill edited in place)."""
    note = note_text(note)
    c.asset_name(kind, name)
    lib = library(target, source)
    with c.locks([lib["path"]]):
        before = status(target, source)
        if not before.get("is_repo") or before.get("fingerprint") != fingerprint:
            raise c.ShareError("Repository changed. Refresh and review again.")
        grouped = edits(lib["repo"], lib["prefix"], before["changes"])
        if not grouped.get((kind, name)):
            raise c.ShareError(f"{name} has no uncommitted changes in {source}")
        problems = commit_blockers(lib["repo"])
        if problems:
            raise c.ShareError("; ".join(problems))
        dest = e.source_path(lib["cfg"], source, kind, name)
        sha = commit(lib["repo"], [dest.relative_to(lib["repo"]).as_posix()], note)
        pushed, error = publish(target, source, lib["path"], push)
    return {
        "commit": sha,
        "pushed": pushed,
        "push_error": error,
        "get_command": get_command(target, source, kind, name),
    }


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
