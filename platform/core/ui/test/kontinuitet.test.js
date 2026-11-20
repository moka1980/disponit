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
  // Tre skjemaer: åpne hendelse, legg til post, lukk hendelse. Kart- og
  // kontaktskjemaene trenger katalogen og medlemslista, som ikke er
  // servert her — og da skal de IKKE stå der med tomme nedtrekkslister.
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

// ---------------------------------------------------------------------
// ADR-008: veien inn til tjenestekartet og kontaktene
// ---------------------------------------------------------------------
const REF_GJELDENDE = "tjeneste-nede@" + "b".repeat(64);
const KATALOG = { playbooks: [
  { navn: "leverandor-utfall", tittel: "Utfall hos en leverandør",
    naar: "En ekstern tjeneste er nede.", steg: ["Bekreft utfallet."],
    sha256: "c".repeat(64), ref: "leverandor-utfall@" + "c".repeat(64) },
  { navn: "tjeneste-nede", tittel: "Tjenesten er nede",
    naar: "Tjenesten svarer ikke.", steg: ["Bekreft feilen."],
    sha256: "b".repeat(64), ref: REF_GJELDENDE },
] };
const MEDLEMMER = { medlemmer: [
  { bruker_id: "bruker-1", navn: "Kari Vakt" },
  { bruker_id: "bruker-2", navn: null },
] };

function medKall(svar) {
  const kall = [];
  const forrige = globalThis.fetch;
  globalThis.fetch = async (url, valg = {}) => {
    const sti = url.split("?")[0];
    if (valg.method && valg.method !== "GET") {
      kall.push({ sti, kropp: JSON.parse(valg.body),
        idem: valg.headers["Idempotency-Key"] });
      const status = typeof svar === "function" ? svar(sti) : 200;
      return { ok: status < 400, status,
        json: async () => (status < 400 ? { ok: true } : { feil: "x" }) };
    }
    return forrige(url, valg);
  };
  return { kall, tilbake: () => { globalThis.fetch = forrige; } };
}

function skriv(felt, verdi) {
  felt.value = verdi;
  felt.dispatchEvent(new window.Event("input", { bubbles: true }));
}

function send(skjema) {
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
}

