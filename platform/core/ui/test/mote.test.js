// M-7 møteflaten (133) — flateporten (jsdom + axe).
//
// PORTENE MÅLER KLYNGENS DOM, IKKE BARE AT SKJERMEN TEGNES.
//
//   En ytring avgitt i husets navn kan ikke tas tilbake — OG DEN SOM
//   LESER DEN VET IKKE AT EN MASKIN SKREV DEN.
//
// Et referat ser likt ut enten et menneske skrev det eller en
// transkripsjon gjettet. Derfor måler portene her:
//
//   * at `ubekreftet` står som et VARSEL i samme rad som teksten, og
//     at kilden står ved siden av.
//   * at TERSKELEN som gjaldt da står sammen med tallet — ikke dagens.
//   * at en beslutning som hviler på et ubekreftet punkt BÆRER DET
//     VIDERE. Usikkerheten forsvinner ikke fordi noen skrev
//     «besluttet» over den.
//   * at et opptak vises med sitt GRUNNLAG, alltid.
//   * at en utløpt hjemmel merkes — den ser ellers nøyaktig ut som en
//     gyldig.
//   * at det ikke finnes en «fatt beslutning»-knapp.
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
  aksjonstabell, beslutningstabell, funntabell, hjemmeltabell,
  motetabell, prosent, referattabell, sammendrag, visMote,
} from "../static/js/flater/mote.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));
const ROT = join(HER, "..", "..", "..", "..");

const M1 = "aaaaaaaa-1111-1111-1111-111111111111";
const H1 = "bbbbbbbb-1111-1111-1111-111111111111";
const H2 = "bbbbbbbb-2222-2222-2222-222222222222";
const P1 = "cccccccc-1111-1111-1111-111111111111";
const P2 = "cccccccc-2222-2222-2222-222222222222";
const A1 = "dddddddd-1111-1111-1111-111111111111";
const F1 = "eeeeeeee-1111-1111-1111-111111111111";
const F2 = "eeeeeeee-2222-2222-2222-222222222222";

// TO PUNKTER: ett bekreftet og manuelt, ett ubekreftet fra et opptak.
const REFERAT = {
  request_id: "r-r", mote_id: M1,
  punkter: [
    { punkt_id: P1, rekkefolge: 1,
      tekst: "Vi utsetter innkjoepet til neste kvartal",
      kilde: "manuell", kilde_ref: "u-kari", sikkerhet_bp: 10000,
      terskel_bp: 7000, ubekreftet: false, retter_punkt_id: null,
      registrert: "2026-09-01T10:00:00+00:00",
      registrert_av: "u-kari", er_rettet: false },
    { punkt_id: P2, rekkefolge: 2,
      tekst: "Noe uklart ble sagt om budsjettrammen",
      kilde: "opptak", kilde_ref: "opptak-1", sikkerhet_bp: 4000,
      terskel_bp: 7000, ubekreftet: true, retter_punkt_id: null,
      registrert: "2026-09-01T10:05:00+00:00",
      registrert_av: "m7", er_rettet: false },
  ],
  beslutninger: [
    { beslutning_id: "ffffffff-1111-1111-1111-111111111111",
      tekst: "Innkjoepet utsettes", besluttet_av: "u-kari",
      besluttet_ts: "2026-09-01T10:10:00+00:00", punkt_id: P2,
      punkt_ubekreftet: true },
  ],
};

const MOTER = [
  { mote_id: M1, tittel: "Styremoete mars",
    start_ts: "2026-09-01T09:00:00+00:00",
    slutt_ts: "2026-09-01T10:30:00+00:00", innkalt_av: "u-kari",
    antall_deltakere: 4, antall_punkter: 2, antall_ubekreftede: 1,
    antall_beslutninger: 1, antall_apne_aksjoner: 1,
    har_opptak: true, opptakshjemmel: "berettiget_interesse" },
];

const HJEMLER = [
  { hjemmel_id: H1, grunnlagstype: "berettiget_interesse",
    beskrivelse: "Referatfoering av styremoeter etter vedtak 12/24",
    formal: "referatfoering", gyldig_fra: "2026-01-01",
    gyldig_til: null, gjelder: true, antall_opptak: 3 },
  // EN UTLØPT HJEMMEL SER NØYAKTIG UT SOM EN GYLDIG.
  { hjemmel_id: H2, grunnlagstype: "samtykke",
    beskrivelse: "Opptak av kundesamtaler i pilotperioden",
    formal: "opplaering", gyldig_fra: "2025-01-01",
    gyldig_til: "2025-12-31", gjelder: false, antall_opptak: 12 },
];

