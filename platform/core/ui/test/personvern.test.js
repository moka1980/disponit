// M-30 personvernflaten (099) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd, på hver
//     av de fire skjermene flaten kan stå i (liste, tom liste, dialog
//     åpen, lesende økt).
//   * DAGER SOM GJENSTÅR er flatens viktigste tall, og det står som ord.
//   * OVERSITTET ER TEKST, ikke bare farge (WCAG 1.4.1).
//   * LAGRENE SAKEN DEKKER er en egen kolonne — det er koblingen mot
//     M-4, og uten den kan ingen etterprøve om svaret var fullstendig.
//   * SVARDIALOGEN KREVER SVARHENVISNINGEN, og hjelpeteksten sier både
//     hvorfor og at registeret IKKE sletter noe.
//   * En lesende `security:read`-økt ser registeret, men INGEN
//     mutasjonskontroller.
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
import { visPersonvern } from "../static/js/flater/personvern.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));

// SUBJEKTREFERANSER, ALDRI NAVN. Testdataene bærer den formen registeret
// faktisk skal bære — et testsett med «Ola Nordmann» i `subjekt_ref`
// ville lært den neste leseren feil form.
const SAKER = {
  saker: [
    { sak_id: "11111111-1111-1111-1111-111111111111", type: "innsyn",
      subjekt_ref: "DSR-2026-0041", mottatt: "2026-07-01",
      frist: "2026-08-01", forlenget_til: null,
      forlengelse_begrunnelse: null, gjeldende_frist: "2026-08-01",
      dogn_til_frist: -12, eier_bruker_id: "bid_a", eier_navn: "Kari Nordmann",
      status: "apen", svar_ref: null, svar_ts: null,
      avvist_begrunnelse: null, lukket_av: null,
      lager_id: ["epost_melding", "kandidat_originaldokument"],
      apne_funn: ["frist_oversittet"] },
    { sak_id: "22222222-2222-2222-2222-222222222222", type: "sletting",
      subjekt_ref: "DSR-2026-0042", mottatt: "2026-08-10",
      frist: "2026-09-10", forlenget_til: "2026-11-10",
      forlengelse_begrunnelse: "Saken omfatter fire lagre og en ekstern part.",
      gjeldende_frist: "2026-11-10", dogn_til_frist: 300,
      eier_bruker_id: "bid_b", eier_navn: null, status: "apen",
      svar_ref: null, svar_ts: null, avvist_begrunnelse: null,
      lukket_av: null, lager_id: [], apne_funn: ["sak_uten_lagre"] },
    { sak_id: "33333333-3333-3333-3333-333333333333", type: "portabilitet",
      subjekt_ref: "DSR-2026-0033", mottatt: "2026-04-02",
      frist: "2026-05-02", forlenget_til: null,
      forlengelse_begrunnelse: null, gjeldende_frist: "2026-05-02",
      dogn_til_frist: -120, eier_bruker_id: "bid_a",
      eier_navn: "Kari Nordmann", status: "besvart",
      svar_ref: "ARK-2026-1188", svar_ts: "2026-04-28T09:00:00+00:00",
      avvist_begrunnelse: null, lukket_av: "bid_a",
      lager_id: ["epost_melding"], apne_funn: [] },
  ],
  request_id: "r-pv",
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
  return m;
}

