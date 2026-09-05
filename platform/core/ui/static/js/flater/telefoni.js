// M-43 tale- og telefoniagent (135) — IDENTIFIKASJONEN ER PRODUKTET.
//
// FLATENS VIKTIGSTE JOBB ER Å VISE HVA DEN ANDRE PARTEN VISSTE.
//
// Klyngens delte dom: en ytring avgitt i husets navn kan ikke tas
// tilbake — og DEN SOM LESER DEN VET IKKE AT EN MASKIN SKREV DEN. HER
// ER DEN BOKSTAVELIG: den andre parten HØRER en stemme, og en stemme
// høres ikke ut som en maskin lenger.
//
// DERFOR STÅR SEKUNDENE TIL IDENTIFIKASJON I SAMME RAD SOM SAMTALEN,
// med ordlyden agenten faktisk brukte. «Agenten identifiserte seg» er
// en påstand; teksten er en måling — og tallet sier hvor lenge den
// andre parten snakket før hun fikk vite det.
//
// EN UBEKREFTET LINJE STÅR SOM ET VARSEL i samme rad som teksten, med
// tallet OG terskelen som gjaldt DA. En transkripsjon uten usikkerhet
// er en påstand om at maskinen hørte riktig.
//
// OPPTAK VISES MED SITT GRUNNLAG, ALLTID. Et opptak uten synlig
// hjemmel ville sett ut som et opptak uten hjemmel.
//
// EN ESKALERING VISES MED REGELEN SOM BAR DEN. En eskalering uten en
// regel å peke på er modulens egen beslutning om at noe var viktig nok
// til å vekke et menneske.
//
// DET FINNES INGEN «GI RABATT»- ELLER «BEKREFT AVTALE»-KNAPP, OG DET
// KAN IKKE FINNES. Modulen nedtegner hva som ble sagt; den binder
// ingenting.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  avsluttSamtale, avviklEskaleringsregel, eskalerSamtale,
  FeilformetFeil, hentJson, lukkEskalering, lukkTelefonifunn,
  nyIdempotensnokkel, registrerEskaleringsregel,
  registrerOpptakshjemmelTelefoni, registrerTranskripsjonslinje,
  settTelefonikrav, startSamtale, startSamtaleopptak, UautorisertFeil,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

export const RETNINGER = ["inngaaende", "utgaaende"];
export const TALERE = ["agent", "motpart", "menneske"];
export const GRUNNLAGSTYPER = ["samtykke", "avtale",
                               "berettiget_interesse",
                               "rettslig_forpliktelse"];
export const ESKALERINGSUTFALL = ["haandtert", "henlagt"];

const RETNINGSTEKST = {
  inngaaende: "ui.telefoni.retning_inn",
  utgaaende: "ui.telefoni.retning_ut",
};

const TALERTEKST = {
  agent: "ui.telefoni.taler_agent",
  motpart: "ui.telefoni.taler_motpart",
  menneske: "ui.telefoni.taler_menneske",
};

const GRUNNLAGSTEKST = {
  samtykke: "ui.telefoni.grunnlag_samtykke",
  avtale: "ui.telefoni.grunnlag_avtale",
  berettiget_interesse: "ui.telefoni.grunnlag_berettiget",
  rettslig_forpliktelse: "ui.telefoni.grunnlag_rettslig",
};

const UTFALLTEKST = {
  haandtert: "ui.telefoni.utfall_haandtert",
  henlagt: "ui.telefoni.utfall_henlagt",
};

const FUNNTEKST = {
  samtale_uten_avslutning: "ui.telefoni.funn_hengende",
  eskalering_over_frist: "ui.telefoni.funn_glemt",
  ubekreftet_linje_uavklart: "ui.telefoni.funn_ubekreftet",
  opptak_uten_hjemmel: "ui.telefoni.funn_uten_hjemmel",
  opptak_uten_varsling: "ui.telefoni.funn_uten_varsling",
  agenten_skjulte_at_den_er_automatisert: "ui.telefoni.funn_skjult",
  eskalering_uten_regel: "ui.telefoni.funn_uten_regel",
};


export function prosent(bp) {
  if (typeof bp !== "number") return "–";
  return `${Math.round(bp / 100)} %`;
}


export function dato(iso) {
  if (typeof iso !== "string" || !iso) return "–";
  return iso.slice(0, 16).replace("T", " ");
}


// SEKUNDENE TIL IDENTIFIKASJON, MED FRISTEN SOM GJALDT.
//
// Tallet alene sier ingenting: fire sekunder er raskt for én tenant og
// for sent for en annen. Derfor står fristen ved siden av.
export function identifikasjonstekst(sek, frist) {
  if (typeof sek !== "number") return "–";
  const t1 = t("ui.telefoni.ident_verdi").replace("{n}", String(sek));
  if (typeof frist !== "number") return t1;
  return `${t1} ${t("ui.telefoni.ident_frist").replace("{n}", String(frist))}`;
}