const AKSJONER = [
  { aksjon_id: A1, mote_id: M1, tekst: "Hent inn tre tilbud",
    eier: "u-per", frist: "2026-08-15", status: "apen",
    lukket_av: null, dogn_over_frist: 21 },
];

const FUNN = [
  // SVEIPENS EGET — ingen kan lukke det.
  { funn_id: F1, funntype: "mote_uten_referat", referanse: M1,
    detaljer: "moetet sluttet og er 27 doegn over referatfristen",
    over_grense: 27, apen: true,
    forst_sett: "2026-08-15T09:00:00+00:00",
    sist_sett: "2026-09-05T09:00:00+00:00", lukket_av: null,
    kan_lukkes: false },
  // UBEKREFTET PUNKT — det KAN lukkes av et menneske.
  { funn_id: F2, funntype: "ubekreftet_punkt_uavklart",
    referanse: P2, detaljer: "foert med 4000 basispunkters sikkerhet",
    over_grense: 4000, apen: true,
    forst_sett: "2026-09-02T09:00:00+00:00",
    sist_sett: "2026-09-05T09:00:00+00:00", lukket_av: null,
    kan_lukkes: true },
];

const BILDE = {
  request_id: "r-x",
  sammendrag: {
    moter: 1, moter_uten_referat: 0, punkter: 2, ubekreftede: 1,
    beslutninger: 1, beslutninger_paa_ubekreftet: 1,
    apne_aksjoner: 1, aksjoner_over_frist: 1, opptak: 3, hjemler: 2,
    gyldige_hjemler: 1, apne_funn: 2, har_krav: true,
    referatfrist_dogn: 3, aksjonsfrist_dogn: 7,
    sikkerhetsterskel_bp: 7000, kravversjon: 1,
  },
  moter: MOTER, hjemler: HJEMLER, aksjoner: AKSJONER, funn: FUNN,
};

const TOMT = {
  request_id: "r-t",
  sammendrag: {
    moter: 0, moter_uten_referat: 0, punkter: 0, ubekreftede: 0,
    beslutninger: 0, beslutninger_paa_ubekreftet: 0,
    apne_aksjoner: 0, aksjoner_over_frist: 0, opptak: 0, hjemler: 0,
    gyldige_hjemler: 0, apne_funn: 0, har_krav: false,
    referatfrist_dogn: null, aksjonsfrist_dogn: null,
    sikkerhetsterskel_bp: null, kravversjon: null,
  },
  moter: [], hjemler: [], aksjoner: [], funn: [],
};

