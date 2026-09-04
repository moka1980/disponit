// Toll- og HS-kodeagenten (M-52 v1) — FORSLAGET, IKKE DEKLARASJONEN.
//
// FLATENS VIKTIGSTE JOBB er å vise HVA et forslag hviler på, og under
// hvilket regelverk det ble avgitt.
//
// En HS-kode er en RETTSLIG PÅSTAND om hva en vare er. Feil kode gir
// bot, ikke bare forsinkelse — og boten treffer KUNDEN.
//
// DERFOR VISES EN KODE ALDRI ALENE. Hvert sted en kode står, står
// nomenklaturversjonen den hviler på, om det regelverket fortsatt
// gjelder, og HVOR MANGE GRUNNER forslaget har. En kode uten de tre er
// nettopp det som produserer falsk trygghet: den ser like ferdig ut
// som en noen har tenkt på.
//
// ET TOMT FELT SPØR; EN KODE UTEN GRUNNLAG SVARER.
//
// OG GRUNNENE VISES I RETTSKILDENES REKKEFØLGE: en bindende
// forhåndsuttalelse veier tyngre enn en egen tidligere klassifisering,
// som veier tyngre enn en tekstlikhet. Den som leser skal se det
// tyngste først.
//
// DET FINNES INGEN «DEKLARER»-KNAPP. 122 har ingen «deklarert»-kolonne
// å skrive til. «Klar til deklarering» er en tilstand HOS OSS, og
// hjelpeteksten sier det — ellers kunne ordet leses som «sendt».
//
// TERSKELEN STÅR IKKE HER. Det finnes ingen tallkonstant i denne fila
// for hvor sikker en klassifisering må være: den kommer fra
// `tollkrav` gjennom API-et, og mangler den, sier flaten det og tilbyr
// ikke å klassifisere.
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, avgiTollforslag, hentJson, lukkTollfunn,
  merkForslagKlart, nyIdempotensnokkel, registrerNomenklatur,
  registrerTollvare, registrerVarenummer, settTollGyldigTil,
  settTollkrav,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// LUKKEDE SETT, speilet fra 122.
export const SYSTEMER = ["hs", "kn", "tolltariff"];
export const GRUNNARTER = ["bindende_forhandsuttalelse",
                           "tidligere_klassifisering",
                           "nomenklaturtekst",
                           "alminnelig_fortolkningsregel",
                           "faglig_vurdering"];

const SYSTEMTEKST = {
  hs: "ui.tollkode.system_hs",
  kn: "ui.tollkode.system_kn",
  tolltariff: "ui.tollkode.system_tolltariff",
};

const GRUNNTEKST = {
  bindende_forhandsuttalelse: "ui.tollkode.grunn_bku",
  tidligere_klassifisering: "ui.tollkode.grunn_tidligere",
  nomenklaturtekst: "ui.tollkode.grunn_tekst",
  alminnelig_fortolkningsregel: "ui.tollkode.grunn_afr",
  faglig_vurdering: "ui.tollkode.grunn_faglig",
};

const FUNNTEKST = {
  nomenklatur_utlopt: "ui.tollkode.funn_utlopt",
  nomenklatur_utloper_snart: "ui.tollkode.funn_utloper",
  forslag_mot_utlopt_nomenklatur: "ui.tollkode.funn_kode_utlopt",
  vare_uten_forslag: "ui.tollkode.funn_uklassifisert",
  forslag_under_terskel: "ui.tollkode.funn_under_terskel",
  forslag_ikke_klart: "ui.tollkode.funn_ikke_klart",
  ingen_krav: "ui.tollkode.funn_uten_krav",
};

// RETTSKILDENES REKKEFØLGE, speilet fra `m52_grunnene` i 122. Den
// står her fordi flaten SORTERER lister den selv bygger — men basen
// er kilden, og porten måler at de er like.
export const GRUNNVEKT = ["bindende_forhandsuttalelse",
                          "tidligere_klassifisering",
                          "alminnelig_fortolkningsregel",
                          "nomenklaturtekst", "faglig_vurdering"];


// NOMENKLATUREN, MED GYLDIGHETEN SIN — ALDRI VERSJONEN ALENE.
//
// MUTASJONEN SOM DREPER PORTEN: returner bare «HS 2022». En versjon
// uten om regelverket fortsatt gjelder er nettopp den opplysningen som
// gjør en avviklet kode umulig å skille fra en gyldig.
export function nomenklaturTekst(rad) {
  if (!rad || !rad.system) return t("ui.tollkode.uten_nomenklatur");
  const navn = `${t(SYSTEMTEKST[rad.system] || rad.system)}`
    + ` ${rad.versjon}`;
  if (rad.gyldig_naa === null || rad.gyldig_naa === undefined) {
    return navn;
  }
  return t(rad.gyldig_naa ? "ui.tollkode.nomenklatur_gyldig"
                          : "ui.tollkode.nomenklatur_utlopt")
    .replace("{navn}", navn);
}


// AVVIKLINGEN, MED RETNING (118s form).
export function utlopTekst(dogn) {
  if (dogn === null || dogn === undefined) {
    return t("ui.tollkode.uten_sluttdato");
  }
  if (dogn < 0) {
    return t("ui.tollkode.utlop_passert").replace("{n}", String(-dogn));
  }
  if (dogn === 0) return t("ui.tollkode.utlop_i_dag");
  return t("ui.tollkode.utlop_om").replace("{n}", String(dogn));
}


// TOLLSATSEN, REGNET I HELTALL FRA BASISPUNKTER.
//
// NULL BETYR «IKKE REGISTRERT», IKKE «NULL TOLL» — og skillet er hele
// forskjellen mellom en vare som er tollfri og en vi ikke vet satsen
// på. Bare den ene av dem er trygg å deklarere.
export function satsTekst(bp) {
  if (bp === null || bp === undefined) {
    return t("ui.tollkode.sats_ukjent");
  }
  const hele = Math.trunc(bp / 100);
  const rest = String(bp % 100).padStart(2, "0");
  return t("ui.tollkode.sats").replace("{n}", `${hele},${rest}`);
}


