// M-40 medarbeiderflaten (140) — flateporten (jsdom + axe).
//
// PORTENE MÅLER KLYNGENS DOM, IKKE BARE AT SKJERMEN TEGNES.
//
//   EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
//   ROLLBACK.
//
// M-28 sa det om en bil på veien. Her er det tyngre: en oppsigelse som
// ble rullet tilbake er fortsatt en samtale som fant sted. Derfor
// måler portene her:
//
//   * at «BESLUTNINGER: 0» og «INDIVIDPROFILER: 0» står i
//     sammendraget, ALLTID.
//   * at det ikke finnes en «vurder»-, «ranger»- eller «score»-knapp.
//   * at PULSSKJEMAET IKKE HAR ET PERSONFELT — det finnes ingen
//     kolonne å skrive det i, og skjemaet skal ikke antyde at det gjør.
//   * at et tomt pulsbilde SIER HVORFOR uten å si hvor mange: tallet
//     ville i seg selv vært det terskelen verner.
//   * at løpslisten viser ANSATTNUMMERET og aldri et navn.
//   * at kontraktlisten viser malversjonen og kildefeltene, aldri
//     verdiene.
//   * at en tilbaketrukket mal er et varsel.
//
// Ingen delt fixture (m16-formen): hver test bygger sin egen skjerm.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import {
  AVSLUTNINGER, dato, framdrift, funnrader, kontraktrader, loprader,
  maalingsrader, malsporet, pulskvittering, pulsrader, sammendrag,
  STEGTYPER, visMedarbeider,
} from "../static/js/flater/medarbeider.js";

settI18nForTest(NB, "nb");

const L1 = "aaaaaaaa-1111-1111-1111-111111111111";
const L2 = "aaaaaaaa-2222-2222-2222-222222222222";
const T1 = "cccccccc-1111-1111-1111-111111111111";
const K1 = "bbbbbbbb-1111-1111-1111-111111111111";
const M1 = "dddddddd-1111-1111-1111-111111111111";
const M2 = "dddddddd-2222-2222-2222-222222222222";
const U1 = "eeeeeeee-1111-1111-1111-111111111111";
const U2 = "eeeeeeee-2222-2222-2222-222222222222";
const V1 = "ffffffff-1111-1111-1111-111111111111";

const LOP = [
  { lop_id: L1, taker_id: T1, ekstern_ref: "ans-014", status: "apent",
    startet: "2026-09-01T08:00:00+00:00", steg: 5, steg_utfort: 3 },
  { lop_id: L2, taker_id: T1, ekstern_ref: "ans-009",
    status: "fullfort", startet: "2026-08-01T08:00:00+00:00",
    steg: 5, steg_utfort: 5 },
];

const KONTRAKTER = [
  // EN KONTRAKT PÅ EN MAL SOM SIDEN ER TRUKKET TILBAKE.
  { kontrakt_id: K1, taker_id: T1, ekstern_ref: "ans-014",
    malversjon_id: V1, malversjonsnr: 2,
    malnavn: "Ansettelseskontrakt", malstatus: "tilbaketrukket",
    felt: ["startdato", "stilling"],
    utstedt: "2026-09-01T09:00:00+00:00" },
];

const MAALINGER = [
  // EN MED LESBARE GRUPPER.
  { maaling_id: M1, tittel: "Trivsel Q3", gruppeterskel: 5,
    apnet: "2026-09-01T08:00:00+00:00", lukket: null,
    lesbare_grupper: 2 },
  // OG EN INGEN FÅR LESE.
  { maaling_id: M2, tittel: "Ledergruppen", gruppeterskel: 5,
    apnet: "2026-08-01T08:00:00+00:00",
    lukket: "2026-08-20T08:00:00+00:00", lesbare_grupper: 0 },
];

const FUNN = [
  { funn_id: U1, funntype: "maaling_uten_lesbar_gruppe",
    referanse: M2,
    detalj: "maalingen «Ledergruppen» har svar, men ingen gruppe naar terskelen",
    sveipens: true, forst_sett: "2026-09-06T04:00:00+00:00" },
  { funn_id: U2, funntype: "kontrakt_paa_tilbaketrukket_mal",
    referanse: K1,
    detalj: "kontrakten hviler paa malversjon 2, som er trukket tilbake",
    sveipens: true, forst_sett: "2026-09-06T04:00:00+00:00" },
];

