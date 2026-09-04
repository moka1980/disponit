// M-54 EHF-flaten (121) — flateporten (jsdom + axe).
//
// Portene her måler det modulen står og faller på:
//
//   * `validering_mot_utlopt_skjema`: versjonen vises ALDRI uten om
//     settet gjelder i dag, og valideringsskjemaet tilbyr bare
//     gyldige sett. En foreldet dom ser velformet ut og er gal.
//   * `modulen_sendte_faktura`: ingen «send»-knapp, ingen mottaker,
//     ingen signatur. «Klar til signering» er en tilstand hos oss, og
//     hjelpeteksten sier det.
//   * `retting_uten_avviksreferanse`: rettingsskjemaet velger et
//     avvik fra en liste, og `uten_grunnlag`-avvik står ikke i den.
//   * DET TREDJE UTFALLET: `uten_grunnlag` er ikke stille grønt —
//     tallet står på dommen, og et tomt felt skilles fra et felt som
//     ikke fantes.
//
// Ingen delt fixture (m16-formen): hver test bygger sin egen skjerm.
import { readFileSync } from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import {
  ALVORLIGHETER, KRAVTYPER, RETNINGER, STANDARDER, avvikstabell,
  dokumenttabell, domTekst, funnetTekst, regelsettTekst, regeltabell,
  rettingTekst, utlopTekst, valideringstabell, visEhf,
} from "../static/js/flater/ehf.js";

settI18nForTest(NB, "nb");

const S1 = "11111111-1111-1111-1111-111111111111";
const S2 = "22222222-2222-2222-2222-222222222222";
const D1 = "aaaaaaaa-1111-1111-1111-111111111111";
const V1 = "bbbbbbbb-1111-1111-1111-111111111111";
const A1 = "cccccccc-1111-1111-1111-111111111111";
const A2 = "cccccccc-2222-2222-2222-222222222222";
const F1 = "dddddddd-1111-1111-1111-111111111111";
const F2 = "dddddddd-2222-2222-2222-222222222222";
const SHA = "a".repeat(64);

const BILDE = {
  sammendrag: {
    regelsett: 2, gyldige_regelsett: 1, utlopte_regelsett: 1,
    dokumenter: 1, validerte: 1, med_feil: 1, uten_grunnlag: 1,
    uvaliderte: 0, dommer_under_utlopt: 1, rettinger: 1,
    klare_rettinger: 0, apne_funn: 2, har_krav: true,
    kravversjon: 1, vist: 1,
  },
  regelsett: [
    { regelsett_id: S1, standard: "ehf", versjon: "3.0",
      gyldig_fra: "2024-01-01", gyldig_til: "2026-09-01",
      gyldig_naa: false, dogn_til_utlop: -5, innhold_sha256: SHA,
      kilde_url: null, registrert: "2026-01-02T09:00:00+00:00",
      registrert_av: "kari", antall_regler: 5,
      antall_valideringer: 1 },
    { regelsett_id: S2, standard: "peppol_bis", versjon: "3.0.15",
      gyldig_fra: "2025-01-01", gyldig_til: null, gyldig_naa: true,
      dogn_til_utlop: null, innhold_sha256: "b".repeat(64),
      kilde_url: "https://docs.peppol.eu/",
      registrert: "2026-01-02T09:00:00+00:00", registrert_av: "kari",
      antall_regler: 3, antall_valideringer: 0 },
  ],
  dokumenter: [
    { dokument_id: D1, retning: "utgaaende", ekstern_ref: "F-2026-1",
      motpart: "Kunde AS", fakturadato: "2026-09-01",
      innhold_sha256: SHA, innhold_bytes: 8192,
      registrert: "2026-09-01T09:00:00+00:00", registrert_av: "kari",
      antall_felt: 6, validering_id: V1, standard: "ehf",
      versjon: "3.0", antall_regler: 5, antall_feil: 2,
      antall_advarsler: 1, antall_uten_grunnlag: 1, gyldig: false,
      validert: "2026-09-02T09:00:00+00:00",
      regelsett_gyldig_naa: false, antall_rettinger: 1,
      klare_rettinger: 0, antall_valideringer: 1 },
  ],
  krav: { utlopsvarsel_dogn: 30, avviksfrist_dogn: 7, versjon: 1,
          oppdatert: "2026-08-01T09:00:00+00:00",
          oppdatert_av: "kari" },
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    regelsett: 0, gyldige_regelsett: 0, utlopte_regelsett: 0,
    dokumenter: 0, validerte: 0, med_feil: 0, uten_grunnlag: 0,
    uvaliderte: 0, dommer_under_utlopt: 0, rettinger: 0,
    klare_rettinger: 0, apne_funn: 0, har_krav: false,
    kravversjon: null, vist: 0,
  },
  regelsett: [], dokumenter: [], krav: null, request_id: "r-b",
};

