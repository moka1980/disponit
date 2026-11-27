// M-24 leverandørflaten (105) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd, på hver
//     av skjermene flaten kan stå i (liste, tomt register, detaljpanel).
//   * `belop_i_flyttall`: beløp OG promille formateres i
//     HELTALLSARITMETIKK, og kroner→øre går gjennom `Math.round`.
//   * `modulen_utforte_betaling`: flaten har INGEN betalingskontroll,
//     og kilden bærer ingen betalingsvei.
//   * `modulen_beregnet_ny_pris`: `prisavvik` er et AVVIK mellom to
//     målte tall, ikke et forslag. M-26 foreslår.
//   * BRUDD-DOMMEN KOMMER FRA BASEN. Flaten har ingen retningstabell —
//     en andre tabell å holde i takt ville gjort et feil fortegn STILLE.
//   * BRUDD OG UTLØP ER TEKST, ikke bare farge (WCAG 1.4.1).
//   * SAMMENDRAGET TELLER ALT, og avkortingen sies høyt.
//   * En lesende økt ser avtalene, men INGEN mutasjonskontroller.
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
  belopTekst, bruddTekst, promilleTilFelt, prosentTekst, tilOre,
  tilPromille, utlopTekst, visLeverandor,
} from "../static/js/flater/leverandor.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));

const A1 = "11111111-1111-1111-1111-111111111111";
const A2 = "22222222-2222-2222-2222-222222222222";
const A3 = "33333333-3333-3333-3333-333333333333";
const L1 = "aaaaaaaa-1111-1111-1111-111111111111";

const BILDE = {
  sammendrag: {
    aktive_avtaler: 42, leverandorer: 11, apne_funn: 7,
    avtaler_med_brudd: 4, avtalt_ore: 125000000, har_terskel: true,
    terskelversjon: 2, vist: 3,
  },
  slaoversikt: [
    { sla_type: "leveringstid_dogn", avtaler: 3, malinger: 20, brudd: 2 },
    { sla_type: "responstid_timer", avtaler: 0, malinger: 0, brudd: 0 },
    { sla_type: "feilrate_promille", avtaler: 1, malinger: 4, brudd: 0 },
    { sla_type: "oppetid_promille", avtaler: 5, malinger: 60, brudd: 9 },
  ],
  avtaler: [
    { avtale_id: A1, leverandor_id: L1, leverandor_navn: "Nordisk Drift AS",
      leverandor_aktiv: true, ytelse: "Drift av server",
      sla_type: "oppetid_promille", avtalt_verdi: 995,
      avtalt_pris_ore: 250000, gyldig_fra: "2026-05-01",
      gyldig_til: "2026-09-20", status: "aktiv", malinger: 5, brudd: 2,
      siste_levert: "2026-08-20", siste_faktisk_verdi: 985,
      siste_faktisk_pris_ore: 300000, prisavvik_promille: 200,
      dogn_til_utlop: 18,
      apne_funn: ["pris_over_terskel", "sla_brudd"] },
    { avtale_id: A2, leverandor_id: L1, leverandor_navn: "Fjord Support AS",
      leverandor_aktiv: true, ytelse: "Brukerstøtte",
      sla_type: "responstid_timer", avtalt_verdi: 4,
      avtalt_pris_ore: 100000, gyldig_fra: "2026-01-01",
      gyldig_til: "2026-12-31", status: "aktiv", malinger: 0, brudd: 0,
      siste_levert: null, siste_faktisk_verdi: null,
      siste_faktisk_pris_ore: null, prisavvik_promille: null,
      dogn_til_utlop: 120, apne_funn: ["avtale_uten_maling"] },
    { avtale_id: A3, leverandor_id: L1, leverandor_navn: "Berg Lisens AS",
      leverandor_aktiv: false, ytelse: "Lisenser",
      sla_type: "leveringstid_dogn", avtalt_verdi: 3,
      avtalt_pris_ore: 50000, gyldig_fra: "2025-01-01",
      gyldig_til: "2026-06-30", status: "avsluttet", malinger: 12,
      brudd: 1, siste_levert: "2026-06-20", siste_faktisk_verdi: 2,
      siste_faktisk_pris_ore: 50000, prisavvik_promille: 0,
      dogn_til_utlop: -64, apne_funn: [] },
  ],
  leverandorer: [
    { leverandor_id: L1, navn: "Nordisk Drift AS", ekstern_ref: "999",
      aktiv: true, aktive_avtaler: 2 },
    { leverandor_id: "aaaaaaaa-2222-2222-2222-222222222222",
      navn: "Gammel AS", ekstern_ref: null, aktiv: false,
      aktive_avtaler: 0 },
  ],
  terskler: {
    prisstigning_promille: 125, sla_brudd_grense: 2,
    avtale_varsel_dogn: 30, maling_stillhet_dogn: 90, versjon: 2,
    oppdatert: "2026-08-01T09:00:00+00:00", oppdatert_av: "kari",
  },
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    aktive_avtaler: 0, leverandorer: 0, apne_funn: 0,
    avtaler_med_brudd: 0, avtalt_ore: 0, har_terskel: false,
    terskelversjon: null, vist: 0,
  },
  slaoversikt: [
    { sla_type: "leveringstid_dogn", avtaler: 0, malinger: 0, brudd: 0 },
    { sla_type: "responstid_timer", avtaler: 0, malinger: 0, brudd: 0 },
    { sla_type: "feilrate_promille", avtaler: 0, malinger: 0, brudd: 0 },
    { sla_type: "oppetid_promille", avtaler: 0, malinger: 0, brudd: 0 },
  ],
  avtaler: [], leverandorer: [], terskler: null, request_id: "r-b",
};

