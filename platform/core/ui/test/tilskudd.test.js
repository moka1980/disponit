// M-51 tilskuddsflaten (119) — flateporten (jsdom + axe).
//
// Portene her måler det modulen står og faller på:
//
//   * SUMMEN VISES ALDRI ALENE. Et tilskuddsestimat er et tall en
//     bedrift PLANLEGGER ETTER, og ett tall er en lovnad. Hvert sted
//     et estimat står, står spennet ved siden av.
//   * `belop_uten_kildepost`: postskjemaet krever en kildepost fra en
//     liste — ikke fordi flaten sjekker det, men fordi
//     `tilskuddsestimat` i 119 ikke har en beløpskolonne.
//   * `estimat_uten_forutsetninger`: mangler de, sier flaten det med
//     `role="alert"` — det er grunnen til at estimatet ikke kan
//     ferdigstilles, ikke en tom tabell.
//   * `modulen_sendte_soknad`: ingen «send søknad»-knapp.
//   * `belop_i_flyttall`: øre overlever rundturen felt → base → felt.
//
// Ingen delt fixture (m16-formen): hver test bygger sin egen skjerm.
import { readFileSync } from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import {
  FORUTSETNINGSARTER, SYSTEMER, estimatTekst, feltTilOre,
  forutsetningTabell, fristTekst, kildepostTabell, ordningTabell,
  oreTekst, oreTilFelt, postTabell, tilstandTekst, visTilskudd,
} from "../static/js/flater/tilskudd.js";

settI18nForTest(NB, "nb");

const O1 = "11111111-1111-1111-1111-111111111111";
const O2 = "22222222-2222-2222-2222-222222222222";
const E1 = "aaaaaaaa-1111-1111-1111-111111111111";
const K1 = "bbbbbbbb-1111-1111-1111-111111111111";
const K2 = "bbbbbbbb-2222-2222-2222-222222222222";

const BILDE = {
  sammendrag: {
    ordninger: 8, aktive: 5, med_estimat: 3, klare: 1,
    sum_klare_ore: 36000000,
    naermeste_frist: "2026-10-01T12:00:00+00:00", apne_funn: 2,
    kildeposter: 2, utdaterte_kildeposter: 1, har_krav: true,
    kravversjon: 2, vist: 2,
  },
  ordninger: [
    { ordning_id: O1, ordningskode: "SKATTEFUNN",
      navn: "Skattefunn 2026", forvalter: "Forskningsrådet",
      regelverksversjon: "2026-rev3", maks_belop_ore: 40000000,
      sats_prosent: 19, soknadsfrist: "2026-10-01T12:00:00+00:00",
      aktiv: true, dogn_til_frist: 27, siste_estimat: 1,
      estimat_id: E1, klar: false, sum_ore: 36000000,
      nedre_ore: 28800000, ovre_ore: 43200000, antall_poster: 1,
      antall_forutsetninger: 0, apne_funn: 1 },
    { ordning_id: O2, ordningskode: "KOMMUNAL",
      navn: "Kommunalt tilskudd", forvalter: "Oslo kommune",
      regelverksversjon: "2026-v1", maks_belop_ore: null,
      sats_prosent: null, soknadsfrist: "2026-08-01T12:00:00+00:00",
      aktiv: false, dogn_til_frist: -34, siste_estimat: null,
      estimat_id: null, klar: false, sum_ore: null, nedre_ore: null,
      ovre_ore: null, antall_poster: 0, antall_forutsetninger: 0,
      apne_funn: 1 },
  ],
  kildeposter: [
    { kildepost_id: K1, system: "lonn", ekstern_ref: "LA-2026-01",
      beskrivelse: "Utvikler, 1200 timer", belop_ore: 90000000,
      periode_fra: "2026-01-01", periode_til: "2026-06-30",
      registrert: "2026-07-01T09:00:00+00:00", registrert_av: "kari",
      fersk: true, brukt_i_poster: 1 },
    { kildepost_id: K2, system: "regnskap", ekstern_ref: "BIL-99",
      beskrivelse: "Gammel post", belop_ore: 5000000,
      periode_fra: "2024-01-01", periode_til: "2024-06-30",
      registrert: "2024-07-01T09:00:00+00:00", registrert_av: "kari",
      fersk: false, brukt_i_poster: 0 },
  ],
  krav: {
    frist_varsel_dogn: 21, kildepost_gyldig_dogn: 400,
    usikkerhet_prosent: 20, versjon: 2,
    oppdatert: "2026-08-01T09:00:00+00:00", oppdatert_av: "kari",
  },
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    ordninger: 0, aktive: 0, med_estimat: 0, klare: 0,
    sum_klare_ore: 0, naermeste_frist: null, apne_funn: 0,
    kildeposter: 0, utdaterte_kildeposter: 0, har_krav: false,
    kravversjon: null, vist: 0,
  },
  ordninger: [], kildeposter: [], krav: null, request_id: "r-b",
};

