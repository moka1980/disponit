// PR-013 CP7 — Policyadministrasjons-flaten (jsdom + axe).
// Diff + risikoklasse PER endring vises FØR aktivering; fire-øyne-status
// rendres; attestering går som CSRF-POST med Idempotency-Key og bærer diff_hash
// (godkjenneren attesterer diffen hun SÅ, ikke versjonsnummeret).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { NB, alvorligeBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visPolicyadmin } from "../static/js/flater/policyadmin.js";
import { lagRuter } from "../static/js/ruter.js";
import { el, sett } from "../static/js/dom.js";
import { meldLive } from "../static/js/komponenter.js";

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

// Kvitteringen kommer i TO trinn, og trinn to er en egen oppgave (se
// `kvitteringsBoks`): boksen settes inn tom, teksten kommer etterpå. Å vente på
// elementet alene er derfor å vente på halve kvitteringen — testene venter på
// den ferdige.
const _ventKvittering = async (h) => {
  await vent(() => {
    const k = h.querySelector(".pa-kvittering");
    return k && k.textContent.trim().length > 0;
  });
  return h.querySelector(".pa-kvittering");
};

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

// Codex P2: forrige fiks dekket bare SUKSESSVEIEN — `flyttFokus` ble konsumert
// av `tegn(...)`, som en avvist liste-GET aldri når. `medStatus` river den
// fokuserte tilbakeknappen synkront for lastetilstanden og tegner feilen, så
// «Tilbake» som feilet lot fokus ligge på `body`: eier måtte navigere seg fram
// til «Prøv igjen» forfra, i en flate hun nettopp sto i.
test("Tilbake som FEILER: fokus følger med til feiltilstanden", async () => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": DETALJ,
    __post: async () => ({}) };
  const h = nyHoved();
  await _aapneDetalj(h);

  delete SVAR["/v1/policyutkast"];               // lista svarer 404 nå
  const tilbake = _finn(h, t("ui.policyadmin.tilbake_til_liste"));
  tilbake.focus();                               // slik en tastaturbruker står
  tilbake.dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector(".tilstand.feil"));

  assert.notEqual(document.activeElement, document.body,
    "fokus falt til body — eier må navigere seg fram til «Prøv igjen» forfra");
  assert.ok(h.contains(document.activeElement), "fokus havnet utenfor flaten");
  assert.match(document.activeElement.tagName, /^H[12]$/,
    "fokus skal lande på feiltilstandens overskrift");

  // «Prøv igjen» bærer fokusløftet videre: knappen forsvinner i det den
  // trykkes, så et forsøk som lykkes skal lande der «Tilbake» skulle.
  SVAR["/v1/policyutkast"] = LISTE;
  _finn(h, t("ui.prov_igjen")).dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector("tbody"));
  assert.equal(document.activeElement.textContent, t("ui.policyadmin.tittel"),
    "fokus skal lande på listas overskrift etter et vellykket nytt forsøk");
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

// Codex P2: eierskapssjekken sto i `tegnFn`, altså bare på SUKSESSVEIEN. En
// liste-GET som ble avvist etter at brukeren hadde byttet rute, nådde aldri
// `tegnFn` — `medStatus` fanget avvisningen og tegnet policyadmins feiltilstand
// rett over ruten hun sto i.
test("Avvist liste-GET river ikke bort ruten brukeren har navigert til", async () => {
  let feil = null;
  const brukFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    if (url.split("?")[0] === "/v1/policyutkast") {
      await new Promise((r) => { feil = r; });
      throw new TypeError("nettverket falt ut");
    }
    return brukFetch(url, opts);
  };
  SVAR = { __post: async () => ({}) };
  const h = nyHoved();
  const annenFlate = (hoved) => sett(hoved, el("h1", { text: "annen flate" }));

  window.location.hash = "#/policyadmin";
  await vent(() => false, 5);
  const ruter = lagRuter(h, ctx(),
    { policyadmin: visPolicyadmin, annen: annenFlate }, () => {});
  ruter.naviger();
  await vent(() => feil);                          // liste-GET er ute på nettet

  // Ruteren kobles av med det samme, slik at ingen `hashchange` kan tegne
  // `annen` på nytt og vaske bort sporet etter det foreldede svaret.
  window.location.hash = "#/annen";
  ruter.naviger();
  ruter.stopp();
  assert.ok(h.textContent.includes("annen flate"));

  feil();
  await vent(() => false, 20);                     // la avvisningen få tegne, om den vil
  assert.ok(h.textContent.includes("annen flate"),
    "den avviste liste-GET-en rev bort ruten brukeren står i");
  assert.equal(h.querySelector(".tilstand.feil"), null,
    "policyadmins feiltilstand tegnet seg inn i en annen rute");
  globalThis.fetch = brukFetch;
});

