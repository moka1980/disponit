// M-20 innholdsflaten (134) — flateporten (jsdom + axe).
//
// PORTENE MÅLER KLYNGENS DOM, IKKE BARE AT SKJERMEN TEGNES.
//
//   En ytring avgitt i husets navn kan ikke tas tilbake — OG DEN SOM
//   LESER DEN VET IKKE AT EN MASKIN SKREV DEN.
//
// En produktpåstand ser like troverdig ut enten den hviler på en
// testrapport eller på ingenting. Derfor måler portene her:
//
//   * at KILDEN står i samme rad som påstanden, med sin type, og at
//     en utløpt kilde er et VARSEL og ikke en fotnote.
//   * at en side som hviler på noe utløpt merkes i SIDELISTEN, uten
//     et klikk til.
//   * at VEIEN TILBAKE sies FØR veien fram tas — publiseringspanelet
//     forteller hva en rollback vil gjøre.
//   * at hver PERIODE en versjon var levende er sin egen rad, med
//     begge navnene: «hvor lenge sto det ute» er et spørsmål noen
//     stiller etterpå.
//   * at tilbakerullingen sier fra når den gamle siden IKKE kunne
//     gjenopprettes. En stille suksess ville latt noen tro at den
//     står ute igjen.
//   * at det ikke finnes en «publiser automatisk»-knapp.
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
  dato, dognTekst, funntabell, kildetabell, paastandstabell,
  publiseringstabell, sammendrag, sidetabell, visInnhold,
} from "../static/js/flater/innhold.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));
const ROT = join(HER, "..", "..", "..", "..");

const U1 = "aaaaaaaa-1111-1111-1111-111111111111";
const U2 = "aaaaaaaa-2222-2222-2222-222222222222";
const K1 = "bbbbbbbb-1111-1111-1111-111111111111";
const K2 = "bbbbbbbb-2222-2222-2222-222222222222";
const P1 = "cccccccc-1111-1111-1111-111111111111";
const V1 = "dddddddd-1111-1111-1111-111111111111";
const B1 = "eeeeeeee-1111-1111-1111-111111111111";
const B2 = "eeeeeeee-2222-2222-2222-222222222222";
const F1 = "ffffffff-1111-1111-1111-111111111111";
const F2 = "ffffffff-2222-2222-2222-222222222222";

// TO PÅSTANDER: én på en gyldig kilde, én på en som har gått ut.
const PAASTANDER = [
  { paastand_id: P1, rekkefolge: 1,
    tekst: "Lader til 80 prosent paa 20 minutter",
    kilde_id: K1, kilde_tittel: "Testrapport TR-2026-04",
    dokumenttype: "testrapport", kilde_sha256: "a".repeat(64),
    kilde_gyldig_til: "2030-01-01", kilde_gyldig: true,
    registrert: "2026-09-01T10:00:00+00:00", registrert_av: "u-kari" },
  { paastand_id: "cccccccc-2222-2222-2222-222222222222", rekkefolge: 2,
    tekst: "Produsert med fornybar kraft",
    kilde_id: K2, kilde_tittel: "Leverandoererklaering 2024",
    dokumenttype: "leverandorerklaering", kilde_sha256: "b".repeat(64),
    kilde_gyldig_til: "2025-12-31", kilde_gyldig: false,
    registrert: "2026-09-01T10:05:00+00:00", registrert_av: "u-kari" },
];

const SIDER = [
  { side_id: "produkt/hurtiglader", siste_versjon: 2,
    siste_utkast_id: U2, siste_status: "klar", levende_versjon: 1,
    levende_publisert: "2026-08-01T09:00:00+00:00",
    levende_publisert_av: "u-per", antall_paastander: 2,
    antall_utlopte_kilder: 1, antall_visninger: 1 },
  { side_id: "om-oss", siste_versjon: 1, siste_utkast_id: U1,
    siste_status: "utkast", levende_versjon: null,
    levende_publisert: null, levende_publisert_av: null,
    antall_paastander: 0, antall_utlopte_kilder: 0,
    antall_visninger: 0 },
];

const KILDER = [
  { kilde_id: K1, tittel: "Testrapport TR-2026-04",
    dokumenttype: "testrapport", gyldig_til: "2030-01-01",
    gyldig: true, dogn_igjen: 1200, innhold_sha256: "a".repeat(64),
    registrert: "2026-04-01T09:00:00+00:00", registrert_av: "u-kari",
    antall_paastander: 1 },
  // EN UTLØPT KILDE SER NØYAKTIG UT SOM EN GYLDIG.
  { kilde_id: K2, tittel: "Leverandoererklaering 2024",
    dokumenttype: "leverandorerklaering", gyldig_til: "2025-12-31",
    gyldig: false, dogn_igjen: -248, innhold_sha256: "b".repeat(64),
    registrert: "2024-01-01T09:00:00+00:00", registrert_av: "u-kari",
    antall_paastander: 1 },
];

