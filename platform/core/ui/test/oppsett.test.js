// «Kom i gang» på dashbordet (/v1/oppsett).
import { test } from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visOversikt } from "../static/js/flater/oversikt.js";

settI18nForTest(NB, "nb");

const OVERSIKT = { vindu_start: "2026-11-17T10:00:00+00:00",
  vindu_slutt: "2026-11-18T10:00:00+00:00", tidssone: "UTC",
  tillatt: 0, stoppet: 0, unntak: 0, totalt: 0 };

let SVAR;
const POSTER = [];
let POSTSVAR = 200;
globalThis.fetch = async (url, opts = {}) => {
  if (opts.method === "POST") {
    POSTER.push({ url, kropp: JSON.parse(opts.body),
      idem: opts.headers["Idempotency-Key"] });
    if (POSTSVAR !== 200) {
      return { ok: false, status: POSTSVAR, json: async () => ({ feil: "x" }) };
    }
    SVAR["/v1/oppsett"] = { moduler: [
      { modul: 27, tilgjengelig: true, klar: true, apne_funn: 0 }] };
    return { ok: true, status: 200, json: async () => ({ versjon: 1 }) };
  }
  const oppf = SVAR[url.split("?")[0]];
  if (!oppf) return { ok: false, status: 404, json: async () => ({ feil: "x" }) };
  if (typeof oppf === "number") {
    return { ok: false, status: oppf, json: async () => ({ feil: "x" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

const OKONOMI = ["decisions:read", "okonomi:read"];
const ctx = (scopes = OKONOMI) => ({ sprak: "nb", scopes, tenant: "acme",
  paaUautorisert: () => {} });

async function vent(pred, n = 80) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}

function nyHoved() {
  const m = document.createElement("main");
  m.id = "hovedinnhold"; m.tabIndex = -1;
  nyttBrett().append(m);
  return m;
}

test("Mangler krav: ett kort per modul, knappen går rett til modulen", async () => {
  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": { moduler: [
    { modul: 27, tilgjengelig: true, klar: false, apne_funn: 1 },
    { modul: 14, tilgjengelig: true, klar: true, apne_funn: 3 },
    { modul: 13, tilgjengelig: true, klar: null, apne_funn: 0 },
    { modul: 41, tilgjengelig: false, klar: null, apne_funn: null }] } };
  const h = nyHoved();
  visOversikt(h, ctx());
  const blokk = h.querySelector(".dash-oppsett");
  assert.ok(await vent(() => !blokk.hidden), "blokken ble aldri vist");

  assert.equal(blokk.querySelector("h2").textContent, t("ui.oppsett.tittel"));
  assert.ok(blokk.textContent.includes("3 av 4 moduler er satt opp"));
  const prog = blokk.querySelector("progress");
  assert.equal(prog.getAttribute("value"), "3");
  assert.equal(prog.getAttribute("max"), "4");

  const kort = [...blokk.querySelectorAll(".oppsett-kort")];
  assert.equal(kort.length, 1);
  assert.equal(kort[0].querySelector("h3").textContent, t("ui.nav.lager"));
  assert.equal(kort[0].querySelector("a.knapp.primar").getAttribute("href"),
    "#/lager");

  const brikker = [...blokk.querySelectorAll(".oppsett-brikker a")];
  assert.deepEqual(brikker.map((a) => a.getAttribute("href")),
    ["#/faktura", "#/avstemming", "#/betaling"],
    "flest funn først, utilgjengelige sist");
  assert.ok(brikker[0].textContent.includes("3 åpne funn"));
  assert.ok(brikker[1].textContent.includes(t("ui.oppsett.ingen_funn")));
  assert.ok(brikker[2].textContent.includes(t("ui.oppsett.utilgjengelig")));

  // Blokken står FØR tellekortene.
  const kpi = h.querySelector(".dash-kpi");
  assert.ok(blokk.compareDocumentPosition(kpi)
    & Node.DOCUMENT_POSITION_FOLLOWING);
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

test("Tomt register: kortet ber om det første, ikke om grenser", async () => {
  // Registermodulene (lisenser, tilganger, kontroller …) har ingen grenser
  // å sette. «Mangler grensene sine» ville sendt kunden på leting etter et
  // skjema som ikke finnes.
  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": { moduler: [
    { modul: 22, tilgjengelig: true, klar: false, apne_funn: null,
      mangler: "register" },
    { modul: 27, tilgjengelig: true, klar: false, apne_funn: 0 }] } };
  const h = nyHoved();
  visOversikt(h, ctx());
  const blokk = h.querySelector(".dash-oppsett");
  assert.ok(await vent(() => !blokk.hidden), "blokken ble aldri vist");
  const kort = [...blokk.querySelectorAll(".oppsett-kort")];
  assert.equal(kort.length, 2);
  const lisens = kort.find((k) =>
    k.querySelector("h3").textContent === t("ui.nav.lisens"));
  assert.ok(lisens, "lisenskortet mangler");
  assert.ok(lisens.textContent.includes(t("ui.oppsett.mangler_register")));
  assert.ok(!lisens.textContent.includes(t("ui.oppsett.mangler_krav")));
  const lenke = lisens.querySelector("a.knapp.primar");
  assert.equal(lenke.textContent, t("ui.oppsett.registrer"));
  assert.equal(lenke.getAttribute("href"), "#/lisens");
  const lager = kort.find((k) => k !== lisens);
  assert.ok(lager.textContent.includes(t("ui.oppsett.mangler_krav")));
});

