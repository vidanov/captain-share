#!/usr/bin/env python3
"""The verification entrypoint used both locally and by CI; missing tools fail."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run_steps(steps, root):
    for title, argv in steps:
        print(f"\n[{title}]", flush=True)
        try:
            result = subprocess.run(argv, cwd=root, check=False)
        except OSError as exc:
            print(
                f"INVALID_MEASUREMENT: {title} could not run: {exc}. Run make bootstrap.",
                file=sys.stderr,
            )
            return 2
        if result.returncode:
            print(f"FAILED: {title} (exit {result.returncode})", file=sys.stderr)
            return result.returncode
    return 0


def package_smoke(root):
    with tempfile.TemporaryDirectory(prefix="captain-share-package-") as temporary:
        tmp = Path(temporary)
        code = run_steps(
            [
                (
                    "Build wheel",
                    [
                        sys.executable,
                        "-m",
                        "build",
                        "--wheel",
                        "--no-isolation",
                        "--outdir",
                        str(tmp / "wheels"),
                    ],
                ),
            ],
            root,
        )
        if code:
            return code
        wheels = list((tmp / "wheels").glob("*.whl"))
        if len(wheels) != 1:
            print("INVALID_MEASUREMENT: expected one wheel", file=sys.stderr)
            return 2
        code = run_steps(
            [
                (
                    "Install wheel in isolation",
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "--no-deps",
                        "--no-index",
                        "--target",
                        str(tmp / "installed"),
                        str(wheels[0]),
                    ],
                ),
            ],
            tmp,
        )
        if code:
            return code
        env = os.environ.copy()
        env["PYTHONPATH"] = str(tmp / "installed")
        env["CAPTAIN_SHARE_HOME"] = str(tmp / "global")
        check = """from pathlib import Path
import captain_share
from captain_share.cli import main
package = Path(captain_share.__file__).resolve().parent
assert package.parent == Path("installed").resolve(), "Imported checkout instead of installed wheel"
assert (package / "web/index.html").is_file(), "Web assets missing from wheel"
assert (package / "skills/manage-shared-assets/SKILL.md").is_file(), "Management skill missing from wheel"
raise SystemExit(main(["--json", "status"]))
"""
        print("\n[Exercise installed package]", flush=True)
        return subprocess.run(
            [sys.executable, "-c", check], cwd=tmp, env=env, check=False
        ).returncode


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="Skip tests and wheel smoke test")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    steps = [
        (
            "Python formatting",
            [
                sys.executable,
                "-m",
                "ruff",
                "format",
                "--check",
                "captain_share",
                "tests",
                "scripts",
            ],
        ),
        (
            "Python lint",
            [sys.executable, "-m", "ruff", "check", "captain_share", "tests", "scripts"],
        ),
        ("Type checking", [sys.executable, "-m", "mypy"]),
        ("Repository boundaries", [sys.executable, "scripts/check_repository.py"]),
        ("Web syntax", ["node", "--check", "captain_share/web/app.js"]),
        (
            "Web formatting",
            [str(root / "node_modules/.bin/prettier"), "--check", "captain_share/web"],
        ),
    ]
    if not args.fast:
        steps.append(
            (
                "Disposable behavioral tests",
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            )
        )
    result = run_steps(steps, root)
    if result or args.fast:
        return result
    result = package_smoke(root)
    if not result:
        print("\nAll verification checks passed.")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
