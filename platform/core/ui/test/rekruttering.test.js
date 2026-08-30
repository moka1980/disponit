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
import { harNokkel, settI18nForTest, t } from "../static/js/i18n.js";
import { visRekruttering } from "../static/js/flater/rekruttering.js";
import { byggRuter } from "../static/js/sitekart.js";
import { Tidspunkt } from "../static/js/komponenter.js";

settI18nForTest(NB, "nb");

const ROT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..");

// EN-settet, for portene som må se forskjell på «riktig» og «hardkodet
// norsk» — på `nb` er de to den samme strengen.
const EN = JSON.parse(readFileSync(join(ROT, "locales", "en.json"), "utf-8"));

let KALL;
let SVAR;
globalThis.fetch = async (url, opts = {}) => {
  const sti = url.split("?")[0];
  let kropp = null;
  if (opts.body) {
    // Opplastingen sender RÅ bytes (ArrayBuffer) — de er ikke JSON og
    // registreres som binær størrelse i stedet.
    try { kropp = JSON.parse(opts.body); }
    catch { kropp = { binaer: opts.body.byteLength || 0 }; }
  }
  KALL.push({ sti, url, metode: opts.method || "GET", kropp,
    hoder: opts.headers || {} });
  // SPØRRESTRENGEN KAN VELGE SVAR (Cursor P2, #183). `sti` er strippet, og
  // med bare den kunne prosessbyttet aldri få et ANNET svar enn den første
  // lastingen — så fixturene måtte gi hver prosess full data, altså en
  // kontrakt serveren ikke har. En eksakt URL-nøkkel vinner nå over
  // sti-nøkkelen; alt som ikke bruker den går nøyaktig samme vei som før.
  const raatt = (typeof SVAR === "function") ? SVAR(sti, opts)
    : (SVAR[url] !== undefined ? SVAR[url] : SVAR[sti]);
  // Et `SVAR` som er et LØFTE henger hele kallet til testen slipper det, og
  // utfallet avgjøres først DA. Uten dette sto `ok` klar før løftet var
  // løst, så et hengende kall kunne bare ende i suksess — og vinduet
  // «POST-en henger og feiler så med 5xx» var umålbart. Bare løfter ventes
  // på: et vanlig svar går nøyaktig samme vei som før, på samme tikk, så
  // ingen eksisterende test får ny timing.
  let svar = (raatt && typeof raatt.then === "function") ? await raatt : raatt;
  // TEST-SERVEREN BÆRER DEN NYE KONTRAKTEN (eiers valg A på #258-A):
  // svaret peker selv på ferskeste klare rapport, målt på nøyaktig
  // (opprettet, id) — klienten sammenligner aldri. Fixturer som setter
  // feltet eksplisitt beholder sitt; resten får det utledet her, slik
  // den ekte serveren gjør det.
  if (svar && sti === "/v1/rekruttering/evalueringer"
      && Array.isArray(svar.evalueringer)
      && svar.ferskeste_klar_oppdrag === undefined) {
    const klar = svar.evalueringer.filter((e) => e.rapport_klar);
    klar.sort((a, b) => (a.opprettet === b.opprettet
      ? b.oppdrag_id - a.oppdrag_id
      : (a.opprettet < b.opprettet ? 1 : -1)));
    svar = { ...svar,
      ferskeste_klar_oppdrag: klar.length ? klar[0].oppdrag_id : null };
  }
  if (svar === undefined) {
    return { ok: false, status: 404, json: async () => ({ feil: "ikke_funnet" }) };
  }
  if (typeof svar === "number") {
    return { ok: false, status: svar, json: async () => ({ feil: "x" }) };
  }
  // `__status`/`__kropp` — husets form (`adjudikator.test.js:27`): et tall
  // sier bare statusen, og flaten skiller på KODEN. En 409
  // `idempotenskonflikt` fra bestillingen betyr «nøkkelen er opptatt av et
  // forsøk som fortsatt går», og det er en annen sak enn en 409 uten kode.
  if (svar && svar.__status) {
    const kropp = svar.__kropp !== undefined ? svar.__kropp : svar;
    return { ok: svar.__status < 400, status: svar.__status,
      json: async () => kropp };
  }
  return { ok: true, status: opts.method === "POST" ? 201 : 200,
    json: async () => svar };
};

const HASH = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
  + "a1b2c3d4e5f60718293a4b5c6d7e8f90";

function prosess() {
  return { prosesser: [{
    prosess_id: "p-1", blinding_av: false,
    // `vekter_kilde` står i fixturen fordi serveren ALLTID sender det:
    // «evalueringsartefakt» når artefaktet bar vektene, «standard» når
    // huset måtte finne på dem. De øvrige testene handler om noe annet
    // og skal ikke tegne kilde-merknaden.
    vekter: { drift: 3, sky: 2 }, vekter_kilde: "evalueringsartefakt",
    // Samme grunn for `evaluering_status`: serveren sender ALLTID
    // oppdragets status, og «utfort» er den ferdige evalueringen de
    // øvrige testene handler om — de skal ikke tegne ufullstendig-
    // merknaden.
    evaluering_status: "utfort",
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
    // Serveren teller raden sin i indeksen (#183) og sender tallet på
    // hver prosess, også den spisse.
    kandidat_antall: 2,
  }], valgt_prosess_id: "p-1" };
}

