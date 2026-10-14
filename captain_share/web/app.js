"use strict";
const $ = (id) => document.getElementById(id);
// Theme: follow the system unless the viewer chose light or dark (kept per browser).
const THEMES = ["auto", "light", "dark"];
function storedTheme() {
  try {
    return localStorage.getItem("captain-share-theme") || "auto";
  } catch {
    return "auto";
  }
}
function applyTheme(theme) {
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  const label = { auto: "Auto", light: "Light", dark: "Dark" }[theme];
  $("theme-switch").textContent = label;
  $("theme-switch").setAttribute("aria-label", "Theme: " + label);
}
let theme = storedTheme();
applyTheme(theme);
$("theme-switch").onclick = () => {
  const next = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
  theme = next;
  try {
    localStorage.setItem("captain-share-theme", next);
  } catch {
    // Without storage the choice lasts until reload.
  }
  applyTheme(next);
};
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
  view = "home",
  // Library home data (ADR 005) and the Get in progress.
  home = null,
  getting = null,
  applyNote = null,
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
function message(text, error = false, extra = {}) {
  clearTimeout(messageTimer);
  // While the review sheet is open, speak inside it; otherwise in the dock.
  const box = $("message");
  if (!$("review").hidden) $("review-hint").after(box);
  else if (box.parentElement !== $("dock")) $("dock").prepend(box);
  box.replaceChildren();
  if (text) box.append(error ? "Error: " + text : text);
  if (text && extra.hint) box.append(el("span", extra.hint, "message-hint"));
  if (text && extra.command) box.append(commandLine(extra.command));
  box.className = error ? "error" : "";
  if (text && !error) messageTimer = setTimeout(() => message(""), 6000);
}
// A command the person can run to fix something, with Copy.
function commandLine(command) {
  const line = el("div", undefined, "copy-line");
  const copy = el("button", "Copy", "quiet");
  copy.type = "button";
  copy.onclick = (event) => {
    event.stopPropagation();
    navigator.clipboard
      .writeText(command)
      .then(() => (copy.textContent = "Copied"))
      .catch(() => (copy.textContent = "Select and copy"));
  };
  line.append(el("code", command), copy);
  return line;
}
$("message").title = "Dismiss";
$("message").onclick = () => message("");
let currentTarget = "",
  projects = [],
  pendingBatch = null;
