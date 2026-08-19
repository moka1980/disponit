// 047 — versjonshistorikk, diff og rullbakk (klarsignal §6/§7, portene
// 21, 39–43): tabell med caption/scope, aktiv versjon som TEKST, ubundne
// versjoner sier «attestanter ikke bundet» (aldri feil attestanter),
// diffen er en tekstliste med retningen i ord FØRST, rullbakk er en
// alertdialog og POSTer rollback_av_versjon UTEN innhold (serveren eier
// kopien), axe rent.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visPolicyadmin } from "../static/js/flater/policyadmin.js";
import { visPolicy } from "../static/js/flater/policy.js";
import { opphavTekst } from "../static/js/flater/policyhistorikk.js";

settI18nForTest(NB, "nb");

const VERSJONER = { policy_id: "faktura-no", versjoner: [
  { versjon: "3", innholds_hash: "h3", aktiv: true,
    opprettet: "2026-08-19T10:00:00+00:00",
    aktivert_ts: "2026-08-19T10:00:00+00:00",
    attestanter: ["ida", "jon"], aktivert_av_operasjon: "aktiver-u3-r1",
    rollback_av_versjon: "1", rollback_kilde: "bundet", generasjon: 30 },
  { versjon: "2", innholds_hash: "h2", aktiv: false,
    opprettet: "2026-08-18T10:00:00+00:00",
    aktivert_ts: null, attestanter: null, aktivert_av_operasjon: null,
    rollback_av_versjon: null, generasjon: 20 },
  { versjon: "1", innholds_hash: "h1", aktiv: false,
    opprettet: "2026-08-17T10:00:00+00:00",
    aktivert_ts: "2026-08-17T10:00:00+00:00",
    attestanter: ["ida"], aktivert_av_operasjon: "aktiver-u1-r1",
    rollback_av_versjon: null, generasjon: 10 },
] };

const DIFF = { policy_id: "faktura-no", fra: "1", til: "3",
  risikoklasse: "UTVIDER",
  klassifisering_endringer: [],
  diff: { endringer: [
    { sti: "handlinger[0].grenser.belop_maks", type: "endret",
      fra: "1000", til: "5000" },
    { sti: "roller[1]", type: "lagt_til", til: { id: "regnskap" } },
  ] } };

const AKTIVE = { policyer: [
  { policy_id: "faktura-no", versjon: "3", innholds_hash: "h3" },
] };

let SVAR;
let POSTET;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  if (opts && opts.method && opts.method !== "GET") {
    POSTET.push({ sti, headers: opts.headers || {},
      kropp: opts.body ? JSON.parse(opts.body) : null });
    return { ok: true, status: 201,
      json: async () => ({ utkast_id: "u-ny" }) };
  }
  const d = SVAR[sti];
  return d ? { ok: true, status: 200, json: async () => d }
           : { ok: false, status: 404, json: async () => ({ feil: "x" }) };
};

function ctx() {
  return { sprak: "nb", scopes: ["policy:read", "policy:write"],
    tenant: "acme", paaUautorisert: () => {} };
}
async function vent(pred, n = 80) {
  for (let i = 0; i < n; i++) { if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0)); }
  return pred();
}
function nyHoved() {
  const brett = nyttBrett();
  const m = document.createElement("main");
  m.id = "hovedinnhold"; m.tabIndex = -1;
  brett.append(m); return m;
}
const finn = (h, tekst) => [...h.querySelectorAll("button")]
  .find((k) => k.textContent === tekst);

async function aapneHistorikk(h) {
  visPolicyadmin(h, ctx());
  await vent(() => finn(h, t("ui.historikk.knapp")));
  finn(h, t("ui.historikk.knapp")).dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector(".datatabell caption"));
}

