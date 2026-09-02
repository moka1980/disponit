// M-25 prosjektflaten (107) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd på hver
//     skjerm flaten kan stå i.
//   * `modulen_fakturerte`: flaten har INGEN «fakturer»-knapp.
//   * `milepael_uten_dokumentasjon`: feltet er `required`, og
//     hjelpeteksten sier HVA SOM STÅR PÅ SPILL — ikke «feltet er
//     påkrevd».
//   * FORBRUK OG BETALINGSPLAN ER TO KOLONNER, aldri én. Et register
//     som la dem sammen ville gjort «går prosjektet i pluss» til et
//     spørsmål ingen kunne svare på.
//   * `belop_i_flyttall`: beløp OG timer i HELTALLSARITMETIKK — timer
//     lagres som hele minutter.
//   * MILEPÆLER OG ALDER ER TEKST, ikke bare farge (WCAG 1.4.1).
//   * En lesende økt ser prosjektene, men INGEN mutasjonskontroller.
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
  belopTekst, forbrukTekst, oreTilFelt, parsePlanlinjer, planTekst,
  sluttTekst, tilMinutter, tilOre, timeTekst, visProsjekt,
} from "../static/js/flater/prosjekt.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));

const P1 = "11111111-1111-1111-1111-111111111111";
const P2 = "22222222-2222-2222-2222-222222222222";

const BILDE = {
  sammendrag: {
    aktive: 12, avsluttede: 30, budsjett_ore: 5000000,
    forbruk_ore: 4100000, klar_ore: 3000000, apne_funn: 5,
    over_budsjett: 1, har_terskel: true, terskelversjon: 2, vist: 2,
  },
  prosjekter: [
    { prosjekt_id: P1, kunde_ref: "Kunde AS", navn: "Nybygg",
      kontrakt_ref: "K-1", budsjett_ore: 1000000, forbruk_ore: 1100000,
      minutter: 960, start: "2026-06-01", planlagt_slutt: "2026-11-01",
      status: "aktiv", dogn_til_slutt: 60, milepaeler: 3, naadde: 1,
      klar_ore: 300000, plan_ore: 1000000,
      apne_funn: ["budsjett_overskredet", "milepael_over_frist"] },
    { prosjekt_id: P2, kunde_ref: "Fjord AS", navn: "Tilbygg",
      kontrakt_ref: null, budsjett_ore: 0, forbruk_ore: 0,
      minutter: 0, start: "2026-08-01", planlagt_slutt: "2026-08-30",
      status: "avsluttet", dogn_til_slutt: -3, milepaeler: 0,
      naadde: 0, klar_ore: 0, plan_ore: 0, apne_funn: [] },
  ],
  terskler: {
    budsjettvarsel_promille: 50, milepael_frist_dogn: 7,
    stillhet_dogn: 30, versjon: 2,
    oppdatert: "2026-08-01T09:00:00+00:00", oppdatert_av: "kari",
  },
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    aktive: 0, avsluttede: 0, budsjett_ore: 0, forbruk_ore: 0,
    klar_ore: 0, apne_funn: 0, over_budsjett: 0, har_terskel: false,
    terskelversjon: null, vist: 0,
  },
  prosjekter: [], terskler: null, request_id: "r-b",
};

const MILEPAELER = {
  prosjekt_id: P1,
  milepaeler: [
    { milepael_nr: 1, navn: "Oppstart", planlagt_dato: "2026-07-01",
      belop_ore: 300000, naadd_ts: "2026-07-02T09:00:00+00:00",
      naadd_av: "kari", dokumentasjon_ref: "befaring, bilde 41",
      dogn_over_frist: null },
    { milepael_nr: 2, navn: "Råbygg", planlagt_dato: "2026-08-01",
      belop_ore: 400000, naadd_ts: null, naadd_av: null,
      dokumentasjon_ref: null, dogn_over_frist: 32 },
    { milepael_nr: 3, navn: "Overtakelse",
      planlagt_dato: "2026-12-01", belop_ore: 300000, naadd_ts: null,
      naadd_av: null, dokumentasjon_ref: null, dogn_over_frist: -90 },
  ],
  request_id: "r-c",
};

