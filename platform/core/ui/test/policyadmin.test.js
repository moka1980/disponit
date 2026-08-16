// PR-013 CP7 — Policyadministrasjons-flaten (jsdom + axe).
// Diff + risikoklasse PER endring vises FØR aktivering; fire-øyne-status
// rendres; attestering går som CSRF-POST med Idempotency-Key og bærer diff_hash
// (godkjenneren attesterer diffen hun SÅ, ikke versjonsnummeret).
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visPolicyadmin } from "../static/js/flater/policyadmin.js";
import { lagRuter } from "../static/js/ruter.js";
import { el, sett } from "../static/js/dom.js";

settI18nForTest(NB, "nb");

const DETALJ = {
  utkast_id: "u-1", policy_id: "faktura-no", status: "validert",
  utkastversjon: 2, opprettet_av: "forf", innholds_hash: "h",
  base_versjon: null, diff_hash: "abcdef0123456789",
  risikoklasse: "UTVIDER",
  klassifisering_endringer: [
    { sti: "roller[]", klasse: "UTVIDER" },
    { sti: "handlinger[bokfor]", klasse: "INNSNEVRER" },
  ],
  diff: { endringer: [
    { sti: "roller[0].id", type: "lagt_til", til: "regnskap" },
  ] },
  pakrevd_antall_godkjennere: 2,
  aktiv_runde: {
    runde: 1, status: "apen", diff_hash: "abcdef0123456789",
    risikoklasse: "UTVIDER", pakrevd_antall_godkjennere: 2,
    utloper: "2026-08-11T10:00:00+00:00",
    attestasjoner: [
      { bruker_id: "forf", rolle: "policyforvalter", er_forfatter: true,
        ts: "2026-08-10T09:00:00+00:00" },
    ],
  },
};

const LISTE = { utkast: [
  { utkast_id: "u-1", policy_id: "faktura-no", status: "validert",
    utkastversjon: 2, opprettet: "2026-08-10T08:00:00+00:00" },
] };

// To utkast i lista — det som skal til for å kappløpe to detaljåpninger.
const TO_UTKAST = { utkast: [
  LISTE.utkast[0],
  { utkast_id: "u-2", policy_id: "lonn-no", status: "validert",
    utkastversjon: 1, opprettet: "2026-08-10T09:00:00+00:00" },
] };

let SVAR;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  if (opts && opts.method) return SVAR.__post(url, opts);
  if (SVAR.__get) SVAR.__get(sti);
  const d = SVAR[sti];
  return d ? { ok: true, status: 200, json: async () => d }
           : { ok: false, status: 404, json: async () => ({ feil: "x" }) };
};

function ctx(over = {}) {
  return { sprak: "nb", scopes: [], tenant: "acme",
    paaUautorisert: () => {}, ...over };
}
async function vent(pred, n = 80) {
  for (let i = 0; i < n; i++) { if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0)); }
  return pred();
}
function nyHoved() {
  const brett = nyttBrett();
  const m = document.createElement("main"); m.id = "hovedinnhold"; m.tabIndex = -1;
  brett.append(m); return m;
}

test("Liste: utkast med status og versjon, axe rent", async () => {
  SVAR = { "/v1/policyutkast": LISTE, __post: async () => ({}) };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  assert.ok(h.textContent.includes("faktura-no"));
  assert.ok(h.textContent.includes(t("ui.policyadmin.status.validert")));
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

test("Detalj: diff + risikoklasse PER endring + fire-øyne-status", async () => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": DETALJ,
    __post: async () => ({}) };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  // Detaljene står i flaten nå, ikke i en skuff.
  await vent(() => _finn(h, t("ui.policyadmin.tilbake_til_liste")));
  const dlg = h;
  // Risikoklasse per endring (både UTVIDER og INNSNEVRER vises).
  // Skuffen er delt i trinn: klassifisering og diff bor under «Endringer»,
  // fire-øyne-status under sin egen fane. Testen navigerer dit i stedet for å
  // anta at alt står under hverandre.
  const gaaTil = (tittel) => [...dlg.querySelectorAll('[role="tab"]')]
    .find((f) => f.textContent === tittel)
    .dispatchEvent(new window.Event("click"));
  gaaTil(t("ui.policyadmin.fane.endringer"));
  assert.ok(dlg.textContent.includes(t("risiko.UTVIDER")));
  assert.ok(dlg.textContent.includes(t("risiko.INNSNEVRER")));
  assert.ok(dlg.textContent.includes("roller[]"));
  gaaTil(t("ui.policyadmin.fane.fire_oyne"));
  // Fire-øyne-status: 1/2 + forfatter markert.
  assert.ok(dlg.textContent.includes("1 / 2"));
  assert.ok(dlg.textContent.includes(t("ui.policyadmin.forfatter")));
  assert.equal((await alvorligeBrudd(dlg, { fragment: true })).length, 0);
});

