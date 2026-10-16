from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from . import config as c
from . import engine as e
from . import imports as im
from . import integrations as i
from . import repositories as r


def explain(hint=None, command=None, stream=None):
    """Print the next step for an explained Git failure (ADR 009)."""
    if hint:
        print(f"  {hint}", file=stream or sys.stdout)
    if command:
        print(f"  Run: {command}", file=stream or sys.stdout)


def pushed_lines(result):
    """Push outcome, review link and next step after a commit."""
    if result.get("pushed"):
        print("Pushed.")
    else:
        print(f"Not pushed: {result.get('push_error')}")
        explain(result.get("push_error_hint"), result.get("push_error_command"))
    if result.get("waiting_for_review"):
        print(f"Waiting for review on branch {result.get('branch')}.")
        if result.get("review_url"):
            print(f"Open the review: {result['review_url']}")


def outdated_text(report):
    """Readable groups for `outdated`, each with what to do about it."""
    rows = report["rows"]

    def ref(row):
        return f"{row['source']}/{row['kind']}/{row['name']}"

    def where(row):
        return "" if row["place"] == "Global" or "path" in row else f"  (in {row['place']})"

    def path(row):
        base = "~" if row["place"] == "Global" else row["place"]
        return f"{base}/{row['path']}"

    def have(row):
        found = row.get("have") or {}
        return f" · you have {found['commit']}" if found.get("commit") else ""

    groups = [
        (
            "newer",
            "Newer in the library on the server (Update library, or captain-share fetch/pull):",
            lambda x: f"{ref(x)} · {x['newer']} newer commit{'s' if x['newer'] != 1 else ''}{have(x)}",
        ),
        (
            "edited",
            "Edited in your library checkout, not shared yet (Share → Send changes):",
            lambda x: ref(x) + have(x),
        ),
        (
            "older-copy",
            "Copies of an older team version (captain-share adopt KIND NAME --source S --replace):",
            lambda x: f"{path(x)} = {x['source']} version from {x['older']['at'][:10]}",
        ),
        (
            "changed-copy",
            "Copies you changed (captain-share diff KIND NAME --source S):",
            lambda x: f"{path(x)} differs from {x['source']}",
        ),
        (
            "kept-behind",
            "Your kept versions whose team original changed since:",
            lambda x: f"{x['kind']}/{x['name']} (from {x['source']}/{x['of']})",
        ),
        ("unknown", "Could not check:", lambda x: f"{ref(x)}: {x.get('detail', '')}"),
    ]
    lines = []
    for state, title, show in groups:
        found = [x for x in rows if x["state"] == state]
        if found:
            lines.append(title)
            lines.extend(f"  {show(x)}{where(x)}" for x in found)
            lines.append("")
    current = sum(1 for x in rows if x["state"] in ("current", "copy-current"))
    lines.append(f"Up to date: {current}" if lines else f"Everything is up to date ({current}).")
    if report["checked"]:
        when = ", ".join(
            f"{source} {(at or 'never')[:16].replace('T', ' ')}"
            for source, at in sorted(report["checked"].items())
        )
        lines.append(f"Last checked with the server: {when} (--fetch to check now)")
    else:
        lines.append("Compared with the commits last fetched; --fetch checks the server now.")
    lines.extend(f"Problem: {x}" for x in report["problems"])
    return "\n".join(lines)


def update_text(found, applied):
    """Readable lines for `update`."""
    if applied:
        if not found["updated"]:
            return "Captain Share is up to date."
        return (
            f"Updated Captain Share from {found['from']} to {found['to']}.\n"
            "Restart open windows (captain-share serve) to use it."
        )
    where = {
        "checkout": f"Git checkout {found.get('path')}",
        "pip-folder": f"pip, from {found.get('path')}",
        "pip-git": f"pip, from {found.get('url')}",
    }.get(found["kind"], "an unknown way")
    lines = [
        f"Captain Share {found['version']}"
        + (f" · {found['commit']}" if found.get("commit") else "")
        + (f" ({found['date'][:10]})" if found.get("date") else ""),
        f"Installed with {where}",
    ]
    behind = found.get("behind")
    if behind:
        lines.append(f"{behind} update{'s' if behind != 1 else ''} available.")
        for section in found.get("news") or []:
            lines.append(f"What's new in {section['version']}:")
            lines.extend(f"  - {item}" for item in section["items"])
        if not found.get("news"):
            lines.extend(f"  {line}" for line in found.get("incoming", []))
        lines.append("Run captain-share update --apply to update.")
    elif behind == 0:
        lines.append("Up to date.")
    else:
        lines.append(f"Update by hand: {found['command']}")
    if found.get("dirty"):
        lines.append(
            "The checkout has local changes; update leaves it alone until they are committed."
        )
    return "\n".join(lines)


def why_text(found):
    """Readable lines for `why`."""
    home = str(Path.home())

    def short(value):
        return "~" + value[len(home) :] if value.startswith(home + "/") else value

    lines = [short(found["asset"])]
    if found.get("source"):
        lines[0] += (
            f"  →  {found['source']}/{found['kind']}/{found.get('library_name', found['name'])}"
        )
    if found.get("original"):
        lines.append(f"  link to {short(found['original'])}")
    if found["status"] == "linked":
        lines.append(
            "  installed by Captain Share"
            if found.get("managed")
            else "  a link Captain Share did not create"
        )
    if found.get("selected_in"):
        lines.append(f"  selected in {short(found['selected_in'] + '/.captain/sharing.json')}")
    if found.get("note"):
        lines.append(f"  {found['note']}")
    return "\n".join(lines)


