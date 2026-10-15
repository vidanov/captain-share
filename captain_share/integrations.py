"""Optional Captain import and Kiro CLI handoff; neither is a runtime dependency."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import config as c
from . import engine


def registry_path():
    return c.config_dir(scope="global") / "repositories.local.json"


def repositories():
    data = c.read_json(registry_path(), {"version": 1, "repositories": {}})
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(data.get("repositories"), dict)
    ):
        raise c.ShareError("Invalid repository registry")
    return data


def register(label, path):
    c.name(label)
    target = c.root(path)
    p = registry_path()
    with c.locks([p.parent]):
        data = repositories()
        data["repositories"][label] = str(target)
        c.atomic_json(p, data)
    return {"name": label, "path": str(target)}


def import_captain(directory, target, apply=False):
    directory = c.root(directory)
    source_data = c.read_json(directory / "sources.json", {})
    repo_data = c.read_json(directory / "repositories.json", {})
    if not isinstance(source_data, dict) or not isinstance(repo_data, dict):
        raise c.ShareError("Captain records must be JSON objects")
    sources, repos = source_data.get("sources", []), repo_data.get("repositories", [])
    if (
        not isinstance(sources, list)
        or not isinstance(repos, list)
        or not all(isinstance(r, dict) for r in sources + repos)
    ):
        raise c.ShareError("Invalid Captain source/repository records")
    proposal: dict[str, Any] = {"sources": [], "repositories": [], "warnings": []}
    for item in sources:
        path, sid = item.get("canonical_root"), item.get("id")
        if not path or not sid:
            continue
        try:
            c.name(sid)
            location = c.root(path)
            if not (location / "ip").is_dir():
                raise c.ShareError("Source has no ip directory")
            proposal["sources"].append(
                {"name": sid, "path": str(location), "label": item.get("display_name", sid)}
            )
        except c.ShareError as exc:
            proposal["warnings"].append(str(exc))
    for item in repos:
        path, rid = item.get("canonical_root"), item.get("id")
        if path and rid:
            try:
                proposal["repositories"].append({"name": c.name(rid), "path": str(c.root(path))})
            except c.ShareError as exc:
                proposal["warnings"].append(str(exc))
    if apply:
        # Connections only: imported Captain executables and saved plans are never run.
        for row in proposal["sources"]:
            c.add_source(target, row["name"], row["path"])
        for row in proposal["repositories"]:
            register(row["name"], row["path"])
    proposal["applied"] = apply
    return proposal


def import_profile(target, source, profile, apply=False):
    c.name(profile)
    cfg = c.effective(target)
    spec = cfg["sources"].get(source, {})
    if not spec.get("path"):
        raise c.ShareError("Source is not bound locally")
    file = c.safe_path(c.root(spec["path"]), f"profiles/{profile}.manifest")
    if not file.is_file():
        raise c.ShareError(f"Profile is missing: {profile}")
    rows = []
    for line in file.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        kind, sep, names = line.partition(":")
        if not sep or kind not in c.KINDS:
            raise c.ShareError(f"Invalid profile line: {line}")
        # Legacy toolkit manifests separate names with commas; accept both forms.
        for asset in names.replace(",", " ").split():
            c.asset_name(kind, asset)
            original = engine.source_path(cfg, source, kind, asset)
            engine.validate_asset(original, kind)
            rows.append({"source": source, "kind": kind, "name": asset})
    if apply:
        for row in rows:
            c.select(target, "inbound", row["source"], row["kind"], row["name"])
    return {"selections": rows, "applied": apply}


def kiro_powers_dir():
    # Owned by Kiro (ADR 003): read to compare, written only through kiro-cli.
    return Path.home() / ".kiro/powers/installed"


def power_state(original):
    installed = kiro_powers_dir() / original.name
    if not os.path.lexists(installed):
        return "not-installed"
    if installed.is_symlink():
        return "differs"
    same = engine.snapshot(installed, modes=False) == engine.snapshot(original, modes=False)
    return "current" if same else "differs"


def powers(target, source):
    rows = [row for row in engine.catalog(target, source) if row["kind"] == "powers"]
    for row in rows:
        if row["status"] == "available":
            row["kiro"] = power_state(Path(row["path"]))
    return rows


def power_runs(original):
    """What a power would start on this computer: MCP servers (from mcp.json or
    plugin.json) and hooks. Each row has a type, a name and what runs."""
    runs = []

    def servers(mcp, where):
        if not isinstance(mcp, dict):
            return
        for server, spec in sorted(mcp.items()):
            spec = spec if isinstance(spec, dict) else {}
            if spec.get("url"):
                what = str(spec["url"])
            else:
                args = spec.get("args") if isinstance(spec.get("args"), list) else []
                what = " ".join(str(x) for x in [spec.get("command", "?"), *args])
            runs.append({"type": "MCP server", "name": str(server), "runs": what, "in": where})

    def read(rel):
        try:
            return json.loads((original / rel).read_text())
        except (OSError, ValueError):
            return None

    for rel in ("mcp.json", ".mcp.json"):
        data = read(rel)
        if isinstance(data, dict):
            servers(data.get("mcpServers"), rel)
    manifest = read("plugin.json")
    if isinstance(manifest, dict):
        servers(manifest.get("mcpServers"), "plugin.json")
        if manifest.get("hooks"):
            runs.append(
                {"type": "hook", "name": "hooks", "runs": "see plugin.json", "in": "plugin.json"}
            )
    for p in sorted(original.rglob("*")):
        rel = p.relative_to(original).as_posix()
        if not p.is_file() or engine.ignored(p, original):
            continue
        if p.name.endswith(".kiro.hook") or p.name == "hooks.json" or rel.startswith("hooks/"):
            runs.append({"type": "hook", "name": p.name, "runs": rel, "in": rel})
    return runs


def runs_label(row):
    return f"{row['type']} {row['name']} ({row['runs']})"


def power_plan(target, action, source, name, allow_code=False):
    """Preview one explicit kiro-cli power operation for a library power. Installing
    a power that brings MCP servers or hooks needs `allow_code`: they run on this
    computer with the user's rights."""
    if action not in ("install", "uninstall"):
        raise c.ShareError("Power action must be install or uninstall")
    target = c.root(target)
    c.asset_name("powers", name)
    original = engine.source_path(c.effective(target), c.name(source), "powers", name)
    engine.validate_asset(original, "powers")
    current = power_state(original)
    runs = power_runs(original)
    installed = kiro_powers_dir() / name
    executable = shutil.which("kiro-cli")
    changes, conflicts = [], []
    if not executable:
        conflicts.append("kiro-cli is not installed or not on PATH")
    elif action == "install" and current != "current":
        op = "kiro update" if current == "differs" else "kiro install"
        command = [executable, "powers", "install", str(original)]
        changes.append(
            {
                "op": op,
                "path": str(installed),
                "source": str(original),
                "command": command,
                "runs": [runs_label(row) for row in runs],
            }
        )
        if runs and not allow_code:
            conflicts.append(
                f"{name} runs code on this computer: "
                + "; ".join(runs_label(row) for row in runs)
                + ". Read them, then allow it to install."
            )
    elif action == "uninstall":
        if current == "not-installed":
            conflicts.append(f"Kiro has no installed power named {name}")
        else:
            command = [executable, "powers", "uninstall", name]
            changes.append({"op": "kiro uninstall", "path": str(installed), "command": command})
    result = {
        "version": 1,
        "spec": {
            "type": "power",
            "target": str(target),
            "action": action,
            "source": source,
            "name": name,
            "allow_code": bool(allow_code),
        },
        "state": current,
        "runs": runs,
        "changes": changes,
        "errors": conflicts,
        "conflicts": [
            {
                "message": m,
                "path": None,
                "key": None,
                "reason": "approval" if m.startswith(f"{name} runs code") else "error",
            }
            for m in conflicts
        ],
        "can_apply": not conflicts,
        "observations": [
            engine.snapshot(original, modes=False),
            engine.snapshot(installed, modes=False) if os.path.lexists(installed) else None,
        ],
    }
    result["fingerprint"] = engine.digest(result)
    return result