// #183-FORMEN, ÉTT STED (Cursor P2). Svaret bærer en LETT indeks over
// prosessene og full data for ÉN — den navngitte, ellers den nyeste. De
// eksisterende flerprosess-fixturene ga hver prosess full
// `kandidater`/`lister`, altså en nyttelast serveren ikke har: de beviste
// BYTTELOGIKK, ikke den nye formen, og et funn som bare finnes i
// indeksraden — etiketten som teller 0 kandidater — kunne derfor ikke
// fanges av en grønn suite.
//
// Hjelperen tar den fulle fixturen og gir kartet flaten faktisk henter:
// uten `?prosess_id=` er den FØRSTE spissen, og hver id har sin egen
// oppføring. Tester som måler noe helt annet enn byttet beholder sine
// full-data-mocker.
function svar183(full) {
  const alle = full.prosesser;
  const antall = (p) => (p.kandidater || []).length;
  const lett = (p) => {
    const rad = { prosess_id: p.prosess_id,
      evaluering_status: p.evaluering_status,
      kandidat_antall: antall(p) };
    if (p.opprettet !== undefined) rad.opprettet = p.opprettet;
    // `navn` sender serveren ikke i dag — men fixturen setter det der
    // testen måler at det VINNER den dagen #162-kjeden gir tittelen, og
    // da er indeksraden nettopp raden det gjelder.
    if (p.navn !== undefined) rad.navn = p.navn;
    return rad;
  };
  const bygg = (valgt) => ({
    valgt_prosess_id: valgt,
    prosesser: alle.map((p) => (p.prosess_id === valgt
      ? { ...p, kandidat_antall: antall(p) } : lett(p))),
  });
  const kart = { "/v1/rekruttering/prosesser": bygg(alle[0].prosess_id) };
  for (const p of alle) {
    kart["/v1/rekruttering/prosesser?prosess_id="
      + encodeURIComponent(p.prosess_id)] = bygg(p.prosess_id);
  }
  return kart;
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

test("Rekruttering: vektkontrollens kravnavn bæres av locale, ikke av rå id (port 32)", async () => {
  // Cursor P2: `t("ui.rekruttering.krav.<krav>", krav)` faller til reserven
  // — den RÅ kravnøkkelen — når locale mangler oppføringen, og en
  // skyver merket «skytjenester» er hardkodet visningstekst i praksis
  // (RUTINER §5). Kravene demoen og seeden faktisk viser skal ha tekst i
  // BEGGE språkene.
  //
  // Listen står her og ikke lest ut av `seed-rekruttering-demo.py`: en
  // JS-test som hand-parser Python for en dict er nettopp den fremmede
  // grammatikken K4/SP-13 forbyr. Endres seedens VEKTER, feiler denne.
  const en = JSON.parse(readFileSync(join(ROT, "locales", "en.json"), "utf-8"));
  for (const krav of ["drift", "sky", "skytjenester", "norsk"]) {
    const n = `ui.rekruttering.krav.${krav}`;
    assert.ok(harNokkel(n), `nb mangler ${n}`);
    assert.ok(typeof en[n] === "string" && en[n].length, `en mangler ${n}`);
  }
  // …og etiketten i DOM-en er teksten, ikke reserven.
  const hoved = await tegnet();
  const etikett = hoved.querySelector('label[for="vekt-skytjenester"]')
    || hoved.querySelector('label[for="vekt-sky"]');
  assert.equal(etikett.textContent, t("ui.rekruttering.krav.sky"));
  assert.notEqual(etikett.textContent, "sky");
  // MUTASJONEN SOM DREPER DENNE: fjern nøkkelen fra nb.json — da blir
  // etiketten «sky», og linjen over faller.
});

test("Rekruttering: brukerens sortering overlever en vektendring (port 30)", async () => {
  // Codex P2: vektendringen KREVER en ny tabell, og hver nye `DataTabell`
  // fikk «poeng, synkende» hardkodet. Vendte brukeren kolonnen stigende for
  // å se hvem som ligger nederst, slo tabellen tilbake til synkende ved
  // første piltast på en skyver — akkurat den handlingen hun sorterte for å
  // studere. `tabell.js` tilbyr `sort`/`paaSort` for nettopp dette.
  //
  // MUTASJONEN SOM DREPER DENNE: sett `sort:` tilbake til
  // `{ nokkel: "poeng", retning: "descending" }` i `rekruttering.js`, eller
  // fjern `paaSort`.
  const hoved = await tegnet();
  const poengTh = [...hoved.querySelectorAll("th")]
    .find((th) => th.textContent.includes(t("ui.rekruttering.kol_poeng")));
  // Tastaturbrukerens vei: knappen i th-en, ingen mus.
  poengTh.querySelector("button").click();
  await vent(() => hoved.querySelector('th[aria-sort="ascending"]'));
  assert.deepEqual([...hoved.querySelectorAll("tbody tr")]
    .map((tr) => tr.querySelector("td").textContent), ["K-2", "K-1"],
    "stigende sortering slo ikke igjennom");

  // …og så en vektendring, som bygger tabellen på nytt.
  const range = hoved.querySelector('input[type="range"]#vekt-drift');
  range.value = "9";
  range.dispatchEvent(new window.Event("input", { bubbles: true }));
  assert.ok(hoved.querySelector('th[aria-sort="ascending"]'),
    "vektendringen kastet brukerens sortering tilbake til synkende");
  // K-1 (9+2=11) og K-2 (9+0=9): stigende betyr K-2 først, fortsatt.
  assert.deepEqual([...hoved.querySelectorAll("tbody tr")]
    .map((tr) => tr.querySelector("td").textContent), ["K-2", "K-1"]);
  // Kunngjøringen leser den raden som FAKTISK står øverst, ikke den
  // høyest rangerte: den skal aldri si noe annet enn det tabellen viser.
  assert.ok(hoved.querySelector('[aria-live="polite"]').textContent
    .includes("K-2"), "kunngjøringen navnga en annen rad enn den øverste");
});

test("Rekruttering: blindingen er et tilstandsmerke, ikke et valg, uten #159",
  async () => {
    // Codex P2 (runde 4): bryteren sto handlingsklar for enhver
    // administrator, og etiketten lovte at valget «loggføres med hvem, når
    // og hvorfor» — men `blinding_endepunkt` svarer en kodet 409 uten å se
    // på prosessen og uten å skrive et spor, begge veier. Hvert gyldige
    // forsøk endte altså i en generisk avvisning på et løfte om
    // revisjonsevidens. Løftet er trukket der det ble gitt: bryteren viser
    // TILSTANDEN (blindingen er på), er død, og merknaden sier hvorfor.
    //
    // MUTASJONEN SOM DREPER DENNE: fjern `bryter.disabled = true`.
    const hoved = await tegnet();
    const bryter = hoved.querySelector("#rekrut-blinding");
    assert.equal(bryter.checked, true, "blinding er standard PÅ");
    assert.ok(bryter.disabled,
      "bryteren tilbyr en mutasjon endepunktet ikke kan utføre");
    assert.ok([...hoved.querySelectorAll("p")].some((n) => n.textContent ===
      t("ui.rekruttering.blinding_avskruing_utilgjengelig")),
      "merknaden om at avskruing mangler, står ikke på flaten");
    // …og ingen vei ut av flaten går til blinding-ruten.
    KALL = [];
    bryter.checked = false;
    bryter.dispatchEvent(new window.Event("change", { bubbles: true }));
    await vent(() => KALL.some((k) => k.metode === "POST"), 5);
    assert.ok(!KALL.some((k) => k.sti.endsWith("/blinding")),
      "flaten kalte fortsatt blinding-ruten");
    assert.ok(!document.querySelector('[role="alertdialog"]'),
      "avskruingsdialogen åpnet seg fra en død bryter");
  });

test("Rekruttering: en uferdig evaluering sier fra — alle tre veier",
  async () => {
    // Codex P2: prosessen fødes MENS kjøringen står på (`plukket`), og
    // artefaktene skrives inkrementelt. Tabellen viste derfor en delvis
    // kandidatliste som en ferdig rangering, uten et tegn på at noen
    // manglet — og etter en `feilet`/`kansellert` kjøring kommer resten
    // aldri.
    //
    // Målt alle tre veier, ellers er testen bare en påstand om at en <p>
    // finnes: pågår, avbrutt, og ferdig (der merknaden IKKE skal stå).
    //
    // MUTASJONEN SOM DREPER DENNE: gjør betingelsen i `flater/
    // rekruttering.js` konstant (alltid eller aldri).
    const merknad = (h) => [...h.querySelectorAll(".rekrut-evaluering p")]
      .map((p) => p.textContent);

    const kjorer = prosess();
    kjorer.prosesser[0].evaluering_status = "plukket";
    SVAR = { "/v1/rekruttering/prosesser": kjorer };
    const h1 = nyHoved();
    visRekruttering(h1, ctx());
    await vent(() => h1.querySelector("table"));
    assert.deepEqual(merknad(h1), [t("ui.rekruttering.evaluering_pagar")],
      "en delvis kandidatliste ble vist som en ferdig rangering");
    // …og teksten er locale-båret, aldri en rå nøkkel (RUTINER §5).
    assert.ok(!merknad(h1)[0].startsWith("ui."),
      "merknaden falt til reservenøkkelen — locale mangler");

    const avbrutt = prosess();
    avbrutt.prosesser[0].evaluering_status = "kansellert";
    SVAR = { "/v1/rekruttering/prosesser": avbrutt };
    const h2 = nyHoved();
    visRekruttering(h2, ctx());
    await vent(() => h2.querySelector("table"));
    assert.deepEqual(merknad(h2), [t("ui.rekruttering.evaluering_avbrutt")],
      "en avbrutt kjøring lovet fortsatt at resten kommer");

    SVAR = { "/v1/rekruttering/prosesser": prosess() };   // utfort
    const h3 = nyHoved();
    visRekruttering(h3, ctx());
    await vent(() => h3.querySelector("table"));
    assert.deepEqual(merknad(h3), [],
      "en FERDIG evaluering ble stemplet som ufullstendig");
  });

test("Rekruttering: oppfunne vekter sier fra — begge veier", async () => {
  // Codex P1: er vektene husets reserve (3 per krav) og ikke
  // evalueringens, viser tabellen en rangering evalueringen aldri
  // produserte. Serveren har hele tiden sagt det i `vekter_kilde`;
  // flaten leste ikke feltet, så substitusjonen var STILLE.
  //
  // Målt BEGGE veier, ellers er testen bare en påstand om at en <p>
  // finnes: merknaden skal stå når kilden er reserven, og den skal IKKE
  // stå når vektene faktisk er artefaktets — en merknad som alltid står,
  // sier ingenting.
  //
  // MUTASJONEN SOM DREPER DENNE: gjør betingelsen i `flater/
  // rekruttering.js` konstant (alltid eller aldri).
  const merknad = (h) => [...h.querySelectorAll("fieldset.rekrut-vekter p")]
    .map((p) => p.textContent);

  const std = prosess();
  std.prosesser[0].vekter_kilde = "standard";
  SVAR = { "/v1/rekruttering/prosesser": std };
  const h1 = nyHoved();
  visRekruttering(h1, ctx());
  await vent(() => h1.querySelector("table"));
  assert.deepEqual(merknad(h1), [t("ui.rekruttering.vekter_standard")],
    "husets reservevekter ble presentert uten et ord om opphavet");
  // …og teksten er locale-båret, aldri en rå nøkkel (RUTINER §5).
  assert.ok(!merknad(h1)[0].startsWith("ui."),
    "merknaden falt til reservenøkkelen — locale mangler");

  SVAR = { "/v1/rekruttering/prosesser": prosess() };  // evalueringsartefakt
  const h2 = nyHoved();
  visRekruttering(h2, ctx());
  await vent(() => h2.querySelector("table"));
  assert.deepEqual(merknad(h2), [],
    "evalueringens EGNE vekter ble stemplet som oppfunne");
});

test("Rekruttering: poengsummen teller `true`, ikke «sant nok»", async () => {
  // Cursor P1 (10:01): serveren utleder trafikklyset med `v is True`,
  // fordi `"false"` — den vanligste JSON-feilen en modell gjør — er en
  // SANN streng, og begge skriveportene avviser ikke-boolske verdier.
  // Flaten regnet poeng med en truthy-test, så den samme kandidaten fikk
  // hele kravets vekt i poengkolonnen mens lyset ved siden av sa «Bør
  // vurderes»: to tall om samme kandidat på samme skjerm.
  //
  // MUTASJONEN SOM DREPER DENNE: bytt `=== true` mot `` i `poengFor`.
  KALL = [];
  const data = prosess();
  data.prosesser[0].kandidater = [
    { kandidat_id: "K-1", oppfylt: { drift: "false", sky: true },
      status: "vurderes", funn: [], intervjusporsmal: [] },
  ];
  SVAR = { "/v1/rekruttering/prosesser": data };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  await vent(() => hoved.querySelector("table"));
  const celler = [...hoved.querySelectorAll("tbody tr td")]
    .map((td) => td.textContent);
  assert.equal(celler[1], "2",
    "«false» som streng ga kandidaten drift-vekten på 3");
});

test("Rekruttering: serverens «signert» dreper knappen i en FERSK økt", async () => {
  // Codex P2: `okt.signerte` er ØKTENS hukommelse — den overlever et
  // prosessbytte, ikke en omlasting eller en ny fane. `liste.signert` er
  // seriens signatur-slot lest fra basen, og den ble ikke lest i det hele
  // tatt. En ny økt fikk derfor en handlingsklar Signer-knapp på en serie
  // som ALT er signert, og klikket kunne bare ende i `serien_alt_signert`:
  // flaten lovte en irreversibel handling den ikke kunne levere.
  //
  // MUTASJONEN SOM DREPER DENNE: fjern `liste.signert ||` fra
  // ferdig-merket i `flater/rekruttering.js`.
  KALL = [];
  const data = prosess();
  data.prosesser[0].lister[0].signert = true;
  SVAR = { "/v1/rekruttering/prosesser": data };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());            // fersk økt: `signerte` er tom
  await vent(() => hoved.querySelector("table"));
  const knapp = [...hoved.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.signer_knapp"));
  assert.ok(knapp, "listen forsvant helt — den skal vises, bare død");
  assert.ok(knapp.disabled,
    "en alt signert serie fikk en levende Signer-knapp i en ny økt");
  // …og den døde knappen åpner ikke dialogen, så ingen POST kan oppstå.
  knapp.click();
  await vent(() => KALL.some((k) => k.metode === "POST"), 5);
  assert.equal(document.querySelectorAll('[role="alertdialog"]').length, 0,
    "den døde knappen åpnet signaturdialogen likevel");
  assert.ok(!KALL.some((k) => k.metode === "POST"),
    "flaten sendte en signering på en alt signert serie");
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
  // Codex P1: knappen het «Signer og send» og teksten lovte «Dette sender
  // N e-poster», men signeringen AUTORISERER bare — frigivelsen er
  // `frigi_utsendelse` + en sendejobb, og den benen har ingen
  // produksjonskaller (#151). Dialogen må si begge deler: irreversibel
  // autorisasjon, OG at dette klikket ikke sender noe.
  assert.ok(tekst.includes("sender ingen e-post"),
    "dialogen lover fortsatt en utsendelse dette klikket ikke gjør");
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
  // Flaten har flere alert-regioner (evalueringene står FØRST etter
  // eiers UX-prinsipp) — utfallet måles der det faktisk meldes.
  assert.ok(await vent(() => [...hoved.querySelectorAll('[role="alert"]')]
    .some((a) => a.textContent.includes(HASH.slice(0, 12)))),
    "utfallet mangler");
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
  // #183-formen: indeksen først, den valgte hentes.
  SVAR = svar183(to);
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
  // BYTTE ER EN HENTING (#183): flaten laster ikke lenger alle prosesser
  // med kandidatene sine, så velgeren henter den valgte og tegner NÅR
  // svaret er der. Testen må derfor vente på tegningen i stedet for å
  // lese DOM-en på samme tikk.
  assert.ok(await vent(() => [...hoved.querySelectorAll("tbody tr")]
    .some((tr) => tr.querySelector("td").textContent === "K-9")),
    "prosessbyttet hentet aldri den valgte prosessen");
  // ... og den kunne BARE komme derfra: indeksraden for p-2 bærer ingen
  // kandidater, så K-9 finnes ikke i det første svaret (#183).
  assert.ok(KALL.some((k) =>
    k.url === "/v1/rekruttering/prosesser?prosess_id=p-2"),
    "byttet ba aldri om den valgte prosessen på id");
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

test("Rekruttering: en åpen dialog river ikke prosessen ut under seg",
  async () => {
  // Codex P2: under prosesshentingen er BARE velgeren låst. Resten av
  // flaten lever, så leseren rekker å åpne en kandidatdetalj for
  // prosessen som fortsatt står der. Kom svaret da, tegnet `tegn` hele
  // prosessen om UNDER den åpne dialogen: `aapneDialog` fanget
  // `document.activeElement` som åpner og gir fokus tilbake dit ved
  // lukking, men den knappen er nå en frakoblet node. Tastaturbruk
  // ender uten fokusposisjon — og i signeringstilfellet går
  // bekreftelsen videre gjennom den gamle knappen etter at flaten har
  // byttet prosess.
  //
  // MUTASJONEN SOM DREPER DENNE: fjern `.overlegg`-vakten i
  // suksessarmen.
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", blinding_av: false, vekter: { drift: 1 },
    evaluering_status: "utfort",
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  KALL = [];
  SVAR = svar183(to);
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")));
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  assert.ok(velger, "ingen prosessvelger med flere prosesser i svaret");

  // Byttet HENGER: svaret slippes først når testen vil.
  let slipp;
  const hengende = new Promise((r) => { slipp = r; });
  const indeks = svar183(to);
  SVAR = (sti, opts) => (sti === "/v1/rekruttering/prosesser"
    && opts.method === undefined
    && KALL[KALL.length - 1].url.includes("p-2")
    ? hengende : indeks(sti, opts));
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(await vent(() => velger.disabled),
    "velgeren ble aldri låst — hentingen startet ikke");

  // ... og MENS den henger, åpner leseren en kandidatdetalj.
  const rad = [...hoved.querySelectorAll("tbody tr")]
    .find((tr) => tr.querySelector("td").textContent === "K-2");
  const detaljer = [...rad.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.detaljer"));
  assert.ok(detaljer, "raden mangler detaljknappen");
  detaljer.click();
  const panel = document.querySelector('[role="dialog"]');
  assert.ok(panel, "detaljknappen åpnet ingenting");
  assert.ok(panel.isConnected && detaljer.isConnected,
    "dialogen eller åpneren var alt frakoblet før svaret kom");

  slipp(to);
  // Svaret forkastes: prosessen under dialogen står, og åpneren lever.
  assert.ok(await vent(() =>
    hoved.querySelector(".rekrut-velgerfeil").textContent !== ""),
    "byttet sa ingenting da det ble avvist av den åpne dialogen");
  assert.equal(hoved.querySelector(".rekrut-velgerfeil").textContent,
    t("ui.rekruttering.prosessbytte_utsatt"),
    "avvisningen ble meldt som en vanlig byttefeil");
  assert.ok(detaljer.isConnected,
    "om-tegningen koblet fra knappen dialogen skal gi fokus tilbake til");
  assert.ok(document.querySelector('[role="dialog"]'),
    "dialogen forsvant med prosessen under den");
  const viste = [...hoved.querySelectorAll("tbody tr")]
    .map((tr) => tr.querySelector("td").textContent);
  assert.deepEqual([...viste].sort(), ["K-1", "K-2"],
    "flaten byttet prosess under en åpen dialog");
  assert.ok(!viste.includes("K-9"),
    "p-2s kandidat kom inn under dialogen som eier p-1");
  // Velgeren låses OPP: ingen mutasjons-`finally` venter på å gjøre det,
  // og en kontroll som blir stående låst til omlasting er den samme
  // feilen én etasje ned. Fokus flyttes ikke — det tilhører dialogen.
  assert.equal(velger.disabled, false,
    "velgeren ble stående låst uten at noen kunne løfte den");
  assert.ok(document.activeElement !== velger,
    "avvisningen rev fokus ut av den åpne dialogen");
  document.querySelector(".dialog-lukk").click();
});

test("Rekruttering: 401 under prosessbyttet er innlogging, ikke en byttefeil",
  async () => {
  // Codex: `.catch`-armen på prosesshentingen kastet 401 VIDERE — i enden
  // av en kjede ingen venter på. Det ble en ubehandlet avvisning:
  // `paaUautorisert` ble aldri kalt, velgeren sto igjen låst, og brukeren
  // ble stående i det innloggede skallet uten økt bak seg og uten et ord
  // om hvorfor. Samme tilstand `meldFeil` alt er felt over én gang for
  // mutasjonene; byttet er ikke et unntak fra V2 (401 → innlogging).
  //
  // MUTASJONEN SOM DREPER DENNE: sett `throw e` tilbake i armen.
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", blinding_av: false, vekter: { drift: 1 },
    evaluering_status: "utfort", kandidater: [], lister: [],
  });
  KALL = [];
  SVAR = svar183(to);
  let uautorisert = 0;
  const hoved = nyHoved();
  visRekruttering(hoved,
    { ...ctx(), paaUautorisert: () => { uautorisert += 1; } });
  assert.ok(await vent(() => hoved.querySelector("table")));
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  assert.ok(velger, "ingen prosessvelger med flere prosesser i svaret");
  // … og HER dør økten, mellom lastingen av flaten og byttet.
  KALL = [];
  SVAR = (sti) => (sti === "/v1/rekruttering/prosesser" ? 401 : undefined);
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(await vent(() => uautorisert === 1),
    "401 under prosessbyttet nådde aldri paaUautorisert");
  assert.ok(KALL.some((k) => k.sti === "/v1/rekruttering/prosesser"),
    "byttet hentet aldri — testen måler ikke det den tror");
  assert.ok([...hoved.querySelectorAll('[role="alert"]')]
    .every((a) => a.textContent === ""),
    "401 ble meldt som en vanlig prosessbyttefeil");
});

test("Rekruttering: velgeren navngir prosessen, aldri bare UUID-en", async () => {
  // Codex P2 (runde 4): velgeren leste `p.navn || p.prosess_id`, men
  // serveren har aldri sendt `navn` — stillingens tittel bor i #162-kjeden
  // og finnes ikke å hente ennå. Med flere prosesser måtte brukeren derfor
  // velge mellom rå UUID-er FØR hun kunne lese kandidater eller signere en
  // irreversibel utsendelse. Starttidspunktet er det som finnes i
  // klartekst, og det skiller prosessene fra hverandre for et menneske.
  //
  // MUTASJONEN SOM DREPER DENNE: sett `p.navn || p.prosess_id` tilbake.
  const to = prosess();
  to.prosesser[0].opprettet = "2026-08-24T06:10:00+00:00";
  to.prosesser.push({
    prosess_id: "p-2", opprettet: "2026-08-20T09:00:00+00:00",
    blinding_av: false, vekter: { drift: 1 },
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  KALL = [];
  SVAR = svar183(to);
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")));
  const tekster = [...hoved.querySelector("#rekrut-prosessvelger").options]
    .map((o) => o.textContent);
  assert.ok(!tekster.some((x) => x === "p-1" || x === "p-2"),
    `velgeren tilbyr fortsatt rå id-er: ${JSON.stringify(tekster)}`);
  // Etiketten er husets datoform + antallet, og de to oppføringene skiller
  // seg fra hverandre — det er hele poenget med å ha en etikett.
  assert.equal(tekster[1], t("ui.rekruttering.prosessetikett")
    .replaceAll("{dato}", Tidspunkt("2026-08-20T09:00:00+00:00").textContent)
    .replaceAll("{antall}", "1"));
  assert.notEqual(tekster[0], tekster[1]);
  // …og den dagen #162 gir tittelen, vinner den uten at flaten røres —
  // også på INDEKSRADEN, som er der de øvrige prosessene bor (#183).
  to.prosesser[1].navn = "Sykepleier vest";
  SVAR = svar183(to);
  KALL = [];
  const medNavn = nyHoved();
  visRekruttering(medNavn, ctx());
  assert.ok(await vent(() => medNavn.querySelector("table")));
  assert.ok([...medNavn.querySelector("#rekrut-prosessvelger").options]
    .some((o) => o.textContent === "Sykepleier vest"),
    "navnet fra serveren tapte mot tidsstempelet");
});

test("Velgeretiketten teller INDEKSENS kandidater, ikke den valgtes liste",
  async () => {
  // Cursor P2 (#183): etter #183 bærer bare den VALGTE prosessen
  // `kandidater`; de øvrige oppføringene er en lett indeks. Etiketten
  // leste `(p.kandidater || []).length` og sa derfor ALLTID «kandidater:
  // 0» om dem — om en prosess som kan bære tusenvis. Det er ikke en
  // manglende opplysning, det er en gal en, på den ene kontrollen som
  // skiller prosessene fra hverandre foran en irreversibel signering.
  //
  // MUTASJONEN SOM DREPER DENNE: les `(p.kandidater || []).length` igjen.
  const svar = { valgt_prosess_id: "p-1", prosesser: [
    { ...prosess().prosesser[0], kandidat_antall: 2 },
    // Indeksraden: serverens #183-form — ingen `kandidater`, men et tall.
    { prosess_id: "p-2", opprettet: "2026-08-20T09:00:00+00:00",
      evaluering_status: "utfort", kandidat_antall: 1743 },
  ] };
  svar.prosesser[0].opprettet = "2026-08-24T06:10:00+00:00";
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": svar };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")));
  const tekster = [...hoved.querySelector("#rekrut-prosessvelger").options]
    .map((o) => o.textContent);
  assert.equal(tekster[1], t("ui.rekruttering.prosessetikett")
    .replaceAll("{dato}", Tidspunkt("2026-08-20T09:00:00+00:00").textContent)
    .replaceAll("{antall}", "1743"),
    `indeksraden lyver om antallet: ${JSON.stringify(tekster)}`);
  // Og den VALGTE raden teller det samme tallet, ikke to ulike sannheter.
  assert.equal(tekster[0], t("ui.rekruttering.prosessetikett")
    .replaceAll("{dato}", Tidspunkt("2026-08-24T06:10:00+00:00").textContent)
    .replaceAll("{antall}", "2"));
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

test("Rekruttering: et tapt svar meldes som uvisst, ikke som «ingenting er sendt»", async () => {
  // Codex P1: `meldFeil` sa den DEFINITIVE setningen «Handlingen ble
  // avvist. Ingenting er sendt.» også ved status 0 (fetch nådde aldri
  // fram, eller svaret gikk tapt etter at serveren commitet) og ved 5xx,
  // der commit-status er ukjent. For en irreversibel utsendelse er det
  // falsk trygghet: brukeren kan gå fra skjermen i den tro at ingen
  // e-post gikk ut. Bare 4xx er serverens avvisning FØR commit.
  //
  // MUTASJONEN SOM DREPER DENNE: la `meldFeil` melde `feil_utfall` for
  // alt som ikke er 401.
  const hoved = await tegnet();
  const knapp = [...hoved.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.signer_knapp"));
  const melding = () => [...hoved.querySelectorAll('[role="alert"]')]
    .map((a) => a.textContent).join("");
  const signer = async () => {
    assert.ok(await vent(() => !knapp.disabled), "knappen ble aldri åpen");
    knapp.click();
    [...document.querySelector('[role="alertdialog"]')
      .querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.rekruttering.signer_bekreft"))
      .click();
  };
  // 5xx: serveren kan ha commitet før den røk.
  SVAR = (sti, opts) => (opts.method === "POST" ? 500 : prosess());
  await signer();
  assert.ok(await vent(() =>
    melding() === t("ui.rekruttering.usikkert_utfall")),
    "5xx ble meldt som et definitivt avslag");
  // Transporten som aldri kom fram (status 0) er samme uvisshet.
  SVAR = (sti, opts) => {
    if (opts.method === "POST") throw new TypeError("failed to fetch");
    return prosess();
  };
  await signer();
  assert.ok(await vent(() =>
    melding() === t("ui.rekruttering.usikkert_utfall")),
    "en tapt transport ble meldt som et definitivt avslag");
  // …og 4xx ER serverens egen avvisning før commit: da SKAL setningen
  // være definitiv, ellers er den nye meldingen bare støy.
  SVAR = (sti, opts) => (opts.method === "POST" ? 422 : prosess());
  await signer();
  assert.ok(await vent(() =>
    melding() === t("ui.rekruttering.feil_utfall")),
    "et 4xx-avslag ble meldt som uvisst");
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
  const bytt = async (id) => {
    const velger = hoved.querySelector("#rekrut-prosessvelger");
    velger.value = id;
    velger.dispatchEvent(new window.Event("change", { bubbles: true }));
    // #183: byttet henter. Vent til flaten faktisk står på den nye
    // prosessen — ellers måler testen tilstanden FØR byttet.
    await vent(() => {
      const v = hoved.querySelector("#rekrut-prosessvelger");
      return v && v.value === id && !v.disabled;
    });
  };

  // Første forsøk på p-1: svaret går tapt.
  await signer();
  assert.ok(await vent(() => KALL.some((k) => k.sti.endsWith("/signer"))));
  const forste = KALL.find((k) => k.sti.endsWith("/signer"));
  assert.ok(forste.hoder["Idempotency-Key"], "signeringen gikk uten nøkkel");

  // Brukeren ser innom den andre prosessen før hun prøver igjen.
  await bytt("p-2");
  assert.equal(signerKnapp(), undefined, "p-2 har ingen lister å signere");
  await bytt("p-1");
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
  await bytt("p-2");
  await bytt("p-1");
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

test("Rekruttering: signeringens kvittering overlever et prosessbytte",
  async () => {
  // Codex P2 (runde 10): bytter brukeren prosess etter at hun bekreftet
  // signeringen, men FØR POST-en er besvart, tegner `tegn` et nytt
  // utfallsområde — mens tilbakekallingen fortsatt lukker om det gamle.
  // Meldingen ble skrevet til en frakoblet node: ingenting vist,
  // ingenting kunngjort, for den ene handlingen som ikke kan gjøres om.
  //
  // MUTASJONEN SOM DREPER DENNE: bytt `meldUtfall(hoved, okt, ...)` i
  // signeringens `try` tilbake mot `sett(utfall, ...)`.
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", navn: "Sykepleier vest", blinding_av: false,
    vekter: { drift: 1 }, vekter_kilde: "evalueringsartefakt",
    evaluering_status: "utfort",
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  // POST-en henger til testen slipper den: nøyaktig vinduet funnet
  // handler om. `json()` venter på løftet, så svaret kommer ETTER byttet.
  let losPost;
  const iLufta = new Promise((los) => { losPost = los; });
  KALL = [];
  SVAR = (sti, opts) => (opts.method === "POST" ? iLufta : to);
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")));
  const bytt = async (id) => {
    const velger = hoved.querySelector("#rekrut-prosessvelger");
    velger.value = id;
    velger.dispatchEvent(new window.Event("change", { bubbles: true }));
    // #183: byttet henter — vent til den nye prosessen står tegnet.
    await vent(() => {
      const v = hoved.querySelector("#rekrut-prosessvelger");
      return v && v.value === id && !v.disabled;
    });
  };
  const knapp = [...hoved.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.signer_knapp"));
  assert.ok(await vent(() => !knapp.disabled), "knappen ble aldri åpen");
  knapp.click();
  [...document.querySelector('[role="alertdialog"]')
    .querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.signer_bekreft"))
    .click();
  assert.ok(await vent(() => KALL.some((k) => k.metode === "POST")),
    "signeringen ble aldri sendt");

  const gammel = hoved.querySelector(".rekrut-utfall");
  await bytt("p-2");
  assert.notEqual(hoved.querySelector(".rekrut-utfall"), gammel,
    "prosessbyttet tegnet ikke et nytt utfallsområde — testen måler ikke"
    + " lenger vinduet den ble skrevet for");
  assert.equal(gammel.isConnected, false, "den gamle noden henger igjen");

  losPost({ innhold_hash: HASH });
  const ventet = t("ui.rekruttering.signer_utfall")
    .replaceAll("{hash}", `${HASH.slice(0, 12)}…`);
  assert.ok(await vent(() =>
    hoved.querySelector(".rekrut-utfall").textContent === ventet),
    "kvitteringen for en irreversibel signering ble skrevet til en"
    + " frakoblet node");
  // …og den blir stående når brukeren går tilbake: økten bærer den.
  await bytt("p-1");
  assert.equal(hoved.querySelector(".rekrut-utfall").textContent, ventet,
    "kvitteringen forsvant ved neste tegning");
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
  // Ingen intervjuspørsmål i utvelgelsen (eiers produktbeslutning,
  // #224/#225): selv når payloaden bærer dem, rendres de ikke.
  assert.ok(!panel.textContent.includes("Fortell om tidslinjen."),
    "intervjuspørsmål skal ikke vises i detaljpanelet");
});

test("Rekruttering: et funn uten sitat åpner panelet og skjules ikke", async () => {
  // Cursor P2: `f.kilde.sitat` uten vern. Skriveveien krever `kilde` på
  // hvert funn, men runtime har INSERT på artefaktlageret, så formen kan
  // faktisk komme inn — og da kastet oppbyggingen TypeError: dialogen
  // åpnet ALDRI, og raden satt igjen med en «Detaljer»-knapp som ikke
  // svarte. Nøyaktig den flyten tastaturgjennomgangens punkt 4 lover.
  //
  // Og funnet skal ikke gjemmes bort for å slippe unna: kategorien ER
  // risikoopplysningen foran en irreversibel utsendelse. Panelet sier at
  // belegget mangler — på locale-båret tekst, aldri en rå nøkkel.
  //
  // MUTASJONEN SOM DREPER DENNE: skriv `el("q", { text: f.kilde.sitat })`
  // tilbake. Byttes plassholderen mot å droppe funnet, faller den også.
  const uten = prosess();
  uten.prosesser[0].kandidater[0].funn = [
    { kategori: "uklar_tidslinje" },                 // ingen `kilde`
    { kategori: "manglende_dokumentasjon", kilde: {} },  // kilde uten sitat
    { kategori: "motstridende_opplysning", kilde: { sitat: "2019" } },
  ];
  SVAR = { "/v1/rekruttering/prosesser": uten };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  await vent(() => hoved.querySelector("table"));
  const rad = [...hoved.querySelectorAll("tbody tr")]
    .find((tr) => tr.querySelector("td").textContent === "K-2");
  [...rad.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.detaljer")).click();
  const panel = document.querySelector('[role="dialog"]');
  assert.ok(panel, "detaljknappen åpnet ingenting");
  // Alle tre funnene står der — de to uten belegg også.
  for (const kategori of ["uklar_tidslinje", "manglende_dokumentasjon",
    "motstridende_opplysning"]) {
    assert.ok(panel.textContent.includes(t(`ui.rekruttering.funn.${kategori}`)),
      `funnet ${kategori} forsvant fra panelet`);
  }
  const plassholdere = [...panel.querySelectorAll("em")]
    .map((e) => e.textContent);
  assert.deepEqual(plassholdere,
    [t("ui.rekruttering.uten_sitat"), t("ui.rekruttering.uten_sitat")],
    "de manglende sitatene ble ikke sagt fra om");
  assert.ok(!plassholdere[0].startsWith("ui."),
    "plassholderen falt til reservenøkkelen — locale mangler");
  // …og det sitatet som FINNES står fortsatt som sitat.
  assert.deepEqual([...panel.querySelectorAll("q")].map((q) => q.textContent),
    ["2019"], "det ekte kildesitatet forsvant");
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

test("Rekruttering: in-flight-lås — ingen andre mutasjon mens den første henger", async () => {
  // Cursor P2: dialogen lukkes ved bekreftelse og knappene sto åpne, så
  // et nytt klikk mens forrige POST hang ga to samtidige kall på den
  // irreversible signeringen. (Blindingshalvdelen av denne testen falt
  // med mutasjonsbenet i runde 4 — se «tilstandsmerke, ikke et valg».)
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
  } finally {
    globalThis.fetch = ekte;
  }
});

test("Rekruttering: 401 i mutasjonene er innlogging, ikke en handlingsfeil", async () => {
  // Codex P1: en utløpt økt ble fanget som «noe gikk galt», og brukeren
  // ble stående i det innloggede skallet uten økt bak seg. 401 er global
  // i resten av klienten (V2: 401 → innlogging, 403 → ingen tilgang).
  for (const flyt of ["signer"]) {
    KALL = [];
    SVAR = { "/v1/rekruttering/prosesser": prosess() };
    let uautorisert = 0;
    const hoved = nyHoved();
    visRekruttering(hoved, { sprak: "nb", tenant: "acme",
      scopes: ["decisions:read", "bestilling:opprett"],
      paaUautorisert: () => { uautorisert += 1; } });
    await vent(() => hoved.querySelector("table"));
    SVAR = (sti, opts) => (opts.method === "POST" ? 401 : prosess());
    [...hoved.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.rekruttering.signer_knapp"))
      .click();
    const dialog = document.querySelector('[role="alertdialog"]');
    [...dialog.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.rekruttering.signer_bekreft"))
      .click();
    assert.ok(await vent(() => uautorisert === 1),
      `${flyt}: 401 nådde aldri paaUautorisert`);
    assert.ok([...hoved.querySelectorAll('[role="alert"]')]
      .every((a) => a.textContent === ""),
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
  // …OG DEN PÅSTÅR VERKEN MER ELLER MINDRE ENN DEN MÅLTE (Cursor P2, og
  // P2 igjen på #176). Doket beskrev en gang «`Tab` fra menyen →
  // Rekruttering» som observert mens `sitekart` holdt ruten ute: flyten
  // var umulig å gå, og «observert» om noe ingen kan gjøre er falsk
  // evidens for port 32 — verre enn ingen gjennomgang, fordi det ser ut
  // som dekning. Nå er ruten inne, og doket skal si DET.
  //
  // Kravet henger på rutetabellen selv, ikke på en liste her, så det
  // følger `byggRuter` i BEGGE retninger: rulles ruten ut igjen uten at
  // doket følger med, faller testen — og motsatt.
  //
  // MUTASJONEN SOM DREPER DENNE: la doket stå på UTESTÅENDE mens ruten
  // er inne (eller sett menyraden tilbake som observert etter en
  // utrulling).
  const ruter = byggRuter({ scopes: ["decisions:read", "exceptions:read",
    "policy:read", "bestilling:opprett", "domains:adjudicate"] })
    .map((r) => r.nokkel);
  const menyraden = dok.split("\n").find((l) => /^\| 1 \|/.test(l));
  assert.ok(menyraden, "menyflyten (rad 1) står ikke i flyttabellen");
  if (ruter.includes("rekruttering")) {
    assert.ok(/\*\*PORTET\*\*/.test(menyraden),
      "ruten er inne, men doket holder menyflyten som ikke gjennomgått");
    assert.ok(!/ikke i sitekartet/i.test(dok),
      "doket påstår fortsatt at ruten er ute av sitekartet");
  } else {
    assert.ok(/ikke i sitekartet/i.test(dok),
      "doket sier ikke fra om at ruten er ute, men beskriver menyveien");
    assert.ok(/\*\*UTESTÅENDE\*\*/.test(menyraden),
      "menyflyten står som gjennomgått mens ruten er stengt");
  }
});

test("Rekruttering: hver signeringsknapp har sitt eget tilgjengelige navn (port 29)", async () => {
  // Codex P2: med to utsendingslister sto to knapper med IDENTISK
  // tilgjengelig navn — «Signer og send» — mens listetypen, antallet og
  // hashen lå som søskentekst i raden, som ikke inngår i knappens navn. En
  // skjermleserbruker som navigerer knapp for knapp, kunne ikke avgjøre
  // hvilken irreversibel utsendelse hun sto på før dialogen var åpen.
  //
  // MUTASJONEN SOM DREPER DENNE: fjern `aria-label` fra knappen i
  // `rekruttering.js` — navnene faller tilbake til knappeteksten og blir like.
  KALL = [];
  const data = prosess();
  data.prosesser[0].lister.push({ liste_id: "L-2", listetype: "avslag",
    antall: 7, innhold_hash: "f".repeat(64) });
  SVAR = { "/v1/rekruttering/prosesser": data };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "tabellen kom aldri");

  const knapper = [...hoved.querySelectorAll(".rekrut-liste button")];
  assert.equal(knapper.length, 2, "begge listene skal ha en signeringsknapp");
  const navn = knapper.map((b) => b.getAttribute("aria-label"));
  assert.equal(new Set(navn).size, 2,
    `to irreversible handlinger deler tilgjengelig navn: ${navn.join(" / ")}`);
  // Navnet bærer det raden viser med øyet: type, antall og hashkortform.
  assert.ok(navn[0].includes(t("ui.rekruttering.listetype.invitasjon")));
  assert.ok(navn[0].includes("42"));
  assert.ok(navn[0].includes(HASH.slice(0, 12) + "…"), "hashkortformen mangler");
  assert.ok(navn[1].includes(t("ui.rekruttering.listetype.avslag")));
  assert.ok(navn[1].includes("7"));
  // Ingen fullhash, og ingen plassholder som overlevde innsettingen.
  for (const n of navn) {
    assert.ok(!n.includes(HASH), "fullhashen skal ikke ut i navnet");
    assert.ok(!/\{[a-zæøå_]+\}/.test(n), `plassholder står igjen: ${n}`);
  }
  // Den synlige teksten står fortsatt der: `aria-label` supplerer cellen,
  // den erstatter den ikke, og knappeteksten er uendret for øyet.
  assert.equal(knapper[0].textContent, t("ui.rekruttering.signer_knapp"));
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Rekruttering: hver detaljknapp har sitt eget tilgjengelige navn (port 29)", async () => {
  // Codex P2: samme klasse som signeringsknappene, én rad ned. Hver rad
  // bærer knappeteksten «Detaljer», og kandidat-id-en står i SØSKENCELLEN
  // — som ikke inngår i knappens tilgjengelige navn. En skjermleserbruker
  // som navigerer knapp for knapp fikk derfor N identiske «Detaljer» og
  // ingen måte å vite hvilken kandidat hun åpnet.
  //
  // MUTASJONEN SOM DREPER DENNE: fjern `tilgjengeligNavn` fra radhandlingen
  // i `rekruttering.js` — navnene faller tilbake til knappeteksten og blir
  // like.
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": prosess() };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "tabellen kom aldri");

  const knapper = [...hoved.querySelectorAll("tbody .handling-celle button")];
  assert.equal(knapper.length, 2, "begge kandidatradene skal ha en knapp");
  const navn = knapper.map((b) => b.getAttribute("aria-label"));
  assert.equal(new Set(navn).size, 2,
    `to radhandlinger deler tilgjengelig navn: ${navn.join(" / ")}`);
  // Navnet bærer kandidat-id-en raden viser med øyet — og radene står i
  // rangert rekkefølge, så navnet må følge SIN rad, ikke fikstureringen.
  for (const rad of hoved.querySelectorAll("tbody tr")) {
    const id = rad.querySelector("td").textContent;
    const knapp = rad.querySelector(".handling-celle button");
    assert.ok(knapp.getAttribute("aria-label").includes(id),
      `knappen på raden for ${id} navngir ikke kandidaten`);
  }
  // Den synlige teksten står fortsatt der: `aria-label` supplerer cellen.
  assert.equal(knapper[0].textContent, t("ui.rekruttering.detaljer"));
  // … og knappen gjør fortsatt jobben sin: detaljpanelet åpnes.
  knapper[0].click();
  assert.ok(await vent(() => document.querySelector(".dialog.skuff")),
    "detaljpanelet åpnet ikke");
  const panel = document.querySelector(".dialog.skuff");
  assert.ok(panel.textContent.includes(knapper[0].closest("tr")
    .querySelector("td").textContent),
    "detaljpanelet viser en annen kandidat enn knappen navnga");
  panel.querySelector(".dialog-lukk").click();

  const brudd2 = await alvorligeBrudd(hoved);
  assert.equal(brudd2.length, 0, beskrivBrudd(brudd2));
});

// ------------------------------------------------------------------
// Stillingsprofil-editoren (#189).

function profiler() {
  return { profiler: [{
    profil_id: "prof-1", versjon: 2, navn: "Driftskonsulent",
    opprettet: "2026-08-25T10:00:00Z", opprettet_av: "b-1",
    krav: [{ kravnavn: "Drift", vekt: 3 }, { kravnavn: "Norsk", vekt: 1 }],
  }] };
}

async function tegnetMedProfiler() {
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": prosess(),
           "/v1/rekruttering/stillingsprofiler": profiler(),
           "/v1/rekruttering/evalueringer": { evalueringer: [] } };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")),
    "flaten kom aldri");
  return hoved;
}

test("Evalueringer: liste med status, og rapporten rendres blindet", async () => {
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true },
      { oppdrag_id: 97, status: "opprettet",
        opprettet: "2026-08-27T07:00:00+00:00", rapport_klar: false },
      { oppdrag_id: 95, status: "feilet",
        opprettet: "2026-08-26T22:00:00+00:00", rapport_klar: false },
      { oppdrag_id: 90, status: "utfort",
        opprettet: "2026-08-20T10:00:00+00:00", rapport_klar: false,
        slettet: true },
      { oppdrag_id: 89, status: "kansellert",
        opprettet: "2026-08-19T10:00:00+00:00", rapport_klar: false },
      { oppdrag_id: 88, status: "utfort",
        opprettet: "2026-08-18T10:00:00+00:00", rapport_klar: false },
    ], flere: true },
    "/v1/rekruttering/rapport/96": { oppdrag_id: 96, rapport: {
      rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
      profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
      antall_soknader: 2,
      rangering: [
        { kandidat_id: "kandidat-01", poeng: 5,
          nedbrytning: { drift: 3, sky: 2 } },
        { kandidat_id: "kandidat-02", poeng: 3,
          nedbrytning: { drift: 3, sky: 0 } },
      ],
      kandidater: {
        "kandidat-01": { funn: [{ kategori: "uklar_tidslinje",
          kilde: { start: 0, slutt: 9, sitat: "[NAVN-1] har" } },
          { kategori: "manglende_dokumentasjon" }],   // uten `kilde`
          intervjusporsmal: ["Fortell om driftserfaringen."],
          kildetekst: "[NAVN-1] har drift" },
        "kandidat-02": { funn: [], intervjusporsmal: [],
          kildetekst: "[NAVN-2] litt sky" },
      },
      fremdrift: { filer_lest: 2, filer_totalt: 2, byte_lest: 100 },
    } } };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]")), "seksjonen kom aldri");
  const seksjon = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  const tekst = seksjon.textContent;
  assert.match(tekst, /96/);
  assert.match(tekst, new RegExp(t("ui.rekruttering.evalueringer.klar")));
  assert.match(tekst, new RegExp(t("ui.rekruttering.evalueringer.venter")));
  // Terminal status er sin egen sannhet — aldri "under arbeid".
  assert.match(tekst, new RegExp(t("ui.rekruttering.evalueringer.feilet")));
  // En reapet evaluering er navngitt slettet — ikke «under arbeid» i
  // det uendelige, og uten Vis-knapp (rapport_klar er false).
  assert.match(tekst,
    new RegExp(t("ui.rekruttering.evalueringer.slettet")));
  // … og kansellert-stien er bevist, ikke bare skrevet (pass-P3).
  assert.match(tekst,
    new RegExp(t("ui.rekruttering.evalueringer.kansellert")));
  // Et utfort oppdrag uten lesbar rapport (intet anker) er
  // «utilgjengelig» — aldri «under arbeid» for alltid (Codex P2).
  assert.match(tekst,
    new RegExp(t("ui.rekruttering.evalueringer.utilgjengelig")));
  // Fullt vindu: avkortingen SIES, aldri stille (Cursor P2-3; #221 tar
  // selve pagineringen).
  assert.match(tekst,
    new RegExp(t("ui.rekruttering.evalueringer.flere").slice(0, 25)));
  // Kun den ferdige raden har en Vis-knapp.
  const knapper = [...seksjon.querySelectorAll(".rekrut-kortliste button")]
    .filter((b) => b.textContent === t("ui.rekruttering.evalueringer.vis"));
  assert.equal(knapper.length, 1);
  knapper[0].click();
  assert.ok(await vent(() => seksjon.textContent.includes("Driftskonsulent")),
    "rapporten rendret aldri");
  const etter = seksjon.textContent;
  assert.match(etter, /kandidat-01/);
  // FLATEN SNAKKER NORSK, OGSÅ HER (RUTINER §5, Cursor P2). Rapporten
  // rendret rå maskinkoder — kravnøklene i nedbrytningen — mens
  // prosesspanelet i samme fil oversatte dem.
  assert.match(etter, new RegExp(`${t("ui.rekruttering.krav.drift")}: 3`));
  assert.doesNotMatch(etter, /drift:/);
  // Fokus flyttes til rapportoverskriften — tastatur og skjermleser skal
  // få vite at lastingen ble ferdig (Codex P2).
  const overskrift = seksjon.querySelector("h3[tabindex='-1']");
  assert.ok(overskrift && overskrift.textContent.includes("Driftskonsulent"),
    "rapportoverskriften mangler");
  assert.equal(seksjon.ownerDocument.activeElement, overskrift,
    "fokus ble ikke flyttet til rapporten");
  // Detaljkroppen bygges LAT (Codex P2: skjemaet tillater 5000 kandidater
  // à 100 funn) — før åpning finnes verken funn eller spørsmål i DOM.
  assert.doesNotMatch(etter, /\[NAVN-1\] har/);
  assert.doesNotMatch(etter, /Fortell om driftserfaringen/);
  // Radenes funn-detalj, ikke vekterfolden (eiers mobilfunn 30/8 la
  // vektene i sin egen <details> foran tabellen).
  const boks = seksjon.querySelector("tbody details");
  boks.open = true;
  boks.dispatchEvent(new (seksjon.ownerDocument.defaultView.Event)("toggle"));
  const aapnet = seksjon.textContent;
  assert.match(aapnet, new RegExp(t("ui.rekruttering.funn.uklar_tidslinje")));
  assert.doesNotMatch(aapnet, /uklar_tidslinje/);
  assert.match(aapnet, /\[NAVN-1\] har/);       // sitatet, blindet form
  // Et funn uten `kilde` beholdes med plassholder (speilet fra
  // prosesspanelet) — det skal aldri velte åpningen av detaljene.
  assert.match(aapnet,
    new RegExp(t("ui.rekruttering.funn.manglende_dokumentasjon")));
  assert.match(aapnet, new RegExp(t("ui.rekruttering.uten_sitat")));
  // Ingen intervjuspørsmål i rangeringen (eiers produktbeslutning):
  // selv om payloaden skulle bære dem, rendres de ikke.
  assert.doesNotMatch(aapnet, /Fortell om driftserfaringen/);
  // Begge tabellene står i rullbar container (Codex P2: 50 krav à 120
  // tegn i nedbrytningscellen skal ikke velte siden).
  // Listen er kort, ikke tabell — de rullbare tabellene er RAPPORTENS
  // (rangeringen og nedbrytningen), og begge skal stå i .tablewrap.
  assert.ok(seksjon.querySelectorAll(".tablewrap table").length >= 1,
    "rapporttabellen mangler .tablewrap");
  // Poengfordelingen bor i DETALJEN, ikke i en egen kolonne (eiers
  // funn 30/8: linjen tok halve mobilskjermen per rad) — og den finnes.
  assert.ok(seksjon.querySelector(".rekrut-detalj .rekrut-fordeling"),
    "poengfordelingen forsvant fra funn-detaljen");
  assert.ok(!etter.includes(t("ui.rekruttering.evalueringer.nedbrytning")),
    "nedbrytningskolonnen er tilbake i tabellen — mobilplassen er borte");
  assert.match(etter,
    new RegExp(t("ui.rekruttering.evalueringer.blindet").slice(0, 20)));
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Evalueringer: feilet rapporthenting melder i alert, ikke stille", async () => {
  KALL = [];
  // Første klikk lykkes; deretter svarer ruta 500 — den gamle rapporten
  // skal da IKKE bli stående under en feilmelding som gjelder en annen.
  let rapportSvar = { oppdrag_id: 96, rapport: {
    rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
    profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
    antall_soknader: 1,
    rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
      nedbrytning: { drift: 5 } }],
    kandidater: { "kandidat-01": { funn: [], intervjusporsmal: [],
      kildetekst: "[NAVN-1]" } },
    fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
  } };
  SVAR = (sti) => ({
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true },
      { oppdrag_id: 97, status: "utfort",
        opprettet: "2026-08-27T01:00:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/96": rapportSvar,
    "/v1/rekruttering/rapport/97": rapportSvar,
  })[sti] ?? 500;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => [...hoved.querySelectorAll(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste button")]
    .filter((b) => b.textContent
      === t("ui.rekruttering.evalueringer.vis")).length === 2));
  const seksjon = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  const knappFor = (id) => [...seksjon.querySelectorAll(".rekrut-kortliste button")]
    .filter((b) => b.textContent === t("ui.rekruttering.evalueringer.vis"))
    .find((b) => b.closest(".rekrut-kort")
      && b.closest(".rekrut-kort").textContent.includes(String(id)));
  knappFor(96).click();
  assert.ok(await vent(() => seksjon.textContent.includes("Driftskonsulent")),
    "rapporten rendret aldri");
  rapportSvar = undefined; // ?? 500 tar over — 97 er IKKE i cachen
  knappFor(97).click();
  await vent(() => seksjon.querySelector("[role=alert]").textContent
    === t("ui.rekruttering.evalueringer.rapportfeil"));
  assert.equal(seksjon.querySelector("[role=alert]").textContent,
    t("ui.rekruttering.evalueringer.rapportfeil"));
  assert.ok(!seksjon.textContent.includes("Driftskonsulent"),
    "den gamle rapporten ble stående etter feilet henting");
  // Negativ kontroll for `flere`-merknaden: uten flagget finnes den ikke.
  assert.doesNotMatch(seksjon.textContent,
    new RegExp(t("ui.rekruttering.evalueringer.flere").slice(0, 25)));
});

test("Evalueringer: 200 med urendrbar rapport lander i alert, ikke tom seksjon", async () => {
  KALL = [];
  // Ruta svarer 200, men kroppen mangler `rangering` — nøyaktig den
  // «200-og-feiler-under-rendring»-klassen diskriminatorportene verner
  // serversiden mot. Rendringen skjer etter at `utfall` og rapport-roten
  // er tømt: uten vakt blir det en STILLE tom seksjon uten role="alert".
  SVAR = (sti) => ({
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/96": { oppdrag_id: 96, rapport: null },
  })[sti] ?? 500;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste button")));
  const seksjon = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  seksjon.querySelector(".rekrut-kortliste button").click();
  assert.ok(await vent(() => seksjon.querySelector("[role=alert]").textContent
    === t("ui.rekruttering.evalueringer.rapportfeil")),
    "urendrbar rapport ga ingen feilmelding — seksjonen ble stille tom");
  // Ingen halv DOM: en delvis bygget rapport ser ekte ut for leseren.
  assert.equal(seksjon.querySelectorAll("table").length, 0,
    "rapporttabellen ble stående halvbygget ved siden av feilmeldingen");
  assert.doesNotMatch(seksjon.textContent,
    new RegExp(t("ui.rekruttering.evalueringer.blindet").slice(0, 20)));
});

test("Evalueringer: «Last flere» følger serverens cursor og appender (#221)", async () => {
  KALL = [];
  // Side 1 bærer `neste_cursor`; klikket henter side 2 MED cursoren og
  // APPENDER — brukeren mister ikke radene hen ser på. Side 2 uten
  // cursor: knappen forsvinner, ingen «flere»-melding står igjen.
  // Objektform: URL-nøkkelen (med spørrestreng) vinner over sti-nøkkelen
  // — riggens egen vei for kall der cursoren velger svaret.
  SVAR = {
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": {
      evalueringer: [{ oppdrag_id: 200, status: "utfort",
        opprettet: "2026-08-27T02:00:00+00:00", rapport_klar: false }],
      flere: true, neste_cursor: "c-side2.mac" },
    "/v1/rekruttering/evalueringer?cursor=c-side2.mac": {
      evalueringer: [{ oppdrag_id: 100, status: "utfort",
        opprettet: "2026-08-27T01:00:00+00:00", rapport_klar: false }],
      flere: false, neste_cursor: null },
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste")));
  const seksjon = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  const flereKnapp = () => [...seksjon.querySelectorAll("button")]
    .find((b) => b.textContent
      === t("ui.rekruttering.evalueringer.last_flere"));
  assert.ok(flereKnapp(), "cursoren ga ingen «Last flere»-knapp");
  // Cursoren erstatter den passive meldingen — aldri begge.
  assert.doesNotMatch(seksjon.textContent,
    new RegExp(t("ui.rekruttering.evalueringer.flere").slice(0, 25)));
  flereKnapp().click();
  assert.ok(await vent(() => seksjon.textContent.includes("100")),
    "side 2 ble aldri appendet");
  assert.ok(seksjon.textContent.includes("200"),
    "appenderingen mistet raden brukeren alt så på");
  assert.ok(KALL.some((k) =>
    k.url === "/v1/rekruttering/evalueringer?cursor=c-side2.mac"),
  "klikket fulgte ikke serverens cursor");
  assert.ok(await vent(() => !flereKnapp()),
    "knappen ble stående uten fortsettelse å følge");
});

test("Evalueringer: fokus OVERLEVER om-tegningen av listen, og redrawet "
  + "annonseres", async () => {
  KALL = [];
  // Codex P2: `tegnListe` bytter hele seksjonen med `sett(rot, …)`, så
  // knappen brukeren nettopp aktiverte er en ANNEN node etterpå — fokus
  // falt til `document.body` ved hver oppfriskning og hver lastede side,
  // uten noe som sa hvor de nye radene havnet.
  const rad = (id) => ({ oppdrag_id: id, status: "opprettet",
    opprettet: "2026-08-27T02:00:00+00:00", rapport_klar: false });
  SVAR = {
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": {
      evalueringer: [rad(200)], flere: true, neste_cursor: "c-side2.mac" },
    "/v1/rekruttering/evalueringer?cursor=c-side2.mac": {
      evalueringer: [rad(100)], flere: false, neste_cursor: null },
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste")));
  const seksjon = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  const knapp = (nokkel) => [...seksjon.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.evalueringer." + nokkel));
  const live = () => document.querySelector(
    '[role=status][aria-live=polite]').textContent;
  const meldt = (antall) => t("ui.rekruttering.evalueringer.listemeldt")
    .replace("{antall}", String(antall));

  // «Oppdater»: erstatningsknappen får fokus, ikke `document.body`.
  const gammel = knapp("oppdater");
  gammel.focus();
  gammel.click();
  assert.ok(await vent(() => knapp("oppdater") !== gammel),
    "listen ble aldri tegnet på nytt");
  assert.ok(await vent(() => document.activeElement === knapp("oppdater")),
    "fokus falt ut av seksjonen etter oppfriskningen");
  assert.equal(live(), meldt(1),
    "redrawet ble aldri annonsert i den høflige live-regionen");

  // «Last flere»: siste side finnes ikke, så knappen forsvinner —
  // fokus lander på «Oppdater» i stedet for i ingenting.
  knapp("last_flere").focus();
  knapp("last_flere").click();
  assert.ok(await vent(() => seksjon.textContent.includes("100")),
    "side 2 ble aldri appendet");
  assert.ok(!knapp("last_flere"),
    "positiv kontroll: siste side skal fjerne «Last flere»");
  assert.equal(document.activeElement, knapp("oppdater"),
    "fokus falt ut av seksjonen da «Last flere» forsvant");
  assert.equal(live(), meldt(2), "den appendede siden ble aldri annonsert");
  // MUTASJONEN SOM DREPER DENNE: fjern `etterListeklikk(…)` fra de to
  // klikkhåndtererne — fokus faller da til `document.body`.
});

test("Evalueringer: en feilet listehandling MELDER fra — ikke stille "
  + "utdatert liste", async () => {
  KALL = [];
  // Codex P2: en feilet «Last flere»/«Oppdater» re-aktiverte bare
  // knappen. Brukeren kunne ikke skille 403/5xx/nettbrudd fra
  // «oppdatert, ingenting nytt», og handlet videre på gamle statuser.
  const rad = (id) => ({ oppdrag_id: id, status: "opprettet",
    opprettet: "2026-08-27T02:00:00+00:00", rapport_klar: false });
  SVAR = {
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": {
      evalueringer: [rad(200)], flere: true, neste_cursor: "c-side2.mac" },
    "/v1/rekruttering/evalueringer?cursor=c-side2.mac": 503,
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste")));
  const seksjon = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  const knapp = (nokkel) => [...seksjon.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.evalueringer." + nokkel));
  const alert = () => seksjon.querySelector(".rekrut-listeutfall").textContent;
  assert.equal(alert(), "", "positiv kontroll: utfallsområdet står tomt");
  knapp("last_flere").click();
  assert.ok(await vent(() => alert().includes(
    t("ui.rekruttering.evalueringer.handlingfeil"))),
  "en feilet «Last flere» sa ingenting");
  assert.ok(!knapp("last_flere").disabled,
    "knappen ble stående deaktivert etter feilen");
  assert.ok(seksjon.textContent.includes("200"),
    "listen brukeren så på forsvant på feilveien");

  // ... og «Oppdater» på samme vei.
  SVAR["/v1/rekruttering/evalueringer"] = 503;
  knapp("oppdater").click();
  assert.ok(await vent(() => alert().includes(
    t("ui.rekruttering.evalueringer.handlingfeil"))),
  "en feilet «Oppdater» sa ingenting");

  // ... og en vellykket handling RYDDER meldingen: en gammel feil skal
  // aldri bli stående over en fersk liste.
  SVAR["/v1/rekruttering/evalueringer"] = {
    evalueringer: [rad(201), rad(200)], flere: false, neste_cursor: null };
  knapp("oppdater").click();
  assert.ok(await vent(() => alert() === ""),
    "feilmeldingen ble stående over en vellykket oppfriskning");
  assert.ok(seksjon.textContent.includes("201"), "listen ble ikke oppfrisket");
  // MUTASJONEN SOM DREPER DENNE: fjern `meldListefeil(true)` fra katchene
  // — da er alle tre ventingene tomme og testen ryker på den første.
});

test("Evalueringer: «Oppdater» og «Last flere» blander aldri to "
  + "cursorkjeder — siste klikk vinner", async () => {
  KALL = [];
  // Codex P2: begge knappene står klikkbare, og pagineringen LESTE bare
  // generasjonen mens oppfriskningen tok den. Da delte de to hentingene
  // generasjon, og et cursorsvar fra den GAMLE kjeden kunne appendes på
  // en nyhentet første side — to kjeder blandet i én liste.
  //
  // Riggen: oppfriskningen HENGER, pagineringen svarer straks. Slippes
  // oppfriskningen etterpå, skal den forkastes (klikket på «Last flere»
  // var den siste intensjonen), og listen skal være den GAMLE kjeden
  // hel: 200 + 100, aldri 201 fra den ferske første siden.
  let slippTreg;
  const treg = new Promise((res) => { slippTreg = res; });
  const rad = (id, tid) => ({ oppdrag_id: id, status: "opprettet",
    opprettet: tid, rapport_klar: false });
  SVAR = {
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": {
      evalueringer: [rad(200, "2026-08-27T02:00:00+00:00")],
      flere: true, neste_cursor: "c-side2.mac" },
    "/v1/rekruttering/evalueringer?cursor=c-side2.mac": {
      evalueringer: [rad(100, "2026-08-27T01:00:00+00:00")],
      flere: false, neste_cursor: null },
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste")));
  const seksjon = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  const knapp = (nokkel) => [...seksjon.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.evalueringer." + nokkel));
  // Fra nå henger første side — altså oppfriskningen.
  SVAR["/v1/rekruttering/evalueringer"] = treg;
  knapp("oppdater").click();
  knapp("last_flere").click();
  assert.ok(await vent(() => seksjon.textContent.includes("100")),
    "side 2 ble aldri appendet");
  slippTreg({ evalueringer: [rad(201, "2026-08-27T03:00:00+00:00"),
    rad(200, "2026-08-27T02:00:00+00:00")], flere: false,
  neste_cursor: null });
  await new Promise((r) => setTimeout(r, 30));
  const rader = [...seksjon.querySelectorAll(".rekrut-kort")]
    .map((c) => c.dataset.oppdrag);
  assert.deepEqual(rader, ["200", "100"],
    "oppfriskningen og pagineringen skrev over hverandre: " + rader.join(","));
  // MUTASJONEN SOM DREPER DENNE: bytt `const min = ++eval2.nr` tilbake til
  // `const min = eval2.nr` i «Last flere» — da slipper det trege
  // oppfriskningssvaret gjennom og listen blir ["201", "200"], altså den
  // appendede siden stille borte.
});

test("Evalueringer: en taperunde låser ikke «Last flere» når «Oppdater» "
  + "tok generasjonen og FEILET", async () => {
  KALL = [];
  // Cursor P2: generasjonsvakten forkastet det tapte svaret med et bart
  // `return` — uten å slå knappen på igjen. Vant en VELLYKKET
  // «Oppdater», gjorde det ingenting: om-tegningen river noden uansett.
  // Men «Oppdater» bumper generasjonen FØR den vet om den lykkes, og på
  // feilveien tegner den ikke. Da ble «Last flere» stående deaktivert
  // over en liste som fortsatt meldte `flere: true` — kontrollen var
  // død til noe annet tvang en full om-tegning.
  let slippSide2;
  const treg = new Promise((res) => { slippSide2 = res; });
  const rad = (id, tid) => ({ oppdrag_id: id, status: "opprettet",
    opprettet: tid, rapport_klar: false });
  SVAR = {
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": {
      evalueringer: [rad(200, "2026-08-27T02:00:00+00:00")],
      flere: true, neste_cursor: "c-side2.mac" },
    "/v1/rekruttering/evalueringer?cursor=c-side2.mac": treg,
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste")));
  const seksjon = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  const knapp = (nokkel) => [...seksjon.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.evalueringer." + nokkel));

  // 1) «Last flere» tar generasjonen og henger.
  knapp("last_flere").click();
  assert.ok(knapp("last_flere").disabled,
    "positiv kontroll: knappen skal være deaktivert mens siden er i lufta");
  // 2) «Oppdater» bumper generasjonen og FEILER — ingen om-tegning, så
  //    «Last flere»-noden lever fortsatt.
  SVAR["/v1/rekruttering/evalueringer"] = 503;
  knapp("oppdater").click();
  assert.ok(await vent(() => seksjon.querySelector(".rekrut-listeutfall")
    .textContent
    .includes(t("ui.rekruttering.evalueringer.handlingfeil"))),
  "riggen feilet: «Oppdater» meldte aldri fra");
  // 3) Den tapte siden lander. Den skal ikke skrive noe — og ikke låse.
  slippSide2({ evalueringer: [rad(100, "2026-08-27T01:00:00+00:00")],
    flere: false, neste_cursor: null });
  await new Promise((r) => setTimeout(r, 30));
  assert.ok(!seksjon.textContent.includes("100"),
    "det tapte svaret ble skrevet inn i listen likevel");
  assert.ok(knapp("last_flere") && !knapp("last_flere").disabled,
    "«Last flere» ble stående deaktivert etter en tapt runde");
  // MUTASJONEN SOM DREPER DENNE: fjern `k.disabled = false` fra
  // `if (min !== eval2.nr)`-grenen i «Last flere».
});

test("Evalueringer: «Oppdater» henter listen på nytt uten side-reload"
  + " (#221)", async () => {
  KALL = [];
  let status = "plukket";
  SVAR = (sti) => {
    if (sti === "/v1/rekruttering/evalueringer") {
      return { evalueringer: [{ oppdrag_id: 300, status,
        opprettet: "2026-08-27T03:00:00+00:00",
        rapport_klar: status === "utfort" }], flere: false };
    }
    return ({ "/v1/rekruttering/prosesser": prosess(),
      "/v1/rekruttering/stillingsprofiler": profiler() })[sti] ?? 500;
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste")));
  const seksjon = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  assert.match(seksjon.textContent,
    new RegExp(t("ui.rekruttering.evalueringer.venter")),
    "positiv kontroll: oppdraget står som underveis");
  // Evalueringen blir ferdig hos serveren; ingen omlasting av ruta.
  status = "utfort";
  const oppdater = [...seksjon.querySelectorAll("button")]
    .find((b) => b.textContent
      === t("ui.rekruttering.evalueringer.oppdater"));
  assert.ok(oppdater, "flaten mangler oppdateringsknappen");
  oppdater.click();
  assert.ok(await vent(() => seksjon.textContent.includes(
    t("ui.rekruttering.evalueringer.klar"))),
  "statusen forble «under arbeid» til en side-reload");
});

test("Evalueringer: det siste klikket vinner — et tregt eldre svar forkastes", async () => {
  KALL = [];
  // Rapport 96 HENGER til testen slipper den; 97 svarer straks. Slippes
  // 96 etterpå, skal det trege svaret forkastes — ikke erstatte 97.
  let slippFoerste;
  const treg = new Promise((res) => { slippFoerste = res; });
  const rapportFor = (navn) => ({ oppdrag_id: 0, rapport: {
    rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
    profil: { profil_id: "p-1", versjon: 2, navn },
    antall_soknader: 1,
    rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
      nedbrytning: { drift: 5 } }],
    kandidater: { "kandidat-01": { funn: [], intervjusporsmal: [],
      kildetekst: "[NAVN-1]" } },
    fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
  } });
  SVAR = (sti) => ({
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true },
      { oppdrag_id: 97, status: "utfort",
        opprettet: "2026-08-27T01:00:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/96": treg,
    "/v1/rekruttering/rapport/97": rapportFor("Sikkerhetsleder"),
  })[sti] ?? 500;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => [...hoved.querySelectorAll(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste button")]
    .filter((b) => b.textContent
      === t("ui.rekruttering.evalueringer.vis")).length === 2));
  const seksjon = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  const [knapp96, knapp97] = [...seksjon.querySelectorAll(".rekrut-kortliste button")]
    .filter((b) => b.textContent === t("ui.rekruttering.evalueringer.vis"));
  knapp96.click();
  knapp97.click();
  assert.ok(await vent(() => seksjon.textContent.includes("Sikkerhetsleder")),
    "97-rapporten rendret aldri");
  slippFoerste(rapportFor("Driftskonsulent"));
  await new Promise((r) => setTimeout(r, 30));
  assert.ok(seksjon.textContent.includes("Sikkerhetsleder"),
    "det ferske svaret forsvant");
  assert.ok(!seksjon.textContent.includes("Driftskonsulent"),
    "det TREGE eldre svaret vant over brukerens siste valg");
});

test("Evalueringer: siste oppfriskning vinner — tregt eldre listesvar forkastes", async () => {
  KALL = [];
  // `paagaaende` slipper opp FØR den fire-and-forget `oppdater()` er
  // ferdig, så to raske bestillinger gir to listehentinger i lufta.
  // Hentingen etter bestilling 42 HENGER og svarer til slutt `[42]`;
  // hentingen etter bestilling 43 svarer `[42,43]` straks. Slippes den
  // trege etterpå, må 43 fortsatt stå — ellers forsvinner oppdraget
  // brukeren nettopp leverte, helt til neste side-omlasting.
  let slippTreg;
  const treg = new Promise((res) => { slippTreg = res; });
  const rad = (id) => ({ oppdrag_id: id, status: "opprettet",
    opprettet: "2026-08-27T07:00:00+00:00", rapport_klar: false });
  let listekall = 0;
  const basis = { "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/inndata/reserver": { reservasjon_jti: "j-1",
                              inndata_ref: "inndata:u-1" },
    "/v1/inndata/opplast/j-1": {} };
  let bestillingssvar = { beslutning: "tillat", oppdrag_id: 42 };
  SVAR = (sti) => {
    if (sti === "/v1/bestilling") return bestillingssvar;
    if (sti === "/v1/rekruttering/evalueringer") {
      listekall += 1;
      if (listekall === 1) return { evalueringer: [] };
      if (listekall === 2) return treg;
      return { evalueringer: [rad(42), rad(43)] };
    }
    return basis[sti];
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const seksjon = hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  const skjema = seksjon.querySelector("form");
  const filInp = skjema.querySelector("input[type=file]");
  Object.defineProperty(filInp, "files", { configurable: true,
    value: [{ name: "bunt.zip",
              arrayBuffer: async () => new ArrayBuffer(16) }] });
  const send = async (oppdrag) => {
    bestillingssvar = { beslutning: "tillat", oppdrag_id: oppdrag };
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    assert.ok(await vent(() => seksjon.querySelector("[role=alert]")
      .textContent.includes(String(oppdrag)), 20),
      `bestilling ${oppdrag} kvitterte aldri`);
  };
  await send(42);
  await send(43);
  const evalSeksjon = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  assert.ok(await vent(() => evalSeksjon.textContent.includes("43")),
    "den ferske listen kom aldri på skjermen");
  slippTreg({ evalueringer: [rad(42)] });
  await new Promise((r) => setTimeout(r, 30));
  const rader = [...evalSeksjon.querySelectorAll(".rekrut-kort")]
    .map((c) => c.dataset.oppdrag);
  assert.ok(rader.includes("43"),
    "det TREGE eldre listesvaret tegnet over den nyeste listen");
  assert.ok(rader.includes("42"), "oppdrag 42 forsvant fra listen");
});

test("Evalueringer: det leverte oppdraget overlever et prosessbytte", async () => {
  KALL = [];
  // Listen er TENANT-global, ikke prosessbundet — men `tegn` bygger
  // seksjonen på nytt ved hvert prosessbytte. Skrev oppfriskningen bare
  // DOM, seedet den nye instansen seg fra `data`-snapshoten fra
  // sidelastingen, og oppdraget brukeren nettopp leverte forsvant igjen
  // (Cursor P1). Ingen ny listehenting i mount-pathen skal redde det:
  // etter den første lastingen svarer endepunktet aldri mer.
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", navn: "Sykepleier vest", blinding_av: false,
    vekter: { drift: 1 },
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  const rad = (id) => ({ oppdrag_id: id, status: "opprettet",
    opprettet: "2026-08-27T07:00:00+00:00", rapport_klar: false });
  let listekall = 0;
  const basis = { "/v1/rekruttering/prosesser": to,
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/bestilling": { beslutning: "tillat", oppdrag_id: 42 },
    "/v1/inndata/reserver": { reservasjon_jti: "j-1",
                              inndata_ref: "inndata:u-1" },
    "/v1/inndata/opplast/j-1": {} };
  SVAR = (sti) => {
    if (sti === "/v1/rekruttering/evalueringer") {
      listekall += 1;
      // Lastingen ser tom historikk; oppfriskningen etter `tillat` er
      // det ENESTE svaret som bærer oppdrag 42.
      return { evalueringer: listekall === 1 ? [] : [rad(42)] };
    }
    return basis[sti];
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const seksjon = hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  const skjema = seksjon.querySelector("form");
  const filInp = skjema.querySelector("input[type=file]");
  Object.defineProperty(filInp, "files", { configurable: true,
    value: [{ name: "bunt.zip",
              arrayBuffer: async () => new ArrayBuffer(16) }] });
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() => seksjon.querySelector("[role=alert]")
    .textContent.includes("42")), "bestillingen kvitterte aldri");
  const evalRader = () => [...hoved.querySelectorAll(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kort")]
    .map((c) => c.dataset.oppdrag);
  assert.ok(await vent(() => evalRader().includes("42")),
    "det leverte oppdraget kom aldri i listen");
  const kallFoer = listekall;
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(evalRader().includes("42"),
    "oppdraget døde i prosessbyttets om-tegning");
  assert.equal(listekall, kallFoer,
    "seksjonen hentet listen på nytt ved mount — den skal seedes fra økten");
});

test("Evalueringer: produktet først og null klikk — ferskeste klare "
  + "rapport står ferdig rendret uten fokus-tyveri", async () => {
  KALL = [];
  // Eiers UX-prinsipp (27/8): færrest mulig klikk til produktet.
  // 1) Evalueringsseksjonen er FØRSTE seksjon på flaten.
  // 2) Finnes en ferdig rapport, er den rendret ved lasting — uten
  //    klikk, og uten å stjele fokus fra der brukeren er.
  SVAR = {
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/96": { oppdrag_id: 96, rapport: {
      rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
      profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
      antall_soknader: 1,
      rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
        nedbrytning: { drift: 5 } }],
      kandidater: { "kandidat-01": { funn: [] } },
      fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
    } },
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.textContent.includes("Driftskonsulent")),
    "rapporten rendret ikke av seg selv");
  const seksjoner = [...hoved.querySelectorAll("section[aria-labelledby]")];
  assert.equal(seksjoner[0] && seksjoner[0].getAttribute("aria-labelledby"),
    "evaluering-tittel", "produktet skal stå FØRST på flaten");
  const overskrift = hoved.querySelector("h3[tabindex='-1']");
  assert.ok(overskrift, "rapportoverskriften mangler");
  assert.notEqual(hoved.ownerDocument.activeElement, overskrift,
    "auto-visningen stjal fokus — fokus hører til eksplisitt klikk");
  // Klikk på rapporten som ALT står der: cache-treff (immutabelt
  // artefakt) — ingen ny henting, men klikkets fokus-semantikk.
  const hentingerFoer = KALL.filter(
    (k) => k.sti === "/v1/rekruttering/rapport/96").length;
  hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste button").click();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(KALL.filter(
    (k) => k.sti === "/v1/rekruttering/rapport/96").length, hentingerFoer,
    "klikk på cachet rapport lastet den ned på nytt");
  const overskrift2 = hoved.querySelector("h3[tabindex='-1']");
  assert.equal(hoved.ownerDocument.activeElement, overskrift2,
    "cache-treffet ga ikke klikkets fokus");
  // ... og en ALT VIST rapport bygges ikke på nytt (Codex P2): samme
  // overskrift-NODE — en rebuild hadde kollapset åpne detaljbokser.
  assert.equal(overskrift2, overskrift,
    "klikk på vist rapport bygde DOM-en på nytt");
  // 3) ... og stille er ikke det samme som skånsom (Cursor P2): uten
  //    fokusflytting er den høflige live-regionen det ENESTE sporet som
  //    sier fra at produktet dukket opp.
  const live = hoved.ownerDocument.body
    .querySelector('[role=status][aria-live=polite]');
  assert.ok(live, "den høflige live-regionen finnes ikke");
  assert.equal(live.textContent, overskrift.textContent,
    "auto-visningen meldte ikke rangeringen til skjermleseren");
  // MUTASJONEN SOM DREPER DENNE: fjern `meldLive(...)` fra `!fokus`-grenen.
});

test("Evalueringer: auto-visningen tar den FERSKESTE klare rapporten, "
  + "uansett rekkefølgen listen kommer i", async () => {
  // Cursor P2: `find(e => e.rapport_klar)` leste «ferskeste» ut av
  // listens rekkefølge — altså ut av `ORDER BY o.id DESC` i `lesing.py`,
  // en sortering flaten hverken eier eller binder. Testen kjører BEGGE
  // rekkefølgene: stigende seed dreper den gamle formen, synkende seed
  // er motprøven som sier at valget ikke bare snudde en antagelse.
  const rapport = (navn) => ({ rapport: {
    rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
    profil: { profil_id: "p-1", versjon: 2, navn },
    antall_soknader: 1,
    rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
      nedbrytning: { drift: 5 } }],
    kandidater: { "kandidat-01": { funn: [] } },
    fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
  } });
  const rad = (oid) => ({ oppdrag_id: oid, status: "utfort",
    opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true });
  for (const seed of [[rad(96), rad(97)], [rad(97), rad(96)]]) {
    KALL = [];
    SVAR = {
      "/v1/rekruttering/prosesser": prosess(),
      "/v1/rekruttering/stillingsprofiler": profiler(),
      "/v1/rekruttering/evalueringer": { evalueringer: seed },
      "/v1/rekruttering/rapport/96": rapport("Eldre rangering"),
      "/v1/rekruttering/rapport/97": rapport("Ferskere rangering"),
    };
    const hoved = nyHoved();
    visRekruttering(hoved, ctx());
    const rekkefolge = seed.map((e) => e.oppdrag_id).join(",");
    assert.ok(await vent(() => hoved.querySelector("h3[tabindex='-1']")),
      `rapporten rendret ikke av seg selv (seed ${rekkefolge})`);
    assert.ok(hoved.textContent.includes("Ferskere rangering"),
      `auto-visningen viste ikke det høyeste oppdraget (seed ${rekkefolge})`);
    assert.ok(!hoved.textContent.includes("Eldre rangering"),
      `den eldre rapporten ble vist (seed ${rekkefolge})`);
    assert.ok(!KALL.some((k) => k.sti === "/v1/rekruttering/rapport/96"),
      `flaten hentet den eldre rapporten (seed ${rekkefolge})`);
  }
  // MUTASJONEN SOM DREPER DENNE: bytt reduksjonen tilbake til
  // `seedListe.find((e2) => e2.rapport_klar)` — stigende seed viser 96.
});

test("Evalueringer: auto-visningen bruker API-ETS nøkkel (opprettet, id) — "
  + "høyeste id er ikke ferskest", async () => {
  // Codex P2: endepunktet sorterer på `(opprettet, id)`, flaten valgte på
  // `id` alene. Ordenene DIVERGERER for samtidige bestillinger, fordi
  // PostgreSQLs `now()` er transaksjonens starttid mens id-en tildeles
  // ved selve inserten: en forsinket ELDRE transaksjon kan få den høyeste
  // id-en. Her er 98 eldst i tid, men høyest i id — tabellen er korrekt
  // tidssortert, og auto-stien skal åpne 97, ikke 98.
  const rapport = (navn) => ({ rapport: {
    rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
    profil: { profil_id: "p-1", versjon: 2, navn },
    antall_soknader: 1,
    rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
      nedbrytning: { drift: 5 } }],
    kandidater: { "kandidat-01": { funn: [] } },
    fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
  } });
  const rad = (oid, tid) => ({ oppdrag_id: oid, status: "utfort",
    opprettet: tid, rapport_klar: true });
  const seed = [rad(97, "2026-08-27T02:00:00+00:00"),
    rad(98, "2026-08-27T01:00:00+00:00")];
  KALL = [];
  SVAR = {
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: seed },
    "/v1/rekruttering/rapport/97": rapport("Ferskest i tid"),
    "/v1/rekruttering/rapport/98": rapport("Hoyest id, eldst i tid"),
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("h3[tabindex='-1']")),
    "rapporten rendret ikke av seg selv");
  assert.ok(hoved.textContent.includes("Ferskest i tid"),
    "auto-visningen fulgte ikke serverens (opprettet, id)-nøkkel");
  assert.ok(!KALL.some((k) => k.sti === "/v1/rekruttering/rapport/98"),
    "flaten hentet raden som bare hadde høyest id");
  // MUTASJONEN SOM DREPER DENNE: sammenlign på `oppdrag_id` alene igjen —
  // da vinner 98, og «Hoyest id, eldst i tid» rendres.
});

