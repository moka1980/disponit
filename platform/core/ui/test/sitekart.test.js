import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { byggRuter, harScope, hashForDypLenke, synligeSakstyper,
  tillatteFlater, visningFraSok } from "../static/js/sitekart.js";

const HER = dirname(fileURLToPath(import.meta.url));
const locale = (navn) => JSON.parse(readFileSync(
  join(HER, "..", "..", "..", "..", "locales", `${navn}.json`), "utf-8"));

test("harScope: leser scopes fra sesjon uten kast", () => {
  assert.equal(harScope({ scopes: ["policy:write"] }, "policy:write"), true);
  assert.equal(harScope({ scopes: [] }, "policy:write"), false);
  assert.equal(harScope({}, "policy:write"), false);
});

test("synligeSakstyper: køene avledes av `security:read`, ikke av flaten", () => {
  // Uten scopet finnes nøyaktig én kø — og det er serverens standard, så
  // inngangen er uendret for alle som har flaten i dag.
  assert.deepEqual(synligeSakstyper({ scopes: ["exceptions:read"] }),
    ["normal"]);
  assert.deepEqual(synligeSakstyper({}), ["normal"]);
  // Med scopet er alle tre nåbare — og `normal` står først, så
  // standardvisningen ikke flytter seg for den som får scopet.
  const alle = synligeSakstyper({ scopes: ["exceptions:read",
                                           "security:read"] });
  assert.deepEqual(alle, ["normal", "sikkerhet", "drift"]);
  assert.equal(alle[0], "normal");
});

test("synligeSakstyper speiler serverens `synlige_sakstyper`", () => {
  // Codex P2: to avledninger av samme køregel er én for mye. Nøkkeltallene
  // teller over serverens liste, unntaksflaten når klientens — glir de fra
  // hverandre, teller kortet rader flaten ikke har noen vei til, og «hele
  // listen» blir igjen et løfte som ikke holdes. Her krysses de mekanisk.
  const py = readFileSync(
    join(HER, "..", "..", "api", "app.py"), "utf8");
  const m = py.match(/^SAKSTYPER = \(([^)]+)\)/m);
  assert.ok(m, "fant ikke `SAKSTYPER` i api/app.py");
  const serverAlle = m[1].match(/"([^"]+)"/g).map((s) => s.slice(1, -1));
  assert.deepEqual(synligeSakstyper({ scopes: ["security:read"] }), serverAlle,
    "klientens køliste har glidd fra serverens SAKSTYPER");
  // …og at det er `security:read` som skiller, ikke noe annet scope: uten
  // det svarer serveren `scope_mangler` på alt utenom `normal`.
  const smal = py.match(
    /def synlige_sakstyper[\s\S]*?if "([^"]+)" in scopes:\s*\n\s*return SAKSTYPER\s*\n\s*return \(("[^"]+"),?\)/);
  assert.ok(smal, "fant ikke regelen i `synlige_sakstyper`");
  assert.equal(smal[1], "security:read");
  assert.deepEqual(synligeSakstyper({ scopes: [] }),
    [smal[2].slice(1, -1)]);
});

