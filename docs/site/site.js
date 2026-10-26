"use strict";

// Screens are captured from the application's non-operational sample preview.
const screens = {
  overview: {
    alt: "Overview with refresh, Discord, connection and renewal controls; sample data",
    title: "Your starting point.",
    description:
      "See connection and renewal status, open your tools, and follow activity in one window.",
  },
  accounts: {
    alt: "Accounts and apps screen with three sample email addresses and assigned IPA files",
    title: "An assignment for every account.",
    description:
      "Pick your IPA folder, add accounts, and choose the file each account installs. Saved assignments are shared with Discord and scheduled work.",
  },
  connections: {
    alt: "Connections screen showing USB pairing, local Wi-Fi and remote access setup controls",
    title: "Pair once. Add options when you need them.",
    description:
      "Start with a trusted USB connection, then configure local Wi-Fi or experimental remote access.",
  },
};
document.querySelectorAll("[data-screen]").forEach((button) => {
  button.addEventListener("click", () => {
    const screen = screens[button.dataset.screen];
    const path = `assets/${button.dataset.screen}.png`;
    const image = document.getElementById("gallery-image");
    image.src = path;
    image.alt = screen.alt;
    document.getElementById("gallery-link").href = path;
    const caption = document.getElementById("gallery-caption");
    const title = document.createElement("strong");
    title.textContent = screen.title;
    caption.replaceChildren(title, document.createTextNode(` ${screen.description}`));
    document.querySelectorAll("[data-screen]").forEach((item) => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    });
  });
});

const search = document.getElementById("command-search");
const commands = [...document.querySelectorAll(".command")];
search.addEventListener("input", () => {
  const term = search.value.trim().toLocaleLowerCase();
  let visible = 0;
  commands.forEach((command) => {
    command.hidden = !command.textContent.toLocaleLowerCase().includes(term);
    if (!command.hidden) visible += 1;
  });
  document.getElementById("command-empty").hidden = visible !== 0;
  document.getElementById("command-count").textContent =
    `${visible} command${visible === 1 ? "" : "s"} · Reference only, not a live Discord session`;
});

const dialog = document.getElementById("screenshot-dialog");
if (typeof dialog.showModal === "function") {
  document.querySelectorAll("[data-lightbox]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      const image = document.getElementById("dialog-image");
      image.src = link.href;
      image.alt = link.querySelector("img").alt;
      dialog.showModal();
    });
  });
  dialog.querySelector("button").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const box = dialog.getBoundingClientRect();
    if (
      event.clientX < box.left ||
      event.clientX > box.right ||
      event.clientY < box.top ||
      event.clientY > box.bottom
    )
      dialog.close();
  });
}
