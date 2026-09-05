// M-33 prognoseflaten (130) — flateporten (jsdom + axe).
//
// PORTENE MÅLER KLYNGENS DOM, IKKE BARE AT SKJERMEN TEGNES.
//
//   En gal prognose ser nøyaktig ut som en riktig prognose — helt til
//   horisonten er passert, og da har alle sluttet å se.
//
// …og M-33s egen, som er den vanskeligste å måle i en flate:
//
//   EN MODELL SOM IKKE KAN TAPE, HAR IKKE VUNNET.
//
// Derfor måler portene her:
//
//   * at BÅNDET tegnes i samme rad som punktet. En bane vist som én
//     linje ville sett ut som en presis prognose.
//   * at BASISLINJEN står i samme rad som punktet og det faktiske.
//     Ligger den i en fotnote, krever «slår modellen den?»
//     hoderegning — og da blir spørsmålet ikke stilt.
//   * at `ukjent` datakvalitet vises som et VARSEL og ikke som `ren`.
//     «Ingen funn» og «ingen har sett etter» er ikke samme tilstand.
//   * at «mål denne uken» bare finnes når `kan_maales` fra BASEN sier
//     det — flaten regner ikke selv.
//   * at det ikke finnes en knapp som tar en personalavgjørelse.
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
  banetabell, funntabell, minutter, modelltabell, prognosetabell,
  sammendrag, visPrognose,
} from "../static/js/flater/prognose.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));
const ROT = join(HER, "..", "..", "..", "..");

const P1 = "aaaaaaaa-1111-1111-1111-111111111111";
const P2 = "aaaaaaaa-2222-2222-2222-222222222222";
const M1 = "bbbbbbbb-1111-1111-1111-111111111111";
const F1 = "dddddddd-1111-1111-1111-111111111111";
const F2 = "dddddddd-2222-2222-2222-222222222222";

// TO PROGNOSER: én med kjent (ren) datakvalitet, én laget i blinde.
const PROGNOSER = [
  { prognose_id: P1, laget_dato: "2026-09-01", horisont_uker: 4,
    modellversjon: "2026-01", baselinje: "samme som forrige uke",
    grunnlag_uker: 8, grunnlag_siste_dato: "2026-08-30",
    grunnlag_antall_uker: 8, datakvalitet: "ren",
    datakvalitet_antall: 0, gjelder_til: "2026-09-29",
    laget_av: "u-kari", antall_maalt: 1 },
  { prognose_id: P2, laget_dato: "2026-05-01", horisont_uker: 4,
    modellversjon: "2026-01", baselinje: "samme som forrige uke",
    grunnlag_uker: 8, grunnlag_siste_dato: "2026-04-30",
    grunnlag_antall_uker: 6, datakvalitet: "ukjent",
    datakvalitet_antall: 0, gjelder_til: "2026-05-29",
    laget_av: "u-kari", antall_maalt: 0 },
];

const BANE = {
  request_id: "r-b", prognose_id: P1,
  bane: [
    // UKE 1: over, målt — og modellen BOMMET mens basislinjen traff
    // bedre. Det er nettopp raden som skal være lesbar uten regning.
    { uke_nr: 1, ukeslutt: "2026-09-07", forventet_minutter: 450,
      nedre_minutter: 220, ovre_minutter: 680,
      baseline_minutter: 800, faktisk_minutter: 900,
      avvik_minutter: 450, baseline_avvik_minutter: 100,
      innenfor_intervall: false, kan_maales: false },
    // UKE 2: over, IKKE målt → knappen skal finnes.
    { uke_nr: 2, ukeslutt: "2026-09-14", forventet_minutter: 450,
      nedre_minutter: 220, ovre_minutter: 680,
      baseline_minutter: 800, faktisk_minutter: null,
      avvik_minutter: null, baseline_avvik_minutter: null,
      innenfor_intervall: null, kan_maales: true },
    // UKE 3: ikke over ennå → ingen knapp.
    { uke_nr: 3, ukeslutt: "2026-09-21", forventet_minutter: 450,
      nedre_minutter: 220, ovre_minutter: 680,
      baseline_minutter: 800, faktisk_minutter: null,
      avvik_minutter: null, baseline_avvik_minutter: null,
      innenfor_intervall: null, kan_maales: false },
  ],
};