const REGLER = {
  regelsett_id: S1, request_id: "r-c",
  regler: [
    { regel_id: "e1", kode: "EHF-002", sti: "Invoice/Currency",
      krav: "i_kodeliste", kodeverdi: ["NOK", "EUR"], sum_sti: null,
      alvorlighet: "feil", beskrivelse: "Valuta i kodelisten",
      registrert: "2026-01-02T09:00:00+00:00", registrert_av: "kari" },
    { regel_id: "e2", kode: "EHF-003", sti: "Invoice/Total",
      krav: "lik_sum", kodeverdi: [],
      sum_sti: "Invoice/Line/Amount", alvorlighet: "feil",
      beskrivelse: "Totalen er linjesummen",
      registrert: "2026-01-02T09:00:00+00:00", registrert_av: "kari" },
  ],
};

const AVVIK = {
  validering_id: V1, request_id: "r-d",
  avvik: [
    { avvik_id: A1, regelkode: "EHF-002", alvorlighet: "feil",
      sti: "Invoice/Currency", funnet_verdi: "USD",
      forventet: "NOK, EUR", beskrivelse: "Valuta i kodelisten",
      retting_id: null, felt_sti: null, fra_verdi: null,
      til_verdi: null, retting_begrunnelse: null,
      klar_til_signering: false, klar_ts: null, klar_av: null },
    { avvik_id: A2, regelkode: "EHF-005",
      alvorlighet: "uten_grunnlag", sti: "Invoice/BuyerRef",
      funnet_verdi: null, forventet: "feltet må ikke være tomt",
      beskrivelse: "Kjøperreferanse", retting_id: null,
      felt_sti: null, fra_verdi: null, til_verdi: null,
      retting_begrunnelse: null, klar_til_signering: false,
      klar_ts: null, klar_av: null },
  ],
};

const VALIDERINGER = {
  dokument_id: D1, request_id: "r-e",
  valideringer: [
    { validering_id: V1, regelsett_id: S1, standard: "ehf",
      versjon: "3.0", antall_regler: 5, antall_feil: 2,
      antall_advarsler: 1, antall_uten_grunnlag: 1, gyldig: false,
      regelsett_gyldig_naa: false,
      validert: "2026-09-02T09:00:00+00:00", validert_av: "kari" },
  ],
};

const FUNN = {
  request_id: "r-f",
  funn: [
    { funn_id: F1, funntype: "validering_mot_utlopt_regelsett",
      regelsett_id: null, dokument_id: null, validering_id: V1,
      standard: "ehf", regelsettversjon: "3.0",
      ekstern_ref: "F-2026-1", over_grense: 5, detalj: "ehf 3.0",
      kravversjon: 1, forst_sett: "2026-09-06T09:00:00+00:00",
      sist_sett_sveip: "2026-09-06T09:00:00+00:00", apen: true,
      lukket_ts: null, lukket_av: null, lukkenotat: null },
    { funn_id: F2, funntype: "avvik_uten_retting",
      regelsett_id: null, dokument_id: null, validering_id: V1,
      standard: "ehf", regelsettversjon: "3.0",
      ekstern_ref: "F-2026-1", over_grense: 2, detalj: null,
      kravversjon: 1, forst_sett: "2026-09-06T09:00:00+00:00",
      sist_sett_sveip: "2026-09-06T09:00:00+00:00", apen: true,
      lukket_ts: null, lukket_av: null, lukkenotat: null },
  ],
};