const ESTIMATER = {
  ordning_id: O1,
  estimater: [
    { estimat_id: E1, versjon: 1, periode_fra: "2026-01-01",
      periode_til: "2026-06-30", usikkerhet_prosent: 20,
      kravversjon: 2, klar_til_gjennomgang: false, klar_ts: null,
      klar_av: null, opprettet: "2026-07-02T09:00:00+00:00",
      opprettet_av: "kari", sum_ore: 36000000, antall_poster: 1,
      antall_forutsetninger: 0 },
  ],
  request_id: "r-c",
};

const POSTER = {
  estimat_id: E1,
  poster: [
    { post_id: "cccccccc-1111-1111-1111-111111111111",
      kildepost_id: K1, system: "lonn", ekstern_ref: "LA-2026-01",
      beskrivelse: "Utvikler, 1200 timer", kilde_belop_ore: 90000000,
      andel_ore: 36000000,
      begrunnelse: "19 % av lønnskostnaden, jf. regelverk",
      periode_fra: "2026-01-01", periode_til: "2026-06-30",
      registrert: "2026-07-02T09:00:00+00:00", registrert_av: "kari" },
  ],
  request_id: "r-d",
};

const FORUTSETNINGER = {
  estimat_id: E1, forutsetninger: [], request_id: "r-e",
};

const FUNN = {
  request_id: "r-f",
  funn: [
    { ordning_id: O1, ordningskode: "SKATTEFUNN",
      navn: "Skattefunn 2026",
      soknadsfrist: "2026-10-01T12:00:00+00:00",
      funntype: "estimat_over_ordningstak", over_grense: null,
      detalj: null, sum_ore: 36000000, kravversjon: 2,
      forst_sett: "2026-09-02T09:00:00+00:00",
      sist_sett_sveip: "2026-09-03T09:00:00+00:00", apen: true,
      lukket_ts: null },
    { ordning_id: O2, ordningskode: "KOMMUNAL",
      navn: "Kommunalt tilskudd",
      soknadsfrist: "2026-08-01T12:00:00+00:00",
      funntype: "frist_passert", over_grense: -34,
      detalj: "2026-08-01", sum_ore: null, kravversjon: 2,
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
        json: async () => ({ feil: "tilskudd_ulovlig_tilstand" }) };
    }
    return { ok: true, status: 200,
             json: async () => ({ ok: true, sum_ore: 36000000,
                                  nedre_ore: 28800000,
                                  ovre_ore: 43200000 }) };
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
  KALL = []; SISTE = undefined; SVARSTATUS = 200;
  return m;
}

