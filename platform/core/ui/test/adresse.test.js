// M-19 adresseflaten (112) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd på hver
//     skjerm flaten kan stå i.
//   * `modulen_slo_opp_eksternt`: flaten har INGEN «slå opp»-knapp og
//     ingen egen utgående kanal.
//   * `normalisering_uten_original`: det som VISES er originalen, og
//     den normaliserte formen kommer aldri ut av basen.
//   * `adresse_uten_kilde_og_metode`: kilden vises alltid med
//     adressen, utfallet alltid med metoden, og begrunnelsen blir
//     påkrevd i det utfallet ikke er «godkjent».
//   * `valideringskrav_hardkodet`: metodene er en AVKRYSNING fra det
//     lukkede settet — en tenant kan ikke skrive «oppslag».
//   * «Ikke kontrollert» er noe annet enn «godkjent» (WCAG 1.4.1 og
//     alminnelig ærlighet).
//   * En lesende økt ser registeret, men INGEN mutasjonskontroller.
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
  KILDER, METODER, UTFALL, adresseTekst, kildeTekst, kontrollTekst,
  visAdresse,
} from "../static/js/flater/adresse.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));

const S1 = "11111111-1111-1111-1111-111111111111";
const S2 = "22222222-2222-2222-2222-222222222222";
const V1 = "aaaaaaaa-1111-1111-1111-111111111111";

function locale(sprak) {
  return JSON.parse(readFileSync(
    join(HER, "..", "..", "..", "..", "locales", `${sprak}.json`),
    "utf-8"));
}

const BILDE = {
  sammendrag: {
    subjekter: 40, aktive: 12, med_adresse: 9, kontrollerte: 6,
    apne_funn: 4, apne_avvist: 2, har_krav: true, kravversjon: 2,
    vist: 2,
  },
  subjekter: [
    { subjekt_id: S1, ekstern_ref: "ORD-100", navn: "Kari Kunde",
      aktiv: true, versjon_id: V1, linje1: "Storgt.   5",
      linje2: null, postnr: "0155", poststed: "Oslo", land: "NO",
      gjelder_fra: "2026-01-10", kilde: "oppgitt_av_kunde",
      siste_metode: "visuell", siste_utfall: "godkjent",
      siste_kontrollert: "2026-01-12", versjoner: 2,
      apne_funn: ["utilstrekkelig_metode"] },
    { subjekt_id: S2, ekstern_ref: "ORD-200", navn: "Ola Kunde",
      aktiv: false, versjon_id: null, linje1: null, linje2: null,
      postnr: null, poststed: null, land: null, gjelder_fra: null,
      kilde: null, siste_metode: null, siste_utfall: null,
      siste_kontrollert: null, versjoner: 0,
      apne_funn: ["ukontrollert_adresse"] },
  ],
  krav: {
    ukontrollert_dogn: 14, kontroll_gyldig_dogn: 365,
    godkjente_metoder: ["bekreftet_av_kunde", "dokumentert"],
    versjon: 2, oppdatert: "2026-08-01T09:00:00+00:00",
    oppdatert_av: "kari",
  },
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    subjekter: 0, aktive: 0, med_adresse: 0, kontrollerte: 0,
    apne_funn: 0, apne_avvist: 0, har_krav: false, kravversjon: null,
    vist: 0,
  },
  subjekter: [], krav: null, request_id: "r-b",
};

const HISTORIKK = {
  subjekt_id: S1,
  versjoner: [
    { versjon_id: V1, linje1: "Storgt.   5", linje2: null,
      postnr: "0155", poststed: "Oslo", land: "NO",
      kilde: "oppgitt_av_kunde", kilde_ref: "evt_b1",
      gjelder_fra: "2026-01-10", notat: "renskrevet",
      registrert: "2026-01-10T09:00:00+00:00", registrert_av: "kari",
      endret: true, kontroller: 2, siste_utfall: "godkjent",
      siste_metode: "visuell", siste_kontrollert: "2026-01-12" },
    { versjon_id: "aaaaaaaa-0000-0000-0000-000000000000",
      linje1: "Storgata 5", linje2: null, postnr: "0155",
      poststed: "Oslo", land: "NO", kilde: "ordre",
      kilde_ref: "evt_a1", gjelder_fra: "2026-01-01",
      notat: "fra ordren",
      registrert: "2026-01-01T09:00:00+00:00", registrert_av: "kari",
      endret: false, kontroller: 0, siste_utfall: null,
      siste_metode: null, siste_kontrollert: null },
  ],
  request_id: "r-c",
};

