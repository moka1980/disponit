// M-41 betalingsflaten (111) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd på hver
//     skjerm flaten kan stå i.
//   * `modulen_refunderte`: flaten har INGEN «refunder»-knapp.
//     `refundert` kan FØRES, fordi en refusjon kan ha skjedd; den kan
//     ikke UTLØSES.
//   * `betalingsstatus_uten_kilde`: statusen vises ALDRI uten kilden
//     sin, og kildereferansen er påkrevd i skjemaet.
//   * `betalingsmiddel_lagret_i_klartekst`: kortnummeret vises aldri og
//     feltet tømmes etter innsending.
//   * `belop_i_flyttall`: beløp i øre, og avviket som en DIFFERANSE —
//     aldri en prosent flaten fant på.
//   * «Ingen forventning ført» er noe annet enn «stemmer» (WCAG 1.4.1
//     og alminnelig ærlighet).
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
  ABONNEMENTSSTATUSER, KILDER, STATUSER, avvikTekst, belopTekst,
  maskeTekst, statusTekst, tilOre, visBetaling,
} from "../static/js/flater/betaling.js";

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
    subjekter: 40, aktive: 12, med_status: 9, gjennomforte: 6,
    apne_funn: 4, apne_avvik: 2, har_terskel: true,
    terskelversjon: 2, vist: 2,
  },
  subjekter: [
    { subjekt_id: S1, ekstern_ref: "ORD-100", navn: "Kari Kunde",
      aktiv: true, status: "gjennomfort", belop_ore: 149800,
      forventet_ore: 149900, valuta: "NOK",
      betalingsmiddel_maske: "************9010", kilde: "leverandor",
      inntruffet: "2026-08-21", abonnementsstatus: "aktivt",
      hendelser: 2, apne_funn: ["belopsavvik"] },
    { subjekt_id: S2, ekstern_ref: "ORD-200", navn: "Ola Kunde",
      aktiv: false, status: null, belop_ore: null,
      forventet_ore: null, valuta: null, betalingsmiddel_maske: null,
      kilde: null, inntruffet: null, abonnementsstatus: null,
      hendelser: 0, apne_funn: ["uavklart_betaling"] },
  ],
  terskler: {
    uavklart_dogn: 3, belopsavvik_ore: 200, reautorisasjon_dogn: 7,
    versjon: 2, oppdatert: "2026-08-01T09:00:00+00:00",
    oppdatert_av: "kari",
  },
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    subjekter: 0, aktive: 0, med_status: 0, gjennomforte: 0,
    apne_funn: 0, apne_avvik: 0, har_terskel: false,
    terskelversjon: null, vist: 0,
  },
  subjekter: [], terskler: null, request_id: "r-b",
};

const HISTORIKK = {
  subjekt_id: S1,
  hendelser: [
    { hendelse_id: "h-2", status: "gjennomfort", belop_ore: 149800,
      forventet_ore: 149900, valuta: "NOK",
      betalingsmiddel_maske: "************9010", kilde: "leverandor",
      kilde_ref: "evt_b1", inntruffet: "2026-08-21",
      notat: "delbetalt", registrert: "2026-08-21T09:00:00+00:00",
      registrert_av: "kari", endret: true, middel_endret: false },
    { hendelse_id: "h-1", status: "autorisert", belop_ore: 149900,
      forventet_ore: 149900, valuta: "NOK",
      betalingsmiddel_maske: "************9010", kilde: "leverandor",
      kilde_ref: "evt_a1", inntruffet: "2026-08-20",
      notat: "autorisert", registrert: "2026-08-20T09:00:00+00:00",
      registrert_av: "kari", endret: false, middel_endret: false },
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
        json: async () => ({ feil: "betaling_ulovlig_tilstand" }) };
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
    "/v1/betaling": BILDE,
    [`/v1/betaling/${S1}/historikk`]: HISTORIKK,
    [`/v1/betaling/${S2}/historikk`]: { subjekt_id: S2, hendelser: [],
                                        request_id: "r-d" },
  };
}

// Tabellrekkefølgen: subjektene (0), tersklene (1) og — når
// detaljpanelet står åpent — historikken (2).
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
// belop_i_flyttall — OG AVVIKET SOM DIFFERANSE
// ---------------------------------------------------------------------

