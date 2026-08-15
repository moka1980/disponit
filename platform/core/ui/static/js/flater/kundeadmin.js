import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { flateHode } from "./felles.js";
import { modulerForTenant, tenantTelling } from "../plattformdata.js";
import { kanForvaltePolicy } from "../sitekart.js";
import { siteModuleKort, siteStatusMerke } from "../sitekomponenter.js";

export function visKundeadmin(hoved, ctx = {}) {
  // Flaten er åpen for hele kundeøkten, men policyADMINISTRASJONEN er det
  // ikke: uten `policy:write`/`policy:activate` peker snarveien på en flate
  // ruteren nekter, med aktiveringsknapper som uansett gir 403. Leseren får
  // lesevisningen `#/policy` i stedet — samme mønster som admin-flaten.
  const forvalter = kanForvaltePolicy(ctx);
  // Kundens arbeidsflate viser KUNDENS moduler, ikke plattformkatalogen: uten
  // dette meldte Bjørkli (tildelt M-1 og M-2) tre aktive moduler og viste M-37
  // som aktiv og M-38 under bygging. `null` = tildelingen er ukjent, og da sier
  // flaten det i stedet for å gjette.
  const mine = modulerForTenant(ctx.tenant);
  const moduler = mine || [];
  const telling = tenantTelling(moduler);
  const aktive = moduler.filter((mod) => mod.status === "i_drift");
  const bygges = moduler.filter((mod) => mod.status === "bygges");

  sett(hoved,
    ...flateHode(t("ui.kundeadmin.tittel"), t("ui.kundeadmin.undertittel")),
    el("div", { class: "site-grid site-grid-3" },
      el("section", { class: "kort site-hero-card" },
        el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.workspace") }),
        el("h2", { text: ctx.tenant || t("ui.kundeadmin.standard_tenant") }),
        el("p", { text: t("ui.kundeadmin.workspace_tekst") }),
        el("div", { class: "site-kpi-row" },
          el("div", { class: "site-kpi" },
            el("strong", { text: String(aktive.length) }),
            el("span", { text: t("ui.kundeadmin.kpi.aktive_moduler") })),
          el("div", { class: "site-kpi" },
            el("strong", { text: String(bygges.length) }),
            el("span", { text: t("ui.kundeadmin.kpi.bygges") })),
          el("div", { class: "site-kpi" },
            el("strong", { text: String(telling.planlagt) }),
            el("span", { text: t("ui.kundeadmin.kpi.planlagt") })))),
      el("section", { class: "kort" },
        el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.brukere") }),
        el("h2", { text: t("ui.kundeadmin.brukere_tittel") }),
        el("ul", { class: "site-list" },
          el("li", {}, el("strong", { text: t("ui.kundeadmin.rolle.leser") }), " ", t("ui.kundeadmin.rolle.leser_tekst")),
          el("li", {}, el("strong", { text: t("ui.kundeadmin.rolle.godkjenner") }), " ", t("ui.kundeadmin.rolle.godkjenner_tekst")),
          el("li", {}, el("strong", { text: t("ui.kundeadmin.rolle.policyforvalter") }), " ", t("ui.kundeadmin.rolle.policyforvalter_tekst")))),
      el("section", { class: "kort" },
        el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.integrasjoner") }),
        el("h2", { text: t("ui.kundeadmin.integrasjoner_tittel") }),
        el("ul", { class: "site-list" },
          el("li", {}, t("ui.kundeadmin.integrasjon.regnskap")),
          el("li", {}, t("ui.kundeadmin.integrasjon.epost")),
          el("li", {}, t("ui.kundeadmin.integrasjon.bank")),
          el("li", {}, t("ui.kundeadmin.integrasjon.idp"))))),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.plattform") }),
          el("h2", { text: t("ui.kundeadmin.plattform_tittel") }))),
      el("div", { class: "site-grid site-grid-3" },
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.kundeadmin.plattform.status_tittel") }),
          el("p", { text: t("ui.kundeadmin.plattform.status_tekst") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.kundeadmin.plattform.policy_tittel") }),
          el("p", { text: t("ui.kundeadmin.plattform.policy_tekst") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.kundeadmin.plattform.utvidelse_tittel") }),
          el("p", { text: t("ui.kundeadmin.plattform.utvidelse_tekst") })))),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.moduler") }),
          el("h2", { text: t("ui.kundeadmin.moduler_tittel") })),
        el("span", { class: "site-inline-note", text: t("ui.kundeadmin.moduler_note") })),
      mine
        ? el("div", { class: "site-card-grid" },
          moduler.map((mod) => siteModuleKort(mod)))
        : el("p", { class: "muted", text: t("ui.kundeadmin.moduler_ukjent") })),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.handlinger") }),
          el("h2", { text: t("ui.kundeadmin.handlinger_tittel") }))),
      el("div", { class: "site-card-grid" },
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.kundeadmin.handling.oversikt") }),
          el("p", { text: t("ui.kundeadmin.handling.oversikt_tekst") }),
          el("a", { class: "lenkeknapp", href: "#/oversikt",
            text: t("ui.kundeadmin.handling.ga_til") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.kundeadmin.handling.unntak") }),
          el("p", { text: t("ui.kundeadmin.handling.unntak_tekst") }),
          el("a", { class: "lenkeknapp", href: "#/unntak",
            text: t("ui.kundeadmin.handling.ga_til") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.kundeadmin.handling.policy") }),
          el("p", { text: t("ui.kundeadmin.handling.policy_tekst") }),
          el("a", { class: "lenkeknapp", href: "#/policy",
            text: t("ui.kundeadmin.handling.ga_til") })))),
    el("section", { class: "site-grid site-grid-2" },
      forvalter
        ? el("article", { class: "kort" },
          el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.policy") }),
          el("h2", { text: t("ui.kundeadmin.policy_tittel") }),
          el("p", { text: t("ui.kundeadmin.policy_tekst") }),
          el("a", { class: "knapp primar", href: "#/policyadmin", text: t("ui.kundeadmin.policy_handling") }))
        : el("article", { class: "kort" },
          el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.policy") }),
          el("h2", { text: t("ui.kundeadmin.policy_lesing_tittel") }),
          el("p", { text: t("ui.kundeadmin.policy_lesing_tekst") }),
          el("a", { class: "knapp primar", href: "#/policy", text: t("ui.kundeadmin.policy_lesing_handling") })),
      el("article", { class: "kort" },
        el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.neste") }),
        el("h2", { text: t("ui.kundeadmin.neste_tittel") }),
        el("p", { text: t("ui.kundeadmin.neste_tekst") }),
        el("div", { class: "site-inline-badges" },
          siteStatusMerke("i_drift"),
          siteStatusMerke("bygges"),
          siteStatusMerke("planlagt")))));
}
