// M-14 fakturaflaten (106) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd, på hver
//     av skjermene flaten kan stå i.
//   * `modulen_signerte_attestasjon` og `modulen_bokforte`: flaten har
//     INGEN «bokfør»-knapp og ingen signering. «Kontrollert» betyr at
//     noen har sett på fakturaen, ikke at penger har flyttet seg.
//   * `belop_i_flyttall`: beløp OG satser formateres i
//     HELTALLSARITMETIKK, og kroner→øre går gjennom `Math.round`.
//   * MVA-AVRUNDINGEN BOR I BASEN. Flaten regner den ikke — en andre
//     avrundingsregel å holde i takt er slik en mva-kontroll blir
//     stille gal.
//   * KONTROLLER OG ALDER ER TEKST, ikke bare farge (WCAG 1.4.1).
//   * TREFFRATEN TELLER ALT, og alle fem typene står i tabellen.
//   * En lesende økt ser fakturaene, men INGEN mutasjonskontroller.
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
  alderTekst, belopTekst, kontrollTekst, oreTilFelt, promilleTilFelt,
  satsTekst, tilOre, tilPromille, visFaktura,
} from "../static/js/flater/faktura.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));

const F1 = "11111111-1111-1111-1111-111111111111";
const F2 = "22222222-2222-2222-2222-222222222222";
const F3 = "33333333-3333-3333-3333-333333333333";

const BILDE = {
  sammendrag: {
    mottatte: 42, mottatt_ore: 125000000, kontrollerte: 30, avviste: 2,
    apne_funn: 9, ukontrollerte: 4, har_terskel: true,
    terskelversjon: 2, satser: 3, vist: 3,
  },
  treffrate: [
    { kontrolltype: "dublett", kjort: 74, avvik: 3 },
    { kontrolltype: "mva", kjort: 74, avvik: 11 },
    { kontrolltype: "leverandor", kjort: 74, avvik: 5 },
    { kontrolltype: "belopsgrense", kjort: 0, avvik: 0 },
    { kontrolltype: "manuell", kjort: 30, avvik: 2 },
  ],
  fakturaer: [
    { faktura_id: F1, leverandor_ref: "Nordisk Drift AS",
      fakturanummer: "F-1001", netto_ore: 10000, mva_ore: 2600,
      brutto_ore: 12600, sats_kode: "hoy", valuta: "NOK",
      utstedt: "2026-08-01", forfall: "2026-08-31",
      mottatt: "2026-08-02", status: "mottatt", dogn_siden_mottatt: 31,
      kontroller: 3, avvik: 2,
      apne_funn: ["mva_avvik", "ukontrollert"] },
    { faktura_id: F2, leverandor_ref: "Fjord Support AS",
      fakturanummer: "F-1002", netto_ore: 100000, mva_ore: 25000,
      brutto_ore: 125000, sats_kode: "hoy", valuta: "NOK",
      utstedt: "2026-08-20", forfall: "2026-09-20",
      mottatt: "2026-09-01", status: "mottatt", dogn_siden_mottatt: 1,
      kontroller: 3, avvik: 0, apne_funn: [] },
    { faktura_id: F3, leverandor_ref: "Berg Lisens AS",
      fakturanummer: "F-0999", netto_ore: 5000, mva_ore: 1250,
      brutto_ore: 6250, sats_kode: "hoy", valuta: "NOK",
      utstedt: "2026-06-01", forfall: "2026-06-30",
      mottatt: "2026-06-02", status: "kontrollert",
      dogn_siden_mottatt: 92, kontroller: 4, avvik: 0, apne_funn: [] },
  ],
  satser: [
    { sats_kode: "hoy", promille: 250, gyldig_fra: "2020-01-01",
      gyldig_til: null, gjelder_i_dag: true },
    { sats_kode: "lav", promille: 120, gyldig_fra: "2026-07-01",
      gyldig_til: null, gjelder_i_dag: true },
    { sats_kode: "lav", promille: 150, gyldig_fra: "2020-01-01",
      gyldig_til: "2026-06-30", gjelder_i_dag: false },
  ],
  terskler: {
    mva_slingring_ore: 1, belopsgrense_ore: 2500000,
    kontrollfrist_dogn: 7, dublettvindu_dogn: 3, versjon: 2,
    oppdatert: "2026-08-01T09:00:00+00:00", oppdatert_av: "kari",
  },
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    mottatte: 0, mottatt_ore: 0, kontrollerte: 0, avviste: 0,
    apne_funn: 0, ukontrollerte: 0, har_terskel: false,
    terskelversjon: null, satser: 0, vist: 0,
  },
  treffrate: [
    { kontrolltype: "dublett", kjort: 0, avvik: 0 },
    { kontrolltype: "mva", kjort: 0, avvik: 0 },
    { kontrolltype: "leverandor", kjort: 0, avvik: 0 },
    { kontrolltype: "belopsgrense", kjort: 0, avvik: 0 },
    { kontrolltype: "manuell", kjort: 0, avvik: 0 },
  ],
  fakturaer: [], satser: [], terskler: null, request_id: "r-b",
};

const KONTROLLER = {
  faktura_id: F1,
  kontroller: [
    { kontroll_id: "k-1", kontrolltype: "mva", utfall: "avvik",
      avvik_ore: 100, notat: null,
      kjort: "2026-08-02T08:00:00+00:00", kjort_av: "import" },
    { kontroll_id: "k-2", kontrolltype: "dublett", utfall: "ok",
      avvik_ore: null, notat: null,
      kjort: "2026-08-02T08:00:00+00:00", kjort_av: "import" },
    { kontroll_id: "k-3", kontrolltype: "leverandor", utfall: "ok",
      avvik_ore: null, notat: null,
      kjort: "2026-08-02T08:00:00+00:00", kjort_av: "import" },
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
        json: async () => ({ feil: "faktura_ulovlig_tilstand" }) };
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
    "/v1/faktura": BILDE,
    [`/v1/faktura/${F1}/kontroller`]: KONTROLLER,
  };
}

// ---------------------------------------------------------------------
// belop_i_flyttall
// ---------------------------------------------------------------------

test("Faktura: beløp og satser i heltallsaritmetikk", () => {
  assert.equal(belopTekst(0), "0,00");
  assert.equal(belopTekst(5), "0,05");
  assert.equal(belopTekst(12600), "126,00");
  // DEN SOM AVSLØRER `/100`: 123456799 øre er 1234567,99 kroner.
  assert.equal(belopTekst(123456799), "1234567,99");
  assert.equal(belopTekst(12.5), "—");
  assert.equal(belopTekst(null), "—");

  // DEN SOM AVSLØRER `promille / 10`: 250 er 25,0 %.
  assert.equal(satsTekst(250), "25,0 %");
  assert.equal(satsTekst(125), "12,5 %");
  assert.equal(satsTekst(0), "0,0 %");
  assert.equal(satsTekst(null), "—");

  assert.equal(tilOre("2500"), 250000);
  // DEN SOM AVSLØRER `parseFloat(x) * 100` uten avrunding: 8,15 kroner
  // er 814.9999999999999 i flyttall.
  assert.equal(tilOre("8.15"), 815);
  assert.equal(tilOre("to hundre"), null);
  assert.equal(tilPromille("12.5"), 125);
  assert.equal(tilPromille("25"), 250);
  // …og de to veiene TILBAKE til skjemafeltene, uten divisjon.
  assert.equal(oreTilFelt(2500000), "25000.00");
  assert.equal(oreTilFelt(1), "0.01");
  assert.equal(oreTilFelt(null), "");
  assert.equal(promilleTilFelt(125), "12.5");
});

test("Faktura: flaten regner ingen mva", () => {
  // MVA-AVRUNDINGEN BOR I BASEN: `(netto * promille + 500) / 1000`,
  // halv-opp. En flate som regnet den selv ville hatt en ANDRE
  // avrundingsregel å holde i takt — og et flyttall der ville gitt
  // 2499.9999999999995 øre på 99,99 kroner netto, altså et avvik på
  // hver eneste faktura.
  const kilde = readFileSync(
    join(HER, "..", "static", "js", "flater", "faktura.js"), "utf8");
  const uten = kilde.replace(/^\s*\/\/.*$/gm, "");
  assert.ok(!/netto_ore\s*\*/.test(uten),
    "flaten multipliserer netto — mva-regningen er basens");
  assert.ok(!/forventetMva|beregnMva/.test(uten));
  assert.ok(!/promille\s*\/\s*1000/.test(uten));
});

