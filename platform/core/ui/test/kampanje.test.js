// M-44 kampanjeflaten (114) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd på hver
//     skjerm flaten kan stå i.
//   * `modulen_sendte`: flaten har INGEN «send»-knapp og ingen egen
//     utgående kanal.
//   * `mottaker_uten_samtykke`: samtykket vises ALDRI uten kanalen sin.
//   * `kampanje_uten_avmeldingslenke`: feltet er påkrevd og må være
//     https — og lenken vises som TEKST, ikke som noe å trykke på.
//   * `samtykkehistorikk_overskrevet`: «meld av» er et VALG i skjemaet,
//     ikke en slettknapp.
//   * `over_frekvensgrense_uten_funn`: antallet vises mot tenantens
//     eget tak, «2 av 2», aldri som en prosent.
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
  KANALER, TILSTANDER, maskeTekst, samtykkeTekst, takTekst,
  visKampanje,
} from "../static/js/flater/kampanje.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));

const M1 = "11111111-1111-1111-1111-111111111111";
const M2 = "22222222-2222-2222-2222-222222222222";
const K1 = "aaaaaaaa-1111-1111-1111-111111111111";

function locale(sprak) {
  return JSON.parse(readFileSync(
    join(HER, "..", "..", "..", "..", "locales", `${sprak}.json`),
    "utf-8"));
}

const BILDE = {
  sammendrag: {
    mottakere: 40, aktive: 12, med_samtykke: 9, kampanjer: 3,
    planlagte: 2, apne_funn: 4, apne_over_tak: 2, har_grense: true,
    grenseversjon: 2, vist: 2,
  },
  mottakere: [
    { mottaker_id: M1, ekstern_ref: "MOT-100", navn: "Kari Kunde",
      kontakt_maske: "k****@example.com", aktiv: true,
      tilstand: "gitt", kanal: "kasse",
      siste_samtykke: "2026-08-01", i_planer: 3,
      apne_funn: ["over_frekvensgrense"] },
    { mottaker_id: M2, ekstern_ref: "MOT-200", navn: "Ola Kunde",
      kontakt_maske: null, aktiv: false, tilstand: null, kanal: null,
      siste_samtykke: null, i_planer: 0,
      apne_funn: ["uten_samtykke"] },
  ],
  kampanjer: [
    { kampanje_id: K1, ekstern_ref: "KAMP-1", navn: "Høstsalg",
      formal: "salg", avmeldingslenke: "https://x.example/avmeld",
      planlagt_sendt: "2026-08-10", status: "registrert",
      mottakere: 2, opprettet: "2026-08-01T09:00:00+00:00",
      opprettet_av: "kari" },
    { kampanje_id: "aaaaaaaa-2222-2222-2222-222222222222",
      ekstern_ref: "KAMP-2", navn: "Avlyst",
      formal: "salg", avmeldingslenke: "https://x.example/avmeld2",
      planlagt_sendt: "2026-08-12", status: "avlyst", mottakere: 0,
      opprettet: "2026-08-01T09:00:00+00:00", opprettet_av: "kari" },
  ],
  grense: {
    maks_per_periode: 2, periode_dogn: 7, samtykke_gyldig_dogn: 730,
    versjon: 2, oppdatert: "2026-08-01T09:00:00+00:00",
    oppdatert_av: "kari",
  },
  request_id: "r-a",
};

const TOMT = {
  sammendrag: {
    mottakere: 0, aktive: 0, med_samtykke: 0, kampanjer: 0,
    planlagte: 0, apne_funn: 0, apne_over_tak: 0, har_grense: false,
    grenseversjon: null, vist: 0,
  },
  mottakere: [], kampanjer: [], grense: null, request_id: "r-b",
};

const HISTORIKK = {
  mottaker_id: M1,
  hendelser: [
    { hendelse_id: "h-2", tilstand: "trukket",
      kanal: "avmeldingslenke", kilde_ref: "evt_b1",
      formal: "nyhetsbrev", inntruffet: "2026-08-20",
      notat: "meldte seg av", registrert: "2026-08-20T09:00:00+00:00",
      registrert_av: "kari", endret: true },
    { hendelse_id: "h-1", tilstand: "gitt", kanal: "kasse",
      kilde_ref: "evt_a1", formal: "nyhetsbrev",
      inntruffet: "2026-08-01", notat: "avkrysset",
      registrert: "2026-08-01T09:00:00+00:00", registrert_av: "kari",
      endret: false },
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
        json: async () => ({ feil: "kampanje_ulovlig_tilstand" }) };
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
    "/v1/kampanje": BILDE,
    [`/v1/kampanje/mottaker/${M1}/samtykke`]: HISTORIKK,
    [`/v1/kampanje/mottaker/${M2}/samtykke`]: {
      mottaker_id: M2, hendelser: [], request_id: "r-d" },
  };
}

