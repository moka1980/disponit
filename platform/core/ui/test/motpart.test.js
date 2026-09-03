// M-48 motpartsflaten (116) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover, og ÉN av dem er
// annerledes enn i søskenmodulene:
//
//   * `oppslag_uten_formaal_og_hjemmel`: flaten HAR en «slå opp»-knapp
//     — klyngens eneste — så doktrinen kan ikke vises som et fravær.
//     Den vises som at knappen er DEAKTIVERT til formål og hjemmel er
//     fylt ut, og at ingen av dem har en forhåndsvalgt verdi.
//   * `oppslag_uten_ferskhetsvindu`: vinduet står SYNLIG ved knappen.
//     Et oppslag som blir nektet av basen etter et klikk lærer
//     brukeren ingenting; regelen på skjermen gjør det.
//   * `modulen_satte_kredittgrense`: det finnes ingen kontroll som
//     setter en grense. DET fraværet er fortsatt dommen.
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd på hver
//     skjerm flaten kan stå i.
//   * `kredittpolicy_hardkodet`: vinduet og taket som vises er
//     TENANTENS tall, ikke konstanter i flaten.
//   * En lesende økt ser registeret, men INGEN mutasjonskontroller.
//   * Ingen hardkodet tekst (pseudo-locale).
//
// Ingen delt fixture (m16-formen): hver test bygger sin egen skjerm.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import {
  FORMAAL, GRUNNLAG, funnTabell, oreTekst, profilTekst, visMotpart,
} from "../static/js/flater/motpart.js";

settI18nForTest(NB, "nb");

const M1 = "11111111-1111-1111-1111-111111111111";
const M2 = "22222222-2222-2222-2222-222222222222";
const V1 = "aaaaaaaa-1111-1111-1111-111111111111";

const BILDE = {
  sammendrag: {
    motparter: 40, aktive: 12, med_profil: 9, vurderte: 6,
    apne_funn: 4, apne_avviklet: 1, oppslag_siste_dogn: 7,
    apne_reservasjoner: 1, har_krav: true, kravversjon: 2,
    registrert_vert: "data.brreg.no", vist: 2,
  },
  motparter: [
    { motpart_id: M1, organisasjonsnummer: "923609016",
      navn_oppgitt: "Equinor ASA", aktiv: true,
      opprettet: "2026-08-01T09:00:00+00:00",
      siste_versjon: "2026-09-01T09:00:00+00:00",
      siste_registerstatus: "aktiv",
      siste_vurdering: "2026-09-01T10:00:00+00:00",
      siste_forslag_ore: 25000000, apne_funn: 1 },
    { motpart_id: M2, organisasjonsnummer: "912345678",
      navn_oppgitt: "Annen AS", aktiv: false,
      opprettet: "2026-08-02T09:00:00+00:00",
      siste_versjon: null, siste_registerstatus: null,
      siste_vurdering: null, siste_forslag_ore: null, apne_funn: 2 },
  ],
  krav: {
    oppslag_ferskhet_timer: 24, vurdering_gyldig_dogn: 180,
    uvurdert_dogn: 30, maks_forslag_ore: 50000000,
    godkjente_grunnlag: ["foretaksregister"], versjon: 2,
    oppdatert: "2026-08-01T09:00:00+00:00", oppdatert_av: "kari",
  },
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    motparter: 0, aktive: 0, med_profil: 0, vurderte: 0, apne_funn: 0,
    apne_avviklet: 0, oppslag_siste_dogn: 0, apne_reservasjoner: 0,
    har_krav: false, kravversjon: null,
    registrert_vert: "data.brreg.no", vist: 0,
  },
  motparter: [], krav: null, request_id: "r-b",
};

