import { el } from "./dom.js";
import { t } from "./i18n.js";

// `ok` er reservert for modulen som FAKTISK kjører hos kunder. `klargjort` er
// godkjent-men-ikke-i-drift og deler `info` med `bygges`: begge er «ikke i
// drift ennå», og et grønt merke der ville lovet drift manifestet ikke har.
export function siteStatusMerke(status) {
  const cls = status === "i_drift" ? "ok"
    : status === "planlagt" ? "plan" : "info";
  return el("span", { class: `site-badge ${cls}` }, t(`site.status.${status}`));
}

export function siteFaseMerke(status) {
  const cls = status === "aktiv" ? "ok" : "plan";
  return el("span", { class: `site-badge ${cls}` }, t(`site.fase_status.${status}`));
}

export function siteModuleKort(mod) {
  return el("article", { class: "site-module-card" },
    el("div", { class: "site-module-head" },
      el("strong", { text: `M-${mod.id} ${t(mod.navn_nokkel)}` }),
      siteStatusMerke(mod.status)),
    el("p", { text: t(mod.tekst_nokkel) }),
    el("p", { class: "muted", text: `${t("site.fase")}: ${t(mod.fase_nokkel)}` }));
}
