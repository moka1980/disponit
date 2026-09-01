// M-3 datakvalitet — flateporten (jsdom + axe): axe uten alvorlige brudd,
// avviksandel som TALL og tekst (aldri trafikklys alene), «ikke målt»
// skilt fra både «0» og «ingen rader», `avbrutt` sagt med TEKST i
// kjøringens hode, tabellsemantikk (caption + th scope begge retninger +
// aria-sort), ÆRLIGE tomtilstander, ingen knapper, og ingen hardkodet
// tekst (pseudo-locale). Ingen delt fixture (m16-formen).
//
// Den viktigste porten her er den fjerde testen: en regel som står i
// kjøringens `umaalbare_regler` skal ALDRI rendres som 0. «0 tomme felt»
// fordi målingen ikke kjørte er ikke en grønn profil, og en flate som
// tegnet en null der ville gjenopprettet løgnen basen nekter å skrive.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visDatakvalitet } from "../static/js/flater/datakvalitet.js";

settI18nForTest(NB, "nb");

const REGLER = [
  { regel_id: "beredskap.bruker.unik", relasjon: "beredskapskontakt",
    kolonne: "bruker_id", regeltype: "unik_innen_tenant", uttrykk: null,
    alvorlighet: "lav", terskel_andel: 0, begrunnelse: "Duplikat person." },
  { regel_id: "domene.hostname.format", relasjon: "domenekontroll",
    kolonne: "hostname", regeltype: "format", uttrykk: "^[a-z.]+$",
    alvorlighet: "hoy", terskel_andel: 0, begrunnelse: "Kanonisk form." },
  { regel_id: "varsel.tekstnokkel.format", relasjon: "varsel",
    kolonne: "tekstnokkel", regeltype: "format", uttrykk: "^[a-z.]+$",
    alvorlighet: "middels", terskel_andel: 0, begrunnelse: "Locale-nøkkel." },
];

// Kjøringen dekker alle TRE tilstandene en regel kan ha på flaten:
//   * målt med tall   (beredskap.bruker.unik: 1 av 4)
//   * ikke målt       (varsel.tekstnokkel.format, i umaalbare_regler)
//   * ingen rader     (domene.hostname.format: målt, men tenanten har
//                      ingen domenekontroll-rader)
const SVAR_FULLT = {
  plattformdrift: false,
  regler: REGLER,
  kjoringer: [
    { kjoring_id: "k-1", startet_ts: "2026-09-01T02:14:00+00:00",
      fullfort_ts: "2026-09-01T02:14:09+00:00", alder_s: 900,
      antall_regler: 3, antall_umaalbare: 1, antall_funn: 2,
      umaalbare_regler: ["varsel.tekstnokkel.format"], avbrutt: false,
      profiler: [
        { regel_id: "beredskap.bruker.unik", rader_vurdert: 4,
          rader_avvik: 1, andel_avvik: 0.25 },
      ] },
    { kjoring_id: "k-2", startet_ts: "2026-08-31T02:14:00+00:00",
      fullfort_ts: "2026-08-31T02:14:07+00:00", alder_s: 87300,
      antall_regler: 3, antall_umaalbare: 0, antall_funn: 0,
      umaalbare_regler: [], avbrutt: false, profiler: [] },
  ],
  funn: [
    { regel_id: "beredskap.bruker.unik", funntype: "terskel_overskredet",
      forst_sett_ts: "2026-08-30T02:14:00+00:00",
      sist_sett_ts: "2026-09-01T02:14:00+00:00", ganger_sett: 3,
      detaljer: { rader_avvik: 1, rader_vurdert: 4 } },
  ],
  request_id: "r-1",
};