// Codex P1: fanene flyttet diffen bak et fanevalg mens attester-knappen ble
// stående fast utenfor dem — en godkjenner kunne aktivere fra «Oversikt» uten
// å ha sett hva hun aktiverte. Kontroll: settes starttrinnet tilbake til
// «oversikt», eller kobles `paaBytte` fra handlingene, forblir knappen låst og
// denne testen blir rød.
test("Attester: låst til diffen er sett — skuffen åpner PÅ «Endringer»", async () => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": DETALJ,
    __post: async () => ({}) };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  // Detaljene står i flaten nå, ikke i en skuff.
  await vent(() => _finn(h, t("ui.policyadmin.tilbake_til_liste")));
  const dlg = h;
  const valgt = [...dlg.querySelectorAll('[role="tab"]')]
    .find((f) => f.getAttribute("aria-selected") === "true");
  assert.equal(valgt.textContent, t("ui.policyadmin.fane.endringer"),
    "utkast som venter på attestering skal åpne på diffen");
  assert.ok(dlg.textContent.includes("roller[]"), "diffen er ikke synlig");
  const attest = [...dlg.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === t("ui.policyadmin.handling.attester"));
  assert.equal(attest.disabled, false, "diffen er sett — knappen skal være åpen");
  assert.equal(attest.getAttribute("aria-describedby"), null);
  assert.equal(dlg.querySelectorAll(".pa-handling p.sub").length, 0,
    "hintet skal fjernes når låsen er åpnet");
  assert.equal((await alvorligeBrudd(dlg, { fragment: true })).length, 0);
});

test("Attester: kvitteringen viser den granulære diffen, ikke bare hashen", async () => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": DETALJ,
    __post: async () => ({}) };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => _finn(h, t("ui.policyadmin.tilbake_til_liste")));
  [...h.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === t("ui.policyadmin.handling.attester"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => [...document.querySelectorAll('[role="dialog"]')]
    .some((d) => d.textContent.includes(t("ui.policyadmin.du_aktiverer"))));
  const bek = [...document.querySelectorAll('[role="dialog"]')]
    .find((d) => d.textContent.includes(t("ui.policyadmin.du_aktiverer")));
  // Hashen identifiserer diffen; den SIER den ikke. Selve endringene skal stå
  // i bekreftelsen man binder seg til.
  assert.ok(bek.textContent.includes("roller[0].id"), "felt-diff mangler");
  assert.ok(bek.textContent.includes("roller[]"), "klassifisering mangler");
  assert.ok(bek.textContent.includes(t("risiko.INNSNEVRER")));
  assert.equal((await alvorligeBrudd(bek, { fragment: true })).length, 0);
});