let SVAR;
let SISTE;
let KALL;
let SVARSTATUS;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  KALL.push({ sti, metode: (opts && opts.method) || "GET" });
  if (opts && opts.method === "POST") {
    SISTE = { sti, kropp: JSON.parse(opts.body), headers: opts.headers };
    if (SVARSTATUS && SVARSTATUS !== 200) {
      return { ok: false, status: SVARSTATUS,
        json: async () => ({ feil: "ehf_ulovlig_tilstand" }) };
    }
    return { ok: true, status: 200,
             json: async () => ({ ok: true, antall_regler: 5,
                                  antall_feil: 2,
                                  antall_advarsler: 1,
                                  antall_uten_grunnlag: 1,
                                  gyldig: false }) };
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

async function vent(pred, n = 120) {
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
  KALL = []; SISTE = undefined; SVARSTATUS = 200;
  return m;
}

function fullSvar() {
  return {
    "/v1/ehf": BILDE,
    "/v1/ehf/funn": FUNN,
    [`/v1/ehf/regelsett/${S1}/regler`]: REGLER,
    [`/v1/ehf/regelsett/${S2}/regler`]: { regelsett_id: S2,
                                          regler: [],
                                          request_id: "r-g" },
    [`/v1/ehf/validering/${V1}/avvik`]: AVVIK,
    [`/v1/ehf/dokument/${D1}/valideringer`]: VALIDERINGER,
  };
}

function tabeller(h) {
  return [...h.querySelectorAll("table")];
}

async function apneDokument(h) {
  await vent(() => tabeller(h).length >= 2);
  const dok = tabeller(h).find(
    (tb) => tb.querySelector("caption").textContent
      === t("ui.ehf.dokumenter.tittel"));
  dok.querySelector("tbody tr button").click();
  // VENT PÅ NOE SOM BARE FINNES I PANELET (118s lærdom).
  await vent(() => [...h.querySelectorAll("caption")].some(
    (c) => c.textContent === t("ui.ehf.valideringer.tittel")));
}

// ---------------------------------------------------------------------
// validering_mot_utlopt_skjema — VERSJONEN ALDRI UTEN GYLDIGHETEN
// ---------------------------------------------------------------------

test("EHF: regelsettet vises aldri uten om det gjelder i dag", () => {
  // EN FORELDET REGEL SER NØYAKTIG UT SOM EN RIKTIG REGEL. Versjonen
  // alene er nettopp den opplysningen som gjør en foreldet dom umulig
  // å skille fra en riktig.
  //
  // MUTASJONEN SOM DREPER DENNE: returner bare «EHF 3.0».
  const gyldig = regelsettTekst({ standard: "ehf", versjon: "3.0",
                                  gyldig_naa: true });
  const utlopt = regelsettTekst({ standard: "ehf", versjon: "3.0",
                                  gyldig_naa: false });
  assert.ok(gyldig.includes("3.0") && utlopt.includes("3.0"));
  assert.notEqual(gyldig, utlopt, "gyldig og utløpt leses likt");
  assert.equal(utlopt, t("ui.ehf.regelsett_utlopt")
    .replace("{navn}", "EHF 3.0"));
  // …og uten flagget sier vi INGENTING om gyldigheten framfor å gjette.
  assert.equal(regelsettTekst({ standard: "ehf", versjon: "3.0" }),
    "EHF 3.0");
  assert.equal(regelsettTekst(null), t("ui.ehf.uten_regelsett"));
});

test("EHF: dokumentraden bærer regelsettversjonen og gyldigheten",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visEhf(h, ctx());
    await vent(() => tabeller(h).length >= 2);
    const dok = tabeller(h).find(
      (tb) => tb.querySelector("caption").textContent
        === t("ui.ehf.dokumenter.tittel"));
    const rad = dok.querySelector("tbody tr").textContent;
    assert.ok(rad.includes("3.0"), rad);
    assert.ok(rad.includes(t("ui.ehf.regelsett_utlopt")
      .replace("{navn}", "EHF 3.0")), rad);
  });

test("EHF: en dom felt under et utløpt sett sies høyt", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visEhf(h, ctx());
  await apneDokument(h);
  const varsel = [...h.querySelectorAll('[role="alert"]')]
    .map((n) => n.textContent);
  assert.ok(varsel.includes(t("ui.ehf.dom_under_utlopt_varsel")),
    varsel);
});