test("Betaling: beløp i heltallsaritmetikk", () => {
  assert.equal(belopTekst(149900), "1499,00");
  assert.equal(belopTekst(0), "0,00");
  // DEN SOM AVSLØRER `/100`.
  assert.equal(belopTekst(123456799), "1234567,99");
  assert.equal(belopTekst(-5), "-0,05");
  assert.equal(belopTekst(12.5), "—");
  assert.equal(belopTekst(null), "—");

  // KRONER INN, ØRE UT. Et tomt felt er `null`, ikke null kroner:
  // «ingen forventning ført» og «forventet null» er to ulike svar.
  assert.equal(tilOre("1499.00"), 149900);
  assert.equal(tilOre("8.15"), 815);
  assert.equal(tilOre(""), null);
  assert.equal(tilOre(null), null);
  assert.equal(tilOre("gratis"), null);
});

test("Betaling: avviket er en differanse, ikke en prosent", () => {
  // FORTEGNET SKAL SES: betalt for lite og betalt for mye er to helt
  // forskjellige samtaler.
  assert.equal(avvikTekst(149800, 149900),
    t("ui.betaling.avvik_under").replace("{belop}", "1,00"));
  assert.equal(avvikTekst(150000, 149900),
    t("ui.betaling.avvik_over").replace("{belop}", "1,00"));
  assert.equal(avvikTekst(149900, 149900), t("ui.betaling.uten_avvik"));
  // INGEN FORVENTNING ER IKKE «STEMMER». Uten et forventet beløp finnes
  // det ikke noe avvik å måle, og flaten later ikke som noe annet.
  assert.equal(avvikTekst(149900, null),
    t("ui.betaling.uten_forventet"));
  assert.equal(avvikTekst(null, 149900),
    t("ui.betaling.uten_forventet"));
});

test("Betaling: statusen vises aldri uten kilden sin", () => {
  assert.equal(statusTekst("gjennomfort", "leverandor"),
    t("ui.betaling.status_fra")
      .replace("{status}", t("ui.betaling.status.gjennomfort"))
      .replace("{kilde}", t("ui.betaling.kilde.leverandor")));
  // …og «ingen status ført» er et svar, ikke en tom celle.
  assert.equal(statusTekst(null, null), t("ui.betaling.uten_status"));
  assert.equal(maskeTekst(null), t("ui.betaling.uten_middel"));
  assert.equal(maskeTekst("************9010"), "************9010");
});

test("Betaling: begge språk navngir hver status og hver kilde", () => {
  for (const sprak of ["nb", "en"]) {
    const tekster = locale(sprak);
    for (const s of STATUSER) {
      assert.ok(tekster[`ui.betaling.status.${s}`],
        `${sprak} mangler statusen ${s}`);
    }
    for (const k of KILDER) {
      assert.ok(tekster[`ui.betaling.kilde.${k}`],
        `${sprak} mangler kilden ${k}`);
    }
    for (const a of ABONNEMENTSSTATUSER) {
      assert.ok(tekster[`ui.betaling.abo.${a}`],
        `${sprak} mangler abonnementsstatusen ${a}`);
    }
  }
});

// ---------------------------------------------------------------------
// modulen_refunderte — flatens halvdel
// ---------------------------------------------------------------------

test("Betaling: flaten refunderer ingenting", () => {
  const kilde = readFileSync(
    join(HER, "..", "static", "js", "flater", "betaling.js"), "utf8");
  // Kommentarene forklarer FRAVÆRET og må derfor ikke telle med.
  const uten = kilde.replace(/^\s*\/\/.*$/gm, "");
  for (const ord of ["utbetal", "attester", "signer", "kortnummer",
                     "fetch(", "XMLHttpRequest"]) {
    assert.ok(!uten.toLowerCase().includes(ord.toLowerCase()),
      `flaten bærer «${ord}» — v1 refunderer ingenting`);
  }
  // `refunder(?!t)`: «refundert» er en STATUS man registrerer.
  // «Refunder» er handlingen v1 ikke gjør.
  assert.ok(!/refunder(?!t)/i.test(uten),
    "flaten bærer en refusjonshandling");
  // …og `autoriser(?!t)` likeså.
  assert.ok(!/autoriser(?!t)/i.test(uten),
    "flaten bærer en autorisasjonshandling");
  const api = readFileSync(
    join(HER, "..", "static", "js", "api.js"), "utf8");
  assert.ok(!/export const (utforRefusjon|autoriserBetaling)/.test(api));
  // ALLE FEM SKRIVEVEIENE sender en Idempotency-Key.
  for (const n of ["settBetalingsterskler", "registrerBetalingssubjekt",
                   "registrerBetalingsstatus", "settAbonnementsstatus",
                   "settBetalingssubjektAktiv"]) {
    const i = api.indexOf(`export const ${n} =`);
    assert.ok(i > 0, `${n} mangler i api.js`);
    const kropp = api.slice(i, api.indexOf("\n\n", i));
    assert.ok(/idem \|\| nyIdempotensnokkel\(\)/.test(kropp),
      `${n} sender ingen Idempotency-Key`);
  }
});

// ---------------------------------------------------------------------
// Skjermene — og axe på hver av dem
// ---------------------------------------------------------------------

test("Betaling: listen viser status, kilde og avvik, axe rent",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visBetaling(h, ctx());
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
      "ORD-100");
    // STATUSEN OG KILDEN STÅR SAMMEN.
    assert.ok(rader[0].textContent.includes(
      t("ui.betaling.kilde.leverandor")));
    assert.ok(rader[0].textContent.includes("1498,00"));
    // AVVIKET MED FORTEGN.
    assert.ok(rader[0].textContent.includes(
      t("ui.betaling.avvik_under").replace("{belop}", "1,00")));
    assert.ok(rader[0].textContent.includes("************9010"));
    // MERKET ER TEKST (WCAG 1.4.1).
    assert.ok(rader[0].textContent.includes(t("ui.betaling.merke_avvik")));
    // ET SUBJEKT UTEN STATUS SIER DET MED ORD.
    assert.ok(rader[1].textContent.includes(t("ui.betaling.uten_status")));
    assert.ok(rader[1].textContent.includes(t("ui.betaling.uten_middel")));
    assert.ok(rader[1].textContent.includes(
      t("ui.betaling.uten_abonnement")));
    assert.ok(rader[1].textContent.includes(
      t("ui.betaling.status.inaktiv")));

    // AVVIKENE STÅR FOR SEG i sammendraget.
    assert.ok(h.textContent.includes(
      t("ui.betaling.apne_avvik").replace("{n}", "2")));
    assert.ok(h.textContent.includes(
      t("ui.betaling.avkortet").replace("{vist}", "2")));
    assert.ok(h.textContent.includes(t("ui.betaling.oversikt.hvorfor")));
  });

test("Betaling: tomt register sier hva som mangler, axe rent",
  async () => {
    SVAR = { "/v1/betaling": TOMT };
    const h = nyHoved();
    visBetaling(h, ctx());
    await vent(() => h.textContent.includes(
      t("ui.betaling.liste.ingen")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
    assert.ok(h.textContent.includes(t("ui.betaling.ingen_terskler")));
    assert.ok(!h.textContent.includes(
      t("ui.betaling.apne_avvik").replace("{n}", "0")));
  });

// ---------------------------------------------------------------------
// betalingshistorikk_overskrevet — HISTORIKKEN ER SKJERMEN
// ---------------------------------------------------------------------

test("Betaling: historikken merker statusskiftet", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visBetaling(h, ctx());
  await apneForste(h);

  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));

  const rader = [...tabeller(h)[2].querySelectorAll("tbody tr")];
  assert.equal(rader.length, 2);
  // NYESTE ØVERST, og skiftet er merket MED ORD.
  assert.equal(rader[0].querySelector('th[scope="row"]').textContent,
    "2026-08-21");
  assert.ok(rader[0].textContent.includes(t("ui.betaling.merke_skifte")));
  assert.ok(rader[0].textContent.includes("evt_b1"));
  assert.ok(rader[0].textContent.includes("delbetalt"));
  // DEN FORRIGE LINJEN BLIR STÅENDE, uten skiftemerke.
  assert.ok(!rader[1].textContent.includes(t("ui.betaling.merke_skifte")));
  assert.ok(rader[1].textContent.includes("evt_a1"));
  // BETALINGSMIDDELET ER UENDRET — det er grunnlaget
  // `samme_betalingsmiddel` en dag skal hvile på.
  assert.ok(!h.textContent.includes(
    t("ui.betaling.merke_middelskifte")));
  assert.ok(h.textContent.includes("ORD-100 · Kari Kunde"));
});

test("Betaling: et subjekt uten status sier det, og tar ikke imot mer",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visBetaling(h, ctx());
    await vent(() => tabeller(h).length >= 2);
    tabeller(h)[0].querySelectorAll("tbody tr")[1]
      .querySelector("button").click();
    assert.ok(await vent(() => h.textContent.includes(
      t("ui.betaling.detalj.ingen"))), "tomheten ble aldri sagt");
    // ET DEAKTIVERT SUBJEKT TAR IKKE IMOT NYE STATUSER…
    for (const id of ["#bt-st-belop", "#bt-ab-grunn"]) {
      const knapp = h.querySelector(id).closest("form")
        .querySelector("button[type=submit]");
      assert.equal(knapp.disabled, true, id);
    }
    // …men det KAN aktiveres igjen.
    const aktiv = [...h.querySelectorAll("button[type=submit]")]
      .find((b) => b.textContent === t("ui.betaling.knapp.aktiver"));
    assert.ok(aktiv, "aktiveringsknappen mangler eller bærer feil tekst");
    assert.equal(aktiv.disabled, false);
  });

