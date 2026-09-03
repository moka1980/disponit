// M-39 lønnsflaten (113) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd på hver
//     skjerm flaten kan stå i.
//   * `modulen_produserte_lonnsfil`: flaten har INGEN «generer»- eller
//     «last ned»-knapp, og ingen egen utgående kanal.
//   * `overtid_uten_flagg`: det finnes ingen kontroll å merke overtid
//     med. Overtid utledes og blir et FUNN.
//   * `timer_i_flyttall`: minutter inn og ut, «7:30» ut på skjermen,
//     og `tilMinutter` runder av ÉN gang.
//   * `time_uten_arbeidsplan`: «ingen plan» og «planlagt fri» vises
//     ALDRI likt (WCAG 1.4.1 og alminnelig ærlighet).
//   * En lesende økt ser registeret, men INGEN mutasjonskontroller.
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
  KILDER, avvikTekst, kildeTekst, planTekst, tilMinutter, timeTekst,
  visLonn,
} from "../static/js/flater/lonn.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));

const S1 = "11111111-1111-1111-1111-111111111111";
const S2 = "22222222-2222-2222-2222-222222222222";

function locale(sprak) {
  return JSON.parse(readFileSync(
    join(HER, "..", "..", "..", "..", "locales", `${sprak}.json`),
    "utf-8"));
}

const BILDE = {
  sammendrag: {
    takere: 40, aktive: 12, med_timer: 9, med_plan: 8, apne_funn: 4,
    apne_overtid: 2, har_terskel: true, terskelversjon: 2, vist: 2,
  },
  takere: [
    { taker_id: S1, ekstern_ref: "ANS-100", navn: "Kari Ansatt",
      aktiv: true, plan_id: "p-1", planlagt_minutter_dag: 450,
      plan_prosjektkode: "P-1", plan_fra: "2026-01-01",
      sum_minutter: 2700, dager: 6, siste_dato: "2026-08-08",
      apne_funn: ["overtid"] },
    { taker_id: S2, ekstern_ref: "ANS-200", navn: "Ola Ansatt",
      aktiv: false, plan_id: null, planlagt_minutter_dag: null,
      plan_prosjektkode: null, plan_fra: null, sum_minutter: 0,
      dager: 0, siste_dato: null,
      apne_funn: ["time_uten_arbeidsplan"] },
  ],
  terskler: {
    normaltid_minutter_dag: 450, normaltid_minutter_uke: 2250,
    avvik_minutter: 15, uten_plan_dogn: 7, versjon: 2,
    oppdatert: "2026-08-01T09:00:00+00:00", oppdatert_av: "kari",
  },
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    takere: 0, aktive: 0, med_timer: 0, med_plan: 0, apne_funn: 0,
    apne_overtid: 0, har_terskel: false, terskelversjon: null, vist: 0,
  },
  takere: [], terskler: null, request_id: "r-b",
};

const DAGER = {
  taker_id: S1,
  dager: [
    { dato: "2026-08-05", minutter: 600, planlagt_minutter: 450,
      avvik_minutter: 150, prosjektkoder: ["P-1", "P-9"],
      plan_prosjektkode: "P-1", poster: 2,
      ukjent_prosjektkode: true },
    { dato: "2026-08-04", minutter: 450, planlagt_minutter: 450,
      avvik_minutter: 0, prosjektkoder: ["P-1"],
      plan_prosjektkode: "P-1", poster: 1,
      ukjent_prosjektkode: false },
    // DAGEN FØR PLANEN: `planlagt_minutter` er NULL, ikke 0.
    { dato: "2025-12-01", minutter: 450, planlagt_minutter: null,
      avvik_minutter: null, prosjektkoder: ["P-1"],
      plan_prosjektkode: null, poster: 1,
      ukjent_prosjektkode: false },
  ],
  request_id: "r-c",
};

