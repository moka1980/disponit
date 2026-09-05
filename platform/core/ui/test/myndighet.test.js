// M-47 myndighetsflaten (123) — flateporten (jsdom + axe).
//
// DENNE FLATEN ER ANNERLEDES ENN KLYNGE 6s, OG PORTENE MÅ VÆRE DET.
//
// Der målte portene FRAVÆR: ingen send-knapp, ingen mottaker, ingen
// signatur. Det gjør de her også — men det er bare halve dommen.
//
// HER ER SKADEN OGSÅ Å LA VÆRE. En frist som går uten innsending er
// nøyaktig det modulen ble bygget for å hindre, så portene måler også
// NÆRVÆRET: at det som har gått galt står FØRST, at fortegnet på
// fristen er der, og at flaten ikke tilbyr å lukke et avvik.
//
//   EN STILLE M-47 ER VERRE ENN INGEN M-47.
//
// Ingen delt fixture (m16-formen): hver test bygger sin egen skjerm.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import {
  FREKVENSER, MYNDIGHETER, fristTekst, ilokalDato, plikttabell,
  regelverkTekst, regelverkstabell, sammendrag, tilstandTekst,
  visMyndighet,
} from "../static/js/flater/myndighet.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));
const ROT = join(HER, "..", "..", "..", "..");

const R1 = "11111111-1111-1111-1111-111111111111";
const R2 = "22222222-2222-2222-2222-222222222222";
const T1 = "aaaaaaaa-1111-1111-1111-111111111111";
const P1 = "bbbbbbbb-1111-1111-1111-111111111111";
const P2 = "bbbbbbbb-2222-2222-2222-222222222222";
const P3 = "bbbbbbbb-3333-3333-3333-333333333333";
const B1 = "cccccccc-1111-1111-1111-111111111111";
const F1 = "dddddddd-1111-1111-1111-111111111111";
const F2 = "dddddddd-2222-2222-2222-222222222222";
const SHA = "a".repeat(64);

const REGELVERK = [
  { regelverk_id: R1, myndighet: "skatteetaten", navn: "MVA-melding",
    versjon: "2026-01", hjemmel: "skattebetalingsloven 8-1",
    gyldig_fra: "2026-01-01", gyldig_til: null, gyldig_naa: true,
    dogn_til_utlop: null, innhold_sha256: SHA, kilde_url: null,
    antall_plikter: 2 },
  { regelverk_id: R2, myndighet: "skatteetaten", navn: "Gammelt skjema",
    versjon: "2019", hjemmel: "gammel hjemmel",
    gyldig_fra: "2019-01-01", gyldig_til: "2020-12-31",
    gyldig_naa: false, dogn_til_utlop: -2000, innhold_sha256: SHA,
    kilde_url: null, antall_plikter: 1 },
];

// EN PASSERT FRIST, en som nærmer seg, og en som er sendt inn.
const PLIKTER = [
  { plikt_id: P1, plikttype_id: T1, typenavn: "MVA-melding",
    typenokkel: "mva_melding", periode_fra: "2026-05-01",
    periode_til: "2026-06-30", frist: "2026-08-31",
    dogn_til_frist: -10, myndighet: "skatteetaten",
    regelnavn: "MVA-melding", regelversjon: "2026-01",
    hjemmel: "skattebetalingsloven 8-1", regelverk_gyldig_naa: true,
    bevis_id: null, innsendt_dato: null, kvittering_ref: null,
    innsendt_av_person: null, dogn_etter_frist: null,
    kravversjon: 1, registrert: "2026-07-01T09:00:00+00:00",
    registrert_av: "u-1" },
  { plikt_id: P2, plikttype_id: T1, typenavn: "MVA-melding",
    typenokkel: "mva_melding", periode_fra: "2026-07-01",
    periode_til: "2026-08-31", frist: "2026-09-11",
    dogn_til_frist: 7, myndighet: "skatteetaten",
    regelnavn: "Gammelt skjema", regelversjon: "2019",
    hjemmel: "gammel hjemmel", regelverk_gyldig_naa: false,
    bevis_id: null, innsendt_dato: null, kvittering_ref: null,
    innsendt_av_person: null, dogn_etter_frist: null,
    kravversjon: 1, registrert: "2026-08-01T09:00:00+00:00",
    registrert_av: "u-1" },
  { plikt_id: P3, plikttype_id: T1, typenavn: "MVA-melding",
    typenokkel: "mva_melding", periode_fra: "2026-03-01",
    periode_til: "2026-04-30", frist: "2026-06-10",
    dogn_til_frist: -90, myndighet: "skatteetaten",
    regelnavn: "MVA-melding", regelversjon: "2026-01",
    hjemmel: "skattebetalingsloven 8-1", regelverk_gyldig_naa: true,
    bevis_id: B1, innsendt_dato: "2026-06-14",
    kvittering_ref: "AR-99881", innsendt_av_person: "Kari Nordmann",
    dogn_etter_frist: 4, kravversjon: 1,
    registrert: "2026-05-01T09:00:00+00:00", registrert_av: "u-1" },
];

const BILDE = {
  request_id: "r-b",
  sammendrag: {
    plikter: 3, beviste: 1, ubeviste: 2, frist_passert: 1,
    frist_naer: 1, regelverk: 2, gyldige: 1, utlopte: 1,
    apne_funn: 2, har_krav: true, varselfrist_dogn: 14,
    kravversjon: 1, vist: 3,
  },
  krav: { varselfrist_dogn: 14, eskaleringsfrist_dogn: 3,
          regelvarsel_dogn: 60, versjon: 1 },
  regelverk: REGELVERK,
  plikttyper: [{ plikttype_id: T1, nokkel: "mva_melding",
                 navn: "MVA-melding", frekvens: "to_maanedlig",
                 beskrivelse: null, antall_plikter: 3 }],
  plikter: PLIKTER,
};

const TOMT = {
  request_id: "r-t",
  sammendrag: { plikter: 0, beviste: 0, ubeviste: 0, frist_passert: 0,
                frist_naer: 0, regelverk: 0, gyldige: 0, utlopte: 0,
                apne_funn: 0, har_krav: false, varselfrist_dogn: null,
                kravversjon: null, vist: 0 },
  krav: null, regelverk: [], plikttyper: [], plikter: [],
};

const FUNN = {
  request_id: "r-f",
  funn: [
    { funn_id: F1, funntype: "frist_passert_uten_bevis",
      regelverk_id: null, plikt_id: P1, myndighet: "skatteetaten",
      regelnavn: "MVA-melding", regelversjon: "2026-01",
      typenavn: "MVA-melding", frist: "2026-08-31", over_grense: 10,
      detalj: "MVA-melding 2026-01", kravversjon: 1,
      // SVEIPENS EGET — ingen kan lukke det for hånd.
      kan_lukkes: false, forst_sett: "2026-09-01T09:00:00+00:00",
      sist_sett_sveip: "2026-09-05T09:00:00+00:00", apen: true,
      lukket_ts: null, lukket_av: null, lukkenotat: null },
    { funn_id: F2, funntype: "frist_naermer_seg",
      regelverk_id: null, plikt_id: P2, myndighet: "skatteetaten",
      regelnavn: "Gammelt skjema", regelversjon: "2019",
      typenavn: "MVA-melding", frist: "2026-09-11", over_grense: 7,
      detalj: null, kravversjon: 1,
      // EN PÅMINNELSE — den kan lukkes.
      kan_lukkes: true, forst_sett: "2026-09-04T09:00:00+00:00",
      sist_sett_sveip: "2026-09-05T09:00:00+00:00", apen: true,
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
        json: async () => ({ feil: "myndighet_ulovlig_tilstand" }) };
    }
    return { ok: true, status: 200,
             json: async () => ({ ok: true, dogn_etter_frist: 0 }) };
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

const fullSvar = () => ({ "/v1/myndighet": BILDE,
                          "/v1/myndighet/funn": FUNN });

const tabeller = (h) => [...h.querySelectorAll("table")];
const tabell = (h, nokkel) => tabeller(h).find(
  (tb) => tb.querySelector("caption").textContent === t(nokkel));

// ---------------------------------------------------------------------
// DET SOM HAR GÅTT GALT STÅR FØRST
// ---------------------------------------------------------------------

test("myndighet: sammendraget begynner med de passerte fristene", () => {
  // MODULENS DOM, SETT FRA FLATEN. Et sammendrag som begynte med «3
  // plikter registrert» ville fortalt hvor flittige vi har vært, ikke
  // hva som er galt — og en frist som går i stillhet er hele skaden.
  //
  // MUTASJONEN SOM DREPER DENNE: flytt `frist_passert` bakerst.
  const p = sammendrag(BILDE.sammendrag);
  const forste = p.querySelector("strong");
  assert.equal(forste.textContent,
    t("ui.myndighet.passert_sum").replace("{n}", "1"));
  // …OG DEN STÅR I FET SKRIFT, ikke som en av flere like tellinger.
  assert.equal(forste.tagName, "STRONG");
});

test("myndighet: uten varselfrist sier flaten at ingenting overvåkes",
     () => {
  // Uten tenantens frist finnes det ingen frist å varsle på. Da er
  // registeret uovervåket, og det er verre enn tomt — det SER
  // overvåket ut.
  const p = sammendrag(TOMT.sammendrag);
  const varsler = [...p.querySelectorAll("[role='alert']")]
    .map((e) => e.textContent);
  assert.ok(varsler.includes(t("ui.myndighet.ingen_varselfrist")));
});

test("myndighet: fristen har retning", () => {
  // Fortegnet er hele beskjeden: en frist om sju døgn og en som gikk
  // for sju døgn siden er to helt forskjellige tilstander.
  assert.equal(fristTekst(null), t("ui.myndighet.uten_frist"));
  assert.equal(fristTekst(0), t("ui.myndighet.frist_i_dag"));
  assert.ok(fristTekst(7).includes("7"));
  assert.ok(fristTekst(-7).includes("7"));
  assert.notEqual(fristTekst(7), fristTekst(-7),
    "en passert frist ser ut som en kommende");
});

test("myndighet: pliktens tilstand navngir det som mangler", () => {
  // REKKEFØLGEN ER EN DOM. «Sendt inn» sjekkes FØRST: en plikt som ER
  // sendt inn er ferdig, uansett hva regelverket har gjort siden.
  assert.equal(tilstandTekst(PLIKTER[2]),
    t("ui.myndighet.sendt_for_sent").replace("{n}", "4"));
  assert.equal(tilstandTekst(PLIKTER[0]),
    t("ui.myndighet.ikke_sendt_passert"));
  assert.equal(tilstandTekst({ bevis_id: null, dogn_til_frist: 7,
    regelverk_gyldig_naa: false }), t("ui.myndighet.hjemmel_utlopt"));
  assert.equal(tilstandTekst({ bevis_id: null, dogn_til_frist: 7,
    regelverk_gyldig_naa: true }), t("ui.myndighet.venter"));
  // …og et bevis levert I TIDE er ikke «for sent».
  assert.equal(tilstandTekst({ bevis_id: B1, dogn_etter_frist: -2 }),
    t("ui.myndighet.sendt"));
});

// ---------------------------------------------------------------------
// REGELVERKET BÆRER OM DET GJELDER
// ---------------------------------------------------------------------

test("myndighet: regelversjonen vises aldri uten om den gjelder", () => {
  // EN FORELDET REGEL SER NØYAKTIG UT SOM EN RIKTIG REGEL. Versjonen
  // alene er nettopp opplysningen som gjør dem umulige å skille.
  const gyldig = regelverkTekst(REGELVERK[0]);
  const utlopt = regelverkTekst(REGELVERK[1]);
  assert.notEqual(gyldig, utlopt);
  assert.ok(utlopt.includes("2019"));
  assert.equal(utlopt, t("ui.myndighet.regelverk_utlopt")
    .replace("{navn}", "Gammelt skjema 2019"));
});

test("myndighet: hjemmelen er en kolonne, ikke en detalj", () => {
  // En frist uten hjemmel er en påstand om at noen må gjøre noe, uten
  // å si hvem som har bestemt det.
  const tb = regelverkstabell(REGELVERK, null);
  assert.ok(tb.textContent.includes("skattebetalingsloven 8-1"));
  const kol = [...tb.querySelectorAll("thead th")]
    .map((e) => e.textContent);
  assert.ok(kol.includes(t("ui.myndighet.kol.hjemmel")));
  const pt = plikttabell(PLIKTER, null);
  assert.ok(pt.textContent.includes("skattebetalingsloven 8-1"));
  assert.ok([...pt.querySelectorAll("thead th")]
    .map((e) => e.textContent).includes(t("ui.myndighet.kol.hjemmel")));
});

// ---------------------------------------------------------------------
// FRAVÆRET — men bare halve dommen
// ---------------------------------------------------------------------

test("myndighet: ingen kontroll sender inn", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visMyndighet(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  for (const knapp of h.querySelectorAll("button")) {
    const tekst = knapp.textContent.toLowerCase();
    for (const forbudt of ["send inn", "signer", "innsend",
                           "overfør"]) {
      assert.equal(tekst.includes(forbudt), false,
                   `knappen «${knapp.textContent}» ${forbudt}`);
    }
  }
  assert.equal(KALL.some((k) => k.sti.includes("send")), false);
  assert.equal(KALL.some((k) => k.sti.includes("signer")), false);
});

test("myndighet: «registrer bevis» sier at systemet ikke sendte noe",
     async () => {
  // DETTE ER IKKE «SEND INN» MED ET ANNET NAVN, og den som fyller ut
  // skjemaet skal ikke kunne tro at systemet sender noe.
  const h = nyHoved(); SVAR = fullSvar();
  visMyndighet(h, ctx());
  await vent(() => tabell(h, "ui.myndighet.plikter.tittel"));
  const rad = [...tabell(h, "ui.myndighet.plikter.tittel")
    .querySelectorAll("tbody tr")][0];
  rad.querySelector("button").click();
  await vent(() => h.querySelector("#my-b-kvittering"));
  const hjelp = [...h.querySelectorAll("p")].map((e) => e.textContent);
  assert.ok(hjelp.includes(t("ui.myndighet.bevis.hvorfor")));
  // KVITTERINGEN ER PÅKREVD. Uten den er beviset en påstand.
  assert.equal(h.querySelector("#my-b-kvittering").required, true);
  assert.equal(h.querySelector("#my-b-person").required, true);
  // DATOEN KAN IKKE VÆRE I FRAMTIDEN.
  assert.ok(h.querySelector("#my-b-dato").max);
});

test("myndighet: beviset sendes med kvittering og person", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visMyndighet(h, ctx());
  await vent(() => tabell(h, "ui.myndighet.plikter.tittel"));
  const rad = [...tabell(h, "ui.myndighet.plikter.tittel")
    .querySelectorAll("tbody tr")][0];
  rad.querySelector("button").click();
  await vent(() => h.querySelector("#my-b-kvittering"));
  h.querySelector("#my-b-dato").value = "2026-09-01";
  h.querySelector("#my-b-kvittering").value = "AR-12345";
  h.querySelector("#my-b-person").value = "Ola Nordmann";
  const skjema = h.querySelector("#my-b-kvittering").closest("form");
  skjema.dispatchEvent(new Event("submit", { bubbles: true,
                                             cancelable: true }));
  await vent(() => SISTE && SISTE.sti.endsWith("/bevis"));
  assert.equal(SISTE.kropp.kvittering_ref, "AR-12345");
  assert.equal(SISTE.kropp.innsendt_av_person, "Ola Nordmann");
  assert.ok(SISTE.headers["Idempotency-Key"]);
});