def output(value, as_json=False):
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True))
    elif isinstance(value, dict) and "fingerprint" in value:
        scope = value["spec"].get("direction", "Kiro powers")
        print(f"Preview: {value['spec']['action']} / {scope} in {value['spec']['target']}")
        for row in value["changes"]:
            print(
                f"  {row['op']:8} {row['path']}"
                + (f" ← {row['source']}" if "source" in row else "")
            )
            if "command" in row:
                print(f"           runs: {' '.join(row['command'])}")
        for row in value.get("kept", []):
            print(f"  kept     {row['path']} ({row['reason']})")
        for note in value.get("notes", []):
            print(f"  note     {note}")
        for error in value["errors"]:
            print(f"  Blocked: {error}")
        print(
            f"{len(value['changes'])} asset changes. "
            + ("Ready to apply." if value["can_apply"] else "Resolve conflicts first.")
        )
    else:
        print(json.dumps(value, indent=2, sort_keys=True))


def parser():
    p = argparse.ArgumentParser(
        prog="captain-share",
        description="Share skills, agents and prompts between independent folders and repositories.",
    )
    p.add_argument(
        "--target",
        default=None,
        help="Project/repo folder (use your home folder for global Kiro installation)",
    )
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create .captain configuration")
    init.add_argument("--global", dest="global_scope", action="store_true")
    init.add_argument("--library", action="store_true", help="Also create the ip/ asset folders")
    source = sub.add_parser("source", help="Bind a library checkout")
    ss = source.add_subparsers(dest="source_action", required=True)
    add = ss.add_parser("add")
    add.add_argument("name")
    add.add_argument("path")
    add.add_argument(
        "--layout", choices=["ip", "flat"], help="Default: detected (ip/ folder, else flat)"
    )
    add.add_argument("--scope", choices=["repo", "folder", "global"], default="repo")
    add.add_argument("--url")
    remove = ss.add_parser("remove")
    remove.add_argument("name")
    remove.add_argument("--scope", choices=["repo", "folder", "global"], default="repo")
    disc = ss.add_parser(
        "disconnect",
        help="Disconnect a library everywhere: archive its links, drop its selections "
        "and bindings; the checkout folder stays",
    )
    disc.add_argument("name")
    disc.add_argument("--apply", action="store_true")
    ss.add_parser("list")
    sub.add_parser("config", help="Show merged configuration and contributing layers")
    tools = sub.add_parser(
        "tools", help="Show or set the agent tools that receive links (kiro, claude, copilot)"
    )
    tools.add_argument("tools", nargs="*", metavar="TOOL", help="Omit to show the current list")
    tools.add_argument("--scope", choices=["repo", "global"], default="repo")
    ls = sub.add_parser("list", help="List inbound assets")
    ls.add_argument("--source", required=True)
    sub.add_parser("scan", help="Find local outbound candidates")
    for command in ("select", "deselect"):
        sel = sub.add_parser(command, help="Save or disable one selection")
        sel.add_argument("direction", choices=["inbound", "outbound"])
        sel.add_argument("source")
        sel.add_argument("kind", choices=list(c.KINDS))
        sel.add_argument("name")
        sel.add_argument("--alias")
        sel.add_argument("--path", help="Outbound original, relative to target")
    choose = sub.add_parser("choose", help="Choose assets interactively")
    choose.add_argument("direction", choices=["inbound", "outbound"])
    choose.add_argument("--source", required=True)
    for command in ("plan", "sync"):
        cmd = sub.add_parser(
            command,
            help="Preview sharing changes"
            if command == "plan"
            else "Preview or apply the current selections",
        )
        cmd.add_argument("--direction", choices=["inbound", "outbound", "both"], default="inbound")
        if command == "plan":
            cmd.add_argument("--save", help="Save the exact preview as a local JSON file")
        else:
            cmd.add_argument("--apply", action="store_true", help="Apply this fresh preview")
    ap = sub.add_parser("apply", help="Apply a saved preview if still current")
    ap.add_argument("plan_file")
    get = sub.add_parser(
        "get",
        help="Get one library asset by name for your user (or this project with --here)",
    )
    get.add_argument("ref", metavar="REF", help="NAME, SOURCE/NAME or SOURCE/KIND/NAME")
    get.add_argument(
        "--here", action="store_true", help="Install into --target (or this folder) instead"
    )
    get.add_argument("--apply", action="store_true", help="Apply without asking")
    get.add_argument(
        "--allow-code",
        action="store_true",
        help="For a Kiro power: allow the MCP servers and hooks it brings to run",
    )
    share = sub.add_parser(
        "share", help="Copy one local asset into a library and commit only it with a note"
    )
    share.add_argument("kind", choices=list(c.KINDS))
    share.add_argument("name")
    share.add_argument("--source", required=True, help="Library to share into")
    share.add_argument("--note", help="Commit message describing the share (needed to apply)")
    share.add_argument("--path", help="The asset's path in --target when several exist")
    share.add_argument("--push", action="store_true", help="Push the commit after it is made")
    share.add_argument("--apply", action="store_true", help="Copy, commit (and push)")
    share.add_argument(
        "--review",
        action="store_true",
        help="Commit on a new review branch (merge request) instead of the library branch",
    )
    share.add_argument(
        "--adopt",
        action="store_true",
        help="Afterwards link this folder to the library original instead of its copy",
    )
    out = sub.add_parser(
        "outdated", help="Show what has a newer or different version than the library"
    )
    out.add_argument(
        "--fetch", action="store_true", help="Fetch each library first (reads the server)"
    )
    ren = sub.add_parser(
        "rendered", help="List files generated for other tools' formats and who links them"
    )
    ren.add_argument(
        "--clean", action="store_true", help="Archive generated files nothing links any more"
    )
    chk = sub.add_parser(
        "check", help="Show or set the command that checks a library before a share commits"
    )
    chk.add_argument("source")
    chk_set = chk.add_mutually_exclusive_group()
    chk_set.add_argument("--set", metavar="COMMAND", help='e.g. "python3 scripts/check.py"')
    chk_set.add_argument("--clear", action="store_true")
    why = sub.add_parser(
        "why", help="Show which library and asset a file in an agent folder came from"
    )
    why.add_argument("path")
    dif = sub.add_parser("diff", help="Show how your copy differs from the library version")
    dif.add_argument("kind", choices=list(c.KINDS))
    dif.add_argument("name")
    dif.add_argument("--source", required=True)
    dif.add_argument("--path", help="Your copy, relative to --target (default: found)")
    pro = sub.add_parser(
        "propose", help="Send your copy's changes to the library version (directly or for review)"
    )
    pro.add_argument("kind", choices=list(c.KINDS))
    pro.add_argument("name")
    pro.add_argument("--source", required=True)
    pro.add_argument("--path", help="Your copy, relative to --target (default: found)")
    pro.add_argument("--review", action="store_true", help="Commit on a new review branch")
    pro.add_argument("--note", help="Commit message (needed to apply)")
    pro.add_argument("--push", action="store_true")
    pro.add_argument("--apply", action="store_true")
    keep = sub.add_parser("keep", help="Keep your copy as your own version under a new name")
    keep.add_argument("kind", choices=list(c.KINDS))
    keep.add_argument("name")
    keep.add_argument("--source", required=True)
    keep.add_argument("--as", dest="new_name", required=True, metavar="NEWNAME")
    keep.add_argument("--path", help="Your copy, relative to --target (default: found)")
    keep.add_argument(
        "--no-team", dest="link_team", action="store_false", help="Do not link the team version"
    )
    keep.add_argument("--apply", action="store_true")
    sub.add_parser("versions", help="List your kept versions and whether the team's changed")
    add_cmd = sub.add_parser(
        "add",
        help="Get skills from a Git repository, like npx skills add (into my-skills)",
    )
    add_cmd.add_argument("source", metavar="REPOSITORY", help="https://…, git@… or OWNER/REPO")
    add_cmd.add_argument("--skill", action="append", default=[], help="A skill to add (repeatable)")
    add_cmd.add_argument("--all", action="store_true", help="Add every valid skill in it")
    add_cmd.add_argument("--ref", help="A branch or tag instead of the default branch")
    add_cmd.add_argument(
        "--here", action="store_true", help="Install into --target (or this folder) instead"
    )
    add_cmd.add_argument("--apply", action="store_true", help="Add and install without asking")
    ins = sub.add_parser(
        "install",
        help="Add skills from a zip file or folder to your library (my-skills) and get them",
    )
    ins.add_argument("path", metavar="PATH", help="A zip file or a folder")
    ins.add_argument(
        "--here", action="store_true", help="Install into --target (or this folder) instead"
    )
    ins.add_argument("--apply", action="store_true", help="Add, commit locally and install")
    prom = sub.add_parser("promote", help="Move an asset from my-skills to a team library")
    prom.add_argument("kind", choices=list(c.KINDS))
    prom.add_argument("name")
    prom.add_argument("--to", dest="team", required=True, metavar="TEAM")
    prom.add_argument("--note", help="Commit message in the team library (needed to apply)")
    prom.add_argument("--push", action="store_true")
    prom.add_argument("--apply", action="store_true")
    prom.add_argument(
        "--review",
        action="store_true",
        help="Commit on a new review branch (merge request) instead of the library branch",
    )
    st = sub.add_parser("starter", help="Show or change a library's starter set or a profile")
    st.add_argument("source")
    st.add_argument(
        "--profile",
        default="starter",
        help="A named profile (profiles/NAME.manifest) instead of the starter set",
    )
    change = st.add_mutually_exclusive_group()
    change.add_argument("--set", metavar="KIND/NAME,...", help="Replace the set")
    change.add_argument("--add", metavar="KIND/NAME")
    change.add_argument("--remove", metavar="KIND/NAME")
    change.add_argument("--clear", action="store_true", help="Remove the set")
    st.add_argument("--note", help="Commit message (needed to apply)")
    st.add_argument("--push", action="store_true")
    st.add_argument("--apply", action="store_true")
    st.add_argument(
        "--review",
        action="store_true",
        help="Commit on a new review branch (merge request) instead of the library branch",
    )
    profiles = sub.add_parser("profiles", help="List the named profiles a library offers")
    profiles.add_argument("source")
    adopt = sub.add_parser(
        "adopt", help="Replace local copies of a library asset with links to the original"
    )
    adopt.add_argument("kind", choices=list(c.KINDS))
    adopt.add_argument("name")
    adopt.add_argument("--source", required=True, help="Library that holds the original")
    adopt.add_argument("--path", help="Also archive this copy, e.g. the one you shared from")
    adopt.add_argument(
        "--replace", action="store_true", help="Archive copies that differ from the library"
    )
    adopt.add_argument("--apply", action="store_true")
    imp = sub.add_parser(
        "import",
        help="Add assets from a zip file or folder to a library, committing only them",
    )
    imp.add_argument("path", metavar="PATH", help="A zip file or a folder")
    imp.add_argument("--source", required=True, help="Library to add the assets to")
    imp.add_argument("--kind", choices=list(c.KINDS), help="Only assets of this kind")
    imp.add_argument("--name", help="Add only the asset with this name (renames a single asset)")
    imp.add_argument("--note", help="Commit message describing the addition (needed to apply)")
    imp.add_argument("--push", action="store_true", help="Push the commit after it is made")
    imp.add_argument("--apply", action="store_true", help="Copy, commit (and push)")
    imp.add_argument(
        "--review",
        action="store_true",
        help="Commit on a new review branch (merge request) instead of the library branch",
    )
    sub.add_parser("status", help="Inspect links and configuration")
    sub.add_parser("guard", help="Check that local installation paths are not tracked by Git")
    rem = sub.add_parser("remove", help="Archive one local asset or alias")
    rem.add_argument("kind", choices=list(c.KINDS))
    rem.add_argument("name")
    rem.add_argument(
        "--path", help="Archive only this unmanaged destination, e.g. .claude/skills/x"
    )
    rem.add_argument("--apply", action="store_true")
    sub.add_parser("archive", help="List archived assets")
    restore = sub.add_parser("restore", help="Restore an archived asset without overwriting")
    restore.add_argument("id")
    restore.add_argument("--apply", action="store_true")
    profile = sub.add_parser(
        "import-profile", help="Import an existing toolkit manifest as selections"
    )
    profile.add_argument("source")
    profile.add_argument("profile")
    profile.add_argument("--apply", action="store_true")
    cap = sub.add_parser(
        "import-captain", help="Import Captain connections, without taking over installations"
    )
    cap.add_argument("directory")
    cap.add_argument("--apply", action="store_true")
    pw = sub.add_parser("powers", help="List or install library powers with Kiro's installer")
    pa = pw.add_subparsers(dest="power_action", required=True)
    pl = pa.add_parser("list", help="Library powers and their Kiro install state")
    pl.add_argument("--source", required=True)
    for action in ("install", "uninstall"):
        cmd = pa.add_parser(
            action,
            help="Preview, then with --apply run kiro-cli powers "
            + action
            + (" (also updates)" if action == "install" else ""),
        )
        cmd.add_argument("source")
        cmd.add_argument("name")
        cmd.add_argument("--apply", action="store_true")
        if action == "install":
            cmd.add_argument(
                "--allow-code",
                action="store_true",
                help="Allow the MCP servers and hooks the power brings to run",
            )
    repo = sub.add_parser("repo", help="Keep a local directory of connected repos")
    rr = repo.add_subparsers(dest="repo_action", required=True)
    ra = rr.add_parser("add")
    ra.add_argument("name")
    ra.add_argument("path")
    rr.add_parser("list")
    assist = sub.add_parser("assist", help="Prepare or launch a Kiro CLI management chat")
    assist.add_argument("request")
    assist.add_argument("--run", action="store_true")
    assist.add_argument("--agent")
    sk = sub.add_parser(
        "skill", help="Install the skill that teaches your agents to use Captain Share"
    )
    sk.add_argument("action", nargs="?", choices=["install", "remove"], help="Omit to show status")
    sk.add_argument("--here", action="store_true", help="This project instead of every project")
    sk.add_argument("--apply", action="store_true")
    upd = sub.add_parser("update", help="Show what is new in Captain Share; --apply updates it")
    upd.add_argument("--apply", action="store_true", help="Update the way it was installed")
    serve = sub.add_parser("serve", help="Start the local web interface for this target")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--open", action="store_true")
    serve.add_argument(
        "--new",
        action="store_true",
        help="Start another window even if one is open for this folder",
    )
    return p


