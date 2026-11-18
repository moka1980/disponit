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
import { hentJson, brukAnbefalteGrenser, nyIdempotensnokkel, UautorisertFeil,
         IngenTilgangFeil } from "../api.js";
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

// Feltnavnene er modulenes EGNE skjemaetiketter, så kunden leser de
// samme ordene her som inne i modulen.
const FELTETIKETT = {
  14: { mva_slingring_ore: "ui.faktura.terskel.slingring",
        belopsgrense_ore: "ui.faktura.terskel.belopsgrense",
        kontrollfrist_dogn: "ui.faktura.terskel.kontrollfrist",
        dublettvindu_dogn: "ui.faktura.terskel.dublettvindu" },
  24: { prisstigning_promille: "ui.leverandor.terskel.prisstigning",
        sla_brudd_grense: "ui.leverandor.terskel.bruddgrense",
        avtale_varsel_dogn: "ui.leverandor.terskel.varsel",
        maling_stillhet_dogn: "ui.leverandor.terskel.stillhet" },
  25: { budsjettvarsel_promille: "ui.prosjekt.terskel.budsjett",
        milepael_frist_dogn: "ui.prosjekt.terskel.milepaelfrist",
        stillhet_dogn: "ui.prosjekt.terskel.stillhet" },
  26: { rabattgrense_promille: "ui.prisbok.terskel.rabatt",
        utlop_varsel_dogn: "ui.prisbok.terskel.varsel",
        uten_pris_dogn: "ui.prisbok.terskel.utenpris" },
  27: { stille_dogn: "ui.lager.terskel.stille",
        uten_punkt_dogn: "ui.lager.terskel.punkt",
        telleintervall_dogn: "ui.lager.terskel.telle" },
  39: { normaltid_minutter_dag: "ui.lonn.terskel.dag",
        normaltid_minutter_uke: "ui.lonn.terskel.uke",
        avvik_minutter: "ui.lonn.terskel.avvik",
        uten_plan_dogn: "ui.lonn.terskel.uten_plan",
        vurderingsvindu_dogn: "ui.lonn.terskel.vindu" },
  41: { uavklart_dogn: "ui.betaling.terskel.uavklart",
        belopsavvik_ore: "ui.betaling.terskel.avvik",
        reautorisasjon_dogn: "ui.betaling.terskel.reautorisasjon" },
  42: { reverifikasjon_dogn: "ui.kontovakt.terskel.reverifikasjon",
        uverifisert_dogn: "ui.kontovakt.terskel.uverifisert" },
  47: { varselfrist_dogn: "ui.myndighet.krav.varsel",
        eskaleringsfrist_dogn: "ui.myndighet.krav.eskalering",
        regelvarsel_dogn: "ui.myndighet.krav.regelvarsel" },
  50: { slettevarsel_dogn: "ui.journal.krav.varsel",
        sletteplan_maks_dogn: "ui.journal.krav.maks",
        kildevarsel_dogn: "ui.journal.krav.kilde" },
  51: { frist_varsel_dogn: "ui.tilskudd.krav.frist",
        kildepost_gyldig_dogn: "ui.tilskudd.krav.kilde_gyldig",
        usikkerhet_prosent: "ui.tilskudd.krav.usikkerhet" },
  54: { avviksfrist_dogn: "ui.ehf.krav.avvik",
        utlopsvarsel_dogn: "ui.ehf.krav.utlop" },
};

export function feltverdi(felt, n, sprak = "nb") {
  const tall = (x, des = 0) => new Intl.NumberFormat(sprak,
    { maximumFractionDigits: des }).format(x);
  if (felt.endsWith("_dogn")) {
    return t("ui.oppsett.enhet.dogn").replace("{n}", tall(n));
  }
  if (felt.endsWith("_prosent")) {
    return t("ui.oppsett.enhet.prosent").replace("{n}", tall(n));
  }
  if (felt.endsWith("_promille")) {
    return t("ui.oppsett.enhet.prosent").replace("{n}", tall(n / 10, 1));
  }
  if (felt.endsWith("_ore")) {
    return t("ui.oppsett.enhet.kr").replace("{n}", tall(n / 100, 2));
  }
  if (felt.includes("minutter")) {
    return t("ui.oppsett.enhet.timer").replace("{n}", tall(n / 60, 2));
  }
  if (felt === "sla_brudd_grense") {
    return t("ui.leverandor.terskel.bruddgrense_verdi").replace("{n}", tall(n));
  }
  return tall(n);
}

