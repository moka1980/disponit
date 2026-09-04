// M-46 anbudsflaten (118) — flateporten (jsdom + axe).
//
// Portene her måler de to FRAVÆRENE modulen hviler på:
//
//   * `modulen_sendte_tilbud`: flaten har ingen «send inn»-knapp. Et
//     innsendt tilbud er bindende, og fristen gjør det irreversibelt.
//     «Klar til gjennomgang» sier selv at den ikke sender noe.
//   * `utkastpunkt_uten_kilde`: punktskjemaet krever et kildedokument
//     fra en nedtrekksliste, og lista inneholder BARE dokumenter som
//     er gyldige nå. Det finnes ingen «skriv fritt»-vei — fordi
//     `utkastpunkt` i 118 ikke har en kolonne å legge en kildeløs
//     påstand i.
//
// OG ÉN TING FLATEN MÅ VISE: et udekket krav, som udekket. En flate
// som filtrerte dem bort ville skjult nettopp det som må gjøres — og
// det er det udekkede ABSOLUTTE kravet som gjør et tilbud avvist.
//
// Ingen delt fixture (m16-formen): hver test bygger sin egen skjerm.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import {
  ANBUDSKILDER, DOKUMENTTYPER, KRAVTYPER, anbudTabell, dekningTekst,
  feltTilOre, fristTekst, kildeTabell, kravTabell, oreTekst,
  oreTilFelt, visAnbud,
} from "../static/js/flater/anbud.js";

settI18nForTest(NB, "nb");

const A1 = "11111111-1111-1111-1111-111111111111";
const A2 = "22222222-2222-2222-2222-222222222222";
const K1 = "aaaaaaaa-1111-1111-1111-111111111111";
const K2 = "aaaaaaaa-2222-2222-2222-222222222222";
const U1 = "bbbbbbbb-1111-1111-1111-111111111111";
const D1 = "cccccccc-1111-1111-1111-111111111111";
const D2 = "cccccccc-2222-2222-2222-222222222222";

const BILDE = {
  sammendrag: {
    anbud: 12, aktive: 8, med_utkast: 5, klare: 2,
    udekkede_absolutte: 3, naermeste_frist: "2026-09-20T12:00:00+00:00",
    apne_funn: 4, kilder: 2, utlopte_kilder: 1, har_profil: true,
    profilversjon: 2, vist: 2,
  },
  anbud: [
    { anbud_id: A1, ekstern_ref: "DOF-1", kilde: "doffin",
      tittel: "Drift av fagsystem", oppdragsgiver: "Oslo kommune",
      nace_kode: "62.010", geografi: "Oslo", verdi_ore: 500000000,
      frist: "2026-09-20T12:00:00+00:00", aktiv: true,
      dogn_til_frist: 16, antall_krav: 2, absolutte_krav: 1,
      udekkede_absolutte: 1, siste_utkast: 1, klar: false,
      apne_funn: 2 },
    { anbud_id: A2, ekstern_ref: "TED-9", kilde: "ted",
      tittel: "Rammeavtale", oppdragsgiver: "Bergen kommune",
      nace_kode: "62.020", geografi: "Bergen", verdi_ore: null,
      frist: "2026-08-01T12:00:00+00:00", aktiv: false,
      dogn_til_frist: -34, antall_krav: 0, absolutte_krav: 0,
      udekkede_absolutte: 0, siste_utkast: null, klar: false,
      apne_funn: 1 },
  ],
  kilder: [
    { kilde_id: D1, tittel: "ISO 9001-sertifikat",
      dokumenttype: "sertifikat", gyldig_til: "2030-01-01",
      innhold_sha256: "a".repeat(64),
      registrert: "2026-01-01T09:00:00+00:00", registrert_av: "kari",
      gyldig_naa: true, brukt_i_punkter: 1 },
    { kilde_id: D2, tittel: "Gammel attest", dokumenttype: "attest",
      gyldig_til: "2020-01-01", innhold_sha256: "b".repeat(64),
      registrert: "2019-01-01T09:00:00+00:00", registrert_av: "kari",
      gyldig_naa: false, brukt_i_punkter: 0 },
  ],
  profil: {
    nace_koder: ["62.010"], geografi: ["Oslo"], min_verdi_ore: 0,
    maks_verdi_ore: 100000000000, frist_varsel_dogn: 14,
    kilde_gyldig_dogn: 365, versjon: 2,
    oppdatert: "2026-08-01T09:00:00+00:00", oppdatert_av: "kari",
  },
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    anbud: 0, aktive: 0, med_utkast: 0, klare: 0,
    udekkede_absolutte: 0, naermeste_frist: null, apne_funn: 0,
    kilder: 0, utlopte_kilder: 0, har_profil: false,
    profilversjon: null, vist: 0,
  },
  anbud: [], kilder: [], profil: null, request_id: "r-b",
};

