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
  beslutninger: { total: 7, deler: { TILLAT: 5, UNNTAK: 1, hokuspokus: 1 },
    andeler: { TILLAT: 0.7143, UNNTAK: 0.1429, hokuspokus: 0.1429 } },
  frekvens: { total: 3, deler: { utbetaling: 2, fakturering: 1 },
    andeler: { utbetaling: 0.6667, fakturering: 0.3333 } },
  aktiveringer: {
    kilde: { total: 2, deler: { styrt: 1, historisk: 1 },
      andeler: { styrt: 0.5, historisk: 0.5 } },
    kvorumskrav: { total: 2, deler: { 1: 1, 2: 1 },
      andeler: { 1: 0.5, 2: 0.5 } },
    attestanter: { total: 2, deler: { en: 1, to: 1 },
      andeler: { en: 0.5, to: 0.5 } },
  },
  oppdrag: { total: 4, deler: { utfort: 3, feilet: 1 },
    andeler: { utfort: 0.75, feilet: 0.25 } },
  unntak_aktivitet: { total: 2, deler: { over_grense: 2 },
    andeler: { over_grense: 1 } },
  unntak_lukkede: [{ id: 9, kategori: "over_grense", sakstype: "normal",
    status: "løst", opprettet: "2026-08-20T11:00:00+00:00",
    lukket: "2026-08-20T12:30:00+00:00", varighet_s: 5400 }],
  unntak_lukkede_totalt: 1,
  unntak_lukkede_grense: 50,
  unntak_lukketid: { sum_s: 5400, antall: 1, gjennomsnitt_s: 5400 },
  apne_naa: 6,
  tick: { total: 0, deler: {}, andeler: {} },
  tick_alltid_totalt: 12,
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

// Nøkkeltallflaten ligger bak `decisions:read` i sitekartet, så DET er
// grunnlinjen for enhver økt som i det hele tatt kan se den; `leser` har
// unntaksscopet i tillegg. Et tomt scopesett — som sto her før — er ingen
// virkelig økt, og en flate testet uten scopes kan ikke vise at den
// skjuler noe for den som mangler ett.
function ctx(scopes = ["decisions:read", "exceptions:read"]) {
  return { sprak: "nb", scopes, tenant: "acme",
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
    // Codex P2: raden skal ha BEGGE retningene. Cellen som navngir raden
    // er en overskrift — ellers knytter en skjermleser i tall- eller
    // søylekolonnen bare kolonneoverskriften til tallet, og hvilken
    // kategori tallet gjelder går tapt. Gjelder begge tabellformene.
    for (const rad of tb.querySelectorAll("tbody tr, tfoot tr")) {
      assert.equal(rad.cells[0].tagName, "TH",
        `radnavnet er ikke en overskrift i «${
          tb.querySelector("caption").textContent}»`);
      assert.equal(rad.cells[0].getAttribute("scope"), "row");
    }
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
  // Port 9, fase 2: tick-kortet med 0 rader i vinduet OG kjøringer over
  // all tid sier BEGGE deler — vinduet er tomt, og tallet for all tid
  // (målt av m16_tick_alltid) står i setningen.
  assert.ok(tekst.includes(t("ui.nokkeltall.ingen_tick_vindu_alltid")
    .replace("{antall}", "12")));
  // Setningen bærer tallet i begge språk — uten plassholderen ville
  // vinduets tomhet igjen kunne leses som et fravær over all tid.
  for (const kart of [NB, EN]) {
    assert.ok(kart["ui.nokkeltall.ingen_tick_vindu_alltid"]
      .includes("{antall}"),
      "all-tid-setningen mangler tallet");
  }
  // Fase 2: andelskolonnen — API-ets ferdige andel som prosenttekst,
  // ved siden av telleren og totalen den kan leses tilbake til.
  assert.ok(tekst.includes(t("ui.nokkeltall.kolonne.andel")));
  const tillatRad = [...beslT.querySelectorAll("tbody tr")][0];
  assert.equal(tillatRad.cells[2].textContent, "71 %",
    "andelen er ikke API-verdien omskrevet til prosent");
  // Fase 2: frekvensen er et EGET kort — en partisjon per handling med
  // total, ikke lenger en skalarlinje i beslutningskortet.
  const frekT = [...h.querySelectorAll("table")]
    .find((tb) => tb.querySelector("caption").textContent
      === t("ui.nokkeltall.kort.frekvens"));
  assert.ok(frekT, "frekvenskortet mangler");
  assert.ok(frekT.textContent.includes("utbetaling"));
  assert.ok(frekT.querySelector("tfoot").textContent.includes("3"));
  assert.ok(!beslT.parentElement.textContent.includes("utbetaling"),
    "frekvensen står fortsatt i beslutningskortet");
  // Fase 2: lukketid-snittet under lukkede-listen — API-ets omskriving
  // av sum og antall som begge står i svaret; 5400 s → «1 t 30 min».
  assert.ok(tekst.includes(t("ui.nokkeltall.lukketid_gjennomsnitt")
    .replace("{varighet}",
      `${t("ui.nokkeltall.varighet_timer").replace("{n}", "1")} `
      + t("ui.nokkeltall.varighet_min").replace("{n}", "30"))
    .replace("{antall}", "1")));
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
    beslutninger: { total: 0, deler: {}, andeler: {} },
    oppdrag: { total: 0, deler: {}, andeler: {} },
    unntak_aktivitet: { total: 0, deler: {}, andeler: {} },
    unntak_lukkede: [], unntak_lukkede_totalt: 0,
    unntak_lukketid: { sum_s: 0, antall: 0, gjennomsnitt_s: null },
    apne_naa: 0 } };
  const h = nyHoved();
  visNokkeltall(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 4);
  const beslT = [...h.querySelectorAll("table")]
    .find((tb) => tb.querySelector("caption").textContent
      === t("ui.nokkeltall.kort.beslutninger"));
  assert.ok(beslT.textContent.includes(t("ui.nokkeltall.ingen")));
  assert.ok(beslT.querySelector("tfoot").textContent.includes("0"));
  // Nevner 0: andelen er «ikke definert» — aldri «0 %». At ingen ble
  // talt er ikke det samme som at andelen er ingenting.
  assert.ok(beslT.textContent
    .includes(t("ui.nokkeltall.andel_ikke_definert")));
  assert.ok(!beslT.textContent.includes("0 %"));
  assert.ok(h.textContent.includes(t("ui.nokkeltall.ingen_lukkede")));
  // Ingen lukkede saker → ikke noe snitt å omtale (null, ikke 0 sek).
  assert.ok(!h.textContent.includes(
    t("ui.nokkeltall.lukketid_gjennomsnitt").slice(0, 12)));
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