const MODELLER = [
  { modell_id: M1, navn: "Glidende snitt", versjon: "2026-01",
    metode: "Glidende snitt over de siste hele ukene med foert tid.",
    baselinje: "samme som forrige uke", gyldig_fra: "2026-01-01",
    gyldig_til: null, gjelder: true, antall_prognoser: 2 },
];

const FUNN = [
  // SVEIPENS EGET — ingen kan lukke det.
  { funn_id: F1, funntype: "slaar_ikke_naiv_baseline",
    referanse: "2026-01",
    detaljer: "samlet avvik 1400 minutter over 4 malte uker",
    over_grense: 1400, apen: true,
    forst_sett: "2026-08-15T09:00:00+00:00",
    sist_sett: "2026-09-05T09:00:00+00:00", lukket_av: null,
    kan_lukkes: false },
  // UKJENT DATAKVALITET — det KAN lukkes av et menneske.
  { funn_id: F2, funntype: "prognose_paa_ukjent_datakvalitet",
    referanse: P2, detaljer: "M-3 har aldri profilert tenanten",
    over_grense: 0, apen: true,
    forst_sett: "2026-09-02T09:00:00+00:00",
    sist_sett: "2026-09-05T09:00:00+00:00", lukket_av: null,
    kan_lukkes: true },
];

const BILDE = {
  request_id: "r-x",
  sammendrag: {
    prognoser: 2, modeller: 1, gyldige_modeller: 1, uker_totalt: 8,
    uker_maalt: 1, uker_umaalt: 3, treff: 0, bom: 1, apne_funn: 2,
    har_krav: true, horisont_uker: 4, grunnlag_uker: 8,
    maalefrist_dogn: 14, domsgrunnlag_uker: 4, kravversjon: 1,
    prognoser_ukjent_kvalitet: 1,
  },
  prognoser: PROGNOSER, modeller: MODELLER, funn: FUNN,
};

const TOMT = {
  request_id: "r-t",
  sammendrag: {
    prognoser: 0, modeller: 0, gyldige_modeller: 0, uker_totalt: 0,
    uker_maalt: 0, uker_umaalt: 0, treff: 0, bom: 0, apne_funn: 0,
    har_krav: false, horisont_uker: null, grunnlag_uker: null,
    maalefrist_dogn: null, domsgrunnlag_uker: null, kravversjon: null,
    prognoser_ukjent_kvalitet: 0,
  },
  prognoser: [], modeller: [], funn: [],
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
        json: async () => ({ feil: "prognose_ulovlig_tilstand" }) };
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
  SVAR = { "/v1/prognose": BILDE,
           [`/v1/prognose/prognose/${P1}/bane`]: BANE };
  SISTE = null;
  KALL = [];
  SVARSTATUS = 200;
  return m;
}


// =====================================================================
// KLYNGENS DOM: MÅLINGEN ER PRODUKTET.
// =====================================================================

test("sammendraget setter umålte uker først", () => {
  const p = sammendrag(BILDE.sammendrag);
  const sterke = [...p.querySelectorAll("strong")];
  assert.ok(sterke.length >= 2);
  // UMÅLTE FØRST. Et sammendrag som begynte med «2 prognoser» ville
  // fortalt hvor flittige vi har vært, ikke om vi hadde rett.
  assert.ok(sterke[0].textContent.includes("3"));
  assert.ok(sterke[0].textContent
    .includes(t("ui.prognose.umaalte_sum").split("{")[0].trim()));
});

test("ingen målinger ser ikke ut som at alt stemmer", () => {
  const s = { ...BILDE.sammendrag, treff: 0, bom: 0, uker_umaalt: 0 };
  const p = sammendrag(s);
  assert.ok(p.textContent.includes(t("ui.prognose.ingen_maalinger")));
  // …og det står som et VARSEL, ikke som en fotnote.
  assert.ok([...p.querySelectorAll("strong")]
    .some((x) => x.getAttribute("role") === "alert"));
});

test("banetabellen viser båndet i samme rad som punktet", () => {
  const tab = banetabell(BANE.bane, null);
  const rader = [...tab.querySelectorAll("tbody tr")];
  assert.equal(rader.length, 3);
  const celler = [...rader[0].querySelectorAll("td")]
    .map((c) => c.textContent);
  // punkt, nedre OG øvre — alle tre synlige, ingen bak et klikk.
  assert.ok(celler.some((c) => c.includes("450")));
  assert.ok(celler.some((c) => c.includes("220") && c.includes("680")));
});

