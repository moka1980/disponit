// M-45 ESG-flaten (136) — flateporten (jsdom + axe).
//
// PORTENE MÅLER KLYNGENS DOM, IKKE BARE AT SKJERMEN TEGNES.
//
//   En ytring avgitt i husets navn kan ikke tas tilbake — OG DEN SOM
//   LESER DEN VET IKKE AT EN MASKIN SKREV DEN.
//
// En bærekraftsrapport leses av investorer, kunder og et tilsyn, og ET
// ESTIMAT LEST SOM EN MÅLING ER GRØNNVASKING. Derfor måler portene:
//
//   * at `er_estimat` står som et VARSEL i samme rad som tallet, med
//     grunnlaget under.
//   * at STANDARDVERSJONEN står på hver periode — en foreldet regel
//     ser nøyaktig ut som en riktig regel.
//   * at estimatandelen vises med ÉN DESIMAL: 4,7 % og 4,2 % er ikke
//     samme tall i en rapport et tilsyn leser.
//   * at TALLET IKKE BLIR EN `Number` noe sted i flaten — en `float`
//     flytter siste desimal, og da er tallet på skjermen ikke tallet i
//     rapporten.
//   * at en påstand som hviler på et estimat BÆRER DET VIDERE.
//   * at målingspanelet bare tilbyr faktorer i periodens egen
//     standardversjon.
//   * at det ikke finnes en «send rapport»-knapp.
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
  andel, dato, faktortabell, funntabell, iDagLokal, maalingstabell,
  paastandstabell, periodetabell, rapporttabell, sammendrag, tall,
  visEsg,
} from "../static/js/flater/esg.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));
const ROT = join(HER, "..", "..", "..", "..");

const P1 = "aaaaaaaa-1111-1111-1111-111111111111";
const P2 = "aaaaaaaa-2222-2222-2222-222222222222";
const F1 = "bbbbbbbb-1111-1111-1111-111111111111";
const F2 = "bbbbbbbb-2222-2222-2222-222222222222";
const M1 = "cccccccc-1111-1111-1111-111111111111";
const M2 = "cccccccc-2222-2222-2222-222222222222";
const R1 = "dddddddd-1111-1111-1111-111111111111";
const G1 = "eeeeeeee-1111-1111-1111-111111111111";
const G2 = "eeeeeeee-2222-2222-2222-222222222222";

// TO TALL: ett målt, ett gjettet.
const MAALINGER = [
  { maaling_id: M1, kategori: "elektrisitet_no", mengde: "120000.000000",
    enhet: "kWh", utslipp_kg: "2040.000000", er_estimat: false,
    estimatgrunnlag: null, faktor_verdi: "0.01700000",
    standardversjon: "2026.1", kilde_tittel: "Nettleiefaktura 2026",
    kilde_sha256: "a".repeat(64), kilde_gyldig: true, erstattet: false,
    dogn_gammelt: 12, registrert: "2026-09-01T09:00:00+00:00",
    registrert_av: "u-kari" },
  { maaling_id: M2, kategori: "transport_diesel", mengde: "5000.000000",
    enhet: "kWh", utslipp_kg: "85.000000", er_estimat: true,
    estimatgrunnlag: "anslag basert paa fjoraarets forbruk",
    faktor_verdi: "0.01700000", standardversjon: "2026.1",
    kilde_tittel: "Regnskap 2025", kilde_sha256: "b".repeat(64),
    kilde_gyldig: false, erstattet: false, dogn_gammelt: 500,
    registrert: "2025-04-01T09:00:00+00:00", registrert_av: "u-kari" },
];

