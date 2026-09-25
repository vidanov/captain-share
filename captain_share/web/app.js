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
      "Restore an earlier asset",
      "Archived originals and shortcuts remain here until restored.",
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
          : state.archives;
  const query = $("search").value.toLowerCase();
  rows = rows.filter((r) =>
    [r.name, r.kind, r.path, r.relative_path]
      .join(" ")
      .toLowerCase()
      .includes(query),
  );
  $("count").textContent = rows.length + " assets";
  $("assets").replaceChildren();
  if (!rows.length) {
    const empty = el("div", undefined, "empty");
    empty.append(
      el(
        "strong",
        view === "archive"
          ? "Nothing archived yet."
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
      main.append(
        el("div", row.name || row.relative_path, "asset-title"),
        el("div", row.status || row.created, "asset-type"),
        el("div", row.shared_original || row.id, "asset-path"),
      );
      const button = el(
        "button",
        view === "installed"
          ? "Archive"
          : row.restored
            ? "Restored"
            : "Restore",
        "quiet",
      );
      button.disabled = !!row.restored;
      button.onclick = () =>
        task(() =>
          preview(
            view === "installed"
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
