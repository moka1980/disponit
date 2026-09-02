// M-34 kontrollflaten (100) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd, på hver
//     av skjermene flaten kan stå i (liste, tom liste, dialog åpen).
//   * FORBIGÅTT ER TEKST, ikke bare farge (WCAG 1.4.1) — og antall døgn
//     står som ord. Det er flatens viktigste jobb.
//   * EVIDENSEN ER EN EGEN KOLONNE, med henvisning OG dato. En kontroll
//     uten evidens sier det som en setning, ikke som en tom celle.
//   * ETTERPRØVINGSDIALOGEN KREVER EVIDENSHENVISNING OG DATO, og
//     hjelpeteksten sier HVORFOR. Et tomt felt sender ingenting.
//   * ET AVVIK KREVER EN BESKRIVELSE.
//   * MODULEN SENDER INGENTING INN: ingen kontroll på flaten kaller noe
//     som ligner en innsendingsvei, og kilden bærer ingen.
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
import { visCompliance } from "../static/js/flater/compliance.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));

const KONTROLLER = {
  kontroller: [
    { kontroll_id: "11111111-1111-1111-1111-111111111111",
      rammeverk: "ISO 27001", rammeverk_versjon: "2022",
      krav_ref: "A.8.16", beskrivelse: "Overvåking av aktiviteter i drift",
      eier_bruker_id: "bid_a", eier_navn: "Kari Nordmann", eier_aktiv: true,
      etterproving_dogn: 90, sist_etterprovd: "2026-05-02",
      evidens_ref: "SAK-2026-118", forfaller: "2026-07-31",
      dogn_over_frist: 41, status: "oppfylt",
      ikke_relevant_begrunnelse: null, antall_etterprovinger: 3,
      siste_utfall: "oppfylt", siste_avvik: null,
      apne_funn: ["etterproving_forbigatt"] },
    { kontroll_id: "22222222-2222-2222-2222-222222222222",
      rammeverk: "GDPR", rammeverk_versjon: null,
      krav_ref: "art. 32", beskrivelse: "Kryptering av persondata i ro",
      eier_bruker_id: "bid_b", eier_navn: null, eier_aktiv: false,
      etterproving_dogn: 365, sist_etterprovd: null, evidens_ref: null,
      forfaller: "2027-03-01", dogn_over_frist: -180,
      status: "ikke_oppfylt", ikke_relevant_begrunnelse: null,
      antall_etterprovinger: 0, siste_utfall: null, siste_avvik: null,
      apne_funn: ["kontroll_uten_eier"] },
    { kontroll_id: "33333333-3333-3333-3333-333333333333",
      rammeverk: "NIS2", rammeverk_versjon: null,
      krav_ref: "§ 21 nr. 2 f", beskrivelse: "Test av beredskapsplan",
      eier_bruker_id: "bid_a", eier_navn: "Kari Nordmann", eier_aktiv: true,
      etterproving_dogn: 180, sist_etterprovd: "2026-08-14",
      evidens_ref: "OEV-2026-2", forfaller: "2027-02-10",
      dogn_over_frist: -160, status: "ikke_oppfylt",
      ikke_relevant_begrunnelse: null, antall_etterprovinger: 1,
      siste_utfall: "avvik",
      siste_avvik: "To av fem kontakter svarte ikke innen fristen.",
      apne_funn: [] },
    { kontroll_id: "44444444-4444-4444-4444-444444444444",
      rammeverk: "ISO 27001", rammeverk_versjon: "2022",
      krav_ref: "A.7.4", beskrivelse: "Fysisk overvåking av lokaler",
      eier_bruker_id: "bid_a", eier_navn: "Kari Nordmann", eier_aktiv: true,
      etterproving_dogn: 365, sist_etterprovd: null, evidens_ref: null,
      forfaller: "2026-01-01", dogn_over_frist: 240,
      status: "ikke_relevant",
      ikke_relevant_begrunnelse: "Vi har ingen egne lokaler.",
      antall_etterprovinger: 0, siste_utfall: null, siste_avvik: null,
      apne_funn: [] },
  ],
  request_id: "r-c",
};

let SVAR;
let SISTE;
let KALL;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  KALL.push({ sti, metode: (opts && opts.method) || "GET" });
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

function ctx(scopes = ["security:read", "bestilling:opprett"]) {
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
  KALL = [];
  return m;
}

