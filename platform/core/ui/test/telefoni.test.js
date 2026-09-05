// M-43 telefoniflaten (135) — flateporten (jsdom + axe).
//
// PORTENE MÅLER KLYNGENS DOM, IKKE BARE AT SKJERMEN TEGNES.
//
//   En ytring avgitt i husets navn kan ikke tas tilbake — OG DEN SOM
//   LESER DEN VET IKKE AT EN MASKIN SKREV DEN.
//
// HER ER DEN BOKSTAVELIG: den andre parten HØRER en stemme. Derfor
// måler portene her:
//
//   * at SEKUNDENE TIL IDENTIFIKASJON står i samme rad som samtalen,
//     med fristen ved siden av — et tall alene sier ingenting.
//   * at ORDLYDEN agenten brukte står under tallet. «Agenten
//     identifiserte seg» er en påstand; teksten er en måling.
//   * at en identifikasjon som kom for sent er et VARSEL.
//   * at en ubekreftet linje står som varsel med tallet OG terskelen
//     som gjaldt da.
//   * at en eskalering vises med REGELEN som bar den.
//   * at panelene NEKTER før døra gjør det: uten gyldig hjemmel, uten
//     gjeldende regel.
//   * at det ikke finnes en «gi rabatt»- eller «bekreft avtale»-knapp.
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
  dato, eskaleringstabell, funntabell, hjemmeltabell,
  identifikasjonstekst, prosent, regeltabell, sammendrag,
  samtaletabell, transkripsjonstabell, visTelefoni,
} from "../static/js/flater/telefoni.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));
const ROT = join(HER, "..", "..", "..", "..");

const S1 = "aaaaaaaa-1111-1111-1111-111111111111";
const S2 = "aaaaaaaa-2222-2222-2222-222222222222";
const H1 = "bbbbbbbb-1111-1111-1111-111111111111";
const H2 = "bbbbbbbb-2222-2222-2222-222222222222";
const R1 = "cccccccc-1111-1111-1111-111111111111";
const R2 = "cccccccc-2222-2222-2222-222222222222";
const E1 = "dddddddd-1111-1111-1111-111111111111";
const E2 = "dddddddd-2222-2222-2222-222222222222";
const F1 = "eeeeeeee-1111-1111-1111-111111111111";
const F2 = "eeeeeeee-2222-2222-2222-222222222222";

// TO LINJER: én sikker, én maskinen var usikker på.
const LINJER = [
  { linje_id: "ffffffff-1111-1111-1111-111111111111", rekkefolge: 1,
    taler: "agent", linje_ts: "2026-09-01T09:00:04+00:00",
    tekst: "Hei, du snakker med en automatisk assistent",
    kilde: "manuell", sikkerhet_bp: 10000, terskel_bp: 7000,
    ubekreftet: false, retter_linje_id: null, er_rettet: false,
    registrert: "2026-09-01T09:00:04+00:00", registrert_av: "m43" },
  { linje_id: "ffffffff-2222-2222-2222-222222222222", rekkefolge: 2,
    taler: "motpart", linje_ts: "2026-09-01T09:00:20+00:00",
    tekst: "Jeg lurer paa fakturaen fra i fjor",
    kilde: "transkripsjon", sikkerhet_bp: 4000, terskel_bp: 7000,
    ubekreftet: true, retter_linje_id: null, er_rettet: false,
    registrert: "2026-09-01T09:00:20+00:00", registrert_av: "m43" },
];