test("Attester: eksplisitt kvittering → CSRF-POST m/ Idempotency-Key + diff_hash", async () => {
  const kalt = [];
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": DETALJ,
    __post: async (url, opts) => {
      kalt.push({ url, opts });
      return { ok: true, status: 200,
        json: async () => ({ utfall: "aktivert", versjon: "1" }) };
    } };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => _finn(h, t("ui.policyadmin.tilbake_til_liste")));
  const finn = (rot, tekst) => [...rot.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === tekst);
  const attest = finn(h, t("ui.policyadmin.handling.attester"));
  assert.ok(attest, "attester-knapp mangler");
  attest.dispatchEvent(new window.Event("click"));
  // Eksplisitt kvittering viser risikoklasse + diff-hash.
  await vent(() => [...document.querySelectorAll('[role="dialog"]')]
    .some((d) => d.textContent.includes(t("ui.policyadmin.du_aktiverer"))));
  const bek = [...document.querySelectorAll('[role="dialog"]')]
    .find((d) => d.textContent.includes(t("ui.policyadmin.du_aktiverer")));
  assert.ok(bek.textContent.includes("abcdef012345"), "diff-hash ikke vist");
  finn(bek, t("ui.policyadmin.handling.attester"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => kalt.length > 0);
  assert.equal(kalt[0].opts.method, "POST");
  assert.ok(kalt[0].url.includes("/v1/policyutkast/u-1/attester"));
  assert.equal(kalt[0].opts.headers["X-Disponit-CSRF"], "tok123");
  assert.ok(kalt[0].opts.headers["Idempotency-Key"], "mangler Idempotency-Key");
  assert.equal(JSON.parse(kalt[0].opts.body).diff_hash, "abcdef0123456789");
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
});

test("Attester: nettverksretry GJENBRUKER samme Idempotency-Key", async () => {
  const kalt = [];
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": DETALJ,
    __post: async (url, opts) => {
      kalt.push({ url, opts });
      if (kalt.length === 1) throw new TypeError("network");
      return { ok: true, status: 200, json: async () => ({ utfall: "aktivert" }) };
    } };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => _finn(h, t("ui.policyadmin.tilbake_til_liste")));
  const finn = (rot, tekst) => [...rot.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === tekst);
  finn(h,
    t("ui.policyadmin.handling.attester")).dispatchEvent(new window.Event("click"));
  await vent(() => [...document.querySelectorAll('[role="dialog"]')]
    .some((d) => d.textContent.includes(t("ui.policyadmin.du_aktiverer"))));
  const bek = [...document.querySelectorAll('[role="dialog"]')]
    .find((d) => d.textContent.includes(t("ui.policyadmin.du_aktiverer")));
  finn(bek, t("ui.policyadmin.handling.attester"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => kalt.length >= 2);
  assert.equal(kalt.length, 2, "nettverksfeil skal gi nøyaktig én retry");
  assert.equal(kalt[0].opts.headers["Idempotency-Key"],
    kalt[1].opts.headers["Idempotency-Key"], "retry MÅ gjenbruke nøkkelen");
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
});

// PR-014 R3: stabil idempotensnøkkel for flatens valider + rundeåpning — et
// tapt svar + nytt klikk MÅ gjenbruke nøkkelen (retry, ikke ny operasjon).
const _finn = (rot, tekst) => [...rot.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === tekst);

function _medCsrf() {
  const desc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  return () => { if (desc) Object.defineProperty(document, "cookie", desc); };
}

// Åpner detaljskuffen og venter på DEN skuffen som bærer den forventede
// knappen. `_aapneDetalj` venter på «en hvilken som helst dialog», og en
// forrige tests sene async-rendring kan rekke å legge sin egen skuff i DOM-en
// først — da tester man forrige tests tilstand og får en umulig feil.
// Utkastet åpnes nå som en vanlig side i flaten, ikke som en skuff. Hjelperne
// returnerer derfor `hoved` — det er der detaljene står — i stedet for å lete
// etter `[role="dialog"]`.
async function _aapneDetaljMed(h, tekst) {
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => _finn(h, tekst));
  return h;
}

async function _aapneDetalj(h) {
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  // Detaljsiden er inne når tilbakeveien finnes — den bygges sammen med
  // innholdet, og finnes ikke i lista.
  await vent(() => _finn(h, t("ui.policyadmin.tilbake_til_liste")));
  return h;
}

