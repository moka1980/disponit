import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { flateHode } from "./felles.js";
import { FASEOVERSIKT, TENANTOVERSIKT, modulmerke, plattformTelling, tenantRad }
  from "../plattformdata.js";
import { erPlattformdrift, kanForvaltePolicy } from "../sitekart.js";
import { siteFaseMerke } from "../sitekomponenter.js";

export function visAdmin(hoved, ctx = {}) {
  const telling = plattformTelling();
  const forvalter = kanForvaltePolicy(ctx);
  // Tenanttabellen er kontrollplan på tvers av kunder, og krever
  // plattformdrift. En tenantbundet ops-økt (`security:read`) ser bare sin
  // egen rad — ikke hver eneste andre kundes plan, moduler og neste steg.
  const plattformdrift = erPlattformdrift(ctx);
  const egen = tenantRad(ctx.tenant);
  const tenanter = plattformdrift ? TENANTOVERSIKT : (egen ? [egen] : []);

  sett(hoved,
    ...flateHode(t("ui.admin.tittel"), t("ui.admin.undertittel")),
    el("div", { class: "site-grid site-grid-3" },
      el("section", { class: "kort site-hero-card" },
        el("p", { class: "site-eyebrow", text: t("ui.admin.status") }),
        el("h2", { text: t("ui.admin.status_tittel") }),
        el("p", { text: t("ui.admin.status_tekst") }),
        el("div", { class: "site-kpi-row" },
          el("div", { class: "site-kpi" },
            el("strong", { text: `${telling.iDrift}/${telling.totalt}` }),
            el("span", { text: t("ui.admin.kpi.i_drift") })),
          el("div", { class: "site-kpi" },
            el("strong", { text: String(telling.bygges) }),
            el("span", { text: t("ui.admin.kpi.bygges") })),
          el("div", { class: "site-kpi" },
            el("strong", { text: String(tenanter.length) }),
            el("span", { text: t("ui.admin.kpi.tenanter") })))),
      el("section", { class: "kort" },
        el("p", { class: "site-eyebrow", text: t("ui.admin.utrulling") }),
        el("h2", { text: t("ui.admin.utrulling_tittel") }),
        el("ol", { class: "site-list site-list-ordered" },
          el("li", { text: t("ui.admin.utrulling.ci") }),
          el("li", { text: t("ui.admin.utrulling.staging") }),
          el("li", { text: t("ui.admin.utrulling.kanari") }),
          el("li", { text: t("ui.admin.utrulling.rollback") }))),
      el("section", { class: "kort" },
        el("p", { class: "site-eyebrow", text: t("ui.admin.tillit") }),
        el("h2", { text: t("ui.admin.tillit_tittel") }),
        el("ul", { class: "site-list" },
          el("li", { text: t("ui.admin.tillit.p1") }),
          el("li", { text: t("ui.admin.tillit.p2") }),
          el("li", { text: t("ui.admin.tillit.p3") })))),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.admin.kontrollplan") }),
          el("h2", { text: t("ui.admin.kontrollplan_tittel") }))),
      el("div", { class: "site-grid site-grid-3" },
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.admin.kontrollplan.release_tittel") }),
          el("p", { text: t("ui.admin.kontrollplan.release_tekst") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.admin.kontrollplan.tenant_tittel") }),
          el("p", { text: t("ui.admin.kontrollplan.tenant_tekst") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.admin.kontrollplan.port_tittel") }),
          el("p", { text: t("ui.admin.kontrollplan.port_tekst") })))),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.admin.faser") }),
          el("h2", { text: t("ui.admin.faser_tittel") })),
        el("span", { class: "site-inline-note", text: t("ui.admin.faser_note") })),
      el("div", { class: "site-card-grid" },
        FASEOVERSIKT.map((fase) =>
          el("article", { class: "site-module-card" },
            el("div", { class: "site-module-head" },
              el("strong", { text: t(fase.navn_nokkel) }),
              siteFaseMerke(fase.status)),
            el("p", { text: t(fase.tekst_nokkel) }))))),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.admin.tenanter") }),
          el("h2", { text: plattformdrift
            ? t("ui.admin.tenanter_tittel")
            : t("ui.admin.tenanter_egen_tittel") })),
        plattformdrift
          ? null
          : el("span", { class: "site-inline-note",
            text: t("ui.admin.tenanter_egen_note") })),
      tenanter.length
        ? el("div", { class: "tablewrap" },
          el("table", {},
            el("caption", { class: "sr-only", text: plattformdrift
              ? t("ui.admin.tenanter_tittel")
              : t("ui.admin.tenanter_egen_tittel") }),
            el("thead", {},
              el("tr", {},
                el("th", { scope: "col", text: t("ui.admin.kol.tenant") }),
                el("th", { scope: "col", text: t("ui.admin.kol.plan") }),
                el("th", { scope: "col", text: t("ui.admin.kol.moduler") }),
                el("th", { scope: "col", text: t("ui.admin.kol.neste") }))),
            el("tbody", {},
              tenanter.map((tenant) =>
                el("tr", {},
                  el("td", {}, el("strong", { text: t(tenant.navn_nokkel) })),
                  el("td", { text: t(tenant.plan_nokkel) }),
                  el("td", { text: tenant.moduler.map(modulmerke).join(", ") }),
                  el("td", { text: t(tenant.neste_nokkel) }))))))
        : el("p", { class: "muted", text: t("ui.admin.tenanter_ukjent") })),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.admin.handlinger") }),
          el("h2", { text: t("ui.admin.handlinger_tittel") }))),
      el("div", { class: "site-card-grid" },
        // Kundeadmin er en leseflate og ligger i basisrutene — snarveien dit
        // gjelder derfor alle. PolicyADMINISTRASJONEN krever forvaltning:
        // admin-flaten åpnes av `security:read`, og rollene `admin`/`sikkerhet`
        // har bare `policy:read`, så snarveien ville pekt på en flate ruteren
        // nekter dem, med mutasjonsknapper som uansett gir 403.
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.admin.handling.kundeadmin") }),
          el("p", { text: t("ui.admin.handling.kundeadmin_tekst") }),
          el("a", { class: "lenkeknapp", href: "#/kundeadmin",
            text: t("ui.admin.handling.ga_til") })),
        ...(forvalter ? [
          el("article", { class: "site-mini-card" },
            el("strong", { text: t("ui.admin.handling.policyadmin") }),
            el("p", { text: t("ui.admin.handling.policyadmin_tekst") }),
            el("a", { class: "lenkeknapp", href: "#/policyadmin",
              text: t("ui.admin.handling.ga_til") })),
        ] : [
          // Leserettighet skal fortsatt komme til policy — bare til
          // lesevisningen, ikke til aktiveringsflaten.
          el("article", { class: "site-mini-card" },
            el("strong", { text: t("ui.admin.handling.policy_lesing") }),
            el("p", { text: t("ui.admin.handling.policy_lesing_tekst") }),
            el("a", { class: "lenkeknapp", href: "#/policy",
              text: t("ui.admin.handling.ga_til") })),
        ]),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.admin.handling.unntak") }),
          el("p", { text: t("ui.admin.handling.unntak_tekst") }),
          el("a", { class: "lenkeknapp", href: "#/unntak",
            text: t("ui.admin.handling.ga_til") })))));
}
