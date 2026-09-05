// M-53 HMS-flaten (127) — flateporten (jsdom + axe).
//
// PORTENE MÅLER MODULENS DOM, IKKE BARE AT SKJERMEN TEGNES.
//
// Dette er den eneste modulen i katalogen som mottar data OM en ansatt
// FRA en ansatt, og flaten er stedet varslervernet enten holder eller
// ryker. Derfor måler portene her:
//
//   * at NAVNEFELTET FJERNES fra DOM-en når melderen er anonym — ikke
//     skjules, ikke deaktiveres. Et felt som kan fylles blir fylt.
//   * at navnet ALDRI er med i kroppen som sendes for et anonymt
//     avvik. Ikke `null`, ikke tom streng — FRAVÆRENDE.
//   * at advarselen om fritekst står FØR feltet. Den skal leses av den
//     som skriver, ikke av den som har skrevet ferdig.
//   * at «meldt anonymt» og «navnet er slettet» ALDRI ser like ut.
//   * at det som er GALT står FØRST i sammendraget.
//   * og at flaten ikke avgjør hva som kan lukkes.
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
  AVVIKSTYPER, MELDERFORMER, avvikstabell, funntabell, ilokalDato,
  meldertilstand, oppbevaringstekst, regeltabell, sammendrag, visHms,
} from "../static/js/flater/hms.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));
const ROT = join(HER, "..", "..", "..", "..");

const A1 = "aaaaaaaa-1111-1111-1111-111111111111";
const A2 = "aaaaaaaa-2222-2222-2222-222222222222";
const A3 = "aaaaaaaa-3333-3333-3333-333333333333";
const R1 = "bbbbbbbb-1111-1111-1111-111111111111";
const F1 = "cccccccc-1111-1111-1111-111111111111";
const F2 = "cccccccc-2222-2222-2222-222222222222";

// TRE AVVIK SOM DEKKER DE TRE MELDERTILSTANDENE:
//   A1 anonymt      → navnet ble ALDRI skrevet
//   A2 navngitt     → navnet står
//   A3 anonymisert  → navnet ER slettet
const AVVIK = [
  { avvik_id: A1, avvikstype: "personskade", melderform: "anonym",
    beskrivelse: "Fall fra stillas i tredje etasje",
    sted: "Byggeplass A", hendelsesdato: "2026-08-01",
    meldt_dato: "2026-08-02", status: "apen", behandlet_av: null,
    regelversjon: "2026-01",
    oppbevaring_hjemmel: "arbeidsmiljoloven 5-1",
    oppbevaring_til: "2031-07-31", dogn_til_oppbevaring: 1790,
    helseopplysninger: true, melder_navn: null, anonymisert: false,
    m30_sak_ref: null, antall_tiltak: 0 },
  { avvik_id: A2, avvikstype: "materiell", melderform: "navngitt",
    beskrivelse: "Truck kjorte inn i porten paa lageret",
    sted: "Lager B", hendelsesdato: "2026-07-10",
    meldt_dato: "2026-07-10", status: "behandlet",
    behandlet_av: "u-kari", regelversjon: "2026-01",
    oppbevaring_hjemmel: "internkontrollforskriften 5",
    oppbevaring_til: "2026-08-01", dogn_til_oppbevaring: -35,
    helseopplysninger: false, melder_navn: "Kari Nordmann",
    anonymisert: false, m30_sak_ref: null, antall_tiltak: 2 },
  { avvik_id: A3, avvikstype: "sykdom", melderform: "navngitt",
    beskrivelse: "Langvarig eksponering for stov i verkstedet",
    sted: "Verksted C", hendelsesdato: "2026-01-05",
    meldt_dato: "2026-01-06", status: "behandlet",
    behandlet_av: "u-per", regelversjon: "2025-04",
    oppbevaring_hjemmel: "arbeidsmiljoloven 5-1",
    oppbevaring_til: "2031-01-05", dogn_til_oppbevaring: 1580,
    helseopplysninger: true, melder_navn: null, anonymisert: true,
    m30_sak_ref: "SAK-2026-119", antall_tiltak: 1 },
];

const REGLER = [
  { regel_id: R1, avvikstype: "personskade", versjon: "2026-01",
    hjemmel: "arbeidsmiljoloven 5-1", oppbevaring_dogn: 1825,
    helseopplysninger: true, gyldig_fra: "2026-01-01",
    gyldig_til: null, gyldig_naa: true, dogn_til_utlop: null,
    antall_avvik: 1 },
];