const BILDE = {
  request_id: "r-m",
  sammendrag: {
    apne_lop: 1, fullforte_lop: 1, kontrakter: 1, apne_maalinger: 1,
    lesbare_grupper: 2, apne_funn: 2, beslutninger: 0,
    individprofiler: 0, har_krav: true, gruppeterskel_min: 5,
    apent_lop_frist_dogn: 14, kravversjon: 1,
  },
  lop: LOP, kontrakter: KONTRAKTER, maalinger: MAALINGER, funn: FUNN,
};

const TOMT = {
  request_id: "r-m",
  sammendrag: {
    apne_lop: 0, fullforte_lop: 0, kontrakter: 0, apne_maalinger: 0,
    lesbare_grupper: 0, apne_funn: 0, beslutninger: 0,
    individprofiler: 0, har_krav: false, gruppeterskel_min: null,
    apent_lop_frist_dogn: null, kravversjon: null,
  },
  lop: [], kontrakter: [], maalinger: [], funn: [],
};

//: Aggregatet for M1 — to grupper som begge når terskelen.
const PULS_LESBAR = { request_id: "r-p", grupper: [
  { gruppe: "drift", antall: 8, snitt: 4.13 },
  { gruppe: "utvikling", antall: 6, snitt: 3.5 },
] };

//: Aggregatet for M2 — TOMT, fordi ingen gruppe er stor nok.
const PULS_TOMT = { request_id: "r-p", grupper: [] };

let SVAR;
let SISTE;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  if (opts && opts.method === "POST") {
    SISTE = { sti, kropp: opts.body ? JSON.parse(opts.body) : null };
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
  return { sprak: "nb", scopes, tenant: "acme",
           paaUautorisert: () => {} };
}

async function vent(pred, n = 120) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 5));
  }
  return pred();
}

function nyHoved() {
  const brett = nyttBrett();
  const m = document.createElement("main");
  m.id = "hovedinnhold";
  m.tabIndex = -1;
  brett.append(m);
  SVAR = {
    "/v1/medarbeider": BILDE,
    [`/v1/medarbeider/maaling/${M1}/puls`]: PULS_LESBAR,
    [`/v1/medarbeider/maaling/${M2}/puls`]: PULS_TOMT,
  };
  SISTE = null;
  return m;
}


// =====================================================================
// DET MODULEN IKKE GJØR, SKAL SES.
// =====================================================================

test("sammendraget sier alltid at ingenting er avgjort", () => {
  // TALLENE ER IKKE TELLINGER. De er påstander om at kolonnene ikke
  // finnes: det er ingen tabell å telle en beslutning i, og ingen
  // kolonne å telle en profil i.
  const ventet = t("ui.medarbeider.ingen_beslutning")
    .replace("{b}", "0").replace("{p}", "0");
  assert.ok(sammendrag(BILDE.sammendrag).textContent.includes(ventet));
  assert.ok(sammendrag(TOMT.sammendrag).textContent.includes(ventet));
});

test("flaten har ingen vurder-, ranger- eller scoreknapp", async () => {
  const h = nyHoved();
  await visMedarbeider(h, ctx());
  await vent(() => h.querySelector("#md-terskel"));
  const tekster = [...h.querySelectorAll("button")]
    .map((b) => b.textContent.toLowerCase());
  for (const forbudt of ["vurder", "ranger", "score", "profil",
                         "ansett", "si opp", "oppsig", "avskjed",
                         "lønn"]) {
    for (const tekst of tekster) {
      assert.ok(!tekst.includes(forbudt),
                `knappen «${tekst}» inneholder «${forbudt}»`);
    }
  }
});

test("pulsskjemaet har ingen personnokkel", async () => {
  // DET ER IKKE ET SKJULT FELT. Det finnes ingen kolonne å skrive en
  // personnøkkel i, og skjemaet skal ikke antyde at det gjør.
  const h = nyHoved();
  await visMedarbeider(h, ctx());
  await vent(() => h.querySelector("#md-terskel"));
  h.querySelectorAll("button").forEach((b) => {
    if (b.textContent === t("ui.medarbeider.svar_puls")) b.click();
  });
  await vent(() => h.querySelector("#md-gruppe"));
  const skjema = h.querySelector("#md-gruppe").closest("form");
  const felter = [...skjema.querySelectorAll("input, select, textarea")]
    .map((x) => x.id).sort();
  assert.deepEqual(felter, ["md-gruppe", "md-verdi"]);
});

