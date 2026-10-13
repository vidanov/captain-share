---
version: 1
slug: "captain-share-web-index-html"
primary_target: "captain_share/web/index.html"
related_targets: ["captain_share/web/app.js","captain_share/web/style.css"]
---

# Surface brief: Captain Share web window

Scope: the local web UI (index.html, app.js, style.css). Mode: Operate.
Audience and job: teammates setting up a project and browsing/adding skills for
Kiro, Claude Code, Codex, Copilot; secondary: sync projects, share back, history.
Constraints: vanilla, no build, CSP self only, no CDN fonts; every change previewed.

## Direction contract

THESIS: The window is a patch bay. Library assets are rows, places (this project,
the folders above it, Your user) are columns, and a lit jack shows where each asset
is installed. It refuses the admin sidebar with a checkbox table and tabs of lists.

OWN-WORLD: Anodized near-black panel with a second, lifted panel tone; engraved
off-white labels in small tracked caps; one workhorse sans, monospace only for
paths. Round jacks: hollow ring = free, mint fill = live, amber ring with dot =
pending patch, dimmed fill = inherited, red ring = blocked. Inheritance drawn as a
thin amber cable from a folder jack to the project jack. Hairline rules, no cards.

STORY: The teammate sees at a glance where every skill lives, patches what this
project or their user needs in a few clicks, reads the routing summary, applies.

FIRST VIEWPORT: Faceplate bar (wordmark, current project, libraries, Review). Below
it the matrix: column heads per place with their agent chips, rows grouped by
library with name and one-line description; a routing bar pinned at the bottom
("3 patches · Review and apply"). With no library: a single "Plug in a library"
panel with Choose folder and Clone.

FORM: Patch Bay; rank 1 of my ordered list (the pick, chosen by the user over the
assigned Settings Pane); seed key 3f345ea5. Signature interaction: clicking a jack
lights it amber and draws or removes its cable; the routing bar counts it.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
