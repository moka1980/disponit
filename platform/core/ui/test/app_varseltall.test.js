// Skallets varselteller. Egen fil fordi `app.js` starter seg selv ved import
// og holder modultilstanden — ett scenario per prosess, slik `node --test`
// kjører filene. Riggen må stå ferdig før importen, og importen er dynamisk.
//
// 🔴 Funnet: `visApp` bygde AppShell UTEN `varsler`, så statusfeltet sa
// «Varseltall ikke tilgjengelig» i hver eneste økt — også når `/v1/varsel`
// hadde uleste attesteringer å melde. Den som hadde valgt «kun portal» satt
// dermed helt uten proaktiv beskjed: eneste vei til å finne ut om noe ventet,
// var å åpne varselflaten og se etter.
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

// `policy:write` er det minste som gir varselruten (`kanForvaltePolicy`), og
// det gir samtidig INGEN av basisrutene utenom den scope-frie `kundeadmin` —
// som ikke kaller noe endepunkt. Skallet blir dermed ferdig uten at riggen må
// svare for en flate, og det er skallet denne testen handler om.
const SESJON = { tenant: "acme", scopes: ["policy:write"] };

let varselkall = 0;
let uleste = 3;

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

async function vent(pred, n = 200) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}

const status = () => (document.querySelector(".skall-status") || {}).textContent;

document.documentElement.setAttribute("data-sprak", "nb");
const app = document.createElement("div");
app.id = "app";
document.body.replaceChildren(app);

await import("../static/js/app.js");

test("Skallet henter og viser antallet uleste varsler", async () => {
  await vent(() => document.documentElement.getAttribute("data-visning") === "app");
  const ventet = NB["ui.shell.status_varsler"].replace("{antall}", "3");
  assert.ok(await vent(() => (status() || "").includes(ventet)),
    `statuslinja sier «${status()}» — den skulle sagt «${ventet}»`);
  // …og den sier det i stedet for «ikke tilgjengelig», ikke i tillegg til.
  assert.ok(!status().includes(NB["ui.shell.status_varsler_ukjent"]),
    "statuslinja bærer både et tall og «ikke tilgjengelig»");
  assert.equal(varselkall, 1, "skallet kalte /v1/varsel flere ganger");
});
