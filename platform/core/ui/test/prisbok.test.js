// M-26 prisbokflaten (108) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd på hver
//     skjerm flaten kan stå i.
//   * `modulen_genererte_tilbud` og `modulen_signerte_attestasjon`:
//     flaten har INGEN «generer tilbud»-knapp og ingen signatur.
//   * `modulen_satte_pris`: flaten ganger ikke og indekserer ikke —
//     `listepris_ore` er tallet et menneske skriver.
//   * `pris_uten_versjon`: HISTORIKKEN ER EN EGEN SKJERM, med versjon,
//     gyldighet OG begrunnelse. Uten den er «hva sto i boka da vi ga
//     det tilbudet» et spørsmål ingen kan svare på.
//   * `klausul_endret_i_stillhet`: hashen VISES, men SENDES ALDRI.
//   * `belop_i_flyttall`: beløp i øre og rabatt i promille, begge i
//     heltallsaritmetikk.
//   * ÅPEN GYLDIGHET OG UTLØP ER TEKST, ikke bare farge (WCAG 1.4.1).
//   * En lesende økt ser boka, men INGEN mutasjonskontroller.
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
  belopTekst, gyldighetTekst, promilleTilFelt, prosentTekst, tilOre,
  tilPromille, utlopTekst, visPrisbok,
} from "../static/js/flater/prisbok.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));

const P1 = "11111111-1111-1111-1111-111111111111";
const P2 = "22222222-2222-2222-2222-222222222222";

function locale(sprak) {
  return JSON.parse(readFileSync(
    join(HER, "..", "..", "..", "..", "locales", `${sprak}.json`),
    "utf-8"));
}

const BILDE = {
  sammendrag: {
    produkter: 40, aktive: 12, med_gyldig_pris: 9, klausuler: 3,
    standardklausuler: 2, apne_funn: 4, har_terskel: true,
    terskelversjon: 2, vist: 2,
  },
  produkter: [
    { produkt_id: P1, kode: "K-100", navn: "Konsulenttime",
      enhet: "time", aktiv: true, versjon: 2, listepris_ore: 165000,
      valuta: "NOK", gyldig_fra: "2026-07-01", gyldig_til: null,
      dogn_til_utlop: null, versjoner: 2, apne_funn: [] },
    { produkt_id: P2, kode: "K-200", navn: "Kranleie", enhet: "døgn",
      aktiv: false, versjon: 1, listepris_ore: null, valuta: null,
      gyldig_fra: null, gyldig_til: null, dogn_til_utlop: null,
      versjoner: 0, apne_funn: ["uten_gyldig_pris"] },
  ],
  klausuler: [
    { kode: "ansvar", versjon: 2, tittel: "Ansvarsbegrensning",
      tekst: "Vårt ansvar er begrenset til kontraktssummen.",
      tekst_hash: "abcdef0123456789abcdef", standard: true,
      gyldig_fra: "2026-07-01", gyldig_til: null },
    { kode: "frist", versjon: 1, tittel: "Leveringsfrist",
      tekst: "Leveringsfristen er veiledende.",
      tekst_hash: "0f0f0f0f0f0f0f0f0f0f0f", standard: false,
      gyldig_fra: "2026-01-01", gyldig_til: "2026-07-01" },
  ],
  terskler: {
    rabattgrense_promille: 125, utlop_varsel_dogn: 30,
    uten_pris_dogn: 7, versjon: 2,
    oppdatert: "2026-08-01T09:00:00+00:00", oppdatert_av: "kari",
  },
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    produkter: 0, aktive: 0, med_gyldig_pris: 0, klausuler: 0,
    standardklausuler: 0, apne_funn: 0, har_terskel: false,
    terskelversjon: null, vist: 0,
  },
  produkter: [], klausuler: [], terskler: null, request_id: "r-b",
};

const HISTORIKK = {
  produkt_id: P1,
  versjoner: [
    { versjon: 2, listepris_ore: 165000, valuta: "NOK",
      gyldig_fra: "2026-07-01", gyldig_til: null,
      begrunnelse: "indeksregulering 2026",
      opprettet: "2026-06-20T09:00:00+00:00", opprettet_av: "kari" },
    { versjon: 1, listepris_ore: 150000, valuta: "NOK",
      gyldig_fra: "2026-01-01", gyldig_til: "2026-07-01",
      begrunnelse: "startpris", opprettet: "2026-01-02T09:00:00+00:00",
      opprettet_av: "ola" },
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
        json: async () => ({ feil: "prisbok_ulovlig_tilstand" }) };
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
    "/v1/prisbok": BILDE,
    [`/v1/prisbok/${P1}/historikk`]: HISTORIKK,
    [`/v1/prisbok/${P2}/historikk`]: { produkt_id: P2, versjoner: [],
                                       request_id: "r-d" },
  };
}

