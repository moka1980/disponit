// Registreringsflaten (192) mot mocket API.
//
// Portene måler de tre tingene som avgjør om eiers mål er nådd: at skjemaet
// er kort, at feilene sies ved feltet og ikke bare på toppen, og — den
// viktigste — at hun får VITE at hun må logge inn på nytt.
//
// Uten den siste beskjeden ville hun møtt en 401 rett etter å ha gjort alt
// riktig: registrantraden slettes i samme transaksjon som firmaet opprettes,
// men sesjonen hennes peker fortsatt på registreringskonteksten.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visFirmaregistrering, orgnummerFeil, idempotensnokkelFor,
         BRANSJER } from "../static/js/flater/firmaregistrering.js";

settI18nForTest(NB, "nb");

let SVAR;
const KALL = [];
globalThis.fetch = async (url, opts = {}) => {
  KALL.push({ url, metode: opts.method || "GET",
    idem: (opts.headers || {})["Idempotency-Key"],
    kropp: opts.body ? JSON.parse(opts.body) : null });
  const oppf = SVAR[url.split("?")[0]];
  if (typeof oppf === "number") {
    return { ok: false, status: oppf,
      json: async () => ({ feil: SVAR._feilkode || "x" }) };
  }
  if (!oppf) {
    return { ok: false, status: 404, json: async () => ({ feil: "ikke_funnet" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx() {
  const c = { sprak: "nb", scopes: ["firma:opprett"], tenant: "_registrering",
    _ua: false };
  c.paaUautorisert = () => { c._ua = true; };
  return c;
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
  KALL.length = 0;
  return m;
}

function fyll(h, { navn = "Fjordlys Elektro AS", orgnr = "923609016",
                   bransje = "tjenestebedrift" } = {}) {
  h.querySelector("#firmareg-navn").value = navn;
  h.querySelector("#firmareg-orgnr").value = orgnr;
  h.querySelector("#firmareg-bransje").value = bransje;
  h.querySelector("form").dispatchEvent(
    new window.Event("submit", { cancelable: true, bubbles: true }));
}

test("Registrering: skjemaet er kort, merket og axe-rent", async () => {
  SVAR = {};
  const h = nyHoved();
  visFirmaregistrering(h, ctx());

  // TRE FELTER, ikke flere. Et registreringsskjema som spør om alt, er
  // grunnen til at folk ikke fullfører det.
  const felter = h.querySelectorAll("input, select");
  assert.equal(felter.length, 3, "skjemaet har vokst forbi tre felter");
  for (const f of felter) {
    assert.ok(h.querySelector(`label[for="${f.id}"]`),
      `feltet ${f.id} mangler en label`);
  }
  // Bransjen er et NEDTREKK med nøyaktig de tre malene som finnes.
  const valg = [...h.querySelectorAll("#firmareg-bransje option")]
    .map((o) => o.value);
  assert.deepEqual(valg, BRANSJER);
  // Og hva valget BETYR står der, ikke bare hva det heter.
  assert.ok(h.textContent.includes(t("ui.firmareg.bransje_hjelp")));

  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Registrering: hele veien, og hun får VITE at hun må logge inn på nytt",
     async () => {
  SVAR = { "/v1/firma/registrer": { tenant: "fjordlys-elektro-as",
    navn: "Fjordlys Elektro AS", prove_utloper: "2026-10-12",
    bransje: "tjenestebedrift" } };
  const h = nyHoved();
  visFirmaregistrering(h, ctx());
  fyll(h);
  await vent(() => h.textContent.includes(t("ui.firmareg.ferdig_tittel")));

  // DEN AVGJØRENDE BESKJEDEN. Uten den møter hun en 401 hun ikke forstår.
  assert.ok(h.textContent.includes(t("ui.firmareg.ferdig_undertittel")));
  const lenke = h.querySelector("a.knapp");
  assert.ok(lenke, "ingen vei videre til innlogging");
  assert.equal(lenke.getAttribute("href"), "/");
  // Kvitteringen sier BÅDE navnet og fristen — ikke bare «ok».
  assert.ok(h.textContent.includes("Fjordlys Elektro AS"));
  assert.ok(h.textContent.includes("2026-10-12"));

  const kall = KALL.filter((k) => k.metode === "POST");
  assert.equal(kall.length, 1);
  assert.deepEqual(kall[0].kropp, { navn: "Fjordlys Elektro AS",
    orgnummer: "923609016", bransje: "tjenestebedrift" });
  // Skjemaet er BORTE: ingenting å klikke to ganger på.
  assert.equal(h.querySelector("form"), null);

  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Registrering: nøkkelen er STABIL for samme innhold", () => {
  // Et tapt svar + nytt klikk skal gi replay, ikke firma nummer to.
  const a = idempotensnokkelFor("Fjordlys AS", "923609016", "netthandel");
  const b = idempotensnokkelFor("Fjordlys AS", "923609016", "netthandel");
  assert.equal(a, b);
  // Endres innholdet, er det en ANNEN operasjon.
  assert.notEqual(a, idempotensnokkelFor("Fjordlys AS", null, "netthandel"));
  assert.notEqual(a, idempotensnokkelFor("Nordlys AS", "923609016", "netthandel"));
});

test("Registrering: feltgrensene kan ikke forskyves (CodeRabbit)", () => {
  // Rå sammenliming ga SAMME nøkkel for («AB», «C») og («A», «BC») — to
  // ULIKE registreringer ville delt idempotensnøkkel, og den andre hadde
  // fått replay av den første: hun får «ferdig», og firmaet finnes ikke.
  assert.notEqual(idempotensnokkelFor("AB", "C", "netthandel"),
                  idempotensnokkelFor("A", "BC", "netthandel"));
  assert.notEqual(idempotensnokkelFor("Fjordlys", "AS", "netthandel"),
                  idempotensnokkelFor("Fjordlys AS", "", "netthandel"));
  // En utelatt verdi er ikke det samme som en tom streng.
  assert.notEqual(idempotensnokkelFor("X AS", null, "netthandel"),
                  idempotensnokkelFor("X AS", "", "netthandel"));
  // Og nøkkelen er fortsatt en gyldig, kort header-verdi.
  const n = idempotensnokkelFor("Ærlig Øre AS", "923609016", "handverk-bygg");
  assert.match(n, /^firmareg-[0-9a-f]{16}$/);
});

test("Registrering: orgnummeret sies ved FELTET, ikke bare på toppen",
     async () => {
  SVAR = {};
  const h = nyHoved();
  visFirmaregistrering(h, ctx());
  fyll(h, { orgnr: "12345" });
  await vent(() => h.querySelector("#firmareg-orgnr[aria-invalid=true]"));

  const inp = h.querySelector("#firmareg-orgnr");
  assert.equal(inp.getAttribute("aria-errormessage"), "firmareg-orgnr-feil");
  const feil = h.querySelector("#firmareg-orgnr-feil");
  assert.equal(feil.hidden, false);
  assert.ok(feil.textContent.includes(t("ui.firmareg.feil.orgnummer")));
  // INGEN FORESPØRSEL ble sendt — feilen fanges før den når serveren.
  assert.equal(KALL.filter((k) => k.metode === "POST").length, 0);
  // Og fokus står i feltet hun skal rette.
  assert.equal(h.ownerDocument.activeElement, inp);

  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Registrering: tomt navn stopper før serveren", async () => {
  SVAR = {};
  const h = nyHoved();
  visFirmaregistrering(h, ctx());
  fyll(h, { navn: "   " });
  await vent(() => h.querySelector("#firmareg-navn[aria-invalid=true]"));
  assert.equal(KALL.filter((k) => k.metode === "POST").length, 0);
  assert.ok(h.textContent.includes(t("ui.firmareg.feil.navn")));
});

test("Registrering: orgnummer er VALGFRITT", () => {
  assert.equal(orgnummerFeil(""), null);
  assert.equal(orgnummerFeil(null), null);
  assert.equal(orgnummerFeil("923 609 016"), null, "mellomrom skal tåles");
  assert.equal(orgnummerFeil("12345"), "ui.firmareg.feil.orgnummer");
  assert.equal(orgnummerFeil("abcdefghi"), "ui.firmareg.feil.orgnummer");
});

test("Registrering: taket sies med sine egne ord", async () => {
  SVAR = { "/v1/firma/registrer": 409, _feilkode: "firma_tak_naadd" };
  const h = nyHoved();
  visFirmaregistrering(h, ctx());
  fyll(h);
  await vent(() => h.textContent.includes(t("ui.firmareg.feil.tak")));
  // Knappen er BRUKBAR IGJEN — en feil skal ikke låse skjemaet.
  assert.equal(h.querySelector("button[type=submit]").disabled, false);
});

test("Registrering: en ukjent feil sier noe, ikke ingenting", async () => {
  SVAR = { "/v1/firma/registrer": 500, _feilkode: "noe_annet" };
  const h = nyHoved();
  visFirmaregistrering(h, ctx());
  fyll(h);
  await vent(() => h.textContent.includes(t("ui.firmareg.feil.ukjent")));
  assert.equal(h.querySelector("button[type=submit]").disabled, false);
});
