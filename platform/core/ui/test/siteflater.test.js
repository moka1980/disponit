import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t, velgSprak } from "../static/js/i18n.js";
import { visInnlogging } from "../static/js/innlogging.js";
import { AppShell } from "../static/js/komponenter.js";
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

const LOCALER = { nb: NB, en: EN };

globalThis.fetch = async (url) => {
  const sti = url.split("?")[0];
  if (sti === "/ui/oppsett.json") {
    return { ok: true, status: 200, json: async () => ({ provider_id: "google" }) };
  }
  // Språkbyttet på forsiden går gjennom `hentI18n`, altså et ekte
  // locale-oppslag — serveres her fra de samme filene serveren serverer.
  const locale = sti.match(/^\/ui\/locale\/(nb|en)$/);
  if (locale) {
    return { ok: true, status: 200, json: async () => LOCALER[locale[1]] };
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

test("Landing: forsiden har hovednavigasjon og er ikke en lang katalog", async () => {
  window.history.replaceState({}, "", "/");
  const app = nyttAppBrett();
  await visInnlogging();
  await vent(() => app.querySelector(".site-hovednav"));
  assert.ok(app.textContent.includes(t("site.home.tittel")));
  // Forsiden selger TILBUDET, ikke byggestatusen: kundevendte navn og en
  // tilgjengelighetsbrikke, ikke modulnumre og «0/45 i drift».
  assert.ok(app.textContent.includes(t("site.tilbud_tittel")));
  assert.ok(app.textContent.includes(t("site.tilbud.fullmakt.navn")));
  assert.equal(app.querySelectorAll(".site-hovednav a").length, 5);
  assert.equal(app.querySelectorAll('.site-hovednav a[aria-current="page"]').length, 1);
  assert.equal(app.querySelector(".site-sok"), null,
    "katalogsøket konkurrerer fortsatt med forsidens primærhandling");
  assert.equal(app.querySelectorAll(".site-home-handlinger .primar").length, 1,
    "forsiden har ikke nøyaktig én visuelt primær handling i heroen");
  assert.equal(app.querySelectorAll(".site-kontrollflyt li").length, 4,
    "kontrollflyten viser ikke hele veien handling → policy → utfall → spor");
  assert.ok(!app.textContent.includes(t("site.katalog.m42.navn")),
    "modulkatalogen ligger fortsatt som en lang liste på forsiden");
  assert.equal(app.querySelectorAll('form[action="/v1/oidc/start"]').length, 0,
    "innloggingskortene ligger fortsatt på forsiden");
  // …men DRIFTSVOKABULARET skal ikke nå en anonym besøkende. Skillet er ikke
  // «modul» mot «ikke modul»: navnene ER tilbudet. Det som ikke hører hjemme
  // er de interne merkelappene — modulnumre og byggeregnskap.
  assert.ok(!/\bM-\d+\b/.test(app.textContent),
    "internt modulnummer på den publike forsiden");
  assert.ok(!/\b0\/45\b/.test(app.textContent),
    "byggeregnskap på den publike forsiden");
  assert.equal(document.documentElement.getAttribute("data-visning"), "landing");
  const b = await alvorligeBrudd(app);
  assert.equal(b.length, 0, beskrivBrudd(b));
});

test("Landing: tjenester har hele katalogen og søk filtrerer den", async () => {
  window.history.replaceState({}, "", "/?side=tjenester&q=svindel");
  const app = nyttAppBrett();
  await visInnlogging();
  assert.ok(app.textContent.includes(t("site.katalog.m42.navn")));
  assert.ok(!app.textContent.includes(t("site.katalog.m1.navn")));
  assert.ok(app.querySelector(".site-sok input[type=search]"),
    "katalogsøket er ikke bevart på tjenestesiden");
  assert.equal(app.querySelector('.site-hovednav a[aria-current="page"]').textContent,
    t("site.nav.tjenester"));
  const b = await alvorligeBrudd(app);
  assert.equal(b.length, 0, beskrivBrudd(b));
  window.history.replaceState({}, "", "/");
});

test("Landing: innlogging er en egen side med riktig retursti", async () => {
  window.history.replaceState({}, "", "/?side=innlogging");
  const app = nyttAppBrett();
  await visInnlogging();
  const retur = [...app.querySelectorAll('input[name="retursti"]')]
    .map((n) => n.getAttribute("value"));
  // Språket er med i retursti-en (Codex P2): innlogging er den siste
  // offentlige navigasjonen, og for den som ikke kan lagre valget er URL-en
  // det ENESTE som bærer det over OIDC-runden. `trygg_retursti` beholder
  // query-strengen på en lokal path-referanse, så leddet overlever turen.
  assert.deepEqual(retur,
    ["/?visning=kundeadmin&sprak=nb", "/?visning=admin&sprak=nb"]);
  window.history.replaceState({}, "", "/");
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
  await vent(() => app.querySelectorAll(".site-feature .site-badge").length > 0);
  const css = ["base.css", "komponenter.css"]
    .map((f) => readFileSync(join(HER, "..", "static", "css", f), "utf-8"))
    .join("\n");
  const definert = new Set([...css.matchAll(/\.([A-Za-z_][-\w]*)/g)]
    .map((m) => m[1]));

  const brikker = [...app.querySelectorAll(".site-feature .site-badge")];
  assert.equal(brikker.length, TILBUD.length,
    "hvert tilbudspunkt skal ha én tilgjengelighetsbrikke");
  for (const brikke of brikker) {
    for (const klasse of brikke.className.split(/\s+/).filter(Boolean)) {
      assert.ok(definert.has(klasse),
        `brikka bruker .${klasse}, som ingen stilfil definerer — ` +
        `da rendres tilstanden umerket`);
    }
  }
  // Alle fire sier «Kommer» i dag: M-1 er `klargjort` (den kjører, men på
  // staging-serveren), og løftet krever i tillegg at verten står i
  // produksjonsmodus — begge ledd, som `erTilgjengeligFor` sier.
  // Testen skal likevel holde den dagen en modul går i drift, så den måler
  // klassen mot `erTilgjengelig` per punkt i stedet for å anta fordelingen.
  const forventet = TILBUD.map((post) =>
    erTilgjengelig(post.id) ? "site-badge ok" : "site-badge plan");
  assert.deepEqual(brikker.map((b) => b.className), forventet);
  assert.notEqual(siteTilbudMerke(true).className,
    siteTilbudMerke(false).className,
    "tilgjengelig og kommer deler klasse — da skiller ingenting dem visuelt");
});

test("Landing: hvert språknavn er merket med sitt eget språk", async () => {
  // Codex P2: begge etikettene arvet sidens `lang`. På den norske forsiden ble
  // «English» dermed uttalt med norsk uttale av en skjermleser, og etter
  // byttet ble «Norsk» uttalt som engelsk. Disse to knappene er nettopp
  // kontrollen en bruker trenger for å komme seg UT av et språk de ikke
  // forstår — de er de siste som tåler å bli lest feil.
  const app = nyttAppBrett();
  await visInnlogging();
  await vent(() => app.querySelectorAll(".site-sprak-knapp").length === 2);
  const merking = [...app.querySelectorAll(".site-sprak-knapp")]
    .map((k) => [k.getAttribute("lang"), k.textContent]);
  assert.deepEqual(merking, [["nb", NB["ui.sprak.nb"]], ["en", NB["ui.sprak.en"]]],
    "språkknappene mangler sitt eget lang — etiketten arver sidens språk");
  // Samme krav i skallet bak innlogging: der er velgeren en <select>, og
  // valgene arvet skallets lang på nøyaktig samme måte.
  const skall = AppShell({ tenant: "acme", ruter: [], aktiv: "oversikt",
    sprak: "nb" });
  const valg = [...skall.rot.querySelectorAll(".sprakvelger option")]
    .map((o) => [o.getAttribute("lang"), o.textContent]);
  assert.deepEqual(valg, [["nb", NB["ui.sprak.nb"]], ["en", NB["ui.sprak.en"]]],
    "språkvalgene i AppShell mangler sitt eget lang");
});

test("Landing: hoppelenka følger språkbyttet", async () => {
  // Codex P2: `.hoppelenke` står i `index.html`, altså UTENFOR `#app`, mens
  // språkbyttet på forsiden bare skriver `#app`. Lokaliseringen bodde privat i
  // `app.js` og ble aldri kalt herfra, så etter bytte til engelsk sto den
  // første tastaturkontrollen på siden igjen som «Hopp til innhold» under
  // `lang="en"` — feil språk for nøyaktig den som trenger den mest.
  const app = nyttAppBrett();
  // Lenka rigges som i `index.html`: søsken til `#app`, ikke barn.
  const lenke = document.createElement("a");
  lenke.className = "hoppelenke";
  lenke.setAttribute("href", "#hovedinnhold");
  lenke.textContent = NB["ui.hopp_til_innhold"];
  app.parentNode.insertBefore(lenke, app);
  try {
    await visInnlogging();
    await vent(() => app.querySelectorAll(".site-sprak-knapp").length === 2);

    const engelsk = [...app.querySelectorAll(".site-sprak-knapp")]
      .find((k) => k.textContent === NB["ui.sprak.en"]);
    engelsk.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await vent(() => app.textContent.includes(EN["site.home.tittel"]));

    assert.equal(lenke.textContent, EN["ui.hopp_til_innhold"],
      "hoppelenka står igjen på norsk etter byttet til engelsk");
    assert.equal(document.documentElement.getAttribute("lang"), "en");
  } finally {
    settI18nForTest(NB, "nb");
  }
});

// Codex P2: de fem offentlige sidene byttet bare kroppen, mens tittelen ble
// stående som den statiske `Disponit` fra `index.html`. Fanen, historikken og
// skjermleserens sideannonsering kunne da ikke skille hjem fra tjenester fra
// innlogging. Testen måler hver rute, og at tittelen følger språket: den er
// visningstekst som alt annet.
test("Landing: hver offentlig side har sin egen dokumenttittel", async () => {
  const sider = ["hjem", "tjenester", "produkt", "sikkerhet", "innlogging"];
  try {
    const sett = new Set();
    for (const side of sider) {
      window.history.replaceState({}, "", side === "hjem" ? "/" : `/?side=${side}`);
      nyttAppBrett();
      await visInnlogging();
      assert.ok(document.title.includes(NB[`site.nav.${side}`]),
        `tittelen på «${side}» navngir ikke siden: ${document.title}`);
      assert.ok(document.title.includes(NB["app.navn"]),
        "tittelen sier ikke hvilket produkt fanen tilhører");
      sett.add(document.title);
    }
    assert.equal(sett.size, sider.length,
      "to offentlige sider deler tittel — da skiller ingenting dem i fanelista");

    // Tittelen er tekst, ikke chrome: den skal bytte språk med resten.
    const app = nyttAppBrett();
    window.history.replaceState({}, "", "/?side=tjenester");
    await visInnlogging();
    await vent(() => app.querySelectorAll(".site-sprak-knapp").length === 2);
    [...app.querySelectorAll(".site-sprak-knapp")]
      .find((k) => k.textContent === NB["ui.sprak.en"])
      .dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await vent(() => document.title.includes(EN["site.nav.tjenester"]));
    assert.ok(document.title.includes(EN["site.nav.tjenester"]),
      `tittelen står igjen på norsk etter språkbyttet: ${document.title}`);
  } finally {
    window.history.replaceState({}, "", "/");
    document.documentElement.setAttribute("data-sprak", "nb");
    settI18nForTest(NB, "nb");
  }
});

// Codex P2: `byttTil` lagrer valget best effort, men lagringen KAN være nektet
// (privat modus, herdet nettleser). Den offentlige navigasjonen er ordinære
// `href`-er som laster dokumentet på nytt, så etter et klikk finnes valget kun
// der det er skrevet. Uten språkleddet i URL-en leste neste side ingenting og
// falt tilbake til `data-sprak="nb"` — en besøkende som byttet til engelsk fikk
// norsk igjen ved første klikk i den nye navigasjonen. Testen kjører med
// nektet `localStorage`, altså nøyaktig den brukeren, og krever at HVER vei
// videre bærer språket: lenkene, søkeformen og adresselinja.
test("Landing: språkvalget følger navigasjonen når lagring er nektet", async () => {
  const app = nyttAppBrett();
  const ekte = Object.getOwnPropertyDescriptor(window, "localStorage");
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    get() { throw new Error("lagring nektet"); },
  });
  try {
    window.history.replaceState({}, "", "/");
    await visInnlogging();
    await vent(() => app.querySelectorAll(".site-sprak-knapp").length === 2);

    const engelsk = [...app.querySelectorAll(".site-sprak-knapp")]
      .find((k) => k.textContent === NB["ui.sprak.en"]);
    engelsk.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await vent(() => app.textContent.includes(EN["site.home.tittel"]));

    const lenker = [...app.querySelectorAll(".site-hovednav a, .site-logo, " +
      ".site-cta a, .site-bunn-cta a, .site-footer a")];
    assert.ok(lenker.length >= 8, "for få offentlige lenker til å måle noe");
    for (const a of lenker) {
      assert.equal(new URL(a.getAttribute("href"), "https://x.test")
        .searchParams.get("sprak"), "en",
        `${a.getAttribute("href")} mister språkvalget ved klikk`);
    }
    assert.equal(app.querySelector(".site-sok"), null,
      "katalogsøket skal ikke stå på forsiden");
    assert.equal(new URLSearchParams(window.location.search).get("sprak"), "en",
      "adresselinja sier fortsatt norsk — en oppdatering mister valget");

    // Og den andre enden: en URL med språkleddet velges over dokumentets
    // `data-sprak`, ellers hadde lenkene båret et valg ingen leser.
    window.history.replaceState({}, "", "/?side=tjenester&sprak=en");
    await visInnlogging();
    assert.equal(
      app.querySelector('.site-sok input[name="sprak"]').getAttribute("value"),
      "en", "katalogsøket sender brukeren tilbake til norsk");
    document.documentElement.setAttribute("data-sprak", "nb");
    assert.equal(velgSprak(), "en");
  } finally {
    if (ekte) Object.defineProperty(window, "localStorage", ekte);
    else delete window.localStorage;
    window.history.replaceState({}, "", "/");
    document.documentElement.setAttribute("data-sprak", "nb");
    settI18nForTest(NB, "nb");
  }
});

