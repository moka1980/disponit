// M-40 HR- og medarbeideragent (140) — MODULEN SOM IKKE AVGJØR NOE.
//
// FLATENS VIKTIGSTE JOBB ER Å VISE HVA SOM IKKE ER TALT.
//
// Klyngens delte dom: EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN
// ANGRES IKKE AV EN ROLLBACK. M-28 sa det om en bil på veien. Her er
// det tyngre — en oppsigelse som ble rullet tilbake er fortsatt en
// samtale som fant sted, en beskjed som ble lest og et menneske som
// brukte kvelden på den.
//
// DERFOR STÅR «BESLUTNINGER: 0» OG «INDIVIDPROFILER: 0» I
// SAMMENDRAGET, ALLTID. Tallene er ikke tellinger av kolonner — de er
// påstander om at kolonnene ikke finnes. Et tall som alltid er null er
// stedet et menneske kan se etter for å oppdage den dagen det ikke er
// det.
//
// PULSFLATEN VISER GRUPPER, ALDRI MENNESKER. Et aggregat under
// terskelen kommer aldri hit, fordi døra ikke sender det. Flaten
// MASKERER INGENTING — den har ingenting å maskere, og det er en
// vesentlig forskjell: en maskert verdi er fortsatt en verdi.
//
// ET TOMT PULSBILDE ER ET GYLDIG SVAR, og teksten sier hvorfor: «for
// få har svart til at noen kan lese det». Flaten sier ikke hvor
// mange — det ville i seg selv vært tallet terskelen verner.
//
// DET FINNES INGEN «VURDER»-, «RANGER»- ELLER «SCORE»-KNAPP, OG DET
// KAN IKKE FINNES. Modulen kjører løp, utsteder fra låste maler og
// måler stemning på gruppenivå; den avgjør ingenting om noen.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  apneMaaling, avgiPuls, avsluttLop, hentJson, lukkMedarbeiderfunn,
  lukkMaaling, nyIdempotensnokkel, settMedarbeiderkrav, startLop,
  utfoerSteg, utstedKontrakt, UautorisertFeil,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// STEGENE I EN FØRSTEUKE, LUKKET SETT.
//
// En åpen `stegtype` ville gjort katalogen til fritekst, og da kan
// ingen si hva et løp faktisk inneholder.
export const STEGTYPER = [
  "utstyr_utlevert",
  "tilgang_opprettet",
  "kontrakt_utstedt",
  "introsamtale_holdt",
  "hms_gjennomgatt",
  "fadder_tildelt",
  "opplaering_fullfort",
];

const STEGTEKST = {
  utstyr_utlevert: "ui.medarbeider.steg_utstyr",
  tilgang_opprettet: "ui.medarbeider.steg_tilgang",
  kontrakt_utstedt: "ui.medarbeider.steg_kontrakt",
  introsamtale_holdt: "ui.medarbeider.steg_intro",
  hms_gjennomgatt: "ui.medarbeider.steg_hms",
  fadder_tildelt: "ui.medarbeider.steg_fadder",
  opplaering_fullfort: "ui.medarbeider.steg_opplaering",
};

export const AVSLUTNINGER = ["fullfort", "avbrutt"];

const AVSLUTNINGSTEKST = {
  fullfort: "ui.medarbeider.avslutning_fullfort",
  avbrutt: "ui.medarbeider.avslutning_avbrutt",
};

const STATUSTEKST = {
  apent: "ui.medarbeider.status_apent",
  fullfort: "ui.medarbeider.status_fullfort",
  avbrutt: "ui.medarbeider.status_avbrutt",
};

const MALSTATUSTEKST = {
  utkast: "ui.medarbeider.mal_utkast",
  publisert: "ui.medarbeider.mal_publisert",
  tilbaketrukket: "ui.medarbeider.mal_tilbaketrukket",
};

