// Plattformens innstillinger (236) og Plattform-sidens faner (jsdom + axe).
//
// Eier 27/11: «gjøre de siste innstillingene som fortsatt ligger i koden
// mulig å endre i plattformadmin» og «når man klikker plattform, siden blir
// lang … disse bør organiseres som tabmeny».
//
// MUTASJONER SOM DREPER DENNE: dropp `.kpi-kort-liste`-omslaget i visAdmin
// (fanene forsvinner), slå sammen firmalisten og opprettingen igjen (én
// fane for lite), send skjemaet uten å sjekke grensene (POST med 0 døgn),
// la registreringen vise alle bransjer uansett hva serveren sier.
import { test } from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { innstillingsseksjon, visAdmin } from "../static/js/flater/admin.js";
import { visFirmaregistrering } from "../static/js/flater/firmaregistrering.js";
import { seksjonsfaner } from "../static/js/seksjonsfaner.js";

settI18nForTest(NB, "nb");

let EIER = true;
let INNST = { prove_dogn: 30, bransjer: ["tjenestebedrift", "handverk-bygg",
  "netthandel"], endret: null };
const POSTER = [];
globalThis.fetch = async (url, opts = {}) => {
  const sti = url.split("?")[0];
  const ok = (d) => ({ ok: true, status: 200, json: async () => d });
  if (opts.method === "POST") {
    const kropp = JSON.parse(opts.body);
    POSTER.push({ sti, kropp });
    INNST = { ...kropp, endret: "2026-11-27T12:00:00+00:00" };
    return ok({ innstillinger: INNST });
  }
  if (sti === "/v1/plattform/meg") return ok({ eier: EIER });
  if (sti === "/v1/plattform/innstillinger") {
    return ok({ innstillinger: INNST, grenser: { prove_dogn: [1, 365] },
      alle_bransjer: ["tjenestebedrift", "handverk-bygg", "netthandel"] });
  }
  if (sti === "/v1/registrering/valg") {
    return ok({ prove_dogn: INNST.prove_dogn, bransjer: INNST.bransjer });
  }
  if (sti === "/v1/plattform/firmaer") return ok({ firmaer: [] });
  if (sti === "/v1/plattform/leverandor") return ok({ leverandor: null });
  if (sti === "/v1/plattform/juridisk") {
    return ok({ dokumenter: [], felt: [] });
  }
  if (sti === "/v1/juridisk") return ok({ gjeldende: {}, kreves: {} });
  if (sti === "/v1/plattform/pliktkatalog") {
    return ok({ myndigheter: ["skatteetaten"], maler: [] });
  }
  return { ok: false, status: 404, json: async () => ({ feil: "x" }) };
};

async function vent(pred, n = 200) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}
const ctx = { sprak: "nb", scopes: ["policy:read"], tenant: "disponit",
  roller: ["admin"], tenanter: [], paaUautorisert: () => {} };

function nyHoved() {
  const m = document.createElement("main");
  nyttBrett().append(m);
  return m;
}

test("Innstillinger: finnes ikke for den som ikke er plattformeier", async () => {
  EIER = false;
  const s = innstillingsseksjon(ctx);
  await vent(() => false, 20);
  assert.equal(s.hidden, true);
  assert.equal(s.querySelector("form"), null);
  EIER = true;
});