test("Evalueringer: hopplenke med ÉN prosess — ankeret står før "
  + "blinding, vekter og signering", async () => {
  // Pass-P2 (speilport): flerprosess-testen låser ankerets plass mot
  // prosessVELGEREN, som bare finnes med ≥2 prosesser. Vanligste flate
  // er én prosess — der må ankeret ligge etter rangeringens siste
  // `<summary>` og FØR første prosesskontroll (blinding/vekter),
  // ellers er bypass ødelagt for de fleste uten at porten ser det.
  KALL = [];
  const kandidater = ["kandidat-01", "kandidat-02", "kandidat-03"];
  SVAR = {
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/96": { oppdrag_id: 96, rapport: {
      rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
      profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
      antall_soknader: 3,
      rangering: kandidater.map((id, i) => ({ kandidat_id: id,
        poeng: 9 - i, nedbrytning: { drift: 9 - i } })),
      kandidater: Object.fromEntries(kandidater.map((id) => [id, { funn: [] }])),
      fremdrift: { filer_lest: 3, filer_totalt: 3, byte_lest: 150 },
    } },
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("h3[tabindex='-1']")),
    "rapporten rendret ikke av seg selv");
  const sammendrag = [...hoved.querySelectorAll(
    "section[aria-labelledby=evaluering-tittel] details > summary")];
  assert.ok(sammendrag.length >= 3);
  const hopp = hoved.querySelector("a.rekrut-hopp");
  assert.ok(hopp, "hopplenken mangler i én-prosess-flaten");
  const maal = hoved.querySelector(hopp.getAttribute("href"));
  assert.ok(maal, "ankeret mangler i én-prosess-flaten");
  const alle = [...hoved.querySelectorAll("*")];
  assert.ok(alle.indexOf(maal)
    > alle.indexOf(sammendrag[sammendrag.length - 1]),
    "ankeret ligger foran detaljboksene — ingenting hoppes over");
  // Første prosesskontroll i én-prosess-grenen: blindingsbryteren/vekt-
  // kontrollen — ankeret må ligge FØR den (mutasjonen «flytt hoppAnker
  // etter listeRot» rødner her).
  // Rapportens EGNE vekter (mobil-redesignet 29/8) hører til rangeringen
  // og ligger med rette FØR ankeret — prosesskontrollen porten måler er
  // dypdykkets (fieldset.rekrut-vekter), blindingen eller tabellen.
  const foersteKontroll = hoved.querySelector(
    "fieldset.rekrut-vekter input[type=range], input[type=checkbox],"
    + " table[aria-label]");
  assert.ok(foersteKontroll, "fant ingen prosesskontroll i riggen");
  assert.ok(alle.indexOf(maal) < alle.indexOf(foersteKontroll),
    "ankeret ligger etter prosesskontrollene — hoppet lander for langt ned");
  const klikk = new window.MouseEvent("click",
    { bubbles: true, cancelable: true });
  hopp.dispatchEvent(klikk);
  assert.equal(hoved.ownerDocument.activeElement, maal,
    "hopplenken flyttet ikke fokus til ankeret");
  assert.ok(klikk.defaultPrevented, "fragmentnavigasjonen slapp gjennom");
});

test("Evalueringer: hopplenke forbi rangeringen til prosess og signering "
  + "— og den rører ikke ruterens hash", async () => {
  // Cursor P2: «produktet først» + null klikk mounter én fokusbar
  // `<summary>` per kandidat FORAN prosessvelger, vekter og signering.
  // Tastaturveien til de irreversible handlingene ble dermed like lang som
  // kandidatlisten (skjemaet tillater 5000). `<summary>`-ene beholder
  // tab-rekkefølgen sin — å ta dem ut ville stengt tastaturveien INN i
  // detaljene — og veien forbi er WCAG 2.4.1s egen: en hopplenke.
  KALL = [];
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", navn: "Sykepleier vest", blinding_av: false,
    vekter: { drift: 1 },
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  const kandidater = ["kandidat-01", "kandidat-02", "kandidat-03"];
  SVAR = {
    "/v1/rekruttering/prosesser": to,
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/96": { oppdrag_id: 96, rapport: {
      rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
      profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
      antall_soknader: 3,
      rangering: kandidater.map((id, i) => ({ kandidat_id: id,
        poeng: 9 - i, nedbrytning: { drift: 9 - i } })),
      kandidater: Object.fromEntries(kandidater.map((id) => [id, { funn: [] }])),
      fremdrift: { filer_lest: 3, filer_totalt: 3, byte_lest: 150 },
    } },
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("h3[tabindex='-1']")),
    "rapporten rendret ikke av seg selv");
  const sammendrag = [...hoved.querySelectorAll("details > summary")];
  assert.ok(sammendrag.length >= 3,
    `rangeringen ga bare ${sammendrag.length} detaljbokser`);
  // `.rekrut-hopp`, ikke sidetopp-`.hoppelenke` (pass-funn: den er
  // viewport-absolute og teleporterte fokuset bort fra rangeringen).
  const hopp = hoved.querySelector("a.rekrut-hopp");
  // ... og lenken står ETTER overskriften (Codex P2): tab framover fra
  // den fokuserte overskriften skal møte bypass-en før rangeringen.
  const rapportOverskrift = hoved.querySelector("h3[tabindex='-1']");
  assert.ok(rapportOverskrift.compareDocumentPosition(hopp)
    & rapportOverskrift.DOCUMENT_POSITION_FOLLOWING,
    "hopplenken står foran overskriften — utabbar fra fokuspunktet");
  assert.ok(hopp, "hopplenken over rangeringen mangler");
  const maal = hoved.querySelector(hopp.getAttribute("href"));
  assert.ok(maal, "hopplenken peker på et anker som ikke finnes i flaten");
  // Ankeret har et TILGJENGELIG navn (Codex P2): fokus på en navnløs,
  // tom div annonseres som ingenting for skjermleseren.
  assert.equal(maal.getAttribute("aria-label"),
    t("ui.rekruttering.evalueringer.hopp_maal"));
  // Dokumentrekkefølge: ankeret ligger ETTER alle detaljboksene og FØR
  // prosesskontrollen — altså er det nettopp rangeringen som hoppes over.
  const alle = [...hoved.querySelectorAll("*")];
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  assert.ok(velger, "prosessvelgeren mangler i riggen");
  assert.ok(alle.indexOf(maal) > alle.indexOf(sammendrag[sammendrag.length - 1]),
    "ankeret ligger foran detaljboksene — da hoppes ingenting over");
  assert.ok(alle.indexOf(maal) < alle.indexOf(velger),
    "ankeret ligger etter prosessvelgeren — hoppet lander for langt ned");
  assert.ok(alle.indexOf(hopp) < alle.indexOf(sammendrag[0]),
    "hopplenken står ikke foran rangeringen den skal hoppe over");
  // ... og hoppet er et FOKUSHOPP, ikke en navigasjon: hash-en eies av
  // `ruter.js` (`#/<rute>`), og en ukjent rute sender brukeren til
  // reserveflaten — altså ut av rekrutteringen lenken skulle hoppe inne i.
  // Målt på HENDELSEN, ikke på `location.hash`: jsdom følger ikke
  // fragmentlenker, så en hash-sammenligning ville stått grønn uansett —
  // en port som ikke kan feile. `defaultPrevented` er selve forsvaret.
  const klikk = new window.MouseEvent("click",
    { bubbles: true, cancelable: true });
  hopp.dispatchEvent(klikk);
  assert.equal(hoved.ownerDocument.activeElement, maal,
    "hopplenken flyttet ikke fokus til ankeret");
  assert.ok(klikk.defaultPrevented,
    "fragmentnavigasjonen gikk videre til ruteren — den leser hashen som "
    + "`#/<rute>` og sender brukeren til reserveflaten");
  // MUTASJONEN SOM DREPER DENNE: fjern hopplenken fra rapporten, flytt
  // ankeret foran evalueringsseksjonen, eller slipp klikket videre til
  // nettleserens egen fragmentnavigasjon.
});

test("Evalueringer: klikk under pågående auto-lasting deler løftet — "
  + "én henting, fokus til klikket", async () => {
  KALL = [];
  // Codex P2: auto-lastingen og et utålmodig klikk på samme rapport
  // skal aldri bli to hentinger.
  let slipp;
  const treg = new Promise((res) => { slipp = res; });
  SVAR = (sti) => ({
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/96": treg,
  })[sti] ?? 500;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste button")));
  const seksjon = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  seksjon.querySelector(".rekrut-kortliste button").click();
  slipp({ oppdrag_id: 96, rapport: {
    rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
    profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
    antall_soknader: 1,
    rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
      nedbrytning: { drift: 5 } }],
    kandidater: { "kandidat-01": { funn: [] } },
    fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
  } });
  assert.ok(await vent(() => seksjon.textContent.includes("kandidat-01")),
    "rapporten rendret aldri");
  assert.equal(KALL.filter(
    (k) => k.sti === "/v1/rekruttering/rapport/96").length, 1,
    "klikket startet en henting nr. 2 av samme rapport");
  const overskrift = seksjon.querySelector("h3[tabindex='-1']");
  assert.equal(seksjon.ownerDocument.activeElement, overskrift,
    "klikket skal ha fokus-semantikken selv om løftet var autoens");
});

test("Evalueringer: auto-feil er stille — alert hører til klikket", async () => {
  KALL = [];
  // Pass-funn: listen og detaljen kan divergere i vinduet mellom mount
  // og henting (frist/TOCTOU/transient). En usolicited role=alert på
  // hver sidelasting er falsk alarm — auto-feilen lar rapportområdet
  // stå tomt; klikket får feilmeldingen som før.
  SVAR = {
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }] },
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => KALL.some(
    (k) => k.sti === "/v1/rekruttering/rapport/96")),
    "auto-lastingen prøvde aldri");
  await new Promise((r) => setTimeout(r, 20));
  const seksjon = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  assert.ok([...seksjon.querySelectorAll('[role="alert"]')]
    .every((a) => !a.textContent.includes(
      t("ui.rekruttering.evalueringer.rapportfeil"))),
    "auto-feilen malte en usolicited alert");
  // ... og KLIKKET får feilmeldingen som før (positiv kontroll).
  seksjon.querySelector(".rekrut-kortliste button").click();
  assert.ok(await vent(() => [...seksjon.querySelectorAll('[role="alert"]')]
    .some((a) => a.textContent
      === t("ui.rekruttering.evalueringer.rapportfeil"))),
    "klikket ga aldri feilmeldingen (positiv kontroll)");
});

test("Evalueringer: auto-lastingen kjører ÉN gang per økt — "
  + "prosessbytte re-fetcher ikke rapporten", async () => {
  KALL = [];
  // Codex P2: listen er tenant-global; hvert prosessbytte bygger
  // seksjonen på nytt, og en ubetinget auto-lasting hadde hentet og
  // re-rendret rapporten for hver veksling.
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", navn: "Sykepleier vest", blinding_av: false,
    vekter: { drift: 1 },
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  SVAR = {
    ...svar183(to),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/96": { oppdrag_id: 96, rapport: {
      rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
      profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
      antall_soknader: 1,
      rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
        nedbrytning: { drift: 5 } }],
      kandidater: { "kandidat-01": { funn: [] } },
      fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
    } },
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.textContent.includes("kandidat-01")),
    "auto-visningen rendret aldri");
  const foer = KALL.filter(
    (k) => k.sti === "/v1/rekruttering/rapport/96").length;
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(await vent(() => [...hoved.querySelectorAll("tbody tr")]
    .some((tr) => tr.querySelector("td").textContent === "K-9")),
    "prosessbyttet hentet aldri den valgte prosessen");
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(KALL.filter(
    (k) => k.sti === "/v1/rekruttering/rapport/96").length, foer,
    "prosessbyttet re-fetchet rapporten — auto-lastingen er per økt");
  // ... og rapporten OVERLEVER byttet (Codex P2): re-bygget fra øktens
  // cache inn i den nye seksjonen — uten henting.
  assert.ok(hoved.textContent.includes("kandidat-01"),
    "rapporten forsvant i prosessbyttet — cachen re-bygges ikke");
});

test("Evalueringer: et svar etter RUTE-forlatelse bygger og melder "
  + "ingenting", async () => {
  KALL = [];
  // Codex P2: eierskapstesten står FØR byggingen — et svar som lander
  // etter at brukeren forlot rekrutteringen skal hverken bygge
  // 5000-raders DOM, tegne eller kunngjøre.
  let slipp;
  const treg = new Promise((res) => { slipp = res; });
  SVAR = (sti) => ({
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/96": treg,
  })[sti] ?? 500;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => KALL.some(
    (k) => k.sti === "/v1/rekruttering/rapport/96")),
    "auto-lastingen startet aldri");
  // Brukeren forlater ruta: en annen flate overtar hovedinnholdet.
  hoved.replaceChildren();
  const live = document.querySelector('[role="status"][aria-live="polite"]');
  if (live) live.textContent = "";
  slipp({ oppdrag_id: 96, rapport: {
    rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
    profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
    antall_soknader: 1,
    rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
      nedbrytning: { drift: 5 } }],
    kandidater: { "kandidat-01": { funn: [] } },
    fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
  } });
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(hoved.textContent, "",
    "svaret tegnet inn i en forlatt flate");
  assert.ok(!live || !live.textContent.includes("Driftskonsulent"),
    "svaret kunngjorde et produkt fra en forlatt flate");
});

test("Evalueringer: seksjonen er SAMME node etter prosessbyttet — "
  + "og ingenting hentes på nytt", async () => {
  KALL = [];
  // Remount-dommen (port a): identitet dreper en gjeninnført rebuild.
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", navn: "Sykepleier vest", blinding_av: false,
    vekter: { drift: 1 },
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  SVAR = {
    ...svar183(to),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/96": { oppdrag_id: 96, rapport: {
      rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
      profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
      antall_soknader: 1,
      rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
        nedbrytning: { drift: 5 } }],
      kandidater: { "kandidat-01": { funn: [] } },
      fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
    } },
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.textContent.includes("kandidat-01")));
  const foer = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  const kallFoer = KALL.length;
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 10));
  const etter = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  assert.equal(etter, foer,
    "prosessbyttet bygde seksjonen på nytt — noden skulle gjenbrukes");
  // #183: byttet er selv EN henting — den nye prosessen må hentes, for
  // svaret bærer bare den valgtes data. Det er ikke det denne testen
  // måler. Påstanden skjerpes derfor i stedet for å slakkes: nøyaktig
  // ETT nytt kall, og det er prosesshentingen — evalueringsseksjonen
  // henter fortsatt ingenting.
  const nye = KALL.slice(kallFoer);
  assert.deepEqual(nye.map((k) => k.sti), ["/v1/rekruttering/prosesser"],
    "prosessbyttet utløste andre nettverkskall enn selve prosesshentingen"
    + ` — evalueringsseksjonen skal ikke hente: ${JSON.stringify(nye)}`);
  assert.ok(etter.textContent.includes("kandidat-01"),
    "rapporten forsvant fra den gjenbrukte noden");
});

test("Evalueringer: en hengende KLIKK-henting overlever byttet — "
  + "brukerens valg tegnes, auto blander seg aldri", async () => {
  KALL = [];
  // Remount-dommen (port b, runde 5-klassen c47a4409): B henger når
  // brukeren bytter prosess. Uten remount finnes ikke vinduet der
  // auto-A kunne overta — B lander i samme node, og A hentes aldri
  // utover auto-ens opprinnelige (som her alt er cache).
  let slippB;
  const tregB = new Promise((res) => { slippB = res; });
  const rapportA = { oppdrag_id: 97, rapport: {
    rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
    profil: { profil_id: "p-1", versjon: 2, navn: "Sikkerhetsleder" },
    antall_soknader: 1,
    rangering: [{ kandidat_id: "kandidat-02", poeng: 4,
      nedbrytning: { drift: 4 } }],
    kandidater: { "kandidat-02": { funn: [] } },
    fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
  } };
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", navn: "Sykepleier vest", blinding_av: false,
    vekter: { drift: 1 },
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  SVAR = (sti) => ({
    "/v1/rekruttering/prosesser": to,
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 97, status: "utfort",
        opprettet: "2026-08-27T01:00:00+00:00", rapport_klar: true },
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/97": rapportA,
    "/v1/rekruttering/rapport/96": tregB,
  })[sti] ?? 500;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  // Auto-A (97) er rendret; brukeren klikker B (96), som HENGER.
  assert.ok(await vent(() => hoved.textContent.includes("Sikkerhetsleder")));
  const seksjon = () => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  [...seksjon().querySelectorAll("button")]
    .find((b) => b.closest(".rekrut-kort")
      && b.closest(".rekrut-kort").textContent.includes("96")).click();
  const aFoer = KALL.filter(
    (k) => k.sti === "/v1/rekruttering/rapport/97").length;
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(KALL.filter(
    (k) => k.sti === "/v1/rekruttering/rapport/97").length, aFoer,
    "byttet lot auto-A overta — A skulle aldri hentes på nytt");
  slippB({ oppdrag_id: 96, rapport: {
    rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
    profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
    antall_soknader: 1,
    rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
      nedbrytning: { drift: 5 } }],
    kandidater: { "kandidat-01": { funn: [] } },
    fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
  } });
  assert.ok(await vent(() => seksjon().textContent.includes("Driftskonsulent")),
    "brukerens hengende valg (B) nådde aldri frem");
  assert.ok(!seksjon().textContent.includes("Sikkerhetsleder"),
    "auto-A ble stående over brukerens valg");
});

test("Evalueringer: et hengende auto-løfte OVERLEVER prosessbyttet — "
  + "rapporten når frem, én nedlasting", async () => {
  KALL = [];
  // Remount-dommen (port c): noden overlever byttet, så det hengende
  // auto-løftets mål finnes fortsatt — ingen gjenopptakelses-maskin,
  // ingen ny nedlasting; svaret lander i den samme seksjonen.
  let slippA;
  const tregA = new Promise((res) => { slippA = res; });
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", navn: "Sykepleier vest", blinding_av: false,
    vekter: { drift: 1 },
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  SVAR = (sti) => ({
    "/v1/rekruttering/prosesser": to,
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 97, status: "utfort",
        opprettet: "2026-08-27T01:00:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/97": tregA,
  })[sti] ?? 500;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => KALL.some(
    (k) => k.sti === "/v1/rekruttering/rapport/97")),
    "auto-lastingen startet aldri");
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(KALL.filter(
    (k) => k.sti === "/v1/rekruttering/rapport/97").length, 1,
    "byttet utløste en ny nedlasting — noden skulle overlevd");
  slippA({ oppdrag_id: 97, rapport: {
    rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
    profil: { profil_id: "p-1", versjon: 2, navn: "Sikkerhetsleder" },
    antall_soknader: 1,
    rangering: [{ kandidat_id: "kandidat-02", poeng: 4,
      nedbrytning: { drift: 4 } }],
    kandidater: { "kandidat-02": { funn: [] } },
    fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
  } });
  const seksjon = () => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  assert.ok(await vent(() => seksjon().textContent.includes("Sikkerhetsleder")),
    "det hengende auto-løftet nådde aldri frem etter byttet");
});

test("Evalueringer: A→B→A gjenbruker As løfte — aldri to nedlastinger "
  + "av samme rapport", async () => {
  KALL = [];
  // Codex P2: in-flight-markøren er nøklet per rapport-id.
  let slippA, slippB;
  const tregA = new Promise((res) => { slippA = res; });
  const tregB = new Promise((res) => { slippB = res; });
  SVAR = (sti) => ({
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 97, status: "utfort",
        opprettet: "2026-08-27T01:00:00+00:00", rapport_klar: true },
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/97": tregA,
    "/v1/rekruttering/rapport/96": tregB,
  })[sti] ?? 500;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => [...hoved.querySelectorAll(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste button")]
    .filter((b) => b.textContent
      === t("ui.rekruttering.evalueringer.vis")).length === 2));
  const seksjon = () => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  const knappFor = (id) => [...seksjon().querySelectorAll("button")]
    .find((b) => b.closest(".rekrut-kort")
      && b.closest(".rekrut-kort").textContent.includes(String(id)));
  // Auto tok alt 97 (A). Brukeren: B (96), så A (97) igjen — mens
  // BEGGE henger.
  knappFor(96).click();
  knappFor(97).click();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(KALL.filter(
    (k) => k.sti === "/v1/rekruttering/rapport/97").length, 1,
    "gjenvalget av A lastet den ned på nytt mens As løfte hang");
  // Slipp begge: siste valg (A/97) vinner rendringen.
  slippB({ oppdrag_id: 96, rapport: {
    rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
    profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
    antall_soknader: 1,
    rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
      nedbrytning: { drift: 5 } }],
    kandidater: { "kandidat-01": { funn: [] } },
    fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
  } });
  slippA({ oppdrag_id: 97, rapport: {
    rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
    profil: { profil_id: "p-1", versjon: 2, navn: "Sikkerhetsleder" },
    antall_soknader: 1,
    rangering: [{ kandidat_id: "kandidat-02", poeng: 4,
      nedbrytning: { drift: 4 } }],
    kandidater: { "kandidat-02": { funn: [] } },
    fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
  } });
  assert.ok(await vent(() => seksjon().textContent.includes("Sikkerhetsleder")),
    "brukerens siste valg (A) rendret aldri");
  assert.ok(!seksjon().textContent.includes("Driftskonsulent"),
    "det forbigåtte B-svaret ble stående");
});

test("Evalueringer: et sent auto-svar kan aldri restarte auto-stien — "
  + "brukerens valg står", async () => {
  KALL = [];
  // Codex P2 (femte runde → K2-forenklingen): auto A henger, brukeren
  // klikker B; A fullfører sent; deretter prosessbytte. Latch-modellen
  // kunne «gjenåpnes» av A og re-starte A over brukerens B — i den
  // avledede modellen finnes ingen latch: cache/aktiv styrer, og A
  // hverken tegner eller endrer tilstanden B eier.
  let slippA;
  const tregA = new Promise((res) => { slippA = res; });
  const rapportFor = (navn) => ({ oppdrag_id: 0, rapport: {
    rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
    profil: { profil_id: "p-1", versjon: 2, navn },
    antall_soknader: 1,
    rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
      nedbrytning: { drift: 5 } }],
    kandidater: { "kandidat-01": { funn: [] } },
    fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
  } });
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", navn: "Sykepleier vest", blinding_av: false,
    vekter: { drift: 1 },
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  SVAR = (sti) => ({
    "/v1/rekruttering/prosesser": to,
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 97, status: "utfort",
        opprettet: "2026-08-27T01:00:00+00:00", rapport_klar: true },
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/97": tregA,           // NYESTE — auto tar A
    "/v1/rekruttering/rapport/96": rapportFor("Driftskonsulent"),
  })[sti] ?? 500;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => [...hoved.querySelectorAll(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste button")]
    .filter((b) => b.textContent
      === t("ui.rekruttering.evalueringer.vis")).length === 2));
  const seksjon = () => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  // Brukeren velger B (96) mens auto-A (97) henger.
  [...seksjon().querySelectorAll("button")]
    .find((b) => b.closest(".rekrut-kort")
      && b.closest(".rekrut-kort").textContent.includes("96")).click();
  assert.ok(await vent(() => seksjon().textContent.includes("kandidat-01")),
    "B rendret aldri");
  // A fullfører SENT — og skal hverken tegne eller røre tilstanden.
  slippA(rapportFor("Sikkerhetsleder"));
  await new Promise((r) => setTimeout(r, 20));
  assert.ok(!seksjon().textContent.includes("Sikkerhetsleder"),
    "det sene auto-svaret tegnet over brukerens valg");
  // Prosessbytte: B står (fra cachen), og A re-fetches ALDRI.
  const aFoer = KALL.filter(
    (k) => k.sti === "/v1/rekruttering/rapport/97").length;
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));
  assert.ok(seksjon().textContent.includes("Driftskonsulent"),
    "brukerens valgte rapport overlevde ikke byttet");
  assert.equal(KALL.filter(
    (k) => k.sti === "/v1/rekruttering/rapport/97").length, aFoer,
    "auto-stien restartet A over brukerens valg");
});

test("Evalueringer: en stille auto-feil bruker ikke opp noe — neste "
  + "rute-inngang prøver igjen", async () => {
  KALL = [];
  // Remount-dommen: mount skjer ved RUTE-INNGANG (og full lasting) —
  // prosessbytte gjenbruker noden og prøver ikke på nytt (det er
  // poenget). En feilet auto-runde etterlater cache og kart tomme, så
  // NESTE rute-inngang henter rapporten som nå finnes.
  let rapportSvar; // undefined ⇒ 404 fra fetch-stubben
  SVAR = (sti) => ({
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/96": rapportSvar,
  })[sti];
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => KALL.some(
    (k) => k.sti === "/v1/rekruttering/rapport/96")),
    "auto-lastingen prøvde aldri");
  await new Promise((r) => setTimeout(r, 20));
  const seksjon = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  assert.ok([...seksjon.querySelectorAll('[role="alert"]')]
    .every((a) => !a.textContent.includes(
      t("ui.rekruttering.evalueringer.rapportfeil"))),
    "auto-feilen malte en usolicited alert");
  // Rapporten blir tilgjengelig; NESTE rute-inngang henter den.
  rapportSvar = { oppdrag_id: 96, rapport: {
    rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
    profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
    antall_soknader: 1,
    rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
      nedbrytning: { drift: 5 } }],
    kandidater: { "kandidat-01": { funn: [] } },
    fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
  } };
  const hoved2 = nyHoved();
  visRekruttering(hoved2, ctx());
  assert.ok(await vent(() => hoved2.textContent.includes("kandidat-01")),
    "den stille auto-feilen latchet noe — produktet kom aldri tilbake");
});

test("Evalueringer: en rapport som lander etter et prosessbytte tegner "
  + "i den MONTERTE seksjonen", async () => {
  KALL = [];
  // Samme klasse som listeoppfriskningen: `hentingNr` var instans-lokal,
  // så en flygende `visRapport` etter `tegn()` pekte på gammel
  // `rapportRot` — svaret landet i frakoblet DOM og forsvant stille.
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", navn: "Sykepleier vest", blinding_av: false,
    vekter: { drift: 1 },
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  let slipp;
  const treg = new Promise((res) => { slipp = res; });
  SVAR = (sti) => ({
    "/v1/rekruttering/prosesser": to,
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/96": treg,
  })[sti] ?? 500;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste button")));
  hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste button").click();
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  slipp({ oppdrag_id: 96, rapport: {
    rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
    profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
    antall_soknader: 1,
    rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
      nedbrytning: { drift: 5 } }],
    kandidater: { "kandidat-01": { funn: [], intervjusporsmal: [] } },
    fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
  } });
  const seksjon = () => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  assert.ok(await vent(() => seksjon()
    && seksjon().textContent.includes("Driftskonsulent")),
    "rapporten landet i frakoblet DOM og forsvant stille");
  assert.ok(seksjon().isConnected);
});

test("Evalueringer: en oppfriskning som lander etter et prosessbytte tegner "
  + "i den MONTERTE seksjonen", async () => {
  KALL = [];
  // `paagaaende` slipper opp før den fire-and-forget `oppdater()` er
  // ferdig, så brukeren rekker å bytte prosess mens oppfriskningen står
  // i lufta. Svaret tilhørte da en frakoblet DOM og ble stille sluppet:
  // økten fikk listen, men skjermen viste den ikke før NESTE bytte. Det
  // er den monterte seksjonen som tegner, ikke den som ba (Codex P2).
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", navn: "Sykepleier vest", blinding_av: false,
    vekter: { drift: 1 },
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  let slipp;
  const treg = new Promise((res) => { slipp = res; });
  const rad = (id) => ({ oppdrag_id: id, status: "opprettet",
    opprettet: "2026-08-27T07:00:00+00:00", rapport_klar: false });
  let listekall = 0;
  const basis = { "/v1/rekruttering/prosesser": to,
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/bestilling": { beslutning: "tillat", oppdrag_id: 42 },
    "/v1/inndata/reserver": { reservasjon_jti: "j-1",
                              inndata_ref: "inndata:u-1" },
    "/v1/inndata/opplast/j-1": {} };
  SVAR = (sti) => {
    if (sti === "/v1/rekruttering/evalueringer") {
      listekall += 1;
      // Lastingen ser tom historikk; oppfriskningen HENGER.
      return listekall === 1 ? { evalueringer: [] } : treg;
    }
    return basis[sti];
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const seksjon = hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  const skjema = seksjon.querySelector("form");
  const filInp = skjema.querySelector("input[type=file]");
  Object.defineProperty(filInp, "files", { configurable: true,
    value: [{ name: "bunt.zip",
              arrayBuffer: async () => new ArrayBuffer(16) }] });
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() => seksjon.querySelector("[role=alert]")
    .textContent.includes("42")), "bestillingen kvitterte aldri");
  assert.ok(await vent(() => listekall === 2), "oppfriskningen kom aldri");
  // ... og HER bytter brukeren prosess, med svaret fortsatt i lufta.
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  slipp({ evalueringer: [rad(42)] });
  assert.ok(await vent(() => [...hoved.querySelectorAll(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kort")]
    .map((c) => c.dataset.oppdrag).includes("42")),
    "svaret døde med instansen som ba om det");
});

test("Evalueringer: utilgjengelig liste er en feiltilstand, ikke tom historikk", async () => {
  KALL = [];
  SVAR = (sti) => sti === "/v1/rekruttering/evalueringer" ? 500 : ({
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
  })[sti];
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]")), "seksjonen kom aldri");
  const seksjon = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  assert.match(seksjon.textContent,
    new RegExp(t("ui.rekruttering.evalueringer.listefeil").slice(0, 25)));
  assert.doesNotMatch(seksjon.textContent,
    new RegExp(t("ui.rekruttering.evalueringer.ingen")));
});

