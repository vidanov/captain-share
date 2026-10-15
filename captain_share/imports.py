"""Add assets from outside: a zip file or a folder (ADR 006).

A folder is read in place. A zip is extracted into a private temporary folder with
strict entry checks; nothing in it is executed or opened. Candidates found there are
copied into a library through a one-off plan and committed like a share (ADR 005):
only their paths, with the user's note, and pushed only when asked."""

from __future__ import annotations

import io
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from . import config as c
from . import engine as e
from . import repositories as r

MAX_UPLOAD = 20 * 1024 * 1024
MAX_ENTRIES = 2000
MAX_UNPACKED = 100 * 1024 * 1024
MAX_STAGES = 20
MAX_CANDIDATES = 500
MAX_ITEMS = 200
# Archive and folder clutter that is never an asset.
IGNORED = {"__MACOSX", ".DS_Store", *e.HOUSEKEEPING}
# Repository paperwork that is not a prompt even when it is the only Markdown there.
PAPERWORK = {"readme.md", "license.md", "changelog.md", "contributing.md", "code_of_conduct.md"}
MARKDOWN_KINDS = ["prompts", "steering"]
DRIVE = re.compile(r"[A-Za-z]:")
CHUNK = 1024 * 1024


def entry_parts(name):
    """Path segments of one archive entry; unsafe names are refused, not cleaned."""
    # Some Windows tools write backslashes; read them as separators, then check.
    clean = name.replace("\\", "/")
    parts = [part for part in clean.split("/") if part not in ("", ".")]
    if (
        clean.startswith("/")
        or DRIVE.match(clean)
        or ".." in parts
        or any(ord(ch) < 32 for ch in clean)
    ):
        raise c.ShareError(f"Unsafe entry in the zip file: {name!r}")
    return parts


def entry_type(info):
    """'dir' or 'file'; symlinks, devices, encrypted and other entries are refused."""
    if info.flag_bits & 0x1:
        raise c.ShareError(f"Encrypted entries are not supported: {info.filename!r}")
    kind = stat.S_IFMT(info.external_attr >> 16)
    if kind == stat.S_IFLNK:
        raise c.ShareError(f"The zip file contains a symlink: {info.filename!r}")
    if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise c.ShareError(f"The zip file contains a special entry: {info.filename!r}")
    if info.is_dir() or kind == stat.S_IFDIR:
        return "dir"
    return "file"


def folder(path):
    """Create one plain folder inside the extraction; anything else in the way fails."""
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise c.ShareError(f"The zip file has conflicting entries: {path.name}")
    if not path.exists():
        path.mkdir(mode=0o755)


def extract(archive, dest):
    """Extract a zip (path or bytes) into the empty folder `dest`: only folders and
    non-executable files, within the entry and size limits."""
    source = io.BytesIO(archive) if isinstance(archive, bytes) else archive
    try:
        with zipfile.ZipFile(source) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ENTRIES:
                raise c.ShareError(f"The zip file has more than {MAX_ENTRIES} entries")
            entries, declared = [], 0
            for info in infos:
                parts = entry_parts(info.filename)
                if not parts or any(part in IGNORED for part in parts):
                    continue
                kind = entry_type(info)
                declared += info.file_size
                if declared > MAX_UNPACKED:
                    raise c.ShareError("The zip file unpacks to more than 100 MB")
                entries.append((parts, kind, info))
            written = 0
            for parts, kind, info in entries:
                parent = dest
                for part in parts[:-1]:
                    parent = parent / part
                    folder(parent)
                path = parent / parts[-1]
                if kind == "dir":
                    folder(path)
                    continue
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                try:
                    handle = os.open(path, flags, 0o644)
                except FileExistsError as exc:
                    raise c.ShareError(
                        f"The zip file has conflicting entries: {info.filename!r}"
                    ) from exc
                with os.fdopen(handle, "wb") as out, zf.open(info) as data:
                    while True:
                        chunk = data.read(CHUNK)
                        if not chunk:
                            break
                        # Headers can understate sizes; count what is actually written.
                        written += len(chunk)
                        if written > MAX_UNPACKED:
                            raise c.ShareError("The zip file unpacks to more than 100 MB")
                        out.write(chunk)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError) as exc:
        # RuntimeError: zipfile's report of an unsupported or encrypted member.
        raise c.ShareError(f"Cannot read this zip file: {exc}") from exc
    except EOFError as exc:
        raise c.ShareError("Cannot read this zip file: it is truncated") from exc
    except OSError as exc:
        # The message names the entry, never the private extraction folder.
        raise c.ShareError(
            f"Cannot extract this zip file: {exc.strerror or type(exc).__name__}"
        ) from exc


