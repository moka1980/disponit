// Adjudikatorkøen (041 §5-6): egen visning bak `domains:adjudicate`,
// partene synlige KUN her, axe rent (port 40) — og domenefanens
// forklaringstekster for avklaring/tilbakekalling uten motpartens identitet.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visAdjudikator } from "../static/js/flater/adjudikator.js";
import { visDomener } from "../static/js/flater/domener.js";
import { byggRuter } from "../static/js/sitekart.js";

settI18nForTest(NB, "nb");

let KALL;
let SVAR;
globalThis.fetch = async (url, opts = {}) => {
  const sti = url.split("?")[0];
  // `url` (med spørrestreng), kropp og headere føres med: pagineringsporten
  // måler at cursoren sendes videre, og adjudikasjonsporten at utfallet,
  // vinnende tenant og CSRF-tokenet faktisk går på tråden.
  KALL.push({ sti, url, metode: opts.method || "GET",
              kropp: opts.body, headers: opts.headers || {} });
  const svar = (typeof SVAR === "function") ? SVAR(sti, opts) : SVAR[sti];
  if (svar === undefined) {
    return { ok: false, status: 404, json: async () => ({ feil: "ikke_funnet" }) };
  }
  // `__status` lar en test svare med serverens LEGIBLE 409-er (409 er ikke
  // en transportfeil her — den bærer «1 av 2 avgitt»).
  const status = svar.__status || 200;
  const kropp = svar.__kropp !== undefined ? svar.__kropp : svar;
  return { ok: status < 400, status, json: async () => kropp };
};

function nyHoved() {
  const brett = nyttBrett();
  const m = document.createElement("main");
  m.id = "hovedinnhold"; m.tabIndex = -1;
  brett.append(m);
  return m;
}

async function vent(pred, n = 60) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}

const SAK = {
  unntak_id: 7, hostname: "kunde.example", saksrevisjon: 1,
  autorisasjonsgenerasjon: 2, utfordrer_tenant: "utfordrer-as",
  tapt_tenant: "taper-as", status: "ny", ts: "2026-08-19T00:00:00+00:00",
};

