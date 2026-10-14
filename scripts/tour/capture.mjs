// usage: node capture-tour.mjs <url-with-token> <fixture-dir> <out-dir>
// One headless Chrome session walks the product; each step saves a JPEG and the
// cursor target (percent of the 1280x800 viewport). Prints the steps as JSON.
import { spawn, execSync } from "node:child_process";
import { writeFileSync, mkdtempSync, readFileSync } from "node:fs";
const [url, F, out] = process.argv.slice(2);
const W = 1280, H = 800;
const profile = mkdtempSync(process.env.TMPDIR + "/tour-");
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ["--headless=new", "--remote-debugging-port=9334", `--user-data-dir=${profile}`, "--hide-scrollbars", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let target;
for (let i = 0; i < 50 && !target; i++) {
  await sleep(200);
  try { target = (await (await fetch("http://127.0.0.1:9334/json")).json()).find((t) => t.type === "page"); } catch {}
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0; const waits = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (waits.has(d.id)) { waits.get(d.id)(d); waits.delete(d.id); } };
const send = (method, params = {}) => new Promise((r) => { waits.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
const js = async (expression) => {
  const r = await send("Runtime.evaluate", { expression: `(async () => { ${expression} })()`, awaitPromise: true, returnByValue: true });
  if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails));
  return r.result?.result?.value;
};
await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: false });
await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "dark" }] });
await send("Page.enable");
await send("Page.navigate", { url });
await sleep(2500);

const steps = [];
async function shot(file, selector, step) {
  const rect = selector ? await js(`const e = ${selector}; if (!e) return null; e.scrollIntoView({block: "center"}); await new Promise(r => setTimeout(r, 300)); const b = e.getBoundingClientRect(); return {x: b.left + b.width / 2, y: b.top + b.height / 2};`) : null;
  await sleep(500);
  const s = await send("Page.captureScreenshot", { format: "jpeg", quality: 82 });
  writeFileSync(`${out}/${file}`, Buffer.from(s.result.data, "base64"));
  steps.push({ image: file, ...(rect ? { x: +(rect.x / W * 100).toFixed(1), y: +(rect.y / H * 100).toFixed(1) } : {}), ...step });
}
const q = (sel) => `document.querySelector(${JSON.stringify(sel)})`;
const byText = (sel, text) => `[...document.querySelectorAll(${JSON.stringify(sel)})].find(b => b.textContent.trim() === ${JSON.stringify(text)})`;
const row = (name) => `[...document.querySelectorAll(".lib-row")].find(r => r.querySelector(".lib-name span")?.textContent === ${JSON.stringify(name)})`;
const go = (tab) => js(`document.querySelector('.faceplate-nav [data-tab="${tab}"]').click(); await new Promise(r => setTimeout(r, 1500)); window.scrollTo(0, 0);`);

// 1 Setup: agents
await shot("01-setup.jpg", byText("#setup-agents button", "Claude"), { click: true,
  title: "Set up once", text: "A new teammate picks the agents they use, for example Kiro and Claude Code. Skills they get are linked into each of them." });
await js(`${byText("#setup-agents button", "Claude")}.click(); ${byText("#setup-agents button", "Copilot")}.click(); const u = ${q("#setup-url")}; u.value = "git@example.com:acme/team.git"; u.dispatchEvent(new Event("input"));`);
// 2 Setup: library
await shot("02-clone.jpg", byText("#setup-clone button", "Clone and continue"), { click: true,
  title: "Add the team library", text: "Paste the Git address of your team's library. It is cloned into ~/Libraries with your own Git access. Nothing else is needed." });
// The screenshot shows a neutral address; the clone uses the local test remote.
await js(`const u = ${q("#setup-url")}; u.value = ${JSON.stringify(F + "/team.git")}; u.dispatchEvent(new Event("input")); ${byText("#setup-clone button", "Clone and continue")}.click(); await new Promise(r => setTimeout(r, 4000));`);
execSync(`git -C "${F}/home/Libraries/team" config user.name "Alex Example" && git -C "${F}/home/Libraries/team" config user.email alex@example.com && git -C "${F}/home/Libraries/team" config commit.gpgsign false`);
// 3 Library + starter
await shot("03-library.jpg", `document.querySelector(".strip.starter button")`, { click: true,
  title: "Library is home", text: "It lists everything in the library, who shared it and when. The team suggests a starter set; one click takes it." });
await js(`document.querySelector(".strip.starter button").click(); await new Promise(r => setTimeout(r, 2000));`);
// 4 Review
await shot("04-review.jpg", q("#apply"), { click: true,
  title: "See it before it happens", text: "Every change is listed first: here, links for plan and review into Kiro and Claude. Apply creates them; Cancel changes nothing." });
