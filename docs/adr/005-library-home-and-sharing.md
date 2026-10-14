# ADR 005: Library home, get, and share

Status: Accepted

## Context

Most teammates set Captain Share up once and open it again weeks later, usually
because someone said "try this skill". A few people use it often and want to pass a
good skill on. The assets grid (ADR 004) answers "where is what installed", which is
a maintenance question; neither group starts there. Sharing a skill back took four
separate steps (select outbound, preview, apply, commit and push in another tool).

## Decision

The web window opens on a **library home**: what is new since the user last looked,
what they have, and everything else, with one Get or Remove action per asset. The
grid stays available as "Where installed".

- **Get** saves one inbound selection for the user (home) or for the startup project,
  then previews and applies through the normal plan. Remove uses the existing remove
  plan. Kiro powers keep their `kiro-cli` plans (ADR 003).
- **New since last looked**: the first library request of a server session compares
  each library's assets with `seen.json` in the global configuration folder and then
  records the current set. The comparison stays fixed for that session, so items
  remain marked while the window is open. "Updated" marks installed assets whose
  files changed between the recorded and current library commit.
- **Shared by / when** comes from the library's Git history (last commit touching
  the asset). It is shown only; nothing is sent anywhere.
- **Update library** fetches, then fast-forwards with the existing pull operation.
  Fetch is read-only and runs only when asked.
- **Share** lists assets in the project that are not in a library, and library
  assets with uncommitted edits (a linked skill edited in place edits the library
  original). Sharing copies through the outbound plan, then commits **only that
  asset's paths** with the user's note as the message, and pushes when asked. This
  is the one place Captain Share creates commits; it never stages other paths, never
  forces, and keeps the commit if the push is refused.
- **`captain-share get NAME`** resolves `[SOURCE/][KIND/]NAME` across connected
  libraries, defaults to the user-wide target (`--here` for the current project),
  shows the preview, and applies after confirmation or `--apply`.
- **Starter set**: a library may provide `profiles/starter.manifest` (the existing
  profile format). First setup offers it; choosing it saves those selections for the
  user and opens the normal review.
- First setup asks once which agents the user uses and stores them as the user
  layer's `tools` (ADR 002).

## Consequences

The everyday screens show no places, agents or layers. Previews, ownership checks,
archives and locks are unchanged, because every action goes through the same plans.
Captain Share now commits in library checkouts, limited to explicitly shared asset
paths. `seen.json` is machine-local state and never belongs in Git.
