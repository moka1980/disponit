// Skallets feilpolitikk for utrullingsdata: tilleggsdata kan mangle, men en
// borte økt skal aldri se ut som et vellykket tomt svar.
import test from "node:test";
import assert from "node:assert/strict";
import "./hjelp.js";
import { hentUtrullingForSkall, UautorisertFeil, IngenTilgangFeil }
  from "../static/js/api.js";

function medSvar(svar, fn) {
  const ekte = globalThis.fetch;
  globalThis.fetch = async () => svar;
  return fn().finally(() => { globalThis.fetch = ekte; });
}

const svar = (status, kropp = {}) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => kropp,
});

test("hentUtrullingForSkall: 200 gir radene", async () => {
  const ut = await medSvar(svar(200, { tenanter: [{ tenant: "alfa" }] }),
    () => hentUtrullingForSkall("nb"));
  assert.deepEqual(ut.tenanter, [{ tenant: "alfa" }]);
});

test("hentUtrullingForSkall: 403 er tomme tenantfelt, ikke en felt app", async () => {
  // Mangler økten scopet bak endepunktet, er utrullingen bare fraværende —
  // flatene har tomtilstand for det, og skallet skal rendres.
  const ut = await medSvar(svar(403, { feil: "ingen_tilgang" }),
    () => hentUtrullingForSkall("nb"));
  assert.deepEqual(ut, {});
});

test("hentUtrullingForSkall: 5xx og nettverksfeil felles heller ikke appen", async () => {
  assert.deepEqual(await medSvar(svar(500), () => hentUtrullingForSkall("nb")), {});

  const ekte = globalThis.fetch;
  globalThis.fetch = async () => { throw new TypeError("nede"); };
  try {
    assert.deepEqual(await hentUtrullingForSkall("nb"), {});
  } finally { globalThis.fetch = ekte; }
});

test("hentUtrullingForSkall: 401 slukes IKKE", async () => {
  // Økten kan ha utløpt eller blitt tilbakekalt ETTER at `/v1/sesjon` svarte.
  // Gjorde denne catchen 401 om til `{}`, rendret `start()` et autentisert
  // skall på foreldede øktdata i stedet for å gå til innlogging.
  await assert.rejects(
    () => medSvar(svar(401, { feil: "uautorisert" }),
      () => hentUtrullingForSkall("nb")),
    (e) => {
      assert.ok(e instanceof UautorisertFeil);
      assert.ok(!(e instanceof IngenTilgangFeil));
      assert.equal(e.status, 401);
      return true;
    });
});