test("EHF: valideringsskjemaet tilbyr bare gyldige sett med regler",
  async () => {
    // Døra nekter mot et utløpt sett, og en knapp som alltid feiler er
    // verre enn en valgmulighet som ikke finnes.
    SVAR = fullSvar();
    const h = nyHoved();
    visEhf(h, ctx());
    await apneDokument(h);
    await vent(() => h.querySelector("#ef-v-sett") !== null);
    const verdier = [...h.querySelector("#ef-v-sett").options]
      .map((o) => o.value).filter(Boolean);
    assert.deepEqual(verdier, [S2],
      "et utløpt regelsett ble tilbudt for validering");
  });

test("EHF: uten et gyldig sett finnes ingen valideringsknapp",
  async () => {
    SVAR = { ...fullSvar(), "/v1/ehf": {
      ...BILDE,
      regelsett: [BILDE.regelsett[0]],
      sammendrag: { ...BILDE.sammendrag, gyldige_regelsett: 0 } } };
    const h = nyHoved();
    visEhf(h, ctx());
    await apneDokument(h);
    const knapp = [...h.querySelectorAll("button")].find(
      (b) => b.textContent === t("ui.ehf.knapp.valider"));
    assert.equal(knapp, undefined, "valideringsknappen sto der");
    const varsel = [...h.querySelectorAll('[role="alert"]')]
      .map((n) => n.textContent);
    assert.ok(varsel.includes(t("ui.ehf.validering.ingen_gyldige")));
    // …og sammendraget sier at modulen har sluttet å virke.
    assert.ok(h.textContent.includes(
      t("ui.ehf.ingen_gyldig_regelsett")));
  });

test("EHF: valideringen melder alle fire tallene", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visEhf(h, ctx());
  await apneDokument(h);
  await vent(() => h.querySelector("#ef-v-sett") !== null);
  const valg = h.querySelector("#ef-v-sett");
  valg.value = S2;
  valg.dispatchEvent(new window.Event("change"));
  valg.form.dispatchEvent(new window.Event("submit",
    { cancelable: true }));
  await vent(() => SISTE !== undefined);
  assert.equal(SISTE.sti, `/v1/ehf/dokument/${D1}/valider`);
  assert.equal(SISTE.kropp.regelsett_id, S2);
  await vent(() => h.textContent.includes(
    t("ui.ehf.skjema.validert")
      .replace("{regler}", "5").replace("{feil}", "2")
      .replace("{advarsler}", "1").replace("{utenfor}", "1")));
});

test("EHF: en avvist validering sier at den ble avvist", async () => {
  // 409 HAR TO GRUNNER (CodeRabbit): et utløpt regelsett, og et
  // dokument som alt er dømt mot dette settet. Å alltid si «utløpt»
  // ville sendt den som gjentok en validering på jakt etter et
  // problem som ikke fantes.
  SVAR = fullSvar();
  const h = nyHoved();
  visEhf(h, ctx());
  await apneDokument(h);
  await vent(() => h.querySelector("#ef-v-sett") !== null);
  SVARSTATUS = 409;
  const valg = h.querySelector("#ef-v-sett");
  valg.value = S2;
  valg.dispatchEvent(new window.Event("change"));
  valg.form.dispatchEvent(new window.Event("submit",
    { cancelable: true }));
  await vent(() => h.textContent.includes(
    t("ui.ehf.feil.validering_avvist")));
  assert.ok(h.textContent.includes(
    t("ui.ehf.feil.validering_avvist")));
});

// ---------------------------------------------------------------------
// DET TREDJE UTFALLET
// ---------------------------------------------------------------------

test("EHF: dommen bærer alle fire tallene, utenfor inkludert", () => {
  // En leser som bare ser «2 feil» vet ikke om resten var grønn eller
  // UDØMT — og tallet som mangler er det farligste av dem.
  const tekst = domTekst({ validering_id: V1, antall_regler: 5,
                           antall_feil: 2, antall_advarsler: 1,
                           antall_uten_grunnlag: 1, gyldig: false });
  for (const n of ["5", "2", "1"]) assert.ok(tekst.includes(n), tekst);
  // …OG NÅR DEN ER NULL STÅR DEN LIKEVEL.
  const rent = domTekst({ validering_id: V1, antall_regler: 5,
                          antall_feil: 0, antall_advarsler: 0,
                          antall_uten_grunnlag: 0, gyldig: true });
  assert.ok(rent.includes("0"), rent);
  assert.equal(domTekst({}), t("ui.ehf.uvalidert"));
});