let SVAR;
let SISTE;
let KALL;
let SVARSTATUS;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  KALL.push({ sti, metode: (opts && opts.method) || "GET" });
  if (opts && opts.method === "POST") {
    SISTE = { sti, headers: opts.headers,
              kropp: opts.body ? JSON.parse(opts.body) : null };
    if (SVARSTATUS && SVARSTATUS !== 200) {
      return { ok: false, status: SVARSTATUS,
        json: async () => ({ feil: "mote_ulovlig_tilstand" }) };
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

function ctx(scopes = ["security:read", "bestilling:opprett"]) {
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
  SVAR = { "/v1/mote": BILDE, [`/v1/mote/${M1}/referat`]: REFERAT };
  SISTE = null;
  KALL = [];
  SVARSTATUS = 200;
  return m;
}


// =====================================================================
// USIKKERHETEN SKAL SES.
// =====================================================================

test("et ubekreftet punkt står som varsel, et bekreftet gjør ikke", () => {
  const tab = referattabell(REFERAT.punkter);
  const varsler = [...tab.querySelectorAll("strong[role='alert']")];
  assert.equal(varsler.length, 1, "bekreftet og ubekreftet tegnes likt");
  assert.equal(varsler[0].textContent, t("ui.mote.ubekreftet"));
  assert.ok(tab.textContent.includes(t("ui.mote.bekreftet")));
});

test("kilden står i samme rad som teksten", () => {
  const tab = referattabell(REFERAT.punkter);
  const rader = [...tab.querySelectorAll("tbody tr")];
  const forste = [...rader[0].querySelectorAll("td")]
    .map((c) => c.textContent);
  assert.ok(forste.includes(t("ui.mote.kilde_manuell")));
  const andre = [...rader[1].querySelectorAll("td")]
    .map((c) => c.textContent);
  assert.ok(andre.includes(t("ui.mote.kilde_opptak")));
});

test("terskelen som gjaldt DA står sammen med tallet", () => {
  // Uten den kan «hvorfor er dette merket?» ikke besvares etter at
  // grensen er justert.
  const tab = referattabell(REFERAT.punkter);
  assert.ok(tab.textContent.includes(
    t("ui.mote.sikkerhet_verdi")
      .replace("{n}", "40 %").replace("{terskel}", "70 %")));
});

test("en beslutning på et ubekreftet punkt bærer det videre", () => {
  // Usikkerheten forsvinner ikke fordi noen skrev «besluttet» over
  // den.
  const tab = beslutningstabell(REFERAT.beslutninger);
  assert.ok([...tab.querySelectorAll("strong[role='alert']")]
    .some((x) => x.textContent
      === t("ui.mote.hviler_paa_ubekreftet")));
  // …OG NAVNET STÅR. Modulen fatter ingen beslutning.
  assert.ok(tab.textContent.includes("u-kari"));
});

test("sammendraget setter beslutninger på ubekreftet grunnlag først", () => {
  const p = sammendrag(BILDE.sammendrag);
  const sterke = [...p.querySelectorAll("strong")];
  assert.equal(sterke[0].getAttribute("role"), "alert");
  assert.ok(sterke[0].textContent.includes("1"));
  assert.ok(sterke[0].textContent.includes(
    t("ui.mote.beslutning_paa_ubekreftet").split("{")[0].trim()));
});

test("prosent avrunder bare i visningen", () => {
  assert.equal(prosent(7000), "70 %");
  assert.equal(prosent(4050), "41 %");
  assert.equal(prosent(null), "–");
});


// =====================================================================
// OPPTAKET OG HJEMMELEN.
// =====================================================================

test("et møte med opptak viser grunnlaget uten et klikk til", () => {
  const tab = motetabell(MOTER, () => {});
  assert.ok(tab.textContent
    .includes(t("ui.mote.grunnlag_berettiget")));
});

test("et møte uten opptak sier det", () => {
  const tab = motetabell(
    [{ ...MOTER[0], har_opptak: false, opptakshjemmel: null }],
    () => {});
  assert.ok(tab.textContent.includes(t("ui.mote.intet_opptak")));
});

test("en utløpt hjemmel merkes som varsel", () => {
  // EN UTLØPT HJEMMEL SER NØYAKTIG UT SOM EN GYLDIG — klynge 7s dom.
  const tab = hjemmeltabell(HJEMLER, null);
  const varsler = [...tab.querySelectorAll("strong[role='alert']")]
    .map((x) => x.textContent);
  assert.deepEqual(varsler, [t("ui.mote.utlopt")]);
});

test("bare en gyldig hjemmel kan avsluttes", () => {
  const kalt = [];
  const tab = hjemmeltabell(HJEMLER, (h) => kalt.push(h.hjemmel_id));
  const knapper = [...tab.querySelectorAll("button")];
  assert.equal(knapper.length, 1);
  knapper[0].click();
  assert.deepEqual(kalt, [H1]);
});

test("opptakspanelet nekter uten en gyldig hjemmel", async () => {
  const h = nyHoved();
  SVAR["/v1/mote"] = {
    ...BILDE,
    hjemler: [{ ...HJEMLER[1] }],
    sammendrag: { ...BILDE.sammendrag, gyldige_hjemler: 0 },
  };
  await visMote(h, ctx());
  await vent(() => h.textContent.includes("Styremoete mars"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent
      === t("ui.mote.opptak_for").replace("{tittel}", "Styremoete mars"))
    .click();
  await vent(() => h.textContent
    .includes(t("ui.mote.ingen_gyldig_hjemmel")));
  // DØRA NEKTER, og panelet sier det i stedet for å la brukeren finne
  // det ut av en 400.
  assert.equal(h.querySelector("#mote-hjemmelvalg"), null);
});

test("opptaksskjemaet ber om varslingen før starttidspunktet", async () => {
  // REKKEFØLGEN I SKJEMAET ER REKKEFØLGEN I REGELEN.
  const h = nyHoved();
  await visMote(h, ctx());
  await vent(() => h.textContent.includes("Styremoete mars"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent
      === t("ui.mote.opptak_for").replace("{tittel}", "Styremoete mars"))
    .click();
  await vent(() => h.querySelector("#mote-varslet"));
  const felter = [...h.querySelectorAll("#mote-varslet, #mote-startet")]
    .map((x) => x.id);
  assert.deepEqual(felter, ["mote-varslet", "mote-startet"]);
  assert.ok(h.textContent.includes(t("ui.mote.varslet_hjelp")));
});

test("et tomt varslingsfelt sendes ikke", async () => {
  const h = nyHoved();
  await visMote(h, ctx());
  await vent(() => h.textContent.includes("Styremoete mars"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent
      === t("ui.mote.opptak_for").replace("{tittel}", "Styremoete mars"))
    .click();
  await vent(() => h.querySelector("#mote-varslet"));
  const skjema = h.querySelector("#mote-varslet").form;
  h.querySelector("#mote-varsletav").value = "u-kari";
  h.querySelector("#mote-varslede").value = "ext:1";
  h.querySelector("#mote-startet").value = "2026-09-01T09:00";
  // `varslet_ts` står tom.
  skjema.dispatchEvent(new window.Event("submit",
                                        { cancelable: true }));
  await vent(() => h.textContent.includes(t("ui.mote.feil.generell")));
  assert.equal(SISTE, null, "et tomt felt ble sendt som en varsling");
});


// =====================================================================
// V1-DOMMEN OG FUNNENE.
// =====================================================================

test("kilden har ingen vei som fatter en beslutning", () => {
  // KOMMENTARER **OG STRENGER** FJERNES FØRST (128s lærdom).
  const kilde = readFileSync(join(ROT, "platform", "core", "ui",
    "static", "js", "flater", "mote.js"), "utf8");
  const uten = kilde.split("\n")
    .filter((l) => !l.trim().startsWith("//")).join("\n")
    .replace(/"[^"\n]*"|'[^'\n]*'|`[^`]*`/g, '""');
  for (const forbudt of ["fattbeslutning", "fatt_beslutning",
                         "auto_beslutning"]) {
    assert.ok(!uten.toLowerCase().includes(forbudt), forbudt);
  }
});

test("funntabellen leser kan_lukkes fra basen", () => {
  const kalt = [];
  const tab = funntabell(FUNN, (f) => kalt.push(f.funntype));
  const knapper = [...tab.querySelectorAll("button")];
  assert.equal(knapper.length, 1);
  knapper[0].click();
  assert.deepEqual(kalt, ["ubekreftet_punkt_uavklart"]);
});

test("en leser får ikke «lukkes av sveipen» på det den kan lukke", () => {
  // 132s CodeRabbit-funn: betingelsen står på `kan_lukkes`, ikke på
  // skrivescopet.
  const tab = funntabell(FUNN, null);
  const merker = [...tab.querySelectorAll("span")]
    .filter((x) => x.textContent === t("ui.mote.lukkes_av_sveipen"));
  assert.equal(merker.length, 1,
    "leseren fikk teksten på et funn et menneske kan lukke");
});

test("en aksjon over frist står som varsel", () => {
  const tab = aksjonstabell(AKSJONER, null);
  assert.ok([...tab.querySelectorAll("strong[role='alert']")]
    .some((x) => x.textContent.includes("21")));
  // …OG EIEREN STÅR. En aksjon uten eier er en aksjon ingen gjør.
  assert.ok(tab.textContent.includes("u-per"));
});

test("en leser uten skrivescope får ingen skjemaer", async () => {
  const h = nyHoved();
  await visMote(h, ctx(["security:read"]));
  await vent(() => h.textContent.includes(t("ui.mote.moter")));
  assert.equal(h.querySelector("#mote-referatfrist"), null);
  assert.equal(h.querySelector("#mote-grunnlagstype"), null);
});

test("kravskjemaet forhåndsutfylles med ALLE tre grensene", async () => {
  const h = nyHoved();
  await visMote(h, ctx());
  await vent(() => h.querySelector("#mote-referatfrist"));
  assert.equal(h.querySelector("#mote-referatfrist").value, "3");
  assert.equal(h.querySelector("#mote-aksjonsfrist").value, "7");
  assert.equal(h.querySelector("#mote-terskel").value, "7000");
});

test("flaten er ren for axe", async () => {
  const h = nyHoved();
  await visMote(h, ctx());
  await vent(() => h.querySelector("#mote-referatfrist"));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("den tomme flaten er ren for axe", async () => {
  const h = nyHoved();
  SVAR["/v1/mote"] = TOMT;
  await visMote(h, ctx());
  await vent(() => h.textContent.includes(t("ui.mote.moter_tomt")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});