const PUBLISERINGER = [
  { publisering_id: B1, side_id: "produkt/hurtiglader", versjon: 1,
    publisert_ts: "2026-08-01T09:00:00+00:00", publisert_av: "u-per",
    rollbackform: "avpublisering", rollback_til_versjon: null,
    tilbake_ts: null, tilbake_av: null, levende: true,
    vist_ts: "2026-08-01T08:30:00+00:00", vist_for: "u-kari" },
  { publisering_id: B2, side_id: "gammel-kampanje", versjon: 3,
    publisert_ts: "2026-05-01T09:00:00+00:00", publisert_av: "u-per",
    rollbackform: "forrige_versjon", rollback_til_versjon: 2,
    tilbake_ts: "2026-06-01T09:00:00+00:00", tilbake_av: "u-kari",
    levende: false, vist_ts: "2026-05-01T08:00:00+00:00",
    vist_for: "u-per" },
];

// TO VISNINGER: én av GJELDENDE innhold, én av et eldre.
const VISNINGER = [
  { visning_id: V1, vist_hash: "c".repeat(64),
    vist_ts: "2026-09-01T08:30:00+00:00", vist_for: "u-kari",
    gjelder_dette_innholdet: true },
  { visning_id: "dddddddd-2222-2222-2222-222222222222",
    vist_hash: "d".repeat(64), vist_ts: "2026-08-01T08:00:00+00:00",
    vist_for: "u-per", gjelder_dette_innholdet: false },
];

const FUNN = [
  // SVEIPENS EGET — ingen kan lukke det.
  { funn_id: F1, funntype: "publisert_paastand_uten_gyldig_kilde",
    referanse: B1, detaljer: "1 publisert paastand hviler paa en utloept kilde",
    over_grense: 1, apen: true,
    forst_sett: "2026-09-01T09:00:00+00:00",
    sist_sett: "2026-09-05T09:00:00+00:00", lukket_av: null,
    kan_lukkes: false },
  // KILDE SOM SNART GÅR UT — et menneske KAN avklare den.
  { funn_id: F2, funntype: "kilde_utloper_snart_uavklart",
    referanse: K1, detaljer: "kilden utloeper om 12 doegn",
    over_grense: 12, apen: true,
    forst_sett: "2026-09-02T09:00:00+00:00",
    sist_sett: "2026-09-05T09:00:00+00:00", lukket_av: null,
    kan_lukkes: true },
];

const BILDE = {
  request_id: "r-x",
  sammendrag: {
    sider: 2, utkast: 1, klare: 1, publiserte: 1, levende_sider: 1,
    paastander: 2, kilder: 2, utlopte_kilder: 1,
    paastander_paa_utlopt_kilde: 1, visninger: 1, apne_funn: 2,
    har_krav: true, kilde_gyldig_dogn: 365, visning_gyldig_min: 60,
    varselfrist_dogn: 30, kravversjon: 1,
  },
  sider: SIDER, kilder: KILDER, publiseringer: PUBLISERINGER,
  funn: FUNN,
};

const TOMT = {
  request_id: "r-t",
  sammendrag: {
    sider: 0, utkast: 0, klare: 0, publiserte: 0, levende_sider: 0,
    paastander: 0, kilder: 0, utlopte_kilder: 0,
    paastander_paa_utlopt_kilde: 0, visninger: 0, apne_funn: 0,
    har_krav: false, kilde_gyldig_dogn: null, visning_gyldig_min: null,
    varselfrist_dogn: null, kravversjon: null,
  },
  sider: [], kilder: [], publiseringer: [], funn: [],
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
        json: async () => ({ feil: "innhold_ulovlig_tilstand" }) };
    }
    return { ok: true, status: 200,
             json: async () => ({ ok: true, utfall: "avpublisert" }) };
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
    "/v1/innhold": BILDE,
    [`/v1/innhold/utkast/${U2}`]: { request_id: "r-u", utkast_id: U2,
                                    paastander: PAASTANDER,
                                    visninger: VISNINGER },
  };
  SISTE = null;
  SVARSTATUS = 200;
  return m;
}


// =====================================================================
// KILDEN SKAL SES.
// =====================================================================