const UTKAST = {
  anbud_id: A1,
  utkast: [
    { utkast_id: U1, versjon: 1, klar_til_gjennomgang: false,
      klar_ts: null, klar_av: null,
      opprettet: "2026-09-01T09:00:00+00:00", opprettet_av: "kari",
      antall_punkter: 1 },
  ],
  request_id: "r-c",
};

const KRAV = {
  anbud_id: A1, utkast_id: U1,
  krav: [
    { krav_id: K1, kravnummer: "K1", kravtekst: "ISO 9001",
      kravtype: "sertifisering", absolutt: true, punkt_id: null,
      sitat: null, sidereferanse: null, kilde_id: null,
      kildetittel: null, kilde_gyldig_til: null },
    { krav_id: K2, kravnummer: "K2", kravtekst: "Tre referanser",
      kravtype: "erfaring", absolutt: false,
      punkt_id: "dddddddd-1111-1111-1111-111111111111",
      sitat: "Tre leveranser i 2024", sidereferanse: "s. 4",
      kilde_id: D1, kildetittel: "ISO 9001-sertifikat",
      kilde_gyldig_til: "2030-01-01" },
  ],
  request_id: "r-d",
};

const FUNN = {
  request_id: "r-e",
  funn: [
    { anbud_id: A1, ekstern_ref: "DOF-1",
      tittel: "Drift av fagsystem", frist: "2026-09-20T12:00:00+00:00",
      funntype: "udekket_absolutt_krav", over_grense: 1, detalj: "K1",
      profilversjon: 2, forst_sett: "2026-09-02T09:00:00+00:00",
      sist_sett_sveip: "2026-09-03T09:00:00+00:00", apen: true,
      lukket_ts: null },
    { anbud_id: A2, ekstern_ref: "TED-9", tittel: "Rammeavtale",
      frist: "2026-08-01T12:00:00+00:00", funntype: "frist_passert",
      over_grense: -34, detalj: "2026-08-01", profilversjon: 2,
      forst_sett: "2026-09-02T09:00:00+00:00",
      sist_sett_sveip: "2026-09-03T09:00:00+00:00", apen: true,
      lukket_ts: null },
  ],
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
        json: async () => ({ feil: "anbud_ulovlig_tilstand" }) };
    }
    return { ok: true, status: 200,
             json: async () => ({ ok: true, udekkede_vektede: 0 }) };
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

async function vent(pred, n = 120) {
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
    "/v1/anbud": BILDE,
    "/v1/anbud/funn": FUNN,
    [`/v1/anbud/${A1}/utkast`]: UTKAST,
    [`/v1/anbud/${A1}/krav`]: KRAV,
    [`/v1/anbud/${A2}/utkast`]: { anbud_id: A2, utkast: [],
                                  request_id: "r-f" },
    [`/v1/anbud/${A2}/krav`]: { anbud_id: A2, utkast_id: null,
                                krav: [], request_id: "r-g" },
  };
}

function tabeller(h) {
  return [...h.querySelectorAll("table")];
}