const LEVERANSER = {
  avtale_id: A1,
  leveranser: [
    { leveranse_id: "v-1", levert: "2026-08-20", faktisk_verdi: 985,
      faktisk_pris_ore: 300000, referanse: "faktura 8812", brudd: true,
      registrert: "2026-08-21T08:00:00+00:00",
      registrert_av: "kari@example.test" },
    { leveranse_id: "v-2", levert: "2026-07-20", faktisk_verdi: 999,
      faktisk_pris_ore: 250000, referanse: null, brudd: false,
      registrert: "2026-07-21T08:00:00+00:00",
      registrert_av: "kari@example.test" },
  ],
  request_id: "r-c",
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
        json: async () => ({ feil: "leverandor_ulovlig_tilstand" }) };
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
  return m;
}

function fullSvar() {
  return {
    "/v1/leverandor": BILDE,
    [`/v1/leverandor/${A1}/leveranser`]: LEVERANSER,
  };
}

// ---------------------------------------------------------------------
// belop_i_flyttall
// ---------------------------------------------------------------------

test("Leverandør: beløp formateres i heltallsaritmetikk", () => {
  assert.equal(belopTekst(0), "0,00");
  assert.equal(belopTekst(5), "0,05");
  assert.equal(belopTekst(250000), "2500,00");
  assert.equal(belopTekst(-4250), "-42,50");
  // DEN SOM AVSLØRER `/100`: 123456799 øre er 1234567,99 kroner. Et
  // flyttall gir «1234567,9899999999» her.
  assert.equal(belopTekst(123456799), "1234567,99");
  assert.equal(belopTekst(12.5), "—");
  assert.equal(belopTekst(null), "—");
});

test("Leverandør: promille formateres i heltallsaritmetikk", () => {
  // DEN SOM AVSLØRER `promille / 10`: 200 promille er 20,0 % — men et
  // prisavvik mot en leverandør tåler ikke et tall som nesten stemmer,
  // og divisjonen er unødvendig når basen alt gir et heltall.
  assert.equal(prosentTekst(200), "20,0 %");
  assert.equal(prosentTekst(125), "12,5 %");
  assert.equal(prosentTekst(0), "0,0 %");
  assert.equal(prosentTekst(-35), "-3,5 %");
  assert.equal(prosentTekst(1), "0,1 %");
  // `null` ER DET ÆRLIGE SVARET når den avtalte prisen er null: «hvor
  // mange promille over null» har intet svar, og et oppdiktet tall ville
  // sett ut som en måling.
  assert.equal(prosentTekst(null), "—");
  assert.equal(prosentTekst(12.5), "—");
  // …og den andre veien, til skjemafeltet (punktum, ikke komma).
  assert.equal(promilleTilFelt(125), "12.5");
  assert.equal(promilleTilFelt(200), "20.0");
  assert.equal(promilleTilFelt(null), "");
});

test("Leverandør: kroner inn øre ut, prosent inn promille ut", () => {
  assert.equal(tilOre("2500"), 250000);
  // DEN SOM AVSLØRER `parseFloat(x) * 100` uten avrunding: 8,15 kroner
  // er 814.9999999999999 i flyttall.
  assert.equal(tilOre("8.15"), 815);
  assert.equal(tilOre("to hundre"), null);
  assert.equal(tilPromille("12.5"), 125);
  assert.equal(tilPromille("10"), 100);
  assert.equal(tilPromille("0"), 0);
  assert.equal(tilPromille("Infinity"), null);
});

// ---------------------------------------------------------------------
// Ord, ikke farge
// ---------------------------------------------------------------------

test("Leverandør: utløp og brudd som TEKST", () => {
  assert.equal(utlopTekst(18),
    t("ui.leverandor.om_dogn").replace("{dogn}", "18"));
  // ENTALL HAR SIN EGEN NØKKEL på begge språk — locale-settet har ingen
  // pluralmaskineri, og «expires in 1 days» ville stått på den raden et
  // menneske leser først.
  assert.equal(utlopTekst(1), t("ui.leverandor.om_ett_dogn"));
  assert.equal(utlopTekst(0), t("ui.leverandor.utloper_i_dag"));
  assert.equal(utlopTekst(-1), t("ui.leverandor.utlopt_ett_dogn"));
  assert.equal(utlopTekst(-64),
    t("ui.leverandor.utlopt_for").replace("{dogn}", "64"));
  assert.equal(utlopTekst(null), "—");

  assert.equal(bruddTekst({ malinger: 5, brudd: 2 }),
    t("ui.leverandor.brudd_av").replace("{brudd}", "2")
      .replace("{malinger}", "5"));
  // «0 AV 0» ER INGEN MÅLT KVALITET. En avtale ingen har målt er en
  // annen tilstand enn en avtale uten brudd, og et menneske handler
  // ulikt på de to.
  assert.equal(bruddTekst({ malinger: 0, brudd: 0 }),
    t("ui.leverandor.ingen_malinger"));
});

test("Leverandør: begge språk har entallsnøklene", () => {
  for (const sprak of ["nb", "en"]) {
    const tekster = JSON.parse(readFileSync(
      join(HER, "..", "..", "..", "..", "locales", `${sprak}.json`),
      "utf-8"));
    for (const n of ["ui.leverandor.om_ett_dogn",
                     "ui.leverandor.utlopt_ett_dogn",
                     "ui.leverandor.utloper_i_dag"]) {
      assert.ok(tekster[n], `${sprak} mangler ${n}`);
      assert.ok(!tekster[n].includes("{dogn}"),
        `${sprak}: ${n} har en tallplass — da er den ikke entallsformen`);
    }
  }
});

// ---------------------------------------------------------------------
// Retningen hører hjemme i basen
// ---------------------------------------------------------------------

