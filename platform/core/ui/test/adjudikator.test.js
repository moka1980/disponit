// Adjudikatorkøen (041 §5-6): egen visning bak `domains:adjudicate`,
// partene synlige KUN her, axe rent (port 40) — og domenefanens
// forklaringstekster for avklaring/tilbakekalling uten motpartens identitet.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visAdjudikator } from "../static/js/flater/adjudikator.js";
import { visDomener } from "../static/js/flater/domener.js";
import { byggRuter } from "../static/js/sitekart.js";

settI18nForTest(NB, "nb");

let KALL;
let SVAR;
globalThis.fetch = async (url, opts = {}) => {
  const sti = url.split("?")[0];
  // `url` (med spørrestreng) føres med: pagineringsporten måler at
  // cursoren faktisk sendes videre, ikke bare at et kall skjedde.
  KALL.push({ sti, url, metode: opts.method || "GET" });
  const svar = (typeof SVAR === "function") ? SVAR(sti, opts) : SVAR[sti];
  if (svar === undefined) {
    return { ok: false, status: 404, json: async () => ({ feil: "ikke_funnet" }) };
  }
  return { ok: true, status: 200, json: async () => svar };
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

const SAK = {
  unntak_id: 7, hostname: "kunde.example", saksrevisjon: 1,
  autorisasjonsgenerasjon: 2, utfordrer_tenant: "utfordrer-as",
  tapt_tenant: "taper-as", status: "ny", ts: "2026-08-19T00:00:00+00:00",
};

test("adjudikatorkøen: tabell med parter, caption/scope, axe rent", async () => {
  KALL = []; SVAR = { "/v1/domeneovertakelse/saker": { saker: [SAK] } };
  const h = nyHoved();
  visAdjudikator(h, { paaUautorisert: () => {} });
  await vent(() => h.querySelector(".adjudikatorliste table"));
  const tabell = h.querySelector(".adjudikatorliste table");
  // Partene er SYNLIGE her — dette er den ene flaten som skal vise dem.
  assert.ok(tabell.textContent.includes("utfordrer-as"));
  assert.ok(tabell.textContent.includes("taper-as"));
  assert.ok(tabell.querySelector("caption"));
  assert.ok(tabell.textContent.includes(t("ui.adjudikator.status.ny")));
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("adjudikatorkøen: tom kø er en tilstand, ikke en tom tabell", async () => {
  KALL = []; SVAR = { "/v1/domeneovertakelse/saker": { saker: [] } };
  const h = nyHoved();
  visAdjudikator(h, { paaUautorisert: () => {} });
  await vent(() => h.textContent.includes(t("ui.adjudikator.tom_tittel")));
  assert.ok(!h.querySelector(".adjudikatorliste table"));
});

test("adjudikatorkøen: «vis mer» bærer cursoren og legger til, ikke bytter",
     async () => {
  // Køen er ubundet i tid (saker står åpne til et menneske avgjør dem), så
  // siden er keyset-paginert. Flaten skal da BLA, ikke skjule resten.
  const SAK2 = { ...SAK, unntak_id: 8, hostname: "annen.example",
    ts: "2026-08-19T01:00:00+00:00" };
  KALL = [];
  SVAR = (sti) => {
    if (sti !== "/v1/domeneovertakelse/saker") return undefined;
    const forrige = KALL[KALL.length - 1] || {};
    return forrige.url && forrige.url.includes("cursor=c1")
      ? { saker: [SAK2], neste_cursor: null }
      : { saker: [SAK], neste_cursor: "c1" };
  };
  const h = nyHoved();
  visAdjudikator(h, { paaUautorisert: () => {} });
  await vent(() => h.querySelector(".adjudikatorliste table"));
  assert.equal(h.querySelectorAll(".adjudikatorliste tbody tr").length, 1);

  const mer = [...h.querySelectorAll(".cursornav button")]
    .find((b) => b.textContent === t("ui.vis_mer"));
  assert.ok(mer, "«vis mer» mangler selv om serveren ga en neste_cursor");
  mer.click();
  await vent(() => h.querySelectorAll(".adjudikatorliste tbody tr").length === 2);
  const tekst = h.querySelector(".adjudikatorliste table").textContent;
  assert.ok(tekst.includes("kunde.example") && tekst.includes("annen.example"),
    "andre side erstattet den første i stedet for å legge til");
  assert.ok(KALL.some((k) => k.url.includes("cursor=c1")),
    "cursoren ble ikke sendt med");
  // Siste side: ingen `neste_cursor` → ingen «vis mer» igjen.
  assert.ok(![...h.querySelectorAll(".cursornav button")]
    .some((b) => b.textContent === t("ui.vis_mer")));
});

test("sitekartet: adjudikator-ruten finnes KUN for adjudikasjonsscopet", () => {
  const uten = byggRuter({ scopes: ["decisions:read", "exceptions:read",
                                    "bestilling:opprett"] });
  assert.ok(!uten.some((r) => r.nokkel === "adjudikator"),
    "en kunderolle fikk adjudikatorkøen i navigasjonen");
  const med = byggRuter({ scopes: ["decisions:read", "domains:adjudicate"] });
  assert.ok(med.some((r) => r.nokkel === "adjudikator"));
});

test("domenefanen: avklaring og tilbakekalling forklares uten motpartens" +
     " identitet, axe rent", async () => {
  KALL = [];
  SVAR = { "/v1/domener": { domener: [
    { hostname: "a.example", status: "avklaring_kreves", wildcard: false,
      verifisert_ts: null, utloper: null,
      siste_vellykkede_revalidering: null,
      challenge_utstedt: null, challenge_utloper: null },
    { hostname: "b.example", status: "tilbakekalt", wildcard: false,
      verifisert_ts: null, utloper: null,
      siste_vellykkede_revalidering: null,
      challenge_utstedt: null, challenge_utloper: null },
  ] } };
  const h = nyHoved();
  visDomener(h, { paaUautorisert: () => {} });
  await vent(() => h.querySelector(".domeneliste table"));
  const tekst = h.querySelector(".domeneliste table").textContent;
  // Forklaringen står i CELLEN — tekst, ikke bare et statusord.
  assert.ok(tekst.includes(t("domenestatus.avklaring_kreves.forklaring")));
  assert.ok(tekst.includes(t("domenestatus.tilbakekalt.forklaring")));
  // ... og skjermleserveien er koblet: statusordet peker på forklaringen.
  const peker = h.querySelector('[aria-describedby^="dm-forklaring-"]');
  assert.ok(peker, "statusordet mangler aria-describedby");
  assert.ok(h.querySelector(`#${peker.getAttribute("aria-describedby")}`),
    "aria-describedby peker på et element som ikke finnes");
  // Ingen kryssidentitet: motpartens navn finnes ikke i svaret og dermed
  // ikke i flaten — men porten måler at TEKSTENE heller ikke bærer noen.
  assert.ok(!tekst.match(/utfordrer-as|taper-as/));
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});
