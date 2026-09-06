// M-32 global lokaliserings- og skatteagent (138) — REGELVERSJONEN ER
// PRODUKTET.
//
// FLATENS VIKTIGSTE JOBB ER Å VISE HVILKEN REGEL SOM GJALDT.
//
// Klyngens delte dom: EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN
// ANGRES IKKE AV EN ROLLBACK. En innberettet mva-oppgave er hos
// skattemyndigheten; en rollback gjør den ikke usendt, bare
// uforklarlig.
//
// DERFOR STÅR REGELVERSJONEN I SAMME RAD SOM SATSEN, ALLTID. En sats
// uten versjonen den kom fra er et tall ingen kan etterprøve — og
// akseptansekravet sier «regelversjon lagres per transaksjon».
//
// BEGGE LANDENE VISES. v1s regel er at jurisdiksjonen er kjøperens
// land: riktig for fjernsalg til forbruker i EØS, feil for flere andre
// tilfeller. En flate som bare viste svaret ville gjort en forenkling
// til en sannhet.
//
// LANDREGISTERET ER SYNLIG, OG DET ER MED VILJE. Den som lurer på
// hvorfor en beregning stoppet, skal se at landet mangler en pakke —
// framfor å måtte spørre oss.
//
// DET FINNES INGEN «INNBERETT»-KNAPP OG INGEN «ENDRE SATS»-KNAPP, OG
// DET KAN IKKE FINNES. Modulen svarer på hva som gjaldt; den sender
// ingenting, og landets regler er landets.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  beregnSkatt, hentJson, lukkSkattefunn, nyIdempotensnokkel,
  settSkattekrav, UautorisertFeil,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

const AVRUNDINGSTEKST = {
  halv_opp: "ui.skatt.avrund_opp",
  halv_ned: "ui.skatt.avrund_ned",
  mot_null: "ui.skatt.avrund_null",
};

const FUNNTEKST = {
  stor_vurdering_ukontrollert: "ui.skatt.funn_ukontrollert",
  landpakke_utloper_snart: "ui.skatt.funn_utloper",
  landpakke_uten_sats: "ui.skatt.funn_uten_sats",
  jurisdiksjon_uten_pakke: "ui.skatt.funn_uten_pakke",
  krav_mangler: "ui.skatt.funn_krav_mangler",
  transaksjon_uten_jurisdiksjon: "ui.skatt.funn_uten_jurisdiksjon",
  sats_uten_regelversjon: "ui.skatt.funn_uten_regelversjon",
  sats_uten_komplett_landpakke: "ui.skatt.funn_ukomplett",
  landpakke_endret_gjennom_dor: "ui.skatt.funn_endret",
};


export function dato(iso) {
  if (typeof iso !== "string" || !iso) return "–";
  return iso.slice(0, 10);
}


// DAGENS DATO I BRUKERENS EGEN SONE, IKKE I UTC.
//
// `new Date().toISOString().slice(0, 10)` gir UTC-datoen. Norge ligger
// FORAN UTC, så mellom midnatt og 01/02 om natten gir den GÅRSDAGEN —
// og en transaksjon datert «i dag» ville blitt regnet mot gårsdagens
// landpakke. Arvet fra 133/135/137, der CodeRabbit fant den 5/9.
export function iDagLokal(naa) {
  const d = naa || new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}


// ØRE TIL LESBART BELØP, MED VALUTAEN.
//
// Heltall hele veien: flyttall og skatt hører ikke sammen. Delingen
// skjer bare i VISNINGEN, og desimalene er landets.
export function belop(ore, valuta, desimaler) {
  if (typeof ore !== "number") return "–";
  const d = typeof desimaler === "number" ? desimaler : 2;
  const faktor = 10 ** d;
  const tall = (ore / faktor).toFixed(d);
  return valuta ? `${tall} ${valuta}` : tall;
}


// SATSEN MED VERSJONEN SIN, ALDRI ALENE.
//
// «25 %» sier ingenting om hvilken regel som ga den. «25 % · NO v1»
// kan slås opp.
export function satstekst(rad) {
  if (!rad || typeof rad.promille !== "number") return "–";
  const p = (rad.promille / 10).toFixed(1).replace(/\.0$/, "");
  return t("ui.skatt.sats_verdi")
    .replace("{p}", p)
    .replace("{land}", rad.jurisdiksjon || "–")
    .replace("{v}", String(rad.regelversjon ?? "–"));
}


