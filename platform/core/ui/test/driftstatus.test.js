// M-10 + M-11 driftstatus — flateporten (jsdom + axe): axe uten alvorlige
// brudd, status som TEKST (aldri kun farge), tabellsemantikk (caption +
// th scope begge retninger) i alle tre tabellene, <time datetime> på hvert
// tidspunkt, ÆRLIGE tomtilstander (en tom seksjon sier at fravær er en
// tilstand som varsles, ikke at alt er i orden), og ingen hardkodet tekst
// (pseudo-locale). Ingen delt fixture (m16-formen).
//
// Flaten henter TO endepunkter i ett kall-par. Stubben under svarer på
// begge, og én test tar bort det ene for å måle at rammen ikke tegner en
// halv side.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visDriftstatus } from "../static/js/flater/driftstatus.js";

settI18nForTest(NB, "nb");

const BACKUP = {
  verifiseringer: [
    { backup_ts: "2026-08-31T03:15:00+00:00",
      verifisert_ts: "2026-08-31T03:22:00+00:00",
      restore_varighet_s: 42.5, tabeller: 137, storrelse_b: 8388608,
      registrert: "2026-08-31T03:30:00+00:00", alder_s: 12600 },
    { backup_ts: "2026-08-30T03:15:00+00:00",
      verifisert_ts: "2026-08-30T03:21:00+00:00",
      restore_varighet_s: 40.1, tabeller: 136, storrelse_b: 8300000,
      registrert: "2026-08-30T03:30:00+00:00", alder_s: 99000 },
  ],
  request_id: "r-b",
};

const SELVTEST = {
  kjoringer: [
    { kjoring_id: "k-1", ts: "2026-08-31T12:00:00+00:00", samlet: "rod",
      alder_s: 900, prober: [
        { probe: "api_live", status: "gronn", maalt: { exitkode: 0 } },
        { probe: "ollama", status: "ikke_konfigurert",
          maalt: { grunn: "ikke_satt" } },
        { probe: "timer_disponit-backup", status: "rod",
          maalt: { grunn: "for_lenge_siden", alder_s: 300000 } },
      ] },
    { kjoring_id: "k-2", ts: "2026-08-31T11:00:00+00:00", samlet: "gronn",
      alder_s: 4500, prober: [
        { probe: "api_live", status: "gronn", maalt: {} },
      ] },
  ],
  request_id: "r-s",
};

