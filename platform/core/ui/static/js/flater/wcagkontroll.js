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
import { harScope } from "../sitekart.js";
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

  // FANENE BÆRER HVER SITT SCOPE (Codex P2). Ruten hit krever bare
  // `decisions:read` — den svakeste delen — nettopp for at rapportene ikke
  // skal forsvinne for `leser`, `sikkerhet`, `godkjenner` og
  // `policyforvalter` bare fordi tre menyoppføringer ble til én. Da må
  // MUTASJONENE skjules her i stedet: både bestillingen (`POST
  // /v1/bestilling`) og domeneutstedelsen (`POST /v1/domener`) krever
  // `bestilling:opprett`, og en fane som bare kan svare 403 er et løfte
  // flaten ikke kan holde.
  //
  // Domenefanen skjules HEL, ikke bare skjemaet: den finnes for å skaffe
  // seg et verifisert domene, og en leseliste over andres domenestatuser er
  // ikke den flaten. Rapportfanen står igjen alene, og `Faner` faller
  // tilbake til første trinn når `start` ikke finnes i settet.
  // REKKEFØLGEN ER FLYTEN (eier 18/8): et domene må verifiseres FØR en
  // bestilling kan gå gjennom, så Domener står først og er startfanen —
  // rekkefølgen i tablisten skal fortelle brukeren hvilken vei jobben går.
  const trinn = [
    { nokkel: "domener", tittel: t("ui.wcag.fane.domener"),
      scope: "bestilling:opprett", bygg: del(visDomener) },
    { nokkel: "bestill", tittel: t("ui.wcag.fane.bestill"),
      scope: "bestilling:opprett", bygg: del(visBestilling) },
    { nokkel: "rapporter", tittel: t("ui.wcag.fane.rapporter"),
      scope: null, bygg: del(visRapport) },
  ].filter((s) => !s.scope || harScope(ctx, s.scope));

  // Faner returnerer { rot, gaaTil, aktiv } — det er ROTEN som monteres.
  const faner = Faner({ trinn, start: "domener" });
  sett(hoved,
    ...flateHode(t("ui.wcag.tittel"), t("ui.wcag.under")),
    faner.rot);
}
