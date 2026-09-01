// M-1 kundeflate — inngang. Sjekker økten, viser innloggingsflate (401) eller
// bygger AppShell + ruteren (200). 401 og 403 holdes adskilt (V2): 401 →
// innlogging, 403 → ingen-tilgang PÅ flaten (håndteres i flatene).
import { sett } from "./dom.js";
import { velgSprak, lagreSprak, hentI18n, t, sprak } from "./i18n.js";
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
import { visVarsler } from "./flater/varsler.js";
import { visAdmin } from "./flater/admin.js";
import { visWcagKontroll } from "./flater/wcagkontroll.js";
import { visRekruttering } from "./flater/rekruttering.js";
import { visAdjudikator } from "./flater/adjudikator.js";
import { visNokkeltall } from "./flater/nokkeltall.js";
import { visModellstyring } from "./flater/modellstyring.js";
import { visEpost } from "./flater/epost.js";
import { visKontinuitet } from "./flater/kontinuitet.js";
import { visDriftstatus } from "./flater/driftstatus.js";
import { visDatakvalitet } from "./flater/datakvalitet.js";
import { visRetensjon } from "./flater/retensjon.js";
import { byggRuter, hashForDypLenke, tillatteFlater } from "./sitekart.js";

const FLATER = {
  oversikt: visOversikt, nokkeltall: visNokkeltall, policy: visPolicy,
  beslutninger: visBeslutninger, unntak: visUnntak,
  policyadmin: visPolicyadmin, kundeadmin: visKundeadmin,
  varsler: visVarsler,
  admin: visAdmin,
  wcagkontroll: visWcagKontroll,
  // M-57: ruten står i `sitekart.js` nå som serverendepunktene finnes.
  // Oppføringen her er uansett ikke i seg selv en vei inn —
  // `tillatteFlater` slipper bare gjennom flater som HAR en rute økten
  // fikk, så scope-gaten bor ett sted.
  rekruttering: visRekruttering,
  adjudikator: visAdjudikator,
  // M-31: basisrute bak `security:read` (sitekart.js) — scope-gaten
  // bor der, som for de andre flatene.
  modellstyring: visModellstyring,
  // M-6 PR-B: kildeflaten — scope-gaten (`epost:read`) bor i
  // sitekart.js som for de andre.
  epost: visEpost,
  // M-35 (089): basisrute bak `kontinuitet:read` (sitekart.js) —
  // scope-gaten bor der, som for de andre flatene. Skriveveiene i
  // flaten er i tillegg gated på `kontinuitet:write`, men det er
  // ergonomi: dørene i basen er den bindende porten.
  kontinuitet: visKontinuitet,
  // M-10 + M-11: basisrute bak `security:read` (sitekart.js) —
  // scope-gaten bor der, som for de andre flatene.
  driftstatus: visDriftstatus,
  // M-3 (092): basisrute bak `security:read` (sitekart.js) — scope-gaten
  // bor der. Den tverrgående funnlisten inne på flaten er i tillegg
  // gated på `platform:admin`, men det avgjøres av SERVEREN: flaten
  // tegner seksjonen bare når svaret sier `plattformdrift: true`.
  datakvalitet: visDatakvalitet,
  // M-4 (093): basisrute bak `security:read` (sitekart.js) —
  // scope-gaten bor der, som for de andre flatene.
  retensjon: visRetensjon,
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
// inne i `hentI18n`, hvis `taIBruk()` returnerer `null` når det ble forbigått.
let omstartNr = 0;

// Ruteren overlever ikke skallet sitt (Codex P2). `lagRuter` henger på et
// globalt `hashchange`, men rendrer inn i det `hoved`-elementet skallet hadde
// da den ble laget. Bygges skallet på nytt — språkbytte — eller forlates det
// helt — utlogging, tapt økt — er det elementet løsrevet, og en ruter som
// fortsatt lytter ville kjørt et helt sett API-kall for å tegne inn i et tre
// ingen ser. Nøyaktig ÉN ruter skal lytte om gangen: den som eier flaten på
// skjermen nå.
let aktivRuter = null;

// BARE DET NYESTE VARSELTALLET FÅR SKRIVE (Codex P2). `oppdaterVarseltall`
// kalles fra flere kanter — oppstarten, hver navigasjon, og varselflaten hver
// gang den merker noe lest eller melder av — og de kallene kan overlappe uten
// at noe skiller dem. Oppstartens kall kunne lese `uleste=3`, innboksen merke
// ett lest og skrive 2, og så kunne det trege førstekallet legge 3 tilbake
// oppå. Samme regel som `omstartNr` for omstartene og `EIERSKAP` for flatene:
// den som startet sist eier feltet. Nummeret er på MODULNIVÅ og ikke i
// skallets closure, slik at et svar fra et forrige skall heller ikke kan vinne
// over et kall fra det nye.
let varseltallNr = 0;

// …og det som lytter på vegne av telleren må kunne rives ned, som ruteren.
// Lytteren henger på `document`, mens `settVarsler` skriver inn i ETT bestemt
// skall — nøyaktig den bindingen `riveNedRuter` finnes for.
let varseltallStopp = null;

function riveNedRuter() {
  if (aktivRuter) aktivRuter.stopp();
  aktivRuter = null;
  if (varseltallStopp) varseltallStopp();
  varseltallStopp = null;
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
  // Modulmenyen i skallet får tenantens EGEN tildeling (Codex P2), samme kilde
  // og samme regel som kundeflaten: `/v1/utrulling` har allerede avgjort hvilke
  // moduler økten eier, og `null` betyr «vet ikke» — ikke «hele katalogen».
  const tildelteModuler = Array.isArray(utrulling.moduler)
    ? utrulling.moduler : null;
  const skall = AppShell({
    tenant: sesjon.tenant, sprak: sprak(), aktiv: "oversikt", ruter: tilgjengeligeRuter,
    brukerId: sesjon.bruker_id, epost: sesjon.epost, roller: sesjon.roller,
    moduler: tildelteModuler,
    paaSprak: byttSprak, paaLoggUt: bekreftLoggUt,
  });
  sett(app, skall.rot);
  app.setAttribute("aria-busy", "false");
  document.documentElement.setAttribute("data-visning", "app");
  sikreLiveRegion();

  // VARSELTELLEREN I SKALLET (Codex P2). Skallet har alltid hatt plassen, men
  // ingen fylte den: `visApp` sendte aldri `varsler`, så statusfeltet sa «ikke
  // tilgjengelig» i hver eneste økt — også når `/v1/varsel` hadde uleste
  // attesteringer å melde. Den som har valgt kun portal fikk dermed ingen
  // proaktiv beskjed i det hele tatt og måtte åpne varselflaten for å oppdage
  // om det fantes noe der.
  //
  // Bare for økter som HAR ruten: uten `policy:write`/`policy:activate` svarer
  // `/v1/varsel` 403, og et kall som er dømt til å feile hører ikke hjemme i
  // oppstarten. «Ikke tilgjengelig» er da også det sanne svaret.
  //
  // Feiler kallet, står feltet på «ikke tilgjengelig». Et tall er en påstand,
  // og skallet skal ikke hevde at det er rolig fordi en GET falt.
  const harVarsler = tilgjengeligeRuter.some((r) => r.nokkel === "varsler");
  const oppdaterVarseltall = () => {
    if (!harVarsler) return Promise.resolve();
    // Generasjonen tas FØR kallet, og sjekkes etter: et svar som ikke lenger
    // er det nyeste skriver ingenting. Uten dette kunne et tregt førstekall
    // legge et gammelt tall oppå et ferskere (se `varseltallNr`).
    const nr = ++varseltallNr;
    return hentJson("/v1/varsel?uleste=1")
      .then((d) => {
        if (nr !== varseltallNr) return;
        if (aktivRuter === klientruter) skall.settVarsler(d.uleste);
      })
      .catch(() => {});
  };

  const ctx = {
    sprak: sprak(), scopes: sesjon.scopes || [], tenant: sesjon.tenant,
    // Tenantdata kommer fra `/v1/utrulling`, ikke fra klientpakken: serveren
    // har allerede avgjort hvilke rader økten får se. Mangler svaret (feil,
    // eller en økt uten `decisions:read`), står feltene tomme — og flatene
    // viser sin eksplisitte tomtilstand i stedet for å gjette.
    tenanter: Array.isArray(utrulling.tenanter) ? utrulling.tenanter : [],
    moduler: tildelteModuler,
    paaUautorisert: () => tilInnlogging(),
    // Varselflaten er den eneste som ENDRER tallet — merker lest, melder av —
    // og må derfor kunne be skallet lese det på nytt. Uten dette sto telleren
    // på verdien fra innlastingen mens innboksen tømte seg foran øynene på
    // brukeren.
    oppdaterVarseltall,
  };
  // TELLEREN MÅ OGSÅ OPPDATERES AV ANDRES HANDLINGER (Codex P2). Fram til nå
  // var oppstartens ene kall den eneste automatiske hentingen i hele øktens
  // levetid — de andre kallstedene ligger i varselflaten og utløses bare av
  // at DENNE klienten merker noe lest eller åpner det. Åpner en annen
  // policyforvalter en runde etter at siden er lastet, var det ingenting som
  // spurte igjen: telleren kunne stå på null resten av økten, og den som har
  // valgt kun portal fikk aldri den proaktive beskjeden hele feltet finnes
  // for.
  //
  // To anledninger, og de er valgt fordi de er de eneste øyeblikkene et
  // ferskere tall kan endre noe for brukeren:
  //
  //   * HVER NAVIGASJON. Hun gjør noe i appen, og skallet står foran henne.
  //     Første navigasjon hoppes over — oppstartens eget kall under dekker
  //     den, og to kall om det samme ved hver innlasting er bare støy.
  //   * TILBAKE TIL FANEN. En SPA som har ligget i bakgrunnen er nettopp der
  //     et varsel rekker å oppstå.
  //
  // Ingen bakgrunnstimer. En `setInterval` måtte vært ryddet på HVER vei ut av
  // skallet (språkbytte, utlogging, tapt økt), og en timer som overlever
  // skallet sitt er samme feil som ruteren som overlevde sitt — bare stillere,
  // fordi den ikke tegner noe. Det som er igjen er en ærlig grense: står
  // brukeren helt i ro i en synlig fane, oppdateres tallet ikke før hun rører
  // seg. Da har hun heller ikke handlet på det.
  let forsteNavigasjon = true;
  const settAktivOgHentTall = (rute) => {
    skall.settAktiv(rute);
    if (forsteNavigasjon) { forsteNavigasjon = false; return; }
    oppdaterVarseltall();
  };
  // Ruteren ser BARE flatene økten har rute til: ellers ville `#/admin` skrevet
  // rett i adressefeltet rendret admin uten `security:read`, siden `gjeldende()`
  // validerer mot flatekartet — ikke mot menyen.
  const klientruter = lagRuter(skall.hoved, ctx,
    tillatteFlater(tilgjengeligeRuter, FLATER), settAktivOgHentTall);
  aktivRuter = klientruter;
  if (harVarsler) {
    const paaSynlighet = () => { if (!document.hidden) oppdaterVarseltall(); };
    document.addEventListener("visibilitychange", paaSynlighet);
    varseltallStopp = () =>
      document.removeEventListener("visibilitychange", paaSynlighet);
  }
  // Enten setter vi hash (og `hashchange` rendrer), ELLER så navigerer vi selv.
  // Begge deler ville rendret flaten to ganger på en dyplenke som
  // `/?visning=oversikt`: to sett API-kall, og en forbigående feil i det ene
  // kallet kunne vasket bort innholdet det andre nettopp hadde skrevet.
  const dypLenke = hashForDypLenke(window.location.search,
    window.location.hash, tilgjengeligeRuter);
  if (dypLenke) window.location.hash = dypLenke;
  else klientruter.naviger();

  // Etter at ruteren er satt: `oppdaterVarseltall` sjekker `aktivRuter` mot
  // nettopp denne, så et svar som kommer etter et språkbytte eller en
  // utlogging ikke skriver inn i et skall ingen ser.
  oppdaterVarseltall();

  // Etter rutingen, ikke før: den første `naviger()` flytter ikke fokus selv
  // (`forste` i `ruter.js`), men flaten skriver i `hoved` — og fokus skal ende
  // på velgeren i det ferdige skallet, ikke i noe som straks blir overskrevet.
  if (opsjoner.fokuserSprak && skall.velger) skall.velger.focus();
}

// Hoppelenka står utenfor `#app` og overlever rendringen av flaten, så den
// lokaliseres her — sammen med selve ibruktakingen av språket, aldri før den:
// før sto den på det nye språket mens flaten fortsatt sto på det gamle.
function taSpraketIBruk(i18n) {
  const s = i18n.taIBruk();
  if (s !== null) lokaliserSkiplenke();
  return s;
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

  // Settet HENTES her, men tas ikke i bruk før flaten som skal bære det er
  // klar (Codex P2). Mellom hentingen og skallet ligger to kall til: commitet
  // vi språket med én gang, sto skallet på skjermen med gammel tekst under et
  // `<html lang>` som allerede sa noe annet, og en navigasjon i det gapet
  // rendret en flate på det nye språket inn i det gamle treet.
  const i18n = await hentI18n(valgtSprak || velgSprak());
  if (!gjelderFortsatt()) return;
  try {
    const sesjon = await hentJson("/v1/sesjon");
    if (!gjelderFortsatt()) return;
    // Utrullingen hentes ETTER at økten er bekreftet, og en feil her felles
    // ikke appen: alt annet enn 401 betyr bare at tenantdata mangler — flatene
    // har en tomtilstand for nettopp det. 401 slukes IKKE (se
    // `hentUtrullingForSkall`): økten kan ha blitt borte mellom de to kallene,
    // og da hører den hjemme i `catch`-en under, ikke i et tomt svar.
    //
    // Den hentes på språket som er PÅ VEI INN, ikke på det som står: `sprak()`
    // svarer fortsatt det gamle helt til `taIBruk()` har kjørt, og
    // tenantteksten i svaret skal høre til skallet vi straks bygger.
    const utrulling = await hentUtrullingForSkall(i18n.sprak);
    if (!gjelderFortsatt()) return;
    // Alt som skal til for å bytte flaten er inne: språket tas i bruk, og
    // skallet bygges i samme omgang. `null` = et nyere valg eier språket nå.
    if (taSpraketIBruk(i18n) === null) return;
    visApp(sesjon, utrulling, opsjoner);
  } catch (e) {
    if (!gjelderFortsatt()) return;
    // Innloggingsflaten tegnes NÅ, så språket skal være i bruk før den bygges.
    // Ved første last er alternativet ikke «forrige språk», men ingen tekster
    // i det hele tatt: `_kart` er tomt til noe er tatt i bruk, og `t()` ville
    // gitt nøklene tilbake på en side ingen har logget inn på ennå.
    taSpraketIBruk(i18n);
    if (e instanceof UautorisertFeil) { tilInnlogging(); return; }
    // Nettverk/annet på øktsjekk: fall til innlogging (ingen økt å stole på).
    tilInnlogging();
  }
}

start();
