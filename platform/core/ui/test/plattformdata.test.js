import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  MODULOVERSIKT, MODULSTATUS, TILBUD, erTilgjengelig, heroTekstNokkel,
  produksjonsmiljo, settProduksjonsmiljo,
  heroTekstNokkelFor, modulStatus, modulerFraIder, modulmerke,
  plattformTelling, tenantTelling,
} from "../static/js/plattformdata.js";

// Locale-settene leses direkte, ikke via `hjelp.js`: denne fila påstår om ren
// data og trenger ingen jsdom-rigg for å gjøre det.
const ROT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..");
const LOKALER = ["nb", "en"].map((sprak) =>
  [sprak, JSON.parse(readFileSync(join(ROT, "locales", `${sprak}.json`), "utf-8"))]);

test("modulStatus: ukjent modul er planlagt, ikke udefinert", () => {
  // Verdiene er avledet av manifestene (pinnet i test_ui_kontrakt.py): M-1 er
  // godkjent og i drift, M-2/M-37 er under utvikling, M-38 har intet manifest.
  // `i_drift` krever `driftstilstand: produksjon` i manifestet — og bare M-1
  // har det.
  assert.equal(modulStatus(1), "i_drift");
  assert.equal(modulStatus(2), "bygges");
  assert.equal(modulStatus(38), "planlagt");
  assert.equal(modulStatus(45), "planlagt");
});

test("MODULSTATUS: ingen modul lover drift uten at manifestet gjør det", () => {
  // Regelen er BINDINGEN, ikke tallet: hver `i_drift` her må ha
  // `driftstilstand: produksjon` i sitt manifest, og
  // `test_ui_kontrakt.py::test_modulstatus_folger_manifestene` håndhever den
  // retningen. M-1 står i drift fordi disponit.com kjører den.
  assert.deepEqual(
    Object.entries(MODULSTATUS).filter(([, s]) => s === "i_drift")
      .map(([id]) => Number(id)),
    [1], "andre moduler enn M-1 påstår drift — sjekk manifestene");
});

test("erTilgjengelig: løftet krever BÅDE drift og produksjonsmiljø", () => {
  // To ledd, ikke ett. M-1 kjører (i_drift), men verten står i staging-modus
  // fordi policyene som binder beslutningene fortsatt er `utkast` — da skal
  // forsiden ikke love kunden noe. Begge retninger måles, så porten ikke bare
  // beskriver dagens tilstand.
  settProduksjonsmiljo(false);
  assert.equal(erTilgjengelig(1), false, "drift alene er ikke nok");
  settProduksjonsmiljo(true);
  assert.equal(erTilgjengelig(1), true, "drift + produksjonsmiljø skal loves");
  assert.equal(erTilgjengelig(37), false, "M-37 bygges, uansett miljø");
  for (const verdi of [undefined, null, "produksjon", "produksjonn", 1, {}]) {
    settProduksjonsmiljo(verdi);
    assert.equal(produksjonsmiljo(), false,
      `${JSON.stringify(verdi)} slo på produksjonsmiljøet`);
  }
  settProduksjonsmiljo(false);
});

test("erTilgjengelig: brikka følger BEGGE aksene", () => {
  // I produksjonsmodus er brikka nøyaktig MODULSTATUS: det som kjører kan
  // loves, resten ikke. Testen setter miljøet EKSPLISITT i stedet for å arve
  // det, så den måler regelen og ikke rekkefølgen testene kjøres i.
  settProduksjonsmiljo(true);
  for (const post of TILBUD) {
    assert.equal(erTilgjengelig(post.id), modulStatus(post.id) === "i_drift",
      `M-${post.id} lover noe annet enn MODULSTATUS bærer`);
  }
  assert.equal(erTilgjengelig(45), false, "ukjent modul er planlagt");
  // …og i staging-modus loves ingenting, uansett hva som kjører.
  settProduksjonsmiljo(false);
  for (const post of TILBUD) {
    assert.equal(erTilgjengelig(post.id), false,
      `M-${post.id} loves uten produksjonsmiljø`);
  }
});

