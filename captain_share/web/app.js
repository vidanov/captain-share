"use strict";
const $ = (id) => document.getElementById(id);
const token =
  new URLSearchParams(location.hash.slice(1)).get("token") ||
  sessionStorage.getItem("captain-share-token") ||
  "";
if (token) sessionStorage.setItem("captain-share-token", token);
history.replaceState(null, "", location.pathname);
let state,
  catalog = [],
  view = "inbound",
  pending = null,
  busy = false,
  drafts = {};
let repository = null,
  gitPending = null;
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function message(text, error = false) {
  $("message").textContent = error ? "Error: " + text : text;
  $("message").className = error ? "error" : "";
}
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
  if (busy) return;
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
  $("review").hidden = true;
  $("apply").disabled = true;
}
async function refresh() {
  state = await api("state");
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
  await loadCatalog();
  await loadRepository();
  drafts = {};
  invalidate();
  render();
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
  let rows =
    view === "inbound"
      ? catalog
      : view === "outbound"
        ? state.candidates
        : view === "installed"
          ? state.installed
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
      main.append(
        revealButton(
          view === "inbound" ? row.path : state.target + "/" + row.path,
        ),
      );
      if (row.error) main.append(el("p", row.error));
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
      main.append(
        el(
          "div",
          installed ? row.path.split("/").pop() : row.relative_path,
          "asset-title",
        ),
        el(
          "div",
          installed
            ? row.status === "linked"
              ? "Installed"
              : "Needs attention"
            : "Archived " + friendlyDate(row.created),
          "asset-type",
        ),
        el("div", installed ? row.path : row.relative_path, "asset-path"),
      );
      if (installed)
        main.append(
          el("div", "Original: " + row.shared_original, "asset-path"),
        );
      main.append(
        revealButton(
          installed
            ? state.target + "/" + row.path
            : state.target + "/.captain/archive/" + row.id + "/payload",
        ),
      );
      if (installed)
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
async function saveSelection() {
  if (!$("source").value)
    throw new Error("Connect and choose a library first.");
  for (const input of $("assets").querySelectorAll(
    "input[type=checkbox]:not(:disabled)",
  )) {
    const row = JSON.parse(input.dataset.row);
    remember(row, input, input.closest(".asset").querySelector(".alias"));
  }
  const prefix = view + "/" + $("source").value + "/";
  const selections = Object.entries(drafts)
    .filter(([k]) => k.startsWith(prefix))
    .map(([, draft]) => ({
      kind: draft.row.kind,
      name: draft.row.name,
      path: draft.row.path,
      enabled: draft.enabled,
      alias: draft.alias || undefined,
    }));
  await api("selections", {
    direction: view,
    source: $("source").value,
    selections,
  });
  await refresh();
  message("Selection saved. Preview to see the file changes.");
}
async function preview(spec = { direction: view }) {
  pending = await api("plan", spec);
  $("review").hidden = false;
  $("changes").replaceChildren();
  $("errors").replaceChildren();
  $("review-count").textContent = pending.changes.length + " changes";
  pending.changes.forEach((change) => {
    const div = el("div", undefined, "change");
    div.append(el("strong", change.op), el("div", change.path));
    if (change.source) div.append(el("div", "From " + change.source));
    $("changes").append(div);
  });
  if (!pending.changes.length)
    $("changes").append(el("p", "The selected assets are already in place."));
  pending.errors.forEach((error) => $("errors").append(el("p", error)));
  $("apply").disabled = !pending.can_apply;
  $("review").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
$("add-source").onsubmit = (event) => {
  event.preventDefault();
  task(async () => {
    await api("source", {
      name: $("source-name").value,
      path: $("source-path").value,
      layout: $("source-layout").value,
    });
    await refresh();
    message("Library connected. Choose the assets to receive or contribute.");
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
    await saveSelection();
    await preview({ direction: view });
  });
$("apply").onclick = () =>
  task(async () => {
    if (!pending) return;
    const result = await api("apply", { fingerprint: pending.fingerprint });
    await refresh();
    message("Applied " + result.changes + " asset changes.");
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
  return (view === "inbound" ? catalog : state.candidates).filter((r) =>
    ["available", "local"].includes(r.status),
  );
}
function selectionCount() {
  if (!state || !["inbound", "outbound"].includes(view)) return;
  const rows = selectable();
  const selected = rows.filter((r) => {
    const draft = drafts[draftKey(r)],
      saved = state.config[view][key(r)];
    return draft
      ? draft.enabled
      : !!saved && (view !== "outbound" || saved.path === r.path);
  }).length;
  $("selected-count").textContent =
    selected +
    " / " +
    rows.length +
    " selected · all assets, including filtered results";
}
function selectAll(enabled) {
  if (!$("source").value) throw new Error("Choose a library first.");
  const rows = selectable();
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
    selectAll(true);
    await saveSelection();
    await preview({ direction: "inbound" });
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
    for (const folder of result.folders) {
      const button = el("button", folder.name, "folder-entry quiet");
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
    : "Browse to the repository checkout you want to connect.";
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
