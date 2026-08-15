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
  start(s);
}

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
function tilInnlogging() {
  riveNedRuter();
  return visInnlogging();
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

function visApp(sesjon, utrulling = {}) {
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
}

// BARE DEN SISTE OMSTARTEN FÅR TEGNE (Codex P2). `byttSprak` venter ikke på
// `start`, og språkvelgeren i skallet står åpen hele veien mens locale, økt og
// utrulling hentes. På en treg linje rekker brukeren å velge om igjen, og da
// løper to omstarter side om side gjennom fire ventepunkter. Uten et skille
// vant den som tilfeldigvis kom sist i mål — altså kunne det FØRSTE valget
// rendre over det andre, og `_kart`, `<html lang>` og den markerte knappen
// ende på hvert sitt språk.
//
// `omstartNr` er den ene sannheten om hvilket valg som gjelder: `start` teller
// den opp med én gang, og etter HVERT ventepunkt sjekker den at den fortsatt
// er den siste. Er den ikke det, trekker den seg stille — inkludert på vei til
// innlogging, for et fall tilbake fra en forlatt omstart skal ikke rive ned
// flaten et nyere valg holder på å bygge. Locale-settet er vernet på samme vis
// inne i `lastI18n`, som returnerer `null` når det ble forbigått.
let omstartNr = 0;

// `valgtSprak` settes KUN av `byttSprak`. Ved første last er den udefinert, og
// da gjelder den vanlige rekkefølgen i `velgSprak` (lagret valg → dokumentets
// `data-sprak` → nettleseren → nb).
async function start(valgtSprak) {
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
    visApp(sesjon, utrulling);
  } catch (e) {
    if (!gjelderFortsatt()) return;
    if (e instanceof UautorisertFeil) { tilInnlogging(); return; }
    // Nettverk/annet på øktsjekk: fall til innlogging (ingen økt å stole på).
    tilInnlogging();
  }
}

start();
