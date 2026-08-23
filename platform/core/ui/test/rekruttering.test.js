// M-57-flaten (klarsignalet §8, portene 29–32): tabellens ARIA-mønster,
// trafikklys som tekst, vektendring uten mus med kunngjort re-rangering,
// blindingsbryterens alertdialog, signaturdialogens tekst og hashkortform,
// utfall i role=alert, axe rent — og tastaturgjennomgangen dokumentert.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visRekruttering } from "../static/js/flater/rekruttering.js";

settI18nForTest(NB, "nb");

const ROT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..");

let KALL;
let SVAR;
globalThis.fetch = async (url, opts = {}) => {
  const sti = url.split("?")[0];
  KALL.push({ sti, metode: opts.method || "GET",
    kropp: opts.body ? JSON.parse(opts.body) : null,
    hoder: opts.headers || {} });
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

const HASH = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
  + "a1b2c3d4e5f60718293a4b5c6d7e8f90";

function prosess() {
  return { prosesser: [{
    prosess_id: "p-1", blinding_av: false,
    vekter: { drift: 3, sky: 2 },
    kandidater: [
      { kandidat_id: "K-2", oppfylt: { drift: true, sky: false },
        status: "vurderes",
        funn: [{ kategori: "uklar_tidslinje",
                 kilde: { start: 0, slutt: 4, sitat: "2019" } }],
        intervjusporsmal: ["Fortell om tidslinjen."] },
      { kandidat_id: "K-1", oppfylt: { drift: true, sky: true },
        status: "anbefalt", funn: [], intervjusporsmal: [] },
    ],
    lister: [{ liste_id: "L-1", listetype: "invitasjon", antall: 42,
               innhold_hash: HASH }],
  }] };
}

function ctx() {
  return { sprak: "nb", scopes: ["decisions:read", "bestilling:opprett"],
    tenant: "acme", paaUautorisert: () => {} };
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

async function tegnet() {
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": prosess() };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "tabellen kom aldri");
  return hoved;
}

test("Rekruttering: tabell med caption, scope, aria-sort — og axe rent", async () => {
  const hoved = await tegnet();
  const tabell = hoved.querySelector("table");
  assert.ok(tabell.querySelector("caption").textContent.length > 0);
  for (const th of tabell.querySelectorAll("th")) {
    assert.equal(th.getAttribute("scope"), "col");
  }
  // Poengkolonnen er sortert synkende som utgangspunkt, og det STÅR der.
  const sortert = tabell.querySelector('th[aria-sort="descending"]');
  assert.ok(sortert, "aria-sort mangler");
  // Rangert: K-1 (5 poeng) foran K-2 (3 poeng).
  const rader = [...tabell.querySelectorAll("tbody tr")]
    .map((tr) => tr.querySelector("td").textContent);
  assert.deepEqual(rader, ["K-1", "K-2"]);
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Rekruttering: trafikklyset er tekst, aldri bare farge (port 30)", async () => {
  const hoved = await tegnet();
  const lys = [...hoved.querySelectorAll(".trafikklys")];
  assert.equal(lys.length, 2);
  for (const l of lys) {
    assert.ok(l.textContent.trim().length > 0,
      "kategorien mangler som tekst — farge alene er ikke informasjon");
  }
  assert.ok(lys.some((l) => l.textContent.includes(
    t("ui.rekruttering.status.anbefalt"))));
  // …og FARGEN finnes faktisk (Codex P2): uten regler i stilarket rendret
  // hver kategori likt, og prikken var en tom span uten flate — «tekst +
  // farge» var bare tekst. Statusene hentes fra locale, ikke fra en liste
  // her, så en ny kategori uten fargeregel feller porten.
  const css = readFileSync(join(ROT,
    "platform/core/ui/static/css/komponenter.css"), "utf-8");
  assert.ok(css.includes(".trafikklys-prikk{"), "prikken er uten flate");
  const statuser = Object.keys(NB)
    .filter((k) => k.startsWith("ui.rekruttering.status."))
    .map((k) => k.slice("ui.rekruttering.status.".length));
  assert.ok(statuser.length >= 3, "locale mangler statuskategoriene");
  for (const s of statuser) {
    assert.ok(css.includes(`.trafikklys-${s}{`),
      `ingen fargeregel for kategorien ${s}`);
  }
});

test("Rekruttering: vektendring uten mus re-rangerer og kunngjøres (port 30)", async () => {
  const hoved = await tegnet();
  const range = hoved.querySelector('input[type="range"]#vekt-sky');
  assert.ok(range.labels === undefined
    || hoved.querySelector('label[for="vekt-sky"]'), "range mangler label");
  // Tastaturbrukerens vei: sett verdien og fyr input-hendelsen — ingen mus.
  range.value = "0";
  range.dispatchEvent(new window.Event("input", { bubbles: true }));
  // Uten sky-vekt: K-1 og K-2 har begge 3 — likhet brytes på kandidat-id.
  const rader = [...hoved.querySelectorAll("tbody tr")]
    .map((tr) => tr.querySelector("td").textContent);
  assert.deepEqual(rader, ["K-1", "K-2"]);
  const kunngjoring = hoved.querySelector('[aria-live="polite"]');
  assert.ok(kunngjoring.textContent.includes("K-1"),
    "re-rangeringen ble ikke kunngjort");
  // Synlig verdi følger kontrollen.
  const visning = hoved.querySelector('output[for="vekt-sky"]');
  assert.equal(visning.textContent, "0");
});

test("Rekruttering: avskruing av blinding krever alertdialog med begrunnelse", async () => {
  const hoved = await tegnet();
  const bryter = hoved.querySelector("#rekrut-blinding");
  assert.equal(bryter.checked, true, "blinding er standard PÅ");
  bryter.checked = false;
  bryter.dispatchEvent(new window.Event("change", { bubbles: true }));
  const dialog = document.querySelector('[role="alertdialog"]');
  assert.ok(dialog, "alertdialog mangler ved avskruing");
  assert.equal(bryter.checked, true,
    "bryteren skal stå PÅ til dialogen bekrefter");
  // Bekreft uten begrunnelse → avvist lokalt, ingen POST — og dialogen
  // BLIR STÅENDE (Codex P2): meldingen om det manglende feltet er verdiløs
  // hvis feltet forsvant idet den kom, og brukeren måtte da skru bryteren
  // om igjen for å rette seg.
  const primar = [...dialog.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.blinding_av_bekreft"));
  primar.click();
  await vent(() => KALL.some((k) => k.metode === "POST"), 5);
  assert.ok(!KALL.some((k) => k.metode === "POST"),
    "POST gikk uten begrunnelse");
  assert.ok(document.body.contains(dialog),
    "dialogen lukket seg, og feilmeldingen gjaldt et felt som var borte");
  assert.equal(dialog.querySelector("textarea").getAttribute("aria-invalid"),
    "true");
  assert.ok([...dialog.querySelectorAll('[role="alert"]')]
    .some((n) => n.textContent ===
      t("ui.rekruttering.blinding_begrunnelse_mangler")),
    "feilen ble aldri meldt inne i dialogen");
  // Med begrunnelse i SAMME dialog → POST med begrunnelsen i kroppen.
  const dialog2 = dialog;
  dialog2.querySelector("textarea").value = "intern rekruttering";
  SVAR["/v1/rekruttering/prosesser/p-1/blinding"] = { ok: true };
  [...dialog2.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.blinding_av_bekreft"))
    .click();
  assert.ok(await vent(() => KALL.some((k) =>
    k.sti === "/v1/rekruttering/prosesser/p-1/blinding")), "POST kom aldri");
  const post = KALL.find((k) => k.sti.endsWith("/blinding"));
  assert.equal(post.kropp.begrunnelse, "intern rekruttering");
  assert.ok(post.hoder["Idempotency-Key"], "skrivevei uten idempotensnøkkel");
  assert.ok(await vent(() => hoved.querySelector('[role="alert"]')
    .textContent.length > 0), "utfallet nådde aldri alert-området");
});

test("Rekruttering: gjen-påslag av blinding er en mutasjon, og uten scope er bryteren død", async () => {
  // CodeRabbit (major, første pre-commit-pass): PÅ-igjen endret bare
  // UI-tilstand — serveren fikk aldri vite det. Nå følger bryteren
  // UTFALLET av kallet, begge veier.
  const hoved = await tegnet();
  const bryter = hoved.querySelector("#rekrut-blinding");
  bryter.checked = false;
  bryter.dispatchEvent(new window.Event("change", { bubbles: true }));
  const dialog = document.querySelector('[role="alertdialog"]');
  dialog.querySelector("textarea").value = "test";
  SVAR["/v1/rekruttering/prosesser/p-1/blinding"] = { ok: true };
  [...dialog.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.blinding_av_bekreft"))
    .click();
  await vent(() => !bryter.checked);
  assert.equal(bryter.checked, false);
  KALL = [];
  // PÅ igjen → POST med av:false; bryteren PÅ først når svaret er der.
  bryter.checked = true;
  bryter.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(await vent(() => KALL.some((k) =>
    k.sti.endsWith("/blinding") && k.kropp.av === false)),
    "gjen-påslaget nådde aldri serveren");
  // Cursor P2 / port 32: begrunnelsen er revisjonsinnhold OG tekst koden
  // skriver — hardkodet ble den norsk også i en engelsk UI.
  assert.equal(
    KALL.find((k) => k.sti.endsWith("/blinding")).kropp.begrunnelse,
    t("ui.rekruttering.blinding_pa_begrunnelse"),
    "auditbegrunnelsen kommer ikke fra locale");
  assert.ok(await vent(() => bryter.checked));
  // …og feiler kallet, står bryteren AV (utfallet, ikke klikket).
  bryter.checked = false;
  bryter.dispatchEvent(new window.Event("change", { bubbles: true }));
  const d2 = document.querySelector('[role="alertdialog"]');
  d2.querySelector("textarea").value = "test";
  [...d2.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.blinding_av_bekreft"))
    .click();
  await vent(() => !bryter.checked);
  SVAR = (sti, opts) => (opts.method === "POST" ? 403
    : { prosesser: [] });
  bryter.checked = true;
  bryter.dispatchEvent(new window.Event("change", { bubbles: true }));
  await vent(() => hoved.querySelector('[role="alert"]')
    .textContent === t("ui.rekruttering.feil_utfall"));
  assert.equal(bryter.checked, false, "bryteren fulgte klikket, ikke utfallet");
});

test("Rekruttering: uten mutasjonsscope er blindingsbryteren deaktivert", async () => {
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": prosess() };
  const hoved = nyHoved();
  visRekruttering(hoved, { sprak: "nb", scopes: ["decisions:read"],
    tenant: "acme", paaUautorisert: () => {} });
  await vent(() => hoved.querySelector("table"));
  assert.ok(hoved.querySelector("#rekrut-blinding").disabled,
    "bryteren tilbys en rolle som bare kan få 403");
  const signer = [...hoved.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.signer_knapp"));
  assert.ok(signer.disabled, "signeringen var alt gatet — den skal stå");
});

test("Rekruttering: signaturdialogen sier antall, type, hashkortform — og «Kan ikke angres» (port 31)", async () => {
  const hoved = await tegnet();
  const knapp = [...hoved.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.signer_knapp"));
  knapp.click();
  const dialog = document.querySelector('[role="alertdialog"]');
  assert.ok(dialog, "signering uten alertdialog");
  const tekst = dialog.textContent;
  assert.ok(tekst.includes("42"), "antallet mangler");
  // Cursor P1: `{antall}` står TO ganger i locale-teksten, og
  // `String.replace` med en streng bytter bare den første. Dialogen sa
  // «… 42 mottakere … Dette sender {antall} e-poster», og port 31 krever
  // setningen med tallet. Målt generisk: ingen plassholder overlever, og
  // tallet står like mange steder som locale-teksten nevner det.
  assert.ok(!/\{[a-zæøå_]+\}/.test(tekst),
    `plassholder står igjen i dialogen: ${tekst}`);
  assert.equal(
    (tekst.match(/42/g) || []).length,
    (NB["ui.rekruttering.signer_tekst"].match(/\{antall\}/g) || []).length,
    "antallet ble ikke satt inn overalt teksten nevner det");
  assert.ok(tekst.includes(t("ui.rekruttering.listetype.invitasjon")));
  assert.ok(tekst.includes(HASH.slice(0, 12) + "…"), "hashkortformen mangler");
  assert.ok(tekst.includes("Kan ikke angres"), "irreversibiliteten er taus");
  assert.ok(!tekst.includes(HASH), "fullhashen skal ikke ut i dialogen");
  // Signer → POST binder innholdshashen; utfallet står i role=alert.
  SVAR["/v1/rekruttering/lister/L-1/signer"] = { innhold_hash: HASH };
  [...dialog.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.signer_bekreft"))
    .click();
  assert.ok(await vent(() => KALL.some((k) =>
    k.sti === "/v1/rekruttering/lister/L-1/signer")), "POST kom aldri");
  assert.equal(KALL.find((k) => k.sti.endsWith("/signer")).kropp.innhold_hash,
    HASH);
  assert.ok(await vent(() => hoved.querySelector('[role="alert"]')
    .textContent.includes(HASH.slice(0, 12))), "utfallet mangler");
});

test("Rekruttering: hver prosess i svaret kan velges (ikke bare den første)", async () => {
  // Codex P2: endepunktet er i flertall og ruten bærer ingen prosess-id,
  // så `prosesser[0]` gjorde enhver senere prosess — kandidatlisten og de
  // usignerte utsendingene hennes — utilgjengelig.
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", navn: "Sykepleier vest", blinding_av: false,
    vekter: { drift: 1 },
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": to };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")));
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  assert.ok(velger, "ingen prosessvelger med flere prosesser i svaret");
  assert.ok(hoved.querySelector('label[for="rekrut-prosessvelger"]'),
    "velgeren mangler label");
  assert.deepEqual([...velger.options].map((o) => o.value), ["p-1", "p-2"]);
  // Den andre prosessens kandidater er nådd uten en ny runde i ruteren.
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  const rader = [...hoved.querySelectorAll("tbody tr")]
    .map((tr) => tr.querySelector("td").textContent);
  assert.deepEqual(rader, ["K-9"]);
  assert.equal(hoved.querySelector("#rekrut-prosessvelger").value, "p-2",
    "velgeren mistet valget da flaten ble tegnet på nytt");
  // Med bare én prosess står ingen velger i veien.
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": prosess() };
  const en = nyHoved();
  visRekruttering(en, ctx());
  assert.ok(await vent(() => en.querySelector("table")));
  assert.equal(en.querySelector("#rekrut-prosessvelger"), null);
});

test("Rekruttering: signeringen gjenbruker idempotensnøkkelen etter usikker feil", async () => {
  // Codex P1: den irreversible operasjonen fikk fersk nøkkel per klikk.
  // Commiter serveren og svaret går tapt, må BRUKERENS retry bære SAMME
  // nøkkel — ellers replayer serveren ikke, og klienten kan ikke avgjøre
  // om posten er sendt.
  const hoved = await tegnet();
  const knapp = [...hoved.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.signer_knapp"));
  const signer = async () => {
    // In-flight-låsen holder knappen død til forrige forsøk er avgjort;
    // brukerens retry kommer etter det.
    assert.ok(await vent(() => !knapp.disabled), "knappen ble aldri åpen");
    knapp.click();
    [...document.querySelector('[role="alertdialog"]')
      .querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.rekruttering.signer_bekreft"))
      .click();
  };
  // Første forsøk: svaret går tapt (500) …
  SVAR = (sti, opts) => (opts.method === "POST" ? 500 : prosess());
  KALL = [];
  await signer();
  assert.ok(await vent(() => KALL.some((k) => k.sti.endsWith("/signer"))));
  const forste = KALL.find((k) => k.sti.endsWith("/signer"));
  // … brukeren prøver igjen: samme nøkkel, så serveren kan replaye.
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/lister/L-1/signer": { innhold_hash: HASH } };
  await signer();
  assert.ok(await vent(() => KALL.some((k) => k.sti.endsWith("/signer"))));
  const andre = KALL.find((k) => k.sti.endsWith("/signer"));
  assert.ok(forste.hoder["Idempotency-Key"], "signeringen gikk uten nøkkel");
  assert.equal(andre.hoder["Idempotency-Key"],
    forste.hoder["Idempotency-Key"],
    "retryen bar en NY nøkkel — serveren ser en ny operasjon");
});

test("Rekruttering: signeringsnøkkel og «signert» overlever prosessbytte", async () => {
  // Codex P1 / Cursor P1: nøkkelkartet og «denne knappen er ferdig» lå
  // inne i `tegn`. Prosessvelgeren tegner flaten på nytt mot det SAMME
  // svaret, så et bytte fram og tilbake ga fersk nøkkel (retryen etter et
  // tapt svar ble en ny operasjon serveren ikke kan replaye) og en levende
  // «Signer» på en liste som alt var sendt — irreversibelt, to ganger.
  //
  // MUTASJONEN SOM DREPER DENNE: flytt `signeringsnokler`/`signerte`
  // tilbake inn i `tegn`.
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", navn: "Sykepleier vest", blinding_av: false,
    vekter: { drift: 1 },
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  KALL = [];
  SVAR = (sti, opts) => (opts.method === "POST" ? 500 : to);
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")));

  const signerKnapp = () => [...hoved.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.signer_knapp"));
  const signer = async () => {
    const knapp = signerKnapp();
    assert.ok(await vent(() => !knapp.disabled), "knappen ble aldri åpen");
    knapp.click();
    [...document.querySelector('[role="alertdialog"]')
      .querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.rekruttering.signer_bekreft"))
      .click();
  };
  const bytt = (id) => {
    const velger = hoved.querySelector("#rekrut-prosessvelger");
    velger.value = id;
    velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  };

  // Første forsøk på p-1: svaret går tapt.
  await signer();
  assert.ok(await vent(() => KALL.some((k) => k.sti.endsWith("/signer"))));
  const forste = KALL.find((k) => k.sti.endsWith("/signer"));
  assert.ok(forste.hoder["Idempotency-Key"], "signeringen gikk uten nøkkel");

  // Brukeren ser innom den andre prosessen før hun prøver igjen.
  bytt("p-2");
  assert.equal(signerKnapp(), undefined, "p-2 har ingen lister å signere");
  bytt("p-1");
  KALL = [];
  SVAR = (sti, opts) => (opts.method === "POST"
    ? { innhold_hash: HASH } : to);
  await signer();
  assert.ok(await vent(() => KALL.some((k) => k.sti.endsWith("/signer"))));
  assert.equal(KALL.find((k) => k.sti.endsWith("/signer"))
    .hoder["Idempotency-Key"], forste.hoder["Idempotency-Key"],
    "prosessbyttet ga retryen en NY nøkkel — serveren kan ikke replaye");

  // …og etter den vellykkede signeringen er listen ferdig: et bytte fram
  // og tilbake gjenoppliver den ikke.
  assert.ok(await vent(() => signerKnapp().disabled),
    "knappen levde videre etter vellykket signering");
  bytt("p-2");
  bytt("p-1");
  KALL = [];
  const gjenoppstatt = signerKnapp();
  assert.ok(gjenoppstatt.disabled,
    "prosessbyttet ga en levende Signer-knapp på en alt signert liste");
  gjenoppstatt.click();
  await vent(() => KALL.some((k) => k.metode === "POST"), 5);
  assert.equal(document.querySelectorAll('[role="alertdialog"]').length, 0,
    "den døde knappen åpnet signaturdialogen likevel");
  assert.ok(!KALL.some((k) => k.metode === "POST"),
    "listen ble sendt en gang til etter prosessbytte");
});

test("Rekruttering: blindingen gjenbruker idempotensnøkkelen etter usikker feil", async () => {
  // Cursor P2: signeringen fikk stabil nøkkel i forrige runde, blindingen
  // ikke — og uten `idem` lager `api.js` en fersk per kall. Et tapt svar
  // + nytt forsøk ble en NY auditert mutasjon i stedet for et replay: to
  // revisjonsrader for ett valg, og klienten vet ikke om den første gikk.
  //
  // MUTASJONEN SOM DREPER DENNE: dropp `idem`-argumentet igjen.
  const avslaatt = prosess();
  avslaatt.prosesser[0].blinding_av = true;   // bryteren starter AV
  KALL = [];
  SVAR = (sti, opts) => (opts.method === "POST" ? 500 : avslaatt);
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")));
  const bryter = hoved.querySelector("#rekrut-blinding");
  assert.equal(bryter.checked, false);

  // PÅ-veien går uten dialog: slå på, svaret går tapt …
  const slaaPaa = async () => {
    assert.ok(await vent(() => !bryter.disabled), "bryteren ble aldri åpen");
    bryter.checked = true;
    bryter.dispatchEvent(new window.Event("change", { bubbles: true }));
  };
  await slaaPaa();
  assert.ok(await vent(() => KALL.some((k) => k.sti.endsWith("/blinding"))));
  const forste = KALL.find((k) => k.sti.endsWith("/blinding"));
  assert.ok(forste.hoder["Idempotency-Key"], "blindingen gikk uten nøkkel");
  // … brukeren prøver igjen: SAMME nøkkel, så serveren kan replaye.
  KALL = [];
  SVAR = (sti, opts) => (opts.method === "POST" ? { ok: true } : avslaatt);
  await slaaPaa();
  assert.ok(await vent(() => KALL.some((k) => k.sti.endsWith("/blinding"))));
  const andre = KALL.find((k) => k.sti.endsWith("/blinding"));
  assert.equal(andre.hoder["Idempotency-Key"], forste.hoder["Idempotency-Key"],
    "retryen bar en NY nøkkel — serveren ser en ny auditert mutasjon");
  assert.ok(await vent(() => bryter.checked));

  // …men et NYTT valg samme vei er en ny operasjon, ikke et replay av
  // den forrige: skru av, og på igjen, og nøkkelen skal være en annen.
  bryter.checked = false;
  bryter.dispatchEvent(new window.Event("change", { bubbles: true }));
  const dialog = document.querySelector('[role="alertdialog"]');
  dialog.querySelector("textarea").value = "intern rekruttering";
  [...dialog.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.blinding_av_bekreft"))
    .click();
  assert.ok(await vent(() => !bryter.checked), "avskruingen slo aldri igjennom");
  KALL = [];
  await slaaPaa();
  assert.ok(await vent(() => KALL.some((k) => k.sti.endsWith("/blinding"))));
  assert.notEqual(KALL.find((k) => k.sti.endsWith("/blinding"))
    .hoder["Idempotency-Key"], forste.hoder["Idempotency-Key"],
    "en ny beslutning ble sendt som replay av den forrige");
});

test("Rekruttering: «Detaljer» åpner panelet med funn, sitat og spørsmål", async () => {
  // Codex P2: radhandlingen ble sendt som `utfor`, mens DataTabell binder
  // `handling.paaKlikk`. En `undefined` lytter er ingen feil i nettleseren
  // — den er ingenting. Knappen sto der, tok fokus, ble lest opp som
  // knapp, og gjorde intet; funnene, kildesitatene og intervjuspørsmålene
  // var utilgjengelige for ALLE. Tastaturgjennomgangens punkt 4 lover
  // nettopp denne flyten.
  //
  // MUTASJONEN SOM DREPER DENNE: bytt `paaKlikk` tilbake til `utfor`.
  const hoved = await tegnet();
  const rad = [...hoved.querySelectorAll("tbody tr")]
    .find((tr) => tr.querySelector("td").textContent === "K-2");
  const detaljer = [...rad.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.detaljer"));
  assert.ok(detaljer, "raden mangler detaljknappen");
  detaljer.click();
  const panel = document.querySelector('[role="dialog"]');
  assert.ok(panel, "detaljknappen åpnet ingenting");
  assert.ok(panel.textContent.includes("K-2"), "panelet gjelder feil kandidat");
  assert.ok(panel.textContent.includes(
    t("ui.rekruttering.funn.uklar_tidslinje")), "funnet mangler");
  assert.ok(panel.querySelector("q").textContent === "2019",
    "kildesitatet mangler");
  assert.ok(panel.textContent.includes("Fortell om tidslinjen."),
    "intervjuspørsmålet mangler");
});

test("Rekruttering: vektskyveren rommer vektene kontrakten godtar", async () => {
  // Codex P1: `evaluering.ranger` godtar ethvert ikke-negativt heltall,
  // men kontrollen sto på `max="10"`. Med en gyldig vekt på 20 regnet
  // flaten poeng på 20 og skrev 20 i `output`-en, mens skyveren selv sto
  // klemt på 10 — og brukerens FØRSTE piltast slo vekten ned til 9 og
  // rangerte kandidatene om uten at noe var ment endret.
  //
  // MUTASJONEN SOM DREPER DENNE: sett `max: "10"` tilbake.
  const stor = prosess();
  stor.prosesser[0].vekter = { drift: 20, sky: 2 };
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": stor };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")));

  const range = hoved.querySelector('input[type="range"]#vekt-drift');
  assert.ok(Number(range.max) >= 20,
    `skyveren tar ikke imot vekten serveren sendte (max=${range.max})`);
  // Kontrollens EGEN verdi er den serveren sendte — ikke en klemt utgave.
  assert.equal(range.value, "20",
    "nettleseren klemte verdien til taket; skyveren og tallet er uenige");
  assert.equal(hoved.querySelector('output[for="vekt-drift"]').textContent,
    "20");
  // Skalaen deles, så skyverne fortsatt kan sammenliknes med øyet.
  assert.equal(hoved.querySelector('input[type="range"]#vekt-sky').max,
    range.max);
  // Og en vanlig prosess beholder husets skala.
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": prosess() };
  const vanlig = nyHoved();
  visRekruttering(vanlig, ctx());
  assert.ok(await vent(() => vanlig.querySelector("table")));
  assert.equal(vanlig.querySelector('input[type="range"]#vekt-drift').max,
    "10");
});

test("Rekruttering: blindingsvalget overlever prosessbytte", async () => {
  // Codex P1 / Cursor P2: en vellykket mutasjon oppdaterte bare bryteren.
  // `prosess.blinding_av` i det hentede svaret sto igjen slik serveren
  // svarte FØR mutasjonen, og en ny tegning har bare det svaret å gå på —
  // så et prosessbytte og tilbake viste en bryter som påsto PÅ mens
  // serveren hadde AV. Feil revisjonsbilde rett før en evaluering.
  //
  // MUTASJONEN SOM DREPER DENNE: fjern `prosess.blinding_av = …`.
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", navn: "Sykepleier vest", blinding_av: false,
    vekter: { drift: 1 },
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  KALL = [];
  SVAR = (sti, opts) => (opts.method === "POST" ? { ok: true } : to);
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")));
  const bytt = (id) => {
    const velger = hoved.querySelector("#rekrut-prosessvelger");
    velger.value = id;
    velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  };

  // Skru AV blindingen på p-1 …
  const bryter = hoved.querySelector("#rekrut-blinding");
  bryter.checked = false;
  bryter.dispatchEvent(new window.Event("change", { bubbles: true }));
  const dialog = document.querySelector('[role="alertdialog"]');
  dialog.querySelector("textarea").value = "intern rekruttering";
  [...dialog.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.blinding_av_bekreft"))
    .click();
  assert.ok(await vent(() => !hoved.querySelector("#rekrut-blinding").checked),
    "avskruingen slo aldri igjennom");

  // … og gå innom p-2 og tilbake: valget står, uten en ny runde på nettet.
  bytt("p-2");
  assert.equal(hoved.querySelector("#rekrut-blinding").checked, true,
    "p-2 arvet p-1s blindingsvalg");
  KALL = [];
  bytt("p-1");
  assert.equal(hoved.querySelector("#rekrut-blinding").checked, false,
    "bryteren påsto PÅ mens serveren har AV");
  assert.ok(!KALL.some((k) => k.metode === "POST"),
    "prosessbyttet sendte en mutasjon");

  // Samme vei tilbake: gjen-påslaget skal også feste seg i modellen.
  const bryter2 = hoved.querySelector("#rekrut-blinding");
  bryter2.checked = true;
  bryter2.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(await vent(() => hoved.querySelector("#rekrut-blinding").checked),
    "gjen-påslaget slo aldri igjennom");
  bytt("p-2");
  bytt("p-1");
  assert.equal(hoved.querySelector("#rekrut-blinding").checked, true,
    "gjen-påslaget forsvant i tegningen");
});

test("Rekruttering: in-flight-lås — ingen andre mutasjon mens den første henger", async () => {
  // Cursor P2: dialogen lukkes ved bekreftelse og knappene sto åpne, så
  // et nytt klikk mens forrige POST hang ga to samtidige kall på en
  // irreversibel (signering) og en auditert (blinding) handling.
  const hoved = await tegnet();
  const ekte = globalThis.fetch;
  let poster = 0;
  globalThis.fetch = async (url, opts = {}) => {
    if ((opts.method || "GET") === "POST") {
      poster += 1;
      return new Promise(() => {});         // svaret kommer aldri
    }
    return ekte(url, opts);
  };
  try {
    const knapp = [...hoved.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.rekruttering.signer_knapp"));
    knapp.click();
    [...document.querySelector('[role="alertdialog"]')
      .querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.rekruttering.signer_bekreft"))
      .click();
    assert.ok(await vent(() => poster === 1), "signeringen gikk aldri");
    assert.ok(knapp.disabled, "signer-knappen sto åpen mens kallet hang");
    knapp.click();
    assert.equal(document.querySelectorAll('[role="alertdialog"]').length, 0,
      "et nytt klikk åpnet dialogen igjen midt i en pågående signering");
    assert.equal(poster, 1, "to signeringer av samme liste");

    // Samme lås på blindingen: bryteren er død mens kallet henger.
    const bryter = hoved.querySelector("#rekrut-blinding");
    bryter.checked = false;
    bryter.dispatchEvent(new window.Event("change", { bubbles: true }));
    const dialog = document.querySelector('[role="alertdialog"]');
    dialog.querySelector("textarea").value = "intern rekruttering";
    [...dialog.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.rekruttering.blinding_av_bekreft"))
      .click();
    assert.ok(await vent(() => poster === 2), "blindingskallet gikk aldri");
    assert.ok(bryter.disabled, "bryteren sto åpen mens kallet hang");
    bryter.checked = true;
    bryter.dispatchEvent(new window.Event("change", { bubbles: true }));
    await vent(() => poster === 3, 5);
    assert.equal(poster, 2, "gjen-påslaget gikk mens avskruingen hang");
  } finally {
    globalThis.fetch = ekte;
  }
});

test("Rekruttering: 401 i mutasjonene er innlogging, ikke en handlingsfeil", async () => {
  // Codex P1: en utløpt økt ble fanget som «noe gikk galt», og brukeren
  // ble stående i det innloggede skallet uten økt bak seg. 401 er global
  // i resten av klienten (V2: 401 → innlogging, 403 → ingen tilgang).
  for (const flyt of ["blinding", "signer"]) {
    KALL = [];
    SVAR = { "/v1/rekruttering/prosesser": prosess() };
    let uautorisert = 0;
    const hoved = nyHoved();
    visRekruttering(hoved, { sprak: "nb", tenant: "acme",
      scopes: ["decisions:read", "bestilling:opprett"],
      paaUautorisert: () => { uautorisert += 1; } });
    await vent(() => hoved.querySelector("table"));
    SVAR = (sti, opts) => (opts.method === "POST" ? 401 : prosess());
    if (flyt === "blinding") {
      const bryter = hoved.querySelector("#rekrut-blinding");
      bryter.checked = false;
      bryter.dispatchEvent(new window.Event("change", { bubbles: true }));
      const dialog = document.querySelector('[role="alertdialog"]');
      dialog.querySelector("textarea").value = "intern rekruttering";
      [...dialog.querySelectorAll("button")]
        .find((b) => b.textContent === t("ui.rekruttering.blinding_av_bekreft"))
        .click();
    } else {
      [...hoved.querySelectorAll("button")]
        .find((b) => b.textContent === t("ui.rekruttering.signer_knapp"))
        .click();
      const dialog = document.querySelector('[role="alertdialog"]');
      [...dialog.querySelectorAll("button")]
        .find((b) => b.textContent === t("ui.rekruttering.signer_bekreft"))
        .click();
    }
    assert.ok(await vent(() => uautorisert === 1),
      `${flyt}: 401 nådde aldri paaUautorisert`);
    assert.equal(hoved.querySelector('[role="alert"]').textContent, "",
      `${flyt}: 401 ble meldt som en vanlig handlingsfeil`);
  }
});

test("Rekruttering: ingen hardkodet visningstekst, og tastaturgjennomgangen er dokumentert (port 32)", async () => {
  // Alle brukersynlige strenger går via t() — målt ved å rendre med et
  // locale der hver nøkkel er sin egen verdi, og kreve at flatens tekst
  // består av nøkler og data, aldri norsk/engelsk prosa i koden.
  const kilde = readFileSync(join(ROT,
    "platform/core/ui/static/js/flater/rekruttering.js"), "utf-8");
  assert.ok(!/text: "[A-ZÆØÅ][a-zæøå]+ /.test(kilde),
    "hardkodet visningstekst i flaten");
  // Tastaturgjennomgangen: dokumentet finnes og dekker de fire flytene.
  const dok = readFileSync(join(ROT,
    "docs/pr/PR-M57-TASTATURGJENNOMGANG.md"), "utf-8");
  for (const flyt of ["vekt", "sorter", "blinding", "signer"]) {
    assert.ok(dok.toLowerCase().includes(flyt),
      `tastaturgjennomgangen dekker ikke ${flyt}-flyten`);
  }
});
