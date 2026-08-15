// Ruteren får et flatekart som ALLEREDE er scope-filtrert (`tillatteFlater`).
// Reserveruten må derfor komme fra det kartet — ikke fra en antakelse om at
// hver økt har `oversikt`.
import test from "node:test";
import assert from "node:assert/strict";
import "./hjelp.js";
import { lagRuter } from "../static/js/ruter.js";

function rigg(flater) {
  const hoved = document.createElement("main");
  const aktive = [];
  const ruter = lagRuter(hoved, {}, flater, (n) => aktive.push(n));
  return { hoved, aktive, ruter };
}

test("lagRuter: ugyldig hash faller til en rute økten FAKTISK har", () => {
  // En økt uten `decisions:read` har ingen `oversikt` i kartet. Med hardkodet
  // reserve slo dette rett i `flater["oversikt"](...)` — et kall på
  // `undefined` — og appen ble stående blank i stedet for å vise en flate
  // økten har.
  window.location.hash = "#/oversikt";
  const rendret = [];
  const { aktive, ruter } = rigg({
    kundeadmin: () => rendret.push("kundeadmin"),
    admin: () => rendret.push("admin"),
  });
  assert.doesNotThrow(() => ruter.naviger());
  assert.equal(ruter.gjeldende(), "kundeadmin");
  assert.deepEqual(rendret, ["kundeadmin"]);
  assert.deepEqual(aktive, ["kundeadmin"]);
});

test("lagRuter: en vanlig kundeøkt havner fortsatt på oversikt", () => {
  // Rekkefølgen i kartet er `byggRuter` sin, så reserven for en økt som HAR
  // `oversikt` er uendret.
  window.location.hash = "#/finnesikke";
  const rendret = [];
  const { ruter } = rigg({
    oversikt: () => rendret.push("oversikt"),
    policy: () => rendret.push("policy"),
  });
  ruter.naviger();
  assert.deepEqual(rendret, ["oversikt"]);
});

test("lagRuter: gyldig hash rendrer sin egen flate", () => {
  window.location.hash = "#/policy";
  const rendret = [];
  const { ruter } = rigg({
    oversikt: () => rendret.push("oversikt"),
    policy: () => rendret.push("policy"),
  });
  ruter.naviger();
  assert.deepEqual(rendret, ["policy"]);
});

test("lagRuter: en økt uten én eneste rute kaster ikke", () => {
  // `scopes_for_roller` er default-deny: et medlemskap med bare ukjente roller
  // gir tom scope-mengde, altså tomt flatekart. Da finnes det ingen flate å
  // rendre, og ruteren skal la skallet stå — ikke kaste under bootstrap.
  window.location.hash = "#/oversikt";
  const { aktive, ruter } = rigg({});
  assert.equal(ruter.gjeldende(), null);
  assert.doesNotThrow(() => ruter.naviger());
  assert.deepEqual(aktive, []);
});

// Språkbyttet bygger skallet — og dermed ruteren — på nytt. Uten `stopp` ble
// den gamle lytteren stående på `window` og rendret sin flate inn i et
// frakoblet `<main>`: usynlig for brukeren, men med ekte API-kall, og ett
// ekstra sett per bytte. Testene under låser at bare den LEVENDE ruteren
// svarer på en `hashchange`.
// Hendelsen sendes eksplisitt i stedet for å vente på jsdom' egen kø: køen
// tømmes først ved neste `await`, og da lander også hash-endringene fra
// testene over. Et synkront `dispatchEvent` måler nøyaktig én navigasjon.
function navigerTil(hash) {
  window.location.hash = hash;
  window.dispatchEvent(new Event("hashchange"));
}

test("lagRuter: en stoppet ruter rendrer ikke lenger på hashchange", () => {
  window.location.hash = "#/oversikt";
  const rendret = [];
  const gammel = rigg({
    oversikt: () => rendret.push("gammel:oversikt"),
    policy: () => rendret.push("gammel:policy"),
  });
  const ny = rigg({
    oversikt: () => rendret.push("ny:oversikt"),
    policy: () => rendret.push("ny:policy"),
  });
  gammel.ruter.stopp();

  navigerTil("#/policy");

  assert.deepEqual(rendret, ["ny:policy"]);
  ny.ruter.stopp();
});

test("lagRuter: stopp er idempotent", () => {
  window.location.hash = "#/oversikt";
  const rendret = [];
  const { ruter } = rigg({ policy: () => rendret.push("policy") });
  ruter.stopp();
  assert.doesNotThrow(() => ruter.stopp());

  navigerTil("#/policy");

  assert.deepEqual(rendret, []);
});