// Tabellrekkefølgen: produktene (0), klausulene (1), tersklene (2) og —
// når detaljpanelet står åpent — historikken (3).
function tabeller(h) {
  return [...h.querySelectorAll("table")];
}

async function apneForste(h) {
  await vent(() => tabeller(h).length >= 3);
  tabeller(h)[0].querySelectorAll("tbody tr")[0]
    .querySelector("button").click();
  await vent(() => tabeller(h).length >= 4);
}

// ---------------------------------------------------------------------
// belop_i_flyttall — ØRE OG PROMILLE
// ---------------------------------------------------------------------

test("Prisbok: beløp og rabatt i heltallsaritmetikk", () => {
  assert.equal(belopTekst(0), "0,00");
  assert.equal(belopTekst(165000), "1650,00");
  // DEN SOM AVSLØRER `/100`.
  assert.equal(belopTekst(123456799), "1234567,99");
  assert.equal(belopTekst(-5), "-0,05");
  assert.equal(belopTekst(12.5), "—");
  assert.equal(belopTekst(null), "—");

  // RABATTGRENSEN ER PROMILLE. 12,5 % som flyttall er 0.125 —
  // og 0.1 + 0.025 er ikke 0.125 i binær flyttallsaritmetikk.
  assert.equal(prosentTekst(125), "12,5 %");
  assert.equal(prosentTekst(0), "0,0 %");
  assert.equal(prosentTekst(1000), "100,0 %");
  assert.equal(prosentTekst(2.5), "—");

  assert.equal(tilOre("1650.00"), 165000);
  assert.equal(tilOre("8.15"), 815);
  assert.equal(tilOre("to"), null);
  assert.equal(tilPromille("12.5"), 125);
  assert.equal(tilPromille("10"), 100);
  assert.equal(tilPromille("x"), null);
  // …og tilbake, uten divisjon.
  assert.equal(promilleTilFelt(125), "12.5");
  assert.equal(promilleTilFelt(100), "10.0");
  assert.equal(promilleTilFelt(null), "");
});

// ---------------------------------------------------------------------
// Gyldigheten og utløpet SOM ORD
// ---------------------------------------------------------------------

test("Prisbok: åpen gyldighet sies med ord", () => {
  assert.equal(gyldighetTekst("2026-07-01", null),
    t("ui.prisbok.gyldig_apen").replace("{fra}", "2026-07-01"));
  assert.equal(gyldighetTekst("2026-01-01", "2026-07-01"),
    t("ui.prisbok.gyldig_til").replace("{fra}", "2026-01-01")
      .replace("{til}", "2026-07-01"));
  // INGEN PRIS ER IKKE EN TOM CELLE: «gratis» og «ingen pris ført» er
  // to helt forskjellige svar.
  assert.equal(gyldighetTekst(null, null), t("ui.prisbok.uten_pris"));
});

test("Prisbok: utløpet er tekst, og entall har egen nøkkel", () => {
  assert.equal(utlopTekst(30),
    t("ui.prisbok.om_dogn").replace("{dogn}", "30"));
  assert.equal(utlopTekst(1), t("ui.prisbok.om_ett_dogn"));
  assert.equal(utlopTekst(0), t("ui.prisbok.utloper_i_dag"));
  assert.equal(utlopTekst(-1), t("ui.prisbok.utlopt_ett_dogn"));
  assert.equal(utlopTekst(-4),
    t("ui.prisbok.utlopt_for").replace("{dogn}", "4"));
  assert.equal(utlopTekst(null), "—");
});

test("Prisbok: begge språk har entallsnøklene", () => {
  for (const sprak of ["nb", "en"]) {
    const tekster = locale(sprak);
    for (const n of ["ui.prisbok.om_ett_dogn",
                     "ui.prisbok.utlopt_ett_dogn",
                     "ui.prisbok.utloper_i_dag"]) {
      assert.ok(tekster[n], `${sprak} mangler ${n}`);
      assert.ok(!tekster[n].includes("{dogn}"),
        `${sprak}: ${n} har en tallplass — da er den ikke entallsformen`);
    }
  }
});

// ---------------------------------------------------------------------
// modulen_genererte_tilbud / modulen_signerte_attestasjon /
// modulen_satte_pris — flatens halvdel
// ---------------------------------------------------------------------