test("Valider: retry etter nettverksfeil gjenbruker Idempotency-Key", async () => {
  const gjenopprett = _medCsrf();
  const kalt = [];
  SVAR = {
    "/v1/policyutkast": { utkast: [{ utkast_id: "u-1", policy_id: "p",
      status: "utkast", utkastversjon: 2, opprettet: "2026-08-10T08:00:00+00:00" }] },
    "/v1/policyutkast/u-1": { ...DETALJ, status: "utkast", aktiv_runde: null },
    __post: async (url, opts) => {
      kalt.push({ url, opts });
      if (kalt.length === 1) throw new TypeError("network");
      // Ugyldig → blir i skuffen (ingen re-åpning) så testen ikke lekker async.
      // Formen er serverens EKTE: 422 `policy_ugyldig` med `detaljer`. Den sto
      // før som 200 + `utfall: "ugyldig"` — en form serveren aldri sender — og
      // nettopp derfor fanget ingen test at flaten stolte på den.
      return { ok: false, status: 422,
        json: async () => ({ feil: "policy_ugyldig", detaljer: ["x"] }) };
    },
  };
  const dlg = await _aapneDetalj(nyHoved());
  const knapp = _finn(dlg, t("ui.policyadmin.handling.valider"));
  knapp.dispatchEvent(new window.Event("click"));
  await vent(() => kalt.length === 1);
  knapp.dispatchEvent(new window.Event("click"));      // retry
  await vent(() => kalt.length >= 2);
  assert.ok(kalt[0].url.includes("/valider"));
  assert.equal(kalt[0].opts.headers["Idempotency-Key"],
    kalt[1].opts.headers["Idempotency-Key"],
    "valider-retry MÅ gjenbruke idempotensnøkkelen");
  gjenopprett();
});

test("Åpne runde: retry etter nettverksfeil gjenbruker Idempotency-Key", async () => {
  const gjenopprett = _medCsrf();
  const kalt = [];
  SVAR = {
    "/v1/policyutkast": { utkast: [{ utkast_id: "u-1", policy_id: "p",
      status: "validert", utkastversjon: 2, opprettet: "2026-08-10T08:00:00+00:00" }] },
    "/v1/policyutkast/u-1": { ...DETALJ, status: "validert", aktiv_runde: null },
    __post: async (url, opts) => {
      kalt.push({ url, opts });
      if (kalt.length === 1) throw new TypeError("network");
      return { ok: true, status: 200, json: async () => ({ runde: 1 }) };
    },
  };
  const dlg = await _aapneDetalj(nyHoved());
  const knapp = _finn(dlg, t("ui.policyadmin.handling.apne_runde"));
  knapp.dispatchEvent(new window.Event("click"));
  await vent(() => kalt.length === 1);
  knapp.dispatchEvent(new window.Event("click"));      // retry
  await vent(() => kalt.length >= 2);
  assert.ok(kalt[0].url.includes("/aktiveringsrunde"));
  assert.equal(kalt[0].opts.headers["Idempotency-Key"],
    kalt[1].opts.headers["Idempotency-Key"],
    "rundeåpning-retry MÅ gjenbruke idempotensnøkkelen");
  gjenopprett();
});

