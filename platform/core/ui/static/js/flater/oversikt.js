// Oversikt — helsebilde for siste døgn (telling, ikke KPI/M-16). Tidssonen er
// UTC fra serveren; visning lokaliseres av Tidspunkt.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson } from "../api.js";
import { Tidspunkt } from "../komponenter.js";
import { medStatus, flateHode } from "./felles.js";

function stat(klasse, tall, tekst) {
  return el("div", { class: `stat ${klasse}`.trim() },
    el("b", { text: String(tall) }),
    el("span", { text: tekst }));
}

export function visOversikt(hoved, ctx) {
  medStatus(hoved, ctx, () => hentJson("/v1/oversikt"), (d) => {
    sett(hoved,
      ...flateHode(t("ui.oversikt.tittel"), t("ui.oversikt.undertittel")),
      el("div", { class: "cards" },
        stat("tillat", d.tillatt, t("ui.oversikt.tillatt")),
        stat("stopp", d.stoppet, t("ui.oversikt.stoppet")),
        stat("unntak", d.unntak, t("ui.oversikt.unntak")),
        stat("", d.totalt, t("ui.oversikt.totalt"))),
      el("p", { class: "muted" },
        `${t("ui.oversikt.oppdatert")}: `, Tidspunkt(d.vindu_slutt),
        ` (${d.tidssone})`),
      el("p", { class: "legend muted", text: t("ui.oversikt.telling_note") }));
  });
}
