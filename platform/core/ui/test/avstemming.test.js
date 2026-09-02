// M-13 avstemmingsflaten (101) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd, på hver
//     av skjermene flaten kan stå i (lister, tomme lister, dialog åpen).
//   * BELØP FORMATERES I HELTALLSARITMETIKK. Et flyttall her ville gitt
//     «1234,5599999999999» på et beløp som er nøyaktig i basen, og en
//     avstemmingsflate som viser et annet tall enn registeret er verre
//     enn ingen flate.
//   * ALDER OG FORFALL ER TEKST, ikke bare farge (WCAG 1.4.1) — og
//     antall døgn står som ord. Det er flatens viktigste jobb.
//   * SAMMENDRAGET KOMMER FRA SIN EGEN DØR og teller ALT. Er listen
//     avkortet, SIER flaten det — ellers ser den komplett ut nettopp når
//     den er det minst.
//   * MATCHDIALOGEN FILTRERER PÅ FORTEGN: en utbetaling kan ikke dekke
//     en kundefaktura, og valgene som garantert ville gitt 409 finnes
//     ikke i nedtrekket.
//   * MODULEN BOKFØRER INGENTING: ingen kontroll på flaten kaller noe
//     som ligner en bokføringsvei, og kilden bærer ingen.
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
import {
  belopTekst, visAvstemming,
} from "../static/js/flater/avstemming.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));

const BILDE = {
  sammendrag: {
    poster_totalt: 340, poster_uavstemt: 12, uavstemt_ore: 4520000,
    bilag_apne: 3, rest_ore: 155000, apne_funn: 4,
    poster_vist: 2, bilag_vist: 2,
  },
  kontoer: [
    { konto_id: "aaaaaaaa-1111-1111-1111-111111111111",
      navn: "Driftskonto", kontonummer_hale: "8903", valuta: "NOK",
      aktiv: true, poster: 340 },
  ],
  poster: [
    { post_id: "11111111-1111-1111-1111-111111111111",
      konto_navn: "Driftskonto", konto_hale: "8903", valuta: "NOK",
      ekstern_ref: "BANK-2026-0001", bokfort: "2026-01-15",
      belop_ore: 1234567, tekst: "Innbetaling fra kunde",
      motpart: "Kunde AS", alder_dogn: 230,
      apne_funn: ["uavstemt_post_over_grense"] },
    { post_id: "22222222-2222-2222-2222-222222222222",
      konto_navn: "Driftskonto", konto_hale: "8903", valuta: "NOK",
      ekstern_ref: "BANK-2026-0002", bokfort: "2026-08-30",
      belop_ore: -50000, tekst: "Utbetaling til leverandør",
      motpart: "Lev AS", alder_dogn: 1, apne_funn: [] },
  ],
  bilag: [
    { bilag_id: "33333333-3333-3333-3333-333333333333",
      bilagsnummer: "F-1001", retning: "inn", belop_ore: 200000,
      dekket_ore: 0, rest_ore: 200000, motpart: "Kunde AS",
      utstedt: "2026-06-01", forfall: "2026-07-01",
      dogn_over_forfall: 63,
      apne_funn: ["forfalt_bilag_uten_dekning"] },
    { bilag_id: "44444444-4444-4444-4444-444444444444",
      bilagsnummer: "F-1002", retning: "ut", belop_ore: 100000,
      dekket_ore: 60000, rest_ore: 40000, motpart: "Lev AS",
      utstedt: "2026-08-01", forfall: "2026-09-01",
      dogn_over_forfall: 1,
      apne_funn: ["delvis_dekket_bilag"] },
  ],
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    poster_totalt: 0, poster_uavstemt: 0, uavstemt_ore: 0,
    bilag_apne: 0, rest_ore: 0, apne_funn: 0,
    poster_vist: 0, bilag_vist: 0,
  },
  kontoer: [], poster: [], bilag: [], request_id: "r-b",
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