test("Valider: serverens 422 vises som feilliste, ikke som en død knapp", async () => {
  // Serveren svarer 422 `policy_ugyldig` med feillista i `detaljer` — ALDRI
  // 200 med `utfall: "ugyldig"`. Koden ventet på den siste formen, så
  // `.then` kjørte aldri og 422-en havnet i en `.catch` som bare kalte
  // `meldLive`: annonsert til skjermleser, usynlig på skjermen. Eier klikket
  // «Valider» og ingenting skjedde.
  //
  // Fikstruren i testen over hadde NØYAKTIG samme misforståelse (den mocket
  // 200 + `utfall`), og det er derfor ingen test fanget dette: mocken bekreftet
  // koden i stedet for serveren. Denne bruker det ekte svaret.
  const gjenopprett = _medCsrf();
  SVAR = {
    "/v1/policyutkast": { utkast: [{ utkast_id: "u-1", policy_id: "p",
      status: "utkast", utkastversjon: 2, opprettet: "2026-08-10T08:00:00+00:00" }] },
    "/v1/policyutkast/u-1": { ...DETALJ, status: "utkast", aktiv_runde: null },
    __post: async () => ({
      ok: false, status: 422,
      json: async () => ({ feil: "policy_ugyldig", request_id: "r1",
        detaljer: ["skjema: meta/policy_id: '01' is too short"] }),
    }),
  };

  const dlg = await _aapneDetaljMed(nyHoved(),
    t("ui.policyadmin.handling.valider"));
  _finn(dlg, t("ui.policyadmin.handling.valider"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => dlg.querySelector(".pa-valfeil"));

  const boks = dlg.querySelector(".pa-valfeil");
  assert.ok(boks, "ingen synlig feilboks — knappen ser død ut");
  assert.equal(boks.getAttribute("role"), "alert");
  assert.ok(boks.textContent.includes("is too short"),
    "serverens begrunnelse nådde ikke skjermen");
  gjenopprett();
});

test("Valider: 422 uten detaljer sier likevel synlig fra", async () => {
  // Fail-visible: mangler serveren begrunnelse, skal flaten fortsatt vise at
  // utkastet er ugyldig — ikke falle tilbake til stillhet.
  const gjenopprett = _medCsrf();
  SVAR = {
    "/v1/policyutkast": { utkast: [{ utkast_id: "u-1", policy_id: "p",
      status: "utkast", utkastversjon: 2, opprettet: "2026-08-10T08:00:00+00:00" }] },
    "/v1/policyutkast/u-1": { ...DETALJ, status: "utkast", aktiv_runde: null },
    __post: async () => ({ ok: false, status: 422,
      json: async () => ({ feil: "policy_ugyldig" }) }),
  };
  const dlg = await _aapneDetaljMed(nyHoved(),
    t("ui.policyadmin.handling.valider"));
  _finn(dlg, t("ui.policyadmin.handling.valider"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => dlg.querySelector(".pa-valfeil"));
  assert.ok(dlg.querySelector(".pa-valfeil").textContent
    .includes(t("ui.policyadmin.ugyldig_uten_detaljer")));
  gjenopprett();
});

test("Valider: 5xx sier «handlingen feilet», ikke «utkastet er ugyldig»", async () => {
  // Bare 422 er et valideringssvar. En 500 (eller nettverksfeil, 403, 409)
  // sier ingenting om utkastet — påstår flaten «ugyldig» der, sender den eier
  // ut på leting etter feil i en policy som kan være helt i orden.
  const gjenopprett = _medCsrf();
  SVAR = {
    "/v1/policyutkast": { utkast: [{ utkast_id: "u-1", policy_id: "p",
      status: "utkast", utkastversjon: 2, opprettet: "2026-08-10T08:00:00+00:00" }] },
    "/v1/policyutkast/u-1": { ...DETALJ, status: "utkast", aktiv_runde: null },
    __post: async () => ({ ok: false, status: 500,
      json: async () => ({ feil: "internfeil" }) }),
  };
  const dlg = await _aapneDetaljMed(nyHoved(),
    t("ui.policyadmin.handling.valider"));
  _finn(dlg, t("ui.policyadmin.handling.valider"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => dlg.querySelector(".pa-valfeil"));
  const tekst = dlg.querySelector(".pa-valfeil").textContent;
  assert.ok(tekst.includes(t("ui.policyadmin.feilet")),
    "serverfeil skal være synlig, ikke bare annonsert");
  assert.ok(!tekst.includes(t("ui.policyadmin.ugyldig")),
    "en serverfeil er ikke et bevis på at utkastet er ugyldig");
  gjenopprett();
});

test("Utkast: åpnes som side i flaten, med policy-ID synlig og vei tilbake", async () => {
  // To åpne runder endte med hver sin attestasjon fordi flaten ikke sa hvilket
  // utkast man sto i — skuffen viste «Detalj» uten identitet. Siden bærer nå
  // policy-ID i overskriften og utkast-ID under, og har en synlig vei tilbake.
  const h = nyHoved();
  const dlg = await _aapneDetalj(h);

  assert.equal(document.querySelectorAll('[role="dialog"]').length, 0,
    "utkastet åpnet fortsatt som en skuff over flaten");
  assert.ok(dlg.textContent.includes(DETALJ.policy_id),
    "overskriften sier ikke HVILKEN policy utkastet gjelder");
  assert.ok(dlg.textContent.includes("u-1"),
    "utkast-ID vises ikke — to utkast ser like ut");
  assert.ok(_finn(h, t("ui.policyadmin.tilbake_til_liste")),
    "ingen synlig vei tilbake til lista");

  // Fokus følger sidebyttet: uten det står fokus igjen på raden man klikket,
  // i en liste som ikke er på skjermen lenger.
  assert.match(document.activeElement.tagName, /^H[12]$/,
    'fokus havnet ikke på sidens overskrift');

  // …og tilbake fører faktisk tilbake.
  _finn(h, t("ui.policyadmin.tilbake_til_liste"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector("tbody"));
  assert.ok(h.querySelector("tbody"), "kom ikke tilbake til utkastlista");
});

// Codex P1: da detaljen var en skuff, måtte «Rediger» lukke skuffen først. Som
// side ble lukkingen til `tilbakeTilListe`, og den lukker ingenting — den
// starter en ny liste-GET. Den og editorens detalj-GET tegner i samme `hoved`,
// så et sent listesvar erstattet editoren, med det eier hadde rukket å skrive.
test("Rediger: går rett til editoren uten å laste lista på nytt", async () => {
  const get = [];
  SVAR = {
    "/v1/policyutkast": { utkast: [{ utkast_id: "u-1", policy_id: "p",
      status: "utkast", utkastversjon: 2, opprettet: "2026-08-10T08:00:00+00:00" }] },
    "/v1/policyutkast/u-1": { ...DETALJ, status: "utkast", aktiv_runde: null,
      innhold: { meta: { policy_id: "p" } } },
    __get: (sti) => get.push(sti),
    __post: async () => ({}),
  };
  const h = nyHoved();
  const side = await _aapneDetaljMed(h, t("ui.policyadmin.handling.rediger"));
  get.length = 0;
  _finn(side, t("ui.policyadmin.handling.rediger"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => h.textContent.includes(t("ui.editor.tittel")));
  assert.deepEqual(get.filter((s) => s === "/v1/policyutkast"), [],
    "Rediger startet en liste-GET som kappløper med editoren om `hoved`");
});

// Codex P2: veien FRAM flyttet fokus til detaljsidens overskrift, veien
// TILBAKE flyttet det ingensteds. `last()` river DOM-en synkront for
// lastetilstanden, så knappen tastaturbrukeren nettopp trykte på er borte og
// fokus faller til `body` — tastaturnavigasjonen starter forfra, utenfor lista.
test("Tilbake til lista: fokus følger med, ikke til `body`", async () => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": DETALJ,
    __post: async () => ({}) };
  const h = nyHoved();
  await _aapneDetalj(h);
  _finn(h, t("ui.policyadmin.tilbake_til_liste"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector("tbody"));
  assert.notEqual(document.activeElement, document.body,
    "fokus falt til body — tastaturnavigasjonen starter utenfor lista");
  assert.ok(h.contains(document.activeElement), "fokus havnet utenfor flaten");
  assert.equal(document.activeElement.textContent, t("ui.policyadmin.tittel"),
    "fokus skal lande på listas overskrift");
});

// Codex P2: feilet detalj-GET erstattet HELE flaten med en naken feiltilstand.
// Skuffen lot lista ligge under seg; siden gjorde et forbigående 5xx til en
// blindvei man bare kom ut av ved å laste appen på nytt.
test("Detalj: feilet lasting beholder vei tilbake OG tilbyr «Prøv igjen»", async () => {
  SVAR = { "/v1/policyutkast": LISTE, __post: async () => ({}) };  // detalj → 404
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector(".tilstand.feil"));

  assert.ok(_finn(h, t("ui.prov_igjen")), "ingen vei ut av en forbigående feil");
  assert.ok(_finn(h, t("ui.policyadmin.tilbake_til_liste")),
    "feiltilstanden fjernet veien tilbake til lista");
  assert.ok(h.textContent.includes("u-1"),
    "feilsiden sier ikke hvilket utkast som ikke lot seg åpne");
  assert.match(document.activeElement.tagName, /^H[12]$/,
    "fokus ble stående i en liste som ikke er på skjermen lenger");
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);

  // «Prøv igjen» laster faktisk på nytt — og lykkes når serveren er tilbake.
  SVAR["/v1/policyutkast/u-1"] = DETALJ;
  _finn(h, t("ui.prov_igjen")).dispatchEvent(new window.Event("click"));
  await vent(() => h.textContent.includes(DETALJ.policy_id));
  assert.ok(_finn(h, t("ui.policyadmin.handling.attester")),
    "«Prøv igjen» hentet ikke detaljen på nytt");
});

test("Detalj: tilbake fra feiltilstanden fører til lista", async () => {
  SVAR = { "/v1/policyutkast": LISTE, __post: async () => ({}) };  // detalj → 404
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector(".tilstand.feil"));
  _finn(h, t("ui.policyadmin.tilbake_til_liste"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector("tbody"));
  assert.ok(h.querySelector("tbody"), "kom ikke tilbake til utkastlista");
});

// Codex P2: alle flater rendrer i ETT `hoved`. Et detaljsvar som kom tilbake
// etter at brukeren hadde valgt en annen toppnivårute, tegnet seg selv over
// DEN flaten — mens menyvalget hennes ble stående markert. Skjermen viste én
// flate og navigasjonen en annen.
test("Treigt detaljsvar rører ikke ruten brukeren har navigert til", async () => {
  let slipp = null;
  const brukFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    if (url.split("?")[0] === "/v1/policyutkast/u-1") {
      await new Promise((r) => { slipp = r; });
      return { ok: true, status: 200, json: async () => DETALJ };
    }
    return brukFetch(url, opts);
  };
  SVAR = { "/v1/policyutkast": LISTE, __post: async () => ({}) };
  const h = nyHoved();
  const annenFlate = (hoved) => sett(hoved, el("h1", { text: "annen flate" }));

  // Hashen settes FØR ruteren finnes, og `hashchange`-en den utløser slippes
  // gjennom uten lytter: ellers kunne den re-montere flaten midt i testen og
  // gjøre stempelet foreldet av seg selv — da hadde testen vært grønn uansett.
  window.location.hash = "#/policyadmin";
  await vent(() => false, 5);
  const ruter = lagRuter(h, ctx(),
    { policyadmin: visPolicyadmin, annen: annenFlate }, () => {});
  ruter.naviger();
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => slipp);                       // detalj-GET er ute på nettet

  // Brukeren navigerer videre mens svaret fortsatt er underveis. Ruteren
  // kobles av med det samme: en `hashchange` som tegnet `annen` på nytt ville
  // vasket bort sporet etter det foreldede svaret og skjult feilen.
  window.location.hash = "#/annen";
  ruter.naviger();
  ruter.stopp();
  assert.ok(h.textContent.includes("annen flate"));

  slipp();
  await vent(() => false, 20);                   // la svaret få tegne, om det vil
  assert.ok(h.textContent.includes("annen flate"),
    "det foreldede detaljsvaret rev bort ruten brukeren står i");
  assert.equal(_finn(h, t("ui.policyadmin.tilbake_til_liste")), undefined,
    "policyutkastet tegnet seg inn i en annen rute");

  globalThis.fetch = brukFetch;
});

