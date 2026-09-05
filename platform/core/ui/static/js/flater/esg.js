// M-45 bærekrafts- og ESG-agent (136) — GRUNNLAGET ER PRODUKTET.
//
// FLATENS VIKTIGSTE JOBB ER Å VISE HVA SOM ER MÅLT OG HVA SOM ER
// GJETTET.
//
// Klyngens delte dom: en ytring avgitt i husets navn kan ikke tas
// tilbake — og DEN SOM LESER DEN VET IKKE AT EN MASKIN SKREV DEN. En
// bærekraftsrapport leses av investorer, kunder og et tilsyn, og ET
// ESTIMAT LEST SOM EN MÅLING ER GRØNNVASKING, uansett hva som var ment.
//
// DERFOR STÅR `er_estimat` SOM ET VARSEL I SAMME RAD SOM TALLET, med
// grunnlaget under. Et tall uten den merkingen ville sett ut som en
// måling — og forskjellen er hele modulen.
//
// STANDARDVERSJONEN STÅR PÅ HVER PERIODE. En foreldet regel ser
// nøyaktig ut som en riktig regel, og et tall regnet med fjorårets
// faktor og lest som årets er feil på nøyaktig den måten CSRD skal
// hindre.
//
// ESTIMATANDELEN REGNES AV UTSLIPPET, IKKE AV ANTALLET, og den står i
// sammendraget: den som leser skal se hvor mye av tallet som er
// gjettet, uten å regne det ut selv.
//
// DET FINNES INGEN «SEND RAPPORT»-KNAPP, OG DET KAN IKKE FINNES.
// Modulen sammenstiller et grunnlag; innsendingen til et tilsyn er et
// menneskes, og den hører hjemme i M-47.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  apneRapportperiode, avviklUtslippsfaktor, FeilformetFeil, hentJson,
  lukkEsgfunn, lukkRapportperiode, nyIdempotensnokkel,
  registrerEsgkilde, registrerEsgmaaling, registrerEsgpaastand,
  registrerUtslippsfaktor, sammenstillEsgrapport, settEsgkrav,
  UautorisertFeil,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

export const DOKUMENTTYPER = ["maaling", "testrapport", "datablad",
                              "leverandorerklaering", "sertifikat",
                              "attest", "regnskap", "referanse",
                              "policy", "annet"];

const TYPETEKST = {
  maaling: "ui.esg.type_maaling",
  testrapport: "ui.esg.type_testrapport",
  datablad: "ui.esg.type_datablad",
  leverandorerklaering: "ui.esg.type_leverandorerklaering",
  sertifikat: "ui.esg.type_sertifikat",
  attest: "ui.esg.type_attest",
  regnskap: "ui.esg.type_regnskap",
  referanse: "ui.esg.type_referanse",
  policy: "ui.esg.type_policy",
  annet: "ui.esg.type_annet",
};

const STATUSTEKST = {
  apen: "ui.esg.status_apen",
  lukket: "ui.esg.status_lukket",
};

const FUNNTEKST = {
  estimat_ikke_erstattet_over_frist: "ui.esg.funn_gammelt_estimat",
  standardversjon_foreldet_i_apen_periode: "ui.esg.funn_foreldet",
  estimatandel_over_terskel_uavklart: "ui.esg.funn_andel",
  tall_uten_kilde: "ui.esg.funn_uten_kilde",
  tall_uten_faktorversjon: "ui.esg.funn_uten_versjon",
  estimat_ikke_merket: "ui.esg.funn_umerket",
  paastand_uten_kilde: "ui.esg.funn_paastand_uten_kilde",
  modulen_sendte_rapport: "ui.esg.funn_sendte",
};


// PROSENT AV BASISPUNKTER, MED ÉN DESIMAL.
//
// En estimatandel på 4,7 % og en på 4,2 % er ikke det samme tallet i
// en rapport et tilsyn leser, og avrunding til hele prosent ville
// skjult forskjellen.
export function andel(bp) {
  if (typeof bp !== "number") return "–";
  return `${(bp / 100).toFixed(1).replace(".", ",")} %`;
}


// TALLET SOM TEKST, MED TUSENSKILLE.
//
// Verdien kommer som STRENG fra API-et, og den skal bli der: en
// `Number()` her ville flyttet siste desimal, og da ville tallet på
// skjermen ikke vært tallet i rapporten.
export function tall(tekst) {
  if (typeof tekst !== "string" || tekst === "") return "–";
  const [heltall, desimal] = tekst.split(".");
  const gruppert = heltall.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  if (!desimal) return gruppert;
  const trimmet = desimal.replace(/0+$/, "");
  return trimmet ? `${gruppert},${trimmet}` : gruppert;
}