test("heroTekstNokkelFor: delvis utrulling har sin egen tekst", () => {
  // Presensformen («agenten håndterer utbetalinger, purringer, oppfølging og
  // rapportering») lover ALLE fire områdene. Den er derfor bare sann når
  // hvert tilbudspunkt er i drift. Ett `some()` slo den på ved den FØRSTE
  // modulen som gikk i drift, mens tre brikker fortsatt sa «Kommer» — samme
  // selvmotsigelse som med null i drift, bare flyttet (Codex P2).
  assert.equal(heroTekstNokkelFor(0, 4), "site.hero.tekst_bygges");
  assert.equal(heroTekstNokkelFor(1, 4), "site.hero.tekst_delvis",
    "én av fire i drift lover ikke alle fire");
  assert.equal(heroTekstNokkelFor(3, 4), "site.hero.tekst_delvis",
    "tre av fire i drift lover fortsatt ikke alle fire");
  assert.equal(heroTekstNokkelFor(4, 4), "site.hero.tekst");
  // Alle tre nøklene må finnes på BEGGE språk — ellers ville en delvis
  // utrulling rendret selve nøkkelen som brødtekst på forsiden, og bare på
  // det språket ingen husket å fylle.
  for (const [sprak, sett] of LOKALER) {
    for (const nokkel of ["site.hero.tekst", "site.hero.tekst_bygges",
                          "site.hero.tekst_delvis"]) {
      assert.ok(sett[nokkel], `${nokkel} mangler i locales/${sprak}.json`);
    }
  }
});

test("heroTekstNokkel: hovedløftet følger brikkene, ikke redaktøren", () => {
  // Nøkkelen utledes av den samme MODULSTATUS som brikkene: to kilder ville
  // drevet fra hverandre neste gang en modul skifter tilstand.
  const iDrift = TILBUD.filter((post) => erTilgjengelig(post.id)).length;
  assert.equal(heroTekstNokkel(), heroTekstNokkelFor(iDrift, TILBUD.length));
  // Slik plattformen faktisk står: null moduler i drift, altså bygge-formen.
  // I staging-modus lover forsiden ingenting, uansett hva som kjører.
  assert.equal(iDrift, 0,
    "et tilbudspunkt loves — men verten står ikke i produksjonsmodus");
  assert.equal(heroTekstNokkel(), "site.hero.tekst_bygges");
});

test("MODULOVERSIKT: kortstatus utledes av MODULSTATUS, ikke duplisert", () => {
  for (const mod of MODULOVERSIKT) {
    assert.equal(mod.status, modulStatus(mod.id),
      `M-${mod.id} har annen status på kortet enn i MODULSTATUS`);
  }
});

test("plattformTelling: KPI-ene teller det kortene viser", () => {
  const telling = plattformTelling();
  const kort = (s) => MODULOVERSIKT.filter((m) => m.status === s).length;
  // Alt i MODULSTATUS er beskrevet i oversikten, så tallene skal møtes.
  assert.equal(Object.keys(MODULSTATUS).length, MODULOVERSIKT.length);
  assert.equal(telling.iDrift, kort("i_drift"));
  assert.equal(telling.klargjort, kort("klargjort"));
  assert.equal(telling.bygges, kort("bygges"));
  assert.equal(telling.underArbeid, telling.klargjort + telling.bygges);
  assert.equal(telling.iDrift + telling.klargjort + telling.bygges
    + telling.planlagt, telling.totalt);
});

test("modulerFraIder: kundens tildeling, ikke plattformkatalogen", () => {
  // Tildelingen kommer fra den autentiserte veien; her måles bare
  // oppslaget fra ID-er til modulkort.
  assert.deepEqual(modulerFraIder([1, 2]).map((m) => m.id), [1, 2]);
  assert.deepEqual(modulerFraIder([1, 2, 37, 38]).map((m) => m.id),
    [1, 2, 37, 38]);
  assert.deepEqual(modulerFraIder([]).map((m) => m.id), []);
  // «Vet ikke» (null) er ikke det samme som «ingen moduler» ([]): en flate
  // uten tildeling skal si at den ikke vet, ikke vise hele katalogen.
  assert.equal(modulerFraIder(undefined), null);
  assert.equal(modulerFraIder(null), null);
  assert.equal(modulerFraIder("1,2"), null);
});

test("tenantTelling: teller kundens moduler, ikke plattformens", () => {
  // En tenant med M-1 (klargjort) og M-2 (bygges): ingen i drift, to under
  // arbeid — og resten av katalogen er planlagt for kunden. Tildelingen er
  // ID-er fra den autentiserte veien, ikke et oppslag i klientpakken.
  // Tellingen følger MODULSTATUS (hvor koden kjører), ikke kundeløftet på
  // forsiden: M-1 er i drift, M-2 bygges.
  const telling = tenantTelling(modulerFraIder([1, 2]));
  assert.equal(telling.iDrift, 1);
  assert.equal(telling.underArbeid, 1);
  assert.equal(telling.planlagt, telling.totalt - 2);
  const ukjent = tenantTelling([]);
  assert.equal(ukjent.iDrift, 0);
  assert.equal(ukjent.underArbeid, 0);
});

test("modulmerke: tenantens modul-ID-er vises som M-<id>", () => {
  assert.deepEqual([1, 2].map(modulmerke), ["M-1", "M-2"]);
});
