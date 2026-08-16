// PR-013 CP7 — Policyadministrasjons-flaten (jsdom + axe).
// Diff + risikoklasse PER endring vises FØR aktivering; fire-øyne-status
// rendres; attestering går som CSRF-POST med Idempotency-Key og bærer diff_hash
// (godkjenneren attesterer diffen hun SÅ, ikke versjonsnummeret).
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visPolicyadmin } from "../static/js/flater/policyadmin.js";

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

let SVAR;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  if (opts && opts.method) return SVAR.__post(url, opts);
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