test("byggRuter: hver rute krever scopet API-et bak flaten krever", () => {
  // Uten scopes finnes bare kundeflaten: den leser det økten allerede har fått
  // og kaller ikke noe endepunkt. Alle de andre ville lovet en flate serveren
  // svarer 403 på.
  assert.deepEqual(byggRuter({ scopes: [] }).map((r) => r.nokkel),
    ["kundeadmin"]);
  const alle = byggRuter({ scopes: ["decisions:read", "exceptions:read",
    "policy:read"] }).map((r) => r.nokkel);
  // 038/039: WCAG-kontroll er ÉN rute, og den står bak flatens SVAKESTE del
  // (Codex P2). `leser` her har `decisions:read` og skal fortsatt nå
  // rapportene sine — `GET /v1/rapport/{id}` krever bare det. Med
  // mutasjonsscopet på hele oppføringen inndro sammenslåingen tilgang de
  // hadde før den.
  // M-57: rekruttering står bak samme svakeste ledd som WCAG-flaten —
  // lesingen av kandidatlisten krever bare `decisions:read`; blinding og
  // signering gates INNE i flaten.
  assert.deepEqual(alle,
    ["oversikt", "nokkeltall", "policy", "beslutninger", "unntak",
      "kundeadmin", "wcagkontroll", "rekruttering"]);
  const medBestilling = byggRuter({ scopes: ["decisions:read",
    "bestilling:opprett"] }).map((r) => r.nokkel);
  assert.ok(medBestilling.includes("wcagkontroll"));
  assert.ok(!medBestilling.includes("bestilling") &&
    !medBestilling.includes("rapport"), "de gamle enkeltrutene er borte");
  // ...og uten `decisions:read` finnes ruten ikke: da er det ingen fane
  // igjen på flaten.
  assert.ok(!byggRuter({ scopes: ["policy:read"] })
    .map((r) => r.nokkel).includes("wcagkontroll"));
});

test("byggRuter: godkjenner får ikke policyruten den ikke kan lese", () => {
  // Kanonisk `godkjenner` i `autorisasjon.py`: unntakskøen, men INGEN
  // `policy:read`. Sto `policy` i basisrutene, tilbød både menyen og
  // kundeflaten en lesevisning bak et endepunkt som svarer 403.
  const ruter = byggRuter({ scopes: ["decisions:read", "exceptions:read",
    "exceptions:approve", "exceptions:reject", "exceptions:escalate"] })
    .map((r) => r.nokkel);
  assert.ok(!ruter.includes("policy"), "policyrute uten policy:read");
  assert.ok(ruter.includes("unntak"));
  assert.ok(ruter.includes("kundeadmin"));
});

test("byggRuter: policyforvalter får ikke unntaksruten den ikke kan lese", () => {
  // Speilbildet: `policyforvalter` har ikke `exceptions:read`.
  const ruter = byggRuter({ scopes: ["decisions:read", "policy:read",
    "policy:write", "policy:activate"] }).map((r) => r.nokkel);
  assert.ok(!ruter.includes("unntak"), "unntaksrute uten exceptions:read");
  assert.ok(ruter.includes("policy"));
  assert.ok(ruter.includes("policyadmin"));
});

test("byggRuter: vanlig kundeøkt når kundeflaten, ikke policyadmin", () => {
  // `leser` har bare lesescopes, og kundeinnloggingen sender den til
  // `/?visning=kundeadmin`: nektes ruten, lander knappen stille på `oversikt`.
  const ruter = byggRuter({ scopes: ["decisions:read", "exceptions:read",
    "policy:read"] }).map((r) => r.nokkel);
  assert.ok(ruter.includes("kundeadmin"));
  assert.ok(!ruter.includes("policyadmin"));
  assert.ok(!ruter.includes("admin"));
});

test("byggRuter: policyadmin krever policy-forvaltningsscope", () => {
  const ruter = byggRuter({ scopes: ["policy:activate"] }).map((r) => r.nokkel);
  assert.ok(ruter.includes("policyadmin"));
  assert.ok(!ruter.includes("admin"));
});

test("byggRuter: admin krever security-scope", () => {
  const ruter = byggRuter({ scopes: ["security:read"] }).map((r) => r.nokkel);
  assert.ok(ruter.includes("admin"));
  assert.ok(!ruter.includes("policyadmin"));
});

test("byggRuter: plattformdrift når admin uten tenant-lokale scopes", () => {
  // Plattformdrift er en EGEN autoritet. Krevde ruten `security:read`, ville en
  // ren `platform:admin`-økt landet stille på `oversikt` — og filteret inne på
  // flaten aldri blitt kjørt.
  const ruter = byggRuter({ scopes: ["platform:admin"] }).map((r) => r.nokkel);
  assert.ok(ruter.includes("admin"));
  assert.ok(!ruter.includes("policyadmin"));
});