test("EHF: regler uten grunnlag sies høyt", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visEhf(h, ctx());
  await apneDokument(h);
  const varsel = [...h.querySelectorAll('[role="alert"]')]
    .map((n) => n.textContent);
  assert.ok(varsel.includes(t("ui.ehf.uten_grunnlag_varsel")
    .replace("{n}", "1")), varsel);
});

test("EHF: tomt felt og manglende felt er to forskjellige svar", () => {
  assert.equal(funnetTekst(null), t("ui.ehf.feltet_fantes_ikke"));
  assert.equal(funnetTekst(""), t("ui.ehf.feltet_var_tomt"));
  assert.equal(funnetTekst("USD"), "USD");
  assert.notEqual(funnetTekst(null), funnetTekst(""));
  // …og avvikstabellen bruker skillet.
  const node = avvikstabell(AVVIK.avvik);
  const rader = [...node.querySelectorAll("tbody tr")]
    .map((r) => r.textContent);
  assert.ok(rader[0].includes("USD"), rader[0]);
  assert.ok(rader[1].includes(t("ui.ehf.feltet_fantes_ikke")),
    rader[1]);
});

// ---------------------------------------------------------------------
// modulen_sendte_faktura OG retting_uten_avviksreferanse
// ---------------------------------------------------------------------

test("EHF: ingen kontroll sender en faktura", async () => {
  // 121 har ingen mottaker og ingen utboks. «Klar til signering» er en
  // tilstand HOS OSS — og hjelpeteksten sier det, ellers kunne ordet
  // leses som «sendt».
  SVAR = fullSvar();
  const h = nyHoved();
  visEhf(h, ctx());
  await apneDokument(h);
  const kontroller = [...h.querySelectorAll("button, a[href]")]
    .map((n) => n.textContent.toLowerCase());
  for (const k of kontroller) {
    assert.ok(!/send|lever|submit|signer nå/.test(k),
      `utgående kontroll: «${k}»`);
  }
  for (const kall of KALL) {
    assert.ok(kall.sti.startsWith("/v1/ehf"), kall.sti);
  }
  assert.ok(h.textContent.includes(t("ui.ehf.oversikt.hvorfor")));
});

test("EHF: rettingsskjemaet tilbyr ikke avvik uten grunnlag",
  async () => {
    // Døra nekter, og en retting der vi ikke kunne dømme ville endret
    // fakturaen fordi vi manglet data — ikke fordi noe var galt.
    SVAR = fullSvar();
    const h = nyHoved();
    visEhf(h, ctx());
    await apneDokument(h);
    await vent(() => h.querySelector("#ef-x-avvik") !== null);
    const verdier = [...h.querySelector("#ef-x-avvik").options]
      .map((o) => o.value).filter(Boolean);
    assert.deepEqual(verdier, [A1],
      "et avvik uten grunnlag ble tilbudt for retting");
  });

test("EHF: rettingen sendes med avvik, felt, verdi og begrunnelse",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visEhf(h, ctx());
    await apneDokument(h);
    await vent(() => h.querySelector("#ef-x-avvik") !== null);
    const valg = h.querySelector("#ef-x-avvik");
    const sti = h.querySelector("#ef-x-sti");
    const til = h.querySelector("#ef-x-til");
    const begr = h.querySelector("#ef-x-begrunnelse");
    const lagre = [...h.querySelectorAll("button")].find(
      (b) => b.textContent === t("ui.ehf.knapp.lagre_retting"));
    assert.equal(lagre.disabled, true);
    valg.value = A1;
    valg.dispatchEvent(new window.Event("change"));
    // STIEN FYLLES FRA AVVIKET: en skrivefeil der ville gitt en
    // retting av et felt ingen så på.
    assert.equal(sti.value, "Invoice/Currency");
    til.value = "NOK";
    til.dispatchEvent(new window.Event("input"));
    assert.equal(lagre.disabled, true, "uten begrunnelse holdt");
    begr.value = "valutaen skal være NOK";
    begr.dispatchEvent(new window.Event("input"));
    assert.equal(lagre.disabled, false);
    valg.form.dispatchEvent(new window.Event("submit",
      { cancelable: true }));
    await vent(() => SISTE !== undefined);
    assert.equal(SISTE.sti, `/v1/ehf/avvik/${A1}/retting`);
    assert.equal(SISTE.kropp.felt_sti, "Invoice/Currency");
    assert.equal(SISTE.kropp.til_verdi, "NOK");
    // FRA-VERDIEN KOMMER FRA AVVIKET — hva som FAKTISK sto der.
    assert.equal(SISTE.kropp.fra_verdi, "USD");
    assert.ok(SISTE.headers["Idempotency-Key"]);
  });

