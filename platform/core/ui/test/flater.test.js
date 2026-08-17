// Flate-integrasjon mot mocket API (jsdom + axe). Dekker de fem tilstandene,
// kontraktsformene (V1) og de skarpe portene: ukjent art → Feiltilstand
// (gate 9), sikkerhet KUN når feltet finnes (gate 10), 401→innlogging vs
// 403→ingen-tilgang (V2), TILLAT som «Tillatt» aldri «utført» (gate 11).
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visOversikt } from "../static/js/flater/oversikt.js";
import { visPolicy } from "../static/js/flater/policy.js";
import { visBeslutninger } from "../static/js/flater/beslutninger.js";
import { visUnntak } from "../static/js/flater/unntak.js";

settI18nForTest(NB, "nb");

// --- Mockbart API ----------------------------------------------------------
const STD = {
  "/v1/oversikt": { vindu_start: "2026-08-08T10:00:00+00:00",
    vindu_slutt: "2026-08-09T10:00:00+00:00", tidssone: "UTC",
    tillatt: 128, stoppet: 4, unntak: 3, totalt: 135 },
  "/v1/policy/aktiv": { skjemaversjon: 1, policy_id: "p", versjon: "0.2.0",
    innholds_hash: "a".repeat(64),
    roller: [{ id: "admin", beskrivelse_kode: "rolle.admin" }],
    handlinger: [{ navn: "utbetaling", modus: "auto_med_vilkaar",
      grenser: { belop_maks: "1000.00", valuta: ["NOK"],
        tidsvindu: { ukedager: [0, 1, 2, 3, 4], fra: "08:00", til: "16:00",
          tidssone: "Europe/Oslo" },
        frekvens: { maks: 5, vindu_enhet: "dager", vindu_antall: 1 } },
      vilkaar: ["fire_oyne"] }],
    verifikatorer: [{ offentlig_id: "v1", betrodd_for: ["belop"],
      kan_fastsla_permanent: true }] },
  "/v1/beslutninger": { rader: [{ id: 1, ts: "2026-08-09T09:00:00+00:00",
    handling: "utbetaling", policybeslutning: "TILLAT",
    begrunnelse: ["belop_over_grense"] }], neste_cursor: null },
  "/v1/beslutninger/1": { id: 1, handling: "utbetaling",
    begrunnelse: ["belop_over_grense"], policy_versjon: "0.2.0",
    policy_hash: "x", beslutning_ts: "2026-08-09T09:00:00+00:00",
    resultat: { art: "sideeffektfri_tillatt" }, evidensstatus: "IKKE_RELEVANT",
    sen_evidens: false, konflikt_evidens: false },
  "/v1/unntak": { saker: [{ id: 1, ts: "2026-08-09T09:00:00+00:00",
    handling: "utbetaling", kategori: "over_grense", prioritet: "hoy",
    status: "ny", sakstype: "normal" }], neste_cursor: null },
  "/v1/unntak/1": { id: 1, ts: "2026-08-09T09:00:00+00:00",
    handling: "utbetaling", kategori: "over_grense", sakstype: "normal",
    status: "ny", prioritet: "hoy", begrunnelse: ["belop_over_grense"] },
  "/v1/unntak/1/historikk": { rader: [{ id: 1, hendelse: "opprettet",
    fra_status: null, til_status: "ny", ts: "2026-08-09T09:00:00+00:00" }],
    neste_cursor: null },
};