export function dato(iso) {
  if (typeof iso !== "string" || !iso) return "–";
  return iso.slice(0, 10);
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


// DAGENS DATO I BRUKERENS EGEN SONE (135s lærdom, arvet).
export function iDagLokal(naa) {
  const d = naa || new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
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
          ? t("ui.esg.feil.tilstand")
          : t("ui.esg.feil.generell") }));
      return;
    }
    idem = null;
    knapp.disabled = false;
    if (tilbakestill) tilbakestill();
    meldLive(t(okNokkel));
    kvitter(t(okNokkel));
    await last();
  });
}


// SAMMENDRAGET. DET DYRESTE TALLET FØRST.
//
// Den perioden der mest av utslippet er gjettet, er det ene tallet som
// betyr at rapporten kan være svakere enn den ser ut.
export function sammendrag(s) {
  const p = el("p", {});
  if (s.hoyeste_estimatandel_bp != null
      && s.estimatterskel_bp != null
      && s.hoyeste_estimatandel_bp > s.estimatterskel_bp) {
    p.append(el("strong", { role: "alert",
      text: t("ui.esg.hoy_estimatandel")
        .replace("{n}", andel(s.hoyeste_estimatandel_bp))
        .replace("{terskel}", andel(s.estimatterskel_bp)) }), " ");
  }
  p.append(el("span", { text: t("ui.esg.sammendrag_tekst")
    .replace("{perioder}", String(s.perioder ?? 0))
    .replace("{maalinger}", String(s.maalinger ?? 0))
    .replace("{estimater}", String(s.estimater ?? 0))
    .replace("{faktorer}", String(s.faktorer ?? 0)) }));
  if (s.utlopte_kilder > 0) {
    p.append(" ", el("span", { text: t("ui.esg.utlopte_kilder")
      .replace("{n}", String(s.utlopte_kilder)) }));
  }
  if (!s.har_krav) {
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.esg.mangler_krav") }));
  }
  return p;
}


// MÅLINGENE. ESTIMATET MERKET, I SAMME RAD SOM TALLET.
export function maalingstabell(maalinger) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.esg.kategori", "ui.esg.mengde",
                   "ui.esg.utslipp", "ui.esg.grunnlag",
                   "ui.esg.kilde", "ui.esg.status"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const m of maalinger) {
    const grunnlag = el("td", {});
    if (m.er_estimat) {
      // ET ESTIMAT ER ET VARSEL, IKKE EN FOTNOTE.
      grunnlag.append(el("strong", { role: "alert",
        text: t("ui.esg.estimat") }));
      // …OG HVA DET HVILER PÅ STÅR UNDER.
      grunnlag.append(el("p", { class: "muted",
                                text: m.estimatgrunnlag || "" }));
    } else {
      grunnlag.append(el("span", { text: t("ui.esg.maalt") }));
    }
    const kilde = el("td", {});
    kilde.append(el("span", { text: m.kilde_tittel }));
    if (!m.kilde_gyldig) {
      kilde.append(" ", el("strong", { role: "alert",
        text: t("ui.esg.kilde_utlopt") }));
    }
    const status = el("td", {});
    if (m.erstattet) {
      // ET ERSTATTET TALL ER SYNLIG SOM ERSTATTET, ikke borte.
      status.append(el("span", { text: t("ui.esg.erstattet") }));
    } else {
      status.append(el("span", { text: t("ui.esg.gjeldende") }));
    }
    kropp.append(el("tr", {},
      el("td", { text: m.kategori }),
      el("td", { text: `${tall(m.mengde)} ${m.enhet}` }),
      el("td", { text: `${tall(m.utslipp_kg)} kg` }),
      grunnlag, kilde, status));
  }
  tabell.append(kropp);
  return tabell;
}


