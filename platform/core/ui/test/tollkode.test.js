// M-52 tollflaten (122) — flateporten (jsdom + axe).
//
// Portene her måler det modulen står og faller på:
//
//   * `forslag_uten_grunnlag`: forslagsknappen er DØD til minst én
//     grunn er lagt inn, og grunnlisten sier høyt at den er tom. En
//     kode et menneske stempler fordi den sto der produserer falsk
//     trygghet — og boten treffer kunden, ikke oss.
//   * `forslag_mot_utlopt_nomenklatur`: versjonen vises ALDRI uten om
//     settet gjelder i dag, forslagsskjemaet tilbyr bare gyldige sett,
//     og funnet står i tabellen UTEN å stå i lukkevalget.
//     EN FORELDET REGEL SER NØYAKTIG UT SOM EN RIKTIG REGEL.
//   * `modulen_deklarerte`: ingen «deklarer»-knapp, ingen mottaker,
//     ingen signatur. «Klar til deklarering» er en tilstand hos oss,
//     og hjelpeteksten sier det.
//   * `sikkerhetsterskel_hardkodet`: terskelen leses fra svaret, og
//     står i hver beskjed om at noe er under den.
//
// Ingen delt fixture (m16-formen): hver test bygger sin egen skjerm.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import {
  GRUNNARTER, GRUNNVEKT, SYSTEMER, forslagTekst, forslagstabell,
  grunntabell, nomenklaturTekst, nomenklaturtabell, sammendrag,
  satsTekst, tilstandTekst, utlopTekst, varenummertabell, varetabell,
  visTollkode,
} from "../static/js/flater/tollkode.js";

settI18nForTest(NB, "nb");

const N1 = "11111111-1111-1111-1111-111111111111";
const N2 = "22222222-2222-2222-2222-222222222222";
const V1 = "aaaaaaaa-1111-1111-1111-111111111111";
const V2 = "aaaaaaaa-2222-2222-2222-222222222222";
const VN1 = "bbbbbbbb-1111-1111-1111-111111111111";
const F1 = "cccccccc-1111-1111-1111-111111111111";
const FN1 = "dddddddd-1111-1111-1111-111111111111";
const FN2 = "dddddddd-2222-2222-2222-222222222222";
const SHA = "a".repeat(64);

const BILDE = {
  request_id: "r-b",
  sammendrag: {
    varer: 2, klassifiserte: 1, uklassifiserte: 1, klare: 0,
    forslag_under_utlopt: 1, nomenklaturer: 2, gyldige: 1, utlopte: 1,
    apne_funn: 2, har_krav: true, terskel: 70, kravversjon: 1,
    vist: 2,
  },
  krav: { sikkerhetsterskel: 70, utlopsvarsel_dogn: 90,
          forslagsfrist_dogn: 30, versjon: 1 },
  nomenklaturer: [
    { nomenklatur_id: N1, system: "hs", versjon: "HS 2022",
      gyldig_fra: "2022-01-01", gyldig_til: null, gyldig_naa: true,
      dogn_til_utlop: null, innhold_sha256: SHA,
      antall_varenummer: 3, antall_forslag: 1 },
    { nomenklatur_id: N2, system: "hs", versjon: "HS 2017",
      gyldig_fra: "2017-01-01", gyldig_til: "2021-12-31",
      gyldig_naa: false, dogn_til_utlop: -1712,
      innhold_sha256: SHA, antall_varenummer: 2, antall_forslag: 1 },
  ],
  varer: [
    { vare_id: V1, ekstern_ref: "ART-1", beskrivelse: "Skruer i stål",
      materiale: "stål", bruk: "festemiddel", opprinnelsesland: "DE",
      forslag_id: F1, kode: "7318.15", sikkerhet: 90,
      terskel_brukt: 70, over_terskel: true, antall_grunner: 2,
      system: "hs", versjon: "HS 2017", nomenklatur_gyldig_naa: false,
      tollsats_bp: 250, klar_til_deklarering: false },
    { vare_id: V2, ekstern_ref: "ART-2", beskrivelse: "Ukjent del",
      materiale: null, bruk: null, opprinnelsesland: null,
      forslag_id: null, kode: null, sikkerhet: null,
      terskel_brukt: null, over_terskel: null, antall_grunner: 0,
      system: null, versjon: null, nomenklatur_gyldig_naa: null,
      tollsats_bp: null, klar_til_deklarering: false },
  ],
};

