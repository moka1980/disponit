// M-20 nettside- og innholdsagent (134) — KILDEN ER PRODUKTET.
//
// FLATENS VIKTIGSTE JOBB ER Å VISE HVA EN PÅSTAND HVILER PÅ.
//
// Klyngens delte dom: en ytring avgitt i husets navn kan ikke tas
// tilbake — og DEN SOM LESER DEN VET IKKE AT EN MASKIN SKREV DEN. En
// produktpåstand ser like troverdig ut enten den hviler på en
// testrapport eller på ingenting. Derfor står KILDEN i samme rad som
// påstanden, med sin type og sin gyldighet, og en utløpt kilde er et
// VARSEL — ikke en fotnote.
//
// EN ROLLBACK FJERNER SIDEN. DEN FJERNER IKKE AT NOEN LESTE DEN.
// Derfor viser publiseringslisten hver PERIODE en versjon var levende,
// med begge navnene og begge tidspunktene. «Hvor lenge sto det ute» er
// et spørsmål noen stiller etterpå.
//
// VEIEN TILBAKE VISES FØR VEIEN FRAM TAS. Publiseringsknappen sier
// hva en rollback vil gjøre — `forrige_versjon` med et nummer, eller
// `avpublisering` — fordi en rollback som skulle vært funnet ut av
// etterpå er ingen rollback, det er et håp.
//
// DET FINNES INGEN «PUBLISER AUTOMATISK»-KNAPP, OG DET KAN IKKE
// FINNES. Skjemaet krever `publisert_av`: modulen gjør et utkast
// klart, den publiserer det ikke.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  FeilformetFeil, hentJson, lukkInnholdsfunn, merkInnholdsutkastKlart,
  nyIdempotensnokkel, publiserUtkast, registrerInnholdskilde,
  registrerPaastand, registrerUtkast, registrerVisning,
  rullTilbakePublisering, settInnholdskrav, UautorisertFeil,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

export const DOKUMENTTYPER = ["testrapport", "maaling", "datablad",
                              "leverandorerklaering", "sertifikat",
                              "attest", "regnskap", "referanse",
                              "policy", "cv", "annet"];

const TYPETEKST = {
  testrapport: "ui.innhold.type_testrapport",
  maaling: "ui.innhold.type_maaling",
  datablad: "ui.innhold.type_datablad",
  leverandorerklaering: "ui.innhold.type_leverandorerklaering",
  sertifikat: "ui.innhold.type_sertifikat",
  attest: "ui.innhold.type_attest",
  regnskap: "ui.innhold.type_regnskap",
  referanse: "ui.innhold.type_referanse",
  policy: "ui.innhold.type_policy",
  cv: "ui.innhold.type_cv",
  annet: "ui.innhold.type_annet",
};

const STATUSTEKST = {
  utkast: "ui.innhold.status_utkast",
  klar: "ui.innhold.status_klar",
  publisert: "ui.innhold.status_publisert",
  forkastet: "ui.innhold.status_forkastet",
};

const ROLLBACKTEKST = {
  forrige_versjon: "ui.innhold.rollback_forrige",
  avpublisering: "ui.innhold.rollback_avpublisering",
};

const UTFALLTEKST = {
  avpublisert: "ui.innhold.utfall_avpublisert",
  forrige_gjenopprettet: "ui.innhold.utfall_gjenopprettet",
  forrige_ikke_gjenopprettet: "ui.innhold.utfall_ikke_gjenopprettet",
};

const FUNNTEKST = {
  publisert_paastand_uten_gyldig_kilde: "ui.innhold.funn_utlopt_kilde",
  klart_utkast_uten_forhaandsvisning: "ui.innhold.funn_usett",
  kilde_utloper_snart_uavklart: "ui.innhold.funn_snart",
  paastand_uten_kilde: "ui.innhold.funn_uten_kilde",
  publisering_uten_forhaandsvisning: "ui.innhold.funn_uten_visning",
  publisering_uten_rollbackvei: "ui.innhold.funn_uten_vei",
};


export function dato(iso) {
  if (typeof iso !== "string" || !iso) return "–";
  return iso.slice(0, 10);
}


// DØGN TIL UTLØP, MED FORTEGNET SYNLIG.
//
// Et negativt tall er ikke «lenge siden» — det er UTLØPT, og de to
// skal ikke se like ut.
export function dognTekst(n) {
  if (typeof n !== "number") return "–";
  if (n < 0) return t("ui.innhold.utlopt_for").replace("{n}", String(-n));
  return t("ui.innhold.utloper_om").replace("{n}", String(n));
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


// KNAPPEN OG LYTTEREN I ETT (127s lærdom): `el()` setter ukjente
// nøkler med `setAttribute`, så `onclick: fn` ville blitt STRENGEN
// «() => …» — en knapp som ser ut som den virker og ikke gjør det.
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
          ? t("ui.innhold.feil.tilstand")
          : t("ui.innhold.feil.generell") }));
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
// En påstand som står ute NÅ og hviler på et dokument som ikke lenger
// gjelder, er det ene tallet som betyr at noe kan ha gått galt
// allerede. Alt annet er tilstand; dette er en skade.
export function sammendrag(s) {
  const p = el("p", {});
  if (s.paastander_paa_utlopt_kilde > 0) {
    p.append(el("strong", { role: "alert",
      text: t("ui.innhold.paastand_paa_utlopt")
        .replace("{n}", String(s.paastander_paa_utlopt_kilde)) }), " ");
  }
  p.append(el("span", { text: t("ui.innhold.sammendrag_tekst")
    .replace("{sider}", String(s.sider ?? 0))
    .replace("{levende}", String(s.levende_sider ?? 0))
    .replace("{paastander}", String(s.paastander ?? 0))
    .replace("{kilder}", String(s.kilder ?? 0)) }));
  if (s.utlopte_kilder > 0) {
    p.append(" ", el("span", { text: t("ui.innhold.utlopte_kilder")
      .replace("{n}", String(s.utlopte_kilder)) }));
  }
  if (!s.har_krav) {
    // UTEN GRENSER KAN INGENTING SKRIVES. Døra nekter, og flaten sier
    // det før noen prøver.
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.innhold.mangler_krav") }));
  }
  return p;
}


// PÅSTANDENE MED SINE KILDER.
//
// KILDEN STÅR I SAMME RAD SOM PÅSTANDEN — med tittel, type og
// gyldighet. Et oppslag til ville gjort det mulig å lese påstanden
// uten å se hva den hviler på, og det er nøyaktig tilstanden modulen
// finnes for å hindre.
export function paastandstabell(paastander) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.innhold.rekkefolge", "ui.innhold.paastand",
                   "ui.innhold.kilde", "ui.innhold.kildetype",
                   "ui.innhold.kildestatus", "ui.innhold.registrert_av"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const p of paastander) {
    const status = el("td", {});
    if (p.kilde_gyldig) {
      status.append(el("span", { text: t("ui.innhold.kilde_gyldig") }));
    } else {
      // EN UTLØPT KILDE ER ET VARSEL. Påstanden står fortsatt der.
      status.append(el("strong", { role: "alert",
        text: t("ui.innhold.kilde_utlopt") }));
    }
    const tn = TYPETEKST[p.dokumenttype];
    kropp.append(el("tr", {},
      el("td", { text: String(p.rekkefolge) }),
      el("td", { text: p.tekst }),
      el("td", { text: p.kilde_tittel }),
      el("td", { text: tn ? t(tn) : String(p.dokumenttype) }),
      status,
      el("td", { text: p.registrert_av })));
  }
  tabell.append(kropp);
  return tabell;
}


export function sidetabell(sider, aapne) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.innhold.side", "ui.innhold.siste_versjon",
                   "ui.innhold.status", "ui.innhold.levende",
                   "ui.innhold.paastander", "ui.innhold.visninger",
                   "ui.innhold.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const s of sider) {
    const levende = el("td", {});
    if (s.levende_versjon == null) {
      levende.append(el("span", { text: t("ui.innhold.ikke_publisert") }));
    } else {
      levende.append(el("span", { text: t("ui.innhold.levende_versjon")
        .replace("{n}", String(s.levende_versjon))
        .replace("{av}", s.levende_publisert_av || "") }));
    }
    const paastander = el("td", {});
    paastander.append(el("span", { text: String(s.antall_paastander) }));
    if (s.antall_utlopte_kilder > 0) {
      // DEN SOM SER EN SIDE, SKAL SE OM DEN HVILER PÅ NOE UTLØPT —
      // uten et klikk til.
      paastander.append(" ", el("strong", { role: "alert",
        text: t("ui.innhold.av_dem_utlopt")
          .replace("{n}", String(s.antall_utlopte_kilder)) }));
    }
    const sn = STATUSTEKST[s.siste_status];
    kropp.append(el("tr", {},
      el("td", { text: s.side_id }),
      el("td", { text: String(s.siste_versjon) }),
      el("td", { text: sn ? t(sn) : String(s.siste_status) }),
      levende,
      paastander,
      el("td", { text: String(s.antall_visninger) }),
      el("td", {}, aapne
        ? knappMed(t("ui.innhold.vis_utkast"), () => aapne(s))
        : el("span", { text: "–" }))));
  }
  tabell.append(kropp);
  return tabell;
}