export function periodetabell(perioder, terskel, aapne) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.esg.periode", "ui.esg.vindu",
                   "ui.esg.standardversjon", "ui.esg.status",
                   "ui.esg.utslipp", "ui.esg.estimatandel",
                   "ui.esg.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const p of perioder) {
    const andelcelle = el("td", {});
    if (typeof terskel === "number" && p.estimatandel_bp > terskel) {
      andelcelle.append(el("strong", { role: "alert",
        text: andel(p.estimatandel_bp) }));
    } else {
      andelcelle.append(el("span", { text: andel(p.estimatandel_bp) }));
    }
    if (p.antall_utlopte_kilder > 0) {
      andelcelle.append(" ", el("strong", { role: "alert",
        text: t("ui.esg.paa_utlopt_kilde")
          .replace("{n}", String(p.antall_utlopte_kilder)) }));
    }
    const sn = STATUSTEKST[p.status];
    kropp.append(el("tr", {},
      el("td", { text: p.merke }),
      el("td", { text: `${p.fra} – ${p.til}` }),
      // VERSJONEN SOM ER LÅST, med standarden foran.
      el("td", { text: `${p.standard} ${p.standardversjon}` }),
      el("td", { text: sn ? t(sn) : String(p.status) }),
      el("td", { text: `${tall(p.sum_utslipp_kg)} kg` }),
      andelcelle,
      el("td", {}, aapne
        ? knappMed(t("ui.esg.vis_tallene"), () => aapne(p))
        : el("span", { text: "–" }))));
  }
  tabell.append(kropp);
  return tabell;
}


export function faktortabell(faktorer, avvikl) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.esg.kategori", "ui.esg.faktorverdi",
                   "ui.esg.standardversjon", "ui.esg.kilde",
                   "ui.esg.gyldig_fra", "ui.esg.status",
                   "ui.esg.brukt", "ui.esg.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const f of faktorer) {
    const status = el("td", {});
    if (f.gjelder) {
      status.append(el("span", { text: t("ui.esg.gjelder") }));
    } else {
      status.append(el("span", { text: t("ui.esg.avviklet") }));
    }
    kropp.append(el("tr", {},
      el("td", { text: f.kategori }),
      el("td", { text: `${tall(f.verdi)} kg/${f.enhet}` }),
      el("td", { text: `${f.standard} ${f.standardversjon}` }),
      el("td", { text: f.kilde_tittel }),
      el("td", { text: f.gyldig_fra }),
      status,
      el("td", { text: String(f.antall_maalinger) }),
      el("td", {}, (f.gjelder && avvikl)
        ? knappMed(t("ui.esg.avvikl"), () => avvikl(f))
        : el("span", { text: "–" }))));
  }
  tabell.append(kropp);
  return tabell;
}


// RAPPORTENE. HVER SAMMENSTILLING MED TALLENE SLIK DE STO DA.
export function rapporttabell(rapporter, terskel) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.esg.periode", "ui.esg.versjon",
                   "ui.esg.utslipp", "ui.esg.estimatandel",
                   "ui.esg.standardversjon", "ui.esg.sammenstilt",
                   "ui.esg.sammenstilt_av"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const r of rapporter) {
    const andelcelle = el("td", {});
    if (typeof terskel === "number" && r.estimatandel_bp > terskel) {
      andelcelle.append(el("strong", { role: "alert",
        text: andel(r.estimatandel_bp) }));
    } else {
      andelcelle.append(el("span", { text: andel(r.estimatandel_bp) }));
    }
    kropp.append(el("tr", {},
      el("td", { text: r.periodemerke }),
      el("td", { text: String(r.versjon) }),
      el("td", { text: `${tall(r.sum_utslipp_kg)} kg` }),
      andelcelle,
      el("td", { text: r.standardversjon }),
      el("td", { text: dato(r.sammenstilt) }),
      el("td", { text: r.sammenstilt_av })));
  }
  tabell.append(kropp);
  return tabell;
}


export function funntabell(funn, lukk) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.esg.funntype", "ui.esg.detaljer",
                   "ui.esg.forst_sett", "ui.esg.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const f of funn) {
    const handling = el("td", {});
    if (!f.apen) {
      handling.append(el("span", { text: t("ui.esg.lukket_av")
        .replace("{av}", f.lukket_av || "") }));
    } else if (!f.kan_lukkes) {
      handling.append(el("span", {
        text: t("ui.esg.lukkes_av_sveipen") }));
    } else if (lukk) {
      handling.append(knappMed(t("ui.esg.avklar"), () => lukk(f)));
    } else {
      handling.append(el("span", { text: "–" }));
    }
    const fn = FUNNTEKST[f.funntype];
    kropp.append(el("tr", {},
      el("td", { text: fn ? t(fn) : String(f.funntype) }),
      el("td", { text: f.detaljer }),
      el("td", { text: dato(f.forst_sett) }),
      handling));
  }
  tabell.append(kropp);
  return tabell;
}


