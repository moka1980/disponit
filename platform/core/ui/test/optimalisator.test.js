// M-36 optimalisatorflaten (132) — flateporten (jsdom + axe).
//
// PORTENE MÅLER VAKTSETNINGEN, IKKE BARE AT SKJERMEN TEGNES.
//
//   «Kan aldri utvide egen fullmakt; korrelasjon presenteres ikke som
//   årsak; porteføljestopp tilgjengelig.»
//
// Den midterste er et krav til DATAMODELLEN, men flaten er der
// påstanden faktisk møter et menneske. Derfor måler portene her:
//
//   * at `grunnlagstype` står i SAMME RAD som plasseringen og tallet,
//     og at KORRELASJON merkes som varsel. Et forslag som er nummer
//     én på grunn av en samvariasjon skal ikke se ut som ett som er
//     det på grunn av et eksperiment.
//   * at BÅNDET tegnes i samme rad som punktet. En rangering av
//     punktestimater er en rekkefølge som later som den er sikker.
//   * at porteføljestoppen står ØVERST i sammendraget når den er på —
//     en modul som er slått av skal ikke se ut som en modul uten
//     forslag.
//   * at flaten sier hva stoppen GJØR, ikke hva navnet lover.
//   * at det ikke finnes en «iverksett»-knapp, og ikke kan finnes en.
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
  funntabell, kroner, modelltabell, rangeringstabell,
  rangeringstabellen, sammendrag, tiltakstabell, visOptimalisator,
} from "../static/js/flater/optimalisator.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));
const ROT = join(HER, "..", "..", "..", "..");

const R1 = "aaaaaaaa-1111-1111-1111-111111111111";
const T1 = "bbbbbbbb-1111-1111-1111-111111111111";
const T2 = "bbbbbbbb-2222-2222-2222-222222222222";
const M1 = "cccccccc-1111-1111-1111-111111111111";
const S1 = "dddddddd-1111-1111-1111-111111111111";
const F1 = "eeeeeeee-1111-1111-1111-111111111111";
const F2 = "eeeeeeee-2222-2222-2222-222222222222";

// TO POSTER: den øverste hviler på KORRELASJON og er IRREVERSIBEL —
// nettopp kombinasjonen flaten må gjøre synlig.
const POSTER = {
  request_id: "r-p", rangering_id: R1,
  poster: [
    { plass: 1, tiltak_id: T1,
      beskrivelse: "Bytt fraktleverandoer for aa spare",
      forventet_effekt_ore: 9000000, nedre_effekt_ore: 7200000,
      ovre_effekt_ore: 10800000, grunnlagstype: "korrelasjon",
      reversibilitet: "irreversibel", ukeslutt: "2026-08-30",
      faktisk_effekt_ore: 2000000, avvik_ore: 7000000,
      innenfor_intervall: false, status: "foreslatt",
      kan_maales: false },
    // Over horisonten, IKKE målt → knappen skal finnes.
    { plass: 2, tiltak_id: T2,
      beskrivelse: "Send purring tidligere i loepet",
      forventet_effekt_ore: 4500000, nedre_effekt_ore: 3600000,
      ovre_effekt_ore: 5400000, grunnlagstype: "eksperiment",
      reversibilitet: "reversibel", ukeslutt: "2026-08-30",
      faktisk_effekt_ore: null, avvik_ore: null,
      innenfor_intervall: null, status: "foreslatt",
      kan_maales: true },
  ],
};

const RANGERINGER = [
  { rangering_id: R1, laget_dato: "2026-06-01", horisont_uker: 12,
    modellversjon: "2026-01", baselinje: "ingen rangering",
    grunnlag_apne_funn: 41, grunnlag_registre: 32,
    gjelder_til: "2026-08-24", laget_av: "u-kari",
    antall_poster: 2, antall_maalt: 1 },
];

const TILTAK = [
  { tiltak_id: T1, beskrivelse: "Bytt fraktleverandoer for aa spare",
    grunnlagstype: "korrelasjon",
    grunnlag: "Fraktkost samvarierer med volum",
    reversibilitet: "irreversibel", kilde_modul: "m24_leverandor",
    kilde_funntype: "avvik_i_pris", anslag_effekt_ore: 9000000,
    status: "foreslatt", vurdert_av: null, vurderingsnotat: null,
    opprettet: "2026-06-01T09:00:00+00:00", opprettet_av: "u-kari" },
];

