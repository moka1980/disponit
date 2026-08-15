import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  MODULOVERSIKT, MODULSTATUS, PRODUKSJONSMILJO, TILBUD, dataSvarNokkel,
  dataSvarNokkelFor, erTilgjengelig, erTilgjengeligFor, heroTekstNokkel,
  heroTekstNokkelFor, modulStatus, modulerFraIder, modulmerke,
  plattformTelling, tenantTelling,
} from "../static/js/plattformdata.js";

// Locale-settene leses direkte, ikke via `hjelp.js`: denne fila påstår om ren
// data og trenger ingen jsdom-rigg for å gjøre det.
const ROT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..");
const LOKALER = ["nb", "en"].map((sprak) =>
  [sprak, JSON.parse(readFileSync(join(ROT, "locales", `${sprak}.json`), "utf-8"))]);

test("modulStatus: ukjent modul er planlagt, ikke udefinert", () => {
  // Verdiene er avledet av manifestene (pinnet i test_ui_kontrakt.py): M-1
  // kjører i produksjon, M-2/M-37 er under utvikling, M-38 har intet manifest.
  // `i_drift` krever `driftstilstand: produksjon` i manifestet — ikke en
  // redaksjonell avgjørelse her.
  assert.equal(modulStatus(1), "i_drift");
  assert.equal(modulStatus(2), "bygges");
  assert.equal(modulStatus(38), "planlagt");
  assert.equal(modulStatus(45), "planlagt");
});

test("MODULSTATUS: ingen modul lover drift uten at manifestet gjør det", () => {
  // Regelen er bindingen, ikke tallet: hver `i_drift` her må ha
  // `driftstilstand: produksjon` i sitt manifest, og
  // `test_ui_kontrakt.py::test_modulstatus_folger_manifestene` håndhever
  // nettopp den retningen. M-1 er i drift fordi disponit.com kjører den.
  assert.deepEqual(
    Object.entries(MODULSTATUS).filter(([, s]) => s === "i_drift")
      .map(([id]) => Number(id)),
    [1], "andre moduler enn M-1 påstår drift — sjekk manifestene");
});

test("erTilgjengeligFor: løftet krever både drift OG et produksjonsmiljø", () => {
  // «Tilgjengelig» er et løfte til en besøkende om at hen kan ta modulen i
  // bruk NÅ, med sine egne data. Bare `i_drift` bærer det FØRSTE leddet:
  // `klargjort` er godkjent uten drift, og `bygges`/`planlagt` er enda lenger
  // unna. Men drift alene er ikke nok (Codex P2): finnes ikke miljøet kundens
  // data skulle ligge i, er løftet tomt uansett hvor koden kjører.
  //
  // Ren funksjon av de to aksene, så BEGGE tilstandene kan pinnes uten å
  // forfalske `MODULSTATUS` eller vente på at et miljø faktisk finnes.
  assert.equal(erTilgjengeligFor("i_drift", true), true);
  assert.equal(erTilgjengeligFor("i_drift", false), false,
    "drift uten produksjonsmiljø er ikke et løfte en kunde kan innfri");
  assert.equal(erTilgjengeligFor("klargjort", true), false,
    "godkjent er ikke i drift");
  assert.equal(erTilgjengeligFor("bygges", true), false);
  assert.equal(erTilgjengeligFor("planlagt", true), false);
});

test("erTilgjengelig: forsiden lover bare det som faktisk kjører", () => {
  for (const post of TILBUD) {
    assert.equal(erTilgjengelig(post.id),
      erTilgjengeligFor(modulStatus(post.id), PRODUKSJONSMILJO),
      `M-${post.id} lover noe annet enn kildene bærer`);
  }
  // Slik plattformen faktisk står: M-1 KJØRER (manifestet sier
  // `driftstilstand: produksjon`, og `test_ui_kontrakt.py` pinner det), men
  // produksjonsmiljøet finnes ikke ennå — policyene bak står `utkast` og
  // verten står `DISPONIT_MILJO=staging`. Brikka sier derfor «Kommer».
  assert.equal(modulStatus(1), "i_drift", "M-1 kjører — det er uendret");
  assert.equal(PRODUKSJONSMILJO, false,
    "flippes denne, må datasvaret og brikkene flippe SAMMEN — se testen under");
  assert.equal(erTilgjengelig(1), false,
    "M-1 loves som tilgjengelig uten at produksjonsmiljøet finnes");
  assert.equal(erTilgjengelig(37), false, "M-37 er under utvikling");
  assert.equal(erTilgjengelig(45), false, "ukjent modul er planlagt");
});

