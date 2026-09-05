// M-7 møteoperasjonsagent (133) — REFERATET ER PRODUKTET.
//
// FLATENS VIKTIGSTE JOBB ER Å VISE HVA MASKINEN VAR USIKKER PÅ.
//
// Klyngens delte dom: en ytring avgitt i husets navn kan ikke tas
// tilbake — og DEN SOM LESER DEN VET IKKE AT EN MASKIN SKREV DEN. Et
// referat ser likt ut enten et menneske skrev det eller en
// transkripsjon gjettet. Derfor står `ubekreftet` som et VARSEL i
// samme rad som teksten, ikke som en fotnote, og kilden står ved
// siden av: den som leser skal se om punktet kom fra et opptak, fra
// agendaen, eller fra et menneske som skrev selv.
//
// EN BESLUTNING TATT PÅ ET UBEKREFTET PUNKT BÆRER DET VIDERE.
// Usikkerheten forsvinner ikke fordi noen skrev «besluttet» over den,
// og beslutningslisten merker det.
//
// OPPTAK VISES MED SITT GRUNNLAG, ALLTID. Den som ser at et møte ble
// tatt opp, skal se hvorfor det var lov — uten et klikk til. Et
// opptak uten synlig hjemmel ville sett ut som et opptak uten hjemmel,
// og forskjellen er hele modulen.
//
// DET FINNES INGEN «FATT BESLUTNING»-KNAPP, OG DET KAN IKKE FINNES.
// Skjemaet krever `besluttet_av`: modulen skriver ned beslutningen
// menneskene tok, den fatter den ikke.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  avsluttOpptakshjemmel, FeilformetFeil, hentJson, lukkMoteaksjon,
  lukkMotefunn, nyIdempotensnokkel, registrerBeslutning,
  registrerMote, registrerMoteaksjon, registrerOpptakshjemmel,
  registrerReferatpunkt, settMotekrav, startOpptak, UautorisertFeil,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

export const GRUNNLAGSTYPER = ["samtykke", "avtale",
                               "berettiget_interesse",
                               "rettslig_forpliktelse"];
export const KILDER = ["opptak", "manuell", "agenda"];
export const AKSJONSLUKKINGER = ["utfort", "henlagt"];

const GRUNNLAGSTEKST = {
  samtykke: "ui.mote.grunnlag_samtykke",
  avtale: "ui.mote.grunnlag_avtale",
  berettiget_interesse: "ui.mote.grunnlag_berettiget",
  rettslig_forpliktelse: "ui.mote.grunnlag_rettslig",
};

const KILDETEKST = {
  opptak: "ui.mote.kilde_opptak",
  manuell: "ui.mote.kilde_manuell",
  agenda: "ui.mote.kilde_agenda",
};

const LUKKETEKST = {
  utfort: "ui.mote.aksjon_utfort",
  henlagt: "ui.mote.aksjon_henlagt",
};

const FUNNTEKST = {
  mote_uten_referat: "ui.mote.funn_uten_referat",
  aksjon_over_frist: "ui.mote.funn_over_frist",
  ubekreftet_punkt_uavklart: "ui.mote.funn_ubekreftet",
  opptak_uten_hjemmel: "ui.mote.funn_uten_hjemmel",
};


