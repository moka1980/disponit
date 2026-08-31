// Kandidatens tidsvalg (M-8, 082 — planen §4 + §0): den innloggingsfrie
// flaten skal være WCAG-ren fra første strek. Portene: fieldset/legend
// med radio per slot, fulle slots disabled MED tekstlig «fullt» (DOM 4),
// bekreftelse i role=status med valgt tid gjentatt (DOM 3: gjenbesøk
// viser bekreftelsen), UNIFORM avvisningstekst i role=alert uten
// årsaksskille — og tokenet forlater aldri klienten annet enn i
// POST-kroppen.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { t } from "../static/js/i18n.js";
import { start } from "../static/js/flater/tidsvalg.js";

const TOKEN = "tid_" + "a".repeat(32) + "." + "b".repeat(64);

let KALL;
let SVAR;
globalThis.fetch = async (url, opts = {}) => {
  if (String(url).startsWith("/ui/locale/")) {
    return { ok: true, status: 200, json: async () => NB };
  }
  const kropp = opts.body ? JSON.parse(opts.body) : null;
  KALL.push({ url: String(url), kropp, opts });
  const svar = typeof SVAR === "function" ? SVAR(String(url), kropp)
    : SVAR[String(url)];
  if (svar === undefined) {
    return { ok: false, status: 404, json: async () => ({}) };
  }
  if (svar.__status) {
    return { ok: svar.__status < 400, status: svar.__status,
      json: async () => (svar.__kropp ?? {}) };
  }
  return { ok: true, status: 200, json: async () => svar };
};

function oppslag(slots, valgt = null) {
  return { valgt_slot: valgt, slots };
}

const SLOTS = [
  { slot_id: "S-1", start: "2026-09-10T09:00:00+00:00",
    slutt: "2026-09-10T10:00:00+00:00", ledig: true },
  { slot_id: "S-2", start: "2026-09-11T09:00:00+00:00",
    slutt: "2026-09-11T10:00:00+00:00", ledig: false },
];

async function vent(pred, n = 60) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}

function nyRot() {
  const brett = nyttBrett();
  const rot = document.createElement("main");
  rot.setAttribute("aria-busy", "true");
  brett.append(rot);
  return rot;
}

