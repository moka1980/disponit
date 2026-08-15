import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visInnlogging } from "../static/js/innlogging.js";
import { visKundeadmin } from "../static/js/flater/kundeadmin.js";
import { visAdmin } from "../static/js/flater/admin.js";
import { TILBUD, erTilgjengelig } from "../static/js/plattformdata.js";
import { siteTilbudMerke } from "../static/js/sitekomponenter.js";

const HER = dirname(fileURLToPath(import.meta.url));

// Tenantrader som testdata, ikke som produksjonsinnhold: de lever HER, i en
// testfil som aldri serveres, og ikke i klientpakken eller locale-settet.
// `plan` er en KODE fra et lukket vokabular — etiketten slås opp i
// locale-settet — mens `neste` er fritekst serveren allerede har oversatt.
const RADER = [
  { id: "alfa", navn: "Alfa", plan: "pilot", moduler: [1, 2, 37],
    neste: "M-38 når kapasitet er grønt." },
  { id: "beta", navn: "Beta", plan: "pilot", moduler: [1, 2],
    neste: "M-37 etter signerte unntaksrutiner." },
  { id: "gamma", navn: "Gamma", plan: "internt", moduler: [1, 2, 37, 38],
    neste: "Kunde null for utrulling." },
];

const EN = JSON.parse(readFileSync(
  join(HER, "..", "..", "..", "..", "locales", "en.json"), "utf-8"));

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
  // Forsiden selger TILBUDET, ikke byggestatusen: kundevendte navn og en
  // tilgjengelighetsbrikke, ikke modulnumre og «0/45 i drift».
  assert.ok(app.textContent.includes(t("site.tilbud_tittel")));
  assert.ok(app.textContent.includes(t("site.tilbud.fullmakt.navn")));
  assert.ok(app.textContent.includes(t("site.problem_tittel")));
  assert.ok(app.textContent.includes(t("site.svar_tittel")));
  assert.ok(app.textContent.includes(t("site.arbeidsflyt_tittel")));
  // Driftsvokabularet skal IKKE nå en anonym besøkende.
  assert.ok(!app.textContent.includes(t("site.modul.m37.navn")),
    "internt modulnavn på den publike forsiden");
  assert.ok(!/\b0\/45\b/.test(app.textContent),
    "byggeregnskap på den publike forsiden");
  const retur = [...app.querySelectorAll('input[name="retursti"]')]
    .map((n) => n.getAttribute("value"));
  assert.deepEqual(retur, ["/?visning=kundeadmin", "/?visning=admin"]);
  assert.equal(document.documentElement.getAttribute("data-visning"), "landing");
  const b = await alvorligeBrudd(app);
  assert.equal(b.length, 0, beskrivBrudd(b));
});

test("Landing: tilgjengelighetsbrikkene har CSS som faktisk skiller dem", async () => {
  // Codex P3: brikkene bar `merke-i_drift`/`merke-planlagt`, klasser som ikke
  // finnes i noen stilfil. Begge rendret da som en umerket `.merke`, og
  // «Tilgjengelig» så nøyaktig ut som «Kommer». Et merke som bare skiller i
  // tekst er ikke et merke. Testen krever derfor to ting av HVER brikke:
  // klassene må ha en definisjon i stilkilden, og de to tilstandene må ha
  // forskjellig klasse.
  const app = nyttAppBrett();
  await visInnlogging();
  await vent(() => app.querySelectorAll(".site-mini-card .site-badge").length > 0);
  const css = ["base.css", "komponenter.css"]
    .map((f) => readFileSync(join(HER, "..", "static", "css", f), "utf-8"))
    .join("\n");
  const definert = new Set([...css.matchAll(/\.([A-Za-z_][-\w]*)/g)]
    .map((m) => m[1]));

  const brikker = [...app.querySelectorAll(".site-mini-card .site-badge")];
  assert.equal(brikker.length, TILBUD.length,
    "hvert tilbudspunkt skal ha én tilgjengelighetsbrikke");
  for (const brikke of brikker) {
    for (const klasse of brikke.className.split(/\s+/).filter(Boolean)) {
      assert.ok(definert.has(klasse),
        `brikka bruker .${klasse}, som ingen stilfil definerer — ` +
        `da rendres tilstanden umerket`);
    }
  }
  // M-1 er `klargjort`, altså ikke i drift: alle fire sier «Kommer» i dag.
  // Testen skal likevel holde den dagen en modul går i drift, så den måler
  // klassen mot `erTilgjengelig` per punkt i stedet for å anta fordelingen.
  const forventet = TILBUD.map((post) =>
    erTilgjengelig(post.id) ? "site-badge ok" : "site-badge plan");
  assert.deepEqual(brikker.map((b) => b.className), forventet);
  assert.notEqual(siteTilbudMerke(true).className,
    siteTilbudMerke(false).className,
    "tilgjengelig og kommer deler klasse — da skiller ingenting dem visuelt");
});

test("Kundeadmin: modulstatus og policyhandling rendres uten alvorlige brudd", async () => {
  const h = nyHoved();
  visKundeadmin(h, ctx({ tenant: "Alfa", moduler: [1, 2, 37],
    scopes: ["decisions:read", "policy:read", "policy:write",
      "policy:activate"] }));
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
  // Bjørkli er tildelt M-1 og M-2. Da skal M-37 og M-38 IKKE stå på flaten.
  // Ingen av de to er i drift (manifestene sier `ikke_i_drift`), så «aktive
  // moduler» er 0 og «under arbeid» er 2 — kundens to, ikke katalogens.
  const h = nyHoved();
  visKundeadmin(h, ctx({ tenant: "Beta", moduler: [1, 2] }));
  assert.ok(h.textContent.includes(t("site.modul.m1.navn")));
  assert.ok(h.textContent.includes(t("site.modul.m2.navn")));
  assert.ok(!h.textContent.includes(t("site.modul.m37.navn")),
    "M-37 vises for en tenant som ikke har den");
  assert.ok(!h.textContent.includes(t("site.modul.m38.navn")),
    "M-38 vises for en tenant som ikke har den");
  const kpi = [...h.querySelectorAll(".site-kpi strong")].map((n) => n.textContent);
  assert.equal(kpi[0], "0");
  assert.equal(kpi[1], "2");
});

test("Kundeadmin: ukjent tenant sier «vet ikke», viser ikke katalogen", async () => {
  const h = nyHoved();
  visKundeadmin(h, ctx({ tenant: "Ukjent" }));
  assert.ok(h.textContent.includes(t("ui.kundeadmin.moduler_ukjent")));
  assert.ok(!h.textContent.includes(t("site.modul.m1.navn")),
    "plattformkatalogen vises for en ukjent tenant");
  const kpi = [...h.querySelectorAll(".site-kpi strong")].map((n) => n.textContent);
  assert.deepEqual(kpi.slice(0, 2), ["0", "0"]);
});

test("Kundeadmin: leser får lesevisning av policy, ikke aktiveringsflaten", () => {
  // Kundeflaten er åpen for hele kundeøkten, men `leser` skal ikke tilbys
  // policyadministrasjon: den flaten nekter ruteren dem, og knappene der gir
  // 403. Lesevegen til `#/policy` skal stå igjen.
  const lese = nyHoved();
  visKundeadmin(lese, ctx({ tenant: "Alfa", moduler: [1, 2, 37],
    scopes: ["decisions:read", "exceptions:read", "policy:read"] }));
  assert.equal(lese.querySelector('a[href="#/policyadmin"]'), null,
    "aktiveringsflate tilbudt leser");
  assert.ok(lese.querySelector('a[href="#/policy"]'), "lesevei til policy borte");
  assert.ok(lese.textContent.includes(t("ui.kundeadmin.policy_lesing_tittel")));

  const forvalter = nyHoved();
  visKundeadmin(forvalter, ctx({ tenant: "Alfa", moduler: [1, 2, 37],
    scopes: ["policy:write"] }));
  assert.ok(forvalter.querySelector('a[href="#/policyadmin"]'));
});

