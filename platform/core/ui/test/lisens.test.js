// M-22 SaaS- og lisensflaten (098) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd, på hver
//     av de fire skjermene flaten kan stå i (liste, tom liste,
//     fornyelsesdialog, avslutningsdialog).
//   * BESLUTNINGSDATOEN ER SYNLIG VED SIDEN AV FORNYELSEN, og
//     oppsigelsesfristen står som ord. Det er hele grunnen til at
//     modulen finnes: en tabell som bare viste fornyelsen ville gjentatt
//     feilen den skal hindre.
//   * FRISTEN ER UTE er TEKST, ikke bare farge (WCAG 1.4.1).
//   * MODULEN SIER IKKE OPP NOE — avslutningsdialogens hjelpetekst sier
//     eksplisitt at handlingen ikke rører leverandøren, og flaten har
//     ingen knapp som lover noe annet.
//   * Avslutningen krever begrunnelsen, fornyelsen krever datoen, og et
//     tomt felt sender ingenting.
//   * En lesende økt ser registeret, men INGEN mutasjonskontroller.
//   * Ingen hardkodet tekst (pseudo-locale).
//
// Ingen delt fixture (m16/m21-formen): hver test bygger sin egen skjerm.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visLisens } from "../static/js/flater/lisens.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));

const LISENSER = {
  lisenser: [
    // 90 døgns oppsigelsesfrist, fornyelse et halvår fram — og likevel
    // PASSERT beslutningspunkt. Nøyaktig raden modulen finnes for.
    { lisens_id: "11111111-1111-1111-1111-111111111111",
      leverandor: "Nordvind AS", produkt: "Prosjektrom",
      eier_bruker_id: "bid_a", eier_navn: "Kari Nordmann",
      antall_seter: 25, kostnad_aar: "120000.00", valuta: "NOK",
      fornyelsesdato: "2026-11-30", oppsigelsesfrist_dogn: 90,
      beslutningsdato: "2026-09-01", dogn_til_beslutning: -12,
      fornyelsestype: "automatisk", kilde: "AVT-2026-7", status: "aktiv",
      avslutt_begrunnelse: null, avsluttet: null, avsluttet_av: null },
    // Ingen frist avtalt: beslutningsdato = fornyelsesdato.
    { lisens_id: "22222222-2222-2222-2222-222222222222",
      leverandor: "Fjellstad IT", produkt: "Regnskapsmodul",
      eier_bruker_id: "bid_b", eier_navn: null,
      antall_seter: null, kostnad_aar: null, valuta: null,
      fornyelsesdato: "2027-06-30", oppsigelsesfrist_dogn: null,
      beslutningsdato: "2027-06-30", dogn_til_beslutning: 300,
      fornyelsestype: "manuell", kilde: "ORD-99812", status: "aktiv",
      avslutt_begrunnelse: null, avsluttet: null, avsluttet_av: null },
    { lisens_id: "33333333-3333-3333-3333-333333333333",
      leverandor: "Havbris Software", produkt: "Signeringstjeneste",
      eier_bruker_id: "bid_a", eier_navn: "Kari Nordmann",
      antall_seter: 5, kostnad_aar: "9800.50", valuta: "EUR",
      fornyelsesdato: "2026-05-01", oppsigelsesfrist_dogn: 30,
      beslutningsdato: "2026-04-01", dogn_til_beslutning: -150,
      fornyelsestype: "engang", kilde: "FAK-2025-4412",
      status: "avsluttet",
      avslutt_begrunnelse: "Erstattet av plattformens egen signering.",
      avsluttet: "2026-03-20T09:00:00+00:00", avsluttet_av: "bid_a" },
  ],
  request_id: "r-l",
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

test("Lisens: beslutningsdato OG fornyelse, fristen som ord, axe rent",
  async () => {
    SVAR = { "/v1/lisens": LISENSER };
    SISTE = null;
    const h = nyHoved();
    visLisens(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 3);

    const tb = h.querySelector("table");
    assert.ok(tb.querySelector("caption").textContent.trim(),
      "tabellen mangler caption");
    assert.ok(tb.querySelector('th[scope="col"]'));
    for (const rad of tb.querySelectorAll("tbody tr")) {
      // Tabellsemantikk i BEGGE retninger: uten th scope="row" mister en
      // skjermleser i frist- og kostnadskolonnene hvilken lisens raden
      // gjelder.
      assert.equal(rad.cells[0].tagName, "TH");
      assert.equal(rad.cells[0].getAttribute("scope"), "row");
    }
    // LÆRDOM 1 og 2: wrapperen rundt tabellen, og `.celle-tekst` på en
    // TD/TH — `max-width` gjør ingenting på et inline-element.
    assert.ok(tb.closest(".tablewrap"),
      "tabellen mangler .tablewrap — kolonnene klemmes mot min-content");
    for (const e of h.querySelectorAll(".celle-tekst")) {
      assert.ok(["TD", "TH", "DIV"].includes(e.tagName),
        `.celle-tekst på ${e.tagName} gjør ingenting`);
    }

    const tekst = h.textContent;
    // EIER, KILDE OG KOSTNAD ER SYNLIGE. Det er hele grunnen til at v1 er
    // en liste og ikke et kostnadsdashbord.
    assert.ok(tekst.includes("Kari Nordmann"), "eiernavnet vises ikke");
    assert.ok(tekst.includes("bid_b"),
      "eier uten visningsnavn faller ut — en tom celle finner ingen");
    assert.ok(tekst.includes("AVT-2026-7"), "kilden vises ikke");
    assert.ok(tekst.includes("Nordvind AS"), "leverandøren vises ikke");
    assert.ok(/120\s?000/.test(tekst.replace(/ /g, " ")),
      "kostnaden vises ikke");
    assert.ok(tekst.includes("NOK"));

    // MODULENS POENG, målt: begge datoene står, og oppsigelsesfristen
    // står som ord. En flate som bare viste fornyelsen ville sagt at
    // valget kunne tas 30. november — det kunne det ikke.
    const tider = [...h.querySelectorAll("time")].map(
      (e) => e.getAttribute("datetime"));
    assert.ok(tider.includes("2026-09-01"), "beslutningsdatoen vises ikke");
    assert.ok(tider.includes("2026-11-30"), "fornyelsesdatoen vises ikke");
    assert.ok(tekst.includes(
      t("ui.lisens.frist_dogn").replace("{dogn}", "90")),
    "oppsigelsesfristen står ikke som ord");
    // …og NULL er ikke null døgn: «ingen frist avtalt» er sin egen
    // setning, fordi de to betyr helt forskjellige ting.
    assert.ok(tekst.includes(t("ui.lisens.frist_ingen")));

    // FRISTEN ER UTE SOM TEKST, aldri bare farge.
    assert.ok(tekst.includes(t("ui.lisens.merke_utlopt")));
    assert.ok(tekst.includes(
      t("ui.lisens.utlopt_for").replace("{dogn}", "12")));
    assert.ok(tekst.includes(
      t("ui.lisens.om_dogn").replace("{dogn}", "300")));
    // ENTALLET HAR SIN EGEN SETNING på begge språk: «in 1 days» ville
    // stått på nøyaktig den raden som må besluttes i morgen.
    for (const sett of ["nb", "en"]) {
      const tekster = JSON.parse(readFileSync(
        join(HER, "..", "..", "..", "..", "locales", `${sett}.json`),
        "utf-8"));
      for (const n of ["ui.lisens.om_ett_dogn",
                       "ui.lisens.utlopt_for_ett_dogn",
                       "ui.lisens.frist_ett_dogn"]) {
        assert.ok(tekster[n] && !tekster[n].includes("{dogn}"),
          `${sett}.json: ${n} mangler eller bærer et telleplassholder`);
      }
      // LÆRDOM 7: lenketeksten er flatetittelen, i BEGGE språk.
      assert.equal(tekster["ui.nav.lisens"], tekster["ui.lisens.tittel"],
        `${sett}.json: menyoppføringen og flatetittelen er ikke samme streng`);
    }
    // …og den AVSLUTTEDE lisensen med gammel dato er IKKE merket: den er
    // avgjort. Nøyaktig to merker på skjermen ville vært feil — det er
    // ett.
    assert.equal(
      [...h.querySelectorAll("strong")].filter(
        (e) => e.textContent === t("ui.lisens.merke_utlopt")).length,
      1, "en avsluttet lisens med gammel dato ble merket utløpt");

    // Tilstanden står som ord for begge statusene flaten kan møte.
    assert.ok(tekst.includes(t("ui.lisens.status.aktiv")));
    assert.ok(tekst.includes(t("ui.lisens.status.avsluttet")));

    // Handlingsknapper KUN på de aktive lisensene: dørene avviser en
    // lisens som alt er avsluttet, og en knapp som alltid feiler er en
    // løgn.
    const rader = [...tb.querySelectorAll("tbody tr")];
    assert.equal(rader[0].querySelectorAll("button").length, 2);
    assert.equal(rader[2].querySelectorAll("button").length, 0);

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Lisens: lesende økt ser registeret, men ingen mutasjonskontroller",
  async () => {
    SVAR = { "/v1/lisens": LISENSER };
    const h = nyHoved();
    visLisens(h, ctx(["decisions:read"]));
    await vent(() => h.querySelectorAll("table tbody tr").length === 3);
    // Hva virksomheten betaler for og når valget må tas er ikke
    // administratorens hemmelighet — men å endre det er hennes.
    assert.equal(h.querySelectorAll("form").length, 0);
    assert.equal(h.querySelectorAll("button").length, 0);
    assert.ok(h.textContent.includes("Prosjektrom"));
    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Lisens: avslutningen KREVER begrunnelsen, og sier at den ikke sier opp",
  async () => {
    SVAR = { "/v1/lisens": LISENSER };
    SISTE = null;
    const h = nyHoved();
    visLisens(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 3);

    const rad = h.querySelector("table tbody tr");
    rad.querySelectorAll("button")[1].dispatchEvent(
      new window.Event("click", { bubbles: true }));

    const felt = h.querySelector("#lisens-dialogfelt");
    assert.ok(felt, "dialogen åpnet ikke");
    assert.equal(felt.getAttribute("name"), "begrunnelse");
    assert.equal(felt.getAttribute("required"), "");
    assert.ok(h.querySelector('label[for="lisens-dialogfelt"]'));
    // V1-DOMMEN, PÅ FLATEN: hjelpeteksten sier eksplisitt at handlingen
    // IKKE rører leverandøren. En flate som lot brukeren tro at Disponit
    // sa opp abonnementet ville vært en løgn om hva systemet gjør — og
    // nøyaktig den løgnen katalogens guard ber oss unngå.
    assert.ok(h.textContent.includes(
      t("ui.lisens.dialog.begrunnelsehjelp")));

    // Tomt felt → ingenting sendes, og feilteksten sier HVORFOR.
    const skjema = felt.closest("form");
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    await vent(() => h.textContent.includes(
      t("ui.lisens.feil.begrunnelse_kreves")));
    assert.equal(SISTE, null, "et tomt felt sendte likevel et kall");
    assert.ok(h.querySelector('[role="alert"]'),
      "feilteksten er ikke annonsert");

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));

    // …og med begrunnelsen går den gjennom, på lisensen som ble valgt.
    felt.value = "Verktøyet er erstattet av plattformens egen modul.";
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    await vent(() => SISTE !== null);
    assert.equal(SISTE.sti,
      "/v1/lisens/11111111-1111-1111-1111-111111111111/avslutt");
    assert.equal(SISTE.kropp.begrunnelse,
      "Verktøyet er erstattet av plattformens egen modul.");
    // SP-2: kallet bærer en idempotensnøkkel — en tapt respons + nytt
    // klikk skal gjenspille, ikke avslutte to ganger.
    assert.ok(SISTE.headers["Idempotency-Key"]);

    // BEKREFTELSEN NÅR FRAM, og den ligger UTENFOR det som skjules: lå
    // live-regionen inne i dialogen, ble meldingen både usynlig og
    // uannonsert i nøyaktig det øyeblikket den hadde noe å si.
    await vent(() => document.body.textContent.includes(
      t("ui.lisens.dialog.ok")));
    const meldinger = [...document.body.querySelectorAll(
      '[aria-live="polite"]')].filter(
      (e) => e.textContent.includes(t("ui.lisens.dialog.ok")));
    assert.ok(meldinger.length, "bekreftelsen står ikke i en live-region");
    for (const m of meldinger) {
      for (let n = m; n; n = n.parentElement) {
        assert.ok(!n.hidden, "bekreftelsen ligger inne i et skjult element");
      }
    }
  });

test("Lisens: fornyelsen er en DATO, ikke en tekst — og feltet er et annet",
  async () => {
    SVAR = { "/v1/lisens": LISENSER };
    SISTE = null;
    const h = nyHoved();
    visLisens(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 3);

    const rad = h.querySelector("table tbody tr");
    rad.querySelectorAll("button")[0].dispatchEvent(
      new window.Event("click", { bubbles: true }));
    const felt = h.querySelector("#lisens-dialogfelt");
    // TO KONTROLLER, ikke én med skiftende `type`: en `<input>` som
    // bytter type beholder verdien sin, og en dato som ble stående igjen
    // som begrunnelse er nøyaktig den feilen ingen ser før den står i
    // registeret.
    assert.equal(felt.getAttribute("type"), "date");
    assert.equal(felt.getAttribute("name"), "fornyelsesdato");
    assert.ok(h.textContent.includes(t("ui.lisens.dialog.fornyelsehjelp")));
    assert.ok(!h.textContent.includes(
      t("ui.lisens.dialog.begrunnelsehjelp")));

    const skjema = felt.closest("form");
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    await vent(() => h.textContent.includes(t("ui.lisens.feil.dato_kreves")));
    assert.equal(SISTE, null);

    felt.value = "2027-11-30";
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    await vent(() => SISTE !== null);
    assert.equal(SISTE.sti,
      "/v1/lisens/11111111-1111-1111-1111-111111111111/fornyelse");
    // DATOEN SENDES SOM DAG, uten klokkeslett: en fornyelsesdato er en
    // kalenderdag, og et påfunnet tidspunkt ville gjort samme frist til
    // to ulike for to kolleger.
    assert.equal(SISTE.kropp.fornyelsesdato, "2027-11-30");

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Lisens: registreringen krever eier og produkt eksplisitt", async () => {
  SVAR = { "/v1/lisens": LISENSER };
  SISTE = null;
  const h = nyHoved();
  visLisens(h, ctx());
  await vent(() => h.querySelector("#lisens-eier") !== null);
  const eier = h.querySelector("#lisens-eier");
  // EIEREN ER PÅKREVD OG TOM. En flate som forhåndsutfylte innloggeren
  // ville gjort eierkolonnen sann på papiret og falsk i praksis — den som
  // kjøper er ofte ikke den som forvalter.
  assert.equal(eier.getAttribute("required"), "");
  assert.equal(eier.value, "");
  assert.ok(h.textContent.includes(t("ui.lisens.skjema.eierhjelp")));
  // LÆRDOM 4: én `.felt`-div per felt, med etikett og kontroll SAMMEN.
  for (const id of ["lisens-produkt", "lisens-leverandor", "lisens-eier",
                    "lisens-kilde", "lisens-fornyelsesdato"]) {
    assert.equal(h.querySelector(`#${id}`).getAttribute("required"), "");
    assert.ok(h.querySelector(`label[for="${id}"]`), `${id} mangler label`);
    assert.ok(h.querySelector(`#${id}`).closest(".felt"),
      `${id} står ikke i sin egen .felt-gruppe`);
  }
  assert.ok(h.querySelector("form.kv-skjema.kv-skjema-rutenett"),
    "registreringsskjemaet mangler rutenettklassen");
  assert.ok(h.querySelector(".skjema-bunn button[type=submit]"),
    "send-knappen står ikke i skjema-bunnen");
  // FRISTHJELPEN FORKLARER MODULENS POENG.
  assert.ok(h.textContent.includes(t("ui.lisens.skjema.fristhjelp")));

  h.querySelector("#lisens-produkt").value = "Tegneverktøy";
  h.querySelector("#lisens-leverandor").value = "Blåfjell Design";
  eier.value = "bid_c";
  h.querySelector("#lisens-kilde").value = "AVT-2027-3";
  h.querySelector("#lisens-fornyelsesdato").value = "2027-05-31";
  h.querySelector("#lisens-frist").value = "60";
  h.querySelector("#lisens-seter").value = "12";
  h.querySelector("#lisens-kostnad").value = "48000";
  h.querySelector("#lisens-valuta").value = "NOK";
  h.querySelector("#lisens-fornyelsestype").value = "manuell";
  h.querySelector("#lisens-produkt").closest("form").dispatchEvent(
    new window.Event("submit", { bubbles: true, cancelable: true }));
  await vent(() => SISTE !== null);
  assert.equal(SISTE.sti, "/v1/lisens");
  assert.equal(SISTE.kropp.eier_bruker_id, "bid_c");
  assert.equal(SISTE.kropp.fornyelsesdato, "2027-05-31");
  assert.equal(SISTE.kropp.oppsigelsesfrist_dogn, 60);
  assert.equal(SISTE.kropp.antall_seter, 12);
  // KOSTNADEN SENDES SOM STRENG: NUMERIC(14,2) er eksakt, og en
  // JSON-flyttall hører ikke hjemme i et kostnadsregister.
  assert.equal(SISTE.kropp.kostnad_aar, "48000");
  assert.equal(SISTE.kropp.valuta, "NOK");
  assert.equal(SISTE.kropp.fornyelsestype, "manuell");
});

test("Lisens: tomme valgfrie felt sendes IKKE som null eller 0", async () => {
  SVAR = { "/v1/lisens": LISENSER };
  SISTE = null;
  const h = nyHoved();
  visLisens(h, ctx());
  await vent(() => h.querySelector("#lisens-eier") !== null);
  h.querySelector("#lisens-produkt").value = "Minimalt";
  h.querySelector("#lisens-leverandor").value = "Enkel AS";
  h.querySelector("#lisens-eier").value = "bid_d";
  h.querySelector("#lisens-kilde").value = "ORD-1";
  h.querySelector("#lisens-fornyelsesdato").value = "2027-01-01";
  h.querySelector("#lisens-produkt").closest("form").dispatchEvent(
    new window.Event("submit", { bubbles: true, cancelable: true }));
  await vent(() => SISTE !== null);
  // NULL ER «VI VET IKKE», 0 er «det er gratis» og «kan sies opp samme
  // dag». De to er ikke det samme, og flaten skal ikke gjette.
  assert.ok(!("oppsigelsesfrist_dogn" in SISTE.kropp));
  assert.ok(!("antall_seter" in SISTE.kropp));
  assert.ok(!("kostnad_aar" in SISTE.kropp));
  assert.ok(!("valuta" in SISTE.kropp));
});

test("Lisens: tomt register sier hva fraværet betyr", async () => {
  SVAR = { "/v1/lisens": { lisenser: [], request_id: "r" } };
  const h = nyHoved();
  visLisens(h, ctx());
  await vent(() => h.textContent.includes(t("ui.lisens.liste.ingen")));
  // ÆRLIG TOMTILSTAND: et tomt register er ikke «vi betaler ikke for
  // noe».
  assert.equal(h.querySelectorAll("table").length, 0);
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Lisens: ingen hardkodet tekst (pseudo-locale)", async () => {
  const PL = Object.fromEntries(Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = { "/v1/lisens": LISENSER };
    const h = nyHoved();
    visLisens(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 3);
    const tekst = h.textContent;
    for (const ekte of ["Lisenser og abonnementer", "Fristen er ute",
                        "Eier", "Kilde", "Registrer fornyelse",
                        "Marker avsluttet", "Aktiv", "Avsluttet",
                        "Fornyes automatisk"]) {
      assert.ok(!tekst.includes(ekte),
        `hardkodet norsk tekst i flaten: «${ekte}»`);
    }
    assert.ok(tekst.includes("PL_ui.lisens.tittel"));
    assert.ok(tekst.includes("PL_ui.lisens.merke_utlopt"));
  } finally {
    settI18nForTest(NB, "nb");
  }
});
