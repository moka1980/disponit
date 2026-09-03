// M-27 lagerflaten (109) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd på hver
//     skjerm flaten kan stå i.
//   * `modulen_bestilte` og `modulen_beregnet_prognose`: flaten har
//     INGEN «bestill påfyll»-knapp og viser INGEN prognose.
//   * `beholdning_uten_bevegelse`: flaten SETTER ingen beholdning. En
//     telling sender det TALTE antallet, og basen skriver differansen.
//     Hovedboken bærer den løpende beholdningen på hver linje — det er
//     svaret på «hvorfor står det 7 her».
//   * `belop_i_flyttall`: antall som HELTALL i varens egen enhet, og
//     enhetskost i øre. Et desimalt antall sendes aldri.
//   * `under_bestillingspunkt_uten_funn`: merket og «mangler punkt» er
//     TEKST, ikke bare farge (WCAG 1.4.1) — og «ingen punkt satt» er
//     noe annet enn «0».
//   * En lesende økt ser beholdningen, men INGEN mutasjonskontroller.
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
  antallTekst, belopTekst, dognTekst, endringTekst, punktTekst,
  tilAntall, tilOre, visLager,
} from "../static/js/flater/lager.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));

const V1 = "11111111-1111-1111-1111-111111111111";
const V2 = "22222222-2222-2222-2222-222222222222";

function locale(sprak) {
  return JSON.parse(readFileSync(
    join(HER, "..", "..", "..", "..", "locales", `${sprak}.json`),
    "utf-8"));
}

const BILDE = {
  sammendrag: {
    varer: 40, aktive: 12, med_punkt: 9, under_punkt: 3, apne_funn: 5,
    har_terskel: true, terskelversjon: 2, vist: 2,
  },
  varer: [
    { vare_id: V1, kode: "V-100", navn: "Skrue 4x40", enhet: "stk",
      aktiv: true, beholdning: 38, punkt_antall: 50, punktversjon: 2,
      dogn_siden_bevegelse: 3, dogn_siden_telling: 1,
      apne_funn: ["under_bestillingspunkt"] },
    { vare_id: V2, kode: "V-200", navn: "Kranarm", enhet: "stk",
      aktiv: false, beholdning: 0, punkt_antall: null,
      punktversjon: null, dogn_siden_bevegelse: 400,
      dogn_siden_telling: 0,
      apne_funn: ["uten_bestillingspunkt", "uten_bevegelse"] },
  ],
  terskler: {
    stille_dogn: 180, uten_punkt_dogn: 30, telleintervall_dogn: 365,
    versjon: 2, oppdatert: "2026-08-01T09:00:00+00:00",
    oppdatert_av: "kari",
  },
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    varer: 0, aktive: 0, med_punkt: 0, under_punkt: 0, apne_funn: 0,
    har_terskel: false, terskelversjon: null, vist: 0,
  },
  varer: [], terskler: null, request_id: "r-b",
};

const HOVEDBOK = {
  vare_id: V1,
  bevegelser: [
    { bevegelse_id: "b-3", bevegelsestype: "telling", endring: -2,
      enhetskost_ore: null, utfort: "2026-09-01", notat: "årstelling",
      registrert: "2026-09-01T09:00:00+00:00", registrert_av: "kari",
      beholdning_etter: 38 },
    { bevegelse_id: "b-2", bevegelsestype: "uttak", endring: -60,
      enhetskost_ore: null, utfort: "2026-08-20", notat: "jobb 12",
      registrert: "2026-08-20T09:00:00+00:00", registrert_av: "ola",
      beholdning_etter: 40 },
    { bevegelse_id: "b-1", bevegelsestype: "mottak", endring: 100,
      enhetskost_ore: 1500, utfort: "2026-08-01", notat: "palle",
      registrert: "2026-08-01T09:00:00+00:00", registrert_av: "kari",
      beholdning_etter: 100 },
  ],
  request_id: "r-c",
};

let SVAR;
let SISTE;
let KALL;
let SVARSTATUS;
// STIER SOM SVARER SENT. Uten dem er kappløpet mellom to åpninger ikke
// et kappløp, og porten under ville målt ingenting.
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
        json: async () => ({ feil: "lager_ulovlig_tilstand" }) };
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
    "/v1/lager": BILDE,
    [`/v1/lager/${V1}/bevegelser`]: HOVEDBOK,
    [`/v1/lager/${V2}/bevegelser`]: { vare_id: V2, bevegelser: [],
                                      request_id: "r-d" },
  };
}

