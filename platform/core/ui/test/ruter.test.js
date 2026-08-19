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

// Codex P2: en flate hadde ingen måte å be om et BESTEMT objekt på. Varselet
// «policyutkast u-1 venter på deg» kunne bare sende eier til policyadmin-lista
// og la henne lete fram igjen det utkastet varselet nettopp navnga.
test("lagRuter: hash-en bærer et mål, og flaten får det", () => {
  window.location.hash = "#/policyadmin/u-1";
  const sett = [];
  const { ruter, aktive } = rigg({
    oversikt: (h, c, mal) => sett.push(["oversikt", mal]),
    policyadmin: (h, c, mal) => sett.push(["policyadmin", mal]),
  });
  ruter.naviger();
  assert.deepEqual(sett, [["policyadmin", "u-1"]]);
  // Ruten er fortsatt ruten: menyen markerer `policyadmin`, ikke «u-1».
  assert.deepEqual(aktive, ["policyadmin"]);
  assert.equal(ruter.gjeldende(), "policyadmin");
});

test("lagRuter: uten mål får flaten null, og ukjent rute faller til reserven",
  () => {
    const sett = [];
    const flater = {
      oversikt: (h, c, mal) => sett.push(["oversikt", mal]),
      policyadmin: (h, c, mal) => sett.push(["policyadmin", mal]),
    };

    window.location.hash = "#/policyadmin";
    rigg(flater).ruter.naviger();
    assert.deepEqual(sett.pop(), ["policyadmin", null]);

    // En id med tegn som må escapes overlever turen gjennom adressefeltet.
    window.location.hash = `#/policyadmin/${encodeURIComponent("u/1 2")}`;
    rigg(flater).ruter.naviger();
    assert.deepEqual(sett.pop(), ["policyadmin", "u/1 2"]);

    // En ødelagt escape-sekvens skal ikke rive ned navigasjonen: det rå leddet
    // bæres videre, og flaten svarer det den ville svart på en ukjent id.
    window.location.hash = "#/policyadmin/%E0%A4A";
    assert.doesNotThrow(() => rigg(flater).ruter.naviger());
    assert.equal(sett.pop()[0], "policyadmin");

    // Et mål på en rute økten IKKE har er ingen bakvei inn: reserven tegnes,
    // og målet følger ikke med til en flate det ikke var ment for.
    window.location.hash = "#/finnesikke/u-1";
    rigg(flater).ruter.naviger();
    assert.deepEqual(sett.pop(), ["oversikt", null]);
  });

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

// Codex P2: `#/plan` var en ekte rute til planflaten helt til periodisk
// kontroll ble en FANE under WCAG kontroll (eier 19/8). Bokmerket og den
// delte lenken forsvinner ikke med ruten, og en ukjent rute tegner reserven —
// som regel Oversikt. Den som hadde bokmerket den periodiske kontrollen
// landet altså på en annen flate, og fant ingen «Plan» i menyen å ta seg
// videre med heller: den oppføringen er nettopp den som ble borte.
test("lagRuter: den arvede planadressen lander på planfanen, og rettes", () => {
  window.location.hash = "#/plan";
  const sett = [];
  const { ruter } = rigg({
    oversikt: (h, c, mal) => sett.push(["oversikt", mal]),
    wcagkontroll: (h, c, mal) => sett.push(["wcagkontroll", mal]),
  });
  ruter.naviger();
  // Målet er FANEN, ikke bare flaten: uten det hadde lenken landet på
  // startfanen (Domener, eller Rapporter for en leseøkt).
  assert.deepEqual(sett, [["wcagkontroll", "plan"]]);
  // Én tegning, ikke to: omskrivingen er `replaceState`, som ikke fyrer
  // `hashchange`. Og adressen er den kanoniske etterpå, så et fanebytte
  // videre blir skrevet av samleflaten — den rører bare sin egen rute.
  assert.equal(window.location.hash, "#/wcagkontroll/plan");
  assert.equal(ruter.gjeldende(), "wcagkontroll");
  ruter.stopp();
});

test("lagRuter: den arvede adressen bærer målet sitt videre", () => {
  // Planvarslene pekte på `#/plan/<plan_id>` før flyttingen. Målet vinner
  // over aliasets fanenøkkel: samleflaten leser en nøkkel den ikke kjenner
  // som en plan-id, og åpner planfanen på den.
  const id = "3f7e-1";
  window.location.hash = "#/plan/" + id;
  const sett = [];
  const { ruter } = rigg({
    oversikt: (h, c, mal) => sett.push(["oversikt", mal]),
    wcagkontroll: (h, c, mal) => sett.push(["wcagkontroll", mal]),
  });
  ruter.naviger();
  assert.deepEqual(sett, [["wcagkontroll", id]]);
  assert.equal(window.location.hash, "#/wcagkontroll/" + id);
  ruter.stopp();
});

test("lagRuter: en arvet adresse er ingen vei rundt scope-filteret", () => {
  // `flater` er allerede scope-filtrert. Har økten ikke `wcagkontroll`, skal
  // aliaset falle til reserven som enhver annen ukjent rute — og adressen
  // ikke skrives om til en flate økten ikke har.
  window.location.hash = "#/plan";
  const sett = [];
  const { ruter } = rigg({
    kundeadmin: (h, c, mal) => sett.push(["kundeadmin", mal]),
  });
  ruter.naviger();
  assert.deepEqual(sett, [["kundeadmin", null]]);
  assert.equal(window.location.hash, "#/plan");
  ruter.stopp();
});

test("lagRuter: en gyldig adresse skrives ikke om", () => {
  // Omskrivingen gjelder KUN de arvede rutene. En vanlig rute med mål skal
  // stå som den er — ellers ville hver navigasjon rørt historikken.
  window.location.hash = "#/policyadmin/u-1";
  const { ruter } = rigg({ oversikt: () => {}, policyadmin: () => {} });
  ruter.naviger();
  assert.equal(window.location.hash, "#/policyadmin/u-1");
  ruter.stopp();
});
