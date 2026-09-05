// M-53 HMS- og avviksmottak (127) — ET FELT SOM KAN FYLLES BLIR FYLT.
//
// FLATENS VIKTIGSTE JOBB ER Å GJØRE ANONYMITET TIL ET VALG, IKKE TIL
// ET TOMT FELT.
//
// Meldeskjemaet har to former, og de er GJENSIDIG UTELUKKENDE I
// MARKUPEN: velger man «anonym», FJERNES navnefeltet fra DOM-en. Det
// blir ikke skjult, ikke deaktivert, ikke tømt — det finnes ikke. Et
// felt som kan fylles blir fylt: av en autofyll, av en test, av en
// integrasjon, av en velmenende kollega.
//
// OG FLATEN SIER FRA OM DET DATABASEN IKKE KAN LØSE. Fritekst kan
// identifisere melderen uansett hva skjemaet gjør — «jeg sa fra til
// formannen på tirsdag» peker på én person i en bedrift med tolv
// ansatte. Advarselen står FØR feltet, ikke etter, fordi den skal
// leses av den som skriver og ikke av den som har skrevet ferdig.
//
// `melder_navn: null` BETYR TO HELT ULIKE TING, og flaten sier hvilket:
//   • melderform «anonym»  → navnet ble ALDRI skrevet
//   • melderform «navngitt» + anonymisert → navnet ER slettet
// En flate som slo dem sammen ville fortalt en varsler at systemet
// «har slettet» noe det aldri hadde.
//
// «ANONYMISER» ER IKKE «SLETT». Knappen tømmer navnet og lar sporet
// stå: at vi HAR hatt avviket er nøyaktig det Arbeidstilsynet
// etterprøver. FØR oppbevaringsfristen krever knappen en
// M-30-sakshenvisning — se docs/M53-M30-GRENSESNITTET.md.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  anonymiserAvvik, hentJson, lukkHmsfunn, meldAvvik,
  nyIdempotensnokkel, registrerHmsregel, registrerTiltak,
  settHmskrav, UautorisertFeil,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

export const AVVIKSTYPER = ["naerulykke", "personskade", "sykdom",
                            "materiell", "psykososialt", "varsel"];
export const MELDERFORMER = ["navngitt", "anonym"];

const TYPETEKST = {
  naerulykke: "ui.hms.type_naerulykke",
  personskade: "ui.hms.type_personskade",
  sykdom: "ui.hms.type_sykdom",
  materiell: "ui.hms.type_materiell",
  psykososialt: "ui.hms.type_psykososialt",
  varsel: "ui.hms.type_varsel",
};

const FUNNTEKST = {
  ingen_krav: "ui.hms.funn_ingen_krav",
  regelverk_utlopt: "ui.hms.funn_regelverk_utlopt",
  regelverk_utloper_snart: "ui.hms.funn_regelverk_snart",
  avvik_mot_utlopt_regelverk: "ui.hms.funn_utlopt_regelverk",
  avvik_ubehandlet: "ui.hms.funn_ubehandlet",
  oppbevaring_naermer_seg: "ui.hms.funn_oppbevaring_snart",
  oppbevaring_utlopt: "ui.hms.funn_oppbevaring_utlopt",
  for_tidlig_anonymisert: "ui.hms.funn_for_tidlig",
};

// LOKAL DATO, IKKE `toISOString()`. Den siste konverterer til UTC, og
// et skjema åpnet 00:30 norsk tid ville foreslått gårsdagen (124s
// `ilokalDato`, samme grunn).
export function ilokalDato(d = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}


// MELDERENS TILSTAND. `null` navn er ikke ett svar — det er to, og
// forskjellen er hele varslervernet.
export function meldertilstand(a) {
  if (a.melderform === "anonym") return t("ui.hms.melder_anonym");
  if (a.anonymisert) return t("ui.hms.melder_anonymisert");
  return a.melder_navn || "";
}


