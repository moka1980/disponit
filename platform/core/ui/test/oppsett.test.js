// «Kom i gang» på dashbordet (/v1/oppsett).
import { test } from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visOversikt } from "../static/js/flater/oversikt.js";

settI18nForTest(NB, "nb");

const OVERSIKT = { vindu_start: "2026-11-17T10:00:00+00:00",
  vindu_slutt: "2026-11-18T10:00:00+00:00", tidssone: "UTC",
  tillatt: 0, stoppet: 0, unntak: 0, totalt: 0 };

let SVAR;
globalThis.fetch = async (url) => {
  const oppf = SVAR[url.split("?")[0]];
  if (!oppf) return { ok: false, status: 404, json: async () => ({ feil: "x" }) };
  if (typeof oppf === "number") {
    return { ok: false, status: oppf, json: async () => ({ feil: "x" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

const OKONOMI = ["decisions:read", "okonomi:read"];
const ctx = (scopes = OKONOMI) => ({ sprak: "nb", scopes, tenant: "acme",
  paaUautorisert: () => {} });

async function vent(pred, n = 80) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}

function nyHoved() {
  const m = document.createElement("main");
  m.id = "hovedinnhold"; m.tabIndex = -1;
  nyttBrett().append(m);
  return m;
}

test("Mangler krav: ett kort per modul, knappen går rett til modulen", async () => {
  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": { moduler: [
    { modul: 27, tilgjengelig: true, klar: false, apne_funn: 1 },
    { modul: 14, tilgjengelig: true, klar: true, apne_funn: 3 },
    { modul: 13, tilgjengelig: true, klar: null, apne_funn: 0 },
    { modul: 41, tilgjengelig: false, klar: null, apne_funn: null }] } };
  const h = nyHoved();
  visOversikt(h, ctx());
  const blokk = h.querySelector(".dash-oppsett");
  assert.ok(await vent(() => !blokk.hidden), "blokken ble aldri vist");

  assert.equal(blokk.querySelector("h2").textContent, t("ui.oppsett.tittel"));
  assert.ok(blokk.textContent.includes("3 av 4 moduler er satt opp"));
  const prog = blokk.querySelector("progress");
  assert.equal(prog.getAttribute("value"), "3");
  assert.equal(prog.getAttribute("max"), "4");

  const kort = [...blokk.querySelectorAll(".oppsett-kort")];
  assert.equal(kort.length, 1);
  assert.equal(kort[0].querySelector("h3").textContent, t("ui.nav.lager"));
  assert.equal(kort[0].querySelector("a.knapp.primar").getAttribute("href"),
    "#/lager");

  const brikker = [...blokk.querySelectorAll(".oppsett-brikker a")];
  assert.deepEqual(brikker.map((a) => a.getAttribute("href")),
    ["#/faktura", "#/avstemming", "#/betaling"],
    "flest funn først, utilgjengelige sist");
  assert.ok(brikker[0].textContent.includes("3 åpne funn"));
  assert.ok(brikker[1].textContent.includes(t("ui.oppsett.ingen_funn")));
  assert.ok(brikker[2].textContent.includes(t("ui.oppsett.utilgjengelig")));

  // Blokken står FØR tellekortene.
  const kpi = h.querySelector(".dash-kpi");
  assert.ok(blokk.compareDocumentPosition(kpi)
    & Node.DOCUMENT_POSITION_FOLLOWING);
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

test("Alt satt opp: ingen kort, egen tittel", async () => {
  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": { moduler: [
    { modul: 27, tilgjengelig: true, klar: true, apne_funn: 0 }] } };
  const h = nyHoved();
  visOversikt(h, ctx());
  const blokk = h.querySelector(".dash-oppsett");
  assert.ok(await vent(() => !blokk.hidden));
  assert.equal(blokk.querySelector("h2").textContent,
    t("ui.oppsett.tittel_klar"));
  assert.equal(blokk.querySelectorAll(".oppsett-kort").length, 0);
  assert.ok(blokk.textContent.includes(t("ui.oppsett.alt_klart")));
});

test("Modul uten rute for økten vises ikke; tomt svar skjuler blokken", async () => {
  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": { moduler: [
    { modul: 27, tilgjengelig: true, klar: false, apne_funn: 0 }] } };
  const h = nyHoved();
  // Uten okonomi:read har økten ingen lagerrute — da skal ingen knapp
  // love en flate serveren svarer 403 på.
  visOversikt(h, ctx(["decisions:read"]));
  assert.ok(await vent(() => h.querySelector(".cards")));
  for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 0));
  assert.equal(h.querySelector(".dash-oppsett").hidden, true);
});

test("Feil fra /v1/oppsett: blokken forblir skjult, dashbordet står", async () => {
  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": 500 };
  const h = nyHoved();
  visOversikt(h, ctx());
  assert.ok(await vent(() => h.querySelector(".cards")));
  for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 0));
  assert.equal(h.querySelector(".dash-oppsett").hidden, true);
  assert.ok(h.textContent.includes(t("ui.oversikt.telling_note")));
});
