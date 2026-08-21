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
  Feiltilstand, TilgangsVakt, CursorNavigasjon, meldAlert, meldLive,
} from "../komponenter.js";
import { DataTabell } from "../tabell.js";
import { Detaljpanel, Bekreftelsesdialog } from "../dialog.js";
import { medStatus, flateHode, kvRad } from "./felles.js";
import { synligeSakstyper } from "../sitekart.js";

const STATUSFILTRE = [null, "ny", "under_behandling", "løst", "avvist"];

function utfoer(id, oh, saksversjon, ctx, paaFerdig, busyEl) {
  // ÉN idempotensnøkkel per operasjon — gjenbrukes ved en nettverksretry, så
  // serveren ser samme nøkkel og ikke utfører handlingen to ganger.
  const nokkel = nyIdempotensnokkel();
  if (busyEl) busyEl.setAttribute("aria-busy", "true");
  const ferdig = () => {
    if (busyEl) busyEl.removeAttribute("aria-busy");
    if (paaFerdig) paaFerdig();
  };
  const forsok = (attempt) =>
    postHandling(id, oh, saksversjon, nokkel).then((kropp) => {
      // 043 (§7): en KANSELLERING annonseres assertivt, med årsaken opplest
      // — det er en irreversibel konsekvens, ikke en statuslinje.
      if (kropp && Array.isArray(kropp.opplosning) && kropp.opplosning.length) {
        meldAlert(t("ui.unntak.kansellert_alert") + " " +
          kropp.opplosning.map((o) => `#${o.oppdrag_id}`).join(", "));
      } else {
        meldLive(t(`ui.unntak.handling.${oh}`) + ": " +
          t("ui.unntak.behandlet"));
      }
      ferdig();
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
        ferdig();
        return;
      }
      // 043: kvitteringen vant kappløpet — ingenting er kansellert, og det
      // skal sies HØYT, med referansen mennesket beslutter på nytt med.
      if (e instanceof ApiFeil && e.kode === "oppdrag_utfort") {
        const [oid, ref] = e.detaljer || [];
        meldAlert(t("ui.unntak.oppdrag_utfort_alert") +
          (oid ? ` #${oid}` : "") +
          (ref ? ` — ${t("ui.unntak.kvitteringsref")}: ${ref}` : ""));
        ferdig();
        return;
      }
      meldLive(t("ui.unntak.behandling_feilet"));
      ferdig();
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
      // 043 (Gate 14b §7): avvis på sak med LEVENDE oppdrag varsler
      // konsekvensen FØR handlingen — alertdialog med oppdraget og
      // modulen navngitt, fokus inn, Escape lukker uten handling, fokus
      // tilbake til utløseren (alt båret av dialogmekanikken).
      const kansellerer = oh === "avvis"
        && Array.isArray(detalj.avvis_kansellerer)
        && detalj.avvis_kansellerer.length
        ? detalj.avvis_kansellerer : null;
      const tekst = kansellerer
        ? kansellerer.map((o) =>
            `${t("ui.unntak.avvis_kansellerer_1")} #${o.oppdrag_id} ` +
            `${t("ui.unntak.avvis_kansellerer_2")} ${o.modul_id} ` +
            t("ui.unntak.avvis_kansellerer_3")).join(" ") + " " +
          t("ui.unntak.kan_ikke_angres")
        : oh === "godkjenn" && grunnkode
        ? `${t("ui.unntak.du_godkjenner")}: ${t(`grunn.${grunnkode}`, grunnkode)}`
        : t(`ui.unntak.bekreft.${oh}`, t("ui.unntak.bekreft_generisk"));
      Bekreftelsesdialog({
        tittel: t(`ui.unntak.handling.${oh}`),
        tekst,
        rolle: kansellerer ? "alertdialog" : "dialog",
        primarTekst: t(`ui.unntak.handling.${oh}`),
        farlig: oh !== "godkjenn",
        paaPrimar: () => utfoer(id, oh, detalj.saksversjon, ctx, paaFerdig,
                                boks),
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

//: Saksgrunner som krever at operatøren gjør noe UTENFOR systemet, og som
//  derfor får en forklarende note i tillegg til etiketten. Lukket mengde:
//  en ukjent årsak vises som etikett alene, aldri med feil forklaring.
const FORKLARTE_SAKSARSAKER = ["kompensasjon_kreves", "irreversibel_utfort",
  "reversibilitet_ukjent"];

function saksarsakForklaring(arsak) {
  if (!FORKLARTE_SAKSARSAKER.includes(arsak)) return null;
  return el("p", { class: "muted", role: "note",
    text: t(`ui.unntak.saksarsak.${arsak}`) });
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
  // 043 (Gate 14b, Codex P2): saker FØDT av et oppdrag bærer grunnen sin i
  // `arsak`, og fra 043 er tre av verdiene `kompensasjon_kreves`,
  // `irreversibel_utfort` og `reversibilitet_ukjent` — «noen må kompensere
  // manuelt», «en irreversibel handling ble rapportert utført» og «vi vet
  // ikke om virkningen kan reverseres». Uten denne raden var de ikke til å
  // skille fra en hvilken som helst arvet sak, og da er saken født uten å
  // si det den ble født for å si.
  //
  // Forklaringene sier hva systemet KAN vite (Codex P2, runde 8): at
  // kvitteringen ANKOM etter nei-et. Rekkefølgen mellom operatørens klikk
  // og selve utførelsen er ikke målt noe sted — modulen kan ha vært i gang
  // lenge før — og en tekst som påstår den, sender operatøren ut på en
  // gransking med feil utgangspunkt.
  if (detalj.arsak) {
    kvRad(dl, t("ui.kol.saksarsak"),
      t(`saksarsak.${detalj.arsak}`, detalj.arsak));
  }
  const rot = el("div", {}, dl,
    el("h3", { text: t("ui.detalj.begrunnelse") }),
    BegrunnelseKjede(detalj.begrunnelse),
    saksarsakForklaring(detalj.arsak),
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
  // Køene ØKTEN kan lese — én avledning, `sitekart.synligeSakstyper`, samme
  // regel som serveren håndhever. `normal` er første valg og dermed
  // uendret inngang for alle: den er også serverens standard.
  const koer = synligeSakstyper(ctx);
  // `sort` bor her, ikke i tabellen: den bygges på nytt ved hvert statusbytte og
  // hver «Vis mer», og eiers kolonnevalg skal ikke nullstilles av det.
  const st = { status: null, sakstype: koer[0], rader: [], neste: null,
               sort: null };

  function rad(r) {
    return {
      id: r.id,
      celler: {
        ts: Tidspunkt(r.ts),
        handling: r.handling,
        kategori: KategoriTag(r.kategori),
        status: t(`status.${r.status}`, r.status),
        prioritet: t(`prioritet.${r.prioritet}`, r.prioritet),
        // 043: en sak født av et oppdrag skal være til å skille fra en
        // arvet sak i LISTEN — det er der operatøren leter.
        saksarsak: r.arsak ? t(`saksarsak.${r.arsak}`, r.arsak)
          : t("saksarsak.ingen"),
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

  // Køvelgeren finnes bare når det ER noe å velge mellom. Uten
  // `security:read` er `normal` den eneste køen økten kan lese, og en velger
  // med ett alternativ er en kontroll som later som den tilbyr et valg — og
  // som ville antydet at det finnes andre køer, den opplysningen v3-delta
  // pkt. 5 nettopp verner (`app.py:84`: at en sikkerhetssak FINNES er selv
  // den beskyttede opplysningen).
  function kofilter() {
    if (koer.length < 2) return null;
    const bar = el("div", { class: "filterbar", role: "group",
      "aria-label": t("ui.unntak.sakstype") });
    for (const s of koer) {
      const b = el("button", { class: "knapp", type: "button",
        text: t(`sakstype.${s}`, s),
        "aria-pressed": String(st.sakstype === s) });
      b.addEventListener("click", () => {
        if (st.sakstype === s) return;
        // Købytte er en NY liste, ikke et filter over den gamle: cursoren
        // hører til køen den ble delt ut i, og `lastForste` nullstiller
        // begge deler. Statusvalget følger med — det er leserens spørsmål,
        // ikke køens.
        st.sakstype = s; lastForste();
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
            { nokkel: "saksarsak", tittel: t("ui.kol.saksarsak") },
          ],
          rader: st.rader.map(rad),
          sort: st.sort,
          paaSort: (s) => { st.sort = s; },
        })
      : TomTilstand({});
    sett(hoved,
      ...flateHode(t("ui.unntak.tittel")),
      kofilter(),
      filterbar(),
      innhold,
      CursorNavigasjon({ neste: st.neste, paaMer: lastMer,
        paaOppdater: lastForste }));
  }

  function sok(cursor) {
    return hentJson("/v1/unntak", { limit: 50, sakstype: st.sakstype,
                                    status: st.status, cursor });
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