// Tabellrekkefølgen: mottakerne (0), kampanjene (1), grensen (2) og —
// når detaljpanelet står åpent — samtykkehistorikken (3).
function tabeller(h) {
  return [...h.querySelectorAll("table")];
}

async function apneForste(h) {
  await vent(() => tabeller(h).length >= 3);
  tabeller(h)[0].querySelectorAll("tbody tr")[0]
    .querySelector("button").click();
  await vent(() => tabeller(h).length >= 4);
}

// ---------------------------------------------------------------------
// mottaker_uten_samtykke — SAMTYKKET MED SIN KANAL
// ---------------------------------------------------------------------

test("Kampanje: samtykket vises aldri uten kanalen sin", () => {
  assert.equal(samtykkeTekst("gitt", "kasse"),
    t("ui.kampanje.samtykke_fra")
      .replace("{tilstand}", t("ui.kampanje.tilstand.gitt"))
      .replace("{kanal}", t("ui.kampanje.kanal.kasse")));
  // «Ingen samtykke ført» er et svar, ikke en tom celle.
  assert.equal(samtykkeTekst(null, null),
    t("ui.kampanje.uten_samtykke"));
  // EN IMPORTERT LISTE OG EN AVKRYSSING I KASSA SER FORSKJELLIGE UT —
  // og det er hele poenget: en importert liste er ofte ikke et
  // samtykke i det hele tatt.
  assert.notEqual(samtykkeTekst("gitt", "import"),
                  samtykkeTekst("gitt", "kasse"));
  assert.equal(maskeTekst(null), t("ui.kampanje.uten_kontakt"));
  assert.equal(maskeTekst("k****@example.com"), "k****@example.com");
});

test("Kampanje: antallet vises mot tenantens eget tak", () => {
  assert.equal(takTekst(2, 2),
    t("ui.kampanje.av_tak").replace("{n}", "2").replace("{maks}", "2"));
  assert.equal(takTekst(3, 2),
    t("ui.kampanje.av_tak").replace("{n}", "3").replace("{maks}", "2"));
  // UTEN ET TAK vises bare tallet — flaten finner ikke på en grense.
  assert.equal(takTekst(3, null), "3");
  assert.equal(takTekst(null, 2), "—");
});

test("Kampanje: begge språk navngir hver tilstand og hver kanal", () => {
  for (const sprak of ["nb", "en"]) {
    const tekster = locale(sprak);
    for (const s of TILSTANDER) {
      assert.ok(tekster[`ui.kampanje.tilstand.${s}`],
        `${sprak} mangler tilstanden ${s}`);
    }
    for (const k of KANALER) {
      assert.ok(tekster[`ui.kampanje.kanal.${k}`],
        `${sprak} mangler kanalen ${k}`);
    }
  }
});

// ---------------------------------------------------------------------
// modulen_sendte — flatens halvdel
// ---------------------------------------------------------------------

test("Kampanje: flaten sender ingenting", () => {
  const kilde = readFileSync(
    join(HER, "..", "static", "js", "flater", "kampanje.js"), "utf8");
  // Kommentarene forklarer FRAVÆRET og må derfor ikke telle med.
  const uten = kilde.replace(/^\s*\/\/.*$/gm, "");
  for (const ord of ["mailto:", "sendBeacon", "fetch(",
                     "XMLHttpRequest", "smtp", "utsend"]) {
    assert.ok(!uten.toLowerCase().includes(ord.toLowerCase()),
      `flaten bærer «${ord}» — v1 sender ingenting`);
  }
  const api = readFileSync(
    join(HER, "..", "static", "js", "api.js"), "utf8");
  assert.ok(!/export const (sendKampanje|utsendKampanje)/.test(api));
  // ALLE SYV SKRIVEVEIENE sender en Idempotency-Key.
  for (const n of ["settKampanjegrense", "registrerKampanjemottaker",
                   "registrerSamtykke", "registrerKampanje",
                   "avlysKampanje", "leggIKampanjeplan",
                   "settKampanjemottakerAktiv"]) {
    const i = api.indexOf(`export const ${n} =`);
    assert.ok(i > 0, `${n} mangler i api.js`);
    const j = api.indexOf("\n\n", i);
    const kropp = api.slice(i, j === -1 ? api.length : j);
    assert.ok(/idem \|\| nyIdempotensnokkel\(\)/.test(kropp),
      `${n} sender ingen Idempotency-Key`);
  }
});

