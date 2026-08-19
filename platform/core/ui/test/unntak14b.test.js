// Gate 14b (§7): avvis på sak med levende oppdrag åpner en ALERTDIALOG som
// navngir oppdraget og modulen; Escape lukker uten handling og fokus går
// tilbake til utløseren; utfallet annonseres i role="alert"; axe rent.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { sikreAlertRegion } from "../static/js/komponenter.js";

settI18nForTest(NB, "nb");

let KALL;
let SVAR;
globalThis.fetch = async (url, opts = {}) => {
  const sti = url.split("?")[0];
  KALL.push({ sti, metode: opts.method || "GET",
    kropp: opts.body ? JSON.parse(opts.body) : null });
  const svar = (typeof SVAR === "function") ? SVAR(sti, opts) : SVAR[sti];
  if (svar === undefined) {
    return { ok: false, status: 404, json: async () => ({ feil: "ikke_funnet" }) };
  }
  if (typeof svar === "number") {
    return { ok: false, status: svar, json: async () => ({ feil: "x" }) };
  }
  if (svar && svar.__status) {
    const { __status, ...kropp } = svar;
    return { ok: __status < 400, status: __status, json: async () => kropp };
  }
  return { ok: true, status: 200, json: async () => svar };
};

const DETALJ = {
  id: 7, ts: "2026-08-19T06:00:00+00:00", handling: "faktura.bokfor",
  kategori: "over_grense", sakstype: "normal", status: "manuell",
  prioritet: "normal", begrunnelse: ["rolle_ok"], saksversjon: 3,
  tillatte_handlinger: ["avvis", "eskaler"],
  avvis_kansellerer: [{ oppdrag_id: 42, status: "plukket",
                        modul_id: "m_wcag_audit" }],
};

function nyHoved() {
  const brett = nyttBrett();
  const m = document.createElement("main");
  m.id = "hovedinnhold"; m.tabIndex = -1;
  brett.append(m);
  return m;
}

async function vent(pred, n = 60) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}

async function aapneAvvisdialog(h) {
  const { visUnntak } = await import("../static/js/flater/unntak.js");
  KALL = [];
  SVAR = (sti) => {
    if (sti === "/v1/unntak") {
      return { saker: [{ id: 7, ts: DETALJ.ts, handling: DETALJ.handling,
        kategori: DETALJ.kategori, prioritet: DETALJ.prioritet,
        status: DETALJ.status, sakstype: DETALJ.sakstype }],
        neste_cursor: null };
    }
    if (sti === "/v1/unntak/7") return DETALJ;
    if (sti === "/v1/unntak/7/historikk") {
      return { rader: [], neste_cursor: null };
    }
    return undefined;
  };
  visUnntak(h, { sprak: "nb", scopes: ["exceptions:read"], tenant: "acme",
    paaUautorisert: () => {} });
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").dispatchEvent(new window.Event("click"));
  await vent(() => document.querySelector(".behandling-knapper"));
  const knapp = [...document.querySelectorAll(".behandling-knapper button")]
    .find((k) => k.textContent === t("ui.unntak.handling.avvis"));
  assert.ok(knapp, "avvis-knappen finnes (levende oppdrag stenger den ikke)");
  knapp.focus();
  knapp.dispatchEvent(new window.Event("click"));
  await vent(() => document.querySelector('[role="alertdialog"]'));
  const dlg = document.querySelector('[role="alertdialog"]');
  assert.ok(dlg, "avvis med levende oppdrag åpnet ingen alertdialog");
  return { dlg, knapp };
}