let SVAR;
let SISTE;
let KALL;
let SVARSTATUS;
let TREGE;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  KALL.push({ sti, metode: (opts && opts.method) || "GET" });
  if (TREGE && TREGE.has(sti)) {
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setTimeout(r, 0));
    }
  }
  if (opts && opts.method === "POST") {
    SISTE = { sti, kropp: JSON.parse(opts.body), headers: opts.headers };
    if (SVARSTATUS && SVARSTATUS !== 200) {
      return { ok: false, status: SVARSTATUS,
        json: async () => ({ feil: "lonn_ulovlig_tilstand" }) };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  }
  const oppf = SVAR[sti];
  if (!oppf) {
    return { ok: false, status: 404,
      json: async () => ({ feil: "ikke_funnet" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx(scopes = ["okonomi:read", "bestilling:opprett"]) {
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
  SISTE = undefined;
  SVARSTATUS = 200;
  TREGE = new Set();
  return m;
}

function fullSvar() {
  return {
    "/v1/lonn": BILDE,
    [`/v1/lonn/${S1}/dager`]: DAGER,
    [`/v1/lonn/${S2}/dager`]: { taker_id: S2, dager: [],
                                request_id: "r-d" },
  };
}

// Tabellrekkefølgen: takerne (0), grensene (1) og — når detaljpanelet
// står åpent — dagene (2).
function tabeller(h) {
  return [...h.querySelectorAll("table")];
}

async function apneForste(h) {
  await vent(() => tabeller(h).length >= 2);
  tabeller(h)[0].querySelectorAll("tbody tr")[0]
    .querySelector("button").click();
  await vent(() => tabeller(h).length >= 3);
}

// ---------------------------------------------------------------------
// timer_i_flyttall
// ---------------------------------------------------------------------

test("Lønn: minutter inn, «7:30» ut", () => {
  assert.equal(timeTekst(450), "7:30");
  assert.equal(timeTekst(0), "0:00");
  assert.equal(timeTekst(60), "1:00");
  assert.equal(timeTekst(2250), "37:30");
  // DEN SOM AVSLØRER `/60` UTEN `trunc`.
  assert.equal(timeTekst(59), "0:59");
  assert.equal(timeTekst(-90), "-1:30");
  assert.equal(timeTekst(7.5), "—");
  assert.equal(timeTekst(null), "—");

  // TIMER INN, MINUTTER UT. Avrundingen skjer HER, én gang.
  assert.equal(tilMinutter("7.5"), 450);
  assert.equal(tilMinutter("0.25"), 15);
  assert.equal(tilMinutter("37.5"), 2250);
  // 7,5 × 60 er 450 EKSAKT — også når mellomregningen er et flyttall,
  // fordi `Math.round` tar det siste steget.
  assert.ok(Number.isInteger(tilMinutter("7.5")));
  assert.equal(tilMinutter(""), null);
  assert.equal(tilMinutter(null), null);
  assert.equal(tilMinutter("halvannen"), null);
});

test("Lønn: avviket er en differanse, ikke en prosent", () => {
  // FORTEGNET SKAL SES: mindre enn planlagt er fravær, mer er overtid.
  assert.equal(avvikTekst(150),
    t("ui.lonn.avvik_over").replace("{tid}", "2:30"));
  assert.equal(avvikTekst(-30),
    t("ui.lonn.avvik_under").replace("{tid}", "0:30"));
  assert.equal(avvikTekst(0), t("ui.lonn.uten_avvik"));
  // INGEN PLAN ER IKKE «STEMMER». Uten en plan finnes det ingen
  // sammenligning, og flaten later ikke som noe annet.
  assert.equal(avvikTekst(null), t("ui.lonn.uten_plan"));
  assert.equal(avvikTekst(undefined), t("ui.lonn.uten_plan"));
  assert.equal(planTekst(null, "P-1"), t("ui.lonn.uten_plan"));
  assert.equal(planTekst(450, "P-1"),
    t("ui.lonn.plan_med_kode").replace("{tid}", "7:30")
      .replace("{kode}", "P-1"));
  assert.equal(kildeTekst(null), t("ui.lonn.uten_kilde"));
});

test("Lønn: begge språk navngir hver kilde", () => {
  for (const sprak of ["nb", "en"]) {
    const tekster = locale(sprak);
    for (const k of KILDER) {
      assert.ok(tekster[`ui.lonn.kilde.${k}`],
        `${sprak} mangler kilden ${k}`);
    }
  }
});

// ---------------------------------------------------------------------
// modulen_produserte_lonnsfil og overtid_uten_flagg — flatens halvdel
// ---------------------------------------------------------------------

test("Lønn: flaten genererer ingen fil og setter ingen overtid", () => {
  const kilde = readFileSync(
    join(HER, "..", "static", "js", "flater", "lonn.js"), "utf8");
  // Kommentarene forklarer FRAVÆRET og må derfor ikke telle med.
  const uten = kilde.replace(/^\s*\/\/.*$/gm, "");
  for (const ord of ["download", "Blob", "createObjectURL", "toCSV",
                     "eksport", "utbetal", "fetch(", "XMLHttpRequest"]) {
    assert.ok(!uten.toLowerCase().includes(ord.toLowerCase()),
      `flaten bærer «${ord}» — v1 produserer ingen lønnsfil`);
  }
  // OVERTID SENDES ALDRI. Målt på NYTTELASTEN, ikke på at ordet
  // forekommer: `MERKE` slår opp funntypen `overtid`, og det er riktig
  // bruk.
  const i = kilde.indexOf("registrerTimer(gjeldende.taker_id, {");
  const nyttelast = kilde.slice(i, kilde.indexOf("}, idem)", i));
  assert.ok(i > 0 && !nyttelast.includes("overtid"),
    "flaten sender et overtidsflagg");
  assert.ok(!/id: "[a-z-]*overtid/.test(kilde),
    "flaten tilbyr en overtidskontroll");

  const api = readFileSync(
    join(HER, "..", "static", "js", "api.js"), "utf8");
  assert.ok(
    !/export const (genererLonnsfil|utbetalLonn|eksporterLonn)/.test(api));
  // ALLE FEM SKRIVEVEIENE sender en Idempotency-Key.
  for (const n of ["settLonnsterskler", "registrerLonnstaker",
                   "settArbeidsplan", "registrerTimer",
                   "settLonnstakerAktiv"]) {
    const j = api.indexOf(`export const ${n} =`);
    assert.ok(j > 0, `${n} mangler i api.js`);
    const k = api.indexOf("\n\n", j);
    const kropp = api.slice(j, k === -1 ? api.length : k);
    assert.ok(/idem \|\| nyIdempotensnokkel\(\)/.test(kropp),
      `${n} sender ingen Idempotency-Key`);
  }
});

// ---------------------------------------------------------------------
// Skjermene — og axe på hver av dem
// ---------------------------------------------------------------------

test("Lønn: listen viser plan og ført tid, axe rent", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visLonn(h, ctx());
  await vent(() => tabeller(h).length >= 2);

  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));

  for (const tb of tabeller(h)) {
    assert.ok(tb.querySelector("caption"), "tabell uten caption");
    assert.ok(tb.querySelectorAll('th[scope="col"]').length >= 2);
    assert.ok(tb.closest(".tablewrap"),
      "tabellen mangler sidescrollens container");
  }

  const rader = [...tabeller(h)[0].querySelectorAll("tbody tr")];
  assert.equal(rader.length, 2);
  assert.equal(rader[0].querySelector('th[scope="row"]').textContent,
    "ANS-100");
  // PLANEN MED KODEN, og ført tid som «45:00».
  assert.ok(rader[0].textContent.includes("7:30"));
  assert.ok(rader[0].textContent.includes("P-1"));
  assert.ok(rader[0].textContent.includes("45:00"));
  // MERKET ER TEKST (WCAG 1.4.1).
  assert.ok(rader[0].textContent.includes(t("ui.lonn.merke_overtid")));
  // EN TAKER UTEN PLAN OG UTEN TIMER SIER DET MED ORD.
  assert.ok(rader[1].textContent.includes(t("ui.lonn.uten_plan")));
  assert.ok(rader[1].textContent.includes(t("ui.lonn.uten_timer")));
  assert.ok(rader[1].textContent.includes(t("ui.lonn.status.inaktiv")));

  // OVERTIDEN STÅR FOR SEG i sammendraget.
  assert.ok(h.textContent.includes(
    t("ui.lonn.apne_overtid").replace("{n}", "2")));
  assert.ok(h.textContent.includes(
    t("ui.lonn.avkortet").replace("{vist}", "2")));
  assert.ok(h.textContent.includes(t("ui.lonn.oversikt.hvorfor")));
  // GRENSENE VISES SOM TID, ikke som minuttall.
  assert.ok(h.textContent.includes("37:30"));
});

test("Lønn: tomt register sier hva som mangler, axe rent", async () => {
  SVAR = { "/v1/lonn": TOMT };
  const h = nyHoved();
  visLonn(h, ctx());
  await vent(() => h.textContent.includes(t("ui.lonn.liste.ingen")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  assert.ok(h.textContent.includes(t("ui.lonn.ingen_terskler")));
  assert.ok(!h.textContent.includes(
    t("ui.lonn.apne_overtid").replace("{n}", "0")));
});

// ---------------------------------------------------------------------
// time_uten_arbeidsplan — SAMMENLIGNINGEN ER SKJERMEN
// ---------------------------------------------------------------------

test("Lønn: ført og planlagt står på samme linje", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visLonn(h, ctx());
  await apneForste(h);

  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));

  const rader = [...tabeller(h)[2].querySelectorAll("tbody tr")];
  assert.equal(rader.length, 3);
  // NYESTE ØVERST: 10 timer ført mot 7:30 planlagt, 2:30 over.
  assert.equal(rader[0].querySelector('th[scope="row"]').textContent,
    "2026-08-05");
  assert.ok(rader[0].textContent.includes("10:00"));
  assert.ok(rader[0].textContent.includes("7:30"));
  assert.ok(rader[0].textContent.includes(
    t("ui.lonn.avvik_over").replace("{tid}", "2:30")));
  // KODEN SOM IKKE ER PLANENS ER MERKET, MED ORD.
  assert.ok(rader[0].textContent.includes(t("ui.lonn.merke_kode")));
  // EN DAG SOM STEMMER SIER DET.
  assert.ok(rader[1].textContent.includes(t("ui.lonn.uten_avvik")));
  assert.ok(!rader[1].textContent.includes(t("ui.lonn.merke_kode")));
  // DAGEN FØR PLANEN: «ingen plan», ALDRI «0:00» og aldri «stemmer».
  // Det er hele forskjellen mellom en time som er MÅLT og en som ikke
  // er det.
  const uten = rader[2].textContent;
  assert.ok(uten.includes(t("ui.lonn.uten_plan")));
  assert.ok(!uten.includes(t("ui.lonn.uten_avvik")));
  assert.equal((uten.match(/0:00/g) || []).length, 0);
  assert.ok(h.textContent.includes("ANS-100 · Kari Ansatt"));
});

test("Lønn: en taker uten timer sier det, og tar ikke imot mer",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visLonn(h, ctx());
    await vent(() => tabeller(h).length >= 2);
    tabeller(h)[0].querySelectorAll("tbody tr")[1]
      .querySelector("button").click();
    assert.ok(await vent(() => h.textContent.includes(
      t("ui.lonn.detalj.ingen"))), "tomheten ble aldri sagt");
    for (const id of ["#ln-t-dato", "#ln-p-timer"]) {
      const knapp = h.querySelector(id).closest("form")
        .querySelector("button[type=submit]");
      assert.equal(knapp.disabled, true, id);
    }
    const aktiv = [...h.querySelectorAll("button[type=submit]")]
      .find((b) => b.textContent === t("ui.lonn.knapp.aktiver"));
    assert.ok(aktiv, "aktiveringsknappen mangler eller bærer feil tekst");
    assert.equal(aktiv.disabled, false);
  });

