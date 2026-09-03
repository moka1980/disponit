// M-49 sanksjonsflaten (117) — flateporten (jsdom + axe).
//
// Portene her måler de TO FRAVÆRENE hele modulen hviler på:
//
//   * `modulen_blokkerte_motpart`: flaten har ingen «blokker»-knapp.
//     Ikke fordi den ville vært vanskelig, men fordi den ville løyet —
//     knappen ville skrevet et flagg ingen leser, og brukeren ville
//     trodd handelen var stanset.
//   * `modulen_avfeide_navnelikhet`: ett treff avklares om gangen,
//     med en konklusjon uten forhåndsvalg og en begrunnelse på minst
//     tolv tegn. Ingen avkrysningsbokser, ingen samlet knapp, ingen
//     «lukk alle under 90 %».
//
// OG ÉN TING FLATEN MÅ SI HØYT: at modulen ikke stanser handel. En
// bruker som TROR den gjør det er farligere stilt enn en som vet at
// den ikke gjør det.
//
// MATCHTYPEN VISES SOM TEKST, ALLTID. «Eksakt identifikator» og
// «navnelikhet» er ikke grader av det samme (WCAG 1.4.1, og
// datamodellens hele poeng).
//
// Ingen delt fixture (m16-formen): hver test bygger sin egen skjerm.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import {
  KILDER, KONKLUSJONER, MATCHTYPER, kontrollTekst, listeTabell,
  matchTekst, treffTabell, visSanksjon,
} from "../static/js/flater/sanksjon.js";

settI18nForTest(NB, "nb");

const S1 = "11111111-1111-1111-1111-111111111111";
const S2 = "22222222-2222-2222-2222-222222222222";
const T1 = "aaaaaaaa-1111-1111-1111-111111111111";
const T2 = "aaaaaaaa-2222-2222-2222-222222222222";
const L1 = "bbbbbbbb-1111-1111-1111-111111111111";

const BILDE = {
  sammendrag: {
    subjekter: 40, aktive: 12, kontrollerte: 9, uavklarte_treff: 3,
    bekreftede_treff: 1, apne_funn: 4, lister: 2,
    nyeste_listeversjon: "ofac 2026-09-01", har_krav: true,
    kravversjon: 2, vist: 2,
  },
  subjekter: [
    { subjekt_id: S1, ekstern_ref: "K-100",
      navn_oppgitt: "Mohammed Ali", subjekttype: "person",
      land: "NO", har_identifikator: false, aktiv: true,
      opprettet: "2026-08-01T09:00:00+00:00",
      siste_kontroll: "2026-09-01T09:00:00+00:00",
      siste_utfall: "treff", apne_treff: 2,
      groveste_matchtype: "navnelikhet", apne_funn: 1 },
    { subjekt_id: S2, ekstern_ref: "K-200",
      navn_oppgitt: "Testfirma AS", subjekttype: "foretak",
      land: "NO", har_identifikator: true, aktiv: false,
      opprettet: "2026-08-02T09:00:00+00:00",
      siste_kontroll: null, siste_utfall: null, apne_treff: 0,
      groveste_matchtype: null, apne_funn: 2 },
  ],
  lister: [
    { liste_id: L1, kilde: "ofac", listeversjon: "2026-09-01",
      gjelder_fra: "2026-09-01", innhold_sha256: "a".repeat(64),
      antall_oppforinger: 12000,
      registrert: "2026-09-01T08:00:00+00:00",
      registrert_av: "kari", er_nyeste: true },
  ],
  krav: {
    matchterskel: 85, kontroll_gyldig_dogn: 90,
    uavklart_frist_dogn: 3, ukontrollert_dogn: 30, versjon: 2,
    oppdatert: "2026-08-01T09:00:00+00:00", oppdatert_av: "kari",
  },
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    subjekter: 0, aktive: 0, kontrollerte: 0, uavklarte_treff: 0,
    bekreftede_treff: 0, apne_funn: 0, lister: 0,
    nyeste_listeversjon: null, har_krav: false, kravversjon: null,
    vist: 0,
  },
  subjekter: [], lister: [], krav: null, request_id: "r-b",
};