// DAGENS DATO I BRUKERENS EGEN SONE, IKKE I UTC.
//
// `new Date().toISOString().slice(0, 10)` gir UTC-datoen. Norge ligger
// FORAN UTC, så mellom midnatt og 01/02 om natten gir den GÅRSDAGEN —
// og en regel som ble registrert «i dag» ville da blitt forsøkt
// avviklet dagen FØR den gjaldt. Døra nekter det, med rette, og
// brukeren ville sett en uforklarlig feil som forsvant om morgenen.
//
// CodeRabbit fant den 5/9. Den var arvet fra 133s flate, og er rettet
// begge steder.
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


// KNAPPEN OG LYTTEREN I ETT (127s lærdom).
function knappMed(tekst, ved) {
  const b = el("button", { type: "button", text: tekst });
  b.addEventListener("click", ved);
  return b;
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
          ? t("ui.telefoni.feil.tilstand")
          : t("ui.telefoni.feil.generell") }));
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
// Den lengste tiden noen snakket med en maskin uten å vite det, er det
// ene tallet som betyr at noe kan ha gått galt allerede. Deretter
// eskaleringene ingen tok: den andre parten fikk beskjed om at noen
// skulle ta over.
export function sammendrag(s) {
  const p = el("p", {});
  if (s.tregeste_identifikasjon_sek != null
      && s.identifikasjonsfrist_sek != null
      && s.tregeste_identifikasjon_sek > s.identifikasjonsfrist_sek) {
    p.append(el("strong", { role: "alert",
      text: t("ui.telefoni.treg_identifikasjon")
        .replace("{n}", String(s.tregeste_identifikasjon_sek))
        .replace("{frist}", String(s.identifikasjonsfrist_sek)) }), " ");
  }
  if (s.apne_eskaleringer > 0) {
    p.append(el("strong", { role: "alert",
      text: t("ui.telefoni.apne_eskaleringer")
        .replace("{n}", String(s.apne_eskaleringer)) }), " ");
  }
  p.append(el("span", { text: t("ui.telefoni.sammendrag_tekst")
    .replace("{samtaler}", String(s.samtaler ?? 0))
    .replace("{linjer}", String(s.linjer ?? 0))
    .replace("{ubekreftede}", String(s.ubekreftede ?? 0))
    .replace("{opptak}", String(s.opptak ?? 0)) }));
  if (!s.har_krav) {
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.telefoni.mangler_krav") }));
  }
  return p;
}


// TRANSKRIPSJONEN. USIKKERHETEN I SAMME RAD SOM TEKSTEN.
export function transkripsjonstabell(linjer) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.telefoni.rekkefolge", "ui.telefoni.taler",
                   "ui.telefoni.linje", "ui.telefoni.tidspunkt",
                   "ui.telefoni.sikkerhet", "ui.telefoni.status"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const l of linjer) {
    const status = el("td", {});
    if (l.ubekreftet) {
      // EN UBEKREFTET LINJE ER ET VARSEL, IKKE EN FOTNOTE.
      status.append(el("strong", { role: "alert",
        text: t("ui.telefoni.ubekreftet") }));
    } else if (l.er_rettet) {
      // EN RETTET LINJE ER SYNLIG SOM RETTET, ikke borte.
      status.append(el("span", { text: t("ui.telefoni.rettet") }));
    } else {
      status.append(el("span", { text: t("ui.telefoni.bekreftet") }));
    }
    const tn = TALERTEKST[l.taler];
    kropp.append(el("tr", {},
      el("td", { text: String(l.rekkefolge) }),
      el("td", { text: tn ? t(tn) : String(l.taler) }),
      el("td", { text: l.tekst }),
      el("td", { text: dato(l.linje_ts) }),
      // TALLET OG TERSKELEN SOM GJALDT DA, side om side.
      el("td", { text: t("ui.telefoni.sikkerhet_verdi")
        .replace("{n}", prosent(l.sikkerhet_bp))
        .replace("{terskel}", prosent(l.terskel_bp)) }),
      status));
  }
  tabell.append(kropp);
  return tabell;
}


