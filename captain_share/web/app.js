"use strict";
const $ = (id) => document.getElementById(id);
function readToken() {
  const fresh = new URLSearchParams(location.hash.slice(1)).get("token");
  if (fresh) sessionStorage.setItem("captain-share-token", fresh);
  if (location.hash) history.replaceState(null, "", location.pathname);
  return fresh || sessionStorage.getItem("captain-share-token") || "";
}
let token = readToken();
// Pasting the printed URL into an open tab changes only the fragment.
window.addEventListener("hashchange", () => {
  const before = token;
  token = readToken();
  if (token !== before)
    task(async () => {
      await refresh();
      message("Connected.");
    });
});
let state,
  bay = { columns: [], rows: [] },
  patches = new Map(),
  kindFilter = "",
  showAllFolders = false,
  // Saved choices a cancelled review puts back: { steps, patches, touched }.
  undo = null,
  view = "patch",
  pending = null,
  lastSpec = null,
  resumeSpec = null,
  busy = false,
  drafts = {};
let repository = null,
  gitPending = null,
  messageTimer = null;
const TOOL_NAMES = {
  kiro: "Kiro",
  claude: "Claude Code",
  codex: "Codex",
  copilot: "Copilot",
};
const DESTINATION_LABELS = {
  installed: "installed",
  absent: "not installed",
  blocked: "local file in the way",
  contributed: "this folder's original",
  changed: "link needs review",
};
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function message(text, error = false) {
  clearTimeout(messageTimer);
  $("message").textContent = !text ? "" : error ? "Error: " + text : text;
  $("message").className = error ? "error" : "";
  if (text && !error) messageTimer = setTimeout(() => message(""), 6000);
}
$("message").title = "Dismiss";
$("message").onclick = () => message("");
let currentTarget = "",
  projects = [],
  pendingBatch = null;
async function api(path, data) {
  // Every request names the folder it acts on (ADR 004); the server checks it.
  if (currentTarget) {
    if (data === undefined)
      path +=
        (path.includes("?") ? "&" : "?") +
        "target=" +
        encodeURIComponent(currentTarget);
    else if (!("target" in data)) data = { ...data, target: currentTarget };
  }
  const res = await fetch("/api/" + path, {
    method: data === undefined ? "GET" : "POST",
    headers: {
      "X-Captain-Token": token,
      ...(data === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(data === undefined ? {} : { body: JSON.stringify(data) }),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || "Request failed");
  return body;
}
async function task(fn) {
  if (busy) {
    message("Still working on the previous action. Try again in a moment.");
    return;
  }
  busy = true;
  try {
    await fn();
  } catch (e) {
    message(e.message, true);
  } finally {
    busy = false;
  }
}
function invalidate() {
  pending = null;
  pendingBatch = null;
  resumeSpec = null;
  $("review").hidden = true;
  $("apply").disabled = true;
}
function here() {
  return state.places.find((row) => row.path === state.target) || {};
}
function homePath() {
  return (state.places.find((row) => row.kind === "user") || {}).path;
}
function renderPlaces() {
  const choices = state.places.filter((row) => row.kind !== "folder");
  $("place").replaceChildren(
    ...choices.map((row) => {
      const option = el(
        "option",
        row.kind === "user"
          ? "Your user only"
          : row.label + (row.path === state.startup ? " (this window)" : ""),
      );
      option.value = row.path;
      return option;
    }),
  );
  $("place").value = state.target;
}
function relativeTo(path, base) {
  return path.startsWith(base + "/")
    ? path.slice(base.length + 1)
    : shortPath(path);
}
function shortPath(path) {
  const home = homePath();
  return home && path.startsWith(home) ? "~" + path.slice(home.length) : path;
}
async function refresh(keepDrafts = false) {
  state = await api("state");
  currentTarget = state.target;
  renderPlaces();
  projects = await api("projects").catch(() => []);
  $("target").textContent = shortPath(state.target);
  $("target").title = state.target;
  $("reveal-workspace").textContent = "Show project in " + state.file_manager;
  $("layers").replaceChildren(...state.config.layers.map((p) => el("li", p)));
  const saved = $("source").value;
  $("source").replaceChildren(el("option", "Choose a library"));
  $("source").firstChild.value = "";
  Object.keys(state.config.sources).forEach((id) => {
    const op = el("option", id);
    op.value = id;
    $("source").append(op);
  });
  $("source").value = state.config.sources[saved]
    ? saved
    : Object.keys(state.config.sources)[0] || "";
  $("installed-count").textContent = state.installed.length;
  $("installed-count").hidden = !state.installed.length;
  bay = await api("matrix");
  await loadRepository();
  if (!keepDrafts) drafts = {};
  invalidate();
  render();
}
function key(row) {
  return [$("source").value, row.kind, row.name].join("/");
}
function draftKey(row) {
  return view + "/" + key(row) + (view === "outbound" ? "/" + row.path : "");
}
function remember(row, input, alias) {
  drafts[draftKey(row)] = {
    row,
    enabled: input.checked,
    alias: alias?.value || "",
  };
  invalidate();
  selectionCount();
}
function render() {
  const titles = {
    patch: [
      "Assets",
      "Click a circle to install an asset there. A folder shares its assets with every project inside it.",
    ],
    projects: [
      "Projects",
      "This project and the ones you registered. Sync after changing assets or pulling a library.",
    ],
    libraries: [
      "Libraries",
      "Folders with shared assets. Add one once and every project can use it.",
    ],
    outbound: [
      "Share back",
      "Copy local originals from this project into a library checkout, then commit there.",
    ],
    installed: [
      "Local files",
      "What sits in this project's agent folders: managed links and files Captain Share does not manage.",
    ],
    archive: [
      "Archive",
      "Removed assets waiting to be restored. Nothing is deleted.",
    ],
  };
  $("view-title").textContent = titles[view][0];
  $("view-help").textContent = titles[view][1];
  document
    .querySelectorAll(".faceplate-nav button")
    .forEach((b) => b.classList.toggle("active", b.dataset.tab === view));
  const sources = Object.keys(state.config.sources).length;
  $("first-run").hidden = !!sources || view !== "patch";
  $("bay").hidden = !sources && view === "patch";
  const isPatch = view === "patch";
  $("matrix").hidden = !isPatch;
  $("kind-filter").hidden = !isPatch;
  $("routing").hidden = !isPatch || !patchCount();
  $("libraries-view").hidden = view !== "libraries";
  $("list-view").hidden = isPatch || view === "libraries";
  $("search").hidden = ["projects", "libraries"].includes(view);
  $("source-bar").hidden = view !== "outbound";
  $("selection-actions").hidden = view !== "outbound";
  $("project-actions").hidden = view !== "projects";
  if (isPatch) return renderMatrix();
  if (view === "libraries") return renderLibraries();
  if (view === "projects") {
    renderHistory("");
    renderProjects();
    return;
  }
  selectionCount();
  const managed = new Set(state.installed.map((row) => row.path));
  let rows =
    view === "outbound"
      ? state.candidates
      : view === "installed"
        ? [
            ...state.installed,
            // Unmanaged files in tool folders can block receiving; offer to archive them.
            ...state.candidates
              .filter((row) => row.tool && !managed.has(row.path))
              .map((row) => ({ ...row, local: true })),
          ]
        : state.archives.filter((row) => !row.restored);
  const query = $("search").value.toLowerCase();
  rows = rows.filter((r) =>
    [r.name, r.kind, r.path, r.relative_path]
      .join(" ")
      .toLowerCase()
      .includes(query),
  );
  renderHistory(query);
  $("count").textContent =
    rows.length + (rows.length === 1 ? " asset" : " assets");
  $("assets").replaceChildren();
  if (!rows.length) {
    const empty = el("div", undefined, "empty");
    empty.append(
      el(
        "strong",
        view === "archive"
          ? "No assets waiting to be restored."
          : view === "installed"
            ? "Nothing in this project's agent folders yet."
            : $("source").value
              ? "No local originals to share."
              : "Add a library first.",
      ),
      el("p", "Your existing project files stay in place."),
    );
    $("assets").append(empty);
    return;
  }
  rows.forEach((row, index) => {
    const wrap = el("div", undefined, "asset");
    const main = el("div", undefined, "asset-main");
    if (view === "outbound") {
      const checked = state.config[view][key(row)];
      const draft = drafts[draftKey(row)];
      const input = el("input");
      input.type = "checkbox";
      input.id = "pick-" + index;
      input.checked = draft
        ? draft.enabled
        : !!checked && (view !== "outbound" || checked.path === row.path);
      input.disabled = !["available", "local"].includes(row.status);
      input.dataset.row = JSON.stringify(row);
      input.addEventListener("change", () =>
        remember(row, input, main.querySelector(".alias")),
      );
      wrap.append(input);
      const label = el("label", row.name, "asset-title");
      label.htmlFor = input.id;
      main.append(label);
      main.append(
        el("div", row.kind + " · " + row.status, "asset-type"),
        el("div", row.path, "asset-path"),
      );
      main.append(revealButton(state.target + "/" + row.path));
      if (row.error) main.append(el("p", row.error));
    } else {
      const installed = view === "installed";
      if (row.local && !rows[index - 1]?.local)
        $("assets").append(
          el("p", "Local files not managed by Captain Share", "group-title"),
        );
      const tool = row.tool ? " · " + TOOL_NAMES[row.tool] : "";
      main.append(
        el(
          "div",
          installed ? row.path.split("/").pop() : row.relative_path,
          "asset-title",
        ),
        el(
          "div",
          installed
            ? row.local
              ? "Local " + row.kind + tool
              : (row.status === "linked" ? "Installed" : "Needs attention") +
                tool
            : "Archived " +
                friendlyDate(row.created) +
                (row.snapshot?.type === "link"
                  ? " · managed link"
                  : " · local copy"),
          "asset-type",
        ),
        el("div", installed ? row.path : row.relative_path, "asset-path"),
      );
      if (installed && !row.local)
        main.append(
          el("div", "Original: " + row.shared_original, "asset-path"),
        );
      if (row.error) main.append(el("p", row.error));
      main.append(
        revealButton(
          installed
            ? state.target + "/" + row.path
            : state.target + "/.captain/archive/" + row.id + "/payload",
        ),
      );
      if (installed && !row.local)
        main.append(
          revealButton(
            row.shared_original,
            "Show original in " + state.file_manager,
          ),
        );
      const button = el("button", installed ? "Archive" : "Restore", "quiet");
      button.onclick = () =>
        task(() =>
          preview(
            installed
              ? {
                  action: "remove",
                  kind: row.kind,
                  name: row.path.split("/").pop(),
                  path: row.path,
                }
              : { action: "restore", id: row.id },
          ),
        );
      wrap.append(button);
    }
    wrap.append(main);
    $("assets").append(wrap);
  });
}
function changedDrafts() {
  const prefix = view + "/" + $("source").value + "/";
  return Object.entries(drafts)
    .filter(([k, draft]) => {
      if (!k.startsWith(prefix)) return false;
      const saved = state.config[view][key(draft.row)];
      return draft.enabled !== isSaved(draft.row) || false;
    })
    .map(([, draft]) => draft);
}
async function saveSelection(quiet = false) {
  if (!$("source").value)
    throw new Error("Connect and choose a library first.");
  // Send only real changes, so inherited selections are not copied into this folder.
  const selections = changedDrafts().map((draft) => ({
    kind: draft.row.kind,
    name: draft.row.name,
    path: draft.row.path,
    enabled: draft.enabled,
    alias: draft.alias || undefined,
  }));
  if (selections.length)
    await api("selections", {
      direction: view,
      source: $("source").value,
      selections,
    });
  await refresh();
  if (!quiet)
    message(
      selections.length
        ? "Selection saved. Preview to see the file changes."
        : "Nothing changed since the last save.",
    );
}
async function preview(spec = { direction: view }) {
  pending = await api("plan", spec);
  lastSpec = spec;
  $("review").hidden = false;
  $("changes").replaceChildren();
  $("errors").replaceChildren();
  $("review-count").textContent =
    pending.changes.length +
    (pending.changes.length === 1 ? " change" : " changes") +
    (pending.conflicts.length
      ? " · " +
        pending.conflicts.length +
        (pending.conflicts.length === 1 ? " conflict" : " conflicts")
      : "");
  setReviewHint(false);
  pending.changes.forEach((change) => {
    const div = el("div", undefined, "change");
    div.append(
      el(
        "strong",
        change.op,
        /archive|uninstall/.test(change.op) ? "op-remove" : "",
      ),
      " ",
      el("code", relativeTo(change.path, state.target)),
    );
    if (change.source) div.append(el("div", "From " + change.source));
    if (change.command)
      div.append(el("code", "Runs " + change.command.join(" "), "command"));
    $("changes").append(div);
  });
  if (!pending.changes.length)
    $("changes").append(
      el(
        "p",
        pending.conflicts.length
          ? "No changes can be applied until the conflicts below are resolved."
          : "The selected assets are already in place.",
      ),
    );
  pending.conflicts.forEach((conflict) =>
    $("errors").append(conflictRow(conflict, spec)),
  );
  $("conflict-help").hidden = !pending.conflicts.length;
  $("apply").disabled = !pending.can_apply;
  $("review").scrollIntoView({ behavior: "smooth", block: "start" });
}
function renderProjects() {
  $("count").textContent =
    projects.length + (projects.length === 1 ? " project" : " projects");
  $("assets").replaceChildren();
  if (!projects.length) {
    const empty = el("div", undefined, "empty");
    empty.append(
      el("strong", "No projects registered."),
      el("p", "Add project… registers a folder so it can be synced from here."),
    );
    $("assets").append(empty);
    return;
  }
  for (const row of projects) {
    const wrap = el("div", undefined, "asset");
    const main = el("div", undefined, "asset-main");
    const status = row.error
      ? row.error
      : row.linked +
        " linked · " +
        (row.changes
          ? row.changes +
            (row.changes === 1 ? " change" : " changes") +
            " to sync"
          : "in sync") +
        (row.drift ? " · " + row.drift + " need attention" : "") +
        (row.conflicts ? " · " + row.conflicts + " conflicts" : "");
    main.append(
      el("div", row.label, "asset-title"),
      el(
        "div",
        status + (row.path === state.target ? " · open here" : ""),
        "asset-type",
      ),
      el("div", shortPath(row.path), "asset-path"),
    );
    const actions = el("div", undefined, "destinations");
    if (row.path !== state.target) {
      const open = el("button", "Work on it", "quiet");
      open.type = "button";
      open.onclick = () => task(() => switchPlace(row.path, "patch"));
      actions.append(open);
    }
    const sync = el("button", "Preview sync", "quiet");
    sync.type = "button";
    sync.onclick = () => task(() => previewMany([row]));
    actions.append(sync);
    main.append(actions);
    wrap.append(main);
    $("assets").append(wrap);
  }
}
async function switchPlace(path, nextView) {
  currentTarget = path;
  drafts = {};
  if (nextView) view = nextView;
  await refresh();
  message(
    "Working on " + (here().kind === "user" ? "your user" : here().label) + ".",
  );
}
async function previewMany(rows) {
  if (!rows.length) throw new Error("No registered projects to sync here.");
  const plans = [];
  for (const [index, row] of rows.entries())
    plans.push({
      row,
      plan: await api("plan", {
        direction: "inbound",
        target: row.path,
        batch: index > 0,
      }),
    });
  pending = null;
  pendingBatch = plans;
  $("review").hidden = false;
  $("changes").replaceChildren();
  $("errors").replaceChildren();
  setReviewHint(!!undo);
  let total = 0;
  // Places that are already in sync add nothing to review; leave them out.
  const shown = plans.filter(
    ({ plan }) => plan.changes.length || plan.conflicts.length,
  );
  for (const { row, plan } of shown) {
    total += plan.changes.length;
    $("changes").append(
      el(
        "p",
        row.label +
          " · " +
          (plan.conflicts.length
            ? plan.conflicts.length + " conflicts, not applied"
            : plan.changes.length +
              (plan.changes.length === 1 ? " change" : " changes")),
        "group-title",
      ),
    );
    for (const change of plan.changes) {
      const div = el("div", undefined, "change");
      div.append(
        el(
          "strong",
          change.op,
          /archive|uninstall/.test(change.op) ? "op-remove" : "",
        ),
        " ",
        el("code", relativeTo(change.path, row.path)),
      );
      $("changes").append(div);
    }
    // Fixes act on the window's own place; other places name the conflict only.
    for (const conflict of plan.conflicts)
      $("errors").append(
        row.path === state.target
          ? conflictRow(conflict, { direction: "inbound" })
          : el("p", row.label + ": " + conflict.message),
      );
  }
  if (!shown.length)
    $("changes").append(el("p", "Everything is already in place."));
  $("review-count").textContent =
    total +
    (total === 1 ? " change" : " changes") +
    (shown.length > 1 ? " in " + shown.length + " places" : "");
  $("conflict-help").hidden = !plans.some(({ plan }) => plan.conflicts.length);
  $("apply").disabled = !plans.some(
    ({ plan }) => plan.can_apply && plan.changes.length,
  );
  $("review").scrollIntoView({ behavior: "smooth", block: "start" });
}
function conflictRow(conflict, spec) {
  const div = el("div", undefined, "conflict");
  div.append(el("p", conflict.message));
  const [source, kind, name] = (conflict.key || "").split("/");
  if (conflict.reason === "unmanaged" && conflict.path) {
    const archive = el("button", "Archive local copy", "quiet");
    archive.onclick = () =>
      task(async () => {
        // Continuing with a fix keeps the saved choices.
        undo = null;
        resumeSpec = spec;
        await preview({
          action: "remove",
          kind,
          name: conflict.path.split("/").pop(),
          path: conflict.path,
        });
        message(
          "Review the archive, apply it, then the original preview runs again.",
        );
      });
    div.append(archive);
  }
  if (name && state.config.inbound[conflict.key]) {
    const deselect = el("button", "Stop receiving " + name, "quiet");
    deselect.onclick = () =>
      task(async () => {
        delete drafts["inbound/" + conflict.key];
        await api("selections", {
          direction: "inbound",
          source,
          selections: [{ kind, name, enabled: false }],
        });
        await refresh(true);
        await preview(spec);
        message(name + " deselected. Preview updated.");
      });
    div.append(deselect);
  }
  return div;
}
let nameTouched = false;
function suggestName(value) {
  // Mirrors config.suggest_name: lower-case, unsafe characters become "-".
  return (
    value
      .toLowerCase()
      .replace(/[^a-z0-9_.-]+/g, "-")
      .replace(/^[-.]+|[-.]+$/g, "") || "library"
  );
}
async function describeLibrary() {
  const path = $("source-path").value.trim();
  const note = $("source-detected");
  // Never disable while checking: the field's change event fires on the same
  // mouse press that clicks Connect, and a disabled button drops that click.
  $("connect-source").disabled = !path;
  if (!path) {
    note.textContent = "";
    return null;
  }
  let info;
  try {
    info = await api("library?path=" + encodeURIComponent(path));
  } catch (error) {
    note.textContent = error.message;
    note.className = "hint warn";
    if (!nameTouched) $("source-name").value = "";
    return null;
  }
  // The checkout root is the library even when its ip/ folder was chosen.
  $("source-path").value = info.path;
  if (!nameTouched || !$("source-name").value.trim()) {
    $("source-name").value = info.name;
    nameTouched = false;
  }
  const found = Object.entries(info.assets)
    .map(([kind, count]) => count + " " + assetNoun(kind, count))
    .join(", ");
  note.textContent = info.connected_as
    ? "Already connected as " + info.connected_as + "."
    : !info.layout
      ? "No ip/ or skills/, agents/, … folders here. Choose the checkout root."
      : "Found " +
        (found || "no assets yet") +
        (info.layout === "ip" ? " under ip/." : " at the folder root.");
  note.className = "hint" + (info.connected_as || !info.layout ? " warn" : "");
  // Connect only a folder that is a library and not connected yet.
  $("connect-source").disabled = !!info.connected_as || !info.layout;
  return info;
}
function assetNoun(kind, count) {
  const nouns = {
    skills: ["skill", "skills"],
    agents: ["agent", "agents"],
    prompts: ["prompt", "prompts"],
    steering: ["steering file", "steering files"],
    powers: ["Kiro power", "Kiro powers"],
    copilot: ["Copilot instruction file", "Copilot instruction files"],
    "copilot-instructions": ["Copilot instruction", "Copilot instructions"],
    "copilot-prompts": ["Copilot prompt", "Copilot prompts"],
  };
  return (nouns[kind] || [kind, kind])[count === 1 ? 0 : 1];
}
function resetAddLibrary() {
  for (const id of ["source-path", "source-name"]) $(id).value = "";
  $("source-layout").value = "";
  $("source-detected").textContent = "";
  $("connect-source").disabled = true;
  nameTouched = false;
}
$("source-name").oninput = () => (nameTouched = true);
// Not routed through task(): a busy flag here would swallow the Connect click.
let describeTimer = null;
$("source-path").oninput = () => {
  $("connect-source").disabled = !$("source-path").value.trim();
  clearTimeout(describeTimer);
  describeTimer = setTimeout(describeLibrary, 400);
};
$("source-path").onchange = () => describeLibrary();
$("add-source").onsubmit = (event) => {
  event.preventDefault();
  task(async () => {
    const info = await describeLibrary();
    if (!info?.layout)
      throw new Error(
        "Choose a library folder (with ip/ or skills/, agents/, …) first.",
      );
    if (info.connected_as)
      throw new Error(
        "This folder is already connected as " + info.connected_as + ".",
      );
    const name = $("source-name").value.trim() || info.name;
    // Every project inherits libraries plugged in for the user.
    const scope =
      $("source-scope").value === "user" ? homePath() : state.target;
    await api("source", {
      name,
      path: info.path,
      layout: $("source-layout").value,
      target: scope,
    });
    resetAddLibrary();
    $("add-library").open = false;
    view = "patch";
    await refresh();
    message("Library " + name + " added. Click a circle to install an asset.");
  });
};
$("source").onchange = () =>
  task(async () => {
    invalidate();
    await loadRepository();
    render();
  });
$("search").oninput = () => (view === "patch" ? renderMatrix() : render());
$("save").onclick = () => task(saveSelection);
$("preview").onclick = () =>
  task(async () => {
    await saveSelection(true);
    await preview({ direction: view });
  });
$("apply").onclick = () =>
  task(async () => {
    undo = null;
    if (pendingBatch) {
      const batch = pendingBatch;
      let synced = 0,
        skipped = 0;
      for (const { plan } of batch) {
        if (!plan.changes.length) continue;
        if (!plan.can_apply) {
          skipped++;
          continue;
        }
        await api("apply", { fingerprint: plan.fingerprint });
        synced++;
      }
      await refresh(true);
      message(
        "Applied changes in " +
          synced +
          (synced === 1 ? " place" : " places") +
          (skipped ? "; " + skipped + " skipped because of conflicts." : "."),
      );
      return;
    }
    if (!pending) return;
    const result = await api("apply", { fingerprint: pending.fingerprint });
    const resume = resumeSpec;
    resumeSpec = null;
    await refresh();
    message(
      result.output
        ? "Kiro: " + result.output
        : "Applied " +
            result.changes +
            (result.changes === 1 ? " asset change." : " asset changes.") +
            (resume ? " Previewing the original changes again." : ""),
    );
    if (resume) await preview(resume);
  });
$("dismiss").onclick = () => task(cancelReview);
$("place").onchange = () =>
  task(async () => {
    await cancelReview();
    await switchPlace($("place").value);
  });
$("preview-all").onclick = () => task(() => previewMany(projects));
$("add-project").onclick = () => openFolderPicker("project-path");
document.querySelectorAll(".faceplate-nav button").forEach(
  (button) =>
    (button.onclick = () =>
      task(async () => {
        await cancelReview();
        view = button.dataset.tab;
        $("search").value = "";
        invalidate();
        render();
      })),
);
function selectable() {
  return state.candidates.filter((r) => r.status === "local");
}
function isSaved(row) {
  const saved = state.config[view][key(row)];
  return !!saved && (view !== "outbound" || saved.path === row.path);
}
function unsaved() {
  return changedDrafts().length;
}
function selectionCount() {
  if (!state || view !== "outbound") return;
  const rows = selectable();
  const selected = rows.filter((r) => {
    const draft = drafts[draftKey(r)];
    return draft ? draft.enabled : isSaved(r);
  }).length;
  const changes = unsaved();
  $("selection-actions").classList.toggle("dirty", changes > 0);
  $("pending-count").textContent =
    selected +
    " selected · " +
    (changes
      ? changes + (changes === 1 ? " unsaved change" : " unsaved changes")
      : "no unsaved changes");
}
function repoSource() {
  return $("repo-scope").value === "project" ? "" : $("source").value;
}
async function loadRepository() {
  gitPending = null;
  $("git-review").hidden = true;
  const area = $("repo-status");
  area.replaceChildren();
  repository = null;
  $("git-pull").disabled = true;
  $("git-push").disabled = true;
  if ($("repo-scope").value === "library" && !$("source").value) {
    area.append(el("p", "Connect a library to see its repository."));
    return;
  }
  try {
    repository = await api(
      "repository?source=" + encodeURIComponent(repoSource()),
    );
  } catch (error) {
    area.append(el("p", error.message));
    return;
  }
  area.append(el("p", repository.path, "asset-path"));
  if (!repository.is_repo) {
    area.append(el("p", "This folder is not a Git repository."));
    return;
  }
  area.append(
    el("strong", repository.branch || "Detached HEAD"),
    el(
      "p",
      repository.clean
        ? "Working tree clean"
        : repository.changes.length +
            (repository.changes.length === 1
              ? " changed file"
              : " changed files"),
    ),
    el(
      "p",
      repository.ahead === null
        ? "No upstream tracking branch"
        : repository.ahead + " ahead · " + repository.behind + " behind",
    ),
  );
  if (repository.remote_url)
    area.append(el("p", repository.remote_url, "asset-path"));
  const list = el("ul", undefined, "git-files");
  for (const row of repository.changes)
    list.append(el("li", changeLabel(row.status) + " · " + row.path));
  area.append(list);
  const ready = !!(
    repository.branch &&
    repository.head &&
    repository.remote_url
  );
  $("git-pull").disabled = !ready || !repository.clean;
  $("git-push").disabled = !ready;
}
$("repo-scope").onchange = () => task(loadRepository);
for (const action of ["pull", "push"])
  $("git-" + action).onclick = () =>
    task(async () => {
      await loadRepository();
      if (!repository?.is_repo) return;
      gitPending = {
        action,
        source: repoSource(),
        fingerprint: repository.fingerprint,
      };
      $("git-summary").textContent =
        action === "push"
          ? "Push committed HEAD to " +
            repository.push_url +
            " → " +
            repository.remote_ref +
            "? Uncommitted files are excluded."
          : "Pull from " +
            repository.remote_url +
            " → " +
            repository.remote_ref +
            " into " +
            repository.path +
            "? Linked assets update immediately.";
      $("git-confirm").textContent = "Confirm " + action;
      $("git-review").hidden = false;
    });
$("git-cancel").onclick = () => {
  gitPending = null;
  $("git-review").hidden = true;
};
$("git-confirm").onclick = () =>
  task(async () => {
    if (!gitPending) return;
    const request = gitPending;
    gitPending = null;
    $("git-review").hidden = true;
    message("Running " + request.action + "…");
    const result = await api("git", request);
    await refresh();
    message(result.output);
  });
let cloneNameTouched = false;
$("clone-name").oninput = () => (cloneNameTouched = true);
$("clone-url").oninput = () => {
  if (cloneNameTouched) return;
  const last = $("clone-url")
    .value.trim()
    .replace(/\/+$/, "")
    .split(/[/:]/)
    .pop()
    .replace(/\.git$/, "");
  $("clone-name").value = last ? suggestName(last) : "";
};
$("clone-source").onsubmit = (event) => {
  event.preventDefault();
  task(async () => {
    message("Cloning repository…");
    const name = $("clone-name").value;
    await api("clone", {
      name,
      url: $("clone-url").value,
      path: $("clone-path").value,
      layout: $("clone-layout").value,
      target: homePath(),
    });
    view = "patch";
    await refresh();
    message(
      "Cloned and added for every project. Click a circle to install an asset.",
    );
  });
};

// ---------------------------------------------------------------- patch bay
const KIND_LABELS = {
  skills: "Skill",
  agents: "Agent",
  prompts: "Prompt",
  steering: "Steering",
  powers: "Kiro power",
  copilot: "Copilot",
  "copilot-instructions": "Copilot",
  "copilot-prompts": "Copilot",
};
const AGENTS = [
  ["kiro", "Kiro"],
  ["claude", "Claude"],
  ["codex", "Codex"],
  ["copilot", "Copilot"],
];
function patchKey(place, key) {
  return place + "\n" + key;
}
function patchCount() {
  return patches.size;
}
function visibleColumns() {
  const folders = bay.columns.filter((col) => col.kind === "folder");
  // The nearest folder and any folder that already passes patches down stay visible.
  const used = new Set(
    bay.rows.flatMap((row) =>
      folders.filter((col) => row.cells[col.path]?.own).map((col) => col.path),
    ),
  );
  return bay.columns.filter(
    (col) =>
      col.kind !== "folder" ||
      showAllFolders ||
      col.path === folders[0]?.path ||
      used.has(col.path) ||
      [...patches.keys()].some((k) => k.startsWith(col.path + "\n")),
  );
}
function folderPatch(row, col) {
  // A pending change on a folder above this place reaches this place too.
  const index = bay.columns.findIndex((c) => c.path === col.path);
  for (const above of bay.columns.slice(index + 1)) {
    if (above.kind !== "folder") continue;
    const pending = patches.get(patchKey(above.path, row.key));
    if (pending !== undefined) return { from: above.path, on: pending };
  }
  return null;
}
function jackState(row, col) {
  const cell = row.cells[col.path] || {};
  const pending = patches.get(patchKey(col.path, row.key));
  let on = pending === undefined ? cell.on : pending;
  const user = bay.columns.find((c) => c.kind === "user");
  const userPending = user && patches.get(patchKey(user.path, row.key));
  const coveredByUser =
    col.kind !== "user" &&
    user &&
    (userPending === undefined ? row.cells[user.path]?.on : userPending);
  if (row.kind === "powers")
    return col.kind === "user"
      ? {
          kind: "power",
          label:
            {
              "not-installed": "not installed",
              current: "installed",
              differs: "update available",
            }[cell.power] || "unavailable",
          lit: cell.power === "current",
          stale: cell.power === "differs",
        }
      : { kind: "na", label: "Kiro powers install for your user" };
  if (row.status !== "available")
    return { kind: "na", label: row.error || "invalid" };
  let from = cell.from;
  let flowing = false;
  const upstream =
    col.kind !== "user" && pending === undefined && !cell.own
      ? folderPatch(row, col)
      : null;
  if (upstream && (upstream.on || cell.from === upstream.from)) {
    on = upstream.on;
    from = upstream.from;
    flowing = true;
  }
  // A pending change at this place stays visible even when the user install covers it.
  if (coveredByUser && !on && pending === undefined)
    return { kind: "covered", label: "installed for your user" };
  const folderName = from ? from.split("/").pop() : "";
  return {
    kind: "jack",
    on,
    pending: pending !== undefined,
    flowing,
    inherited: !!from && on && pending === undefined,
    from,
    linked: cell.linked,
    blocked: on && ["blocked", "contributed", "changed"].includes(cell.dest),
    dest: cell.dest,
    label: flowing
      ? on
        ? "will come from " + folderName
        : "will be removed with " + folderName
      : pending !== undefined
        ? on
          ? "will be installed"
          : "will be removed"
        : from && on
          ? "from " + folderName
          : on
            ? col.kind === "folder"
              ? "shared with projects inside"
              : cell.linked
                ? "installed"
                : "selected, not linked yet"
            : "not installed",
  };
}
function toggleJack(row, col) {
  if (undo)
    // The review is open on saved choices; put them back first, then edit.
    return task(async () => {
      await cancelReview();
      toggleJack(row, col);
    });
  const s = jackState(row, col);
  if (s.kind === "power")
    return task(() =>
      preview({
        power: true,
        action: s.lit ? "uninstall" : "install",
        source: row.source,
        name: row.name,
        target: homePath(),
      }),
    );
  if (s.kind === "covered") {
    message(
      row.name +
        " is already installed for your user, so every project has it.",
    );
    return;
  }
  if (s.kind !== "jack") return;
  const cell = row.cells[col.path] || {};
  const k = patchKey(col.path, row.key);
  const next = !s.on;
  if (next === !!cell.on) patches.delete(k);
  else patches.set(k, next);
  invalidate();
  renderMatrix();
}
function renderKindFilter(kinds) {
  const box = $("kind-filter");
  box.replaceChildren();
  for (const [value, label] of [
    ["", "All"],
    ...kinds.map((k) => [k, KIND_LABELS[k] + "s"]),
  ]) {
    const chip = el(
      "button",
      label,
      "chip" + (kindFilter === value ? " active" : ""),
    );
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(kindFilter === value));
    chip.onclick = () => {
      kindFilter = value;
      renderMatrix();
    };
    box.append(chip);
  }
}
function placeName(col) {
  return col?.kind === "user" ? "your user" : col?.label || "";
}
function columnHead(col) {
  const head = el("div", undefined, "col-head col-" + col.kind);
  head.setAttribute("role", "columnheader");
  const title = el("strong", col.kind === "user" ? "Your user" : col.label);
  title.title = col.path;
  head.append(
    title,
    el(
      "span",
      col.kind === "project"
        ? "this project"
        : col.kind === "folder"
          ? "folder · shared below"
          : "every project",
      "col-kind",
    ),
  );
  const agents = el("div", undefined, "agent-chips");
  for (const [tool, name] of AGENTS) {
    if (tool === "copilot" && col.kind === "user") continue;
    const chip = el(
      "button",
      name,
      "agent" + (col.tools.includes(tool) ? " on" : ""),
    );
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(col.tools.includes(tool)));
    chip.title =
      (col.tools.includes(tool) ? "Stop linking into " : "Also link into ") +
      name +
      " here";
    chip.onclick = () =>
      task(async () => {
        await cancelReview();
        const tools = col.tools.includes(tool)
          ? col.tools.filter((t) => t !== tool)
          : [...col.tools, tool];
        if (!tools.length)
          throw new Error("Keep at least one agent for this place.");
        await api("tools", { tools, target: col.path });
        bay = await api("matrix");
        renderMatrix();
        message(
          name +
            (tools.includes(tool) ? " added" : " removed") +
            " for " +
            placeName(col) +
            ". Review and apply to update its links.",
        );
        patchesTouched(col.path);
      });
    agents.append(chip);
  }
  head.append(agents);
  return head;
}
function patchesTouched(place) {
  // A tool change needs a sync even without jack changes; remember the place.
  touchedPlaces.add(place);
  renderRouting();
}
const touchedPlaces = new Set();
// Cables already on screen; only new ones draw in.
let drawnCables = new Set();
function renderMatrix() {
  const grid = $("matrix");
  grid.replaceChildren();
  const cols = visibleColumns();
  grid.style.setProperty("--cols", cols.length);
  const hidden =
    bay.columns.filter((c) => c.kind === "folder").length -
    cols.filter((c) => c.kind === "folder").length;
  const kinds = [...new Set(bay.rows.map((r) => r.kind))];
  renderKindFilter(kinds);
  // Header row
  const head = el("div", undefined, "mx-row mx-head");
  head.setAttribute("role", "row");
  const corner = el("div", undefined, "mx-asset corner");
  corner.append(el("span", "Asset", "engraved"));
  if (hidden || showAllFolders) {
    const more = el(
      "button",
      showAllFolders
        ? "Fewer folders"
        : "+" + hidden + (hidden === 1 ? " folder above" : " folders above"),
      "quiet small",
    );
    more.type = "button";
    more.onclick = () => {
      showAllFolders = !showAllFolders;
      renderMatrix();
    };
    corner.append(more);
  }
  head.append(corner, ...cols.map(columnHead));
  grid.append(head);
  const query = $("search").value.toLowerCase();
  const rows = bay.rows.filter(
    (r) =>
      (!kindFilter || r.kind === kindFilter) &&
      (r.name + " " + r.description + " " + r.source)
        .toLowerCase()
        .includes(query),
  );
  $("count").textContent =
    rows.length + (rows.length === 1 ? " asset" : " assets");
  const cables = new Set();
  let library = null;
  for (const row of rows) {
    if (row.source !== library) {
      library = row.source;
      const section = el("div", undefined, "mx-section");
      section.setAttribute("role", "row");
      section.append(
        el("span", library, "engraved"),
        el(
          "span",
          bay.rows.filter((r) => r.source === library).length + " assets",
          "col-kind",
        ),
      );
      grid.append(section);
    }
    const line = el("div", undefined, "mx-row");
    line.setAttribute("role", "row");
    const asset = el("div", undefined, "mx-asset");
    asset.setAttribute("role", "rowheader");
    const title = el("div", undefined, "asset-name");
    title.append(
      el("span", row.name),
      el("span", KIND_LABELS[row.kind] || row.kind, "kind-tag"),
    );
    asset.append(title);
    const desc = el("p", row.description || row.error || "", "asset-desc");
    asset.append(desc);
    asset.tabIndex = 0;
    asset.onclick = () => asset.classList.toggle("open");
    asset.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        asset.classList.toggle("open");
      }
    };
    line.append(asset);
    cols.forEach((col, index) => {
      const s = jackState(row, col);
      const cell = el("div", undefined, "mx-cell");
      cell.setAttribute("role", "gridcell");
      if (s.kind === "na") {
        cell.append(el("span", undefined, "jack-none"));
        cell.title = s.label;
      } else {
        const jack = el("button", undefined, "jack");
        jack.type = "button";
        const classes = [s.kind];
        if (s.kind === "jack") {
          if (s.on) classes.push("on");
          if (s.pending || s.flowing) classes.push("pending");
          if (s.inherited || (s.flowing && s.on)) classes.push("inherited");
          if (s.blocked) classes.push("blocked");
          if (
            s.on &&
            !s.pending &&
            !s.flowing &&
            !s.linked &&
            col.kind !== "folder"
          )
            classes.push("unsynced");
          jack.setAttribute("aria-pressed", String(!!s.on));
        }
        if (s.kind === "power" && s.lit) classes.push("on");
        if (s.kind === "power" && s.stale) classes.push("pending");
        jack.className = "jack " + classes.join(" ");
        jack.setAttribute(
          "aria-label",
          row.name +
            " · " +
            (col.kind === "user" ? "your user" : col.label) +
            ": " +
            s.label,
        );
        const at = (row.cells[col.path]?.paths || []).join(", ");
        jack.title = !s.blocked
          ? s.label
          : s.dest === "changed"
            ? "The managed link at " +
              at +
              " changed. Review this place for the fix."
            : "A local file is in the way at " +
              at +
              ". Review this place to archive it.";
        jack.onclick = () => toggleJack(row, col);
        cell.append(jack);
        // Cable from the folder that shares the asset down to this place.
        if (s.kind === "jack" && s.on && s.from && (s.inherited || s.flowing)) {
          const from = cols.findIndex((c) => c.path === s.from);
          if (from > index) {
            const id = row.key + "\n" + col.path + "\n" + s.from;
            const cable = el(
              "span",
              undefined,
              "cable" +
                (s.flowing ? " flowing" : "") +
                (drawnCables.has(id) ? "" : " fresh"),
            );
            cable.style.setProperty("--span", from - index);
            cell.append(cable);
            cables.add(id);
          }
        }
      }
      line.append(cell);
    });
    grid.append(line);
  }
  drawnCables = cables;
  if (!rows.length)
    grid.append(el("p", "No asset matches this filter.", "mx-empty"));
  renderRouting();
}
function routingPhrase(place, adds, removes) {
  const name = placeName(bay.columns.find((c) => c.path === place)) || place;
  const what = (keys) =>
    keys.length === 1
      ? keys[0].split("/").slice(2).join("/")
      : keys.length + " assets";
  const parts = [];
  if (adds.length) parts.push("add " + what(adds) + " to " + name);
  if (removes.length) parts.push("remove " + what(removes) + " from " + name);
  if (!parts.length) parts.push("update agent links for " + name);
  return parts.join(", ");
}
function renderRouting() {
  const byPlace = new Map();
  for (const [k, on] of patches) {
    const [place, key] = k.split("\n");
    const entry = byPlace.get(place) || { add: [], remove: [] };
    entry[on ? "add" : "remove"].push(key);
    byPlace.set(place, entry);
  }
  for (const place of touchedPlaces)
    if (!byPlace.has(place)) byPlace.set(place, { add: [], remove: [] });
  $("routing").hidden = view !== "patch" || !byPlace.size;
  const phrases = [...byPlace].map(([place, n]) =>
    routingPhrase(place, n.add, n.remove),
  );
  const text =
    phrases.length > 2
      ? patches.size +
        (patches.size === 1 ? " change" : " changes") +
        " in " +
        phrases.length +
        " places"
      : phrases.join("; ");
  $("routing-summary").textContent =
    text.charAt(0).toUpperCase() + text.slice(1);
}
function setReviewHint(saved) {
  $("review-hint").textContent = saved
    ? "Your choices are saved; the links below are created only when you apply. Cancel puts your choices back."
    : "Nothing changes on disk until you apply.";
  $("dismiss").textContent = saved ? "Cancel" : "Close";
}
async function cancelReview() {
  const saved = undo;
  undo = null;
  invalidate();
  if (!saved) return;
  for (const step of saved.steps) await api("restore-selections", step);
  patches = saved.patches;
  for (const place of saved.touched) touchedPlaces.add(place);
  bay = await api("matrix");
  renderMatrix();
}
async function saveAndReview() {
  // 1. Save each place's choices as selections, grouped by library.
  const groups = new Map();
  for (const [k, on] of patches) {
    const [place, key] = k.split("\n");
    const [source, kind, ...rest] = key.split("/");
    const id = place + "\n" + source;
    if (!groups.has(id)) groups.set(id, { place, source, selections: [] });
    groups.get(id).selections.push({ kind, name: rest.join("/"), enabled: on });
  }
  const saved = {
    steps: [],
    patches: new Map(patches),
    touched: [...touchedPlaces],
  };
  try {
    for (const group of groups.values()) {
      const result = await api("selections", {
        target: group.place,
        source: group.source,
        direction: "inbound",
        selections: group.selections,
      });
      saved.steps.push({
        target: group.place,
        direction: "inbound",
        previous: result.previous,
      });
    }
  } catch (error) {
    // Put back whatever part of the batch was saved before the failure.
    for (const step of saved.steps) await api("restore-selections", step);
    throw error;
  }
  // 2. Preview every place whose links can change.
  const changed = new Set(
    [...groups.values()].map((g) => g.place).concat([...touchedPlaces]),
  );
  const user = bay.columns.find((c) => c.kind === "user");
  const project = bay.columns.find((c) => c.kind === "project");
  const targets = [];
  if (user && changed.has(user.path))
    targets.push({ path: user.path, label: "Your user" });
  const folders = [...changed].filter(
    (p) => bay.columns.find((c) => c.path === p)?.kind === "folder",
  );
  if (project && (changed.has(project.path) || folders.length))
    targets.push({ path: project.path, label: project.label });
  for (const folder of folders)
    for (const other of projects)
      if (
        other.path.startsWith(folder + "/") &&
        !targets.some((t) => t.path === other.path)
      )
        targets.push({ path: other.path, label: other.label });
  patches = new Map();
  touchedPlaces.clear();
  bay = await api("matrix");
  renderMatrix();
  if (!targets.length) {
    message("Saved. Nothing else to sync here.");
    return;
  }
  undo = saved;
  await previewMany(targets);
}
$("routing-review").onclick = () => task(saveAndReview);
$("routing-clear").onclick = () => {
  patches = new Map();
  touchedPlaces.clear();
  renderMatrix();
};