// ---------------------------------------------------------------------
// Ord, ikke farge
// ---------------------------------------------------------------------

test("Faktura: alder og kontroller som TEKST", () => {
  assert.equal(alderTekst(31),
    t("ui.faktura.mottatt_for").replace("{dogn}", "31"));
  // ENTALL HAR SIN EGEN NØKKEL på begge språk.
  assert.equal(alderTekst(1), t("ui.faktura.mottatt_i_gaar"));
  assert.equal(alderTekst(0), t("ui.faktura.mottatt_i_dag"));
  assert.equal(alderTekst(null), "—");

  assert.equal(kontrollTekst({ kontroller: 3, avvik: 2 }),
    t("ui.faktura.avvik_av").replace("{avvik}", "2")
      .replace("{kontroller}", "3"));
  // «0 AV 0» ER INGEN MÅLT KONTROLL. En faktura ingen har kontrollert
  // er en annen tilstand enn en uten avvik.
  assert.equal(kontrollTekst({ kontroller: 0, avvik: 0 }),
    t("ui.faktura.ingen_kontroller"));
});

test("Faktura: begge språk har entallsnøklene", () => {
  for (const sprak of ["nb", "en"]) {
    const tekster = JSON.parse(readFileSync(
      join(HER, "..", "..", "..", "..", "locales", `${sprak}.json`),
      "utf-8"));
    for (const n of ["ui.faktura.mottatt_i_dag",
                     "ui.faktura.mottatt_i_gaar"]) {
      assert.ok(tekster[n], `${sprak} mangler ${n}`);
      assert.ok(!tekster[n].includes("{dogn}"),
        `${sprak}: ${n} har en tallplass — da er den ikke entallsformen`);
    }
  }
});

// ---------------------------------------------------------------------
// modulen_bokforte / modulen_signerte_attestasjon — flatens halvdel
// ---------------------------------------------------------------------

test("Faktura: flaten har ingen bokføringsknapp og ingen signering",
  () => {
    // POLICYEN VI SENDER UT navngir modulen som `v_regnskap`, betrodd
    // for `faktura_godkjent` — og bruker den attestasjonen til å la
    // `faktura.bokfor` gå automatisk. Fraværet er dommen, og her måles
    // det på KILDEN. De andre halvdelene står i `test_m14_faktura.py`.
    const kilde = readFileSync(
      join(HER, "..", "static", "js", "flater", "faktura.js"), "utf8");
    const uten = kilde.replace(/^\s*\/\/.*$/gm, "");
    for (const ord of ["bokfor", "attester", "signer", "hovedbok",
                       "kontoplan", "godkjennFaktura"]) {
      assert.ok(!uten.toLowerCase().includes(ord.toLowerCase()),
        `flaten bærer «${ord}» — v1 bokfører og attesterer ingenting`);
    }
    const api = readFileSync(
      join(HER, "..", "static", "js", "api.js"), "utf8");
    assert.ok(!/export const bokforFaktura/.test(api));
  });

// ---------------------------------------------------------------------
// Skjermene — og axe på hver av dem
// ---------------------------------------------------------------------