test("datasvaret og brikkene kan ikke motsi hverandre", () => {
  // Kjernen i funnet (Codex P2): forsiden rendrer BÅDE tilgjengelighetsbrikker
  // og svaret på «Hvor ligger dataene?». Sto svaret som en fast nøkkel, kunne
  // den merke et tilbudspunkt «Tilgjengelig» i én seksjon og si «i dag finnes
  // bare staging» i den neste. Begge utledes nå av samme konstant, og testen
  // pinner koblingen i BEGGE retninger — ikke bare i dagens tilstand.
  assert.equal(dataSvarNokkelFor(false), "site.svar.data_sv");
  assert.equal(dataSvarNokkelFor(true), "site.svar.data_sv_produksjon");
  assert.equal(dataSvarNokkel(), dataSvarNokkelFor(PRODUKSJONSMILJO));
  // Sier siden at bare staging finnes, kan intet tilbudspunkt være merket
  // tilgjengelig — og motsatt.
  if (dataSvarNokkel() === "site.svar.data_sv") {
    assert.equal(TILBUD.filter((post) => erTilgjengelig(post.id)).length, 0,
      "forsiden lover et tilbud som tilgjengelig, men sier at bare staging " +
      "finnes — to utelukkende påstander på samme side");
  }
  // Begge svarene må finnes på BEGGE språk, ellers ville flippen rendret selve
  // nøkkelen som brødtekst — og bare på det språket ingen husket å fylle.
  for (const [sprak, sett] of LOKALER) {
    for (const nokkel of ["site.svar.data_sv", "site.svar.data_sv_produksjon"]) {
      assert.ok(sett[nokkel], `${nokkel} mangler i locales/${sprak}.json`);
    }
  }
});

test("heroTekstNokkelFor: delvis utrulling har sin egen tekst", () => {
  // Presensformen («agenten håndterer utbetalinger, purringer, oppfølging og
  // rapportering») lover ALLE fire områdene. Den er derfor bare sann når
  // hvert tilbudspunkt er i drift. Ett `some()` slo den på ved den FØRSTE
  // modulen som gikk i drift, mens tre brikker fortsatt sa «Kommer» — samme
  // selvmotsigelse som med null i drift, bare flyttet (Codex P2).
  assert.equal(heroTekstNokkelFor(0, 4), "site.hero.tekst_bygges");
  assert.equal(heroTekstNokkelFor(1, 4), "site.hero.tekst_delvis",
    "én av fire i drift lover ikke alle fire");
  assert.equal(heroTekstNokkelFor(3, 4), "site.hero.tekst_delvis",
    "tre av fire i drift lover fortsatt ikke alle fire");
  assert.equal(heroTekstNokkelFor(4, 4), "site.hero.tekst");
  // Alle tre nøklene må finnes på BEGGE språk — ellers ville en delvis
  // utrulling rendret selve nøkkelen som brødtekst på forsiden, og bare på
  // det språket ingen husket å fylle.
  for (const [sprak, sett] of LOKALER) {
    for (const nokkel of ["site.hero.tekst", "site.hero.tekst_bygges",
                          "site.hero.tekst_delvis"]) {
      assert.ok(sett[nokkel], `${nokkel} mangler i locales/${sprak}.json`);
    }
  }
});

test("heroTekstNokkel: hovedløftet følger brikkene, ikke redaktøren", () => {
  // Nøkkelen utledes av den samme MODULSTATUS som brikkene: to kilder ville
  // drevet fra hverandre neste gang en modul skifter tilstand.
  const iDrift = TILBUD.filter((post) => erTilgjengelig(post.id)).length;
  assert.equal(heroTekstNokkel(), heroTekstNokkelFor(iDrift, TILBUD.length));
  // Slik plattformen faktisk står: ingen tilbudspunkter er tilgjengelige for
  // en kunde ennå, fordi produksjonsmiljøet ikke finnes — og hovedløftet står
  // derfor i bygges-formen. Det er den samme kilden som brikkene og datasvaret:
  // ryker koblingen, ville helten lovet noe brikkene rett under motsa.
  assert.equal(iDrift, 0, "antall tilgjengelige tilbudspunkter har endret seg");
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
  // En tenant med M-1 (i drift) og M-2 (bygges): én i drift, én under arbeid
  // — og resten av katalogen er planlagt for kunden. Tildelingen er ID-er fra
  // den autentiserte veien, ikke et oppslag i klientpakken.
  const telling = tenantTelling(modulerFraIder([1, 2]));
  assert.equal(telling.iDrift, 1);
  assert.equal(telling.underArbeid, 1);
  assert.equal(telling.planlagt, telling.totalt - 2);
  const ukjent = tenantTelling([]);
  assert.equal(ukjent.iDrift, 0);
  assert.equal(ukjent.underArbeid, 0);
});

test("modulmerke: tenantens modul-ID-er vises som M-<id>", () => {
  assert.deepEqual([1, 2].map(modulmerke), ["M-1", "M-2"]);
});
