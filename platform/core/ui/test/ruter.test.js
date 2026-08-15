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

test("lagRuter: stopp kobler ruteren av hashchange", () => {
  // Skallet bygges på nytt ved språkbytte, og da lages en NY ruter. Uten
  // avkobling lå den gamle igjen på `hashchange` og rendret inn i sitt eget,
  // nå løsrevne, `hoved` — ett ekstra sett API-kall per bytte, i et tre ingen
  // ser. Her: to rutere, den første stoppet, én navigasjon → kun den nye.
  window.location.hash = "#/oversikt";
  const rendret = [];
  const gammel = rigg({ oversikt: () => rendret.push("gammel") }).ruter;
  const ny = rigg({ oversikt: () => rendret.push("ny") }).ruter;
  gammel.stopp();

  window.location.hash = "#/policy";
  window.dispatchEvent(new window.HashChangeEvent("hashchange"));
  assert.deepEqual(rendret, ["ny"]);

  // Tålig å kalle to ganger, og den stoppede ruteren kan fortsatt spørres.
  assert.doesNotThrow(() => gammel.stopp());
  ny.stopp();
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
