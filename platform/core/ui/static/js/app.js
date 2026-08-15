// M-1 kundeflate — inngang. Sjekker økten, viser innloggingsflate (401) eller
// bygger AppShell + ruteren (200). 401 og 403 holdes adskilt (V2): 401 →
// innlogging, 403 → ingen-tilgang PÅ flaten (håndteres i flatene).
import { sett } from "./dom.js";
import { velgSprak, lagreSprak, lastI18n, t, sprak } from "./i18n.js";
import { hentJson, hentUtrullingForSkall, loggUt, UautorisertFeil } from "./api.js";
import { AppShell, sikreLiveRegion } from "./komponenter.js";
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

function lokaliserSkiplenke() {
  const l = document.querySelector(".hoppelenke");
  if (l) l.textContent = t("ui.hopp_til_innhold");
}

// Ruteren eier en `hashchange`-lytter på `window`, og den overlever skallet
// sitt (Codex P2 til PR #42): et språkbytte — og en utlogging — bygger `#app`
// på nytt, men lytteren fra forrige skall satt igjen og pekte på et frakoblet
// `<main>`. Hver navigasjon rendret da BÅDE den nye og alle gamle ruterne, med
// hvert sitt sett API-kall (`/v1/oversikt` to ganger etter ett bytte, tre etter
// to). Derfor: nøyaktig én ruter av gangen, og den forrige rives før neste
// bygges — eller før flaten forsvinner helt.
let aktivRuter = null;
function rivRuter() {
  if (aktivRuter) aktivRuter.stopp();
  aktivRuter = null;
}

// Innloggingsflaten erstatter hele `#app`, altså også ruterens `<main>`.
// Den må rives i samme åndedrag, ellers navigerer en gammel ruter videre bak
// en flate som ikke har noen økt.
function tilInnlogging() {
  rivRuter();
  visInnlogging();
}

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
  rivRuter();
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

// `valgtSprak` settes KUN av `byttSprak`. Ved første last er den udefinert, og
// da gjelder den vanlige rekkefølgen i `velgSprak` (lagret valg → dokumentets
// `data-sprak` → nettleseren → nb).
async function start(valgtSprak) {
  await lastI18n(valgtSprak || velgSprak());
  lokaliserSkiplenke();
  try {
    const sesjon = await hentJson("/v1/sesjon");
    // Utrullingen hentes ETTER at økten er bekreftet, og en feil her felles
    // ikke appen: alt annet enn 401 betyr bare at tenantdata mangler — flatene
    // har en tomtilstand for nettopp det. 401 slukes IKKE (se
    // `hentUtrullingForSkall`): økten kan ha blitt borte mellom de to kallene,
    // og da hører den hjemme i `catch`-en under, ikke i et tomt svar.
    const utrulling = await hentUtrullingForSkall(sprak());
    visApp(sesjon, utrulling);
  } catch (e) {
    if (e instanceof UautorisertFeil) { tilInnlogging(); return; }
    // Nettverk/annet på øktsjekk: fall til innlogging (ingen økt å stole på).
    tilInnlogging();
  }
}

start();
