// WCAG kontroll — samleflaten (eiers UX-krav 18/8: ÉN oppføring i menyen,
// faner i stedet for flere knapper). Fanene er de tre delene av samme
// arbeidsflyt: verifiser domenet → bestill kontrollen → les rapporten.
// Hver del bor fortsatt i sin egen modul; denne fila er bare rammen.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { Faner } from "../komponenter.js";
import { flateHode } from "./felles.js";
import { visBestilling } from "./bestilling.js";
import { visRapport } from "./rapport.js";
import { visDomener } from "./domener.js";

export function visWcagKontroll(hoved, ctx) {
  function del(bygger) {
    // Hver fane får sitt eget vedvarende rotelement: Faner gjenbruker
    // panelet, og delens tilstand (utfylt skjema, lastet rapport) skal
    // overleve et fanebytte frem og tilbake.
    const rot = el("div", { class: "wcag-del" });
    let tegnet = false;
    return () => {
      if (!tegnet) { bygger(rot, ctx); tegnet = true; }
      return rot;
    };
  }

  const trinn = [
    { nokkel: "bestill", tittel: t("ui.wcag.fane.bestill"),
      bygg: del(visBestilling) },
    { nokkel: "rapporter", tittel: t("ui.wcag.fane.rapporter"),
      bygg: del(visRapport) },
    { nokkel: "domener", tittel: t("ui.wcag.fane.domener"),
      bygg: del(visDomener) },
  ];

  // Faner returnerer { rot, gaaTil, aktiv } — det er ROTEN som monteres.
  const faner = Faner({ trinn, start: "bestill" });
  sett(hoved,
    ...flateHode(t("ui.wcag.tittel"), t("ui.wcag.under")),
    faner.rot);
}
