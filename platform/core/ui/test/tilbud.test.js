// M-26 tilbudsflaten (169, ARC B tilbud PR 1) — flateporten (jsdom + axe).
//   * Lista og detaljen viser masken, aldri adressen.
//   * Tallene er dørens: flaten regner ingen pris (belopTekst er heltall).
//   * Fakta mot boka står som TEKST (WCAG 1.4.1).
//   * Ingen «send»-knapp: sendingen er plattformens arm.
//   * En lesende økt ser tilbudene, men ingen skjema.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { belopTekst, faktaTekst, tilOre, visTilbud } from "../static/js/flater/tilbud.js";

settI18nForTest(NB, "nb");
const HER = dirname(fileURLToPath(import.meta.url));
const T1 = "11111111-1111-1111-1111-111111111111";
const T2 = "22222222-2222-2222-2222-222222222222";
const ADRESSE = "styret@nordvik.example";

const LISTE = {
  sammendrag: { vist: 2, utkast: 1, godkjente: 1, sum_godkjent_ore: 2850000 },
  tilbud: [
    { tilbud_id: T1, kunde_navn: "Tromsø Borettslag", kunde_ref: null,
      kunde_maske: "s****@nordvik.example", tilbudsdato: "2026-09-10",
      gyldig_til: "2026-12-31", valuta: "NOK", sum_ore: 2850000,
      status: "godkjent", antall_linjer: 2, priser_fra_boka: true,
      klausuler_uendret: true, avgjort_ts: "2026-09-10T10:00:00+00:00",
      avgjort_av: "bruker:a", opprettet: "2026-09-10T09:00:00+00:00",
      opprettet_av: "bruker:a" },
    { tilbud_id: T2, kunde_navn: "Fjord Support AS", kunde_ref: "K-9",
      kunde_maske: "p****@fjord.example", tilbudsdato: "2026-09-09",
      gyldig_til: "2026-10-01", valuta: "NOK", sum_ore: 12500,
      status: "utkast", antall_linjer: 1, priser_fra_boka: false,
      klausuler_uendret: false, avgjort_ts: null, avgjort_av: null,
      opprettet: "2026-09-09T09:00:00+00:00", opprettet_av: "bruker:b" },
  ],
  request_id: "r-a",
};
const DETALJ = { ...LISTE.tilbud[0], innledning: "Takk for befaringen.",
  linjer: [
    { linje_nr: 1, produkt_id: "p1", produktkode: "KAB", produktnavn: "Kabel",
      enhet: "m", antall: 40, prisversjon: 1, listepris_ore: 1250,
      enhetspris_ore: 1250, linjesum_ore: 50000 },
    { linje_nr: 2, produkt_id: "p2", produktkode: "VP", produktnavn: "Varmepumpe",
      enhet: "stk", antall: 1, prisversjon: 2, listepris_ore: 2890000,
      enhetspris_ore: 2800000, linjesum_ore: 2800000 },
  ],
  klausuler: [{ kode: "BET-14", versjon: 1, tekst_hash: "a".repeat(64),
                tittel: "Betaling", tekst: "14 dager" }],
  request_id: "r-b" };
const PRISBOK = { produkter: [
  { produkt_id: "p1", kode: "KAB", navn: "Kabel", enhet: "m", aktiv: true },
  { produkt_id: "p3", kode: "GML", navn: "Utgått", enhet: "stk", aktiv: false }],
  request_id: "r-c" };

let SVAR; let SISTE; let KALL;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  KALL.push({ sti, metode: (opts && opts.method) || "GET" });
  if (opts && opts.method === "POST") {
    SISTE = { sti, kropp: JSON.parse(opts.body), headers: opts.headers };
    return { ok: true, status: 200, json: async () => ({ ok: true, tilbud_id: T1, ny: true }) };
  }
  const oppf = SVAR[sti];
  if (!oppf) return { ok: false, status: 404, json: async () => ({ feil: "ikke_funnet" }) };
  return { ok: true, status: 200, json: async () => oppf };
};
function ctx(scopes = ["okonomi:read", "bestilling:opprett"]) {
  return { sprak: "nb", scopes, tenant: "acme", paaUautorisert: () => {} };
}
async function vent(pred, n = 80) {
  for (let i = 0; i < n; i++) { if (pred()) return true; await new Promise((r) => setTimeout(r, 0)); }
  return pred();
}
function nyHoved() {
  const brett = nyttBrett();
  const m = document.createElement("main"); m.id = "hovedinnhold"; m.tabIndex = -1;
  brett.append(m); KALL = []; return m;
}
function fullSvar() {
  return { "/v1/tilbud": LISTE, [`/v1/tilbud/${T1}`]: DETALJ, "/v1/prisbok": PRISBOK };
}