test("Faktura: listen tegnes med funn som tekst, axe rent", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visFaktura(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 4);

  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));

  // FIRE TABELLER: treffrate, fakturaer, satser, terskler.
  const tabeller = [...h.querySelectorAll("table")];
  assert.equal(tabeller.length, 4);
  for (const tb of tabeller) {
    assert.ok(tb.querySelector("caption"), "tabell uten caption");
    assert.ok(tb.querySelectorAll('th[scope="col"]').length >= 2);
    assert.ok(tb.closest(".tablewrap"),
      "tabellen mangler sidescrollens container");
  }

  // TREFFRATEN TEGNER ALLE FEM TYPENE, også den ubrukte.
  assert.equal(tabeller[0].querySelectorAll("tbody tr").length, 5);
  assert.ok(tabeller[0].textContent.includes(
    t("ui.faktura.kontrolltype.belopsgrense")));

  // MERKENE ER ORD (WCAG 1.4.1).
  const rader = [...tabeller[1].querySelectorAll("tbody tr")];
  assert.equal(rader.length, 3);
  assert.ok(rader[0].textContent.includes(t("ui.faktura.merke_mva")));
  assert.ok(rader[0].textContent.includes(
    t("ui.faktura.merke_ukontrollert")));
  assert.ok(rader[0].textContent.includes(
    t("ui.faktura.avvik_av").replace("{avvik}", "2")
      .replace("{kontroller}", "3")));
  // MVA-EN STÅR VED SIDEN AV BRUTTO, så et menneske ser et avvik uten
  // å regne.
  assert.ok(rader[0].textContent.includes("126,00"));
  assert.ok(rader[0].textContent.includes("26,00"));
  // LEVERANDØREN NAVNGIR RADEN.
  assert.equal(rader[0].querySelector('th[scope="row"]').textContent,
    "Nordisk Drift AS");

  // SATSTABELLEN SIER «gjelder fortsatt» MED ORD; en tom celle ville
  // sett ut som manglende data.
  assert.ok(tabeller[2].textContent.includes(
    t("ui.faktura.sats.apen").replace("{fra}", "2020-01-01")));
  assert.ok(tabeller[2].textContent.includes("25,0 %"));
  assert.ok(tabeller[2].textContent.includes(
    t("ui.faktura.sats.historisk")));
});

test("Faktura: sammendraget teller ALT, og avkortingen sies høyt",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visFaktura(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 4);
    const tekst = h.textContent;
    // TALLENE ER SAMMENDRAGETS, ikke listens.
    assert.ok(tekst.includes("42"));
    assert.ok(tekst.includes("1250000,00"));
    assert.ok(tekst.includes(
      t("ui.faktura.avkortet").replace("{vist}", "3")));
    assert.ok(!tekst.includes(t("ui.faktura.ingen_terskler")));
    assert.ok(!tekst.includes(t("ui.faktura.ingen_satser")));
    // TREFFRATEN SIER HVORFOR DEN FINNES.
    assert.ok(tekst.includes(t("ui.faktura.treffrate.hvorfor")));
  });

test("Faktura: tomt register sier hva som mangler, axe rent", async () => {
  SVAR = { "/v1/faktura": TOMT };
  const h = nyHoved();
  visFaktura(h, ctx());
  await vent(() => h.textContent.includes(t("ui.faktura.liste.ingen")));

  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));

  assert.ok(h.textContent.includes(t("ui.faktura.ingen_terskler")));
  assert.ok(h.textContent.includes(t("ui.faktura.ingen_satser")));
  // INGEN GJELDENDE SATS, INGEN FAKTURA: en setning er ærligere enn et
  // tomt nedtrekk som ser ut som en feil.
  assert.ok(h.textContent.includes(t("ui.faktura.skjema.ingen_sats")));
  assert.equal(h.querySelector("#fa-ny-sats"), null);
  // …men treffratetabellen tegnes likevel, med alle typene på null.
  assert.equal(
    h.querySelectorAll("table")[0].querySelectorAll("tbody tr").length, 5);
});

test("Faktura: detaljpanelet viser kontrollene med avviket i kroner",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visFaktura(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 4);
    [...h.querySelectorAll("table")[1].querySelectorAll("tbody tr")][0]
      .querySelector("button").click();
    await vent(() => h.querySelectorAll("li").length >= 3);

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));

    const punkter = [...h.querySelectorAll("li")].map((n) => n.textContent);
    assert.ok(punkter[0].includes(t("ui.faktura.kontrolltype.mva")));
    assert.ok(punkter[0].includes(t("ui.faktura.utfall.avvik")));
    // AVVIKET STÅR I KRONER når det er et beløp.
    assert.ok(punkter[0].includes("1,00"));
    // …og IKKE når typen ikke har et tall: en «0,00» der ville vært en
    // oppdiktet måling.
    assert.ok(!punkter[1].includes("0,00"));
    assert.ok(punkter[1].includes(t("ui.faktura.utfall.ok")));
    assert.ok(h.textContent.includes("Nordisk Drift AS · F-1001"));
  });