// ---------------------------------------------------------------- libraries
function renderLibraries() {
  const list = $("library-list");
  list.replaceChildren();
  const sources = Object.entries(state.config.sources);
  if (!sources.length) list.append(el("p", "No library added yet.", "hint"));
  for (const [name, spec] of sources) {
    const row = el("div", undefined, "library-row");
    const count = bay.rows.filter((r) => r.source === name).length;
    row.append(
      el("strong", name),
      el("span", count + (count === 1 ? " asset" : " assets"), "col-kind"),
      el(
        "code",
        shortPath(spec.path || "not bound on this machine"),
        "faceplate-path",
      ),
    );
    if (spec.path)
      row.append(revealButton(spec.path, "Show in " + state.file_manager));
    list.append(row);
  }
}
$("first-choose").onclick = () => {
  view = "libraries";
  render();
  $("add-library").open = true;
  openFolderPicker("source-path");
};
$("first-clone").onclick = () => {
  view = "libraries";
  render();
  $("clone-library").open = true;
  $("clone-url").focus();
};
task(refresh);

function changeLabel(status) {
  if (status === "??") return "New file";
  if (status.includes("U") || ["AA", "DD"].includes(status)) return "Conflict";
  const names = {
    A: "Added",
    M: "Modified",
    D: "Deleted",
    R: "Renamed",
    C: "Copied",
    T: "Type changed",
  };
  const parts = [];
  if (status[0] !== " ")
    parts.push((names[status[0]] || status[0]) + " (staged)");
  if (status[1] !== " ") parts.push(names[status[1]] || status[1]);
  return parts.join(" / ");
}

