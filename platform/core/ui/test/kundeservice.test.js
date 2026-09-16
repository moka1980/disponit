// M-17 kundeserviceflaten (102, PR-A) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd, på hver
//     av skjermene flaten kan stå i (kø, tom kø, detaljpanel åpent,
//     leseøkt uten innsyn).
//   * ALDER OG FUNN ER TEKST, ikke bare farge (WCAG 1.4.1).
//   * KØEN OG INNHOLDET ER TO KALL. Listen bærer aldri kundeteksten;
//     den hentes først når et menneske åpner raden.
//   * UTEN `kundeservice:innhold` sier flaten det med rene ord i
//     stedet for å vise en tom boks — og den henter ikke innholdet.
//   * MODULEN SENDER INGENTING: ingen kontroll på flaten kaller noe som
//     ligner en sendevei, og de eneste to dommene et utkast kan få heter
//     `forkastet` og `brukt_manuelt`.
//   * En lesende økt ser køen, men INGEN mutasjonskontroller.
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
  alderTekst, avsenderTekst, klassifiseringTekst, kortref, regelTekst,
  visKundeservice,
} from "../static/js/flater/kundeservice.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));

const H1 = "11111111-1111-1111-1111-111111111111";
const H2 = "22222222-2222-2222-2222-222222222222";

const KOEN = {
  sammendrag: {
    apne: 42, uklassifiserte: 7, i_unntakskoe: 2, kritiske: 1,
    apne_funn: 9, lukkede_siste_30: 130, vist: 2,
  },
  koe: [
    { henvendelse_id: H1, kanal: "epost", ekstern_ref: "MSG-2026-0001",
      mottatt: "2026-08-20T09:00:00+00:00", avsender_hash: "a".repeat(64),
      alder_dogn: 13, prioritet: null, tema: null, handlingstype: null,
      klassifisert_av: null, i_unntakskoe: false, antall_utkast: 0,
      brukt_utkast: false,
      apne_funn: ["uklassifisert_over_grense"] },
    { henvendelse_id: H2, kanal: "skjema", ekstern_ref: "MSG-2026-0002",
      mottatt: "2026-09-01T09:00:00+00:00", avsender_hash: "b".repeat(64),
      alder_dogn: 1, prioritet: "kritisk", tema: "klage",
      handlingstype: "mistenkelig", klassifisert_av: "menneske",
      i_unntakskoe: true, antall_utkast: 2, brukt_utkast: true,
      apne_funn: [] },
  ],
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    apne: 0, uklassifiserte: 0, i_unntakskoe: 0, kritiske: 0,
    apne_funn: 0, lukkede_siste_30: 0, vist: 0,
  },
  koe: [], request_id: "r-b",
};

const INNHOLD = {
  henvendelse_id: H1, emne: "Faktura stemmer ikke",
  kropp: "Hei, jeg fikk faktura paa 5000 men avtalte 3000.",
  request_id: "r-c",
};

const UTKASTENE = {
  henvendelse_id: H1,
  utkast: [
    { utkast_id: "33333333-3333-3333-3333-333333333333",
      tekst: "Vi har sett paa avtalen din og krediterer 2000.",
      kunnskapsref: ["begrep:kreditnota"], kilde: "menneske",
      modell_digest: null, status: "foreslatt",
      opprettet: "2026-09-02T08:00:00+00:00", opprettet_av: "bid_a" },
  ],
  request_id: "r-d",
};