test("Personvern: dagene som gjenstår, oversittet som TEKST, axe rent",
  async () => {
    SVAR = { "/v1/personvern": SAKER };
    SISTE = null;
    const h = nyHoved();
    visPersonvern(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 3);

    const tb = h.querySelector("table");
    assert.ok(tb.querySelector("caption").textContent.trim(),
      "tabellen mangler caption");
    assert.ok(tb.querySelector('th[scope="col"]'));
    for (const rad of tb.querySelectorAll("tbody tr")) {
      // Tabellsemantikk i BEGGE retninger: uten th scope="row" mister en
      // skjermleser i frist- og eierkolonnene hvilken sak raden gjelder.
      assert.equal(rad.cells[0].tagName, "TH");
      assert.equal(rad.cells[0].getAttribute("scope"), "row");
    }
    // …og wrapperen, som er sidescrollens container. Uten den klemmer
    // nettleseren kolonnene mot min-content — ett tegn per linje.
    assert.ok(tb.parentElement.classList.contains("tablewrap"));

    const tekst = h.textContent;
    // FLATENS VIKTIGSTE JOBB: hvor mange dager som gjenstår, som ord.
    assert.ok(tekst.includes(
      t("ui.personvern.oversittet_for").replace("{dogn}", "12")),
    "dagene siden fristen ble oversittet vises ikke");
    assert.ok(tekst.includes(
      t("ui.personvern.om_dogn").replace("{dogn}", "300")));
    // ENTALLET HAR SIN EGEN SETNING på begge språk: «1 days left» ville
    // stått på nøyaktig den raden som forfaller i morgen.
    for (const sett of ["nb", "en"]) {
      const tekster = JSON.parse(readFileSync(
        join(HER, "..", "..", "..", "..", "locales", `${sett}.json`),
        "utf-8"));
      for (const n of ["ui.personvern.om_ett_dogn",
                       "ui.personvern.oversittet_for_ett_dogn"]) {
        assert.ok(tekster[n] && !tekster[n].includes("{dogn}"),
          `${sett}.json: ${n} mangler eller bærer et telleplassholder`);
      }
    }

    // OVERSITTET SOM TEKST, aldri bare farge — og NØYAKTIG ÉN GANG: den
    // besvarte saken med gammel frist er ikke oversittet, den er gjort.
    assert.equal(
      [...h.querySelectorAll("strong")].filter(
        (e) => e.textContent === t("ui.personvern.merke_oversittet")).length,
      1, "en besvart sak med gammel frist ble merket oversittet");

    // FORLENGELSEN SKAL SYNES. En frist som stille hadde flyttet seg
    // ville skjult nøyaktig den handlingen art. 12 nr. 3 krever begrunnet
    // — og den viste fristen er den GJELDENDE, ikke den opprinnelige.
    assert.ok(tekst.includes(t("ui.personvern.forlenget")));
    const tider = [...h.querySelectorAll("time")].map(
      (e) => e.getAttribute("datetime"));
    assert.deepEqual(tider, ["2026-08-01", "2026-11-10", "2026-05-02"],
      "fristkolonnen viser ikke den GJELDENDE fristen");

    // LAGRENE SAKEN DEKKER — koblingen mot M-4, som egen kolonne.
    assert.ok(tekst.includes("epost_melding"), "lagerlisten vises ikke");
    assert.ok(tekst.includes("kandidat_originaldokument"));
    // …og en sak uten lagre sier det, framfor å ha en tom celle.
    assert.ok(tekst.includes(t("ui.personvern.ingen_lagre")));
    // Lange lager-id-er må kunne BRYTE: `.celle-id` bærer
    // `overflow-wrap: anywhere`, og uten den blir sidescrollen like bred
    // som den lengste strengen.
    const idceller = [...h.querySelectorAll("td.celle-id")];
    assert.equal(idceller.length, 3);

    // DE ÅPNE FUNNENE står på raden. Et funn ingen kan se er ikke et
    // funn — det er en rad.
    assert.ok(tekst.includes(t("ui.personvern.funn.frist_oversittet")));
    assert.ok(tekst.includes(t("ui.personvern.funn.sak_uten_lagre")));

    // Eier og rettighet som ord.
    assert.ok(tekst.includes("Kari Nordmann"), "eiernavnet vises ikke");
    assert.ok(tekst.includes("bid_b"),
      "eier uten visningsnavn faller ut — en tom celle finner ingen");
    assert.ok(tekst.includes(t("ui.personvern.type.innsyn")));
    assert.ok(tekst.includes(t("ui.personvern.type.sletting")));
    assert.ok(tekst.includes(t("ui.personvern.status.besvart")));

    // Handlingsknapper KUN på de åpne sakene: dørene avviser en sak som
    // alt er lukket, og en knapp som alltid feiler er en løgn.
    const rader = [...tb.querySelectorAll("tbody tr")];
    assert.equal(rader[0].querySelectorAll("button").length, 3);
    assert.equal(rader[2].querySelectorAll("button").length, 0);

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Personvern: lesende økt ser registeret, men ingen mutasjonskontroller",
  async () => {
    SVAR = { "/v1/personvern": SAKER };
    const h = nyHoved();
    visPersonvern(h, ctx(["security:read"]));
    await vent(() => h.querySelectorAll("table tbody tr").length === 3);
    // Å LESE hvilke frister som løper er tilsyn; å svare på vegne av
    // virksomheten er myndighet. `sikkerhet` har `security:read` og ikke
    // `bestilling:opprett`, og flaten speiler nøyaktig det.
    assert.equal(h.querySelectorAll("form").length, 0);
    assert.equal(h.querySelectorAll("button").length, 0);
    assert.ok(h.textContent.includes("DSR-2026-0041"));
    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Personvern: svardialogen KREVER svarhenvisningen, og sier hvorfor",
  async () => {
    SVAR = { "/v1/personvern": SAKER };
    SISTE = null;
    const h = nyHoved();
    visPersonvern(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 3);

    const rad = h.querySelector("table tbody tr");
    rad.querySelectorAll("button")[0].dispatchEvent(
      new window.Event("click", { bubbles: true }));

    const felt = h.querySelector("#pv-referanse");
    assert.ok(felt, "dialogen åpnet ikke");
    assert.equal(felt.getAttribute("required"), "");
    assert.ok(h.querySelector('label[for="pv-referanse"]'));
    // Hjelpeteksten er BEGRUNNELSEN, ikke en gjentakelse av etiketten —
    // og den sier i tillegg hva knappen IKKE gjør: registeret sletter
    // ingenting.
    assert.ok(h.textContent.includes(t("ui.personvern.dialog.svarhjelp")));
    // Datofeltet hører BARE forlengelsen til.
    assert.ok(h.querySelector("#pv-nyfrist").closest("[hidden]"),
      "datofeltet står synlig i svarformen");

    // Tomt felt → ingenting sendes, og feilteksten sier HVORFOR.
    const skjema = felt.closest("form");
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    await vent(() => h.textContent.includes(
      t("ui.personvern.feil.svar_kreves")));
    assert.equal(SISTE, null, "et tomt felt sendte likevel et kall");
    assert.ok(h.querySelector('[role="alert"]'),
      "feilteksten er ikke annonsert");

    // …og med referansen går den gjennom, på saken som ble valgt.
    felt.value = "ARK-2026-4711";
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    await vent(() => SISTE !== null);
    assert.equal(SISTE.sti,
      "/v1/personvern/11111111-1111-1111-1111-111111111111/svar");
    assert.equal(SISTE.kropp.svar_ref, "ARK-2026-4711");
    // SP-2: kallet bærer en idempotensnøkkel — en tapt respons + nytt
    // klikk skal gjenspille, ikke besvare to ganger.
    assert.ok(SISTE.headers["Idempotency-Key"]);

    // BEKREFTELSEN NÅR FRAM: den står i APPENS live-region (som
    // overlever at `last()` tegner flaten på nytt), og den lokale kopien
    // ligger UTENFOR det som skjules.
    await vent(() => document.body.textContent.includes(
      t("ui.personvern.dialog.ok")));
    const meldinger = [...document.body.querySelectorAll(
      '[aria-live="polite"]')].filter(
      (e) => e.textContent.includes(t("ui.personvern.dialog.ok")));
    assert.ok(meldinger.length, "bekreftelsen står ikke i en live-region");
    for (const m of meldinger) {
      for (let n = m; n; n = n.parentElement) {
        assert.ok(!n.hidden, "bekreftelsen ligger inne i et skjult element");
      }
    }
    assert.ok(h.querySelector("#pv-referanse").closest("[hidden]"),
      "dialogen ble ikke lukket etter et registrert svar");

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Personvern: avslaget har sin EGEN tekst, aldri svarets", async () => {
  SVAR = { "/v1/personvern": SAKER };
  SISTE = null;
  const h = nyHoved();
  visPersonvern(h, ctx());
  await vent(() => h.querySelectorAll("table tbody tr").length === 3);
  const rad = h.querySelector("table tbody tr");

  // De tre formene deler felt, men ALDRI tekst: et svar sier at
  // forespørselen ER BESVART, et avslag at den ikke etterkommes — og en
  // flate som kalte begge «referanse» ville invitert til å bruke den ene
  // som den andre.
  rad.querySelectorAll("button")[1].dispatchEvent(
    new window.Event("click", { bubbles: true }));
  assert.ok(h.textContent.includes(t("ui.personvern.dialog.avvishjelp")));
  assert.ok(!h.textContent.includes(t("ui.personvern.dialog.svarhjelp")));

  const felt = h.querySelector("#pv-referanse");
  const skjema = felt.closest("form");
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  await vent(() => h.textContent.includes(
    t("ui.personvern.feil.begrunnelse_kreves")));
  assert.equal(SISTE, null, "et tomt felt sendte likevel et kall");

  felt.value = "Anmodningen er åpenbart grunnløs, jf. art. 12 nr. 5.";
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  await vent(() => SISTE !== null);
  assert.equal(SISTE.sti,
    "/v1/personvern/11111111-1111-1111-1111-111111111111/avvis");
  assert.equal(SISTE.kropp.begrunnelse,
    "Anmodningen er åpenbart grunnløs, jf. art. 12 nr. 5.");

  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Personvern: forlengelsen har sitt eget datofelt, og krever det",
  async () => {
    SVAR = { "/v1/personvern": SAKER };
    SISTE = null;
    const h = nyHoved();
    visPersonvern(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 3);
    const rad = h.querySelector("table tbody tr");

    rad.querySelectorAll("button")[2].dispatchEvent(
      new window.Event("click", { bubbles: true }));
    assert.ok(h.textContent.includes(t("ui.personvern.dialog.forlenghjelp")));
    // Datofeltet finnes BARE her — et felt som står synlig i to av tre
    // former og betyr noe i én, er et felt folk fyller ut i feil form.
    const dato = h.querySelector("#pv-nyfrist");
    assert.equal(dato.closest("[hidden]"), null,
      "datofeltet er skjult i forlengelsesformen");
    assert.equal(dato.getAttribute("required"), "");
    assert.ok(h.querySelector('label[for="pv-nyfrist"]'));

    const felt = h.querySelector("#pv-referanse");
    const skjema = felt.closest("form");
    felt.value = "Saken omfatter fire lagre og en ekstern databehandler.";
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    // Uten dato sendes ingenting, og feilteksten sier hva som mangler.
    await vent(() => h.textContent.includes(
      t("ui.personvern.feil.dato_kreves")));
    assert.equal(SISTE, null);

    dato.value = "2026-10-01";
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    await vent(() => SISTE !== null);
    assert.equal(SISTE.sti,
      "/v1/personvern/11111111-1111-1111-1111-111111111111/forleng");
    assert.equal(SISTE.kropp.forlenget_til, "2026-10-01");
    assert.equal(SISTE.kropp.begrunnelse,
      "Saken omfatter fire lagre og en ekstern databehandler.");

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Personvern: registreringen krever eier og subjektreferanse, ikke frist",
  async () => {
    SVAR = { "/v1/personvern": SAKER };
    SISTE = null;
    const h = nyHoved();
    visPersonvern(h, ctx());
    await vent(() => h.querySelector("#pv-eier") !== null);

    // EIEREN ER PÅKREVD OG TOM. En flate som forhåndsutfylte innloggeren
    // ville gjort «forespørsler uten eier» sann på papiret og falsk i
    // praksis — den som tar imot er ofte ikke den som skal besvare.
    const eier = h.querySelector("#pv-eier");
    assert.equal(eier.getAttribute("required"), "");
    assert.equal(eier.value, "");
    assert.ok(h.textContent.includes(t("ui.personvern.skjema.eierhjelp")));
    // SUBJEKTET ER EN REFERANSE, og hjelpeteksten sier hvorfor: et
    // register over dem som har krevd innsyn er selv et persondatalager.
    assert.ok(h.textContent.includes(t("ui.personvern.skjema.subjekthjelp")));
    for (const id of ["pv-subjekt", "pv-eier", "pv-mottatt"]) {
      assert.equal(h.querySelector(`#${id}`).getAttribute("required"), "");
      assert.ok(h.querySelector(`label[for="${id}"]`), `${id} mangler label`);
    }
    // FRISTEN ER IKKE ET FELT. Registeret regner den av `mottatt` (én
    // måned, art. 12 nr. 3) — en frist noen kunne skrive fritt ville
    // gjort «oversittet» til en mening i stedet for et faktum.
    assert.equal(h.querySelector("#pv-frist"), null,
      "flaten tilbyr å skrive fristen — den skal regnes i basen");

    h.querySelector("#pv-type").value = "sletting";
    h.querySelector("#pv-subjekt").value = "DSR-2026-0050";
    eier.value = "bid_c";
    h.querySelector("#pv-mottatt").value = "2026-09-01";
    h.querySelector("#pv-lagre").value = "epost_melding, epost_vedlegg";
    h.querySelector("#pv-subjekt").closest("form").dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }));
    await vent(() => SISTE !== null);
    assert.equal(SISTE.sti, "/v1/personvern");
    assert.equal(SISTE.kropp.eier_bruker_id, "bid_c");
    assert.equal(SISTE.kropp.type, "sletting");
    assert.equal(SISTE.kropp.subjekt_ref, "DSR-2026-0050");
    // Datoen sendes som «YYYY-MM-DD», nøyaktig formen basen tar imot:
    // fristen er en DAG, og en tidssone som flyttet den et halvt døgn
    // ville vært en presisjon som ikke finnes i hjemmelen.
    assert.equal(SISTE.kropp.mottatt, "2026-09-01");
    assert.deepEqual(SISTE.kropp.lager_id,
      ["epost_melding", "epost_vedlegg"]);
  });

test("Personvern: tomt register sier hva fraværet betyr", async () => {
  SVAR = { "/v1/personvern": { saker: [], request_id: "r" } };
  const h = nyHoved();
  visPersonvern(h, ctx());
  await vent(() => h.textContent.includes(t("ui.personvern.liste.ingen")));
  // ÆRLIG TOMTILSTAND: et tomt register er ikke «ingen har spurt».
  assert.equal(h.querySelectorAll("table").length, 0);
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Personvern: ingen hardkodet tekst (pseudo-locale)", async () => {
  const PL = Object.fromEntries(Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = { "/v1/personvern": SAKER };
    const h = nyHoved();
    visPersonvern(h, ctx());
    await vent(() => h.querySelectorAll("table tbody tr").length === 3);
    const tekst = h.textContent;
    for (const ekte of ["Personvernforespørsler", "Innsyn", "Sletting",
                        "Eier", "Frist", "Åpen", "Besvart",
                        "Registrer svar", "Avvis", "Forleng fristen"]) {
      assert.ok(!tekst.includes(ekte),
        `hardkodet norsk tekst i flaten: «${ekte}»`);
    }
    assert.ok(tekst.includes("PL_ui.personvern.tittel"));
    assert.ok(tekst.includes("PL_ui.personvern.merke_oversittet"));
  } finally {
    settI18nForTest(NB, "nb");
  }
});