test("Leverandør: flaten har ingen retningstabell", () => {
  // BRUDD-DOMMEN KOMMER FRA BASEN (`m24_bryter_sla`). En flate som
  // regnet den selv ville hatt en ANDRE retningstabell å holde i takt,
  // og et brudd regnet med feil fortegn er STILLE — det ser ut som at
  // alt er i orden.
  const kilde = readFileSync(
    join(HER, "..", "static", "js", "flater", "leverandor.js"), "utf8");
  const uten = kilde.replace(/^\s*\/\/.*$/gm, "");
  assert.ok(!/faktisk_verdi\s*[<>]/.test(uten),
    "flaten sammenligner faktisk mot avtalt — dommen er basens");
  assert.ok(!/avtalt_verdi\s*[<>]/.test(uten));
  assert.ok(!/bryterSla|erBrudd\s*\(/.test(uten));
  // …og hjelpeteksten SIER retningen, på begge språk, så den som fyller
  // ut skjemaet vet hva som blir målt.
  for (const sprak of ["nb", "en"]) {
    const tekster = JSON.parse(readFileSync(
      join(HER, "..", "..", "..", "..", "locales", `${sprak}.json`),
      "utf-8"));
    const hjelp = tekster["ui.leverandor.skjema.sla_hjelp"];
    assert.ok(/HØYERE|HIGHER/.test(hjelp), `${sprak}: mangler retning opp`);
    assert.ok(/LAVERE|LOWER/.test(hjelp), `${sprak}: mangler retning ned`);
  }
});

// ---------------------------------------------------------------------
// modulen_utforte_betaling / modulen_beregnet_ny_pris — flatens halvdel
// ---------------------------------------------------------------------

test("Leverandør: flaten har ingen betalingskontroll", () => {
  // KATALOGEN LOVER LEVERANDØRBETALING; v1 registrerer og måler.
  // Fraværet er dommen — her måles det på KILDEN. De andre halvdelene
  // står i `test_m24_leverandor.py` (AST, datamodellen, rutene, og
  // radantallet etter en sveip).
  const kilde = readFileSync(
    join(HER, "..", "static", "js", "flater", "leverandor.js"), "utf8");
  const uten = kilde.replace(/^\s*\/\/.*$/gm, "");
  for (const ord of ["betalFaktura", "utbetal", "kontonummer", "iban",
                     "/v1/betaling", "foreslattPris", "nyPris"]) {
    assert.ok(!uten.toLowerCase().includes(ord.toLowerCase()),
      `flaten bærer «${ord}» — v1 betaler ingenting og setter ingen pris`);
  }
  const api = readFileSync(
    join(HER, "..", "static", "js", "api.js"), "utf8");
  assert.ok(!/export const betalLeverandor/.test(api));
});

// ---------------------------------------------------------------------
// Skjermene — og axe på hver av dem
// ---------------------------------------------------------------------

test("Leverandør: listen tegnes med funn som tekst, axe rent",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visLeverandor(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));

    // TRE TABELLER: SLA-oversikt, avtaler, terskler — hver med
    // <caption> og th[scope="col"] (m16-formen).
    const tabeller = [...h.querySelectorAll("table")];
    assert.equal(tabeller.length, 3);
    for (const tb of tabeller) {
      assert.ok(tb.querySelector("caption"), "tabell uten caption");
      assert.ok(tb.querySelectorAll('th[scope="col"]').length >= 2);
      assert.ok(tb.closest(".tablewrap"),
        "tabellen mangler sidescrollens container");
    }

    // MERKENE ER ORD, ikke bare farge (WCAG 1.4.1).
    const rader = [...tabeller[1].querySelectorAll("tbody tr")];
    assert.equal(rader.length, 3);
    assert.ok(rader[0].textContent.includes(t("ui.leverandor.merke_brudd")));
    assert.ok(rader[0].textContent.includes(t("ui.leverandor.merke_pris")));
    assert.ok(rader[0].textContent.includes(
      t("ui.leverandor.brudd_av").replace("{brudd}", "2")
        .replace("{malinger}", "5")));
    assert.ok(rader[0].textContent.includes("20,0 %"));
    // EN AVTALE UTEN MÅLINGER SIER DET MED ORD, og prisavviket er «—»:
    // det finnes ingen målt pris å avvike fra.
    assert.ok(rader[1].textContent.includes(
      t("ui.leverandor.ingen_malinger")));
    assert.ok(rader[1].textContent.includes(
      t("ui.leverandor.merke_umalt")));
    assert.ok(rader[1].textContent.includes("—"));
    // UTLØPT: entallsfri, og med sitt eget fortegn.
    assert.ok(rader[2].textContent.includes(
      t("ui.leverandor.utlopt_for").replace("{dogn}", "64")));
    // LEVERANDØREN NAVNGIR RADEN.
    assert.equal(rader[0].querySelector('th[scope="row"]').textContent,
      "Nordisk Drift AS");

    // SLA-OVERSIKTEN TEGNER ALLE FIRE TYPENE, også den ubrukte.
    assert.equal(tabeller[0].querySelectorAll("tbody tr").length, 4);
    assert.ok(tabeller[0].textContent.includes(
      t("ui.leverandor.sla.responstid_timer")));
  });

test("Leverandør: sammendraget teller ALT, og avkortingen sies høyt",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visLeverandor(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);
    const tekst = h.textContent;
    // TALLENE ER SAMMENDRAGETS, ikke listens: 42 aktive avtaler, mens
    // tabellen viser tre. En flate som telte radene ville sagt «3
    // avtaler» om en virksomhet som har 42.
    assert.ok(tekst.includes("42"), "sammendraget teller listen, ikke alt");
    assert.ok(tekst.includes("1250000,00"));
    assert.ok(tekst.includes(
      t("ui.leverandor.avkortet").replace("{vist}", "3")),
      "avkortingen sies ikke høyt");
    assert.ok(!tekst.includes(t("ui.leverandor.ingen_terskler")));
    // TERSKLENE VISES SOM TENANTENS TALL, med sin versjon.
    assert.ok(tekst.includes("12,5 %"));
    assert.ok(tekst.includes(
      t("ui.leverandor.terskel.versjon").replace("{versjon}", "2")));
  });