const FUNNTEKST = {
  apent_lop_over_frist: "ui.medarbeider.funn_over_frist",
  ansatt_uten_lop: "ui.medarbeider.funn_uten_lop",
  maaling_uten_lesbar_gruppe: "ui.medarbeider.funn_ulesbar",
  kontrakt_paa_tilbaketrukket_mal: "ui.medarbeider.funn_trukket_mal",
  krav_mangler: "ui.medarbeider.funn_krav_mangler",
  beslutning_med_rettsvirkning: "ui.medarbeider.funn_beslutning",
  individprofil_bygget: "ui.medarbeider.funn_profil",
  puls_identifiserte_en_person: "ui.medarbeider.funn_identifisert",
  gruppeterskel_endret: "ui.medarbeider.funn_terskel_endret",
  kontrakt_uten_malversjon: "ui.medarbeider.funn_uten_mal",
};


export function dato(iso) {
  if (typeof iso !== "string" || !iso) return "–";
  return iso.slice(0, 10);
}


// FRAMDRIFTEN I ET LØP, SOM ÉN LESBAR STRENG.
//
// «3 av 5» og ikke «60 %»: et løp har få steg, og en prosent av fem
// ting er en presisjon tallet ikke har.
export function framdrift(rad) {
  if (!rad || typeof rad.steg !== "number") return "–";
  return t("ui.medarbeider.framdrift")
    .replace("{utfort}", String(rad.steg_utfort ?? 0))
    .replace("{av}", String(rad.steg));
}


// KONTRAKTENS SPOR TIL MALEN.
//
// «Ansettelseskontrakt v1» sier hvilken tekst den hviler på.
// Akseptansekravet ber om malversjon OG kildefelt, og begge står i
// raden.
export function malsporet(rad) {
  if (!rad) return "–";
  return t("ui.medarbeider.malspor")
    .replace("{navn}", rad.malnavn || "–")
    .replace("{v}", String(rad.malversjonsnr ?? "–"));
}


// PULSKVITTERINGEN.
//
// EGEN FUNKSJON FORDI DEN SKAL KUNNE MÅLES — 138s lærdom: en port som
// leser teksten ut av skjermen etter to nettverksrunder måler timingen
// framfor innholdet.
//
// DEN NEVNER ALDRI HVA SOM BLE SVART. Kvitteringen sier at svaret er
// mottatt, ikke hva det var — en kvittering med verdien i ville vært
// den eneste linjen i systemet som koblet et menneske til sin egen
// puls, og den ville stått på hennes egen skjerm.
export function pulskvittering() {
  return t("ui.medarbeider.puls_mottatt");
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
  // `last()` bygger `kropp` på nytt (138s lærdom).
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
      // «Malen er ikke publisert» og «terskelen er under gulvet» er
      // begge noe kalleren kan gjøre noe med. `e.message` VISES ALDRI:
      // en `TypeError` har heller ingen `status`, så «vis meldingen
      // for feil uten status» ville vist innmat (139s funn).
      //
      // Serverens egen begrunnelse kommer i `detaljer` (api.js), og
      // den er trygg: den er skrevet av oss, for et menneske.
      const detalj = e && Array.isArray(e.detaljer) && e.detaljer.length
        ? String(e.detaljer[0]) : null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.medarbeider.feil.tilstand")
          : t("ui.medarbeider.feil.generell") }));
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


// SAMMENDRAGET. LINJENE SOM ALLTID SIER NULL.
export function sammendrag(s) {
  const p = el("p", {});
  p.append(el("span", {
    text: t("ui.medarbeider.sammendrag")
      .replace("{lop}", String(s.apne_lop ?? 0))
      .replace("{kontrakter}", String(s.kontrakter ?? 0))
      .replace("{maalinger}", String(s.apne_maalinger ?? 0))
      .replace("{funn}", String(s.apne_funn ?? 0)) }));
  // DE TO TALLENE SOM ER HELE V1-DOMMEN.
  p.append(" ", el("strong", {
    class: "utfort-null",
    text: t("ui.medarbeider.ingen_beslutning")
      .replace("{b}", String(s.beslutninger ?? 0))
      .replace("{p}", String(s.individprofiler ?? 0)) }));
  return p;
}