test("Prisbok: flaten har ingen tilbudsknapp og ingen signatur", () => {
  const kilde = readFileSync(
    join(HER, "..", "static", "js", "flater", "prisbok.js"), "utf8");
  // Kommentarene forklarer FRAVÆRET og må derfor ikke telle med.
  const uten = kilde.replace(/^\s*\/\/.*$/gm, "");
  for (const ord of ["tilbud", "attester", "signer", "indeksregul",
                     "foreslå", "/v1/dokumentmal", "genererTilbud"]) {
    assert.ok(!uten.toLowerCase().includes(ord.toLowerCase()),
      `flaten bærer «${ord}» — v1 er boka, ikke tilbudet`);
  }
  const api = readFileSync(
    join(HER, "..", "static", "js", "api.js"), "utf8");
  assert.ok(!/export const genererTilbud/.test(api));
  assert.ok(!/export function genererTilbud/.test(api));
});

test("Prisbok: prisfeltet er et tall et menneske skriver", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visPrisbok(h, ctx());
  await apneForste(h);
  const felt = h.querySelector("#pb-pris-belop");
  // INGEN FORHÅNDSUTFYLLING: flaten foreslår ingen pris, heller ikke
  // «forrige gang pluss indeks».
  assert.equal(felt.value, "");
  assert.equal(felt.type, "number");
  assert.equal(felt.required, true);
  assert.equal(felt.min, "0");
  // …og BEGRUNNELSEN er påkrevd i skjemaet, av samme grunn som i basen.
  assert.equal(h.querySelector("#pb-pris-grunn").required, true);
  for (const sprak of ["nb", "en"]) {
    const hjelp = locale(sprak)["ui.prisbok.skjema.begrunnelse_hjelp"];
    assert.ok(/etterprøv|review|verif|audit/i.test(hjelp),
      `${sprak}: hjelpeteksten sier ikke hva begrunnelsen er til for`);
  }
});

// ---------------------------------------------------------------------
// Skjermene — og axe på hver av dem
// ---------------------------------------------------------------------

test("Prisbok: listen viser prisen, gyldigheten og merkene, axe rent",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visPrisbok(h, ctx());
    await vent(() => tabeller(h).length >= 3);

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));

    for (const tb of tabeller(h)) {
      assert.ok(tb.querySelector("caption"), "tabell uten caption");
      assert.ok(tb.querySelectorAll('th[scope="col"]').length >= 2);
      assert.ok(tb.closest(".tablewrap"),
        "tabellen mangler sidescrollens container");
    }

    const rader = [...tabeller(h)[0].querySelectorAll("tbody tr")];
    assert.equal(rader.length, 2);
    // KODEN NAVNGIR RADEN — det er den et tilbud siterer.
    assert.equal(rader[0].querySelector('th[scope="row"]').textContent,
      "K-100");
    assert.ok(rader[0].textContent.includes("1650,00"));
    assert.ok(rader[0].textContent.includes(
      t("ui.prisbok.gyldig_apen").replace("{fra}", "2026-07-01")));
    // ET PRODUKT UTEN PRIS SIER DET MED ORD, ikke med «0,00».
    assert.ok(rader[1].textContent.includes(t("ui.prisbok.uten_pris")));
    assert.ok(!rader[1].textContent.includes("0,00"));
    // MERKET ER TEKST (WCAG 1.4.1).
    assert.ok(rader[1].textContent.includes(
      t("ui.prisbok.merke_uten_pris")));
    assert.ok(rader[1].textContent.includes(t("ui.prisbok.status.inaktiv")));

    // SAMMENDRAGET TELLER ALT, og avkortingen sies høyt.
    assert.ok(h.textContent.includes(
      t("ui.prisbok.avkortet").replace("{vist}", "2")));
    assert.ok(!h.textContent.includes(t("ui.prisbok.ingen_terskler")));
    // …og HVORFOR BOKA FINNES står på flaten.
    assert.ok(h.textContent.includes(t("ui.prisbok.oversikt.hvorfor")));
  });

