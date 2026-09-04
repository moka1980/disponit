// Tilskudds- og støtteordningsvakten (M-51 v1) — ESTIMATET, IKKE
// SØKNADEN.
//
// FLATENS VIKTIGSTE JOBB er å vise et estimat som et ESTIMAT.
//
// Et tilskuddsestimat er et TALL EN BEDRIFT PLANLEGGER ETTER. Sier vi
// «dere kan få 400 000», og bedriften ansetter på det grunnlaget, er
// avstanden mellom estimat og lovnad ikke akademisk — den er
// lønnsutbetalinger.
//
// DERFOR VISES ALDRI SUMMEN ALENE. Hvert sted et estimat står, står
// spennet ved siden av: «360 000 (288 000–432 000)». Ett tall ville
// vært en lovnad; et intervall er en beskjed man kan planlegge etter.
//
// OG FORUTSETNINGENE ER IKKE EN FOTNOTE. De står i sin egen tabell med
// sin KONSEKVENS — «faller bort helt» og «reduseres med ca. 30 %» er
// to helt forskjellige beskjeder — og et estimat uten dem kan ikke
// ferdigstilles i det hele tatt.
//
// DET FINNES INGEN «SEND SØKNAD»-KNAPP. 119 har ingen «sendt»-kolonne
// å skrive til, så knappen kan ikke finnes.
//
// OG DET FINNES INGEN VEI TIL Å SETTE ET BELØP DIREKTE. Hver post
// velges fra en kildepostliste — ikke fordi flaten sjekker det, men
// fordi `tilskuddsestimat` ikke har en beløpskolonne. Summen ER
// summen av postene.
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, ferdigstillEstimat, hentJson, leggTilForutsetning,
  leggTilEstimatpost, lukkTilskuddsfunn, nyIdempotensnokkel,
  opprettTilskuddsestimat, registrerKildepost, registrerOrdning,
  settOrdningAktiv, settTilskuddskrav,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// LUKKEDE SETT, speilet fra 119.
export const SYSTEMER = ["regnskap", "lonn", "timeforing", "faktura",
                         "manuell"];
export const FORUTSETNINGSARTER = ["regelverk", "regnskapstall",
                                   "bemanning", "aktivitet", "annet"];

const SYSTEMTEKST = {
  regnskap: "ui.tilskudd.system.regnskap",
  lonn: "ui.tilskudd.system.lonn",
  timeforing: "ui.tilskudd.system.timeforing",
  faktura: "ui.tilskudd.system.faktura",
  manuell: "ui.tilskudd.system.manuell",
};
const ARTTEKST = {
  regelverk: "ui.tilskudd.art.regelverk",
  regnskapstall: "ui.tilskudd.art.regnskapstall",
  bemanning: "ui.tilskudd.art.bemanning",
  aktivitet: "ui.tilskudd.art.aktivitet",
  annet: "ui.tilskudd.art.annet",
};
const MERKE = {
  frist_naermer_seg: "ui.tilskudd.merke_frist_naer",
  frist_passert: "ui.tilskudd.merke_frist_passert",
  estimat_uten_poster: "ui.tilskudd.merke_uten_poster",
  estimat_over_ordningstak: "ui.tilskudd.merke_over_tak",
  utdatert_kildepost: "ui.tilskudd.merke_utdatert_kilde",
  ingen_estimat: "ui.tilskudd.merke_uten_estimat",
  ingen_krav: "ui.tilskudd.merke_uten_krav",
};

// ØRE ↔ FELTVERDI, MED HELTALLSMATEMATIKK BEGGE VEIER (118s lærdom).
//
// `Math.floor(ore / 100)` og `Number(kr) * 100` er ikke en rundtur:
// 123456 øre vises som 1234 kr og lagres tilbake som 123400. Femtiseks
// øre forsvinner — stille, på en lagring brukeren gjorde av en helt
// annen grunn. Her går begge veier gjennom strenger og heltall.
export function oreTilFelt(ore) {
  if (ore === null || ore === undefined) return "";
  const n = BigInt(ore);
  const neg = n < 0n;
  const a = (neg ? -n : n).toString().padStart(3, "0");
  return `${neg ? "-" : ""}${a.slice(0, -2)}.${a.slice(-2)}`;
}

export function feltTilOre(verdi) {
  const s = String(verdi === null || verdi === undefined ? "" : verdi)
    .trim().replace(",", ".");
  if (!s) return null;
  const m = /^(-?)(\d*)(?:\.(\d{0,2}))?$/.exec(s);
  if (!m) return null;
  const kr = m[2] || "0";
  const ore = (m[3] || "").padEnd(2, "0");
  const tall = BigInt(kr) * 100n + BigInt(ore);
  return Number(m[1] === "-" ? -tall : tall);
}

export function oreTekst(ore) {
  if (ore === null || ore === undefined) return "–";
  const n = BigInt(ore);
  const neg = n < 0n;
  const a = (neg ? -n : n).toString().padStart(3, "0");
  return `${neg ? "-" : ""}${a.slice(0, -2)},${a.slice(-2)}`;
}