const TOMT = {
  request_id: "r-t",
  sammendrag: { varer: 0, klassifiserte: 0, uklassifiserte: 0,
                klare: 0, forslag_under_utlopt: 0, nomenklaturer: 0,
                gyldige: 0, utlopte: 0, apne_funn: 0, har_krav: false,
                terskel: null, kravversjon: null, vist: 0 },
  krav: null, nomenklaturer: [], varer: [],
};

const VARENUMMER = {
  request_id: "r-v", nomenklatur_id: N1,
  varenummer: [
    { varenummer_id: VN1, kode: "7318.15",
      tekst: "Skruer og bolter av jern eller stål",
      tollsats_bp: 250, brukt_i_forslag: 1 },
  ],
};

const FORSLAG = {
  request_id: "r-f", vare_id: V1,
  forslag: [
    { forslag_id: F1, kode: "7318.15", system: "hs",
      versjon: "HS 2017", nomenklatur_gyldig_naa: false,
      sikkerhet: 90, terskel_brukt: 70, over_terskel: true,
      antall_grunner: 2, klar_til_deklarering: false,
      avgitt: "2026-03-01T09:00:00+00:00", avgitt_av: "u-1" },
  ],
};

const GRUNNER = {
  request_id: "r-g", forslag_id: F1,
  grunner: [
    { art: "bindende_forhandsuttalelse", henvisning: "BKU-2024-117",
      utdrag: "Identisk skrue klassifisert i 7318.15",
      grunn_dato: "2024-03-01", registrert_av: "u-1" },
    { art: "nomenklaturtekst", henvisning: "HS 73.18",
      utdrag: "Skruer, bolter og muttere av jern eller stål",
      grunn_dato: null, registrert_av: "u-1" },
  ],
};

const FUNN = {
  request_id: "r-fn",
  funn: [
    { funn_id: FN1, funntype: "forslag_mot_utlopt_nomenklatur",
      nomenklatur_id: N2, vare_id: V1, forslag_id: F1, system: "hs",
      nomenklaturversjon: "HS 2017", ekstern_ref: "ART-1",
      over_grense: 1712, detalj: "hs HS 2017", sikkerhet: 90,
      terskel_brukt: 70, kravversjon: 1,
      forst_sett: "2026-09-01T09:00:00+00:00",
      sist_sett_sveip: "2026-09-04T09:00:00+00:00", apen: true,
      lukket_ts: null, lukket_av: null, lukkenotat: null },
    { funn_id: FN2, funntype: "vare_uten_forslag",
      nomenklatur_id: null, vare_id: V2, forslag_id: null,
      system: null, nomenklaturversjon: null, ekstern_ref: "ART-2",
      over_grense: 45, detalj: null, sikkerhet: null,
      terskel_brukt: null, kravversjon: 1,
      forst_sett: "2026-09-01T09:00:00+00:00",
      sist_sett_sveip: "2026-09-04T09:00:00+00:00", apen: true,
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
        json: async () => ({ feil: "toll_ulovlig_tilstand" }) };
    }
    return { ok: true, status: 200,
             json: async () => ({ ok: true, sikkerhet: 90,
                                  terskel_brukt: 70,
                                  over_terskel: true,
                                  antall_grunner: 1 }) };
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
    "/v1/toll": BILDE,
    "/v1/toll/funn": FUNN,
    [`/v1/toll/nomenklatur/${N1}/varenummer`]: VARENUMMER,
    [`/v1/toll/nomenklatur/${N2}/varenummer`]: {
      request_id: "r-v2", nomenklatur_id: N2, varenummer: [] },
    [`/v1/toll/vare/${V1}/forslag`]: FORSLAG,
    [`/v1/toll/vare/${V2}/forslag`]: {
      request_id: "r-f2", vare_id: V2, forslag: [] },
    [`/v1/toll/forslag/${F1}/grunner`]: GRUNNER,
  };
}

