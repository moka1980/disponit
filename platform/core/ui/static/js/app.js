// M-1 kundeflate — inngang. Sjekker økten, viser innloggingsflate (401) eller
// bygger AppShell + ruteren (200). 401 og 403 holdes adskilt (V2): 401 →
// innlogging, 403 → ingen-tilgang PÅ flaten (håndteres i flatene).
import { sett } from "./dom.js";
import { velgSprak, lagreSprak, lastI18n, t, sprak } from "./i18n.js";
import { hentJson, hentUtrullingForSkall, loggUt, UautorisertFeil } from "./api.js";
import { AppShell, sikreLiveRegion, lokaliserSkiplenke } from "./komponenter.js";
import { Bekreftelsesdialog } from "./dialog.js";
import { lagRuter } from "./ruter.js";
import { visInnlogging } from "./innlogging.js";
import { visOversikt } from "./flater/oversikt.js";
import { visPolicy } from "./flater/policy.js";
import { visBeslutninger } from "./flater/beslutninger.js";
import { visUnntak } from "./flater/unntak.js";
import { visPolicyadmin } from "./flater/policyadmin.js";
import { visKundeadmin } from "./flater/kundeadmin.js";
import { visAdmin } from "./flater/admin.js";
import { byggRuter, hashForDypLenke, tillatteFlater } from "./sitekart.js";

const FLATER = {
  oversikt: visOversikt, policy: visPolicy,
  beslutninger: visBeslutninger, unntak: visUnntak,
  policyadmin: visPolicyadmin, kundeadmin: visKundeadmin,
  admin: visAdmin,
};

// Lagre valget, men ikke LIT på at lagringen gikk (Codex P2 til PR #42).
// `lagreSprak` svelger et nektet `localStorage` — privat modus, blokkerte
// tredjepartscookies, en herdet nettleser — og `location.reload()` ville da
// startet appen på nytt med det GAMLE språket, uten et eneste varsel.
// I stedet kjøres oppstarten på nytt med språket som argument: samme vei som
// ved første last (locale, skiplenke, økt, utrulling, skall), men valget bæres
// i modulen i stedet for i lageret. Lagringen er nå kun det som gjør at
// valget overlever til NESTE besøk.
function byttSprak(s) {
  lagreSprak(s);
  // `fokuserSprak`: kontrollen brukeren nettopp brukte blir skrevet ut av
  // DOM-en når skallet bygges på nytt, og fokus skal følge med over (Codex P2).
  start(s, { fokuserSprak: true });
}

// `omstartNr` er den ene sannheten om hvilket valg som gjelder: `start` teller
// den opp med én gang, og etter HVERT ventepunkt sjekker den at den fortsatt
// er den siste. Er den ikke det, trekker den seg stille — inkludert på vei til
// innlogging, for et fall tilbake fra en forlatt omstart skal ikke rive ned
// flaten et nyere valg holder på å bygge. Locale-settet er vernet på samme vis
// inne i `lastI18n`, som returnerer `null` når det ble forbigått.
let omstartNr = 0;

// Ruteren overlever ikke skallet sitt (Codex P2). `lagRuter` henger på et
// globalt `hashchange`, men rendrer inn i det `hoved`-elementet skallet hadde
// da den ble laget. Bygges skallet på nytt — språkbytte — eller forlates det
// helt — utlogging, tapt økt — er det elementet løsrevet, og en ruter som
// fortsatt lytter ville kjørt et helt sett API-kall for å tegne inn i et tre
// ingen ser. Nøyaktig ÉN ruter skal lytte om gangen: den som eier flaten på
// skjermen nå.
let aktivRuter = null;

function riveNedRuter() {
  if (aktivRuter) aktivRuter.stopp();
  aktivRuter = null;
}

// Alle veier tilbake til innlogging går herfra, så ruteren aldri blir stående
// igjen og lytte på en flate som er borte.
//
// Å rive ned ruteren er ikke nok (Codex P1). Bytter noen språk og logger ut
// mens `start()` venter på `/v1/utrulling`, er det svaret allerede autorisert
// — det kommer tilbake ETTER utloggingen, `gjelderFortsatt()` sier fortsatt
// ja, og omstarten kaller `visApp()` med økten og tenantdataene fra FØR
// utloggingen. Innloggingssiden byttes da ut med et tilsynelatende
// autentisert skall, og en flate uten API-kall (`kundeadmin`) kunne blitt
// stående synlig på ubestemt tid.
//
// Omstartsgenerasjonen telles derfor opp her: enhver omstart som er underveis
// blir forbigått i samme øyeblikk som vi går til innlogging, uansett hvor i
// ventekjeden den står.
//
// Overgangen tar samtidig eierskapet til flaten, ikke bare fra andre — også
// for seg selv. Den har nemlig sitt EGET ventepunkt: `visInnlogging` henter
// `/ui/oppsett.json` før den tegner. Blir overgangen forbigått mens den venter
// — en 401 fra en flate, og så et språkbytte i skallet som fortsatt sto på
// skjermen — skulle den ikke tegnet innloggingsflaten over det nyere valget.
// Nummeret bæres derfor med inn, samme regel som `byttNr` i `byttTil`.
function tilInnlogging() {
  const nr = ++omstartNr;
  riveNedRuter();
  return visInnlogging({ gjelderFortsatt: () => nr === omstartNr });
}

function bekreftLoggUt() {
  Bekreftelsesdialog({
    tittel: t("ui.logg_ut_bekreft_tittel"),
    tekst: t("ui.logg_ut_bekreft_tekst"),
    primarTekst: t("ui.logg_ut_bekreft_primar"),
    farlig: true,
    paaPrimar: async () => { await loggUt(); tilInnlogging(); },
  });
}

