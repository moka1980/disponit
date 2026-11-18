// Oversikt — dashbordet §2.3 beskriver for Sentrum:
// «KPI-kort + prioriterte varsler + siste aktiviteter».
//
// KPI-kortene er de fire telleverdiene fra /v1/oversikt (telling, ikke
// M-16-KPI-er — noten under kortene sier det). Prioriterte varsler er de ÅPNE
// unntakene: sakene som venter på et menneske er per definisjon det
// prioriterte. Siste aktiviteter er de ferskeste beslutningene.
//
// HVER BLOKK LASTER FOR SEG. Dashbordet er tre uavhengige spørsmål, og et
// 5xx på unntakslisten skal ikke rive bort beslutningstallene som alt står
// på skjermen — `medStatus` (hele-siden-varianten) ville gjort nettopp det.
// En blokk som feiler viser sin egen feiltilstand med «Prøv igjen» som bare
// laster DEN blokken på nytt.
//
// Tidssonen er UTC fra serveren; visning lokaliseres av Tidspunkt.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson, UautorisertFeil, IngenTilgangFeil } from "../api.js";
import { Tidspunkt, Feiltilstand, TomTilstand, TilgangsVakt,
         BeslutningBadge, KategoriTag } from "../komponenter.js";
import { flateHode } from "./felles.js";
import { byggRuter } from "../sitekart.js";

function stat(klasse, tall, tekst) {
  return el("div", { class: `stat ${klasse}`.trim() },
    el("b", { text: String(tall) }),
    el("span", { text: tekst }));
}

// Hvilke saker som er ÅPNE avgjøres av serveren (`GET /v1/unntak?status=apen`),
// ikke her. To ting gikk galt da denne flaten svarte på det selv med en
// tillatelsesliste over statuser:
//
//   * listen var en KOPI av statusmaskinen i migrasjon 011, og kopien hadde
//     alt sakket etter — de fire godkjenningsstatusene fra PR-012
//     (`venter_godkjenning`, `venter_andre_godkjenner`, `godkjenning_klar`,
//     `venter_utførelse`) manglet, så saker som ventet på en godkjenner
//     forsvant fra «prioriterte varsler» mens de var på sitt mest prioriterte;
//   * og filtreringen skjedde ETTER serverens `LIMIT`. Var de åtte ferskeste
//     sakene løst eller avvist, viste dashbordet «ingenting venter» selv om
//     en eldre åpen sak lå rett bak sidegrensen.
//
// Begge forsvinner når spørsmålet stilles der dataene er.

// Én selvstendig dashbordblokk: overskrift + eget lastings-/feilløp.
// `tegnInnhold(data) -> node`. Feiler lastingen, får blokken sin egen
// Feiltilstand med «Prøv igjen» som kun berører denne blokken.
function blokk(ctx, tittel, lastFn, tegnInnhold) {
  const rot = el("section", { class: "dash-blokk", "aria-label": tittel },
    el("h2", { text: tittel }));
  const kropp = el("div", { class: "dash-kropp" });
  rot.append(kropp);
  const last = () => {
    sett(kropp, el("p", { class: "muted", text: t("ui.laster") }));
    lastFn().then((d) => sett(kropp, tegnInnhold(d)))
      .catch((e) => {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        if (e instanceof IngenTilgangFeil) { sett(kropp, TilgangsVakt({})); return; }
        sett(kropp, Feiltilstand({ paaProvIgjen: last }));
      });
  };
  last();
  return rot;
}

function lenkeknapp(tekst, hash) {
  const b = el("button", { class: "knapp liten", type: "button", text: tekst });
  b.addEventListener("click", () => { window.location.hash = hash; });
  return b;
}

function unntaksliste(rader) {
  const aapne = rader.slice(0, 5);
  if (!aapne.length) {
    return TomTilstand({ tittel: t("ui.dashbord.ingen_varsler"),
      tekst: t("ui.dashbord.ingen_varsler_tekst") });
  }
  const ul = el("ul", { class: "dash-liste" });
  for (const r of aapne) {
    ul.append(el("li", { class: "dash-rad" },
      el("span", { class: "dash-handling", text: r.handling }),
      document.createTextNode(" · "),
      KategoriTag(r.kategori),
      document.createTextNode(" · "),
      el("span", { class: "sub" }, Tidspunkt(r.ts))));
  }
  return el("div", {}, ul,
    lenkeknapp(t("ui.dashbord.til_unntak"), "#/unntak"));
}

function aktivitetsliste(rader) {
  const siste = rader.slice(0, 8);
  if (!siste.length) {
    return TomTilstand({ tittel: t("ui.dashbord.ingen_aktivitet"),
      tekst: t("ui.dashbord.ingen_aktivitet_tekst") });
  }
  const ul = el("ul", { class: "dash-liste" });
  for (const r of siste) {
    ul.append(el("li", { class: "dash-rad" },
      BeslutningBadge(r.policybeslutning),
      document.createTextNode(" "),
      el("span", { class: "dash-handling", text: r.handling }),
      document.createTextNode(" · "),
      el("span", { class: "sub" }, Tidspunkt(r.ts))));
  }
  return el("div", {}, ul,
    lenkeknapp(t("ui.dashbord.til_beslutninger"), "#/beslutninger"));
}

// «Kom i gang»: én rad per tildelt modul fra `/v1/oppsett`. Serveren har
// alt avgjort hvilke moduler økten får se; her slås bare ruten opp, så
// knappen fører rett til skjemaet som mangler. Blokken står ØVERST fordi
// en ny kunde ellers møter fire nuller og ingen vei videre.
function ruteForModul(ctx) {
  const kart = new Map();
  for (const r of byggRuter({ scopes: ctx.scopes || [] })) {
    if (r.modulflate != null && !kart.has(r.modulflate)) {
      kart.set(r.modulflate, r.nokkel);
    }
  }
  return kart;
}

function funnTekst(n) {
  if (n == null) return t("ui.oppsett.funn_ukjent");
  if (n === 0) return t("ui.oppsett.ingen_funn");
  return t(n === 1 ? "ui.oppsett.ett_funn" : "ui.oppsett.funn")
    .replace("{antall}", String(n));
}

function oppsettInnhold(d, ruter) {
  const moduler = (d.moduler || []).filter((m) => ruter.has(m.modul));
  if (!moduler.length) return null;
  const mangler = moduler.filter((m) => m.tilgjengelig && m.klar === false);
  const klare = moduler.filter((m) => m.tilgjengelig && m.klar !== false);
  const borte = moduler.filter((m) => !m.tilgjengelig);
  const navn = (m) => t(`ui.nav.${ruter.get(m.modul)}`);
  const hash = (m) => `#/${ruter.get(m.modul)}`;

  const totalt = moduler.length;
  const ferdig = totalt - mangler.length;
  const ut = [el("p", { class: "oppsett-status" },
    el("span", { text: t("ui.oppsett.status")
      .replace("{klare}", String(ferdig)).replace("{totalt}", String(totalt)) })),
  el("progress", { class: "oppsett-fremdrift", max: String(totalt),
    value: String(ferdig), "aria-label": t("ui.oppsett.fremdrift") })];

  if (mangler.length) {
    const ul = el("ul", { class: "oppsett-flis" });
    for (const m of mangler) {
      const knapp = el("a", { class: "knapp primar", href: hash(m),
        text: t("ui.oppsett.sett_opp") });
      knapp.setAttribute("aria-label",
        `${t("ui.oppsett.sett_opp")}: ${navn(m)}`);
      ul.append(el("li", { class: "kort oppsett-kort" },
        el("h3", { text: navn(m) }),
        el("p", { class: "muted", text: t("ui.oppsett.mangler_krav") }),
        knapp));
    }
    ut.push(ul);
  } else {
    ut.push(el("p", { class: "muted", text: t("ui.oppsett.alt_klart") }));
  }

  if (klare.length || borte.length) {
    const rad = el("ul", { class: "brikkerad oppsett-brikker",
      "aria-label": t("ui.oppsett.i_drift") });
    const sortert = [...klare].sort(
      (a, b) => (b.apne_funn || 0) - (a.apne_funn || 0));
    for (const m of sortert) {
      rad.append(el("li", {},
        el("a", { class: "brikke brikke-tekst", href: hash(m) },
          el("span", { class: "brikke-navn", text: navn(m) }),
          el("span", { class: m.apne_funn ? "" : "muted",
            text: funnTekst(m.apne_funn) }))));
    }
    for (const m of borte) {
      rad.append(el("li", {},
        el("a", { class: "brikke brikke-tekst", href: hash(m) },
          el("span", { class: "brikke-navn", text: navn(m) }),
          el("span", { class: "muted",
            text: t("ui.oppsett.utilgjengelig") }))));
    }
    ut.push(el("h3", { class: "oppsett-under", text: t("ui.oppsett.i_drift") }),
      rad);
  }
  return { tittel: mangler.length ? t("ui.oppsett.tittel")
    : t("ui.oppsett.tittel_klar"), noder: ut };
}

function oppsettBlokk(ctx) {
  const ruter = ruteForModul(ctx);
  const rot = el("section", { class: "kort dash-oppsett",
    "aria-labelledby": "oppsett-tittel" });
  rot.hidden = true;
  hentJson("/v1/oppsett").then((d) => {
    const innhold = oppsettInnhold(d, ruter);
    if (!innhold) return;
    sett(rot, el("h2", { id: "oppsett-tittel", text: innhold.tittel }),
      ...innhold.noder);
    rot.hidden = false;
  }).catch((e) => {
    if (e instanceof UautorisertFeil) ctx.paaUautorisert();
    // Andre feil: blokken forblir skjult — dashbordet under står uansett.
  });
  return rot;
}

export function visOversikt(hoved, ctx) {
  // KPI-kortene beholder heltside-semantikken de alltid har hatt: feiler
  // selve helsebildet, feiler siden — det er dashbordets rygg. De to
  // listene under er blokker med egne løp.
  sett(hoved,
    ...flateHode(t("ui.oversikt.tittel"), t("ui.oversikt.undertittel")),
    oppsettBlokk(ctx),
    el("div", { class: "dash-kpi" }));

  const kpi = hoved.querySelector(".dash-kpi");
  const lastKpi = () => {
    sett(kpi, el("p", { class: "muted", text: t("ui.laster") }));
    hentJson("/v1/oversikt").then((d) => {
      sett(kpi,
        el("div", { class: "cards" },
          stat("tillat", d.tillatt, t("ui.oversikt.tillatt")),
          stat("stopp", d.stoppet, t("ui.oversikt.stoppet")),
          stat("unntak", d.unntak, t("ui.oversikt.unntak")),
          stat("", d.totalt, t("ui.oversikt.totalt"))),
        el("p", { class: "muted" },
          `${t("ui.oversikt.oppdatert")}: `, Tidspunkt(d.vindu_slutt),
          ` (${d.tidssone})`),
        el("p", { class: "legend muted",
          text: t("ui.oversikt.telling_note") }));
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      // 403 er en tilstand PÅ flaten, aldri en innlogging (speiler medStatus).
      if (e instanceof IngenTilgangFeil) { sett(kpi, TilgangsVakt({})); return; }
      sett(kpi, Feiltilstand({ paaProvIgjen: lastKpi }));
    });
  };
  lastKpi();

  hoved.append(el("div", { class: "dash-grid" },
    blokk(ctx, t("ui.dashbord.varsler"),
      () => hentJson("/v1/unntak", { limit: 8, status: "apen" }),
      (d) => unntaksliste(d.saker || [])),
    blokk(ctx, t("ui.dashbord.aktivitet"),
      () => hentJson("/v1/beslutninger", { limit: 8 }),
      (d) => aktivitetsliste(d.rader || []))));
}
