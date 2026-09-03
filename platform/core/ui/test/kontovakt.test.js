// M-42 kontovaktflaten (110) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd på hver
//     skjerm flaten kan stå i.
//   * `modulen_stoppet_betaling` og
//     `modulen_verifiserte_mot_ekstern_kanal`: flaten har INGEN «sperr
//     betaling»-knapp og gjør ingen bankoppslag.
//   * `kontohistorikk_overskrevet`: HISTORIKKEN ER SKJERMEN. Hver
//     oppgitt konto står, nyeste først, og byttet er merket med ORD.
//   * `verifikasjon_uten_menneske_og_metode`: metode og «verifisert av»
//     er påkrevd, og feltet er ALDRI forhåndsutfylt med den som oppga
//     kontoen — det ville invitert til nettopp den selvverifikasjonen
//     basen nekter.
//   * KONTONUMMERET VISES ALDRI, og feltet tømmes etter innsending.
//   * «Ingen konto ført» og «ikke verifisert» er TEKST, ikke tomme
//     celler (WCAG 1.4.1 og alminnelig ærlighet).
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
  KANALER, METODER, maskeTekst, verifikasjonTekst, visKontovakt,
} from "../static/js/flater/kontovakt.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));

const M1 = "11111111-1111-1111-1111-111111111111";
const M2 = "22222222-2222-2222-2222-222222222222";
const O1 = "aaaaaaaa-1111-1111-1111-111111111111";

function locale(sprak) {
  return JSON.parse(readFileSync(
    join(HER, "..", "..", "..", "..", "locales", `${sprak}.json`),
    "utf-8"));
}

const BILDE = {
  sammendrag: {
    mottakere: 40, aktive: 12, med_konto: 9, verifiserte: 6,
    apne_funn: 4, apne_endringer: 2, har_terskel: true,
    terskelversjon: 2, vist: 2,
  },
  mottakere: [
    { mottaker_id: M1, ekstern_ref: "LEV-100", navn: "Byggmester AS",
      aktiv: true, kontonummer_maske: "*******2233",
      oppgitt_av: "Kari hos motparten", oppgitt_kanal: "epost",
      oppgitt_dato: "2026-03-01", verifisert_av: null, metode: null,
      verifisert_dato: null, oppgaver: 2,
      apne_funn: ["kontoendring"] },
    { mottaker_id: M2, ekstern_ref: "LEV-200", navn: "Rørlegger AS",
      aktiv: false, kontonummer_maske: null, oppgitt_av: null,
      oppgitt_kanal: null, oppgitt_dato: null, verifisert_av: null,
      metode: null, verifisert_dato: null, oppgaver: 0,
      apne_funn: ["uverifisert_konto"] },
  ],
  terskler: {
    reverifikasjon_dogn: 365, uverifisert_dogn: 7, versjon: 2,
    oppdatert: "2026-08-01T09:00:00+00:00", oppdatert_av: "kari",
  },
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    mottakere: 0, aktive: 0, med_konto: 0, verifiserte: 0,
    apne_funn: 0, apne_endringer: 0, har_terskel: false,
    terskelversjon: null, vist: 0,
  },
  mottakere: [], terskler: null, request_id: "r-b",
};

const HISTORIKK = {
  mottaker_id: M1,
  oppgaver: [
    { oppgave_id: O1, kontonummer_maske: "*******2233",
      oppgitt_av: "Kari hos motparten", oppgitt_kanal: "epost",
      oppgitt_dato: "2026-03-01", notat: "ny konto i e-post",
      registrert: "2026-03-01T09:00:00+00:00", registrert_av: "ola",
      verifisert_av: null, metode: null, verifisert_dato: null,
      verifikasjonsnotat: null, endret: true },
    { oppgave_id: "aaaaaaaa-2222-2222-2222-222222222222",
      kontonummer_maske: "*******8903",
      oppgitt_av: "Kari hos motparten", oppgitt_kanal: "faktura",
      oppgitt_dato: "2026-01-10", notat: "fra faktura",
      registrert: "2026-01-10T09:00:00+00:00", registrert_av: "ola",
      verifisert_av: "Ola Hansen", metode: "ringte_kjent_nummer",
      verifisert_dato: "2026-01-11",
      verifikasjonsnotat: "ringte kjent nummer", endret: false },
  ],
  request_id: "r-c",
};