// KVITTERINGEN FOR EN BEREGNING — JURISDIKSJONEN, VERSJONEN, SATSEN
// OG BELØPET.
//
// Alle fire er ting kalleren IKKE oppga: hun ga en adresseversjon og
// en satskode, og fikk landet og promillen tilbake. Derfor er dette
// den ene teksten i flaten som må være riktig.
//
// DESIMALENE ER LANDETS, IKKE TO. Her sto `2` hardkodet til CodeRabbit
// fant det — og det ville undergravd nettopp den kolonnen landpakken
// har for å bære dem: JPY har null, og 1234 yen ville stått som
// «12.34 JPY».
//
// Egen funksjon FORDI DEN SKAL KUNNE MÅLES. Da porten prøvde å lese
// teksten ut av skjermen, målte den timingen i to nettverksrunder
// framfor innholdet — og en port som er grønn eller rød etter timing
// måler ikke det den sier.
export function kvitteringstekst(ut, land) {
  if (!ut) return "";
  const pakke = (land || []).find(
    (l) => l.landkode === ut.jurisdiksjon
      && l.regelversjon === ut.regelversjon) || {};
  return t("ui.skatt.beregnet_svar")
    .replace("{land}", ut.jurisdiksjon)
    .replace("{v}", String(ut.regelversjon))
    .replace("{p}", (ut.promille / 10).toFixed(1).replace(/\.0$/, ""))
    .replace("{skatt}", belop(ut.skatt_ore, ut.valuta, pakke.desimaler));
}


function felt(id, tekst, kontroll, hjelp) {
  return el("div", { class: "felt" },
    el("label", { for: id, text: t(tekst) }),
    kontroll,
    hjelp ? el("p", { class: "muted", text: t(hjelp) }) : null);
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
  // `last()` bygger `kropp` på nytt — og for beregningen er det
  // nettopp svaret som betyr noe: jurisdiksjonen, regelversjonen og
  // satsen er de tre tingene kalleren IKKE oppga.
  //
  // `kvittering` ligger utenfor `kropp` og står. CodeRabbits funn om
  // hardkodede desimaler førte hit: porten som skulle måle desimalene
  // fant at teksten aldri ble vist i det hele tatt.
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
      // «Tyskland har ingen landpakke» er noe kalleren kan gjøre noe
      // med. En generell «noe gikk galt» ville sendt henne til
      // driftsvakten framfor til den som feller en landpakke.
      const egen = e && !e.status && typeof e.message === "string"
        && e.message ? e.message : null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.skatt.feil.tilstand")
          : (egen || t("ui.skatt.feil.generell")) }));
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


// SAMMENDRAGET. LINJEN SOM ALLTID SIER NULL.
//
// «Innberetninger: 0» står her hver eneste gang, og det er ikke støy —
// det er modulens dom, gjort synlig. Et menneske skal ikke måtte anta
// at maskinen ikke har sendt noe til skattemyndigheten; hun skal se
// det.
export function sammendrag(s) {
  const p = el("p", {});
  if (s.over_kontrollgrense > 0) {
    p.append(el("strong", { role: "alert",
      text: t("ui.skatt.over_grense")
        .replace("{n}", String(s.over_kontrollgrense)) }), " ");
  }
  p.append(el("span", {
    text: t("ui.skatt.sammendrag")
      .replace("{n}", String(s.vurderinger ?? 0))
      .replace("{land}", String(s.land_i_bruk ?? 0))
      .replace("{funn}", String(s.apne_funn ?? 0)) }));
  p.append(" ", el("strong", {
    class: "utfort-null",
    text: t("ui.skatt.ingen_innberetning")
      .replace("{n}", String(s.innberetninger ?? 0)) }));
  return p;
}