test("Profiler: uten bestilling:opprett finnes ingen skriveknapper (P2-1)", async () => {
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": prosess(),
           "/v1/rekruttering/stillingsprofiler": profiler(),
           "/v1/rekruttering/evalueringer": { evalueringer: [] } };
  const hoved = nyHoved();
  const leser = ctx();
  leser.scopes = ["decisions:read"];
  visRekruttering(hoved, leser);
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const seksjon = hoved.querySelector("section[aria-labelledby=profil-tittel]");
  assert.ok(seksjon, "profilseksjonen mangler — lesing skal stå åpen");
  assert.match(seksjon.textContent, /Driftskonsulent/);
  // #258 P2-C: profilseksjonen har ingen tbody — «tbody button» var
  // alltid tom, og de negative armene gikk grønt uansett.
  const tekster = [...seksjon.querySelectorAll("button")].map((b) => b.textContent);
  assert.ok(!tekster.includes(t("ui.rekruttering.profiler.ny")),
    "Ny-knappen finnes uten skrive-scope");
  assert.ok(!tekster.includes(t("ui.rekruttering.profiler.rediger")),
    "Rediger-knappen finnes uten skrive-scope");
  // …og med scopet finnes begge (positiv kontroll — fraværstesten alene
  // ville gått grønn på en tom seksjon).
  const hoved2 = nyHoved();
  visRekruttering(hoved2, ctx());
  assert.ok(await vent(() => hoved2.querySelector("table")), "flaten kom aldri");
  const s2 = hoved2.querySelector("section[aria-labelledby=profil-tittel]");
  const t2 = [...s2.querySelectorAll("button")].map((b) => b.textContent);
  assert.ok(t2.includes(t("ui.rekruttering.profiler.ny")));
  assert.ok(t2.includes(t("ui.rekruttering.profiler.rediger")));
});

test("Bestilling: uten bestilling:opprett finnes ingen bestillingsseksjon (P2-7)", async () => {
  // Speilet av profilenes P2-1-test: POST-rutene bak kjeden krever
  // `bestilling:opprett` (app.py), og et skjema uten scopet er en
  // blindvei som først dør server-side.
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": prosess(),
           "/v1/rekruttering/stillingsprofiler": profiler(),
           "/v1/rekruttering/evalueringer": { evalueringer: [] } };
  const hoved = nyHoved();
  const leser = ctx();
  leser.scopes = ["decisions:read"];
  visRekruttering(hoved, leser);
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  assert.equal(hoved.querySelector("section[aria-labelledby=bestill-tittel]"),
    null, "bestillingsseksjonen sto der uten bestilling:opprett");
  assert.equal(hoved.querySelector("#bestill-fil"), null,
    "filvelgeren sto der uten bestilling:opprett");
  assert.equal(hoved.textContent.includes(t("ui.rekruttering.bestill.send")),
    false, "bestillingsknappen sto der uten bestilling:opprett");
  // ... og med scopet står den der.
  const hoved2 = nyHoved();
  visRekruttering(hoved2, ctx());
  assert.ok(await vent(() => hoved2.querySelector("table")), "flaten kom aldri");
  assert.ok(hoved2.querySelector("section[aria-labelledby=bestill-tittel] form"),
    "bestillingsskjemaet mangler med bestilling:opprett");
});

test("Bestilling: hele kjeden — reserver, opplast, bestill (SP-2)", async () => {
  KALL = [];
  const basis = { "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [] },
    "/v1/inndata/reserver": { reservasjon_jti: "j-1",
                              inndata_ref: "inndata:u-1" },
    "/v1/inndata/opplast/j-1": {} };
  let bestillingssvar = { beslutning: "tillat", oppdrag_id: 42 };
  SVAR = (sti) => sti === "/v1/bestilling" ? bestillingssvar : basis[sti];
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  // Mount hentet en TOM liste; fra nå av finnes oppdrag 42 på serveren —
  // det er OPPFRISKNINGEN etter `tillat` som skal få det på skjermen.
  basis["/v1/rekruttering/evalueringer"] = { evalueringer: [
    { oppdrag_id: 42, status: "opprettet",
      opprettet: "2026-08-27T07:00:00+00:00", rapport_klar: false }] };
  const seksjon = hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  assert.ok(seksjon, "bestillingsseksjonen mangler");
  const skjema = seksjon.querySelector("form");
  const filInp = skjema.querySelector("input[type=file]");
  Object.defineProperty(filInp, "files", { configurable: true,
    value: [{ name: "bunt.zip",
              arrayBuffer: async () => new ArrayBuffer(16) }] });
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  await vent(() => seksjon.querySelector("[role=alert]")
    .textContent.includes("42"), 20);
  const stier = KALL.filter((k) => k.metode !== "GET").map((k) => k.sti);
  assert.deepEqual(stier, ["/v1/inndata/reserver",
    "/v1/inndata/opplast/j-1", "/v1/bestilling"]);
  const [res, opp, best] = KALL.filter((k) => k.metode !== "GET");
  assert.ok(res.hoder["Idempotency-Key"], "reservasjonen mangler nøkkel");
  assert.equal(opp.metode, "PUT");
  assert.equal(opp.hoder["content-type"], "application/zip");
  assert.equal(opp.kropp.binaer, 16, "opplastingen sendte ikke bytene");
  assert.ok(best.hoder["Idempotency-Key"], "bestillingen mangler nøkkel");
  assert.deepEqual(best.kropp, { bestillingstype: "rekruttering.evaluering",
    inndata_ref: "inndata:u-1", stillingsprofil_ref: "prof-1@2",
    antall_soknader: 1, omfang: "bunt" });
  // Codex P2 (minste bit av #221): et definitivt `tillat` oppfrisker
  // evalueringslisten — det leverte oppdraget vises uten side-omlasting.
  const evalSeksjon = hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  assert.ok(await vent(() => evalSeksjon.textContent.includes("42")),
    "det leverte oppdraget kom aldri inn i evalueringslisten");
  assert.match(evalSeksjon.textContent,
    new RegExp(t("ui.rekruttering.evalueringer.venter")));
  assert.equal(KALL.filter(
    (k) => k.sti === "/v1/rekruttering/evalueringer").length, 2,
    "listen ble ikke hentet på nytt etter tillat");
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Bestilling: 5xx beholder nøkkel OG opplastet bunt — 4xx roterer nøkkelen", async () => {
  KALL = [];
  const basis = { "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/inndata/reserver": { reservasjon_jti: "j-1",
                              inndata_ref: "inndata:u-1" },
    "/v1/inndata/opplast/j-1": {} };
  let bestillingssvar = 500;
  SVAR = (sti) => sti === "/v1/bestilling" ? bestillingssvar : basis[sti];
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const seksjon = hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  const skjema = seksjon.querySelector("form");
  const filInp = skjema.querySelector("input[type=file]");
  Object.defineProperty(filInp, "files", { configurable: true,
    value: [{ name: "bunt.zip",
              arrayBuffer: async () => new ArrayBuffer(16) }] });
  const send = () => skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  const bestillinger = () =>
    KALL.filter((k) => k.sti === "/v1/bestilling");
  send();
  await vent(() => bestillinger().length === 1, 20);
  bestillingssvar = { beslutning: "tillat", oppdrag_id: 7 };
  send();
  await vent(() => bestillinger().length === 2, 20);
  // 5xx: retry er SAMME operasjon — samme nøkkel, og bunten lastes ALDRI
  // opp på nytt (én reservasjon totalt).
  const [b1, b2] = bestillinger();
  assert.equal(b1.hoder["Idempotency-Key"], b2.hoder["Idempotency-Key"],
    "5xx-retry roterte nøkkelen");
  assert.equal(KALL.filter((k) => k.sti === "/v1/inndata/reserver").length,
    1, "retryen reserverte bunten på nytt");
  await vent(() => seksjon.querySelector("[role=alert]")
    .textContent.includes("7"), 20);
  // 4xx: serveren DØMTE — neste forsøk er en NY operasjon (ny nøkkel),
  // og en NY bunt (skjemaet er nullstilt etter suksessen over).
  Object.defineProperty(skjema.querySelector("input[type=file]"), "files",
    { configurable: true, value: [{ name: "b2.zip",
        arrayBuffer: async () => new ArrayBuffer(8) }] });
  bestillingssvar = 409;
  send();
  await vent(() => bestillinger().length === 3, 20);
  // Vent til CATCHEN har dømt (feilteksten står) — kallet logges i det
  // fetch STARTER, og å sende b4 før b3s dom er et kappløp i testen,
  // ikke i flaten.
  await vent(() => seksjon.querySelector("[role=alert]")
    .textContent === t("ui.rekruttering.bestill.feil"), 20);
  bestillingssvar = { beslutning: "tillat", oppdrag_id: 8 };
  send();
  await vent(() => bestillinger().length === 4, 20);
  const [, , b3, b4] = bestillinger();
  assert.notEqual(b3.hoder["Idempotency-Key"], b4.hoder["Idempotency-Key"],
    "4xx-dommen skulle rotert nøkkelen");
});

test("Bestilling: 409 «nøkkelen er opptatt» beholder nøkkelen (Codex P1)",
  async () => {
    KALL = [];
    const basis = { "/v1/rekruttering/prosesser": prosess(),
      "/v1/rekruttering/stillingsprofiler": profiler(),
      "/v1/inndata/reserver": { reservasjon_jti: "j-1",
                                inndata_ref: "inndata:u-1" },
      "/v1/inndata/opplast/j-1": {} };
    // `utfor_bestilling` fant nøkkelen OPPTATT av en forespørsel som
    // fortsatt går (`bestilling.py:478-485`) og svarer den samme 409
    // `idempotenskonflikt` som en ekte intensjonskonflikt
    // (`:1135-1139`) — men uten dom og uten kvotetrekk: det FØRSTE
    // forsøket kan committe like etter.
    let bestillingssvar = { __status: 409,
      __kropp: { feil: "idempotenskonflikt" } };
    SVAR = (sti) => sti === "/v1/bestilling" ? bestillingssvar : basis[sti];
    const hoved = nyHoved();
    visRekruttering(hoved, ctx());
    assert.ok(await vent(() => hoved.querySelector("table")),
      "flaten kom aldri");
    const seksjon =
      hoved.querySelector("section[aria-labelledby=bestill-tittel]");
    const skjema = seksjon.querySelector("form");
    Object.defineProperty(skjema.querySelector("input[type=file]"), "files",
      { configurable: true, value: [{ name: "bunt.zip",
          arrayBuffer: async () => new ArrayBuffer(16) }] });
    const send = () => skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    const bestillinger = () => KALL.filter((k) => k.sti === "/v1/bestilling");
    send();
    await vent(() => bestillinger().length === 1, 20);
    // Teksten sier det serveren sier: ingenting er avgjort. «Bestillingen
    // feilet» ville vært den samme løgnklassen som `usikkert_utfall` alt
    // lukket for 0/5xx.
    assert.ok(await vent(() => seksjon.querySelector("[role=alert]")
      .textContent === t("ui.rekruttering.bestill.opptatt"), 20),
    "en opptatt nøkkel ble meldt som en dom");
    bestillingssvar = { beslutning: "tillat", oppdrag_id: 9 };
    send();
    await vent(() => bestillinger().length === 2, 20);
    const [b1, b2] = bestillinger();
    assert.equal(b2.hoder["Idempotency-Key"], b1.hoder["Idempotency-Key"],
      "retryen bar en FERSK nøkkel: den første bestillingen kunne da "
      + "committe som nummer to — to oppdrag på samme bunt");
    assert.equal(KALL.filter((k) => k.sti === "/v1/inndata/reserver").length,
      1, "bunten ble reservert på nytt");
  });

test("Bestilling: 409 fra RESERVASJONEN er en død nøkkel, ikke en opptatt",
  async () => {
    KALL = [];
    const basis = { "/v1/rekruttering/prosesser": prosess(),
      "/v1/rekruttering/stillingsprofiler": profiler(),
      "/v1/inndata/opplast/j-1": {} };
    // Grensen for regelen over: kom 409-en FØR `inndataRef` ble satt,
    // traff den reservasjonen — og 058 sier at en brukt/utløpt
    // reservasjon krever en NY nøkkel (P1-3). Koden er den samme;
    // stedet i kjeden er det som skiller.
    let reserversvar = { __status: 409,
      __kropp: { feil: "idempotenskonflikt" } };
    SVAR = (sti) => sti === "/v1/inndata/reserver" ? reserversvar : basis[sti];
    const hoved = nyHoved();
    visRekruttering(hoved, ctx());
    assert.ok(await vent(() => hoved.querySelector("table")),
      "flaten kom aldri");
    const seksjon =
      hoved.querySelector("section[aria-labelledby=bestill-tittel]");
    const skjema = seksjon.querySelector("form");
    Object.defineProperty(skjema.querySelector("input[type=file]"), "files",
      { configurable: true, value: [{ name: "bunt.zip",
          arrayBuffer: async () => new ArrayBuffer(16) }] });
    const send = () => skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    const reservasjoner = () =>
      KALL.filter((k) => k.sti === "/v1/inndata/reserver");
    send();
    await vent(() => reservasjoner().length === 1, 20);
    await vent(() => seksjon.querySelector("[role=alert]")
      .textContent === t("ui.rekruttering.bestill.feil"), 20);
    reserversvar = { reservasjon_jti: "j-1", inndata_ref: "inndata:u-1" };
    send();
    await vent(() => reservasjoner().length === 2, 20);
    const [r1, r2] = reservasjoner();
    assert.notEqual(r2.hoder["Idempotency-Key"], r1.hoder["Idempotency-Key"],
      "den døde reservasjonsnøkkelen ble beholdt — den svarer konflikt "
      + "i det uendelige");
  });

test("Bestilling: 409 «bunten er ubrukelig» er ikke feltene (eierdom (c))",
  async () => {
    KALL = [];
    const basis = { "/v1/rekruttering/prosesser": prosess(),
      "/v1/rekruttering/stillingsprofiler": profiler(),
      "/v1/inndata/reserver": { reservasjon_jti: "j-1",
                                inndata_ref: "inndata:u-1" },
      "/v1/inndata/opplast/j-1": {} };
    // 058-formen: ETT svar for alle de TERMINALE årsakene — ukjent,
    // utløpt, ikke ferdig lastet, alt bundet. Ingen av dem står i et
    // felt brukeren kan rette. Den forbigående naboen har siden #215
    // sin egen kode og sin egen test under.
    let bestillingssvar = { __status: 409,
      __kropp: { feil: "inndata_ubrukelig" } };
    SVAR = (sti) => sti === "/v1/bestilling" ? bestillingssvar : basis[sti];
    const hoved = nyHoved();
    visRekruttering(hoved, ctx());
    assert.ok(await vent(() => hoved.querySelector("table")),
      "flaten kom aldri");
    const seksjon =
      hoved.querySelector("section[aria-labelledby=bestill-tittel]");
    const skjema = seksjon.querySelector("form");
    Object.defineProperty(skjema.querySelector("input[type=file]"), "files",
      { configurable: true, value: [{ name: "bunt.zip",
          arrayBuffer: async () => new ArrayBuffer(16) }] });
    const send = () => skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    const bestillinger = () => KALL.filter((k) => k.sti === "/v1/bestilling");
    send();
    await vent(() => bestillinger().length === 1, 20);
    assert.ok(await vent(() => seksjon.querySelector("[role=alert]")
      .textContent === t("ui.rekruttering.bestill.bunt_ubrukelig"), 20),
    "buntens 409 ble meldt som en feil i skjemafeltene");
    assert.notEqual(seksjon.querySelector("[role=alert]").textContent,
      t("ui.rekruttering.bestill.feil"));
    // DEN TERMINALE KODEN ROTERER NØKKELEN — mot en død bunt er «prøv
    // igjen, samme operasjon» en løgn. Skillet bæres av utadkoden
    // (#215): den forbigående naboen under BEHOLDER nøkkelen.
    bestillingssvar = { beslutning: "tillat", oppdrag_id: 11 };
    send();
    await vent(() => bestillinger().length === 2, 20);
    const [b1, b2] = bestillinger();
    assert.notEqual(b2.hoder["Idempotency-Key"], b1.hoder["Idempotency-Key"],
      "den terminale koden beholdt nøkkelen — det er den forbigående "
      + "naboens (inndata_opptatt) økonomi, ikke denne kodens");
  });

test("Bestilling: 409 «bunten er opptatt» beholder nøkkelen (#215)",
  async () => {
    KALL = [];
    const basis = { "/v1/rekruttering/prosesser": prosess(),
      "/v1/rekruttering/stillingsprofiler": profiler(),
      "/v1/inndata/reserver": { reservasjon_jti: "j-1",
                                inndata_ref: "inndata:u-1" },
      "/v1/inndata/opplast/j-1": {} };
    // #215: en annen bestilling holder bunten AKKURAT NÅ. Ingen dom,
    // ingen kvote — retry med SAMME nøkkel er samme operasjon, og
    // teksten sier «prøv igjen om et øyeblikk» i stedet for å sende
    // brukeren til filvelgeren.
    let bestillingssvar = { __status: 409,
      __kropp: { feil: "inndata_opptatt" } };
    SVAR = (sti) => sti === "/v1/bestilling" ? bestillingssvar : basis[sti];
    const hoved = nyHoved();
    visRekruttering(hoved, ctx());
    assert.ok(await vent(() => hoved.querySelector("table")),
      "flaten kom aldri");
    const seksjon =
      hoved.querySelector("section[aria-labelledby=bestill-tittel]");
    const skjema = seksjon.querySelector("form");
    Object.defineProperty(skjema.querySelector("input[type=file]"), "files",
      { configurable: true, value: [{ name: "bunt.zip",
          arrayBuffer: async () => new ArrayBuffer(16) }] });
    const send = () => skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    const bestillinger = () => KALL.filter((k) => k.sti === "/v1/bestilling");
    send();
    await vent(() => bestillinger().length === 1, 20);
    assert.ok(await vent(() => seksjon.querySelector("[role=alert]")
      .textContent === t("ui.rekruttering.bestill.bunt_opptatt"), 20),
    "buntlåsens 409 fikk ikke sin egen forbigående tekst");
    assert.notEqual(seksjon.querySelector("[role=alert]").textContent,
      t("ui.rekruttering.bestill.bunt_ubrukelig"),
      "den forbigående koden falt i den terminale armen");
    // NØKKELØKONOMIEN ER LØFTET KODEN GIR: retry er SAMME operasjon.
    // MUTASJONEN SOM DREPER DENNE: fjern `buntOpptatt` fra
    // nøkkel-vilkåret i submit-handleren.
    bestillingssvar = { beslutning: "tillat", oppdrag_id: 11 };
    send();
    await vent(() => bestillinger().length === 2, 20);
    const [b1, b2] = bestillinger();
    assert.equal(b2.hoder["Idempotency-Key"], b1.hoder["Idempotency-Key"],
      "den forbigående koden roterte nøkkelen — retryen ble en NY "
      + "operasjon i stedet for den samme");
  });

test("Bestilling: en FORLATT bunt får ikke «samme operasjon» av 409-en "
  + "(Codex P2)", async () => {
  // Grensevakten til testen over: der ER retryen den samme operasjonen,
  // fordi nøkkelen står. Bytter brukeren fil mens POST-en flyr, nuller
  // `change` derimot `inndataRef` og forkaster `bestillIdem` samtidig som
  // den bumper `generasjon` — og da beholder 409-armen bare en nøkkel som
  // er borte. `buntOpptatt` ble likevel valgt FØR `forlatt`, så teksten
  // lovte at et nytt forsøk gjentar SAMME operasjon mens neste Send bar
  // en fersk nøkkel på en NY bunt. Samme løgnklasse som
  // `sendt_forlatt_bunt`, `stoppet_forlatt` og `forlatt_usikkert`.
  //
  // MUTASJONEN SOM DREPER DENNE: la `buntOpptatt`-armen velge
  // `bunt_opptatt` uten `forlatt`-vurderingen.
  KALL = [];
  let slippBestilling;
  let bestillingssvar = new Promise((r) => { slippBestilling = r; });
  let reservasjon = { reservasjon_jti: "j-1", inndata_ref: "inndata:u-1" };
  SVAR = (sti) => {
    if (sti === "/v1/rekruttering/prosesser") return prosess();
    if (sti === "/v1/rekruttering/stillingsprofiler") return profiler();
    if (sti === "/v1/inndata/reserver") return reservasjon;
    if (sti.startsWith("/v1/inndata/opplast/")) return {};
    if (sti === "/v1/bestilling") return bestillingssvar;
    return undefined;
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const seksjon =
    hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  const skjema = seksjon.querySelector("form");
  const send = skjema.querySelector("button[type=submit]");
  const filInp = skjema.querySelector("input[type=file]");
  const velgFil = (navn) => {
    Object.defineProperty(filInp, "files", { configurable: true,
      value: [{ name: navn, arrayBuffer: async () => new ArrayBuffer(16) }] });
    filInp.dispatchEvent(new window.Event("change", { bubbles: true }));
  };
  const bestillinger = () => KALL.filter((k) => k.sti === "/v1/bestilling");
  const bestill = () => skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  velgFil("bunt.zip");
  bestill();
  assert.ok(await vent(() => bestillinger().length === 1, 40),
    "bestillingen kom aldri");
  // Brukeren bytter bunt mens bestillingen står UBESVART — og FØRST DA
  // svarer serveren at buntlåsen er holdt.
  reservasjon = { reservasjon_jti: "j-2", inndata_ref: "inndata:u-2" };
  velgFil("bunt2.zip");
  slippBestilling({ __status: 409, __kropp: { feil: "inndata_opptatt" } });
  assert.ok(await vent(() => !send.disabled, 40), "kjeden ble aldri ferdig");
  const melding = seksjon.querySelector("[role=alert]").textContent;
  assert.notEqual(melding, t("ui.rekruttering.bestill.bunt_opptatt"),
    "en forlatt bunt lovte fortsatt at retry er SAMME operasjon");
  // `t()` faller tilbake til nøkkelen selv når den mangler, og da ville
  // linjen under målt seg selv: begge sider hadde vært samme streng.
  assert.notEqual(melding, "ui.rekruttering.bestill.bunt_opptatt_forlatt",
    "locale mangler nøkkelen — brukeren fikk en rå identifikator");
  assert.equal(melding, t("ui.rekruttering.bestill.bunt_opptatt_forlatt"));
  // ... og setningen er sann: neste Send ER en ny operasjon, på den nye
  // bunten og med en fersk nøkkel.
  bestillingssvar = { beslutning: "tillat", oppdrag_id: 12 };
  bestill();
  assert.ok(await vent(() => bestillinger().length === 2, 40),
    "den nye bestillingen kom aldri");
  const [b1, b2] = bestillinger();
  assert.equal(b1.kropp.inndata_ref, "inndata:u-1");
  assert.equal(b2.kropp.inndata_ref, "inndata:u-2",
    "den nye bestillingen gikk på den forlatte bunten");
  assert.notEqual(b2.hoder["Idempotency-Key"], b1.hoder["Idempotency-Key"],
    "en ny kropp bar den forlatte intensjonens nøkkel");
});

test("Bestilling: den terminale teksten beskriver ikke den forbigående "
  + "naboen (#215)", () => {
  // Cursor P2: kodene ble delt i #215, men `bunt_ubrukelig` bar fortsatt
  // kollaps-teksten «enten holder en annen bestilling den akkurat nå,
  // eller …». Da lyver den terminale armen om årsaksklassen og blander
  // «vent, samme nøkkel» med «velg ny fil» — nøyaktig skillet koden
  // alene nå skal bære. Den forbigående teksten eier den klausulen.
  //
  // MUTASJONEN SOM DREPER DENNE: legg den forbigående klausulen tilbake
  // i `bunt_ubrukelig` i nb.json eller en.json.
  const en = JSON.parse(readFileSync(join(ROT, "locales", "en.json"), "utf-8"));
  const ubrukelig = "ui.rekruttering.bestill.bunt_ubrukelig";
  const opptatt = "ui.rekruttering.bestill.bunt_opptatt";
  assert.ok(!/holder .* akkurat nå/.test(t(ubrukelig)),
    "nb: den terminale teksten beskriver fortsatt den forbigående naboen");
  assert.ok(!/holding .* right now/.test(en[ubrukelig]),
    "en: den terminale teksten beskriver fortsatt den forbigående naboen");
  // …og klausulen står der den hører hjemme.
  assert.ok(/holder .* akkurat nå/.test(t(opptatt)), "nb: bunt_opptatt");
  assert.ok(/holding .* right now/.test(en[opptatt]), "en: bunt_opptatt");
});

test("Bestilling: den forbigående teksten lover ikke ubrukt kvote "
  + "(Codex P2)", () => {
  // `inndata_opptatt` bæres av TO grener i `utfor_bestilling`: den
  // vanlige (buntlåsen tas FØR beslutningen — ingen dom, ingen kvote) og
  // gjenopprettingen, der et alt committet `TILLAT` re-tar låsen
  // (`bestilling.py:617-626`) og svarer den samme koden. På den andre er
  // dommen felt og kvoten trukket, så «Ingenting er avgjort, og ingen
  // kvote er brukt» var to usanne setninger. Teksten skal si bare det
  // BEGGE grenene garanterer — holdt bunt, trygg retry med samme nøkkel.
  //
  // MUTASJONEN SOM DREPER DENNE: legg kvote-/dom-løftet tilbake i
  // `bunt_opptatt` i nb.json eller en.json.
  const en = JSON.parse(readFileSync(join(ROT, "locales", "en.json"), "utf-8"));
  const opptatt = "ui.rekruttering.bestill.bunt_opptatt";
  const forlatt = "ui.rekruttering.bestill.bunt_opptatt_forlatt";
  for (const nokkel of [opptatt, forlatt]) {
    assert.ok(!/ingen kvote/i.test(t(nokkel)), `nb: ${nokkel} lover kvote`);
    assert.ok(!/no quota/i.test(en[nokkel]), `en: ${nokkel} lover kvote`);
    assert.ok(!/ingenting er avgjort/i.test(t(nokkel)),
      `nb: ${nokkel} lover at ingenting er avgjort`);
    assert.ok(!/nothing has been decided/i.test(en[nokkel]),
      `en: ${nokkel} lover at ingenting er avgjort`);
  }
  // …og det koden FAKTISK garanterer står fortsatt der (testen over
  // eier holdt-bunt-klausulen; denne eier nøkkelløftet).
  assert.ok(/SAMME operasjonen/.test(t(opptatt)), "nb: retry-løftet falt ut");
  assert.ok(/SAME operation/.test(en[opptatt]), "en: retry-løftet falt ut");
});

test("Bestilling: endret kropp etter usikkert svar gir NY nøkkel (P1-2)", async () => {
  KALL = [];
  const basis = { "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/inndata/reserver": { reservasjon_jti: "j-1",
                              inndata_ref: "inndata:u-1" },
    "/v1/inndata/opplast/j-1": {} };
  let bestillingssvar = 500;
  SVAR = (sti) => sti === "/v1/bestilling" ? bestillingssvar : basis[sti];
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const seksjon = hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  const skjema = seksjon.querySelector("form");
  const filInp = skjema.querySelector("input[type=file]");
  Object.defineProperty(filInp, "files", { configurable: true,
    value: [{ name: "bunt.zip",
              arrayBuffer: async () => new ArrayBuffer(16) }] });
  const send = () => skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  const bestillinger = () => KALL.filter((k) => k.sti === "/v1/bestilling");
  send();
  await vent(() => bestillinger().length === 1, 20);
  // Usikkert utfall (5xx): nøkkelen står — helt til brukeren endrer
  // kroppen. Da er neste innsending en ANNEN intensjon, og å bære den
  // gamle nøkkelen ville enten kollidert eller replayet den forrige.
  const antall = skjema.querySelector("#bestill-antall");
  antall.value = "3";
  antall.dispatchEvent(new window.Event("input", { bubbles: true }));
  bestillingssvar = { beslutning: "tillat", oppdrag_id: 9 };
  send();
  await vent(() => bestillinger().length === 2, 20);
  const [b1, b2] = bestillinger();
  assert.equal(b1.kropp.antall_soknader, 1);
  assert.equal(b2.kropp.antall_soknader, 3);
  assert.notEqual(b1.hoder["Idempotency-Key"], b2.hoder["Idempotency-Key"],
    "endret kropp bar fortsatt den gamle intensjonens nøkkel");
  // Bunten er den samme: feltendringen roterer bestillingsnøkkelen, ikke
  // reservasjonen.
  assert.equal(KALL.filter((k) => k.sti === "/v1/inndata/reserver").length, 1,
    "feltendringen reserverte bunten på nytt");
});

test("Bestilling: en FORLATT bestilling lover ikke «samme operasjon» (Cursor P2)",
  async () => {
    // Filvelgeren er den ENE kontrollen `frys` ikke tar: et bunt-bytte
    // under den flygende POST-en bumper `generasjon` og nullstiller
    // `bestillIdem` — og det er riktig, en ny bunt er en ny kropp. Men
    // teksten for det uvisse utfallet lovte fortsatt at et nytt forsøk
    // gjentar SAMME operasjon, og den nøkkelen fantes ikke lenger: et
    // «prøv igjen» ville lagt bestilling nummer to oppå en som ved 0/5xx
    // godt kan være committet.
    KALL = [];
    let slippBestilling;
    let bestillingssvar = new Promise((r) => { slippBestilling = r; });
    let reservasjon = { reservasjon_jti: "j-1", inndata_ref: "inndata:u-1" };
    SVAR = (sti) => {
      if (sti === "/v1/rekruttering/prosesser") return prosess();
      if (sti === "/v1/rekruttering/stillingsprofiler") return profiler();
      if (sti === "/v1/inndata/reserver") return reservasjon;
      if (sti.startsWith("/v1/inndata/opplast/")) return {};
      if (sti === "/v1/bestilling") return bestillingssvar;
      return undefined;
    };
    const hoved = nyHoved();
    visRekruttering(hoved, ctx());
    assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
    const seksjon =
      hoved.querySelector("section[aria-labelledby=bestill-tittel]");
    const skjema = seksjon.querySelector("form");
    const send = skjema.querySelector("button[type=submit]");
    const filInp = skjema.querySelector("input[type=file]");
    const velgFil = (navn) => {
      Object.defineProperty(filInp, "files", { configurable: true,
        value: [{ name: navn, arrayBuffer: async () => new ArrayBuffer(16) }] });
      filInp.dispatchEvent(new window.Event("change", { bubbles: true }));
    };
    const bestillinger = () => KALL.filter((k) => k.sti === "/v1/bestilling");
    const bestill = () => skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    velgFil("bunt.zip");
    bestill();
    assert.ok(await vent(() => bestillinger().length === 1, 40),
      "bestillingen kom aldri");
    // Brukeren bytter bunt mens bestillingen står UBESVART: intensjonen er
    // forlatt, og nøkkelen med den.
    reservasjon = { reservasjon_jti: "j-2", inndata_ref: "inndata:u-2" };
    velgFil("bunt2.zip");
    slippBestilling(500);
    assert.ok(await vent(() => !send.disabled, 40), "kjeden ble aldri ferdig");
    const melding = seksjon.querySelector("[role=alert]").textContent;
    assert.notEqual(melding, t("ui.rekruttering.usikkert_utfall"),
      "en forlatt intensjon lovte fortsatt at retry er SAMME operasjon");
    assert.notEqual(melding, t("ui.rekruttering.bestill.avbrutt"),
      "teksten lovte at ingenting er bestilt — det vet vi ikke her");
    // `t()` faller tilbake til nøkkelen selv når den mangler, og da ville
    // linjen under målt seg selv: begge sider hadde vært samme streng.
    assert.notEqual(melding, "ui.rekruttering.bestill.forlatt_usikkert",
      "locale mangler nøkkelen — brukeren fikk en rå identifikator");
    assert.equal(melding, t("ui.rekruttering.bestill.forlatt_usikkert"));
    // ... og neste Send ER en ny operasjon: ny bunt, ny reservasjon, ny
    // nøkkel. Teksten over er den eneste grunnen brukeren har til å vite
    // det før hun trykker.
    bestillingssvar = { beslutning: "tillat", oppdrag_id: 12 };
    bestill();
    assert.ok(await vent(() => bestillinger().length === 2, 40),
      "den nye bestillingen kom aldri");
    const [b1, b2] = bestillinger();
    assert.equal(b1.kropp.inndata_ref, "inndata:u-1");
    assert.equal(b2.kropp.inndata_ref, "inndata:u-2",
      "den nye bestillingen gikk på den forlatte bunten");
    assert.notEqual(b2.hoder["Idempotency-Key"], b1.hoder["Idempotency-Key"],
      "en ny kropp bar den forlatte intensjonens nøkkel");
    // MUTASJONEN SOM DREPER DENNE: la `forlatt`-armen falle tilbake til
    // `usikkert_utfall`.
  });

test("Bestilling: kvitteringen navngir bunten som ble sendt (Cursor P2)",
  async () => {
    // Samme vindu som testen over, men på det VISSE utfallet: svaret er
    // `tillat`, oppdraget ER committet — og nettopp derfor er «Bestillingen
    // er levert: tillat, oppdrag N» for lite. Skjemaet står alt med den nye
    // bunten (nullstillingen hoppes over, riktig), så kvitteringen ble lest
    // mot en fil den ikke gjaldt. Feilarmen fikk speilingen sin i
    // `forlatt_usikkert`; dette er den for suksessarmen.
    KALL = [];
    let slippBestilling;
    let bestillingssvar = new Promise((r) => { slippBestilling = r; });
    let reservasjon = { reservasjon_jti: "j-1", inndata_ref: "inndata:u-1" };
    SVAR = (sti) => {
      if (sti === "/v1/rekruttering/prosesser") return prosess();
      if (sti === "/v1/rekruttering/stillingsprofiler") return profiler();
      if (sti === "/v1/inndata/reserver") return reservasjon;
      if (sti.startsWith("/v1/inndata/opplast/")) return {};
      if (sti === "/v1/bestilling") return bestillingssvar;
      return undefined;
    };
    const hoved = nyHoved();
    visRekruttering(hoved, ctx());
    assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
    const seksjon =
      hoved.querySelector("section[aria-labelledby=bestill-tittel]");
    const skjema = seksjon.querySelector("form");
    const send = skjema.querySelector("button[type=submit]");
    const filInp = skjema.querySelector("input[type=file]");
    const velgFil = (navn) => {
      Object.defineProperty(filInp, "files", { configurable: true,
        value: [{ name: navn, arrayBuffer: async () => new ArrayBuffer(16) }] });
      filInp.dispatchEvent(new window.Event("change", { bubbles: true }));
    };
    const bestillinger = () => KALL.filter((k) => k.sti === "/v1/bestilling");
    const bestill = () => skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    velgFil("bunt.zip");
    bestill();
    assert.ok(await vent(() => bestillinger().length === 1, 40),
      "bestillingen kom aldri");
    // Bunt-bytte mens bestillingen står ubesvart — og DA svarer serveren
    // `tillat` på den forrige bunten.
    reservasjon = { reservasjon_jti: "j-2", inndata_ref: "inndata:u-2" };
    velgFil("bunt2.zip");
    slippBestilling({ beslutning: "tillat", oppdrag_id: 77 });
    assert.ok(await vent(() => !send.disabled, 40), "kjeden ble aldri ferdig");
    const melding = seksjon.querySelector("[role=alert]").textContent;
    assert.notEqual(melding, t("ui.rekruttering.bestill.sendt")
      .replace("{oppdrag}", "77").replace("{beslutning}", "tillat"),
    "kvitteringen sa AT noe ble levert, ikke HVA — uten buntbinding");
    // `t()` faller tilbake til nøkkelen selv når den mangler: uten denne
    // hadde linjen under kunnet passere på en rå identifikator.
    assert.ok(!melding.includes("ui.rekruttering.bestill.sendt_forlatt_bunt"),
      "locale mangler nøkkelen — brukeren fikk en rå identifikator");
    assert.ok(melding.includes(t("ui.rekruttering.bestill.sendt_forlatt_bunt")
      .replaceAll("{filnavn}", "bunt.zip")),
    "kvitteringen bandt ikke oppdraget til den SENDTE bunten");
    // Den harde kjernen: navnet er kjedens eget, fanget før `await` — ikke
    // det filvelgeren viser når svaret lander.
    assert.ok(melding.includes("bunt.zip"), "den sendte bunten er ikke navngitt");
    assert.ok(!melding.includes("bunt2.zip"),
      "kvitteringen navnga filen brukeren nettopp valgte, ikke den bestilte");
    // ... og brukerens ferske valg overlevde: neste Send er en NY
    // bestilling på den nye bunten, med fersk nøkkel — ingen replay av
    // oppdrag 77.
    bestillingssvar = { beslutning: "tillat", oppdrag_id: 78 };
    bestill();
    assert.ok(await vent(() => bestillinger().length === 2, 40),
      "den nye bestillingen kom aldri");
    const [b1, b2] = bestillinger();
    assert.equal(b1.kropp.inndata_ref, "inndata:u-1");
    assert.equal(b2.kropp.inndata_ref, "inndata:u-2",
      "den nye bestillingen gikk på den forlatte bunten");
    assert.notEqual(b2.hoder["Idempotency-Key"], b1.hoder["Idempotency-Key"],
      "en ny kropp bar den forlatte intensjonens nøkkel");
    // MUTASJONEN SOM DREPER DENNE: les `tilstand.filnavn` ETTER `await`
    // (da blir navnet «bunt2.zip»), eller la `tillat`-armen falle tilbake
    // til ren `bestill.sendt` uten speilingen.
  });

test("Bestilling: STOPP/unntak lover ikke «bunten står klar» når den er forlatt (Cursor P2)",
  async () => {
    // Tredje og siste arm i samme løgnklasse som `sendt_forlatt_bunt`
    // (tillat) og `forlatt_usikkert` (0/5xx): `stoppet`/`unntak` lover at
    // bunten ikke er brukt opp og står klar. Byttet brukeren fil mens
    // dommen var underveis, er DEN bunten ute av skjemaet — `change`
    // nullet `inndataRef` — så løftet peker på en fil som verken er
    // reservert eller lastet opp.
    KALL = [];
    let slippBestilling;
    let bestillingssvar = new Promise((r) => { slippBestilling = r; });
    let reservasjon = { reservasjon_jti: "j-1", inndata_ref: "inndata:u-1" };
    SVAR = (sti) => {
      if (sti === "/v1/rekruttering/prosesser") return prosess();
      if (sti === "/v1/rekruttering/stillingsprofiler") return profiler();
      if (sti === "/v1/inndata/reserver") return reservasjon;
      if (sti.startsWith("/v1/inndata/opplast/")) return {};
      if (sti === "/v1/bestilling") return bestillingssvar;
      return undefined;
    };
    const hoved = nyHoved();
    visRekruttering(hoved, ctx());
    assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
    const seksjon =
      hoved.querySelector("section[aria-labelledby=bestill-tittel]");
    const skjema = seksjon.querySelector("form");
    const send = skjema.querySelector("button[type=submit]");
    const filInp = skjema.querySelector("input[type=file]");
    const velgFil = (navn) => {
      Object.defineProperty(filInp, "files", { configurable: true,
        value: [{ name: navn, arrayBuffer: async () => new ArrayBuffer(16) }] });
      filInp.dispatchEvent(new window.Event("change", { bubbles: true }));
    };
    const bestillinger = () => KALL.filter((k) => k.sti === "/v1/bestilling");
    const alert = () => seksjon.querySelector("[role=alert]").textContent;
    // ARM 1 — STOPP med forlatt bunt.
    velgFil("bunt.zip");
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    assert.ok(await vent(() => bestillinger().length === 1, 40),
      "bestillingen kom aldri");
    reservasjon = { reservasjon_jti: "j-2", inndata_ref: "inndata:u-2" };
    velgFil("bunt2.zip");
    slippBestilling({ beslutning: "stopp", begrunnelse: [], oppdrag_id: null });
    assert.ok(await vent(() => !send.disabled, 40), "kjeden ble aldri ferdig");
    assert.ok(!alert().includes(t("ui.rekruttering.bestill.stoppet")),
      "STOPP lovet fortsatt at den forlatte bunten «står klar»");
    // `t()` faller tilbake til nøkkelen selv: uten denne kunne linjen
    // under passere på en rå identifikator.
    assert.ok(!alert().includes("ui.rekruttering.bestill.stoppet_forlatt"),
      "locale mangler nøkkelen — brukeren fikk en rå identifikator");
    assert.ok(alert().includes("bunt.zip"), "dommens egen bunt er ikke navngitt");
    assert.ok(!alert().includes("bunt2.zip"),
      "dommen ble tilskrevet filen brukeren nettopp valgte");
    // ARM 2 — unntakskøen, samme vindu.
    bestillingssvar = new Promise((r) => { slippBestilling = r; });
    reservasjon = { reservasjon_jti: "j-3", inndata_ref: "inndata:u-3" };
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    assert.ok(await vent(() => bestillinger().length === 2, 40),
      "den andre bestillingen kom aldri");
    reservasjon = { reservasjon_jti: "j-4", inndata_ref: "inndata:u-4" };
    velgFil("bunt3.zip");
    slippBestilling({ beslutning: "brudd", begrunnelse: [], unntak_id: 5 });
    assert.ok(await vent(() => !send.disabled, 40), "andre kjede ble aldri ferdig");
    assert.notEqual(alert(), t("ui.rekruttering.bestill.unntak"),
      "unntakskøen lovet fortsatt at den forlatte bunten «står klar»");
    assert.ok(!alert().includes("ui.rekruttering.bestill.unntak_forlatt"),
      "locale mangler nøkkelen — brukeren fikk en rå identifikator");
    assert.ok(alert().includes("bunt2.zip"), "saken er ikke bundet til sin bunt");
    assert.ok(!alert().includes("bunt3.zip"),
      "saken ble tilskrevet filen brukeren nettopp valgte");
    const brudd = await alvorligeBrudd(hoved);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
    // MUTASJONEN SOM DREPER DENNE: la `else`-grenen falle tilbake til ren
    // `bestill.stoppet`/`bestill.unntak` uten `forlatt`-vurderingen.
  });

test("Bestilling: profilen er låst mens kroppen er underveis (Cursor P1)", async () => {
  // `stillingsprofil_ref` var det ENESTE kroppsfeltet uten lås. To vinduer
  // sto åpne: i opplastingsvinduet (før `kropp` bygges) kunne profilen gli
  // fra den brukeren trykket Send på, og under den flygende POST-en kastet
  // `change`-handlerens `nyIntensjon()` nøkkelen kallet eide — så retryen
  // `usikkert_utfall` lover er «SAMME operasjon», bar en fersk nøkkel.
  KALL = [];
  const toProfiler = profiler();
  toProfiler.profiler.push({ ...toProfiler.profiler[0],
    profil_id: "prof-2", versjon: 1, navn: "Rådgiver" });
  let slippOpplast;
  let opplastsvar = new Promise((r) => { slippOpplast = r; });
  let slippBestilling;
  let bestillingssvar = new Promise((r) => { slippBestilling = r; });
  const basis = { "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": toProfiler,
    "/v1/inndata/reserver": { reservasjon_jti: "j-1",
                              inndata_ref: "inndata:u-1" } };
  SVAR = (sti) => {
    if (sti === "/v1/inndata/opplast/j-1") return opplastsvar;
    if (sti === "/v1/bestilling") return bestillingssvar;
    return basis[sti];
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const seksjon = hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  const skjema = seksjon.querySelector("form");
  const send = skjema.querySelector("button[type=submit]");
  const profil = skjema.querySelector("#bestill-profil");
  assert.equal(profil.options.length, 2, "testen trenger noe å bytte TIL");
  Object.defineProperty(skjema.querySelector("input[type=file]"), "files",
    { configurable: true, value: [{ name: "bunt.zip",
        arrayBuffer: async () => new ArrayBuffer(16) }] });
  const bestillinger = () => KALL.filter((k) => k.sti === "/v1/bestilling");
  // Et bytte er et bytte enten det kommer fra en finger eller fra en
  // syntetisk `change`: nettleseren sperrer det første, låsen det andre.
  const forsokBytte = (verdi) => {
    profil.value = verdi;
    profil.dispatchEvent(new window.Event("change", { bubbles: true }));
  };
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  // VINDU 1 — opplastingen henger, kroppen er ikke bygget ennå.
  assert.ok(await vent(() =>
    KALL.some((k) => k.sti === "/v1/inndata/opplast/j-1"), 20),
    "opplastingen startet aldri");
  assert.equal(profil.disabled, true,
    "profilen kunne byttes midt i en pågående opplasting");
  assert.equal(skjema.getAttribute("aria-busy"), "true");
  forsokBytte("prof-2@1");
  assert.equal(profil.value, "prof-1@2",
    "det låste valget gled under opplastingen");
  slippOpplast({});
  // VINDU 2 — bestillingen henger, og NÅ eier kallet en nøkkel.
  assert.ok(await vent(() => bestillinger().length === 1, 20),
    "bestillingen kom aldri");
  assert.equal(profil.disabled, true,
    "profilen kunne byttes mens bestillingen sto ubesvart");
  forsokBytte("prof-2@1");
  assert.equal(profil.value, "prof-1@2",
    "det låste valget gled under bestillingen");
  // 5xx: utfallet er ukjent, og da er retry med SAMME nøkkel nettopp det
  // SP-2 finnes for.
  slippBestilling(500);
  assert.ok(await vent(() => !send.disabled, 40), "kjeden ble aldri ferdig");
  assert.equal(seksjon.querySelector("[role=alert]").textContent,
    t("ui.rekruttering.usikkert_utfall"));
  assert.equal(profil.disabled, false, "profilen ble stående frosset");
  assert.equal(skjema.hasAttribute("aria-busy"), false);
  bestillingssvar = { beslutning: "tillat", oppdrag_id: 11 };
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() => bestillinger().length === 2, 40),
    "retryen kom aldri");
  const [b1, b2] = bestillinger();
  assert.equal(b1.kropp.stillingsprofil_ref, "prof-1@2",
    "kroppen bar en annen profil enn den Send ble trykket på");
  assert.equal(b2.kropp.stillingsprofil_ref, b1.kropp.stillingsprofil_ref,
    "retryen bestilte på en annen profil enn den første");
  assert.equal(b2.hoder["Idempotency-Key"], b1.hoder["Idempotency-Key"],
    "retryen etter et usikkert utfall bar en FERSK nøkkel");
  // Bunten lastes aldri opp på nytt: retryen er samme operasjon hele veien.
  assert.equal(KALL.filter((k) => k.sti === "/v1/inndata/reserver").length, 1,
    "retryen reserverte bunten på nytt");
  // MUTASJONEN SOM DREPER DENNE: fjern `profilVelger.disabled = paa` i
  // `frys` (vindu 1 faller), eller `paagaaende`-vakten i profilvelgerens
  // `change` (begge vinduene faller).
});