test("kilden står i samme rad som påstanden, med sin type", () => {
  const tab = paastandstabell(PAASTANDER);
  const rader = [...tab.querySelectorAll("tbody tr")];
  const forste = [...rader[0].querySelectorAll("td")]
    .map((c) => c.textContent);
  assert.ok(forste.includes("Testrapport TR-2026-04"));
  assert.ok(forste.includes(t("ui.innhold.type_testrapport")));
});

test("en utløpt kilde er et varsel, en gyldig er det ikke", () => {
  const tab = paastandstabell(PAASTANDER);
  const varsler = [...tab.querySelectorAll("strong[role='alert']")];
  assert.equal(varsler.length, 1, "gyldig og utloept tegnes likt");
  assert.equal(varsler[0].textContent, t("ui.innhold.kilde_utlopt"));
  assert.ok(tab.textContent.includes(t("ui.innhold.kilde_gyldig")));
});

test("en side som hviler på noe utløpt merkes i sidelisten", () => {
  // …uten et klikk til. Ellers må man åpne hver side for å finne det.
  const tab = sidetabell(SIDER, () => {});
  const varsler = [...tab.querySelectorAll("strong[role='alert']")]
    .map((x) => x.textContent);
  assert.deepEqual(varsler,
    [t("ui.innhold.av_dem_utlopt").replace("{n}", "1")]);
});

test("sammendraget setter den publiserte udokumenterte påstanden først", () => {
  const p = sammendrag(BILDE.sammendrag);
  const sterke = [...p.querySelectorAll("strong")];
  assert.equal(sterke[0].getAttribute("role"), "alert");
  assert.ok(sterke[0].textContent.includes("1"));
});

test("sammendraget sier fra når grensene ikke er satt", () => {
  const p = sammendrag(TOMT.sammendrag);
  assert.ok([...p.querySelectorAll("strong[role='alert']")]
    .some((x) => x.textContent === t("ui.innhold.mangler_krav")));
});

test("døgn til utløp skiller «utløper om» fra «utløp for»", () => {
  // Et negativt tall er ikke «lenge siden» — det er UTLØPT.
  assert.ok(dognTekst(12).includes("12"));
  assert.ok(dognTekst(-248).includes("248"));
  assert.notEqual(dognTekst(12), dognTekst(-12));
  assert.equal(dognTekst(null), "–");
});

test("kildetabellen merker den utløpte og viser gyldigheten på den andre", () => {
  const tab = kildetabell(KILDER);
  const varsler = [...tab.querySelectorAll("strong[role='alert']")];
  assert.equal(varsler.length, 1);
  assert.ok(tab.textContent.includes("1200"));
});


// =====================================================================
// VEIEN TILBAKE.
// =====================================================================

test("hver periode en versjon var levende er sin egen rad", () => {
  const tab = publiseringstabell(PUBLISERINGER);
  const rader = [...tab.querySelectorAll("tbody tr")];
  assert.equal(rader.length, 2);
  assert.ok(rader[0].textContent.includes(t("ui.innhold.staar_ute")));
  // …OG DEN SOM TOK DEN NED HAR ET NAVN.
  assert.ok(rader[1].textContent.includes("u-kari"));
});

test("publiseringen viser hvem som SÅ den, ikke bare hvem som publiserte", () => {
  // Uten dette er «godkjent» en påstand.
  const tab = publiseringstabell(PUBLISERINGER);
  assert.ok(tab.textContent.includes(
    t("ui.innhold.sett_verdi").replace("{av}", "u-kari")
      .replace("{dato}", "2026-08-01")));
});

test("veien tilbake står på raden, med versjonsnummeret", () => {
  const tab = publiseringstabell(PUBLISERINGER);
  assert.ok(tab.textContent.includes(
    t("ui.innhold.rollback_forrige").replace("{n}", "2")));
  assert.ok(tab.textContent.includes(
    t("ui.innhold.rollback_avpublisering")));
});

