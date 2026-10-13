"""Explicit Git operations for registered checkouts; no automatic commits."""

import os
import re
import subprocess
from pathlib import Path
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
            if "\n" in before["push_url"]:
                raise c.ShareError("Multiple push destinations require your Git client")
            output = run(path, "push", "--no-force", "--no-mirror", "--", remote, "HEAD:" + ref)
        return {
            "output": output or f"{action.capitalize()} completed",
            "repository": status(target, source),
        }


def clone(target, name, url, destination, layout=None):
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
    dest = c.safe_path(c.root(dest.parent), dest.name)
    with c.locks([dest]):
        if os.path.lexists(dest):
            raise c.ShareError("Clone destination must not exist")
        run(dest.parent, "clone", "--", url, str(dest))
        # Keep a successful checkout if registration fails; never delete user data.
        c.add_source(target, name, dest, layout)
    return {"path": str(dest), "source": name}