let SVAR;
let SISTE;
let KALL;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  KALL.push({ sti, metode: (opts && opts.method) || "GET" });
  if (opts && opts.method === "POST") {
    SISTE = { sti, kropp: JSON.parse(opts.body), headers: opts.headers };
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  }
  const oppf = SVAR[sti];
  if (!oppf) {
    return { ok: false, status: 404,
      json: async () => ({ feil: "ikke_funnet" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx(scopes = ["decisions:read", "kundeservice:innhold",
                       "bestilling:opprett"]) {
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
  return m;
}

function fullSvar() {
  return {
    "/v1/kundeservice": KOEN,
    [`/v1/kundeservice/henvendelse/${H1}/innhold`]: INNHOLD,
    [`/v1/kundeservice/henvendelse/${H1}/utkast`]: UTKASTENE,
  };
}

test("Kundeservice: alder og klassifisering som TEKST", () => {
  assert.equal(alderTekst(13), t("ui.kundeservice.alder_dogn")
    .replace("{dogn}", "13"));
  // ENTALL HAR SIN EGEN SETNING: «1 days» ville stått på den raden et
  // menneske leser først.
  assert.equal(alderTekst(1), t("ui.kundeservice.alder_ett_dogn"));
  assert.equal(alderTekst(0), t("ui.kundeservice.alder_i_dag"));
  assert.equal(alderTekst(null), "—");
  // EN UKLASSIFISERT HENVENDELSE SIER DET. En tom celle ville lest som
  // «normal», og det er nettopp den forvekslingen sveipen finnes for.
  assert.equal(klassifiseringTekst({}), t("ui.kundeservice.uklassifisert"));
  assert.ok(klassifiseringTekst({ prioritet: "kritisk", tema: "klage",
    handlingstype: "mistenkelig" })
    .includes(t("ui.kundeservice.prioritet.kritisk")));
});

test("Kundeservice: køen tegnes med funn som tekst, axe rent",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKundeservice(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 2);

    const tb = h.querySelector("table");
    assert.ok(tb.querySelector("caption").textContent.trim());
    assert.ok(tb.querySelector('th[scope="col"]'));
    for (const rad of tb.querySelectorAll("tbody tr")) {
      // Uten th scope="row" mister en skjermleser i alders- og
      // klassifiseringskolonnene hvilken henvendelse raden gjelder.
      assert.equal(rad.cells[0].tagName, "TH");
      assert.equal(rad.cells[0].getAttribute("scope"), "row");
    }

    const tekst = h.textContent;
    assert.ok(tekst.includes(t("ui.kundeservice.merke_uklassifisert")));
    assert.ok(tekst.includes(t("ui.kundeservice.merke_i_koe")));
    assert.ok(tekst.includes(t("ui.kundeservice.uklassifisert")));
    assert.ok(tekst.includes(t("ui.kundeservice.alder_ett_dogn")));
    // SAMMENDRAGET TELLER ALT: 42 åpne, ikke 2.
    assert.ok(tekst.includes("42"));
    assert.ok(tekst.includes(
      t("ui.kundeservice.avkortet").replace("{vist}", "2")),
    "flaten sier ikke at listen er avkortet");

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Kundeservice: listen bærer ALDRI kundeteksten", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visKundeservice(h, ctx());
  await vent(() => h.querySelectorAll("table tbody tr").length === 2);
  // KØEN OG INNHOLDET ER TO KALL. Så lenge ingen har åpnet en rad, er
  // det INGEN innholdskall gjort — og teksten finnes ikke på skjermen.
  // Ved tegning hentes køen og (204) regellisten for stille avsendere;
  // den bærer domener og hasher, aldri kundetekst. Settet er PINNET, så
  // et tredje kall ved tegning må begrunnes her.
  await vent(() => KALL.length >= 2);
  assert.deepEqual([...new Set(KALL.map((k) => k.sti))].sort(),
    ["/v1/kundeservice", "/v1/kundeservice/stilleregler"]);
  assert.ok(!KALL.some((k) => k.sti.includes("/innhold")),
    "innholdet ble hentet uten at noen åpnet en rad");
  assert.ok(!h.textContent.includes(INNHOLD.kropp));
  assert.ok(!h.textContent.includes(INNHOLD.emne));
});

test("Kundeservice: innholdet hentes først når en rad åpnes",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKundeservice(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 2);
    const apne = [...h.querySelectorAll("tbody button")].filter(
      (b) => b.textContent === t("ui.kundeservice.knapp.apne"));
    assert.equal(apne.length, 2);
    apne[0].click();
    await vent(() => h.textContent.includes(INNHOLD.kropp));
    assert.ok(h.textContent.includes(INNHOLD.emne));
    // …og utkastene kom fra sitt eget kall.
    await vent(() => h.textContent.includes(UTKASTENE.utkast[0].tekst));
    assert.ok(KALL.some(
      (k) => k.sti === `/v1/kundeservice/henvendelse/${H1}/innhold`));
    assert.ok(KALL.some(
      (k) => k.sti === `/v1/kundeservice/henvendelse/${H1}/utkast`));

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Kundeservice: uten innsynsscope sies det med ord, og ingenting hentes",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKundeservice(h, ctx(["decisions:read", "bestilling:opprett"]));
    await vent(() => h.querySelectorAll("table tbody tr").length === 2);
    const apne = [...h.querySelectorAll("tbody button")].filter(
      (b) => b.textContent === t("ui.kundeservice.knapp.apne"));
    apne[0].click();
    await vent(() => h.textContent.includes(
      t("ui.kundeservice.detalj.uten_innsyn")));
    // ÆRLIG OM HVA SOM MANGLER: en setning, ikke en tom boks — og
    // klassifiseringsarbeidet er fortsatt mulig.
    assert.ok(h.querySelector("#ks-handlingstype"),
      "den som ikke får lese teksten skal fortsatt kunne klassifisere");
    // …og flaten spurte ALDRI etter innholdet.
    assert.ok(!KALL.some((k) => k.sti.endsWith("/innhold")),
      "flaten hentet innholdet uten å ha scopet for det");

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Kundeservice: en lesende økt får ingen mutasjonskontroller",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKundeservice(h, ctx(["decisions:read",
                            "kundeservice:innhold"]));
    await vent(() => h.querySelectorAll("table tbody tr").length === 2);
    assert.equal(h.querySelectorAll("form").length, 0);
    // «Åpne» er ikke en mutasjon og skal stå igjen — men ingen av de
    // seks skriveknappene.
    for (const nokkel of ["ui.kundeservice.knapp.klassifiser",
                          "ui.kundeservice.knapp.unntakskoe",
                          "ui.kundeservice.knapp.utkast",
                          "ui.kundeservice.knapp.lukk_besvart",
                          "ui.kundeservice.knapp.lukk_ikke_aktuell"]) {
      assert.ok(!h.textContent.includes(t(nokkel)), nokkel);
    }
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Kundeservice: klassifiseringen sender de tre aksene med nøkkel",
  async () => {
    SVAR = fullSvar();
    SISTE = null;
    const h = nyHoved();
    visKundeservice(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 2);
    [...h.querySelectorAll("tbody button")].find(
      (b) => b.textContent === t("ui.kundeservice.knapp.apne")).click();
    await vent(() => h.querySelector("#ks-handlingstype") !== null);
    h.querySelector("#ks-prioritet").value = "hoy";
    h.querySelector("#ks-tema").value = "faktura";
    h.querySelector("#ks-handlingstype").value = "svar_kreves";
    h.querySelector("#ks-handlingstype").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE !== null);
    assert.equal(SISTE.sti,
      `/v1/kundeservice/henvendelse/${H1}/klassifiser`);
    assert.deepEqual(SISTE.kropp, { prioritet: "hoy", tema: "faktura",
      handlingstype: "svar_kreves" });
    assert.ok(SISTE.headers["Idempotency-Key"]);
  });

