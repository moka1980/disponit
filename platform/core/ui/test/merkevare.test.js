// M-55 merkevareflaten (120) — flateporten (jsdom + axe).
//
// Portene her måler det modulen står og faller på:
//
//   * `modulen_sendte_krav`: ingen «send krav»-knapp, ingen «send
//     klage», ingen mottaker — og ingen LENKE til den påståtte
//     krenkerens side. En klikkbar lenke ville vært en utgående
//     forespørsel flaten inviterer til.
//   * `funn_uten_bevaringskopi`: funnskjemaet krever en kopi fra en
//     liste, og hver funnrad bærer URL, tidspunkt og innholdssum.
//   * `forvekslingsvurdering_uten_grunnlag`: likheten står ALDRI
//     alene — terskelen og grunnlaget står ved siden av, og
//     vurderingsrekken bærer de to tekstene som ble sammenlignet.
//   * `forvekslingsterskel_hardkodet`: ingen tallkonstant for
//     terskelen finnes i flaten; mangler den, sier flaten det med
//     `role="alert"` og tilbyr ikke å vurdere.
//
// Ingen delt fixture (m16-formen): hver test bygger sin egen skjerm.
import { readFileSync } from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import {
  ARTER, BRUKSFORMER, bytesTekst, funntabell, grunnlagTekst,
  kopitabell, likhetTekst, merketabell, summenTekst, tilstandTekst,
  visMerkevare, vurderingstabell,
} from "../static/js/flater/merkevare.js";

settI18nForTest(NB, "nb");

const M1 = "11111111-1111-1111-1111-111111111111";
const M2 = "22222222-2222-2222-2222-222222222222";
const F1 = "aaaaaaaa-1111-1111-1111-111111111111";
const F2 = "aaaaaaaa-2222-2222-2222-222222222222";
const K1 = "bbbbbbbb-1111-1111-1111-111111111111";
const W1 = "cccccccc-1111-1111-1111-111111111111";
const W2 = "cccccccc-2222-2222-2222-222222222222";
const SHA = "a".repeat(64);

const BILDE = {
  sammendrag: {
    merker: 2, aktive: 2, funn: 2, apne_funn: 2, uvurderte: 1,
    over_terskel: 1, uhenviste: 1, henviste: 0, bevaringskopier: 1,
    ubrukte_kopier: 0, apne_varsler: 2, har_krav: true, terskel: 80,
    kravversjon: 1, vist: 2,
  },
  merker: [
    { merkevare_id: M1, navn: "Disponit", art: "varemerke",
      registernummer: "301234", registerfoerer: "Patentstyret",
      vareklasser: ["9", "42"], gjelder_fra: "2024-01-01",
      aktiv: true, registrert: "2026-01-02T09:00:00+00:00",
      antall_funn: 2, apne_funn: 2, uvurderte: 1, over_terskel: 1,
      uhenviste: 1, hoyeste_likhet: 87, apne_varsler: 2 },
    { merkevare_id: M2, navn: "Kaffekopp", art: "produktnavn",
      registernummer: null, registerfoerer: null, vareklasser: [],
      gjelder_fra: "2025-01-01", aktiv: true,
      registrert: "2026-01-02T09:00:00+00:00", antall_funn: 0,
      apne_funn: 0, uvurderte: 0, over_terskel: 0, uhenviste: 0,
      hoyeste_likhet: null, apne_varsler: 1 },
  ],
  bevaringskopier: [
    { kopi_id: K1, kilde_url: "https://eksempel.no/annonse",
      hentet_ts: "2026-09-01T08:00:00+00:00", innhold_sha256: SHA,
      innhold_bytes: 40960, medietype: "text/html",
      lagringsnokkel: "artefakt/2026/kopi-1",
      registrert: "2026-09-01T09:00:00+00:00",
      registrert_av: "kari", brukt_i_funn: 2 },
  ],
  krav: { forvekslingsterskel: 80, funnfrist_dogn: 14,
          henvisningsfrist_dogn: 3, versjon: 1,
          oppdatert: "2026-08-01T09:00:00+00:00",
          oppdatert_av: "kari" },
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    merker: 0, aktive: 0, funn: 0, apne_funn: 0, uvurderte: 0,
    over_terskel: 0, uhenviste: 0, henviste: 0, bevaringskopier: 0,
    ubrukte_kopier: 0, apne_varsler: 0, har_krav: false,
    terskel: null, kravversjon: null, vist: 0,
  },
  merker: [], bevaringskopier: [], krav: null, request_id: "r-b",
};