test("tillatteFlater: direkte hash kan ikke nå en flate uten scope", () => {
  const flater = { oversikt: () => {}, policy: () => {}, beslutninger: () => {},
    unntak: () => {}, kundeadmin: () => {}, policyadmin: () => {},
    admin: () => {} };
  const leser = tillatteFlater(byggRuter({ scopes: ["decisions:read",
    "exceptions:read", "policy:read"] }), flater);
  assert.deepEqual(Object.keys(leser),
    ["oversikt", "policy", "beslutninger", "unntak", "kundeadmin"]);
  assert.equal(leser.admin, undefined);
  assert.equal(leser.policyadmin, undefined);

  // `#/policy` skrevet rett i adressefeltet av en `godkjenner` skal ikke rendre:
  // ruteren validerer mot flatekartet, og flatekartet er scopene.
  const godkjenner = tillatteFlater(byggRuter({ scopes: ["decisions:read",
    "exceptions:read", "exceptions:approve"] }), flater);
  assert.equal(godkjenner.policy, undefined);
  assert.equal(typeof godkjenner.unntak, "function");

  const med = tillatteFlater(byggRuter({ scopes: ["security:read"] }), flater);
  assert.equal(typeof med.admin, "function");
  assert.equal(med.policyadmin, undefined);
});

// Rollen `admin` i `autorisasjon.py`: lesescopene + planforvaltningen. Den
// har verken `policy:write` eller `policy:activate`.
const ADMINSCOPES = ["decisions:read", "exceptions:read", "policy:read",
  "security:read", "bestilling:opprett", "plan:opprett", "plan:aktiver",
  "plan:gjenoppta"];

test("byggRuter: mottakeren av et planvarsel når innboksen sin", () => {
  // Codex P2: ruten sto bak `kanForvaltePolicy` alene. 044 sender pause- og
  // bruddvarsler til administratoren som aktiverte planen — en rolle uten
  // `policy:write`/`policy:activate` — så skallet pollet aldri `/v1/varsel`
  // for henne. Varselet ble skrevet, men ingen kunne se det, og en
  // administrator som hadde valgt `kun_portal` satt igjen uten både e-post
  // og portalspor.
  const admin = byggRuter({ scopes: ADMINSCOPES }).map((r) => r.nokkel);
  assert.ok(admin.includes("varsler"), admin);
  // Planen er en fane under wcagkontroll (eier 19/8) — veien varselet
  // peker på er samleflatens rute.
  assert.ok(admin.includes("wcagkontroll"),
    "veien planvarselet peker på må finnes");
  // Policyforvalteren står uendret.
  assert.ok(byggRuter({ scopes: ["policy:read", "policy:write"] })
    .map((r) => r.nokkel).includes("varsler"));
  // En ren leser blir ikke varslet og får ingen tom innboks i menyen.
  assert.ok(!byggRuter({ scopes: ["decisions:read", "policy:read"] })
    .map((r) => r.nokkel).includes("varsler"));
  // ... og hver rolle som KAN motta bærer `policy:read`, som er scopet
  // `GET /v1/varsel` krever — ellers ville menyen lovet en 403.
  assert.ok(ADMINSCOPES.includes("policy:read"));
});

test("visningFraSok: returnerer kun tilgjengelig visning", () => {
  const ruter = byggRuter({ scopes: ["policy:write", "security:read"] });
  assert.equal(visningFraSok("?visning=kundeadmin", ruter), "kundeadmin");
  assert.equal(visningFraSok("?visning=admin", ruter), "admin");
  assert.equal(visningFraSok("?visning=ukjent", ruter), null);
  assert.equal(visningFraSok("", ruter), null);
});