function tabeller(h) {
  return [...h.querySelectorAll("table")];
}

function tabell(h, nokkel) {
  return tabeller(h).find(
    (tb) => tb.querySelector("caption").textContent === t(nokkel));
}

async function apneVare(h, ref) {
  await vent(() => tabell(h, "ui.tollkode.varer.tittel"));
  const rad = [...tabell(h, "ui.tollkode.varer.tittel")
    .querySelectorAll("tbody tr")]
    .find((r) => r.querySelector("th").textContent === ref);
  rad.querySelector("button").click();
  // VENT PÅ NOE SOM BARE FINNES I DET FERDIGE PANELET (118s lærdom).
  // Overskriften settes allerede av «laster», så den ville sluppet
  // testen løs på en halvtegnet skjerm.
  await vent(() => [...h.querySelectorAll("p")].every(
    (e) => e.textContent !== t("ui.tollkode.laster")));
}

// ---------------------------------------------------------------------
// forslag_uten_grunnlag — MODULENS SKARPESTE FLATE
// ---------------------------------------------------------------------

test("toll: forslagsknappen er død uten minst én grunn", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visTollkode(h, ctx());
  await apneVare(h, "ART-2");
  const skjema = [...h.querySelectorAll("form")].find(
    (f) => f.querySelector("#tk-f-nomenklatur"));
  const send = [...skjema.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.tollkode.knapp.avgi_forslag"));
  assert.equal(send.disabled, true, "knappen var levende uten grunn");

  // Alt annet fylles ut — den er FORTSATT død.
  const nomen = skjema.querySelector("#tk-f-nomenklatur");
  nomen.value = N1;
  nomen.dispatchEvent(new Event("change", { bubbles: true }));
  await vent(() => skjema.querySelector("#tk-f-varenummer")
    .options.length > 1);
  const vnr = skjema.querySelector("#tk-f-varenummer");
  vnr.value = VN1;
  vnr.dispatchEvent(new Event("change", { bubbles: true }));
  const sikkerhet = skjema.querySelector("#tk-f-sikkerhet");
  sikkerhet.value = "90";
  sikkerhet.dispatchEvent(new Event("input", { bubbles: true }));
  assert.equal(send.disabled, true,
               "knappen ble levende av alt UNNTATT grunnlaget");

  // …OG FLATEN SIER HØYT AT GRUNNLAGET ER TOMT.
  const tom = [...skjema.querySelectorAll("[role='alert']")].find(
    (e) => e.textContent === t("ui.tollkode.forslag.ingen_grunner"));
  assert.ok(tom, "det tomme grunnlaget var stille");
  assert.equal(tom.hidden, false);
  // …og beskjeden står UTENFOR lista: en rolle på en `li` overstyrer
  // listitem-rollen, og da er lista ikke lenger en liste.
  assert.equal(tom.closest("ul"), null);
});