async function apneForste(h) {
  await vent(() => tabeller(h).length >= 2);
  tabeller(h)[0].querySelectorAll("tbody tr")[0]
    .querySelector("button").click();
  // VENT PÅ NOE SOM BARE FINNES I PANELET. «ISO 9001» står alt på
  // hovedskjermen (kildedokumentet heter «ISO 9001-sertifikat»), så
  // en venting på den teksten ville sluppet gjennom før panelet var
  // tegnet — og hver test etterpå ville lett i feil DOM.
  await vent(() => [...h.querySelectorAll("caption")].some(
    (c) => c.textContent === t("ui.anbud.krav.tittel")));
}

// ---------------------------------------------------------------------
// modulen_sendte_tilbud — FRAVÆRET ER DOMMEN
// ---------------------------------------------------------------------

test("Anbud: ingen knapp sender inn et tilbud", async () => {
  // MUTASJONEN SOM DREPER DENNE: legg til en «Send inn»-knapp.
  SVAR = fullSvar();
  const h = nyHoved();
  visAnbud(h, ctx());
  await apneForste(h);
  const knapper = [...h.querySelectorAll("button")]
    .map((b) => b.textContent.toLowerCase());
  for (const forbudt of ["send", "lever", "publiser", "innsend"]) {
    assert.ok(!knapper.some((k) => k.includes(forbudt)),
      `fant knapp: ${forbudt} i ${JSON.stringify(knapper)}`);
  }
});

test("Anbud: «klar til gjennomgang» sier at den ikke sender noe",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visAnbud(h, ctx());
    await vent(() => tabeller(h).length >= 2);
    assert.ok(h.textContent.includes(t("ui.anbud.oversikt.hvorfor")),
      "flaten sa ikke hva den ikke gjør");
  });

// ---------------------------------------------------------------------
// utkastpunkt_uten_kilde — INGEN VEI TIL EN KILDELØS PÅSTAND
// ---------------------------------------------------------------------