export function samtaletabell(samtaler, frist, aapne) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.telefoni.motpart", "ui.telefoni.retning",
                   "ui.telefoni.startet", "ui.telefoni.identifikasjon",
                   "ui.telefoni.opptak", "ui.telefoni.linjer",
                   "ui.telefoni.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const s of samtaler) {
    const ident = el("td", {});
    const forSent = typeof frist === "number"
      && s.sekunder_til_identifikasjon > frist;
    if (forSent) {
      // DEN SOM HAR SNAKKET FOR LENGE FØR HUN FÅR VITE HVA HUN SNAKKER
      // MED, HAR ALLEREDE SVART SOM TIL ET MENNESKE.
      ident.append(el("strong", { role: "alert",
        text: identifikasjonstekst(s.sekunder_til_identifikasjon,
                                   frist) }));
    } else {
      ident.append(el("span", {
        text: identifikasjonstekst(s.sekunder_til_identifikasjon,
                                   frist) }));
    }
    // ORDLYDEN STÅR UNDER TALLET. «Agenten identifiserte seg» er en
    // påstand; dette er hva den faktisk sa.
    ident.append(el("p", { class: "muted",
                          text: s.identifikasjonstekst }));
    const opptak = el("td", {});
    if (!s.har_opptak) {
      opptak.append(el("span", { text: t("ui.telefoni.intet_opptak") }));
    } else {
      const gn = GRUNNLAGSTEKST[s.opptakshjemmel];
      opptak.append(el("span", {
        text: gn ? t(gn) : String(s.opptakshjemmel) }));
    }
    const linjer = el("td", {});
    linjer.append(el("span", { text: String(s.antall_linjer) }));
    if (s.antall_ubekreftede > 0) {
      linjer.append(" ", el("strong", { role: "alert",
        text: t("ui.telefoni.av_dem_ubekreftet")
          .replace("{n}", String(s.antall_ubekreftede)) }));
    }
    const rn = RETNINGSTEKST[s.retning];
    kropp.append(el("tr", {},
      el("td", { text: s.motpart }),
      el("td", { text: rn ? t(rn) : String(s.retning) }),
      el("td", { text: s.slutt_ts
        ? dato(s.startet_ts)
        : t("ui.telefoni.paagaar").replace("{dato}",
                                           dato(s.startet_ts)) }),
      ident, opptak, linjer,
      el("td", {}, aapne
        ? knappMed(t("ui.telefoni.vis_transkripsjon"), () => aapne(s))
        : el("span", { text: "–" }))));
  }
  tabell.append(kropp);
  return tabell;
}


// ESKALERINGENE, MED REGELEN SOM BAR DEM.
export function eskaleringstabell(eskaleringer, frist, lukk) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.telefoni.regel", "ui.telefoni.mottaker",
                   "ui.telefoni.begrunnelse", "ui.telefoni.eskalert",
                   "ui.telefoni.status", "ui.telefoni.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const e of eskaleringer) {
    const status = el("td", {});
    if (e.lukket_ts) {
      const un = UTFALLTEKST[e.lukket_utfall];
      status.append(el("span", { text: t("ui.telefoni.lukket_verdi")
        .replace("{utfall}", un ? t(un) : String(e.lukket_utfall))
        .replace("{av}", e.lukket_av || "") }));
    } else if (typeof frist === "number" && e.dogn_apen > frist) {
      // DEN DYRESTE STILLHETEN I MODULEN: den andre parten fikk
      // beskjed om at noen skulle ta over.
      status.append(el("strong", { role: "alert",
        text: t("ui.telefoni.apen_dogn")
          .replace("{n}", String(e.dogn_apen)) }));
    } else {
      status.append(el("span", { text: t("ui.telefoni.apen") }));
    }
    kropp.append(el("tr", {},
      // REGELEN, IKKE BARE REGEL-ID-EN. Den som ble vekket skal kunne
      // lese hvorfor.
      el("td", { text: e.regeltekst }),
      el("td", { text: e.mottaker }),
      el("td", { text: e.begrunnelse }),
      el("td", { text: dato(e.eskalert_ts) }),
      status,
      el("td", {}, (!e.lukket_ts && lukk)
        ? knappMed(t("ui.telefoni.lukk"), () => lukk(e))
        : el("span", { text: "–" }))));
  }
  tabell.append(kropp);
  return tabell;
}


export function regeltabell(regler, avvikl) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.telefoni.regel", "ui.telefoni.mottaker",
                   "ui.telefoni.gyldig_fra", "ui.telefoni.status",
                   "ui.telefoni.brukt", "ui.telefoni.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const r of regler) {
    const status = el("td", {});
    if (r.gjelder) {
      status.append(el("span", { text: t("ui.telefoni.gjelder") }));
    } else {
      status.append(el("span", { text: t("ui.telefoni.avviklet") }));
    }
    kropp.append(el("tr", {},
      el("td", { text: r.beskrivelse }),
      el("td", { text: r.mottaker }),
      el("td", { text: r.gyldig_fra }),
      status,
      el("td", { text: String(r.antall_eskaleringer) }),
      el("td", {}, (r.gjelder && avvikl)
        ? knappMed(t("ui.telefoni.avvikl"), () => avvikl(r))
        : el("span", { text: "–" }))));
  }
  tabell.append(kropp);
  return tabell;
}


export function hjemmeltabell(hjemler, avslutt) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.telefoni.grunnlag", "ui.telefoni.formal",
                   "ui.telefoni.beskrivelse", "ui.telefoni.status",
                   "ui.telefoni.opptak"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const h of hjemler) {
    const status = el("td", {});
    if (h.gjelder) {
      status.append(el("span", { text: t("ui.telefoni.gjelder") }));
    } else {
      // EN UTLØPT HJEMMEL SER NØYAKTIG UT SOM EN GYLDIG.
      status.append(el("strong", { role: "alert",
        text: t("ui.telefoni.utlopt") }));
    }
    const gn = GRUNNLAGSTEKST[h.grunnlagstype];
    kropp.append(el("tr", {},
      el("td", { text: gn ? t(gn) : String(h.grunnlagstype) }),
      el("td", { text: h.formal }),
      el("td", { text: h.beskrivelse }),
      status,
      el("td", { text: String(h.antall_opptak) })));
  }
  if (avslutt) { /* v1 avslutter hjemler i M-7s flate, ikke her. */ }
  tabell.append(kropp);
  return tabell;
}


