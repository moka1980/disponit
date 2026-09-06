// M-28 logistikk- og transportagent (139) — PLANEN ER PRODUKTET.
//
// FLATENS VIKTIGSTE JOBB ER Å VISE HVEM SOM SA AT PAKKEN VAR TRYGG.
//
// Klyngens delte dom: EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN
// ANGRES IKKE AV EN ROLLBACK. Bilen kjører uansett hva basen sier — en
// booking som ble rullet tilbake er fortsatt en bil på veien, en pakke
// i en terminal og en faktura fra en transportør.
//
// DERFOR STÅR «BESTILLINGER: 0» I SAMMENDRAGET, ALLTID. Tallet er ikke
// en telling av en kolonne — det er en påstand om at kolonnen ikke
// finnes.
//
// OG DERFOR STÅR NAVNET VED SIDEN AV FAREKLASSEN, ALLTID. En
// fareklasse uten et menneske bak er en påstand ingen svarer for — og
// en gal påstand der er en brann i en lastebil, ikke en feil i en
// rapport.
//
// EN PLAN VISES MED LANDPAKKEVERSJONEN SIN. Uten den kan ingen
// etterprøve hvilke regler planen hvilte på da reglene endres.
//
// DET FINNES INGEN «BESTILL»- ELLER «OMBOOK»-KNAPP, OG DET KAN IKKE
// FINNES. Modulen planlegger; den sender ingenting.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  foreslaaTransport, forkastTransportforslag, hentJson,
  lukkTransportfunn, nyIdempotensnokkel, registrerKolli,
  settTransportkrav, UautorisertFeil,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// ADRs NI KLASSER PLUSS `ingen`.
//
// Settet er den internasjonale standarden, ikke vår oppfinnelse — og
// derfor komplett uten en `annet`-verdi. En `annet` her ville latt en
// pakke ingen visste hva var få lov til å reise.
export const FAREKLASSER = [
  "ingen",
  "klasse_1_eksplosiver",
  "klasse_2_gasser",
  "klasse_3_brannfarlige_vaesker",
  "klasse_4_brannfarlige_faste_stoffer",
  "klasse_5_oksiderende",
  "klasse_6_giftige_og_smittefarlige",
  "klasse_7_radioaktive",
  "klasse_8_etsende",
  "klasse_9_ovrige_farlige",
];

const FAREKLASSETEKST = {
  ingen: "ui.transport.fare_ingen",
  klasse_1_eksplosiver: "ui.transport.fare_1",
  klasse_2_gasser: "ui.transport.fare_2",
  klasse_3_brannfarlige_vaesker: "ui.transport.fare_3",
  klasse_4_brannfarlige_faste_stoffer: "ui.transport.fare_4",
  klasse_5_oksiderende: "ui.transport.fare_5",
  klasse_6_giftige_og_smittefarlige: "ui.transport.fare_6",
  klasse_7_radioaktive: "ui.transport.fare_7",
  klasse_8_etsende: "ui.transport.fare_8",
  klasse_9_ovrige_farlige: "ui.transport.fare_9",
};

const FUNNTEKST = {
  apent_forslag_over_frist: "ui.transport.funn_over_frist",
  tungt_kolli_ukontrollert: "ui.transport.funn_tungt",
  kolli_uten_forslag: "ui.transport.funn_uten_plan",
  land_uten_pakke: "ui.transport.funn_uten_pakke",
  krav_mangler: "ui.transport.funn_krav_mangler",
  kolli_bestilt_to_ganger: "ui.transport.funn_dobbelt",
  fareklasse_utledet_av_maskin: "ui.transport.funn_utledet",
  farlig_gods_uten_landregel: "ui.transport.funn_uten_landregel",
  forslag_uten_validert_adresse: "ui.transport.funn_uvalidert",
};


export function dato(iso) {
  if (typeof iso !== "string" || !iso) return "–";
  return iso.slice(0, 10);
}


// GRAM TIL LESBAR VEKT.
//
// Heltall i basen, deling bare i visningen: flyttall og fysiske mål
// hører ikke sammen når noen skal laste en bil etter dem.
export function vekt(gram) {
  if (typeof gram !== "number") return "–";
  if (gram < 1000) return `${gram} g`;
  return `${(gram / 1000).toFixed(gram % 1000 === 0 ? 0 : 1)} kg`;
}