// SIKKERHET VISES SOM PROSENT, MEN LAGRES I BASISPUNKTER.
//
// Basispunkter fordi resten av huset regner usikkerhet slik (M-15,
// M-36), og heltall fordi `0.1 + 0.2` ikke er `0.3`. Prosenten er en
// AVLEDNING for skjermen, og den avrundes — men tallet som avgjorde
// merkingen er det som står i basen.
export function prosent(bp) {
  if (typeof bp !== "number") return "–";
  return `${Math.round(bp / 100)} %`;
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


// KNAPPEN OG LYTTEREN I ETT. `el()` setter ukjente nøkler med
// `setAttribute`, så `onclick: fn` ville blitt STRENGEN «() => …» i
// attributtet — en knapp som ser ut som den virker og ikke gjør det
// (127s lærdom).
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
          ? t("ui.mote.feil.tilstand")
          : t("ui.mote.feil.generell") }));
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
// En beslutning tatt på et punkt maskinen var usikker på, er det ene
// tallet som betyr at noe kan ha gått galt allerede. Deretter møtene
// uten referat: et referat som ikke finnes er en gjengivelse ingen
// kan etterprøve.
export function sammendrag(s) {
  const p = el("p", {});
  if (s.beslutninger_paa_ubekreftet > 0) {
    p.append(el("strong", { role: "alert",
      text: t("ui.mote.beslutning_paa_ubekreftet")
        .replace("{n}", String(s.beslutninger_paa_ubekreftet)) }),
      " ");
  }
  p.append(el("strong", {
    text: t("ui.mote.uten_referat_sum")
      .replace("{n}", String(s.moter_uten_referat ?? 0)) }));
  if (s.ubekreftede > 0) {
    p.append(" ", el("strong", {
      text: t("ui.mote.ubekreftede_sum")
        .replace("{n}", String(s.ubekreftede))
        .replace("{av}", String(s.punkter ?? 0)) }));
  }
  if (s.aksjoner_over_frist > 0) {
    p.append(" ", el("strong", {
      text: t("ui.mote.over_frist_sum")
        .replace("{n}", String(s.aksjoner_over_frist)) }));
  }
  p.append(" ", el("span", {
    text: t("ui.mote.tellinger")
      .replace("{moter}", String(s.moter ?? 0))
      .replace("{punkter}", String(s.punkter ?? 0))
      .replace("{aksjoner}", String(s.apne_aksjoner ?? 0)) }));
  if (s.opptak > 0) {
    p.append(" ", el("span", { class: "muted",
      text: t("ui.mote.opptak_sum")
        .replace("{n}", String(s.opptak))
        .replace("{hjemler}", String(s.gyldige_hjemler ?? 0)) }));
  }
  if (s.apne_funn > 0) {
    p.append(" ", el("strong", {
      text: t("ui.mote.apne_funn_sum")
        .replace("{n}", String(s.apne_funn)) }));
  }
  if (!s.har_krav) {
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.mote.krav_mangler") }));
  } else {
    p.append(" ", el("span", { class: "muted",
      text: t("ui.mote.grensene_er")
        .replace("{referat}", String(s.referatfrist_dogn ?? 0))
        .replace("{aksjon}", String(s.aksjonsfrist_dogn ?? 0))
        .replace("{terskel}", prosent(s.sikkerhetsterskel_bp)) }));
  }
  return p;
}


// REFERATTABELLEN. USIKKERHETEN OG KILDEN I SAMME RAD SOM TEKSTEN.
export function referattabell(punkter) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.mote.rekkefolge", "ui.mote.punkt",
                   "ui.mote.kilde", "ui.mote.sikkerhet",
                   "ui.mote.status", "ui.mote.registrert_av"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const p of punkter) {
    const status = el("td", {});
    if (p.ubekreftet) {
      // ET UBEKREFTET PUNKT ER ET VARSEL, IKKE EN FOTNOTE.
      status.append(el("strong", { role: "alert",
        text: t("ui.mote.ubekreftet") }));
    } else if (p.er_rettet) {
      // ET RETTET PUNKT ER SYNLIG SOM RETTET, ikke borte: referatet
      // er append-only, og den som leser skal se at noe ble
      // korrigert.
      status.append(el("span", { text: t("ui.mote.rettet") }));
    } else {
      status.append(el("span", { text: t("ui.mote.bekreftet") }));
    }
    const kn = KILDETEKST[p.kilde];
    kropp.append(el("tr", {},
      el("td", { text: String(p.rekkefolge) }),
      el("td", { text: p.tekst }),
      el("td", { text: kn ? t(kn) : String(p.kilde) }),
      // TALLET OG TERSKELEN SOM GJALDT DA, side om side.
      el("td", { text: t("ui.mote.sikkerhet_verdi")
        .replace("{n}", prosent(p.sikkerhet_bp))
        .replace("{terskel}", prosent(p.terskel_bp)) }),
      status,
      el("td", { text: p.registrert_av })));
  }
  tabell.append(kropp);
  return tabell;
}