test("EHF: klarmerking sier at ingenting er sendt", async () => {
  const medRetting = {
    ...AVVIK,
    avvik: [{ ...AVVIK.avvik[0], retting_id: "r-1",
              felt_sti: "Invoice/Currency", fra_verdi: "USD",
              til_verdi: "NOK", retting_begrunnelse: "skal være NOK",
              klar_til_signering: false },
             AVVIK.avvik[1]] };
  SVAR = { ...fullSvar(), [`/v1/ehf/validering/${V1}/avvik`]:
           medRetting };
  const h = nyHoved();
  visEhf(h, ctx());
  await apneDokument(h);
  const knapp = [...h.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.ehf.knapp.merk_klar")
      .replace("{kode}", "EHF-002"));
  assert.ok(knapp, "klarknappen manglet");
  assert.ok(h.textContent.includes(t("ui.ehf.klar_hjelp")));
  knapp.click();
  await vent(() => SISTE !== undefined);
  assert.equal(SISTE.sti, "/v1/ehf/retting/r-1/klar");
  await vent(() => h.textContent.includes(
    t("ui.ehf.skjema.klar_ok")));
});

test("EHF: en avvist klarmerking sier hvorfor", async () => {
  const medRetting = {
    ...AVVIK,
    avvik: [{ ...AVVIK.avvik[0], retting_id: "r-1",
              felt_sti: "Invoice/Currency", fra_verdi: "USD",
              til_verdi: "NOK", retting_begrunnelse: "skal være NOK",
              klar_til_signering: false },
             AVVIK.avvik[1]] };
  SVAR = { ...fullSvar(), [`/v1/ehf/validering/${V1}/avvik`]:
           medRetting };
  const h = nyHoved();
  visEhf(h, ctx());
  await apneDokument(h);
  SVARSTATUS = 409;
  const knapp = [...h.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.ehf.knapp.merk_klar")
      .replace("{kode}", "EHF-002"));
  knapp.click();
  await vent(() => h.textContent.includes(
    t("ui.ehf.feil.urettet_formfeil")));
  assert.equal(knapp.disabled, false, "knappen ble liggende død");
});

// ---------------------------------------------------------------------
// FUNN, REGLER OG SLUTTDATO
// ---------------------------------------------------------------------

test("EHF: dom under utløpt regelsett tilbys ikke for lukking",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visEhf(h, ctx());
    await vent(() => h.querySelector("#ef-f-valg") !== null);
    const verdier = [...h.querySelector("#ef-f-valg").options]
      .map((o) => o.value).filter(Boolean);
    assert.deepEqual(verdier, [F2],
      "det uviskbare funnet ble tilbudt for lukking");
    // …men det STÅR i tabellen.
    const funntabell = [...h.querySelectorAll("table")].find(
      (tb) => tb.querySelector("caption").textContent
        === t("ui.ehf.funn.tittel"));
    assert.ok(funntabell.textContent.includes(
      t("ui.ehf.funn_dom_utlopt")));
    assert.ok(h.textContent.includes(t("ui.ehf.funn.lukk_hvorfor")));
  });

test("EHF: regelen kan leses uten å kjøres", () => {
  // Parameteren står i tabellen: en kodelisteregel viser listen, en
  // summeregel viser stien den summerer.
  const node = regeltabell(REGLER.regler);
  const rader = [...node.querySelectorAll("tbody tr")]
    .map((r) => r.textContent);
  assert.ok(rader[0].includes("NOK, EUR"), rader[0]);
  assert.ok(rader[1].includes("Invoice/Line/Amount"), rader[1]);
  assert.ok(rader[0].includes(t("ui.ehf.krav_i_kodeliste")));
});