def resolve(ref, places):
    """(source, kind, name) for NAME, SOURCE/NAME or SOURCE/KIND/NAME across the
    libraries visible from `places`; ambiguity is an error naming the full refs."""
    parts = ref.split("/") if isinstance(ref, str) else []
    if not 1 <= len(parts) <= 3 or not all(parts):
        raise c.ShareError("Use NAME, SOURCE/NAME or SOURCE/KIND/NAME")
    source = c.name(parts[0]) if len(parts) > 1 else None
    kind = parts[1] if len(parts) == 3 else None
    if kind is not None and kind not in c.KINDS:
        raise c.ShareError(f"Unknown asset kind: {kind}")
    sources: dict = {}
    for place in places:
        sources.update(c.effective(place)["sources"])
    found = e.locate({"sources": sources}, parts[-1], source, kind)
    if not found:
        raise c.ShareError(
            f"No connected library has {ref}. If a teammate shared it recently, update "
            "the library first: Update library in `captain-share serve`, or git pull in "
            "its checkout."
        )
    if len(found) > 1:
        refs = ", ".join("/".join(row) for row in found)
        raise c.ShareError(f"{ref} is ambiguous; use one of: {refs}")
    return found[0]


def own_selection(target, key):
    data = c.read_json(c.safe_path(target, ".captain/sharing.json"), c.blank()) or c.blank()
    return data.get("inbound", {}).get(key)