export function oppbevaringstekst(a) {
  const n = a.dogn_til_oppbevaring;
  if (typeof n !== "number") return "";
  if (n < 0) {
    return t("ui.hms.oppbevaring_over").replace("{n}", String(-n));
  }
  return t("ui.hms.oppbevaring_igjen").replace("{n}", String(n));
}


// KNAPPEN OG LYTTEREN I ETT. `el()` setter ukjente nøkler med
// `setAttribute`, så `onclick: fn` ville blitt STRENGEN «() => …» i
// attributtet — en knapp som ser ut som den virker og ikke gjør det.
function knappMed(tekst, ved) {
  const b = el("button", { type: "button", text: tekst });
  b.addEventListener("click", ved);
  return b;
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
          ? t("ui.hms.feil.tilstand")
          : t("ui.hms.feil.generell") }));
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


// SAMMENDRAGET. TO TALL STÅR FØRST OG I FET SKRIFT, og rekkefølgen er
// en dom:
//
//   `ubehandlet_over_frist` — noen meldte fra, og ingen gjorde noe.
//   Det er hele grunnen til at modulen finnes, og en stille modul er
//   verre enn ingen modul: noen stolte på at den så etter.
//
//   `oppbevaring_passert` — vi holder en helseopplysning lenger enn
//   vår egen hjemmel rekker.
//
// Et sammendrag som begynte med «142 avvik registrert» ville fortalt
// hvor flittige folk har vært med å melde, ikke hva som er galt.
export function sammendrag(s) {
  const p = el("p", {});
  p.append(el("strong", {
    text: t("ui.hms.ubehandlet_sum")
      .replace("{n}", String(s.ubehandlet_over_frist ?? 0)) }));
  p.append(" ", el("strong", {
    text: t("ui.hms.passert_sum")
      .replace("{n}", String(s.oppbevaring_passert ?? 0)) }));
  if (s.oppbevaring_naer > 0) {
    p.append(" ", el("span", {
      text: t("ui.hms.naer_sum")
        .replace("{n}", String(s.oppbevaring_naer)) }));
  }
  p.append(" ", el("span", {
    text: t("ui.hms.tellinger")
      .replace("{avvik}", String(s.avvik ?? 0))
      .replace("{apne}", String(s.apne ?? 0))
      .replace("{anonyme}", String(s.anonyme ?? 0)) }));
  if (s.med_helseopplysninger > 0) {
    // SÆRLIGE KATEGORIER TELLES FOR SEG. Den som leser skal se hvor
    // mye av registeret som er art. 9-data uten å måtte åpne radene.
    p.append(" ", el("span", { class: "muted",
      text: t("ui.hms.helse_sum")
        .replace("{n}", String(s.med_helseopplysninger)) }));
  }
  if (!s.gyldige_regler) {
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.hms.ingen_gyldig_regel") }));
  }
  if (s.apne_funn > 0) {
    p.append(" ", el("strong", {
      text: t("ui.hms.apne_funn_sum")
        .replace("{n}", String(s.apne_funn)) }));
  }
  if (!s.har_krav) {
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.hms.krav_mangler") }));
  } else {
    p.append(" ", el("span", { class: "muted",
      text: t("ui.hms.taket_er")
        .replace("{n}", String(s.oppbevaring_maks_dogn))
        .replace("{f}", String(s.tiltaksfrist_dogn)) }));
  }
  return p;
}


