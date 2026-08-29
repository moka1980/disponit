// PR-014 — den veiledede policy-editoren (jsdom + axe).
// Bransjemal-velger → skjema (grunnopplysninger, roller, handlinger m/ modus +
// grenser) → lagre som utkast (POST med CSRF + Idempotency-Key).
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visPolicyeditor } from "../static/js/flater/policyeditor.js";

settI18nForTest(NB, "nb");

const MAL = {
  meta: { policy_id: "netthandel-no", versjon: "0.2.0",
    bransjemal: "netthandel-no", status: "utkast" },
  schema_version: "0.2", tidssone: "Europe/Oslo",
  roller: [{ id: "daglig_leder" }, { id: "agent", beskrivelse: "Automatisk" }],
  dataklasser: ["finansiell"], verifikatorer: {},
  handlinger: [{ id: "ordre.bekreft", modul: "M-25", modus: "auto",
    ved_brudd: "unntakskø", grenser: { belop_maks: "1000.00", valuta: ["NOK"] },
    reversering: { type: "kompenserende", handling: "ordre.kanseller",
      frist_sekunder: 3600 } }],
  unntak: { kategorier: ["over_grense"], maks_auto_forsok: 3,
    eskalering: "unntakskø" },
};

let POST;
globalThis.fetch = async (url, opts) => {
  if (opts && opts.method && opts.method !== "GET") { POST = { url, opts };
    return { ok: true, status: 200,
      json: async () => ({ utkast_id: "u-ny", status: "utkast",
        utkastversjon: 1 }) }; }
  const sti = url.split("?")[0];
  if (sti === "/v1/policyadmin/editorgrunnlag") {
    return { ok: true, status: 200, json: async () => ({
      plattformvilkar: [{ vilkar_type: "domenekontroll_verifisert",
                          maldomene: "web_hostname" },
                        // To registrerte navn for SAMME domene: serverens
                        // prøve er eksistensiell, så hvert av dem dekker
                        // kravet alene.
                        { vilkar_type: "domene_eier_bekreftet",
                          maldomene: "web_hostname" },
                        // Et registrert plattformvilkår for et ANNET domene:
                        // det er aldri denne handlingens krav, og skal aldri
                        // låses her.
                        { vilkar_type: "annet_domenevilkar",
                          maldomene: "annet_domene" }],
      // Hvilke handlinger kravet i det hele tatt GJELDER for, og for
      // hvilket domene (`oppdragskontrakt`). Registeret er plattform-
      // globalt, kravet er ikke.
      malautorisasjonskrav: [{ prefiks: "kontroll.wcag.",
                               maldomene: "web_hostname" }],
      // `frekvensgrense_naadd` står med vilje i navnelista uten et krav:
      // motoren kan ikke løfte den, og flaten skal da ikke tilby den.
      godkjennbare_grunnkoder: ["belop_over_grense",
        "valuta_ikke_tillatt", "frekvensgrense_naadd"],
      godkjennbare_krav: [
        { grunnkode: "belop_over_grense", krever: "belop_maks" },
        { grunnkode: "valuta_ikke_tillatt", krever: "valuta" }] }) };
  }
  if (sti === "/v1/policymaler") {
    return { ok: true, status: 200, json: async () => ({ maler: [
      { mal_id: "netthandel", bransjemal: "netthandel-no", innhold: MAL }] }) };
  }
  if (sti === "/v1/policyutkast/u-1") {
    // Utkastet er REGISTRERT under «acme», men dokumentet bærer fortsatt
    // malens id: nøyaktig utkastet som ble opprettet før serveren krevde
    // samsvar. Kopi, ikke `MAL` selv — editoren redigerer innholdet in-place,
    // og et delt fikstur ville latt én test skrive i den neste.
    return { ok: true, status: 200, json: async () => ({
      utkast_id: "u-1", policy_id: "acme", status: "utkast", utkastversjon: 2,
      innhold: JSON.parse(JSON.stringify(MAL)) }) };
  }
  return { ok: false, status: 404, json: async () => ({ feil: "x" }) };
};

function ctx() { return { sprak: "nb", scopes: [], tenant: "acme",
  paaUautorisert: () => {} }; }
async function vent(pred, n = 80) {
  for (let i = 0; i < n; i++) { if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0)); }
  return pred();
}
function nyHoved() {
  const b = nyttBrett();
  const m = document.createElement("main"); m.id = "hovedinnhold"; m.tabIndex = -1;
  b.append(m); return m;
}
// Editoren er delt i trinn (§2.1). Testene må derfor si HVILKET trinn de
// måler, i stedet for å anta at alt står på skjermen samtidig.
function gaaTilFane(rot, tittel) {
  const fane = [...rot.querySelectorAll('[role="tab"]')]
    .find((f) => f.textContent === tittel);
  if (!fane) throw new Error(`fant ikke fanen «${tittel}»`);
  fane.dispatchEvent(new window.Event("click"));
  return fane;
}

const finnKnapp = (rot, tekst) => [...rot.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === tekst);

test("Ny: malvelger → skjema → lagre POSTer med CSRF + Idempotency-Key", async () => {
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  POST = undefined;
  const h = nyHoved();
  let aapnet = null;
  visPolicyeditor(h, ctx(), { aapneUtkast: (u) => { aapnet = u; } });

  // Malvelger.
  await vent(() => h.querySelector(".mal-liste"));
  assert.ok(h.textContent.includes(t("ui.editor.mal.netthandel")));
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
  h.querySelector(".mal-kort").dispatchEvent(new window.Event("click"));

  // Skjemaet er delt i trinn: fanene finnes samtidig, innholdet ett og ett.
  await vent(() => h.querySelector(".editor-seksjon"));
  const fanetitler = [...h.querySelectorAll('[role="tab"]')].map((f) => f.textContent);
  assert.deepEqual(fanetitler, [t("ui.editor.fane.grunn"),
    t("ui.editor.fane.roller"), t("ui.editor.fane.handlinger"),
    t("ui.editor.fane.overstyring")]);
  gaaTilFane(h, t("ui.editor.fane.roller"));
  assert.ok(h.textContent.includes(t("ui.editor.roller")));
  gaaTilFane(h, t("ui.editor.fane.handlinger"));
  assert.ok(h.textContent.includes(t("ui.editor.handlinger")));
  assert.ok(h.textContent.includes("ordre.bekreft"));
  assert.ok(h.querySelector("select"), "modus-velger mangler");
  gaaTilFane(h, t("ui.editor.fane.grunn"));
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);

  // Sett policy_id (første tekstfelt = policy_id) og modus.
  const pid = h.querySelector("input.felt-inp");
  pid.value = "acme-netthandel";
  pid.dispatchEvent(new window.Event("input"));
  gaaTilFane(h, t("ui.editor.fane.handlinger"));
  const sel = h.querySelector("select");
  sel.value = "alltid_stopp";
  sel.dispatchEvent(new window.Event("change"));

  // Lagre.
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  assert.equal(POST.opts.method, "POST");
  assert.ok(POST.url.includes("/v1/policyutkast"));
  assert.equal(POST.opts.headers["X-Disponit-CSRF"], "tok123");
  assert.ok(POST.opts.headers["Idempotency-Key"], "mangler Idempotency-Key");
  const sendt = JSON.parse(POST.opts.body);
  assert.equal(sendt.policy_id, "acme-netthandel");
  assert.equal(sendt.innhold.handlinger[0].modus, "alltid_stopp");
  // Codex P1: malen bærer `status: utkast`. Lagres den slik, validerer
  // utkastet ikke — og etter frysing kan statusen ikke rettes noe sted, for
  // editoren har ingen statuskontroll. Utkastet blir derfor bygget som det
  // aktiveringen faktisk skriver.
  assert.equal(sendt.innhold.meta.status, "produksjon",
    "utkastet ble lagret med malens status og kan ikke aktiveres");
  await vent(() => aapnet === "u-ny");
  assert.equal(aapnet, "u-ny");
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
});

test("Grunnopplysninger: eier får VITE at utkastet blir en produksjonspolicy",
  async () => {
    // Statusen skrives inn i dokumentet ved lagring. En stille endring av et
    // felt eier tror hun eier, er ikke greit — så den står i klartekst, som en
    // opplysning og ikke som et valg (de andre statusene kan ikke aktiveres).
    const h = nyHoved();
    visPolicyeditor(h, ctx(), { startPolicy: {
      meta: { policy_id: "acme", versjon: "1.0.0", bransjemal: "x",
              status: "utkast" },
      roller: [], handlinger: [],
    } });
    await vent(() => h.querySelector(".editor-seksjon"));
    assert.ok(h.textContent.includes(t("ui.editor.status_laast")),
      "editoren sier ikke hva utkastet lagres som");
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
  });

test("Rediger: laster utkastets innhold og PUTer med utkastversjon", async () => {
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  POST = undefined;
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { utkast_id: "u-1", aapneUtkast: () => {} });
  await vent(() => h.querySelector(".editor-seksjon"));
  // policy_id-feltet er låst ved redigering.
  assert.ok(h.querySelector("input[disabled]"), "policy_id skal være låst");
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  assert.equal(POST.opts.method, "PUT");
  assert.ok(POST.url.includes("/v1/policyutkast/u-1"));
  assert.equal(JSON.parse(POST.opts.body).utkastversjon, 2);
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
});

// Codex P2: et utkast der dokumentets `meta.policy_id` ikke er radens er
// INNELÅST uten dette. Valideringen nekter å fryse det og viser eier tilbake
// til editoren — der id-feltet er låst, altså der den eneste feilen ikke kan
// rettes. Feltet fylles derfor fra raden, og lagringen bærer rettelsen videre.
test("Rediger: fremmed id i dokumentet rettes til radens, og lagres", async () => {
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  POST = undefined;
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { utkast_id: "u-1", aapneUtkast: () => {} });
  await vent(() => h.querySelector(".editor-seksjon"));

  // Fikstur: raden er «acme», dokumentet oppgir «netthandel-no».
  const pid = h.querySelector("input.felt-inp");
  assert.equal(pid.value, "acme", "feltet skal vise radens identitet");
  assert.ok(pid.hasAttribute("disabled"), "identiteten er fortsatt låst");
  // Rettelsen er eiers å vite om: hjelpeteksten sier hva som sto der.
  const hint = h.querySelector(".felt-hint").textContent;
  assert.ok(hint.includes("netthandel-no"),
    "hjelpeteksten skal nevne id-en dokumentet oppga");
  assert.ok(!hint.includes("{id}"), "plassholderen er ikke byttet ut");
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);

  // ... og lagringen skriver den inn i dokumentet: neste validering går
  // gjennom, uten at eier måtte forlate utkastet.
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  assert.equal(JSON.parse(POST.opts.body).innhold.meta.policy_id, "acme");
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
});

// Codex P2: raden lagres med den id-en som sendes, og den kan ALDRI endres.
// Bryter den skjemaets form, er utkastet dødfødt: radens id kan ikke skrives
// inn i dokumentet (skjemaet avviser den), og en skjemagyldig id spriker fra
// raden. Serveren avviser den nå — og eier skal møte kravet ved feltet.
test("Ny: en policy-ID som bryter formen stoppes før lagring", async () => {
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  POST = undefined;
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: MAL, aapneUtkast: () => {} });
  await vent(() => h.querySelector(".editor-seksjon"));
  const pid = h.querySelector("input.felt-inp");

  for (const ugyldig of ["ACME", "ac", "acme_no"]) {
    pid.value = ugyldig;
    pid.dispatchEvent(new window.Event("input"));
    finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
    await vent(() => h.textContent.includes(t("ui.editor.policy_id_ugyldig")));
    assert.ok(h.textContent.includes(t("ui.editor.policy_id_ugyldig")), ugyldig);
    assert.equal(POST, undefined, `${ugyldig} skal ikke ha blitt sendt`);
  }

  // …og en gyldig id går gjennom, så vakten er om FORMEN og ikke en blokade.
  h.querySelector("input.felt-inp").value = "acme-netthandel";
  h.querySelector("input.felt-inp").dispatchEvent(new window.Event("input"));
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  assert.equal(JSON.parse(POST.opts.body).policy_id, "acme-netthandel");
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
});

