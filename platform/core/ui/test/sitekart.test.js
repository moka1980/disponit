import test from "node:test";
import assert from "node:assert/strict";
import { byggRuter, harScope, tillatteFlater, visningFraSok }
  from "../static/js/sitekart.js";

test("harScope: leser scopes fra sesjon uten kast", () => {
  assert.equal(harScope({ scopes: ["policy:write"] }, "policy:write"), true);
  assert.equal(harScope({ scopes: [] }, "policy:write"), false);
  assert.equal(harScope({}, "policy:write"), false);
});

test("byggRuter: basisruter finnes alltid", () => {
  const ruter = byggRuter({ scopes: [] }).map((r) => r.nokkel);
  assert.deepEqual(ruter, ["oversikt", "policy", "beslutninger", "unntak"]);
});

test("byggRuter: kundeadmin og policyadmin krever policy-scope", () => {
  const ruter = byggRuter({ scopes: ["policy:activate"] }).map((r) => r.nokkel);
  assert.ok(ruter.includes("kundeadmin"));
  assert.ok(ruter.includes("policyadmin"));
  assert.ok(!ruter.includes("admin"));
});

test("byggRuter: admin krever security-scope", () => {
  const ruter = byggRuter({ scopes: ["security:read"] }).map((r) => r.nokkel);
  assert.ok(ruter.includes("admin"));
  assert.ok(!ruter.includes("kundeadmin"));
});

test("tillatteFlater: direkte hash kan ikke nå en flate uten scope", () => {
  const flater = { oversikt: () => {}, policy: () => {}, beslutninger: () => {},
    unntak: () => {}, kundeadmin: () => {}, policyadmin: () => {},
    admin: () => {} };
  const uten = tillatteFlater(byggRuter({ scopes: [] }), flater);
  assert.deepEqual(Object.keys(uten),
    ["oversikt", "policy", "beslutninger", "unntak"]);
  assert.equal(uten.admin, undefined);
  assert.equal(uten.kundeadmin, undefined);

  const med = tillatteFlater(byggRuter({ scopes: ["security:read"] }), flater);
  assert.equal(typeof med.admin, "function");
  assert.equal(med.kundeadmin, undefined);
});

test("visningFraSok: returnerer kun tilgjengelig visning", () => {
  const ruter = byggRuter({ scopes: ["policy:write", "security:read"] });
  assert.equal(visningFraSok("?visning=kundeadmin", ruter), "kundeadmin");
  assert.equal(visningFraSok("?visning=admin", ruter), "admin");
  assert.equal(visningFraSok("?visning=ukjent", ruter), null);
  assert.equal(visningFraSok("", ruter), null);
});
