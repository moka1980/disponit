// EHF- og Peppol-avviksretteren (M-54 v1) — FORMEN, IKKE INNHOLDET.
//
// FLATENS VIKTIGSTE JOBB er å vise HVILKEN REGEL en dom ble felt
// under, og om den regelen fortsatt gjelder.
//
// EHF er den norske innrettingen av PEPPOL BIS Billing 3.0, og begge
// får nye versjoner. Et avvik funnet mot en gammel regelsettversjon er
// ikke et avvik — det er en FORELDET DOM SOM SER VELFORMET UT. Det er
// forskjellen fra en feil: en feil gir et avvik noen ser, mens en
// foreldet regel gir et svar som er velformet, selvsikkert og galt.
//
// DERFOR STÅR REGELSETTVERSJONEN PÅ HVER DOM, og ved siden av den om
// settet er gyldig I DAG. En dom uten den opplysningen er nettopp det
// klyngen finnes for å unngå.
//
// TRE UTFALL PER REGEL, OG DET TREDJE ER DET SOM SKILLER MODULEN FRA
// EN VANLIG VALIDATOR: `feil`, `advarsel` og `uten_grunnlag`. Det
// siste betyr at regelen nevnte et felt vi ikke har trukket ut — den
// er IKKE stille grønn, og tallet står på dommen så den som leser den
// ser hvor mye den IKKE dekket.
//
// DET FINNES INGEN «SEND»-KNAPP OG INGEN SIGNATUR. 121 har ingen
// mottaker og ingen utboks. «Klar til signering» er en tilstand HOS
// OSS, og hjelpeteksten sier det — ellers kunne ordet leses som
// «sendt».
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, hentJson, lukkEhffunn, merkRettingKlar,
  nyIdempotensnokkel, registrerEhfdokument, registrerEhffelter,
  registrerEhfregel, registrerEhfregelsett, registrerEhfretting,
  settEhfGyldigTil, settEhfkrav, validerEhfdokument,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// LUKKEDE SETT, speilet fra 121.
export const STANDARDER = ["ubl", "peppol_bis", "ehf"];
export const KRAVTYPER = ["finnes", "ikke_tom", "i_kodeliste",
                          "lik_sum"];
export const ALVORLIGHETER = ["feil", "advarsel"];
export const RETNINGER = ["inngaaende", "utgaaende"];

const STANDARDTEKST = {
  ubl: "ui.ehf.standard_ubl",
  peppol_bis: "ui.ehf.standard_peppol",
  ehf: "ui.ehf.standard_ehf",
};

const KRAVTEKST = {
  finnes: "ui.ehf.krav_finnes",
  ikke_tom: "ui.ehf.krav_ikke_tom",
  i_kodeliste: "ui.ehf.krav_i_kodeliste",
  lik_sum: "ui.ehf.krav_lik_sum",
};

const ALVORTEKST = {
  feil: "ui.ehf.alvor_feil",
  advarsel: "ui.ehf.alvor_advarsel",
  uten_grunnlag: "ui.ehf.alvor_uten_grunnlag",
};

const RETNINGSTEKST = {
  inngaaende: "ui.ehf.retning_inngaaende",
  utgaaende: "ui.ehf.retning_utgaaende",
};

const FUNNTEKST = {
  regelsett_utlopt: "ui.ehf.funn_utlopt",
  regelsett_utloper_snart: "ui.ehf.funn_utloper",
  validering_mot_utlopt_regelsett: "ui.ehf.funn_dom_utlopt",
  dokument_uten_validering: "ui.ehf.funn_uvalidert",
  avvik_uten_retting: "ui.ehf.funn_uten_retting",
  retting_ikke_klar: "ui.ehf.funn_ikke_klar",
  ingen_krav: "ui.ehf.funn_uten_krav",
};


// REGELSETTET, MED GYLDIGHETEN SIN — ALDRI VERSJONEN ALENE.
//
// MUTASJONEN SOM DREPER PORTEN: returner bare «ehf 3.0». En versjon
// uten om den gjelder i dag er nøyaktig den opplysningen som gjør en
// foreldet dom umulig å skille fra en riktig.
export function regelsettTekst(rad) {
  if (!rad || !rad.standard) return t("ui.ehf.uten_regelsett");
  const navn = `${t(STANDARDTEKST[rad.standard] || rad.standard)}`
    + ` ${rad.versjon}`;
  // `gyldig_naa` kan mangle på rader som ikke bærer den (funnlisten);
  // da sier vi ingenting om gyldigheten framfor å gjette.
  if (rad.gyldig_naa === null || rad.gyldig_naa === undefined) {
    return navn;
  }
  return t(rad.gyldig_naa ? "ui.ehf.regelsett_gyldig"
                          : "ui.ehf.regelsett_utlopt")
    .replace("{navn}", navn);
}


// UTLØPET, MED RETNING. «Om 12 døgn» og «12 døgn siden» er ikke samme
// sak (118s form).
export function utlopTekst(dogn) {
  if (dogn === null || dogn === undefined) {
    return t("ui.ehf.uten_sluttdato");
  }
  if (dogn < 0) {
    return t("ui.ehf.utlop_passert").replace("{n}", String(-dogn));
  }
  if (dogn === 0) return t("ui.ehf.utlop_i_dag");
  return t("ui.ehf.utlop_om").replace("{n}", String(dogn));
}