test("Lønn: trege dager tegnes ikke inn i et annet panel", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visLonn(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  TREGE.add(`/v1/lonn/${S1}/dager`);
  const rader = [...tabeller(h)[0].querySelectorAll("tbody tr")];
  rader[0].querySelector("button").click();   // treg
  rader[1].querySelector("button").click();   // rask
  assert.ok(await vent(() => h.textContent.includes(
    t("ui.lonn.detalj.ingen"))), "Bs tomme panel kom aldri");
  await vent(() => false, 40);
  assert.ok(h.textContent.includes(t("ui.lonn.detalj.ingen")),
    "de trege dagene ble tegnet inn i feil panel");
  assert.ok(!h.textContent.includes("10:00"),
    "ANS-100s dager står i ANS-200s panel");
  assert.ok(h.textContent.includes("ANS-200 · Ola Ansatt"));
});

// ---------------------------------------------------------------------
// Skriveveiene
// ---------------------------------------------------------------------

test("Lønn: timene sendes som minutter", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visLonn(h, ctx());
  await apneForste(h);
  assert.equal(h.querySelector("#ln-t-kilderef").required, true);
  const kilder = [...h.querySelector("#ln-t-kilde").options]
    .map((o) => o.value);
  assert.deepEqual(kilder, KILDER);
  // DET FINNES INGEN OVERTIDSAVKRYSNING I SKJEMAET.
  assert.equal(h.querySelectorAll('[id*="overtid"]').length, 0);
  assert.equal(
    h.querySelectorAll('input[type="checkbox"]').length, 0);

  h.querySelector("#ln-t-dato").value = "2026-08-06";
  h.querySelector("#ln-t-timer").value = "7.5";
  h.querySelector("#ln-t-kode").value = "P-1";
  h.querySelector("#ln-t-kilderef").value = "evt_ny";
  h.querySelector("#ln-t-notat").value = "ført";
  h.querySelector("#ln-t-dato").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("/timer"));
  // 7,5 TIME BLIR 450 MINUTTER — et heltall, aldri et flyttall.
  assert.equal(SISTE.kropp.minutter, 450);
  assert.ok(Number.isInteger(SISTE.kropp.minutter));
  assert.equal(SISTE.kropp.kilde, "fort_av_ansatt");
  assert.equal(SISTE.kropp.kilde_ref, "evt_ny");
  assert.ok(!("overtid" in SISTE.kropp));
  assert.ok(SISTE.headers["Idempotency-Key"]);
});

