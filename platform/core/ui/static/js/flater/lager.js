// Lager- og logistikkagenten (M-27 v1) — BEHOLDNINGEN.
//
// FLATENS VIKTIGSTE JOBB er å svare på HVORFOR DET STÅR 7 HER.
// Beholdningen er summen av bevegelser, og hovedboken med den løpende
// summen på hver linje er derfor en førsteklasses skjerm, ikke en
// detalj. `lager_reservert` er en attestasjon om et tall, og den er
// verdiløs hvis ingen kan spore tallet.
//
// DET FINNES INGEN «BESTILL PÅFYLL»-KNAPP, og fraværet er dommen: to av
// tre bransjemaler navngir modulen som `v_lager` og bruker
// `lager_reservert` til å la `lager.bestill_pafyll` gå automatisk. En
// bestilling binder virksomheten økonomisk; v1 skriver funnet.
//
// OG DEN VISER INGEN PROGNOSE. Ingen forbruksrate, ingen «varer i N
// døgn», ingen ekstrapolering — `prognose_konfidens` uten en målt
// treffrate bak seg er et tall som ser ut som kunnskap.
//
// DET FINNES HELLER INGEN «SETT BEHOLDNING». En telling sender det
// TALTE antallet, og basen skriver differansen som en linje.
//
// ANTALL ER HELTALL i varens egen enhet; beløp er heltall i øre (101s
// form, ordrett).
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, hentJson, nyIdempotensnokkel, registrerBevegelse,
  registrerTelling, registrerVare, settBestillingspunkt,
  settLagerterskler, settVareAktiv,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// FUNNTYPE → MERKETEKST. Lukket tabell, ikke strengbygging.
const MERKE = {
  under_bestillingspunkt: "ui.lager.merke_under_punkt",
  uten_bestillingspunkt: "ui.lager.merke_uten_punkt",
  uten_bevegelse: "ui.lager.merke_stille",
  ikke_talt: "ui.lager.merke_ikke_talt",
  ingen_terskel: "ui.lager.merke_uten_terskel",
};

// BEVEGELSESTYPENE, lukket sett. `telling` står ikke her: den har sin
// egen dør, fordi den ikke er en bevegelse noen observerte.
export const BEVEGELSESTYPER = ["mottak", "uttak", "retur", "svinn"];

const TYPETEKST = {
  mottak: "ui.lager.type.mottak",
  uttak: "ui.lager.type.uttak",
  retur: "ui.lager.type.retur",
  svinn: "ui.lager.type.svinn",
  telling: "ui.lager.type.telling",
};

// ANTALL ER HELTALL I VARENS EGEN ENHET. Et desimaltall her ville vært
// en beholdning som drev fra sannheten én bevegelse av gangen.
export function antallTekst(antall, enhet) {
  if (typeof antall !== "number" || !Number.isInteger(antall)) return "—";
  return t("ui.lager.antall").replace("{antall}", String(antall))
    .replace("{enhet}", enhet || "");
}

// FORTEGNET SKAL SES. En bevegelse på -60 er noe annet enn 60.
export function endringTekst(endring, enhet) {
  if (typeof endring !== "number" || !Number.isInteger(endring)) return "—";
  const fortegn = endring > 0 ? "+" : "";
  return t("ui.lager.antall")
    .replace("{antall}", `${fortegn}${endring}`)
    .replace("{enhet}", enhet || "");
}

// BELØP I HELTALLSARITMETIKK, aldri via `/100`.
export function belopTekst(ore) {
  if (typeof ore !== "number" || !Number.isInteger(ore)) return "—";
  const neg = ore < 0;
  const a = Math.abs(ore);
  return `${neg ? "-" : ""}${Math.trunc(a / 100)},`
    + `${String(a % 100).padStart(2, "0")}`;
}

// PUNKTETS ORD. INGEN PUNKT ER IKKE «0»: «vi holder ikke lager på
// denne» og «ingen har satt et punkt» er to helt forskjellige svar, og
// det siste er nettopp det `uten_bestillingspunkt` finnes for å
// avsløre.
export function punktTekst(antall, enhet) {
  if (typeof antall !== "number" || !Number.isInteger(antall)) {
    return t("ui.lager.uten_punkt");
  }
  return antallTekst(antall, enhet);
}