// Tabellrekkefølgen: varene (0), tersklene (1) og — når detaljpanelet
// står åpent — hovedboken (2).
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
// belop_i_flyttall — ANTALL OG ØRE
// ---------------------------------------------------------------------

test("Lager: antall er heltall i varens egen enhet", () => {
  assert.equal(antallTekst(38, "stk"),
    t("ui.lager.antall").replace("{antall}", "38").replace("{enhet}",
      "stk"));
  assert.equal(antallTekst(0, "stk"),
    t("ui.lager.antall").replace("{antall}", "0").replace("{enhet}",
      "stk"));
  // ET DESIMALT ANTALL ER IKKE ET ANTALL ENHETER.
  assert.equal(antallTekst(2.5, "stk"), "—");
  assert.equal(antallTekst(null, "stk"), "—");

  // FORTEGNET SKAL SES: -60 er noe annet enn 60.
  assert.equal(endringTekst(100, "stk"),
    t("ui.lager.antall").replace("{antall}", "+100")
      .replace("{enhet}", "stk"));
  assert.equal(endringTekst(-60, "stk"),
    t("ui.lager.antall").replace("{antall}", "-60")
      .replace("{enhet}", "stk"));
  assert.equal(endringTekst(0, "stk"),
    t("ui.lager.antall").replace("{antall}", "0")
      .replace("{enhet}", "stk"));
  assert.equal(endringTekst(1.5, "stk"), "—");

  // BELØP I HELTALLSARITMETIKK, aldri via `/100`.
  assert.equal(belopTekst(1500), "15,00");
  assert.equal(belopTekst(0), "0,00");
  assert.equal(belopTekst(123456799), "1234567,99");
  assert.equal(belopTekst(12.5), "—");

  // FLATEN RUNDER IKKE BORT ET DESIMALT ANTALL — DEN NEKTER.
  assert.equal(tilAntall("38"), 38);
  assert.equal(tilAntall("0"), 0);
  assert.equal(tilAntall("2.5"), null);
  assert.equal(tilAntall("-1"), null);
  assert.equal(tilAntall("to"), null);
  // ET TOMT FELT ER IKKE NULL ENHETER. `Number("")` er 0, og uten
  // vakten ville en tom telling blitt sendt som «vi talte til null».
  assert.equal(tilAntall(""), null);
  assert.equal(tilAntall("   "), null);
  assert.equal(tilAntall(null), null);
  assert.equal(tilAntall(undefined), null);
  // …men kroner blir til øre, med `Math.round` på produktet.
  assert.equal(tilOre("15.00"), 1500);
  assert.equal(tilOre("8.15"), 815);
  assert.equal(tilOre(""), null);
  assert.equal(tilOre("gratis"), null);
});

test("Lager: «ingen punkt satt» er ikke «0»", () => {
  assert.equal(punktTekst(50, "stk"),
    t("ui.lager.antall").replace("{antall}", "50")
      .replace("{enhet}", "stk"));
  // NULL ER LOVLIG OG BETYR NOE: «vi holder ikke lager på denne».
  assert.equal(punktTekst(0, "stk"),
    t("ui.lager.antall").replace("{antall}", "0")
      .replace("{enhet}", "stk"));
  // …mens fraværet av et punkt er et helt annet svar.
  assert.equal(punktTekst(null, "stk"), t("ui.lager.uten_punkt"));
  assert.equal(punktTekst(undefined, "stk"), t("ui.lager.uten_punkt"));
});

test("Lager: døgn siden sist er tekst, og entall har egen nøkkel", () => {
  assert.equal(dognTekst(400),
    t("ui.lager.dogn_siden").replace("{dogn}", "400"));
  assert.equal(dognTekst(1), t("ui.lager.ett_dogn_siden"));
  assert.equal(dognTekst(0), t("ui.lager.i_dag"));
  assert.equal(dognTekst(null), "—");
});