test("Lønn: arbeidsplanen sendes med begrunnelse", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visLonn(h, ctx());
  await apneForste(h);
  assert.equal(h.querySelector("#ln-p-grunn").required, true);
  h.querySelector("#ln-p-timer").value = "7";
  h.querySelector("#ln-p-kode").value = "P-2";
  h.querySelector("#ln-p-fra").value = "2026-09-01";
  h.querySelector("#ln-p-grunn").value = "ny turnus";
  h.querySelector("#ln-p-grunn").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("/plan"));
  assert.equal(SISTE.kropp.planlagt_minutter_dag, 420);
  assert.equal(SISTE.kropp.begrunnelse, "ny turnus");
  for (const sprak of ["nb", "en"]) {
    const hjelp = locale(sprak)["ui.lonn.skjema.gyldig_fra_hjelp"];
    assert.ok(/bakover|backwards/i.test(hjelp),
      `${sprak}: hjelpeteksten sier ikke hva som står på spill`);
  }
});

test("Lønn: grensene er forhåndsutfylt i timer, sendes som minutter",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visLonn(h, ctx());
    await vent(() => !!h.querySelector("#ln-k-dag"));
    // 450 MINUTTER VISES SOM 7,50 TIMER — og sendes tilbake som 450.
    assert.equal(h.querySelector("#ln-k-dag").value, "7.50");
    assert.equal(h.querySelector("#ln-k-uke").value, "37.50");
    assert.equal(h.querySelector("#ln-k-avvik").value, "0.25");
    assert.equal(h.querySelector("#ln-k-utenplan").value, "7");
    h.querySelector("#ln-k-dag").value = "8";
    h.querySelector("#ln-k-dag").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE && SISTE.sti.includes("/terskler"));
    assert.equal(SISTE.kropp.normaltid_minutter_dag, 480);
    assert.equal(SISTE.kropp.normaltid_minutter_uke, 2250);
    assert.equal(SISTE.kropp.avvik_minutter, 15);
    assert.equal(SISTE.kropp.uten_plan_dogn, 7);
  });