test("14b: alertdialogen navngir oppdrag+modul, beskriver seg selv, axe rent",
     async () => {
  const h = nyHoved();
  const { dlg } = await aapneAvvisdialog(h);
  // Konsekvensen er navngitt: oppdraget og modulen.
  assert.ok(dlg.textContent.includes("#42"));
  assert.ok(dlg.textContent.includes("m_wcag_audit"));
  assert.ok(dlg.textContent.includes(t("ui.unntak.kan_ikke_angres")));
  // aria-describedby peker på budskapet.
  const bid = dlg.getAttribute("aria-describedby");
  assert.ok(bid && document.getElementById(bid),
    "alertdialogen beskriver ikke seg selv");
  // Fokus er INNE i dialogen.
  assert.ok(dlg.contains(document.activeElement), "fokus fulgte ikke inn");
  const brudd = await alvorligeBrudd(dlg, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  dlg.closest(".overlegg")?.remove();
});

test("14b: Escape lukker uten handling; Avbryt returnerer fokus", async () => {
  const h = nyHoved();
  let { knapp } = await aapneAvvisdialog(h);
  const foer = KALL.filter((k) => k.metode === "POST").length;
  document.dispatchEvent(new window.KeyboardEvent("keydown",
    { key: "Escape", bubbles: true }));
  await vent(() => !document.querySelector('[role="alertdialog"]'));
  assert.ok(!document.querySelector('[role="alertdialog"]'));
  assert.equal(KALL.filter((k) => k.metode === "POST").length, foer,
    "Escape utløste en handling");
  // ... og fokus står ALDRI igjen i en revet dialog.
  assert.ok(!document.activeElement
            || !document.activeElement.closest?.('[role="alertdialog"]'));

  // Fokusreturen måles der bare TOPPDIALOGEN lukkes: Avbryt-knappen.
  ({ knapp } = await aapneAvvisdialog(nyHoved()));
  const dlg = document.querySelector('[role="alertdialog"]');
  const avbryt = [...dlg.querySelectorAll("button")]
    .find((k) => k.textContent === t("ui.avbryt"));
  avbryt.dispatchEvent(new window.Event("click"));
  await vent(() => !document.querySelector('[role="alertdialog"]'));
  assert.equal(document.activeElement, knapp,
    "fokus kom ikke tilbake til utløseren");
  assert.equal(KALL.filter((k) => k.metode === "POST").length, foer,
    "Avbryt utløste en handling");
});

test("14b: kansellert-utfallet annonseres i role=alert", async () => {
  const h = nyHoved();
  const { dlg } = await aapneAvvisdialog(h);
  SVAR = (sti, opts) => {
    if (sti === "/v1/unntak/7/handling" && opts.method === "POST") {
      return { utfall: "avvist", unntak_id: 7,
               opplosning: [{ oppdrag_id: 42,
                              oppdrag_status_ved_avvis: "plukket" }] };
    }
    if (sti === "/v1/unntak") return { rader: [], neste: null };
    if (sti === "/v1/unntak/7") return { ...DETALJ, status: "avvist",
      tillatte_handlinger: [] };
    if (sti === "/v1/unntak/7/historikk") return { rader: [] };
    return undefined;
  };
  const primar = [...dlg.querySelectorAll("button")]
    .find((k) => k.textContent === t("ui.unntak.handling.avvis"));
  primar.dispatchEvent(new window.Event("click"));
  await vent(() => sikreAlertRegion().textContent.length > 0);
  const alert = sikreAlertRegion();
  assert.equal(alert.getAttribute("role"), "alert");
  assert.ok(alert.textContent.includes(t("ui.unntak.kansellert_alert")));
  assert.ok(alert.textContent.includes("#42"), "årsaken navngir ikke oppdraget");
});

test("14b: oppdrag_utfort-409 annonseres med referansen", async () => {
  const h = nyHoved();
  const { dlg } = await aapneAvvisdialog(h);
  SVAR = (sti, opts) => {
    if (sti === "/v1/unntak/7/handling" && opts.method === "POST") {
      return { __status: 409, feil: "oppdrag_utfort", oppdrag_id: 42,
               kvitteringsref: "abc123" };
    }
    if (sti === "/v1/unntak") return { rader: [], neste: null };
    if (sti === "/v1/unntak/7") return DETALJ;
    if (sti === "/v1/unntak/7/historikk") return { rader: [] };
    return undefined;
  };
  const primar = [...dlg.querySelectorAll("button")]
    .find((k) => k.textContent === t("ui.unntak.handling.avvis"));
  primar.dispatchEvent(new window.Event("click"));
  await vent(() => sikreAlertRegion().textContent.length > 0);
  const alert = sikreAlertRegion();
  assert.ok(alert.textContent.includes(
    t("ui.unntak.oppdrag_utfort_alert")));
  assert.ok(alert.textContent.includes("abc123"),
    "kvitteringsreferansen leses ikke opp");
});
