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
  alderTekst, klassifiseringTekst, visKundeservice,
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
  // det ETT kall gjort — og teksten finnes ikke på skjermen.
  assert.deepEqual(KALL.map((k) => k.sti), ["/v1/kundeservice"]);
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

test("Kundeservice: utkastets to dommer, og ingen av dem heter sendt",
  async () => {
    SVAR = fullSvar();
    SISTE = null;
    const h = nyHoved();
    visKundeservice(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 2);
    [...h.querySelectorAll("tbody button")].find(
      (b) => b.textContent === t("ui.kundeservice.knapp.apne")).click();
    await vent(() => h.textContent.includes(UTKASTENE.utkast[0].tekst));
    const dommer = [...h.querySelectorAll("button")]
      .map((b) => b.textContent)
      .filter((s) => s === t("ui.kundeservice.knapp.forkast")
                  || s === t("ui.kundeservice.knapp.brukt"));
    assert.deepEqual(dommer.sort(),
      [t("ui.kundeservice.knapp.brukt"),
        t("ui.kundeservice.knapp.forkast")].sort());
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