// ---------------------------------------------------------------------
// Skjermene — og axe på hver av dem
// ---------------------------------------------------------------------

test("Kampanje: listen viser samtykke og tak, axe rent", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visKampanje(h, ctx());
  await vent(() => tabeller(h).length >= 3);

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
  assert.equal(rader[0].querySelector('th[scope="row"]').textContent,
    "MOT-100");
  // MASKEN, aldri adressen.
  assert.ok(rader[0].textContent.includes("k****@example.com"));
  assert.ok(!rader[0].textContent.toLowerCase().includes("nordmann"));
  // SAMTYKKET MED KANALEN.
  assert.ok(rader[0].textContent.includes(t("ui.kampanje.kanal.kasse")));
  // ANTALLET MOT TAKET: «3 av 2».
  assert.ok(rader[0].textContent.includes(
    t("ui.kampanje.av_tak").replace("{n}", "3").replace("{maks}", "2")));
  // MERKET ER TEKST (WCAG 1.4.1).
  assert.ok(rader[0].textContent.includes(
    t("ui.kampanje.merke_over_tak")));
  // EN MOTTAKER UTEN SAMTYKKE SIER DET MED ORD.
  assert.ok(rader[1].textContent.includes(
    t("ui.kampanje.uten_samtykke")));
  assert.ok(rader[1].textContent.includes(
    t("ui.kampanje.uten_kontakt")));
  assert.ok(rader[1].textContent.includes(
    t("ui.kampanje.status.inaktiv")));

  // FREKVENSBRUDDENE STÅR FOR SEG i sammendraget.
  assert.ok(h.textContent.includes(
    t("ui.kampanje.apne_over_tak").replace("{n}", "2")));
  assert.ok(h.textContent.includes(t("ui.kampanje.oversikt.hvorfor")));
});

test("Kampanje: avmeldingslenken vises som tekst, ikke som lenke",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKampanje(h, ctx());
    await vent(() => tabeller(h).length >= 3);
    const rader = [...tabeller(h)[1].querySelectorAll("tbody tr")];
    assert.equal(rader.length, 2);
    assert.ok(rader[0].textContent.includes("https://x.example/avmeld"));
    // INGEN <a> Å TRYKKE PÅ: avmeldingslenken hører til i e-posten,
    // ikke i administrasjonsflaten. En klikkbar lenke her ville vært
    // en avmelding gjort av feil person.
    assert.equal(h.querySelectorAll("a[href]").length, 0);
    // AVLYS-KNAPPEN STÅR BARE PÅ DEN SOM IKKE ER AVLYST.
    const avlysknapper = [...h.querySelectorAll("button")]
      .filter((b) => b.textContent === t("ui.kampanje.knapp.avlys"));
    assert.equal(avlysknapper.length, 1);
    assert.ok(rader[1].textContent.includes(
      t("ui.kampanje.kampanje.avlyst")));
  });

