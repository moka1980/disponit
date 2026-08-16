// Innloggingsflate. OIDC-start skjer som TOPPNIVÅ-NAVIGASJON via et ordinært
// same-origin <form method="post"> (klarsignal V2) — ALDRI fetch(), som ville
// prøvd å følge 303-redirecten til IdP-en gjennom CORS og feilet stille.
// provider_id kommer fra /ui/oppsett.json (deploy-satt per arbeidsområde),
// aldri hardkodet i klienten.
import { el, sett } from "./dom.js";
import { t, sprak, lagreSprak, hentI18n } from "./i18n.js";
import { hentJson } from "./api.js";
import { Feiltilstand, lokaliserSkiplenke } from "./komponenter.js";
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
function lesSide() {
  const side = new URLSearchParams(window.location.search).get("side") || "hjem";
  return ["hjem", "tjenester", "produkt", "sikkerhet", "innlogging"].includes(side)
    ? side : "hjem";
}

// Hver offentlig lenke bærer språket (Codex P2). Navigasjonen her er ordinære
// `href`-er som laster dokumentet på nytt, og etter en reload finnes valget
// bare der det er SKREVET: i lageret — som kan være nektet — eller i URL-en.
// Derfor bygges ingen offentlig lenke for hånd lenger; alle går gjennom denne,
// og `velgSprak` leser leddet i den andre enden.
function offentligUrl(side, ekstra = {}) {
  const p = new URLSearchParams();
  if (side && side !== "hjem") p.set("side", side);
  for (const [n, v] of Object.entries(ekstra)) if (v) p.set(n, v);
  p.set("sprak", sprak());
  return `/?${p.toString()}`;
}

function offentligTopp(side) {
  const lenker = ["hjem", "tjenester", "produkt", "sikkerhet", "innlogging"];
  const nav = el("nav", { class: "site-hovednav", "aria-label": t("site.nav.hoved") },
    el("ul", {}, lenker.map((nokkel) => {
      const attrs = {
        href: offentligUrl(nokkel),
        text: t(`site.nav.${nokkel}`),
      };
      if (nokkel === side) attrs["aria-current"] = "page";
      return el("li", {}, el("a", attrs));
    })));
  // Søket er en GET-form: den bygger sin egen URL av feltene sine, så språket
  // må stå som et felt her — ellers mister nettopp søket valget (Codex P2).
  const sok = el("form", { class: "site-sok", method: "get", action: "/" },
    el("input", { type: "hidden", name: "side", value: "tjenester" }),
    el("input", { type: "hidden", name: "sprak", value: sprak() }),
    el("label", { class: "sr-only", for: "site-sokefelt", text: t("site.sok.label") }),
    el("input", { id: "site-sokefelt", name: "q", type: "search",
      placeholder: t("site.sok.placeholder"), value: side === "tjenester"
        ? new URLSearchParams(window.location.search).get("q") || "" : "" }),
    el("button", { type: "submit", text: t("site.sok.knapp") }));
  return el("header", { class: "site-topp" },
    el("div", { class: "site-topp-rad" },
      el("a", { class: "site-logo", href: offentligUrl("hjem"),
        "aria-label": t("site.nav.logo") },
        el("span", { "aria-hidden": "true", text: "D" }),
        el("strong", { text: t("app.navn", "Disponit") })),
      nav,
      sprakvelger()),
    el("div", { class: "site-sok-rad" }, sok));
}