// FORSLAGET, ALDRI KODEN ALENE.
//
// Koden, sikkerheten, terskelen den ble målt mot og ANTALL GRUNNER.
// En kode uten de tre siste ser like ferdig ut som en noen har tenkt
// på — og det er nettopp den falske tryggheten modulen finnes for å
// unngå.
export function forslagTekst(rad) {
  if (!rad || !rad.forslag_id) return t("ui.tollkode.uklassifisert");
  // ÉN GRUNN ER IKKE «1 grunner» (CodeRabbit). Huset har en egen
  // entallsform, og et forslag som hviler på ÉN grunn er nettopp det
  // tilfellet en leser skal kjenne igjen — det er det tynneste
  // grunnlaget døra slipper gjennom.
  const en = rad.antall_grunner === 1;
  return t(rad.over_terskel
    ? (en ? "ui.tollkode.forslag_over_en" : "ui.tollkode.forslag_over")
    : (en ? "ui.tollkode.forslag_under_en"
          : "ui.tollkode.forslag_under"))
    .replace("{kode}", rad.kode)
    .replace("{sikkerhet}", String(rad.sikkerhet))
    .replace("{terskel}", String(rad.terskel_brukt))
    .replace("{grunner}", String(rad.antall_grunner));
}


// VARENS TILSTAND, MED NAVN PÅ DET SOM MANGLER.
export function tilstandTekst(v) {
  if (!v.forslag_id) return t("ui.tollkode.uklassifisert");
  if (v.nomenklatur_gyldig_naa === false) {
    return t("ui.tollkode.kode_under_utlopt");
  }
  if (!v.over_terskel) return t("ui.tollkode.under_terskel");
  if (v.klar_til_deklarering) return t("ui.tollkode.klar");
  return t("ui.tollkode.ikke_klar");
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
          ? t("ui.tollkode.feil.tilstand")
          : t("ui.tollkode.feil.generell") }));
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


// SAMMENDRAGET. `forslag_under_utlopt` står FØRST og i fet skrift: det
// er det ene tallet klyngen finnes for — koder som hviler på et
// regelverk som siden er avviklet.
export function sammendrag(s) {
  const p = el("p");
  p.append(el("strong", {
    text: t("ui.tollkode.under_utlopt_sum")
      .replace("{n}", String(s.forslag_under_utlopt ?? 0)) }));
  p.append(" ", el("span", {
    text: t("ui.tollkode.tellinger")
      .replace("{varer}", String(s.varer ?? 0))
      .replace("{klassifiserte}", String(s.klassifiserte ?? 0))
      .replace("{klare}", String(s.klare ?? 0)) }));
  if (s.uklassifiserte > 0) {
    // EN VARE INGEN HAR KLASSIFISERT FORTOLLES PÅ GJETNING den dagen
    // den skal ut.
    p.append(" ", el("strong", {
      text: t("ui.tollkode.uklassifiserte_sum")
        .replace("{n}", String(s.uklassifiserte)) }));
  }
  if (s.utlopte > 0) {
    p.append(" ", el("span", { class: "muted",
      text: t("ui.tollkode.utlopte_nomenklaturer")
        .replace("{n}", String(s.utlopte)) }));
  }
  if (!s.gyldige) {
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.tollkode.ingen_gyldig_nomenklatur") }));
  }
  if (s.apne_funn > 0) {
    p.append(" ", el("strong", {
      text: t("ui.tollkode.apne_funn")
        .replace("{n}", String(s.apne_funn)) }));
  }
  if (!s.har_krav) {
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.tollkode.ingen_terskel_varsel") }));
  } else {
    p.append(" ", el("span", { class: "muted",
      text: t("ui.tollkode.terskelen_er")
        .replace("{n}", String(s.terskel)) }));
  }
  if (s.vist < s.varer) {
    p.append(" ", el("strong", {
      text: t("ui.tollkode.avkortet").replace("{vist}", String(s.vist)) }));
  }
  return p;
}


export function nomenklaturtabell(nomenklaturer, aapne) {
  const tbody = el("tbody");
  for (const n of nomenklaturer) {
    const knapp = el("button", { type: "button",
      text: t("ui.tollkode.knapp.apne_varenummer") });
    knapp.addEventListener("click", () => aapne(n));
    tbody.append(el("tr", {},
      el("th", { scope: "row",
                 text: t(SYSTEMTEKST[n.system] || n.system) }),
      el("td", { text: n.versjon }),
      el("td", { text: n.gyldig_fra }),
      el("td", { text: n.gyldig_til || t("ui.tollkode.uten_sluttdato") }),
      el("td", { text: n.gyldig_naa ? t("ui.tollkode.ja")
                                    : t("ui.tollkode.nei") }),
      el("td", { text: utlopTekst(n.dogn_til_utlop) }),
      el("td", { title: n.innhold_sha256,
                 text: `${(n.innhold_sha256 || "").slice(0, 12)}…` }),
      el("td", { class: "tall", text: String(n.antall_varenummer) }),
      el("td", { class: "tall", text: String(n.antall_forslag) }),
      el("td", {}, knapp)));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.tollkode.nomenklatur.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.tollkode.kol.system") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.versjon") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.fra") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.til") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.gyldig_naa") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.utlop") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.sum") }),
        el("th", { scope: "col",
                   text: t("ui.tollkode.kol.varenummer") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.forslag") }),
        el("th", { scope: "col",
                   text: t("ui.tollkode.kol.handling") }))),
      tbody));
}