test("Bestilling: en opplastet bunt overlever prosessbyttet SYNLIG (P2-6)", async () => {
  KALL = [];
  const to = prosess();
  to.prosesser.push({ ...to.prosesser[0], prosess_id: "p-2" });
  const basis = { "/v1/rekruttering/prosesser": to,
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/inndata/reserver": { reservasjon_jti: "j-1",
                              inndata_ref: "inndata:u-1" },
    "/v1/inndata/opplast/j-1": {} };
  let bestillingssvar = 500;
  SVAR = (sti) => sti === "/v1/bestilling" ? bestillingssvar : basis[sti];
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const bestill = () =>
    hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  const skjema = bestill().querySelector("form");
  const send = skjema.querySelector("button[type=submit]");
  const fil = skjema.querySelector("input[type=file]");
  Object.defineProperty(fil, "files", { configurable: true,
    value: [{ name: "bunt.zip",
              arrayBuffer: async () => new ArrayBuffer(16) }] });
  // Nettleseren melder valget som `change` — det er der flaten fanger
  // filnavnet, og navnet er hele poenget med denne testen.
  fil.dispatchEvent(new window.Event("change", { bubbles: true }));
  // Bunten kommer opp, men bestillingen svarer 5xx: buntens referanse
  // står i økten.
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() => !send.disabled, 40), "runden ble aldri ferdig");
  assert.equal(KALL.filter((k) => k.sti === "/v1/bestilling").length, 1);
  // Brukeren bytter prosess — hele flaten tegnes på nytt, og fil-inputen
  // er tom igjen.
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  // #183: byttet henter først, tegner så. Uten ventingen leses det GAMLE
  // skjemaet — og da måler testen tilstanden før byttet, ikke etter.
  assert.ok(await vent(() => {
    const v = hoved.querySelector("#rekrut-prosessvelger");
    return v && v.value === "p-2" && !v.disabled;
  }), "prosessbyttet ble aldri tegnet");
  const nyttSkjema = bestill().querySelector("form");
  const nyFil = nyttSkjema.querySelector("input[type=file]");
  assert.equal(nyFil.files.length, 0, "testen antar en tom filvelger");
  assert.match(bestill().textContent, /bunt\.zip/,
    "den opplastede bunten står ikke navngitt i skjemaet");
  assert.equal(nyFil.hasAttribute("required"), false,
    "filen kreves fortsatt, selv om bunten alt er lastet opp");
  // ... og bestillingen går på den lagrede bunten, uten en ny opplasting.
  bestillingssvar = { beslutning: "tillat", oppdrag_id: 11 };
  nyttSkjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  await vent(() => KALL.filter((k) => k.sti === "/v1/bestilling").length === 2,
    40);
  const siste = KALL.filter((k) => k.sti === "/v1/bestilling").pop();
  assert.equal(siste.kropp.inndata_ref, "inndata:u-1");
  assert.equal(KALL.filter((k) => k.sti === "/v1/inndata/reserver").length, 1,
    "bunten ble reservert på nytt etter prosessbyttet");
  // Kvitteringen nullstiller: bunten er forbrukt, og filen kreves igjen.
  await vent(() => bestill().querySelector("[role=alert]")
    .textContent.includes("11"), 20);
  assert.doesNotMatch(bestill().textContent, /bunt\.zip/,
    "den forbrukte bunten står fortsatt navngitt");
  assert.equal(
    bestill().querySelector("input[type=file]").hasAttribute("required"), true);
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Bestilling: et tapt svar meldes som uvisst, ikke som «feilet» (P2-4)", async () => {
  KALL = [];
  const basis = { "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/inndata/reserver": { reservasjon_jti: "j-1",
                              inndata_ref: "inndata:u-1" },
    "/v1/inndata/opplast/j-1": {} };
  let bestillingssvar = 500;
  SVAR = (sti) => sti === "/v1/bestilling" ? bestillingssvar : basis[sti];
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const seksjon = hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  const skjema = seksjon.querySelector("form");
  const send = skjema.querySelector("button[type=submit]");
  const alert = seksjon.querySelector("[role=alert]");
  Object.defineProperty(skjema.querySelector("input[type=file]"), "files",
    { configurable: true, value: [{ name: "bunt.zip",
        arrayBuffer: async () => new ArrayBuffer(16) }] });
  const runde = async () => {
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    assert.ok(await vent(() => !send.disabled, 40), "runden ble aldri ferdig");
  };
  await runde();
  assert.equal(alert.textContent, t("ui.rekruttering.usikkert_utfall"),
    "5xx ble meldt som en definitiv feil");
  // Serverens egen dom er derimot definitiv — og sier det.
  bestillingssvar = 409;
  await runde();
  assert.equal(alert.textContent, t("ui.rekruttering.bestill.feil"));
});

test("Bestilling: en bunt byttet under opplastingen binder aldri feil bunt (P1-2)", async () => {
  // Skjemaet står åpent mens en stor ZIP går opp. Byttet brukeren fil,
  // nullstilte `change` referansen — men den flygende handleren skrev
  // likevel SIN `inndata_ref` inn etterpå, mens skjermen viste den nye
  // filen: neste bestilling gikk på bunt A under navnet B.
  KALL = [];
  let slippOpplast;
  let opplastsvar = new Promise((r) => { slippOpplast = r; });
  let reservasjon = { reservasjon_jti: "j-1", inndata_ref: "inndata:u-1" };
  SVAR = (sti) => {
    if (sti === "/v1/rekruttering/prosesser") return prosess();
    if (sti === "/v1/rekruttering/stillingsprofiler") return profiler();
    if (sti === "/v1/inndata/reserver") return reservasjon;
    if (sti.startsWith("/v1/inndata/opplast/")) return opplastsvar;
    if (sti === "/v1/bestilling") return { beslutning: "tillat", oppdrag_id: 3 };
    return undefined;
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const seksjon = hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  const skjema = seksjon.querySelector("form");
  const send = skjema.querySelector("button[type=submit]");
  const filInp = skjema.querySelector("input[type=file]");
  const velgFil = (navn) => {
    Object.defineProperty(filInp, "files", { configurable: true,
      value: [{ name: navn, arrayBuffer: async () => new ArrayBuffer(16) }] });
    filInp.dispatchEvent(new window.Event("change", { bubbles: true }));
  };
  velgFil("bunt.zip");
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() =>
    KALL.some((k) => k.sti.startsWith("/v1/inndata/opplast/")), 20),
    "opplastingen startet aldri");
  // Kroppen er låst mens den er underveis (readOnly, ikke disabled).
  assert.equal(skjema.querySelector("#bestill-antall").readOnly, true,
    "antall kunne skrives om midt i en pågående kjede");
  assert.equal(send.disabled, true);
  // Midt i opplastingen bytter brukeren bunt.
  velgFil("bunt2.zip");
  reservasjon = { reservasjon_jti: "j-2", inndata_ref: "inndata:u-2" };
  slippOpplast({});
  assert.ok(await vent(() => !send.disabled, 40), "kjeden ble aldri ferdig");
  // Kjeden tilhørte bunt A og er forlatt: INGEN bestilling er sendt, og
  // A-referansen ble aldri skrevet inn under B-navnet.
  assert.equal(KALL.filter((k) => k.sti === "/v1/bestilling").length, 0,
    "en forlatt kjede bestilte likevel");
  assert.equal(seksjon.querySelector("[role=alert]").textContent,
    t("ui.rekruttering.bestill.avbrutt"));
  assert.doesNotMatch(seksjon.textContent, /inndata:u-1/);
  assert.equal(filInp.hasAttribute("required"), true,
    "flaten tror den har en bunt etter en forlatt opplasting");
  // Neste innsending går på den NYE bunten — egen reservasjon, egen ref.
  opplastsvar = {};
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() =>
    KALL.filter((k) => k.sti === "/v1/bestilling").length === 1, 40),
    "bestillingen kom aldri");
  const best = KALL.find((k) => k.sti === "/v1/bestilling");
  assert.equal(best.kropp.inndata_ref, "inndata:u-2",
    "bestillingen bar den forlatte buntens referanse");
  assert.match(seksjon.textContent, /bunt2\.zip|inndata:u-2|3/);
});

test("Bestilling: prosessvelgeren er frosset mens kjeden er i lufta (A-dommen)", async () => {
  // A-DOMMEN (#212): GENERATOREN, IKKE INSTANSENE. Låsen lå først i ÉN
  // knapp, så i `paagaaende`/`generasjon`/`laasOpp` — men om-tegningen
  // SELV sto igjen, og hver binding submit-handleren lukker over (alerten,
  // skjemaet, `visBunt`) ble en frakoblet node ved et prosessbytte midt i
  // kjeden. Runde tre fant den fjerde, femte og sjette bindingen; runde
  // fire ville funnet den syvende.
  //
  // Nå fryses utløseren i stedet: velgeren er `disabled` med `aria-busy`
  // så lenge kjeden er i lufta, seksjonen kan ikke rives, og kvitteringen
  // treffer den alerten brukeren faktisk ser.
  //
  // TO MUTASJONER, BEGGE MÅLT: fjern `laas.meld("velger", velger)` → den
  // synlige frysen faller («prosessvelgeren sto handlingsklar»); fjern
  // `paagaaende`-vakten i velgerens `change` → seksjonen rives igjen
  // («bestillingsseksjonen ble revet»). Begge må stå: den ene er det
  // brukeren SER, den andre er invarianten koden selv holder.
  KALL = [];
  let slippBestilling;
  // Bestillingen henger: bunten er ALT lastet opp, så et skjema tegnet i
  // dette vinduet har alt den trenger for å sende bestilling nummer to —
  // på nøyaktig den bestillingen som står ubesvart.
  const bestillingssvar = new Promise((r) => {
    slippBestilling = () => r({ beslutning: "tillat", oppdrag_id: 8 });
  });
  const to = prosess();
  to.prosesser.push({ ...to.prosesser[0], prosess_id: "p-2" });
  SVAR = (sti) => {
    if (sti === "/v1/rekruttering/prosesser") return to;
    if (sti === "/v1/rekruttering/stillingsprofiler") return profiler();
    if (sti === "/v1/inndata/reserver") {
      return { reservasjon_jti: "j-1", inndata_ref: "inndata:u-1" };
    }
    if (sti.startsWith("/v1/inndata/opplast/")) return {};
    if (sti === "/v1/bestilling") return bestillingssvar;
    return undefined;
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const bestill = () =>
    hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  const filInp = bestill().querySelector("input[type=file]");
  Object.defineProperty(filInp, "files", { configurable: true,
    value: [{ name: "bunt.zip",
              arrayBuffer: async () => new ArrayBuffer(16) }] });
  filInp.dispatchEvent(new window.Event("change", { bubbles: true }));
  bestill().querySelector("form").dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() =>
    KALL.some((k) => k.sti === "/v1/bestilling"), 20),
    "bestillingen ble aldri sendt");
  // Utløseren er frosset — og den SIER det, den later ikke som ingenting.
  const skjema = bestill().querySelector("form");
  const send = skjema.querySelector("button[type=submit]");
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  assert.equal(velger.disabled, true,
    "prosessvelgeren sto handlingsklar mens kjeden var i lufta");
  assert.equal(velger.parentElement.getAttribute("aria-busy"), "true",
    "velgeren er låst uten å si hvorfor");
  assert.equal(send.disabled, true);
  assert.equal(skjema.getAttribute("aria-busy"), "true");
  assert.equal(skjema.querySelector("#bestill-antall").readOnly, true);
  // Et bytteforsøk river ikke seksjonen: samme skjema, samme knapp.
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.equal(bestill().querySelector("form"), skjema,
    "bestillingsseksjonen ble revet mens kjeden var i lufta");
  assert.equal(KALL.filter((k) => k.sti === "/v1/bestilling").length, 1,
    "om-tegningen startet en parallell kjede");
  slippBestilling();
  // Kjeden fullfører — og kvitteringen treffer alerten brukeren SER,
  // uten en eneste peker mot «det som er synlig nå».
  assert.ok(await vent(() =>
    bestill().querySelector("[role=alert]").textContent.includes("8"), 40),
    "kvitteringen nådde aldri den synlige alerten");
  assert.doesNotMatch(bestill().textContent, /bunt\.zip/,
    "den forbrukte bunten står fortsatt navngitt i det synlige skjemaet");
  assert.equal(bestill().querySelector("input[type=file]")
    .hasAttribute("required"), true,
    "det synlige skjemaet ble aldri nullstilt etter kvitteringen");
  // ... og frysen løftes: velgeren er brukbar igjen, uten `aria-busy`.
  assert.ok(await vent(() => !send.disabled, 20),
    "skjemaet ble stående låst etter at svaret kom");
  assert.equal(velger.disabled, false, "velgeren ble stående frosset");
  assert.equal(velger.parentElement.hasAttribute("aria-busy"), false);
  assert.equal(skjema.hasAttribute("aria-busy"), false);
  assert.equal(skjema.querySelector("#bestill-antall").readOnly, false);
  // ... og NÅ går prosessbyttet, som før — men valget flyttes av TEGNINGEN,
  // ikke av klikket (Cursor P1): mellom klikk og svar står velgeren på
  // prosessen som faktisk vises, så den kan ikke navngi B mens tabellen er
  // A. Byttet måles derfor der det fullfører.
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(await vent(() =>
    hoved.querySelector("#rekrut-prosessvelger").value === "p-2", 20),
    "prosessbyttet gikk ikke gjennom etter at frysen var løftet");
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Bestilling: en mutasjon startet UNDER prosesshentingen låses aldri ute "
  + "av byttets om-tegning (Codex P1)", async () => {
  // SPEILET AV A-DOMMEN (#212). Testen over måler den ene retningen: et
  // bytteforsøk MENS kjeden henger. Frysen av `tegn`-utløserne dekker
  // den, og dekket i sin tid begge — fordi klikket og om-tegningen var
  // samme hendelse.
  //
  // Etter #183 er byttet en HENTING, og de to falt fra hverandre:
  // `paagaaende`-vakten i velgerens `change` måler ved KLIKKET, mens
  // om-tegningen skjer et nettverk senere. I det vinduet er Send og
  // Lagre ufrosne — en lesning tar ikke mutasjonslåsen — så brukeren
  // rekker å sende bestillingen. Landet svaret uten vakt, tegnet det
  // flaten om midt i kjeden: de NYE kontrollene ble meldt frosne
  // (`laas.meld` ser `paagaaende`), mens kjedens `finally` løftet frysen
  // på den `laas`-en den selv tok — den gamle, nå frakoblede. Send sto
  // låst til omlasting, og kvitteringen ble skrevet i en alert utenfor
  // dokumentet. Nøyaktig den frakoblede noden A-dommen finnes for.
  //
  // MUTASJONEN SOM DREPER DENNE: fjern `if (okt.bestilling.paagaaende)`
  // fra suksessarmen i velgerens `change`. Da rives skjemaet
  // («bestillingsseksjonen ble revet»), og knappen kommer aldri opp igjen
  // («Send sto låst etter at bestillingen svarte»).
  const full = prosess();
  full.prosesser.push({ ...full.prosesser[0], prosess_id: "p-2" });
  const kart = svar183(full);
  KALL = [];
  SVAR = { ...kart, "/v1/rekruttering/stillingsprofiler": profiler() };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const bestill = () =>
    hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  assert.ok(velger, "ingen prosessvelger med flere prosesser i svaret");

  // Byttet henger på nettet — og bestillingen henger etter det, så begge
  // vinduene er åpne samtidig og rekkefølgen kan styres.
  let slippBytte;
  const byttesvar = new Promise((r) => {
    slippBytte = () => r(kart["/v1/rekruttering/prosesser?prosess_id=p-2"]);
  });
  let slippBestilling;
  const bestillingssvar = new Promise((r) => {
    slippBestilling = () => r({ beslutning: "tillat", oppdrag_id: 8 });
  });
  KALL = [];
  SVAR = (sti) => {
    if (sti === "/v1/rekruttering/prosesser") return byttesvar;
    if (sti === "/v1/rekruttering/stillingsprofiler") return profiler();
    if (sti === "/v1/inndata/reserver") {
      return { reservasjon_jti: "j-1", inndata_ref: "inndata:u-1" };
    }
    if (sti.startsWith("/v1/inndata/opplast/")) return {};
    if (sti === "/v1/bestilling") return bestillingssvar;
    return undefined;
  };
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(await vent(() =>
    KALL.some((k) => k.sti === "/v1/rekruttering/prosesser"), 20),
    "byttet hentet aldri — testen måler ikke det den tror");

  // ... og MENS den henger tar bestillingen låsen. Den kan det: en
  // lesning fryser ikke mutasjonene, så knappen står handlingsklar.
  const skjema = bestill().querySelector("form");
  const send = skjema.querySelector("button[type=submit]");
  assert.equal(send.disabled, false,
    "Send var alt frosset — vinduet testen måler finnes ikke");
  const filInp = skjema.querySelector("input[type=file]");
  Object.defineProperty(filInp, "files", { configurable: true,
    value: [{ name: "bunt.zip",
              arrayBuffer: async () => new ArrayBuffer(16) }] });
  filInp.dispatchEvent(new window.Event("change", { bubbles: true }));
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() =>
    KALL.some((k) => k.sti === "/v1/bestilling"), 40),
    "bestillingen ble aldri sendt");
  assert.equal(send.disabled, true, "kjeden tok aldri låsen");

  // NÅ lander byttet — midt i kjeden. Svaret forkastes, og velgeren sier
  // fra at valget står tilbake på prosessen flaten faktisk viser.
  slippBytte();
  // Vinduet ventes UT, ikke på vakten selv: da er det STRUKTUREN som
  // felles først om vakten faller — den frakoblede noden er funnet,
  // meldingen bare hvordan flaten sier det.
  await vent(() => false, 40);
  assert.equal(bestill().querySelector("form"), skjema,
    "bestillingsseksjonen ble revet mens kjeden var i lufta");
  assert.equal(send.isConnected, true,
    "Send-knappen kjeden låste står ikke lenger i dokumentet");
  assert.equal(velger.value, "p-1",
    "velgeren navngir en prosess flaten ikke viser");
  // Låsen er mutasjonens, ikke hentingens: byttet låser den ikke opp.
  assert.equal(velger.disabled, true,
    "byttet løftet en frys det ikke hadde tatt");
  assert.notEqual(hoved.querySelector(".rekrut-velgerfeil").textContent, "",
    "byttet forkastet svaret uten å si fra");

  // Kjeden fullfører — på de SAMME kontrollene som tok låsen.
  slippBestilling();
  assert.ok(await vent(() => !send.disabled, 60),
    "Send sto låst etter at bestillingen svarte");
  assert.equal(bestill().querySelector("form"), skjema,
    "kvitteringen tegnet et nytt skjema — kjeden mistet sine kontroller");
  assert.match(bestill().querySelector("[role=alert]").textContent, /8/,
    "kvitteringen nådde aldri den synlige alerten");
  assert.equal(velger.disabled, false,
    "velgeren ble stående frosset etter at kjeden slapp låsen");

  // ... og byttet går, som før, når låsen er løftet.
  KALL = [];
  SVAR = { ...kart, "/v1/rekruttering/stillingsprofiler": profiler() };
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(await vent(() =>
    hoved.querySelector("#rekrut-prosessvelger").value === "p-2", 40),
    "byttet gikk ikke gjennom etter at kjeden slapp låsen");
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Bestilling: en FEILET henting låser ikke opp velgeren mutasjonen "
  + "holder (Codex P1, feilarmen)", async () => {
  // SAMME ROT, ANDRE ARM. Feilveien låser opp og sier fra — den formen
  // er riktig når hentingen selv holder låsen. Tar en mutasjon
  // `paagaaende` mens hentingen står på, er velgeren derimot frosset av
  // `laas`, og en `laas`-frys skal løftes av den `finally`-en som tok
  // den. Uten vakten sto velgeren åpen midt i en bestilling: en kontroll
  // som ser handlingsklar ut, men der et klikk bare ruller seg selv
  // tilbake på `paagaaende`-vakten.
  //
  // MUTASJONEN SOM DREPER DENNE: fjern `if (okt.bestilling.paagaaende)
  // return;` fra feilarmen — da står velgeren `disabled === false` mens
  // kjeden fortsatt henger.
  const full = prosess();
  full.prosesser.push({ ...full.prosesser[0], prosess_id: "p-2" });
  const kart = svar183(full);
  KALL = [];
  SVAR = { ...kart, "/v1/rekruttering/stillingsprofiler": profiler() };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const bestill = () =>
    hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  let slippBytte;
  const byttesvar = new Promise((r) => { slippBytte = () => r(500); });
  let slippBestilling;
  const bestillingssvar = new Promise((r) => {
    slippBestilling = () => r({ beslutning: "tillat", oppdrag_id: 9 });
  });
  KALL = [];
  SVAR = (sti) => {
    if (sti === "/v1/rekruttering/prosesser") return byttesvar;
    if (sti === "/v1/rekruttering/stillingsprofiler") return profiler();
    if (sti === "/v1/inndata/reserver") {
      return { reservasjon_jti: "j-9", inndata_ref: "inndata:u-9" };
    }
    if (sti.startsWith("/v1/inndata/opplast/")) return {};
    if (sti === "/v1/bestilling") return bestillingssvar;
    return undefined;
  };
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(await vent(() =>
    KALL.some((k) => k.sti === "/v1/rekruttering/prosesser"), 20),
    "byttet hentet aldri — testen måler ikke det den tror");
  const skjema = bestill().querySelector("form");
  const send = skjema.querySelector("button[type=submit]");
  const filInp = skjema.querySelector("input[type=file]");
  Object.defineProperty(filInp, "files", { configurable: true,
    value: [{ name: "bunt.zip",
              arrayBuffer: async () => new ArrayBuffer(16) }] });
  filInp.dispatchEvent(new window.Event("change", { bubbles: true }));
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() =>
    KALL.some((k) => k.sti === "/v1/bestilling"), 40),
    "bestillingen ble aldri sendt");
  assert.equal(send.disabled, true, "kjeden tok aldri låsen");

  // 500 på byttet, midt i kjeden: meldingen kommer, frysen står.
  slippBytte();
  assert.ok(await vent(() =>
    hoved.querySelector(".rekrut-velgerfeil").textContent !== "", 40),
    "en feilet henting sa aldri fra");
  assert.equal(velger.disabled, true,
    "den feilede hentingen låste opp en velger mutasjonen holder");
  assert.equal(send.disabled, true, "kjeden mistet låsen sin");

  // ... og kjeden løfter frysen selv, på sine egne kontroller.
  slippBestilling();
  assert.ok(await vent(() => !send.disabled, 60),
    "Send sto låst etter at bestillingen svarte");
  assert.equal(velger.disabled, false,
    "velgeren ble stående frosset etter at kjeden slapp låsen");
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Bestilling: STOPP er ingen leveranse — bunten står igjen (P1-1)", async () => {
  // Serveren svarer `200` også på STOPP og unntak, uten oppdrag, og lar
  // bunten stå `lastet` (`test_stopp_binder_ikke_bunten`). Sa flaten
  // «Bestillingen er levert» og nullstilte kjeden, løy den om utfallet OG
  // kastet en bunt serveren fortsatt holder fri.
  KALL = [];
  const basis = { "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/inndata/reserver": { reservasjon_jti: "j-1",
                              inndata_ref: "inndata:u-1" },
    "/v1/inndata/opplast/j-1": {} };
  let bestillingssvar = { beslutning: "stopp", oppdrag_id: null,
    begrunnelse: ["bestilling_policy_stopp"] };
  SVAR = (sti) => sti === "/v1/bestilling" ? bestillingssvar : basis[sti];
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const seksjon = hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  const skjema = seksjon.querySelector("form");
  const send = skjema.querySelector("button[type=submit]");
  const alert = seksjon.querySelector("[role=alert]");
  const filInp = skjema.querySelector("input[type=file]");
  Object.defineProperty(filInp, "files", { configurable: true,
    value: [{ name: "bunt.zip",
              arrayBuffer: async () => new ArrayBuffer(16) }] });
  filInp.dispatchEvent(new window.Event("change", { bubbles: true }));
  const runde = async () => {
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    assert.ok(await vent(() => !send.disabled, 40), "runden ble aldri ferdig");
  };
  const bestillinger = () => KALL.filter((k) => k.sti === "/v1/bestilling");
  await runde();
  assert.equal(bestillinger().length, 1);
  assert.ok(!alert.textContent.includes(
    t("ui.rekruttering.bestill.sendt_uten_oppdrag")
      .replace("{beslutning}", "stopp")),
    "en STOPP ble meldt som en levert bestilling");
  assert.ok(alert.textContent.startsWith(t("ui.rekruttering.bestill.stoppet")),
    `STOPP-teksten mangler: ${alert.textContent}`);
  assert.ok(alert.textContent.includes(
    t("kode.bestilling_policy_stopp", "bestilling_policy_stopp")),
    "STOPP-årsaken står ikke i alerten (§7)");
  // Bunten er ikke forbrukt: den står navngitt, filen kreves ikke, og
  // neste forsøk går på SAMME reservasjon — ingen ny opplasting.
  assert.match(seksjon.textContent, /bunt\.zip/,
    "den frie bunten ble kastet ut av økten");
  assert.equal(filInp.hasAttribute("required"), false);
  bestillingssvar = { beslutning: "tillat", oppdrag_id: 77 };
  await runde();
  assert.equal(KALL.filter((k) => k.sti === "/v1/inndata/reserver").length, 1,
    "bunten ble reservert på nytt etter en STOPP");
  const [b1, b2] = bestillinger();
  assert.equal(b2.kropp.inndata_ref, "inndata:u-1");
  // Serveren har DØMT den forrige kroppen: samme nøkkel ville bare fått
  // den samme STOPP-en replayet.
  assert.notEqual(b1.hoder["Idempotency-Key"], b2.hoder["Idempotency-Key"],
    "et nytt forsøk bar nøkkelen til den alt dømte intensjonen");
  await vent(() => alert.textContent.includes("77"), 20);
  // ... og unntakskøen har sin egen tekst, ikke stopp-teksten.
  bestillingssvar = { beslutning: "brudd", oppdrag_id: null,
    begrunnelse: [], unntak_id: 5 };
  Object.defineProperty(filInp, "files", { configurable: true,
    value: [{ name: "bunt2.zip",
              arrayBuffer: async () => new ArrayBuffer(16) }] });
  filInp.dispatchEvent(new window.Event("change", { bubbles: true }));
  await runde();
  assert.equal(alert.textContent, t("ui.rekruttering.bestill.unntak"));
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Bestilling: 4xx på reservasjon/opplast slipper den døde nøkkelen (P1-3)", async () => {
  KALL = [];
  let reserversvar = 409;
  let opplastsvar = {};
  SVAR = (sti) => {
    if (sti === "/v1/rekruttering/prosesser") return prosess();
    if (sti === "/v1/rekruttering/stillingsprofiler") return profiler();
    if (sti === "/v1/inndata/reserver") return reserversvar;
    if (sti === "/v1/inndata/opplast/j-1") return opplastsvar;
    if (sti === "/v1/bestilling") return { beslutning: "tillat", oppdrag_id: 5 };
    return undefined;
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const seksjon = hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  const skjema = seksjon.querySelector("form");
  const send = skjema.querySelector("button[type=submit]");
  Object.defineProperty(skjema.querySelector("input[type=file]"), "files",
    { configurable: true, value: [{ name: "bunt.zip",
        arrayBuffer: async () => new ArrayBuffer(16) }] });
  // Runden er ferdig når låsen er løftet igjen — teksten testes andre
  // steder, og å vente på den ville bundet denne testen til ordlyden.
  const runde = async () => {
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    assert.ok(await vent(() => !send.disabled, 40), "runden ble aldri ferdig");
  };
  const reservasjoner = () =>
    KALL.filter((k) => k.sti === "/v1/inndata/reserver");
  // 1) Serveren DØMMER reservasjonen (409): nøkkelen er død, og uten fil-
  //    bytte er det ingenting brukeren kan gjøre for å få en ny.
  await runde();
  assert.equal(reservasjoner().length, 1);
  // 2) Reservasjonen går gjennom, men opplastingen svarer 5xx: utfallet
  //    er UKJENT, og retry skal være SAMME operasjon.
  reserversvar = { reservasjon_jti: "j-1", inndata_ref: "inndata:u-1" };
  opplastsvar = 500;
  await runde();
  assert.equal(reservasjoner().length, 2);
  // 3) Opplastingen går gjennom, kjeden fullfører.
  opplastsvar = {};
  await runde();
  assert.equal(reservasjoner().length, 3);
  const [r1, r2, r3] = reservasjoner().map((k) => k.hoder["Idempotency-Key"]);
  assert.notEqual(r1, r2, "4xx-dommen etterlot klienten på en død nøkkel");
  assert.equal(r2, r3, "et usikkert utfall roterte reservasjonsnøkkelen");
  await vent(() => seksjon.querySelector("[role=alert]")
    .textContent.includes("5"), 20);
});