// LØPENE. ANSATTNUMMERET, ALDRI NAVNET.
//
// Modulen vet AT hun er ansatt, ikke hva hun heter — kolonnegranten på
// `lonnstaker` utelater `navn` med vilje, og flaten kan derfor ikke
// vise det selv om noen ba den om det.
export function loprader(data, ctx) {
  const ut = [];
  for (const l of data.lop || []) {
    const tr = el("tr", {});
    tr.append(el("td", { text: l.ekstern_ref || "–" }));
    tr.append(el("td", {
      text: t(STATUSTEKST[l.status] || "ui.medarbeider.status_ukjent") }));
    tr.append(el("td", { text: framdrift(l) }));
    tr.append(el("td", { text: dato(l.startet) }));
    const handling = el("td", {});
    if (l.status === "apent" && ctx.kanSkrive) {
      handling.append(knappMed(t("ui.medarbeider.registrer_steg"),
        () => ctx.steg(l)));
      handling.append(" ");
      handling.append(knappMed(t("ui.medarbeider.avslutt"),
        () => ctx.avslutt(l)));
    } else if (l.status !== "apent") {
      // DET AVSLUTTEDE LØPET STÅR. Sletting ville fjernet beviset på
      // at noen faktisk tok imot henne.
      handling.append(el("span", { class: "muted",
        text: t(AVSLUTNINGSTEKST[l.status] || "") }));
    }
    tr.append(handling);
    ut.push(tr);
  }
  return ut;
}


// KONTRAKTENE. MALVERSJONEN OG KILDEFELTENE, ALDRI VERDIENE.
export function kontraktrader(data) {
  const ut = [];
  for (const k of data.kontrakter || []) {
    const tr = el("tr", {});
    tr.append(el("td", { text: k.ekstern_ref || "–" }));
    tr.append(el("td", { text: malsporet(k) }));
    // MALENS STATUS I DAG. En tilbaketrukket mal under en gyldig
    // kontrakt er ikke en feil — det er noe et menneske bør se på.
    const status = el("td", {});
    if (k.malstatus === "tilbaketrukket") {
      status.append(el("strong", { role: "alert",
        text: t(MALSTATUSTEKST.tilbaketrukket) }));
    } else {
      status.append(el("span", { class: "muted",
        text: t(MALSTATUSTEKST[k.malstatus] || "") }));
    }
    tr.append(status);
    // KILDEFELTENE — hvilke, aldri hva som sto i dem.
    tr.append(el("td", { text: (k.felt || []).join(", ") || "–" }));
    tr.append(el("td", { text: dato(k.utstedt) }));
    ut.push(tr);
  }
  return ut;
}