// PERIODEPANELET — TALLENE OG PÅSTANDENE, hentet når noen spør.
export function periodepanel(ctx) {
  const node = el("section", { class: "kpi-kort", hidden: true });

  async function aapne(p) {
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.esg.tallene") }),
      el("p", { class: "muted", text: t("ui.esg.laster") }));
    let m;
    let s;
    try {
      m = await hentJson(`/v1/esg/periode/${p.periode_id}/maalinger`);
      s = await hentJson(`/v1/esg/periode/${p.periode_id}/paastander`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.esg.tallene") }),
        el("p", { role: "alert", text: t("ui.esg.feil.generell") }));
      return;
    }
    const maalinger = m.maalinger || [];
    const paastander = s.paastander || [];
    const deler = [
      el("h2", { text: t("ui.esg.tallene") }),
      // STANDARDVERSJONEN STÅR ØVERST. Den som ser tallene skal se
      // hvilken standard de er regnet med.
      el("p", { class: "muted", text: t("ui.esg.periode_om")
        .replace("{merke}", p.merke)
        .replace("{standard}", `${p.standard} ${p.standardversjon}`) }),
    ];
    if (!maalinger.length) {
      deler.push(el("p", { class: "muted",
                           text: t("ui.esg.ingen_maalinger") }));
    } else {
      deler.push(maalingstabell(maalinger));
    }
    deler.push(el("h3", { text: t("ui.esg.paastandene") }));
    if (!paastander.length) {
      deler.push(el("p", { class: "muted",
                           text: t("ui.esg.ingen_paastander") }));
    } else {
      deler.push(paastandstabell(paastander));
    }
    sett(node, ...deler);
  }

  return { node, aapne };
}


// PÅSTANDENE. EN PÅSTAND SOM HVILER PÅ ET ESTIMAT BÆRER DET VIDERE.
export function paastandstabell(paastander) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.esg.rekkefolge", "ui.esg.paastand",
                   "ui.esg.kilde", "ui.esg.kildetype",
                   "ui.esg.hviler_paa"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const p of paastander) {
    const hviler = el("td", {});
    if (p.maaling_er_estimat === true) {
      // USIKKERHETEN FORSVINNER IKKE fordi noen skrev en setning rundt
      // tallet (133/134s form).
      hviler.append(el("strong", { role: "alert",
        text: t("ui.esg.hviler_paa_estimat") }));
    } else if (p.maaling_id) {
      hviler.append(el("span", { text: t("ui.esg.hviler_paa_maaling") }));
    } else {
      hviler.append(el("span", { text: t("ui.esg.hviler_paa_dokument") }));
    }
    const kilde = el("td", {});
    kilde.append(el("span", { text: p.kilde_tittel }));
    if (!p.kilde_gyldig) {
      kilde.append(" ", el("strong", { role: "alert",
        text: t("ui.esg.kilde_utlopt") }));
    }
    const tn = TYPETEKST[p.dokumenttype];
    kropp.append(el("tr", {},
      el("td", { text: String(p.rekkefolge) }),
      el("td", { text: p.tekst }),
      kilde,
      el("td", { text: tn ? t(tn) : String(p.dokumenttype) }),
      hviler));
  }
  tabell.append(kropp);
  return tabell;
}


export function lukkepanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });

  function aapne(f) {
    node.hidden = false;
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    const form = el("form", { class: "skjema" });
    const grunn = el("input", { type: "text", id: "esg-grunn",
                                name: "grunn", required: true });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.esg.avklar") });
    form.append(
      el("p", { class: "muted", text: t("ui.esg.avklar_om") }),
      felt("esg-grunn", "ui.esg.grunn", grunn, "ui.esg.grunn_hjelp"),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.esg.avklart_ok",
      send: (idem) => lukkEsgfunn(f.funn_id, { grunn: grunn.value },
                                  idem),
    });
    sett(node, el("h2", { text: t("ui.esg.avklar") }), form);
  }

  return { node, aapne };
}


// MÅLINGSPANELET — MODULENS VIKTIGSTE SKJEMA.
//
// `er_estimat` ER ET EKSPLISITT VALG, ikke en avkrysning som står
// tom. Et felt som kunne stå urørt ville stille blitt til «målt», og
// et estimat lest som en måling er grønnvasking.
export function maalingspanel(ctx, last, kvitter, faktorer) {
  const node = el("section", { class: "kpi-kort", hidden: true });

  function aapne(p) {
    node.hidden = false;
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    if (p.status !== "apen") {
      sett(node, el("h2", { text: t("ui.esg.nytt_tall") }),
        el("p", { role: "alert", text: t("ui.esg.perioden_lukket") }));
      return;
    }
    // BARE FAKTORER I PERIODENS EGEN STANDARDVERSJON. Døra nekter på
    // resten, og valget skal ikke tilby dem.
    const passende = (faktorer || []).filter(
      (f) => f.gjelder && f.standardversjon === p.standardversjon);
    if (!passende.length) {
      sett(node, el("h2", { text: t("ui.esg.nytt_tall") }),
        el("p", { role: "alert", text: t("ui.esg.ingen_faktor")
          .replace("{versjon}", p.standardversjon) }));
      return;
    }
    const form = el("form", { class: "skjema" });
    const faktor = el("select", { id: "esg-faktor", name: "faktor_id" });
    for (const f of passende) {
      faktor.append(el("option", { value: f.faktor_id,
        text: `${f.kategori} — ${tall(f.verdi)} kg/${f.enhet}` }));
    }
    const kategori = el("input", { type: "text", id: "esg-kategori",
                                   name: "kategori", required: true,
                                   pattern: "[a-z][a-z0-9_]*" });
    const mengde = el("input", { type: "text", id: "esg-mengde",
                                 name: "mengde", required: true,
                                 inputmode: "decimal" });
    const enhet = el("input", { type: "text", id: "esg-enhet",
                                name: "enhet", required: true });
    const estimat = el("input", { type: "checkbox", id: "esg-estimat",
                                  name: "er_estimat" });
    const grunnlag = el("input", { type: "text", id: "esg-estimatgrunnlag",
                                   name: "estimatgrunnlag" });
    const kilde = el("input", { type: "text", id: "esg-kildevalg",
                                name: "kilde_id", required: true });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.esg.lagre_tall") });
    // GRUNNLAGSFELTET FØLGER AVKRYSNINGEN. Et estimat må si hva det
    // hviler på, og skjemaet spør om det i det samme.
    grunnlag.disabled = true;
    estimat.addEventListener("change", () => {
      grunnlag.disabled = !estimat.checked;
      if (!estimat.checked) grunnlag.value = "";
    });
    form.append(
      el("p", { class: "muted", text: t("ui.esg.tall_om")
        .replace("{merke}", p.merke)
        .replace("{versjon}", p.standardversjon) }),
      felt("esg-kategori", "ui.esg.kategori", kategori,
           "ui.esg.kategori_hjelp"),
      felt("esg-mengde", "ui.esg.mengde", mengde, "ui.esg.mengde_hjelp"),
      felt("esg-enhet", "ui.esg.enhet", enhet),
      felt("esg-faktor", "ui.esg.faktor", faktor, "ui.esg.faktor_hjelp"),
      felt("esg-estimat", "ui.esg.er_estimat", estimat,
           "ui.esg.estimat_hjelp"),
      felt("esg-estimatgrunnlag", "ui.esg.estimatgrunnlag", grunnlag),
      felt("esg-kildevalg", "ui.esg.kilde", kilde, "ui.esg.kilde_hjelp"),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.esg.tall_lagret",
      tilbakestill: () => { mengde.value = ""; },
      send: (idem) => {
        for (const f of [kategori, mengde, enhet, kilde]) {
          if (f.value.trim() === "") {
            throw new FeilformetFeil(400, "felt_mangler");
          }
        }
        if (estimat.checked && grunnlag.value.trim().length < 16) {
          // ET ESTIMAT SOM IKKE SIER HVA DET HVILER PÅ, ER ET TALL
          // NOEN GJETTET.
          throw new FeilformetFeil(400, "estimatgrunnlag_mangler");
        }
        return registrerEsgmaaling(p.periode_id, {
          kategori: kategori.value.trim(),
          // MENGDEN SENDES SOM TEKST. En `Number()` her ville flyttet
          // siste desimal.
          mengde: mengde.value.trim().replace(",", "."),
          enhet: enhet.value.trim(),
          faktor_id: faktor.value,
          er_estimat: estimat.checked,
          estimatgrunnlag: estimat.checked
            ? grunnlag.value.trim() : null,
          erstatter_maaling_id: null,
          kilde_id: kilde.value.trim(),
        }, idem);
      },
    });
    sett(node, el("h2", { text: t("ui.esg.nytt_tall") }), form);
  }

  return { node, aapne };
}