const KONTROLLER = {
  subjekt_id: S1,
  kontroller: [
    { kontroll_id: "cccccccc-1111-1111-1111-111111111111",
      liste_id: L1, kilde: "ofac", listeversjon: "2026-09-01",
      matchterskel: 85, sammenlignede_felt: ["navn", "land"],
      kravversjon: 2, utfall: "treff", antall_treff: 2,
      kontrollert: "2026-09-01T09:00:00+00:00",
      kontrollert_av: "kari" },
  ],
  request_id: "r-c",
};

const TREFF = {
  subjekt_id: S1,
  treff: [
    { treff_id: T1, kontroll_id: "cccccccc-1111-1111-1111-111111111111",
      matchtype: "navnelikhet", matchfelt: ["navn"], likhet: 92,
      listenavn: "ALI, Mohamed", liste_referanse: "SDN-1",
      liste_program: "SDGT", kilde: "ofac",
      listeversjon: "2026-09-01",
      registrert: "2026-09-01T09:00:00+00:00",
      konklusjon: null, begrunnelse: null, avklart: null,
      avklart_av: null },
    { treff_id: T2, kontroll_id: "cccccccc-1111-1111-1111-111111111111",
      matchtype: "eksakt_navn", matchfelt: ["navn"], likhet: 100,
      listenavn: "ALI, Mohammed", liste_referanse: "SDN-2",
      liste_program: null, kilde: "ofac",
      listeversjon: "2026-09-01",
      registrert: "2026-09-01T09:00:00+00:00",
      konklusjon: "ikke_samme_part",
      begrunnelse: "Fodselsdato avviker med tolv aar",
      avklart: "2026-09-02T09:00:00+00:00", avklart_av: "per" },
  ],
  request_id: "r-d",
};

const FUNN = {
  request_id: "r-e",
  funn: [
    { subjekt_id: S1, ekstern_ref: "K-100",
      navn_oppgitt: "Mohammed Ali", funntype: "uavklart_treff",
      over_grense: 5, siste_matchtype: "navnelikhet",
      siste_utfall: null, kravversjon: 2,
      forst_sett: "2026-09-02T09:00:00+00:00",
      sist_sett_sveip: "2026-09-03T09:00:00+00:00", apen: true,
      lukket_ts: null },
    { subjekt_id: S2, ekstern_ref: "K-200",
      navn_oppgitt: "Testfirma AS", funntype: "bekreftet_treff",
      over_grense: null, siste_matchtype: null, siste_utfall: null,
      kravversjon: 2, forst_sett: "2026-09-02T09:00:00+00:00",
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
        json: async () => ({ feil: "sanksjon_ulovlig_tilstand" }) };
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
    "/v1/sanksjon": BILDE,
    "/v1/sanksjon/funn": FUNN,
    [`/v1/sanksjon/${S1}/kontroller`]: KONTROLLER,
    [`/v1/sanksjon/${S1}/treff`]: TREFF,
    [`/v1/sanksjon/${S2}/kontroller`]: { subjekt_id: S2,
                                         kontroller: [],
                                         request_id: "r-f" },
    [`/v1/sanksjon/${S2}/treff`]: { subjekt_id: S2, treff: [],
                                    request_id: "r-g" },
  };
}

function tabeller(h) {
  return [...h.querySelectorAll("table")];
}

async function apneForste(h) {
  await vent(() => tabeller(h).length >= 2);
  tabeller(h)[0].querySelectorAll("tbody tr")[0]
    .querySelector("button").click();
  await vent(() => h.textContent.includes("ALI, Mohamed"));
}

// ---------------------------------------------------------------------
// modulen_blokkerte_motpart — FRAVÆRET ER DOMMEN
// ---------------------------------------------------------------------

test("Sanksjon: ingen kontroll blokkerer handel", async () => {
  // MUTASJONEN SOM DREPER DENNE: legg til en «Blokker»-knapp.
  SVAR = fullSvar();
  const h = nyHoved();
  visSanksjon(h, ctx());
  await apneForste(h);
  const knapper = [...h.querySelectorAll("button")]
    .map((b) => b.textContent.toLowerCase());
  for (const forbudt of ["blokker", "sperr", "stans", "stopp"]) {
    assert.ok(!knapper.some((k) => k.includes(forbudt)),
      `fant knapp: ${forbudt}`);
  }
});