test("Kundeadmin: godkjenner tilbys ikke policylesing den ikke har", () => {
  // Kanonisk `godkjenner` i `autorisasjon.py` har hverken forvaltnings- eller
  // LESEscope på policy. Fallbacken lovet den likevel `#/policy`, en flate
  // ruteren nekter, bak et endepunkt som svarer 403. Nå står forklaringen der
  // i stedet — og ingen lenke.
  const h = nyHoved();
  visKundeadmin(h, ctx({ tenant: "Alfa", moduler: [1, 2],
    scopes: ["decisions:read", "exceptions:read", "exceptions:approve",
      "exceptions:reject", "exceptions:escalate"] }));
  assert.equal(h.querySelector('a[href="#/policy"]'), null,
    "lesevei til policy tilbudt uten policy:read");
  assert.equal(h.querySelector('a[href="#/policyadmin"]'), null);
  assert.ok(h.textContent.includes(t("ui.kundeadmin.policy_ingen_tittel")));
  // Unntakskøen er derimot nettopp det rollen KAN, og skal stå igjen.
  assert.ok(h.querySelector('a[href="#/unntak"]'), "unntakssnarvei borte");
  assert.ok(h.querySelector('a[href="#/oversikt"]'));
});

test("Kundeadmin: policyforvalter tilbys ikke unntakskøen den ikke kan lese", () => {
  // Speilbildet: `policyforvalter` mangler `exceptions:read`.
  const h = nyHoved();
  visKundeadmin(h, ctx({ tenant: "Alfa", moduler: [1, 2],
    scopes: ["decisions:read", "policy:read", "policy:write",
      "policy:activate"] }));
  assert.equal(h.querySelector('a[href="#/unntak"]'), null,
    "unntakssnarvei tilbudt uten exceptions:read");
  assert.ok(h.querySelector('a[href="#/policyadmin"]'));
});

test("Admin: tenanttabell og faser lokaliseres uten alvorlige brudd", async () => {
  const h = nyHoved();
  visAdmin(h, ctx({ scopes: ["platform:admin"], tenanter: RADER }));
  assert.ok(h.textContent.includes(t("ui.admin.tittel")));
  assert.ok(h.textContent.includes(t("site.fase.fundament")));
  assert.ok(h.textContent.includes("Alfa"));
  assert.ok(h.textContent.includes(t("site.plan.pilot")));
  assert.ok(h.textContent.includes(t("ui.admin.kontrollplan_tittel")));
  const b = await alvorligeBrudd(h, { fragment: true });
  assert.equal(b.length, 0, beskrivBrudd(b));
});

test("Admin: plantildelingen følger valgt språk, ikke serverens morsmål", () => {
  // Før dette sendte serveren etiketten «Pilot»/«Internt» ferdig skrevet, og
  // flaten rendret den verbatim: den engelske tabellen viste norsk. Nå er
  // `plan` en KODE, og etiketten slås opp i locale-settet der den hører hjemme.
  try {
    settI18nForTest(EN, "en");
    const h = nyHoved();
    visAdmin(h, ctx({ scopes: ["platform:admin"], tenanter: RADER }));
    const planer = [...h.querySelectorAll("tbody tr")]
      .map((r) => r.children[1].textContent);
    assert.deepEqual(planer, [EN["site.plan.pilot"], EN["site.plan.pilot"],
      EN["site.plan.internt"]]);
    assert.ok(!planer.includes("internt"), "plankoden lekket til tabellen");
    assert.ok(!planer.includes(NB["site.plan.internt"]),
      "norsk planetikett vist på engelsk flate");
  } finally {
    settI18nForTest(NB, "nb");
  }
});