// ---------------------------------------------------------------------
// FUNNENE: FLATEN AVGJØR IKKE HVA SOM KAN LUKKES
// ---------------------------------------------------------------------

test("myndighet: avviket kan ikke lukkes, påminnelsen kan", async () => {
  // `kan_lukkes` KOMMER FRA BASEN. En kopi av regelen her ville vært
  // en andre regel som kunne komme i utakt — og da hadde flaten
  // tilbudt en knapp som alltid feiler.
  //
  // MUTASJONEN SOM DREPER DENNE: filtrer på funntype i stedet.
  const h = nyHoved(); SVAR = fullSvar();
  visMyndighet(h, ctx());
  await vent(() => h.querySelector("#my-l-valg"));
  const valg = [...h.querySelector("#my-l-valg").options]
    .map((o) => o.textContent);
  assert.equal(valg.some((v) =>
    v.startsWith(t("ui.myndighet.funn_frist_passert"))), false,
  "et avvik kunne lukkes for hånd");
  assert.ok(valg.some((v) =>
    v.startsWith(t("ui.myndighet.funn_frist_naer"))),
  "påminnelsen forsvant sammen med avviket");
});

test("myndighet: hver funnrad sier om den kan lukkes", async () => {
  // Uten dette ville en manglende knapp sett ut som en feil.
  const h = nyHoved(); SVAR = fullSvar();
  visMyndighet(h, ctx());
  await vent(() => tabell(h, "ui.myndighet.funn.tittel"));
  const rader = [...tabell(h, "ui.myndighet.funn.tittel")
    .querySelectorAll("tbody tr")];
  const avvik = rader.find((r) => r.querySelector("th").textContent
    === t("ui.myndighet.funn_frist_passert"));
  assert.ok(avvik.textContent.includes(t("ui.myndighet.funn.sveipens")));
  const paaminnelse = rader.find((r) =>
    r.querySelector("th").textContent
      === t("ui.myndighet.funn_frist_naer"));
  assert.ok(paaminnelse.textContent
    .includes(t("ui.myndighet.funn.kan_lukkes")));
});