const PAASTANDER = [
  { paastand_id: G1, rekkefolge: 1,
    tekst: "Vi kuttet stroemforbruket med 12 prosent",
    kilde_tittel: "Nettleiefaktura 2026", dokumenttype: "maaling",
    kilde_sha256: "a".repeat(64), kilde_gyldig: true, maaling_id: M1,
    maaling_er_estimat: false,
    registrert: "2026-09-01T10:00:00+00:00", registrert_av: "u-kari" },
  // DENNE HVILER PÅ ET ESTIMAT.
  { paastand_id: G2, rekkefolge: 2,
    tekst: "Transporten vaar er tilnaermet utslippsfri",
    kilde_tittel: "Regnskap 2025", dokumenttype: "regnskap",
    kilde_sha256: "b".repeat(64), kilde_gyldig: false, maaling_id: M2,
    maaling_er_estimat: true,
    registrert: "2026-09-01T10:05:00+00:00", registrert_av: "u-kari" },
];

const PERIODER = [
  { periode_id: P1, merke: "2026", fra: "2026-01-01", til: "2026-12-31",
    standard: "ESRS", standardversjon: "2026.1", status: "apen",
    antall_maalinger: 2, antall_estimater: 1, antall_paastander: 2,
    sum_utslipp_kg: "2125.000000", estimatandel_bp: 400,
    siste_rapportversjon: 1, antall_utlopte_kilder: 1 },
  // DENNE HVILER PÅ ALT FOR MYE GJETNING.
  { periode_id: P2, merke: "2025", fra: "2025-01-01", til: "2025-12-31",
    standard: "ESRS", standardversjon: "2025.2", status: "lukket",
    antall_maalinger: 4, antall_estimater: 3, antall_paastander: 1,
    sum_utslipp_kg: "9000.000000", estimatandel_bp: 4700,
    siste_rapportversjon: 2, antall_utlopte_kilder: 0 },
];

const FAKTORER = [
  { faktor_id: F1, kategori: "elektrisitet_no", enhet: "kWh",
    verdi: "0.01700000", standard: "ESRS", standardversjon: "2026.1",
    kilde_tittel: "DEFRA-tabell 2026", gyldig_fra: "2026-01-01",
    gyldig_til: null, gjelder: true, antall_maalinger: 2 },
  // SAMME KATEGORI, ANNEN STANDARDVERSJON.
  { faktor_id: F2, kategori: "elektrisitet_no", enhet: "kWh",
    verdi: "0.01900000", standard: "ESRS", standardversjon: "2027.1",
    kilde_tittel: "DEFRA-tabell 2027", gyldig_fra: "2027-01-01",
    gyldig_til: null, gjelder: true, antall_maalinger: 0 },
];

const RAPPORTER = [
  { rapport_id: R1, periode_id: P1, periodemerke: "2026", versjon: 1,
    innholds_hash: "c".repeat(64), sum_utslipp_kg: "2125.000000",
    antall_maalinger: 2, antall_estimater: 1, estimatandel_bp: 400,
    antall_paastander: 2, standardversjon: "2026.1",
    sammenstilt: "2026-09-02T09:00:00+00:00",
    sammenstilt_av: "u-kari" },
];

const FUNN = [
  // SVEIPENS EGET — ingen kan lukke det.
  { funn_id: "ffffffff-1111-1111-1111-111111111111",
    funntype: "estimat_ikke_erstattet_over_frist", referanse: M2,
    detaljer: "estimatet har staatt i 500 doegn", over_grense: 500,
    apen: true, forst_sett: "2026-09-01T13:20:00+00:00",
    sist_sett: "2026-09-10T13:20:00+00:00", lukket_av: null,
    kan_lukkes: false },
  // ESTIMATANDEL — et menneske KAN avklare den.
  { funn_id: "ffffffff-2222-2222-2222-222222222222",
    funntype: "estimatandel_over_terskel_uavklart", referanse: P2,
    detaljer: "4700 basispunkter av utslippet hviler paa estimat",
    over_grense: 4700, apen: true,
    forst_sett: "2026-09-01T13:20:00+00:00",
    sist_sett: "2026-09-10T13:20:00+00:00", lukket_av: null,
    kan_lukkes: true },
];

