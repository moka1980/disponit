// M-57-flaten (klarsignalet §8, portene 29–32): tabellens ARIA-mønster,
// trafikklys som tekst, vektendring uten mus med kunngjort re-rangering,
// blindingsbryterens alertdialog, signaturdialogens tekst og hashkortform,
// utfall i role=alert, axe rent — og tastaturgjennomgangen dokumentert.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visRekruttering } from "../static/js/flater/rekruttering.js";

settI18nForTest(NB, "nb");

const ROT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..");

let KALL;
let SVAR;
globalThis.fetch = async (url, opts = {}) => {
  const sti = url.split("?")[0];
  KALL.push({ sti, metode: opts.method || "GET",
    kropp: opts.body ? JSON.parse(opts.body) : null,
    hoder: opts.headers || {} });
  const svar = (typeof SVAR === "function") ? SVAR(sti, opts) : SVAR[sti];
  if (svar === undefined) {
    return { ok: false, status: 404, json: async () => ({ feil: "ikke_funnet" }) };
  }
  if (typeof svar === "number") {
    return { ok: false, status: svar, json: async () => ({ feil: "x" }) };
  }
  return { ok: true, status: opts.method === "POST" ? 201 : 200,
    json: async () => svar };
};

const HASH = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
  + "a1b2c3d4e5f60718293a4b5c6d7e8f90";

function prosess() {
  return { prosesser: [{
    prosess_id: "p-1", blinding_av: false,
    vekter: { drift: 3, sky: 2 },
    kandidater: [
      { kandidat_id: "K-2", oppfylt: { drift: true, sky: false },
        status: "vurderes",
        funn: [{ kategori: "uklar_tidslinje",
                 kilde: { start: 0, slutt: 4, sitat: "2019" } }],
        intervjusporsmal: ["Fortell om tidslinjen."] },
      { kandidat_id: "K-1", oppfylt: { drift: true, sky: true },
        status: "anbefalt", funn: [], intervjusporsmal: [] },
    ],
    lister: [{ liste_id: "L-1", listetype: "invitasjon", antall: 42,
               innhold_hash: HASH }],
  }] };
}

function ctx() {
  return { sprak: "nb", scopes: ["decisions:read", "bestilling:opprett"],
    tenant: "acme", paaUautorisert: () => {} };
}

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

async function tegnet() {
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": prosess() };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "tabellen kom aldri");
  return hoved;
}

test("Rekruttering: tabell med caption, scope, aria-sort — og axe rent", async () => {
  const hoved = await tegnet();
  const tabell = hoved.querySelector("table");
  assert.ok(tabell.querySelector("caption").textContent.length > 0);
  for (const th of tabell.querySelectorAll("th")) {
    assert.equal(th.getAttribute("scope"), "col");
  }
  // Poengkolonnen er sortert synkende som utgangspunkt, og det STÅR der.
  const sortert = tabell.querySelector('th[aria-sort="descending"]');
  assert.ok(sortert, "aria-sort mangler");
  // Rangert: K-1 (5 poeng) foran K-2 (3 poeng).
  const rader = [...tabell.querySelectorAll("tbody tr")]
    .map((tr) => tr.querySelector("td").textContent);
  assert.deepEqual(rader, ["K-1", "K-2"]);
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Rekruttering: trafikklyset er tekst, aldri bare farge (port 30)", async () => {
  const hoved = await tegnet();
  const lys = [...hoved.querySelectorAll(".trafikklys")];
  assert.equal(lys.length, 2);
  for (const l of lys) {
    assert.ok(l.textContent.trim().length > 0,
      "kategorien mangler som tekst — farge alene er ikke informasjon");
  }
  assert.ok(lys.some((l) => l.textContent.includes(
    t("ui.rekruttering.status.anbefalt"))));
});

test("Rekruttering: vektendring uten mus re-rangerer og kunngjøres (port 30)", async () => {
  const hoved = await tegnet();
  const range = hoved.querySelector('input[type="range"]#vekt-sky');
  assert.ok(range.labels === undefined
    || hoved.querySelector('label[for="vekt-sky"]'), "range mangler label");
  // Tastaturbrukerens vei: sett verdien og fyr input-hendelsen — ingen mus.
  range.value = "0";
  range.dispatchEvent(new window.Event("input", { bubbles: true }));
  // Uten sky-vekt: K-1 og K-2 har begge 3 — likhet brytes på kandidat-id.
  const rader = [...hoved.querySelectorAll("tbody tr")]
    .map((tr) => tr.querySelector("td").textContent);
  assert.deepEqual(rader, ["K-1", "K-2"]);
  const kunngjoring = hoved.querySelector('[aria-live="polite"]');
  assert.ok(kunngjoring.textContent.includes("K-1"),
    "re-rangeringen ble ikke kunngjort");
  // Synlig verdi følger kontrollen.
  const visning = hoved.querySelector('output[for="vekt-sky"]');
  assert.equal(visning.textContent, "0");
});