test("Lønn: kvitteringen og panelet overlever tegningen", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visLonn(h, ctx());
  await apneForste(h);
  h.querySelector("#ln-t-dato").value = "2026-08-06";
  h.querySelector("#ln-t-timer").value = "1";
  h.querySelector("#ln-t-kode").value = "P-1";
  h.querySelector("#ln-t-kilderef").value = "evt_k";
  h.querySelector("#ln-t-notat").value = "x";
  h.querySelector("#ln-t-dato").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.ok(await vent(() => KALL.filter(
    (k) => k.sti.includes("/dager")).length >= 2),
    "panelet ble aldri gjenåpnet — porten måler ingenting");
  assert.ok(h.textContent.includes(t("ui.lonn.skjema.timer_ok")),
    "kvitteringen forsvant i tegningen");
  assert.ok(h.textContent.includes("ANS-100 · Kari Ansatt"),
    "panelet lukket seg etter en føring");
});

test("Lønn: en 409 sier hva registeret nektet", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visLonn(h, ctx());
  await vent(() => !!h.querySelector("#ln-ny-ref"));
  SVARSTATUS = 409;
  h.querySelector("#ln-ny-ref").value = "ANS-100";
  h.querySelector("#ln-ny-navn").value = "Dublett";
  const skjema = h.querySelector("#ln-ny-ref").closest("form");
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.ok(await vent(() => h.textContent.includes(
    t("ui.lonn.feil.tilstand"))), "tilstandsfeilen ble ikke vist");
  assert.ok(!h.textContent.includes(t("ui.lonn.feil.generell")));
  assert.ok(h.querySelector('[role="alert"]'));
  assert.equal(skjema.querySelector("button[type=submit]").disabled, false);
});

