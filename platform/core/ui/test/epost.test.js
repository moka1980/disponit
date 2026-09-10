// M-6 PR-B: kildeflaten mot mocket API (jsdom + axe). Portene: lista
// er en tilgjengelig tabell (axe-ren, th scope, status som TEKST),
// forvaltningskontrollene finnes KUN med `epost:kilde:administrer`,
// «Koble til M365» navigerer til serverens authorize-URL (aldri en
// egenbygd), `m365_ikke_konfigurert` er en ÆRLIG melding på flaten,
// og deaktivering poster mot riktig rute og tegner om.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visEpost, settNavigasjonForTest, lesbarTekst }
  from "../static/js/flater/epost.js";

settI18nForTest(NB, "nb");

const KILDER = { kilder: [
  { kilde_id: "5e0a3f1e-0000-4000-8000-000000000001", leverandor: "m365",
    postboks: "post@acme.example", status: "aktiv",
    sist_hentet_ts: "2026-08-30T10:00:00+00:00",
    opprettet: "2026-08-01T09:00:00+00:00" },
  { kilde_id: "5e0a3f1e-0000-4000-8000-000000000002", leverandor: "m365",
    postboks: "faktura@acme.example", status: "deaktivert",
    sist_hentet_ts: null, opprettet: "2026-07-01T09:00:00+00:00" },
] };