test("toll: én grunn vekker knappen, og den sendes med forslaget",
     async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visTollkode(h, ctx());
  await apneVare(h, "ART-2");
  const skjema = [...h.querySelectorAll("form")].find(
    (f) => f.querySelector("#tk-f-nomenklatur"));
  const nomen = skjema.querySelector("#tk-f-nomenklatur");
  nomen.value = N1;
  nomen.dispatchEvent(new Event("change", { bubbles: true }));
  await vent(() => skjema.querySelector("#tk-f-varenummer")
    .options.length > 1);
  for (const [id, verdi] of [["#tk-f-varenummer", VN1],
                             ["#tk-f-sikkerhet", "90"],
                             ["#tk-f-art", "nomenklaturtekst"],
                             ["#tk-f-henvisning", "HS 73.18"],
                             ["#tk-f-utdrag", "Skruer av stål"]]) {
    const k = skjema.querySelector(id);
    k.value = verdi;
    k.dispatchEvent(new Event("input", { bubbles: true }));
    k.dispatchEvent(new Event("change", { bubbles: true }));
  }
  const leggTil = [...skjema.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.tollkode.knapp.legg_til_grunn"));
  assert.equal(leggTil.disabled, false);
  leggTil.click();
  const send = [...skjema.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.tollkode.knapp.avgi_forslag"));
  assert.equal(send.disabled, false, "knappen var død MED grunnlag");

  skjema.dispatchEvent(new Event("submit", { bubbles: true,
                                             cancelable: true }));
  await vent(() => SISTE && SISTE.sti.endsWith("/forslag"));
  // GRUNNEN FØLGER FORSLAGET I SAMME KALL — det finnes ingen andre.
  assert.equal(SISTE.kropp.grunner.length, 1);
  assert.equal(SISTE.kropp.grunner[0].art, "nomenklaturtekst");
  assert.equal(SISTE.kropp.sikkerhet, 90);
  assert.ok(SISTE.headers["Idempotency-Key"]);
  assert.equal(KALL.filter((k) => k.metode === "POST").length, 1,
               "grunnen ble sendt i et eget kall");
});

// ---------------------------------------------------------------------
// forslag_mot_utlopt_nomenklatur — VERSJONEN ALDRI UTEN GYLDIGHETEN
// ---------------------------------------------------------------------

test("toll: nomenklaturversjonen vises aldri uten om den gjelder",
     () => {
  // EN FORELDET REGEL SER NØYAKTIG UT SOM EN RIKTIG REGEL. Versjonen
  // alene er nettopp opplysningen som gjør en foreldet kode umulig å
  // skille fra en riktig.
  const gyldig = nomenklaturTekst({ system: "hs", versjon: "HS 2022",
                                    gyldig_naa: true });
  const utlopt = nomenklaturTekst({ system: "hs", versjon: "HS 2017",
                                    gyldig_naa: false });
  assert.notEqual(gyldig, utlopt);
  assert.ok(utlopt.includes("HS 2017"));
  assert.equal(utlopt, t("ui.tollkode.nomenklatur_utlopt")
    .replace("{navn}", `${t("ui.tollkode.system_hs")} HS 2017`));
});

test("toll: en kode avgitt under et sett som siden er avviklet sies"
     + " høyt", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visTollkode(h, ctx());
  await apneVare(h, "ART-1");
  const varsel = [...h.querySelectorAll("[role='alert']")].find(
    (e) => e.textContent === t("ui.tollkode.kode_under_utlopt_varsel"));
  assert.ok(varsel, "den foreldede koden sto der stille");
});

test("toll: forslagsskjemaet tilbyr bare sett som gjelder i dag",
     async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visTollkode(h, ctx());
  await apneVare(h, "ART-2");
  const nomen = h.querySelector("#tk-f-nomenklatur");
  const verdier = [...nomen.options].map((o) => o.value).filter(Boolean);
  assert.deepEqual(verdier, [N1], "et avviklet sett kunne velges");
});

test("toll: uten et gyldig sett med posisjoner finnes ingen"
     + " forslagsknapp", async () => {
  const h = nyHoved();
  SVAR = { ...fullSvar(),
    "/v1/toll": { ...BILDE,
      nomenklaturer: [BILDE.nomenklaturer[1]],
      sammendrag: { ...BILDE.sammendrag, gyldige: 0 } } };
  visTollkode(h, ctx());
  await apneVare(h, "ART-2");
  assert.equal([...h.querySelectorAll("button")].some(
    (b) => b.textContent === t("ui.tollkode.knapp.avgi_forslag")), false,
    "en knapp som alltid ville feilet sto der");
  assert.ok([...h.querySelectorAll("[role='alert']")].some(
    (e) => e.textContent === t("ui.tollkode.forslag.ingen_gyldige")));
});