test("Compliance: forbigått som TEKST med antall døgn, axe rent",
  async () => {
    SVAR = { "/v1/compliance": KONTROLLER };
    SISTE = null;
    const h = nyHoved();
    visCompliance(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 4);

    const tb = h.querySelector("table");
    assert.ok(tb.querySelector("caption").textContent.trim(),
      "tabellen mangler caption");
    assert.ok(tb.querySelector('th[scope="col"]'));
    for (const rad of tb.querySelectorAll("tbody tr")) {
      // Tabellsemantikk i BEGGE retninger: uten th scope="row" mister en
      // skjermleser i frist- og evidenskolonnene hvilken kontroll raden
      // gjelder.
      assert.equal(rad.cells[0].tagName, "TH");
      assert.equal(rad.cells[0].getAttribute("scope"), "row");
    }

    const tekst = h.textContent;
    // FLATENS VIKTIGSTE JOBB: hvor mange døgn over fristen, som ORD.
    assert.ok(tekst.includes(
      t("ui.compliance.forbigatt_for").replace("{dogn}", "41")),
    "antall døgn over fristen står ikke som tekst");
    assert.ok(tekst.includes(t("ui.compliance.merke_forbigatt")));
    assert.ok(tekst.includes(
      t("ui.compliance.om_dogn").replace("{dogn}", "180")));
    // ENTALLET HAR SIN EGEN SETNING på begge språk: «by 1 days» ville
    // stått på nøyaktig den raden et menneske leser først.
    for (const sett of ["nb", "en"]) {
      const tekster = JSON.parse(readFileSync(
        join(HER, "..", "..", "..", "..", "locales", `${sett}.json`),
        "utf-8"));
      for (const n of ["ui.compliance.om_ett_dogn",
                       "ui.compliance.forbigatt_ett_dogn"]) {
        assert.ok(tekster[n] && !tekster[n].includes("{dogn}"),
          `${sett}.json: ${n} mangler eller bærer et telleplassholder`);
      }
    }
    // …og den IKKE-RELEVANTE kontrollen med gammel frist er IKKE
    // forbigått: den er en skreven beslutning. Nøyaktig ett
    // forbigått-merke på skjermen.
    assert.equal(
      [...h.querySelectorAll("strong")].filter(
        (e) => e.textContent === t("ui.compliance.merke_forbigatt")).length,
      1, "en ikke-relevant kontroll ble merket forbigått");
    // OG SETNINGEN VED SIDEN AV MERKET SIER DET SAMME. Merket ble holdt
    // borte av `erForbigatt`, men fristcellen regnet videre og sa
    // «forbigått for 240 døgn» om nøyaktig den beslutningen — to svar på
    // samme spørsmål i samme rad. `m34_kontrollbilde` regner
    // `dogn_over_frist` for HVER rad, så tallet finnes; det er flaten som
    // skal la være å lese det.
    assert.ok(!tekst.includes(
      t("ui.compliance.forbigatt_for").replace("{dogn}", "240")),
      "en ikke-relevant kontroll fikk fristcellen sin regnet som forbigått");
    assert.ok(tekst.includes("Vi har ingen egne lokaler."),
      "begrunnelsen for ikke-relevant vises ikke");

    // EVIDENSEN ER EN EGEN KOLONNE: henvisning OG dato — og den ærlige
    // setningen der ingen finnes.
    assert.ok(tekst.includes("SAK-2026-118"), "evidenshenvisningen vises ikke");
    assert.ok(tekst.includes("2026-05-02"), "evidensdatoen vises ikke");
    assert.ok(tekst.includes(t("ui.compliance.ingen_evidens")),
      "en kontroll uten evidens har en tom celle i stedet for en setning");

    // Eieren som har sluttet står som ORD, ikke som en blek celle.
    assert.ok(tekst.includes(t("ui.compliance.merke_uten_eier")));
    assert.ok(tekst.includes("Kari Nordmann"), "eiernavnet vises ikke");
    assert.ok(tekst.includes("bid_b"),
      "eier uten visningsnavn faller ut — en tom celle finner ingen");

    // Sammendraget sier antallet som en setning, ikke som et tall i en
    // boks: «2 av 4 kontroller er forbi sin etterprøvingsfrist».
    assert.ok(tekst.includes(t("ui.compliance.sammendrag")
      .replace("{forbigatt}", "1").replace("{antall}", "4")));

    // Avviket fra siste etterprøving er lesbart, ikke gjemt i en farge.
    assert.ok(tekst.includes("To av fem kontakter svarte ikke"));

    // `.tablewrap` er sidescrollens container.
    assert.ok(h.querySelector(".tablewrap table"),
      "tabellen mangler .tablewrap og klemmer kolonnene");
    // `celle-tekst`/`celle-id` MÅ stå på cellen, ikke på et inline-barn:
    // `max-width` gjør ingenting på et <span>.
    for (const e of h.querySelectorAll(".celle-tekst, .celle-id")) {
      assert.ok(["TD", "TH", "DIV", "P"].includes(e.tagName),
        `${e.tagName} bærer celle-klassen — max-width virker ikke der`);
    }

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Compliance: lesende økt ser registeret, men ingen mutasjonskontroller",
  async () => {
    SVAR = { "/v1/compliance": KONTROLLER };
    const h = nyHoved();
    visCompliance(h, ctx(["security:read"]));
    await vent(() => h.querySelectorAll("table tbody tr").length === 4);
    // Hvilke kontroller som er forbigått skal ops-økten kunne SE; å
    // registrere en etterprøving er administratorens.
    assert.equal(h.querySelectorAll("form").length, 0);
    assert.equal(h.querySelectorAll("button").length, 0);
    assert.ok(h.textContent.includes("A.8.16"));
    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Compliance: etterprøvingen KREVER evidenshenvisning og dato",
  async () => {
    SVAR = { "/v1/compliance": KONTROLLER };
    SISTE = null;
    const h = nyHoved();
    visCompliance(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 4);

    const rad = h.querySelector("table tbody tr");
    rad.querySelectorAll("button")[0].dispatchEvent(
      new window.Event("click", { bubbles: true }));

    const evidens = h.querySelector("#etterproving-evidens");
    assert.ok(evidens, "dialogen åpnet ikke");
    // FELTENE ER PÅKREVDE i markeringen, og etikettene er knyttet til dem.
    assert.equal(evidens.getAttribute("required"), "");
    assert.equal(h.querySelector("#etterproving-dato")
      .getAttribute("required"), "");
    for (const id of ["etterproving-dato", "etterproving-utforer",
                      "etterproving-evidens", "etterproving-utfall"]) {
      assert.ok(h.querySelector(`label[for="${id}"]`), `${id} mangler label`);
    }
    // Hjelpeteksten er BEGRUNNELSEN, ikke en gjentakelse av etiketten.
    assert.ok(h.textContent.includes(
      t("ui.compliance.dialog.evidenshjelp")));

    // Tomt felt → ingenting sendes, og feilteksten sier HVORFOR.
    const skjema = evidens.closest("form");
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    await vent(() => h.textContent.includes(
      t("ui.compliance.feil.evidens_kreves")));
    assert.equal(SISTE, null, "et tomt felt sendte likevel et kall");
    assert.ok(h.querySelector('[role="alert"]'),
      "feilteksten er ikke annonsert");

    // …og med henvisning og dato går den gjennom, på kontrollen som ble
    // valgt.
    h.querySelector("#etterproving-dato").value = "2026-09-01";
    h.querySelector("#etterproving-utforer").value = "bid_a";
    evidens.value = "SAK-2026-931";
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    await vent(() => SISTE !== null);
    assert.equal(SISTE.sti,
      "/v1/compliance/kontroll/11111111-1111-1111-1111-111111111111"
      + "/etterproving");
    assert.equal(SISTE.kropp.evidens_ref, "SAK-2026-931");
    assert.equal(SISTE.kropp.utfort, "2026-09-01");
    assert.equal(SISTE.kropp.utfall, "oppfylt");
    // SP-2: kallet bærer en idempotensnøkkel — en tapt respons + nytt
    // klikk skal gjenspille, ikke bokføre etterprøvingen to ganger.
    assert.ok(SISTE.headers["Idempotency-Key"]);

    // BEKREFTELSEN NÅR FRAM, og den ligger UTENFOR det som skjules: lå
    // live-regionen inne i dialogen, ble kvitteringen både usynlig og
    // uannonsert i nøyaktig det øyeblikket den hadde noe å si.
    await vent(() => document.body.textContent.includes(
      t("ui.compliance.dialog.ok")));
    const meldinger = [...document.body.querySelectorAll(
      '[aria-live="polite"]')].filter(
      (e) => e.textContent.includes(t("ui.compliance.dialog.ok")));
    assert.ok(meldinger.length, "bekreftelsen står ikke i en live-region");
    for (const m of meldinger) {
      for (let n = m; n; n = n.parentElement) {
        assert.ok(!n.hidden, "bekreftelsen ligger inne i et skjult element");
      }
    }
    assert.ok(h.querySelector("#etterproving-evidens").closest("[hidden]"),
      "dialogen ble ikke lukket etter en vellykket etterprøving");

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Compliance: et avvik krever en beskrivelse", async () => {
  SVAR = { "/v1/compliance": KONTROLLER };
  SISTE = null;
  const h = nyHoved();
  visCompliance(h, ctx());
  await vent(() => h.querySelectorAll("table tbody tr").length === 4);
  h.querySelector("table tbody tr").querySelectorAll("button")[0]
    .dispatchEvent(new window.Event("click", { bubbles: true }));

  const avvik = h.querySelector("#etterproving-avvik");
  // Avviksfeltet finnes bare når utfallet er `avvik`: et alltid synlig
  // felt som bare noen ganger er påkrevd er en felle.
  assert.ok(avvik.closest("[hidden]"),
    "avviksfeltet står synlig på et utfall som ikke har avvik");
  const valg = h.querySelector("#etterproving-utfall");
  valg.value = "avvik";
  valg.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(!avvik.closest("[hidden]"), "avviksfeltet kom aldri fram");
  assert.equal(avvik.getAttribute("required"), "");

  h.querySelector("#etterproving-dato").value = "2026-09-01";
  h.querySelector("#etterproving-utforer").value = "bid_a";
  h.querySelector("#etterproving-evidens").value = "SAK-1";
  const skjema = avvik.closest("form");
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  await vent(() => h.textContent.includes(
    t("ui.compliance.feil.avvik_kreves")));
  assert.equal(SISTE, null, "et avvik uten beskrivelse ble sendt");

  avvik.value = "To av fem kontakter svarte ikke.";
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  await vent(() => SISTE !== null);
  assert.equal(SISTE.kropp.utfall, "avvik");
  assert.equal(SISTE.kropp.avviksbeskrivelse,
    "To av fem kontakter svarte ikke.");
});

test("Compliance: «ikke relevant» krever begrunnelsen, med sin egen tekst",
  async () => {
    SVAR = { "/v1/compliance": KONTROLLER };
    SISTE = null;
    const h = nyHoved();
    visCompliance(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 4);

    const rader = [...h.querySelectorAll("table tbody tr")];
    // Knappen finnes IKKE på den som alt står slik: døren avviser den,
    // og en knapp som alltid feiler er en løgn om hva systemet kan.
    assert.equal(rader[3].querySelectorAll("button").length, 1,
      "en alt ikke-relevant kontroll tilbyr å bli det en gang til");
    rader[0].querySelectorAll("button")[1].dispatchEvent(
      new window.Event("click", { bubbles: true }));

    const felt = h.querySelector("#kontroll-begrunnelse");
    assert.ok(felt, "dialogen åpnet ikke");
    assert.equal(felt.getAttribute("required"), "");
    assert.ok(h.querySelector('label[for="kontroll-begrunnelse"]'));
    assert.ok(h.textContent.includes(
      t("ui.compliance.dialog.begrunnelsehjelp")));

    const skjema = felt.closest("form");
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    await vent(() => h.textContent.includes(
      t("ui.compliance.feil.begrunnelse_kreves")));
    assert.equal(SISTE, null);

    felt.value = "Vi behandler ingen slike data.";
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    await vent(() => SISTE !== null);
    assert.equal(SISTE.sti,
      "/v1/compliance/kontroll/11111111-1111-1111-1111-111111111111"
      + "/ikke-relevant");
    assert.equal(SISTE.kropp.begrunnelse, "Vi behandler ingen slike data.");
  });

test("Compliance: registreringen krever eier og intervall eksplisitt",
  async () => {
    SVAR = { "/v1/compliance": KONTROLLER };
    SISTE = null;
    const h = nyHoved();
    visCompliance(h, ctx());
    await vent(() => h.querySelector("#kontroll-eier") !== null);
    const eier = h.querySelector("#kontroll-eier");
    // EIEREN ER PÅKREVD OG TOM. En flate som forhåndsutfylte innloggeren
    // ville gjort «kontroller uten eier» sann på papiret og falsk i
    // praksis.
    assert.equal(eier.getAttribute("required"), "");
    assert.equal(eier.value, "");
    assert.ok(h.textContent.includes(t("ui.compliance.skjema.eierhjelp")));
    for (const id of ["kontroll-rammeverk", "kontroll-krav",
                      "kontroll-beskrivelse", "kontroll-dogn"]) {
      assert.equal(h.querySelector(`#${id}`).getAttribute("required"), "");
      assert.ok(h.querySelector(`label[for="${id}"]`), `${id} mangler label`);
    }
    // Versjonen er det ENE valgfrie feltet, og etiketten sier det.
    assert.equal(h.querySelector("#kontroll-versjon")
      .getAttribute("required"), null);
    // Skjemaet sier hva en ny kontroll står som, og hvorfor.
    assert.ok(h.textContent.includes(t("ui.compliance.skjema.starthjelp")));

    h.querySelector("#kontroll-rammeverk").value = "ISO 27001";
    h.querySelector("#kontroll-versjon").value = "2022";
    h.querySelector("#kontroll-krav").value = "A.5.1";
    h.querySelector("#kontroll-beskrivelse").value = "Policy for infosikkerhet";
    eier.value = "bid_c";
    h.querySelector("#kontroll-dogn").value = "365";
    h.querySelector("#kontroll-krav").closest("form").dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }));
    await vent(() => SISTE !== null);
    assert.equal(SISTE.sti, "/v1/compliance/kontroll");
    assert.equal(SISTE.kropp.eier_bruker_id, "bid_c");
    assert.equal(SISTE.kropp.rammeverk, "ISO 27001");
    assert.equal(SISTE.kropp.rammeverk_versjon, "2022");
    // Intervallet sendes som TALL, ikke som teksten fra et number-felt:
    // dørens CHECK er `> 0`, og «"365"» ville blitt 400 i stedet.
    assert.equal(SISTE.kropp.etterproving_dogn, 365);
  });

