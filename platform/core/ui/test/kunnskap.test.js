// M-9 ordlisten — flateporten (jsdom + axe): axe uten alvorlige brudd,
// KILDEN SOM KOLONNE (ikke fotnote), UTLØPT SOM TEKST (aldri kun farge),
// tabellsemantikk begge veier, `aria-live` på resultattellingen, ærlige
// tomtilstander, søk som sender når brukeren sier fra (ikke per
// tastetrykk), og ingen hardkodet tekst (pseudo-locale).
//
// Flaten er RENT LESENDE. Den eneste knappen er søkeknappen: det finnes
// ingen HTTP-skrivevei inn i ordlisten (dørene i 095 er REVOKEt fra
// runtime-rollen), og en «Nytt begrep»-knapp ville vært teater.
//
// Ingen delt fixture (m16-formen).
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visKunnskap } from "../static/js/flater/kunnskap.js";

settI18nForTest(NB, "nb");

const SVAR = {
  sporring: "",
  begreper: [
    { begrep_id: "b-1", term: "avtale",
      forklaring: "En bindende enighet mellom to parter.",
      eier: "juridisk", kilde: "kilde://intern/avtaleharmonisering",
      gyldig_til: "2027-01-31", versjonsnr: 2, utlopt: false, rang: 1.0 },
    { begrep_id: "b-2", term: "leveransefrist",
      forklaring: "Siste dag en leveranse kan skje uten dagbot.",
      eier: "innkjop", kilde: "Rutine INK-14",
      gyldig_til: "2024-06-30", versjonsnr: 1, utlopt: true, rang: 0.4 },
  ],
  funn: [
    { begrep_id: "b-2", funntype: "utlopt", term: "leveransefrist",
      gyldig_til: "2024-06-30", forst_sett: "2026-08-31T04:20:00+00:00",
      sist_sett_sveip: "2026-09-01T04:20:00+00:00", alder_s: 86400 },
  ],
  request_id: "r-k",
};

const TOMT = { sporring: "zzz", begreper: [], funn: [], request_id: "r-t" };

let NESTE = SVAR;
let KALL = [];
globalThis.fetch = async (url) => {
  KALL.push(url);
  if (NESTE === null) {
    return { ok: false, status: 500, json: async () => ({ feil: "serverfeil" }) };
  }
  return { ok: true, status: 200, json: async () => NESTE };
};

