// Produkt- og plattformdata for den offentlige webflaten og de nye
// administrasjonsvisningene. Én statuskilde gjør at modulstatus kan oppdateres
// ett sted når en modul faktisk er ferdig.
import { t } from "./i18n.js";
import { KATALOG_ANTALL } from "./katalog.js";

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
//
// `staging` faller altså sammen med `ikke_i_drift` HER, og det er riktig for en
// FLATE: ordene beskriver hva en besøkende kan regne med, ikke hvor mange
// maskiner koden står på. Skillet mellom «kjører ingen steder» og «kjører på
// vår egen testserver» er ekte, men det bor i manifestet og i registerets
// `i_drift`-liste — ikke i det kunden leser.
export const MODULSTATUS = {
  1: "klargjort",   // m01_policy: status aktiv, driftstilstand staging
  2: "i_drift",     // m02_revisjonslogg: aktiv, produksjon (akseptert
                    // 2026-08-23, innholdsadressert @ 2aaca01 — grensen
                    // m02-aksept-v1, alle punkter bundet)
  37: "bygges",     // m37_unntak: under_utvikling, ikke_i_drift
  38: "planlagt",   // ingen manifest i platform/modules/ ennå
  // m56_wcag_audit: akseptert 2026-08-23 på wcag-r23 (r21-runden +
  // flippedrillen 22/8, aksepthendelsen i basen) og flippet SAMMEN med
  // m02 — konsistensregelen som holdt den igjen er nå oppfylt begge
  // veier. Manifestet (denne flatens eneste kilde) sier
  // aktiv/produksjon.
  56: "i_drift",
  57: "bygges",     // m57_ats: under_utvikling, ikke_i_drift — flippes
                    // av M-57-aksepten, aldri av en byggemilepæl
};

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
  {
    id: 56,
    navn_nokkel: "site.modul.m56.navn",
    fase_nokkel: "site.fase.autopiloter",
    tekst_nokkel: "site.modul.m56.tekst",
  },
  {
    id: 57,
    navn_nokkel: "site.modul.m57.navn",
    fase_nokkel: "site.fase.autopiloter",
    tekst_nokkel: "site.modul.m57.tekst",
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

// Fasestatusen sto som en EGEN skrevet verdi ved siden av `MODULSTATUS`
// (Codex P2 på #152): M-56 ble `i_drift` i flippet, mens fasen modulen
// ligger i fortsatt rendret «Planlagt» på den SAMME adminsiden som viste
// kortet «I drift». Det er dobbeltautoriteten toppen av denne fila sier at
// kort, merker og KPI-er ikke skal ha — og den koster en glemt linje hver
// gang en modul flytter seg.
//
// Avledningen: en fase er `aktiv` når minst én av modulene i den er
// PÅBEGYNT, altså har en annen tilstand enn `planlagt`. En fase ingen har
// begynt på er planlagt. Det gir de samme fire verdiene flaten har vist
// hele tiden, bortsett fra den ene som var feil.
export function faseStatus(navn_nokkel) {
  return MODULER.some((mod) => mod.fase_nokkel === navn_nokkel &&
    modulStatus(mod.id) !== "planlagt") ? "aktiv" : "planlagt";
}

export const FASEOVERSIKT = [
  {
    navn_nokkel: "site.fase.fundament",
    tekst_nokkel: "site.fase.fundament.tekst",
  },
  {
    navn_nokkel: "site.fase.operasjoner",
    tekst_nokkel: "site.fase.operasjoner.tekst",
  },
  {
    navn_nokkel: "site.fase.autopiloter",
    tekst_nokkel: "site.fase.autopiloter.tekst",
  },
  {
    navn_nokkel: "site.fase.global",
    tekst_nokkel: "site.fase.global.tekst",
  },
].map((fase) => ({ ...fase, status: faseStatus(fase.navn_nokkel) }));

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
// `i_drift` teller (Codex P2). `klargjort` betyr GODKJENT, ikke i drift hos
// kunder, så en modul i den tilstanden kan aldri stå «Tilgjengelig». M-1 står
// der i dag: manifestet sier `driftstilstand: staging` — koden kjører, men på
// vår egen testserver, ikke hos noen kunde.
// `sitekomponenter.js` gjør allerede det samme skillet på
// adminflaten: grønt er reservert for det som FAKTISK kjører hos kunder.
// Forsiden sier «Kommer» om resten — ett ord, ikke et byggeregnskap.
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
// med sine egne data. Det løftet har TO ledd, ikke ett: modulen må være rullet
// ut til kunder (`i_drift`), OG verten må kjøre i produksjonsmodus. Leddene
// ble oppfylt hver for seg, slik denne kommentaren forutså: etter
// akseptflippet holder det FØRSTE for M-2 og M-56, mens verten fortsatt er
// staging og `DISPONIT_MILJO` sier det samme. Da er ETT av dem ikke nok —
// policyene som binder beslutningene står `utkast` så lenge verten er staging,
// uansett hvor koden er rullet ut, og `erTilgjengelig(2)` er derfor `false`
// med `modulStatus(2) === "i_drift"`.
//
// Skillet er det samme manifestene gjør med `status` og `driftstilstand`:
// kollapses to akser til ett ord, lover flaten mer enn den ene aksen bærer.
export function erTilgjengeligFor(status, produksjonsmiljo) {
  return status === "i_drift" && produksjonsmiljo === true;
}

// Miljøet kommer fra SERVEREN (`/ui/oppsett.json` → `miljo`), ikke fra en
// konstant her. En hardkodet verdi ville krevd kodeendring, review og
// utrulling bare for å si sannheten den dagen verten flippet — og like gjerne
// blitt stående og løyet motsatt vei etterpå. Startverdien er `false`: laster
// oppsettet aldri, lover forsiden ingenting.
let _produksjonsmiljo = false;

export function settProduksjonsmiljo(pa) { _produksjonsmiljo = pa === true; }

export function produksjonsmiljo() { return _produksjonsmiljo; }

export function erTilgjengelig(id) {
  return erTilgjengeligFor(modulStatus(id), _produksjonsmiljo);
}

// Statuslinja i heltet var formulert i presens («agenten håndterer …»), men
// med null moduler i drift rendres hvert eneste tilbudspunkt under det som
// «Kommer». Forsiden motsa da seg selv innenfor én skjermhøyde (Codex P2).
// Teksten velges derfor av den SAMME `MODULSTATUS` som brikkene.
//
// Utrullingen har TRE tilstander, ikke to (Codex P2, andre runde): et
// `some()` lot den første modulen som gikk i drift slå på «alle områdene er
// i drift», selv om tre brikker fortsatt sa «Kommer» — samme selvmotsigelse,
// bare flyttet fra null til delvis. Den formen krever derfor at HVERT
// tilbudspunkt er i drift; er noen, men ikke alle, i drift, har delvis-formen
// sin egen tekst som peker på det som står merket «Tilgjengelig» og sier at
// resten bygges.
//
// Selve valget er skilt ut som en ren funksjon av tellingene, så alle tre
// tilstandene kan pinnes i test uten å forfalske `MODULSTATUS` — den
// beholder én kilde, og delvis-tilfellet trenger ikke vente på at en modul
// faktisk går i drift før noen oppdager at teksten er feil.
//
// Nøklene her sier BARE hvor langt utrullingen har kommet (Codex P2, tredje
// runde). Den samlede tilbudsbeskrivelsen — alle områdene Disponit dekker —
// lå en periode inni `tekst_bygges`, altså i den ene nøkkelen som forsvinner
// i det den FØRSTE modulen går i drift: hele beskrivelsen ville da falt bort
// og etterlatt den gamle firepunkts-innrammingen. Tilbudet er ikke en
// funksjon av utrullingen, så det står i `site.hero.tilbud`, som rendres
// uansett tilstand. Da har hver tekst én jobb, og det finnes ikke lenger en
// tilstand som mister innhold.
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
//
// Tallet er AVLEDET av den genererte katalogen, ikke skrevet av (Codex P1 på
// PR #99). Da v8 utvidet omfanget fra 45 til 55 moduler ble denne konstanten
// stående på 45, og statuslinja ville sagt «i drift av 45» mens katalogen den
// teller mot hadde 55 — en nevner ingen hadde bestemt, i en flate som skal
// være den ene sanne statuskilden.
const KATALOG_TOTALT = KATALOG_ANTALL;

export function plattformTelling() {
  const tell = (s) => Object.values(MODULSTATUS).filter((v) => v === s).length;
  const iDrift = tell("i_drift");
  const klargjort = tell("klargjort");
  const bygges = tell("bygges");
  return { iDrift, klargjort, bygges, underArbeid: klargjort + bygges,
    planlagt: KATALOG_TOTALT - iDrift - klargjort - bygges,
    totalt: KATALOG_TOTALT };
}