test("Kundeservice: utkastets tre dommer, og ingen av dem heter sendt",
  async () => {
    SVAR = fullSvar();
    SISTE = null;
    const h = nyHoved();
    visKundeservice(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 2);
    [...h.querySelectorAll("tbody button")].find(
      (b) => b.textContent === t("ui.kundeservice.knapp.apne")).click();
    await vent(() => h.textContent.includes(UTKASTENE.utkast[0].tekst));
    // ARC B (160): «Godkjenn for sending» er et menneskes ja til at
    // PLATTFORMEN sender — flaten sender fortsatt ingenting.
    const dommer = [...h.querySelectorAll("button")]
      .map((b) => b.textContent)
      .filter((s) => s === t("ui.kundeservice.knapp.forkast")
                  || s === t("ui.kundeservice.knapp.brukt")
                  || s === t("ui.kundeservice.knapp.godkjenn"));
    assert.deepEqual(dommer.sort(),
      [t("ui.kundeservice.knapp.brukt"),
        t("ui.kundeservice.knapp.forkast"),
        t("ui.kundeservice.knapp.godkjenn")].sort());
    assert.ok(!h.textContent.toLowerCase().includes("send svar"));
    [...h.querySelectorAll("button")].find(
      (b) => b.textContent === t("ui.kundeservice.knapp.brukt")).click();
    await vent(() => SISTE !== null);
    assert.equal(SISTE.sti,
      `/v1/kundeservice/utkast/${UTKASTENE.utkast[0].utkast_id}/dom`);
    assert.equal(SISTE.kropp.status, "brukt_manuelt");
  });