// Codex P1: `paaFerdig` friskner opp detaljsiden når en handling er utført —
// men den sto ubetinget. Klikket eier «Valider» og så «Rediger», lå POST-en
// fortsatt ute mens editoren hadde tatt over `hoved`. Svaret kom, kalte
// `aapneDetalj`, og erstattet editoren med detaljsiden — sammen med det eier
// hadde rukket å skrive i den.
test("Valider som fullfører etter «Rediger» river ikke bort editoren", async () => {
  const gjenopprett = _medCsrf();
  let slipp = null;
  SVAR = {
    "/v1/policyutkast": { utkast: [{ utkast_id: "u-1", policy_id: "p",
      status: "utkast", utkastversjon: 2, opprettet: "2026-08-10T08:00:00+00:00" }] },
    "/v1/policyutkast/u-1": { ...DETALJ, status: "utkast", aktiv_runde: null,
      innhold: { meta: { policy_id: "p" } } },
    __post: async () => {
      await new Promise((r) => { slipp = r; });
      return { ok: true, status: 200, json: async () => ({ utfall: "gyldig" }) };
    },
  };
  const h = nyHoved();
  const side = await _aapneDetaljMed(h, t("ui.policyadmin.handling.valider"));
  _finn(side, t("ui.policyadmin.handling.valider"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => slipp);                     // valideringen er ute på nettet
  _finn(side, t("ui.policyadmin.handling.rediger"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => h.textContent.includes(t("ui.editor.tittel")));

  slipp();
  await vent(() => false, 20);                 // la svaret få tegne, om det vil
  assert.ok(h.textContent.includes(t("ui.editor.tittel")),
    "valideringssvaret rev bort editoren eier sto i");
  assert.equal(_finn(h, t("ui.policyadmin.tilbake_til_liste")), undefined,
    "detaljsiden tegnet seg over editoren");
  gjenopprett();
});

// Codex P2: eierskapet stoppet ved editordøra. `aapneEditor` talte opp
// generasjonen, men editoren fikk aldri vite hva den skulle måles mot — og den
// tegner ingenting før utkastet er hentet. Detaljsiden med tilbakeknappen blir
// derfor stående mens GET-en er ute: rakk eier å trykke «Tilbake», lastet lista,
// og editorsvaret tegnet seg etterpå rett over den.
test("Editor: treigt utkastsvar tegner seg ikke over lista man gikk tilbake til",
  async () => {
    let slipp = null;
    let runde = 0;
    const brukFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      // Første GET er detaljsidens; den andre er editorens, og det er den som
      // skal henge mens eier finner veien tilbake.
      if (url.split("?")[0] === "/v1/policyutkast/u-1" && ++runde === 2) {
        await new Promise((r) => { slipp = r; });
        return { ok: true, status: 200, json: async () =>
          ({ ...DETALJ, status: "utkast", aktiv_runde: null,
             innhold: { meta: { policy_id: "p" } } }) };
      }
      return brukFetch(url, opts);
    };
    SVAR = {
      "/v1/policyutkast": { utkast: [{ utkast_id: "u-1", policy_id: "p",
        status: "utkast", utkastversjon: 2,
        opprettet: "2026-08-10T08:00:00+00:00" }] },
      "/v1/policyutkast/u-1": { ...DETALJ, status: "utkast", aktiv_runde: null,
        innhold: { meta: { policy_id: "p" } } },
      __post: async () => ({}),
    };
    const h = nyHoved();
    const side = await _aapneDetaljMed(h, t("ui.policyadmin.handling.rediger"));
    _finn(side, t("ui.policyadmin.handling.rediger"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => slipp);                   // editorens GET er ute på nettet

    // Detaljsiden står fremdeles — editoren har ikke tegnet noe ennå, og
    // tilbakeknappen er dermed fortsatt eiers.
    _finn(h, t("ui.policyadmin.tilbake_til_liste"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => h.querySelector("tbody"));

    slipp();
    await vent(() => false, 20);               // la svaret få tegne, om det vil
    assert.ok(h.querySelector("tbody"),
      "editorsvaret rev bort lista eier gikk tilbake til");
    assert.ok(!h.textContent.includes(t("ui.editor.tittel")),
      "editoren tegnet seg over lista");
    globalThis.fetch = brukFetch;
  });

// Codex P2: sorteringen er eiers valg, men den bodde i `DataTabell` — og den
// bygges på nytt hver gang lista lastes. «Tilbake» fra et utkast kastet dermed
// kolonne og retning, og den som gikk gjennom flere utkast måtte sortere på nytt
// for hvert eneste ett.
test("Tilbake til lista: kolonnevalget står igjen", async () => {
  SVAR = { "/v1/policyutkast": TO_UTKAST, "/v1/policyutkast/u-1": DETALJ,
    __post: async () => ({}) };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelectorAll("tbody button").length === 2);

  const sorter = () => [...h.querySelectorAll("th button")]
    .find((b) => b.textContent === t("ui.policyadmin.kol.policy"));
  sorter().dispatchEvent(new window.Event("click"));      // stigende
  sorter().dispatchEvent(new window.Event("click"));      // synkende
  const forsteRad = () => h.querySelector("tbody tr").textContent;
  assert.ok(forsteRad().includes("lonn-no"),
    "sorteringen slo ikke inn — testen måler ikke det den tror");

  // Raden hentes på policy-id, ikke på indeks: rekkefølgen er nettopp det som
  // er under test.
  [...h.querySelectorAll("tbody tr")]
    .find((tr) => tr.textContent.includes("faktura-no"))
    .querySelector("button").dispatchEvent(new window.Event("click"));
  await vent(() => _finn(h, t("ui.policyadmin.tilbake_til_liste")));
  _finn(h, t("ui.policyadmin.tilbake_til_liste"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector("tbody"));

  assert.ok(forsteRad().includes("lonn-no"),
    "«Tilbake» kastet kolonnevalget og ga serverrekkefølgen tilbake");
  const th = [...h.querySelectorAll("th")]
    .find((x) => x.textContent.includes(t("ui.policyadmin.kol.policy")));
  assert.equal(th.getAttribute("aria-sort"), "descending",
    "radene var sortert, men aria-sort sa «usortert» til skjermleseren");
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

// Eier attesterte, så ingenting skje, og meldte «jeg klikker, men får ikke
// beskjed at den er validert og attestert». Serveren hadde svart korrekt hele
// tiden — «venter_godkjennere», 1 avgitt av 2 — men svaret nådde aldri
// skjermen: det gikk til `meldLive` (aria-live), og et halvt sekund senere
// tegnet `paaFerdig()` hele siden på nytt.
//
// Kontroll for begge testene under: bytt `_settKvittering(...)` tilbake til
// `meldLive(...)`, eller la `detaljInnhold` slutte å tegne `taKvittering()`,
// så blir de røde. Å asserte på et aria-live-område ville IKKE fanget feilen —
// den gamle koden fylte jo nettopp det.
test("Attester: utfallet er SYNLIG etter gjentegningen, med antall som gjenstår",
  async () => {
    SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": DETALJ,
      __post: async () => ({ ok: true, status: 200, json: async () => ({
        utfall: "venter_godkjennere", antall: 1, gjenstaar: 1,
        mangler_uavhengig: true }) }) };
    const h = nyHoved();
    visPolicyadmin(h, ctx());
    await vent(() => h.querySelector("tbody button"));
    h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
    await vent(() => _finn(h, t("ui.policyadmin.tilbake_til_liste")));
    _finn(h, t("ui.policyadmin.handling.attester"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => [...document.querySelectorAll('[role="dialog"]')]
      .some((d) => d.textContent.includes(t("ui.policyadmin.du_aktiverer"))));
    const bek = [...document.querySelectorAll('[role="dialog"]')]
      .find((d) => d.textContent.includes(t("ui.policyadmin.du_aktiverer")));
    [...bek.querySelectorAll("button")]
      .find((b) => b.textContent.trim() === t("ui.policyadmin.handling.attester"))
      .dispatchEvent(new window.Event("click"));

    // Kvitteringen skal stå i den NYE siden — altså etter at detaljen er hentet
    // og `sett(hoved, …)` har byttet ut alt innholdet.
    const kvitt = await _ventKvittering(h);
    assert.ok(kvitt, "utfallet av attesteringen er ikke synlig noe sted");
    assert.equal(kvitt.getAttribute("role"), "status",
      "utfallet må annonseres av seg selv, politt");
    assert.ok(kvitt.textContent.includes(
      t("ui.policyadmin.utfall.venter_godkjennere")));
    // Tallene er hele poenget: «venter på flere godkjennere» uten «1 av 2»
    // sier ikke om det står på deg eller på noen andre.
    assert.ok(kvitt.textContent.includes("1"), "antall avgitt/gjenstår mangler");
    assert.ok(kvitt.textContent.includes(
      t("ui.policyadmin.utfall.mangler_uavhengig")),
    "at det MÅ være en annen enn forfatteren, er den avgjørende opplysningen");
    assert.ok(!kvitt.textContent.includes("{avgitt}")
      && !kvitt.textContent.includes("{gjenstaar}"),
    "plassholderne er ikke fylt inn");
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
  });

test("Valider: et gyldig utkast gir en synlig kvittering, ikke bare stillhet",
  async () => {
    const utkast = { ...DETALJ, status: "utkast", aktiv_runde: null };
    SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": utkast,
      __post: async () => ({ ok: true, status: 200, json: async () => ({}) }) };
    const h = nyHoved();
    visPolicyadmin(h, ctx());
    await vent(() => h.querySelector("tbody button"));
    h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
    await vent(() => _finn(h, t("ui.policyadmin.handling.valider")));
    _finn(h, t("ui.policyadmin.handling.valider"))
      .dispatchEvent(new window.Event("click"));
    const kvitt = await _ventKvittering(h);
    assert.ok(kvitt, "«Valider» ga ingen synlig tilbakemelding");
    assert.equal(kvitt.textContent.trim(), t("ui.policyadmin.validert"));
    assert.equal(kvitt.getAttribute("role"), "status");
    // Suksess og feil skal ikke se like ut.
    assert.ok(kvitt.classList.contains("pa-kvittering-ok"));
    assert.equal(h.querySelectorAll(".pa-valfeil").length, 0,
      "et gyldig utkast skal ikke vise feilboksen");
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
  });

// Kvitteringen hører til handlingen som nettopp skjedde. Uten «forbruk én
// gang» ville den blitt hengende igjen og bekreftet en attestering på nytt
// hver gang siden ble tegnet av en helt annen grunn.
test("Kvitteringen vises ÉN gang, ikke ved neste gjentegning", async () => {
  const utkast = { ...DETALJ, status: "utkast", aktiv_runde: null };
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": utkast,
    __post: async () => ({ ok: true, status: 200, json: async () => ({}) }) };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => _finn(h, t("ui.policyadmin.handling.valider")));
  _finn(h, t("ui.policyadmin.handling.valider"))
    .dispatchEvent(new window.Event("click"));
  await _ventKvittering(h);
  // Tilbake til lista og inn igjen: en NY tegning av samme detalj.
  _finn(h, t("ui.policyadmin.tilbake_til_liste"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => _finn(h, t("ui.policyadmin.handling.valider")));
  assert.equal(h.querySelectorAll(".pa-kvittering").length, 0,
    "kvitteringen ble hengende igjen og bekrefter noe som ikke skjedde nå");
});

// Codex P2: `rebasering_kreves` og `semantikk_endret` er TERMINALE utfall —
// serveren har kansellert aktiveringsrunden og krever en ny handling av eier —
// men de kommer som 200. De traff derfor `.then`, der alt som ikke var
// `aktivert` ble klassifisert som «vent», og en kansellert runde fikk samme
// rolige ventestil som en runde som går sin gang. Samme `rebasering_kreves`
// ble vist som FEIL når den kom som `ApiFeil`: ett utfall, to farger, og den
// villedende av dem sa «len deg tilbake» til noen som må åpne ny runde.
//
// Kontroll: sett `UTFALLSART` tilbake til «alt som ikke er aktivert = vent», så
// blir testen rød.
const _attesterMedPost = async (h, post) => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": DETALJ,
    __post: post };
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => _finn(h, t("ui.policyadmin.handling.attester")));
  _finn(h, t("ui.policyadmin.handling.attester"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => [...document.querySelectorAll('[role="dialog"]')]
    .some((d) => d.textContent.includes(t("ui.policyadmin.du_aktiverer"))));
  const bek = [...document.querySelectorAll('[role="dialog"]')]
    .find((d) => d.textContent.includes(t("ui.policyadmin.du_aktiverer")));
  [...bek.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === t("ui.policyadmin.handling.attester"))
    .dispatchEvent(new window.Event("click"));
  return _ventKvittering(h);
};

const _attesterMedUtfall = (h, svar) => _attesterMedPost(h,
  async () => ({ ok: true, status: 200, json: async () => svar }));

// Codex P2: terskelen har TO betingelser, men serverens `gjenstaar` teller bare
// den ene. Krever runden én godkjenning (INNSNEVRER/NØYTRAL) og forfatteren
// attesterer først, svarer serveren `antall: 1, gjenstaar: 0,
// mangler_uavhengig: true` — talloppgjøret er oppfylt, uavhengighetskravet er
// det ikke. Kvitteringen sa da «0 gjenstår» og fortsatte med at det mangler en
// uavhengig godkjenner: to påstander som ikke kan være sanne samtidig, der
// tallet — det eier leser først — var det som løy.
//
// Kontroll: send `svar.gjenstaar` rett inn i teksten igjen, så blir testen rød.
test("Attester: uavhengighetskravet teller med i det som gjenstår", async () => {
  const kvitt = await _attesterMedUtfall(nyHoved(), {
    utfall: "venter_godkjennere", antall: 1, gjenstaar: 0,
    mangler_uavhengig: true });
  assert.ok(kvitt.textContent.includes(
    t("ui.policyadmin.utfall.venter_antall")
      .replace("{avgitt}", "1").replace("{gjenstaar}", "1")),
  "kvitteringen sa «0 gjenstår» og krevde en godkjenner til i samme setning");
  assert.ok(kvitt.textContent.includes(
    t("ui.policyadmin.utfall.mangler_uavhengig")),
  "hvem den siste godkjenningen må komme fra, er fortsatt poenget");
});

// Er talloppgjøret det strengeste kravet, er det talloppgjøret som vises: den
// ene som gjenstår, må uansett være en annen enn forfatteren.
test("Attester: to som gjenstår blir ikke rundet ned til uavhengighetskravet",
  async () => {
    const kvitt = await _attesterMedUtfall(nyHoved(), {
      utfall: "venter_godkjennere", antall: 1, gjenstaar: 2,
      mangler_uavhengig: true });
    assert.ok(kvitt.textContent.includes(
      t("ui.policyadmin.utfall.venter_antall")
        .replace("{avgitt}", "1").replace("{gjenstaar}", "2")));
  });

test("Attester: kansellert runde er en FEIL, ikke en ventetilstand", async () => {
  const kvitt = await _attesterMedUtfall(nyHoved(),
    { utfall: "rebasering_kreves" });
  assert.ok(kvitt.textContent.includes(
    t("ui.policyadmin.utfall.rebasering_kreves")));
  assert.ok(kvitt.classList.contains("pa-kvittering-feil"),
    "en kansellert runde ble vist med ventestil — eier tror hun kan vente");
  assert.ok(!kvitt.classList.contains("pa-kvittering-vent"));
  assert.equal(kvitt.getAttribute("role"), "alert",
    "et terminalt utfall krever handling og skal ikke annonseres politt");
});

test("Attester: semantikkendring er en FEIL, ikke en ventetilstand", async () => {
  const kvitt = await _attesterMedUtfall(nyHoved(),
    { utfall: "semantikk_endret" });
  assert.ok(kvitt.textContent.includes(
    t("ui.policyadmin.utfall.semantikk_endret")));
  assert.ok(kvitt.classList.contains("pa-kvittering-feil"));
});

// Codex P2: en usynk peker er den ENE feilen der et nytt forsøk er nytteløst —
// dataene må repareres av noen med tilgang til basen. Sa flaten «Handlingen
// feilet», sendte den eier inn i en runde med klikk som aldri kan lykkes, og
// den nye teksten om reparasjon ble aldri vist til noen.
//
// Utfallet kommer i TO former: som 200 med `utfall` (attestasjonen ble tatt
// imot, men aktiveringen kolliderte) og som 409 `ApiFeil` (serveren nektet å ta
// imot attestasjonen i det hele tatt). Begge må si det samme.
test("Attester: usynk peker sier at dataene må repareres (utfall)", async () => {
  const kvitt = await _attesterMedUtfall(nyHoved(),
    { utfall: "aktiv_peker_usynk" });
  assert.ok(kvitt.textContent.includes(
    t("ui.policyadmin.utfall.aktiv_peker_usynk")),
  "eier fikk ikke vite at det er dataene, ikke handlingen, som må rettes");
  assert.ok(kvitt.classList.contains("pa-kvittering-feil"));
  assert.equal(kvitt.getAttribute("role"), "alert");
});

test("Attester: usynk peker som 409 sier det samme som utfallet", async () => {
  const kvitt = await _attesterMedPost(nyHoved(), async () => ({
    ok: false, status: 409,
    json: async () => ({ feil: "aktiv_peker_usynk" }) }));
  assert.ok(kvitt.textContent.includes(
    t("ui.policyadmin.utfall.aktiv_peker_usynk")),
  "409-en falt til «Handlingen feilet» og skjulte at retry er nytteløst");
  assert.ok(!kvitt.textContent.includes(t("ui.policyadmin.feilet")));
  assert.ok(kvitt.classList.contains("pa-kvittering-feil"));
});

test("Åpne runde: usynk peker sier at dataene må repareres", async () => {
  const gjenopprett = _medCsrf();
  SVAR = {
    "/v1/policyutkast": { utkast: [{ utkast_id: "u-1", policy_id: "p",
      status: "validert", utkastversjon: 2,
      opprettet: "2026-08-10T08:00:00+00:00" }] },
    "/v1/policyutkast/u-1": { ...DETALJ, status: "validert", aktiv_runde: null },
    __post: async () => ({ ok: false, status: 409,
      json: async () => ({ feil: "aktiv_peker_usynk" }) }),
  };
  const h = await _aapneDetaljMed(nyHoved(),
    t("ui.policyadmin.handling.apne_runde"));
  _finn(h, t("ui.policyadmin.handling.apne_runde"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector(".pa-kvittering-feil"));
  const tekst = h.querySelector(".pa-kvittering-feil").textContent;
  assert.ok(tekst.includes(t("ui.policyadmin.utfall.aktiv_peker_usynk")),
    "runden kan ikke åpnes på en ødelagt base — det må stå her");
  assert.ok(!tekst.includes(t("ui.policyadmin.feilet")));
  gjenopprett();
});

// Codex P1: versjonen registeret lagrer er utkastets EGEN `meta.versjon`. Er
// den brukt fra før, ikke nyere enn den aktive, eller ikke der i det hele tatt,
// kan utkastet ikke aktiveres — og INGEN mengde klikk endrer det. Handlingen
// eier må gjøre er å øke versjonen i utkastet, så den setningen må fram.
test("Attester: brukt versjon sier at utkastet må få ny versjon (utfall)",
  async () => {
    const kvitt = await _attesterMedUtfall(nyHoved(),
      { utfall: "versjon_i_bruk" });
    assert.ok(kvitt.textContent.includes(
      t("ui.policyadmin.utfall.versjon_i_bruk")),
    "eier fikk ikke vite at det er versjonen i utkastet som må økes");
    assert.ok(kvitt.classList.contains("pa-kvittering-feil"));
    assert.equal(kvitt.getAttribute("role"), "alert");
  });

// Codex P2 på #63: `utkast_ugyldig` kommer også som UTFALL, ikke bare som
// feilkode — runden kan ha vært ferdig attestert da kravet kom, og da er det
// migrasjon 022 i DB-grensen som stopper aktiveringen. Utfallet deler SQLSTATE
// med versjonsinvariantene, så uten skillet ville eier fått «versjonen er i
// bruk» om en verifikator-id og økt versjonen uten at noe ble bedre.
test("Attester: utkast_ugyldig som utfall peker på innholdet, ikke versjonen",
  async () => {
    const kvitt = await _attesterMedUtfall(nyHoved(),
      { utfall: "utkast_ugyldig" });
    assert.ok(kvitt.textContent.includes(
      t("ui.policyadmin.utfall.utkast_ugyldig")),
    "eier fikk ikke vite at det er utkastets innhold som må rettes");
    assert.ok(!kvitt.textContent.includes(
      t("ui.policyadmin.utfall.versjon_i_bruk")),
    "eier ble sendt til versjonsnummeret for en feil i innholdet");
    assert.ok(kvitt.classList.contains("pa-kvittering-feil"));
    assert.equal(kvitt.getAttribute("role"), "alert");
  });

// Codex P2 på #63: `utkast_ugyldig` er 422, ikke 409 — det er INNHOLDET som er
// ugyldig, ikke tilstanden. Grunnlagsteksten må følge koden, ikke statusen.
test("Attester: grunnlagsfeil sier det samme som utfallet", async () => {
  for (const [kode, status] of [["versjon_i_bruk", 409],
    ["versjon_mangler", 409], ["utkast_ugyldig", 422]]) {
    const kvitt = await _attesterMedPost(nyHoved(), async () => ({
      ok: false, status, json: async () => ({ feil: kode }) }));
    assert.ok(kvitt.textContent.includes(t(`ui.policyadmin.utfall.${kode}`)),
      `${kode} falt til «Handlingen feilet» og skjulte hva som må rettes`);
    assert.ok(!kvitt.textContent.includes(t("ui.policyadmin.feilet")));
    assert.ok(kvitt.classList.contains("pa-kvittering-feil"));
  }
});

test("Åpne runde: brukt versjon sier hva som må rettes", async () => {
  const gjenopprett = _medCsrf();
  SVAR = {
    "/v1/policyutkast": { utkast: [{ utkast_id: "u-1", policy_id: "p",
      status: "validert", utkastversjon: 2,
      opprettet: "2026-08-10T08:00:00+00:00" }] },
    "/v1/policyutkast/u-1": { ...DETALJ, status: "validert", aktiv_runde: null },
    __post: async () => ({ ok: false, status: 409,
      json: async () => ({ feil: "versjon_i_bruk" }) }),
  };
  const h = await _aapneDetaljMed(nyHoved(),
    t("ui.policyadmin.handling.apne_runde"));
  _finn(h, t("ui.policyadmin.handling.apne_runde"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector(".pa-kvittering-feil"));
  const tekst = h.querySelector(".pa-kvittering-feil").textContent;
  assert.ok(tekst.includes(t("ui.policyadmin.utfall.versjon_i_bruk")),
    "ingen runde kan åpnes på et utkast som ikke kan lagres — det må stå her");
  assert.ok(!tekst.includes(t("ui.policyadmin.feilet")));
  gjenopprett();
});

// Codex P1: dokumentet må oppgi den policyen utkastet er registrert under. Gjør
// det ikke det, er hverken runden eller aktiveringen mulig — og «Handlingen
// feilet» ville sendt eier tilbake til knappen i stedet for til `meta.policy_id`
// i utkastet. Begge former må si det: 409 fra porten (`policy_id_avvik`) og
// utfallet fra en kansellert runde (`dokument_avvik`).
test("Attester: fremmed policy_id sier hva som må rettes (utfall)", async () => {
  const kvitt = await _attesterMedUtfall(nyHoved(),
    { utfall: "dokument_avvik" });
  assert.ok(kvitt.textContent.includes(
    t("ui.policyadmin.utfall.dokument_avvik")),
  "eier fikk ikke vite at det er identiteten i utkastet som er feil");
  assert.ok(kvitt.classList.contains("pa-kvittering-feil"));
  assert.equal(kvitt.getAttribute("role"), "alert");
});

test("Attester: dokumentavvik som 409 sier hva som må rettes", async () => {
  for (const kode of ["policy_id_avvik", "status_ikke_produksjon",
    "dokument_avvik"]) {
    const kvitt = await _attesterMedPost(nyHoved(), async () => ({
      ok: false, status: 409, json: async () => ({ feil: kode }) }));
    assert.ok(kvitt.textContent.includes(t(`ui.policyadmin.utfall.${kode}`)),
      `${kode} falt til «Handlingen feilet» og skjulte hva som må rettes`);
    assert.ok(!kvitt.textContent.includes(t("ui.policyadmin.feilet")));
    assert.ok(kvitt.classList.contains("pa-kvittering-feil"));
  }
});

test("Åpne runde: fremmed policy_id sier hva som må rettes", async () => {
  const gjenopprett = _medCsrf();
  SVAR = {
    "/v1/policyutkast": { utkast: [{ utkast_id: "u-1", policy_id: "p",
      status: "validert", utkastversjon: 2,
      opprettet: "2026-08-10T08:00:00+00:00" }] },
    "/v1/policyutkast/u-1": { ...DETALJ, status: "validert", aktiv_runde: null },
    __post: async () => ({ ok: false, status: 409,
      json: async () => ({ feil: "policy_id_avvik" }) }),
  };
  const h = await _aapneDetaljMed(nyHoved(),
    t("ui.policyadmin.handling.apne_runde"));
  _finn(h, t("ui.policyadmin.handling.apne_runde"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector(".pa-kvittering-feil"));
  const tekst = h.querySelector(".pa-kvittering-feil").textContent;
  assert.ok(tekst.includes(t("ui.policyadmin.utfall.policy_id_avvik")),
    "ingen runde kan åpnes på et utkast med fremmed identitet");
  assert.ok(!tekst.includes(t("ui.policyadmin.feilet")));
  gjenopprett();
});

// `vent` er reservert for det ENE utfallet der ventingen faktisk ER svaret. Et
// utfall flaten ikke kjenner, er ikke en bekreftet aktivering, og skal verken
// se ut som en eller som en rolig venting (fail-closed).
test("Attester: ukjent utfall bekrefter ingenting", async () => {
  const kvitt = await _attesterMedUtfall(nyHoved(), { utfall: "noe_nytt" });
  assert.ok(kvitt.classList.contains("pa-kvittering-feil"));
  assert.equal(kvitt.textContent.trim(), t("ui.policyadmin.utfall.ukjent"));
  assert.ok(!kvitt.textContent.includes(t("ui.policyadmin.utfall.aktivert")),
    "et ukjent utfall skal ikke påstå at policyen er aktivert");
});

// Codex P2: «ukjent» må gjelde ALLE ukjente navn — også de som tilfeldigvis
// finnes på `Object.prototype`. Med et objektliteral som kart svarte oppslaget
// på arvede navn, så `"constructor"` og `"__proto__"` slapp forbi
// `|| "feil"`-fallbacken: klassen ble ugyldig, og fordi arten ikke var strengen
// `"feil"`, ble kvitteringen politt i stedet for et varsel.
//
// Kontroll: bytt `UTFALLSART` tilbake til et objektliteral med `[utfall]`, så
// blir testen rød.
for (const navn of ["constructor", "toString", "__proto__"]) {
  test(`Attester: «${navn}» er et ukjent utfall som alle andre`, async () => {
    const kvitt = await _attesterMedUtfall(nyHoved(), { utfall: navn });
    assert.ok(kvitt.classList.contains("pa-kvittering-feil"),
      "et arvet prototypenavn slapp forbi fail-closed-fallbacken");
    assert.equal(kvitt.getAttribute("role"), "alert",
      "et ukjent utfall skal varsles, ikke hviskes politt");
    assert.equal(kvitt.textContent.trim(), t("ui.policyadmin.utfall.ukjent"));
  });
}

// Codex P2: et `role="status"` annonserer ikke pålitelig tekst som lå der
// allerede da området kom inn i tilgjengelighetstreet — den oppførselen er det
// bare `role="alert"` som vanligvis får. Bygde vi kvitteringsboksen ferdig
// utfylt og satte hele undertreet inn i ett jafs, kunne altså nettopp de
// POSITIVE utfallene bli tause for skjermleseren: synlig på skjermen, stille i
// lyd. Regionen skal derfor stå i dokumentet FØRST, og teksten komme som en
// egen endring etterpå.
//
// Codex P2 igjen, et hakk dypere: to DOM-endringer i SAMME oppgave beviser
// ingenting. Nettleseren oppdaterer tilgjengelighetstreet mellom oppgaver, så
// rakk den ikke å se regionen tom, er «tom → utfylt» ikke en endring i et
// registrert live-område — bare en ferdig utfylt region som dukker opp, altså
// nøyaktig det tause tilfellet. Rekkefølgen på mutasjonene ville sett riktig ut
// likevel.
//
// Testen måler derfor OPPGAVESKILLET, ikke rekkefølgen. En MutationObserver
// kjører som mikrooppgave på slutten av oppgaven som endret DOM-en: står
// teksten allerede der når innsettingen meldes, skjedde begge delene i samme
// oppgave. Kontroll: bytt `fyll` tilbake til et synkront `sett(linje, …)`, så
// blir testen rød.
test("Live-området rekker å bli registrert før teksten kommer", async () => {
  const utkast = { ...DETALJ, status: "utkast", aktiv_runde: null };
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": utkast,
    __post: async () => ({ ok: true, status: 200, json: async () => ({}) }) };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => _finn(h, t("ui.policyadmin.handling.valider")));

  let tekstVedInnsetting = null;
  const obs = new window.MutationObserver((poster) => {
    for (const p of poster) {
      const boks = [...p.addedNodes].find((n) => n.nodeType === 1
        && (n.classList?.contains("pa-kvittering")
          || n.querySelector?.(".pa-kvittering")));
      if (!boks || tekstVedInnsetting !== null) continue;
      const kv = boks.classList.contains("pa-kvittering")
        ? boks : boks.querySelector(".pa-kvittering");
      tekstVedInnsetting = kv.textContent;
    }
  });
  obs.observe(h, { childList: true, subtree: true, characterData: true });
  _finn(h, t("ui.policyadmin.handling.valider"))
    .dispatchEvent(new window.Event("click"));
  const kvitt = await _ventKvittering(h);
  obs.disconnect();

  assert.equal(kvitt.getAttribute("role"), "status");
  assert.equal(kvitt.textContent.trim(), t("ui.policyadmin.validert"),
    "teksten kom aldri inn i live-området");
  assert.notEqual(tekstVedInnsetting, null,
    "kvitteringen ble aldri satt inn i `hoved`");
  assert.equal(tekstVedInnsetting, "",
    "teksten sto der alt da innsettingen ble meldt — begge delene skjedde i "
    + "samme oppgave, og da kan tilgjengelighetstreet aldri ha sett regionen "
    + "tom");
});

// Eier P1 / Codex P2: kvitteringen var en naken modulglobal uten identitet.
// Attesterte man A og gikk tilbake mens gjentegningen av A fortsatt var ute på
// nettet, avviste eierskapssjekken den tegningen — men kvitteringen ble
// liggende igjen i modulen. Neste utkast som ble tegnet, forbrukte den, og
// skjermen bekreftet «attestert — venter på godkjennere» på FEIL policy. I en
// styringsflate er det den verste formen for feil: den bekrefter en
// fullmaktshandling på et annet objekt enn det handlingen traff.
//
// Kontroll: la `taKvittering` slutte å kreve `uid`, eller flytt kallet tilbake
// ned i `detaljInnhold`, så blir testen rød.
test("Kvitteringen for A lekker ikke til B når A-tegningen aldri kom fram",
  async () => {
    let slippA = null;
    let aRunde = 0;
    const brukFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const sti = url.split("?")[0];
      if (sti === "/v1/policyutkast/u-1" && !(opts && opts.method)) {
        // Første GET tegner detaljen. Den ANDRE er gjentegningen `paaFerdig()`
        // starter etter attesteringen — den holdes ute på nettet.
        if (++aRunde === 2) await new Promise((r) => { slippA = r; });
        return { ok: true, status: 200, json: async () => DETALJ };
      }
      if (sti === "/v1/policyutkast/u-2") {
        return { ok: true, status: 200, json: async () => Object.assign(
          {}, DETALJ, { utkast_id: "u-2", policy_id: "lonn-no" }) };
      }
      return brukFetch(url, opts);
    };
    SVAR = { "/v1/policyutkast": TO_UTKAST,
      __post: async () => ({ ok: true, status: 200, json: async () => ({
        utfall: "venter_godkjennere", antall: 1, gjenstaar: 1,
        mangler_uavhengig: true }) }) };
    const h = nyHoved();
    visPolicyadmin(h, ctx());
    await vent(() => h.querySelectorAll("tbody button").length === 2);
    const aapne = (polId) => [...h.querySelectorAll("tbody tr")]
      .find((tr) => tr.textContent.includes(polId)).querySelector("button");

    aapne("faktura-no").dispatchEvent(new window.Event("click"));   // A = u-1
    await vent(() => _finn(h, t("ui.policyadmin.handling.attester")));
    _finn(h, t("ui.policyadmin.handling.attester"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => [...document.querySelectorAll('[role="dialog"]')]
      .some((d) => d.textContent.includes(t("ui.policyadmin.du_aktiverer"))));
    const bek = [...document.querySelectorAll('[role="dialog"]')]
      .find((d) => d.textContent.includes(t("ui.policyadmin.du_aktiverer")));
    [...bek.querySelectorAll("button")]
      .find((b) => b.textContent.trim() === t("ui.policyadmin.handling.attester"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => slippA);            // gjentegningen av A er ute på nettet

    // Eier gir opp å vente og går tilbake, så inn i et ANNET utkast.
    _finn(h, t("ui.policyadmin.tilbake_til_liste"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => h.querySelectorAll("tbody button").length === 2);
    aapne("lonn-no").dispatchEvent(new window.Event("click"));      // B = u-2
    await vent(() => h.textContent.includes("lonn-no")
      && _finn(h, t("ui.policyadmin.tilbake_til_liste")));

    assert.equal(h.querySelectorAll(".pa-kvittering").length, 0,
      "attesteringen av A ble kvittert ut på utkast B");

    // Og den skal heller ikke dukke opp når det foreldede A-svaret lander.
    slippA();
    await vent(() => false, 20);
    assert.equal(h.querySelectorAll(".pa-kvittering").length, 0,
      "det foreldede A-svaret tegnet kvitteringen sin inn i utkast B");
    assert.ok(h.textContent.includes("lonn-no"),
      "det foreldede A-svaret rev bort utkastet eier står i");
    globalThis.fetch = brukFetch;
  });

// Codex P2: forlot eier detaljsiden mens POST-en fortsatt lå ute, avviste
// eierskapssjekken oppfriskningen — riktig, siden gjentegningen ellers ville
// revet bort det hun sto i. Men kvitteringen var alt lagt igjen i modulen, og
// uten en gjentegning ble den ALDRI forbrukt: eier fikk null tilbakemelding på
// en fullmaktshandling som faktisk ble utført, og kvitteringen ble liggende og
// vente på en tilfeldig senere tegning av samme utkast — der den ville dukket
// opp som om utfallet var ferskt.
//
// Kontroll: la `paaFerdig` gå tilbake til `if (eierSkjermen(min)) …` alene, så
// blir begge påstandene under røde.
test("Et utfall som ikke kan VISES, blir i det minste lest opp", async () => {
  let slippPost = null;
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": DETALJ,
    __post: async () => {
      await new Promise((r) => { slippPost = r; });
      return { ok: true, status: 200, json: async () => ({
        utfall: "venter_godkjennere", antall: 1, gjenstaar: 1,
        mangler_uavhengig: false }) };
    } };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => _finn(h, t("ui.policyadmin.handling.attester")));
  _finn(h, t("ui.policyadmin.handling.attester"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => [...document.querySelectorAll('[role="dialog"]')]
    .some((d) => d.textContent.includes(t("ui.policyadmin.du_aktiverer"))));
  const bek = [...document.querySelectorAll('[role="dialog"]')]
    .find((d) => d.textContent.includes(t("ui.policyadmin.du_aktiverer")));
  [...bek.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === t("ui.policyadmin.handling.attester"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => slippPost);          // attesteringen er ute på nettet

  // Eier venter ikke — hun går tilbake til lista mens POST-en henger.
  _finn(h, t("ui.policyadmin.tilbake_til_liste"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector("tbody"));
  slippPost();
  await vent(() => false, 20);

  const live = [...document.querySelectorAll('[aria-live="polite"]')]
    .map((n) => n.textContent).join(" ");
  assert.ok(live.includes(t("ui.policyadmin.utfall.venter_godkjennere")),
    "attesteringen ble utført, men eier fikk aldri vite utfallet");
  assert.equal(h.querySelectorAll(".pa-kvittering").length, 0,
    "gjentegningen skulle IKKE hente eier tilbake til detaljsiden");

  // Og den skal ikke ligge igjen og dukke opp neste gang utkastet åpnes.
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => _finn(h, t("ui.policyadmin.handling.attester")));
  assert.equal(h.querySelectorAll(".pa-kvittering").length, 0,
    "et gammelt utfall ble vist som om det var ferskt");
});

// Codex P2: kvitteringen ble forbrukt da tegningen startet, men feilgrenen
// tegnet den aldri. Lyktes handlingen mens den påfølgende detalj-GET-en feilet,
// fikk eier INGEN tilbakemelding på noe som FAKTISK ble utført — bare en naken
// feiltilstand — og «Prøv igjen» kunne ikke hente kvitteringen tilbake, for den
// var borte for godt. Det nærliggende neste trekket er da å gjøre handlingen om
// igjen. Utfallet av HANDLINGEN og utfallet av OPPFRISKNINGEN er to
// forskjellige ting, og begge er sanne samtidig.
//
// Kontroll: fjern tegningen av `boks` i `.catch`-grenen, så blir testen rød.
test("Kvitteringen overlever at gjentegningen feiler", async () => {
  let gets = 0;
  SVAR = {
    "/v1/policyutkast": LISTE,
    "/v1/policyutkast/u-1": { ...DETALJ, status: "utkast", aktiv_runde: null },
    // Første GET tegner detaljen. Den ANDRE er gjentegningen `paaFerdig()`
    // starter etter valideringen — den svarer 404.
    __get: (sti) => {
      if (sti === "/v1/policyutkast/u-1" && ++gets === 2) delete SVAR[sti];
    },
    __post: async () => ({ ok: true, status: 200, json: async () => ({}) }),
  };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => _finn(h, t("ui.policyadmin.handling.valider")));
  _finn(h, t("ui.policyadmin.handling.valider"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector(".tilstand.feil"));

  const kvitt = await _ventKvittering(h);
  assert.ok(kvitt,
    "handlingen ble utført, men eier fikk ingen kvittering — da gjør hun den om");
  assert.equal(kvitt.textContent.trim(), t("ui.policyadmin.validert"),
    "kvitteringen ble tegnet tom");
  assert.ok(kvitt.classList.contains("pa-kvittering-ok"),
    "det var oppfriskningen som feilet, ikke handlingen");
  // Feiltilstanden skal fortsatt stå: den sier noe annet enn kvitteringen.
  assert.ok(_finn(h, t("ui.prov_igjen")),
    "veien ut av den feilede gjentegningen forsvant");
  assert.ok(_finn(h, t("ui.policyadmin.tilbake_til_liste")));
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

// Codex P2: å ta kvitteringen ut når tegningen STARTER lukket ett tapsvindu og
// åpnet et annet rett etter. Fullførte POST-en mens eier fortsatt sto i
// detaljen, kalte `paaFerdig` riktignok `aapneDetalj` — men da er kvitteringen
// alt tatt ut av modulen og eid av en tegning som ennå bare er en GET på
// nettet. Gikk eier tilbake i det sekundet, returnerte både `.then` og
// `.catch` på eierskapssjekken, og kvitteringen var borte for godt: ikke feil
// kvittering, ikke gammel kvittering — INGEN. Eier fikk null tilbakemelding på
// en fullmaktshandling som faktisk ble utført, og det nærliggende neste
// trekket er da å gjøre den om igjen.
//
// Dette er et ANNET vindu enn det `paaFerdig` alt dekker: der navigerte eier
// FØR POST-en var i havn, her etter. Den grenen redder ikke dette tilfellet.
//
// Kontroll: fjern `meldTaptKvittering(kvitt)` fra eierskapsreturen i `.then`,
// så blir testen rød.
test("Utfallet leses opp selv om gjentegningen mister skjermen underveis",
  async () => {
    let slippGet = null;
    let gets = 0;
    const utkast = { ...DETALJ, status: "utkast", aktiv_runde: null };
    SVAR = {
      "/v1/policyutkast": LISTE,
      "/v1/policyutkast/u-1": utkast,
      __post: async () => ({ ok: true, status: 200, json: async () => ({}) }),
    };
    const brukFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const sti = url.split("?")[0];
      // Første GET tegner detaljen. Den ANDRE er gjentegningen `paaFerdig()`
      // starter etter valideringen — POST-en er da ferdig og kvitteringen tatt
      // ut av modulen, mens GET-en holdes ute på nettet.
      if (sti === "/v1/policyutkast/u-1" && !(opts && opts.method)
          && ++gets === 2) {
        await new Promise((r) => { slippGet = r; });
        return { ok: true, status: 200, json: async () => utkast };
      }
      return brukFetch(url, opts);
    };
    const h = nyHoved();
    visPolicyadmin(h, ctx());
    await vent(() => h.querySelector("tbody button"));
    h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
    await vent(() => _finn(h, t("ui.policyadmin.handling.valider")));

    meldLive("");                        // ren startlinje å måle mot
    _finn(h, t("ui.policyadmin.handling.valider"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => slippGet);          // POST i havn, gjentegningen ute

    // Eier venter ikke på gjentegningen — hun går tilbake til lista.
    _finn(h, t("ui.policyadmin.tilbake_til_liste"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => h.querySelector("tbody"));
    slippGet();
    await vent(() => false, 20);

    const live = [...document.querySelectorAll('[aria-live="polite"]')]
      .map((n) => n.textContent).join(" ");
    assert.ok(live.includes(t("ui.policyadmin.validert")),
      "valideringen ble utført, men utfallet forsvant med tegningen");
    assert.equal(h.querySelectorAll(".pa-kvittering").length, 0,
      "det foreldede svaret hentet eier tilbake til detaljsiden");

    // Og den skal ikke ligge igjen og dukke opp neste gang utkastet åpnes.
    h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
    await vent(() => _finn(h, t("ui.policyadmin.handling.valider")));
    await vent(() => false, 20);
    assert.equal(h.querySelectorAll(".pa-kvittering").length, 0,
      "et gammelt utfall ble vist som om det var ferskt");
    globalThis.fetch = brukFetch;
  });

// Codex P2: «Åpne runde» friskner ikke opp siden ved feil — feilen tegnes rett
// i handlingsboksen, som skal være synlig og ikke bare hørbar. Men boksen hører
// til DEN tegningen som lagde den, og POST-en er ute på nettet mens eier
// fortsatt kan klikke. Rakk hun «Tilbake» først, hadde `sett` byttet ut hele
// `hoved`, og alerten ble hengt inn i et frakoblet tre: usynlig fordi det ikke
// står på skjermen, OG stumt fordi `role="alert"` ikke annonserer noe utenfor
// dokumentet. Da feilen ble gjort synlig, mistet den altså `meldLive` — det
// ene sporet som overlevde navigasjon — og en mislykket handling ble helt
// stille. Eier tror runden er åpnet.
//
// Kontroll: bytt `visEllerMeld` tilbake til et rått `boks.append(…)`, så blir
// testen rød.
test("En handling som feiler etter at eier har gått videre, blir hørt",
  async () => {
    const gjenopprett = _medCsrf();
    let slippPost = null;
    SVAR = {
      "/v1/policyutkast": LISTE,
      // Validert uten runde → «Åpne runde».
      "/v1/policyutkast/u-1": { ...DETALJ, aktiv_runde: null },
      __post: async () => {
        await new Promise((r) => { slippPost = r; });
        return { ok: false, status: 500, json: async () => ({ feil: "x" }) };
      },
    };
    const h = await _aapneDetaljMed(nyHoved(),
      t("ui.policyadmin.handling.apne_runde"));

    meldLive("");                        // ren startlinje å måle mot
    _finn(h, t("ui.policyadmin.handling.apne_runde"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => slippPost);         // POST-en er ute på nettet

    // Eier venter ikke på svaret — hun går tilbake til lista.
    _finn(h, t("ui.policyadmin.tilbake_til_liste"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => h.querySelector("tbody"));
    slippPost();
    await vent(() => false, 20);

    const live = [...document.querySelectorAll('[aria-live="polite"]')]
      .map((n) => n.textContent).join(" ");
    assert.ok(live.includes(t("ui.policyadmin.feilet")),
      "runden ble ikke åpnet, og eier fikk ingen beskjed om det");
    assert.equal(h.querySelectorAll(".pa-kvittering-feil").length, 0,
      "feilen rev eier tilbake til en side hun hadde forlatt");
    gjenopprett();
  });

// Samme frakobling gjelder valideringens feilliste: også den tegnes rett i
// handlingsboksen, og også den kan svare etter at eier har forlatt siden.
// Kontroll: bytt `visEllerMeld` i `visFeil` tilbake til `boks.append(…)`.
test("Valideringsfeil som lander etter navigasjon, blir hørt", async () => {
  const gjenopprett = _medCsrf();
  let slippPost = null;
  SVAR = {
    "/v1/policyutkast": LISTE,
    "/v1/policyutkast/u-1": { ...DETALJ, status: "utkast", aktiv_runde: null },
    __post: async () => {
      await new Promise((r) => { slippPost = r; });
      return { ok: false, status: 422, json: async () => ({
        feil: "policy_ugyldig", detaljer: ["rolle mangler"] }) };
    },
  };
  const h = await _aapneDetaljMed(nyHoved(),
    t("ui.policyadmin.handling.valider"));

  meldLive("");
  _finn(h, t("ui.policyadmin.handling.valider"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => slippPost);
  _finn(h, t("ui.policyadmin.tilbake_til_liste"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector("tbody"));
  slippPost();
  await vent(() => false, 20);

  const live = [...document.querySelectorAll('[aria-live="polite"]')]
    .map((n) => n.textContent).join(" ");
  assert.ok(live.includes(t("ui.policyadmin.ugyldig")),
    "utkastet var ugyldig, men eier fikk aldri vite det");
  assert.ok(live.includes("rolle mangler"),
    "serverens egen feilliste forsvant med den frakoblede boksen");
  assert.equal(h.querySelectorAll(".pa-valfeil").length, 0,
    "feillista ble tegnet inn i en side eier hadde forlatt");
  gjenopprett();
});

// Diffen er det godkjenneren BINDER SEG TIL. En ny policy ga ~200 flate rader
// («handlinger[0].vilkaar[2].verifikator · added: "v_prognose"»), og det
// spørsmålet et menneske skal svare på — hva får agenten lov til, og opp til
// hvilket beløp — lå begravd. Eier meldte den som «en lang liste, litt
// vanskelig å forholde seg til».
//
// Kravet er derfor TO ting samtidig: den skal være til å lese, OG ingenting
// skal forsvinne. Den siste er den viktige — en diff som skjuler noe gjør
// attesteringen til en løgn.
const STOR_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [
    { sti: "handlinger[]", klasse: "UTVIDER" },
    { sti: "roller[]", klasse: "UTVIDER" },
    { sti: "unntak.kategorier[]", klasse: "UTVIDER" },
  ],
  diff: { endringer: [
    { sti: "handlinger[0].id", type: "lagt_til", til: "ordre.bekreft_og_fakturer" },
    { sti: "handlinger[0].modul", type: "lagt_til", til: "M-25" },
    { sti: "handlinger[0].modus", type: "lagt_til", til: "auto" },
    { sti: "handlinger[0].vilkaar[0].navn", type: "lagt_til", til: "betaling_autorisert" },
    { sti: "handlinger[1].id", type: "lagt_til", til: "refusjon.utfor" },
    { sti: "handlinger[1].modul", type: "lagt_til", til: "M-41" },
    { sti: "handlinger[1].modus", type: "lagt_til", til: "auto" },
    { sti: "handlinger[1].grenser.belop_maks", type: "lagt_til", til: "5000.00" },
    { sti: "handlinger[1].grenser.valuta[0]", type: "lagt_til", til: "NOK" },
    { sti: "roller[0].id", type: "lagt_til", til: "daglig_leder" },
    { sti: "unntak.kategorier[0]", type: "lagt_til", til: "manglende_data" },
    { sti: "unntak.kategorier[1]", type: "lagt_til", til: "over_grense" },
    { sti: "unntak.kategorier[2]", type: "lagt_til", til: "svindelmistanke" },
    { sti: "tidssone", type: "endret", fra: "UTC", til: "Europe/Oslo" },
  ] },
};

async function aapneEndringer(h) {
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector(".diff-grupper"));
  return h.querySelector(".diff-grupper");
}

// Kontroll: gjør `feltDiff` flat igjen (én li per endring), så blir denne rød.
test("Diff: grupperes per område, og de som utvider fullmakt står åpne øverst",
  async () => {
    SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": STOR_DIFF,
      __post: async () => ({}) };
    const rot = await aapneEndringer(nyHoved());
    const grupper = [...rot.querySelectorAll(".diff-gruppe")];
    assert.equal(grupper.length, 4,
      "handlinger, roller, unntak og tidssone skal bli fire områder");
    const navn = grupper.map((g) =>
      g.querySelector(".diff-gruppenavn").textContent);
    assert.deepEqual(navn.slice(0, 3).sort(), [
      t("ui.policyadmin.diff.gruppe.handlinger"),
      t("ui.policyadmin.diff.gruppe.roller"),
      t("ui.policyadmin.diff.gruppe.unntak"),
    ].sort(), "områdene som utvider fullmakt skal ligge først");
    // Åpne = det fire-øyne-kravet finnes for. Resten kan foldes ut.
    for (const g of grupper.slice(0, 3)) {
      assert.ok(g.hasAttribute("open"), "et område som utvider skal stå åpent");
    }
    assert.ok(!grupper[3].hasAttribute("open"),
      "tidssone utvider ikke fullmakt og skal ikke stjele plass");
  });

// Klassifikatoren skriver «verifikatorer{}» / «verifikator_prioritet{}» —
// «{}» sier at beholderen er en objekt-map, mens bladdiffen navngir de samme
// feltene uten markør. Beholdes markøren i gruppenavnet, er «verifikatorer{}»
// og «verifikatorer» to forskjellige grupper, og en verifikator som UTVIDER
// fullmakten blir verken sortert først, åpnet eller merket (Codex P1).
// Kontroll: fjern `normaliserKlassifikatorSti`, så blir denne rød.
const VERIFIKATOR_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [
    { sti: "verifikatorer{}", klasse: "UTVIDER" },
    { sti: "tidssone", klasse: "NØYTRAL" },
  ],
  diff: { endringer: [
    { sti: "tidssone", type: "endret", fra: "UTC", til: "Europe/Oslo" },
    { sti: "verifikatorer.v_prognose.betrodd_for[0]", type: "lagt_til",
      til: "ordre.bekreft" },
    { sti: "verifikatorer.v_prognose.kan_fastsla_permanent", type: "lagt_til",
      til: true },
  ] },
};

test("Diff: «{}»-markøren fra klassifikatoren peker på samme gruppe som diffen",
  async () => {
    SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": VERIFIKATOR_DIFF,
      __post: async () => ({}) };
    const rot = await aapneEndringer(nyHoved());
    const grupper = [...rot.querySelectorAll(".diff-gruppe")];
    const verif = grupper.find((g) =>
      g.querySelector(".diff-gruppenavn").textContent
        === t("ui.policyadmin.diff.gruppe.verifikatorer"));
    assert.ok(verif, "verifikatorene ble ikke en egen gruppe");
    assert.equal(grupper[0], verif,
      "gruppen som utvider fullmakt skal sorteres først");
    assert.ok(verif.hasAttribute("open"),
      "en gruppe som utvider fullmakt skal stå åpen");
    assert.ok(verif.querySelector('[data-risiko="UTVIDER"]'),
      "gruppen som utvider fullmakt skal bære samme merking som risikolista");
  });

// To feil i samme uttrykk for hva et «element» er (Codex P2 × 2).
//
// `menneskelig_overstyring.godkjennbare[0].handling`: elementet stoppet før
// indeksen, så ALLE overstyringene havnet i ett kort med samlestien som
// eneste overskrift. Det er en fullmaktsbærende liste — hvilken handling og
// hvilket beløp per overstyring er nettopp det godkjenneren skal skille.
//
// `dataklasser[0]`: her ble den indekserte stien selv elementnøkkelen, så
// hver indeks ble sin egen rad — stikk i strid med løftet om ÉN rad for
// `dataklasser[]`.
const NOSTET_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [],
  diff: { endringer: [
    { sti: "menneskelig_overstyring.godkjennbare[0].handling",
      type: "lagt_til", til: "refusjon.utfor" },
    { sti: "menneskelig_overstyring.godkjennbare[0].belop_maks",
      type: "lagt_til", til: "5000.00" },
    { sti: "menneskelig_overstyring.godkjennbare[1].handling",
      type: "lagt_til", til: "ordre.kanseller" },
    { sti: "menneskelig_overstyring.godkjennbare[1].belop_maks",
      type: "lagt_til", til: "250000.00" },
    { sti: "dataklasser[0]", type: "lagt_til", til: "personopplysninger" },
    { sti: "dataklasser[1]", type: "lagt_til", til: "regnskapsdata" },
    { sti: "dataklasser[2]", type: "lagt_til", til: "kontonummer" },
  ] },
};

function gruppeMedNavn(rot, navn) {
  return [...rot.querySelectorAll(".diff-gruppe")].find((g) =>
    g.querySelector(".diff-gruppenavn").textContent === navn);
}

test("Diff: hver oppføring i en nøstet objektliste blir sitt eget kort",
  async () => {
    SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": NOSTET_DIFF,
      __post: async () => ({}) };
    const rot = await aapneEndringer(nyHoved());
    const mo = gruppeMedNavn(rot, t(
      "ui.policyadmin.diff.gruppe.menneskelig_overstyring",
      "menneskelig_overstyring"));
    const kort = [...mo.querySelectorAll(".diff-element")];
    assert.equal(kort.length, 2,
      "to overstyringer er to beslutninger — og to kort "
      + `(fant ${kort.length})`);
    // Og hver av dem må bære SINE egne verdier, ikke naboens.
    const refusjon = kort.find((k) => k.textContent.includes("refusjon.utfor"));
    const ordre = kort.find((k) => k.textContent.includes("ordre.kanseller"));
    assert.ok(refusjon && ordre, "overstyringene ble ikke skilt fra hverandre");
    assert.ok(refusjon.textContent.includes("5000.00")
      && !refusjon.textContent.includes("250000.00"),
      "beløpsgrensene ble blandet mellom overstyringene");
  });

// Å dele overstyringene i hvert sitt kort hjelper ikke hvis alle kortene har
// samme overskrift. `godkjennbare[]` har ingen `id` — skjemaet krever
// `grunnkode` + `handling` — så overskriften ble «…godkjennbare[n] · N felt»,
// og godkjenneren måtte åpne hvert kort for å finne handlingen og
// beløpsgrensen (Codex P2).
//
// Kontroll: fjern `handling` fra `IDENTITET` og `["belop_maks", "valuta"]`
// fra `BELOPSFELT`, så blir denne rød.
test("Diff: en overstyring identifiseres på handling og beløp, ikke på indeks",
  async () => {
    SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": {
      ...NOSTET_DIFF,
      diff: { endringer: [
        ...NOSTET_DIFF.diff.endringer,
        { sti: "menneskelig_overstyring.godkjennbare[0].grunnkode",
          type: "lagt_til", til: "belop_over_grense" },
        { sti: "menneskelig_overstyring.godkjennbare[0].valuta",
          type: "lagt_til", til: "NOK" },
      ] },
    }, __post: async () => ({}) };
    const rot = await aapneEndringer(nyHoved());
    const mo = gruppeMedNavn(rot, t(
      "ui.policyadmin.diff.gruppe.menneskelig_overstyring",
      "menneskelig_overstyring"));
    const opps = [...mo.querySelectorAll(".diff-element > summary")]
      .map((s) => s.textContent);
    const refusjon = opps.find((o) => o.includes("refusjon.utfor"));
    assert.ok(refusjon,
      "overstyringen identifiseres ikke ved handling, bare ved indeks "
      + `(${JSON.stringify(opps)})`);
    assert.ok(refusjon.includes("belop_over_grense"),
      "grunnkoden — hva overstyringen gjelder — mangler i overskriften");
    assert.ok(refusjon.includes("5000.00") && refusjon.includes("NOK"),
      "beløpsgrensen er selve fullmakten, og mangler i overskriften");
    assert.ok(opps.some((o) => o.includes("ordre.kanseller")),
      "den andre overstyringen har fortsatt bare indeksen som overskrift");
  });

test("Diff: en skalarliste på toppnivå blir ÉN rad, ikke én per indeks",
  async () => {
    SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": NOSTET_DIFF,
      __post: async () => ({}) };
    const rot = await aapneEndringer(nyHoved());
    const dk = gruppeMedNavn(rot, t("ui.policyadmin.diff.gruppe.dataklasser"));
    const rader = dk.querySelectorAll("li");
    assert.equal(rader.length, 1,
      `tre dataklasser er én rad, ikke ${rader.length}`);
    assert.equal(rader[0].querySelector("code").textContent, "dataklasser[]");
    for (const v of ["personopplysninger", "regnskapsdata", "kontonummer"]) {
      assert.ok(rader[0].textContent.includes(v), `${v} forsvant`);
    }
  });

// Et enkelt lagt-til skalarfelt tilfredsstilte også «alle blader er
// skalare», og gikk gjennom sammenslåingen som hektet på «[]». «tidssone»
// ble vist som «tidssone[]» — diffen påsto at feltet var en liste.
// Godkjenneren attesterer strukturen hun ser (Codex P2).
const SKALAR_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [],
  diff: { endringer: [
    { sti: "tidssone", type: "lagt_til", til: "Europe/Oslo" },
    { sti: "unntak.maks_auto_forsok", type: "lagt_til", til: 3 },
    { sti: "unntak.kategorier[0]", type: "lagt_til", til: "over_grense" },
    { sti: "unntak.kategorier[1]", type: "lagt_til", til: "manglende_data" },
  ] },
};