function fullSvar() {
  return {
    "/v1/tilskudd": BILDE,
    "/v1/tilskudd/funn": FUNN,
    [`/v1/tilskudd/${O1}/estimater`]: ESTIMATER,
    [`/v1/tilskudd/estimat/${E1}/poster`]: POSTER,
    [`/v1/tilskudd/estimat/${E1}/forutsetninger`]: FORUTSETNINGER,
    [`/v1/tilskudd/${O2}/estimater`]: { ordning_id: O2, estimater: [],
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
  // VENT PÅ NOE SOM BARE FINNES I PANELET (118s lærdom).
  await vent(() => [...h.querySelectorAll("caption")].some(
    (c) => c.textContent === t("ui.tilskudd.estimater.tittel")));
}

// ---------------------------------------------------------------------
// SUMMEN VISES ALDRI ALENE
// ---------------------------------------------------------------------

test("Tilskudd: et estimat vises alltid med spennet", () => {
  // ETT TALL ER EN LOVNAD, ET INTERVALL ER ET ESTIMAT.
  //
  // MUTASJONEN SOM DREPER DENNE: la `estimatTekst` returnere bare
  // summen.
  const tekst = estimatTekst({
    sum_ore: 36000000, nedre_ore: 28800000, ovre_ore: 43200000,
    antall_poster: 1,
  });
  assert.ok(tekst.includes("360000,00"), tekst);
  assert.ok(tekst.includes("288000,00"), tekst);
  assert.ok(tekst.includes("432000,00"), tekst);
  // Uten poster er det ikke et estimat i det hele tatt.
  assert.equal(estimatTekst({ sum_ore: 0, antall_poster: 0 }),
    t("ui.tilskudd.uten_poster"));
  assert.equal(estimatTekst(null), t("ui.tilskudd.uten_estimat"));
});

test("Tilskudd: spennet står i ordningstabellen", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visTilskudd(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  const rad = tabeller(h)[0].querySelector("tbody tr");
  assert.ok(rad.textContent.includes("288000,00"),
    "spennet manglet i tabellen");
  assert.ok(rad.textContent.includes("432000,00"));
});

test("Tilskudd: sammendraget tar forbehold", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visTilskudd(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  const tekst = h.textContent;
  assert.ok(tekst.includes(t("ui.tilskudd.oversikt.hvorfor")));
  // …og summen av de klare bærer ordet «forbehold»/«subject to».
  const mal = t("ui.tilskudd.sum_klare");
  assert.ok(/forbehold|subject to/i.test(mal), mal);
});

// ---------------------------------------------------------------------
// belop_uten_kildepost — INGEN VEI TIL ET FRITT BELØP
// ---------------------------------------------------------------------

test("Tilskudd: postskjemaet krever kildepost, andel og begrunnelse",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visTilskudd(h, ctx());
    await apneForste(h);
    await vent(() => h.querySelector("#ti-p-kilde") !== null);
    const kilde = h.querySelector("#ti-p-kilde");
    const andel = h.querySelector("#ti-p-andel");
    const begr = h.querySelector("#ti-p-begrunnelse");
    const lagre = [...h.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.tilskudd.knapp.lagre_post"));
    assert.equal(lagre.disabled, true, "knappen var levende fra start");

    kilde.value = K1;
    kilde.dispatchEvent(new window.Event("change"));
    assert.equal(lagre.disabled, true, "kilde alene åpnet knappen");

    andel.value = "360000";
    andel.dispatchEvent(new window.Event("input"));
    assert.equal(lagre.disabled, true, "kilde+andel alene holdt");

    begr.value = "19 % av lønnskostnaden";
    begr.dispatchEvent(new window.Event("input"));
    assert.equal(lagre.disabled, false, "alle tre fylt ut, død knapp");
  });

test("Tilskudd: bare FERSKE kildeposter tilbys", async () => {
  // Døra ville nektet en for gammel, men en knapp som alltid feiler
  // er verre enn en valgmulighet som ikke finnes.
  SVAR = fullSvar();
  const h = nyHoved();
  visTilskudd(h, ctx());
  await apneForste(h);
  await vent(() => h.querySelector("#ti-p-kilde") !== null);
  const verdier = [...h.querySelector("#ti-p-kilde").options]
    .map((o) => o.value).filter(Boolean);
  assert.deepEqual(verdier, [K1], "en utdatert kildepost ble tilbudt");
  // …og beløpet står i valget, så andelen kan settes med kontekst.
  const tekst = [...h.querySelector("#ti-p-kilde").options]
    .map((o) => o.textContent).join(" ");
  assert.ok(tekst.includes("900000,00"), tekst);
});

test("Tilskudd: posten sender kildepost, andel i ØRE og begrunnelse",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visTilskudd(h, ctx());
    await apneForste(h);
    await vent(() => h.querySelector("#ti-p-kilde") !== null);
    const kilde = h.querySelector("#ti-p-kilde");
    const andel = h.querySelector("#ti-p-andel");
    const begr = h.querySelector("#ti-p-begrunnelse");
    kilde.value = K1; kilde.dispatchEvent(new window.Event("change"));
    andel.value = "360000.55";
    andel.dispatchEvent(new window.Event("input"));
    begr.value = "19 % av lønnskostnaden";
    begr.dispatchEvent(new window.Event("input"));
    kilde.form.dispatchEvent(
      new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE !== undefined);
    assert.equal(SISTE.sti, `/v1/tilskudd/estimat/${E1}/post`);
    assert.equal(SISTE.kropp.kildepost_id, K1);
    // ØRE, HELTALL — ikke 36000055.000000001.
    assert.equal(SISTE.kropp.andel_ore, 36000055);
    assert.ok(SISTE.headers["Idempotency-Key"], SISTE.headers);
  });

// ---------------------------------------------------------------------
// estimat_uten_forutsetninger
// ---------------------------------------------------------------------

test("Tilskudd: manglende forutsetninger sies høyt", async () => {
  // Fraværet er GRUNNEN til at estimatet ikke kan ferdigstilles, ikke
  // en tom tabell.
  SVAR = fullSvar();
  const h = nyHoved();
  visTilskudd(h, ctx());
  await apneForste(h);
  const varsel = [...h.querySelectorAll('[role="alert"]')]
    .map((n) => n.textContent);
  assert.ok(varsel.some(
    (x) => x === t("ui.tilskudd.uten_forutsetninger_varsel")), varsel);
});

test("Tilskudd: forutsetningsskjemaet krever art, tekst OG konsekvens",
  async () => {
    // En forutsetning uten konsekvens er en ansvarsfraskrivelse.
    SVAR = fullSvar();
    const h = nyHoved();
    visTilskudd(h, ctx());
    await apneForste(h);
    await vent(() => h.querySelector("#ti-f-art") !== null);
    const art = h.querySelector("#ti-f-art");
    const tekst = h.querySelector("#ti-f-tekst");
    const kons = h.querySelector("#ti-f-konsekvens");
    const lagre = [...h.querySelectorAll("button")].find(
      (b) => b.textContent
        === t("ui.tilskudd.knapp.lagre_forutsetning"));
    assert.equal(lagre.disabled, true);
    art.value = "regnskapstall";
    art.dispatchEvent(new window.Event("change"));
    tekst.value = "Timetallet er ikke revidert";
    tekst.dispatchEvent(new window.Event("input"));
    assert.equal(lagre.disabled, true, "uten konsekvens holdt");
    kons.value = "Reduseres proporsjonalt";
    kons.dispatchEvent(new window.Event("input"));
    assert.equal(lagre.disabled, false);
    // …og arten har ingen forhåndsvalgt verdi.
    assert.equal(
      [...art.options][0].value, "");
    assert.deepEqual(
      [...art.options].map((o) => o.value).filter(Boolean),
      FORUTSETNINGSARTER);
  });

test("Tilskudd: forutsetningen viser sin KONSEKVENS", () => {
  const node = forutsetningTabell([{
    forutsetning_id: "x", art: "regnskapstall",
    tekst: "Timetallet er ikke revidert",
    konsekvens: "Reduseres proporsjonalt", registrert_av: "kari",
  }]);
  assert.ok(node.textContent.includes("Reduseres proporsjonalt"));
  const kol = [...node.querySelectorAll('th[scope="col"]')]
    .map((n) => n.textContent);
  assert.ok(kol.includes(t("ui.tilskudd.kol.konsekvens")), kol);
});

// ---------------------------------------------------------------------
// modulen_sendte_soknad — FLATEN HAR INGEN UTGÅENDE VEI
// ---------------------------------------------------------------------

test("Tilskudd: ingen kontroll sender en søknad noe sted", async () => {
  // M-51 REGISTRERER OG MÅLER. Det finnes ingen knapp som sender noe
  // ut av huset, og «klar til gjennomgang» er en tilstand HOS OSS —
  // hjelpeteksten sier det med rene ord.
  SVAR = fullSvar();
  const h = nyHoved();
  visTilskudd(h, ctx());
  await apneForste(h);
  const kontroller = [...h.querySelectorAll("button, a[href]")]
    .map((n) => n.textContent.toLowerCase());
  for (const k of kontroller) {
    assert.ok(!/send|søk|innsend|lever|submit|apply/.test(k),
      `utgående kontroll: «${k}»`);
  }
  assert.equal(h.querySelectorAll('a[href^="http"]').length, 0);
  assert.ok(h.textContent.includes(t("ui.tilskudd.ferdigstill_hjelp")));
  // …og alle kall gikk til vår egen /v1/tilskudd.
  for (const k of KALL) {
    assert.ok(k.sti.startsWith("/v1/tilskudd"), k.sti);
  }
});

test("Tilskudd: ferdigstilling melder spennet, ikke bare «ok»",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visTilskudd(h, ctx());
    await apneForste(h);
    const knapp = [...h.querySelectorAll("button")].find(
      (b) => b.textContent === t("ui.tilskudd.knapp.ferdigstill"));
    assert.ok(knapp, "ferdigstill-knappen manglet");
    knapp.click();
    await vent(() => SISTE !== undefined);
    assert.equal(SISTE.sti, `/v1/tilskudd/estimat/${E1}/ferdigstill`);
    await vent(() => document.body.textContent.includes("288000,00"));
    const tekst = document.body.textContent;
    assert.ok(tekst.includes("360000,00") && tekst.includes("432000,00"),
      "kvitteringen bar ikke spennet");
  });

