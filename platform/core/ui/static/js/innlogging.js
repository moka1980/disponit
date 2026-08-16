// Innloggingsflate. OIDC-start skjer som TOPPNIVÅ-NAVIGASJON via et ordinært
// same-origin <form method="post"> (klarsignal V2) — ALDRI fetch(), som ville
// prøvd å følge 303-redirecten til IdP-en gjennom CORS og feilet stille.
// provider_id kommer fra /ui/oppsett.json (deploy-satt per arbeidsområde),
// aldri hardkodet i klienten.
import { el, sett } from "./dom.js";
import { t, sprak, lagreSprak, hentI18n } from "./i18n.js";
import { hentJson } from "./api.js";
import { Feiltilstand, lokaliserSkiplenke, meldLive,
  settDokumenttittel } from "./komponenter.js";
import { TILBUD, erTilgjengelig, settProduksjonsmiljo, heroTekstNokkel } from "./plattformdata.js";
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
// `data_sv` måles mot `docs/DEPLOY.md` (produksjon er en egen maskin som
// settes opp når fase 1 nærmer seg pilot — dagens Cloud Server er staging
// og deles med et annet produkt), og `kontroll_sv` mot
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
// BARE DET SISTE BYTTET FÅR TEGNE (Codex P2). Byttet har TO ventepunkter, ikke
// ett: locale-settet, og så `/ui/oppsett.json` inne i `visInnlogging`. Vernet
// på det første — et nyere valg overtok mens settet ble hentet — slapp et
// forlatt bytte videre inn i det andre, der det rendret ubetinget. Da tegnet
// det over flaten et nyere bytte nettopp bygde, med SITT oppsett-svar: gikk det
// ene kallet gjennom og det andre ikke, avgjorde rekkefølgen om forsiden viste
// innloggingsknappene eller «ikke tilgjengelig», og fokus ble revet til en
// flate som allerede var erstattet.
//
// `byttNr` er den ene sannheten om hvilket bytte som eier flaten — samme regel
// som `omstartNr` i `app.js`, og den bæres HELE veien: `visInnlogging` får
// `gjelderFortsatt` og sjekker den etter sitt eget ventepunkt, rett før den
// rører DOM-en.
//
// Settet bæres med på samme vis, og tas i bruk først der (Codex P2). Byttet
// begynner ikke i det locale-svaret kommer, men i det flaten skiftes: tok vi
// språket i bruk med én gang, sto den norske forsiden merket `lang="en"` helt
// til oppsettskallet var ferdig — og hang det kallet, sto den slik for godt.
let byttNr = 0;

async function byttTil(s) {
  const nr = ++byttNr;
  lagreSprak(s);              // best effort — kan være nektet, og det er greit
  const i18n = await hentI18n(s);
  if (nr !== byttNr) return;                // forbigått av et nyere valg
  await visInnlogging({ fokuserSprak: true, i18n,
    gjelderFortsatt: () => nr === byttNr });
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
  ["site.svar.data_sp", "site.svar.data_sv"],
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

// `gjelderFortsatt` er kallerens rett til å tegne, målt ETTER ventepunktet
// under: den som kalte kan ha blitt forbigått mens oppsettet ble hentet, og et
// forlatt kall skal da trekke seg stille i stedet for å skrive over flaten som
// står. Uten opsjonen tegner flaten alltid — det er riktig for førstelasten og
// for `tilInnlogging`, som ikke konkurrerer med noen.
//
// `i18n` er et hentet, men ikke ibruktatt locale-sett fra `hentI18n`. Det tas i
// bruk her, ett skritt før treet bygges, slik at språket og flaten som bærer
// det skifter i samme omgang. Uten opsjonen står språket som det står — riktig
// for `tilInnlogging`, som ikke bytter språk i det hele tatt.
function tilbudSeksjon() {
  return el("section", { class: "kort site-section" },
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
            el("p", { text: t(post.tekst_nokkel) })))));
}

function problemSeksjon() {
  return el("section", { class: "kort site-section" },
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
          el("p", { text: t("site.problem.etterpa_tekst") }))));
}

function argumentSeksjon() {
  return el("section", { class: "site-grid site-grid-3" },
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
        el("p", { text: t("site.argument.kostnad_tekst") })));
}

function arbeidsflytSeksjon() {
  return el("section", { class: "kort site-section" },
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
          el("p", { text: t("site.arbeidsflyt.evidens_tekst") }))));
}