test("Bestilling: den første profilen låser opp bestillingsskjemaet (P1-1)", async () => {
  KALL = [];
  let profilsvar = { profiler: [] };
  SVAR = (sti, opts = {}) => {
    if (sti === "/v1/rekruttering/prosesser") return prosess();
    if (sti === "/v1/rekruttering/stillingsprofiler") {
      if ((opts.method || "GET") === "POST") {
        profilsvar = profiler();
        return { profil_id: "prof-1", versjon: 2 };
      }
      return profilsvar;
    }
    return undefined;
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  // Seksjonen re-tegnes, så noden må hentes på nytt hver gang.
  const bestill = () =>
    hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  assert.ok(bestill(), "bestillingsseksjonen mangler");
  assert.equal(bestill().querySelector("form"), null,
    "bestillingsskjemaet sto der uten en eneste profil");
  const profilDel =
    hoved.querySelector("section[aria-labelledby=profil-tittel]");
  const ny = [...profilDel.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.profiler.ny"));
  assert.ok(ny, "Ny profil-knappen mangler");
  ny.click();
  const skjema = profilDel.querySelector("form");
  skjema.querySelector("#profil-navn").value = "Driftskonsulent";
  skjema.querySelector('input[type=text][id^="profil-krav"]').value = "Drift";
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() => bestill().querySelector("form"), 40),
    "bestillingsskjemaet våknet aldri etter den første profilen");
  assert.ok(bestill().querySelector("select#bestill-profil option"),
    "profilvelgeren står tom etter at profilen ble lagret");
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Bestilling: en ny profilversjon når velgeren uten å rive skjemaet (P2-2)",
  async () => {
  // P1-1-vakten («ikke tegn om et skjema som finnes») gjorde skjemaet
  // urørlig, og da ble den bevarte tilstanden feil på et annet punkt:
  // «lagret (versjon 3)» sto ved siden av en velger som fortsatt bare
  // kjente `prof-1@2`, og bestillingen gikk mot en erstattet versjon.
  KALL = [];
  let profilsvar = profiler();
  let bestillingssvar = 500;
  SVAR = (sti, opts = {}) => {
    if (sti === "/v1/rekruttering/prosesser") return prosess();
    if (sti === "/v1/rekruttering/stillingsprofiler") {
      if ((opts.method || "GET") === "POST") {
        const ny = profiler();
        ny.profiler[0].versjon = 3;
        profilsvar = ny;
        return { profil_id: "prof-1", versjon: 3 };
      }
      return profilsvar;
    }
    if (sti === "/v1/inndata/reserver") {
      return { reservasjon_jti: "j-1", inndata_ref: "inndata:u-1" };
    }
    if (sti.startsWith("/v1/inndata/opplast/")) return {};
    if (sti === "/v1/bestilling") return bestillingssvar;
    return undefined;
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const bestill = () =>
    hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  const skjema = bestill().querySelector("form");
  const send = skjema.querySelector("button[type=submit]");
  const antall = skjema.querySelector("#bestill-antall");
  antall.value = "4";
  antall.dispatchEvent(new window.Event("input", { bubbles: true }));
  const filInp = skjema.querySelector("input[type=file]");
  Object.defineProperty(filInp, "files", { configurable: true,
    value: [{ name: "bunt.zip",
              arrayBuffer: async () => new ArrayBuffer(16) }] });
  filInp.dispatchEvent(new window.Event("change", { bubbles: true }));
  // Bunten lastes opp; bestillingen svarer 5xx, så referansen står i økten.
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() => !send.disabled, 40), "runden ble aldri ferdig");
  assert.equal(bestill().querySelector("#bestill-profil").value, "prof-1@2");
  // Brukeren lagrer en ny versjon av den samme profilen.
  const profilDel =
    hoved.querySelector("section[aria-labelledby=profil-tittel]");
  [...profilDel.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.profiler.rediger")).click();
  const profilSkjema = profilDel.querySelector("form");
  profilSkjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() => [...bestill()
    .querySelectorAll("#bestill-profil option")]
    .some((o) => o.value === "prof-1@3"), 40),
    "velgeren kjenner bare den erstattede versjonen");
  // Skjemaet ble IKKE revet: samme noder, samme antall, samme bunt.
  assert.equal(bestill().querySelector("form"), skjema,
    "bestillingsskjemaet ble revet ned av en profillagring");
  assert.equal(bestill().querySelector("#bestill-antall").value, "4",
    "brukerens antall forsvant med profillagringen");
  assert.match(bestill().textContent, /bunt\.zip/,
    "den opplastede bunten forsvant med profillagringen");
  // ... og bestillingen går mot den NYE versjonen, på den samme bunten.
  bestillingssvar = { beslutning: "tillat", oppdrag_id: 21 };
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() =>
    KALL.filter((k) => k.sti === "/v1/bestilling").length === 2, 40),
    "bestillingen kom aldri");
  const [b1, b2] = KALL.filter((k) => k.sti === "/v1/bestilling");
  assert.equal(b1.kropp.stillingsprofil_ref, "prof-1@2");
  assert.equal(b2.kropp.stillingsprofil_ref, "prof-1@3",
    "bestillingen gikk mot en erstattet profilversjon");
  assert.equal(b2.kropp.inndata_ref, "inndata:u-1");
  assert.equal(KALL.filter((k) => k.sti === "/v1/inndata/reserver").length, 1,
    "bunten ble reservert på nytt etter profillagringen");
  // En annen kropp er en annen intensjon: nøkkelen fulgte med.
  assert.notEqual(b1.hoder["Idempotency-Key"], b2.hoder["Idempotency-Key"],
    "den nye profilversjonen bar den forrige intensjonens nøkkel");
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Bestilling: ingen profilversjon forsvinner i bestillingsvinduet (P2-1)",
  async () => {
  // P2-2 lot en ny profilversjon nå velgeren uten å rive skjemaet — men
  // hoppet over HELE oppdateringen mens en bestilling var i lufta, og
  // hentet den aldri inn igjen. Lagret brukeren versjon 3 i det vinduet,
  // sto velgeren på erstattet `@2` etterpå, og neste bestilling gikk mot
  // en versjon som ikke lenger var den nyeste.
  //
  // A-dommen fjerner vinduet i stedet for å lappe det: «Lagre» tar den
  // SAMME låsen som kjeden, så de to mutasjonene aldri er i lufta
  // samtidig — og da kan `oppdaterProfilvalg` kjøre ubetinget.
  //
  // MUTASJONEN SOM DREPER DENNE: sett `!okt.bestilling.paagaaende &&`
  // tilbake foran `oppdaterProfilvalg` i `paaProfilendring`.
  KALL = [];
  let slippBestilling;
  const bestillingssvar = new Promise((r) => {
    slippBestilling = () => r({ beslutning: "stopp", begrunnelse: [] });
  });
  let profilsvar = profiler();
  SVAR = (sti, opts = {}) => {
    if (sti === "/v1/rekruttering/prosesser") return prosess();
    if (sti === "/v1/rekruttering/stillingsprofiler") {
      if ((opts.method || "GET") === "POST") {
        const ny = profiler();
        ny.profiler[0].versjon = 3;
        profilsvar = ny;
        return { profil_id: "prof-1", versjon: 3 };
      }
      return profilsvar;
    }
    if (sti === "/v1/inndata/reserver") {
      return { reservasjon_jti: "j-1", inndata_ref: "inndata:u-1" };
    }
    if (sti.startsWith("/v1/inndata/opplast/")) return {};
    if (sti === "/v1/bestilling") return bestillingssvar;
    return undefined;
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const bestill = () =>
    hoved.querySelector("section[aria-labelledby=bestill-tittel]");
  const profilDel =
    hoved.querySelector("section[aria-labelledby=profil-tittel]");
  const skjema = bestill().querySelector("form");
  const send = skjema.querySelector("button[type=submit]");
  const filInp = skjema.querySelector("input[type=file]");
  Object.defineProperty(filInp, "files", { configurable: true,
    value: [{ name: "bunt.zip",
              arrayBuffer: async () => new ArrayBuffer(16) }] });
  filInp.dispatchEvent(new window.Event("change", { bubbles: true }));
  // Brukeren åpner profileditoren FØRST, så knappen finnes når kjeden
  // starter — den er kontrollen låsen skal ta.
  [...profilDel.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.profiler.rediger")).click();
  const profilSkjema = profilDel.querySelector("form");
  const lagre = profilSkjema.querySelector("button[type=submit]");
  assert.equal(lagre.disabled, false, "testen antar en åpen editor");
  // Bestillingen henger.
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() => KALL.some((k) => k.sti === "/v1/bestilling"), 20),
    "bestillingen ble aldri sendt");
  // «Lagre» er frosset — og sier det. Et forsøk poster ingenting.
  assert.equal(lagre.disabled, true,
    "profileditoren sto handlingsklar mens en bestilling var i lufta");
  assert.equal(profilSkjema.getAttribute("aria-busy"), "true");
  profilSkjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  await vent(() => false, 5);
  assert.equal(KALL.filter((k) => k.metode === "POST"
    && k.sti === "/v1/rekruttering/stillingsprofiler").length, 0,
    "en profilversjon ble skrevet midt i en bestilling");
  // STOPP: bunten står, kjeden slipper låsen — og NÅ lagrer brukeren.
  slippBestilling();
  assert.ok(await vent(() => !send.disabled, 40), "kjeden ble aldri ferdig");
  assert.equal(lagre.disabled, false, "editoren ble stående frosset");
  assert.equal(profilSkjema.hasAttribute("aria-busy"), false);
  profilSkjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  // Velgeren kjenner den nye versjonen FØR neste innsending.
  assert.ok(await vent(() => [...bestill()
    .querySelectorAll("#bestill-profil option")]
    .some((o) => o.value === "prof-1@3"), 40),
    "velgeren står igjen på en erstattet profilversjon");
  assert.equal(bestill().querySelector("#bestill-profil").value, "prof-1@3");
  // ... og skjemaet ble ikke revet: bunten fra STOPP-runden står igjen.
  assert.equal(bestill().querySelector("form"), skjema);
  assert.match(bestill().textContent, /bunt\.zip/);
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Profiler: en editor som åpnes midt i en bestilling fødes frosset",
  async () => {
  // Frysen tar kontrollene som FINNES. Profilskjemaet åpnes på et klikk,
  // så «Lagre» kan fødes etter at låsen ble tatt — og en knapp som slipper
  // unna frysen er hele P2-1-vinduet på nytt, bare gjennom en annen dør.
  //
  // MUTASJONEN SOM DREPER DENNE: fjern
  // `if (okt.bestilling.paagaaende) frysEn(kontroll, true)` fra
  // `laas.meld`.
  KALL = [];
  let slippBestilling;
  const bestillingssvar = new Promise((r) => {
    slippBestilling = () => r({ beslutning: "stopp", begrunnelse: [] });
  });
  SVAR = (sti) => {
    if (sti === "/v1/rekruttering/prosesser") return prosess();
    if (sti === "/v1/rekruttering/stillingsprofiler") return profiler();
    if (sti === "/v1/inndata/reserver") {
      return { reservasjon_jti: "j-1", inndata_ref: "inndata:u-1" };
    }
    if (sti.startsWith("/v1/inndata/opplast/")) return {};
    if (sti === "/v1/bestilling") return bestillingssvar;
    return undefined;
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const profilDel =
    hoved.querySelector("section[aria-labelledby=profil-tittel]");
  const skjema = hoved
    .querySelector("section[aria-labelledby=bestill-tittel] form");
  const send = skjema.querySelector("button[type=submit]");
  const filInp = skjema.querySelector("input[type=file]");
  Object.defineProperty(filInp, "files", { configurable: true,
    value: [{ name: "bunt.zip",
              arrayBuffer: async () => new ArrayBuffer(16) }] });
  filInp.dispatchEvent(new window.Event("change", { bubbles: true }));
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() => KALL.some((k) => k.sti === "/v1/bestilling"), 20),
    "bestillingen ble aldri sendt");
  // FØRST NÅ åpnes editoren — knappen finnes ikke før dette klikket.
  [...profilDel.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.profiler.ny")).click();
  const profilSkjema = profilDel.querySelector("form");
  const lagre = profilSkjema.querySelector("button[type=submit]");
  assert.equal(lagre.disabled, true,
    "en editor åpnet midt i en bestilling ga en handlingsklar «Lagre»");
  assert.equal(profilSkjema.getAttribute("aria-busy"), "true");
  // ... og den åpnes når kjeden slipper, som enhver annen utløser.
  slippBestilling();
  assert.ok(await vent(() => !send.disabled, 40), "kjeden ble aldri ferdig");
  assert.equal(lagre.disabled, false, "editoren ble stående frosset");
  assert.equal(profilSkjema.hasAttribute("aria-busy"), false);
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Profiler: en profillagring i lufta fryser bestillingskroppen (P2-2)",
  async () => {
  // SPEILET AV «profilen er låst mens kroppen er underveis» (Cursor P2-2).
  // Testene dekket bare den ene retningen — bestilling ⇒ profil/prosess/
  // «Lagre» frosset. Motsatt vei sto umålt, og der var låsen halv: en
  // profillagring tok `laas.frys` alene, som eier `tegn`-utløserne, mens
  // bestillingens KROPP — profil, antall, frist — eies av seksjonens egen
  // `frys`. Skjemaet fikk `aria-busy` gjennom `send` og tok input likevel,
  // og profilvelgerens `change`-vakt rullet tilbake til `frossetProfil`,
  // som ingen hadde satt: valget ble TØMT i stedet for bevart.
  //
  // MUTASJONEN SOM DREPER DENNE: sett `laas.frys` tilbake alene i
  // profil-submit (begge stedene, `finally` inkludert).
  KALL = [];
  let slippProfil;
  const profilPost = new Promise((r) => {
    slippProfil = () => r({ profil_id: "prof-1", versjon: 3 });
  });
  SVAR = (sti, opts = {}) => {
    if (sti === "/v1/rekruttering/prosesser") return prosess();
    if (sti === "/v1/rekruttering/stillingsprofiler") {
      return (opts.method || "GET") === "POST" ? profilPost : profiler();
    }
    return undefined;
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const profilDel =
    hoved.querySelector("section[aria-labelledby=profil-tittel]");
  const skjema = hoved
    .querySelector("section[aria-labelledby=bestill-tittel] form");
  const profilVelger = skjema.querySelector("#bestill-profil");
  const antall = skjema.querySelector("#bestill-antall");
  const frist = skjema.querySelector("#bestill-frist");
  assert.equal(profilVelger.value, "prof-1@2", "testen antar et valgt utgangspunkt");
  // Brukeren lagrer en ny versjon; POST-en henger.
  [...profilDel.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.profiler.rediger")).click();
  const profilSkjema = profilDel.querySelector("form");
  profilSkjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() => KALL.some((k) => k.metode === "POST"
    && k.sti === "/v1/rekruttering/stillingsprofiler"), 20),
    "profilversjonen ble aldri sendt");
  // Hele bestillingen er frosset — ikke bare utløserne den deler med `laas`.
  assert.equal(profilVelger.disabled, true,
    "profilvelgeren tok input mens en profilversjon ble skrevet");
  assert.equal(antall.readOnly, true, "antallet sto åpent under profillagringen");
  assert.equal(frist.readOnly, true, "slettefristen sto åpen under profillagringen");
  assert.equal(skjema.getAttribute("aria-busy"), "true");
  assert.equal(skjema.querySelector("button[type=submit]").disabled, true);
  // ... og et bytte som slipper gjennom `disabled` BEVARER valget.
  // Uten kroppsfrysen er `frossetProfil` `null` her, og velgeren tømmes.
  profilVelger.value = "";
  profilVelger.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.equal(profilVelger.value, "prof-1@2",
    "valget ble tømt i stedet for rullet tilbake til profilen kroppen bæres av");
  // Låsen løftes når versjonen er skrevet — på de samme kontrollene.
  slippProfil();
  assert.ok(await vent(() => !profilVelger.disabled, 40),
    "kroppen ble stående frosset etter profillagringen");
  assert.equal(antall.readOnly, false);
  assert.equal(frist.readOnly, false);
  assert.equal(skjema.hasAttribute("aria-busy"), false);
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Profiler: listen viser navn, versjon og krav — og axe rent", async () => {
  const hoved = await tegnetMedProfiler();
  const seksjon = hoved.querySelector("section[aria-labelledby=profil-tittel]");
  assert.ok(seksjon, "profilseksjonen mangler");
  const tekst = seksjon.textContent;
  assert.match(tekst, /Driftskonsulent/);
  assert.match(tekst, /Drift 3/);
  assert.match(tekst, /Norsk 1/);
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Profiler: editoren har label, caption og tallfeltets grenser", async () => {
  const hoved = await tegnetMedProfiler();
  const seksjon = hoved.querySelector("section[aria-labelledby=profil-tittel]");
  const knapper = [...seksjon.querySelectorAll("button")];
  const ny = knapper.find((b) => b.textContent === t("ui.rekruttering.profiler.ny"));
  assert.ok(ny, "Ny profil-knappen mangler");
  ny.click();
  const skjema = seksjon.querySelector("form");
  assert.ok(skjema, "skjemaet åpnet ikke");
  const navnLabel = skjema.querySelector("label[for=profil-navn]");
  assert.ok(navnLabel && navnLabel.textContent.length, "navnelabelen mangler");
  assert.ok(skjema.querySelector("table caption"), "kravtabellen mangler caption");
  const vekt = skjema.querySelector("input[type=number]");
  assert.equal(vekt.getAttribute("min"), "0");
  assert.equal(vekt.getAttribute("max"), "10");
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Profiler: lagring poster hele kravsettet og melder i alert", async () => {
  const hoved = await tegnetMedProfiler();
  SVAR = { "/v1/rekruttering/prosesser": prosess(),
           "/v1/rekruttering/stillingsprofiler":
             (KALL.length, profiler()) };
  const seksjon = hoved.querySelector("section[aria-labelledby=profil-tittel]");
  const rediger = [...seksjon.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.profiler.rediger"));
  rediger.click();
  const skjema = seksjon.querySelector("form");
  // Rediger-skjemaet er forhåndsutfylt fra profilen.
  const felt = [...skjema.querySelectorAll("tbody input[type=text]")];
  assert.equal(felt[0].value, "Drift");
  felt[0].value = "Drift og beredskap";
  KALL = [];
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() => KALL.some((k) => k.metode === "POST")),
    "POST-en gikk aldri");
  const post = KALL.find((k) => k.metode === "POST");
  assert.equal(post.sti, "/v1/rekruttering/stillingsprofiler");
  assert.equal(post.kropp.profil_id, "prof-1");
  assert.deepEqual(post.kropp.krav[0],
    { kravnavn: "Drift og beredskap", vekt: 3 });
  assert.equal(post.kropp.krav.length, 2);
  // SP-2 (Cursor P1-1/P2-7): nøkkelen er med, og kvitteringen står i
  // alerten ETTER at listen er oppdatert.
  assert.ok(post.hoder["Idempotency-Key"],
    "Idempotency-Key mangler i lagringen");
  const seksjon2 = hoved.querySelector(
    "section[aria-labelledby=profil-tittel]");
  assert.ok(await vent(() => {
    const alert = seksjon2.querySelector("[role=alert]");
    return alert && /lagret/.test(alert.textContent);
  }), "kvitteringen kom aldri i alerten");
});

test("Profiler: kvitteringen navngir profilen som ble lagret (Cursor P2-1)",
  async () => {
    // Samme vindu som bestillingens buntbinding (`:1350`), på profilens
    // suksessarm: `laas` fryser utløserne og bestillingskroppen — men
    // ikke `#profil-navn`. Feltet står åpent mens POST-en henger, og
    // kvitteringen leste det ETTER svaret mens kroppen bar navnet fra
    // kallstart. Alerten kunne dermed navngi en profil serveren aldri
    // så. (At feltet ikke fryses er frys-klassen, dom-klasse
    // `p2-1-og-p2-2-utsatt-til-214` — bindingen her er en annen sak.)
    const hoved = await tegnetMedProfiler();
    const seksjon = hoved.querySelector(
      "section[aria-labelledby=profil-tittel]");
    const rediger = [...seksjon.querySelectorAll("button")]
      .find((b) => b.textContent === t("ui.rekruttering.profiler.rediger"));
    rediger.click();
    const skjema = seksjon.querySelector("form");
    const navnInp = skjema.querySelector("#profil-navn");
    assert.equal(navnInp.value, "Driftskonsulent", "editoren var ikke fylt");
    navnInp.value = "Driftskonsulent II";
    let slipp;
    KALL = [];
    SVAR = (sti, opts) => {
      if ((opts.method || "GET") === "POST") {
        return new Promise((r) => { slipp = r; });
      }
      return sti.includes("stillingsprofiler") ? profiler() : prosess();
    };
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    assert.ok(await vent(() => KALL.some((k) => k.metode === "POST")),
      "POST-en gikk aldri");
    assert.equal(KALL.find((k) => k.metode === "POST").kropp.navn,
      "Driftskonsulent II", "kroppen bar ikke navnet fra kallstart");
    // Brukeren skriver videre mens POST-en står ubesvart — DA lander 2xx
    // på det navnet som faktisk ble sendt.
    navnInp.value = "En helt annen profil";
    slipp({ profil_id: "prof-1", versjon: 3 });
    const lagre = skjema.querySelector("button[type=submit]");
    assert.ok(await vent(() => !lagre.disabled, 40), "runden ble aldri ferdig");
    const melding = seksjon.querySelector("[role=alert]").textContent;
    assert.ok(melding.includes("Driftskonsulent II"),
      "kvitteringen navnga ikke profilen som faktisk ble lagret");
    assert.ok(!melding.includes("En helt annen profil"),
      "kvitteringen navnga navnet brukeren nettopp skrev, ikke det sendte");
    // MUTASJONEN SOM DREPER DENNE: les `navnInp.value` etter `await` igjen
    // (da blir navnet «En helt annen profil»).
  });

test("Profiler: tapt svar → retry sender SAMME nøkkel (SP-2)", async () => {
  const hoved = await tegnetMedProfiler();
  const seksjon = hoved.querySelector(
    "section[aria-labelledby=profil-tittel]");
  const rediger = [...seksjon.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.profiler.rediger"));
  rediger.click();
  const skjema = seksjon.querySelector("form");
  // Første forsøk: nettverket dør (fetch kaster → ApiFeil(0)).
  KALL = [];
  SVAR = (sti, opts) => {
    if ((opts.method || "GET") === "POST") throw new Error("nett");
    return sti.includes("stillingsprofiler") ? profiler() : prosess();
  };
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() => KALL.some((k) => k.metode === "POST")),
    "første POST gikk aldri");
  const forste = KALL.find((k) => k.metode === "POST");
  // Retryen er brukerens NESTE klikk, ikke et andre klikk midt i det
  // første: flaten holder ÉN mutasjon om gangen (A-dommen, #212), og
  // «Lagre» står frosset til runden er ferdig. Det er nøkkelen som er
  // under måling her, ikke låsen.
  const lagre = skjema.querySelector("button[type=submit]");
  assert.ok(await vent(() => !lagre.disabled, 40), "runden ble aldri ferdig");
  // Andre forsøk: samme operasjon — samme nøkkel.
  SVAR = { "/v1/rekruttering/prosesser": prosess(),
           "/v1/rekruttering/stillingsprofiler": profiler(),
           "/v1/rekruttering/evalueringer": { evalueringer: [] } };
  KALL = [];
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() => KALL.some((k) => k.metode === "POST")),
    "andre POST gikk aldri");
  const andre = KALL.find((k) => k.metode === "POST");
  assert.equal(andre.hoder["Idempotency-Key"],
    forste.hoder["Idempotency-Key"],
    "retry etter tapt svar byttet nøkkel — serveren kan ikke replaye");
});

test("Profiler: et tapt svar meldes som uvisst, ikke som «kunne ikke lagre» (P2-1)", async () => {
  // Nøkkeløkonomien skiller alt 4xx fra resten (nøkkelen står ved 0/5xx),
  // men teksten gjorde det ikke: en profilversjon kan stå lagret mens
  // svaret gikk tapt, og «sjekk kravene» er da falsk trygghet.
  const hoved = await tegnetMedProfiler();
  const seksjon = hoved.querySelector(
    "section[aria-labelledby=profil-tittel]");
  const alert = seksjon.querySelector("[role=alert]");
  const rediger = [...seksjon.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.profiler.rediger"));
  rediger.click();
  const skjema = seksjon.querySelector("form");
  const lagre = [...skjema.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.profiler.lagre"));
  const runde = async () => {
    skjema.dispatchEvent(new window.Event("submit",
      { bubbles: true, cancelable: true }));
    assert.ok(await vent(() => !lagre.disabled, 40), "runden ble aldri ferdig");
  };
  // Nettet dør: fetch kaster → ApiFeil(0). Utfallet er UKJENT.
  SVAR = (sti, opts) => {
    if ((opts.method || "GET") === "POST") throw new Error("nett");
    return sti.includes("stillingsprofiler") ? profiler() : prosess();
  };
  await runde();
  assert.equal(alert.textContent, t("ui.rekruttering.usikkert_utfall"),
    "et tapt svar ble meldt som en definitiv lagringsfeil");
  // 5xx er like uvisst — serveren kan ha skrevet versjonen.
  SVAR = (sti, opts) => {
    if ((opts.method || "GET") === "POST") return 500;
    return sti.includes("stillingsprofiler") ? profiler() : prosess();
  };
  await runde();
  assert.equal(alert.textContent, t("ui.rekruttering.usikkert_utfall"));
  // Serverens egen dom er derimot definitiv — og sier det.
  SVAR = (sti, opts) => {
    if ((opts.method || "GET") === "POST") return 409;
    return sti.includes("stillingsprofiler") ? profiler() : prosess();
  };
  await runde();
  assert.equal(alert.textContent, t("ui.rekruttering.profiler.feil"));
});

test("Profiler: endret innhold etter tapt svar gir NY nøkkel (P2-5)", async () => {
  const hoved = await tegnetMedProfiler();
  const seksjon = hoved.querySelector(
    "section[aria-labelledby=profil-tittel]");
  const rediger = [...seksjon.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.profiler.rediger"));
  rediger.click();
  const skjema = seksjon.querySelector("form");
  // Første forsøk: nettverket dør — utfallet er ukjent, nøkkelen står.
  KALL = [];
  SVAR = (sti, opts) => {
    if ((opts.method || "GET") === "POST") throw new Error("nett");
    return sti.includes("stillingsprofiler") ? profiler() : prosess();
  };
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() => KALL.some((k) => k.metode === "POST")),
    "første POST gikk aldri");
  const forste = KALL.find((k) => k.metode === "POST");
  // Runden må være ferdig før brukeren rører skjemaet igjen: flaten
  // holder ÉN mutasjon om gangen (A-dommen, #212).
  const lagre = skjema.querySelector("button[type=submit]");
  assert.ok(await vent(() => !lagre.disabled, 40), "runden ble aldri ferdig");
  // ... men brukeren endrer navnet. Da er neste lagring en ANNEN
  // profilversjon, og den gamle nøkkelen ville enten kollidert eller
  // fått serveren til å replaye den forrige.
  const navn = skjema.querySelector("#profil-navn");
  navn.value = "Driftsarkitekt";
  navn.dispatchEvent(new window.Event("input", { bubbles: true }));
  SVAR = { "/v1/rekruttering/prosesser": prosess(),
           "/v1/rekruttering/stillingsprofiler": profiler(),
           "/v1/rekruttering/evalueringer": { evalueringer: [] } };
  KALL = [];
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  assert.ok(await vent(() => KALL.some((k) => k.metode === "POST")),
    "andre POST gikk aldri");
  const andre = KALL.find((k) => k.metode === "POST");
  assert.equal(andre.kropp.navn, "Driftsarkitekt");
  assert.notEqual(andre.hoder["Idempotency-Key"],
    forste.hoder["Idempotency-Key"],
    "endret innhold bar fortsatt den gamle intensjonens nøkkel");
});


// Rapportfikstur for produktportene under (28/8): to kandidater er nok
// til å måle både radplasseringen og at ingen mur står igjen.
function enkelRapportSvar() {
  return {
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/96": { oppdrag_id: 96, rapport: {
      rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
      profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
      antall_soknader: 2,
      rangering: [
        { kandidat_id: "kandidat-01", poeng: 5, nedbrytning: { drift: 3 } },
        { kandidat_id: "kandidat-02", poeng: 3, nedbrytning: { drift: 3 } },
      ],
      kandidater: {
        "kandidat-01": { funn: [], intervjusporsmal: [], kildetekst: "a" },
        "kandidat-02": { funn: [], intervjusporsmal: [], kildetekst: "b" },
      },
      fremdrift: { filer_lest: 2, filer_totalt: 2, byte_lest: 100 },
    } } };
}
test("Rapport: navnet står ÉN gang — for øyet OG for øret", async () => {
  // Produktrunden 28/8 fjernet det SYNLIGE duplikatet ved å gjøre
  // captionen `sr-only`. Men `sr-only` skjuler bare for øyet: caption og
  // `h3` sto igjen med identisk tekst i tilgjengelighetstreet, så
  // skjermleseren leste «Rangering — Driftskonsulent (versjon 2)» først
  // som overskrift og så en gang til som tabellnavn (Cursor P2).
  //
  // Neste forsøk lot tabellen LÅNE overskriften via `aria-labelledby`.
  // Det flyttet hvor navnet kom fra, men ikke HVA som ble annonsert:
  // tabellen har fortsatt sin egen annonsering, og strengen var den
  // samme (Codex P2). Duplikatet lå i TEKSTEN, ikke i noden.
  //
  // Porten måler derfor rotårsaken og ikke formen: tabellens
  // tilgjengelige navn — uansett om det kommer fra caption,
  // `aria-label` eller `aria-labelledby` — skal finnes, og det skal
  // IKKE være overskriftens tekst.
  //
  // MUTASJONEN SOM DREPER DENNE: gi tabellen overskriftens tekst igjen,
  // på hvilken som helst av de tre måtene — eller fjern navnet helt.
  KALL = [];
  SVAR = { ...enkelRapportSvar() };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.textContent.includes("kandidat-01")));
  // RAPPORTENS tabell, ikke prosessens: DataTabell-en over har sin egen
  // caption, og `querySelector` ville tatt den første i DOM-en.
  const rapporttabell = [...hoved.querySelectorAll("table")]
    .find((tb) => tb.textContent.includes("kandidat-01"));
  assert.ok(rapporttabell, "rangeringstabellen mangler");
  const synlige = [...hoved.querySelectorAll("h3")]
    .filter((h) => h.textContent.includes("Driftskonsulent"));
  assert.equal(synlige.length, 1,
    `overskriften står ${synlige.length} ganger`);
  const overskrift = synlige[0];
  // Tabellens tilgjengelige navn, slik en skjermleser regner det ut:
  // `aria-label`, ellers `aria-labelledby` → den utpekte noden, ellers
  // `caption`. Alle tre veiene måles av samme port.
  const pekt = rapporttabell.getAttribute("aria-labelledby");
  const cap = rapporttabell.querySelector("caption");
  const navn = (rapporttabell.getAttribute("aria-label")
    || (pekt && (hoved.querySelector(`#${pekt}`) || {}).textContent)
    || (cap && cap.textContent) || "").trim();
  assert.ok(navn, "rangeringstabellen har ikke noe tilgjengelig navn");
  // ROTÅRSAKEN: navnet skal ikke være overskriftens tekst — hverken
  // kopiert inn eller lånt via `aria-labelledby`. Overskriften
  // annonseres allerede av seg selv rett før tabellen.
  assert.notEqual(navn, overskrift.textContent.trim(),
    "tabellnavnet gjentar overskriften for øret");
  // ...og det er ikke bare ordrett likhet som er duplikatet: bærer
  // navnet profilen fra overskriften, er det den samme opplysningen om
  // igjen.
  assert.ok(!navn.includes("Driftskonsulent"),
    `tabellnavnet gjentar profilen fra overskriften: «${navn}»`);
  // Navnet står ikke i den SYNLIGE flaten: PR-ens produktvalg er én
  // overskrift for øyet. En caption uten `sr-only` ville bygget den
  // andre linjen opp igjen.
  if (cap) {
    assert.ok(cap.classList.contains("sr-only"),
      "tabellnavnet ble synlig og gir øyet to linjer igjen");
  }
  // ...og fokusmålet etter lasting er fortsatt overskriften.
  assert.equal(overskrift.getAttribute("tabindex"), "-1",
    "overskriften er ikke lenger fokusmål etter lasting");
});

test("Maler: en verdi som bærer en plassholder stjeler ikke neste nøkkel",
  async () => {
  // Cursor P2. Malene ble fylt med en KJEDE av `.replace`: først
  // `{navn}`, så `{versjon}`. Kjeden leser resultatet av forrige ledd om
  // igjen, så et profilnavn som selv inneholder `{versjon}` fikk
  // versjonen limt inn i SEG — og malens egen `{versjon}` sto igjen rå.
  // På rapportens `h3` er det fokusmålet etter lasting, altså det første
  // en skjermleser sier når rapporten er klar.
  //
  // Navnet er brukerens: `#profil-navn` er et fritt tekstfelt, så
  // «Drift{versjon}X» er en profil noen kan lagre i dag.
  //
  // PORTEN SUBSTITUERER IKKE SELV. Den deler malen på plassholderne og
  // limer segmentene rundt verdiene — ellers hadde den målt sin egen
  // implementasjon i stedet for flatens.
  //
  // MUTASJONEN SOM DREPER DENNE: bytt `flett(...)` tilbake til
  // `.replace("{navn}", …).replace("{versjon}", …)` på ett av de tre
  // stedene (rapportoverskrift, profilvalg, lagret-kvittering).
  const navn = "Drift{versjon}X";
  const ventet = (nokkel, versjon) => {
    const [a, b, c] = t(nokkel).split(/\{navn\}|\{versjon\}/);
    assert.ok(c !== undefined,
      `malen ${nokkel} har ikke lenger begge plassholderne`);
    return `${a}${navn}${b}${versjon}${c}`;
  };
  const enProfil = { ...profiler().profiler[0], navn };
  const basis = enkelRapportSvar();
  basis["/v1/rekruttering/stillingsprofiler"] = { profiler: [enProfil] };
  basis["/v1/rekruttering/rapport/96"] = {
    oppdrag_id: 96,
    rapport: { ...basis["/v1/rekruttering/rapport/96"].rapport,
      profil: { profil_id: "prof-1", versjon: 2, navn } } };
  KALL = [];
  SVAR = basis;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.textContent.includes("kandidat-01")),
    "rapporten kom aldri");

  // (1) Rapportens overskrift — fokusmålet etter lasting.
  const [forNavn] = t("ui.rekruttering.evalueringer.rangering").split("{navn}");
  const overskrift = [...hoved.querySelectorAll("h3")]
    .find((h) => h.textContent.startsWith(forNavn));
  assert.ok(overskrift, "rangeringsoverskriften mangler");
  assert.equal(overskrift.textContent,
    ventet("ui.rekruttering.evalueringer.rangering", 2),
    "overskriftens versjon ble spist av profilnavnet");

  // (2) Bestillingens profilvalg — samme mal, samme kjede.
  const valg = hoved.querySelector("select#bestill-profil option");
  assert.ok(valg, "profilvelgeren mangler");
  assert.equal(valg.textContent,
    ventet("ui.rekruttering.bestill.profilvalg", 2),
    "nedtrekkets versjon ble spist av profilnavnet");

  // (3) Kvitteringen etter lagring — den tredje kallstedet.
  const pSeksjon = hoved.querySelector(
    "section[aria-labelledby=profil-tittel]");
  [...pSeksjon.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.profiler.rediger"))
    .click();
  const skjema = pSeksjon.querySelector("form");
  skjema.querySelector("#profil-navn").value = navn;
  KALL = [];
  SVAR = (sti, opts) => ((opts.method || "GET") === "POST"
    ? { profil_id: "prof-1", versjon: 3 } : basis[sti]);
  skjema.dispatchEvent(new window.Event("submit",
    { bubbles: true, cancelable: true }));
  const lagre = skjema.querySelector("button[type=submit]");
  assert.ok(await vent(() => !lagre.disabled, 40), "runden ble aldri ferdig");
  assert.equal(pSeksjon.querySelector("[role=alert]").textContent,
    ventet("ui.rekruttering.profiler.lagret", 3),
    "kvitteringens versjon ble spist av profilnavnet");
});

test("Rapport: detaljene ligger i kandidatens RAD, ikke som en mur under", async () => {
  // De sto som en flat liste av `<details>` under tabellen — én per
  // kandidat, opp mot 5000 — uten kobling til linjen de gjaldt. Leseren
  // måtte telle seg fram.
  //
  // MUTASJONEN SOM DREPER DENNE: legg detaljene tilbake som søsken av
  // tabellen.
  KALL = [];
  SVAR = { ...enkelRapportSvar() };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.textContent.includes("kandidat-01")));
  const tabell = [...hoved.querySelectorAll("table")]
    .find((tb) => tb.textContent.includes("kandidat-01"));
  assert.ok(tabell, "rangeringstabellen mangler");
  const iRad = tabell.querySelectorAll("tbody tr details");
  assert.equal(iRad.length, 2, "detaljene ligger ikke i radene");
  // ...og ingen står igjen utenfor tabellen.
  // Vekterfolden er en EGEN, villet detalj (mobilfunn 30/8) — muren
  // porten vokter mot er funn-detaljene.
  const utenfor = [...hoved.querySelectorAll("details")]
    .filter((d) => !tabell.contains(d)
      && !d.classList.contains("rekrut-vekterfold"));
  assert.equal(utenfor.length, 0,
    `${utenfor.length} detaljbokser står fortsatt som en mur under tabellen`);
});

test("Prosess: to id-er med samme åtte tegn får ULIKE kortnavn", async () => {
  // Codex P2: et UUID-prefiks på åtte heksadesimaler er 32 bit, og det er
  // ingen garanti innenfor de 5000 kandidatene skjemaet tillater. To rader
  // kunne vist samme referanse — på flaten der leseren skal skille dem fra
  // hverandre før en irreversibel utsendelse — mens `title` hverken er
  // kopierbar eller tilgjengelig for berøring og tastatur.
  //
  // Lengden regnes nå av DATAENE: korteste prefiks som er entydig i denne
  // prosessen.
  //
  // MUTASJONEN SOM DREPER DENNE: `id.slice(0, 8)` igjen.
  const a = "58f17252-8a2b-4092-a420-adf5d5d430d1";
  const b = "58f17252-8a2b-4092-a420-adf5d5d430d2";  // skiller på SISTE tegn
  const data = prosess();
  data.prosesser[0].kandidater = [a, b].map((id) => ({
    kandidat_id: id, oppfylt: { drift: true }, status: "anbefalt",
    funn: [], intervjusporsmal: [] }));
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": data };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelectorAll("tbody tr").length === 2));
  const viste = [...hoved.querySelectorAll("tbody tr")]
    .map((tr) => tr.querySelector("td, th").textContent.trim());
  assert.equal(new Set(viste).size, 2,
    `to kandidater vises med SAMME referanse: ${JSON.stringify(viste)}`);
  // Og begge hele id-ene er fortsatt å få tak i.
  const titler = [...hoved.querySelectorAll("[title]")]
    .map((n) => n.getAttribute("title"));
  assert.ok(titler.includes(a) && titler.includes(b),
    "en av de hele id-ene forsvant");
});

test("Rapport: hvert «Vis funn» navngir sin egen kandidat for øret",
  async () => {
  // Codex P2: en skjermleser som lister interaktive elementer leser
  // kontrollens tilgjengelige navn ALENE — radoverskriften ved siden av
  // er ikke med. Fem tusen kontroller som alle heter «Vis funn» er
  // nøyaktig den telle-seg-fram-en denne runden fjernet for øyet.
  //
  // Den SYNLIGE teksten skal forbli kort: gjentok vi kandidaten der,
  // ville vi vært tilbake i «Detaljer for kandidat-NN» på hver linje.
  //
  // MUTASJONEN SOM DREPER DENNE: fjern `aria-label` fra summary-en.
  KALL = [];
  SVAR = { ...enkelRapportSvar() };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.textContent.includes("kandidat-01")));
  const tabell = [...hoved.querySelectorAll("table")]
    .find((tb) => tb.textContent.includes("kandidat-01"));
  const sammendrag = [...tabell.querySelectorAll("tbody tr summary")];
  assert.equal(sammendrag.length, 2, "detaljene mangler i radene");
  const navn = sammendrag.map((sm) => sm.getAttribute("aria-label"));
  assert.ok(navn.every(Boolean),
    "et sammendrag mangler tilgjengelig navn — det leses som «Vis funn»");
  assert.equal(new Set(navn).size, 2,
    `to kontroller har samme navn: ${JSON.stringify(navn)}`);
  assert.ok(navn.some((n) => n.includes("kandidat-01"))
    && navn.some((n) => n.includes("kandidat-02")),
    `navnene bærer ikke kandidatene: ${JSON.stringify(navn)}`);
  // Den synlige teksten er fortsatt kort.
  assert.ok(sammendrag.every(
    (sm) => sm.textContent === t("ui.rekruttering.evalueringer.vis_funn")),
    "kandidaten kom inn i den SYNLIGE teksten — muren er tilbake i raden");
});

test("Vis funn: det tilgjengelige navnet er en LOCALE-mal, ikke en setning i koden",
  async () => {
  // Cursor P2 · RUTINER §5. Navnet ble limt sammen i koden: `vis_funn`
  // pluss et hardkodet `" — "` pluss `kandidat` pluss kortnavnet.
  // Skilletegnet og ordstillingen sto dermed UTENFOR locale, og et språk
  // som vil sette kandidaten først — eller skille med noe annet enn en
  // tankestrek — kunne ikke uttrykke det uansett hvor godt oversatt hver
  // enkelt brikke var.
  //
  // PORTEN KJØRER PÅ ENGELSK, for det er der en hardkodet norsk form
  // faktisk kan sees: på `nb` er «riktig» og «hardkodet» den samme
  // strengen, og porten ville vært grønn på begge.
  //
  // MUTASJONEN SOM DREPER DENNE: bygg navnet i koden igjen
  // (`${t("…vis_funn")} — ${t("…kandidat")} ${kortnavn(...)}`).
  settI18nForTest(EN, "en");
  try {
    KALL = [];
    SVAR = { ...enkelRapportSvar() };
    const hoved = nyHoved();
    visRekruttering(hoved, ctx());
    assert.ok(await vent(() => hoved.textContent.includes("kandidat-01")),
      "rapporten kom aldri");
    const tabell = [...hoved.querySelectorAll("table")]
      .find((tb) => tb.textContent.includes("kandidat-01"));
    // Funnene bor i egen fullbredderad (mobil-redesignet 29/8):
    // hovedradene telles, og summaryen står i PARETS detaljrad.
    const rader = [...tabell.querySelectorAll(
      "tbody tr:not(.rekrut-detaljrad)")];
    assert.equal(rader.length, 2, "rangeringen kom aldri");
    // Malen deles på plassholderen — porten substituerer ikke selv, og
    // måler derfor flatens fylling og ikke sin egen.
    const [for_, etter] = t("ui.rekruttering.evalueringer.vis_funn_for")
      .split("{kandidat}");
    assert.ok(etter !== undefined, "malen har ikke lenger {kandidat}");
    assert.ok(for_.trim(), "malen er tom — da måler resten ingenting");
    for (const rad of rader) {
      const synlig = rad.querySelector("th[scope=row]").textContent.trim();
      const sm = rad.nextElementSibling.querySelector("details > summary");
      assert.equal(sm.getAttribute("aria-label"), `${for_}${synlig}${etter}`,
        "det tilgjengelige navnet følger ikke malen i locale");
      // Den synlige teksten er fortsatt bare handlingen.
      assert.equal(sm.textContent, t("ui.rekruttering.evalueringer.vis_funn"),
        "kandidaten kom inn i den SYNLIGE teksten");
    }
  } finally {
    settI18nForTest(NB, "nb");
  }
});

