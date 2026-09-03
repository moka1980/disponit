// Sanksjonskontrollen (M-49 v1) — KONTROLLEN, IKKE BLOKKERINGEN.
//
// FLATENS VANSKELIGSTE JOBB er å vise et treff uten å foreslå hva man
// skal gjøre med det.
//
// Spesifikasjonen vil at modulen skal blokkere fail-closed. v1 gjør
// det ikke, og den tyngste grunnen er at det ikke finnes noe å
// blokkere med: et register stanser ingen handel. Beslutningen, med
// motargumentet og utløseren, står i toppen av migrasjon 117.
//
// FOR FLATEN BETYR DET TO TING, OG BEGGE ER FRAVÆR:
//
//   * DET FINNES INGEN «BLOKKER»-KNAPP. Ikke fordi den ville vært
//     vanskelig, men fordi den ville løyet: knappen ville skrevet et
//     flagg ingen leser, og en bruker som trykket den ville trodd
//     handelen var stanset. Det er `alarm`-feltet fra 115 om igjen —
//     og verre, fordi det her ville vært synlig som en handling.
//
//   * DET FINNES INGEN «AVFEI»-KNAPP OG INGEN MASSEHANDLING. Et treff
//     avklares ett om gangen, med en konklusjon fra et lukket sett og
//     en begrunnelse på minst tolv tegn. Ingen «lukk alle under
//     90 %», ingen avkrysningsbokser med en samlet knapp under. En kø
//     som går ned ser ut som saksbehandling, og det er nettopp derfor
//     den ikke skal kunne gå ned av seg selv.
//
// TREFFET VISES MED SIN MATCHTYPE, ALLTID. «Eksakt identifikator» og
// «navnelikhet» er ikke grader av det samme: den første er den ene
// klassen som en dag kan blokkere maskinelt, den andre er en kandidat
// et menneske må se på. En flate som viste dem likt ville utvisket
// nøyaktig det skillet hele datamodellen er formet rundt.
//
// UAVKLARTE TREFF STÅR ØVERST I SAMMENDRAGET. Et treff ingen har sett
// på er ikke et vern; det er en udokumentert risiko.
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, avklarSanksjonstreff, hentJson, lukkSanksjonsfunn,
  nyIdempotensnokkel, registrerSanksjonsliste,
  registrerSanksjonssubjekt, settSanksjonskrav,
  settSanksjonssubjektAktiv,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// LUKKEDE SETT, speilet fra 117.
export const KILDER = ["ofac", "eu", "fn"];
export const SUBJEKTTYPER = ["person", "foretak"];
export const MATCHTYPER = ["eksakt_identifikator", "eksakt_navn",
                           "navnelikhet"];
// TRE KONKLUSJONER, IKKE TO. `uavklart_eskalert` er den ærlige
// tredje: en saksbehandler som ikke klarer å avgjøre skal kunne si
// det, i stedet for å velge en av de to for å bli ferdig.
export const KONKLUSJONER = ["bekreftet_treff", "ikke_samme_part",
                             "uavklart_eskalert"];

const KILDETEKST = {
  ofac: "ui.sanksjon.kilde.ofac",
  eu: "ui.sanksjon.kilde.eu",
  fn: "ui.sanksjon.kilde.fn",
};
const TYPETEKST = {
  person: "ui.sanksjon.type.person",
  foretak: "ui.sanksjon.type.foretak",
};
const MATCHTEKST = {
  eksakt_identifikator: "ui.sanksjon.match.eksakt_identifikator",
  eksakt_navn: "ui.sanksjon.match.eksakt_navn",
  navnelikhet: "ui.sanksjon.match.navnelikhet",
};
const KONKLUSJONSTEKST = {
  bekreftet_treff: "ui.sanksjon.konklusjon.bekreftet_treff",
  ikke_samme_part: "ui.sanksjon.konklusjon.ikke_samme_part",
  uavklart_eskalert: "ui.sanksjon.konklusjon.uavklart_eskalert",
};
const MERKE = {
  uavklart_treff: "ui.sanksjon.merke_uavklart",
  ukontrollert_subjekt: "ui.sanksjon.merke_ukontrollert",
  kontroll_utlopt: "ui.sanksjon.merke_utlopt",
  kontroll_mot_gammel_liste: "ui.sanksjon.merke_gammel_liste",
  bekreftet_treff: "ui.sanksjon.merke_bekreftet",
  ingen_liste: "ui.sanksjon.merke_uten_liste",
  ingen_krav: "ui.sanksjon.merke_uten_krav",
};