function nyttFunn(over) {
  return {
    funn_id: over ? F1 : F2, merkevare_id: M1, merkenavn: "Disponit",
    observert_navn: over ? "Dispunit" : "Kaffe og krus",
    bruksform: "annonsetekst",
    kontekst: "Google-annonse pa sok etter merket",
    motpart: over ? "Ukjent AS" : null,
    registrert: "2026-09-02T09:00:00+00:00", registrert_av: "kari",
    kopi_id: K1, kilde_url: "https://eksempel.no/annonse",
    hentet_ts: "2026-09-01T08:00:00+00:00", innhold_sha256: SHA,
    innhold_bytes: 40960, medietype: "text/html",
    likhet: over ? 87 : null, terskel_brukt: over ? 80 : null,
    over_terskel: over ? true : null,
    grunnlag: over ? ["redigeringsavstand"] : null,
    algoritmeversjon: over ? "lev-1" : null,
    kravversjon: over ? 1 : null,
    vurdert: over ? "2026-09-02T10:00:00+00:00" : null,
    antall_vurderinger: over ? 1 : 0,
    henvist_unntak_id: null, henvist_ts: null, henvist_av: null,
    lukket_ts: null, lukket_av: null, lukkebegrunnelse: null,
  };
}

const FUNN = { merkevare_id: M1, request_id: "r-c",
               funn: [nyttFunn(true), nyttFunn(false)] };

const VURDERINGER = {
  funn_id: F1, request_id: "r-d",
  vurderinger: [
    { vurdering_id: "dddddddd-1111-1111-1111-111111111111",
      likhet: 87, terskel_brukt: 80, over_terskel: true,
      grunnlag: ["redigeringsavstand"], algoritmeversjon: "lev-1",
      kravversjon: 1, merkenavn_ved_vurdering: "Disponit",
      observert_ved_vurdering: "Dispunit",
      vurdert: "2026-09-02T10:00:00+00:00", vurdert_av: "kari" },
  ],
};

const VARSLER = {
  request_id: "r-e",
  varsler: [
    { varsel_id: W1, merkevare_id: M1, merkenavn: "Disponit",
      funn_id: F1, observert_navn: "Dispunit",
      varseltype: "forveksling_ikke_henvist", over_grense: 2,
      detalj: null, likhet: 87, terskel_brukt: 80, kravversjon: 1,
      forst_sett: "2026-09-03T09:00:00+00:00",
      sist_sett_sveip: "2026-09-04T09:00:00+00:00", apen: true,
      lukket_ts: null, lukket_av: null, lukkenotat: null },
    { varsel_id: W2, merkevare_id: M2, merkenavn: "Kaffekopp",
      funn_id: null, observert_navn: null,
      varseltype: "merkevare_uten_funn", over_grense: null,
      detalj: null, likhet: null, terskel_brukt: 80, kravversjon: 1,
      forst_sett: "2026-09-03T09:00:00+00:00",
      sist_sett_sveip: "2026-09-04T09:00:00+00:00", apen: true,
      lukket_ts: null, lukket_av: null, lukkenotat: null },
  ],
};