test("Faktura: en avgjort faktura tar ikke imot noe", async () => {
  SVAR = { ...fullSvar(),
    [`/v1/faktura/${F3}/kontroller`]: { kontroller: [],
                                        request_id: "r-d" } };
  const h = nyHoved();
  visFaktura(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 4);
  const rader = [...h.querySelectorAll("table")[1]
    .querySelectorAll("tbody tr")];
  // BARE DETALJPANELETS TO KNAPPER — skjemaene utenfor panelet skal
  // være levende uansett hvilken faktura som er åpnet.
  const knapper = () => ["#fa-k-notat", "#fa-a-grunn"]
    .map((id) => h.querySelector(id).closest("form")
      .querySelector("button[type=submit]"));

  rader[0].querySelector("button").click();
  await vent(() => h.querySelectorAll("li").length >= 3);
  assert.ok(knapper().every((k) => !k.disabled));

  rader[2].querySelector("button").click();
  await vent(() => h.textContent.includes(t("ui.faktura.detalj.ingen")));
  assert.ok(knapper().every((k) => k.disabled),
    "en avgjort faktura tilbyr fortsatt en kontroll");
});

// ---------------------------------------------------------------------
// Skriveveiene
// ---------------------------------------------------------------------

test("Faktura: avgjørelsen har to utfall, og «bokført» er ikke ett",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visFaktura(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 4);
    [...h.querySelectorAll("table")[1].querySelectorAll("tbody tr")][0]
      .querySelector("button").click();
    await vent(() => h.querySelectorAll("li").length >= 3);
    const valg = [...h.querySelectorAll("#fa-a-status option")]
      .map((o) => o.value);
    // FRAVÆRET ER DOMMEN.
    assert.deepEqual(valg, ["kontrollert", "avvist"]);
  });

test("Faktura: fakturaen sendes i ØRE med én nøkkel", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visFaktura(h, ctx());
  await vent(() => !!h.querySelector("#fa-ny-netto"));
  h.querySelector("#fa-ny-ref").value = "Ny Leverandør AS";
  h.querySelector("#fa-ny-nummer").value = "N-1";
  h.querySelector("#fa-ny-netto").value = "8.15";
  h.querySelector("#fa-ny-mva").value = "2.04";
  h.querySelector("#fa-ny-brutto").value = "10.19";
  h.querySelector("#fa-ny-utstedt").value = "2026-09-01";
  h.querySelector("#fa-ny-forfall").value = "2026-10-01";
  h.querySelector("#fa-ny-mottatt").value = "2026-09-02";
  h.querySelector("#fa-ny-netto").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE !== undefined);
  assert.equal(SISTE.sti, "/v1/faktura");
  // KRONER INN, ØRE UT — og ikke 814.
  assert.equal(SISTE.kropp.netto_ore, 815);
  assert.equal(SISTE.kropp.mva_ore, 204);
  assert.equal(SISTE.kropp.brutto_ore, 1019);
  // …og BARE GJELDENDE SATSER i nedtrekket: en historisk sats ville
  // invitert til en kontroll mot noe som ikke lenger er sant.
  const satser = [...h.querySelectorAll("#fa-ny-sats option")]
    .map((o) => o.value);
  assert.deepEqual(satser, ["hoy", "lav"]);
  assert.ok(SISTE.headers["Idempotency-Key"]);
});

test("Faktura: satsen sendes i PROMILLE", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visFaktura(h, ctx());
  await vent(() => !!h.querySelector("#fa-s-prosent"));
  h.querySelector("#fa-s-kode").value = "matvarer";
  h.querySelector("#fa-s-prosent").value = "12.5";
  h.querySelector("#fa-s-fra").value = "2026-09-01";
  h.querySelector("#fa-s-kode").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("mvasats"));
  assert.equal(SISTE.kropp.promille, 125);
  // ÅPEN ENDE SENDES SOM `null`, ikke som tom streng: den gjeldende
  // satsen har ingen sluttdato.
  assert.equal(SISTE.kropp.gyldig_til, null);
});