export function beslutningstabell(beslutninger) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.mote.beslutning", "ui.mote.besluttet_av",
                   "ui.mote.besluttet_ts", "ui.mote.hviler_paa"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const b of beslutninger) {
    const hviler = el("td", {});
    if (b.punkt_ubekreftet === true) {
      // USIKKERHETEN BÆRES VIDERE.
      hviler.append(el("strong", { role: "alert",
        text: t("ui.mote.hviler_paa_ubekreftet") }));
    } else if (b.punkt_id) {
      hviler.append(el("span", { text: t("ui.mote.hviler_paa_punkt") }));
    } else {
      hviler.append(el("span", { class: "muted",
        text: t("ui.mote.hviler_paa_ingenting") }));
    }
    kropp.append(el("tr", {},
      el("td", { text: b.tekst }),
      // ET NAVN, ALLTID. Modulen fatter ingen beslutning.
      el("td", { text: b.besluttet_av }),
      el("td", { text: b.besluttet_ts }),
      hviler));
  }
  tabell.append(kropp);
  return tabell;
}


export function motetabell(moter, aapne) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.mote.motetittel", "ui.mote.start",
                   "ui.mote.innkalt_av", "ui.mote.deltakere",
                   "ui.mote.punkter", "ui.mote.beslutninger",
                   "ui.mote.apne_aksjoner", "ui.mote.opptak",
                   "ui.mote.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const m of moter) {
    const punkter = el("td", {});
    if (m.antall_punkter === 0) {
      punkter.append(el("strong", { text: "0" }));
    } else if (m.antall_ubekreftede > 0) {
      punkter.append(el("span", {
        text: t("ui.mote.punkter_med_ubekreftede")
          .replace("{n}", String(m.antall_punkter))
          .replace("{ubekreftede}", String(m.antall_ubekreftede)) }));
    } else {
      punkter.append(el("span", { text: String(m.antall_punkter) }));
    }
    const opptak = el("td", {});
    if (m.har_opptak) {
      // DEN SOM SER AT ET MØTE BLE TATT OPP, SKAL SE HVORFOR DET VAR
      // LOV — uten et klikk til.
      const gn = GRUNNLAGSTEKST[m.opptakshjemmel];
      opptak.append(el("span", {
        text: gn ? t(gn) : String(m.opptakshjemmel ?? "") }));
    } else {
      opptak.append(el("span", { class: "muted",
        text: t("ui.mote.intet_opptak") }));
    }
    const handling = el("td", {});
    handling.append(knappMed(t("ui.mote.vis_referat"),
                             () => aapne(m)));
    kropp.append(el("tr", {},
      el("td", { text: m.tittel }),
      el("td", { text: m.start_ts }),
      el("td", { text: m.innkalt_av }),
      el("td", { text: String(m.antall_deltakere) }),
      punkter,
      el("td", { text: String(m.antall_beslutninger) }),
      el("td", { text: String(m.antall_apne_aksjoner) }),
      opptak, handling));
  }
  tabell.append(kropp);
  return tabell;
}


export function hjemmeltabell(hjemler, avslutt) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.mote.grunnlagstype", "ui.mote.beskrivelse",
                   "ui.mote.formal", "ui.mote.gyldig_fra",
                   "ui.mote.gyldig_til", "ui.mote.gjelder",
                   "ui.mote.antall_opptak", "ui.mote.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const h of hjemler) {
    const gjelder = el("td", {});
    if (h.gjelder) {
      gjelder.append(el("span", { text: t("ui.mote.gjelder_ja") }));
    } else {
      // EN UTLØPT HJEMMEL SER NØYAKTIG UT SOM EN GYLDIG — klynge 7s
      // dom, og den gjelder her. Derfor merkes den.
      gjelder.append(el("strong", { role: "alert",
        text: t("ui.mote.utlopt") }));
    }
    const handling = el("td", {});
    if (avslutt && h.gjelder) {
      handling.append(knappMed(t("ui.mote.avslutt"), () => avslutt(h)));
    }
    const gn = GRUNNLAGSTEKST[h.grunnlagstype];
    kropp.append(el("tr", {},
      el("td", { text: gn ? t(gn) : String(h.grunnlagstype) }),
      el("td", { text: h.beskrivelse }),
      el("td", { text: h.formal }),
      el("td", { text: h.gyldig_fra }),
      el("td", { text: h.gyldig_til || "–" }),
      gjelder,
      el("td", { text: String(h.antall_opptak) }),
      handling));
  }
  tabell.append(kropp);
  return tabell;
}