test("Diff: et enkelt skalarfelt omdøpes ikke til en liste", async () => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": SKALAR_DIFF,
    __post: async () => ({}) };
  const rot = await aapneEndringer(nyHoved());
  const stier = [...rot.querySelectorAll("code")].map((c) => c.textContent);
  assert.ok(stier.includes("tidssone"),
    `«tidssone» skal stå med sin egen sti (fant ${JSON.stringify(stier)})`);
  assert.ok(!stier.includes("tidssone[]"),
    "et skalarfelt ble presentert som en liste");
  assert.ok(stier.includes("unntak.maks_auto_forsok")
    && !stier.includes("unntak.maks_auto_forsok[]"),
    "et skalarfelt under en gruppe ble presentert som en liste");
  // Og den EKTE lista skal fortsatt slås sammen.
  assert.ok(stier.includes("unntak.kategorier[]"),
    "en reell skalarliste skal fortsatt bli én rad");
});

// Sammenslåingen bytter OPPDELING, ikke innhold. `String()` på hver verdi
// visket ut typen (`true` og `"true"` ble samme tekst) og grensene mellom
// verdiene (en verdi med komma i seg så ut som to oppføringer). Godkjenneren
// attesterer `diff_hash` over de eksakte verdiene (Codex P2).
//
// Kontroll: bytt `JSON.stringify` tilbake til `String` i `skalarListeRad`, så
// blir denne rød.
test("Diff: sammenslåtte listeverdier beholder JSON-type og grenser",
  async () => {
    SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": {
      ...DETALJ,
      klassifisering_endringer: [],
      diff: { endringer: [
        { sti: "dataklasser[0]", type: "lagt_til", til: true },
        { sti: "dataklasser[1]", type: "lagt_til", til: "true" },
        { sti: "dataklasser[2]", type: "lagt_til", til: "a, b" },
      ] },
    }, __post: async () => ({}) };
    const rot = await aapneEndringer(nyHoved());
    const rad = gruppeMedNavn(rot, t("ui.policyadmin.diff.gruppe.dataklasser"))
      .querySelector("li");
    assert.match(rad.textContent, /true, "true"/,
      "boolsk `true` og strengen «true» må være til å skille fra hverandre "
      + `(fikk «${rad.textContent}»)`);
    assert.ok(rad.textContent.includes('"a, b"'),
      "en verdi som selv inneholder komma må ha synlige grenser");
  });

test("Diff: overskriften sier hva handlingen ER, ikke hvor den står i JSON-en",
  async () => {
    SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": STOR_DIFF,
      __post: async () => ({}) };
    const rot = await aapneEndringer(nyHoved());
    const overskrifter = [...rot.querySelectorAll(".diff-element > summary")]
      .map((s) => s.textContent);
    const refusjon = overskrifter.find((o) => o.includes("refusjon.utfor"));
    assert.ok(refusjon, "handlingen identifiseres ikke ved navn");
    // Nettopp disse tre avgjør fullmakten, og skal kunne leses UTEN å utfolde.
    assert.ok(refusjon.includes("M-41"), "modul mangler i overskriften");
    assert.ok(refusjon.includes("auto"), "modus mangler i overskriften");
    assert.ok(refusjon.includes("5000.00") && refusjon.includes("NOK"),
      "beløpsgrensen mangler i overskriften");
    // Delene må skilles i TEKSTEN, ikke bare visuelt: flex-gap er ingen
    // avstand for en skjermleser, og «refusjon.utforM-41auto» er uleselig.
    assert.ok(!/refusjon\.utforM-41/.test(refusjon),
      "overskriftsdelene limes sammen uten skilletegn");
  });

// Det vanligste tilfellet er ikke en ny policy, men en JUSTERT: da inneholder
// diffen bare det ene bladet som skiftet. Bygges overskriften utelukkende av
// de endrede bladene, faller den tilbake til «handlinger[1]» — uten navn,
// modul eller beløpsgrense. Godkjenneren får altså vite at noe endret seg,
// men ikke på hvilken handling (Codex P2). Utkastets `innhold` er allerede
// med i detaljsvaret og er fasit for hva elementet ER etter endringen.
const ENDRET_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [],
  innhold: {
    handlinger: [
      { id: "ordre.bekreft", modul: "M-25", modus: "forslag" },
      { id: "refusjon.utfor", modul: "M-41", modus: "auto",
        grenser: { belop_maks: "5000.00", valuta: ["NOK"] } },
    ],
  },
  diff: { endringer: [
    { sti: "handlinger[1].modus", type: "endret", fra: "forslag", til: "auto" },
  ] },
};