test("Landing: et forbigått språkbytte tegner ikke over flaten som står", async () => {
  // Codex P2: byttet har TO ventepunkter, og bare det første var vernet.
  // `taIBruk` melder fra med `null` når et nyere valg har overtatt, men et
  // bytte som rakk forbi det vernet gikk videre inn i `visInnlogging`, som
  // henter `/ui/oppsett.json` og deretter rendret ubetinget. Kom det svaret
  // sist, tegnet et forlatt bytte over flaten et nyere bytte hadde bygd — med
  // SITT oppsett-svar.
  //
  // Riggen er nøyaktig det: det første byttets oppsett-kall henger og svarer
  // til slutt UTEN provider (nettverksglipp, feilende oppsettsrute), mens det
  // andre byttet går rett gjennom med provider. Vinner det gamle svaret, bytter
  // forsiden ut innloggingsknappene med «ikke tilgjengelig» — en besøkende
  // mister veien inn fordi et kall de hadde forlatt kom i mål.
  window.history.replaceState({}, "", "/?side=innlogging");
  const app = nyttAppBrett();
  const ekteFetch = globalThis.fetch;
  let slippOppsett = () => {};
  const holdt = new Promise((r) => { slippOppsett = r; });
  let oppsettNr = 0;
  globalThis.fetch = async (url) => {
    const sti = String(url).split("?")[0];
    if (sti === "/ui/oppsett.json") {
      const nr = ++oppsettNr;
      if (nr === 2) {                    // det FØRSTE byttet: henger, og taper
        await holdt;
        return { ok: true, status: 200, json: async () => ({}) };
      }
      return { ok: true, status: 200,
        json: async () => ({ provider_id: "google" }) };
    }
    return ekteFetch(url);
  };
  try {
    await visInnlogging();
    await vent(() => app.querySelectorAll(".site-sprak-knapp").length === 2);
    const engelsk = [...app.querySelectorAll(".site-sprak-knapp")]
      .find((k) => k.textContent === NB["ui.sprak.en"]);

    // Første klikk: locale-settet lastes ferdig, så blir kallet stående i
    // oppsett-hentingen. Flaten er urørt, så knappen står der fortsatt.
    engelsk.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await vent(() => oppsettNr === 2);
    // Andre klikk — samme knapp, for siden er ikke rendret på nytt ennå. Dette
    // byttet eier flaten fra nå, og det er det som kommer i mål først.
    engelsk.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await vent(() => app.textContent.includes(EN["site.login.tittel"]));
    assert.equal(app.querySelectorAll('form[action="/v1/oidc/start"]').length, 2,
      "det gjeldende byttet rendret ikke innloggingsveiene");

    // …og så kommer det forlatte byttet i mål, med sitt provider-løse svar.
    slippOppsett();
    await vent(() => false, 20);         // la det forlatte kallet få kjøre ut

    assert.equal(app.querySelectorAll('form[action="/v1/oidc/start"]').length, 2,
      "et forbigått språkbytte skrev over flaten med sitt eget oppsett-svar");
    assert.ok(!app.textContent.includes(NB["ui.logg_inn_utilgjengelig"]),
      "forsiden endte i feiltilstand fra et kall brukeren hadde forlatt");
    assert.ok(app.textContent.includes(EN["site.login.tittel"]),
      "flaten det gjeldende byttet bygde står ikke lenger");
  } finally {
    slippOppsett();
    globalThis.fetch = ekteFetch;
    settI18nForTest(NB, "nb");
    window.history.replaceState({}, "", "/");
  }
});