test("Tilskudd: en avvist ferdigstilling SIER det", async () => {
  // 117s lærdom: et klikk som ikke virker og ikke sier noe, er verre
  // enn et som feiler høyt.
  SVAR = fullSvar();
  const h = nyHoved();
  visTilskudd(h, ctx());
  await apneForste(h);
  SVARSTATUS = 409;
  const knapp = [...h.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.tilskudd.knapp.ferdigstill"));
  knapp.click();
  await vent(() => document.body.textContent.includes(
    t("ui.tilskudd.feil.uten_forutsetninger")));
  assert.ok(document.body.textContent.includes(
    t("ui.tilskudd.feil.uten_forutsetninger")));
  assert.equal(knapp.disabled, false, "knappen ble liggende død");
});

// ---------------------------------------------------------------------
// FUNN
// ---------------------------------------------------------------------

test("Tilskudd: et takfunn kan ikke lukkes fra flaten", async () => {
  // Døra nekter det (119). Flaten tilbyr det derfor ikke — en knapp
  // som alltid feiler lærer brukeren at systemet er upålitelig.
  SVAR = fullSvar();
  const h = nyHoved();
  visTilskudd(h, ctx());
  await vent(() => h.querySelector("#ti-fn-valg") !== null);
  const verdier = [...h.querySelector("#ti-fn-valg").options]
    .map((o) => o.value).filter(Boolean);
  assert.equal(verdier.length, 1);
  assert.ok(verdier[0].endsWith("frist_passert"), verdier[0]);
  assert.ok(!verdier.some((v) => v.includes("over_ordningstak")),
    "takfunnet ble tilbudt for lukking");
  // …men det STÅR i tabellen, med summen sin.
  const funntabell = [...h.querySelectorAll("table")].find(
    (tb) => tb.querySelector("caption").textContent
      === t("ui.tilskudd.funn.tittel"));
  assert.ok(funntabell.textContent.includes("360000,00"),
    "funnet manglet summen — «over taket» uten hvor mye");
});

test("Tilskudd: funnet lukkes med ordning OG funntype", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visTilskudd(h, ctx());
  await vent(() => h.querySelector("#ti-fn-valg") !== null);
  const valg = h.querySelector("#ti-fn-valg");
  const notat = h.querySelector("#ti-fn-notat");
  valg.value = [...valg.options].map((o) => o.value).filter(Boolean)[0];
  notat.value = "Fristen gjaldt fjorårets runde";
  valg.form.dispatchEvent(new window.Event("submit",
    { cancelable: true }));
  await vent(() => SISTE !== undefined);
  assert.equal(SISTE.sti, `/v1/tilskudd/${O2}/funn/lukk`);
  // BEGGE DELER: funnets nøkkel er (ordning, funntype). Sendte
  // flaten bare ordningen, ville et vilkårlig av ordningens funn
  // blitt lukket.
  assert.equal(SISTE.kropp.funntype, "frist_passert");
  assert.equal(SISTE.kropp.notat, "Fristen gjaldt fjorårets runde");
});