let SVAR;
let SISTE;
let KALL;
let SVARSTATUS;
let TREGE;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  KALL.push({ sti, metode: (opts && opts.method) || "GET" });
  if (TREGE && TREGE.has(sti)) {
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setTimeout(r, 0));
    }
  }
  if (opts && opts.method === "POST") {
    SISTE = { sti, kropp: JSON.parse(opts.body), headers: opts.headers };
    if (SVARSTATUS && SVARSTATUS !== 200) {
      return { ok: false, status: SVARSTATUS,
        json: async () => ({ feil: "adresse_ulovlig_tilstand" }) };
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
  TREGE = new Set();
  return m;
}

function fullSvar() {
  return {
    "/v1/adresse": BILDE,
    [`/v1/adresse/${S1}/historikk`]: HISTORIKK,
    [`/v1/adresse/${S2}/historikk`]: { subjekt_id: S2, versjoner: [],
                                       request_id: "r-d" },
  };
}

// Tabellrekkefølgen: subjektene (0), kravene (1) og — når
// detaljpanelet står åpent — historikken (2).
function tabeller(h) {
  return [...h.querySelectorAll("table")];
}

async function apneForste(h) {
  await vent(() => tabeller(h).length >= 2);
  tabeller(h)[0].querySelectorAll("tbody tr")[0]
    .querySelector("button").click();
  await vent(() => tabeller(h).length >= 3);
}

// ---------------------------------------------------------------------
// normalisering_uten_original — ORIGINALEN ER DET SOM VISES
// ---------------------------------------------------------------------

test("Adresse: originalen vises uendret", () => {
  // TRE MELLOMROM STÅR SOM DE STO. Flaten renskriver ikke; det kunden
  // skrev er det eneste som kan forklare en feillevering i ettertid.
  assert.equal(
    adresseTekst({ linje1: "Storgt.   5", postnr: "0155",
                   poststed: "Oslo", land: "NO" }),
    "Storgt.   5, 0155 Oslo, NO");
  assert.equal(
    adresseTekst({ linje1: "Storgata 5", linje2: "c/o Hansen",
                   postnr: "0155", poststed: "Oslo", land: "NO" }),
    "Storgata 5, c/o Hansen, 0155 Oslo, NO");
  // «Ingen adresse ført» er et svar, ikke en tom celle.
  assert.equal(adresseTekst(null), t("ui.adresse.uten_adresse"));
  assert.equal(adresseTekst({}), t("ui.adresse.uten_adresse"));
});

test("Adresse: kontrollen vises aldri uten metoden sin", () => {
  assert.equal(kontrollTekst("godkjent", "dokumentert"),
    t("ui.adresse.utfall_ved")
      .replace("{utfall}", t("ui.adresse.utfall.godkjent"))
      .replace("{metode}", t("ui.adresse.metode.dokumentert")));
  // «Ikke kontrollert» er noe annet enn «godkjent».
  assert.equal(kontrollTekst(null, null), t("ui.adresse.uten_kontroll"));
  // …og «lot seg ikke kontrollere» er noe annet enn begge.
  assert.notEqual(kontrollTekst("ukontrollerbar", "visuell"),
                  kontrollTekst("avvist", "visuell"));
  assert.notEqual(kontrollTekst("ukontrollerbar", "visuell"),
                  t("ui.adresse.uten_kontroll"));
  assert.equal(kildeTekst(null), t("ui.adresse.uten_kilde"));
  assert.equal(kildeTekst("ordre"), t("ui.adresse.kilde.ordre"));
});

test("Adresse: begge språk navngir hver kilde, metode og utfall", () => {
  for (const sprak of ["nb", "en"]) {
    const tekster = locale(sprak);
    for (const k of KILDER) {
      assert.ok(tekster[`ui.adresse.kilde.${k}`],
        `${sprak} mangler kilden ${k}`);
    }
    for (const m of METODER) {
      assert.ok(tekster[`ui.adresse.metode.${m}`],
        `${sprak} mangler metoden ${m}`);
    }
    for (const u of UTFALL) {
      assert.ok(tekster[`ui.adresse.utfall.${u}`],
        `${sprak} mangler utfallet ${u}`);
    }
  }
});

// ---------------------------------------------------------------------
// modulen_slo_opp_eksternt — flatens halvdel
// ---------------------------------------------------------------------

test("Adresse: flaten slår ingenting opp", () => {
  const kilde = readFileSync(
    join(HER, "..", "static", "js", "flater", "adresse.js"), "utf8");
  // Kommentarene forklarer FRAVÆRET og må derfor ikke telle med.
  const uten = kilde.replace(/^\s*\/\/.*$/gm, "");
  for (const ord of ["oppslag", "slåOpp", "slaaOpp", "kartverket",
                     "geocod", "fetch(", "XMLHttpRequest",
                     "WebSocket", "sendBeacon", "http://", "https://"]) {
    assert.ok(!uten.toLowerCase().includes(ord.toLowerCase()),
      `flaten bærer «${ord}» — v1 slår ingenting opp`);
  }
  const api = readFileSync(
    join(HER, "..", "static", "js", "api.js"), "utf8");
  assert.ok(!/export const (slaaOppAdresse|validerAdresse)/.test(api));
  // ALLE FEM SKRIVEVEIENE sender en Idempotency-Key.
  for (const n of ["settAdressekrav", "registrerAdressesubjekt",
                   "registrerAdresse", "registrerAdressekontroll",
                   "settAdressesubjektAktiv"]) {
    const i = api.indexOf(`export const ${n} =`);
    assert.ok(i > 0, `${n} mangler i api.js`);
    const j = api.indexOf("\n\n", i);
    const kropp = api.slice(i, j === -1 ? api.length : j);
    assert.ok(/idem \|\| nyIdempotensnokkel\(\)/.test(kropp),
      `${n} sender ingen Idempotency-Key`);
  }
});

// ---------------------------------------------------------------------
// Skjermene — og axe på hver av dem
// ---------------------------------------------------------------------

test("Adresse: listen viser adresse, kilde og kontroll, axe rent",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visAdresse(h, ctx());
    await vent(() => tabeller(h).length >= 2);

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
    assert.equal(rader[0].querySelector('th[scope="row"]').textContent,
      "ORD-100");
    // ORIGINALEN, med mellomrommene i behold.
    assert.ok(rader[0].textContent.includes("Storgt.   5, 0155 Oslo, NO"));
    // KILDEN OG KONTROLLEN STÅR MED.
    assert.ok(rader[0].textContent.includes(
      t("ui.adresse.kilde.oppgitt_av_kunde")));
    assert.ok(rader[0].textContent.includes(
      t("ui.adresse.metode.visuell")));
    // MERKET ER TEKST (WCAG 1.4.1).
    assert.ok(rader[0].textContent.includes(t("ui.adresse.merke_svak")));
    // ET SUBJEKT UTEN ADRESSE SIER DET MED ORD.
    assert.ok(rader[1].textContent.includes(
      t("ui.adresse.uten_adresse")));
    assert.ok(rader[1].textContent.includes(
      t("ui.adresse.uten_kontroll")));
    assert.ok(rader[1].textContent.includes(
      t("ui.adresse.status.inaktiv")));

    // AVSLAGENE STÅR FOR SEG i sammendraget.
    assert.ok(h.textContent.includes(
      t("ui.adresse.apne_avvist").replace("{n}", "2")));
    assert.ok(h.textContent.includes(
      t("ui.adresse.avkortet").replace("{vist}", "2")));
    assert.ok(h.textContent.includes(t("ui.adresse.oversikt.hvorfor")));
  });