test("Kampanje: tomt register sier hva som mangler, axe rent",
  async () => {
    SVAR = { "/v1/kampanje": TOMT };
    const h = nyHoved();
    visKampanje(h, ctx());
    await vent(() => h.textContent.includes(
      t("ui.kampanje.liste.ingen")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
    assert.ok(h.textContent.includes(t("ui.kampanje.ingen_grense")));
    assert.ok(h.textContent.includes(
      t("ui.kampanje.kampanjer.ingen")));
    assert.ok(!h.textContent.includes(
      t("ui.kampanje.apne_over_tak").replace("{n}", "0")));
  });

// ---------------------------------------------------------------------
// samtykkehistorikk_overskrevet — HISTORIKKEN ER SKJERMEN
// ---------------------------------------------------------------------

test("Kampanje: historikken merker skiftet, avmeldingen står",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKampanje(h, ctx());
    await apneForste(h);

    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));

    const rader = [...tabeller(h)[3].querySelectorAll("tbody tr")];
    assert.equal(rader.length, 2);
    // NYESTE ØVERST, og skiftet er merket MED ORD.
    assert.equal(rader[0].querySelector('th[scope="row"]').textContent,
      "2026-08-20");
    assert.ok(rader[0].textContent.includes(
      t("ui.kampanje.tilstand.trukket")));
    assert.ok(rader[0].textContent.includes(
      t("ui.kampanje.merke_skifte")));
    // DEN FORRIGE LINJEN BLIR STÅENDE — avmeldingen slettet ikke
    // samtykket, den la seg oppå det. Det er dette som svarer på «hadde
    // vi lov den dagen».
    assert.ok(rader[1].textContent.includes(
      t("ui.kampanje.tilstand.gitt")));
    assert.ok(rader[1].textContent.includes(
      t("ui.kampanje.kanal.kasse")));
    assert.ok(!rader[1].textContent.includes(
      t("ui.kampanje.merke_skifte")));
    assert.ok(h.textContent.includes("MOT-100 · Kari Kunde"));
  });

test("Kampanje: «meld av» er et valg, ikke en slettknapp", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visKampanje(h, ctx());
  await apneForste(h);
  const tilstander = [...h.querySelector("#kp-s-tilstand").options]
    .map((o) => o.value);
  assert.deepEqual(tilstander, TILSTANDER);
  assert.ok(tilstander.includes("trukket"));
  // INGEN SLETTEKNAPP NOE STED.
  const knapper = [...h.querySelectorAll("button")]
    .map((b) => b.textContent);
  for (const k of knapper) {
    assert.ok(!/slett|fjern|delete/i.test(k), `slettknapp: «${k}»`);
  }
});

test("Kampanje: en mottaker uten historikk sier det", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visKampanje(h, ctx());
  await vent(() => tabeller(h).length >= 3);
  tabeller(h)[0].querySelectorAll("tbody tr")[1]
    .querySelector("button").click();
  assert.ok(await vent(() => h.textContent.includes(
    t("ui.kampanje.detalj.ingen"))), "tomheten ble aldri sagt");
  // EN DEAKTIVERT MOTTAKER LEGGES IKKE I NYE PLANER…
  const planknapp = h.querySelector("#kp-p-kampanje").closest("form")
    .querySelector("button[type=submit]");
  assert.equal(planknapp.disabled, true);
  // …MEN SAMTYKKESKJEMAET STÅR LEVENDE, fordi en avmelding alltid tas
  // imot. Å stenge det ville vært å nekte noen å trekke samtykket sitt.
  const samtykkeknapp = h.querySelector("#kp-s-tilstand")
    .closest("form").querySelector("button[type=submit]");
  assert.equal(samtykkeknapp.disabled, false);
  const aktiv = [...h.querySelectorAll("button[type=submit]")]
    .find((b) => b.textContent === t("ui.kampanje.knapp.aktiver"));
  assert.ok(aktiv, "aktiveringsknappen mangler eller bærer feil tekst");
});

test("Kampanje: en treg historikk tegnes ikke inn i et annet panel",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKampanje(h, ctx());
    await vent(() => tabeller(h).length >= 3);
    TREGE.add(`/v1/kampanje/mottaker/${M1}/samtykke`);
    const rader = [...tabeller(h)[0].querySelectorAll("tbody tr")];
    rader[0].querySelector("button").click();   // treg
    rader[1].querySelector("button").click();   // rask
    assert.ok(await vent(() => h.textContent.includes(
      t("ui.kampanje.detalj.ingen"))), "Bs tomme historikk kom aldri");
    await vent(() => false, 40);
    assert.ok(h.textContent.includes(t("ui.kampanje.detalj.ingen")),
      "den trege historikken ble tegnet inn i feil panel");
    assert.ok(!h.textContent.includes("meldte seg av"),
      "MOT-100s linjer står i MOT-200s panel");
    assert.ok(h.textContent.includes("MOT-200 · Ola Kunde"));
  });

// ---------------------------------------------------------------------
// Skriveveiene
// ---------------------------------------------------------------------