const BILDE = {
  request_id: "r-x",
  sammendrag: {
    perioder: 2, apne_perioder: 1, maalinger: 6, estimater: 4,
    paastander: 3, faktorer: 2, gjeldende_faktorer: 2, rapporter: 3,
    kilder: 4, utlopte_kilder: 1, apne_funn: 2,
    hoyeste_estimatandel_bp: 4700, har_krav: true,
    estimatterskel_bp: 2000, estimatfrist_dogn: 400,
    kilde_gyldig_dogn: 1095, kravversjon: 1,
  },
  perioder: PERIODER, faktorer: FAKTORER, rapporter: RAPPORTER,
  funn: FUNN,
};

const TOMT = {
  request_id: "r-t",
  sammendrag: {
    perioder: 0, apne_perioder: 0, maalinger: 0, estimater: 0,
    paastander: 0, faktorer: 0, gjeldende_faktorer: 0, rapporter: 0,
    kilder: 0, utlopte_kilder: 0, apne_funn: 0,
    hoyeste_estimatandel_bp: null, har_krav: false,
    estimatterskel_bp: null, estimatfrist_dogn: null,
    kilde_gyldig_dogn: null, kravversjon: null,
  },
  perioder: [], faktorer: [], rapporter: [], funn: [],
};

let SVAR;
let SISTE;
let SVARSTATUS;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  if (opts && opts.method === "POST") {
    SISTE = { sti, headers: opts.headers,
              kropp: opts.body ? JSON.parse(opts.body) : null };
    if (SVARSTATUS && SVARSTATUS !== 200) {
      return { ok: false, status: SVARSTATUS,
        json: async () => ({ feil: "esg_ulovlig_tilstand" }) };
    }
    return { ok: true, status: 200,
             json: async () => ({ ok: true, versjon: 2,
                                  estimatandel_bp: 400 }) };
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
  SVAR = {
    "/v1/esg": BILDE,
    [`/v1/esg/periode/${P1}/maalinger`]: {
      request_id: "r-m", periode_id: P1, maalinger: MAALINGER },
    [`/v1/esg/periode/${P1}/paastander`]: {
      request_id: "r-p", periode_id: P1, paastander: PAASTANDER },
  };
  SISTE = null;
  SVARSTATUS = 200;
  return m;
}


// =====================================================================
// ESTIMATET SKAL SES.
// =====================================================================

test("et estimat står som varsel, en måling gjør ikke", () => {
  const tab = maalingstabell(MAALINGER);
  const varsler = [...tab.querySelectorAll("strong[role='alert']")]
    .map((x) => x.textContent);
  assert.ok(varsler.includes(t("ui.esg.estimat")),
    "estimatet er ikke merket som et varsel");
  assert.ok(tab.textContent.includes(t("ui.esg.maalt")));
});

test("estimatgrunnlaget står under merkingen", () => {
  // Et estimat som ikke sier hva det hviler på, er et tall noen
  // gjettet.
  const tab = maalingstabell(MAALINGER);
  assert.ok(tab.textContent
    .includes("anslag basert paa fjoraarets forbruk"));
});

test("en utløpt kilde merkes i samme rad som tallet", () => {
  const tab = maalingstabell(MAALINGER);
  const varsler = [...tab.querySelectorAll("strong[role='alert']")]
    .map((x) => x.textContent);
  assert.ok(varsler.includes(t("ui.esg.kilde_utlopt")));
});

test("en påstand som hviler på et estimat bærer det videre", () => {
  // Usikkerheten forsvinner ikke fordi noen skrev en setning rundt
  // tallet.
  const tab = paastandstabell(PAASTANDER);
  const varsler = [...tab.querySelectorAll("strong[role='alert']")]
    .map((x) => x.textContent);
  assert.ok(varsler.includes(t("ui.esg.hviler_paa_estimat")));
  assert.ok(tab.textContent.includes(t("ui.esg.hviler_paa_maaling")));
});

