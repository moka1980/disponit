// M-18 onboardingflaten (103) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd, på hver
//     av skjermene flaten kan stå i (løp, tomt register, detaljpanel).
//   * FRAMDRIFT OG FUNN ER TEKST, ikke bare farge (WCAG 1.4.1).
//   * `blokkert` GJØR AT KNAPPEN IKKE LYVER: et steg som venter på et
//     tidligere obligatorisk steg får en SETNING, ikke en grå knapp.
//   * SAMMENDRAGET TELLER ALT, ikke listen.
//   * STEGENE SKRIVES SOM LINJER, ikke som JSON — og parseren er streng.
//   * MODULEN PROVISJONERER INGENTING: ingen kontroll på flaten kaller
//     noe som ligner en provisjoneringsvei.
//   * En lesende økt ser løpene, men INGEN mutasjonskontroller.
//   * Ingen hardkodet tekst (pseudo-locale).
//
// Ingen delt fixture (m16-formen): hver test bygger sin egen skjerm.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import {
  framdriftTekst, fristTekst, parseSteglinjer, visOnboarding,
} from "../static/js/flater/onboarding.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));

const L1 = "11111111-1111-1111-1111-111111111111";
const L2 = "22222222-2222-2222-2222-222222222222";
const M1 = "aaaaaaaa-1111-1111-1111-111111111111";

const BILDE = {
  sammendrag: {
    paagaende: 12, fullforte: 30, avbrutte: 3, stoppede: 2,
    apne_funn: 5, maler: 2, vist: 2,
  },
  lop: [
    { lop_id: L1, kunde_ref: "Nordvik AS", mal_navn: "Standard",
      mal_versjon: 2, startet: "2026-07-01", status: "paagaar",
      eier_bruker_id: "bid_a", eier_navn: "Kari Nordmann",
      eier_aktiv: true, alder_dogn: 63, gjort: 1, totalt: 4,
      obligatoriske_igjen: 2, neste_steg: "Betaling",
      apne_funn: ["stoppet_lop", "steg_over_frist"] },
    { lop_id: L2, kunde_ref: "Fjord AS", mal_navn: "Standard",
      mal_versjon: 2, startet: "2026-08-25", status: "paagaar",
      eier_bruker_id: "bid_b", eier_navn: null, eier_aktiv: false,
      alder_dogn: 8, gjort: 4, totalt: 4, obligatoriske_igjen: 0,
      neste_steg: null, apne_funn: ["lop_uten_aktiv_eier"] },
  ],
  maler: [
    { mal_id: M1, navn: "Standard", versjon: 2, aktiv: true,
      antall_steg: 4, paagaende_lop: 2 },
    { mal_id: "aaaaaaaa-2222-2222-2222-222222222222", navn: "Enkel",
      versjon: 1, aktiv: true, antall_steg: 2, paagaende_lop: 0 },
  ],
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    paagaende: 0, fullforte: 0, avbrutte: 0, stoppede: 0,
    apne_funn: 0, maler: 0, vist: 0,
  },
  lop: [], maler: [], request_id: "r-b",
};

const STEGENE = {
  lop_id: L1,
  steg: [
    { steg_nr: 1, navn: "Kontrakt", beskrivelse: "Signert avtale",
      frist_dogn: 2, obligatorisk: true, eier_bruker_id: "bid_a",
      eier_navn: "Kari Nordmann",
      fullfort_ts: "2026-07-02T09:00:00+00:00", fullfort_av: "bid_a",
      notat: null, forfaller: "2026-07-03", dogn_over_frist: null,
      blokkert: false },
    { steg_nr: 2, navn: "Betaling", beskrivelse: "Første faktura",
      frist_dogn: 7, obligatorisk: true, eier_bruker_id: "bid_a",
      eier_navn: "Kari Nordmann", fullfort_ts: null, fullfort_av: null,
      notat: null, forfaller: "2026-07-08", dogn_over_frist: 56,
      blokkert: false },
    { steg_nr: 3, navn: "Velkomstmøte", beskrivelse: "Gjennomgang",
      frist_dogn: 10, obligatorisk: false, eier_bruker_id: "bid_a",
      eier_navn: "Kari Nordmann", fullfort_ts: null, fullfort_av: null,
      notat: null, forfaller: "2026-07-11", dogn_over_frist: 53,
      blokkert: false },
    { steg_nr: 4, navn: "Workspace", beskrivelse: "Oppsett",
      frist_dogn: 14, obligatorisk: true, eier_bruker_id: "bid_a",
      eier_navn: "Kari Nordmann", fullfort_ts: null, fullfort_av: null,
      notat: null, forfaller: "2026-07-15", dogn_over_frist: 49,
      blokkert: true },
  ],
  request_id: "r-c",
};