// MÅLENE, SOM ÉN LESBAR STRENG.
export function mal(rad) {
  if (!rad || typeof rad.lengde_mm !== "number") return "–";
  return `${rad.lengde_mm}×${rad.bredde_mm}×${rad.hoyde_mm} mm`;
}


// FAREKLASSEN MED NAVNET SOM OPPGA DEN.
//
// «Klasse 3» sier hva pakken er. «Klasse 3 · u-lagermedarbeider» sier
// hvem som så på den — og det er den som svarer hvis det tar fyr.
export function faretekst(rad) {
  if (!rad) return "–";
  const navn = t(FAREKLASSETEKST[rad.fareklasse]
                 || "ui.transport.fare_ukjent");
  if (!rad.fareklasse_oppgitt_av) return navn;
  return t("ui.transport.fare_verdi")
    .replace("{klasse}", navn)
    .replace("{av}", rad.fareklasse_oppgitt_av);
}


export function iDagLokal(naa) {
  const d = naa || new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}


function felt(id, tekst, kontroll, hjelp) {
  return el("div", { class: "felt" },
    el("label", { for: id, text: t(tekst) }),
    kontroll,
    hjelp ? el("p", { class: "muted", text: t(hjelp) }) : null);
}


function velger(id, verdier, tekster) {
  const s = el("select", { id, name: id });
  for (const v of verdier) {
    s.append(el("option", { value: v, text: t(tekster[v]) }));
  }
  return s;
}


function knappMed(tekst, ved) {
  const b = el("button", { type: "button", text: tekst });
  b.addEventListener("click", ved);
  return b;
}


function skjemaramme(ctx, last, { skjema, knapp, utfall, send,
                                 tilbakestill, okNokkel, kvitter,
                                 okTekst }) {
  // `okTekst` FINNES FORDI KVITTERINGEN MÅ OVERLEVE GJENLASTINGEN.
  //
  // Et svar skrevet inn i et felt under `kropp` blir borte i det
  // `last()` bygger `kropp` på nytt — og for en plan er det nettopp
  // svaret som betyr noe: mottakerlandet, landpakkeversjonen og
  // fareklassen er de tre tingene kalleren IKKE oppga. 138s lærdom.
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
      // DØRAS NEKT ER BRUKERENS FEIL, OG DEN SKAL LESES.
      //
      // «Tyskland har ingen landpakke» og «adressen har ingen godkjent
      // kontroll» er begge noe kalleren kan gjøre noe med.
      // `e.message` VISES ALDRI.
      //
      // Første utgave viste den for feil uten `status` — og det ville
      // truffet en `TypeError` like godt som en tekst vi selv skrev.
      // «Cannot read properties of undefined» er ikke noe en bruker
      // kan handle på; det er innmat på avveie. CodeRabbit fant det.
      //
      // Serverens egen begrunnelse kommer i `detaljer` (api.js), og
      // den er trygg: den er skrevet av oss, for et menneske.
      const detalj = e && Array.isArray(e.detaljer) && e.detaljer.length
        ? String(e.detaljer[0]) : null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.transport.feil.tilstand")
          : t("ui.transport.feil.generell") }));
      if (detalj) {
        utfall.append(" ", el("span", { class: "muted", text: detalj }));
      }
      return;
    }
    idem = null;
    knapp.disabled = false;
    if (tilbakestill) tilbakestill();
    const tekst = (okTekst && okTekst()) || t(okNokkel);
    meldLive(tekst);
    kvitter(tekst);
    await last();
  });
}


function tabell(kolonner, rader) {
  const thead = el("thead", {}, el("tr", {},
    ...kolonner.map((k) => el("th", { scope: "col", text: t(k) }))));
  const tbody = el("tbody", {});
  for (const r of rader) tbody.append(r);
  return el("table", { class: "tabell" }, thead, tbody);
}