test("Kundeservice: en uklassifisert rad arver ikke forrige rads dom",
  async () => {
    // PANELET GJENBRUKES FOR HVER RAD. Uten nullstillingen bærer skjemaet
    // forrige henvendelses klassifisering, og ett klikk på «Lagre»
    // skriver den over på denne. Det er ikke en visningsfeil — det er
    // feil data i registeret.
    SVAR = fullSvar();
    const h = nyHoved();
    visKundeservice(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 2);
    const apne = [...h.querySelectorAll("tbody button")].filter(
      (b) => b.textContent === t("ui.kundeservice.knapp.apne"));
    // Rad 2 er KLASSIFISERT (kritisk/klage/mistenkelig) — åpne den først.
    apne[1].click();
    await vent(() => h.querySelector("#ks-prioritet") !== null);
    assert.equal(h.querySelector("#ks-prioritet").value, "kritisk");
    assert.equal(h.querySelector("#ks-handlingstype").value, "mistenkelig");
    // Rad 1 er UKLASSIFISERT. Skjemaet skal stå på standardverdiene.
    apne[0].click();
    await vent(() => h.querySelector("#ks-prioritet").value === "normal");
    assert.equal(h.querySelector("#ks-prioritet").value, "normal");
    assert.notEqual(h.querySelector("#ks-handlingstype").value,
      "mistenkelig");
    assert.notEqual(h.querySelector("#ks-tema").value, "klage");
  });