let SVAR;
let SISTE;
let KALL;
let SVARSTATUS;
let TREGE;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  KALL.push({ sti, metode: (opts && opts.method) || "GET" });
  if (TREGE && TREGE.has(sti)) {
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setTimeout(r, 0));
    }
  }
  if (opts && opts.method === "POST") {
    SISTE = { sti, kropp: JSON.parse(opts.body), headers: opts.headers };
    if (SVARSTATUS && SVARSTATUS !== 200) {
      return { ok: false, status: SVARSTATUS,
        json: async () => ({ feil: "kontovakt_ulovlig_tilstand" }) };
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
  KALL = [];
  SISTE = undefined;
  SVARSTATUS = 200;
  TREGE = new Set();
  return m;
}

function fullSvar() {
  return {
    "/v1/kontovakt": BILDE,
    [`/v1/kontovakt/${M1}/historikk`]: HISTORIKK,
    [`/v1/kontovakt/${M2}/historikk`]: { mottaker_id: M2, oppgaver: [],
                                         request_id: "r-d" },
  };
}

// Tabellrekkefølgen: mottakerne (0), tersklene (1) og — når
// detaljpanelet står åpent — historikken (2).
function tabeller(h) {
  return [...h.querySelectorAll("table")];
}

async function apneForste(h) {
  await vent(() => tabeller(h).length >= 2);
  tabeller(h)[0].querySelectorAll("tbody tr")[0]
    .querySelector("button").click();
  await vent(() => tabeller(h).length >= 3);
}

// ---------------------------------------------------------------------
// Ordene for det som IKKE finnes
// ---------------------------------------------------------------------

test("Kontovakt: «ingen konto ført» er ikke en tom celle", () => {
  assert.equal(maskeTekst("*******2233"), "*******2233");
  // EN TOM CELLE VILLE SETT UT SOM MANGLENDE DATA der den betyr «ingen
  // har oppgitt en konto».
  assert.equal(maskeTekst(null), t("ui.kontovakt.uten_konto"));
  assert.equal(maskeTekst(""), t("ui.kontovakt.uten_konto"));
  assert.equal(maskeTekst(undefined), t("ui.kontovakt.uten_konto"));
});

test("Kontovakt: «ikke verifisert» er et svar", () => {
  assert.equal(
    verifikasjonTekst("Ola Hansen", "ringte_kjent_nummer", "2026-01-11"),
    t("ui.kontovakt.verifisert_av").replace("{av}", "Ola Hansen")
      .replace("{metode}", t("ui.kontovakt.metode.ringte_kjent_nummer"))
      .replace("{dato}", "2026-01-11"));
  // …og det er nettopp den tilstanden `konto_verifisert` skal kunne
  // benekte.
  assert.equal(verifikasjonTekst(null, null, null),
    t("ui.kontovakt.ikke_verifisert"));
  assert.equal(verifikasjonTekst("Ola", "annet", null),
    t("ui.kontovakt.ikke_verifisert"));
});

test("Kontovakt: begge språk navngir hver kanal og hver metode", () => {
  for (const sprak of ["nb", "en"]) {
    const tekster = locale(sprak);
    for (const k of KANALER) {
      assert.ok(tekster[`ui.kontovakt.kanal.${k}`],
        `${sprak} mangler kanalen ${k}`);
    }
    for (const m of METODER) {
      assert.ok(tekster[`ui.kontovakt.metode.${m}`],
        `${sprak} mangler metoden ${m}`);
    }
    // …og METODEN SOM IKKE KAN FORFALSKES sier hva den er.
    assert.ok(/fra før|already had/i.test(
      tekster["ui.kontovakt.metode.ringte_kjent_nummer"]), sprak);
  }
});

// ---------------------------------------------------------------------
// modulen_stoppet_betaling / modulen_verifiserte_mot_ekstern_kanal
// ---------------------------------------------------------------------

test("Kontovakt: flaten sperrer ingenting og slår ikke opp i en bank",
  () => {
    const kilde = readFileSync(
      join(HER, "..", "static", "js", "flater", "kontovakt.js"), "utf8");
    // Kommentarene forklarer FRAVÆRET og må derfor ikke telle med.
    const uten = kilde.replace(/^\s*\/\/.*$/gm, "");
    for (const ord of ["sperr", "blokker", "stoppBetaling", "attester",
                       "bankapi", "fetch(", "XMLHttpRequest"]) {
      assert.ok(!uten.toLowerCase().includes(ord.toLowerCase()),
        `flaten bærer «${ord}» — v1 stopper ingen betaling`);
    }
    // `signer(?!t_dokument)`: «signert dokument» er en av
    // VERIFIKASJONSMETODENE — noe et menneske holder i hånda. Å SIGNERE
    // er handlingen v1 ikke gjør.
    assert.ok(!/signer(?!t_dokument)/i.test(uten),
      "flaten bærer en signeringshandling");
    const api = readFileSync(
      join(HER, "..", "static", "js", "api.js"), "utf8");
    assert.ok(!/export const (sperrBetaling|blokkerMottaker)/.test(api));
    // …og ALLE FEM SKRIVEVEIENE sender en Idempotency-Key.
    for (const n of ["settKontoterskler", "registrerMottaker",
                     "oppgiKonto", "verifiserKonto",
                     "settMottakerAktiv"]) {
      const i = api.indexOf(`export const ${n} =`);
      assert.ok(i > 0, `${n} mangler i api.js`);
      const kropp = api.slice(i, api.indexOf("\n\n", i));
      assert.ok(/idem \|\| nyIdempotensnokkel\(\)/.test(kropp),
        `${n} sender ingen Idempotency-Key`);
    }
  });

// ---------------------------------------------------------------------
// Skjermene — og axe på hver av dem
// ---------------------------------------------------------------------

test("Kontovakt: listen viser konto, kanal og merker, axe rent",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKontovakt(h, ctx());
    await vent(() => tabeller(h).length >= 2);

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));

    for (const tb of tabeller(h)) {
      assert.ok(tb.querySelector("caption"), "tabell uten caption");
      assert.ok(tb.querySelectorAll('th[scope="col"]').length >= 2);
      assert.ok(tb.closest(".tablewrap"),
        "tabellen mangler sidescrollens container");
    }

    const rader = [...tabeller(h)[0].querySelectorAll("tbody tr")];
    assert.equal(rader.length, 2);
    // REFERANSEN NAVNGIR RADEN — det er den en faktura siterer.
    assert.equal(rader[0].querySelector('th[scope="row"]').textContent,
      "LEV-100");
    assert.ok(rader[0].textContent.includes("*******2233"));
    assert.ok(rader[0].textContent.includes(
      t("ui.kontovakt.kanal.epost")));
    assert.ok(rader[0].textContent.includes("Kari hos motparten"));
    // MERKET ER TEKST (WCAG 1.4.1).
    assert.ok(rader[0].textContent.includes(
      t("ui.kontovakt.merke_endring")));
    assert.ok(rader[0].textContent.includes(
      t("ui.kontovakt.ikke_verifisert")));
    // EN MOTTAKER UTEN KONTO SIER DET MED ORD.
    assert.ok(rader[1].textContent.includes(t("ui.kontovakt.uten_konto")));
    assert.ok(rader[1].textContent.includes(
      t("ui.kontovakt.status.inaktiv")));

    // KONTOENDRINGENE STÅR FOR SEG — et tall som druknet i «åpne funn»
    // ville vært usynlig.
    assert.ok(h.textContent.includes(
      t("ui.kontovakt.apne_endringer").replace("{n}", "2")));
    assert.ok(h.textContent.includes(
      t("ui.kontovakt.avkortet").replace("{vist}", "2")));
    assert.ok(!h.textContent.includes(t("ui.kontovakt.ingen_terskler")));
    assert.ok(h.textContent.includes(t("ui.kontovakt.oversikt.hvorfor")));
  });