export function aksjonstabell(aksjoner, lukk) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.mote.aksjon", "ui.mote.eier", "ui.mote.frist",
                   "ui.mote.status", "ui.mote.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const a of aksjoner) {
    const status = el("td", {});
    if (a.status === "apen" && a.dogn_over_frist > 0) {
      status.append(el("strong", { role: "alert",
        text: t("ui.mote.over_frist")
          .replace("{n}", String(a.dogn_over_frist)) }));
    } else if (a.status === "apen") {
      status.append(el("span", { text: t("ui.mote.aksjon_apen") }));
    } else {
      const ln = LUKKETEKST[a.status];
      status.append(el("span", {
        text: `${ln ? t(ln) : String(a.status)} (${a.lukket_av})` }));
    }
    const handling = el("td", {});
    if (lukk && a.status === "apen") {
      handling.append(knappMed(t("ui.mote.lukk_aksjon"),
                               () => lukk(a)));
    }
    kropp.append(el("tr", {},
      el("td", { text: a.tekst }),
      // EN AKSJON UTEN EIER ER EN AKSJON INGEN GJØR — kolonnen er
      // NOT NULL i basen, så den er alltid fylt.
      el("td", { text: a.eier }),
      el("td", { text: a.frist }),
      status, handling));
  }
  tabell.append(kropp);
  return tabell;
}


export function funntabell(funn, lukk) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.mote.funntype", "ui.mote.referanse",
                   "ui.mote.detaljer", "ui.mote.over_grense",
                   "ui.mote.status", "ui.mote.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const f of funn) {
    const handling = el("td", {});
    // `kan_lukkes` KOMMER FRA BASEN (124s form), og betingelsen ser på
    // DEN — ikke på skrivescopet. En LESER skal ikke få vite at et
    // funn «lukkes av sveipen» når et menneske faktisk kan lukke det
    // (132s CodeRabbit-funn).
    if (lukk && f.kan_lukkes) {
      handling.append(knappMed(t("ui.mote.lukk"), () => lukk(f)));
    } else if (f.apen && !f.kan_lukkes) {
      handling.append(el("span", { class: "muted",
        text: t("ui.mote.lukkes_av_sveipen") }));
    }
    const nokkel = FUNNTEKST[f.funntype];
    kropp.append(el("tr", {},
      el("td", { text: nokkel ? t(nokkel) : String(f.funntype) }),
      el("td", { text: f.referanse }),
      el("td", { text: f.detaljer }),
      el("td", { text: String(f.over_grense ?? 0) }),
      el("td", { text: f.apen
        ? t("ui.mote.apen")
        : t("ui.mote.lukket_av").replace("{av}", f.lukket_av || "–") }),
      handling));
  }
  tabell.append(kropp);
  return tabell;
}