test("Diff: overskriften hentes fra hele utkastet, ikke bare fra det endrede",
  async () => {
    SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": ENDRET_DIFF,
      __post: async () => ({}) };
    const rot = await aapneEndringer(nyHoved());
    const opps = rot.querySelector(".diff-element > summary").textContent;
    assert.ok(opps.includes("refusjon.utfor"),
      `overskriften sier ikke hvilken handling som endres: «${opps}»`);
    assert.ok(opps.includes("M-41"), "modul mangler i overskriften");
    assert.ok(opps.includes("5000.00") && opps.includes("NOK"),
      "beløpsgrensen mangler i overskriften");
    // Selve endringen skal fortsatt vise BEGGE sider.
    const rad = rot.querySelector(".feltdiff li").textContent;
    assert.ok(rad.includes("forslag") && rad.includes("auto"),
      "en endret verdi må vise både fra og til");
  });

// Et slettet element finnes ikke i utkastet. Da er `fra`-verdiene i diffen
// det eneste som forteller hva som forsvinner, og de må beholdes.
const SLETTET_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [],
  innhold: { handlinger: [] },
  diff: { endringer: [
    { sti: "handlinger[0].id", type: "fjernet", fra: "refusjon.utfor" },
    { sti: "handlinger[0].modul", type: "fjernet", fra: "M-41" },
    { sti: "handlinger[0].modus", type: "fjernet", fra: "auto" },
  ] },
};

test("Diff: et slettet element beholder navnet sitt fra fra-verdiene",
  async () => {
    SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": SLETTET_DIFF,
      __post: async () => ({}) };
    const rot = await aapneEndringer(nyHoved());
    const opps = rot.querySelector(".diff-element > summary").textContent;
    assert.ok(opps.includes("refusjon.utfor"),
      `den fjernede handlingen mistet navnet sitt: «${opps}»`);
    assert.ok(opps.includes("M-41"), "modulen forsvant fra overskriften");
  });

// Serverens diff sammenligner lister POSISJONELT. Fjernes den første av to
// handlinger, blir `handlinger[0]` til bladet «id: A → B», og `handlinger[1]`
// til de fjernede bladene til gamle B. Hentes overskriften utelukkende fra
// utkastet, het BEGGE kortene «B» — og A, det som faktisk forsvant, sto ingen
// steder (Codex P2).
//
// Kontroll: la `vis()` returnere bare den nye verdien, så blir denne rød.
const FORSKJOVET_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [],
  innhold: {
    handlinger: [
      { id: "refusjon.utfor", modul: "M-41", modus: "auto" },
    ],
  },
  diff: { endringer: [
    { sti: "handlinger[0].id", type: "endret",
      fra: "ordre.bekreft", til: "refusjon.utfor" },
    { sti: "handlinger[0].modul", type: "endret", fra: "M-25", til: "M-41" },
    { sti: "handlinger[1].id", type: "fjernet", fra: "refusjon.utfor" },
    { sti: "handlinger[1].modul", type: "fjernet", fra: "M-41" },
  ] },
};

test("Diff: en handling som forsvinner når indeksene forskyves, blir synlig",
  async () => {
    SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": FORSKJOVET_DIFF,
      __post: async () => ({}) };
    const rot = await aapneEndringer(nyHoved());
    const opps = [...rot.querySelectorAll(".diff-element > summary")]
      .map((s) => s.textContent);
    assert.equal(opps.length, 2, "to posisjoner er to kort");
    assert.ok(opps.some((o) => o.includes("ordre.bekreft")),
      "handlingen som ble fjernet står ikke i noen overskrift: "
      + JSON.stringify(opps));
    // Og kortet som BÆRER «A → B» må vise begge, ikke bare den nye.
    const skiftet = opps.find((o) => o.includes("ordre.bekreft"));
    assert.ok(skiftet.includes("refusjon.utfor"),
      "kortet viser bare den ene siden av identitetsskiftet");
    assert.ok(skiftet.includes("M-25") && skiftet.includes("M-41"),
      "modulen skiftet også, og begge sider hører hjemme i overskriften");
  });