const ARBEID = {
  prosjekt_id: P1,
  arbeid: [
    { arbeid_id: "a-1", utfort: "2026-08-20", minutter: 480,
      kostnad_ore: 400000, beskrivelse: "grunnarbeid",
      registrert: "2026-08-21T08:00:00+00:00", registrert_av: "kari" },
    { arbeid_id: "a-2", utfort: "2026-08-21", minutter: 90,
      kostnad_ore: 700000, beskrivelse: "kranleie",
      registrert: "2026-08-22T08:00:00+00:00", registrert_av: "kari" },
  ],
  request_id: "r-d",
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
        json: async () => ({ feil: "prosjekt_ulovlig_tilstand" }) };
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
    "/v1/prosjekt": BILDE,
    [`/v1/prosjekt/${P1}/milepaeler`]: MILEPAELER,
    [`/v1/prosjekt/${P1}/arbeidsliste`]: ARBEID,
  };
}

// ---------------------------------------------------------------------
// belop_i_flyttall — OG TIMENE
// ---------------------------------------------------------------------

test("Prosjekt: beløp og timer i heltallsaritmetikk", () => {
  assert.equal(belopTekst(0), "0,00");
  assert.equal(belopTekst(1100000), "11000,00");
  // DEN SOM AVSLØRER `/100`.
  assert.equal(belopTekst(123456799), "1234567,99");
  assert.equal(belopTekst(12.5), "—");

  // TIMER LAGRES SOM HELE MINUTTER: «7,5 time» som flyttall er
  // 7.499999999999999 på veien tilbake fra 450 minutter.
  assert.equal(timeTekst(960),
    t("ui.prosjekt.timer").replace("{timer}", "16")
      .replace("{minutter}", "00"));
  assert.equal(timeTekst(90),
    t("ui.prosjekt.timer").replace("{timer}", "1")
      .replace("{minutter}", "30"));
  assert.equal(timeTekst(0),
    t("ui.prosjekt.timer").replace("{timer}", "0")
      .replace("{minutter}", "00"));
  assert.equal(timeTekst(1.5), "—");

  assert.equal(tilOre("8.15"), 815);
  // 1,5 time er 90 minutter, ikke 89.99999999999999.
  assert.equal(tilMinutter("1.5"), 90);
  assert.equal(tilMinutter("0.25"), 15);
  assert.equal(tilMinutter("to"), null);
  assert.equal(oreTilFelt(1000000), "10000.00");
  assert.equal(oreTilFelt(null), "");
});

// ---------------------------------------------------------------------
// De to tallene som ikke er det samme
// ---------------------------------------------------------------------

test("Prosjekt: forbruk og betalingsplan er to setninger", () => {
  assert.equal(forbrukTekst({ budsjett_ore: 1000000,
                              forbruk_ore: 1100000 }),
    t("ui.prosjekt.forbruk_av").replace("{forbruk}", "11000,00")
      .replace("{budsjett}", "10000,00"));
  // ET PROSJEKT UTEN BUDSJETT er ikke «0 av 0» — det er en annen
  // tilstand, og et menneske handler ulikt på den.
  assert.equal(forbrukTekst({ budsjett_ore: 0, forbruk_ore: 0 }),
    t("ui.prosjekt.uten_budsjett"));
  assert.equal(planTekst({ milepaeler: 3, naadde: 1, klar_ore: 300000 }),
    t("ui.prosjekt.klar_av").replace("{naadde}", "1")
      .replace("{milepaeler}", "3").replace("{klar}", "3000,00"));
  assert.equal(planTekst({ milepaeler: 0, naadde: 0, klar_ore: 0 }),
    t("ui.prosjekt.uten_plan"));
});

test("Prosjekt: planlagt slutt som TEKST", () => {
  assert.equal(sluttTekst(60),
    t("ui.prosjekt.om_dogn").replace("{dogn}", "60"));
  // ENTALL HAR SIN EGEN NØKKEL på begge språk.
  assert.equal(sluttTekst(1), t("ui.prosjekt.om_ett_dogn"));
  assert.equal(sluttTekst(0), t("ui.prosjekt.slutter_i_dag"));
  assert.equal(sluttTekst(-1), t("ui.prosjekt.over_ett_dogn"));
  assert.equal(sluttTekst(-3),
    t("ui.prosjekt.over_for").replace("{dogn}", "3"));
  assert.equal(sluttTekst(null), "—");
});