// DØGN SIDEN SIST, som ORD. ENTALL HAR SIN EGEN NØKKEL.
export function dognTekst(dogn) {
  if (typeof dogn !== "number" || !Number.isInteger(dogn)) return "—";
  if (dogn === 0) return t("ui.lager.i_dag");
  return dogn === 1
    ? t("ui.lager.ett_dogn_siden")
    : t("ui.lager.dogn_siden").replace("{dogn}", String(dogn));
}

// ANTALL INN, HELTALL UT. Et desimaltall er IKKE et antall enheter, og
// flaten runder det ikke bort — den nekter.
//
// ET TOMT FELT ER HELLER IKKE NULL ENHETER. `Number("")` er 0 i
// JavaScript, og uten denne linja ville et tomt antall blitt sendt som
// en telling på null — altså en beholdning nullstilt av en utelatelse.
export function tilAntall(verdi) {
  if (typeof verdi !== "number" && !String(verdi ?? "").trim()) {
    return null;
  }
  const n = Number(verdi);
  if (!Number.isInteger(n) || n < 0) return null;
  return n;
}

// KRONER INN, ØRE UT. `Math.round` på produktet er den ENESTE veien.
export function tilOre(verdi) {
  if (verdi === "" || verdi === null || verdi === undefined) return null;
  const n = Number(verdi);
  if (!Number.isFinite(n)) return null;
  return Math.round(n * 100);
}

function varerad(v, apneDetalj) {
  const rad = el("tr", {});
  // KODEN NAVNGIR raden — det er den et uttak siterer.
  rad.append(el("th", { scope: "row", class: "celle-id", text: v.kode }));
  rad.append(el("td", { class: "celle-tekst", text: v.navn }));
  rad.append(el("td", { class: "celle-tall",
    text: antallTekst(v.beholdning, v.enhet) }));
  rad.append(el("td", { class: "celle-tall",
    text: punktTekst(v.punkt_antall, v.enhet) }));
  rad.append(el("td", { text: dognTekst(v.dogn_siden_bevegelse) }));
  rad.append(el("td", { text: dognTekst(v.dogn_siden_telling) }));

  const merkecelle = el("td", {},
    el("span", { text: v.aktiv ? t("ui.lager.status.aktiv")
                               : t("ui.lager.status.inaktiv") }));
  for (const funn of v.apne_funn || []) {
    if (!MERKE[funn]) continue;
    // MERKET ER TEKST (WCAG 1.4.1).
    merkecelle.append(" ", el("strong", { class: "merke",
                                          text: t(MERKE[funn]) }));
  }
  rad.append(merkecelle);

  const handling = el("td", {});
  const knapp = el("button", { type: "button",
    text: t("ui.lager.knapp.apne") });
  knapp.addEventListener("click", () => apneDetalj(v));
  handling.append(knapp);
  rad.append(handling);
  return rad;
}

function vareTabell(varer, apneDetalj) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.lager.liste.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.lager.kolonne.kode") }),
    el("th", { scope: "col", text: t("ui.lager.kolonne.navn") }),
    el("th", { scope: "col", text: t("ui.lager.kolonne.beholdning") }),
    el("th", { scope: "col", text: t("ui.lager.kolonne.punkt") }),
    el("th", { scope: "col", text: t("ui.lager.kolonne.bevegelse") }),
    el("th", { scope: "col", text: t("ui.lager.kolonne.telling") }),
    el("th", { scope: "col", text: t("ui.lager.kolonne.status") }),
    el("th", { scope: "col", text: t("ui.lager.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const v of varer) tbody.append(varerad(v, apneDetalj));
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function terskelTabell(terskler) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.lager.terskel.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.lager.kolonne.terskel") }),
    el("th", { scope: "col", text: t("ui.lager.kolonne.verdi") }))));
  const tbody = el("tbody");
  const linjer = [
    ["ui.lager.terskel.stille", terskler.stille_dogn],
    ["ui.lager.terskel.punkt", terskler.uten_punkt_dogn],
    ["ui.lager.terskel.telle", terskler.telleintervall_dogn],
  ];
  for (const [nokkel, verdi] of linjer) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: t(nokkel) }),
      el("td", { class: "celle-tall",
        text: t("ui.lager.dogn").replace("{dogn}", String(verdi)) })));
  }
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
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
          ? t("ui.lager.feil.tilstand")
          : t("ui.lager.feil.generell") }));
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

