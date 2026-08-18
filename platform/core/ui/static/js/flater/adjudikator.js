// Adjudikatorkøen (041 §5): overtakelsessaker som venter på avgjørelse.
//
// EGEN visning, atskilt fra tenantens unntakskø — adjudikatoren skal se
// PARTENE (utfordrer og forrige innehaver) for å kunne avgjøre, og nettopp
// derfor kan denne listen aldri vises i en kundeflate: motpartens identitet
// hører hjemme her og bare her. Ruten er scope-gatet (`domains:adjudicate`)
// i sitekartet, og API-et bak (`GET /v1/domeneovertakelse/saker`) leser
// under adjudikatorrollen, avgrenset til sakene der DIN tenant er
// utfordreren — flaten er visning, aldri autoritet.
//
// Køen er PAGINERT (keyset, eldste først): en overtakelsessak står åpen til
// et menneske avgjør den, så beholdningen har ingen naturlig øvre grense.
// Uten en bundet side ville hver henting materialisert hele etterslepet.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson, UautorisertFeil } from "../api.js";
import {
  Tidspunkt, TomTilstand, Feiltilstand, CursorNavigasjon, meldLive,
} from "../komponenter.js";
import { flateHode } from "./felles.js";

const SIDESTORRELSE = 50;

export function visAdjudikator(hoved, ctx) {
  const liste = el("div", { class: "adjudikatorliste", "aria-busy": "true" });
  const st = { rader: [], neste: null };

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

  // Navigasjonen ligger UTENFOR listen (egen søskennode): en tom kø skal
  // fortsatt kunne oppdateres, og «vis mer» finnes bare når serveren sa at
  // det er mer — knappen er dermed selv svaret på «er det flere?».
  let navnode = el("div");

  function tegn() {
    tegnListe(st.rader);
    const ny = CursorNavigasjon({ neste: st.neste, paaMer: lastMer,
      paaOppdater: lastForste });
    navnode.replaceWith(ny);
    navnode = ny;
  }

  // Samme generasjonsvern som domenelisten: et foreldet svar får ikke
  // overskrive et nyere, hverken med data eller med en feiltilstand.
  let generasjon = 0;

  function hent(cursor) {
    return hentJson("/v1/domeneovertakelse/saker",
                    { limit: SIDESTORRELSE, cursor });
  }

  function lastForste() {
    const min = ++generasjon;
    liste.setAttribute("aria-busy", "true");
    hent(null).then((d) => {
      if (min !== generasjon) return;
      st.rader = d.saker || [];
      st.neste = d.neste_cursor || null;
      tegn();
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (min !== generasjon) return;
      liste.removeAttribute("aria-busy");
      sett(liste, Feiltilstand({ paaProvIgjen: lastForste }));
    });
  }

  function lastMer() {
    const min = ++generasjon;
    hent(st.neste).then((d) => {
      if (min !== generasjon) return;
      st.rader = st.rader.concat(d.saker || []);
      st.neste = d.neste_cursor || null;
      tegn();
      meldLive(`${st.rader.length}`);
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (min !== generasjon) return;
      meldLive(t("ui.feil_tittel"));
    });
  }

  sett(hoved,
    ...flateHode(t("ui.adjudikator.tittel"), t("ui.adjudikator.under")),
    liste, navnode);
  lastForste();
}
