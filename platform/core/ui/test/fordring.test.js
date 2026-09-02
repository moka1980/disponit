// M-23 fordringsflaten (104) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd, på hver
//     av skjermene flaten kan stå i (liste, tomt register, detaljpanel).
//   * `belop_i_flyttall`: beløp formateres i HELTALLSARITMETIKK, og
//     kroner→øre går gjennom `Math.round` — 8,15 er 815 øre, ikke
//     814,9999999999999.
//   * MODENHET OG FORFALL ER TEKST, ikke bare farge (WCAG 1.4.1).
//   * `purretrinn_hardkodet`: trinnene skrives som LINJER av tenanten,
//     og hjelpeteksten sier nøyaktig det parseren gjør.
//   * `purretrinn_hoppet_over`: knappen heter «flytt til neste trinn»,
//     tar ingen trinnparameter, og hjelpeteksten sier ETT hakk.
//   * `modulen_sendte_til_kunde`: flaten har INGEN send-kontroll, og
//     kilden bærer ingen egressvei.
//   * SAMMENDRAGET TELLER ALT, og avkortingen sies høyt.
//   * En lesende økt ser fordringene, men INGEN mutasjonskontroller.
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
  belopTekst, erModen, forfallTekst, parsePlanlinjer, tilOre, visFordring,
} from "../static/js/flater/fordring.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));

const F1 = "11111111-1111-1111-1111-111111111111";
const F2 = "22222222-2222-2222-2222-222222222222";
const F3 = "33333333-3333-3333-3333-333333333333";

const BILDE = {
  sammendrag: {
    apne: 42, apent_ore: 125000000, forfalte: 17, forfalt_ore: 48000000,
    i_purring: 9, har_purreplan: true, vist: 3,
  },
  aldersfordeling: [
    { botte: "ikke_forfalt", antall: 25, ore: 77000000 },
    { botte: "1_30", antall: 8, ore: 21000000 },
    { botte: "31_60", antall: 5, ore: 15000000 },
    { botte: "61_90", antall: 0, ore: 0 },
    { botte: "over_90", antall: 4, ore: 12000000 },
  ],
  fordringer: [
    { fordring_id: F1, kunde_ref: "Nordvik AS", fakturanummer: "F-1001",
      belop_ore: 250000, betalt_ore: 0, rest_ore: 250000,
      status: "apen", trinn: 1, trinn_navn: "Påminnelse",
      utstedt: "2026-06-20", forfall: "2026-07-01",
      dogn_over_forfall: 41, moden_for_trinn: 3,
      apne_funn: ["trinn_forfalt"] },
    { fordring_id: F2, kunde_ref: "Fjord AS", fakturanummer: "F-1002",
      belop_ore: 100000, betalt_ore: 40000, rest_ore: 60000,
      status: "apen", trinn: 0, trinn_navn: null,
      utstedt: "2026-08-20", forfall: "2026-09-10",
      dogn_over_forfall: -8, moden_for_trinn: null, apne_funn: [] },
    { fordring_id: F3, kunde_ref: "Berg AS", fakturanummer: "F-0999",
      belop_ore: 500000, betalt_ore: 500000, rest_ore: 0,
      status: "betalt", trinn: 2, trinn_navn: "Purring",
      utstedt: "2026-05-01", forfall: "2026-05-20",
      dogn_over_forfall: 104, moden_for_trinn: 3, apne_funn: [] },
  ],
  purreplan: [
    { versjon: 2, trinn_nr: 1, navn: "Påminnelse", dogn_etter_forfall: 3,
      handling: "paaminnelse", gebyr_ore: 0 },
    { versjon: 2, trinn_nr: 2, navn: "Purring", dogn_etter_forfall: 14,
      handling: "purring", gebyr_ore: 7000 },
    { versjon: 2, trinn_nr: 3, navn: "Inkassovarsel",
      dogn_etter_forfall: 28, handling: "inkassovarsel",
      gebyr_ore: 35000 },
  ],
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    apne: 0, apent_ore: 0, forfalte: 0, forfalt_ore: 0, i_purring: 0,
    har_purreplan: false, vist: 0,
  },
  aldersfordeling: [
    { botte: "ikke_forfalt", antall: 0, ore: 0 },
    { botte: "1_30", antall: 0, ore: 0 },
    { botte: "31_60", antall: 0, ore: 0 },
    { botte: "61_90", antall: 0, ore: 0 },
    { botte: "over_90", antall: 0, ore: 0 },
  ],
  fordringer: [], purreplan: [], request_id: "r-b",
};