const SAMTALER = [
  // DENNE SA HVA DEN ER I TIDE.
  { samtale_id: S1, retning: "inngaaende", motpart: "+4790000000",
    startet_ts: "2026-09-01T09:00:00+00:00",
    slutt_ts: "2026-09-01T09:04:00+00:00",
    sekunder_til_identifikasjon: 4,
    identifikasjonstekst: "Hei, du snakker med en automatisk assistent",
    antall_linjer: 2, antall_ubekreftede: 1,
    antall_apne_eskaleringer: 1, har_opptak: true,
    opptakshjemmel: "berettiget_interesse" },
  // DENNE BRUKTE 45 SEKUNDER. Fristen er 10.
  { samtale_id: S2, retning: "utgaaende", motpart: "+4790000001",
    startet_ts: "2026-09-02T10:00:00+00:00", slutt_ts: null,
    sekunder_til_identifikasjon: 45,
    identifikasjonstekst: "Forresten, jeg er en robot",
    antall_linjer: 0, antall_ubekreftede: 0,
    antall_apne_eskaleringer: 0, har_opptak: false,
    opptakshjemmel: null },
];

const HJEMLER = [
  { hjemmel_id: H1, grunnlagstype: "berettiget_interesse",
    beskrivelse: "Opptak av kundesamtaler etter vedtak 12/24",
    formal: "kvalitetssikring", gyldig_fra: "2026-01-01",
    gyldig_til: null, gjelder: true, antall_opptak: 3 },
  // EN UTLØPT HJEMMEL SER NØYAKTIG UT SOM EN GYLDIG.
  { hjemmel_id: H2, grunnlagstype: "samtykke",
    beskrivelse: "Opptak i pilotperioden, med samtykke",
    formal: "opplaering", gyldig_fra: "2025-01-01",
    gyldig_til: "2025-12-31", gjelder: false, antall_opptak: 12 },
];

const REGLER = [
  { regel_id: R1, beskrivelse: "Sinte kunder gaar til vakthavende",
    mottaker: "vakt@acme", gyldig_fra: "2026-01-01",
    gyldig_til: null, gjelder: true, antall_eskaleringer: 2 },
  { regel_id: R2, beskrivelse: "Gamle regler for pilotperioden",
    mottaker: "pilot@acme", gyldig_fra: "2025-01-01",
    gyldig_til: "2025-12-31", gjelder: false,
    antall_eskaleringer: 7 },
];

const ESKALERINGER = [
  { eskalering_id: E1, samtale_id: S1, regel_id: R1,
    regeltekst: "Sinte kunder gaar til vakthavende",
    mottaker: "vakt@acme", begrunnelse: "kunden var opprevet",
    eskalert_ts: "2026-09-01T09:03:00+00:00", eskalert_av: "m43",
    lukket_ts: null, lukket_av: null, lukket_utfall: null,
    dogn_apen: 9 },
  { eskalering_id: E2, samtale_id: S1, regel_id: R1,
    regeltekst: "Sinte kunder gaar til vakthavende",
    mottaker: "vakt@acme", begrunnelse: "ba om leder",
    eskalert_ts: "2026-08-01T09:00:00+00:00", eskalert_av: "m43",
    lukket_ts: "2026-08-01T11:00:00+00:00", lukket_av: "u-kari",
    lukket_utfall: "haandtert", dogn_apen: null },
];

const FUNN = [
  // SVEIPENS EGET — ingen kan lukke det.
  { funn_id: F1, funntype: "eskalering_over_frist", referanse: E1,
    detaljer: "eskaleringen har staatt aapen i 9 doegn",
    over_grense: 9, apen: true,
    forst_sett: "2026-09-04T13:05:00+00:00",
    sist_sett: "2026-09-10T13:05:00+00:00", lukket_av: null,
    kan_lukkes: false },
  // UBEKREFTET LINJE — et menneske KAN avklare den.
  { funn_id: F2, funntype: "ubekreftet_linje_uavklart",
    referanse: "ffffffff-2222-2222-2222-222222222222",
    detaljer: "hoert med 4000 basispunkters sikkerhet",
    over_grense: 4000, apen: true,
    forst_sett: "2026-09-02T13:05:00+00:00",
    sist_sett: "2026-09-10T13:05:00+00:00", lukket_av: null,
    kan_lukkes: true },
];

