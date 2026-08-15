// Innloggingsflate. OIDC-start skjer som TOPPNIVÅ-NAVIGASJON via et ordinært
// same-origin <form method="post"> (klarsignal V2) — ALDRI fetch(), som ville
// prøvd å følge 303-redirecten til IdP-en gjennom CORS og feilet stille.
// provider_id kommer fra /ui/oppsett.json (deploy-satt per arbeidsområde),
// aldri hardkodet i klienten.
import { el, sett } from "./dom.js";
import { t, sprak, lagreSprak, lastI18n } from "./i18n.js";
import { hentJson } from "./api.js";
import { Feiltilstand } from "./komponenter.js";
import { TILBUD, erTilgjengelig, heroTekstNokkel, settProduksjonsmiljo,
  dataSvarNokkel } from "./plattformdata.js";
import { OMRADER, KATALOG_ANTALL } from "./katalog.js";

// Hele produktomfanget, gruppert slik en kjøper leser det: elleve områder, 45
// moduler, fire faser. Uten dette svarte forsiden bare på de fire punktene i
// «Hva du får» — og en besøkende kunne tro at det var alt vi tilbyr.
//
// Ingen statusbrikke per modul her. Katalogen er OMFANGET (hva plattformen
// dekker), ikke en leveranseplan, og 45 «Kommer»-merker ville gjort seksjonen
// til nettopp det byggeregnskapet forsiden ble ryddet for. Hva som kjører i
// dag står ett sted: brikkene i «Hva du får».
function katalogseksjon() {
  return el("section", { class: "kort site-section" },
    el("div", { class: "site-section-head" },
      el("div", {},
        el("p", { class: "site-eyebrow", text: t("site.katalog") }),
        el("h2", { text: t("site.katalog_tittel") })),
      el("span", { class: "site-inline-note",
        text: t("site.katalog_note").replace("{antall}", KATALOG_ANTALL) })),
    el("div", { class: "site-grid site-grid-3" },
      OMRADER.map((omrade) =>
        el("article", { class: "site-mini-card" },
          el("strong", { text: t(`site.omrade.${omrade.id}`) }),
          el("ul", { class: "site-list site-list-tett" },
            omrade.moduler.map((n) =>
              el("li", { text: t(`site.katalog.m${n}.navn`) })))))));
}
import { siteTilbudMerke } from "./sitekomponenter.js";

// Spørsmålene en kjøper stiller i et møte, i den rekkefølgen de kommer.
// SVARENE ER PÅSTANDER OM SYSTEMET, IKKE SALGSTEKST: hvert av dem har en
// kilde i repoet, og avviker svaret fra kilden, er det svaret som er feil.
// Datasvaret VELGES av miljøet serveren oppgir, det er ikke en fast nøkkel (Codex
// P2). Det er den samme kilden brikkene i «Hva du får» leser, og det er hele
// poenget: en fast `data_sv` sa «i dag finnes bare staging» på en side som i
// seksjonen over kunne merke et tilbudspunkt «Tilgjengelig». To utelukkende
// påstander om det samme, i samme skjermbilde. Nå kan de ikke skille lag —
// flippes kilden, flyttes begge. Selve teksten måles fortsatt mot
// `docs/DEPLOY.md` sin miljøtabell, og `kontroll_sv` mot
// `policy_validator/engine.py` + `flater/unntak.js` (en policy-autorisert
// godkjenning KAN løfte nøyaktig den bundne grensen). Begge lovet mer enn
// koden bar (Codex P2) — endres et svar her, sjekk kilden først.
// Språkvalget må finnes FØR innlogging: en besøkende som ikke leser norsk
// skal kunne lese tilbudet, ikke bare finne bryteren etterpå — den lå bare i
// `AppShell`, altså bak en økt. En knapp per språk, ikke en `select`, fordi
// det er to valg og begge skal være synlige — da ser man at engelsk FINNES
// uten å åpne noe.
//
// Byttet LAGRER, men LITER IKKE PÅ at lagringen gikk (Codex P2). `lagreSprak`
// svelger et nektet `localStorage` — privat modus, blokkerte tredjeparts-
// cookies, en herdet nettleser — og en `location.reload()` ville da lest
// `index.html` sin `data-sprak="nb"` og gitt norsk tilbake. Nøyaktig de
// brukerne som trenger knappen mest ville sittet fast. Locale-settet lastes
// derfor rett inn i modulen og flaten rendres på nytt: valget lever i økten
// uansett hva lageret svarer, og lagringen er kun det som gjør at det
// overlever et nytt besøk.
// Bare det SISTE trykket får rendre (Codex P2 til PR #42). Knappene er to
// klikk fra hverandre, og hentingen av locale-settet tar tid: to raske trykk
// ga to gjennomløp om den samme flaten, og den tregeste rendret sist. Da
// kunne siden endt på et annet språk enn knappen brukeren trykket — og enn
// den som står merket `aria-current`. Nummeret tas ved inngangen; er det ikke
// lenger det høyeste, eier et nyere trykk flaten og dette trekker seg.
let byttNr = 0;
async function byttTil(s) {
  lagreSprak(s);              // best effort — kan være nektet, og det er greit
  const min = ++byttNr;
  await lastI18n(s);          // kilden til sannhet for DENNE økten
  if (min !== byttNr) return;
  await visInnlogging({ fokuserSprak: true });
}

