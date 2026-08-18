// Samleflaten «WCAG kontroll» (eiers UX-krav 18/8): ÉN nav-oppføring,
// tre faner (Bestill | Rapporter | Domener), tilstand overlever fanebytte,
// axe rent — og Domener-fanen: TXT-oppskrift vises én gang, tabell med
// caption/scope, feil med aria-invalid + fokus.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visWcagKontroll } from "../static/js/flater/wcagkontroll.js";
import { visDomener } from "../static/js/flater/domener.js";

settI18nForTest(NB, "nb");

let KALL;
let SVAR;
globalThis.fetch = async (url, opts = {}) => {
  const sti = url.split("?")[0];
  KALL.push({ sti, metode: opts.method || "GET",
    kropp: opts.body ? JSON.parse(opts.body) : null });
  const svar = (typeof SVAR === "function") ? SVAR(sti, opts) : SVAR[sti];
  if (svar === undefined) {
    return { ok: false, status: 404, json: async () => ({ feil: "ikke_funnet" }) };
  }
  if (typeof svar === "number") {
    return { ok: false, status: svar, json: async () => ({ feil: "x" }) };
  }
  return { ok: true, status: opts.method === "POST" ? 201 : 200,
    json: async () => svar };
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

function fane(h, nokkel) {
  const knapper = [...h.querySelectorAll('[role="tab"]')];
  const kn = knapper.find((k) => k.textContent === t(`ui.wcag.fane.${nokkel}`));
  kn.dispatchEvent(new window.Event("click"));
  return kn;
}

const TOMME_DOMENER = { "/v1/domener": { domener: [] } };

test("WCAG kontroll: én flate, tre faner, riktig ARIA-mønster, axe rent", async () => {
  KALL = []; SVAR = TOMME_DOMENER;
  const h = nyHoved();
  visWcagKontroll(h, ctx());
  assert.equal(h.querySelectorAll("h1").length, 1, "nøyaktig én h1");
  const faner = h.querySelectorAll('[role="tab"]');
  assert.equal(faner.length, 3);
  assert.ok(h.querySelector('[role="tablist"]'));
  // Bestill-fanen er aktiv og bærer bestillingsskjemaet
  assert.ok(h.querySelector("#bf-hostname"));
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("WCAG kontroll: leseøkt beholder rapportene, mister mutasjonsfanene", async () => {
  // Codex P2: sammenslåingen til ÉN nav-oppføring la hele flaten bak
  // `bestilling:opprett`, og da mistet hver ikke-admin kunderolle — `leser`,
  // `sikkerhet`, `godkjenner`, `policyforvalter` — sin ENESTE vei til
  // rapportene. `GET /v1/rapport/{id}` krever fortsatt bare `decisions:read`.
  // Ruten står nå bak det svakeste scopet, og MUTASJONENE skjules her.
  //
  // MUTASJONEN SOM DREPER DENNE: fjern scope-filteret på `trinn`, eller sett
  // ruten tilbake bak `bestilling:opprett`.
  KALL = []; SVAR = TOMME_DOMENER;
  const h = nyHoved();
  visWcagKontroll(h, { ...ctx(), scopes: ["decisions:read"] });
  const faner = [...h.querySelectorAll('[role="tab"]')]
    .map((k) => k.textContent);
  assert.deepEqual(faner, [t("ui.wcag.fane.rapporter")],
    "leseøkten skal se rapportfanen, og bare den");
  assert.ok(h.querySelector("#rp-oppdrag"), "rapportfanen er aktiv");
  assert.equal(h.querySelector("#bf-hostname"), null,
    "bestillingsskjemaet tegnes for en økt som bare kan få 403");
  assert.equal(h.querySelectorAll("h1").length, 1, "nøyaktig én h1");
  // Ingen av fanene henter domenelisten: den fanen finnes ikke for denne økten.
  assert.ok(!KALL.some((k) => k.sti === "/v1/domener"), KALL);
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("WCAG kontroll: fanebytte bevarer utfylt skjema", async () => {
  KALL = []; SVAR = TOMME_DOMENER;
  const h = nyHoved();
  visWcagKontroll(h, ctx());
  const inp = h.querySelector("#bf-hostname");
  inp.value = "kunde.example";
  fane(h, "rapporter");
  assert.ok(h.querySelector("#rp-oppdrag"), "rapportfanen tegnet");
  fane(h, "bestill");
  assert.equal(h.querySelector("#bf-hostname").value, "kunde.example",
    "utfylt felt overlevde fanebyttet");
});

test("Domener: tom liste, legg til → TXT-oppskrift i alert, liste oppdateres", async () => {
  KALL = [];
  let lagt = false;
  SVAR = (sti, opts) => {
    if (sti === "/v1/domener" && (opts.method || "GET") === "GET") {
      return lagt
        ? { domener: [{ hostname: "dittfirma.no", status: "ventende",
            wildcard: false, verifisert_ts: null, utloper: null,
            siste_vellykkede_revalidering: null,
            challenge_utstedt: "2026-08-18T10:00:00+00:00",
            challenge_utloper: "2026-08-25T10:00:00+00:00" }] }
        : { domener: [] };
    }
    if (sti === "/v1/domener" && opts.method === "POST") {
      lagt = true;
      return { hostname: "dittfirma.no", txt_navn: "_disponit-challenge.dittfirma.no",
        txt_verdi: "a".repeat(64), gyldig_dager: 7 };
    }
    return undefined;
  };
  const h = nyHoved();
  visWcagKontroll(h, ctx());
  fane(h, "domener");
  await vent(() => h.querySelector(".tilstand.tom"));
  const inp = h.querySelector("#dm-host");
  inp.value = "Dittfirma.NO"; inp.dispatchEvent(new window.Event("input"));
  inp.form.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => h.querySelector(".domeneutfall").textContent);
  const utfall = h.querySelector(".domeneutfall");
  assert.ok(utfall.textContent.includes(t("ui.domener.utfall.utstedt")));
  assert.ok(utfall.textContent.includes("a".repeat(64)), "TXT-verdien vises");
  assert.ok(utfall.textContent.includes(t("ui.domener.en_gang")));
  // Codex P2: oppføringen SER ut som et engangssteg, men revalideringen slår
  // den opp hver dag og autorisasjonen faller bort etter 72 timer uten treff
  // — mens fanen fortsatt viser «verifisert». Sto kravet ingen steder, ryddet
  // en kunde som ryddet etter seg vekk sin egen tilgang uten å få vite det.
  const behold = [...utfall.querySelectorAll("p")]
    .find((p) => p.textContent === t("ui.domener.behold"));
  assert.ok(behold, "oppskriften sier ikke at oppføringen må bli stående");
  assert.ok(!behold.classList.contains("muted"),
    "et krav skal ikke stå som dempet fotnote");
  assert.equal(KALL.find((k) => k.metode === "POST").kropp.hostname,
    "dittfirma.no", "hostname normaliseres før innsending");
  await vent(() => h.querySelector(".domeneliste table"));
  const tab = h.querySelector(".domeneliste table");
  assert.ok(tab.querySelector("caption"));
  assert.ok(tab.querySelector('th[scope="row"]'));
  assert.ok(tab.textContent.includes(t("domenestatus.ventende")),
    "status som TEKST");
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Domener: ugyldig vertsnavn → aria-invalid + fokus, intet kall", async () => {
  KALL = []; SVAR = TOMME_DOMENER;
  const h = nyHoved();
  visWcagKontroll(h, ctx());
  fane(h, "domener");
  await vent(() => h.querySelector("#dm-host"));
  const inp = h.querySelector("#dm-host");
  inp.value = "ugyldig..host"; inp.dispatchEvent(new window.Event("input"));
  const antallFoer = KALL.filter((k) => k.metode === "POST").length;
  inp.form.dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.equal(inp.getAttribute("aria-invalid"), "true");
  assert.equal(document.activeElement, inp);
  assert.equal(KALL.filter((k) => k.metode === "POST").length, antallFoer);
});

// Codex P2: flaten LOVER at statusen oppdateres når fanen åpnes igjen, og
// verifiseringen skjer i bakgrunnen (arbeideren, ~5 min). Med bare
// `tegnet`-vakten i `del()` ble den cachede DOM-en hengt tilbake uendret: en
// challenge som ble `verifisert` mens brukeren var på en annen fane sto
// fortsatt `ventende` til hele siden ble lastet på nytt.
test("Domener: fanen henter status på nytt ved hver aktivering", async () => {
  KALL = [];
  let status = "ventende";
  SVAR = (sti, opts) => {
    if (sti === "/v1/domener" && (opts.method || "GET") === "GET") {
      return { domener: [{ hostname: "dittfirma.no", status,
        wildcard: false, gyldig: status === "verifisert",
        verifisert_ts: null, utloper: null,
        siste_vellykkede_revalidering: null,
        challenge_utstedt: "2026-08-18T10:00:00+00:00",
        challenge_utloper: "2026-08-25T10:00:00+00:00" }] };
    }
    return undefined;
  };
  const h = nyHoved();
  visWcagKontroll(h, ctx());
  fane(h, "domener");
  await vent(() => h.querySelector(".domeneliste table"));
  const foer = KALL.filter((k) => k.sti === "/v1/domener").length;
  assert.equal(foer, 1, "første aktivering henter listen");
  assert.ok(h.querySelector(".domeneliste table").textContent
    .includes(t("domenestatus.ventende")));

  // Arbeideren finner beviset mens brukeren er på en annen fane.
  status = "verifisert";
  fane(h, "bestill");
  fane(h, "domener");
  await vent(() => KALL.filter((k) => k.sti === "/v1/domener").length > foer);
  assert.equal(KALL.filter((k) => k.sti === "/v1/domener").length, foer + 1,
    "gjenåpning av fanen hentet ikke status på nytt");
  await vent(() => h.querySelector(".domeneliste table").textContent
    .includes(t("domenestatus.verifisert")));
  assert.ok(h.querySelector(".domeneliste table").textContent
    .includes(t("domenestatus.verifisert")), "ny status vises");
});

test("Domener: oppfriskningen river ikke skjemaet eller TXT-oppskriften", async () => {
  KALL = [];
  let lagt = false;
  SVAR = (sti, opts) => {
    if (sti === "/v1/domener" && (opts.method || "GET") === "GET") {
      return { domener: lagt
        ? [{ hostname: "dittfirma.no", status: "ventende", wildcard: false,
            verifisert_ts: null, utloper: null,
            siste_vellykkede_revalidering: null,
            challenge_utstedt: "2026-08-18T10:00:00+00:00",
            challenge_utloper: "2026-08-25T10:00:00+00:00" }]
        : [] };
    }
    if (sti === "/v1/domener" && opts.method === "POST") {
      lagt = true;
      return { hostname: "dittfirma.no", txt_navn: "_disponit-challenge.dittfirma.no",
        txt_verdi: "b".repeat(64), gyldig_dager: 7 };
    }
    return undefined;
  };
  const h = nyHoved();
  visWcagKontroll(h, ctx());
  fane(h, "domener");
  await vent(() => h.querySelector(".tilstand.tom"));
  const inp = h.querySelector("#dm-host");
  inp.value = "dittfirma.no"; inp.dispatchEvent(new window.Event("input"));
  inp.form.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => h.querySelector(".domeneutfall").textContent);

  // TXT-verdien vises ÉN gang og finnes ikke igjen — en oppfriskning som
  // bygget delen om, ville slettet det eneste stedet kunden kan lese den.
  fane(h, "rapporter");
  fane(h, "domener");
  await vent(() => KALL.filter((k) => k.sti === "/v1/domener"
    && k.metode === "GET").length >= 3);
  assert.ok(h.querySelector(".domeneutfall").textContent
    .includes("b".repeat(64)), "TXT-oppskriften overlevde fanebyttet");
  assert.ok(h.querySelector("#dm-host"), "skjemaet står");
});

// Codex P2: to `last()`-kall kan overlappe — den første innlastingen står
// ennå ute når en vellykket utstedelse (eller en ny faneaktivering) ber om
// en oppfriskning. Uten et generasjonsnummer avgjorde ANKOMSTREKKEFØLGEN hva
// som ble stående på skjermen.
function _rad(hostname, status, gyldig = status === "verifisert") {
  return { hostname, status, wildcard: false, gyldig, verifisert_ts: null,
    utloper: null, siste_vellykkede_revalidering: null,
    challenge_utstedt: "2026-08-18T10:00:00+00:00",
    challenge_utloper: "2026-08-25T10:00:00+00:00" };
}

// Codex P2: basen lar en rad stå `verifisert` etter at 90-dagersvinduet har
// passert ELLER den daglige revalideringen har vært borte i mer enn 72 timer.
// `v_domeneautorisasjon.gyldig` er falsk da, og både egress og
// bestillingsporten avviser domenet — men fanen viste «Verifisert», så kunden
// fulgte flaten hit, så at alt var i orden, og fikk
// `bestilling_hostname_uverifisert` uten noe sted å lese hvorfor.
test("Domener: en verifisert rad UTEN gyldig autorisasjon vises ikke som verifisert", async () => {
  const ekte = globalThis.fetch;
  const koe = [];
  globalThis.fetch = _styrtFetch(koe);
  try {
    const h = nyHoved();
    visDomener(h, ctx());
    koe[0]({ ok: true, status: 200, json: async () => ({ domener: [
      // Lagret status `verifisert`, men autorisasjonen er borte.
      _rad("foreldet.example", "verifisert", false),
      // ...og naboen, som ER gyldig, skal fortsatt lese «Verifisert».
      _rad("frisk.example", "verifisert", true),
    ] }) });
    await vent(() => h.querySelector(".domeneliste table"));
    const rader = [...h.querySelectorAll(".domeneliste tbody tr")];
    const celle = (vert) => rader
      .find((r) => r.querySelector("th").textContent === vert)
      .querySelectorAll("td")[0].textContent;
    assert.equal(celle("foreldet.example"), t("domenestatus.ikke_aktiv"),
      "flaten påstår autorisasjon domenet ikke har");
    assert.equal(celle("frisk.example"), t("domenestatus.verifisert"));

    // Fail-closed: et svar UTEN feltet er ikke et ja.
    const uten = _rad("uten.example", "verifisert");
    delete uten.gyldig;
    const h2 = nyHoved();
    visDomener(h2, ctx());
    koe[1]({ ok: true, status: 200,
      json: async () => ({ domener: [uten] }) });
    await vent(() => h2.querySelector(".domeneliste table"));
    assert.ok(h2.querySelector(".domeneliste table").textContent
      .includes(t("domenestatus.ikke_aktiv")),
    "et manglende gyldig-felt ble lest som en verifisering");
  } finally {
    globalThis.fetch = ekte;
  }
});

async function _tomKo() {          // la de utestående mikrotaskene løpe ut
  for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0));
}

function _styrtFetch(koe) {
  return () => new Promise((resolve) => koe.push(resolve));
}

test("Domener: et foreldet listesvar overskriver ikke det nyere", async () => {
  const ekte = globalThis.fetch;
  const koe = [];
  globalThis.fetch = _styrtFetch(koe);
  try {
    const h = nyHoved();
    const oppfrisk = visDomener(h, ctx());   // A: første innlasting
    oppfrisk();                              // B: oppfriskningen, mens A står ute
    assert.equal(koe.length, 2, "to overlappende hentinger");

    const ok = (domener) => ({ ok: true, status: 200,
      json: async () => ({ domener }) });
    koe[1](ok([_rad("ny.example", "verifisert")]));      // B kommer først
    await vent(() => h.querySelector(".domeneliste table"));
    koe[0](ok([_rad("gammel.example", "ventende")]));    // A kommer ETTERPÅ
    await _tomKo();

    const tabell = h.querySelector(".domeneliste table").textContent;
    assert.ok(tabell.includes("ny.example"), tabell);
    assert.ok(!tabell.includes("gammel.example"),
      "det foreldede svaret overskrev den nyere listen");
  } finally {
    globalThis.fetch = ekte;
  }
});

test("Domener: en foreldet FEIL river ikke den nyere tabellen", async () => {
  const ekte = globalThis.fetch;
  const koe = [];
  globalThis.fetch = _styrtFetch(koe);
  try {
    const h = nyHoved();
    const oppfrisk = visDomener(h, ctx());
    oppfrisk();
    koe[1]({ ok: true, status: 200,
      json: async () => ({ domener: [_rad("ny.example", "verifisert")] }) });
    await vent(() => h.querySelector(".domeneliste table"));
    koe[0]({ ok: false, status: 500, json: async () => ({ feil: "x" }) });
    await _tomKo();

    assert.ok(h.querySelector(".domeneliste table"),
      "en gammel feil byttet ut en nyere, riktig tabell med en feiltilstand");
    assert.equal(h.querySelector(".domeneliste").getAttribute("aria-busy"),
      null, "ventetilstanden ble hengende etter det gjeldende svaret");
  } finally {
    globalThis.fetch = ekte;
  }
});

// Codex P2: disposisjonen måles i RAMMEN, ikke i delen. Hver del for seg
// hadde riktige nivåer; det var først da rapportinnholdet ble tegnet inne i
// en fane at bolkene («Sammendrag», «Dekning», «Sider», «Funn») sto som
// SØSKEN av fanens egen overskrift. En skjermleser som navigerer på nivå
// leste dem da som fire nye deler av flaten, ikke som rapportens innmat.
const RAPPORT_SVAR = {
  oppdrag_id: 42, artefakt_id: "a-1", artefakttype: "rapport.wcag.v1",
  promotert_ts: "2026-08-18T10:00:00+00:00",
  rapport: {
    kravsett: "wcag21_aa", regelsett_versjon: "axe-4.10",
    kjort_ts: "2026-08-18T09:59:00+00:00", varighet_ms: 12000,
    sider_kontrollert: [{ url: "https://kunde.example/", status: "ok" }],
    funn: [{ regel_id: "color-contrast", alvorlighet: "alvorlig",
      antall: 3, eksempler: ["main > p"] }],
    sammendrag: { kritisk: 0, alvorlig: 3, moderat: 0, lav: 0 },
    avkortet: { truffet: false, tak: 500, verdi: 12 },
    dekningsbegrensninger: [],
    manuelle_kriterier_vurdert: false,
  },
  request_id: "r",
};

// Nivåene i dokumentrekkefølge — det er DEN rekkefølgen en skjermleser
// bygger disposisjonen sin av.
function nivaer(rot) {
  return [...rot.querySelectorAll("h1,h2,h3,h4,h5,h6")]
    .map((n) => ({ niva: Number(n.tagName[1]), tekst: n.textContent }));
}

test("WCAG kontroll: rapportens bolker ligger UNDER fanens overskrift", async () => {
  // MUTASJONEN SOM DREPER DENNE: sett bolkene i rapport.js tilbake til h2
  // (eller flytt delens egen overskrift til h3 uten å flytte bolkene).
  KALL = []; SVAR = { ...TOMME_DOMENER, "/v1/rapport/42": RAPPORT_SVAR };
  const h = nyHoved();
  visWcagKontroll(h, { ...ctx(), scopes: ["decisions:read"] });
  const inp = h.querySelector("#rp-oppdrag");
  inp.value = "42"; inp.dispatchEvent(new window.Event("input"));
  inp.form.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await vent(() => h.querySelector(".rapport table"));

  const disp = nivaer(h);
  assert.equal(disp[0].niva, 1, "flaten eier h1");
  assert.equal(disp[1].niva, 2, "fanens del er h2");
  // Ingen hopp nedover: hvert nivå er høyst ETT dypere enn det forrige.
  for (let i = 1; i < disp.length; i++) {
    assert.ok(disp[i].niva <= disp[i - 1].niva + 1,
      `hoppet fra h${disp[i - 1].niva} til h${disp[i].niva} ved `
      + `«${disp[i].tekst}»`);
  }
  // ... og bolkene er faktisk BARN av delen, ikke søsken av den.
  for (const kode of ["sammendrag", "dekning", "sider", "funn"]) {
    const b = disp.find((x) => x.tekst === t(`ui.rapport.${kode}`));
    assert.ok(b, `bolken ${kode} mangler`);
    assert.equal(b.niva, 3, `bolken ${kode} står på h${b.niva}, ikke under delen`);
  }
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});
