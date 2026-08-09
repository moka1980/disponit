// Porter: (1) ingen hardkodet synlig tekst — all chrome-tekst kommer fra
// locales/ (KONSOLIDERT §8); (2) tenant sendes ALDRI fra klienten (V3/gate 8):
// arbeidsområdet bestemmes kun av økten server-side.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, nyttBrett } from "./hjelp.js";
import { settI18nForTest } from "../static/js/i18n.js";

// Pseudo-locale: hver nøkkel → «PL_<nøkkel>». Ekte tekst som overlever i DOM
// er da IKKE hentet fra locale = hardkodet.
const PL = { _meta: NB._meta };
for (const k of Object.keys(NB)) if (k !== "_meta") PL[k] = `PL_${k}`;
settI18nForTest(PL, "nb");

// Mock-API for flate-rendring.
const DATA = {
  "/v1/oversikt": { vindu_slutt: "2026-08-09T10:00:00+00:00", tidssone: "UTC",
    tillatt: 1, stoppet: 2, unntak: 3, totalt: 6 },
  "/v1/beslutninger": { rader: [{ id: 1, ts: "2026-08-09T09:00:00+00:00",
    handling: "utbetaling", policybeslutning: "TILLAT", begrunnelse: [] }],
    neste_cursor: null },
  "/v1/unntak": { saker: [{ id: 1, ts: "2026-08-09T09:00:00+00:00",
    handling: "utbetaling", kategori: "over_grense", prioritet: "hoy",
    status: "ny", sakstype: "normal" }], neste_cursor: null },
  "/v1/policy/aktiv": { skjemaversjon: 1, policy_id: "p", versjon: "0.2.0",
    innholds_hash: "a".repeat(64), roller: [{ id: "admin",
    beskrivelse_kode: "rolle.admin" }], handlinger: [], verifikatorer: [] },
};
const kalt = [];
globalThis.fetch = async (url) => {
  kalt.push(url);
  const d = DATA[url.split("?")[0]];
  return d ? { ok: true, status: 200, json: async () => d }
           : { ok: false, status: 404, json: async () => ({ feil: "x" }) };
};

async function vent(pred, n = 60) {
  for (let i = 0; i < n; i++) { if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0)); }
  return pred();
}
function nyHoved() {
  const brett = nyttBrett();
  const m = document.createElement("main"); m.id = "hovedinnhold";
  brett.append(m); return m;
}
function ctx() { return { sprak: "nb", scopes: [], tenant: "acme",
  paaUautorisert: () => {} }; }

test("Ingen hardkodet chrome-tekst: alt kommer fra locale (pseudo-locale)", async () => {
  const { visOversikt } = await import("../static/js/flater/oversikt.js");
  const h = nyHoved();
  visOversikt(h, ctx());
  await vent(() => h.querySelector(".cards"));
  // Chrome-tekst er pseudo-oversatt (kom fra t()):
  assert.ok(h.textContent.includes("PL_ui.oversikt.tittel"), "tittel ikke fra locale");
  assert.ok(h.textContent.includes("PL_ui.oversikt.telling_note"));
  // Den EKTE norske strengen skal IKKE finnes (ville betydd hardkoding):
  assert.ok(!h.textContent.includes(NB["ui.oversikt.telling_note"]),
    "hardkodet norsk tekst i oversikt");
});

test("AppShell + policy: nav og seksjoner er lokalisert", async () => {
  const { AppShell } = await import("../static/js/komponenter.js");
  const { rot } = AppShell({ tenant: "Acme", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }, { nokkel: "policy" },
            { nokkel: "beslutninger" }, { nokkel: "unntak" }],
    paaSprak: () => {}, paaLoggUt: () => {} });
  for (const n of ["oversikt", "policy", "beslutninger", "unntak"]) {
    assert.ok(rot.textContent.includes(`PL_ui.nav.${n}`), `nav ${n} ikke lokalisert`);
  }
  assert.ok(rot.textContent.includes("PL_ui.logg_ut"));
});

test("Klienten sender ALDRI tenant (V3): ingen forespørsel bærer 'tenant'", async () => {
  kalt.length = 0;
  const { visOversikt } = await import("../static/js/flater/oversikt.js");
  const { visBeslutninger } = await import("../static/js/flater/beslutninger.js");
  const { visUnntak } = await import("../static/js/flater/unntak.js");
  const { visPolicy } = await import("../static/js/flater/policy.js");
  for (const fn of [visOversikt, visBeslutninger, visUnntak, visPolicy]) {
    fn(nyHoved(), ctx());
  }
  await vent(() => kalt.length >= 4);
  for (const url of kalt) {
    assert.ok(!/tenant/i.test(url), `forespørsel bar tenant: ${url}`);
  }
});