// Codex P3: en id med riktig FORM kan fortsatt være for stor for
// registernøkkelen, og «for lang» er ikke «feil tegn». Serveren har fått en
// egen kode (`policy_id_for_stor`) fordi `utkast_feilformet` ble oversatt til
// «innholdet er ikke gyldig JSON-struktur» — eier ble sendt for å reparere et
// dokument som var helt i orden. Her møter hun det ved feltet i stedet.
test("Ny: en policy-ID over registernøkkelens tak stoppes med egen melding",
     async () => {
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  POST = undefined;
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: MAL, aapneUtkast: () => {} });
  await vent(() => h.querySelector(".editor-seksjon"));
  const pid = h.querySelector("input.felt-inp");

  // Skjemagyldig form, 2500 byte: over det tenant-uavhengige taket.
  pid.value = "a".repeat(2500);
  pid.dispatchEvent(new window.Event("input"));
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => h.textContent.includes(t("ui.editor.policy_id_for_stor")));
  assert.ok(h.textContent.includes(t("ui.editor.policy_id_for_stor")));
  assert.equal(POST, undefined, "for stor id skal ikke ha blitt sendt");

  // En lang, men lagringsbar id går gjennom — kontrollen er et tak, ikke en
  // ny formregel, og den avviser aldri noe serveren ville godtatt.
  const lang = "a".repeat(200);
  pid.value = lang;
  pid.dispatchEvent(new window.Event("input"));
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  assert.equal(JSON.parse(POST.opts.body).policy_id, lang);
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
});

test("Ny: id-en trimmes likt i raden og i dokumentet", async () => {
  // Raden opprettes fra den trimmede id-en. Bar dokumentet råteksten, ville
  // utkastet vært i avvik allerede ved fødselen — og låst ute av valideringen
  // fra første stund, siden feltet låses etter opprettelse.
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  POST = undefined;
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: MAL, aapneUtkast: () => {} });
  await vent(() => h.querySelector(".editor-seksjon"));
  const pid = h.querySelector("input.felt-inp");
  pid.value = "  acme-netthandel  ";
  pid.dispatchEvent(new window.Event("input"));
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  const sendt = JSON.parse(POST.opts.body);
  assert.equal(sendt.policy_id, "acme-netthandel");
  assert.equal(sendt.innhold.meta.policy_id, "acme-netthandel");
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
});

test("Stabil nøkkel: retry etter nettverksfeil gjenbruker Idempotency-Key", async () => {
  // Codex R1: en retry av SAMME lagring (tapt svar) må gjenbruke nøkkelen, så
  // serveren REPLAYer i stedet for å duplisere.
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  const ekte = globalThis.fetch;
  const kalt = [];
  globalThis.fetch = async (url, opts) => {
    if (opts && opts.method && opts.method !== "GET") {
      kalt.push({ url, opts });
      if (kalt.length === 1) throw new TypeError("network");   // tapt svar
      return { ok: true, status: 200,
        json: async () => ({ utkast_id: "u-ny", utkastversjon: 1 }) };
    }
    return { ok: false, status: 404, json: async () => ({ feil: "x" }) };
  };
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: MAL, aapneUtkast: () => {} });
  await vent(() => h.querySelector(".editor-seksjon"));
  gaaTilFane(h, t("ui.editor.fane.roller"));
  const pid = h.querySelector("input.felt-inp");
  pid.value = "acme"; pid.dispatchEvent(new window.Event("input"));

  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => kalt.length === 1);
  // Re-klikk (samme innhold) → retry.
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => kalt.length >= 2);
  assert.equal(kalt[0].opts.headers["Idempotency-Key"],
    kalt[1].opts.headers["Idempotency-Key"],
    "retry med samme innhold MÅ gjenbruke idempotensnøkkelen");
  globalThis.fetch = ekte;
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
});

test("Roller: legg til og fjern re-tegner", async () => {
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: MAL, aapneUtkast: () => {} });
  await vent(() => h.querySelector(".editor-seksjon"));
  gaaTilFane(h, t("ui.editor.fane.roller"));
  const foer = h.querySelectorAll(".editor-liste .editor-rad").length;
  finnKnapp(h, t("ui.editor.legg_til_rolle"))
    .dispatchEvent(new window.Event("click"));
  await vent(() =>
    h.querySelectorAll(".editor-liste .editor-rad").length === foer + 1);
  assert.equal(h.querySelectorAll(".editor-liste .editor-rad").length, foer + 1);
});

test("Roller: en rolle handlinger peker på kan ikke fjernes ved et uhell", async () => {
  // Dette er feilen som faktisk skjedde: eier fjernet rollen `agent`, og fikk
  // seks valideringsfeil som pekte på handlinger han aldri hadde rørt.
  // Referansen er kjent i det øyeblikket knappen tegnes, så flaten skal si det
  // DER — ikke la validatoren si det etterpå.
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: {
    meta: { policy_id: "p-1", versjon: "0.1.0", bransjemal: "x", status: "utkast" },
    roller: [{ id: "agent" }, { id: "ubrukt" }],
    handlinger: [{ id: "faktura.bokfor", tillatt_for: ["agent"] },
                 { id: "betaling.utfor", tillatt_for: ["agent"] }],
  } });
  gaaTilFane(h, t("ui.editor.fane.roller"));
  await vent(() => h.querySelectorAll(".editor-rad").length >= 2);

  const rader = [...h.querySelectorAll(".editor-rad")];
  const iBruk = rader.find((r) => r.textContent.includes("faktura.bokfor"));
  assert.ok(iBruk, "raden sier ikke hvilke handlinger som holder rollen");
  const sperret = iBruk.querySelector("button");
  assert.ok(sperret.hasAttribute("disabled"),
    "en rolle i bruk kunne fjernes — da blir policyen ugyldig ved validering");
  assert.ok(sperret.getAttribute("title").includes("betaling.utfor"),
    "forklaringen nevner ikke alle handlingene som holder rollen");

  // …og en UBRUKT rolle skal fortsatt kunne fjernes. En vakt som sperrer alt
  // er ikke en vakt, den er en blokkering.
  const fri = rader.find((r) => !r.textContent.includes("faktura.bokfor")
    && r.querySelector("button"));
  assert.ok(!fri.querySelector("button").hasAttribute("disabled"),
    "en ubrukt rolle skal kunne fjernes");
});

test("Roller: en rolle som BARE menneskelig overstyring bruker er også låst", async () => {
  // `menneskelig_overstyring.krever_rolle` er en rollereferanse på lik linje
  // med `tillatt_for`: schema.py avviser policyen med «ukjent rolle» når den
  // mangler. Uten den i vakten så rollen fjernbar ut, og knappen førte rett i
  // den samme fella vakten er til for å stenge.
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: {
    meta: { policy_id: "p-2", versjon: "0.1.0", bransjemal: "x", status: "utkast" },
    roller: [{ id: "okonomi" }, { id: "ubrukt" }],
    handlinger: [{ id: "faktura.bokfor", tillatt_for: [] }],
    menneskelig_overstyring: { krever_rolle: "okonomi", godkjennbare: [] },
  } });
  gaaTilFane(h, t("ui.editor.fane.roller"));
  await vent(() => h.querySelectorAll(".editor-rad").length >= 2);

  const rader = [...h.querySelectorAll(".editor-rad")];
  const merke = t("ui.editor.rolle_i_bruk_overstyring");
  const overstyring = rader.find((r) => r.textContent.includes(merke));
  assert.ok(overstyring, "raden sier ikke at overstyringen holder rollen");
  assert.ok(overstyring.querySelector("button").hasAttribute("disabled"),
    "rollen overstyringen krever kunne fjernes — policyen blir da ugyldig");

  const fri = rader.find((r) => !r.textContent.includes(merke)
    && r.querySelector("button"));
  assert.ok(!fri.querySelector("button").hasAttribute("disabled"),
    "en ubrukt rolle skal fortsatt kunne fjernes");
});

test("Policy-ID: malen foreslår sin egen id, og regelen står ved feltet", async () => {
  // Feltet var tomt, uten format og uten å si hva id-en brukes til. En ny id
  // lager en NY policy ved siden av den som gjelder — i stedet for å avløse
  // den — og «01» ble avvist av skjemaet uten at noen fikk vite hvorfor.
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: {
    meta: { policy_id: "tjenestebedrift-no", versjon: "0.2.0",
            bransjemal: "tjenestebedrift-no", status: "utkast" },
    roller: [], handlinger: [],
  } });
  await vent(() => h.querySelector(".felt-inp"));
  const felt = [...h.querySelectorAll(".felt")]
    .find((f) => f.textContent.includes(t("ui.editor.policy_id")));
  assert.ok(felt.textContent.includes("3"),
    "regelen om minst 3 tegn står ikke ved feltet");
  const hint = felt.querySelector(".felt-hint");
  assert.ok(hint, "ingen hjelpetekst");
  assert.equal(felt.querySelector("input").getAttribute("aria-describedby"),
    hint.id, "hjelpeteksten er ikke koblet til feltet for skjermlesere");
});

// --- Hva policy-id-en FAKTISK gjør avhenger av hva som gjelder i dag -----

// Hjelpeteksten lovet universelt at man «beholder malens id for å avløse
// policyen som gjelder i dag». Det kunne klienten ikke vite: aktivering er per
// `policy_id`, katalogen har flere maler, og en kunde som opprettet policyen
// med sin EGEN id har ikke malens id i det hele tatt. Følger man rådet der,
// får man en NY policyserie ved siden av — mens den gamle fortsatt gjelder.
async function velgMalMedAktiv(svarPaaAktiv) {
  const ekte = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    if (url.split("?")[0] === "/v1/policy/aktiv") return svarPaaAktiv();
    return ekte(url, opts);
  };
  try {
    const h = nyHoved();
    visPolicyeditor(h, ctx(), { aapneUtkast: () => {} });
    await vent(() => h.querySelector(".mal-liste"));
    h.querySelector(".mal-kort").dispatchEvent(new window.Event("click"));
    await vent(() => h.querySelector(".editor-seksjon"));
    // Policy-ID bor på grunnopplysninger — første trinn, altså allerede åpent.
    const felt = [...h.querySelectorAll(".felt")]
      .find((f) => f.textContent.includes(t("ui.editor.policy_id")));
    return { felt, inp: felt.querySelector("input"),
             hint: felt.querySelector(".felt-hint").textContent };
  } finally {
    globalThis.fetch = ekte;
  }
}

test("Policy-ID: feltet fylles med den AKTIVE policyens id, ikke malens", async () => {
  const { inp, hint } = await velgMalMedAktiv(() => ({
    ok: true, status: 200,
    json: async () => ({ policy_id: "acme-netthandel", versjon: "1.0.0" }) }));
  assert.equal(inp.value, "acme-netthandel",
    "malens id ble foreslått som om den var dagens policy — den avløser " +
    "ingenting, den lager en ny policyserie ved siden av");
  assert.ok(hint.includes("acme-netthandel"),
    "teksten sier ikke hvilken id som faktisk gjelder i dag");
  assert.ok(!hint.includes("netthandel-no") || hint.includes("acme"),
    "teksten peker fortsatt på malens id");
});

test("Policy-ID: uten aktiv policy loves ingen avløsning", async () => {
  // 404 = vi VET at ingenting gjelder. Da er det ingenting å avløse, og
  // teksten skal si nettopp det i stedet for å love det motsatte.
  const { inp, hint } = await velgMalMedAktiv(() => ({
    ok: false, status: 404, json: async () => ({ feil: "ikke_funnet" }) }));
  assert.equal(inp.value, "netthandel-no",
    "uten aktiv policy er malens egen id et greit forslag");
  assert.equal(hint, t("ui.editor.policy_id_hint_ingen_aktiv"));
});

test("Policy-ID: uten svar på hva som gjelder påstås ingenting", async () => {
  // 403 (ingen `policy:read`) og 500 (registeret har flere aktive og nekter å
  // velge én) betyr at flaten IKKE VET. Da skal den si regelen — samme id
  // viderefører serien, en annen lager en ny — og ikke hva som gjelder.
  const { inp, hint } = await velgMalMedAktiv(() => ({
    ok: false, status: 403, json: async () => ({ feil: "ingen_tilgang" }) }));
  assert.equal(inp.value, "netthandel-no");
  assert.equal(hint, t("ui.editor.policy_id_hint"));
  assert.ok(!hint.includes(t("ui.editor.policy_id_hint_ingen_aktiv")),
    "flaten påstår at ingenting gjelder, men den vet det ikke");
});

// --- Rolle-ID-en er REDIGERBAR, og vakten må følge med -------------------

const rolleRad = (h, idVerdi) =>
  [...h.querySelectorAll(".editor-liste .editor-rad")]
    .find((r) => r.querySelector("input.felt-inp").value === idVerdi);

function skrivId(rad, verdi) {
  const inp = rad.querySelector("input.felt-inp");
  inp.value = verdi;
  inp.dispatchEvent(new window.Event("input"));
}

test("Roller: vakten regnes om når rolle-ID-en endres", async () => {
  // Vakten ble regnet ut ÉN gang, da raden ble tegnet, mens ID-feltet
  // fortsatte å mutere `r.id` uten å tegne om. Retter eier et ugyldig utkast
  // ved å gi den UBRUKTE rollen det navnet `tillatt_for` peker på, sto «Fjern»
  // igjen aktiv fra forrige navn — og kunne fjerne rollen som nå var påkrevd.
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: {
    meta: { policy_id: "p-3", versjon: "0.1.0", bransjemal: "x",
            status: "utkast" },
    roller: [{ id: "ubrukt" }],
    handlinger: [{ id: "faktura.bokfor", tillatt_for: ["agent"] }],
  } });
  gaaTilFane(h, t("ui.editor.fane.roller"));
  await vent(() => h.querySelector(".editor-liste .editor-rad"));

  const rad = rolleRad(h, "ubrukt");
  const fjern = rad.querySelector("button");
  assert.ok(!fjern.hasAttribute("disabled"), "ubrukt rolle skal kunne fjernes");

  skrivId(rad, "agent");
  assert.ok(fjern.hasAttribute("disabled"),
    "rollen er nå referert av faktura.bokfor, men «Fjern» sto igjen aktiv");
  assert.ok(rad.textContent.includes("faktura.bokfor"),
    "raden sier ikke hvem som holder rollen etter navnebyttet");

  // Og klikket skal ikke slippe gjennom selv om knappen skulle stå feil:
  // referansene avgjør i det øyeblikket det klikkes, ikke da raden ble tegnet.
  fjern.dispatchEvent(new window.Event("click"));
  assert.ok(rolleRad(h, "agent"), "en referert rolle ble fjernet ved klikk");
});

test("Roller: navnebytte tar referansene med seg", async () => {
  // Å endre rolle-ID-en er et NAVNEBYTTE, ikke en ny rolle. Uten at
  // referansene følger med, gjorde `agent` → `agent-ny` handlingenes
  // `tillatt_for: ["agent"]` foreldreløse — nøyaktig den «ukjent
  // rolle»-tilstanden fjerningsvakten finnes for å stenge, bare via en annen
  // dør. Tømming underveis skal IKKE propagere: eier som sletter feltet for å
  // skrive noe nytt, skal ikke få `tillatt_for: [""]` på veien.
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  POST = undefined;
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { aapneUtkast: () => {}, startPolicy: {
    meta: { policy_id: "p-4", versjon: "0.1.0", bransjemal: "x",
            status: "utkast" },
    roller: [{ id: "agent" }],
    handlinger: [{ id: "faktura.bokfor", tillatt_for: ["agent"] },
                 { id: "betaling.utfor", tillatt_for: ["agent"] }],
    menneskelig_overstyring: { krever_rolle: "agent", godkjennbare: [] },
  } });
  gaaTilFane(h, t("ui.editor.fane.roller"));
  await vent(() => h.querySelector(".editor-liste .editor-rad"));

  const rad = rolleRad(h, "agent");
  assert.ok(rad.querySelector("button").hasAttribute("disabled"),
    "rollen er i bruk og skal være låst før navnebyttet");
  skrivId(rad, "");                       // tømmer først …
  skrivId(rad, "agent-ny");               // … og skriver så det nye navnet

  assert.ok(rad.querySelector("button").hasAttribute("disabled"),
    "rollen er fortsatt i bruk etter navnebyttet, men vakten slapp den fri");
  assert.ok(rad.textContent.includes("faktura.bokfor")
    && rad.textContent.includes("betaling.utfor"),
    "referansene fulgte ikke med navnebyttet");

  // Det som faktisk LAGRES er fasiten — DOM-en kan ha rett av andre grunner.
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  const sendt = JSON.parse(POST.opts.body).innhold;
  for (const handling of sendt.handlinger) {
    assert.deepEqual(handling.tillatt_for, ["agent-ny"],
      `${handling.id} peker på et rollenavn som ikke finnes`);
  }
  assert.equal(sendt.menneskelig_overstyring.krever_rolle, "agent-ny",
    "menneskelig_overstyring peker på et rollenavn som ikke finnes");
  assert.equal(sendt.roller[0].id, "agent-ny");
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
});

test("Roller: et utkast med ødelagt `tillatt_for` kan fortsatt ÅPNES", async () => {
  // Utkast opprettes og redigeres uten skjemavalidering — den er et eget steg
  // — så `handlinger[].tillatt_for` kan inneholde et objekt eller et tall.
  // Server-siden klassifiserer bevisst en ikke-liste som «tom» framfor å
  // avvise den, så verdien når fram hit. Med `.includes` rett på verdien kastet
  // editoren `TypeError` mens den TEGNET, og eieren kunne ikke åpne utkastet
  // for å reparere nettopp det som var galt.
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: {
    meta: { policy_id: "p-6", versjon: "0.1.0", bransjemal: "x",
            status: "utkast" },
    roller: [{ id: "agent" }, { id: "okonomi" }],
    handlinger: [{ id: "faktura.bokfor", tillatt_for: { rolle: "agent" } },
                 { id: "betaling.utfor", tillatt_for: 5 },
                 { id: "rapport.les", tillatt_for: ["okonomi"] }],
  } });
  gaaTilFane(h, t("ui.editor.fane.roller"));
  await vent(() => h.querySelector(".editor-liste .editor-rad"));

  const agent = rolleRad(h, "agent");
  assert.ok(agent, "editoren tegnet ikke rollene for et ødelagt utkast");
  assert.ok(!agent.querySelector("button").hasAttribute("disabled"),
    "en ikke-liste er ingen rollereferanse og skal ikke låse raden");
  // …og en ekte referanse i samme utkast holder fortsatt sin rolle låst.
  assert.ok(rolleRad(h, "okonomi").querySelector("button")
    .hasAttribute("disabled"),
    "den GYLDIGE referansen ble borte sammen med de ødelagte");
});

test("Roller: navnebytte som PASSERER en annen rolles id lar den i fred", async () => {
  // Navnet skrives tegn for tegn. Med rollene `admin` og `ad` går veien til
  // `admin2` gjennom `admin`: `ad` → `adm` → `admi` → `admin` → `admin2`. Med
  // global tekstutskifting slukte raden den ekte `admin`-rollens referanser i
  // det mellomsteget, og neste tastetrykk flyttet dem videre — `rapport.les`
  // ble stille flyttet fra `admin` til `admin2`. Resultatet er strukturelt
  // gyldig, så servervalideringen fanger det ikke; det er nettopp derfor det
  // må stanses her.
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  POST = undefined;
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { aapneUtkast: () => {}, startPolicy: {
    meta: { policy_id: "p-5", versjon: "0.1.0", bransjemal: "x",
            status: "utkast" },
    roller: [{ id: "admin" }, { id: "ad" }],
    handlinger: [{ id: "rapport.les", tillatt_for: ["admin"] },
                 { id: "faktura.bokfor", tillatt_for: ["ad"] }],
    menneskelig_overstyring: { krever_rolle: "admin", godkjennbare: [] },
  } });
  gaaTilFane(h, t("ui.editor.fane.roller"));
  await vent(() => h.querySelector(".editor-liste .editor-rad"));

  const rad = rolleRad(h, "ad");
  for (const steg of ["adm", "admi", "admin", "admin2"]) skrivId(rad, steg);

  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  const sendt = JSON.parse(POST.opts.body).innhold;
  const av = (id) => sendt.handlinger.find((x) => x.id === id).tillatt_for;
  assert.deepEqual(av("faktura.bokfor"), ["admin2"],
    "den redigerte rollens egen referanse fulgte ikke med navnebyttet");
  assert.deepEqual(av("rapport.les"), ["admin"],
    "en ANNEN rolles referanse ble dratt med navnebyttet — stille "
    + "rettighetsendring som validatoren ikke fanger");
  assert.equal(sendt.menneskelig_overstyring.krever_rolle, "admin",
    "overstyringen pekte på `admin` og skal fortsatt gjøre det");
  assert.deepEqual(sendt.roller.map((r) => r.id), ["admin", "admin2"]);
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
});

test("Grenser: valuta og tidsvindu velges, de skrives ikke", async () => {
  // Feltene var fritekst: valuta «kommaseparert» (en rå array lekket ut i
  // UI-et) og tidsvindu en streng med egen grammatikk. Et felt som bare kan
  // produsere gyldige verdier fjerner hele feilklassen.
  const policy = {
    meta: { policy_id: "p-1", versjon: "0.1.0", bransjemal: "x", status: "utkast" },
    roller: [{ id: "agent" }],
    handlinger: [{ id: "betaling.utfor", modus: "manuell", tillatt_for: ["agent"],
      grenser: { belop_maks: "1000.00", valuta: ["NOK"],
        tidsvindu: "man-fre 08:00-16:00" } }],
  };
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: policy });
  gaaTilFane(h, t("ui.editor.fane.handlinger"));
  await vent(() => h.querySelector(".editor-kort"));

  const kort = h.querySelector(".editor-kort");
  const valutaSel = [...kort.querySelectorAll("select")]
    .find((s) => [...s.options].some((o) => o.value === "NOK"));
  assert.ok(valutaSel, "valuta er ikke et nedtrekk");
  assert.equal(valutaSel.value, "NOK");
  assert.ok([...valutaSel.options]
    .every((o) => o.value === "" || /^[A-Z]{3}$/.test(o.value)),
    "nedtrekket kan produsere en kode skjemaet avviser");

  // Tidsvinduet er dager + klokkeslett, og skrives tilbake på skjemaets form.
  assert.equal(kort.querySelectorAll('input[type="time"]').length, 2,
    "tidsvindu har ikke klokkeslettvelgere");
  const dagSel = [...kort.querySelectorAll("select")]
    .filter((s) => [...s.options].some((o) => o.value === "man"));
  assert.equal(dagSel.length, 2, "tidsvindu har ikke dagvelgere");
  dagSel[1].value = "lor";
  dagSel[1].dispatchEvent(new window.Event("change"));
  // Editoren dyp-kopierer `startPolicy`, så originalen endrer seg ALDRI —
  // en påstand om den ville vært grønn uansett hva velgeren skrev. Verdien
  // leses derfor av det som faktisk sendes til serveren.
  POST = undefined;
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  const sendt = JSON.parse(POST.opts.body);
  const tv = sendt.innhold.handlinger[0].grenser.tidsvindu;
  assert.match(tv,
    /^(man|tir|ons|tor|fre|lor|son)-(man|tir|ons|tor|fre|lor|son) \d{2}:\d{2}-\d{2}:\d{2}$/,
    "velgeren skrev en verdi skjemaet ikke godtar");
  assert.ok(tv.startsWith("man-lor"), `tidsvindu ble «${tv}»`);
});

test("Grenser: et nedtrekk kaster aldri en valuta policyen alt har", async () => {
  // En policy kan bære flere valutaer, og en naiv `g.valuta = [valgt]` ville
  // slettet resten stille. Koden som ikke er skrevet kan ikke feile — men den
  // som ER skrevet skal måles.
  const policy = {
    meta: { policy_id: "p-1", versjon: "0.1.0", bransjemal: "x", status: "utkast" },
    roller: [{ id: "agent" }],
    handlinger: [{ id: "betaling.utfor", modus: "manuell", tillatt_for: ["agent"],
      grenser: { valuta: ["CHF", "EUR"] } }],
  };
  const cookieDesc = Object.getOwnPropertyDescriptor(
    window.Document.prototype, "cookie");
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: policy });
  gaaTilFane(h, t("ui.editor.fane.handlinger"));
  await vent(() => h.querySelector(".editor-kort"));
  const kort = h.querySelector(".editor-kort");
  const sel = [...kort.querySelectorAll("select")]
    .find((s) => [...s.options].some((o) => o.value === "CHF"));
  assert.ok(sel, "en ukjent kode fra policyen mangler i nedtrekket");
  assert.equal(sel.value, "CHF");
  sel.value = "NOK";
  sel.dispatchEvent(new window.Event("change"));
  POST = undefined;
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  assert.deepEqual(
    JSON.parse(POST.opts.body).innhold.handlinger[0].grenser.valuta,
    ["NOK", "EUR"], "de øvrige valutaene ble kastet");
});