// ---------------------------------------------------------------------
// Scope og språk
// ---------------------------------------------------------------------

test("Lønn: en lesende økt ser registeret, men ingen kontroller",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visLonn(h, ctx(["okonomi:read"]));
    await vent(() => tabeller(h).length >= 2);
    assert.ok(h.textContent.includes("ANS-100"));
    assert.ok(h.textContent.includes("45:00"));
    assert.equal(h.querySelectorAll("form").length, 0);
    assert.equal(h.querySelectorAll("input, select, textarea").length, 0);
    assert.ok([...h.querySelectorAll("button")].some(
      (b) => b.textContent === t("ui.lonn.knapp.apne")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Lønn: ingen hardkodet tekst", async () => {
  const PL = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visLonn(h, ctx());
    await apneForste(h);
    // `th[scope="row"]` er UTELATT: den cellen bærer ansattnummeret,
    // datoen og grensenavnet — altså tenantens egne data.
    for (const node of h.querySelectorAll(
      'h2, h3, h4, label, caption, legend, th[scope="col"], button,'
      + " option")) {
      const s = node.textContent.trim();
      if (!s) continue;
      assert.ok(s.startsWith("PL_"), `hardkodet tekst: «${s}»`);
    }
  } finally {
    settI18nForTest(NB, "nb");
  }
});
