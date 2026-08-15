// Språkbyttet er asynkront, og tilstanden i `i18n.js` er delt av hele flaten.
// To bytter i rask rekkefølge — en bruker som ombestemmer seg, eller bare
// dobbeltklikker — ga to hentinger som kunne svare i motsatt rekkefølge av
// den de ble bedt om. Testene her låser at det er det SISTE valget som gjelder,
// uansett hvem som svarer først (Codex P2 til PR #42).
import test from "node:test";
import assert from "node:assert/strict";
import "./hjelp.js";
import { lastI18n, t, sprak, settI18nForTest } from "../static/js/i18n.js";

// Et locale-svar som lar seg forsinke: det er nettopp rekkefølgen som er
// poenget, ikke innholdet.
function svar(kart, { ok = true, status = 200, forsinkelse = 0 } = {}) {
  return new Promise((r) => setTimeout(
    () => r({ ok, status, json: async () => kart }), forsinkelse));
}

function medFetch(rigg, fn) {
  const ekte = globalThis.fetch;
  globalThis.fetch = rigg;
  return Promise.resolve().then(fn).finally(() => { globalThis.fetch = ekte; });
}

function kodeFor(url) {
  return String(url).endsWith("/en") ? "en" : "nb";
}

test("lastI18n: det siste valget vinner selv om det første svarer sist", async () => {
  // Brukeren trykker «English», ombestemmer seg og trykker «Norsk». Det
  // forlatte engelske svaret kommer først TILBAKE — og skrev før dette både
  // kartet, `sprak()` og `<html lang>`, altså engelsk tekst under en side som
  // sa den var norsk.
  await medFetch(
    (url) => svar({ "ui.hei": kodeFor(url) === "en" ? "Hello" : "Hei" },
                  { forsinkelse: kodeFor(url) === "en" ? 30 : 0 }),
    async () => {
      const forlatt = lastI18n("en");
      const gjeldende = lastI18n("nb");

      assert.equal(await gjeldende, "nb");
      // Det forbigåtte kallet får vite hva som FAKTISK gjelder, ikke hva det ba om.
      assert.equal(await forlatt, "nb");

      assert.equal(sprak(), "nb");
      assert.equal(t("ui.hei"), "Hei");
      assert.equal(document.documentElement.getAttribute("lang"), "nb");
      assert.equal(document.documentElement.getAttribute("data-sprak"), "nb");
    });
});

test("lastI18n: et forbigått kall rører ingenting når det svarer raskest", async () => {
  // Samme kappløp, motsatt utfall på nettverket: det forlatte svaret kommer
  // sist. Da må det heller ikke skrive over det brukeren endte på.
  await medFetch(
    (url) => svar({ "ui.hei": kodeFor(url) === "en" ? "Hello" : "Hei" },
                  { forsinkelse: kodeFor(url) === "en" ? 0 : 30 }),
    async () => {
      const forlatt = lastI18n("en");
      const gjeldende = lastI18n("nb");

      await Promise.all([forlatt, gjeldende]);
      assert.equal(sprak(), "nb");
      assert.equal(t("ui.hei"), "Hei");
      assert.equal(document.documentElement.getAttribute("lang"), "nb");
    });
});

test("lastI18n: en FEIL på et forbigått kall feller ikke det gjeldende språket", async () => {
  // Et tregt 500-svar på et språk ingen venter på lenger skal ikke kaste:
  // kalleren i `app.js` fanger et kast og faller til innloggingsflaten, og
  // den ville da revet ned appen som nettopp rendret riktig.
  await medFetch(
    (url) => (kodeFor(url) === "en"
      ? svar({}, { ok: false, status: 500, forsinkelse: 30 })
      : svar({ "ui.hei": "Hei" })),
    async () => {
      const forlatt = lastI18n("en");
      assert.equal(await lastI18n("nb"), "nb");

      await assert.doesNotReject(() => forlatt);
      assert.equal(sprak(), "nb");
      assert.equal(t("ui.hei"), "Hei");
    });
});

test("lastI18n: et ekte, IKKE forbigått feilsvar kaster fortsatt", async () => {
  // Guarden skal dempe kappløp, ikke svelge feil. Er kallet det nyeste, må et
  // 500-svar fortsatt nå kalleren.
  await medFetch(
    () => svar({}, { ok: false, status: 500 }),
    async () => {
      await assert.rejects(() => lastI18n("en"), /locale en: 500/);
    });
});

test("settI18nForTest: en henting underveis kan ikke overskrive testkartet", async () => {
  await medFetch(
    () => svar({ "ui.hei": "Hello" }, { forsinkelse: 20 }),
    async () => {
      const underveis = lastI18n("en");
      settI18nForTest({ "ui.hei": "Rigget" }, "nb");

      await underveis;
      assert.equal(sprak(), "nb");
      assert.equal(t("ui.hei"), "Rigget");
    });
});