// DOMMEN, MED ALLE FIRE TALLENE.
//
// `uten_grunnlag` ER MED SELV NÅR DEN ER NULL, og det er med vilje: en
// leser som bare ser «2 feil» vet ikke om resten var grønn eller
// udømt. Tallet som mangler er det farligste av dem.
export function domTekst(rad) {
  if (!rad || !rad.validering_id) return t("ui.ehf.uvalidert");
  return t(rad.gyldig ? "ui.ehf.dom_gyldig" : "ui.ehf.dom_ugyldig")
    .replace("{regler}", String(rad.antall_regler))
    .replace("{feil}", String(rad.antall_feil))
    .replace("{advarsler}", String(rad.antall_advarsler))
    .replace("{utenfor}", String(rad.antall_uten_grunnlag));
}


// AVVIKETS FUNNE VERDI, MED SKILLET SOM BETYR NOE.
//
// `null` betyr at FELTET IKKE FANTES; tom streng at det fantes og var
// tomt. Å vise begge som «–» ville visket ut det første et menneske
// spør om.
export function funnetTekst(verdi) {
  if (verdi === null || verdi === undefined) {
    return t("ui.ehf.feltet_fantes_ikke");
  }
  if (verdi === "") return t("ui.ehf.feltet_var_tomt");
  return verdi;
}


export function rettingTekst(rad) {
  if (!rad || !rad.retting_id) return t("ui.ehf.uten_retting");
  return t(rad.klar_til_signering ? "ui.ehf.retting_klar"
                                  : "ui.ehf.retting_utkast")
    .replace("{fra}", funnetTekst(rad.fra_verdi))
    .replace("{til}", rad.til_verdi);
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
          ? t("ui.ehf.feil.tilstand")
          : t("ui.ehf.feil.generell") }));
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


// SAMMENDRAGET. `dommer_under_utlopt` står FØRST og i fet skrift: det
// er det ene tallet klyngen finnes for.
export function sammendrag(s) {
  const p = el("p");
  p.append(el("strong", {
    text: t("ui.ehf.dommer_under_utlopt")
      .replace("{n}", String(s.dommer_under_utlopt ?? 0)) }));
  p.append(" ", el("span", {
    text: t("ui.ehf.tellinger")
      .replace("{dok}", String(s.dokumenter ?? 0))
      .replace("{validerte}", String(s.validerte ?? 0))
      .replace("{feil}", String(s.med_feil ?? 0)) }));
  if (s.uten_grunnlag > 0) {
    // REGLER SOM IKKE HADDE ET GRUNNLAG Å DØMME PÅ.
    p.append(" ", el("strong", {
      text: t("ui.ehf.uten_grunnlag_sum")
        .replace("{n}", String(s.uten_grunnlag)) }));
  }
  if (s.utlopte_regelsett > 0) {
    p.append(" ", el("span", { class: "muted",
      text: t("ui.ehf.utlopte_regelsett")
        .replace("{n}", String(s.utlopte_regelsett)) }));
  }
  if (s.uvaliderte > 0) {
    p.append(" ", el("span", { class: "muted",
      text: t("ui.ehf.uvaliderte")
        .replace("{n}", String(s.uvaliderte)) }));
  }
  if (!s.gyldige_regelsett) {
    // UTEN ET GYLDIG SETT KAN INGENTING VALIDERES, og det er ikke en
    // detalj — det er modulen som har sluttet å virke.
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.ehf.ingen_gyldig_regelsett") }));
  }
  if (s.apne_funn > 0) {
    p.append(" ", el("strong", {
      text: t("ui.ehf.apne_funn").replace("{n}", String(s.apne_funn)) }));
  }
  if (!s.har_krav) {
    p.append(" ", el("strong", { text: t("ui.ehf.ingen_krav") }));
  }
  if (s.vist < s.dokumenter) {
    p.append(" ", el("strong", {
      text: t("ui.ehf.avkortet").replace("{vist}", String(s.vist)) }));
  }
  return p;
}


export function regelsettabell(regelsett, aapne) {
  const tbody = el("tbody");
  for (const r of regelsett) {
    const knapp = el("button", { type: "button",
      text: t("ui.ehf.knapp.apne_regler") });
    knapp.addEventListener("click", () => aapne(r));
    tbody.append(el("tr", {},
      el("th", { scope: "row",
                 text: t(STANDARDTEKST[r.standard] || r.standard) }),
      el("td", { text: r.versjon }),
      el("td", { text: r.gyldig_fra }),
      el("td", { text: r.gyldig_til || t("ui.ehf.uten_sluttdato") }),
      // GYLDIGHETEN REGNES I BASEN, ikke her: to lesere skal ikke
      // kunne komme til hver sin konklusjon.
      el("td", { text: r.gyldig_naa ? t("ui.ehf.ja")
                                    : t("ui.ehf.nei") }),
      el("td", { text: utlopTekst(r.dogn_til_utlop) }),
      el("td", { title: r.innhold_sha256,
                 text: `${(r.innhold_sha256 || "").slice(0, 12)}…` }),
      el("td", { class: "tall", text: String(r.antall_regler) }),
      el("td", { class: "tall",
                 text: String(r.antall_valideringer) }),
      el("td", {}, knapp)));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.ehf.regelsett.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.ehf.kol.standard") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.versjon") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.fra") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.til") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.gyldig_naa") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.utlop") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.sum") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.regler") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.dommer") }),
        el("th", { scope: "col",
                   text: t("ui.ehf.kol.handling") }))),
      tbody));
}


export function regeltabell(regler) {
  const tbody = el("tbody");
  for (const g of regler) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: g.kode }),
      el("td", { class: "brytord", text: g.sti }),
      el("td", { text: t(KRAVTEKST[g.krav] || g.krav) }),
      // PARAMETEREN, SÅ REGELEN KAN LESES UTEN Å KJØRES.
      el("td", { text: g.krav === "i_kodeliste"
                   ? (g.kodeverdi || []).join(", ")
                   : (g.sum_sti || "–") }),
      el("td", { text: t(ALVORTEKST[g.alvorlighet]
                         || g.alvorlighet) }),
      el("td", { text: g.beskrivelse })));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.ehf.regler.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.ehf.kol.kode") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.sti") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.krav") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.parameter") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.alvorlighet") }),
        el("th", { scope: "col",
                   text: t("ui.ehf.kol.beskrivelse") }))),
      tbody));
}


