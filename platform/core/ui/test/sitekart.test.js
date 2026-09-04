import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
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
  // M-57: rekruttering sto ute til serverarmen fantes (Codex P1 / Cursor
  // P1). Endepunktene er registrert i `app.py` nå, så ruten er inne — bak
  // flatens svakeste ledd (`decisions:read`), med mutasjonene gatet i
  // `RUTESCOPE` på `bestilling:opprett`.
  // M-5 (094): malregisteret er en MODULFLATE bak flatens svakeste ledd
  // (`decisions:read` — `GET /v1/dokumentmal`), med opprettelse,
  // publisering og tilbaketrekking gatet på `bestilling:opprett` i
  // `RUTESCOPE`. Utfyllingen bærer BEVISST lesescopet: den returnerer.
  // M-9 (095): ordlisten står bak `decisions:read` — kundens egen
  // referansetekst, som ALLE kunderollene skal kunne slå opp i.
  // `GET /v1/kunnskap` krever nøyaktig det scopet i `RUTESCOPE`.
  //
  // M-21 (096): pliktregisteret er en LESEFLATE for enhver kunderolle
  // med `decisions:read` — hvilke frister som løper og hvem som eier
  // dem er ikke administratorens hemmelighet. Skriveveiene er gatet på
  // `bestilling:opprett` i `RUTESCOPE`.
  //
  // ÉN assert for alle tre, med vilje: listen er UTTØMMENDE, så to
  // separate deepEqual-er over samme `alle` kan ALDRI begge være sanne
  // når flere moduler deler scopet — den andre er garantert rød. Tre
  // moduler i samme klynge landet på `decisions:read`, og hver av dem
  // skrev først sin egen komplette liste. Rekkefølgen er `BASISRUTER`-ens.
  // M-22 (098): lisensregisteret landet på det SAMME scopet, og listen er
  // uttømmende — den UTVIDES, den dupliseres ikke. `lisens` står sist
  // fordi det er `BASISRUTER`-ens rekkefølge.
  //
  // M-17 (102): kundeservicekøen landet på det samme scopet igjen, og
  // det er en dom: køen er tenantens ALMINNELIGE arbeidsflate. Selve
  // henvendelsens innhold ligger bak `kundeservice:innhold` og
  // hentes av et eget endepunkt inne på flaten — menyoppføringen lover
  // at LISTEN kan vises, ikke at hver celle kan åpnes, og flaten sier
  // det med rene ord til den som mangler innsynsscopet.
  assert.deepEqual(alle,
    ["oversikt", "nokkeltall", "policy", "beslutninger", "unntak",
      "kundeadmin", "wcagkontroll", "rekruttering", "dokumentmal",
      "kunnskap", "avtalefrist", "lisens", "kundeservice",
      "onboarding"]);
  // …og en leseøkt skal FAKTISK nå flaten: uten rute slipper
  // `tillatteFlater` heller ikke en håndskrevet `#/rekruttering` gjennom,
  // og demo-stien lander på reserveflaten (Oversikt) i stedet.
  assert.ok(tillatteFlater(byggRuter({ scopes: ["decisions:read"] }),
    { rekruttering: "flate", oversikt: "flate" }).rekruttering,
  "leseøkten når ikke rekrutteringsflaten");
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

