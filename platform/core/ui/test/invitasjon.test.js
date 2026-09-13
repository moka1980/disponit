// Invitasjonsflatene (194/195) mot mocket API.
//
// To ting skiller disse fra husets andre skjemaer, og begge måles her:
//
//   * LENKEN VISES ÉN GANG. Tokenet finnes ikke i basen og kan ikke hentes
//     igjen, så flaten må gi henne noe å kopiere — ikke en flyktig melding.
//   * ADRESSEN RYDDES ETTER at innløsningen lyktes, aldri før. Rydder vi
//     først og kallet feiler, har hun mistet lenken uten å ha blitt medlem,
//     og ingenting å prøve på nytt med.
//
// Og en tredje, som er et sikkerhetsvalg og ikke en detalj: ryddingen er
// HYGIENE. `replaceState` kaster i sandkasser, og feiler den, står tokenet
// i adressen. Det vi HVILER på er at tokenet er engangs.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visBliMed, lesInvitasjon, ryddAdressen } from "../static/js/flater/blimed.js";
import { visKundeadmin, INVITERBARE } from "../static/js/flater/kundeadmin.js";

settI18nForTest(NB, "nb");

let SVAR;
const KALL = [];
globalThis.fetch = async (url, opts = {}) => {
  KALL.push({ url: String(url), metode: opts.method || "GET",
    idem: (opts.headers || {})["Idempotency-Key"],
    kropp: opts.body ? JSON.parse(opts.body) : null });
  const sti = String(url).split("?")[0];
  const oppf = SVAR[sti];
  if (typeof oppf === "number") {
    return { ok: false, status: oppf,
      json: async () => ({ feil: SVAR._feilkode || "x" }) };
  }
  if (!oppf) {
    return { ok: false, status: 404, json: async () => ({ feil: "ikke_funnet" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

async function vent(pred, n = 80) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}

function nyHoved(sok = "") {
  const brett = nyttBrett();
  const m = document.createElement("main");
  m.id = "hovedinnhold"; m.tabIndex = -1;
  brett.append(m);
  KALL.length = 0;
  window.history.replaceState({}, "", `/${sok}`);
  return m;
}

// ---------------------------------------------------------------------------
// Lenken selv — ren funksjon.
// ---------------------------------------------------------------------------

test("Invitasjon: lenken leses bare når BEGGE delene er der", () => {
  assert.deepEqual(lesInvitasjon("?t=fjordlys-as&k=hemmelig"),
                   { tenant: "fjordlys-as", token: "hemmelig" });
  // Halve lenken er ingen lenke: uten token ville flaten vist en knapp som
  // garantert feiler, og uten firma vet den ikke hvor hun skal.
  assert.equal(lesInvitasjon("?t=fjordlys-as"), null);
  assert.equal(lesInvitasjon("?k=hemmelig"), null);
  assert.equal(lesInvitasjon(""), null);
  assert.equal(lesInvitasjon("?t=%20&k=hemmelig"), null, "blanke ignoreres");
});

test("Invitasjon: ryddingen fjerner BARE tokenet", () => {
  nyHoved("?visning=blimed&t=fjordlys-as&k=hemmelig");
  assert.equal(ryddAdressen(), true);
  const q = new URLSearchParams(window.location.search);
  assert.equal(q.get("k"), null, "tokenet står igjen i adressen");
  // `t` og `visning` blir stående: en oppfriskning skal fortsatt vise
  // riktig flate, og feilmeldingen skal nevne riktig firma.
  assert.equal(q.get("t"), "fjordlys-as");
  assert.equal(q.get("visning"), "blimed");
});

test("Invitasjon: ryddingen kaster aldri, selv om nettleseren nekter", () => {
  // `ruter.js`: «adressefeltet er ikke vår å stole på — replaceState kaster
  // i sandkasser». En flate som antok at ryddingen lyktes, ville kastet en
  // uhåndtert feil nettopp der hun står midt i å bli medlem.
  const falsk = { location: { href: "https://x.example/?t=a&k=b" },
    history: { replaceState() { throw new Error("sandkasse"); } } };
  assert.equal(ryddAdressen(falsk), false);
});

// ---------------------------------------------------------------------------
// Bli med-flaten.
// ---------------------------------------------------------------------------

test("Bli med: hele veien, og hun får vite at hun må logge inn på nytt",
     async () => {
  SVAR = { "/v1/invitasjoner/innloes": { tenant: "fjordlys-as",
    roller: ["leser", "godkjenner"] } };
  const h = nyHoved("?visning=blimed&t=fjordlys-as&k=hemmelig123");
  visBliMed(h, { scopes: ["firma:opprett"] });

  assert.ok(h.textContent.includes("fjordlys-as"), "firmaet nevnes ikke");
  h.querySelector("button").dispatchEvent(
    new window.MouseEvent("click", { bubbles: true }));
  await vent(() => h.textContent.includes(t("ui.blimed.ferdig_tittel")));

  const kall = KALL.filter((k) => k.metode === "POST");
  assert.equal(kall.length, 1);
  assert.deepEqual(kall[0].kropp,
    { tenant: "fjordlys-as", token: "hemmelig123" });
  // Rollene sies, ikke bare «ok».
  assert.ok(h.textContent.includes("leser"));
  // Og veien videre finnes.
  assert.equal(h.querySelector("a.knapp").getAttribute("href"), "/");
  // ADRESSEN ER RYDDET etter at det lyktes.
  assert.equal(new URLSearchParams(window.location.search).get("k"), null);

  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Bli med: adressen ryddes IKKE når innløsningen feiler", async () => {
  // Ryddet vi først, ville hun mistet lenken uten å ha blitt medlem — og
  // ikke hatt noe å prøve på nytt med.
  SVAR = { "/v1/invitasjoner/innloes": 409, _feilkode: "invitasjon_ugyldig" };
  const h = nyHoved("?visning=blimed&t=fjordlys-as&k=hemmelig123");
  visBliMed(h, { scopes: ["firma:opprett"] });
  h.querySelector("button").dispatchEvent(
    new window.MouseEvent("click", { bubbles: true }));
  await vent(() => h.textContent.includes(t("ui.blimed.feil.ugyldig")));

  assert.equal(new URLSearchParams(window.location.search).get("k"),
               "hemmelig123", "lenken ble ryddet bort selv om det feilet");
  // Knappen er brukbar igjen — en feil skal ikke låse flaten.
  assert.equal(h.querySelector("button").disabled, false);
});

test("Bli med: en halv lenke sier hva som mangler", async () => {
  SVAR = {};
  const h = nyHoved("?visning=blimed&t=fjordlys-as");
  visBliMed(h, { scopes: ["firma:opprett"] });
  assert.ok(h.textContent.includes(t("ui.blimed.mangler_tekst")));
  assert.equal(h.querySelector("button"), null, "en knapp som må feile");
  assert.equal(KALL.length, 0);

  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Invitasjon: innløsningsnøkkelen skiller to lenker til SAMME firma",
     async () => {
  // Første utgave nøklet bare på tenant (CodeRabbit). To ULIKE
  // invitasjoner til samme firma — en utløpt og en ny — ville delt nøkkel,
  // og det andre kallet fått REPLAY av det førstes svar i stedet for å bli
  // innløst.
  const { innloesInvitasjon } = await import("../static/js/api.js");
  SVAR = { "/v1/invitasjoner/innloes": { tenant: "a", roller: ["leser"] } };
  KALL.length = 0;
  await innloesInvitasjon("fjordlys-as", "token-EN");
  await innloesInvitasjon("fjordlys-as", "token-TO");
  await innloesInvitasjon("fjordlys-as", "token-EN");
  const n = KALL.map((k) => k.idem);
  assert.notEqual(n[0], n[1], "to ulike lenker delte idempotensnøkkel");
  assert.equal(n[0], n[2], "samme lenke ga ny nøkkel — retry ville duplisert");
  // Og hemmeligheten står ikke i headeren.
  assert.ok(!n[0].includes("token-EN"), "tokenet lekket inn i nøkkelen");
});

// ---------------------------------------------------------------------------
// Admins side: skjemaet i kundeadmin.
// ---------------------------------------------------------------------------

function ctxAdmin(scopes = ["firma:inviter", "security:read"]) {
  return { sprak: "nb", scopes, tenant: "fjordlys-as", moduler: [],
           paaUautorisert() {} };
}

test("Inviter: admin får en lenke hun kan kopiere, og den vises én gang",
     async () => {
  SVAR = {
    "/v1/invitasjoner": { invitasjoner: [] },
  };
  const h = nyHoved();
  visKundeadmin(h, ctxAdmin());
  await vent(() => h.querySelector("#inv-leser"));

  // Rollene som tilbys: guidens tre + admin.
  for (const id of INVITERBARE) {
    assert.ok(h.querySelector(`#inv-${id}`), `mangler avkryssing for ${id}`);
  }
  assert.ok(INVITERBARE.includes("admin"),
    "et firma må kunne ha mer enn én administrator");

  SVAR["/v1/invitasjoner"] = { tenant: "fjordlys-as", token: "T0K3N",
    roller: ["leser"], utloper: "2026-09-20T10:00:00+00:00" };
  h.querySelector("#inv-leser").checked = true;
  h.querySelector("form.kv-skjema").dispatchEvent(
    new window.Event("submit", { cancelable: true, bubbles: true }));
  await vent(() => h.querySelector("#inv-lenke"));

  const felt = h.querySelector("#inv-lenke");
  assert.ok(felt.value.includes("visning=blimed"));
  assert.ok(felt.value.includes("t=fjordlys-as"));
  assert.ok(felt.value.includes("k=T0K3N"), "lenken mangler tokenet");
  // Feltet er LESBART og merkbart — hun skal kunne kopiere det.
  assert.equal(felt.readOnly, true);
  assert.ok(h.textContent.includes(t("ui.kundeadmin.inviter_lenke")));
});

test("Inviter: ingen rolle valgt stopper før serveren", async () => {
  SVAR = { "/v1/invitasjoner": { invitasjoner: [] } };
  const h = nyHoved();
  visKundeadmin(h, ctxAdmin());
  await vent(() => h.querySelector("form.kv-skjema"));
  KALL.length = 0;

  h.querySelector("form.kv-skjema").dispatchEvent(
    new window.Event("submit", { cancelable: true, bubbles: true }));
  await vent(() => h.textContent.includes(t("ui.kundeadmin.inviter_ingen_rolle")));
  assert.equal(KALL.filter((k) => k.metode === "POST").length, 0);
});

test("Inviter: en leser ser hverken skjema eller liste", async () => {
  SVAR = { "/v1/invitasjoner": { invitasjoner: [] } };
  const h = nyHoved();
  visKundeadmin(h, ctxAdmin(["decisions:read", "part:read"]));
  await vent(() => h.textContent.includes(t("ui.kundeadmin.brukere_tittel")));

  assert.equal(h.querySelector("#inv-leser"), null,
    "en leser fikk invitasjonsskjemaet");
  assert.ok(!h.textContent.includes(t("ui.kundeadmin.invitasjoner")),
    "en leser fikk invitasjonslista");
  assert.equal(KALL.filter((k) => k.url.includes("/v1/invitasjoner")).length, 0,
    "flaten spurte etter invitasjoner uten å ha scopet");
});

test("Inviter: sikkerhetsrollen ser lista, men ikke skjemaet", async () => {
  SVAR = { "/v1/invitasjoner": { invitasjoner: [
    { merke: "a1b2c3d4", roller: ["leser"], opprettet_av: "bruker:bid_x",
      opprettet: "2026-09-13T09:00:00+00:00",
      utloper: "2026-09-20T09:00:00+00:00", brukt: null }] } };
  const h = nyHoved();
  visKundeadmin(h, ctxAdmin(["security:read"]));
  await vent(() => h.textContent.includes("a1b2c3d4"));

  assert.equal(h.querySelector("#inv-leser"), null,
    "sikkerhetsrollen kunne invitere");
  assert.ok(h.textContent.includes(t("ui.kundeadmin.invitasjon_apen")));
});


test("Inviter: nøkkelen holder gjennom en retry, men bytter med rollevalget",
     async () => {
  // Et tapt svar + nytt klikk skal REPLAYe, ikke lage invitasjon nummer to
  // som blir liggende ubrukt til den utløper (CodeRabbit).
  SVAR = { "/v1/invitasjoner": 500, _feilkode: "drift" };
  const h = nyHoved();
  visKundeadmin(h, ctxAdmin());
  await vent(() => h.querySelector("#inv-leser"));
  const send = () => h.querySelector("form.kv-skjema").dispatchEvent(
    new window.Event("submit", { cancelable: true, bubbles: true }));

  h.querySelector("#inv-leser").checked = true;
  KALL.length = 0;
  send();
  await vent(() => KALL.filter((k) => k.metode === "POST").length === 1);
  send();
  await vent(() => KALL.filter((k) => k.metode === "POST").length === 2);
  const poster = KALL.filter((k) => k.metode === "POST");
  assert.equal(poster[0].idem, poster[1].idem,
    "en retry fikk ny nøkkel — serveren ville laget to invitasjoner");

  // Endrer hun valget, er det en ANNEN operasjon.
  h.querySelector("#inv-godkjenner").checked = true;
  send();
  await vent(() => KALL.filter((k) => k.metode === "POST").length === 3);
  const tredje = KALL.filter((k) => k.metode === "POST")[2];
  assert.notEqual(tredje.idem, poster[0].idem,
    "et nytt rollevalg gjenbrukte nøkkelen fra det forrige");
});