test("Nøkkeltall: veien videre tilbys aldri til en økt som ikke kan gå den",
     async () => {
  // `policyforvalter` har `decisions:read` (ser nøkkeltallene) men ikke
  // `exceptions:read`, så `#/unntak` er ikke en rute i hennes sitekart:
  // ruteren leser klikket som en ukjent adresse og tegner Oversikt. Da
  // skal knappen ikke finnes — men SETNINGEN om at listen er avkuttet
  // skal fortsatt stå, for den er sann uansett hvem som leser.
  SVAR = { "/v1/nokkeltall": { ...SVARFORM, unntak_lukkede_totalt: 137,
    unntak_lukkede_grense: 50 } };
  const h = nyHoved();
  visNokkeltall(h, ctx(["decisions:read", "policy:write"]));
  await vent(() => h.querySelectorAll("table").length >= 5);
  const tekst = h.textContent;
  assert.ok(tekst.includes(t("ui.nokkeltall.lukkede_avkuttet")
    .replace("{vist}", "1").replace("{totalt}", "137")
    .replace("{grense}", "50")),
    "avkuttingen skal sies uansett scope");
  assert.ok(!tekst.includes(t("ui.nokkeltall.til_unntak")),
    "lovet en flate økten ikke har rute til");
  // Beslutningsflaten deler scope med nøkkeltallflaten — den veien er
  // åpen for den samme økten, og skal derfor fortsatt tilbys.
  assert.ok(tekst.includes(t("ui.nokkeltall.til_beslutninger")));
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

test("Nøkkeltall: en feilet last lar ikke forrige svars tall stå igjen",
  async () => {
    // Ett vellykket vindu, så ett som feiler. Feilveien tegnet bare
    // kortene, så «åpne nå» og grensene fra det FORRIGE svaret ble stående
    // ved siden av feilen — under den nye vindusledeteksten.
    const svar = [];
    globalThis.fetch = async (url) =>
      new Promise((r) => { svar.push({ url, r }); });
    try {
      const h = nyHoved();
      visNokkeltall(h, ctx());
      await vent(() => svar.length === 1);
      svar[0].r({ ok: true, status: 200,
        json: async () => ({ ...SVARFORM, apne_naa: 6 }) });
      await vent(() => h.querySelector(".kpi-tilstand").textContent
        .includes("6"));
      const grenser = h.querySelectorAll("p.muted")[0].textContent;
      assert.ok(grenser.includes(t("ui.nokkeltall.vindu_valgt")));

      const velger = h.querySelector("select#nokkeltall-vindu");
      velger.value = "7d";
      velger.dispatchEvent(new window.Event("change"));
      await vent(() => svar.length === 2);
      svar[1].r({ ok: false, status: 500, json: async () => ({ feil: "x" }) });
      await vent(() => h.querySelector(".tilstand.feil"));

      assert.ok(!h.querySelector(".kpi-tilstand").textContent.includes("6"),
        "«åpne nå» fra forrige svar sto igjen ved siden av feilen");
      assert.ok(!h.textContent.includes(t("ui.nokkeltall.vindu_valgt")),
        "grensene fra forrige vindu sto igjen under det nye vindusnavnet");
      assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
    } finally {
      globalThis.fetch = STANDARD_FETCH;
    }
  });

test("Nøkkeltall: engelsk visning viser aldri de norske maskinkodene", async () => {
  SVAR = { "/v1/nokkeltall": { ...SVARFORM,
    tick: { total: 3, deler: { tillat: 2, hoppet_over: 1 },
      andeler: { tillat: 0.6667, hoppet_over: 0.3333 } } } };
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

test("Nøkkeltall: tomt vindu og ingen kjøring noensinne er to ulike setninger",
  async () => {
    // Fase 2: all-tid-påstanden er nå MÅLT (m16_tick_alltid), så flaten
    // får si den — men bare når tellingen faktisk er 0. Med kjøringer
    // utenfor vinduet står i stedet setningen med tallet (se første
    // test); de to tilstandene deler aldri tekst.
    SVAR = { "/v1/nokkeltall": { ...SVARFORM, tick_alltid_totalt: 0 } };
    const h = nyHoved();
    visNokkeltall(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 5);
    assert.ok(h.textContent.includes(t("ui.nokkeltall.ingen_tick_alltid")),
      "alltid=0-setningen mangler");
    assert.ok(!h.textContent.includes(
      t("ui.nokkeltall.ingen_tick_vindu_alltid").slice(-20)),
      "begge tomtilstandssetningene står samtidig");
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
    SVAR = { "/v1/nokkeltall": SVARFORM };
  });

test("Nøkkeltall: samme kode leses av kortet den står på", async () => {
  // Codex P2: definerne skriver `ukjent` som NULL-sentinel på HVERT kort,
  // men på unntakskortet er den samtidig en ekte kategori med en etablert
  // betydning i repoet. Den kortblinde `ui.nokkeltall.verdi.ukjent` sto
  // først i kandidatlisten og stjal den betydningen. Nå avgjør
  // spesifisitet: kortets familie slår flatens generelle tekst.
  SVAR = { "/v1/nokkeltall": { ...SVARFORM,
    unntak_aktivitet: { total: 2, deler: { ukjent: 2 },
      andeler: { ukjent: 1 } },
    aktiveringer: { ...SVARFORM.aktiveringer,
      kvorumskrav: { total: 1, deler: { ukjent: 1 },
        andeler: { ukjent: 1 } } } } };
  const h = nyHoved();
  visNokkeltall(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 5);
  const kort = (caption) => [...h.querySelectorAll("table")]
    .find((tb) => tb.querySelector("caption").textContent === caption);
  assert.ok(kort(t("ui.nokkeltall.kort.unntak_aktivitet")).textContent
    .includes(t("unntak.ukjent")),
    "unntakskortet viser ikke kategoriens etablerte betydning");
  // Kvorumskravkortet har ingen familie: der ER `ukjent` NULL-sentinelen,
  // og flatens generelle tekst er fortsatt riktig — og fortsatt synlig.
  assert.ok(kort(t("ui.nokkeltall.kort.aktiveringer.kvorumskrav"))
    .textContent.includes(t("ui.nokkeltall.verdi.ukjent")));
  SVAR = { "/v1/nokkeltall": SVARFORM };
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
    unntak_lukkede_totalt: 5,
    unntak_lukketid: { sum_s: 180224, antall: 5,
      gjennomsnitt_s: 36045 } } };
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

test("Nøkkeltall: sonemerket er sonen tidspunktene faktisk vises i",
  async () => {
    // Svaret sier hvilken sone grensene er uttrykt i, og flaten skal
    // TEGNE dem i den — ikke bare skrive navnet ved siden av leserens
    // egen klokke. Tokyo er valgt fordi den ligger langt fra både UTC og
    // kjørerens sone: 10:00Z er 19:00, 12:30Z er 21:30.
    SVAR = { "/v1/nokkeltall": { ...SVARFORM, tidssone: "Asia/Tokyo" } };
    const h = nyHoved();
    visNokkeltall(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 5);
    const meta = [...h.querySelectorAll("time")]
      .filter((n) => n.getAttribute("datetime") === SVARFORM.vindu_start
                  || n.getAttribute("datetime") === SVARFORM.vindu_slutt);
    assert.equal(meta.length, 2, "vindusgrensene mangler som <time>");
    for (const n of meta) {
      assert.ok(n.textContent.endsWith("(Asia/Tokyo)"),
        `grensen bærer ikke sonen: ${n.textContent}`);
      assert.ok(n.textContent.includes("19:00"),
        `grensen er ikke tegnet i sonen: ${n.textContent}`);
    }
    // Radene ligger per definisjon i vinduet — tegnes de i en annen sone
    // enn grensene, ser en leser rader utenfor vinduet de er talt i.
    const lukket = [...h.querySelectorAll("time")]
      .find((n) => n.getAttribute("datetime")
        === SVARFORM.unntak_lukkede[0].lukket);
    assert.ok(lukket, "lukket-tidspunktet mangler som <time>");
    assert.ok(lukket.textContent.includes("21:30")
      && lukket.textContent.endsWith("(Asia/Tokyo)"),
      `raden følger ikke vinduets sone: ${lukket.textContent}`);
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

// KPI-BLOKKEN KRYMPER, DEN RULLER IKKE (Codex P2, WCAG 1.4.10).
//
// Ved 320 CSS-piksler — 400 % zoom på en vanlig skjerm — har `.skall-hoved`
// under 20rem igjen etter sin egen padding. Hver absolutte bredde i blokka
// er derfor en potensiell horisontal siderulling, og de var tre: sporet i
// rutenettet, søylens `width`, og et radnavn uten et sted å brekke.
// jsdom har ingen layout å måle, så porten står på stilkilden — samme form
// som `.skall-bruker` bruker for nøyaktig samme klasse feil.
test("Nøkkeltall: ingen absolutt bredde tvinger fram siderulling", () => {
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)),
         "..", "static", "css", "komponenter.css"), "utf-8");
  const regel = (velger) => {
    const i = css.indexOf(velger);
    assert.ok(i >= 0, `${velger} skal finnes i stilkilden`);
    return css.slice(i, css.indexOf("}", i));
  };
  // Sporet er et ØNSKE om 20rem, avkortet av plassen som faktisk finnes.
  assert.match(regel(".kpi-kort-liste{"),
    /minmax\(\s*min\(\s*20rem\s*,\s*100%\s*\)\s*,\s*1fr\s*\)/,
    "et ubetinget rem-minimum i sporet sprenger containeren på smal skjerm");
  // Søylen er aria-hidden og gir etter først; teksten bærer opplysningen.
  const soyle = regel(".kpi-soyle{");
  assert.match(soyle, /max-width:\s*8rem/,
    "8rem må være søylens fulle bredde, ikke dens minste");
  assert.ok(!/(^|[;{\s])width:\s*8rem/.test(soyle),
    "en fast søylebredde binder tallkolonnene uansett skjermbredde");
  // En rå maskinkode er ett ord uten mellomrom å brekke på.
  assert.match(regel(".kpi-tabell td,.kpi-tabell th{"),
    /overflow-wrap:\s*(anywhere|break-word)/,
    "et ubrytelig radnavn setter ellers tabellens minstebredde");
});

// PR-C: EGENDEFINERT INTERVALL. Velgeren viser kontrollene (to
// datetime-local med label + hent-knapp) uten å hente noe — intervallet
// finnes ikke før brukeren har angitt det. Hent sender paret `fra`/`til`
// (lokaltid omskrevet til UTC, eksplisitt) og ALDRI `vindu`.
test("Nøkkeltall: egendefinert intervall GET-er fra/til — aldri vindu", async () => {
  const kall = [];
  globalThis.fetch = async (url) => {
    kall.push(url);
    return { ok: true, status: 200, json: async () => SVARFORM };
  };
  try {
    const h = nyHoved();
    visNokkeltall(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 5);
    assert.equal(kall.length, 1);

    const velger = h.querySelector("select#nokkeltall-vindu");
    const valg = [...velger.options].map((o) => o.value);
    assert.ok(valg.includes("egendefinert"), "egendefinert mangler i velgeren");
    velger.value = "egendefinert";
    velger.dispatchEvent(new window.Event("change"));

    const fra = h.querySelector("input#nokkeltall-intervall-fra");
    const til = h.querySelector("input#nokkeltall-intervall-til");
    assert.ok(fra && til, "intervallkontrollene mangler");
    assert.equal(fra.type, "datetime-local");
    assert.ok(h.querySelector('label[for="nokkeltall-intervall-fra"]'));
    assert.ok(h.querySelector('label[for="nokkeltall-intervall-til"]'));
    assert.ok(!fra.closest("div").hidden, "kontrollene er skjult");
    // Ingen henting av at velgeren byttet: intervallet er ikke angitt.
    assert.equal(kall.length, 1);
    // …og forrige svar står fortsatt (det er sant, med egne grenser).
    assert.ok(h.querySelectorAll("table").length >= 5);

    fra.value = "2026-08-01T12:00";
    til.value = "2026-08-02T12:00";
    const knapp = [...h.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.nokkeltall.intervall_hent"));
    assert.ok(knapp, "hent-knappen mangler");
    knapp.click();
    await vent(() => kall.length === 2);
    const u = new URL(kall[1], "http://x");
    assert.equal(u.searchParams.get("vindu"), null,
      "egendefinert sendte vindu-parameteren");
    // Lokaltid → UTC eksplisitt: nøyaktig toISOString av feltverdien,
    // tolket i kjørerens egen sone — samme omskriving flaten gjør.
    assert.equal(u.searchParams.get("fra"),
      new Date("2026-08-01T12:00").toISOString());
    assert.equal(u.searchParams.get("til"),
      new Date("2026-08-02T12:00").toISOString());
    await vent(() => h.querySelectorAll("table").length >= 5);
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
  } finally {
    globalThis.fetch = STANDARD_FETCH;
  }
});

test("Nøkkeltall: 400 på egendefinert står ved kontrollene — aldri tom og stum flate", async () => {
  const svarkoe = [{ ok: true, status: 200, json: async () => SVARFORM },
                   { ok: false, status: 400,
                     json: async () => ({ feil: "request_feilformet" }) }];
  globalThis.fetch = async () => svarkoe.shift();
  try {
    const h = nyHoved();
    visNokkeltall(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 5);

    const velger = h.querySelector("select#nokkeltall-vindu");
    velger.value = "egendefinert";
    velger.dispatchEvent(new window.Event("change"));
    const knapp = [...h.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.nokkeltall.intervall_hent"));

    // Halvt utfylt: samme feiltekst, uten rundtur (svarkøen røres ikke).
    h.querySelector("input#nokkeltall-intervall-fra").value =
      "2026-08-01T12:00";
    knapp.click();
    assert.ok(h.textContent.includes(t("ui.nokkeltall.intervall_feil")),
      "halvt par ga ingen feiltekst");
    assert.equal(svarkoe.length, 1, "halvt par gikk til serveren");

    // Fullt utfylt, serveren svarer 400: teksten står ved kontrollene
    // (role=alert), og flaten viser verken den generelle feiltilstanden
    // eller en løgnaktig «laster»-linje.
    h.querySelector("input#nokkeltall-intervall-til").value =
      "2026-08-02T12:00";
    knapp.click();
    await vent(() => svarkoe.length === 0);
    await vent(() => h.querySelector('[role="alert"]').textContent
      .includes(t("ui.nokkeltall.intervall_feil")));
    assert.ok(!h.querySelector(".tilstand.feil"),
      "400 rev flaten til den generelle feiltilstanden");
    assert.ok(!h.textContent.includes(t("ui.laster")));
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
  } finally {
    globalThis.fetch = STANDARD_FETCH;
  }
});