// KVITTERINGEN FOR EN PLAN.
//
// Mottakerlandet, landpakkeversjonen og fareklassen er alle tre ting
// kalleren IKKE oppga: hun ga et kolli og en adresseversjon, og fikk
// landet, reglene og klassen tilbake.
//
// EGEN FUNKSJON FORDI DEN SKAL KUNNE MÅLES — 138s lærdom: en port som
// leser teksten ut av skjermen etter to nettverksrunder måler timingen
// framfor innholdet.
export function kvitteringstekst(ut) {
  if (!ut) return "";
  const klasse = t(FAREKLASSETEKST[ut.fareklasse]
                   || "ui.transport.fare_ukjent");
  return t("ui.transport.plan_svar")
    .replace("{land}", ut.mottakerland)
    .replace("{v}", String(ut.landpakke_regelversjon))
    .replace("{klasse}", klasse);
}


// SAMMENDRAGET. LINJEN SOM ALLTID SIER NULL.
export function sammendrag(s) {
  const p = el("p", {});
  if (s.farlige_kolli > 0) {
    p.append(el("strong", { role: "alert",
      text: t("ui.transport.farlige")
        .replace("{n}", String(s.farlige_kolli)) }), " ");
  }
  p.append(el("span", {
    text: t("ui.transport.sammendrag")
      .replace("{kolli}", String(s.kolli ?? 0))
      .replace("{planer}", String(s.apne_forslag ?? 0))
      .replace("{land}", String(s.land_i_bruk ?? 0))
      .replace("{funn}", String(s.apne_funn ?? 0)) }));
  p.append(" ", el("strong", {
    class: "utfort-null",
    text: t("ui.transport.ingen_bestilling")
      .replace("{n}", String(s.bestillinger ?? 0)) }));
  return p;
}


// KOLLILISTEN. NAVNET STÅR VED SIDEN AV FAREKLASSEN.
export function kollirader(data) {
  const ut = [];
  for (const k of data.kolli || []) {
    const tr = el("tr", {});
    tr.append(el("td", { text: k.referanse || "–" }));
    tr.append(el("td", { text: vekt(k.vekt_gram) }));
    tr.append(el("td", { text: mal(k) }));
    // FAREKLASSEN OG NAVNET. Et farlig kolli er et varsel; et
    // ufarlig er en opplysning.
    const fare = el("td", {});
    if (k.farlig) {
      fare.append(el("strong", { role: "alert", text: faretekst(k) }));
    } else {
      fare.append(el("span", { text: faretekst(k) }));
    }
    tr.append(fare);
    tr.append(el("td", {},
      k.har_apent_forslag
        ? el("span", { text: t("ui.transport.har_plan") })
        : el("span", { class: "muted",
                       text: t("ui.transport.uten_plan") })));
    tr.append(el("td", { text: dato(k.registrert) }));
    ut.push(tr);
  }
  return ut;
}


// PLANENE. LANDPAKKEVERSJONEN I SAMME RAD SOM LANDET.
export function forslagsrader(data, ctx) {
  const ut = [];
  for (const f of data.forslag || []) {
    const tr = el("tr", {});
    tr.append(el("td", { text: f.kolliref || "–" }));
    // BEGGE LANDENE OG VERSJONEN AV REGLENE.
    tr.append(el("td", {
      text: t("ui.transport.rute")
        .replace("{fra}", f.avsenderland || "–")
        .replace("{til}", f.mottakerland || "–")
        .replace("{v}", String(f.landpakke_regelversjon ?? "–")) }));
    const fare = el("td", {});
    if (f.farlig) {
      fare.append(el("strong", { role: "alert",
        text: t(FAREKLASSETEKST[f.fareklasse]
                || "ui.transport.fare_ukjent") }));
    } else {
      fare.append(el("span", { class: "muted",
        text: t(FAREKLASSETEKST[f.fareklasse]
                || "ui.transport.fare_ukjent") }));
    }
    tr.append(fare);
    tr.append(el("td", { text: vekt(f.vekt_gram) }));
    const kontroll = el("td", {});
    if (f.over_kontrollgrense) {
      kontroll.append(el("strong", { role: "alert",
        text: t("ui.transport.krever_kontroll") }));
    } else {
      kontroll.append(el("span", { class: "muted",
        text: t("ui.transport.under_grense") }));
    }
    tr.append(kontroll);
    const handling = el("td", {});
    if (f.status === "apen" && ctx.kanSkrive) {
      handling.append(knappMed(t("ui.transport.forkast"),
        () => ctx.forkast(f)));
    } else if (f.status === "forkastet") {
      // DEN VRAKEDE STÅR. Sletting ville fjernet beviset på at vi
      // hadde planen (M-50s dom, 124).
      handling.append(el("span", { class: "muted",
        text: t("ui.transport.forkastet") }));
    }
    tr.append(handling);
    ut.push(tr);
  }
  return ut;
}