export function referatpanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  let aktiv = null;

  async function aapne(m) {
    aktiv = m;
    // MÅLET FANGES FØR VENTINGEN (128s CodeRabbit-funn). To raske
    // klikk gir to hentinger, og den TREGESTE kunne svart sist og
    // tegnet feil møtes referat under overskriften til det nyeste —
    // et referat med feil overskrift er verre enn intet referat,
    // fordi det ser riktig ut og gjelder noe annet.
    const maal = m.mote_id;
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.mote.referatet") }),
      el("p", { class: "muted",
                text: t("ui.mote.referat_om")
                  .replace("{tittel}", m.tittel)
                  .replace("{dato}", m.start_ts) }));
    let d;
    try {
      d = await hentJson(`/v1/mote/${maal}/referat`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (aktiv.mote_id !== maal) return;
      node.append(el("p", { role: "alert",
                            text: t("ui.mote.feil") }));
      return;
    }
    if (aktiv.mote_id !== maal) return;
    const punkter = d.punkter || [];
    const beslutninger = d.beslutninger || [];
    if (!punkter.length) {
      node.append(el("p", { role: "alert",
                            text: t("ui.mote.referat_tomt") }));
    } else {
      node.append(referattabell(punkter));
    }
    if (beslutninger.length) {
      node.append(el("h3", { text: t("ui.mote.beslutninger") }),
                  beslutningstabell(beslutninger));
    }
  }

  return { node, aapne };
}


export function lukkepanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  let aktiv = null;

  function aapne(f) {
    aktiv = f;
    node.hidden = false;
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    const form = el("form", { class: "skjema" });
    const begrunnelse = el("textarea", { id: "mote-lukkegrunn",
                                         name: "begrunnelse",
                                         rows: "3", required: true });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.mote.lukk") });
    form.append(
      felt("mote-lukkegrunn", "ui.mote.begrunnelse", begrunnelse,
           "ui.mote.begrunnelse_hjelp"),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.mote.lukket_ok",
      send: (idem) => lukkMotefunn(
        aktiv.funn_id, { begrunnelse: begrunnelse.value }, idem),
    });
    sett(node, el("h2", { text: t("ui.mote.lukk_funn") }),
      el("p", { class: "muted",
                text: t("ui.mote.lukk_om")
                  .replace("{type}", f.funntype) }),
      form);
  }

  return { node, aapne };
}


export function aksjonslukkepanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  let aktiv = null;

  function aapne(a) {
    aktiv = a;
    node.hidden = false;
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    const form = el("form", { class: "skjema" });
    const status = velger("mote-aksjonsstatus", AKSJONSLUKKINGER,
                          LUKKETEKST);
    const begrunnelse = el("textarea", { id: "mote-aksjonsgrunn",
                                         name: "begrunnelse",
                                         rows: "2", required: true });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.mote.lukk_aksjon") });
    form.append(
      felt("mote-aksjonsstatus", "ui.mote.status", status),
      felt("mote-aksjonsgrunn", "ui.mote.begrunnelse", begrunnelse,
           "ui.mote.aksjonsgrunn_hjelp"),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.mote.aksjon_lukket_ok",
      send: (idem) => lukkMoteaksjon(aktiv.aksjon_id, {
        status: status.value, begrunnelse: begrunnelse.value,
      }, idem),
    });
    sett(node, el("h2", { text: t("ui.mote.lukk_aksjon") }),
      el("p", { class: "muted",
                text: t("ui.mote.aksjon_om")
                  .replace("{eier}", a.eier) }),
      form);
  }

  return { node, aapne };
}