function katalogseksjon() {
  const sok = (new URLSearchParams(window.location.search).get("q") || "")
    .trim().toLocaleLowerCase(sprak());
  const omrader = OMRADER.map((omrade) => ({
    ...omrade,
    moduler: omrade.moduler.filter((n) =>
      !sok || t(`site.katalog.m${n}.navn`).toLocaleLowerCase(sprak()).includes(sok)),
  })).filter((omrade) => omrade.moduler.length);
  const antall = omrader.reduce((sum, omrade) => sum + omrade.moduler.length, 0);
  return el("section", { class: "kort site-section" },
    el("div", { class: "site-section-head" },
      el("div", {},
        el("p", { class: "site-eyebrow", text: t("site.katalog") }),
        el("h2", { text: t("site.katalog_tittel") })),
      el("span", { class: "site-inline-note", text: sok
        ? t("site.sok.resultat").replace("{antall}", antall)
        : t("site.katalog_note").replace("{antall}", KATALOG_ANTALL) })),
    antall === 0 ? el("p", { class: "site-tom", text: t("site.sok.tom") }) :
    el("div", { class: "site-grid site-grid-3" },
      omrader.map((omrade) =>
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
    // Retursti-en er den siste offentlige navigasjonen: språket må bli med
    // over OIDC-runden, ellers står skallet på `data-sprak="nb"` etter
    // innlogging for den som ikke kan lagre valget. `trygg_retursti` beholder
    // query-strengen på en lokal path-referanse, så leddet overlever turen.
    form.append(
      el("input", { type: "hidden", name: "provider_id", value: provider }),
      el("input", { type: "hidden", name: "retursti",
        value: `/?visning=${visning}&sprak=${sprak()}` }),
      el("button", { type: "submit", class: "knapp primar", text: knapp }));
    kort.append(form);
  } else {
    kort.append(Feiltilstand({ tittel: t("ui.feil_tittel"),
      tekst: t("ui.logg_inn_utilgjengelig") }));
  }
  return kort;
}

function tilbudseksjon() {
  return el("section", { class: "site-section site-tilbud" },
    el("div", { class: "site-section-head" }, el("div", {},
      el("p", { class: "site-eyebrow", text: t("site.tilbud") }),
      el("h2", { text: t("site.tilbud_tittel") }))),
    el("div", { class: "site-grid site-grid-2" }, TILBUD.map((post, indeks) =>
      el("article", { class: "site-feature" },
        el("span", { class: "site-feature-nr", "aria-hidden": "true",
          text: String(indeks + 1).padStart(2, "0") }),
        el("div", {},
          el("div", { class: "site-module-head" },
            el("h3", { text: t(post.navn_nokkel) }),
            siteTilbudMerke(erTilgjengelig(post.id))),
          el("p", { text: t(post.tekst_nokkel) }))))));
}

function hjemSide() {
  return [
    el("section", { class: "site-hero site-hero-ny" },
      el("div", { class: "site-hero-copy" },
        el("p", { class: "site-eyebrow", text: t("site.hero.kicker") }),
        el("h1", { text: t("site.hero.tittel") }),
        el("p", { class: "site-hero-text", text: t(heroTekstNokkel()) }),
        el("div", { class: "site-cta" },
          el("a", { class: "knapp primar", href: offentligUrl("tjenester"),
            text: t("site.cta.tjenester") }),
          el("a", { class: "knapp", href: offentligUrl("produkt"),
            text: t("site.cta.produkt") }))),
      el("aside", { class: "site-signal", "aria-label": t("site.hero.punkter_tittel") },
        el("span", { class: "site-orbit", "aria-hidden": "true" }),
        el("p", { class: "site-eyebrow", text: t("site.hero.punkter") }),
        el("h2", { text: t("site.hero.punkter_tittel") }),
        el("ul", { class: "site-checklist" },
          el("li", { text: t("site.hero.punkt.fullmakt") }),
          el("li", { text: t("site.hero.punkt.stopp") }),
          el("li", { text: t("site.hero.punkt.spor") })))),
    tilbudseksjon(),
    el("section", { class: "site-bunn-cta" },
      el("div", {}, el("p", { class: "site-eyebrow", text: t("site.cta.kicker") }),
        el("h2", { text: t("site.cta.tittel") })),
      el("a", { class: "knapp primar", href: offentligUrl("innlogging"),
        text: t("site.nav.innlogging") })),
  ];
}

function produktSide() {
  return [
    sideIntroduksjon("site.problem", "site.problem_tittel", "site.problem_tekst"),
    el("section", { class: "site-grid site-grid-3" },
      ["manuelt", "spredt", "etterpa"].map((n) => el("article", { class: "site-feature" },
        el("h2", { text: t(`site.problem.${n}_tittel`) }),
        el("p", { text: t(`site.problem.${n}_tekst`) })))),
    el("section", { class: "site-section" },
      el("p", { class: "site-eyebrow", text: t("site.arbeidsflyt") }),
      el("h2", { text: t("site.arbeidsflyt_tittel") }),
      el("ol", { class: "site-steg" },
        ["styring", "policy", "evidens"].map((n, i) => el("li", {},
          el("span", { "aria-hidden": "true", text: String(i + 1) }),
          el("div", {}, el("h3", { text: t(`site.arbeidsflyt.${n}_tittel`) }),
            el("p", { text: t(`site.arbeidsflyt.${n}_tekst`) })))))),
    svarseksjon(),
  ];
}

function sideIntroduksjon(kicker, tittel, tekst) {
  return el("header", { class: "site-sideintro" },
    el("p", { class: "site-eyebrow", text: t(kicker) }),
    el("h1", { text: t(tittel) }),
    el("p", { class: "site-hero-text", text: t(tekst) }));
}

function svarseksjon() {
  return el("section", { class: "site-section site-sporsmal" },
    el("p", { class: "site-eyebrow", text: t("site.svar") }),
    el("h2", { text: t("site.svar_tittel") }),
    el("dl", {}, SPORSMAL.map(([sp, sv]) => el("div", {},
      el("dt", { text: t(sp) }), el("dd", { text: t(sv) })))));
}

function sikkerhetSide() {
  return [
    sideIntroduksjon("site.security.kicker", "site.security.tittel", "site.security.tekst"),
    el("section", { class: "site-grid site-grid-3" },
      ["presisjon", "plattform", "kostnad"].map((n) =>
        el("article", { class: "site-feature" },
          el("p", { class: "site-eyebrow", text: t(`site.argument.${n}`) }),
          el("h2", { text: t(`site.argument.${n}_tittel`) }),
          el("p", { text: t(`site.argument.${n}_tekst`) })))),
    svarseksjon(),
  ];
}

function tjenesterSide() {
  return [
    sideIntroduksjon("site.katalog", "site.tjenester.tittel", "site.tjenester.tekst"),
    katalogseksjon(),
  ];
}

function innloggingSide(provider) {
  return [
    sideIntroduksjon("site.login.kicker", "site.login.tittel", "site.login.tekst"),
    el("section", { class: "site-grid site-grid-2" },
      loginKort(provider, "kundeadmin", t("site.login.kunde_tittel"),
        t("site.login.kunde_tekst"), t("site.login.kunde_knapp")),
      loginKort(provider, "admin", t("site.login.admin_tittel"),
        t("site.login.admin_tekst"), t("site.login.admin_knapp"))),
  ];
}

// Adresselinja skal si hvilket språk siden faktisk står på. Byttet skjer uten
// navigasjon, så uten dette pekte URL-en fortsatt på det forrige språket: en
// oppdatering (F5) eller en lenke kopiert ut av adressefeltet leser ingen
// minnetilstand, og med nektet lagring falt begge tilbake til norsk.
// `replaceState` og ikke `pushState`: et språkbytte er ikke et nytt sted i
// historikken, og «tilbake» skal føre dit brukeren kom fra.
// De fem offentlige sidene byttet bare kroppen; tittelen ble stående som den
// statiske `Disponit` fra `index.html` (Codex P2). Da sier verken fanen,
// historikken eller skjermleserens sideannonsering hvilken av dem som er åpen
// — fem åpne faner het det samme, og «tilbake» var et gjett. Tittelen settes
// derfor per rute, av samme navn som står i navigasjonen: det brukeren klikket
// på, er det siden heter. Mønsteret ligger i locale-settet, ikke her — også en
// tittel er visningstekst (RUTINER pkt. 5).
function settDokumenttittel(side) {
  document.title = t("site.dokumenttittel", "{side} – {app}")
    .replace("{side}", t(`site.nav.${side}`))
    .replace("{app}", t("app.navn", "Disponit"));
}

function offentligBunn() {
  return el("footer", { class: "site-footer" },
    el("strong", { text: t("app.navn", "Disponit") }),
    el("p", { text: t("site.footer.tekst") }),
    el("a", { href: offentligUrl("sikkerhet"), text: t("site.nav.sikkerhet") }));
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
export async function visInnlogging(opsjoner = {}) {
  const gjelderFortsatt = opsjoner.gjelderFortsatt || (() => true);
  const app = document.getElementById("app");
  let provider = null;
  try {
    const o = await hentJson("/ui/oppsett.json");
    provider = o && typeof o.provider_id === "string" ? o.provider_id : null;
    // Miljøet avgjør om forsiden kan LOVE noe. Settes FØR rendringen under, og
    // fail-closed: bare den eksakte strengen teller, så et manglende felt
    // eller en feilet henting koster et løfte i stedet for å gi et.
    settProduksjonsmiljo(o && o.miljo === "produksjon");
  } catch {
    provider = null;
    settProduksjonsmiljo(false);
  }
  // Sjekken står FØR treet bygges, ikke bare før `sett`: er kallet forbigått,
  // er også dette oppsett-svaret gammelt, og ingenting av det skal på skjermen.
  if (!gjelderFortsatt()) return;

  // Nå — og ikke tidligere — er alt til stede for å bytte flaten. `null` fra
  // `taIBruk` betyr at et nyere valg eier språket; da tegner vi ikke.
  if (opsjoner.i18n) {
    // `taIBruk` speiler selv språket i URL-en: leddet skrives der språket
    // commites, ikke her, slik at også skallet holder URL-en à jour.
    if (opsjoner.i18n.taIBruk() === null) return;
    // Hoppelenka står UTENFOR `#app` og overlever rendringen under (Codex P2).
    lokaliserSkiplenke();
  }

  const side = lesSide();
  settDokumenttittel(side);
  const sideinnhold = side === "tjenester" ? tjenesterSide()
    : side === "produkt" ? produktSide()
      : side === "sikkerhet" ? sikkerhetSide()
        : side === "innlogging" ? innloggingSide(provider) : hjemSide();
  const hoved = el("main", { id: "hovedinnhold", class: "skall-hoved site-shell",
    tabindex: "-1" }, ...sideinnhold);

  sett(app, offentligTopp(side), hoved, offentligBunn());
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