// FUNNTABELLEN. `kan_lukkes` LESES FRA BASEN (132s CodeRabbit-funn).
export function funntabell(funn, lukk) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.telefoni.funntype", "ui.telefoni.detaljer",
                   "ui.telefoni.forst_sett", "ui.telefoni.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const f of funn) {
    const handling = el("td", {});
    if (!f.apen) {
      handling.append(el("span", { text: t("ui.telefoni.lukket_av")
        .replace("{av}", f.lukket_av || "") }));
    } else if (!f.kan_lukkes) {
      handling.append(el("span", {
        text: t("ui.telefoni.lukkes_av_sveipen") }));
    } else if (lukk) {
      handling.append(knappMed(t("ui.telefoni.avklar"), () => lukk(f)));
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


// TRANSKRIPSJONSPANELET — hentes når noen spør.
export function transkripsjonspanel(ctx) {
  const node = el("section", { class: "kpi-kort", hidden: true });

  async function aapne(s) {
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.telefoni.transkripsjonen") }),
      el("p", { class: "muted", text: t("ui.telefoni.laster") }));
    let d;
    try {
      d = await hentJson(
        `/v1/telefoni/samtale/${s.samtale_id}/transkripsjon`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.telefoni.transkripsjonen") }),
        el("p", { role: "alert", text: t("ui.telefoni.feil.generell") }));
      return;
    }
    const linjer = d.linjer || [];
    const deler = [
      el("h2", { text: t("ui.telefoni.transkripsjonen") }),
      el("p", { class: "muted", text: t("ui.telefoni.samtale_om")
        .replace("{motpart}", s.motpart)
        .replace("{dato}", dato(s.startet_ts)) }),
    ];
    if (!linjer.length) {
      deler.push(el("p", { class: "muted",
                           text: t("ui.telefoni.ingen_linjer") }));
    } else {
      deler.push(transkripsjonstabell(linjer));
    }
    sett(node, ...deler);
  }

  return { node, aapne };
}


export function lukkepanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });

  function aapne(f) {
    node.hidden = false;
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    const form = el("form", { class: "skjema" });
    const grunn = el("input", { type: "text", id: "telefoni-grunn",
                                name: "grunn", required: true });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.telefoni.avklar") });
    form.append(
      el("p", { class: "muted", text: t("ui.telefoni.avklar_om") }),
      felt("telefoni-grunn", "ui.telefoni.grunn", grunn,
           "ui.telefoni.grunn_hjelp"),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.telefoni.avklart_ok",
      send: (idem) => lukkTelefonifunn(f.funn_id,
        { grunn: grunn.value }, idem),
    });
    sett(node, el("h2", { text: t("ui.telefoni.avklar") }), form);
  }

  return { node, aapne };
}


export function eskaleringslukkepanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });

  function aapne(e) {
    node.hidden = false;
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    const form = el("form", { class: "skjema" });
    const valg = velger("telefoni-utfall", ESKALERINGSUTFALL,
                        UTFALLTEKST);
    const knapp = el("button", { type: "submit",
                                 text: t("ui.telefoni.lukk") });
    form.append(
      // UTFALLET ER PÅKREVD: en lukking uten det ville gjort «ble det
      // gjort noe» til et spørsmål ingen kan svare på etterpå.
      el("p", { class: "muted", text: t("ui.telefoni.lukk_om")
        .replace("{regel}", e.regeltekst) }),
      felt("telefoni-utfall", "ui.telefoni.utfall", valg),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.telefoni.eskalering_lukket",
      send: (idem) => lukkEskalering(e.eskalering_id,
        { utfall: valg.value }, idem),
    });
    sett(node, el("h2", { text: t("ui.telefoni.lukk") }), form);
  }

  return { node, aapne };
}


