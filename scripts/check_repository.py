#!/usr/bin/env python3
"""Check the product's static import boundary and maintained local doc links."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# Only the low-level engine/config separation is constrained. UI/CLI can evolve.
ALLOWED_INTERNAL = {
    "config": set(),
    "engine": {"config"},
    "integrations": {"config", "engine"},
}


def internal_imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.name.startswith("captain_share."):
                    yield item.name.split(".")[1], node.lineno
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level and not module:
                for item in node.names:
                    yield item.name.split(".")[0], node.lineno
            elif node.level:
                yield module.split(".")[0], node.lineno
            elif module == "captain_share":
                for item in node.names:
                    yield item.name.split(".")[0], node.lineno
            elif module.startswith("captain_share."):
                yield module.split(".")[1], node.lineno


def inspect(root: Path) -> list[str]:
    failures = []
    for module, allowed in ALLOWED_INTERNAL.items():
        path = root / "captain_share" / f"{module}.py"
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except (OSError, SyntaxError) as exc:
            failures.append(f"INVALID_MEASUREMENT: cannot parse {path}: {exc}")
            continue
        for dependency, line in internal_imports(tree):
            if dependency not in allowed:
                failures.append(f"IMPORT_BOUNDARY: {path.name}:{line} cannot import {dependency}")
    documents = [root / "README.md", root / "AGENTS.md", root / "CONTRIBUTING.md"]
    documents += sorted((root / "docs").rglob("*.md"))
    for path in documents:
        if not path.is_file():
            failures.append(f"MISSING_DOCUMENT: {path.relative_to(root)}")
            continue
        for link in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text()):
            if link.startswith(("http:", "https:", "mailto:", "#")):
                continue
            clean = link.split("#", 1)[0]
            if clean and not (path.parent / clean).exists():
                failures.append(f"BROKEN_LINK: {path.relative_to(root)} -> {clean}")
    return failures


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    root = Path(arguments[0]).resolve() if arguments else Path(__file__).resolve().parents[1]
    problems = inspect(root)
    for problem in problems:
        print(problem)
    if not problems:
        print("Repository checks passed: static import boundaries and local document links.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