const FUNN = [
  // SVEIPENS EGET — ingen kan lukke det for hånd.
  { funn_id: F1, funntype: "oppbevaring_utlopt", regel_id: null,
    avvik_id: A2, over_grense: 35,
    detalj: "internkontrollforskriften 5", kravversjon: 1,
    forst_sett: "2026-08-02T09:00:00+00:00",
    sist_sett_sveip: "2026-09-05T09:00:00+00:00", apen: true,
    lukket_ts: null, lukket_av: null, lukkenotat: null,
    kan_lukkes: false },
  // ET VARSEL — det kan lukkes.
  { funn_id: F2, funntype: "avvik_ubehandlet", regel_id: null,
    avvik_id: A1, over_grense: 20, detalj: "personskade meldt",
    kravversjon: 1, forst_sett: "2026-08-22T09:00:00+00:00",
    sist_sett_sveip: "2026-09-05T09:00:00+00:00", apen: true,
    lukket_ts: null, lukket_av: null, lukkenotat: null,
    kan_lukkes: true },
];

const BILDE = {
  request_id: "r-b",
  sammendrag: {
    avvik: 3, apne: 1, ubehandlet_over_frist: 1, anonyme: 1,
    med_helseopplysninger: 2, levende: 2, oppbevaring_passert: 1,
    oppbevaring_naer: 0, regler: 1, gyldige_regler: 1, apne_funn: 2,
    har_krav: true, oppbevaring_maks_dogn: 3650,
    oppbevaringsvarsel_dogn: 60, tiltaksfrist_dogn: 14,
    regelvarsel_dogn: 60, kravversjon: 1,
  },
  avvik: AVVIK, regelverk: REGLER, funn: FUNN,
};

const TOMT = {
  request_id: "r-t",
  sammendrag: {
    avvik: 0, apne: 0, ubehandlet_over_frist: 0, anonyme: 0,
    med_helseopplysninger: 0, levende: 0, oppbevaring_passert: 0,
    oppbevaring_naer: 0, regler: 0, gyldige_regler: 0, apne_funn: 0,
    har_krav: false, oppbevaring_maks_dogn: null,
    oppbevaringsvarsel_dogn: null, tiltaksfrist_dogn: null,
    regelvarsel_dogn: null, kravversjon: null,
  },
  avvik: [], regelverk: [], funn: [],
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
        json: async () => ({ feil: "hms_ulovlig_tilstand" }) };
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
  SVAR = { "/v1/hms": BILDE };
  SISTE = null;
  KALL = [];
  SVARSTATUS = 200;
  return m;
}


// =====================================================================
// VARSLERVERNET — DEN SKARPESTE GRUPPEN.
// =====================================================================

test("meldertilstand skiller ALDRI skrevet fra slettet", () => {
  // TRE TILSTANDER, TRE ULIKE SVAR. En flate som slo de to første
  // sammen ville fortalt en varsler at systemet «har slettet» noe det
  // aldri hadde.
  assert.equal(meldertilstand(AVVIK[0]), t("ui.hms.melder_anonym"));
  assert.equal(meldertilstand(AVVIK[1]), "Kari Nordmann");
  assert.equal(meldertilstand(AVVIK[2]),
               t("ui.hms.melder_anonymisert"));
  assert.notEqual(t("ui.hms.melder_anonym"),
                  t("ui.hms.melder_anonymisert"));
});

test("navnefeltet FJERNES fra DOM-en når melderen er anonym", async () => {
  const h = nyHoved();
  await visHms(h, ctx());
  await vent(() => h.querySelector("#hms-melder-navn"));
  const navn = h.querySelector("#hms-melder-navn");
  assert.ok(navn, "navnefeltet mangler for en navngitt melder");

  const anonym = h.querySelector("#hms-melderform-anonym");
  anonym.checked = true;
  anonym.dispatchEvent(new Event("change",
                                             { bubbles: true }));
  await vent(() => !h.querySelector("#hms-melder-navn"));

  // IKKE SKJULT, IKKE DEAKTIVERT — BORTE.
  assert.equal(h.querySelector("#hms-melder-navn"), null,
               "navnefeltet finnes fortsatt i DOM-en");
  assert.equal(h.querySelector("#hms-melder-rolle"), null);
});

