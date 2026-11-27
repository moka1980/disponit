// «Om Disponit» i plattformadmin (234): eieren redigerer leverandøren selv.
import { test } from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { leverandorseksjon } from "../static/js/flater/admin.js";

settI18nForTest(NB, "nb");

let EIER = true;
let LEV = null;
const POSTER = [];
globalThis.fetch = async (url, opts = {}) => {
  const sti = url.split("?")[0];
  if (opts.method === "POST") {
    const kropp = JSON.parse(opts.body);
    POSTER.push({ sti, kropp });
    LEV = { ...kropp, endret: "2026-11-27T12:00:00+00:00" };
    return { ok: true, status: 200, json: async () => ({ leverandor: LEV }) };
  }
  if (sti === "/v1/plattform/meg") {
    return { ok: true, status: 200, json: async () => ({ eier: EIER }) };
  }
  if (sti === "/v1/plattform/leverandor") {
    return { ok: true, status: 200, json: async () => ({ leverandor: LEV }) };
  }
  return { ok: false, status: 404, json: async () => ({ feil: "x" }) };
};

async function vent(pred, n = 60) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}
const ctx = { sprak: "nb", scopes: ["policy:read"], tenant: "disponit",
  paaUautorisert: () => {} };

test("Leverandør: finnes ikke for den som ikke er plattformeier", async () => {
  EIER = false;
  const s = leverandorseksjon(ctx);
  await vent(() => false, 10);
  assert.equal(s.hidden, true);
  assert.equal(s.querySelector("form"), null);
});

test("Leverandør: tomt skjema sier det, feil org.nr. sendes ikke, riktig lagres",
  async () => {
    EIER = true; LEV = null; POSTER.length = 0;
    const m = document.createElement("main");
    nyttBrett().append(m);
    const s = leverandorseksjon(ctx);
    m.append(s);
    assert.ok(await vent(() => !s.hidden && s.querySelector("form")));
    assert.ok(s.textContent.includes(t("ui.omdisponit.tekst_tom")));
    s.querySelector("#lev-navn").value = "Disponit";
    s.querySelector("#lev-orgnummer").value = "12345";
    s.querySelector("form").requestSubmit();
    assert.equal(POSTER.length, 0, "et ugyldig org.nr. skal ikke sendes");
    assert.equal(s.querySelector(".melding").textContent,
                 t("ui.omdisponit.feil.orgnummer"));
    s.querySelector("#lev-orgnummer").value = "123 456 789";
    s.querySelector("#lev-kontakt_epost").value = "post@disponit.example";
    s.querySelector("#lev-verneting").value = "Lillestrøm tingrett";
    s.querySelector("form").requestSubmit();
    assert.ok(await vent(() => POSTER.length === 1));
    assert.deepEqual(POSTER[0].kropp, { navn: "Disponit",
      orgnummer: "123456789", adresse: null,
      kontakt_epost: "post@disponit.example",
      verneting: "Lillestrøm tingrett" });
    assert.ok(await vent(() => s.querySelector(".melding").textContent
      === t("ui.omdisponit.lagret")));
    // Skjemaet står igjen med det som ble lagret.
    assert.equal(s.querySelector("#lev-orgnummer").value, "123456789");
    const brudd = await alvorligeBrudd(m);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });
