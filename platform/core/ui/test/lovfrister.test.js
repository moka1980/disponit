// Lovpålagte frister (237): kundens flate og plattformeierens katalog
// (jsdom + axe).
//
// MUTASJONER SOM DREPER DENNE: la oppslaget ikke fylle valgene (kunden
// må gjette organisasjonsformen), send «levert» uten kvittering, vis
// katalogen for en som ikke er plattformeier.
import { test } from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visLovfrister, statusTekst } from "../static/js/flater/lovfrister.js";
import { pliktkatalogseksjon } from "../static/js/flater/admin.js";
import { fristkort } from "../static/js/flater/oversikt.js";

settI18nForTest(NB, "nb");

const F = (n, tittel, periode, frist, status = "apen") => ({
  nokkel: n, tittel, periode, frist, myndighet: "skatteetaten",
  hjemmel: "Skatteforvaltningsforskriften § 8-3-10",
  kilde_url: "https://www.skatteetaten.no/", plikt_id: `${n}-${frist}`,
  registrert: true, status, dogn_til_frist: null, kvittering_ref: null });

let SVAR;
let EIER = true;
const POSTER = [];
globalThis.fetch = async (url, opts = {}) => {
  const sti = url.split("?")[0];
  const ok = (d) => ({ ok: true, status: 200, json: async () => d });
  if (opts.method === "POST") {
    const kropp = JSON.parse(opts.body);
    POSTER.push({ sti, kropp });
    if (sti === "/v1/lovfrister/oppslag") {
      return ok({ forslag: { orgnummer: kropp.orgnummer, navn: "AKME AS",
        orgform: "AS", mva: true, ansatte: false } });
    }
    if (sti === "/v1/lovfrister") {
      SVAR = { ...SVAR, profil: { ...kropp, oppdatert: "2026-11-27" },
               frister: [F("mva_termin", "MVA-melding", "1. termin 2099",
                           "2099-04-12")], nye_i_katalogen: 0, nye: 1 };
      return ok(SVAR);
    }
    if (sti.startsWith("/v1/plattform/pliktkatalog")) {
      return ok({ maler: [{ nokkel: "mva_termin", tittel: "MVA-melding",
        myndighet: "skatteetaten", hjemmel: "h", kilde_url: "https://x.no/",
        orgformer: [], krever_mva: true, krever_ansatte: false, aktiv: true,
        frister: [{ periode: kropp.periode || "x", frist: kropp.frist }] }] });
    }
    return ok({ ok: true });
  }
  if (sti === "/v1/lovfrister") return ok(SVAR);
  if (sti === "/v1/plattform/meg") return ok({ eier: EIER });
  if (sti === "/v1/plattform/pliktkatalog") {
    return ok({ myndigheter: ["skatteetaten", "brreg"], maler: [{
      nokkel: "mva_termin", tittel: "MVA-melding", myndighet: "skatteetaten",
      hjemmel: "Skatteforvaltningsforskriften § 8-3-10",
      kilde_url: "https://x.no/", orgformer: [], krever_mva: true,
      krever_ansatte: false, aktiv: true,
      frister: [{ periode: "1. termin 2027", frist: "2027-04-12" }] }] });
  }
  return { ok: false, status: 404, json: async () => ({ feil: "x" }) };
};

async function vent(pred, n = 200) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}
const ctx = { sprak: "nb", scopes: ["decisions:read", "bestilling:opprett"],
  tenant: "akme", paaUautorisert: () => {} };
const nyHoved = () => {
  const m = document.createElement("main");
  nyttBrett().append(m);
  return m;
};

test("Frister: statusen er ord, og forfalt er forfalt", () => {
  const idag = new Date(2026, 10, 27);
  assert.equal(statusTekst(F("a", "A", "p", "2026-11-20"), idag)[0],
               t("ui.lovfrister.status.forfalt").replace("{n}", "7"));
  assert.equal(statusTekst(F("a", "A", "p", "2026-11-27"), idag)[0],
               t("ui.lovfrister.status.i_dag"));
  assert.equal(statusTekst(F("a", "A", "p", "2026-12-07", "lukket"), idag)[0],
               t("ui.lovfrister.status.levert"));
});