test("Compliance: flaten har ingen innsendingsvei (v1-dommen)", async () => {
  // 🔴 KATALOGTEKSTEN LOVER INNSENDING TIL SERTIFISERINGSORGAN. v1
  // registrerer kontrollen. Fraværet måles på TO måter, fordi én ville
  // vært for lite:
  //
  //   1. KILDEN: ingen adresse, ingen ekstern URL, ingen ord som ville
  //      vært det første et forsøk skrev.
  //   2. SKJERMEN: hvert eneste kall flaten gjør går til `/v1/compliance`
  //      og ingen andre steder — heller ikke etter at hver knapp på
  //      flaten er trykket.
  //
  // Kommentarene STRIPPES først, og det er ikke en oppmykning: flaten
  // FORKLARER fraværet i prosa («ingen innsendingsvei»), og en port som
  // ikke skilte kode fra kommentar ville felt nettopp den setningen som
  // gjør dommen lesbar for neste utvikler. Det som måles er MEKANISMEN.
  const kilde = readFileSync(new URL(
    "../static/js/flater/compliance.js", import.meta.url), "utf8")
    .split("\n").filter((l) => !l.trim().startsWith("//")).join("\n");
  for (const mekanisme of ["http://", "https://", "XMLHttpRequest",
                           "WebSocket", "sendBeacon", "new Image(",
                           "fetch("]) {
    assert.ok(!kilde.includes(mekanisme),
      `flaten bærer «${mekanisme}» — v1 sender ingenting inn`);
  }

  SVAR = { "/v1/compliance": KONTROLLER };
  SISTE = null;
  const h = nyHoved();
  visCompliance(h, ctx());
  await vent(() => h.querySelectorAll("table tbody tr").length === 4);
  for (const knapp of [...h.querySelectorAll("button")]) {
    knapp.dispatchEvent(new window.Event("click", { bubbles: true }));
  }
  await vent(() => false, 5);
  const utenfor = KALL.filter((k) => !k.sti.startsWith("/v1/compliance"));
  assert.deepEqual(utenfor, [],
    "flaten kalte noe utenfor sitt eget register");
  // …og undertittelen SIER at registeret ikke sender noe, så ingen leter
  // etter en knapp som ikke finnes.
  assert.ok(h.textContent.includes(t("ui.compliance.undertittel")));
});