def restore_selection(target, key, previous):
    directory = c.config_dir(target)
    with c.locks([directory]):
        cfg = c.read_json(directory / "sharing.json")
        c.validate(cfg, directory / "sharing.json")
        if previous is None:
            cfg["inbound"].pop(key, None)
        else:
            cfg["inbound"][key] = previous
        c.validate(cfg, directory / "sharing.json")
        c.atomic_json(directory / "sharing.json", cfg)


def get(args, project):
    """Save one inbound selection (or preview a Kiro power), preview, then apply
    after --apply or a yes at the terminal. A plan with conflicts applies nothing."""
    home = c.global_dir().parent
    target = project if args.here else home
    source, kind, name = resolve(args.ref, [home, target] if args.here else [home])
    label = f"{source}/{kind}/{name}"
    report = {"ref": label, "target": str(target), "applied": False, "result": None}

    def say(text):
        if not args.json:
            print(text)

    previous = None
    if kind == "powers":
        planned = i.power_plan(target, "install", source, name, args.allow_code)
    else:
        previous = own_selection(target, label)
        c.select(target, "inbound", source, kind, name)
        planned = e.plan(target, "inbound")
    report["plan"] = planned
    if not args.json:
        output(planned)
    if not planned["can_apply"]:
        if kind != "powers":
            restore_selection(target, label, previous)
        if any(x["reason"] == "approval" for x in planned["conflicts"]):
            say(f"Nothing was changed. If you trust it: captain-share get {args.ref} --allow-code")
        else:
            say(f"Nothing was changed; resolve the conflicts above to get {label}.")
        return report
    if not planned["changes"]:
        say(f"{label} is already installed.")
        return report
    apply = args.apply
    if not apply and not args.json and sys.stdin.isatty():
        apply = input(f"Get {name}? [y/N] ").strip().lower() in ("y", "yes")
        if not apply:
            if kind != "powers":
                restore_selection(target, label, previous)
            say("Nothing was changed.")
            return report
    if not apply:
        where = " --here" if args.here else ""
        say(f"Selection saved. To apply: captain-share get {args.ref}{where} --apply")
        return report
    report["result"] = i.power_apply(planned) if kind == "powers" else e.apply(planned)
    report["applied"] = True
    say(f"Got {label} for {'this project' if args.here else 'your user'}.")
    return report