// VARENUMMERTABELLEN. POSISJONSTEKSTEN STÅR, ikke bare koden: det er
// teksten en klassifisering argumenteres mot — koden er en adresse.
export function varenummertabell(varenummer) {
  const tbody = el("tbody");
  for (const v of varenummer) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: v.kode }),
      el("td", { text: v.tekst }),
      el("td", { text: satsTekst(v.tollsats_bp) }),
      el("td", { class: "tall", text: String(v.brukt_i_forslag) })));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.tollkode.varenummer.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.tollkode.kol.kode") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.posisjon") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.sats") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.brukt") }))),
      tbody));
}


// VARETABELLEN. KODEN STÅR ALDRI ALENE — nomenklaturversjonen, om den
// gjelder, sikkerheten og antall grunner står ved siden av.
export function varetabell(varer, aapne) {
  const tbody = el("tbody");
  for (const v of varer) {
    const knapp = el("button", { type: "button",
      text: t("ui.tollkode.knapp.apne") });
    knapp.addEventListener("click", () => aapne(v));
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: v.ekstern_ref }),
      el("td", { text: v.beskrivelse }),
      // MATERIALE OG BRUK ER DET NOMENKLATUREN KLASSIFISERER PÅ.
      el("td", { text: v.materiale || "–" }),
      el("td", { text: v.bruk || "–" }),
      el("td", { text: v.opprinnelsesland || "–" }),
      el("td", { text: forslagTekst(v) }),
      el("td", { text: nomenklaturTekst({
        system: v.system, versjon: v.versjon,
        gyldig_naa: v.nomenklatur_gyldig_naa }) }),
      el("td", { text: satsTekst(v.tollsats_bp) }),
      el("td", { text: tilstandTekst(v) }),
      el("td", {}, knapp)));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.tollkode.varer.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.tollkode.kol.ref") }),
        el("th", { scope: "col",
                   text: t("ui.tollkode.kol.beskrivelse") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.materiale") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.bruk") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.land") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.forslag") }),
        el("th", { scope: "col",
                   text: t("ui.tollkode.kol.nomenklatur") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.sats") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.tilstand") }),
        el("th", { scope: "col",
                   text: t("ui.tollkode.kol.handling") }))),
      tbody));
}


// GRUNNTABELLEN. REKKEFØLGEN ER RETTSKILDENES, og den kommer fra
// basen: `m52_grunnene` sorterer, og flaten viser rekkefølgen den fikk.
export function grunntabell(grunner) {
  const tbody = el("tbody");
  for (const g of grunner) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: t(GRUNNTEKST[g.art] || g.art) }),
      el("td", { text: g.henvisning }),
      el("td", { text: g.utdrag }),
      el("td", { text: g.grunn_dato || "–" }),
      el("td", { text: g.registrert_av })));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.tollkode.grunner.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.tollkode.kol.grunnart") }),
        el("th", { scope: "col",
                   text: t("ui.tollkode.kol.henvisning") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.utdrag") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.grunndato") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.av") }))),
      tbody));
}


// FORSLAGSREKKEN. HELE rekken: en ny nomenklaturversjon gir en ny rad,
// og det er der «hva var riktig kode den gangen» står.
export function forslagstabell(forslag) {
  const tbody = el("tbody");
  for (const f of forslag) {
    tbody.append(el("tr", {},
      el("th", { scope: "row",
                 text: f.avgitt.slice(0, 16).replace("T", " ") }),
      el("td", { text: f.kode }),
      el("td", { text: nomenklaturTekst({
        system: f.system, versjon: f.versjon,
        gyldig_naa: f.nomenklatur_gyldig_naa }) }),
      el("td", { text: t("ui.tollkode.prosent_mot")
                   .replace("{sikkerhet}", String(f.sikkerhet))
                   .replace("{terskel}",
                            String(f.terskel_brukt)) }),
      el("td", { class: "tall", text: String(f.antall_grunner) }),
      el("td", { text: f.klar_til_deklarering ? t("ui.tollkode.ja")
                                              : t("ui.tollkode.nei") }),
      el("td", { text: f.avgitt_av })));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.tollkode.forslag.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.tollkode.kol.avgitt") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.kode") }),
        el("th", { scope: "col",
                   text: t("ui.tollkode.kol.nomenklatur") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.sikkerhet") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.grunner") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.klar") }),
        el("th", { scope: "col", text: t("ui.tollkode.kol.av") }))),
      tbody));
}


