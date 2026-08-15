// Vinduet mellom «språkbytte startet» og «locale-settet kom» er der en
// utlogging kan komme imellom. `app.test.js` holder igjen ØKT-kallet; her
// holdes LOCALE-kallet, som ligger enda tidligere i `start()` — før den i det
// hele tatt har rukket å sjekke flategenerasjonen sin. Modulen starter seg
// selv ved import og kan bare startes én gang per prosess, så scenen er én
// sammenhengende test i sin egen fil.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { NB } from "./hjelp.js";
import { sprak, t } from "../static/js/i18n.js";

const HER = dirname(fileURLToPath(import.meta.url));
const EN = JSON.parse(readFileSync(
  join(HER, "..", "..", "..", "..", "locales", "en.json"), "utf-8"));
const LOCALER = { nb: NB, en: EN };

const SESJON = { tenant: "acme", scopes: ["decisions:read"] };

function svar(kropp) {
  return { ok: true, status: 200, json: async () => kropp };
}

// `/ui/locale/en` holdes igjen til testen slipper det.
let slippLocale = null;
let loggetUt = false;

globalThis.fetch = async (url, opsjoner = {}) => {
  const sti = String(url).split("?")[0];
  const metode = String(opsjoner.method || "GET").toUpperCase();
  if (sti === "/ui/oppsett.json") return svar({ provider_id: "google" });
  const locale = sti.match(/^\/ui\/locale\/(nb|en)$/);
  if (locale) {
    if (locale[1] === "en") await new Promise((r) => { slippLocale = r; });
    return svar(LOCALER[locale[1]]);
  }
  if (sti === "/v1/sesjon" && metode === "DELETE") {
    loggetUt = true;
    return { ok: true, status: 204, json: async () => null };
  }
  if (sti === "/v1/sesjon") return svar(SESJON);
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

test("Utlogging ugyldiggjør en språkhenting som fortsatt er underveis",
  async () => {
    await import("../static/js/app.js");
    assert.ok(await vent(() => app.querySelector(".skall") !== null),
      "appskallet ble aldri bygget");
    assert.equal(visning(), "app");

    // Språkbytte til engelsk: `start("en")` stopper på locale-kallet, altså
    // FØR `lokaliserSkiplenke()`, økten og skallet.
    const velger = app.querySelector(".sprakvelger");
    velger.value = "en";
    velger.dispatchEvent(new Event("change"));
    assert.ok(await vent(() => slippLocale !== null),
      "språkbyttet nådde aldri locale-kallet");

    // …og imens bekrefter brukeren utlogging.
    const hoyre = app.querySelector(".skall-hoyre");
    hoyre.querySelector("button.knapp").click();
    const primar = document.querySelector(".overlegg .dialog-bunn button.fare");
    assert.ok(primar, "bekreftelsesdialogen for utlogging kom ikke opp");
    primar.click();
    assert.ok(await vent(() => visning() === "landing"),
      "utloggingen førte aldri til innloggingsflaten");
    assert.ok(loggetUt, "økten ble aldri slettet på serveren");
    assert.equal(document.documentElement.getAttribute("lang"), "nb",
      "innloggingsflaten ble ikke rendret på det gjeldende språket");

    // Locale-settet kommer ETTER utloggingen. Det tilhører en flate som ikke
    // finnes lenger, og har ingenting å gjøre i det globale kartet: flaten på
    // skjermen er norsk, og et engelsk kart ville merket den engelsk og gitt
    // alle senere oppslag tekster som ikke passer til det som står der.
    slippLocale();
    await hvil();
    assert.equal(visning(), "landing",
      "en forbigått oppstart tok over flaten etter utlogging");
    assert.equal(document.documentElement.getAttribute("lang"), "nb",
      "den norske innloggingsflaten ble merket med et annet språk");
    assert.equal(document.documentElement.getAttribute("data-sprak"), "nb",
      "data-sprak fulgte en språkhenting ingen flate ventet på");
    assert.equal(sprak(), "nb",
      "en forbigått språkhenting committet språket sitt");
    assert.equal(t("site.hero.tittel"), NB["site.hero.tittel"],
      "tekstkartet passer ikke lenger til flaten som står rendret");
    assert.equal(
      app.querySelector(".site-hero h1").textContent,
      NB["site.hero.tittel"],
      "innloggingsflaten står ikke på norsk");
    assert.equal(hoppelenke.textContent, NB["ui.hopp_til_innhold"],
      "hoppelenken fulgte en forbigått språkhenting");
  });
