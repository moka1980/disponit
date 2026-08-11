// PR-014 — den veiledede policy-editoren (jsdom + axe).
// Bransjemal-velger → skjema (grunnopplysninger, roller, handlinger m/ modus +
// grenser) → lagre som utkast (POST med CSRF + Idempotency-Key).
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visPolicyeditor } from "../static/js/flater/policyeditor.js";

settI18nForTest(NB, "nb");

const MAL = {
  meta: { policy_id: "netthandel-no", versjon: "0.2.0",
    bransjemal: "netthandel-no", status: "utkast" },
  schema_version: "0.2", tidssone: "Europe/Oslo",
  roller: [{ id: "daglig_leder" }, { id: "agent", beskrivelse: "Automatisk" }],
  dataklasser: ["finansiell"], verifikatorer: {},
  handlinger: [{ id: "ordre.bekreft", modul: "M-25", modus: "auto",
    ved_brudd: "unntakskø", grenser: { belop_maks: "1000.00", valuta: ["NOK"] },
    reversering: { type: "kompenserende", handling: "ordre.kanseller",
      frist_sekunder: 3600 } }],
  unntak: { kategorier: ["over_grense"], maks_auto_forsok: 3,
    eskalering: "unntakskø" },
};

let POST;
globalThis.fetch = async (url, opts) => {
  if (opts && opts.method && opts.method !== "GET") { POST = { url, opts };
    return { ok: true, status: 200,
      json: async () => ({ utkast_id: "u-ny", status: "utkast",
        utkastversjon: 1 }) }; }
  const sti = url.split("?")[0];
  if (sti === "/v1/policymaler") {
    return { ok: true, status: 200, json: async () => ({ maler: [
      { mal_id: "netthandel", bransjemal: "netthandel-no", innhold: MAL }] }) };
  }
  if (sti === "/v1/policyutkast/u-1") {
    return { ok: true, status: 200, json: async () => ({
      utkast_id: "u-1", policy_id: "acme", status: "utkast", utkastversjon: 2,
      innhold: MAL }) };
  }
  return { ok: false, status: 404, json: async () => ({ feil: "x" }) };
};

function ctx() { return { sprak: "nb", scopes: [], tenant: "acme",
  paaUautorisert: () => {} }; }
async function vent(pred, n = 80) {
  for (let i = 0; i < n; i++) { if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0)); }
  return pred();
}
function nyHoved() {
  const b = nyttBrett();
  const m = document.createElement("main"); m.id = "hovedinnhold"; m.tabIndex = -1;
  b.append(m); return m;
}
const finnKnapp = (rot, tekst) => [...rot.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === tekst);

test("Ny: malvelger → skjema → lagre POSTer med CSRF + Idempotency-Key", async () => {
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  POST = undefined;
  const h = nyHoved();
  let aapnet = null;
  visPolicyeditor(h, ctx(), { aapneUtkast: (u) => { aapnet = u; } });

  // Malvelger.
  await vent(() => h.querySelector(".mal-liste"));
  assert.ok(h.textContent.includes(t("ui.editor.mal.netthandel")));
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
  h.querySelector(".mal-kort").dispatchEvent(new window.Event("click"));

  // Skjema: grunnopplysninger + roller + handlinger m/ modus.
  await vent(() => h.querySelector(".editor-seksjon"));
  assert.ok(h.textContent.includes(t("ui.editor.roller")));
  assert.ok(h.textContent.includes(t("ui.editor.handlinger")));
  assert.ok(h.textContent.includes("ordre.bekreft"));
  assert.ok(h.querySelector("select"), "modus-velger mangler");
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);

  // Sett policy_id (første tekstfelt = policy_id) og modus.
  const pid = h.querySelector("input.felt-inp");
  pid.value = "acme-netthandel";
  pid.dispatchEvent(new window.Event("input"));
  const sel = h.querySelector("select");
  sel.value = "alltid_stopp";
  sel.dispatchEvent(new window.Event("change"));

  // Lagre.
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  assert.equal(POST.opts.method, "POST");
  assert.ok(POST.url.includes("/v1/policyutkast"));
  assert.equal(POST.opts.headers["X-Disponit-CSRF"], "tok123");
  assert.ok(POST.opts.headers["Idempotency-Key"], "mangler Idempotency-Key");
  const sendt = JSON.parse(POST.opts.body);
  assert.equal(sendt.policy_id, "acme-netthandel");
  assert.equal(sendt.innhold.handlinger[0].modus, "alltid_stopp");
  await vent(() => aapnet === "u-ny");
  assert.equal(aapnet, "u-ny");
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
});

test("Rediger: laster utkastets innhold og PUTer med utkastversjon", async () => {
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  POST = undefined;
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { utkast_id: "u-1", aapneUtkast: () => {} });
  await vent(() => h.querySelector(".editor-seksjon"));
  // policy_id-feltet er låst ved redigering.
  assert.ok(h.querySelector("input[disabled]"), "policy_id skal være låst");
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  assert.equal(POST.opts.method, "PUT");
  assert.ok(POST.url.includes("/v1/policyutkast/u-1"));
  assert.equal(JSON.parse(POST.opts.body).utkastversjon, 2);
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
});

test("Stabil nøkkel: retry etter nettverksfeil gjenbruker Idempotency-Key", async () => {
  // Codex R1: en retry av SAMME lagring (tapt svar) må gjenbruke nøkkelen, så
  // serveren REPLAYer i stedet for å duplisere.
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  const ekte = globalThis.fetch;
  const kalt = [];
  globalThis.fetch = async (url, opts) => {
    if (opts && opts.method && opts.method !== "GET") {
      kalt.push({ url, opts });
      if (kalt.length === 1) throw new TypeError("network");   // tapt svar
      return { ok: true, status: 200,
        json: async () => ({ utkast_id: "u-ny", utkastversjon: 1 }) };
    }
    return { ok: false, status: 404, json: async () => ({ feil: "x" }) };
  };
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: MAL, aapneUtkast: () => {} });
  await vent(() => h.querySelector(".editor-seksjon"));
  const pid = h.querySelector("input.felt-inp");
  pid.value = "acme"; pid.dispatchEvent(new window.Event("input"));

  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => kalt.length === 1);
  // Re-klikk (samme innhold) → retry.
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => kalt.length >= 2);
  assert.equal(kalt[0].opts.headers["Idempotency-Key"],
    kalt[1].opts.headers["Idempotency-Key"],
    "retry med samme innhold MÅ gjenbruke idempotensnøkkelen");
  globalThis.fetch = ekte;
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
});

test("Roller: legg til og fjern re-tegner", async () => {
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: MAL, aapneUtkast: () => {} });
  await vent(() => h.querySelector(".editor-seksjon"));
  const foer = h.querySelectorAll(".editor-liste .editor-rad").length;
  finnKnapp(h, t("ui.editor.legg_til_rolle"))
    .dispatchEvent(new window.Event("click"));
  await vent(() =>
    h.querySelectorAll(".editor-liste .editor-rad").length === foer + 1);
  assert.equal(h.querySelectorAll(".editor-liste .editor-rad").length, foer + 1);
});
