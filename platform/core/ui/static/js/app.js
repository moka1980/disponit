// M-1 kundeflate — inngang. CP1: minimalt, gyldig skall (header + main-
// landemerke). Datalag, komponentbibliotek, ruting og de fire flatene kommer
// i CP2/CP3 og erstatter `skall()` med den fulle AppShell + ruteren.
import { el, sett } from "./dom.js";

function skall() {
  const app = document.getElementById("app");
  const hoved = el("main",
    { id: "hovedinnhold", class: "skall-hoved", tabindex: "-1" },
    el("p", { class: "tilstand" },
      el("span", { class: "sr-only", text: "Status:" }), "Laster …"));
  sett(app,
    el("header", { class: "skall-topp" },
      el("span", { class: "skall-merke", text: "Disponit" })),
    hoved);
  app.setAttribute("aria-busy", "false");
}

skall();