// `lang` per knapp (Codex P2): etikettene ER på hvert sitt språk, og uten
// dette arver de sidens `lang`. En skjermleser på den norske forsiden ville
// da uttalt «English» med norsk uttale — og etter byttet «Norsk» med engelsk.
// Det er nøyaktig de to kontrollene en bruker trenger for å komme seg UT av
// et språk de ikke forstår, så de er de siste som tåler å bli lest feil.
// Attributtet står på knappen, ikke på `<nav>`: `aria-label`-en der er på
// sidens språk, mens hver etikett er på sitt eget.
function sprakvelger() {
  const valgt = sprak();
  return el("nav", { class: "site-sprak", "aria-label": t("ui.sprak") },
    ["nb", "en"].map((s) => {
      const knapp = el("button", {
        type: "button",
        lang: s,
        class: s === valgt ? "site-sprak-knapp valgt" : "site-sprak-knapp",
        text: t(`ui.sprak.${s}`),
      });
      if (s === valgt) knapp.setAttribute("aria-current", "true");
      else knapp.addEventListener("click", () => { byttTil(s); });
      return knapp;
    }));
}

const SPORSMAL = [
  ["site.svar.hvem_sp", "site.svar.hvem_sv"],
  ["site.svar.kontroll_sp", "site.svar.kontroll_sv"],
  ["site.svar.feil_sp", "site.svar.feil_sv"],
  ["site.svar.data_sp", dataSvarNokkel()],
  ["site.svar.start_sp", "site.svar.start_sv"],
];

// INGEN MODUL- ELLER FASESTATUS PÅ DEN PUBLIKE FORSIDEN. Statusen er ekte og
// bindende, men den hører hjemme bak innlogging: for en besøkende var det
// første tallet på siden «0/45 moduler i drift», og fire av fem seksjoner
// handlet om hva som ikke var levert ennå. Modulregisteret, produktfasene og
// KPI-ene ligger nå på adminflaten (`flater/admin.js`), der de er
// scope-gatede og leses av dem som faktisk styrer utrullingen.
// Forsiden svarer på hva Disponit GJØR for en bedrift.

function loginKort(provider, visning, tittel, tekst, knapp) {
  const kort = el("article", { class: "kort site-login-card" },
    el("h2", { text: tittel }),
    el("p", { text: tekst }));

  if (provider) {
    const form = el("form", { class: "innlogging-form", method: "post",
      action: "/v1/oidc/start" });
    form.append(
      el("input", { type: "hidden", name: "provider_id", value: provider }),
      el("input", { type: "hidden", name: "retursti", value: `/?visning=${visning}` }),
      el("button", { type: "submit", class: "knapp primar", text: knapp }));
    kort.append(form);
  } else {
    kort.append(Feiltilstand({ tittel: t("ui.feil_tittel"),
      tekst: t("ui.logg_inn_utilgjengelig") }));
  }
  return kort;
}