// DOKUMENTTABELLEN. REGELSETTVERSJONEN STÅR PÅ HVER RAD, med om den
// gjelder i dag.
export function dokumenttabell(dokumenter, aapne) {
  const tbody = el("tbody");
  for (const d of dokumenter) {
    const knapp = el("button", { type: "button",
      text: t("ui.ehf.knapp.apne") });
    knapp.addEventListener("click", () => aapne(d));
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: d.ekstern_ref }),
      el("td", { text: t(RETNINGSTEKST[d.retning] || d.retning) }),
      el("td", { text: d.motpart }),
      el("td", { text: d.fakturadato }),
      el("td", { class: "tall", text: String(d.antall_felt) }),
      el("td", { text: regelsettTekst({
        standard: d.standard, versjon: d.versjon,
        gyldig_naa: d.regelsett_gyldig_naa }) }),
      el("td", { text: domTekst(d) }),
      el("td", { class: "tall",
                 text: `${d.klare_rettinger}/${d.antall_rettinger}` }),
      el("td", {}, knapp)));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.ehf.dokumenter.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.ehf.kol.ref") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.retning") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.motpart") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.dato") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.felter") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.regelsett") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.dom") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.rettinger") }),
        el("th", { scope: "col",
                   text: t("ui.ehf.kol.handling") }))),
      tbody));
}


// AVVIKSTABELLEN. `funnet_verdi` skiller «fantes ikke» fra «var tomt».
export function avvikstabell(avvik) {
  const tbody = el("tbody");
  for (const a of avvik) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: a.regelkode }),
      el("td", { text: t(ALVORTEKST[a.alvorlighet]
                         || a.alvorlighet) }),
      el("td", { class: "brytord", text: a.sti }),
      el("td", { text: funnetTekst(a.funnet_verdi) }),
      el("td", { text: a.forventet || "–" }),
      el("td", { text: a.beskrivelse }),
      el("td", { text: rettingTekst(a) })));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.ehf.avvik.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.ehf.kol.kode") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.alvorlighet") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.sti") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.funnet") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.forventet") }),
        el("th", { scope: "col",
                   text: t("ui.ehf.kol.beskrivelse") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.retting") }))),
      tbody));
}


// VALIDERINGSREKKEN. HELE rekken: en ny regelsettversjon gir en ny
// rad, og det er der «hva sa standarden den gangen» står.
export function valideringstabell(valideringer) {
  const tbody = el("tbody");
  for (const v of valideringer) {
    tbody.append(el("tr", {},
      el("th", { scope: "row",
                 text: v.validert.slice(0, 16).replace("T", " ") }),
      el("td", { text: regelsettTekst({
        standard: v.standard, versjon: v.versjon,
        gyldig_naa: v.regelsett_gyldig_naa }) }),
      el("td", { class: "tall", text: String(v.antall_regler) }),
      el("td", { class: "tall", text: String(v.antall_feil) }),
      el("td", { class: "tall", text: String(v.antall_advarsler) }),
      el("td", { class: "tall",
                 text: String(v.antall_uten_grunnlag) }),
      el("td", { text: v.gyldig ? t("ui.ehf.ja") : t("ui.ehf.nei") }),
      el("td", { text: v.validert_av })));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.ehf.valideringer.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.ehf.kol.validert") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.regelsett") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.regler") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.feil") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.advarsler") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.utenfor") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.gyldig") }),
        el("th", { scope: "col", text: t("ui.ehf.kol.av") }))),
      tbody));
}