test("Kampanje: kontaktpunktet sendes én gang og tømmes etterpå",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKampanje(h, ctx());
    await vent(() => !!h.querySelector("#kp-ny-kontakt"));
    const kontakt = h.querySelector("#kp-ny-kontakt");
    // FELTET ER TOMT OG UTEN AUTOFULLFØRING.
    assert.equal(kontakt.value, "");
    assert.equal(kontakt.getAttribute("autocomplete"), "off");

    h.querySelector("#kp-ny-ref").value = "MOT-300";
    h.querySelector("#kp-ny-navn").value = "Ny Kunde";
    kontakt.value = "Kari.Nordmann@Example.COM";
    kontakt.closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE && SISTE.sti.includes("/mottaker"));
    assert.equal(SISTE.kropp.kontakt, "Kari.Nordmann@Example.COM");
    assert.ok(SISTE.headers["Idempotency-Key"]);

    // ADRESSEN BLIR IKKE STÅENDE — målt ETTER at flaten har tegnet seg
    // om, for det er den tilstanden brukeren ser.
    assert.ok(await vent(() => KALL.filter(
      (k) => k.sti === "/v1/kampanje").length >= 2),
      "flaten tegnet seg aldri om");
    assert.ok(!h.textContent.toLowerCase().includes("nordmann"));
    assert.ok(![...h.querySelectorAll("input")].some(
      (i) => i.value.toLowerCase().includes("nordmann")),
      "adressen står fortsatt i et felt");
  });

test("Kampanje: avmeldingslenken er påkrevd og må være https",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKampanje(h, ctx());
    await vent(() => !!h.querySelector("#kp-k-lenke"));
    const lenke = h.querySelector("#kp-k-lenke");
    assert.equal(lenke.required, true);
    assert.equal(lenke.type, "url");
    // NETTLESEREN GIR SAMME DOM SOM BASEN: https, ikke http.
    assert.equal(lenke.getAttribute("pattern"), "https://.+");
    for (const sprak of ["nb", "en"]) {
      const hjelp = locale(sprak)["ui.kampanje.skjema.avmelding_hjelp"];
      assert.ok(/http:|lekker|leaks/i.test(hjelp),
        `${sprak}: hjelpeteksten sier ikke hva som står på spill`);
    }
    h.querySelector("#kp-k-ref").value = "KAMP-9";
    h.querySelector("#kp-k-navn").value = "Ny";
    h.querySelector("#kp-k-formal").value = "salg";
    lenke.value = "https://x.example/av";
    h.querySelector("#kp-k-dato").value = "2026-09-01";
    lenke.closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE && SISTE.sti === "/v1/kampanje/kampanje");
    assert.equal(SISTE.kropp.avmeldingslenke, "https://x.example/av");
    assert.equal(SISTE.kropp.planlagt_sendt, "2026-09-01");
  });

test("Kampanje: samtykket sendes med kanal og formål", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visKampanje(h, ctx());
  await apneForste(h);
  const kanaler = [...h.querySelector("#kp-s-kanal").options]
    .map((o) => o.value);
  assert.deepEqual(kanaler, KANALER);
  assert.equal(h.querySelector("#kp-s-kilderef").required, true);
  assert.equal(h.querySelector("#kp-s-formal").required, true);

  h.querySelector("#kp-s-tilstand").value = "trukket";
  h.querySelector("#kp-s-kanal").value = "avmeldingslenke";
  h.querySelector("#kp-s-kilderef").value = "evt_ny";
  h.querySelector("#kp-s-formal").value = "nyhetsbrev";
  h.querySelector("#kp-s-dato").value = "2026-08-25";
  h.querySelector("#kp-s-notat").value = "meldte seg av";
  h.querySelector("#kp-s-notat").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("/samtykke"));
  assert.equal(SISTE.kropp.tilstand, "trukket");
  assert.equal(SISTE.kropp.kanal, "avmeldingslenke");
  assert.equal(SISTE.kropp.formal, "nyhetsbrev");
});

test("Kampanje: planen legger mottakeren til, avlyste er ikke valgbare",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKampanje(h, ctx());
    await apneForste(h);
    const valg = [...h.querySelector("#kp-p-kampanje").options]
      .map((o) => o.value);
    // BARE DEN SOM IKKE ER AVLYST. En avlyst kampanje går ikke.
    assert.deepEqual(valg, [K1]);
    h.querySelector("#kp-p-kampanje").closest("form")
      .dispatchEvent(new window.Event("submit", { cancelable: true }));
    await vent(() => SISTE && SISTE.sti.includes("/plan"));
    assert.ok(SISTE.sti.includes(`/v1/kampanje/kampanje/${K1}/plan`));
    assert.equal(SISTE.kropp.mottaker_id, M1);
  });