test("Lager: begge språk har entallsnøklene", () => {
  for (const sprak of ["nb", "en"]) {
    const tekster = locale(sprak);
    for (const n of ["ui.lager.ett_dogn_siden", "ui.lager.i_dag"]) {
      assert.ok(tekster[n], `${sprak} mangler ${n}`);
      assert.ok(!tekster[n].includes("{dogn}"),
        `${sprak}: ${n} har en tallplass — da er den ikke entallsformen`);
    }
  }
});

// ---------------------------------------------------------------------
// modulen_bestilte / modulen_beregnet_prognose — flatens halvdel
// ---------------------------------------------------------------------

test("Lager: flaten bestiller ingenting og viser ingen prognose", () => {
  const kilde = readFileSync(
    join(HER, "..", "static", "js", "flater", "lager.js"), "utf8");
  // Kommentarene forklarer FRAVÆRET og må derfor ikke telle med.
  const uten = kilde.replace(/^\s*\/\/.*$/gm, "");
  for (const ord of ["prognose", "forbruksrate", "ekstrapol",
                     "attester", "signer", "innkjøp", "/v1/leverandor"]) {
    assert.ok(!uten.toLowerCase().includes(ord.toLowerCase()),
      `flaten bærer «${ord}» — v1 bestiller ingenting`);
  }
  // «bestillingspunkt» er GRENSEN som utløser funnet; «bestill» alene
  // ville vært handlingen v1 ikke gjør.
  assert.ok(!/bestill(?!ingspunkt|ingsPunkt|ing:opprett)/i.test(uten),
    "flaten bærer en bestillingshandling");
  const api = readFileSync(
    join(HER, "..", "static", "js", "api.js"), "utf8");
  assert.ok(!/export const bestillPafyll/.test(api));
  assert.ok(!/export const settBeholdning/.test(api));
});

// ---------------------------------------------------------------------
// Skjermene — og axe på hver av dem
// ---------------------------------------------------------------------

test("Lager: listen viser beholdning, punkt og merker, axe rent",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visLager(h, ctx());
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
    // KODEN NAVNGIR RADEN — det er den et uttak siterer.
    assert.equal(rader[0].querySelector('th[scope="row"]').textContent,
      "V-100");
    assert.ok(rader[0].textContent.includes(
      t("ui.lager.antall").replace("{antall}", "38")
        .replace("{enhet}", "stk")));
    // MERKET ER TEKST (WCAG 1.4.1).
    assert.ok(rader[0].textContent.includes(
      t("ui.lager.merke_under_punkt")));
    assert.ok(rader[0].textContent.includes(
      t("ui.lager.dogn_siden").replace("{dogn}", "3")));
    // EN VARE UTEN PUNKT SIER DET MED ORD, ikke med «0».
    assert.ok(rader[1].textContent.includes(t("ui.lager.uten_punkt")));
    assert.ok(rader[1].textContent.includes(
      t("ui.lager.merke_uten_punkt")));
    assert.ok(rader[1].textContent.includes(t("ui.lager.merke_stille")));
    assert.ok(rader[1].textContent.includes(t("ui.lager.status.inaktiv")));

    // SAMMENDRAGET TELLER ALT, og avkortingen sies høyt.
    assert.ok(h.textContent.includes(
      t("ui.lager.avkortet").replace("{vist}", "2")));
    assert.ok(!h.textContent.includes(t("ui.lager.ingen_terskler")));
    // …og HVORFOR REGISTERET FINNES står på flaten.
    assert.ok(h.textContent.includes(t("ui.lager.oversikt.hvorfor")));
  });