// HVER PERIODE EN VERSJON VAR LEVENDE, MED BEGGE NAVNENE.
//
// «Hvor lenge sto det ute, og hvem tok det ned» er spørsmål noen
// stiller etterpå, og de har bare svar fordi hver periode er sin egen
// rad.
export function publiseringstabell(publiseringer) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.innhold.side", "ui.innhold.versjon",
                   "ui.innhold.publisert", "ui.innhold.publisert_av",
                   "ui.innhold.sett_av", "ui.innhold.veien_tilbake",
                   "ui.innhold.tilbake"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const p of publiseringer) {
    const tilbake = el("td", {});
    if (p.levende) {
      tilbake.append(el("strong", { text: t("ui.innhold.staar_ute") }));
    } else {
      tilbake.append(el("span", { text: t("ui.innhold.tatt_ned_av")
        .replace("{dato}", dato(p.tilbake_ts))
        .replace("{av}", p.tilbake_av || "") }));
    }
    const rn = ROLLBACKTEKST[p.rollbackform];
    const veitekst = p.rollback_til_versjon != null
      ? t(rn).replace("{n}", String(p.rollback_til_versjon))
      : t(rn);
    kropp.append(el("tr", {},
      el("td", { text: p.side_id }),
      el("td", { text: String(p.versjon) }),
      el("td", { text: dato(p.publisert_ts) }),
      el("td", { text: p.publisert_av }),
      // HVEM SOM SÅ DET, OG NÅR. Uten dette er «godkjent» en påstand.
      el("td", { text: t("ui.innhold.sett_verdi")
        .replace("{av}", p.vist_for).replace("{dato}", dato(p.vist_ts)) }),
      el("td", { text: veitekst }),
      tilbake));
  }
  tabell.append(kropp);
  return tabell;
}


export function kildetabell(kilder) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.innhold.kilde", "ui.innhold.kildetype",
                   "ui.innhold.gyldighet", "ui.innhold.brukt_av",
                   "ui.innhold.registrert_av"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const k of kilder) {
    const gyldighet = el("td", {});
    if (!k.gyldig) {
      gyldighet.append(el("strong", { role: "alert",
        text: t("ui.innhold.kilde_utlopt") }));
    } else {
      gyldighet.append(el("span", { text: dognTekst(k.dogn_igjen) }));
    }
    const tn = TYPETEKST[k.dokumenttype];
    kropp.append(el("tr", {},
      el("td", { text: k.tittel }),
      el("td", { text: tn ? t(tn) : String(k.dokumenttype) }),
      gyldighet,
      el("td", { text: String(k.antall_paastander) }),
      el("td", { text: k.registrert_av })));
  }
  tabell.append(kropp);
  return tabell;
}