// ---------------------------------------------------------------------
// ØRE
// ---------------------------------------------------------------------

test("Tilskudd: øre overlever rundturen felt → base → felt", () => {
  // 118s FEIL: `Math.floor(ore / 100)` + `Number(kr) * 100` gjorde
  // 123456 øre om til 123400 — på en lagring brukeren gjorde av en
  // helt annen grunn.
  for (const ore of [0, 1, 99, 100, 123456, 36000055, 90000000,
                     999999999999, -4567]) {
    assert.equal(feltTilOre(oreTilFelt(ore)), ore, String(ore));
  }
  assert.equal(feltTilOre("360000,55"), 36000055, "komma");
  assert.equal(feltTilOre("360000.5"), 36000050, "én desimal");
  assert.equal(feltTilOre(""), null);
  assert.equal(feltTilOre("tolv"), null);
  assert.equal(oreTilFelt(null), "");
  assert.equal(oreTekst(123456), "1234,56");
  assert.equal(oreTekst(5), "0,05");
  assert.equal(oreTekst(null), "–");
});

test("Tilskudd: hvert beløpsfelt tar imot øre", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visTilskudd(h, ctx());
  await apneForste(h);
  const tall = [...h.querySelectorAll('input[type="number"]')]
    .filter((i) => /belop|andel|maks|sum/.test(i.id));
  assert.ok(tall.length >= 3, tall.map((i) => i.id).join(","));
  for (const i of tall) {
    assert.equal(i.getAttribute("step"), "0.01", i.id);
  }
});

