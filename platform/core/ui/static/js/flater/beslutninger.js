// Beslutninger — revisjonsloggen (M-2), filtrerbar, keyset-paginert. Detalj i
// skuff: de TRE ortogonale aksene (resultat.art / evidensstatus / flagg +
// sikkerhet). UI kombinerer aldri selv; ukjent art → Feiltilstand (gate 9);
// sikkerhet vises KUN når feltet finnes (gate 10); TILLAT er «Tillatt», aldri
// «utført» (gate 11).
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson, UautorisertFeil, IngenTilgangFeil } from "../api.js";
import {
  BeslutningBadge, BegrunnelseKjede, Tidspunkt, TomTilstand, Feiltilstand,
  TilgangsVakt, CursorNavigasjon, meldLive,
} from "../komponenter.js";
import { DataTabell } from "../tabell.js";
import { Detaljpanel } from "../dialog.js";
import { medStatus, flateHode, kvRad } from "./felles.js";

const KJENTE_ARTER = new Set([
  "policy_stoppet", "sideeffektfri_tillatt", "til_unntak",
  "utforelsesdata_ikke_tilgjengelig", "outbox_opprettet", "outbox_plukket",
  "outbox_utfort", "outbox_feilet", "outbox_kansellert",
]);

const FILTRE = [null, "TILLAT", "STOPP", "UNNTAK"];

function detaljInnhold(d) {
  // Ukjent resultattype: vis IKKE — gjett aldri (gate 9).
  if (!d.resultat || !KJENTE_ARTER.has(d.resultat.art)) {
    return Feiltilstand({ tittel: t("ui.detalj.ukjent_tittel"),
      tekst: t("ui.detalj.ukjent_tekst") });
  }
  const dl = el("dl", { class: "kv" });
  kvRad(dl, t("ui.kol.handling"), d.handling);
  kvRad(dl, t("ui.detalj.resultat"), t(`art.${d.resultat.art}`));
  kvRad(dl, t("ui.detalj.evidens"), t(`evidens.${d.evidensstatus}`,
    d.evidensstatus));
  if (d.resultat.feil_aarsak) {
    kvRad(dl, t("ui.detalj.evidens"),
      t(`feil_aarsak.${d.resultat.feil_aarsak}`, d.resultat.feil_aarsak));
  }
  kvRad(dl, t("ui.detalj.policy"), d.policy_versjon || "—");
  // sikkerhet KUN når feltet finnes (fravær ≠ false — gate 10).
  if ("sikkerhet" in d) {
    kvRad(dl, t("ui.detalj.sak_finnes"),
      d.sikkerhet.sak_finnes ? t("ui.detalj.sak_ja") : t("ui.detalj.sak_nei"));
  }
  const rot = el("div", {}, dl);
  if (d.sen_evidens) {
    rot.append(el("p", { class: "muted", text: t("ui.detalj.sen_evidens") }));
  }
  if (d.konflikt_evidens) {
    rot.append(el("p", { class: "muted", text: t("ui.detalj.konflikt_evidens") }));
  }
  rot.append(el("h3", { text: t("ui.detalj.begrunnelse") }),
    BegrunnelseKjede(d.begrunnelse));
  return rot;
}

function aapneDetalj(id, ctx) {
  hentJson(`/v1/beslutninger/${id}`).then((d) => {
    Detaljpanel({ tittel: t("ui.detalj.tittel"), innhold: detaljInnhold(d) });
  }).catch((e) => {
    if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
    Detaljpanel({ tittel: t("ui.detalj.tittel"),
      innhold: e instanceof IngenTilgangFeil ? TilgangsVakt({}) : Feiltilstand({}) });
  });
}

export function visBeslutninger(hoved, ctx) {
  // `sort` bor her, ikke i tabellen: den bygges på nytt ved hvert filterbytte og
  // hver «Vis mer», og eiers kolonnevalg skal ikke nullstilles av det.
  const st = { filter: null, rader: [], neste: null, sort: null };

  function rad(r) {
    return {
      id: r.id,
      celler: {
        ts: Tidspunkt(r.ts),
        handling: r.handling,
        beslutning: BeslutningBadge(r.policybeslutning),
      },
      sortverdi: { ts: r.ts, handling: r.handling },
      handling: { tekst: t("ui.aapne"), paaKlikk: () => aapneDetalj(r.id, ctx) },
    };
  }

  function filterbar() {
    const bar = el("div", { class: "filterbar", role: "group",
      "aria-label": t("ui.besl.filter") });
    for (const f of FILTRE) {
      const tekst = f === null ? t("ui.besl.alle") : t(`beslutning.${f}`);
      const b = el("button", { class: "knapp", type: "button", text: tekst,
        "aria-pressed": String(st.filter === f) });
      b.addEventListener("click", () => {
        if (st.filter === f) return;
        st.filter = f; lastForste();
      });
      bar.append(b);
    }
    return bar;
  }

  function tegn() {
    const innhold = st.rader.length
      ? DataTabell({
          captionTekst: t("ui.besl.tittel"),
          kolonner: [
            { nokkel: "ts", tittel: t("ui.kol.tidspunkt"), sorterbar: true },
            { nokkel: "handling", tittel: t("ui.kol.handling"), sorterbar: true },
            { nokkel: "beslutning", tittel: t("ui.kol.beslutning") },
          ],
          rader: st.rader.map(rad),
          sort: st.sort,
          paaSort: (s) => { st.sort = s; },
        })
      : TomTilstand({});
    sett(hoved,
      ...flateHode(t("ui.besl.tittel")),
      filterbar(),
      innhold,
      CursorNavigasjon({ neste: st.neste, paaMer: lastMer,
        paaOppdater: lastForste }));
  }

  function sok(cursor) {
    return hentJson("/v1/beslutninger",
      { limit: 50, policybeslutning: st.filter, cursor });
  }

  function lastForste() {
    medStatus(hoved, ctx, () => sok(null), (d) => {
      st.rader = d.rader; st.neste = d.neste_cursor; tegn();
    });
  }

  async function lastMer() {
    try {
      const d = await sok(st.neste);
      st.rader = st.rader.concat(d.rader); st.neste = d.neste_cursor; tegn();
      meldLive(`${st.rader.length}`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      meldLive(t("ui.feil_tittel"));
    }
  }

  lastForste();
}