// Serveren sender diffstiene LEKSIKALSK sortert, så fra ti elementer og
// oppover kommer «[10]» og «[11]» før «[2]». Sammenslåingen fjerner
// indeksene, og da sto en masseendring i en annen rekkefølge enn lista
// faktisk har — uten indeksene igjen til å avsløre det. Godkjenneren
// attesterer `diff_hash` over de eksakte verdiene, rekkefølgen inkludert
// (Codex P2).
//
// Kontroll: fjern sorteringen i `skalarListeRad`, så blir denne rød.
const TOSIFRET_LISTE_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [],
  innhold: { dataklasser: [...Array(12).keys()].map((i) => `k${i}`) },
  diff: { endringer: [...Array(12).keys()]
    .map((i) => ({ sti: `dataklasser[${i}]`, type: "lagt_til", til: `k${i}` }))
    // Serverens leksikalske rekkefølge: [0], [1], [10], [11], [2], …
    .sort((a, b) => a.sti.localeCompare(b.sti)) },
};

test("Diff: sammenslåtte listeverdier står i listas rekkefølge", async () => {
  SVAR = { "/v1/policyutkast": LISTE,
    "/v1/policyutkast/u-1": TOSIFRET_LISTE_DIFF, __post: async () => ({}) };
  const rot = await aapneEndringer(nyHoved());
  const rad = gruppeMedNavn(rot, t("ui.policyadmin.diff.gruppe.dataklasser"))
    .querySelector("li").textContent;
  const rekkefolge = [...rad.matchAll(/k(\d+)/g)].map((m) => Number(m[1]));
  assert.deepEqual(rekkefolge, [...Array(12).keys()],
    `verdiene står ikke i listas rekkefølge: «${rad}»`);
});

// `retention[]` har ingen `id`, men skjemaet KREVER `dataklasse` — og det er
// dataklassen en oppbevaringsregel handler om. Uten den som identitet het
// hvert kort «retention[0]», «retention[1]» …, og med flere regler måtte
// godkjenneren åpne hvert eneste kort for å finne ut hvilke data den endrede
// regelen gjaldt (Codex P2).
//
// Kontroll: ta `dataklasse` ut av `IDENTITET`, så blir denne rød.
const RETENTION_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [],
  innhold: {
    retention: [
      { dataklasse: "persondata", aar_min: 5, regel: "gdpr" },
      { dataklasse: "finansiell", aar_min: 10, regel: "bokforing" },
    ],
  },
  diff: { endringer: [
    { sti: "retention[0].aar_min", type: "endret", fra: 3, til: 5 },
    { sti: "retention[1].aar_min", type: "endret", fra: 7, til: 10 },
  ] },
};

test("Diff: en oppbevaringsregel navngis av dataklassen sin", async () => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": RETENTION_DIFF,
    __post: async () => ({}) };
  const rot = await aapneEndringer(nyHoved());
  const opps = [...rot.querySelectorAll(".diff-element > summary")]
    .map((s) => s.textContent);
  assert.equal(opps.length, 2, "to oppbevaringsregler er to kort");
  for (const k of ["persondata", "finansiell"]) {
    assert.ok(opps.some((o) => o.includes(k)),
      `ingen overskrift navngir dataklassen «${k}»: ${JSON.stringify(opps)}`);
  }
});

// `tillatt_for` er en MENGDE av roller. Sto bare `tillatt_for[0]` i
// overskriften, var en rolle lagt til ETTER den første usynlig i den lukkede
// oppsummeringen — enda det å gi en ny rolle fullmakt er nettopp det serveren
// klassifiserer som UTVIDER (Codex P2).
//
// Kontroll: sett `MENGDEFELT` tilbake til `tillatt_for[0]` i `NOKKELFELT`, så
// blir denne rød.
const NY_ROLLE_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [{ sti: "handlinger[].tillatt_for[]",
    klasse: "UTVIDER" }],
  innhold: {
    handlinger: [
      { id: "refusjon.utfor", modul: "M-41",
        tillatt_for: ["admin", "ansatt", "regnskap"] },
    ],
  },
  diff: { endringer: [
    { sti: "handlinger[0].tillatt_for[1]", type: "lagt_til", til: "ansatt" },
    { sti: "handlinger[0].tillatt_for[2]", type: "lagt_til", til: "regnskap" },
  ] },
};

test("Diff: hver rolle som får fullmakt, står i overskriften", async () => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": NY_ROLLE_DIFF,
    __post: async () => ({}) };
  const rot = await aapneEndringer(nyHoved());
  const opps = rot.querySelector(".diff-element > summary").textContent;
  for (const r of ["admin", "ansatt", "regnskap"]) {
    assert.ok(opps.includes(r),
      `rollen «${r}» mangler i overskriften: «${opps}»`);
  }
  // Rollene er en mengde, og skal skilles i TEKSTEN — «adminansatt» er ikke
  // to roller for et menneske, og ikke for en skjermleser heller.
  assert.ok(/admin,\s*ansatt/.test(opps),
    `rollene limes sammen uten skilletegn: «${opps}»`);
});

// Byttes én rolle mot en annen, sammenligner serverens diff listene POSISJONELT
// og melder ett `endret`-blad på indeks 0. Ble «borte» avgjort på indeks, fantes
// indeks 0 fortsatt, og overskriften viste bare den nye rollen: den som mistet
// fullmakten sto ingen steder (Codex P2). `admin` beholder samtidig fullmakten
// sin på en annen indeks enn før, og skal IKKE meldes fjernet.
//
// Kontroll: avgjør `borte` på indeks igjen (`sider(k).ny === undefined`), så
// blir denne rød.
const BYTTET_ROLLE_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [{ sti: "handlinger[].tillatt_for[]",
    klasse: "UTVIDER" }],
  innhold: {
    handlinger: [
      { id: "refusjon.utfor", modul: "M-41", tillatt_for: ["ansatt", "admin"] },
    ],
  },
  diff: { endringer: [
    { sti: "handlinger[0].tillatt_for[0]", type: "endret",
      fra: "admin", til: "ansatt" },
    { sti: "handlinger[0].tillatt_for[1]", type: "endret",
      fra: "regnskap", til: "admin" },
  ] },
};

test("Diff: en rolle som mister fullmakten, står i overskriften", async () => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": BYTTET_ROLLE_DIFF,
    __post: async () => ({}) };
  const rot = await aapneEndringer(nyHoved());
  const opps = rot.querySelector(".diff-element > summary").textContent;
  const fjernet = t("ui.policyadmin.diff.fjernet");
  assert.ok(opps.includes(`regnskap → ${fjernet}`),
    `rollen som mistet fullmakten mangler i overskriften: «${opps}»`);
  for (const r of ["ansatt", "admin"]) {
    assert.ok(opps.includes(r), `rollen «${r}» mangler i overskriften: «${opps}»`);
  }
  // `admin` har fortsatt fullmakt — bare på en annen indeks. En posisjonell
  // sammenligning ville påstått at rollen var borte.
  assert.ok(!opps.includes(`admin → ${fjernet}`),
    `en rolle som fortsatt har fullmakt meldes fjernet: «${opps}»`);
});

// `dataklasser_tillatt` er den andre fullmaktsbærende mengden på en handling,
// og klassifikatoren behandler den som `tillatt_for`: en dataklasse lagt til er
// UTVIDER. Den sto ikke i overskriften i det hele tatt, så handlingen hadde
// nøyaktig samme lukkede oppsummering før og etter (Codex P2).
//
// Kontroll: ta `dataklasser_tillatt` ut av `MENGDEFELT`, så blir denne rød.
const NY_DATAKLASSE_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [{ sti: "handlinger[].dataklasser_tillatt[]",
    klasse: "UTVIDER" }],
  innhold: {
    handlinger: [
      { id: "refusjon.utfor", modul: "M-41",
        dataklasser_tillatt: ["intern", "sensitiv"] },
    ],
  },
  diff: { endringer: [
    { sti: "handlinger[0].dataklasser_tillatt[1]", type: "lagt_til",
      til: "sensitiv" },
  ] },
};

test("Diff: en ny dataklasse handlingen får bruke, står i overskriften",
  async () => {
    SVAR = { "/v1/policyutkast": LISTE,
      "/v1/policyutkast/u-1": NY_DATAKLASSE_DIFF, __post: async () => ({}) };
    const rot = await aapneEndringer(nyHoved());
    const opps = rot.querySelector(".diff-element > summary").textContent;
    for (const d of ["intern", "sensitiv"]) {
      assert.ok(opps.includes(d),
        `dataklassen «${d}» mangler i overskriften: «${opps}»`);
    }
    assert.ok(/intern,\s*sensitiv/.test(opps),
      `dataklassene limes sammen uten skilletegn: «${opps}»`);
  });

// `grenser.valuta` er en LISTE av valutaer, men overskriften leste bare
// indeks 0. Utvides den fra ["NOK"] til ["NOK", "EUR"], klassifiserer serveren
// det som UTVIDER — mens overskriften sto uendret på «maks 5000.00 NOK», og den
// nye valutaen var usynlig til kortet ble åpnet (Codex P2).
//
// Kontroll: sett `grenser.valuta[0]` tilbake i `BELOPSFELT`, så blir denne rød.
const NY_VALUTA_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [{ sti: "handlinger[].grenser.valuta[]",
    klasse: "UTVIDER" }],
  innhold: {
    handlinger: [
      { id: "refusjon.utfor", modul: "M-41",
        grenser: { belop_maks: "5000.00", valuta: ["NOK", "EUR"] } },
    ],
  },
  diff: { endringer: [
    { sti: "handlinger[0].grenser.valuta[1]", type: "lagt_til", til: "EUR" },
  ] },
};

test("Diff: hver valuta grensen gjelder i, står i overskriften", async () => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": NY_VALUTA_DIFF,
    __post: async () => ({}) };
  const rot = await aapneEndringer(nyHoved());
  const opps = rot.querySelector(".diff-element > summary").textContent;
  for (const v of ["NOK", "EUR"]) {
    assert.ok(opps.includes(v), `valutaen «${v}» mangler i overskriften: «${opps}»`);
  }
  assert.ok(/NOK,\s*EUR/.test(opps),
    `valutaene limes sammen uten skilletegn: «${opps}»`);
  assert.ok(opps.includes("5000.00"),
    `grensen valutaene gjelder for, mangler: «${opps}»`);
});

// Fjernes et nøkkelfelt fra et element som fortsatt finnes, har utkastet
// ingenting å hydrere overskriften med — og feltet forsvant da helt fra den.
// Å fjerne `grenser.belop_maks` gjør en begrenset handling UBEGRENSET og
// klassifiseres som UTVIDER, men overskriften sa bare «refusjon.utfor · M-41»
// og lot den gamle grensen ligge tolv rader ned (Codex P2).
//
// Kontroll: la `vis()` returnere `undefined` når den nye verdien mangler, så
// blir denne rød.
const FJERNET_GRENSE_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [{ sti: "handlinger[].grenser.belop_maks",
    klasse: "UTVIDER" }],
  innhold: {
    handlinger: [{ id: "refusjon.utfor", modul: "M-41", grenser: {} }],
  },
  diff: { endringer: [
    { sti: "handlinger[0].grenser.belop_maks", type: "fjernet", fra: "5000.00" },
    { sti: "handlinger[0].grenser.valuta[0]", type: "fjernet", fra: "NOK" },
    { sti: "handlinger[0].modus", type: "fjernet", fra: "auto" },
  ] },
};

test("Diff: nøkkelfelt som fjernes, står i overskriften", async () => {
  SVAR = { "/v1/policyutkast": LISTE,
    "/v1/policyutkast/u-1": FJERNET_GRENSE_DIFF, __post: async () => ({}) };
  const rot = await aapneEndringer(nyHoved());
  const opps = rot.querySelector(".diff-element > summary").textContent;
  assert.ok(opps.includes("5000.00"),
    `den gamle grensen forsvant fra overskriften: «${opps}»`);
  assert.ok(opps.includes(t("ui.policyadmin.diff.uten_grense")),
    `overskriften sier ikke at handlingen er ubegrenset nå: «${opps}»`);
  // Samme gjelder et vanlig nøkkelfelt: «auto» er borte, og det skal SES.
  assert.ok(opps.includes("auto")
    && opps.includes(t("ui.policyadmin.diff.fjernet")),
    `et fjernet nøkkelfelt forsvant fra overskriften: «${opps}»`);
  // Og en handling som fortsatt finnes skal ikke miste navnet sitt.
  assert.ok(opps.includes("refusjon.utfor"), "handlingen mistet navnet sitt");
});

// `verifikatorer` hadde UBEGRENSEDE nøkkelnavn i skjemaet, så «foo.bar» var en
// gyldig verifikator-id. Skjemaet forbyr nå punktum og klammer i id-en, men
// utkastdetaljen viser diff også for utkast som ennå ikke er validert, så
// oppdelingen må fortsatt tåle stien.
// Serverens flate sti skjøter map-nøkler med punktum,
// og en oppdeling som leser punktum som skilletegn slo derfor to helt ulike
// verifikatorer sammen til elementet «verifikatorer.foo» — bladene deres i
// ett kort, og oppslaget i utkastet ned i nøkler som ikke finnes (Codex P2).
//
// Kontroll: la `delOppLedd` ignorere utkastet og dele på punktum, så blir
// denne rød.
const PUNKTUMNOKKEL_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [],
  innhold: {
    verifikatorer: {
      "foo.bar": { beskrivelse: "Bankintegrasjon", betrodd_for: ["betaling"] },
      "foo.baz": { beskrivelse: "Fakturamottak", betrodd_for: ["mottak"] },
    },
  },
  diff: { endringer: [
    { sti: "verifikatorer.foo.bar.betrodd_for[0]", type: "lagt_til",
      til: "betaling" },
    { sti: "verifikatorer.foo.bar.beskrivelse", type: "lagt_til",
      til: "Bankintegrasjon" },
    { sti: "verifikatorer.foo.baz.betrodd_for[0]", type: "lagt_til",
      til: "mottak" },
    { sti: "verifikatorer.foo.baz.beskrivelse", type: "lagt_til",
      til: "Fakturamottak" },
  ] },
};

