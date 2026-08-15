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
            { nokkel: "beslutninger" }, { nokkel: "unntak" },
            { nokkel: "kundeadmin" }, { nokkel: "policyadmin" },
            { nokkel: "admin" }],
    paaSprak: () => {}, paaLoggUt: () => {} });
  for (const n of ["oversikt", "policy", "beslutninger", "unntak",
                   "kundeadmin", "policyadmin", "admin"]) {
    assert.ok(rot.textContent.includes(`PL_ui.nav.${n}`), `nav ${n} ikke lokalisert`);
  }
  assert.ok(rot.textContent.includes("PL_ui.logg_ut"));
});

test("Landing og nye adminflater: synlig tekst kommer fra locale, ikke hardkoding", async () => {
  const fetch0 = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const sti = url.split("?")[0];
    if (sti === "/ui/oppsett.json") {
      return { ok: true, status: 200, json: async () => ({ provider_id: "google" }) };
    }
    return { ok: false, status: 404, json: async () => ({ feil: "x" }) };
  };
  const { visInnlogging } = await import("../static/js/innlogging.js");
  const { visKundeadmin } = await import("../static/js/flater/kundeadmin.js");
  const { visAdmin } = await import("../static/js/flater/admin.js");
  const hoved = nyHoved();
  // `#app` opprettes ETTER `nyHoved()`, ikke før: `nyttBrett()` gjør
  // `document.body.replaceChildren()`, så en div som lages først blir koblet
  // FRA brettet igjen, og `visInnlogging()` finner ingenting å skrive i.
  // Skallet (`static/index.html`) har `<div id="app">` ved siden av
  // hovedinnholdet, og det er den formen testen skal etterligne.
  const app = document.createElement("div");
  app.id = "app";
  document.body.append(app);

  // Teksten samles opp ETTER hver rendring, ikke én gang til slutt: begge
  // flatene eier `hovedinnhold` og skriver med `sett()` (replaceChildren), så
  // en avsluttende avlesning ville bare sett den SISTE flata — og påstanden om
  // kundeadmin ville feilet på at admin rendret etterpå, ikke på locale.
  await visInnlogging();
  let tekst = document.body.textContent;
  visKundeadmin(hoved, ctx());
  tekst += document.body.textContent;
  // Tenantradene er DATA fra den autentiserte veien, ikke locale-nøkler, så
  // de sendes inn her. Selve locale-påstanden under gjelder chrome-teksten.
  visAdmin(hoved, { ...ctx(), scopes: ["platform:admin"],
    tenanter: [{ id: "alfa", navn: "Alfa", plan: "Pilot", moduler: [1],
                 neste: "M-2" }] });
  tekst += document.body.textContent;
  for (const k of [
    "site.hero.tittel",
    "site.modul.m1.navn",
    "ui.kundeadmin.tittel",
    "ui.admin.tittel",
  ]) {
    assert.ok(tekst.includes(`PL_${k}`), `${k} ikke fra locale`);
  }
  assert.ok(!tekst.includes("Policy- og fullmaktsmotor"),
    "hardkodet modulnavn lekket til DOM");
  globalThis.fetch = fetch0;
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