test("Anbud: punktskjemaet krever kilde, sitat og side", async () => {
  // MUTASJONEN SOM DREPER DENNE: fjern `disabled`, eller la ett felt
  // være nok.
  SVAR = fullSvar();
  const h = nyHoved();
  visAnbud(h, ctx());
  await apneForste(h);
  const dekk = [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.anbud.knapp.dekk"));
  assert.ok(dekk, "fant ingen dekk-knapp på det udekkede kravet");
  dekk.click();
  await vent(() => h.querySelector("#an-p-kilde") !== null);
  const kilde = h.querySelector("#an-p-kilde");
  const sitat = h.querySelector("#an-p-sitat");
  const side = h.querySelector("#an-p-side");
  const lagre = [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.anbud.knapp.lagre_punkt"));
  assert.equal(lagre.disabled, true, "knappen var levende fra start");

  kilde.value = D1;
  kilde.dispatchEvent(new window.Event("change"));
  assert.equal(lagre.disabled, true, "kilde alene åpnet knappen");

  sitat.value = "Sertifikatet gjelder";
  sitat.dispatchEvent(new window.Event("input"));
  assert.equal(lagre.disabled, true, "kilde+sitat alene holdt");

  side.value = "s. 1";
  side.dispatchEvent(new window.Event("input"));
  assert.equal(lagre.disabled, false, "alle tre fylt ut, men død knapp");
});

test("Anbud: bare GYLDIGE kilder tilbys", async () => {
  // Et utløpt sertifikat er ikke dokumentasjon, og døra ville nektet
  // det — men en knapp som alltid feiler er verre enn en valgmulighet
  // som ikke finnes.
  SVAR = fullSvar();
  const h = nyHoved();
  visAnbud(h, ctx());
  await apneForste(h);
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.anbud.knapp.dekk")).click();
  await vent(() => h.querySelector("#an-p-kilde") !== null);
  const verdier = [...h.querySelector("#an-p-kilde").options]
    .map((o) => o.value).filter(Boolean);
  assert.deepEqual(verdier, [D1], "en utløpt kilde ble tilbudt");
});

test("Anbud: punktet sender kilde, sitat og side", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visAnbud(h, ctx());
  await apneForste(h);
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.anbud.knapp.dekk")).click();
  await vent(() => h.querySelector("#an-p-kilde") !== null);
  const kilde = h.querySelector("#an-p-kilde");
  const sitat = h.querySelector("#an-p-sitat");
  const side = h.querySelector("#an-p-side");
  kilde.value = D1; kilde.dispatchEvent(new window.Event("change"));
  sitat.value = "Sertifikatet gjelder drift";
  sitat.dispatchEvent(new window.Event("input"));
  side.value = "s. 1"; side.dispatchEvent(new window.Event("input"));
  kilde.form.dispatchEvent(
    new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE !== undefined);
  assert.equal(SISTE.sti, `/v1/anbud/utkast/${U1}/punkt`);
  assert.equal(SISTE.kropp.kilde_id, D1);
  assert.equal(SISTE.kropp.krav_id, K1);
  assert.equal(SISTE.kropp.sitat, "Sertifikatet gjelder drift");
  assert.equal(SISTE.kropp.sidereferanse, "s. 1");
  assert.ok(SISTE.headers["Idempotency-Key"], SISTE.headers);
});

test("Anbud: uten gyldige kilder sies det, i stedet for et dødt skjema",
  async () => {
    SVAR = { ...fullSvar(), "/v1/anbud": {
      ...BILDE,
      kilder: BILDE.kilder.map((k) => ({ ...k, gyldig_naa: false })) } };
    const h = nyHoved();
    visAnbud(h, ctx());
    await apneForste(h);
    [...h.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.anbud.knapp.dekk")).click();
    await vent(() => h.textContent.includes(
      t("ui.anbud.punkt.ingen_gyldige_kilder")));
    assert.equal(h.querySelector("#an-p-sitat"), null,
      "skjemaet ble vist uten en eneste gyldig kilde");
  });

// ---------------------------------------------------------------------
// Udekkede krav, ærlighet i tekst, og tabellene
// ---------------------------------------------------------------------

test("Anbud: et udekket krav vises som udekket", async () => {
  // MUTASJONEN SOM DREPER DENNE: filtrer bort de udekkede.
  SVAR = fullSvar();
  const h = nyHoved();
  visAnbud(h, ctx());
  await apneForste(h);
  const tab = [...h.querySelectorAll("caption")]
    .find((c) => c.textContent === t("ui.anbud.krav.tittel"))
    .closest("table");
  const rader = [...tab.querySelectorAll("tbody tr")];
  assert.equal(rader.length, 2, "et krav forsvant fra lista");
  // K1 er udekket og merket; K2 har sitat og kilde.
  assert.ok(rader[0].textContent.includes(t("ui.anbud.udekket")));
  assert.ok(rader[0].textContent.includes(t("ui.anbud.absolutt")));
  assert.ok(rader[1].textContent.includes("Tre leveranser i 2024"));
  assert.ok(rader[1].textContent.includes("ISO 9001-sertifikat"));
});

test("Anbud: absolutt og vektet skilles som TEKST", () => {
  // WCAG 1.4.1: forskjellen mellom «ulempe» og «avvisning» må kunne
  // leses, ikke bare ses som en farge.
  const node = kravTabell(KRAV.krav, null);
  const tekst = node.textContent;
  assert.ok(tekst.includes(t("ui.anbud.absolutt")));
  assert.ok(tekst.includes(t("ui.anbud.vektet")));
  assert.notEqual(t("ui.anbud.absolutt"), t("ui.anbud.vektet"));
});

test("Anbud: fristen leses med retning", () => {
  // «Om 3 døgn» og «3 døgn siden» er ikke samme sak, og et negativt
  // tall alene ville krevd at leseren regnet ut hva minus betyr.
  assert.equal(fristTekst(3),
    t("ui.anbud.frist_om").replace("{n}", "3"));
  assert.equal(fristTekst(0), t("ui.anbud.frist_i_dag"));
  assert.equal(fristTekst(-3),
    t("ui.anbud.frist_passert").replace("{n}", "3"));
  assert.notEqual(fristTekst(3), fristTekst(-3));
  assert.equal(fristTekst(null), "–");
});

test("Anbud: «ingen krav registrert» er noe annet enn «alle dekket»",
  () => {
    // Et anbud ingen har lest kravene ut av skal ikke se ferdig ut.
    const utenKrav = dekningTekst({ antall_krav: 0 });
    const klart = dekningTekst({ antall_krav: 2,
                                 udekkede_absolutte: 0,
                                 siste_utkast: 1, klar: true });
    const hull = dekningTekst({ antall_krav: 2,
                                udekkede_absolutte: 1 });
    assert.equal(utenKrav, t("ui.anbud.uten_krav"));
    assert.notEqual(utenKrav, klart);
    assert.ok(hull.includes("1"));
  });

test("Anbud: øre regnes om uten flyttall", () => {
  assert.equal(oreTekst(500000000), "5000000,00");
  assert.equal(oreTekst(1), "0,01");
  assert.equal(oreTekst(0), "0,00");
  assert.equal(oreTekst("9007199254740993"), "90071992547409,93");
  assert.equal(oreTekst(null), "–");
});

test("Anbud: øre overlever rundturen felt → base → felt", () => {
  // CodeRabbit, 118. `Math.floor(ore / 100)` og `Number(kr) * 100` er
  // ikke en rundtur: 123456 øre ble vist som 1234 kr og lagret tilbake
  // som 123400. Femtiseks øre forsvant — STILLE, og på en lagring
  // brukeren gjorde av en helt annen grunn.
  //
  // MUTASJONEN SOM DREPER DENNE: bytt tilbake til divisjon og `* 100`.
  for (const ore of [0, 1, 56, 100, 123456, 99999999,
                     100000000000]) {
    assert.equal(feltTilOre(oreTilFelt(ore)), ore,
      `tapte øre på ${ore}`);
  }
  // Formen er den `<input type=number>` godtar: punktum, ikke komma.
  assert.equal(oreTilFelt(123456), "1234.56");
  assert.equal(oreTilFelt(1), "0.01");
  assert.equal(oreTilFelt(null), "");
  // …og innmatingen godtar begge skilletegn, uten flyttall.
  assert.equal(feltTilOre("1234,56"), 123456);
  assert.equal(feltTilOre("1234.5"), 123450);
  assert.equal(feltTilOre("1234"), 123400);
  assert.equal(feltTilOre(""), null);
  assert.equal(feltTilOre("tull"), null);
});

test("Anbud: kildens gyldighet har TRE tilstander", () => {
  // `null` betyr at tenanten mangler profil, så vinduet ikke kan
  // regnes — det er noe annet enn «utløpt».
  const node = kildeTabell([
    { ...BILDE.kilder[0], gyldig_naa: true },
    { ...BILDE.kilder[1], gyldig_naa: false },
    { ...BILDE.kilder[0], kilde_id: "x", gyldig_naa: null },
  ]);
  const tekst = node.textContent;
  assert.ok(tekst.includes(t("ui.anbud.ja")));
  assert.ok(tekst.includes(t("ui.anbud.nei")));
  assert.ok(tekst.includes(t("ui.anbud.ukjent")));
});

test("Anbud: de lukkede settene er som i basen", () => {
  assert.deepEqual(ANBUDSKILDER, ["doffin", "ted", "direkte", "annen"]);
  assert.deepEqual(KRAVTYPER, ["kvalifikasjon", "dokumentasjon",
                               "erfaring", "sertifisering", "okonomi",
                               "annet"]);
  assert.deepEqual(DOKUMENTTYPER, ["sertifikat", "attest", "regnskap",
                                   "referanse", "policy", "cv",
                                   "annet"]);
});

test("Anbud: tabellene er ekte", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visAnbud(h, ctx());
  await apneForste(h);
  const tab = tabeller(h);
  assert.ok(tab.length >= 4, `bare ${tab.length} tabeller`);
  for (const tabell of tab) {
    assert.ok(tabell.querySelector("caption"), "tabell uten <caption>");
    assert.ok(tabell.closest(".tablewrap"),
      "tabell uten sidescroll-container");
    const rad = tabell.querySelector("tbody tr");
    if (rad) {
      assert.ok(rad.querySelector('th[scope="row"]'),
        "rad uten th[scope=row]");
    }
  }
});

test("Anbud: en lesende økt ser registeret, men ingen skriveveier",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visAnbud(h, ctx(["okonomi:read"]));
    await vent(() => tabeller(h).length >= 2);
    assert.ok(h.textContent.includes("Drift av fagsystem"));
    assert.equal(h.querySelector("form"), null,
      "en lesende økt fikk et skjema");
    tabeller(h)[0].querySelectorAll("tbody tr")[0]
      .querySelector("button").click();
    await vent(() => h.textContent.includes("ISO 9001"));
    const dekk = [...h.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.anbud.knapp.dekk"));
    assert.equal(dekk, undefined, "en lesende økt fikk dekke et krav");
  });

test("Anbud: et udekket absolutt krav tilbys ikke for lukking",
  async () => {
    // Døra ville nektet det uansett.
    SVAR = fullSvar();
    const h = nyHoved();
    visAnbud(h, ctx());
    await vent(() => h.querySelector("#an-f-valg") !== null);
    const verdier = [...h.querySelector("#an-f-valg").options]
      .map((o) => o.value).filter(Boolean);
    assert.equal(verdier.length, 1, verdier);
    assert.ok(verdier[0].startsWith(A2), verdier[0]);
  });

test("Anbud: udekkede absolutte krav står øverst i sammendraget",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visAnbud(h, ctx());
    await vent(() => tabeller(h).length >= 2);
    assert.ok(h.textContent.includes(
      t("ui.anbud.udekkede_absolutte_sum").replace("{n}", "3")));
    assert.ok(h.textContent.includes(
      t("ui.anbud.utlopte_kilder").replace("{n}", "1")));
  });

