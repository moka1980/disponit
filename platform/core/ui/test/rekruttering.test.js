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
  KALL.push({ sti, metode: opts.method || "GET", kropp,
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
  SVAR = { "/v1/rekruttering/prosesser": to };
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
  // …og den dagen #162 gir tittelen, vinner den uten at flaten røres.
  to.prosesser[1].navn = "Sykepleier vest";
  KALL = [];
  const medNavn = nyHoved();
  visRekruttering(medNavn, ctx());
  assert.ok(await vent(() => medNavn.querySelector("table")));
  assert.ok([...medNavn.querySelector("#rekrut-prosessvelger").options]
    .some((o) => o.textContent === "Sykepleier vest"),
    "navnet fra serveren tapte mot tidsstempelet");
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
  const melding = () => hoved.querySelector('[role="alert"]').textContent;
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
  const bytt = (id) => {
    const velger = hoved.querySelector("#rekrut-prosessvelger");
    velger.value = id;
    velger.dispatchEvent(new window.Event("change", { bubbles: true }));
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
  bytt("p-2");
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
  bytt("p-1");
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
  assert.ok(panel.textContent.includes("Fortell om tidslinjen."),
    "intervjuspørsmålet mangler");
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
           "/v1/rekruttering/stillingsprofiler": profiler() };
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")),
    "flaten kom aldri");
  return hoved;
}

test("Profiler: uten bestilling:opprett finnes ingen skriveknapper (P2-1)", async () => {
  KALL = [];
  SVAR = { "/v1/rekruttering/prosesser": prosess(),
           "/v1/rekruttering/stillingsprofiler": profiler() };
  const hoved = nyHoved();
  const leser = ctx();
  leser.scopes = ["decisions:read"];
  visRekruttering(hoved, leser);
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
  const seksjon = hoved.querySelector("section[aria-labelledby=profil-tittel]");
  assert.ok(seksjon, "profilseksjonen mangler — lesing skal stå åpen");
  assert.match(seksjon.textContent, /Driftskonsulent/);
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

test("Bestilling: hele kjeden — reserver, opplast, bestill (SP-2)", async () => {
  KALL = [];
  const basis = { "/v1/rekruttering/prosesser": prosess(),
    "/v1/rekruttering/stillingsprofiler": profiler(),
    "/v1/inndata/reserver": { reservasjon_jti: "j-1",
                              inndata_ref: "inndata:u-1" },
    "/v1/inndata/opplast/j-1": {} };
  let bestillingssvar = { beslutning: "tillat", oppdrag_id: 42 };
  SVAR = (sti) => sti === "/v1/bestilling" ? bestillingssvar : basis[sti];
  const hoved = nyHoved();
  visRekruttering(hoved, ctx());
  assert.ok(await vent(() => hoved.querySelector("table")), "flaten kom aldri");
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
  // Andre forsøk: samme operasjon — samme nøkkel.
  SVAR = { "/v1/rekruttering/prosesser": prosess(),
           "/v1/rekruttering/stillingsprofiler": profiler() };
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