test("kroppen for et anonymt avvik bærer INGEN melder_navn-nøkkel",
     async () => {
  const h = nyHoved();
  await visHms(h, ctx());
  await vent(() => h.querySelector("#hms-melderform-anonym"));

  const anonym = h.querySelector("#hms-melderform-anonym");
  anonym.checked = true;
  anonym.dispatchEvent(new Event("change",
                                             { bubbles: true }));
  await vent(() => !h.querySelector("#hms-melder-navn"));

  h.querySelector("#hms-beskrivelse").value =
    "Fall fra stillas i tredje etasje";
  h.querySelector("#hms-sted").value = "Byggeplass A";
  const skjema = h.querySelector("#hms-beskrivelse")
    .closest("form");
  skjema.dispatchEvent(new Event("submit",
    { bubbles: true, cancelable: true }));
  await vent(() => SISTE && SISTE.sti === "/v1/hms/avvik");

  // NØKKELEN SKAL IKKE FINNES. `null` ville vært en påstand om at vi
  // hadde et navn og valgte å ikke sende det.
  assert.ok(!("melder_navn" in SISTE.kropp),
            "melder_navn er med i kroppen for et anonymt avvik");
  assert.ok(!("melder_rolle" in SISTE.kropp));
  assert.equal(SISTE.kropp.melderform, "anonym");
});

test("et navn skrevet før byttet til anonym følger ikke med",
     async () => {
  // AUTOFYLL, ANGRE, OMBESTEMME SEG. Bytter man til anonym etter å ha
  // skrevet navnet sitt, skal navnet være borte — også hvis man
  // bytter tilbake igjen.
  const h = nyHoved();
  await visHms(h, ctx());
  await vent(() => h.querySelector("#hms-melder-navn"));
  h.querySelector("#hms-melder-navn").value = "Kari Nordmann";

  const anonym = h.querySelector("#hms-melderform-anonym");
  anonym.checked = true;
  anonym.dispatchEvent(new Event("change",
                                             { bubbles: true }));
  await vent(() => !h.querySelector("#hms-melder-navn"));

  const navngitt = h
    .querySelector("#hms-melderform-navngitt");
  navngitt.checked = true;
  navngitt.dispatchEvent(new Event("change",
                                               { bubbles: true }));
  await vent(() => h.querySelector("#hms-melder-navn"));
  assert.equal(h.querySelector("#hms-melder-navn").value, "",
               "navnet overlevde en runde innom anonym");
});

test("advarselen om fritekst står FØR beskrivelsesfeltet", async () => {
  const h = nyHoved();
  await visHms(h, ctx());
  await vent(() => h.querySelector("#hms-beskrivelse"));
  const felt = h.querySelector("#hms-beskrivelse");
  const advarsel = [...h.querySelectorAll("p")]
    .find((p) => p.textContent === t("ui.hms.fritekst_advarsel"));
  assert.ok(advarsel, "advarselen om fritekst finnes ikke");
  // DOCUMENT_POSITION_FOLLOWING = 4: advarselen kommer FØR feltet.
  assert.ok(advarsel.compareDocumentPosition(felt) & 4,
            "advarselen står etter feltet den advarer om");
});


// =====================================================================
// SAMMENDRAGET OG TABELLENE.
// =====================================================================

test("sammendraget setter det som er GALT først", () => {
  const p = sammendrag(BILDE.sammendrag);
  const sterke = [...p.querySelectorAll("strong")];
  assert.ok(sterke.length >= 2);
  // Ubehandlet FØRST: det er hele grunnen til at modulen finnes.
  assert.ok(sterke[0].textContent.includes("1"));
  assert.ok(p.textContent.includes(String(
    BILDE.sammendrag.med_helseopplysninger)));
});

test("et tomt register roper om at grensene mangler", () => {
  const p = sammendrag(TOMT.sammendrag);
  assert.ok(p.textContent.includes(t("ui.hms.krav_mangler")));
  assert.ok([...p.querySelectorAll("strong")]
    .some((s) => s.textContent === t("ui.hms.krav_mangler")));
});

test("oppbevaringstekst teller begge veier", () => {
  assert.ok(oppbevaringstekst({ dogn_til_oppbevaring: -35 })
    .includes("35"));
  assert.ok(oppbevaringstekst({ dogn_til_oppbevaring: 10 })
    .includes("10"));
  assert.equal(oppbevaringstekst({}), "");
});

test("avvikstabellen bruker meldertilstand, ikke melder_navn", () => {
  const tab = avvikstabell(AVVIK, {});
  const tekst = tab.textContent;
  assert.ok(tekst.includes(t("ui.hms.melder_anonym")));
  assert.ok(tekst.includes(t("ui.hms.melder_anonymisert")));
  assert.ok(tekst.includes("Kari Nordmann"));
});

