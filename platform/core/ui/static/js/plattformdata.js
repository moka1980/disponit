// Produkt- og plattformdata for den offentlige webflaten og de nye
// administrasjonsvisningene. Én statuskilde gjør at modulstatus kan oppdateres
// ett sted når en modul faktisk er ferdig.
import { t } from "./i18n.js";

// Kanonisk statuskilde. Alt annet — kort, merker og KPI-er — utleder herfra,
// så en modul som skifter tilstand oppdateres ETT sted.
//
// Verdiene er ikke en mening om modulene: de er AVLEDET av manifestenes to
// akser (`platform/modules/*/manifest.yaml`), og
// `test_ui_kontrakt.py::test_modulstatus_folger_manifestene` pinner kartet mot
// dem. Manifestet skiller bevisst `status` (er modulen GODKJENT?) fra
// `driftstilstand` (hvor kjører den FAKTISK?) — kollapset flaten de to til ett
// ord, lovet den drift der registeret sa `ikke_i_drift`. Avledningen:
//
//   driftstilstand: produksjon          → i_drift     modulen kjører hos kunder
//   status: aktiv, ikke i produksjon    → klargjort   godkjent, men ikke i drift
//   manifest finnes, status ikke aktiv  → bygges      under utvikling
//   ingen manifest                      → planlagt    beskrevet, ikke påbegynt
export const MODULSTATUS = {
  1: "i_drift",     // m01_policy: status aktiv, driftstilstand produksjon
  2: "bygges",      // m02_revisjonslogg: under_utvikling, ikke_i_drift
  37: "bygges",     // m37_unntak: under_utvikling, ikke_i_drift
  38: "planlagt",   // ingen manifest i platform/modules/ ennå
};

// Den ANDRE aksen den publike forsiden trenger: FINNES produksjonsmiljøet?
//
// `MODULSTATUS` sier hvor koden kjører. Det er ikke det samme som at en kunde
// kan legge sine egne data inn i den. `docs/DEPLOY.md` sin miljøtabell sier at
// produksjon er en egen maskin som settes opp når fase 1 nærmer seg pilot, og
// verten som kjører i dag står `DISPONIT_MILJO=staging` — altså regelverket
// som slipper `utkast`-policyer gjennom. Begge deler er sanne samtidig:
// disponit.com avgjør ekte forespørsler, OG produksjonsmiljøet finnes ikke.
//
// Denne konstanten er kilden BEGGE de publike påstandene utledes av: brikka
// «Tilgjengelig» og svaret på «Hvor ligger dataene?». Sto de hver for seg,
// kunne forsiden love drift i den ene setningen og si at bare staging finnes
// i den neste (Codex P2) — to utelukkende påstander innenfor samme side, og
// en besøkende uten noen måte å vite hvilken som gjaldt.
//
// Den flippes når miljøet FAKTISK finnes, og det er fire ledd, ikke ett:
// policyene promotert fra `utkast` til `produksjon`, `DISPONIT_MILJO=produksjon`
// satt på verten, en utrulling som beviser verdien i den kjørende prosessen —
// og først da bærer forsiden løftet.
//
// Verdien kommer fra SERVEREN (`/ui/oppsett.json` → `miljo`), ikke fra en
// konstant her. En hardkodet `false` ville krevd en kodeendring, en review og
// en utrulling for å si sannheten den dagen de fire leddene var på plass — og
// like gjerne blitt stående og lyve motsatt vei etterpå. Serveren leser den
// samme `DISPONIT_MILJO` som `policyregister.tillatte_statuser`, så brikka og
// regelverket som binder beslutningene kan ikke komme i utakt.
//
// Startverdien er `false`: laster oppsettet aldri, lover forsiden ingenting.
let _produksjonsmiljo = false;

export function settProduksjonsmiljo(pa) { _produksjonsmiljo = pa === true; }

export function produksjonsmiljo() { return _produksjonsmiljo; }

