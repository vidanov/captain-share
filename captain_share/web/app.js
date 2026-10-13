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
  catalog = [],
  view = "inbound",
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
async function api(path, data) {
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
  resumeSpec = null;
  $("review").hidden = true;
  $("apply").disabled = true;
}
async function refresh(keepDrafts = false) {
  state = await api("state");
  for (const input of $("tools").querySelectorAll("input"))
    input.checked = state.config.tools.includes(input.value);
  $("target").textContent = state.target;
  $("reveal-workspace").textContent = "Show in " + state.file_manager;
  $("layers").replaceChildren(...state.config.layers.map((p) => el("li", p)));
  if (!state.config.layers.length)
    $("layers").append(
      el("li", "No saved configuration yet. Connect a library to begin."),
    );
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
  if (!Object.keys(state.config.sources).length) $("add-library").open = true;
  await loadCatalog();
  await loadRepository();
  if (!keepDrafts) drafts = {};
  invalidate();
  render();
}
function installable(row) {
  return (
    row.status === "available" &&
    row.destinations?.length > 0 &&
    row.destinations.every((d) => ["absent", "installed"].includes(d.state))
  );
}
async function loadCatalog() {
  catalog = $("source").value
    ? await api("catalog?source=" + encodeURIComponent($("source").value))
    : [];
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
    inbound: [
      "Receive from a library",
      "Choose the tools this project can use.",
    ],
    outbound: [
      "Contribute to a library",
      "Choose local originals to copy into the selected library.",
    ],
    installed: [
      "Installed in this folder",
      "These shortcuts read directly from their shared originals.",
    ],
    archive: [
      "Archived assets",
      "Only assets waiting to be restored appear here. Completed restores are in the history below.",
    ],
  };
  $("view-title").textContent = titles[view][0];
  $("view-help").textContent = titles[view][1];
  document
    .querySelectorAll("nav button")
    .forEach((b) => b.classList.toggle("active", b.dataset.tab === view));
  $("selection-actions").hidden = !["inbound", "outbound"].includes(view);
  $("bulk-actions").hidden = !["inbound", "outbound"].includes(view);
  $("full-pack").hidden = view !== "inbound";
  selectionCount();
  const managed = new Set(state.installed.map((row) => row.path));
  let rows =
    view === "inbound"
      ? catalog
      : view === "outbound"
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
            ? "No managed assets yet."
            : $("source").value
              ? "No matching assets."
              : "Connect a library to get started.",
      ),
      el("p", "Your existing project files stay in place."),
    );
    $("assets").append(empty);
    return;
  }
  rows.forEach((row, index) => {
    const wrap = el("div", undefined, "asset");
    const main = el("div", undefined, "asset-main");
    if (view === "inbound" || view === "outbound") {
      const checked = state.config[view][key(row)];
      const draft = drafts[draftKey(row)];
      const input = el("input");
      input.type = "checkbox";
      input.id = "pick-" + index;
      input.checked = draft
        ? draft.enabled
        : !!checked && (view !== "outbound" || checked.path === row.path);
      input.disabled =
        !["available", "local"].includes(row.status) ||
        (view === "inbound" && row.kind === "powers");
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
      main.append(
        revealButton(
          view === "inbound" ? row.path : state.target + "/" + row.path,
        ),
      );
      if (row.error) main.append(el("p", row.error));
      if (view === "inbound" && row.kind === "powers" && row.kiro)
        main.append(powerControls(row));
      else if (view === "inbound" && row.status === "available") {
        const list = el("div", undefined, "destinations");
        for (const d of row.destinations)
          list.append(
            el(
              "span",
              TOOL_NAMES[d.tool] + " · " + DESTINATION_LABELS[d.state],
              "badge " + d.state,
            ),
          );
        if (!row.destinations.length)
          list.append(
            el("span", "No enabled tool reads " + row.kind, "badge blocked"),
          );
        main.append(list);
      }
      if (view === "inbound" && !input.disabled) {
        const alias = el("input");
        alias.className = "alias";
        alias.placeholder = "Local alias (optional)";
        alias.setAttribute("aria-label", "Local alias for " + row.name);
        alias.value = draft ? draft.alias : checked?.alias || "";
        alias.addEventListener("input", () => remember(row, input, alias));
        main.append(alias);
      }
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
      return (
        draft.enabled !== isSaved(draft.row) ||
        (view === "inbound" &&
          draft.enabled &&
          (draft.alias || "") !== (saved?.alias || ""))
      );
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
  pending.changes.forEach((change) => {
    const div = el("div", undefined, "change");
    div.append(el("strong", change.op), el("div", change.path));
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
const POWER_LABELS = {
  "not-installed": "not installed",
  current: "installed, same as library",
  differs: "installed, differs from library",
};
function powerControls(row) {
  const box = el("div", undefined, "destinations");
  box.append(
    el(
      "span",
      "Kiro powers (user-wide) · " + POWER_LABELS[row.kiro],
      "badge " +
        (row.kiro === "current"
          ? "installed"
          : row.kiro === "differs"
            ? "changed"
            : "absent"),
    ),
  );
  const actions =
    row.kiro === "not-installed"
      ? [["install", "Install in Kiro"]]
      : row.kiro === "differs"
        ? [
            ["install", "Update in Kiro"],
            ["uninstall", "Uninstall from Kiro"],
          ]
        : [["uninstall", "Uninstall from Kiro"]];
  for (const [action, label] of actions) {
    const button = el("button", label, "quiet");
    button.type = "button";
    button.onclick = () =>
      task(() =>
        preview({ power: true, action, source: row.source, name: row.name }),
      );
    box.append(button);
  }
  return box;
}
function conflictRow(conflict, spec) {
  const div = el("div", undefined, "conflict");
  div.append(el("p", conflict.message));
  const [source, kind, name] = (conflict.key || "").split("/");
  if (conflict.reason === "unmanaged" && conflict.path) {
    const archive = el("button", "Archive local copy", "quiet");
    archive.onclick = () =>
      task(async () => {
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
    await api("source", {
      name,
      path: info.path,
      layout: $("source-layout").value,
    });
    await refresh();
    $("source").value = name;
    await loadCatalog();
    await loadRepository();
    render();
    resetAddLibrary();
    $("add-library").open = false;
    message("Library " + name + " connected. Choose assets under Receive.");
  });
};
$("source").onchange = () =>
  task(async () => {
    invalidate();
    await loadCatalog();
    await loadRepository();
    render();
  });
$("refresh").onclick = () => task(refresh);
$("search").oninput = render;
$("save").onclick = () => task(saveSelection);
$("preview").onclick = () =>
  task(async () => {
    await saveSelection(true);
    await preview({ direction: view });
  });
$("apply").onclick = () =>
  task(async () => {
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
$("tools").onchange = (event) =>
  task(async () => {
    const tools = [...$("tools").querySelectorAll("input:checked")].map(
      (input) => input.value,
    );
    if (!tools.length) {
      event.target.checked = true;
      throw new Error("Keep at least one tool enabled.");
    }
    await api("tools", { tools });
    await refresh(true);
    message(
      "Tools saved. Preview to add or archive links for the changed tools.",
    );
  });
$("dismiss").onclick = invalidate;
document.querySelectorAll("nav button").forEach(
  (button) =>
    (button.onclick = () => {
      view = button.dataset.tab;
      $("search").value = "";
      invalidate();
      render();
    }),
);
function selectable() {
  // Powers are installed by Kiro, not linked, so they are never inbound selections.
  return (view === "inbound" ? catalog : state.candidates).filter(
    (r) =>
      ["available", "local"].includes(r.status) &&
      !(view === "inbound" && r.kind === "powers"),
  );
}
function isSaved(row) {
  const saved = state.config[view][key(row)];
  return !!saved && (view !== "outbound" || saved.path === row.path);
}
function unsaved() {
  return changedDrafts().length;
}
function selectionCount() {
  if (!state || !["inbound", "outbound"].includes(view)) return;
  const rows = selectable();
  const selected = rows.filter((r) => {
    const draft = drafts[draftKey(r)];
    return draft ? draft.enabled : isSaved(r);
  }).length;
  $("selected-count").textContent =
    selected +
    " / " +
    rows.length +
    " selected · all assets, including filtered results";
  const changes = unsaved();
  $("selection-actions").classList.toggle("dirty", changes > 0);
  $("pending-count").textContent =
    selected +
    " selected · " +
    (changes
      ? changes + (changes === 1 ? " unsaved change" : " unsaved changes")
      : "no unsaved changes");
}
function selectAll(enabled, rows = selectable()) {
  if (!$("source").value) throw new Error("Choose a library first.");
  if (
    enabled &&
    view === "outbound" &&
    new Set(rows.map(key)).size !== rows.length
  )
    throw new Error(
      "Multiple originals share a name. Select the intended originals individually.",
    );
  for (const row of rows) {
    const saved = state.config[view][key(row)],
      prior = drafts[draftKey(row)];
    drafts[draftKey(row)] = {
      row,
      enabled,
      alias: prior?.alias ?? saved?.alias ?? "",
    };
  }
  invalidate();
  render();
}
$("select-all").onclick = () => task(async () => selectAll(true));
$("deselect-all").onclick = () => task(async () => selectAll(false));
$("full-pack").onclick = () =>
  task(async () => {
    // Assets blocked by local files stay unselected so the rest can install.
    const rows = selectable().filter(installable);
    const skipped = selectable().length - rows.length;
    if (!rows.length)
      throw new Error("No asset in this library can be installed as is.");
    selectAll(true, rows);
    await saveSelection(true);
    await preview({ direction: "inbound" });
    if (skipped)
      message(
        "Full pack: " +
          rows.length +
          " selected, " +
          skipped +
          " skipped because a local file is in the way or no enabled tool reads them.",
      );
  });
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
    });
    await refresh();
    $("source").value = name;
    await loadCatalog();
    await loadRepository();
    render();
    message(
      "Repository cloned and connected. Choose Install full pack to preview all assets.",
    );
  });
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
  $("folder-picker-title").textContent = cloning
    ? "Choose checkout parent folder"
    : "Choose a library folder";
  $("folder-picker-help").textContent = cloning
    ? "Choose an existing parent and name the new checkout. The folder is created when you clone."
    : "Folders marked library contain ip/ or skills/, agents/, … Open one and choose Use this folder.";
  $("folder-select").textContent = cloning
    ? "Use checkout location"
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
