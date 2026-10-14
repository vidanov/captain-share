"""Explicit Git operations for library checkouts.

Captain Share commits only when a user shares one asset (ADR 005): it stages and
commits exactly that asset's paths with the user's note, never forces a push, and
keeps the commit when a push is refused. Review mode commits on a new branch built
from a temporary index instead (ADR 007, ADR 009); Git failures are explained with
a hint and, where one helps, an exact command (ADR 009)."""

from __future__ import annotations

import difflib
import getpass
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from . import config as c
from . import engine as e


def run(path, *args):
    return run_with(path, args).strip()


def run_with(path, args, env_extra=None, data=None, text=True, combined=False):
    """Git without inherited GIT_* variables (only `env_extra`), optionally with stdin.
    `combined` returns stderr too (push progress and host messages are on stderr)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND="ssh -oBatchMode=yes")
    env.update(env_extra or {})
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "-c", "protocol.ext.allow=never", *args],
            env=env,
            input=data,
            capture_output=True,
            text=text,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise c.ShareError("Git timed out. Check repository status before retrying.") from exc
    if result.returncode:
        err = result.stderr if text else result.stderr.decode(errors="replace")
        out = result.stdout if text else ""
        raise friendly(err.strip() or out.strip() or "Git failed", path)
    if combined:
        return (result.stdout or "") + (result.stderr or "")
    return result.stdout


CREDENTIALS = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/@\s]+@", re.I)
HTTPS_LOGIN = (
    "could not read username",
    "could not read password",
    "authentication failed",
    "http basic: access denied",
    "terminal prompts disabled",
)
SIGNING_LOCAL = (
    "gpg failed to sign",
    "failed to sign the data",
    "secret key not available",
    "no secret key",
    "cannot run gpg",
    "couldn't load public key",
    "signing failed",
)
SIGNING_REMOTE = ("must be signed", "signed commits", "unsigned", "not signed", "gpg", "signature")
PROTECTED = ("protected branch", "not allowed to push", "pre-receive hook declined")
BEHIND = ("fetch first", "non-fast-forward")


def redact_text(text):
    """Text with credentials removed from any URL in it."""
    return CREDENTIALS.sub(r"\1", str(text))


def ssh_address(url):
    """git@HOST:PATH.git for an https remote (GitHub, GitLab and others alike)."""
    parsed = urlparse(url or "")
    parts = remote_parts(url) if parsed.scheme in ("http", "https") else None
    return f"git@{parts[0]}:{parts[1]}.git" if parts else None


def remote_of(path):
    """(remote name, redacted URL) of a checkout's current branch, or (None, None)."""
    try:
        repo = e.git_root(path)
    except (c.ShareError, OSError):
        repo = None
    if repo is None:
        return None, None
    branch = quiet(repo, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
    remote = quiet(repo, "config", "--get", f"branch.{branch}.remote").strip() or "origin"
    url = quiet(repo, "remote", "get-url", remote).strip()
    return remote, (redact(url) if url else None)


def friendly(stderr, path=None):
    """A ShareError explaining a Git failure: message, hint, command and the original
    (redacted) Git text as detail."""
    detail = redact_text(stderr).strip()
    low = detail.lower()
    where = str(path) if path else "LIBRARY"
    remote, url = remote_of(path) if path else (None, None)
    host = (remote_parts(url) or ("HOST", ""))[0] if url else "HOST"

    def has(words):
        return any(word in low for word in words)

    if has(HTTPS_LOGIN):
        ssh = ssh_address(url)
        return c.ShareError(
            "The library's HTTPS address needs a login, and Captain Share cannot type passwords.",
            "Switch the library to its SSH address (with your SSH key added to your account), "
            "or set up a Git credential helper or access token for HTTPS, then try again.",
            f"git -C {where} remote set-url {remote or 'origin'} {ssh}" if ssh else None,
            detail,
        )
    if "permission denied (publickey)" in low:
        return c.ShareError(
            "The Git host did not accept your SSH key.",
            f"Add your public SSH key to your account on {host} and load it into ssh-agent; "
            "the command below tests the connection.",
            f"ssh -T git@{host}",
            detail,
        )
    if "host key verification failed" in low:
        return c.ShareError(
            "This computer does not know the Git host's SSH key yet.",
            "Connect once in a terminal and confirm the host key, then try again.",
            f"ssh -T git@{host}",
            detail,
        )
    if has(SIGNING_LOCAL):
        return c.ShareError(
            "Git could not sign the commit.",
            "Check that your signing key is set (user.signingkey) and can sign without a "
            "terminal prompt, or turn off signing for this library in Settings.",
            f"git -C {where} config user.signingkey YOUR_KEY_ID",
            detail,
        )
    if has(SIGNING_REMOTE) and ("remote" in low or "rejected" in low or "hook" in low):
        return c.ShareError(
            "The library only accepts signed commits.",
            "Turn on Sign commits for this library in Settings (or run the command below, "
            "with user.signingkey set), then share again. The unsigned commit was not pushed.",
            f"git -C {where} config commit.gpgsign true",
            detail,
        )
    if has(PROTECTED):
        return c.ShareError(
            "The library's branch is protected and only accepts changes through review.",
            "Use Ask for review: Captain Share pushes a new branch and opens a merge request. "
            "The commit made here stays in the library checkout, not pushed.",
            None,
            detail,
        )
    if has(BEHIND):
        return c.ShareError(
            "The library has newer commits on the server.",
            "Update library first, then push or share again.",
            None,
            detail,
        )
    return c.ShareError(detail or "Git failed", detail=None)


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


def push_checked(path, before, branch=None, options=()):
    """Push the current branch to its own upstream branch, or a new review branch to
    the same remote: never forced, one push URL. Returns Git's output, host
    messages included."""
    if not before.get("remote_url"):
        raise c.ShareError(
            "This library has no remote to push to; it lives only on this machine",
            hint="Add a remote with git remote add origin URL, or share to a team library.",
        )
    if not before.get("head"):
        raise c.ShareError("A commit and a remote are required")
    if branch is None and not before.get("branch"):
        raise c.ShareError("A branch, commit and remote are required")
    remote, ref = before["remote"], before["remote_ref"]
    if branch is not None:
        ref = f"refs/heads/{branch}"
    if remote.startswith("-") or not ref.startswith("refs/heads/"):
        raise c.ShareError("Unsupported remote or branch")
    if "\n" in before["push_url"]:
        raise c.ShareError("Multiple push destinations require your Git client")
    source = "HEAD" if branch is None else ref
    flags = [arg for option in options for arg in ("-o", option)]
    return run_with(
        path,
        ["push", "--no-force", "--no-mirror", *flags, "--", remote, f"{source}:{ref}"],
        combined=True,
    ).strip()


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
        after = status(target, source)
        if action == "pull":
            remember(source, after)
        return {"output": output or f"{action.capitalize()} completed", "repository": after}


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


def older_version(repo, prefix, kind, name, local, limit=100):
    """The library commit whose version of this asset equals the local copy, or None.
    A copy that matches an earlier team version is outdated, not the user's edits."""
    asset = "/".join(part for part in (prefix, kind, name) if part)
    local = Path(local)
    if local.is_symlink():
        return None
    if local.is_file():
        files = {"": local}
    else:
        files = {
            p.relative_to(local).as_posix(): p
            for p in sorted(local.rglob("*"))
            if p.is_file() and not p.is_symlink() and not e.ignored(p, local)
        }
    if not files or len(files) > 500:
        return None
    hashed = quiet(repo, "hash-object", "--no-filters", "--", *map(str, files.values()))
    blobs = hashed.split()
    if len(blobs) != len(files):
        return None
    mine = dict(zip(files, blobs))
    history = quiet(repo, "log", f"-n{limit}", "--format=%H %cI", "--", asset).splitlines()
    for line in history:
        commit, _, when = line.partition(" ")
        listing = quiet(repo, "ls-tree", "-r", "--full-tree", commit, "--", asset)
        theirs = {}
        for row in listing.splitlines():
            meta, _, path = row.partition("\t")
            parts = meta.split()
            if len(parts) != 3 or parts[1] != "blob":
                continue
            rel = "" if path == asset else path[len(asset) + 1 :]
            if any(part in e.HOUSEKEEPING for part in rel.split("/")):
                continue
            theirs[rel] = parts[2]
        if theirs and theirs == mine:
            return {"commit": commit[:10], "at": iso(when)}
    return None


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
    spec = cfg["sources"].get(source, {})
    return {
        "cfg": cfg,
        "path": path,
        "base": base,
        "repo": repo,
        "prefix": prefix,
        "sign": bool(spec.get("sign")),
        "check": spec.get("check"),
    }


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


def iso(value):
    """Git writes UTC as "Z"; Python 3.9/3.10 fromisoformat only reads "+00:00"."""
    value = value.strip()
    return value[:-1] + "+00:00" if value.endswith("Z") else value


def history(repo, prefix):
    """Author and date of the newest commit touching each asset: one git log."""
    marker = "\x1e"
    out = quiet(
        repo,
        "log",
        "--no-renames",
        f"--format={marker}%an\x1f%aI\x1f%ae",
        "--name-only",
        "--",
        prefix or ".",
    )
    found: dict[tuple[str, str], dict[str, str]] = {}
    who: dict[str, str] = {}
    # Not splitlines(): it also splits on the record and unit separators used here.
    for line in out.split("\n"):
        if line.startswith(marker):
            author, _, rest = line[1:].partition("\x1f")
            when, _, email = rest.partition("\x1f")
            who = {"by": author, "at": iso(when), "email": email.strip().lower()}
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
    return iso(quiet(repo, "log", "-1", "--format=%cI")) or None


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
        after = status(target, source)
        remember(source, after)
    return after


FRESH_SECONDS = 6 * 3600


def status_path():
    # Machine-local: when each library was last fetched or pulled (ADR 009).
    return c.config_dir(scope="global") / "library-status.json"


def remembered():
    try:
        data = c.read_json(status_path(), {})
    except c.ShareError:
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def remember(source, st):
    """Record a successful fetch or pull: when, how far behind/ahead, at which head."""
    target = status_path()
    with c.locks([target]):
        data = remembered()
        data[source] = {
            "checked_at": now(),
            "behind": st.get("behind"),
            "ahead": st.get("ahead"),
            "head": st.get("head") or None,
        }
        c.atomic_json(target, data)


def freshness(source, head, at=None):
    """(checked_at, fresh, row): fresh means checked within six hours at this head."""
    row = remembered().get(source)
    if not row or not isinstance(row.get("checked_at"), str):
        return None, False, None
    try:
        checked = datetime.fromisoformat(row["checked_at"])
    except ValueError:
        return None, False, None
    moment = at or datetime.now(timezone.utc)
    age = (moment - checked).total_seconds()
    fresh = 0 <= age <= FRESH_SECONDS and bool(head) and row.get("head") == head
    return row["checked_at"], fresh, row


def default_branch(repo):
    """The library's main branch on origin (origin/HEAD, else main or master), or None
    when the checkout has no such remote (for example the personal library)."""
    ref = quiet(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD").strip()
    if ref.startswith("origin/"):
        return ref[len("origin/") :]
    for name in ("main", "master"):
        if quiet(repo, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{name}").strip():
            return name
    return None


def commit_blockers(repo):
    """Reasons a direct commit of shared paths cannot be made in this checkout."""
    problems = []
    if e.git(repo, "symbolic-ref", "--quiet", "HEAD", check=False).returncode:
        problems.append("Check out a branch in the library before sharing")
    else:
        # A direct share belongs on the library's main branch; a leftover review branch
        # would mix unrelated changes into its merge request.
        current = quiet(repo, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
        main = default_branch(repo)
        if main and current and current != main:
            problems.append(
                f"Your library checkout is on branch {current}, not {main}. Switch back "
                f"with: git -C {repo} switch {main}  (or use Ask for review)"
            )
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


def dirty(repo):
    """Uncommitted paths with the hash of their current content ("" when deleted)."""
    found = {}
    out = e.git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    for item in out.decode(errors="replace").split("\0"):
        if len(item) > 3:
            rel = item[3:]
            full = repo / rel
            found[rel] = quiet(repo, "hash-object", "--", rel).strip() if full.is_file() else ""
    return found


def run_check(repo, check):
    """Run the library's check (the user's per-machine setting) in the repository.
    Returns repository paths it changed, which belong in the commit; raises with its
    output when it fails, before anything is committed."""
    before = dirty(repo)
    shown = " ".join(check)
    try:
        # Fixed argv chosen by the user; no shell.
        proc = subprocess.run(check, cwd=repo, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise c.ShareError(
            f"The library check could not run ({shown}): {exc}. Nothing was committed.",
            "Fix the check command in Settings, or turn it off for this library.",
            f"cd {repo} && {shown}",
            final=True,
        ) from exc
    if proc.returncode:
        output = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-30:])
        raise c.ShareError(
            f"The library check failed ({shown}). Nothing was committed.",
            "The files stay in the library folder. Fix what the check reports there, "
            "then use Share again (or Send changes).",
            f"cd {repo} && {shown}",
            output,
            final=True,
        )
    after = dirty(repo)
    return sorted(rel for rel, digest in after.items() if before.get(rel) != digest)


def commit(repo, paths, note, sign=False, check=None):
    """Commit exactly `paths` with `note`; other staged changes stay staged. Git applies
    the user's own commit.gpgsign; `sign` (the library opt-in) adds -S. With `check`,
    the library's check runs first and the files it changed are committed too."""
    if check:
        paths = list(paths) + [p for p in run_check(repo, check) if p not in paths]
    run(repo, "add", "--all", "--", *paths)
    if e.git(repo, "diff", "--cached", "--quiet", "--", *paths, check=False).returncode == 0:
        raise c.ShareError("Nothing to commit: the library already has this version")
    run(repo, "commit", "--quiet", *(["-S"] if sign else []), "-m", note, "--", *paths)
    return run(repo, "rev-parse", "HEAD")


def signs(lib):
    """Whether review commits (commit-tree) must be signed: the library opt-in, or
    the user's commit.gpgsign, which commit-tree does not apply by itself."""
    if lib["sign"]:
        return True
    return quiet(lib["repo"], "config", "--bool", "commit.gpgsign").strip() == "true"


def sign_check(lib):
    """Before changing anything, prove the signing key works (an unreferenced commit
    object is the only trace). Raises the friendly signing error otherwise."""
    if lib["repo"] is None or not lib["sign"]:
        return
    if not quiet(lib["repo"], "rev-parse", "--verify", "HEAD").strip():
        return
    run_with(
        lib["repo"],
        ["commit-tree", "-S", "HEAD^{tree}", "-m", "Captain Share signing check"],
    )


def push_failure(exc=None):
    """The push_error fields of a result (all None when the push worked)."""
    return {
        "push_error": str(exc) if exc else None,
        "push_error_hint": getattr(exc, "hint", None) if exc else None,
        "push_error_command": getattr(exc, "command", None) if exc else None,
    }


def bad_signature(repo, rev="HEAD"):
    """A ShareError when `rev` carries a signature that does not verify, else None.
    A gpg.program wrapper that reads a passphrase from stdin signs the wrong data:
    Git accepts it, but nobody can verify the commit, so it must not be pushed."""
    mark = quiet(repo, "log", "-1", "--format=%G?", rev).strip()
    if mark != "B":
        return None
    return c.ShareError(
        "The commit's signature does not verify, so it was not pushed.",
        "Your signing setup signed the wrong data; a gpg.program wrapper that passes "
        "the passphrase on standard input does this. Fix it, re-sign the commit with "
        "git commit --amend --no-edit -S in the library, then push.",
        f"git -C {repo} verify-commit {rev}",
    )


def publish(target, source, path, push):
    """(pushed, push_error fields). A refused push keeps the commit."""
    if not push:
        return False, push_failure()
    problem = bad_signature(path)
    if problem is not None:
        return False, push_failure(problem)
    try:
        push_checked(path, status(target, source))
    except c.ShareError as exc:
        return False, push_failure(exc)
    return True, push_failure()


def direct_fields(source, repo, check=None):
    """Keys every apply result has, for a commit on the checkout's own branch:
    which library and branch the commit landed on, and the check that ran first."""
    return {
        "branch": None,
        "review_url": None,
        "waiting_for_review": False,
        "library": source,
        "landed_on": quiet(repo, "symbolic-ref", "--quiet", "--short", "HEAD").strip() or None,
        "checked": " ".join(check) if check else None,
    }


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


def share_plan(target, source, kind, name, path, adopt=False, mode="direct"):
    """Preview copying one project asset into a library and committing only it.
    With `adopt`, also predict swapping the local copies for library links. In
    review mode the commit goes to a new branch and nothing local changes."""
    target = c.root(target)
    c.name(source)
    c.asset_name(kind, name)
    if mode not in ("direct", "review"):
        raise c.ShareError("mode must be direct or review")
    if not isinstance(path, str):
        raise c.ShareError("Choose the asset's path in this folder")
    c.safe_path(target, path)
    lib = library(target, source)
    row = {"source": source, "kind": kind, "name": name, "path": path}
    result = e.plan(target, "outbound", selections={c.selection_key(row): row})
    problems, commit_paths, head, pending = [], [], None, ""
    branch = base = None
    if lib["repo"] is None:
        problems.append(f"Library {source} is not a Git repository, so it cannot record a share")
    else:
        dest = e.source_path(lib["cfg"], source, kind, name)
        commit_paths = [dest.relative_to(lib["repo"]).as_posix()]
        head = quiet(lib["repo"], "rev-parse", "HEAD").strip() or None
        pending = quiet(lib["repo"], "status", "--porcelain=v1", "--", *commit_paths)
        if mode == "direct":
            problems += commit_blockers(lib["repo"])
        else:
            branch, base, _, found = review_prepare(target, source, lib, asset_stem(kind, name))
            problems += found
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
    result["mode"], result["branch"], result["base"] = mode, branch, base
    result["share"] = {
        "target": str(target),
        "source": source,
        "kind": kind,
        "name": name,
        "path": path,
        "mode": mode,
        "repository": str(lib["repo"]) if lib["repo"] else None,
        "head": head,
        "branch": branch,
    }
    result["fingerprint"] = e.digest(
        [result["plan_fingerprint"], result["share"], commit_paths, pending, problems]
    )
    if adopt and mode == "direct":
        # A prediction only: the adopt is planned afresh after the commit.
        result["adopt_changes"] = e.adopt_preview(target, kind, name, path)
    elif adopt:
        # Under review the project keeps its copy until the change is merged.
        result["adopt_changes"] = []
    return result


def adopt_after_share(target, source, kind, name, path):
    """Use the library original in the project instead of its own copy. Returns an
    error message instead of raising, so a failure never undoes the share."""
    try:
        planned = e.plan(target, action="adopt", kind=kind, asset=name, source=source, path=path)
        if not planned["can_apply"]:
            return "; ".join(planned["errors"])
        e.apply(planned)
    except (c.ShareError, OSError) as exc:
        return str(exc)
    return None


def share_apply(saved, note, push=False, adopt=False):
    """Apply a fresh share preview, commit only its paths, push when asked, and then
    with `adopt` link the project to the library original instead of its copy."""
    note = note_text(note)
    spec = saved.get("share") if isinstance(saved, dict) else None
    if not isinstance(spec, dict):
        raise c.ShareError("Preview the share before applying it")
    keys = ("target", "source", "kind", "name", "path")
    args = {k: spec[k] for k in keys}
    mode = spec.get("mode", "direct")
    path = checkout(args["target"], args["source"])
    with c.locks([path]):
        current = share_plan(**args, mode=mode)
        if current["fingerprint"] != saved.get("fingerprint"):
            raise c.ShareError(
                "Share preview is stale: files or the library changed. Preview again."
            )
        if not current["can_apply"]:
            raise c.ShareError("; ".join(current["errors"]))
        lib = library(args["target"], args["source"])
        if mode == "review":
            local = c.safe_path(c.root(args["target"]), args["path"])
            result = review_send(
                args["target"],
                args["source"],
                lib,
                [(current["commit_paths"][0], local)],
                note,
                current["branch"],
                current["base"],
                current["share"]["head"],
                push,
            )
        else:
            sign_check(lib)
            e.apply({**current, "fingerprint": current["plan_fingerprint"]})
            repo = Path(current["share"]["repository"])
            try:
                sha = commit(repo, current["commit_paths"], note, lib["sign"], lib["check"])
            except c.ShareError as exc:
                raise exc.wrap(
                    f"Copied into the library, but the commit failed: {exc}. "
                    "The copy stays in the library; commit it there or share again."
                ) from exc
            pushed, failure = publish(args["target"], args["source"], path, push)
            result = {
                "commit": sha,
                "pushed": pushed,
                **failure,
                **direct_fields(args["source"], repo, lib["check"]),
            }
    # Under review the project keeps its copy until the change is merged.
    adopt_error = adopt_after_share(**args) if adopt and mode == "direct" else None
    return {
        **result,
        "get_command": get_command(args["target"], args["source"], args["kind"], args["name"]),
        "adopted": bool(adopt) and mode == "direct" and adopt_error is None,
        "adopt_error": adopt_error,
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
        sha = commit(
            lib["repo"],
            [dest.relative_to(lib["repo"]).as_posix()],
            note,
            lib["sign"],
            lib["check"],
        )
        pushed, failure = publish(target, source, lib["path"], push)
    return {
        "commit": sha,
        "pushed": pushed,
        **failure,
        **direct_fields(source, lib["repo"], lib["check"]),
        "get_command": get_command(target, source, kind, name),
    }


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact(url):
    """A remote URL without credentials: no user info for http(s), no password else."""
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https") and "@" in parsed.netloc:
        return parsed._replace(netloc=parsed.netloc.rpartition("@")[2]).geturl()
    if parsed.scheme and parsed.password is not None:
        user = parsed.username or ""
        host = parsed.netloc.rpartition("@")[2]
        return parsed._replace(netloc=f"{user}@{host}").geturl()
    return url


def remote_parts(url):
    """(web host, repository path) of an https, ssh or scp-style remote, else None."""
    if not isinstance(url, str) or not url:
        return None
    scp = re.fullmatch(r"(?:[^@/\s]+@)?([\w.-]+):(?!//)([^\s]+)", url)
    if scp and "://" not in url:
        host, path = scp.group(1), scp.group(2)
    else:
        parsed = urlparse(url)
        if parsed.scheme not in ("https", "http", "ssh", "git+ssh") or not parsed.hostname:
            return None
        host, path = parsed.hostname, parsed.path
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    if not path or ".." in path.split("/"):
        return None
    return host.lower(), path


def review_url(remote_url, base, branch):
    """A GitHub compare or GitLab merge-request link for a pushed review branch."""
    parts = remote_parts(remote_url)
    if not parts or not branch:
        return None
    host, path = parts
    if host == "github.com" and path.count("/") == 1 and base:
        compare = quote(f"{base}...{branch}", safe="/.")
        return f"https://github.com/{path}/compare/{compare}?expand=1"
    if host.startswith("gitlab."):
        query = urlencode({"merge_request[source_branch]": branch})
        return f"https://{host}/{path}/-/merge_requests/new?{query}"
    return None


def slug(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")


def asset_stem(kind, name):
    return name[: -len(c.SUFFIXES[kind])] if kind in c.SUFFIXES else name


def review_branch(repo, stem):
    """share/STEM-USER, with USER from Git's user.name or the operating-system user."""
    user = quiet(repo, "config", "user.name").strip()
    if not user:
        try:
            user = getpass.getuser()
        except (KeyError, OSError):
            user = ""
    branch = f"share/{slug(stem) or 'asset'}-{slug(user) or 'me'}"
    if e.git(repo, "check-ref-format", f"refs/heads/{branch}", check=False).returncode:
        raise c.ShareError(f"Cannot name a review branch {branch}")
    return branch


def commit_files(repo, commit, rel, name):
    """Relative path -> content of an asset at one commit (single files keyed by name)."""
    listing = run_with(
        repo, ["ls-tree", "-r", "-z", "--full-tree", commit, "--", rel], text=False
    ).split(b"\0")
    entries = []
    for item in listing:
        if not item:
            continue
        meta, _, path = item.partition(b"\t")
        mode, kind, sha = meta.decode().split()
        if kind != "blob":
            continue
        path_text = os.fsdecode(path)
        key = name if path_text == rel else path_text[len(rel) + 1 :]
        if any(part in e.HOUSEKEEPING for part in key.split("/")):
            continue
        entries.append((key, sha, mode))
    if not entries:
        return {}
    out = run_with(
        repo,
        ["cat-file", "--batch"],
        data="".join(sha + "\n" for _, sha, _ in entries).encode(),
        text=False,
    )
    files, offset = {}, 0
    for key, _, mode in entries:
        end = out.index(b"\n", offset)
        size = int(out[offset:end].split()[2])
        data = out[end + 1 : end + 1 + size]
        offset = end + 1 + size + 1
        files[key] = (b"symlink -> " + data) if mode == "120000" else e.bounded(data)
    return files


def version_rows(target):
    """Kept versions recorded at target, with whether the team original changed since."""
    rows, cache = [], {}
    for key, row in sorted(c.versions(target).items()):
        kind, new = key.split("/", 1)
        item = {
            "key": key,
            "kind": kind,
            "name": new,
            "path": None,
            "source": row["source"],
            "of": row["name"],
            "base": row["base"],
            "team_changed": False,
            "team_files": [],
        }
        for folder in c.folders(kind):
            if os.path.lexists(c.safe_path(c.root(target), f"{folder}/{new}", leaf_link=True)):
                item["path"] = f"{folder}/{new}"
                break
        try:
            if row["source"] not in cache:
                cache[row["source"]] = library(target, row["source"])
            lib = cache[row["source"]]
            original = e.source_path(lib["cfg"], row["source"], kind, row["name"])
        except c.ShareError:
            rows.append(item)
            continue
        if lib["repo"] is not None and row["base"]:
            rel = original.relative_to(lib["repo"]).as_posix()
            out = quiet(
                lib["repo"], "diff", "--no-renames", "--name-only", row["base"], "HEAD", "--", rel
            )
            for line in [x for x in out.split("\n") if x]:
                full = lib["repo"] / line
                try:
                    item["team_files"].append(full.relative_to(lib["path"]).as_posix())
                except ValueError:
                    item["team_files"].append(line)
            item["team_changed"] = bool(item["team_files"])
        rows.append(item)
    return rows


def version_diff(target, key):
    """How the team original changed between a kept version's base and HEAD."""
    row = c.versions(target).get(key)
    if row is None:
        raise c.ShareError(f"No kept version {key} here")
    lib = library(target, row["source"])
    if lib["repo"] is None or not row["base"]:
        raise c.ShareError("This version has no recorded library commit to compare with")
    original = e.source_path(lib["cfg"], row["source"], row["kind"], row["name"])
    rel = original.relative_to(lib["repo"]).as_posix()
    if e.git(lib["repo"], "cat-file", "-e", row["base"] + "^{commit}", check=False).returncode:
        raise c.ShareError("The recorded library commit is not in this checkout")
    old = commit_files(lib["repo"], row["base"], rel, row["name"])
    new = commit_files(lib["repo"], "HEAD", rel, row["name"])
    return e.compare(old, new, "then", "now")


def propose_plan(target, source, kind, name, path=None, mode="direct"):
    """Preview sending a local copy's changes to the library original (ADR 007)."""
    target = c.root(target)
    c.name(source)
    c.asset_name(kind, name)
    if mode not in ("direct", "review"):
        raise c.ShareError("mode must be direct or review")
    lib = library(target, source)
    cfg, repo = lib["cfg"], lib["repo"]
    rel = path if path is not None else e.local_copy(target, cfg, e.state(target), kind, name)
    local = c.safe_path(target, rel, leaf_link=True)
    if not os.path.lexists(local) or local.is_symlink():
        raise c.ShareError(f"{rel} is not a local copy of {name}")
    original = e.source_path(cfg, source, kind, name)
    problems: list[tuple[str, str]] = []
    try:
        e.validate_asset(local, kind, outbound=True)
    except c.ShareError as exc:
        problems.append((str(exc), "invalid"))
    exists = os.path.lexists(original)
    if not exists:
        problems.append((f"The library has no {name}; share it as a new asset", "missing"))
    elif e.same_content(local, original):
        problems.append((f"Your copy of {name} is the same as the library version", "same"))
    commit_paths, branch, head, base, pending, push_url = [], None, None, None, "", None
    if repo is None:
        problems.append((f"Library {source} is not a Git repository", "repository"))
    else:
        commit_paths = [original.relative_to(repo).as_posix()]
        st = status(target, source)
        head = st["head"] or None
        base = st["remote_ref"][len("refs/heads/") :] if st["upstream"] else st["branch"]
        push_url = redact(st["push_url"] or st["remote_url"])
        if mode == "direct":
            problems += [(m, "repository") for m in commit_blockers(repo)]
            pending = quiet(repo, "status", "--porcelain=v1", "--", *commit_paths)
            if pending.strip():
                problems.append(
                    (f"The library has uncommitted edits of {name}; commit them first", "edited")
                )
        else:
            branch, _, _, found = review_prepare(target, source, lib, asset_stem(kind, name))
            problems += [(m, "branch" if m.startswith("Branch ") else "repository") for m in found]
    changes = []
    if exists:
        change = {"op": "update", "path": str(original), "source": str(local)}
        if branch:
            change["branch"] = branch
        changes.append(change)
    result: dict[str, Any] = {
        "version": 1,
        "spec": {"type": "propose", "target": str(target), "action": "propose", "direction": mode},
        "propose": {
            "target": str(target),
            "source": source,
            "kind": kind,
            "name": name,
            "path": rel,
            "mode": mode,
        },
        "changes": changes,
        "errors": [m for m, _ in problems],
        "conflicts": [
            {"message": m, "path": rel, "key": None, "reason": why} for m, why in problems
        ],
        "can_apply": not problems,
        "commit_paths": commit_paths,
        "mode": mode,
        "branch": branch,
        "base": base,
        "push_url": push_url,
        "path": rel,
        "observations": [
            e.snapshot(local),
            e.snapshot(original) if exists else None,
            head,
            pending,
        ],
    }
    result["fingerprint"] = e.digest(result)
    return result


def review_commit(repo, entries, note, branch, head, sign=False):
    """Commit `entries` on a new branch from head via a temporary index: the working
    tree, the real index and the current branch are not touched. Each entry is
    (repository path, source) where source is a file or folder Path, bytes for a
    file's content, or None to remove the path."""
    files: list[tuple[str, Any]] = []
    removed = []
    for rel, source in entries:
        removed.append(rel)
        if isinstance(source, (bytes, str)):
            files.append((rel, source.encode() if isinstance(source, str) else source))
        elif isinstance(source, Path) and source.is_file():
            files.append((rel, source))
        elif isinstance(source, Path) and source.is_dir():
            for item in sorted(source.rglob("*")):
                if e.ignored(item, source) or not item.is_file() or item.is_symlink():
                    continue
                files.append((f"{rel}/{item.relative_to(source).as_posix()}", item))
    if any(ch in str(src) for _, src in files if isinstance(src, Path) for ch in "\n\r"):
        raise c.ShareError("File names with line breaks cannot be committed for review")
    with tempfile.TemporaryDirectory(prefix="captain-share-index-") as temporary:
        env = {"GIT_INDEX_FILE": str(Path(temporary) / "index")}
        run_with(repo, ["read-tree", head], env)
        run_with(repo, ["rm", "-r", "--cached", "--quiet", "--ignore-unmatch", "--", *removed], env)
        on_disk = [(rel, src) for rel, src in files if isinstance(src, Path)]
        shas = dict(
            zip(
                [rel for rel, _ in on_disk],
                run_with(
                    repo,
                    ["hash-object", "-w", "--no-filters", "--stdin-paths"],
                    env,
                    data="".join(f"{src}\n" for _, src in on_disk),
                ).split()
                if on_disk
                else [],
            )
        )
        info = ""
        for rel, src in files:
            if isinstance(src, Path):
                mode = "100755" if os.access(src, os.X_OK) else "100644"
                sha = shas[rel]
            else:
                mode = "100644"
                sha = run_with(repo, ["hash-object", "-w", "--stdin"], env, data=src, text=False)
                sha = sha.decode().strip()
            info += f"{mode} {sha}\t{rel}\0"
        if info:
            run_with(repo, ["update-index", "-z", "--add", "--index-info"], env, data=info)
        tree = run_with(repo, ["write-tree"], env).strip()
    signed = ["-S"] if sign else []
    commit_id = run_with(repo, ["commit-tree", *signed, tree, "-p", head, "-m", note]).strip()
    # The empty old value refuses to replace an existing branch.
    run_with(repo, ["update-ref", f"refs/heads/{branch}", commit_id, ""])
    return commit_id


def review_prepare(target, source, lib, stem):
    """(branch, base, head, problems) for a review commit: a new share/STEM-USER branch
    from the checkout's HEAD, aimed at the upstream (or current) branch."""
    repo, problems = lib["repo"], []
    if e.git(repo, "var", "GIT_COMMITTER_IDENT", check=False).returncode:
        problems.append("Git needs your name and email (git config user.name/user.email)")
    st = status(target, source)
    head = st["head"] or None
    if not head:
        problems.append("The library has no commits yet")
    base = st["remote_ref"][len("refs/heads/") :] if st["upstream"] else st["branch"]
    branch = review_branch(repo, stem)
    ref = f"refs/heads/{branch}"
    if e.git(repo, "rev-parse", "-q", "--verify", ref, check=False).returncode == 0:
        problems.append(f"Branch {branch} already exists in the library")
    return branch, base or None, head, problems


REVIEW_LINK = re.compile(
    r"https?://\S*?(?:/-/merge_requests/\S+|/merge_requests/\S+|/pull/new/\S+)"
)


def pushed_review_url(output):
    """The merge or pull request link a host printed while receiving a push."""
    for line in output.splitlines():
        found = REVIEW_LINK.search(line)
        if found:
            return redact_text(found.group(0))
    return None


def review_options(remote_url, base, note):
    """GitLab push options that open the merge request while pushing."""
    parts = remote_parts(remote_url)
    if not parts or not parts[0].startswith("gitlab.") or not base:
        return []
    title = note.strip().splitlines()[0][:255]
    return [
        "merge_request.create",
        f"merge_request.target={base}",
        f"merge_request.title={title}",
    ]


def review_send(target, source, lib, entries, note, branch, base, head, push):
    """Commit entries on the review branch, push it when asked, and find its link."""
    sha = review_commit(lib["repo"], entries, note, branch, head, signs(lib))
    pushed, failure, url = False, push_failure(), None
    problem = bad_signature(lib["repo"], sha) if push else None
    if problem is not None:
        failure = push_failure(problem)
    elif push:
        before = status(target, source)
        try:
            output = push_checked(
                lib["path"], before, branch, review_options(before["remote_url"], base, note)
            )
            pushed = True
            url = pushed_review_url(output) or review_url(before["remote_url"], base, branch)
        except c.ShareError as exc:
            failure = push_failure(exc)
    return {
        "commit": sha,
        "pushed": pushed,
        **failure,
        "branch": branch,
        "review_url": url,
        "waiting_for_review": True,
        "library": source,
        "landed_on": branch,
    }


def propose_apply(saved, note, push=False):
    """Apply a fresh propose preview: update the library original directly (commit,
    push, then adopt) or on a review branch that is pushed for review."""
    note = note_text(note)
    spec = saved.get("propose") if isinstance(saved, dict) else None
    if not isinstance(spec, dict):
        raise c.ShareError("Preview the proposal before applying it")
    args = {k: spec[k] for k in ("target", "source", "kind", "name", "path", "mode")}
    lib = library(args["target"], args["source"])
    adopted, adopt_error = False, None
    with c.locks([lib["path"]]):
        current = propose_plan(**args)
        if current["fingerprint"] != saved.get("fingerprint"):
            raise c.ShareError("Preview is stale: files or the library changed. Preview again.")
        if not current["can_apply"]:
            raise c.ShareError("; ".join(current["errors"]))
        repo = lib["repo"]
        local = c.safe_path(c.root(args["target"]), args["path"], leaf_link=True)
        original = e.source_path(lib["cfg"], args["source"], args["kind"], args["name"])
        if args["mode"] == "direct":
            sign_check(lib)
            with c.locks([lib["path"] / ".captain"]):
                e.replace_original(local, original, lib["path"])
            try:
                sha = commit(repo, current["commit_paths"], note, lib["sign"], lib["check"])
            except c.ShareError as exc:
                raise exc.wrap(
                    f"Updated the library, but the commit failed: {exc}. The previous "
                    "version is archived in the library; commit there or restore it."
                ) from exc
            pushed, failure = publish(args["target"], args["source"], lib["path"], push)
            result = {
                "commit": sha,
                "pushed": pushed,
                **failure,
                **direct_fields(args["source"], repo, lib["check"]),
            }
        else:
            entries = [(current["commit_paths"][0], local)]
            result = review_send(
                args["target"],
                args["source"],
                lib,
                entries,
                note,
                current["branch"],
                current["base"],
                current["observations"][2],
                push,
            )
    if args["mode"] == "direct":
        adopt_error = adopt_after_share(
            args["target"], args["source"], args["kind"], args["name"], args["path"]
        )
        adopted = adopt_error is None
    return {**result, "adopted": adopted, "adopt_error": adopt_error}


STARTER = "profiles/starter.manifest"
STARTER_HEADER = "# Starter set: offered to new teammates by Captain Share\n"


def starter_text(assets):
    """Manifest text: one `kind: names` line per kind in KINDS order, names sorted."""
    by_kind: dict[str, set[str]] = {}
    for ref in assets:
        kind, _, name = ref.partition("/") if isinstance(ref, str) else ("", "", "")
        by_kind.setdefault(kind, set()).add(name)
    lines = [f"{k}: {' '.join(sorted(by_kind[k]))}\n" for k in c.KINDS if k in by_kind]
    return STARTER_HEADER + "".join(lines)


def starter_plan(target, source, assets, clear=False, mode="direct"):
    """Preview writing (or with `clear`, removing) a library's starter set, directly
    or on a review branch."""
    target = c.root(target)
    c.name(source)
    if mode not in ("direct", "review"):
        raise c.ShareError("mode must be direct or review")
    if not isinstance(assets, list) or len(assets) > 500:
        raise c.ShareError("Expected a list of at most 500 kind/name assets")
    if not isinstance(clear, bool):
        raise c.ShareError("clear must be true or false")
    if clear and assets:
        raise c.ShareError("Clear the starter set or choose assets, not both")
    if not clear and not assets:
        raise c.ShareError("Choose at least one asset, or clear the starter set")
    lib = library(target, source)
    for ref in assets:
        kind, sep, name = ref.partition("/") if isinstance(ref, str) else ("", "", "")
        if not sep or kind not in c.KINDS:
            raise c.ShareError(f"Use kind/name for starter assets: {ref!r}")
        c.asset_name(kind, name)
        if not c.DESTINATIONS[kind]:
            raise c.ShareError(f"{kind} are installed by their tool and cannot be in a starter set")
        original = e.source_path(lib["cfg"], source, kind, name)
        if not os.path.lexists(original):
            raise c.ShareError(f"The library {source} has no {ref}")
        e.validate_asset(original, kind)
    file = c.safe_path(lib["path"], STARTER)
    exists = file.is_file()
    current = file.read_text(errors="replace") if exists else ""
    wanted = None if clear else starter_text(assets)
    problems, commit_paths, pending, head = [], [], "", None
    branch = base = None
    if clear and not exists:
        problems.append(("This library has no starter set to clear", "same"))
    elif wanted == current:
        problems.append(("The starter set already has exactly these assets", "same"))
    if lib["repo"] is None:
        problems.append((f"Library {source} is not a Git repository", "repository"))
    else:
        commit_paths = [file.relative_to(lib["repo"]).as_posix()]
        head = quiet(lib["repo"], "rev-parse", "HEAD").strip() or None
        pending = quiet(lib["repo"], "status", "--porcelain=v1", "--", *commit_paths)
        if mode == "review":
            branch, base, _, found = review_prepare(target, source, lib, "starter-set")
            problems += [(m, "branch" if m.startswith("Branch ") else "repository") for m in found]
        else:
            problems += [(m, "repository") for m in commit_blockers(lib["repo"])]
        if pending.strip() and mode == "direct":
            problems.append(
                (
                    "The starter set has uncommitted changes in the library; commit them first",
                    "edited",
                )
            )
    diff = "".join(
        difflib.unified_diff(
            current.splitlines(True),
            (wanted or "").splitlines(True),
            fromfile=f"a/{STARTER}" if exists else "/dev/null",
            tofile=f"b/{STARTER}" if wanted is not None else "/dev/null",
        )
    )
    result: dict[str, Any] = {
        "version": 1,
        "spec": {"type": "starter", "target": str(target), "action": "starter"},
        "starter": {
            "target": str(target),
            "source": source,
            "assets": assets,
            "clear": clear,
            "mode": mode,
        },
        "mode": mode,
        "branch": branch,
        "base": base,
        "changes": [{"op": "remove" if clear else "write", "path": STARTER}],
        "diff": diff,
        "content": wanted,
        "errors": [m for m, _ in problems],
        "conflicts": [
            {"message": m, "path": STARTER, "key": None, "reason": why} for m, why in problems
        ],
        "can_apply": not problems,
        "commit_paths": commit_paths,
        "observations": [current if exists else None, head, pending],
    }
    result["fingerprint"] = e.digest(result)
    return result


def starter_apply(saved, note, push=False):
    """Write the previewed starter set, commit only that file, and push when asked."""
    note = note_text(note)
    spec = saved.get("starter") if isinstance(saved, dict) else None
    if not isinstance(spec, dict):
        raise c.ShareError("Preview the starter set before applying it")
    args = {k: spec[k] for k in ("target", "source", "assets", "clear")}
    args["mode"] = spec.get("mode", "direct")
    lib = library(args["target"], args["source"])
    with c.locks([lib["path"], lib["path"] / ".captain"]):
        current = starter_plan(**args)
        if current["fingerprint"] != saved.get("fingerprint"):
            raise c.ShareError("Preview is stale: the starter set changed. Preview again.")
        if not current["can_apply"]:
            raise c.ShareError("; ".join(current["errors"]))
        if args["mode"] == "review":
            content = None if args["clear"] else current["content"]
            return review_send(
                args["target"],
                args["source"],
                lib,
                [(current["commit_paths"][0], content)],
                note,
                current["branch"],
                current["base"],
                current["observations"][1],
                push,
            )
        sign_check(lib)
        file = c.safe_path(lib["path"], STARTER)
        tx = e.Transaction()
        try:
            if args["clear"]:
                # The committed version stays in history; the file itself is archived.
                e.archive_into(tx, file, lib["path"])
            else:
                tx.write(file, current["content"])
            sha = commit(lib["repo"], current["commit_paths"], note, lib["sign"], lib["check"])
        except Exception as exc:
            issues = tx.rollback()
            # The file had no uncommitted changes, so its index entry returns to HEAD.
            e.git(lib["repo"], "reset", "-q", "--", *current["commit_paths"], check=False)
            message = f"Saving the starter set failed and was rolled back: {exc}" + (
                f"; recovery needed: {issues}" if issues else ""
            )
            if isinstance(exc, c.ShareError):
                raise exc.wrap(message) from exc
            raise c.ShareError(message) from exc
        pushed, failure = publish(args["target"], args["source"], lib["path"], push)
    return {
        "commit": sha,
        "pushed": pushed,
        **failure,
        **direct_fields(args["source"], lib["repo"], lib["check"]),
    }


def outdated(target, do_fetch=False):
    """What has a newer or different version than the library, for the user (home)
    and the target folder. Uses the commits last fetched; `do_fetch` fetches first."""
    home = c.global_dir().parent
    target = c.root(target)
    places = [home] + ([target] if target != home else [])
    libs: dict[str, Any] = {}
    statuses: dict[str, Any] = {}
    problems: list[str] = []

    def lib_for(place, source):
        if source not in libs:
            try:
                libs[source] = library(place, source)
            except c.ShareError as exc:
                libs[source] = exc
        return libs[source]

    if do_fetch:
        sources = set()
        for place in places:
            try:
                sources.update(c.effective(place)["sources"])
            except c.ShareError:
                continue
        for source in sorted(sources):
            lib = lib_for(target, source)
            if isinstance(lib, c.ShareError) or lib["repo"] is None:
                continue
            try:
                if status(target, source).get("remote_url"):
                    fetch(target, source)
            except c.ShareError as exc:
                problems.append(f"{source}: {exc}")

    def library_status(source, lib):
        if source not in statuses:
            statuses[source] = status_of(lib["path"])
        return statuses[source]

    rows = []
    for place in places:
        label = "Global" if place == home else str(place)
        try:
            installed = e.state(place)["inbound"]
            cfg = c.effective(place)
        except c.ShareError as exc:
            problems.append(f"{label}: {exc}")
            continue
        seen = set()
        for row in installed.values():
            key = (row["source"], row["kind"], row["name"])
            if key in seen:
                continue
            seen.add(key)
            source, kind, name = key
            item = {"place": label, "source": source, "kind": kind, "name": name}
            lib = lib_for(place, source)
            if isinstance(lib, c.ShareError) or lib["repo"] is None:
                item.update(state="unknown", detail="the library is not a Git checkout here")
                rows.append(item)
                continue
            try:
                original = e.source_path(lib["cfg"], source, kind, name)
            except c.ShareError as exc:
                item.update(state="unknown", detail=str(exc))
                rows.append(item)
                continue
            rel = original.relative_to(lib["repo"]).as_posix()
            commit, _, when = (
                quiet(lib["repo"], "log", "-1", "--format=%h %cI", "--", rel).strip().partition(" ")
            )
            item["have"] = {"commit": commit or None, "at": iso(when) or None}
            newer = []
            if quiet(lib["repo"], "rev-parse", "--verify", "-q", "@{upstream}").strip():
                newer = quiet(
                    lib["repo"], "log", "--format=%h", "HEAD..@{upstream}", "--", rel
                ).split()
            st = library_status(source, lib)
            edited = any(
                change["path"] == rel or change["path"].startswith(rel + "/")
                for change in st.get("changes", [])
            )
            if newer:
                item.update(state="newer", newer=len(newer))
            elif edited:
                item.update(state="edited")
            else:
                item.update(state="current")
            rows.append(item)
        for found in e.scan(place):
            if found["status"] != "local":
                continue
            matches = e.locate(cfg, found["name"], kind=found["kind"])
            if not matches:
                continue
            source = matches[0][0]
            local = place / found["path"]
            item = {
                "place": label,
                "source": source,
                "kind": found["kind"],
                "name": found["name"],
                "path": found["path"],
            }
            try:
                original = e.source_path(cfg, source, found["kind"], matches[0][2])
                if e.same_content(local, original):
                    item.update(state="copy-current")
                    rows.append(item)
                    continue
            except (c.ShareError, OSError):
                pass
            lib = lib_for(place, source)
            older = None
            if not isinstance(lib, c.ShareError) and lib["repo"] is not None:
                older = older_version(
                    lib["repo"], lib["prefix"], found["kind"], matches[0][2], local
                )
            if older:
                item.update(state="older-copy", older=older)
            else:
                item.update(state="changed-copy")
            rows.append(item)
        for version in version_rows(place):
            if version["team_changed"]:
                rows.append(
                    {
                        "place": label,
                        "source": version["source"],
                        "kind": version["kind"],
                        "name": version["name"],
                        "of": version["of"],
                        "state": "kept-behind",
                    }
                )
    connected = set()
    for place in places:
        try:
            connected.update(c.effective(place)["sources"])
        except c.ShareError:
            continue
    known = remembered()
    checked = {
        source: (known.get(source) or {}).get("checked_at")
        for source in sorted(connected)
        if source != "my-skills"
    }
    return {"rows": rows, "checked": checked, "problems": problems}


def status_of(path):
    """Uncommitted changes of the Git repository at `path` (porcelain paths)."""
    repo = e.git_root(path)
    if repo is None:
        return {}
    out = quiet(repo, "status", "--porcelain=v1", "--untracked-files=all")
    return {"changes": [{"path": line[3:]} for line in out.splitlines() if len(line) > 3]}
