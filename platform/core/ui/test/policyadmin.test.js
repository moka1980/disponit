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
  await vent(() => document.querySelector('[role="dialog"]'));
  const dlg = document.querySelector('[role="dialog"]');
  // Risikoklasse per endring (både UTVIDER og INNSNEVRER vises).
  assert.ok(dlg.textContent.includes(t("risiko.UTVIDER")));
  assert.ok(dlg.textContent.includes(t("risiko.INNSNEVRER")));
  assert.ok(dlg.textContent.includes("roller[]"));
  // Fire-øyne-status: 1/2 + forfatter markert.
  assert.ok(dlg.textContent.includes("1 / 2"));
  assert.ok(dlg.textContent.includes(t("ui.policyadmin.forfatter")));
  assert.equal((await alvorligeBrudd(dlg, { fragment: true })).length, 0);
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
  await vent(() => document.querySelector('[role="dialog"]'));
  const finn = (rot, tekst) => [...rot.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === tekst);
  const dlg = document.querySelector('[role="dialog"]');
  const attest = finn(dlg, t("ui.policyadmin.handling.attester"));
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
  await vent(() => document.querySelector('[role="dialog"]'));
  const finn = (rot, tekst) => [...rot.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === tekst);
  finn(document.querySelector('[role="dialog"]'),
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
