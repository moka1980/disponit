import test from "node:test";
import assert from "node:assert/strict";
import {
  MODULOVERSIKT, MODULSTATUS, TENANTOVERSIKT, modulStatus, modulerForTenant,
  modulmerke, plattformTelling, tenantTelling,
} from "../static/js/plattformdata.js";

test("modulStatus: ukjent modul er planlagt, ikke udefinert", () => {
  assert.equal(modulStatus(1), "i_drift");
  assert.equal(modulStatus(38), "bygges");
  assert.equal(modulStatus(45), "planlagt");
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
  assert.equal(telling.bygges, kort("bygges"));
  assert.equal(telling.iDrift + telling.bygges + telling.planlagt,
    telling.totalt);
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
  const telling = tenantTelling(modulerForTenant("bjorkli"));
  assert.equal(telling.iDrift, 2);
  assert.equal(telling.bygges, 0);
  assert.equal(telling.planlagt, telling.totalt - 2);
  const ukjent = tenantTelling([]);
  assert.equal(ukjent.iDrift, 0);
  assert.equal(ukjent.bygges, 0);
});

test("modulmerke: tenantens modul-ID-er vises som M-<id>", () => {
  assert.deepEqual(TENANTOVERSIKT[1].moduler.map(modulmerke), ["M-1", "M-2"]);
});