// SUMMEN MED SPENNET, ALDRI SUMMEN ALENE. Se filhodet: ett tall er en
// lovnad, et intervall er et estimat.
export function estimatTekst(rad) {
  if (!rad || rad.sum_ore === null || rad.sum_ore === undefined) {
    return t("ui.tilskudd.uten_estimat");
  }
  if (!rad.antall_poster) return t("ui.tilskudd.uten_poster");
  return t("ui.tilskudd.sum_med_spenn")
    .replace("{sum}", oreTekst(rad.sum_ore))
    .replace("{nedre}", oreTekst(rad.nedre_ore))
    .replace("{ovre}", oreTekst(rad.ovre_ore));
}

// FRISTEN MED RETNING (118s form): «om 3 døgn» og «3 døgn siden» er
// ikke samme sak.
export function fristTekst(dogn) {
  if (dogn === null || dogn === undefined) return "–";
  if (dogn < 0) {
    return t("ui.tilskudd.frist_passert").replace("{n}", String(-dogn));
  }
  if (dogn === 0) return t("ui.tilskudd.frist_i_dag");
  return t("ui.tilskudd.frist_om").replace("{n}", String(dogn));
}

// «INGEN FORUTSETNINGER» ER IKKE EN TOM CELLE — det er grunnen til at
// estimatet ikke kan ferdigstilles, og det må kunne leses.
export function tilstandTekst(rad) {
  if (!rad.estimat_id) return t("ui.tilskudd.uten_estimat");
  if (!rad.antall_poster) return t("ui.tilskudd.uten_poster");
  if (!rad.antall_forutsetninger) {
    return t("ui.tilskudd.uten_forutsetninger");
  }
  return rad.klar ? t("ui.tilskudd.klart")
                  : t("ui.tilskudd.under_arbeid");
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
          ? t("ui.tilskudd.feil.tilstand")
          : t("ui.tilskudd.feil.generell") }));
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


// SAMMENDRAGET. Summen av de KLARE estimatene står først — det er
// tallet en bedrift planlegger etter — og alltid med et forbehold om
// at det er estimater.
export function sammendrag(s) {
  const p = el("p");
  p.append(el("strong", {
    text: t("ui.tilskudd.sum_klare")
      .replace("{sum}", oreTekst(s.sum_klare_ore ?? 0))
      .replace("{n}", String(s.klare ?? 0)) }));
  p.append(" ", el("span", {
    text: t("ui.tilskudd.tellinger")
      .replace("{ordninger}", String(s.ordninger ?? 0))
      .replace("{aktive}", String(s.aktive ?? 0))
      .replace("{estimat}", String(s.med_estimat ?? 0)) }));
  if (s.naermeste_frist) {
    p.append(" ", el("span", { class: "muted",
      text: t("ui.tilskudd.naermeste_frist")
        .replace("{dato}", s.naermeste_frist.slice(0, 10)) }));
  }
  if (s.utdaterte_kildeposter > 0) {
    // ET REGNSKAPSTALL FRA I FJOR ER IKKE GRUNNLAG FOR ÅRETS SØKNAD.
    p.append(" ", el("strong", {
      text: t("ui.tilskudd.utdaterte_kildeposter")
        .replace("{n}", String(s.utdaterte_kildeposter)) }));
  }
  if (s.apne_funn > 0) {
    p.append(" ", el("strong", {
      text: t("ui.tilskudd.apne_funn")
        .replace("{n}", String(s.apne_funn)) }));
  }
  if (!s.har_krav) {
    p.append(" ", el("strong", { text: t("ui.tilskudd.ingen_krav") }));
  }
  if (s.vist < s.ordninger) {
    p.append(" ", el("strong", {
      text: t("ui.tilskudd.avkortet")
        .replace("{vist}", String(s.vist)) }));
  }
  return p;
}


export function ordningTabell(ordninger, apne) {
  const tbody = el("tbody");
  for (const o of ordninger) {
    const knapp = el("button", { type: "button",
      text: t("ui.tilskudd.knapp.apne") });
    knapp.addEventListener("click", () => apne(o));
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: o.navn }),
      el("td", { text: o.forvalter }),
      el("td", { text: o.regelverksversjon }),
      el("td", { text: o.soknadsfrist.slice(0, 10) }),
      el("td", { text: fristTekst(o.dogn_til_frist) }),
      // ESTIMATET MED SPENNET, aldri summen alene.
      el("td", { text: estimatTekst(o) }),
      el("td", { text: oreTekst(o.maks_belop_ore) }),
      el("td", { text: tilstandTekst(o) }),
      el("td", { class: "tall", text: String(o.apne_funn ?? 0) }),
      el("td", { text: o.aktiv ? t("ui.tilskudd.ja")
                               : t("ui.tilskudd.nei") }),
      el("td", {}, knapp)));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.tilskudd.liste.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.tilskudd.kol.navn") }),
        el("th", { scope: "col",
                   text: t("ui.tilskudd.kol.forvalter") }),
        el("th", { scope: "col",
                   text: t("ui.tilskudd.kol.regelverk") }),
        el("th", { scope: "col", text: t("ui.tilskudd.kol.frist") }),
        el("th", { scope: "col",
                   text: t("ui.tilskudd.kol.frist_om") }),
        el("th", { scope: "col", text: t("ui.tilskudd.kol.estimat") }),
        el("th", { scope: "col", text: t("ui.tilskudd.kol.tak") }),
        el("th", { scope: "col", text: t("ui.tilskudd.kol.tilstand") }),
        el("th", { scope: "col", text: t("ui.tilskudd.kol.funn") }),
        el("th", { scope: "col", text: t("ui.tilskudd.kol.aktiv") }),
        el("th", { scope: "col",
                   text: t("ui.tilskudd.kol.handling") }))),
      tbody));
}