// AVVIKSTABELLEN.
//
// MELDEREN STÅR I EN EGEN KOLONNE, og teksten kommer fra
// `meldertilstand` — ikke fra `melder_navn` direkte. Skrev tabellen
// `a.melder_navn || "—"`, ville en anonym melding og en anonymisert
// rad sett helt like ut, og det er nettopp forskjellen som betyr noe.
export function avvikstabell(avvik, { aapneTiltak, aapneAnonymiser }) {
  const tbody = el("tbody");
  for (const a of avvik) {
    const merker = el("span", {});
    if (a.helseopplysninger) {
      merker.append(el("span", { class: "merke",
        text: t("ui.hms.helse_merke") }));
    }
    if (a.anonymisert) {
      merker.append(el("span", { class: "merke",
        text: t("ui.hms.anonymisert_merke") }));
    }
    const handling = el("span", {});
    if (aapneTiltak) {
      handling.append(knappMed(t("ui.hms.tiltak"), () => aapneTiltak(a)));
    }
    // ANONYMISER-KNAPPEN FORSVINNER NÅR RADEN ALT ER ANONYMISERT.
    // Deaktivert ville sett ut som «du mangler tilgang»; borte sier
    // det som er sant: det er ingenting igjen å gjøre.
    if (aapneAnonymiser && !a.anonymisert) {
      handling.append(knappMed(t("ui.hms.anonymiser"), () => aapneAnonymiser(a)));
    }
    const oppbevaring = el("td", {});
    const over = (a.dogn_til_oppbevaring ?? 0) < 0 && !a.anonymisert;
    oppbevaring.append(el(over ? "strong" : "span", {
      text: a.oppbevaring_til }));
    oppbevaring.append(el("br", {}));
    oppbevaring.append(el(over ? "strong" : "span", {
      class: over ? null : "muted", text: oppbevaringstekst(a) }));
    tbody.append(el("tr", {},
      el("td", { text: t(TYPETEKST[a.avvikstype] || a.avvikstype) }),
      el("td", { text: a.beskrivelse }),
      el("td", { text: a.sted }),
      el("td", { text: a.meldt_dato }),
      el("td", { text: meldertilstand(a) }),
      el("td", { text: a.status === "apen"
        ? t("ui.hms.status_apen") : t("ui.hms.status_behandlet") }),
      el("td", {}, el("span", { text: a.oppbevaring_hjemmel }),
         el("br", {}),
         el("span", { class: "muted", text: a.regelversjon })),
      oppbevaring,
      el("td", {}, merker),
      el("td", {}, handling)));
  }
  return el("table", { class: "tabell" },
    el("thead", {}, el("tr", {},
      el("th", { text: t("ui.hms.avvikstype") }),
      el("th", { text: t("ui.hms.beskrivelse") }),
      el("th", { text: t("ui.hms.sted") }),
      el("th", { text: t("ui.hms.meldt_dato") }),
      el("th", { text: t("ui.hms.melder") }),
      el("th", { text: t("ui.hms.status_apen") }),
      el("th", { text: t("ui.hms.hjemmel") }),
      el("th", { text: t("ui.hms.oppbevares_til") }),
      el("th", { text: "" }),
      el("th", { text: "" }))),
    tbody);
}


export function regeltabell(regler, avvikle) {
  const tbody = el("tbody");
  for (const r of regler) {
    const status = el("td", {});
    if (r.gyldig_naa) {
      status.append(el("span", { text: t("ui.hms.gyldig_naa") }));
      if (typeof r.dogn_til_utlop === "number"
          && r.dogn_til_utlop <= 60) {
        status.append(el("br", {}), el("strong", {
          text: t("ui.hms.dogn_til_utlop")
            .replace("{n}", String(r.dogn_til_utlop)) }));
      }
    } else {
      status.append(el("span", { class: "muted",
        text: t("ui.hms.avviklet") }));
    }
    const handling = el("td", {});
    // EN AVVIKLET REGEL KAN IKKE AVVIKLES IGJEN, og en som alt har en
    // sluttdato skal ikke få en ny: identiteten er frosset, og bare
    // `gyldig_til` kan settes ÉN gang.
    if (avvikle && r.gyldig_naa && !r.gyldig_til) {
      handling.append(knappMed(t("ui.hms.avvikle"),
                               () => avvikle(r)));
    }
    tbody.append(el("tr", {},
      el("td", { text: t(TYPETEKST[r.avvikstype] || r.avvikstype) }),
      el("td", { text: r.versjon }),
      el("td", { text: r.hjemmel }),
      el("td", { text: String(r.oppbevaring_dogn) }),
      el("td", { text: r.helseopplysninger
        ? t("ui.hms.helse_merke") : "" }),
      el("td", { text: r.gyldig_fra }),
      el("td", { text: r.gyldig_til || "" }),
      status,
      el("td", { text: t("ui.hms.antall_avvik")
        .replace("{n}", String(r.antall_avvik ?? 0)) }),
      handling));
  }
  return el("table", { class: "tabell" },
    el("thead", {}, el("tr", {},
      el("th", { text: t("ui.hms.avvikstype") }),
      el("th", { text: t("ui.hms.versjon") }),
      el("th", { text: t("ui.hms.hjemmel") }),
      el("th", { text: t("ui.hms.oppbevaring_dogn") }),
      el("th", { text: t("ui.hms.helse_merke") }),
      el("th", { text: t("ui.hms.gyldig_fra") }),
      el("th", { text: t("ui.hms.gyldig_til") }),
      el("th", { text: t("ui.hms.gyldig_naa") }),
      el("th", { text: t("ui.hms.avvik") }),
      el("th", { text: "" }))),
    tbody);
}