const HENDELSER = {
  fordring_id: F1,
  hendelser: [
    { hendelse_id: "h-1", art: "betaling", belop_ore: 50000, trinn: null,
      inntruffet: "2026-07-20", begrunnelse: null,
      opprettet_av: "kari@example.test" },
    { hendelse_id: "h-2", art: "trinn", belop_ore: null, trinn: 1,
      inntruffet: "2026-07-05", begrunnelse: "purret per telefon",
      opprettet_av: "kari@example.test" },
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
        json: async () => ({ feil: "fordring_ulovlig_tilstand" }) };
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
    "/v1/fordring": BILDE,
    [`/v1/fordring/${F1}/hendelser`]: HENDELSER,
  };
}

// ---------------------------------------------------------------------
// belop_i_flyttall
// ---------------------------------------------------------------------

test("Fordring: beløp formateres i heltallsaritmetikk", () => {
  assert.equal(belopTekst(0), "0,00");
  assert.equal(belopTekst(5), "0,05");
  assert.equal(belopTekst(250000), "2500,00");
  assert.equal(belopTekst(-4250), "-42,50");
  // DEN SOM AVSLØRER `/100`: 123456799 øre er 1234567,99 kroner. Et
  // flyttall gir «1234567,9899999999» her, og et krav mot en kunde tåler
  // ikke et tall som nesten stemmer.
  assert.equal(belopTekst(123456799), "1234567,99");
  // …og et beløp som IKKE er et heltall er ikke et beløp.
  assert.equal(belopTekst(12.5), "—");
  assert.equal(belopTekst(null), "—");
  assert.equal(belopTekst("250000"), "—");
});

test("Fordring: kroner inn, øre ut — gjennom Math.round", () => {
  assert.equal(tilOre("2500"), 250000);
  // DEN SOM AVSLØRER `parseFloat(x) * 100` uten avrunding: 8,15 kroner
  // er 814.9999999999999 i flyttall, og `Math.trunc` ville gitt 814 øre.
  assert.equal(tilOre("8.15"), 815);
  // EN HALV ØRE FINNES IKKE, og porten er ærlig om hvorfor svaret er
  // 100 og ikke 101: 1,005 har ingen eksakt binær form (den er
  // 1,00499999…), så `Math.round` runder ned. Skjemaet har `step="0.01"`
  // nettopp fordi et halvøres-beløp ikke er et beløp — porten fester
  // oppførselen så ingen senere «fikser» den til noe udefinert.
  assert.equal(tilOre("1.005"), 100);
  assert.equal(tilOre("0.01"), 1);
  assert.equal(tilOre(""), 0);
  assert.equal(tilOre("to hundre"), null);
  assert.equal(tilOre("Infinity"), null);
});

// ---------------------------------------------------------------------
// Ord, ikke farge
// ---------------------------------------------------------------------

test("Fordring: forfall og modenhet som TEKST", () => {
  assert.equal(forfallTekst(41),
    t("ui.fordring.forfalt_for").replace("{dogn}", "41"));
  // ENTALL HAR SIN EGEN NØKKEL på begge språk — locale-settet har ingen
  // pluralmaskineri, og «forfalt for 1 døgn siden» ville stått på den
  // raden et menneske leser først.
  assert.equal(forfallTekst(1), t("ui.fordring.forfalt_ett_dogn"));
  assert.equal(forfallTekst(0), t("ui.fordring.forfaller_i_dag"));
  assert.equal(forfallTekst(-1), t("ui.fordring.om_ett_dogn"));
  assert.equal(forfallTekst(-8),
    t("ui.fordring.om_dogn").replace("{dogn}", "8"));
  assert.equal(forfallTekst(null), "—");

  // MODEN = planen har et høyere trinn enn kravet står på.
  assert.equal(erModen(BILDE.fordringer[0]), true);
  assert.equal(erModen(BILDE.fordringer[1]), false);
  // ET BETALT KRAV ER ALDRI MODENT, uansett hva trinnet sier. Uten
  // status-leddet ville en oppgjort faktura stått som «moden for trinn
  // 3» — og det er nøyaktig raden noen ville eskalert.
  assert.equal(erModen(BILDE.fordringer[2]), false);
});

