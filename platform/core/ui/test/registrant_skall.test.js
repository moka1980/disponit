// SKALLET FOR EN REGISTRANT — veien et nytt menneske faktisk går.
//
// EIERS FUNN var at maskineriet virket, men veien ikke fantes: «hvor kan
// man registrere ny kunde?» Denne porten måler det neste leddet i samme
// kjede — at hun som klikker «Registrer bedriften» på den offentlige siden
// faktisk KOMMER FRAM.
//
// Kjeden har ett ledd som ikke er åpenbart: skallet henter `/v1/utrulling`
// ved oppstart, og den ruten krever `decisions:read`. En registrant har
// BARE `firma:opprett`, så kallet svarer 403.
//
// Det bærer fordi `_kast` gjør 403 til `IngenTilgangFeil` og ikke
// `UautorisertFeil` — bare den siste kastes videre av
// `hentUtrullingForSkall`. Svarte utrullingen 401 i stedet, ville hun blitt
// LOGGET UT midt i registreringen, og ingenting hadde sagt hvorfor.
//
// Det er ikke planlagt for registranten; det er en eldre beslutning som
// tilfeldigvis bærer. Derfor måles den her, så den ikke kan endres i stillhet.
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

// Nøyaktig det callbacken gir henne: den reserverte tenanten og ETT scope.
const REGISTRANT = { tenant: "_registrering", scopes: ["firma:opprett"],
  bruker_id: "bid_ny", roller: ["registrant"] };

let utrullingKall = 0;

globalThis.fetch = async (url, opsjoner = {}) => {
  const sti = String(url).split("?")[0];
  const locale = sti.match(/^\/ui\/locale\/(nb|en)$/);
  if (locale) return svar(LOCALER[locale[1]]);
  if (sti === "/ui/oppsett.json") return svar({ provider_id: "google" });
  if (sti === "/v1/sesjon") return svar(REGISTRANT);
  if (sti === "/v1/utrulling") {
    utrullingKall += 1;
    // DET SERVEREN FAKTISK SVARER en registrant: scopet mangler.
    return svar({ feil: "scope_mangler" }, 403);
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

test("Registrant: skallet bygges, og hun lander på registreringsflaten",
     async () => {
  document.body.replaceChildren();
  const app = document.createElement("div");
  app.id = "app";
  document.body.append(app);
  window.history.replaceState({}, "", "/?visning=firmaregistrering&sprak=nb");

  await import("../static/js/app.js");
  await vent(() => app.textContent.includes(NB["ui.firmareg.tittel"]));

  // 1. Utrullingen BLE forsøkt, og 403-en drepte ikke oppstarten.
  assert.ok(utrullingKall >= 1, "skallet spurte aldri om utrulling");
  assert.ok(app.querySelector("form"),
    "registreringsskjemaet ble aldri tegnet — 403 fra utrullingen stoppet "
    + "oppstarten, eller dyplenken traff ikke ruten");

  // 2. Hun står på registreringsflaten, ikke på et tomt skall.
  assert.ok(app.textContent.includes(NB["ui.firmareg.navn"]));
  assert.ok(app.querySelector("#firmareg-bransje"));

  // 3. Og hun ble IKKE logget ut. Var 403 blitt behandlet som 401, ville
  //    `hentUtrullingForSkall` kastet videre og sparket henne ut midt i
  //    registreringen — uten å si hvorfor.
  assert.ok(!app.textContent.includes(NB["site.login.tittel"]),
    "registranten ble sendt tilbake til innlogging");
});