test("Landing: språket tas i bruk først når flaten som bærer det er klar", async () => {
  // Codex P2: locale-settet ble tatt i bruk i det svaret kom, men flaten ble
  // først byttet når `/ui/oppsett.json` var ferdig. I gapet sto den NORSKE
  // forsiden merket `lang="en"` — og henger oppsettskallet, står den slik på
  // ubestemt tid: en skjermleser leser norsk tekst med engelsk uttale, og en
  // oversetter ser en side som lyver om sitt eget språk.
  //
  // Riggen er nøyaktig gapet: byttets oppsett-kall parkeres, og vi ser på
  // dokumentet mens det står der.
  const app = nyttAppBrett();
  const ekteFetch = globalThis.fetch;
  let slippOppsett = () => {};
  const holdt = new Promise((r) => { slippOppsett = r; });
  let oppsettNr = 0;
  globalThis.fetch = async (url) => {
    const sti = String(url).split("?")[0];
    if (sti === "/ui/oppsett.json") {
      if (++oppsettNr === 2) await holdt;   // språkbyttets eget kall
      return { ok: true, status: 200,
        json: async () => ({ provider_id: "google" }) };
    }
    return ekteFetch(url);
  };
  document.documentElement.setAttribute("lang", "nb");
  try {
    await visInnlogging();
    await vent(() => app.querySelectorAll(".site-sprak-knapp").length === 2);

    const engelsk = [...app.querySelectorAll(".site-sprak-knapp")]
      .find((k) => k.textContent === NB["ui.sprak.en"]);
    engelsk.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await vent(() => oppsettNr === 2);
    await vent(() => false, 20);          // la locale-svaret komme helt fram

    assert.ok(app.textContent.includes(NB["site.home.tittel"]),
      "riggen parkerte ikke byttet — flaten er allerede byttet");
    assert.equal(document.documentElement.getAttribute("lang"), "nb",
      "dokumentet ble merket engelsk mens den norske forsiden fortsatt sto");
    assert.equal(t("site.home.tittel"), NB["site.home.tittel"],
      "locale-kartet ble byttet under flaten som fortsatt sto på skjermen");

    // Oppsettet kommer i mål: NÅ skal språket og flaten skifte sammen.
    slippOppsett();
    await vent(() => app.textContent.includes(EN["site.home.tittel"]));
    assert.equal(document.documentElement.getAttribute("lang"), "en",
      "flaten ble engelsk uten at dokumentet fulgte med");
  } finally {
    slippOppsett();
    globalThis.fetch = ekteFetch;
    settI18nForTest(NB, "nb");
    document.documentElement.setAttribute("lang", "nb");
  }
});