function svarSeksjon() {
  return el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("site.svar") }),
          el("h2", { text: t("site.svar_tittel") }))),
      el("dl", { class: "site-list" },
        SPORSMAL.map(([sp, sv]) =>
          el("div", {},
            el("dt", {}, el("strong", { text: t(sp) })),
            el("dd", { text: t(sv) })))));
}

let _sidelytter = null;

// ---------------------------------------------------------------------------
// SIDER. Forsiden var ÉN lang rulle: tilbud, katalog, problem, arbeidsflyt,
// spørsmål og innlogging under hverandre. Spesifikasjonen
// (`prototype/Ai-bedriftsagent-prototype-v5.html` §2.1/§2.3) sier det motsatte:
// hovednavigasjon med 5–7 elementer på topp, minimalistisk innhold, maks tre
// handlingsvalg per skjermbilde, alt innen to klikk.
//
// Hver side er en egen visning, og bare ÉN er i DOM-en om gangen. Det er ikke
// bare mindre støy: en skjermleser slipper å vandre gjennom seksjoner ingen ba
// om, og «hvor er jeg» besvares av `aria-current` i stedet for rullehøyden.
const SIDER = [
  { nokkel: "hjem", bygg: (ctx) => sideHjem(ctx) },
  { nokkel: "tjenester", bygg: () => sideTjenester() },
  { nokkel: "slik", bygg: () => sideSlik() },
  { nokkel: "om", bygg: () => sideOm() },
  { nokkel: "logg-inn", bygg: (ctx) => sideLoggInn(ctx) },
];

function sidetittel(tekst) {
  // `tabindex="-1"` gjør overskriften fokuserbar uten å legge den i
  // tab-rekkefølgen: fokus flyttes hit ved sidebytte, ellers står det igjen på
  // lenka man klikket og bytte av innhold blir usynlig for tastatur og
  // skjermleser (WCAG 2.4.3 / 4.1.3).
  return el("h1", { id: "sidetittel", tabindex: "-1", text: tekst });
}

function sideHjem(ctx) {
  return el("div", {},
    el("section", { class: "site-hero" },
      el("div", { class: "site-hero-copy" },
        el("p", { class: "site-eyebrow", text: t("site.hero.kicker") }),
        sidetittel(t("site.hero.tittel")),
        el("p", { class: "site-hero-text", text: t(heroTekstNokkel()) }),
        // Maks tre handlingsvalg (§2.1). Her er det to.
        el("div", { class: "site-cta" },
          el("a", { class: "knapp primar", href: "#/logg-inn",
            text: t("site.cta.logg_inn") }),
          el("a", { class: "knapp", href: "#/tjenester",
            text: t("site.cta.se_tjenester") }))),
      el("aside", { class: "kort site-hero-card" },
        el("p", { class: "site-eyebrow", text: t("site.hero.punkter") }),
        el("h2", { text: t("site.hero.punkter_tittel") }),
        el("ul", { class: "site-list" },
          el("li", { text: t("site.hero.punkt.fullmakt") }),
          el("li", { text: t("site.hero.punkt.stopp") }),
          el("li", { text: t("site.hero.punkt.spor") })))));
}

function sideTjenester() {
  return el("div", {},
    sidetittel(t("site.nav.tjenester")),
    el("p", { class: "site-hero-text", text: t("site.tjenester_ingress") }),
    tilbudSeksjon(),
    katalogseksjon());
}

function sideSlik() {
  return el("div", {},
    sidetittel(t("site.nav.slik")),
    arbeidsflytSeksjon(),
    problemSeksjon());
}

function sideOm() {
  return el("div", {},
    sidetittel(t("site.nav.om")),
    argumentSeksjon(),
    svarSeksjon());
}

function sideLoggInn(ctx) {
  return el("div", {},
    sidetittel(t("site.nav.logg_inn")),
    el("section", { class: "site-grid site-grid-2" },
      loginKort(ctx.provider, "kundeadmin", t("site.login.kunde_tittel"),
        t("site.login.kunde_tekst"), t("site.login.kunde_knapp")),
      loginKort(ctx.provider, "admin", t("site.login.admin_tittel"),
        t("site.login.admin_tekst"), t("site.login.admin_knapp"))));
}

// EN HASH ER IKKE ALLTID EN RUTE (Codex P2). `#/tjenester` er en rute;
// `#hovedinnhold` fra hopp-lenka er et FRAGMENT — et sted på siden nettleseren
// skal flytte fokus til, ikke en side som skal byttes. Forskjellen står i
// `#/`-prefikset, og uten den skilnaden svelget «ukjent rute → hjem» fragmentet
// og kastet brukeren tilbake til Hjem: den som sto på Tjenester og trykket
// «Hopp til innhold» mistet siden de leste, midt i handlingen som skulle spare
// dem for tastetrykk. Tom hash teller som rute (hjem) — går man tilbake til
// `/` uten fragment, er det en ekte navigasjon til forsiden.
function erRute(hash) {
  const h = hash || "";
  return h === "" || h === "#" || h.startsWith("#/");
}