test("sammendraget setter den høyeste estimatandelen først", () => {
  const p = sammendrag(BILDE.sammendrag);
  const sterke = [...p.querySelectorAll("strong")];
  assert.equal(sterke[0].getAttribute("role"), "alert");
  assert.ok(sterke[0].textContent.includes("47,0 %"));
  assert.ok(sterke[0].textContent.includes("20,0 %"));
});

test("en estimatandel under terskelen løfter ingen varsel", () => {
  const p = sammendrag({ ...BILDE.sammendrag,
                         hoyeste_estimatandel_bp: 400,
                         utlopte_kilder: 0 });
  assert.equal(p.querySelectorAll("strong[role='alert']").length, 0);
});


// =====================================================================
// TALLET SKAL IKKE FLYTTE SEG.
// =====================================================================

test("andelen vises med én desimal", () => {
  // 4,7 % og 4,2 % er ikke samme tall i en rapport et tilsyn leser.
  assert.equal(andel(470), "4,7 %");
  assert.equal(andel(420), "4,2 %");
  assert.notEqual(andel(470), andel(420));
  assert.equal(andel(null), "–");
});

test("tallet grupperes uten å bli et flyttall", () => {
  assert.equal(tall("120000.000000"), "120 000");
  assert.equal(tall("2040.500000"), "2 040,5");
  assert.equal(tall("0.01700000"), "0,017");
  assert.equal(tall(null), "–");
});

test("flaten gjør aldri et tall om til en Number", () => {
  // KOMMENTARER OG STRENGER FJERNES FØRST (128s lærdom). En `float`
  // flytter siste desimal, og da er tallet på skjermen ikke tallet i
  // rapporten.
  const kilde = readFileSync(join(ROT, "platform", "core", "ui",
    "static", "js", "flater", "esg.js"), "utf8");
  const uten = kilde.split("\n")
    .filter((l) => !l.trim().startsWith("//")).join("\n")
    .replace(/"[^"\n]*"|'[^'\n]*'|`[^`]*`/g, '""');
  for (const forbudt of ["Number(m.", "Number(f.", "Number(p.",
                         "parseFloat", "Number(mengde.value",
                         "Number(verdi.value"]) {
    assert.ok(!uten.includes(forbudt), forbudt);
  }
});