const BILDE = {
  request_id: "r-x",
  sammendrag: {
    samtaler: 2, apne_samtaler: 1, linjer: 2, ubekreftede: 1,
    opptak: 1, hjemler: 2, gyldige_hjemler: 1, regler: 2,
    gjeldende_regler: 1, eskaleringer: 2, apne_eskaleringer: 1,
    apne_funn: 2, tregeste_identifikasjon_sek: 45, har_krav: true,
    sikkerhetsterskel_bp: 7000, identifikasjonsfrist_sek: 10,
    eskaleringsfrist_dogn: 3, samtaletak_timer: 24, kravversjon: 1,
  },
  samtaler: SAMTALER, hjemler: HJEMLER, regler: REGLER,
  eskaleringer: ESKALERINGER, funn: FUNN,
};

const TOMT = {
  request_id: "r-t",
  sammendrag: {
    samtaler: 0, apne_samtaler: 0, linjer: 0, ubekreftede: 0,
    opptak: 0, hjemler: 0, gyldige_hjemler: 0, regler: 0,
    gjeldende_regler: 0, eskaleringer: 0, apne_eskaleringer: 0,
    apne_funn: 0, tregeste_identifikasjon_sek: null, har_krav: false,
    sikkerhetsterskel_bp: null, identifikasjonsfrist_sek: null,
    eskaleringsfrist_dogn: null, samtaletak_timer: null,
    kravversjon: null,
  },
  samtaler: [], hjemler: [], regler: [], eskaleringer: [], funn: [],
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
        json: async () => ({ feil: "telefoni_ulovlig_tilstand" }) };
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
  SVAR = {
    "/v1/telefoni": BILDE,
    [`/v1/telefoni/samtale/${S1}/transkripsjon`]: {
      request_id: "r-t", samtale_id: S1, linjer: LINJER },
  };
  SISTE = null;
  SVARSTATUS = 200;
  return m;
}


// =====================================================================
// IDENTIFIKASJONEN SKAL SES.
// =====================================================================

test("sekundene står med fristen ved siden av", () => {
  // Et tall alene sier ingenting: fire sekunder er raskt for én
  // tenant og for sent for en annen.
  const tekst = identifikasjonstekst(4, 10);
  assert.ok(tekst.includes("4"));
  assert.ok(tekst.includes("10"));
  assert.equal(identifikasjonstekst(null, 10), "–");
});

test("en for sen identifikasjon er et varsel, en rask er det ikke", () => {
  const tab = samtaletabell(SAMTALER, 10, () => {});
  const varsler = [...tab.querySelectorAll("strong[role='alert']")]
    .map((x) => x.textContent);
  // Ett varsel for 45-sekunderen, ett for den ubekreftede linjen.
  assert.ok(varsler.some((x) => x.includes("45")));
  assert.ok(!varsler.some((x) => x.includes("etter 4 sek")));
});

test("ordlyden agenten brukte står under tallet", () => {
  // «Agenten identifiserte seg» er en påstand; teksten er en måling.
  const tab = samtaletabell(SAMTALER, 10, () => {});
  assert.ok(tab.textContent
    .includes("Hei, du snakker med en automatisk assistent"));
  assert.ok(tab.textContent.includes("Forresten, jeg er en robot"));
});

test("sammendraget setter den tregeste identifikasjonen først", () => {
  const p = sammendrag(BILDE.sammendrag);
  const sterke = [...p.querySelectorAll("strong")];
  assert.equal(sterke[0].getAttribute("role"), "alert");
  assert.ok(sterke[0].textContent.includes("45"));
  assert.ok(sterke[0].textContent.includes("10"));
});

test("en identifikasjon innenfor fristen løfter ingen varsel", () => {
  const p = sammendrag({ ...BILDE.sammendrag,
                         tregeste_identifikasjon_sek: 4,
                         apne_eskaleringer: 0 });
  assert.equal(p.querySelectorAll("strong[role='alert']").length, 0);
});


// =====================================================================
// TRANSKRIPSJONEN.
// =====================================================================