test("Alt satt opp: ingen kort, egen tittel", async () => {
  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": { moduler: [
    { modul: 27, tilgjengelig: true, klar: true, apne_funn: 0 }] } };
  const h = nyHoved();
  visOversikt(h, ctx());
  const blokk = h.querySelector(".dash-oppsett");
  assert.ok(await vent(() => !blokk.hidden));
  assert.equal(blokk.querySelector("h2").textContent,
    t("ui.oppsett.tittel_klar"));
  assert.equal(blokk.querySelectorAll(".oppsett-kort").length, 0);
  assert.ok(blokk.textContent.includes(t("ui.oppsett.alt_klart")));
});

test("Modul uten rute for økten vises ikke; tomt svar skjuler blokken", async () => {
  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": { moduler: [
    { modul: 27, tilgjengelig: true, klar: false, apne_funn: 0 }] } };
  const h = nyHoved();
  // Uten okonomi:read har økten ingen lagerrute — da skal ingen knapp
  // love en flate serveren svarer 403 på.
  visOversikt(h, ctx(["decisions:read"]));
  assert.ok(await vent(() => h.querySelector(".cards")));
  for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 0));
  assert.equal(h.querySelector(".dash-oppsett").hidden, true);
});

test("Feil fra /v1/oppsett: blokken forblir skjult, dashbordet står", async () => {
  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": 500 };
  const h = nyHoved();
  visOversikt(h, ctx());
  assert.ok(await vent(() => h.querySelector(".cards")));
  for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 0));
  assert.equal(h.querySelector(".dash-oppsett").hidden, true);
  assert.ok(h.textContent.includes(t("ui.oversikt.telling_note")));
});

const LAGER_ANBEFALT = { sti: "/v1/lager/terskler", kropp: {
  stille_dogn: 180, uten_punkt_dogn: 30, telleintervall_dogn: 365 } };

test("Anbefalte grenser: vist i klartekst, ett klikk lagrer og kortet går bort", async () => {
  POSTER.length = 0; POSTSVAR = 200;
  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": { moduler: [
    { modul: 27, tilgjengelig: true, klar: false, apne_funn: 0,
      anbefalt: LAGER_ANBEFALT }] } };
  const h = nyHoved();
  visOversikt(h, ctx());
  const blokk = h.querySelector(".dash-oppsett");
  assert.ok(await vent(() => blokk.querySelector(".oppsett-anbefalt")));
  const tekst = blokk.querySelector(".oppsett-anbefalt").textContent;
  assert.ok(tekst.includes(t("ui.lager.terskel.stille")));
  assert.ok(tekst.includes("180 døgn"));
  assert.ok(tekst.includes("365 døgn"));
  assert.equal(blokk.querySelector("a.oppsett-velg").getAttribute("href"),
    "#/lager");
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);

  blokk.querySelector("button.knapp.primar").click();
  assert.ok(await vent(() => blokk.querySelector(".oppsett-melding")
    .textContent.length > 0), "ingen lagret-melding");
  assert.deepEqual(POSTER.map((p) => [p.url, p.kropp]),
    [["/v1/lager/terskler", LAGER_ANBEFALT.kropp]]);
  assert.equal(blokk.querySelectorAll(".oppsett-kort").length, 0);
  assert.ok(blokk.querySelector(".oppsett-melding").textContent
    .includes(t("ui.nav.lager")));
  assert.equal(blokk.querySelector(".oppsett-melding").getAttribute("role"),
    "status");
});

