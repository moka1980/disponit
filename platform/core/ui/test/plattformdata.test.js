import test from "node:test";
import assert from "node:assert/strict";
import {
  MODULOVERSIKT, MODULSTATUS, TENANTOVERSIKT, modulStatus, modulerForTenant,
  modulmerke, plattformTelling, tenantTelling,
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

test("modulerForTenant: kundens tildeling, ikke plattformkatalogen", () => {
  assert.deepEqual(modulerForTenant("Bjorkli").map((m) => m.id), [1, 2]);
  assert.deepEqual(modulerForTenant("granmo").map((m) => m.id), [1, 2, 37, 38]);
  // Ukjent tenant er «vet ikke» (null), ikke «hele katalogen».
  assert.equal(modulerForTenant("acme"), null);
  assert.equal(modulerForTenant(""), null);
  assert.equal(modulerForTenant(undefined), null);
});

test("tenantTelling: teller kundens moduler, ikke plattformens", () => {
  // Bjørkli har M-1 (klargjort) og M-2 (bygges): ingen i drift, to under
  // arbeid — og resten av katalogen er planlagt for kunden.
  const telling = tenantTelling(modulerForTenant("bjorkli"));
  assert.equal(telling.iDrift, 0);
  assert.equal(telling.underArbeid, 2);
  assert.equal(telling.planlagt, telling.totalt - 2);
  const ukjent = tenantTelling([]);
  assert.equal(ukjent.iDrift, 0);
  assert.equal(ukjent.underArbeid, 0);
});

test("modulmerke: tenantens modul-ID-er vises som M-<id>", () => {
  assert.deepEqual(TENANTOVERSIKT[1].moduler.map(modulmerke), ["M-1", "M-2"]);
});