const HISTORIKK = {
  motpart_id: M1,
  versjoner: [
    { versjon_id: V1, oppslag_id: "bbbbbbbb-1111-1111-1111-111111111111",
      kilde: "foretaksregister", kildeversjon: "2026-09-01",
      navn_registrert: "EQUINOR ASA", organisasjonsform: "ASA",
      registerstatus: "aktiv", konkurs: false,
      under_tvangsavvikling: false, gjelder_fra: "2026-09-01",
      registrert: "2026-09-01T09:00:00+00:00", registrert_av: "kari" },
  ],
  request_id: "r-c",
};

const LOGG = {
  motpart_id: M1,
  oppslag: [
    { oppslag_id: "bbbbbbbb-1111-1111-1111-111111111111",
      organisasjonsnummer: "923609016", vert: "data.brreg.no",
      formaal: "kredittvurdering",
      hjemmel: "personvernforordningen art 6.1.f",
      svarstatus: "treff", svar_sha256: "a".repeat(64),
      reservert: "2026-09-01T09:00:00+00:00", reservert_av: "kari",
      fullfort: "2026-09-01T09:00:01+00:00" },
  ],
  request_id: "r-d",
};

const FUNN = {
  request_id: "r-e",
  funn: [
    { motpart_id: M2, organisasjonsnummer: "912345678",
      navn_oppgitt: "Annen AS", funntype: "uvurdert_motpart",
      over_grense: 5, siste_registerstatus: null,
      siste_forslag_ore: null, kravversjon: 2,
      forst_sett: "2026-09-02T09:00:00+00:00",
      sist_sett_sveip: "2026-09-03T09:00:00+00:00", apen: true,
      lukket_ts: null },
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
        json: async () => ({ feil: "motpart_ulovlig_tilstand" }) };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
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
  KALL = [];
  SISTE = undefined;
  SVARSTATUS = 200;
  return m;
}

function fullSvar() {
  return {
    "/v1/motpart": BILDE,
    "/v1/motpart/funn": FUNN,
    [`/v1/motpart/${M1}/historikk`]: HISTORIKK,
    [`/v1/motpart/${M1}/oppslagslogg`]: LOGG,
    [`/v1/motpart/${M2}/historikk`]: { motpart_id: M2, versjoner: [],
                                       request_id: "r-f" },
    [`/v1/motpart/${M2}/oppslagslogg`]: { motpart_id: M2, oppslag: [],
                                          request_id: "r-g" },
  };
}

function tabeller(h) {
  return [...h.querySelectorAll("table")];
}

async function apneForste(h) {
  await vent(() => tabeller(h).length >= 2);
  tabeller(h)[0].querySelectorAll("tbody tr")[0]
    .querySelector("button").click();
  await vent(() => h.querySelector("#mp-o-formaal") !== null);
}

// ---------------------------------------------------------------------
// oppslag_uten_formaal_og_hjemmel — DOKTRINEN SOM SYNLIG KRAV
// ---------------------------------------------------------------------

