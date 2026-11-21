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
         IngenTilgangFeil, opprettInvitasjon } from "../api.js";
import { Tidspunkt, Feiltilstand, TomTilstand, TilgangsVakt,
         BeslutningBadge, KategoriTag } from "../komponenter.js";
import { flateHode } from "./felles.js";
import { byggRuter, kanForvaltePolicy } from "../sitekart.js";

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

// Hva et kort sier når modulen ikke er klar, etter HVA som mangler.
// Serveren sender `mangler`; uten feltet er det grensene (den eldste formen).
const MANGLERTEKST = { register: "ui.oppsett.mangler_register",
  tilkobling: "ui.oppsett.mangler_tilkobling" };
const MANGLERKNAPP = { register: "ui.oppsett.registrer",
  tilkobling: "ui.oppsett.koble_til" };

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

// «Går av seg selv»: autopilotens handlinger og om kundens policy slår
// dem på. Serveren avgjør (samme regel som bestillingsveien: handlingen
// må være tillatt for `agent`); her vises bare svaret.
function automatikkDel(liste, ctx) {
  const tittel = el("h3", { class: "oppsett-under",
    text: t("ui.oppsett.auto.tittel") });
  if (liste === null) {
    return [tittel, el("p", { class: "muted",
      text: t("ui.oppsett.auto.ingen_policy") })];
  }
  if (!liste.length) return [];
  const rad = el("ul", { class: "brikkerad oppsett-auto",
    "aria-label": t("ui.oppsett.auto.tittel") });
  for (const a of liste) {
    const navn = t(`ui.oppsett.auto.${a.handling.replaceAll(".", "_")}`);
    rad.append(el("li", { class: `brikke brikke-tekst ${a.paa ? "paa" : "av"}` },
      el("span", { class: "brikke-navn", text: navn }),
      el("span", { class: a.paa ? "" : "muted",
        text: t(a.paa ? "ui.oppsett.auto.paa" : "ui.oppsett.auto.av") })));
  }
  const ut = [tittel, rad];
  if (liste.some((a) => !a.paa)) {
    const tekst = el("p", { class: "muted oppsett-auto-tekst",
      text: t("ui.oppsett.auto.av_tekst") });
    if (kanForvaltePolicy({ scopes: ctx.scopes || [] })) {
      tekst.append(" ", el("a", { href: "#/policyadmin",
        text: t("ui.oppsett.auto.endre") }));
    }
    ut.push(tekst);
  }
  return ut;
}