test("Leverandør: tomt register sier hva som mangler, axe rent",
  async () => {
    SVAR = { "/v1/leverandor": TOMT };
    const h = nyHoved();
    visLeverandor(h, ctx());
    await vent(() => h.textContent.includes(t("ui.leverandor.liste.ingen")));

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));

    // UTEN TERSKLER VET INGEN HVA «FOR DYRT» BETYR — som en setning,
    // ikke som en tom tabell lenger nede.
    assert.ok(h.textContent.includes(t("ui.leverandor.ingen_terskler")));
    // …men SLA-oversikten tegnes likevel, med alle typene på null.
    assert.equal(
      h.querySelectorAll("table")[0].querySelectorAll("tbody tr").length, 4);
    // INGEN LEVERANDØR, INGEN AVTALE: en setning er ærligere enn et tomt
    // nedtrekk som ser ut som en feil.
    assert.ok(h.textContent.includes(t("ui.leverandor.skjema.ingen_part")));
    assert.equal(h.querySelector("#lv-avt-part"), null);
  });

test("Leverandør: detaljpanelet viser målingene med brudd-dommen",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visLeverandor(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);

    const rader = [...h.querySelectorAll("table")[1]
      .querySelectorAll("tbody tr")];
    rader[0].querySelector("button").click();
    await vent(() => h.querySelectorAll("li").length >= 2);

    assert.deepEqual(KALL.filter((k) => k.sti.includes("leveranser")),
      [{ sti: `/v1/leverandor/${A1}/leveranser`, metode: "GET" }]);

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));

    const punkter = [...h.querySelectorAll("li")].map((n) => n.textContent);
    // BRUDD-DOMMEN KOMMER FRA BASEN og står som ORD på linjen.
    assert.ok(punkter[0].includes(t("ui.leverandor.detalj.brudd")));
    assert.ok(punkter[0].includes("3000,00"));
    assert.ok(punkter[0].includes("faktura 8812"));
    assert.ok(punkter[1].includes(t("ui.leverandor.detalj.innenfor")));
    // MERKELINJEN NAVNGIR AVTALEN så handlingene nedenfor ikke er anonyme.
    assert.ok(h.textContent.includes("Nordisk Drift AS · Drift av server"));
  });

test("Leverandør: en avsluttet avtale tar ikke imot noe", async () => {
  // KNAPPENE DEAKTIVERES i stedet for å love noe serveren avviser med
  // 409. En avsluttet avtale som tilbød «Registrer måling» ville vært en
  // måling brukeren trodde hun kunne gjøre.
  SVAR = { ...fullSvar(),
    [`/v1/leverandor/${A3}/leveranser`]: { leveranser: [],
                                           request_id: "r-d" } };
  const h = nyHoved();
  visLeverandor(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 3);
  const rader = [...h.querySelectorAll("table")[1]
    .querySelectorAll("tbody tr")];

  // BARE DETALJPANELETS TO KNAPPER. Skjemaene utenfor panelet (ny
  // leverandør, ny avtale, terskler) skal være levende uansett hvilken
  // avtale som er åpnet — en port som tok dem med ville målt feil skjema.
  const knapper = () => ["#lv-mal-dato", "#lv-slutt-grunn"]
    .map((id) => h.querySelector(id).closest("form")
      .querySelector("button[type=submit]"));

  rader[0].querySelector("button").click();
  await vent(() => h.querySelectorAll("li").length >= 2);
  assert.equal(knapper().length, 2);
  assert.ok(knapper().every((k) => !k.disabled));

  rader[2].querySelector("button").click();
  await vent(() => h.textContent.includes(t("ui.leverandor.detalj.ingen")));
  assert.ok(knapper().every((k) => k.disabled),
    "en avsluttet avtale tilbyr fortsatt en måling");
});

// ---------------------------------------------------------------------
// Skriveveiene
// ---------------------------------------------------------------------