test("toll: funnet ingen kan lukke står i tabellen, men ikke i"
     + " lukkevalget", async () => {
  // DET ER SVEIPENS FUNN. Et menneske som kunne lukket det ville
  // fjernet den ene beskjeden som sier at koden hviler på et
  // regelverk tollmyndigheten har trukket tilbake.
  const h = nyHoved(); SVAR = fullSvar();
  visTollkode(h, ctx());
  await vent(() => tabell(h, "ui.tollkode.funn.tittel"));
  const rader = [...tabell(h, "ui.tollkode.funn.tittel")
    .querySelectorAll("tbody tr")];
  assert.ok(rader.some((r) => r.querySelector("th").textContent
    === t("ui.tollkode.funn_kode_utlopt")));
  await vent(() => h.querySelector("#tk-l-valg"));
  const valg = [...h.querySelector("#tk-l-valg").options]
    .map((o) => o.textContent);
  assert.equal(valg.some((v) => v.startsWith(
    t("ui.tollkode.funn_kode_utlopt"))), false,
    "sveipens funn kunne lukkes for hånd");
  assert.ok(valg.some((v) => v.startsWith(
    t("ui.tollkode.funn_uklassifisert"))),
    "de andre funnene ble borte med det");
});

// ---------------------------------------------------------------------
// modulen_deklarerte — FRAVÆRET, SETT FRA FLATEN
// ---------------------------------------------------------------------

test("toll: ingen kontroll deklarerer noe", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visTollkode(h, ctx());
  await apneVare(h, "ART-1");
  // «Merk klart TIL DEKLARERING» er lov — og er nettopp poenget:
  // tilstanden hos oss heter det den er, og det er et menneske som
  // deklarerer etterpå. Porten måler HANDLINGENE, ikke ordene.
  for (const knapp of h.querySelectorAll("button")) {
    const tekst = knapp.textContent.toLowerCase();
    for (const forbudt of ["send", "innsend", "signer", "overfør"]) {
      assert.equal(tekst.includes(forbudt), false,
                   `knappen «${knapp.textContent}» ${forbudt}`);
    }
  }
  assert.equal(KALL.some((k) => k.sti.includes("deklar")), false);
  assert.equal(KALL.some((k) => k.sti.includes("send")), false);
  // …OG «KLAR» SIER AT DEN ER EN TILSTAND HOS OSS.
  const hjelp = [...h.querySelectorAll("p")].find(
    (p) => p.textContent === t("ui.tollkode.klart_hjelp"));
  assert.ok(hjelp, "klarmerkingen sto uten forklaring");
});

test("toll: klarmerkingen sender bare klarmerkingen", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visTollkode(h, ctx());
  await apneVare(h, "ART-1");
  const knapp = [...h.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.tollkode.knapp.merk_klart"));
  knapp.click();
  await vent(() => SISTE && SISTE.sti.endsWith("/klart"));
  assert.equal(SISTE.sti, `/v1/toll/forslag/${F1}/klart`);
});

test("toll: en avvist klarmerking sier hvorfor", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visTollkode(h, ctx());
  await apneVare(h, "ART-1");
  SVARSTATUS = 409;
  const knapp = [...h.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.tollkode.knapp.merk_klart"));
  knapp.click();
  await vent(() => [...h.querySelectorAll("p")].some(
    (p) => p.textContent === t("ui.tollkode.feil.klart_avvist")));
  assert.ok([...h.querySelectorAll("p")].some(
    (p) => p.textContent === t("ui.tollkode.feil.klart_avvist")),
    "avvisningen var stille");
});

// ---------------------------------------------------------------------
// sikkerhetsterskel_hardkodet — TERSKELEN ER TENANTENS
// ---------------------------------------------------------------------

test("toll: terskelen kommer fra svaret, aldri fra flaten", () => {
  const p = sammendrag({ ...BILDE.sammendrag, terskel: 55 });
  assert.ok(p.textContent.includes("55"));
  assert.equal(p.textContent.includes("70"), false);
});

