"""The personal library (ADR 008): a local Git repository for trying skills before
sharing them, and promoting an asset from it to a team library."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from . import config as c
from . import engine as e
from . import repositories as r

SOURCE = "my-skills"
FOLDERS = ("skills", "agents", "prompts")


def home():
    return c.global_dir().parent


def path():
    """The one fixed folder the personal library lives in: ~/Libraries/my-skills."""
    return c.safe_path(home(), f"Libraries/{SOURCE}")


def is_personal(source, location):
    return source == SOURCE and location is not None and str(location) == str(path())


def identity_problem(folder):
    """Committing needs an explicit Git name and email, not an auto-detected one."""
    for key in ("user.name", "user.email"):
        if not r.quiet(folder, "config", "--get", key).strip():
            return "Git needs your name and email (git config --global user.name/user.email)"
    return None


def ensure():
    """Create and connect the personal library if needed. Idempotent; only the fixed
    folder is created, and an existing non-empty folder is used only when it already
    is a Git repository with a library layout."""
    folder = path()
    connected = c.effective(home())["sources"].get(SOURCE)
    if connected and connected.get("path"):
        if Path(connected["path"]) != folder.resolve():
            raise c.ShareError(
                f"A library named {SOURCE} is already connected at {connected['path']}"
            )
        if folder.is_dir():
            return {"source": SOURCE, "path": str(folder), "created": False}
    with c.locks([folder]):
        if folder.is_symlink():
            raise c.ShareError(f"{folder} is a link; choose a real folder")
        existing = folder.is_dir() and any(folder.iterdir())
        if existing:
            if e.git_root(folder) != folder.resolve() or not c.detect_layout(folder):
                raise c.ShareError(
                    f"{folder} already exists and is not a library; move it away first"
                )
            c.add_source(home(), SOURCE, folder, layout=c.detect_layout(folder), scope="global")
            return {"source": SOURCE, "path": str(folder), "created": False}
        problem = identity_problem(home())
        if problem:
            raise c.ShareError(problem)
        made = not folder.exists()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            r.run(folder, "init", "-q", "-b", "main")
            for name in FOLDERS:
                (folder / name).mkdir(exist_ok=True)
                (folder / name / ".gitkeep").write_text("")
            # Archive and machine records stay out of Git from the first commit.
            c.init(folder)
            r.run(folder, "add", "--all", "--", *FOLDERS, ".captain")
            r.run(folder, "commit", "-q", "-m", "Start my skills")
            c.add_source(home(), SOURCE, folder, layout="flat", scope="global")
        except Exception:
            # Only content this call created is removed; nothing else existed there.
            if made:
                shutil.rmtree(folder, ignore_errors=True)
            else:
                for item in folder.iterdir():
                    if item.is_dir() and not item.is_symlink():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
            raise
    return {"source": SOURCE, "path": str(folder), "created": True}


def relinks(asset_key, places, team=None):
    """Places that select or link the asset, as previews of their relink; `bound` says
    whether the team library is connected there."""
    rows = []
    for place in places:
        target = Path(place["path"])
        try:
            cfg = c.effective(target)
            own = c.read_json(target / ".captain/sharing.json", c.blank()) or c.blank()
            links = sorted(
                rel
                for rel, row in e.state(target)["inbound"].items()
                if row.get("key") == asset_key
            )
        except (c.ShareError, OSError):
            continue
        if asset_key in cfg["inbound"] or asset_key in own.get("inbound", {}) or links:
            rows.append(
                {
                    "op": "relink",
                    "path": str(target),
                    "label": place["label"],
                    "links": links,
                    "bound": bool(cfg["sources"].get(team, {}).get("path")),
                }
            )
    return rows


def promote_plan(target, kind, name, team, places, mode="direct"):
    """Preview moving one asset from the personal library to a team library: copy and
    commit there, switch every place's selection and links, then archive it here.
    In review mode only the copy happens, on a review branch (ADR 009); the personal
    copy and every link stay until the change is merged."""
    target = c.root(target)
    if mode not in ("direct", "review"):
        raise c.ShareError("mode must be direct or review")
    c.asset_name(kind, name)
    c.name(team)
    if team == SOURCE:
        raise c.ShareError("Choose a team library to move the asset to")
    mine = r.library(target, SOURCE)
    lib = r.library(target, team)
    original = e.source_path(mine["cfg"], SOURCE, kind, name)
    if not os.path.lexists(original):
        raise c.ShareError(f"{SOURCE} has no {kind} named {name}")
    dest = e.source_path(lib["cfg"], team, kind, name)
    problems: list[tuple[str, str]] = []
    try:
        e.validate_asset(original, kind, outbound=True)
    except c.ShareError as exc:
        problems.append((str(exc), "invalid"))
    if os.path.lexists(dest):
        problems.append((f"{team} already has {kind}/{name}", "exists"))
    commit_paths, team_head, mine_rel, pending = [], None, None, []
    branch = base = None
    if lib["repo"] is None:
        problems.append((f"Library {team} is not a Git repository", "repository"))
    else:
        commit_paths = [dest.relative_to(lib["repo"]).as_posix()]
        team_head = r.quiet(lib["repo"], "rev-parse", "HEAD").strip() or None
        pending.append(r.quiet(lib["repo"], "status", "--porcelain=v1", "--", *commit_paths))
        if mode == "review":
            branch, base, _, found = r.review_prepare(target, team, lib, r.asset_stem(kind, name))
            problems += [(m, "branch" if m.startswith("Branch ") else "repository") for m in found]
        else:
            problems += [(m, "repository") for m in r.commit_blockers(lib["repo"])]
            if pending[-1].strip():
                problems.append((f"{team} has uncommitted changes at {commit_paths[0]}", "exists"))
    if mine["repo"] is not None:
        mine_rel = original.relative_to(mine["repo"]).as_posix()
        pending.append(r.quiet(mine["repo"], "status", "--porcelain=v1", "--", mine_rel))
        if pending[-1].strip():
            problems.append(
                (f"{name} has uncommitted edits in {SOURCE}; commit them first", "edited")
            )
    key = f"{SOURCE}/{kind}/{name}"
    switched = relinks(key, places, team) if mode == "direct" else []
    for row in switched:
        if not row["bound"]:
            # Archiving the personal copy would otherwise leave this place's links broken.
            problems.append(
                (f"{team} is not connected for {row['label']}; connect it there first", "unbound")
            )
    copy: dict[str, Any] = {"op": "copy", "path": str(dest), "source": str(original)}
    if branch:
        copy["branch"] = branch
    changes: list[dict[str, Any]] = [copy, *switched]
    if mode == "direct":
        changes.append(
            {"op": "archive", "path": str(original), "commit": f"Moved {name} to {team}"}
        )
    places_seen = [
        [
            e.snapshot(Path(p["path"]) / f)
            for f in (".captain/sharing.json", ".captain/sharing-state.json")
        ]
        for p in places
    ]
    result: dict[str, Any] = {
        "version": 1,
        "spec": {"type": "promote", "target": str(target), "action": "promote"},
        "promote": {
            "target": str(target),
            "kind": kind,
            "name": name,
            "team": team,
            "mode": mode,
        },
        "mode": mode,
        "branch": branch,
        "base": base,
        "changes": changes,
        "errors": [m for m, _ in problems],
        "conflicts": [
            {"message": m, "path": None, "key": key, "reason": why} for m, why in problems
        ],
        "can_apply": not problems,
        "commit_paths": commit_paths,
        "places": [{"path": p["path"], "label": p["label"]} for p in places],
        "observations": [e.snapshot(original), team_head, mine_rel, pending, places_seen],
    }
    result["fingerprint"] = e.digest(result)
    return result


def promote_apply(saved, note, push=False):
    """Copy and commit into the team library (pushing when asked), then relink every
    place and archive the personal copy. Later failures never undo the team commit."""
    note = r.note_text(note)
    spec = saved.get("promote") if isinstance(saved, dict) else None
    if not isinstance(spec, dict):
        raise c.ShareError("Preview the move before applying it")
    places = saved.get("places")
    if not isinstance(places, list):
        raise c.ShareError("Preview the move before applying it")
    target, kind, name, team = spec["target"], spec["kind"], spec["name"], spec["team"]
    mode = spec.get("mode", "direct")
    lib = r.library(target, team)
    with c.locks([lib["path"]]):
        current = promote_plan(target, kind, name, team, places, mode)
        if current["fingerprint"] != saved.get("fingerprint"):
            raise c.ShareError("Preview is stale: files or the libraries changed. Preview again.")
        if not current["can_apply"]:
            raise c.ShareError("; ".join(current["errors"]))
        copy = current["changes"][0]
        if mode == "review":
            result = r.review_send(
                target,
                team,
                lib,
                [(current["commit_paths"][0], Path(copy["source"]))],
                note,
                current["branch"],
                current["base"],
                current["observations"][1],
                push,
            )
            # Personal copy and links stay until the review is merged.
            return {**result, "relinked": [], "removed": False, "errors": []}
        r.sign_check(lib)
        tx = e.Transaction()
        try:
            tx.copy(Path(copy["source"]), Path(copy["path"]))
        except Exception as exc:
            issues = tx.rollback()
            raise c.ShareError(
                f"Copying failed and was rolled back: {exc}"
                + (f"; recovery needed: {issues}" if issues else "")
            ) from exc
        try:
            sha = r.commit(lib["repo"], current["commit_paths"], note, lib["sign"])
        except c.ShareError as exc:
            raise exc.wrap(
                f"Copied into {team}, but the commit failed: {exc}. "
                "The copy stays there; commit it in that library."
            ) from exc
        pushed, failure = r.publish(target, team, lib["path"], push)
    errors: list[str] = []
    relinked = []
    for row in current["changes"]:
        if row["op"] != "relink":
            continue
        try:
            planned = e.plan(
                row["path"],
                action="relink",
                kind=kind,
                asset=name,
                source=team,
                from_source=SOURCE,
            )
            if not planned["can_apply"]:
                raise c.ShareError("; ".join(planned["errors"]))
            e.apply(planned)
            relinked.append(row["path"])
        except (c.ShareError, OSError) as exc:
            errors.append(f"{row['label']}: {exc}")
    removed = False
    if errors:
        # Places that still link the personal copy keep it; nothing is archived.
        errors.append(f"{SOURCE}: kept {name} because some places still use it")
    else:
        try:
            removed = archive_personal(target, kind, name, team)
        except (c.ShareError, OSError) as exc:
            errors.append(f"{SOURCE}: {exc}")
    return {
        "commit": sha,
        "pushed": pushed,
        **failure,
        **r.direct_fields(),
        "relinked": relinked,
        "removed": removed,
        "errors": errors,
    }


def archive_personal(target, kind, name, team):
    """Archive the moved asset in the personal library and record that locally."""
    mine = r.library(target, SOURCE)
    original = e.source_path(mine["cfg"], SOURCE, kind, name)
    rel = original.relative_to(mine["repo"]).as_posix()
    with c.locks([mine["path"], mine["path"] / ".captain"]):
        if r.quiet(mine["repo"], "status", "--porcelain=v1", "--", rel).strip():
            raise c.ShareError(f"{name} changed in {SOURCE}; it was kept there")
        tx = e.Transaction()
        try:
            e.archive_into(tx, original, mine["path"])
            r.commit(mine["repo"], [rel], f"Moved {name} to {team}", mine["sign"])
        except Exception as exc:
            issues = tx.rollback()
            e.git(mine["repo"], "reset", "-q", "--", rel, check=False)
            raise c.ShareError(
                f"Archiving failed and was rolled back: {exc}"
                + (f"; recovery needed: {issues}" if issues else "")
            ) from exc
    return True