export function paastandspanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });

  function aapne(p) {
    node.hidden = false;
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    if (p.status !== "apen") {
      sett(node, el("h2", { text: t("ui.esg.ny_paastand") }),
        el("p", { role: "alert", text: t("ui.esg.perioden_lukket") }));
      return;
    }
    const form = el("form", { class: "skjema" });
    const nr = el("input", { type: "number", id: "esg-paastandnr",
                             name: "rekkefolge", min: "1",
                             required: true,
                             value: String((p.antall_paastander || 0) + 1) });
    const tekst = el("input", { type: "text", id: "esg-paastandtekst",
                                name: "tekst", required: true });
    const kilde = el("input", { type: "text", id: "esg-paastandkilde",
                                name: "kilde_id", required: true });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.esg.lagre_paastand") });
    form.append(
      // KILDEN ER IKKE VALGFRI, OG SKJEMAET KAN IKKE GJØRE DEN DET.
      el("p", { class: "muted", text: t("ui.esg.paastand_om") }),
      felt("esg-paastandnr", "ui.esg.rekkefolge", nr),
      felt("esg-paastandtekst", "ui.esg.paastand", tekst),
      felt("esg-paastandkilde", "ui.esg.kilde", kilde,
           "ui.esg.kilde_hjelp"),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.esg.paastand_lagret",
      tilbakestill: () => { tekst.value = ""; },
      send: (idem) => registrerEsgpaastand(p.periode_id, {
        rekkefolge: Number(nr.value),
        tekst: tekst.value,
        kilde_id: kilde.value.trim(),
        maaling_id: null,
      }, idem),
    });
    sett(node, el("h2", { text: t("ui.esg.ny_paastand") }), form);
  }

  return { node, aapne };
}


