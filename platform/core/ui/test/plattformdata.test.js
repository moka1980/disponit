import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  MODULOVERSIKT, MODULSTATUS, TILBUD, erTilgjengelig, erTilgjengeligFor,
  heroTekstNokkel, produksjonsmiljo, settProduksjonsmiljo,
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
  // godkjent og kjører — men på STAGING, så flaten sier `klargjort`.
  // `i_drift` krever `driftstilstand: produksjon`, altså en utrulling hos
  // kunder, og den finnes ikke ennå. M-37 er under utvikling, M-38 har intet
  // manifest.
  //
  // M-2 sto kort `klargjort` mens manifestet var aktivert i akseptrunden på
  // #89, og er tilbake på `bygges`: tre av seks sjekklistepunkter er målt,
  // men ikke bundet gjennom evidensporten (Codex P1, runde 2). Denne testen
  // og `MODULSTATUS` er to sider av samme påstand og må flytte seg sammen —
  // ellers er den ene bare en kopi av den andre uten portverdi.
  assert.equal(modulStatus(1), "klargjort");
  assert.equal(modulStatus(2), "bygges");
  assert.equal(modulStatus(38), "planlagt");
  assert.equal(modulStatus(45), "planlagt");
});

test("MODULSTATUS: ingen modul lover drift uten at manifestet gjør det", () => {
  // Regelen er BINDINGEN, ikke tallet: hver `i_drift` her må ha
  // `driftstilstand: produksjon` i sitt manifest, og
  // `test_ui_kontrakt.py::test_modulstatus_folger_manifestene` håndhever den
  // retningen. Ingen har det i dag — M-1 kjører på staging-serveren, og
  // `docs/DEPLOY.md` reserverer produksjon for en egen VPS med kundedata.
  assert.deepEqual(
    Object.entries(MODULSTATUS).filter(([, s]) => s === "i_drift")
      .map(([id]) => Number(id)),
    [], "en modul påstår drift hos kunder — sjekk manifestets driftstilstand");
});

test("erTilgjengelig: løftet krever BÅDE drift og produksjonsmiljø", () => {
  // To ledd, ikke ett — og regelen måles på REGELEN, ikke på dagens tilstand:
  // hadde testen pinnet «M-1 er i drift», ville den målt manifestet på nytt og
  // ryket hver gang en modul flyttet seg, uten at porten var svekket.
  for (const status of ["i_drift", "klargjort", "bygges", "planlagt"]) {
    assert.equal(erTilgjengeligFor(status, true), status === "i_drift",
      `${status} + produksjonsmiljø lover feil`);
    assert.equal(erTilgjengeligFor(status, false), false,
      `${status} loves uten produksjonsmiljø`);
  }
  // Slik plattformen faktisk står: M-1 kjører på staging, altså `klargjort`.
  // Da faller løftet på det FØRSTE leddet alene, uansett hva verten sier.
  settProduksjonsmiljo(true);
  assert.equal(erTilgjengelig(1), false, "M-1 er klargjort, ikke i drift");
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
  // «Alle områdene under er i drift» lover ALLE fire punktene, og er derfor
  // bare sann når hvert tilbudspunkt er i drift. Ett `some()` slo den på ved
  // den FØRSTE modulen som gikk i drift, mens tre brikker fortsatt sa
  // «Kommer» — samme selvmotsigelse som med null i drift, bare flyttet
  // (Codex P2).
  assert.equal(heroTekstNokkelFor(0, 4), "site.hero.tekst_bygges");
  assert.equal(heroTekstNokkelFor(1, 4), "site.hero.tekst_delvis",
    "én av fire i drift lover ikke alle fire");
  assert.equal(heroTekstNokkelFor(3, 4), "site.hero.tekst_delvis",
    "tre av fire i drift lover fortsatt ikke alle fire");
  assert.equal(heroTekstNokkelFor(4, 4), "site.hero.tekst");
  // Alle nøklene må finnes på BEGGE språk — ellers ville en delvis
  // utrulling rendret selve nøkkelen som brødtekst på forsiden, og bare på
  // det språket ingen husket å fylle. `site.hero.tilbud` står i samme liste
  // fordi den rendres i ALLE tre tilstandene, ikke bare én av dem.
  for (const [sprak, sett] of LOKALER) {
    for (const nokkel of ["site.hero.tilbud", "site.hero.tekst",
                          "site.hero.tekst_bygges", "site.hero.tekst_delvis"]) {
      assert.ok(sett[nokkel], `${nokkel} mangler i locales/${sprak}.json`);
    }
  }
});

