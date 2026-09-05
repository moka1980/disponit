// M-15 likviditetsflaten (128) — flateporten (jsdom + axe).
//
// PORTENE MÅLER KLYNGENS DOM, IKKE BARE AT SKJERMEN TEGNES.
//
//   En gal prognose ser nøyaktig ut som en riktig prognose — helt til
//   horisonten er passert, og da har alle sluttet å se.
//
// Derfor måler portene her:
//
//   * at BÅNDET tegnes i samme rad som punktet. En bane vist som én
//     linje ville sett ut som en presis prognose.
//   * at `umaalte` og treffraten står FØRST i sammendraget, foran
//     tallet på hvor mange prognoser vi har laget.
//   * at «ingen målinger» ikke ser ut som «alt stemmer».
//   * at «mål denne uken» bare finnes når `kan_maales` fra BASEN sier
//     det — flaten regner ikke selv.
//   * at det ikke finnes en «iverksett»-knapp, og ikke kan finnes en.
//   * at ØRE deles på 100 kun i visningen.
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
  POSTTYPER, REVERSIBILITET, banetabell, funntabell, ilokalDato,
  kroner, modelltabell, posttabell, prognosetabell, sammendrag,
  tiltakstabell, visLikviditet,
} from "../static/js/flater/likviditet.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));
const ROT = join(HER, "..", "..", "..", "..");

const P1 = "aaaaaaaa-1111-1111-1111-111111111111";
const P2 = "aaaaaaaa-2222-2222-2222-222222222222";
const M1 = "bbbbbbbb-1111-1111-1111-111111111111";
const T1 = "cccccccc-1111-1111-1111-111111111111";
const F1 = "dddddddd-1111-1111-1111-111111111111";
const F2 = "dddddddd-2222-2222-2222-222222222222";

// TO PROGNOSER: én aktiv, én hvis horisont er passert UTEN måling.
const PROGNOSER = [
  { prognose_id: P1, laget_dato: "2026-09-01", horisont_uker: 13,
    gjelder_til: "2026-12-01", modellversjon: "2026-01",
    baselinje: "samme som forrige uke", startsaldo_ore: 120000000,
    laveste_ore: -4050000, grunnlag_alder_dogn: 2, antall_uker: 13,
    antall_maalinger: 0, treff: 0, kravversjon: 1,
    opprettet_av: "u-kari", aktiv: true },
  { prognose_id: P2, laget_dato: "2026-05-01", horisont_uker: 13,
    gjelder_til: "2026-07-31", modellversjon: "2026-01",
    baselinje: "samme som forrige uke", startsaldo_ore: 90000000,
    laveste_ore: 15000000, grunnlag_alder_dogn: 1, antall_uker: 13,
    antall_maalinger: 0, treff: 0, kravversjon: 1,
    opprettet_av: "u-kari", aktiv: false },
];

const BANE = {
  request_id: "r-b", prognose_id: P1,
  bane: [
    // UKE 1: over, målt, og traff.
    { uke_nr: 1, ukeslutt: "2026-09-08", punkt_ore: 111500000,
      nedre_ore: 94775000, ovre_ore: 128225000, inn_ore: 0,
      ut_ore: -8500000, faktisk_ore: 112000000, avvik_ore: 500000,
      innenfor_intervall: true, maalt_av: "u-kari",
      kan_maales: false },
    // UKE 2: over, IKKE målt → knappen skal finnes.
    { uke_nr: 2, ukeslutt: "2026-09-15", punkt_ore: 79500000,
      nedre_ore: 67575000, ovre_ore: 91425000, inn_ore: 0,
      ut_ore: -32000000, faktisk_ore: null, avvik_ore: null,
      innenfor_intervall: null, maalt_av: null, kan_maales: true },
    // UKE 3: ikke over ennå → ingen knapp.
    { uke_nr: 3, ukeslutt: "2026-09-22", punkt_ore: -4050000,
      nedre_ore: -4657500, ovre_ore: -3442500, inn_ore: 0,
      ut_ore: 0, faktisk_ore: null, avvik_ore: null,
      innenfor_intervall: null, maalt_av: null, kan_maales: false },
  ],
};