test("Grenser: en beholdt valuta i HALEN er like redigerbar som den første",
  async () => {
    // Codex P2. Kontrollen var ett nedtrekk over den FØRSTE koden, så en kode
    // som lå bak — `["NOK","CHF"]` — sto nevnt i et hint uten å kunne røres:
    // eier kunne verken velge den eller fjerne NOK og beholde den, slik det
    // gamle fritekstfeltet tillot. Nå har hver kode sin egen rad, så halen har
    // nøyaktig de samme knappene som hodet.
    Object.defineProperty(document, "cookie", { configurable: true,
      get: () => "__Host-disponit_csrf=tok123" });
    const h = nyHoved();
    visPolicyeditor(h, ctx(),
      { startPolicy: medGrenser({ valuta: ["NOK", "CHF"] }) });
    gaaTilFane(h, t("ui.editor.fane.handlinger"));
  await vent(() => h.querySelector(".editor-kort"));
    assert.equal(valutaRader(h).length, 2,
      "en beholdt kode i halen har ingen rad å bli redigert fra");
    assert.equal(valutaRader(h)[1].querySelector("select").value, "CHF");

    // Eier fjerner NOK og beholder CHF — veien fritekstfeltet hadde.
    fjernValuta(h, 0);
    await vent(() => valutaRader(h).length === 1);
    POST = undefined;
    finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
    await vent(() => POST);
    assert.deepEqual(
      JSON.parse(POST.opts.body).innhold.handlinger[0].grenser.valuta, ["CHF"],
      "eier kunne ikke fjerne NOK og beholde CHF");
  });

test("Grenser: nedtrekket tilbyr HELE den kanoniske valutamengden",
  async () => {
    // Codex P2. Nedtrekket bar seks koder pluss de policyen alt hadde. Eier
    // som skulle sette CAD, CHF eller JPY hadde derfor ingen vei dit, selv om
    // `_valider_grenser` godtar dem — erstatningen kunne mindre enn
    // fritekstfeltet den avløste. Autoriteten er `ISO4217` i lesing.py;
    // `test_ui_kontrakt.py` pinner lista mot den, denne måler kontrollen.
    Object.defineProperty(document, "cookie", { configurable: true,
      get: () => "__Host-disponit_csrf=tok123" });
    const h = nyHoved();
    visPolicyeditor(h, ctx(), { startPolicy: medGrenser({ valuta: ["NOK"] }) });
    gaaTilFane(h, t("ui.editor.fane.handlinger"));
  await vent(() => h.querySelector(".editor-kort"));
    const sel = [...h.querySelectorAll(".editor-kort select")]
      .find((s) => [...s.options].some((o) => o.value === "NOK"));
    for (const kode of ["CAD", "JPY", "CHF", "ZAR"]) {
      assert.ok([...sel.options].some((o) => o.value === kode),
        `${kode} godtas av serveren, men kan ikke velges`);
    }
    // «XXX» er tre store bokstaver og består skjemaets mønster — men det er
    // ingen valuta, og en policy med den leses som `policy_korrupt`.
    assert.equal([...sel.options].some((o) => o.value === "XXX"), false,
      "nedtrekket kan produsere en kode _valider_grenser vraker");
    sel.value = "JPY";
    sel.dispatchEvent(new window.Event("change"));
    POST = undefined;
    finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
    await vent(() => POST);
    assert.deepEqual(
      JSON.parse(POST.opts.body).innhold.handlinger[0].grenser.valuta, ["JPY"],
      "eiers valg utenfor kortlista ble ikke lagret");
  });

test("Grenser: reparasjonen berger ingen kode serveren vraker", async () => {
  // Codex P2. «Behold» målte mot `^[A-Z]{3}$`, så `["XXX","XXX"]` ble berget
  // til `["XXX"]` — porten åpnet, utkastet kunne aktiveres, og først ved
  // neste lesning av den AKTIVE policyen kom svaret: `policy_korrupt`. Da
  // hadde reparasjonen bare flyttet feilen lenger unna feltet som viste den.
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  const h = nyHoved();
  visPolicyeditor(h, ctx(),
    { startPolicy: medGrenser({ valuta: ["XXX", "XXX"] }) });
  gaaTilFane(h, t("ui.editor.fane.handlinger"));
  await vent(() => h.querySelector(".editor-kort"));
  assert.ok(h.querySelector(".editor-reparasjon"),
    "en liste av ikke-valutaer ble ikke vist som en grense som må repareres");
  assert.equal(reparasjonsvalg(h, t("ui.editor.grense_behold")), undefined,
    "«behold» tilbød en kode _valider_grenser vraker");

  POST = undefined;
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector(".editor-feil"));
  assert.equal(POST, undefined, "et utkast med «XXX» slapp gjennom porten");

  // Veien ut finnes, den er bare eiers: fjern grensen.
  reparasjonsvalg(h, t("ui.editor.grense_fjern"))
    .dispatchEvent(new window.Event("click"));
  POST = undefined;
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  assert.equal(
    "valuta" in JSON.parse(POST.opts.body).innhold.handlinger[0].grenser, false,
    "eiers «fjern» fjernet ikke grensen");
});

test("Grenser: beløpshintet TEGNES, og henger på feltet", async () => {
  // Codex P2. Hintet ble sendt som et femte argument til en `tekstfelt` som
  // tok fire — så det forsvant i begge språk, og beløpsfeltet sto igjen uten
  // den formveiledningen hele endringen handlet om.
  const policy = {
    meta: { policy_id: "p-1", versjon: "0.1.0", bransjemal: "x", status: "utkast" },
    roller: [{ id: "agent" }],
    handlinger: [{ id: "betaling.utfor", modus: "manuell", tillatt_for: ["agent"],
      grenser: { belop_maks: "1000.00" } }],
  };
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: policy });
  gaaTilFane(h, t("ui.editor.fane.handlinger"));
  await vent(() => h.querySelector(".editor-kort"));
  const kort = h.querySelector(".editor-kort");
  const hint = [...kort.querySelectorAll(".felt-hint")]
    .find((n) => n.textContent === t("ui.editor.belop_hint"));
  assert.ok(hint, "beløpshintet ble ikke tegnet");
  // Synlig er ikke nok: hintet må nå den som ikke SER det.
  const inp = kort.querySelector(`[aria-describedby="${hint.id}"]`);
  assert.ok(inp, "hintet henger ikke på noe felt");
  assert.equal(inp.value, "1000.00", "hintet henger på feil felt");
  // Et felt uten hint skal ikke peke i tomme luften.
  const uten = [...h.querySelectorAll("input[aria-describedby]")]
    .filter((i) => !h.querySelector(`#${i.getAttribute("aria-describedby")}`));
  assert.deepEqual(uten, [], "aria-describedby peker på en id som ikke finnes");
});

test("Grenser: et tømt klokkeslett lager ikke et ugyldig tidsvindu", async () => {
  // Codex P2. Et tømt `type="time"` gir "", og lagringen kjører ingen native
  // skjemavalidering — så «man-fre -16:00» gikk rett inn i utkastet og døde
  // først hos validatoren. Å FJERNE vinduet er av/på-bryterens jobb.
  const policy = {
    meta: { policy_id: "p-1", versjon: "0.1.0", bransjemal: "x", status: "utkast" },
    roller: [{ id: "agent" }],
    handlinger: [{ id: "betaling.utfor", modus: "manuell", tillatt_for: ["agent"],
      grenser: { tidsvindu: "man-fre 08:00-16:00" } }],
  };
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: policy });
  gaaTilFane(h, t("ui.editor.fane.handlinger"));
  await vent(() => h.querySelector(".editor-kort"));
  const kort = h.querySelector(".editor-kort");
  const klokker = [...kort.querySelectorAll('input[type="time"]')];
  assert.equal(klokker.length, 2);
  klokker[0].value = "";
  klokker[0].dispatchEvent(new window.Event("change"));
  assert.equal(klokker[0].value, "08:00",
    "feltet ble stående tomt i stedet for å falle tilbake");

  POST = undefined;
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  const tv = JSON.parse(POST.opts.body).innhold.handlinger[0].grenser.tidsvindu;
  assert.equal(tv, "man-fre 08:00-16:00",
    `et tomt felt ble serialisert: «${tv}»`);

  // Bryteren er veien ut: den fjerner grensen i stedet for å halvskrive den.
  const bryter = kort.querySelector('input[type="checkbox"]');
  bryter.checked = false;
  bryter.dispatchEvent(new window.Event("change"));
  POST = undefined;
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  assert.equal(
    "tidsvindu" in JSON.parse(POST.opts.body).innhold.handlinger[0].grenser,
    false, "av/på-bryteren fjernet ikke tidsvinduet");
});

// Ett kort med én ugyldig lagret grense, klart til å åpnes.
function medGrenser(grenser) {
  return {
    meta: { policy_id: "p-1", versjon: "0.1.0", bransjemal: "x",
      status: "utkast" },
    roller: [{ id: "agent" }],
    handlinger: [{ id: "betaling.utfor", modus: "manuell",
      tillatt_for: ["agent"], grenser }],
  };
}

// Valutakontrollen: én rad per kode, og et eget nedtrekk som legger til.
function valutaRader(rot) {
  return [...rot.querySelectorAll(".valuta-rad")];
}

function leggTilValuta(rot) {
  return rot.querySelector(".valuta-legg-til select");
}

function velgValuta(sel, kode) {
  sel.value = kode;
  sel.dispatchEvent(new window.Event("change"));
}

function fjernValuta(rot, i) {
  valutaRader(rot)[i].querySelector("button")
    .dispatchEvent(new window.Event("click"));
}

function reparasjonsvalg(h, tekst) {
  return [...h.querySelectorAll(".editor-reparasjon button")]
    .find((k) => k.textContent.startsWith(tekst));
}

test("Grenser: en ugyldig lagret grense repareres av EIER, ikke av editoren",
  async () => {
    // Eierverdikt (P1). Editoren muterte modellen allerede under tegning: en
    // ugyldig verdi ble byttet ut med et oppdiktet standardvindu, og tomme/
    // ikke-streng-verdier ble slettet. Da kunne eier åpne et eldre utkast,
    // endre et HELT ANNET felt, lagre — og ha byttet ut eller fjernet en
    // tidsgrense uten å velge det. Fravær av `tidsvindu` betyr ingen
    // tidsbegrensning, så slettingen utvider fullmakten stille. Målt her:
    // (1) åpning + urelatert endring rører ikke grensen, (2) lagring slipper
    // ikke gjennom før eier har valgt.
    Object.defineProperty(document, "cookie", { configurable: true,
      get: () => "__Host-disponit_csrf=tok123" });
    for (const raa of ["xyz-abc 99:99-16:00", "", null, 0, { fra: "08:00" }]) {
      const h = nyHoved();
      visPolicyeditor(h, ctx(), { startPolicy: medGrenser({ tidsvindu: raa }) });
      gaaTilFane(h, t("ui.editor.fane.handlinger"));
  await vent(() => h.querySelector(".editor-kort"));
      const kort = h.querySelector(".editor-kort");
      assert.ok(kort.querySelector(".editor-reparasjon"),
        `${JSON.stringify(raa)} ble ikke vist som en grense som må repareres`);
      assert.equal(kort.querySelectorAll('input[type="time"]').length, 0,
        "en uleselig verdi ble tegnet som et vindu");

      // Eier rører IKKE tidsvinduet — bare et annet felt på samme kort.
      const modus = [...kort.querySelectorAll("select")]
        .find((s) => [...s.options].some((o) => o.value === "alltid_stopp"));
      modus.value = "alltid_stopp";
      modus.dispatchEvent(new window.Event("change"));
      POST = undefined;
      finnKnapp(h, t("ui.editor.lagre"))
        .dispatchEvent(new window.Event("click"));
      await vent(() => h.querySelector(".editor-feil"));
      assert.equal(POST, undefined,
        `${JSON.stringify(raa)} ble lagret uten at eier valgte reparasjon`);
      // Etter det avviste forsøket er kortet tegnet på nytt fra modellen, og
      // den viser fortsatt RÅVERDIEN: hverken erstattet eller slettet.
      assert.ok([...h.querySelectorAll(".editor-reparasjon p")]
        .some((p) => p.textContent.endsWith(`: ${JSON.stringify(raa)}`)),
        `${JSON.stringify(raa)} ble endret av å åpne og lagre`);

      // Eiers valg nummer én: fjern grensen. NÅ, og først nå, forsvinner den.
      reparasjonsvalg(h, t("ui.editor.grense_fjern"))
        .dispatchEvent(new window.Event("click"));
      POST = undefined;
      finnKnapp(h, t("ui.editor.lagre"))
        .dispatchEvent(new window.Event("click"));
      await vent(() => POST);
      const sendt = JSON.parse(POST.opts.body).innhold.handlinger[0];
      assert.equal("tidsvindu" in sendt.grenser, false,
        "eiers «fjern» fjernet ikke grensen");
      assert.equal(sendt.modus, "alltid_stopp",
        "den urelaterte endringen gikk tapt");
    }
  });

