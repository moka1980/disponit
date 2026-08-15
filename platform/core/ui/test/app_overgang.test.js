// Overgangen TIL innlogging har sitt eget ventepunkt. Egen fil fordi `app.js`
// starter seg selv ved import og holder modultilstanden (`omstartNr`,
// `aktivRuter`) — ett scenario per prosess, slik `node --test` kjører filene.
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

// `decisions:read` gir `oversikt` som første rute, altså en flate med et ekte
// API-kall bak seg — det er den som kan svare 401 og sende oss til innlogging.
const SESJON = { tenant: "acme", scopes: ["decisions:read"] };
const OVERSIKT = { tillatt: 0, stoppet: 0, unntak: 0, totalt: 0,
  vindu_slutt: "2026-08-15T12:00:00Z", tidssone: "UTC" };

let oversiktNr = 0;
let oppsettNr = 0;
let slippOppsett = () => {};
const oppsettHoldt = new Promise((r) => { slippOppsett = r; });

globalThis.fetch = async (url) => {
  const sti = String(url).split("?")[0];
  const locale = sti.match(/^\/ui\/locale\/(nb|en)$/);
  if (locale) return svar(LOCALER[locale[1]]);
  if (sti === "/ui/oppsett.json") {
    // Innloggingsflatens eget ventepunkt: overgangen parkeres her.
    if (++oppsettNr === 1) await oppsettHoldt;
    return svar({ provider_id: "google" });
  }
  if (sti === "/v1/sesjon") return svar(SESJON);
  if (sti === "/v1/utrulling") return svar({ tenanter: [], moduler: [] });
  if (sti === "/v1/oversikt") {
    // Ett blaff: første kall er 401 og sender flaten til innlogging, økten selv
    // er i behold. Neste last av samme flate går som normalt.
    if (++oversiktNr === 1) return svar({ feil: "uautorisert" }, 401);
    return svar(OVERSIKT);
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

document.documentElement.setAttribute("data-sprak", "nb");
const app = document.createElement("div");
app.id = "app";
document.body.replaceChildren(app);

await import("../static/js/app.js");

test("En forbigått overgang tegner ikke innlogging over en nyere omstart", async () => {
  // `tilInnlogging` teller opp `omstartNr` for å forbigå alt som er underveis,
  // men den venter SELV på `/ui/oppsett.json` før den tegner. Uten at den
  // bærer sitt eget nummer med inn i `visInnlogging`, tegner en overgang som i
  // mellomtiden er blitt forbigått innloggingsflaten over skallet et nyere
  // språkvalg nettopp bygde — og økten er fortsatt gyldig, så brukeren blir
  // bedt om å logge inn på nytt uten grunn.
  assert.ok(await vent(() => oppsettNr === 1),
    "401 fra oversiktsflaten sendte aldri overgangen til innlogging");
  assert.equal(visning(), "app",
    "skallet skulle stått urørt mens overgangen venter på oppsettet");

  // Skallet står fortsatt på skjermen mens overgangen venter: brukeren bytter
  // språk, og den omstarten kommer helt fram.
  const velger = app.querySelector(".sprakvelger");
  assert.ok(velger, "skallet mangler språkvelgeren");
  velger.value = "en";
  velger.dispatchEvent(new Event("change"));
  assert.ok(await vent(() => document.documentElement.getAttribute("lang") === "en"
    && oversiktNr === 2),
    "språkbyttets omstart nådde aldri fram til skallet");

  // Den parkerte overgangen kommer i mål — etter at den ble forbigått.
  slippOppsett();
  await vent(() => false, 20);

  assert.equal(visning(), "app",
    "en forbigått overgang tegnet innloggingsflaten over et nyere språkvalg");
  assert.equal(app.querySelector(".site-sprak"), null,
    "innloggingsflaten står på skjermen selv om økten er i behold");
  assert.ok(app.querySelector(".sprakvelger"), "skallet ble skrevet over");
});