def share(args, target):
    path = args.path
    if path is None:
        found = [
            row["path"]
            for row in e.scan(target)
            if row["kind"] == args.kind and row["name"] == args.name and row["status"] == "local"
        ]
        if len(found) != 1:
            raise c.ShareError(
                f"Choose the original with --path: {', '.join(found)}"
                if found
                else f"No local {args.kind} named {args.name} in {target}"
            )
        path = found[0]
    if args.apply and not args.note:
        raise c.ShareError("Add --note describing what you share; it becomes the commit message")
    planned = r.share_plan(
        target,
        args.source,
        args.kind,
        args.name,
        path,
        adopt=args.adopt,
        mode="review" if args.review else "direct",
    )
    if not args.apply or not planned["can_apply"]:
        if not args.json:
            output(planned)
            if planned["commit_paths"]:
                print(f"Commits only: {', '.join(planned['commit_paths'])}")
            for row in planned.get("adopt_changes", []):
                print(f"  then {row['op']:8} {row['path']}")
        return planned
    result = r.share_apply(planned, args.note, args.push, adopt=args.adopt)
    if not args.json:
        print(f"Committed {result['commit'][:12]} in {args.source}.")
        if args.push or result.get("waiting_for_review"):
            pushed_lines(result)
        if args.adopt:
            print(
                "This folder now uses the library version."
                if result["adopted"]
                else f"Kept your copy: {result['adopt_error']}"
            )
        print(f"Teammates can run: {result['get_command']}")
    return result


def import_items(rows, kind, name, label):
    """The candidates `import` adds: all valid ones, or the one --name selects."""
    if kind:
        rows = [row for row in rows if kind in row["kinds"]]
    if name:
        chosen = [row for row in rows if row["name"] == name]
        if not chosen and len(rows) == 1:
            chosen = rows  # A single asset is renamed.
        if len(chosen) != 1:
            names = ", ".join(row["name"] for row in rows) or "none"
            raise c.ShareError(
                f"Choose one asset with --name (and --kind); found in {label}: {names}"
                if chosen
                else f"No asset named {name} in {label}; found: {names}"
            )
    else:
        chosen = [row for row in rows if row["error"] is None]
        if not chosen:
            problems = "; ".join(f"{row['id']}: {row['error']}" for row in rows)
            raise c.ShareError(
                f"No asset to add in {label}" + (f" ({problems})" if problems else "")
            )
    return [
        {"id": row["id"], "kind": kind or row["kind"], "name": name or row["name"]}
        for row in chosen
    ]


def import_assets(args, target):
    """Preview adding assets from a zip file or folder; with --apply copy them into
    the library and commit only their paths with the note (ADR 006)."""
    if args.apply and not args.note:
        raise c.ShareError("Add --note describing what you add; it becomes the commit message")
    path = Path(args.path).expanduser().absolute()
    temp = None
    try:
        if path.is_file():
            if not zipfile.is_zipfile(path):
                raise c.ShareError(f"Not a zip file or folder: {path}")
            temp = Path(tempfile.mkdtemp(prefix="captain-share-import-"))
            _, root = im.unzip_file(path, temp)
        else:
            root = c.root(path)
            im.too_broad(root)
        rows = im.find(root, str(path) if temp else "")
        items = import_items(rows, args.kind, args.name, path)
        planned = im.plan(
            target,
            args.source,
            root,
            items,
            label=str(path),
            mode="review" if args.review else "direct",
        )
        if not args.apply or not planned["can_apply"]:
            planned["candidates"] = rows
            if not args.json:
                print(f"Found in {path}:")
                for row in rows:
                    note = f"  (cannot add: {row['error']})" if row["error"] else ""
                    print(f"  {row['kind']:10} {row['name']}{note}")
                print(f"Preview: add to {args.source}")
                for change in planned["changes"]:
                    print(f"  copy     {change['path']} ← {change['source']}")
                for error in planned["errors"]:
                    print(f"  Blocked: {error}")
                if planned["commit_paths"]:
                    print(f"Commits only: {', '.join(planned['commit_paths'])}")
                if planned["can_apply"]:
                    print("Ready. Add --note TEXT --apply to copy and commit.")
            return planned
        result = im.apply(planned, args.note, args.push)
    finally:
        if temp is not None:
            shutil.rmtree(temp, ignore_errors=True)
    if not args.json:
        print(f"Committed {result['commit'][:12]} in {args.source}.")
        if args.push or result.get("waiting_for_review"):
            pushed_lines(result)
        for command in result["get_commands"]:
            print(f"Teammates can run: {command}")
    return result


def staged(path):
    """(root, temporary folder or None) for a zip file or folder given on the command line."""
    if path.is_file():
        if not zipfile.is_zipfile(path):
            raise c.ShareError(f"Not a zip file or folder: {path}")
        temp = Path(tempfile.mkdtemp(prefix="captain-share-import-"))
        return im.unzip_file(path, temp)[1], temp
    root = c.root(path)
    im.too_broad(root)
    return root, None


def install(args, project):
    """Add a zip's or folder's assets to the personal library (ADR 008), committing
    locally, then get each one for the user (or --here for the project)."""
    from . import personal as me

    path = Path(args.path).expanduser().absolute()
    root, temp = staged(path)
    try:
        rows = im.find(root, str(path) if temp else "")
        items = import_items(rows, None, None, path)
        if not args.apply:
            if not args.json:
                print(f"Found in {path}:")
                for row in rows:
                    note = f"  (cannot add: {row['error']})" if row["error"] else ""
                    print(f"  {row['kind']:10} {row['name']}{note}")
                where = "this project" if args.here else "your user"
                print(f"Run with --apply to add them to {me.SOURCE} and install them for {where}.")
            return {
                "library": None,
                "added": [],
                "installed": [],
                "applied": False,
                "candidates": rows,
            }
        names = ", ".join(row["name"] for row in items)
        return add_and_install(args, project, root, items, str(path), f"Add {names}")
    finally:
        if temp is not None:
            shutil.rmtree(temp, ignore_errors=True)


