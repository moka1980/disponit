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
  if (sti === "/v1/policymaler") {
    return { ok: true, status: 200, json: async () => ({ maler: [
      { mal_id: "netthandel", bransjemal: "netthandel-no", innhold: MAL }] }) };
  }
  if (sti === "/v1/policyutkast/u-1") {
    return { ok: true, status: 200, json: async () => ({
      utkast_id: "u-1", policy_id: "acme", status: "utkast", utkastversjon: 2,
      innhold: MAL }) };
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

  // Skjema: grunnopplysninger + roller + handlinger m/ modus.
  await vent(() => h.querySelector(".editor-seksjon"));
  assert.ok(h.textContent.includes(t("ui.editor.roller")));
  assert.ok(h.textContent.includes(t("ui.editor.handlinger")));
  assert.ok(h.textContent.includes("ordre.bekreft"));
  assert.ok(h.querySelector("select"), "modus-velger mangler");
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);

  // Sett policy_id (første tekstfelt = policy_id) og modus.
  const pid = h.querySelector("input.felt-inp");
  pid.value = "acme-netthandel";
  pid.dispatchEvent(new window.Event("input"));
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
  await vent(() => aapnet === "u-ny");
  assert.equal(aapnet, "u-ny");
  if (cookieDesc) Object.defineProperty(document, "cookie", cookieDesc);
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