// FUNNTABELLEN. `kan_lukkes` LESES FRA BASEN.
//
// 132s CodeRabbit-funn: betingelsen står på radens egen `kan_lukkes`,
// ikke på om brukeren har skrivescope. En leser skal ikke få vite at
// et funn «lukkes av sveipen» når et menneske faktisk kan lukke det.
export function funntabell(funn, lukk) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.innhold.funntype", "ui.innhold.detaljer",
                   "ui.innhold.forst_sett", "ui.innhold.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const f of funn) {
    const handling = el("td", {});
    if (!f.apen) {
      handling.append(el("span", { text: t("ui.innhold.lukket_av")
        .replace("{av}", f.lukket_av || "") }));
    } else if (!f.kan_lukkes) {
      handling.append(el("span", {
        text: t("ui.innhold.lukkes_av_sveipen") }));
    } else if (lukk) {
      handling.append(knappMed(t("ui.innhold.avklar"), () => lukk(f)));
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


// UTKASTPANELET — PÅSTANDENE MED SINE KILDER, HENTET NÅR NOEN SPØR.
//
// Én ekstra runde mot API-et, og den er verdt den: kildene er
// modulens tyngste data, og å laste dem for hver side i registeret
// ville gjort listen treg for å spare et klikk.
export function utkastpanel(ctx) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  let aktiv = null;

  async function aapne(s) {
    aktiv = s;
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.innhold.utkastet") }),
      el("p", { class: "muted", text: t("ui.innhold.laster") }));
    let d;
    try {
      d = await hentJson(`/v1/innhold/utkast/${s.siste_utkast_id}`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.innhold.utkastet") }),
        el("p", { role: "alert", text: t("ui.innhold.feil.generell") }));
      return;
    }
    const paastander = d.paastander || [];
    const deler = [
      el("h2", { text: t("ui.innhold.utkastet") }),
      el("p", { class: "muted", text: t("ui.innhold.utkast_om")
        .replace("{side}", s.side_id)
        .replace("{versjon}", String(s.siste_versjon)) }),
    ];
    if (!paastander.length) {
      // EN SIDE UTEN PÅSTANDER ER LOV. Ikke alt hus sier er en
      // påstand som må belegges — en kontaktside er ikke det.
      deler.push(el("p", { class: "muted",
                           text: t("ui.innhold.ingen_paastander") }));
    } else {
      deler.push(paastandstabell(paastander));
    }
    sett(node, ...deler);
  }

  return { node, aapne, get aktiv() { return aktiv; } };
}


// PUBLISERINGSPANELET — MODULENS TYNGSTE SKJEMA.
//
// `publisert_av` ER ET EGET FELT, og det er ikke skjemapynt: den
// innloggede brukeren kalte ruten, men `publisert_av` er den som
// SVARER FOR at siden står ute. På et lite hus er de den samme
// personen, og da skal begge stå — ikke én utledet av den andre.
//
// PANELET SIER HVA EN ROLLBACK VIL GJØRE, FØR DET PUBLISERES.
export function publiseringspanel(ctx, last, kvitter, sider) {
  const node = el("section", { class: "kpi-kort", hidden: true });

  function aapne(s, visninger) {
    node.hidden = false;
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    if (s.siste_status !== "klar") {
      // DØRA NEKTER, og panelet sier det i stedet for å la brukeren
      // finne det ut av en 400.
      sett(node, el("h2", { text: t("ui.innhold.publiser") }),
        el("p", { role: "alert", text: t("ui.innhold.ikke_klar") }));
      return;
    }
    if (!visninger || !visninger.length) {
      sett(node, el("h2", { text: t("ui.innhold.publiser") }),
        el("p", { role: "alert",
                  text: t("ui.innhold.ingen_visning") }));
      return;
    }
    const form = el("form", { class: "skjema" });
    const valg = el("select", { id: "innhold-visningsvalg",
                                name: "visning_id" });
    for (const v of visninger) {
      valg.append(el("option", { value: v.visning_id,
        text: t("ui.innhold.sett_verdi").replace("{av}", v.vist_for)
          .replace("{dato}", dato(v.vist_ts)) }));
    }
    const av = el("input", { type: "text", id: "innhold-publisertav",
                             name: "publisert_av", required: true });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.innhold.publiser") });
    // VEIEN TILBAKE, SAGT FØR VEIEN FRAM TAS.
    const vei = (sider || []).some(
      (x) => x.side_id === s.side_id && x.levende_versjon != null)
      ? t("ui.innhold.vei_forrige")
      : t("ui.innhold.vei_avpublisering");
    form.append(
      el("p", { class: "muted", text: t("ui.innhold.publiser_om")
        .replace("{side}", s.side_id)
        .replace("{versjon}", String(s.siste_versjon)) }),
      el("p", { text: vei }),
      felt("innhold-visningsvalg", "ui.innhold.forhaandsvisning", valg,
           "ui.innhold.visning_hjelp"),
      felt("innhold-publisertav", "ui.innhold.publisert_av", av,
           "ui.innhold.publisert_av_hjelp"),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.innhold.publisert_ok",
      send: (idem) => {
        if (av.value.trim() === "") {
          throw new FeilformetFeil(400, "felt_mangler");
        }
        return publiserUtkast(s.siste_utkast_id, {
          visning_id: valg.value,
          publisert_av: av.value.trim(),
        }, idem);
      },
    });
    sett(node, el("h2", { text: t("ui.innhold.publiser") }), form);
  }

  return { node, aapne };
}


export function tilbakepanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });

  function aapne(p) {
    node.hidden = false;
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    const form = el("form", { class: "skjema" });
    const av = el("input", { type: "text", id: "innhold-tilbakeav",
                             name: "tilbake_av", required: true });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.innhold.rull_tilbake") });
    const rn = ROLLBACKTEKST[p.rollbackform];
    form.append(
      el("p", { class: "muted", text: t("ui.innhold.tilbake_om")
        .replace("{side}", p.side_id)
        .replace("{versjon}", String(p.versjon)) }),
      // HVA DEN VIL GJØRE, LEST FRA RADEN — ikke gjettet i flaten.
      el("p", { text: p.rollback_til_versjon != null
        ? t(rn).replace("{n}", String(p.rollback_til_versjon))
        : t(rn) }),
      // …OG AT DEN KAN ENDE I ET TOMROM.
      el("p", { class: "muted", text: t("ui.innhold.tilbake_forbehold") }),
      felt("innhold-tilbakeav", "ui.innhold.tilbake_av", av),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.innhold.tilbake_ok",
      send: async (idem) => {
        if (av.value.trim() === "") {
          throw new FeilformetFeil(400, "felt_mangler");
        }
        const svar = await rullTilbakePublisering(
          p.publisering_id, { tilbake_av: av.value.trim() }, idem);
        // UTFALLET SIES HØYT. «Forrige kunne ikke gjenopprettes» er
        // det viktigste av de tre, og en stille suksess ville latt
        // noen tro at den gamle siden står ute igjen.
        const un = UTFALLTEKST[svar && svar.utfall];
        if (un) meldLive(t(un));
        return svar;
      },
    });
    sett(node, el("h2", { text: t("ui.innhold.rull_tilbake") }), form);
  }

  return { node, aapne };
}


export function lukkepanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });

  function aapne(f) {
    node.hidden = false;
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    const form = el("form", { class: "skjema" });
    const grunn = el("input", { type: "text", id: "innhold-grunn",
                                name: "grunn", required: true });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.innhold.avklar") });
    form.append(
      el("p", { class: "muted", text: t("ui.innhold.avklar_om") }),
      felt("innhold-grunn", "ui.innhold.grunn", grunn,
           "ui.innhold.grunn_hjelp"),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.innhold.avklart_ok",
      send: (idem) => lukkInnholdsfunn(f.funn_id,
        { grunn: grunn.value }, idem),
    });
    sett(node, el("h2", { text: t("ui.innhold.avklar") }), form);
  }

  return { node, aapne };
}