let SVAR;
let SISTE;
let KALL;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  KALL.push({ sti, metode: (opts && opts.method) || "GET" });
  if (opts && opts.method === "POST") {
    SISTE = { sti, kropp: JSON.parse(opts.body), headers: opts.headers };
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  }
  const oppf = SVAR[sti];
  if (!oppf) {
    return { ok: false, status: 404,
      json: async () => ({ feil: "ikke_funnet" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx(scopes = ["decisions:read", "bestilling:opprett"]) {
  return { sprak: "nb", scopes, tenant: "acme", paaUautorisert: () => {} };
}

async function vent(pred, n = 80) {
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
  KALL = [];
  return m;
}

function fullSvar() {
  return {
    "/v1/onboarding": BILDE,
    [`/v1/onboarding/lop/${L1}/steg`]: STEGENE,
  };
}

test("Onboarding: frist og framdrift som TEKST", () => {
  assert.equal(fristTekst(56),
    t("ui.onboarding.frist_over").replace("{dogn}", "56"));
  // ENTALL HAR SIN EGEN SETNING på begge språk.
  assert.equal(fristTekst(1), t("ui.onboarding.frist_ett_dogn_over"));
  assert.equal(fristTekst(0), t("ui.onboarding.frist_i_dag"));
  assert.equal(fristTekst(-1), t("ui.onboarding.frist_om_ett"));
  assert.equal(fristTekst(-5),
    t("ui.onboarding.frist_om").replace("{dogn}", "5"));
  // ET FULLFØRT STEG HAR INGEN LØPENDE FRIST. `null` er ikke «null
  // døgn» — en tom celle er det ærlige svaret, ikke «forfaller i dag».
  assert.equal(fristTekst(null), "—");
  assert.equal(framdriftTekst({ gjort: 1, totalt: 4 }),
    t("ui.onboarding.framdrift").replace("{gjort}", "1")
      .replace("{totalt}", "4"));
});

test("Onboarding: steglinjer parses strengt", () => {
  const ok = parseSteglinjer(
    "Kontrakt | Signert avtale | 2\nMøte | Gjennomgang | 10 | V");
  assert.equal(ok.length, 2);
  // ALT ER OBLIGATORISK MED MINDRE NOEN SIER NOE ANNET. Et steg man
  // glemte å merke skal BLOKKERE, ikke stilltiende hoppes over.
  assert.equal(ok[0].obligatorisk, true);
  assert.equal(ok[1].obligatorisk, false);
  assert.equal(ok[0].frist_dogn, 2);
  // Tomme linjer hoppes over; alt annet galt gir null.
  assert.equal(parseSteglinjer("Kontrakt | Avtale | 2\n\n").length, 1);
  assert.equal(parseSteglinjer("Kontrakt | Avtale"), null);
  assert.equal(parseSteglinjer("Kontrakt | Avtale | tre"), null);
  assert.equal(parseSteglinjer("Kontrakt | Avtale | 2.5"), null);
  assert.equal(parseSteglinjer("Kontrakt | Avtale | -1"), null);
  assert.equal(parseSteglinjer(" | Avtale | 2"), null);
  assert.equal(parseSteglinjer(""), null);
  // HJELPETEKSTEN MÅ SI DET PARSEREN GJØR. Første utgave sa «O for
  // obligatorisk» mens parseren leser «V for valgfritt» — en bruker som
  // fulgte teksten ville skrevet O, fått et obligatorisk steg likevel,
  // og ikke skjønt hvorfor. Porten binder de to sammen.
  for (const sprak of ["nb", "en"]) {
    const tekster = JSON.parse(readFileSync(
      join(HER, "..", "..", "..", "..", "locales", `${sprak}.json`),
      "utf-8"));
    const hjelp = tekster["ui.onboarding.skjema.steg_jsonhjelp"];
    assert.ok(hjelp.includes("V"), `${sprak}: hjelpeteksten nevner ikke V`);
    assert.ok(!/\bO for\b/.test(hjelp),
      `${sprak}: hjelpeteksten sier fortsatt «O for …»`);
  }
});

test("Onboarding: løpene tegnes med funn som tekst, axe rent",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visOnboarding(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);

    const tb = h.querySelector("table");
    assert.ok(tb.querySelector("caption").textContent.trim());
    assert.ok(tb.querySelector('th[scope="col"]'));
    for (const rad of tb.querySelectorAll("tbody tr")) {
      assert.equal(rad.cells[0].tagName, "TH");
      assert.equal(rad.cells[0].getAttribute("scope"), "row");
    }

    const tekst = h.textContent;
    assert.ok(tekst.includes(t("ui.onboarding.merke_stoppet")));
    assert.ok(tekst.includes(t("ui.onboarding.merke_forsinket")));
    assert.ok(tekst.includes(t("ui.onboarding.merke_uten_eier")));
    assert.ok(tekst.includes(t("ui.onboarding.framdrift")
      .replace("{gjort}", "1").replace("{totalt}", "4")));
    // «Alle steg gjort» i stedet for en tom celle.
    assert.ok(tekst.includes(t("ui.onboarding.ingen_neste")));
    // SAMMENDRAGET TELLER ALT: 12 pågående, ikke 2.
    assert.ok(tekst.includes("12") && tekst.includes("30"));
    assert.ok(tekst.includes(
      t("ui.onboarding.avkortet").replace("{vist}", "2")),
    "flaten sier ikke at listen er avkortet");

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Onboarding: eieren som sluttet merkes én gang, ikke to",
  async () => {
    // `lop_uten_aktiv_eier` står ALT i eierkolonnen. Å gjenta det som et
    // statusmerke ville vært to merker om den samme tingen på samme
    // linje — og en flate som sier alt to ganger, leses som at noe er
    // galt to ganger.
    SVAR = fullSvar();
    const h = nyHoved();
    visOnboarding(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length >= 2);
    const merker = [...h.querySelectorAll("strong.merke")]
      .map((e) => e.textContent)
      .filter((s) => s === t("ui.onboarding.merke_uten_eier"));
    assert.equal(merker.length, 1, "eieren som sluttet er merket to ganger");
  });

test("Onboarding: blokkert steg får en setning, ikke en død knapp",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visOnboarding(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length >= 2);
    [...h.querySelectorAll("tbody button")].find(
      (b) => b.textContent === t("ui.onboarding.knapp.apne")).click();
    await vent(() => h.textContent.includes("Workspace"));

    // Steg 4 er blokkert: SETNINGEN står, og det finnes ingen
    // fullfør-knapp på den raden. Uten setningen ville brukeren trodd
    // flaten var i stykker.
    assert.ok(h.textContent.includes(t("ui.onboarding.merke_blokkert")));
    // Steg 1 er gjort: «gjort av» i stedet for en knapp.
    assert.ok(h.textContent.includes(
      t("ui.onboarding.detalj.gjort_av").replace("{av}", "bid_a")));
    // Steg 2 og 3 er verken gjort eller blokkert → to knapper.
    const knapper = [...h.querySelectorAll("button")].filter(
      (b) => b.textContent === t("ui.onboarding.knapp.fullfor"));
    assert.equal(knapper.length, 2,
      "et blokkert eller gjort steg fikk en fullfør-knapp");

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Onboarding: fullføring sender løp og stegnummer med nøkkel",
  async () => {
    SVAR = fullSvar();
    SISTE = null;
    const h = nyHoved();
    visOnboarding(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length >= 2);
    [...h.querySelectorAll("tbody button")].find(
      (b) => b.textContent === t("ui.onboarding.knapp.apne")).click();
    await vent(() => h.textContent.includes("Workspace"));
    [...h.querySelectorAll("button")].find(
      (b) => b.textContent === t("ui.onboarding.knapp.fullfor")).click();
    await vent(() => SISTE !== null);
    assert.equal(SISTE.sti, `/v1/onboarding/lop/${L1}/steg/2/fullfor`);
    assert.ok(SISTE.headers["Idempotency-Key"]);
  });

test("Onboarding: maler med pågående løp sier hvorfor de er låst",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visOnboarding(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);
    // FLATEN SIER HVORFOR malen ikke kan endres, i stedet for å la
    // brukeren møte vaktens feilmelding etter å ha fylt ut skjemaet.
    assert.ok(h.textContent.includes(
      t("ui.onboarding.maler.laast").replace("{antall}", "2")));
  });

test("Onboarding: tomt register sier at en mal mangler", async () => {
  SVAR = { "/v1/onboarding": TOMT };
  const h = nyHoved();
  visOnboarding(h, ctx());
  await vent(() => h.textContent.includes(t("ui.onboarding.lop.ingen")));
  // ÆRLIG TOMTILSTAND: uten en mal kan ingen starte et løp, og
  // setningen sier hvorfor i stedet for å vise en tom tabell.
  assert.ok(h.textContent.includes(t("ui.onboarding.maler.ingen")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Onboarding: en lesende økt får ingen mutasjonskontroller",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visOnboarding(h, ctx(["decisions:read"]));
    await vent(() => h.querySelectorAll("table").length >= 2);
    assert.equal(h.querySelectorAll("form").length, 0);
    for (const nokkel of ["ui.onboarding.knapp.ny_mal",
                          "ui.onboarding.knapp.start",
                          "ui.onboarding.knapp.lagre_steg",
                          "ui.onboarding.knapp.avslutt_fullfort",
                          "ui.onboarding.knapp.avslutt_avbrutt"]) {
      assert.ok(!h.textContent.includes(t(nokkel)), nokkel);
    }
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Onboarding: ingen hardkodet tekst i flaten", async () => {
  const nokler = Object.keys(NB).filter(
    (k) => k.startsWith("ui.onboarding"));
  assert.ok(nokler.length > 50, `bare ${nokler.length} nøkler`);
  const pseudo = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(pseudo, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visOnboarding(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);
    // `th[scope="row"]` er UTELATT: den cellen bærer kundenavnet og
    // malnavnet, altså tenantens data — ikke en oversatt etikett.
    for (const node of h.querySelectorAll(
      'h2, h3, label, caption, th[scope="col"], button')) {
      const s = node.textContent.trim();
      if (!s) continue;
      assert.ok(s.startsWith("PL_"), `hardkodet tekst: «${s}»`);
    }
  } finally {
    settI18nForTest(NB, "nb");
  }
});

test("Onboarding: kilden bærer ingen provisjoneringsvei", () => {
  // MODULEN PROVISJONERER INGENTING, målt på FLATENS kilde. De andre
  // halvdelene står i `test_m18_onboarding.py` (AST, datamodellen,
  // rutene, og radantallet etter en sveip).
  const kilde = readFileSync(
    join(HER, "..", "static", "js", "flater", "onboarding.js"), "utf8");
  const uten = kilde.replace(/^\s*\/\/.*$/gm, "");
  for (const ord of ["provisjoner", "/v1/tilgang", "opprettKonto",
                     "m12_"]) {
    assert.ok(!uten.toLowerCase().includes(ord.toLowerCase()),
      `flaten bærer «${ord}» — v1 provisjonerer ingenting`);
  }
  const api = readFileSync(
    join(HER, "..", "static", "js", "api.js"), "utf8");
  assert.ok(!/export const provisjoner/.test(api));
});


// KVITTERINGEN SKAL OVERLEVE TEGNINGEN.
//
// Porten finnes fordi det var galt i alle fem flatene i klyngen:
// suksessmeldingen ble satt i skjemaets eget `utfall`, og `last()` bygde
// straks både panelet og skjemaet på nytt. Brukeren trykket, så skjermen
// blinke, og satt igjen uten å vite om det gikk bra. Skjermleseren hørte
// det (`meldLive`), men en seende bruker fikk ingenting.
//
// MUTASJONEN SOM DREPER DENNE: flytt kvitteringen tilbake inn i `kropp`.
test("Onboarding: kvitteringen og panelet overlever tegningen",
  async () => {
    SVAR = fullSvar();
    SISTE = null;
    const h = nyHoved();
    visOnboarding(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length >= 2);
    [...h.querySelectorAll("tbody button")].find(
      (b) => b.textContent === t("ui.onboarding.knapp.apne")).click();
    await vent(() => h.textContent.includes("Workspace"));
    [...h.querySelectorAll("button")].find(
      (b) => b.textContent === t("ui.onboarding.knapp.fullfor")).click();
    await vent(() => SISTE !== null);
    // `assert.ok(await vent(...))`, ikke bare `await vent(...)`: en
    // `vent()` som gir opp returnerer falskt uten å kaste, og en port
    // som ikke ser på svaret er alltid grønn. (CodeRabbit.)
    assert.ok(await vent(() => h.textContent.includes(
      t("ui.onboarding.steg_ok"))), "kvitteringen forsvant i tegningen");
    // …OG PANELET STÅR ÅPENT PÅ SAMME LØP.
    assert.ok(await vent(() => h.textContent.includes("Workspace")),
      "panelet lukket seg etter et fullført steg");
  });