def add_and_install(args, project, root, items, label, note, repository=False):
    """Copy `items` from a staged `root` into the personal library with `note` as the
    commit message, then get each one for the user (or --here for the project)."""
    from . import personal as me

    home = c.global_dir().parent
    target = project if args.here else home
    report: dict = {"library": me.ensure(), "added": [], "installed": [], "applied": False}
    names = ", ".join(row["name"] for row in items)
    planned = im.plan(target, me.SOURCE, root, items, label=label, repository=repository)
    if not planned["can_apply"]:
        report["plan"] = planned
        if not args.json:
            for error in planned["errors"]:
                print(f"  Blocked: {error}")
        return {**report, "can_apply": False}
    im.apply(planned, note, push=False)
    report["added"] = [f"{row['kind']}/{row['name']}" for row in items]
    for row in items:
        if row["kind"] == "powers":
            continue
        c.select(target, "inbound", me.SOURCE, row["kind"], row["name"])
    result = e.plan(target, "inbound")
    report["plan"] = result
    if not result["can_apply"]:
        if not args.json:
            output(result)
            print(f"Added to {me.SOURCE}, but not installed; resolve the conflicts above.")
        return {**report, "can_apply": False}
    e.apply(result)
    report.update(installed=report["added"], applied=True)
    if not args.json:
        print(
            f"Added {names} to {me.SOURCE} and installed for "
            + ("this project." if args.here else "your user.")
        )
    return report


def add(args, project):
    """Get skills from a Git repository, like `npx skills add URL --skill NAME`: a
    shallow clone, the chosen skills into the personal library (the commit names the
    repository and commit), then linked for the user (or --here for the project)."""
    from . import personal as me

    temp = Path(tempfile.mkdtemp(prefix="captain-share-add-"))
    try:
        root, commit, label = im.clone(args.source, temp / "repository", args.ref)
        rows = im.find_repository(root, label)
        valid = [row for row in rows if row["error"] is None]
        if not args.skill and not args.all:
            if not args.json:
                print(f"Skills in {label}:")
                for row in rows:
                    note = f"  (cannot add: {row['error']})" if row["error"] else ""
                    desc = f" · {row['description']}" if row.get("description") else ""
                    print(f"  {row['name']}{desc[:90]}{note}")
                print("Choose with --skill NAME (repeat it for several), or --all.")
            return {"source": label, "candidates": rows, "applied": False}
        if args.all:
            chosen = valid
        else:
            by_name = {row["name"]: row for row in rows}
            missing = [name for name in args.skill if name not in by_name]
            if missing:
                raise c.ShareError(
                    f"Not in {label}: {', '.join(missing)}. Run without --skill to list them."
                )
            chosen = [by_name[name] for name in args.skill]
            broken = [f"{row['name']}: {row['error']}" for row in chosen if row["error"]]
            if broken:
                raise c.ShareError("Cannot add " + "; ".join(broken))
        items = [{"id": row["id"], "kind": row["kind"], "name": row["name"]} for row in chosen]
        names = ", ".join(row["name"] for row in items)
        where = "this project" if args.here else "your user"
        if not args.apply:
            if not args.json:
                print(f"From {label}:")
                for row in chosen:
                    print(f"  {row['kind']:10} {row['name']}  ({row['id']})")
                print(
                    f"Adds them to {me.SOURCE} (only on this machine) and installs them for "
                    f"{where}. Read them first: skills from outside can run commands."
                )
            apply = (
                not args.json
                and sys.stdin.isatty()
                and input(f"Add {names}? [y/N] ").strip().lower() in ("y", "yes")
            )
            if not apply:
                if not args.json:
                    print("Nothing was changed.")
                return {"source": label, "chosen": items, "applied": False}
        return {
            "source": label,
            "commit": commit,
            **add_and_install(
                args, project, root, items, label, f"Add {names} from {label}", repository=True
            ),
        }
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def promote(args, target):
    from . import personal as me
    from .server import places

    planned = me.promote_plan(
        target,
        args.kind,
        args.name,
        args.team,
        places(target),
        "review" if args.review else "direct",
    )
    if not args.apply or not planned["can_apply"]:
        if not args.json:
            for row in planned["changes"]:
                where = row.get("label") or row["path"]
                print(f"  {row['op']:8} {where}")
            for error in planned["errors"]:
                print(f"  Blocked: {error}")
        return planned
    if not args.note:
        raise c.ShareError("Add --note describing the asset; it is the team commit message")
    result = me.promote_apply(planned, args.note, args.push)
    if not args.json:
        print(f"Committed {result['commit'][:12]} in {args.team}.")
        if args.push or result.get("waiting_for_review"):
            pushed_lines(result)
        for problem in result["errors"]:
            print(f"  Not finished: {problem}")
    return result