test("Adresse: tomt register sier hva som mangler, axe rent",
  async () => {
    SVAR = { "/v1/adresse": TOMT };
    const h = nyHoved();
    visAdresse(h, ctx());
    await vent(() => h.textContent.includes(t("ui.adresse.liste.ingen")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
    assert.ok(h.textContent.includes(t("ui.adresse.ingen_krav")));
    assert.ok(!h.textContent.includes(
      t("ui.adresse.apne_avvist").replace("{n}", "0")));
  });

// ---------------------------------------------------------------------
// adressehistorikk_overskrevet — HISTORIKKEN ER SKJERMEN
// ---------------------------------------------------------------------

test("Adresse: historikken merker adresseskiftet", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visAdresse(h, ctx());
  await apneForste(h);

  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));

  const rader = [...tabeller(h)[2].querySelectorAll("tbody tr")];
  assert.equal(rader.length, 2);
  // NYESTE ØVERST, og skiftet er merket MED ORD.
  assert.equal(rader[0].querySelector('th[scope="row"]').textContent,
    "2026-01-10");
  assert.ok(rader[0].textContent.includes(t("ui.adresse.merke_skifte")));
  assert.ok(rader[0].textContent.includes("evt_b1"));
  assert.ok(rader[0].textContent.includes("renskrevet"));
  // DEN FORRIGE LINJEN BLIR STÅENDE, uten skiftemerke, og med sin
  // EGEN kilde — den kom fra ordren, ikke fra kunden.
  assert.ok(!rader[1].textContent.includes(t("ui.adresse.merke_skifte")));
  assert.ok(rader[1].textContent.includes(t("ui.adresse.kilde.ordre")));
  // …og en versjon ingen kontrollerte sier nettopp det.
  assert.ok(rader[1].textContent.includes(t("ui.adresse.uten_kontroll")));
  // DEN NORMALISERTE FORMEN KOMMER ALDRI UT AV BASEN, og flaten viser
  // den derfor ikke — bare originalen.
  assert.ok(!h.textContent.toLowerCase().includes("normalisert"));
  assert.ok(h.textContent.includes("ORD-100 · Kari Kunde"));
});