// Samme frie nøkkelnavn gjør «foo{}bar» til en gyldig verifikator-id — «{}» er
// ikke et skilletegn i den flate stien, så innstrammingen over rører den ikke.
// Ble normaliseringen av klassifikatorens beholder-markør kjørt på bladdiffen
// også, skrev den «verifikatorer.foo{}bar» om til «verifikatorer.foobar» — og
// fantes BEGGE i policyen, pekte de to stiene på samme element (Codex P2).
//
// Kontroll: la `delOppSti` normalisere stien igjen, så blir denne rød.
const KLAMMENOKKEL_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [],
  innhold: {
    verifikatorer: {
      "foo{}bar": { beskrivelse: "Bankintegrasjon" },
      foobar: { beskrivelse: "Fakturamottak" },
    },
  },
  diff: { endringer: [
    { sti: "verifikatorer.foo{}bar.beskrivelse", type: "lagt_til",
      til: "Bankintegrasjon" },
    { sti: "verifikatorer.foo{}bar.kan_fastsla_permanent", type: "lagt_til",
      til: true },
    { sti: "verifikatorer.foobar.beskrivelse", type: "lagt_til",
      til: "Fakturamottak" },
    { sti: "verifikatorer.foobar.kan_fastsla_permanent", type: "lagt_til",
      til: false },
  ] },
};

test("Diff: «{}» inne i en gyldig map-nøkkel blir stående", async () => {
  SVAR = { "/v1/policyutkast": LISTE,
    "/v1/policyutkast/u-1": KLAMMENOKKEL_DIFF, __post: async () => ({}) };
  const rot = await aapneEndringer(nyHoved());
  const v = gruppeMedNavn(rot, t("ui.policyadmin.diff.gruppe.verifikatorer"));
  const kort = [...v.querySelectorAll(".diff-element")];
  assert.equal(kort.length, 2,
    `to verifikatorer er to kort (fant ${kort.length})`);
  const bar = kort.find((k) => k.textContent.includes("Bankintegrasjon"));
  assert.ok(bar, "verifikatorene ble slått sammen til ett kort");
  assert.ok(!bar.textContent.includes("Fakturamottak"),
    "en annen verifikators felt havnet i dette kortet");
  assert.ok(bar.querySelector("summary").textContent.includes("foo{}bar"),
    "overskriften navngir ikke verifikatoren: "
    + `«${bar.querySelector("summary").textContent}»`);
});

test("Diff: to verifikatorer med punktum i id-en blir to kort", async () => {
  SVAR = { "/v1/policyutkast": LISTE,
    "/v1/policyutkast/u-1": PUNKTUMNOKKEL_DIFF, __post: async () => ({}) };
  const rot = await aapneEndringer(nyHoved());
  const v = gruppeMedNavn(rot, t("ui.policyadmin.diff.gruppe.verifikatorer"));
  const kort = [...v.querySelectorAll(".diff-element")];
  assert.equal(kort.length, 2,
    `to verifikatorer er to kort (fant ${kort.length})`);
  const bar = kort.find((k) => k.textContent.includes("Bankintegrasjon"));
  assert.ok(bar, "verifikatorene ble slått sammen til ett kort");
  assert.ok(!bar.textContent.includes("Fakturamottak"),
    "en annen verifikators felt havnet i dette kortet");
  // Og oppslaget i utkastet må treffe den EKTE nøkkelen, ikke «foo».
  assert.ok(bar.querySelector("summary").textContent.includes("foo.bar"),
    "overskriften navngir ikke verifikatoren: "
    + `«${bar.querySelector("summary").textContent}»`);
});

// Den verre varianten av de frie nøkkelnavnene: id-ene `foo` OG
// `foo.beskrivelse` finnes SAMTIDIG. Da er `verifikatorer.foo.beskrivelse`
// både beskrivelsen til `foo` og roten til den andre verifikatoren — stien er
// EKTE flertydig, og ingen parser kan lese den riktig. Lengste treff gjettet
// på den lengste id-en, så beskrivelsen til `foo` havnet i kortet til
// `foo.beskrivelse`, sammen med DENS felt: godkjenneren leste en
// tillitsendring på feil verifikator (Codex P2 på #61).
//
// Skjemaet forbyr nå punktum og klammer i verifikator-id-en, så en policy som
// kan AKTIVERES kan ikke komme hit. Men utkastdetaljen viser diff også for et
// utkast som ennå ikke er validert (`policyadmin.hent_utkast_detalj`), så
// stien kan fortsatt nå UI-et. Da gjettes det ikke: det flertydige bladet blir
// stående som ett ledd med hele den rå stien, så det får sitt EGET kort i
// stedet for å bli lagt inn under en annen verifikator. Dårligere gruppering,
// men aldri feil tilskriving — og ingenting forsvinner.
//
// Kontroll: la `delOppLedd` gjette på lengste treff igjen, så blir denne rød.
const FLERTYDIG_NOKKEL_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [],
  innhold: {
    verifikatorer: {
      foo: { beskrivelse: "Bankintegrasjon", betrodd_for: ["vilkaar_a"] },
      "foo.beskrivelse": { beskrivelse: "Fakturamottak",
        betrodd_for: ["vilkaar_b"] },
    },
  },
  diff: { endringer: [
    { sti: "verifikatorer.foo.betrodd_for[0]", type: "lagt_til",
      til: "vilkaar_a" },
    { sti: "verifikatorer.foo.beskrivelse", type: "lagt_til",
      til: "Bankintegrasjon" },
    { sti: "verifikatorer.foo.beskrivelse.betrodd_for[0]", type: "lagt_til",
      til: "vilkaar_b" },
    { sti: "verifikatorer.foo.beskrivelse.beskrivelse", type: "lagt_til",
      til: "Fakturamottak" },
  ] },
};

test("Diff: flertydig verifikator-id blander ikke to verifikatorer",
  async () => {
    SVAR = { "/v1/policyutkast": LISTE,
      "/v1/policyutkast/u-1": FLERTYDIG_NOKKEL_DIFF, __post: async () => ({}) };
    const rot = await aapneEndringer(nyHoved());
    const v = gruppeMedNavn(rot, t("ui.policyadmin.diff.gruppe.verifikatorer"));
    const kort = [...v.querySelectorAll(".diff-element")];
    // Selve funnet: ingen kort tilskriver den ene verifikatoren den andres
    // felt. `vilkaar_a`/`vilkaar_b` er tillitsendringen — den som må treffe
    // riktig verifikator.
    for (const k of kort) {
      const tekst = k.textContent;
      assert.ok(!(tekst.includes("vilkaar_a") && tekst.includes("vilkaar_b")),
        `to verifikatorers betrodd_for i ett kort: «${tekst}»`);
      assert.ok(
        !(tekst.includes("Bankintegrasjon") && tekst.includes("Fakturamottak")),
        `to verifikatorer slått sammen i ett kort: «${tekst}»`);
    }
    // Og ingenting forsvinner: alle fire bladene står fortsatt et sted.
    for (const s of ["vilkaar_a", "vilkaar_b", "Bankintegrasjon",
      "Fakturamottak"]) {
      assert.ok(v.textContent.includes(s), `«${s}» forsvant fra diffen`);
    }
  });

// Samme punktumnøkler, men SLETTET: da finnes ingen av dem i utkastet, og en
// oppdeling som bare kjenner utkastet faller tilbake på første punktum. Begge
// verifikatorene havnet da i ett kort som het «verifikatorer.foo» — nøyaktig
// i det tilfellet der fullmakt FORSVINNER (Codex P2). Basen diffen måles mot
// er den eneste kilden som fortsatt vet hvor de nøklene slutter.
//
// Kontroll: la `kilder` bare inneholde `detalj.innhold`, så blir denne rød.
const SLETTET_PUNKTUMNOKKEL_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [],
  innhold: { tidssone: "UTC" },
  base_innhold: {
    verifikatorer: {
      "foo.bar": { beskrivelse: "Bankintegrasjon" },
      "foo.baz": { beskrivelse: "Fakturamottak" },
    },
  },
  diff: { endringer: [
    { sti: "verifikatorer.foo.bar.beskrivelse", type: "fjernet",
      fra: "Bankintegrasjon" },
    { sti: "verifikatorer.foo.bar.kan_fastsla_permanent", type: "fjernet",
      fra: true },
    { sti: "verifikatorer.foo.baz.beskrivelse", type: "fjernet",
      fra: "Fakturamottak" },
    { sti: "verifikatorer.foo.baz.kan_fastsla_permanent", type: "fjernet",
      fra: false },
  ] },
};

test("Diff: to slettede verifikatorer med punktum i id-en blir to kort",
  async () => {
    SVAR = { "/v1/policyutkast": LISTE,
      "/v1/policyutkast/u-1": SLETTET_PUNKTUMNOKKEL_DIFF,
      __post: async () => ({}) };
    const rot = await aapneEndringer(nyHoved());
    const v = gruppeMedNavn(rot, t("ui.policyadmin.diff.gruppe.verifikatorer"));
    const kort = [...v.querySelectorAll(".diff-element")];
    assert.equal(kort.length, 2,
      `to slettede verifikatorer er to kort (fant ${kort.length})`);
    const bar = kort.find((k) => k.textContent.includes("Bankintegrasjon"));
    assert.ok(bar, "de slettede verifikatorene ble slått sammen til ett kort");
    assert.ok(!bar.textContent.includes("Fakturamottak"),
      "en annen slettet verifikators felt havnet i dette kortet");
    for (const k of kort) {
      const opps = k.querySelector("summary").textContent;
      assert.ok(/foo\.ba[rz]/.test(opps),
        `overskriften navngir ikke verifikatoren som forsvinner: «${opps}»`);
    }
  });

// `verifikatorer` har ubegrensede nøkkelnavn, så en id kan også BEGYNNE med
// et skilletegn: `[bank]` og `.faktura` er lovlige. `_flat` skjøter dem på med
// nøyaktig ett punktum («verifikatorer.[bank]…», «verifikatorer..faktura…»),
// men parseren tolket skilletegnet FØR den så på nodens nøkler: klammene ble
// listeindeks og det ene punktumet forsvant. Begge falt tilbake til
// samlegruppen `verifikatorer` (Codex P2, bekreftet blokkerende av eier).
//
// Kontroll: la `delOppLedd` tolke `[`/`.` før nøkkeloppslaget igjen, så blir
// denne rød.
const SKILLETEGNNOKKEL_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [],
  innhold: {
    verifikatorer: {
      "[bank]": { beskrivelse: "Bankintegrasjon" },
      ".faktura": { beskrivelse: "Fakturamottak" },
    },
  },
  diff: { endringer: [
    { sti: "verifikatorer.[bank].beskrivelse", type: "lagt_til",
      til: "Bankintegrasjon" },
    { sti: "verifikatorer.[bank].kan_fastsla_permanent", type: "endret",
      fra: false, til: true },
    { sti: "verifikatorer..faktura.beskrivelse", type: "lagt_til",
      til: "Fakturamottak" },
    { sti: "verifikatorer..faktura.kan_fastsla_permanent", type: "lagt_til",
      til: false },
  ] },
};

// Og slettet: da finnes nøklene bare på før-siden, så oppdelingen må finne
// grensene i basen — nøyaktig det tilfellet der fullmakt FORSVINNER.
const SLETTET_SKILLETEGNNOKKEL_DIFF = {
  ...DETALJ,
  klassifisering_endringer: [],
  innhold: { tidssone: "UTC" },
  base_innhold: {
    verifikatorer: {
      "[bank]": { beskrivelse: "Bankintegrasjon" },
      ".faktura": { beskrivelse: "Fakturamottak" },
    },
  },
  diff: { endringer: [
    { sti: "verifikatorer.[bank].beskrivelse", type: "fjernet",
      fra: "Bankintegrasjon" },
    { sti: "verifikatorer.[bank].kan_fastsla_permanent", type: "fjernet",
      fra: true },
    { sti: "verifikatorer..faktura.beskrivelse", type: "fjernet",
      fra: "Fakturamottak" },
    { sti: "verifikatorer..faktura.kan_fastsla_permanent", type: "fjernet",
      fra: false },
  ] },
};

for (const [hva, fikstur] of [
  ["beholdt", SKILLETEGNNOKKEL_DIFF],
  ["slettet", SLETTET_SKILLETEGNNOKKEL_DIFF],
]) {
  test(`Diff: en ${hva} map-nøkkel som starter med et skilletegn er én nøkkel`,
    async () => {
      SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": fikstur,
        __post: async () => ({}) };
      const rot = await aapneEndringer(nyHoved());
      const v = gruppeMedNavn(rot,
        t("ui.policyadmin.diff.gruppe.verifikatorer"));
      const kort = [...v.querySelectorAll(".diff-element")];
      assert.equal(kort.length, 2,
        `to verifikatorer er to kort (fant ${kort.length})`);
      const bank = kort.find((k) => k.textContent.includes("Bankintegrasjon"));
      assert.ok(bank, "verifikatorene ble slått sammen til ett kort");
      assert.ok(!bank.textContent.includes("Fakturamottak"),
        "en annen verifikators felt havnet i dette kortet");
      for (const [id, opps] of kort.map((k) =>
        [k.textContent.includes("Bankintegrasjon") ? "[bank]" : ".faktura",
          k.querySelector("summary").textContent])) {
        assert.ok(opps.includes(id),
          `overskriften navngir ikke verifikatoren «${id}»: «${opps}»`);
      }
    });
}

test("Diff: en liste av rene verdier blir ÉN rad, ikke én per indeks", async () => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": STOR_DIFF,
    __post: async () => ({}) };
  const rot = await aapneEndringer(nyHoved());
  const unntak = [...rot.querySelectorAll(".diff-gruppe")].find((g) =>
    g.querySelector(".diff-gruppenavn").textContent
      === t("ui.policyadmin.diff.gruppe.unntak"));
  const alle = unntak.querySelectorAll("li");
  assert.equal(alle.length, 1,
    "tre unntakskategorier er én beslutning — og én rad, ikke en wrapper "
    + `rundt tre (fant ${alle.length})`);
  for (const v of ["manglende_data", "over_grense", "svindelmistanke"]) {
    assert.ok(alle[0].textContent.includes(v), `${v} forsvant`);
  }
});