test("Leverandør: målingen sendes i ØRE med én nøkkel", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visLeverandor(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 3);
  [...h.querySelectorAll("table")[1].querySelectorAll("tbody tr")][0]
    .querySelector("button").click();
  await vent(() => h.querySelectorAll("li").length >= 2);

  h.querySelector("#lv-mal-dato").value = "2026-08-25";
  h.querySelector("#lv-mal-verdi").value = "993";
  h.querySelector("#lv-mal-pris").value = "8.15";
  h.querySelector("#lv-mal-ref").value = "faktura 9001";
  h.querySelector("#lv-mal-dato").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("leveranse"));

  assert.equal(SISTE.sti, `/v1/leverandor/${A1}/leveranse`);
  // KRONER INN, ØRE UT — og ikke 814.
  assert.equal(SISTE.kropp.faktisk_pris_ore, 815);
  assert.equal(SISTE.kropp.faktisk_verdi, 993);
  assert.equal(SISTE.kropp.levert, "2026-08-25");
  assert.ok(SISTE.headers["Idempotency-Key"]);
});

test("Leverandør: kvitteringen og panelet overlever tegningen",
  async () => {
    // PORTEN FINNES FORDI DET VAR GALT: suksessmeldingen ble satt i
    // skjemaets eget `utfall`, og `last()` bygde straks både panelet og
    // skjemaet på nytt. Brukeren trykket «Registrer måling», så skjermen
    // blinke, og satt igjen uten å vite om det gikk bra — og uten
    // panelet, så neste måling krevde at hun fant fram til raden igjen.
    //
    // MUTASJONEN SOM DREPER DENNE: flytt kvitteringen tilbake inn i
    // `kropp`, eller fjern gjenåpningen etter tegningen.
    SVAR = fullSvar();
    const h = nyHoved();
    visLeverandor(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);
    [...h.querySelectorAll("table")[1].querySelectorAll("tbody tr")][0]
      .querySelector("button").click();
    await vent(() => h.querySelectorAll("li").length >= 2);

    h.querySelector("#lv-mal-dato").value = "2026-08-25";
    h.querySelector("#lv-mal-verdi").value = "993";
    h.querySelector("#lv-mal-pris").value = "12";
    h.querySelector("#lv-mal-dato").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    // Panelet hentes på nytt etter tegningen — det andre kallet mot
    // `leveranser` ER gjenåpningen.
    await vent(() => KALL.filter(
      (k) => k.sti.includes("/leveranser")).length >= 2);
    await vent(() => h.querySelectorAll("li").length >= 2);

    assert.ok(h.textContent.includes(t("ui.leverandor.skjema.maling_ok")),
      "kvitteringen forsvant i tegningen");
    assert.ok(h.textContent.includes("Nordisk Drift AS · Drift av server"),
      "panelet lukket seg etter en måling");

    // …OG EN AXE-PASSERING PÅ AKKURAT DEN SKJERMEN. Kvitteringslinjen er
    // ny tekst rett under overskriften, og den skal ikke ha brutt noe.
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Leverandør: en avslutning lukker panelet, men ikke kvitteringen",
  async () => {
    // Å GJENÅPNE PANELET på en avtale som nettopp ble avsluttet ville
    // tilbudt to knapper som begge er døde.
    SVAR = fullSvar();
    const h = nyHoved();
    visLeverandor(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);
    [...h.querySelectorAll("table")[1].querySelectorAll("tbody tr")][0]
      .querySelector("button").click();
    await vent(() => h.querySelectorAll("li").length >= 2);

    const grunn = h.querySelector("#lv-slutt-grunn");
    grunn.value = "byttet leverandør";
    grunn.closest("form").dispatchEvent(
      new window.Event("submit", { cancelable: true }));
    await vent(() => h.textContent.includes(
      t("ui.leverandor.skjema.avslutt_ok")));
    await vent(() => h.querySelectorAll("table").length >= 3);
    assert.equal(h.querySelectorAll("li").length, 0,
      "panelet står åpent på en avtale som nettopp ble avsluttet");
    assert.ok(h.textContent.includes(
      t("ui.leverandor.skjema.avslutt_ok")));
  });

test("Leverandør: tersklene sendes i PROMILLE", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visLeverandor(h, ctx());
  await vent(() => !!h.querySelector("#lv-t-pris"));

  // SKJEMAET ER FORHÅNDSUTFYLT MED TENANTENS EGNE TALL, ikke med
  // modulens standardverdier: et skjema som viste 10,0 % der tenanten
  // hadde satt 12,5 % ville stilltiende endret grensen ved neste lagring.
  assert.equal(h.querySelector("#lv-t-pris").value, "12.5");
  assert.equal(h.querySelector("#lv-t-grense").value, "2");

  h.querySelector("#lv-t-pris").value = "7.5";
  h.querySelector("#lv-t-pris").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("terskler"));
  assert.equal(SISTE.kropp.prisstigning_promille, 75);
  assert.equal(SISTE.kropp.sla_brudd_grense, 2);
  assert.equal(SISTE.kropp.maling_stillhet_dogn, 90);
});