test("mengden sendes som tekst, med komma gjort om til punktum", async () => {
  const h = nyHoved();
  await visEsg(h, ctx());
  await vent(() => h.textContent.includes("2026"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.esg.tall_for")
      .replace("{merke}", "2026"))
    .click();
  await vent(() => h.querySelector("#esg-mengde"));
  h.querySelector("#esg-kategori").value = "elektrisitet_no";
  h.querySelector("#esg-mengde").value = "120000,5";
  h.querySelector("#esg-enhet").value = "kWh";
  h.querySelector("#esg-kildevalg").value = "en-kilde-id";
  h.querySelector("#esg-mengde").form.dispatchEvent(
    new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE !== null);
  assert.equal(SISTE.kropp.mengde, "120000.5");
  assert.equal(typeof SISTE.kropp.mengde, "string");
  assert.equal(SISTE.kropp.er_estimat, false);
});


// =====================================================================
// STANDARDVERSJONEN OG LÅSEN.
// =====================================================================

test("standardversjonen står på hver periode", () => {
  // En foreldet regel ser nøyaktig ut som en riktig regel.
  const tab = periodetabell(PERIODER, 2000, () => {});
  assert.ok(tab.textContent.includes("ESRS 2026.1"));
  assert.ok(tab.textContent.includes("ESRS 2025.2"));
});

test("en periode over estimatterskelen er et varsel", () => {
  const tab = periodetabell(PERIODER, 2000, () => {});
  const varsler = [...tab.querySelectorAll("strong[role='alert']")]
    .map((x) => x.textContent);
  assert.ok(varsler.includes("47,0 %"));
  assert.ok(!varsler.includes("4,0 %"));
});

test("målingspanelet tilbyr bare faktorer i periodens versjon", async () => {
  // Døra nekter på resten, og valget skal ikke tilby dem.
  const h = nyHoved();
  await visEsg(h, ctx());
  await vent(() => h.textContent.includes("2026"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.esg.tall_for")
      .replace("{merke}", "2026"))
    .click();
  await vent(() => h.querySelector("#esg-faktor"));
  const valg = [...h.querySelectorAll("#esg-faktor option")]
    .map((o) => o.value);
  assert.deepEqual(valg, [F1], "en faktor fra 2027.1 ble tilbudt");
});

test("målingspanelet nekter når ingen faktor har periodens versjon", async () => {
  const h = nyHoved();
  SVAR["/v1/esg"] = { ...BILDE, faktorer: [FAKTORER[1]] };
  await visEsg(h, ctx());
  await vent(() => h.textContent.includes("2026"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.esg.tall_for")
      .replace("{merke}", "2026"))
    .click();
  await vent(() => h.textContent.includes("2026.1")
    && h.textContent.includes(t("ui.esg.ingen_faktor")
      .replace("{versjon}", "2026.1")));
  assert.equal(h.querySelector("#esg-faktor"), null);
});

test("estimatgrunnlaget er avslått til noen huker av", async () => {
  const h = nyHoved();
  await visEsg(h, ctx());
  await vent(() => h.textContent.includes("2026"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.esg.tall_for")
      .replace("{merke}", "2026"))
    .click();
  await vent(() => h.querySelector("#esg-estimatgrunnlag"));
  const g = h.querySelector("#esg-estimatgrunnlag");
  assert.equal(g.disabled, true);
  const boks = h.querySelector("#esg-estimat");
  boks.checked = true;
  boks.dispatchEvent(new window.Event("change"));
  assert.equal(g.disabled, false);
});

test("et estimat uten grunnlag sendes ikke", async () => {
  const h = nyHoved();
  await visEsg(h, ctx());
  await vent(() => h.textContent.includes("2026"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.esg.tall_for")
      .replace("{merke}", "2026"))
    .click();
  await vent(() => h.querySelector("#esg-mengde"));
  h.querySelector("#esg-kategori").value = "transport_diesel";
  h.querySelector("#esg-mengde").value = "5000";
  h.querySelector("#esg-enhet").value = "kWh";
  h.querySelector("#esg-kildevalg").value = "en-kilde-id";
  const boks = h.querySelector("#esg-estimat");
  boks.checked = true;
  boks.dispatchEvent(new window.Event("change"));
  h.querySelector("#esg-estimatgrunnlag").value = "kort";
  SISTE = null;
  h.querySelector("#esg-mengde").form.dispatchEvent(
    new window.Event("submit", { cancelable: true }));
  await vent(() => h.textContent.includes(t("ui.esg.feil.generell")));
  assert.equal(SISTE, null, "et estimat uten grunnlag ble sendt");
});

test("en lukket periode tar ikke imot flere tall", async () => {
  const h = nyHoved();
  await visEsg(h, ctx());
  await vent(() => h.textContent.includes("2025"));
  // Knappen finnes bare for åpne perioder.
  const knapp = [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.esg.tall_for")
      .replace("{merke}", "2025"));
  assert.equal(knapp, undefined,
    "en lukket periode fikk en knapp for nye tall");
});


// =====================================================================
// V1-DOMMEN OG FUNNENE.
// =====================================================================

test("kilden har ingen vei som sender rapporten", () => {
  const kilde = readFileSync(join(ROT, "platform", "core", "ui",
    "static", "js", "flater", "esg.js"), "utf8");
  const uten = kilde.split("\n")
    .filter((l) => !l.trim().startsWith("//")).join("\n")
    .replace(/"[^"\n]*"|'[^'\n]*'|`[^`]*`/g, '""');
  for (const forbudt of ["sendrapport", "send_rapport", "innsend",
                         "tiltilsyn"]) {
    assert.ok(!uten.toLowerCase().includes(forbudt), forbudt);
  }
});

test("sammenstillingen sier hva den ikke er", () => {
  // «En sammenstilling er et grunnlag, ikke en innsending.»
  const tab = rapporttabell(RAPPORTER, 2000);
  assert.ok(tab.textContent.includes("2026"));
  assert.ok(tab.textContent.includes("u-kari"));
});

test("sammenstillingen melder estimatandelen høyt", async () => {
  const h = nyHoved();
  await visEsg(h, ctx());
  await vent(() => h.textContent.includes("2026"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.esg.sammenstill_for")
      .replace("{merke}", "2026"))
    .click();
  await vent(() => h.textContent.includes(
    t("ui.esg.sammenstilt_ok").replace("{versjon}", "2")
      .replace("{andel}", "4,0 %")));
});

test("funntabellen leser kan_lukkes fra basen", () => {
  const kalt = [];
  const tab = funntabell(FUNN, (f) => kalt.push(f.funntype));
  const knapper = [...tab.querySelectorAll("button")];
  assert.equal(knapper.length, 1);
  knapper[0].click();
  assert.deepEqual(kalt, ["estimatandel_over_terskel_uavklart"]);
});

test("en leser får ikke «lukkes av sveipen» på det den kan lukke", () => {
  const tab = funntabell(FUNN, null);
  const merker = [...tab.querySelectorAll("span")]
    .filter((x) => x.textContent === t("ui.esg.lukkes_av_sveipen"));
  assert.equal(merker.length, 1);
});

test("bare en gjeldende faktor kan avvikles", () => {
  const kalt = [];
  const tab = faktortabell(FAKTORER, (f) => kalt.push(f.faktor_id));
  const knapper = [...tab.querySelectorAll("button")];
  assert.equal(knapper.length, 2);
  knapper[0].click();
  assert.deepEqual(kalt, [F1]);
});

test("dagens dato regnes i brukerens sone", () => {
  // 135s lærdom, arvet: UTC ville gitt gårsdagen sent på kvelden.
  const kveld = new Date(2026, 8, 6, 0, 30);
  assert.equal(iDagLokal(kveld), "2026-09-06");
});

test("en leser uten skrivescope får ingen skjemaer", async () => {
  const h = nyHoved();
  await visEsg(h, ctx(["security:read"]));
  await vent(() => h.textContent.includes(t("ui.esg.perioder")));
  assert.equal(h.querySelector("#esg-terskel"), null);
  assert.equal(h.querySelector("#esg-merke"), null);
});

test("kravskjemaet forhåndsutfylles med ALLE tre grensene", async () => {
  const h = nyHoved();
  await visEsg(h, ctx());
  await vent(() => h.querySelector("#esg-terskel"));
  assert.equal(h.querySelector("#esg-terskel").value, "2000");
  assert.equal(h.querySelector("#esg-estimatfrist").value, "400");
  assert.equal(h.querySelector("#esg-kildedogn").value, "1095");
});

test("periodepanelet henter tallene og påstandene når noen spør", async () => {
  const h = nyHoved();
  await visEsg(h, ctx());
  await vent(() => h.textContent.includes("2026"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.esg.vis_tallene"))
    .click();
  await vent(() => h.textContent.includes("120 000"));
  assert.ok(h.textContent.includes("Vi kuttet stroemforbruket"));
  // …OG STANDARDVERSJONEN STÅR ØVERST.
  assert.ok(h.textContent.includes("ESRS 2026.1"));
});

test("dato viser bare datodelen", () => {
  assert.equal(dato("2026-09-01T09:00:00+00:00"), "2026-09-01");
  assert.equal(dato(null), "–");
});

test("flaten er ren for axe", async () => {
  const h = nyHoved();
  await visEsg(h, ctx());
  await vent(() => h.querySelector("#esg-terskel"));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("den tomme flaten er ren for axe", async () => {
  const h = nyHoved();
  SVAR["/v1/esg"] = TOMT;
  await visEsg(h, ctx());
  await vent(() => h.textContent.includes(t("ui.esg.perioder_tomt")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});