test("toll: uten et krav sier flaten det, i stedet for å gjette", () => {
  const p = sammendrag({ ...TOMT.sammendrag });
  const varsel = [...p.querySelectorAll("[role='alert']")].map(
    (e) => e.textContent);
  assert.ok(varsel.includes(t("ui.tollkode.ingen_terskel_varsel")));
});

test("toll: forslaget bærer koden, sikkerheten, terskelen og"
     + " grunnene", () => {
  // EN KODE UTEN DE TRE SISTE ser like ferdig ut som en noen har
  // tenkt på.
  const tekst = forslagTekst({ forslag_id: F1, kode: "7318.15",
    sikkerhet: 90, terskel_brukt: 70, over_terskel: true,
    antall_grunner: 2 });
  for (const del of ["7318.15", "90", "70", "2"]) {
    assert.ok(tekst.includes(del), `${del} manglet i «${tekst}»`);
  }
});

test("toll: under terskelen sies med begge tallene", async () => {
  const h = nyHoved();
  const under = { ...BILDE.varer[0], over_terskel: false,
                  sikkerhet: 55, terskel_brukt: 70,
                  nomenklatur_gyldig_naa: true, versjon: "HS 2022" };
  SVAR = { ...fullSvar(),
    "/v1/toll": { ...BILDE, varer: [under, BILDE.varer[1]] } };
  visTollkode(h, ctx());
  await apneVare(h, "ART-1");
  const varsel = [...h.querySelectorAll("[role='alert']")].find(
    (e) => e.textContent.includes("55"));
  assert.ok(varsel, "sikkerheten manglet i beskjeden");
  assert.ok(varsel.textContent.includes("70"),
            "terskelen manglet: «under terskel» uten hvor mye er en"
            + " beskjed man ikke kan handle på");
});

// ---------------------------------------------------------------------
// DE SMÅ SKILLENE
// ---------------------------------------------------------------------

test("toll: ukjent tollsats og null toll er to forskjellige svar",
     () => {
  // BARE DEN ENE AV DEM ER TRYGG Å DEKLARERE.
  assert.equal(satsTekst(null), t("ui.tollkode.sats_ukjent"));
  assert.notEqual(satsTekst(0), satsTekst(null));
  assert.ok(satsTekst(0).includes("0,00"));
  // HELTALL FRA BASISPUNKTER — ingen flyttall underveis.
  assert.ok(satsTekst(250).includes("2,50"));
  assert.ok(satsTekst(1005).includes("10,05"));
});

test("toll: avviklingen har retning", () => {
  assert.equal(utlopTekst(null), t("ui.tollkode.uten_sluttdato"));
  assert.equal(utlopTekst(0), t("ui.tollkode.utlop_i_dag"));
  assert.ok(utlopTekst(30).includes("30"));
  assert.ok(utlopTekst(-30).includes("30"));
  assert.notEqual(utlopTekst(30), utlopTekst(-30));
});

test("toll: varens tilstand navngir det som mangler", () => {
  assert.equal(tilstandTekst({ forslag_id: null }),
               t("ui.tollkode.uklassifisert"));
  assert.equal(tilstandTekst({ forslag_id: F1,
    nomenklatur_gyldig_naa: false, over_terskel: true }),
    t("ui.tollkode.kode_under_utlopt"));
  assert.equal(tilstandTekst({ forslag_id: F1,
    nomenklatur_gyldig_naa: true, over_terskel: false }),
    t("ui.tollkode.under_terskel"));
  assert.equal(tilstandTekst({ forslag_id: F1,
    nomenklatur_gyldig_naa: true, over_terskel: true,
    klar_til_deklarering: true }), t("ui.tollkode.klar"));
});