test("Detalj: 403 gir tilgangsvakt med vei tilbake, men INGEN «Prøv igjen»", async () => {
  // 403 er ikke forbigående. En «Prøv igjen»-knapp der lover et annet svar
  // neste gang, og det løftet holder den ikke.
  const brukFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    if (url.split("?")[0] === "/v1/policyutkast/u-1") {
      return { ok: false, status: 403, json: async () => ({ feil: "ingen_tilgang" }) };
    }
    return brukFetch(url, opts);
  };
  SVAR = { "/v1/policyutkast": LISTE, __post: async () => ({}) };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector(".tilstand.ingen-tilgang"));
  assert.ok(_finn(h, t("ui.policyadmin.tilbake_til_liste")),
    "403 stengte eier inne uten vei tilbake");
  assert.equal(_finn(h, t("ui.prov_igjen")), undefined,
    "«Prøv igjen» på 403 lover noe den ikke kan holde");
  globalThis.fetch = brukFetch;
});

// Codex P2: rutersjekken alene er for grov. Den skifter bare når brukeren
// bytter TOPPNIVÅRUTE, mens flaten bytter visning på egen hånd — og de
// visningene kappløper om det samme `hoved`. Åpnet hun utkast A og så B, besto
// begge svarene rutersjekken: svarte A sist, tegnet A seg over B.
test("Detalj: treigt svar for A tegner seg ikke over utkastet B", async () => {
  const slipp = {};
  const brukFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    const m = /^\/v1\/policyutkast\/(u-\d)$/.exec(url.split("?")[0]);
    if (m) {
      await new Promise((r) => { slipp[m[1]] = r; });
      return { ok: true, status: 200, json: async () =>
        Object.assign({}, DETALJ, { utkast_id: m[1], policy_id: `pol-${m[1]}` }) };
    }
    return brukFetch(url, opts);
  };
  SVAR = { "/v1/policyutkast": TO_UTKAST, __post: async () => ({}) };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelectorAll("tbody button").length === 2);

  // Radrekkefølgen er tabellens, ikke vår: knappen hentes fra RADEN som bærer
  // riktig policy-id, ikke fra en antatt indeks.
  const aapne = (polId) => [...h.querySelectorAll("tbody tr")]
    .find((tr) => tr.textContent.includes(polId)).querySelector("button");
  aapne("faktura-no").dispatchEvent(new window.Event("click"));   // A = u-1
  await vent(() => slipp["u-1"]);
  aapne("lonn-no").dispatchEvent(new window.Event("click"));      // B = u-2
  await vent(() => slipp["u-2"]);

  slipp["u-2"]();                                  // B svarer først og tegnes
  await vent(() => h.textContent.includes("pol-u-2"));
  slipp["u-1"]();                                  // A svarer etterpå
  await vent(() => false, 20);                     // la svaret få tegne, om det vil

  assert.ok(h.textContent.includes("pol-u-2"),
    "det foreldede svaret for A rev bort utkastet brukeren valgte");
  assert.ok(!h.textContent.includes("pol-u-1"),
    "utkast A tegnet seg over utkast B");
  globalThis.fetch = brukFetch;
});