test("Lager: tomt register sier hva som mangler, axe rent", async () => {
  SVAR = { "/v1/lager": TOMT };
  const h = nyHoved();
  visLager(h, ctx());
  await vent(() => h.textContent.includes(t("ui.lager.liste.ingen")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  assert.ok(h.textContent.includes(t("ui.lager.ingen_terskler")));
});

// ---------------------------------------------------------------------
// beholdning_uten_bevegelse — HOVEDBOKEN ER SKJERMEN SOM BETYR NOE
// ---------------------------------------------------------------------

test("Lager: hovedboken svarer på hvorfor det står 38", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visLager(h, ctx());
  await apneForste(h);

  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));

  const rader = [...tabeller(h)[2].querySelectorAll("tbody tr")];
  assert.equal(rader.length, 3);
  // NYESTE ØVERST, og hver linje bærer DEN LØPENDE BEHOLDNINGEN. En
  // leser som måtte summere selv ville ikke kunne se hvor tallet kom
  // fra — og det er hele spørsmålet hovedboken finnes for å svare på.
  assert.ok(rader[0].textContent.includes(t("ui.lager.type.telling")));
  assert.ok(rader[0].textContent.includes(
    t("ui.lager.antall").replace("{antall}", "-2")
      .replace("{enhet}", "stk")));
  assert.ok(rader[0].textContent.includes(
    t("ui.lager.antall").replace("{antall}", "38")
      .replace("{enhet}", "stk")));
  assert.ok(rader[0].textContent.includes("årstelling"));
  // MOTTAKET BÆRER ENHETSKOSTEN; uttaket har ingen, og det er «—», ikke
  // «0,00» — en tvungen null ville vært en påstand om at varen var
  // gratis.
  assert.ok(rader[2].textContent.includes("15,00"));
  assert.ok(rader[1].textContent.includes("—"));
  assert.ok(!rader[1].textContent.includes("0,00"));
  assert.ok(h.textContent.includes("V-100 · Skrue 4x40 · stk"));
});

test("Lager: en vare uten bevegelser sier det", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visLager(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  tabeller(h)[0].querySelectorAll("tbody tr")[1]
    .querySelector("button").click();
  assert.ok(await vent(() => h.textContent.includes(
    t("ui.lager.detalj.ingen"))), "tomheten ble aldri sagt");
  // EN INAKTIV VARE TAR IKKE IMOT BEVEGELSER ELLER TELLINGER…
  for (const id of ["#lg-bev-antall", "#lg-tell-antall"]) {
    const knapp = h.querySelector(id).closest("form")
      .querySelector("button[type=submit]");
    assert.equal(knapp.disabled, true, id);
  }
  // …men den KAN aktiveres igjen, så den knappen står levende.
  const aktiv = [...h.querySelectorAll("button[type=submit]")]
    .find((b) => b.textContent === t("ui.lager.knapp.aktiver"));
  assert.ok(aktiv, "aktiveringsknappen mangler eller bærer feil tekst");
  assert.equal(aktiv.disabled, false);
});

test("Lager: en treg hovedbok tegnes ikke inn i en annen vares panel",
  async () => {
    // Åpner noen vare B mens As hovedbok fortsatt er underveis, ville As
    // svar ellers blitt tegnet inn i Bs panel — altså en beholdning som
    // ser ut til å høre til en annen vare. I DETTE registeret er det
    // ikke en kosmetisk feil (CodeRabbit, 109).
    SVAR = fullSvar();
    const h = nyHoved();
    visLager(h, ctx());
    await vent(() => tabeller(h).length >= 2);
    TREGE.add(`/v1/lager/${V1}/bevegelser`);
    const rader = [...tabeller(h)[0].querySelectorAll("tbody tr")];
    rader[0].querySelector("button").click();   // treg
    rader[1].querySelector("button").click();   // rask
    assert.ok(await vent(() => h.textContent.includes(
      t("ui.lager.detalj.ingen"))), "Bs tomme hovedbok kom aldri");
    // …og når As svar endelig lander, skal det IKKE overskrive Bs.
    await vent(() => false, 40);
    assert.ok(h.textContent.includes(t("ui.lager.detalj.ingen")),
      "den trege hovedboken ble tegnet inn i feil vares panel");
    assert.ok(!h.textContent.includes("årstelling"),
      "V-100s linjer står i V-200s panel");
    assert.ok(h.textContent.includes("V-200 · Kranarm · stk"));
  });

// ---------------------------------------------------------------------
// Skriveveiene
// ---------------------------------------------------------------------