// Status står IKKE her: modulene beskriver navn, fase og tekst, mens
// `MODULSTATUS` eier hva de faktisk er. Sto den begge steder, ville en modul
// som skifter tilstand vise `bygges` på kortet mens KPI-en talte den som
// `i_drift` — eller motsatt, avhengig av hvilket sted som ble oppdatert.
const MODULER = [
  {
    id: 1,
    navn_nokkel: "site.modul.m1.navn",
    fase_nokkel: "site.fase.fundament",
    tekst_nokkel: "site.modul.m1.tekst",
  },
  {
    id: 2,
    navn_nokkel: "site.modul.m2.navn",
    fase_nokkel: "site.fase.fundament",
    tekst_nokkel: "site.modul.m2.tekst",
  },
  {
    id: 37,
    navn_nokkel: "site.modul.m37.navn",
    fase_nokkel: "site.fase.fundament",
    tekst_nokkel: "site.modul.m37.tekst",
  },
  {
    id: 38,
    navn_nokkel: "site.modul.m38.navn",
    fase_nokkel: "site.fase.fundament",
    tekst_nokkel: "site.modul.m38.tekst",
  },
];

// En modul uten oppføring i `MODULSTATUS` er planlagt: den er beskrevet, men
// ingen har sagt at den er i gang.
export function modulStatus(id) {
  return MODULSTATUS[id] || "planlagt";
}

export const MODULOVERSIKT = MODULER.map((mod) => ({
  ...mod, status: modulStatus(mod.id),
}));

// Rolleguiden kundeflaten viser. `scopes` er rollens FAKTISKE scopes slik
// `platform/core/api/autorisasjon.py` utleder dem, og `test_ui_kontrakt.py`
// pinner listen mot den kanoniske `ROLLE_TIL_SCOPES`. Uten den bindingen kunne
// guiden love en fullmakt rollen ikke har — som da `godkjenner` ble beskrevet
// som å attestere policy, mens attestasjon krever `policy:activate` og bare
// `policyforvalter` har den. Kunden ville tildelt feil rolle og oppdaget det
// først på en 403.
export const KUNDEROLLER = [
  {
    id: "leser",
    navn_nokkel: "ui.kundeadmin.rolle.leser",
    tekst_nokkel: "ui.kundeadmin.rolle.leser_tekst",
    scopes: ["decisions:read", "exceptions:read", "policy:read"],
  },
  {
    id: "godkjenner",
    navn_nokkel: "ui.kundeadmin.rolle.godkjenner",
    tekst_nokkel: "ui.kundeadmin.rolle.godkjenner_tekst",
    scopes: ["decisions:read", "exceptions:read", "exceptions:approve",
             "exceptions:reject", "exceptions:escalate"],
  },
  {
    id: "policyforvalter",
    navn_nokkel: "ui.kundeadmin.rolle.policyforvalter",
    tekst_nokkel: "ui.kundeadmin.rolle.policyforvalter_tekst",
    scopes: ["decisions:read", "policy:read", "policy:write", "policy:activate"],
  },
];

export const FASEOVERSIKT = [
  {
    navn_nokkel: "site.fase.fundament",
    status: "aktiv",
    tekst_nokkel: "site.fase.fundament.tekst",
  },
  {
    navn_nokkel: "site.fase.operasjoner",
    status: "planlagt",
    tekst_nokkel: "site.fase.operasjoner.tekst",
  },
  {
    navn_nokkel: "site.fase.autopiloter",
    status: "planlagt",
    tekst_nokkel: "site.fase.autopiloter.tekst",
  },
  {
    navn_nokkel: "site.fase.global",
    status: "planlagt",
    tekst_nokkel: "site.fase.global.tekst",
  },
];

// INGEN TENANTDATA I DENNE FILA (Codex P1). `/ui/{sti}` serveres uten
// sesjonssjekk — den anonyme landingssiden importerer dette modultreet — så alt
// som ligger her kan lastes ned av hvem som helst, uansett hvilken
// scope-sjekk flatene gjør før de RENDRER. Et tenantregister her ville lekket
// hver kundes navn, plan, modultildeling og neste steg til en `curl`.
//
// Samme grunn gjelder `locales/`: `/ui/locale/nb` svarer 200 uten cookie, så
// kundenavn kan heller ikke ligge der som oversettelsesnøkler. Tenantdata er
// DATA, ikke chrome-tekst — den hentes fra et autentisert, server-autorisert
// endepunkt og sendes inn i flatene som `ctx.tenanter` / `ctx.moduler`.
// Flatene viser en eksplisitt tomtilstand til det endepunktet finnes.
//
// `platform/core/ui/test/offentlige_ressurser.test.js` håndhever begge deler.