// Opphavet er en påstand om en GENERASJON, ikke om et nummer (Codex P2).
// Nummeret frigjøres av sletting og kan gjenskapes med annet innhold, så
// «rullbakk fra 1» må ikke stå der når kilden er borte eller ubundet.
test("historikk: opphavskolonnen skiller bundet, borte og ubundet kilde",
  () => {
    assert.equal(opphavTekst({ rollback_av_versjon: null }), "");
    assert.equal(
      opphavTekst({ rollback_av_versjon: "1", rollback_kilde: "bundet" }),
      t("ui.historikk.rullbakk_fra").replace("{n}", "1"));
    const borte = opphavTekst(
      { rollback_av_versjon: "1", rollback_kilde: "borte" });
    assert.equal(borte,
      t("ui.historikk.rullbakk_fra_borte").replace("{n}", "1"));
    assert.notEqual(borte, t("ui.historikk.rullbakk_fra").replace("{n}", "1"));
    // Ingen `rollback_kilde` (eller `ubundet`) er IKKE det samme som
    // bundet: et rullbakkutkast fra før hashen fantes kan ikke måles.
    assert.equal(opphavTekst({ rollback_av_versjon: "1" }),
                 t("ui.historikk.rullbakk_fra_ubundet").replace("{n}", "1"));
    assert.equal(
      opphavTekst({ rollback_av_versjon: "1", rollback_kilde: "ubundet" }),
      t("ui.historikk.rullbakk_fra_ubundet").replace("{n}", "1"));
  });

