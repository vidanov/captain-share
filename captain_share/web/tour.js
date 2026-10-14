"use strict";
// A replayable tour: real screenshots, a moving cursor, one caption per step.
// Cursor positions are percentages of the 1280×800 screenshots; see scripts/tour.
const STEPS = [
  {
    image: "01-setup.jpg",
    x: 41.4,
    y: 41.9,
    click: true,
    title: "Set up once",
    text: "A new teammate picks the agents they use, for example Kiro and Claude Code. Skills they get are linked into each of them.",
  },
  {
    image: "02-clone.jpg",
    x: 37.4,
    y: 69.6,
    click: true,
    title: "Add the team library",
    text: "Paste the Git address of your team's library. It is cloned into ~/Libraries with your own Git access. Nothing else is needed.",
  },
  {
    image: "03-library.jpg",
    x: 71.4,
    y: 36.1,
    click: true,
    title: "Library is home",
    text: "It lists everything in the library, who shared it and when. The team suggests a starter set; one click takes it.",
  },
  {
    image: "04-review.jpg",
    x: 69.6,
    y: 45.7,
    click: true,
    title: "See it before it happens",
    text: "Every change is listed first: here, links for plan and review into Kiro and Claude. Apply creates them; Cancel changes nothing.",
  },
  {
    image: "05-get.jpg",
    x: 65.2,
    y: 74.6,
    click: true,
    title: "Get what a colleague recommends",
    text: "Someone says “try pr-review”. Find it in Library and click Get.",
  },
  {
    image: "06-where.jpg",
    x: 81.4,
    y: 27.2,
    click: false,
    title: "Choose where it works",
    text: "Everywhere for you, in every project, or only in this project. The review shows the exact links before anything changes.",
  },
  {
    image: "07-look.jpg",
    x: 74.2,
    y: 37.6,
    click: true,
    title: "Look before you share",
    text: "Share lists skills in this project and in your global folders that no library has yet. Preview shows what is inside; Show in Finder opens the folder.",
  },
  {
    image: "08-share.jpg",
    x: 26.8,
    y: 90.5,
    click: true,
    title: "Share what works",
    text: "Share copies it into the library, commits it with your note, and pushes it to the team. A library check, when set, runs first.",
  },
  {
    image: "09-shared.jpg",
    x: 72.3,
    y: 62.5,
    click: true,
    title: "Tell your team",
    text: "The result says where the commit landed. You get a line to paste in chat; that is all a teammate needs.",
  },
  {
    terminal:
      "$ captain-share get sql-explain\n\nPreview: sync / inbound in ~\n  link   ~/.kiro/skills/sql-explain ← ~/Libraries/team/ip/skills/sql-explain\n  link   ~/.claude/skills/sql-explain ← ~/Libraries/team/ip/skills/sql-explain\n2 asset changes. Ready to apply.\nGet sql-explain? [y/N] y\nGot team/skills/sql-explain for your user.",
    title: "A teammate takes it",
    text: "They paste the line into a terminal, see what it will link, and confirm. The skill is in every project for them.",
  },
  {
    image: "11-install.jpg",
    x: 26,
    y: 50,
    click: true,
    title: "Got a skill as a zip?",
    text: "Downloaded one, or a colleague sent a folder? Install it into your own library first and try it. Share it with the team later, from Share.",
  },
  {
    image: "12-updates.jpg",
    x: 72.4,
    y: 33.8,
    click: true,
    title: "Weeks later: what's new?",
    text: "Check for updates asks Git whether the team pushed anything. Update library brings it in.",
  },
  {
    image: "13-new.jpg",
    x: 65.2,
    y: 50,
    click: false,
    title: "New since you last looked",
    text: "New items wait at the top with who shared them. Get the ones you want; ignore the rest.",
  },
  {
    image: "14-behind.jpg",
    x: 69.7,
    y: 49.9,
    click: false,
    title: "Behind the team",
    text: "An old copy of plan is still in this project, and the team has improved plan since. One click swaps the copy for the team version; the copy goes to the Archive.",
  },
  {
    image: "15-where.jpg",
    x: 37,
    y: 50,
    click: false,
    title: "The full picture, when you need it",
    text: "Where installed shows every asset in this project, the folders above it, and your user. Most people never need it.",
  },
];
const $ = (id) => document.getElementById(id);
// Same theme choice as the main window (Auto unless the switch chose one).
try {
  const chosen = localStorage.getItem("captain-share-theme");
  if (chosen === "light" || chosen === "dark")
    document.documentElement.dataset.theme = chosen;
} catch {
  // No storage: follow the system.
}
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let index = 0,
  playing = !reduced,
  timer = null,
  front = "tour-a";