test("Grenser: standardvinduet er eiers valg, ikke editorens reparasjon",
  async () => {
    // Den andre veien ut av reparasjonstilstanden: eier kan sette standarden
    // — men det er et klikk, ikke noe som skjer av seg selv når kortet tegnes.
    Object.defineProperty(document, "cookie", { configurable: true,
      get: () => "__Host-disponit_csrf=tok123" });
    const h = nyHoved();
    visPolicyeditor(h, ctx(),
      { startPolicy: medGrenser({ tidsvindu: "xyz-abc 99:99-16:00" }) });
    gaaTilFane(h, t("ui.editor.fane.handlinger"));
  await vent(() => h.querySelector(".editor-kort"));
    reparasjonsvalg(h, t("ui.editor.grense_sett_standard"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => h.querySelector('input[type="time"]'));
    assert.equal(h.querySelectorAll(".editor-reparasjon").length, 0,
      "reparasjonstilstanden ble stående etter eiers valg");
    POST = undefined;
    finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
    await vent(() => POST);
    assert.equal(
      JSON.parse(POST.opts.body).innhold.handlinger[0].grenser.tidsvindu,
      "man-fre 08:00-16:00", "eiers valgte standardvindu ble ikke lagret");
  });

test("Grenser: valuta som mangler vises som uvalgt, ikke som NOK", async () => {
  // Codex P1. `grenser.valuta` er valgfri i skjemaet, og fraværet betyr noe
  // ANNET enn NOK: motoren sjekker valuta bare når feltet finnes. Et nedtrekk
  // som viste NOK uten å skrive NOK fortalte eier om en begrensning policyen
  // ikke hadde. Målt på begge sider: hva nedtrekket viser, og hva som SENDES.
  const policy = {
    meta: { policy_id: "p-1", versjon: "0.1.0", bransjemal: "x", status: "utkast" },
    roller: [{ id: "agent" }],
    handlinger: [{ id: "betaling.utfor", modus: "manuell", tillatt_for: ["agent"],
      grenser: { belop_maks: "1000.00" } }],
  };
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: policy });
  gaaTilFane(h, t("ui.editor.fane.handlinger"));
  await vent(() => h.querySelector(".editor-kort"));
  const kort = h.querySelector(".editor-kort");
  assert.equal(kort.querySelectorAll(".valuta-rad").length, 0,
    "en policy uten valuta viser en begrensning");
  assert.ok(kort.textContent.includes(t("ui.editor.valuta_ingen")),
    "tilstanden «ingen valutabegrensning» står ingen steder");

  POST = undefined;
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  assert.equal(
    "valuta" in JSON.parse(POST.opts.body).innhold.handlinger[0].grenser, false,
    "editoren fant på en valutabegrensning eieren aldri valgte");

  // …og veien tilbake: en lagt til kode kan tas AV igjen, ikke bare byttes.
  velgValuta(leggTilValuta(h), "EUR");
  await vent(() => h.querySelectorAll(".valuta-rad").length === 1);
  fjernValuta(h, 0);
  await vent(() => h.querySelectorAll(".valuta-rad").length === 0);
  POST = undefined;
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  assert.equal(
    "valuta" in JSON.parse(POST.opts.body).innhold.handlinger[0].grenser, false,
    "«ingen begrensning» kunne ikke velges tilbake");
});

test("Grenser: en valuta policyen alt har, kan ikke velges to ganger",
  async () => {
    // Codex P1. Å velge en kode som lå LENGER BAK i lista skrev den fram uten
    // å ta den ut der den lå: `["NOK","EUR"]` + EUR ga `["EUR","EUR"]`. Det
    // kanoniske skjemaet krever ikke unike koder, så utkastet validerer og kan
    // aktiveres — men `_valider_grenser` vraker duplikater, så senere lesninger
    // av den aktive policyen svarer `policy_korrupt`. Med én rad per kode kan
    // dubletten ikke lages i det hele tatt: en kode en annen rad bærer, står
    // ikke i nedtrekket. Det er en billigere vakt enn å rydde opp etterpå.
    Object.defineProperty(document, "cookie", { configurable: true,
      get: () => "__Host-disponit_csrf=tok123" });
    const h = nyHoved();
    visPolicyeditor(h, ctx(),
      { startPolicy: medGrenser({ valuta: ["NOK", "EUR"] }) });
    gaaTilFane(h, t("ui.editor.fane.handlinger"));
  await vent(() => h.querySelector(".editor-kort"));
    const rader = valutaRader(h);
    assert.equal(rader.length, 2, "hver valuta har ikke sin egen rad");
    assert.equal(rader[0].querySelector("select").value, "NOK");
    assert.equal(rader[1].querySelector("select").value, "EUR");
    for (const [i, egen, annen] of [[0, "NOK", "EUR"], [1, "EUR", "NOK"]]) {
      const valg = [...rader[i].querySelectorAll("option")].map((o) => o.value);
      assert.ok(valg.includes(egen), `rad ${i} mangler sin egen kode`);
      assert.equal(valg.includes(annen), false,
        `rad ${i} kan lage dubletten ${annen} som _valider_grenser vraker`);
    }
    const leggValg = [...leggTilValuta(h).querySelectorAll("option")]
      .map((o) => o.value);
    assert.equal(leggValg.includes("NOK") || leggValg.includes("EUR"), false,
      "legg-til-nedtrekket kan legge til en kode som alt står der");

    // Og radene er uavhengige: å bytte den ene rører ikke den andre.
    velgValuta(rader[0].querySelector("select"), "CHF");
    await vent(() => valutaRader(h)[0].querySelector("select").value === "CHF");
    POST = undefined;
    finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
    await vent(() => POST);
    assert.deepEqual(
      JSON.parse(POST.opts.body).innhold.handlinger[0].grenser.valuta,
      ["CHF", "EUR"], "et bytte på én rad tok en annen rads valuta med seg");
  });

test("Grenser: en valuta kan LEGGES TIL, ikke bare byttes", async () => {
  // Codex P2. Kontrollen var ett nedtrekk som byttet den første koden, så
  // `["NOK"]` + EUR ga alltid `["EUR"]` — halen var tom, og det fantes ingen
  // vei til `["NOK","EUR"]` i det hele tatt. Skjemaet og motoren støtter
  // bevisst en liste, og fritekstfeltet dette avløste kunne legge til; eier
  // kunne altså bare BEVARE flere valutaer som alt lå der, ikke lage dem.
  Object.defineProperty(document, "cookie", { configurable: true,
    get: () => "__Host-disponit_csrf=tok123" });
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: medGrenser({ valuta: ["NOK"] }) });
  gaaTilFane(h, t("ui.editor.fane.handlinger"));
  await vent(() => h.querySelector(".editor-kort"));
  assert.equal(valutaRader(h).length, 1);
  velgValuta(leggTilValuta(h), "EUR");
  await vent(() => valutaRader(h).length === 2);
  POST = undefined;
  finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POST);
  assert.deepEqual(
    JSON.parse(POST.opts.body).innhold.handlinger[0].grenser.valuta,
    ["NOK", "EUR"], "valutaen kunne ikke legges til, bare byttes");

  // Taket er serverens (`_valider_grenser`: 1–10), så det står i kontrollen
  // og oppdages ikke som en avvist lagring.
  for (const kode of ["USD", "SEK", "DKK", "GBP", "CHF", "JPY", "CAD", "ZAR"]) {
    velgValuta(leggTilValuta(h), kode);
    await vent(() => valutaRader(h).some(
      (r) => r.querySelector("select").value === kode));
  }
  assert.equal(valutaRader(h).length, 10);
  assert.equal(leggTilValuta(h), null,
    "en ellevte valuta kunne legges til — serveren vraker lista");
});

test("Grenser: en valutaliste ingen kontroll kan vise, repareres av EIER",
  async () => {
    // Samme regel som for tidsvinduet, på det andre feltet: en dublett som lå
    // der fra før, en bar streng eller en tom liste er former nedtrekket ikke
    // kan vise. Editoren normaliserte dem i det stille under tegning — altså
    // den samme gjettingen eierverdiktet slo ned på. Nå står valget hos eier,
    // og det som KAN berges vises før det skrives.
    const tilfeller = [
      [["EUR", "EUR"], ["EUR"]],
      ["NOK", ["NOK"]],
      [["NOK", "chf"], ["NOK"]],
      [[], null],
    ];
    Object.defineProperty(document, "cookie", { configurable: true,
      get: () => "__Host-disponit_csrf=tok123" });
    for (const [inn, berget] of tilfeller) {
      const h = nyHoved();
      visPolicyeditor(h, ctx(), { startPolicy: medGrenser({ valuta: inn }) });
      gaaTilFane(h, t("ui.editor.fane.handlinger"));
  await vent(() => h.querySelector(".editor-kort"));
      assert.ok(h.querySelector(".editor-reparasjon"),
        `${JSON.stringify(inn)} ble ikke vist som en liste som må repareres`);
      POST = undefined;
      finnKnapp(h, t("ui.editor.lagre"))
        .dispatchEvent(new window.Event("click"));
      await vent(() => h.querySelector(".editor-feil"));
      assert.equal(POST, undefined,
        `${JSON.stringify(inn)} ble lagret uten at eier valgte reparasjon`);
      assert.ok([...h.querySelectorAll(".editor-reparasjon p")]
        .some((p) => p.textContent.endsWith(`: ${JSON.stringify(inn)}`)),
        `${JSON.stringify(inn)} ble endret av å åpne og lagre`);

      // Er det koder å berge, skal «behold» vise NØYAKTIG hva den skriver.
      const behold = reparasjonsvalg(h, t("ui.editor.grense_behold"));
      if (berget === null) {
        assert.equal(behold, undefined,
          "en tom liste har ingenting å beholde, men ble tilbudt");
      } else {
        assert.equal(behold.textContent,
          `${t("ui.editor.grense_behold")}: ${berget.join(", ")}`);
      }
      (behold || reparasjonsvalg(h, t("ui.editor.grense_fjern")))
        .dispatchEvent(new window.Event("click"));
      POST = undefined;
      finnKnapp(h, t("ui.editor.lagre"))
        .dispatchEvent(new window.Event("click"));
      await vent(() => POST);
      const g = JSON.parse(POST.opts.body).innhold.handlinger[0].grenser;
      if (berget === null) {
        assert.equal("valuta" in g, false,
          `${JSON.stringify(inn)} ble stående i modellen`);
      } else {
        assert.deepEqual(g.valuta, berget,
          `${JSON.stringify(inn)}: eiers valg ble ikke lagret`);
      }
    }
  });

// --- Handlinger: ÉN om gangen, ikke alle stablet ---------------------------
// Eier: «action-fanen er fortsatt veldig lang … velge en og en». En bransjemal
// har gjerne sju handlinger med beløpsgrense, valutaliste og tidsvindu hver.

const TO_HANDLINGER = () => ({
  meta: { policy_id: "p-1", versjon: "0.1.0", bransjemal: "x",
    status: "utkast" },
  roller: [{ id: "agent" }],
  handlinger: [
    { id: "ordre.bekreft", modul: "M-25", modus: "auto",
      grenser: { belop_maks: "1000.00", valuta: ["NOK"] } },
    { id: "refusjon.utfor", modul: "M-41", modus: "alltid_stopp",
      grenser: { belop_maks: "500.00" } }],
});