test("basislinjen står i samme rad som punktet og det faktiske", () => {
  // DETTE ER PORTEN FOR «EN MODELL SOM IKKE KAN TAPE, HAR IKKE
  // VUNNET». Uke 1 bommet med 450 mens basislinjen bommet med 100 —
  // og begge tallene skal stå der, uten hoderegning.
  const tab = banetabell(BANE.bane, null);
  const celler = [...tab.querySelectorAll("tbody tr")][0]
    .querySelectorAll("td");
  const tekst = [...celler].map((c) => c.textContent);
  assert.ok(tekst.some((c) => c.includes("800")), "basislinjen mangler");
  assert.ok(tekst.includes("450"), "modellavviket mangler");
  assert.ok(tekst.includes("100"), "basislinjeavviket mangler");
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

test("en umålt uke viser strek, ikke null", () => {
  // «0 minutter» og «ikke målt» er ikke samme tilstand. En tabell som
  // skrev 0 ville påstått at ingen jobbet.
  const tab = banetabell([BANE.bane[1]], null);
  const tekst = [...tab.querySelectorAll("tbody td")]
    .map((c) => c.textContent);
  assert.ok(tekst.includes("–"), "en umålt uke ble tegnet som et tall");
  assert.ok(!tekst.includes("0"));
});


// =====================================================================
// «REN» OG «UKJENT» ER IKKE SAMME TILSTAND.
// =====================================================================

test("ukjent datakvalitet står som varsel, ren gjør det ikke", () => {
  const tab = prognosetabell(PROGNOSER, () => {});
  const varsler = [...tab.querySelectorAll("strong[role='alert']")];
  assert.equal(varsler.length, 1,
               "ren og ukjent ble tegnet likt");
  assert.equal(varsler[0].textContent,
               t("ui.prognose.kvalitet_ukjent"));
  // …og den RENE står som vanlig tekst.
  assert.ok(tab.textContent.includes(t("ui.prognose.kvalitet_ren")));
});

test("sammendraget teller prognoser laget i blinde", () => {
  const p = sammendrag(BILDE.sammendrag);
  assert.ok(p.textContent
    .includes(t("ui.prognose.ukjent_kvalitet_sum")
      .replace("{n}", "1")));
});

test("en ukjent kvalitetsverdi vises som seg selv", () => {
  // Faller en ny verdi inn i det lukkede settet uten at flaten er
  // oppdatert, skal den være SYNLIG. En tom celle ville skjult
  // nettopp det som må ses.
  const tab = prognosetabell(
    [{ ...PROGNOSER[0], datakvalitet: "noe_nytt" }], () => {});
  assert.ok(tab.textContent.includes("noe_nytt"));
});


// =====================================================================
// V1-DOMMEN: MODULEN TAR INGEN PERSONALAVGJØRELSE.
// =====================================================================

test("funntabellen leser kan_lukkes fra basen", () => {
  const kalt = [];
  const tab = funntabell(FUNN, (f) => kalt.push(f.funntype));
  const knapper = [...tab.querySelectorAll("button")];
  // BARE ETT FUNN KAN LUKKES. `slaar_ikke_naiv_baseline` lukkes av at
  // modellen faktisk blir bedre — ikke av et klikk.
  assert.equal(knapper.length, 1);
  knapper[0].click();
  assert.deepEqual(kalt, ["prognose_paa_ukjent_datakvalitet"]);
  assert.ok(tab.textContent
    .includes(t("ui.prognose.lukkes_av_sveipen")));
});

test("kilden har ingen vei til en personalavgjørelse", () => {
  // KOMMENTARER **OG STRENGER** FJERNES FØRST.
  //
  // 128s lærdom: en tidligere port traff locale-nøkkelen som SIER at
  // modulen ikke utfører noe — altså teksten som forklarer det
  // avvergede mønsteret, ikke mønsteret. PORTEN SKAL MÅLE
  // HANDLINGEN, IKKE BEGRUNNELSEN.
  const kilde = readFileSync(join(ROT, "platform", "core", "ui",
    "static", "js", "flater", "prognose.js"), "utf8");
  const uten = kilde.split("\n")
    .filter((l) => !l.trim().startsWith("//")).join("\n")
    .replace(/"[^"\n]*"|'[^'\n]*'|`[^`]*`/g, '""');
  for (const forbudt of ["ansett", "si_opp", "iverksett",
                         "flyttvakt", "permitter"]) {
    assert.ok(!uten.toLowerCase().includes(forbudt), forbudt);
  }
});

test("modelltabellen viser metoden og basislinjen, ikke bare navnet", () => {
  const tab = modelltabell(MODELLER);
  // EN MODELL INGEN KAN LESE ER EN MODELL INGEN KAN SI ER FEIL.
  assert.ok(tab.textContent.includes("Glidende snitt over de siste"));
  // …og uten en NAVNGITT basislinje er «slår den den?» et spørsmål
  // uten referanse.
  assert.ok(tab.textContent.includes("samme som forrige uke"));
});


// =====================================================================
// TALL, SKJEMAER OG TILGANG.
// =====================================================================

test("minutter vises som minutter, med timene i parentes", () => {
  assert.equal(minutter(450), "450 (7 t 30 min)");
  assert.equal(minutter(0), "0 (0 t 0 min)");
  assert.equal(minutter(null), "–");
});

test("en leser uten skrivescope får ingen skjemaer", async () => {
  const h = nyHoved();
  await visPrognose(h, ctx(["okonomi:read"]));
  await vent(() => h.textContent.includes(t("ui.prognose.prognoser")));
  assert.equal(h.querySelector("#prog-horisont"), null);
  assert.equal(h.querySelector("#prog-modellvalg"), null);
});

test("kravskjemaet forhåndsutfylles med ALLE fire grensene", async () => {
  // 123s lærdom: et skjema som viser mindre enn det lagrer er en
  // felle, fordi et innsendt skjema setter alle fire — også dem
  // brukeren ikke så.
  const h = nyHoved();
  await visPrognose(h, ctx());
  await vent(() => h.querySelector("#prog-horisont"));
  assert.equal(h.querySelector("#prog-horisont").value, "4");
  assert.equal(h.querySelector("#prog-grunnlag").value, "8");
  assert.equal(h.querySelector("#prog-frist").value, "14");
  assert.equal(h.querySelector("#prog-dom").value, "4");
});

test("et tomt måletall sendes ikke som null minutter", async () => {
  // `Number("")` er `0`, og en måling RETTES IKKE. Et tomt felt
  // sendt inn ville registrert null minutter som ukens faktiske
  // arbeid — permanent.
  const h = nyHoved();
  await visPrognose(h, ctx());
  await vent(() => h.textContent.includes("2026-09-01"));
  const visBane = [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.prognose.vis_bane"));
  visBane.click();
  await vent(() => h.querySelector("button")
    && [...h.querySelectorAll("button")]
      .some((b) => b.textContent === t("ui.prognose.maal_uken")));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.prognose.maal_uken")).click();
  await vent(() => h.querySelector("#prog-faktisk"));
  const felt = h.querySelector("#prog-faktisk");
  // `required` er et BOOLSK attributt: `el()` setter det, og DOM-en
  // viser det som `required=""`. Porten spør om det FINNES, ikke om
  // hva det inneholder.
  assert.ok(felt.hasAttribute("required"));
  felt.value = "";
  felt.form.dispatchEvent(new window.Event("submit",
                                           { cancelable: true }));
  await vent(() => h.textContent
    .includes(t("ui.prognose.feil.generell")));
  assert.equal(SISTE, null, "et tomt felt ble sendt som en måling");
});

test("prognoseskjemaet nekter når ingen modell gjelder", async () => {
  const h = nyHoved();
  SVAR["/v1/prognose"] = {
    ...BILDE,
    modeller: [{ ...MODELLER[0], gjelder: false,
                 gyldig_til: "2026-06-30" }],
  };
  await visPrognose(h, ctx());
  await vent(() => h.textContent
    .includes(t("ui.prognose.ingen_gyldig_modell")));
  assert.equal(h.querySelector("#prog-modellvalg"), null,
               "skjemaet tilbyr en prognose døra ville nektet");
});

test("flaten er ren for axe", async () => {
  const h = nyHoved();
  await visPrognose(h, ctx());
  await vent(() => h.querySelector("#prog-horisont"));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("den tomme flaten er ren for axe", async () => {
  const h = nyHoved();
  SVAR["/v1/prognose"] = TOMT;
  await visPrognose(h, ctx());
  await vent(() => h.textContent
    .includes(t("ui.prognose.prognoser_tomt")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});