function kravskjema(ctx, last, krav, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const terskel = el("input", { id: "tk-t-terskel", name: "terskel",
    type: "number", required: true, min: "1", max: "100", step: "1" });
  const utlop = el("input", { id: "tk-t-utlop", name: "utlop",
    type: "number", required: true, min: "1", max: "730", step: "1" });
  const frist = el("input", { id: "tk-t-frist", name: "frist",
    type: "number", required: true, min: "1", max: "365", step: "1" });
  // VERDIENE KOMMER FRA BASEN, ikke fra en konstant her. Er de ikke
  // satt, står feltene TOMME — et forhåndsutfylt tall ville vært
  // nøyaktig den hardkodede terskelen invarianten forbyr.
  terskel.value = krav ? String(krav.sikkerhetsterskel) : "";
  utlop.value = krav ? String(krav.utlopsvarsel_dogn) : "";
  frist.value = krav ? String(krav.forslagsfrist_dogn) : "";
  const knapp = el("button", { type: "submit",
    text: t("ui.tollkode.knapp.lagre_krav") });
  skjema.append(
    felt("tk-t-terskel", "ui.tollkode.krav.terskel", terskel,
         "ui.tollkode.krav.terskel_hjelp"),
    felt("tk-t-utlop", "ui.tollkode.krav.utlop", utlop,
         "ui.tollkode.krav.utlop_hjelp"),
    felt("tk-t-frist", "ui.tollkode.krav.frist", frist,
         "ui.tollkode.krav.frist_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.tollkode.skjema.krav_ok",
    send: (idem) => settTollkrav({
      sikkerhetsterskel: Math.trunc(Number(terskel.value)),
      utlopsvarsel_dogn: Math.trunc(Number(utlop.value)),
      forslagsfrist_dogn: Math.trunc(Number(frist.value)),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.tollkode.krav.tittel") }),
    el("p", { class: "muted", text: t("ui.tollkode.krav.hvorfor") }),
    skjema, utfall);
}


function nomenklaturskjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const system = el("select", { id: "tk-n-system", name: "system",
    required: true });
  system.append(el("option", { value: "",
    text: t("ui.tollkode.nomenklatur.velg_system") }));
  for (const s of SYSTEMER) {
    system.append(el("option", { value: s,
      text: t(SYSTEMTEKST[s] || s) }));
  }
  const versjon = el("input", { id: "tk-n-versjon", name: "versjon",
    type: "text", required: true, maxlength: "200" });
  const fra = el("input", { id: "tk-n-fra", name: "fra",
    type: "date", required: true });
  const til = el("input", { id: "tk-n-til", name: "til",
    type: "date" });
  const sum = el("input", { id: "tk-n-sum", name: "sum",
    type: "text", required: true, minlength: "64", maxlength: "64",
    pattern: "[0-9a-fA-F]{64}" });
  const url = el("input", { id: "tk-n-url", name: "url",
    type: "url", maxlength: "2000" });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.tollkode.knapp.lagre_nomenklatur") });
  const vurder = () => {
    knapp.disabled = !system.value || !versjon.value.trim()
      || !fra.value || !/^[0-9a-fA-F]{64}$/.test(sum.value.trim());
  };
  for (const k of [system, versjon, fra, til, sum, url]) {
    k.addEventListener("change", vurder);
    k.addEventListener("input", vurder);
  }
  skjema.append(
    felt("tk-n-system", "ui.tollkode.nomenklatur.system", system, null),
    felt("tk-n-versjon", "ui.tollkode.nomenklatur.versjon", versjon,
         "ui.tollkode.nomenklatur.versjon_hjelp"),
    felt("tk-n-fra", "ui.tollkode.nomenklatur.fra", fra, null),
    felt("tk-n-til", "ui.tollkode.nomenklatur.til", til,
         "ui.tollkode.nomenklatur.til_hjelp"),
    felt("tk-n-sum", "ui.tollkode.nomenklatur.sum", sum,
         "ui.tollkode.nomenklatur.sum_hjelp"),
    felt("tk-n-url", "ui.tollkode.nomenklatur.url", url, null),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.tollkode.skjema.nomenklatur_ok",
    tilbakestill: () => {
      system.value = ""; versjon.value = ""; fra.value = "";
      til.value = ""; sum.value = ""; url.value = "";
      knapp.disabled = true;
    },
    send: (idem) => registrerNomenklatur({
      system: system.value,
      versjon: versjon.value.trim(),
      gyldig_fra: fra.value,
      // TOM SLUTTDATO ER `null` OG BETYR «GJELDER FORTSATT».
      gyldig_til: til.value || null,
      innhold_sha256: sum.value.trim().toLowerCase(),
      kilde_url: url.value.trim() || null,
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.tollkode.nomenklatur.skjema_tittel") }),
    el("p", { class: "muted",
              text: t("ui.tollkode.nomenklatur.hvorfor") }),
    skjema, utfall);
}


function sluttdatoskjema(ctx, last, nomenklatur, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const til = el("input", { id: "tk-g-til", name: "til",
    type: "date" });
  til.value = nomenklatur.gyldig_til || "";
  const knapp = el("button", { type: "submit",
    text: t("ui.tollkode.knapp.sett_sluttdato") });
  skjema.append(
    felt("tk-g-til", "ui.tollkode.sluttdato.felt", til,
         "ui.tollkode.sluttdato.hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.tollkode.skjema.sluttdato_ok",
    send: (idem) => settTollGyldigTil(nomenklatur.nomenklatur_id,
                                      til.value || null, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.tollkode.sluttdato.tittel") }),
    el("p", { class: "muted", text: t("ui.tollkode.sluttdato.hvorfor") }),
    skjema, utfall);
}


function varenummerskjema(ctx, last, nomenklaturId, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const kode = el("input", { id: "tk-v-kode", name: "kode",
    type: "text", required: true, maxlength: "20" });
  const tekst = el("input", { id: "tk-v-tekst", name: "tekst",
    type: "text", required: true, maxlength: "4000" });
  const sats = el("input", { id: "tk-v-sats", name: "sats",
    type: "number", min: "0", max: "1000000", step: "1" });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.tollkode.knapp.lagre_varenummer") });
  const vurder = () => {
    knapp.disabled = !/^[0-9]{4}[0-9.]*$/.test(kode.value.trim())
      || !tekst.value.trim();
  };
  for (const k of [kode, tekst, sats]) {
    k.addEventListener("change", vurder);
    k.addEventListener("input", vurder);
  }
  skjema.append(
    felt("tk-v-kode", "ui.tollkode.varenummer.kode", kode,
         "ui.tollkode.varenummer.kode_hjelp"),
    felt("tk-v-tekst", "ui.tollkode.varenummer.tekst", tekst,
         "ui.tollkode.varenummer.tekst_hjelp"),
    felt("tk-v-sats", "ui.tollkode.varenummer.sats", sats,
         "ui.tollkode.varenummer.sats_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.tollkode.skjema.varenummer_ok",
    tilbakestill: () => {
      kode.value = ""; tekst.value = ""; sats.value = "";
      knapp.disabled = true;
    },
    send: (idem) => registrerVarenummer({
      nomenklatur_id: nomenklaturId,
      kode: kode.value.trim(),
      tekst: tekst.value.trim(),
      // TOMT FELT ER `null` OG BETYR «IKKE REGISTRERT», ikke «null
      // toll» — skillet er hele forskjellen mellom en tollfri vare og
      // en vi ikke vet satsen på.
      tollsats_bp: sats.value === "" ? null
                                     : Math.trunc(Number(sats.value)),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.tollkode.varenummer.skjema_tittel") }),
    skjema, utfall);
}


function vareskjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const ref = el("input", { id: "tk-w-ref", name: "ref",
    type: "text", required: true, maxlength: "200" });
  const beskrivelse = el("input", { id: "tk-w-beskrivelse",
    name: "beskrivelse", type: "text", required: true,
    maxlength: "4000" });
  const materiale = el("input", { id: "tk-w-materiale",
    name: "materiale", type: "text", maxlength: "500" });
  const bruk = el("input", { id: "tk-w-bruk", name: "bruk",
    type: "text", maxlength: "500" });
  const land = el("input", { id: "tk-w-land", name: "land",
    type: "text", maxlength: "2", pattern: "[A-Za-z]{2}" });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.tollkode.knapp.lagre_vare") });
  const vurder = () => {
    const landOk = land.value === ""
      || /^[A-Za-z]{2}$/.test(land.value.trim());
    knapp.disabled = !ref.value.trim() || !beskrivelse.value.trim()
      || !landOk;
  };
  for (const k of [ref, beskrivelse, materiale, bruk, land]) {
    k.addEventListener("change", vurder);
    k.addEventListener("input", vurder);
  }
  skjema.append(
    felt("tk-w-ref", "ui.tollkode.vare.ref", ref, null),
    felt("tk-w-beskrivelse", "ui.tollkode.vare.beskrivelse",
         beskrivelse, null),
    felt("tk-w-materiale", "ui.tollkode.vare.materiale", materiale,
         "ui.tollkode.vare.materiale_hjelp"),
    felt("tk-w-bruk", "ui.tollkode.vare.bruk", bruk,
         "ui.tollkode.vare.bruk_hjelp"),
    felt("tk-w-land", "ui.tollkode.vare.land", land,
         "ui.tollkode.vare.land_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.tollkode.skjema.vare_ok",
    tilbakestill: () => {
      ref.value = ""; beskrivelse.value = ""; materiale.value = "";
      bruk.value = ""; land.value = ""; knapp.disabled = true;
    },
    send: (idem) => registrerTollvare({
      ekstern_ref: ref.value.trim(),
      beskrivelse: beskrivelse.value.trim(),
      materiale: materiale.value.trim() || null,
      bruk: bruk.value.trim() || null,
      opprinnelsesland: land.value.trim().toUpperCase() || null,
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.tollkode.vare.tittel") }),
    el("p", { class: "muted", text: t("ui.tollkode.vare.hvorfor") }),
    skjema, utfall);
}


// FORSLAGSSKJEMAET. MODULENS SKARPESTE FLATE.
//
// GRUNNENE LEGGES INN FØR FORSLAGET KAN SENDES, og knappen er død til
// minst én finnes. Det er ikke en validering flaten fant på: døra
// skriver forslaget og grunnene i SAMME setning, og et forslag uten
// grunnlag kan ikke oppstå.
//
// BARE GYLDIGE NOMENKLATURER TILBYS. Døra nekter mot en avviklet, og
// en knapp som alltid feiler er verre enn en valgmulighet som ikke
// finnes.
function forslagsskjema(ctx, last, vare, nomenklaturer, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const gyldige = nomenklaturer.filter(
    (n) => n.gyldig_naa === true && n.antall_varenummer > 0);
  const nomen = el("select", { id: "tk-f-nomenklatur",
    name: "nomenklatur", required: true });
  nomen.append(el("option", { value: "",
    text: t("ui.tollkode.forslag.velg_nomenklatur") }));
  for (const n of gyldige) {
    nomen.append(el("option", { value: n.nomenklatur_id,
      text: `${t(SYSTEMTEKST[n.system] || n.system)} ${n.versjon}`
            + ` (${n.antall_varenummer})` }));
  }
  const varenr = el("select", { id: "tk-f-varenummer",
    name: "varenummer", required: true, disabled: true });
  const varenrutfall = el("p", { "aria-live": "polite" });
  varenr.append(el("option", { value: "",
    text: t("ui.tollkode.forslag.velg_varenummer") }));
  const sikkerhet = el("input", { id: "tk-f-sikkerhet",
    name: "sikkerhet", type: "number", required: true, min: "0",
    max: "100", step: "1" });
  const art = el("select", { id: "tk-f-art", name: "art" });
  art.append(el("option", { value: "",
    text: t("ui.tollkode.forslag.velg_art") }));
  for (const a of GRUNNARTER) {
    art.append(el("option", { value: a,
      text: t(GRUNNTEKST[a] || a) }));
  }
  const henvisning = el("input", { id: "tk-f-henvisning",
    name: "henvisning", type: "text", maxlength: "500" });
  const utdrag = el("input", { id: "tk-f-utdrag", name: "utdrag",
    type: "text", minlength: "4", maxlength: "4000" });
  const grunndato = el("input", { id: "tk-f-grunndato",
    name: "grunndato", type: "date" });
  const leggTil = el("button", { type: "button", disabled: true,
    text: t("ui.tollkode.knapp.legg_til_grunn") });
  const grunnliste = el("ul", { class: "kv-liste" });
  // DET TOMME GRUNNLAGET STÅR UTENFOR LISTA, ikke som en `li` med
  // `role="alert"`: en rolle på en `li` OVERSTYRER listitem-rollen,
  // og da er `ul`-en en liste med et element som ikke er et
  // listeelement (axe «list», alvorlig). Beskjeden er dessuten ikke
  // en tom rad i grunnlaget — den er grunnen til at knappen er død.
  const grunntomt = el("p", { role: "alert",
    text: t("ui.tollkode.forslag.ingen_grunner") });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.tollkode.knapp.avgi_forslag") });
  const grunner = [];

  const tegnGrunner = () => {
    sett(grunnliste, ...grunner.map((g) => el("li", {},
      el("strong", { text: t(GRUNNTEKST[g.art] || g.art) }),
      " ", el("span", { text: g.henvisning }),
      " — ", el("span", { text: g.utdrag }))));
    grunnliste.hidden = grunner.length === 0;
    grunntomt.hidden = grunner.length > 0;
  };
  const vurder = () => {
    leggTil.disabled = !art.value || !henvisning.value.trim()
      || utdrag.value.trim().length < 4;
    // FORSLAGET KAN IKKE SENDES UTEN MINST ÉN GRUNN.
    knapp.disabled = !nomen.value || !varenr.value
      || sikkerhet.value === "" || grunner.length === 0;
  };
  // GENERASJONSMERKET (CodeRabbit). Bytter man nomenklatur to ganger
  // raskt, kan det FØRSTE svaret komme SIST — og da ville posisjonene
  // til det forrige regelverket blitt lagt inn under det nye. Døra
  // utleder nomenklaturen FRA varenummeret, så forslaget hadde blitt
  // avgitt mot et regelverk brukeren ikke valgte, uten en feilmelding.
  // Det er nettopp «hvilken versjon ble dette avgjort mot» som er
  // modulens sak.
  let generasjon = 0;
  nomen.addEventListener("change", async () => {
    const min = ++generasjon;
    varenr.disabled = !nomen.value;
    sett(varenr, el("option", { value: "",
      text: t("ui.tollkode.forslag.velg_varenummer") }));
    sett(varenrutfall);
    vurder();
    if (!nomen.value) return;
    try {
      const id = encodeURIComponent(nomen.value);
      const d = await hentJson(
        `/v1/toll/nomenklatur/${id}/varenummer`);
      if (min !== generasjon) return;
      for (const v of d.varenummer || []) {
        varenr.append(el("option", { value: v.varenummer_id,
          text: `${v.kode} — ${v.tekst}` }));
      }
      // …OG AVKORTINGEN SIES. Den som ikke finner koden sin skal vite
      // at han ikke har sett alle, ikke tro at den ikke finnes.
      if (d.grense && d.vist >= d.grense) {
        sett(varenrutfall, el("span", { role: "alert",
          text: t("ui.tollkode.avkortet").replace("{vist}",
                                              String(d.vist)) }));
      }
    } catch (e) {
      if (min !== generasjon) return;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); }
    }
    vurder();
  });
  for (const k of [varenr, sikkerhet, art, henvisning, utdrag]) {
    k.addEventListener("change", vurder);
    k.addEventListener("input", vurder);
  }
  leggTil.addEventListener("click", () => {
    grunner.push({ art: art.value,
                   henvisning: henvisning.value.trim(),
                   utdrag: utdrag.value.trim(),
                   grunn_dato: grunndato.value || null });
    art.value = ""; henvisning.value = ""; utdrag.value = "";
    grunndato.value = "";
    tegnGrunner(); vurder();
  });
  tegnGrunner();

  const deler = [];
  if (!gyldige.length) {
    deler.push(el("p", { role: "alert",
      text: t("ui.tollkode.forslag.ingen_gyldige") }));
  } else {
    deler.push(
      felt("tk-f-nomenklatur", "ui.tollkode.forslag.nomenklatur", nomen,
           "ui.tollkode.forslag.nomenklatur_hjelp"),
      felt("tk-f-varenummer", "ui.tollkode.forslag.varenummer", varenr,
           null),
      varenrutfall,
      felt("tk-f-sikkerhet", "ui.tollkode.forslag.sikkerhet", sikkerhet,
           "ui.tollkode.forslag.sikkerhet_hjelp"),
      felt("tk-f-art", "ui.tollkode.forslag.art", art,
           "ui.tollkode.forslag.art_hjelp"),
      felt("tk-f-henvisning", "ui.tollkode.forslag.henvisning",
           henvisning, "ui.tollkode.forslag.henvisning_hjelp"),
      felt("tk-f-utdrag", "ui.tollkode.forslag.utdrag", utdrag, null),
      felt("tk-f-grunndato", "ui.tollkode.forslag.grunndato", grunndato,
           "ui.tollkode.forslag.grunndato_hjelp"),
      el("div", { class: "skjema-bunn" }, leggTil),
      grunntomt, grunnliste,
      el("div", { class: "skjema-bunn" }, knapp));
  }
  skjema.append(...deler);

  // EGEN SUBMIT, fordi svaret er et FORSLAG og ikke bare «ok».
  let idem = null;
  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("change", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled) return;
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    let svar;
    try {
      svar = await avgiTollforslag(vare.vare_id, {
        varenummer_id: varenr.value,
        sikkerhet: Math.trunc(Number(sikkerhet.value)),
        grunner,
      }, idem);
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.tollkode.feil.forslag_avvist")
          : t("ui.tollkode.feil.generell") }));
      return;
    }
    idem = null;
    knapp.disabled = false;
    // SVARET BÆRER KODEN, SIKKERHETEN, TERSKELEN OG ANTALL GRUNNER.
    const m = t(svar.antall_grunner === 1
      ? "ui.tollkode.skjema.forslag_ok_en"
      : "ui.tollkode.skjema.forslag_ok")
      .replace("{kode}", svar.kode)
      .replace("{sikkerhet}", String(svar.sikkerhet))
      .replace("{terskel}", String(svar.terskel_brukt))
      .replace("{grunner}", String(svar.antall_grunner));
    kvitter(m); meldLive(m);
    await last();
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.tollkode.forslag.skjema_tittel") }),
    el("p", { class: "muted", text: t("ui.tollkode.forslag.hvorfor") }),
    skjema, utfall);
}


// FUNNSEKSJONEN. `forslag_mot_utlopt_nomenklatur` KAN IKKE LUKKES.
function funnseksjon(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.tollkode.funn.tittel") }),
    el("p", { class: "muted", text: t("ui.tollkode.laster") }));
  (async () => {
    let d;
    try {
      d = await hentJson("/v1/toll/funn");
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.tollkode.funn.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.tollkode.feil.generell") }));
      return;
    }
    const funn = d.funn || [];
    const deler = [el("h2", { text: t("ui.tollkode.funn.tittel") })];
    if (!funn.length) {
      deler.push(el("p", { class: "muted",
        text: t("ui.tollkode.funn.ingen") }));
    } else {
      const tbody = el("tbody");
      for (const f of funn) {
        tbody.append(el("tr", {},
          el("th", { scope: "row",
                     text: t(FUNNTEKST[f.funntype] || f.funntype) }),
          el("td", { text: f.ekstern_ref
                       || (f.system
                           ? nomenklaturTekst({ system: f.system,
                               versjon: f.nomenklaturversjon })
                           : "–") }),
          // SIKKERHETEN STÅR PÅ FUNNET, med terskelen: «under
          // terskel» uten å si hvor mye er en beskjed man ikke kan
          // handle på (119s lærdom).
          el("td", { text: f.sikkerhet === null
                           || f.sikkerhet === undefined
                       ? "–"
                       : t("ui.tollkode.prosent_mot")
                           .replace("{sikkerhet}",
                                    String(f.sikkerhet))
                           .replace("{terskel}",
                                    String(f.terskel_brukt ?? "–")) }),
          el("td", { class: "tall",
                     text: f.over_grense === null
                           || f.over_grense === undefined
                       ? "–" : String(f.over_grense) }),
          el("td", { text: f.detalj || "–" })));
      }
      deler.push(el("div", { class: "tablewrap" }, el("table", {},
        el("caption", { text: t("ui.tollkode.funn.tittel") }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col", text: t("ui.tollkode.kol.funntype") }),
          el("th", { scope: "col", text: t("ui.tollkode.kol.gjelder") }),
          el("th", { scope: "col",
                     text: t("ui.tollkode.kol.sikkerhet") }),
          el("th", { scope: "col", text: t("ui.tollkode.kol.over") }),
          el("th", { scope: "col", text: t("ui.tollkode.kol.detalj") }))),
        tbody)));
      if (harScope(ctx, "bestilling:opprett")) {
        const lukkbare = funn.filter(
          (f) => f.funntype !== "forslag_mot_utlopt_nomenklatur");
        if (lukkbare.length) {
          deler.push(lukkskjema(ctx, last, lukkbare, kvitter));
        }
      }
    }
    sett(node, ...deler);
  })();
  return node;
}


function lukkskjema(ctx, last, funn, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const valg = el("select", { id: "tk-l-valg", name: "funn",
    required: true });
  valg.append(el("option", { value: "",
    text: t("ui.tollkode.funn.velg") }));
  for (const f of funn) {
    valg.append(el("option", { value: f.funn_id,
      text: `${t(FUNNTEKST[f.funntype] || f.funntype)}`
            + ` — ${f.ekstern_ref || f.detalj || ""}` }));
  }
  const notat = el("input", { id: "tk-l-notat", name: "notat",
    type: "text", required: true, minlength: "4", maxlength: "4000" });
  const knapp = el("button", { type: "submit",
    text: t("ui.tollkode.knapp.lukk_funn") });
  skjema.append(
    felt("tk-l-valg", "ui.tollkode.funn.hvilket", valg, null),
    felt("tk-l-notat", "ui.tollkode.funn.notat", notat,
         "ui.tollkode.funn.notat_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.tollkode.skjema.funn_lukket",
    tilbakestill: () => { valg.value = ""; notat.value = ""; },
    send: (idem) => lukkTollfunn(valg.value, notat.value.trim(), idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.tollkode.funn.lukk_tittel") }),
    el("p", { class: "muted", text: t("ui.tollkode.funn.lukk_hvorfor") }),
    skjema, utfall);
}


// NOMENKLATURPANELET. Varenumrene, og avviklingsdatoen som kan settes.
function nomenklaturpanel(ctx, last, kvitter, settApent) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  const aapne = async (nomenklatur) => {
    settApent(nomenklatur.nomenklatur_id);
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.tollkode.varenummer.tittel") }),
         el("p", { class: "muted", text: t("ui.tollkode.laster") }));
    let d = { varenummer: [] };
    try {
      const id = encodeURIComponent(nomenklatur.nomenklatur_id);
      d = await hentJson(`/v1/toll/nomenklatur/${id}/varenummer`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.tollkode.varenummer.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.tollkode.feil.generell") }));
      return;
    }
    const rader = d.varenummer || [];
    const skriver = harScope(ctx, "bestilling:opprett");
    const deler = [
      el("h2", { text: t("ui.tollkode.varenummer.tittel") }),
      el("p", { class: "muted",
                text: nomenklaturTekst(nomenklatur) }),
    ];
    if (!rader.length) {
      // EN NOMENKLATUR UTEN POSISJONER KAN INGENTING KLASSIFISERES
      // MOT, og et forslag mot den ville ikke kunne avgis i det hele
      // tatt — døra krever et varenummer.
      deler.push(el("p", { role: "alert",
        text: t("ui.tollkode.varenummer.ingen") }));
    } else {
      deler.push(varenummertabell(rader));
    }
    if (skriver) {
      deler.push(
        varenummerskjema(ctx, last, nomenklatur.nomenklatur_id,
                         kvitter),
        sluttdatoskjema(ctx, last, nomenklatur, kvitter));
    }
    sett(node, ...deler);
  };
  return { node, aapne };
}


// VAREPANELET. Forslagene, grunnene og de to handlingene som finnes:
// AVGI FORSLAG og MERK KLART. Ingen tredje.
function varepanel(ctx, last, kvitter, settApen, nomenklaturer) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  const aapne = async (vare) => {
    settApen(vare.vare_id);
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.tollkode.detalj.tittel") }),
         el("p", { class: "muted", text: t("ui.tollkode.laster") }));
    let forslag = { forslag: [] };
    let grunner = { grunner: [] };
    try {
      const id = encodeURIComponent(vare.vare_id);
      forslag = await hentJson(`/v1/toll/vare/${id}/forslag`);
      if (vare.forslag_id) {
        const fid = encodeURIComponent(vare.forslag_id);
        grunner = await hentJson(`/v1/toll/forslag/${fid}/grunner`);
      }
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.tollkode.detalj.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.tollkode.feil.generell") }));
      return;
    }
    const skriver = harScope(ctx, "bestilling:opprett");
    const rader = forslag.forslag || [];
    const grunnrader = grunner.grunner || [];
    const deler = [
      el("h2", { text: t("ui.tollkode.detalj.tittel") }),
      el("p", { class: "muted",
                text: `${vare.ekstern_ref} · ${vare.beskrivelse}` }),
    ];
    if (!rader.length) {
      deler.push(el("p", { role: "alert",
        text: t("ui.tollkode.uklassifisert_varsel") }));
    } else {
      // FORSLAGET MED SITT REGELVERK OG SINE GRUNNER, ØVERST.
      deler.push(el("p", {
        text: t("ui.tollkode.detalj.forslag")
          .replace("{forslag}", forslagTekst(vare))
          .replace("{nomenklatur}", nomenklaturTekst({
            system: vare.system, versjon: vare.versjon,
            gyldig_naa: vare.nomenklatur_gyldig_naa })) }));
      if (vare.nomenklatur_gyldig_naa === false) {
        deler.push(el("p", { role: "alert",
          text: t("ui.tollkode.kode_under_utlopt_varsel") }));
      }
      if (vare.over_terskel === false) {
        deler.push(el("p", { role: "alert",
          text: t("ui.tollkode.under_terskel_varsel")
            .replace("{sikkerhet}", String(vare.sikkerhet))
            .replace("{terskel}", String(vare.terskel_brukt)) }));
      }
      deler.push(forslagstabell(rader));
      if (grunnrader.length) deler.push(grunntabell(grunnrader));
    }

    if (skriver) {
      deler.push(forslagsskjema(ctx, last, vare, nomenklaturer,
                                kvitter));
      if (vare.forslag_id && !vare.klar_til_deklarering) {
        const knapp = el("button", { type: "button",
          text: t("ui.tollkode.knapp.merk_klart") });
        knapp.addEventListener("click", async () => {
          knapp.disabled = true;
          try {
            await merkForslagKlart(vare.forslag_id);
          } catch (e) {
            knapp.disabled = false;
            if (e instanceof UautorisertFeil) {
              ctx.paaUautorisert(); return;
            }
            const m = e && e.status === 409
              ? t("ui.tollkode.feil.klart_avvist")
              : t("ui.tollkode.feil.generell");
            kvitter(m); meldLive(m);
            return;
          }
          kvitter(t("ui.tollkode.skjema.klart_ok"));
          meldLive(t("ui.tollkode.skjema.klart_ok"));
          await last();
        });
        deler.push(el("div", { class: "skjema-bunn" }, knapp,
          el("p", { class: "muted",
                    text: t("ui.tollkode.klart_hjelp") })));
      }
    }
    sett(node, ...deler);
  };
  return { node, aapne };
}


export function visTollkode(hoved, ctx) {
  const hode = () => flateHode(t("ui.tollkode.tittel"),
    t("ui.tollkode.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenVare = null;
  let apenNomenklatur = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/toll"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const nomenklaturer = d.nomenklaturer || [];
      const varer = d.varer || [];
      const nomen = nomenklaturpanel(ctx, last, kvitter,
                                     (id) => { apenNomenklatur = id; });
      const detalj = varepanel(ctx, last, kvitter,
                               (id) => { apenVare = id; },
                               nomenklaturer);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.tollkode.oversikt.tittel") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT.
        el("p", { class: "muted",
                  text: t("ui.tollkode.oversikt.hvorfor") }));

      const nomenseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.tollkode.nomenklatur.tittel") }));
      if (!nomenklaturer.length) {
        nomenseksjon.append(el("p", { role: "alert",
          text: t("ui.tollkode.nomenklatur.ingen") }));
      } else {
        nomenseksjon.append(
          nomenklaturtabell(nomenklaturer, nomen.aapne));
      }

      const vareseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.tollkode.varer.tittel") }));
      if (!varer.length) {
        vareseksjon.append(el("p", { class: "muted",
          text: t("ui.tollkode.varer.ingen") }));
      } else {
        vareseksjon.append(varetabell(varer, detalj.aapne));
      }

      const deler = [oversikt, nomenseksjon, nomen.node, vareseksjon,
                     detalj.node, funnseksjon(ctx, last, kvitter)];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(nomenklaturskjema(ctx, last, kvitter),
                   vareskjema(ctx, last, kvitter),
                   kravskjema(ctx, last, d.krav, kvitter));
      }
      sett(kropp, ...deler);
      if (apenNomenklatur) {
        const rad = nomenklaturer.find(
          (x) => x.nomenklatur_id === apenNomenklatur);
        if (rad) nomen.aapne(rad); else apenNomenklatur = null;
      }
      if (apenVare) {
        const rad = varer.find((x) => x.vare_id === apenVare);
        if (rad) detalj.aapne(rad); else apenVare = null;
      }
    });
  last();
}
