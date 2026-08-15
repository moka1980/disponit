// Inngangsmodulen `app.js` starter seg selv ved import: den henter locale, økt
// og utrulling, og bygger skallet. Testen driver derfor HELE modulen én gang,
// i rekkefølge, med et fetch-lag som kan holde igjen ett svar om gangen — det
// er nettopp de asynkrone mellomrommene feilen bor i.
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

const SESJON = { tenant: "acme", scopes: ["decisions:read"] };

function svar(kropp) {
  return { ok: true, status: 200, json: async () => kropp };
}

// `/v1/sesjon` GET nummer to holdes igjen til testen slipper den: det er
// vinduet mellom «språkbytte startet» og «språkbytte rendrer», altså der en
// utlogging kan komme imellom.
let sesjonNr = 0;
let slippSesjon = null;
let loggetUt = false;

globalThis.fetch = async (url, opsjoner = {}) => {
  const sti = String(url).split("?")[0];
  const metode = String(opsjoner.method || "GET").toUpperCase();
  if (sti === "/ui/oppsett.json") return svar({ provider_id: "google" });
  const locale = sti.match(/^\/ui\/locale\/(nb|en)$/);
  if (locale) return svar(LOCALER[locale[1]]);
  if (sti === "/v1/sesjon" && metode === "DELETE") {
    loggetUt = true;
    return { ok: true, status: 204, json: async () => null };
  }
  if (sti === "/v1/sesjon") {
    sesjonNr += 1;
    if (sesjonNr === 2) await new Promise((r) => { slippSesjon = r; });
    return svar(SESJON);
  }
  if (sti === "/v1/utrulling") return svar({});
  return { ok: false, status: 404, json: async () => ({ feil: "ukjent" }) };
};

// Sidechromet slik `index.html` serverer det: hoppelenken ligger UTENFOR
// `#app`, og `#app` er det eneste flatene skriver i.
document.documentElement.setAttribute("data-sprak", "nb");
document.body.replaceChildren();
const hoppelenke = document.createElement("a");
hoppelenke.className = "hoppelenke";
hoppelenke.href = "#hovedinnhold";
hoppelenke.textContent = "Hopp til innhold";
const app = document.createElement("div");
app.id = "app";
app.setAttribute("aria-busy", "true");
document.body.append(hoppelenke, app);

async function vent(pred, n = 80) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}

async function hvil(n = 40) {
  for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0));
}

function visning() {
  return document.documentElement.getAttribute("data-visning");
}

// Én sammenhengende scene, fordi modulen bare kan startes én gang per prosess.
test("Utlogging ugyldiggjør et språkbytte som fortsatt venter på økten",
  async () => {
    await import("../static/js/app.js");
    assert.ok(await vent(() => app.querySelector(".skall") !== null),
      "appskallet ble aldri bygget");
    assert.equal(visning(), "app");

    // Språkbytte: `start()` kjøres på nytt og stopper på økt-kallet.
    const velger = app.querySelector(".sprakvelger");
    velger.value = "en";
    velger.dispatchEvent(new Event("change"));
    assert.ok(await vent(() => slippSesjon !== null),
      "språkbyttet nådde aldri økt-kallet");

    // …og imens bekrefter brukeren utlogging. Knappen finnes via DOM-en, ikke
    // via teksten: locale-settet er allerede byttet til engelsk, mens skallet
    // fortsatt står på norsk — nettopp fordi rendringen ikke har skjedd.
    const hoyre = app.querySelector(".skall-hoyre");
    hoyre.querySelector("button.knapp").click();
    const primar = document.querySelector(".overlegg .dialog-bunn button.fare");
    assert.ok(primar, "bekreftelsesdialogen for utlogging kom ikke opp");
    primar.click();
    assert.ok(await vent(() => visning() === "landing"),
      "utloggingen førte aldri til innloggingsflaten");
    assert.ok(loggetUt, "økten ble aldri slettet på serveren");

    // Det utsatte svaret kommer ETTER utloggingen. Uten at `tilInnlogging()`
    // teller opp flategenerasjonen, fortsetter denne oppstarten til
    // `visApp()` og skriver et innlogget skall over innloggingsflaten — med
    // øktdata hentet før utloggingen.
    slippSesjon();
    await hvil();
    assert.equal(visning(), "landing",
      "en forbigått oppstart tok over flaten etter utlogging");
    assert.equal(app.querySelector(".skall"), null,
      "appskallet ble rendret over innloggingsflaten etter utlogging");
    assert.ok(app.querySelector(".site-hero"),
      "innloggingsflaten ble borte");
  });