// ---------------------------------------------------------------------
// SKJEMAENE
// ---------------------------------------------------------------------

test("myndighet: pliktskjemaet tilbyr bare regelverk som gjelder",
     async () => {
  // Døra nekter mot et avviklet regelverk, og en knapp som alltid
  // feiler er verre enn en valgmulighet som ikke finnes. Arkivet står
  // fortsatt i tabellen — det er BRUKEN som er stengt, ikke minnet.
  const h = nyHoved(); SVAR = fullSvar();
  visMyndighet(h, ctx());
  await vent(() => h.querySelector("#my-p-regel"));
  const verdier = [...h.querySelector("#my-p-regel").options]
    .map((o) => o.value).filter(Boolean);
  assert.deepEqual(verdier, [R1], "et avviklet regelverk kunne velges");
  // …men det avviklede STÅR i registeret.
  assert.ok(tabell(h, "ui.myndighet.regelverk.tittel")
    .textContent.includes("Gammelt skjema"));
});

test("myndighet: uten et gyldig regelverk finnes ingen pliktknapp",
     async () => {
  const h = nyHoved();
  SVAR = { ...fullSvar(),
    "/v1/myndighet": { ...BILDE, regelverk: [REGELVERK[1]],
      sammendrag: { ...BILDE.sammendrag, gyldige: 0 } } };
  visMyndighet(h, ctx());
  // `vent` gir SANN/USANN tilbake. Uten denne asserten ville testen
  // bestått også om varselet aldri kom — og da hadde den bare målt at
  // knappen manglet på en skjerm som ikke var ferdig tegnet.
  assert.ok(await vent(() => [...h.querySelectorAll("[role='alert']")]
    .some((e) => e.textContent
      === t("ui.myndighet.plikt.ingen_gyldige"))),
  "varselet om manglende regelverk kom aldri");
  assert.equal([...h.querySelectorAll("button")].some(
    (b) => b.textContent === t("ui.myndighet.knapp.registrer_plikt")),
  false, "en knapp som alltid ville feilet sto der");
});