test("Anbefalte grenser: feil gir melding, nytt klikk bruker SAMME nøkkel", async () => {
  POSTER.length = 0; POSTSVAR = 500;
  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": { moduler: [
    { modul: 27, tilgjengelig: true, klar: false, apne_funn: 0,
      anbefalt: LAGER_ANBEFALT }] } };
  const h = nyHoved();
  visOversikt(h, ctx());
  const blokk = h.querySelector(".dash-oppsett");
  assert.ok(await vent(() => blokk.querySelector("button.knapp.primar")));
  const knapp = blokk.querySelector("button.knapp.primar");
  knapp.click();
  assert.ok(await vent(() => blokk.querySelector(".oppsett-feil")
    .textContent.length > 0));
  assert.equal(knapp.disabled, false);
  POSTSVAR = 200;
  knapp.click();
  assert.ok(await vent(() => POSTER.length === 2));
  assert.equal(POSTER[0].idem, POSTER[1].idem);
});

test("Anbefalingssti utenfor formen avvises i klienten", async () => {
  const { brukAnbefalteGrenser } = await import("../static/js/api.js");
  POSTER.length = 0;
  await assert.rejects(brukAnbefalteGrenser(
    { sti: "/v1/firma/slett", kropp: {} }));
  assert.equal(POSTER.length, 0);
});

const KLAR = [{ modul: 23, tilgjengelig: true, klar: true, apne_funn: 0 }];

test("Går av seg selv: på og av, lenke til policyen bare for forvalter", async () => {
  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": { moduler: KLAR,
    automatikk: [
      { handling: "purring.send", modul: 23, paa: true },
      { handling: "kampanje.send", modul: 44, paa: false }] } };
  const h = nyHoved();
  visOversikt(h, ctx([...OKONOMI, "policy:read", "policy:write"]));
  const blokk = h.querySelector(".dash-oppsett");
  assert.ok(await vent(() => blokk.querySelector(".oppsett-auto")));
  const brikker = [...blokk.querySelectorAll(".oppsett-auto .brikke")];
  assert.equal(brikker.length, 2);
  assert.ok(brikker[0].classList.contains("paa"));
  assert.ok(brikker[0].textContent.includes(t("ui.oppsett.auto.purring_send")));
  assert.ok(brikker[1].textContent.includes(t("ui.oppsett.auto.av")));
  assert.equal(blokk.querySelector('a[href="#/policyadmin"]').textContent,
    t("ui.oppsett.auto.endre"));
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);

  const h2 = nyHoved();
  visOversikt(h2, ctx([...OKONOMI, "policy:read"]));
  const b2 = h2.querySelector(".dash-oppsett");
  assert.ok(await vent(() => b2.querySelector(".oppsett-auto")));
  assert.equal(b2.querySelector('a[href="#/policyadmin"]'), null);
});

test("Går av seg selv: uten aktiv policy sies det rett ut; uten felt ingen seksjon", async () => {
  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": { moduler: KLAR,
    automatikk: null } };
  const h = nyHoved();
  visOversikt(h, ctx([...OKONOMI, "policy:read"]));
  const blokk = h.querySelector(".dash-oppsett");
  assert.ok(await vent(() => !blokk.hidden));
  assert.ok(blokk.textContent.includes(t("ui.oppsett.auto.ingen_policy")));

  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": { moduler: KLAR } };
  const h2 = nyHoved();
  visOversikt(h2, ctx());
  const b2 = h2.querySelector(".dash-oppsett");
  assert.ok(await vent(() => !b2.hidden));
  assert.equal(b2.textContent.includes(t("ui.oppsett.auto.tittel")), false);
});

// ---------------------------------------------------------------------
// Fire øyne: godkjenneren legges til DER kunden står
// ---------------------------------------------------------------------
const ADMIN = [...OKONOMI, "policy:read", "policy:write", "policy:activate",
  "firma:inviter"];