let SVAR;
let SISTE;
let KALL;
let SVARSTATUS;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  KALL.push({ sti, metode: (opts && opts.method) || "GET" });
  if (opts && opts.method === "POST") {
    SISTE = { sti, kropp: JSON.parse(opts.body), headers: opts.headers };
    if (SVARSTATUS && SVARSTATUS !== 200) {
      return { ok: false, status: SVARSTATUS,
        json: async () => ({ feil: "merkevare_ulovlig_tilstand" }) };
    }
    return { ok: true, status: 200,
             json: async () => ({ ok: true, likhet: 87,
                                  terskel_brukt: 80,
                                  over_terskel: true,
                                  grunnlag: ["redigeringsavstand"],
                                  algoritmeversjon: "lev-1" }) };
  }
  const oppf = SVAR[sti];
  if (!oppf) {
    return { ok: false, status: 404,
      json: async () => ({ feil: "ikke_funnet" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx(scopes = ["okonomi:read", "bestilling:opprett"]) {
  return { sprak: "nb", scopes, tenant: "acme", paaUautorisert: () => {} };
}

async function vent(pred, n = 120) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}

function nyHoved() {
  const brett = nyttBrett();
  const m = document.createElement("main");
  m.id = "hovedinnhold"; m.tabIndex = -1;
  brett.append(m);
  KALL = []; SISTE = undefined; SVARSTATUS = 200;
  return m;
}

function fullSvar() {
  return {
    "/v1/merkevare": BILDE,
    "/v1/merkevare/varsler": VARSLER,
    "/v1/merkevare/bevaringskopier": {
      request_id: "r-f", bevaringskopier: BILDE.bevaringskopier },
    [`/v1/merkevare/${M1}/funn`]: FUNN,
    [`/v1/merkevare/${M2}/funn`]: { merkevare_id: M2, funn: [],
                                    request_id: "r-g" },
    [`/v1/merkevare/funn/${F1}/vurderinger`]: VURDERINGER,
  };
}

function tabeller(h) {
  return [...h.querySelectorAll("table")];
}

async function apneForste(h) {
  await vent(() => tabeller(h).length >= 2);
  tabeller(h)[0].querySelectorAll("tbody tr")[0]
    .querySelector("button").click();
  // VENT PÅ NOE SOM BARE FINNES I PANELET (118s lærdom).
  await vent(() => [...h.querySelectorAll("caption")].some(
    (c) => c.textContent === t("ui.merkevare.vurderinger.tittel")));
}

// ---------------------------------------------------------------------
// modulen_sendte_krav — FLATEN HAR INGEN UTGÅENDE VEI
// ---------------------------------------------------------------------

test("Merkevare: ingen kontroll sender et krav noe sted", async () => {
  // M-55 DOKUMENTERER OG RAPPORTERER. Det finnes ingen knapp som
  // sender noe ut av huset; modulens eneste utgang er henvisning til
  // unntakskøen, og der beslutter et menneske.
  SVAR = fullSvar();
  const h = nyHoved();
  visMerkevare(h, ctx());
  await apneForste(h);
  const kontroller = [...h.querySelectorAll("button, a[href]")]
    .map((n) => n.textContent.toLowerCase());
  for (const k of kontroller) {
    assert.ok(!/send|klage|krav mot|brev|varsle|submit|notify/.test(k),
      `utgående kontroll: «${k}»`);
  }
  // …og alle kall gikk til vår egen /v1/merkevare.
  for (const kall of KALL) {
    assert.ok(kall.sti.startsWith("/v1/merkevare"), kall.sti);
  }
  assert.ok(h.textContent.includes(t("ui.merkevare.oversikt.hvorfor")));
});

test("Merkevare: den påståtte krenkerens side er ikke en lenke",
  async () => {
    // En klikkbar lenke ville vært en utgående forespørsel flaten
    // inviterer til — og et klikk fra vår side inn på en annens
    // annonse er ikke en nøytral handling.
    SVAR = fullSvar();
    const h = nyHoved();
    visMerkevare(h, ctx());
    await apneForste(h);
    assert.equal(h.querySelectorAll("a").length, 0,
      "flaten laget en lenke");
    // …men URL-en STÅR der, som tekst. Beviset skal være synlig.
    assert.ok(h.textContent.includes("https://eksempel.no/annonse"));
  });

// ---------------------------------------------------------------------
// funn_uten_bevaringskopi
// ---------------------------------------------------------------------

test("Merkevare: funnskjemaet krever kopi, navn, bruksform og kontekst",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visMerkevare(h, ctx());
    await apneForste(h);
    await vent(() => h.querySelector("#mv-f-kopi") !== null);
    const kopi = h.querySelector("#mv-f-kopi");
    const observert = h.querySelector("#mv-f-observert");
    const bruksform = h.querySelector("#mv-f-bruksform");
    const kontekst = h.querySelector("#mv-f-kontekst");
    const lagre = [...h.querySelectorAll("button")].find(
      (b) => b.textContent === t("ui.merkevare.knapp.lagre_funn"));
    assert.equal(lagre.disabled, true, "knappen var levende fra start");

    kopi.value = K1;
    kopi.dispatchEvent(new window.Event("change"));
    observert.value = "Dispunit";
    observert.dispatchEvent(new window.Event("input"));
    assert.equal(lagre.disabled, true, "kopi+navn alene åpnet knappen");

    bruksform.value = "annonsetekst";
    bruksform.dispatchEvent(new window.Event("change"));
    assert.equal(lagre.disabled, true, "uten kontekst holdt");

    kontekst.value = "Annonse på søk etter merket";
    kontekst.dispatchEvent(new window.Event("input"));
    assert.equal(lagre.disabled, false, "alt fylt ut, død knapp");
  });

test("Merkevare: funnet sendes MED kopi-id", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visMerkevare(h, ctx());
  await apneForste(h);
  await vent(() => h.querySelector("#mv-f-kopi") !== null);
  const kopi = h.querySelector("#mv-f-kopi");
  kopi.value = K1; kopi.dispatchEvent(new window.Event("change"));
  const observert = h.querySelector("#mv-f-observert");
  observert.value = "Dispunit";
  observert.dispatchEvent(new window.Event("input"));
  const bruksform = h.querySelector("#mv-f-bruksform");
  bruksform.value = "annonsetekst";
  bruksform.dispatchEvent(new window.Event("change"));
  const kontekst = h.querySelector("#mv-f-kontekst");
  kontekst.value = "Annonse på søk";
  kontekst.dispatchEvent(new window.Event("input"));
  kopi.form.dispatchEvent(new window.Event("submit",
    { cancelable: true }));
  await vent(() => SISTE !== undefined);
  assert.equal(SISTE.sti, "/v1/merkevare/funn");
  assert.equal(SISTE.kropp.kopi_id, K1);
  assert.equal(SISTE.kropp.merkevare_id, M1);
  // MOTPART ER VALGFRI, og tomt sendes som null — ikke som "".
  assert.equal(SISTE.kropp.motpart, null);
  assert.ok(SISTE.headers["Idempotency-Key"], SISTE.headers);
});

test("Merkevare: uten en eneste kopi finnes ingen funnknapp",
  async () => {
    // En knapp som alltid feiler lærer brukeren at systemet er
    // upålitelig — når det egentlig gjorde nøyaktig det det skal.
    SVAR = { ...fullSvar(),
      "/v1/merkevare/bevaringskopier": {
        request_id: "r-h", bevaringskopier: [] } };
    const h = nyHoved();
    visMerkevare(h, ctx());
    await apneForste(h);
    await vent(() => h.textContent.includes(
      t("ui.merkevare.funn.ingen_kopier")));
    assert.equal(h.querySelector("#mv-f-kopi"), null);
    const lagre = [...h.querySelectorAll("button")].find(
      (b) => b.textContent === t("ui.merkevare.knapp.lagre_funn"));
    assert.equal(lagre, undefined, "funnknappen sto der uten kopi");
    const varsel = [...h.querySelectorAll('[role="alert"]')]
      .map((n) => n.textContent);
    assert.ok(varsel.includes(t("ui.merkevare.funn.ingen_kopier")));
  });

test("Merkevare: hver funnrad bærer beviset sitt", () => {
  const node = funntabell([nyttFunn(true)]);
  const rad = node.querySelector("tbody tr").textContent;
  assert.ok(rad.includes("https://eksempel.no/annonse"), rad);
  assert.ok(rad.includes("2026-09-01"), rad);
  assert.ok(rad.includes(SHA.slice(0, 12)), rad);
  assert.ok(rad.includes("40 kB"), rad);
  // HELE SUMMEN STÅR I `title` — det er den som binder raden til
  // bytene, og en forkortelse alene ville ikke gjort det.
  const celle = [...node.querySelectorAll("td")]
    .find((c) => c.getAttribute("title") === SHA);
  assert.ok(celle, "hele innholdssummen manglet");
});

test("Merkevare: kopiskjemaet krever sum, størrelse og lagringsnøkkel",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visMerkevare(h, ctx());
    await vent(() => h.querySelector("#mv-k-url") !== null);
    const lagre = [...h.querySelectorAll("button")].find(
      (b) => b.textContent === t("ui.merkevare.knapp.lagre_kopi"));
    assert.equal(lagre.disabled, true);
    const sett = (id, v, ev = "input") => {
      const n = h.querySelector(id);
      n.value = v;
      n.dispatchEvent(new window.Event(ev));
    };
    sett("#mv-k-url", "https://eksempel.no/side");
    sett("#mv-k-hentet", "2026-09-01T08:00");
    assert.equal(lagre.disabled, true, "uten sum åpnet knappen");
    sett("#mv-k-sum", SHA);
    sett("#mv-k-bytes", "4096");
    assert.equal(lagre.disabled, true, "uten medietype åpnet knappen");
    sett("#mv-k-medietype", "text/html");
    sett("#mv-k-lagring", "artefakt/2026/x");
    assert.equal(lagre.disabled, false);

    // …OG EN IKKE-WEB-URL ÅPNER DEN IKKE.
    sett("#mv-k-url", "file:///etc/passwd");
    assert.equal(lagre.disabled, true, "file:-URL ble godtatt");
  });