test("Fordring: begge språk har entallsnøklene", () => {
  for (const sprak of ["nb", "en"]) {
    const tekster = JSON.parse(readFileSync(
      join(HER, "..", "..", "..", "..", "locales", `${sprak}.json`),
      "utf-8"));
    for (const n of ["ui.fordring.forfalt_ett_dogn",
                     "ui.fordring.om_ett_dogn",
                     "ui.fordring.forfaller_i_dag"]) {
      assert.ok(tekster[n], `${sprak} mangler ${n}`);
      assert.ok(!tekster[n].includes("{dogn}"),
        `${sprak}: ${n} har en tallplass — da er den ikke entallsformen`);
    }
  }
});

// ---------------------------------------------------------------------
// purretrinn_hardkodet — planen skrives av tenanten
// ---------------------------------------------------------------------

test("Fordring: purrelinjer parses strengt", () => {
  const ok = parsePlanlinjer(
    "Påminnelse | 3 | paaminnelse\nPurring | 14 | purring | 70");
  assert.equal(ok.length, 2);
  assert.deepEqual(ok[0], { navn: "Påminnelse", dogn_etter_forfall: 3,
    handling: "paaminnelse", gebyr_ore: 0 });
  // GEBYRET GÅR GJENNOM SAMME `Math.round` som beløpene.
  assert.equal(ok[1].gebyr_ore, 7000);
  assert.equal(parsePlanlinjer("A | 0 | purring")[0].dogn_etter_forfall, 0);
  // Tomme linjer hoppes over; alt annet galt gir null.
  assert.equal(parsePlanlinjer("A | 3 | purring\n\n").length, 1);
  assert.equal(parsePlanlinjer("A | 3"), null);
  assert.equal(parsePlanlinjer("A | tre | purring"), null);
  assert.equal(parsePlanlinjer("A | 3.5 | purring"), null);
  assert.equal(parsePlanlinjer("A | -1 | purring"), null);
  assert.equal(parsePlanlinjer(" | 3 | purring"), null);
  // EN UKJENT HANDLING AVVISES. Uten den lukkede listen ville
  // «namsmann» blitt sendt videre som en handling ingen har definert.
  assert.equal(parsePlanlinjer("A | 3 | namsmann"), null);
  assert.equal(parsePlanlinjer("A | 3 | purring | -5"), null);
  assert.equal(parsePlanlinjer(""), null);
  assert.equal(parsePlanlinjer(null), null);
});