function anbefaltListe(modul, anbefalt, sprak) {
  const dl = el("dl", { class: "oppsett-anbefalt" });
  for (const [felt, n] of Object.entries(anbefalt.kropp)) {
    dl.append(el("div", {},
      el("dt", { text: t((FELTETIKETT[modul] || {})[felt] || felt) }),
      el("dd", { text: feltverdi(felt, n, sprak) })));
  }
  return dl;
}

function oppsettInnhold(d, ruter, ctx, paaLagret) {
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
      const kort = el("li", { class: "kort oppsett-kort" },
        el("h3", { text: navn(m) }));
      if (m.anbefalt) {
        kort.append(
          el("p", { class: "muted", text: t("ui.oppsett.anbefalt_tekst") }),
          anbefaltListe(m.modul, m.anbefalt, ctx.sprak));
        const bruk = el("button", { class: "knapp primar", type: "button",
          text: t("ui.oppsett.bruk_anbefalt"),
          "aria-label": `${t("ui.oppsett.bruk_anbefalt")}: ${navn(m)}` });
        const feil = el("p", { class: "oppsett-feil", role: "alert" });
        // Stabil over nye klikk: et tapt svar skal spilles av, ikke skrives
        // på nytt som en ny versjon.
        const idem = nyIdempotensnokkel();
        bruk.addEventListener("click", () => {
          bruk.disabled = true;
          feil.textContent = "";
          brukAnbefalteGrenser(m.anbefalt, idem).then(() => {
            paaLagret(t("ui.oppsett.lagret").replace("{modul}", navn(m)));
          }).catch((e) => {
            if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
            bruk.disabled = false;
            feil.textContent = t("ui.oppsett.lagring_feilet");
          });
        });
        kort.append(el("div", { class: "knapperad" }, bruk,
          el("a", { class: "oppsett-velg", href: hash(m),
            text: t("ui.oppsett.velg_selv"),
            "aria-label": `${t("ui.oppsett.velg_selv")}: ${navn(m)}` })),
          feil);
      } else {
        kort.append(
          el("p", { class: "muted", text: t("ui.oppsett.mangler_krav") }),
          el("a", { class: "knapp primar", href: hash(m),
            text: t("ui.oppsett.sett_opp"),
            "aria-label": `${t("ui.oppsett.sett_opp")}: ${navn(m)}` }));
      }
      ul.append(kort);
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
  // Statuslinjen står UTENFOR innholdet som tegnes på nytt, ellers ville
  // skjermleseren mistet meldingen i det øyeblikket den ble skrevet.
  const melding = el("p", { class: "oppsett-melding", role: "status" });
  const innholdsrot = el("div", { class: "oppsett-innhold" });
  rot.append(innholdsrot, melding);
  rot.hidden = true;
  const last = (tekst) => {
    hentJson("/v1/oppsett").then((d) => {
      const innhold = oppsettInnhold(d, ruter, ctx, last);
      if (!innhold) { rot.hidden = true; return; }
      sett(innholdsrot,
        el("h2", { id: "oppsett-tittel", text: innhold.tittel,
          tabindex: "-1" }),
        ...innhold.noder);
      rot.hidden = false;
      if (tekst) {
        melding.textContent = tekst;
        innholdsrot.querySelector("h2").focus();
      }
    }).catch((e) => {
      if (e instanceof UautorisertFeil) ctx.paaUautorisert();
      // Andre feil: blokken forblir som den er — dashbordet står uansett.
    });
  };
  last();
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
