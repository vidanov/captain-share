from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config as c
from . import engine as e
from . import integrations as i
from . import repositories as r


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
    serve = sub.add_parser("serve", help="Start the local web interface for this target")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--open", action="store_true")
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
        planned = i.power_plan(target, "install", source, name)
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
    planned = r.share_plan(target, args.source, args.kind, args.name, path)
    if not args.apply or not planned["can_apply"]:
        if not args.json:
            output(planned)
            if planned["commit_paths"]:
                print(f"Commits only: {', '.join(planned['commit_paths'])}")
        return planned
    result = r.share_apply(planned, args.note, args.push)
    if not args.json:
        print(f"Committed {result['commit'][:12]} in {args.source}.")
        if args.push:
            print("Pushed." if result["pushed"] else f"Not pushed: {result['push_error']}")
        print(f"Teammates can run: {result['get_command']}")
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
        elif command in ("get", "share"):
            result = get(args, target) if command == "get" else share(args, target)
            if args.json:
                output(result, True)
            blocked = result.get("can_apply") is False or result.get("push_error")
            return 1 if blocked or result.get("plan", {}).get("can_apply") is False else 0
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
                result = i.power_plan(target, args.power_action, args.source, args.name)
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

            serve(target, args.port, args.open)
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
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"captain-share: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