test("Prisbok: en pris som utløper sier hvor mange døgn det er igjen",
  async () => {
    SVAR = { ...fullSvar(), "/v1/prisbok": { ...BILDE, produkter: [
      { ...BILDE.produkter[0], dogn_til_utlop: 4,
        gyldig_til: "2026-09-06",
        apne_funn: ["pris_utloper_snart"] }] } };
    const h = nyHoved();
    visPrisbok(h, ctx());
    await vent(() => tabeller(h).length >= 3);
    const rad = tabeller(h)[0].querySelector("tbody tr");
    assert.ok(rad.textContent.includes(t("ui.prisbok.merke_utloper")));
    assert.ok(rad.textContent.includes(
      t("ui.prisbok.om_dogn").replace("{dogn}", "4")),
      "merket sier ikke hvor lenge prisen står");
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Prisbok: tom bok sier hva som mangler, axe rent", async () => {
  SVAR = { "/v1/prisbok": TOMT };
  const h = nyHoved();
  visPrisbok(h, ctx());
  await vent(() => h.textContent.includes(t("ui.prisbok.liste.ingen")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  assert.ok(h.textContent.includes(t("ui.prisbok.klausul.ingen")));
  assert.ok(h.textContent.includes(t("ui.prisbok.ingen_terskler")));
});

// ---------------------------------------------------------------------
// pris_uten_versjon — HISTORIKKEN ER SKJERMEN SOM BETYR NOE
// ---------------------------------------------------------------------

test("Prisbok: historikken svarer på hva som sto i boka da", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visPrisbok(h, ctx());
  await apneForste(h);

  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));

  const rader = [...tabeller(h)[3].querySelectorAll("tbody tr")];
  assert.equal(rader.length, 2);
  // NYESTE ØVERST, og hver rad bærer versjon, pris, gyldighet OG
  // begrunnelse. Uten begrunnelsen er prisendringen en beslutning
  // ingen kan etterprøve.
  assert.equal(rader[0].querySelector('th[scope="row"]').textContent, "2");
  assert.ok(rader[0].textContent.includes("1650,00"));
  assert.ok(rader[0].textContent.includes("indeksregulering 2026"));
  assert.ok(rader[0].textContent.includes(
    t("ui.prisbok.gyldig_apen").replace("{fra}", "2026-07-01")));
  assert.ok(rader[0].textContent.includes("kari"));
  // DEN GAMLE VERSJONEN STÅR DER FORTSATT, med sin lukkede gyldighet.
  assert.equal(rader[1].querySelector('th[scope="row"]').textContent, "1");
  assert.ok(rader[1].textContent.includes("1500,00"));
  assert.ok(rader[1].textContent.includes(
    t("ui.prisbok.gyldig_til").replace("{fra}", "2026-01-01")
      .replace("{til}", "2026-07-01")));
  assert.ok(h.textContent.includes("K-100 · Konsulenttime · time"));
});

test("Prisbok: et produkt uten prishistorikk sier det", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visPrisbok(h, ctx());
  await vent(() => tabeller(h).length >= 3);
  tabeller(h)[0].querySelectorAll("tbody tr")[1]
    .querySelector("button").click();
  assert.ok(await vent(() => h.textContent.includes(
    t("ui.prisbok.detalj.ingen"))), "tomheten ble aldri sagt");
  // ET INAKTIVT PRODUKT TAR IKKE IMOT NY PRIS…
  const pris = h.querySelector("#pb-pris-belop").closest("form")
    .querySelector("button[type=submit]");
  assert.equal(pris.disabled, true);
  // …men det KAN aktiveres igjen, så den knappen står levende.
  const aktiv = [...h.querySelectorAll("button[type=submit]")]
    .find((b) => b.textContent === t("ui.prisbok.knapp.aktiver"));
  assert.ok(aktiv, "aktiveringsknappen mangler eller bærer feil tekst");
  assert.equal(aktiv.disabled, false);
});

// ---------------------------------------------------------------------
// klausul_endret_i_stillhet — hashen VISES, men SENDES ALDRI
// ---------------------------------------------------------------------

test("Prisbok: klausulen viser hashen og sender den ikke", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visPrisbok(h, ctx());
  await vent(() => tabeller(h).length >= 3);
  // HASHEN STÅR I TABELLEN, avkortet: det er den
  // `laste_klausuler_uendret` til slutt måles mot.
  const rad = tabeller(h)[1].querySelector("tbody tr");
  assert.ok(rad.textContent.includes("abcdef012345"));
  assert.ok(rad.textContent.includes(t("ui.prisbok.ja")));

  h.querySelector("#pb-kl-kode").value = "ansvar";
  h.querySelector("#pb-kl-tittel").value = "Ansvarsbegrensning";
  h.querySelector("#pb-kl-tekst").value = "Ny tekst.";
  h.querySelector("#pb-kl-standard").checked = true;
  h.querySelector("#pb-kl-fra").value = "2027-01-01";
  h.querySelector("#pb-kl-kode").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("/klausul"));
  assert.equal(SISTE.kropp.tekst, "Ny tekst.");
  assert.equal(SISTE.kropp.standard, true);
  // INGEN HASH: den regnes i basen, av teksten selv.
  assert.ok(!("tekst_hash" in SISTE.kropp));
  assert.ok(!JSON.stringify(SISTE.kropp).includes("hash"));
  assert.ok(SISTE.headers["Idempotency-Key"]);
});