test("Én som kan godkjenne: kortet sier hvorfor, og invitasjonen lages på stedet", async () => {
  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": { moduler: KLAR,
    automatikk: [], godkjenning: { kan_godkjenne: 1, apen_runde: true,
      invitasjon_ute: false } } };
  const forrige = globalThis.fetch;
  const kall = [];
  globalThis.fetch = async (url, opts = {}) => {
    if (opts.method === "POST" && url.startsWith("/v1/invitasjoner")) {
      kall.push(JSON.parse(opts.body));
      return { ok: true, status: 200, json: async () => ({
        tenant: "acme", token: "hemmelig+token",
        utloper: "2026-11-27T10:00:00+00:00" }) };
    }
    return forrige(url, opts);
  };
  try {
    const h = nyHoved();
    visOversikt(h, ctx(ADMIN));
    const blokk = h.querySelector(".dash-oppsett");
    assert.ok(await vent(() => blokk.querySelector(".oppsett-godkjenning")));
    // Med noe å gjøre heter blokken «Kom i gang», selv om modulene er klare.
    assert.equal(blokk.querySelector("h2").textContent, t("ui.oppsett.tittel"));
    const kort = blokk.querySelector(".oppsett-godkjenning");
    assert.ok(kort.textContent.includes(
      t("ui.oppsett.godkjenner.mangler_tekst")));
    // Runden som venter står i SAMME kort, med veien til den.
    assert.ok(kort.textContent.includes(
      t("ui.oppsett.godkjenner.venter_tittel")));
    assert.ok(kort.querySelector('a[href="#/policyadmin"]'));

    const knapp = [...kort.querySelectorAll("button")].find(
      (b) => b.textContent === t("ui.oppsett.godkjenner.inviter"));
    knapp.click();
    assert.ok(await vent(() => kort.querySelector("#godkjenner-lenke")));
    // Rollen er den som KAN attestere — ikke «godkjenner», som bare
    // behandler unntak.
    assert.deepEqual(kall, [{ roller: ["policyforvalter"] }]);
    const lenke = kort.querySelector("#godkjenner-lenke").value;
    assert.ok(lenke.includes("visning=blimed"));
    assert.ok(lenke.includes("t=acme"));
    assert.ok(lenke.includes("k=hemmelig%2Btoken"), "tokenet er ikke URL-kodet");
    assert.ok(kort.querySelector('label[for="godkjenner-lenke"]'));
    assert.ok(kort.textContent.includes("2026-11-27"));
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
  } finally { globalThis.fetch = forrige; }
});

test("Uten invitasjonsrett: kortet sier hvem som kan, og har ingen knapp som feiler", async () => {
  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": { moduler: KLAR,
    automatikk: [], godkjenning: { kan_godkjenne: 1, apen_runde: false,
      invitasjon_ute: true } } };
  const h = nyHoved();
  visOversikt(h, ctx([...OKONOMI, "policy:read"]));
  const blokk = h.querySelector(".dash-oppsett");
  assert.ok(await vent(() => blokk.querySelector(".oppsett-godkjenning")));
  const kort = blokk.querySelector(".oppsett-godkjenning");
  assert.equal(kort.querySelectorAll("button").length, 0);
  assert.ok(kort.textContent.includes(t("ui.oppsett.godkjenner.be_admin")));
  assert.ok(kort.textContent.includes(
    t("ui.oppsett.godkjenner.invitasjon_ute")));
});

test("To som kan godkjenne og ingen runde: ingen godkjenningsdel", async () => {
  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": { moduler: KLAR,
    automatikk: [], godkjenning: { kan_godkjenne: 2, apen_runde: false,
      invitasjon_ute: false } } };
  const h = nyHoved();
  visOversikt(h, ctx(ADMIN));
  const blokk = h.querySelector(".dash-oppsett");
  assert.ok(await vent(() => !blokk.hidden));
  assert.equal(blokk.querySelector(".oppsett-godkjenning"), null);
  assert.equal(blokk.querySelector("h2").textContent,
    t("ui.oppsett.tittel_klar"));

  // …men en runde som venter vises, med knappen som hovedhandling.
  SVAR["/v1/oppsett"].godkjenning.apen_runde = true;
  const h2 = nyHoved();
  visOversikt(h2, ctx(ADMIN));
  const b2 = h2.querySelector(".dash-oppsett");
  assert.ok(await vent(() => b2.querySelector(".oppsett-godkjenning")));
  const a = b2.querySelector('.oppsett-godkjenning a[href="#/policyadmin"]');
  assert.ok(a.classList.contains("primar"));
});

test("Ingen moduler, men en godkjenner mangler: blokken vises likevel", async () => {
  SVAR = { "/v1/oversikt": OVERSIKT, "/v1/oppsett": { moduler: [],
    godkjenning: { kan_godkjenne: 1, apen_runde: false,
      invitasjon_ute: false } } };
  const h = nyHoved();
  visOversikt(h, ctx(ADMIN));
  const blokk = h.querySelector(".dash-oppsett");
  assert.ok(await vent(() => !blokk.hidden));
  assert.ok(blokk.querySelector(".oppsett-godkjenning"));
});