test("myndighet: varselfristen kommer fra svaret, aldri fra flaten",
     async () => {
  const h = nyHoved();
  SVAR = { ...fullSvar(),
    "/v1/myndighet": { ...BILDE,
      krav: { ...BILDE.krav, varselfrist_dogn: 21 },
      sammendrag: { ...BILDE.sammendrag, varselfrist_dogn: 21 } } };
  visMyndighet(h, ctx());
  await vent(() => h.querySelector("#my-k-varsel"));
  assert.equal(h.querySelector("#my-k-varsel").value, "21");
  // …og uten krav står feltet TOMT, ikke på en oppdiktet standard.
  const h2 = nyHoved();
  SVAR = { "/v1/myndighet": TOMT,
           "/v1/myndighet/funn": { request_id: "r-0", funn: [] } };
  visMyndighet(h2, ctx());
  await vent(() => h2.querySelector("#my-k-varsel"));
  assert.equal(h2.querySelector("#my-k-varsel").value, "");
});

test("myndighet: sluttdatoen kan settes, og tomt betyr gjelder ennå",
     async () => {
  // 121s lærdom: nøkkelen sendes ALLTID, også når verdien er tom.
  // Utelatt felt og eksplisitt null er to forskjellige ting.
  const h = nyHoved(); SVAR = fullSvar();
  visMyndighet(h, ctx());
  await vent(() => tabell(h, "ui.myndighet.regelverk.tittel"));
  const rad = [...tabell(h, "ui.myndighet.regelverk.tittel")
    .querySelectorAll("tbody tr")][0];
  rad.querySelector("button").click();
  await vent(() => h.querySelector("#my-s-til"));
  h.querySelector("#my-s-til").value = "";
  h.querySelector("#my-s-til").closest("form")
    .dispatchEvent(new Event("submit", { bubbles: true,
                                         cancelable: true }));
  await vent(() => SISTE && SISTE.sti.endsWith("/gyldig-til"));
  assert.ok("gyldig_til" in SISTE.kropp, "nøkkelen ble utelatt");
  assert.equal(SISTE.kropp.gyldig_til, null);
});