test("Prosjekt: begge språk har entallsnøklene", () => {
  for (const sprak of ["nb", "en"]) {
    const tekster = JSON.parse(readFileSync(
      join(HER, "..", "..", "..", "..", "locales", `${sprak}.json`),
      "utf-8"));
    for (const n of ["ui.prosjekt.om_ett_dogn",
                     "ui.prosjekt.over_ett_dogn",
                     "ui.prosjekt.slutter_i_dag"]) {
      assert.ok(tekster[n], `${sprak} mangler ${n}`);
      assert.ok(!tekster[n].includes("{dogn}"),
        `${sprak}: ${n} har en tallplass — da er den ikke entallsformen`);
    }
  }
});

// ---------------------------------------------------------------------
// Betalingsplanen skrives som linjer
// ---------------------------------------------------------------------

test("Prosjekt: planlinjer parses strengt", () => {
  const ok = parsePlanlinjer(
    "Oppstart | 2026-07-01 | 3000\nOvertakelse | 2026-12-01 | 7000.50");
  assert.equal(ok.length, 2);
  assert.deepEqual(ok[0], { navn: "Oppstart",
    planlagt_dato: "2026-07-01", belop_ore: 300000 });
  // BELØPET GÅR GJENNOM SAMME `Math.round` som alt annet.
  assert.equal(ok[1].belop_ore, 700050);
  // Tomme linjer hoppes over; alt annet galt gir null.
  assert.equal(parsePlanlinjer("A | 2026-07-01 | 1\n\n").length, 1);
  assert.equal(parsePlanlinjer("A | 2026-07-01"), null);
  assert.equal(parsePlanlinjer("A | 1. juli | 1"), null);
  assert.equal(parsePlanlinjer("A | 2026-7-1 | 1"), null);
  assert.equal(parsePlanlinjer("A | 2026-07-01 | tusen"), null);
  assert.equal(parsePlanlinjer("A | 2026-07-01 | -1"), null);
  assert.equal(parsePlanlinjer(" | 2026-07-01 | 1"), null);
  assert.equal(parsePlanlinjer("A | 2026-07-01 | 1 | ekstra"), null);
  assert.equal(parsePlanlinjer(""), null);
  assert.equal(parsePlanlinjer(null), null);
});

test("Prosjekt: hjelpeteksten sier det parseren gjør", () => {
  // LÆRDOMMEN FRA M-18: hjelpeteksten og parseren skal si det samme.
  for (const sprak of ["nb", "en"]) {
    const tekster = JSON.parse(readFileSync(
      join(HER, "..", "..", "..", "..", "locales", `${sprak}.json`),
      "utf-8"));
    const hjelp = tekster["ui.prosjekt.skjema.plan_hjelp"];
    assert.ok(/ÅÅÅÅ-MM-DD|YYYY-MM-DD/.test(hjelp),
      `${sprak}: hjelpeteksten sier ikke datoformatet`);
    // …og den sier at nådde milepæler ikke røres.
    assert.ok(/nådd|reached/i.test(hjelp), sprak);
  }
});

// ---------------------------------------------------------------------
// modulen_fakturerte — flatens halvdel
// ---------------------------------------------------------------------

test("Prosjekt: flaten har ingen fakturaknapp", () => {
  const kilde = readFileSync(
    join(HER, "..", "static", "js", "flater", "prosjekt.js"), "utf8");
  const uten = kilde.replace(/^\s*\/\/.*$/gm, "");
  for (const ord of ["faktur", "attester", "signer", "/v1/fordring",
                     "krevInn"]) {
    assert.ok(!uten.toLowerCase().includes(ord.toLowerCase()),
      `flaten bærer «${ord}» — v1 fakturerer ingenting`);
  }
  const api = readFileSync(
    join(HER, "..", "static", "js", "api.js"), "utf8");
  assert.ok(!/export const fakturerMilepael/.test(api));
});

// ---------------------------------------------------------------------
// Skjermene — og axe på hver av dem
// ---------------------------------------------------------------------

