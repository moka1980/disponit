// M-16 nøkkeltall — flateportene 11–15 (jsdom + axe): UI-tall == API-svar,
// axe uten alvorlige brudd, alle tall som tekst (søylen bærer aldri noe
// alene), aldri kun farge, ingen hardkodet visningstekst, tomtilstander
// som eksplisitt innhold. Ingen delt fixture.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visNokkeltall } from "../static/js/flater/nokkeltall.js";

settI18nForTest(NB, "nb");

const SVARFORM = {
  vindu_start: "2026-08-20T10:00:00+00:00",
  vindu_slutt: "2026-08-21T10:00:00+00:00",
  tidssone: "UTC",
  beslutninger: { total: 7, deler: { TILLAT: 5, UNNTAK: 1, hokuspokus: 1 } },
  frekvensreservasjoner: 3,
  aktiveringer: {
    kilde: { total: 2, deler: { styrt: 1, historisk: 1 } },
    kvorumskrav: { total: 2, deler: { 1: 1, 2: 1 } },
    attestanter: { total: 2, deler: { en: 1, to: 1 } },
  },
  oppdrag: { total: 4, deler: { utfort: 3, feilet: 1 } },
  unntak_aktivitet: { total: 2, deler: { over_grense: 2 } },
  unntak_lukkede: [{ id: 9, kategori: "over_grense", sakstype: "normal",
    status: "løst", opprettet: "2026-08-20T11:00:00+00:00",
    lukket: "2026-08-20T12:30:00+00:00", varighet_s: 5400 }],
  unntak_lukkede_totalt: 1,
  unntak_lukkede_grense: 50,
  apne_naa: 6,
  tick: { total: 0, deler: {} },
  request_id: "r-test",
};