test("Handlinger: bare ETT kort vises, velgeren bytter", async () => {
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: TO_HANDLINGER() });
  gaaTilFane(h, t("ui.editor.fane.handlinger"));
  await vent(() => h.querySelector(".handling-velger"));

  assert.equal(h.querySelectorAll(".editor-kort").length, 1,
    "alle handlingene sto stablet — det var nettopp den lange rullen");
  assert.ok(h.querySelector(".editor-kort").textContent
    .includes("ordre.bekreft"));
  assert.ok(h.textContent.includes(
    t("ui.editor.handling_posisjon").replace("{n}", "1").replace("{av}", "2")));

  const knapp = [...h.querySelectorAll(".handling-velger-knapp")]
    .find((b) => b.textContent.includes("refusjon.utfor"));
  assert.equal(knapp.getAttribute("aria-pressed"), "false");
  knapp.dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector(".editor-kort")
    .textContent.includes("refusjon.utfor"));
  assert.equal(h.querySelectorAll(".editor-kort").length, 1);
});

test("Handlinger: forrige/neste går sekvensielt og stopper i endene",
  async () => {
    const h = nyHoved();
    visPolicyeditor(h, ctx(), { startPolicy: TO_HANDLINGER() });
    gaaTilFane(h, t("ui.editor.fane.handlinger"));
    await vent(() => h.querySelector(".handling-velger"));
    const finn = (tekst) => [...h.querySelectorAll("button")]
      .find((b) => b.textContent.trim() === tekst);

    assert.ok(finn(t("ui.editor.handling_forrige")).disabled,
      "«forrige» skal være død på første handling");
    finn(t("ui.editor.handling_neste")).dispatchEvent(new window.Event("click"));
    await vent(() => h.querySelector(".editor-kort")
      .textContent.includes("refusjon.utfor"));
    assert.ok(finn(t("ui.editor.handling_neste")).disabled,
      "«neste» skal være død på siste handling");
    assert.ok(!finn(t("ui.editor.handling_forrige")).disabled);
  });

test("Handlinger: valget overlever re-rendringen feltene utløser", async () => {
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: TO_HANDLINGER() });
  gaaTilFane(h, t("ui.editor.fane.handlinger"));
  await vent(() => h.querySelector(".handling-velger"));
  [...h.querySelectorAll(".handling-velger-knapp")]
    .find((b) => b.textContent.includes("refusjon.utfor"))
    .dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector(".editor-kort")
    .textContent.includes("refusjon.utfor"));

  // Å velge en valuta tegner hele fanen på nytt. Uten husket valg hoppet
  // fanen tilbake til første handling — midt i redigeringen av den andre.
  const nedtrekk = [...h.querySelectorAll(".editor-kort select")].pop();
  nedtrekk.value = "EUR";
  nedtrekk.dispatchEvent(new window.Event("change"));
  await vent(() => h.querySelector(".editor-kort"));
  assert.ok(h.querySelector(".editor-kort").textContent
    .includes("refusjon.utfor"),
  "re-rendringen kastet eier tilbake til første handling");
});

test("Handlinger: velgeren viser modusen som faktisk vil bli lagret",
  async () => {
    // Velgerknappen bærer handlingens MODUS — det raskeste svaret på «hva gjør
    // denne». Muterte modusfeltet bare modellen, sto knappen igjen med den
    // gamle verdien til noe ANNET tilfeldigvis tegnet fanen på nytt, og
    // navigatoren motsa da verdien som ville blitt lagret.
    const h = nyHoved();
    visPolicyeditor(h, ctx(), { startPolicy: TO_HANDLINGER() });
    gaaTilFane(h, t("ui.editor.fane.handlinger"));
    await vent(() => h.querySelector(".handling-velger"));

    const knapp = () => [...h.querySelectorAll(".handling-velger-knapp")]
      .find((b) => b.textContent.includes("ordre.bekreft"));
    assert.ok(knapp().textContent.includes(t("modus.auto")));

    const modus = [...h.querySelectorAll(".editor-kort select")]
      .find((s) => [...s.options].some((o) => o.value === "alltid_stopp"));
    modus.value = "alltid_stopp";
    modus.dispatchEvent(new window.Event("change"));

    await vent(() => knapp()
      && knapp().textContent.includes(t("modus.alltid_stopp")));
    assert.ok(knapp().textContent.includes(t("modus.alltid_stopp")),
      "velgeren reklamerte fortsatt for den gamle modusen");
    // Og valget står: modusendringen skal ikke kaste eier til første handling
    // eller bytte hvilket kort som vises.
    assert.ok(h.querySelector(".editor-kort").textContent
      .includes("ordre.bekreft"));
  });


// --- 047: menneskelig overstyring og vilkår (portene 27–33, 41) ------------