test("pulsen sendes uten hvem som svarte", async () => {
  const h = nyHoved();
  await visMedarbeider(h, ctx());
  await vent(() => h.querySelector("#md-terskel"));
  h.querySelectorAll("button").forEach((b) => {
    if (b.textContent === t("ui.medarbeider.svar_puls")) b.click();
  });
  await vent(() => h.querySelector("#md-gruppe"));
  h.querySelector("#md-gruppe").value = "drift";
  h.querySelector("#md-verdi").value = "5";
  const skjema = h.querySelector("#md-gruppe").closest("form");
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true,
                                                    bubbles: true }));
  await vent(() => SISTE !== null);
  assert.ok(SISTE, "pulsen ble aldri sendt");
  assert.equal(SISTE.sti, `/v1/medarbeider/maaling/${M1}/puls`);
  assert.deepEqual(Object.keys(SISTE.kropp).sort(), ["gruppe", "verdi"]);
  for (const forbudt of ["taker_id", "ansatt_id", "person_id", "navn",
                         "epost", "bruker", "bruker_id"]) {
    assert.ok(!(forbudt in SISTE.kropp), `kroppen bar ${forbudt}`);
  }
});

test("pulskvitteringen sier ikke hva som ble svart", () => {
  // EGEN FUNKSJON FORDI DEN SKAL KUNNE MÅLES (138s lærdom).
  //
  // En kvittering med verdien i ville vært den eneste linjen i
  // systemet som koblet et menneske til sin egen puls — og den ville
  // stått på hennes egen skjerm.
  const tekst = pulskvittering();
  assert.equal(tekst, t("ui.medarbeider.puls_mottatt"));
  for (const tall of ["1", "2", "3", "4", "5"]) {
    assert.ok(!tekst.includes(tall), `kvitteringen bar tallet ${tall}`);
  }
});


// =====================================================================
// AGGREGATET: ET TOMT SVAR ER ET GYLDIG SVAR.
// =====================================================================

test("et tomt pulsbilde sier hvorfor uten aa si hvor mange", async () => {
  // TALLET VILLE I SEG SELV VÆRT DET TERSKELEN VERNER: «to av fem har
  // svart» forteller nøyaktig så mye om en gruppe på fem som
  // aggregatet nekter å si.
  const h = nyHoved();
  await visMedarbeider(h, ctx());
  await vent(() => h.querySelector("#md-terskel"));
  const knapper = [...h.querySelectorAll("button")]
    .filter((b) => b.textContent === t("ui.medarbeider.se_puls"));
  // Andre rad er M2 — den ingen får lese.
  knapper[1].click();
  await vent(() => h.textContent.includes(
    t("ui.medarbeider.puls_for_faa").replace("{terskel}", "5")));
  assert.ok(h.textContent.includes(
    t("ui.medarbeider.puls_for_faa").replace("{terskel}", "5")));
  // OG DET FINNES INGEN TABELL MED TALL.
  const tabeller = [...h.querySelectorAll("table")];
  for (const tab of tabeller) {
    assert.ok(!tab.textContent.includes(t("ui.medarbeider.kol_snitt")),
              "et tomt aggregat viste en snittkolonne");
  }
});

test("et lesbart pulsbilde viser gruppene", async () => {
  const h = nyHoved();
  await visMedarbeider(h, ctx());
  await vent(() => h.querySelector("#md-terskel"));
  const knapper = [...h.querySelectorAll("button")]
    .filter((b) => b.textContent === t("ui.medarbeider.se_puls"));
  knapper[0].click();
  // VENT PÅ SNITTET, IKKE PÅ «drift».
  //
  // Kolonneoverskriften «Framdrift» INNEHOLDER «drift», så en vent på
  // den ordet ville returnert med én gang — før nettverksrunden var
  // ferdig — og porten ville målt at siden tegnet seg, ikke at
  // aggregatet kom. Første utgave gjorde nettopp det.
  await vent(() => h.textContent.includes("4.13"));
  assert.ok(h.textContent.includes("4.13"));
  assert.ok(h.textContent.includes("utvikling"));
  assert.ok(h.textContent.includes("3.5"));
});