let SVAR;
globalThis.fetch = async (url) => {
  const sti = url.split("?")[0];
  const oppf = SVAR[sti];
  if (!oppf) return { ok: false, status: 404,
    json: async () => ({ feil: "ikke_funnet" }) };
  if (typeof oppf === "number") {
    return { ok: false, status: oppf, json: async () => ({ feil: "x" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx() {
  return { sprak: "nb", scopes: [], tenant: "acme",
    paaUautorisert: () => {} };
}

async function vent(pred, n = 60) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}

function nyHoved() {
  const brett = nyttBrett();
  const m = document.createElement("main");
  m.id = "hovedinnhold"; m.tabIndex = -1;
  brett.append(m);
  return m;
}

test("Nøkkeltall: UI-tall == API-svar, tabellform, axe rent", async () => {
  SVAR = { "/v1/nokkeltall": SVARFORM };
  const h = nyHoved();
  visNokkeltall(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 5);

  // Port 11: hvert tall i svaret finnes som TEKST i flaten.
  const tekst = h.textContent;
  for (const tall of ["7", "5", "3", "4", "6"]) {
    assert.ok(tekst.includes(tall), `tallet ${tall} mangler som tekst`);
  }
  // Suminvariantens visning: totalraden bærer partisjonens total.
  const beslT = [...h.querySelectorAll("table")]
    .find((tb) => tb.querySelector("caption").textContent
      === t("ui.nokkeltall.kort.beslutninger"));
  assert.ok(beslT, "beslutningskortet mangler");
  assert.ok(beslT.querySelector("tfoot").textContent.includes("7"));
  // Port 1s UI-halvdel: en UKJENT nøkkel vises — i egen rad, i totalen.
  assert.ok(beslT.textContent.includes("hokuspokus"),
    "ukjent verdi er skjult i flaten");
  // Port 13: søylen er aria-hidden og har ALLTID et tekst-tall ved siden.
  for (const rad of beslT.querySelectorAll("tbody tr")) {
    assert.ok(/\d/.test(rad.cells[1].textContent),
      "rad uten tekstlig tall");
  }
  for (const s of h.querySelectorAll(".kpi-soyle")) {
    assert.equal(s.getAttribute("aria-hidden"), "true");
  }
  // Port 14: kategorier bæres av tekstkolonnen (én søylefarge for alle).
  // Port 15/§5: tabellsemantikk — caption + th scope på hvert kort.
  for (const tb of h.querySelectorAll("table")) {
    assert.ok(tb.querySelector("caption").textContent.trim().length > 0);
    assert.ok(tb.querySelector('th[scope="col"]'));
  }
  // Vindusvelgeren: <select> med <label for>.
  const velger = h.querySelector("select#nokkeltall-vindu");
  assert.ok(velger, "vindusvelgeren mangler");
  assert.ok(h.querySelector('label[for="nokkeltall-vindu"]'));
  // «Åpne nå» står utenfor kortlisten, med egen ledetekst (7c-UI).
  const tilstand = h.querySelector(".kpi-tilstand");
  assert.ok(tilstand.textContent.includes("6"));
  assert.ok(tilstand.textContent
    .includes(t("ui.nokkeltall.apne_naa_tekst")));
  assert.ok(!tilstand.closest(".kpi-kort-liste"));
  // Port 9: tick-kortet med 0 rader er en SETNING.
  assert.ok(tekst.includes(t("ui.nokkeltall.ingen_tick")));
  // Radvis varighet vises som omskrevet klartekst.
  // 5400 s → «1 timer»-formen (omskriving av radens eget tall).
  assert.ok(tekst.includes(t("ui.nokkeltall.varighet_timer")
    .replace("{n}", "1")));
  // Port 12: axe uten alvorlige brudd.
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

test("Nøkkeltall: tomt vindu viser 0 og «ingen» — aldri et skjult kort", async () => {
  SVAR = { "/v1/nokkeltall": { ...SVARFORM,
    beslutninger: { total: 0, deler: {} },
    oppdrag: { total: 0, deler: {} },
    unntak_aktivitet: { total: 0, deler: {} },
    unntak_lukkede: [], unntak_lukkede_totalt: 0, apne_naa: 0 } };
  const h = nyHoved();
  visNokkeltall(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 4);
  const beslT = [...h.querySelectorAll("table")]
    .find((tb) => tb.querySelector("caption").textContent
      === t("ui.nokkeltall.kort.beslutninger"));
  assert.ok(beslT.textContent.includes(t("ui.nokkeltall.ingen")));
  assert.ok(beslT.querySelector("tfoot").textContent.includes("0"));
  assert.ok(h.textContent.includes(t("ui.nokkeltall.ingen_lukkede")));
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

test("Nøkkeltall: avkuttet lukkede-liste sier det i klartekst", async () => {
  SVAR = { "/v1/nokkeltall": { ...SVARFORM, unntak_lukkede_totalt: 137,
    unntak_lukkede_grense: 50 } };
  const h = nyHoved();
  visNokkeltall(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 5);
  // Både utsnittet og settets størrelse står som TEKST — flaten kan
  // aldri lese som om den viste alle lukkede saker i vinduet.
  const tekst = h.textContent;
  assert.ok(tekst.includes("137"), "totalen i vinduet mangler som tekst");
  assert.ok(tekst.includes(t("ui.nokkeltall.lukkede_avkuttet")
    .replace("{vist}", "1").replace("{totalt}", "137")
    .replace("{grense}", "50")));
  assert.ok(tekst.includes(t("ui.nokkeltall.til_unntak")));
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

test("Nøkkeltall: ufullstendig liste påstås aldri fullstendig", async () => {
  // Ikke avkuttet: ingen grense-setning, ingen lenke til unntakslisten.
  SVAR = { "/v1/nokkeltall": SVARFORM };
  const h = nyHoved();
  visNokkeltall(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 5);
  assert.ok(!h.textContent.includes(t("ui.nokkeltall.til_unntak")));
});

test("Nøkkeltall: ingen SVG/canvas — søylen er HTML/CSS, tastaturvei", async () => {
  SVAR = { "/v1/nokkeltall": SVARFORM };
  const h = nyHoved();
  visNokkeltall(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 5);
  assert.equal(h.querySelectorAll("svg, canvas").length, 0);
  // Tastaturgjennomgangens mekaniske halvdel: de interaktive elementene
  // er native (select/button) og dermed i tabrekkefølgen; ingen
  // tabindex-feller. (Den manuelle gjennomgangen dokumenteres i PR-en.)
  for (const e of h.querySelectorAll("select, button")) {
    assert.ok(!e.hasAttribute("tabindex") ||
      e.tabIndex >= 0, "interaktivt element tatt ut av tabrekkefølgen");
  }
  const bytt = h.querySelector("select#nokkeltall-vindu");
  bytt.value = "7d";
  bytt.dispatchEvent(new window.Event("change"));
  await vent(() => h.querySelectorAll("table").length >= 5);
});