test("anonymiser-knappen forsvinner på en anonymisert rad", () => {
  const kalt = [];
  const tab = avvikstabell(AVVIK, {
    aapneAnonymiser: (a) => kalt.push(a.avvik_id) });
  const knapper = [...tab.querySelectorAll("button")]
    .filter((b) => b.textContent === t("ui.hms.anonymiser"));
  // TO av tre: A3 er alt anonymisert. Deaktivert ville sett ut som
  // «du mangler tilgang»; borte sier det som er sant.
  assert.equal(knapper.length, 2);
});

test("en passert oppbevaringsfrist står i fet skrift", () => {
  const tab = avvikstabell(AVVIK, {});
  const sterke = [...tab.querySelectorAll("strong")]
    .map((s) => s.textContent);
  assert.ok(sterke.some((s) => s.includes("35")),
            "den passerte fristen er ikke uthevet");
});

test("funntabellen leser kan_lukkes fra basen", () => {
  const kalt = [];
  const tab = funntabell(FUNN, (f) => kalt.push(f.funn_id));
  const knapper = [...tab.querySelectorAll("button")];
  assert.equal(knapper.length, 1, "flaten avgjør selv hva som lukkes");
  knapper[0].click();
  assert.deepEqual(kalt, [F2]);
  assert.ok(tab.textContent.includes(t("ui.hms.funn_kan_ikke_lukkes")));
});

test("en avviklet regel kan ikke avvikles igjen", () => {
  const avviklet = [{ ...REGLER[0], gyldig_naa: false,
                      gyldig_til: "2026-06-30" }];
  const tab = regeltabell(avviklet, () => {});
  assert.equal(tab.querySelectorAll("button").length, 0);
  const gjeldende = regeltabell(REGLER, () => {});
  assert.equal(gjeldende.querySelectorAll("button").length, 1);
});

test("ilokalDato bruker lokal tid, ikke UTC", () => {
  // 2026-01-01 00:30 lokal tid: `toISOString()` ville gitt 2025-12-31
  // i en tidssone øst for UTC, altså i går.
  const d = new Date(2026, 0, 1, 0, 30, 0);
  assert.equal(ilokalDato(d), "2026-01-01");
});


// =====================================================================
// FLATEN SOM HELHET, TILGANG OG TILGJENGELIGHET.
// =====================================================================

test("uten skrivescope finnes ingen skjemaer", async () => {
  const h = nyHoved();
  await visHms(h, ctx(["security:read"]));
  await vent(() => h.textContent.includes(t("ui.hms.avvik")));
  assert.equal(h.querySelector("#hms-beskrivelse"), null,
               "meldeskjemaet vises uten skrivetilgang");
  assert.equal(h.querySelector("#hms-maks"), null);
});

test("funnene står før avvikene", async () => {
  const h = nyHoved();
  await visHms(h, ctx());
  await vent(() => h.querySelectorAll("h2").length > 2);
  const titler = [...h.querySelectorAll("h2")]
    .map((h) => h.textContent);
  const iFunn = titler.indexOf(t("ui.hms.funn"));
  const iAvvik = titler.indexOf(t("ui.hms.avvik"));
  assert.ok(iFunn >= 0 && iAvvik >= 0);
  assert.ok(iFunn < iAvvik,
            "avvikslista står før funnene — det haster ikke mest");
});

test("kravskjemaet forhåndsutfylles med ALLE fire grensene",
     async () => {
  // 123s lærdom: et skjema som viser mindre enn det lagrer er en
  // felle. En grense som ikke kom med ville blitt overskrevet med
  // standardverdien første gang noen trykket lagre.
  const h = nyHoved();
  await visHms(h, ctx());
  await vent(() => h.querySelector("#hms-maks"));
  assert.equal(h.querySelector("#hms-maks").value, "3650");
  assert.equal(h.querySelector("#hms-varsel").value, "60");
  assert.equal(h.querySelector("#hms-tiltak").value, "14");
  assert.equal(h.querySelector("#hms-regelvarsel").value,
               "60");
});