// FUNNTABELLEN. `kan_lukkes` KOMMER FRA BASEN, ikke fra en liste her.
// Regelen bor ÉTT sted (`m53_funn_er_sveipens`), og en kopi på flaten
// ville råtnet den dagen en funntype ble lagt til.
export function funntabell(funn, lukk) {
  const tbody = el("tbody");
  for (const f of funn) {
    const handling = el("td", {});
    if (lukk && f.kan_lukkes) {
      handling.append(knappMed(t("ui.hms.funn_lukk"),
                               () => lukk(f)));
    } else if (f.kan_lukkes === false) {
      handling.append(el("span", { class: "muted",
        text: t("ui.hms.funn_kan_ikke_lukkes") }));
    }
    tbody.append(el("tr", {},
      el("td", {}, el("strong", {
        text: t(FUNNTEKST[f.funntype] || f.funntype) })),
      el("td", { text: f.detalj || "" }),
      el("td", { text: typeof f.over_grense === "number"
        ? String(f.over_grense) : "" }),
      el("td", { text: f.forst_sett }),
      handling));
  }
  return el("table", { class: "tabell" },
    el("thead", {}, el("tr", {},
      el("th", { text: t("ui.hms.funn") }),
      el("th", { text: t("ui.hms.beskrivelse") }),
      el("th", { text: "±" }),
      el("th", { text: t("ui.hms.funn_forst_sett") }),
      el("th", { text: "" }))),
    tbody);
}