const MODELLER = [
  { modell_id: M1, navn: "Effektrangering", versjon: "2026-01",
    metode: "Anslag sortert synkende; reversibelt foran irreversibelt.",
    baselinje: "ingen rangering", usikkerhet_bp: 2000,
    gyldig_fra: "2026-01-01", gyldig_til: null, gjelder: true,
    antall_rangeringer: 1 },
];

const STOPP = [
  { stopp_id: S1, begrunnelse: "Vi omorganiserer i tredje kvartal",
    satt_ts: "2026-07-01T09:00:00+00:00", satt_av: "u-kari",
    opphevet_ts: null, opphevet_av: null, aktiv: true },
];

const FUNN = [
  // SVEIPENS EGET — ingen kan lukke det.
  { funn_id: F1, funntype: "korrelasjon_alene_paa_topp",
    referanse: R1,
    detaljer: "det oeverste forslaget hviler bare paa korrelasjon",
    over_grense: 0, apen: true,
    forst_sett: "2026-06-02T09:00:00+00:00",
    sist_sett: "2026-09-05T09:00:00+00:00", lukket_av: null,
    kan_lukkes: false },
  // STOPPEN — den KAN lukkes av et menneske.
  { funn_id: F2, funntype: "stopp_staar_uten_oppheving",
    referanse: S1, detaljer: "stoppen har staatt 40 doegn",
    over_grense: 26, apen: true,
    forst_sett: "2026-08-01T09:00:00+00:00",
    sist_sett: "2026-09-05T09:00:00+00:00", lukket_av: null,
    kan_lukkes: true },
];

const BILDE = {
  request_id: "r-x",
  sammendrag: {
    rangeringer: 1, modeller: 1, gyldige_modeller: 1, tiltak: 1,
    uvurderte_tiltak: 1, irreversible_uvurderte: 1, poster: 2,
    maalte: 1, umaalte: 1, treff: 0, bom: 1, apne_funn: 2,
    stopp_aktiv: true, har_krav: true, horisont_uker: 12,
    maalefrist_dogn: 14, maks_i_rangering: 10, kravversjon: 1,
    apne_funn_i_huset: 41, registre: 32,
  },
  rangeringer: RANGERINGER, tiltak: TILTAK, modeller: MODELLER,
  stopp: STOPP, funn: FUNN,
};

const TOMT = {
  request_id: "r-t",
  sammendrag: {
    rangeringer: 0, modeller: 0, gyldige_modeller: 0, tiltak: 0,
    uvurderte_tiltak: 0, irreversible_uvurderte: 0, poster: 0,
    maalte: 0, umaalte: 0, treff: 0, bom: 0, apne_funn: 0,
    stopp_aktiv: false, har_krav: false, horisont_uker: null,
    maalefrist_dogn: null, maks_i_rangering: null, kravversjon: null,
    apne_funn_i_huset: 0, registre: 32,
  },
  rangeringer: [], tiltak: [], modeller: [], stopp: [], funn: [],
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
        json: async () => ({ feil: "optimalisator_ulovlig_tilstand" }) };
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
  SVAR = { "/v1/optimalisator": BILDE,
           [`/v1/optimalisator/rangering/${R1}`]: POSTER };
  SISTE = null;
  KALL = [];
  SVARSTATUS = 200;
  return m;
}


// =====================================================================
// «KORRELASJON PRESENTERES IKKE SOM ÅRSAK».
// =====================================================================

test("rangeringen viser grunnlagstypen i samme rad som tallet", () => {
  const tab = rangeringstabell(POSTER.poster, null);
  const celler = [...[...tab.querySelectorAll("tbody tr")][0]
    .querySelectorAll("td")].map((c) => c.textContent);
  assert.ok(celler.includes("1"), "plassen mangler");
  assert.ok(celler.some((c) => c.includes("90 000")), "tallet mangler");
  assert.ok(celler.includes(t("ui.optimalisator.grunnlag_korrelasjon")),
            "grunnlagstypen mangler i samme rad");
});

test("korrelasjon merkes som varsel, eksperiment gjør det ikke", () => {
  const tab = rangeringstabell(POSTER.poster, null);
  const varsler = [...tab.querySelectorAll("strong[role='alert']")]
    .map((x) => x.textContent);
  assert.deepEqual(varsler,
    [t("ui.optimalisator.grunnlag_korrelasjon")]);
  // …og eksperimentet står som vanlig tekst.
  assert.ok(tab.textContent
    .includes(t("ui.optimalisator.grunnlag_eksperiment")));
});