test("Prosjekt: listen holder de to tallene fra hverandre, axe rent",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visProsjekt(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));

    const tabeller = [...h.querySelectorAll("table")];
    assert.equal(tabeller.length, 2);
    for (const tb of tabeller) {
      assert.ok(tb.querySelector("caption"), "tabell uten caption");
      assert.ok(tb.querySelectorAll('th[scope="col"]').length >= 2);
      assert.ok(tb.closest(".tablewrap"),
        "tabellen mangler sidescrollens container");
    }

    const rader = [...tabeller[0].querySelectorAll("tbody tr")];
    assert.equal(rader.length, 2);
    // DE TO TALLENE STÅR I HVER SIN KOLONNE.
    assert.ok(rader[0].textContent.includes(
      t("ui.prosjekt.forbruk_av").replace("{forbruk}", "11000,00")
        .replace("{budsjett}", "10000,00")));
    assert.ok(rader[0].textContent.includes(
      t("ui.prosjekt.klar_av").replace("{naadde}", "1")
        .replace("{milepaeler}", "3").replace("{klar}", "3000,00")));
    // …og SETNINGEN SOM SIER AT DE IKKE ER DET SAMME står på flaten.
    assert.ok(h.textContent.includes(t("ui.prosjekt.oversikt.skille")));
    // MERKENE ER ORD (WCAG 1.4.1).
    assert.ok(rader[0].textContent.includes(
      t("ui.prosjekt.merke_budsjett")));
    assert.ok(rader[0].textContent.includes(
      t("ui.prosjekt.merke_milepael")));
    // KUNDEN NAVNGIR RADEN.
    assert.equal(rader[0].querySelector('th[scope="row"]').textContent,
      "Kunde AS");
    // …og et avsluttet prosjekt over tiden sier det med ord.
    assert.ok(rader[1].textContent.includes(
      t("ui.prosjekt.over_for").replace("{dogn}", "3")));
  });

test("Prosjekt: sammendraget teller ALT, og avkortingen sies høyt",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visProsjekt(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);
    const tekst = h.textContent;
    assert.ok(tekst.includes("12"), "sammendraget teller listen, ikke alt");
    assert.ok(tekst.includes("50000,00"));
    assert.ok(tekst.includes("41000,00"));
    assert.ok(tekst.includes("30000,00"));
    assert.ok(tekst.includes(
      t("ui.prosjekt.avkortet").replace("{vist}", "2")));
    assert.ok(!tekst.includes(t("ui.prosjekt.ingen_terskler")));
  });

test("Prosjekt: tomt register sier hva som mangler, axe rent",
  async () => {
    SVAR = { "/v1/prosjekt": TOMT };
    const h = nyHoved();
    visProsjekt(h, ctx());
    await vent(() => h.textContent.includes(t("ui.prosjekt.liste.ingen")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
    assert.ok(h.textContent.includes(t("ui.prosjekt.ingen_terskler")));
  });

test("Prosjekt: detaljpanelet viser dokumentasjonen på hver milepæl",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visProsjekt(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);
    [...h.querySelectorAll("table")[0].querySelectorAll("tbody tr")][0]
      .querySelector("button").click();
    await vent(() => h.querySelectorAll("table").length >= 3);
    await vent(() => h.querySelectorAll("li").length >= 2);

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));

    // TABELLENE I REKKEFØLGE: prosjektlisten (0), tersklene (1), og
    // milepælene (2) — detaljpanelet står SIST i `deler`.
    const mp = h.querySelectorAll("table")[2];
    const rader = [...mp.querySelectorAll("tbody tr")];
    assert.equal(rader.length, 3);
    // DOKUMENTASJONEN ER KOLONNEN SOM BETYR NOE.
    assert.ok(rader[0].textContent.includes("befaring, bilde 41"));
    assert.ok(rader[0].textContent.includes(
      t("ui.prosjekt.milepael.naadd").replace("{av}", "kari")));
    // …og en unådd milepæl forbi sin dato sier hvor langt over den er.
    assert.ok(rader[1].textContent.includes(
      t("ui.prosjekt.milepael.over").replace("{dogn}", "32")));
    assert.ok(rader[1].textContent.includes("—"));
    // …mens en som ikke er forfalt bare venter.
    assert.ok(rader[2].textContent.includes(
      t("ui.prosjekt.milepael.venter")));

    // ARBEIDET STÅR SOM TIMER OG MINUTTER, ikke som desimaltall.
    const punkter = [...h.querySelectorAll("li")].map((n) => n.textContent);
    assert.ok(punkter[0].includes(
      t("ui.prosjekt.timer").replace("{timer}", "8")
        .replace("{minutter}", "00")));
    assert.ok(punkter[1].includes(
      t("ui.prosjekt.timer").replace("{timer}", "1")
        .replace("{minutter}", "30")));
    // MERKELINJEN HOLDER DE TO TALLENE FRA HVERANDRE.
    assert.ok(h.textContent.includes(t("ui.prosjekt.detalj.budsjett")));
    assert.ok(h.textContent.includes(t("ui.prosjekt.detalj.plan")));
  });

