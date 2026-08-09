// M-1 kundeflate — inngang. Sjekker økten, viser innloggingsflate (401) eller
// bygger AppShell + ruteren (200). 401 og 403 holdes adskilt (V2): 401 →
// innlogging, 403 → ingen-tilgang PÅ flaten (håndteres i flatene).
import { el, sett } from "./dom.js";
import { velgSprak, lagreSprak, lastI18n, t, sprak } from "./i18n.js";
import { hentJson, loggUt, UautorisertFeil } from "./api.js";
import { AppShell, sikreLiveRegion } from "./komponenter.js";
import { Bekreftelsesdialog } from "./dialog.js";
import { lagRuter } from "./ruter.js";
import { visInnlogging } from "./innlogging.js";
import { visOversikt } from "./flater/oversikt.js";
import { visPolicy } from "./flater/policy.js";
import { visBeslutninger } from "./flater/beslutninger.js";
import { visUnntak } from "./flater/unntak.js";

const RUTER = [
  { nokkel: "oversikt" }, { nokkel: "policy" },
  { nokkel: "beslutninger" }, { nokkel: "unntak" },
];
const FLATER = {
  oversikt: visOversikt, policy: visPolicy,
  beslutninger: visBeslutninger, unntak: visUnntak,
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

function visApp(sesjon) {
  const app = document.getElementById("app");
  const skall = AppShell({
    tenant: sesjon.tenant, sprak: sprak(), aktiv: "oversikt", ruter: RUTER,
    paaSprak: byttSprak, paaLoggUt: bekreftLoggUt,
  });
  sett(app, skall.rot);
  app.setAttribute("aria-busy", "false");
  document.documentElement.setAttribute("data-visning", "app");
  sikreLiveRegion();

  const ctx = {
    sprak: sprak(), scopes: sesjon.scopes || [], tenant: sesjon.tenant,
    paaUautorisert: () => visInnlogging(),
  };
  const ruter = lagRuter(skall.hoved, ctx, FLATER, skall.settAktiv);
  ruter.naviger();
}

async function start() {
  await lastI18n(velgSprak());
  lokaliserSkiplenke();
  try {
    const sesjon = await hentJson("/v1/sesjon");
    visApp(sesjon);
  } catch (e) {
    if (e instanceof UautorisertFeil) { visInnlogging(); return; }
    // Nettverk/annet på øktsjekk: fall til innlogging (ingen økt å stole på).
    visInnlogging();
  }
}

start();
