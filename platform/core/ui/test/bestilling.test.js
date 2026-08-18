// Bestillingsflaten (038 §7) — akseptansekriteriene, målt: axe rent,
// valideringsfeil med aria-invalid/aria-errormessage og fokus på FØRSTE
// feil, utfall annonsert i role="alert" med STOPP-årsaken i teksten,
// ventetilstand med aria-busy + tekst, idempotensnøkkel stabil over retry
// og ny etter innholdsendring.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visBestilling } from "../static/js/flater/bestilling.js";

settI18nForTest(NB, "nb");

let KALL;
let SVAR;
globalThis.fetch = async (url, opts = {}) => {
  KALL.push({ url: url.split("?")[0], metode: opts.method || "GET",
    headers: opts.headers || {},
    kropp: opts.body ? JSON.parse(opts.body) : null });
  const svar = typeof SVAR === "function" ? SVAR() : SVAR;
  if (typeof svar === "number") {
    return { ok: false, status: svar,
      json: async () => ({ feil: "bestilling_hostname_uverifisert" }) };
  }
  return { ok: true, status: 200, json: async () => svar };
};

function ctx() {
  return { sprak: "nb", scopes: ["bestilling:opprett"], tenant: "acme",
    paaUautorisert: () => {} };
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

function fyll(h, { hostname = "kunde.example", sti = "", maks = "1" } = {}) {
  const hn = h.querySelector("#bf-hostname");
  hn.value = hostname; hn.dispatchEvent(new window.Event("input"));
  const st = h.querySelector("#bf-sti");
  st.value = sti; st.dispatchEvent(new window.Event("input"));
  const mk = h.querySelector("#bf-maks_sider");
  mk.value = maks; mk.dispatchEvent(new window.Event("input"));
}

function send(h) {
  h.querySelector("form").dispatchEvent(
    new window.Event("submit", { cancelable: true }));
}

function velgOmfang(h, o) {
  const valgt = h.querySelector(`#bf-omfang-${o}`);
  for (const r of h.querySelectorAll('input[name="bf-omfang"]')) {
    r.checked = r === valgt;
  }
  valgt.dispatchEvent(new window.Event("change"));
}

test("Bestilling: ekte skjema-semantikk og axe rent", async () => {
  KALL = []; SVAR = {};
  const h = nyHoved();
  visBestilling(h, ctx());
  const form = h.querySelector("form");
  assert.ok(form, "ekte <form>");
  assert.ok(form.querySelectorAll("fieldset legend").length >= 3);
  for (const inp of form.querySelectorAll("input")) {
    if (inp.type === "radio") continue;
    assert.ok(h.querySelector(`label[for="${inp.id}"]`),
      `label mangler for ${inp.id}`);
  }
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Bestilling: valideringsfeil → aria-invalid + fokus på første feil, intet kall", async () => {
  KALL = []; SVAR = {};
  const h = nyHoved();
  visBestilling(h, ctx());
  fyll(h, { hostname: "Ugyldig..host", sti: "/a/../b" });
  send(h);
  const hn = h.querySelector("#bf-hostname");
  assert.equal(hn.getAttribute("aria-invalid"), "true");
  const feilId = hn.getAttribute("aria-errormessage");
  assert.ok(feilId, "aria-errormessage satt");
  assert.equal(document.getElementById(feilId).textContent,
    t("ui.bestilling.feil.hostname"));
  // begge feltene er ugyldige — fokus står på det FØRSTE
  assert.equal(document.activeElement, hn);
  assert.equal(h.querySelector("#bf-sti").getAttribute("aria-invalid"),
    "true");
  assert.equal(KALL.length, 0, "ugyldig skjema skal aldri nå serveren");
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Bestilling: TILLAT annonseres i role=alert med oppdragsnummer", async () => {
  KALL = [];
  SVAR = { beslutning: "tillat", oppdrag_id: 42, request_id: "r" };
  const h = nyHoved();
  visBestilling(h, ctx());
  fyll(h);
  send(h);
  await vent(() => h.querySelector('[role="alert"]').textContent);
  const alert = h.querySelector('[role="alert"]');
  assert.ok(alert.textContent.includes(t("ui.bestilling.utfall.tillat")));
  assert.ok(alert.textContent.includes("42"));
  assert.equal(KALL[0].metode, "POST");
  assert.equal(KALL[0].url, "/v1/bestilling");
  assert.ok(KALL[0].headers["Idempotency-Key"], "idempotensnøkkel sendes");
  assert.equal(KALL[0].kropp.mal_url, undefined,
    "klienten sender ALDRI en URL");
});

test("Bestilling: STOPP-årsaken står i alert-teksten", async () => {
  KALL = [];
  SVAR = { beslutning: "stopp", oppdrag_id: null,
    begrunnelse: ["rolle_ikke_tillatt"], request_id: "r" };
  const h = nyHoved();
  visBestilling(h, ctx());
  fyll(h);
  send(h);
  await vent(() => h.querySelector('[role="alert"]').textContent);
  const alert = h.querySelector('[role="alert"]');
  assert.ok(alert.textContent.includes(t("ui.bestilling.utfall.stopp")));
  assert.ok(alert.textContent.includes(t("kode.rolle_ikke_tillatt")),
    "årsaken skal leses opp, ikke bare vises");
});

test("Bestilling: retry gjenbruker nøkkelen; endring gir ny", async () => {
  KALL = [];
  SVAR = { beslutning: "tillat", oppdrag_id: 1, request_id: "r" };
  const h = nyHoved();
  visBestilling(h, ctx());
  fyll(h);
  send(h);
  await vent(() => KALL.length === 1);
  send(h);
  await vent(() => KALL.length === 2);
  assert.equal(KALL[0].headers["Idempotency-Key"],
    KALL[1].headers["Idempotency-Key"], "uendret innhold → samme nøkkel");
  fyll(h, { maks: "2" });
  send(h);
  await vent(() => KALL.length === 3);
  assert.notEqual(KALL[1].headers["Idempotency-Key"],
    KALL[2].headers["Idempotency-Key"], "endret innhold → ny nøkkel");
});

test("Bestilling: uverifisert hostname → navngitt feil i alert", async () => {
  KALL = []; SVAR = 403;
  const h = nyHoved();
  visBestilling(h, ctx());
  fyll(h, { hostname: "fremmed.example" });
  send(h);
  await vent(() => h.querySelector('[role="alert"]').textContent);
  assert.ok(h.querySelector('[role="alert"]').textContent
    .includes(t("ui.bestilling.feil.uverifisert")));
});

test("Bestilling: utfallet bærer sitt eget mål, også når feltene endres", async () => {
  // Codex P2: bare knappen var låst mens svaret var underveis. Endret
  // brukeren målet i mellomtiden, ble oppdragsnummeret for nettsted A vist
  // under nettsted B — uten at noe i meldingen sa hvilket den gjaldt.
  KALL = [];
  let slipp;
  SVAR = () => new Promise((r) => {
    slipp = () => r({ beslutning: "tillat", oppdrag_id: 99, request_id: "r" });
  });
  const h = nyHoved();
  visBestilling(h, ctx());
  fyll(h, { hostname: "a.example", sti: "/en" });
  send(h);
  await vent(() => KALL.length === 1);
  const hn = h.querySelector("#bf-hostname");
  assert.equal(hn.readOnly, true, "målfeltene fryses mens svaret er underveis");
  assert.equal(h.querySelector("#bf-sti").readOnly, true);
  // Radioknappene kan ikke låses uten `disabled` — derfor må utfallet
  // uansett navngi målet sitt. Her simuleres endringen direkte.
  hn.readOnly = false;
  hn.value = "b.example"; hn.dispatchEvent(new window.Event("input"));
  slipp();
  await vent(() => h.querySelector('[role="alert"]').textContent);
  const tekst = h.querySelector('[role="alert"]').textContent;
  assert.ok(tekst.includes("https://a.example/en"), tekst);
  assert.ok(!tekst.includes("b.example"), `målet ble tilskrevet feil: ${tekst}`);
  assert.ok(tekst.includes("99"), tekst);
  assert.ok(tekst.includes(t("ui.bestilling.omfang.enkeltside")), tekst);
  assert.equal(h.querySelector("#bf-sti").readOnly, false, "frysen slippes");
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Bestilling: «hele nettstedet» sender nettstedets sidetall, ikke 1", async () => {
  // Codex P2: feltet sto på 1 og ble ALLTID sendt, mens serverens
  // 50-default bare gjelder når `maks_sider` utelates. Et omfang som
  // presenterer seg som hele nettstedet kontrollerte da forsiden alene.
  KALL = []; SVAR = { beslutning: "tillat", oppdrag_id: 7, request_id: "r" };
  const h = nyHoved();
  visBestilling(h, ctx());
  fyll(h);
  velgOmfang(h, "nettsted");
  const mk = h.querySelector("#bf-maks_sider");
  assert.equal(mk.value, "50", "tallet skal følge omfanget, og SYNLIG");
  send(h);
  await vent(() => KALL.length === 1);
  assert.equal(KALL[0].kropp.omfang, "nettsted");
  assert.equal(KALL[0].kropp.maks_sider, 50);

  // Brukerens eget tall er brukerens: et omfangsbytte overskriver det ikke.
  mk.value = "7"; mk.dispatchEvent(new window.Event("input"));
  velgOmfang(h, "enkeltside");
  assert.equal(mk.value, "7");
  send(h);
  await vent(() => KALL.length === 2);
  assert.equal(KALL[1].kropp.maks_sider, 7);
});