// POSTTABELLEN. HVER RAD VISER KILDEPOSTENS EGET BELØP VED SIDEN AV
// ANDELEN, så «andel 360 000» kan etterprøves uten å slå opp noe
// annet sted.
export function postTabell(poster) {
  const tbody = el("tbody");
  for (const p of poster) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: p.ekstern_ref }),
      el("td", { text: t(SYSTEMTEKST[p.system] || p.system) }),
      el("td", { text: p.beskrivelse }),
      el("td", { class: "tall", text: oreTekst(p.kilde_belop_ore) }),
      el("td", { class: "tall", text: oreTekst(p.andel_ore) }),
      el("td", { text: p.begrunnelse }),
      el("td", { text: `${p.periode_fra} – ${p.periode_til}` })));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.tilskudd.poster.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.tilskudd.kol.ref") }),
        el("th", { scope: "col", text: t("ui.tilskudd.kol.system") }),
        el("th", { scope: "col",
                   text: t("ui.tilskudd.kol.beskrivelse") }),
        el("th", { scope: "col",
                   text: t("ui.tilskudd.kol.kildebelop") }),
        el("th", { scope: "col", text: t("ui.tilskudd.kol.andel") }),
        el("th", { scope: "col",
                   text: t("ui.tilskudd.kol.begrunnelse") }),
        el("th", { scope: "col", text: t("ui.tilskudd.kol.periode") }))),
      tbody));
}


// FORUTSETNINGSTABELLEN. KONSEKVENSEN STÅR I SIN EGEN KOLONNE — en
// forutsetning uten konsekvens er en ansvarsfraskrivelse, ikke en
// opplysning.
export function forutsetningTabell(forutsetninger) {
  const tbody = el("tbody");
  for (const f of forutsetninger) {
    tbody.append(el("tr", {},
      el("th", { scope: "row",
                 text: t(ARTTEKST[f.art] || f.art) }),
      el("td", { text: f.tekst }),
      el("td", { text: f.konsekvens }),
      el("td", { text: f.registrert_av })));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.tilskudd.forutsetninger.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.tilskudd.kol.art") }),
        el("th", { scope: "col",
                   text: t("ui.tilskudd.kol.forutsetning") }),
        el("th", { scope: "col",
                   text: t("ui.tilskudd.kol.konsekvens") }),
        el("th", { scope: "col", text: t("ui.tilskudd.kol.av") }))),
      tbody));
}


export function kildepostTabell(kildeposter) {
  const tbody = el("tbody");
  for (const k of kildeposter) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: k.ekstern_ref }),
      el("td", { text: t(SYSTEMTEKST[k.system] || k.system) }),
      el("td", { text: k.beskrivelse }),
      el("td", { class: "tall", text: oreTekst(k.belop_ore) }),
      el("td", { text: `${k.periode_fra} – ${k.periode_til}` }),
      // FERSK HAR TRE TILSTANDER: `null` betyr at tenanten mangler
      // terskler, så vinduet ikke kan regnes.
      el("td", { text: k.fersk === null || k.fersk === undefined
                   ? t("ui.tilskudd.ukjent")
                   : (k.fersk ? t("ui.tilskudd.ja")
                              : t("ui.tilskudd.nei")) }),
      el("td", { class: "tall", text: String(k.brukt_i_poster) })));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.tilskudd.kildeposter.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.tilskudd.kol.ref") }),
        el("th", { scope: "col", text: t("ui.tilskudd.kol.system") }),
        el("th", { scope: "col",
                   text: t("ui.tilskudd.kol.beskrivelse") }),
        el("th", { scope: "col", text: t("ui.tilskudd.kol.belop") }),
        el("th", { scope: "col", text: t("ui.tilskudd.kol.periode") }),
        el("th", { scope: "col", text: t("ui.tilskudd.kol.fersk") }),
        el("th", { scope: "col", text: t("ui.tilskudd.kol.brukt") }))),
      tbody));
}