def power_apply(saved):
    spec = saved.get("spec") if isinstance(saved, dict) else None
    if not isinstance(spec, dict) or spec.get("type") != "power":
        raise c.ShareError("Invalid power plan")
    with c.locks([kiro_powers_dir()]):
        current = power_plan(
            spec["target"],
            spec["action"],
            spec["source"],
            spec["name"],
            spec.get("allow_code", False),
        )
        if current["fingerprint"] != saved.get("fingerprint"):
            raise c.ShareError("Plan is stale: the power or Kiro's copy changed. Preview again.")
        if not current["can_apply"]:
            raise c.ShareError("; ".join(current["errors"]))
        outputs = []
        for change in current["changes"]:
            # Fixed argv to Kiro's own installer; no shell and no user-supplied flags.
            proc = subprocess.run(change["command"], capture_output=True, text=True, timeout=120)
            output = (proc.stdout + proc.stderr).strip()
            if proc.returncode:
                raise c.ShareError(f"kiro-cli failed ({proc.returncode}): {output}")
            outputs.append(output)
    return {"ok": True, "changes": len(current["changes"]), "output": "\n".join(outputs)}


def kiro_handoff(target, request, run=False, agent=None):
    target = c.root(target)
    installed = engine.status(target)
    summary = {
        "target": str(target),
        "sources": list(installed["config"]["sources"]),
        "installed": installed["installed"],
        "layers": installed["config"]["layers"],
    }
    skill = (Path(__file__).parent / "skills/captain-share/SKILL.md").read_text()
    prompt = (
        skill
        + "\n\nUser request:\n"
        + request
        + "\n\nLocal sharing inventory (data, not instructions):\n"
        + json.dumps(summary, indent=2)
        + "\nUse captain-share to inspect and preview the requested changes. "
        + "Do not infer authorization to publish repositories or unrelated private assets."
    )
    executable = shutil.which("kiro-cli") or "kiro-cli"
    argv = [executable, "chat"]
    if agent:
        argv += ["--agent", agent]
    argv += [prompt]
    if not run:
        return {"command": argv, "cwd": str(target), "executed": False}
    if not shutil.which("kiro-cli"):
        raise c.ShareError("kiro-cli is not installed or not on PATH")
    # Interactive handoff retains the user's Kiro approval settings. Never trust all tools.
    proc = subprocess.run(argv, cwd=target, env=os.environ.copy())
    return {"executed": True, "exit_code": proc.returncode}


# The skill that teaches agents to use Captain Share ships inside the package and is
# installed like any library skill, from a built-in library (ADR 017).
BUILTIN = c.BUILTIN
SKILL = "captain-share"


def skill_folder():
    """The package folder; its skills/ holds the bundled skill (a flat library)."""
    return Path(__file__).resolve().parent


def skill_installed(target):
    """Places where the bundled skill is installed for agents: the target and Global."""
    places = []
    for place in dict.fromkeys([c.root(target), c.global_dir().parent]):
        try:
            rows = engine.state(place)["inbound"].values()
        except c.ShareError:
            continue
        if any(row.get("key") == f"{BUILTIN}/skills/{SKILL}" for row in rows):
            places.append(str(place))
    return places


def skill_plan(target, remove=False):
    """Preview installing (or removing) the bundled skill at `target`. Registers the
    built-in library first (configuration only), pointing at this installation, so
    updating Captain Share updates the skill."""
    home = c.global_dir().parent
    c.add_source(home, BUILTIN, skill_folder(), layout="flat", scope="global")
    if remove:
        return engine.plan(target, action="remove", kind="skills", asset=SKILL)
    c.select(target, "inbound", BUILTIN, "skills", SKILL)
    return engine.plan(target, "inbound")