// ---------------------------------------------------------------------
// forvekslingsvurdering_uten_grunnlag OG
// forvekslingsterskel_hardkodet
// ---------------------------------------------------------------------

test("Merkevare: likheten står aldri uten terskelen", () => {
  // ET TALL ALENE ER EN MENING I TALLFORM. Terskelen er tenantens
  // egen, og uten den sier «87 %» ingenting om hva vi mener om det.
  //
  // MUTASJONEN SOM DREPER DENNE: la `likhetTekst` returnere bare
  // prosenten.
  const over = likhetTekst({ likhet: 87, terskel_brukt: 80,
                             over_terskel: true });
  assert.ok(over.includes("87") && over.includes("80"), over);
  const under = likhetTekst({ likhet: 40, terskel_brukt: 80,
                              over_terskel: false });
  assert.ok(under.includes("40") && under.includes("80"), under);
  assert.notEqual(over, under, "over og under leses likt");
  assert.equal(likhetTekst({ likhet: null }),
    t("ui.merkevare.uvurdert"));
  assert.equal(likhetTekst(null), t("ui.merkevare.uvurdert"));

  // UTEN `over_terskel` UTLEDES DOMMEN AV TALLENE (CodeRabbit).
  // `m55_varslene` sender ikke flagget, og `undefined` lest som usant
  // ville snudd beskjeden.
  assert.equal(likhetTekst({ likhet: 87, terskel_brukt: 80 }), over);
  assert.equal(likhetTekst({ likhet: 40, terskel_brukt: 80 }), under);
  // …og grensen er basens egen: `likhet >= terskel`.
  assert.equal(likhetTekst({ likhet: 80, terskel_brukt: 80 }),
    t("ui.merkevare.likhet_over").replace("{likhet}", "80")
      .replace("{terskel}", "80"));
});