test("Kontovakt: tomt register sier hva som mangler, axe rent",
  async () => {
    SVAR = { "/v1/kontovakt": TOMT };
    const h = nyHoved();
    visKontovakt(h, ctx());
    await vent(() => h.textContent.includes(
      t("ui.kontovakt.liste.ingen")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
    assert.ok(h.textContent.includes(t("ui.kontovakt.ingen_terskler")));
    // …og ingen «N kontoendringer» når det ikke er noen.
    assert.ok(!h.textContent.includes(
      t("ui.kontovakt.apne_endringer").replace("{n}", "0")));
  });

// ---------------------------------------------------------------------
// kontohistorikk_overskrevet — HISTORIKKEN ER SKJERMEN
// ---------------------------------------------------------------------

test("Kontovakt: historikken viser hver konto og merker byttet",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKontovakt(h, ctx());
    await apneForste(h);

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));

    const rader = [...tabeller(h)[2].querySelectorAll("tbody tr")];
    assert.equal(rader.length, 2);
    // NYESTE ØVERST, og BYTTET ER MERKET MED ORD. Uten det måtte
    // leseren sammenligne maskene selv — og en maske kan gjenta seg.
    assert.equal(rader[0].querySelector('th[scope="row"]').textContent,
      "2026-03-01");
    assert.ok(rader[0].textContent.includes("*******2233"));
    assert.ok(rader[0].textContent.includes(
      t("ui.kontovakt.merke_endring")));
    assert.ok(rader[0].textContent.includes(
      t("ui.kontovakt.ikke_verifisert")));
    assert.ok(rader[0].textContent.includes("ny konto i e-post"));
    // DEN FORRIGE LINJEN BLIR STÅENDE, med sin verifikasjon.
    assert.ok(rader[1].textContent.includes("*******8903"));
    assert.ok(rader[1].textContent.includes("Ola Hansen"));
    assert.ok(rader[1].textContent.includes(
      t("ui.kontovakt.metode.ringte_kjent_nummer")));
    assert.ok(!rader[1].textContent.includes(
      t("ui.kontovakt.merke_endring")));
    assert.ok(h.textContent.includes("LEV-100 · Byggmester AS"));
  });