test("Adresse: et subjekt uten adresse tar ikke imot en kontroll",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visAdresse(h, ctx());
    await vent(() => tabeller(h).length >= 2);
    tabeller(h)[0].querySelectorAll("tbody tr")[1]
      .querySelector("button").click();
    assert.ok(await vent(() => h.textContent.includes(
      t("ui.adresse.detalj.ingen"))), "tomheten ble aldri sagt");
    // ET DEAKTIVERT SUBJEKT TAR IKKE IMOT NYE ADRESSER…
    const vKnapp = h.querySelector("#ad-v-linje1").closest("form")
      .querySelector("button[type=submit]");
    assert.equal(vKnapp.disabled, true);
    // …OG EN KONTROLL TRENGER EN VERSJON Å GJELDE. Uten adresse finnes
    // det ingenting å kontrollere, og knappen sier det ved å stå død
    // framfor å gi en 404 når noen trykker.
    const kKnapp = h.querySelector("#ad-k-metode").closest("form")
      .querySelector("button[type=submit]");
    assert.equal(kKnapp.disabled, true);
    // …men det KAN aktiveres igjen.
    const aktiv = [...h.querySelectorAll("button[type=submit]")]
      .find((b) => b.textContent === t("ui.adresse.knapp.aktiver"));
    assert.ok(aktiv, "aktiveringsknappen mangler eller bærer feil tekst");
    assert.equal(aktiv.disabled, false);
  });

test("Adresse: en treg historikk tegnes ikke inn i et annet panel",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visAdresse(h, ctx());
    await vent(() => tabeller(h).length >= 2);
    TREGE.add(`/v1/adresse/${S1}/historikk`);
    const rader = [...tabeller(h)[0].querySelectorAll("tbody tr")];
    rader[0].querySelector("button").click();   // treg
    rader[1].querySelector("button").click();   // rask
    assert.ok(await vent(() => h.textContent.includes(
      t("ui.adresse.detalj.ingen"))), "Bs tomme historikk kom aldri");
    await vent(() => false, 40);
    assert.ok(h.textContent.includes(t("ui.adresse.detalj.ingen")),
      "den trege historikken ble tegnet inn i feil panel");
    assert.ok(!h.textContent.includes("renskrevet"),
      "ORD-100s linjer står i ORD-200s panel");
    assert.ok(h.textContent.includes("ORD-200 · Ola Kunde"));
  });

// ---------------------------------------------------------------------
// Skriveveiene
// ---------------------------------------------------------------------