const MODELLER = [
  { modell_id: M1, navn: "Kumulativ kontantbane", versjon: "2026-01",
    metode: "Startsaldo fra bankposter, fordringer inn paa forfall.",
    baselinje: "samme som forrige uke", gyldig_fra: "2026-01-01",
    gyldig_til: null, gyldig_naa: true, dogn_til_utlop: null,
    antall_prognoser: 2 },
];

const POSTER = [
  { post_id: "eeee0000-1111-1111-1111-111111111111",
    posttype: "lonn", beskrivelse: "Lonn seks ansatte, netto",
    belop_ore: -32000000, forste_forfall: "2026-09-15",
    gjentakelse: "maanedlig", gjelder_til: null, aktiv: true,
    registrert: "2026-09-01T09:00:00+00:00",
    registrert_av: "u-kari" },
];

const TILTAK = [
  { tiltak_id: T1, beskrivelse: "Si opp fjorten ubrukte lisenser",
    forventet_effekt_ore: 4500000, reversibilitet: "irreversibel",
    grunnlag: "Ingen paalogging siste nitti dager", status: "foreslatt",
    vurdert_av: null, vurderingsnotat: null,
    opprettet: "2026-09-01T09:00:00+00:00", opprettet_av: "u-kari" },
];

const FUNN = [
  // SVEIPENS EGET — ingen kan lukke det.
  { funn_id: F1, funntype: "prognose_uten_maaling", prognose_id: P2,
    modell_id: null, over_grense: 22,
    detalj: "13 uker, ingen måling", kravversjon: 1,
    forst_sett: "2026-08-15T09:00:00+00:00",
    sist_sett_sveip: "2026-09-05T09:00:00+00:00", apen: true,
    lukket_ts: null, lukket_av: null, lukkenotat: null,
    kan_lukkes: false },
  // ET VARSEL — det kan lukkes.
  { funn_id: F2, funntype: "bane_under_null", prognose_id: P1,
    modell_id: null, over_grense: 4050000,
    detalj: "under null fra uke 3", kravversjon: 1,
    forst_sett: "2026-09-02T09:00:00+00:00",
    sist_sett_sveip: "2026-09-05T09:00:00+00:00", apen: true,
    lukket_ts: null, lukket_av: null, lukkenotat: null,
    kan_lukkes: true },
];

const BILDE = {
  request_id: "r-x",
  sammendrag: {
    prognoser: 2, aktive: 1, maalte: 1, umaalte: 1, treff: 1, bom: 0,
    modeller: 1, gyldige_modeller: 1, poster: 1, tiltak: 1,
    uvurderte_tiltak: 1, apne_funn: 2, laveste_ore: -4050000,
    har_krav: true, horisont_uker: 13, grunnlag_maks_alder_dogn: 7,
    maalefrist_dogn: 14, modellvarsel_dogn: 30, kravversjon: 1,
  },
  prognoser: PROGNOSER, modeller: MODELLER, poster: POSTER,
  tiltak: TILTAK, funn: FUNN,
};

const TOMT = {
  request_id: "r-t",
  sammendrag: {
    prognoser: 0, aktive: 0, maalte: 0, umaalte: 0, treff: 0, bom: 0,
    modeller: 0, gyldige_modeller: 0, poster: 0, tiltak: 0,
    uvurderte_tiltak: 0, apne_funn: 0, laveste_ore: null,
    har_krav: false, horisont_uker: null,
    grunnlag_maks_alder_dogn: null, maalefrist_dogn: null,
    modellvarsel_dogn: null, kravversjon: null,
  },
  prognoser: [], modeller: [], poster: [], tiltak: [], funn: [],
};