test("Prosjekt: et avsluttet prosjekt tar ikke imot noe", async () => {
  SVAR = { ...fullSvar(),
    [`/v1/prosjekt/${P2}/milepaeler`]: { milepaeler: [],
                                         request_id: "r-e" },
    [`/v1/prosjekt/${P2}/arbeidsliste`]: { arbeid: [],
                                           request_id: "r-f" } };
  const h = nyHoved();
  visProsjekt(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 2);
  const rader = [...h.querySelectorAll("table")[0]
    .querySelectorAll("tbody tr")];
  const knapper = () => ["#pr-plan-linjer", "#pr-mp-dok", "#pr-ar-tekst",
                         "#pr-slutt-grunn"]
    .map((id) => h.querySelector(id).closest("form")
      .querySelector("button[type=submit]"));

  rader[0].querySelector("button").click();
  await vent(() => h.querySelectorAll("li").length >= 2);
  assert.equal(knapper().length, 4);
  assert.ok(knapper().every((k) => !k.disabled));

  rader[1].querySelector("button").click();
  await vent(() => h.textContent.includes(
    t("ui.prosjekt.detalj.ingen_arbeid")));
  assert.ok(knapper().every((k) => k.disabled),
    "et avsluttet prosjekt tilbyr fortsatt en handling");
});

// ---------------------------------------------------------------------
// Skriveveiene
// ---------------------------------------------------------------------

test("Prosjekt: milepælen krever dokumentasjon i skjemaet også",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visProsjekt(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);
    [...h.querySelectorAll("table")[0].querySelectorAll("tbody tr")][0]
      .querySelector("button").click();
    await vent(() => !!h.querySelector("#pr-mp-dok"));
    // FELTET ER PÅKREVD i skjemaet, av samme grunn som CHECK-en er det
    // i basen.
    assert.equal(h.querySelector("#pr-mp-dok").required, true);
    // …og HJELPETEKSTEN SIER HVA SOM STÅR PÅ SPILL, ikke «feltet er
    // påkrevd».
    for (const sprak of ["nb", "en"]) {
      const tekster = JSON.parse(readFileSync(
        join(HER, "..", "..", "..", "..", "locales", `${sprak}.json`),
        "utf-8"));
      const hjelp = tekster["ui.prosjekt.skjema.dokumentasjon_hjelp"];
      assert.ok(/krav|claim/i.test(hjelp),
        `${sprak}: hjelpeteksten sier ikke hva som står på spill`);
    }
    h.querySelector("#pr-mp-nr").value = "2";
    h.querySelector("#pr-mp-dok").value = "rapport 12";
    h.querySelector("#pr-mp-dok").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE && SISTE.sti.includes("milepael"));
    assert.equal(SISTE.kropp.milepael_nr, 2);
    assert.equal(SISTE.kropp.dokumentasjon_ref, "rapport 12");
    assert.ok(SISTE.headers["Idempotency-Key"]);
  });

test("Prosjekt: arbeidet sendes i MINUTTER og ØRE", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visProsjekt(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 2);
  [...h.querySelectorAll("table")[0].querySelectorAll("tbody tr")][0]
    .querySelector("button").click();
  await vent(() => !!h.querySelector("#pr-ar-timer"));
  h.querySelector("#pr-ar-dato").value = "2026-09-01";
  h.querySelector("#pr-ar-timer").value = "1.5";
  h.querySelector("#pr-ar-kost").value = "8.15";
  h.querySelector("#pr-ar-tekst").value = "kranleie";
  h.querySelector("#pr-ar-timer").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("/arbeid"));
  // 1,5 time er 90 minutter, og 8,15 kroner er 815 øre.
  assert.equal(SISTE.kropp.minutter, 90);
  assert.equal(SISTE.kropp.kostnad_ore, 815);
});

