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
const _attesterMedUtfall = async (h, svar) => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": DETALJ,
    __post: async () => ({ ok: true, status: 200, json: async () => svar }) };
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
// Kontroll: fjern `normaliserSti`, så blir denne rød.
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

test("Diff: den grupperte visningen er axe-ren", async () => {
  SVAR = { "/v1/policyutkast": LISTE, "/v1/policyutkast/u-1": STOR_DIFF,
    __post: async () => ({}) };
  const h = nyHoved();
  await aapneEndringer(h);
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});