test("site.hero.tilbud: tilbudsteksten overlever hver utrullingstilstand", () => {
  // Tilbudsbeskrivelsen lå inni `tekst_bygges`, altså i den ENE nøkkelen som
  // velges når ingenting er i drift. Første modul i drift ville byttet den
  // ut med `tekst_delvis` og tatt hele beskrivelsen med seg (Codex P2).
  //
  // Testen bevoktes av innholdet, ikke av lengden: står et av områdene som
  // BARE tilbudsteksten nevner i en utrullingsavhengig nøkkel, har noen
  // flyttet beskrivelsen tilbake dit den forsvinner igjen.
  const utrullingsavhengige = ["site.hero.tekst", "site.hero.tekst_bygges",
                               "site.hero.tekst_delvis"];
  // Sentinelene er per språk: tilbudsteksten (claude.ai, INNHOLD 19/8) er
  // norsk prosa uten anglisismene «compliance»/«HR», så vaktene er to
  // områdeord som BARE tilbudsbeskrivelsen nevner.
  const sentineler = { nb: ["fakturering", "avstemming"],
                       en: ["invoicing", "reconciliation"] };
  for (const [sprak, sett] of LOKALER) {
    const tilbud = sett["site.hero.tilbud"];
    for (const omrade of sentineler[sprak]) {
      assert.ok(tilbud.includes(omrade),
        `site.hero.tilbud nevner ikke ${omrade} i locales/${sprak}.json`);
      for (const nokkel of utrullingsavhengige) {
        assert.ok(!sett[nokkel].includes(omrade),
          `${nokkel} bærer tilbudsteksten (${omrade}) i locales/${sprak}.json` +
          " — den forsvinner da i de andre utrullingstilstandene");
      }
    }
  }
});

test("heroTekstNokkel: hovedløftet følger brikkene, ikke redaktøren", () => {
  // Nøkkelen utledes av den samme MODULSTATUS som brikkene: to kilder ville
  // drevet fra hverandre neste gang en modul skifter tilstand.
  const iDrift = TILBUD.filter((post) => erTilgjengelig(post.id)).length;
  assert.equal(heroTekstNokkel(), heroTekstNokkelFor(iDrift, TILBUD.length));
  // Slik plattformen faktisk står: ingen tilbudspunkter loves, altså
  // bygge-formen. Begge leddene holder tallet på null hver for seg — ingen
  // modul er rullet ut hos kunder, og verten står ikke i produksjonsmodus.
  assert.equal(iDrift, 0,
    "et tilbudspunkt loves — sjekk driftstilstand og DISPONIT_MILJO");
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
  // Tellingen følger MODULSTATUS: `iDrift` er utrulling hos kunder, og M-1
  // kjører på staging-serveren, ikke der.
  const telling = tenantTelling(modulerFraIder([1, 2]));
  assert.equal(telling.iDrift, 0);
  assert.equal(telling.underArbeid, 2);
  assert.equal(telling.planlagt, telling.totalt - 2);
  const ukjent = tenantTelling([]);
  assert.equal(ukjent.iDrift, 0);
  assert.equal(ukjent.underArbeid, 0);
});

test("modulmerke: tenantens modul-ID-er vises som M-<id>", () => {
  assert.deepEqual([1, 2].map(modulmerke), ["M-1", "M-2"]);
});