// FIRE ØYNE, SAGT DER KUNDEN STÅR. En policyendring må godkjennes av en
// annen enn den som skrev den. Har firmaet bare én som kan godkjenne, blir
// runden stående åpen — og før dette sa ingenting hvorfor, eller hvor en
// godkjenner legges til. Invitasjonen lages HER, med rollen som faktisk kan
// godkjenne (policyforvalter), så ingen må vite at den heter det.
function godkjenningDel(g, ctx) {
  if (!g) return [];
  // 227: uten krav om en uavhengig godkjenner er «bare én som kan
  // godkjenne» ikke et hinder — da sier kortet det heller ikke.
  const enHolder = g.uavhengig_godkjenning === false;
  const mangler = !enHolder && g.kan_godkjenne < 2;
  if (!mangler && !g.apen_runde) return [];
  const kort = el("div", { class: "kort oppsett-kort oppsett-godkjenning" });
  if (mangler) {
    kort.append(
      el("h3", { text: t("ui.oppsett.godkjenner.mangler_tittel") }),
      el("p", { class: "muted",
        text: t("ui.oppsett.godkjenner.mangler_tekst") }));
    if (g.invitasjon_ute) {
      kort.append(el("p", { text: t("ui.oppsett.godkjenner.invitasjon_ute") }));
    }
    if ((ctx.scopes || []).includes("firma:inviter")) {
      const knapp = el("button", { class: "knapp primar", type: "button",
        text: t(g.invitasjon_ute ? "ui.oppsett.godkjenner.ny_lenke"
          : "ui.oppsett.godkjenner.inviter") });
      const feil = el("p", { class: "oppsett-feil", role: "alert" });
      const lenkeboks = el("div", { class: "oppsett-lenke" });
      // Stabil over nye klikk til opprettelsen lykkes: et tapt svar skal
      // gi SAMME invitasjon, ikke to.
      let idem = nyIdempotensnokkel();
      knapp.addEventListener("click", () => {
        knapp.disabled = true;
        feil.textContent = "";
        opprettInvitasjon({ roller: ["policyforvalter"] }, idem).then((svar) => {
          idem = nyIdempotensnokkel();
          const url = `${window.location.origin}/?visning=blimed`
            + `&t=${encodeURIComponent(svar.tenant)}`
            + `&k=${encodeURIComponent(svar.token)}`;
          // LENKEN VISES ÉN GANG — tokenet lagres ikke og kan ikke hentes
          // igjen. Derfor et felt som kan merkes og kopieres.
          const felt = el("input", { class: "felt-inp", readOnly: true,
            value: url, id: "godkjenner-lenke" });
          // Kopiknappen finnes bare der nettleseren kan kopiere; feltet
          // er uansett merket, så Ctrl+C virker.
          let kopier = null;
          if (navigator.clipboard && navigator.clipboard.writeText) {
            kopier = el("button", { class: "knapp", type: "button",
              text: t("ui.oppsett.godkjenner.kopier") });
            kopier.addEventListener("click", () => {
              navigator.clipboard.writeText(url).then(() => {
                kopier.textContent = t("ui.oppsett.godkjenner.kopiert");
              }).catch(() => { felt.focus(); felt.select(); });
            });
          }
          sett(lenkeboks,
            el("label", { for: "godkjenner-lenke",
              text: t("ui.oppsett.godkjenner.lenke") }),
            felt, kopier && el("div", { class: "knapperad" }, kopier),
            el("p", { class: "muted",
              text: t("ui.oppsett.godkjenner.lenke_hjelp")
                .replace("{dato}", String(svar.utloper).slice(0, 10)) }));
          felt.focus();
          felt.select();
          knapp.disabled = false;
          knapp.textContent = t("ui.oppsett.godkjenner.ny_lenke");
        }).catch((e) => {
          if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
          knapp.disabled = false;
          feil.textContent = t("ui.oppsett.godkjenner.feil");
        });
      });
      kort.append(el("div", { class: "knapperad" }, knapp), feil, lenkeboks);
    } else {
      kort.append(el("p", { text: t("ui.oppsett.godkjenner.be_admin") }));
    }
  }
  if (g.apen_runde) {
    kort.append(
      el(mangler ? "h4" : "h3",
        { text: t("ui.oppsett.godkjenner.venter_tittel") }),
      el("p", { class: "muted", text: t(mangler
        ? "ui.oppsett.godkjenner.venter_tekst_mangler"
        : "ui.oppsett.godkjenner.venter_tekst") }));
    if (kanForvaltePolicy(ctx)) {
      kort.append(el("div", { class: "knapperad" },
        el("a", { class: mangler ? "knapp" : "knapp primar",
          href: "#/policyadmin",
          text: t("ui.oppsett.godkjenner.apne") })));
    }
  }
  return [kort];
}

function oppsettInnhold(d, ruter, ctx, paaLagret) {
  const moduler = (d.moduler || []).filter((m) => ruter.has(m.modul));
  const godkjenn = godkjenningDel(d.godkjenning, ctx);
  // Uten moduler OG uten noe å gjøre for godkjenningen er blokken tom.
  if (!moduler.length && !godkjenn.length) return null;
  if (!moduler.length) {
    return { tittel: t("ui.oppsett.tittel"), noder: godkjenn };
  }
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
  ut.push(...godkjenn);

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
          // Et tomt REGISTER eller en manglende TILKOBLING er noe annet enn
          // manglende grenser: kunden skal legge inn det første eller koble
          // til, ikke velge et tall.
          el("p", { class: "muted", text: t(MANGLERTEKST[m.mangler]
            || "ui.oppsett.mangler_krav") }),
          el("a", { class: "knapp primar", href: hash(m),
            text: t(MANGLERKNAPP[m.mangler] || "ui.oppsett.sett_opp"),
            "aria-label": `${t(MANGLERKNAPP[m.mangler]
              || "ui.oppsett.sett_opp")}: ${navn(m)}` }));
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
  if (d.automatikk !== undefined) ut.push(...automatikkDel(d.automatikk, ctx));
  return { tittel: (mangler.length || godkjenn.length)
    ? t("ui.oppsett.tittel") : t("ui.oppsett.tittel_klar"), noder: ut };
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