export async function visInnlogging(opsjoner = {}) {
  const app = document.getElementById("app");
  let provider = null;
  try {
    const o = await hentJson("/ui/oppsett.json");
    provider = o && typeof o.provider_id === "string" ? o.provider_id : null;
    // Miljøet avgjør om forsiden kan LOVE noe (brikka «Tilgjengelig» og
    // svaret om hvor dataene ligger). Det settes FØR rendringen under, og
    // fail-closed: bare den eksakte strengen `produksjon` teller, så et
    // manglende felt eller en skrivefeil koster et løfte i stedet for å gi et.
    settProduksjonsmiljo(o && o.miljo === "produksjon");
  } catch {
    provider = null;
    settProduksjonsmiljo(false);
  }

  const hoved = el("main", { id: "hovedinnhold", class: "skall-hoved site-shell",
    tabindex: "-1" },
    sprakvelger(),
    el("section", { class: "site-hero" },
      el("div", { class: "site-hero-copy" },
        el("p", { class: "site-eyebrow", text: t("site.hero.kicker") }),
        el("h1", { text: t("site.hero.tittel") }),
        el("p", { class: "site-hero-text", text: t(heroTekstNokkel()) })),
      el("aside", { class: "kort site-hero-card" },
        el("p", { class: "site-eyebrow", text: t("site.hero.punkter") }),
        el("h2", { text: t("site.hero.punkter_tittel") }),
        el("ul", { class: "site-list" },
          el("li", { text: t("site.hero.punkt.fullmakt") }),
          el("li", { text: t("site.hero.punkt.stopp") }),
          el("li", { text: t("site.hero.punkt.spor") })))),
    // TILBUDET først: hva kunden får, med en diskret tilgjengelighetsbrikke
    // per punkt. Ikke et byggeregnskap — «Kommer» sier det samme som
    // «planlagt» uten å gjøre forsiden til en statusrapport.
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("site.tilbud") }),
          el("h2", { text: t("site.tilbud_tittel") })),
        el("span", { class: "site-inline-note", text: t("site.tilbud_note") })),
      el("div", { class: "site-grid site-grid-2" },
        TILBUD.map((post) =>
          el("article", { class: "site-mini-card" },
            el("div", { class: "site-module-head" },
              el("strong", { text: t(post.navn_nokkel) }),
              siteTilbudMerke(erTilgjengelig(post.id))),
            el("p", { text: t(post.tekst_nokkel) }))))),
    katalogseksjon(),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("site.problem") }),
          el("h2", { text: t("site.problem_tittel") }))),
      el("p", { class: "site-hero-text", text: t("site.problem_tekst") }),
      el("div", { class: "site-grid site-grid-3" },
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("site.problem.manuelt_tittel") }),
          el("p", { text: t("site.problem.manuelt_tekst") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("site.problem.spredt_tittel") }),
          el("p", { text: t("site.problem.spredt_tekst") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("site.problem.etterpa_tittel") }),
          el("p", { text: t("site.problem.etterpa_tekst") })))),
    el("section", { class: "site-grid site-grid-3" },
      el("article", { class: "kort" },
        el("p", { class: "site-eyebrow", text: t("site.argument.presisjon") }),
        el("h2", { text: t("site.argument.presisjon_tittel") }),
        el("p", { text: t("site.argument.presisjon_tekst") })),
      el("article", { class: "kort" },
        el("p", { class: "site-eyebrow", text: t("site.argument.plattform") }),
        el("h2", { text: t("site.argument.plattform_tittel") }),
        el("p", { text: t("site.argument.plattform_tekst") })),
      el("article", { class: "kort" },
        el("p", { class: "site-eyebrow", text: t("site.argument.kostnad") }),
        el("h2", { text: t("site.argument.kostnad_tittel") }),
        el("p", { text: t("site.argument.kostnad_tekst") }))),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("site.arbeidsflyt") }),
          el("h2", { text: t("site.arbeidsflyt_tittel") }))),
      el("div", { class: "site-grid site-grid-3" },
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("site.arbeidsflyt.styring_tittel") }),
          el("p", { text: t("site.arbeidsflyt.styring_tekst") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("site.arbeidsflyt.policy_tittel") }),
          el("p", { text: t("site.arbeidsflyt.policy_tekst") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("site.arbeidsflyt.evidens_tittel") }),
          el("p", { text: t("site.arbeidsflyt.evidens_tekst") })))),
    // Rett svar på det en kjøper faktisk lurer på. Her hører honnørordene
    // hjemme — ikke i et byggeregnskap øverst på siden.
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("site.svar") }),
          el("h2", { text: t("site.svar_tittel") }))),
      el("dl", { class: "site-list" },
        SPORSMAL.map(([sp, sv]) =>
          el("div", {},
            el("dt", {}, el("strong", { text: t(sp) })),
            el("dd", { text: t(sv) }))))),
    el("section", { class: "site-grid site-grid-2" },
      loginKort(provider, "kundeadmin", t("site.login.kunde_tittel"),
        t("site.login.kunde_tekst"), t("site.login.kunde_knapp")),
      loginKort(provider, "admin", t("site.login.admin_tittel"),
        t("site.login.admin_tekst"), t("site.login.admin_knapp"))));

  sett(app, hoved);
  app.setAttribute("aria-busy", "false");
  document.documentElement.setAttribute("data-visning", "landing");

  // Knappen brukeren nettopp trykket på ble skrevet ut av DOM-en sammen med
  // resten av flaten. Uten dette havner fokus på `<body>`, og en som styrer
  // med tastatur må tabbe seg inn i siden på nytt for å se at byttet virket.
  // Fokus legges på det nå valgte språket — samme sted, ny tilstand.
  if (opsjoner.fokuserSprak) {
    const aktiv = app.querySelector('.site-sprak-knapp[aria-current="true"]');
    if (aktiv) aktiv.focus();
  }
}