def mask(message, root, label):
    """A message with the staged folder shown as its label (the zip file name, or the
    folder itself) followed by the relative path, never the private temporary path."""
    text, root = str(message), Path(root)
    name = label or root.name
    for prefix in sorted({str(root), str(root.resolve())}, key=len, reverse=True):
        text = text.replace(prefix, name)
    return text


def label_name(label):
    """A safe asset name from a zip file name, used when the zip is one asset."""
    base = str(label or "").replace("\\", "/").rsplit("/", 1)[-1]
    if base.lower().endswith(".zip"):
        base = base[:-4]
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", base).strip("-.")[:100] or "imported"


def display_label(label):
    text = "".join(ch for ch in str(label or "") if ord(ch) >= 32)
    return text.replace("\\", "/").rsplit("/", 1)[-1][:255] or "upload.zip"


def unzip(archive, parent, label):
    """Extract into a new folder under `parent`, named after the zip; removed on failure."""
    holder = Path(tempfile.mkdtemp(prefix="stage-", dir=parent))
    root = holder / label_name(label)
    try:
        root.mkdir(mode=0o755)
        extract(archive, root)
    except BaseException:
        shutil.rmtree(holder, ignore_errors=True)
        raise
    return holder, root


def unzip_file(path, parent):
    path = Path(path)
    if path.stat().st_size > MAX_UPLOAD:
        raise c.ShareError("The zip file must be at most 20 MB")
    with path.open("rb") as handle:
        return unzip(handle, parent, path.name)


def folder_kind(path):
    """The kind of a folder that is itself an asset, else None."""
    if path.is_symlink() or not path.is_dir():
        return None
    if (path / "SKILL.md").is_file():
        return "skills"
    if (path / "POWER.md").is_file() or (path / "plugin.json").is_file():
        return "powers"
    return None


def visible(path):
    try:
        items = sorted(path.iterdir())
    except OSError:
        return []
    return [item for item in items if not item.name.startswith(".") and item.name not in IGNORED]


def entry(root, rel, kind, name, kinds, label=""):
    path = root if rel == "." else c.safe_path(root, rel)
    error = None
    try:
        c.asset_name(kind, name)
        e.validate_asset(path, kind, outbound=True)
    except (c.ShareError, OSError) as exc:
        error = mask(exc, root, label)
    return {
        "id": rel,
        "kind": kind,
        "name": name,
        "description": e.describe(path, kind),
        "error": error,
        "kinds": kinds,
    }


def level(root, base, prefix, label=""):
    """Candidates in `base`: library layouts, asset folders, else loose Markdown."""
    rows = []
    for layout in ("ip/", ""):
        if layout and (base / "ip").is_symlink():
            continue
        for kind in c.KINDS:
            kind_folder = base / (layout + kind)
            if kind_folder.is_symlink() or not kind_folder.is_dir():
                continue
            for item in visible(kind_folder):
                if e.candidate(item, kind):
                    rel = f"{prefix}{layout}{kind}/{item.name}"
                    rows.append(entry(root, rel, kind, item.name, [kind], label))
    for item in visible(base):
        if item.name == "ip" or item.name in c.KINDS:
            continue
        kind = folder_kind(item)
        if kind:
            rows.append(entry(root, prefix + item.name, kind, item.name, [kind], label))
    if not rows:
        for item in visible(base):
            if (
                item.suffix.lower() == ".md"
                and item.is_file()
                and not item.is_symlink()
                and item.name.lower() not in PAPERWORK
            ):
                rows.append(
                    entry(root, prefix + item.name, "prompts", item.name, MARKDOWN_KINDS, label)
                )
    return rows


def find(root, label=""):
    """Assets in a staged folder: the folder itself, the assets in it, its library
    layouts (ip/<kind>/, <kind>/), or its Markdown files. A single wrapping folder,
    as in a repository download, is looked into once. Errors name paths by `label`."""
    root = Path(root)
    kind = folder_kind(root)
    if kind:
        return [entry(root, ".", kind, root.name, [kind], label)]
    rows = level(root, root, "", label)
    children = visible(root)
    if not rows and len(children) == 1 and children[0].is_dir() and not children[0].is_symlink():
        rows = level(root, children[0], children[0].name + "/", label)
    return rows[:MAX_CANDIDATES]


# Folders a repository search never walks into.
SKIPPED = {"node_modules", ".git", "__pycache__", "dist", "build", "vendor"}
MAX_DEPTH = 5


