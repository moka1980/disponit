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
  // KLYNGEN «orden i eget hus» (3, 4, 5, 9, 21) — registrert 1/9
  // sammen med grensene m3-v1…m21-v1 og de tildelte
  // migrasjonsnumrene 092–096 (docs/KLYNGE-FUNDAMENT.md). Ordet er
  // `bygges` fordi manifestet finnes; ingen av de fem har kode ennå,
  // og ingen av dem flippes av en byggemilepæl — bare av en aksept.
  3: "bygges",      // m03_datakvalitet: under_utvikling, ikke_i_drift
  4: "bygges",      // m04_dataforvalter: under_utvikling, ikke_i_drift
  5: "bygges",      // m05_dokumentmal: under_utvikling, ikke_i_drift
  6: "bygges",      // m06_epost: under_utvikling, ikke_i_drift — PR-A
                    // (datamodell + retensjon) er fundamentet; flippes
                    // av en M-6-aksept, aldri av en byggemilepæl
  8: "bygges",      // m08_kalender: under_utvikling, ikke_i_drift —
                    // v1 er tidsvalg-benen (082); flippes av en
                    // M-8-aksept, aldri av en byggemilepæl
  9: "bygges",      // m09_kunnskap: under_utvikling, ikke_i_drift
  // m16_nokkeltall: ETTERREGISTRERT kjerneflate — nøkkeltallsflaten
  // kjører i produksjon (BASISRUTE `nokkeltall`), men MODULEN har
  // ingen aksepthendelse, og registerets regel (drift krever aktiv,
  // aktiv krever bevis) gjør `i_drift` usigelig uten en akseptdom.
  // Manifestet sier under_utvikling/ikke_i_drift om registerobjektet;
  // ordet her er avlesningen av DET, ikke av flaten. Se manifestets
  // hode for hva en ærligere avlesning krever.
  16: "bygges",
  21: "bygges",     // m21_avtalefrist: under_utvikling, ikke_i_drift
  // m31_modellstyring: under_utvikling, ikke_i_drift — registrert
  // 31/8 sammen med golden-sett-porten (086). Flippes av en
  // M-31-aksept, aldri av en byggemilepæl.
  31: "bygges",
  // m35_kontinuitet: under_utvikling, ikke_i_drift — registrert 31/8
  // sammen med kontinuitetsregisteret (089), øvelseslogikken og
  // m35-v1-grensen. Flippes av en M-35-aksept, aldri av en
  // byggemilepæl — og aldri av at plattformens backup er verifisert:
  // en verifisert backup er noe modulen MÅLER, ikke noe den er.
  35: "bygges",
  37: "bygges",     // m37_unntak: under_utvikling, ikke_i_drift
  // m38_ruter: under_utvikling, ikke_i_drift — etterregistrert 31/8
  // (fairness 085/#314 + policycachen #316 levert; aksept gjenstår).
  38: "bygges",
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
    // M-3/M-4/M-5/M-9: katalogfase 1, ikke plattform- og
    // sikkerhetsområdet — altså autopiloter, samme klasse som
    // M-6 og M-8. Fasen er plattformens utrullingsfase, ikke
    // katalogens områdeinndeling.
    id: 3,
    navn_nokkel: "site.modul.m3.navn",
    fase_nokkel: "site.fase.autopiloter",
    tekst_nokkel: "site.modul.m3.tekst",
  },
  {
    id: 4,
    navn_nokkel: "site.modul.m4.navn",
    fase_nokkel: "site.fase.autopiloter",
    tekst_nokkel: "site.modul.m4.tekst",
  },
  {
    id: 5,
    navn_nokkel: "site.modul.m5.navn",
    fase_nokkel: "site.fase.autopiloter",
    tekst_nokkel: "site.modul.m5.tekst",
  },
  {
    // M-6 er en operasjonsagent i kundens hverdag — samme klasse som
    // M-8 (også katalogfase 1): fasen her er plattformens
    // utrullingsfase, og agentene hører til autopilotene.
    id: 6,
    navn_nokkel: "site.modul.m6.navn",
    fase_nokkel: "site.fase.autopiloter",
    tekst_nokkel: "site.modul.m6.tekst",
  },
  {
    id: 8,
    navn_nokkel: "site.modul.m8.navn",
    fase_nokkel: "site.fase.autopiloter",
    tekst_nokkel: "site.modul.m8.tekst",
  },
  {
    id: 9,
    navn_nokkel: "site.modul.m9.navn",
    fase_nokkel: "site.fase.autopiloter",
    tekst_nokkel: "site.modul.m9.tekst",
  },
  {
    // M-16 hører til fase 2 i katalogen (analyse_og_ledelse), altså
    // `site.fase.operasjoner` — samme kilde som `katalog.js`.
    id: 16,
    navn_nokkel: "site.modul.m16.navn",
    fase_nokkel: "site.fase.operasjoner",
    tekst_nokkel: "site.modul.m16.tekst",
  },
  {
    // M-21 er katalogfase 2 (juridisk_og_compliance), altså
    // `site.fase.operasjoner` — samme kilde som `katalog.js`.
    id: 21,
    navn_nokkel: "site.modul.m21.navn",
    fase_nokkel: "site.fase.operasjoner",
    tekst_nokkel: "site.modul.m21.tekst",
  },
  {
    // M-31 hører til fase 3 i katalogen (plattform_og_sikkerhet,
    // fase: 3) — altså `site.fase.autopiloter`, samme kilde som
    // `katalog.js` (m16-regelen).
    id: 31,
    navn_nokkel: "site.modul.m31.navn",
    fase_nokkel: "site.fase.autopiloter",
    tekst_nokkel: "site.modul.m31.tekst",
  },
  {
    // M-35 hører til fase 4 i katalogen (it_og_drift, fase: 4) — altså
    // `site.fase.global`, samme kilde som `katalog.js` (m16/m31-regelen:
    // fasen leses av katalogen, den skrives aldri på nytt her).
    id: 35,
    navn_nokkel: "site.modul.m35.navn",
    fase_nokkel: "site.fase.global",
    tekst_nokkel: "site.modul.m35.tekst",
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
    scopes: ["decisions:read", "exceptions:read", "policy:read",
             "epost:read", "kontinuitet:read"],
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