async function api(path, data) {
  // Every request names the folder it acts on (ADR 004); the server checks it.
  if (currentTarget) {
    if (data === undefined) {
      if (!/[?&]target=/.test(path))
        path +=
          (path.includes("?") ? "&" : "?") +
          "target=" +
          encodeURIComponent(currentTarget);
    } else if (!("target" in data)) data = { ...data, target: currentTarget };
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
  if (res.status === 403 && /Open the URL/.test(body.error || ""))
    // The server restarted (new token) or this tab was opened without its link.
    throw new Error(
      "This window is no longer connected; Captain Share was restarted or this link is old. Open the new link from the terminal where captain-share serve runs, or run captain-share serve --open.",
    );
  if (!res.ok) {
    const error = new Error(body.error || "Request failed");
    error.hint = body.hint;
    error.command = body.command;
    throw error;
  }
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
    message(e.message, true, { hint: e.hint, command: e.command });
  } finally {
    busy = false;
  }
}
function invalidate() {
  pending = null;
  pendingBatch = null;
  resumeSpec = null;
  $("review").hidden = true;
  $("review-where").hidden = true;
  bulkAdopt = null;
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
          ? "Global"
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
  $("place").title = state.target;
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
  home = await api("home").catch(() => null);
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
      "Where installed",
      "Click a circle to install an asset there. A folder shares its assets with every project inside it.",
    ],
    projects: [
      "Projects",
      "This project and the ones you registered. Sync after changing assets or pulling a library.",
    ],
    libraries: ["Settings", "Your agents and the libraries this machine uses."],
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
  const sources = Object.keys(state.config.sources).length;
  const where = WHERE_VIEWS.includes(view);
  document
    .querySelectorAll(".faceplate-nav button")
    .forEach((b) =>
      b.classList.toggle("active", b.dataset.tab === (where ? "patch" : view)),
    );
  document
    .querySelectorAll("#subnav button")
    .forEach((b) => b.classList.toggle("active", b.dataset.tab === view));
  // Everyday views show no places; the project picker belongs to Where installed.
  const onAbout = !$("about-page").hidden;
  document.querySelector(".faceplate-place").hidden = !where || onAbout;
  if (onAbout)
    document
      .querySelectorAll(".faceplate-nav button")
      .forEach((b) => b.classList.remove("active"));
  $("subnav").hidden = !where;
  const setup = !sources && view !== "libraries";
  $("setup").hidden = !setup;
  $("home-view").hidden = setup || view !== "home";
  $("share-view").hidden = setup || view !== "share";
  $("bay").hidden = setup || !(where || view === "libraries");
  $("bay").classList.toggle("narrow", view === "libraries");
  $("routing").hidden = true;
  if (setup) return renderSetup();
  if (view === "home") return renderHome();
  if (view === "share") return renderShare();
  $("view-title").textContent = titles[view][0];
  $("view-help").textContent = titles[view][1];
  const isPatch = view === "patch";
  // The grid and its key belong to the Assets view only.
  $("bay-body").hidden = !isPatch;
  $("kind-filter").hidden = !isPatch;
  $("routing").hidden = !isPatch || !patchCount();
  $("libraries-view").hidden = view !== "libraries";
  $("list-view").hidden = isPatch || view === "libraries";
  $("search").hidden = ["projects", "libraries"].includes(view);
  $("source-bar").hidden = view !== "outbound";
  $("selection-actions").hidden = view !== "outbound";
  $("project-actions").hidden = view !== "projects";
  if (isPatch) return renderMatrix();
  if (view === "libraries") {
    $("count").textContent = "";
    renderAgents();
    foundList($("settings-found"), "Found on this machine, not added yet:");
    return renderLibraries();
  }
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
async function preview(spec = { direction: view }, endpoint = "plan") {
  pending = await api(endpoint, spec);
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
    if (change.source)
      div.append(el("div", "From " + shortPath(change.source), "change-from"));
    if (change.edit) div.append(el("div", change.edit, "change-from"));
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
    $("errors").append(
      endpoint === "adopt-plan"
        ? adoptConflict(conflict, spec)
        : conflictRow(conflict, spec),
    ),
  );
  // The generic conflict advice does not fit adopting; its conflicts carry their own choices.
  $("conflict-help").hidden =
    !pending.conflicts.length || endpoint === "adopt-plan";
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
    "Working on " + (here().kind === "user" ? "Global" : here().label) + ".",
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
    view = "home";
    await refresh();
    message("Library " + name + " added. Get what you need from it.");
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
    if (bulkAdopt) {
      // Each copy is planned again just before it is applied, so every step is fresh.
      const specs = bulkAdopt;
      bulkAdopt = null;
      let done = 0;
      for (const spec of specs) {
        const plan = await api("adopt-plan", spec);
        if (!plan.can_apply) {
          await refresh(true);
          shareData = null;
          message(
            "Updated " +
              done +
              " of " +
              specs.length +
              "; " +
              spec.name +
              " changed meanwhile. Review it again.",
            true,
          );
          return;
        }
        await api("apply", { fingerprint: plan.fingerprint });
        done++;
      }
      await refresh(true);
      shareData = null;
      if (view === "share") renderShare();
      message(
        done +
          (done === 1 ? " copy now uses" : " copies now use") +
          " the team version; the old copies are in the Archive.",
      );
      return;
    }
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
      const note = applyNote;
      applyNote = null;
      getting = null;
      await refresh(true);
      message(
        note && synced && !skipped
          ? note
          : "Applied changes in " +
              synced +
              (synced === 1 ? " place" : " places") +
              (skipped
                ? "; " + skipped + " skipped because of conflicts."
                : "."),
      );
      return;
    }
    if (!pending) return;
    const result = await api("apply", { fingerprint: pending.fingerprint });
    const resume = resumeSpec;
    resumeSpec = null;
    const note = applyNote;
    applyNote = null;
    await refresh();
    if (note && !resume) {
      message(note);
      if (view === "share") renderShare();
      return;
    }
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
document.querySelectorAll(".faceplate-nav button, #subnav button").forEach(
  (button) =>
    (button.onclick = () =>
      task(async () => {
        await cancelReview();
        // The sections live on the workspace page; leave About when one is chosen.
        if (!$("about-page").hidden) {
          history.pushState(null, "", "/");
          showPage("/");
        }
        view = button.dataset.tab;
        if (view === "share") shareData = null;
        $("search").value = "";
        invalidate();
        render();
        window.scrollTo(0, 0);
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
  area.append(el("p", shortPath(repository.path), "asset-path"));
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
    view = "home";
    await refresh();
    message("Cloned and added globally. Get what you need from it.");
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
      : { kind: "na", label: "Kiro powers install globally" };
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
    return { kind: "covered", label: "installed globally" };
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
      row.name + " is already installed globally, so every project has it.",
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
  const select = $("kind-filter");
  select.replaceChildren();
  for (const [value, label] of [
    ["", "All kinds"],
    ...kinds.map((k) => [k, KIND_LABELS[k] + "s"]),
  ]) {
    const option = el("option", label);
    option.value = value;
    select.append(option);
  }
  if (!kinds.includes(kindFilter)) kindFilter = "";
  select.value = kindFilter;
  select.hidden = kinds.length < 2;
}
$("kind-filter").onchange = () => {
  kindFilter = $("kind-filter").value;
  renderMatrix();
};
function placeName(col) {
  return col?.kind === "user" ? "Global" : col?.label || "";
}
function columnHead(col) {
  const head = el("div", undefined, "col-head col-" + col.kind);
  head.setAttribute("role", "columnheader");
  const title = el("strong", col.kind === "user" ? "Global" : col.label);
  title.title = col.path;
  head.append(
    title,
    el(
      "span",
      col.kind === "project"
        ? "this project"
        : col.kind === "folder"
          ? "folder · shared below"
          : "all projects",
      "col-kind",
    ),
  );
  const linked = AGENTS.filter(([tool]) => col.tools.includes(tool)).map(
    ([, name]) => name,
  );
  const summary = el(
    "button",
    linked.join(", ") || "No agents",
    "agent-summary" + (editingAgents.has(col.path) ? " open" : ""),
  );
  summary.type = "button";
  summary.title = "Choose which agents get links here";
  summary.setAttribute("aria-expanded", String(editingAgents.has(col.path)));
  summary.onclick = () => {
    if (editingAgents.has(col.path)) editingAgents.delete(col.path);
    else editingAgents.add(col.path);
    renderMatrix();
  };
  head.append(summary);
  if (!editingAgents.has(col.path)) return head;
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
// Column heads whose agent editor is open.
const editingAgents = new Set();
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
            (col.kind === "user" ? "Global" : col.label) +
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
  getting = null;
  applyNote = null;
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
    targets.push({ path: user.path, label: "Global" });
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
    if (spec.path) {
      const sign = el("label", undefined, "check sign-option");
      const box = el("input");
      box.type = "checkbox";
      box.checked = !!spec.sign;
      box.onchange = () =>
        task(async () => {
          await api("library-settings", { source: name, sign: box.checked });
          state.config.sources[name].sign = box.checked;
          message(
            box.checked
              ? "Commits to " + name + " are signed from now on."
              : "Commits to " +
                  name +
                  " are no longer signed by Captain Share.",
          );
        });
      sign.append(
        box,
        " Sign my commits to " +
          name +
          " (for libraries that require signed commits)",
      );
      list.append(sign, starterEditor(name));
    }
  }
}
// Maintainers choose the starter set offered to new teammates (profiles/starter.manifest).
function starterEditor(source) {
  const box = el("details", undefined, "starter-editor");
  const summary = el("summary", "Starter set");
  box.append(summary);
  box.ontoggle = () => {
    if (!box.open || box.dataset.loaded) return;
    box.dataset.loaded = "1";
    task(async () => {
      const current = await api("starter?" + query({ source }));
      const chosen = new Set(current.assets);
      const assets = bay.rows.filter(
        (r) => r.source === source && r.status === "available",
      );
      const body = el("div", undefined, "starter-body");
      body.append(
        el(
          "p",
          current.error
            ? "The current file has a problem: " + current.error
            : "New teammates are offered these in first setup and on Library. Saving commits profiles/starter.manifest in the library.",
          current.error ? "hint warn" : "hint",
        ),
      );
      const picks = el("div", undefined, "starter-picks");
      for (const asset of assets) {
        const key = asset.kind + "/" + asset.name;
        const label = el("label", undefined, "check");
        const input = el("input");
        input.type = "checkbox";
        input.checked = chosen.has(source + "/" + key) || chosen.has(key);
        input.dataset.key = key;
        label.append(
          input,
          " " + asset.name,
          el("span", KIND_LABELS[asset.kind] || asset.kind, "kind-tag"),
        );
        picks.append(label);
      }
      body.append(picks);
      const note = el("input");
      note.value = "Update the starter set";
      const noteLabel = el("label", "Note for the commit");
      noteLabel.append(note);
      body.append(noteLabel);
      const changes = el("div", undefined, "share-changes");
      body.append(changes);
      const reach = reviewChoice(source, () => {
        plan = null;
        save.disabled = true;
        changes.replaceChildren();
      });
      body.append(reach.element);
      const actions = el("div", undefined, "actions");
      const review = el("button", "Review", "quiet");
      review.type = "button";
      const save = el("button", "Save and push");
      save.type = "button";
      save.disabled = true;
      actions.append(review, save);
      body.append(actions);
      let plan = null;
      picks.onchange = () => {
        plan = null;
        save.disabled = true;
        changes.replaceChildren();
      };
      review.onclick = () =>
        task(async () => {
          const keys = [...picks.querySelectorAll("input:checked")].map(
            (input) => input.dataset.key,
          );
          plan = await api("starter-plan", {
            source,
            assets: keys,
            clear: !keys.length,
            mode: reach.mode(),
          });
          const lines = [];
          if (plan.diff) {
            const pre = el("pre", undefined, "diff-text");
            for (const line of plan.diff.split("\n"))
              pre.append(
                el(
                  "span",
                  line + "\n",
                  line.startsWith("+") && !line.startsWith("+++")
                    ? "diff-add"
                    : line.startsWith("-") && !line.startsWith("---")
                      ? "diff-del"
                      : "diff-meta",
                ),
              );
            lines.push(pre);
          }
          for (const conflict of plan.conflicts)
            lines.push(el("p", conflict.message, "hint warn"));
          if (!plan.changes.length)
            lines.push(
              el("p", "The starter set is already like this.", "hint"),
            );
          changes.replaceChildren(...lines);
          save.disabled = !plan.can_apply || !plan.changes.length;
        });
      save.onclick = () =>
        task(async () => {
          if (!note.value.trim()) throw new Error("Add a short note.");
          const result = await api("starter-apply", {
            fingerprint: plan.fingerprint,
            note: note.value.trim(),
            push: true,
          });
          plan = null;
          save.disabled = true;
          changes.replaceChildren(
            el(
              "p",
              result.waiting_for_review
                ? "Proposed for review."
                : result.pushed
                  ? "Saved and pushed. Teammates see it after Update library."
                  : "Saved in the library, not pushed yet.",
              "hint",
            ),
            ...pushOutcome(result),
          );
          home = await api("home").catch(() => home);
        });
      box.append(body);
    });
  };
  return box;
}
// ---------------------------------------------------------------- library home (ADR 005)
const WHERE_VIEWS = ["patch", "projects", "installed", "archive", "outbound"];
let previewOpen = null,
  showAllAssets = false,
  shareData = null,
  shareOpen = null,
  setupAgents = null;
function ago(iso) {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  if (Number.isNaN(days)) return "";
  if (days < 1) return "today";
  if (days === 1) return "yesterday";
  if (days < 14) return days + " days ago";
  if (days < 60) return Math.round(days / 7) + " weeks ago";
  return Math.round(days / 30) + " months ago";
}
// "12 min ago" for recent checks; days for older ones.
function agoTime(iso) {
  const minutes = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (Number.isNaN(minutes)) return "";
  if (minutes < 1) return "just now";
  if (minutes < 60) return minutes + " min ago";
  if (minutes < 24 * 60) return Math.floor(minutes / 60) + " h ago";
  return ago(iso);
}
function projectName() {
  return (home?.project || "").split("/").pop() || "this project";
}
function getCommand(asset) {
  const taken = home.assets.filter((a) => a.name === asset.name).length > 1;
  return "captain-share get " + (taken ? asset.source + "/" : "") + asset.name;
}
function copyCommand(asset) {
  const command = getCommand(asset);
  navigator.clipboard
    .writeText(command)
    .then(() => message("Copied “" + command + "”. Paste it in chat."))
    .catch(() => message("Send this to a teammate: " + command));
}
function owned(a) {
  if (a.kind === "powers")
    return a.power === "current" || a.power === "differs";
  return a.have.user || a.have.project;
}
function libraryStrip(lib) {
  if (lib.personal) {
    const strip = el("div", undefined, "strip");
    const count = home.assets.filter((a) => a.source === lib.source).length;
    const text = el("p");
    text.append(
      "Your own library · " + count + (count === 1 ? " asset" : " assets"),
      el(
        "small",
        "Only on this machine. Share an asset from Share when it is ready.",
      ),
    );
    strip.append(el("span", undefined, "strip-dot ok"), text);
    return strip;
  }
  const strip = el("div", undefined, "strip");
  const waiting = lib.behind || lib.new || lib.updated;
  strip.append(el("span", undefined, "strip-dot" + (waiting ? "" : " ok")));
  const text = el("p");
  text.append(
    lib.behind
      ? lib.behind +
          (lib.behind === 1 ? " update" : " updates") +
          " waiting for " +
          lib.source
      : lib.new || lib.updated
        ? [
            lib.new ? lib.new + " new" : "",
            lib.updated ? lib.updated + " updated" : "",
          ]
            .filter(Boolean)
            .join(" and ") + " since you last looked"
        : lib.source +
          " is up to date" +
          (lib.checked_at ? " · checked " + agoTime(lib.checked_at) : ""),
    el(
      "small",
      lib.source +
        (lib.checked_at ? " · checked " + agoTime(lib.checked_at) : "") +
        (lib.last_commit_at
          ? " · last change " + ago(lib.last_commit_at)
          : "") +
        (lib.is_repo ? "" : " · not a Git checkout"),
    ),
  );
  strip.append(text);
  if (lib.is_repo) {
    const button = el(
      "button",
      lib.behind
        ? "Update library"
        : lib.checked_fresh
          ? "Check again"
          : "Check for updates",
      lib.behind ? "" : "quiet",
    );
    button.type = "button";
    button.onclick = () =>
      task(async () => {
        if (!lib.behind) {
          const status = await api("fetch", { source: lib.source });
          home = await api("home");
          renderHome();
          message(
            status.behind
              ? status.behind + " updates waiting. Update library to get them."
              : lib.source + " is up to date.",
          );
          return;
        }
        const repo = await api(
          "repository?source=" +
            encodeURIComponent(lib.source) +
            "&target=" +
            encodeURIComponent(home.project),
        );
        await api("git", {
          source: lib.source,
          action: "pull",
          fingerprint: repo.fingerprint,
          target: home.project,
        });
        await refresh();
        message(lib.source + " updated. New and changed assets are marked.");
      });
    strip.append(button);
  }
  return strip;
}
function starterBanner() {
  for (const lib of home.libraries) {
    const missing = (lib.starter || []).filter((key) => {
      const asset = home.assets.find((a) => a.key === key);
      return asset && !owned(asset);
    });
    if (!missing.length) continue;
    const box = el("div", undefined, "strip starter");
    const text = el("p");
    text.append(
      "Start with the " + lib.source + " starter set",
      el(
        "small",
        missing.map((key) => key.split("/").pop()).join(", ") +
          " · picked by the library maintainers",
      ),
    );
    const button = el("button", "Get the starter set");
    button.type = "button";
    button.onclick = () =>
      task(async () => {
        await cancelReview();
        await api("profile", {
          target: home.user,
          source: lib.source,
          profile: "starter",
        });
        applyNote = "Starter set ready globally, in all your projects.";
        await previewMany([{ path: home.user, label: "Global" }]);
      });
    box.append(el("span", undefined, "strip-dot"), text, button);
    return box;
  }
  return null;
}
function assetRow(a) {
  const row = el("div", undefined, "lib-row");
  const name = el("div", undefined, "lib-name");
  name.append(
    el("span", a.name),
    el("span", KIND_LABELS[a.kind] || a.kind, "kind-tag"),
  );
  if (a.new) name.append(el("span", "New", "badge-new"));
  else if (a.updated) name.append(el("span", "Updated", "badge-new"));
  if (a.edited) name.append(el("span", "Edited here", "badge-edit"));
  row.append(name);
  if (a.description || a.error)
    row.append(el("p", a.description || a.error, "lib-desc"));
  const meta = [];
  if (a.shared_by)
    meta.push(
      "Shared by " +
        a.shared_by +
        (a.shared_at ? " · " + ago(a.shared_at) : ""),
    );
  if (home.libraries.length > 1) meta.push(a.source);
  for (const version of a.versions || [])
    meta.push(
      "Your version: " +
        version.name +
        (version.team_changed ? " (team changed since)" : ""),
    );
  if (meta.length) row.append(el("p", meta.join(" · "), "lib-meta"));
  const act = el("div", undefined, "lib-act");
  const have = (text) => {
    const pill = el("span", undefined, "have");
    pill.append(el("i"), text);
    act.append(pill);
  };
  const link = (text, fn, cls = "") => {
    const button = el("button", text, "link " + cls);
    button.type = "button";
    button.onclick = fn;
    act.append(button);
  };
  if (a.status !== "available") {
    act.append(el("span", "Can't be installed", "lib-meta"));
    act.title = a.error || "";
  } else if (a.kind === "powers") {
    if (a.power === "current") {
      have("Global");
      link("Remove", () => task(() => powerAction(a, "uninstall")));
    } else if (a.power === "unavailable" || !a.power) {
      act.append(el("span", "Needs Kiro CLI", "lib-meta"));
    } else {
      const button = el(
        "button",
        a.power === "differs" ? "Update" : "Get",
        a.new ? "" : "quiet",
      );
      button.type = "button";
      button.onclick = () => task(() => powerAction(a, "install"));
      act.append(button);
    }
  } else if (a.have.user) {
    have(a.linked.user ? "Global" : "Global, not linked yet");
    if (!a.linked.user)
      link("Finish", () =>
        task(() => previewMany([{ path: home.user, label: "Global" }])),
      );
    link("Remove", () => task(() => removeAsset(a, home.user)));
  } else if (a.have.project && a.have.from) {
    have("Via folder " + a.have.from.split("/").pop());
  } else if (a.have.project) {
    have("Only in " + projectName());
    link("Remove", () => task(() => removeAsset(a, home.project)));
  } else {
    const button = el("button", "Get", a.new || a.updated ? "" : "quiet");
    button.type = "button";
    button.onclick = () => task(() => getAsset(a, "user"));
    act.append(button);
  }
  if (a.status === "available")
    link("Copy get command", () => copyCommand(a), "copy-get");
  link(previewOpen === a.key ? "Hide preview" : "Preview", () => {
    previewOpen = previewOpen === a.key ? null : a.key;
    renderHome();
  });
  row.append(act);
  if (previewOpen === a.key) {
    row.classList.add("open");
    row.append(assetPreview(a));
  }
  return row;
}
// Read-only look inside an asset, with a way to open its folder.
function assetPreview(a) {
  const panel = el("div", undefined, "share-panel asset-preview");
  panel.append(el("p", "Loading…", "hint"));
  api(
    "asset?" +
      query({
        target: home.project,
        source: a.source,
        kind: a.kind,
        name: a.name,
      }),
  )
    .then((view) => {
      const head = el("div", undefined, "preview-head");
      head.append(
        el(
          "p",
          view.files.length +
            (view.files.length === 1 ? " file" : " files") +
            (view.more_files ? " (+" + view.more_files + " more)" : "") +
            " · " +
            shortPath(view.path),
          "lib-meta",
        ),
        revealButton(view.path, "Show in " + (state.file_manager || "folder")),
      );
      panel.replaceChildren(head);
      if (view.files.length > 1) {
        const list = el("ul", undefined, "preview-files");
        for (const file of view.files)
          list.append(el("li", file.path + " · " + bytes(file.size)));
        panel.append(list);
      }
      if (view.text !== null) {
        panel.append(el("p", view.main, "diff-file"));
        panel.append(el("pre", view.text, "diff-text preview-text"));
        if (view.truncated)
          panel.append(el("p", "Showing the beginning of the file.", "hint"));
      } else if (view.main)
        panel.append(el("p", view.main + " is not a text file.", "hint"));
    })
    .catch((error) =>
      panel.replaceChildren(el("p", error.message, "hint warn")),
    );
  return panel;
}
function bytes(size) {
  return size < 1024
    ? size + " B"
    : size < 1048576
      ? Math.round(size / 1024) + " KB"
      : (size / 1048576).toFixed(1) + " MB";
}
function homeSection(title, rows, limit, id, closed = false) {
  const searching = !!$("home-search").value.trim();
  const shown = limit && rows.length > limit ? rows.slice(0, limit) : rows;
  const items = shown.map(assetRow);
  if (shown.length < rows.length) {
    const more = el("button", "Show all " + rows.length, "link lib-more");
    more.type = "button";
    more.onclick = () => {
      showAllAssets = true;
      renderHome();
    };
    items.push(more);
  }
  return listSection(title + " · " + rows.length, items, id, closed, searching);
}
// A section is an expander; its open or closed state is remembered per browser.
function listSection(title, items, id, closed = false, force = false) {
  const section = el("details", undefined, "lib-section");
  const remembered = prefs("section-" + id);
  section.open = force || (remembered ? remembered === "open" : !closed);
  section.ontoggle = () => {
    if (!force) prefs("section-" + id, section.open ? "open" : "closed");
  };
  section.append(el("summary", title, "lib-heading"));
  const list = el("div", undefined, "lib-list");
  list.append(...items);
  section.append(list);
  return section;
}
function prefs(key, value) {
  try {
    if (value === undefined)
      return localStorage.getItem("captain-share-" + key);
    localStorage.setItem("captain-share-" + key, value);
  } catch {
    // Without storage, sections use their defaults.
  }
  return null;
}
function renderHome() {
  if (!home) {
    $("home-lists").replaceChildren(
      el("p", "The library view needs a newer Captain Share server.", "hint"),
    );
    return;
  }
  const teams = home.libraries.filter((lib) => !lib.personal);
  $("home-title").textContent =
    teams.length === 1 && home.libraries.length === 1
      ? home.libraries[0].source.charAt(0).toUpperCase() +
        home.libraries[0].source.slice(1) +
        " library"
      : "Your libraries";
  $("library-strips").replaceChildren(
    ...home.libraries.map(libraryStrip),
    ...[starterBanner()].filter(Boolean),
  );
  const query = $("home-search").value.toLowerCase();
  const assets = home.assets.filter((a) =>
    (a.name + " " + a.description + " " + a.kind).toLowerCase().includes(query),
  );
  const fresh = assets.filter((a) => a.new || a.updated);
  const mine = assets.filter((a) => !fresh.includes(a) && owned(a));
  const rest = assets.filter((a) => !fresh.includes(a) && !owned(a));
  const lists = [];
  if (fresh.length)
    lists.push(homeSection("New from your team", fresh, 0, "home-new"));
  if (mine.length) lists.push(homeSection("You have", mine, 0, "home-mine"));
  if (rest.length)
    lists.push(
      homeSection(
        mine.length || fresh.length ? "Everything else" : "In the library",
        rest,
        query || showAllAssets ? 0 : 12,
        "home-rest",
        // Once you have assets, the rest of the library waits behind its heading.
        !query && (mine.length > 0 || fresh.length > 0),
      ),
    );
  if (!assets.length)
    lists.push(
      el("p", query ? "Nothing matches." : "This library is empty.", "hint"),
    );
  $("home-lists").replaceChildren(...lists);
  $("home-lists").querySelectorAll(".lib-row").forEach(expandable);
}
$("home-search").oninput = () => renderHome();
async function getAsset(a, where) {
  await cancelReview();
  const target = where === "user" ? home.user : home.project;
  const result = await api("selections", {
    target,
    source: a.source,
    direction: "inbound",
    selections: [{ kind: a.kind, name: a.name }],
  });
  undo = {
    steps: [{ target, direction: "inbound", previous: result.previous }],
    patches: new Map(),
    touched: [],
  };
  getting = a;
  applyNote =
    a.name +
    (where === "user"
      ? " is ready globally, in all your projects."
      : " is ready in " + projectName() + ".") +
    " Start a new agent session to load it.";
  await previewMany([
    {
      path: target,
      label: where === "user" ? "Global" : projectName(),
    },
  ]);
  $("where-project").textContent = "Only in " + projectName();
  document.querySelector('#review-where input[value="' + where + '"]').checked =
    true;
  $("review-where").hidden = false;
}
document.querySelectorAll('#review-where input[name="where"]').forEach(
  (input) =>
    (input.onchange = () => {
      const asset = getting;
      if (asset) task(() => getAsset(asset, input.value));
    }),
);
async function removeAsset(a, target) {
  await cancelReview();
  applyNote = null;
  await preview({ action: "remove", kind: a.kind, name: a.name, target });
}
async function powerAction(a, action) {
  await cancelReview();
  await preview({
    power: true,
    action,
    source: a.source,
    name: a.name,
    target: home.user,
  });
}

// ---------------------------------------------------------------- share
async function renderShare() {
  const box = $("share-lists");
  if (!shareData) {
    box.replaceChildren(el("p", "Looking for skills to share…", "hint"));
    try {
      shareData = await api(
        "share-candidates?target=" + encodeURIComponent(home?.project || ""),
      );
    } catch (error) {
      box.replaceChildren(el("p", error.message, "hint"));
      return;
    }
  }
  const lists = [];
  const project = projectName();
  const fresh = shareData.local.filter((row) => !row.exists_in);
  const copies = shareData.local.filter((row) => row.exists_in);
  if (fresh.length)
    lists.push(
      listSection(
        "In " + project + " · not in a library",
        fresh.map((row) => shareRow(row, "local")),
        "share-fresh",
      ),
    );
  if ((shareData.personal || []).length)
    lists.push(
      listSection(
        "In your own library",
        shareData.personal.map(personalRow),
        "share-personal",
      ),
    );
  const older = copies.filter((row) => row.differs && row.older);
  if (older.length > 1) {
    const bulk = el("div", undefined, "strip");
    const text = el("p");
    text.append(
      older.length + " copies are older team versions",
      el(
        "small",
        "They match what the team library had earlier, so they are not your changes.",
      ),
    );
    const all = el("button", "Update all " + older.length + "…");
    all.type = "button";
    all.onclick = () => task(() => previewAdoptAll(older));
    bulk.append(el("span", undefined, "strip-dot"), text, all);
    lists.push(bulk);
  }
  if (copies.length)
    lists.push(
      listSection(
        "Your copies of team assets",
        copies.map((row) => shareRow(row, "local")),
        "share-copies",
      ),
    );
  if ((shareData.versions || []).length)
    lists.push(
      listSection(
        "Your versions",
        shareData.versions.map(versionRow),
        "share-versions",
      ),
    );
  if (shareData.edited.length)
    lists.push(
      listSection(
        "Library assets you edited",
        shareData.edited.map((row) => shareRow(row, "edited")),
        "share-edited",
      ),
    );
  if (!lists.length)
    lists.push(
      el(
        "p",
        "Nothing to share from " +
          project +
          ". Skills you create in its agent folders appear here.",
        "hint",
      ),
    );
  box.replaceChildren(...lists);
  box.querySelectorAll(".lib-row").forEach(expandable);
}
function shareRow(row, type) {
  if (type === "local" && row.exists_in && row.differs && row.older)
    return olderRow(row);
  if (type === "local" && row.exists_in && row.differs) return extendedRow(row);
  const id = type + "/" + (row.source || "") + "/" + row.kind + "/" + row.name;
  const wrap = el("div", undefined, "lib-row");
  const name = el("div", undefined, "lib-name");
  name.append(
    el("span", row.name),
    el("span", KIND_LABELS[row.kind] || row.kind, "kind-tag"),
  );
  if (type === "edited") name.append(el("span", "Edited", "badge-edit"));
  wrap.append(name);
  if (row.description) wrap.append(el("p", row.description, "lib-desc"));
  wrap.append(
    el(
      "p",
      type === "local"
        ? row.path + (row.exists_in ? " · already in " + row.exists_in : "")
        : row.source +
            " · " +
            row.files.length +
            (row.files.length === 1 ? " file" : " files"),
      "lib-meta",
    ),
  );
  const act = el("div", undefined, "lib-act");
  if (type === "local" && row.exists_in) {
    // Already in a library: swap this copy for the library original.
    const use = el("button", "Use team version…", "quiet");
    use.type = "button";
    use.onclick = () =>
      task(() => adoptAsset({ ...row, source: row.exists_in }, false));
    act.append(use);
    wrap.append(act);
    return wrap;
  }
  const open = el(
    "button",
    type === "local" ? "Share…" : "Send changes…",
    shareOpen === id ? "quiet" : "",
  );
  open.type = "button";
  open.onclick = () => {
    shareOpen = shareOpen === id ? null : id;
    renderShare();
  };
  act.append(open);
  wrap.append(act);
  if (shareOpen === id) wrap.append(sharePanel(row, type));
  return wrap;
}
function sharePanel(row, type) {
  const panel = el("div", undefined, "share-panel");
  // Sharing goes to a team library; your own library is for trying things.
  const personal = new Set(
    (home?.libraries || [])
      .filter((lib) => lib.personal)
      .map((lib) => lib.source),
  );
  const libraries = shareData.libraries.filter((name) => !personal.has(name));
  if (type === "local" && !libraries.length) {
    panel.append(
      el("p", "Add your team library first (Settings).", "hint warn"),
    );
    return panel;
  }
  const note = el("textarea");
  note.rows = 2;
  note.value = type === "local" ? row.description || "" : "Update " + row.name;
  const noteLabel = el("label", "What is it good for? Teammates see this.");
  noteLabel.append(note);
  panel.append(noteLabel);
  let library = null;
  if (type === "local" && libraries.length > 1) {
    library = el("select");
    for (const name of libraries) {
      const option = el("option", name);
      option.value = name;
      library.append(option);
    }
    const label = el("label", "Library");
    label.append(library);
    panel.append(label);
  }
  const pushBox = el("label", undefined, "check");
  const push = el("input");
  push.type = "checkbox";
  push.checked = true;
  pushBox.append(push, " Push to the team now");
  panel.append(pushBox);
  // After sharing, this project links to the library original instead of a copy.
  const adopt = el("input");
  adopt.type = "checkbox";
  adopt.checked = true;
  if (type === "local") {
    const adoptBox = el("label", undefined, "check");
    adoptBox.append(adopt, " Use the team version here afterwards");
    adoptBox.title =
      "Your copy goes to the Archive and is replaced by a link to the library original.";
    panel.append(adoptBox);
  }
  const reach = reviewChoice(row.source || shareData.libraries[0] || "", () =>
    loadPlan(),
  );
  if (type === "local") panel.append(reach.element);
  const changes = el("div", undefined, "share-changes");
  panel.append(changes);
  const actions = el("div", undefined, "actions");
  const go = el("button", type === "local" ? "Share" : "Send changes");
  go.type = "button";
  const cancel = el("button", "Cancel", "quiet");
  cancel.type = "button";
  cancel.onclick = () => {
    shareOpen = null;
    renderShare();
  };
  actions.append(go, cancel);
  panel.append(actions);
  let plan = null;
  const source = () => library?.value || row.source || libraries[0];
  const loadPlan = () =>
    task(async () => {
      changes.replaceChildren(el("p", "Preparing…", "hint"));
      if (type === "edited") {
        changes.replaceChildren(
          ...row.files.map((file) => {
            const line = el("div", undefined, "change");
            line.append(el("strong", "Commit"), " ", el("code", file));
            return line;
          }),
        );
        return;
      }
      plan = await api("share-plan", {
        target: home.project,
        source: source(),
        kind: row.kind,
        name: row.name,
        path: row.path,
        adopt: adopt.checked && reach.mode() === "direct",
        mode: reach.mode(),
      });
      // For review, only a branch commit is made; the library folder is not touched.
      const review = plan.mode === "review";
      const lines = (review ? [] : plan.changes).map((change) => {
        const line = el("div", undefined, "change");
        line.append(
          el("strong", change.op),
          " ",
          el("code", relativeTo(change.path, home.project)),
        );
        return line;
      });
      for (const path of plan.commit_paths || []) {
        const line = el("div", undefined, "change");
        line.append(
          el("strong", review ? "Commit on " + plan.branch : "Commit"),
          " ",
          el("code", path),
        );
        lines.push(line);
      }
      for (const change of plan.adopt_changes || []) {
        const line = el("div", undefined, "change");
        line.append(
          el("strong", change.op, /archive/.test(change.op) ? "op-remove" : ""),
          " ",
          el("code", change.path),
        );
        lines.push(line);
      }
      for (const conflict of plan.conflicts)
        lines.push(el("p", conflict.message, "hint warn"));
      changes.replaceChildren(...lines);
      go.disabled = !plan.can_apply;
    });
  if (library) library.onchange = loadPlan;
  adopt.onchange = loadPlan;
  go.onclick = () =>
    task(async () => {
      if (!note.value.trim())
        throw new Error("Say in a few words what it is good for.");
      const result =
        type === "local"
          ? await api("share-apply", {
              fingerprint: plan.fingerprint,
              note: note.value.trim(),
              push: push.checked,
              adopt: adopt.checked && reach.mode() === "direct",
            })
          : await api("commit-edits", {
              source: row.source,
              kind: row.kind,
              name: row.name,
              note: note.value.trim(),
              push: push.checked,
              fingerprint: (
                await api(
                  "repository?source=" +
                    encodeURIComponent(row.source) +
                    "&target=" +
                    encodeURIComponent(home.project),
                )
              ).fingerprint,
            });
      panel.replaceChildren(shareDone(row, result));
      shareData = null;
      home = await api("home").catch(() => home);
    });
  setTimeout(loadPlan);
  return panel;
}
function shareDone(row, result) {
  const box = el("div", undefined, "share-done");
  box.append(
    el(
      "strong",
      result.waiting_for_review
        ? row.name + " is proposed to the team."
        : result.pushed
          ? row.name + " is shared."
          : row.name + " is committed in the library but not pushed yet.",
    ),
  );
  box.append(...pushOutcome(result));
  if (result.adopted)
    box.append(
      el(
        "p",
        projectName() +
          " now uses the team version; your copy is in the Archive.",
        "hint",
      ),
    );
  else if (result.adopt_error) {
    box.append(
      el("p", "Your copy is still here: " + result.adopt_error, "hint warn"),
    );
    const retry = el("button", "Use the team version…", "quiet");
    retry.type = "button";
    retry.onclick = () => task(() => adoptAsset(row, false));
    box.append(retry);
  }
  if (result.waiting_for_review) {
    box.append(
      el(
        "p",
        "Once it is merged, teammates get it with captain-share get " +
          row.name +
          ".",
        "hint",
      ),
    );
    return box;
  }
  const command = result.get_command || "captain-share get " + row.name;
  box.append(el("p", "Tell your team. Paste this in chat:", "hint"));
  const copy = el("div", undefined, "copy-line");
  const code = el("code", "Try " + row.name + ": " + command);
  const button = el("button", "Copy", "quiet");
  button.type = "button";
  button.onclick = () =>
    navigator.clipboard
      .writeText(code.textContent)
      .then(() => message("Copied. Paste it in chat."))
      .catch(() => message(code.textContent));
  copy.append(code, button);
  box.append(copy);
  return box;
}

async function adoptAsset(row, replace) {
  await cancelReview();
  applyNote =
    projectName() +
    " now uses the team version of " +
    row.name +
    ". Your copy is in the Archive.";
  shareData = null;
  await preview(
    {
      target: home.project,
      source: row.source,
      kind: row.kind,
      name: row.name,
      replace,
    },
    "adopt-plan",
  );
}
function adoptConflict(conflict, spec) {
  const div = el("div", undefined, "conflict");
  div.append(el("p", conflict.message));
  if (conflict.reason === "differs" && !spec.replace) {
    const replace = el("button", "Replace mine (it is archived)", "quiet");
    replace.type = "button";
    replace.onclick = () =>
      task(() => adoptAsset({ ...spec, name: spec.name }, true));
    const keep = el("button", "Keep mine", "link");
    keep.type = "button";
    keep.onclick = () => task(cancelReview);
    div.append(replace, keep);
  }
  return div;
}

// ---------------------------------------------------------------- agents and first setup
function agentPicks(box, selected, onToggle) {
  box.replaceChildren(
    ...AGENTS.map(([tool, name]) => {
      const pick = el(
        "button",
        name,
        "agent-pick" + (selected.includes(tool) ? " on" : ""),
      );
      pick.type = "button";
      pick.setAttribute("aria-pressed", String(selected.includes(tool)));
      pick.onclick = () => onToggle(tool);
      return pick;
    }),
  );
}
function renderAgents() {
  const current = home?.agents || [];
  agentPicks($("user-agents"), current, (tool) =>
    task(async () => {
      const tools = current.includes(tool)
        ? current.filter((t) => t !== tool)
        : [...current, tool];
      if (!tools.length) throw new Error("Keep at least one agent.");
      await api("tools", { tools, target: home.user });
      home = await api("home");
      renderAgents();
      applyNote = "Your agents are updated.";
      await previewMany([{ path: home.user, label: "Global" }]);
    }),
  );
}
function cloneDestination() {
  const last = $("setup-url")
    .value.trim()
    .split(/[/:]/)
    .pop()
    .replace(/\.git$/, "");
  const name = last ? suggestName(last) : "";
  return { name, path: name ? homePath() + "/Libraries/" + name : "" };
}
function renderSetup() {
  if (!$("setup-found").childElementCount)
    foundList(
      $("setup-found"),
      "Already on this machine? Add it with one click:",
    );
  if (!setupAgents) setupAgents = [...(home?.agents || ["kiro"])];
  agentPicks($("setup-agents"), setupAgents, (tool) => {
    setupAgents = setupAgents.includes(tool)
      ? setupAgents.filter((t) => t !== tool)
      : [...setupAgents, tool];
    renderSetup();
  });
  const dest = cloneDestination();
  $("setup-dest").textContent = dest.path
    ? "Cloned to " + shortPath(dest.path) + " with your Git access."
    : "Paste the Git URL your team uses for shared skills.";
}
async function saveSetupAgents() {
  if (!setupAgents.length) throw new Error("Choose at least one agent.");
  await api("tools", { tools: setupAgents, target: homePath() });
}
$("setup-url").oninput = renderSetup;
$("setup-clone").onsubmit = (event) => {
  event.preventDefault();
  task(async () => {
    await saveSetupAgents();
    const dest = cloneDestination();
    message("Cloning " + dest.name + "…");
    await api("clone", {
      name: dest.name,
      url: $("setup-url").value.trim(),
      path: dest.path,
      target: homePath(),
      create_parent: true,
    });
    view = "home";
    await refresh();
    message("Ready. Get what you need, or start with the starter set.");
  });
};
$("setup-folder").onclick = () =>
  task(async () => {
    await saveSetupAgents();
    view = "libraries";
    render();
    $("add-library").open = true;
    $("source-scope").value = "user";
    openFolderPicker("source-path");
  });

// ---------------------------------------------------------------- add from elsewhere (ADR 006)
let importStage = null;
$("import-zip").onclick = () => $("import-file").click();
$("import-folder").onclick = () => openFolderPicker("import-path");
$("import-file").onchange = () =>
  task(async () => {
    const file = $("import-file").files[0];
    $("import-file").value = "";
    if (!file) return;
    if (file.size > 20 * 1024 * 1024)
      throw new Error("That zip is larger than 20 MB.");
    message("Checking " + file.name + "…");
    const res = await fetch(
      "/api/import-upload?name=" +
        encodeURIComponent(file.name) +
        "&target=" +
        encodeURIComponent(currentTarget),
      {
        method: "POST",
        headers: {
          "X-Captain-Token": token,
          "Content-Type": "application/zip",
        },
        body: file,
      },
    );
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || "Upload failed");
    importStage = body;
    message("");
    renderImport();
  });
function importLibraries() {
  return (home?.libraries || []).filter((lib) => lib.is_repo && lib.path);
}
function renderImport() {
  const box = $("import-panel");
  if (!importStage) {
    box.replaceChildren();
    return;
  }
  const panel = el("div", undefined, "share-panel import-panel");
  panel.append(el("strong", "From " + shortPath(importStage.label)));
  const items = [];
  const list = el("div", undefined, "import-items");
  for (const candidate of importStage.candidates) {
    const row = el("div", undefined, "import-item");
    const pick = el("input");
    pick.type = "checkbox";
    pick.checked = !candidate.error;
    pick.disabled = !!candidate.error;
    pick.setAttribute("aria-label", "Install " + candidate.name);
    const name = el("input");
    name.value = candidate.name;
    name.setAttribute("aria-label", "Name");
    let kind = null;
    if ((candidate.kinds || []).length > 1) {
      kind = el("select");
      for (const value of candidate.kinds) {
        const option = el("option", KIND_LABELS[value] || value);
        option.value = value;
        kind.append(option);
      }
      kind.value = candidate.kind;
      kind.setAttribute("aria-label", "Kind");
    }
    const info = el("div", undefined, "import-info");
    info.append(
      el("span", KIND_LABELS[candidate.kind] || candidate.kind, "kind-tag"),
    );
    if (candidate.description || candidate.error)
      info.append(
        el(
          "p",
          candidate.error || candidate.description,
          candidate.error ? "lib-meta warn" : "lib-desc",
        ),
      );
    row.append(pick, name, ...(kind ? [kind] : []), info);
    list.append(row);
    items.push({ candidate, pick, name, kind });
  }
  if (!importStage.candidates.length)
    list.append(
      el(
        "p",
        "No skill, agent, prompt or power found here. A skill is a folder with SKILL.md.",
        "hint warn",
      ),
    );
  panel.append(list);
  // Where the tried skill should work; it lives in your own library either way.
  const modes = el("fieldset", undefined, "where");
  modes.append(el("legend", "Where should it work?"));
  const option = (value, title, text, checked) => {
    const label = el("label", undefined, "where-option");
    const input = el("input");
    input.type = "radio";
    input.name = "install-where";
    input.value = value;
    input.checked = checked;
    const span = el("span");
    span.append(el("strong", title), " " + text);
    label.append(input, span);
    modes.append(label);
    return input;
  };
  const global = option(
    "user",
    "Global",
    "All your projects on this machine.",
    true,
  );
  option(
    "project",
    "Only in " + projectName(),
    "The project this window opened on.",
    false,
  );
  panel.append(modes);
  panel.append(
    el(
      "p",
      "It is kept in your own library (~/Libraries/my-skills, only on this machine). Share it with the team later from Share.",
      "hint",
    ),
  );
  const actions = el("div", undefined, "actions");
  const go = el("button", "Install…");
  go.type = "button";
  const cancel = el("button", "Cancel", "link");
  cancel.type = "button";
  cancel.onclick = () => {
    importStage = null;
    renderImport();
  };
  actions.append(go, cancel);
  panel.append(actions);
  go.onclick = () =>
    task(async () => {
      const chosen = items
        .filter((item) => item.pick.checked)
        .map((item) => ({
          id: item.candidate.id,
          kind: item.kind ? item.kind.value : item.candidate.kind,
          name: item.name.value.trim(),
        }));
      if (!chosen.length) throw new Error("Choose at least one to install.");
      const personal = await api("personal-library", {});
      const plan = await api("import-plan", {
        stage: importStage.stage,
        items: chosen,
        source: personal.source,
      });
      if (!plan.can_apply)
        throw new Error(
          plan.conflicts.map((c) => c.message).join(" ") ||
            "These cannot be installed.",
        );
      await api("import-apply", {
        fingerprint: plan.fingerprint,
        note: "Add " + chosen.map((c) => c.name).join(", "),
        push: false,
      });
      importStage = null;
      renderImport();
      // Link them like any library asset: one review for the chosen place.
      const target = global.checked ? home.user : home.project;
      await cancelReview();
      const result = await api("selections", {
        target,
        source: personal.source,
        direction: "inbound",
        selections: chosen.map((c) => ({ kind: c.kind, name: c.name })),
      });
      undo = {
        steps: [{ target, direction: "inbound", previous: result.previous }],
        patches: new Map(),
        touched: [],
      };
      applyNote =
        chosen.map((c) => c.name).join(", ") +
        (global.checked
          ? " installed globally. Try it in a new agent session."
          : " installed in " +
            projectName() +
            ". Try it in a new agent session.");
      await refresh(true);
      await previewMany([
        {
          path: target,
          label: global.checked ? "Global" : projectName(),
        },
      ]);
    });
  box.replaceChildren(panel);
}

// ---------------------------------------------------------------- libraries on this machine
let found = null,
  finding = null;
async function discover() {
  if (found) return found;
  if (!finding)
    finding = api("discover")
      .then((result) => (found = result))
      .catch(() => (found = { libraries: [] }))
      .finally(() => (finding = null));
  return finding;
}
function foundList(box, intro) {
  box.replaceChildren(
    el("p", "Looking for libraries on this machine…", "hint"),
  );
  discover().then((result) => {
    const rows = result.libraries.filter((lib) => !lib.connected).slice(0, 8);
    if (!rows.length) {
      box.replaceChildren();
      return;
    }
    const list = el("div", undefined, "lib-list");
    for (const lib of rows) {
      const row = el("div", undefined, "lib-row");
      const name = el("div", undefined, "lib-name");
      name.append(el("span", lib.name));
      row.append(
        name,
        el(
          "p",
          Object.entries(lib.counts)
            .map(([kind, n]) => n + " " + assetNoun(kind, n))
            .join(", ") +
            " · " +
            shortPath(lib.path),
          "lib-meta",
        ),
      );
      const act = el("div", undefined, "lib-act");
      const add = el("button", "Add");
      add.type = "button";
      add.onclick = () =>
        task(async () => {
          if (!$("setup").hidden && setupAgents) await saveSetupAgents();
          await api("source", {
            name: lib.name,
            path: lib.path,
            layout: lib.layout,
            target: homePath(),
          });
          found = null;
          view = "home";
          await refresh();
          message(
            "Library " +
              lib.name +
              " added globally. Get what you need from it.",
          );
        });
      act.append(add);
      row.append(act);
      list.append(row);
    }
    box.replaceChildren(el("p", intro, "found-intro"), list);
    if (result.truncated)
      box.append(
        el(
          "p",
          "Searched part of your home folder. Use Add a library folder for others.",
          "hint",
        ),
      );
  });
}

// ---------------------------------------------------------------- extended copies (ADR 007)
function diffView(result) {
  const box = el("div", undefined, "diff");
  if (!result.files.length) {
    box.append(el("p", "No differences.", "hint"));
    return box;
  }
  for (const file of result.files) {
    const head = el("p", undefined, "diff-file");
    head.append(
      el(
        "strong",
        { added: "Added", removed: "Removed", changed: "Changed" }[
          file.status
        ] || file.status,
        file.status === "removed" ? "op-remove" : "",
      ),
      " ",
      el("code", file.path),
    );
    box.append(head);
    if (file.binary) {
      box.append(el("p", "Binary file; contents not shown.", "hint"));
      continue;
    }
    if (!file.diff) continue;
    const pre = el("pre", undefined, "diff-text");
    for (const line of file.diff.split("\n")) {
      const cls =
        line.startsWith("+++") || line.startsWith("---")
          ? "diff-meta"
          : line.startsWith("@@")
            ? "diff-hunk"
            : line.startsWith("+")
              ? "diff-add"
              : line.startsWith("-")
                ? "diff-del"
                : "";
      pre.append(el("span", line + "\n", cls));
    }
    box.append(pre);
  }
  if (result.truncated)
    box.append(el("p", "Showing the first part of the changes.", "hint"));
  return box;
}
function query(params) {
  return Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null)
    .map(([key, value]) => key + "=" + encodeURIComponent(value))
    .join("&");
}
// One open panel per row, keyed by row id and kind of panel.
let versionPanel = null;
function toggleVersionPanel(id) {
  versionPanel = versionPanel === id ? null : id;
  renderShare();
}
function extendedRow(row) {
  // A local copy of a library asset that differs from it: three explicit choices.
  const id = "x/" + row.exists_in + "/" + row.kind + "/" + row.name;
  const wrap = el("div", undefined, "lib-row");
  const name = el("div", undefined, "lib-name");
  name.append(
    el("span", row.name),
    el("span", KIND_LABELS[row.kind] || row.kind, "kind-tag"),
    el("span", "Your copy differs", "badge-edit"),
  );
  wrap.append(name);
  if (row.description) wrap.append(el("p", row.description, "lib-desc"));
  wrap.append(
    el(
      "p",
      row.path + " · the team version is in " + row.exists_in,
      "lib-meta",
    ),
  );
  const act = el("div", undefined, "lib-act");
  const button = (label, panel, cls = "quiet") => {
    const b = el(
      "button",
      label,
      cls + (versionPanel === id + panel ? " on" : ""),
    );
    b.type = "button";
    b.onclick = () => toggleVersionPanel(id + panel);
    act.append(b);
  };
  button("Send my changes…", "/propose");
  button("Keep as my version…", "/keep");
  const use = el("button", "Use team version…", "link");
  use.type = "button";
  use.onclick = () =>
    task(() => adoptAsset({ ...row, source: row.exists_in }, false));
  act.append(use);
  const show = el("button", "Show changes", "link");
  show.type = "button";
  show.onclick = () => toggleVersionPanel(id + "/diff");
  act.append(show);
  wrap.append(act);
  const spec = {
    target: home.project,
    source: row.exists_in,
    kind: row.kind,
    name: row.name,
    path: row.path,
  };
  if (versionPanel === id + "/diff") wrap.append(diffPanel("diff", spec));
  if (versionPanel === id + "/propose") wrap.append(proposePanel(row, spec));
  if (versionPanel === id + "/keep") wrap.append(keepPanel(row, spec));
  return wrap;
}
function diffPanel(endpoint, params) {
  const panel = el("div", undefined, "share-panel");
  panel.append(el("p", "Loading changes…", "hint"));
  task(async () => {
    const result = await api(endpoint + "?" + query(params));
    panel.replaceChildren(diffView(result));
  });
  return panel;
}
function proposePanel(row, spec) {
  const panel = el("div", undefined, "share-panel");
  const note = el("textarea");
  note.rows = 2;
  note.value = "Improve " + row.name;
  const noteLabel = el(
    "label",
    "What did you change, and why? Teammates see this.",
  );
  noteLabel.append(note);
  panel.append(noteLabel);
  const modes = el("fieldset", undefined, "where");
  modes.append(el("legend", "How should it reach the team?"));
  const option = (value, title, text, checked) => {
    const label = el("label", undefined, "where-option");
    const input = el("input");
    input.type = "radio";
    input.name = "propose-mode-" + row.name;
    input.value = value;
    input.checked = checked;
    const span = el("span");
    span.append(el("strong", title), " " + text);
    label.append(input, span);
    modes.append(label);
    return input;
  };
  const direct = option(
    "direct",
    "Update the team version now",
    "Commit and push to the library; this project then links to it.",
    true,
  );
  option(
    "review",
    "Ask for review",
    "Push your version to a new branch and open a pull or merge request. Your library checkout is not changed.",
    false,
  );
  panel.append(modes);
  const changes = el("div", undefined, "share-changes");
  panel.append(changes);
  const actions = el("div", undefined, "actions");
  const go = el("button", "Send my changes");
  go.type = "button";
  go.disabled = true;
  const cancel = el("button", "Cancel", "link");
  cancel.type = "button";
  cancel.onclick = () => toggleVersionPanel(versionPanel);
  actions.append(go, cancel);
  panel.append(actions);
  let plan = null;
  const load = () =>
    task(async () => {
      go.disabled = true;
      changes.replaceChildren(el("p", "Preparing…", "hint"));
      plan = await api("propose-plan", {
        ...spec,
        mode: direct.checked ? "direct" : "review",
      });
      const lines = plan.changes.map((change) => {
        const line = el("div", undefined, "change");
        line.append(
          el("strong", change.op),
          " ",
          el("code", shortPath(change.path)),
        );
        return line;
      });
      for (const path of plan.commit_paths || []) {
        const line = el("div", undefined, "change");
        line.append(
          el("strong", plan.branch ? "Commit on " + plan.branch : "Commit"),
          " ",
          el("code", path),
        );
        lines.push(line);
      }
      for (const conflict of plan.conflicts)
        lines.push(el("p", conflict.message, "hint warn"));
      changes.replaceChildren(...lines);
      go.disabled = !plan.can_apply;
    });
  modes.onchange = load;
  go.onclick = () =>
    task(async () => {
      if (!note.value.trim())
        throw new Error("Say in a few words what you changed.");
      const result = await api("propose-apply", {
        fingerprint: plan.fingerprint,
        note: note.value.trim(),
        push: true,
      });
      const done = el("div", undefined, "share-done");
      if (result.branch) {
        done.append(
          el(
            "strong",
            result.pushed
              ? "Your version is on branch " + result.branch + "."
              : "Your version is committed on branch " +
                  result.branch +
                  " but not pushed.",
          ),
        );
        if (result.review_url) {
          const link = el("a", "Open the review request", "about-tour");
          link.href = result.review_url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          done.append(el("p", "Ask a teammate to review it:", "hint"), link);
        } else if (result.pushed)
          done.append(
            el(
              "p",
              "Open a pull or merge request for that branch in your Git host.",
              "hint",
            ),
          );
        done.append(
          el(
            "p",
            "Your copy stays here until the change is merged; then use the team version.",
            "hint",
          ),
        );
      } else {
        done.append(
          el(
            "strong",
            result.pushed
              ? "The team version now has your changes."
              : "Your changes are committed in the library but not pushed.",
          ),
        );
        if (result.adopted)
          done.append(
            el(
              "p",
              projectName() +
                " now links to the team version; your copy is in the Archive.",
              "hint",
            ),
          );
      }
      if (result.push_error)
        done.append(el("p", "Push failed: " + result.push_error, "hint warn"));
      if (result.adopt_error)
        done.append(
          el(
            "p",
            "Your copy is still here: " + result.adopt_error,
            "hint warn",
          ),
        );
      panel.replaceChildren(done);
      shareData = null;
      home = await api("home").catch(() => home);
    });
  setTimeout(load);
  return panel;
}
function keepPanel(row, spec) {
  const panel = el("div", undefined, "share-panel");
  const base = row.name.replace(/\.(md|json)$/, "");
  const ext = row.name.slice(base.length);
  const input = el("input");
  input.value = base + "-" + suggestName(projectName()) + ext;
  const label = el("label", "Name for your version");
  label.append(input);
  panel.append(label);
  panel.append(
    el(
      "p",
      "Agents then see two different names. Your current copy is archived and a renamed copy is written; the name inside it is updated too.",
      "hint",
    ),
  );
  const teamBox = el("label", undefined, "check");
  const team = el("input");
  team.type = "checkbox";
  team.checked = true;
  teamBox.append(team, " Also use the team version as " + row.name);
  panel.append(teamBox);
  const actions = el("div", undefined, "actions");
  const go = el("button", "Review…");
  go.type = "button";
  go.onclick = () =>
    task(async () => {
      await cancelReview();
      applyNote =
        "Kept as " +
        input.value.trim() +
        ". You will see here when the team version changes.";
      shareData = null;
      versionPanel = null;
      await preview(
        { ...spec, new_name: input.value.trim(), link_team: team.checked },
        "keep-plan",
      );
    });
  const cancel = el("button", "Cancel", "link");
  cancel.type = "button";
  cancel.onclick = () => toggleVersionPanel(versionPanel);
  actions.append(go, cancel);
  panel.append(actions);
  return panel;
}
function versionRow(version) {
  const id = "v/" + version.key;
  const wrap = el("div", undefined, "lib-row");
  const name = el("div", undefined, "lib-name");
  name.append(
    el("span", version.name),
    el("span", KIND_LABELS[version.kind] || version.kind, "kind-tag"),
  );
  if (version.team_changed)
    name.append(el("span", "Team changed", "badge-new"));
  wrap.append(name);
  wrap.append(
    el(
      "p",
      "Your version of " +
        version.of +
        " (" +
        version.source +
        ")" +
        (version.team_changed
          ? " · the team version changed since you made yours"
          : " · up to date with the team version it came from"),
      "lib-meta",
    ),
  );
  const act = el("div", undefined, "lib-act");
  if (version.team_changed) {
    const show = el("button", "Show team changes", "quiet");
    show.type = "button";
    show.onclick = () => toggleVersionPanel(id);
    act.append(show);
  }
  wrap.append(act);
  if (versionPanel === id)
    wrap.append(
      diffPanel("version-diff", { target: home.project, key: version.key }),
    );
  return wrap;
}