test("Overstyring: fravær er en TILSTAND, par legges til fra nedtrekk",
  async () => {
    const h = nyHoved();
    visPolicyeditor(h, ctx(), {
      startPolicy: JSON.parse(JSON.stringify(MAL)) });
    await vent(() => h.querySelector(".editor-seksjon"));
    gaaTilFane(h, t("ui.editor.fane.overstyring"));
    await vent(() => h.textContent.includes(t("ui.editor.overstyring")));
    // Port 27: fraværet vises som tilstand — «ingen (standard)» —
    // aldri som tomhet.
    assert.ok(h.textContent.includes(t("ui.editor.overstyring_ingen")));
    // Grunnkodene og handlingene er NEDTREKK (port 29/30): bare motorens
    // godkjennbare koder og policyens egne handlinger kan velges.
    await vent(() => h.querySelector("#mo-grunnkode"));
    const gk = h.querySelector("#mo-grunnkode");
    const koder = [...gk.querySelectorAll("option")].map((o) => o.value);
    // `frekvensgrense_naadd` står i serverens navneliste, men uten et krav —
    // motoren kan ikke løfte den. Da tilbys den ikke: en oppføring for den
    // ville endt i STOPP ved HVER godkjenning.
    assert.deepEqual(koder, ["belop_over_grense", "valuta_ikke_tillatt"]);
    assert.ok(!koder.includes("teknisk_feil"),
      "en ikke-godkjennbar grunnkode er ikke velgbar");
    const hv = h.querySelector("#mo-handling");
    assert.deepEqual([...hv.querySelectorAll("option")].map((o) => o.value),
      ["ordre.bekreft"]);
    // Paret alene er ikke en overstyring: uten verdien motoren skal løfte
    // TIL avvises det her og nå, og ingenting legges til.
    finnKnapp(h, t("ui.editor.overstyring_legg_til"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => h.textContent.includes(
      t("ui.editor.overstyring_valuta_feil")));
    assert.equal(h.querySelector(".overstyring-liste li"), null,
      "en oppføring motoren ikke kan anvende skal ikke kunne legges til");
    // Med beløpstak og valuta går den gjennom — og verdien vises i raden.
    const belop = h.querySelector("#mo-belop");
    const valuta = h.querySelector("#mo-valuta");
    valuta.value = "nok";
    valuta.dispatchEvent(new window.Event("input"));
    belop.value = "50000.00";
    belop.dispatchEvent(new window.Event("input"));
    finnKnapp(h, t("ui.editor.overstyring_legg_til"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => h.querySelector(".overstyring-liste li"));
    assert.ok(h.textContent.includes(t("ui.editor.overstyring_par")
      .replace("{grunnkode}", "belop_over_grense")
      .replace("{handling}", "ordre.bekreft")));
    assert.ok(h.textContent.includes("50000.00 NOK"),
      "verdien motoren løfter til vises i raden");
    assert.ok(!h.textContent.includes(t("ui.editor.overstyring_ingen")));
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
  });

// Codex P2: en velger som viser noe ANNET enn det som er lagret.
//
// `krever_rolle` kan peke på en rolle som er fjernet — utkast lagres uten
// skjemavalidering. Sto verdien ikke blant valgene, merket ingen <option> seg
// som valgt, og nettleseren viste da den FØRSTE gyldige rollen mens modellen
// fortsatt bar den ugyldige. Ingen `change` fyrer av seg selv, og med bare ETT
// gyldig valg finnes det ikke engang et annet valg å ta for å utløse en: eier
// så «agent», lagret «borte», og fikk samme valideringsfeil om igjen — eneste
// vei ut var å slette hele overstyringen.
test("Overstyring: en ukjent krever_rolle VISES, og lagres ikke i skjul",
  async () => {
    const cookieDesc = Object.getOwnPropertyDescriptor(
      window.Document.prototype, "cookie");
    Object.defineProperty(document, "cookie", { configurable: true,
      get: () => "__Host-disponit_csrf=tok123" });
    POST = undefined;
    const h = nyHoved();
    visPolicyeditor(h, ctx(), { aapneUtkast: () => {}, startPolicy: {
      meta: { policy_id: "p-mo", versjon: "0.1.0", bransjemal: "x",
              status: "utkast" },
      roller: [{ id: "agent" }],
      handlinger: [{ id: "faktura.bokfor", tillatt_for: ["agent"] }],
      menneskelig_overstyring: { krever_rolle: "borte", godkjennbare: [] },
    } });
    await vent(() => h.querySelector(".editor-seksjon"));
    gaaTilFane(h, t("ui.editor.fane.overstyring"));
    await vent(() => h.textContent.includes(t("ui.editor.overstyring_rolle")));
    const merket = [...h.querySelectorAll("select")]
      .find((s) => [...s.options].some((o) => o.value === "borte"));
    assert.ok(merket, "den ukjente rollen er ikke å se i velgeren");
    assert.equal(merket.value, "borte",
      "velgeren viste en annen rolle enn den som er lagret");
    assert.ok(merket.textContent.includes(
      t("ui.editor.verdi_ukjent").replace("{verdi}", "borte")),
    "den ukjente verdien er ikke merket som ukjent");
    // Ingenting skrives om av at noe TEGNES: modellen bærer fortsatt eiers
    // egen verdi til eier selv retter den.
    finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
    await vent(() => POST);
    assert.equal(
      JSON.parse(POST.opts.body).innhold.menneskelig_overstyring.krever_rolle,
      "borte", "editoren viste én rolle og lagret en annen");
    // …og reparasjonen er ETT valg unna, selv om det bare finnes én gyldig
    // rolle: den ukjente oppføringen er den andre.
    POST = undefined;
    merket.value = "agent";
    merket.dispatchEvent(new window.Event("change"));
    finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
    await vent(() => POST);
    assert.equal(
      JSON.parse(POST.opts.body).innhold.menneskelig_overstyring.krever_rolle,
      "agent");
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
    if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
  });

// Codex P2: skriveveien tar med vilje imot ustrukturerte utkast — porten står
// i `valider_utkast`, ikke i lagringen. Et lagret utkast kan derfor bære
// `roller: {}` eller `handlinger: {}`. Leste overstyringsfanen dem rått med
// `.map`, kastet den TypeError på nettopp den fanen eier måtte åpne for å se
// hva som var galt, og skjemafeilen kunne bare forkastes.
test("Overstyring: en ustrukturert roller/handlinger-samling krasjer ikke",
  async () => {
    for (const vrang of [{}, "roller", 7, [null, { id: 5 }, { id: "ok" }]]) {
      const start = JSON.parse(JSON.stringify(MAL));
      start.roller = vrang;
      start.handlinger = vrang;
      start.menneskelig_overstyring = { krever_rolle: "agent",
        godkjennbare: [] };
      const h = nyHoved();
      visPolicyeditor(h, ctx(), { startPolicy: start });
      await vent(() => h.querySelector(".editor-seksjon"));
      gaaTilFane(h, t("ui.editor.fane.overstyring"));
      await vent(() => h.textContent.includes(t("ui.editor.overstyring")));
      // Fanen tegner seg, og nedtrekkene bærer bare de LESBARE id-ene.
      await vent(() => h.querySelector("#mo-handling"));
      const valg = [...h.querySelectorAll("#mo-handling option")]
        .map((o) => o.value);
      assert.deepEqual(valg, Array.isArray(vrang) ? ["ok"] : [],
        JSON.stringify(vrang));
    }
  });

// `_krev_malautorisasjonsvilkar` stiller kravet KUN for en handling hvis
// kodefestede type krever målautorisasjon, og bare for typens eget domene.
// Låsen er en påstand om at serveren nekter fjerningen, så testene under
// måler den der kravet faktisk finnes — `kontroll.wcag.`-prefikset. Motprøven
// («…på en handling kravet ikke gjelder…») bruker malens egen `ordre.bekreft`.
function medKravhandling(start) {
  start.handlinger[0].id = "kontroll.wcag.nettsted";
  return start;
}

test("Vilkår: plattformvilkåret er LÅST med aria-disabled og forklaring",
  async () => {
    const start = medKravhandling(JSON.parse(JSON.stringify(MAL)));
    start.verifikatorer = { v_domenekontroll: {
      beskrivelse: "Plattformens domenekontroll",
      betrodd_for: ["domenekontroll_verifisert", "eget_vilkar"] } };
    start.handlinger[0].vilkaar = [
      { navn: "domenekontroll_verifisert", verifikator: "v_domenekontroll" },
      { navn: "eget_vilkar", verifikator: "v_domenekontroll" },
    ];
    const h = nyHoved();
    visPolicyeditor(h, ctx(), { startPolicy: start });
    await vent(() => h.querySelector(".editor-seksjon"));
    gaaTilFane(h, t("ui.editor.fane.handlinger"));
    // Grunnlaget hentes asynkront; det som skal måles er tilstanden ETTER
    // at det er inne (uten det er alt låst — egen test under).
    await vent(() => h.textContent.includes(
      t("ui.editor.vilkaar_plattform_forklaring")));
    const rader = [...h.querySelectorAll(".vilkaar-rad")];
    assert.equal(rader.length, 2);
    // Port 41: låst rad har aria-disabled OG aria-describedby → forklaring.
    const laast = rader[0].querySelector('[aria-disabled="true"]');
    assert.ok(laast, "plattformvilkåret er låst");
    const beskrivelse = document.getElementById(
      laast.getAttribute("aria-describedby"));
    assert.ok(beskrivelse.textContent.includes(
      t("ui.editor.vilkaar_plattform_forklaring")));
    assert.ok(!rader[0].querySelector("button"),
      "ingen fjern-knapp på plattformvilkåret (port 31)");
    // Policyens EGET vilkår kan fjernes; nytt velges fra verifikatorens
    // betrodd_for (port 33).
    assert.ok(rader[1].querySelector("button"));
    const navnValg = h.querySelector('[id^="vk-navn-"]');
    assert.deepEqual(
      [...navnValg.querySelectorAll("option")].map((o) => o.value),
      ["domenekontroll_verifisert", "eget_vilkar"]);
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
  });

// Codex P2: låsen verner KRAVET, ikke raden. `_krev_malautorisasjonsvilkar`
// er et `SELECT 1`: den spør om handlingen HAR et målautorisasjonsvilkår for
// domenet sitt. Bærer den to, godtar serveren gjerne at det ene fjernes —
// men låste flaten begge, kunne en dublett eller et foreldet navn aldri
// ryddes, og eier møtte et nei porten aldri sa.
test("Vilkår: overflødige plattformvilkår kan fjernes, det siste ikke",
  async () => {
    // To ULIKE registrerte navn for samme domene, og en dublett i tillegg:
    // tre rader som hver for seg dekker kravet.
    for (const [andre, merkelapp] of [
      [{ navn: "domene_eier_bekreftet", verifikator: "v_domenekontroll" },
       "to ulike navn"],
      [{ navn: "domenekontroll_verifisert", verifikator: "v_annen" },
       "dublett"],
    ]) {
      const start = medKravhandling(JSON.parse(JSON.stringify(MAL)));
      start.verifikatorer = {
        v_domenekontroll: { beskrivelse: "Plattformens domenekontroll",
          betrodd_for: ["domenekontroll_verifisert",
            "domene_eier_bekreftet"] },
        v_annen: { beskrivelse: "En annen",
          betrodd_for: ["domenekontroll_verifisert"] },
      };
      start.handlinger[0].vilkaar = [
        { navn: "domenekontroll_verifisert",
          verifikator: "v_domenekontroll" },
        andre,
      ];
      const h = nyHoved();
      visPolicyeditor(h, ctx(), { startPolicy: start });
      await vent(() => h.querySelector(".editor-seksjon"));
      gaaTilFane(h, t("ui.editor.fane.handlinger"));
      await vent(() => h.textContent.includes(
        t("ui.editor.vilkaar_plattform_forklaring")));
      let rader = [...h.querySelectorAll(".vilkaar-rad")];
      assert.equal(rader.length, 2, merkelapp);
      // INGEN av dem er låst: kravet står igjen uansett hvilken som går.
      // Og de er fjern-knapper, ikke reparasjonsnedtrekk — radene feiler
      // ingenting, de er bare overflødige.
      for (const rad of rader) {
        assert.ok(!rad.querySelector('[aria-disabled="true"]'),
          `${merkelapp}: en overflødig rad var låst`);
        assert.ok(!rad.querySelector("select"),
          `${merkelapp}: en velformet rad ble tilbudt reparasjon`);
        assert.equal(rad.querySelector("button").textContent,
          t("ui.editor.vilkaar_fjern"), merkelapp);
      }
      // Fjern den ene — den som blir igjen bærer kravet alene, og låses.
      rader[1].querySelector("button").click();
      await vent(() => h.querySelectorAll(".vilkaar-rad").length === 1);
      rader = [...h.querySelectorAll(".vilkaar-rad")];
      assert.ok(rader[0].querySelector('[aria-disabled="true"]'),
        `${merkelapp}: den siste raden mistet låsen`);
      assert.ok(!rader[0].querySelector("button"),
        `${merkelapp}: den siste raden kunne fjernes`);
      assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
    }
  });

// Codex P2: låsen gjelder IDENTITETEN (navnet + verifikatoren), ikke
// terskelen. `min` er eierens egen, og den avgjør om en attestasjon godtas
// — en foreldet terskel er derfor en levende feil hun må kunne rette.
// Uten dette viste den låste raden verken verdien eller en vei til å endre
// den, og raden kunne ikke fjernes og legges inn på nytt: eneste utvei var
// å forkaste utkastet, for en endring serveren gjerne ville tatt imot.
test("Vilkår: terskelen på et låst plattformvilkår kan rettes",
  async () => {
    const start = medKravhandling(JSON.parse(JSON.stringify(MAL)));
    start.verifikatorer = { v_domenekontroll: {
      beskrivelse: "Plattformens domenekontroll",
      betrodd_for: ["domenekontroll_verifisert"] } };
    start.handlinger[0].vilkaar = [
      { navn: "domenekontroll_verifisert", verifikator: "v_domenekontroll",
        min: 2 },
    ];
    const h = nyHoved();
    visPolicyeditor(h, ctx(), { startPolicy: start });
    await vent(() => h.querySelector(".editor-seksjon"));
    gaaTilFane(h, t("ui.editor.fane.handlinger"));
    await vent(() => h.textContent.includes(
      t("ui.editor.vilkaar_plattform_forklaring")));
    const rad = h.querySelector(".vilkaar-rad");
    assert.ok(rad.querySelector('[aria-disabled="true"]'),
      "identiteten er fortsatt låst");
    const felt = rad.querySelector("input");
    assert.ok(felt, "terskelen har ikke noe felt");
    // Verdien VISES — den var usynlig før.
    assert.equal(felt.value, "2");
    // …og endringen lever i editorens egen tilstand: målt ved å tegne
    // fanen på nytt, ikke ved å lese objektet vi sendte inn.
    const lesTerskel = () => {
      gaaTilFane(h, t("ui.editor.fane.roller"));
      gaaTilFane(h, t("ui.editor.fane.handlinger"));
      return h.querySelector(".vilkaar-rad input");
    };
    felt.value = "5";
    felt.dispatchEvent(new window.Event("input"));
    assert.equal(lesTerskel().value, "5");
    // Tom = ingen terskel, ikke en ugyldig verdi — og raden er FORTSATT
    // låst, altså fortsatt velformet.
    const felt2 = h.querySelector(".vilkaar-rad input");
    felt2.value = "";
    felt2.dispatchEvent(new window.Event("input"));
    assert.equal(lesTerskel().value, "");
    assert.ok(h.querySelector('.vilkaar-rad [aria-disabled="true"]'),
      "raden mistet låsen da terskelen ble fjernet");
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
  });

// Codex P2: skriveveien tar med vilje imot ustrukturerte utkast — porten
// står i `valider_utkast`, ikke i lagringen. Et lagret utkast kan derfor
// bære `vilkaar: [null]` eller en naken streng. Leste editoren `v.navn`
// rått, kastet den TypeError på nettopp det utkastet eier måtte åpne for å
// RETTE feilen, og flaten låste seg på det eneste stedet den kunne fjernes.
test("Vilkår: en ulesbar oppføring kan åpnes og fjernes, ikke krasje",
  async () => {
    const start = JSON.parse(JSON.stringify(MAL));
    start.verifikatorer = { v: { betrodd_for: ["eget_vilkar"] } };
    start.handlinger[0].vilkaar = [
      null,
      "eget_vilkar",                                   // naken streng
      { navn: "eget_vilkar", verifikator: "v" },
    ];
    const h = nyHoved();
    visPolicyeditor(h, ctx(), { startPolicy: start });
    await vent(() => h.querySelector(".editor-seksjon"));
    gaaTilFane(h, t("ui.editor.fane.handlinger"));
    await vent(() => h.textContent.includes(
      t("ui.editor.vilkaar_plattform_forklaring")));
    const rader = [...h.querySelectorAll(".vilkaar-rad")];
    assert.equal(rader.length, 3, "alle tre oppføringene tegnes");
    // Den ulesbare sier hva den er, i stedet for å bli et «?».
    assert.ok(rader[0].textContent.includes(
      t("ui.editor.vilkaar_ulesbart")), rader[0].textContent);
    // …og den kan fjernes, ellers er utkastet en blindgate.
    const fjern = rader[0].querySelector("button");
    assert.ok(fjern, "den ulesbare oppføringen må kunne fjernes");
    fjern.dispatchEvent(new window.Event("click"));
    await vent(() => h.querySelectorAll(".vilkaar-rad").length === 2);
    assert.equal(h.querySelectorAll(".vilkaar-rad").length, 2,
      "den ulesbare oppføringen ble ikke fjernet");
    assert.ok(!h.textContent.includes(t("ui.editor.vilkaar_ulesbart")));
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
  });

// Codex P2: låsen målte NAVNET alene. Et utkast med et registrert
// plattformnavn, men uten en betrodd verifikator, ble derfor låst — mens
// valideringen avviste nettopp den verifikatorpekeren. Raden kunne verken
// rettes eller fjernes, og utkastet kunne bare forkastes.
test("Vilkår: et plattformvilkår uten betrodd verifikator kan repareres",
  async () => {
    const start = medKravhandling(JSON.parse(JSON.stringify(MAL)));
    start.verifikatorer = { v_domenekontroll: {
      beskrivelse: "Plattformens domenekontroll",
      betrodd_for: ["domenekontroll_verifisert"] } };
    start.handlinger[0].vilkaar = [
      { navn: "domenekontroll_verifisert" },            // verifikator mangler
      { navn: "domenekontroll_verifisert", verifikator: 7 },  // ikke en streng
    ];
    const h = nyHoved();
    visPolicyeditor(h, ctx(), { startPolicy: start });
    await vent(() => h.querySelector(".editor-seksjon"));
    gaaTilFane(h, t("ui.editor.fane.handlinger"));
    await vent(() => h.textContent.includes(
      t("ui.editor.vilkaar_plattform_forklaring")));
    const rader = [...h.querySelectorAll(".vilkaar-rad")];
    assert.equal(rader.length, 2);
    for (const rad of rader) {
      assert.ok(!rad.querySelector('[aria-disabled="true"]'),
        "en ufullstendig oppføring er ikke et håndhevet plattformvilkår");
    }
    // Reparasjonen tilbyr KUN verifikatorer som er betrodd for navnet.
    const velg = rader[0].querySelector("select");
    assert.ok(velg, "raden må kunne repareres");
    assert.deepEqual([...velg.querySelectorAll("option")].map((o) => o.value),
      ["v_domenekontroll"]);
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
    rader[0].querySelector("button").dispatchEvent(new window.Event("click"));
    await vent(() => h.querySelector('.vilkaar-rad [aria-disabled="true"]'));
    // Navnet registeret krever overlevde reparasjonen — raden bærer nå
    // navnet OG verifikatoren, og er det låste plattformvilkåret den skal
    // være.
    const reparert = h.querySelectorAll(".vilkaar-rad")[0];
    assert.ok(reparert.textContent.includes(
      "domenekontroll_verifisert — v_domenekontroll"), reparert.textContent);
    assert.ok(reparert.querySelector('[aria-disabled="true"]'));
    // Den andre raden er urørt og fortsatt reparerbar.
    assert.ok(h.querySelectorAll(".vilkaar-rad")[1].querySelector("select"));
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
  });

// Er ingen av policyens verifikatorer betrodd for navnet, finnes det ingen
// gyldig form av raden i det hele tatt — da er fjerning den eneste veien ut,
// og en lås ville gjort utkastet til en blindgate.
test("Vilkår: et plattformnavn ingen verifikator kan bære, kan fjernes",
  async () => {
    const start = medKravhandling(JSON.parse(JSON.stringify(MAL)));
    start.verifikatorer = { v_annet: { betrodd_for: ["eget_vilkar"] } };
    start.handlinger[0].vilkaar = [{ navn: "domenekontroll_verifisert" }];
    const h = nyHoved();
    visPolicyeditor(h, ctx(), { startPolicy: start });
    await vent(() => h.querySelector(".editor-seksjon"));
    gaaTilFane(h, t("ui.editor.fane.handlinger"));
    await vent(() => h.textContent.includes(
      t("ui.editor.vilkaar_plattform_forklaring")));
    const rad = h.querySelector(".vilkaar-rad");
    assert.ok(!rad.querySelector('[aria-disabled="true"]'));
    assert.ok(!rad.querySelector("select"), "ingen betrodd verifikator å velge");
    rad.querySelector("button").dispatchEvent(new window.Event("click"));
    await vent(() => !h.querySelector(".vilkaar-rad"));
    assert.equal(h.querySelectorAll(".vilkaar-rad").length, 0);
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
  });

// Codex P2: låsen målte navn + verifikatortillit (§5), men skjemaet krever
// også at `min` er numerisk og avviser ekstra felter. En rad med
// `min: "ugyldig"` var derfor «velformet» for låsen og ble låst, mens
// serveren avviste den — samme blindgate som navnelåsen ga, én etasje ned.
test("Vilkår: en rad skjemaet avviser på strukturen låses ikke", async () => {
  const start = medKravhandling(JSON.parse(JSON.stringify(MAL)));
  start.verifikatorer = { v_domenekontroll: {
    beskrivelse: "Plattformens domenekontroll",
    betrodd_for: ["domenekontroll_verifisert"] } };
  start.handlinger[0].vilkaar = [
    // Betrodd verifikator, men `min` er ikke et tall (skjemaet: number).
    { navn: "domenekontroll_verifisert", verifikator: "v_domenekontroll",
      min: "ugyldig" },
    // Betrodd verifikator, men et felt skjemaet ikke kjenner
    // (additionalProperties: false).
    { navn: "domenekontroll_verifisert", verifikator: "v_domenekontroll",
      tull: 1 },
  ];
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: start });
  await vent(() => h.querySelector(".editor-seksjon"));
  gaaTilFane(h, t("ui.editor.fane.handlinger"));
  await vent(() => h.textContent.includes(
    t("ui.editor.vilkaar_plattform_forklaring")));
  const rader = [...h.querySelectorAll(".vilkaar-rad")];
  assert.equal(rader.length, 2);
  for (const rad of rader) {
    assert.ok(!rad.querySelector('[aria-disabled="true"]'),
      "en rad skjemaet avviser er ikke et håndhevet plattformvilkår");
    assert.ok(rad.querySelector("select"), "raden må kunne repareres");
  }
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
  // Reparasjonen rydder bort nettopp det skjemaet avviste, og raden blir
  // det låste plattformvilkåret den skulle vært. Låsen ER påstanden om at
  // raden nå er velformet i BEGGE lag — den kan ikke bli sann med
  // `min: "ugyldig"` i behold.
  rader[0].querySelector("button").dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector('.vilkaar-rad [aria-disabled="true"]'));
  const reparert = h.querySelectorAll(".vilkaar-rad")[0];
  assert.ok(reparert.querySelector('[aria-disabled="true"]'));
  assert.ok(!reparert.querySelector("button"));
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

// En GYLDIG `min` er eierens egen terskel, ikke feilen som ble reparert —
// den skal ikke forsvinne fordi verifikatorpekeren måtte rettes.
test("Vilkår: en velformet rad med numerisk min er låst, og min overlever"
  + " en reparasjon", async () => {
    const cookieDesc = Object.getOwnPropertyDescriptor(
      window.Document.prototype, "cookie");
    Object.defineProperty(document, "cookie", { configurable: true,
      get: () => "__Host-disponit_csrf=tok123" });
    const start = medKravhandling(JSON.parse(JSON.stringify(MAL)));
    start.verifikatorer = { v_domenekontroll: {
      betrodd_for: ["domenekontroll_verifisert"] } };
    start.handlinger[0].vilkaar = [
      // Velformet: `min` er et tall → låst som ethvert plattformvilkår.
      { navn: "domenekontroll_verifisert", verifikator: "v_domenekontroll",
        min: 2 },
      // Ubetrodd verifikator, men gyldig `min` — reparasjonen retter
      // pekeren og BEHOLDER terskelen.
      { navn: "domenekontroll_verifisert", verifikator: "v_ukjent", min: 3 },
    ];
    const h = nyHoved();
    visPolicyeditor(h, ctx(), { startPolicy: start });
    await vent(() => h.querySelector(".editor-seksjon"));
    gaaTilFane(h, t("ui.editor.fane.handlinger"));
    await vent(() => h.textContent.includes(
      t("ui.editor.vilkaar_plattform_forklaring")));
    const rader = [...h.querySelectorAll(".vilkaar-rad")];
    assert.equal(rader.length, 2);
    assert.ok(rader[0].querySelector('[aria-disabled="true"]'),
      "numerisk min er velformet og skal fortsatt låses");
    assert.ok(!rader[0].querySelector("button"));
    rader[1].querySelector("button").dispatchEvent(new window.Event("click"));
    await vent(() => !h.querySelectorAll(".vilkaar-rad")[1]
      .querySelector("select"));
    // Editoren dyp-kopierer `startPolicy`, så terskelen leses av det som
    // faktisk sendes til serveren — ikke av originalobjektet.
    POST = undefined;
    finnKnapp(h, t("ui.editor.lagre")).dispatchEvent(new window.Event("click"));
    await vent(() => POST);
    assert.deepEqual(
      JSON.parse(POST.opts.body).innhold.handlinger[0].vilkaar,
      [{ navn: "domenekontroll_verifisert", verifikator: "v_domenekontroll",
         min: 2 },
       { navn: "domenekontroll_verifisert", verifikator: "v_domenekontroll",
         min: 3 }],
      "reparasjonen kastet eierens egen terskel");
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
    if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
  });

// Codex P2: låsen målte et GLOBALT sett av vilkårsnavn. Registeret er
// plattform-globalt, men kravet er ikke: `_krev_malautorisasjonsvilkar`
// stiller det bare for handlinger hvis kodefestede type krever
// målautorisasjon, og bare for typens eget domene. Et velformet
// `domenekontroll_verifisert` som havnet på en vanlig `ordre.bekreft` ble
// derfor umulig å fjerne — enda serveren gjerne ville sluppet fjerningen.
test("Vilkår: et plattformvilkår på en handling uten kravet kan fjernes",
  async () => {
    const start = JSON.parse(JSON.stringify(MAL));   // `ordre.bekreft`
    start.verifikatorer = { v_domenekontroll: {
      betrodd_for: ["domenekontroll_verifisert"] } };
    start.handlinger[0].vilkaar = [
      { navn: "domenekontroll_verifisert", verifikator: "v_domenekontroll" },
    ];
    const h = nyHoved();
    visPolicyeditor(h, ctx(), { startPolicy: start });
    await vent(() => h.querySelector(".editor-seksjon"));
    gaaTilFane(h, t("ui.editor.fane.handlinger"));
    await vent(() => h.textContent.includes(
      t("ui.editor.vilkaar_plattform_forklaring")));
    const rad = h.querySelector(".vilkaar-rad");
    assert.ok(!rad.querySelector('[aria-disabled="true"]'),
      "kravet gjelder ikke denne handlingen, så raden er ikke låst");
    rad.querySelector("button").dispatchEvent(new window.Event("click"));
    await vent(() => !h.querySelector(".vilkaar-rad"));
    assert.equal(h.querySelectorAll(".vilkaar-rad").length, 0);
    assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
  });

// …og motsatt vei: på en handling kravet GJELDER for, låses bare vilkåret
// for handlingens EGET domene. Et registrert plattformvilkår for et annet
// domene kan aldri tilfredsstille kravet, og skal derfor kunne fjernes.
test("Vilkår: bare vilkåret for handlingens eget domene låses", async () => {
  const start = medKravhandling(JSON.parse(JSON.stringify(MAL)));
  start.verifikatorer = { v_dk: {
    betrodd_for: ["domenekontroll_verifisert", "annet_domenevilkar"] } };
  start.handlinger[0].vilkaar = [
    { navn: "domenekontroll_verifisert", verifikator: "v_dk" },
    { navn: "annet_domenevilkar", verifikator: "v_dk" },
  ];
  const h = nyHoved();
  visPolicyeditor(h, ctx(), { startPolicy: start });
  await vent(() => h.querySelector(".editor-seksjon"));
  gaaTilFane(h, t("ui.editor.fane.handlinger"));
  await vent(() => h.textContent.includes(
    t("ui.editor.vilkaar_plattform_forklaring")));
  const rader = [...h.querySelectorAll(".vilkaar-rad")];
  assert.equal(rader.length, 2);
  assert.ok(rader[0].querySelector('[aria-disabled="true"]'),
    "handlingens eget domene er kravet, og låses");
  assert.ok(!rader[1].querySelector('[aria-disabled="true"]'),
    "et annet domene kan aldri telle for denne handlingen");
  assert.ok(rader[1].querySelector("button"), "…og må kunne fjernes");
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});

// Fail-closed gjelder også en HALV grunnlagsrespons: uten kravlisten vet
// flaten ikke hvilke handlinger kravet gjelder for, og kan ikke tilby en
// fjerning serveren kanskje nekter.
test("Vilkår: uten kravlisten i grunnlaget er ALT låst", async () => {
  const gammelFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    const sti = url.split("?")[0];
    if (sti === "/v1/policyadmin/editorgrunnlag") {
      const svar = await gammelFetch(url, opts);
      const d = await svar.json();
      delete d.malautorisasjonskrav;
      return { ok: true, status: 200, json: async () => d };
    }
    return gammelFetch(url, opts);
  };
  try {
    const start = JSON.parse(JSON.stringify(MAL));
    start.verifikatorer = { v: { betrodd_for: ["eget_vilkar"] } };
    start.handlinger[0].vilkaar = [{ navn: "eget_vilkar", verifikator: "v" }];
    const h = nyHoved();
    visPolicyeditor(h, ctx(), { startPolicy: start });
    await vent(() => h.querySelector(".editor-seksjon"));
    gaaTilFane(h, t("ui.editor.fane.handlinger"));
    await vent(() => h.querySelector(".vilkaar-liste"));
    assert.ok(h.textContent.includes(t("ui.editor.vilkaar_grunnlag_mangler")));
    assert.ok(!h.querySelector(".vilkaar-rad button"),
      "ingen fjern-knapp uten kravlisten");
  } finally {
    globalThis.fetch = gammelFetch;
  }
});