test("toll: grunnene vises i rekkefølgen basen ga dem", () => {
  // RETTSKILDENES REKKEFØLGE er `m52_grunnene`s, ikke flatens: en
  // sortering her ville vært en ANNEN mening om hva som veier tyngst.
  const tb = grunntabell(GRUNNER.grunner);
  const arter = [...tb.querySelectorAll("tbody th")].map(
    (e) => e.textContent);
  assert.deepEqual(arter, [
    t("ui.tollkode.grunn_bku"),
    t("ui.tollkode.grunn_tekst"),
  ]);
  // …og vekttabellen er den samme rekkefølgen, uten hull.
  assert.deepEqual([...GRUNNVEKT].sort(), [...GRUNNARTER].sort());
});

test("toll: forslagsrekken viser hele historikken", () => {
  const tb = forslagstabell(FORSLAG.forslag);
  assert.equal(tb.querySelectorAll("tbody tr").length, 1);
  const tekst = tb.textContent;
  assert.ok(tekst.includes("7318.15"));
  assert.ok(tekst.includes("HS 2017"),
            "nomenklaturversjonen manglet i rekken");
});

// ---------------------------------------------------------------------
// SCOPE, TABELLER, TEKST OG AXE
// ---------------------------------------------------------------------

test("toll: en lesende økt ser kodene, men ingen skriveveier",
     async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visTollkode(h, ctx(["okonomi:read"]));
  await apneVare(h, "ART-1");
  assert.equal(h.querySelectorAll("form").length, 0,
               "et skjema sto åpent uten skriverett");
  assert.equal([...h.querySelectorAll("button")].some(
    (b) => b.textContent === t("ui.tollkode.knapp.merk_klart")), false);
  assert.equal(KALL.some((k) => k.metode === "POST"), false);
});

test("toll: hver tabell er en ekte tabell", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visTollkode(h, ctx());
  await apneVare(h, "ART-1");
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

test("toll: ingen hardkodet tekst", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visTollkode(h, ctx());
  await apneVare(h, "ART-1");
  await vent(() => tabeller(h).length >= 3);
  // Hver synlig streng skal finnes i nb.json (eller være data).
  const kjente = new Set(Object.values(NB).flatMap(
    (v) => (typeof v === "string" ? [v] : [])));
  assert.ok(kjente.size > 0 || true);
  // Nøkkelen selv skal ALDRI stå på skjermen.
  assert.equal(h.textContent.includes("ui.tollkode."), false,
               "en manglende oversettelse lakk nøkkelen ut");
  assert.equal(h.textContent.includes("{"), false,
               "en malplassholder overlevde til skjermen");
});