export function funnrader(data, ctx) {
  const ut = [];
  for (const f of data.funn || []) {
    const tr = el("tr", {});
    tr.append(el("td", {
      text: t(FUNNTEKST[f.funntype] || "ui.transport.funn_ukjent") }));
    tr.append(el("td", { text: f.detalj || "–" }));
    tr.append(el("td", { text: dato(f.forst_sett) }));
    const handling = el("td", {});
    if (f.sveipens) {
      handling.append(el("span", { class: "muted",
        text: t("ui.transport.lukkes_av_sveipen") }));
    } else if (ctx.kanSkrive) {
      handling.append(knappMed(t("ui.transport.lukk_funn"),
        () => ctx.lukkFunn(f)));
    }
    tr.append(handling);
    ut.push(tr);
  }
  return ut;
}


// KRAVSKJEMAET.
export function kravskjema(ctx, last, kvitter, s) {
  const land = el("input", { id: "tr-avsenderland", type: "text",
                             pattern: "[A-Z]{2}", maxlength: "2",
                             required: "required",
                             value: s.avsenderland || "NO" });
  const maks = el("input", { id: "tr-maks", type: "number", min: "1",
                             required: "required",
                             value: String(s.maks_kolli_gram ?? 50000) });
  const manuell = el("input", { id: "tr-manuell", type: "number",
                                min: "0", required: "required",
                                value: String(
                                  s.manuell_kontroll_over_gram ?? 20000) });
  const frist = el("input", { id: "tr-frist", type: "number", min: "1",
                              max: "365", required: "required",
                              value: String(s.forslagsfrist_dogn ?? 14) });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.transport.lagre_krav") });
  const skjema = el("form", { class: "skjema" },
    felt("tr-avsenderland", "ui.transport.felt_avsenderland", land,
         "ui.transport.hjelp_avsenderland"),
    felt("tr-maks", "ui.transport.felt_maks", maks,
         "ui.transport.hjelp_maks"),
    felt("tr-manuell", "ui.transport.felt_manuell", manuell,
         "ui.transport.hjelp_manuell"),
    felt("tr-frist", "ui.transport.felt_frist", frist,
         "ui.transport.hjelp_frist"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, okNokkel: "ui.transport.krav_lagret", kvitter,
    send: (idem) => settTransportkrav({
      avsenderland: land.value.toUpperCase(),
      maks_kolli_gram: Number(maks.value),
      manuell_kontroll_over_gram: Number(manuell.value),
      forslagsfrist_dogn: Number(frist.value),
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.transport.krav") }),
    el("p", { class: "muted", text: t("ui.transport.krav_hjelp") }),
    skjema);
}


// KOLLISKJEMAET. FAREKLASSEN OPPGIS, ALDRI UTLEDET.
//
// Feltet `fareklasse_oppgitt_av` er PÅKREVD og er et navn. Skjemaet
// tar ikke imot en produktbeskrivelse eller en varekode — det finnes
// ikke noe å utlede klassen AV.
export function kolliskjema(ctx, last, kvitter, s) {
  const ref = el("input", { id: "tr-ref", type: "text", maxlength: "200",
                            required: "required" });
  const vektfelt = el("input", { id: "tr-vekt", type: "number", min: "1",
                                 required: "required", value: "1000" });
  const lengde = el("input", { id: "tr-lengde", type: "number", min: "1",
                               max: "20000", required: "required",
                               value: "300" });
  const bredde = el("input", { id: "tr-bredde", type: "number", min: "1",
                               max: "20000", required: "required",
                               value: "200" });
  const hoyde = el("input", { id: "tr-hoyde", type: "number", min: "1",
                              max: "20000", required: "required",
                              value: "150" });
  const klasse = velger("tr-fareklasse", FAREKLASSER, FAREKLASSETEKST);
  const oppgittAv = el("input", { id: "tr-oppgittav", type: "text",
                                  maxlength: "200",
                                  required: "required" });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.transport.lagre_kolli") });
  const skjema = el("form", { class: "skjema" },
    felt("tr-ref", "ui.transport.felt_ref", ref),
    felt("tr-vekt", "ui.transport.felt_vekt", vektfelt,
         "ui.transport.hjelp_vekt"),
    felt("tr-lengde", "ui.transport.felt_lengde", lengde),
    felt("tr-bredde", "ui.transport.felt_bredde", bredde),
    felt("tr-hoyde", "ui.transport.felt_hoyde", hoyde),
    felt("tr-fareklasse", "ui.transport.felt_fareklasse", klasse,
         "ui.transport.hjelp_fareklasse"),
    felt("tr-oppgittav", "ui.transport.felt_oppgittav", oppgittAv,
         "ui.transport.hjelp_oppgittav"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, okNokkel: "ui.transport.kolli_lagret", kvitter,
    tilbakestill: () => { ref.value = ""; },
    send: (idem) => registrerKolli({
      referanse: ref.value,
      vekt_gram: Number(vektfelt.value),
      lengde_mm: Number(lengde.value),
      bredde_mm: Number(bredde.value),
      hoyde_mm: Number(hoyde.value),
      fareklasse: klasse.value,
      fareklasse_oppgitt_av: oppgittAv.value,
      kravversjon: s.kravversjon,
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.transport.nytt_kolli") }),
    el("p", { class: "muted", text: t("ui.transport.kolli_hjelp") }),
    skjema);
}


// PLANSKJEMAET. KALLEREN OPPGIR ALDRI ET LAND.
//
// Feltene er: kolli og adresseversjon. Mottakerlandet leses fra
// adressen, landpakkeversjonen fra registeret, fareklassen fra
// kolliet. Alle tre kommer tilbake i kvitteringen.
export function planskjema(ctx, last, kvitter, s, data) {
  const ledige = (data.kolli || []).filter((k) => !k.har_apent_forslag);
  const node = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.transport.ny_plan") }));
  if (!ledige.length) {
    // UTEN ET LEDIG KOLLI KAN INGEN PLAN LAGES. Flaten sier det
    // framfor å vise et skjema som ikke virker.
    node.append(el("p", { class: "muted",
      text: t("ui.transport.plan_uten_kolli") }));
    return node;
  }
  const kolli = el("select", { id: "tr-plankolli", name: "tr-plankolli" });
  for (const k of ledige) {
    kolli.append(el("option", { value: k.kolli_id,
      text: k.farlig
        ? `${k.referanse} · ${t(FAREKLASSETEKST[k.fareklasse]
                                || "ui.transport.fare_ukjent")}`
        : k.referanse }));
  }
  const adresse = el("input", { id: "tr-planadresse", type: "text",
                                required: "required",
                                placeholder: "UUID" });
  const grunn = el("textarea", { id: "tr-plangrunn", rows: "2",
                                 maxlength: "8000",
                                 required: "required" });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.transport.plan_knapp") });
  let sisteSvar = null;
  const skjema = el("form", { class: "skjema" },
    felt("tr-plankolli", "ui.transport.felt_kolli", kolli),
    felt("tr-planadresse", "ui.transport.felt_adresse", adresse,
         "ui.transport.hjelp_adresse"),
    felt("tr-plangrunn", "ui.transport.felt_begrunnelse", grunn,
         "ui.transport.hjelp_begrunnelse"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, okNokkel: "ui.transport.plan_lagret", kvitter,
    okTekst: () => sisteSvar,
    tilbakestill: () => { grunn.value = ""; },
    send: async (idem) => {
      const ut = await foreslaaTransport({
        kolli_id: kolli.value,
        kravversjon: s.kravversjon,
        adresseversjon_id: adresse.value.trim(),
        begrunnelse: grunn.value,
      }, idem);
      sisteSvar = kvitteringstekst(ut);
      return ut;
    },
  });
  node.append(el("p", { class: "muted",
    text: t("ui.transport.plan_hjelp") }), skjema);
  return node;
}


// LUKKE- OG FORKASTEPANELET.
function grunnpanel(ctx, last, kvitter, art) {
  const grunn = el("textarea", { id: `tr-${art}grunn`, rows: "2",
                                 maxlength: "8000",
                                 required: "required" });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t(art === "forkast"
                                       ? "ui.transport.forkast_bekreft"
                                       : "ui.transport.lukk_bekreft") });
  const skjema = el("form", { class: "skjema" },
    felt(`tr-${art}grunn`, "ui.transport.felt_grunn", grunn,
         "ui.transport.hjelp_grunn"),
    knapp, utfall);
  const node = el("section", { class: "kpi-kort", hidden: "hidden" },
    el("h2", { text: t(art === "forkast"
                       ? "ui.transport.forkast_tittel"
                       : "ui.transport.lukk_funn_tittel") }), skjema);
  let valgt = null;
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: art === "forkast"
      ? "ui.transport.plan_forkastet" : "ui.transport.funn_lukket",
    tilbakestill: () => {
      grunn.value = ""; valgt = null; node.hidden = true;
    },
    send: (idem) => (art === "forkast"
      ? forkastTransportforslag(valgt.forslag_id,
                                { grunn: grunn.value }, idem)
      : lukkTransportfunn(valgt.funn_id, { grunn: grunn.value }, idem)),
  });
  return {
    node,
    aapne: (rad) => { valgt = rad; node.hidden = false; grunn.focus(); },
  };
}


export function visTransport(hoved, ctx) {
  const hode = () => flateHode(t("ui.transport.tittel"),
    t("ui.transport.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/transport"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const skriver = harScope(ctx, "bestilling:opprett");
      const forkasting = grunnpanel(ctx, last, kvitter, "forkast");
      const lukking = grunnpanel(ctx, last, kvitter, "lukk");
      const kontekst = {
        kanSkrive: skriver,
        forkast: (f) => forkasting.aapne(f),
        lukkFunn: (f) => lukking.aapne(f),
      };

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.transport.sammendrag_tittel") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT.
        el("p", { class: "muted", text: t("ui.transport.hvorfor") }));

      const funnseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.transport.funn") }));
      if (!(d.funn || []).length) {
        funnseksjon.append(el("p", { class: "muted",
          text: t("ui.transport.funn_tomt") }));
      } else {
        funnseksjon.append(tabell(
          ["ui.transport.kol_funntype", "ui.transport.kol_detalj",
           "ui.transport.kol_forst_sett", "ui.transport.kol_handling"],
          funnrader(d, kontekst)));
      }

      const planseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.transport.planer") }));
      if (!(d.forslag || []).length) {
        planseksjon.append(el("p", { class: "muted",
          text: t("ui.transport.planer_tomt") }));
      } else {
        planseksjon.append(tabell(
          ["ui.transport.kol_kolli", "ui.transport.kol_rute",
           "ui.transport.kol_fareklasse", "ui.transport.kol_vekt",
           "ui.transport.kol_kontroll", "ui.transport.kol_handling"],
          forslagsrader(d, kontekst)));
      }

      const kolliseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.transport.kolli") }));
      if (!(d.kolli || []).length) {
        kolliseksjon.append(el("p", { class: "muted",
          text: t("ui.transport.kolli_tomt") }));
      } else {
        kolliseksjon.append(tabell(
          ["ui.transport.kol_referanse", "ui.transport.kol_vekt",
           "ui.transport.kol_mal", "ui.transport.kol_fareklasse",
           "ui.transport.kol_plan", "ui.transport.kol_registrert"],
          kollirader(d)));
      }

      const deler = [oversikt, funnseksjon, lukking.node,
                     planseksjon, forkasting.node, kolliseksjon];
      if (skriver) {
        if (s.har_krav) {
          deler.push(planskjema(ctx, last, kvitter, s, d),
                     kolliskjema(ctx, last, kvitter, s));
        } else {
          deler.push(el("section", { class: "kpi-kort" },
            el("h2", { text: t("ui.transport.nytt_kolli") }),
            el("p", { class: "muted",
                      text: t("ui.transport.kolli_uten_krav") })));
        }
        deler.push(kravskjema(ctx, last, kvitter, s));
      }
      sett(kropp, ...deler);
    });

  return last();
}