// SIDEN ER EN TILSTAND, IKKE EN AVLESNING AV HASH-EN (Codex P2). Vernet lå
// først bare i `hashchange`-lytteren, og det dekket nøyaktig ett tilfelle: selve
// hash-byttet. Men flaten bygges også på nytt UTEN at hash-en rører seg — et
// språkbytte kaller `visInnlogging` om igjen — og sto hash-en da på
// `#hovedinnhold` etter et hopp, leste denne funksjonen fragmentet på nytt og
// svarte «hjem». Brukeren som sto på Tjenester, hoppet til innholdet og byttet
// språk, kom tilbake til Hjem uten å ha bedt om det.
//
// Derfor holder vi hvilken side som står, og hash-en får bare endre den når den
// FAKTISK er en rute. Ukjent rute faller fortsatt tilbake til hjem —
// `#/finnes-ikke` skal ikke gi en tom side — og et fragment endrer ingenting,
// uansett hvem som spør og hvor mange ganger flaten bygges om.
let _aktivSide = "hjem";

function gjeldendeSide() {
  const hash = window.location.hash || "";
  if (!erRute(hash)) return _aktivSide;
  const n = hash.replace(/^#\//, "");
  _aktivSide = SIDER.some((x) => x.nokkel === n) ? n : "hjem";
  return _aktivSide;
}

// Sidens navn, ett sted. Nav-lenka og dokumenttittelen skal si det SAMME —
// står det «Tjenester» i navigasjonen og noe annet i faneveksleren, er det to
// navn på én side. (`logg-inn` → `logg_inn`: nøkler tar ikke bindestrek.)
function sidenavn(nokkel) {
  return t(`site.nav.${nokkel.replace("-", "_")}`);
}

// Fem elementer, innenfor §2.3 sine 5–7, i fast rekkefølge — så plasseringen
// er forutsigbar fra side til side.
function hovednav(aktiv) {
  return el("nav", { class: "site-nav", "aria-label": t("site.nav.merkelapp") },
    el("ul", { class: "site-nav-liste" },
      SIDER.map((side) => {
        const a = el("a", { class: "site-nav-lenke", href: `#/${side.nokkel}`,
          text: sidenavn(side.nokkel) });
        // `aria-current` svarer på «hvor er jeg». Markeringen er BÅDE farge og
        // en understrek — farge alene er ikke informasjon (WCAG 1.4.1).
        if (side.nokkel === aktiv) {
          a.setAttribute("aria-current", "page");
          a.classList.add("valgt");
        }
        return el("li", {}, a);
      })));
}

export async function visInnlogging(opsjoner = {}) {
  const gjelderFortsatt = opsjoner.gjelderFortsatt || (() => true);
  const app = document.getElementById("app");
  let provider = null;
  // Miljøet avgjør om forsiden kan LOVE noe, og avlesningen er fail-closed:
  // bare den eksakte strengen teller, så et manglende felt eller en feilet
  // henting koster et løfte i stedet for å gi et. Verdien HOLDES lokalt her —
  // se skrivepunktet under.
  let iProduksjon = false;
  try {
    const o = await hentJson("/ui/oppsett.json");
    provider = o && typeof o.provider_id === "string" ? o.provider_id : null;
    iProduksjon = !!(o && o.miljo === "produksjon");
  } catch {
    provider = null;
    iProduksjon = false;
  }
  // Sjekken står FØR treet bygges, ikke bare før `sett`: er kallet forbigått,
  // er også dette oppsett-svaret gammelt, og ingenting av det skal på skjermen.
  if (!gjelderFortsatt()) return;

  // Nå — og ikke tidligere — er alt til stede for å bytte flaten. `null` fra
  // `taIBruk` betyr at et nyere valg eier språket; da tegner vi ikke.
  if (opsjoner.i18n) {
    if (opsjoner.i18n.taIBruk() === null) return;
    // Hoppelenka står UTENFOR `#app` og overlever rendringen under (Codex P2).
    lokaliserSkiplenke();
  }

  // ET FORBIGÅTT SVAR SKRIVER IKKE MILJØET (Codex P2). `settProduksjonsmiljo`
  // sto FØR eierskapssjekkene, og skrev derfor en global verdi som kallet selv
  // straks etterpå kunne miste retten til å bruke: rakk to språkbytter å
  // overlappe, og det tapende oppsettkallet svarte sist — fordi det feilet
  // eller manglet `miljo` — sto `false` igjen etter at vinneren hadde skrevet
  // `true`. Rendringen ble riktig nok forkastet, men verdien ble stående.
  //
  // Med sider som bygges LAZY er det ikke lenger en skrivefeil som forsvinner
  // ved neste tegning: Tjenester bygges først når man navigerer dit, og leser
  // da `erTilgjengelig()` mot den stale verdien. En bruker som aldri gjorde
  // noe galt fikk hele modultilbudet merket «Kommer» — og heltet fikk teksten
  // som hører til en tom plattform — på en vert som var i produksjon.
  //
  // Skrivepunktet hører derfor ETTER begge eierskapssjekkene: den som eier
  // flaten er den som eier miljøet den leses i.
  settProduksjonsmiljo(iProduksjon);

  const ctx = { provider };
  const navplass = el("div", { class: "site-navplass" });
  // HOPP-MÅLET ER `<main>`, OG TOPPLINJA STÅR UTENFOR DET (Codex P2). Headeren
  // lå inne i `#hovedinnhold`, altså inne i målet for `.hoppelenke`: en
  // tastaturbruker som hoppet «til innhold» landet på toppen AV navigasjonen
  // den skal forbi, og neste Tab gikk gjennom merket, alle fem nav-lenkene og
  // språkknappene. En hopp-lenke som ikke hopper over den gjentatte
  // navigasjonen gjør ingenting (WCAG 2.4.1) — den koster bare et tastetrykk.
  // Formen er den samme som `AppShell` allerede har: et skall-element eier
  // topplinja, og `<main>` eier bare sidens eget innhold.
  const visning = el("main", { id: "hovedinnhold", class: "site-visning",
    tabindex: "-1" });
  const hoved = el("div", { class: "skall-hoved site-shell" },
    el("header", { class: "site-topp" },
      el("a", { class: "site-merke", href: "#/hjem",
        text: t("app.navn", "Disponit") }),
      navplass,
      sprakvelger()),
    visning);

  const tegnSide = (fokuser) => {
    const aktiv = gjeldendeSide();
    sett(navplass, hovednav(aktiv));
    sett(visning, SIDER.find((x) => x.nokkel === aktiv).bygg(ctx));
    document.documentElement.setAttribute("data-side", aktiv);
    // Sidebyttet er ikke ferdig før siden HETER noe (Codex P2): tittelen er
    // det historikk, bokmerker og faneveksler kjenner siden på, og den følger
    // språket fordi navnet hentes fra locale-settet ved hvert bytte.
    settDokumenttittel(sidenavn(aktiv));
    if (!fokuser) return;
    const h = visning.querySelector("#sidetittel");
    if (h) h.focus();
    if (h) meldLive(h.textContent);
  };

  // ÉN lytter. Forsiden tegnes på nytt ved språkbytte, og to lyttere ville
  // tegnet to ganger per klikk — og den gamle ville skrevet inn i et tre som
  // ikke lenger er på skjermen.
  if (_sidelytter) window.removeEventListener("hashchange", _sidelytter);
  _sidelytter = () => {
    // Lytteren skal ALDRI tegne inn i et tre som ikke står på skjermen. Etter
    // innlogging eier app-ruteren hash-en (`#/oversikt` osv.), og denne
    // visningen er for lengst byttet ut — uten denne sjekken ville forsiden
    // bygget seg selv på nytt, i det stille, inne i en løsrevet node ved hvert
    // eneste rutebytte i appen. Samme sjekk gjør testene uavhengige av
    // hverandre: en hash som nullstilles for NESTE test tegner ikke i denne.
    if (!visning.isConnected) {
      window.removeEventListener("hashchange", _sidelytter);
      return;
    }
    // …og den skal heller ikke tegne når hash-en ikke er en rute (Codex P2).
    // Hopp-lenka setter `#hovedinnhold`: nettleseren flytter fokus dit selv, og
    // det er ALT som skal skje. Uten denne linja gikk fragmentet gjennom
    // «ukjent rute → hjem», og et hopp til innholdet byttet ut siden man sto
    // på — så fokus landet i et innhold brukeren aldri ba om.
    if (!erRute(window.location.hash)) return;
    tegnSide(true);
  };
  window.addEventListener("hashchange", _sidelytter);
  tegnSide(false);

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
