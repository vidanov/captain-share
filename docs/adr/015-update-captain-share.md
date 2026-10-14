# ADR 015: Update Captain Share from the window and the CLI

Status: Accepted

## Context

Captain Share runs from a Git checkout (`bin/captain-share`, often linked into
`~/.local/bin`) or is installed with pip from a Git URL or a local folder. People
update rarely and then by hand, and an open window keeps running the old code until
its process is replaced, which meant finding and stopping it first.

## Decision

`selfupdate.py` finds how this copy runs: from a Git checkout (the package lies in
one), or from pip's record of its source (PEP 610 `direct_url.json`: a Git URL, or a
local folder that is a Git checkout). `captain-share update` and About in the window
show the version, the installation and, after a fetch, the incoming commits.
Updating uses the same way it was installed:

- A checkout is fast-forwarded with `git pull --ff-only`, only when it has no local
  changes, has an upstream and is not ahead of it. Otherwise nothing changes and the
  hint names the command to look.
- A pip install from a folder pulls that checkout, then reinstalls unless it is
  editable; a pip install from Git runs `pip install --upgrade git+URL@REF` with the
  interpreter that runs Captain Share.
- Anything else is refused with the manual command.

**Restart window** replaces the server process (`execve`) with the same command line,
without `--open`, on the same port and with the same launch token, so the open page
reconnects and reloads. The token travels in a file created with `mkstemp` (readable
by the user only) in the temporary folder; the new process reads and deletes it, and
only a file in that folder is trusted. Neither its environment nor programs it
starts hold the token.

## Consequences

Updating needs network access to the Captain Share remote and the user's Git or pip
setup; failures are reported, never retried or forced. Other windows still run the
old code until restarted. A restart drops plans the window had previewed; they are
previewed again.