test("en ubekreftet linje står som varsel, en bekreftet gjør ikke", () => {
  const tab = transkripsjonstabell(LINJER);
  const varsler = [...tab.querySelectorAll("strong[role='alert']")];
  assert.equal(varsler.length, 1, "bekreftet og ubekreftet tegnes likt");
  assert.equal(varsler[0].textContent, t("ui.telefoni.ubekreftet"));
});

test("terskelen som gjaldt DA står sammen med tallet", () => {
  // Uten den kan «hvorfor er dette merket?» ikke besvares etter at
  // grensen er justert.
  const tab = transkripsjonstabell(LINJER);
  assert.ok(tab.textContent.includes(
    t("ui.telefoni.sikkerhet_verdi")
      .replace("{n}", "40 %").replace("{terskel}", "70 %")));
});

test("taleren står i samme rad som linjen", () => {
  const tab = transkripsjonstabell(LINJER);
  const rader = [...tab.querySelectorAll("tbody tr")];
  assert.ok([...rader[0].querySelectorAll("td")]
    .map((c) => c.textContent).includes(t("ui.telefoni.taler_agent")));
  assert.ok([...rader[1].querySelectorAll("td")]
    .map((c) => c.textContent).includes(t("ui.telefoni.taler_motpart")));
});

test("prosent avrunder bare i visningen", () => {
  assert.equal(prosent(7000), "70 %");
  assert.equal(prosent(4050), "41 %");
  assert.equal(prosent(null), "–");
});

test("dato viser dato og klokkeslett", () => {
  assert.equal(dato("2026-09-01T09:00:04+00:00"), "2026-09-01 09:00");
  assert.equal(dato(null), "–");
});


// =====================================================================
// ESKALERINGEN, REGELEN OG HJEMMELEN.
// =====================================================================

test("en eskalering vises med regelen som bar den", () => {
  // En eskalering uten en regel å peke på er modulens egen beslutning.
  const tab = eskaleringstabell(ESKALERINGER, 3, () => {});
  assert.ok(tab.textContent
    .includes("Sinte kunder gaar til vakthavende"));
  assert.ok(tab.textContent.includes("vakt@acme"));
});

test("en eskalering over frist er et varsel, en lukket er det ikke", () => {
  const tab = eskaleringstabell(ESKALERINGER, 3, () => {});
  const varsler = [...tab.querySelectorAll("strong[role='alert']")];
  assert.equal(varsler.length, 1);
  assert.ok(varsler[0].textContent.includes("9"));
  // …OG DEN LUKKEDE BÆRER UTFALLET OG NAVNET.
  assert.ok(tab.textContent.includes(t("ui.telefoni.utfall_haandtert")));
  assert.ok(tab.textContent.includes("u-kari"));
});

test("bare en åpen eskalering kan lukkes", () => {
  const kalt = [];
  const tab = eskaleringstabell(ESKALERINGER, 3,
                                (e) => kalt.push(e.eskalering_id));
  const knapper = [...tab.querySelectorAll("button")];
  assert.equal(knapper.length, 1);
  knapper[0].click();
  assert.deepEqual(kalt, [E1]);
});

test("bare en gjeldende regel kan avvikles", () => {
  const kalt = [];
  const tab = regeltabell(REGLER, (r) => kalt.push(r.regel_id));
  const knapper = [...tab.querySelectorAll("button")];
  assert.equal(knapper.length, 1);
  knapper[0].click();
  assert.deepEqual(kalt, [R1]);
});

test("en utløpt hjemmel merkes som varsel", () => {
  const tab = hjemmeltabell(HJEMLER, null);
  const varsler = [...tab.querySelectorAll("strong[role='alert']")]
    .map((x) => x.textContent);
  assert.deepEqual(varsler, [t("ui.telefoni.utlopt")]);
});

