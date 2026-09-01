// M-21 avtale- og fristflaten (096) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd, på hver
//     av de tre skjermene flaten kan stå i (liste, tom liste, dialog
//     åpen).
//   * FORFALT ER TEKST, ikke bare farge (WCAG 1.4.1).
//   * EIER OG KILDE er egne kolonner — det er hele grunnen til at v1 er
//     en liste og ikke et årshjul.
//   * LUKKEDIALOGEN KREVER KVITTERINGSREFERANSEN, og feilteksten sier
//     HVORFOR. Et tomt felt sender ingenting.
//   * En lesende økt ser registeret, men INGEN mutasjonskontroller.
//   * Ingen hardkodet tekst (pseudo-locale).
//
// Ingen delt fixture (m16-formen): hver test bygger sin egen skjerm.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visAvtalefrist } from "../static/js/flater/avtalefrist.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));

const PLIKTER = {
  plikter: [
    { plikt_id: "11111111-1111-1111-1111-111111111111",
      tittel: "MVA-melding Q3", eier_bruker_id: "bid_a",
      eier_navn: "Kari Nordmann", kilde: "sktl. § 8-3",
      frist: "2026-08-20T00:00:00+00:00", dogn_til_frist: -12,
      gjentakelse: "kvartalsvis", status: "apen", kvittering_ref: null,
      lukket: null, lukket_av: null, bortfall_begrunnelse: null,
      bortfalt: null, bortfalt_av: null },
    { plikt_id: "22222222-2222-2222-2222-222222222222",
      tittel: "Årsregnskap 2026", eier_bruker_id: "bid_b",
      eier_navn: null, kilde: "regnskapsloven § 3-1",
      frist: "2027-06-30T00:00:00+00:00", dogn_til_frist: 300,
      gjentakelse: "aarlig", status: "apen", kvittering_ref: null,
      lukket: null, lukket_av: null, bortfall_begrunnelse: null,
      bortfalt: null, bortfalt_av: null },
    { plikt_id: "33333333-3333-3333-3333-333333333333",
      tittel: "Databehandleravtale Acme", eier_bruker_id: "bid_a",
      eier_navn: "Kari Nordmann", kilde: "DPA-2025-04",
      frist: "2026-05-01T00:00:00+00:00", dogn_til_frist: -120,
      gjentakelse: "engang", status: "lukket",
      kvittering_ref: "ARK-2026-1188",
      lukket: "2026-04-28T09:00:00+00:00", lukket_av: "bid_a",
      bortfall_begrunnelse: null, bortfalt: null, bortfalt_av: null },
  ],
  request_id: "r-p",
};