test("Kontovakt: en mottaker uten konto sier det, og kan ikke verifiseres",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKontovakt(h, ctx());
    await vent(() => tabeller(h).length >= 2);
    tabeller(h)[0].querySelectorAll("tbody tr")[1]
      .querySelector("button").click();
    assert.ok(await vent(() => h.textContent.includes(
      t("ui.kontovakt.detalj.ingen"))), "tomheten ble aldri sagt");
    // DET FINNES INGENTING Å VERIFISERE før en konto er ført…
    const ver = h.querySelector("#ko-ver-av").closest("form")
      .querySelector("button[type=submit]");
    assert.equal(ver.disabled, true);
    // …og en DEAKTIVERT mottaker tar ikke imot nye kontoer.
    const konto = h.querySelector("#ko-konto-nummer").closest("form")
      .querySelector("button[type=submit]");
    assert.equal(konto.disabled, true);
    // …men den KAN aktiveres igjen.
    const aktiv = [...h.querySelectorAll("button[type=submit]")]
      .find((b) => b.textContent === t("ui.kontovakt.knapp.aktiver"));
    assert.ok(aktiv, "aktiveringsknappen mangler eller bærer feil tekst");
    assert.equal(aktiv.disabled, false);
  });

test("Kontovakt: en treg historikk tegnes ikke inn i en annen parts panel",
  async () => {
    // Åpner noen mottaker B mens As historikk er underveis, ville As
    // linjer blitt tegnet inn i Bs panel — altså en kontohistorikk som
    // ser ut til å høre til en annen part (109s lærdom).
    SVAR = fullSvar();
    const h = nyHoved();
    visKontovakt(h, ctx());
    await vent(() => tabeller(h).length >= 2);
    TREGE.add(`/v1/kontovakt/${M1}/historikk`);
    const rader = [...tabeller(h)[0].querySelectorAll("tbody tr")];
    rader[0].querySelector("button").click();   // treg
    rader[1].querySelector("button").click();   // rask
    assert.ok(await vent(() => h.textContent.includes(
      t("ui.kontovakt.detalj.ingen"))), "Bs tomme historikk kom aldri");
    await vent(() => false, 40);
    assert.ok(h.textContent.includes(t("ui.kontovakt.detalj.ingen")),
      "den trege historikken ble tegnet inn i feil parts panel");
    assert.ok(!h.textContent.includes("ny konto i e-post"),
      "LEV-100s linjer står i LEV-200s panel");
    assert.ok(h.textContent.includes("LEV-200 · Rørlegger AS"));
  });