// POSTSKJEMAET. KILDEPOSTEN VELGES FRA EN LISTE, og lista viser
// beløpet — så den som setter andelen ser hva den kan være en andel
// AV. Det finnes ingen «skriv et beløp»-vei, fordi
// `tilskuddsestimat` ikke har en beløpskolonne.
function postSkjema(ctx, last, estimatId, kildeposter, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const ferske = kildeposter.filter((k) => k.fersk === true);
  const kilde = el("select", { id: "ti-p-kilde", name: "kilde",
                               required: true });
  kilde.append(el("option", { value: "",
    text: t("ui.tilskudd.post.velg_kilde") }));
  for (const k of ferske) {
    kilde.append(el("option", { value: k.kildepost_id,
      text: `${k.ekstern_ref} — ${k.beskrivelse}`
            + ` (${oreTekst(k.belop_ore)})` }));
  }
  const andel = el("input", { id: "ti-p-andel", name: "andel",
    type: "number", required: true, step: "0.01", min: "0" });
  const begrunnelse = el("input", { id: "ti-p-begrunnelse",
    name: "begrunnelse", type: "text", required: true,
    minlength: "4", maxlength: "4000" });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.tilskudd.knapp.lagre_post") });
  const vurder = () => {
    knapp.disabled = !kilde.value || feltTilOre(andel.value) === null
      || begrunnelse.value.trim().length < 4;
  };
  for (const k of [kilde, andel, begrunnelse]) {
    k.addEventListener("change", vurder);
    k.addEventListener("input", vurder);
  }
  const deler = [];
  if (!ferske.length) {
    deler.push(el("p", { role: "alert",
      text: t("ui.tilskudd.post.ingen_ferske_kilder") }));
  } else {
    deler.push(
      felt("ti-p-kilde", "ui.tilskudd.post.kilde", kilde,
           "ui.tilskudd.post.kilde_hjelp"),
      felt("ti-p-andel", "ui.tilskudd.post.andel_kr", andel,
           "ui.tilskudd.post.andel_hjelp"),
      felt("ti-p-begrunnelse", "ui.tilskudd.post.begrunnelse",
           begrunnelse, "ui.tilskudd.post.begrunnelse_hjelp"),
      el("div", { class: "skjema-bunn" }, knapp));
  }
  skjema.append(...deler);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.tilskudd.skjema.post_ok",
    tilbakestill: () => {
      kilde.value = ""; andel.value = ""; begrunnelse.value = "";
      knapp.disabled = true;
    },
    send: (idem) => leggTilEstimatpost(estimatId, {
      kildepost_id: kilde.value,
      andel_ore: feltTilOre(andel.value),
      begrunnelse: begrunnelse.value.trim(),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.tilskudd.post.tittel") }),
    el("p", { class: "muted", text: t("ui.tilskudd.post.hvorfor") }),
    skjema, utfall);
}


// FORUTSETNINGSSKJEMAET. KONSEKVENSEN ER PÅKREVD, og hjelpeteksten
// sier hvorfor: «faller bort helt» og «reduseres med ca. 30 %» er to
// helt forskjellige beskjeder til den som planlegger.
function forutsetningSkjema(ctx, last, estimatId, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const art = el("select", { id: "ti-f-art", name: "art",
                             required: true });
  art.append(el("option", { value: "",
    text: t("ui.tilskudd.forutsetning.velg_art") }));
  for (const a of FORUTSETNINGSARTER) {
    art.append(el("option", { value: a, text: t(ARTTEKST[a]) }));
  }
  const tekst = el("input", { id: "ti-f-tekst", name: "tekst",
    type: "text", required: true, minlength: "8", maxlength: "4000" });
  const konsekvens = el("input", { id: "ti-f-konsekvens",
    name: "konsekvens", type: "text", required: true,
    minlength: "8", maxlength: "4000" });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.tilskudd.knapp.lagre_forutsetning") });
  const vurder = () => {
    knapp.disabled = !art.value || tekst.value.trim().length < 8
      || konsekvens.value.trim().length < 8;
  };
  for (const k of [art, tekst, konsekvens]) {
    k.addEventListener("change", vurder);
    k.addEventListener("input", vurder);
  }
  skjema.append(
    felt("ti-f-art", "ui.tilskudd.forutsetning.art", art, null),
    felt("ti-f-tekst", "ui.tilskudd.forutsetning.tekst", tekst,
         "ui.tilskudd.forutsetning.tekst_hjelp"),
    felt("ti-f-konsekvens", "ui.tilskudd.forutsetning.konsekvens",
         konsekvens, "ui.tilskudd.forutsetning.konsekvens_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.tilskudd.skjema.forutsetning_ok",
    tilbakestill: () => {
      art.value = ""; tekst.value = ""; konsekvens.value = "";
      knapp.disabled = true;
    },
    send: (idem) => leggTilForutsetning(estimatId, {
      art: art.value,
      tekst: tekst.value.trim(),
      konsekvens: konsekvens.value.trim(),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.tilskudd.forutsetning.tittel") }),
    el("p", { class: "muted",
              text: t("ui.tilskudd.forutsetning.hvorfor") }),
    skjema, utfall);
}


function kravSkjema(ctx, last, krav, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const frist = el("input", { id: "ti-k-frist", name: "frist",
    type: "number", required: true, step: "1", min: "1", max: "365" });
  const kilde = el("input", { id: "ti-k-kilde", name: "kilde",
    type: "number", required: true, step: "1", min: "1", max: "3650" });
  const usikkerhet = el("input", { id: "ti-k-usikkerhet",
    name: "usikkerhet", type: "number", required: true, step: "1",
    min: "0", max: "100" });
  if (krav) {
    frist.value = String(krav.frist_varsel_dogn);
    kilde.value = String(krav.kildepost_gyldig_dogn);
    usikkerhet.value = String(krav.usikkerhet_prosent);
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.tilskudd.knapp.lagre_krav") });
  skjema.append(
    felt("ti-k-frist", "ui.tilskudd.krav.frist", frist,
         "ui.tilskudd.krav.frist_hjelp"),
    felt("ti-k-kilde", "ui.tilskudd.krav.kilde_gyldig", kilde,
         "ui.tilskudd.krav.kilde_hjelp"),
    felt("ti-k-usikkerhet", "ui.tilskudd.krav.usikkerhet",
         usikkerhet, "ui.tilskudd.krav.usikkerhet_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.tilskudd.skjema.krav_ok",
    send: (idem) => settTilskuddskrav({
      frist_varsel_dogn: Number(frist.value),
      kildepost_gyldig_dogn: Number(kilde.value),
      usikkerhet_prosent: Number(usikkerhet.value),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.tilskudd.krav.tittel") }), skjema, utfall);
}


function ordningSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const kode = el("input", { id: "ti-o-kode", name: "kode",
    type: "text", required: true, maxlength: "200" });
  const navn = el("input", { id: "ti-o-navn", name: "navn",
    type: "text", required: true, maxlength: "500" });
  const forvalter = el("input", { id: "ti-o-forvalter",
    name: "forvalter", type: "text", required: true, maxlength: "500" });
  const versjon = el("input", { id: "ti-o-versjon", name: "versjon",
    type: "text", required: true, maxlength: "200" });
  const sum = el("input", { id: "ti-o-sum", name: "sum", type: "text",
    required: true, pattern: "[0-9a-fA-F]{64}", maxlength: "64" });
  const maks = el("input", { id: "ti-o-maks", name: "maks",
    type: "number", step: "0.01", min: "0" });
  const sats = el("input", { id: "ti-o-sats", name: "sats",
    type: "number", step: "1", min: "0", max: "100" });
  const frist = el("input", { id: "ti-o-frist", name: "frist",
    type: "datetime-local", required: true });
  const knapp = el("button", { type: "submit",
    text: t("ui.tilskudd.knapp.registrer_ordning") });
  skjema.append(
    felt("ti-o-kode", "ui.tilskudd.ny.kode", kode, null),
    felt("ti-o-navn", "ui.tilskudd.ny.navn", navn, null),
    felt("ti-o-forvalter", "ui.tilskudd.ny.forvalter", forvalter,
         null),
    felt("ti-o-versjon", "ui.tilskudd.ny.regelverk", versjon,
         "ui.tilskudd.ny.regelverk_hjelp"),
    felt("ti-o-sum", "ui.tilskudd.ny.sum", sum,
         "ui.tilskudd.ny.sum_hjelp"),
    // TAKET OG SATSEN ER VALGFRIE, og hjelpeteksten sier hvorfor:
    // en ordning uten tak har ikke et tak på null.
    felt("ti-o-maks", "ui.tilskudd.ny.maks_kr", maks,
         "ui.tilskudd.ny.maks_hjelp"),
    felt("ti-o-sats", "ui.tilskudd.ny.sats", sats, null),
    felt("ti-o-frist", "ui.tilskudd.ny.frist", frist, null),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.tilskudd.skjema.ordning_ok",
    tilbakestill: () => {
      kode.value = ""; navn.value = ""; forvalter.value = "";
      versjon.value = ""; sum.value = ""; maks.value = "";
      sats.value = ""; frist.value = "";
    },
    send: (idem) => registrerOrdning({
      ordningskode: kode.value.trim(),
      navn: navn.value.trim(),
      forvalter: forvalter.value.trim(),
      regelverksversjon: versjon.value.trim(),
      regelverk_sha256: sum.value.trim().toLowerCase(),
      maks_belop_ore: feltTilOre(maks.value),
      sats_prosent: sats.value === "" ? null : Number(sats.value),
      soknadsfrist: new Date(frist.value).toISOString(),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.tilskudd.ny.tittel") }),
    el("p", { class: "muted", text: t("ui.tilskudd.ny.hvorfor") }),
    skjema, utfall);
}


function kildepostSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const system = el("select", { id: "ti-kp-system", name: "system",
                                required: true });
  system.append(el("option", { value: "",
    text: t("ui.tilskudd.kildepost.velg_system") }));
  for (const s of SYSTEMER) {
    system.append(el("option", { value: s, text: t(SYSTEMTEKST[s]) }));
  }
  const ref = el("input", { id: "ti-kp-ref", name: "ref",
    type: "text", required: true, maxlength: "200" });
  const beskrivelse = el("input", { id: "ti-kp-beskrivelse",
    name: "beskrivelse", type: "text", required: true,
    minlength: "4", maxlength: "4000" });
  const belop = el("input", { id: "ti-kp-belop", name: "belop",
    type: "number", required: true, step: "0.01", min: "0" });
  const fra = el("input", { id: "ti-kp-fra", name: "fra",
    type: "date", required: true });
  const til = el("input", { id: "ti-kp-til", name: "til",
    type: "date", required: true });
  const knapp = el("button", { type: "submit",
    text: t("ui.tilskudd.knapp.registrer_kildepost") });
  skjema.append(
    felt("ti-kp-system", "ui.tilskudd.kildepost.system", system,
         "ui.tilskudd.kildepost.system_hjelp"),
    felt("ti-kp-ref", "ui.tilskudd.kildepost.ref", ref,
         "ui.tilskudd.kildepost.ref_hjelp"),
    felt("ti-kp-beskrivelse", "ui.tilskudd.kildepost.beskrivelse",
         beskrivelse, null),
    felt("ti-kp-belop", "ui.tilskudd.kildepost.belop_kr", belop, null),
    felt("ti-kp-fra", "ui.tilskudd.kildepost.fra", fra, null),
    felt("ti-kp-til", "ui.tilskudd.kildepost.til", til,
         "ui.tilskudd.kildepost.til_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.tilskudd.skjema.kildepost_ok",
    tilbakestill: () => {
      system.value = ""; ref.value = ""; beskrivelse.value = "";
      belop.value = ""; fra.value = ""; til.value = "";
    },
    send: (idem) => registrerKildepost({
      system: system.value,
      ekstern_ref: ref.value.trim(),
      beskrivelse: beskrivelse.value.trim(),
      belop_ore: feltTilOre(belop.value),
      periode_fra: fra.value,
      periode_til: til.value,
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.tilskudd.kildepost.tittel") }),
    el("p", { class: "muted",
              text: t("ui.tilskudd.kildepost.hvorfor") }),
    skjema, utfall);
}


// DETALJPANELET. Postene og forutsetningene står sammen, fordi de
// SAMMEN er estimatet: postene sier hva tallet er, forutsetningene sier
// hva det hviler på. Ett uten det andre er ikke et estimat.
function detaljpanel(ctx, last, kvitter, settApen, kildeposter) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  const apne = async (ordning) => {
    settApen(ordning.ordning_id);
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.tilskudd.detalj.tittel") }),
         el("p", { class: "muted", text: t("ui.tilskudd.laster") }));
    let estimater = { estimater: [] };
    let poster = { poster: [] };
    let forutsetninger = { forutsetninger: [] };
    try {
      const id = encodeURIComponent(ordning.ordning_id);
      estimater = await hentJson(`/v1/tilskudd/${id}/estimater`);
      const nyeste = (estimater.estimater || [])[0];
      if (nyeste) {
        const eid = encodeURIComponent(nyeste.estimat_id);
        poster = await hentJson(`/v1/tilskudd/estimat/${eid}/poster`);
        forutsetninger = await hentJson(
          `/v1/tilskudd/estimat/${eid}/forutsetninger`);
      }
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.tilskudd.detalj.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.tilskudd.feil.generell") }));
      return;
    }
    const nyeste = (estimater.estimater || [])[0];
    const skriver = harScope(ctx, "bestilling:opprett");
    const eTbody = el("tbody");
    for (const e of estimater.estimater || []) {
      eTbody.append(el("tr", {},
        el("th", { scope: "row", text: String(e.versjon) }),
        el("td", { text: `${e.periode_fra} – ${e.periode_til}` }),
        el("td", { class: "tall", text: oreTekst(e.sum_ore) }),
        el("td", { class: "tall", text: String(e.antall_poster) }),
        el("td", { class: "tall",
                   text: String(e.antall_forutsetninger) }),
        el("td", { text: `${e.usikkerhet_prosent} %` }),
        el("td", { text: e.klar_til_gjennomgang
                     ? t("ui.tilskudd.klart")
                     : t("ui.tilskudd.under_arbeid") }),
        el("td", { text: e.klar_av || "–" })));
    }
    const deler = [
      el("h2", { text: t("ui.tilskudd.detalj.tittel") }),
      el("p", { class: "muted",
                text: `${ordning.navn} · ${ordning.forvalter}`
                      + ` · ${ordning.regelverksversjon}` }),
      el("div", { class: "tablewrap" }, el("table", {},
        el("caption", { text: t("ui.tilskudd.estimater.tittel") }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col",
                     text: t("ui.tilskudd.kol.versjon") }),
          el("th", { scope: "col",
                     text: t("ui.tilskudd.kol.periode") }),
          el("th", { scope: "col", text: t("ui.tilskudd.kol.sum") }),
          el("th", { scope: "col",
                     text: t("ui.tilskudd.kol.poster") }),
          el("th", { scope: "col",
                     text: t("ui.tilskudd.kol.forutsetninger") }),
          el("th", { scope: "col",
                     text: t("ui.tilskudd.kol.usikkerhet") }),
          el("th", { scope: "col",
                     text: t("ui.tilskudd.kol.tilstand") }),
          el("th", { scope: "col",
                     text: t("ui.tilskudd.kol.klar_av") }))),
        eTbody)),
    ];
    if (nyeste) {
      // SUMMEN MED SPENNET, STORT OG ØVERST. Det er dette tallet noen
      // planlegger etter, og det skal aldri stå alene.
      deler.push(el("p", {
        text: t("ui.tilskudd.detalj.estimat")
          .replace("{tekst}", estimatTekst({
            sum_ore: nyeste.sum_ore,
            nedre_ore: nyeste.sum_ore
              - Math.trunc(nyeste.sum_ore * nyeste.usikkerhet_prosent
                           / 100),
            ovre_ore: nyeste.sum_ore
              + Math.trunc(nyeste.sum_ore * nyeste.usikkerhet_prosent
                           / 100),
            antall_poster: nyeste.antall_poster,
          })) }));
      deler.push(postTabell(poster.poster || []));
      if (!(forutsetninger.forutsetninger || []).length) {
        // FRAVÆRET SIES HØYT. Det er grunnen til at estimatet ikke kan
        // ferdigstilles, ikke en tom tabell.
        deler.push(el("p", { role: "alert",
          text: t("ui.tilskudd.uten_forutsetninger_varsel") }));
      } else {
        deler.push(forutsetningTabell(forutsetninger.forutsetninger));
      }
    }
    if (skriver) {
      if (!nyeste || nyeste.klar_til_gjennomgang) {
        deler.push(estimatSkjema(ctx, last, ordning.ordning_id,
                                 kvitter));
      } else {
        deler.push(postSkjema(ctx, last, nyeste.estimat_id,
                              kildeposter, kvitter),
                   forutsetningSkjema(ctx, last, nyeste.estimat_id,
                                      kvitter));
        const ferdig = el("button", { type: "button",
          text: t("ui.tilskudd.knapp.ferdigstill") });
        ferdig.addEventListener("click", async () => {
          ferdig.disabled = true;
          let svar;
          try {
            svar = await ferdigstillEstimat(nyeste.estimat_id);
          } catch (e) {
            ferdig.disabled = false;
            if (e instanceof UautorisertFeil) {
              ctx.paaUautorisert(); return;
            }
            const m = e && e.status === 409
              ? t("ui.tilskudd.feil.uten_forutsetninger")
              : t("ui.tilskudd.feil.generell");
            kvitter(m); meldLive(m);
            return;
          }
          // SVARET BÆRER SPENNET. Den som ferdigstiller skal se hva
          // estimatet faktisk sier, ikke bare at det gikk bra.
          const m = t("ui.tilskudd.skjema.ferdigstilt")
            .replace("{sum}", oreTekst(svar.sum_ore))
            .replace("{nedre}", oreTekst(svar.nedre_ore))
            .replace("{ovre}", oreTekst(svar.ovre_ore));
          kvitter(m); meldLive(m);
          await last();
        });
        deler.push(el("div", { class: "skjema-bunn" }, ferdig,
          el("p", { class: "muted",
                    text: t("ui.tilskudd.ferdigstill_hjelp") })));
      }
      const aktiv = el("button", { type: "button",
        text: ordning.aktiv ? t("ui.tilskudd.knapp.deaktiver")
                            : t("ui.tilskudd.knapp.aktiver") });
      aktiv.addEventListener("click", async () => {
        aktiv.disabled = true;
        try {
          await settOrdningAktiv(ordning.ordning_id, !ordning.aktiv);
        } catch (e) {
          aktiv.disabled = false;
          if (e instanceof UautorisertFeil) {
            ctx.paaUautorisert(); return;
          }
          const m = t("ui.tilskudd.feil.generell");
          kvitter(m); meldLive(m);
          return;
        }
        kvitter(t("ui.tilskudd.skjema.aktiv_ok"));
        meldLive(t("ui.tilskudd.skjema.aktiv_ok"));
        await last();
      });
      deler.push(el("div", { class: "skjema-bunn" }, aktiv));
    }
    sett(node, ...deler);
  };
  return { node, apne };
}


function estimatSkjema(ctx, last, ordningId, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const fra = el("input", { id: "ti-e-fra", name: "fra",
    type: "date", required: true });
  const til = el("input", { id: "ti-e-til", name: "til",
    type: "date", required: true });
  const knapp = el("button", { type: "submit",
    text: t("ui.tilskudd.knapp.nytt_estimat") });
  skjema.append(
    felt("ti-e-fra", "ui.tilskudd.estimat.fra", fra, null),
    felt("ti-e-til", "ui.tilskudd.estimat.til", til,
         "ui.tilskudd.estimat.periode_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.tilskudd.skjema.estimat_ok",
    tilbakestill: () => { fra.value = ""; til.value = ""; },
    send: (idem) => opprettTilskuddsestimat(ordningId, {
      periode_fra: fra.value, periode_til: til.value,
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.tilskudd.estimat.tittel") }), skjema,
    utfall);
}


// FUNNSEKSJONEN. `estimat_over_ordningstak` KAN IKKE LUKKES — døra
// nekter det, av samme grunn som M-46s udekkede absolutte krav (118)
// og M-49s bekreftede treff (117).
function funnseksjon(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.tilskudd.funn.tittel") }),
    el("p", { class: "muted", text: t("ui.tilskudd.laster") }));
  (async () => {
    let d;
    try {
      d = await hentJson("/v1/tilskudd/funn");
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.tilskudd.funn.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.tilskudd.feil.generell") }));
      return;
    }
    const funn = d.funn || [];
    const deler = [el("h2", { text: t("ui.tilskudd.funn.tittel") })];
    if (!funn.length) {
      deler.push(el("p", { class: "muted",
        text: t("ui.tilskudd.funn.ingen") }));
    } else {
      const tbody = el("tbody");
      for (const f of funn) {
        tbody.append(el("tr", {},
          el("th", { scope: "row", text: f.navn }),
          el("td", { text: t(MERKE[f.funntype] || f.funntype) }),
          el("td", { text: f.soknadsfrist.slice(0, 10) }),
          // SUMMEN STÅR PÅ FUNNET: «over taket» uten å si hvor mye er
          // en beskjed man ikke kan handle på.
          el("td", { class: "tall", text: oreTekst(f.sum_ore) }),
          el("td", { text: f.detalj || "–" })));
      }
      deler.push(el("div", { class: "tablewrap" }, el("table", {},
        el("caption", { text: t("ui.tilskudd.funn.tittel") }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col", text: t("ui.tilskudd.kol.navn") }),
          el("th", { scope: "col",
                     text: t("ui.tilskudd.kol.funntype") }),
          el("th", { scope: "col", text: t("ui.tilskudd.kol.frist") }),
          el("th", { scope: "col", text: t("ui.tilskudd.kol.sum") }),
          el("th", { scope: "col",
                     text: t("ui.tilskudd.kol.detalj") }))),
        tbody)));
      if (harScope(ctx, "bestilling:opprett")) {
        const lukkbare = funn.filter(
          (f) => f.funntype !== "estimat_over_ordningstak");
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
  const valg = el("select", { id: "ti-fn-valg", name: "funn",
                              required: true });
  valg.append(el("option", { value: "",
    text: t("ui.tilskudd.funn.velg") }));
  for (const f of funn) {
    valg.append(el("option", {
      value: `${f.ordning_id}\u001f${f.funntype}`,
      text: `${f.navn} — ${t(MERKE[f.funntype] || f.funntype)}`,
    }));
  }
  const notat = el("input", { id: "ti-fn-notat", name: "notat",
    type: "text", required: true, minlength: "4", maxlength: "4000" });
  const knapp = el("button", { type: "submit",
    text: t("ui.tilskudd.knapp.lukk_funn") });
  skjema.append(
    felt("ti-fn-valg", "ui.tilskudd.funn.hvilket", valg, null),
    felt("ti-fn-notat", "ui.tilskudd.funn.notat", notat,
         "ui.tilskudd.funn.notat_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.tilskudd.skjema.funn_lukket",
    tilbakestill: () => { valg.value = ""; notat.value = ""; },
    send: (idem) => {
      const [id, type] = valg.value.split("\u001f");
      return lukkTilskuddsfunn(id, type, notat.value.trim(), idem);
    },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.tilskudd.funn.lukk_tittel") }), skjema,
    utfall);
}


export function visTilskudd(hoved, ctx) {
  const hode = () => flateHode(t("ui.tilskudd.tittel"),
    t("ui.tilskudd.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenRad = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const settApen = (id) => { apenRad = id; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/tilskudd"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const ordninger = d.ordninger || [];
      const kildeposter = d.kildeposter || [];
      const detalj = detaljpanel(ctx, last, kvitter, settApen,
                                 kildeposter);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.tilskudd.oversikt.tittel") }),
        sammendrag(s),
        // HVA TALLENE ER, SAGT RETT UT: estimater med forutsetninger,
        // ikke lovnader.
        el("p", { class: "muted",
                  text: t("ui.tilskudd.oversikt.hvorfor") }));

      const liste = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.tilskudd.liste.tittel") }));
      if (!ordninger.length) {
        liste.append(el("p", { class: "muted",
          text: t("ui.tilskudd.liste.ingen") }));
      } else {
        liste.append(ordningTabell(ordninger, detalj.apne));
      }

      const kildeseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.tilskudd.kildeposter.tittel") }));
      if (!kildeposter.length) {
        kildeseksjon.append(el("p", { class: "muted",
          text: t("ui.tilskudd.kildeposter.ingen") }));
      } else {
        kildeseksjon.append(kildepostTabell(kildeposter));
      }

      const kravseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.tilskudd.krav.tittel") }));
      if (!d.krav) {
        kravseksjon.append(el("p", { class: "muted",
          text: t("ui.tilskudd.ingen_krav") }));
      } else {
        kravseksjon.append(el("p", { class: "muted",
          text: t("ui.tilskudd.krav.versjon")
            .replace("{versjon}", String(d.krav.versjon)) }));
      }

      const deler = [oversikt, liste, kildeseksjon, kravseksjon,
                     detalj.node, funnseksjon(ctx, last, kvitter)];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(ordningSkjema(ctx, last, kvitter),
                   kildepostSkjema(ctx, last, kvitter),
                   kravSkjema(ctx, last, d.krav, kvitter));
      }
      sett(kropp, ...deler);
      if (apenRad) {
        const rad = ordninger.find((x) => x.ordning_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