test("Kundeservice: tom kø sier det, axe rent", async () => {
  SVAR = { "/v1/kundeservice": TOMT };
  const h = nyHoved();
  visKundeservice(h, ctx());
  await vent(() => h.textContent.includes(t("ui.kundeservice.koe.ingen")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Kundeservice: ingen hardkodet tekst i flaten", async () => {
  const nokler = Object.keys(NB).filter(
    (k) => k.startsWith("ui.kundeservice"));
  assert.ok(nokler.length > 50, `bare ${nokler.length} nøkler`);
  const pseudo = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(pseudo, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visKundeservice(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 2);
    // `th[scope="row"]` er UTELATT: den cellen bærer kanalens egen
    // referanse, altså kundens data — ikke en oversatt etikett.
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

test("Kundeservice: kilden bærer ingen sendevei", () => {
  // MODULEN SENDER INGENTING, målt på FLATENS kilde. De andre halvdelene
  // av samme dom står i `test_m17_kundeservice.py` (AST, datamodellen,
  // rutene); denne fanger en knapp som kalte et endepunkt som ikke
  // finnes ennå — altså den formen en sendevei ville hatt her først.
  const kilde = readFileSync(
    join(HER, "..", "static", "js", "flater", "kundeservice.js"), "utf8");
  const uten = kilde.replace(/^\s*\/\/.*$/gm, "");
  for (const ord of ["/send", "smtp", "\"sendt\"", "sendSvar"]) {
    assert.ok(!uten.toLowerCase().includes(ord.toLowerCase()),
      `flaten bærer «${ord}» — v1 sender ingenting`);
  }
  const api = readFileSync(
    join(HER, "..", "static", "js", "api.js"), "utf8");
  assert.ok(!/export const sendSvar/.test(api));
});


// KVITTERINGEN SKAL OVERLEVE TEGNINGEN.
//
// Porten finnes fordi det var galt i alle fem flatene i klyngen:
// suksessmeldingen ble satt i skjemaets eget `utfall`, og `last()` bygde
// straks både panelet og skjemaet på nytt. Brukeren trykket, så skjermen
// blinke, og satt igjen uten å vite om det gikk bra. Skjermleseren hørte
// det (`meldLive`), men en seende bruker fikk ingenting.
//
// MUTASJONEN SOM DREPER DENNE: flytt kvitteringen tilbake inn i `kropp`.
test("Kundeservice: kvitteringen og panelet overlever tegningen",
  async () => {
    SVAR = fullSvar();
    SISTE = null;
    const h = nyHoved();
    visKundeservice(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 2);
    [...h.querySelectorAll("tbody button")].find(
      (b) => b.textContent === t("ui.kundeservice.knapp.apne")).click();
    await vent(() => h.querySelector("#ks-handlingstype") !== null);
    h.querySelector("#ks-prioritet").value = "hoy";
    h.querySelector("#ks-tema").value = "faktura";
    h.querySelector("#ks-handlingstype").value = "svar_kreves";
    h.querySelector("#ks-handlingstype").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE !== null);
    // NØKKELEN ER `klassifisering_ok`, ikke `klassifiser_ok`. Første
    // utgave ventet på en streng som aldri kom — og siden `vent()` bare
    // RETURNERER falskt uten å kaste, var porten grønn på en flate der
    // kvitteringen aldri ble vist. En `vent()` uten `assert` etterpå er
    // ingen port. (CodeRabbit.)
    assert.ok(await vent(() => h.textContent.includes(
      t("ui.kundeservice.skjema.klassifisering_ok"))),
      "kvitteringen forsvant i tegningen");
    // …OG PANELET STÅR ÅPENT PÅ SAMME RAD. Uten dette måtte brukeren
    // finne fram til raden igjen for hver eneste handling.
    assert.ok(await vent(() => h.querySelector("#ks-handlingstype") !== null),
      "panelet lukket seg etter en klassifisering");
  });

// ---------------------------------------------------------------------
// ARC B kundeservice (160–164): avsenderen, statusen som ord, plattformens
// utfall per utkast — flaten sender fortsatt ingenting.
// ---------------------------------------------------------------------

const AVSENDER = { avsender_navn: "Fjordlys Elektro AS",
  svar_til: "post@fjordlys.example", signatur: "Fjordlys Elektro AS",
  oppdatert: "2026-09-09T09:00:00+00:00" };

test("Kundeservice: uten avsenderprofil sies det høyt, med profil vises den",
  async () => {
    SVAR = fullSvar();
    let h = nyHoved();
    visKundeservice(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 2);
    assert.ok(h.textContent.includes(t("ui.kundeservice.avsender.ingen")));
    assert.ok(h.querySelector("#ks-avs-navn"), "avsenderskjemaet mangler");
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
    SVAR = { ...fullSvar(),
      "/v1/kundeservice": { ...KOEN, avsenderprofil: AVSENDER } };
    h = nyHoved();
    visKundeservice(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 2);
    assert.ok(h.textContent.includes(t("ui.kundeservice.avsender.navn")
      .replace("{navn}", "Fjordlys Elektro AS")));
    assert.ok(h.textContent.includes("post@fjordlys.example"));
    assert.equal(h.querySelector("#ks-avs-navn").value, "Fjordlys Elektro AS");
    h = nyHoved();
    visKundeservice(h, ctx(["decisions:read"]));
    await vent(() => h.querySelectorAll("table tbody tr").length === 2);
    assert.ok(!h.querySelector("#ks-avs-navn"));
  });

test("Kundeservice: avsenderskjemaet sender navn, svar-til og signatur",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKundeservice(h, ctx());
    await vent(() => !!h.querySelector("#ks-avs-navn"));
    h.querySelector("#ks-avs-navn").value = "Fjordlys Elektro AS";
    h.querySelector("#ks-avs-svar").value = "post@fjordlys.example";
    h.querySelector("#ks-avs-signatur").value = "Fjordlys";
    SISTE = null;
    h.querySelector("#ks-avs-navn").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE && SISTE.sti === "/v1/kundeservice/avsender");
    assert.deepEqual(SISTE.kropp, { avsender_navn: "Fjordlys Elektro AS",
      svar_til: "post@fjordlys.example", signatur: "Fjordlys" });
    assert.ok(SISTE.headers["Idempotency-Key"]);
  });

test("Kundeservice: statusen som ord og plattformens utfall per utkast",
  async () => {
    const utk = { ...UTKASTENE, utkast: [
      { ...UTKASTENE.utkast[0], status: "godkjent",
        bestilling: { utfall: "brudd", oppdrag_id: null, unntak_id: 12,
          bestilt_ts: "2026-09-09T08:00:00+00:00" } }] };
    SVAR = { ...fullSvar(),
      [`/v1/kundeservice/henvendelse/${H1}/utkast`]: utk };
    const h = nyHoved();
    visKundeservice(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 2);
    [...h.querySelectorAll("tbody button")].find(
      (b) => b.textContent === t("ui.kundeservice.knapp.apne")).click();
    await vent(() => h.textContent.includes(utk.utkast[0].tekst));
    assert.ok(h.textContent.includes(t("ui.kundeservice.utkaststatus.godkjent")));
    assert.ok(h.textContent.includes(
      t("ui.kundeservice.bestilling.utfall.brudd")));
    // Et godkjent utkast har ingen dommer igjen å klikke på.
    const dommer = [...h.querySelectorAll("button")]
      .filter((b) => b.textContent === t("ui.kundeservice.knapp.godkjenn"));
    assert.equal(dommer.length, 0);
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

// EN EKTE LEVERANDØR-ID, ikke fixturens `MSG-2026-0001`. Graphs
// melding-id-er er ~150 tegn base64; med tretten tegn i fixturen hadde
// porten aldri sett det som veltet køen på skjermen 15/9.
// Lengden er en del av fixturen og MÅLES under: første utkast limte
// sammen ~140 tegn og trodde det var «ekte»; skjermen hadde 152.
const GRAPH_ID = "AQMkADAwATM3ZmYBLWI5MWQtZjE3My0wMAItMDAKAEYAAAPy22ernEP6TLc"
  + "AmqUD2XICBwAniNzSc1IFQ53ctzagMWr5AAACAQwAAAAniNzSc1IFQ53ctzagMWr5AAAAB"
  + "WXdfQAAAAniNzSc1IFQ53ctzagMWr5AAAABWXdfQAAAA==";
const MASKE = "a****@accountprotection.microsoft.com";

test("Kundeservice: køen er lesbar med ekte id-er — avsender først, kort"
  + " referanse, hele verdien i title", async () => {
  assert.ok(GRAPH_ID.length > 140, "fixturen har ikke en ekte lengde");
  // Enhetene først: kort merke er hode + hale, korte verdier røres ikke.
  assert.equal(kortref("MSG-2026-0001"), "MSG-2026-0001");
  const k = kortref(GRAPH_ID);
  assert.ok(k.length < 24, `kortref ga ${k.length} tegn`);
  assert.ok(k.startsWith(GRAPH_ID.slice(0, 10)) && k.endsWith(GRAPH_ID.slice(-8)));
  assert.equal(avsenderTekst({ har_avsender: true, avsender_maske: MASKE }),
    MASKE);
  assert.equal(avsenderTekst({ har_avsender: false }),
    t("ui.kundeservice.avsender.ukjent"));

  SVAR = fullSvar();
  const koe = structuredClone(KOEN);
  koe.koe[0] = { ...koe.koe[0], ekstern_ref: GRAPH_ID,
                 har_avsender: true, avsender_maske: MASKE };
  koe.koe[1] = { ...koe.koe[1], har_avsender: false, avsender_maske: null };
  SVAR["/v1/kundeservice"] = koe;
  const h = nyHoved();
  visKundeservice(h, ctx());
  await vent(() => h.querySelectorAll("table tbody tr").length === 2);

  const rader = h.querySelectorAll("table tbody tr");
  // HVEM står først, som radens navn for skjermleseren.
  assert.equal(rader[0].cells[0].tagName, "TH");
  assert.equal(rader[0].cells[0].textContent, MASKE);
  assert.equal(rader[1].cells[0].textContent,
    t("ui.kundeservice.avsender.ukjent"));
  // Referansen er kort på skjermen og hel i title — aldri 150 tegn i
  // en celle. MUTASJONEN SOM DREPER DENNE: sett `h.ekstern_ref` tilbake
  // som celletekst.
  const ref = rader[0].cells[1];
  assert.ok(ref.textContent.length < 24,
    `referansecellen er ${ref.textContent.length} tegn`);
  assert.equal(ref.getAttribute("title"), GRAPH_ID);
  assert.equal(rader[1].cells[1].textContent, "MSG-2026-0002");
  // Kolonneoverskriften finnes, oversatt.
  assert.ok(h.textContent.includes(t("ui.kundeservice.kolonne.avsender")));

  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

// STILLE AVSENDERE (204): listen, skjemaet, fjerningen — og at hele settet
// sendes hver gang.
const REGLER = { regler: [
  { regel_id: "r-1", art: "domene", monster: "microsoft.com",
    handlingstype: "til_info", prioritet: "lav", tema: "annet",
    opprettet: "2026-09-15T17:40:00+00:00" },
  { regel_id: "r-2", art: "adresse", monster: "a".repeat(64),
    handlingstype: "nyhetsbrev", prioritet: "lav", tema: "annet",
    opprettet: "2026-09-15T17:41:00+00:00" },
], request_id: "r-s" };

test("Kundeservice: stille avsendere — liste, legg til sender HELE settet,"
  + " adressen vises bare som hash", async () => {
  assert.equal(regelTekst(REGLER.regler[0]),
    `microsoft.com → ${t("ui.kundeservice.handlingstype.til_info")}`);
  assert.ok(!regelTekst(REGLER.regler[1]).includes("a".repeat(20)),
    "en adresseregel viser hele hashen");
  SVAR = fullSvar();
  SVAR["/v1/kundeservice/stilleregler"] = REGLER;
  const h = nyHoved();
  visKundeservice(h, ctx());
  await vent(() => h.querySelectorAll(".stille-liste li").length === 2);
  const tekst = h.textContent;
  assert.ok(tekst.includes(t("ui.kundeservice.stille.tittel")));
  assert.ok(tekst.includes("microsoft.com"));

  h.querySelector("#ks-stille-art").value = "adresse";
  h.querySelector("#ks-stille-monster").value = "NoReply@Leverandor.no";
  h.querySelector("#ks-stille-handling").value = "nyhetsbrev";
  SISTE = null;
  h.querySelector("#ks-stille-monster").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti === "/v1/kundeservice/stilleregler");
  // HELE SETTET: de to som fantes + den nye. Adressen sendes som TEKST —
  // API-et hasher; den eksisterende adresseregelen går som hash.
  assert.deepEqual(SISTE.kropp, { regler: [
    { art: "domene", monster: "microsoft.com", handlingstype: "til_info" },
    { art: "adresse", monster: "a".repeat(64), handlingstype: "nyhetsbrev" },
    { art: "adresse", monster: "NoReply@Leverandor.no",
      handlingstype: "nyhetsbrev" },
  ] });
  assert.ok(SISTE.headers["Idempotency-Key"]);

  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Kundeservice: fjern-knappen sender settet UTEN regelen", async () => {
  SVAR = fullSvar();
  SVAR["/v1/kundeservice/stilleregler"] = REGLER;
  const h = nyHoved();
  visKundeservice(h, ctx());
  await vent(() => h.querySelectorAll(".stille-liste li button").length === 2);
  SISTE = null;
  h.querySelectorAll(".stille-liste li button")[0].click();
  await vent(() => SISTE && SISTE.sti === "/v1/kundeservice/stilleregler");
  assert.deepEqual(SISTE.kropp, { regler: [
    { art: "adresse", monster: "a".repeat(64), handlingstype: "nyhetsbrev" },
  ] });
});

test("Kundeservice: en lesende økt ser reglene, men verken skjema eller"
  + " fjern-knapp", async () => {
  SVAR = fullSvar();
  SVAR["/v1/kundeservice/stilleregler"] = REGLER;
  const h = nyHoved();
  visKundeservice(h, ctx(["decisions:read", "kundeservice:innhold"]));
  await vent(() => h.querySelectorAll(".stille-liste li").length === 2);
  assert.ok(!h.querySelector("#ks-stille-monster"));
  assert.equal(h.querySelectorAll(".stille-liste li button").length, 0);
});

test("Kundeservice: ingen skriving før regellisten er lastet — «[] + den"
  + " nye» skal aldri sendes", async () => {
  // GET-en for reglene henger til vi slipper den.
  let slipp;
  const henger = new Promise((r) => { slipp = r; });
  SVAR = fullSvar();
  const opprinnelig = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    if (url.split("?")[0] === "/v1/kundeservice/stilleregler"
        && !(opts && opts.method === "POST")) {
      await henger;
      return { ok: true, status: 200, json: async () => REGLER };
    }
    return opprinnelig(url, opts);
  };
  try {
    const h = nyHoved();
    visKundeservice(h, ctx());
    await vent(() => !!h.querySelector("#ks-stille-monster"));
    const knapp = h.querySelector("#ks-stille-monster").closest("form")
      .querySelector('button[type="submit"]');
    assert.ok(knapp.disabled, "knappen er åpen før listen er lastet");
    h.querySelector("#ks-stille-monster").value = "x.no";
    SISTE = null;
    h.querySelector("#ks-stille-monster").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => false, 10);
    assert.equal(SISTE, null, "en innsending gikk før listen var lastet");
    slipp();
    await vent(() => !knapp.disabled);
    assert.ok(!knapp.disabled, "knappen låses ikke opp etter lasting");
  } finally {
    globalThis.fetch = opprinnelig;
  }
});