test("Betaling: en treg historikk tegnes ikke inn i et annet panel",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visBetaling(h, ctx());
    await vent(() => tabeller(h).length >= 2);
    TREGE.add(`/v1/betaling/${S1}/historikk`);
    const rader = [...tabeller(h)[0].querySelectorAll("tbody tr")];
    rader[0].querySelector("button").click();   // treg
    rader[1].querySelector("button").click();   // rask
    assert.ok(await vent(() => h.textContent.includes(
      t("ui.betaling.detalj.ingen"))), "Bs tomme historikk kom aldri");
    await vent(() => false, 40);
    assert.ok(h.textContent.includes(t("ui.betaling.detalj.ingen")),
      "den trege historikken ble tegnet inn i feil panel");
    assert.ok(!h.textContent.includes("delbetalt"),
      "ORD-100s linjer står i ORD-200s panel");
    assert.ok(h.textContent.includes("ORD-200 · Ola Kunde"));
  });

// ---------------------------------------------------------------------
// Skriveveiene
// ---------------------------------------------------------------------

test("Betaling: kortnummeret sendes én gang og tømmes etterpå",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visBetaling(h, ctx());
    await apneForste(h);
    const middel = h.querySelector("#bt-st-middel");
    // FELTET ER TOMT OG UTEN AUTOFULLFØRING.
    assert.equal(middel.value, "");
    assert.equal(middel.getAttribute("autocomplete"), "off");
    // KILDEREFERANSEN ER PÅKREVD — en status uten kilde er en påstand.
    assert.equal(h.querySelector("#bt-st-kilderef").required, true);
    const kilder = [...h.querySelector("#bt-st-kilde").options]
      .map((o) => o.value);
    assert.deepEqual(kilder, KILDER);

    middel.value = "4571 1234 5678 9010";
    h.querySelector("#bt-st-belop").value = "1499.00";
    h.querySelector("#bt-st-forventet").value = "1499.00";
    h.querySelector("#bt-st-kilderef").value = "evt_ny";
    h.querySelector("#bt-st-dato").value = "2026-08-22";
    h.querySelector("#bt-st-notat").value = "ført";
    middel.closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE && SISTE.sti.includes("/status"));
    assert.equal(SISTE.kropp.belop_ore, 149900);
    assert.equal(SISTE.kropp.forventet_ore, 149900);
    assert.equal(SISTE.kropp.betalingsmiddel, "4571 1234 5678 9010");
    assert.equal(SISTE.kropp.kilde, "leverandor");
    assert.equal(SISTE.kropp.kilde_ref, "evt_ny");
    assert.ok(SISTE.headers["Idempotency-Key"]);

    // NUMMERET BLIR IKKE STÅENDE — målt ETTER at flaten har tegnet seg
    // om, for det er den tilstanden brukeren ser.
    assert.ok(await vent(() => KALL.filter(
      (k) => k.sti === "/v1/betaling").length >= 2),
      "flaten tegnet seg aldri om");
    assert.ok(!h.textContent.includes("4571 1234 5678 9010"));
    assert.ok(![...h.querySelectorAll("input")].some(
      (i) => i.value.includes("5678")),
      "kortnummeret står fortsatt i et felt");
  });

test("Betaling: et tomt forventet beløp sendes som null", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visBetaling(h, ctx());
  await apneForste(h);
  h.querySelector("#bt-st-belop").value = "1499.00";
  h.querySelector("#bt-st-forventet").value = "";
  h.querySelector("#bt-st-kilderef").value = "evt_u";
  h.querySelector("#bt-st-dato").value = "2026-08-22";
  h.querySelector("#bt-st-notat").value = "uten forventning";
  h.querySelector("#bt-st-belop").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("/status"));
  // NULL, IKKE 0. «Ingen forventning ført» og «forventet null» er to
  // helt forskjellige svar, og bare det ene gir et avvik å måle.
  assert.equal(SISTE.kropp.forventet_ore, null);
  assert.equal(SISTE.kropp.betalingsmiddel, null);
});