test("Kontinuitet: tomt kart gir skjemaet som fyller det — navn inn, sekunder ut",
  async () => {
    SVAR = {
      "/v1/kontinuitet": { siste_ovelse: null, tjenester: [], kontakter: [],
        hendelser: [], request_id: "r" },
      "/v1/kontinuitet/playbooks": KATALOG,
      "/v1/firma/medlemmer": MEDLEMMER,
    };
    const h = nyHoved();
    visKontinuitet(h, ctx(["kontinuitet:read", "kontinuitet:write"]));
    await vent(() => h.querySelector("#kart-ny-navn"));
    // Første gang: kartet er tomt, og skjemaet står rett under setningen
    // som sier det — ikke bak en knapp.
    assert.ok(h.textContent.includes(t("ui.kontinuitet.kart.ingen")));
    for (const felt of h.querySelectorAll("input, select, textarea")) {
      assert.ok(h.querySelector(`label[for="${felt.id}"]`),
        `ingen label for ${felt.id}`);
    }
    // Playbookens «når brukes den» står som tekst under velgeren.
    assert.ok(h.textContent.includes(KATALOG.playbooks[0].naar));

    const { kall, tilbake } = medKall();
    try {
      skriv(h.querySelector("#kart-ny-navn"), "  Regnskapssystemet ");
      skriv(h.querySelector("#kart-ny-tid"), "90");
      skriv(h.querySelector("#kart-ny-tap"), "30");
      h.querySelector("#kart-ny-pb").value = "tjeneste-nede";
      skriv(h.querySelector("#kart-ny-rolle"), "driftsvakt");
      send(h.querySelector("#kart-ny-navn").form);
      await vent(() => kall.length === 1);
    } finally { tilbake(); }
    assert.equal(kall[0].sti, "/v1/kontinuitet/tjenester");
    assert.deepEqual(kall[0].kropp, {
      kritikalitet: "viktig", rto_maal_s: 5400, rpo_maal_s: 1800,
      // NAVNET, aldri hashen: bindingen er serverens.
      playbook: "tjeneste-nede", kontaktrolle: "driftsvakt",
      referent_type: "ekstern", referent_id: "Regnskapssystemet" });
    assert.ok(kall[0].idem, "skrivingen gikk uten idempotensnøkkel");

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Kontinuitet: endring er forhåndsutfylt, og en utdatert playbook sies som ord",
  async () => {
    SVAR = {
      "/v1/kontinuitet": { ...FULLT, tjenester: [
        { ...FULLT.tjenester[0], playbook_ref: REF_GJELDENDE },
        { ...FULLT.tjenester[0],
          tjeneste_id: "66666666-6666-6666-6666-666666666666",
          referent_id: "regnskap", referent_type: "ekstern",
          playbook_ref: "tjeneste-nede@" + "d".repeat(64) },
        { ...FULLT.tjenester[0],
          tjeneste_id: "77777777-7777-7777-7777-777777777777",
          referent_id: "lager", playbook_ref: "borte@" + "e".repeat(64) },
      ] },
      "/v1/kontinuitet/playbooks": KATALOG,
      "/v1/firma/medlemmer": MEDLEMMER,
    };
    const h = nyHoved();
    visKontinuitet(h, ctx(["kontinuitet:read", "kontinuitet:write"]));
    await vent(() => h.querySelectorAll("details").length === 3);
    const rader = [...h.querySelectorAll("table")[0]
      .querySelectorAll("tbody tr")].map((r) => r.cells[4].textContent);
    assert.equal(rader[0], "tjeneste-nede");
    assert.ok(rader[1].includes(t("ui.kontinuitet.kart.playbook_utdatert")));
    assert.ok(rader[2].includes(t("ui.kontinuitet.kart.playbook_ukjent")));

    const id = FULLT.tjenester[0].tjeneste_id;
    // Identiteten er frosset: endringsskjemaet har ikke navnefeltet.
    assert.equal(h.querySelector(`#kart-${id}-navn`), null);
    assert.equal(h.querySelector(`#kart-${id}-tid`).value, "60");
    assert.equal(h.querySelector(`#kart-${id}-tap`).value, "1440");
    assert.equal(h.querySelector(`#kart-${id}-pb`).value, "tjeneste-nede");
    assert.equal(h.querySelector(`#kart-${id}-rolle`).value, "driftsvakt");

    const { kall, tilbake } = medKall();
    try {
      skriv(h.querySelector(`#kart-${id}-tid`), "120");
      send(h.querySelector(`#kart-${id}-tid`).form);
      await vent(() => kall.length === 1);
    } finally { tilbake(); }
    assert.equal(kall[0].sti, `/v1/kontinuitet/tjeneste/${id}`);
    assert.equal(kall[0].kropp.rto_maal_s, 7200);
    assert.ok(!("referent_id" in kall[0].kropp));

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Kontinuitet: kontakter velges blant medlemmene og bekreftes på raden",
  async () => {
    SVAR = { "/v1/kontinuitet": FULLT,
      "/v1/kontinuitet/playbooks": KATALOG,
      "/v1/firma/medlemmer": MEDLEMMER };
    const h = nyHoved();
    visKontinuitet(h, ctx(["kontinuitet:read", "kontinuitet:write"]));
    await vent(() => h.querySelector("#kontakt-person"));
    const tabell = h.querySelectorAll("table")[1];
    // Navnet vises der det finnes; en person uten navn vises med id-en
    // — aldri som en tom celle.
    assert.equal(tabell.querySelector("tbody tr").cells[2].textContent,
      "Kari Vakt");
    assert.equal(tabell.querySelectorAll("tbody tr")[1].cells[2].textContent,
      "bruker-2");
    const valg = [...h.querySelector("#kontakt-person").options]
      .map((o) => [o.value, o.textContent]);
    assert.deepEqual(valg, [["bruker-1", "Kari Vakt"], ["bruker-2", "bruker-2"]]);

    const knapper = [...tabell.querySelectorAll("tbody button")];
    assert.equal(knapper.length, 3, "hver rad skal kunne bekreftes");
    assert.equal(knapper[0].getAttribute("aria-label"),
      t("ui.kontinuitet.kontakter.bekreft_hvem")
        .replace("{rolle}", "driftsvakt").replace("{navn}", "Kari Vakt"));

    const { kall, tilbake } = medKall(
      (sti) => (sti === "/v1/kontinuitet/kontakter" ? 409 : 200));
    try {
      knapper[0].click();
      await vent(() => kall.length === 1);
      assert.equal(kall[0].sti,
        `/v1/kontinuitet/kontakt/${FULLT.kontakter[0].kontakt_id}/bekreft`);

      await vent(() => h.querySelector("#kontakt-rolle"));
      skriv(h.querySelector("#kontakt-rolle"), "driftsvakt");
      skriv(h.querySelector("#kontakt-prioritet"), "1");
      send(h.querySelector("#kontakt-rolle").form);
      await vent(() => h.textContent.includes(
        t("ui.kontinuitet.kontakter.opptatt")));
      // Plassen er tatt: svaret sier HVA som er galt, ikke «prøv igjen».
      assert.ok(h.textContent.includes(t("ui.kontinuitet.kontakter.opptatt")));
      const sist = kall[kall.length - 1];
      assert.deepEqual(sist.kropp,
        { rolle: "driftsvakt", prioritet: 1, bruker_id: "bruker-1" });
    } finally { tilbake(); }
  });

test("Kontinuitet: en leser får verken kartskjema, kontaktskjema eller bekreftknapp",
  async () => {
    SVAR = { "/v1/kontinuitet": FULLT,
      "/v1/kontinuitet/playbooks": KATALOG,
      "/v1/firma/medlemmer": MEDLEMMER };
    const h = nyHoved();
    visKontinuitet(h, ctx(["kontinuitet:read"]));
    await vent(() => h.querySelectorAll("table").length >= 2);
    assert.equal(h.querySelectorAll("form, details, tbody button").length, 0);
    assert.ok(!h.textContent.includes(t("ui.kontinuitet.kolonne.handling")));
  });