export function modulmerke(id) {
  return `M-${id}`;
}

// TILBUDET slik en kunde møter det: hva agenten gjør for bedriften, i
// klartekst. Interne modulnumre (M-1, M-37) og driftsvokabular
// (`klargjort`/`bygges`) hører hjemme på adminflaten — en kunde skal lese
// «Fullmakter og policy», ikke «M-1 Klargjort».
//
// `tilgjengelig` utledes av den SAMME `MODULSTATUS` som resten, og bare
// `i_drift` teller (Codex P2). `klargjort` betyr GODKJENT, ikke i drift, og
// «Tilgjengelig» på noe som ikke kjører ville vært et løfte en besøkende ikke
// kan innfri. `sitekomponenter.js` gjør samme skille på adminflaten: grønt er
// reservert for det som FAKTISK kjører hos kunder. Forsiden sier «Kommer» om
// resten — ett ord, ikke et byggeregnskap.
//
// M-1 står nå `i_drift` fordi den GJØR det: disponit.com kjører
// `disponit-api.service` og `disponit-m37.service` mot migrasjon 19, og
// policymotoren avgjør ekte forespørsler der. Manifestet sa `ikke_i_drift`
// med begrunnelsen «staging har ingen installerte units, og det finnes ikke
// engang en unit for API-et» — begge deler var sant da det ble skrevet, og
// ingen av dem er sanne nå.
export const TILBUD = [
  { id: 1, navn_nokkel: "site.tilbud.fullmakt.navn",
    tekst_nokkel: "site.tilbud.fullmakt.tekst" },
  { id: 37, navn_nokkel: "site.tilbud.unntak.navn",
    tekst_nokkel: "site.tilbud.unntak.tekst" },
  { id: 2, navn_nokkel: "site.tilbud.spor.navn",
    tekst_nokkel: "site.tilbud.spor.tekst" },
  { id: 38, navn_nokkel: "site.tilbud.kapasitet.navn",
    tekst_nokkel: "site.tilbud.kapasitet.tekst" },
];

// «Tilgjengelig» er et løfte til en BESØKENDE om at hen kan ta modulen i bruk
// — med sine egne data. Det løftet har TO ledd, ikke ett (Codex P2): modulen
// må kjøre, og miljøet den skulle kjøre kundens data i må finnes.
// `driftstilstand: produksjon` dekker bare det første. M-1 avgjør ekte
// forespørsler på disponit.com i dag, men policyene som binder dem står
// `utkast` og verten står `staging` — brikka ville lovet en kunde noe ingen
// kunne innfri, samtidig som svaret rett under sa at bare staging finnes.
//
// Skillet er det samme manifestene gjør med `status` og `driftstilstand`:
// kollapses to akser til ett ord, lover flaten mer enn den ene aksen bærer.
export function erTilgjengeligFor(status, produksjonsmiljo) {
  return status === "i_drift" && produksjonsmiljo;
}

export function erTilgjengelig(id) {
  return erTilgjengeligFor(modulStatus(id), _produksjonsmiljo);
}

// Svaret på «Hvor ligger dataene?» er en PÅSTAND OM SYSTEMET, ikke salgstekst,
// og det utledes derfor av den SAMME kilden som brikkene. `data_sv` beskriver
// dagens tilstand (bare staging, syntetiske testdata); `data_sv_produksjon`
// blir sant i nøyaktig samme øyeblikk som `PRODUKSJONSMILJO`. Begge finnes i
// begge locale-sett, så flippen er ett ord her — ikke en redaksjonsrunde der
// noen kan komme til å oppdatere halve forsiden.
export function dataSvarNokkelFor(produksjonsmiljo) {
  return produksjonsmiljo
    ? "site.svar.data_sv_produksjon" : "site.svar.data_sv";
}