// ---------------------------------------------------------------- compact lists
// Compact: one line per item; the expander shows its description and other actions.
function applyDensity() {
  const compact = prefs("density") === "compact";
  document.body.classList.toggle("compact", compact);
  document.querySelectorAll(".density").forEach((button) => {
    button.textContent = compact ? "Detailed view" : "Compact view";
    button.setAttribute("aria-pressed", String(compact));
  });
}
document.querySelectorAll(".density").forEach(
  (button) =>
    (button.onclick = () => {
      prefs(
        "density",
        document.body.classList.contains("compact") ? "detailed" : "compact",
      );
      applyDensity();
    }),
);
applyDensity();
function expandable(row) {
  const name = row.querySelector(".lib-name");
  if (!name || name.querySelector(".row-toggle")) return;
  const toggle = el("button", undefined, "row-toggle");
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", String(row.classList.contains("open")));
  toggle.setAttribute("aria-label", "Show details");
  toggle.onclick = () => {
    const open = row.classList.toggle("open");
    toggle.setAttribute("aria-expanded", String(open));
  };
  name.prepend(toggle);
  // Panels opened from a row keep it expanded.
  if (row.querySelector(".share-panel")) {
    row.classList.add("open");
    toggle.setAttribute("aria-expanded", "true");
  }
}