test("Adresse: adressen sendes slik den ble skrevet", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visAdresse(h, ctx());
  await apneForste(h);
  // KILDEREFERANSEN ER PÅKREVD — en adresse uten kilde er en påstand.
  assert.equal(h.querySelector("#ad-v-kilderef").required, true);
  const kilder = [...h.querySelector("#ad-v-kilde").options]
    .map((o) => o.value);
  assert.deepEqual(kilder, KILDER);

  h.querySelector("#ad-v-linje1").value = "  Storgt.   5 ";
  h.querySelector("#ad-v-postnr").value = "0155";
  h.querySelector("#ad-v-poststed").value = "Oslo";
  h.querySelector("#ad-v-land").value = "no";
  h.querySelector("#ad-v-kilderef").value = "evt_ny";
  h.querySelector("#ad-v-fra").value = "2026-02-01";
  h.querySelector("#ad-v-notat").value = "oppgitt";
  h.querySelector("#ad-v-linje1").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("/versjon"));
  // FLATEN RETTER INGENTING. Mellomrommene går med; API-et trimmer
  // ytterkantene og basen regner normaliseringen — ingen av leddene
  // gjetter på hva kunden mente.
  assert.equal(SISTE.kropp.linje1, "  Storgt.   5 ");
  assert.equal(SISTE.kropp.land, "no");
  assert.equal(SISTE.kropp.kilde, "oppgitt_av_kunde");
  assert.equal(SISTE.kropp.kilde_ref, "evt_ny");
  // …og en tom andre linje sendes som null, ikke som "".
  assert.equal(SISTE.kropp.linje2, null);
  assert.ok(SISTE.headers["Idempotency-Key"]);
  // DEN NORMALISERTE FORMEN ER IKKE ET FELT KLIENTEN KAN SENDE.
  assert.ok(!("linje1_normalisert" in SISTE.kropp));
});

test("Adresse: begrunnelsen blir påkrevd når utfallet ikke er godkjent",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visAdresse(h, ctx());
    await apneForste(h);
    const metoder = [...h.querySelector("#ad-k-metode").options]
      .map((o) => o.value);
    // INGEN AV METODENE ER ET OPPSLAG.
    assert.deepEqual(metoder, METODER);
    assert.ok(!metoder.includes("oppslag"));
    const utfall = h.querySelector("#ad-k-utfall");
    assert.deepEqual([...utfall.options].map((o) => o.value), UTFALL);

    const grunn = h.querySelector("#ad-k-grunn");
    assert.equal(grunn.required, false, "godkjent krever ingen grunn");
    for (const u of ["avvist", "ukontrollerbar"]) {
      utfall.value = u;
      utfall.dispatchEvent(new window.Event("change"));
      assert.equal(grunn.required, true, u);
    }
    utfall.value = "godkjent";
    utfall.dispatchEvent(new window.Event("change"));
    assert.equal(grunn.required, false);

    // KONTROLLØREN ER PÅKREVD: uten hvem er «validert» en påstand.
    assert.equal(h.querySelector("#ad-k-hvem").required, true);
    h.querySelector("#ad-k-hvem").value = "Kari Kontrollør";
    h.querySelector("#ad-k-kilderef").value = "k1";
    h.querySelector("#ad-k-dato").value = "2026-01-12";
    grunn.closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE && SISTE.sti.includes("/kontroll"));
    // KONTROLLEN HENGES PÅ VERSJONEN, ikke på subjektet — flytter
    // kunden, sier den gamle godkjenningen ingenting om den nye
    // adressen.
    assert.ok(SISTE.sti.includes(`/v1/adresse/versjon/${V1}/kontroll`));
    assert.equal(SISTE.kropp.kontrollor, "Kari Kontrollør");
    assert.equal(SISTE.kropp.begrunnelse, null);
  });