// MATCHTYPEN SOM TEKST, ALDRI SOM EN GRAD. Se filhodet.
export function matchTekst(matchtype) {
  if (!matchtype) return t("ui.sanksjon.uten_treff");
  const n = MATCHTEKST[matchtype];
  return n ? t(n) : matchtype;
}

// «IKKE KONTROLLERT» ER NOE ANNET ENN «INGEN TREFF» (WCAG 1.4.1 og
// alminnelig ærlighet). Et subjekt ingen har sjekket skal ikke se ut
// som et som er sjekket og funnet rent.
export function kontrollTekst(rad) {
  if (!rad.siste_kontroll) return t("ui.sanksjon.aldri_kontrollert");
  if (rad.siste_utfall === "ingen_treff") {
    return t("ui.sanksjon.ingen_treff");
  }
  return t("ui.sanksjon.antall_treff")
    .replace("{n}", String(rad.apne_treff ?? 0));
}


function felt(id, tekst, kontroll, hjelp) {
  return el("div", { class: "felt" },
    el("label", { for: id, text: t(tekst) }),
    kontroll,
    hjelp ? el("p", { class: "muted", text: t(hjelp) }) : null);
}


function skjemaramme(ctx, last, { skjema, knapp, utfall, send,
                                  tilbakestill, okNokkel, kvitter }) {
  let idem = null;
  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("change", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled) return;
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    try {
      await send(idem);
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.sanksjon.feil.tilstand")
          : t("ui.sanksjon.feil.generell") }));
      return;
    }
    idem = null;
    knapp.disabled = false;
    if (tilbakestill) tilbakestill();
    meldLive(t(okNokkel));
    // KVITTERINGEN SKAL OVERLEVE TEGNINGEN (klynge 3-rettingen).
    kvitter(t(okNokkel));
    await last();
  });
}


// SAMMENDRAGET. Uavklarte treff står FØRST, av grunnen i filhodet.
export function sammendrag(s) {
  const p = el("p");
  p.append(el("strong", {
    text: t("ui.sanksjon.uavklarte_treff")
      .replace("{n}", String(s.uavklarte_treff ?? 0)) }));
  if (s.bekreftede_treff > 0) {
    // ET BEKREFTET TREFF ER IKKE ET TALL BLANT ANDRE. Noen har sagt at
    // parten står på lista.
    p.append(" ", el("strong", {
      text: t("ui.sanksjon.bekreftede_treff")
        .replace("{n}", String(s.bekreftede_treff)) }));
  }
  p.append(" ", el("span", {
    text: t("ui.sanksjon.tellinger")
      .replace("{subjekter}", String(s.subjekter ?? 0))
      .replace("{aktive}", String(s.aktive ?? 0))
      .replace("{kontrollerte}", String(s.kontrollerte ?? 0)) }));
  p.append(" ", el("span", { class: "muted",
    text: s.nyeste_listeversjon
      ? t("ui.sanksjon.nyeste_liste")
          .replace("{liste}", s.nyeste_listeversjon)
      : t("ui.sanksjon.ingen_liste") }));
  if (s.apne_funn > 0) {
    p.append(" ", el("strong", {
      text: t("ui.sanksjon.apne_funn")
        .replace("{n}", String(s.apne_funn)) }));
  }
  if (!s.har_krav) {
    p.append(" ", el("strong", { text: t("ui.sanksjon.ingen_krav") }));
  }
  if (s.vist < s.subjekter) {
    p.append(" ", el("strong", {
      text: t("ui.sanksjon.avkortet").replace("{vist}",
                                              String(s.vist)) }));
  }
  return p;
}


