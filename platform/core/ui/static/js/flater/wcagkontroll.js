// WCAG kontroll — samleflaten (eiers UX-krav 18/8: ÉN oppføring i menyen,
// faner i stedet for flere knapper). Fanene er de tre delene av samme
// arbeidsflyt: verifiser domenet → bestill kontrollen → les rapporten.
// Hver del bor fortsatt i sin egen modul; denne fila er bare rammen.
//
// Rammen har ÉN kontrakt mot delene: `bygg(rot, ctx)` kalles én gang, og
// returnerer den valgfritt en funksjon, kalles DEN ved hver senere aktivering
// av fanen. Det er oppfriskningen, ikke en ny tegning — se `del()` under.
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
    //
    // Men BEVART er ikke det samme som FERSK (Codex P2). Domener-fanen lover
    // brukeren at «statusen oppdateres når du åpner fanen igjen», og
    // verifiseringen skjer i bakgrunnen (arbeideren, ~5 min). Med bare
    // `tegnet`-vakten ble den cachede DOM-en hengt tilbake uendret: en
    // challenge som var blitt `verifisert` mens brukeren var på en annen fane
    // sto fortsatt `ventende` til hele siden ble lastet på nytt.
    //
    // Delen får derfor lov til å returnere en OPPFRISKNINGSKROK fra
    // byggingen. Er den der, kalles den ved hver senere aktivering — og bare
    // det: skjemaet bygges ikke om, så utfylt tekst og lastet rapport står
    // urørt. Deler som ikke har noe å friske opp (bestilling, rapport)
    // returnerer ingenting og oppfører seg akkurat som før.
    const rot = el("div", { class: "wcag-del" });
    let tegnet = false;
    let frisk = null;
    return () => {
      if (!tegnet) {
        frisk = bygger(rot, ctx);
        tegnet = true;
      } else if (typeof frisk === "function") {
        frisk();
      }
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