test("historikk: tabell med caption, aktiv som TEKST, ubundet som tekst,"
     + " axe rent", async () => {
  POSTET = [];
  SVAR = { "/v1/policyutkast": { utkast: [] },
    "/v1/policy/aktive": AKTIVE,
    "/v1/policy/faktura-no/versjoner": VERSJONER };
  const h = nyHoved();
  await aapneHistorikk(h);
  const tabell = h.querySelector(".datatabell");
  assert.ok(tabell.querySelector("caption").textContent
    .includes("faktura-no"));
  for (const th of tabell.querySelectorAll("thead th")) {
    assert.equal(th.getAttribute("scope"), "col");
  }
  // Port 40: aktiv versjon markert med TEKST, aldri kun stil.
  assert.ok(tabell.textContent.includes(t("ui.historikk.aktiv_na")));
  // Port 21: den ubundne versjonen sier det — aldri feil attestanter.
  assert.ok(tabell.textContent.includes(
    t("ui.historikk.attestanter_ubundet")));
  assert.ok(tabell.textContent.includes("ida, jon"));
  // Rullbakk-opphavet vises som tekst.
  assert.ok(tabell.textContent.includes(
    t("ui.historikk.rullbakk_fra").replace("{n}", "1")));
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("historikk: diffen er en TEKSTLISTE med retningen i ord først",
  async () => {
    POSTET = [];
    SVAR = { "/v1/policyutkast": { utkast: [] },
      "/v1/policy/aktive": AKTIVE,
      "/v1/policy/faktura-no/versjoner": VERSJONER,
      "/v1/policy/faktura-no/diff": DIFF };
    const h = nyHoved();
    await aapneHistorikk(h);
    const vis = finn(h, t("ui.historikk.vis_diff"));
    assert.ok(vis, "diffvelgeren finnes med ≥2 versjoner");
    // Velgerne er <select> med <label> (§7).
    assert.ok(h.querySelector('label[for="hist-fra"]'));
    assert.ok(h.querySelector('label[for="hist-til"]'));
    vis.dispatchEvent(new window.Event("click"));
    await vent(() => h.querySelector(".diffliste"));
    // Retningen i ord FØRST (port 40) — overskriften bærer den.
    const overskrift = h.querySelector(".historikk-diff h4").textContent;
    assert.ok(overskrift.startsWith(t("ui.historikk.retning.UTVIDER")),
      overskrift);
    // Hver endring er tekst, ingen <pre> (port §7).
    assert.equal(h.querySelectorAll(".historikk-diff pre").length, 0);
    const linjer = [...h.querySelectorAll(".diffliste li")]
      .map((li) => li.textContent);
    assert.equal(linjer.length, 2);
    assert.ok(linjer[0].startsWith(t("ui.historikk.endring.endret")));
    assert.ok(linjer[1].startsWith(t("ui.historikk.endring.lagt_til")));
    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

test("historikk: rullbakk er en alertdialog og POSTer uten innhold",
  async () => {
    POSTET = [];
    SVAR = { "/v1/policyutkast": { utkast: [] },
      "/v1/policy/aktive": AKTIVE,
      "/v1/policy/faktura-no/versjoner": VERSJONER,
      "/v1/policyutkast/u-ny": { utkast_id: "u-ny",
        policy_id: "faktura-no", status: "utkast", utkastversjon: 1,
        innholds_hash: null, diff: { endringer: [] },
        klassifisering_endringer: [], opprettet_av: "meg" } };
    const h = nyHoved();
    await aapneHistorikk(h);
    const rb = [...h.querySelectorAll("button")]
      .filter((k) => k.textContent === t("ui.historikk.rullbakk"));
    assert.equal(rb.length, 3, "rullbakk per versjon (policy:write)");
    rb[2].dispatchEvent(new window.Event("click"));   // versjon 1
    // §7: alertdialog med konsekvensen — og aria-describedby til teksten.
    await vent(() => document.querySelector('[role="alertdialog"]'));
    const dlg = document.querySelector('[role="alertdialog"]');
    assert.ok(dlg.getAttribute("aria-describedby"));
    assert.ok(dlg.textContent.includes(
      t("ui.historikk.rullbakk_tekst").replace("{n}", "1")));
    // Bekreft: POST bærer rollback_av_versjon og Idempotency-Key —
    // og ALDRI innhold (kopien er serverens sannhet, port 22).
    [...dlg.querySelectorAll("button")]
      .find((k) => k.textContent === t("ui.historikk.rullbakk"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => POSTET.length);
    assert.equal(POSTET[0].sti, "/v1/policyutkast");
    assert.equal(POSTET[0].kropp.rollback_av_versjon, "1");
    // …og GENERASJONEN linjen viste (Codex P2): nummeret peker,
    // generasjonen identifiserer. Uten den kunne serveren kopiert en
    // erstatning som kom til mellom visningen og klikket.
    assert.equal(POSTET[0].kropp.rollback_av_generasjon, 10);
    assert.ok(!("innhold" in POSTET[0].kropp));
    assert.ok(POSTET[0].headers["Idempotency-Key"]
              || POSTET[0].headers["idempotency-key"]);
  });

// Codex P2: `Bekreftelsesdialog` lukker seg synkront på primærklikk, så et
// tvetydig avbrudd (forespørselen kom fram, svaret gikk tapt) etterlater
// brukeren med lukket dialog og ingen kvittering. Eneste vei videre er å
// åpne dialogen på nytt — og med nøkkelen laget i dialogen ble det andre
// forsøket en NY operasjon som laget et rullbakk-utkast nummer to, i stedet
// for å replaye det første. Nøkkelen bor derfor på visningen, per versjon.
test("historikk: rullbakk-nokkelen overlever at dialogen aapnes paa nytt",
  async () => {
    POSTET = [];
    SVAR = { "/v1/policyutkast": { utkast: [] },
      "/v1/policy/aktive": AKTIVE,
      "/v1/policy/faktura-no/versjoner": VERSJONER,
      "/v1/policyutkast/u-ny": { utkast_id: "u-ny",
        policy_id: "faktura-no", status: "utkast", utkastversjon: 1,
        innholds_hash: null, diff: { endringer: [] },
        klassifisering_endringer: [], opprettet_av: "meg" } };
    const h = nyHoved();
    await aapneHistorikk(h);
    const bekreft = async (n) => {
      const rb = [...h.querySelectorAll("button")]
        .filter((k) => k.textContent === t("ui.historikk.rullbakk"));
      rb[n].dispatchEvent(new window.Event("click"));
      await vent(() => document.querySelector('[role="alertdialog"]'));
      const dlg = document.querySelector('[role="alertdialog"]');
      [...dlg.querySelectorAll("button")]
        .find((k) => k.textContent === t("ui.historikk.rullbakk"))
        .dispatchEvent(new window.Event("click"));
    };
    const nokkel = (i) => POSTET[i].headers["Idempotency-Key"]
      || POSTET[i].headers["idempotency-key"];
    await bekreft(2);                      // versjon 1
    await vent(() => POSTET.length === 1);
    // Dialogen er lukket; brukeren åpner den på nytt for SAMME versjon.
    await bekreft(2);
    await vent(() => POSTET.length === 2);
    assert.equal(nokkel(0), nokkel(1),
      "ny dialog gav ny nøkkel — retryen ville laget et utkast til");
    // En ANNEN versjon er en annen operasjon og har sin egen nøkkel.
    await bekreft(1);                      // versjon 2
    await vent(() => POSTET.length === 3);
    assert.notEqual(nokkel(2), nokkel(0));
  });

// 047, Codex P2: rutene bak historikken krever `policy:read`, ikke
// `policy:write`. Inngangen lå likevel bare i policyadmin — bak
// `kanForvaltePolicy` i sitekartet OG inne i `aktivePolicyerSeksjon`, som
// returnerer tomt uten skrivetilgang. En `leser` kunne altså kalle
// endepunktene og hadde ingen vei dit. Nå står knappen på leseflaten, og
// visningen er den samme — uten rullbakk, som er `policy:write`.
const DTO = { skjemaversjon: 1, policy_id: "faktura-no", versjon: "3",
  innholds_hash: "h3", roller: [{ id: "admin" }], handlinger: [],
  verifikatorer: [] };

test("historikk: leseokten naar historikken fra policy-flaten, uten rullbakk",
  async () => {
    POSTET = [];
    SVAR = { "/v1/policy/aktiv": DTO,
      "/v1/policy/faktura-no/versjoner": VERSJONER };
    const h = nyHoved();
    const leser = { sprak: "nb", scopes: ["policy:read"], tenant: "acme",
      paaUautorisert: () => {} };
    visPolicy(h, leser);
    await vent(() => finn(h, t("ui.historikk.knapp")));
    finn(h, t("ui.historikk.knapp")).dispatchEvent(new window.Event("click"));
    await vent(() => h.querySelector(".datatabell caption"));
    const tabell = h.querySelector(".datatabell");
    assert.ok(tabell.querySelector("caption").textContent
      .includes("faktura-no"));
    assert.ok(tabell.textContent.includes("ida, jon"));
    // Rullbakken er `policy:write` — knappen LAGES ikke, den skjules ikke.
    assert.equal([...h.querySelectorAll("button")]
      .filter((k) => k.textContent === t("ui.historikk.rullbakk")).length, 0);
    assert.equal([...tabell.querySelectorAll("thead th")]
      .map((th) => th.textContent)
      .includes(t("ui.historikk.kol.handlinger")), false);
    // Tilbakeveien fører til leseflaten, ikke til policyadmin.
    finn(h, t("ui.historikk.tilbake")).dispatchEvent(new window.Event("click"));
    await vent(() => h.querySelector(".policy-sec"));
    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

// Bootstrap-raden sier hva den ER (047, Codex P2): «lagt inn ved oppsett»,
// ikke «attestanter ikke bundet» — den siste beskriver en rad fra før
// lineagen fantes, og en oppsettsregistrering fra i går er ikke det.
test("historikk: bootstrap-raden skiller seg fra en ubundet historisk rad",
  async () => {
    POSTET = [];
    SVAR = { "/v1/policyutkast": { utkast: [] },
      "/v1/policy/aktive": AKTIVE,
      "/v1/policy/faktura-no/versjoner": { policy_id: "faktura-no",
        versjoner: [{ versjon: "1", innholds_hash: "h1", aktiv: true,
          opprettet: "2026-08-17T10:00:00+00:00", aktivert_ts: null,
          attestanter: null, aktivert_av_operasjon: null,
          rollback_av_versjon: null, aktiveringskilde: "bootstrap" }] } };
    const h = nyHoved();
    await aapneHistorikk(h);
    const tabell = h.querySelector(".datatabell");
    assert.ok(tabell.textContent.includes(t("ui.historikk.kilde_bootstrap")));
    assert.ok(!tabell.textContent.includes(
      t("ui.historikk.attestanter_ubundet")));
  });

// En REGISTRERT versjon er ikke en aktivert versjon (Codex P2):
// `registrer(..., aktiver=False)` gir en rad uten hendelse og uten
// aktiveringstidspunkt, og «Aktivert»-kolonnen skal da ikke låne
// registreringstidspunktet — den skal si at versjonen ikke er aktivert.
test("historikk: en aldri aktivert versjon viser ikke et aktiveringstidspunkt",
  async () => {
    POSTET = [];
    SVAR = { "/v1/policyutkast": { utkast: [] },
      "/v1/policy/aktive": AKTIVE,
      "/v1/policy/faktura-no/versjoner": { policy_id: "faktura-no",
        versjoner: [
          { versjon: "1", innholds_hash: "h1", aktiv: true,
            opprettet: "2026-08-17T10:00:00+00:00",
            aktivert_ts: "2026-08-17T10:00:00+00:00", attestanter: ["ida"],
            aktivert_av_operasjon: "aktiver-u1-r1", rollback_av_versjon: null,
            aktiveringskilde: "bootstrap", aktivert: true },
          { versjon: "9", innholds_hash: "h9", aktiv: false,
            opprettet: "2026-08-19T10:00:00+00:00", aktivert_ts: null,
            attestanter: null, aktivert_av_operasjon: null,
            rollback_av_versjon: null, aktiveringskilde: "bootstrap",
            aktivert: false }] } };
    const h = nyHoved();
    await aapneHistorikk(h);
    const rader = h.querySelectorAll(".datatabell tbody tr");
    assert.equal(rader.length, 2);
    assert.ok(rader[1].textContent.includes(t("ui.historikk.uaktivert")));
    // Default-diffen skal ikke bli «uaktivert → aktiv» (Codex P2): med
    // bare ÉN aktivering finnes det ingen sann retning, og eier velger.
    assert.equal(h.querySelector("#hist-fra").value, "");
    assert.equal(h.querySelector("#hist-til").value, "");
    finn(h, t("ui.historikk.vis_diff"))
      .dispatchEvent(new window.Event("click"));
    assert.ok(h.textContent.includes(t("ui.historikk.velg_begge")));
    // Ingen diff er hentet — meldingen ER svaret på klikket.
    assert.equal(h.querySelector(".diffliste"), null);
    // Registreringstidspunktet skal ikke stå der som en aktivering: 19.
    // august finnes ikke i raden, verken som dato eller klokkeslett.
    assert.ok(!rader[1].textContent.includes("19"),
              rader[1].textContent);
    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

// En rullbakk er en påstand om at vi går TILBAKE til noe (Codex P2). Den
// aldri aktiverte raden over er et arbeidsstykke, ikke en fortid: en kopi
// av den er en helt ordinær ny versjon, og `policyversjon_kilde` avviser
// derfor kallet. Knappen ville endt i 409 hver gang — den skal ikke stå
// der, og grunnen skal stå i stedet.
test("historikk: ingen rullbakk til en versjon som aldri har vært i kraft",
  async () => {
    POSTET = [];
    SVAR = { "/v1/policyutkast": { utkast: [] },
      "/v1/policy/aktive": AKTIVE,
      "/v1/policy/faktura-no/versjoner": { policy_id: "faktura-no",
        versjoner: [
          { versjon: "1", innholds_hash: "h1", aktiv: true,
            opprettet: "2026-08-17T10:00:00+00:00",
            aktivert_ts: "2026-08-17T10:00:00+00:00", attestanter: ["ida"],
            aktivert_av_operasjon: "aktiver-u1-r1", rollback_av_versjon: null,
            aktiveringskilde: "bootstrap", aktivert: true },
          { versjon: "9", innholds_hash: "h9", aktiv: false,
            opprettet: "2026-08-19T10:00:00+00:00", aktivert_ts: null,
            attestanter: null, aktivert_av_operasjon: null,
            rollback_av_versjon: null, aktiveringskilde: "bootstrap",
            aktivert: false }] } };
    const h = nyHoved();
    await aapneHistorikk(h);
    const rader = h.querySelectorAll(".datatabell tbody tr");
    assert.equal(rader.length, 2);
    // Den aktiverte raden beholder handlingen — sperren er per VERSJON, og
    // må ikke bli et stille tap av rullbakk for hele serien.
    const knapper = Array.from(h.querySelectorAll("button"))
      .filter((k) => k.textContent === t("ui.historikk.rullbakk"));
    assert.equal(knapper.length, 1,
      "rullbakk skal tilbys for den aktiverte raden, og bare den");
    assert.ok(rader[0].querySelector("button"),
      "den aktiverte raden mistet rullbakken");
    assert.equal(rader[1].querySelector("button"), null,
      "rullbakk tilbys til en versjon som aldri har vært i kraft");
    // Grunnen står der; en tom celle ville bare sett ut som en glipp.
    assert.ok(rader[1].textContent.includes(
      t("ui.historikk.rullbakk_uaktivert")),
      rader[1].textContent);
    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

// En slettet generasjons AKTIVERING står i loggen selv om dokumentet er
// borte (Codex P2): `slett_ubrukt_policy` fjerner raden, `policyaktivering`
// er evig. Linjen hører hjemme i revisjonssporet — men den kan verken
// diffes eller rulles tilbake, og var nummeret gjenskapt etterpå, ville et
// oppslag servert den NYE generasjonens dokument under den gamles linje.
test("historikk: en slettet generasjon staar i loggen, uten diff og rullbakk",
  async () => {
    POSTET = [];
    SVAR = { "/v1/policyutkast": { utkast: [] },
      "/v1/policy/aktive": AKTIVE,
      "/v1/policy/faktura-no/versjoner": { policy_id: "faktura-no",
        versjoner: [
          // Den gjenskapte, levende generasjonen av SAMME nummer.
          { versjon: "1", innholds_hash: "h1ny", aktiv: true,
            opprettet: "2026-08-19T10:00:00+00:00",
            aktivert_ts: "2026-08-19T10:00:00+00:00", attestanter: ["ida"],
            aktivert_av_operasjon: "aktiver-u9-r1", rollback_av_versjon: null,
            aktiveringskilde: "styrt", aktivert: true, innhold_finnes: true },
          // Den slettede: hendelsen står, dokumentet er borte.
          { versjon: "1", innholds_hash: "h1", aktiv: false,
            opprettet: "2026-08-17T10:00:00+00:00",
            aktivert_ts: "2026-08-17T10:00:00+00:00", attestanter: ["jon"],
            aktivert_av_operasjon: "aktiver-u1-r1", rollback_av_versjon: null,
            aktiveringskilde: "styrt", aktivert: true,
            innhold_finnes: false }] } };
    const h = nyHoved();
    await aapneHistorikk(h);
    const rader = h.querySelectorAll(".datatabell tbody tr");
    // Begge linjene står — aktiveringen forsvant ikke med raden.
    assert.equal(rader.length, 2);
    assert.ok(rader[1].textContent.includes("jon"),
      `den slettede generasjonens attestant mangler: ${rader[1].textContent}`);
    // Grunnen står der, for leseren OG for eier: ingen tom celle.
    assert.ok(rader[1].textContent.includes(t("ui.historikk.innhold_borte")),
      rader[1].textContent);
    assert.equal(rader[1].querySelector("button"), null,
      "rullbakk tilbys til en generasjon som ikke finnes lenger");
    assert.ok(rader[0].querySelector("button"),
      "den levende generasjonen mistet rullbakken");
    // Diffvelgerne finnes ikke: bare ÉN diffbar versjon igjen — og et
    // duplisert nummer i nedtrekket hadde uansett vært et tvetydig valg.
    assert.equal(h.querySelector("#hist-fra"), null);
    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

// `policy_id` er ikke et snilt tegnsett bare fordi skjemaet er strengt i
// dag (Codex P2). Lastekontrakten slipper med vilje gjennom alt aktive
// policyer fra før innstrammingen bærer, og den gamle Python-`$`-en matchet
// også en avsluttende linjeskift — `/v1/policy/aktiv` kan altså svare med
// `acme\n`. Interpolert rått forsvant linjeskiftet i URL-parseren, og flaten
// ba om historikken til `acme`: tom eller FEIL historikk, uten at noe sa fra.
const RAR = "acme\n";
const RAR_VERSJONER = { policy_id: RAR, versjoner: [
  { versjon: "2", innholds_hash: "h2", aktiv: true,
    opprettet: "2026-08-19T10:00:00+00:00",
    aktivert_ts: "2026-08-19T10:00:00+00:00",
    attestanter: ["ida"], aktivert_av_operasjon: "aktiver-u2-r1",
    rollback_av_versjon: null },
  { versjon: "1", innholds_hash: "h1", aktiv: false,
    opprettet: "2026-08-17T10:00:00+00:00",
    aktivert_ts: "2026-08-17T10:00:00+00:00",
    attestanter: ["jon"], aktivert_av_operasjon: "aktiver-u1-r1",
    rollback_av_versjon: null },
], nytt_utkast_avvist: "policy_id_ugyldig" };

test("historikk: policy-id-en prosentkodes i bade versjons- og diff-URL-en",
  async () => {
    POSTET = [];
    // NØKLENE ER KODET. Interpolerte flaten id-en rått, ville stien blitt
    // en annen, mocken svart 404, og tabellen aldri kommet.
    const kodet = encodeURIComponent(RAR);
    SVAR = { "/v1/policyutkast": { utkast: [] },
      "/v1/policy/aktive": { policyer: [
        { policy_id: RAR, versjon: "2", innholds_hash: "h2" }] } };
    SVAR["/v1/policy/" + kodet + "/versjoner"] = RAR_VERSJONER;
    SVAR["/v1/policy/" + kodet + "/diff"] = Object.assign(
      {}, DIFF, { policy_id: RAR, fra: "1", til: "2" });
    const h = nyHoved();
    await aapneHistorikk(h);
    const tabell = h.querySelector(".datatabell");
    assert.ok(tabell, "historikken kom aldri — URL-en traff ikke");
    assert.ok(tabell.textContent.includes("ida"));
    // Diffen bygger sin egen URL og har samme sak.
    finn(h, t("ui.historikk.vis_diff"))
      .dispatchEvent(new window.Event("click"));
    await vent(() => h.querySelector(".diffliste"));
    assert.ok(h.querySelector(".diffliste"),
      "diffen kom aldri — URL-en traff ikke");
  });

// Den samme arvede id-en kan LESES, men den kan ikke bære et nytt utkast:
// `opprett_utkast` avviser identiteten, så en rullbakk-knapp ville
// deterministisk endt i 400 (Codex P2). Porten sier det i svaret; flaten
// tilbyr da ikke handlingen — og sier hvorfor.
test("historikk: ingen rullbakk for en id porten ikke gir nye utkast",
  async () => {
    POSTET = [];
    const kodet = encodeURIComponent(RAR);
    SVAR = { "/v1/policyutkast": { utkast: [] },
      "/v1/policy/aktive": { policyer: [
        { policy_id: RAR, versjon: "2", innholds_hash: "h2" }] } };
    SVAR["/v1/policy/" + kodet + "/versjoner"] = RAR_VERSJONER;
    const h = nyHoved();
    await aapneHistorikk(h);
    const tabell = h.querySelector(".datatabell");
    assert.ok(tabell, "historikken kom aldri");
    // Historikken står — det er bare HANDLINGEN som ikke tilbys.
    assert.ok(tabell.textContent.includes("ida"));
    assert.ok(!finn(h, t("ui.historikk.rullbakk")),
      "rullbakk tilbys for en id porten avviser");
    assert.ok(h.textContent.includes(
      t("ui.historikk.rullbakk_sperret.policy_id_ugyldig")
        .replace("{policy}", RAR)),
      "sperren mangler forklaringen");
    const brudd = await alvorligeBrudd(h, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });

// Motprøven: uten en dom fra porten er rullbakk fortsatt handlingen den
// var. Sperren må ikke bli et stille tap av funksjonalitet.
test("historikk: rullbakk tilbys som før uten en sperre fra porten",
  async () => {
    POSTET = [];
    SVAR = { "/v1/policyutkast": { utkast: [] },
      "/v1/policy/aktive": AKTIVE,
      "/v1/policy/faktura-no/versjoner": VERSJONER };
    const h = nyHoved();
    await aapneHistorikk(h);
    assert.ok(finn(h, t("ui.historikk.rullbakk")),
      "rullbakk forsvant for en helt vanlig policy-id");
  });