test("Motpart: oppslagsknappen er død til formål OG hjemmel er fylt ut",
  async () => {
    // MUTASJONEN SOM DREPER DENNE: fjern `disabled`, eller la ett av
    // feltene være nok.
    SVAR = fullSvar();
    const h = nyHoved();
    visMotpart(h, ctx());
    await apneForste(h);
    const formaal = h.querySelector("#mp-o-formaal");
    const hjemmel = h.querySelector("#mp-o-hjemmel");
    const knapp = [...h.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.motpart.knapp.slaa_opp"));
    assert.ok(knapp, "fant ingen oppslagsknapp");
    assert.equal(knapp.disabled, true, "knappen var levende fra start");

    // Bare formål: fortsatt død.
    formaal.value = "kredittvurdering";
    formaal.dispatchEvent(new window.Event("change"));
    assert.equal(knapp.disabled, true, "formål alene åpnet knappen");

    // Bare hjemmel: også død.
    formaal.value = "";
    formaal.dispatchEvent(new window.Event("change"));
    hjemmel.value = "personvernforordningen art 6.1.f";
    hjemmel.dispatchEvent(new window.Event("input"));
    assert.equal(knapp.disabled, true, "hjemmel alene åpnet knappen");

    // Begge: levende.
    formaal.value = "onboarding";
    formaal.dispatchEvent(new window.Event("change"));
    assert.equal(knapp.disabled, false, "begge fylt ut, men død knapp");
  });

test("Motpart: en for kort hjemmel teller ikke", async () => {
  // «ok» er ikke en hjemmel. Basen krever åtte tegn; flaten sier det
  // FØR forespørselen, ikke etterpå.
  SVAR = fullSvar();
  const h = nyHoved();
  visMotpart(h, ctx());
  await apneForste(h);
  const formaal = h.querySelector("#mp-o-formaal");
  const hjemmel = h.querySelector("#mp-o-hjemmel");
  const knapp = [...h.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.motpart.knapp.slaa_opp"));
  formaal.value = "kredittvurdering";
  formaal.dispatchEvent(new window.Event("change"));
  hjemmel.value = "kort";
  hjemmel.dispatchEvent(new window.Event("input"));
  assert.equal(knapp.disabled, true);
});

test("Motpart: formålet har ingen forhåndsvalgt verdi", async () => {
  // MUTASJONEN SOM DREPER DENNE: forhåndsvelg «kredittvurdering».
  // Da måtte brukeren aldri ta stilling, og porten ville vært pynt.
  SVAR = fullSvar();
  const h = nyHoved();
  visMotpart(h, ctx());
  await apneForste(h);
  const formaal = h.querySelector("#mp-o-formaal");
  assert.equal(formaal.value, "", "et formål var forhåndsvalgt");
  assert.equal(formaal.options[0].value, "");
  // …og alle de lovlige formålene er med.
  const verdier = [...formaal.options].map((o) => o.value).filter(Boolean);
  assert.deepEqual(verdier, FORMAAL);
});

test("Motpart: oppslaget sender formål og hjemmel i kroppen",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visMotpart(h, ctx());
    await apneForste(h);
    const formaal = h.querySelector("#mp-o-formaal");
    const hjemmel = h.querySelector("#mp-o-hjemmel");
    formaal.value = "periodisk_kontroll";
    formaal.dispatchEvent(new window.Event("change"));
    hjemmel.value = "internkontroll, art 6.1.f";
    hjemmel.dispatchEvent(new window.Event("input"));
    h.querySelector("#mp-o-formaal").form
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE !== undefined);
    assert.equal(SISTE.sti, `/v1/motpart/${M1}/oppslag`);
    assert.equal(SISTE.kropp.formaal, "periodisk_kontroll");
    assert.equal(SISTE.kropp.hjemmel, "internkontroll, art 6.1.f");
    // SP-2: hver forespørsel bærer en idempotensnøkkel — en gjentatt
    // POST må ikke bli to utgående oppslag.
    assert.ok(SISTE.headers["Idempotency-Key"], SISTE.headers);
  });

// ---------------------------------------------------------------------
// oppslag_uten_ferskhetsvindu — REGELEN STÅR PÅ SKJERMEN
// ---------------------------------------------------------------------

test("Motpart: ferskhetsvinduet vises med tenantens eget tall",
  async () => {
    // MUTASJONEN SOM DREPER DENNE: skriv et fast tall i flaten.
    SVAR = fullSvar();
    const h = nyHoved();
    visMotpart(h, ctx());
    await apneForste(h);
    const tekst = h.textContent;
    assert.ok(tekst.includes(
      t("ui.motpart.oppslag.vindu").replace("{timer}", "24")),
      "ferskhetsvinduet sto ikke ved knappen");
  });

test("Motpart: uten policy sier flaten det, i stedet for å gjette",
  async () => {
    SVAR = { ...fullSvar(), "/v1/motpart": {
      ...BILDE, krav: null,
      sammendrag: { ...BILDE.sammendrag, har_krav: false } } };
    const h = nyHoved();
    visMotpart(h, ctx());
    await apneForste(h);
    assert.ok(h.textContent.includes(t("ui.motpart.oppslag.uten_krav")));
  });

