// Vilkår, personvern og databehandleravtale (235): visningen og
// plattformadminens redigering (jsdom + axe).
import { test } from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { tekstTilNoder, dokFraSti } from "../static/js/flater/juridisk.js";
import { juridiskseksjon } from "../static/js/flater/admin.js";

settI18nForTest(NB, "nb");

test("Juridisk: markdown blir trygge noder — aldri HTML fra teksten", () => {
  const m = document.createElement("div");
  m.append(...tekstTilNoder(
    "# Tittel\n\nEt **fett** og *kursivt* avsnitt\nsom fortsetter.\n\n"
    + "- ett\n- to\n  fortsatt\n\n1. først\n2. så\n\n"
    + "| A | B |\n|---|---|\n| x | y |\n\n<img src=x onerror=alert(1)>"));
  assert.equal(m.querySelector("h1").textContent, "Tittel");
  assert.equal(m.querySelector("strong").textContent, "fett");
  assert.equal(m.querySelector("em").textContent, "kursivt");
  assert.ok(m.querySelector("p").textContent.includes("som fortsetter."));
  assert.deepEqual([...m.querySelectorAll("ul li")].map((l) => l.textContent),
                   ["ett", "to fortsatt"]);
  assert.equal(m.querySelectorAll("ol li").length, 2);
  assert.equal(m.querySelector("td").textContent, "x");
  // Teksten fra basen tolkes ALDRI som HTML.
  assert.equal(m.querySelector("img"), null);
  assert.ok(m.textContent.includes("<img src=x onerror=alert(1)>"));
});

test("Juridisk: stien velger dokumentet, og ingenting annet", () => {
  assert.equal(dokFraSti("/vilkar"), "vilkar");
  assert.equal(dokFraSti("/databehandleravtale/"), "databehandleravtale");
  assert.equal(dokFraSti("/admin"), null);
});

let LISTE;
const POSTER = [];
globalThis.fetch = async (url, opts = {}) => {
  const sti = url.split("?")[0];
  if (opts.method === "POST") {
    POSTER.push({ sti, kropp: JSON.parse(opts.body || "{}") });
    return { ok: true, status: 200, json: async () => ({ versjon: 2 }) };
  }
  if (sti === "/v1/plattform/meg") {
    return { ok: true, status: 200, json: async () => ({ eier: true }) };
  }
  if (sti === "/v1/plattform/juridisk") {
    return { ok: true, status: 200, json: async () => LISTE };
  }
  return { ok: false, status: 404, json: async () => ({ feil: "x" }) };
};
async function vent(pred, n = 80) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}

test("Juridisk admin: utkast lagres, forhåndsvises og publiseres i to trinn",
  async () => {
    LISTE = { felt: ["navn", "kontakt_epost"], dokumenter: [
      { dok: "vilkar", gjeldende: { versjon: 1, tekst: "# V1",
          publisert: "2026-11-27T10:00:00+00:00", samtykker: 3 },
        utkast: { versjon: 2, tekst: "# Vilkår\n\nNytt avsnitt.",
          opprettet: "2026-11-27T11:00:00+00:00", publisert: null } },
      { dok: "personvern", gjeldende: null, utkast: null },
      { dok: "databehandleravtale", gjeldende: null,
        utkast: { versjon: 1, tekst: "# DBA", opprettet: "x", publisert: null } }] };
    POSTER.length = 0;
    const m = document.createElement("main");
    nyttBrett().append(m);
    const s = juridiskseksjon({ sprak: "nb", scopes: ["policy:read"],
      paaUautorisert: () => {} });
    m.append(s);
    assert.ok(await vent(() => !s.hidden && s.querySelector("#jur-vilkar")));
    const vilkar = s.querySelector("#jur-vilkar").closest(".jur-dok");
    assert.ok(vilkar.textContent.includes(t("ui.juridiskadmin.gjeldende")
      .replace("{v}", "1").replace("{dato}", "27.11.2026").replace("{n}", "3")));
    // Utkastet står i feltet, ikke den publiserte teksten.
    assert.equal(s.querySelector("#jur-vilkar").value,
                 "# Vilkår\n\nNytt avsnitt.");
    // Personvern uten utkast: «Publiser» er av.
    const pv = s.querySelector("#jur-personvern").closest(".jur-dok");
    assert.equal([...pv.querySelectorAll("button")].find((b) =>
      b.textContent === t("ui.juridiskadmin.publiser")).disabled, true);
    // Forhåndsvisningen er den offentlige sidens visning.
    const knapp = (rot, nokkel) => [...rot.querySelectorAll("button")]
      .find((b) => b.textContent === t(nokkel));
    knapp(vilkar, "ui.juridiskadmin.forhandsvis").click();
    assert.equal(vilkar.querySelector(".jur-forhand h1").textContent, "Vilkår");
    // Lagre utkast.
    s.querySelector("#jur-vilkar").value = "# Vilkår\n\nEndret.";
    knapp(vilkar, "ui.juridiskadmin.lagre").click();
    assert.ok(await vent(() => POSTER.length === 1));
    assert.deepEqual(POSTER[0], { sti: "/v1/plattform/juridisk/vilkar/utkast",
      kropp: { tekst: "# Vilkår\n\nEndret." } });
    // Publiser: to trinn, «Avbryt» har fokus, og avbryt sender ingenting.
    const v2 = s.querySelector("#jur-vilkar").closest(".jur-dok");
    knapp(v2, "ui.juridiskadmin.publiser").click();
    assert.equal(document.activeElement.textContent,
                 t("ui.juridiskadmin.avbryt"));
    document.activeElement.click();
    assert.equal(POSTER.length, 1);
    knapp(v2, "ui.juridiskadmin.publiser").click();
    knapp(v2, "ui.juridiskadmin.publiser_ja").click();
    assert.ok(await vent(() => POSTER.length === 2));
    assert.equal(POSTER[1].sti, "/v1/plattform/juridisk/vilkar/publiser");
    const brudd = await alvorligeBrudd(m);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });
