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

// Tilgjengelighetsbrikka på forsidens tilbudspunkter. Den bor HER, sammen med
// de andre site-merkene, fordi klassevokabularet er `site-badge`-familien i
// `komponenter.css` — det er den eneste som har definisjoner. Kallstedet skrev
// før sine egne `merke-i_drift`/`merke-planlagt`, som ikke finnes i noen CSS,
// så «Tilgjengelig» og «Kommer» rendret helt likt (Codex P3). Et merke som
// bare skiller i tekst er ikke et merke.
//
// `ok` mot `plan` er samme akse som `siteStatusMerke`: grønt er det som
// kjører, nøytralt er det som kommer. Teksten er salgsordet, ikke
// driftsordet — kunden leser «Kommer», ikke «Bygges».
export function siteTilbudMerke(tilgjengelig) {
  return el("span", {
    class: tilgjengelig ? "site-badge ok" : "site-badge plan",
    text: t(tilgjengelig ? "site.tilbud.tilgjengelig" : "site.tilbud.kommer"),
  });
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
