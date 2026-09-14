// Plattformeierens firmaadministrasjon på adminflaten (199).
//
// Eier: «Jeg er … eier … og skal også ha mulighet til å opprette nye firmaer,
// redigere og slette.» Alle tre verbene måles her.
//
// DET VIKTIGSTE ER HVA SOM IKKE VISES. Fullmakten er en rad i `plattformeier`,
// ikke et scope — menyen kan derfor ikke avgjøre dette, og flaten må spørre
// serveren. Svarer den nei, skal seksjonen ikke finnes på skjermen i det hele
// tatt: ikke en tom tabell, ikke en «du mangler tilgang». En seksjon som
// forteller at den finnes, er en opplysning den som ikke skal se den ikke
// trenger.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visAdmin } from "../static/js/flater/admin.js";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HER = dirname(fileURLToPath(import.meta.url));
const CSS = readdirSync(join(HER, "..", "static", "css"))
  .filter((f) => f.endsWith(".css"))
  .map((f) => readFileSync(join(HER, "..", "static", "css", f), "utf-8"))
  .join("\n");

settI18nForTest(NB, "nb");

const FIRMAER = [
  { tenant: "akme", navn: "Akme AS", orgnummer: null, status: "prove",
    prove_utloper: "2026-10-13", stengt: null,
    opprettet: "2026-09-13T10:00:00+00:00", endret: "2026-09-13T10:00:00+00:00" },
  { tenant: "bolig-nord", navn: "Bolig Nord AS", orgnummer: null,
    status: "aktiv", prove_utloper: "2026-09-01", stengt: null,
    opprettet: "2026-08-01T10:00:00+00:00", endret: "2026-08-01T10:00:00+00:00" },
];

let EIER, POSTET, FEIL, HENTET;

globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  if (!opts || !opts.method) HENTET.push(sti);
  if (opts && opts.method) {
    POSTET.push({ sti, body: opts.body ? JSON.parse(opts.body) : null });
    if (FEIL) {
      return { ok: false, status: FEIL.status,
        json: async () => ({ feil: FEIL.kode }) };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  }
  if (sti === "/v1/plattform/meg") {
    return { ok: true, status: 200, json: async () => ({ eier: EIER }) };
  }
  if (sti === "/v1/plattform/firmaer") {
    return { ok: true, status: 200, json: async () => ({ firmaer: FIRMAER }) };
  }
  return { ok: false, status: 404, json: async () => ({ feil: "x" }) };
};

function nyHoved() {
  const brett = nyttBrett();
  const m = document.createElement("main");
  m.id = "hovedinnhold"; m.tabIndex = -1;
  brett.append(m);
  return m;
}

const ctx = () => ({ tenant: "disponit", scopes: ["policy:read"],
  roller: ["admin"], tenanter: [], paaUautorisert: () => {} });