function kravskjema(ctx, last, krav, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const utlop = el("input", { id: "ef-t-utlop", name: "utlop",
    type: "number", required: true, min: "1", max: "365", step: "1" });
  const avvik = el("input", { id: "ef-t-avvik", name: "avvik",
    type: "number", required: true, min: "1", max: "365", step: "1" });
  // VERDIENE KOMMER FRA BASEN, ikke fra en konstant her.
  utlop.value = krav ? String(krav.utlopsvarsel_dogn) : "";
  avvik.value = krav ? String(krav.avviksfrist_dogn) : "";
  const knapp = el("button", { type: "submit",
    text: t("ui.ehf.knapp.lagre_krav") });
  skjema.append(
    felt("ef-t-utlop", "ui.ehf.krav.utlop", utlop,
         "ui.ehf.krav.utlop_hjelp"),
    felt("ef-t-avvik", "ui.ehf.krav.avvik", avvik,
         "ui.ehf.krav.avvik_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.ehf.skjema.krav_ok",
    send: (idem) => settEhfkrav({
      utlopsvarsel_dogn: Math.trunc(Number(utlop.value)),
      avviksfrist_dogn: Math.trunc(Number(avvik.value)),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.ehf.krav.tittel") }),
    el("p", { class: "muted", text: t("ui.ehf.krav.hvorfor") }),
    skjema, utfall);
}


// REGELSETTSKJEMAET. ET ALT UTLØPT SETT KAN REGISTRERES, og
// hjelpeteksten sier hvorfor: modulen finnes for å kunne svare på hva
// standarden sa den gangen.
function regelsettskjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const standard = el("select", { id: "ef-r-standard",
    name: "standard", required: true });
  standard.append(el("option", { value: "",
    text: t("ui.ehf.regelsett.velg_standard") }));
  for (const s of STANDARDER) {
    standard.append(el("option", { value: s,
      text: t(STANDARDTEKST[s] || s) }));
  }
  const versjon = el("input", { id: "ef-r-versjon", name: "versjon",
    type: "text", required: true, maxlength: "200" });
  const fra = el("input", { id: "ef-r-fra", name: "fra",
    type: "date", required: true });
  const til = el("input", { id: "ef-r-til", name: "til",
    type: "date" });
  const sum = el("input", { id: "ef-r-sum", name: "sum",
    type: "text", required: true, minlength: "64", maxlength: "64",
    pattern: "[0-9a-fA-F]{64}" });
  const url = el("input", { id: "ef-r-url", name: "url",
    type: "url", maxlength: "2000" });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.ehf.knapp.lagre_regelsett") });
  const vurder = () => {
    knapp.disabled = !standard.value || !versjon.value.trim()
      || !fra.value || !/^[0-9a-fA-F]{64}$/.test(sum.value.trim());
  };
  for (const k of [standard, versjon, fra, til, sum, url]) {
    k.addEventListener("change", vurder);
    k.addEventListener("input", vurder);
  }
  skjema.append(
    felt("ef-r-standard", "ui.ehf.regelsett.standard", standard,
         null),
    felt("ef-r-versjon", "ui.ehf.regelsett.versjon", versjon,
         "ui.ehf.regelsett.versjon_hjelp"),
    felt("ef-r-fra", "ui.ehf.regelsett.fra", fra, null),
    felt("ef-r-til", "ui.ehf.regelsett.til", til,
         "ui.ehf.regelsett.til_hjelp"),
    felt("ef-r-sum", "ui.ehf.regelsett.sum", sum,
         "ui.ehf.regelsett.sum_hjelp"),
    felt("ef-r-url", "ui.ehf.regelsett.url", url, null),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.ehf.skjema.regelsett_ok",
    tilbakestill: () => {
      standard.value = ""; versjon.value = ""; fra.value = "";
      til.value = ""; sum.value = ""; url.value = "";
      knapp.disabled = true;
    },
    send: (idem) => registrerEhfregelsett({
      standard: standard.value,
      versjon: versjon.value.trim(),
      gyldig_fra: fra.value,
      // TOM SLUTTDATO ER `null` OG BETYR «GJELDER FORTSATT» — ikke
      // «gjelder for alltid», og sveipen skiller på det.
      gyldig_til: til.value || null,
      innhold_sha256: sum.value.trim().toLowerCase(),
      kilde_url: url.value.trim() || null,
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.ehf.regelsett.skjema_tittel") }),
    el("p", { class: "muted",
              text: t("ui.ehf.regelsett.hvorfor") }),
    skjema, utfall);
}


// SLUTTDATOEN. DENNE FINNES FORDI REGELEN ER MYNDIGHETENS.
function sluttdatoskjema(ctx, last, regelsett, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const til = el("input", { id: "ef-g-til", name: "til",
    type: "date" });
  til.value = regelsett.gyldig_til || "";
  const knapp = el("button", { type: "submit",
    text: t("ui.ehf.knapp.sett_sluttdato") });
  skjema.append(
    felt("ef-g-til", "ui.ehf.sluttdato.felt", til,
         "ui.ehf.sluttdato.hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.ehf.skjema.sluttdato_ok",
    send: (idem) => settEhfGyldigTil(regelsett.regelsett_id,
                                     til.value || null, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.ehf.sluttdato.tittel") }),
    el("p", { class: "muted", text: t("ui.ehf.sluttdato.hvorfor") }),
    skjema, utfall);
}


function regelskjema(ctx, last, regelsettId, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const kode = el("input", { id: "ef-g-kode", name: "kode",
    type: "text", required: true, maxlength: "200" });
  const sti = el("input", { id: "ef-g-sti", name: "sti",
    type: "text", required: true, maxlength: "200" });
  const krav = el("select", { id: "ef-g-krav", name: "krav",
    required: true });
  krav.append(el("option", { value: "",
    text: t("ui.ehf.regel.velg_krav") }));
  for (const k of KRAVTYPER) {
    krav.append(el("option", { value: k, text: t(KRAVTEKST[k] || k) }));
  }
  const kodeverdi = el("input", { id: "ef-g-kodeverdi",
    name: "kodeverdi", type: "text", maxlength: "2000" });
  const sumSti = el("input", { id: "ef-g-sumsti", name: "sumsti",
    type: "text", maxlength: "200" });
  const alvor = el("select", { id: "ef-g-alvor", name: "alvor",
    required: true });
  alvor.append(el("option", { value: "",
    text: t("ui.ehf.regel.velg_alvorlighet") }));
  for (const a of ALVORLIGHETER) {
    alvor.append(el("option", { value: a,
      text: t(ALVORTEKST[a] || a) }));
  }
  const beskrivelse = el("input", { id: "ef-g-beskrivelse",
    name: "beskrivelse", type: "text", required: true,
    minlength: "4", maxlength: "4000" });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.ehf.knapp.lagre_regel") });
  const vurder = () => {
    // KRAVET OG PARAMETEREN HENGER SAMMEN. 121 nekter ellers — og en
    // `i_kodeliste` uten kodeliste ville vært STILLE GRØNN, den verste
    // tilstanden en regel kan ha.
    const trengerKodeliste = krav.value === "i_kodeliste";
    const trengerSum = krav.value === "lik_sum";
    const harKodeliste = kodeverdi.value.split(",")
      .some((x) => x.trim());
    knapp.disabled = !kode.value.trim() || !sti.value.trim()
      || !krav.value || !alvor.value
      || beskrivelse.value.trim().length < 4
      || trengerKodeliste !== harKodeliste
      || trengerSum !== Boolean(sumSti.value.trim());
  };
  for (const k of [kode, sti, krav, kodeverdi, sumSti, alvor,
                   beskrivelse]) {
    k.addEventListener("change", vurder);
    k.addEventListener("input", vurder);
  }
  skjema.append(
    felt("ef-g-kode", "ui.ehf.regel.kode", kode,
         "ui.ehf.regel.kode_hjelp"),
    felt("ef-g-sti", "ui.ehf.regel.sti", sti,
         "ui.ehf.regel.sti_hjelp"),
    felt("ef-g-krav", "ui.ehf.regel.krav", krav,
         "ui.ehf.regel.krav_hjelp"),
    felt("ef-g-kodeverdi", "ui.ehf.regel.kodeverdi", kodeverdi,
         "ui.ehf.regel.kodeverdi_hjelp"),
    felt("ef-g-sumsti", "ui.ehf.regel.sumsti", sumSti,
         "ui.ehf.regel.sumsti_hjelp"),
    felt("ef-g-alvor", "ui.ehf.regel.alvorlighet", alvor,
         "ui.ehf.regel.alvorlighet_hjelp"),
    felt("ef-g-beskrivelse", "ui.ehf.regel.beskrivelse", beskrivelse,
         null),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.ehf.skjema.regel_ok",
    tilbakestill: () => {
      kode.value = ""; sti.value = ""; krav.value = "";
      kodeverdi.value = ""; sumSti.value = ""; alvor.value = "";
      beskrivelse.value = ""; knapp.disabled = true;
    },
    send: (idem) => registrerEhfregel({
      regelsett_id: regelsettId,
      kode: kode.value.trim(),
      sti: sti.value.trim(),
      krav: krav.value,
      kodeverdi: kodeverdi.value.split(",").map((x) => x.trim())
        .filter(Boolean),
      sum_sti: sumSti.value.trim() || null,
      alvorlighet: alvor.value,
      beskrivelse: beskrivelse.value.trim(),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.ehf.regel.tittel") }),
    el("p", { class: "muted", text: t("ui.ehf.regel.hvorfor") }),
    skjema, utfall);
}


function dokumentskjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const retning = el("select", { id: "ef-d-retning",
    name: "retning", required: true });
  retning.append(el("option", { value: "",
    text: t("ui.ehf.dokument.velg_retning") }));
  for (const r of RETNINGER) {
    retning.append(el("option", { value: r,
      text: t(RETNINGSTEKST[r] || r) }));
  }
  const ref = el("input", { id: "ef-d-ref", name: "ref",
    type: "text", required: true, maxlength: "200" });
  const motpart = el("input", { id: "ef-d-motpart", name: "motpart",
    type: "text", required: true, maxlength: "500" });
  const dato = el("input", { id: "ef-d-dato", name: "dato",
    type: "date", required: true });
  const sum = el("input", { id: "ef-d-sum", name: "sum",
    type: "text", required: true, minlength: "64", maxlength: "64",
    pattern: "[0-9a-fA-F]{64}" });
  const storrelse = el("input", { id: "ef-d-bytes", name: "bytes",
    type: "number", required: true, min: "1", step: "1" });
  const lagring = el("input", { id: "ef-d-lagring", name: "lagring",
    type: "text", required: true, maxlength: "200" });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.ehf.knapp.lagre_dokument") });
  const vurder = () => {
    knapp.disabled = !retning.value || !ref.value.trim()
      || !motpart.value.trim() || !dato.value
      || !/^[0-9a-fA-F]{64}$/.test(sum.value.trim())
      || !(Number(storrelse.value) >= 1) || !lagring.value.trim();
  };
  for (const k of [retning, ref, motpart, dato, sum, storrelse,
                   lagring]) {
    k.addEventListener("change", vurder);
    k.addEventListener("input", vurder);
  }
  skjema.append(
    felt("ef-d-retning", "ui.ehf.dokument.retning", retning,
         "ui.ehf.dokument.retning_hjelp"),
    felt("ef-d-ref", "ui.ehf.dokument.ref", ref, null),
    felt("ef-d-motpart", "ui.ehf.dokument.motpart", motpart, null),
    felt("ef-d-dato", "ui.ehf.dokument.dato", dato, null),
    felt("ef-d-sum", "ui.ehf.dokument.sum", sum,
         "ui.ehf.dokument.sum_hjelp"),
    felt("ef-d-bytes", "ui.ehf.dokument.bytes", storrelse, null),
    felt("ef-d-lagring", "ui.ehf.dokument.lagring", lagring, null),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.ehf.skjema.dokument_ok",
    tilbakestill: () => {
      retning.value = ""; ref.value = ""; motpart.value = "";
      dato.value = ""; sum.value = ""; storrelse.value = "";
      lagring.value = ""; knapp.disabled = true;
    },
    send: (idem) => registrerEhfdokument({
      retning: retning.value,
      ekstern_ref: ref.value.trim(),
      motpart: motpart.value.trim(),
      fakturadato: dato.value,
      innhold_sha256: sum.value.trim().toLowerCase(),
      innhold_bytes: Math.trunc(Number(storrelse.value)),
      lagringsnokkel: lagring.value.trim(),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.ehf.dokument.tittel") }), skjema, utfall);
}


// RETTINGSSKJEMAET. `uten_grunnlag`-AVVIK TILBYS IKKE: døra nekter,
// og en knapp som alltid feiler lærer brukeren at systemet er
// upålitelig.
function rettingsskjema(ctx, last, avvik, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const valg = el("select", { id: "ef-x-avvik", name: "avvik",
    required: true });
  valg.append(el("option", { value: "",
    text: t("ui.ehf.retting.velg_avvik") }));
  const rettbare = avvik.filter(
    (a) => a.alvorlighet !== "uten_grunnlag" && !a.retting_id);
  for (const a of rettbare) {
    valg.append(el("option", { value: a.avvik_id,
      text: `${a.regelkode} — ${a.sti}` }));
  }
  const feltSti = el("input", { id: "ef-x-sti", name: "sti",
    type: "text", required: true, maxlength: "200" });
  const til = el("input", { id: "ef-x-til", name: "til",
    type: "text", required: true, maxlength: "4000" });
  const begrunnelse = el("input", { id: "ef-x-begrunnelse",
    name: "begrunnelse", type: "text", required: true,
    minlength: "4", maxlength: "4000" });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.ehf.knapp.lagre_retting") });
  const vurder = () => {
    knapp.disabled = !valg.value || !feltSti.value.trim()
      || !til.value || begrunnelse.value.trim().length < 4;
  };
  for (const k of [valg, feltSti, til, begrunnelse]) {
    k.addEventListener("change", vurder);
    k.addEventListener("input", vurder);
  }
  // STIEN FYLLES FRA AVVIKET. Den som retter skal ikke måtte skrive
  // av en XPath for hånd — en skrivefeil der ville gitt en retting av
  // et felt ingen så på.
  valg.addEventListener("change", () => {
    const a = rettbare.find((x) => x.avvik_id === valg.value);
    if (a && !feltSti.value) feltSti.value = a.sti;
    vurder();
  });
  const deler = [];
  if (!rettbare.length) {
    deler.push(el("p", { class: "muted",
      text: t("ui.ehf.retting.ingen_rettbare") }));
  } else {
    deler.push(
      felt("ef-x-avvik", "ui.ehf.retting.avvik", valg,
           "ui.ehf.retting.avvik_hjelp"),
      felt("ef-x-sti", "ui.ehf.retting.sti", feltSti, null),
      felt("ef-x-til", "ui.ehf.retting.til", til,
           "ui.ehf.retting.til_hjelp"),
      felt("ef-x-begrunnelse", "ui.ehf.retting.begrunnelse",
           begrunnelse, "ui.ehf.retting.begrunnelse_hjelp"),
      el("div", { class: "skjema-bunn" }, knapp));
  }
  skjema.append(...deler);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.ehf.skjema.retting_ok",
    tilbakestill: () => {
      valg.value = ""; feltSti.value = ""; til.value = "";
      begrunnelse.value = ""; knapp.disabled = true;
    },
    send: (idem) => {
      const a = rettbare.find((x) => x.avvik_id === valg.value);
      return registrerEhfretting(valg.value, {
        felt_sti: feltSti.value.trim(),
        // FRA-VERDIEN KOMMER FRA AVVIKET, ikke fra et felt brukeren
        // fyller: den er hva som FAKTISK sto der, og `null` betyr at
        // feltet skal legges til.
        fra_verdi: a ? a.funnet_verdi : null,
        til_verdi: til.value,
        begrunnelse: begrunnelse.value.trim(),
      }, idem);
    },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.ehf.retting.tittel") }),
    el("p", { class: "muted", text: t("ui.ehf.retting.hvorfor") }),
    skjema, utfall);
}


// FUNNSEKSJONEN. `validering_mot_utlopt_regelsett` KAN IKKE LUKKES —
// døra nekter det, av samme grunn som M-49s bekreftede treff (117),
// M-46s udekkede absolutte krav (118), M-51s takfunn (119) og M-55s
// uhenviste forveksling (120).
function funnseksjon(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.ehf.funn.tittel") }),
    el("p", { class: "muted", text: t("ui.ehf.laster") }));
  (async () => {
    let d;
    try {
      d = await hentJson("/v1/ehf/funn");
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.ehf.funn.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.ehf.feil.generell") }));
      return;
    }
    const funn = d.funn || [];
    const deler = [el("h2", { text: t("ui.ehf.funn.tittel") })];
    if (!funn.length) {
      deler.push(el("p", { class: "muted",
        text: t("ui.ehf.funn.ingen") }));
    } else {
      const tbody = el("tbody");
      for (const f of funn) {
        tbody.append(el("tr", {},
          el("th", { scope: "row",
                     text: t(FUNNTEKST[f.funntype] || f.funntype) }),
          el("td", { text: f.ekstern_ref
                       || regelsettTekst({ standard: f.standard,
                                           versjon: f.regelsettversjon })
                       || "–" }),
          el("td", { class: "tall",
                     text: f.over_grense === null
                           || f.over_grense === undefined
                       ? "–" : String(f.over_grense) }),
          el("td", { text: f.detalj || "–" }),
          el("td", { text: f.forst_sett.slice(0, 10) })));
      }
      deler.push(el("div", { class: "tablewrap" }, el("table", {},
        el("caption", { text: t("ui.ehf.funn.tittel") }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col", text: t("ui.ehf.kol.funntype") }),
          el("th", { scope: "col", text: t("ui.ehf.kol.gjelder") }),
          el("th", { scope: "col", text: t("ui.ehf.kol.dogn") }),
          el("th", { scope: "col", text: t("ui.ehf.kol.detalj") }),
          el("th", { scope: "col",
                     text: t("ui.ehf.kol.forst_sett") }))),
        tbody)));
      if (harScope(ctx, "bestilling:opprett")) {
        const lukkbare = funn.filter(
          (f) => f.funntype !== "validering_mot_utlopt_regelsett");
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
  const valg = el("select", { id: "ef-f-valg", name: "funn",
    required: true });
  valg.append(el("option", { value: "",
    text: t("ui.ehf.funn.velg") }));
  for (const f of funn) {
    valg.append(el("option", { value: f.funn_id,
      text: `${t(FUNNTEKST[f.funntype] || f.funntype)}`
            + ` — ${f.ekstern_ref || f.detalj || ""}` }));
  }
  const notat = el("input", { id: "ef-f-notat", name: "notat",
    type: "text", required: true, minlength: "4", maxlength: "4000" });
  const knapp = el("button", { type: "submit",
    text: t("ui.ehf.knapp.lukk_funn") });
  skjema.append(
    felt("ef-f-valg", "ui.ehf.funn.hvilket", valg, null),
    felt("ef-f-notat", "ui.ehf.funn.notat", notat,
         "ui.ehf.funn.notat_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.ehf.skjema.funn_lukket",
    tilbakestill: () => { valg.value = ""; notat.value = ""; },
    send: (idem) => lukkEhffunn(valg.value, notat.value.trim(), idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.ehf.funn.lukk_tittel") }),
    el("p", { class: "muted", text: t("ui.ehf.funn.lukk_hvorfor") }),
    skjema, utfall);
}


// REGELSETTPANELET. Reglene, og sluttdatoen som kan settes.
function regelpanel(ctx, last, kvitter, settApent) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  const aapne = async (regelsett) => {
    settApent(regelsett.regelsett_id);
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.ehf.regler.tittel") }),
         el("p", { class: "muted", text: t("ui.ehf.laster") }));
    let d = { regler: [] };
    try {
      const id = encodeURIComponent(regelsett.regelsett_id);
      d = await hentJson(`/v1/ehf/regelsett/${id}/regler`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.ehf.regler.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.ehf.feil.generell") }));
      return;
    }
    const regler = d.regler || [];
    const skriver = harScope(ctx, "bestilling:opprett");
    const deler = [
      el("h2", { text: t("ui.ehf.regler.tittel") }),
      el("p", { class: "muted",
                text: regelsettTekst(regelsett) }),
    ];
    if (!regler.length) {
      // ET REGELSETT UTEN REGLER DØMMER INGENTING, og en validering
      // mot det ville sagt «null feil» om et dokument ingen har sett
      // på.
      deler.push(el("p", { role: "alert",
        text: t("ui.ehf.regler.ingen") }));
    } else {
      deler.push(regeltabell(regler));
    }
    if (skriver) {
      deler.push(regelskjema(ctx, last, regelsett.regelsett_id,
                             kvitter),
                 sluttdatoskjema(ctx, last, regelsett, kvitter));
    }
    sett(node, ...deler);
  };
  return { node, aapne };
}


// DOKUMENTPANELET. Dommene, avvikene og de tre handlingene som
// finnes: VALIDER, RETT, MERK KLAR. Ingen fjerde.
function dokumentpanel(ctx, last, kvitter, settApen, regelsett) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  const aapne = async (dokument) => {
    settApen(dokument.dokument_id);
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.ehf.detalj.tittel") }),
         el("p", { class: "muted", text: t("ui.ehf.laster") }));
    let valideringer = { valideringer: [] };
    let avvik = { avvik: [] };
    try {
      const id = encodeURIComponent(dokument.dokument_id);
      valideringer = await hentJson(
        `/v1/ehf/dokument/${id}/valideringer`);
      if (dokument.validering_id) {
        const vid = encodeURIComponent(dokument.validering_id);
        avvik = await hentJson(`/v1/ehf/validering/${vid}/avvik`);
      }
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.ehf.detalj.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.ehf.feil.generell") }));
      return;
    }
    const skriver = harScope(ctx, "bestilling:opprett");
    const rader = valideringer.valideringer || [];
    const avvikrader = avvik.avvik || [];
    const deler = [
      el("h2", { text: t("ui.ehf.detalj.tittel") }),
      el("p", { class: "muted",
                text: `${dokument.ekstern_ref} · ${dokument.motpart}`
                      + ` · ${t(RETNINGSTEKST[dokument.retning]
                                || dokument.retning)}` }),
    ];
    if (!rader.length) {
      deler.push(el("p", { role: "alert",
        text: t("ui.ehf.uvalidert_varsel") }));
    } else {
      // DOMMEN MED SIN REGELSETTVERSJON, STORT OG ØVERST.
      deler.push(el("p", {
        text: t("ui.ehf.detalj.dom")
          .replace("{dom}", domTekst(dokument))
          .replace("{regelsett}", regelsettTekst({
            standard: dokument.standard, versjon: dokument.versjon,
            gyldig_naa: dokument.regelsett_gyldig_naa })) }));
      if (dokument.regelsett_gyldig_naa === false) {
        // DOMMEN BLE FELT UNDER EN REGEL SOM SIDEN HAR GÅTT UT.
        deler.push(el("p", { role: "alert",
          text: t("ui.ehf.dom_under_utlopt_varsel") }));
      }
      if (dokument.antall_uten_grunnlag > 0) {
        deler.push(el("p", { role: "alert",
          text: t("ui.ehf.uten_grunnlag_varsel")
            .replace("{n}",
                     String(dokument.antall_uten_grunnlag)) }));
      }
      deler.push(valideringstabell(rader));
      if (avvikrader.length) deler.push(avvikstabell(avvikrader));
    }

    if (skriver) {
      deler.push(valideringsskjema(ctx, last, dokument, regelsett,
                                   kvitter));
      if (avvikrader.length) {
        deler.push(rettingsskjema(ctx, last, avvikrader, kvitter));
        const uklare = avvikrader.filter(
          (a) => a.retting_id && !a.klar_til_signering);
        for (const a of uklare) {
          const knapp = el("button", { type: "button",
            text: t("ui.ehf.knapp.merk_klar")
              .replace("{kode}", a.regelkode) });
          knapp.addEventListener("click", async () => {
            knapp.disabled = true;
            try {
              await merkRettingKlar(a.retting_id);
            } catch (e) {
              knapp.disabled = false;
              if (e instanceof UautorisertFeil) {
                ctx.paaUautorisert(); return;
              }
              const m = e && e.status === 409
                ? t("ui.ehf.feil.urettet_formfeil")
                : t("ui.ehf.feil.generell");
              kvitter(m); meldLive(m);
              return;
            }
            kvitter(t("ui.ehf.skjema.klar_ok"));
            meldLive(t("ui.ehf.skjema.klar_ok"));
            await last();
          });
          deler.push(el("div", { class: "skjema-bunn" }, knapp,
            el("p", { class: "muted",
                      text: t("ui.ehf.klar_hjelp") })));
        }
      }
    }
    sett(node, ...deler);
  };
  return { node, aapne };
}


// VALIDERINGSSKJEMAET. BARE GYLDIGE REGELSETT TILBYS.
//
// Døra nekter mot et utløpt sett, og en knapp som alltid feiler er
// verre enn en valgmulighet som ikke finnes.
function valideringsskjema(ctx, last, dokument, regelsett, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const gyldige = regelsett.filter((r) => r.gyldig_naa === true
                                     && r.antall_regler > 0);
  const valg = el("select", { id: "ef-v-sett", name: "sett",
    required: true });
  valg.append(el("option", { value: "",
    text: t("ui.ehf.validering.velg_sett") }));
  for (const r of gyldige) {
    valg.append(el("option", { value: r.regelsett_id,
      text: `${t(STANDARDTEKST[r.standard] || r.standard)}`
            + ` ${r.versjon} (${r.antall_regler})` }));
  }
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.ehf.knapp.valider") });
  const vurder = () => { knapp.disabled = !valg.value; };
  valg.addEventListener("change", vurder);
  const deler = [];
  if (!gyldige.length) {
    deler.push(el("p", { role: "alert",
      text: t("ui.ehf.validering.ingen_gyldige") }));
  } else {
    deler.push(
      felt("ef-v-sett", "ui.ehf.validering.sett", valg,
           "ui.ehf.validering.sett_hjelp"),
      el("div", { class: "skjema-bunn" }, knapp));
  }
  skjema.append(...deler);
  // EGEN SUBMIT, fordi svaret er en DOM og ikke bare «ok».
  let idem = null;
  skjema.addEventListener("change", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled) return;
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    let svar;
    try {
      svar = await validerEhfdokument(dokument.dokument_id,
                                      valg.value, idem);
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      // 409 HAR TO GRUNNER HER (CodeRabbit): et utløpt regelsett,
      // og et dokument som ALT er dømt mot dette settet. Å alltid
      // si «utløpt» ville sendt den som gjentok en validering på
      // jakt etter et problem som ikke fantes.
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.ehf.feil.validering_avvist")
          : t("ui.ehf.feil.generell") }));
      return;
    }
    idem = null;
    knapp.disabled = false;
    // SVARET BÆRER ALLE FIRE TALLENE, og `uten_grunnlag` er med selv
    // når den er null: en leser som bare ser «2 feil» vet ikke om
    // resten var grønn eller udømt.
    const m = t("ui.ehf.skjema.validert")
      .replace("{regler}", String(svar.antall_regler))
      .replace("{feil}", String(svar.antall_feil))
      .replace("{advarsler}", String(svar.antall_advarsler))
      .replace("{utenfor}", String(svar.antall_uten_grunnlag));
    kvitter(m); meldLive(m);
    await last();
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.ehf.validering.tittel") }),
    el("p", { class: "muted",
              text: t("ui.ehf.validering.hvorfor") }),
    skjema, utfall);
}


export function visEhf(hoved, ctx) {
  const hode = () => flateHode(t("ui.ehf.tittel"),
    t("ui.ehf.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenDok = null;
  let apentSett = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/ehf"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const regelsett = d.regelsett || [];
      const dokumenter = d.dokumenter || [];
      const regel = regelpanel(ctx, last, kvitter,
                               (id) => { apentSett = id; });
      const detalj = dokumentpanel(ctx, last, kvitter,
                                   (id) => { apenDok = id; },
                                   regelsett);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.ehf.oversikt.tittel") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT.
        el("p", { class: "muted",
                  text: t("ui.ehf.oversikt.hvorfor") }));

      const settseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.ehf.regelsett.tittel") }));
      if (!regelsett.length) {
        settseksjon.append(el("p", { role: "alert",
          text: t("ui.ehf.regelsett.ingen") }));
      } else {
        settseksjon.append(regelsettabell(regelsett, regel.aapne));
      }

      const dokseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.ehf.dokumenter.tittel") }));
      if (!dokumenter.length) {
        dokseksjon.append(el("p", { class: "muted",
          text: t("ui.ehf.dokumenter.ingen") }));
      } else {
        dokseksjon.append(dokumenttabell(dokumenter, detalj.aapne));
      }

      const deler = [oversikt, settseksjon, regel.node, dokseksjon,
                     detalj.node, funnseksjon(ctx, last, kvitter)];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(regelsettskjema(ctx, last, kvitter),
                   dokumentskjema(ctx, last, kvitter),
                   kravskjema(ctx, last, d.krav, kvitter));
      }
      sett(kropp, ...deler);
      if (apentSett) {
        const rad = regelsett.find(
          (x) => x.regelsett_id === apentSett);
        if (rad) regel.aapne(rad); else apentSett = null;
      }
      if (apenDok) {
        const rad = dokumenter.find((x) => x.dokument_id === apenDok);
        if (rad) detalj.aapne(rad); else apenDok = null;
      }
    });
  last();
}
