"""Convert one tool's file format into another's (ADR 012). Pure text functions: each
returns (content, notes), where content is None when the target tool has no
equivalent and notes name what the conversion could not carry over."""

from __future__ import annotations

import json
import re

STEERING_KEYS = ("inclusion", "fileMatchPattern", "name", "description")


def split_frontmatter(text):
    """(fields as raw lines by key, body) for a leading --- block; (None, text) without."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text
    for end in range(1, len(lines)):
        if lines[end].strip() == "---":
            break
    else:
        return None, text
    fields: dict[str, list[str]] = {}
    key = None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t", "-")) and ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            fields[key] = [value.strip()] if value.strip() else []
        elif key is not None:
            fields[key].append(line.strip())
    return fields, "\n".join(lines[end + 1 :])


def scalar(raw):
    value = raw[0] if raw else ""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def patterns(raw):
    """fileMatchPattern as a list: a string, an inline [..] list or a block list."""
    if not raw:
        return []
    first = raw[0]
    if first.startswith("["):
        try:
            items = json.loads(first.replace("'", '"'))
        except ValueError:
            items = [x.strip().strip("\"'") for x in first.strip("[]").split(",")]
        return [str(x) for x in items if str(x).strip()]
    if first.startswith("- "):
        return [x[2:].strip().strip("\"'") for x in raw if x.startswith("- ")]
    return [scalar(raw)] if scalar(raw) else []


def steering_to_rule(text):
    """Kiro steering to a Claude Code rule (.claude/rules): always-included steering
    becomes a rule without frontmatter, fileMatch steering a rule with `paths`.
    Manual and auto steering have no rule equivalent."""
    fields, body = split_frontmatter(text)
    fields = fields or {}
    notes = []
    inclusion = scalar(fields.get("inclusion", [])) or "always"
    if inclusion in ("manual", "auto"):
        return None, [f"{inclusion} steering has no Claude rule equivalent; it stays Kiro-only"]
    if inclusion not in ("always", "fileMatch"):
        return None, [f"unknown inclusion {inclusion!r}; it stays Kiro-only"]
    dropped = [key for key in fields if key not in ("inclusion", "fileMatchPattern")]
    if dropped:
        notes.append("Claude rules read only paths; dropped " + ", ".join(dropped))
    if "#[[file:" in body or "#[[folder:" in body:
        notes.append("#[[file:…]] references stay as text; Claude does not resolve them")
    body = body.lstrip("\n")
    if inclusion == "always":
        return body, notes
    globs = patterns(fields.get("fileMatchPattern", []))
    if not globs:
        return None, ["fileMatch steering without fileMatchPattern; it stays Kiro-only"]
    head = "---\npaths:\n" + "".join(f"  - {json.dumps(g)}\n" for g in globs) + "---\n"
    return head + body, notes


ARGUMENT = re.compile(r"\$\{(10|[1-9])\}|\$\{@\}")


def prompt_to_command(text):
    """A Kiro CLI prompt to a Claude Code command (.claude/commands). Kiro numbers
    arguments from ${1}; Claude from $ARGUMENTS[0]. ${@} is $ARGUMENTS in both."""

    def replace(match):
        if match.group(0) == "${@}":
            return "$ARGUMENTS"
        return f"$ARGUMENTS[{int(match.group(1)) - 1}]"

    return ARGUMENT.sub(replace, text), []


# (kind, tool) pairs whose destination needs a generated file instead of a link.
CONVERTERS = {
    ("steering", "claude"): steering_to_rule,
    ("prompts", "claude"): prompt_to_command,
}


def convert(kind, tool, text):
    """(content, notes) for a destination that needs conversion."""
    return CONVERTERS[(kind, tool)](text)