test("Merkevare: grunnlaget står ved siden av tallet", () => {
  const tekst = grunnlagTekst(["redigeringsavstand",
                               "identisk_normalisert"]);
  assert.ok(tekst.includes(t("ui.merkevare.grunnlag_avstand")), tekst);
  assert.ok(tekst.includes(t("ui.merkevare.grunnlag_identisk")), tekst);
  assert.equal(grunnlagTekst([]), "–");
  assert.equal(grunnlagTekst(null), "–");
});

test("Merkevare: vurderingsrekken bærer de to sammenlignede tekstene",
  () => {
    // UTEN DEM KAN INGEN REGNE ETTER, og en vurdering ingen kan regne
    // etter er en mening — ikke et bevis.
    const node = vurderingstabell(VURDERINGER.vurderinger);
    const rad = node.querySelector("tbody tr").textContent;
    assert.ok(rad.includes("Disponit"), rad);
    assert.ok(rad.includes("Dispunit"), rad);
    assert.ok(rad.includes("lev-1"), rad);
    assert.ok(rad.includes("87") && rad.includes("80"), rad);
    const kol = [...node.querySelectorAll('th[scope="col"]')]
      .map((n) => n.textContent);
    for (const k of ["merkenavn_da", "observert_da", "algoritme",
                     "kravversjon"]) {
      assert.ok(kol.includes(t(`ui.merkevare.kol.${k}`)), k);
    }
  });

test("Merkevare: uten terskel finnes ingen vurderingsknapp", async () => {
  // Døra ville nektet. Og et forhåndsutfylt standardtall i skjemaet
  // ville vært nøyaktig den hardkodede terskelen invarianten forbyr.
  SVAR = { ...fullSvar(), "/v1/merkevare": TOMT };
  const h = nyHoved();
  visMerkevare(h, ctx());
  await vent(() => h.textContent.includes(
    t("ui.merkevare.ingen_terskel_varsel")));
  const vurder = [...h.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.merkevare.knapp.vurder"));
  assert.equal(vurder, undefined, "vurderingsknappen sto der");
  const varsel = [...h.querySelectorAll('[role="alert"]')]
    .map((n) => n.textContent);
  assert.ok(varsel.includes(t("ui.merkevare.ingen_terskel_varsel")));
  // …OG TERSKELFELTET STÅR TOMT.
  await vent(() => h.querySelector("#mv-t-terskel") !== null);
  assert.equal(h.querySelector("#mv-t-terskel").value, "");
  assert.equal(h.querySelector("#mv-t-funnfrist").value, "");
});

test("Merkevare: terskelfeltet fylles fra basen, ikke fra en konstant",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visMerkevare(h, ctx());
    await vent(() => h.querySelector("#mv-t-terskel") !== null);
    assert.equal(h.querySelector("#mv-t-terskel").value, "80");
    assert.equal(h.querySelector("#mv-t-funnfrist").value, "14");
    assert.equal(h.querySelector("#mv-t-henvfrist").value, "3");
  });

test("Merkevare: vurderingen melder dommen, ikke bare «ok»", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visMerkevare(h, ctx());
  await apneForste(h);
  const knapp = [...h.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.merkevare.knapp.vurder"));
  assert.ok(knapp, "vurderingsknappen manglet");
  knapp.click();
  await vent(() => SISTE !== undefined);
  assert.equal(SISTE.sti, `/v1/merkevare/funn/${F1}/vurder`);
  await vent(() => document.body.textContent.includes("87"));
  const tekst = document.body.textContent;
  assert.ok(tekst.includes("87") && tekst.includes("80"),
    "kvitteringen bar ikke likheten og terskelen");
  assert.ok(tekst.includes(t("ui.merkevare.grunnlag_avstand")),
    "kvitteringen bar ikke grunnlaget");
});