test("Tilbud: beløp i heltall, fakta som tekst", () => {
  assert.equal(belopTekst(2850000), "28500,00");
  assert.equal(belopTekst(5), "0,05");
  assert.equal(belopTekst(12.5), "—");
  assert.equal(tilOre("28500"), 2850000);
  assert.equal(tilOre("12,50"), 1250);
  assert.equal(tilOre("abc"), null);
  assert.ok(faktaTekst(LISTE.tilbud[0]).includes(t("ui.tilbud.fakta.priser_ok")));
  assert.ok(faktaTekst(LISTE.tilbud[1]).includes(t("ui.tilbud.fakta.klausuler_endret")));
});

test("Tilbud: lista og detaljen viser masken, aldri adressen — og ingen send-knapp", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visTilbud(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 1);
  assert.ok(!h.textContent.includes(ADRESSE));
  assert.ok(h.textContent.includes("s****@nordvik.example"));
  assert.ok(h.textContent.includes(t("ui.tilbud.status.godkjent")));
  const rader = [...h.querySelectorAll("table")[0].querySelectorAll("tbody tr")];
  rader[0].querySelector("button").click();
  await vent(() => h.querySelectorAll("table").length >= 2);
  assert.ok(h.textContent.includes("Takk for befaringen."));
  assert.ok(h.textContent.includes("28000,00"));      // enhetspris under boka
  assert.ok(h.textContent.includes("14 dager"));
  for (const b of h.querySelectorAll("button")) {
    assert.ok(!/send/i.test(b.textContent), b.textContent);
  }
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Tilbud: skjemaet sender produkt, antall og valgfri enhetspris — ikke priser fra boka", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visTilbud(h, ctx());
  await vent(() => h.querySelector("#tb-navn"));
  const sel = h.querySelector("#tb-l1-produkt");
  assert.equal(sel.options.length, 1);                 // bare aktive produkter
  h.querySelector("#tb-navn").value = "Tromsø Borettslag";
  h.querySelector("#tb-epost").value = ADRESSE;
  h.querySelector("#tb-gyldig").value = "2026-12-31";
  h.querySelector("#tb-l1-antall").value = "40";
  h.querySelector("#tb-l1-pris").value = "12,00";
  h.querySelector("#tb-navn").closest("form").requestSubmit();
  await vent(() => SISTE && SISTE.sti === "/v1/tilbud");
  assert.deepEqual(SISTE.kropp.linjer, [{ produkt_id: "p1", antall: 40, enhetspris_ore: 1200 }]);
  assert.equal(SISTE.kropp.kunde_epost, ADRESSE);
  assert.ok(!("sum_ore" in SISTE.kropp));
  assert.ok(!JSON.stringify(SISTE.kropp).includes("listepris_ore"));
});

test("Tilbud: en lesende økt ser tilbudene, men ingen skjema", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visTilbud(h, ctx(["okonomi:read"]));
  await vent(() => h.querySelectorAll("table").length >= 1);
  assert.equal(h.querySelector("#tb-navn"), null);
  assert.ok(!KALL.some((k) => k.sti === "/v1/prisbok"));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Tilbud: flaten har ingen sending og ingen prisregning", () => {
  const kilde = readFileSync(join(HER, "..", "static", "js", "flater", "tilbud.js"), "utf8")
    .replace(/^\s*\/\/.*$/gm, "");
  for (const ord of ["smtp", "mailto:", "sendTilbud", "/send", "listepris_ore *", "* antall"]) {
    assert.ok(!kilde.includes(ord), `flaten bærer «${ord}»`);
  }
});
