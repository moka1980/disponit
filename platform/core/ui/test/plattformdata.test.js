import test from "node:test";
import assert from "node:assert/strict";
import {
  MODULOVERSIKT, MODULSTATUS, TILBUD, erTilgjengelig, heroTekstNokkel,
  modulStatus, modulerFraIder, modulmerke, plattformTelling, tenantTelling,
} from "../static/js/plattformdata.js";

test("modulStatus: ukjent modul er planlagt, ikke udefinert", () => {
  // Verdiene er avledet av manifestene (pinnet i test_ui_kontrakt.py): M-1 er
  // godkjent men ikke i drift, M-2/M-37 er under utvikling, M-38 har intet
  // manifest. Ingen av dem er `i_drift` — det ordet krever
  // `driftstilstand: produksjon`.
  assert.equal(modulStatus(1), "klargjort");
  assert.equal(modulStatus(2), "bygges");
  assert.equal(modulStatus(38), "planlagt");
  assert.equal(modulStatus(45), "planlagt");
});

test("MODULSTATUS: ingen modul lover drift uten at manifestet gjør det", () => {
  assert.equal(Object.values(MODULSTATUS).filter((s) => s === "i_drift").length,
    0, "en modul står i_drift — da må manifestet ha driftstilstand: produksjon");
});

test("erTilgjengelig: forsiden lover bare det som faktisk kjører", () => {
  // «Tilgjengelig» er et løfte til en besøkende om at hen kan ta modulen i
  // bruk NÅ. Bare `i_drift` bærer det løftet: `klargjort` er godkjent uten
  // drift (M-1 har `ikke_i_drift` og ingen API-enhet i manifestet), og
  // `bygges`/`planlagt` er enda lenger unna. Alle fire sier «Kommer».
  for (const post of TILBUD) {
    assert.equal(erTilgjengelig(post.id), modulStatus(post.id) === "i_drift",
      `M-${post.id} lover noe annet enn MODULSTATUS bærer`);
  }
  assert.equal(erTilgjengelig(1), false, "M-1 er klargjort, ikke i drift");
  assert.equal(erTilgjengelig(45), false, "ukjent modul er planlagt");
});

test("heroTekstNokkel: hovedløftet følger brikkene, ikke redaktøren", () => {
  // Presensformen («agenten håndterer …») er et løfte om noe som virker NÅ.
  // Står hvert tilbudspunkt i «Kommer», er det løftet usant, og forsiden
  // motsier seg selv innenfor én skjermhøyde (Codex P2). Nøkkelen utledes
  // derfor av den samme MODULSTATUS som brikkene.
  const noeIDrift = TILBUD.some((post) => erTilgjengelig(post.id));
  assert.equal(heroTekstNokkel(),
    noeIDrift ? "site.hero.tekst" : "site.hero.tekst_bygges");
  // Slik plattformen faktisk står: null moduler i drift, altså bygge-formen.
  assert.equal(noeIDrift, false, "et tilbudspunkt er i drift — sjekk MODULSTATUS");
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