// LANDREGISTERET. EN PAKKE UTEN SATSER ER ET VARSEL.
//
// Den ville tilfredsstilt fremmednøkkelen fra en vurdering og forklart
// ingenting — og døra ville nektet hver beregning mot den, uten at
// noen visste hvorfor.
export function landrader(data) {
  const ut = [];
  for (const l of data.land || []) {
    const tr = el("tr", {});
    tr.append(el("td", { text: l.landkode || "–" }));
    tr.append(el("td", { text: String(l.regelversjon ?? "–") }));
    tr.append(el("td", { text: l.valuta || "–" }));
    tr.append(el("td", {
      text: t(AVRUNDINGSTEKST[l.avrundingsregel]
              || "ui.skatt.avrund_ukjent") }));
    const satser = el("td", {});
    if (!(l.satser > 0)) {
      satser.append(el("strong", { role: "alert",
        text: t("ui.skatt.uten_satser") }));
    } else {
      satser.append(el("span", { text: String(l.satser) }));
    }
    tr.append(satser);
    tr.append(el("td", {},
      l.gjelder
        ? el("span", { text: t("ui.skatt.gjelder_ja") })
        : el("span", { class: "muted",
                       text: l.gyldig_til
                         ? t("ui.skatt.gjaldt_til")
                             .replace("{d}", dato(l.gyldig_til))
                         : t("ui.skatt.gjelder_nei") })));
    // ET MENNESKE HAR SETT PÅ DEN.
    tr.append(el("td", { text: l.signert_av || "–" }));
    ut.push(tr);
  }
  return ut;
}


// VURDERINGENE. SATSEN MED VERSJONEN, OG BEGGE LANDENE.
export function vurderingsrader(data) {
  const land = new Map();
  for (const l of data.land || []) {
    if (!land.has(l.landkode)) land.set(l.landkode, l);
  }
  const ut = [];
  for (const v of data.vurderinger || []) {
    const l = land.get(v.jurisdiksjon) || {};
    const tr = el("tr", {});
    tr.append(el("td", { text: v.transaksjonsref || "–" }));
    tr.append(el("td", { text: dato(v.transaksjonsdato) }));
    // BEGGE LANDENE. Kjøperens først, fordi det er jurisdiksjonen i
    // v1s regel — og selgerens ved siden av, fordi regelen kan endres.
    tr.append(el("td", {
      text: t("ui.skatt.landpar")
        .replace("{kjoper}", v.kjoperland || "–")
        .replace("{selger}", v.selgerland || "–") }));
    tr.append(el("td", { text: satstekst(v) }));
    tr.append(el("td", {
      text: belop(v.belop_ore, l.valuta, l.desimaler) }));
    tr.append(el("td", {
      text: belop(v.skatt_ore, l.valuta, l.desimaler) }));
    const kontroll = el("td", {});
    if (v.over_kontrollgrense) {
      kontroll.append(el("strong", { role: "alert",
        text: t("ui.skatt.krever_kontroll") }));
    } else {
      kontroll.append(el("span", { class: "muted",
        text: t("ui.skatt.under_grense") }));
    }
    tr.append(kontroll);
    ut.push(tr);
  }
  return ut;
}


export function funnrader(data, ctx) {
  const ut = [];
  for (const f of data.funn || []) {
    const tr = el("tr", {});
    tr.append(el("td", {
      text: t(FUNNTEKST[f.funntype] || "ui.skatt.funn_ukjent") }));
    tr.append(el("td", { text: f.detalj || "–" }));
    tr.append(el("td", { text: dato(f.forst_sett) }));
    const handling = el("td", {});
    if (f.sveipens) {
      // SVEIPENS EGNE LUKKES NÅR TILSTANDEN ER BORTE. En knapp her
      // ville invitert til å lukke en måling.
      handling.append(el("span", { class: "muted",
        text: t("ui.skatt.lukkes_av_sveipen") }));
    } else if (ctx.kanSkrive) {
      handling.append(knappMed(t("ui.skatt.lukk_funn"),
        () => ctx.lukkFunn(f)));
    }
    tr.append(handling);
    ut.push(tr);
  }
  return ut;
}