def disconnect_library(args, target):
    """Preview, or with --apply carry out, disconnecting a library (ADR 010)."""
    from . import disconnect as dc
    from .server import places

    planned = dc.plan(args.name, places(target))
    if not args.apply or not planned["can_apply"]:
        if not args.json:
            print(f"Disconnect {args.name} (the folder {planned['folder']} stays):")
            for row in planned["changes"]:
                where = row.get("place_label", "")
                print(f"  {row['op']:10} {row.get('path') or row.get('key')}  [{where}]")
            for row in planned["kept"]:
                what = row.get("path") if row["reason"] == "copy" else row.get("name")
                print(f"  kept       {what} ({row['reason']})")
                if row.get("command"):
                    print(f"             uninstall with: {row['command']}")
            for error in planned["errors"]:
                print(f"  Blocked: {error}")
            if planned["can_apply"]:
                print("Ready. Add --apply to disconnect.")
        return planned
    result = dc.apply(planned)
    if not args.json:
        print(
            f"Disconnected {args.name}: {len(result['archives'])} links archived in "
            f"{len(result['places'])} places; the folder stays."
        )
        for row in result["kept"]:
            what = row.get("path") if row["reason"] == "copy" else row.get("name")
            print(f"  kept {what} ({row['reason']})")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        target = c.root(args.target or ".")
        command = args.command
        if command == "init":
            result = c.init(target, "global" if args.global_scope else "repo", args.library)
        elif command == "source":
            if args.source_action == "add":
                result = c.add_source(
                    target, args.name, args.path, args.layout, args.scope, args.url
                )
            elif args.source_action == "remove":
                result = c.remove_source(target, args.name, args.scope)
            elif args.source_action == "disconnect":
                result = disconnect_library(args, target)
                if not args.json:
                    return 1 if result.get("can_apply") is False else 0
            else:
                result = c.effective(target)["sources"]
        elif command == "config":
            result = c.effective(target)
        elif command == "tools":
            result = (
                c.set_tools(target, args.tools, args.scope)
                if args.tools
                else {"tools": c.effective(target)["tools"]}
            )
        elif command == "list":
            result = e.catalog(target, args.source)
        elif command == "scan":
            result = e.scan(target)
        elif command in ("select", "deselect"):
            result = c.select(
                target,
                args.direction,
                args.source,
                args.kind,
                args.name,
                args.alias,
                args.path,
                command == "select",
            )
        elif command == "choose":
            if not sys.stdin.isatty():
                raise c.ShareError(
                    "Interactive selection needs a terminal; use select or the web interface"
                )
            candidates = (
                e.catalog(target, args.source) if args.direction == "inbound" else e.scan(target)
            )
            candidates = [row for row in candidates if row["status"] in ("available", "local")]
            for number, row in enumerate(candidates, 1):
                print(f"{number:3}  {row['kind']:20} {row['name']}")
            answer = input("Choose numbers separated by spaces (blank cancels): ").strip()
            selections = [int(n) for n in answer.split()]
            if any(n < 1 or n > len(candidates) for n in selections):
                raise c.ShareError("Selection number out of range")
            result = []
            for n in selections:
                row = candidates[n - 1]
                result.append(
                    c.select(
                        target,
                        args.direction,
                        args.source,
                        row["kind"],
                        row["name"],
                        path=row.get("path") if args.direction == "outbound" else None,
                    )
                )
        elif command in ("plan", "sync"):
            result = e.plan(target, args.direction)
            if command == "plan" and args.save:
                # Plan files include local paths; save them outside tracked project content.
                c.atomic_json(Path(args.save).expanduser().absolute(), result)
            if command == "sync" and args.apply:
                result = e.apply(result)
        elif command == "apply":
            saved = c.read_json(Path(args.plan_file))
            if not saved:
                raise c.ShareError("Saved plan does not exist")
            if args.target is not None and c.root(saved["spec"]["target"]) != target:
                raise c.ShareError("Saved plan targets a different folder than --target")
            # A saved plan owns its target; an explicit mismatch is rejected.
            result = e.apply(saved)
        elif command in ("get", "share", "import", "install", "promote", "add"):
            handler = {
                "add": add,
                "get": get,
                "share": share,
                "import": import_assets,
                "install": install,
                "promote": promote,
            }[command]
            result = handler(args, target)
            if args.json:
                output(result, True)
            blocked = (
                result.get("can_apply") is False
                or result.get("push_error")
                or result.get("adopt_error")
                or result.get("errors")
            )
            return 1 if blocked or result.get("plan", {}).get("can_apply") is False else 0
        elif command == "outdated":
            result = r.outdated(target, args.fetch)
            if not args.json:
                print(outdated_text(result))
                return 0
        elif command == "rendered":
            result = e.rendered_files(apply=args.clean)
            if not args.json:
                for row in result["files"]:
                    used = ", ".join(row["places"]) or "nothing links it"
                    print(f"{row['path']}\n  {used}")
                if not result["files"]:
                    print("No generated files.")
                elif result["archived"]:
                    print(f"Archived {len(result['archived'])} unused generated files.")
                elif result["unused"]:
                    print(f"{len(result['unused'])} unused; --clean archives them.")
                return 0
        elif command == "check":
            if args.set is not None or args.clear:
                command = None if args.clear else shlex.split(args.set) or None
                result = c.set_check(target, args.source, command)
            else:
                spec = c.effective(target)["sources"].get(args.source)
                if spec is None:
                    raise c.ShareError(f"Unknown library {args.source}")
                result = {"source": args.source, "check": spec.get("check")}
        elif command == "skill":
            home = c.global_dir().parent
            place = target if args.here else home
            if not args.action:
                found = i.skill_installed(place)
                result = {"installed": found}
                if not args.json:
                    print(
                        "The captain-share skill is installed for your agents ("
                        + ", ".join("Global" if p == str(home) else p for p in found)
                        + ")."
                        if found
                        else "The captain-share skill is not installed. "
                        "Run captain-share skill install to let your agents use Captain Share."
                    )
                    return 0
            else:
                result = i.skill_plan(place, remove=args.action == "remove")
                apply = args.apply
                if not apply and not args.json and result["can_apply"] and result["changes"]:
                    # Like get: show the preview, then ask, so it never ends silently.
                    output(result)
                    if sys.stdin.isatty():
                        verb = "Install" if args.action == "install" else "Remove"
                        apply = input(
                            f"{verb} the captain-share skill? [y/N] "
                        ).strip().lower() in (
                            "y",
                            "yes",
                        )
                    if not apply:
                        print(
                            f"Nothing was changed. To apply: captain-share skill {args.action} --apply"
                        )
                        return 0
                if apply and result["can_apply"]:
                    result = e.apply(result)
                    if not args.json:
                        print(
                            "Installed the captain-share skill. Start a new agent session to use it."
                            if args.action == "install"
                            else "Removed the captain-share skill (archived)."
                        )
                        return 0
        elif command == "update":
            from . import selfupdate as su

            result = su.update() if args.apply else su.status(fetch=True)
            if not args.json:
                print(update_text(result, args.apply))
                return 0
        elif command == "why":
            result = e.why(args.path)
            if not args.json:
                print(why_text(result))
                return 0
        elif command == "diff":
            result = e.diff(target, args.source, args.kind, args.name, args.path)
            if not args.json:
                for row in result["files"]:
                    if row["diff"]:
                        print(row["diff"], end="")
                    else:
                        note = "binary" if row["binary"] else "too large to show"
                        print(f"{row['status']}: {row['path']} ({note})")
                if not result["files"]:
                    print("Your copy matches the library version.")
                if result["truncated"]:
                    print("(Output shortened.)")
                return 0
        elif command == "propose":
            mode = "review" if args.review else "direct"
            planned = r.propose_plan(target, args.source, args.kind, args.name, args.path, mode)
            if not args.apply or not planned["can_apply"]:
                result = planned
                if not args.json:
                    output(planned)
                    if planned["branch"]:
                        print(f"Review branch: {planned['branch']}")
                    return 0 if planned["can_apply"] else 1
            else:
                if not args.note:
                    raise c.ShareError(
                        "Add --note describing your change; it is the commit message"
                    )
                result = r.propose_apply(planned, args.note, args.push)
                if not args.json:
                    print(
                        f"Committed {result['commit'][:12]}"
                        + (f" on {result['branch']}" if result["branch"] else "")
                        + "."
                    )
                    if args.push or result.get("waiting_for_review"):
                        pushed_lines(result)
                    if result["adopt_error"]:
                        print(f"Kept your copy: {result['adopt_error']}")
                    return 1 if result["push_error"] or result["adopt_error"] else 0
        elif command == "keep":
            result = e.plan(
                target,
                action="keep",
                kind=args.kind,
                asset=args.name,
                source=args.source,
                path=args.path,
                new_name=args.new_name,
                link_team=args.link_team,
            )
            if args.apply:
                result = e.apply(result)
        elif command == "versions":
            result = r.version_rows(target)
            if not args.json:
                for row in result:
                    state = "team changed since" if row["team_changed"] else "up to date with"
                    print(f"{row['key']:30} {state} {row['source']}/{row['kind']}/{row['of']}")
                if not result:
                    print("No kept versions here.")
                return 0
        elif command == "profiles":
            from .server import profiles_view

            result = profiles_view(target, args.source)
            if not args.json:
                for row in result["profiles"]:
                    state = f"invalid: {row['error']}" if row["error"] else " ".join(row["assets"])
                    print(f"{row['name']:20} {state}")
                if not result["profiles"]:
                    print(f"{args.source} offers no profiles.")
                return 1 if any(row["error"] for row in result["profiles"]) else 0
        elif command == "starter":
            from .server import starter_view

            view = starter_view(target, args.source, args.profile)
            label = r.profile_label(args.profile)
            if not (args.set or args.add or args.remove or args.clear):
                result = view
                if not args.json:
                    if view["error"]:
                        print(f"{label[0].upper() + label[1:]} is invalid: {view['error']}")
                    for ref in view["assets"]:
                        print(ref)
                    if not view["exists"]:
                        print(f"{args.source} has no {label.removeprefix('the ')}.")
                    return 1 if view["error"] else 0
            else:
                assets = list(view["assets"])
                if view["error"] and (args.add or args.remove):
                    raise c.ShareError(
                        f"The current {label} is invalid ({view['error']}); use --set or --clear"
                    )
                if args.set:
                    assets = [x.strip() for x in args.set.split(",") if x.strip()]
                elif args.add and args.add not in assets:
                    assets.append(args.add)
                elif args.remove:
                    if args.remove not in assets:
                        raise c.ShareError(f"{args.remove} is not in {label}")
                    assets.remove(args.remove)
                clear = bool(args.clear)
                planned = r.starter_plan(
                    target,
                    args.source,
                    [] if clear else assets,
                    clear,
                    "review" if args.review else "direct",
                    args.profile,
                )
                if not args.apply or not planned["can_apply"]:
                    result = planned
                    if not args.json:
                        print(planned["diff"], end="")
                        for error in planned["errors"]:
                            print(f"  Blocked: {error}")
                        return 0 if planned["can_apply"] else 1
                else:
                    if not args.note:
                        raise c.ShareError(
                            "Add --note describing the change; it is the commit message"
                        )
                    result = r.starter_apply(planned, args.note, args.push)
                    if not args.json:
                        print(f"Committed {result['commit'][:12]} in {args.source}.")
                        if args.push or result.get("waiting_for_review"):
                            pushed_lines(result)
                        return 1 if result["push_error"] else 0
        elif command == "adopt":
            result = e.plan(
                target,
                action="adopt",
                kind=args.kind,
                asset=args.name,
                source=args.source,
                replace=args.replace,
                path=args.path,
            )
            if args.apply:
                result = e.apply(result)
        elif command == "status":
            result = e.status(target)
        elif command == "guard":
            result = e.guard(target)
        elif command in ("remove", "restore"):
            result = e.plan(
                target,
                action=command,
                kind=getattr(args, "kind", None),
                asset=getattr(args, "name", None),
                archive_id=getattr(args, "id", None),
                path=getattr(args, "path", None),
            )
            if args.apply:
                result = e.apply(result)
        elif command == "archive":
            result = e.archives(target)
        elif command == "import-profile":
            result = i.import_profile(target, args.source, args.profile, args.apply)
        elif command == "import-captain":
            result = i.import_captain(args.directory, target, args.apply)
        elif command == "powers":
            if args.power_action == "list":
                result = i.powers(target, args.source)
            else:
                result = i.power_plan(
                    target,
                    args.power_action,
                    args.source,
                    args.name,
                    getattr(args, "allow_code", False),
                )
                if args.apply:
                    result = i.power_apply(result)
        elif command == "repo":
            result = (
                i.register(args.name, args.path) if args.repo_action == "add" else i.repositories()
            )
        elif command == "assist":
            result = i.kiro_handoff(target, args.request, args.run, args.agent)
        else:
            from .server import serve

            serve(target, args.port, args.open, args.new)
            return 0
        output(result, args.json)
        return (
            1
            if isinstance(result, dict)
            and (
                result.get("can_apply") is False
                or result.get("ok") is False
                or result.get("exit_code", 0) != 0
            )
            else 0
        )
    except (c.ShareError, OSError, ValueError, KeyError, TypeError) as exc:
        fields = exc.fields() if isinstance(exc, c.ShareError) else {}
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc), **fields}))
        else:
            print(f"captain-share: {exc}", file=sys.stderr)
            explain(fields.get("hint"), fields.get("command"), sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