test("Compliance: tomt register sier hva fraværet betyr", async () => {
  SVAR = { "/v1/compliance": { kontroller: [], request_id: "r" } };
  const h = nyHoved();
  visCompliance(h, ctx());
  await vent(() => h.textContent.includes(t("ui.compliance.liste.ingen")));
  // ÆRLIG TOMTILSTAND: et tomt kontrollregister er ikke «vi har ingen
  // krav».
  assert.equal(h.querySelectorAll("table").length, 0);
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Compliance: ingen hardkodet tekst (pseudo-locale)", async () => {
  const PL = Object.fromEntries(Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = { "/v1/compliance": KONTROLLER };
    const h = nyHoved();
    visCompliance(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 4);
    const tekst = h.textContent;
    for (const ekte of ["Kontroller og etterlevelse", "Forbigått", "Eier",
                        "Evidens", "Registrer etterprøving",
                        "Marker ikke relevant", "Oppfylt", "Ikke relevant"]) {
      assert.ok(!tekst.includes(ekte),
        `hardkodet norsk tekst i flaten: «${ekte}»`);
    }
    assert.ok(tekst.includes("PL_ui.compliance.tittel"));
    assert.ok(tekst.includes("PL_ui.compliance.merke_forbigatt"));
  } finally {
    settI18nForTest(NB, "nb");
  }
});
