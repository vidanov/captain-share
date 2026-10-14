"""Update Captain Share itself (ADR 015): find how this copy was installed, show what
is new, and update it the same way. Only fast-forward pulls and pip reinstalls from
the recorded source; nothing is forced and a checkout with local edits is left alone."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from . import __version__
from . import config as c
from . import repositories as r

PACKAGE = Path(__file__).resolve().parent


def run(path, *args, timeout=120):
    """Git in `path` without prompts; a refusal becomes a friendly ShareError."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    proc = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, env=env, timeout=timeout
    )
    if proc.returncode:
        raise r.friendly(proc.stderr, path)
    return proc.stdout.strip()


def quiet(path, *args):
    try:
        return run(path, *args, timeout=30)
    except (c.ShareError, OSError, subprocess.TimeoutExpired):
        return ""


def checkout_of(folder):
    """The Git checkout whose captain_share package is `folder`'s, or None."""
    top = quiet(folder, "rev-parse", "--show-toplevel")
    if not top:
        return None
    top = Path(top).resolve()
    return top if (top / "captain_share" / "__init__.py").is_file() else None


def direct_url():
    """pip's record of where this package was installed from (PEP 610), or {}."""
    try:
        from importlib import metadata

        text = metadata.distribution("captain-share").read_text("direct_url.json")
    except Exception:
        return {}
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def installation():
    """How this copy runs: from a Git checkout, or installed by pip from a Git URL or
    a local folder; `kind` is "checkout", "pip-git", "pip-folder" or "unknown"."""
    found: dict[str, Any] = {"version": __version__, "package": str(PACKAGE)}
    repo = checkout_of(PACKAGE.parent)
    if repo is not None:
        return {**found, "kind": "checkout", "path": str(repo)}
    record = direct_url()
    url = record.get("url", "")
    vcs = record.get("vcs_info")
    if isinstance(vcs, dict) and vcs.get("vcs") == "git" and url:
        return {
            **found,
            "kind": "pip-git",
            "url": url,
            "ref": vcs.get("requested_revision"),
            "commit": vcs.get("commit_id"),
        }
    if url.startswith("file://"):
        folder = Path(unquote(urlparse(url).path))
        repo = checkout_of(folder) if folder.is_dir() else None
        editable = bool((record.get("dir_info") or {}).get("editable"))
        if repo is not None:
            return {**found, "kind": "pip-folder", "path": str(repo), "editable": editable}
    return {**found, "kind": "unknown"}


def describe(repo):
    """Version facts of a checkout: commit, its date, branch and upstream."""
    return {
        "commit": quiet(repo, "rev-parse", "--short", "HEAD") or None,
        "date": r.iso(quiet(repo, "log", "-1", "--format=%cI")) or None,
        "branch": quiet(repo, "symbolic-ref", "--quiet", "--short", "HEAD") or None,
        "upstream": quiet(repo, "rev-parse", "--abbrev-ref", "@{upstream}") or None,
        "dirty": bool(quiet(repo, "status", "--porcelain", "--untracked-files=no")),
    }


def status(fetch=False):
    """The installation, the installed version and what is new upstream. With
    `fetch`, asks the server first (read-only: remote-tracking refs only)."""
    found: dict[str, Any] = installation()
    if found["kind"] in ("checkout", "pip-folder"):
        repo = Path(found["path"])
        found.update(describe(repo))
        if fetch and found["upstream"]:
            run(repo, "fetch", "--quiet")
        if found["upstream"]:
            counts = quiet(repo, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
            ahead, _, behind = counts.partition("\t")
            found["ahead"] = int(ahead or 0)
            found["behind"] = int(behind or 0)
            log = quiet(repo, "log", "--format=%h %s", "-n", "20", "HEAD..@{upstream}")
            found["incoming"] = [line for line in log.splitlines() if line]
    elif found["kind"] == "pip-git" and fetch:
        ref = found.get("ref") or "HEAD"
        url = found["url"]
        head = run(Path.cwd(), "ls-remote", url, ref).split("\t")[0]
        found["latest"] = head[:12] or None
        found["behind"] = None if not head else int(head != found.get("commit"))
    found["command"] = manual(found)
    return found


def manual(found):
    """The command that updates this installation by hand."""
    if found["kind"] == "checkout":
        return f"git -C {found['path']} pull --ff-only"
    if found["kind"] == "pip-folder":
        if found.get("editable"):
            return f"git -C {found['path']} pull --ff-only"
        return f"git -C {found['path']} pull --ff-only && {sys.executable} -m pip install --upgrade {found['path']}"
    if found["kind"] == "pip-git":
        ref = f"@{found['ref']}" if found.get("ref") else ""
        return f"{sys.executable} -m pip install --upgrade git+{found['url']}{ref}"
    return "Reinstall Captain Share the way you installed it."


def update():
    """Update this installation the way it was installed. A checkout must be clean,
    on a branch with an upstream, and not ahead of it (fast-forward only)."""
    found = status(fetch=True)
    kind = found["kind"]
    if kind == "unknown":
        raise c.ShareError(
            "Captain Share cannot tell how it was installed here.",
            "Update it the way you installed it.",
        )
    if kind in ("checkout", "pip-folder"):
        repo = Path(found["path"])
        if found["dirty"]:
            raise c.ShareError(
                "The Captain Share checkout has local changes; nothing was updated.",
                "Commit or stash them, then update again.",
                f"git -C {repo} status",
            )
        if not found["upstream"]:
            raise c.ShareError(
                "The Captain Share checkout has no upstream branch to update from.",
                "Switch to the main branch, then update again.",
                f"git -C {repo} switch main",
            )
        if found.get("ahead"):
            raise c.ShareError(
                "The Captain Share checkout has commits the server does not have.",
                "Push or move them first; Captain Share only fast-forwards.",
                f"git -C {repo} log @{{upstream}}..HEAD --oneline",
            )
        before = found["commit"]
        if found.get("behind"):
            run(repo, "pull", "--ff-only", "--quiet")
        if kind == "pip-folder" and not found.get("editable") and found.get("behind"):
            pip(["install", "--quiet", "--upgrade", str(repo)])
        after = describe(repo)["commit"]
        return {"updated": before != after, "from": before, "to": after, "kind": kind}
    ref = f"@{found['ref']}" if found.get("ref") else ""
    if found.get("behind") == 0:
        return {
            "updated": False,
            "from": found.get("commit"),
            "to": found.get("commit"),
            "kind": kind,
        }
    pip(["install", "--quiet", "--upgrade", f"git+{found['url']}{ref}"])
    return {"updated": True, "from": found.get("commit"), "to": found.get("latest"), "kind": kind}


def pip(args):
    proc = subprocess.run(
        [sys.executable, "-m", "pip", *args], capture_output=True, text=True, timeout=600
    )
    if proc.returncode:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-20:])
        raise c.ShareError("pip could not update Captain Share.", None, None, tail)


def restart_argv():
    """The command line that starts this process again, without opening a new tab."""
    argv = [arg for arg in sys.argv if arg != "--open"]
    if argv and argv[0].endswith("__main__.py"):
        return [sys.executable, "-m", "captain_share", *argv[1:]]
    return [sys.executable, *argv]
