// Rapportvisningen (038 §7): ekte tabeller med caption/scope, ærligheten
// (manuelle kriterier, avkorting, dekningsbegrensninger) som TEKST i
// rapporten, alvorlighet aldri kun ved farge — og axe rent i alle
// tilstander.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visRapport } from "../static/js/flater/rapport.js";

settI18nForTest(NB, "nb");

const RAPPORT = {
  oppdrag_id: 42, artefakt_id: "a-1", artefakttype: "rapport.wcag.v1",
  promotert_ts: "2026-08-18T10:00:00+00:00",
  rapport: {
    kravsett: "wcag21_aa", regelsett_versjon: "axe-4.10",
    kjort_ts: "2026-08-18T09:59:00+00:00", varighet_ms: 12000,
    sider_kontrollert: [
      { url: "https://kunde.example/", status: "ok" },
      { url: "https://kunde.example/om", status: "feilet" }],
    funn: [{ regel_id: "color-contrast", alvorlighet: "alvorlig",
      antall: 3, eksempler: ["main > p"] }],
    sammendrag: { kritisk: 0, alvorlig: 3, moderat: 0, lav: 0 },
    avkortet: { truffet: true, tak: 500, verdi: 512 },
    dekningsbegrensninger: [{ vert: "cdn.example", antall: 2,
      art: "skript" }],
    miljo: { axe_versjon: "4.10", chromium_versjon: "127",
      container_image_digest: `sha256:${"a".repeat(64)}`,
      viewport: "1280x800", locale: "nb", timezone: "Europe/Oslo" },
    manuelle_kriterier_vurdert: false,
  },
  request_id: "r",
};

let SVAR;
globalThis.fetch = async () => {
  if (typeof SVAR === "number") {
    return { ok: false, status: SVAR, json: async () => ({ feil: "ikke_funnet" }) };
  }
  return { ok: true, status: 200, json: async () => SVAR };
};

function ctx() {
  return { sprak: "nb", scopes: ["decisions:read"], tenant: "acme",
    paaUautorisert: () => {} };
}

function nyHoved() {
  const brett = nyttBrett();
  const m = document.createElement("main");
  m.id = "hovedinnhold"; m.tabIndex = -1;
  brett.append(m);
  return m;
}

async function vent(pred, n = 60) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}

function hentRapport(h, id = "42") {
  const inp = h.querySelector("#rp-oppdrag");
  inp.value = id; inp.dispatchEvent(new window.Event("input"));
  h.querySelector("form").dispatchEvent(
    new window.Event("submit", { cancelable: true }));
}

test("Rapport: tabeller med caption/scope, ærlighet som tekst, axe rent", async () => {
  SVAR = RAPPORT;
  const h = nyHoved();
  visRapport(h, ctx());
  hentRapport(h);
  await vent(() => h.querySelector("table"));
  // Ekte tabellsemantikk (§7)
  const tabeller = h.querySelectorAll("table");
  assert.ok(tabeller.length >= 3, "sider, funn og begrensninger som tabell");
  for (const tab of tabeller) {
    assert.ok(tab.querySelector("caption"), "caption mangler");
    assert.ok(tab.querySelector('th[scope="col"]'), "th scope mangler");
  }
  // Ærligheten er tekst i rapporten — aldri tooltip/fotnote
  assert.ok(h.textContent.includes(t("ui.rapport.manuelle_ikke_vurdert")));
  assert.ok(h.textContent.includes(t("ui.rapport.avkortet")));
  assert.ok(h.textContent.includes("cdn.example"));
  // Alvorlighet som TEKST
  assert.ok(h.textContent.includes(t("alvorlighet.alvorlig")));
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Rapport: 404 → tom-tilstand med forklaring, ikke feil", async () => {
  SVAR = 404;
  const h = nyHoved();
  visRapport(h, ctx());
  hentRapport(h);
  await vent(() => h.querySelector(".tilstand.tom"));
  assert.ok(h.textContent.includes(t("ui.rapport.mangler_tekst")));
});

test("Rapport: et FORELDET svar erstatter ikke en nyere rapport", async () => {
  // Codex P2: skjemaet lar deg sende på nytt mens et svar er underveis.
  // Ber du om A og så B, og A svarer SIST, erstattet A-svaret B-rapporten
  // — mens feltet viste B. Kontroll: fjern generasjonssjekken i
  // rapport.js, så blir denne rød.
  const gammelFetch = globalThis.fetch;
  const slipp = [];                       // én «slipp løs»-funksjon per kall
  globalThis.fetch = (u) =>
    new Promise((res) => slipp.push(() => res({
      ok: true, status: 200,
      json: async () => ({
        ...RAPPORT,
        oppdrag_id: Number(String(u).split("/").pop()),
        rapport: { ...RAPPORT.rapport,
          regelsett_versjon: `axe-for-${String(u).split("/").pop()}` } }),
    })));
  try {
    const h = nyHoved();
    visRapport(h, ctx());
    hentRapport(h, "41");                 // A
    hentRapport(h, "42");                 // B
    await vent(() => slipp.length === 2);
    slipp[1]();                           // B svarer først ...
    await vent(() => h.textContent.includes("axe-for-42"));
    slipp[0]();                           // ... A svarer SIST
    // Gi det foreldede svaret rikelig anledning til å skrive.
    await vent(() => false, 20);
    assert.ok(h.textContent.includes("axe-for-42"),
      "det foreldede A-svaret erstattet B-rapporten");
    assert.ok(!h.textContent.includes("axe-for-41"));
    // ... og B sin ventetilstand ble ryddet av B, ikke stående igjen.
    assert.equal(h.querySelector("form").getAttribute("aria-busy"), null);
  } finally {
    globalThis.fetch = gammelFetch;
  }
});

test("Rapport: ugyldig oppdragsnummer → aria-invalid + fokus, intet kall", async () => {
  let kalt = false;
  SVAR = RAPPORT;
  const gammelFetch = globalThis.fetch;
  globalThis.fetch = async (...a) => { kalt = true; return gammelFetch(...a); };
  const h = nyHoved();
  visRapport(h, ctx());
  hentRapport(h, "0");
  const inp = h.querySelector("#rp-oppdrag");
  assert.equal(inp.getAttribute("aria-invalid"), "true");
  assert.equal(document.activeElement, inp);
  assert.equal(kalt, false);
  globalThis.fetch = gammelFetch;
});