test("EHF: et regelsett uten regler sies høyt", async () => {
  // En validering mot det ville sagt «null feil» om et dokument ingen
  // har sett på.
  SVAR = fullSvar();
  const h = nyHoved();
  visEhf(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  const sett = tabeller(h).find(
    (tb) => tb.querySelector("caption").textContent
      === t("ui.ehf.regelsett.tittel"));
  sett.querySelectorAll("tbody tr")[1].querySelector("button").click();
  await vent(() => h.textContent.includes(t("ui.ehf.regler.ingen")));
  const varsel = [...h.querySelectorAll('[role="alert"]')]
    .map((n) => n.textContent);
  assert.ok(varsel.includes(t("ui.ehf.regler.ingen")));
});

test("EHF: sluttdatoen kan settes, og tomt betyr gjelder fortsatt",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visEhf(h, ctx());
    await vent(() => tabeller(h).length >= 2);
    const sett = tabeller(h).find(
      (tb) => tb.querySelector("caption").textContent
        === t("ui.ehf.regelsett.tittel"));
    sett.querySelectorAll("tbody tr")[1].querySelector("button")
      .click();
    await vent(() => h.querySelector("#ef-g-til") !== null);
    assert.ok(h.textContent.includes(t("ui.ehf.sluttdato.hvorfor")));
    const til = h.querySelector("#ef-g-til");
    assert.equal(til.value, "", "et sett uten sluttdato fikk en");
    til.value = "2026-12-31";
    til.dispatchEvent(new window.Event("input"));
    til.form.dispatchEvent(new window.Event("submit",
      { cancelable: true }));
    await vent(() => SISTE !== undefined);
    assert.equal(SISTE.sti, `/v1/ehf/regelsett/${S2}/gyldig-til`);
    assert.equal(SISTE.kropp.gyldig_til, "2026-12-31");
  });

test("EHF: utløpet har retning", () => {
  assert.equal(utlopTekst(12), t("ui.ehf.utlop_om")
    .replace("{n}", "12"));
  assert.equal(utlopTekst(0), t("ui.ehf.utlop_i_dag"));
  assert.equal(utlopTekst(-5), t("ui.ehf.utlop_passert")
    .replace("{n}", "5"));
  assert.equal(utlopTekst(null), t("ui.ehf.uten_sluttdato"));
});

test("EHF: rettingen leses med begge verdiene", () => {
  const uten = rettingTekst({});
  assert.equal(uten, t("ui.ehf.uten_retting"));
  const utkast = rettingTekst({ retting_id: "r", fra_verdi: "USD",
                                til_verdi: "NOK",
                                klar_til_signering: false });
  assert.ok(utkast.includes("USD") && utkast.includes("NOK"));
  const klar = rettingTekst({ retting_id: "r", fra_verdi: null,
                              til_verdi: "REF-1",
                              klar_til_signering: true });
  // NULL FRA-VERDI BETYR AT FELTET SKAL LEGGES TIL.
  assert.ok(klar.includes(t("ui.ehf.feltet_fantes_ikke")), klar);
  assert.notEqual(utkast, klar);
});

test("EHF: de lukkede settene er 121s egne", () => {
  // KODEN, IKKE KOMMENTARENE — for fjerde gang i klynge 6/7.
  //
  // `CHECK (standard IN ('ubl', ...))` har en kommentar «(OASIS UBL
  // 2.1)» på samme linje, og en `[^)]*`-gruppe stopper på parentesen
  // INNE I KOMMENTAREN. Porten leste da bare det første elementet og
  // ville sagt fra om en feil som ikke fantes.
  const raa = readFileSync(new URL(
    "../../db/migrations/121_m54_ehf_avvik.sql", import.meta.url),
    "utf8");
  const sql = raa.split("\n")
    .map((l) => l.replace(/--.*$/, "")).join("\n");
  const les = (navn, kolonne) => {
    // `CHECK (` kan ha linjeskift etter seg — 121 bryter der linjen
    // ellers ble for lang. En regex uten `\\s*` her ville sagt at
    // CHECK-en manglet.
    const m = new RegExp(
      `${navn} CHECK \\(\\s*${kolonne} IN \\(([^)]*)\\)`).exec(sql);
    assert.ok(m, navn);
    return [...m[1].matchAll(/'([a-z_]+)'/g)].map((x) => x[1]);
  };
  assert.deepEqual(STANDARDER,
    les("ehfregelsett_standard_lukket", "standard"));
  assert.deepEqual(KRAVTYPER, les("ehfregel_krav_lukket", "krav"));
  assert.deepEqual(RETNINGER,
    les("ehfdokument_retning_lukket", "retning"));
  // ALVORLIGHETENE i regelen er de TO standarden har; avviket har en
  // tredje (`uten_grunnlag`) som ingen regel kan settes til.
  assert.deepEqual(ALVORLIGHETER,
    les("ehfregel_alvorlighet_lukket", "alvorlighet"));
});

