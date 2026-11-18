// Eiervelgeren: rå bruker-id erstattes av en nedtrekksliste med navn.
import { test } from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { lagVelger, berikEierfelt } from "../static/js/eiervelger.js";

settI18nForTest(NB, "nb");

let MEDLEMMER = null;
globalThis.fetch = async (url) => {
  if (url.split("?")[0] === "/v1/firma/medlemmer" && MEDLEMMER) {
    return { ok: true, status: 200, json: async () => MEDLEMMER };
  }
  return { ok: false, status: 404, json: async () => ({ feil: "x" }) };
};

function skjemaMedFelt() {
  const brett = document.createElement("form");
  nyttBrett().append(brett);
  const lab = document.createElement("label");
  lab.setAttribute("for", "x-eier");
  lab.textContent = "Eier";
  const input = document.createElement("input");
  input.id = "x-eier"; input.name = "eier_bruker_id"; input.required = true;
  input.setAttribute("aria-describedby", "x-eier-hjelp");
  const hjelp = document.createElement("p");
  hjelp.id = "x-eier-hjelp"; hjelp.textContent = "hjelp";
  brett.append(lab, input, hjelp);
  return { brett, input };
}

test("Velgeren er påkrevd og TOM, og verdien går via det gamle feltet", async () => {
  const { brett, input } = skjemaMedFelt();
  const valg = lagVelger(input, [
    { bruker_id: "bid_a", navn: "Kari Nordmann" },
    { bruker_id: "bid_b", navn: null }]);
  assert.equal(brett.querySelector("#x-eier"), valg);
  assert.equal(valg.tagName, "SELECT");
  assert.equal(valg.getAttribute("required"), "");
  assert.equal(valg.getAttribute("aria-describedby"), "x-eier-hjelp");
  assert.equal(input.value, "", "den innloggede skal aldri være forhåndsvalgt");
  assert.equal(valg.options[0].textContent, t("ui.eier.velg"));
  assert.equal(valg.options[1].textContent, "Kari Nordmann");
  assert.equal(valg.options[2].textContent, t("ui.eier.uten_navn"));
  valg.value = "bid_a";
  assert.equal(input.value, "bid_a");
  input.value = "";
  assert.equal(valg.value, "");
  assert.equal((await alvorligeBrudd(brett, { fragment: true })).length, 0);
});

test("Feiler hentingen, står det opprinnelige feltet urørt", async () => {
  MEDLEMMER = null;
  const { brett, input } = skjemaMedFelt();
  assert.equal(await berikEierfelt(input), null);
  assert.equal(brett.querySelector("#x-eier"), input);
  input.value = "bid_x";
  assert.equal(input.value, "bid_x");
});

test("Tom medlemsliste gir ingen velger", async () => {
  MEDLEMMER = { medlemmer: [] };
  const { brett, input } = skjemaMedFelt();
  assert.equal(await berikEierfelt(input), null);
  assert.equal(brett.querySelector("#x-eier").tagName, "INPUT");
});