// MÅLINGENE. LESBARE GRUPPER, ALDRI ANTALL SVAR.
//
// Et totaltall for en måling med én gruppe VILLE VÆRT gruppens tall,
// og da hadde terskelen vært omgått av oversikten framfor av
// aggregatet.
export function maalingsrader(data, ctx) {
  const ut = [];
  for (const m of data.maalinger || []) {
    const tr = el("tr", {});
    tr.append(el("td", { text: m.tittel || "–" }));
    tr.append(el("td", { text: String(m.gruppeterskel ?? "–") }));
    const lesbare = el("td", {});
    if ((m.lesbare_grupper ?? 0) === 0) {
      // INGEN LESBAR GRUPPE ER IKKE EN FEIL — det er terskelen som
      // gjør jobben sin. Men det er verdt å se.
      lesbare.append(el("span", { class: "muted",
        text: t("ui.medarbeider.ingen_lesbar") }));
    } else {
      lesbare.append(el("span", { text: String(m.lesbare_grupper) }));
    }
    tr.append(lesbare);
    tr.append(el("td", {
      text: m.lukket ? dato(m.lukket) : t("ui.medarbeider.apen") }));
    const handling = el("td", {});
    handling.append(knappMed(t("ui.medarbeider.se_puls"),
      () => ctx.visPuls(m)));
    if (!m.lukket && ctx.kanSkrive) {
      handling.append(" ");
      handling.append(knappMed(t("ui.medarbeider.svar_puls"),
        () => ctx.svarPuls(m)));
      handling.append(" ");
      handling.append(knappMed(t("ui.medarbeider.lukk_maaling"),
        () => ctx.lukkMaaling(m)));
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
      text: t(FUNNTEKST[f.funntype] || "ui.medarbeider.funn_ukjent") }));
    tr.append(el("td", { text: f.detalj || "–" }));
    tr.append(el("td", { text: dato(f.forst_sett) }));
    const handling = el("td", {});
    if (f.sveipens) {
      handling.append(el("span", { class: "muted",
        text: t("ui.medarbeider.lukkes_av_sveipen") }));
    } else if (ctx.kanSkrive) {
      handling.append(knappMed(t("ui.medarbeider.lukk_funn"),
        () => ctx.lukkFunn(f)));
    }
    tr.append(handling);
    ut.push(tr);
  }
  return ut;
}


// PULSGRUPPENE. DET ENESTE STEDET SVARENE VISES.
export function pulsrader(grupper) {
  const ut = [];
  for (const g of grupper || []) {
    const tr = el("tr", {});
    tr.append(el("td", { text: g.gruppe || "–" }));
    tr.append(el("td", { text: String(g.antall ?? "–") }));
    tr.append(el("td", { text: String(g.snitt ?? "–") }));
    ut.push(tr);
  }
  return ut;
}