// SAMTALEPANELET — MODULENS VIKTIGSTE SKJEMA.
//
// IDENTIFIKASJONEN STÅR FØR ALT ANNET, fordi det er rekkefølgen
// regelen har. Skjemaet ber om tidspunktet OG ordlyden: «agenten
// identifiserte seg» er en påstand, teksten er en måling.
export function samtaleskjema(ctx, last, kvitter, s) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const retning = velger("telefoni-retning", RETNINGER, RETNINGSTEKST);
  const motpart = el("input", { type: "text", id: "telefoni-motpart",
                                name: "motpart", required: true });
  const start = el("input", { type: "datetime-local",
                              id: "telefoni-startet",
                              name: "startet_ts", required: true });
  const ident = el("input", { type: "datetime-local",
                              id: "telefoni-identifisert",
                              name: "identifisert_ts", required: true });
  const identtekst = el("input", { type: "text",
                                   id: "telefoni-identtekst",
                                   name: "identifikasjonstekst",
                                   required: true });
  const knapp = el("button", { type: "submit",
                               text: t("ui.telefoni.registrer_samtale") });
  form.append(
    el("p", { class: "muted", text: t("ui.telefoni.samtale_forklaring")
      .replace("{n}", String(s.identifikasjonsfrist_sek ?? 10)) }),
    felt("telefoni-retning", "ui.telefoni.retning", retning),
    felt("telefoni-motpart", "ui.telefoni.motpart", motpart),
    felt("telefoni-startet", "ui.telefoni.startet", start),
    // IDENTIFIKASJONEN ETTER STARTEN, fordi det er rekkefølgen.
    felt("telefoni-identifisert", "ui.telefoni.identifisert", ident,
         "ui.telefoni.identifisert_hjelp"),
    felt("telefoni-identtekst", "ui.telefoni.identtekst", identtekst,
         "ui.telefoni.identtekst_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.telefoni.samtale_registrert",
    tilbakestill: () => { motpart.value = ""; },
    send: (idem) => {
      // TOMME FELT SENDES IKKE. `new Date("")` er `Invalid Date`, og
      // en ugyldig dato sendt til en `timestamptz` ville gitt en 500 i
      // stedet for et nekt kalleren kan handle på (133s lærdom).
      for (const f of [motpart, start, ident, identtekst]) {
        if (f.value.trim() === "") {
          throw new FeilformetFeil(400, "felt_mangler");
        }
      }
      return startSamtale({
        retning: retning.value,
        motpart: motpart.value.trim(),
        startet_ts: new Date(start.value).toISOString(),
        identifisert_ts: new Date(ident.value).toISOString(),
        identifikasjonstekst: identtekst.value.trim(),
      }, idem);
    },
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.telefoni.ny_samtale") }), form);
}


export function regelskjema(ctx, last, kvitter) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const besk = el("input", { type: "text", id: "telefoni-regeltekst",
                             name: "beskrivelse", required: true,
                             minLength: "16" });
  const mottaker = el("input", { type: "text", id: "telefoni-mottaker",
                                 name: "mottaker", required: true });
  const fra = el("input", { type: "date", id: "telefoni-regelfra",
                            name: "gyldig_fra", required: true });
  const knapp = el("button", { type: "submit",
                               text: t("ui.telefoni.lagre_regel") });
  form.append(
    // «ESKALERINGSREGLER ER KUNDENS», sagt i skjemaet.
    el("p", { class: "muted", text: t("ui.telefoni.regel_om") }),
    felt("telefoni-regeltekst", "ui.telefoni.regel", besk,
         "ui.telefoni.regel_hjelp"),
    felt("telefoni-mottaker", "ui.telefoni.mottaker", mottaker,
         "ui.telefoni.mottaker_hjelp"),
    felt("telefoni-regelfra", "ui.telefoni.gyldig_fra", fra),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.telefoni.regel_lagret",
    tilbakestill: () => { besk.value = ""; mottaker.value = ""; },
    send: (idem) => registrerEskaleringsregel({
      beskrivelse: besk.value,
      mottaker: mottaker.value,
      gyldig_fra: fra.value,
      gyldig_til: null,
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.telefoni.ny_regel") }), form);
}


export function hjemmelskjema(ctx, last, kvitter) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const typ = velger("telefoni-grunnlag", GRUNNLAGSTYPER,
                     GRUNNLAGSTEKST);
  const besk = el("input", { type: "text", id: "telefoni-hjemmeltekst",
                             name: "beskrivelse", required: true,
                             minLength: "16" });
  const formal = el("input", { type: "text", id: "telefoni-formal",
                               name: "formal", required: true });
  const fra = el("input", { type: "date", id: "telefoni-hjemmelfra",
                            name: "gyldig_fra", required: true });
  const knapp = el("button", { type: "submit",
                               text: t("ui.telefoni.lagre_hjemmel") });
  form.append(
    // DEN DELTE HJEMMELEN, sagt rett ut: den er M-7s, og de to
    // modulene deler den.
    el("p", { class: "muted", text: t("ui.telefoni.hjemmel_om") }),
    felt("telefoni-grunnlag", "ui.telefoni.grunnlag", typ,
         "ui.telefoni.grunnlag_hjelp"),
    felt("telefoni-hjemmeltekst", "ui.telefoni.beskrivelse", besk),
    felt("telefoni-formal", "ui.telefoni.formal", formal),
    felt("telefoni-hjemmelfra", "ui.telefoni.gyldig_fra", fra),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.telefoni.hjemmel_lagret",
    tilbakestill: () => { besk.value = ""; formal.value = ""; },
    send: (idem) => registrerOpptakshjemmelTelefoni({
      grunnlagstype: typ.value,
      beskrivelse: besk.value,
      formal: formal.value,
      gyldig_fra: fra.value,
      gyldig_til: null,
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.telefoni.ny_hjemmel") }), form);
}


export function kravskjema(ctx, last, kvitter, s) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  // ALLE FIRE GRENSENE FORHÅNDSUTFYLLES (123s lærdom).
  const terskel = el("input", { type: "number", id: "telefoni-terskel",
                                name: "sikkerhetsterskel_bp", min: "1",
                                max: "10000", required: true,
                                value: String(s.sikkerhetsterskel_bp ?? 7000) });
  const identfrist = el("input", { type: "number",
                                   id: "telefoni-identfrist",
                                   name: "identifikasjonsfrist_sek",
                                   min: "1", max: "120", required: true,
                                   value: String(s.identifikasjonsfrist_sek ?? 10) });
  const eskfrist = el("input", { type: "number", id: "telefoni-eskfrist",
                                 name: "eskaleringsfrist_dogn", min: "1",
                                 max: "90", required: true,
                                 value: String(s.eskaleringsfrist_dogn ?? 3) });
  const tak = el("input", { type: "number", id: "telefoni-tak",
                            name: "samtaletak_timer", min: "1",
                            max: "168", required: true,
                            value: String(s.samtaletak_timer ?? 24) });
  const knapp = el("button", { type: "submit",
                               text: t("ui.telefoni.lagre_krav") });
  form.append(
    el("p", { class: "muted", text: t("ui.telefoni.krav_om") }),
    felt("telefoni-terskel", "ui.telefoni.sikkerhetsterskel", terskel,
         "ui.telefoni.terskel_hjelp"),
    felt("telefoni-identfrist", "ui.telefoni.identifikasjonsfrist",
         identfrist, "ui.telefoni.identfrist_hjelp"),
    felt("telefoni-eskfrist", "ui.telefoni.eskaleringsfrist", eskfrist),
    felt("telefoni-tak", "ui.telefoni.samtaletak", tak,
         "ui.telefoni.samtaletak_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.telefoni.krav_lagret",
    send: (idem) => settTelefonikrav({
      sikkerhetsterskel_bp: Number(terskel.value),
      identifikasjonsfrist_sek: Number(identfrist.value),
      eskaleringsfrist_dogn: Number(eskfrist.value),
      samtaletak_timer: Number(tak.value),
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.telefoni.krav") }), form);
}


// OPPTAKSPANELET — 133s FORM, ARVET.
//
// VARSLINGEN FØR STARTEN, fordi det er rekkefølgen regelen har. ET
// NEKT SOM KOMMER ETTER MIKROFONEN ER IKKE ET NEKT.
export function opptakspanel(ctx, last, kvitter, hjemler) {
  const node = el("section", { class: "kpi-kort", hidden: true });

  function aapne(sam) {
    node.hidden = false;
    const gyldige = (hjemler || []).filter((h) => h.gjelder);
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    if (!gyldige.length) {
      // UTEN EN GYLDIG HJEMMEL NEKTER DØRA, og panelet sier det i
      // stedet for å la brukeren finne det ut av en 400.
      sett(node, el("h2", { text: t("ui.telefoni.start_opptak") }),
        el("p", { role: "alert",
                  text: t("ui.telefoni.ingen_gyldig_hjemmel") }));
      return;
    }
    const form = el("form", { class: "skjema" });
    const valg = el("select", { id: "telefoni-hjemmelvalg",
                                name: "hjemmel_id" });
    for (const h of gyldige) {
      const gn = GRUNNLAGSTEKST[h.grunnlagstype];
      valg.append(el("option", { value: h.hjemmel_id,
        text: `${gn ? t(gn) : h.grunnlagstype} — ${h.formal}` }));
    }
    const varslet = el("input", { type: "datetime-local",
                                  id: "telefoni-varslet",
                                  name: "varslet_ts", required: true });
    const varsletAv = el("input", { type: "text",
                                    id: "telefoni-varsletav",
                                    name: "varslet_av", required: true });
    const varslede = el("input", { type: "text", id: "telefoni-varslede",
                                   name: "varslede", required: true });
    const startet = el("input", { type: "datetime-local",
                                  id: "telefoni-opptakstart",
                                  name: "startet_ts", required: true });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.telefoni.start_opptak") });
    form.append(
      el("p", { class: "muted",
                text: t("ui.telefoni.opptak_forklaring") }),
      felt("telefoni-hjemmelvalg", "ui.telefoni.hjemmel", valg),
      // VARSLINGEN FØRST.
      felt("telefoni-varslet", "ui.telefoni.varslet", varslet,
           "ui.telefoni.varslet_hjelp"),
      felt("telefoni-varsletav", "ui.telefoni.varslet_av", varsletAv),
      felt("telefoni-varslede", "ui.telefoni.varslede", varslede,
           "ui.telefoni.varslede_hjelp"),
      felt("telefoni-opptakstart", "ui.telefoni.opptak_startet", startet),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.telefoni.opptak_registrert",
      send: (idem) => {
        for (const f of [varslet, varsletAv, varslede, startet]) {
          if (f.value.trim() === "") {
            throw new FeilformetFeil(400, "felt_mangler");
          }
        }
        return startSamtaleopptak(sam.samtale_id, {
          hjemmel_id: valg.value,
          varslet_ts: new Date(varslet.value).toISOString(),
          varslet_av: varsletAv.value,
          varslede: varslede.value.split(",").map((x) => x.trim())
            .filter((x) => x !== ""),
          startet_ts: new Date(startet.value).toISOString(),
        }, idem);
      },
    });
    sett(node, el("h2", { text: t("ui.telefoni.start_opptak") }),
      el("p", { class: "muted", text: t("ui.telefoni.opptak_om")
        .replace("{motpart}", sam.motpart) }),
      form);
  }

  return { node, aapne };
}


// LINJEPANELET — EN RETTELSE ER EN NY LINJE.
//
// Panelet skriver bare MANUELLE linjer: en transkripsjon kommer fra
// motoren, ikke fra et skjema. Derfor er `kilde` ikke et valg her, og
// sikkerheten tvinges til full av døra — ET MENNESKE SOM SKREV SELV,
// HØRTE IKKE FEIL.
export function linjepanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });

  function aapne(sam, retterLinje) {
    node.hidden = false;
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    const form = el("form", { class: "skjema" });
    const taler = velger("telefoni-taler", TALERE, TALERTEKST);
    const nr = el("input", { type: "number", id: "telefoni-linjenr",
                             name: "rekkefolge", min: "1",
                             required: true,
                             value: String((sam.antall_linjer || 0) + 1) });
    const naar = el("input", { type: "datetime-local",
                               id: "telefoni-linjenaar",
                               name: "linje_ts", required: true });
    const tekst = el("input", { type: "text", id: "telefoni-linjetekst",
                                name: "tekst", required: true });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.telefoni.lagre_linje") });
    form.append(
      el("p", { class: "muted", text: retterLinje
        ? t("ui.telefoni.linje_retter")
        : t("ui.telefoni.linje_om") }),
      felt("telefoni-linjenr", "ui.telefoni.rekkefolge", nr),
      felt("telefoni-taler", "ui.telefoni.taler", taler),
      felt("telefoni-linjenaar", "ui.telefoni.tidspunkt", naar,
           "ui.telefoni.linje_tid_hjelp"),
      felt("telefoni-linjetekst", "ui.telefoni.linje", tekst),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.telefoni.linje_lagret",
      tilbakestill: () => { tekst.value = ""; },
      send: (idem) => {
        for (const f of [naar, tekst]) {
          if (f.value.trim() === "") {
            throw new FeilformetFeil(400, "felt_mangler");
          }
        }
        return registrerTranskripsjonslinje(sam.samtale_id, {
          rekkefolge: Number(nr.value),
          taler: taler.value,
          linje_ts: new Date(naar.value).toISOString(),
          tekst: tekst.value,
          // MANUELL, ALLTID. Døra tvinger sikkerheten til full.
          kilde: "manuell",
          sikkerhet_bp: 10000,
          retter_linje_id: retterLinje || null,
        }, idem);
      },
    });
    sett(node, el("h2", { text: t("ui.telefoni.ny_linje") }), form);
  }

  return { node, aapne };
}


// ESKALERINGSPANELET — REGELEN ER IKKE VALGFRI.
export function eskaleringspanel(ctx, last, kvitter, regler) {
  const node = el("section", { class: "kpi-kort", hidden: true });

  function aapne(sam) {
    node.hidden = false;
    const gjeldende = (regler || []).filter((r) => r.gjelder);
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    if (!gjeldende.length) {
      // EN ESKALERING UTEN EN REGEL Å PEKE PÅ ER MODULENS EGEN
      // BESLUTNING, og skjemaet kan ikke gjøre den mulig.
      sett(node, el("h2", { text: t("ui.telefoni.eskaler") }),
        el("p", { role: "alert",
                  text: t("ui.telefoni.ingen_gjeldende_regel") }));
      return;
    }
    const form = el("form", { class: "skjema" });
    const valg = el("select", { id: "telefoni-regelvalg",
                                name: "regel_id" });
    for (const r of gjeldende) {
      valg.append(el("option", { value: r.regel_id,
        text: `${r.beskrivelse} → ${r.mottaker}` }));
    }
    const grunn = el("input", { type: "text", id: "telefoni-eskgrunn",
                                name: "begrunnelse", required: true });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.telefoni.eskaler") });
    form.append(
      el("p", { class: "muted", text: t("ui.telefoni.eskaler_om")
        .replace("{motpart}", sam.motpart) }),
      felt("telefoni-regelvalg", "ui.telefoni.regel", valg,
           "ui.telefoni.regelvalg_hjelp"),
      felt("telefoni-eskgrunn", "ui.telefoni.begrunnelse", grunn),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.telefoni.eskalert_ok",
      send: (idem) => eskalerSamtale(sam.samtale_id, {
        regel_id: valg.value,
        begrunnelse: grunn.value,
      }, idem),
    });
    sett(node, el("h2", { text: t("ui.telefoni.eskaler") }), form);
  }

  return { node, aapne };
}


export function visTelefoni(hoved, ctx) {
  const hode = () => flateHode(t("ui.telefoni.tittel"),
    t("ui.telefoni.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/telefoni"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const samtaler = d.samtaler || [];
      const hjemler = d.hjemler || [];
      const regler = d.regler || [];
      const eskaleringer = d.eskaleringer || [];
      const funn = d.funn || [];
      const skriver = harScope(ctx, "bestilling:opprett");
      const transkripsjon = transkripsjonspanel(ctx);
      const lukking = lukkepanel(ctx, last, kvitter);
      const esklukking = eskaleringslukkepanel(ctx, last, kvitter);
      const opptak = opptakspanel(ctx, last, kvitter, hjemler);
      const linje = linjepanel(ctx, last, kvitter);
      const eskalering = eskaleringspanel(ctx, last, kvitter, regler);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.telefoni.sammendrag") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT.
        el("p", { class: "muted", text: t("ui.telefoni.hvorfor") }));

      const funnseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.telefoni.funn") }));
      if (!funn.length) {
        funnseksjon.append(el("p", { class: "muted",
          text: t("ui.telefoni.funn_tomt") }));
      } else {
        funnseksjon.append(funntabell(
          funn, skriver ? lukking.aapne : null));
      }

      const eskseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.telefoni.eskaleringer") }));
      if (!eskaleringer.length) {
        eskseksjon.append(el("p", { class: "muted",
          text: t("ui.telefoni.eskaleringer_tomt") }));
      } else {
        eskseksjon.append(eskaleringstabell(
          eskaleringer, s.eskaleringsfrist_dogn,
          skriver ? esklukking.aapne : null));
      }

      const samtaleseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.telefoni.samtaler") }));
      if (!samtaler.length) {
        samtaleseksjon.append(el("p", { class: "muted",
          text: t("ui.telefoni.samtaler_tomt") }));
      } else {
        samtaleseksjon.append(samtaletabell(
          samtaler, s.identifikasjonsfrist_sek, transkripsjon.aapne));
        if (skriver) {
          const velg = el("div", { class: "felt" });
          for (const x of samtaler.filter((y) => !y.slutt_ts)
                                  .slice(0, 20)) {
            velg.append(knappMed(
              t("ui.telefoni.avslutt_for").replace("{motpart}", x.motpart),
              async () => {
                try {
                  await avsluttSamtale(x.samtale_id,
                    { slutt_ts: new Date().toISOString() },
                    nyIdempotensnokkel());
                } catch (e) {
                  if (e instanceof UautorisertFeil) {
                    ctx.paaUautorisert();
                    return;
                  }
                  kvitter(t("ui.telefoni.feil.generell"));
                  return;
                }
                meldLive(t("ui.telefoni.samtale_avsluttet"));
                kvitter(t("ui.telefoni.samtale_avsluttet"));
                await last();
              }));
          }
          if (velg.childNodes.length) samtaleseksjon.append(velg);
          // HANDLINGENE PER SAMTALE: opptak, manuell linje, eskalering.
          const handlinger = el("div", { class: "felt" });
          for (const x of samtaler.slice(0, 10)) {
            handlinger.append(
              knappMed(t("ui.telefoni.opptak_for")
                .replace("{motpart}", x.motpart), () => opptak.aapne(x)),
              knappMed(t("ui.telefoni.linje_for")
                .replace("{motpart}", x.motpart),
                () => linje.aapne(x, null)),
              knappMed(t("ui.telefoni.eskaler_for")
                .replace("{motpart}", x.motpart),
                () => eskalering.aapne(x)));
          }
          samtaleseksjon.append(handlinger);
        }
      }

      const regelseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.telefoni.regler") }));
      if (!regler.length) {
        regelseksjon.append(el("p", { class: "muted",
          text: t("ui.telefoni.regler_tomt") }));
      } else {
        regelseksjon.append(regeltabell(regler, skriver
          ? (r) => avviklRegel(ctx, last, kvitter, r) : null));
      }

      const hjemmelseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.telefoni.hjemler") }));
      if (!hjemler.length) {
        hjemmelseksjon.append(el("p", { class: "muted",
          text: t("ui.telefoni.hjemler_tomt") }));
      } else {
        hjemmelseksjon.append(hjemmeltabell(hjemler, null));
      }

      // FUNNENE FØRST, SÅ ESKALERINGENE. Det som haster er en
      // eskalering ingen tok — ikke listen over hvor mange samtaler vi
      // har hatt.
      const deler = [oversikt, funnseksjon, lukking.node,
                     eskseksjon, esklukking.node,
                     samtaleseksjon, transkripsjon.node,
                     opptak.node, linje.node, eskalering.node,
                     regelseksjon, hjemmelseksjon];
      if (skriver) {
        deler.push(samtaleskjema(ctx, last, kvitter, s),
                   regelskjema(ctx, last, kvitter),
                   hjemmelskjema(ctx, last, kvitter),
                   kravskjema(ctx, last, kvitter, s));
      }
      sett(kropp, ...deler);
    });

  async function avviklRegel(c, l, k, r) {
    // AVVIKLING ER ENVEIS, og datoen er i dag. En regel som kunne
    // avvikles fram i tid ville gjort «gjelder den nå?» til et
    // spørsmål med to svar (133s form).
    try {
      await avviklEskaleringsregel(r.regel_id,
        { gyldig_til: iDagLokal() },
        nyIdempotensnokkel());
    } catch (e) {
      if (e instanceof UautorisertFeil) { c.paaUautorisert(); return; }
      k(t("ui.telefoni.feil.generell"));
      return;
    }
    meldLive(t("ui.telefoni.regel_avviklet"));
    k(t("ui.telefoni.regel_avviklet"));
    await l();
  }

  return last();
}