let SVAR;
let SISTE;
let KALL;
let SVARSTATUS;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  KALL.push({ sti, metode: (opts && opts.method) || "GET" });
  if (opts && opts.method === "POST") {
    SISTE = { sti, headers: opts.headers,
              kropp: opts.body ? JSON.parse(opts.body) : null };
    if (SVARSTATUS && SVARSTATUS !== 200) {
      return { ok: false, status: SVARSTATUS,
        json: async () => ({ feil: "likviditet_ulovlig_tilstand" }) };
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
  SVAR = { "/v1/likviditet": BILDE,
           [`/v1/likviditet/prognose/${P1}/bane`]: BANE };
  SISTE = null;
  KALL = [];
  SVARSTATUS = 200;
  return m;
}


// =====================================================================
// KLYNGENS DOM: MÅLINGEN ER PRODUKTET.
// =====================================================================

test("sammendraget setter umålte prognoser først", () => {
  const p = sammendrag(BILDE.sammendrag);
  const sterke = [...p.querySelectorAll("strong")];
  assert.ok(sterke.length >= 2);
  // UMÅLTE FØRST. Et sammendrag som begynte med «2 prognoser» ville
  // fortalt hvor flittige vi har vært, ikke om vi hadde rett.
  assert.ok(sterke[0].textContent.includes("1"));
  assert.ok(sterke[0].textContent
    .includes(t("ui.likviditet.umaalte_sum").split("{")[0].trim()));
});

test("ingen målinger ser ikke ut som at alt stemmer", () => {
  const s = { ...BILDE.sammendrag, treff: 0, bom: 0, umaalte: 0 };
  const p = sammendrag(s);
  assert.ok(p.textContent.includes(t("ui.likviditet.ingen_maalinger")));
  // …og det står som et VARSEL, ikke som en fotnote.
  assert.ok([...p.querySelectorAll("strong")]
    .some((x) => x.getAttribute("role") === "alert"));
});

test("en bane under null står i fet skrift i sammendraget", () => {
  const p = sammendrag(BILDE.sammendrag);
  assert.ok([...p.querySelectorAll("strong")]
    .some((x) => x.textContent.includes("40 500")));
});

test("banetabellen viser båndet i samme rad som punktet", () => {
  const tab = banetabell(BANE.bane, null);
  const rader = [...tab.querySelectorAll("tbody tr")];
  assert.equal(rader.length, 3);
  const celler = [...rader[0].querySelectorAll("td")]
    .map((c) => c.textContent);
  // punkt, nedre, ovre — alle tre synlige, ingen bak et klikk.
  assert.ok(celler.some((c) => c.includes("1 115 000")));
  assert.ok(celler.some((c) => c.includes("947 750")));
  assert.ok(celler.some((c) => c.includes("1 282 250")));
});

test("«mål denne uken» finnes bare der basen sier kan_maales", () => {
  const kalt = [];
  const tab = banetabell(BANE.bane, (u) => kalt.push(u.uke_nr));
  const knapper = [...tab.querySelectorAll("button")];
  // UKE 2 ALENE: uke 1 er målt, uke 3 er ikke over.
  assert.equal(knapper.length, 1);
  knapper[0].click();
  assert.deepEqual(kalt, [2]);
});

test("bom på intervallet står i fet skrift, treff gjør det ikke", () => {
  const bom = [{ ...BANE.bane[0], innenfor_intervall: false }];
  const tabBom = banetabell(bom, null);
  assert.ok([...tabBom.querySelectorAll("strong")]
    .some((x) => x.textContent === t("ui.likviditet.bom")));
  const tabTreff = banetabell([BANE.bane[0]], null);
  assert.ok(![...tabTreff.querySelectorAll("strong")]
    .some((x) => x.textContent === t("ui.likviditet.traff")));
});

test("en umålt prognose merkes i tabellen", () => {
  const tab = prognosetabell(PROGNOSER, null);
  assert.ok([...tab.querySelectorAll("strong")]
    .some((x) => x.textContent === t("ui.likviditet.umaalt")));
});


// =====================================================================
// V1-DOMMEN: MODULEN UTFØRER INGENTING.
// =====================================================================

test("et irreversibelt tiltak står i fet skrift", () => {
  const tab = tiltakstabell(TILTAK, null);
  assert.ok([...tab.querySelectorAll("strong")]
    .some((x) => x.textContent
      === t("ui.likviditet.rev_irreversibel")));
});

test("tiltakstabellen har ingen iverksett-knapp", () => {
  const tab = tiltakstabell(TILTAK, () => {});
  const tekster = [...tab.querySelectorAll("button")]
    .map((b) => b.textContent);
  assert.deepEqual(tekster, [t("ui.likviditet.vurder")]);
});

test("kilden har ingen iverksettelsesvei", () => {
  // KOMMENTARER **OG STRENGER** FJERNES FØRST.
  //
  // Første utgave strippet bare kommentarer, og falt på
  // `t("ui.likviditet.ingen_iverksettelse")` — locale-nøkkelen som
  // SIER at modulen ikke iverksetter. Porten traff altså teksten som
  // forklarer det avvergede mønsteret, ikke mønsteret.
  //
  // Det er samme felle klynge 6 gikk i tre ganger og python-siden
  // løser med `_bare_kode(..., uten_strenger=True)`. PORTEN SKAL MÅLE
  // HANDLINGEN, IKKE BEGRUNNELSEN.
  const kilde = readFileSync(join(ROT, "platform", "core", "ui",
    "static", "js", "flater", "likviditet.js"), "utf8");
  const uten = kilde.split("\n")
    .filter((l) => !l.trim().startsWith("//")).join("\n")
    .replace(/"[^"\n]*"|'[^'\n]*'|`[^`]*`/g, '""');
  for (const forbudt of ["iverksett", "si_opp", "utfor_tiltak"]) {
    assert.ok(!uten.toLowerCase().includes(forbudt), forbudt);
  }
});


// =====================================================================
// TALL, TABELLER OG TILGANG.
// =====================================================================

test("kroner formaterer øre uten flyttall", () => {
  assert.equal(kroner(120000000), "1 200 000,00");
  assert.equal(kroner(-4050000), "−40 500,00");
  assert.equal(kroner(1), "0,01");
  assert.equal(kroner(null), "");
});

test("posttabellen viser hvem som satte tallet", () => {
  const tab = posttabell(POSTER);
  // HELE GRUNNEN TIL AT TABELLEN FINNES: huset kan ikke prise lønn.
  assert.ok(tab.textContent.includes("u-kari"));
  assert.ok(tab.textContent.includes(t("ui.likviditet.type_lonn")));
});

test("modelltabellen viser metoden, ikke bare navnet", () => {
  const tab = modelltabell(MODELLER);
  // EN MODELL INGEN KAN LESE ER EN MODELL INGEN KAN SI ER FEIL.
  assert.ok(tab.textContent.includes("Startsaldo fra bankposter"));
  assert.ok(tab.textContent.includes("samme som forrige uke"));
});

test("funntabellen leser kan_lukkes fra basen", () => {
  const kalt = [];
  const tab = funntabell(FUNN, (f) => kalt.push(f.funn_id));
  const knapper = [...tab.querySelectorAll("button")];
  assert.equal(knapper.length, 1, "flaten avgjør selv hva som lukkes");
  knapper[0].click();
  assert.deepEqual(kalt, [F2]);
  assert.ok(tab.textContent
    .includes(t("ui.likviditet.funn_kan_ikke_lukkes")));
});

test("alle posttyper og reversibiliteter har en tekst", () => {
  for (const p of POSTTYPER) {
    const n = `ui.likviditet.type_${p}`;
    assert.notEqual(t(n), n, `${n} mangler i locale`);
  }
  const rev = { reversibel: "rev_reversibel",
                delvis_reversibel: "rev_delvis",
                irreversibel: "rev_irreversibel" };
  for (const r of REVERSIBILITET) {
    const n = `ui.likviditet.${rev[r]}`;
    assert.notEqual(t(n), n, `${n} mangler i locale`);
  }
});

test("alle funntyper har en tekst", () => {
  const typer = ["ingen_krav", "ingen_gyldig_modell",
                 "modell_utloper_snart", "prognose_uten_maaling",
                 "prognose_mot_utdatert_grunnlag", "bane_under_null"];
  const tab = funntabell(typer.map((ft, i) => ({
    funn_id: `f${i}`, funntype: ft, prognose_id: P1, modell_id: null,
    over_grense: 1, detalj: null, kravversjon: 1,
    forst_sett: "2026-09-05T09:00:00+00:00",
    sist_sett_sveip: "2026-09-05T09:00:00+00:00", apen: true,
    lukket_ts: null, lukket_av: null, lukkenotat: null,
    kan_lukkes: true })), null);
  // INGEN RÅ NØKKEL PÅ SKJERMEN.
  assert.ok(!/ui\.likviditet\./.test(tab.textContent));
});

test("ilokalDato bruker lokal tid, ikke UTC", () => {
  const d = new Date(2026, 0, 1, 0, 30, 0);
  assert.equal(ilokalDato(d), "2026-01-01");
});


// =====================================================================
// FLATEN SOM HELHET.
// =====================================================================

test("funnene står før prognosene", async () => {
  const h = nyHoved();
  await visLikviditet(h, ctx());
  await vent(() => h.querySelectorAll("h2").length > 2);
  const titler = [...h.querySelectorAll("h2")].map((x) => x.textContent);
  const iFunn = titler.indexOf(t("ui.likviditet.funn"));
  const iPrognoser = titler.indexOf(t("ui.likviditet.prognoser"));
  assert.ok(iFunn >= 0 && iPrognoser >= 0);
  assert.ok(iFunn < iPrognoser,
            "prognoselista står før funnene — det haster ikke mest");
});

test("uten skrivescope finnes ingen skjemaer", async () => {
  const h = nyHoved();
  await visLikviditet(h, ctx(["okonomi:read"]));
  await vent(() => h.textContent.includes(t("ui.likviditet.funn")));
  assert.equal(h.querySelector("#likv-horisont"), null);
  assert.equal(h.querySelector("#likv-belop"), null);
});

test("kravskjemaet forhåndsutfylles med alle fire grensene",
     async () => {
  const h = nyHoved();
  await visLikviditet(h, ctx());
  await vent(() => h.querySelector("#likv-horisont"));
  assert.equal(h.querySelector("#likv-horisont").value, "13");
  assert.equal(h.querySelector("#likv-grunnlag").value, "7");
  assert.equal(h.querySelector("#likv-maalefrist").value, "14");
  assert.equal(h.querySelector("#likv-modellvarsel").value, "30");
});

test("banepanelet åpner og tilbyr måling der uken er over", async () => {
  const h = nyHoved();
  await visLikviditet(h, ctx());
  await vent(() => [...h.querySelectorAll("button")]
    .some((b) => b.textContent === t("ui.likviditet.vis_bane")));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.likviditet.vis_bane")).click();
  await vent(() => [...h.querySelectorAll("button")]
    .some((b) => b.textContent === t("ui.likviditet.maal_uken")));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.likviditet.maal_uken")).click();
  await vent(() => h.querySelector("#likv-faktisk"));
  h.querySelector("#likv-faktisk").value = "79000000";
  const skjema = h.querySelector("#likv-faktisk").closest("form");
  skjema.dispatchEvent(new Event("submit",
    { bubbles: true, cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("/maaling"));
  assert.equal(SISTE.kropp.uke_nr, 2);
  assert.equal(SISTE.kropp.faktisk_ore, 79000000);
  // BASELINJEN ER VALGFRI, og `null` når den ikke er fylt ut.
  assert.equal(SISTE.kropp.baselinje_ore, null);
});

test("prognoseskjemaet nekter når ingen modell gjelder", async () => {
  const h = nyHoved();
  SVAR["/v1/likviditet"] = {
    ...BILDE,
    modeller: [{ ...MODELLER[0], gyldig_naa: false,
                 gyldig_til: "2026-06-30" }],
  };
  await visLikviditet(h, ctx());
  await vent(() => h.textContent
    .includes(t("ui.likviditet.ingen_gyldig_modell")));
  assert.equal(h.querySelector("#likv-modellvalg"), null,
               "skjemaet tilbyr en prognose døra ville nektet");
});

test("flaten er ren for axe", async () => {
  const h = nyHoved();
  await visLikviditet(h, ctx());
  await vent(() => h.querySelector("#likv-horisont"));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("den tomme flaten er ren for axe", async () => {
  const h = nyHoved();
  SVAR["/v1/likviditet"] = TOMT;
  await visLikviditet(h, ctx());
  await vent(() => h.textContent
    .includes(t("ui.likviditet.prognoser_tomt")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});