function ctx() {
  return { sprak: "nb", scopes: ["decisions:read"], tenant: "acme",
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
  KALL = [];
  const brett = nyttBrett();
  const m = document.createElement("main");
  m.id = "hovedinnhold"; m.tabIndex = -1;
  brett.append(m);
  return m;
}

test("Ordliste: kilde og eier er KOLONNER, utløpt er TEKST, axe rent",
  async () => {
    NESTE = SVAR;
    const h = nyHoved();
    visKunnskap(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);

    const tabeller = [...h.querySelectorAll("table")];
    assert.equal(tabeller.length, 2, "ordlisten + utløpsfunnene");
    for (const tb of tabeller) {
      assert.ok(tb.querySelector("caption").textContent.trim(),
        "en tabell uten caption");
      assert.ok(tb.querySelector('th[scope="col"]'));
      for (const rad of tb.querySelectorAll("tbody tr")) {
        // Termen NAVNGIR raden: uten th scope="row" mister en
        // skjermleser i kilde- og datokolonnene hvilket begrep det er.
        assert.equal(rad.cells[0].tagName, "TH");
        assert.equal(rad.cells[0].getAttribute("scope"), "row");
      }
    }

    // KILDEN ER EN KOLONNE. Overskriften finnes, og hver rad har en
    // celle med den faktiske kildeteksten — ikke en tooltip, ikke en
    // «vis mer».
    const kolonner = [...tabeller[0].querySelectorAll('thead th')]
      .map((e) => e.textContent);
    for (const n of ["term", "forklaring", "eier", "kilde", "gyldig_til"]) {
      assert.ok(kolonner.includes(t(`ui.kunnskap.kolonne.${n}`)),
        `kolonnen ${n} mangler i ordlisten`);
    }
    const rader = [...tabeller[0].querySelectorAll("tbody tr")];
    assert.equal(rader.length, 2);
    const celler = [...rader[0].cells].map((c) => c.textContent);
    assert.ok(celler.includes("juridisk"), "eier står ikke i en celle");
    assert.ok(celler.includes("kilde://intern/avtaleharmonisering"),
      "kilden står ikke i en celle");
    assert.ok(celler.includes("2027-01-31"),
      "gyldighetsdatoen står ikke i en celle");
    // Kilden er RÅ TEKST, aldri en lenke — flaten vet ikke hva den peker på.
    assert.equal(tabeller[0].querySelectorAll("a").length, 0,
      "kilden ble gjort klikkbar — flaten påstår da at den vet hva den peker på");

    // UTLØPT SOM TEKST, aldri kun farge (WCAG 1.4.1).
    const tekst = h.textContent;
    assert.ok(tekst.includes(t("ui.kunnskap.status.utlopt")),
      "«utløpt» står ikke som tekst");
    assert.ok(tekst.includes(t("ui.kunnskap.status.gjeldende")));
    // …og statusen står på RIKTIG rad, ikke bare et sted på siden.
    assert.ok([...rader[1].cells].map((c) => c.textContent)
      .includes(t("ui.kunnskap.status.utlopt")));
    assert.ok([...rader[0].cells].map((c) => c.textContent)
      .includes(t("ui.kunnskap.status.gjeldende")));

    // Funnet er oversatt, ikke en rå maskinkode på skjermen.
    assert.ok(tekst.includes(t("ui.kunnskap.funntype.utlopt")));
    assert.ok(!tekst.includes("utloper_snart"));

    // <time datetime> på tidspunktet i funnlisten.
    const tider = [...h.querySelectorAll("time")];
    assert.ok(tider.length >= 1);
    for (const e of tider) assert.ok(e.getAttribute("datetime"));

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Ordliste: tellingen har aria-live og sier hvor mange treff",
  async () => {
    NESTE = SVAR;
    const h = nyHoved();
    visKunnskap(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);
    const telling = h.querySelector('[aria-live]');
    assert.ok(telling, "resultattellingen mangler aria-live");
    // `polite`, ikke `assertive`: et søkeresultat skal ikke avbryte den
    // som holder på å lese noe annet.
    assert.equal(telling.getAttribute("aria-live"), "polite");
    assert.equal(telling.getAttribute("aria-atomic"), "true");
    assert.equal(telling.textContent,
      t("ui.kunnskap.treff").replace("{antall}", "2"));
  });

test("Ordliste: søket sender når brukeren sier fra, ikke per tastetrykk",
  async () => {
    NESTE = SVAR;
    const h = nyHoved();
    visKunnskap(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);
    assert.equal(KALL.length, 1, "første tegning er ETT kall (listingen)");
    assert.ok(KALL[0].startsWith("/v1/kunnskap"));

    const felt = h.querySelector('input[type="search"]');
    assert.ok(felt, "søkefeltet mangler");
    // Et felt uten <label for> er et felt en skjermleser ikke kan navngi.
    const merke = h.querySelector(`label[for="${felt.id}"]`);
    assert.ok(merke && merke.textContent.trim());

    // Tastetrykk alene skal IKKE spørre serveren: en resultatliste som
    // endrer seg under fingrene gir aria-live seks halvferdige svar.
    felt.value = "avt";
    felt.dispatchEvent(new Event("input", { bubbles: true }));
    felt.value = "avta";
    felt.dispatchEvent(new Event("input", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(KALL.length, 1, "flaten spurte serveren per tastetrykk");

    NESTE = TOMT;
    felt.value = "zzz";
    h.querySelector("form").dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }));
    await vent(() => KALL.length === 2);
    assert.ok(KALL[1].includes("q=zzz"), KALL[1]);
    await vent(() => h.textContent.includes(t("ui.kunnskap.ingen_treff")));
  });

test("Ordliste: tomtilstandene sier hva fraværet betyr", async () => {
  NESTE = TOMT;
  const h = nyHoved();
  visKunnskap(h, ctx());
  await vent(() => h.textContent.includes(t("ui.kunnskap.ordliste.ingen")));
  // Begge tomtilstandene er EKSPLISITT innhold, ikke en tom seksjon —
  // og funn-teksten sier at sveipen er det som fyller listen, så en tom
  // liste på en vert der timeren aldri har kjørt ikke leses som «i orden».
  assert.ok(h.textContent.includes(t("ui.kunnskap.ordliste.ingen")));
  assert.ok(h.textContent.includes(t("ui.kunnskap.funn.ingen")));
  assert.equal(h.querySelectorAll("table").length, 0);
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Ordliste: ingen mutasjonsknapper utenom søket", async () => {
  NESTE = SVAR;
  const h = nyHoved();
  visKunnskap(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 2);
  const knapper = [...h.querySelectorAll("button")];
  assert.equal(knapper.length, 1,
    "flaten er rent lesende — den eneste knappen er søkeknappen");
  assert.equal(knapper[0].textContent, t("ui.kunnskap.sok_knapp"));
  assert.equal(h.querySelectorAll("form").length, 1);
});

test("Ordliste: et feilet kall tegner ingen halv side", async () => {
  NESTE = null;
  const h = nyHoved();
  visKunnskap(h, ctx());
  await vent(() => h.textContent.includes(t("ui.feil_tittel")));
  assert.equal(h.querySelectorAll("table").length, 0,
    "ordlisten ble tegnet selv om kallet feilet");
});

test("Ordliste: ingen hardkodet tekst (pseudo-locale)", async () => {
  const PL = Object.fromEntries(Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    NESTE = SVAR;
    const h = nyHoved();
    visKunnskap(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);
    const tekst = h.textContent;
    for (const ekte of ["Ordliste", "Begrep", "Forklaring", "Eier", "Kilde",
                        "Gyldig til", "Utløpt", "Gjeldende", "Søk"]) {
      assert.ok(!tekst.includes(ekte),
        `hardkodet norsk tekst i flaten: «${ekte}»`);
    }
    assert.ok(tekst.includes("PL_ui.kunnskap.tittel"));
    assert.ok(tekst.includes("PL_ui.kunnskap.status.utlopt"));
    // …men DATAENE står som de kom: kilden er kundens tekst, ikke en nøkkel.
    assert.ok(tekst.includes("kilde://intern/avtaleharmonisering"));
  } finally {
    settI18nForTest(NB, "nb");
  }
});

test("Ordliste: nb og en har hver eneste ui.kunnskap-nøkkel", async () => {
  // `t()` faller tilbake til NØKKELEN, ikke til nb — en manglende
  // engelsk nøkkel ville vist `ui.kunnskap.kolonne.kilde` midt i tabellen.
  const { readFileSync } = await import("node:fs");
  const en = JSON.parse(readFileSync(
    new URL("../../../../locales/en.json", import.meta.url), "utf-8"));
  const mine = Object.keys(NB).filter((k) => k.startsWith("ui.kunnskap.")
    || k === "ui.nav.kunnskap");
  assert.ok(mine.length >= 20, `for få nøkler i porten: ${mine.length}`);
  for (const k of mine) {
    assert.ok(typeof en[k] === "string" && en[k].trim(),
      `en.json mangler ${k}`);
  }
});