test("pulsradene baerer gruppe, antall og snitt — ikke mer", () => {
  const rader = pulsrader(PULS_LESBAR.grupper);
  assert.equal(rader.length, 2);
  assert.equal(rader[0].querySelectorAll("td").length, 3);
});


// =====================================================================
// ANSATTREGISTERET ER M-39s.
// =====================================================================

test("loepslisten viser ansattnummeret og aldri et navn", () => {
  const rader = loprader(BILDE, { kanSkrive: false });
  assert.ok(rader[0].textContent.includes("ans-014"));
  // MODULEN KJENNER IKKE NAVN: kolonnegranten på `lonnstaker` utelater
  // `navn`, så flaten kan ikke vise det selv om noen ba den om det.
  for (const rad of rader) {
    assert.ok(!/[A-ZÆØÅ][a-zæøå]+ [A-ZÆØÅ][a-zæøå]+/.test(rad.textContent),
              `raden ser ut til aa baere et navn: ${rad.textContent}`);
  }
});

test("framdriften er en broek og ikke en prosent", () => {
  // «3 av 5» og ikke «60 %»: en prosent av fem ting er en presisjon
  // tallet ikke har.
  assert.equal(framdrift(LOP[0]),
               t("ui.medarbeider.framdrift")
                 .replace("{utfort}", "3").replace("{av}", "5"));
  assert.equal(framdrift(null), "–");
  assert.ok(!framdrift(LOP[0]).includes("%"));
});


// =====================================================================
// KONTRAKTEN: MALVERSJON OG KILDEFELT, ALDRI VERDIER.
// =====================================================================

test("kontraktlisten viser malen og feltene, aldri verdiene", () => {
  const rader = kontraktrader(BILDE);
  const tekst = rader[0].textContent;
  assert.ok(tekst.includes("Ansettelseskontrakt"));
  assert.ok(tekst.includes("v2"));
  assert.ok(tekst.includes("startdato"));
  assert.ok(tekst.includes("stilling"));
  // FEM KOLONNER, OG INGEN AV DEM ER EN VERDI.
  assert.equal(rader[0].querySelectorAll("td").length, 5);
});

test("en tilbaketrukket mal er et varsel", () => {
  // Kontrakten er ikke ugyldig — den var gyldig da den ble utstedt, og
  // hashen beviser hva den hvilte på. Men noen bør se på den.
  const rader = kontraktrader(BILDE);
  const varsel = rader[0].querySelector("strong[role=alert]");
  assert.ok(varsel, "en tilbaketrukket mal sto uten varsel");
  assert.equal(varsel.textContent,
               t("ui.medarbeider.mal_tilbaketrukket"));
});

test("malsporet baerer navn og versjon", () => {
  assert.equal(malsporet(KONTRAKTER[0]),
               t("ui.medarbeider.malspor")
                 .replace("{navn}", "Ansettelseskontrakt")
                 .replace("{v}", "2"));
  assert.equal(malsporet(null), "–");
});

test("kontraktskjemaet spor om feltnokler og ikke om verdier", async () => {
  const h = nyHoved();
  await visMedarbeider(h, ctx());
  await vent(() => h.querySelector("#md-felter"));
  const skjema = h.querySelector("#md-felter").closest("form");
  const felter = [...skjema.querySelectorAll("input, select, textarea")]
    .map((x) => x.id).sort();
  assert.deepEqual(felter, ["md-felter", "md-ktaker", "md-mal"]);
});

test("kontrakten sendes med feltnokler som liste", async () => {
  const h = nyHoved();
  await visMedarbeider(h, ctx());
  await vent(() => h.querySelector("#md-felter"));
  h.querySelector("#md-ktaker").value = T1;
  h.querySelector("#md-mal").value = V1;
  h.querySelector("#md-felter").value = " stilling , startdato ";
  const skjema = h.querySelector("#md-felter").closest("form");
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true,
                                                    bubbles: true }));
  await vent(() => SISTE !== null);
  assert.equal(SISTE.sti, "/v1/medarbeider/kontrakt/ny");
  assert.deepEqual(SISTE.kropp.feltnokler, ["stilling", "startdato"]);
});


