// Skallets varselteller — OPPFRISKNING. Egen fil, samme grunn som de andre
// `app_*`-filene: `app.js` starter seg selv ved import og holder
// modultilstanden.
//
// 🔴 Funnet: oppstartens ene kall var den eneste AUTOMATISKE hentingen i hele
// øktens levetid. De andre kallstedene ligger i varselflaten og utløses bare
// av at denne klienten selv merker noe lest eller åpner det. Åpner en annen
// policyforvalter en runde etter at siden er lastet, spurte ingenting igjen:
// telleren kunne stå på null resten av økten, og den som har valgt kun portal
// fikk aldri den proaktive beskjeden hele feltet finnes for.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { NB } from "./hjelp.js";

const HER = dirname(fileURLToPath(import.meta.url));
const EN = JSON.parse(readFileSync(
  join(HER, "..", "..", "..", "..", "locales", "en.json"), "utf-8"));
const LOCALER = { nb: NB, en: EN };

const svar = (kropp, status = 200) =>
  ({ ok: status < 400, status, json: async () => kropp });

const SESJON = { tenant: "acme", scopes: ["policy:write"] };

let varselkall = 0;
//: Det ANDRE mennesket i historien: en runde åpnes etter at siden er lastet.
let uleste = 0;

globalThis.fetch = async (url) => {
  const sti = String(url).split("?")[0];
  const locale = sti.match(/^\/ui\/locale\/(nb|en)$/);
  if (locale) return svar(LOCALER[locale[1]]);
  if (sti === "/ui/oppsett.json") return svar({ provider_id: "google" });
  if (sti === "/v1/sesjon") return svar(SESJON);
  if (sti === "/v1/utrulling") return svar({ tenanter: [], moduler: [] });
  if (sti === "/v1/varsel") {
    varselkall += 1;
    return svar({ varsler: [], uleste, kanal: "kun_portal" });
  }
  return svar({ feil: "ukjent" }, 404);
};

async function vent(pred, n = 400) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}

const status = () => (document.querySelector(".skall-status") || {}).textContent;
const sier = (antall) =>
  NB["ui.shell.status_varsler"].replace("{antall}", String(antall));

document.documentElement.setAttribute("data-sprak", "nb");
const app = document.createElement("div");
app.id = "app";
document.body.replaceChildren(app);

await import("../static/js/app.js");

test("Telleren hentes på nytt ved navigasjon og når fanen blir synlig igjen",
     async () => {
  await vent(() => document.documentElement.getAttribute("data-visning") === "app");
  assert.ok(await vent(() => (status() || "").includes(sier(0))),
    `oppstarten viste ikke tallet: «${status()}»`);
  assert.equal(varselkall, 1,
    "oppstarten skal hente tallet ÉN gang — første navigasjon er dekket av"
    + " det kallet, og to kall om det samme ved hver innlasting er støy");

  // Noen andre åpner en runde. Klienten vet ingenting om det ennå.
  uleste = 2;
  assert.ok((status() || "").includes(sier(0)),
    "telleren endret seg uten at noe hentet den");

  // …hun navigerer.
  window.location.hash = "#/kundeadmin";
  assert.ok(await vent(() => (status() || "").includes(sier(2))),
    `navigasjonen hentet ikke tallet på nytt: «${status()}»`);
  assert.equal(varselkall, 2, "navigasjonen hentet tallet flere ganger");

  // …og hun kommer tilbake til fanen etter at enda en runde er åpnet.
  uleste = 5;
  document.dispatchEvent(new Event("visibilitychange"));
  assert.ok(await vent(() => (status() || "").includes(sier(5))),
    `fanen ble synlig igjen uten at tallet ble hentet: «${status()}»`);
});