test("Lager: bevegelsen sendes som STØRRELSE, med typen ved siden av",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visLager(h, ctx());
    await apneForste(h);
    // TYPEVALGET ER ET LUKKET SETT, og `telling` står ikke i det.
    const valg = [...h.querySelector("#lg-bev-type").options]
      .map((o) => o.value);
    assert.deepEqual(valg, ["mottak", "uttak", "retur", "svinn"]);

    h.querySelector("#lg-bev-type").value = "uttak";
    h.querySelector("#lg-bev-antall").value = "60";
    h.querySelector("#lg-bev-kost").value = "";
    h.querySelector("#lg-bev-dato").value = "2026-09-02";
    h.querySelector("#lg-bev-notat").value = "jobb 12";
    h.querySelector("#lg-bev-antall").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE && SISTE.sti.includes("/bevegelse"));
    // ANTALLET ER POSITIVT — fortegnet følger av typen, i basen.
    assert.equal(SISTE.kropp.antall, 60);
    assert.equal(SISTE.kropp.bevegelsestype, "uttak");
    assert.equal(SISTE.kropp.enhetskost_ore, null);
    assert.equal(SISTE.kropp.notat, "jobb 12");
    assert.ok(SISTE.headers["Idempotency-Key"]);
  });

test("Lager: enhetskosten sendes i ØRE når den er oppgitt", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visLager(h, ctx());
  await apneForste(h);
  h.querySelector("#lg-bev-type").value = "mottak";
  h.querySelector("#lg-bev-antall").value = "100";
  h.querySelector("#lg-bev-kost").value = "15.00";
  h.querySelector("#lg-bev-dato").value = "2026-09-02";
  h.querySelector("#lg-bev-notat").value = "palle";
  h.querySelector("#lg-bev-antall").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("/bevegelse"));
  assert.equal(SISTE.kropp.enhetskost_ore, 1500);
});

test("Lager: tellingen sender det TALTE antallet, ikke en beholdning",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visLager(h, ctx());
    await apneForste(h);
    h.querySelector("#lg-tell-antall").value = "40";
    h.querySelector("#lg-tell-dato").value = "2026-09-02";
    h.querySelector("#lg-tell-notat").value = "årstelling";
    h.querySelector("#lg-tell-antall").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE && SISTE.sti.includes("/telling"));
    assert.equal(SISTE.kropp.talt_antall, 40);
    // INGEN BEHOLDNING SENDES: basen skriver differansen som en linje.
    assert.ok(!("beholdning" in SISTE.kropp));
    assert.ok(!("endring" in SISTE.kropp));
    // …og hjelpeteksten sier nettopp det, på begge språk.
    for (const sprak of ["nb", "en"]) {
      const hjelp = locale(sprak)["ui.lager.skjema.talt_hjelp"];
      assert.ok(/differansen|difference/i.test(hjelp),
        `${sprak}: hjelpeteksten sier ikke hva tellingen faktisk gjør`);
    }
  });

test("Lager: bestillingspunktet sendes med begrunnelse", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visLager(h, ctx());
  await apneForste(h);
  h.querySelector("#lg-punkt-antall").value = "50";
  h.querySelector("#lg-punkt-fra").value = "2026-10-01";
  h.querySelector("#lg-punkt-grunn").value = "sesong";
  assert.equal(h.querySelector("#lg-punkt-grunn").required, true);
  h.querySelector("#lg-punkt-antall").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("/punkt"));
  assert.equal(SISTE.kropp.punkt_antall, 50);
  assert.equal(SISTE.kropp.begrunnelse, "sesong");
  for (const sprak of ["nb", "en"]) {
    const hjelp = locale(sprak)["ui.lager.skjema.begrunnelse_hjelp"];
    assert.ok(/etterprøv|review|penger|money|spend/i.test(hjelp),
      `${sprak}: hjelpeteksten sier ikke hva som står på spill`);
  }
});

test("Lager: grensene er forhåndsutfylt og sendes som døgn", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visLager(h, ctx());
  await vent(() => !!h.querySelector("#lg-t-stille"));
  // EN GRENSE MAN MÅ GJETTE PÅ BLIR SATT FEIL.
  assert.equal(h.querySelector("#lg-t-stille").value, "180");
  assert.equal(h.querySelector("#lg-t-punkt").value, "30");
  assert.equal(h.querySelector("#lg-t-telle").value, "365");
  h.querySelector("#lg-t-stille").value = "90";
  h.querySelector("#lg-t-stille").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("/terskler"));
  assert.equal(SISTE.kropp.stille_dogn, 90);
  assert.equal(SISTE.kropp.uten_punkt_dogn, 30);
  assert.equal(SISTE.kropp.telleintervall_dogn, 365);
});