test("Prosjekt: en plan uten gyldig format sendes aldri", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visProsjekt(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 2);
  [...h.querySelectorAll("table")[0].querySelectorAll("tbody tr")][0]
    .querySelector("button").click();
  await vent(() => !!h.querySelector("#pr-plan-linjer"));
  const linjer = h.querySelector("#pr-plan-linjer");
  linjer.value = "Oppstart | 1. juli | 3000";
  linjer.closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.ok(await vent(() => h.textContent.includes(
    t("ui.prosjekt.skjema.plan_feil"))), "formatfeilen ble ikke vist");
  assert.equal(KALL.filter((k) => k.metode === "POST").length, 0);

  linjer.value = "Oppstart | 2026-07-01 | 3000";
  linjer.closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("betalingsplan"));
  assert.equal(SISTE.kropp.milepaeler.length, 1);
  assert.equal(SISTE.kropp.milepaeler[0].belop_ore, 300000);
});

test("Prosjekt: kvitteringen og panelet overlever tegningen", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visProsjekt(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 2);
  [...h.querySelectorAll("table")[0].querySelectorAll("tbody tr")][0]
    .querySelector("button").click();
  await vent(() => !!h.querySelector("#pr-ar-timer"));
  h.querySelector("#pr-ar-dato").value = "2026-09-01";
  h.querySelector("#pr-ar-timer").value = "1";
  h.querySelector("#pr-ar-kost").value = "10";
  h.querySelector("#pr-ar-tekst").value = "x";
  h.querySelector("#pr-ar-timer").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.ok(await vent(() => KALL.filter(
    (k) => k.sti.includes("/milepaeler")).length >= 2),
    "panelet ble aldri gjenåpnet — porten måler ingenting");
  assert.ok(h.textContent.includes(t("ui.prosjekt.skjema.arbeid_ok")),
    "kvitteringen forsvant i tegningen");
  assert.ok(h.textContent.includes("Kunde AS · Nybygg"),
    "panelet lukket seg etter en føring");
});

test("Prosjekt: en 409 sier hva registeret nektet", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visProsjekt(h, ctx());
  await vent(() => !!h.querySelector("#pr-ny-navn"));
  SVARSTATUS = 409;
  h.querySelector("#pr-ny-kunde").value = "X";
  h.querySelector("#pr-ny-navn").value = "Y";
  h.querySelector("#pr-ny-budsjett").value = "1";
  h.querySelector("#pr-ny-start").value = "2026-09-01";
  h.querySelector("#pr-ny-slutt").value = "2026-10-01";
  const skjema = h.querySelector("#pr-ny-navn").closest("form");
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.ok(await vent(() => h.textContent.includes(
    t("ui.prosjekt.feil.tilstand"))), "tilstandsfeilen ble ikke vist");
  assert.ok(!h.textContent.includes(t("ui.prosjekt.feil.generell")));
  assert.ok(h.querySelector('[role="alert"]'));
  assert.equal(skjema.querySelector("button[type=submit]").disabled, false);
});

// ---------------------------------------------------------------------
// Scope og språk
// ---------------------------------------------------------------------

test("Prosjekt: en lesende økt ser tallene, men ingen kontroller",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visProsjekt(h, ctx(["okonomi:read"]));
    await vent(() => h.querySelectorAll("table").length >= 2);
    assert.ok(h.textContent.includes("Kunde AS"));
    assert.equal(h.querySelectorAll("form").length, 0);
    assert.equal(h.querySelectorAll("input, select, textarea").length, 0);
    assert.ok([...h.querySelectorAll("button")].some(
      (b) => b.textContent === t("ui.prosjekt.knapp.apne")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Prosjekt: ingen hardkodet tekst", async () => {
  const PL = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visProsjekt(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);
    // `th[scope="row"]` er UTELATT: den cellen bærer kundenavnet og
    // milepælnummeret, altså tenantens data.
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
