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
task(refresh);