test("Innstillinger: grensene sies ved feltet, riktige verdier lagres",
  async () => {
    POSTER.length = 0;
    const m = nyHoved();
    const s = innstillingsseksjon(ctx);
    m.append(s);
    assert.ok(await vent(() => !s.hidden && s.querySelector("form")));
    const dogn = s.querySelector("#innst-dogn");
    assert.equal(dogn.value, "30");
    dogn.value = "0";
    s.querySelector("form").requestSubmit();
    assert.equal(POSTER.length, 0, "0 døgn skal ikke sendes");
    assert.equal(s.querySelector(".melding").textContent,
                 t("ui.innstillinger.feil.dogn"));
    dogn.value = "14";
    for (const b of s.querySelectorAll("input[name=bransjer]")) b.checked = false;
    s.querySelector("form").requestSubmit();
    assert.equal(POSTER.length, 0, "ingen bransje skal ikke sendes");
    assert.equal(s.querySelector(".melding").textContent,
                 t("ui.innstillinger.feil.bransjer"));
    s.querySelector("#innst-b-netthandel").checked = true;
    s.querySelector("#innst-b-tjenestebedrift").checked = true;
    s.querySelector("form").requestSubmit();
    assert.ok(await vent(() => POSTER.length === 1));
    assert.deepEqual(POSTER[0], { sti: "/v1/plattform/innstillinger",
      kropp: { prove_dogn: 14, bransjer: ["tjenestebedrift", "netthandel"] } });
    assert.ok(await vent(() => s.querySelector(".melding").textContent
      === t("ui.innstillinger.lagret")));
    assert.equal(s.querySelector("#innst-dogn").value, "14");
    assert.equal(s.querySelector("#innst-b-handverk-bygg").checked, false);
    const brudd = await alvorligeBrudd(m);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Registreringen viser bare bransjene som tilbys, og eierens antall dager",
  async () => {
    INNST = { prove_dogn: 14, bransjer: ["netthandel"], endret: null };
    const m = nyHoved();
    visFirmaregistrering(m, { ...ctx, tenant: "_registrering" });
    const valg = m.querySelector("#firmareg-bransje");
    assert.ok(await vent(() => valg.options.length === 1));
    assert.equal(valg.value, "netthandel");
    assert.ok(await vent(() => m.querySelector(".sub").textContent
      === t("ui.firmareg.undertittel").replace("{n}", "14")));
  });

test("Plattform: eierens seksjoner er faner, ikke én lang side", async () => {
  EIER = true;
  const m = nyHoved();
  const stopp = seksjonsfaner(m);
  try {
    visAdmin(m, ctx);
    const forventet = [t("ui.profil.tittel"), t("ui.plattform.firmaer"),
      t("ui.plattform.nytt_firma"), t("ui.innstillinger.tittel"),
      t("ui.pliktkatalog.tittel"),
      t("ui.omdisponit.tittel"), t("ui.juridiskadmin.tittel"),
      t("ui.admin.drift_fane")];
    const faner = () => [...m.querySelectorAll('[role="tab"]')]
      .map((f) => f.textContent.trim());
    assert.ok(await vent(() => faner().length === forventet.length),
      `faner: ${faner().join(" | ")}`);
    assert.deepEqual(faner(), forventet);
    // ÉN SEKSJON OM GANGEN: bare profilen står fram.
    const paneler = [...m.querySelectorAll('[role="tabpanel"]')];
    assert.equal(paneler.filter((p) => !p.hidden).length, 1);
    const innst = [...m.querySelectorAll('[role="tab"]')]
      .find((f) => f.textContent.trim() === t("ui.innstillinger.tittel"));
    innst.click();
    assert.ok(await vent(() => !m.querySelector("#innst-dogn")
      .closest("section").hidden));
    assert.equal(m.querySelector("#pf-navn").closest("section").hidden, true);
    const brudd = await alvorligeBrudd(m);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  } finally { stopp(); }
});

test("Plattform: uten plattformfullmakt er det ingen fanerad", async () => {
  EIER = false;
  const m = nyHoved();
  const stopp = seksjonsfaner(m);
  try {
    visAdmin(m, ctx);
    await vent(() => false, 40);
    assert.equal(m.querySelectorAll('[role="tab"]').length, 0,
      "profil + drift er to seksjoner — under terskelen for faner");
    assert.ok(m.textContent.includes(t("ui.profil.tittel")));
  } finally { stopp(); EIER = true; }
});