def deep_skills(root, label="", depth=MAX_DEPTH):
    """Skill folders at any depth of a repository (skills/<category>/<name>/SKILL.md
    and the like), not looking inside a skill folder."""
    root = Path(root)
    rows: list[dict[str, Any]] = []

    def walk(folder, level):
        if level > depth or len(rows) >= MAX_CANDIDATES:
            return
        for item in visible(folder):
            if item.is_symlink() or not item.is_dir() or item.name in SKIPPED:
                continue
            if folder_kind(item) == "skills":
                rel = item.relative_to(root).as_posix()
                rows.append(entry(root, rel, "skills", item.name, ["skills"], label))
            else:
                walk(item, level + 1)

    walk(root, 1)
    return rows


def find_repository(root, label=""):
    """Assets in a downloaded repository: its library layouts and skill folders at
    any depth. Loose Markdown is not offered: in a repository it is documentation."""
    deep = deep_skills(root, label)
    inner = {row["id"] for row in deep}
    # A category folder holding skills (skills/engineering/) is not a broken skill.
    rows = [
        row
        for row in find(root, label)
        if row["kind"] not in MARKDOWN_KINDS
        and not (row["error"] and any(x.startswith(row["id"] + "/") for x in inner))
        # A plain folder under skills/ (no SKILL.md) is not a skill at all.
        and not (
            row["error"]
            and row["kind"] == "skills"
            and not (Path(root) / row["id"] / "SKILL.md").is_file()
        )
    ]
    seen = {row["id"] for row in rows}
    rows += [row for row in deep if row["id"] not in seen]
    return sorted(rows, key=lambda row: row["name"])[:MAX_CANDIDATES]


GITHUB_SHORT = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
REMOTE = re.compile(r"(https://[^\s@/]+(/[^\s@]+)+|git@[A-Za-z0-9_.-]+:[^\s]+)\Z")


def repository_url(value):
    """An HTTPS or SSH Git address, or OWNER/REPO for GitHub. Local paths, other
    protocols and addresses with credentials are refused."""
    if not isinstance(value, str) or not value.strip():
        raise c.ShareError("Give a Git address, for example https://github.com/OWNER/REPO")
    value = value.strip()
    if GITHUB_SHORT.fullmatch(value) and not value.startswith("."):
        return f"https://github.com/{value}"
    if not REMOTE.fullmatch(value) or value.startswith("-"):
        raise c.ShareError(
            "Use an https:// or git@ address (or OWNER/REPO for GitHub), without a password"
        )
    return value


def clone(url, dest, ref=None):
    """Shallow-clone a public or accessible repository into `dest`; (root, commit,
    label). Only HTTPS and SSH are allowed; Git never prompts."""
    url = repository_url(url)
    if ref is not None and (
        not isinstance(ref, str)
        or not re.fullmatch(r"[A-Za-z0-9_./-]+", ref)
        or ref.startswith("-")
    ):
        raise c.ShareError("Unusable branch or tag name")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="https:ssh")
    args = ["git", "clone", "--quiet", "--depth", "1"]
    if ref:
        args += ["--branch", ref]
    proc = subprocess.run(
        [*args, "--", url, str(dest)], capture_output=True, text=True, env=env, timeout=300
    )
    if proc.returncode:
        raise r.friendly(proc.stderr, dest)
    commit = e.git(dest, "rev-parse", "HEAD").stdout.decode().strip()
    short = url.removeprefix("https://").removesuffix(".git")
    return Path(dest), commit, f"{short}@{commit[:7]}"


class Stages:
    """Zips and folders staged by one server session. Uploaded zips live in one
    private temporary folder, removed by close(); the oldest stage is dropped
    beyond the limit."""

    def __init__(self, limit=None):
        self.limit = limit or MAX_STAGES
        self.rows: dict[str, dict[str, Any]] = {}
        self.folder: Path | None = None

    def temp(self):
        if self.folder is None:
            self.folder = Path(tempfile.mkdtemp(prefix="captain-share-import-")).resolve()
        return self.folder

    def add(self, root, label, holder=None, repository=False):
        stage = secrets.token_urlsafe(18)
        self.rows[stage] = {
            "root": str(Path(root).resolve()),
            "label": label,
            "holder": holder,
            "repository": repository,
        }
        while len(self.rows) > self.limit:
            self.drop(next(iter(self.rows)))
        return stage

    def drop(self, stage):
        row = self.rows.pop(stage, None)
        if row and row["holder"]:
            shutil.rmtree(row["holder"], ignore_errors=True)

    def get(self, stage):
        if not isinstance(stage, str) or stage not in self.rows:
            raise c.ShareError("This zip or folder is no longer staged; add it again")
        return self.rows[stage]

    def view(self, stage):
        row = self.get(stage)
        finder = find_repository if row.get("repository") else find
        return {
            "stage": stage,
            "label": row["label"],
            "repository": bool(row.get("repository")),
            "candidates": finder(row["root"], row["label"]),
        }

    def upload(self, data, label):
        if not isinstance(data, bytes) or not data or len(data) > MAX_UPLOAD:
            raise c.ShareError("The zip file must be at most 20 MB")
        label = display_label(label)
        holder, root = unzip(data, self.temp(), label)
        return self.view(self.add(root, label, holder))

    def open_folder(self, value):
        if not isinstance(value, str) or not value:
            raise c.ShareError("Choose a folder")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise c.ShareError("Choose an absolute folder path")
        if path.exists() and not path.is_dir():
            raise c.ShareError("Choose a folder, not a file (a zip file is uploaded instead)")
        root = c.root(path)
        too_broad(root)
        return self.view(self.add(root, str(root)))

    def clone(self, url, ref=None):
        """Stage a Git repository (a shallow clone in the private temporary folder)."""
        holder = Path(tempfile.mkdtemp(prefix="clone-", dir=self.temp()))
        try:
            root, _, label = clone(url, holder / "repository", ref)
        except BaseException:
            shutil.rmtree(holder, ignore_errors=True)
            raise
        return self.view(self.add(root, label, holder, repository=True))

    def close(self):
        self.rows.clear()
        if self.folder is not None:
            shutil.rmtree(self.folder, ignore_errors=True)
            self.folder = None


def too_broad(root):
    """A home folder (or anything above it) holds notes and documents, not one
    skill; scanning it offers unrelated Markdown files as prompts."""
    home = Path.home().resolve()
    configured = c.global_dir().parent.resolve()
    for base in {home, configured}:
        if root == base or root in base.parents:
            raise c.ShareError(
                "Choose the skill's own folder (or a folder of skills), not your home folder"
            )


def plan(target, source, root, items, stage="", label="", mode="direct", repository=False):
    """Preview copying chosen candidates of a staged folder into a library and
    committing only their paths, directly or on a review branch (ADR 009). Existing
    library assets are never overwritten. A `repository` (a Git download) is searched
    like find_repository, skill folders at any depth included."""
    try:
        return planned(target, source, root, items, stage, label, mode, repository)
    except c.ShareError as exc:
        raise exc.wrap(mask(exc, root, label)) from exc
    except OSError as exc:
        raise c.ShareError(mask(exc, root, label)) from exc


def planned(target, source, root, items, stage, label, mode="direct", repository=False):
    target = c.root(target)
    c.name(source)
    if mode not in ("direct", "review"):
        raise c.ShareError("mode must be direct or review")
    root = c.root(root)
    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
        raise c.ShareError(f"Choose between 1 and {MAX_ITEMS} assets to add")
    finder = find_repository if repository else find
    found = {row["id"]: row for row in finder(root, label)}
    lib = r.library(target, source)
    changes: list[dict[str, Any]] = []
    errors: list[str] = []
    conflicts: list[dict[str, Any]] = []
    observations, commit_paths, chosen, pending = [], [], [], []
    destinations: set[Path] = set()

    def conflict(message, path=None, key=None, reason="error"):
        message = mask(message, root, label)
        errors.append(message)
        conflicts.append({"message": message, "path": path, "key": key, "reason": reason})

    for item in items:
        if not isinstance(item, dict):
            raise c.ShareError("Each asset needs an id, kind and name")
        rel, kind, name = item.get("id"), item.get("kind"), item.get("name")
        candidate = found.get(rel) if isinstance(rel, str) else None
        if candidate is None:
            raise c.ShareError(f"Not an asset found in this zip or folder: {rel!r}")
        if kind not in candidate["kinds"]:
            raise c.ShareError(f"{rel} cannot be added as {kind!r}")
        if not isinstance(name, str):
            raise c.ShareError(f"Choose a name for {rel}")
        chosen.append({"id": rel, "kind": kind, "name": name})
        key = f"{kind}/{name}"
        src = root if rel == "." else c.safe_path(root, rel)
        try:
            c.asset_name(kind, name)
            e.validate_asset(src, kind, outbound=True)
            if kind == "powers" and name != src.name:
                raise c.ShareError(f"A power keeps the name in its manifest: {src.name}")
            dest = e.source_path(lib["cfg"], source, kind, name)
            if dest in destinations:
                raise c.ShareError(f"Two assets would be added as {key}")
            destinations.add(dest)
            if src == dest or src in dest.parents or dest in src.parents:
                raise c.ShareError(f"{rel} is already inside library {source}")
            observations.append({"path": str(src), "snapshot": e.snapshot(src)})
            if os.path.lexists(dest):
                conflict(
                    f"{source} already has {key}; rename it to add it as a new asset",
                    rel,
                    key,
                    "exists",
                )
                continue
            if lib["repo"] is not None:
                path = dest.relative_to(lib["repo"]).as_posix()
                state = r.quiet(lib["repo"], "status", "--porcelain=v1", "--", path)
                if state.strip():
                    # A deleted but tracked asset: adding would overwrite its history.
                    conflict(f"{source} has uncommitted changes at {path}", rel, key, "exists")
                    continue
                commit_paths.append(path)
                pending.append(state)
            changes.append({"op": "copy", "path": str(dest), "source": str(src)})
        except (c.ShareError, OSError) as exc:
            conflict(str(exc), rel, key)
    head = branch = base = None
    problems = []
    if lib["repo"] is None:
        problems.append(f"Library {source} is not a Git repository, so it cannot record an import")
    elif mode == "review":
        stem = chosen[0]["name"] if chosen else "import"
        stem = r.asset_stem(chosen[0]["kind"], stem) if chosen else stem
        branch, base, head, found = r.review_prepare(target, source, lib, stem)
        problems += found
    else:
        problems += r.commit_blockers(lib["repo"])
        head = r.quiet(lib["repo"], "rev-parse", "HEAD").strip() or None
    for message in problems:
        conflict(message, reason="repository")
    spec = {
        "target": str(target),
        "source": source,
        "root": str(root),
        "items": chosen,
        "stage": stage,
        "label": label,
        "mode": mode,
        "download": bool(repository),
    }
    result: dict[str, Any] = {
        "version": 1,
        "changes": changes,
        "errors": errors,
        "conflicts": conflicts,
        "can_apply": not errors,
        "commit_paths": commit_paths,
        "mode": mode,
        "branch": branch,
        "base": base,
        "import": {
            **spec,
            "repository": str(lib["repo"]) if lib["repo"] else None,
            "head": head,
        },
    }
    result["fingerprint"] = e.digest([result, observations, pending])
    return result


def apply(saved, note, push=False):
    """Apply a fresh import preview, commit only its paths, and push when asked."""
    note = r.note_text(note)
    spec = saved.get("import") if isinstance(saved, dict) else None
    if not isinstance(spec, dict):
        raise c.ShareError("Preview the import before applying it")
    args = {k: spec[k] for k in ("target", "source", "root", "items", "stage", "label")}
    args["mode"] = spec.get("mode", "direct")
    args["repository"] = bool(spec.get("download"))
    path = r.checkout(args["target"], args["source"])
    get_commands = [
        r.get_command(args["target"], args["source"], row["kind"], row["name"])
        for row in args["items"]
    ]
    with c.locks([path]):
        current = plan(**args)
        if current["fingerprint"] != saved.get("fingerprint"):
            raise c.ShareError(
                "Import preview is stale: files or the library changed. Preview again."
            )
        if not current["can_apply"]:
            raise c.ShareError("; ".join(current["errors"]))
        lib = r.library(args["target"], args["source"])
        if args["mode"] == "review":
            entries = [
                (rel, Path(change["source"]))
                for rel, change in zip(current["commit_paths"], current["changes"])
            ]
            result = r.review_send(
                args["target"],
                args["source"],
                lib,
                entries,
                note,
                current["branch"],
                current["base"],
                current["import"]["head"],
                push,
            )
            return {**result, "get_commands": get_commands}
        r.sign_check(lib)
        tx = e.Transaction()
        try:
            for change in current["changes"]:
                tx.copy(Path(change["source"]), Path(change["path"]))
        except Exception as exc:
            issues = tx.rollback()
            raise c.ShareError(
                mask(
                    f"Adding failed and was rolled back: {exc}"
                    + (f"; recovery needed: {issues}" if issues else ""),
                    args["root"],
                    args["label"],
                )
            ) from exc
        repo = Path(current["import"]["repository"])
        try:
            sha = r.commit(repo, current["commit_paths"], note, lib["sign"], lib["check"])
        except c.ShareError as exc:
            raise exc.wrap(
                f"Copied into the library, but the commit failed: {exc}. "
                "The copies stay in the library; commit them there."
            ) from exc
        pushed, failure = r.publish(args["target"], args["source"], path, push)
    return {
        "commit": sha,
        "pushed": pushed,
        **failure,
        **r.direct_fields(args["source"], repo, lib["check"]),
        "get_commands": get_commands,
    }