// ---------------------------------------------------------------------
// Skriveveiene
// ---------------------------------------------------------------------

test("Kontovakt: kontonummeret sendes én gang og tømmes etterpå",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKontovakt(h, ctx());
    await apneForste(h);
    const nummer = h.querySelector("#ko-konto-nummer");
    // FELTET ER TOMT OG UTEN AUTOFULLFØRING.
    assert.equal(nummer.value, "");
    assert.equal(nummer.getAttribute("autocomplete"), "off");
    nummer.value = "1234.56.78903";
    h.querySelector("#ko-konto-av").value = "Kari hos motparten";
    h.querySelector("#ko-konto-kanal").value = "epost";
    h.querySelector("#ko-konto-dato").value = "2026-03-01";
    h.querySelector("#ko-konto-notat").value = "ny konto i e-post";
    nummer.closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE && SISTE.sti.includes("/konto"));
    assert.equal(SISTE.kropp.kontonummer, "1234.56.78903");
    assert.equal(SISTE.kropp.oppgitt_kanal, "epost");
    assert.ok(SISTE.headers["Idempotency-Key"]);
    // NUMMERET BLIR IKKE STÅENDE I SKJERMBILDET. Målt ETTER at flaten
    // har tegnet seg om — det er den tilstanden brukeren ser.
    assert.ok(await vent(() => KALL.filter(
      (k) => k.sti === "/v1/kontovakt").length >= 2),
      "flaten tegnet seg aldri om");
    assert.ok(!h.textContent.includes("1234.56.78903"));
    assert.ok(![...h.querySelectorAll("input")].some(
      (i) => i.value.includes("78903")),
      "kontonummeret står fortsatt i et felt");
  });

test("Kontovakt: verifikasjonen fylles aldri ut med den som oppga kontoen",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKontovakt(h, ctx());
    await apneForste(h);
    // EN FORHÅNDSUTFYLLING HER VILLE INVITERT til nettopp den
    // selvverifikasjonen basen nekter.
    assert.equal(h.querySelector("#ko-ver-av").value, "");
    assert.equal(h.querySelector("#ko-ver-av").required, true);
    assert.equal(h.querySelector("#ko-ver-notat").required, true);
    const metoder = [...h.querySelector("#ko-ver-metode").options]
      .map((o) => o.value);
    assert.deepEqual(metoder, METODER);
    for (const sprak of ["nb", "en"]) {
      const hjelp = locale(sprak)["ui.kontovakt.skjema.verifisert_av_hjelp"];
      assert.ok(/kan ikke verifisere|cannot verify/i.test(hjelp),
        `${sprak}: hjelpeteksten sier ikke hva regelen er`);
    }
    h.querySelector("#ko-ver-av").value = "Ola Hansen";
    h.querySelector("#ko-ver-notat").value = "ringte kjent nummer";
    h.querySelector("#ko-ver-dato").value = "2026-03-02";
    h.querySelector("#ko-ver-av").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE && SISTE.sti.includes("/verifikasjon"));
    // VERIFIKASJONEN GJELDER DEN SISTE OPPGAVEN.
    assert.ok(SISTE.sti.includes(O1), SISTE.sti);
    assert.equal(SISTE.kropp.verifisert_av, "Ola Hansen");
    assert.equal(SISTE.kropp.metode, "ringte_kjent_nummer");
  });