let SVAR;
let SISTE;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  if (opts && opts.method === "POST") {
    SISTE = { sti, kropp: JSON.parse(opts.body), headers: opts.headers };
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  }
  const oppf = SVAR[sti];
  if (!oppf) {
    return { ok: false, status: 404,
      json: async () => ({ feil: "ikke_funnet" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx(scopes = ["decisions:read", "bestilling:opprett"]) {
  return { sprak: "nb", scopes, tenant: "acme", paaUautorisert: () => {} };
}

async function vent(pred, n = 60) {
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

test("Avtalefrist: listen med eier og kilde, forfalt som TEKST, axe rent",
  async () => {
    SVAR = { "/v1/plikt": PLIKTER };
    SISTE = null;
    const h = nyHoved();
    visAvtalefrist(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 3);

    const tb = h.querySelector("table");
    assert.ok(tb.querySelector("caption").textContent.trim(),
      "tabellen mangler caption");
    assert.ok(tb.querySelector('th[scope="col"]'));
    for (const rad of tb.querySelectorAll("tbody tr")) {
      // Tabellsemantikk i BEGGE retninger: uten th scope="row" mister en
      // skjermleser i frist- og eierkolonnene hvilken plikt raden gjelder.
      assert.equal(rad.cells[0].tagName, "TH");
      assert.equal(rad.cells[0].getAttribute("scope"), "row");
    }

    const tekst = h.textContent;
    // EIER OG KILDE ER SYNLIGE. Det er hele grunnen til at v1 er en
    // liste: et årshjul viser når, ikke hvem og hvorfor.
    assert.ok(tekst.includes("Kari Nordmann"), "eiernavnet vises ikke");
    assert.ok(tekst.includes("bid_b"),
      "eier uten visningsnavn faller ut — en tom celle finner ingen");
    assert.ok(tekst.includes("sktl. § 8-3"), "kilden vises ikke");
    assert.ok(tekst.includes("regnskapsloven § 3-1"));

    // FORFALT SOM TEKST, aldri bare farge. Merket står som ord, og
    // avstanden er en setning — ikke et fargenivå.
    assert.ok(tekst.includes(t("ui.avtalefrist.merke_forfalt")));
    assert.ok(tekst.includes(
      t("ui.avtalefrist.forfalt_for").replace("{dogn}", "12")));
    assert.ok(tekst.includes(
      t("ui.avtalefrist.om_dogn").replace("{dogn}", "300")));
    // ENTALLET HAR SIN EGEN SETNING på begge språk: «in 1 days» ville
    // stått på nøyaktig den raden som forfaller i morgen.
    for (const sett of ["nb", "en"]) {
      const tekster = JSON.parse(readFileSync(
        join(HER, "..", "..", "..", "..", "locales", `${sett}.json`),
        "utf-8"));
      for (const n of ["ui.avtalefrist.om_ett_dogn",
                       "ui.avtalefrist.forfalt_for_ett_dogn"]) {
        assert.ok(tekster[n] && !tekster[n].includes("{dogn}"),
          `${sett}.json: ${n} mangler eller bærer et telleplassholder`);
      }
    }
    // …og den LUKKEDE plikten med gammel frist er IKKE forfalt: den er
    // gjort. Nøyaktig ett merke på skjermen.
    assert.equal(
      [...h.querySelectorAll("strong")].filter(
        (e) => e.textContent === t("ui.avtalefrist.merke_forfalt")).length,
      1, "en lukket plikt med gammel frist ble merket forfalt");

    // Tilstanden står som ord for alle tre statusene flaten kan møte.
    assert.ok(tekst.includes(t("ui.avtalefrist.status.apen")));
    assert.ok(tekst.includes(t("ui.avtalefrist.status.lukket")));

    // <time datetime> på hver frist — maskinlesbar verdi ved siden av
    // den formaterte.
    const tider = [...h.querySelectorAll("time")];
    assert.equal(tider.length, 3);
    for (const e of tider) assert.ok(e.getAttribute("datetime"));

    // Handlingsknapper KUN på de åpne pliktene: dørene avviser en plikt
    // som alt er lukket, og en knapp som alltid feiler er en løgn.
    const rader = [...tb.querySelectorAll("tbody tr")];
    assert.equal(rader[0].querySelectorAll("button").length, 2);
    assert.equal(rader[2].querySelectorAll("button").length, 0);

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Avtalefrist: lesende økt ser registeret, men ingen mutasjonskontroller",
  async () => {
    SVAR = { "/v1/plikt": PLIKTER };
    const h = nyHoved();
    visAvtalefrist(h, ctx(["decisions:read"]));
    await vent(() => h.querySelectorAll("table tbody tr").length === 3);
    // Hvilke frister som løper og hvem som eier dem er ikke
    // administratorens hemmelighet — men å endre dem er hennes.
    assert.equal(h.querySelectorAll("form").length, 0);
    assert.equal(h.querySelectorAll("button").length, 0);
    assert.ok(h.textContent.includes("MVA-melding Q3"));
    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Avtalefrist: lukkedialogen KREVER kvitteringsreferansen, og sier hvorfor",
  async () => {
    SVAR = { "/v1/plikt": PLIKTER };
    SISTE = null;
    const h = nyHoved();
    visAvtalefrist(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 3);

    const rad = h.querySelector("table tbody tr");
    rad.querySelectorAll("button")[0].dispatchEvent(
      new window.Event("click", { bubbles: true }));

    const felt = h.querySelector("#plikt-referanse");
    assert.ok(felt, "dialogen åpnet ikke");
    // FELTET ER PÅKREVD i markeringen, og etiketten er knyttet til det.
    assert.equal(felt.getAttribute("required"), "");
    assert.ok(h.querySelector('label[for="plikt-referanse"]'));
    // Hjelpeteksten er BEGRUNNELSEN, ikke en gjentakelse av etiketten.
    assert.ok(h.textContent.includes(
      t("ui.avtalefrist.dialog.kvitteringhjelp")));

    // Tomt felt → ingenting sendes, og feilteksten sier HVORFOR.
    const skjema = felt.closest("form");
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    await vent(() => h.textContent.includes(
      t("ui.avtalefrist.feil.kvittering_kreves")));
    assert.equal(SISTE, null, "et tomt felt sendte likevel et kall");
    assert.ok(h.querySelector('[role="alert"]'),
      "feilteksten er ikke annonsert");

    // …og med referansen går den gjennom, på plikten som ble valgt.
    felt.value = "ARK-2026-4711";
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    await vent(() => SISTE !== null);
    assert.equal(SISTE.sti,
      "/v1/plikt/11111111-1111-1111-1111-111111111111/lukk");
    assert.equal(SISTE.kropp.kvittering_ref, "ARK-2026-4711");
    // SP-2: kallet bærer en idempotensnøkkel — en tapt respons + nytt
    // klikk skal gjenspille, ikke kvittere ut to ganger.
    assert.ok(SISTE.headers["Idempotency-Key"]);

    // BEKREFTELSEN NÅR FRAM. To krav, og de er ikke det samme:
    //
    //   * den står i APPENS live-region, som overlever at `last()`
    //     tegner hele flaten på nytt. En melding som bare sto i boksen
    //     ville rukket å bli skrevet og revet bort i samme tikk;
    //   * og den lokale kopien ligger UTENFOR det som skjules — lå
    //     live-regionen inne i dialogen, ble kvitteringen både usynlig
    //     og uannonsert i nøyaktig det øyeblikket den hadde noe å si.
    await vent(() => document.body.textContent.includes(
      t("ui.avtalefrist.dialog.ok")));
    const meldinger = [...document.body.querySelectorAll(
      '[aria-live="polite"]')].filter(
      (e) => e.textContent.includes(t("ui.avtalefrist.dialog.ok")));
    assert.ok(meldinger.length, "bekreftelsen står ikke i en live-region");
    for (const m of meldinger) {
      for (let n = m; n; n = n.parentElement) {
        assert.ok(!n.hidden, "bekreftelsen ligger inne i et skjult element");
      }
    }
    // …og skjemaet er borte igjen.
    assert.ok(h.querySelector("#plikt-referanse").closest("[hidden]"),
      "dialogen ble ikke lukket etter en vellykket kvittering");

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Avtalefrist: bortfallsdialogen krever begrunnelsen, med sin egen tekst",
  async () => {
    SVAR = { "/v1/plikt": PLIKTER };
    SISTE = null;
    const h = nyHoved();
    visAvtalefrist(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 3);

    const rad = h.querySelector("table tbody tr");
    rad.querySelectorAll("button")[1].dispatchEvent(
      new window.Event("click", { bubbles: true }));
    const felt = h.querySelector("#plikt-referanse");
    // De to formene deler felt, men ALDRI tekst: en kvittering sier at
    // plikten er GJORT, en bortfallsbegrunnelse at den ikke lenger
    // GJELDER, og en flate som kalte begge «referanse» ville invitert
    // til å bruke den ene som den andre.
    assert.ok(h.textContent.includes(
      t("ui.avtalefrist.dialog.begrunnelsehjelp")));
    assert.ok(!h.textContent.includes(
      t("ui.avtalefrist.dialog.kvitteringhjelp")));

    const skjema = felt.closest("form");
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    await vent(() => h.textContent.includes(
      t("ui.avtalefrist.feil.begrunnelse_kreves")));
    assert.equal(SISTE, null);

    felt.value = "Avtalen er sagt opp av motparten 12.08.2026.";
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    await vent(() => SISTE !== null);
    assert.equal(SISTE.sti,
      "/v1/plikt/11111111-1111-1111-1111-111111111111/bortfall");
    assert.equal(SISTE.kropp.begrunnelse,
      "Avtalen er sagt opp av motparten 12.08.2026.");
  });

test("Avtalefrist: registreringen krever eier eksplisitt", async () => {
  SVAR = { "/v1/plikt": PLIKTER };
  SISTE = null;
  const h = nyHoved();
  visAvtalefrist(h, ctx());
  await vent(() => h.querySelector("#plikt-eier") !== null);
  const eier = h.querySelector("#plikt-eier");
  // EIEREN ER PÅKREVD OG TOM. En flate som forhåndsutfylte innloggeren
  // ville gjort «plikter uten eier»-KPI-en sann på papiret og falsk i
  // praksis — den som registrerer er ofte ikke den som skal gjøre.
  assert.equal(eier.getAttribute("required"), "");
  assert.equal(eier.value, "");
  assert.ok(h.textContent.includes(t("ui.avtalefrist.skjema.eierhjelp")));
  for (const id of ["plikt-tittel", "plikt-kilde", "plikt-frist"]) {
    assert.equal(h.querySelector(`#${id}`).getAttribute("required"), "");
    assert.ok(h.querySelector(`label[for="${id}"]`), `${id} mangler label`);
  }

  h.querySelector("#plikt-tittel").value = "Skattemelding 2026";
  eier.value = "bid_c";
  h.querySelector("#plikt-kilde").value = "sktfvl. § 8-2";
  h.querySelector("#plikt-frist").value = "2027-05-31";
  h.querySelector("#plikt-gjentakelse").value = "aarlig";
  h.querySelector("#plikt-tittel").closest("form").dispatchEvent(
    new window.Event("submit", { bubbles: true, cancelable: true }));
  await vent(() => SISTE !== null);
  assert.equal(SISTE.sti, "/v1/plikt");
  assert.equal(SISTE.kropp.eier_bruker_id, "bid_c");
  // Fristen sendes som midnatt UTC: en dato er en DAG, og en lokal
  // midnatt ville gjort samme frist til to ulike tidspunkter for to
  // kolleger.
  assert.equal(SISTE.kropp.frist, "2027-05-31T00:00:00Z");
  assert.equal(SISTE.kropp.gjentakelse, "aarlig");
});

test("Avtalefrist: tomt register sier hva fraværet betyr", async () => {
  SVAR = { "/v1/plikt": { plikter: [], request_id: "r" } };
  const h = nyHoved();
  visAvtalefrist(h, ctx());
  await vent(() => h.textContent.includes(t("ui.avtalefrist.liste.ingen")));
  // ÆRLIG TOMTILSTAND: et tomt register er ikke «ingenting å gjøre».
  assert.equal(h.querySelectorAll("table").length, 0);
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Avtalefrist: ingen hardkodet tekst (pseudo-locale)", async () => {
  const PL = Object.fromEntries(Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = { "/v1/plikt": PLIKTER };
    const h = nyHoved();
    visAvtalefrist(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 3);
    const tekst = h.textContent;
    for (const ekte of ["Avtaler og frister", "Forfalt", "Eier", "Kilde",
                        "Kvitter ut", "Marker bortfalt", "Åpen", "Lukket",
                        "Kvartalsvis"]) {
      assert.ok(!tekst.includes(ekte),
        `hardkodet norsk tekst i flaten: «${ekte}»`);
    }
    assert.ok(tekst.includes("PL_ui.avtalefrist.tittel"));
    assert.ok(tekst.includes("PL_ui.avtalefrist.merke_forfalt"));
  } finally {
    settI18nForTest(NB, "nb");
  }
});
