// Unntak — M-37-køen. Liste + detalj i skuff med begrunnelse og historikk
// (StatusTidslinje). PR-012: menneskelig BEHANDLING (godkjenn/avvis/eskaler)
// tilbys i detaljen når saken er håndterbar — konvolutten bygges og
// MAC-signeres server-side; klienten sender kun operatørens valg.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  hentJson, postHandling, nyIdempotensnokkel, ApiFeil, UautorisertFeil,
  IngenTilgangFeil,
} from "../api.js";
import {
  KategoriTag, BegrunnelseKjede, StatusTidslinje, Tidspunkt, TomTilstand,
  Feiltilstand, TilgangsVakt, CursorNavigasjon, meldLive,
} from "../komponenter.js";
import { DataTabell } from "../tabell.js";
import { Detaljpanel, Bekreftelsesdialog } from "../dialog.js";
import { medStatus, flateHode, kvRad } from "./felles.js";

const STATUSFILTRE = [null, "ny", "under_behandling", "løst", "avvist"];

function utfoer(id, oh, saksversjon, ctx, paaFerdig) {
  // ÉN idempotensnøkkel per operasjon — gjenbrukes ved en nettverksretry, så
  // serveren ser samme nøkkel og ikke utfører handlingen to ganger.
  const nokkel = nyIdempotensnokkel();
  const forsok = (attempt) =>
    postHandling(id, oh, saksversjon, nokkel).then(() => {
      meldLive(t(`ui.unntak.handling.${oh}`) + ": " + t("ui.unntak.behandlet"));
      if (paaFerdig) paaFerdig();
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      // Kun nettverksfeil (status 0) retries — ÉN gang, med SAMME nøkkel. En
      // konflikt (f.eks. stale saksversjon) retries ALDRI blindt; saken lastes
      // på nytt så operatøren ser fersk tilstand.
      if (e instanceof ApiFeil && e.status === 0 && attempt === 0) {
        return forsok(1);
      }
      // Gate 14a (V9/v2): saken har utestående oppdrag/kapabilitet og er
      // flagget for avklaring — IKKE avvist. Serveren svarer med den lukkede
      // koden `utestaaende_oppdrag`; vis den KONKRETE avklaringsteksten (ikke
      // den generiske feilen), og last saken på nytt uten blind retry.
      if (e instanceof ApiFeil && e.kode === "utestaaende_oppdrag") {
        meldLive(t("ui.unntak.utestaaende_oppdrag"));
        if (paaFerdig) paaFerdig();
        return;
      }
      meldLive(t("ui.unntak.behandling_feilet"));
      if (paaFerdig) paaFerdig();
    });
  return forsok(0);
}

// PR-012: handlingsknappene. `godkjenn` vises kun når serveren sier den er
// tilgjengelig (tillatte_handlinger); ellers forklares hvorfor. Bekreftelses-
// dialogen viser den KONKRETE grunnen i klartekst, så mennesket ser nøyaktig
// hva det binder seg til (v8 §3).
function behandlingsHandlinger(detalj, id, ctx, paaFerdig) {
  const th = detalj.tillatte_handlinger || [];
  const boks = el("section", { class: "behandling",
    "aria-label": t("ui.unntak.behandle") });
  if (!th.length) {
    if (detalj.godkjenn_utilgjengelig) {
      boks.append(el("p", { class: "muted",
        text: t(`ui.unntak.utilgjengelig.${detalj.godkjenn_utilgjengelig}`,
          t("ui.unntak.ikke_behandlbar")) }));
    }
    return boks;
  }
  boks.append(el("h3", { text: t("ui.unntak.behandle") }));
  const grunnkode = (detalj.begrunnelse || []).slice(-1)[0];
  const knapper = el("div", { class: "behandling-knapper", role: "group",
    "aria-label": t("ui.unntak.behandle") });
  for (const oh of th) {
    const knapp = el("button", { class: "knapp", type: "button",
      text: t(`ui.unntak.handling.${oh}`) });
    knapp.addEventListener("click", () => {
      const tekst = oh === "godkjenn" && grunnkode
        ? `${t("ui.unntak.du_godkjenner")}: ${t(`grunn.${grunnkode}`, grunnkode)}`
        : t(`ui.unntak.bekreft.${oh}`, t("ui.unntak.bekreft_generisk"));
      Bekreftelsesdialog({
        tittel: t(`ui.unntak.handling.${oh}`),
        tekst,
        primarTekst: t(`ui.unntak.handling.${oh}`),
        farlig: oh !== "godkjenn",
        paaPrimar: () => utfoer(id, oh, detalj.saksversjon, ctx, paaFerdig),
      });
    });
    knapper.append(knapp);
  }
  boks.append(knapper);
  // Gate 14a: serveren har utelatt `avvis` fordi saken har et utestående
  // oppdrag — forklar det, så operatøren ser hvorfor knappen mangler.
  if (detalj.avvis_utilgjengelig === "utestaaende_oppdrag") {
    boks.append(el("p", { class: "muted",
      text: t("ui.unntak.utestaaende_oppdrag") }));
  }
  return boks;
}

function detaljInnhold(detalj, historikk, id, ctx, paaFerdig) {
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
    behandlingsHandlinger(detalj, id, ctx, paaFerdig),
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
      innhold: detaljInnhold(detalj, historikk, id, ctx,
        () => aapneDetalj(id, ctx)) });
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
