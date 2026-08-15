import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visInnlogging } from "../static/js/innlogging.js";
import { visKundeadmin } from "../static/js/flater/kundeadmin.js";
import { visAdmin } from "../static/js/flater/admin.js";

settI18nForTest(NB, "nb");

globalThis.fetch = async (url) => {
  const sti = url.split("?")[0];
  if (sti === "/ui/oppsett.json") {
    return { ok: true, status: 200, json: async () => ({ provider_id: "google" }) };
  }
  return { ok: false, status: 404, json: async () => ({ feil: "x" }) };
};

function ctx(overstyr = {}) {
  return { sprak: "nb", scopes: [], tenant: "acme", paaUautorisert: () => {},
    ...overstyr };
}

async function vent(pred, n = 60) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}

function nyttAppBrett() {
  const brett = nyttBrett();
  const app = document.createElement("div");
  app.id = "app";
  app.setAttribute("aria-busy", "true");
  brett.append(app);
  return app;
}

function nyHoved() {
  const brett = nyttBrett();
  const m = document.createElement("main");
  m.id = "hovedinnhold";
  m.tabIndex = -1;
  brett.append(m);
  return m;
}

test("Landing: rendrer ekte plattformflate med retursti per innlogging", async () => {
  const app = nyttAppBrett();
  await visInnlogging();
  await vent(() => app.querySelectorAll("form").length === 2);
  assert.ok(app.textContent.includes(t("site.hero.tittel")));
  assert.ok(app.textContent.includes(t("site.modul.m37.navn")));
  assert.ok(app.textContent.includes(t("site.klarhet_tittel")));
  assert.ok(app.textContent.includes(t("site.arbeidsflyt_tittel")));
  const retur = [...app.querySelectorAll('input[name="retursti"]')]
    .map((n) => n.getAttribute("value"));
  assert.deepEqual(retur, ["/?visning=kundeadmin", "/?visning=admin"]);
  assert.equal(document.documentElement.getAttribute("data-visning"), "landing");
  const b = await alvorligeBrudd(app);
  assert.equal(b.length, 0, beskrivBrudd(b));
});

test("Kundeadmin: modulstatus og policyhandling rendres uten alvorlige brudd", async () => {
  const h = nyHoved();
  visKundeadmin(h, ctx({ tenant: "Nordvik" }));
  assert.ok(h.textContent.includes(t("ui.kundeadmin.tittel")));
  assert.ok(h.textContent.includes(t("site.modul.m1.navn")));
  assert.ok(h.textContent.includes(t("ui.kundeadmin.plattform_tittel")));
  const policyLenke = h.querySelector('a[href="#/policyadmin"]');
  const oversiktLenke = h.querySelector('a[href="#/oversikt"]');
  assert.ok(policyLenke, "policylenke mangler");
  assert.ok(oversiktLenke, "oversiktlenke mangler");
  assert.equal(policyLenke.textContent, t("ui.kundeadmin.policy_handling"));
  assert.equal(oversiktLenke.textContent, t("ui.kundeadmin.handling.ga_til"));
  const b = await alvorligeBrudd(h, { fragment: true });
  assert.equal(b.length, 0, beskrivBrudd(b));
});

test("Kundeadmin: modulkort og KPI-er følger tenantens tildeling", async () => {
  // Bjørkli er tildelt M-1 og M-2. Da skal M-37 og M-38 IKKE stå på flaten,
  // og «aktive moduler» skal være 2 — ikke plattformens tre.
  const h = nyHoved();
  visKundeadmin(h, ctx({ tenant: "bjorkli" }));
  assert.ok(h.textContent.includes(t("site.modul.m1.navn")));
  assert.ok(h.textContent.includes(t("site.modul.m2.navn")));
  assert.ok(!h.textContent.includes(t("site.modul.m37.navn")),
    "M-37 vises for en tenant som ikke har den");
  assert.ok(!h.textContent.includes(t("site.modul.m38.navn")),
    "M-38 vises for en tenant som ikke har den");
  const kpi = [...h.querySelectorAll(".site-kpi strong")].map((n) => n.textContent);
  assert.equal(kpi[0], "2");
  assert.equal(kpi[1], "0");
});

test("Kundeadmin: ukjent tenant sier «vet ikke», viser ikke katalogen", async () => {
  const h = nyHoved();
  visKundeadmin(h, ctx({ tenant: "acme" }));
  assert.ok(h.textContent.includes(t("ui.kundeadmin.moduler_ukjent")));
  assert.ok(!h.textContent.includes(t("site.modul.m1.navn")),
    "plattformkatalogen vises for en ukjent tenant");
  const kpi = [...h.querySelectorAll(".site-kpi strong")].map((n) => n.textContent);
  assert.deepEqual(kpi.slice(0, 2), ["0", "0"]);
});

test("Admin: tenanttabell og faser lokaliseres uten alvorlige brudd", async () => {
  const h = nyHoved();
  visAdmin(h, ctx());
  assert.ok(h.textContent.includes(t("ui.admin.tittel")));
  assert.ok(h.textContent.includes(t("site.fase.fundament")));
  assert.ok(h.textContent.includes(t("site.tenant.nordvik.navn")));
  assert.ok(h.textContent.includes(t("site.plan.pilot")));
  assert.ok(h.textContent.includes(t("ui.admin.kontrollplan_tittel")));
  const b = await alvorligeBrudd(h, { fragment: true });
  assert.equal(b.length, 0, beskrivBrudd(b));
});

test("Admin: policyaktivering tilbys bare med policy-forvaltningsscope", () => {
  // `security:read` åpner admin-flaten, men rollene `admin`/`sikkerhet` har
  // bare `policy:read`: da skal aktiveringssnarveien være borte, og lesevegen
  // til policy stå igjen.
  const lese = nyHoved();
  visAdmin(lese, ctx({ scopes: ["security:read", "policy:read"] }));
  assert.equal(lese.querySelector('a[href="#/policyadmin"]'), null,
    "aktiveringssnarvei vist til leser");
  assert.equal(lese.querySelector('a[href="#/kundeadmin"]'), null,
    "kundeadmin-snarvei vist til leser");
  assert.ok(lese.querySelector('a[href="#/policy"]'), "lesevei til policy borte");
  assert.ok(lese.textContent.includes(t("ui.admin.handling.policy_lesing")));

  const forvalter = nyHoved();
  visAdmin(forvalter, ctx({ scopes: ["security:read", "policy:activate"] }));
  assert.ok(forvalter.querySelector('a[href="#/policyadmin"]'));
  assert.ok(forvalter.querySelector('a[href="#/kundeadmin"]'));
});
