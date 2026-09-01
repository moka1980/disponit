// M-35 kontinuitetsflaten (089) — jsdom + axe. Portene her måler det
// dommene 4 og 5 handler om: at flaten NAVNGIR tallene ærlig, at
// fraværet av evidens leses som fravær og ikke som en nullmåling, og at
// skriveveiene er bak write-scopet uten at flaten later som den kan
// felle dørens dom selv.
//
// Ingen delt fixture (m16-formen): hver test bygger sin egen tilstand.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visKontinuitet } from "../static/js/flater/kontinuitet.js";

settI18nForTest(NB, "nb");

const NAA = Date.now();
const DOGN = 86400000;

const FULLT = {
  siste_ovelse: {
    maalt_restoretid_s: 12.5,
    maalt_backupalder_s: 3600,
    restore_verifisert: true,
    live_helse_ok: true,
    siste_gronne_alder_dogn: 4,
    funn: [],
  },
  tjenester: [{
    tjeneste_id: "11111111-1111-1111-1111-111111111111",
    referent_type: "systemd_unit", referent_id: "disponit-api.service",
    kritikalitet: "kritisk", rto_maal_s: 3600, rpo_maal_s: 86400,
    playbook_ref: "gjenopprett-api@" + "a".repeat(64),
    kontaktrolle: "driftsvakt",
    oppdatert: "2026-08-30T10:00:00+00:00", oppdatert_av: "eier",
  }],
  kontakter: [
    { kontakt_id: "22222222-2222-2222-2222-222222222222",
      rolle: "driftsvakt", prioritet: 1, bruker_id: "bruker-1",
      bekreftet: new Date(NAA - 10 * DOGN).toISOString(),
      bekreftet_av: "eier" },
    { kontakt_id: "33333333-3333-3333-3333-333333333333",
      rolle: "kommunikasjon", prioritet: 1, bruker_id: "bruker-2",
      bekreftet: new Date(NAA - 200 * DOGN).toISOString(),
      bekreftet_av: "eier" },
    { kontakt_id: "44444444-4444-4444-4444-444444444444",
      rolle: "juridisk", prioritet: 2, bruker_id: "bruker-3",
      bekreftet: null, bekreftet_av: null },
  ],
  hendelser: [{
    hendelse_id: "55555555-5555-5555-5555-555555555555",
    tekstnokkel: "strombrudd.datasenter", parametre: {},
    alvor: "kritisk", apnet: "2026-08-31T08:00:00+00:00",
    apnet_av: "vakt", lukket: null, lukket_av: null,
    tidslinje: [
      { post_id: "p1", posttype: "opprettet",
        ts: "2026-08-31T08:00:00+00:00", aktor: "vakt",
        tekst: "strombrudd.datasenter" },
      { post_id: "p2", posttype: "tiltak",
        ts: "2026-08-31T08:05:00+00:00", aktor: "vakt",
        tekst: "Startet reserveaggregat" },
    ],
  }],
  request_id: "r-test",
};

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