await js(`${q("#apply")}.click(); await new Promise(r => setTimeout(r, 2500));`);
// 5 Get one
await shot("05-get.jpg", `${row("pr-review")}.querySelector(".lib-act button:not(.link)")`, { click: true,
  title: "Get what a colleague recommends", text: "Someone says “try pr-review”. Find it in Library and click Get." });
await js(`${row("pr-review")}.querySelector(".lib-act button:not(.link)").click(); await new Promise(r => setTimeout(r, 2000));`);
// 6 Where
await shot("06-where.jpg", `document.querySelector('#review-where input[value="user"]').closest("label")`, { click: false,
  title: "Choose where it works", text: "Everywhere for you, in every project, or only in this project. The review shows the exact links before anything changes." });
await js(`${q("#apply")}.click(); await new Promise(r => setTimeout(r, 2500));`);
// 7 Share
await go("share");
await js(`document.querySelector("#share-lists .lib-act button").click(); await new Promise(r => setTimeout(r, 1500)); document.querySelector(".share-panel textarea").value = "Explains a slow query plan and suggests indexes. Nice before a performance review.";`);
await shot("07-share.jpg", byText(".share-panel button", "Share"), { click: true,
  title: "Share what works", text: "Made a useful skill in a project? Share copies it into the library, commits it with your note, and pushes it to the team." });
await js(`${byText(".share-panel button", "Share")}.click(); await new Promise(r => setTimeout(r, 3500));`);
// 8 Shared
await shot("08-shared.jpg", byText(".share-done button", "Copy"), { click: true,
  title: "Tell your team", text: "You get a line to paste in chat. That is all a teammate needs." });
// 9 Terminal
steps.push({ terminal: "$ captain-share get sql-explain\n\nPreview: sync / inbound in ~\n  link   ~/.kiro/skills/sql-explain ← ~/Libraries/team/ip/skills/sql-explain\n  link   ~/.claude/skills/sql-explain ← ~/Libraries/team/ip/skills/sql-explain\n2 asset changes. Ready to apply.\nGet sql-explain? [y/N] y\nGot team/skills/sql-explain for your user.",
  title: "A teammate takes it", text: "They paste the line into a terminal, see what it will link, and confirm. The skill is in every project for them." });
// 10 Import a zip
const b64 = readFileSync(`${F}/commit-message.zip`).toString("base64");
await js(`const bytes = Uint8Array.from(atob(${JSON.stringify(b64)}), c => c.charCodeAt(0)); const dt = new DataTransfer(); dt.items.add(new File([bytes], "commit-message.zip", {type: "application/zip"})); const input = ${q("#import-file")}; input.files = dt.files; input.dispatchEvent(new Event("change")); await new Promise(r => setTimeout(r, 2000)); document.querySelector("#import-panel textarea").value = "Writes a conventional commit message from the staged diff. Found it online, works well."; ${byText("#import-panel button", "Review")}.click(); await new Promise(r => setTimeout(r, 1500));`);
await shot("10-import.jpg", byText("#import-panel button", "Add to library"), { click: true,
  title: "Got a skill as a zip?", text: "Downloaded one, or a colleague sent a folder? Upload it here. It is checked first, then added to the library like any other." });
await js(`${byText("#import-panel button", "Add to library")}.click(); await new Promise(r => setTimeout(r, 3000));`);
// 11 Updates from others
execSync(`rm -rf "${F}/jonas" && git clone -q "${F}/team.git" "${F}/jonas" && mkdir -p "${F}/jonas/ip/prompts" && printf "Turn merged pull requests into release notes.\\n" > "${F}/jonas/ip/prompts/release-notes.md" && git -C "${F}/jonas" add -A && git -C "${F}/jonas" -c user.name=Jonas -c user.email=jonas@example.com -c commit.gpgsign=false commit -qm "Share release-notes" && git -C "${F}/jonas" push -q`);
await go("home");
await js(`${byText(".strip button", "Check for updates")}.click(); await new Promise(r => setTimeout(r, 2500)); window.scrollTo(0, 0);`);
await shot("11-updates.jpg", byText(".strip button", "Update library"), { click: true,
  title: "Weeks later: what's new?", text: "Check for updates asks Git whether the team pushed anything. Update library brings it in." });
await js(`${byText(".strip button", "Update library")}.click(); await new Promise(r => setTimeout(r, 3000)); window.scrollTo(0, 0);`);
await shot("12-new.jpg", `${row("release-notes.md")}.querySelector(".lib-act button:not(.link)")`, { click: false,
  title: "New since you last looked", text: "New items wait at the top with who shared them. Get the ones you want; ignore the rest." });
// 13 Where installed
await go("patch");
await shot("13-where.jpg", q("#matrix"), { click: false,
  title: "The full picture, when you need it", text: "Where installed shows every asset in this project, the folders above it, and your user. Most people never need it." });

console.log(JSON.stringify(steps, null, 2));
ws.close(); chrome.kill();