test("byggRuter: modulflaten følger med ut av byggeren (Codex P1)", () => {
  // 🔴 Mappingen plukket bare `nokkel`, og `byggRuter` er den ENESTE veien
  // `visApp` gir skallet rutene sine. Vedtaket fra 24/8 sto altså i
  // sitekartet uten å nå fram til en eneste ekte økt: modulflatene ble
  // liggende i toppnav, og modulkortene åpnet panelet i stedet for flaten.
  const ruter = byggRuter({ scopes: ["decisions:read"] });
  assert.equal(ruter.find((r) => r.nokkel === "wcagkontroll").modulflate, 56);
  assert.equal(ruter.find((r) => r.nokkel === "rekruttering").modulflate, 57);
  // Plattformflatene har ingen — det er nettopp forskjellen skallet leser.
  assert.equal(ruter.find((r) => r.nokkel === "oversikt").modulflate, undefined);
  // `scope` blir derimot IGJEN: det er brukt opp i filteret over, og en rute
  // som bærer det videre later som den fortsatt gates på noe.
  assert.ok(ruter.every((r) => !("scope" in r)));
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

test("Lenketekst og flatens egen tittel er samme streng — for HVER rute", () => {
  // 🔴 LÅST, IKKE BARE KOMMENTERT (Cursor P2, generalisert 1/9).
  // Kortet eller nav-lenken er den annonserte inngangen til flaten, og det
  // er `ui.nav.<rute>` som står der mens flaten selv bærer sin egen
  // tittelnøkkel. At de to er samme streng er hele begrunnelsen for at
  // inngangen SKAL hete det — uten en port er likheten en tilfeldighet to
  // oversettere kan bryte hver for seg, og da peker inngangen på en side
  // som heter noe annet.
  //
  // FØR var dette en HÅNDHOLDT LISTE over modulflatene. Den hadde to
  // feil: den dekket bare rutene en `decisions:read`-økt ser, og den måtte
  // vedlikeholdes for hånd — en ny flate uten en rad var ulåst uten at noe
  // sa fra. Da eier døpte om `Admin` → `Plattform` og `Kundeadmin` →
  // `Kundeflate` (1/9) gjaldt kravet plutselig to BASISruter også.
  //
  // Regelen er derfor generell: HAR en rute begge nøklene, skal de være
  // like. Målt for hver rute i sitekartet og i begge språk. Ingen liste å
  // glemme.
  const kilde = readFileSync(new URL(
    "../static/js/sitekart.js", import.meta.url), "utf8");
  const ruter = [...new Set(
    [...kilde.matchAll(/nokkel: "([a-z]+)"/g)].map((m) => m[1]))];
  assert.ok(ruter.length > 15,
    "fant ikke rutene i sitekart-kilden — porten måler ingenting");

  // Telleren står PER SPRÅK, ikke samlet. En sum over begge kunne nådd
  // grensen på `nb` alene mens `en` målte NULL par — og en engelsk
  // avvikelse er nettopp den som er usynlig herfra.
  for (const navn of ["nb", "en"]) {
    const tekster = locale(navn);
    let malt = 0;
    for (const rute of ruter) {
      const lenke = tekster[`ui.nav.${rute}`];
      const tittel = tekster[`ui.${rute}.tittel`];
      if (!lenke || !tittel) continue;   // ikke hver flate har egen tittel
      malt += 1;
      assert.equal(lenke, tittel,
        `${navn}/${rute}: inngangen sier «${lenke}», flaten «${tittel}»`);
    }
    assert.ok(malt >= 10,
      `${navn}: porten sammenlignet bare ${malt} par — oppslaget er galt`);
  }
});

test("Hver datatabell ligger i en .tablewrap", () => {
  // 🔴 EIERS FUNN 1/9, RUNDE 3: «Datakvalitet ser ikke bra ut».
  // `.tablewrap { overflow-x: auto }` er sidescrollens container, og
  // `.tablewrap > table { width: max-content }` er det som lar tabellen bli
  // bredere enn den. UTEN wrapperen gjelder `width: 100%`, og nettleseren
  // klemmer kolonnene mot min-content — ett tegn per linje.
  //
  // Da forrige runde fikset CSS-en, traff den bare den ene flaten som
  // ALLEREDE hadde wrapperen. Seks tabeller i fire flater sto uten, og
  // fiksen så ut til å virke fordi jeg testet på retensjon. Porten teller
  // nå begge deler i hver flate og krever at de er like mange.
  // Listen er FELLES og skal UTVIDES, aldri dupliseres: M-12 og M-30 la
  // hver sin flate her samtidig, og en naiv fletting beholdt begge
  // halelinjene — som ga en syntaksfeil i stedet for to lister.
  const flater = ["retensjon", "datakvalitet", "kunnskap", "avtalefrist",
    "dokumentmal", "tilgang", "personvern", "compliance", "avstemming",
    "kundeservice", "onboarding", "fordring", "leverandor",
    "faktura", "prosjekt", "prisbok", "lager", "kontovakt", "betaling"];
  let sett = 0;
  for (const navn of flater) {
    const kilde = readFileSync(new URL(
      `../static/js/flater/${navn}.js`, import.meta.url), "utf8");
    const tabeller = (kilde.match(/el\("table"/g) || []).length;
    if (!tabeller) continue;
    const wrappere = (kilde.match(/class: "tablewrap"/g) || []).length;
    sett += tabeller;
    assert.ok(wrappere >= tabeller,
      `${navn}.js: ${tabeller} tabell(er), men bare ${wrappere} .tablewrap`
      + " — de uten klemmer kolonnene i stedet for å scrolle");
  }
  assert.ok(sett >= 8,
    `porten fant bare ${sett} tabeller — oppslaget er galt`);
});

test("Skjemaets rutenett er opt-in, ikke påtvunget", () => {
  // `.kv-skjema` deles av tre flater. To av dem legger etikett og felt som
  // LØSE SØSKEN, og et rutenett på den felles klassen ville spredt dem i
  // hver sin celle — verre enn før. Regelen står derfor på en egen klasse.
  const css = readFileSync(new URL(
    "../static/css/komponenter.css", import.meta.url), "utf8");
  assert.match(css, /\.kv-skjema-rutenett\s*\{[^}]*display:\s*grid/,
    "rutenettregelen mangler på opt-in-klassen");
  assert.ok(!/^\.kv-skjema\s*\{[^}]*display:\s*grid/m.test(css),
    "rutenettet står på den DELTE klassen og treffer skjemaer som ikke"
    + " er bygget for det");
});

// HVER KVITTERINGSNØKKEL SKAL FINNES I BEGGE SPRÅK.
//
// Porten finnes fordi en av dem ikke gjorde det: `skjemaramme` fikk
// `okNokkel: "…klassifisering_ok"`, og testen ventet på
// `"…klassifiser_ok"` — en nøkkel ingen hadde skrevet. `t()` gir da
// nøkkelen rå tilbake, så FLATEN ville vist «ui.kundeservice.skjema.
// klassifiser_ok» til brukeren i stedet for en setning.
//
// Dette måler ALLE flatene på én gang, ikke bare de fem i klynge 3: en
// nøkkel som ikke finnes er den samme feilen uansett hvem som skrev den.
test("Hver okNokkel i en flate finnes i begge locale-settene", () => {
  const flatemappe = join(HER, "..", "static", "js", "flater");
  const sett = {};
  for (const sprak of ["nb", "en"]) {
    sett[sprak] = JSON.parse(readFileSync(
      join(HER, "..", "..", "..", "..", "locales", `${sprak}.json`),
      "utf-8"));
  }
  const talte = new Set();
  for (const fil of readdirSync(flatemappe)) {
    if (!fil.endsWith(".js")) continue;
    const kilde = readFileSync(join(flatemappe, fil), "utf8");
    for (const m of kilde.matchAll(/okNokkel:\s*"([^"]+)"/g)) {
      talte.add(m[1]);
      for (const sprak of ["nb", "en"]) {
        assert.ok(sett[sprak][m[1]],
          `${fil}: ${m[1]} mangler i locales/${sprak}.json`);
      }
    }
  }
  // …og porten skal måle noe. Null treff ville vært grønt på en tom
  // katalog like godt som på en riktig. FORSKJELLIGE nøkler telles, ikke
  // forekomster: tjue kall med samme nøkkel er én nøkkel målt tjue
  // ganger, og en terskel på forekomster ville vært grønn på det.
  assert.ok(talte.size >= 20, `fant bare ${talte.size} kvitteringsnøkler`);
});

test("sitekart: hver rute i menyen har et navn på begge språk", () => {
  // MODULER SOM VISER MASKINNAVN. `navnFor` slår opp `ui.nav.<flate>`
  // uten reserve, og `t()` gir nøkkelen tilbake når den mangler — så en
  // modul uten tekst står i venstremenyen som «ui.nav.tollkode».
  //
  // SYTTEN MODULER STO SLIK, fra M-19 og utover: hver modul-PR la til
  // ruten og glemte navnet, og ingenting målte det. Porten fantes ikke
  // fordi ingen enkelt PR så mønsteret — den attende ville lagt seg til
  // like stille.
  //
  // MUTASJONEN SOM DREPER DENNE: legg til en rute uten `ui.nav`-nøkkel.
  const nb = locale("nb");
  const en = locale("en");
  // ALLE scopene, så ingen rute gjemmer seg bak et filter.
  const alle = byggRuter({ scopes: [
    "decisions:read", "exceptions:read", "policy:read", "policy:write",
    "policy:activate", "okonomi:read", "okonomi:write", "hr:read",
    "security:read", "platform:admin", "bestilling:opprett",
  ] });
  const mangler = [];
  for (const r of alle) {
    for (const [sprak, ordbok] of [["nb", nb], ["en", en]]) {
      if (!(`ui.nav.${r.nokkel}` in ordbok)) {
        mangler.push(`${sprak}: ui.nav.${r.nokkel}`);
      }
    }
  }
  assert.deepEqual(mangler, [],
    `ruter uten navn — de står med nøkkelen sin i menyen:\n${
      mangler.join("\n")}`);
});
