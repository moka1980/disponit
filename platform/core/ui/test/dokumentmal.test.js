// M-5 malflaten (094) — jsdom + axe.
//
// Portene her måler det invariantene handler om på FLATENS side: at et
// manglende felt er SYNLIG og NAVNGITT (aldri en tom plass), at en låst
// klausul er merket som låst i TEKST (aldri bare en farge), at flaten
// aldri sender en tom streng som om den var en verdi, og at alt dette
// står i sidens eget tilgjengelighetstre — ikke i en iframe axe og en
// skjermleser ikke går inn i.
//
// Ingen delt fixture (m16/m35-formen): hver test bygger sin egen
// tilstand.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visDokumentmal } from "../static/js/flater/dokumentmal.js";

settI18nForTest(NB, "nb");

const KLAUSUL = "Oppsigelsestid er tre maaneder.";

const VERSJON_PUBLISERT = {
  versjon_id: "11111111-1111-1111-1111-111111111111",
  versjonsnr: 2, status: "publisert",
  opprettet: "2026-08-30T09:00:00+00:00", opprettet_av: "eier",
  publisert: "2026-08-31T09:00:00+00:00", publisert_av: "eier",
  tilbaketrukket: null, tilbaketrukket_av: null,
  komponenter: [
    { rekkefolge: 1, komponenttype: "tekst",
      innhold: "Arbeidsavtale mellom ", feltnokkel: null, laast: false },
    { rekkefolge: 2, komponenttype: "felt", innhold: null,
      feltnokkel: "arbeidsgiver", laast: false },
    { rekkefolge: 3, komponenttype: "tekst", innhold: " og ",
      feltnokkel: null, laast: false },
    { rekkefolge: 4, komponenttype: "felt", innhold: null,
      feltnokkel: "arbeidstaker", laast: false },
    { rekkefolge: 5, komponenttype: "klausul", innhold: KLAUSUL,
      feltnokkel: null, laast: true },
  ],
  felt: [
    { feltnokkel: "arbeidsgiver", paakrevd: true, felttype: "tekst",
      beskrivelse: "Arbeidsgiverens navn" },
    { feltnokkel: "arbeidstaker", paakrevd: true, felttype: "tekst",
      beskrivelse: "Arbeidstakerens navn" },
  ],
};

const VERSJON_UTKAST = {
  ...VERSJON_PUBLISERT,
  versjon_id: "22222222-2222-2222-2222-222222222222",
  versjonsnr: 1, status: "utkast",
  publisert: null, publisert_av: null,
};

const REGISTER = {
  familier: [{
    familie_id: "33333333-3333-3333-3333-333333333333",
    navn: "Arbeidsavtale", beskrivelse: "Standard avtale",
    opprettet: "2026-08-29T09:00:00+00:00", opprettet_av: "eier",
    versjoner: [VERSJON_PUBLISERT, VERSJON_UTKAST],
  }],
  request_id: "r-test",
};

// Utfyllingssvaret med ETT dekket og ETT manglende felt — nøyaktig
// tilstanden flaten finnes for å vise ærlig.
const UTFYLLING = {
  versjon_id: VERSJON_PUBLISERT.versjon_id,
  mangler: ["arbeidstaker"],
  fullstendig: false,
  komponenter: [
    { rekkefolge: 1, komponenttype: "tekst", feltnokkel: null, laast: false,
      paakrevd: false, dekket: true, tekst: "Arbeidsavtale mellom " },
    { rekkefolge: 2, komponenttype: "felt", feltnokkel: "arbeidsgiver",
      laast: false, paakrevd: true, dekket: true, tekst: "Acme AS" },
    { rekkefolge: 3, komponenttype: "tekst", feltnokkel: null, laast: false,
      paakrevd: false, dekket: true, tekst: " og " },
    { rekkefolge: 4, komponenttype: "felt", feltnokkel: "arbeidstaker",
      laast: false, paakrevd: true, dekket: false, tekst: null },
    { rekkefolge: 5, komponenttype: "klausul", feltnokkel: null, laast: true,
      paakrevd: false, dekket: true, tekst: KLAUSUL },
  ],
};