// OPPTAKSPANELET — DER REKKEFØLGEN ER SYNLIG.
//
// Skjemaet ber om varslingstidspunktet FØR starttidspunktet, i den
// rekkefølgen, fordi det er den rekkefølgen regelen har. Og
// hjelpeteksten sier hva døra gjør: den nekter hvis varslingen kom
// etterpå.
export function opptakspanel(ctx, last, kvitter, hjemler) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  let aktiv = null;

  function aapne(m) {
    aktiv = m;
    node.hidden = false;
    const gyldige = (hjemler || []).filter((h) => h.gjelder);
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    if (!gyldige.length) {
      // UTEN EN GYLDIG HJEMMEL NEKTER DØRA, og panelet sier det i
      // stedet for å la brukeren finne det ut av en 400.
      sett(node, el("h2", { text: t("ui.mote.start_opptak") }),
        el("p", { role: "alert",
                  text: t("ui.mote.ingen_gyldig_hjemmel") }));
      return;
    }
    const form = el("form", { class: "skjema" });
    const valg = el("select", { id: "mote-hjemmelvalg",
                                name: "hjemmel_id" });
    for (const h of gyldige) {
      const gn = GRUNNLAGSTEKST[h.grunnlagstype];
      valg.append(el("option", { value: h.hjemmel_id,
        text: `${gn ? t(gn) : h.grunnlagstype} — ${h.formal}` }));
    }
    const varslet = el("input", { type: "datetime-local",
                                  id: "mote-varslet",
                                  name: "varslet_ts",
                                  required: true });
    const varslet_av = el("input", { type: "text",
                                     id: "mote-varsletav",
                                     name: "varslet_av",
                                     required: true });
    const varslede = el("input", { type: "text", id: "mote-varslede",
                                   name: "varslede", required: true });
    const startet = el("input", { type: "datetime-local",
                                  id: "mote-startet",
                                  name: "startet_ts",
                                  required: true });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.mote.start_opptak") });
    form.append(
      el("p", { class: "muted",
                text: t("ui.mote.opptak_forklaring") }),
      felt("mote-hjemmelvalg", "ui.mote.hjemmel", valg),
      // VARSLINGEN FØRST, fordi det er rekkefølgen regelen har.
      felt("mote-varslet", "ui.mote.varslet_ts", varslet,
           "ui.mote.varslet_hjelp"),
      felt("mote-varsletav", "ui.mote.varslet_av", varslet_av),
      felt("mote-varslede", "ui.mote.varslede", varslede,
           "ui.mote.varslede_hjelp"),
      felt("mote-startet", "ui.mote.startet_ts", startet),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.mote.opptak_registrert",
      send: (idem) => {
        // TOMME FELT SENDES IKKE. `new Date("")` er `Invalid Date`,
        // og en ugyldig dato sendt til en `timestamptz` ville gitt en
        // 500 i stedet for et nekt kalleren kan handle på.
        for (const f of [varslet, varslet_av, varslede, startet]) {
          if (f.value.trim() === "") {
            throw new FeilformetFeil(400, "felt_mangler");
          }
        }
        return startOpptak(aktiv.mote_id, {
          hjemmel_id: valg.value,
          varslet_ts: new Date(varslet.value).toISOString(),
          varslet_av: varslet_av.value,
          varslede: varslede.value.split(",").map((x) => x.trim())
            .filter((x) => x !== ""),
          startet_ts: new Date(startet.value).toISOString(),
        }, idem);
      },
    });
    sett(node, el("h2", { text: t("ui.mote.start_opptak") }),
      el("p", { class: "muted",
                text: t("ui.mote.opptak_om")
                  .replace("{tittel}", m.tittel) }),
      form);
  }

  return { node, aapne };
}