// ---------------------------------------------------------------- own library → team (ADR 008)
function personalRow(row) {
  const id = "p/" + row.kind + "/" + row.name;
  const wrap = el("div", undefined, "lib-row");
  const name = el("div", undefined, "lib-name");
  name.append(
    el("span", row.name),
    el("span", KIND_LABELS[row.kind] || row.kind, "kind-tag"),
  );
  wrap.append(name);
  if (row.description) wrap.append(el("p", row.description, "lib-desc"));
  wrap.append(
    el(
      "p",
      "In your own library" +
        (row.exists_in ? " · the team also has one with this name" : ""),
      "lib-meta",
    ),
  );
  const act = el("div", undefined, "lib-act");
  const open = el(
    "button",
    "Share with team…",
    versionPanel === id ? "quiet on" : "",
  );
  open.type = "button";
  open.onclick = () => toggleVersionPanel(id);
  act.append(open);
  wrap.append(act);
  if (versionPanel === id) wrap.append(promotePanel(row));
  return wrap;
}
function promotePanel(row) {
  const panel = el("div", undefined, "share-panel");
  const teams = (home?.libraries || []).filter(
    (lib) => !lib.personal && lib.is_repo,
  );
  if (!teams.length) {
    panel.append(
      el("p", "Add your team library first (Settings).", "hint warn"),
    );
    return panel;
  }
  const note = el("textarea");
  note.rows = 2;
  note.value = row.description || "Add " + row.name;
  const noteLabel = el("label", "What is it good for? Teammates see this.");
  noteLabel.append(note);
  panel.append(noteLabel);
  let team = null;
  if (teams.length > 1) {
    team = el("select");
    for (const lib of teams) {
      const option = el("option", lib.source);
      option.value = lib.source;
      team.append(option);
    }
    const label = el("label", "Team library");
    label.append(team);
    panel.append(label);
  }
  const pushBox = el("label", undefined, "check");
  const push = el("input");
  push.type = "checkbox";
  push.checked = true;
  pushBox.append(push, " Push to the team now");
  panel.append(pushBox);
  const reach = reviewChoice(teams[0].source, () => load());
  panel.append(reach.element);
  const changes = el("div", undefined, "share-changes");
  panel.append(changes);
  const actions = el("div", undefined, "actions");
  const go = el("button", "Share with team");
  go.type = "button";
  go.disabled = true;
  const cancel = el("button", "Cancel", "link");
  cancel.type = "button";
  cancel.onclick = () => toggleVersionPanel(versionPanel);
  actions.append(go, cancel);
  panel.append(actions);
  let plan = null;
  const load = () =>
    task(async () => {
      go.disabled = true;
      changes.replaceChildren(el("p", "Preparing…", "hint"));
      plan = await api("promote-plan", {
        kind: row.kind,
        name: row.name,
        team: team?.value || teams[0].source,
        target: home.project,
        mode: reach.mode(),
      });
      const review = plan.mode === "review";
      const lines = (review ? [] : plan.changes).map((change) => {
        const line = el("div", undefined, "change");
        if (change.op === "relink") {
          // Places, in this window's words: Global or the project's name.
          const place =
            change.path === home.user
              ? "Global"
              : (change.path || "").split("/").pop();
          line.append(
            el("strong", "Switch to the team version"),
            " · " + place,
          );
          return line;
        }
        line.append(
          el(
            "strong",
            change.op,
            /archive|remove/.test(change.op) ? "op-remove" : "",
          ),
          " ",
          el("code", shortPath(change.path || "")),
        );
        return line;
      });
      for (const path of plan.commit_paths || []) {
        const line = el("div", undefined, "change");
        line.append(
          el("strong", review ? "Commit on " + plan.branch : "Commit"),
          " ",
          el("code", path),
        );
        lines.push(line);
      }
      if (review)
        lines.push(
          el(
            "p",
            "Your own copy and your links stay as they are until the review is merged.",
            "hint",
          ),
        );
      for (const conflict of plan.conflicts)
        lines.push(el("p", conflict.message, "hint warn"));
      changes.replaceChildren(...lines);
      go.disabled = !plan.can_apply;
    });
  if (team) team.onchange = load;
  go.onclick = () =>
    task(async () => {
      if (!note.value.trim())
        throw new Error("Say in a few words what it is good for.");
      const result = await api("promote-apply", {
        fingerprint: plan.fingerprint,
        note: note.value.trim(),
        push: push.checked,
      });
      const done = el("div", undefined, "share-done");
      done.append(
        el(
          "strong",
          result.waiting_for_review
            ? row.name +
                " is proposed to the team. Your links stay on your own copy until it is merged; then use Share with team again."
            : result.pushed
              ? row.name + " is shared with the team."
              : row.name + " is in the team library but not pushed yet.",
        ),
      );
      done.append(...pushOutcome(result));
      if ((result.relinked || []).length)
        done.append(
          el(
            "p",
            "Your agents now use the team version (" +
              result.relinked
                .map((path) =>
                  path === home.user ? "Global" : path.split("/").pop(),
                )
                .join(", ") +
              ").",
            "hint",
          ),
        );
      for (const problem of (result.errors || []).filter(Boolean))
        done.append(el("p", problem, "hint warn"));
      if (result.waiting_for_review) {
        panel.replaceChildren(done);
        shareData = null;
        return;
      }
      const command = "captain-share get " + row.name;
      const copy = el("div", undefined, "copy-line");
      const code = el("code", "Try " + row.name + ": " + command);
      const button = el("button", "Copy", "quiet");
      button.type = "button";
      button.onclick = () =>
        navigator.clipboard
          .writeText(code.textContent)
          .then(() => message("Copied. Paste it in chat."))
          .catch(() => message(code.textContent));
      copy.append(code, button);
      done.append(el("p", "Tell your team. Paste this in chat:", "hint"), copy);
      panel.replaceChildren(done);
      shareData = null;
      home = await api("home").catch(() => home);
    });
  setTimeout(load);
  return panel;
}