// FOKUS FØLGER MED NÅR SKALLET BYGGES PÅ NYTT (Codex P2). Et språkbytte i
// skallet erstatter hele treet — inkludert `<select>`-en brukeren står i.
// Uten `fokuserSprak` falt fokus til `<body>`, og en som styrer med tastatur
// måtte tabbe seg inn i siden på nytt for å se at byttet virket. Forsiden har
// hatt regelen hele tiden; skallet er den samme situasjonen, og får den nå
// også. Bare omstarter som KOMMER fra velgeren flytter fokus: førstelasten og
// et fall tilbake fra en flate skal ikke rykke brukeren ut av der de er.
function visApp(sesjon, utrulling = {}, opsjoner = {}) {
  // Før skallet skrives over: riv ned ruteren som eide det forrige.
  riveNedRuter();
  const app = document.getElementById("app");
  const tilgjengeligeRuter = byggRuter(sesjon);
  const skall = AppShell({
    tenant: sesjon.tenant, sprak: sprak(), aktiv: "oversikt", ruter: tilgjengeligeRuter,
    paaSprak: byttSprak, paaLoggUt: bekreftLoggUt,
  });
  sett(app, skall.rot);
  app.setAttribute("aria-busy", "false");
  document.documentElement.setAttribute("data-visning", "app");
  sikreLiveRegion();

  const ctx = {
    sprak: sprak(), scopes: sesjon.scopes || [], tenant: sesjon.tenant,
    // Tenantdata kommer fra `/v1/utrulling`, ikke fra klientpakken: serveren
    // har allerede avgjort hvilke rader økten får se. Mangler svaret (feil,
    // eller en økt uten `decisions:read`), står feltene tomme — og flatene
    // viser sin eksplisitte tomtilstand i stedet for å gjette.
    tenanter: Array.isArray(utrulling.tenanter) ? utrulling.tenanter : [],
    moduler: Array.isArray(utrulling.moduler) ? utrulling.moduler : null,
    paaUautorisert: () => tilInnlogging(),
  };
  // Ruteren ser BARE flatene økten har rute til: ellers ville `#/admin` skrevet
  // rett i adressefeltet rendret admin uten `security:read`, siden `gjeldende()`
  // validerer mot flatekartet — ikke mot menyen.
  const klientruter = lagRuter(skall.hoved, ctx,
    tillatteFlater(tilgjengeligeRuter, FLATER), skall.settAktiv);
  aktivRuter = klientruter;
  // Enten setter vi hash (og `hashchange` rendrer), ELLER så navigerer vi selv.
  // Begge deler ville rendret flaten to ganger på en dyplenke som
  // `/?visning=oversikt`: to sett API-kall, og en forbigående feil i det ene
  // kallet kunne vasket bort innholdet det andre nettopp hadde skrevet.
  const dypLenke = hashForDypLenke(window.location.search,
    window.location.hash, tilgjengeligeRuter);
  if (dypLenke) window.location.hash = dypLenke;
  else klientruter.naviger();

  // Etter rutingen, ikke før: den første `naviger()` flytter ikke fokus selv
  // (`forste` i `ruter.js`), men flaten skriver i `hoved` — og fokus skal ende
  // på velgeren i det ferdige skallet, ikke i noe som straks blir overskrevet.
  if (opsjoner.fokuserSprak && skall.velger) skall.velger.focus();
}

// BARE DEN SISTE OMSTARTEN FÅR TEGNE (Codex P2). `byttSprak` venter ikke på
// `start`, og språkvelgeren i skallet står åpen hele veien mens locale, økt og
// utrulling hentes. På en treg linje rekker brukeren å velge om igjen, og da
// løper to omstarter side om side gjennom fire ventepunkter. Uten et skille
// vant den som tilfeldigvis kom sist i mål — altså kunne det FØRSTE valget
// rendre over det andre, og `_kart`, `<html lang>` og den markerte knappen
// ende på hvert sitt språk. Eierskapsregelen selv står ved `omstartNr` øverst.
//
// `valgtSprak` settes KUN av `byttSprak`. Ved første last er den udefinert, og
// da gjelder den vanlige rekkefølgen i `velgSprak` (lagret valg → dokumentets
// `data-sprak` → nettleseren → nb).
async function start(valgtSprak, opsjoner = {}) {
  const nr = ++omstartNr;
  const gjelderFortsatt = () => nr === omstartNr;

  if (await lastI18n(valgtSprak || velgSprak()) === null) return;
  if (!gjelderFortsatt()) return;
  lokaliserSkiplenke();
  try {
    const sesjon = await hentJson("/v1/sesjon");
    if (!gjelderFortsatt()) return;
    // Utrullingen hentes ETTER at økten er bekreftet, og en feil her felles
    // ikke appen: alt annet enn 401 betyr bare at tenantdata mangler — flatene
    // har en tomtilstand for nettopp det. 401 slukes IKKE (se
    // `hentUtrullingForSkall`): økten kan ha blitt borte mellom de to kallene,
    // og da hører den hjemme i `catch`-en under, ikke i et tomt svar.
    const utrulling = await hentUtrullingForSkall(sprak());
    if (!gjelderFortsatt()) return;
    visApp(sesjon, utrulling, opsjoner);
  } catch (e) {
    if (!gjelderFortsatt()) return;
    if (e instanceof UautorisertFeil) { tilInnlogging(); return; }
    // Nettverk/annet på øktsjekk: fall til innlogging (ingen økt å stole på).
    tilInnlogging();
  }
}

start();
