// M-12 tilgangsflaten (097) — flateporten (jsdom + axe).
//
// Portene her måler nøyaktig det v1-dommen lover:
//   * `ui_axe_alvorlige_brudd`: null alvorlige/kritiske brudd, på hver
//     av skjermene flaten kan stå i (fullt register, tomt register,
//     leseøkt uten skjemaer).
//   * V1-DOMMEN I FLATEN: det finnes INGEN kontroll som fjerner,
//     flytter eller oppretter en tilgang i et fremmed system. De eneste
//     knappene registrerer — et objekt, en tilgang, en gjennomgang. En
//     «Fjern tilgang»-knapp som ikke gjør noe i systemet den navngir er
//     en løgn om hva plattformen kan, og den som tror en tilgang ble
//     fjernet sjekker ikke om den fortsatt finnes.
//   * EIER OG HJEMMEL ER EGNE KOLONNER. De er halve svaret på «skal
//     denne tilgangen finnes», og de skal kunne leses ved siden av
//     hverandre — ikke i en tooltip.
//   * FORFALT ER TEKST, ikke bare farge (WCAG 1.4.1).
//   * OBJEKTET VELGES, det skrives ikke inn som en UUID.
//   * En lesende økt ser registeret, men INGEN mutasjonskontroller.
//   * Ingen hardkodet tekst (pseudo-locale).
//
// Ingen delt fixture (m16-formen): hver test bygger sin egen skjerm.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visTilgang } from "../static/js/flater/tilgang.js";

settI18nForTest(NB, "nb");

const O_A = "11111111-1111-1111-1111-111111111111";
const O_B = "22222222-2222-2222-2222-222222222222";
const T_A = "aaaaaaaa-1111-1111-1111-111111111111";
const T_B = "bbbbbbbb-2222-2222-2222-222222222222";

const FULLT = {
  objekter: [
    { objekt_id: O_A, system: "Microsoft 365",
      navn: "Delt postkasse for lonn", kritikalitet: "kritisk",
      antall_tilganger: 1, opprettet: "2026-01-05T08:00:00+00:00" },
    { objekt_id: O_B, system: "Regnskapssystem",
      navn: "Hovedbok", kritikalitet: "hoy", antall_tilganger: 1,
      opprettet: "2026-01-05T08:00:00+00:00" },
  ],
  tilganger: [
    { tilgang_id: T_A, objekt_id: O_A, system: "Microsoft 365",
      objektnavn: "Delt postkasse for lonn", kritikalitet: "kritisk",
      subjekt: "ansatt.en@eksempel.test", subjekttype: "person",
      niva: "admin", eier_bruker_id: "bid_a", eier_navn: "Nora Ansvarlig",
      hjemmel: "Rollebeskrivelse lonnsansvarlig, pkt. 4",
      gjennomgang_dogn: 90, sist_gjennomgatt: null,
      sist_gjennomgatt_av: null, gjennomgang_frist: "2026-04-05",
      dogn_til_gjennomgang: -149,
      opprettet: "2026-01-05T08:00:00+00:00" },
    { tilgang_id: T_B, objekt_id: O_B, system: "Regnskapssystem",
      objektnavn: "Hovedbok", kritikalitet: "hoy",
      subjekt: "svc-fakturarobot", subjekttype: "tjenestekonto",
      niva: "skriv", eier_bruker_id: "bid_b", eier_navn: null,
      hjemmel: "Driftsavtale 2026-11, vedlegg B",
      gjennomgang_dogn: 365, sist_gjennomgatt: "2026-08-01",
      sist_gjennomgatt_av: "bid_b", gjennomgang_frist: "2027-08-01",
      dogn_til_gjennomgang: 334,
      opprettet: "2026-01-05T08:00:00+00:00" },
  ],
  funn: [
    { tilgang_id: T_A, funntype: "gjennomgang_utlopt",
      subjekt: "ansatt.en@eksempel.test", system: "Microsoft 365",
      frist: "2026-04-05", forst_sett: "2026-04-06T04:35:00+00:00",
      sist_sett_sveip: "2026-09-01T04:35:00+00:00", alder_s: 12960000 },
  ],
  request_id: "r-t",
};