test("en ukjent grunnlagstype vises som seg selv", () => {
  // Faller en ny verdi inn i det lukkede settet uten at flaten er
  // oppdatert, skal den være SYNLIG.
  const tab = rangeringstabell(
    [{ ...POSTER.poster[0], grunnlagstype: "noe_nytt" }], null);
  assert.ok(tab.textContent.includes("noe_nytt"));
});

test("båndet tegnes i samme rad som punktet", () => {
  const tab = rangeringstabell(POSTER.poster, null);
  const celler = [...[...tab.querySelectorAll("tbody tr")][0]
    .querySelectorAll("td")].map((c) => c.textContent);
  assert.ok(celler.some((c) => c.includes("72 000")
                            && c.includes("108 000")),
            "baandet star ikke i samme rad som punktet");
});


// =====================================================================
// PORTEFØLJESTOPPEN.
// =====================================================================

test("en aktiv stopp står først i sammendraget", () => {
  const p = sammendrag(BILDE.sammendrag);
  const sterke = [...p.querySelectorAll("strong")];
  // EN MODUL SOM ER SLÅTT AV SKAL IKKE SE UT SOM EN MODUL UTEN
  // FORSLAG.
  assert.equal(sterke[0].textContent, t("ui.optimalisator.stopp_paa"));
  assert.equal(sterke[0].getAttribute("role"), "alert");
});

test("uten stopp står de umålte først", () => {
  const p = sammendrag({ ...BILDE.sammendrag, stopp_aktiv: false });
  const sterke = [...p.querySelectorAll("strong")];
  assert.ok(sterke[0].textContent
    .includes(t("ui.optimalisator.umaalte_sum").split("{")[0].trim()));
});

test("flaten sier hva stoppen gjør, ikke hva navnet lover", async () => {
  const h = nyHoved();
  await visOptimalisator(h, ctx());
  await vent(() => h.querySelector("#opti-stoppgrunn"));
  // NAVNET LOVER MER ENN STOPPEN KAN HOLDE.
  assert.ok(h.textContent
    .includes(t("ui.optimalisator.stopp_forklaring")));
});

test("rangeringsskjemaet nekter når stoppen er på", async () => {
  const h = nyHoved();
  await visOptimalisator(h, ctx());
  await vent(() => h.textContent
    .includes(t("ui.optimalisator.stopp_hindrer")));
  // DØRA NEKTER, og skjemaet sier det i stedet for å la brukeren
  // finne det ut av en 400.
  assert.equal(h.querySelector("#opti-modellvalg"), null);
});

test("uten stopp finnes rangeringsskjemaet", async () => {
  const h = nyHoved();
  SVAR["/v1/optimalisator"] = {
    ...BILDE,
    sammendrag: { ...BILDE.sammendrag, stopp_aktiv: false },
    stopp: [],
  };
  await visOptimalisator(h, ctx());
  await vent(() => h.querySelector("#opti-modellvalg"));
  assert.ok(h.querySelector("#opti-modellvalg"));
});


// =====================================================================
// V1-DOMMEN: MODULEN IVERKSETTER INGENTING.
// =====================================================================

test("tiltakstabellen har ingen iverksett-knapp", () => {
  const tab = tiltakstabell(TILTAK, () => {});
  const tekster = [...tab.querySelectorAll("button")]
    .map((b) => b.textContent);
  assert.deepEqual(tekster, [t("ui.optimalisator.vurder")]);
});

test("kilden vises, slik at forslaget kan spores til en måling", () => {
  const tab = tiltakstabell(TILTAK, null);
  assert.ok(tab.textContent.includes("m24_leverandor"));
  assert.ok(tab.textContent.includes("avvik_i_pris"));
});

test("et irreversibelt tiltak står i fet skrift", () => {
  const tab = tiltakstabell(TILTAK, null);
  assert.ok([...tab.querySelectorAll("strong")]
    .some((x) => x.textContent
      === t("ui.optimalisator.rev_irreversibel")));
});

test("kilden har ingen iverksettelsesvei og ingen policyvei", () => {
  // KOMMENTARER **OG STRENGER** FJERNES FØRST (128s lærdom).
  const kilde = readFileSync(join(ROT, "platform", "core", "ui",
    "static", "js", "flater", "optimalisator.js"), "utf8");
  const uten = kilde.split("\n")
    .filter((l) => !l.trim().startsWith("//")).join("\n")
    .replace(/"[^"\n]*"|'[^'\n]*'|`[^`]*`/g, '""');
  for (const forbudt of ["iverksett", "policyutkast",
                         "policyaktivering"]) {
    assert.ok(!uten.toLowerCase().includes(forbudt), forbudt);
  }
});