test("Faktura: tersklene er forhåndsutfylt med tenantens egne tall",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visFaktura(h, ctx());
    await vent(() => !!h.querySelector("#fa-t-grense"));
    // Et skjema som viste modulens standardverdier der tenanten hadde
    // satt sine egne ville stilltiende endret grensen ved neste lagring.
    assert.equal(h.querySelector("#fa-t-slingring").value, "0.01");
    assert.equal(h.querySelector("#fa-t-grense").value, "25000.00");
    assert.equal(h.querySelector("#fa-t-frist").value, "7");
    h.querySelector("#fa-t-grense").value = "50000";
    h.querySelector("#fa-t-grense").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE && SISTE.sti.includes("terskler"));
    assert.equal(SISTE.kropp.belopsgrense_ore, 5000000);
    assert.equal(SISTE.kropp.mva_slingring_ore, 1);
  });

test("Faktura: kvitteringen og panelet overlever tegningen", async () => {
  // Klynge 3-rettingen, holdt fast i den nye modulen.
  SVAR = fullSvar();
  const h = nyHoved();
  visFaktura(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 4);
  [...h.querySelectorAll("table")[1].querySelectorAll("tbody tr")][0]
    .querySelector("button").click();
  await vent(() => h.querySelectorAll("li").length >= 3);
  h.querySelector("#fa-k-notat").value = "sammenholdt med bestillingen";
  h.querySelector("#fa-k-notat").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.ok(await vent(() => KALL.filter(
    (k) => k.sti.includes("/kontroller")).length >= 2),
    "panelet ble aldri gjenåpnet — porten måler ingenting");
  assert.ok(h.textContent.includes(t("ui.faktura.skjema.kontroll_ok")),
    "kvitteringen forsvant i tegningen");
  assert.ok(h.textContent.includes("Nordisk Drift AS · F-1001"),
    "panelet lukket seg etter en kontroll");
});

test("Faktura: en 409 sier hva registeret nektet", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visFaktura(h, ctx());
  await vent(() => !!h.querySelector("#fa-ny-netto"));
  SVARSTATUS = 409;
  h.querySelector("#fa-ny-ref").value = "X";
  h.querySelector("#fa-ny-nummer").value = "N-9";
  h.querySelector("#fa-ny-netto").value = "1";
  h.querySelector("#fa-ny-mva").value = "0";
  h.querySelector("#fa-ny-brutto").value = "1";
  h.querySelector("#fa-ny-utstedt").value = "2026-09-01";
  h.querySelector("#fa-ny-forfall").value = "2026-10-01";
  h.querySelector("#fa-ny-mottatt").value = "2026-09-02";
  const skjema = h.querySelector("#fa-ny-netto").closest("form");
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.ok(await vent(() => h.textContent.includes(
    t("ui.faktura.feil.tilstand"))), "tilstandsfeilen ble ikke vist");
  // EN TILSTANDSFEIL ER IKKE EN GENERELL FEIL. «Prøv igjen» ville vært
  // løgn: det samme forsøket blir avvist hver gang.
  assert.ok(!h.textContent.includes(t("ui.faktura.feil.generell")));
  assert.ok(h.querySelector('[role="alert"]'));
  assert.equal(skjema.querySelector("button[type=submit]").disabled, false);
});

// ---------------------------------------------------------------------
// Scope og språk
// ---------------------------------------------------------------------

test("Faktura: en lesende økt ser tallene, men ingen kontroller",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visFaktura(h, ctx(["okonomi:read"]));
    await vent(() => h.querySelectorAll("table").length >= 4);
    assert.ok(h.textContent.includes("Nordisk Drift AS"));
    assert.equal(h.querySelectorAll("form").length, 0);
    assert.equal(h.querySelectorAll("input, select, textarea").length, 0);
    // …men «Åpne» står igjen: kontrollene er en LESEVEI.
    assert.ok([...h.querySelectorAll("button")].some(
      (b) => b.textContent === t("ui.faktura.knapp.apne")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Faktura: ingen hardkodet tekst", async () => {
  const PL = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visFaktura(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 4);
    // `th[scope="row"]` er UTELATT: den cellen bærer leverandørnavnet og
    // satskoden, altså tenantens data — ikke en oversatt etikett.
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
