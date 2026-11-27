// «Dataene dine» i kundeadmin (27/11): admin laster ned alt som én fil.
import { test } from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { eksportdel } from "../static/js/flater/kundeadmin.js";

settI18nForTest(NB, "nb");

let SVAR = 200;
const KALL = [];
globalThis.fetch = async (url, opts = {}) => {
  KALL.push({ url, opts });
  if (SVAR !== 200) {
    return { ok: false, status: SVAR, json: async () => ({ feil: "x" }),
             headers: new Map() };
  }
  return { ok: true, status: 200,
    headers: { get: (h) => h === "content-disposition"
      ? 'attachment; filename="disponit-acme-2026-11-27.json"' : null },
    blob: async () => new Blob(["{}"], { type: "application/json" }) };
};
const LAGET = [];
URL.createObjectURL = (b) => { LAGET.push(b); return "blob:eksport"; };
URL.revokeObjectURL = () => {};

async function vent(pred, n = 60) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}

const ctx = (scopes) => ({ sprak: "nb", scopes, tenant: "acme",
  paaUautorisert: () => {} });

test("Eksport: finnes ikke uten firma:eksport", () => {
  const d = eksportdel(ctx(["decisions:read"]));
  assert.equal(d.hidden, true);
  assert.equal(d.querySelector("button"), null);
});

test("Eksport: ett klikk laster ned fila med serverens filnavn, axe rent",
  async () => {
    SVAR = 200;
    const m = document.createElement("main");
    nyttBrett().append(m);
    const d = eksportdel(ctx(["firma:eksport"]));
    m.append(d);
    d.querySelector("button").click();
    assert.ok(await vent(() => d.querySelector(".melding").textContent
      .includes("disponit-acme-2026-11-27.json")));
    assert.equal(KALL.at(-1).url, "/v1/firma/eksport");
    assert.equal(KALL.at(-1).opts.credentials, "same-origin");
    assert.equal(LAGET.length, 1);
    assert.equal(d.querySelector("button").disabled, false);
    const brudd = await alvorligeBrudd(m);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Eksport: en feil blir en melding, ikke en side med rå JSON", async () => {
  SVAR = 503;
  const d = eksportdel(ctx(["firma:eksport"]));
  nyttBrett().append(d);
  d.querySelector("button").click();
  assert.ok(await vent(() => d.querySelector(".melding").textContent
    === t("ui.kundeadmin.eksport.feil")));
});