// ---------------------------------------------------------------------
// Skriveveiene
// ---------------------------------------------------------------------

test("Prisbok: prisen sendes i ØRE", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visPrisbok(h, ctx());
  await apneForste(h);
  h.querySelector("#pb-pris-belop").value = "1650.00";
  h.querySelector("#pb-pris-fra").value = "2027-01-01";
  h.querySelector("#pb-pris-grunn").value = "indeks";
  h.querySelector("#pb-pris-belop").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("/pris"));
  assert.equal(SISTE.kropp.listepris_ore, 165000);
  assert.equal(SISTE.kropp.valuta, "NOK");
  assert.equal(SISTE.kropp.begrunnelse, "indeks");
});

test("Prisbok: rabattgrensen sendes i PROMILLE", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visPrisbok(h, ctx());
  await vent(() => !!h.querySelector("#pb-t-rabatt"));
  // FELTET ER FORHÅNDSUTFYLT MED DET SOM GJELDER — en terskel man må
  // gjette på blir satt feil.
  assert.equal(h.querySelector("#pb-t-rabatt").value, "12.5");
  assert.equal(h.querySelector("#pb-t-varsel").value, "30");
  h.querySelector("#pb-t-rabatt").value = "10.5";
  h.querySelector("#pb-t-rabatt").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("/terskler"));
  assert.equal(SISTE.kropp.rabattgrense_promille, 105);
  assert.equal(SISTE.kropp.utlop_varsel_dogn, 30);
  assert.equal(SISTE.kropp.uten_pris_dogn, 7);
});

test("Prisbok: kvitteringen og panelet overlever tegningen", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visPrisbok(h, ctx());
  await apneForste(h);
  h.querySelector("#pb-pris-belop").value = "10";
  h.querySelector("#pb-pris-fra").value = "2027-01-01";
  h.querySelector("#pb-pris-grunn").value = "x";
  h.querySelector("#pb-pris-belop").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.ok(await vent(() => KALL.filter(
    (k) => k.sti.includes("/historikk")).length >= 2),
    "panelet ble aldri gjenåpnet — porten måler ingenting");
  assert.ok(h.textContent.includes(t("ui.prisbok.skjema.pris_ok")),
    "kvitteringen forsvant i tegningen");
  assert.ok(h.textContent.includes("K-100 · Konsulenttime · time"),
    "panelet lukket seg etter en prisføring");
});

test("Prisbok: en 409 sier hva boka nektet", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visPrisbok(h, ctx());
  await vent(() => !!h.querySelector("#pb-ny-kode"));
  SVARSTATUS = 409;
  h.querySelector("#pb-ny-kode").value = "K-100";
  h.querySelector("#pb-ny-navn").value = "Dublett";
  h.querySelector("#pb-ny-enhet").value = "stk";
  const skjema = h.querySelector("#pb-ny-kode").closest("form");
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.ok(await vent(() => h.textContent.includes(
    t("ui.prisbok.feil.tilstand"))), "tilstandsfeilen ble ikke vist");
  assert.ok(!h.textContent.includes(t("ui.prisbok.feil.generell")));
  assert.ok(h.querySelector('[role="alert"]'));
  assert.equal(skjema.querySelector("button[type=submit]").disabled, false);
});

// ---------------------------------------------------------------------
// Scope og språk
// ---------------------------------------------------------------------

test("Prisbok: en lesende økt ser boka, men ingen kontroller",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visPrisbok(h, ctx(["okonomi:read"]));
    await vent(() => tabeller(h).length >= 3);
    assert.ok(h.textContent.includes("K-100"));
    assert.ok(h.textContent.includes("abcdef012345"));
    assert.equal(h.querySelectorAll("form").length, 0);
    assert.equal(h.querySelectorAll("input, select, textarea").length, 0);
    // …men historikken er en LESNING, og den står åpen.
    assert.ok([...h.querySelectorAll("button")].some(
      (b) => b.textContent === t("ui.prisbok.knapp.apne")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Prisbok: ingen hardkodet tekst", async () => {
  const PL = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visPrisbok(h, ctx());
    await apneForste(h);
    // `th[scope="row"]` er UTELATT: den cellen bærer produktkoden og
    // versjonsnummeret, altså tenantens egne data.
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
