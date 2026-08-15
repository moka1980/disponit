import test from "node:test";
import assert from "node:assert/strict";
import { MODULOVERSIKT, MODULSTATUS, modulStatus, plattformTelling }
  from "../static/js/plattformdata.js";

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
