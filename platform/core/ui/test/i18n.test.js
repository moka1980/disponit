// Språkbyttet er ASYNKRONT, og velgeren står åpen mens locale-settet hentes.
// To valg kan derfor være i lufta samtidig, og de kan lande i motsatt
// rekkefølge av at de ble gjort. Testene her holder på de to reglene som gjør
// det trygt: bare den SISTE lastingen får skrive til modulens tilstand, og
// ingen lasting skriver før kalleren tar den i bruk.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { NB } from "./hjelp.js";
import { hentI18n, settI18nForTest, sprak, t } from "../static/js/i18n.js";

const HER = dirname(fileURLToPath(import.meta.url));
const EN = JSON.parse(readFileSync(
  join(HER, "..", "..", "..", "..", "locales", "en.json"), "utf-8"));
const LOCALER = { nb: NB, en: EN };

// Locale-svar med en bremse: `en` henger til testen slipper den, `nb` går rett
// gjennom. Det er nøyaktig formen på en treg linje der det FØRSTE valget kommer
// sist i mål.
function riggTregEn() {
  const ekte = globalThis.fetch;
  let slipp;
  const holdt = new Promise((r) => { slipp = r; });
  globalThis.fetch = async (url) => {
    const m = String(url).match(/\/ui\/locale\/(nb|en)$/);
    if (!m) return { ok: false, status: 404, json: async () => ({}) };
    if (m[1] === "en") await holdt;
    return { ok: true, status: 200, json: async () => LOCALER[m[1]] };
  };
  return { slipp, gjenopprett: () => { globalThis.fetch = ekte; } };
}

test("hentI18n: en forbigått lasting skriver ikke over det nyeste valget", async () => {
  // Codex P2: `_sprak` ble satt FØR hentingen og `_kart` av den som kom sist
  // i mål. Valgte brukeren engelsk på en treg linje og så norsk, rendret norsk
  // ferdig — og deretter landet engelsk og satte `_kart` og `<html lang>` til
  // engelsk under en side som sto på norsk.
  settI18nForTest(NB, "nb");
  document.documentElement.setAttribute("lang", "nb");
  const rigg = riggTregEn();
  try {
    const forst = hentI18n("en");   // brukerens første valg — henger
    const sist = hentI18n("nb");    // brukeren ombestemmer seg

    assert.equal((await sist).taIBruk(), "nb",
      "det siste valget ble ikke tatt i bruk");
    rigg.slipp();
    assert.equal((await forst).taIBruk(), null,
      "den forbigåtte lastingen meldte seg som gjeldende");

    assert.equal(sprak(), "nb", "`sprak()` endte på det forlatte valget");
    assert.equal(t("ui.sprak.nb"), NB["ui.sprak.nb"],
      "locale-kartet ble skrevet over av den forbigåtte lastingen");
    assert.equal(document.documentElement.getAttribute("lang"), "nb",
      "<html lang> endte på det forlatte valget");
  } finally {
    rigg.gjenopprett();
    settI18nForTest(NB, "nb");
  }
});

test("hentI18n: den siste lastingen gjelder også når den kommer sist", async () => {
  // Motprøven: samme rigg, men det TREGE valget er det siste. Da skal det
  // vinne — vernet skal ikke låse språket til det som tilfeldigvis er raskt.
  settI18nForTest(NB, "nb");
  document.documentElement.setAttribute("lang", "nb");
  const rigg = riggTregEn();
  try {
    const forst = hentI18n("nb");
    const sist = hentI18n("en");

    assert.equal((await forst).taIBruk(), null,
      "det forlatte valget ble tatt i bruk");
    rigg.slipp();
    assert.equal((await sist).taIBruk(), "en",
      "det siste valget nådde aldri fram");

    assert.equal(sprak(), "en");
    assert.equal(t("ui.sprak.nb"), EN["ui.sprak.nb"]);
    assert.equal(document.documentElement.getAttribute("lang"), "en");
  } finally {
    rigg.gjenopprett();
    settI18nForTest(NB, "nb");
  }
});

test("hentI18n: settet er hentet, men ingenting er endret før det tas i bruk", async () => {
  // Codex P2: hentingen commitet `_kart` og `<html lang>` i samme øyeblikk
  // svaret kom, mens flaten som skulle BÆRE språket først ble byttet etter
  // kallerens neste ventepunkt — oppsettet på forsiden, økt og utrulling i
  // skallet. I det gapet sto en norsk side merket `lang="en"`, og hang det
  // neste kallet, sto den slik på ubestemt tid. Hentingen skal derfor ikke
  // røre noe: den bærer settet, og kalleren tar det i bruk når flaten er klar.
  settI18nForTest(NB, "nb");
  document.documentElement.setAttribute("lang", "nb");
  const rigg = riggTregEn();
  rigg.slipp();                     // ingen brems: begge språk svarer med én gang
  try {
    const hentet = await hentI18n("en");
    assert.equal(hentet.sprak, "en",
      "hentingen sier ikke hvilket språk den bærer — kalleren trenger det til "
      + "utrullingskallet før språket er tatt i bruk");
    assert.equal(sprak(), "nb",
      "hentingen endret språket før noen hadde tatt det i bruk");
    assert.equal(t("ui.sprak.nb"), NB["ui.sprak.nb"],
      "locale-kartet ble byttet av hentingen alene");
    assert.equal(document.documentElement.getAttribute("lang"), "nb",
      "<html lang> ble byttet av hentingen alene — flaten står fortsatt på nb");

    assert.equal(hentet.taIBruk(), "en", "ibruktakingen tok ikke språket i bruk");
    assert.equal(sprak(), "en");
    assert.equal(t("ui.sprak.nb"), EN["ui.sprak.nb"]);
    assert.equal(document.documentElement.getAttribute("lang"), "en");
  } finally {
    rigg.gjenopprett();
    settI18nForTest(NB, "nb");
    document.documentElement.setAttribute("lang", "nb");
  }
});