// Samme rot, andre vei: en «Prøv igjen» som fortsatt henger når brukeren har
// gått tilbake til lista, skal ikke dra henne inn i detaljen igjen.
test("Detalj: hengende «Prøv igjen» tegner ikke over lista man gikk tilbake til",
  async () => {
    let slipp = null;
    let runde = 0;
    const brukFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      if (url.split("?")[0] === "/v1/policyutkast/u-1") {
        if (++runde === 1) {
          return { ok: false, status: 500, json: async () => ({ feil: "x" }) };
        }
        await new Promise((r) => { slipp = r; });
        return { ok: true, status: 200, json: async () => DETALJ };
      }
      return brukFetch(url, opts);
    };
    SVAR = { "/v1/policyutkast": LISTE, __post: async () => ({}) };
    const h = nyHoved();
    visPolicyadmin(h, ctx());
    await vent(() => h.querySelector("tbody button"));
    h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
    await vent(() => h.querySelector(".tilstand.feil"));

    _finn(h, t("ui.prov_igjen")).dispatchEvent(new window.Event("click"));
    await vent(() => slipp);                       // forsøket er ute på nettet
    _finn(h, t("ui.policyadmin.tilbake_til_liste"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => h.querySelector("tbody"));

    slipp();
    await vent(() => false, 20);
    assert.ok(h.querySelector("tbody"),
      "det hengende forsøket dro brukeren ut av lista hun gikk tilbake til");
    globalThis.fetch = brukFetch;
  });