test("Fordring: hjelpeteksten sier nøyaktig det parseren gjør", () => {
  // LÆRDOMMEN FRA M-18: første utgave av steg-hjelpen sa «O for
  // obligatorisk» mens parseren leste «V for valgfritt». En bruker som
  // fulgte teksten fikk noe annet enn den lovte, og skjønte ikke hvorfor.
  // Porten binder de fire lovlige handlingene til hjelpeteksten, på
  // begge språk, og krever at DØGNENE MÅ STIGE står der — det er
  // regelen basen håndhever og den ENESTE brukeren ikke kan gjette.
  const kilde = readFileSync(
    join(HER, "..", "static", "js", "flater", "fordring.js"), "utf8");
  const m = kilde.match(/const HANDLINGER = \[([^\]]+)\]/);
  assert.ok(m, "fant ikke HANDLINGER-listen i flaten");
  const handlinger = m[1].split(",").map((s) => s.trim().replace(/"/g, ""))
    .filter(Boolean);
  assert.equal(handlinger.length, 4);
  for (const sprak of ["nb", "en"]) {
    const tekster = JSON.parse(readFileSync(
      join(HER, "..", "..", "..", "..", "locales", `${sprak}.json`),
      "utf-8"));
    const hjelp = tekster["ui.fordring.skjema.plan_hjelp"];
    for (const h of handlinger) {
      assert.ok(hjelp.includes(h),
        `${sprak}: hjelpeteksten nevner ikke handlingen «${h}»`);
      // …og hver handling har en oversatt etikett i tabellen.
      assert.ok(tekster[`ui.fordring.handling.${h}`],
        `${sprak} mangler ui.fordring.handling.${h}`);
    }
    assert.ok(/stige|increase/i.test(hjelp),
      `${sprak}: hjelpeteksten sier ikke at døgnene må stige`);
  }
});

// ---------------------------------------------------------------------
// purretrinn_hoppet_over — knappen lover ETT hakk
// ---------------------------------------------------------------------

test("Fordring: trinnknappen tar ingen trinnparameter", () => {
  // FLATEN KAN IKKE BE OM ET TRINN. Det finnes ingen kontroll å velge
  // trinn i, og API-hjelperen tar ingen — et hopp er en eskalering ingen
  // besluttet, og den kan ikke engang formuleres herfra.
  const kilde = readFileSync(
    join(HER, "..", "static", "js", "flater", "fordring.js"), "utf8");
  assert.ok(!/nesteTrinn\([^)]*trinn\s*[,)]/.test(kilde));
  const api = readFileSync(
    join(HER, "..", "static", "js", "api.js"), "utf8");
  const linje = api.match(/export const nesteTrinn = \(([^)]*)\)/);
  assert.ok(linje, "fant ikke nesteTrinn i api.js");
  assert.deepEqual(linje[1].split(",").map((s) => s.trim()),
    ["fordringId", "begrunnelse", "idem"]);
  for (const sprak of ["nb", "en"]) {
    const tekster = JSON.parse(readFileSync(
      join(HER, "..", "..", "..", "..", "locales", `${sprak}.json`),
      "utf-8"));
    // HJELPETEKSTEN SIER HVA KNAPPEN GJØR: ETT hakk. Uten den ville en
    // bruker trodd hun kunne velge trinn, og møtt en 409 uten å forstå.
    assert.ok(/ETT|ONE/.test(tekster["ui.fordring.skjema.trinn_hjelp"]),
      `${sprak}: trinnhjelpen sier ikke ETT hakk`);
  }
});

// ---------------------------------------------------------------------
// modulen_sendte_til_kunde — flatens halvdel
// ---------------------------------------------------------------------

test("Fordring: flaten har ingen send-kontroll", () => {
  // KATALOGEN LOVER ET FORSLAG TIL KUNDEN; v1 registrerer kravet.
  // Fraværet er dommen — og her måles det på KILDEN og på KNAPPENE.
  // De andre halvdelene står i `test_m23_fordring.py` (AST,
  // datamodellen, rutene, og radantallet etter en sveip).
  const kilde = readFileSync(
    join(HER, "..", "static", "js", "flater", "fordring.js"), "utf8");
  const uten = kilde.replace(/^\s*\/\/.*$/gm, "");
  for (const ord of ["sendPurring", "sendVarsel", "epost", "mailto:",
                     "/v1/utsending", "sms"]) {
    assert.ok(!uten.toLowerCase().includes(ord.toLowerCase()),
      `flaten bærer «${ord}» — v1 sender ingenting`);
  }
  const api = readFileSync(
    join(HER, "..", "static", "js", "api.js"), "utf8");
  assert.ok(!/export const sendPurring/.test(api));
});

// ---------------------------------------------------------------------
// Skjermene — og axe på hver av dem
// ---------------------------------------------------------------------

