// Språkbyttet er ASYNKRONT, og velgeren står åpen mens locale-settet hentes.
// To valg kan derfor være i lufta samtidig, og de kan lande i motsatt
// rekkefølge av at de ble gjort. Testene her holder på den ene regelen som
// gjør det trygt: bare den SISTE lastingen får skrive til modulens tilstand.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { NB } from "./hjelp.js";
import { lastI18n, settI18nForTest, sprak, t } from "../static/js/i18n.js";

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

test("lastI18n: en forbigått lasting skriver ikke over det nyeste valget", async () => {
  // Codex P2: `_sprak` ble satt FØR hentingen og `_kart` av den som kom sist
  // i mål. Valgte brukeren engelsk på en treg linje og så norsk, rendret norsk
  // ferdig — og deretter landet engelsk og satte `_kart` og `<html lang>` til
  // engelsk under en side som sto på norsk.
  settI18nForTest(NB, "nb");
  document.documentElement.setAttribute("lang", "nb");
  const rigg = riggTregEn();
  try {
    const forst = lastI18n("en");   // brukerens første valg — henger
    const sist = lastI18n("nb");    // brukeren ombestemmer seg

    assert.equal(await sist, "nb", "det siste valget ble ikke tatt i bruk");
    rigg.slipp();
    assert.equal(await forst, null,
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

test("lastI18n: den siste lastingen gjelder også når den kommer sist", async () => {
  // Motprøven: samme rigg, men det TREGE valget er det siste. Da skal det
  // vinne — vernet skal ikke låse språket til det som tilfeldigvis er raskt.
  settI18nForTest(NB, "nb");
  document.documentElement.setAttribute("lang", "nb");
  const rigg = riggTregEn();
  try {
    const forst = lastI18n("nb");
    const sist = lastI18n("en");

    assert.equal(await forst, null, "det forlatte valget ble tatt i bruk");
    rigg.slipp();
    assert.equal(await sist, "en", "det siste valget nådde aldri fram");

    assert.equal(sprak(), "en");
    assert.equal(t("ui.sprak.nb"), EN["ui.sprak.nb"]);
    assert.equal(document.documentElement.getAttribute("lang"), "en");
  } finally {
    rigg.gjenopprett();
    settI18nForTest(NB, "nb");
  }
});