export function subjektTabell(subjekter, apne) {
  const tbody = el("tbody");
  for (const s of subjekter) {
    const knapp = el("button", { type: "button",
      text: t("ui.sanksjon.knapp.apne") });
    knapp.addEventListener("click", () => apne(s));
    // MATCHTYPEN VISES SOM TEKST, aldri som en farge alene (WCAG
    // 1.4.1): «eksakt identifikator» og «navnelikhet» er ikke grader
    // av det samme, og forskjellen må kunne leses.
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: s.navn_oppgitt }),
      el("td", { text: s.ekstern_ref }),
      el("td", { text: t(TYPETEKST[s.subjekttype]
                         || s.subjekttype) }),
      el("td", { text: s.har_identifikator ? t("ui.sanksjon.ja")
                                           : t("ui.sanksjon.nei") }),
      el("td", { text: kontrollTekst(s) }),
      el("td", { text: matchTekst(s.groveste_matchtype) }),
      el("td", { class: "tall", text: String(s.apne_funn ?? 0) }),
      el("td", { text: s.aktiv ? t("ui.sanksjon.ja")
                               : t("ui.sanksjon.nei") }),
      el("td", {}, knapp)));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.sanksjon.liste.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.sanksjon.kol.navn") }),
        el("th", { scope: "col", text: t("ui.sanksjon.kol.ref") }),
        el("th", { scope: "col", text: t("ui.sanksjon.kol.type") }),
        el("th", { scope: "col", text: t("ui.sanksjon.kol.har_id") }),
        el("th", { scope: "col", text: t("ui.sanksjon.kol.kontroll") }),
        el("th", { scope: "col", text: t("ui.sanksjon.kol.groveste") }),
        el("th", { scope: "col", text: t("ui.sanksjon.kol.funn") }),
        el("th", { scope: "col", text: t("ui.sanksjon.kol.aktiv") }),
        el("th", { scope: "col",
                   text: t("ui.sanksjon.kol.handling") }))),
      tbody));
}


export function listeTabell(lister) {
  const tbody = el("tbody");
  for (const l of lister) {
    tbody.append(el("tr", {},
      el("th", { scope: "row",
                 text: t(KILDETEKST[l.kilde] || l.kilde) }),
      el("td", { text: l.listeversjon }),
      el("td", { text: l.gjelder_fra }),
      el("td", { class: "tall",
                 text: String(l.antall_oppforinger) }),
      // INNHOLDSSUMMEN VISES, forkortet. «Sto de på lista DEN DAGEN»
      // kan ingen svare på uten å kunne peke på nøyaktig hvilken fil.
      el("td", { text: (l.innhold_sha256 || "").slice(0, 12) }),
      el("td", { text: l.er_nyeste ? t("ui.sanksjon.ja")
                                   : t("ui.sanksjon.nei") })));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.sanksjon.lister.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.sanksjon.kol.kilde") }),
        el("th", { scope: "col", text: t("ui.sanksjon.kol.versjon") }),
        el("th", { scope: "col",
                   text: t("ui.sanksjon.kol.gjelder_fra") }),
        el("th", { scope: "col",
                   text: t("ui.sanksjon.kol.oppforinger") }),
        el("th", { scope: "col", text: t("ui.sanksjon.kol.sum") }),
        el("th", { scope: "col", text: t("ui.sanksjon.kol.nyeste") }))),
      tbody));
}


// TREFFENE, AVKLARTE OG UAVKLARTE I SAMME TABELL.
//
// En flate som bare viste de uavklarte ville skjult hva noen faktisk
// konkluderte — og det er nettopp den raden et tilsyn ber om å få se.
// `konklusjon` er tom for de uavklarte, og tomheten er lesbar.
export function treffTabell(treff, avklar) {
  const tbody = el("tbody");
  for (const tr of treff) {
    const handling = el("td", {});
    if (!tr.konklusjon && avklar) {
      const knapp = el("button", { type: "button",
        text: t("ui.sanksjon.knapp.avklar") });
      knapp.addEventListener("click", () => avklar(tr));
      handling.append(knapp);
    }
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: tr.listenavn }),
      el("td", { text: matchTekst(tr.matchtype) }),
      el("td", { class: "tall", text: `${tr.likhet}` }),
      el("td", { text: (tr.matchfelt || []).join(", ") }),
      el("td", { text: `${t(KILDETEKST[tr.kilde] || tr.kilde)}`
                       + ` ${tr.listeversjon}` }),
      el("td", { text: tr.konklusjon
                   ? t(KONKLUSJONSTEKST[tr.konklusjon]
                       || tr.konklusjon)
                   : t("ui.sanksjon.uavklart") }),
      el("td", { text: tr.avklart_av || "–" }),
      handling));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.sanksjon.treff.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col",
                   text: t("ui.sanksjon.kol.listenavn") }),
        el("th", { scope: "col", text: t("ui.sanksjon.kol.matchtype") }),
        el("th", { scope: "col", text: t("ui.sanksjon.kol.likhet") }),
        el("th", { scope: "col", text: t("ui.sanksjon.kol.matchfelt") }),
        el("th", { scope: "col", text: t("ui.sanksjon.kol.liste") }),
        el("th", { scope: "col",
                   text: t("ui.sanksjon.kol.konklusjon") }),
        el("th", { scope: "col", text: t("ui.sanksjon.kol.avklart_av") }),
        el("th", { scope: "col",
                   text: t("ui.sanksjon.kol.handling") }))),
      tbody));
}