export function kravskjema(ctx, last, kvitter, s) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  // ALLE TRE GRENSENE FORHÅNDSUTFYLLES FRA BASEN (123s lærdom).
  const referat = el("input", { type: "number", id: "mote-referatfrist",
                                name: "referatfrist_dogn", min: "1",
                                max: "60", required: true,
                                value: String(s.referatfrist_dogn ?? 3) });
  const aksjon = el("input", { type: "number", id: "mote-aksjonsfrist",
                               name: "aksjonsfrist_dogn", min: "1",
                               max: "180", required: true,
                               value: String(s.aksjonsfrist_dogn ?? 7) });
  const terskel = el("input", { type: "number", id: "mote-terskel",
                                name: "sikkerhetsterskel_bp", min: "1",
                                max: "10000", required: true,
                                value: String(s.sikkerhetsterskel_bp
                                              ?? 7000) });
  const knapp = el("button", { type: "submit",
                               text: t("ui.mote.krav_lagre") });
  form.append(
    felt("mote-referatfrist", "ui.mote.referatfrist", referat,
         "ui.mote.referatfrist_hjelp"),
    felt("mote-aksjonsfrist", "ui.mote.aksjonsfrist", aksjon,
         "ui.mote.aksjonsfrist_hjelp"),
    felt("mote-terskel", "ui.mote.terskel", terskel,
         "ui.mote.terskel_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.mote.krav_lagret",
    send: (idem) => settMotekrav({
      referatfrist_dogn: Number(referat.value),
      aksjonsfrist_dogn: Number(aksjon.value),
      sikkerhetsterskel_bp: Number(terskel.value),
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.mote.krav") }), form);
}


export function hjemmelskjema(ctx, last, kvitter) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const grunnlagstype = velger("mote-grunnlagstype", GRUNNLAGSTYPER,
                               GRUNNLAGSTEKST);
  const beskrivelse = el("textarea", { id: "mote-hjemmelbeskrivelse",
                                       name: "beskrivelse", rows: "2",
                                       required: true });
  const formal = el("input", { type: "text", id: "mote-formal",
                               name: "formal", required: true });
  const fra = el("input", { type: "date", id: "mote-hjemmelfra",
                            name: "gyldig_fra", required: true });
  const til = el("input", { type: "date", id: "mote-hjemmeltil",
                            name: "gyldig_til" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.mote.hjemmel_ny") });
  form.append(
    // SAMTYKKE ER ETT AV FIRE, og hjelpeteksten sier hvorfor det ofte
    // er det svakeste.
    felt("mote-grunnlagstype", "ui.mote.grunnlagstype", grunnlagstype,
         "ui.mote.grunnlagstype_hjelp"),
    felt("mote-hjemmelbeskrivelse", "ui.mote.beskrivelse",
         beskrivelse, "ui.mote.beskrivelse_hjelp"),
    felt("mote-formal", "ui.mote.formal", formal,
         "ui.mote.formal_hjelp"),
    felt("mote-hjemmelfra", "ui.mote.gyldig_fra", fra),
    felt("mote-hjemmeltil", "ui.mote.gyldig_til", til,
         "ui.mote.gyldig_til_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.mote.hjemmel_lagret",
    send: (idem) => registrerOpptakshjemmel({
      grunnlagstype: grunnlagstype.value,
      beskrivelse: beskrivelse.value,
      formal: formal.value,
      gyldig_fra: fra.value,
      gyldig_til: til.value === "" ? null : til.value,
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.mote.hjemmel_ny") }), form);
}


export function moteskjema(ctx, last, kvitter) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const tittel = el("input", { type: "text", id: "mote-tittel",
                               name: "tittel", required: true });
  const start = el("input", { type: "datetime-local", id: "mote-start",
                              name: "start_ts", required: true });
  const slutt = el("input", { type: "datetime-local", id: "mote-slutt",
                              name: "slutt_ts", required: true });
  const innkalt = el("input", { type: "text", id: "mote-innkalt",
                                name: "innkalt_av", required: true });
  const deltakere = el("input", { type: "text", id: "mote-deltakere",
                                  name: "deltakere", required: true });
  const agenda = el("textarea", { id: "mote-agenda", name: "agenda",
                                  rows: "2", required: true });
  const knapp = el("button", { type: "submit",
                               text: t("ui.mote.mote_ny") });
  form.append(
    felt("mote-tittel", "ui.mote.motetittel", tittel),
    felt("mote-start", "ui.mote.start", start),
    felt("mote-slutt", "ui.mote.slutt", slutt),
    felt("mote-innkalt", "ui.mote.innkalt_av", innkalt),
    felt("mote-deltakere", "ui.mote.deltakere", deltakere,
         "ui.mote.deltakere_hjelp"),
    felt("mote-agenda", "ui.mote.agenda", agenda),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.mote.mote_lagret",
    send: (idem) => {
      for (const f of [tittel, start, slutt, innkalt, deltakere]) {
        if (f.value.trim() === "") {
          throw new FeilformetFeil(400, "felt_mangler");
        }
      }
      return registrerMote({
        tittel: tittel.value,
        start_ts: new Date(start.value).toISOString(),
        slutt_ts: new Date(slutt.value).toISOString(),
        innkalt_av: innkalt.value,
        deltakere: deltakere.value.split(",").map((x) => x.trim())
          .filter((x) => x !== ""),
        agenda: agenda.value,
      }, idem);
    },
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.mote.mote_ny") }), form);
}


export function visMote(hoved, ctx) {
  const hode = () => flateHode(t("ui.mote.tittel"),
    t("ui.mote.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/mote"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const moter = d.moter || [];
      const hjemler = d.hjemler || [];
      const aksjoner = d.aksjoner || [];
      const funn = d.funn || [];
      const skriver = harScope(ctx, "bestilling:opprett");
      const referat = referatpanel(ctx, last, kvitter);
      const lukking = lukkepanel(ctx, last, kvitter);
      const aksjonslukking = aksjonslukkepanel(ctx, last, kvitter);
      const opptak = opptakspanel(ctx, last, kvitter, hjemler);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.mote.sammendrag") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT.
        el("p", { class: "muted", text: t("ui.mote.hvorfor") }));

      const funnseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.mote.funn") }));
      if (!funn.length) {
        funnseksjon.append(el("p", { class: "muted",
          text: t("ui.mote.funn_tomt") }));
      } else {
        funnseksjon.append(funntabell(
          funn, skriver ? lukking.aapne : null));
      }

      const moteseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.mote.moter") }));
      if (!moter.length) {
        moteseksjon.append(el("p", { class: "muted",
          text: t("ui.mote.moter_tomt") }));
      } else {
        moteseksjon.append(motetabell(moter, referat.aapne));
        if (skriver) {
          const velg = el("div", { class: "felt" });
          for (const m of moter.slice(0, 20)) {
            velg.append(knappMed(
              t("ui.mote.opptak_for").replace("{tittel}", m.tittel),
              () => opptak.aapne(m)));
          }
          moteseksjon.append(velg);
        }
      }

      const aksjonsseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.mote.aksjoner") }));
      if (!aksjoner.length) {
        aksjonsseksjon.append(el("p", { class: "muted",
          text: t("ui.mote.aksjoner_tomt") }));
      } else {
        aksjonsseksjon.append(aksjonstabell(
          aksjoner, skriver ? aksjonslukking.aapne : null));
      }

      const hjemmelseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.mote.hjemler") }));
      if (!hjemler.length) {
        hjemmelseksjon.append(el("p", { class: "muted",
          text: t("ui.mote.hjemler_tomt") }));
      } else {
        hjemmelseksjon.append(hjemmeltabell(hjemler, skriver
          ? (h) => avsluttHjemmel(ctx, last, kvitter, h) : null));
      }

      // FUNNENE FØRST, SÅ MØTENE. Det som haster er referatet ingen
      // skrev og aksjonen ingen gjorde — ikke listen over hvor mange
      // møter vi har hatt.
      const deler = [oversikt, funnseksjon, lukking.node,
                     moteseksjon, referat.node, opptak.node,
                     aksjonsseksjon, aksjonslukking.node,
                     hjemmelseksjon];
      if (skriver) {
        deler.push(moteskjema(ctx, last, kvitter),
                   hjemmelskjema(ctx, last, kvitter),
                   kravskjema(ctx, last, kvitter, s));
      }
      sett(kropp, ...deler);
    });

  async function avsluttHjemmel(c, l, k, h) {
    // AVSLUTNING ER ENVEIS, og datoen er i dag. En hjemmel som kunne
    // avsluttes fram i tid ville gjort «gjelder den nå?» til et
    // spørsmål med to svar.
    try {
      await avsluttOpptakshjemmel(h.hjemmel_id,
        { gyldig_til: iDagLokal() },
        nyIdempotensnokkel());
    } catch (e) {
      if (e instanceof UautorisertFeil) { c.paaUautorisert(); return; }
      k(t("ui.mote.feil.generell"));
      return;
    }
    meldLive(t("ui.mote.hjemmel_avsluttet"));
    k(t("ui.mote.hjemmel_avsluttet"));
    await l();
  }

  return last();
}
