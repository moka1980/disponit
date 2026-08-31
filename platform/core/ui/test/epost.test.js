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
import { visEpost, settNavigasjonForTest }
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
