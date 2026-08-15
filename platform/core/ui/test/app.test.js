// Oppstarts- og overgangslogikken i `app.js`. Modulen starter seg selv ved
// import (`start()` nederst), så riggen må stå FERDIG før importen: `#app` i
// dokumentet og en fetch som svarer på øktkallet. Derfor dynamisk import.
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

// Økten har INGEN scopes, og det er med hensikt: da er `kundeadmin` den eneste
// ruten (den er scope-fri), og den flaten kaller ikke noe endepunkt. Skallet
// kan altså bygges ferdig uten at riggen må svare for hver enkelt flate — og
// det er nøyaktig den API-frie flaten funnet under handler om: den oppdager
// ikke selv at økten er borte.
const SESJON = { tenant: "acme", scopes: [] };

let utrullingNr = 0;
let slippUtrulling = () => {};
const utrullingHoldt = new Promise((r) => { slippUtrulling = r; });
let sesjonSlettet = false;

globalThis.fetch = async (url, opsjoner = {}) => {
  const sti = String(url).split("?")[0];
  const locale = sti.match(/^\/ui\/locale\/(nb|en)$/);
  if (locale) return svar(LOCALER[locale[1]]);
  if (sti === "/ui/oppsett.json") return svar({ provider_id: "google" });
  if (sti === "/v1/sesjon") {
    if ((opsjoner.method || "GET") === "DELETE") {
      sesjonSlettet = true;
      return svar(null, 204);
    }
    return svar(SESJON);
  }
  if (sti === "/v1/utrulling") {
    // Det ANDRE utrullingskallet er språkbyttets omstart: den parkeres her,
    // slik utrullingen ville hengt på en treg linje, og slippes først etter
    // at brukeren har logget ut.
    if (++utrullingNr === 2) await utrullingHoldt;
    return svar({ tenanter: [], moduler: [] });
  }
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
// `velgSprak` falt til nettleserens språk — «en» i jsdom — og «byttet» til
// engelsk under vært et bytte til samme språk.
document.documentElement.setAttribute("data-sprak", "nb");
const app = document.createElement("div");
app.id = "app";
document.body.replaceChildren(app);

await import("../static/js/app.js");

test("Utlogging forbigår en omstart som allerede venter på svar", async () => {
  // Codex P1: `tilInnlogging` rev ned ruteren, men rørte ikke `omstartNr`.
  // En omstart fra et språkbytte som fortsatt hang i `/v1/utrulling` trodde
  // derfor at den eide flaten, og et svar som ble autorisert like FØR
  // utloggingen kunne komme i mål ETTER den. Da kalte den `visApp` med den
  // utloggede økten: innloggingssiden ble byttet ut med et skall som ser
  // innlogget ut, på øktdata som ikke lenger gjelder.
  assert.ok(await vent(() => visning() === "app"),
    "første last rakk aldri fram til skallet");

  // 1. Brukeren bytter språk. Omstarten kommer til `/v1/utrulling` og parkerer.
  const velger = app.querySelector(".sprakvelger");
  assert.ok(velger, "skallet mangler språkvelgeren");
  velger.value = "en";
  velger.dispatchEvent(new Event("change"));
  assert.ok(await vent(() => utrullingNr === 2),
    "språkbyttets omstart nådde aldri utrullingskallet");

  // 2. Brukeren logger ut mens omstarten står og venter.
  const knapper = [...app.querySelectorAll(".skall-hoyre .knapp")];
  const loggUtKnapp = knapper.find((k) => k.textContent === NB["ui.logg_ut"]);
  assert.ok(loggUtKnapp, "skallet mangler logg ut-knappen");
  loggUtKnapp.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  const bekreft = document.querySelector(".overlegg .knapp.fare");
  assert.ok(bekreft, "bekreftelsesdialogen kom ikke opp");
  bekreft.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  assert.ok(await vent(() => visning() === "landing"),
    "utloggingen nådde aldri innloggingsflaten");
  assert.ok(sesjonSlettet, "økten ble aldri slettet");

  // 3. Det parkerte svaret kommer i mål — etter utloggingen.
  slippUtrulling();
  await vent(() => false, 20);          // gi omstarten rikelig med ticks

  assert.equal(visning(), "landing",
    "en forbigått omstart tegnet skallet over innloggingsflaten etter utlogging");
  assert.equal(app.querySelector(".sprakvelger"), null,
    "skallet står igjen på skjermen etter utlogging");
  assert.ok(app.querySelector(".site-sprak"),
    "innloggingsflaten ble skrevet over");
});