test("Kampanje: grensen er forhåndsutfylt", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visKampanje(h, ctx());
  await vent(() => !!h.querySelector("#kp-g-maks"));
  assert.equal(h.querySelector("#kp-g-maks").value, "2");
  assert.equal(h.querySelector("#kp-g-periode").value, "7");
  assert.equal(h.querySelector("#kp-g-gyldig").value, "730");
  h.querySelector("#kp-g-maks").value = "1";
  h.querySelector("#kp-g-maks").closest("form")
    .dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => SISTE && SISTE.sti.includes("/grense"));
  assert.equal(SISTE.kropp.maks_per_periode, 1);
  assert.equal(SISTE.kropp.periode_dogn, 7);
  for (const sprak of ["nb", "en"]) {
    const hjelp = locale(sprak)["ui.kampanje.grense.maks_hjelp"];
    assert.ok(/forslag|suggestion/i.test(hjelp),
      `${sprak}: hjelpeteksten sier ikke at malens tall er et forslag`);
  }
});

test("Kampanje: en 409 sier hva registeret nektet", async () => {
  SVAR = fullSvar();
  const h = nyHoved();
  visKampanje(h, ctx());
  await vent(() => !!h.querySelector("#kp-ny-ref"));
  SVARSTATUS = 409;
  h.querySelector("#kp-ny-ref").value = "MOT-100";
  h.querySelector("#kp-ny-navn").value = "Dublett";
  h.querySelector("#kp-ny-kontakt").value = "a@b.example";
  const skjema = h.querySelector("#kp-ny-ref").closest("form");
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.ok(await vent(() => h.textContent.includes(
    t("ui.kampanje.feil.tilstand"))), "tilstandsfeilen ble ikke vist");
  assert.ok(!h.textContent.includes(t("ui.kampanje.feil.generell")));
  assert.ok(h.querySelector('[role="alert"]'));
  assert.equal(skjema.querySelector("button[type=submit]").disabled,
               false);
});

// ---------------------------------------------------------------------
// Scope og språk
// ---------------------------------------------------------------------

test("Kampanje: en lesende økt ser registeret, men ingen kontroller",
  async () => {
    SVAR = fullSvar();
    const h = nyHoved();
    visKampanje(h, ctx(["okonomi:read"]));
    await vent(() => tabeller(h).length >= 3);
    assert.ok(h.textContent.includes("MOT-100"));
    assert.ok(h.textContent.includes("k****@example.com"));
    assert.equal(h.querySelectorAll("form").length, 0);
    assert.equal(h.querySelectorAll("input, select, textarea").length, 0);
    // …OG INGEN AVLYS-KNAPP.
    assert.ok(![...h.querySelectorAll("button")].some(
      (b) => b.textContent === t("ui.kampanje.knapp.avlys")));
    assert.ok([...h.querySelectorAll("button")].some(
      (b) => b.textContent === t("ui.kampanje.knapp.apne")));
    const brudd = await alvorligeBrudd(h);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Kampanje: ingen hardkodet tekst", async () => {
  const PL = Object.fromEntries(
    Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = fullSvar();
    const h = nyHoved();
    visKampanje(h, ctx());
    await apneForste(h);
    // `th[scope="row"]` er UTELATT: den cellen bærer referansen, datoen
    // og grensenavnet — altså tenantens egne data. `option` likeså i
    // kampanjevelgeren, som viser tenantens egne kampanjereferanser.
    for (const node of h.querySelectorAll(
      'h2, h3, h4, label, caption, th[scope="col"], button')) {
      const s = node.textContent.trim();
      if (!s) continue;
      assert.ok(s.startsWith("PL_"), `hardkodet tekst: «${s}»`);
    }
    for (const node of h.querySelectorAll(
      "#kp-s-tilstand option, #kp-s-kanal option")) {
      assert.ok(node.textContent.trim().startsWith("PL_"),
        `hardkodet tekst: «${node.textContent}»`);
    }
  } finally {
    settI18nForTest(NB, "nb");
  }
});