// =====================================================================
// MÅLINGENE: LESBARE GRUPPER, ALDRI ANTALL SVAR.
// =====================================================================

test("maalingslisten teller lesbare grupper og ikke svar", () => {
  // Et totaltall for en måling med én gruppe VILLE VÆRT gruppens tall,
  // og da hadde terskelen vært omgått av oversikten framfor av
  // aggregatet.
  const rader = maalingsrader(BILDE, { kanSkrive: true });
  assert.ok(rader[0].textContent.includes("2"));
  assert.ok(rader[1].textContent.includes(t("ui.medarbeider.ingen_lesbar")));
});

test("maalingsskjemaets terskel har tenantens gulv som minimum", async () => {
  const h = nyHoved();
  await visMedarbeider(h, ctx());
  await vent(() => h.querySelector("#md-mterskel"));
  const felt = h.querySelector("#md-mterskel");
  assert.equal(felt.getAttribute("min"), "5");
});

test("en lukket maaling kan verken svares paa eller lukkes igjen", () => {
  const rader = maalingsrader(BILDE, { kanSkrive: true });
  const knapper = [...rader[1].querySelectorAll("button")]
    .map((b) => b.textContent);
  assert.deepEqual(knapper, [t("ui.medarbeider.se_puls")]);
});


// =====================================================================
// FUNNENE OG SVEIPEN.
// =====================================================================

test("sveipens funn kan ikke lukkes av et menneske i flaten", () => {
  // Å lukke et funn sveipen reiser er å lukke en måling og ikke en
  // sak — det ville kommet tilbake neste natt.
  const rader = funnrader(BILDE, { kanSkrive: true });
  for (const rad of rader) {
    assert.equal(rad.querySelectorAll("button").length, 0);
    assert.ok(rad.textContent.includes(
      t("ui.medarbeider.lukkes_av_sveipen")));
  }
});

test("et menneskets funn faar en lukkeknapp naar hun kan skrive", () => {
  const eget = { funn: [{ ...FUNN[0], sveipens: false }] };
  const medRett = funnrader(eget, { kanSkrive: true });
  assert.equal(medRett[0].querySelectorAll("button").length, 1);
  const utenRett = funnrader(eget, { kanSkrive: false });
  assert.equal(utenRett[0].querySelectorAll("button").length, 0);
});


// =====================================================================
// TOMT, LESERETT OG TILGJENGELIGHET.
// =====================================================================

test("uten skriverett vises ingen skjemaer", async () => {
  const h = nyHoved();
  await visMedarbeider(h, ctx(["okonomi:read"]));
  await vent(() => h.textContent.includes(t("ui.medarbeider.lop")));
  assert.equal(h.querySelectorAll("form").length, 0);
});

test("uten krav sier flaten at grensene maa settes forst", async () => {
  const h = nyHoved();
  SVAR["/v1/medarbeider"] = TOMT;
  await visMedarbeider(h, ctx());
  await vent(() => h.textContent.includes(
    t("ui.medarbeider.lop_uten_krav")));
  assert.ok(h.textContent.includes(t("ui.medarbeider.lop_uten_krav")));
  // KRAVSKJEMAET STÅR LIKEVEL — ellers ville det ikke gått an å komme
  // videre.
  assert.ok(h.querySelector("#md-terskel"));
});

test("de lukkede settene har den formen migrasjonen har", () => {
  assert.equal(STEGTYPER.length, 7);
  assert.deepEqual(AVSLUTNINGER, ["fullfort", "avbrutt"]);
  // ET LUKKET SETT SOM KAN UTVIDES I FLATEN ER IKKE LUKKET: verdiene
  // her må finnes i 140s CHECK, og porten i test_m40_medarbeider.py
  // måler den andre veien.
  for (const s of STEGTYPER) {
    assert.match(s, /^[a-z_]+$/);
  }
});

test("datoen kutter til dag og taaler soppel", () => {
  assert.equal(dato("2026-09-01T08:00:00+00:00"), "2026-09-01");
  assert.equal(dato(null), "–");
  assert.equal(dato(""), "–");
});

test("flaten har ingen alvorlige tilgjengelighetsbrudd", async () => {
  const h = nyHoved();
  await visMedarbeider(h, ctx());
  await vent(() => h.querySelector("#md-terskel"));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});