test("Vilkår: uten editorgrunnlag er ALT låst (fail-closed)", async () => {
  const gammelFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    const sti = url.split("?")[0];
    if (sti === "/v1/policyadmin/editorgrunnlag") {
      return { ok: false, status: 500, json: async () => ({ feil: "x" }) };
    }
    return gammelFetch(url, opts);
  };
  try {
    const start = JSON.parse(JSON.stringify(MAL));
    start.verifikatorer = { v: { betrodd_for: ["eget_vilkar"] } };
    start.handlinger[0].vilkaar = [{ navn: "eget_vilkar",
      verifikator: "v" }];
    const h = nyHoved();
    visPolicyeditor(h, ctx(), { startPolicy: start });
    await vent(() => h.querySelector(".editor-seksjon"));
    gaaTilFane(h, t("ui.editor.fane.handlinger"));
    await vent(() => h.querySelector(".vilkaar-liste"));
    // Port 31 er en LUKKING: kan ikke flaten vite hva som er plattformens,
    // kan den ikke tilby fjerning av noe som helst.
    assert.ok(h.textContent.includes(
      t("ui.editor.vilkaar_grunnlag_mangler")));
    assert.ok(!h.querySelector(".vilkaar-rad button"),
      "ingen fjern-knapp uten grunnlag");
  } finally {
    globalThis.fetch = gammelFetch;
  }
});