// AVKLARINGSSKJEMAET. Flatens vanskeligste element.
//
// ÉN AVKLARING OM GANGEN, og skjemaet åpnes for ETT navngitt treff.
// Det finnes ingen avkrysningsbokser i treffabellen og ingen samlet
// knapp under den. En kø som går ned ser ut som saksbehandling, og det
// er nettopp derfor den ikke skal kunne gå ned av seg selv.
//
// KONKLUSJONEN HAR INGEN FORHÅNDSVALGT VERDI, og knappen er død til
// både konklusjon og begrunnelse er fylt ut. Hadde vi forhåndsvalgt
// «ikke samme part» — den vanligste konklusjonen — ville
// `modulen_avfeide_navnelikhet` vært en port ingen merket, fordi
// feltet alltid hadde vært fylt ut av oss.
function avklaringSkjema(ctx, last, treff, kvitter, lukk) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const konklusjon = el("select", { id: "sk-a-konklusjon",
    name: "konklusjon", required: true });
  konklusjon.append(el("option", { value: "",
    text: t("ui.sanksjon.avklaring.velg") }));
  for (const k of KONKLUSJONER) {
    konklusjon.append(el("option", { value: k,
      text: t(KONKLUSJONSTEKST[k]) }));
  }
  const begrunnelse = el("input", { id: "sk-a-begrunnelse",
    name: "begrunnelse", type: "text", required: true,
    minlength: "12", maxlength: "2000" });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.sanksjon.knapp.lagre_avklaring") });
  const vurder = () => {
    knapp.disabled = !konklusjon.value
      || begrunnelse.value.trim().length < 12;
  };
  konklusjon.addEventListener("change", vurder);
  begrunnelse.addEventListener("input", vurder);

  skjema.append(
    // TREFFET STÅR NAVNGITT I SKJEMAET. En avklaring uten at
    // saksbehandleren ser hva den gjelder, er et klikk.
    el("p", { text: t("ui.sanksjon.avklaring.gjelder")
                .replace("{navn}", treff.listenavn)
                .replace("{match}", matchTekst(treff.matchtype))
                .replace("{likhet}", String(treff.likhet)) }),
    felt("sk-a-konklusjon", "ui.sanksjon.avklaring.konklusjon",
         konklusjon, "ui.sanksjon.avklaring.konklusjon_hjelp"),
    felt("sk-a-begrunnelse", "ui.sanksjon.avklaring.begrunnelse",
         begrunnelse, "ui.sanksjon.avklaring.begrunnelse_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.sanksjon.skjema.avklaring_ok",
    tilbakestill: () => {
      konklusjon.value = ""; begrunnelse.value = "";
      knapp.disabled = true;
      if (lukk) lukk();
    },
    send: (idem) => avklarSanksjonstreff(treff.treff_id,
                                         konklusjon.value,
                                         begrunnelse.value.trim(),
                                         idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.sanksjon.avklaring.tittel") }),
    el("p", { class: "muted",
              text: t("ui.sanksjon.avklaring.hvorfor") }),
    skjema, utfall);
}


function kravSkjema(ctx, last, krav, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const terskel = el("input", { id: "sk-k-terskel", name: "terskel",
    type: "number", required: true, step: "1", min: "50", max: "100" });
  const gyldig = el("input", { id: "sk-k-gyldig", name: "gyldig",
    type: "number", required: true, step: "1", min: "1", max: "3650" });
  const uavklart = el("input", { id: "sk-k-uavklart",
    name: "uavklart", type: "number", required: true, step: "1",
    min: "0", max: "365" });
  const ukontrollert = el("input", { id: "sk-k-ukontrollert",
    name: "ukontrollert", type: "number", required: true, step: "1",
    min: "0", max: "3650" });
  if (krav) {
    terskel.value = String(krav.matchterskel);
    gyldig.value = String(krav.kontroll_gyldig_dogn);
    uavklart.value = String(krav.uavklart_frist_dogn);
    ukontrollert.value = String(krav.ukontrollert_dogn);
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.sanksjon.knapp.lagre_krav") });
  skjema.append(
    felt("sk-k-terskel", "ui.sanksjon.krav.terskel", terskel,
         "ui.sanksjon.krav.terskel_hjelp"),
    felt("sk-k-gyldig", "ui.sanksjon.krav.gyldig", gyldig,
         "ui.sanksjon.krav.gyldig_hjelp"),
    felt("sk-k-uavklart", "ui.sanksjon.krav.uavklart", uavklart,
         "ui.sanksjon.krav.uavklart_hjelp"),
    felt("sk-k-ukontrollert", "ui.sanksjon.krav.ukontrollert",
         ukontrollert, "ui.sanksjon.krav.ukontrollert_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.sanksjon.skjema.krav_ok",
    send: (idem) => settSanksjonskrav({
      matchterskel: Number(terskel.value),
      kontroll_gyldig_dogn: Number(gyldig.value),
      uavklart_frist_dogn: Number(uavklart.value),
      ukontrollert_dogn: Number(ukontrollert.value),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.sanksjon.krav.tittel") }), skjema, utfall);
}


// LISTESKJEMAET. Modulen laster ingen liste selv — porten
// `modulen_hentet_eksternt`. Et menneske har hentet fila og oppgir
// kilde, versjon, dato og INNHOLDSSUM. Summen er påkrevd fordi «sto de
// på lista DEN DAGEN» ikke kan besvares uten å kunne peke på nøyaktig
// hvilken fil.
function listeSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const kilde = el("select", { id: "sk-l-kilde", name: "kilde",
                               required: true });
  kilde.append(el("option", { value: "",
    text: t("ui.sanksjon.liste.velg_kilde") }));
  for (const k of KILDER) {
    kilde.append(el("option", { value: k, text: t(KILDETEKST[k]) }));
  }
  const versjon = el("input", { id: "sk-l-versjon", name: "versjon",
    type: "text", required: true, maxlength: "100" });
  const fra = el("input", { id: "sk-l-fra", name: "fra",
    type: "date", required: true });
  const sum = el("input", { id: "sk-l-sum", name: "sum", type: "text",
    required: true, pattern: "[0-9a-fA-F]{64}", maxlength: "64" });
  const antall = el("input", { id: "sk-l-antall", name: "antall",
    type: "number", required: true, step: "1", min: "0" });
  const knapp = el("button", { type: "submit",
    text: t("ui.sanksjon.knapp.registrer_liste") });
  skjema.append(
    felt("sk-l-kilde", "ui.sanksjon.liste.kilde", kilde, null),
    felt("sk-l-versjon", "ui.sanksjon.liste.versjon", versjon,
         "ui.sanksjon.liste.versjon_hjelp"),
    felt("sk-l-fra", "ui.sanksjon.liste.gjelder_fra", fra, null),
    felt("sk-l-sum", "ui.sanksjon.liste.sum", sum,
         "ui.sanksjon.liste.sum_hjelp"),
    felt("sk-l-antall", "ui.sanksjon.liste.antall", antall, null),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.sanksjon.skjema.liste_ok",
    tilbakestill: () => {
      kilde.value = ""; versjon.value = ""; fra.value = "";
      sum.value = ""; antall.value = "";
    },
    send: (idem) => registrerSanksjonsliste({
      kilde: kilde.value,
      listeversjon: versjon.value.trim(),
      gjelder_fra: fra.value,
      innhold_sha256: sum.value.trim().toLowerCase(),
      antall_oppforinger: Number(antall.value),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.sanksjon.liste.tittel_ny") }),
    el("p", { class: "muted", text: t("ui.sanksjon.liste.hvorfor") }),
    skjema, utfall);
}


function subjektSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const ref = el("input", { id: "sk-s-ref", name: "ref", type: "text",
    required: true, maxlength: "100" });
  const navn = el("input", { id: "sk-s-navn", name: "navn",
    type: "text", required: true, maxlength: "300" });
  const type = el("select", { id: "sk-s-type", name: "type",
                              required: true });
  type.append(el("option", { value: "",
    text: t("ui.sanksjon.ny.velg_type") }));
  for (const s of SUBJEKTTYPER) {
    type.append(el("option", { value: s, text: t(TYPETEKST[s]) }));
  }
  const land = el("input", { id: "sk-s-land", name: "land",
    type: "text", maxlength: "2", pattern: "[A-Za-z]{2}" });
  const fodt = el("input", { id: "sk-s-fodt", name: "fodt",
                             type: "date" });
  const ident = el("input", { id: "sk-s-ident", name: "ident",
    type: "text", maxlength: "100" });
  const knapp = el("button", { type: "submit",
    text: t("ui.sanksjon.knapp.registrer_subjekt") });
  skjema.append(
    felt("sk-s-ref", "ui.sanksjon.ny.ref", ref, null),
    felt("sk-s-navn", "ui.sanksjon.ny.navn", navn,
         "ui.sanksjon.ny.navn_hjelp"),
    felt("sk-s-type", "ui.sanksjon.ny.type", type, null),
    felt("sk-s-land", "ui.sanksjon.ny.land", land, null),
    felt("sk-s-fodt", "ui.sanksjon.ny.fodt", fodt,
         "ui.sanksjon.ny.fodt_hjelp"),
    // IDENTIFIKATOREN ER VALGFRI, OG HJELPETEKSTEN SIER HVA DET
    // KOSTER: uten den kan et treff aldri bli mer enn en navnelikhet.
    felt("sk-s-ident", "ui.sanksjon.ny.ident", ident,
         "ui.sanksjon.ny.ident_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.sanksjon.skjema.subjekt_ok",
    tilbakestill: () => {
      ref.value = ""; navn.value = ""; type.value = "";
      land.value = ""; fodt.value = ""; ident.value = "";
    },
    send: (idem) => registrerSanksjonssubjekt({
      ekstern_ref: ref.value.trim(),
      navn_oppgitt: navn.value.trim(),
      subjekttype: type.value,
      land: land.value.trim() ? land.value.trim().toUpperCase() : null,
      fodselsdato: fodt.value || null,
      identifikator: ident.value.trim() || null,
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.sanksjon.ny.tittel") }), skjema, utfall);
}


// DETALJPANELET. Kontrollene og treffene står side om side, fordi de
// svarer på hvert sitt spørsmål: «hva har vi sjekket, mot hvilken
// liste» og «hva kom ut av det».
function detaljpanel(ctx, last, kvitter, settApen) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  let apentTreff = null;
  const apne = async (subjekt) => {
    settApen(subjekt.subjekt_id);
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.sanksjon.detalj.tittel") }),
         el("p", { class: "muted", text: t("ui.sanksjon.laster") }));
    let kontroller = { kontroller: [] };
    let treff = { treff: [] };
    try {
      const id = encodeURIComponent(subjekt.subjekt_id);
      kontroller = await hentJson(`/v1/sanksjon/${id}/kontroller`);
      treff = await hentJson(`/v1/sanksjon/${id}/treff`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.sanksjon.detalj.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.sanksjon.feil.generell") }));
      return;
    }
    const kTbody = el("tbody");
    for (const k of kontroller.kontroller || []) {
      kTbody.append(el("tr", {},
        el("th", { scope: "row",
                   text: k.kontrollert.slice(0, 10) }),
        el("td", { text: `${t(KILDETEKST[k.kilde] || k.kilde)}`
                         + ` ${k.listeversjon}` }),
        el("td", { class: "tall", text: String(k.matchterskel) }),
        el("td", { text: (k.sammenlignede_felt || []).join(", ") }),
        el("td", { text: k.utfall === "ingen_treff"
                     ? t("ui.sanksjon.ingen_treff")
                     : t("ui.sanksjon.antall_treff")
                         .replace("{n}", String(k.antall_treff)) }),
        el("td", { text: k.kontrollert_av })));
    }
    const skriver = harScope(ctx, "bestilling:opprett");
    const apneAvklaring = skriver
      ? (tr) => { apentTreff = tr; apne(subjekt); }
      : null;
    const deler = [
      el("h2", { text: t("ui.sanksjon.detalj.tittel") }),
      el("p", { class: "muted",
                text: `${subjekt.navn_oppgitt} · `
                      + subjekt.ekstern_ref }),
      el("div", { class: "tablewrap" }, el("table", {},
        el("caption", { text: t("ui.sanksjon.kontroller.tittel") }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col",
                     text: t("ui.sanksjon.kol.kontrollert") }),
          el("th", { scope: "col", text: t("ui.sanksjon.kol.liste") }),
          el("th", { scope: "col",
                     text: t("ui.sanksjon.kol.terskel") }),
          el("th", { scope: "col",
                     text: t("ui.sanksjon.kol.sammenlignet") }),
          el("th", { scope: "col", text: t("ui.sanksjon.kol.utfall") }),
          el("th", { scope: "col",
                     text: t("ui.sanksjon.kol.kontrollert_av") }))),
        kTbody)),
      treffTabell(treff.treff || [], apneAvklaring),
    ];
    if (apentTreff) {
      const fortsatt = (treff.treff || []).find(
        (x) => x.treff_id === apentTreff.treff_id && !x.konklusjon);
      if (fortsatt) {
        deler.push(avklaringSkjema(ctx, last, fortsatt, kvitter,
                                   () => { apentTreff = null; }));
      } else {
        apentTreff = null;
      }
    }
    if (skriver) {
      const knapp = el("button", { type: "button",
        text: subjekt.aktiv ? t("ui.sanksjon.knapp.deaktiver")
                            : t("ui.sanksjon.knapp.aktiver") });
      knapp.addEventListener("click", async () => {
        knapp.disabled = true;
        try {
          await settSanksjonssubjektAktiv(subjekt.subjekt_id,
                                          !subjekt.aktiv);
        } catch (e) {
          knapp.disabled = false;
          if (e instanceof UautorisertFeil) {
            ctx.paaUautorisert(); return;
          }
          // EN FEILET HANDLING MÅ SIES HØYT (CodeRabbit, 117).
          // Uten dette re-aktiveres knappen og INGENTING skjer på
          // skjermen — brukeren har trykket «deaktiver», fått ingen
          // beskjed, og tror den gikk gjennom. Et stille avslag er
          // verre enn et synlig.
          const m = e && e.status === 409
            ? t("ui.sanksjon.feil.tilstand")
            : t("ui.sanksjon.feil.generell");
          kvitter(m);
          meldLive(m);
          return;
        }
        kvitter(t("ui.sanksjon.skjema.aktiv_ok"));
        meldLive(t("ui.sanksjon.skjema.aktiv_ok"));
        await last();
      });
      deler.push(el("div", { class: "skjema-bunn" }, knapp));
    }
    sett(node, ...deler);
  };
  return { node, apne };
}


// FUNNSEKSJONEN. Lastes for seg fordi funnene er KRYSS-SUBJEKT.
//
// LUKKING KREVER ET NOTAT, og `bekreftet_treff` kan ikke lukkes i det
// hele tatt — døra nekter det. En knapp som gjorde den observasjonen
// borte ville vært farligere enn manglende blokkering, fordi den ser
// ut som saksbehandling.
function funnseksjon(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.sanksjon.funn.tittel") }),
    el("p", { class: "muted", text: t("ui.sanksjon.laster") }));
  (async () => {
    let d;
    try {
      d = await hentJson("/v1/sanksjon/funn");
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.sanksjon.funn.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.sanksjon.feil.generell") }));
      return;
    }
    const funn = d.funn || [];
    const deler = [el("h2", { text: t("ui.sanksjon.funn.tittel") })];
    if (!funn.length) {
      deler.push(el("p", { class: "muted",
        text: t("ui.sanksjon.funn.ingen") }));
    } else {
      const tbody = el("tbody");
      for (const f of funn) {
        tbody.append(el("tr", {},
          el("th", { scope: "row", text: f.navn_oppgitt }),
          el("td", { text: t(MERKE[f.funntype] || f.funntype) }),
          el("td", { text: matchTekst(f.siste_matchtype) }),
          el("td", { class: "tall",
                     text: f.over_grense === null
                       || f.over_grense === undefined
                       ? "–" : String(f.over_grense) }),
          el("td", { text: f.forst_sett.slice(0, 10) })));
      }
      deler.push(el("div", { class: "tablewrap" }, el("table", {},
        el("caption", { text: t("ui.sanksjon.funn.tittel") }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col", text: t("ui.sanksjon.kol.navn") }),
          el("th", { scope: "col", text: t("ui.sanksjon.kol.funntype") }),
          el("th", { scope: "col", text: t("ui.sanksjon.kol.groveste") }),
          el("th", { scope: "col", text: t("ui.sanksjon.kol.over") }),
          el("th", { scope: "col",
                     text: t("ui.sanksjon.kol.forst_sett") }))),
        tbody)));
      if (harScope(ctx, "bestilling:opprett")) {
        // BARE FUNN SOM FAKTISK KAN LUKKES TILBYS. `bekreftet_treff`
        // er ikke med i lista, fordi døra ville nektet det uansett —
        // og en knapp som alltid feiler er verre enn ingen knapp.
        const lukkbare = funn.filter(
          (f) => f.funntype !== "bekreftet_treff");
        if (lukkbare.length) {
          deler.push(lukkSkjema(ctx, last, lukkbare, kvitter));
        }
      }
    }
    sett(node, ...deler);
  })();
  return node;
}


function lukkSkjema(ctx, last, funn, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const valg = el("select", { id: "sk-f-valg", name: "funn",
                              required: true });
  valg.append(el("option", { value: "",
    text: t("ui.sanksjon.funn.velg") }));
  for (const f of funn) {
    valg.append(el("option", {
      value: `${f.subjekt_id}\u001f${f.funntype}`,
      text: `${f.navn_oppgitt} — ${t(MERKE[f.funntype]
                                     || f.funntype)}`,
    }));
  }
  const notat = el("input", { id: "sk-f-notat", name: "notat",
    type: "text", required: true, minlength: "4", maxlength: "2000" });
  const knapp = el("button", { type: "submit",
    text: t("ui.sanksjon.knapp.lukk_funn") });
  skjema.append(
    felt("sk-f-valg", "ui.sanksjon.funn.hvilket", valg, null),
    felt("sk-f-notat", "ui.sanksjon.funn.notat", notat,
         "ui.sanksjon.funn.notat_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.sanksjon.skjema.funn_lukket",
    tilbakestill: () => { valg.value = ""; notat.value = ""; },
    send: (idem) => {
      const [id, type] = valg.value.split("\u001f");
      return lukkSanksjonsfunn(id, type, notat.value.trim(), idem);
    },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.sanksjon.funn.lukk_tittel") }), skjema,
    utfall);
}


export function visSanksjon(hoved, ctx) {
  const hode = () => flateHode(t("ui.sanksjon.tittel"),
    t("ui.sanksjon.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenRad = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const settApen = (id) => { apenRad = id; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/sanksjon"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const subjekter = d.subjekter || [];
      const detalj = detaljpanel(ctx, last, kvitter, settApen);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.sanksjon.oversikt.tittel") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT PÅ SKJERMEN. En bruker
        // som tror handelen blir stanset er farligere stilt enn en
        // som vet at den ikke blir det.
        el("p", { class: "muted",
                  text: t("ui.sanksjon.oversikt.hvorfor") }));

      const liste = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.sanksjon.liste.tittel") }));
      if (!subjekter.length) {
        liste.append(el("p", { class: "muted",
          text: t("ui.sanksjon.liste.ingen") }));
      } else {
        liste.append(subjektTabell(subjekter, detalj.apne));
      }

      const listeseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.sanksjon.lister.tittel") }));
      if (!(d.lister || []).length) {
        listeseksjon.append(el("p", { class: "muted",
          text: t("ui.sanksjon.lister.ingen") }));
      } else {
        listeseksjon.append(listeTabell(d.lister));
      }

      const kravseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.sanksjon.krav.tittel") }));
      if (!d.krav) {
        kravseksjon.append(el("p", { class: "muted",
          text: t("ui.sanksjon.ingen_krav") }));
      } else {
        kravseksjon.append(el("p", { class: "muted",
          text: t("ui.sanksjon.krav.versjon")
            .replace("{versjon}", String(d.krav.versjon)) }));
      }

      const deler = [oversikt, liste, listeseksjon, kravseksjon,
                     detalj.node, funnseksjon(ctx, last, kvitter)];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(subjektSkjema(ctx, last, kvitter),
                   listeSkjema(ctx, last, kvitter),
                   kravSkjema(ctx, last, d.krav, kvitter));
      }
      sett(kropp, ...deler);
      if (apenRad) {
        const rad = subjekter.find((x) => x.subjekt_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