test("Fordring: listen tegnes med modenhet som tekst, axe rent",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visFordring(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));

    // TRE TABELLER: aldersfordeling, fordringer, purreplan — hver med
    // <caption> og th[scope="col"] (m16-formen).
    const tabeller = [...h.querySelectorAll("table")];
    assert.equal(tabeller.length, 3);
    for (const tb of tabeller) {
      assert.ok(tb.querySelector("caption"), "tabell uten caption");
      assert.ok(tb.querySelectorAll('th[scope="col"]').length >= 3);
      assert.ok(tb.closest(".tablewrap"),
        "tabellen mangler sidescrollens container");
    }

    // MERKET ER ORD, ikke bare farge (WCAG 1.4.1) — og det står på den
    // raden som faktisk har passert sitt trinn.
    const rader = [...tabeller[1].querySelectorAll("tbody tr")];
    assert.equal(rader.length, 3);
    assert.ok(rader[0].textContent.includes(
      t("ui.fordring.merke_moden").replace("{trinn}", "3")));
    assert.ok(rader[0].textContent.includes(
      t("ui.fordring.forfalt_for").replace("{dogn}", "41")));
    // …og raden som ikke er forfalt har INGEN merke.
    assert.ok(!rader[1].textContent.includes(
      t("ui.fordring.merke_moden").replace("{trinn}", "")));
    assert.ok(rader[1].textContent.includes(
      t("ui.fordring.om_dogn").replace("{dogn}", "8")));
    // BELØP OG REST STÅR BEGGE: 1000,00 betalt av 1000,00 er en annen
    // opplysning enn 600,00 igjen av 1000,00.
    assert.ok(rader[1].textContent.includes("1000,00"));
    assert.ok(rader[1].textContent.includes("600,00"));
    // KUNDEN NAVNGIR RADEN.
    assert.equal(rader[0].querySelector('th[scope="row"]').textContent,
      "Nordvik AS");

    // ALDERSFORDELINGEN TEGNER ALLE FEM BØTTENE, også den tomme — en
    // fordeling som endret form fra dag til dag kan ingen sammenligne.
    assert.equal(tabeller[0].querySelectorAll("tbody tr").length, 5);
    assert.ok(tabeller[0].textContent.includes(
      t("ui.fordring.alder.61_90")));
  });

test("Fordring: sammendraget teller ALT, og avkortingen sies høyt",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visFordring(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);
    const tekst = h.textContent;
    // TALLENE ER SAMMENDRAGETS, ikke listens: 42 åpne krav, mens
    // tabellen viser tre. En flate som telte radene ville sagt «3 åpne
    // krav» om en virksomhet som har 42.
    assert.ok(tekst.includes("42"), "sammendraget teller listen, ikke alt");
    assert.ok(tekst.includes("1250000,00"));
    assert.ok(tekst.includes(
      t("ui.fordring.avkortet").replace("{vist}", "3")),
      "avkortingen sies ikke høyt");
    assert.ok(!tekst.includes(t("ui.fordring.ingen_purreplan")));
  });

test("Fordring: tomt register sier hva som mangler, axe rent",
  async () => {
    SVAR = { "/v1/fordring": TOMT };
    const h = nyHoved();
    visFordring(h, ctx());
    await vent(() => h.textContent.includes(t("ui.fordring.liste.ingen")));

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));

    // UTEN PLAN VET INGEN NÅR NOE ESKALERER — som en setning, ikke som
    // en tom tabell lenger nede.
    assert.ok(h.textContent.includes(t("ui.fordring.ingen_purreplan")));
    // …men aldersfordelingen tegnes likevel, med alle bøttene på null.
    const alder = h.querySelectorAll("table")[0];
    assert.equal(alder.querySelectorAll("tbody tr").length, 5);
    // INGEN AVKORTINGSSETNING når ingenting er avkortet.
    assert.ok(!h.textContent.includes(
      t("ui.fordring.avkortet").replace("{vist}", "0")));
  });

