# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Developers and architects on a team who use AI coding agents (Kiro IDE/CLI, Claude
Code, Codex, GitHub Copilot) across several repositories. They know Git and their
agents, but should not have to learn Captain Share's concepts (sources, inbound,
outbound, layers) before getting value. The maintainer of the shared libraries uses
the same window.

## Product Purpose

Captain Share lets a team keep reusable agent assets (skills, agents, prompts,
steering, Kiro powers) in their own library repositories and install them into
projects and agents as live links, with a preview before every change. Success: a
teammate opens the window and, within a minute, has a project set up with the right
skills for their agents, without reading documentation.

## Positioning

One library original, linked into every agent and project that needs it, with
ownership checks, previews, and recoverable archives instead of copy-paste or
overwrite. Libraries stay in the team's own repositories; nothing leaves the machine.

## Operating Context

- Local web UI started from a terminal with `captain-share serve --open`, bound to
  127.0.0.1 with a per-launch token; also a CLI and a bundled agent skill.
- Most frequent jobs (confirmed): set up a project, and browse and add skills.
  Keeping projects in sync and contributing skills back are secondary.
- One window manages a project, the folders above it, the user-wide install, and
  registered projects (ADR 004).

## Capabilities and Constraints

- Vanilla HTML/CSS/JS served as static files; no build step, no CDN, CSP `self` only.
- Every change is previewed, then applied; conflicts are shown with fixes; removals
  archive and can be restored.
- Agents and destinations: Kiro (IDE and CLI) `.kiro/`, Claude Code `.claude/skills`,
  Codex `.agents/skills`, Copilot `.github/`; Kiro powers install through `kiro-cli`.
- macOS and Linux; desktop browsers first, usable at phone width.

## Evidence on Hand

No screenshots, testimonials, or usage numbers exist; do not invent any.

## Product Principles

1. Get a teammate to a working setup before explaining concepts.
2. Show what will happen before it happens; never surprise with a file change.
3. One place per skill: make inheritance and user-wide installs visible.
4. Plain language over internal terms (library, agent, project, not source/inbound).