// ---------------------------------------------------------------- team Git hosts (ADR 009)
// "How should it reach the team?" — remembered per library.
function reviewChoice(source, onChange) {
  const box = el("fieldset", undefined, "where compact-where");
  box.append(el("legend", "How should it reach the team?"));
  const remembered = prefs("mode-" + source) || "direct";
  const inputs = {};
  for (const [value, title, text] of [
    ["direct", "Update directly", "Commit and push to the library's branch."],
    [
      "review",
      "Ask for review",
      "Push a new branch and open a pull or merge request. Use this when the main branch is protected.",
    ],
  ]) {
    const label = el("label", undefined, "where-option");
    const input = el("input");
    input.type = "radio";
    input.name = "mode-" + source + "-" + Math.random().toString(36).slice(2);
    input.value = value;
    input.checked = value === remembered;
    input.onchange = () => {
      prefs("mode-" + source, value);
      if (onChange) onChange(value);
    };
    inputs[value] = input;
    const span = el("span");
    span.append(el("strong", title), " " + text);
    label.append(input, span);
    box.append(label);
  }
  return {
    element: box,
    mode: () => (inputs.review.checked ? "review" : "direct"),
  };
}
// What happened after a commit: waiting for review, pushed, or why the push failed.
function pushOutcome(result) {
  const parts = [];
  if (result.waiting_for_review) {
    parts.push(
      el("p", "Waiting for review on branch " + result.branch + ".", "hint"),
    );
    if (result.review_url) {
      const link = el("a", "Open the review request", "about-tour");
      link.href = result.review_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      parts.push(link);
    }
  }
  if (result.push_error) {
    parts.push(el("p", "Push failed: " + result.push_error, "hint warn"));
    if (result.push_error_hint)
      parts.push(el("p", result.push_error_hint, "hint"));
    if (result.push_error_command)
      parts.push(commandLine(result.push_error_command));
  }
  return parts;
}

