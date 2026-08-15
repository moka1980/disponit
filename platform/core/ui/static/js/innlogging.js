// Innloggingsflate. OIDC-start skjer som TOPPNIVÅ-NAVIGASJON via et ordinært
// same-origin <form method="post"> (klarsignal V2) — ALDRI fetch(), som ville
// prøvd å følge 303-redirecten til IdP-en gjennom CORS og feilet stille.
// provider_id kommer fra /ui/oppsett.json (deploy-satt per arbeidsområde),
// aldri hardkodet i klienten.
import { el, sett } from "./dom.js";
import { t, sprak, lagreSprak } from "./i18n.js";
import { hentJson } from "./api.js";
import { Feiltilstand } from "./komponenter.js";
import { TILBUD, erTilgjengelig, heroTekstNokkel } from "./plattformdata.js";
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
// `AppShell`, altså bak en økt. Samme mekanikk som app-skallet bruker
// (`lagreSprak` + reload): valget lagres, og siden rendres på nytt med det nye
// locale-settet. En knapp per språk, ikke en `select`, fordi det er to valg og
// begge skal være synlige — da ser man at engelsk FINNES uten å åpne noe.
function sprakvelger() {
  const valgt = sprak();
  return el("nav", { class: "site-sprak", "aria-label": t("ui.sprak") },
    ["nb", "en"].map((s) => {
      const knapp = el("button", {
        type: "button",
        class: s === valgt ? "site-sprak-knapp valgt" : "site-sprak-knapp",
        text: t(`ui.sprak.${s}`),
      });
      if (s === valgt) knapp.setAttribute("aria-current", "true");
      else {
        knapp.addEventListener("click", () => {
          lagreSprak(s);
          window.location.reload();
        });
      }
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

export async function visInnlogging() {
  const app = document.getElementById("app");
  let provider = null;
  try {
    const o = await hentJson("/ui/oppsett.json");
    provider = o && typeof o.provider_id === "string" ? o.provider_id : null;
  } catch { provider = null; }

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
}
