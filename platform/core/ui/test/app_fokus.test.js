// Fokus etter et språkbytte i det AUTENTISERTE skallet. Egen fil fordi
// `app.js` starter seg selv ved import og holder modultilstanden — ett
// scenario per prosess, slik `node --test` kjører filene. Riggen må derfor stå
// ferdig før importen, og importen er dynamisk.
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

// Ingen scopes: `kundeadmin` er eneste rute, og den flaten kaller ingen
// endepunkter. Skallet blir dermed ferdig uten at riggen må svare for en flate
// — og det er skallet, ikke flaten, denne testen handler om.
const SESJON = { tenant: "acme", scopes: [] };

globalThis.fetch = async (url) => {
  const sti = String(url).split("?")[0];
  const locale = sti.match(/^\/ui\/locale\/(nb|en)$/);
  if (locale) return svar(LOCALER[locale[1]]);
  if (sti === "/ui/oppsett.json") return svar({ provider_id: "google" });
  if (sti === "/v1/sesjon") return svar(SESJON);
  if (sti === "/v1/utrulling") return svar({ tenanter: [], moduler: [] });
  return svar({ feil: "ukjent" }, 404);
};

async function vent(pred, n = 200) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}

const visning = () => document.documentElement.getAttribute("data-visning");

// Som i `index.html`: dokumentet bærer utgangsspråket. Uten det ville
// `velgSprak` falt til nettleserens språk — «en» i jsdom — og byttet under
// vært et bytte til samme språk.
document.documentElement.setAttribute("data-sprak", "nb");
const app = document.createElement("div");
app.id = "app";
document.body.replaceChildren(app);

await import("../static/js/app.js");

test("Språkbytte i skallet legger fokus tilbake på velgeren", async () => {
  // Codex P2: omstarten erstatter hele skallet, inkludert `<select>`-en
  // brukeren står i. Uten at fokus flyttes over til den nye velgeren, faller
  // det til `<body>`, og en som styrer med tastatur mister plassen sin midt i
  // en handling de selv utløste — mens forsidens språkbytte gjør det riktige.
  assert.ok(await vent(() => visning() === "app"),
    "første last rakk aldri fram til skallet");

  const velger = app.querySelector(".sprakvelger");
  assert.ok(velger, "skallet mangler språkvelgeren");
  velger.focus();
  assert.equal(document.activeElement, velger,
    "riggen fikk ikke fokus inn i velgeren i det hele tatt");

  velger.value = "en";
  velger.dispatchEvent(new Event("change"));

  // Skallet er bygget på nytt når velgeren er et ANNET element enn det vi sto
  // i, og dokumentet er merket engelsk.
  assert.ok(await vent(() => document.documentElement.getAttribute("lang") === "en"
    && app.querySelector(".sprakvelger") !== velger),
    "språkbyttets omstart bygde aldri skallet på nytt");

  const ny = app.querySelector(".sprakvelger");
  assert.equal(document.activeElement, ny,
    "fokus ble ikke med over i det nye skallet etter språkbyttet");
  assert.equal(ny.value, "en", "den nye velgeren står ikke på det valgte språket");
});
