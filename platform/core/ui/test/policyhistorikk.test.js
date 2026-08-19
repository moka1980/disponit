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

settI18nForTest(NB, "nb");

const VERSJONER = { policy_id: "faktura-no", versjoner: [
  { versjon: "3", innholds_hash: "h3", aktiv: true,
    opprettet: "2026-08-19T10:00:00+00:00",
    aktivert_ts: "2026-08-19T10:00:00+00:00",
    attestanter: ["ida", "jon"], aktivert_av_operasjon: "aktiver-u3-r1",
    rollback_av_versjon: "1" },
  { versjon: "2", innholds_hash: "h2", aktiv: false,
    opprettet: "2026-08-18T10:00:00+00:00",
    aktivert_ts: null, attestanter: null, aktivert_av_operasjon: null,
    rollback_av_versjon: null },
  { versjon: "1", innholds_hash: "h1", aktiv: false,
    opprettet: "2026-08-17T10:00:00+00:00",
    aktivert_ts: "2026-08-17T10:00:00+00:00",
    attestanter: ["ida"], aktivert_av_operasjon: "aktiver-u1-r1",
    rollback_av_versjon: null },
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
    assert.ok(!("innhold" in POSTET[0].kropp));
    assert.ok(POSTET[0].headers["Idempotency-Key"]
              || POSTET[0].headers["idempotency-key"]);
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