let pickerField = null,
  pickerListing = null,
  pickerVersion = 0;
async function browseFolders(path) {
  const version = ++pickerVersion;
  pickerListing = null;
  $("folder-select").disabled = true;
  $("folder-up").disabled = true;
  $("folder-error").textContent = "";
  $("folder-list").replaceChildren(el("p", "Loading folders…"));
  try {
    const result = await api(
      "folders?path=" +
        encodeURIComponent(path || "") +
        "&hidden=" +
        ($("folder-hidden").checked ? "1" : "0"),
    );
    if (version !== pickerVersion) return;
    pickerListing = result;
    $("folder-location").value = result.path;
    $("folder-list").replaceChildren();
    $("folder-detected").textContent =
      pickerField === "source-path" && result.library
        ? "This folder is a library (" +
          (result.library === "ip"
            ? "assets under ip/"
            : "assets at its root") +
          "). Use it, or open a subfolder."
        : "";
    for (const folder of result.folders) {
      const button = el("button", folder.name, "folder-entry quiet");
      if (pickerField === "source-path" && folder.library)
        button.append(el("span", "library", "badge"));
      button.type = "button";
      button.onclick = () => browseFolders(folder.path);
      $("folder-list").append(button);
    }
    if (!result.folders.length)
      $("folder-list").append(
        el("p", "No subfolders here. You can select this folder."),
      );
    if (result.truncated)
      $("folder-list").append(
        el(
          "p",
          "Showing the first 1,000 folders. Enter a path to go directly to another folder.",
        ),
      );
    $("folder-select").disabled = false;
    $("folder-up").disabled = result.path === result.parent;
  } catch (error) {
    if (version !== pickerVersion) return;
    $("folder-list").replaceChildren();
    $("folder-error").textContent = error.message;
  }
}
async function openFolderPicker(field) {
  if (busy) return;
  pickerField = field;
  const cloning = field === "clone-path";
  let path = $(field).value.trim();
  $("folder-new").hidden = !cloning;
  const registering = field === "project-path";
  $("folder-picker-title").textContent = cloning
    ? "Choose checkout parent folder"
    : registering
      ? "Choose a project folder"
      : "Choose a library folder";
  $("folder-picker-help").textContent = cloning
    ? "Choose an existing parent and name the new checkout. The folder is created when you clone."
    : registering
      ? "Open the project folder and register it. Nothing is installed until you sync it."
      : "Folders marked library contain ip/ or skills/, agents/, … Open one and choose Use this folder.";
  $("folder-select").textContent = cloning
    ? "Use checkout location"
    : registering
      ? "Register this project"
      : "Use this folder";
  if (cloning) {
    path = path.replace(/\/$/, "");
    const split = path.lastIndexOf("/");
    $("folder-new-name").value =
      split >= 0
        ? path.slice(split + 1)
        : $("clone-name").value.trim() || "shared-tools";
    path = split >= 0 ? path.slice(0, split) || "/" : "";
  }
  // Libraries usually sit beside projects, so start one level above this one.
  if (!path && !cloning && state)
    path = state.target.replace(/\/[^/]+\/?$/, "") || "/";
  $("folder-location").value = path || state?.target || "";
  $("folder-picker").showModal();
  await browseFolders(path);
}
$("browse-source").onclick = () => openFolderPicker("source-path");
$("browse-clone").onclick = () => openFolderPicker("clone-path");
$("folder-location-form").onsubmit = (event) => {
  event.preventDefault();
  browseFolders($("folder-location").value);
};
$("folder-up").onclick = () => {
  if (pickerListing) browseFolders(pickerListing.parent);
};
$("folder-home").onclick = () => browseFolders("~");
$("folder-project").onclick = () => browseFolders("");
$("folder-hidden").onchange = () => browseFolders($("folder-location").value);
$("folder-select").onclick = () => {
  if (!pickerListing) return;
  let path = pickerListing.path;
  if (pickerField === "clone-path") {
    const name = $("folder-new-name").value.trim();
    if (!name || name === "." || name === ".." || /[\/\\\x00]/.test(name)) {
      $("folder-error").textContent = "Enter a folder name without slashes.";
      return;
    }
    path = path.replace(/\/$/, "") + "/" + name;
  }
  $(pickerField).value = path;
  $("folder-picker").close();
  if (pickerField === "source-path") describeLibrary();
  if (pickerField === "project-path")
    task(async () => {
      const result = await api("projects", { path });
      projects = await api("projects");
      render();
      message("Project " + result.name + " registered.");
    });
  if (pickerField === "clone-path" && !$("clone-name").value.trim())
    $("clone-name").value = suggestName(path.split("/").pop());
};
$("folder-cancel").onclick = () => $("folder-picker").close();
$("folder-picker").onclose = () => {
  pickerVersion++;
  pickerListing = null;
};