test("anonymiseringspanelet viser setningen fra basen", async () => {
  // `nyHoved()` NULLSTILLER `SVAR`, så stubben må settes ETTER den.
  const h = nyHoved();
  SVAR[`/v1/hms/avvik/${A1}/oppbevaringsgrunnlag`] = {
    request_id: "r-g", avvik_id: A1,
    hjemmel: "arbeidsmiljoloven 5-1", oppbevaring_til: "2031-07-31",
    regelversjon: "2026-01", helseopplysninger: true,
    kan_anonymiseres_naa: false, dogn_igjen: 1790,
    alt_anonymisert: false,
    setning: "Opplysningen er omfattet av oppbevaringsplikt etter "
      + "arbeidsmiljoloven 5-1 (regelversjon 2026-01) og kan ikke "
      + "slettes for 2031-07-31. Jf. personvernforordningen art. 17 "
      + "nr. 3 bokstav b.",
  };
  await visHms(h, ctx());
  await vent(() => [...h.querySelectorAll("button")]
    .some((b) => b.textContent === t("ui.hms.anonymiser")));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.hms.anonymiser")).click();
  await vent(() => h.querySelector("#hms-m30-ref"));

  // SETNINGEN KOMMER ORDRETT FRA BASEN. Flaten formulerer den ikke
  // selv: hjemmelen og regelversjonen står på RADEN, og en tekst satt
  // sammen her ville vært en annen setning enn den døra håndhever.
  assert.ok(h.textContent.includes("art. 17 nr. 3 bokstav b"));
  // FØR FRISTEN ER M-30-REFERANSEN PÅKREVD.
  assert.equal(h.querySelector("#hms-m30-ref").required,
               true);
});

test("flaten er ren for axe", async () => {
  const h = nyHoved();
  await visHms(h, ctx());
  await vent(() => h.querySelector("#hms-beskrivelse"));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("den tomme flaten er ren for axe", async () => {
  SVAR["/v1/hms"] = TOMT;
  const h = nyHoved();
  await visHms(h, ctx());
  await vent(() => h.textContent
    .includes(t("ui.hms.avvik_tomt")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("ingen role=alert på en li i kilden", () => {
  // 124s funn: rollen overstyrer `listitem`, og axe felte hele lista.
  const kilde = readFileSync(join(ROT, "platform", "core", "ui",
    "static", "js", "flater", "hms.js"), "utf8");
  const uten = kilde.split("\n")
    .filter((l) => !l.trim().startsWith("//")).join("\n");
  assert.ok(!/el\("li"[^)]*role:\s*"alert"/.test(uten));
});

test("alle avvikstyper og melderformer har en tekst", () => {
  for (const a of AVVIKSTYPER) {
    const n = `ui.hms.type_${a}`;
    assert.notEqual(t(n), n, `${n} mangler i locale`);
  }
  for (const m of MELDERFORMER) {
    const n = `ui.hms.melderform_${m}`;
    assert.notEqual(t(n), n, `${n} mangler i locale`);
  }
});

test("alle funntyper har en tekst", () => {
  const typer = ["ingen_krav", "regelverk_utlopt",
                 "regelverk_utloper_snart",
                 "avvik_mot_utlopt_regelverk", "avvik_ubehandlet",
                 "oppbevaring_naermer_seg", "oppbevaring_utlopt",
                 "for_tidlig_anonymisert"];
  const tab = funntabell(typer.map((ft, i) => ({
    funn_id: `f${i}`, funntype: ft, regel_id: null, avvik_id: A1,
    over_grense: 1, detalj: null, kravversjon: 1,
    forst_sett: "2026-09-05T09:00:00+00:00",
    sist_sett_sveip: "2026-09-05T09:00:00+00:00", apen: true,
    lukket_ts: null, lukket_av: null, lukkenotat: null,
    kan_lukkes: true })), null);
  // INGEN RÅ NØKKEL PÅ SKJERMEN. 17 ruter viste en gang rå i18n-nøkler
  // fordi porten hoppet over det som manglet.
  assert.ok(!/ui\.hms\./.test(tab.textContent),
            "en funntype mangler tekst og vises som rå nøkkel");
});

test("meldeskjemaet sender navnet når melderen er navngitt", async () => {
  const h = nyHoved();
  await visHms(h, ctx());
  await vent(() => h.querySelector("#hms-melder-navn"));
  h.querySelector("#hms-beskrivelse").value =
    "Fall fra stillas i tredje etasje";
  h.querySelector("#hms-sted").value = "Byggeplass A";
  h.querySelector("#hms-melder-navn").value = "Kari Nordmann";
  const skjema = h.querySelector("#hms-beskrivelse")
    .closest("form");
  skjema.dispatchEvent(new Event("submit",
    { bubbles: true, cancelable: true }));
  await vent(() => SISTE && SISTE.sti === "/v1/hms/avvik");
  assert.equal(SISTE.kropp.melderform, "navngitt");
  assert.equal(SISTE.kropp.melder_navn, "Kari Nordmann");
});