test("Sanksjon: flaten sier at den ikke stanser handel", async () => {
  // En bruker som TROR handelen blir stanset er farligere stilt enn
  // en som vet at den ikke blir det.
  SVAR = fullSvar();
  const h = nyHoved();
  visSanksjon(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  assert.ok(h.textContent.includes(t("ui.sanksjon.oversikt.hvorfor")),
    "flaten sa ikke hva den ikke gjør");
});

// ---------------------------------------------------------------------
// modulen_avfeide_navnelikhet — ETT TREFF OM GANGEN
// ---------------------------------------------------------------------

test("Sanksjon: avklaringsknappen er død til begge felt er fylt ut",
  async () => {
    // MUTASJONEN SOM DREPER DENNE: fjern `disabled`, eller la ett av
    // feltene være nok.
    SVAR = fullSvar();
    const h = nyHoved();
    visSanksjon(h, ctx());
    await apneForste(h);
    const avklar = [...h.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.sanksjon.knapp.avklar"));
    assert.ok(avklar, "fant ingen avklarknapp");
    avklar.click();
    await vent(() => h.querySelector("#sk-a-konklusjon") !== null);
    const konk = h.querySelector("#sk-a-konklusjon");
    const begr = h.querySelector("#sk-a-begrunnelse");
    const lagre = [...h.querySelectorAll("button")].find(
      (b) => b.textContent === t("ui.sanksjon.knapp.lagre_avklaring"));
    assert.equal(lagre.disabled, true, "knappen var levende fra start");

    konk.value = "ikke_samme_part";
    konk.dispatchEvent(new window.Event("change"));
    assert.equal(lagre.disabled, true, "konklusjon alene åpnet knappen");

    begr.value = "for kort";
    begr.dispatchEvent(new window.Event("input"));
    assert.equal(lagre.disabled, true, "en kort begrunnelse holdt");

    begr.value = "Fodselsdato avviker med tolv aar";
    begr.dispatchEvent(new window.Event("input"));
    assert.equal(lagre.disabled, false, "begge fylt ut, men død knapp");
  });

test("Sanksjon: konklusjonen har ingen forhåndsvalgt verdi",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visSanksjon(h, ctx());
    await apneForste(h);
    [...h.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.sanksjon.knapp.avklar"))
      .click();
    await vent(() => h.querySelector("#sk-a-konklusjon") !== null);
    const konk = h.querySelector("#sk-a-konklusjon");
    assert.equal(konk.value, "", "en konklusjon var forhåndsvalgt");
    assert.equal(konk.options[0].value, "");
    const verdier = [...konk.options].map((o) => o.value)
      .filter(Boolean);
    assert.deepEqual(verdier, KONKLUSJONER);
    // «Uavklart, eskalert» ER et lovlig valg.
    assert.ok(verdier.includes("uavklart_eskalert"));
  });

test("Sanksjon: ingen masseavklaring", async () => {
  // Ingen avkrysningsbokser i treffabellen, og ingen samlet knapp.
  // En kø som går ned ser ut som saksbehandling — derfor skal den
  // ikke kunne gå ned av seg selv.
  SVAR = fullSvar();
  const h = nyHoved();
  visSanksjon(h, ctx());
  await apneForste(h);
  assert.equal(h.querySelector('input[type="checkbox"]'), null,
    "fant en avkrysningsboks");
  const avklarknapper = [...h.querySelectorAll("button")]
    .filter((b) => b.textContent === t("ui.sanksjon.knapp.avklar"));
  // ÉN knapp per uavklart treff, og TREFF har ett uavklart.
  assert.equal(avklarknapper.length, 1, "feil antall avklarknapper");
});

test("Sanksjon: et avklart treff har ingen avklarknapp", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visSanksjon(h, ctx());
  await apneForste(h);
  const rader = [...h.querySelectorAll("caption")]
    .find((c) => c.textContent === t("ui.sanksjon.treff.tittel"))
    .closest("table").querySelectorAll("tbody tr");
  assert.equal(rader.length, 2);
  // Rad 0 er uavklart (har knapp), rad 1 er avklart (har ikke).
  assert.ok(rader[0].querySelector("button"));
  assert.equal(rader[1].querySelector("button"), null);
  // …og konklusjonen VISES på den avklarte.
  assert.ok(rader[1].textContent.includes(
    t("ui.sanksjon.konklusjon.ikke_samme_part")));
});

test("Sanksjon: avklaringen sender konklusjon og begrunnelse",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visSanksjon(h, ctx());
    await apneForste(h);
    [...h.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.sanksjon.knapp.avklar"))
      .click();
    await vent(() => h.querySelector("#sk-a-konklusjon") !== null);
    const konk = h.querySelector("#sk-a-konklusjon");
    const begr = h.querySelector("#sk-a-begrunnelse");
    konk.value = "uavklart_eskalert";
    konk.dispatchEvent(new window.Event("change"));
    begr.value = "Klarer ikke avgjore; sendt til jurist";
    begr.dispatchEvent(new window.Event("input"));
    konk.form.dispatchEvent(
      new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE !== undefined);
    assert.equal(SISTE.sti, `/v1/sanksjon/treff/${T1}/avklaring`);
    assert.equal(SISTE.kropp.konklusjon, "uavklart_eskalert");
    assert.equal(SISTE.kropp.begrunnelse,
      "Klarer ikke avgjore; sendt til jurist");
    assert.ok(SISTE.headers["Idempotency-Key"], SISTE.headers);
  });

// ---------------------------------------------------------------------
// Matchtypen, tabellene og ærligheten
// ---------------------------------------------------------------------

test("Sanksjon: matchtypene er tre, og de leses som tekst", () => {
  // «Eksakt identifikator» og «navnelikhet» er ikke grader av det
  // samme: den første er den ene klassen som en dag kan blokkere
  // maskinelt.
  assert.deepEqual(MATCHTYPER,
    ["eksakt_identifikator", "eksakt_navn", "navnelikhet"]);
  const tekster = MATCHTYPER.map(matchTekst);
  assert.equal(new Set(tekster).size, 3, tekster);
  for (const s of tekster) assert.ok(s && !s.startsWith("ui."), s);
  assert.equal(matchTekst(null), t("ui.sanksjon.uten_treff"));
});

test("Sanksjon: «aldri kontrollert» er noe annet enn «ingen treff»",
  () => {
    // WCAG 1.4.1 og alminnelig ærlighet: et subjekt ingen har sjekket
    // skal ikke se ut som et som er sjekket og funnet rent.
    const aldri = kontrollTekst({ siste_kontroll: null });
    const rent = kontrollTekst({ siste_kontroll: "2026-09-01",
                                 siste_utfall: "ingen_treff" });
    const treff = kontrollTekst({ siste_kontroll: "2026-09-01",
                                  siste_utfall: "treff",
                                  apne_treff: 2 });
    assert.equal(aldri, t("ui.sanksjon.aldri_kontrollert"));
    assert.equal(rent, t("ui.sanksjon.ingen_treff"));
    assert.notEqual(aldri, rent);
    assert.notEqual(rent, treff);
    assert.ok(treff.includes("2"));
  });

test("Sanksjon: listeversjonen vises med innholdssum", async () => {
  // «Sto de på lista DEN DAGEN» kan ingen svare på uten å kunne peke
  // på nøyaktig hvilken fil.
  SVAR = fullSvar();
  const h = nyHoved();
  visSanksjon(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  const tab = [...h.querySelectorAll("caption")]
    .find((c) => c.textContent === t("ui.sanksjon.lister.tittel"));
  assert.ok(tab, "fant ingen listetabell");
  const rad = tab.closest("table").querySelector("tbody tr");
  assert.ok(rad.textContent.includes("aaaaaaaaaaaa"),
    "innholdssummen sto ikke i tabellen");
  assert.ok(rad.textContent.includes("2026-09-01"));
});

test("Sanksjon: kildene er et lukket sett", () => {
  assert.deepEqual(KILDER, ["ofac", "eu", "fn"]);
});

test("Sanksjon: tabellene er ekte", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visSanksjon(h, ctx());
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

test("Sanksjon: en lesende økt ser registeret, men ingen skriveveier",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visSanksjon(h, ctx(["okonomi:read"]));
    await vent(() => tabeller(h).length >= 2);
    assert.ok(h.textContent.includes("Mohammed Ali"));
    assert.equal(h.querySelector("form"), null,
      "en lesende økt fikk et skjema");
    tabeller(h)[0].querySelectorAll("tbody tr")[0]
      .querySelector("button").click();
    await vent(() => h.textContent.includes("ALI, Mohamed"));
    const avklar = [...h.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.sanksjon.knapp.avklar"));
    assert.equal(avklar, undefined, "en lesende økt fikk avklare");
  });

test("Sanksjon: et bekreftet treff tilbys ikke for lukking",
  async () => {
    // Døra ville nektet det uansett, og en knapp som alltid feiler er
    // verre enn ingen knapp.
    SVAR = fullSvar();
    const h = nyHoved();
    visSanksjon(h, ctx());
    await vent(() => h.querySelector("#sk-f-valg") !== null);
    const valg = h.querySelector("#sk-f-valg");
    const verdier = [...valg.options].map((o) => o.value)
      .filter(Boolean);
    assert.equal(verdier.length, 1, verdier);
    assert.ok(verdier[0].startsWith(S1), verdier[0]);
    const tekster = [...valg.options].map((o) => o.textContent);
    assert.ok(!tekster.some(
      (x) => x.includes(t("ui.sanksjon.merke_bekreftet"))), tekster);
  });

test("Sanksjon: funn lukkes bare med en begrunnelse", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visSanksjon(h, ctx());
  await vent(() => h.querySelector("#sk-f-notat") !== null);
  const notat = h.querySelector("#sk-f-notat");
  assert.equal(notat.required, true);
  assert.equal(notat.getAttribute("minlength"), "4");
  assert.equal(h.querySelector("#sk-f-valg").value, "");
});

test("Sanksjon: uavklarte treff står øverst i sammendraget",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visSanksjon(h, ctx());
    await vent(() => tabeller(h).length >= 2);
    const tekst = h.textContent;
    assert.ok(tekst.includes(
      t("ui.sanksjon.uavklarte_treff").replace("{n}", "3")), tekst);
    assert.ok(tekst.includes(
      t("ui.sanksjon.bekreftede_treff").replace("{n}", "1")));
  });

// ---------------------------------------------------------------------
// ui_axe_alvorlige_brudd
// ---------------------------------------------------------------------

test("Sanksjon: null alvorlige axe-brudd på registeret", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visSanksjon(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Sanksjon: null alvorlige axe-brudd med avklaringsskjemaet åpent",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visSanksjon(h, ctx());
    await apneForste(h);
    [...h.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.sanksjon.knapp.avklar"))
      .click();
    await vent(() => h.querySelector("#sk-a-konklusjon") !== null);
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Sanksjon: null alvorlige axe-brudd på et tomt register",
  async () => {
    SVAR = { ...fullSvar(), "/v1/sanksjon": TOMT,
             "/v1/sanksjon/funn": { request_id: "r-h", funn: [] } };
    const h = nyHoved();
    visSanksjon(h, ctx());
    await vent(() => h.textContent.includes(
      t("ui.sanksjon.liste.ingen")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Sanksjon: treff- og listetabellen står alene uten brudd",
  async () => {
    // NODEN SENDES DIREKTE: `alvorligeBrudd` lager sitt eget brett.
    let brudd = await alvorligeBrudd(treffTabell(TREFF.treff, null),
                                     { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
    brudd = await alvorligeBrudd(listeTabell(BILDE.lister),
                                 { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Sanksjon: ingen hardkodet tekst", async () => {
  const PL = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visSanksjon(h, ctx());
    await apneForste(h);
    // `th[scope="row"]` er UTELATT: den cellen bærer navnet, datoen og
    // listenavnet — altså tenantens og listas egne data. Det samme
    // gjelder `option`, som bygges av subjektnavn.
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
