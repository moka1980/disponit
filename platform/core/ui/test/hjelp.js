// Testrigg: jsdom-globaler + axe-kjører. axe' color-contrast krever ekte
// layout (getComputedStyle med verdier) som jsdom ikke gir — den regelen
// slås derfor av her og dekkes i stedet av token-kontrast-doktrinen
// (tokens.css ≥ 4.5:1) + CP1-porten mot hardkodet farge, og av ekte
// nettleser i E2E (CP5). Alt annet (roller, navn, aria, tabell, dialog,
// landemerker) måles reelt.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM } from "jsdom";
import axe from "axe-core";

const HER = dirname(fileURLToPath(import.meta.url));
export const NB = JSON.parse(
  readFileSync(join(HER, "..", "..", "..", "..", "locales", "nb.json"), "utf-8"));

const dom = new JSDOM("<!doctype html><html lang='nb'><body></body></html>", {
  url: "https://disponit.example/",
  pretendToBeVisual: true,
});
const w = dom.window;
// `performance` står IKKE i listen: jsdoms PerformanceImpl.now kaller
// global `performance.now()`, så peker globalen på jsdom-wrapperen blir
// det uendelig rekursjon («Maximum call stack size exceeded») ved første
// tidsmåling — f.eks. når en iframe får en src. Nodes egen duger.
for (const navn of ["window", "document", "navigator", "HTMLElement", "Node",
                    "Element", "getComputedStyle",
                    "localStorage", "SVGElement", "customElements",
                    "NodeFilter", "Event", "KeyboardEvent", "MouseEvent"]) {
  if (w[navn] === undefined) continue;
  // Noen globaler (navigator, performance) er skrivebeskyttede getters i
  // node 24 — defineProperty overstyrer der det går, ellers beholder vi
  // nodes egen (den duger: i18n faller uansett tilbake til nb).
  try {
    Object.defineProperty(globalThis, navn,
      { value: w[navn], configurable: true, writable: true });
  } catch { /* behold nodes egen */ }
}

export function nyttBrett() {
  document.body.replaceChildren();
  return document.body;
}

// Kjør axe på `node`. `fragment: true` slår av sidenivå-regler (region /
// landmark-one-main) som bare gir mening på en HEL side — de testes via
// AppShell/flatene. Returnerer alvorlige/kritiske brudd.
export async function alvorligeBrudd(node, { fragment = false } = {}) {
  const brett = nyttBrett();
  brett.append(node);
  const regler = { "color-contrast": { enabled: false } };
  if (fragment) {
    regler.region = { enabled: false };
    regler["landmark-one-main"] = { enabled: false };
    regler["page-has-heading-one"] = { enabled: false };
  }
  const res = await axe.run(brett, { rules: regler });
  return res.violations.filter((v) => ["serious", "critical"].includes(v.impact));
}

export function beskrivBrudd(brudd) {
  return brudd.map((v) => `${v.id} (${v.impact}): ${v.help}`).join("\n");
}