// =====================================================================
// MÅLING, TALL OG TILGANG.
// =====================================================================

test("«mål effekten» finnes bare der basen sier kan_maales", () => {
  const kalt = [];
  const tab = rangeringstabell(POSTER.poster, (p) => kalt.push(p.plass));
  const knapper = [...tab.querySelectorAll("button")];
  assert.equal(knapper.length, 1);
  knapper[0].click();
  assert.deepEqual(kalt, [2]);
});

test("en umålt post viser strek, ikke null", () => {
  const tab = rangeringstabell([POSTER.poster[1]], null);
  const tekst = [...tab.querySelectorAll("tbody td")]
    .map((c) => c.textContent);
  assert.ok(tekst.includes("–"));
});

test("funntabellen leser kan_lukkes fra basen", () => {
  const kalt = [];
  const tab = funntabell(FUNN, (f) => kalt.push(f.funntype));
  const knapper = [...tab.querySelectorAll("button")];
  // BARE STOPPEN KAN LUKKES. `korrelasjon_alene_paa_topp` lukkes av
  // at toppen endrer seg — ikke av et klikk.
  assert.equal(knapper.length, 1);
  knapper[0].click();
  assert.deepEqual(kalt, ["stopp_staar_uten_oppheving"]);
});

test("rangeringslisten viser hvor bredt den så", () => {
  const tab = rangeringstabellen(RANGERINGER, () => {});
  // ET REGISTER UTEN ÅPNE FUNN ER LEST, IKKE FRAVÆRENDE.
  assert.ok(tab.textContent.includes("41"));
  assert.ok(tab.textContent.includes("32"));
});

test("modelltabellen viser metoden, basislinjen og usikkerheten", () => {
  const tab = modelltabell(MODELLER);
  assert.ok(tab.textContent.includes("Anslag sortert synkende"));
  assert.ok(tab.textContent.includes("ingen rangering"));
  assert.ok(tab.textContent.includes("2000"));
});

test("kroner formaterer øre uten flyttall", () => {
  assert.equal(kroner(9000000), "90 000,00");
  assert.equal(kroner(-4050000), "−40 500,00");
  assert.equal(kroner(1), "0,01");
  assert.equal(kroner(null), "");
});

test("en leser uten skrivescope får ingen skjemaer", async () => {
  const h = nyHoved();
  await visOptimalisator(h, ctx(["okonomi:read"]));
  await vent(() => h.textContent
    .includes(t("ui.optimalisator.rangeringer")));
  assert.equal(h.querySelector("#opti-horisont"), null);
  assert.equal(h.querySelector("#opti-stoppgrunn"), null);
});

test("et tomt måletall sendes ikke som null effekt", async () => {
  // `Number("")` er `0`, og en måling RETTES IKKE.
  const h = nyHoved();
  await visOptimalisator(h, ctx());
  await vent(() => h.textContent.includes("2026-06-01"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.optimalisator.vis_rangering"))
    .click();
  await vent(() => [...h.querySelectorAll("button")]
    .some((b) => b.textContent
      === t("ui.optimalisator.maal_effekten")));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.optimalisator.maal_effekten"))
    .click();
  await vent(() => h.querySelector("#opti-faktisk"));
  const felt = h.querySelector("#opti-faktisk");
  assert.ok(felt.hasAttribute("required"));
  felt.value = "";
  felt.form.dispatchEvent(new window.Event("submit",
                                           { cancelable: true }));
  await vent(() => h.textContent
    .includes(t("ui.optimalisator.feil.generell")));
  assert.equal(SISTE, null, "et tomt felt ble sendt som en måling");
});

test("flaten er ren for axe", async () => {
  const h = nyHoved();
  await visOptimalisator(h, ctx());
  await vent(() => h.querySelector("#opti-horisont"));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("den tomme flaten er ren for axe", async () => {
  const h = nyHoved();
  SVAR["/v1/optimalisator"] = TOMT;
  await visOptimalisator(h, ctx());
  await vent(() => h.textContent
    .includes(t("ui.optimalisator.rangeringer_tomt")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});