test("Fordring: detaljpanelet viser historikken, axe rent", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visFordring(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 3);

  const rader = [...h.querySelectorAll("table")[1]
    .querySelectorAll("tbody tr")];
  rader[0].querySelector("button").click();
  await vent(() => h.querySelectorAll("li").length >= 2);

  assert.deepEqual(KALL.filter((k) => k.sti.includes("hendelser")),
    [{ sti: `/v1/fordring/${F1}/hendelser`, metode: "GET" }]);

  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));

  const punkter = [...h.querySelectorAll("li")].map((n) => n.textContent);
  assert.ok(punkter[0].includes(t("ui.fordring.detalj.art.betaling")));
  assert.ok(punkter[0].includes("500,00"));
  assert.ok(punkter[1].includes(
    t("ui.fordring.detalj.art.trinn").replace("{trinn}", "1")));
  assert.ok(punkter[1].includes("purret per telefon"));
  // MERKELINJEN NAVNGIR KRAVET så handlingene nedenfor ikke er anonyme.
  assert.ok(h.textContent.includes("Nordvik AS · F-1001"));
});

test("Fordring: et avsluttet krav tar ikke imot noe", async () => {
  // KNAPPENE DEAKTIVERES i stedet for å love noe serveren avviser med
  // 409. Et betalt krav som tilbød «Flytt til neste trinn» ville vært en
  // eskalering brukeren trodde hun kunne gjøre.
  SVAR = { ...fullSvar(),
    [`/v1/fordring/${F3}/hendelser`]: { hendelser: [], request_id: "r-d" } };
  const h = nyHoved();
  visFordring(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 3);
  const rader = [...h.querySelectorAll("table")[1]
    .querySelectorAll("tbody tr")];

  // Først det ÅPNE kravet: knappene er levende.
  rader[0].querySelector("button").click();
  await vent(() => h.querySelectorAll("li").length >= 2);
  // BARE DETALJPANELETS TRE KNAPPER. «Ny fordring» og «purreplan» står
  // utenfor panelet og skal være levende uansett hvilket krav som er
  // åpnet — en port som tok dem med ville målt feil skjema.
  const knapper = () => ["#fo-bet-belop", "#fo-trinn-grunn",
                         "#fo-etter-grunn"]
    .map((id) => h.querySelector(id).closest("form")
      .querySelector("button[type=submit]"));
  assert.equal(knapper().length, 3);
  assert.ok(knapper().every((k) => !k.disabled));

  // …og så det BETALTE (rad 3).
  rader[2].querySelector("button").click();
  await vent(() => h.textContent.includes(t("ui.fordring.detalj.ingen")));
  assert.ok(knapper().every((k) => k.disabled),
    "et oppgjort krav tilbyr fortsatt en eskalering");
});

// ---------------------------------------------------------------------
// Skriveveiene
// ---------------------------------------------------------------------

test("Fordring: innbetalingen sendes i ØRE med én nøkkel", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visFordring(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 3);
  [...h.querySelectorAll("table")[1].querySelectorAll("tbody tr")][0]
    .querySelector("button").click();
  await vent(() => h.querySelectorAll("li").length >= 2);

  const skjema = h.querySelector("#fo-bet-belop").closest("form");
  h.querySelector("#fo-bet-belop").value = "8.15";
  h.querySelector("#fo-bet-dato").value = "2026-08-11";
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("betaling"));

  assert.equal(SISTE.sti, `/v1/fordring/${F1}/betaling`);
  // KRONER INN, ØRE UT — og ikke 814.
  assert.equal(SISTE.kropp.belop_ore, 815);
  assert.equal(SISTE.kropp.inntruffet, "2026-08-11");
  assert.ok(SISTE.headers["Idempotency-Key"]);
});

test("Fordring: en purreplan uten gyldig format sendes aldri", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visFordring(h, ctx());
  await vent(() => !!h.querySelector("#fo-plan-linjer"));

  const linjer = h.querySelector("#fo-plan-linjer");
  const skjema = linjer.closest("form");
  linjer.value = "Påminnelse | 3 | namsmann";
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => h.textContent.includes(
    t("ui.fordring.skjema.plan_feil")));
  // FORMATFEILEN FANGES PÅ FLATEN, med en setning om hva som mangler.
  // Sendt videre ville den blitt «request_feilformet», og brukeren ville
  // ikke visst hvilken linje som var gal.
  assert.equal(KALL.filter((k) => k.metode === "POST").length, 0);

  // …og en gyldig plan går ut som TRINN, ikke som råtekst.
  linjer.value = "Påminnelse | 3 | paaminnelse\nPurring | 14 | purring | 70";
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("purreplan"));
  assert.equal(SISTE.kropp.trinn.length, 2);
  assert.equal(SISTE.kropp.trinn[1].gebyr_ore, 7000);
});