test("adjudikatorkøen: tabell med parter, caption/scope, axe rent", async () => {
  KALL = []; SVAR = { "/v1/domeneovertakelse/saker": { saker: [SAK] } };
  const h = nyHoved();
  visAdjudikator(h, { paaUautorisert: () => {} });
  await vent(() => h.querySelector(".adjudikatorliste table"));
  const tabell = h.querySelector(".adjudikatorliste table");
  // Partene er SYNLIGE her — dette er den ene flaten som skal vise dem.
  assert.ok(tabell.textContent.includes("utfordrer-as"));
  assert.ok(tabell.textContent.includes("taper-as"));
  assert.ok(tabell.querySelector("caption"));
  assert.ok(tabell.textContent.includes(t("ui.adjudikator.status.ny")));
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("adjudikatorkøen: tom kø er en tilstand, ikke en tom tabell", async () => {
  KALL = []; SVAR = { "/v1/domeneovertakelse/saker": { saker: [] } };
  const h = nyHoved();
  visAdjudikator(h, { paaUautorisert: () => {} });
  await vent(() => h.textContent.includes(t("ui.adjudikator.tom_tittel")));
  assert.ok(!h.querySelector(".adjudikatorliste table"));
});

test("adjudikatorkøen: «vis mer» bærer cursoren og legger til, ikke bytter",
     async () => {
  // Køen er ubundet i tid (saker står åpne til et menneske avgjør dem), så
  // siden er keyset-paginert. Flaten skal da BLA, ikke skjule resten.
  const SAK2 = { ...SAK, unntak_id: 8, hostname: "annen.example",
    ts: "2026-08-19T01:00:00+00:00" };
  KALL = [];
  SVAR = (sti) => {
    if (sti !== "/v1/domeneovertakelse/saker") return undefined;
    const forrige = KALL[KALL.length - 1] || {};
    return forrige.url && forrige.url.includes("cursor=c1")
      ? { saker: [SAK2], neste_cursor: null }
      : { saker: [SAK], neste_cursor: "c1" };
  };
  const h = nyHoved();
  visAdjudikator(h, { paaUautorisert: () => {} });
  await vent(() => h.querySelector(".adjudikatorliste table"));
  assert.equal(h.querySelectorAll(".adjudikatorliste tbody tr").length, 1);

  const mer = [...h.querySelectorAll(".cursornav button")]
    .find((b) => b.textContent === t("ui.vis_mer"));
  assert.ok(mer, "«vis mer» mangler selv om serveren ga en neste_cursor");
  mer.click();
  await vent(() => h.querySelectorAll(".adjudikatorliste tbody tr").length === 2);
  const tekst = h.querySelector(".adjudikatorliste table").textContent;
  assert.ok(tekst.includes("kunde.example") && tekst.includes("annen.example"),
    "andre side erstattet den første i stedet for å legge til");
  assert.ok(KALL.some((k) => k.url.includes("cursor=c1")),
    "cursoren ble ikke sendt med");
  // Siste side: ingen `neste_cursor` → ingen «vis mer» igjen.
  assert.ok(![...h.querySelectorAll(".cursornav button")]
    .some((b) => b.textContent === t("ui.vis_mer")));
});

const SAKER = "/v1/domeneovertakelse/saker";
const ATTEST = "/v1/unntak/7/domeneattestasjon";

function knapp(h, navn) {
  return [...h.querySelectorAll("button")]
    .find((b) => (b.getAttribute("aria-label") || b.textContent) === navn);
}

async function aapneKoen() {
  const h = nyHoved();
  visAdjudikator(h, { paaUautorisert: () => {} });
  await vent(() => h.querySelector(".adjudikatorliste table"));
  return h;
}

test("adjudikatorkøen: avgjørelsen kan tas i produktet, ikke bare i API-et",
     async () => {
  // Fra 041 bor saken på plattformtenanten og er UTE av den ordinære
  // unntakskøen. Uten knapper her finnes ingen vei i produktet til
  // attestasjonen, og utfordreren blir stående i `avklaring_kreves`.
  // `__Host-`-prefikset krever Secure + Path=/ (og ingen Domain) — uten dem
  // avviser cookiejaren den, og CSRF-headeren ville blitt utelatt uten at
  // testen sa fra hvorfor.
  document.cookie = "__Host-disponit_csrf=csrf-token; Path=/; Secure";
  KALL = [];
  SVAR = (sti) => (sti === SAKER
    ? { saker: [SAK], neste_cursor: null }
    : { status: "avgjort", utfall: "avvis", hostname: SAK.hostname });
  const h = await aapneKoen();

  const avvis = knapp(h, `${t("ui.adjudikator.handling.avvis")}: ${SAK.hostname}`);
  assert.ok(avvis, "køen har ingen avvis-knapp");
  assert.ok(knapp(h, `${t("ui.adjudikator.handling.godkjenn")}: ${SAK.hostname}`),
    "køen har ingen godkjenn-knapp");
  // Navnet bærer vertsnavnet: seks like «Avvis» ville vært uleselige for
  // en skjermleserbruker.
  assert.ok(avvis.getAttribute("aria-label").includes(SAK.hostname));

  avvis.click();
  // KONSEKVENSEN BEKREFTES FØRST — en avvisning avgjør saken med én stemme.
  await vent(() => document.body.textContent.includes(
    t("ui.adjudikator.bekreft.avvis_tittel")));
  assert.equal(KALL.filter((k) => k.metode === "POST").length, 0,
    "attestasjonen ble sendt uten bekreftelse");
  const brudd = await alvorligeBrudd(document.querySelector(".overlegg"),
                                     { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));

  [...document.querySelectorAll(".overlegg button")]
    .find((b) => b.textContent === t("ui.adjudikator.handling.avvis")).click();
  await vent(() => KALL.some((k) => k.metode === "POST"));
  const post = KALL.find((k) => k.metode === "POST");
  assert.equal(post.sti, ATTEST);
  assert.deepEqual(JSON.parse(post.kropp),
    { utfall: "avvis", vinnende_tenant: SAK.utfordrer_tenant });
  assert.equal(post.headers["X-Disponit-CSRF"], "csrf-token");
  // ... og køen lastes på nytt, så saken ikke blir stående som åpen.
  await vent(() => KALL.filter((k) => k.sti === SAKER).length >= 2);
});

test("adjudikatorkøen: 409 `krever_to_attestasjoner` er legibelt, ikke en feil",
     async () => {
  // §4 siste kule: fail-closed skal SIES. Tallet er forskjellen på «systemet
  // er i stykker» og «dere mangler en andre autorisert aktør».
  KALL = [];
  SVAR = (sti) => (sti === SAKER
    ? { saker: [SAK], neste_cursor: null }
    : { __status: 409, __kropp: { feil: "krever_to_attestasjoner",
                                  avgitt: 1, krever: 2 } });
  const h = await aapneKoen();
  knapp(h, `${t("ui.adjudikator.handling.godkjenn")}: ${SAK.hostname}`).click();
  await vent(() => document.body.textContent.includes(
    t("ui.adjudikator.bekreft.godkjenn_tittel")));
  [...document.querySelectorAll(".overlegg button")]
    .find((b) => b.textContent === t("ui.adjudikator.handling.godkjenn")).click();
  const ventet = t("ui.adjudikator.krever_to")
    .replace("{avgitt}", "1").replace("{krever}", "2");
  assert.ok(await vent(() => document.body.textContent.includes(ventet)),
    `live-regionen sa ikke «${ventet}»`);
  assert.ok(!document.body.textContent.includes(t("ui.feil_tittel")),
    "en legibel terskel ble vist som «noe gikk galt»");
});

test("sitekartet: adjudikator-ruten finnes KUN for adjudikasjonsscopet", () => {
  const uten = byggRuter({ scopes: ["decisions:read", "exceptions:read",
                                    "bestilling:opprett"] });
  assert.ok(!uten.some((r) => r.nokkel === "adjudikator"),
    "en kunderolle fikk adjudikatorkøen i navigasjonen");
  const med = byggRuter({ scopes: ["decisions:read", "domains:adjudicate"] });
  assert.ok(med.some((r) => r.nokkel === "adjudikator"));
});

test("domenefanen: avklaring og tilbakekalling forklares uten motpartens" +
     " identitet, axe rent", async () => {
  KALL = [];
  SVAR = { "/v1/domener": { domener: [
    { hostname: "a.example", status: "avklaring_kreves", wildcard: false,
      verifisert_ts: null, utloper: null,
      siste_vellykkede_revalidering: null,
      challenge_utstedt: null, challenge_utloper: null },
    { hostname: "b.example", status: "tilbakekalt", wildcard: false,
      konflikt: true,
      verifisert_ts: null, utloper: null,
      siste_vellykkede_revalidering: null,
      challenge_utstedt: null, challenge_utloper: null },
  ] } };
  const h = nyHoved();
  visDomener(h, { paaUautorisert: () => {} });
  await vent(() => h.querySelector(".domeneliste table"));
  const tekst = h.querySelector(".domeneliste table").textContent;
  // Forklaringen står i CELLEN — tekst, ikke bare et statusord.
  assert.ok(tekst.includes(t("domenestatus.avklaring_kreves.forklaring")));
  assert.ok(tekst.includes(t("domenestatus.tilbakekalt.forklaring")));
  // ... og skjermleserveien er koblet: statusordet peker på forklaringen.
  const peker = h.querySelector('[aria-describedby^="dm-forklaring-"]');
  assert.ok(peker, "statusordet mangler aria-describedby");
  assert.ok(h.querySelector(`#${peker.getAttribute("aria-describedby")}`),
    "aria-describedby peker på et element som ikke finnes");
  // Ingen kryssidentitet: motpartens navn finnes ikke i svaret og dermed
  // ikke i flaten — men porten måler at TEKSTENE heller ikke bærer noen.
  assert.ok(!tekst.match(/utfordrer-as|taper-as/));
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("domenefanen: en ORDINÆR tilbakekalling forklares ikke som en" +
     " overtakelse", async () => {
  // Codex P2: grenen var ubetinget på statusordet, og `tilbakekalt` har to
  // opphav. `tilbakekall_domenekontroll` (018) — operatørens vei — setter
  // ingen motpart, men fikk likevel teksten «DNS-kontroll er bevist av en
  // annen konto»: en falsk overtakelsesadvarsel til en kunde ingen har
  // utfordret. Forklaringen velges nå på ÅRSAKEN endepunktet svarer med.
  KALL = [];
  SVAR = { "/v1/domener": { domener: [
    { hostname: "d.example", status: "tilbakekalt", wildcard: false,
      konflikt: false,
      verifisert_ts: null, utloper: null,
      siste_vellykkede_revalidering: null,
      challenge_utstedt: null, challenge_utloper: null },
  ] } };
  const h = nyHoved();
  visDomener(h, { paaUautorisert: () => {} });
  await vent(() => h.querySelector(".domeneliste table"));
  const tekst = h.querySelector(".domeneliste table").textContent;
  assert.ok(tekst.includes(t("domenestatus.tilbakekalt.forklaring_ordinaer")),
    "den ordinære tilbakekallingen mangler sin forklaring");
  assert.ok(!tekst.includes(t("domenestatus.tilbakekalt.forklaring")),
    "en ordinær tilbakekalling ble forklart som en overtakelse");
  // Statusordet står fortsatt, og skjermleserveien er den samme.
  assert.ok(tekst.includes(t("domenestatus.tilbakekalt")));
  assert.ok(h.querySelector('[aria-describedby^="dm-forklaring-"]'),
    "statusordet mangler aria-describedby");
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("domenefanen: uten årsaksfeltet gjettes det IKKE på en overtakelse",
     async () => {
  // FAIL-CLOSED, som `gyldig`: mangler feltet — en eldre server, et delvis
  // rullet ut endepunkt — er den GENERISKE forklaringen den trygge.
  // Overtakelsesteksten er en PÅSTAND om en annen konto, og en flate som
  // gjetter den er nøyaktig feilen denne runden retter.
  KALL = [];
  SVAR = { "/v1/domener": { domener: [
    { hostname: "e.example", status: "tilbakekalt", wildcard: false,
      verifisert_ts: null, utloper: null,
      siste_vellykkede_revalidering: null,
      challenge_utstedt: null, challenge_utloper: null },
  ] } };
  const h = nyHoved();
  visDomener(h, { paaUautorisert: () => {} });
  await vent(() => h.querySelector(".domeneliste table"));
  const tekst = h.querySelector(".domeneliste table").textContent;
  assert.ok(tekst.includes(t("domenestatus.tilbakekalt.forklaring_ordinaer")),
    "et manglende felt ga ingen forklaring i det hele tatt");
  assert.ok(!tekst.includes(t("domenestatus.tilbakekalt.forklaring")),
    "et manglende felt ble gjettet til en overtakelse");
});