// ---------------------------------------------------------------------
// ui_axe_alvorlige_brudd
// ---------------------------------------------------------------------

test("Anbud: null alvorlige axe-brudd på registeret", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visAnbud(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Anbud: null alvorlige axe-brudd med punktskjemaet åpent",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visAnbud(h, ctx());
    await apneForste(h);
    [...h.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.anbud.knapp.dekk")).click();
    await vent(() => h.querySelector("#an-p-kilde") !== null);
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Anbud: null alvorlige axe-brudd på et tomt register", async () => {
  SVAR = { ...fullSvar(), "/v1/anbud": TOMT,
           "/v1/anbud/funn": { request_id: "r-h", funn: [] } };
  const h = nyHoved();
  visAnbud(h, ctx());
  await vent(() => h.textContent.includes(t("ui.anbud.liste.ingen")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Anbud: krav- og anbudstabellen står alene uten brudd", async () => {
  let brudd = await alvorligeBrudd(kravTabell(KRAV.krav, null),
                                   { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  brudd = await alvorligeBrudd(anbudTabell(BILDE.anbud, () => {}),
                               { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Anbud: ingen hardkodet tekst", async () => {
  const PL = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visAnbud(h, ctx());
    await apneForste(h);
    // `th[scope="row"]` er UTELATT: den cellen bærer tittelen,
    // kravnummeret og dokumentnavnet — tenantens egne data.
    for (const node of h.querySelectorAll(
      'h2, h3, h4, label, caption, legend, th[scope="col"], button')) {
      const s = node.textContent.trim();
      if (!s) continue;
      assert.ok(s.startsWith("PL_"), `hardkodet tekst: «${s}»`);
    }
  } finally {
    settI18nForTest(NB, "nb");
  }
});