test("Tilskudd: ferskheten har TRE tilstander", () => {
  // `null` er ikke «utdatert» — det betyr at tenanten ikke har satt
  // terskler, så vinduet ikke KAN regnes. Å vise det som «nei» ville
  // sagt noe vi ikke vet.
  const node = kildepostTabell([...BILDE.kildeposter,
    { ...BILDE.kildeposter[0], kildepost_id: "k3",
      ekstern_ref: "UKJ-1", fersk: null }]);
  const rader = [...node.querySelectorAll("tbody tr")]
    .map((r) => r.textContent);
  assert.equal(rader.length, 3);
  assert.ok(rader[0].includes(t("ui.tilskudd.ja")), rader[0]);
  assert.ok(rader[1].includes(t("ui.tilskudd.nei")), rader[1]);
  assert.ok(rader[2].includes(t("ui.tilskudd.ukjent")), rader[2]);
  // …og kildepostens eget beløp står der, så andelen kan etterprøves.
  assert.ok(rader[0].includes("900000,00"), rader[0]);
});

// ---------------------------------------------------------------------
// FRIST OG TILSTAND
// ---------------------------------------------------------------------

test("Tilskudd: fristen har retning", () => {
  assert.equal(fristTekst(3), t("ui.tilskudd.frist_om")
    .replace("{n}", "3"));
  assert.equal(fristTekst(0), t("ui.tilskudd.frist_i_dag"));
  assert.equal(fristTekst(-34), t("ui.tilskudd.frist_passert")
    .replace("{n}", "34"));
  assert.equal(fristTekst(null), "–");
});

test("Tilskudd: tilstanden navngir det som mangler", () => {
  assert.equal(tilstandTekst({ estimat_id: null }),
    t("ui.tilskudd.uten_estimat"));
  assert.equal(tilstandTekst({ estimat_id: E1, antall_poster: 0 }),
    t("ui.tilskudd.uten_poster"));
  assert.equal(
    tilstandTekst({ estimat_id: E1, antall_poster: 2,
                    antall_forutsetninger: 0 }),
    t("ui.tilskudd.uten_forutsetninger"));
  assert.equal(
    tilstandTekst({ estimat_id: E1, antall_poster: 2,
                    antall_forutsetninger: 1, klar: true }),
    t("ui.tilskudd.klart"));
});