// KRAVSKJEMAET. SELGERLANDET MÅ HA EN PAKKE.
//
// Døra nekter uten, og nektet kommer ut som en lesbar melding: uten
// selgerlandets pakke kan ingen si hva som er innenlands, og «usikker
// jurisdiksjon» begynner allerede der.
export function kravskjema(ctx, last, kvitter, s, data) {
  const land = el("select", { id: "s-selgerland", name: "s-selgerland" });
  const valgbare = (data.land || []).filter((l) => l.gjelder);
  for (const l of valgbare) {
    land.append(el("option", { value: l.landkode,
      text: `${l.landkode} · ${l.valuta}` }));
  }
  if (s.selgerland) land.value = s.selgerland;
  const grense = el("input", { id: "s-grense", type: "number", min: "0",
                               required: "required",
                               value: String(s.manuell_kontroll_over_ore
                                             ?? 1000000) });
  const frist = el("input", { id: "s-frist", type: "number", min: "1",
                              max: "365", required: "required",
                              value: String(s.kontrollfrist_dogn ?? 14) });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.skatt.lagre_krav") });
  const skjema = el("form", { class: "skjema" },
    felt("s-selgerland", "ui.skatt.felt_selgerland", land,
         "ui.skatt.hjelp_selgerland"),
    felt("s-grense", "ui.skatt.felt_grense", grense,
         "ui.skatt.hjelp_grense"),
    felt("s-frist", "ui.skatt.felt_frist", frist,
         "ui.skatt.hjelp_frist"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, okNokkel: "ui.skatt.krav_lagret", kvitter,
    send: (idem) => settSkattekrav({
      selgerland: land.value,
      manuell_kontroll_over_ore: Number(grense.value),
      kontrollfrist_dogn: Number(frist.value),
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.skatt.krav") }),
    el("p", { class: "muted", text: t("ui.skatt.krav_hjelp") }),
    skjema);
}


// BEREGNINGSSKJEMAET. KALLEREN OPPGIR ALDRI EN SATS ELLER ET LAND.
//
// Feltene er: transaksjonsreferanse, adresseversjon, satskode, beløp
// og dato. Jurisdiksjonen leses fra adressen, satsen fra landpakken.
//
// DET FINNES INGEN PROMILLE-INPUT HER, og det er ikke en forglemmelse:
// en promilleparameter ville gjort hele landregisteret til pynt.
export function beregningsskjema(ctx, last, kvitter, s, data) {
  const ref = el("input", { id: "s-ref", type: "text", maxlength: "200",
                            required: "required" });
  const adresse = el("input", { id: "s-adresse", type: "text",
                                required: "required",
                                placeholder: "UUID" });
  // SATSKODENE ER LANDETS. Lista fylles fra registeret, ikke fra en
  // konstant i klienten — det neste landet har en vi ikke kjenner.
  const koder = new Set();
  for (const l of data.land || []) {
    if (l.gjelder && l.satser > 0) koder.add(l.landkode);
  }
  const satskode = el("input", { id: "s-satskode", type: "text",
                                 pattern: "[a-z_]{2,40}",
                                 required: "required",
                                 value: "standard" });
  const belopfelt = el("input", { id: "s-belop", type: "number", min: "0",
                                  required: "required", value: "0" });
  const dato_ = el("input", { id: "s-dato", type: "date",
                              required: "required", value: iDagLokal() });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.skatt.beregn_knapp") });
  // SVARET BÆRES UT AV `send` OG INN I KVITTERINGEN, ikke inn i et
  // felt her: `last()` bygger dette kortet på nytt straks etterpå.
  let sisteSvar = null;
  const skjema = el("form", { class: "skjema" },
    felt("s-ref", "ui.skatt.felt_ref", ref),
    felt("s-adresse", "ui.skatt.felt_adresse", adresse,
         "ui.skatt.hjelp_adresse"),
    felt("s-satskode", "ui.skatt.felt_satskode", satskode,
         "ui.skatt.hjelp_satskode"),
    felt("s-belop", "ui.skatt.felt_belop", belopfelt,
         "ui.skatt.hjelp_belop"),
    felt("s-dato", "ui.skatt.felt_dato", dato_, "ui.skatt.hjelp_dato"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, okNokkel: "ui.skatt.beregnet", kvitter,
    tilbakestill: () => { ref.value = ""; },
    okTekst: () => sisteSvar,
    send: async (idem) => {
      const ut = await beregnSkatt({
        transaksjonsref: ref.value,
        kravversjon: s.kravversjon,
        adresseversjon_id: adresse.value.trim(),
        satskode: satskode.value,
        belop_ore: Number(belopfelt.value),
        transaksjonsdato: dato_.value,
      }, idem);
      // SVARET BÆRER JURISDIKSJONEN OG VERSJONEN, fordi kalleren ikke
      // oppga noen av delene. Formateringen er `kvitteringstekst`,
      // som porten måler direkte.
      sisteSvar = kvitteringstekst(ut, data.land);
      return ut;
    },
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.skatt.beregn") }),
    el("p", { class: "muted", text: t("ui.skatt.beregn_hjelp") }),
    el("p", { class: "muted",
      text: t("ui.skatt.land_i_registeret")
        .replace("{land}", [...koder].join(", ") || "–") }),
    skjema);
}


// LUKKEPANELET.
function lukkepanel(ctx, last, kvitter) {
  const grunn = el("textarea", { id: "s-lukkgrunn", rows: "2",
                                 maxlength: "8000",
                                 required: "required" });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.skatt.lukk_bekreft") });
  const skjema = el("form", { class: "skjema" },
    felt("s-lukkgrunn", "ui.skatt.felt_grunn", grunn,
         "ui.skatt.hjelp_grunn"),
    knapp, utfall);
  const node = el("section", { class: "kpi-kort", hidden: "hidden" },
    el("h2", { text: t("ui.skatt.lukk_funn_tittel") }), skjema);
  let valgt = null;
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter, okNokkel: "ui.skatt.funn_lukket",
    tilbakestill: () => {
      grunn.value = ""; valgt = null; node.hidden = true;
    },
    send: (idem) => lukkSkattefunn(valgt.funn_id,
                                   { grunn: grunn.value }, idem),
  });
  return {
    node,
    aapne: (rad) => { valgt = rad; node.hidden = false; grunn.focus(); },
  };
}


export function visSkatt(hoved, ctx) {
  const hode = () => flateHode(t("ui.skatt.tittel"),
    t("ui.skatt.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/skatt"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const skriver = harScope(ctx, "bestilling:opprett");
      const lukking = lukkepanel(ctx, last, kvitter);
      const kontekst = {
        kanSkrive: skriver,
        lukkFunn: (f) => lukking.aapne(f),
      };

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.skatt.sammendrag_tittel") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT.
        el("p", { class: "muted", text: t("ui.skatt.hvorfor") }));

      const funnseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.skatt.funn") }));
      if (!(d.funn || []).length) {
        funnseksjon.append(el("p", { class: "muted",
          text: t("ui.skatt.funn_tomt") }));
      } else {
        funnseksjon.append(tabell(
          ["ui.skatt.kol_funntype", "ui.skatt.kol_detalj",
           "ui.skatt.kol_forst_sett", "ui.skatt.kol_handling"],
          funnrader(d, kontekst)));
      }

      const vurderingsseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.skatt.vurderinger") }));
      if (!(d.vurderinger || []).length) {
        vurderingsseksjon.append(el("p", { class: "muted",
          text: t("ui.skatt.vurderinger_tomt") }));
      } else {
        vurderingsseksjon.append(tabell(
          ["ui.skatt.kol_ref", "ui.skatt.kol_dato",
           "ui.skatt.kol_land", "ui.skatt.kol_sats",
           "ui.skatt.kol_belop", "ui.skatt.kol_skatt",
           "ui.skatt.kol_kontroll"],
          vurderingsrader(d)));
      }

      // LANDREGISTERET ER SYNLIG, OG DET ER MED VILJE. Den som lurer
      // på hvorfor en beregning stoppet, skal se at landet mangler en
      // pakke — framfor å måtte spørre oss.
      const landseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.skatt.land") }),
        el("p", { class: "muted", text: t("ui.skatt.land_hjelp") }));
      if (!(d.land || []).length) {
        landseksjon.append(el("p", { class: "muted",
          text: t("ui.skatt.land_tomt") }));
      } else {
        landseksjon.append(tabell(
          ["ui.skatt.kol_landkode", "ui.skatt.kol_regelversjon",
           "ui.skatt.kol_valuta", "ui.skatt.kol_avrunding",
           "ui.skatt.kol_satser", "ui.skatt.kol_gyldighet",
           "ui.skatt.kol_signert"],
          landrader(d)));
      }

      const deler = [oversikt, funnseksjon, lukking.node,
                     vurderingsseksjon, landseksjon];
      if (skriver) {
        if (s.har_krav) {
          deler.push(beregningsskjema(ctx, last, kvitter, s, d));
        } else {
          // UTEN GRENSER KAN INGENTING BEREGNES. Flaten sier det
          // framfor å vise et skjema som ikke virker.
          deler.push(el("section", { class: "kpi-kort" },
            el("h2", { text: t("ui.skatt.beregn") }),
            el("p", { class: "muted",
                      text: t("ui.skatt.beregn_uten_krav") })));
        }
        deler.push(kravskjema(ctx, last, kvitter, s, d));
      }
      sett(kropp, ...deler);
    });

  return last();
}