test("Detaljer: prosesstabellens tilgjengelige navn er en LOCALE-mal, ikke en setning i koden",
  async () => {
  // Cursor P2 · RUTINER §5 — SØSTERKONTROLLEN til porten over. Rapportens
  // «Vis funn» fikk `vis_funn_for`, mens prosesstabellens «Detaljer» ble
  // stående med `${t("…detaljer")}: ${kortnavn(...)}`: kolonet og
  // ordstillingen sto i koden, utenfor locale, på nøyaktig samme defekt
  // to runder alt har felt ett lesested lenger opp.
  //
  // PORTEN KJØRER PÅ ENGELSK av samme grunn som søsteren: på `nb` er
  // «riktig» og «hardkodet» den samme strengen, og porten ville vært
  // grønn på begge.
  //
  // MUTASJONEN SOM DREPER DENNE: bygg navnet i koden igjen
  // (`${t("ui.rekruttering.detaljer")}: ${kortnavn(kandidat.kandidat_id)}`).
  settI18nForTest(EN, "en");
  try {
    KALL = [];
    SVAR = { "/v1/rekruttering/prosesser": prosess() };
    const hoved = nyHoved();
    visRekruttering(hoved, ctx());
    assert.ok(await vent(() => hoved.querySelector("tbody .handling-celle button")),
      "prosesstabellen kom aldri");
    // Malen deles på plassholderen — porten substituerer ikke selv, og
    // måler derfor flatens fylling og ikke sin egen.
    const [for_, etter] = t("ui.rekruttering.detaljer_for").split("{kandidat}");
    assert.ok(etter !== undefined, "malen har ikke lenger {kandidat}");
    assert.ok(for_.trim(), "malen er tom — da måler resten ingenting");
    const rader = [...hoved.querySelectorAll("tbody tr")];
    assert.equal(rader.length, 2, "begge kandidatradene skal stå");
    for (const rad of rader) {
      const synlig = rad.querySelector(".rekrut-kandidat").textContent.trim();
      const knapp = rad.querySelector(".handling-celle button");
      assert.equal(knapp.getAttribute("aria-label"), `${for_}${synlig}${etter}`,
        "det tilgjengelige navnet følger ikke malen i locale");
      // Den synlige teksten er fortsatt bare handlingen.
      assert.equal(knapp.textContent, t("ui.rekruttering.detaljer"),
        "kandidaten kom inn i den SYNLIGE knappeteksten");
    }
  } finally {
    settI18nForTest(NB, "nb");
  }
});

// Samme fikstur, andre id-er: portene under måler FORKORTINGEN, ikke
// rapportens form. Poengene faller med rekkefølgen, så rangeringen er
// den kalleren ba om.
function rapportSvarMed(...ider) {
  const svar = enkelRapportSvar();
  const rapport = svar["/v1/rekruttering/rapport/96"].rapport;
  rapport.rangering = ider.map((id, i) => (
    { kandidat_id: id, poeng: 5 - i, nedbrytning: { drift: 3 } }));
  rapport.kandidater = Object.fromEntries(ider.map((id) => (
    [id, { funn: [], intervjusporsmal: [], kildetekst: "a" }])));
  return svar;
}

// RANGERINGSTABELLEN, IKKE PROSESSENS: flaten har flere `<table>`, og
// `querySelector` ville tatt den første i DOM-en. Kjennetegnet er
// tabellens EGET tilgjengelige navn — den `sr-only`-captionen den fikk
// da den sluttet å låne overskriftens (Codex P2: `aria-labelledby` mot
// `h3` flyttet hvor navnet kom fra, ikke hva som ble annonsert for øret).
//
// NAVNET LESES UT AV LOCALE, IKKE SKREVET INN HER (Cursor P2). En norsk
// literal gjorde hjelperen — og dermed hver port som bygger på den —
// blind på engelsk, der captionen heter «Candidates ranked by score».
// `find` returnerte da `undefined`, og portene falt på «tabellen
// mangler» i stedet for på det de er til for å måle.
function rapporttabellen(hoved) {
  return [...hoved.querySelectorAll("table")].find((tb) => {
    const cap = tb.querySelector("caption");
    return cap && cap.textContent.trim()
      === t("ui.rekruttering.evalueringer.tabellnavn");
  });
}

test("Rapport: hjelperen finner rangeringstabellen på ENGELSK òg", async () => {
  // Cursor P2. `rapporttabellen()` matchet captionen mot en norsk
  // literal mens produksjonen skriver `t("…tabellnavn")`. En port som
  // bare kan kjøre på sitt eget språk måler språket, ikke koden — og
  // portene som bygger på hjelperen (kortnavn, tilgjengelig navn,
  // detaljkropp) ville alle falt på «tabellen mangler» det øyeblikket
  // noen kjørte dem på engelsk.
  //
  // MUTASJONEN SOM DREPER DENNE: skriv «Kandidater rangert etter poeng»
  // tilbake som literal i `rapporttabellen()`.
  settI18nForTest(EN, "en");
  try {
    KALL = [];
    SVAR = { ...enkelRapportSvar() };
    const hoved = nyHoved();
    visRekruttering(hoved, ctx());
    assert.ok(await vent(() => hoved.textContent.includes("kandidat-01")),
      "rapporten kom aldri");
    // Fiksturen krysser faktisk språkskillet — ellers måler resten intet.
    assert.notEqual(t("ui.rekruttering.evalueringer.tabellnavn"),
      "Kandidater rangert etter poeng",
      "EN-settet ga norsk tabellnavn — porten måler da ingenting");
    const tabell = rapporttabellen(hoved);
    assert.ok(tabell, "hjelperen fant ikke rangeringstabellen på engelsk");
    // …og det er RANGERINGEN den fant, ikke prosessens tabell.
    assert.equal(tabell.querySelectorAll(
      "tbody tr:not(.rekrut-detaljrad)").length, 2,
    "hjelperen traff en annen tabell enn rangeringen");
  } finally {
    settI18nForTest(NB, "nb");
  }
});

test("Rapport: en lang kandidat-id kortes, men mistes ikke", async () => {
  // Cursor P2: kortnavnet gjaldt bare prosesstabellen. Rapporten står
  // ØVERST — den er produktdelen leseren møter først — og viste rå
  // `kandidat_id` i radoverskriften. Med seedens UUID-er ble det en vegg
  // av heksadesimal i rapporten og en ryddet kolonne under.
  //
  // MUTASJONEN SOM DREPER DENNE: skriv `rad.kandidat_id` rått i `th`-en
  // igjen (eller fjern `title`).
  const uuid = "58f17252-8a2b-4092-a420-adf5d5d430d1";
  KALL = [];
  SVAR = rapportSvarMed(uuid);
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.textContent.includes("58f17252")));
  const tabell = rapporttabellen(hoved);
  assert.ok(tabell, "rangeringstabellen mangler");
  assert.ok(!tabell.textContent.includes(uuid),
    "hele UUID-en står i rapporttabellen — kolonnen er en vegg på mobil");
  const th = tabell.querySelector("tbody th[scope=row]");
  assert.ok(th, "radoverskriften mangler");
  assert.ok(th.textContent.includes("58f17252"),
    "kandidaten er ikke gjenkjennelig i det hele tatt");
  assert.equal(th.getAttribute("title"), uuid,
    "hele id-en er borte fra rapporten — den skal kunne kopieres");
});

test("Rapport: to id-er med samme åtte tegn får ULIKE kortnavn", async () => {
  // Speiler prosessportens måling på rapportveien: åtte heksadesimaler
  // er 32 bit og ingen garanti innenfor de 5000 kandidatene skjemaet
  // tillater. Skiller intet prefiks dem, står id-ene urørt — flaten
  // lyver heller ikke om at to referanser er like.
  //
  // MUTASJONEN SOM DREPER DENNE: `id.slice(0, 8)` i rapportens `th`.
  const a = "58f17252-8a2b-4092-a420-adf5d5d430d1";
  const b = "58f17252-8a2b-4092-a420-adf5d5d430d2";  // skiller på SISTE tegn
  KALL = [];
  SVAR = rapportSvarMed(a, b);
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => {
    const tb = rapporttabellen(hoved);
    return tb && tb.querySelectorAll("tbody th[scope=row]").length === 2;
  }));
  const tabell = rapporttabellen(hoved);
  const viste = [...tabell.querySelectorAll("tbody th[scope=row]")]
    .map((th) => th.textContent.trim());
  assert.equal(new Set(viste).size, 2,
    `to kandidater vises med SAMME referanse: ${JSON.stringify(viste)}`);
  // Og begge hele id-ene er fortsatt å få tak i.
  const titler = [...tabell.querySelectorAll("tbody th[scope=row]")]
    .map((th) => th.getAttribute("title"));
  assert.ok(titler.includes(a) && titler.includes(b),
    `en av de hele id-ene forsvant: ${JSON.stringify(titler)}`);
});

test("Rapport: den utvidede raden bryter den lange id-en i stedet for tabellen",
  async () => {
  // Codex P2 (review 5057226805, aldri lukket): detaljpanelet flyttet INN i
  // tabellcellen denne runden, og der teller innholdet med i tabellens
  // intrinsikke bredde. En kandidat-id kan per `KANDIDAT_ID_KANON` være 64
  // tegn uten et eneste blanktegn å bryte på, så id-linjen i panelet løftet
  // cellens minstebredde og skjøv tabellen ut i `.tablewrap`-ens sidescroll —
  // på mobillayouten flyttingen var til for. Før lå detaljene UTENFOR
  // tabellen og kunne ikke gjøre det.
  //
  // jsdom legger ikke ut noe, så porten måler de to leddene som FAKTISK
  // bærer bruddet: at panelet i cellen har klassen, og at klassen bryter på
  // en måte som teller med i min-content-bredden.
  //
  // MUTASJONENE SOM DREPER DENNE:
  //   · `class: "rekrut-detalj"` fjernet fra `detaljboks`      → rød
  //   · regelen fjernet fra `base.css`                         → rød
  //   · `anywhere` → `break-word` (bryter synlig, men lar       → rød
  //     tabellen være like bred — altså funnet i behold)
  const lang = "a".repeat(64); // gyldig id, ingen brytepunkt
  KALL = [];
  SVAR = rapportSvarMed(lang);
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => {
    const tb = rapporttabellen(hoved);
    return tb && tb.querySelectorAll("tbody th[scope=row]").length === 1;
  }));
  const iCellen = [...rapporttabellen(hoved).querySelectorAll("td details")];
  assert.equal(iCellen.length, 1, "fant ikke detaljpanelet i tabellcellen");
  assert.ok(iCellen[0].classList.contains("rekrut-detalj"),
    "panelet i cellen har ingen ombrekking — den lange id-en løfter "
    + "tabellens minstebredde og gir sidescroll på mobil");
  const css = readFileSync(join(ROT,
    "platform/core/ui/static/css/base.css"), "utf-8");
  const regel = css.match(/\.rekrut-detalj\s*\{([^}]*)\}/);
  assert.ok(regel, "ingen `.rekrut-detalj`-regel i base.css");
  assert.match(regel[1], /overflow-wrap:\s*anywhere/,
    "`.rekrut-detalj` bryter ikke med `anywhere` — bare `anywhere` teller "
    + `med i min-content-bredden: ${regel[1].trim()}`);
});

test("Kandidatcellen bryter den lange id-en i stedet for tabellen — BEGGE tabeller",
  async () => {
  // Cursor P2 (runde 6). Ombrekkingen over dekket bare `<details>`. Den
  // SYNLIGE kandidatreferansen — `th` i rapporten, cellen i prosesstabellen —
  // sto uten vern, og det gikk bra bare så lenge alt langt ble kortet til et
  // prefiks. Kortingen gjelder nå bare de ugjennomsiktige id-ene (samme
  // runde), så et beskrivende manifestnavn på 64 tegn står HELT i cellen —
  // uten et eneste blanktegn å bryte på, per `KANDIDAT_ID_KANON` — og løfter
  // kolonnens min-content akkurat som id-linjen i panelet gjorde. Wrap-porten
  // over måler bare `details` og kunne ikke se det.
  //
  // jsdom legger ikke ut noe, så porten måler de to leddene som FAKTISK bærer
  // bruddet, som wrap-porten over: at cellen har klassen i begge tabeller, og
  // at klassen bryter på en måte som teller med i min-content-bredden.
  //
  // MUTASJONENE SOM DREPER DENNE:
  //   · klassen fjernet fra rapportens `th`                     → rød
  //   · klassen fjernet fra prosesstabellens celle              → rød
  //   · regelen fjernet fra `base.css`                          → rød
  //   · `anywhere` → `break-word` (bryter synlig, men lar        → rød
  //     tabellen være like bred — altså funnet i behold)
  const lang = "zulu-senior-backend-engineer-med-langt-beskrivende-navn-01xy";
  assert.ok(lang.length > 20 && !/\s/.test(lang),
    "fiksturen må være lang og uten brytepunkt, ellers måler porten intet");

  // (1) Rapportens radoverskrift.
  KALL = [];
  SVAR = rapportSvarMed(lang);
  let hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => {
    const tb = rapporttabellen(hoved);
    return tb && tb.querySelectorAll("tbody th[scope=row]").length === 1;
  }));
  const th = rapporttabellen(hoved).querySelector("tbody th[scope=row]");
  assert.equal(th.textContent.trim(), lang,
    "fiksturen ble kortet — da bærer ikke cellen bredden porten måler");
  assert.ok(th.classList.contains("rekrut-kandidat"),
    "rapportens radoverskrift har ingen ombrekking — den lange id-en løfter "
    + "tabellens minstebredde og gir sidescroll på mobil");

  // (2) Prosesstabellens celle — samme referanse, andre tabell.
  const data = prosess();
  data.prosesser[0].kandidater = [{ kandidat_id: lang, oppfylt: { drift: true },
    status: "anbefalt", funn: [], intervjusporsmal: [] }];
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": data };
  hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelectorAll("tbody tr").length === 1));
  assert.ok(hoved.querySelector("tbody tr .rekrut-kandidat"),
    "prosesstabellens kandidatcelle har ingen ombrekking");

  // (3) Og klassen bryter på måten som faktisk teller.
  const css = readFileSync(join(ROT,
    "platform/core/ui/static/css/base.css"), "utf-8");
  const regel = css.match(/\.rekrut-kandidat\s*\{([^}]*)\}/);
  assert.ok(regel, "ingen `.rekrut-kandidat`-regel i base.css");
  assert.match(regel[1], /overflow-wrap:\s*anywhere/,
    "`.rekrut-kandidat` bryter ikke med `anywhere` — bare `anywhere` teller "
    + `med i min-content-bredden: ${regel[1].trim()}`);
});

test("Kortnavn: et beskrivende manifestnavn kortes ALDRI, uansett lengde",
  async () => {
  // Codex P2 (runde 3): kortingen het «maskingenererte id-er kortes, navn
  // kunden selv har gitt står urørt», men den målte `length > 20`. Kanonen i
  // `m57_ats/parsing.py` tillater kundevalgte ASCII-id-er på inntil 64 tegn,
  // så et beskrivende navn på 26 tegn ble `senior-b…` — i BEGGE tabeller og
  // i kontrollenes tilgjengelige navn. Leseren måtte åpne detaljpanelet for
  // å se hvem raden gjaldt, som er nøyaktig det kortnavnet fjernet.
  //
  // Porten måler ROTÅRSAKEN og ikke tallet: den lange, beskrivende id-en må
  // stå HEL, mens den ugjennomsiktige UUID-en i SAMME tabell fortsatt kortes.
  // Målte den bare det første, ville «fjern kortingen helt» gått grønn.
  //
  // MUTASJONENE SOM DREPER DENNE:
  //   · `erUgjennomsiktig` → `id.length > 20` igjen  → navnet kortes  → rød
  //   · `erUgjennomsiktig` → `false`                 → UUID-en står hel → rød
  const navn = "senior-backend-engineer-01";           // 26 tegn, lesbart
  const uuid = "58f17252-8a2b-4092-a420-adf5d5d430d1"; // ugjennomsiktig
  const data = prosess();
  data.prosesser[0].kandidater = [navn, uuid].map((id) => ({
    kandidat_id: id, oppfylt: { drift: true }, status: "anbefalt",
    funn: [], intervjusporsmal: [] }));
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": data };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelectorAll("tbody tr").length === 2));
  const viste = [...hoved.querySelectorAll("tbody tr")]
    .map((tr) => tr.querySelector("td, th").textContent.trim());
  assert.ok(viste.includes(navn),
    `det beskrivende navnet ble kortet bort: ${JSON.stringify(viste)}`);
  // ... OG den ugjennomsiktige id-en kortes fortsatt, ellers måler porten
  // bare at kortingen er skrudd av.
  assert.ok(viste.some((v) => v !== navn && v.endsWith("…") && uuid.startsWith(v.slice(0, -1))),
    `UUID-en ble ikke kortet — kortingen er slått av: ${JSON.stringify(viste)}`);
});

test("Kortnavn: en ren sifferstreng er ikke heksveggen — den står hel",
  async () => {
  // Cursor P2 (runde 6). Runde 3 byttet lengdemålet mot en tegnklassetest,
  // men `[0-9a-fA-F]` INNEHOLDER sifrene: en id uten en eneste bokstav falt
  // like fullt igjennom som «ugjennomsiktig». `KANDIDAT_ID_KANON`
  // (`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`) tillater nettopp den formen, og et
  // kundenummer som `202408150012345678901` er et menneske leser — ikke en
  // digest. Predikatet er stengt («er vi ikke sikre, står id-en hel»), og
  // dette var det ene stedet asymmetrien pekte feil vei.
  //
  // Porten måler begge lesestedene, fordi kortnavnet har ÉN algoritme og to
  // tabeller: prosesstabellen og rangeringstabellen i rapporten. Og den
  // måler de to positive tegnene hver for seg, ellers ville «skru av
  // kortingen helt» gått grønn.
  //
  // MUTASJONENE SOM DREPER DENNE:
  //   · `HEKSBOKSTAV || UUID_FORM`-leddet fjernet → sifferid kortes  → rød
  //   · `erUgjennomsiktig` → `false`              → UUID-en står hel → rød
  //   · `UUID_FORM`-grenen fjernet         → siffer-UUID-en står hel → rød
  const siffer = "202408150012345678901";              // 21 tegn, lesbart
  const uuid = "58f17252-8a2b-4092-a420-adf5d5d430d1"; // ugjennomsiktig
  const sifferUuid = "20240815-0012-3456-7890-123456789012"; // gruppeformen

  // (1) Prosesstabellen.
  const data = prosess();
  data.prosesser[0].kandidater = [siffer, uuid, sifferUuid].map((id) => ({
    kandidat_id: id, oppfylt: { drift: true }, status: "anbefalt",
    funn: [], intervjusporsmal: [] }));
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": data };
  let hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelectorAll("tbody tr").length === 3));
  const iProsess = [...hoved.querySelectorAll("tbody tr")]
    .map((tr) => tr.querySelector("td, th").textContent.trim());
  assert.ok(iProsess.includes(siffer),
    `sifferid-en ble kortet i prosesstabellen: ${JSON.stringify(iProsess)}`);
  // ... OG heksveggen kortes fortsatt — begge grenene, hver for seg.
  assert.ok(iProsess.some((v) => v.endsWith("…") && uuid.startsWith(v.slice(0, -1))),
    `UUID-en ble ikke kortet — kortingen er slått av: ${JSON.stringify(iProsess)}`);
  assert.ok(iProsess.some((v) => v.endsWith("…") && sifferUuid.startsWith(v.slice(0, -1))),
    "UUID-ens gruppeform er maskingenerert uansett hvilke siffer den fikk, "
    + `og skal fortsatt kortes: ${JSON.stringify(iProsess)}`);

  // (2) Rangeringstabellen i rapporten — samme algoritme, andre lesested.
  KALL = [];
  SVAR = rapportSvarMed(siffer, uuid);
  hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => {
    const tb = rapporttabellen(hoved);
    return tb && tb.querySelectorAll("tbody th[scope=row]").length === 2;
  }));
  const iRapport = [...rapporttabellen(hoved)
    .querySelectorAll("tbody th[scope=row]")].map((th) => th.textContent.trim());
  assert.ok(iRapport.includes(siffer),
    `sifferid-en ble kortet i rapporttabellen: ${JSON.stringify(iRapport)}`);
  assert.ok(iRapport.some((v) => v.endsWith("…") && uuid.startsWith(v.slice(0, -1))),
    `UUID-en ble ikke kortet i rapporten: ${JSON.stringify(iRapport)}`);
});

test("Kortnavn: det tilgjengelige navnet bærer RADENS referanse, ikke rå UUID",
  async () => {
  // Pass-funn (runde 3). `kortnavnFor` kortet den SYNLIGE teksten, mens de
  // to tilgjengelige navnene aldri ble med: `tilgjengeligNavn` på
  // prosessens radhandling og `aria-label` på rapportens `<summary>` limte
  // fortsatt rå `kandidat_id`. Kontrollen navnga altså kandidaten med en
  // streng som ikke sto noe sted på skjermen, og for den som lister
  // interaktive elementer var hver rad tilbake til sin vegg av heksadesimal.
  //
  // PORT 29 MÅLTE DETTE ALT — OG KUNNE LIKEVEL IKKE SE DET. Den krever at
  // navnet inneholder cellens tekst, men fiksturen er `K-1`/`K-2`: begge
  // er under kortingsterskelen på 20 tegn, så synlig tekst og full id ER
  // samme streng og `includes` var sann uansett hva koden gjorde. En port
  // hvis fikstur ikke krysser terskelen den måler, måler ingenting.
  //
  // Derfor id-er som FAKTISK kortes, og assert (1) under er selve vernet
  // mot at porten blir blind igjen: skulle noen bytte fiksturen tilbake
  // til korte id-er, dør porten på sin egen forutsetning i stedet for å
  // stilne.
  //
  // MUTASJONEN SOM DREPER DENNE: skriv `kandidat.kandidat_id` (prosess)
  // eller `rad.kandidat_id` (rapport) rått i det tilgjengelige navnet
  // igjen — begge var koden før dette passet.
  const a = "58f17252-8a2b-4092-a420-adf5d5d430d1";
  const b = "99a17252-8a2b-4092-a420-adf5d5d430d2";  // skiller på FØRSTE tegn

  // --- Prosesstabellen -----------------------------------------------
  const data = prosess();
  data.prosesser[0].kandidater = [a, b].map((id) => ({
    kandidat_id: id, oppfylt: { drift: true }, status: "anbefalt",
    funn: [], intervjusporsmal: [] }));
  data.prosesser[0].kandidat_antall = 2;
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": data };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(
    () => hoved.querySelectorAll("tbody .handling-celle button").length === 2),
    "prosesstabellen kom aldri");

  const pNavn = [];
  for (const rad of hoved.querySelectorAll("tbody tr")) {
    const synlig = rad.querySelector("td").textContent.trim();
    const navn = rad.querySelector(".handling-celle button")
      .getAttribute("aria-label");
    pNavn.push(navn);
    // (1) Fiksturen krysser faktisk terskelen — ellers måler resten intet.
    assert.ok(synlig.endsWith("…") && synlig.length < a.length,
      `fiksturen kortes ikke (${synlig}) — porten måler da ingenting`);
    // (2) Øret hører ikke id-en øyet slapp å se.
    assert.ok(!navn.includes(a) && !navn.includes(b),
      `radhandlingen leser hele UUID-en: ${navn}`);
    // (3) …og det den hører, står faktisk på skjermen, på DENNE raden.
    assert.ok(navn.includes(synlig),
      `navnet bærer ikke referansen raden viser (${synlig}): ${navn}`);
  }
  // (4) To rader, to navn: kortformen skiller dem fortsatt fra hverandre.
  assert.equal(new Set(pNavn).size, 2,
    `to radhandlinger deler tilgjengelig navn: ${pNavn.join(" / ")}`);
  // (5) Hele id-en er ikke tapt: raden åpner panelet som bærer den, og den
  //     veien går tastatur og skjermleser — `title` gjør ingen av delene.
  hoved.querySelector("tbody .handling-celle button").click();
  assert.ok(await vent(() => document.querySelector(".dialog.skuff")),
    "detaljpanelet åpnet ikke");
  const panel = document.querySelector(".dialog.skuff");
  assert.ok(panel.textContent.includes(a) || panel.textContent.includes(b),
    "hele id-en finnes ingen steder utenom `title`");

  // --- Rapportens rangeringstabell ------------------------------------
  KALL = [];
  SVAR = rapportSvarMed(a, b);
  const hoved2 = nyHoved();
  visRekruttering(hoved2, ctx());
  assert.ok(await vent(() => {
    const tb = rapporttabellen(hoved2);
    return tb && tb.querySelectorAll("tbody tr details").length === 2;
  }), "rangeringstabellen kom aldri");

  const rTabell = rapporttabellen(hoved2);
  const rNavn = [];
  for (const rad of rTabell.querySelectorAll(
    "tbody tr:not(.rekrut-detaljrad)")) {
    const synlig = rad.querySelector("th[scope=row]").textContent.trim();
    const navn = rad.nextElementSibling.querySelector("details > summary")
      .getAttribute("aria-label");
    rNavn.push(navn);
    assert.ok(synlig.endsWith("…") && synlig.length < a.length,
      `fiksturen kortes ikke (${synlig}) — porten måler da ingenting`);
    assert.ok(!navn.includes(a) && !navn.includes(b),
      `«Vis funn» leser hele UUID-en: ${navn}`);
    assert.ok(navn.includes(synlig),
      `navnet bærer ikke referansen raden viser (${synlig}): ${navn}`);
  }
  assert.equal(new Set(rNavn).size, 2,
    `to «Vis funn» deler tilgjengelig navn: ${rNavn.join(" / ")}`);
  // Og rapporten har sin egen vei til hele id-en: `<details>` er radens
  // panel, så id-en står i kroppen — ikke bare i `th`-ens `title`.
  const boks = rTabell.querySelector("tbody tr details");
  boks.open = true;
  boks.dispatchEvent(new (rTabell.ownerDocument.defaultView.Event)("toggle"));
  assert.ok(boks.textContent.includes(a),
    "hele id-en finnes ingen steder i rapporten utenom `title`");
});

test("Kortnavn: re-rangeringen KUNNGJØR radens referanse, ikke rå UUID",
  async () => {
  // Pass-funn (runde 5), samme defektklasse som radhandlingen og «Vis
  // funn» over: vekthandlerens `aria-live`-kunngjøring var det siste
  // stedet som limte rå `kandidat_id`. Den som flytter en skyver uten
  // mus fikk trettiseks tegn heksadesimal lest opp, mens cellen øverst i
  // tabellen — det ENESTE stedet kunngjøringen kan bekreftes — sa
  // `99a17252…`. Referansen hun hørte, sto ikke på skjermen.
  //
  // PORT 30 MÅLTE DETTE ALT — OG KUNNE LIKEVEL IKKE SE DET, av nøyaktig
  // samme grunn som port 29: fiksturen `K-1`/`K-2` ligger under
  // kortingsterskelen, så `includes("K-1")` er sann uansett hva koden
  // gjør. Derfor id-er som FAKTISK kortes, og assert (1) er vernet mot at
  // porten blir blind igjen.
  //
  // MUTASJONEN SOM DREPER DENNE: skriv `rader[0].kandidat.kandidat_id`
  // rått i `{forst}` igjen — det var koden før dette passet.
  const a = "58f17252-8a2b-4092-a420-adf5d5d430d1";
  const b = "99a17252-8a2b-4092-a420-adf5d5d430d2";  // skiller på FØRSTE tegn
  const data = prosess();
  // Vektene skiller de to: `a` bæres av drift (3), `b` av sky (2), så `a`
  // står øverst før skyveren røres — og skal MISTE plassen når sky settes
  // høyest. Kunngjøringen må da navngi `b`, ikke den den nettopp forlot.
  data.prosesser[0].kandidater = [
    { kandidat_id: a, oppfylt: { drift: true, sky: false },
      status: "anbefalt", funn: [], intervjusporsmal: [] },
    { kandidat_id: b, oppfylt: { drift: false, sky: true },
      status: "vurderes", funn: [], intervjusporsmal: [] },
  ];
  data.prosesser[0].kandidat_antall = 2;
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": data };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelectorAll("tbody tr").length === 2),
    "prosesstabellen kom aldri");
  const forst = () => hoved.querySelector("tbody tr")
    .querySelector("td, th").textContent.trim();
  assert.ok(forst().startsWith("58f17252"),
    `utgangsrekkefølgen er ikke den porten bygger på: ${forst()}`);

  // Tastaturbrukerens vei: sett verdien og fyr `input` — ingen mus.
  const range = hoved.querySelector('input[type="range"]#vekt-sky');
  range.value = "10";
  range.dispatchEvent(new window.Event("input", { bubbles: true }));

  const synlig = forst();
  const kunngjoring = hoved.querySelector('[aria-live="polite"]').textContent;
  // (1) Fiksturen krysser faktisk terskelen — ellers måler resten intet.
  assert.ok(synlig.endsWith("…") && synlig.length < a.length,
    `fiksturen kortes ikke (${synlig}) — porten måler da ingenting`);
  // (2) Tabellen re-rangerte faktisk, så kunngjøringen har noe å melde.
  assert.ok(synlig.startsWith("99a17252"),
    `vektendringen re-rangerte ikke — øverst står fortsatt ${synlig}`);
  // (3) Øret hører ikke id-en øyet slapp å se.
  assert.ok(!kunngjoring.includes(a) && !kunngjoring.includes(b),
    `kunngjøringen leser hele UUID-en: ${kunngjoring}`);
  // (4) …og det den hører, står faktisk på skjermen — øverst, der den
  //     lover at kandidaten er.
  assert.ok(kunngjoring.includes(synlig),
    `kunngjøringen bærer ikke referansen raden viser (${synlig}): ${kunngjoring}`);
});

test("Prosess: en lang kandidat-id kortes, men mistes ikke", async () => {
  // Seeden gir UUID-er, og en full UUID bryter over tre linjer på mobil —
  // kolonnen ble en vegg av heksadesimal. Hele id-en står i `title`.
  //
  // MUTASJONEN SOM DREPER DENNE: skriv `kandidat_id` rått i cellen igjen.
  const uuid = "58f17252-8a2b-4092-a420-adf5d5d430d1";
  const data = prosess();
  data.prosesser[0].kandidater = [{
    kandidat_id: uuid, oppfylt: { drift: true }, status: "anbefalt",
    funn: [], intervjusporsmal: [] }];
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": data };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("tbody tr")));
  const celle = hoved.querySelector("tbody th, tbody td");
  assert.ok(!hoved.textContent.includes(uuid),
    "hele UUID-en står i tabellen — kolonnen er en vegg på mobil");
  assert.ok(hoved.textContent.includes("58f17252"),
    "kandidaten er ikke gjenkjennelig i det hele tatt");
  const medTittel = [...hoved.querySelectorAll("[title]")]
    .some((n) => n.getAttribute("title") === uuid);
  assert.ok(medTittel, "hele id-en er borte — den skal kunne kopieres");
});

test("Prosessbytte: en feilet henting ruller valget tilbake og SIER fra",
  async () => {
  KALL = [];
  // Cursor P2 (#183): serveren har sin negative test
  // (`test_ukjent_prosess_id_er_ikke_funnet_ikke_den_nyeste` — 404, ikke
  // «ta den nyeste»), men KLIENTENS feilvei hadde ingen. Uten den kan en
  // regresjon som viser kandidatene fra prosess A mens velgeren står på B
  // passere CI — og en 404 er en LOVLIG utgang her: fristen kan ha løpt
  // ut mellom to klikk, så veien er ingen kantsak.
  //
  // MUTASJONEN SOM DREPER DENNE: fjern `velger.value = prosess.prosess_id`
  // (rollbacken, som etter Cursor P1 står ved hentingens START) eller
  // `sett(velgerFeil, …)` fra `.catch`-armen.
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", blinding_av: false, vekter: { drift: 1 },
    evaluering_status: "utfort",
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  SVAR = svar183(to);
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")));
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  assert.ok(velger, "ingen prosessvelger med flere prosesser i svaret");
  // ... og HER er prosessen borte: fristen løp ut mellom lastingen og
  // byttet, og serveren svarer det samme som på en ukjent id.
  KALL = [];
  SVAR = (sti) => (sti === "/v1/rekruttering/prosesser"
    ? { __status: 404, __kropp: { feil: "ikke_funnet" } } : undefined);
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  const feil = hoved.querySelector(".rekrut-velgerfeil");
  assert.ok(feil, "flaten har ingen egen feilregion for byttet");
  assert.equal(feil.getAttribute("role"), "alert",
    "byttefeilen kunngjøres ikke");
  assert.ok(await vent(() => feil.textContent
    === t("ui.rekruttering.prosessbytte_feilet")),
    `byttefeilen ble aldri meldt: ${JSON.stringify(feil.textContent)}`);
  assert.ok(KALL.some((k) => k.sti === "/v1/rekruttering/prosesser"),
    "byttet hentet aldri — testen måler ikke det den tror");
  // VALGET ER RULLET TILBAKE til den prosessen flaten faktisk viser.
  assert.equal(hoved.querySelector("#rekrut-prosessvelger").value, "p-1",
    "velgeren sto igjen på en prosess flaten ikke viser — brukeren leser"
    + " kandidatene til A under navnet B");
  assert.equal(hoved.querySelector("#rekrut-prosessvelger").disabled, false,
    "velgeren ble stående låst etter en feil brukeren kan prøve om igjen");
  // ... og tabellen står urørt på den prosessen som er lastet.
  const rader = [...hoved.querySelectorAll("tbody tr")]
    .map((tr) => tr.querySelector("td").textContent);
  assert.ok(!rader.includes("K-9"),
    `en feilet henting tegnet den andre prosessen likevel: ${rader}`);
  assert.deepEqual([...rader].sort(), ["K-1", "K-2"]);
});

test("Prosessbytte: 500 og nettverksfeil går samme vei som 404", async () => {
  KALL = [];
  // Cursor P2 (#183): feilveien er STATUS-AGNOSTISK med vilje — alt som
  // ikke er 401 låser opp velgeren og sier fra, for brukeren kan prøve om
  // igjen uansett hvorfor det feilet. Bare 404 var målt. En regresjon som
  // forgrener på status i `.catch` — «bare den lovlige 404-en ruller
  // tilbake» — ville latt en 500 eller et falt nett etterlate velgeren
  // låst og taus på en flate der neste handling er irreversibel, og den
  // ville passert dagens CI.
  //
  // MUTASJONEN SOM DREPER DENNE: gjør `.catch`-armen status-spesifikk
  // (f.eks. `if (!(e instanceof ApiFeil) || e.status !== 404) return;`
  // foran opplåsingen og `sett(velgerFeil, …)`).
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", blinding_av: false, vekter: { drift: 1 },
    evaluering_status: "utfort",
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  SVAR = svar183(to);
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")));
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  assert.ok(velger, "ingen prosessvelger med flere prosesser i svaret");
  const feil = hoved.querySelector(".rekrut-velgerfeil");
  assert.ok(feil, "flaten har ingen egen feilregion for byttet");
  const rader = () => [...hoved.querySelectorAll("tbody tr")]
    .map((tr) => tr.querySelector("td").textContent);
  // SERVEREN FALLER (500): ingen kode i kroppen, bare en feil.
  KALL = [];
  SVAR = (sti) => (sti === "/v1/rekruttering/prosesser" ? 500 : undefined);
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(await vent(() => feil.textContent
    === t("ui.rekruttering.prosessbytte_feilet")),
    `en 500 ble aldri meldt: ${JSON.stringify(feil.textContent)}`);
  assert.ok(KALL.some((k) => k.sti === "/v1/rekruttering/prosesser"),
    "byttet hentet aldri — testen måler ikke det den tror");
  assert.equal(hoved.querySelector("#rekrut-prosessvelger").value, "p-1",
    "en 500 etterlot velgeren på en prosess flaten ikke viser");
  assert.equal(hoved.querySelector("#rekrut-prosessvelger").disabled, false,
    "en 500 etterlot velgeren låst — byttet kan ikke prøves om igjen");
  assert.ok(!rader().includes("K-9"),
    `en 500 tegnet den andre prosessen likevel: ${rader().join(",")}`);
  // NETTET FALLER (`ApiFeil(0, "nettverk")`): `fetch` avviser, og da
  // finnes det ingen status å forgrene på i det hele tatt.
  KALL = [];
  feil.textContent = "";
  SVAR = () => Promise.reject(new Error("nettet falt"));
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(await vent(() => feil.textContent
    === t("ui.rekruttering.prosessbytte_feilet")),
    `et falt nett ble aldri meldt: ${JSON.stringify(feil.textContent)}`);
  assert.ok(KALL.some((k) => k.sti === "/v1/rekruttering/prosesser"),
    "det andre byttet hentet aldri — testen måler ikke det den tror");
  assert.equal(hoved.querySelector("#rekrut-prosessvelger").value, "p-1",
    "et falt nett etterlot velgeren på en prosess flaten ikke viser");
  assert.equal(hoved.querySelector("#rekrut-prosessvelger").disabled, false,
    "et falt nett etterlot velgeren låst");
  assert.ok(!rader().includes("K-9"),
    `et falt nett tegnet den andre prosessen likevel: ${rader().join(",")}`);
});