export function dataSvarNokkel() {
  return dataSvarNokkelFor(_produksjonsmiljo);
}

// Hovedløftet i heltet er formulert i presens («agenten håndterer …»), men
// med null moduler i drift rendres hvert eneste tilbudspunkt under det som
// «Kommer». Forsiden motsa da seg selv innenfor én skjermhøyde (Codex P2).
// Teksten velges derfor av den SAMME `MODULSTATUS` som brikkene.
//
// Utrullingen har TRE tilstander, ikke to (Codex P2, andre runde): et
// `some()` lot den første modulen som gikk i drift slå på presensformen, og
// den lover ALLE fire områdene selv om tre brikker fortsatt sier «Kommer» —
// samme selvmotsigelse, bare flyttet fra null til delvis. Presensformen over
// hele linja krever derfor at HVERT tilbudspunkt er i drift; er noen, men
// ikke alle, i drift, har delvis-formen sin egen tekst som lover det som
// står merket «Tilgjengelig» og sier at resten bygges.
//
// Selve valget er skilt ut som en ren funksjon av tellingene, så alle tre
// tilstandene kan pinnes i test uten å forfalske `MODULSTATUS` — den
// beholder én kilde, og delvis-tilfellet trenger ikke vente på at en modul
// faktisk går i drift før noen oppdager at teksten er feil.
export function heroTekstNokkelFor(iDrift, totalt) {
  if (iDrift <= 0) return "site.hero.tekst_bygges";
  return iDrift >= totalt ? "site.hero.tekst" : "site.hero.tekst_delvis";
}

export function heroTekstNokkel() {
  return heroTekstNokkelFor(
    TILBUD.filter((post) => erTilgjengelig(post.id)).length, TILBUD.length);
}

// Planetiketten. Serveren sender en KODE (`pilot`, `internt`) fordi planen er
// et lukket vokabular: sendte den etiketten, viste den engelske tabellen
// «Internt» uansett hvilket språk brukeren hadde valgt. Selve etiketten er
// chrome og ligger i locale-settet — det er TILDELINGEN av en plan til en
// kunde som er tenantdata, og den blir bak den autentiserte veien.
// Ukjent kode faller til koden selv, aldri til en tom celle.
export function planEtikett(kode) {
  const k = String(kode || "").trim();
  return k ? t(`site.plan.${k}`, k) : "";
}

// Modultildelingen for ÉN tenant, utledet av modul-ID-ene den AUTENTISERTE
// veien oppga. `null` betyr «vet ikke», ikke «ingen moduler»: en flate som
// ikke vet, skal si det — ikke vise hele plattformkatalogen som om den var
// kundens. Selve tildelingen er tenantdata og kommer derfor utenfra, ikke fra
// en tabell i denne fila.
export function modulerFraIder(ider) {
  if (!Array.isArray(ider)) return null;
  return MODULOVERSIKT.filter((mod) => ider.includes(mod.id));
}

// Tenantens egne tall. `planlagt` er plattformmodulene kunden ennå ikke har
// fått aktivert — ikke plattformens globale restliste.
export function tenantTelling(moduler) {
  const tell = (s) => moduler.filter((m) => m.status === s).length;
  const iDrift = tell("i_drift");
  const klargjort = tell("klargjort");
  const bygges = tell("bygges");
  const totalt = KATALOG_TOTALT;
  return { iDrift, klargjort, bygges, underArbeid: klargjort + bygges,
    planlagt: totalt - moduler.length, totalt };
}

//: Modulkatalogen slik produktplanen beskriver den. Bare de fire i `MODULER`
//: har en tilstand ennå; resten er `planlagt`.
const KATALOG_TOTALT = 45;

export function plattformTelling() {
  const tell = (s) => Object.values(MODULSTATUS).filter((v) => v === s).length;
  const iDrift = tell("i_drift");
  const klargjort = tell("klargjort");
  const bygges = tell("bygges");
  return { iDrift, klargjort, bygges, underArbeid: klargjort + bygges,
    planlagt: KATALOG_TOTALT - iDrift - klargjort - bygges,
    totalt: KATALOG_TOTALT };
}
