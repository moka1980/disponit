// M-1 kundeflate — inngang. Sjekker økten, viser innloggingsflate (401) eller
// bygger AppShell + ruteren (200). 401 og 403 holdes adskilt (V2): 401 →
// innlogging, 403 → ingen-tilgang PÅ flaten (håndteres i flatene).
import { sett } from "./dom.js";
import { velgSprak, lagreSprak, lastI18n, t, sprak } from "./i18n.js";
import { hentJson, hentUtrulling, loggUt, UautorisertFeil } from "./api.js";
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

function byttSprak(s) {
  // Enkelt og korrekt: lagre valg og last på nytt (re-henter locale + rendrer
  // alt på det nye språket).
  lagreSprak(s);
  window.location.reload();
}

function bekreftLoggUt() {
  Bekreftelsesdialog({
    tittel: t("ui.logg_ut_bekreft_tittel"),
    tekst: t("ui.logg_ut_bekreft_tekst"),
    primarTekst: t("ui.logg_ut_bekreft_primar"),
    farlig: true,
    paaPrimar: async () => { await loggUt(); visInnlogging(); },
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
    paaUautorisert: () => visInnlogging(),
  };
  // Ruteren ser BARE flatene økten har rute til: ellers ville `#/admin` skrevet
  // rett i adressefeltet rendret admin uten `security:read`, siden `gjeldende()`
  // validerer mot flatekartet — ikke mot menyen.
  const klientruter = lagRuter(skall.hoved, ctx,
    tillatteFlater(tilgjengeligeRuter, FLATER), skall.settAktiv);
  // Enten setter vi hash (og `hashchange` rendrer), ELLER så navigerer vi selv.
  // Begge deler ville rendret flaten to ganger på en dyplenke som
  // `/?visning=oversikt`: to sett API-kall, og en forbigående feil i det ene
  // kallet kunne vasket bort innholdet det andre nettopp hadde skrevet.
  const dypLenke = hashForDypLenke(window.location.search,
    window.location.hash, tilgjengeligeRuter);
  if (dypLenke) window.location.hash = dypLenke;
  else klientruter.naviger();
}

async function start() {
  await lastI18n(velgSprak());
  lokaliserSkiplenke();
  try {
    const sesjon = await hentJson("/v1/sesjon");
    // Utrullingen hentes ETTER at økten er bekreftet, og en feil her felles
    // ikke appen: 401 håndteres av øktsjekken over, og alt annet betyr bare at
    // tenantdata mangler — flatene har en tomtilstand for nettopp det.
    const utrulling = await hentUtrulling(sprak()).catch(() => ({}));
    visApp(sesjon, utrulling);
  } catch (e) {
    if (e instanceof UautorisertFeil) { visInnlogging(); return; }
    // Nettverk/annet på øktsjekk: fall til innlogging (ingen økt å stole på).
    visInnlogging();
  }
}

start();