function showPage(path, focus = true) {
  const about = path === "/about";
  $("workspace-page").hidden = about;
  $("about-page").hidden = !about;
  $("about-link").setAttribute("aria-current", about ? "page" : "false");
  $("workspace-link").setAttribute("aria-current", about ? "false" : "page");
  document.title = about ? "About · Captain Share" : "Captain Share";
  if (focus && about) $("about-title").focus();
  if (focus) window.scrollTo(0, 0);
}
for (const id of ["about-link", "workspace-link"])
  $(id).onclick = (event) => {
    if (
      event.button ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    )
      return;
    event.preventDefault();
    const path = $(id).getAttribute("href");
    if (location.pathname !== path) history.pushState(null, "", path);
    showPage(path);
  };
window.addEventListener("popstate", () => showPage(location.pathname));
showPage(location.pathname, false);

function friendlyDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}
function revealButton(path, label) {
  const button = el(
    "button",
    label || "Show in " + state.file_manager,
    "quiet reveal-button",
  );
  button.type = "button";
  button.title = path;
  button.onclick = () =>
    task(async () => {
      await api("reveal", { path });
      message("Shown in " + state.file_manager + ".");
    });
  return button;
}
$("reveal-workspace").onclick = () =>
  task(async () => {
    await api("reveal", { path: state.target });
    message("Shown in " + state.file_manager + ".");
  });
