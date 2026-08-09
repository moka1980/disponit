// Unntak — M-37-køen (read-only i v1: BEHANDLING er bevisst ute av scope).
// Liste + detalj i skuff med begrunnelse og historikk (StatusTidslinje). Det
// er e2e-kriteriet: åpne et unntak → se begrunnelse + historikk.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson, UautorisertFeil, IngenTilgangFeil } from "../api.js";
import {
  KategoriTag, BegrunnelseKjede, StatusTidslinje, Tidspunkt, TomTilstand,
  Feiltilstand, TilgangsVakt, CursorNavigasjon, meldLive,
} from "../komponenter.js";
import { DataTabell } from "../tabell.js";
import { Detaljpanel } from "../dialog.js";
import { medStatus, flateHode, kvRad } from "./felles.js";

const STATUSFILTRE = [null, "ny", "under_behandling", "løst", "avvist"];

function detaljInnhold(detalj, historikk) {
  const dl = el("dl", { class: "kv" });
  kvRad(dl, t("ui.kol.handling"), detalj.handling);
  kvRad(dl, t("ui.kol.kategori"), KategoriTag(detalj.kategori));
  kvRad(dl, t("ui.kol.status"), t(`status.${detalj.status}`, detalj.status));
  kvRad(dl, t("ui.kol.prioritet"), t(`prioritet.${detalj.prioritet}`,
    detalj.prioritet));
  kvRad(dl, t("ui.unntak.sakstype"), t(`sakstype.${detalj.sakstype}`,
    detalj.sakstype));
  const rot = el("div", {}, dl,
    el("h3", { text: t("ui.detalj.begrunnelse") }),
    BegrunnelseKjede(detalj.begrunnelse),
    el("h3", { text: t("ui.unntak.historikk") }));
  rot.append((historikk.rader && historikk.rader.length)
    ? StatusTidslinje(historikk.rader)
    : el("p", { class: "muted", text: t("ui.unntak.ingen_historikk") }));
  return rot;
}

function aapneDetalj(id, ctx) {
  Promise.all([
    hentJson(`/v1/unntak/${id}`),
    hentJson(`/v1/unntak/${id}/historikk`, { limit: 50 }),
  ]).then(([detalj, historikk]) => {
    Detaljpanel({ tittel: t("ui.unntak.detalj_tittel"),
      innhold: detaljInnhold(detalj, historikk) });
  }).catch((e) => {
    if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
    Detaljpanel({ tittel: t("ui.unntak.detalj_tittel"),
      innhold: e instanceof IngenTilgangFeil ? TilgangsVakt({}) : Feiltilstand({}) });
  });
}

export function visUnntak(hoved, ctx) {
  const st = { status: null, rader: [], neste: null };

  function rad(r) {
    return {
      id: r.id,
      celler: {
        ts: Tidspunkt(r.ts),
        handling: r.handling,
        kategori: KategoriTag(r.kategori),
        status: t(`status.${r.status}`, r.status),
        prioritet: t(`prioritet.${r.prioritet}`, r.prioritet),
      },
      sortverdi: { ts: r.ts, handling: r.handling },
      handling: { tekst: t("ui.aapne"), paaKlikk: () => aapneDetalj(r.id, ctx) },
    };
  }

  function filterbar() {
    const bar = el("div", { class: "filterbar", role: "group",
      "aria-label": t("ui.kol.status") });
    for (const s of STATUSFILTRE) {
      const tekst = s === null ? t("ui.besl.alle") : t(`status.${s}`);
      const b = el("button", { class: "knapp", type: "button", text: tekst,
        "aria-pressed": String(st.status === s) });
      b.addEventListener("click", () => {
        if (st.status === s) return;
        st.status = s; lastForste();
      });
      bar.append(b);
    }
    return bar;
  }

  function tegn() {
    const innhold = st.rader.length
      ? DataTabell({
          captionTekst: t("ui.unntak.tittel"),
          kolonner: [
            { nokkel: "ts", tittel: t("ui.kol.tidspunkt"), sorterbar: true },
            { nokkel: "handling", tittel: t("ui.kol.handling"), sorterbar: true },
            { nokkel: "kategori", tittel: t("ui.kol.kategori") },
            { nokkel: "status", tittel: t("ui.kol.status") },
            { nokkel: "prioritet", tittel: t("ui.kol.prioritet") },
          ],
          rader: st.rader.map(rad),
        })
      : TomTilstand({});
    sett(hoved,
      ...flateHode(t("ui.unntak.tittel")),
      filterbar(),
      innhold,
      CursorNavigasjon({ neste: st.neste, paaMer: lastMer,
        paaOppdater: lastForste }));
  }

  function sok(cursor) {
    return hentJson("/v1/unntak", { limit: 50, status: st.status, cursor });
  }

  function lastForste() {
    medStatus(hoved, ctx, () => sok(null), (d) => {
      st.rader = d.saker; st.neste = d.neste_cursor; tegn();
    });
  }

  async function lastMer() {
    try {
      const d = await sok(st.neste);
      st.rader = st.rader.concat(d.saker); st.neste = d.neste_cursor; tegn();
      meldLive(`${st.rader.length}`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      meldLive(t("ui.feil_tittel"));
    }
  }

  lastForste();
}
