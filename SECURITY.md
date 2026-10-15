# Security policy

## Supported versions

Security fixes go into the latest version on `main`; update with
`captain-share update --apply` (or About → Update Captain Share in the window).

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's
[private vulnerability reporting](https://github.com/vidanov/captain-share/security/advisories/new)
for this repository. Include what you did, what happened, and the version
(`captain-share update` shows it). You will get an answer within a week.

## What Captain Share protects

Captain Share runs only on your computer. The window listens on `127.0.0.1` with a
per-launch token and origin checks; nothing is sent anywhere except your own Git
pushes. It refuses unsafe archives, hidden characters, recognisable credentials and
personal paths when sharing, asks before a Kiro power may start MCP servers or
hooks, and never stores credentials. These checks are safeguards, not a
confidentiality review. See [docs/design.md](docs/design.md) for the boundaries.