function renderHistory(query) {
  const details = $("restore-history"),
    list = $("restore-history-list");
  details.hidden = view !== "archive";
  list.replaceChildren();
  if (view !== "archive") return;
  const groups = new Map();
  const history = state.archives.filter(
    (row) => row.restored && row.relative_path.toLowerCase().includes(query),
  );
  for (const row of history) {
    if (!groups.has(row.relative_path)) groups.set(row.relative_path, []);
    groups.get(row.relative_path).push(row);
  }
  $("restore-history-summary").textContent =
    "Restore history · " +
    history.length +
    (history.length === 1 ? " event" : " events");
  if (!groups.size)
    list.append(el("p", "No completed restores match this view.", "hint"));
  for (const [path, events] of groups) {
    events.sort((a, b) => b.restored.localeCompare(a.restored));
    const current = state.installed.find((row) => row.path === path);
    const row = el("div", undefined, "history-entry");
    row.append(
      el("strong", path),
      el(
        "p",
        "Last restored " +
          friendlyDate(events[0].restored) +
          " · " +
          events.length +
          (events.length === 1 ? " restore" : " restores"),
        "hint",
      ),
    );
    if (current) {
      const button = el(
        "button",
        current.status === "linked"
          ? "View installed asset"
          : "Review current link",
        "quiet",
      );
      button.onclick = () => {
        view = "installed";
        $("search").value = path;
        invalidate();
        render();
      };
      row.append(button);
    }
    row.append(revealButton(state.target + "/" + path));
    const more = el("details"),
      timeline = el("ul");
    more.append(el("summary", "Restore dates"));
    for (const event of events)
      timeline.append(el("li", friendlyDate(event.restored)));
    more.append(timeline);
    row.append(more);
    list.append(row);
  }
}