test("hashForDypLenke: hash settes én gang, ellers navigerer ruteren selv", () => {
  const ruter = byggRuter({ scopes: ["policy:write", "security:read"] });
  // Dyplenke uten hash: hash settes, og `hashchange` gjør navigasjonen.
  assert.equal(hashForDypLenke("?visning=admin", "", ruter), "#/admin");
  // Finnes hash allerede, er den sannheten — ruteren navigerer selv (null).
  assert.equal(hashForDypLenke("?visning=admin", "#/unntak", ruter), null);
  // Ukjent eller nektet visning skal ikke sette hash.
  assert.equal(hashForDypLenke("?visning=ukjent", "", ruter), null);
  assert.equal(hashForDypLenke("", "", ruter), null);
  assert.equal(hashForDypLenke("?visning=admin", "", byggRuter({ scopes: [] })),
    null);
});

// Codex P2: dyplenken er den andre veien inn i en fjernet rute. `?visning=plan`
// sto i lagrede lenker fra da planen var sin egen flate, og `visningFraSok`
// svarer bare på ruter økten HAR — en fjernet rute har ingen, så lenken falt
// tvers gjennom til ruterens reserve (Oversikt).
test("hashForDypLenke: den arvede planvisningen peker på fanen", () => {
  const kunde = byggRuter({ scopes: ["decisions:read"] });
  assert.equal(hashForDypLenke("?visning=plan", "", kunde),
    "#/wcagkontroll/plan");
  // Hash-en er fortsatt sannheten når den finnes: ÉN av delene skal skje.
  assert.equal(hashForDypLenke("?visning=plan", "#/unntak", kunde), null);
  // Aliaset er ingen vei rundt scopet: uten `decisions:read` finnes ikke
  // flaten det peker på, og da settes ingen hash.
  assert.equal(hashForDypLenke("?visning=plan", "", byggRuter({ scopes: [] })),
    null);
  // Og det er BARE de arvede nøklene: et arvet oppslag skal ikke svare på det
  // et objektoppslag ville arvet fra prototypen.
  assert.equal(hashForDypLenke("?visning=constructor", "", kunde), null);
  assert.equal(hashForDypLenke("?visning=__proto__", "", kunde), null);
});

// En rute uten etikett er ikke en kosmetisk mangel: `AppShell` slår opp
// `ui.nav.<nokkel>`, og i18n-fallbacket returnerer NØKKELEN når oppslaget
// bommer. Hovedmenyen viste derfor «ui.nav.varsler» til hver eneste
// policyforvalter, på begge språk (Codex P2). Det er nettopp en slik feil som
// ikke fanges av en test av flaten selv — flaten var riktig; det var kartet som
// pekte på en tekst ingen hadde skrevet.
//
// Porten måles mot rutene `byggRuter` FAKTISK kan produsere, ikke mot en liste
// noen må huske å oppdatere. Da er en ny rute uten etikett en rød test, og
// begge locale-settene holdes i takt av det samme kravet.
test("Hver rute byggRuter kan gi har en nav-etikett i BEGGE locale-sett", () => {
  // Alle scopene til sammen: den bredeste økten sitekartet kan bygge for.
  const alle = byggRuter({ scopes: [
    "decisions:read", "policy:read", "policy:write", "policy:activate",
    "exceptions:read", "security:read", "platform:admin",
  ] });
  // Kartet skal faktisk ha rukket å bli bredt — ellers måler testen ingenting.
  assert.ok(alle.length >= 8, `for få ruter i porten: ${alle.length}`);
  for (const navn of ["nb", "en"]) {
    const tekster = locale(navn);
    for (const r of alle) {
      const nokkel = `ui.nav.${r.nokkel}`;
      assert.ok(typeof tekster[nokkel] === "string" && tekster[nokkel].trim(),
        `${navn}.json mangler ${nokkel}`);
    }
  }
});