test("Lager: alle seks skriveveiene sender en Idempotency-Key", () => {
  // Kommentaren i api.js sa en gang at bestillingspunktet IKKE bar en
  // egen nøkkel. Innpakningen gjorde det hele tiden, og en kommentar som
  // motsier koden er verre enn ingen kommentar (CodeRabbit, 109).
  const api = readFileSync(
    join(HER, "..", "static", "js", "api.js"), "utf8");
  const navn = ["settLagerterskler", "registrerVare",
                "settBestillingspunkt", "registrerBevegelse",
                "registrerTelling", "settVareAktiv"];
  for (const n of navn) {
    const i = api.indexOf(`export const ${n} =`);
    assert.ok(i > 0, `${n} mangler i api.js`);
    const kropp = api.slice(i, api.indexOf("\n\n", i));
    assert.ok(/idem \|\| nyIdempotensnokkel\(\)/.test(kropp),
      `${n} sender ingen Idempotency-Key`);
  }
  // …og API-modulen har ingen vei som SETTER en beholdning eller
  // BESTILLER noe.
  assert.ok(!/export const (settBeholdning|bestillPafyll)/.test(api));
});

test("Lager: kvitteringen og panelet overlever tegningen", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visLager(h, ctx());
  await apneForste(h);
  h.querySelector("#lg-bev-antall").value = "1";
  h.querySelector("#lg-bev-dato").value = "2026-09-02";
  h.querySelector("#lg-bev-notat").value = "x";
  h.querySelector("#lg-bev-antall").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.ok(await vent(() => KALL.filter(
    (k) => k.sti.includes("/bevegelser")).length >= 2),
    "panelet ble aldri gjenåpnet — porten måler ingenting");
  assert.ok(h.textContent.includes(t("ui.lager.skjema.bevegelse_ok")),
    "kvitteringen forsvant i tegningen");
  assert.ok(h.textContent.includes("V-100 · Skrue 4x40 · stk"),
    "panelet lukket seg etter en føring");
});

test("Lager: en 409 sier hva registeret nektet", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visLager(h, ctx());
  await vent(() => !!h.querySelector("#lg-ny-kode"));
  SVARSTATUS = 409;
  h.querySelector("#lg-ny-kode").value = "V-100";
  h.querySelector("#lg-ny-navn").value = "Dublett";
  h.querySelector("#lg-ny-enhet").value = "stk";
  const skjema = h.querySelector("#lg-ny-kode").closest("form");
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.ok(await vent(() => h.textContent.includes(
    t("ui.lager.feil.tilstand"))), "tilstandsfeilen ble ikke vist");
  assert.ok(!h.textContent.includes(t("ui.lager.feil.generell")));
  assert.ok(h.querySelector('[role="alert"]'));
  assert.equal(skjema.querySelector("button[type=submit]").disabled, false);
});

// ---------------------------------------------------------------------
// Scope og språk
// ---------------------------------------------------------------------

test("Lager: en lesende økt ser beholdningen, men ingen kontroller",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visLager(h, ctx(["okonomi:read"]));
    await vent(() => tabeller(h).length >= 2);
    assert.ok(h.textContent.includes("V-100"));
    assert.equal(h.querySelectorAll("form").length, 0);
    assert.equal(h.querySelectorAll("input, select, textarea").length, 0);
    // …men hovedboken er en LESNING, og den står åpen.
    assert.ok([...h.querySelectorAll("button")].some(
      (b) => b.textContent === t("ui.lager.knapp.apne")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Lager: ingen hardkodet tekst", async () => {
  const PL = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visLager(h, ctx());
    await apneForste(h);
    // `th[scope="row"]` er UTELATT: den cellen bærer varekoden, datoen
    // og grensenavnet — altså tenantens egne data.
    for (const node of h.querySelectorAll(
      'h2, h3, h4, label, caption, th[scope="col"], button, option')) {
      const s = node.textContent.trim();
      if (!s) continue;
      assert.ok(s.startsWith("PL_"), `hardkodet tekst: «${s}»`);
    }
  } finally {
    settI18nForTest(NB, "nb");
  }
});
