// Skallets varselteller — FERSKHET. Egen fil fordi `app.js` starter seg selv
// ved import og holder modultilstanden; ett scenario per prosess, slik
// `node --test` kjører filene.
//
// 🔴 Funnet: `oppdaterVarseltall` hadde ingen generasjonssjekk. Kallene kommer
// fra flere kanter — oppstarten, hver navigasjon, og varselflaten hver gang
// den merker noe lest eller melder av — og de kan overlappe. Oppstartens kall
// kunne lese `uleste=3`, innboksen merke ett lest og skrive 2, og så kunne det
// trege førstekallet legge 3 tilbake oppå. Feltet sto da og hevdet at noe
// ventet på en bruker som nettopp hadde ryddet det bort.
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
// Førstekallet holdes igjen til testen slipper det. Det er hele riggen: uten
// en rekkefølge vi selv bestemmer, måler testen tilfeldig planlegging.
let slippForste = null;
const forsteSluppet = new Promise((r) => { slippForste = r; });

globalThis.fetch = async (url) => {
  const sti = String(url).split("?")[0];
  const locale = sti.match(/^\/ui\/locale\/(nb|en)$/);
  if (locale) return svar(LOCALER[locale[1]]);
  if (sti === "/ui/oppsett.json") return svar({ provider_id: "google" });
  if (sti === "/v1/sesjon") return svar(SESJON);
  if (sti === "/v1/utrulling") return svar({ tenanter: [], moduler: [] });
  if (sti === "/v1/varsel") {
    varselkall += 1;
    if (varselkall === 1) {
      await forsteSluppet;
      return svar({ varsler: [], uleste: 3, kanal: "kun_portal" });
    }
    return svar({ varsler: [], uleste: 1, kanal: "kun_portal" });
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

test("Et tregt varseltall skriver ikke over et ferskere", async () => {
  await vent(() => document.documentElement.getAttribute("data-visning") === "app");
  // Oppstartens kall er ute og henger. Feltet har ennå ikke noe tall.
  assert.equal(varselkall, 1, "oppstarten kalte /v1/varsel feil antall ganger");

  // En navigasjon utløser et NYERE kall, som svarer med det lavere tallet.
  window.location.hash = "#/kundeadmin";
  assert.ok(await vent(() => varselkall === 2),
    "navigasjonen hentet ikke varseltallet på nytt");
  assert.ok(await vent(() => (status() || "").includes(sier(1))),
    `statuslinja sier «${status()}» — det ferske tallet kom aldri fram`);

  // …og så slipper vi det gamle svaret løs. Det skal ikke få skrive.
  slippForste();
  await new Promise((r) => setTimeout(r, 20));
  assert.ok((status() || "").includes(sier(1)),
    `det trege svaret skrev over det ferske: «${status()}»`);
  assert.ok(!(status() || "").includes(sier(3)),
    `statuslinja bærer det utdaterte tallet: «${status()}»`);
});