test("et opptak vises med sitt grunnlag, et uten sier det", () => {
  const tab = samtaletabell(SAMTALER, 10, () => {});
  assert.ok(tab.textContent
    .includes(t("ui.telefoni.grunnlag_berettiget")));
  assert.ok(tab.textContent.includes(t("ui.telefoni.intet_opptak")));
});


// =====================================================================
// PANELENE NEKTER FØR DØRA GJØR DET.
// =====================================================================

test("opptakspanelet nekter uten en gyldig hjemmel", async () => {
  const h = nyHoved();
  SVAR["/v1/telefoni"] = { ...BILDE, hjemler: [HJEMLER[1]] };
  await visTelefoni(h, ctx());
  await vent(() => h.textContent.includes("+4790000000"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.telefoni.opptak_for")
      .replace("{motpart}", "+4790000000"))
    .click();
  await vent(() => h.textContent
    .includes(t("ui.telefoni.ingen_gyldig_hjemmel")));
  assert.equal(h.querySelector("#telefoni-hjemmelvalg"), null);
});

test("eskaleringspanelet nekter uten en gjeldende regel", async () => {
  const h = nyHoved();
  SVAR["/v1/telefoni"] = { ...BILDE, regler: [REGLER[1]] };
  await visTelefoni(h, ctx());
  await vent(() => h.textContent.includes("+4790000000"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.telefoni.eskaler_for")
      .replace("{motpart}", "+4790000000"))
    .click();
  await vent(() => h.textContent
    .includes(t("ui.telefoni.ingen_gjeldende_regel")));
  assert.equal(h.querySelector("#telefoni-regelvalg"), null);
});

test("opptaksskjemaet ber om varslingen før opptakets start", async () => {
  // REKKEFØLGEN I SKJEMAET ER REKKEFØLGEN I REGELEN.
  const h = nyHoved();
  await visTelefoni(h, ctx());
  await vent(() => h.textContent.includes("+4790000000"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.telefoni.opptak_for")
      .replace("{motpart}", "+4790000000"))
    .click();
  await vent(() => h.querySelector("#telefoni-varslet"));
  const felter = [...h.querySelectorAll(
    "#telefoni-varslet, #telefoni-opptakstart")].map((x) => x.id);
  assert.deepEqual(felter, ["telefoni-varslet", "telefoni-opptakstart"]);
});

test("samtaleskjemaet ber om starten før identifikasjonen", async () => {
  const h = nyHoved();
  await visTelefoni(h, ctx());
  await vent(() => h.querySelector("#telefoni-identifisert"));
  const felter = [...h.querySelectorAll(
    "#telefoni-startet, #telefoni-identifisert, #telefoni-identtekst")]
    .map((x) => x.id);
  assert.deepEqual(felter, ["telefoni-startet", "telefoni-identifisert",
                            "telefoni-identtekst"]);
  // …OG FRISTEN STÅR I FORKLARINGEN.
  assert.ok(h.textContent.includes(
    t("ui.telefoni.samtale_forklaring").replace("{n}", "10")));
});

test("en tom ordlyd sendes ikke", async () => {
  const h = nyHoved();
  await visTelefoni(h, ctx());
  await vent(() => h.querySelector("#telefoni-identtekst"));
  h.querySelector("#telefoni-motpart").value = "+4790000002";
  h.querySelector("#telefoni-startet").value = "2026-09-01T09:00";
  h.querySelector("#telefoni-identifisert").value = "2026-09-01T09:00";
  // Ordlyden står tom.
  SISTE = null;
  h.querySelector("#telefoni-identtekst").form.dispatchEvent(
    new window.Event("submit", { cancelable: true }));
  await vent(() => h.textContent.includes(t("ui.telefoni.feil.generell")));
  assert.equal(SISTE, null, "en samtale uten ordlyd ble sendt");
});

test("den manuelle linjen sendes med full sikkerhet", async () => {
  // ET MENNESKE SOM SKREV SELV, HØRTE IKKE FEIL.
  const h = nyHoved();
  await visTelefoni(h, ctx());
  await vent(() => h.textContent.includes("+4790000000"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.telefoni.linje_for")
      .replace("{motpart}", "+4790000000"))
    .click();
  await vent(() => h.querySelector("#telefoni-linjetekst"));
  h.querySelector("#telefoni-linjenaar").value = "2026-09-01T09:01";
  h.querySelector("#telefoni-linjetekst").value = "Jeg tar over herfra";
  h.querySelector("#telefoni-linjetekst").form.dispatchEvent(
    new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE !== null);
  assert.equal(SISTE.kropp.kilde, "manuell");
  assert.equal(SISTE.kropp.sikkerhet_bp, 10000);
});


// =====================================================================
// V1-DOMMEN OG FUNNENE.
// =====================================================================

test("kilden har ingen vei som inngår avtale eller lover penger", () => {
  // KOMMENTARER **OG STRENGER** FJERNES FØRST (128s lærdom).
  const kilde = readFileSync(join(ROT, "platform", "core", "ui",
    "static", "js", "flater", "telefoni.js"), "utf8");
  const uten = kilde.split("\n")
    .filter((l) => !l.trim().startsWith("//")).join("\n")
    .replace(/"[^"\n]*"|'[^'\n]*'|`[^`]*`/g, '""');
  for (const forbudt of ["girabatt", "gi_rabatt", "bekreftavtale",
                         "inngaaavtale", "aksepter_tilbud"]) {
    assert.ok(!uten.toLowerCase().includes(forbudt), forbudt);
  }
});

test("funntabellen leser kan_lukkes fra basen", () => {
  const kalt = [];
  const tab = funntabell(FUNN, (f) => kalt.push(f.funntype));
  const knapper = [...tab.querySelectorAll("button")];
  assert.equal(knapper.length, 1);
  knapper[0].click();
  assert.deepEqual(kalt, ["ubekreftet_linje_uavklart"]);
});

test("en leser får ikke «lukkes av sveipen» på det den kan lukke", () => {
  const tab = funntabell(FUNN, null);
  const merker = [...tab.querySelectorAll("span")]
    .filter((x) => x.textContent === t("ui.telefoni.lukkes_av_sveipen"));
  assert.equal(merker.length, 1);
});

test("en leser uten skrivescope får ingen skjemaer", async () => {
  const h = nyHoved();
  await visTelefoni(h, ctx(["security:read"]));
  await vent(() => h.textContent.includes(t("ui.telefoni.samtaler")));
  assert.equal(h.querySelector("#telefoni-terskel"), null);
  assert.equal(h.querySelector("#telefoni-motpart"), null);
});

test("kravskjemaet forhåndsutfylles med ALLE fire grensene", async () => {
  const h = nyHoved();
  await visTelefoni(h, ctx());
  await vent(() => h.querySelector("#telefoni-terskel"));
  assert.equal(h.querySelector("#telefoni-terskel").value, "7000");
  assert.equal(h.querySelector("#telefoni-identfrist").value, "10");
  assert.equal(h.querySelector("#telefoni-eskfrist").value, "3");
  assert.equal(h.querySelector("#telefoni-tak").value, "24");
});

test("transkripsjonspanelet henter linjene når noen spør", async () => {
  const h = nyHoved();
  await visTelefoni(h, ctx());
  await vent(() => h.textContent.includes("+4790000000"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.telefoni.vis_transkripsjon"))
    .click();
  await vent(() => h.textContent
    .includes("Jeg lurer paa fakturaen fra i fjor"));
});

test("flaten er ren for axe", async () => {
  const h = nyHoved();
  await visTelefoni(h, ctx());
  await vent(() => h.querySelector("#telefoni-terskel"));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("den tomme flaten er ren for axe", async () => {
  const h = nyHoved();
  SVAR["/v1/telefoni"] = TOMT;
  await visTelefoni(h, ctx());
  await vent(() => h.textContent.includes(t("ui.telefoni.samtaler_tomt")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});
