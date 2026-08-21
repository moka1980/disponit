// M-16 nøkkeltall — flateportene 11–15 (jsdom + axe): UI-tall == API-svar,
// axe uten alvorlige brudd, alle tall som tekst (søylen bærer aldri noe
// alene), aldri kun farge, ingen hardkodet visningstekst, tomtilstander
// som eksplisitt innhold. Ingen delt fixture.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { NB, alvorligeBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";

const EN = JSON.parse(readFileSync(
  join(dirname(fileURLToPath(import.meta.url)),
       "../../../../locales/en.json"), "utf-8"));
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
const STANDARD_FETCH = async (url) => {
  const sti = url.split("?")[0];
  const oppf = SVAR[sti];
  if (!oppf) return { ok: false, status: 404,
    json: async () => ({ feil: "ikke_funnet" }) };
  if (typeof oppf === "number") {
    return { ok: false, status: oppf, json: async () => ({ feil: "x" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};
globalThis.fetch = STANDARD_FETCH;

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
  // Port 9: tick-kortet med 0 rader er en SETNING — om VINDUET.
  assert.ok(tekst.includes(t("ui.nokkeltall.ingen_tick")));
  // Et tomt vindu er ikke et fravær over all tid: kortet teller
  // aktivitet i [fra, til), og setningen får ikke påstå mer enn det.
  for (const kart of [NB, EN]) {
    const s = kart["ui.nokkeltall.ingen_tick"].toLowerCase();
    for (const paastand of ["ennå", "yet", "aldri", "never"]) {
      assert.ok(!s.includes(paastand),
        `tomt tick-vindu påstår et fravær over all tid: ${s}`);
    }
  }
  // Radvis varighet vises som omskrevet klartekst.
  // 5400 s → «1 t 30 min» (omskriving av radens eget tall, tapsfri).
  assert.ok(tekst.includes(
    `${t("ui.nokkeltall.varighet_timer").replace("{n}", "1")} `
    + t("ui.nokkeltall.varighet_min").replace("{n}", "30")));
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

test("Nøkkeltall: et utdatert vindussvar overskriver aldri et nyere", async () => {
  // To vindusbytter i rask rekkefølge, der det FØRSTE svaret kommer sist:
  // flaten må vise det siste valget, ikke det siste svaret.
  const svar = [];
  globalThis.fetch = async (url) =>
    new Promise((r) => { svar.push({ url, r }); });
  const h = nyHoved();
  visNokkeltall(h, ctx());
  await vent(() => svar.length === 1);
  const velger = h.querySelector("select#nokkeltall-vindu");
  velger.value = "7d";
  velger.dispatchEvent(new window.Event("change"));
  await vent(() => svar.length === 2);

  const ok = (d) => ({ ok: true, status: 200, json: async () => d });
  // Nyeste kall (7d) svarer først …
  svar[1].r(ok({ ...SVARFORM, apne_naa: 42 }));
  await vent(() => h.querySelector(".kpi-tilstand").textContent
    .includes("42"));
  // … og deretter kommer det trege 24t-svaret. Det skal kastes.
  svar[0].r(ok({ ...SVARFORM, apne_naa: 6 }));
  await vent(() => false, 10);
  assert.ok(h.querySelector(".kpi-tilstand").textContent.includes("42"),
    "et utdatert svar overskrev det nyere vindusresultatet");

  // Samme vei for en sen FEIL: den skal ikke rive ned et ferskt svar.
  velger.value = "30d";
  velger.dispatchEvent(new window.Event("change"));
  await vent(() => svar.length === 3);
  velger.value = "24t";
  velger.dispatchEvent(new window.Event("change"));
  await vent(() => svar.length === 4);
  svar[3].r(ok({ ...SVARFORM, apne_naa: 7 }));
  await vent(() => h.querySelector(".kpi-tilstand").textContent
    .includes("7"));
  svar[2].r({ ok: false, status: 500, json: async () => ({ feil: "x" }) });
  await vent(() => false, 10);
  assert.ok(h.querySelector(".kpi-tilstand").textContent.includes("7"),
    "en utdatert feil erstattet et ferskt vindusresultat");
  assert.ok(h.querySelectorAll("table").length >= 5, "kortene ble revet ned");
  globalThis.fetch = STANDARD_FETCH;
});

test("Nøkkeltall: engelsk visning viser aldri de norske maskinkodene", async () => {
  SVAR = { "/v1/nokkeltall": { ...SVARFORM,
    tick: { total: 3, deler: { tillat: 2, hoppet_over: 1 } } } };
  settI18nForTest(EN, "en");
  try {
    const h = nyHoved();
    visNokkeltall(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 5);
    const tekst = h.textContent;
    // Hver kode har en kanonisk nøkkelfamilie i repoet — den skal brukes.
    for (const [kode, engelsk] of [
      ["utfort", EN["art.outbox_utfort"]],
      ["feilet", EN["art.outbox_feilet"]],
      ["over_grense", EN["unntak.over_grense"]],
      ["styrt", EN["ui.nokkeltall.verdi.aktivering.styrt"]],
      ["historisk", EN["ui.nokkeltall.verdi.aktivering.historisk"]],
      ["hoppet_over", EN["ui.plan.utfall.hoppet_over"]],
      ["tillat", EN["ui.plan.utfall.tillat"]],
    ]) {
      assert.ok(tekst.includes(engelsk),
        `mangler oversettelsen av «${kode}»`);
      assert.ok(!tekst.includes(kode),
        `den norske koden «${kode}» lekker ut i engelsk visning`);
    }
    // Port 1 står: en verdi UTENFOR det kjente domenet vises fortsatt rå,
    // i egen rad og i totalen — den skjules aldri fordi den er ukjent.
    assert.ok(tekst.includes("hokuspokus"));
  } finally {
    settI18nForTest(NB, "nb");
  }
});

test("Nøkkeltall: radvis varighet mister aldri presisjon", async () => {
  // Grensetilfellene Codex pekte på: 119 s var «1 min», 86 399 s var
  // «23 timer». Nå skal hvert sekund kunne leses tilbake ut av teksten.
  const sak = (id, varighet_s) => ({ id, kategori: "over_grense",
    sakstype: "normal", status: "løst",
    opprettet: "2026-08-20T11:00:00+00:00",
    lukket: "2026-08-20T12:30:00+00:00", varighet_s });
  SVAR = { "/v1/nokkeltall": { ...SVARFORM, unntak_lukkede: [
    sak(1, 119), sak(2, 86399), sak(3, 45), sak(4, 90061), sak(5, 3600)],
    unntak_lukkede_totalt: 5 } };
  const h = nyHoved();
  visNokkeltall(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 5);
  const celler = [...h.querySelectorAll("table")]
    .find((tb) => tb.querySelector("caption").textContent
      === t("ui.nokkeltall.lukkede_caption"))
    .querySelectorAll("tbody tr");
  const d = (n) => t("ui.nokkeltall.varighet_dogn").replace("{n}", String(n));
  const ti = (n) => t("ui.nokkeltall.varighet_timer").replace("{n}", String(n));
  const mi = (n) => t("ui.nokkeltall.varighet_min").replace("{n}", String(n));
  const se = (n) => t("ui.nokkeltall.varighet_sek").replace("{n}", String(n));
  const forventet = [
    `${mi(1)} ${se(59)}`,                      // 119 s, ikke «1 min»
    `${ti(23)} ${mi(59)} ${se(59)}`,           // 86 399 s, ikke «23 timer»
    se(45),
    `${d(1)} ${ti(1)} ${mi(1)} ${se(1)}`,      // 90 061 s
    ti(1),                                     // eksakt time: ingen haleledd
  ];
  assert.equal(celler.length, forventet.length);
  celler.forEach((rad, i) => {
    assert.equal(rad.cells[2].textContent, forventet[i]);
  });
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