// Den viktigste testen i fila: grupperingen er PRESENTASJON. Forsvinner én
// endring, attesterer godkjenneren noe hun ikke har sett.
test("Diff: ingen endring forsvinner i grupperingen", async () => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": STOR_DIFF,
    __post: async () => ({}) };
  const rot = await aapneEndringer(nyHoved());
  const tekst = rot.textContent;
  for (const e of STOR_DIFF.diff.endringer) {
    const verdi = String(e.type === "endret" ? e.til : e.til);
    assert.ok(tekst.includes(verdi),
      `verdien «${verdi}» (${e.sti}) finnes ikke i den grupperte diffen`);
  }
  // Og endringen som ikke er et tillegg skal vise BEGGE sider.
  assert.ok(tekst.includes("UTC") && tekst.includes("Europe/Oslo"),
    "en endret verdi må vise både fra og til");
});

// Grupperingen har lov til å folde sammen, men ikke til å skjule at noe kan
// foldes ut. `summary` er `display: list-item` som standard, og det er den
// standarden som gir nettleserens ▶/▼-markør (en `::marker` finnes bare på
// listeelementer). Flex-oppsettet slo den av, så lukkede områder så ut som
// statiske overskrifter — og en godkjenner som ikke ser at noe kan åpnes,
// åpner ikke. jsdom har ingen layout å måle, så porten står på stilkilden.
test("Diff: sammenleggbare områder viser at de KAN åpnes", () => {
  const HER = dirname(fileURLToPath(import.meta.url));
  const css = readFileSync(
    join(HER, "..", "static", "css", "komponenter.css"), "utf-8");
  const regel = (velger) => {
    const i = css.indexOf(velger);
    assert.ok(i >= 0, `${velger} skal finnes i stilkilden`);
    return css.slice(i, css.indexOf("}", i));
  };
  const markor = regel(
    ".diff-gruppe > summary::before, .diff-element > summary::before {");
  assert.match(markor, /content:\s*""/,
    "markøren må tegnes som form, ikke som et tegn en skjermleser leser opp "
    + "oppå tilstanden `details` allerede melder selv");
  assert.match(markor, /border-color:[^;]*currentColor/,
    "markøren må være synlig i samme farge som teksten");
  assert.match(regel(
    ".diff-gruppe[open] > summary::before, .diff-element[open] > summary::before {"),
    /transform:\s*rotate/, "åpen og lukket må se forskjellig ut");
});

test("Diff: den grupperte visningen er axe-ren", async () => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": STOR_DIFF,
    __post: async () => ({}) };
  const h = nyHoved();
  await aapneEndringer(h);
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

// Eier ba om å kunne slette, ikke bare redigere. Det som KAN slettes er et
// UTKAST — et forslag som ennå ikke binder noen. En policy som har styrt
// beslutninger kan ikke fjernes; da ville revisjonssporet pekt på noe som
// ikke finnes. Derfor «Forkast», ikke «Slett».
test("Forkast: et utkast kan forkastes, med bekreftelse først", async () => {
  const kalt = [];
  const utkast = { ...DETALJ, status: "utkast", aktiv_runde: null };
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": utkast,
    __post: async (url, opts) => { kalt.push({ url, opts });
      return { ok: true, status: 200,
        json: async () => ({ utfall: "forkastet", utkast_id: "u-1" }) }; } };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => _finn(h, t("ui.policyadmin.handling.forkast")));
  _finn(h, t("ui.policyadmin.handling.forkast"))
    .dispatchEvent(new window.Event("click"));
  // Et irreversibelt valg skal ikke skje på ett klikk.
  await vent(() => [...document.querySelectorAll('[role="dialog"]')]
    .some((d) => d.textContent.includes(t("ui.policyadmin.forkast.tittel"))));
  const dlg = [...document.querySelectorAll('[role="dialog"]')]
    .find((d) => d.textContent.includes(t("ui.policyadmin.forkast.tittel")));
  assert.ok(dlg.textContent.includes("faktura-no"),
    "bekreftelsen sier ikke hvilken SERIE som berøres");
  assert.ok(dlg.textContent.includes("u-1"),
    "bekreftelsen sier ikke HVILKET utkast som forkastes");
  assert.equal(kalt.length, 0, "forkastet før eier bekreftet");
  [...dlg.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === t("ui.policyadmin.handling.forkast"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => kalt.length > 0);
  assert.equal(kalt[0].opts.method, "POST");
  assert.ok(kalt[0].url.includes("/v1/policyutkast/u-1/forkast"));
  assert.ok(kalt[0].opts.headers["Idempotency-Key"], "mangler Idempotency-Key");
  assert.equal(JSON.parse(kalt[0].opts.body).utkastversjon, 2,
    "utkastversjonen binder nøkkelen til tilstanden eier så");
  // Kvitteringen fylles i en SENERE oppgave (live-området må rekke å bli
  // registrert), så vi venter på den ferdige boksen — ikke på det tomme
  // skallet. Første utgave av denne testen ventet på skallet og fant "".
  const kvitt = await _ventKvittering(h);
  assert.ok(kvitt.textContent.includes(t("ui.policyadmin.forkastet")),
    "utfallet er ikke synlig");
});

// En policyserie kan ha FLERE utkast samtidig, og de deler `policy_id`. Viser
// bekreftelsen bare serien, er to uopprettelige dialoger for to forskjellige
// forslag ord for ord like — og eier kan ikke se hvilket forslag hun er i ferd
// med å rive bort.
//
// Kontroll: bytt `forkastMaal(detalj, uid)` tilbake til `detalj.policy_id`, så
// blir denne rød.
test("Forkast: bekreftelsen navngir UTKASTET, ikke bare serien", async () => {
  const serie = { utkast: [
    { utkast_id: "u-1", policy_id: "faktura-no", status: "utkast",
      utkastversjon: 2, opprettet: "2026-08-10T08:00:00+00:00" },
    { utkast_id: "u-9", policy_id: "faktura-no", status: "utkast",
      utkastversjon: 5, opprettet: "2026-08-10T09:00:00+00:00" },
  ] };
  // Radene står i samme rekkefølge som `serie`, så raden peker ut utkastet.
  const dialogtekst = async (rad, uid, utkastversjon) => {
    SVAR = { "/v1/policyutkast": serie,
      [`/v1/policyutkast/${uid}`]: { ...DETALJ, utkast_id: uid, utkastversjon,
        status: "utkast", aktiv_runde: null },
      __post: async () => ({}) };
    const h = nyHoved();
    visPolicyadmin(h, ctx());
    await vent(() => h.querySelectorAll("tbody button").length >= 2);
    h.querySelectorAll("tbody button")[rad]
      .dispatchEvent(new window.Event("click"));
    await vent(() => _finn(h, t("ui.policyadmin.handling.forkast")));
    _finn(h, t("ui.policyadmin.handling.forkast"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => [...document.querySelectorAll('[role="dialog"]')]
      .some((d) => d.textContent.includes(t("ui.policyadmin.forkast.tittel"))));
    const d = [...document.querySelectorAll('[role="dialog"]')]
      .filter((x) => x.textContent.includes(t("ui.policyadmin.forkast.tittel")));
    return d[d.length - 1].textContent;
  };
  const en = await dialogtekst(0, "u-1", 2);
  const to = await dialogtekst(1, "u-9", 5);
  assert.ok(en.includes("u-1") && to.includes("u-9"),
    "bekreftelsen navngir ikke utkastet som forkastes");
  assert.notEqual(en, to,
    "to utkast i samme serie gir umulige-å-skille bekreftelser");
});

// Et tapt SVAR er ikke en mislykket handling. Commiter serveren forkastingen
// og svaret forsvinner på vei tilbake, kan eier ikke prøve igjen for å finne
// ut av det: utkastet er `forkastet`, knappen er borte med sin nøkkel, og
// handlingen er uopprettelig. Retryen med SAMME nøkkel er derfor det eneste
// som kan hente kvitteringen hennes tilbake — serveren svarer replay.
//
// Kontroll: fjern status-0-grenen i `forkastForsok`, så blir denne rød.
test("Forkast: nettverksretry GJENBRUKER samme Idempotency-Key", async () => {
  const kalt = [];
  const utkast = { ...DETALJ, status: "utkast", aktiv_runde: null };
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": utkast,
    __post: async (url, opts) => {
      kalt.push({ url, opts });
      if (kalt.length === 1) throw new TypeError("network");
      return { ok: true, status: 200,
        json: async () => ({ utfall: "forkastet", utkast_id: "u-1" }) };
    } };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => _finn(h, t("ui.policyadmin.handling.forkast")));
  _finn(h, t("ui.policyadmin.handling.forkast"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => [...document.querySelectorAll('[role="dialog"]')]
    .some((d) => d.textContent.includes(t("ui.policyadmin.forkast.tittel"))));
  const dlg = [...document.querySelectorAll('[role="dialog"]')]
    .find((d) => d.textContent.includes(t("ui.policyadmin.forkast.tittel")));
  _finn(dlg, t("ui.policyadmin.handling.forkast"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => kalt.length >= 2);
  assert.equal(kalt.length, 2, "nettverksfeil skal gi nøyaktig én retry");
  assert.equal(kalt[0].opts.headers["Idempotency-Key"],
    kalt[1].opts.headers["Idempotency-Key"],
    "retry MÅ gjenbruke nøkkelen — ellers forkastes det på nytt");
  const kvitt = await _ventKvittering(h);
  assert.ok(kvitt.textContent.includes(t("ui.policyadmin.forkastet")),
    "eier fikk ikke den ekte kvitteringen sin");
});

// Svarer nettet ikke andre gangen heller, VET vi ikke hva som skjedde — og
// «Handlingen feilet.» ville vært en påstand vi ikke har dekning for om en
// uopprettelig handling.
test("Forkast: to tapte svar gir «ukjent utfall», ikke «feilet»", async () => {
  const kalt = [];
  const utkast = { ...DETALJ, status: "utkast", aktiv_runde: null };
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": utkast,
    __post: async (url, opts) => {
      kalt.push({ url, opts });
      throw new TypeError("network");
    } };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => _finn(h, t("ui.policyadmin.handling.forkast")));
  _finn(h, t("ui.policyadmin.handling.forkast"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => [...document.querySelectorAll('[role="dialog"]')]
    .some((d) => d.textContent.includes(t("ui.policyadmin.forkast.tittel"))));
  const dlg = [...document.querySelectorAll('[role="dialog"]')]
    .find((d) => d.textContent.includes(t("ui.policyadmin.forkast.tittel")));
  _finn(dlg, t("ui.policyadmin.handling.forkast"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => kalt.length >= 2);
  assert.equal(kalt.length, 2, "nøyaktig én retry, ikke en løkke");
  const kvitt = await _ventKvittering(h);
  assert.ok(kvitt.textContent.includes(t("ui.policyadmin.forkast.ukjent")),
    "utfallet meldes ikke som ukjent");
  assert.ok(!kvitt.textContent.includes(t("ui.policyadmin.feilet")),
    "en falsk feilkvittering på en uopprettelig handling");
});

// Kontroll: flytt `forkastKnapp` ut av runde-betingelsen, så blir denne rød.
test("Forkast: knappen finnes IKKE når en runde er åpen", async () => {
  // DETALJ har en åpen runde med attestasjoner i omløp.
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": DETALJ,
    __post: async () => ({}) };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => _finn(h, t("ui.policyadmin.handling.attester")));
  assert.equal(_finn(h, t("ui.policyadmin.handling.forkast")), undefined,
    "et forslag med attestasjoner i omløp skal ikke kunne rives bort");
});

test("Forkast: axe-ren, og knappen er merket som farlig", async () => {
  const utkast = { ...DETALJ, status: "utkast", aktiv_runde: null };
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": utkast,
    __post: async () => ({}) };
  const h = nyHoved();
  visPolicyadmin(h, ctx());
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => _finn(h, t("ui.policyadmin.handling.forkast")));
  assert.ok(_finn(h, t("ui.policyadmin.handling.forkast"))
    .classList.contains("fare"));
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

// Codex P2: veien fra et varsel til HANDLINGEN. Varselet navngir ett utkast,
// og `#/policyadmin/<utkast_id>` er hvordan ruteren bærer det navnet inn i
// flaten. Uten dette landet godkjenneren på lista over alle utkast og måtte
// finne igjen det hun nettopp ble varslet om.
test("Dyplenke til et utkast åpner utkastet, ikke lista", async () => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": DETALJ,
    __post: async () => ({}) };
  const hentet = [];
  SVAR.__get = (sti) => hentet.push(sti);
  const h = nyHoved();

  window.location.hash = "#/policyadmin/u-1";
  await vent(() => false, 5);
  const ruter = lagRuter(h, ctx(), { policyadmin: visPolicyadmin }, () => {});
  ruter.naviger();
  await vent(() => _finn(h, t("ui.policyadmin.tilbake_til_liste")));
  ruter.stopp();

  assert.ok(h.textContent.includes(DETALJ.policy_id),
    "detaljsiden for det varslede utkastet ble ikke åpnet");
  // Ikke bare «detaljen kom fram til slutt»: lista skal ikke ha vært innom.
  assert.ok(!hentet.includes("/v1/policyutkast"),
    "flaten gikk veien om lista i stedet for rett til utkastet");
  // Dyplenken er en inngang, ikke en blindvei: veien tilbake til lista står.
  assert.ok(_finn(h, t("ui.policyadmin.tilbake_til_liste")),
    "detaljsiden mangler vei tilbake");
});

// Codex P3: hashen ble stående på utkastet etter at eier gikk tilbake til
// lista. Skjermen og adressefeltet sa da hver sin ting — og uenigheten er ikke
// kosmetisk: en refresh åpnet detaljsiden på nytt, og historikken pekte
// fortsatt på en visning hun hadde gått ut av.
test("Veien tilbake fra en dyplenket detalj rydder også lenken", async () => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": DETALJ,
    __post: async () => ({}) };
  const h = nyHoved();

  window.location.hash = "#/policyadmin/u-1";
  await vent(() => false, 5);
  const ruter = lagRuter(h, ctx(), { policyadmin: visPolicyadmin }, () => {});
  ruter.naviger();
  await vent(() => _finn(h, t("ui.policyadmin.tilbake_til_liste")));

  _finn(h, t("ui.policyadmin.tilbake_til_liste"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector("tbody"));
  ruter.stopp();

  assert.equal(window.location.hash, "#/policyadmin",
    "hashen peker fortsatt på utkastet mens skjermen viser lista — en "
    + "refresh ville åpnet detaljsiden på nytt");
  // …og lista står der, tegnet ÉN gang: `replaceState` utløser ingen
  // `hashchange`, så ruteren tegner ikke flaten på nytt oppå denne.
  assert.ok(h.querySelector("tbody"), "lista kom ikke fram");
});