test("myndighet: en avvist handling sier hvorfor", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visMyndighet(h, ctx());
  await vent(() => h.querySelector("#my-k-varsel"));
  SVARSTATUS = 409;
  h.querySelector("#my-k-varsel").closest("form")
    .dispatchEvent(new Event("submit", { bubbles: true,
                                         cancelable: true }));
  await vent(() => [...h.querySelectorAll("[role='alert']")].some(
    (e) => e.textContent === t("ui.myndighet.feil.tilstand")));
  assert.ok([...h.querySelectorAll("[role='alert']")].some(
    (e) => e.textContent === t("ui.myndighet.feil.tilstand")));
});

// ---------------------------------------------------------------------
// SCOPE, TABELLER, TEKST OG AXE
// ---------------------------------------------------------------------

test("myndighet: en lesende økt ser fristene, men ingen skriveveier",
     async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visMyndighet(h, ctx(["okonomi:read"]));
  await vent(() => tabell(h, "ui.myndighet.plikter.tittel"));
  assert.equal(h.querySelectorAll("form").length, 0,
               "et skjema sto åpent uten skriverett");
  assert.equal(KALL.some((k) => k.metode === "POST"), false);
  // …men fristene STÅR der. Å skjule dem for en leser ville vært å
  // gjøre flaten stille for nettopp den som bare skal se etter.
  assert.ok(tabell(h, "ui.myndighet.plikter.tittel")
    .textContent.includes("2026-08-31"));
});