test("Frister: første gang — org.nr., oppslag fyller valgene, ett trykk lager",
  async () => {
    SVAR = { profil: null, frister: [], nye_i_katalogen: 0,
             orgnummer_firma: "923609016" };
    POSTER.length = 0;
    const m = nyHoved();
    visLovfrister(m, ctx);
    assert.ok(await vent(() => m.querySelector("#lf-orgnr")));
    assert.equal(m.querySelector("#lf-orgnr").value, "923609016",
      "firmaets eget org.nr. er forhåndsutfylt");
    // OPPSLAGET GÅR AV SEG SELV når org.nr. er kjent: hun trykker ikke
    // «Hent» for å bekrefte et nummer som alt står der.
    assert.ok(await vent(() => m.querySelector("#lf-mva").checked),
      "oppslaget gikk ikke av seg selv");
    assert.equal(POSTER.filter((p) => p.sti === "/v1/lovfrister/oppslag")
      .length, 1);
    assert.equal(m.querySelector("#lf-orgform").value, "as");
    assert.equal(m.querySelector("#lf-ansatte").checked, false);
    m.querySelector("form").requestSubmit();
    assert.ok(await vent(() => POSTER.some((p) => p.sti === "/v1/lovfrister")));
    assert.deepEqual(POSTER.find((p) => p.sti === "/v1/lovfrister").kropp, {
      orgnummer: "923609016", orgform: "AS", mva: true, ansatte: false,
      kilde: "brreg" });
    assert.ok(await vent(() => m.querySelector(".lf-rad")));
    const brudd = await alvorligeBrudd(m);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Frister: «Marker som levert» krever kvittering og går til M-21",
  async () => {
    SVAR = { profil: { orgform: "AS", mva: true, ansatte: true,
                       kilde: "brreg", orgnummer: "923609016" },
             frister: [F("mva_termin", "MVA-melding", "1. termin 2099",
                         "2099-04-12")],
             nye_i_katalogen: 0, orgnummer_firma: "923609016" };
    POSTER.length = 0;
    const m = nyHoved();
    visLovfrister(m, ctx);
    assert.ok(await vent(() => m.querySelector(".lf-rad")));
    const knapp = [...m.querySelectorAll(".lf-rad button")]
      .find((b) => b.textContent === t("ui.lovfrister.levert"));
    knapp.click();
    const lagre = [...m.querySelectorAll(".lf-rad button")]
      .find((b) => b.textContent === t("ui.lovfrister.lagre_levert"));
    lagre.click();
    assert.equal(POSTER.length, 0, "levert uten kvittering ble sendt");
    m.querySelector(".lf-rad input").value = "AR123456";
    lagre.click();
    assert.ok(await vent(() => POSTER.length === 1));
    assert.equal(POSTER[0].sti, "/v1/plikt/mva_termin-2099-04-12/lukk");
    assert.deepEqual(POSTER[0].kropp, { kvittering_ref: "AR123456" });
    const brudd = await alvorligeBrudd(m);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Pliktkatalog: skjult for andre, og eieren legger til en frist",
  async () => {
    EIER = false;
    const skjult = pliktkatalogseksjon(ctx);
    await vent(() => false, 20);
    assert.equal(skjult.hidden, true);
    EIER = true;
    POSTER.length = 0;
    const m = nyHoved();
    const s = pliktkatalogseksjon(ctx);
    m.append(s);
    assert.ok(await vent(() => !s.hidden && s.querySelector(".pk-mal")));
    s.querySelector("#pk-mva_termin-dato").value = "2028-04-10";
    s.querySelector("#pk-mva_termin-periode").value = "1. termin 2028";
    s.querySelector(".pk-mal form").requestSubmit();
    assert.ok(await vent(() => POSTER.length === 1));
    assert.deepEqual(POSTER[0], { sti: "/v1/plattform/pliktkatalog/frist",
      kropp: { nokkel: "mva_termin", periode: "1. termin 2028",
               frist: "2028-04-10" } });
    assert.ok(await vent(() => s.textContent.includes("10.04.2028")));
    const brudd = await alvorligeBrudd(m);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("Oversikt: fristkortet viser neste frist, eller veien til oppsettet",
  async () => {
    SVAR = { profil: null, frister: [], nye_i_katalogen: 0 };
    let m = nyHoved();
    let k = fristkort(m, ctx);
    m.append(k);
    assert.ok(await vent(() => k.querySelector("a")));
    assert.equal(k.querySelector("a").getAttribute("href"), "#/lovfrister");
    assert.ok(k.textContent.includes(t("ui.dashbord.frist.start")));

    SVAR = { profil: { orgform: "AS", mva: true, ansatte: false },
             frister: [F("a_melding", "A-melding", "Gammel", "2020-01-05"),
                       F("mva_termin", "MVA-melding", "1. termin 2099",
                         "2099-04-12")], nye_i_katalogen: 0 };
    m = nyHoved();
    k = fristkort(m, ctx);
    m.append(k);
    assert.ok(await vent(() => k.textContent.includes("12.04.2099")));
    assert.ok(k.textContent.includes("MVA-melding"));
    assert.ok(k.textContent.includes(
      t("ui.dashbord.frist.forfalt").replace("{n}", "1")),
      "en forfalt frist skal stå med ord");
    const brudd = await alvorligeBrudd(m);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

// 239: egne frister og kalenderlenken.
test("Frister: egen frist legges til på én linje, og kan fjernes", async () => {
  SVAR = { profil: null, frister: [], nye_i_katalogen: 0,
           kalender: { aktiv: false, opprettet: null } };
  POSTER.length = 0;
  const orig = globalThis.fetch;
  globalThis.fetch = async (url, opts = {}) => {
    const sti = url.split("?")[0];
    if (opts.method === "POST" && sti === "/v1/lovfrister/egen") {
      const kropp = JSON.parse(opts.body);
      POSTER.push({ sti, kropp });
      SVAR = { ...SVAR, frister: [{ ...F("egen", kropp.tittel, null,
        kropp.frist), egen: true, hjemmel: null, kilde_url: null,
        gjentakelse: kropp.gjentakelse }] };
      return { ok: true, status: 200, json: async () => SVAR };
    }
    return orig(url, opts);
  };
  try {
    const m = nyHoved();
    visLovfrister(m, ctx);
    // UTEN PROFIL virker egne frister likevel.
    const ny = await (async () => {
      await vent(() => [...m.querySelectorAll("button")]
        .some((b) => b.textContent === t("ui.lovfrister.egen_ny")));
      return [...m.querySelectorAll("button")]
        .find((b) => b.textContent === t("ui.lovfrister.egen_ny"));
    })();
    ny.click();
    assert.equal(ny.getAttribute("aria-expanded"), "true");
    m.querySelector("#lf-egen-dato").value = "2099-03-01";
    m.querySelector("#lf-egen-tittel").value = "Leieavtale – forny";
    m.querySelector("#lf-egen-gjentakelse").value = "aarlig";
    m.querySelector("form.lf-egen").requestSubmit();
    assert.ok(await vent(() => POSTER.length === 1));
    assert.deepEqual(POSTER[0].kropp, { tittel: "Leieavtale – forny",
      frist: "2099-03-01", gjentakelse: "aarlig" });
    assert.ok(await vent(() => m.querySelector(".lf-rad")));
    const rad = m.querySelector(".lf-rad");
    assert.ok(rad.textContent.includes(t("ui.lovfrister.egen")));
    assert.ok(rad.textContent.includes(t("ui.lovfrister.gjentakelse.aarlig")));
    assert.equal(rad.querySelector("a"), null, "en egen frist har ingen kilde");
    const fjern = [...rad.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.lovfrister.fjern"));
    fjern.click();
    assert.ok(await vent(() => POSTER.some((p) =>
      p.sti === "/v1/plikt/egen-2099-03-01/bortfall")));
    const brudd = await alvorligeBrudd(m);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  } finally { globalThis.fetch = orig; }
});

test("Frister: kalenderlenken vises én gang, og kan slås av", async () => {
  const profil = { orgform: "AS", mva: true, ansatte: true, kilde: "brreg" };
  SVAR = { profil, frister: [], nye_i_katalogen: 0,
           kalender: { aktiv: false, opprettet: null } };
  POSTER.length = 0;
  const orig = globalThis.fetch;
  globalThis.fetch = async (url, opts = {}) => {
    const sti = url.split("?")[0];
    const ok = (d) => ({ ok: true, status: 200, json: async () => d });
    if (opts.method === "POST" && sti === "/v1/lovfrister/kalender") {
      POSTER.push({ sti });
      SVAR = { ...SVAR, kalender: { aktiv: true,
        opprettet: "2026-11-28T10:00:00+00:00" } };
      return ok({ ...SVAR, sti: "/kalender/hemmeligtoken1234567890.ics" });
    }
    if (opts.method === "POST" && sti === "/v1/lovfrister/kalender/slett") {
      POSTER.push({ sti });
      SVAR = { ...SVAR, kalender: { aktiv: false, opprettet: null } };
      return ok(SVAR);
    }
    return orig(url, opts);
  };
  const knapp = (m, nokkel) => [...m.querySelectorAll("button")]
    .find((b) => b.textContent === t(nokkel));
  try {
    let m = nyHoved();
    visLovfrister(m, ctx);
    assert.ok(await vent(() => knapp(m, "ui.lovfrister.kal.lag")));
    knapp(m, "ui.lovfrister.kal.lag").click();
    assert.ok(await vent(() => m.querySelector("#lf-kal-url")));
    assert.ok(m.querySelector("#lf-kal-url").value
      .endsWith("/kalender/hemmeligtoken1234567890.ics"));
    assert.ok(m.querySelector('a[href^="webcal://"]'), "ingen abonner-lenke");
    assert.ok(m.textContent.includes(t("ui.lovfrister.kal.advarsel")));
    const brudd = await alvorligeBrudd(m);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));

    // NESTE BESØK: lenken er på, men vises ikke igjen (bare hashen finnes).
    m = nyHoved();
    visLovfrister(m, ctx);
    assert.ok(await vent(() => knapp(m, "ui.lovfrister.kal.av")));
    assert.equal(m.querySelector("#lf-kal-url"), null);
    assert.ok(m.textContent.includes("28.11.2026"));
    knapp(m, "ui.lovfrister.kal.av").click();
    assert.ok(await vent(() => knapp(m, "ui.lovfrister.kal.lag")));
    assert.deepEqual(POSTER.map((p) => p.sti), ["/v1/lovfrister/kalender",
      "/v1/lovfrister/kalender/slett"]);
  } finally { globalThis.fetch = orig; }
});