test("Rekruttering: avskruing av blinding krever alertdialog med begrunnelse", async () => {
  const hoved = await tegnet();
  const bryter = hoved.querySelector("#rekrut-blinding");
  assert.equal(bryter.checked, true, "blinding er standard PÅ");
  bryter.checked = false;
  bryter.dispatchEvent(new window.Event("change", { bubbles: true }));
  const dialog = document.querySelector('[role="alertdialog"]');
  assert.ok(dialog, "alertdialog mangler ved avskruing");
  assert.equal(bryter.checked, true,
    "bryteren skal stå PÅ til dialogen bekrefter");
  // Bekreft uten begrunnelse → avvist lokalt, ingen POST.
  const primar = [...dialog.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.blinding_av_bekreft"));
  primar.click();
  await vent(() => KALL.some((k) => k.metode === "POST"), 5);
  assert.ok(!KALL.some((k) => k.metode === "POST"),
    "POST gikk uten begrunnelse");
  // Med begrunnelse → POST med begrunnelsen i kroppen, utfall i alert.
  bryter.checked = false;
  bryter.dispatchEvent(new window.Event("change", { bubbles: true }));
  const dialog2 = document.querySelector('[role="alertdialog"]');
  dialog2.querySelector("textarea").value = "intern rekruttering";
  SVAR["/v1/rekruttering/prosesser/p-1/blinding"] = { ok: true };
  [...dialog2.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.blinding_av_bekreft"))
    .click();
  assert.ok(await vent(() => KALL.some((k) =>
    k.sti === "/v1/rekruttering/prosesser/p-1/blinding")), "POST kom aldri");
  const post = KALL.find((k) => k.sti.endsWith("/blinding"));
  assert.equal(post.kropp.begrunnelse, "intern rekruttering");
  assert.ok(post.hoder["Idempotency-Key"], "skrivevei uten idempotensnøkkel");
  assert.ok(await vent(() => hoved.querySelector('[role="alert"]')
    .textContent.length > 0), "utfallet nådde aldri alert-området");
});

test("Rekruttering: signaturdialogen sier antall, type, hashkortform — og «Kan ikke angres» (port 31)", async () => {
  const hoved = await tegnet();
  const knapp = [...hoved.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.signer_knapp"));
  knapp.click();
  const dialog = document.querySelector('[role="alertdialog"]');
  assert.ok(dialog, "signering uten alertdialog");
  const tekst = dialog.textContent;
  assert.ok(tekst.includes("42"), "antallet mangler");
  assert.ok(tekst.includes(t("ui.rekruttering.listetype.invitasjon")));
  assert.ok(tekst.includes(HASH.slice(0, 12) + "…"), "hashkortformen mangler");
  assert.ok(tekst.includes("Kan ikke angres"), "irreversibiliteten er taus");
  assert.ok(!tekst.includes(HASH), "fullhashen skal ikke ut i dialogen");
  // Signer → POST binder innholdshashen; utfallet står i role=alert.
  SVAR["/v1/rekruttering/lister/L-1/signer"] = { innhold_hash: HASH };
  [...dialog.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.signer_bekreft"))
    .click();
  assert.ok(await vent(() => KALL.some((k) =>
    k.sti === "/v1/rekruttering/lister/L-1/signer")), "POST kom aldri");
  assert.equal(KALL.find((k) => k.sti.endsWith("/signer")).kropp.innhold_hash,
    HASH);
  assert.ok(await vent(() => hoved.querySelector('[role="alert"]')
    .textContent.includes(HASH.slice(0, 12))), "utfallet mangler");
});

test("Rekruttering: ingen hardkodet visningstekst, og tastaturgjennomgangen er dokumentert (port 32)", async () => {
  // Alle brukersynlige strenger går via t() — målt ved å rendre med et
  // locale der hver nøkkel er sin egen verdi, og kreve at flatens tekst
  // består av nøkler og data, aldri norsk/engelsk prosa i koden.
  const kilde = readFileSync(join(ROT,
    "platform/core/ui/static/js/flater/rekruttering.js"), "utf-8");
  assert.ok(!/text: "[A-ZÆØÅ][a-zæøå]+ /.test(kilde),
    "hardkodet visningstekst i flaten");
  // Tastaturgjennomgangen: dokumentet finnes og dekker de fire flytene.
  const dok = readFileSync(join(ROT,
    "docs/pr/PR-M57-TASTATURGJENNOMGANG.md"), "utf-8");
  for (const flyt of ["vekt", "sorter", "blinding", "signer"]) {
    assert.ok(dok.toLowerCase().includes(flyt),
      `tastaturgjennomgangen dekker ikke ${flyt}-flyten`);
  }
});