export function kravskjema(ctx, last, kvitter, s) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  // ALLE TRE GRENSENE FORHÅNDSUTFYLLES (123s lærdom): et skjema som
  // viser mindre enn det lagrer er en felle — den som endrer ett tall
  // ville stilt de to andre tilbake uten å vite det.
  const kilde = el("input", { type: "number", id: "innhold-kildedogn",
                              name: "kilde_gyldig_dogn", min: "1",
                              max: "3650", required: true,
                              value: String(s.kilde_gyldig_dogn ?? 365) });
  const visning = el("input", { type: "number", id: "innhold-visningmin",
                                name: "visning_gyldig_min", min: "1",
                                max: "20160", required: true,
                                value: String(s.visning_gyldig_min ?? 60) });
  const varsel = el("input", { type: "number", id: "innhold-varselfrist",
                               name: "varselfrist_dogn", min: "0",
                               max: "365", required: true,
                               value: String(s.varselfrist_dogn ?? 30) });
  const knapp = el("button", { type: "submit",
                               text: t("ui.innhold.lagre_krav") });
  form.append(
    el("p", { class: "muted", text: t("ui.innhold.krav_om") }),
    felt("innhold-kildedogn", "ui.innhold.kilde_gyldig_dogn", kilde,
         "ui.innhold.kilde_gyldig_hjelp"),
    felt("innhold-visningmin", "ui.innhold.visning_gyldig_min", visning,
         "ui.innhold.visning_gyldig_hjelp"),
    felt("innhold-varselfrist", "ui.innhold.varselfrist_dogn", varsel),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.innhold.krav_lagret",
    send: (idem) => settInnholdskrav({
      kilde_gyldig_dogn: Number(kilde.value),
      visning_gyldig_min: Number(visning.value),
      varselfrist_dogn: Number(varsel.value),
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.innhold.krav") }), form);
}


export function kildeskjema(ctx, last, kvitter) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const tittel = el("input", { type: "text", id: "innhold-kildetittel",
                               name: "tittel", required: true });
  const type = velger("innhold-kildetype", DOKUMENTTYPER, TYPETEKST);
  const gyldig = el("input", { type: "date", id: "innhold-kildegyldig",
                               name: "gyldig_til" });
  const sum = el("input", { type: "text", id: "innhold-kildesum",
                            name: "innhold_sha256", required: true,
                            pattern: "[0-9a-f]{64}" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.innhold.lagre_kilde") });
  form.append(
    // HUSETS REGISTER, IKKE MODULENS — sagt rett ut i skjemaet.
    el("p", { class: "muted", text: t("ui.innhold.kilde_om") }),
    felt("innhold-kildetittel", "ui.innhold.kildetittel", tittel),
    felt("innhold-kildetype", "ui.innhold.kildetype", type),
    felt("innhold-kildegyldig", "ui.innhold.gyldig_til", gyldig,
         "ui.innhold.gyldig_til_hjelp"),
    felt("innhold-kildesum", "ui.innhold.innhold_sha256", sum,
         "ui.innhold.sum_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.innhold.kilde_lagret",
    tilbakestill: () => { tittel.value = ""; sum.value = ""; },
    send: (idem) => registrerInnholdskilde({
      tittel: tittel.value,
      dokumenttype: type.value,
      gyldig_til: gyldig.value || null,
      innhold_sha256: sum.value.trim(),
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.innhold.ny_kilde") }), form);
}


export function utkastskjema(ctx, last, kvitter) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const side = el("input", { type: "text", id: "innhold-sideid",
                             name: "side_id", required: true,
                             pattern: "[a-z0-9][a-z0-9_/-]*" });
  const innhold = el("textarea", { id: "innhold-innhold",
                                   name: "innhold", rows: "6",
                                   required: true });
  const basert = el("input", { type: "number", id: "innhold-basert",
                               name: "basert_pa_versjon", min: "1" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.innhold.lagre_utkast") });
  form.append(
    // HVER VERSJON ER EN NY RAD, sagt i skjemaet: det finnes ingen
    // «rediger»-knapp, og fraværet er porten `utkast_overskrevet`.
    el("p", { class: "muted", text: t("ui.innhold.utkast_om_nytt") }),
    felt("innhold-sideid", "ui.innhold.side", side,
         "ui.innhold.side_hjelp"),
    felt("innhold-innhold", "ui.innhold.innhold", innhold,
         "ui.innhold.innhold_hjelp"),
    felt("innhold-basert", "ui.innhold.basert_pa", basert,
         "ui.innhold.basert_pa_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.innhold.utkast_lagret",
    tilbakestill: () => { innhold.value = ""; },
    send: (idem) => {
      let json;
      try {
        json = JSON.parse(innhold.value);
      } catch {
        // EN UGYLDIG JSON SENDES IKKE. En 500 fra `jsonb` er en
        // feilmelding ingen kan handle på.
        throw new FeilformetFeil(400, "innhold_feilformet");
      }
      if (json === null || typeof json !== "object"
          || Array.isArray(json)) {
        throw new FeilformetFeil(400, "innhold_feilformet");
      }
      return registrerUtkast({
        side_id: side.value.trim(),
        innhold: json,
        basert_pa_versjon: basert.value ? Number(basert.value) : null,
      }, idem);
    },
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.innhold.nytt_utkast") }), form);
}


export function paastandsskjema(ctx, last, kvitter, sider, kilder) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const apne = (sider || []).filter((s) => s.siste_status === "utkast");
  const gyldige = (kilder || []).filter((k) => k.gyldig);
  if (!apne.length || !gyldige.length) {
    // EN PÅSTAND KREVER BEGGE DELER, og skjemaet sier hvilken som
    // mangler i stedet for å la brukeren finne det ut av en 400.
    return el("section", { class: "kpi-kort" },
      el("h2", { text: t("ui.innhold.ny_paastand") }),
      el("p", { role: "alert", text: !apne.length
        ? t("ui.innhold.ingen_apne_utkast")
        : t("ui.innhold.ingen_gyldig_kilde") }));
  }
  const utkast = el("select", { id: "innhold-paastandutkast",
                                name: "utkast_id" });
  for (const s of apne) {
    utkast.append(el("option", { value: s.siste_utkast_id,
      text: `${s.side_id} v${s.siste_versjon}` }));
  }
  const kilde = el("select", { id: "innhold-paastandkilde",
                               name: "kilde_id" });
  for (const k of gyldige) {
    const tn = TYPETEKST[k.dokumenttype];
    kilde.append(el("option", { value: k.kilde_id,
      text: `${k.tittel} — ${tn ? t(tn) : k.dokumenttype}` }));
  }
  const rekkefolge = el("input", { type: "number", min: "1", max: "500",
                                   id: "innhold-rekkefolge",
                                   name: "rekkefolge", required: true,
                                   value: "1" });
  const tekst = el("input", { type: "text", id: "innhold-paastandtekst",
                              name: "tekst", required: true });
  const knapp = el("button", { type: "submit",
                               text: t("ui.innhold.lagre_paastand") });
  form.append(
    // KILDEN ER IKKE VALGFRI, OG SKJEMAET KAN IKKE GJØRE DEN DET.
    el("p", { class: "muted", text: t("ui.innhold.paastand_om") }),
    felt("innhold-paastandutkast", "ui.innhold.utkast", utkast),
    felt("innhold-rekkefolge", "ui.innhold.rekkefolge", rekkefolge),
    felt("innhold-paastandtekst", "ui.innhold.paastand", tekst),
    felt("innhold-paastandkilde", "ui.innhold.kilde", kilde,
         "ui.innhold.kilde_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.innhold.paastand_lagret",
    tilbakestill: () => { tekst.value = ""; },
    send: (idem) => registrerPaastand(utkast.value, {
      rekkefolge: Number(rekkefolge.value),
      tekst: tekst.value,
      kilde_id: kilde.value,
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.innhold.ny_paastand") }), form);
}


export function visningsskjema(ctx, last, kvitter, sider) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const apne = (sider || []).filter(
    (s) => s.siste_status === "utkast" || s.siste_status === "klar");
  if (!apne.length) {
    return el("section", { class: "kpi-kort" },
      el("h2", { text: t("ui.innhold.ny_visning") }),
      el("p", { role: "alert",
                text: t("ui.innhold.ingen_apne_utkast") }));
  }
  const form = el("form", { class: "skjema" });
  const utkast = el("select", { id: "innhold-visningutkast",
                                name: "utkast_id" });
  for (const s of apne) {
    utkast.append(el("option", { value: s.siste_utkast_id,
      text: `${s.side_id} v${s.siste_versjon}` }));
  }
  const forHvem = el("input", { type: "text", id: "innhold-vistfor",
                                name: "vist_for", required: true });
  const knapp = el("button", { type: "submit",
                               text: t("ui.innhold.lagre_visning") });
  form.append(
    // HVA SOM BLE VIST, IKKE AT DET BLE VIST: summen kopieres fra
    // utkastet av døra, og kan ikke oppgis her.
    el("p", { class: "muted", text: t("ui.innhold.visning_om") }),
    felt("innhold-visningutkast", "ui.innhold.utkast", utkast),
    felt("innhold-vistfor", "ui.innhold.vist_for", forHvem,
         "ui.innhold.vist_for_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.innhold.visning_lagret",
    tilbakestill: () => { forHvem.value = ""; },
    send: (idem) => registrerVisning(utkast.value,
      { vist_for: forHvem.value }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.innhold.ny_visning") }), form);
}


export function visInnhold(hoved, ctx) {
  const hode = () => flateHode(t("ui.innhold.tittel"),
    t("ui.innhold.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/innhold"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const sider = d.sider || [];
      const kilder = d.kilder || [];
      const publiseringer = d.publiseringer || [];
      const funn = d.funn || [];
      const skriver = harScope(ctx, "bestilling:opprett");
      const utkast = utkastpanel(ctx);
      const lukking = lukkepanel(ctx, last, kvitter);
      const publisering = publiseringspanel(ctx, last, kvitter, sider);
      const tilbake = tilbakepanel(ctx, last, kvitter);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.innhold.sammendrag") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT.
        el("p", { class: "muted", text: t("ui.innhold.hvorfor") }));

      const funnseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.innhold.funn") }));
      if (!funn.length) {
        funnseksjon.append(el("p", { class: "muted",
          text: t("ui.innhold.funn_tomt") }));
      } else {
        funnseksjon.append(funntabell(
          funn, skriver ? lukking.aapne : null));
      }

      const sideseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.innhold.sider") }));
      if (!sider.length) {
        sideseksjon.append(el("p", { class: "muted",
          text: t("ui.innhold.sider_tomt") }));
      } else {
        sideseksjon.append(sidetabell(sider, utkast.aapne));
        if (skriver) {
          // «KLAR» ER EN TILSTAND HOS OSS — modulen sier at den er
          // ferdig, ikke at noen har godkjent. Døra nekter så lenge én
          // påstand hviler på en utløpt kilde.
          const klargjor = el("div", { class: "felt" });
          for (const x of sider.filter((y) => y.siste_status === "utkast")
                              .slice(0, 20)) {
            klargjor.append(knappMed(
              t("ui.innhold.merk_klar_for").replace("{side}", x.side_id),
              async () => {
                try {
                  await merkInnholdsutkastKlart(x.siste_utkast_id,
                                        nyIdempotensnokkel());
                } catch (e) {
                  if (e instanceof UautorisertFeil) {
                    ctx.paaUautorisert();
                    return;
                  }
                  kvitter(t("ui.innhold.feil.generell"));
                  return;
                }
                meldLive(t("ui.innhold.klar_ok"));
                kvitter(t("ui.innhold.klar_ok"));
                await last();
              }));
          }
          if (klargjor.childNodes.length) sideseksjon.append(klargjor);
          const velg = el("div", { class: "felt" });
          for (const x of sider.filter((y) => y.siste_status === "klar")
                              .slice(0, 20)) {
            velg.append(knappMed(
              t("ui.innhold.publiser_side").replace("{side}", x.side_id),
              async () => {
                // VISNINGENE HENTES FRA UTKASTET, ikke fra
                // publiseringslisten. En side som publiseres for
                // FØRSTE gang har ingen publiseringer i det hele
                // tatt, og en publiseringsrad bærer `vist_ts` men
                // ikke `visning_id`. Første utkast leste feil sted,
                // og panelet ville sagt «ingen har forhåndsvist
                // dette» om et utkast noen nettopp hadde sett på.
                let vis = [];
                try {
                  const u = await hentJson(
                    `/v1/innhold/utkast/${x.siste_utkast_id}`);
                  // BARE DE SOM GJELDER DETTE INNHOLDET. En eldre
                  // visning av et annet innhold ville blitt avvist av
                  // døra, og valget skal ikke tilby den.
                  vis = (u.visninger || [])
                    .filter((q) => q.gjelder_dette_innholdet);
                } catch (e) {
                  if (e instanceof UautorisertFeil) {
                    ctx.paaUautorisert();
                    return;
                  }
                }
                publisering.aapne(x, vis.length ? vis : null);
              }));
          }
          sideseksjon.append(velg);
        }
      }

      const publiseringsseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.innhold.publiseringer") }));
      if (!publiseringer.length) {
        publiseringsseksjon.append(el("p", { class: "muted",
          text: t("ui.innhold.publiseringer_tomt") }));
      } else {
        publiseringsseksjon.append(publiseringstabell(publiseringer));
        if (skriver) {
          const velg = el("div", { class: "felt" });
          for (const p of publiseringer.filter((q) => q.levende)
                                       .slice(0, 20)) {
            velg.append(knappMed(
              t("ui.innhold.tilbake_for").replace("{side}", p.side_id),
              () => tilbake.aapne(p)));
          }
          publiseringsseksjon.append(velg);
        }
      }

      const kildeseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.innhold.kilder") }));
      if (!kilder.length) {
        kildeseksjon.append(el("p", { class: "muted",
          text: t("ui.innhold.kilder_tomt") }));
      } else {
        kildeseksjon.append(kildetabell(kilder));
      }

      // FUNNENE FØRST, SÅ SIDENE. Det som haster er den udokumenterte
      // påstanden som står ute nå — ikke listen over hvor mange sider
      // vi har.
      const deler = [oversikt, funnseksjon, lukking.node,
                     sideseksjon, utkast.node, publisering.node,
                     publiseringsseksjon, tilbake.node, kildeseksjon];
      if (skriver) {
        deler.push(utkastskjema(ctx, last, kvitter),
                   paastandsskjema(ctx, last, kvitter, sider, kilder),
                   visningsskjema(ctx, last, kvitter, sider),
                   kildeskjema(ctx, last, kvitter),
                   kravskjema(ctx, last, kvitter, s));
      }
      sett(kropp, ...deler);
    });

  return last();
}
