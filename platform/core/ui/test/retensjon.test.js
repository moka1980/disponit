// M-4 (093) retensjonsflaten (jsdom + axe): axe uten alvorlige brudd,
// AVBRUTT som TEKST (den bærende regelen — en avbrutt kjøring skal kunne
// leses, ikke bare mangle et grønt merke), estimattall merket SOM TEKST
// (aldri farge eller kursiv alene), tabellsemantikk i begge retninger,
// funnlisten kun for platform:admin, `null` ≠ `[]`, og ingen
// mutasjonsknapper. Ingen delt fixture (m16-formen).
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visRetensjon } from "../static/js/flater/retensjon.js";

settI18nForTest(NB, "nb");

const LAGRE = [
  { lager_id: "epost_melding", relasjon: "epost_melding",
    klasse: "persondata", tenantkolonne: "tenant",
    alderskolonne: "mottatt_ts", reapetkolonne: "slettet_ts",
    fristkilde: "epost_melding.slettefrist_dogn", frist_dogn: 90,
    reaper: "reap_epostdata", dom: "under_frist",
    dom_begrunnelse: "Reapes av 088 når fristen er passert.",
    dom_migrasjon: "093", rader: 40, rader_ureapet: 7,
    eldste_ureapet_ts: "2026-01-02T03:04:05+00:00",
    sist_reapet_ts: "2026-08-01T00:00:00+00:00" },
  { lager_id: "policyer", relasjon: "policyer", klasse: "konfigurasjon",
    tenantkolonne: "tenant", alderskolonne: "opprettet",
    reapetkolonne: null, fristkilde: null, frist_dogn: null,
    reaper: null, dom: "uten_frist_akseptert",
    dom_begrunnelse: "Slettes av operatøren, aldri av tiden.",
    dom_migrasjon: "093", rader: null, rader_ureapet: null,
    eldste_ureapet_ts: null, sist_reapet_ts: null },
];

const KATALOG = [
  { lager_id: "epost_melding", bytes_totalt: 81920, rader_estimat: 41,
    tenant: "acme", rader: 40, rader_ureapet: 7,
    eldste_ureapet_ts: "2026-01-02T03:04:05+00:00",
    sist_reapet_ts: null },
];

const FUNN = [
  { funn_id: "f1", lager_id: "uregistrert:varsel", relasjon: "varsel",
    tenant: "", funntype: "uregistrert",
    oppdaget_ts: "2026-08-30T01:00:00+00:00",
    oppdaget_maaling: "m1", sist_sett_maaling: "m1", detalj: {} },
];

function svar({ avbrutt = false, plattformdrift = false } = {}) {
  return {
    plattformdrift,
    maaling: { maaling_id: "m1", startet_ts: "2026-08-31T03:17:00+00:00",
      fullfort_ts: avbrutt ? null : "2026-08-31T03:19:00+00:00",
      avbrutt, antall_lagre: 18, antall_umaalbare: 0, antall_funn: 99 },
    lagre: LAGRE,
    katalog: plattformdrift ? KATALOG : null,
    funn: plattformdrift ? FUNN : null,
    request_id: "r-test",
  };
}