let SVAR;              // sti -> svar (dict) | statuskode (tall)
const KALL = [];       // {url, metode, kropp, headers}
globalThis.fetch = async (url, opts = {}) => {
  KALL.push({ url, metode: opts.method || "GET",
    kropp: opts.body ? JSON.parse(opts.body) : null,
    headers: opts.headers || {} });
  const sti = url.split("?")[0];
  const oppf = SVAR[sti];
  if (!oppf) {
    return { ok: false, status: 404,
      json: async () => ({ feil: "ikke_funnet" }) };
  }
  if (typeof oppf === "number") {
    return { ok: false, status: oppf,
      json: async () => ({ feil: SVAR._feilkode || "x" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx(overstyr = {}) {
  // Flagget MÅ bo på det returnerte objektet, ikke på fabrikken: skrev
  // callbacken til `ctx._ua`, kunne 403-porten under aldri se et treff,
  // og «403 sender ikke til innlogging» var en tom påstand (CodeRabbit).
  const c = { sprak: "nb", scopes: ["epost:read"], tenant: "acme",
    _ua: false, ...overstyr };
  c.paaUautorisert = () => { c._ua = true; };
  return c;
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

test("Epost: lesende økt ser tabellen, INGEN forvaltningskontroller, axe rent",
    async () => {
  SVAR = { "/v1/epost/kilder": KILDER };
  KALL.length = 0;
  const h = nyHoved();
  visEpost(h, ctx());
  await vent(() => h.querySelector("table"));
  assert.ok(h.textContent.includes("post@acme.example"));
  // Status er TEKST, ikke bare farge.
  assert.ok(h.textContent.includes(t("ui.epost.status.aktiv")));
  assert.ok(h.textContent.includes(t("ui.epost.status.deaktivert")));
  // Aldri hentet er en SETNING, aldri et tomt felt.
  assert.ok(h.textContent.includes(t("ui.epost.aldri_hentet")));
  // Lesende økt: verken koble-til-seksjon eller deaktiver-knapp.
  assert.equal(h.querySelector("section"), null);
  assert.equal(h.querySelector("button"), null);
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Epost: administrator får koble-til og deaktiver, axe rent", async () => {
  SVAR = { "/v1/epost/kilder": KILDER };
  const h = nyHoved();
  visEpost(h, ctx({ scopes: ["epost:read", "epost:kilde:administrer"] }));
  await vent(() => h.querySelector("table"));
  assert.ok(h.querySelector("section h2"));
  assert.ok(h.querySelector("input[type=email]"));
  // Den aktive kilden har knapp; den deaktiverte har tekst i stedet for
  // en død kontroll (reaktivering = nytt samtykke, aldri en flipp).
  const knapper = [...h.querySelectorAll("tbody button")];
  assert.equal(knapper.length, 1);
  assert.ok(h.textContent.includes(t("ui.epost.deaktivert")));
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Epost: Koble til navigerer til SERVERENS authorize-URL med idem-nøkkel",
    async () => {
  SVAR = { "/v1/epost/kilder": KILDER,
    "/v1/epost/kilder/start":
      { autorisasjonsurl: "https://login.microsoftonline.example/authorize?x=1" } };
  KALL.length = 0;
  const h = nyHoved();
  const navigasjoner = [];
  // jsdoms Location er [Unforgeable] (assign kan ikke redefineres), så
  // navigasjonen fanges gjennom flatens eget snitt.
  settNavigasjonForTest((url) => navigasjoner.push(url));
  try {
    visEpost(h, ctx({ scopes: ["epost:read", "epost:kilde:administrer"] }));
    await vent(() => h.querySelector("input[type=email]"));
    h.querySelector("input[type=email]").value = "ny@acme.example";
    h.querySelector("section button").click();
    await vent(() => navigasjoner.length === 1);
    assert.deepEqual(navigasjoner,
      ["https://login.microsoftonline.example/authorize?x=1"]);
    // Feltet låses SAMMEN med knappen mens forespørselen er i lufta:
    // en redigering underveis ruller idempotensnøkkelen, og eier ville
    // blitt sendt av gårde for en annen boks enn den serveren fikk.
    assert.equal(h.querySelector("input[type=email]").disabled, true);
    const start = KALL.find((k) => k.url === "/v1/epost/kilder/start");
    assert.equal(start.metode, "POST");
    assert.equal(start.kropp.postboks, "ny@acme.example");
    assert.ok(start.headers["Idempotency-Key"],
      "skriveruten skal bære Idempotency-Key");
  } finally {
    settNavigasjonForTest((url) => { window.location.assign(url); });
  }
});

test("Epost: m365_ikke_konfigurert er en ÆRLIG melding, ikke en generisk feil",
    async () => {
  SVAR = { "/v1/epost/kilder": { kilder: [] },
    "/v1/epost/kilder/start": 503, _feilkode: "m365_ikke_konfigurert" };
  const h = nyHoved();
  visEpost(h, ctx({ scopes: ["epost:read", "epost:kilde:administrer"] }));
  await vent(() => h.querySelector("input[type=email]"));
  h.querySelector("input[type=email]").value = "ny@acme.example";
  h.querySelector("section button").click();
  await vent(() => h.textContent.includes(t("ui.epost.ikke_konfigurert")));
  assert.ok(h.textContent.includes(t("ui.epost.ikke_konfigurert")));
});

test("Epost: deaktiver krever bekreftelse, poster så mot riktig rute",
    async () => {
  SVAR = { "/v1/epost/kilder": KILDER,
    "/v1/epost/kilder/5e0a3f1e-0000-4000-8000-000000000001/deaktiver":
      { kilde_id: "5e0a3f1e-0000-4000-8000-000000000001",
        status: "deaktivert" } };
  KALL.length = 0;
  const h = nyHoved();
  visEpost(h, ctx({ scopes: ["epost:read", "epost:kilde:administrer"] }));
  await vent(() => h.querySelector("tbody button"));
  h.querySelector("tbody button").click();
  // Klikket alene deaktiverer INGENTING — enveishandlingen står bak
  // en bekreftelse som navngir postboksen.
  await vent(() => document.querySelector(".overlegg"));
  const dialog = document.querySelector(".overlegg");
  assert.ok(dialog.textContent.includes("post@acme.example"),
    "bekreftelsen må navngi postboksen den kobler fra");
  assert.equal(KALL.filter((k) => k.metode === "POST").length, 0,
    "deaktiveringen gikk uten bekreftelse");
  const bekreft = dialog.querySelector(".knapp.fare");
  bekreft.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  await vent(() => KALL.some((k) => k.metode === "POST"));
  const post = KALL.find((k) => k.metode === "POST");
  assert.equal(post.url,
    "/v1/epost/kilder/5e0a3f1e-0000-4000-8000-000000000001/deaktiver");
  // Omtegningen henter lista på nytt.
  await vent(() =>
    KALL.filter((k) => k.url === "/v1/epost/kilder").length >= 2);
  assert.ok(KALL.filter((k) => k.url === "/v1/epost/kilder").length >= 2);
});

test("Epost: tom liste viser tomtilstanden", async () => {
  SVAR = { "/v1/epost/kilder": { kilder: [] } };
  const h = nyHoved();
  visEpost(h, ctx());
  await vent(() => h.querySelector(".tom"));
  assert.ok(h.textContent.includes(t("ui.epost.tom")));
});

test("Epost: 403 → ingen-tilgang-tilstand på flaten (aldri innlogging)",
    async () => {
  SVAR = { "/v1/epost/kilder": 403 };
  const h = nyHoved();
  const c = ctx();
  visEpost(h, c);
  await vent(() => h.querySelector(".tilstand"));
  assert.ok(h.querySelector(".tilstand"));
  assert.equal(c._ua, false, "403 skal ikke sende til innlogging");
  // Motprøven, ellers måler assertionen over ingenting: en 401 SKAL
  // treffe `paaUautorisert` — det er det som skiller «mangler tilgang»
  // fra «er ikke logget inn».
  SVAR = { "/v1/epost/kilder": 401 };
  const h2 = nyHoved();
  const c2 = ctx();
  visEpost(h2, c2);
  await vent(() => c2._ua === true);
  assert.equal(c2._ua, true, "401 skal sende til innlogging");
});

// ---------------------------------------------------------------------------
// M-6 PR-D a: meldingene innhenteren la i registeret — lesende.
// ---------------------------------------------------------------------------
const K1 = KILDER.kilder[0].kilde_id;
const M1 = "7a1b2c3d-0000-4000-8000-000000000011";
const MELDINGER = { meldinger: [
  { melding_id: M1, kilde_id: K1, mottatt_ts: "2026-09-10T12:00:00+00:00",
    retning: "inn", har_vedlegg: true, trad_id: "c-1",
    slettes_ts: "2026-12-09T12:00:00+00:00", reapet: false,
    fra: "per@nordvik.example", fra_navn: "Per Nordvik",
    emne: "Befaring elbillader", forhandsvisning: "Hei, kan dere komme" },
  { melding_id: "7a1b2c3d-0000-4000-8000-000000000012", kilde_id: K1,
    mottatt_ts: "2026-06-01T08:00:00+00:00", retning: "inn", har_vedlegg: false,
    trad_id: null, slettes_ts: "2026-08-30T08:00:00+00:00", reapet: true,
    slettet_ts: "2026-08-30T08:00:01+00:00", slettet_for_fristen: false,
    fra: null, fra_navn: null, emne: null, forhandsvisning: null },
  { melding_id: "7a1b2c3d-0000-4000-8000-000000000013", kilde_id: K1,
    mottatt_ts: "2026-09-09T08:00:00+00:00", retning: "inn", har_vedlegg: false,
    trad_id: null, slettes_ts: "2026-12-08T08:00:00+00:00", reapet: true,
    slettet_ts: "2026-09-10T14:36:52+00:00", slettet_for_fristen: true,
    fra: null, fra_navn: null, emne: null, forhandsvisning: null },
], vist: 3, avkortet: true };
const MELDING = { ...MELDINGER.meldinger[0], til: ["post@acme.example"],
  kropp: "Hei, kan dere komme på befaring neste uke?\nHilsen Per", kropp_type: "text" };

test("Epost: meldingene vises per aktiv kilde — avsender, emne, slettefrist, reapet som reapet, ingen svar-knapp", async () => {
  SVAR = { "/v1/epost/kilder": KILDER, "/v1/epost/meldinger": MELDINGER,
           [`/v1/epost/meldinger/${M1}`]: MELDING };
  KALL.length = 0;
  const h = nyHoved();
  visEpost(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 2);
  const tekst = h.textContent;
  assert.ok(tekst.includes("Befaring elbillader"));
  assert.ok(tekst.includes("Per Nordvik <per@nordvik.example>"));
  // Slettede står IKKE i lista — de er spor, ikke innboks.
  assert.ok(!tekst.includes(t("ui.epost.meldinger.slettet_av_fristen"))
    || h.querySelector("details"), "slettede skal ligge sammenklappet");
  assert.ok(tekst.includes(t("ui.epost.meldinger.slettede_vis").replace("{n}", "2")));
  assert.ok(tekst.includes(t("ui.epost.meldinger.avkortet")));
  assert.ok(tekst.includes(t("ui.epost.meldinger.vedlegg")));
  // Én meldingsliste: den deaktiverte kilden hentes ikke.
  assert.equal(KALL.filter((k) => k.url.startsWith("/v1/epost/meldinger?")).length, 1);
  assert.ok(KALL.some((k) => k.url === `/v1/epost/meldinger?kilde=${K1}`));
  for (const b of h.querySelectorAll("button")) {
    assert.ok(!/svar|send|videresend|slett/i.test(b.textContent), b.textContent);
  }
  // Bare den levende meldingen har handlinger, og de står SIDE VED SIDE.
  const knapper = [...h.querySelectorAll("table")[1].querySelectorAll("button")];
  assert.equal(knapper.length, 1, "slettede meldinger har ingen handlinger");
  assert.ok(knapper[0].closest(".knapperad"), "handlingene står ikke i én rad");
  knapper[0].click();
  await vent(() => h.querySelector(".epost-kropp"));
  assert.ok(h.querySelector(".epost-kropp").textContent.includes("Hilsen Per"));
  assert.ok(h.textContent.includes("post@acme.example"));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Epost: ingen meldinger ennå sies, og en meldingsliste som feiler stopper ikke kildetabellen", async () => {
  SVAR = { "/v1/epost/kilder": KILDER,
           "/v1/epost/meldinger": { meldinger: [], vist: 0, avkortet: false } };
  let h = nyHoved();
  visEpost(h, ctx());
  await vent(() => h.textContent.includes(t("ui.epost.meldinger.ingen")));
  SVAR = { "/v1/epost/kilder": KILDER, "/v1/epost/meldinger": 500 };
  h = nyHoved();
  visEpost(h, ctx());
  await vent(() => h.querySelector("[role=alert]"));
  assert.ok(h.querySelectorAll("table").length >= 1);
  assert.ok(h.textContent.includes(t("ui.epost.meldinger.feilet").replace("{postboks}", "post@acme.example")));
});

test("Epost: en administrator kan slette en hentet melding — bak bekreftelse, og en leser kan ikke", async () => {
  SVAR = { "/v1/epost/kilder": KILDER, "/v1/epost/meldinger": MELDINGER,
           [`/v1/epost/meldinger/${M1}`]: MELDING,
           [`/v1/epost/meldinger/${M1}/slett`]: { melding_id: M1, slettet: true, ny: true } };
  // Leser: ingen slett-knapp i det hele tatt.
  let h = nyHoved();
  visEpost(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 2);
  assert.ok(![...h.querySelectorAll("button")].some(
    (b) => b.textContent === t("ui.epost.meldinger.slett")));
  // Administrator: knappen finnes, men bare på meldinger som ikke er slettet.
  h = nyHoved();
  KALL.length = 0;
  visEpost(h, ctx({ scopes: ["epost:read", "epost:kilde:administrer"] }));
  await vent(() => h.querySelectorAll("table").length >= 2);
  const slettknapper = [...h.querySelectorAll("button")].filter(
    (b) => b.textContent === t("ui.epost.meldinger.slett"));
  assert.equal(slettknapper.length, 1, "reapet melding skal ikke ha slett-knapp");
  slettknapper[0].click();
  await vent(() => document.querySelector("[role=alertdialog]"));
  const dialog = document.querySelector("[role=alertdialog]");
  assert.ok(dialog.textContent.includes(t("ui.epost.meldinger.slett_tekst")));
  const bekreft = [...dialog.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.epost.meldinger.slett"));
  bekreft.click();
  await vent(() => KALL.some((k) => k.url === `/v1/epost/meldinger/${M1}/slett`));
  const kall = KALL.find((k) => k.url === `/v1/epost/meldinger/${M1}/slett`);
  assert.equal(kall.metode, "POST");
  assert.ok(kall.headers["Idempotency-Key"] || kall.headers["idempotency-key"]);
});

test("Epost: slettede meldinger er spor — sammenklappet, og teksten sier HVEM som slettet", async () => {
  SVAR = { "/v1/epost/kilder": KILDER, "/v1/epost/meldinger": MELDINGER,
           [`/v1/epost/meldinger/${M1}`]: MELDING };
  const h = nyHoved();
  visEpost(h, ctx());
  await vent(() => h.querySelector("details"));
  const detaljer = h.querySelector("details");
  // Sammenklappet: teksten om de slettede står ikke i veien for innboksen.
  assert.equal(detaljer.open, false);
  assert.ok(detaljer.textContent.includes(
    t("ui.epost.meldinger.slettet_av_menneske")), "manuell sletting sies ikke");
  assert.ok(detaljer.textContent.includes(
    t("ui.epost.meldinger.slettet_av_fristen")), "fristen sies ikke");
  // Lista over selve meldingene har bare den levende.
  const rader = [...h.querySelectorAll("table")[1].querySelectorAll("tbody tr")];
  assert.equal(rader.length, 1);
  assert.ok(rader[0].textContent.includes("Befaring elbillader"));
});

test("Epost: panelet står i lista og får fokus når en melding åpnes", async () => {
  SVAR = { "/v1/epost/kilder": KILDER, "/v1/epost/meldinger": MELDINGER,
           [`/v1/epost/meldinger/${M1}`]: MELDING };
  const h = nyHoved();
  visEpost(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 2);
  const liste = h.querySelectorAll("table")[1].closest("section");
  const panel = liste.querySelector(".skjemaboks");
  assert.ok(panel, "panelet står ikke i lista det hører til");
  assert.equal(panel.hidden, true);
  liste.querySelector("tbody button").click();
  await vent(() => !panel.hidden);
  assert.ok(panel.textContent.includes("Hilsen Per"));
  assert.equal(document.activeElement, panel, "panelet fikk ikke fokus");
});

test("Epost: lesevisningen fjerner adressestøyen, og originalen ligger bak en bryter", async () => {
  // Nøyaktig formen Graph gir for en HTML-post konvertert til tekst.
  const raa = [
    "Vis i nettleseren <https://t.emailnotifications.microsoft.com/r/?id=h1a1c44bc7>",
    "",
    "[https://cdn-dynmedia-1.microsoft.com/is/image/microsoftcorp/M365?fmt=png-alpha]",
    "",
    "[En smarttelefon som viser et OneDrive-fotogalleri.] <https://t.emailnotifications.microsoft.com/r/?id=h1a1c44bc41>",
    "",
    "",
    "",
    "Din OneDrive er klar",
    "Les mer <https://onedrive.live.com/>",
    "https://kun-en-adresse.example/spor",
  ].join("\n");
  const ren = lesbarTekst(raa);
  assert.ok(!ren.includes("https://"), ren);
  assert.ok(ren.includes("Din OneDrive er klar"));
  assert.ok(ren.includes("Les mer"));
  assert.ok(!/\n{3,}/.test(ren), "tomme linjer ble ikke kollapset");
  // En ren tekstpost røres ikke.
  const enkel = "Hei, kan dere komme på befaring?\nHilsen Per";
  assert.equal(lesbarTekst(enkel), enkel);

  SVAR = { "/v1/epost/kilder": KILDER, "/v1/epost/meldinger": MELDINGER,
           [`/v1/epost/meldinger/${M1}`]: { ...MELDING, kropp: raa } };
  const h = nyHoved();
  visEpost(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 2);
  h.querySelectorAll("table")[1].querySelector("tbody button").click();
  await vent(() => h.querySelector(".epost-kropp"));
  const panel = h.querySelector(".skjemaboks");
  assert.ok(panel.querySelector(".epost-kropp").textContent
    .includes("Din OneDrive er klar"));
  assert.ok(!panel.querySelector(".epost-kropp").textContent.includes("https://"));
  // Originalen er ikke borte — bryteren VEKSLER til den, og tilbake.
  // Én tekst på skjermen om gangen (eiers merknad: «da blir det duplikat
  // visning»).
  assert.equal(panel.querySelectorAll(".epost-kropp").length, 1);
  const bytt = [...panel.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.epost.meldinger.vis_raa"));
  assert.ok(bytt, "bryteren mangler");
  assert.equal(bytt.getAttribute("aria-pressed"), "false");
  bytt.click();
  assert.equal(panel.querySelectorAll(".epost-kropp").length, 1);
  assert.ok(panel.querySelector(".epost-kropp").textContent.includes("https://"));
  assert.equal(bytt.textContent, t("ui.epost.meldinger.vis_lesbar"));
  assert.equal(bytt.getAttribute("aria-pressed"), "true");
  bytt.click();
  assert.ok(!panel.querySelector(".epost-kropp").textContent.includes("https://"));
  assert.equal(bytt.textContent, t("ui.epost.meldinger.vis_raa"));
});

test("Epost: med to aktive kilder får hver liste sitt eget panel", async () => {
  const K2 = "5e0a3f1e-0000-4000-8000-000000000003";
  const TO = { kilder: [KILDER.kilder[0],
    { kilde_id: K2, leverandor: "m365", postboks: "salg@acme.example",
      status: "aktiv", sist_hentet_ts: "2026-09-10T10:00:00+00:00",
      opprettet: "2026-08-02T09:00:00+00:00" }] };
  SVAR = { "/v1/epost/kilder": TO, "/v1/epost/meldinger": MELDINGER,
           [`/v1/epost/meldinger/${M1}`]: MELDING };
  const h = nyHoved();
  visEpost(h, ctx());
  await vent(() => h.querySelectorAll(".skjemaboks").length >= 2);
  const seksjoner = [...h.querySelectorAll("section")].filter(
    (s) => s.querySelector("tbody button"));
  assert.equal(seksjoner.length, 2, "begge kildene skal ha en liste");
  const panel0 = seksjoner[0].querySelector(".skjemaboks");
  const panel1 = seksjoner[1].querySelector(".skjemaboks");
  assert.ok(panel0 && panel1 && panel0 !== panel1, "listene deler panel");
  // Åpner vi i den FØRSTE lista, skal panelet der vise meldingen.
  seksjoner[0].querySelector("tbody button").click();
  await vent(() => !panel0.hidden);
  assert.ok(panel0.textContent.includes("Hilsen Per"));
  assert.equal(panel1.hidden, true, "panelet under feil kilde åpnet seg");
});

test("Epost: en postboks uten sendetilgang sier det FØR noen skriver et svar", async () => {
  const utenSend = { kilder: [{ ...KILDER.kilder[0], kan_svare: false },
                              KILDER.kilder[1]] };
  SVAR = { "/v1/epost/kilder": utenSend, "/v1/epost/meldinger": MELDINGER,
           [`/v1/epost/meldinger/${M1}`]: MELDING };
  const varselet = t("ui.epost.kilde.mangler_sendetilgang")
    .replace("{postboks}", "post@acme.example");
  let h = nyHoved();
  visEpost(h, ctx());
  await vent(() => h.textContent.includes(varselet));
  // Den deaktiverte kilden får ingen slik oppfordring — den skal ikke
  // kobles til igjen for å svare, den er avviklet.
  const varsler = [...h.querySelectorAll("[role=status]")].filter(
    (n) => n.textContent.includes(t("ui.epost.kilde.mangler_sendetilgang")
      .split("{postboks}")[1].slice(0, 30)));
  assert.equal(varsler.length, 1);
  // Med sendetilgang står det ingenting.
  SVAR = { "/v1/epost/kilder": { kilder: [{ ...KILDER.kilder[0], kan_svare: true }] },
           "/v1/epost/meldinger": MELDINGER,
           [`/v1/epost/meldinger/${M1}`]: MELDING };
  h = nyHoved();
  visEpost(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 2);
  assert.ok(!h.textContent.includes(
    t("ui.epost.kilde.mangler_sendetilgang").split("{postboks}")[1].slice(0, 30)));
});

// ---------------------------------------------------------------------------
// Svarutkastet (179): mennesket skriver og godkjenner, flaten sender ikke.
// ---------------------------------------------------------------------------

test("Epost: et svar skrives som utkast og sendes — men flaten snakker aldri med Microsoft", async () => {
  const medUtkast = { ...MELDING, utkast: [
    { utkast_id: "u-1", status: "foreslatt", opprettet: "2026-09-10T12:00:00+00:00",
      avgjort_ts: null, avgjort_av: null, tekst: "Vi kommer torsdag.", slettet: false }] };
  SVAR = { "/v1/epost/kilder": { kilder: [{ ...KILDER.kilder[0], kan_svare: true }] },
           "/v1/epost/meldinger": MELDINGER,
           [`/v1/epost/meldinger/${M1}`]: medUtkast,
           [`/v1/epost/meldinger/${M1}/svarutkast`]: { utkast_id: "u-2", status: "foreslatt" },
           "/v1/epost/utkast/u-1/send": { utkast_id: "u-1", status: "sendes" },
           "/v1/epost/utkast/u-1/dom": { utkast_id: "u-1", status: "forkastet" } };
  const h = nyHoved();
  visEpost(h, ctx({ scopes: ["epost:read", "epost:kilde:administrer"] }));
  await vent(() => h.querySelectorAll("table").length >= 2);
  h.querySelectorAll("table")[1].querySelector("tbody button").click();
  await vent(() => h.querySelector("textarea"));
  const panel = h.querySelector(".skjemaboks");
  assert.ok(panel.textContent.includes("Vi kommer torsdag."));
  assert.ok(panel.textContent.includes(t("ui.epost.svar.status.foreslatt")));
  // SEND går til send-ruten. Flaten sender ikke selv: den setter
  // utkastet i kø hos bakgrunnsprosessen, som har nøkkelen.
  KALL.length = 0;
  [...panel.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.epost.svar.knapp.send")).click();
  await vent(() => KALL.some((k) => k.url === "/v1/epost/utkast/u-1/send"));
  assert.equal(KALL.find((k) => k.url === "/v1/epost/utkast/u-1/send").metode,
               "POST");
  // Ventetiden står i skjemaet, så ingen tror det gikk ut i samme sekund.
  assert.ok(panel.textContent.includes(t("ui.epost.svar.ventetid")));
  // Etter sendingen tegnes meldingen på nytt: raden sier «i kø», og
  // send-knappen er borte (CodeRabbit).
  SVAR[`/v1/epost/meldinger/${M1}`] = { ...medUtkast, utkast: [
    { ...medUtkast.utkast[0], status: "sendes",
      avgjort_ts: "2026-09-10T12:01:00+00:00", avgjort_av: "bruker:a" }] };
  h.querySelectorAll("table")[1].querySelector("tbody button").click();
  await vent(() => h.querySelector(".skjemaboks").textContent
    .includes(t("ui.epost.svar.i_koe")));
  assert.ok(![...h.querySelector(".skjemaboks").querySelectorAll("button")]
    .some((b) => b.textContent === t("ui.epost.svar.knapp.send")));
  // Nytt utkast lagres, ikke sendes.
  KALL.length = 0;
  const felt = panel.querySelector("textarea");
  felt.value = "Takk for beskjeden.";
  felt.closest("form").requestSubmit();
  await vent(() => KALL.some((k) => k.url.endsWith("/svarutkast")));
  const kall = KALL.find((k) => k.url.endsWith("/svarutkast"));
  assert.equal(kall.metode, "POST");
  assert.equal(kall.kropp.tekst, "Takk for beskjeden.");
  // Send-knappen SKAL finnes (eiervedtak 10/9) — det som ikke skal
  // finnes, er en flate som snakker med Microsoft. Alle kall går til
  // vårt eget API.
  assert.ok(KALL.every((k) => k.url.startsWith("/v1/")),
    "flaten kalte noe utenfor plattformen");
  assert.ok(!KALL.some((k) => /graph\.microsoft|outlook\.office/i.test(k.url)));
});

test("Epost: uten sendetilgang finnes ikke svarfeltet, bare forklaringen", async () => {
  SVAR = { "/v1/epost/kilder": { kilder: [{ ...KILDER.kilder[0], kan_svare: false }] },
           "/v1/epost/meldinger": MELDINGER,
           [`/v1/epost/meldinger/${M1}`]: { ...MELDING, utkast: [] } };
  const h = nyHoved();
  visEpost(h, ctx({ scopes: ["epost:read", "epost:kilde:administrer"] }));
  await vent(() => h.querySelectorAll("table").length >= 2);
  h.querySelectorAll("table")[1].querySelector("tbody button").click();
  await vent(() => h.querySelector(".skjemaboks").textContent
    .includes(t("ui.epost.svar.uten_tilgang")));
  assert.equal(h.querySelector(".skjemaboks textarea"), null);
});

test("Epost: et utkast i kø sier at det sendes innen fem minutter", async () => {
  const iKo = { ...MELDING, utkast: [
    { utkast_id: "u-3", status: "sendes", opprettet: "2026-09-10T12:00:00+00:00",
      avgjort_ts: "2026-09-10T12:01:00+00:00", avgjort_av: "bruker:a",
      tekst: "Vi kommer torsdag.", slettet: false, sendt_ts: null,
      feilgrunn: null }] };
  SVAR = { "/v1/epost/kilder": { kilder: [{ ...KILDER.kilder[0], kan_svare: true }] },
           "/v1/epost/meldinger": MELDINGER,
           [`/v1/epost/meldinger/${M1}`]: iKo };
  const h = nyHoved();
  visEpost(h, ctx({ scopes: ["epost:read", "epost:utkast:behandle"] }));
  await vent(() => h.querySelectorAll("table").length >= 2);
  h.querySelectorAll("table")[1].querySelector("tbody button").click();
  await vent(() => h.querySelector(".skjemaboks").textContent
    .includes(t("ui.epost.svar.i_koe")));
  const panel = h.querySelector(".skjemaboks");
  // Et utkast som alt er i kø, har ingen send-knapp igjen.
  assert.ok(![...panel.querySelectorAll("button")].some(
    (b) => b.textContent === t("ui.epost.svar.knapp.send")));
});