test("Prosessbytte: velgeren viser prosessen som VISES, ikke den som hentes",
  async () => {
  KALL = [];
  // Cursor P1 (#183): nettleseren flytter valget til B i det klikket skjer,
  // men rangeringen, listene og Signer-knappene er A helt til svaret lander
  // og `tegn` kjører. Hentingen kan henge så lenge nettet vil, og i det
  // vinduet leser brukeren kandidatene til A under navnet B — på den ene
  // flaten i huset der handlingen ikke kan angres. `disabled` stanser bare
  // NYE bytter; den sier ingenting om hva velgeren PÅSTÅR mens hentingen
  // står i lufta.
  //
  // MUTASJONEN SOM DREPER DENNE: fjern `velger.value = prosess.prosess_id`
  // fra starten av `change`-handleren (rollbacken FØR hentingen).
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", blinding_av: false, vekter: { drift: 1 },
    evaluering_status: "utfort",
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  const kart = svar183(to);
  const svarB = kart["/v1/rekruttering/prosesser?prosess_id=p-2"];
  SVAR = kart;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")));
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  assert.ok(velger, "ingen prosessvelger med flere prosesser i svaret");
  // Byttet starter, og svaret holdes igjen: HER er vinduet.
  let slipp;
  kart["/v1/rekruttering/prosesser?prosess_id=p-2"] =
    new Promise((res) => { slipp = res; });
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(await vent(() => typeof slipp === "function" && KALL.some((k) =>
    k.url === "/v1/rekruttering/prosesser?prosess_id=p-2")),
    "byttet hentet aldri — testen måler ikke det den tror");
  const rader = () => [...hoved.querySelectorAll("tbody tr")]
    .map((tr) => tr.querySelector("td").textContent);
  assert.ok(!rader().includes("K-9"),
    `tabellen viste prosess B før svaret var der: ${rader().join(",")}`);
  assert.equal(velger.value, "p-1",
    "velgeren sto på prosessen som HENTES mens tabellen fortsatt viste den"
    + " som er lastet — brukeren leser kandidatene til A under navnet B");
  // ... og den irreversible handlingen i vinduet treffer den prosessen
  // brukeren faktisk ser: signeringen går til A-s liste, den eneste som
  // står tegnet. Velger og handling navngir samme prosess.
  [...hoved.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.signer_knapp")).click();
  const dialog = document.querySelector('[role="alertdialog"]');
  assert.ok(dialog, "signering uten alertdialog");
  kart["/v1/rekruttering/lister/L-1/signer"] = { innhold_hash: HASH };
  [...dialog.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.signer_bekreft"))
    .click();
  assert.ok(await vent(() => KALL.some((k) => k.sti.endsWith("/signer"))),
    "signeringen i vinduet kom aldri fram (positiv kontroll)");
  assert.deepEqual(KALL.filter((k) => k.sti.endsWith("/signer"))
    .map((k) => k.sti), ["/v1/rekruttering/lister/L-1/signer"],
    "signeringen i byttevinduet traff en annen liste enn den som vises");
  // POSITIV KONTROLL: rollbacken stanser ikke byttet. Svaret lander, og
  // DA — og bare da — flytter valget seg.
  slipp(svarB);
  assert.ok(await vent(() => rader().includes("K-9")),
    "byttet tegnet aldri etter at svaret landet");
  assert.equal(hoved.querySelector("#rekrut-prosessvelger").value, "p-2",
    "velgeren fulgte ikke med da innholdet endelig ble prosess B");
});

test("Prosessbytte: et svar som lander etter at flaten er forlatt "
  + "tegner ingenting", async () => {
  KALL = [];
  // Cursor P2 (#183): byttet er husets tredje async-vei inn i flaten, og
  // de to andre — rapporten og evalueringslisten — vokter begge både
  // generasjon og tilkobling. Denne hadde ingen vakt: `tegn` skriver rått
  // med `sett(hoved, …)`, så et tregt svar tegnet rekrutteringen inn i
  // det `hoved` en ANNEN rute nå eier.
  //
  // MUTASJONEN SOM DREPER DENNE: fjern `if (!gjelder()) return;` fra
  // `.then`-armen på prosesshentingen.
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", blinding_av: false, vekter: { drift: 1 },
    evaluering_status: "utfort",
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  SVAR = svar183(to);
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")));
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  assert.ok(velger, "ingen prosessvelger med flere prosesser i svaret");
  // Byttet starter, og svaret holdes igjen.
  let slipp;
  SVAR = () => new Promise((res) => { slipp = res; });
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(await vent(() => typeof slipp === "function"),
    "byttet hentet aldri — testen måler ikke det den tror");
  // ... og HER forlater brukeren ruta: en annen flate overtar `hoved`.
  hoved.replaceChildren();
  slipp(to);
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(hoved.textContent, "",
    "prosessbyttet tegnet inn i en forlatt flate");
});

test("Prosessbytte: bare det SISTE byttet får tegne", async () => {
  KALL = [];
  // Samme funn, andre halvdel: to hentinger i lufta samtidig (byttet kan
  // startes på nytt så snart en tegning har bygget en fersk, ulåst
  // velger). Uten generasjon på ØKTEN vinner det TREGESTE svaret, og
  // flaten står igjen på en annen prosess enn velgeren viser.
  //
  // MUTASJONEN SOM DREPER DENNE: la `gjelder()` slutte å måle
  // `min === okt.prosessHent.nr`.
  const to = prosess();
  to.prosesser.push({
    prosess_id: "p-2", blinding_av: false, vekter: { drift: 1 },
    evaluering_status: "utfort",
    kandidater: [{ kandidat_id: "K-9", oppfylt: { drift: true },
      status: "anbefalt", funn: [], intervjusporsmal: [] }],
    lister: [],
  });
  SVAR = svar183(to);
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")));
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  // BEGGE hentingene holdes igjen. Rekkefølgen er hele poenget: det
  // ELDSTE svaret slippes FØRST, mens velgeren fortsatt står tilkoblet i
  // dokumentet — ingen tegning har rukket å rive den. Da er tilkoblingen
  // sann for begge, og generasjonen er den ENESTE porten igjen.
  const slippene = [];
  SVAR = () => new Promise((res) => { slippene.push(res); });
  velger.value = "p-2";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(await vent(() => slippene.length === 1),
    "første bytte hentet aldri");
  // Andre bytte tilbake til p-1 — startet fra den samme, fortsatt
  // tilkoblede velgeren, slik en ny tegning ville gitt den.
  velger.disabled = false;
  velger.value = "p-1";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(await vent(() => slippene.length === 2),
    "andre bytte hentet aldri");
  // Det ELDSTE svaret lander først — og skal slippes på gulvet.
  slippene[0](to);
  await new Promise((r) => setTimeout(r, 20));
  const mellom = [...hoved.querySelectorAll("tbody tr")]
    .map((tr) => tr.querySelector("td").textContent);
  assert.ok(!mellom.includes("K-9"),
    `et utdatert bytte tegnet over det nyeste valget: ${mellom.join(",")}`);
  // ... og det NYESTE tegner, så testen ikke består på en død kjede.
  slippene[1](prosess());
  assert.ok(await vent(() => [...hoved.querySelectorAll("tbody tr")]
    .some((tr) => tr.querySelector("td").textContent === "K-1")),
    "det nyeste byttet tegnet aldri");
});

test("Faner: tre paneler, aktiv fane overlever prosessbytte, innhold rives aldri (mobil-redesignet 29/8)", async () => {
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [], flere: false } };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelectorAll("[role=tab]").length === 3),
    "fanelinja kom aldri");
  const faner = [...hoved.querySelectorAll("[role=tab]")];
  assert.deepEqual(faner.map((f) => f.textContent), [
    t("ui.rekruttering.fane.evalueringer"),
    t("ui.rekruttering.fane.bestill"),
    t("ui.rekruttering.fane.profiler")]);
  // Bestillingsfanen: skjemaet finnes og verdier består over fanebytte
  // (behold-modus — panelene tømmes aldri av et bytte).
  faner[1].click();
  const antall = hoved.querySelector("#bestill-antall");
  assert.ok(antall, "bestillingsskjemaet finnes ikke i fanen");
  antall.value = "42";
  faner[0].click();
  faner[1].click();
  assert.equal(hoved.querySelector("#bestill-antall").value, "42",
    "fanebyttet rev skjemaet — remount-klassen er tilbake");
  // MUTASJONEN SOM DREPER DENNE: fjern `behold: true` fra fanevalg.
});

test("Rapportvekter: skyverne re-vekter poengene og omsorterer — åpne funn består (mobil-redesignet 29/8)", async () => {
  KALL = [];
  SVAR = { ...enkelRapportSvar() };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => rapporttabellen(hoved)), "rapporten kom aldri");
  const felt = hoved.querySelector(".rekrut-rapportvekter");
  assert.ok(felt, "vektfeltet finnes ikke i rapporten");
  const skyvere = [...felt.querySelectorAll("input[type=range]")];
  assert.ok(skyvere.length >= 1, "ingen skyvere");
  const tabell = rapporttabellen(hoved);
  const rekkefolge = () => [...tabell.querySelectorAll(
    "tbody tr:not(.rekrut-detaljrad) th[scope=row]")]
    .map((c) => c.textContent.trim());
  const foer = rekkefolge();
  // Åpne første kandidats funn — tilstanden skal overleve omsortering.
  tabell.querySelector("tbody tr.rekrut-detaljrad details")
    .setAttribute("open", "");
  // Dra ALLE skyverne til 0: alle poeng blir 0, teksten i poengcellene
  // oppdateres — og kunngjøringen melder ny rekkefølge.
  for (const sk of skyvere) {
    sk.value = "0";
    sk.dispatchEvent(new window.Event("input", { bubbles: true }));
  }
  const poeng = [...tabell.querySelectorAll(
    "tbody tr:not(.rekrut-detaljrad) td:first-of-type")]
    .map((c) => c.textContent);
  assert.ok(poeng.every((p2) => p2 === "0"),
    `alle vekter 0 skal gi 0 poeng: ${poeng}`);
  assert.equal(rekkefolge().length, foer.length,
    "omsorteringen mistet rader");
  assert.ok(tabell.querySelector("tbody tr.rekrut-detaljrad details[open]"),
    "omsorteringen rev den åpne detaljboksen");
  // MUTASJONEN SOM DREPER DENNE: fjern `omVekt`-kallet i skyverens
  // input-lytter, eller bygg radene på nytt i stedet for å flytte dem.
});

test("Handlinger: Slett går gjennom bekreftelsen, poster og oppfrisker (069)", async () => {
  KALL = [];
  let evalsvar = { evalueringer: [
    { oppdrag_id: 98, status: "utfort",
      opprettet: "2026-08-27T16:59:00+00:00", rapport_klar: true }],
  flere: false };
  SVAR = (sti) => {
    if (sti === "/v1/rekruttering/evalueringer") return evalsvar;
    if (sti === "/v1/rekruttering/evaluering/98/slett") {
      return { slett_bestilt: true };
    }
    return ({ "/v1/rekruttering/prosesser": prosess(),
      "/v1/rekruttering/stillingsprofiler": profiler(),
      "/v1/rekruttering/rapport/98": 500 })[sti] ?? 500;
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste")), "listen kom aldri");
  const slett = [...hoved.querySelectorAll(".rekrut-kortliste button")]
    .find((b) => b.textContent === t("ui.rekruttering.evalueringer.slett"));
  assert.ok(slett, "Slett-knappen mangler på klar rapport");
  slett.click();
  const dlg = hoved.ownerDocument.querySelector("dialog[open], [role=dialog]");
  assert.ok(dlg, "bekreftelsesdialogen åpnet ikke");
  // Sletting skjer FØRST ved bekreftelse — ikke ved klikket.
  assert.ok(!KALL.some((k) => k.sti.endsWith("/slett")),
    "slettingen gikk uten bekreftelse");
  evalsvar = { evalueringer: [
    { oppdrag_id: 98, status: "utfort",
      opprettet: "2026-08-27T16:59:00+00:00", rapport_klar: false,
      slettet: true }], flere: false };
  [...dlg.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.evalueringer.slett"))
    .click();
  assert.ok(await vent(() => KALL.some((k) =>
    k.sti === "/v1/rekruttering/evaluering/98/slett"
    && k.metode === "POST")), "POST-en gikk aldri");
  assert.ok(await vent(() => hoved.textContent
    .includes(t("ui.rekruttering.evalueringer.slettet"))),
  "listen ble ikke oppfrisket til basens tilstand");
  // MUTASJONEN SOM DREPER DENNE: kall slettEvaluering direkte fra
  // knappens click i stedet for fra dialogens paaPrimar.
});

test("Handlinger: Avbryt finnes på ventende, aldri på terminale — og krever scope", async () => {
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 99, status: "plukket",
        opprettet: "2026-08-29T21:12:00+00:00", rapport_klar: false },
      { oppdrag_id: 97, status: "feilet",
        opprettet: "2026-08-27T08:06:00+00:00", rapport_klar: false }],
    flere: false } };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste")), "listen kom aldri");
  const knapper = [...hoved.querySelectorAll(".rekrut-kortliste button")];
  const avbrytFor = (id) => knapper.find((b) =>
    b.textContent === t("ui.rekruttering.evalueringer.avbryt")
    && b.getAttribute("aria-label").includes(String(id)));
  assert.ok(avbrytFor(99), "Avbryt mangler på den plukkede");
  assert.ok(!avbrytFor(97), "Avbryt står på en terminal evaluering");
  // Feilet får Slett (kandidatdata kan ligge igjen), utfort-uten-anker ikke.
  assert.ok(knapper.some((b) =>
    b.textContent === t("ui.rekruttering.evalueringer.slett")
    && b.getAttribute("aria-label").includes("97")),
  "Slett mangler på den feilede");

  // Uten skrivescope: ingen handlingsknapper i det hele tatt.
  const hoved2 = nyHoved();
  visRekruttering(hoved2, { ...ctx(), scopes: ["decisions:read"] });
  assert.ok(await vent(() => hoved2.querySelector(
    "section[aria-labelledby=evaluering-tittel] .rekrut-kortliste")), "leselisten kom aldri");
  assert.ok(![...hoved2.querySelectorAll(".rekrut-kortliste button")].some((b) =>
    [t("ui.rekruttering.evalueringer.slett"),
      t("ui.rekruttering.evalueringer.avbryt")].includes(b.textContent)),
  "skriveknapper uten bestilling:opprett er en blindvei (P2-1-klassen)");
});

test("Kortlisten: pillen er tekst med fargeregel per art (port 30)", async () => {
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 99, status: "plukket",
        opprettet: "2026-08-29T21:12:00+00:00", rapport_klar: false },
      { oppdrag_id: 98, status: "utfort",
        opprettet: "2026-08-27T16:59:00+00:00", rapport_klar: true },
      { oppdrag_id: 97, status: "feilet",
        opprettet: "2026-08-27T08:06:00+00:00", rapport_klar: false }],
    flere: false } };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector(".rekrut-kortliste")),
    "kortlisten kom aldri");
  // Semantikken er en LISTE — skjermleseren får antallet og navnet.
  const liste = hoved.querySelector(".rekrut-kortliste");
  assert.equal(liste.tagName, "UL");
  assert.ok(liste.getAttribute("aria-label").length > 0);
  const piller = [...liste.querySelectorAll(".rekrut-pille")];
  assert.equal(piller.length, 3);
  for (const p of piller) {
    assert.ok(p.textContent.trim().length > 0,
      "statusen mangler som tekst — farge alene er ikke informasjon");
  }
  // …og fargen finnes faktisk (trafikklys-regelen): hver pille-art som
  // flaten kan sette har en egen regel, eller arver grunnpillens grå.
  const css = readFileSync(join(ROT,
    "platform/core/ui/static/css/komponenter.css"), "utf-8");
  assert.ok(css.includes(".rekrut-pille {"), "grunnpillen er uten flate");
  for (const art of ["venter", "klar", "feilet"]) {
    assert.ok(css.includes(`.rekrut-pille--${art} {`),
      `ingen fargeregel for arten ${art}`);
  }
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Rapporten: Slett står i rapporthodet, gjennom dialogen — og tar visningen ned", async () => {
  KALL = [];
  let evalsvar = { evalueringer: [
    { oppdrag_id: 96, status: "utfort",
      opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }],
  flere: false };
  SVAR = (sti) => ({
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": evalsvar,
    "/v1/rekruttering/rapport/96": { oppdrag_id: 96, rapport: {
      rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
      profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
      antall_soknader: 1,
      rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
        nedbrytning: { drift: 5 } }],
      kandidater: { "kandidat-01": { funn: [], intervjusporsmal: [],
        kildetekst: "[NAVN-1]" } },
      fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
    } },
    "/v1/rekruttering/evaluering/96/slett": { slett_bestilt: true },
  })[sti] ?? 500;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector(".rekrut-rapporthode")),
    "rapporten kom aldri");
  const hode = hoved.querySelector(".rekrut-rapporthode");
  const slett = [...hode.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.evalueringer.slett"));
  assert.ok(slett, "Slett mangler i rapporthodet");
  assert.ok(slett.getAttribute("aria-label").includes("96"));
  slett.click();
  const dlg = hoved.ownerDocument.querySelector("dialog[open], [role=dialog]");
  assert.ok(dlg, "bekreftelsesdialogen åpnet ikke");
  assert.ok(!KALL.some((k) => k.sti.endsWith("/slett")),
    "slettingen gikk uten bekreftelse");
  evalsvar = { evalueringer: [
    { oppdrag_id: 96, status: "utfort",
      opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: false,
      slettet: true }], flere: false };
  [...dlg.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.evalueringer.slett"))
    .click();
  assert.ok(await vent(() => KALL.some((k) =>
    k.sti === "/v1/rekruttering/evaluering/96/slett"
    && k.metode === "POST")), "POST-en gikk aldri");
  // Visningen tas NED: en bestilt sletting er kundens grense fra
  // bestillingsøyeblikket — rapportinnholdet skal ikke bli stående.
  assert.ok(await vent(() => !hoved.querySelector(".rekrut-rapporthode")),
    "rapporten ble stående etter bestilt sletting");
  assert.ok(await vent(() => hoved.textContent
    .includes(t("ui.rekruttering.evalueringer.slettet"))),
  "listen ble ikke oppfrisket til basens tilstand");

  // Uten skrivescope: rapporten vises, men uten Slett.
  const hoved2 = nyHoved();
  evalsvar = { evalueringer: [
    { oppdrag_id: 96, status: "utfort",
      opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }],
  flere: false };
  visRekruttering(hoved2, { ...ctx(), scopes: ["decisions:read"] });
  assert.ok(await vent(() => hoved2.querySelector(".rekrut-rapporthode")),
    "leserapporten kom aldri");
  assert.ok(![...hoved2.querySelectorAll(".rekrut-rapporthode button")]
    .some((b) => b.textContent === t("ui.rekruttering.evalueringer.slett")),
  "Slett uten bestilling:opprett er en blindvei (P2-1-klassen)");
});

test("Listefeil og rapport eier hver sin node — rapporten rydder aldri listevarselet (#258 P2-B)", async () => {
  KALL = [];
  const rapportFor = (id) => ({ oppdrag_id: id, rapport: {
    rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
    profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
    antall_soknader: 1,
    rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
      nedbrytning: { drift: 5 } }],
    kandidater: { "kandidat-01": { funn: [], intervjusporsmal: [],
      kildetekst: "[NAVN-1]" } },
    fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
  } });
  let listesvar = { evalueringer: [
    { oppdrag_id: 96, status: "utfort",
      opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true },
    { oppdrag_id: 97, status: "utfort",
      opprettet: "2026-08-27T01:00:00+00:00", rapport_klar: true }],
  flere: false };
  SVAR = (sti) => ({
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": listesvar,
    "/v1/rekruttering/rapport/96": rapportFor(96),
    "/v1/rekruttering/rapport/97": rapportFor(97),
  })[sti] ?? 500;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  const seksjon = () => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  assert.ok(await vent(() => seksjon()
    && seksjon().querySelector(".rekrut-rapporthode")), "rapporten kom aldri");
  // Listen feiler …
  listesvar = 500;
  [...seksjon().querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.evalueringer.oppdater"))
    .click();
  assert.ok(await vent(() => seksjon().querySelector(".rekrut-listeutfall")
    .textContent.includes(t("ui.rekruttering.evalueringer.handlingfeil"))),
  "listefeilen ble aldri meldt");
  // … og en VELLYKKET rapporttegning etterpå rydder IKKE varselet:
  // listen er fortsatt utdatert, og det skal fortsatt stå der.
  // 96, ikke 97: auto-åpningen valgte alt den ferskeste (97), og et
  // klikk på samme rapport er et cache-treff uten ny tegning
  // (CodeRabbit). 96 tvinger en EKTE `rHent.tegn`-runde.
  [...seksjon().querySelectorAll(".rekrut-kortliste button")]
    .filter((b) => b.textContent === t("ui.rekruttering.evalueringer.vis"))[0]
    .click();
  assert.ok(await vent(() => KALL.some((k) =>
    k.sti === "/v1/rekruttering/rapport/96")), "rapport 96 ble aldri hentet");
  assert.ok(await vent(() => seksjon().textContent.includes("kandidat-01")),
    "rapport 96 rendret aldri");
  assert.ok(seksjon().querySelector(".rekrut-listeutfall").textContent
    .includes(t("ui.rekruttering.evalueringer.handlingfeil")),
  "rapporttegningen ryddet bort varselet om den utdaterte listen");
});

test("Fokus rives ikke tilbake fra en bruker som flyttet seg under et tregt svar (#258 P2-D)", async () => {
  KALL = [];
  const rad = (id) => ({ oppdrag_id: id, status: "opprettet",
    opprettet: "2026-08-27T02:00:00+00:00", rapport_klar: false });
  let slipp; let treg = null;
  SVAR = (sti) => {
    if (sti === "/v1/rekruttering/evalueringer" && treg) return treg;
    return ({ "/v1/rekruttering/prosesser": prosess(),
      "/v1/rekruttering/stillingsprofiler": profiler(),
      "/v1/rekruttering/evalueringer": {
        evalueringer: [rad(200)], flere: false } })[sti] ?? 500;
  };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  const seksjon = () => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  assert.ok(await vent(() => seksjon()
    && seksjon().querySelector(".rekrut-kortliste")), "listen kom aldri");
  // Neste henting er TREG.
  treg = new Promise((res) => { slipp = res; });
  const oppdater = seksjon().querySelector(".eval-oppdater");
  oppdater.click();
  // Brukeren flytter seg UT av seksjonen mens svaret er i lufta.
  const fane = hoved.querySelector(".faner-liste button")
    || hoved.querySelector("button");
  fane.focus();
  slipp({ evalueringer: [rad(200), rad(201)], flere: false });
  assert.ok(await vent(() => seksjon().textContent.includes("201")),
    "den trege oppfriskningen tegnet aldri");
  assert.equal(hoved.ownerDocument.activeElement, fane,
    "fokus ble revet tilbake til listen fra brukerens eget mål");
  // Kunngjøringen gikk likevel — brukeren fikk beskjed uten fokustyveri.
  assert.ok([...hoved.ownerDocument.querySelectorAll("[aria-live=polite]")]
    .some((n) => n.textContent.length > 0),
  "flyttet fokus skal ikke koste annonseringen");
});

test("En observert ugyldig rapport ryddes av den oppfriskede listen (#258 P2-E)", async () => {
  KALL = [];
  let listesvar = { evalueringer: [
    { oppdrag_id: 96, status: "utfort",
      opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }],
  flere: false };
  SVAR = (sti) => ({
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": listesvar,
    "/v1/rekruttering/rapport/96": { oppdrag_id: 96, rapport: {
      rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
      profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
      antall_soknader: 1,
      rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
        nedbrytning: { drift: 5 } }],
      kandidater: { "kandidat-01": { funn: [], intervjusporsmal: [],
        kildetekst: "[NAVN-1]" } },
      fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
    } },
  })[sti] ?? 500;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  const seksjon = () => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  assert.ok(await vent(() => seksjon()
    && seksjon().querySelector(".rekrut-rapporthode")), "rapporten kom aldri");
  // Fristen passerte: den oppfriskede listen OBSERVERER 96 som slettet.
  listesvar = { evalueringer: [
    { oppdrag_id: 96, status: "utfort",
      opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: false,
      slettet: true }], flere: false };
  seksjon().querySelector(".eval-oppdater").click();
  assert.ok(await vent(() => !seksjon().querySelector(".rekrut-rapporthode")),
    "listen sier slettet mens full kandidatrapport står under (#258 P2-E)");
  assert.ok(!seksjon().textContent.includes("kandidat-01"),
    "kandidatpayloaden ble stående etter at basen dømte den borte");
});

test("Kaskadeporten: kandidatoverskriften OMBREKKER faktisk — målt i beregnet stil, ikke regex (sak 251 valg C)", async () => {
  // Rotårsaken bak sak 251: `th { white-space: nowrap }` i komponenter.css
  // slo `.rekrut-kandidat { overflow-wrap: anywhere }` i base.css, og
  // regex-porten som grep'et ÉN fil så det aldri — den gikk grønn mens
  // layouten sto ødelagt. Denne porten laster BEGGE stilarkene i
  // dokumentet og spør den ekte kaskaden (K4: ekte koder, aldri en
  // etterligning): hva ER white-space/overflow-wrap på noden som rendres?
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }],
    flere: false },
    "/v1/rekruttering/rapport/96": { oppdrag_id: 96, rapport: {
      rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
      profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
      antall_soknader: 1,
      rangering: [{ kandidat_id: "e".repeat(64), poeng: 5,
        nedbrytning: { drift: 5 } }],
      kandidater: { ["e".repeat(64)]: { funn: [], intervjusporsmal: [],
        kildetekst: "[NAVN-1]" } },
      fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
    } } };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("th.rekrut-kandidat")),
    "rapportens radoverskrift kom aldri");
  const dok = hoved.ownerDocument;
  const stil = dok.createElement("style");
  stil.textContent = ["base.css", "komponenter.css"]
    .map((navn) => readFileSync(join(ROT,
      "platform/core/ui/static/css", navn), "utf-8"))
    .join("\n");
  dok.head.append(stil);
  try {
    const vindu = dok.defaultView;
    const th = hoved.querySelector("th.rekrut-kandidat");
    const stilTh = vindu.getComputedStyle(th);
    assert.equal(stilTh.whiteSpace, "normal",
      "th-nowrap vinner over klassen igjen — sak 251-regresjonen");
    assert.equal(stilTh.overflowWrap, "anywhere",
      "radoverskriften kan ikke bryte en 64-tegns kandidat-id");
    // …og prosesstabellens kandidatcelle måles i SAMME kaskade (P3-2:
    // dens gamle port sjekket bare at klassen fantes på noden).
    const span = hoved.querySelector("span.rekrut-kandidat")
      || hoved.querySelector(".rekrut-kandidat:not(th)");
    assert.ok(span, "prosesstabellens kandidatcelle finnes ikke — da "
      + "måler porten ingenting (CodeRabbit: aldri en vakuøs arm)");
    assert.equal(vindu.getComputedStyle(span).overflowWrap, "anywhere",
      "prosesscellens ombrekking forsvant fra kaskaden");
  } finally {
    stil.remove();
  }
});


test("Slett står på hvert terminalt løp — og vektene er lukket som standard (eiers funn 30/8 ×2)", async () => {
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 98, status: "utfort",
        opprettet: "2026-08-27T16:59:00+00:00", rapport_klar: true,
        har_anker: true },
      { oppdrag_id: 97, status: "feilet",
        opprettet: "2026-08-27T08:06:00+00:00", rapport_klar: false,
        har_anker: false },
      { oppdrag_id: 94, status: "utfort",
        opprettet: "2026-08-24T14:43:00+00:00", rapport_klar: false,
        har_anker: true },
      { oppdrag_id: 99, status: "plukket",
        opprettet: "2026-08-29T21:12:00+00:00", rapport_klar: false,
        har_anker: true }],
    flere: false },
    "/v1/rekruttering/rapport/98": { oppdrag_id: 98, rapport: {
      rapporttype: "rekruttering.evaluering.rapport", versjon: 1,
      profil: { profil_id: "p-1", versjon: 2, navn: "Driftskonsulent" },
      antall_soknader: 1,
      rangering: [{ kandidat_id: "kandidat-01", poeng: 5,
        nedbrytning: { drift: 5 } }],
      kandidater: { "kandidat-01": { funn: [], intervjusporsmal: [],
        kildetekst: "[NAVN-1]" } },
      fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
    } } };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector(".rekrut-kortliste")),
    "listen kom aldri");
  const slettFor = (id) => [...hoved.querySelectorAll(".rekrut-kortliste button")]
    .find((b) => b.textContent === t("ui.rekruttering.evalueringer.slett")
      && b.getAttribute("aria-label").includes(String(id)));
  assert.ok(slettFor(98), "Slett mangler der kandidatdata finnes");
  // SLETT BETYR AT RADEN FORSVINNER (071, eiers funn 30/8): også en
  // feilet evaluering UTEN anker skal kunne slettes — serveren fjerner
  // raden fra listen og sier ærlig at ingen data var lagret.
  assert.ok(slettFor(97),
    "Slett mangler på den feilede — raden kan aldri ryddes bort");
  assert.ok(slettFor(94),
    "Slett mangler på den utilgjengelige (utfort uten rapport) —"
    + " eiers oppdrag 94");
  // Et AKTIVT løp har Avbryt som sin vei — aldri Slett.
  assert.ok(!slettFor(99),
    "Slett står på et aktivt løp — veien dit er Avbryt");
  // Rapportens vekter er sammenleggbare og LUKKET som standard: listen
  // er produktet, og fire skyveknapper skal ikke dytte den under bretten.
  assert.ok(await vent(() => hoved.querySelector(".rekrut-vekterfold")),
    "vekterfolden kom aldri");
  const fold = hoved.querySelector(".rekrut-vekterfold");
  assert.ok(!fold.open, "vektene står åpne og tar skjermen på mobil");
  assert.equal(fold.querySelector("summary").textContent,
    t("ui.rekruttering.vekter_tittel"));
  assert.ok(fold.querySelector(".rekrut-rapportvekter input[type=range]"),
    "skyveknappene forsvant fra folden — re-vektingen er borte");
});

test("Dypdykket auto-viser aldri et ferdig løp — og har egen Slett (eiers funn 30/8 ×3)", async () => {
  KALL = [];
  const idx = (valgt) => ({
    valgt_prosess_id: valgt,
    prosesser: [{ prosess_id: "p-9", oppdrag_id: 98,
      opprettet: "2026-08-27T16:59:00+00:00",
      evaluering_status: "utfort", kandidat_antall: 2,
      ...(valgt === "p-9" ? {
        blinding_av: false, vekter: { drift: 3 }, vekter_kilde: "profil",
        kandidater: [
          { kandidat_id: "K-1", oppfylt: { drift: true }, funn: [] },
          { kandidat_id: "K-2", oppfylt: { drift: false }, funn: [] }],
        lister: [] } : {}) }],
  });
  // Objektform: funksjonsformen får bare den strippede stien, og
  // prosessbyttet trenger den eksakte URL-nøkkelen.
  const svarkart = {
    "/v1/rekruttering/prosesser": idx(null),
    "/v1/rekruttering/prosesser?prosess_id=p-9": idx("p-9"),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [], flere: false },
    "/v1/rekruttering/evaluering/98/slett": { slett_bestilt: true },
  };
  SVAR = svarkart;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  // Uten aktivt løp: velger med plassholder, infotekst, INTET dypdykk.
  assert.ok(await vent(() => hoved.textContent
    .includes(t("ui.rekruttering.ingen_aktiv_prosess"))),
  "flaten sa aldri at ingen prosess er valgt");
  assert.ok(!hoved.querySelector(".rekrut-tabell"),
    "et ferdig løp auto-rendret dypdykket — gamle rapporter stabler seg");
  const velger = hoved.querySelector("#rekrut-prosessvelger");
  assert.ok(velger, "velgeren mangler — brukeren kan ikke åpne noe");
  assert.equal(velger.value, "", "velgeren later som et valg er gjort");
  // Brukeren velger den ferdige eksplisitt → dypdykket kommer, MED Slett.
  velger.value = "p-9";
  velger.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.ok(await vent(() => hoved.querySelector(".rekrut-tabell")),
    "det valgte løpet rendret aldri");
  const slett = [...hoved.querySelectorAll(".rekrut-prosesshandling button")]
    .find((b) => b.textContent === t("ui.rekruttering.evalueringer.slett"));
  assert.ok(slett, "dypdykket mangler Slett på et terminalt løp");
  assert.ok(slett.getAttribute("aria-label").includes("98"));
  slett.click();
  const dlg = hoved.ownerDocument.querySelector("dialog[open], [role=dialog]");
  assert.ok(dlg, "bekreftelsesdialogen åpnet ikke");
  assert.ok(!KALL.some((k) => k.sti.endsWith("/slett")),
    "slettingen gikk uten bekreftelse");
  [...dlg.querySelectorAll("button")]
    .find((b) => b.textContent === t("ui.rekruttering.evalueringer.slett"))
    .click();
  assert.ok(await vent(() => KALL.some((k) =>
    k.sti === "/v1/rekruttering/evaluering/98/slett"
    && k.metode === "POST")), "POST-en gikk aldri");
  // Etterpå hentes indeksen på nytt — 069-filteret har sluppet
  // prosessen, og dypdykket står tomt igjen.
  svarkart["/v1/rekruttering/prosesser"] =
    { valgt_prosess_id: null, prosesser: [] };
  assert.ok(await vent(() => hoved.textContent
    .includes(t("ui.rekruttering.ingen_prosess"))
    || hoved.textContent.includes(t("ui.rekruttering.ingen_aktiv_prosess"))),
  "dypdykket ble stående etter slettingen");
});

test("Rapporten: vektendring flytter kandidatene og sjiktfargene følger (eiers funn 30/8)", async () => {
  KALL = [];
  // Tolv kandidater i to leire: 01–05 oppfyller drift (vekt 5), 06–12
  // oppfyller sky (vekt 1). Initialt er 01–05 topp 5 (grønn) og 06–10
  // sjikt 6–10 (svak grønn). Skrus drift til 0, snur rangeringen: 06–10
  // skal opp OG bli grønne — plassbyttet og fargen er samme mekanisme.
  const ids = Array.from({ length: 12 }, (_, i) =>
    `kandidat-${String(i + 1).padStart(2, "0")}`);
  const rangering = ids.map((id, i) => (i < 5
    ? { kandidat_id: id, poeng: 5, nedbrytning: { drift: 5, sky: 0 } }
    : { kandidat_id: id, poeng: 1, nedbrytning: { drift: 0, sky: 1 } }));
  SVAR = (sti) => ({
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/96": { oppdrag_id: 96, rapport: {
      rapporttype: "rekruttering.evaluering.rapport", versjon: 2,
      profil: { profil_id: "p-1", versjon: 1, navn: "Driftskonsulent",
        krav: [{ kravnavn: "drift", vekt: 5 },
               { kravnavn: "sky", vekt: 1 }] },
      antall_soknader: 12, rangering,
      kandidater: Object.fromEntries(ids.map((id) => [id, { funn: [] }])),
      fremdrift: { filer_lest: 12, filer_totalt: 12, byte_lest: 500 },
    } },
  })[sti] ?? 500;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  const seksjon = () => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  assert.ok(await vent(() => seksjon() && [...seksjon()
    .querySelectorAll(".rekrut-kortliste button")]
    .some((b) => b.textContent === t("ui.rekruttering.evalueringer.vis"))));
  [...seksjon().querySelectorAll(".rekrut-kortliste button")]
    .find((b) => b.textContent === t("ui.rekruttering.evalueringer.vis"))
    .click();
  assert.ok(await vent(() => seksjon().textContent
    .includes("Driftskonsulent")), "rapporten rendret aldri");
  const hovedrader = () => [...seksjon()
    .querySelectorAll("tbody tr")].filter((tr) => tr.querySelector("th"));
  const navn = (tr) => tr.querySelector("th").getAttribute("title");
  const klasser = () => hovedrader().map((tr) => (
    tr.classList.contains("rekrut-sjikt-topp") ? "topp"
      : tr.classList.contains("rekrut-sjikt-neste") ? "neste" : ""));
  // Initialt: 01 øverst, 5 grønne + 5 svake + 2 umerkede.
  assert.equal(navn(hovedrader()[0]), "kandidat-01");
  assert.deepEqual(klasser(), ["topp", "topp", "topp", "topp", "topp",
    "neste", "neste", "neste", "neste", "neste", "", ""]);
  // Drift til 0 — sky-leiren skal opp, og fargene skal FLYTTE med.
  const skyver = seksjon().querySelector("#rapportvekt-drift");
  skyver.value = "0";
  skyver.dispatchEvent(new (hoved.ownerDocument.defaultView.Event)(
    "input", { bubbles: true }));
  assert.equal(navn(hovedrader()[0]), "kandidat-06",
    "rangeringen snudde ikke da vekten falt");
  assert.equal(navn(hovedrader()[11]), "kandidat-05");
  assert.deepEqual(klasser(), ["topp", "topp", "topp", "topp", "topp",
    "neste", "neste", "neste", "neste", "neste", "", ""],
    "sjiktfargene fulgte ikke plassbyttet");
  // … og sjiktet er en RADENS klasse også i stilarket (fargen finnes).
  const css = readFileSync(join(ROT,
    "platform/core/ui/static/css/komponenter.css"), "utf-8");
  assert.ok(css.includes("rekrut-sjikt-topp"), "topp-sjiktet mangler CSS");
  assert.ok(css.includes("rekrut-sjikt-neste"), "6–10-sjiktet mangler CSS");
});

test("Rapporten: kandidatkortet avmaskerer på klikk (eiers bestilling 30/8)", async () => {
  KALL = [];
  SVAR = (sti) => ({
    "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/rekruttering/evalueringer": { evalueringer: [
      { oppdrag_id: 96, status: "utfort",
        opprettet: "2026-08-27T00:40:00+00:00", rapport_klar: true }] },
    "/v1/rekruttering/rapport/96": { oppdrag_id: 96, rapport: {
      rapporttype: "rekruttering.evaluering.rapport", versjon: 2,
      profil: { profil_id: "p-1", versjon: 1, navn: "Driftskonsulent",
        krav: [{ kravnavn: "drift", vekt: 3 }] },
      antall_soknader: 1,
      rangering: [{ kandidat_id: "kandidat-01", poeng: 3,
        nedbrytning: { drift: 3 } }],
      kandidater: { "kandidat-01": { funn: [] } },
      fremdrift: { filer_lest: 1, filer_totalt: 1, byte_lest: 50 },
    } },
    "/v1/rekruttering/kandidatkort/96/kandidat-01": {
      kandidat_id: "kandidat-01",
      felter: { "[NAVN-1]": "Kari Nordmann",
                "[KONTAKT-1]": "kari@eksempel.no" },
      dokumenter: ["cv.pdf"] },
  })[sti] ?? 500;
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  const seksjon = () => hoved.querySelector(
    "section[aria-labelledby=evaluering-tittel]");
  assert.ok(await vent(() => seksjon() && [...seksjon()
    .querySelectorAll(".rekrut-kortliste button")]
    .some((b) => b.textContent === t("ui.rekruttering.evalueringer.vis"))));
  [...seksjon().querySelectorAll(".rekrut-kortliste button")]
    .find((b) => b.textContent === t("ui.rekruttering.evalueringer.vis"))
    .click();
  assert.ok(await vent(() => seksjon().textContent
    .includes("Driftskonsulent")), "rapporten rendret aldri");
  // Detaljene bygges lat — kortknappen finnes først etter åpning, og
  // navnet finnes IKKE i DOM før leseren eksplisitt ber om det.
  assert.doesNotMatch(seksjon().textContent, /Kari Nordmann/);
  const boks = seksjon().querySelector("tbody details");
  boks.open = true;
  boks.dispatchEvent(new (seksjon().ownerDocument.defaultView.Event)(
    "toggle"));
  const knapp = [...seksjon().querySelectorAll(".rekrut-kandidatkort button")]
    .find((b) => b.textContent === t("ui.rekruttering.kandidatkort.vis"));
  assert.ok(knapp, "kandidatkort-knappen mangler i detaljene");
  knapp.click();
  assert.ok(await vent(() => seksjon().textContent
    .includes("Kari Nordmann")), "avmaskeringen rendret aldri");
  const tekst = seksjon().textContent;
  assert.match(tekst, /kari@eksempel\.no/);
  assert.match(tekst, /cv\.pdf/);
  // Etikettene er locale-tekster utledet av tokenet — aldri rå tokener.
  // (Målt på KORTET: flatens blinding-forklaring nevner selv [NAVN-1]
  // som eksempel, så hele seksjonen kan ikke bære negativen.)
  const kort = seksjon().querySelector(".rekrut-kandidatkort");
  assert.match(kort.textContent,
    new RegExp(t("ui.rekruttering.kandidatkort.felt.navn")));
  assert.doesNotMatch(kort.textContent, /\[NAVN-1\]/);
  assert.match(tekst,
    new RegExp(t("ui.rekruttering.kandidatkort.dokumenter")));
});
