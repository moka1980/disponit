// Planflaten (044 §9, port 28-31) — akseptansekriteriene, målt: ekte
// <form> med fieldset/legend og rytme som RADIOKNAPPER, ukedag/månedsdag
// borte med `hidden` (ikke bare visuelt), valideringsfeil med
// aria-invalid + fokus på FØRSTE feil, overgang annonsert i role="alert"
// med PAUSEGRUNNEN synlig i listen, tick-historikk som tabell med
// caption/scope og utfall som TEKST, axe rent — og mutasjonsknappene
// finnes ikke uten `plan:opprett`.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visPlan } from "../static/js/flater/plan.js";

settI18nForTest(NB, "nb");

let KALL;
let SVAR;
globalThis.fetch = async (url, opts = {}) => {
  const sti = url.split("?")[0];
  KALL.push({ sti, metode: opts.method || "GET",
    headers: opts.headers || {},
    kropp: opts.body ? JSON.parse(opts.body) : null });
  const svar = (typeof SVAR === "function") ? SVAR(sti, opts) : SVAR[sti];
  if (svar === undefined) {
    return { ok: false, status: 404,
      json: async () => ({ feil: "ikke_funnet" }) };
  }
  const status = svar.__status || (opts.method === "POST" ? 201 : 200);
  const kropp = svar.__kropp !== undefined ? svar.__kropp : svar;
  return { ok: status < 400, status, json: async () => kropp };
};

function ctx(scopes = ["decisions:read", "plan:opprett"]) {
  return { sprak: "nb", scopes, tenant: "acme", paaUautorisert: () => {} };
}

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

const PLAN = {
  plan_id: "11111111-2222-4333-8444-555555555555",
  bestillingstype: "kontroll.wcag.nettsted",
  parametre: { hostname: "kunde.example", sti: "/" },
  rytme: "ukentlig", ukedag: 2, manedsdag: null, time_lokal: 8,
  tidssone: "Europe/Oslo", status: "aktiv", pause_aarsak: null,
  opprettet: "2026-08-19T00:00:00+00:00",
};