test("Adresse: kravene er forhåndsutfylt, metodene er avkrysning",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visAdresse(h, ctx());
    await vent(() => !!h.querySelector("#ad-k-ukontrollert"));
    assert.equal(h.querySelector("#ad-k-ukontrollert").value, "14");
    assert.equal(h.querySelector("#ad-k-gyldig").value, "365");
    // METODENE ER AVKRYSNING FRA DET LUKKEDE SETTET. En tenant kan
    // ikke skrive «oppslag» — det er ikke et hinder for
    // brukervennlighet, det ER v1-dommen.
    for (const m of METODER) {
      assert.ok(h.querySelector(`#ad-k-m-${m}`), `mangler boks for ${m}`);
    }
    assert.equal(h.querySelectorAll("#ad-k-m-oppslag").length, 0);
    assert.equal(h.querySelector("#ad-k-m-bekreftet_av_kunde").checked,
                 true);
    assert.equal(h.querySelector("#ad-k-m-visuell").checked, false);
    // …og fieldsettet har en legend, så avkrysningene har en gruppe.
    const fs = h.querySelector("#ad-k-m-visuell").closest("fieldset");
    assert.ok(fs && fs.querySelector("legend"));

    h.querySelector("#ad-k-m-visuell").checked = true;
    h.querySelector("#ad-k-gyldig").value = "180";
    h.querySelector("#ad-k-gyldig").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE && SISTE.sti.includes("/krav"));
    assert.equal(SISTE.kropp.kontroll_gyldig_dogn, 180);
    assert.equal(SISTE.kropp.ukontrollert_dogn, 14);
    assert.deepEqual(SISTE.kropp.godkjente_metoder,
      ["visuell", "bekreftet_av_kunde", "dokumentert"]);
    for (const sprak of ["nb", "en"]) {
      const hjelp = locale(sprak)["ui.adresse.krav.metoder_hjelp"];
      assert.ok(/risiko|risk/i.test(hjelp),
        `${sprak}: hjelpeteksten sier ikke hva som står på spill`);
    }
  });

test("Adresse: kvitteringen og panelet overlever tegningen",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visAdresse(h, ctx());
    await apneForste(h);
    h.querySelector("#ad-v-linje1").value = "Nyveien 1";
    h.querySelector("#ad-v-postnr").value = "0001";
    h.querySelector("#ad-v-poststed").value = "Oslo";
    h.querySelector("#ad-v-kilderef").value = "evt_k";
    h.querySelector("#ad-v-fra").value = "2026-02-01";
    h.querySelector("#ad-v-notat").value = "x";
    h.querySelector("#ad-v-linje1").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    assert.ok(await vent(() => KALL.filter(
      (k) => k.sti.includes("/historikk")).length >= 2),
      "panelet ble aldri gjenåpnet — porten måler ingenting");
    assert.ok(h.textContent.includes(t("ui.adresse.skjema.adresse_ok")),
      "kvitteringen forsvant i tegningen");
    assert.ok(h.textContent.includes("ORD-100 · Kari Kunde"),
      "panelet lukket seg etter en føring");
  });

test("Adresse: en 409 sier hva registeret nektet", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visAdresse(h, ctx());
  await vent(() => !!h.querySelector("#ad-ny-ref"));
  SVARSTATUS = 409;
  h.querySelector("#ad-ny-ref").value = "ORD-100";
  h.querySelector("#ad-ny-navn").value = "Dublett";
  const skjema = h.querySelector("#ad-ny-ref").closest("form");
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.ok(await vent(() => h.textContent.includes(
    t("ui.adresse.feil.tilstand"))), "tilstandsfeilen ble ikke vist");
  assert.ok(!h.textContent.includes(t("ui.adresse.feil.generell")));
  assert.ok(h.querySelector('[role="alert"]'));
  assert.equal(skjema.querySelector("button[type=submit]").disabled, false);
});

// ---------------------------------------------------------------------
// Scope og språk
// ---------------------------------------------------------------------

test("Adresse: en lesende økt ser registeret, men ingen kontroller",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visAdresse(h, ctx(["okonomi:read"]));
    await vent(() => tabeller(h).length >= 2);
    assert.ok(h.textContent.includes("ORD-100"));
    assert.ok(h.textContent.includes("Storgt.   5, 0155 Oslo, NO"));
    assert.equal(h.querySelectorAll("form").length, 0);
    assert.equal(h.querySelectorAll("input, select, textarea").length, 0);
    assert.ok([...h.querySelectorAll("button")].some(
      (b) => b.textContent === t("ui.adresse.knapp.apne")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Adresse: ingen hardkodet tekst", async () => {
  const PL = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visAdresse(h, ctx());
    await apneForste(h);
    // `th[scope="row"]` er UTELATT: den cellen bærer referansen, datoen
    // og kravnavnet — altså tenantens egne data.
    for (const node of h.querySelectorAll(
      'h2, h3, h4, label, caption, legend, th[scope="col"], button,'
      + " option")) {
      const s = node.textContent.trim();
      if (!s) continue;
      assert.ok(s.startsWith("PL_"), `hardkodet tekst: «${s}»`);
    }
  } finally {
    settI18nForTest(NB, "nb");
  }
});