function stepDuration(step) {
  // Long enough to read the caption: about 15 characters per second, 4–9 s.
  return Math.min(9000, Math.max(4000, step.text.length * 65 + 1600));
}
function moveCursor(step) {
  const cursor = $("tour-cursor");
  const ripple = $("tour-ripple");
  ripple.classList.remove("on");
  if (!step.x) {
    cursor.style.display = "none";
    return;
  }
  cursor.style.display = "";
  cursor.style.left = step.x + "%";
  cursor.style.top = step.y + "%";
  ripple.style.left = step.x + "%";
  ripple.style.top = step.y + "%";
  if (step.click)
    setTimeout(
      () => {
        if (STEPS[index] === step) ripple.classList.add("on");
      },
      reduced ? 0 : 1100,
    );
}
function show(next, animate = true) {
  index = (next + STEPS.length) % STEPS.length;
  const step = STEPS[index];
  clearTimeout(timer);
  const term = $("tour-term");
  if (step.terminal) {
    term.textContent = step.terminal;
    term.hidden = false;
  } else {
    term.hidden = true;
    const back = front === "tour-a" ? "tour-b" : "tour-a";
    $(back).src = "/tour/" + step.image;
    $(back).alt = step.alt || step.title;
    $(back).classList.add("front");
    $(front).classList.remove("front");
    front = back;
  }
  $("tour-count").textContent = "Step " + (index + 1) + " of " + STEPS.length;
  $("tour-title").textContent = step.title;
  $("tour-text").textContent = step.text;
  document.querySelectorAll("#tour-steps button").forEach((button, i) => {
    button.classList.toggle("done", i < index);
    button.setAttribute("aria-current", i === index ? "step" : "false");
  });
  moveCursor(step);
  if (playing) timer = setTimeout(() => advance(), stepDuration(step));
  if (!animate) $("tour-cursor").classList.add("jump");
  else $("tour-cursor").classList.remove("jump");
}
function advance() {
  if (index === STEPS.length - 1) {
    setPlaying(false);
    return;
  }
  show(index + 1);
}
function setPlaying(value) {
  playing = value;
  $("tour-play").textContent = playing ? "Pause" : "Play";
  clearTimeout(timer);
  if (playing) timer = setTimeout(() => advance(), stepDuration(STEPS[index]));
}

$("tour-steps").replaceChildren(
  ...STEPS.map((step, i) => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("aria-label", "Step " + (i + 1) + ": " + step.title);
    button.onclick = () => show(i, false);
    item.append(button);
    return item;
  }),
);
$("tour-prev").onclick = () => show(index - 1, false);
$("tour-next").onclick = () => show(index + 1, false);
$("tour-play").onclick = () => {
  if (!playing && index === STEPS.length - 1) {
    setPlaying(true);
    show(0);
    return;
  }
  setPlaying(!playing);
};
$("tour-replay").onclick = () => {
  setPlaying(true);
  show(0);
};
document.addEventListener("keydown", (event) => {
  if (event.target.closest("button") && event.key === " ") return;
  if (event.key === "ArrowRight") show(index + 1, false);
  else if (event.key === "ArrowLeft") show(index - 1, false);
  else if (event.key === " ") {
    event.preventDefault();
    setPlaying(!playing);
  }
});
// Preload every screenshot so steps change without a blank frame.
for (const step of STEPS)
  if (step.image) new Image().src = "/tour/" + step.image;
setPlaying(playing);
show(0);