test("planskjema: fieldset/legend, rytme-radioer, hidden-veksling, axe rent",
     async () => {
  KALL = []; SVAR = { "/v1/plan": { planer: [] } };
  const h = nyHoved();
  visPlan(h, ctx());
  await vent(() => h.querySelector("form"));
  const form = h.querySelector("form");
  // To grupper med legend (§9): mål og rytme.
  const legender = [...form.querySelectorAll("fieldset > legend")]
    .map((l) => l.textContent);
  assert.ok(legender.includes(t("ui.plan.gruppe.maal")));
  assert.ok(legender.includes(t("ui.plan.gruppe.rytme")));
  // Rytme er RADIOKNAPPER med label — aldri egendefinerte klikkbokser.
  const radioer = form.querySelectorAll('input[type="radio"][name="plan-rytme"]');
  assert.equal(radioer.length, 3);
  for (const r of radioer) {
    assert.ok(form.querySelector(`label[for="${r.id}"]`),
      `radio ${r.value} mangler label`);
  }
  // Daglig er default: ukedag og månedsdag er `hidden` — borte for ALLE.
  const ukedag = form.querySelector("#plan-ukedag").closest("div");
  const manedsdag = form.querySelector("#plan-manedsdag").closest("div");
  assert.equal(ukedag.hidden, true);
  assert.equal(manedsdag.hidden, true);
  // Velg ukentlig: ukedag inn, månedsdag fortsatt borte.
  const ukentlig = form.querySelector('input[value="ukentlig"]');
  ukentlig.checked = true;
  ukentlig.dispatchEvent(new Event("change", { bubbles: true }));
  assert.equal(ukedag.hidden, false);
  assert.equal(manedsdag.hidden, true);
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("planskjema: valideringsfeil får aria-invalid og fokus på FØRSTE feil",
     async () => {
  KALL = []; SVAR = { "/v1/plan": { planer: [] } };
  const h = nyHoved();
  visPlan(h, ctx());
  await vent(() => h.querySelector("form"));
  const form = h.querySelector("form");
  const host = form.querySelector("#plan-hostname");
  const sti = form.querySelector("#plan-sti");
  host.value = "ikke gyldig!";
  sti.value = "uten-skraastrek";
  form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  await vent(() => host.getAttribute("aria-invalid") === "true");
  assert.equal(host.getAttribute("aria-invalid"), "true");
  assert.equal(sti.getAttribute("aria-invalid"), "true");
  // Feilteksten er KNYTTET, ikke bare i nærheten.
  const feilId = host.getAttribute("aria-errormessage");
  assert.ok(feilId);
  assert.equal(document.getElementById(feilId).textContent,
    t("ui.plan.feil.hostname"));
  // Fokus til FØRSTE feil (§9).
  assert.equal(document.activeElement, host);
  // Ingenting gikk på tråden.
  assert.ok(!KALL.some((k) => k.metode === "POST"));
});

test("planskjema: gyldig ukentlig plan POSTes med ukedag og full kropp",
     async () => {
  KALL = [];
  SVAR = (sti, opts) => {
    if (sti === "/v1/plan" && (opts.method || "GET") === "POST") {
      return { plan_id: PLAN.plan_id, status: "utkast" };
    }
    return { planer: [] };
  };
  const h = nyHoved();
  visPlan(h, ctx());
  await vent(() => h.querySelector("form"));
  const form = h.querySelector("form");
  form.querySelector("#plan-hostname").value = "Kunde.Example";
  form.querySelector("#plan-sti").value = "/";
  const ukentlig = form.querySelector('input[value="ukentlig"]');
  ukentlig.checked = true;
  ukentlig.dispatchEvent(new Event("change", { bubbles: true }));
  form.querySelector("#plan-ukedag").value = "3";
  form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  await vent(() => KALL.some((k) => k.metode === "POST"));
  const post = KALL.find((k) => k.metode === "POST");
  assert.equal(post.sti, "/v1/plan");
  assert.deepEqual(post.kropp, {
    bestillingstype: "kontroll.wcag.nettsted",
    hostname: "kunde.example", sti: "/", kravsett: "wcag21_aa",
    omfang: "enkeltside", maks_sider: 1,
    rytme: "ukentlig", time_lokal: 8, tidssone: "Europe/Oslo", ukedag: 3,
  });
  // Idempotency-Key er med: uten den er opprettelsen den ene skriveruten
  // uten gjenspill, og et tapt svar gir plan nummer to.
  assert.ok(post.headers["Idempotency-Key"]);
  // Utfallet annonsert (role=alert-regionen får teksten).
  await vent(() => document.querySelector('[role="alert"]'));
  assert.equal(document.querySelector('[role="alert"]').textContent,
    t("ui.plan.opprettet_alert"));
});

test("planskjema: operasjonsnøkkelen er stabil over en retry, fersk ved endring",
     async () => {
  // Codex P1: mister vi svaret på en opprettelse serveren ALT har
  // committet, må neste klikk bære SAMME nøkkel — da gjenspiller
  // serveren planen i stedet for å lage nummer to. Endrer brukeren
  // skjemaet, er det en annen plan og skal ha en fersk nøkkel.
  KALL = [];
  let feilNeste = true;
  SVAR = (sti, opts) => {
    if (sti === "/v1/plan" && (opts.method || "GET") === "POST") {
      if (feilNeste) return { __status: 503, __kropp: { feil: "db_utilgjengelig" } };
      return { plan_id: PLAN.plan_id, status: "utkast" };
    }
    return { planer: [] };
  };
  const h = nyHoved();
  visPlan(h, ctx());
  await vent(() => h.querySelector("form"));
  const form = h.querySelector("form");
  form.querySelector("#plan-hostname").value = "retry.example";
  form.querySelector("#plan-sti").value = "/";
  const send = () => form.dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }));
  send();
  await vent(() => KALL.filter((k) => k.metode === "POST").length === 1);
  feilNeste = false;
  send();
  await vent(() => KALL.filter((k) => k.metode === "POST").length === 2);
  const poster = KALL.filter((k) => k.metode === "POST");
  assert.equal(poster[0].headers["Idempotency-Key"],
    poster[1].headers["Idempotency-Key"],
    "retry av SAMME plan byttet nøkkel — serveren ville laget en dublett");
  // En ny plan (annen kropp) får en fersk nøkkel.
  form.querySelector("#plan-hostname").value = "annen.example";
  form.querySelector("#plan-sti").value = "/";
  send();
  await vent(() => KALL.filter((k) => k.metode === "POST").length === 3);
  const tredje = KALL.filter((k) => k.metode === "POST")[2];
  assert.notEqual(tredje.headers["Idempotency-Key"],
    poster[1].headers["Idempotency-Key"]);
});

test("planliste: caption/scope, pausegrunnen SYNLIG, handling annonsert i alert",
     async () => {
  const pauset = { ...PLAN, plan_id: "22222222-2222-4333-8444-555555555555",
    status: "pauset", pause_aarsak: "menneskelig_avvis" };
  KALL = [];
  SVAR = (sti, opts) => {
    if ((opts.method || "GET") === "POST") return { plan_id: pauset.plan_id };
    return { planer: [pauset] };
  };
  const h = nyHoved();
  visPlan(h, ctx());
  await vent(() => h.querySelector(".planliste table"));
  const tabell = h.querySelector(".planliste table");
  assert.ok(tabell.querySelector("caption"));
  for (const th of tabell.querySelectorAll("thead th")) {
    assert.equal(th.getAttribute("scope"), "col");
  }
  // Pausegrunnen leses som TEKST i statuskolonnen (§9) — aldri kun farge.
  assert.ok(tabell.textContent.includes(t("ui.plan.status.pauset")));
  assert.ok(tabell.textContent.includes(t("ui.plan.pause.menneskelig_avvis")));
  // Pauset plan tilbyr gjenoppta; klikket annonseres i role="alert".
  const knapper = [...tabell.querySelectorAll("button")];
  const gjenoppta = knapper.find(
    (k) => k.textContent === t("ui.plan.gjenoppta"));
  assert.ok(gjenoppta, "gjenoppta-knappen finnes for pauset plan");
  gjenoppta.click();
  await vent(() => KALL.some((k) => k.metode === "POST"));
  const post = KALL.find((k) => k.metode === "POST");
  assert.equal(post.sti, `/v1/plan/${pauset.plan_id}/gjenoppta`);
  await vent(() => document.querySelector('[role="alert"]')
    ?.textContent === t("ui.plan.alert.gjenoppta"));
  assert.equal(document.querySelector('[role="alert"]').textContent,
    t("ui.plan.alert.gjenoppta"));
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("planliste: uten plan:opprett finnes verken skjema eller mutasjonsknapper",
     async () => {
  KALL = []; SVAR = { "/v1/plan": { planer: [{ ...PLAN, status: "utkast" }] } };
  const h = nyHoved();
  visPlan(h, ctx(["decisions:read"]));
  await vent(() => h.querySelector(".planliste table"));
  assert.ok(!h.querySelector("form"), "skjemaet skal ikke rendres");
  const tekster = [...h.querySelectorAll("button")].map((k) => k.textContent);
  assert.ok(!tekster.includes(t("ui.plan.aktiver")));
  assert.ok(!tekster.includes(t("ui.plan.stans")));
  // Lesehandlingen står: historikk er decisions:read sitt domene.
  assert.ok(tekster.includes(t("ui.plan.vis_historikk")));
});

test("historikk: tabell med caption, utfall som tekst — aldri kun farge",
     async () => {
  KALL = [];
  SVAR = {
    "/v1/plan": { planer: [PLAN] },
    [`/v1/plan/${PLAN.plan_id}/historikk`]: {
      tick: [{ vindu_start: "2026-08-18T06:00:00+00:00", utfall: "tillat",
               oppdrag_id: 42, detalj: {},
               registrert: "2026-08-18T06:03:00+00:00" },
             { vindu_start: "2026-08-11T06:00:00+00:00",
               utfall: "hoppet_over", oppdrag_id: null, detalj: {},
               registrert: "2026-08-18T06:03:00+00:00" }],
      hendelser: [],
    },
  };
  const h = nyHoved();
  visPlan(h, ctx());
  await vent(() => h.querySelector(".planliste table"));
  const vis = [...h.querySelectorAll("button")]
    .find((k) => k.textContent === t("ui.plan.vis_historikk"));
  vis.click();
  await vent(() => h.querySelector(".planhistorikk table"));
  const tabell = h.querySelector(".planhistorikk table");
  assert.ok(tabell.querySelector("caption").textContent
    .includes("kunde.example"));
  assert.ok(tabell.textContent.includes(t("ui.plan.utfall.tillat")));
  assert.ok(tabell.textContent.includes(t("ui.plan.utfall.hoppet_over")));
  assert.ok(tabell.textContent.includes("#42"));
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("historikk: manuelt avvist oppdrag vises som avvist, ikke som bestilt",
     async () => {
  // Codex P2: ticket er urørt evidens (`tillat` — det ER hva motoren
  // svarte), men et oppdrag som SIDEN ble avvist av et menneske sto
  // fortsatt som «Bestilt» i flaten. `vist_utfall` er hva som gjelder nå.
  KALL = [];
  SVAR = {
    "/v1/plan": { planer: [PLAN] },
    [`/v1/plan/${PLAN.plan_id}/historikk`]: {
      tick: [{ vindu_start: "2026-08-18T06:00:00+00:00", utfall: "tillat",
               vist_utfall: "avvist_av_menneske", oppdrag_id: 42, detalj: {},
               registrert: "2026-08-18T06:03:00+00:00" }],
      hendelser: [],
    },
  };
  const h = nyHoved();
  visPlan(h, ctx());
  await vent(() => h.querySelector(".planliste table"));
  [...h.querySelectorAll("button")]
    .find((k) => k.textContent === t("ui.plan.vis_historikk")).click();
  await vent(() => h.querySelector(".planhistorikk table"));
  const tabell = h.querySelector(".planhistorikk table");
  assert.ok(tabell.textContent.includes(
    t("ui.plan.utfall.avvist_av_menneske")));
  assert.ok(!tabell.textContent.includes(t("ui.plan.utfall.tillat")));
});

test("planliste: tom liste er en tilstand, ikke en tom tabell", async () => {
  KALL = []; SVAR = { "/v1/plan": { planer: [] } };
  const h = nyHoved();
  visPlan(h, ctx(["decisions:read"]));
  await vent(() => h.textContent.includes(t("ui.plan.tom_tittel")));
  assert.ok(!h.querySelector(".planliste table"));
});