test("Merkevare: en avvist vurdering SIER det", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visMerkevare(h, ctx());
  await apneForste(h);
  SVARSTATUS = 409;
  const knapp = [...h.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.merkevare.knapp.vurder"));
  knapp.click();
  await vent(() => document.body.textContent.includes(
    t("ui.merkevare.feil.vurdering")));
  assert.ok(document.body.textContent.includes(
    t("ui.merkevare.feil.vurdering")));
  assert.equal(knapp.disabled, false, "knappen ble liggende død");
});

test("Merkevare: et uvurdert funn sies høyt", async () => {
  SVAR = { ...fullSvar(),
    [`/v1/merkevare/${M1}/funn`]: {
      merkevare_id: M1, request_id: "r-i", funn: [nyttFunn(false)] },
    [`/v1/merkevare/funn/${F2}/vurderinger`]: {
      funn_id: F2, request_id: "r-j", vurderinger: [] } };
  const h = nyHoved();
  visMerkevare(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  tabeller(h)[0].querySelectorAll("tbody tr")[0]
    .querySelector("button").click();
  await vent(() => h.textContent.includes(
    t("ui.merkevare.uten_vurdering_varsel")));
  const varsel = [...h.querySelectorAll('[role="alert"]')]
    .map((n) => n.textContent);
  assert.ok(varsel.includes(t("ui.merkevare.uten_vurdering_varsel")));
});

// ---------------------------------------------------------------------
// HENVISNING OG LUKKING
// ---------------------------------------------------------------------

test("Merkevare: henvisningen sier at den ikke sender noe", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visMerkevare(h, ctx());
  await apneForste(h);
  await vent(() => h.querySelector("#mv-h-unntak") !== null);
  assert.ok(h.textContent.includes(t("ui.merkevare.henvis.hvorfor")));
  const unntak = h.querySelector("#mv-h-unntak");
  const knapp = [...h.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.merkevare.knapp.henvis"));
  assert.equal(knapp.disabled, true);
  unntak.value = "ikke en uuid";
  unntak.dispatchEvent(new window.Event("input"));
  assert.equal(knapp.disabled, true, "en ugyldig id åpnet knappen");
  unntak.value = "44444444-4444-4444-4444-444444444444";
  unntak.dispatchEvent(new window.Event("input"));
  assert.equal(knapp.disabled, false);
  unntak.form.dispatchEvent(new window.Event("submit",
    { cancelable: true }));
  await vent(() => SISTE !== undefined);
  assert.equal(SISTE.sti, `/v1/merkevare/funn/${F1}/henvis`);
  assert.equal(SISTE.kropp.unntak_id,
    "44444444-4444-4444-4444-444444444444");
});

test("Merkevare: lukking av et uhenvist funn over terskel tilbys ikke",
  async () => {
    // Døra nekter det (120). Flaten sier hvorfor på forhånd i stedet
    // for å la brukeren møte en 409.
    SVAR = fullSvar();
    const h = nyHoved();
    visMerkevare(h, ctx());
    await apneForste(h);
    await vent(() => h.textContent.includes(
      t("ui.merkevare.lukk.tittel")));
    assert.equal(h.querySelector("#mv-l-begrunnelse"), null,
      "lukkeskjemaet sto der på et uhenvist funn over terskel");
    const varsel = [...h.querySelectorAll('[role="alert"]')]
      .map((n) => n.textContent);
    assert.ok(varsel.some((x) => x.includes("87") && x.includes("80")),
      varsel);
  });

test("Merkevare: et uvurdert funn KAN lukkes", async () => {
  // «Vi så på det, det var ingenting» er et lovlig svar.
  SVAR = { ...fullSvar(),
    [`/v1/merkevare/${M1}/funn`]: {
      merkevare_id: M1, request_id: "r-k", funn: [nyttFunn(false)] },
    [`/v1/merkevare/funn/${F2}/vurderinger`]: {
      funn_id: F2, request_id: "r-l", vurderinger: [] } };
  const h = nyHoved();
  visMerkevare(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  tabeller(h)[0].querySelectorAll("tbody tr")[0]
    .querySelector("button").click();
  await vent(() => h.querySelector("#mv-l-begrunnelse") !== null);
  const b = h.querySelector("#mv-l-begrunnelse");
  b.value = "sett på, ingenting";
  b.dispatchEvent(new window.Event("input"));
  b.form.dispatchEvent(new window.Event("submit",
    { cancelable: true }));
  await vent(() => SISTE !== undefined);
  assert.equal(SISTE.sti, `/v1/merkevare/funn/${F2}/lukk`);
  assert.equal(SISTE.kropp.begrunnelse, "sett på, ingenting");
});

test("Merkevare: forveksling_ikke_henvist tilbys ikke for lukking",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visMerkevare(h, ctx());
    await vent(() => h.querySelector("#mv-v-valg") !== null);
    const verdier = [...h.querySelector("#mv-v-valg").options]
      .map((o) => o.value).filter(Boolean);
    assert.deepEqual(verdier, [W2],
      "det uviskbare varselet ble tilbudt for lukking");
    // …men det STÅR i tabellen, med likheten og terskelen sin — OG
    // MED RIKTIG RETNING. `m55_varslene` returnerer ikke
    // `over_terskel` (den er en generert kolonne på VURDERINGEN), så
    // en flate som leste `undefined` som usant ville skrevet «87 % —
    // under terskelen på 80 %» om nøyaktig den forvekslingen modulen
    // finnes for å vise (CodeRabbit).
    const varseltabell = [...h.querySelectorAll("table")].find(
      (tb) => tb.querySelector("caption").textContent
        === t("ui.merkevare.varsler.tittel"));
    const forventet = t("ui.merkevare.likhet_over")
      .replace("{likhet}", "87").replace("{terskel}", "80");
    assert.ok(varseltabell.textContent.includes(forventet),
      varseltabell.textContent);
    assert.ok(!varseltabell.textContent.includes(
      t("ui.merkevare.likhet_under")
        .replace("{likhet}", "87").replace("{terskel}", "80")));
    assert.ok(varseltabell.textContent.includes(
      t("ui.merkevare.varsel_ikke_henvist")));
  });

// ---------------------------------------------------------------------
// TABELLER, LESERETT, SPRÅK OG AXE
// ---------------------------------------------------------------------

test("Merkevare: sammendraget setter uhenviste først", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visMerkevare(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  const forste = h.querySelector("section strong").textContent;
  assert.equal(forste,
    t("ui.merkevare.uhenviste_sum").replace("{n}", "1"));
  assert.ok(h.textContent.includes(
    t("ui.merkevare.uvurderte_sum").replace("{n}", "1")));
  assert.ok(h.textContent.includes(
    t("ui.merkevare.terskelen_er").replace("{n}", "80")));
});

test("Merkevare: registrert og uregistrert skilles i tabellen", () => {
  // Et registrert varemerke og et innarbeidet kjennetegn har ikke
  // samme vern, og forskjellen skal stå på raden.
  const node = merketabell(BILDE.merker, () => {});
  const rader = [...node.querySelectorAll("tbody tr")]
    .map((r) => r.textContent);
  assert.ok(rader[0].includes("Patentstyret 301234"), rader[0]);
  assert.ok(rader[1].includes(t("ui.merkevare.uregistrert")),
    rader[1]);
});

test("Merkevare: tilstanden navngir det som mangler", () => {
  assert.equal(tilstandTekst({ likhet: null }),
    t("ui.merkevare.uvurdert"));
  assert.equal(
    tilstandTekst({ likhet: 87, over_terskel: true,
                    henvist_unntak_id: null }),
    t("ui.merkevare.venter_paa_henvisning"));
  assert.equal(
    tilstandTekst({ likhet: 87, over_terskel: true,
                    henvist_unntak_id: "x" }),
    t("ui.merkevare.henvist"));
  assert.equal(
    tilstandTekst({ likhet: 40, over_terskel: false }),
    t("ui.merkevare.under_terskel"));
  assert.equal(tilstandTekst({ likhet: 87, lukket_ts: "nå" }),
    t("ui.merkevare.lukket"));
});

test("Merkevare: størrelsen og summen leses uten å lyve", () => {
  // 0 byte og 900 byte er ikke det samme, og «0 kB» ville sagt at
  // begge er ingenting.
  assert.equal(bytesTekst(0), t("ui.merkevare.bytes")
    .replace("{n}", "0"));
  assert.equal(bytesTekst(900), t("ui.merkevare.bytes")
    .replace("{n}", "900"));
  assert.equal(bytesTekst(40960), t("ui.merkevare.kilobytes")
    .replace("{n}", "40"));
  assert.equal(bytesTekst(null), "–");
  assert.equal(summenTekst(SHA), `${SHA.slice(0, 12)}…`);
  assert.equal(summenTekst(null), "–");
});

test("Merkevare: de lukkede settene er 120s egne", () => {
  const sql = readFileSync(new URL(
    "../../db/migrations/120_m55_merkevarefunn.sql",
    import.meta.url), "utf8");
  // MERKETS ART og FUNNETS BRUKSFORM er CHECK-er i basen. En port som
  // gjentok listene her ville bare målt seg selv, og en flate som
  // tilbød et valg døra avviser ville lært brukeren at systemet er
  // upålitelig.
  const les = (navn) => {
    const m = new RegExp(`${navn} CHECK \\([a-z]+ IN \\(([^)]*)\\)`)
      .exec(sql);
    assert.ok(m, navn);
    return [...m[1].matchAll(/'([a-z_]+)'/g)].map((x) => x[1]);
  };
  assert.deepEqual(ARTER, les("merkevare_art_lukket"));
  assert.deepEqual(BRUKSFORMER, les("merkevarefunn_bruksform_lukket"));
});

test("Merkevare: en lesende økt ser bevisene, men ingen skriveveier",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visMerkevare(h, ctx(["okonomi:read"]));
    await vent(() => tabeller(h).length >= 2);
    assert.ok(h.textContent.includes("Disponit"));
    assert.equal(h.querySelector("form"), null,
      "en lesende økt fikk et skjema");
    await apneForste(h);
    for (const n of ["vurder", "henvis", "lukk_funn", "lagre_funn",
                     "lagre_kopi", "lagre_merke", "aktiver",
                     "deaktiver"]) {
      const k = [...h.querySelectorAll("button")].find(
        (b) => b.textContent === t(`ui.merkevare.knapp.${n}`));
      assert.equal(k, undefined, `lesende økt fikk «${n}»`);
    }
    // …men BEVISET og VURDERINGEN er synlig, med terskelen.
    assert.ok(h.textContent.includes(SHA.slice(0, 12)));
    assert.ok(h.textContent.includes("87") && h.textContent
      .includes("80"));
  });