let SVAR;
globalThis.fetch = async (url) => {
  const sti = url.split("?")[0];
  const oppf = SVAR[sti];
  if (!oppf) return { ok: false, status: 404,
    json: async () => ({ feil: "ikke_funnet" }) };
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx() {
  return { sprak: "nb", scopes: ["security:read"], tenant: "acme",
    paaUautorisert: () => {} };
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

function beggeSvar() {
  return { "/v1/drift/backup": BACKUP, "/v1/drift/selvtest": SELVTEST };
}

test("Driftstatus: begge seksjoner, status som tekst, tabellsemantikk, axe rent",
  async () => {
    SVAR = beggeSvar();
    const h = nyHoved();
    visDriftstatus(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);

    const tabeller = [...h.querySelectorAll("table")];
    assert.equal(tabeller.length, 3,
      "backup + probene i siste runde + rundehistorikken");
    for (const tb of tabeller) {
      // Tabellsemantikk i BEGGE retninger: uten th scope="row" mister en
      // skjermleser i tallkolonnene hvilken rad tallet gjelder.
      assert.ok(tb.querySelector("caption").textContent.trim(),
        "en tabell uten caption");
      assert.ok(tb.querySelector('th[scope="col"]'));
      for (const rad of tb.querySelectorAll("tbody tr")) {
        assert.equal(rad.cells[0].tagName, "TH");
        assert.equal(rad.cells[0].getAttribute("scope"), "row");
      }
    }

    // STATUS SOM TEKST, aldri kun farge — og alle TRE statusene har hver
    // sin setning. `ikke_konfigurert` er det som ikke lar seg uttrykke i
    // en fargeskala: det er ikke et mildere rødt.
    const tekst = h.textContent;
    for (const kode of ["gronn", "rod", "ikke_konfigurert"]) {
      assert.ok(tekst.includes(t(`ui.driftstatus.status.${kode}`)),
        `statusen ${kode} står ikke som tekst`);
    }
    // Probens årsak er oversatt, ikke en rå maskinkode på skjermen.
    assert.ok(tekst.includes(t("ui.driftstatus.grunn.for_lenge_siden")));
    assert.ok(!tekst.includes("for_lenge_siden"),
      "maskinkoden lekket ut på flaten");

    // <time datetime> på hvert tidspunkt — maskinlesbar verdi ved siden
    // av den formaterte.
    const tider = [...h.querySelectorAll("time")];
    assert.ok(tider.length >= 6, `for få <time>: ${tider.length}`);
    for (const el of tider) {
      assert.ok(el.getAttribute("datetime"),
        "et <time> uten datetime-attributt");
    }
    assert.ok(tider.some((e) => e.getAttribute("datetime")
      === "2026-08-31T03:15:00+00:00"));

    // Tallene står som de kom: ingen utregning i flaten.
    assert.ok(tekst.includes("137"));
    assert.ok(tekst.includes("8388608"));
    assert.ok(tekst.includes("42.5"));
    // Alderen er en enhetsomregning av ETT tall (12600 s = 3 t), ikke et
    // forhold mellom to av svarets tall.
    assert.ok(tekst.includes(
      t("ui.driftstatus.timer_siden").replace("{timer}", "3")));

    // Ingen mutasjonsknapper: det finnes ingen HTTP-dør å tegne en til.
    assert.equal(h.querySelectorAll("button, form, input").length, 0,
      "driftstatus er rent lesende — flaten skal ikke tilby mutasjon");

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Driftstatus: tomme seksjoner sier at fravær er en tilstand som varsles",
  async () => {
    SVAR = {
      "/v1/drift/backup": { verifiseringer: [], request_id: "r" },
      "/v1/drift/selvtest": { kjoringer: [], request_id: "r" },
    };
    const h = nyHoved();
    visDriftstatus(h, ctx());
    await vent(() => h.textContent.includes(t("ui.driftstatus.backup.ingen")));
    // Begge tomtilstandene er EKSPLISITT innhold, ikke en tom seksjon —
    // og de sier hva fraværet betyr, ikke bare at det finnes.
    assert.ok(h.textContent.includes(t("ui.driftstatus.backup.ingen")));
    assert.ok(h.textContent.includes(t("ui.driftstatus.selvtest.ingen")));
    assert.equal(h.querySelectorAll("table").length, 0);
    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Driftstatus: ett feilet kall tegner ingen halv side", async () => {
  // De to endepunktene står bak SAMME scope, så de kan ikke lykkes hver
  // for seg på en meningsfull måte. `Promise.all` gjør de to til ett
  // utfall rammen eier — uten det ville en feil på det ene latt den
  // andre seksjonen stå igjen som om alt var greit.
  SVAR = { "/v1/drift/backup": BACKUP };
  const h = nyHoved();
  visDriftstatus(h, ctx());
  await vent(() => h.textContent.includes(t("ui.feil_tittel")));
  assert.ok(h.textContent.includes(t("ui.feil_tittel")));
  assert.equal(h.querySelectorAll("table").length, 0,
    "backupseksjonen ble tegnet selv om selvtestkallet feilet");
});

test("Driftstatus: ingen hardkodet tekst (pseudo-locale)", async () => {
  const PL = Object.fromEntries(Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = beggeSvar();
    const h = nyHoved();
    visDriftstatus(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 3);
    const tekst = h.textContent;
    for (const ekte of ["Driftstatus", "Grønn", "Rød", "Ikke konfigurert",
                        "Backup", "Selvtest", "Status", "Årsak"]) {
      assert.ok(!tekst.includes(ekte),
        `hardkodet norsk tekst i flaten: «${ekte}»`);
    }
    assert.ok(tekst.includes("PL_ui.driftstatus.tittel"));
    assert.ok(tekst.includes("PL_ui.driftstatus.status.ikke_konfigurert"));
  } finally {
    settI18nForTest(NB, "nb");
  }
});

test("Driftstatus: nb og en har hver eneste ui.driftstatus-nøkkel", async () => {
  // Flaten er PLATTFORMDRIFTENS, og driften er ikke nødvendigvis norsk.
  // En manglende engelsk nøkkel ville vist den norske setningen midt i
  // en engelsk flate — `t()` faller tilbake til nøkkelen, ikke til nb.
  const { readFileSync } = await import("node:fs");
  const en = JSON.parse(readFileSync(
    new URL("../../../../locales/en.json", import.meta.url), "utf-8"));
  const mine = Object.keys(NB).filter((k) => k.startsWith("ui.driftstatus.")
    || k === "ui.nav.driftstatus"
    || k.startsWith("varsel.selvtest_")
    || k === "varsel.backupverifisering_uteblitt");
  assert.ok(mine.length >= 40, `for få nøkler i porten: ${mine.length}`);
  for (const k of mine) {
    assert.ok(typeof en[k] === "string" && en[k].trim(),
      `en.json mangler ${k}`);
  }
});
