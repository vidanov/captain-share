# ADR 017: A skill that teaches agents to use Captain Share

Status: Accepted

## Context

People ask their agents for things like "get the review skill" or "share this
skill with the team". The CLI already does all of it with previews, JSON output
and actionable errors, so an MCP server would add a second interface to maintain
without adding capability. What agents lack is the instructions, and the bundled
management skill was neither installed anywhere nor current.

## Decision

- The bundled skill is `captain_share/skills/captain-share/` (`name: captain-share`):
  short, task-based, built around the CLI, with the rules agents must follow
  (preview first, ask before `--apply`, `--push`, `--replace` and `--allow-code`,
  relay the printed hints, never edit agent folders by hand).
- It is installed like any library skill, from a built-in library named
  `captain-share` whose path is the installed package folder (a flat library with
  one skill). Links therefore point into the installation, and updating Captain
  Share updates the skill. `captain-share skill [install|remove] [--here]`
  previews and applies; first setup offers it with a checkbox (on by default), and
  Settings → Agents shows whether it is installed with Install or Remove.
- The built-in library is not shown among libraries, is not offered as a share
  destination, is not counted as the first library in setup, and is skipped by
  `outdated`.
- No MCP server.

## Consequences

Agents can do what the user asks through the same previews and safeguards as
people. If Captain Share moves (a new checkout folder), running `skill install`
again points the links at the new place.
