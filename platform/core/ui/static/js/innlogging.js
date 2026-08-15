// Innloggingsflate. OIDC-start skjer som TOPPNIVÅ-NAVIGASJON via et ordinært
// same-origin <form method="post"> (klarsignal V2) — ALDRI fetch(), som ville
// prøvd å følge 303-redirecten til IdP-en gjennom CORS og feilet stille.
// provider_id kommer fra /ui/oppsett.json (deploy-satt per arbeidsområde),
// aldri hardkodet i klienten.
import { el, sett } from "./dom.js";
import { t } from "./i18n.js";
import { hentJson } from "./api.js";
import { Feiltilstand } from "./komponenter.js";
import { FASEOVERSIKT, MODULOVERSIKT, plattformTelling } from "./plattformdata.js";
import { siteFaseMerke, siteModuleKort, siteStatusMerke } from "./sitekomponenter.js";

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

  const telling = plattformTelling();
  const hoved = el("main", { id: "hovedinnhold", class: "skall-hoved site-shell",
    tabindex: "-1" },
    el("section", { class: "site-hero" },
      el("div", { class: "site-hero-copy" },
        el("p", { class: "site-eyebrow", text: t("site.hero.kicker") }),
        el("h1", { text: t("site.hero.tittel") }),
        el("p", { class: "site-hero-text", text: t("site.hero.tekst") }),
        el("div", { class: "site-inline-badges" },
          siteStatusMerke("i_drift"),
          siteStatusMerke("klargjort"),
          siteStatusMerke("bygges"),
          siteStatusMerke("planlagt"))),
      el("aside", { class: "kort site-hero-card" },
        el("p", { class: "site-eyebrow", text: t("site.hero.status") }),
        el("h2", { text: t("site.hero.status_tittel") }),
        el("div", { class: "site-kpi-row" },
          el("div", { class: "site-kpi" },
            el("strong", { text: `${telling.iDrift}/${telling.totalt}` }),
            el("span", { text: t("site.hero.kpi.i_drift") })),
          el("div", { class: "site-kpi" },
            el("strong", { text: String(telling.underArbeid) }),
            el("span", { text: t("site.hero.kpi.under_arbeid") })),
          el("div", { class: "site-kpi" },
            el("strong", { text: String(FASEOVERSIKT.length) }),
            el("span", { text: t("site.hero.kpi.faser") }))))),
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
          el("p", { class: "site-eyebrow", text: t("site.klarhet") }),
          el("h2", { text: t("site.klarhet_tittel") }))),
      el("div", { class: "site-grid site-grid-3" },
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("site.klarhet.status_tittel") }),
          el("p", { text: t("site.klarhet.status_tekst") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("site.klarhet.policy_tittel") }),
          el("p", { text: t("site.klarhet.policy_tekst") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("site.klarhet.deploy_tittel") }),
          el("p", { text: t("site.klarhet.deploy_tekst") })))),
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
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("site.moduler") }),
          el("h2", { text: t("site.moduler_tittel") })),
        el("span", { class: "site-inline-note", text: t("site.moduler_note") })),
      el("div", { class: "site-card-grid" },
        MODULOVERSIKT.map((mod) => siteModuleKort(mod)))),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("site.faser") }),
          el("h2", { text: t("site.faser_tittel") }))),
      el("div", { class: "site-card-grid" },
        FASEOVERSIKT.map((fase) =>
          el("article", { class: "site-module-card" },
            el("div", { class: "site-module-head" },
              el("strong", { text: t(fase.navn_nokkel) }),
              siteFaseMerke(fase.status)),
            el("p", { text: t(fase.tekst_nokkel) }))))),
    el("section", { class: "site-grid site-grid-2" },
      loginKort(provider, "kundeadmin", t("site.login.kunde_tittel"),
        t("site.login.kunde_tekst"), t("site.login.kunde_knapp")),
      loginKort(provider, "admin", t("site.login.admin_tittel"),
        t("site.login.admin_tekst"), t("site.login.admin_knapp"))));

  sett(app, hoved);
  app.setAttribute("aria-busy", "false");
  document.documentElement.setAttribute("data-visning", "landing");
}