// ---------------------------------------------------------------------
// LESERETT, TABELLER, SPRÅK OG AXE
// ---------------------------------------------------------------------

test("EHF: en lesende økt ser dommene, men ingen skriveveier",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visEhf(h, ctx(["okonomi:read"]));
    await vent(() => tabeller(h).length >= 2);
    assert.ok(h.textContent.includes("F-2026-1"));
    assert.equal(h.querySelector("form"), null,
      "en lesende økt fikk et skjema");
    await apneDokument(h);
    for (const n of ["valider", "lagre_retting", "merk_klar",
                     "lagre_regelsett", "lagre_regel",
                     "lagre_dokument", "sett_sluttdato"]) {
      const k = [...h.querySelectorAll("button")].find(
        (b) => b.textContent.startsWith(
          t(`ui.ehf.knapp.${n}`).split("{")[0]));
      assert.equal(k, undefined, `lesende økt fikk «${n}»`);
    }
    // …men DOMMEN og REGELSETTVERSJONEN er synlig.
    assert.ok(h.textContent.includes("3.0"));
    assert.ok(h.textContent.includes(t("ui.ehf.regelsett_utlopt")
      .replace("{navn}", "EHF 3.0")));
  });

test("EHF: hver tabell er en ekte tabell", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visEhf(h, ctx());
  await apneDokument(h);
  const t2 = tabeller(h);
  assert.ok(t2.length >= 4, `bare ${t2.length} tabeller`);
  for (const tab of t2) {
    assert.ok(tab.querySelector("caption"), "tabell uten caption");
    assert.ok(tab.querySelectorAll('th[scope="col"]').length > 0,
      "tabell uten kolonneoverskrifter");
    assert.ok(tab.closest(".tablewrap"),
      "tabell uten sidescroll-container");
    const rad = tab.querySelector("tbody tr");
    if (rad) {
      assert.ok(rad.querySelector('th[scope="row"]'),
        "rad uten th[scope=row]");
    }
  }
});

test("EHF: ingen hardkodet tekst", async () => {
  const PL = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visEhf(h, ctx());
    await vent(() => tabeller(h).length >= 2);
    // `th[scope="row"]` er UTELATT: den cellen bærer fakturanummeret,
    // regelkoden og standardens navn — tenantens og myndighetens
    // egne data.
    for (const node of h.querySelectorAll(
      'h2, h3, h4, label, caption, legend, th[scope="col"], button')) {
      const s = node.textContent.trim();
      if (!s) continue;
      assert.ok(s.startsWith("PL_"), `hardkodet tekst: «${s}»`);
    }
  } finally {
    settI18nForTest(NB, "nb");
  }
});

test("EHF: null alvorlige axe-brudd på oversikten", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visEhf(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("EHF: null alvorlige axe-brudd med dokumentpanelet åpent",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visEhf(h, ctx());
    await apneDokument(h);
    await vent(() => h.querySelector("#ef-v-sett") !== null);
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("EHF: null alvorlige axe-brudd på et tomt register", async () => {
  SVAR = { ...fullSvar(), "/v1/ehf": TOMT,
           "/v1/ehf/funn": { request_id: "r-h", funn: [] } };
  const h = nyHoved();
  visEhf(h, ctx());
  await vent(() => h.textContent.includes(
    t("ui.ehf.regelsett.ingen")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("EHF: tabellene står alene uten brudd", async () => {
  let brudd = await alvorligeBrudd(regeltabell(REGLER.regler),
                                   { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  brudd = await alvorligeBrudd(avvikstabell(AVVIK.avvik),
                               { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  brudd = await alvorligeBrudd(
    valideringstabell(VALIDERINGER.valideringer), { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  brudd = await alvorligeBrudd(
    dokumenttabell(BILDE.dokumenter, () => {}), { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});
