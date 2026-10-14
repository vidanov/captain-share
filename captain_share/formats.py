"""Convert one tool's file format into another's (ADR 012, ADR 013). Pure text
functions: `convert` returns a Conversion whose content is None when the target
tool has no equivalent, with notes naming what the conversion could not carry over
and, when the result lands elsewhere than the kind's usual folder, where."""

from __future__ import annotations

import json
import re
from typing import Callable, NamedTuple, Optional

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


class Conversion(NamedTuple):
    content: Optional[str]
    notes: list
    folder: Optional[str] = None  # destination folder; None: the kind's usual one
    leaf: Optional[str] = None  # destination name; None: the asset's name (or alias)
    directory: bool = False  # content is SKILL.md inside a folder named `leaf`


def stem(name):
    for suffix in (".md", ".json"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def skill_name(name):
    """A Claude skill name: lowercase letters, digits and hyphens, at most 64."""
    value = re.sub(r"[^a-z0-9-]+", "-", stem(name).lower()).strip("-")[:64].strip("-")
    return value or None


def steering_to_claude(text, name, read=None):
    """Always and fileMatch steering become rules (steering_to_rule). Manual steering
    becomes a skill only you call (/name); auto steering a skill Claude loads when its
    description fits the request: the closest Claude equivalents (ADR 013)."""
    fields, body = split_frontmatter(text)
    fields = fields or {}
    inclusion = scalar(fields.get("inclusion", [])) or "always"
    if inclusion not in ("manual", "auto"):
        return Conversion(*steering_to_rule(text))
    leaf = skill_name(name)
    if leaf is None:
        return Conversion(None, [f"{name} has no usable skill name; it stays Kiro-only"])
    description = scalar(fields.get("description", []))
    if not description:
        if inclusion == "auto":
            return Conversion(None, ["auto steering without a description stays Kiro-only"])
        description = f"Team guidance: {stem(name)}. Use when asked for it."
    head = f"---\nname: {leaf}\ndescription: {json.dumps(description)}\n"
    if inclusion == "manual":
        head += "disable-model-invocation: true\n"
    notes = [
        f"manual steering becomes the Claude skill /{leaf}, used only when called"
        if inclusion == "manual"
        else f"auto steering becomes the Claude skill {leaf}, loaded when its description fits"
    ]
    if "#[[file:" in body or "#[[folder:" in body:
        notes.append("#[[file:…]] references stay as text; Claude does not resolve them")
    return Conversion(head + "---\n" + body.lstrip("\n"), notes, ".claude/skills", leaf, True)


# Kiro agent tools and the Claude Code tools that do the same; others are dropped.
TOOLS = {
    "read": ["Read", "Grep", "Glob"],
    "fs_read": ["Read", "Grep", "Glob"],
    "write": ["Write", "Edit"],
    "fs_write": ["Write", "Edit"],
    "shell": ["Bash"],
    "execute_bash": ["Bash"],
    "web_fetch": ["WebFetch"],
    "web_search": ["WebSearch"],
    "glob": ["Glob"],
    "grep": ["Grep"],
}


def agent_to_subagent(text, name, read=None):
    """A Kiro CLI agent (JSON) to a Claude Code subagent (Markdown). Only agents that
    list their tools are converted, so a subagent never gets more tools than the
    agent had; MCP servers, hooks, resources and permissions stay Kiro-only."""
    try:
        agent = json.loads(text)
    except ValueError:
        return Conversion(None, ["not valid JSON; it stays Kiro-only"])
    if not isinstance(agent, dict):
        return Conversion(None, ["not an agent object; it stays Kiro-only"])
    prompt = agent.get("prompt")
    if isinstance(prompt, str) and prompt.startswith("file://"):
        ref = prompt[len("file://") :]
        prompt = read(ref) if read and not ref.startswith("/") else None
        if prompt is None:
            return Conversion(None, [f"prompt file {ref} is not readable here; it stays Kiro-only"])
    if not isinstance(prompt, str) or not prompt.strip():
        return Conversion(None, ["agent has no prompt; it stays Kiro-only"])
    tools = agent.get("tools")
    if not isinstance(tools, list) or not tools:
        return Conversion(None, ["agent lists no tools; list them to reach Claude"])
    notes, mapped = [], []
    if "*" not in tools:
        dropped = []
        for tool in tools:
            found = TOOLS.get(str(tool))
            if found:
                mapped += [x for x in found if x not in mapped]
            else:
                dropped.append(str(tool))
        if dropped:
            notes.append("tools without a Claude match dropped: " + ", ".join(dropped))
        if not mapped:
            return Conversion(None, notes + ["no tool maps to Claude; it stays Kiro-only"])
    kept = {"name", "description", "prompt", "tools", "$schema"}
    extra = [key for key in agent if key not in kept and agent[key] not in (None, [], {}, "")]
    if extra:
        notes.append("Kiro-only fields dropped: " + ", ".join(sorted(extra)))
    leaf = skill_name(name)
    if leaf is None:
        return Conversion(None, [f"{name} has no usable agent name; it stays Kiro-only"])
    description = agent.get("description") or f"Team agent {leaf}"
    head = f"---\nname: {leaf}\ndescription: {json.dumps(str(description))}\n"
    if mapped:
        head += "tools: " + ", ".join(mapped) + "\n"
    return Conversion(head + "---\n" + prompt.strip() + "\n", notes, None, leaf + ".md")


def prompt_conversion(text, name, read=None):
    return Conversion(*prompt_to_command(text))


# (kind, tool) pairs whose destination needs a generated file instead of a link.
CONVERTERS: dict[tuple[str, str], Callable[..., Conversion]] = {
    ("steering", "claude"): steering_to_claude,
    ("prompts", "claude"): prompt_conversion,
    ("agents", "claude"): agent_to_subagent,
}


def destinations(kind, name):
    """Destinations a conversion of this asset may use besides folder/name: a Claude
    skill folder (steering) or a renamed Markdown file (agents). Used to validate
    installation state, so an edited state file cannot point anywhere else."""
    leaf = skill_name(name)
    if leaf is None:
        return []
    if kind == "steering":
        return [f".claude/skills/{leaf}"]
    if kind == "agents":
        return [f".claude/agents/{leaf}.md"]
    return []


def convert(kind, tool, text, name, read=None):
    """The conversion of one asset for a tool that reads another format. `read`
    returns the text of a file beside the asset (for agent prompt files), or None."""
    return CONVERTERS[(kind, tool)](text, name, read)