let SVAR;
let SISTE_POST = null;
globalThis.fetch = async (url, opsjoner = {}) => {
  const sti = url.split("?")[0];
  if ((opsjoner.method || "GET") !== "GET") {
    SISTE_POST = { sti, kropp: JSON.parse(opsjoner.body || "{}") };
    const svar = SVAR[sti];
    if (!svar) {
      return { ok: false, status: 409,
        json: async () => ({ feil: "dokumentmal_ulovlig_tilstand" }) };
    }
    return { ok: true, status: 200, json: async () => svar };
  }
  const oppf = SVAR[sti];
  if (!oppf) {
    return { ok: false, status: 404,
      json: async () => ({ feil: "ikke_funnet" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx(scopes = ["decisions:read"]) {
  return { sprak: "nb", scopes, tenant: "acme", paaUautorisert: () => {} };
}

async function vent(pred, n = 80) {
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
  return m;
}

function nullstill() {
  SISTE_POST = null;
  SVAR = {
    "/v1/dokumentmal": REGISTER,
    [`/v1/dokumentmal/versjon/${VERSJON_PUBLISERT.versjon_id}/utfylling`]:
      UTFYLLING,
  };
}

test("Dokumentmal: registeret, statusordene og tabellsemantikken — axe rent",
  async () => {
    nullstill();
    const h = nyHoved();
    visDokumentmal(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 1);

    const tekst = h.textContent;
    // Tilstanden som ORD, aldri som farge alene.
    assert.ok(tekst.includes(t("ui.dokumentmal.status.publisert")));
    assert.ok(tekst.includes(t("ui.dokumentmal.status.utkast")));
    // Låste klausuler telles i REGISTERET også: den som VELGER en mal
    // skal se at den bærer bundet tekst før hun fyller den ut.
    assert.ok(tekst.includes(
      t("ui.dokumentmal.komponenter_med_laaste")
        .replace("{n}", "5").replace("{l}", "1")));

    for (const tb of h.querySelectorAll("table")) {
      assert.ok(tb.querySelector("caption"), "tabell uten caption");
      assert.ok(tb.querySelector('th[scope="col"]'));
      for (const rad of tb.querySelectorAll("tbody tr")) {
        assert.equal(rad.cells[0].tagName, "TH");
        assert.equal(rad.cells[0].getAttribute("scope"), "row");
      }
    }

    // INGEN IFRAME. Dokumentet tegnes med tekstnoder i sidens eget
    // tilgjengelighetstre — en srcdoc-ramme ville lagt det manglende
    // feltet i et dokument axe ikke går inn i og en skjermleser
    // annonserer som «ramme».
    assert.equal(h.querySelectorAll("iframe").length, 0,
      "flaten rendrer i en iframe — hullene havner utenfor axe-treet");

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Dokumentmal: et manglende felt er SYNLIG, navngitt og talt",
  async () => {
    nullstill();
    const h = nyHoved();
    visDokumentmal(h, ctx());
    await vent(() => h.querySelectorAll("form").length >= 1);

    // Utfyllingsskjemaet for den PUBLISERTE versjonen; utkastet har
    // ingen (en knapp som alltid feiler er en løgn om hva systemet kan).
    const inn = h.querySelector(
      `#mal-${VERSJON_PUBLISERT.versjon_id}-arbeidsgiver`);
    assert.ok(inn, "utfyllingsskjemaet mangler et felt for arbeidsgiver");
    assert.ok(!h.querySelector(
      `#mal-${VERSJON_UTKAST.versjon_id}-arbeidsgiver`),
      "et utkast tilbyr utfylling — døren avviser den uansett");
    assert.ok(h.textContent.includes(
      t("ui.dokumentmal.utfylling.kun_publisert")));

    inn.value = "Acme AS";
    const skjema = inn.closest("form");
    skjema.dispatchEvent(new window.Event("submit", { cancelable: true,
      bubbles: true }));
    await vent(() => h.textContent.includes(
      t("ui.dokumentmal.utfylling.mangler")));

    const tekst = h.textContent;
    // PORTENS KJERNE: antallet, navnet OG ordet.
    assert.ok(tekst.includes(t("ui.dokumentmal.utfylling.mangler_antall")
      .replace("{n}", "1")), "antallet manglende felt vises ikke");
    assert.ok(tekst.includes("arbeidstaker"),
      "det manglende feltet er ikke navngitt");
    assert.ok(tekst.includes("Arbeidstakerens navn"),
      "feltets beskrivelse mangler — nøkkelen alene forklarer ingenting");
    assert.ok(tekst.includes(t("ui.dokumentmal.utfylling.mangler")));
    // Den dekkede verdien står der, ellers måler porten ingenting.
    assert.ok(tekst.includes("Acme AS"));

    // Den låste klausulen er MERKET SOM LÅST, i tekst.
    assert.ok(tekst.includes(KLAUSUL));
    assert.ok(tekst.includes(t("ui.dokumentmal.utfylling.laast")),
      "en låst klausul er ikke merket som låst i tekst");

    // TOMME FELT SENDES IKKE SOM TOMME STRENGER: klienten utelater
    // nøkkelen helt, så serveren rapporterer den som manglende i stedet
    // for å motta noe som ser besvart ut.
    assert.deepEqual(SISTE_POST.kropp,
      { verdier: { arbeidsgiver: "Acme AS" } });

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Dokumentmal: overgangsknappene følger tilstanden og skrivescopet",
  async () => {
    nullstill();
    // Uten `bestilling:opprett` finnes verken publiseringsknapp eller
    // skjemaet for ny familie — men utfyllingen står, fordi den bare
    // leser.
    let h = nyHoved();
    visDokumentmal(h, ctx(["decisions:read"]));
    await vent(() => h.querySelectorAll("table").length >= 1);
    let knapper = [...h.querySelectorAll("button")].map((b) => b.textContent);
    assert.ok(!knapper.includes(t("ui.dokumentmal.knapp.publiser")));
    assert.ok(!knapper.includes(t("ui.dokumentmal.knapp.ny_familie")));
    assert.ok(knapper.includes(t("ui.dokumentmal.utfylling.knapp")),
      "utfyllingen ble gatet på skrivescopet — den bare leser");

    h = nyHoved();
    visDokumentmal(h, ctx(["decisions:read", "bestilling:opprett"]));
    await vent(() => h.querySelectorAll("table").length >= 1);
    knapper = [...h.querySelectorAll("button")].map((b) => b.textContent);
    // Utkastet publiseres, den publiserte trekkes tilbake — knappene
    // følger TILSTANDEN, ikke bare scopet.
    assert.ok(knapper.includes(t("ui.dokumentmal.knapp.publiser")));
    assert.ok(knapper.includes(t("ui.dokumentmal.knapp.trekk_tilbake")));
    assert.ok(knapper.includes(t("ui.dokumentmal.knapp.ny_familie")));

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Dokumentmal: et tomt register er en setning, ikke en tom side",
  async () => {
    nullstill();
    SVAR["/v1/dokumentmal"] = { familier: [], request_id: "r" };
    const h = nyHoved();
    visDokumentmal(h, ctx());
    await vent(() => h.textContent.includes(
      t("ui.dokumentmal.ingen_familier")));
    assert.equal(h.querySelectorAll("table").length, 0);
    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Dokumentmal: dørens 409 sier TILSTAND, ikke «noe gikk galt»",
  async () => {
    nullstill();
    // Ingen oppføring for publiseringsruten → riggen svarer 409.
    const h = nyHoved();
    visDokumentmal(h, ctx(["decisions:read", "bestilling:opprett"]));
    await vent(() => h.querySelectorAll("button").length >= 1);
    const knapp = [...h.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.dokumentmal.knapp.publiser"));
    knapp.dispatchEvent(new window.Event("click", { bubbles: true }));
    await vent(() => h.textContent.includes(
      t("ui.dokumentmal.skjema.tilstand_nei")));
    assert.ok(!h.textContent.includes(t("ui.dokumentmal.skjema.feil")),
      "409 ble vist som en generisk feil");
  });

test("Dokumentmal: ingen hardkodet tekst (pseudo-locale)", async () => {
  const PL = Object.fromEntries(Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    nullstill();
    const h = nyHoved();
    visDokumentmal(h, ctx(["decisions:read", "bestilling:opprett"]));
    await vent(() => h.querySelectorAll("table").length >= 1);
    const tekst = h.textContent;
    for (const ekte of ["Dokumentmaler", "Publisert", "Utkast",
                        "Mangler", "Låst klausul", "Fyll ut",
                        "Deklarerte felt"]) {
      assert.ok(!tekst.includes(ekte),
        `hardkodet norsk tekst i flaten: «${ekte}»`);
    }
    assert.ok(tekst.includes("PL_ui.dokumentmal.tittel"));
    // KUNDENS EGNE STRENGER er DATA og står urørt gjennom
    // pseudo-locale: familienavnet, klausulteksten og feltnøklene er
    // malen, ikke grensesnittet.
    assert.ok(tekst.includes("Arbeidsavtale"));
    assert.ok(tekst.includes("Standard avtale"));
  } finally {
    settI18nForTest(NB, "nb");
  }
});

test("Dokumentmal: nb og en har hver eneste ui.dokumentmal-nøkkel",
  async () => {
    // `t()` faller tilbake til NØKKELEN, ikke til nb — en manglende
    // engelsk nøkkel ville vist «ui.dokumentmal.utfylling.mangler» der
    // ordet «Missing» skulle stått, midt i det ene ordet flaten finnes
    // for å si.
    const { readFileSync } = await import("node:fs");
    const en = JSON.parse(readFileSync(
      new URL("../../../../locales/en.json", import.meta.url), "utf-8"));
    const mine = Object.keys(NB).filter((k) => k.startsWith("ui.dokumentmal.")
      || k === "ui.nav.dokumentmal");
    assert.ok(mine.length >= 40, `for få nøkler i porten: ${mine.length}`);
    for (const k of mine) {
      assert.ok(typeof en[k] === "string" && en[k].trim(),
        `en.json mangler ${k}`);
    }
  });