const vent = async (p, n = 200) => {
  for (let i = 0; i < n; i++) {
    if (p()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return p();
};

const finnKnapp = (h, tekst) => [...h.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === tekst);

async function tegn(eier) {
  EIER = eier; POSTET = []; FEIL = null; HENTET = [];
  const h = nyHoved();
  visAdmin(h, ctx());
  if (eier) {
    await vent(() => h.textContent.includes("Akme AS"));
  } else {
    // VENT PÅ NOE EKTE. Første utkast sto `vent(() => POSTET.length >= 0 &&
    // true, 20)` — et uttrykk som er sant med én gang. Testen assertet altså
    // FØR hentingen var ferdig, og var grønn uansett: mutasjonen «dropp
    // eier-sjekken» falt IKKE. En venting som ikke venter er verre enn ingen,
    // fordi den ser ut som et vern.
    //
    // Nå ventes det på at spørsmålet FAKTISK er stilt og besvart, og så
    // slippes mikrooppgavekøen igjennom noen ganger så en eventuell
    // oppfølgingshenting rekker å skje.
    await vent(() => HENTET.includes("/v1/plattform/meg"));
    for (let i = 0; i < 30; i++) await new Promise((r) => setTimeout(r, 0));
  }
  return h;
}

// ---------------------------------------------------------------------------
// Fullmakten
// ---------------------------------------------------------------------------

test("Plattform: en som IKKE er eier ser ingen seksjon i det hele tatt",
  async () => {
    const h = await tegn(false);
    // DEN STERKESTE ASSERTEN FØRST: lista ble aldri HENTET. Et fravær i
    // DOM-en kan skyldes at vi målte for tidlig; et kall som ikke finnes i
    // loggen kan ikke skyldes det.
    assert.ok(!HENTET.includes("/v1/plattform/firmaer"),
      `flaten hentet firmalista for en uten fullmakt: ${HENTET.join(", ")}`);
    // Ikke en tom tabell, ikke en feilmelding — ingenting.
    assert.ok(!h.textContent.includes(t("ui.plattform.firmaer")),
      "seksjonen røpet at den finnes");
    assert.equal(finnKnapp(h, t("ui.plattform.opprett")), undefined,
      "opprett-knappen var tegnet for en uten fullmakt");
  });

test("Plattform: eieren ser alle firmaer, på tvers av tenanter", async () => {
  const h = await tegn(true);
  assert.ok(h.textContent.includes("Akme AS"), "mangler Akme");
  assert.ok(h.textContent.includes("Bolig Nord AS"), "mangler Bolig Nord");
  assert.ok(h.textContent.includes(t("ui.plattform.status.prove")),
    "statusen vises ikke oversatt");
});

// ---------------------------------------------------------------------------
// De tre verbene
// ---------------------------------------------------------------------------

test("Plattform: opprett sender navn, kortnavn og prøveperiode", async () => {
  const h = await tegn(true);
  h.querySelector("#pf-navn").value = "Nytt AS";
  h.querySelector("#pf-tenant").value = "nytt-as";
  h.querySelector("#pf-prove").value = "30";
  h.querySelector("form.skjema").dispatchEvent(
    new window.Event("submit", { cancelable: true }));
  await vent(() => POSTET.some((p) => p.sti === "/v1/plattform/firmaer"));
  const kall = POSTET.find((p) => p.sti === "/v1/plattform/firmaer");
  assert.equal(kall.body.navn, "Nytt AS");
  assert.equal(kall.body.tenant, "nytt-as");
  assert.equal(kall.body.prove_dogn, 30);
});

test("Plattform: «Endre navn» lagrer mot riktig firma", async () => {
  const h = await tegn(true);
  finnKnapp(h, t("ui.plattform.endre")).dispatchEvent(new window.Event("click"));
  await vent(() => h.querySelector('input[type="text"][value="Akme AS"]'));
  const inn = h.querySelector('input[type="text"]');
  inn.value = "Akme Holding AS";
  finnKnapp(h, t("ui.plattform.lagre")).dispatchEvent(new window.Event("click"));
  await vent(() => POSTET.some((p) => p.sti.endsWith("/oppdater")));
  const kall = POSTET.find((p) => p.sti.endsWith("/oppdater"));
  assert.equal(kall.sti, "/v1/plattform/firmaer/akme/oppdater",
    "endringen traff feil firma");
  assert.equal(kall.body.navn, "Akme Holding AS");
});

test("Plattform: «Steng» er sletting — og går mot statusruten", async () => {
  const h = await tegn(true);
  finnKnapp(h, t("ui.plattform.steng")).dispatchEvent(new window.Event("click"));
  await vent(() => POSTET.some((p) => p.sti.endsWith("/status")));
  const kall = POSTET.find((p) => p.sti.endsWith("/status"));
  assert.equal(kall.body.status, "stengt",
    "«slett» skal være `stengt`, ikke en DELETE");
});

test("Plattform: bare LOVLIGE overganger får en knapp", async () => {
  // 190s statusmaskin: `aktiv` kan bare bli `stengt`. En «Aktiver»-knapp på
  // en som alt er aktiv ville gitt en 409 brukeren ikke kunne forstå.
  const h = await tegn(true);
  const rader = [...h.querySelectorAll("tbody tr")];
  const aktivrad = rader.find((r) => r.textContent.includes("Bolig Nord"));
  const knapper = [...aktivrad.querySelectorAll("button")]
    .map((b) => b.textContent.trim());
  assert.ok(knapper.includes(t("ui.plattform.steng")), "mangler «Steng»");
  assert.ok(!knapper.includes(t("ui.plattform.aktiver")),
    "tilbød «Aktiver» på et firma som allerede er aktivt");
});

// ---------------------------------------------------------------------------
// Feilveiene og tilgjengelighet
// ---------------------------------------------------------------------------

test("Plattform: et opptatt kortnavn sier nøyaktig det", async () => {
  const h = await tegn(true);
  FEIL = { status: 409, kode: "firma_finnes" };
  h.querySelector("#pf-navn").value = "Duplikat AS";
  h.querySelector("#pf-tenant").value = "akme";
  h.querySelector("form.skjema").dispatchEvent(
    new window.Event("submit", { cancelable: true }));
  await vent(() => h.textContent.includes(t("ui.plattform.finnes")));
  assert.ok(h.textContent.includes(t("ui.plattform.finnes")),
    "brukeren fikk ikke vite at kortnavnet var opptatt");
});

test("Plattform: prøvefristen vises bare i prøveperioden", async () => {
  // En gammel dato på et AKTIVT firma ser ut som en frist som gjelder.
  const h = await tegn(true);
  const rader = [...h.querySelectorAll("tbody tr")];
  const aktivrad = rader.find((r) => r.textContent.includes("Bolig Nord"));
  assert.ok(!aktivrad.textContent.includes("2026-09-01"),
    "viste en utløpt prøvefrist på et aktivt firma");
  const proverad = rader.find((r) => r.textContent.includes("Akme"));
  assert.ok(proverad.textContent.includes("2026-10-13"),
    "prøvefristen mangler der den faktisk gjelder");
});

test("Plattform: kvitteringen OVERLEVER oppfriskningen", async () => {
  // PORTEN SOM MANGLET. `last()` kaller `sett(boks, …)` og erstatter alt
  // innholdet — meldte vi først og oppfrisket etterpå, ble kvitteringen
  // vasket bort i samme åndedrag, og brukeren så aldri at noe var lagret.
  // Feilen levde fordi jeg bare testet FEILveien, der `last()` ikke kalles.
  //
  // MUTASJON SOM FELLER: bytt tilbake til `meld(...); last();`.
  const h = await tegn(true);
  finnKnapp(h, t("ui.plattform.steng")).dispatchEvent(new window.Event("click"));
  await vent(() => h.textContent.includes(t("ui.plattform.endret")));
  assert.ok(h.textContent.includes(t("ui.plattform.endret")),
    "kvitteringen ble vasket bort av oppfriskningen");
});

test("Plattform: hele radens handlinger låses mens kallet er ute", async () => {
  // Med bare den trykkede knappen låst kunne «Aktiver» og «Steng» ligge ute
  // samtidig, og da avgjør nettverket hvilken status firmaet ender på.
  //
  // MUTASJON SOM FELLER: lås bare `knapp` i stedet for hele raden.
  const h = await tegn(true);
  const rader = [...h.querySelectorAll("tbody tr")];
  const proverad = rader.find((r) => r.textContent.includes("Akme"));
  const knapper = [...proverad.querySelectorAll("button")];
  assert.ok(knapper.length >= 2, "raden har for få knapper til å måle dette");
  knapper.find((b) => b.textContent.trim() === t("ui.plattform.steng"))
    .dispatchEvent(new window.Event("click"));
  // Umiddelbart etter klikket, FØR svaret: alle skal være låst.
  assert.ok(knapper.every((b) => b.disabled),
    "en handling sto åpen mens en annen var ute");
});

test("Plattform: klassene som skal GJØRE noe finnes faktisk i CSS", async () => {
  // FEILEN EIER SÅ PÅ SKJERMEN, og som ingen test kunne se.
  //
  // Jeg skrev `visuelt-skjult` og `tabellramme`. Ingen av dem finnes i noen
  // CSS-fil — huset heter `sr-only` og `tablewrap`, og BEGGE sto allerede i
  // samme fil jeg redigerte. Utslaget: bildeteksten som skulle vært skjult
  // sto synlig og gjentok overskriften rett over, og tabellen mistet sin
  // vannrette rulling på smal skjerm.
  //
  // jsdom laster ikke CSS, så ingen vanlig flatetest kan se dette. Men
  // NAVNET kan måles: en klasse som skal skjule eller rulle, må finnes et
  // sted som faktisk skjuler eller ruller. En ren beholderklasse uten stil
  // er derimot helt legitim — 18 slike finnes i kodebasen — så porten går
  // bare på de to som bærer en OPPFØRSEL.
  //
  // MUTASJON SOM FELLER: bytt tilbake til `visuelt-skjult`/`tabellramme`.
  const h = await tegn(true);

  const caption = h.querySelector("table caption");
  assert.ok(caption, "tabellen mangler bildetekst");
  assert.ok(caption.classList.contains("sr-only"),
    `bildeteksten er ikke skjult for øyet: class="${caption.className}"`);
  assert.ok(/\.sr-only\b/.test(CSS),
    "`sr-only` finnes ikke i CSS — da skjuler den ingenting");

  const omslag = h.querySelector("table").parentElement;
  assert.ok(omslag.classList.contains("tablewrap"),
    `tabellen mangler rulleomslag: class="${omslag.className}"`);
  assert.ok(/\.tablewrap\b/.test(CSS),
    "`tablewrap` finnes ikke i CSS — da ruller ingenting");
});

test("Plattform: seksjonen er axe-ren", async () => {
  const h = await tegn(true);
  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});