const TOMT = { objekter: [], tilganger: [], funn: [], request_id: "r-0" };
//: Et objekt uten en eneste tilgang: registreringen ble halvferdig. Da
//: skal tilgangsskjemaet finnes (det ER noe å velge), men registeret
//: stå tomt med sin ærlige setning.
const BARE_OBJEKT = {
  objekter: [FULLT.objekter[0]], tilganger: [], funn: [],
  request_id: "r-o",
};

let SVAR = FULLT;
let SISTE = null;
let KALL = [];
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  if (opts && opts.method === "POST") {
    SISTE = { sti, kropp: JSON.parse(opts.body), headers: opts.headers };
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  }
  KALL.push(sti);
  if (SVAR === null) {
    return { ok: false, status: 500,
      json: async () => ({ feil: "serverfeil" }) };
  }
  return { ok: true, status: 200, json: async () => SVAR };
};

function ctx(scopes = ["security:read", "bestilling:opprett"]) {
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
  KALL = [];
  SISTE = null;
  const brett = nyttBrett();
  const m = document.createElement("main");
  m.id = "hovedinnhold"; m.tabIndex = -1;
  brett.append(m);
  return m;
}

test("Tilgang: eier og hjemmel er KOLONNER, forfalt er TEKST, axe rent",
  async () => {
    SVAR = FULLT;
    const h = nyHoved();
    visTilgang(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);

    const tabeller = [...h.querySelectorAll("table")];
    assert.equal(tabeller.length, 2, "funnlisten + tilgangsregisteret");
    for (const tb of tabeller) {
      assert.ok(tb.querySelector("caption").textContent.trim(),
        "en tabell uten caption");
      assert.ok(tb.querySelector('th[scope="col"]'));
      for (const rad of tb.querySelectorAll("tbody tr")) {
        // Første celle NAVNGIR raden: uten th scope="row" mister en
        // skjermleser i hjemmels- og fristkolonnene hvilken tilgang det
        // gjelder.
        assert.equal(rad.cells[0].tagName, "TH");
        assert.equal(rad.cells[0].getAttribute("scope"), "row");
      }
    }

    // HVER TABELL LIGGER I EN .tablewrap. Uten wrapperen gjelder
    // `width: 100%`, og nettleseren klemmer kolonnene mot min-content —
    // ett tegn per linje (eiers funn 1/9).
    assert.equal(h.querySelectorAll(".tablewrap > table").length, 2,
      "en tabell står utenfor sin .tablewrap");

    // Registeret er den ANDRE tabellen (funnene står øverst: det som
    // krever noe av noen skal leses først).
    const reg = tabeller[1];
    const kolonner = [...reg.querySelectorAll("thead th")]
      .map((e) => e.textContent);
    for (const n of ["objekt", "kritikalitet", "subjekt", "niva", "eier",
                     "hjemmel", "gjennomgang"]) {
      assert.ok(kolonner.includes(t(`ui.tilgang.kolonne.${n}`)),
        `kolonnen ${n} mangler i registeret`);
    }
    const rader = [...reg.querySelectorAll("tbody tr")];
    assert.equal(rader.length, 2);
    const celler = [...rader[0].cells].map((c) => c.textContent);
    // EIEREN og HJEMMELEN står i hver sin celle — ikke i en tooltip,
    // ikke i en «vis mer».
    assert.ok(celler.includes("Nora Ansvarlig"), "eieren står ikke i en celle");
    assert.ok(celler.includes("Rollebeskrivelse lonnsansvarlig, pkt. 4"),
      "hjemmelen står ikke i en celle");
    // …og hjemmelscellen bærer `.celle-tekst` PÅ td-en. `max-width` gjør
    // ingenting på et inline-element, så klassen på et span inni ville
    // sett riktig ut i diffen og gjort null (klynge 1s lærdom 2).
    const hjemmelscelle = [...rader[0].cells].find(
      (c) => c.textContent === "Rollebeskrivelse lonnsansvarlig, pkt. 4");
    assert.ok(hjemmelscelle.classList.contains("celle-tekst"),
      "hjemmelen mangler .celle-tekst på selve td-en");
    // Lange systemnavn og kontonavn skal BRYTE, ikke gjøre sidescrollen
    // like bred som den lengste strengen (lærdom 3).
    assert.ok(rader[0].cells[0].classList.contains("celle-id"),
      "objektcellen mangler .celle-id");

    // FORFALT SOM TEKST, aldri kun farge (WCAG 1.4.1) — og på RIKTIG rad.
    assert.ok(h.textContent.includes(t("ui.tilgang.merke_forfalt")));
    assert.ok([...rader[0].cells].some(
      (c) => c.textContent.includes(t("ui.tilgang.merke_forfalt"))),
    "«forfalt» står ikke på den forfalte raden");
    assert.ok(!rader[1].textContent.includes(t("ui.tilgang.merke_forfalt")),
      "en tilgang innenfor fristen ble merket forfalt");
    // …og den som aldri er gjennomgått sier NETTOPP det.
    assert.ok(rader[0].textContent.includes(
      t("ui.tilgang.aldri_gjennomgatt")));

    // Nivå, kritikalitet, subjekttype og funntype er OVERSATT, aldri rå
    // maskinkoder på skjermen.
    assert.ok(h.textContent.includes(t("ui.tilgang.niva.admin")));
    assert.ok(h.textContent.includes(t("ui.tilgang.kritikalitet.kritisk")));
    assert.ok(h.textContent.includes(
      t("ui.tilgang.funntype.gjennomgang_utlopt")));
    assert.ok(!h.textContent.includes("gjennomgang_utlopt"));

    // <time datetime> på tidspunktene.
    const tider = [...h.querySelectorAll("time")];
    assert.ok(tider.length >= 2);
    for (const e of tider) assert.ok(e.getAttribute("datetime"));

    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Tilgang: v1 PROVISJONERER INGENTING — ingen fjern/flytt/opprett",
  async () => {
    // 🔴 DEN BÆRENDE PORTEN I FLATEN. Katalogteksten lover JML, og v1
    // gjør ingen av delene. En knapp som ikke gjør noe i systemet den
    // navngir er en løgn om hva plattformen kan — og på akkurat dette
    // området er den farlig: den som tror en tilgang ble fjernet,
    // sjekker ikke om den fortsatt finnes.
    SVAR = FULLT;
    const h = nyHoved();
    visTilgang(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);

    const knappetekster = [...h.querySelectorAll("button")]
      .map((b) => b.textContent);
    // De ENESTE knappene registrerer.
    assert.deepEqual(new Set(knappetekster), new Set([
      t("ui.tilgang.knapp.gjennomgang"),
      t("ui.tilgang.knapp.registrer_objekt"),
      t("ui.tilgang.knapp.registrer_tilgang"),
    ]), `uventede knapper i flaten: ${knappetekster.join(", ")}`);

    // …og KILDEN bærer ingen provisjoneringsvei i det hele tatt.
    const kilde = readFileSync(new URL(
      "../static/js/flater/tilgang.js", import.meta.url), "utf8");
    for (const forbudt of ["fjernTilgang", "slettTilgang", "flyttTilgang",
                           "opprettTilgangI", "provisjoner", "revoker"]) {
      assert.ok(!kilde.includes(forbudt),
        `flaten har en provisjoneringsvei: ${forbudt}`);
    }
    // Flaten skriver KUN til de tre registreringsrutene.
    const api = readFileSync(new URL(
      "../static/js/api.js", import.meta.url), "utf8");
    const m12ruter = [...api.matchAll(/_muter\(\s*[`"](\/v1\/tilgang[^`"]*)/g)]
      .map((x) => x[1]);
    assert.deepEqual(m12ruter.sort(),
      ["/v1/tilgang", "/v1/tilgang/objekt",
        "/v1/tilgang/${encodeURIComponent(tilgangId)}/gjennomgang"].sort());
  });

test("Tilgang: gjennomgangen registreres på RADENS tilgang, uten kropp",
  async () => {
    SVAR = FULLT;
    const h = nyHoved();
    visTilgang(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);
    const knapp = [...h.querySelectorAll("button")].find(
      (b) => b.textContent === t("ui.tilgang.knapp.gjennomgang"));
    knapp.dispatchEvent(new Event("click", { bubbles: true }));
    await vent(() => SISTE !== null);
    assert.equal(SISTE.sti, `/v1/tilgang/${T_A}/gjennomgang`);
    // KROPPEN ER TOM, og det er dommen: hvem som gjennomgikk er ØKTENS
    // bruker-id (serveren tar den fra sesjonen), og datoen er basens.
    // Et felt for noen av delene ville vært en vei til å attestere i en
    // annens navn eller å tilbakedatere en frist.
    assert.deepEqual(SISTE.kropp, {});
    assert.ok(SISTE.headers["Idempotency-Key"],
      "gjennomgangen sendte ingen idempotensnøkkel");
  });

test("Tilgang: objektet VELGES, det skrives ikke inn som en UUID",
  async () => {
    SVAR = FULLT;
    const h = nyHoved();
    visTilgang(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);
    const valg = h.querySelector("select#tilgang-objekt");
    assert.ok(valg, "objektet har ikke et valg å velge fra");
    assert.deepEqual([...valg.options].map((o) => o.value), [O_A, O_B]);
    assert.equal(valg.options[0].textContent,
      "Microsoft 365 — Delt postkasse for lonn");
    // Hvert felt har en <label for> — et felt en skjermleser ikke kan
    // navngi er et felt ingen kan fylle ut.
    for (const felt of h.querySelectorAll(
      "form input, form select")) {
      const merke = h.querySelector(`label[for="${felt.id}"]`);
      assert.ok(merke && merke.textContent.trim(),
        `feltet ${felt.id || "(uten id)"} mangler etikett`);
    }
    // Skjemaene bruker rutenettet OG grupperer hvert felt for seg
    // (klynge 1s lærdom 4): uten `.felt`-diven sprer rutenettet
    // etiketten bort fra kontrollen sin.
    for (const sk of h.querySelectorAll("form")) {
      assert.ok(sk.classList.contains("kv-skjema-rutenett"), sk.className);
      assert.ok(sk.querySelectorAll(".felt").length >= 3);
      assert.ok(sk.querySelector(".skjema-bunn button[type=submit]"),
        "send-knappen står ikke i skjema-bunnen");
    }
  });

test("Tilgang: registreringen sender feltene registeret krever",
  async () => {
    SVAR = FULLT;
    const h = nyHoved();
    visTilgang(h, ctx());
    await vent(() => h.querySelector("select#tilgang-objekt") !== null);
    h.querySelector("#tilgang-subjekt").value = "ny.konto@eksempel.test";
    h.querySelector("#tilgang-eier").value = "bid_a";
    h.querySelector("#tilgang-hjemmel").value = "Vedtak 2026-3";
    h.querySelector("#tilgang-dogn").value = "180";
    const skjema = [...h.querySelectorAll("form")].find(
      (f) => f.querySelector("#tilgang-subjekt"));
    skjema.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }));
    await vent(() => SISTE !== null && SISTE.sti === "/v1/tilgang");
    assert.deepEqual(SISTE.kropp, {
      objekt_id: O_A, subjekt: "ny.konto@eksempel.test",
      subjekttype: "person", niva: "les", eier_bruker_id: "bid_a",
      hjemmel: "Vedtak 2026-3", gjennomgang_dogn: 180,
    });
    // Døgnet er et TALL, ikke en streng: serveren avviser en streng som
    // feilformet, og feilen ville dukket opp først i produksjon.
    assert.equal(typeof SISTE.kropp.gjennomgang_dogn, "number");
  });

test("Tilgang: tomtilstandene sier hva fraværet betyr", async () => {
  SVAR = TOMT;
  const h = nyHoved();
  visTilgang(h, ctx());
  await vent(() => h.textContent.includes(t("ui.tilgang.liste.ingen")));
  // Begge tomtilstandene er EKSPLISITT innhold, ikke en tom seksjon — og
  // funn-teksten sier at sveipen er det som fyller listen, så en tom
  // liste på en vert der timeren aldri har kjørt ikke leses som «i
  // orden».
  assert.ok(h.textContent.includes(t("ui.tilgang.liste.ingen")));
  assert.ok(h.textContent.includes(t("ui.tilgang.funn.ingen")));
  assert.equal(h.querySelectorAll("table").length, 0);
  // Uten et eneste objekt finnes heller ikke tilgangsskjemaet: et
  // skjema med en tom nedtrekksliste er en knapp som alltid feiler.
  assert.equal(h.querySelector("select#tilgang-objekt"), null);
  assert.ok(h.querySelector("#tilgang-system"),
    "objektskjemaet mangler — da finnes ingen vei ut av tomtilstanden");
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Tilgang: et objekt uten tilganger gir skjemaet, ikke en tabell",
  async () => {
    SVAR = BARE_OBJEKT;
    const h = nyHoved();
    visTilgang(h, ctx());
    await vent(() => h.querySelector("select#tilgang-objekt") !== null);
    assert.equal(h.querySelectorAll("table").length, 0);
    assert.ok(h.textContent.includes(t("ui.tilgang.liste.ingen")));
  });

test("Tilgang: en leseøkt ser registeret, men ingen mutasjonskontroller",
  async () => {
    SVAR = FULLT;
    const h = nyHoved();
    visTilgang(h, ctx(["security:read"]));
    await vent(() => h.querySelectorAll("table").length >= 2);
    assert.equal(h.querySelectorAll("button").length, 0,
      "en økt uten bestilling:opprett fikk mutasjonsknapper");
    assert.equal(h.querySelectorAll("form").length, 0);
    // …men registeret ER der: `security:read` skal SE hvem som har hva.
    assert.ok(h.textContent.includes("ansatt.en@eksempel.test"));
    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Tilgang: et feilet kall tegner ingen halv side", async () => {
  SVAR = null;
  const h = nyHoved();
  visTilgang(h, ctx());
  await vent(() => h.textContent.includes(t("ui.feil_tittel")));
  assert.equal(h.querySelectorAll("table").length, 0,
    "registeret ble tegnet selv om kallet feilet");
  SVAR = FULLT;
});

test("Tilgang: ingen hardkodet tekst (pseudo-locale)", async () => {
  const PL = Object.fromEntries(Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = FULLT;
    const h = nyHoved();
    visTilgang(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 2);
    const tekst = h.textContent;
    for (const ekte of ["Tilganger", "Hvem", "Nivå", "Eier", "Hjemmel",
                        "Kritisk", "Administrere", "Forfalt",
                        "Tjenestekonto"]) {
      assert.ok(!tekst.includes(ekte),
        `hardkodet norsk tekst i flaten: «${ekte}»`);
    }
    assert.ok(tekst.includes("PL_ui.tilgang.tittel"));
    assert.ok(tekst.includes("PL_ui.tilgang.niva.admin"));
    // …men DATAENE står som de kom: hjemmelen og kontonavnet er kundens
    // tekst, ikke en nøkkel.
    assert.ok(tekst.includes("Rollebeskrivelse lonnsansvarlig, pkt. 4"));
    assert.ok(tekst.includes("svc-fakturarobot"));
  } finally {
    settI18nForTest(NB, "nb");
  }
});

test("Tilgang: nb og en har hver eneste ui.tilgang-nøkkel", async () => {
  // `t()` faller tilbake til NØKKELEN, ikke til nb — en manglende
  // engelsk nøkkel ville vist `ui.tilgang.kolonne.hjemmel` midt i
  // tabellen.
  const en = JSON.parse(readFileSync(
    new URL("../../../../locales/en.json", import.meta.url), "utf-8"));
  const mine = Object.keys(NB).filter((k) => k.startsWith("ui.tilgang.")
    || k === "ui.nav.tilgang");
  assert.ok(mine.length >= 40, `for få nøkler i porten: ${mine.length}`);
  for (const k of mine) {
    assert.ok(typeof en[k] === "string" && en[k].trim(),
      `en.json mangler ${k}`);
  }
  // LENKETEKST = FLATETITTEL, i begge språk (klynge 1s lærdom 7).
  assert.equal(NB["ui.nav.tilgang"], NB["ui.tilgang.tittel"]);
  assert.equal(en["ui.nav.tilgang"], en["ui.tilgang.tittel"]);
});
