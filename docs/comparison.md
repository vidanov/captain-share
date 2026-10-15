# Captain Share and other tools

Several tools share agent skills. This compares them for the case Captain Share is
built for: a team that keeps skills, steering, prompts and agents in its own Git
repository, works in Kiro and Claude Code (and Codex or Copilot), and wants people
to send improvements back. Checked in October 2026 from each tool's documentation,
plus a hands-on test of Microsoft APM 0.33 on a copy of a real team library. These
tools change quickly; corrections are welcome.

| | Captain Share | `npx skills` (Vercel) | skillshare | Microsoft APM | Claude Code plugin marketplaces |
| --- | --- | --- | --- | --- | --- |
| Asset kinds | skills, steering, prompts, agents, Kiro powers, Copilot files | skills | skills; rules, commands and agents through extra settings | skills, prompts, instructions, agents, hooks, MCP servers | plugins (skills, commands, agents, hooks) |
| Kiro | yes: skills, steering, prompts, agents, powers | not confirmed | not built in | yes, partly (in our test prompts were not installed for Kiro) | no |
| Claude Code | yes; steering, prompts and agents converted from the Kiro originals | yes | yes | yes | yes |
| How it installs | links to one original; converted formats generated and linked | links to one copy | links to one folder | copies, pinned by a lock file | Claude's plugin install |
| Your existing repository | read as it is (`ip/`, flat, or category folders) | folders with `SKILL.md` | its own skills folder | needs its own package layout; our team library was rejected | needs a marketplace manifest |
| Send improvements back | one step: commit only that asset, signed if required, push or a review branch with a merge request | no | pulls local edits back into the source folder; then normal Git | normal Git | normal Git |
| Before changing files | preview, archive instead of delete, hidden-character and credential checks, approval for code-running powers | | security check | audit for drift and hidden Unicode | |
| Interface | web window and CLI, local only | CLI | CLI and web UI | CLI | inside Claude Code |

## When another tool is the better fit

- **You use Claude Code only** and your team already publishes plugins: a plugin
  marketplace is built in.
- **You only consume skills** from public repositories, in many agents: `npx skills`
  is quick and widely used. (`captain-share add OWNER/REPO --skill NAME` does the
  same into your own library.)
- **You want each project pinned** to exact versions of a declared set: APM's
  manifest and lock file do that.

## Why Captain Share

- **Kiro first, Claude too.** Kiro steering (with its inclusion modes), prompts,
  agents and powers are handled, and Claude Code gets rules, commands, skills and
  subagents generated from the same originals. No other tool covered Kiro steering
  and powers.
- **Sharing back is the point.** A skill improved in a project goes back to the team
  library as one small commit with your note, signed if the library requires it, or
  as a review branch with a merge request on a protected branch, and the project
  then uses the shared version. The others leave this to manual Git.
- **Your repository as it is.** No package format to adopt; existing team libraries
  work, and so do public repositories that group skills in categories.
- **Links keep one original.** Updating the library updates every project at once,
  and an edit to a linked skill is an edit to the library, ready to share.
- **Safe by default.** Every change is previewed, nothing is deleted, and the window
  explains Git problems with the command to fix them.

Ideas borrowed along the way: the hidden-character scan, `why` and `outdated` came
from looking at APM.

## Sources

- [vercel-labs/skills quickstart](https://www.mintlify.com/vercel-labs/skills/quickstart)
- [skillshare documentation](https://skillshare.runkids.cc/docs/) and
  [extras](https://skillshare.runkids.cc/docs/reference/commands/extras/)
- [Microsoft APM README](https://cdn.jsdelivr.net/gh/microsoft/apm@main/README.md)
- [Claude Code plugin marketplaces](https://claude.yourdocs.dev/docs/claude-code/plugin-marketplaces)
- [Kiro steering](https://kiro.dev/docs/steering/) and
  [Claude Code memory and rules](https://code.claude.com/docs/en/memory)