test("Landing: språkbyttet virker når localStorage er nektet", async () => {
  // Codex P2: byttet lagret valget og kjørte `location.reload()`. Nektet
  // nettleseren lagringen — privat modus, blokkerte tredjepartscookies, en
  // herdet nettleser — svelget `lagreSprak` feilen, og reloaden leste
  // `index.html` sin `data-sprak="nb"`: siden kom tilbake på norsk. Den
  // besøkende som ikke leser norsk kom seg altså ALDRI til engelsk.
  //
  // Riggen her er nøyaktig den tilstanden: `localStorage` kaster på både
  // lesing og skriving, som i en nettleser med lagring avslått.
  const ekte = globalThis.localStorage;
  const nektende = {
    getItem() { throw new Error("lagring nektet"); },
    setItem() { throw new Error("lagring nektet"); },
  };
  Object.defineProperty(globalThis, "localStorage",
    { value: nektende, configurable: true, writable: true });
  Object.defineProperty(window, "localStorage",
    { value: nektende, configurable: true, writable: true });
  try {
    const app = nyttAppBrett();
    await visInnlogging();
    await vent(() => app.querySelectorAll(".site-sprak-knapp").length === 2);
    assert.ok(app.textContent.includes(NB["site.home.tittel"]));

    const engelsk = [...app.querySelectorAll(".site-sprak-knapp")]
      .find((k) => k.textContent === NB["ui.sprak.en"]);
    assert.ok(engelsk, "ingen knapp for engelsk på forsiden");
    engelsk.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

    await vent(() => app.textContent.includes(EN["site.home.tittel"]));
    assert.ok(app.textContent.includes(EN["site.home.tittel"]),
      "forsiden ble aldri engelsk — byttet lente seg på lagringen");
    assert.ok(!app.textContent.includes(NB["site.home.tittel"]),
      "norsk innhold står igjen etter byttet");
    assert.equal(document.documentElement.getAttribute("lang"), "en",
      "<html lang> følger ikke det valgte språket");
    // Fokus følger med: knappen brukeren trykket på ble skrevet ut av DOM-en,
    // og uten dette må en tastaturbruker tabbe seg inn i siden på nytt.
    const naavaerende = app.querySelector('.site-sprak-knapp[aria-current="true"]');
    assert.equal(document.activeElement, naavaerende,
      "fokus havnet ikke på det valgte språket etter byttet");
  } finally {
    Object.defineProperty(globalThis, "localStorage",
      { value: ekte, configurable: true, writable: true });
    Object.defineProperty(window, "localStorage",
      { value: ekte, configurable: true, writable: true });
    settI18nForTest(NB, "nb");
  }
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
  // Kunden er tildelt M-1 og M-2. Da skal M-37 og M-38 IKKE stå på flaten.
  // M-2 er i drift etter akseptflippen 2026-08-23; M-1 kjører på staging.
  // «Aktive moduler» er derfor 1 og «under arbeid» 1 — kundens to, ikke
  // katalogens.
  const h = nyHoved();
  visKundeadmin(h, ctx({ tenant: "Beta", moduler: [1, 2] }));
  assert.ok(h.textContent.includes(t("site.modul.m1.navn")));
  assert.ok(h.textContent.includes(t("site.modul.m2.navn")));
  assert.ok(!h.textContent.includes(t("site.modul.m37.navn")),
    "M-37 vises for en tenant som ikke har den");
  assert.ok(!h.textContent.includes(t("site.modul.m38.navn")),
    "M-38 vises for en tenant som ikke har den");
  const kpi = [...h.querySelectorAll(".site-kpi strong")].map((n) => n.textContent);
  assert.equal(kpi[0], "1");
  assert.equal(kpi[1], "1");
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

test("Landing: hopp-lenka hopper FORBI navigasjonen, ikke til den", async () => {
  // Fra #52, der feilen faktisk oppsto: topplinja lå INNE i
  // `<main id="hovedinnhold">`, altså inne i målet for `.hoppelenke` i
  // `index.html`. Da lander hoppet på toppen AV den gjentatte navigasjonen, og
  // neste Tab går gjennom merket, alle nav-lenkene, søkefeltet og
  // språkknappene — lenka sparer ingenting (WCAG 2.4.1).
  //
  // Denne forsiden gjør det riktig i dag. Testen finnes fordi forholdet er
  // lett å bryte igjen ved neste omstrukturering, og fordi ingenting ellers
  // måler det: den påstår RELASJONEN — navigasjon og søk utenfor målet,
  // sideinnhold inni — ikke klassenavn.
  window.history.replaceState({}, "", "/");
  const app = nyttAppBrett();
  await visInnlogging();
  await vent(() => app.querySelector(".site-hovednav"));

  const maal = app.querySelector("#hovedinnhold");
  assert.ok(maal, "hopp-målet #hovedinnhold finnes ikke");
  assert.equal(maal.tagName, "MAIN", "hopp-målet er ikke hovedlandemerket");
  assert.equal(maal.querySelector(".site-hovednav"), null,
    "hovednavigasjonen står inne i hopp-målet — da hopper lenka til den");
  assert.equal(maal.querySelector(".site-sok"), null,
    "søkefeltet står inne i hopp-målet");
  assert.ok(maal.textContent.includes(t("site.home.tittel")),
    "sideinnholdet står UTENFOR hopp-målet — da hopper lenka til ingenting");
});

test("Landing: en ukjent side gir hjem, ikke en tom flate", async () => {
  // `?side=finnes-ikke` skal ikke ende i en side uten innhold. Fallet er
  // stille i koden (siste ledd i ternærkjeden), og derfor verdt å låse.
  window.history.replaceState({}, "", "/?side=finnes-ikke");
  const app = nyttAppBrett();
  await visInnlogging();
  await vent(() => app.querySelector(".site-hovednav"));
  assert.ok(app.textContent.includes(t("site.home.tittel")),
    "en ukjent side ga en tom flate i stedet for hjem");
  window.history.replaceState({}, "", "/");
});