test("Leverandør: en 409 sier hva registeret nektet", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visLeverandor(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 3);
  [...h.querySelectorAll("table")[1].querySelectorAll("tbody tr")][0]
    .querySelector("button").click();
  await vent(() => h.querySelectorAll("li").length >= 2);

  SVARSTATUS = 409;
  const grunn = h.querySelector("#lv-slutt-grunn");
  grunn.value = "byttet leverandør";
  grunn.closest("form").dispatchEvent(
    new window.Event("submit", { cancelable: true }));
  await vent(() => h.textContent.includes(t("ui.leverandor.feil.tilstand")));
  // EN TILSTANDSFEIL ER IKKE EN GENERELL FEIL. «Prøv igjen» ville vært
  // løgn: det samme forsøket blir avvist hver gang.
  assert.ok(!h.textContent.includes(t("ui.leverandor.feil.generell")));
  assert.ok(h.querySelector('[role="alert"]'));
  // …og KNAPPEN ER LEVENDE IGJEN, ellers ville skjermen vært død.
  assert.equal(
    grunn.closest("form").querySelector("button[type=submit]").disabled,
    false);
});

test("Leverandør: avtaleskjemaet tilbyr bare AKTIVE leverandører",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visLeverandor(h, ctx());
    await vent(() => !!h.querySelector("#lv-avt-part"));
    const valg = [...h.querySelectorAll("#lv-avt-part option")]
      .map((o) => o.textContent);
    // En deaktivert leverandør er en vi ikke lenger kjøper fra. Å tilby
    // den i nedtrekket ville invitert til en avtale ingen ville hatt.
    assert.deepEqual(valg, ["Nordisk Drift AS"]);
    // …og alle fire SLA-typene står i sitt nedtrekk, i basens rekkefølge.
    const typer = [...h.querySelectorAll("#lv-avt-sla option")]
      .map((o) => o.value);
    assert.deepEqual(typer, ["leveringstid_dogn", "responstid_timer",
                             "feilrate_promille", "oppetid_promille"]);
  });

// ---------------------------------------------------------------------
// Scope og språk
// ---------------------------------------------------------------------

test("Leverandør: en lesende økt ser tallene, men ingen kontroller",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visLeverandor(h, ctx(["okonomi:read"]));
    await vent(() => h.querySelectorAll("table").length >= 3);

    assert.ok(h.textContent.includes("Nordisk Drift AS"));
    // INGEN SKJEMAER I DET HELE TATT — verken ny leverandør, ny avtale,
    // terskler eller de to handlingene i detaljpanelet.
    assert.equal(h.querySelectorAll("form").length, 0);
    assert.equal(h.querySelectorAll("input, select, textarea").length, 0);
    // …men «Åpne» står igjen: målingene er en LESEVEI.
    assert.ok([...h.querySelectorAll("button")].some(
      (b) => b.textContent === t("ui.leverandor.knapp.apne")));

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Leverandør: ingen hardkodet tekst", async () => {
  const PL = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visLeverandor(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);
    // `th[scope="row"]` er UTELATT i avtaletabellen: den cellen bærer
    // leverandørnavnet, altså tenantens data — ikke en oversatt etikett.
    for (const node of h.querySelectorAll(
      'h2, h3, h4, label, caption, th[scope="col"], button')) {
      const s = node.textContent.trim();
      if (!s) continue;
      assert.ok(s.startsWith("PL_"), `hardkodet tekst: «${s}»`);
    }
  } finally {
    settI18nForTest(NB, "nb");
  }
});