// KRAVSKJEMAET. GULVET, IKKE TERSKELEN.
//
// `gruppeterskel_min` sier hvor lavt en tenant får sette terskelen på
// en NY måling. Terskelen som gjelder for en måling som alt er åpnet,
// ligger på målingen og kan ikke endres av noen.
export function kravskjema(ctx, last, kvitter, s) {
  const terskel = el("input", { id: "md-terskel", type: "number",
                                min: "5", max: "1000",
                                required: "required",
                                value: String(s.gruppeterskel_min ?? 5) });
  const frist = el("input", { id: "md-frist", type: "number", min: "1",
                              max: "3650", required: "required",
                              value: String(s.apent_lop_frist_dogn ?? 14) });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.medarbeider.lagre_krav") });
  const skjema = el("form", { class: "skjema" },
    felt("md-terskel", "ui.medarbeider.felt_terskel", terskel,
         "ui.medarbeider.hjelp_terskel"),
    felt("md-frist", "ui.medarbeider.felt_frist", frist,
         "ui.medarbeider.hjelp_frist"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.medarbeider.krav_lagret",
    send: (idem) => settMedarbeiderkrav({
      gruppeterskel_min: Number(terskel.value),
      apent_lop_frist_dogn: Number(frist.value),
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.medarbeider.krav") }),
    el("p", { class: "muted", text: t("ui.medarbeider.krav_hjelp") }),
    skjema);
}


// LØPSSKJEMAET. `taker_id` SLÅS OPP, DET FINNES IKKE OPP.
//
// Feltet er en id fra lønnsregisteret, ikke et navn: «jobber hun her»
// besvares ett sted i huset, og det er M-39s register.
export function lopskjema(ctx, last, kvitter, s) {
  const taker = el("input", { id: "md-taker", type: "text",
                              maxlength: "36", required: "required" });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.medarbeider.start_lop") });
  const skjema = el("form", { class: "skjema" },
    felt("md-taker", "ui.medarbeider.felt_taker", taker,
         "ui.medarbeider.hjelp_taker"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.medarbeider.lop_startet",
    tilbakestill: () => { taker.value = ""; },
    send: (idem) => startLop({
      taker_id: taker.value.trim(),
      kravversjon: s.kravversjon,
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.medarbeider.nytt_lop") }),
    el("p", { class: "muted", text: t("ui.medarbeider.lop_hjelp") }),
    skjema);
}


// KONTRAKTSKJEMAET. MALEN MÅ VÆRE PUBLISERT.
//
// Feltene er en ansatt, en malversjon og hvilke av malens felter som
// ble fylt. VERDIENE SPØRRES DET IKKE OM: en kontraktverdi er
// persondata, og v1 har ingen grunn til å eie den.
export function kontraktskjema(ctx, last, kvitter) {
  const taker = el("input", { id: "md-ktaker", type: "text",
                              maxlength: "36", required: "required" });
  const mal = el("input", { id: "md-mal", type: "text", maxlength: "36",
                            required: "required" });
  const felter = el("input", { id: "md-felter", type: "text",
                               maxlength: "2000", required: "required",
                               value: "stilling, startdato" });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.medarbeider.utsted") });
  const skjema = el("form", { class: "skjema" },
    felt("md-ktaker", "ui.medarbeider.felt_taker", taker),
    felt("md-mal", "ui.medarbeider.felt_mal", mal,
         "ui.medarbeider.hjelp_mal"),
    felt("md-felter", "ui.medarbeider.felt_felter", felter,
         "ui.medarbeider.hjelp_felter"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.medarbeider.kontrakt_utstedt",
    send: (idem) => utstedKontrakt({
      taker_id: taker.value.trim(),
      malversjon_id: mal.value.trim(),
      feltnokler: felter.value.split(",")
        .map((x) => x.trim()).filter(Boolean),
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.medarbeider.ny_kontrakt") }),
    el("p", { class: "muted", text: t("ui.medarbeider.kontrakt_hjelp") }),
    skjema);
}


// MÅLINGSSKJEMAET. TERSKELEN LÅSES HER, ÉN GANG.
//
// Hjelpeteksten sier det rett ut, fordi det er den ene innstillingen i
// modulen som ikke kan angres: etterpå har ingen retten til å skrive
// kolonnen.
export function maalingsskjema(ctx, last, kvitter, s) {
  const tittel = el("input", { id: "md-tittel", type: "text",
                               maxlength: "200", required: "required" });
  const terskel = el("input", { id: "md-mterskel", type: "number",
                                min: String(s.gruppeterskel_min ?? 5),
                                max: "1000", required: "required",
                                value: String(s.gruppeterskel_min ?? 5) });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.medarbeider.apne_maaling") });
  const skjema = el("form", { class: "skjema" },
    felt("md-tittel", "ui.medarbeider.felt_tittel", tittel),
    felt("md-mterskel", "ui.medarbeider.felt_mterskel", terskel,
         "ui.medarbeider.hjelp_mterskel"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.medarbeider.maaling_apnet",
    tilbakestill: () => { tittel.value = ""; },
    send: (idem) => apneMaaling({
      tittel: tittel.value,
      gruppeterskel: Number(terskel.value),
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.medarbeider.ny_maaling") }),
    el("p", { class: "muted", text: t("ui.medarbeider.maaling_hjelp") }),
    skjema);
}


// PANELENE SOM ÅPNES FRA EN RAD.
function stegpanel(ctx, last, kvitter) {
  const stegnr = el("input", { id: "md-stegnr", type: "number", min: "1",
                               max: "100", required: "required",
                               value: "1" });
  const stegtype = velger("md-stegtype", STEGTYPER, STEGTEKST);
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.medarbeider.steg_bekreft") });
  const skjema = el("form", { class: "skjema" },
    felt("md-stegnr", "ui.medarbeider.felt_stegnr", stegnr),
    felt("md-stegtype", "ui.medarbeider.felt_stegtype", stegtype),
    knapp, utfall);
  const node = el("section", { class: "kpi-kort", hidden: "hidden" },
    el("h2", { text: t("ui.medarbeider.steg_tittel") }), skjema);
  let valgt = null;
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.medarbeider.steg_registrert",
    tilbakestill: () => { valgt = null; node.hidden = true; },
    send: (idem) => utfoerSteg(valgt.lop_id, {
      stegnr: Number(stegnr.value), stegtype: stegtype.value,
    }, idem),
  });
  return {
    node,
    aapne: (rad) => { valgt = rad; node.hidden = false; stegnr.focus(); },
  };
}


function avsluttpanel(ctx, last, kvitter) {
  const status = velger("md-avslutning", AVSLUTNINGER, AVSLUTNINGSTEKST);
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.medarbeider.avslutt_bekreft") });
  const skjema = el("form", { class: "skjema" },
    felt("md-avslutning", "ui.medarbeider.felt_avslutning", status,
         "ui.medarbeider.hjelp_avslutning"),
    knapp, utfall);
  const node = el("section", { class: "kpi-kort", hidden: "hidden" },
    el("h2", { text: t("ui.medarbeider.avslutt_tittel") }), skjema);
  let valgt = null;
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.medarbeider.lop_avsluttet",
    tilbakestill: () => { valgt = null; node.hidden = true; },
    send: (idem) => avsluttLop(valgt.lop_id,
                               { status: status.value }, idem),
  });
  return {
    node,
    aapne: (rad) => { valgt = rad; node.hidden = false; status.focus(); },
  };
}


// PULSPANELET. DØRA SOM IKKE TAR IMOT HVEM SOM SVARER.
//
// Skjemaet har to felter: gruppe og verdi. DET ER IKKE ET TREDJE FELT
// SOM ER SKJULT — det finnes ingen personnøkkel å sende, fordi det
// ikke finnes noen kolonne å skrive den i.
function pulspanel(ctx, last, kvitter) {
  const gruppe = el("input", { id: "md-gruppe", type: "text",
                               maxlength: "100", required: "required" });
  const verdi = el("input", { id: "md-verdi", type: "number", min: "1",
                              max: "5", required: "required",
                              value: "4" });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.medarbeider.puls_send") });
  const skjema = el("form", { class: "skjema" },
    felt("md-gruppe", "ui.medarbeider.felt_gruppe", gruppe,
         "ui.medarbeider.hjelp_gruppe"),
    felt("md-verdi", "ui.medarbeider.felt_verdi", verdi,
         "ui.medarbeider.hjelp_verdi"),
    knapp, utfall);
  const node = el("section", { class: "kpi-kort", hidden: "hidden" },
    el("h2", { text: t("ui.medarbeider.puls_tittel") }),
    el("p", { class: "muted", text: t("ui.medarbeider.puls_anonym") }),
    skjema);
  let valgt = null;
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okTekst: () => pulskvittering(),
    okNokkel: "ui.medarbeider.puls_mottatt",
    tilbakestill: () => { valgt = null; node.hidden = true; },
    send: (idem) => avgiPuls(valgt.maaling_id, {
      gruppe: gruppe.value, verdi: Number(verdi.value),
    }, idem),
  });
  return {
    node,
    aapne: (rad) => { valgt = rad; node.hidden = false; gruppe.focus(); },
  };
}


function lukkepanel(ctx, last, kvitter, art) {
  const grunn = el("textarea", { id: `md-${art}grunn`, rows: "2",
                                 maxlength: "8000",
                                 required: "required" });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.medarbeider.lukk_bekreft") });
  const skjema = el("form", { class: "skjema" },
    felt(`md-${art}grunn`, "ui.medarbeider.felt_grunn", grunn,
         "ui.medarbeider.hjelp_grunn"),
    knapp, utfall);
  const node = el("section", { class: "kpi-kort", hidden: "hidden" },
    el("h2", { text: t("ui.medarbeider.lukk_funn_tittel") }), skjema);
  let valgt = null;
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.medarbeider.funn_lukket",
    tilbakestill: () => { grunn.value = ""; valgt = null; node.hidden = true; },
    send: (idem) => lukkMedarbeiderfunn(valgt.funn_id,
                                        { lukkegrunn: grunn.value }, idem),
  });
  return {
    node,
    aapne: (rad) => { valgt = rad; node.hidden = false; grunn.focus(); },
  };
}


// PULSVISNINGEN. ET TOMT SVAR ER ET GYLDIG SVAR.
function pulsvisning() {
  const kropp = el("div", {});
  const node = el("section", { class: "kpi-kort", hidden: "hidden" },
    el("h2", { text: t("ui.medarbeider.pulsbilde") }), kropp);
  return {
    node,
    vis: async (rad) => {
      node.hidden = false;
      sett(kropp, el("p", { class: "muted",
                            text: t("ui.medarbeider.laster") }));
      let d;
      try {
        d = await hentJson(
          `/v1/medarbeider/maaling/${encodeURIComponent(rad.maaling_id)}/puls`);
      } catch (e) {
        const detalj = e && Array.isArray(e.detaljer) && e.detaljer.length
          ? String(e.detaljer[0]) : null;
        sett(kropp, el("p", { role: "alert",
                              text: t("ui.medarbeider.feil.generell") }));
        if (detalj) {
          kropp.append(" ", el("span", { class: "muted", text: detalj }));
        }
        return;
      }
      const grupper = d.grupper || [];
      if (!grupper.length) {
        // FOR FÅ HAR SVART, OG FLATEN SIER IKKE HVOR FÅ.
        //
        // Tallet ville i seg selv vært det terskelen verner: «to av
        // fem har svart» forteller nøyaktig så mye om en gruppe på fem
        // som aggregatet nekter å si.
        sett(kropp, el("p", { class: "muted",
          text: t("ui.medarbeider.puls_for_faa")
            .replace("{terskel}", String(rad.gruppeterskel ?? "–")) }));
        return;
      }
      sett(kropp, el("p", { class: "muted",
        text: t("ui.medarbeider.puls_forklaring")
          .replace("{terskel}", String(rad.gruppeterskel ?? "–")) }),
        tabell(["ui.medarbeider.kol_gruppe", "ui.medarbeider.kol_antall",
                "ui.medarbeider.kol_snitt"], pulsrader(grupper)));
    },
  };
}


export function visMedarbeider(hoved, ctx) {
  const hode = () => flateHode(t("ui.medarbeider.tittel"),
    t("ui.medarbeider.undertittel"));
  sett(hoved, ...hode());
  // KVITTERINGEN LEVER UTENFOR `kropp` (138s lærdom): `last()` bygger
  // `kropp` på nytt, og en kvittering skrevet inn der ville aldri blitt
  // sett av mennesket den var til.
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/medarbeider"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const skriver = harScope(ctx, "bestilling:opprett");
      // PANELENE BYGGES BARE FOR DEN SOM KAN SKRIVE.
      //
      // Et skjult skjema en leser aldri kan åpne er ikke en risiko —
      // API-et håndhever scopet uansett — men det er heller ikke
      // ingenting: felter i DOM-en som ingen vei fører til, er noe en
      // senere leser må bevise er utilgjengelige. Bygger vi dem ikke,
      // finnes de ikke.
      //
      // `pulsvisning` er UNNTAKET og bygges alltid: å SE et aggregat
      // er en lesing, og den skal en leser få gjøre.
      const steg = skriver ? stegpanel(ctx, last, kvitter) : null;
      const avslutt = skriver ? avsluttpanel(ctx, last, kvitter) : null;
      const puls = skriver ? pulspanel(ctx, last, kvitter) : null;
      const lukking = skriver ? lukkepanel(ctx, last, kvitter, "lukk") : null;
      const bilde = pulsvisning();
      const kontekst = {
        kanSkrive: skriver,
        steg: (l) => steg && steg.aapne(l),
        avslutt: (l) => avslutt && avslutt.aapne(l),
        svarPuls: (m) => puls && puls.aapne(m),
        visPuls: (m) => bilde.vis(m),
        lukkMaaling: async (m) => {
          try {
            await lukkMaaling(m.maaling_id, {}, nyIdempotensnokkel());
          } catch (e) {
            if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
            kvitter(t("ui.medarbeider.feil.generell"));
            return;
          }
          kvitter(t("ui.medarbeider.maaling_lukket"));
          meldLive(t("ui.medarbeider.maaling_lukket"));
          await last();
        },
        lukkFunn: (f) => lukking && lukking.aapne(f),
      };

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.medarbeider.sammendrag_tittel") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT.
        el("p", { class: "muted", text: t("ui.medarbeider.hvorfor") }));

      const funnseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.medarbeider.funn") }));
      if (!(d.funn || []).length) {
        funnseksjon.append(el("p", { class: "muted",
          text: t("ui.medarbeider.funn_tomt") }));
      } else {
        funnseksjon.append(tabell(
          ["ui.medarbeider.kol_funntype", "ui.medarbeider.kol_detalj",
           "ui.medarbeider.kol_forst_sett", "ui.medarbeider.kol_handling"],
          funnrader(d, kontekst)));
      }

      const maalingsseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.medarbeider.maalinger") }));
      if (!(d.maalinger || []).length) {
        maalingsseksjon.append(el("p", { class: "muted",
          text: t("ui.medarbeider.maalinger_tomt") }));
      } else {
        maalingsseksjon.append(tabell(
          ["ui.medarbeider.kol_tittel", "ui.medarbeider.kol_terskel",
           "ui.medarbeider.kol_lesbare", "ui.medarbeider.kol_lukket",
           "ui.medarbeider.kol_handling"],
          maalingsrader(d, kontekst)));
      }

      const lopseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.medarbeider.lop") }));
      if (!(d.lop || []).length) {
        lopseksjon.append(el("p", { class: "muted",
          text: t("ui.medarbeider.lop_tomt") }));
      } else {
        lopseksjon.append(tabell(
          ["ui.medarbeider.kol_ansatt", "ui.medarbeider.kol_status",
           "ui.medarbeider.kol_framdrift", "ui.medarbeider.kol_startet",
           "ui.medarbeider.kol_handling"],
          loprader(d, kontekst)));
      }

      const kontraktseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.medarbeider.kontrakter") }));
      if (!(d.kontrakter || []).length) {
        kontraktseksjon.append(el("p", { class: "muted",
          text: t("ui.medarbeider.kontrakter_tomt") }));
      } else {
        kontraktseksjon.append(tabell(
          ["ui.medarbeider.kol_ansatt", "ui.medarbeider.kol_mal",
           "ui.medarbeider.kol_malstatus", "ui.medarbeider.kol_felt",
           "ui.medarbeider.kol_utstedt"],
          kontraktrader(d)));
      }

      const deler = [oversikt, funnseksjon, lukking && lukking.node,
                     maalingsseksjon, bilde.node, puls && puls.node,
                     lopseksjon, steg && steg.node, avslutt && avslutt.node,
                     kontraktseksjon].filter(Boolean);
      if (skriver) {
        if (s.har_krav) {
          deler.push(lopskjema(ctx, last, kvitter, s),
                     maalingsskjema(ctx, last, kvitter, s),
                     kontraktskjema(ctx, last, kvitter));
        } else {
          deler.push(el("section", { class: "kpi-kort" },
            el("h2", { text: t("ui.medarbeider.nytt_lop") }),
            el("p", { class: "muted",
                      text: t("ui.medarbeider.lop_uten_krav") })));
        }
        deler.push(kravskjema(ctx, last, kvitter, s));
      }
      sett(kropp, ...deler);
    });

  return last();
}