let SVAR;
globalThis.fetch = async (url) => {
  const sti = url.split("?")[0];
  const oppf = SVAR[sti];
  if (!oppf) return { ok: false, status: 404, json: async () => ({ feil: "ikke_funnet" }) };
  if (typeof oppf === "number") {
    return { ok: false, status: oppf, json: async () => ({ feil: "x" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx(overstyr = {}) {
  return { sprak: "nb", scopes: [], tenant: "acme",
    paaUautorisert: () => { ctx._ua = true; }, ...overstyr };
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

test("Oversikt: kort med telling, axe rent", async () => {
  SVAR = STD;
  const h = nyHoved();
  visOversikt(h, ctx());
  await vent(() => h.querySelector(".cards"));
  assert.ok(h.textContent.includes("128"));
  assert.ok(h.textContent.includes(t("ui.oversikt.telling_note")));
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

test("Policy: versjon, roller, handling med beløpsgrense", async () => {
  SVAR = STD;
  const h = nyHoved();
  visPolicy(h, ctx());
  await vent(() => h.querySelector(".policy-sec"));
  assert.ok(h.textContent.includes("0.2.0"));
  assert.ok(h.textContent.includes("admin"));
  assert.ok(h.textContent.includes("1000.00"));
  assert.ok(h.textContent.includes(t("modus.auto_med_vilkaar")));
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

test("Policy: 404 → tom-tilstand, ikke feil", async () => {
  SVAR = { "/v1/policy/aktiv": 404 };
  const h = nyHoved();
  visPolicy(h, ctx());
  await vent(() => h.querySelector(".tom"));
  assert.ok(h.querySelector(".tilstand.tom"));
});

test("Beslutninger: tabell + badge + Åpne → detalj uten sikkerhetsrad", async () => {
  SVAR = STD;
  const h = nyHoved();
  visBeslutninger(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  // TILLAT vises som «Tillatt» (aldri «utført») — gate 11
  assert.ok(h.textContent.includes(t("beslutning.TILLAT")));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => document.querySelector('[role="dialog"]'));
  const dlg = document.querySelector('[role="dialog"]');
  assert.ok(dlg.textContent.includes(t("art.sideeffektfri_tillatt")));
  // sikkerhet-feltet fantes ikke → INGEN sikkerhetsrad (gate 10)
  assert.ok(!dlg.textContent.includes(t("ui.detalj.sak_finnes")));
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

test("Beslutningsdetalj: ukjent art → Feiltilstand (gate 9)", async () => {
  SVAR = { ...STD, "/v1/beslutninger/1": { id: 1, handling: "x",
    begrunnelse: [], resultat: { art: "helt_ny_variant_motoren_fant_paa" },
    evidensstatus: "IKKE_RELEVANT", sen_evidens: false, konflikt_evidens: false } };
  const h = nyHoved();
  visBeslutninger(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => document.querySelector('[role="dialog"]'));
  const dlg = document.querySelector('[role="dialog"]');
  assert.ok(dlg.textContent.includes(t("ui.detalj.ukjent_tittel")));
  assert.ok(!dlg.textContent.includes("helt_ny_variant"), "råart skal ikke vises");
});

test("Beslutningsdetalj: sikkerhet vises NÅR feltet finnes (gate 10)", async () => {
  SVAR = { ...STD, "/v1/beslutninger/1": { ...STD["/v1/beslutninger/1"],
    sikkerhet: { sak_finnes: true } } };
  const h = nyHoved();
  visBeslutninger(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => document.querySelector('[role="dialog"]'));
  const dlg = document.querySelector('[role="dialog"]');
  assert.ok(dlg.textContent.includes(t("ui.detalj.sak_finnes")));
  assert.ok(dlg.textContent.includes(t("ui.detalj.sak_ja")));
});

test("Unntak: liste + detalj med begrunnelse og historikk", async () => {
  SVAR = STD;
  const h = nyHoved();
  visUnntak(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  assert.ok(h.textContent.includes(t("unntak.over_grense")));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => document.querySelector('[role="dialog"]'));
  const dlg = document.querySelector('[role="dialog"]');
  assert.ok(dlg.textContent.includes(t("ui.unntak.historikk")));
  assert.ok(dlg.textContent.includes(t("hendelse.opprettet")));
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

test("Unntak: behandlingsknapper + godkjenn-bekreftelse + CSRF-POST (PR-012)", async () => {
  const kalt = [];
  const ekte = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    if (opts && opts.method === "POST") {
      kalt.push({ url, opts });
      return { ok: true, status: 200,
        json: async () => ({ utfall: "TILLAT", unntak_id: 1 }) };
    }
    return ekte(url, opts);
  };
  // jsdom dropper __Host--cookies (krever Secure); overstyr getteren så
  // lesCookie ser den (klientens jobb er kun å videresende tokenet).
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  SVAR = { ...STD, "/v1/unntak/1": { id: 1, ts: "2026-08-09T09:00:00+00:00",
    handling: "utbetaling", kategori: "over_grense", sakstype: "normal",
    status: "manuell", prioritet: "hoy", begrunnelse: ["belop_over_grense"],
    saksversjon: 0, tillatte_handlinger: ["godkjenn", "avvis", "eskaler"] } };
  const h = nyHoved();
  visUnntak(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => document.querySelector('[role="dialog"]'));
  const dlg = document.querySelector('[role="dialog"]');
  assert.ok(dlg.textContent.includes(t("ui.unntak.behandle")));
  const finn = (rot, tekst) => [...rot.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === tekst);
  assert.ok(finn(dlg, t("ui.unntak.handling.godkjenn")), "godkjenn-knapp mangler");
  assert.equal((await alvorligeBrudd(dlg, { fragment: true })).length, 0);

  // Godkjenn → bekreftelse med KONKRET grunn i klartekst (v8 §3).
  finn(dlg, t("ui.unntak.handling.godkjenn"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => [...document.querySelectorAll('[role="dialog"]')]
    .some((d) => d.textContent.includes(t("ui.unntak.du_godkjenner"))));
  const bek = [...document.querySelectorAll('[role="dialog"]')]
    .find((d) => d.textContent.includes(t("ui.unntak.du_godkjenner")));
  assert.ok(bek.textContent.includes(t("grunn.belop_over_grense")));

  // Bekreft → POST med X-Disponit-CSRF (dobbel-innsending).
  finn(bek, t("ui.unntak.handling.godkjenn"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => kalt.length > 0);
  assert.equal(kalt[0].opts.method, "POST");
  assert.ok(kalt[0].url.includes("/v1/unntak/1/handling"));
  assert.equal(kalt[0].opts.headers["X-Disponit-CSRF"], "tok123");
  assert.ok(kalt[0].opts.headers["Idempotency-Key"], "mangler Idempotency-Key");
  const sendt = JSON.parse(kalt[0].opts.body);
  assert.equal(sendt.operatorhandling, "godkjenn");
  assert.equal(sendt.saksversjon, 0);   // versjonen dialogen viste
  globalThis.fetch = ekte;
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
});

test("Unntak: nettverksretry GJENBRUKER samme Idempotency-Key (PR-012)", async () => {
  const kalt = [];
  const ekte = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    if (opts && opts.method === "POST") {
      kalt.push({ url, opts });
      if (kalt.length === 1) throw new TypeError("network");  // nettverksfeil
      return { ok: true, status: 200,
        json: async () => ({ utfall: "TILLAT", unntak_id: 1 }) };
    }
    return ekte(url, opts);
  };
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  SVAR = { ...STD, "/v1/unntak/1": { id: 1, ts: "2026-08-09T09:00:00+00:00",
    handling: "utbetaling", kategori: "over_grense", sakstype: "normal",
    status: "manuell", prioritet: "hoy", begrunnelse: ["belop_over_grense"],
    saksversjon: 3, tillatte_handlinger: ["godkjenn", "avvis", "eskaler"] } };
  const h = nyHoved();
  visUnntak(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => document.querySelector('[role="dialog"]'));
  const dlg = document.querySelector('[role="dialog"]');
  const finn = (rot, tekst) => [...rot.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === tekst);
  finn(dlg, t("ui.unntak.handling.godkjenn"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => [...document.querySelectorAll('[role="dialog"]')]
    .some((d) => d.textContent.includes(t("ui.unntak.du_godkjenner"))));
  const bek = [...document.querySelectorAll('[role="dialog"]')]
    .find((d) => d.textContent.includes(t("ui.unntak.du_godkjenner")));
  finn(bek, t("ui.unntak.handling.godkjenn"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => kalt.length >= 2);
  assert.equal(kalt.length, 2, "nettverksfeil skal gi nøyaktig én retry");
  assert.ok(kalt[0].opts.headers["Idempotency-Key"]);
  assert.equal(kalt[0].opts.headers["Idempotency-Key"],
    kalt[1].opts.headers["Idempotency-Key"],
    "retry MÅ gjenbruke samme idempotensnøkkel");
  assert.equal(JSON.parse(kalt[0].opts.body).saksversjon, 3);
  globalThis.fetch = ekte;
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
});

test("Unntak: 409 utestaaende_oppdrag → avklaringstekst, ingen blind retry (gate 14a)", async () => {
  const kalt = [];
  const ekte = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    if (opts && opts.method === "POST") {
      kalt.push({ url, opts });
      // Den LUKKEDE 14a-koden (ikke rå DTO): UI-et må skille den fra en
      // generisk 409 / stale saksversjon og vise avklaringsteksten.
      return { ok: false, status: 409,
        json: async () => ({ feil: "utestaaende_oppdrag", request_id: "r" }) };
    }
    return ekte(url, opts);
  };
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  SVAR = { ...STD, "/v1/unntak/1": { id: 1, ts: "2026-08-09T09:00:00+00:00",
    handling: "utbetaling", kategori: "over_grense", sakstype: "normal",
    status: "manuell", prioritet: "hoy", begrunnelse: ["belop_over_grense"],
    saksversjon: 2, tillatte_handlinger: ["godkjenn", "avvis", "eskaler"] } };
  const h = nyHoved();
  visUnntak(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => document.querySelector('[role="dialog"]'));
  const finn = (rot, tekst) => [...rot.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === tekst);
  // Avvis → bekreftelsesdialog → bekreft.
  finn(document.querySelector('[role="dialog"]'), t("ui.unntak.handling.avvis"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => [...document.querySelectorAll('[role="dialog"]')]
    .some((d) => d.textContent.includes(t("ui.unntak.bekreft.avvis"))));
  const bek = [...document.querySelectorAll('[role="dialog"]')]
    .find((d) => d.textContent.includes(t("ui.unntak.bekreft.avvis")));
  finn(bek, t("ui.unntak.handling.avvis"))
    .dispatchEvent(new window.Event("click"));

  await vent(() => document.querySelector('[role="status"]')
    && document.querySelector('[role="status"]').textContent.length > 0);
  const live = document.querySelector('[role="status"]').textContent;
  // Den KONKRETE avklaringsteksten, ALDRI den generiske feilen.
  assert.equal(live, t("ui.unntak.utestaaende_oppdrag"));
  assert.notEqual(live, t("ui.unntak.behandling_feilet"));
  // Nøyaktig ett POST-kall: en konflikt retries ALDRI blindt.
  assert.equal(kalt.length, 1, "409 skal ikke gi retry");
  globalThis.fetch = ekte;
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
});

test("Unntak: avvis skjult ved utestaaende oppdrag, m/ forklaring (gate 14a)", async () => {
  SVAR = { ...STD, "/v1/unntak/1": { id: 1, ts: "2026-08-09T09:00:00+00:00",
    handling: "utbetaling", kategori: "over_grense", sakstype: "normal",
    status: "manuell", prioritet: "hoy", begrunnelse: ["belop_over_grense"],
    saksversjon: 2, tillatte_handlinger: ["godkjenn", "eskaler"],
    avvis_utilgjengelig: "utestaaende_oppdrag" } };
  const h = nyHoved();
  visUnntak(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => document.querySelector('[role="dialog"]'));
  const dlg = document.querySelector('[role="dialog"]');
  const knapper = [...dlg.querySelectorAll("button")].map((b) => b.textContent.trim());
  assert.ok(!knapper.includes(t("ui.unntak.handling.avvis")),
    "avvis-knappen skal ikke vises når serveren har utelatt den");
  assert.ok(knapper.includes(t("ui.unntak.handling.eskaler")), "eskaler skal vises");
  assert.ok(dlg.textContent.includes(t("ui.unntak.utestaaende_oppdrag")),
    "forklaringen på hvorfor avvis mangler skal vises");
});

test("Tom liste → TomTilstand", async () => {
  SVAR = { ...STD, "/v1/beslutninger": { rader: [], neste_cursor: null } };
  const h = nyHoved();
  visBeslutninger(h, ctx());
  await vent(() => h.querySelector(".tilstand.tom"));
  assert.ok(h.querySelector(".tilstand.tom"));
});

test("401 på flate → paaUautorisert (innlogging), IKKE ingen-tilgang", async () => {
  SVAR = { "/v1/oversikt": 401 };
  const h = nyHoved();
  let ua = false;
  visOversikt(h, ctx({ paaUautorisert: () => { ua = true; } }));
  await vent(() => ua);
  assert.ok(ua, "401 skal utløse innloggingsflate");
  assert.ok(!h.querySelector(".ingen-tilgang"));
});

test("403 på flate → ingen-tilgang-tilstand, IKKE innlogging", async () => {
  SVAR = { "/v1/oversikt": 403 };
  const h = nyHoved();
  let ua = false;
  visOversikt(h, ctx({ paaUautorisert: () => { ua = true; } }));
  await vent(() => h.querySelector(".ingen-tilgang"));
  assert.ok(h.querySelector(".tilstand.ingen-tilgang"));
  assert.ok(!ua, "403 skal IKKE utløse innlogging");
});

test("Feil (500) → Feiltilstand med Prøv igjen", async () => {
  SVAR = { "/v1/oversikt": 500 };
  const h = nyHoved();
  visOversikt(h, ctx());
  await vent(() => h.querySelector(".tilstand.feil"));
  assert.ok(h.querySelector(".tilstand.feil button"));
});

// --- Dashbordet (§2.3 Sentrum): KPI + prioriterte varsler + siste aktivitet -

test("Dashbord: begge listene rendres med lenke videre", async () => {
  SVAR = { ...STD };
  const h = nyHoved();
  visOversikt(h, ctx());
  await vent(() => h.querySelectorAll(".dash-blokk").length === 2
    && h.querySelectorAll(".dash-rad").length >= 2);
  const tekst = h.textContent;
  assert.ok(tekst.includes(t("ui.dashbord.varsler")));
  assert.ok(tekst.includes(t("ui.dashbord.aktivitet")));
  // Radene kommer fra de EKTE feltnavnene: unntak-svaret heter `saker`,
  // beslutninger heter `rader`. Første utgave leste `rader` begge steder og
  // viste en evig tom varselliste — mocken her speiler serveren, så den
  // fanger nettopp det.
  assert.ok(tekst.includes("utbetaling"));
  const knapper = [...h.querySelectorAll(".dash-blokk button")]
    .map((b) => b.textContent);
  assert.ok(knapper.includes(t("ui.dashbord.til_unntak")));
  assert.ok(knapper.includes(t("ui.dashbord.til_beslutninger")));
});

test("Dashbord: en feilet blokk feller ikke de andre", async () => {
  SVAR = { ...STD, "/v1/unntak": 500 };
  const h = nyHoved();
  visOversikt(h, ctx());
  await vent(() => h.querySelector(".dash-blokk .tilstand.feil")
    && h.textContent.includes("135"));
  // KPI-ene og aktivitetslisten står; unntaksblokken har sin egen
  // feiltilstand med «Prøv igjen» som bare gjelder den.
  assert.ok(h.textContent.includes("135"), "KPI-kortene forsvant");
  assert.ok(h.textContent.includes(t("ui.dashbord.til_beslutninger")),
    "aktivitetslisten ble revet med av unntaksfeilen");
  const feil = h.querySelectorAll(".dash-blokk .tilstand.feil");
  assert.equal(feil.length, 1, "feilen skal være avgrenset til sin blokk");
});

test("Dashbord: åpne varsler avgrenses av SERVEREN, ikke av flaten",
  async () => {
    // Avgrensningen må ligge foran `LIMIT`. Filtrerte flaten selv, ville en
    // side der de åtte ferskeste sakene er ferdigbehandlet blitt vist som
    // «ingenting venter» — med en uløst sak rett bak sidegrensen. Derfor
    // sjekker testen SPØRSMÅLET som stilles, ikke etterbehandlingen av svaret.
    let spurt = null;
    const brukFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      if (url.startsWith("/v1/unntak?")) spurt = url;
      return brukFetch(url, opts);
    };
    // Serveren svarer med en godkjenningsstatus flatens gamle tillatelses-
    // liste ikke kjente: den saken ventet på et menneske og forsvant likevel.
    SVAR = { ...STD, "/v1/unntak": { saker: [
      { id: 3, ts: "2026-08-09T09:02:00+00:00", handling: "venter.sak",
        kategori: "over_grense", prioritet: "hoy",
        status: "venter_godkjenning" },
    ], neste_cursor: null } };
    const h = nyHoved();
    try {
      visOversikt(h, ctx());
      await vent(() => h.textContent.includes("venter.sak"));
      assert.ok(spurt && new URLSearchParams(spurt.split("?")[1])
        .get("status") === "apen",
      `dashbordet ba ikke om kun åpne saker: ${spurt}`);
    } finally {
      globalThis.fetch = brukFetch;
    }
  });

test("Dashbord: tomme lister sier det, og siden er axe-ren", async () => {
  SVAR = { ...STD,
    "/v1/unntak": { saker: [], neste_cursor: null },
    "/v1/beslutninger": { rader: [], neste_cursor: null } };
  const h = nyHoved();
  visOversikt(h, ctx());
  await vent(() => h.textContent.includes(t("ui.dashbord.ingen_varsler")));
  assert.ok(h.textContent.includes(t("ui.dashbord.ingen_aktivitet")));
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

// --- Angre en feilopprettet policy (032) -----------------------------------

test("Policy: slett-knappen spør først, poster så, og flaten viser sannheten",
  async () => {
    let postet = null;
    let kropp = null;
    const brukFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      if (opts && opts.method === "POST") {
        postet = url.split("?")[0];
        kropp = JSON.parse(opts.body);
        return { ok: true, status: 200,
          json: async () => ({ slettet: 1 }) };
      }
      const sti = url.split("?")[0];
      if (sti === "/v1/policy/aktiv") {
        // Etter slettingen finnes ingen aktiv policy — 404 er sannheten.
        if (postet) return { ok: false, status: 404,
          json: async () => ({ feil: "ikke_funnet" }) };
        return { ok: true, status: 200, json: async () => STD[sti] };
      }
      return brukFetch(url, opts);
    };
    const h = nyHoved();
    visPolicy(h, ctx({ scopes: ["policy:read", "policy:write"] }));
    await vent(() => h.textContent.includes(t("ui.policy.slett")));
    [...h.querySelectorAll("button")]
      .find((b) => b.textContent.trim() === t("ui.policy.slett"))
      .dispatchEvent(new window.Event("click"));
    // Et irreversibelt valg krever bekreftelse — og den navngir policyen.
    await vent(() => [...document.querySelectorAll('[role="dialog"]')]
      .some((d) => d.textContent.includes(t("ui.policy.slett_tittel"))));
    const dlg = [...document.querySelectorAll('[role="dialog"]')]
      .find((d) => d.textContent.includes(t("ui.policy.slett_tittel")));
    assert.ok(dlg.textContent.includes("p"), "dialogen navngir ikke policyen");
    // ÉN aktiv policy: da er «tenanten står uten aktiv policy etterpå» sant.
    assert.ok(dlg.textContent.includes(t("ui.policy.slett_tekst")));
    assert.equal(postet, null, "slettet FØR eier bekreftet");
    [...dlg.querySelectorAll("button")]
      .find((b) => b.textContent.trim() === t("ui.policy.slett"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => postet);
    assert.equal(postet, "/v1/policy/p/slett");
    // ...og forespørselen bærer identiteten flaten VISTE (Codex P1). Uten den
    // slettet serveren alle versjoner av `p`, også en som ble aktivert mens
    // dialogen sto åpen. Kontroll: send `{}`, så blir denne rød.
    assert.equal(kropp.versjon, "0.2.0");
    assert.equal(kropp.innholds_hash, "a".repeat(64));
    // Flaten tegnes på nytt og viser at ingen policy er aktiv — sannheten,
    // ikke en foreldet visning av det som nettopp ble slettet.
    await vent(() => h.querySelector(".tilstand.tom"));
    globalThis.fetch = brukFetch;
  });

test("Policy: flere aktive → hver av dem kan slettes, ikke en blindvei",
  async () => {
    // Kontroll: la `hentAktiv` kaste videre på 5xx, så blir denne rød.
    //
    // Dette ER feilen funksjonen finnes for: to policyer ble aktivert ved en
    // feil. `/v1/policy/aktiv` nekter da å velge (500 `intern_feil`), og uten
    // en vei videre endte flaten i en generisk feiltilstand — slettehandlingen
    // var utilgjengelig nøyaktig når den trengtes.
    let postet = null;
    let kropp = null;
    const brukFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const sti = url.split("?")[0];
      if (opts && opts.method === "POST") {
        postet = sti;
        kropp = JSON.parse(opts.body);
        return { ok: true, status: 200, json: async () => ({ slettet: 1 }) };
      }
      if (sti === "/v1/policy/aktiv") {
        return { ok: false, status: 500,
          json: async () => ({ feil: "intern_feil" }) };
      }
      if (sti === "/v1/policy/aktive") {
        return { ok: true, status: 200, json: async () => ({ policyer: [
          { policy_id: "tjenestebedrift1", versjon: "1.0.0",
            innholds_hash: "a".repeat(64) },
          { policy_id: "tjenestebedrift2", versjon: "1.0.0",
            innholds_hash: "b".repeat(64) }] }) };
      }
      return brukFetch(url, opts);
    };
    const h = nyHoved();
    visPolicy(h, ctx({ scopes: ["policy:read", "policy:write"] }));
    await vent(() => h.querySelectorAll(".policy-angre").length === 2);
    assert.ok(!h.querySelector(".tilstand.feil"), "flaten endte i feiltilstand");
    assert.ok(h.textContent.includes(t("ui.policy.flere_aktive")),
      "tilstanden ble ikke forklart");
    // Hver seksjon NAVNGIR sin policy — ellers er to like «Slett policy»-
    // knapper ikke et valg noen kan ta.
    const seksjoner = [...h.querySelectorAll(".policy-angre")];
    assert.ok(seksjoner[0].textContent.includes("tjenestebedrift1"));
    assert.ok(seksjoner[1].textContent.includes("tjenestebedrift2"));
    // ...og den andre knappen sletter den ANDRE policyen.
    [...seksjoner[1].querySelectorAll("button")]
      .find((b) => b.textContent.trim() === t("ui.policy.slett"))
      .dispatchEvent(new window.Event("click"));
    const dlg = await vent(() => [...document.querySelectorAll('[role="dialog"]')]
      .find((d) => d.textContent.includes("tjenestebedrift2"))) &&
      [...document.querySelectorAll('[role="dialog"]')]
        .find((d) => d.textContent.includes("tjenestebedrift2"));
    // Bekreftelsen beskriver tilstanden slettingen faktisk etterlater:
    // `tjenestebedrift1` blir stående og styrer beslutninger videre.
    // Kontroll: bruk `slett_tekst` her også, så blir denne rød.
    assert.ok(dlg.textContent.includes(t("ui.policy.slett_tekst_flere")),
      "bekreftelsen beskrev ikke de øvrige aktive");
    assert.ok(!dlg.textContent.includes(t("ui.policy.slett_tekst")),
      "bekreftelsen lovet at tenanten blir stående uten aktiv policy");
    [...dlg.querySelectorAll("button")]
      .find((b) => b.textContent.trim() === t("ui.policy.slett"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => postet);
    assert.equal(postet, "/v1/policy/tjenestebedrift2/slett");
    // Hver seksjon binder seg til SIN egen rad — ikke den andres, og ikke en
    // identitet fra en liste den tilfeldigvis sto i.
    assert.equal(kropp.innholds_hash, "b".repeat(64));
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
    globalThis.fetch = brukFetch;
  });

test("Policy: en versjon som er aktivert i mellomtiden avvises, ikke slettes",
  async () => {
    // Kontroll: la `angreSeksjon` behandle `policy_endret` som en generisk
    // feil, så blir denne rød. Avvisningen er ikke støy — den sier at
    // visningen er FORELDET, og at eier må se den nye versjonen før hun
    // avgjør om den også skal bort. Flaten tegnes derfor ikke på nytt under
    // henne; den ber om en ny lasting.
    const brukFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const sti = url.split("?")[0];
      if (opts && opts.method === "POST") {
        return { ok: false, status: 409,
          json: async () => ({ feil: "policy_endret" }) };
      }
      if (sti === "/v1/policy/aktiv") {
        return { ok: true, status: 200, json: async () => STD[sti] };
      }
      return brukFetch(url, opts);
    };
    const h = nyHoved();
    visPolicy(h, ctx({ scopes: ["policy:read", "policy:write"] }));
    await vent(() => h.textContent.includes(t("ui.policy.slett")));
    [...h.querySelectorAll("button")]
      .find((b) => b.textContent.trim() === t("ui.policy.slett"))
      .dispatchEvent(new window.Event("click"));
    const dlg = await vent(() => [...document.querySelectorAll('[role="dialog"]')]
      .find((d) => d.textContent.includes(t("ui.policy.slett_tittel")))) &&
      [...document.querySelectorAll('[role="dialog"]')]
        .find((d) => d.textContent.includes(t("ui.policy.slett_tittel")));
    [...dlg.querySelectorAll("button")]
      .find((b) => b.textContent.trim() === t("ui.policy.slett"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => h.textContent.includes(t("ui.policy.slett_endret")));
    assert.ok(h.textContent.includes(t("ui.policy.slett_endret")),
      "avvisningen forklarte ikke at visningen er foreldet");
    assert.ok(!h.textContent.includes(t("ui.policy.slett_feilet")),
      "en foreldet visning ble meldt som «slettingen feilet»");
    globalThis.fetch = brukFetch;
  });

test("Policy: en policy en annen alt har slettet er UKJENT, ikke endret",
  async () => {
    // Naboen til testen over, og den 032 tidligere svarte feil på: «finnes
    // ikke» ble målt ETTER identitetssammenligningen, så en policy en annen
    // operatør alt hadde slettet kom ut som `policy_endret` — flaten sendte
    // eier på leting etter en ny versjon som ikke finnes. Nå skilles de i
    // databasen, og skillet må bæres helt ut hit.
    const brukFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const sti = url.split("?")[0];
      if (opts && opts.method === "POST") {
        return { ok: false, status: 404,
          json: async () => ({ feil: "policy_ukjent" }) };
      }
      if (sti === "/v1/policy/aktiv") {
        return { ok: true, status: 200, json: async () => STD[sti] };
      }
      return brukFetch(url, opts);
    };
    const h = nyHoved();
    visPolicy(h, ctx({ scopes: ["policy:read", "policy:write"] }));
    await vent(() => h.textContent.includes(t("ui.policy.slett")));
    [...h.querySelectorAll("button")]
      .find((b) => b.textContent.trim() === t("ui.policy.slett"))
      .dispatchEvent(new window.Event("click"));
    const dlg = await vent(() => [...document.querySelectorAll('[role="dialog"]')]
      .find((d) => d.textContent.includes(t("ui.policy.slett_tittel")))) &&
      [...document.querySelectorAll('[role="dialog"]')]
        .find((d) => d.textContent.includes(t("ui.policy.slett_tittel")));
    [...dlg.querySelectorAll("button")]
      .find((b) => b.textContent.trim() === t("ui.policy.slett"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => h.textContent.includes(t("ui.policy.slett_ukjent")));
    assert.ok(h.textContent.includes(t("ui.policy.slett_ukjent")));
    assert.ok(!h.textContent.includes(t("ui.policy.slett_endret")),
      "en slettet policy ble meldt som en aktivert ny versjon");
    assert.ok(!h.textContent.includes(t("ui.policy.slett_feilet")));
    globalThis.fetch = brukFetch;
  });

test("Policy: en leser uten policy:write ser HVILKE som står aktive",
  async () => {
    // Kontroll: flytt identitetene tilbake inn i `angreSeksjon`, så blir
    // denne rød. `leser`, `admin` og `sikkerhet` har `policy:read` uten
    // `policy:write`. Uten dette fikk de varselet om at arbeidsområdet er i
    // en feiltilstand — og ikke ett ord om hvilke policyer det gjaldt.
    const brukFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const sti = url.split("?")[0];
      if (sti === "/v1/policy/aktiv") {
        return { ok: false, status: 500,
          json: async () => ({ feil: "intern_feil" }) };
      }
      if (sti === "/v1/policy/aktive") {
        return { ok: true, status: 200, json: async () => ({ policyer: [
          { policy_id: "tjenestebedrift1", versjon: "1.0.0",
            innholds_hash: "a".repeat(64) },
          { policy_id: "tjenestebedrift2", versjon: "2.0.0",
            innholds_hash: "b".repeat(64) }] }) };
      }
      return brukFetch(url, opts);
    };
    const h = nyHoved();
    visPolicy(h, ctx({ scopes: ["policy:read"] }));
    await vent(() => h.textContent.includes("tjenestebedrift2"));
    assert.ok(h.textContent.includes(t("ui.policy.flere_aktive")));
    assert.ok(h.textContent.includes("tjenestebedrift1"));
    // Versjonen er med: to rader som bare sier «tjenestebedrift1» og
    // «tjenestebedrift2» sier ikke hvilke RADER som skal ryddes.
    assert.ok(h.textContent.includes("2.0.0"), "versjonen mangler");
    // ...men mutasjonen står ikke for dem.
    assert.equal(h.querySelectorAll(".policy-angre").length, 0);
    assert.ok(![...h.querySelectorAll("button")]
      .some((b) => b.textContent.trim() === t("ui.policy.slett")),
      "slett-knappen sto for en leser uten policy:write");
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
    globalThis.fetch = brukFetch;
  });

test("Policy: en 5xx reserven ikke forklarer blir stående som feil",
  async () => {
    // `intern_feil` sier «det er FLERE». Kommer reserven tilbake med én, er
    // tilstanden en annen enn feilen beskrev — da står den ærlige
    // feiltilstanden, i stedet for en liste som later som alt er i orden.
    const brukFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const sti = url.split("?")[0];
      if (sti === "/v1/policy/aktiv") {
        return { ok: false, status: 500,
          json: async () => ({ feil: "intern_feil" }) };
      }
      if (sti === "/v1/policy/aktive") {
        return { ok: true, status: 200, json: async () => ({ policyer: [
          { policy_id: "p", versjon: "0.2.0",
            innholds_hash: "a".repeat(64) }] }) };
      }
      return brukFetch(url, opts);
    };
    const h = nyHoved();
    visPolicy(h, ctx({ scopes: ["policy:read", "policy:write"] }));
    await vent(() => h.querySelector(".tilstand.feil"));
    assert.ok(h.querySelector(".tilstand.feil"), "feilen ble skjult");
    assert.ok(!h.querySelector(".policy-angre"));
    globalThis.fetch = brukFetch;
  });

test("Policy: en ENSLIG korrupt aktiv policy kan ryddes", async () => {
  // `policy_korrupt` sier «raden kan ikke TOLKES», og det er sant også når
  // den er alene. `/v1/policy/aktive` bygger med vilje ingen DTO nettopp for
  // at en slik rad skal kunne pekes på og slettes — med terskelen 2 var den
  // enslige korrupte policyen den ENESTE som ikke kunne ryddes fra flaten.
  let postet = null;
  const brukFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    const sti = url.split("?")[0];
    if (opts && opts.method === "POST") {
      postet = sti;
      return { ok: true, status: 200, json: async () => ({ slettet: 1 }) };
    }
    if (sti === "/v1/policy/aktiv") {
      return { ok: false, status: 500,
        json: async () => ({ feil: "policy_korrupt" }) };
    }
    if (sti === "/v1/policy/aktive") {
      return { ok: true, status: 200, json: async () => ({ policyer: [
        { policy_id: "korruptbedrift", versjon: "0.2.0",
          innholds_hash: "a".repeat(64) }] }) };
    }
    return brukFetch(url, opts);
  };
  const h = nyHoved();
  visPolicy(h, ctx({ scopes: ["policy:read", "policy:write"] }));
  await vent(() => h.querySelector(".policy-angre"));
  assert.ok(!h.querySelector(".tilstand.feil"), "blindveien sto igjen");
  // Banneret beskriver DENNE feiltilstanden, ikke «flere aktive»: det er én.
  assert.ok(h.textContent.includes(t("ui.policy.korrupt_aktiv")),
    "banneret forklarte ikke at policyen er ulesbar");
  assert.ok(!h.textContent.includes(t("ui.policy.flere_aktive")));
  assert.ok(h.textContent.includes("korruptbedrift"), "identiteten sto ikke");
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === t("ui.policy.slett"))
    .dispatchEvent(new window.Event("click"));
  const dlg = await vent(() =>
    document.querySelector('[role="dialog"]')) &&
    document.querySelector('[role="dialog"]');
  // ... og bekreftelsen sier det sanne: etterpå står tenanten uten aktiv
  // policy. «De øvrige blir stående» ville vært galt — det er ingen øvrige.
  assert.ok(dlg.textContent.includes(t("ui.policy.slett_tekst")),
    "bekreftelsen beskrev ikke tilstanden etter slettingen");
  assert.ok(!dlg.textContent.includes(t("ui.policy.slett_tekst_flere")),
    "bekreftelsen lovet øvrige aktive som ikke finnes");
  [...dlg.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === t("ui.policy.slett"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => postet);
  assert.equal(postet, "/v1/policy/korruptbedrift/slett");
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
  globalThis.fetch = brukFetch;
});

test("Policy: en utløpt økt under reservekallet sender til innlogging",
  async () => {
    // Kontroll: la reserven fange alt og kaste den opprinnelige 5xx-en, så
    // blir denne rød — og eier får en «prøv igjen»-knapp som aldri kan lykkes.
    //
    // 401 fra det andre kallet er ikke et utsagn om policyen, det er et utsagn
    // om økten: den er ute, og da gjelder rammens globale håndtering også her.
    const brukFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const sti = url.split("?")[0];
      if (sti === "/v1/policy/aktiv") {
        return { ok: false, status: 500,
          json: async () => ({ feil: "intern_feil" }) };
      }
      if (sti === "/v1/policy/aktive") {
        return { ok: false, status: 401,
          json: async () => ({ feil: "uautorisert" }) };
      }
      return brukFetch(url, opts);
    };
    const h = nyHoved();
    let ua = false;
    visPolicy(h, ctx({ scopes: ["policy:read", "policy:write"],
                       paaUautorisert: () => { ua = true; } }));
    await vent(() => ua);
    assert.ok(ua, "401 fra reserven ble svelget som en vanlig feil");
    assert.ok(!h.querySelector(".tilstand.feil"));
    globalThis.fetch = brukFetch;
  });

test("Policy: en trukket tilgang under reservekallet gir ingen-tilgang",
  async () => {
    // Samme sak for 403: rollen er trukket mellom de to kallene. «Prøv igjen»
    // ville vært en løgn om at det finnes noe å prøve.
    const brukFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const sti = url.split("?")[0];
      if (sti === "/v1/policy/aktiv") {
        return { ok: false, status: 500,
          json: async () => ({ feil: "intern_feil" }) };
      }
      if (sti === "/v1/policy/aktive") {
        return { ok: false, status: 403,
          json: async () => ({ feil: "ingen_tilgang" }) };
      }
      return brukFetch(url, opts);
    };
    const h = nyHoved();
    let ua = false;
    visPolicy(h, ctx({ scopes: ["policy:read", "policy:write"],
                       paaUautorisert: () => { ua = true; } }));
    await vent(() => h.querySelector(".tilstand.ingen-tilgang"));
    assert.ok(h.querySelector(".tilstand.ingen-tilgang"),
      "403 fra reserven ble en generisk feiltilstand");
    assert.ok(!ua, "403 skal IKKE utløse innlogging");
    globalThis.fetch = brukFetch;
  });

test("Policy: «i bruk»-avvisningen forklares, den gjemmes ikke", async () => {
  const brukFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    if (opts && opts.method === "POST") {
      return { ok: false, status: 409,
        json: async () => ({ feil: "policy_i_bruk" }) };
    }
    return brukFetch(url, opts);
  };
  SVAR = { ...STD };
  const h = nyHoved();
  visPolicy(h, ctx({ scopes: ["policy:read", "policy:write"] }));
  await vent(() => h.textContent.includes(t("ui.policy.slett")));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === t("ui.policy.slett"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => [...document.querySelectorAll('[role="dialog"]')].length);
  const dlg = [...document.querySelectorAll('[role="dialog"]')].pop();
  [...dlg.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === t("ui.policy.slett"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => h.textContent.includes(t("ui.policy.slett_i_bruk")));
  assert.ok(h.textContent.includes(t("ui.policy.slett_i_bruk")),
    "avvisningen må FORKLARE skillet slett/avvikle — ikke bare feile");
  globalThis.fetch = brukFetch;
});

test("Policy: en leser uten policy:write får ikke slett-seksjonen", async () => {
  // Flaten nås med `policy:read`, og `leser`/`admin`/`sikkerhet` har nettopp
  // det og ikke `policy:write`. «Vis vakten så den kan forstås» gjelder
  // TILSTAND, ikke TILGANG: for dem finnes ingen tilstand som gjør knappen
  // brukbar, så den ville bare invitert gjennom en irreversibel
  // bekreftelsesdialog fram til serverens 403.
  SVAR = { ...STD };
  const h = nyHoved();
  visPolicy(h, ctx({ scopes: ["policy:read"] }));
  await vent(() => h.textContent.includes(t("ui.policy.roller")));
  assert.ok(!h.querySelector(".policy-angre"), "slett-seksjonen ble vist");
  assert.ok(![...h.querySelectorAll("button")]
    .some((b) => b.textContent.trim() === t("ui.policy.slett")));
  // Lesingen står igjen — det er BARE mutasjonen som forsvinner.
  assert.ok(h.textContent.includes("utbetaling"));
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

test("Policy: en retry av slettingen gjenbruker idempotensnøkkelen",
  async () => {
    // Serveren lagrer og replayer slettesvaret, men bare når nøkkelen er den
    // SAMME. Roterer klienten den, er retryen en ny operasjon på en policy som
    // alt er borte → `policy_ukjent`: en endelig feil på noe som lyktes.
    const nokler = [];
    const brukFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      if (opts && opts.method === "POST") {
        nokler.push(opts.headers["Idempotency-Key"]);
        // Første forsøk: svaret går tapt på veien tilbake.
        if (nokler.length === 1) throw new TypeError("nettverk");
        return { ok: true, status: 200,
          json: async () => ({ slettet: 1, policy_id: "p" }) };
      }
      return brukFetch(url, opts);
    };
    SVAR = { ...STD };
    const h = nyHoved();
    visPolicy(h, ctx({ scopes: ["policy:read", "policy:write"] }));
    await vent(() => h.textContent.includes(t("ui.policy.slett")));
    const slett = async () => {
      [...h.querySelectorAll("button")]
        .find((b) => b.textContent.trim() === t("ui.policy.slett"))
        .dispatchEvent(new window.Event("click"));
      await vent(() => [...document.querySelectorAll('[role="dialog"]')].length);
      const dlg = [...document.querySelectorAll('[role="dialog"]')].pop();
      [...dlg.querySelectorAll("button")]
        .find((b) => b.textContent.trim() === t("ui.policy.slett"))
        .dispatchEvent(new window.Event("click"));
    };
    await slett();
    await vent(() => nokler.length === 1);
    // Nettverksfeilen etterlater seksjonen — og dermed nøkkelen — på plass.
    await vent(() => h.textContent.includes(t("ui.policy.slett_feilet")));
    await slett();
    await vent(() => nokler.length === 2);
    assert.ok(nokler[0], "ingen Idempotency-Key ble sendt");
    assert.equal(nokler[1], nokler[0],
      "retryen roterte nøkkelen — serveren kan da ikke replaye");
    globalThis.fetch = brukFetch;
  });