test("Kontovakt: grensene er forhåndsutfylt og sendes som døgn",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKontovakt(h, ctx());
    await vent(() => !!h.querySelector("#ko-t-rever"));
    assert.equal(h.querySelector("#ko-t-rever").value, "365");
    assert.equal(h.querySelector("#ko-t-uver").value, "7");
    h.querySelector("#ko-t-rever").value = "180";
    h.querySelector("#ko-t-rever").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE && SISTE.sti.includes("/terskler"));
    assert.equal(SISTE.kropp.reverifikasjon_dogn, 180);
    assert.equal(SISTE.kropp.uverifisert_dogn, 7);
  });

test("Kontovakt: kvitteringen og panelet overlever tegningen",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKontovakt(h, ctx());
    await apneForste(h);
    h.querySelector("#ko-konto-nummer").value = "1234.56.78903";
    h.querySelector("#ko-konto-av").value = "Kari";
    h.querySelector("#ko-konto-dato").value = "2026-03-01";
    h.querySelector("#ko-konto-notat").value = "x";
    h.querySelector("#ko-konto-nummer").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    assert.ok(await vent(() => KALL.filter(
      (k) => k.sti.includes("/historikk")).length >= 2),
      "panelet ble aldri gjenåpnet — porten måler ingenting");
    assert.ok(h.textContent.includes(t("ui.kontovakt.skjema.konto_ok")),
      "kvitteringen forsvant i tegningen");
    assert.ok(h.textContent.includes("LEV-100 · Byggmester AS"),
      "panelet lukket seg etter en føring");
  });

test("Kontovakt: en 409 sier hva registeret nektet", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visKontovakt(h, ctx());
  await vent(() => !!h.querySelector("#ko-ny-ref"));
  SVARSTATUS = 409;
  h.querySelector("#ko-ny-ref").value = "LEV-100";
  h.querySelector("#ko-ny-navn").value = "Dublett";
  const skjema = h.querySelector("#ko-ny-ref").closest("form");
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.ok(await vent(() => h.textContent.includes(
    t("ui.kontovakt.feil.tilstand"))), "tilstandsfeilen ble ikke vist");
  assert.ok(!h.textContent.includes(t("ui.kontovakt.feil.generell")));
  assert.ok(h.querySelector('[role="alert"]'));
  assert.equal(skjema.querySelector("button[type=submit]").disabled, false);
});

// ---------------------------------------------------------------------
// Scope og språk
// ---------------------------------------------------------------------

test("Kontovakt: en lesende økt ser registeret, men ingen kontroller",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKontovakt(h, ctx(["okonomi:read"]));
    await vent(() => tabeller(h).length >= 2);
    assert.ok(h.textContent.includes("LEV-100"));
    assert.ok(h.textContent.includes("*******2233"));
    assert.equal(h.querySelectorAll("form").length, 0);
    assert.equal(h.querySelectorAll("input, select, textarea").length, 0);
    // …men historikken er en LESNING, og den står åpen.
    assert.ok([...h.querySelectorAll("button")].some(
      (b) => b.textContent === t("ui.kontovakt.knapp.apne")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Kontovakt: ingen hardkodet tekst", async () => {
  const PL = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visKontovakt(h, ctx());
    await apneForste(h);
    // `th[scope="row"]` er UTELATT: den cellen bærer referansen, datoen
    // og grensenavnet — altså tenantens egne data.
    for (const node of h.querySelectorAll(
      'h2, h3, h4, label, caption, th[scope="col"], button, option')) {
      const s = node.textContent.trim();
      if (!s) continue;
      assert.ok(s.startsWith("PL_"), `hardkodet tekst: «${s}»`);
    }
  } finally {
    settI18nForTest(NB, "nb");
  }
});