function ctx(scopes = ["okonomi:read", "bestilling:opprett"]) {
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

test("Avstemming: beløp regnes i heltall, aldri via flyttall",
  () => {
    // 1234567 øre = 12 345,67 kr. Med `/100` og `toFixed` er dette
    // fortsatt riktig — det er de neste som avslører forskjellen.
    assert.equal(belopTekst(1234567), "12345,67");
    assert.equal(belopTekst(-50000), "-500,00");
    assert.equal(belopTekst(5), "0,05");
    assert.equal(belopTekst(0), "0,00");
    assert.equal(belopTekst(100), "1,00");
    // DEN SOM FELLER EN FLYTTALLSIMPLEMENTASJON: 8,15 og 1,05 er begge
    // tall der `x/100` ikke er eksakt i IEEE 754.
    assert.equal(belopTekst(815), "8,15");
    assert.equal(belopTekst(105), "1,05");
    assert.equal(belopTekst(70000000000), "700000000,00");
    // Ikke-heltall er IKKE et beløp og rundes aldri.
    assert.equal(belopTekst(12.5), "—");
    assert.equal(belopTekst(null), "—");
    assert.equal(belopTekst("100"), "—");
  });

test("Avstemming: alder og forfall som TEKST, tabellsemantikk, axe rent",
  async () => {
    SVAR = { "/v1/avstemming": BILDE };
    SISTE = null;
    const h = nyHoved();
    visAvstemming(h, ctx());
    await vent(() => h.querySelectorAll("table").length === 2);

    for (const tb of h.querySelectorAll("table")) {
      assert.ok(tb.querySelector("caption").textContent.trim(),
        "tabellen mangler caption");
      assert.ok(tb.querySelector('th[scope="col"]'));
      for (const rad of tb.querySelectorAll("tbody tr")) {
        // Tabellsemantikk i BEGGE retninger: uten th scope="row" mister
        // en skjermleser i beløps- og alderskolonnene hvilken post eller
        // hvilket bilag tallet gjelder.
        assert.equal(rad.cells[0].tagName, "TH");
        assert.equal(rad.cells[0].getAttribute("scope"), "row");
      }
    }

    const tekst = h.textContent;
    // FLATENS VIKTIGSTE JOBB: hvor lenge, som ORD.
    assert.ok(tekst.includes(
      t("ui.avstemming.alder_dogn").replace("{dogn}", "230")),
    "alderen står ikke som tekst");
    assert.ok(tekst.includes(t("ui.avstemming.alder_ett_dogn")),
      "entallet har ikke sin egen setning");
    assert.ok(tekst.includes(
      t("ui.avstemming.forfalt_for").replace("{dogn}", "63")));
    assert.ok(tekst.includes(t("ui.avstemming.forfalt_ett_dogn")));
    // MERKENE ER TEKST, ikke farge — og de to bilagsmerkene utelukker
    // hverandre.
    assert.ok(tekst.includes(t("ui.avstemming.merke_over_grense")));
    assert.ok(tekst.includes(t("ui.avstemming.merke_uten_dekning")));
    assert.ok(tekst.includes(t("ui.avstemming.merke_delvis")));
    // BELØPENE står formatert, ikke som rå øre.
    assert.ok(tekst.includes("12345,67"), "beløpet står ikke formatert");
    assert.ok(!tekst.includes("1234567"), "råe øre lekker til flaten");

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Avstemming: sammendraget teller ALT og sier fra når listen er kuttet",
  async () => {
    SVAR = { "/v1/avstemming": BILDE };
    const h = nyHoved();
    visAvstemming(h, ctx());
    await vent(() => h.querySelectorAll("table").length === 2);
    const tekst = h.textContent;
    // 12 av 340 — ikke 2 av 2. Hadde sammendraget vært regnet fra
    // listen, ville flaten sagt «to uavstemte poster» når det var tolv,
    // og tallet ville vært mest galt den dagen det betydde mest.
    assert.ok(tekst.includes("12") && tekst.includes("340"),
      "sammendraget teller listen i stedet for registeret");
    assert.ok(tekst.includes(
      t("ui.avstemming.avkortet_poster").replace("{vist}", "2")),
    "flaten sier ikke at postlisten er avkortet");
    assert.ok(tekst.includes(
      t("ui.avstemming.avkortet_bilag").replace("{vist}", "2")),
    "flaten sier ikke at bilagslisten er avkortet");
  });

test("Avstemming: matchdialogen filtrerer på fortegn", async () => {
  SVAR = { "/v1/avstemming": BILDE };
  SISTE = null;
  const h = nyHoved();
  visAvstemming(h, ctx());
  await vent(() => h.querySelectorAll("table").length === 2);

  const knapper = [...h.querySelectorAll("tbody button")].filter(
    (b) => b.textContent === t("ui.avstemming.knapp.avstem"));
  assert.equal(knapper.length, 2, "avstem-knappen mangler på en rad");

  // Rad 1 er en INNBETALING (+12 345,67). Bare `inn`-bilaget skal stå i
  // nedtrekket — en utbetalingsfaktura ville garantert gitt 409.
  knapper[0].click();
  const valg = h.querySelector("#avst-match-bilag");
  assert.ok(valg, "matchdialogen åpnet ikke");
  let verdier = [...valg.options].map((o) => o.value);
  assert.deepEqual(verdier, ["33333333-3333-3333-3333-333333333333"]);

  // Rad 2 er en UTBETALING (−500,00). Nå snur listen.
  knapper[1].click();
  verdier = [...h.querySelector("#avst-match-bilag").options]
    .map((o) => o.value);
  assert.deepEqual(verdier, ["44444444-4444-4444-4444-444444444444"]);

  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Avstemming: matchen sender post, bilag og metode med idempotensnøkkel",
  async () => {
    SVAR = { "/v1/avstemming": BILDE };
    SISTE = null;
    const h = nyHoved();
    visAvstemming(h, ctx());
    await vent(() => h.querySelectorAll("table").length === 2);
    const knapp = [...h.querySelectorAll("tbody button")].find(
      (b) => b.textContent === t("ui.avstemming.knapp.avstem"));
    knapp.click();
    h.querySelector("#avst-match-begrunnelse").value = "samme referanse";
    h.querySelector("#avst-match-bilag").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE !== null);
    assert.equal(SISTE.sti, "/v1/avstemming/match");
    assert.equal(SISTE.kropp.post_id,
      "11111111-1111-1111-1111-111111111111");
    assert.equal(SISTE.kropp.bilag_id,
      "33333333-3333-3333-3333-333333333333");
    assert.equal(SISTE.kropp.metode, "manuell");
    assert.equal(SISTE.kropp.begrunnelse, "samme referanse");
    assert.ok(SISTE.headers["Idempotency-Key"],
      "matchen ble sendt uten idempotensnøkkel");
  });

test("Avstemming: kroner inn blir øre ut, uten flyttallsavvik",
  async () => {
    SVAR = { "/v1/avstemming": BILDE };
    SISTE = null;
    const h = nyHoved();
    visAvstemming(h, ctx());
    await vent(() => h.querySelector("#avst-bilag-belop") !== null);
    // 8,15 kroner. `parseFloat("8.15") * 100` er 814.9999999999999 i
    // IEEE 754; uten `Math.round` ville bilaget blitt registrert med
    // 814 øre, og avviket ville vært usynlig fra det øyeblikket.
    h.querySelector("#avst-bilag-nummer").value = "F-2001";
    h.querySelector("#avst-bilag-belop").value = "8.15";
    h.querySelector("#avst-bilag-motpart").value = "Kunde AS";
    h.querySelector("#avst-bilag-utstedt").value = "2026-09-01";
    h.querySelector("#avst-bilag-belop").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE !== null);
    assert.equal(SISTE.sti, "/v1/avstemming/bilag");
    assert.equal(SISTE.kropp.belop_ore, 815);
  });

test("Avstemming: tom tilstand skiller «alt stemmer» fra «ingenting importert»",
  async () => {
    SVAR = { "/v1/avstemming": TOMT };
    const h = nyHoved();
    visAvstemming(h, ctx());
    await vent(() => h.textContent.includes(
      t("ui.avstemming.poster.ingen")));
    // NULL uavstemte poster kan bety at alt stemmer — eller at ingen har
    // importert en kontoutskrift. De to er ikke det samme.
    assert.ok(h.textContent.includes(t("ui.avstemming.poster.ingen")));
    assert.ok(!h.textContent.includes(
      t("ui.avstemming.poster.alt_avstemt")));
    // …og bankpostskjemaet sier hvorfor det ikke kan brukes ennå.
    assert.ok(h.textContent.includes(
      t("ui.avstemming.skjema.post_ingen_konto")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Avstemming: alt avstemt sier noe annet enn ingenting importert",
  async () => {
    SVAR = { "/v1/avstemming": {
      ...TOMT,
      sammendrag: { ...TOMT.sammendrag, poster_totalt: 42 },
    } };
    const h = nyHoved();
    visAvstemming(h, ctx());
    await vent(() => h.textContent.includes(
      t("ui.avstemming.poster.alt_avstemt")));
    assert.ok(!h.textContent.includes(t("ui.avstemming.poster.ingen")));
  });

test("Avstemming: en lesende økt får ingen mutasjonskontroller",
  async () => {
    SVAR = { "/v1/avstemming": BILDE };
    const h = nyHoved();
    visAvstemming(h, ctx(["okonomi:read"]));
    await vent(() => h.querySelectorAll("table").length === 2);
    // Flatens SVAKESTE ledd: lesingen krever `okonomi:read`, skrivingen
    // `bestilling:opprett`. En knapp som ville gitt 403 er en knapp som
    // lover noe serveren nekter.
    assert.equal(h.querySelectorAll("form").length, 0);
    assert.equal(h.querySelectorAll("button").length, 0);
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Avstemming: ingen hardkodet tekst i flaten", async () => {
  // PSEUDO-LOCALE: hver oversatt streng får et prefiks. Står det tekst
  // på skjermen UTEN prefikset, er den hardkodet — og da finnes den bare
  // på ett språk.
  const nokler = Object.keys(NB).filter((k) => k.startsWith("ui.avstemming"));
  assert.ok(nokler.length > 40, `bare ${nokler.length} nøkler`);
  const pseudo = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(pseudo, "nb");
  try {
    SVAR = { "/v1/avstemming": BILDE };
    const h = nyHoved();
    visAvstemming(h, ctx());
    await vent(() => h.querySelectorAll("table").length === 2);
    // `th[scope="row"]` er UTELATT med vilje: den cellen bærer bankens
    // egen referanse, altså kundens data — ikke en oversatt etikett.
    for (const node of h.querySelectorAll(
      'h2, h3, label, caption, th[scope="col"], button')) {
      const s = node.textContent.trim();
      if (!s) continue;
      assert.ok(s.startsWith("PL_"), `hardkodet tekst: «${s}»`);
    }
  } finally {
    settI18nForTest(NB, "nb");
  }
});

test("Avstemming: kilden bærer ingen bokføringsvei", () => {
  // MODULEN BOKFØRER INGENTING, målt på FLATENS kilde. De tre andre
  // halvdelene av samme dom står i `test_m13_avstemming.py` (AST,
  // datamodellen, rutene); denne fanger en knapp som kalte et endepunkt
  // som ikke finnes ennå — altså den formen en bokføringsvei ville hatt
  // her først.
  const kilde = readFileSync(
    join(HER, "..", "static", "js", "flater", "avstemming.js"), "utf8");
  const uten = kilde.replace(/^\s*\/\/.*$/gm, "");
  // ORDGRENSER, ikke delstrenger: `bokfort` er bankpostens dato og har
  // ingenting med bokføring å gjøre. En port som felte den ville tvunget
  // fram et dårligere feltnavn for å bestå seg selv.
  for (const ord of ["bokfoer", "hovedbok", "postering_", "/v1/regnskap",
                     "bokfor("]) {
    assert.ok(!uten.toLowerCase().includes(ord),
      `flaten bærer «${ord}» — v1 bokfører ingenting`);
  }
  // …og API-modulen eksporterer ingen funksjon som ligner på en.
  const api = readFileSync(
    join(HER, "..", "static", "js", "api.js"), "utf8");
  assert.ok(!/export const bokfor/.test(api));
});