// ---------------------------------------------------------------------
// modulen_satte_kredittgrense — FRAVÆRET ER FORTSATT DOMMEN
// ---------------------------------------------------------------------

test("Motpart: ingen kontroll setter en kredittgrense", async () => {
  // MUTASJONEN SOM DREPER DENNE: legg til en «Innvilg»-knapp.
  SVAR = fullSvar();
  const h = nyHoved();
  visMotpart(h, ctx());
  await apneForste(h);
  const knapper = [...h.querySelectorAll("button")]
    .map((b) => b.textContent.toLowerCase());
  for (const forbudt of ["innvilg", "avslå", "avslag", "sett grense",
                         "godkjenn"]) {
    assert.ok(!knapper.some((k) => k.includes(forbudt)),
      `fant knapp: ${forbudt}`);
  }
  // Kolonnen heter FORSLAG, ikke grense.
  const kol = [...h.querySelectorAll('th[scope="col"]')]
    .map((n) => n.textContent);
  assert.ok(kol.includes(t("ui.motpart.kol.forslag")), kol);
});

test("Motpart: oppslagstallet og verten står i sammendraget",
  async () => {
    // Klyngens unntak er begrunnet med at forespørselen er nødvendig
    // — da må antallet forespørsler være det første noen ser.
    SVAR = fullSvar();
    const h = nyHoved();
    visMotpart(h, ctx());
    await vent(() => tabeller(h).length >= 2);
    const tekst = h.textContent;
    assert.ok(tekst.includes(
      t("ui.motpart.oppslag_siste_dogn").replace("{n}", "7")), tekst);
    assert.ok(tekst.includes(
      t("ui.motpart.mot_vert").replace("{vert}", "data.brreg.no")));
    // En reservasjon uten svar er en forespørsel vi ikke vet utfallet
    // av, og skal se annerledes ut enn et vanlig tall.
    assert.ok(tekst.includes(
      t("ui.motpart.apne_reservasjoner").replace("{n}", "1")));
  });

test("Motpart: oppslagsloggen vises med vert, formål og hjemmel",
  async () => {
    // Et unntak ingen kan etterprøve er ikke et unntak.
    SVAR = fullSvar();
    const h = nyHoved();
    visMotpart(h, ctx());
    await apneForste(h);
    const logg = [...h.querySelectorAll("caption")]
      .find((c) => c.textContent === t("ui.motpart.logg.tittel"));
    assert.ok(logg, "fant ingen oppslagslogg");
    const rad = logg.closest("table").querySelector("tbody tr");
    const celler = [...rad.children].map((c) => c.textContent);
    assert.ok(celler.includes("data.brreg.no"), celler);
    assert.ok(celler.includes(
      t("ui.motpart.formaal.kredittvurdering")), celler);
    assert.ok(celler.includes("personvernforordningen art 6.1.f"),
      celler);
  });

// ---------------------------------------------------------------------
// Beløp, tabeller, og lesende økt
// ---------------------------------------------------------------------

test("Motpart: øre regnes om uten flyttall", () => {
  // MUTASJONEN SOM DREPER DENNE: bytt til `ore / 100`.
  assert.equal(oreTekst(25000000), "250000,00");
  assert.equal(oreTekst(1), "0,01");
  assert.equal(oreTekst(0), "0,00");
  assert.equal(oreTekst(-5), "-0,05");
  // Et beløp større enn Number.MAX_SAFE_INTEGER skal fortsatt stemme.
  assert.equal(oreTekst("9007199254740993"), "90071992547409,93");
  assert.equal(oreTekst(null), "–");
  assert.equal(oreTekst(undefined), "–");
});