// DETALJPANELET: hovedboken, og de fire handlingene.
function detaljpanel(ctx, last, kvitter, settApen) {
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;

  const merkelinje = el("p", { class: "muted" });
  const hovedbok = el("div", {});
  // ÅPNINGSTELLEREN (app.js sin `varseltallNr`-form). Åpner noen vare B
  // mens As hovedbok fortsatt er underveis, ville As svar ellers blitt
  // tegnet inn i Bs panel — altså en beholdning som ser ut til å høre
  // til en annen vare. I dette registeret er det ikke en kosmetisk feil.
  let apningsnr = 0;

  // --- bevegelse ---
  const bSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const bType = el("select", { id: "lg-bev-type", name: "type",
    required: true });
  for (const type of BEVEGELSESTYPER) {
    bType.append(el("option", { value: type, text: t(TYPETEKST[type]) }));
  }
  const bAntall = el("input", { id: "lg-bev-antall", name: "antall",
    type: "number", required: true, step: "1", min: "1" });
  const bKost = el("input", { id: "lg-bev-kost", name: "kost",
    type: "number", step: "0.01", min: "0" });
  const bDato = el("input", { id: "lg-bev-dato", name: "utfort",
    type: "date", required: true });
  const bNotat = el("input", { id: "lg-bev-notat", name: "notat",
    type: "text", required: true, maxlength: 2000 });
  const bKnapp = el("button", { type: "submit",
    text: t("ui.lager.knapp.ny_bevegelse") });
  bSkjema.append(
    felt("lg-bev-type", "ui.lager.skjema.type", bType),
    felt("lg-bev-antall", "ui.lager.skjema.antall", bAntall,
         "ui.lager.skjema.antall_hjelp"),
    felt("lg-bev-kost", "ui.lager.skjema.kost", bKost,
         "ui.lager.skjema.kost_hjelp"),
    felt("lg-bev-dato", "ui.lager.skjema.utfort", bDato,
         "ui.lager.skjema.utfort_hjelp"),
    felt("lg-bev-notat", "ui.lager.skjema.notat", bNotat,
         "ui.lager.skjema.notat_hjelp"),
    el("div", { class: "skjema-bunn" }, bKnapp));
  skjemaramme(ctx, last, {
    skjema: bSkjema, knapp: bKnapp, utfall, kvitter,
    okNokkel: "ui.lager.skjema.bevegelse_ok",
    // ANTALLET ER EN STØRRELSE. Fortegnet følger av typen, i basen.
    send: (idem) => registrerBevegelse(gjeldende.vare_id, {
      bevegelsestype: bType.value, antall: tilAntall(bAntall.value),
      enhetskost_ore: tilOre(bKost.value), utfort: bDato.value,
      notat: bNotat.value,
    }, idem),
    tilbakestill: () => {
      bAntall.value = ""; bKost.value = ""; bNotat.value = "";
    },
  });

  // --- telling ---
  const tSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const tAntall = el("input", { id: "lg-tell-antall", name: "talt",
    type: "number", required: true, step: "1", min: "0" });
  const tDato = el("input", { id: "lg-tell-dato", name: "utfort",
    type: "date", required: true });
  const tNotat = el("input", { id: "lg-tell-notat", name: "notat",
    type: "text", required: true, maxlength: 2000 });
  const tKnapp = el("button", { type: "submit",
    text: t("ui.lager.knapp.ny_telling") });
  tSkjema.append(
    felt("lg-tell-antall", "ui.lager.skjema.talt", tAntall,
         "ui.lager.skjema.talt_hjelp"),
    felt("lg-tell-dato", "ui.lager.skjema.utfort", tDato),
    felt("lg-tell-notat", "ui.lager.skjema.notat", tNotat),
    el("div", { class: "skjema-bunn" }, tKnapp));
  skjemaramme(ctx, last, {
    skjema: tSkjema, knapp: tKnapp, utfall, kvitter,
    okNokkel: "ui.lager.skjema.telling_ok",
    // FLATEN SETTER INGEN BEHOLDNING. Den sender det TALTE antallet, og
    // basen skriver differansen som en linje i hovedboken.
    send: (idem) => registrerTelling(gjeldende.vare_id, {
      talt_antall: tilAntall(tAntall.value), utfort: tDato.value,
      notat: tNotat.value,
    }, idem),
    tilbakestill: () => { tAntall.value = ""; tNotat.value = ""; },
  });

  // --- bestillingspunkt ---
  const pSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const pAntall = el("input", { id: "lg-punkt-antall", name: "punkt",
    type: "number", required: true, step: "1", min: "0" });
  const pFra = el("input", { id: "lg-punkt-fra", name: "gyldig_fra",
    type: "date", required: true });
  const pGrunn = el("input", { id: "lg-punkt-grunn", name: "begrunnelse",
    type: "text", required: true, maxlength: 2000 });
  const pKnapp = el("button", { type: "submit",
    text: t("ui.lager.knapp.nytt_punkt") });
  pSkjema.append(
    felt("lg-punkt-antall", "ui.lager.skjema.punkt", pAntall,
         "ui.lager.skjema.punkt_hjelp"),
    felt("lg-punkt-fra", "ui.lager.skjema.gyldig_fra", pFra,
         "ui.lager.skjema.gyldig_fra_hjelp"),
    felt("lg-punkt-grunn", "ui.lager.skjema.begrunnelse", pGrunn,
         "ui.lager.skjema.begrunnelse_hjelp"),
    el("div", { class: "skjema-bunn" }, pKnapp));
  skjemaramme(ctx, last, {
    skjema: pSkjema, knapp: pKnapp, utfall, kvitter,
    okNokkel: "ui.lager.skjema.punkt_ok",
    send: (idem) => settBestillingspunkt(gjeldende.vare_id, {
      punkt_antall: tilAntall(pAntall.value), gyldig_fra: pFra.value,
      begrunnelse: pGrunn.value,
    }, idem),
    tilbakestill: () => { pAntall.value = ""; pGrunn.value = ""; },
  });

  // --- aktiv/inaktiv ---
  const aSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const aKnapp = el("button", { type: "submit",
    text: t("ui.lager.knapp.deaktiver") });
  aSkjema.append(
    el("p", { class: "muted", text: t("ui.lager.skjema.aktiv_hjelp") }),
    el("div", { class: "skjema-bunn" }, aKnapp));
  skjemaramme(ctx, last, {
    skjema: aSkjema, knapp: aKnapp, utfall, kvitter,
    okNokkel: "ui.lager.skjema.aktiv_ok",
    send: (idem) => settVareAktiv(gjeldende.vare_id, !gjeldende.aktiv,
                                  idem),
    tilbakestill: () => { settApen(null); innhold.hidden = true; },
  });

  const skriver = harScope(ctx, "bestilling:opprett");
  innhold.append(el("h3", { text: t("ui.lager.detalj.tittel") }),
    merkelinje, hovedbok);
  if (skriver) {
    innhold.append(
      el("h4", { text: t("ui.lager.skjema.bevegelse_tittel") }), bSkjema,
      el("h4", { text: t("ui.lager.skjema.telling_tittel") }), tSkjema,
      el("h4", { text: t("ui.lager.skjema.punkt_tittel") }), pSkjema,
      el("h4", { text: t("ui.lager.skjema.aktiv_tittel") }), aSkjema);
  }
  boks.append(innhold, utfall);
  innhold.hidden = true;

  function hovedbokTabell(rader, enhet) {
    const tb = el("table", { class: "kpi-tabell" },
      el("caption", { text: t("ui.lager.hovedbok.caption") }));
    tb.append(el("thead", {}, el("tr", {},
      el("th", { scope: "col", text: t("ui.lager.kolonne.utfort") }),
      el("th", { scope: "col", text: t("ui.lager.kolonne.type") }),
      el("th", { scope: "col", text: t("ui.lager.kolonne.endring") }),
      el("th", { scope: "col", text: t("ui.lager.kolonne.etter") }),
      el("th", { scope: "col", text: t("ui.lager.kolonne.kost") }),
      el("th", { scope: "col", text: t("ui.lager.kolonne.notat") }),
      el("th", { scope: "col", text: t("ui.lager.kolonne.fort_av") }))));
    const tbody = el("tbody");
    for (const b of rader) {
      const rad = el("tr", {});
      rad.append(el("th", { scope: "row", class: "celle-id",
                            text: b.utfort }));
      rad.append(el("td", { text: t(TYPETEKST[b.bevegelsestype]
                                    || "ui.lager.type.ukjent") }));
      rad.append(el("td", { class: "celle-tall",
                            text: endringTekst(b.endring, enhet) }));
      // DEN LØPENDE BEHOLDNINGEN STÅR PÅ HVER LINJE. En leser som måtte
      // summere selv ville ikke kunne se hvor tallet kom fra — og det
      // er hele spørsmålet hovedboken finnes for å svare på.
      rad.append(el("td", { class: "celle-tall",
                            text: antallTekst(b.beholdning_etter,
                                              enhet) }));
      rad.append(el("td", { class: "celle-tall",
        text: typeof b.enhetskost_ore === "number"
          ? belopTekst(b.enhetskost_ore) : "—" }));
      rad.append(el("td", { class: "celle-tekst", text: b.notat }));
      rad.append(el("td", { class: "muted", text: b.registrert_av }));
      tbody.append(rad);
    }
    tb.append(tbody);
    return el("div", { class: "tablewrap" }, tb);
  }

  return {
    node: boks,
    async apne(v) {
      const nr = ++apningsnr;
      gjeldende = v;
      settApen(v.vare_id);
      sett(utfall);
      sett(hovedbok);
      merkelinje.textContent = `${v.kode} · ${v.navn} · ${v.enhet}`;
      aKnapp.textContent = t(v.aktiv ? "ui.lager.knapp.deaktiver"
                                     : "ui.lager.knapp.aktiver");
      // EN INAKTIV VARE TAR IKKE IMOT BEVEGELSER — men den KAN
      // aktiveres igjen, så den knappen står levende.
      bKnapp.disabled = !v.aktiv;
      tKnapp.disabled = !v.aktiv;
      innhold.hidden = false;
      let d;
      try {
        d = await hentJson(
          `/v1/lager/${encodeURIComponent(v.vare_id)}/bevegelser`);
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        if (nr !== apningsnr) return;
        sett(hovedbok, el("p", { class: "muted",
          text: t("ui.lager.feil.generell") }));
        return;
      }
      if (nr !== apningsnr) return;
      const liste = d.bevegelser || [];
      if (!liste.length) {
        sett(hovedbok, el("p", { class: "muted",
          text: t("ui.lager.detalj.ingen") }));
        return;
      }
      sett(hovedbok, hovedbokTabell(liste, v.enhet));
    },
  };
}

function vareSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const kode = el("input", { id: "lg-ny-kode", name: "kode",
    type: "text", required: true, maxlength: 100 });
  const navn = el("input", { id: "lg-ny-navn", name: "navn",
    type: "text", required: true, maxlength: 300 });
  const enhet = el("input", { id: "lg-ny-enhet", name: "enhet",
    type: "text", required: true, maxlength: 60 });
  const knapp = el("button", { type: "submit",
    text: t("ui.lager.knapp.ny_vare") });
  skjema.append(
    felt("lg-ny-kode", "ui.lager.skjema.kode", kode,
         "ui.lager.skjema.kode_hjelp"),
    felt("lg-ny-navn", "ui.lager.skjema.navn", navn),
    felt("lg-ny-enhet", "ui.lager.skjema.enhet", enhet,
         "ui.lager.skjema.enhet_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.lager.skjema.vare_ok",
    send: (idem) => registrerVare({
      kode: kode.value, navn: navn.value, enhet: enhet.value }, idem),
    tilbakestill: () => {
      kode.value = ""; navn.value = ""; enhet.value = "";
    },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.lager.skjema.vare_tittel") }), skjema, utfall);
}

function terskelSkjema(ctx, last, terskler, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const stille = el("input", { id: "lg-t-stille", name: "stille",
    type: "number", required: true, step: "1", min: "0", max: "3650" });
  const punkt = el("input", { id: "lg-t-punkt", name: "punkt",
    type: "number", required: true, step: "1", min: "0", max: "3650" });
  const telle = el("input", { id: "lg-t-telle", name: "telle",
    type: "number", required: true, step: "1", min: "0", max: "3650" });
  if (terskler) {
    stille.value = String(terskler.stille_dogn);
    punkt.value = String(terskler.uten_punkt_dogn);
    telle.value = String(terskler.telleintervall_dogn);
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.lager.knapp.lagre_terskler") });
  skjema.append(
    felt("lg-t-stille", "ui.lager.terskel.stille", stille,
         "ui.lager.terskel.stille_hjelp"),
    felt("lg-t-punkt", "ui.lager.terskel.punkt", punkt,
         "ui.lager.terskel.punkt_hjelp"),
    felt("lg-t-telle", "ui.lager.terskel.telle", telle,
         "ui.lager.terskel.telle_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.lager.skjema.terskel_ok",
    send: (idem) => settLagerterskler({
      stille_dogn: tilAntall(stille.value),
      uten_punkt_dogn: tilAntall(punkt.value),
      telleintervall_dogn: tilAntall(telle.value),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.lager.terskel.tittel") }), skjema, utfall);
}

function sammendrag(s) {
  const p = el("p", {
    text: t("ui.lager.sammendrag")
      .replace("{aktive}", String(s.aktive))
      .replace("{medpunkt}", String(s.med_punkt))
      .replace("{underpunkt}", String(s.under_punkt))
      .replace("{funn}", String(s.apne_funn)) });
  if (!s.har_terskel) {
    p.append(" ", el("strong", { text: t("ui.lager.ingen_terskler") }));
  }
  if (s.vist < s.varer) {
    p.append(" ", el("strong", {
      text: t("ui.lager.avkortet").replace("{vist}", String(s.vist)) }));
  }
  return p;
}

export function visLager(hoved, ctx) {
  const hode = () => flateHode(t("ui.lager.tittel"),
    t("ui.lager.undertittel"));
  sett(hoved, ...hode());
  // KVITTERINGEN OG DEN ÅPNE RADEN LEVER UTENFOR TEGNINGEN.
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenRad = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const settApen = (id) => { apenRad = id; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/lager"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const varer = d.varer || [];
      const detalj = detaljpanel(ctx, last, kvitter, settApen);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.lager.oversikt.tittel") }),
        sammendrag(s),
        // HVORFOR REGISTERET FINNES, sagt på flaten.
        el("p", { class: "muted", text: t("ui.lager.oversikt.hvorfor") }));

      const liste = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.lager.liste.tittel") }));
      if (!varer.length) {
        liste.append(el("p", { class: "muted",
          text: t("ui.lager.liste.ingen") }));
      } else {
        liste.append(vareTabell(varer, detalj.apne));
      }

      const terskelseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.lager.terskel.tittel") }));
      if (!d.terskler) {
        terskelseksjon.append(el("p", { class: "muted",
          text: t("ui.lager.ingen_terskler") }));
      } else {
        terskelseksjon.append(
          el("p", { class: "muted",
            text: t("ui.lager.terskel.versjon")
              .replace("{versjon}", String(d.terskler.versjon)) }),
          terskelTabell(d.terskler));
      }

      const deler = [oversikt, liste, terskelseksjon, detalj.node];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(vareSkjema(ctx, last, kvitter),
                   terskelSkjema(ctx, last, d.terskler, kvitter));
      }
      sett(kropp, ...deler);
      if (apenRad) {
        const rad = varer.find((x) => x.vare_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