let SVAR;
globalThis.fetch = async (url) => {
  const sti = url.split("?")[0];
  const oppf = SVAR[sti];
  if (!oppf) return { ok: false, status: 404,
    json: async () => ({ feil: "ikke_funnet" }) };
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx(scopes = ["security:read"]) {
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

function medSvar(d) { return { "/v1/datakvalitet": d }; }

test("Datakvalitet: tre seksjoner, tabellsemantikk, ingen knapper, axe rent",
  async () => {
    SVAR = medSvar(SVAR_FULLT);
    const h = nyHoved();
    visDatakvalitet(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);

    const tabeller = [...h.querySelectorAll("table")];
    assert.equal(tabeller.length, 3,
      "profiltabell + funntabell + regelregister");
    for (const tb of tabeller) {
      assert.ok(tb.querySelector("caption").textContent.trim(),
        "en tabell uten caption");
      // aria-sort står på kolonnen SERVEREN sorterte på, i begge
      // tabellklassene — en tabell uten den sier til skjermleseren at
      // rekkefølgen er tilfeldig, og det er den ikke.
      assert.ok(tb.querySelector('th[scope="col"][aria-sort]'),
        "en tabell uten aria-sort på den sorterte kolonnen");
      for (const rad of tb.querySelectorAll("tbody tr")) {
        assert.equal(rad.cells[0].tagName, "TH");
        assert.equal(rad.cells[0].getAttribute("scope"), "row");
      }
    }

    // INGEN KNAPPER: v1 retter ingenting, og det finnes ingen HTTP-dør
    // å tegne en knapp til.
    assert.equal(h.querySelectorAll("button, form, input, select").length, 0,
      "datakvalitet er rent lesende — flaten skal ikke tilby mutasjon");

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Datakvalitet: avviksandel står som TALL og tekst, aldri farge alene",
  async () => {
    SVAR = medSvar(SVAR_FULLT);
    const h = nyHoved();
    visDatakvalitet(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);
    const tekst = h.textContent;
    // «1 av 4 (25,0 %)» — begge tellerne OG andelen, i klartekst.
    // Andelen formateres med SAMME formatter som flaten, ikke med et
    // hardkodet skilletegn: porten skal måle at tallet står der, ikke
    // låse norsk tegnsetting inn i en test.
    const andel = new Intl.NumberFormat("nb", {
      style: "percent", minimumFractionDigits: 1, maximumFractionDigits: 1,
    }).format(0.25);
    assert.ok(tekst.includes(
      t("ui.datakvalitet.avvik").replace("{avvik}", "1")
        .replace("{vurdert}", "4").replace("{andel}", andel)),
      `avvikscellen mangler tall eller andel: ${tekst}`);
    assert.ok(/25[.,]0/.test(tekst), "andelen står ikke som et tall");
    // Funntypen er en SETNING, ikke en maskinkode.
    assert.ok(tekst.includes(
      t("ui.datakvalitet.funntype.terskel_overskredet")));
    assert.ok(!tekst.includes("terskel_overskredet"),
      "maskinkoden lekket ut på flaten");
  });

test("Datakvalitet: en umålbar regel står som «ikke målt», ALDRI som 0",
  async () => {
    // Modulens bærende regel, håndhevet på skjermen.
    // MUTASJONEN SOM DREPER DENNE: la profiltabellen rendre en manglende
    // profilrad som 0 i stedet for å slå opp i `umaalbare_regler`.
    SVAR = medSvar(SVAR_FULLT);
    const h = nyHoved();
    visDatakvalitet(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);
    const profil = h.querySelectorAll("table")[0];
    const rader = [...profil.querySelectorAll("tbody tr")];
    assert.equal(rader.length, 3, "alle tre reglene skal ha en rad");
    const finn = (id) =>
      rader.find((r) => r.cells[0].textContent === id);

    const umaalbar = finn("varsel.tekstnokkel.format");
    for (let i = 1; i < umaalbar.cells.length; i++) {
      assert.equal(umaalbar.cells[i].textContent,
        t("ui.datakvalitet.profil.ikke_maalt"));
      assert.ok(!/\b0\b/.test(umaalbar.cells[i].textContent),
        "en umålbar regel ble rendret som et tall");
    }
    // «Ingen rader» er en TREDJE tilstand, ikke den samme som umålbar.
    const tom = finn("domene.hostname.format");
    assert.equal(tom.cells[1].textContent,
      t("ui.datakvalitet.profil.ingen_rader"));
    assert.notEqual(t("ui.datakvalitet.profil.ingen_rader"),
      t("ui.datakvalitet.profil.ikke_maalt"));
  });

test("Datakvalitet: kjøringens hode SIER med tekst om runden var avbrutt",
  async () => {
    // Fullført runde: hodet sier at den gikk gjennom registeret.
    SVAR = medSvar(SVAR_FULLT);
    let h = nyHoved();
    visDatakvalitet(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);
    assert.ok(h.textContent.includes(t("ui.datakvalitet.kjoring.fullfort")));
    assert.ok(!h.textContent.includes(t("ui.datakvalitet.kjoring.avbrutt")));

    // Avbrutt runde: setningen står, og den sier hva fraværet betyr.
    const avbrutt = JSON.parse(JSON.stringify(SVAR_FULLT));
    avbrutt.kjoringer[0].avbrutt = true;
    SVAR = medSvar(avbrutt);
    h = nyHoved();
    visDatakvalitet(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);
    assert.ok(h.textContent.includes(t("ui.datakvalitet.kjoring.avbrutt")),
      "en avbrutt kjøring sa det ikke med tekst");
    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Datakvalitet: tverrgående funnliste KUN når serveren sier plattformdrift",
  async () => {
    // Uten plattformdrift: seksjonen finnes ikke i det hele tatt.
    SVAR = medSvar(SVAR_FULLT);
    let h = nyHoved();
    visDatakvalitet(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);
    assert.ok(!h.textContent.includes(
      t("ui.datakvalitet.tverrgaaende.tittel")));
    assert.equal(h.querySelectorAll("table").length, 3);

    // Med plattformdrift: seksjonen kommer, den SIER at den er
    // tverrgående, og den navngir tenanten hver rad gjelder.
    const admin = JSON.parse(JSON.stringify(SVAR_FULLT));
    admin.plattformdrift = true;
    admin.tverrgaaende_funn = [
      { tenant: "bjorkli", regel_id: "domene.hostname.format",
        funntype: "umaalbar", forst_sett_ts: "2026-08-31T02:14:00+00:00",
        sist_sett_ts: "2026-09-01T02:14:00+00:00", ganger_sett: 2,
        detaljer: {} },
    ];
    SVAR = medSvar(admin);
    h = nyHoved();
    visDatakvalitet(h, ctx(["security:read", "platform:admin"]));
    await vent(() => h.querySelectorAll("table").length >= 4);
    assert.equal(h.querySelectorAll("table").length, 4);
    assert.ok(h.textContent.includes(
      t("ui.datakvalitet.tverrgaaende.forklaring")),
      "den tverrgående tabellen forklarer ikke hvorfor den er der");
    assert.ok(h.textContent.includes("bjorkli"));
    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Datakvalitet: tomme seksjoner sier at fravær er en tilstand, ikke orden",
  async () => {
    SVAR = medSvar({ plattformdrift: false, regler: [], kjoringer: [],
                     funn: [], request_id: "r" });
    const h = nyHoved();
    visDatakvalitet(h, ctx());
    await vent(() => h.textContent.includes(
      t("ui.datakvalitet.profil.ingen")));
    assert.ok(h.textContent.includes(t("ui.datakvalitet.profil.ingen")));
    assert.ok(h.textContent.includes(t("ui.datakvalitet.funn.ingen")));
    assert.ok(h.textContent.includes(t("ui.datakvalitet.regler.ingen")));
    assert.equal(h.querySelectorAll("table").length, 0);
    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Datakvalitet: ingen hardkodet tekst (pseudo-locale)", async () => {
  const PL = Object.fromEntries(Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = medSvar(SVAR_FULLT);
    const h = nyHoved();
    visDatakvalitet(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);
    const tekst = h.textContent;
    for (const ekte of ["Datakvalitet", "Kvalitetsregler", "Avvik",
                        "Ikke målt", "Ingen rader", "Over terskel",
                        "Alvorlighet", "Funntype"]) {
      assert.ok(!tekst.includes(ekte),
        `hardkodet norsk tekst i flaten: «${ekte}»`);
    }
    assert.ok(tekst.includes("PL_ui.datakvalitet.tittel"));
    assert.ok(tekst.includes("PL_ui.datakvalitet.profil.ikke_maalt"));
  } finally {
    settI18nForTest(NB, "nb");
  }
});

test("Datakvalitet: nb og en har hver eneste ui.datakvalitet-nøkkel",
  async () => {
    // Flaten er plattformdriftens, og driften er ikke nødvendigvis
    // norsk. En manglende engelsk nøkkel ville vist den norske setningen
    // midt i en engelsk flate — `t()` faller tilbake til NØKKELEN, ikke
    // til nb, så den engelske brukeren ville sett en maskinkode.
    const { readFileSync } = await import("node:fs");
    const en = JSON.parse(readFileSync(
      new URL("../../../../locales/en.json", import.meta.url), "utf-8"));
    const mine = Object.keys(NB).filter((k) =>
      k.startsWith("ui.datakvalitet.") || k === "ui.nav.datakvalitet");
    assert.ok(mine.length >= 40, `for få nøkler i porten: ${mine.length}`);
    for (const k of mine) {
      assert.ok(typeof en[k] === "string" && en[k].trim(),
        `en.json mangler ${k}`);
    }
  });