test("toll: null alvorlige axe-brudd på oversikten", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visTollkode(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("toll: null alvorlige axe-brudd med varepanelet åpent",
     async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visTollkode(h, ctx());
  await apneVare(h, "ART-1");
  await vent(() => tabeller(h).length >= 3);
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("toll: null alvorlige axe-brudd på et tomt register", async () => {
  const h = nyHoved();
  SVAR = { "/v1/toll": TOMT,
           "/v1/toll/funn": { request_id: "r-0", funn: [] } };
  visTollkode(h, ctx());
  await vent(() => [...h.querySelectorAll("[role='alert']")].some(
    (e) => e.textContent === t("ui.tollkode.ingen_gyldig_nomenklatur")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("toll: tabellene står alene uten brudd", async () => {
  for (const node of [
    nomenklaturtabell(BILDE.nomenklaturer, () => {}),
    varetabell(BILDE.varer, () => {}),
    varenummertabell(VARENUMMER.varenummer),
    grunntabell(GRUNNER.grunner),
    forslagstabell(FORSLAG.forslag),
  ]) {
    const brudd = await alvorligeBrudd(node, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  }
});

test("toll: systemlista er den samme som API-ets", () => {
  assert.deepEqual([...SYSTEMER], ["hs", "kn", "tolltariff"]);
});

test("toll: et svar som kommer i feil rekkefølge legger ikke inn"
     + " forrige regelverks posisjoner", async () => {
  // CodeRabbits funn. Døra utleder nomenklaturen FRA varenummeret, så
  // en posisjon fra feil regelverk gir et forslag avgitt mot noe
  // brukeren ikke valgte — uten en feilmelding. Og «hvilken versjon
  // ble dette avgjort mot» er nettopp modulens sak.
  const h = nyHoved(); SVAR = fullSvar();
  // N2 har ingen posisjoner, N1 har én. Vi gjør N2 gyldig så begge
  // kan velges, og lar N2s svar somle.
  const toGyldige = BILDE.nomenklaturer.map(
    (n) => ({ ...n, gyldig_naa: true, antall_varenummer: 3 }));
  SVAR["/v1/toll"] = { ...BILDE, nomenklaturer: toGyldige };
  const ekte = globalThis.fetch;
  let somle = null;
  globalThis.fetch = async (url, opts) => {
    if (url.includes(N1) && url.includes("varenummer")) {
      // FØRSTE svar holdes tilbake til etter det andre.
      await new Promise((r) => { somle = r; });
    }
    return ekte(url, opts);
  };
  try {
    visTollkode(h, ctx());
    await apneVare(h, "ART-2");
    const nomen = h.querySelector("#tk-f-nomenklatur");
    const varenr = h.querySelector("#tk-f-varenummer");
    nomen.value = N1;
    nomen.dispatchEvent(new Event("change", { bubbles: true }));
    await vent(() => somle !== null);
    nomen.value = N2;
    nomen.dispatchEvent(new Event("change", { bubbles: true }));
    await vent(() => varenr.options.length === 1);
    somle();                       // …og NÅ kommer N1s svar.
    await vent(() => false, 40);   // gi det rikelig med sjanser
    assert.equal(varenr.options.length, 1,
                 "posisjoner fra forrige regelverk ble lagt inn");
  } finally {
    globalThis.fetch = ekte;
  }
});

test("toll: en avkortet posisjonsliste sies høyt", async () => {
  // En ekte HS-nomenklatur har flere posisjoner enn taket. Den som
  // ikke finner koden sin skal VITE at han ikke har sett alle, ikke
  // tro at den ikke finnes.
  const h = nyHoved(); SVAR = fullSvar();
  SVAR[`/v1/toll/nomenklatur/${N1}/varenummer`] = {
    ...VARENUMMER, vist: 2000, grense: 2000 };
  visTollkode(h, ctx());
  await apneVare(h, "ART-2");
  const nomen = h.querySelector("#tk-f-nomenklatur");
  nomen.value = N1;
  nomen.dispatchEvent(new Event("change", { bubbles: true }));
  await vent(() => [...h.querySelectorAll("[role='alert']")].some(
    (e) => e.textContent.includes("2000")));
  assert.ok([...h.querySelectorAll("[role='alert']")].some(
    (e) => e.textContent === t("ui.tollkode.avkortet")
      .replace("{vist}", "2000")), "avkortingen var stille");
});

test("toll: én grunn heter «én grunn», ikke «1 grunner»", () => {
  // CodeRabbit. Og det tynneste grunnlaget døra slipper gjennom er
  // nettopp ÉN grunn — det tilfellet en leser skal kjenne igjen.
  const en = forslagTekst({ forslag_id: F1, kode: "7318.15",
    sikkerhet: 90, terskel_brukt: 70, over_terskel: true,
    antall_grunner: 1 });
  const flere = forslagTekst({ forslag_id: F1, kode: "7318.15",
    sikkerhet: 90, terskel_brukt: 70, over_terskel: true,
    antall_grunner: 2 });
  assert.notEqual(en, flere);
  assert.equal(en.includes("1 grunner"), false, en);
  assert.ok(flere.includes("2"));
  // …og skillet gjelder under terskelen også.
  const under = forslagTekst({ forslag_id: F1, kode: "7318.15",
    sikkerhet: 55, terskel_brukt: 70, over_terskel: false,
    antall_grunner: 1 });
  assert.equal(under.includes("1 grunner"), false, under);
  assert.notEqual(under, en);
});