test("kandidatsiden: fieldset/legend, fulle slots disabled med tekstlig «fullt» — og axe rent", async () => {
  KALL = [];
  SVAR = { "/v1/tidsvalg/oppslag": oppslag(SLOTS) };
  window.location.hash = "#" + TOKEN;
  const rot = nyRot();
  await start(rot);
  // Tokenet gikk i KROPPEN — aldri i URL-en til kallet.
  assert.equal(KALL.length, 1);
  assert.equal(KALL[0].url, "/v1/tidsvalg/oppslag");
  assert.equal(KALL[0].kropp.token, TOKEN);
  assert.equal(KALL[0].opts.credentials, "omit",
    "kandidatsiden skal aldri sende cookies");
  const legend = rot.querySelector("fieldset legend");
  assert.ok(legend && legend.textContent === t("ui.tidsvalg.velg_tittel"));
  const radioer = [...rot.querySelectorAll("input[type=radio]")];
  assert.equal(radioer.length, 2);
  assert.ok(!radioer[0].disabled && radioer[1].disabled,
    "den fulle sloten skal være disabled");
  // «Fullt» er TEKST i den fulle slotens label — aldri bare en tilstand.
  const fullLabel = rot.querySelector('label[for="tidsvalg-slot-1"]');
  assert.ok(fullLabel.textContent.includes(t("ui.tidsvalg.fullt")));
  // DOM 4: aldri tellere i kandidatens DOM.
  assert.ok(!rot.textContent.includes("kapasitet"),
    "kandidaten skal aldri se kapasitet");
  const brudd = await alvorligeBrudd(rot, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("kandidatsiden: valget bekreftes i role=status med tiden gjentatt — og axe rent", async () => {
  KALL = [];
  SVAR = { "/v1/tidsvalg/oppslag": oppslag(SLOTS),
           "/v1/tidsvalg/velg": { valgt: true,
             start: "2026-09-10T09:00:00+00:00",
             slutt: "2026-09-10T10:00:00+00:00" } };
  window.location.hash = "#" + TOKEN;
  const rot = nyRot();
  await start(rot);
  rot.querySelector("input[type=radio]").checked = true;
  rot.querySelector("form button[type=submit]").click();
  assert.ok(await vent(() => rot.querySelector('[role="status"]')),
    "bekreftelsen kom aldri i role=status");
  const velg = KALL.find((k) => k.url === "/v1/tidsvalg/velg");
  assert.ok(velg && velg.kropp.token === TOKEN
    && velg.kropp.slot_id === "S-1");
  const status = rot.querySelector('[role="status"]');
  assert.ok(status.textContent.includes(t("ui.tidsvalg.bekreftet")));
  assert.ok(status.querySelector("time"),
    "den valgte tiden skal GJENTAS i bekreftelsen");
  const brudd = await alvorligeBrudd(rot, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("kandidatsiden: gjenbesøk med brukt token viser bekreftelsen (DOM 3)", async () => {
  KALL = [];
  SVAR = { "/v1/tidsvalg/oppslag": oppslag(SLOTS, "S-1") };
  window.location.hash = "#" + TOKEN;
  const rot = nyRot();
  await start(rot);
  assert.ok(rot.querySelector('[role="status"]'),
    "gjenbesøket skal vise bekreftelsen, aldri et nytt valg");
  assert.ok(!rot.querySelector("form"),
    "valget er endelig — ingen ny velger på et brukt token");
});

test("kandidatsiden: ÉN uniform avvisningstekst — 403 og 500 er samme dom", async () => {
  const tekster = [];
  for (const svar of [{ __status: 403,
                        __kropp: { feil: "tidsvalg_avvist" } },
                      { __status: 500, __kropp: { feil: "intern" } }]) {
    KALL = [];
    SVAR = { "/v1/tidsvalg/oppslag": svar };
    window.location.hash = "#" + TOKEN;
    const rot = nyRot();
    await start(rot);
    const alert = rot.querySelector('[role="alert"]');
    assert.ok(alert, "avvisningen skal stå i role=alert");
    tekster.push(alert.textContent);
  }
  assert.equal(tekster[0], tekster[1],
    "årsaksskille i avvisningsteksten — kandidatsiden skal ikke kunne"
    + " brukes som orakel");
  assert.equal(tekster[0], t("ui.tidsvalg.avvist"));
});

test("kandidatsiden: manglende/feilformet token avvises UTEN et eneste API-kall", async () => {
  for (const hash of ["", "#ikke-et-token", "#tid_kort.feil"]) {
    KALL = [];
    SVAR = {};
    window.location.hash = hash;
    const rot = nyRot();
    await start(rot);
    assert.equal(KALL.length, 0,
      `flaten ringte serveren med et token uten form (${hash})`);
    assert.equal(rot.querySelector('[role="alert"]').textContent,
      t("ui.tidsvalg.avvist"));
  }
});

test("kandidatsiden: slot_fullt sies og listen hentes på nytt", async () => {
  KALL = [];
  let oppslagNr = 0;
  SVAR = (url) => {
    if (url === "/v1/tidsvalg/oppslag") {
      oppslagNr += 1;
      return oppslag(oppslagNr === 1 ? SLOTS
        : [{ ...SLOTS[0], ledig: false }, SLOTS[1]]);
    }
    return { __status: 409, __kropp: { feil: "slot_fullt" } };
  };
  window.location.hash = "#" + TOKEN;
  const rot = nyRot();
  await start(rot);
  rot.querySelector("input[type=radio]").checked = true;
  rot.querySelector("form button[type=submit]").click();
  assert.ok(await vent(() => oppslagNr === 2),
    "listen ble aldri hentet på nytt etter slot_fullt");
  assert.ok(await vent(() => rot.querySelector('[role="alert"]')
    && rot.querySelector('[role="alert"]').textContent
      .includes(t("ui.tidsvalg.slot_fullt"))),
    "slot_fullt-teksten kom aldri");
  // Begge slots er nå fulle og disabled — men fortsatt synlige som tekst.
  assert.ok(await vent(() =>
    [...rot.querySelectorAll("input[type=radio]")]
      .every((r) => r.disabled)));
});