test("Admin: ukjent plankode gir koden, aldri en tom celle", () => {
  const h = nyHoved();
  visAdmin(h, ctx({ scopes: ["platform:admin"],
    tenanter: [{ id: "alfa", navn: "Alfa", plan: "fremtidig", moduler: [1],
      neste: "x" }] }));
  assert.equal(h.querySelector("tbody tr").children[1].textContent, "fremtidig");
});

test("Admin: tenanttabellen på tvers krever plattformdrift", () => {
  // `security:read` er en TENANTBUNDET ops-scope. En kundes sikkerhets-
  // ansvarlige skal se sin egen rad — ikke hver eneste andre kundes plan,
  // moduler og neste steg.
  const ops = nyHoved();
  visAdmin(ops, ctx({ tenant: "beta", scopes: ["security:read"],
    tenanter: RADER }));
  assert.ok(ops.textContent.includes("Beta"));
  assert.ok(!ops.textContent.includes("Alfa"),
    "annen tenant lekket til en tenantbundet økt");
  assert.ok(!ops.textContent.includes("Gamma"),
    "annen tenant lekket til en tenantbundet økt");
  assert.ok(ops.textContent.includes(t("ui.admin.tenanter_egen_note")));
  assert.equal(ops.querySelectorAll("tbody tr").length, 1);

  const drift = nyHoved();
  visAdmin(drift, ctx({ tenant: "beta", scopes: ["platform:admin"],
    tenanter: RADER }));
  assert.equal(drift.querySelectorAll("tbody tr").length, 3);
  assert.ok(drift.textContent.includes(t("ui.admin.tenanter_tittel")));
});

test("Admin: ukjent tenant får ingen tabell å gjette fra", () => {
  const h = nyHoved();
  visAdmin(h, ctx({ tenant: "ukjent", scopes: ["security:read"],
    tenanter: RADER }));
  assert.equal(h.querySelector("tbody"), null, "tabell vist uten kjent tenant");
  assert.ok(h.textContent.includes(t("ui.admin.tenanter_ukjent")));
  const kpi = [...h.querySelectorAll(".site-kpi strong")].map((n) => n.textContent);
  assert.equal(kpi[2], "0");
});

test("Admin: policyaktivering tilbys bare med policy-forvaltningsscope", () => {
  // `security:read` åpner admin-flaten, men rollene `admin`/`sikkerhet` har
  // bare `policy:read`: da skal aktiveringssnarveien være borte, og lesevegen
  // til policy stå igjen.
  const lese = nyHoved();
  visAdmin(lese, ctx({ scopes: ["security:read", "policy:read"] }));
  assert.equal(lese.querySelector('a[href="#/policyadmin"]'), null,
    "aktiveringssnarvei vist til leser");
  // Kundeflaten er derimot en basisrute nå — snarveien dit gjelder alle.
  assert.ok(lese.querySelector('a[href="#/kundeadmin"]'));
  assert.ok(lese.querySelector('a[href="#/policy"]'), "lesevei til policy borte");
  assert.ok(lese.textContent.includes(t("ui.admin.handling.policy_lesing")));

  const forvalter = nyHoved();
  visAdmin(forvalter, ctx({ scopes: ["security:read", "policy:activate"] }));
  assert.ok(forvalter.querySelector('a[href="#/policyadmin"]'));
  assert.ok(forvalter.querySelector('a[href="#/kundeadmin"]'));

  // En ren plattformdriftsøkt bærer ingen tenant-lokale lesescopes: da skal
  // hverken lesevegen til policy eller unntakskøen stå der som en 403 i vente.
  const drift = nyHoved();
  visAdmin(drift, ctx({ scopes: ["platform:admin"] }));
  assert.equal(drift.querySelector('a[href="#/policy"]'), null,
    "lesevei til policy tilbudt uten policy:read");
  assert.equal(drift.querySelector('a[href="#/unntak"]'), null,
    "unntakssnarvei tilbudt uten exceptions:read");
  assert.ok(drift.querySelector('a[href="#/kundeadmin"]'));
});