// ---------------------------------------------------------------------
// LESERETT, TABELLER, SPRÅK OG AXE
// ---------------------------------------------------------------------

test("Tilskudd: en lesende økt ser tallene, men ingen skriveveier",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visTilskudd(h, ctx(["okonomi:read"]));
    await vent(() => tabeller(h).length >= 2);
    assert.ok(h.textContent.includes("Skattefunn 2026"));
    assert.ok(h.textContent.includes("288000,00"),
      "leseren fikk summen uten spennet");
    assert.equal(h.querySelector("form"), null,
      "en lesende økt fikk et skjema");
    await apneForste(h);
    for (const n of ["ferdigstill", "lagre_post", "lagre_forutsetning",
                     "nytt_estimat", "aktiver", "deaktiver"]) {
      const k = [...h.querySelectorAll("button")].find(
        (b) => b.textContent === t(`ui.tilskudd.knapp.${n}`));
      assert.equal(k, undefined, `lesende økt fikk «${n}»`);
    }
  });

test("Tilskudd: hver tabell er en ekte tabell", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visTilskudd(h, ctx());
  await apneForste(h);
  const t2 = tabeller(h);
  assert.ok(t2.length >= 5, `bare ${t2.length} tabeller`);
  for (const tab of t2) {
    assert.ok(tab.querySelector("caption"), "tabell uten caption");
    assert.ok(tab.querySelectorAll('th[scope="col"]').length > 0,
      "tabell uten kolonneoverskrifter");
    assert.ok(tab.closest(".tablewrap"),
      "tabell uten sidescroll-container");
    const rad = tab.querySelector("tbody tr");
    if (rad) {
      assert.ok(rad.querySelector('th[scope="row"]'),
        "rad uten th[scope=row]");
    }
  }
});

test("Tilskudd: ingen hardkodet tekst", async () => {
  const PL = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visTilskudd(h, ctx());
    await vent(() => tabeller(h).length >= 2);
    // `th[scope="row"]` er UTELATT: den cellen bærer ordningsnavnet
    // og den eksterne referansen — tenantens egne data.
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

test("Tilskudd: null alvorlige axe-brudd på oversikten", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visTilskudd(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Tilskudd: null alvorlige axe-brudd med detaljpanelet åpent",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visTilskudd(h, ctx());
    await apneForste(h);
    await vent(() => h.querySelector("#ti-p-kilde") !== null);
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Tilskudd: null alvorlige axe-brudd på et tomt register",
  async () => {
    SVAR = { ...fullSvar(), "/v1/tilskudd": TOMT,
             "/v1/tilskudd/funn": { request_id: "r-i", funn: [] } };
    const h = nyHoved();
    visTilskudd(h, ctx());
    await vent(() => h.textContent.includes(
      t("ui.tilskudd.liste.ingen")));
    assert.ok(h.textContent.includes(t("ui.tilskudd.ingen_krav")),
      "et register uten terskler sa det ikke");
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Tilskudd: tabellene står alene uten brudd", async () => {
  let brudd = await alvorligeBrudd(ordningTabell(BILDE.ordninger,
                                                 () => {}),
                                   { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  brudd = await alvorligeBrudd(postTabell(POSTER.poster),
                               { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  brudd = await alvorligeBrudd(kildepostTabell(BILDE.kildeposter),
                               { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Tilskudd: systemlisten er 119s egen liste", () => {
  // KILDEPOSTENS SYSTEM ER EN CHECK I BASEN. En port som gjentok
  // lista her ville bare målt seg selv, og en flate som tilbød
  // «prosjekt» ville gitt brukeren et valg døra avviser. Porten
  // leser derfor CHECK-en i migrasjonen.
  const sql = readFileSync(new URL(
    "../../db/migrations/119_m51_tilskuddsregister.sql",
    import.meta.url), "utf8");
  const m = /kildepost_system_lukket CHECK \(system IN \(([^)]*)\)/
    .exec(sql);
  assert.ok(m, "fant ikke CHECK-en i 119");
  const fra_basen = [...m[1].matchAll(/'([a-z_]+)'/g)].map((x) => x[1]);
  assert.deepEqual(SYSTEMER, fra_basen);
});