test("Betaling: abonnementsperioden sendes med begrunnelse", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visBetaling(h, ctx());
  await apneForste(h);
  const statuser = [...h.querySelector("#bt-ab-status").options]
    .map((o) => o.value);
  assert.deepEqual(statuser, ABONNEMENTSSTATUSER);
  assert.equal(h.querySelector("#bt-ab-grunn").required, true);
  h.querySelector("#bt-ab-status").value = "i_restanse";
  h.querySelector("#bt-ab-fra").value = "2026-09-01";
  h.querySelector("#bt-ab-grunn").value = "ubetalt faktura";
  h.querySelector("#bt-ab-grunn").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("/abonnement"));
  assert.equal(SISTE.kropp.status, "i_restanse");
  assert.equal(SISTE.kropp.begrunnelse, "ubetalt faktura");
  for (const sprak of ["nb", "en"]) {
    const hjelp = locale(sprak)["ui.betaling.skjema.begrunnelse_hjelp"];
    assert.ok(/etterprøv|review|tjenesten|service/i.test(hjelp),
      `${sprak}: hjelpeteksten sier ikke hva som står på spill`);
  }
});

test("Betaling: grensene er forhåndsutfylt, avviket i kroner",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visBetaling(h, ctx());
    await vent(() => !!h.querySelector("#bt-t-avvik"));
    assert.equal(h.querySelector("#bt-t-uavklart").value, "3");
    // 200 ØRE VISES SOM 2,00 KRONER — og sendes tilbake som 200.
    assert.equal(h.querySelector("#bt-t-avvik").value, "2.00");
    assert.equal(h.querySelector("#bt-t-reaut").value, "7");
    h.querySelector("#bt-t-avvik").value = "5.50";
    h.querySelector("#bt-t-avvik").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE && SISTE.sti.includes("/terskler"));
    assert.equal(SISTE.kropp.belopsavvik_ore, 550);
    assert.equal(SISTE.kropp.uavklart_dogn, 3);
  });

test("Betaling: kvitteringen og panelet overlever tegningen",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visBetaling(h, ctx());
    await apneForste(h);
    h.querySelector("#bt-st-belop").value = "10";
    h.querySelector("#bt-st-kilderef").value = "evt_k";
    h.querySelector("#bt-st-dato").value = "2026-08-22";
    h.querySelector("#bt-st-notat").value = "x";
    h.querySelector("#bt-st-belop").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    assert.ok(await vent(() => KALL.filter(
      (k) => k.sti.includes("/historikk")).length >= 2),
      "panelet ble aldri gjenåpnet — porten måler ingenting");
    assert.ok(h.textContent.includes(t("ui.betaling.skjema.status_ok")),
      "kvitteringen forsvant i tegningen");
    assert.ok(h.textContent.includes("ORD-100 · Kari Kunde"),
      "panelet lukket seg etter en føring");
  });

test("Betaling: en 409 sier hva registeret nektet", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visBetaling(h, ctx());
  await vent(() => !!h.querySelector("#bt-ny-ref"));
  SVARSTATUS = 409;
  h.querySelector("#bt-ny-ref").value = "ORD-100";
  h.querySelector("#bt-ny-navn").value = "Dublett";
  const skjema = h.querySelector("#bt-ny-ref").closest("form");
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.ok(await vent(() => h.textContent.includes(
    t("ui.betaling.feil.tilstand"))), "tilstandsfeilen ble ikke vist");
  assert.ok(!h.textContent.includes(t("ui.betaling.feil.generell")));
  assert.ok(h.querySelector('[role="alert"]'));
  assert.equal(skjema.querySelector("button[type=submit]").disabled, false);
});

// ---------------------------------------------------------------------
// Scope og språk
// ---------------------------------------------------------------------

test("Betaling: en lesende økt ser registeret, men ingen kontroller",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visBetaling(h, ctx(["okonomi:read"]));
    await vent(() => tabeller(h).length >= 2);
    assert.ok(h.textContent.includes("ORD-100"));
    assert.ok(h.textContent.includes("************9010"));
    assert.equal(h.querySelectorAll("form").length, 0);
    assert.equal(h.querySelectorAll("input, select, textarea").length, 0);
    assert.ok([...h.querySelectorAll("button")].some(
      (b) => b.textContent === t("ui.betaling.knapp.apne")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Betaling: ingen hardkodet tekst", async () => {
  const PL = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visBetaling(h, ctx());
    await apneForste(h);
    // `th[scope="row"]` er UTELATT: den cellen bærer referansen, datoen
    // og grensenavnet — altså tenantens egne data.
    for (const node of h.querySelectorAll(
      'h2, h3, h4, label, caption, th[scope="col"], button, option')) {
      const s = node.textContent.trim();
      if (!s) continue;
      assert.ok(s.startsWith("PL_"), `hardkodet tekst: «${s}»`);
    }
  } finally {
    settI18nForTest(NB, "nb");
  }
});
