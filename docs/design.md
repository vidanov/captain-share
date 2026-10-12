# Design and boundaries

The manager separates content ownership from installation:

- A library checkout owns reusable assets. It can be private or public, on any Git host.
- A project owns its selection and local setup; a source connection grants no remote access.
- The CLI engine owns path validation, plans, conflicts, links/copies and archives.
- The loopback UI calls the same engine. A management skill/Kiro chat helps with decisions.

Configuration is layered global → ancestor folders → target, with local bindings
separate from portable selections. Inbound links are live. Outbound transfers copy
whole selected asset units; skills retain their support files. There is no background
publication, remote credential store, or model required for mechanical work.
Explicit UI actions can clone, pull or push using existing Git credentials.

The implementation was informed by an existing Captain tooling interface and local
profile/link/preset workflows. It is a fresh standalone implementation; private engine
source, brand assets, customer settings, and installation records are not vendored.

Plans include content/configuration fingerprints. Apply regenerates the operations
rather than trusting a plan's editable action list, under ordered per-user locks for
targets, sources, and shared Git exclusions. Existing unowned files and independently
edited managed assets conflict. Handled failures reverse completed operations. This
is not a crash-atomic filesystem or a defense against a hostile same-user process.

The UI is served with local static files, no CDN dependencies, no external analytics,
and no remote model calls. It binds to loopback, checks Host/Origin, uses a per-launch
access token for all API operations, and never exposes an arbitrary command runner.
The token is in the URL fragment, then held in session storage. Stop the process to
revoke it. The UI can change only its startup target plus explicitly connected local
library destinations, plus an explicitly chosen new clone destination.

Current scope: POSIX/macOS/Linux, local or explicitly cloned checkouts, Kiro paths, Claude
Code skill paths, and Copilot files ([ADR 002](adr/002-agent-tool-destinations.md)). Kiro powers
are installed through `kiro-cli`, never written directly ([ADR 003](adr/003-kiro-powers.md)). Per-agent discovery rules still apply. Future work can add pinned copies,
agent dependency closure, Windows link handling,
and a Captain adapter without making those claims for this release.