// ---------------------------------------------------------------- outdated copies
// A local copy equal to an earlier team version: update it, nothing to send.
function olderRow(row) {
  const id = "o/" + row.exists_in + "/" + row.kind + "/" + row.name;
  const wrap = el("div", undefined, "lib-row");
  const name = el("div", undefined, "lib-name");
  name.append(
    el("span", row.name),
    el("span", KIND_LABELS[row.kind] || row.kind, "kind-tag"),
    el("span", "Older team version", "badge-new"),
  );
  wrap.append(name);
  if (row.description) wrap.append(el("p", row.description, "lib-desc"));
  wrap.append(
    el(
      "p",
      row.path +
        " · same as the team version from " +
        ago(row.older.at) +
        "; the team has changed it since",
      "lib-meta",
    ),
  );
  const act = el("div", undefined, "lib-act");
  const update = el("button", "Update to team version…");
  update.type = "button";
  update.onclick = () =>
    task(() => adoptAsset({ ...row, source: row.exists_in }, true));
  const show = el("button", "What changed", "link");
  show.type = "button";
  show.onclick = () => toggleVersionPanel(id);
  act.append(update, show);
  wrap.append(act);
  if (versionPanel === id)
    wrap.append(
      diffPanel("diff", {
        target: home.project,
        source: row.exists_in,
        kind: row.kind,
        name: row.name,
        path: row.path,
      }),
    );
  return wrap;
}
let bulkAdopt = null;
async function previewAdoptAll(rows) {
  await cancelReview();
  const specs = rows.map((row) => ({
    target: home.project,
    source: row.exists_in,
    kind: row.kind,
    name: row.name,
    replace: true,
  }));
  $("review").hidden = false;
  $("changes").replaceChildren();
  $("errors").replaceChildren();
  setReviewHint(false);
  let total = 0,
    blocked = 0;
  for (const spec of specs) {
    const plan = await api("adopt-plan", spec);
    total += plan.changes.length;
    $("changes").append(el("p", spec.name, "group-title"));
    for (const change of plan.changes) {
      const div = el("div", undefined, "change");
      div.append(
        el("strong", change.op, /archive/.test(change.op) ? "op-remove" : ""),
        " ",
        el("code", relativeTo(change.path, home.project)),
      );
      $("changes").append(div);
    }
    for (const conflict of plan.conflicts) {
      blocked++;
      $("errors").append(el("p", spec.name + ": " + conflict.message));
    }
  }
  $("review-count").textContent =
    total +
    (total === 1 ? " change" : " changes") +
    " for " +
    specs.length +
    " copies";
  $("conflict-help").hidden = true;
  bulkAdopt = blocked ? null : specs;
  $("apply").disabled = !!blocked;
  $("review").scrollIntoView({ behavior: "smooth", block: "start" });
}

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
    // Libraries first: they are what people look for in this picker.
    const libraryFirst = ["source-path", "import-path"].includes(pickerField);
    const folders = libraryFirst
      ? [...result.folders].sort((a, b) => !!b.library - !!a.library)
      : result.folders;
    for (const folder of folders) {
      const button = el(
        "button",
        folder.name,
        "folder-entry quiet" +
          (libraryFirst && folder.library ? " is-library" : ""),
      );
      if (libraryFirst && folder.library)
        button.append(el("span", "has skills", "badge"));
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
  const importing = field === "import-path";
  let path = importing ? "~" : $(field).value.trim();
  $("folder-new").hidden = !cloning;
  const registering = field === "project-path";
  $("folder-picker-title").textContent = cloning
    ? "Choose checkout parent folder"
    : importing
      ? "Choose a skill folder"
      : registering
        ? "Choose a project folder"
        : "Choose a library folder";
  $("folder-picker-help").textContent = cloning
    ? "Choose an existing parent and name the new checkout. The folder is created when you clone."
    : importing
      ? "Open a skill folder, or a folder with several skills, and add it. Nothing is copied before you review."
      : registering
        ? "Open the project folder and register it. Nothing is installed until you sync it."
        : "Folders marked library contain ip/ or skills/, agents/, … Open one and choose Use this folder.";
  $("folder-select").textContent = cloning
    ? "Use checkout location"
    : importing
      ? "Add from this folder"
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
$("folder-select").onclick = async () => {
  if (!pickerListing) return;
  // A path typed or pasted into the field counts even without pressing Go.
  const typed = $("folder-location").value.trim().replace(/\/+$/, "");
  if (typed && typed !== pickerListing.path.replace(/\/+$/, "")) {
    await browseFolders(typed);
    if (!pickerListing || $("folder-error").textContent) return;
  }
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
  if (pickerField === "import-path")
    task(async () => {
      importStage = await api("import-folder", { path });
      renderImport();
    });
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
  // About is not a section: no section is current and no place applies there.
  if (about) {
    document
      .querySelectorAll(".faceplate-nav button")
      .forEach((b) => b.classList.remove("active"));
    document.querySelector(".faceplate-place").hidden = true;
  } else if (state) render();
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