function ctx(scopes = ["kontinuitet:read"]) {
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

test("Kontinuitet: fire seksjoner, ærlige tallnavn, tabellsemantikk, axe rent",
  async () => {
    SVAR = { "/v1/kontinuitet": FULLT };
    const h = nyHoved();
    visKontinuitet(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);

    // Fire seksjoner, hver med sin h2 — flaten er ett dokument, ikke
    // fire løsrevne lister.
    const h2 = [...h.querySelectorAll("h2")].map((n) => n.textContent);
    for (const nokkel of ["ui.kontinuitet.ovelse.tittel",
                          "ui.kontinuitet.kart.tittel",
                          "ui.kontinuitet.kontakter.tittel",
                          "ui.kontinuitet.hendelser.tittel"]) {
      assert.ok(h2.includes(t(nokkel)), `seksjonen ${nokkel} mangler`);
    }

    // DOM 5, PORTENS KJERNE: tallene bærer sine ÆRLIGE navn. «Målt
    // restore-tid» og «målt backupalder» — og ordene «RTO»/«RPO» skal
    // ikke stå noe sted i flatens tekst, fordi det målte ikke ER dem.
    const tekst = h.textContent;
    assert.ok(tekst.includes(t("ui.kontinuitet.ovelse.restoretid")));
    assert.ok(tekst.includes(t("ui.kontinuitet.ovelse.backupalder")));
    assert.ok(!/\bRTO\b/.test(tekst),
      "flaten sier «RTO» om et tall som er en restore-til-isolert-base-proxy");
    assert.ok(!/\bRPO\b/.test(tekst),
      "flaten sier «RPO» om et tall som er en målt backupalder");
    // …og proxy-forbeholdet står som synlig tekst, ikke som en tooltip.
    assert.ok(tekst.includes(t("ui.kontinuitet.ovelse.proxynotat")));

    // Tabellsemantikk i BEGGE tabellene: caption + th scope begge veier.
    for (const tb of h.querySelectorAll("table")) {
      assert.ok(tb.querySelector("caption"), "tabell uten caption");
      assert.ok(tb.querySelector('th[scope="col"]'));
      for (const rad of tb.querySelectorAll("tbody tr")) {
        assert.equal(rad.cells[0].tagName, "TH");
        assert.equal(rad.cells[0].getAttribute("scope"), "row");
      }
    }

    // Playbook: navnet lesbart, hele navn@sha256 i title — kortformen
    // er presentasjon, aldri en identitet flaten regner videre på.
    const kode = h.querySelector("code");
    assert.equal(kode.getAttribute("title"), FULLT.tjenester[0].playbook_ref);
    assert.equal(kode.textContent, "gjenopprett-api");

    // Kontaktdekningen som TRE ULIKE ORD, aldri som farge alene: fersk,
    // foreldet og aldri bekreftet er tre forskjellige hull.
    assert.ok(tekst.includes(t("ui.kontinuitet.kontakter.fersk")));
    assert.ok(tekst.includes(t("ui.kontinuitet.kontakter.foreldet")),
      "en 200 døgn gammel bekreftelse leses ikke som foreldet");
    assert.ok(tekst.includes(t("ui.kontinuitet.kontakter.ubekreftet")));

    // Tidslinjen er en ordnet liste — rekkefølgen ER informasjonen.
    const ol = h.querySelector("ol.tidslinje");
    assert.equal(ol.querySelectorAll("li").length, 2);
    assert.ok(ol.textContent.includes(t("ui.kontinuitet.posttype.tiltak")));

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Kontinuitet: ingen øvelse er en SETNING, ikke et nulltall", async () => {
  // Dom 4, i flaten: fraværet av evidens skal leses som fravær. Et
  // «0 s» ville lest som en måling, og en beredskapsside som viser
  // «restore-tid 0 s» sier det motsatte av sannheten.
  SVAR = { "/v1/kontinuitet": {
    siste_ovelse: null, tjenester: [], kontakter: [], hendelser: [],
    request_id: "r" } };
  const h = nyHoved();
  visKontinuitet(h, ctx());
  await vent(() => h.textContent.includes(t("ui.kontinuitet.ovelse.ingen")));
  const tekst = h.textContent;
  assert.ok(tekst.includes(t("ui.kontinuitet.ovelse.ingen")));
  assert.ok(!tekst.includes(t("ui.kontinuitet.ovelse.restoretid")),
    "restore-tid-feltet vises uten at noen øvelse finnes");
  // Alle fire tomtilstandene er eksplisitt innhold, ikke bare fravær.
  assert.ok(tekst.includes(t("ui.kontinuitet.kart.ingen")));
  assert.ok(tekst.includes(t("ui.kontinuitet.kontakter.ingen")));
  assert.ok(tekst.includes(t("ui.kontinuitet.hendelser.ingen")));

  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Kontinuitet: øvelse uten evidens viser «ingen evidens», ikke 0",
  async () => {
    SVAR = { "/v1/kontinuitet": {
      siste_ovelse: {
        maalt_restoretid_s: null, maalt_backupalder_s: null,
        restore_verifisert: false, live_helse_ok: true,
        siste_gronne_alder_dogn: null,
        funn: [{ alvor: "rodt",
          tekstnokkel: "kontinuitet.funn.statusfil_mangler" }],
      },
      tjenester: [], kontakter: [], hendelser: [], request_id: "r" } };
    const h = nyHoved();
    visKontinuitet(h, ctx());
    await vent(() => h.textContent
      .includes(t("ui.kontinuitet.ovelse.uten_evidens")));
    const tekst = h.textContent;
    assert.ok(tekst.includes(t("ui.kontinuitet.ovelse.uten_evidens")));
    assert.ok(!/\b0 s\b/.test(tekst),
      "manglende måling rendres som «0 s» — det leses som en måling");
    // Funnet er en TEKSTNØKKEL som oversettes, aldri rå fritekst.
    assert.ok(tekst.includes(t("ui.kontinuitet.funn.statusfil_mangler")));
    assert.ok(!tekst.includes("kontinuitet.funn.statusfil_mangler"),
      "funnets rå tekstnøkkel lekker ut i grensesnittet");
  });

test("Kontinuitet: skjemaene finnes bare med write-scopet", async () => {
  // Skjulingen er ERGONOMI, ikke sikkerhet — dørene i basen er den
  // bindende porten. Men en knapp som alltid ville feilet er en løgn om
  // hva systemet kan, så den skal ikke stå der.
  SVAR = { "/v1/kontinuitet": FULLT };
  const lesende = nyHoved();
  visKontinuitet(lesende, ctx(["kontinuitet:read"]));
  await vent(() => lesende.querySelectorAll("table").length >= 2);
  assert.equal(lesende.querySelectorAll("form").length, 0,
    "en ren leser får skriveskjemaer den aldri kan bruke");

  const skrivende = nyHoved();
  visKontinuitet(skrivende,
    ctx(["kontinuitet:read", "kontinuitet:write"]));
  await vent(() => skrivende.querySelectorAll("form").length >= 3);
  // Tre skjemaer: åpne hendelse, legg til post, lukk hendelse.
  assert.equal(skrivende.querySelectorAll("form").length, 3);
  const tekst = skrivende.textContent;
  // Etteranalyse-kravet står som SETNING ved lukkeknappen — mennesket
  // skal vite hvorfor en lukking blir avvist FØR den avvises.
  assert.ok(tekst.includes(t("ui.kontinuitet.skjema.lukk_krav")));
  // Hver kontroll har en label knyttet til seg (axe fanger resten).
  for (const felt of skrivende.querySelectorAll("input, select, textarea")) {
    assert.ok(felt.id, "skjemakontroll uten id kan ikke ha label");
    assert.ok(skrivende.querySelector(`label[for="${felt.id}"]`),
      `ingen label for ${felt.id}`);
  }
  const brudd = await alvorligeBrudd(skrivende, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Kontinuitet: en LUKKET hendelse får ingen skriveskjemaer", async () => {
  // Tidslinjen er lukket når hendelsen er det (vakten avviser posten
  // uansett) — en flate som tilbød feltet ville lovet noe basen nekter.
  SVAR = { "/v1/kontinuitet": {
    ...FULLT,
    hendelser: [{ ...FULLT.hendelser[0],
      lukket: "2026-08-31T12:00:00+00:00", lukket_av: "eier" }] } };
  const h = nyHoved();
  visKontinuitet(h, ctx(["kontinuitet:read", "kontinuitet:write"]));
  await vent(() => h.querySelectorAll("form").length >= 1);
  // Bare «åpne ny hendelse» står igjen.
  assert.equal(h.querySelectorAll("form").length, 1);
  assert.ok(h.textContent.includes(t("ui.kontinuitet.hendelse.lukket")));
  assert.ok(h.textContent.includes(t("ui.kontinuitet.hendelse.lukket_av")));
});

test("Kontinuitet: ingen hardkodet tekst (pseudo-locale)", async () => {
  const PL = Object.fromEntries(Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = { "/v1/kontinuitet": FULLT };
    const h = nyHoved();
    visKontinuitet(h, ctx(["kontinuitet:read", "kontinuitet:write"]));
    await vent(() => h.querySelectorAll("table").length >= 2);
    const tekst = h.textContent;
    for (const ekte of ["Målt restore-tid", "Målt backupalder", "Åpen",
                        "Tidslinje", "Kontinuitet og beredskap",
                        "Beredskapskontakter"]) {
      assert.ok(!tekst.includes(ekte),
        `hardkodet norsk tekst i flaten: «${ekte}»`);
    }
    assert.ok(tekst.includes("PL_ui.kontinuitet.tittel"));
    // Kundens egen tekstnøkkel er DATA og skal stå urørt gjennom
    // pseudo-locale — reserven, ikke en «PL_»-streng.
    assert.ok(tekst.includes("strombrudd.datasenter"));
  } finally {
    settI18nForTest(NB, "nb");
  }
});