export function periodeskjema(ctx, last, kvitter) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const merke = el("input", { type: "text", id: "esg-merke",
                              name: "merke", required: true });
  const fra = el("input", { type: "date", id: "esg-fra", name: "fra",
                            required: true });
  const til = el("input", { type: "date", id: "esg-til", name: "til",
                            required: true });
  const standard = el("input", { type: "text", id: "esg-standard",
                                 name: "standard", required: true,
                                 value: "ESRS" });
  const versjon = el("input", { type: "text", id: "esg-versjon",
                                name: "standardversjon",
                                required: true });
  const knapp = el("button", { type: "submit",
                               text: t("ui.esg.apne_periode") });
  form.append(
    // LÅSEN SIES RETT UT: versjonen oppgis én gang, her.
    el("p", { class: "muted", text: t("ui.esg.periode_om_ny") }),
    felt("esg-merke", "ui.esg.periode", merke, "ui.esg.merke_hjelp"),
    felt("esg-fra", "ui.esg.fra", fra),
    felt("esg-til", "ui.esg.til", til),
    felt("esg-standard", "ui.esg.standard", standard),
    felt("esg-versjon", "ui.esg.standardversjon", versjon,
         "ui.esg.versjon_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.esg.periode_apnet",
    tilbakestill: () => { merke.value = ""; },
    send: (idem) => apneRapportperiode({
      merke: merke.value.trim(),
      fra: fra.value,
      til: til.value,
      standard: standard.value.trim(),
      standardversjon: versjon.value.trim(),
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.esg.ny_periode") }), form);
}


export function faktorskjema(ctx, last, kvitter) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const kategori = el("input", { type: "text", id: "esg-faktorkategori",
                                 name: "kategori", required: true,
                                 pattern: "[a-z][a-z0-9_]*" });
  const enhet = el("input", { type: "text", id: "esg-faktorenhet",
                              name: "enhet", required: true });
  const verdi = el("input", { type: "text", id: "esg-faktorverdi",
                              name: "verdi", required: true,
                              inputmode: "decimal" });
  const standard = el("input", { type: "text", id: "esg-faktorstandard",
                                 name: "standard", required: true,
                                 value: "ESRS" });
  const versjon = el("input", { type: "text", id: "esg-faktorversjon",
                                name: "standardversjon",
                                required: true });
  const kilde = el("input", { type: "text", id: "esg-faktorkilde",
                              name: "kilde_id", required: true });
  const fra = el("input", { type: "date", id: "esg-faktorfra",
                            name: "gyldig_fra", required: true });
  const knapp = el("button", { type: "submit",
                               text: t("ui.esg.lagre_faktor") });
  form.append(
    // FAKTOREN HVILER OGSÅ PÅ ET DOKUMENT, sagt i skjemaet.
    el("p", { class: "muted", text: t("ui.esg.faktor_om") }),
    felt("esg-faktorkategori", "ui.esg.kategori", kategori,
         "ui.esg.kategori_hjelp"),
    felt("esg-faktorenhet", "ui.esg.enhet", enhet),
    felt("esg-faktorverdi", "ui.esg.faktorverdi", verdi,
         "ui.esg.faktorverdi_hjelp"),
    felt("esg-faktorstandard", "ui.esg.standard", standard),
    felt("esg-faktorversjon", "ui.esg.standardversjon", versjon),
    felt("esg-faktorkilde", "ui.esg.kilde", kilde, "ui.esg.kilde_hjelp"),
    felt("esg-faktorfra", "ui.esg.gyldig_fra", fra),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.esg.faktor_lagret",
    tilbakestill: () => { verdi.value = ""; },
    send: (idem) => registrerUtslippsfaktor({
      kategori: kategori.value.trim(),
      enhet: enhet.value.trim(),
      verdi: verdi.value.trim().replace(",", "."),
      standard: standard.value.trim(),
      standardversjon: versjon.value.trim(),
      kilde_id: kilde.value.trim(),
      gyldig_fra: fra.value,
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.esg.ny_faktor") }), form);
}


export function kildeskjema(ctx, last, kvitter) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const tittel = el("input", { type: "text", id: "esg-kildetittel",
                               name: "tittel", required: true });
  const type = velger("esg-kildetype", DOKUMENTTYPER, TYPETEKST);
  const gyldig = el("input", { type: "date", id: "esg-kildegyldig",
                               name: "gyldig_til" });
  const sum = el("input", { type: "text", id: "esg-kildesum",
                            name: "innhold_sha256", required: true,
                            pattern: "[0-9a-f]{64}" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.esg.lagre_kilde") });
  form.append(
    el("p", { class: "muted", text: t("ui.esg.kilde_om") }),
    felt("esg-kildetittel", "ui.esg.kildetittel", tittel),
    felt("esg-kildetype", "ui.esg.kildetype", type),
    felt("esg-kildegyldig", "ui.esg.gyldig_til", gyldig,
         "ui.esg.gyldig_til_hjelp"),
    felt("esg-kildesum", "ui.esg.innhold_sha256", sum,
         "ui.esg.sum_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.esg.kilde_lagret",
    tilbakestill: () => { tittel.value = ""; sum.value = ""; },
    send: (idem) => registrerEsgkilde({
      tittel: tittel.value,
      dokumenttype: type.value,
      gyldig_til: gyldig.value || null,
      innhold_sha256: sum.value.trim(),
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.esg.ny_kilde") }), form);
}


export function kravskjema(ctx, last, kvitter, s) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  // ALLE TRE GRENSENE FORHÅNDSUTFYLLES (123s lærdom).
  const terskel = el("input", { type: "number", id: "esg-terskel",
                                name: "estimatterskel_bp", min: "0",
                                max: "10000", required: true,
                                value: String(s.estimatterskel_bp ?? 2000) });
  const frist = el("input", { type: "number", id: "esg-estimatfrist",
                              name: "estimatfrist_dogn", min: "1",
                              max: "3650", required: true,
                              value: String(s.estimatfrist_dogn ?? 400) });
  const kilde = el("input", { type: "number", id: "esg-kildedogn",
                              name: "kilde_gyldig_dogn", min: "1",
                              max: "3650", required: true,
                              value: String(s.kilde_gyldig_dogn ?? 1095) });
  const knapp = el("button", { type: "submit",
                               text: t("ui.esg.lagre_krav") });
  form.append(
    el("p", { class: "muted", text: t("ui.esg.krav_om") }),
    felt("esg-terskel", "ui.esg.estimatterskel", terskel,
         "ui.esg.terskel_hjelp"),
    felt("esg-estimatfrist", "ui.esg.estimatfrist", frist,
         "ui.esg.estimatfrist_hjelp"),
    felt("esg-kildedogn", "ui.esg.kilde_gyldig_dogn", kilde),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.esg.krav_lagret",
    send: (idem) => settEsgkrav({
      estimatterskel_bp: Number(terskel.value),
      estimatfrist_dogn: Number(frist.value),
      kilde_gyldig_dogn: Number(kilde.value),
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.esg.krav") }), form);
}


export function visEsg(hoved, ctx) {
  const hode = () => flateHode(t("ui.esg.tittel"),
    t("ui.esg.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/esg"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const perioder = d.perioder || [];
      const faktorer = d.faktorer || [];
      const rapporter = d.rapporter || [];
      const funn = d.funn || [];
      const skriver = harScope(ctx, "bestilling:opprett");
      const periode = periodepanel(ctx);
      const lukking = lukkepanel(ctx, last, kvitter);
      const maaling = maalingspanel(ctx, last, kvitter, faktorer);
      const paastand = paastandspanel(ctx, last, kvitter);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.esg.sammendrag") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT.
        el("p", { class: "muted", text: t("ui.esg.hvorfor") }));

      const funnseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.esg.funn") }));
      if (!funn.length) {
        funnseksjon.append(el("p", { class: "muted",
          text: t("ui.esg.funn_tomt") }));
      } else {
        funnseksjon.append(funntabell(
          funn, skriver ? lukking.aapne : null));
      }

      const periodeseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.esg.perioder") }));
      if (!perioder.length) {
        periodeseksjon.append(el("p", { class: "muted",
          text: t("ui.esg.perioder_tomt") }));
      } else {
        periodeseksjon.append(periodetabell(
          perioder, s.estimatterskel_bp, periode.aapne));
        if (skriver) {
          const velg = el("div", { class: "felt" });
          for (const p of perioder.filter((x) => x.status === "apen")
                                  .slice(0, 20)) {
            velg.append(
              knappMed(t("ui.esg.tall_for").replace("{merke}", p.merke),
                       () => maaling.aapne(p)),
              knappMed(t("ui.esg.paastand_for")
                .replace("{merke}", p.merke), () => paastand.aapne(p)),
              knappMed(t("ui.esg.sammenstill_for")
                .replace("{merke}", p.merke),
                () => sammenstill(ctx, last, kvitter, p)),
              knappMed(t("ui.esg.lukk_for").replace("{merke}", p.merke),
                       () => lukkPeriode(ctx, last, kvitter, p)));
          }
          periodeseksjon.append(velg);
        }
      }

      const rapportseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.esg.rapporter") }));
      if (!rapporter.length) {
        rapportseksjon.append(el("p", { class: "muted",
          text: t("ui.esg.rapporter_tomt") }));
      } else {
        rapportseksjon.append(rapporttabell(rapporter,
                                            s.estimatterskel_bp));
      }
      // HVA SAMMENSTILLINGEN IKKE ER, SAGT UNDER TABELLEN.
      rapportseksjon.append(el("p", { class: "muted",
        text: t("ui.esg.rapport_om") }));

      const faktorseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.esg.faktorer") }));
      if (!faktorer.length) {
        faktorseksjon.append(el("p", { class: "muted",
          text: t("ui.esg.faktorer_tomt") }));
      } else {
        faktorseksjon.append(faktortabell(faktorer, skriver
          ? (f) => avviklFaktor(ctx, last, kvitter, f) : null));
      }

      const deler = [oversikt, funnseksjon, lukking.node,
                     periodeseksjon, periode.node, maaling.node,
                     paastand.node, rapportseksjon, faktorseksjon];
      if (skriver) {
        deler.push(periodeskjema(ctx, last, kvitter),
                   faktorskjema(ctx, last, kvitter),
                   kildeskjema(ctx, last, kvitter),
                   kravskjema(ctx, last, kvitter, s));
      }
      sett(kropp, ...deler);
    });

  async function sammenstill(c, l, k, p) {
    let svar;
    try {
      svar = await sammenstillEsgrapport(p.periode_id,
                                         nyIdempotensnokkel());
    } catch (e) {
      if (e instanceof UautorisertFeil) { c.paaUautorisert(); return; }
      k(t("ui.esg.feil.generell"));
      return;
    }
    // ESTIMATANDELEN SIES HØYT. Den som sammenstiller skal se hvor mye
    // av tallet som er gjettet, med én gang.
    const melding = t("ui.esg.sammenstilt_ok")
      .replace("{versjon}", String(svar && svar.versjon))
      .replace("{andel}", andel(svar && svar.estimatandel_bp));
    meldLive(melding);
    k(melding);
    await l();
  }

  async function lukkPeriode(c, l, k, p) {
    try {
      await lukkRapportperiode(p.periode_id, nyIdempotensnokkel());
    } catch (e) {
      if (e instanceof UautorisertFeil) { c.paaUautorisert(); return; }
      k(t("ui.esg.feil.generell"));
      return;
    }
    meldLive(t("ui.esg.periode_lukket"));
    k(t("ui.esg.periode_lukket"));
    await l();
  }

  async function avviklFaktor(c, l, k, f) {
    // AVVIKLING ER ENVEIS, og datoen er i dag — i BRUKERENS sone
    // (135s lærdom).
    try {
      await avviklUtslippsfaktor(f.faktor_id,
        { gyldig_til: iDagLokal() }, nyIdempotensnokkel());
    } catch (e) {
      if (e instanceof UautorisertFeil) { c.paaUautorisert(); return; }
      k(t("ui.esg.feil.generell"));
      return;
    }
    meldLive(t("ui.esg.faktor_avviklet"));
    k(t("ui.esg.faktor_avviklet"));
    await l();
  }

  return last();
}
