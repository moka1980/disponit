// Adjudikatorkøen (041 §5): overtakelsessaker som venter på avgjørelse.
//
// EGEN visning, atskilt fra tenantens unntakskø — adjudikatoren skal se
// PARTENE (utfordrer og forrige innehaver) for å kunne avgjøre, og nettopp
// derfor kan denne listen aldri vises i en kundeflate: kryssidentitetene
// hører hjemme her og bare her. Ruten er scope-gatet (`domains:adjudicate`)
// i sitekartet, og API-et bak (`GET /v1/domeneovertakelse/saker`) leser
// under adjudikatorrollen — flaten er visning, aldri autoritet.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson, UautorisertFeil } from "../api.js";
import { Tidspunkt, TomTilstand, Feiltilstand } from "../komponenter.js";
import { flateHode } from "./felles.js";

export function visAdjudikator(hoved, ctx) {
  const liste = el("div", { class: "adjudikatorliste", "aria-busy": "true" });

  function statusTekst(status) {
    return t(`ui.adjudikator.status.${status}`, status);
  }

  function tegnListe(saker) {
    liste.removeAttribute("aria-busy");
    if (!saker.length) {
      sett(liste, TomTilstand({ tittel: t("ui.adjudikator.tom_tittel"),
        tekst: t("ui.adjudikator.tom_tekst") }));
      return;
    }
    const thead = el("thead", {}, el("tr", {},
      el("th", { scope: "col", text: t("ui.adjudikator.kol.hostname") }),
      el("th", { scope: "col", text: t("ui.adjudikator.kol.utfordrer") }),
      el("th", { scope: "col", text: t("ui.adjudikator.kol.tapt") }),
      el("th", { scope: "col", text: t("ui.adjudikator.kol.revisjon") }),
      el("th", { scope: "col", text: t("ui.adjudikator.kol.status") }),
      el("th", { scope: "col", text: t("ui.adjudikator.kol.tid") })));
    const tbody = el("tbody", {}, ...saker.map((s) =>
      el("tr", {},
        el("th", { scope: "row", text: s.hostname }),
        el("td", { text: s.utfordrer_tenant }),
        el("td", { text: s.tapt_tenant }),
        el("td", { text: String(s.saksrevisjon) }),
        el("td", { text: statusTekst(s.status) }),
        el("td", {}, s.ts ? Tidspunkt(s.ts) : "—"))));
    // Tabellen ruller i sin egen container — siden skal aldri rulle sideveis.
    sett(liste, el("div", { class: "tabellrull" },
      el("table", { class: "datatabell" },
        el("caption", { text: t("ui.adjudikator.caption") }), thead, tbody)));
  }

  // Samme generasjonsvern som domenelisten: et foreldet svar får ikke
  // overskrive et nyere, hverken med data eller med en feiltilstand.
  let generasjon = 0;

  function last() {
    const min = ++generasjon;
    liste.setAttribute("aria-busy", "true");
    hentJson("/v1/domeneovertakelse/saker").then((d) => {
      if (min !== generasjon) return;
      tegnListe(d.saker || []);
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (min !== generasjon) return;
      liste.removeAttribute("aria-busy");
      sett(liste, Feiltilstand({ paaProvIgjen: last }));
    });
  }

  sett(hoved,
    ...flateHode(t("ui.adjudikator.tittel"), t("ui.adjudikator.under")),
    liste);
  last();
}