test("Motpart: «ingen profil hentet» er noe annet enn «aktiv»", () => {
  // WCAG 1.4.1 og alminnelig ærlighet: en motpart vi ikke har spurt
  // om skal ikke se ut som en vi har fått grønt lys på.
  assert.equal(profilTekst({ siste_registerstatus: null }),
    t("ui.motpart.uten_profil"));
  assert.equal(profilTekst({ siste_registerstatus: "aktiv" }),
    t("ui.motpart.status.aktiv"));
  assert.notEqual(profilTekst({ siste_registerstatus: null }),
    profilTekst({ siste_registerstatus: "aktiv" }));
});

test("Motpart: tabellene er ekte", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visMotpart(h, ctx());
  await apneForste(h);
  const tab = tabeller(h);
  assert.ok(tab.length >= 4, `bare ${tab.length} tabeller`);
  for (const tabell of tab) {
    assert.ok(tabell.querySelector("caption"),
      "tabell uten <caption>");
    assert.ok(tabell.closest(".tablewrap"),
      "tabell uten sidescroll-container");
    const rad = tabell.querySelector("tbody tr");
    if (rad) {
      assert.ok(rad.querySelector('th[scope="row"]'),
        "rad uten th[scope=row]");
    }
  }
});

test("Motpart: en lesende økt ser registeret, men ingen skriveveier",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visMotpart(h, ctx(["okonomi:read"]));
    await vent(() => tabeller(h).length >= 2);
    assert.ok(h.textContent.includes("Equinor ASA"));
    assert.equal(h.querySelector("form"), null,
      "en lesende økt fikk et skjema");
    // …og ingen oppslagsknapp i detaljpanelet heller.
    tabeller(h)[0].querySelectorAll("tbody tr")[0]
      .querySelector("button").click();
    await vent(() => h.textContent.includes("923609016"));
    assert.equal(h.querySelector("#mp-o-formaal"), null,
      "en lesende økt fikk slå opp");
  });

test("Motpart: funn kan lukkes, men bare med en begrunnelse",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visMotpart(h, ctx());
    await vent(() => h.querySelector("#mp-f-notat") !== null);
    const notat = h.querySelector("#mp-f-notat");
    assert.equal(notat.required, true);
    assert.equal(notat.getAttribute("minlength"), "4");
    const valg = h.querySelector("#mp-f-valg");
    assert.equal(valg.value, "", "et funn var forhåndsvalgt");
  });

// ---------------------------------------------------------------------
// ui_axe_alvorlige_brudd
// ---------------------------------------------------------------------

test("Motpart: null alvorlige axe-brudd på registeret", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visMotpart(h, ctx());
  await vent(() => tabeller(h).length >= 2);
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Motpart: null alvorlige axe-brudd med oppslagsskjemaet åpent",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visMotpart(h, ctx());
    await apneForste(h);
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Motpart: null alvorlige axe-brudd på et tomt register",
  async () => {
    SVAR = { ...fullSvar(), "/v1/motpart": TOMT,
             "/v1/motpart/funn": { request_id: "r-h", funn: [] } };
    const h = nyHoved();
    visMotpart(h, ctx());
    await vent(() => h.textContent.includes(
      t("ui.motpart.liste.ingen")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Motpart: funntabellen står alene uten alvorlige brudd", async () => {
  // NODEN SENDES DIREKTE. `alvorligeBrudd` lager sitt eget brett og
  // føyer noden til det; sender man inn `document.body`, tømmer den
  // kroppen og prøver å føye den til seg selv.
  const brudd = await alvorligeBrudd(funnTabell(FUNN.funn),
                                     { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Motpart: ingen hardkodet tekst", async () => {
  const PL = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visMotpart(h, ctx());
    await apneForste(h);
    // `th[scope="row"]` er UTELATT: den cellen bærer navnet, datoen og
    // organisasjonsnummeret — altså tenantens egne data. Det samme
    // gjelder funnvalgets options, som bygges av motpartsnavn.
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

test("Motpart: grunnlagene i policyen er et lukket sett", () => {
  // En tenant kan velge fra settet, ikke utvide det.
  assert.deepEqual(GRUNNLAG,
    ["foretaksregister", "manuell_gjennomgang"]);
});