test("Merkevare: hver tabell er en ekte tabell", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visMerkevare(h, ctx());
  await apneForste(h);
  const t2 = tabeller(h);
  assert.ok(t2.length >= 4, `bare ${t2.length} tabeller`);
  for (const tab of t2) {
    assert.ok(tab.querySelector("caption"), "tabell uten caption");
    assert.ok(tab.querySelectorAll('th[scope="col"]').length > 0,
      "tabell uten kolonneoverskrifter");
    assert.ok(tab.closest(".tablewrap"),
      "tabell uten sidescroll-container");
    const rad = tab.querySelector("tbody tr");
    if (rad) {
      assert.ok(rad.querySelector('th[scope="row"]'),
        "rad uten th[scope=row]");
    }
  }
});

test("Merkevare: ingen hardkodet tekst", async () => {
  const PL = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visMerkevare(h, ctx());
    await vent(() => tabeller(h).length >= 2);
    // `th[scope="row"]` er UTELATT: den cellen bærer merkenavnet, det
    // observerte navnet og kilde-URL-en — tenantens egne data og
    // tredjepartens tekst.
    for (const node of h.querySelectorAll(
      'h2, h3, h4, label, caption, legend, th[scope="col"], button')) {
      const s = node.textContent.trim();
      if (!s) continue;
      assert.ok(s.startsWith("PL_"), `hardkodet tekst: «${s}»`);
    }
  } finally {
    settI18nForTest(NB, "nb");
  }
});

test("Merkevare: null alvorlige axe-brudd på oversikten", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visMerkevare(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Merkevare: null alvorlige axe-brudd med detaljpanelet åpent",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visMerkevare(h, ctx());
    await apneForste(h);
    await vent(() => h.querySelector("#mv-f-kopi") !== null);
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Merkevare: null alvorlige axe-brudd på et tomt register",
  async () => {
    SVAR = { ...fullSvar(), "/v1/merkevare": TOMT,
             "/v1/merkevare/varsler": { request_id: "r-m",
                                        varsler: [] } };
    const h = nyHoved();
    visMerkevare(h, ctx());
    await vent(() => h.textContent.includes(
      t("ui.merkevare.liste.ingen")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Merkevare: tabellene står alene uten brudd", async () => {
  let brudd = await alvorligeBrudd(merketabell(BILDE.merker, () => {}),
                                   { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  brudd = await alvorligeBrudd(funntabell(FUNN.funn),
                               { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  brudd = await alvorligeBrudd(
    vurderingstabell(VURDERINGER.vurderinger), { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  brudd = await alvorligeBrudd(kopitabell(BILDE.bevaringskopier),
                               { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});
