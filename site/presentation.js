const slides = [...document.querySelectorAll(".slide")];
const progress = document.querySelector("[data-progress]");
const previous = document.querySelector("[data-previous]");
const next = document.querySelector("[data-next]");
const menu = document.querySelector("[data-menu]");
const menuToggle = document.querySelector("[data-menu-toggle]");
const fullscreen = document.querySelector("[data-fullscreen]");
const motion = document.querySelector("[data-motion]");
const isEnglish = document.documentElement.lang === "en";
const deckLabel = isEnglish ? "ContextOx dynamic introduction" : "数契动态介绍";
let current = Math.max(0, Math.min(slides.length - 1, Number(location.hash.slice(1) || 1) - 1));

function render(index, push = true) {
  current = Math.max(0, Math.min(slides.length - 1, index));
  slides.forEach((slide, slideIndex) => slide.setAttribute("aria-hidden", String(slideIndex !== current)));
  document.querySelectorAll("[data-slide-index]").forEach((item, itemIndex) => item.setAttribute("aria-current", String(itemIndex === current)));
  progress.textContent = `${String(current + 1).padStart(2, "0")} / ${String(slides.length).padStart(2, "0")}`;
  previous.disabled = current === 0;
  next.disabled = current === slides.length - 1;
  document.title = `${slides[current].dataset.title} · ${deckLabel}`;
  document.body.classList.toggle("deck-stage-active", slides[current].classList.contains("slide--stage"));
  if (push) history.replaceState(null, "", `#${current + 1}`);
  slides[current].focus({preventScroll: true});
}

function move(delta) { render(current + delta); }
previous.addEventListener("click", () => move(-1));
next.addEventListener("click", () => move(1));

menuToggle.addEventListener("click", () => {
  const willOpen = menu.hidden;
  menu.hidden = !willOpen;
  menuToggle.setAttribute("aria-expanded", String(willOpen));
});

document.querySelectorAll("[data-slide-index]").forEach((button, index) => {
  button.addEventListener("click", () => {
    render(index);
    menu.hidden = true;
    menuToggle.setAttribute("aria-expanded", "false");
  });
});

fullscreen.addEventListener("click", async () => {
  if (document.fullscreenElement) await document.exitFullscreen();
  else await document.documentElement.requestFullscreen();
});

motion.addEventListener("click", () => {
  const reduced = document.documentElement.classList.toggle("motion-reduced");
  motion.setAttribute("aria-pressed", String(reduced));
  motion.textContent = reduced
    ? (isEnglish ? "Restore motion" : "恢复动效")
    : (isEnglish ? "Reduce motion" : "减弱动效");
});

document.addEventListener("keydown", (event) => {
  if (event.target.closest("button, a, input, textarea, select")) return;
  if (["ArrowRight", "PageDown", " "].includes(event.key)) { event.preventDefault(); move(1); }
  if (["ArrowLeft", "PageUp"].includes(event.key)) { event.preventDefault(); move(-1); }
  if (event.key === "Home") render(0);
  if (event.key === "End") render(slides.length - 1);
  if (event.key === "Escape" && !menu.hidden) {
    menu.hidden = true;
    menuToggle.setAttribute("aria-expanded", "false");
  }
});

window.addEventListener("hashchange", () => render(Number(location.hash.slice(1) || 1) - 1, false));
render(current, false);