test("Fordring: en 409 sier hva registeret nektet", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visFordring(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 3);
  [...h.querySelectorAll("table")[1].querySelectorAll("tbody tr")][0]
    .querySelector("button").click();
  await vent(() => h.querySelectorAll("li").length >= 2);

  SVARSTATUS = 409;
  const grunn = h.querySelector("#fo-trinn-grunn");
  grunn.closest("form").dispatchEvent(
    new window.Event("submit", { cancelable: true }));
  await vent(() => h.textContent.includes(t("ui.fordring.feil.tilstand")));
  // EN TILSTANDSFEIL ER IKKE EN GENERELL FEIL. «Prøv igjen» ville vært
  // løgn: det samme forsøket blir avvist hver gang.
  assert.ok(!h.textContent.includes(t("ui.fordring.feil.generell")));
  assert.ok(h.querySelector('[role="alert"]'));
  // …og KNAPPEN ER LEVENDE IGJEN, ellers ville skjermen vært død.
  assert.equal(
    grunn.closest("form").querySelector("button[type=submit]").disabled,
    false);
});

// ---------------------------------------------------------------------
// Scope og språk
// ---------------------------------------------------------------------

test("Fordring: en lesende økt ser tallene, men ingen kontroller",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visFordring(h, ctx(["okonomi:read"]));
    await vent(() => h.querySelectorAll("table").length >= 3);

    assert.ok(h.textContent.includes("Nordvik AS"));
    // INGEN SKJEMAER I DET HELE TATT — verken ny fordring, purreplan
    // eller de tre handlingene i detaljpanelet.
    assert.equal(h.querySelectorAll("form").length, 0);
    assert.equal(h.querySelectorAll("input, textarea").length, 0);
    // …men «Åpne» står igjen: historikken er en LESEVEI.
    assert.ok([...h.querySelectorAll("button")].some(
      (b) => b.textContent === t("ui.fordring.knapp.apne")));

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Fordring: ingen hardkodet tekst", async () => {
  const PL = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visFordring(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);
    // `th[scope="row"]` er UTELATT i fordringstabellen: den cellen bærer
    // kundenavnet, altså tenantens data — ikke en oversatt etikett.
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


// KVITTERINGEN SKAL OVERLEVE TEGNINGEN.
//
// Porten finnes fordi det var galt i alle fem flatene i klyngen:
// suksessmeldingen ble satt i skjemaets eget `utfall`, og `last()` bygde
// straks både panelet og skjemaet på nytt. Brukeren trykket, så skjermen
// blinke, og satt igjen uten å vite om det gikk bra. Skjermleseren hørte
// det (`meldLive`), men en seende bruker fikk ingenting.
//
// MUTASJONEN SOM DREPER DENNE: flytt kvitteringen tilbake inn i `kropp`.
test("Fordring: kvitteringen og panelet overlever tegningen", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visFordring(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 3);
  [...h.querySelectorAll("table")[1].querySelectorAll("tbody tr")][0]
    .querySelector("button").click();
  await vent(() => h.querySelectorAll("li").length >= 2);
  h.querySelector("#fo-bet-belop").value = "12";
  h.querySelector("#fo-bet-dato").value = "2026-08-11";
  h.querySelector("#fo-bet-belop").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("betaling"));
  // Det ANDRE kallet mot `hendelser` ER gjenåpningen.
  assert.ok(await vent(() => KALL.filter(
    (k) => k.sti.includes("/hendelser")).length >= 2),
    "panelet ble aldri gjenåpnet — porten måler ingenting");
  assert.ok(h.textContent.includes(t("ui.fordring.skjema.betaling_ok")),
    "kvitteringen forsvant i tegningen");
  assert.ok(h.textContent.includes("Nordvik AS · F-1001"),
    "panelet lukket seg etter en innbetaling");
});