test("publiseringspanelet sier hva rollbacken vil gjøre FØR den tas", async () => {
  const h = nyHoved();
  await visInnhold(h, ctx());
  await vent(() => h.textContent.includes("produkt/hurtiglader"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.innhold.publiser_side")
      .replace("{side}", "produkt/hurtiglader"))
    .click();
  await vent(() => h.querySelector("#innhold-publisertav"));
  // Siden har en levende versjon, så veien er «tilbake til forrige».
  assert.ok(h.textContent.includes(t("ui.innhold.vei_forrige")));
});

test("publiseringspanelet nekter uten en forhåndsvisning", async () => {
  const h = nyHoved();
  // UTKASTET har ingen visninger. Publiseringslisten er urørt — og
  // det er poenget: visningene leses fra utkastet, ikke derfra.
  SVAR[`/v1/innhold/utkast/${U2}`] = {
    request_id: "r-u", utkast_id: U2, paastander: PAASTANDER,
    visninger: [],
  };
  await visInnhold(h, ctx());
  await vent(() => h.textContent.includes("produkt/hurtiglader"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.innhold.publiser_side")
      .replace("{side}", "produkt/hurtiglader"))
    .click();
  await vent(() => h.textContent.includes(t("ui.innhold.ingen_visning")));
  assert.equal(h.querySelector("#innhold-publisertav"), null);
});

test("et tomt publisert_av sendes ikke", async () => {
  const h = nyHoved();
  await visInnhold(h, ctx());
  await vent(() => h.textContent.includes("produkt/hurtiglader"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.innhold.publiser_side")
      .replace("{side}", "produkt/hurtiglader"))
    .click();
  await vent(() => h.querySelector("#innhold-publisertav"));
  const skjema = h.querySelector("#innhold-publisertav").form;
  SISTE = null;
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => h.textContent.includes(t("ui.innhold.feil.generell")));
  assert.equal(SISTE, null, "en publisering uten et navn ble sendt");
});

test("tilbakepanelet sier at siden kan bli stående avpublisert", async () => {
  const h = nyHoved();
  await visInnhold(h, ctx());
  await vent(() => h.textContent.includes("produkt/hurtiglader"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.innhold.tilbake_for")
      .replace("{side}", "produkt/hurtiglader"))
    .click();
  await vent(() => h.querySelector("#innhold-tilbakeav"));
  assert.ok(h.textContent.includes(t("ui.innhold.tilbake_forbehold")));
});


// =====================================================================
// V1-DOMMEN OG FUNNENE.
// =====================================================================

test("kilden har ingen vei som publiserer av seg selv", () => {
  // KOMMENTARER **OG STRENGER** FJERNES FØRST (128s lærdom).
  const kilde = readFileSync(join(ROT, "platform", "core", "ui",
    "static", "js", "flater", "innhold.js"), "utf8");
  const uten = kilde.split("\n")
    .filter((l) => !l.trim().startsWith("//")).join("\n")
    .replace(/"[^"\n]*"|'[^'\n]*'|`[^`]*`/g, '""');
  for (const forbudt of ["autopubliser", "publiserautomatisk",
                         "publiser_selv"]) {
    assert.ok(!uten.toLowerCase().includes(forbudt), forbudt);
  }
});

test("funntabellen leser kan_lukkes fra basen", () => {
  const kalt = [];
  const tab = funntabell(FUNN, (f) => kalt.push(f.funntype));
  const knapper = [...tab.querySelectorAll("button")];
  assert.equal(knapper.length, 1);
  knapper[0].click();
  assert.deepEqual(kalt, ["kilde_utloper_snart_uavklart"]);
});

test("en leser får ikke «lukkes av sveipen» på det den kan lukke", () => {
  // 132s CodeRabbit-funn: betingelsen står på `kan_lukkes`, ikke på
  // skrivescopet.
  const tab = funntabell(FUNN, null);
  const merker = [...tab.querySelectorAll("span")]
    .filter((x) => x.textContent === t("ui.innhold.lukkes_av_sveipen"));
  assert.equal(merker.length, 1,
    "leseren fikk teksten paa et funn et menneske kan lukke");
});

test("en leser uten skrivescope får ingen skjemaer", async () => {
  const h = nyHoved();
  await visInnhold(h, ctx(["security:read"]));
  await vent(() => h.textContent.includes(t("ui.innhold.sider")));
  assert.equal(h.querySelector("#innhold-kildedogn"), null);
  assert.equal(h.querySelector("#innhold-sideid"), null);
});

test("kravskjemaet forhåndsutfylles med ALLE tre grensene", async () => {
  const h = nyHoved();
  await visInnhold(h, ctx());
  await vent(() => h.querySelector("#innhold-kildedogn"));
  assert.equal(h.querySelector("#innhold-kildedogn").value, "365");
  assert.equal(h.querySelector("#innhold-visningmin").value, "60");
  assert.equal(h.querySelector("#innhold-varselfrist").value, "30");
});

test("utkastskjemaet sender ikke ugyldig JSON videre", async () => {
  const h = nyHoved();
  await visInnhold(h, ctx());
  await vent(() => h.querySelector("#innhold-innhold"));
  h.querySelector("#innhold-sideid").value = "forsiden";
  h.querySelector("#innhold-innhold").value = "{ikke json";
  SISTE = null;
  h.querySelector("#innhold-innhold").form.dispatchEvent(
    new window.Event("submit", { cancelable: true }));
  await vent(() => h.textContent.includes(t("ui.innhold.feil.generell")));
  assert.equal(SISTE, null, "en 500 fra jsonb ville vaert svaret");
});

test("påstandsskjemaet sier hvilken forutsetning som mangler", async () => {
  const h = nyHoved();
  SVAR["/v1/innhold"] = { ...BILDE, kilder: [] };
  await visInnhold(h, ctx());
  await vent(() => h.textContent
    .includes(t("ui.innhold.ingen_gyldig_kilde")));
  assert.equal(h.querySelector("#innhold-paastandkilde"), null);
});

test("utkastpanelet henter påstandene når noen spør", async () => {
  const h = nyHoved();
  await visInnhold(h, ctx());
  await vent(() => h.textContent.includes("produkt/hurtiglader"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.innhold.vis_utkast"))
    .click();
  await vent(() => h.textContent
    .includes("Lader til 80 prosent paa 20 minutter"));
  assert.ok(h.textContent.includes("Testrapport TR-2026-04"));
});

test("dato viser bare datodelen", () => {
  assert.equal(dato("2026-09-01T10:00:00+00:00"), "2026-09-01");
  assert.equal(dato(null), "–");
});

test("flaten er ren for axe", async () => {
  const h = nyHoved();
  await visInnhold(h, ctx());
  await vent(() => h.querySelector("#innhold-kildedogn"));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("den tomme flaten er ren for axe", async () => {
  const h = nyHoved();
  SVAR["/v1/innhold"] = TOMT;
  await visInnhold(h, ctx());
  await vent(() => h.textContent.includes(t("ui.innhold.sider_tomt")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});


// =====================================================================
// DEN VEIEN FIXTUREN MIN IKKE MÅLTE.
//
// CodeRabbit fant 5/9 at publiseringsveien lette etter visninger i
// PUBLISERINGSLISTEN. Den var galt på to måter: en side som
// publiseres for FØRSTE gang har ingen publiseringer i det hele tatt,
// og en publiseringsrad bærer `vist_ts`/`vist_for` men ikke `visning_id`.
//
// PORTENE OVER SÅ DET IKKE fordi fixturen min HADDE publiseringer for
// den siden. Den ene veien som virket var den eneste som ble målt.
// =====================================================================

test("en side som publiseres for FØRSTE gang finner sin visning", async () => {
  const h = nyHoved();
  // INGEN PUBLISERINGER I DET HELE TATT. Slik ser en ny side ut.
  SVAR["/v1/innhold"] = { ...BILDE, publiseringer: [] };
  await visInnhold(h, ctx());
  await vent(() => h.textContent.includes("produkt/hurtiglader"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.innhold.publiser_side")
      .replace("{side}", "produkt/hurtiglader"))
    .click();
  await vent(() => h.querySelector("#innhold-publisertav"));
  const valg = h.querySelector("#innhold-visningsvalg");
  assert.ok(valg, "panelet fant ingen visning paa en foerste publisering");
  // …OG ID-EN ER VISNINGENS, ikke publiseringens.
  assert.equal(valg.value, V1);
});

test("bare visninger av GJELDENDE innhold tilbys", async () => {
  // En eldre visning av et annet innhold ville blitt avvist av døra,
  // og valget skal ikke tilby den.
  const h = nyHoved();
  await visInnhold(h, ctx());
  await vent(() => h.textContent.includes("produkt/hurtiglader"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.innhold.publiser_side")
      .replace("{side}", "produkt/hurtiglader"))
    .click();
  await vent(() => h.querySelector("#innhold-visningsvalg"));
  const valg = [...h.querySelectorAll("#innhold-visningsvalg option")]
    .map((o) => o.value);
  assert.deepEqual(valg, [V1], "en visning av et annet innhold ble tilbudt");
});

test("publiseringen sender visningens id, ikke publiseringens", async () => {
  const h = nyHoved();
  await visInnhold(h, ctx());
  await vent(() => h.textContent.includes("produkt/hurtiglader"));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.innhold.publiser_side")
      .replace("{side}", "produkt/hurtiglader"))
    .click();
  await vent(() => h.querySelector("#innhold-publisertav"));
  h.querySelector("#innhold-publisertav").value = "u-per";
  h.querySelector("#innhold-publisertav").form.dispatchEvent(
    new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE !== null);
  assert.equal(SISTE.kropp.visning_id, V1);
  assert.equal(SISTE.kropp.publisert_av, "u-per");
});