let SVAR;
globalThis.fetch = async (url) => {
  const sti = url.split("?")[0];
  const oppf = SVAR[sti];
  if (!oppf) {
    return { ok: false, status: 404,
      json: async () => ({ feil: "ikke_funnet" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx(scopes = ["security:read"]) {
  return { sprak: "nb", scopes, tenant: "acme", paaUautorisert: () => {} };
}

async function vent(pred, n = 60) {
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
  return m;
}

test("Retensjon: register, dom som tekst, tabellsemantikk, axe rent",
  async () => {
    SVAR = { "/v1/retensjon": svar() };
    const h = nyHoved();
    visRetensjon(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 1);

    const tb = h.querySelector("table");
    assert.ok(tb.querySelector("caption").textContent.length > 0);
    assert.ok(tb.querySelector('th[scope="col"]'));
    for (const rad of tb.querySelectorAll("tbody tr")) {
      // Lagernavnet er RADENS navn — uten scope="row" mister en
      // skjermleser i tallkolonnene hvilket lager tallet gjelder.
      assert.equal(rad.cells[0].tagName, "TH");
      assert.equal(rad.cells[0].getAttribute("scope"), "row");
    }

    // Dommen står som TEKST, begge variantene med hver sin setning.
    const tekst = tb.textContent;
    assert.ok(tekst.includes(t("ui.retensjon.dom.under_frist")));
    assert.ok(tekst.includes(t("ui.retensjon.dom.uten_frist_akseptert")));
    // Begrunnelsen følger dommen — en dom uten begrunnelse er en påstand.
    assert.ok(tekst.includes("Slettes av operatøren, aldri av tiden."));
    // Et lager uten reap-markør er «ikke talt», ALDRI en tom celle som
    // kan leses som null.
    assert.ok(tekst.includes(t("ui.retensjon.ikke_talt")));
    assert.ok(tekst.includes("7 / 40"));

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Retensjon: en AVBRUTT kjøring SIER det med tekst", async () => {
  SVAR = { "/v1/retensjon": svar({ avbrutt: true }) };
  const h = nyHoved();
  visRetensjon(h, ctx());
  await vent(() => h.textContent.includes(t("ui.retensjon.avbrutt_ja")));
  assert.ok(h.textContent.includes(t("ui.retensjon.avbrutt_ja")),
    "en avbrutt kjøring må stå som en setning, ikke som et manglende merke");
  assert.ok(!h.textContent.includes(t("ui.retensjon.avbrutt_nei")));
  assert.ok(h.textContent.includes(t("ui.retensjon.ikke_fullfort")));
});

test("Retensjon: fullført kjøring sier DET, med sin egen setning",
  async () => {
    SVAR = { "/v1/retensjon": svar() };
    const h = nyHoved();
    visRetensjon(h, ctx());
    await vent(() => h.textContent.includes(t("ui.retensjon.avbrutt_nei")));
    assert.ok(!h.textContent.includes(t("ui.retensjon.avbrutt_ja")));
  });

test("Retensjon: security:read ser verken funnliste eller katalogtall",
  async () => {
    SVAR = { "/v1/retensjon": svar() };
    const h = nyHoved();
    visRetensjon(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 1);
    assert.equal(h.querySelectorAll("table").length, 1,
      "kun lagertabellen skal stå for en økt uten platform:admin");
    assert.ok(!h.textContent.includes(t("ui.retensjon.funn_caption")));
    assert.ok(!h.textContent.includes(t("ui.retensjon.estimat_merke")));
  });

test("Retensjon: platform:admin ser funn og estimattall MERKET som tekst",
  async () => {
    SVAR = { "/v1/retensjon":
      svar({ plattformdrift: true }) };
    const h = nyHoved();
    visRetensjon(h, ctx(["security:read", "platform:admin"]));
    await vent(() => h.querySelectorAll("table").length >= 3);
    const tekst = h.textContent;
    // ESTIMATET ER MERKET SOM TEKST — ikke farge, ikke kursiv alene.
    assert.ok(tekst.includes(t("ui.retensjon.estimat_merke")),
      "radestimatet må stå merket som estimat i TEKST");
    assert.ok(tekst.includes("41"));
    // Funntypen står som tekst, aldri som et trafikklys alene.
    assert.ok(tekst.includes(t("ui.retensjon.funntype.uregistrert")));
    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Retensjon: flaten muterer ingenting — ingen knapp utenom sortering",
  async () => {
    SVAR = { "/v1/retensjon": svar({ plattformdrift: true }) };
    const h = nyHoved();
    visRetensjon(h, ctx(["security:read", "platform:admin"]));
    await vent(() => h.querySelectorAll("table").length >= 3);
    for (const b of h.querySelectorAll("button")) {
      assert.ok(b.classList.contains("sort-knapp"),
        `flaten har en knapp som ikke er sortering: «${b.textContent}»`);
    }
  });

test("Retensjon: sortering setter aria-sort og beholder tastaturfokus",
  async () => {
    SVAR = { "/v1/retensjon": svar() };
    const h = nyHoved();
    visRetensjon(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 1);
    const th = h.querySelectorAll('thead th[aria-sort]');
    assert.ok(th.length >= 4, "de sorterbare kolonnene mangler aria-sort");
    const klasseTh = [...th].find((e) =>
      e.textContent.includes(t("ui.retensjon.kolonne.klasse")));
    const knapp = klasseTh.querySelector("button.sort-knapp");
    knapp.focus();
    knapp.click();
    assert.equal(klasseTh.getAttribute("aria-sort"), "ascending");
    assert.equal(document.activeElement, knapp,
      "thead ble bygget på nytt og kastet tastaturfokus");
    knapp.click();
    assert.equal(klasseTh.getAttribute("aria-sort"), "descending");
  });

test("Retensjon: tomtilstand og manglende måling er eksplisitt innhold",
  async () => {
    SVAR = { "/v1/retensjon": { plattformdrift: false, maaling: null,
      lagre: [], katalog: null, funn: null, request_id: "r" } };
    const h = nyHoved();
    visRetensjon(h, ctx());
    await vent(() =>
      h.textContent.includes(t("ui.retensjon.maaling_ingen")));
    assert.ok(h.textContent.includes(t("ui.retensjon.maaling_ingen")));
    assert.ok(h.textContent.includes(t("ui.retensjon.ingen_lagre")));
  });

test("Retensjon: tom funnliste er IKKE det samme som ingen tilgang",
  async () => {
    const d = svar({ plattformdrift: true });
    d.funn = [];
    SVAR = { "/v1/retensjon": d };
    const h = nyHoved();
    visRetensjon(h, ctx(["security:read", "platform:admin"]));
    await vent(() => h.textContent.includes(t("ui.retensjon.funn_ingen")));
    assert.ok(h.textContent.includes(t("ui.retensjon.funn_ingen")));
  });

test("Retensjon: ingen hardkodet tekst (pseudo-locale)", async () => {
  const PL = Object.fromEntries(Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = { "/v1/retensjon": svar({ plattformdrift: true }) };
    const h = nyHoved();
    visRetensjon(h, ctx(["security:read", "platform:admin"]));
    await vent(() => h.querySelectorAll("table").length >= 3);
    const tekst = h.textContent;
    for (const ekte of ["Under frist", "Bevisst uten frist", "Retensjon",
                        "Siste måling", "(estimat)"]) {
      assert.ok(!tekst.includes(ekte),
        `hardkodet norsk tekst i flaten: «${ekte}»`);
    }
    assert.ok(tekst.includes("PL_ui.retensjon.tittel"));
  } finally {
    settI18nForTest(NB, "nb");
  }
});
