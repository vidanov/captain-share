"""Explicit Git operations for library checkouts.

Captain Share commits only when a user shares one asset (ADR 005): it stages and
commits exactly that asset's paths with the user's note, never forces a push, and
keeps the commit when a push is refused."""

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


def run_with(path, args, env_extra=None, data=None, text=True):
    """Git without inherited GIT_* variables (only `env_extra`), optionally with stdin."""
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
        raise c.ShareError(err.strip() or out.strip() or "Git failed")
    return result.stdout


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


def push_checked(path, before, branch=None):
    """Push the current branch to its own upstream branch, or a new review branch to
    the same remote: never forced, one push URL."""
    if not before.get("head") or not before.get("remote_url"):
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
    return run(path, "push", "--no-force", "--no-mirror", "--", remote, f"{source}:{ref}")


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
            who = {"by": author, "at": iso(when)}
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


def share_plan(target, source, kind, name, path, adopt=False):
    """Preview copying one project asset into a library and committing only it.
    With `adopt`, also predict swapping the local copies for library links."""
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
    if adopt:
        # A prediction only: the adopt is planned afresh after the commit.
        result["adopt_changes"] = e.adopt_preview(target, kind, name, path)
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
    adopt_error = adopt_after_share(**args) if adopt else None
    return {
        "commit": sha,
        "pushed": pushed,
        "push_error": error,
        "get_command": get_command(args["target"], args["source"], args["kind"], args["name"]),
        "adopted": bool(adopt) and adopt_error is None,
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


def review_branch(repo, kind, name):
    """share/NAME-USER, with USER from Git's user.name or the operating-system user."""
    user = quiet(repo, "config", "user.name").strip()
    if not user:
        try:
            user = getpass.getuser()
        except (KeyError, OSError):
            user = ""
    stem = name[: -len(c.SUFFIXES[kind])] if kind in c.SUFFIXES else name
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
            if e.git(repo, "var", "GIT_COMMITTER_IDENT", check=False).returncode:
                problems.append(
                    (
                        "Git needs your name and email (git config user.name/user.email)",
                        "repository",
                    )
                )
            if not head:
                problems.append(("The library has no commits yet", "repository"))
            branch = review_branch(repo, kind, name)
            if (
                e.git(
                    repo, "rev-parse", "-q", "--verify", f"refs/heads/{branch}", check=False
                ).returncode
                == 0
            ):
                problems.append((f"Branch {branch} already exists in the library", "branch"))
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


def review_commit(repo, rel, local, note, branch, head):
    """Commit local as `rel` on a new branch from head, via a temporary index: the
    working tree, the real index and the current branch are not touched."""
    files = [(rel, local)] if local.is_file() else []
    if local.is_dir():
        for p in sorted(local.rglob("*")):
            if e.ignored(p, local) or not p.is_file() or p.is_symlink():
                continue
            files.append((f"{rel}/{p.relative_to(local).as_posix()}", p))
    if any(ch in str(p) for _, p in files for ch in "\n\r"):
        raise c.ShareError("File names with line breaks cannot be proposed")
    with tempfile.TemporaryDirectory(prefix="captain-share-index-") as temporary:
        env = {"GIT_INDEX_FILE": str(Path(temporary) / "index")}
        run_with(repo, ["read-tree", head], env)
        run_with(repo, ["rm", "-r", "--cached", "--quiet", "--ignore-unmatch", "--", rel], env)
        shas = run_with(
            repo,
            ["hash-object", "-w", "--no-filters", "--stdin-paths"],
            env,
            data="".join(f"{p}\n" for _, p in files),
        ).split()
        info = "".join(
            f"{'100755' if os.access(p, os.X_OK) else '100644'} {sha}\t{path}\0"
            for (path, p), sha in zip(files, shas)
        )
        run_with(repo, ["update-index", "-z", "--add", "--index-info"], env, data=info)
        tree = run_with(repo, ["write-tree"], env).strip()
    commit_id = run_with(repo, ["commit-tree", tree, "-p", head, "-m", note]).strip()
    # The empty old value refuses to replace an existing branch.
    run_with(repo, ["update-ref", f"refs/heads/{branch}", commit_id, ""])
    return commit_id


def propose_apply(saved, note, push=False):
    """Apply a fresh propose preview: update the library original directly (commit,
    push, then adopt) or on a review branch that is pushed for review."""
    note = note_text(note)
    spec = saved.get("propose") if isinstance(saved, dict) else None
    if not isinstance(spec, dict):
        raise c.ShareError("Preview the proposal before applying it")
    args = {k: spec[k] for k in ("target", "source", "kind", "name", "path", "mode")}
    lib = library(args["target"], args["source"])
    adopted, adopt_error, url = False, None, None
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
            with c.locks([lib["path"] / ".captain"]):
                e.replace_original(local, original, lib["path"])
            try:
                sha = commit(repo, current["commit_paths"], note)
            except c.ShareError as exc:
                raise c.ShareError(
                    f"Updated the library, but the commit failed: {exc}. The previous "
                    "version is archived in the library; commit there or restore it."
                ) from exc
            pushed, error = publish(args["target"], args["source"], lib["path"], push)
        else:
            sha = review_commit(
                repo,
                current["commit_paths"][0],
                local,
                note,
                current["branch"],
                current["observations"][2],
            )
            pushed, error = False, None
            if push:
                try:
                    push_checked(
                        lib["path"], status(args["target"], args["source"]), current["branch"]
                    )
                    pushed = True
                except c.ShareError as exc:
                    error = str(exc)
            if pushed:
                before = status(args["target"], args["source"])
                url = review_url(before["remote_url"], current["base"], current["branch"])
    if args["mode"] == "direct":
        adopt_error = adopt_after_share(
            args["target"], args["source"], args["kind"], args["name"], args["path"]
        )
        adopted = adopt_error is None
    return {
        "commit": sha,
        "pushed": pushed,
        "push_error": error,
        "adopted": adopted,
        "adopt_error": adopt_error,
        "branch": current["branch"],
        "review_url": url,
    }


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


def starter_plan(target, source, assets, clear=False):
    """Preview writing (or with `clear`, removing) a library's starter set."""
    target = c.root(target)
    c.name(source)
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
    if clear and not exists:
        problems.append(("This library has no starter set to clear", "same"))
    elif wanted == current:
        problems.append(("The starter set already has exactly these assets", "same"))
    if lib["repo"] is None:
        problems.append((f"Library {source} is not a Git repository", "repository"))
    else:
        commit_paths = [file.relative_to(lib["repo"]).as_posix()]
        problems += [(m, "repository") for m in commit_blockers(lib["repo"])]
        head = quiet(lib["repo"], "rev-parse", "HEAD").strip() or None
        pending = quiet(lib["repo"], "status", "--porcelain=v1", "--", *commit_paths)
        if pending.strip():
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
        "starter": {"target": str(target), "source": source, "assets": assets, "clear": clear},
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
    lib = library(args["target"], args["source"])
    with c.locks([lib["path"], lib["path"] / ".captain"]):
        current = starter_plan(**args)
        if current["fingerprint"] != saved.get("fingerprint"):
            raise c.ShareError("Preview is stale: the starter set changed. Preview again.")
        if not current["can_apply"]:
            raise c.ShareError("; ".join(current["errors"]))
        file = c.safe_path(lib["path"], STARTER)
        tx = e.Transaction()
        try:
            if args["clear"]:
                # The committed version stays in history; the file itself is archived.
                e.archive_into(tx, file, lib["path"])
            else:
                tx.write(file, current["content"])
            sha = commit(lib["repo"], current["commit_paths"], note)
        except Exception as exc:
            issues = tx.rollback()
            # The file had no uncommitted changes, so its index entry returns to HEAD.
            e.git(lib["repo"], "reset", "-q", "--", *current["commit_paths"], check=False)
            raise c.ShareError(
                f"Saving the starter set failed and was rolled back: {exc}"
                + (f"; recovery needed: {issues}" if issues else "")
            ) from exc
        pushed, error = publish(args["target"], args["source"], lib["path"], push)
    return {"commit": sha, "pushed": pushed, "push_error": error}