test("myndighet: hver tabell er en ekte tabell", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visMyndighet(h, ctx());
  await vent(() => tabeller(h).length >= 3);
  for (const tb of tabeller(h)) {
    assert.ok(tb.querySelector("caption"), "tabell uten caption");
    assert.ok(tb.querySelectorAll("thead th[scope='col']").length > 0);
    for (const rad of tb.querySelectorAll("tbody tr")) {
      assert.ok(rad.querySelector("th[scope='row']"),
                "rad uten radoverskrift");
    }
  }
});

test("myndighet: ingen hardkodet tekst", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visMyndighet(h, ctx());
  await vent(() => tabeller(h).length >= 3);
  assert.equal(h.textContent.includes("ui.myndighet."), false,
               "en manglende oversettelse lakk nøkkelen ut");
  assert.equal(h.textContent.includes("{"), false,
               "en malplassholder overlevde til skjermen");
});

test("myndighet: null alvorlige axe-brudd på oversikten", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visMyndighet(h, ctx());
  await vent(() => tabeller(h).length >= 3);
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("myndighet: null alvorlige axe-brudd med bevisskjemaet åpent",
     async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visMyndighet(h, ctx());
  await vent(() => tabell(h, "ui.myndighet.plikter.tittel"));
  [...tabell(h, "ui.myndighet.plikter.tittel")
    .querySelectorAll("tbody tr")][0].querySelector("button").click();
  await vent(() => h.querySelector("#my-b-kvittering"));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("myndighet: null alvorlige axe-brudd på et tomt register",
     async () => {
  const h = nyHoved();
  SVAR = { "/v1/myndighet": TOMT,
           "/v1/myndighet/funn": { request_id: "r-0", funn: [] } };
  visMyndighet(h, ctx());
  await vent(() => [...h.querySelectorAll("[role='alert']")].some(
    (e) => e.textContent === t("ui.myndighet.regelverk.ingen")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("myndighet: tabellene står alene uten brudd", async () => {
  for (const node of [regelverkstabell(REGELVERK, () => {}),
                      plikttabell(PLIKTER, () => {})]) {
    const brudd = await alvorligeBrudd(node, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  }
});

test("myndighet: listene er de samme som API-ets", () => {
  assert.deepEqual([...MYNDIGHETER], ["skatteetaten", "altinn",
    "brreg", "ssb", "nav", "arbeidstilsynet", "annen"]);
  assert.deepEqual([...FREKVENSER], ["maanedlig", "to_maanedlig",
    "kvartalsvis", "halvaarlig", "aarlig", "ved_hendelse"]);
  const api = readFileSync(join(ROT, "platform", "core", "api",
    "myndighetsrapport.py"), "utf-8");
  for (const m of MYNDIGHETER) assert.ok(api.includes(`"${m}"`), m);
  for (const f of FREKVENSER) assert.ok(api.includes(`"${f}"`), f);
});

test("myndighet: bevisdatoen er brukerens døgn, ikke UTC", () => {
  // CodeRabbit. `toISOString()` gir UTC-datoen, og klokka halv ett på
  // natta norsk tid er den fortsatt I GÅR — feltet ville nektet dagens
  // lovlige innsending. Det er nettopp brukstilfellet modulen handler
  // om: noen som sender inn sent.
  //
  // MUTASJONEN SOM DREPER DENNE: bruk `toISOString().slice(0, 10)`.
  const midnatt = new Date(2026, 8, 5, 0, 30, 0);
  assert.equal(ilokalDato(midnatt), "2026-09-05");
  // …og den polstrer ett-sifrede måneder og dager.
  assert.equal(ilokalDato(new Date(2026, 0, 3, 12)), "2026-01-03");
  const kilde = readFileSync(join(ROT, "platform", "core", "ui",
    "static", "js", "flater", "myndighet.js"), "utf-8");
  assert.equal(/toISOString\(\)\.slice/.test(kilde), false,
    "flaten leser UTC-datoen igjen");
});