// MELDESKJEMAET — MODULENS TYNGSTE KOMPONENT.
//
// NAVNEFELTET FINNES IKKE NÅR MELDEREN ER ANONYM. Ikke skjult med
// `hidden`, ikke deaktivert med `disabled`, ikke tømt ved innsending —
// FJERNET FRA DOM-EN. Tre grunner, i stigende styrke:
//
//   1. Et skjult felt sendes fortsatt med skjemaet.
//   2. Et deaktivert felt kan slås på av hva som helst som rører
//      DOM-en, og av en nettleserutvidelse vi ikke kontrollerer.
//   3. Et felt som FINNES blir fylt. Av autofyll, av en test, av en
//      integrasjon. Det er ikke en teoretisk risiko — det er det
//      hyppigste mønsteret i hele denne kodebasen.
//
// ADVARSELEN OM FRITEKST STÅR FØR FELTET. Den skal leses av den som
// skriver, ikke av den som har skrevet ferdig.
export function meldeskjema(ctx, last, kvitter) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });

  const formvelger = el("fieldset", {},
    el("legend", { text: t("ui.hms.melderform") }));
  const radioer = {};
  for (const f of MELDERFORMER) {
    const id = `hms-melderform-${f}`;
    const inp = el("input", { type: "radio", id, name: "melderform",
                              value: f });
    if (f === "navngitt") inp.checked = true;
    radioer[f] = inp;
    formvelger.append(el("div", { class: "felt-inline" }, inp,
      el("label", { for: id, text: t(`ui.hms.melderform_${f}`) })));
  }

  const typevalg = velger("hms-avvikstype", AVVIKSTYPER, TYPETEKST);
  const beskrivelse = el("textarea", { id: "hms-beskrivelse",
                                       name: "beskrivelse", rows: 4 });
  const sted = el("input", { type: "text", id: "hms-sted",
                             name: "sted" });
  const hendelsesdato = el("input", { type: "date",
                                      id: "hms-hendelsesdato",
                                      name: "hendelsesdato",
                                      value: ilokalDato() });

  // NAVNEFELTENE BYGGES, MEN SETTES BARE INN NÅR FORMEN ER NAVNGITT.
  const navn = el("input", { type: "text", id: "hms-melder-navn",
                             name: "melder_navn" });
  const rolle = el("input", { type: "text", id: "hms-melder-rolle",
                              name: "melder_rolle" });
  const navnfelt = el("div", {},
    felt("hms-melder-navn", "ui.hms.melder_navn", navn),
    felt("hms-melder-rolle", "ui.hms.melder_rolle", rolle));

  const anonymnote = el("p", { class: "muted",
    text: t("ui.hms.anonym_forklaring") });
  const plass = el("div", {});

  function tegnMelderdel() {
    const anonym = radioer.anonym.checked;
    if (anonym) {
      // FJERNET, IKKE SKJULT. Og feltene tømmes i tillegg, slik at et
      // bytte tilbake ikke gjenoppretter noe brukeren skrev mens han
      // trodde han var navngitt.
      navn.value = "";
      rolle.value = "";
      sett(plass, anonymnote);
    } else {
      sett(plass, navnfelt);
    }
  }
  for (const f of MELDERFORMER) {
    radioer[f].addEventListener("change", tegnMelderdel);
  }
  tegnMelderdel();

  const knapp = el("button", { type: "submit",
                               text: t("ui.hms.meld_send") });
  form.append(
    formvelger,
    plass,
    felt("hms-avvikstype", "ui.hms.avvikstype", typevalg),
    // ADVARSELEN FØR FELTET.
    el("p", { role: "note", class: "advarsel",
              text: t("ui.hms.fritekst_advarsel") }),
    felt("hms-beskrivelse", "ui.hms.beskrivelse", beskrivelse,
         "ui.hms.beskrivelse_hjelp"),
    felt("hms-sted", "ui.hms.sted", sted),
    felt("hms-hendelsesdato", "ui.hms.hendelsesdato", hendelsesdato),
    knapp, utfall);

  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.hms.meldt_kvittering",
    tilbakestill: () => {
      beskrivelse.value = "";
      sted.value = "";
      navn.value = "";
      rolle.value = "";
    },
    send: async (idem) => {
      const anonym = radioer.anonym.checked;
      const kropp = {
        avvikstype: typevalg.value,
        melderform: anonym ? "anonym" : "navngitt",
        beskrivelse: beskrivelse.value.trim(),
        sted: sted.value.trim(),
        hendelsesdato: hendelsesdato.value,
      };
      // NAVNET SENDES IKKE ENGANG OVER LEDNINGEN FOR ET ANONYMT
      // AVVIK. Døra nekter uansett; her er det ikke med i det hele
      // tatt, slik at en logglinje i et mellomledd ikke kan bære det.
      if (!anonym) {
        kropp.melder_navn = navn.value.trim();
        const r = rolle.value.trim();
        if (r) kropp.melder_rolle = r;
      }
      await meldAvvik(kropp, idem);
    },
  });

  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.hms.meld") }), form);
}


export function kravskjema(ctx, last, kvitter, s) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const tall = (id, verdi) => el("input", {
    type: "number", id, name: id, min: "1", value: String(verdi) });
  // ALLE FIRE GRENSENE FORHÅNDSUTFYLLES FRA BILDET. 123 lærte at et
  // skjema som viser mindre enn det lagrer er en felle: en grense som
  // ikke kom med ville blitt overskrevet med standardverdien første
  // gang noen trykket lagre.
  const maks = tall("hms-maks", s.oppbevaring_maks_dogn ?? 3650);
  const varsel = tall("hms-varsel", s.oppbevaringsvarsel_dogn ?? 60);
  const tiltak = tall("hms-tiltak", s.tiltaksfrist_dogn ?? 14);
  const regel = tall("hms-regelvarsel", s.regelvarsel_dogn ?? 60);
  const knapp = el("button", { type: "submit",
                               text: t("ui.hms.krav_lagre") });
  form.append(
    felt("hms-maks", "ui.hms.oppbevaring_maks_dogn", maks),
    felt("hms-varsel", "ui.hms.oppbevaringsvarsel_dogn", varsel),
    felt("hms-tiltak", "ui.hms.tiltaksfrist_dogn", tiltak),
    felt("hms-regelvarsel", "ui.hms.regelvarsel_dogn", regel),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.hms.krav_lagret_kvittering",
    send: (idem) => settHmskrav({
      oppbevaring_maks_dogn: Number(maks.value),
      oppbevaringsvarsel_dogn: Number(varsel.value),
      tiltaksfrist_dogn: Number(tiltak.value),
      regelvarsel_dogn: Number(regel.value),
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.hms.krav") }), form);
}


export function regelskjema(ctx, last, kvitter) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const type = velger("hms-regel-type", AVVIKSTYPER, TYPETEKST);
  const versjon = el("input", { type: "text", id: "hms-regel-versjon",
                                name: "versjon" });
  const hjemmel = el("input", { type: "text", id: "hms-regel-hjemmel",
                                name: "hjemmel" });
  const dogn = el("input", { type: "number", id: "hms-regel-dogn",
                             name: "oppbevaring_dogn", min: "1",
                             value: "1825" });
  const helse = el("input", { type: "checkbox", id: "hms-regel-helse",
                              name: "helseopplysninger" });
  const fra = el("input", { type: "date", id: "hms-regel-fra",
                            name: "gyldig_fra", value: ilokalDato() });
  const knapp = el("button", { type: "submit",
                               text: t("ui.hms.regel_ny") });
  form.append(
    felt("hms-regel-type", "ui.hms.avvikstype", type),
    felt("hms-regel-versjon", "ui.hms.versjon", versjon),
    felt("hms-regel-hjemmel", "ui.hms.hjemmel", hjemmel),
    felt("hms-regel-dogn", "ui.hms.oppbevaring_dogn", dogn),
    el("div", { class: "felt-inline" }, helse,
       el("label", { for: "hms-regel-helse",
                     text: t("ui.hms.helse_flagg") })),
    felt("hms-regel-fra", "ui.hms.gyldig_fra", fra),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.hms.regel_lagret",
    tilbakestill: () => {
      versjon.value = "";
      hjemmel.value = "";
      helse.checked = false;
    },
    send: (idem) => registrerHmsregel({
      avvikstype: type.value,
      versjon: versjon.value.trim(),
      hjemmel: hjemmel.value.trim(),
      oppbevaring_dogn: Number(dogn.value),
      helseopplysninger: helse.checked,
      gyldig_fra: fra.value,
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.hms.regelverk") }), form);
}


// TILTAKSPANELET. Åpnes fra en rad, og lukker avviket bare når
// mennesket huker av for det. Det finnes ingen automatikk her.
export function tiltakspanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  let aktivt = null;

  async function aapne(a) {
    aktivt = a;
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.hms.tiltak") }),
      el("p", { class: "muted", text: a.beskrivelse }));
    let d;
    try {
      d = await hentJson(`/v1/hms/avvik/${a.avvik_id}/tiltak`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      node.append(el("p", { role: "alert", text: t("ui.hms.feil") }));
      return;
    }
    const tiltak = d.tiltak || [];
    if (!tiltak.length) {
      node.append(el("p", { class: "muted",
                            text: t("ui.hms.tiltak_tomt") }));
    } else {
      const tbody = el("tbody");
      for (const x of tiltak) {
        tbody.append(el("tr", {},
          el("td", { text: x.beskrivelse }),
          el("td", { text: x.utfort_dato }),
          el("td", { text: x.opprettet_av }),
          el("td", { text: x.lukker
            ? t("ui.hms.tiltak_lukker") : "" })));
      }
      node.append(el("table", { class: "tabell" },
        el("thead", {}, el("tr", {},
          el("th", { text: t("ui.hms.tiltak_beskrivelse") }),
          el("th", { text: t("ui.hms.tiltak_utfort") }),
          el("th", { text: t("ui.hms.tiltak_utfort_av") }),
          el("th", { text: "" }))),
        tbody));
    }
    if (!harScope(ctx, "bestilling:opprett")) {
      node.append(el("p", { class: "muted",
                            text: t("ui.hms.mangler_tilgang") }));
      return;
    }
    // ET ANONYMISERT AVVIK TAR IKKE IMOT NYE TILTAK. Raden er et spor
    // av en avsluttet behandling; døra nekter, og skjemaet skal ikke
    // tilby noe døra vil avvise.
    if (a.anonymisert) return;
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    const form = el("form", { class: "skjema" });
    const beskr = el("textarea", { id: "hms-tiltak-beskrivelse",
                                   name: "beskrivelse", rows: 3 });
    const dato = el("input", { type: "date", id: "hms-tiltak-dato",
                               name: "utfort_dato",
                               value: ilokalDato() });
    const lukker = el("input", { type: "checkbox",
                                 id: "hms-tiltak-lukker",
                                 name: "lukker" });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.hms.tiltak_nytt") });
    form.append(
      felt("hms-tiltak-beskrivelse", "ui.hms.tiltak_beskrivelse",
           beskr, "ui.hms.beskrivelse_hjelp"),
      felt("hms-tiltak-dato", "ui.hms.tiltak_utfort", dato),
      el("div", { class: "felt-inline" }, lukker,
         el("label", { for: "hms-tiltak-lukker",
                       text: t("ui.hms.tiltak_lukker") })),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.hms.tiltak_lagret",
      send: (idem) => registrerTiltak(aktivt.avvik_id, {
        beskrivelse: beskr.value.trim(),
        lukker: lukker.checked,
        utfort_dato: dato.value,
      }, idem),
    });
    node.append(form);
  }

  return { node, aapne };
}


// ANONYMISERINGSPANELET — OG M-30-GRENSEN, PÅ SKJERMEN.
//
// Panelet henter `m53_oppbevaringsgrunnlag` FØR det tegner noe. Er
// fristen ikke gått, vises setningen saksbehandleren kan lime rett inn
// i M-30s avslagsbegrunnelse, OG feltet for saksreferansen — for uten
// den nekter døra.
//
// Er fristen gått, er anonymisering vår egen plikt, og referansen er
// valgfri.
export function anonymiseringspanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  let aktivt = null;

  async function aapne(a) {
    aktivt = a;
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.hms.anonymiser_tittel") }),
      el("p", { class: "muted", text: a.beskrivelse }));
    let g;
    try {
      g = await hentJson(
        `/v1/hms/avvik/${a.avvik_id}/oppbevaringsgrunnlag`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      node.append(el("p", { role: "alert", text: t("ui.hms.feil") }));
      return;
    }
    node.append(el("h3", { text: t("ui.hms.grunnlag") }));
    // SETNINGEN, ORDRETT FRA BASEN. Flaten formulerer den ikke selv:
    // hjemmelen og regelversjonen står på RADEN, og en tekst satt
    // sammen her ville vært en annen setning enn den døra håndhever.
    node.append(el("p", {}, el("strong", { text: g.setning })));
    node.append(el("p", { class: "muted",
                          text: t("ui.hms.grunnlag_hjelp") }));

    if (g.alt_anonymisert) return;
    if (!harScope(ctx, "bestilling:opprett")) {
      node.append(el("p", { class: "muted",
                            text: t("ui.hms.mangler_tilgang") }));
      return;
    }

    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    const form = el("form", { class: "skjema" });
    const ref = el("input", { type: "text", id: "hms-m30-ref",
                              name: "m30_sak_ref" });
    if (!g.kan_anonymiseres_naa) {
      form.append(el("p", { role: "alert",
        text: t("ui.hms.anonymiser_for_tidlig")
          .replace("{dato}", g.oppbevaring_til) }));
      ref.required = true;
    }
    form.append(
      el("p", { class: "muted",
                text: t("ui.hms.anonymiser_forklaring") }),
      felt("hms-m30-ref", "ui.hms.anonymiser_m30_ref", ref));
    const knapp = el("button", { type: "submit",
                                 text: t("ui.hms.anonymiser_bekreft") });
    form.append(knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.hms.anonymisert_ok",
      send: (idem) => anonymiserAvvik(aktivt.avvik_id,
        { m30_sak_ref: ref.value.trim() || null }, idem),
    });
    node.append(form);
  }

  return { node, aapne };
}


export function lukkepanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  let aktivt = null;

  function aapne(f) {
    aktivt = f;
    node.hidden = false;
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    const form = el("form", { class: "skjema" });
    const notat = el("textarea", { id: "hms-lukkenotat",
                                   name: "notat", rows: 3 });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.hms.funn_lukk") });
    form.append(
      felt("hms-lukkenotat", "ui.hms.funn_notat", notat),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.hms.funn_lukket",
      send: (idem) => lukkHmsfunn(aktivt.funn_id,
        { notat: notat.value.trim() }, idem),
    });
    sett(node, el("h2", { text: t("ui.hms.funn_lukk") }),
      el("p", {}, el("strong", {
        text: t(FUNNTEKST[f.funntype] || f.funntype) })),
      form);
  }

  return { node, aapne };
}


export function visHms(hoved, ctx) {
  const hode = () => flateHode(t("ui.hms.tittel"),
    t("ui.hms.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/hms"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const avvik = d.avvik || [];
      const regler = d.regelverk || [];
      const funn = d.funn || [];
      const skriver = harScope(ctx, "bestilling:opprett");
      const tiltak = tiltakspanel(ctx, last, kvitter);
      const anonym = anonymiseringspanel(ctx, last, kvitter);
      const lukking = lukkepanel(ctx, last, kvitter);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.hms.sammendrag") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT.
        el("p", { class: "muted", text: t("ui.hms.hvorfor") }));

      const avviksseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.hms.avvik") }));
      if (!avvik.length) {
        avviksseksjon.append(el("p", { class: "muted",
          text: t("ui.hms.avvik_tomt") }));
      } else {
        avviksseksjon.append(avvikstabell(avvik, {
          aapneTiltak: tiltak.aapne,
          aapneAnonymiser: skriver ? anonym.aapne : null }));
        if (s.avvik > avvik.length) {
          avviksseksjon.append(el("p", { class: "muted",
            text: t("ui.hms.vist_av")
              .replace("{vist}", String(avvik.length))
              .replace("{grense}", String(s.avvik)) }));
        }
      }

      const funnseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.hms.funn") }));
      if (!funn.length) {
        funnseksjon.append(el("p", { class: "muted",
          text: t("ui.hms.funn_tomt") }));
      } else {
        funnseksjon.append(funntabell(
          funn, skriver ? lukking.aapne : null));
      }

      const regelseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.hms.regelverk") }));
      if (!regler.length) {
        regelseksjon.append(el("p", { role: "alert",
          text: t("ui.hms.regelverk_tomt") }));
      } else {
        regelseksjon.append(regeltabell(regler, null));
      }

      // FUNNENE FØR AVVIKENE, og avvikene før regelverket.
      // Rekkefølgen er en dom: det som haster er det ingen har gjort
      // noe med, ikke registeret det ble meldt under.
      const deler = [oversikt, funnseksjon, lukking.node,
                     avviksseksjon, tiltak.node, anonym.node,
                     regelseksjon];
      if (skriver) {
        deler.push(meldeskjema(ctx, last, kvitter),
                   regelskjema(ctx, last, kvitter),
                   kravskjema(ctx, last, kvitter, s));
      }
      sett(kropp, ...deler);
    });
  return last();
}
