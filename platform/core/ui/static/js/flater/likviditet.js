// M-15 likviditets- og kostnadsagent (128) — MÅLINGEN ER PRODUKTET.
//
// FLATENS VIKTIGSTE JOBB ER Å VISE AT VI TOK FEIL.
//
// Klyngens delte dom: en gal prognose ser nøyaktig ut som en riktig
// prognose — helt til horisonten er passert, og da har alle sluttet å
// se. Derfor står `umaalte` og treffraten ØVERST, foran alle tallene
// om hvor mange prognoser vi har laget. Et sammendrag som begynte med
// «14 prognoser» ville fortalt hvor flittige vi har vært, ikke om vi
// hadde rett.
//
// BÅNDET TEGNES ALLTID, ALDRI BARE PUNKTET. Et punktestimat uten
// usikkerhet er ikke en presis prognose — det er en upresis prognose
// som har mistet informasjonen om hvor upresis den er. Tabellen viser
// nedre og øvre ved siden av punktet, i samme rad, uten at man må
// klikke.
//
// «MÅL DENNE UKEN» FINNES BARE NÅR UKEN ER OVER, og `kan_maales`
// kommer fra BASEN — flaten regner ikke ut selv om en uke er passert.
// Regelen bor ett sted (124s `kan_lukkes`-form), og en kopi her ville
// råtnet den dagen nådefristen endret seg.
//
// DET FINNES INGEN «IVERKSETT»-KNAPP, OG DET KAN IKKE FINNES. Et
// kostnadstiltak kan bli vurdert eller avvist. Oppsigelsen av et
// abonnement går gjennom M-41s policykontrollerte vei, av et
// menneske — og den veien vet ingenting om denne flaten.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  FeilformetFeil, hentJson, lagPrognose, lukkLikviditetsfunn,
  nyIdempotensnokkel,
  registrerLikviditetsmodell, registrerLikviditetspost,
  registrerMaaling, settLikviditetskrav, UautorisertFeil,
  vurderTiltak,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

export const POSTTYPER = ["lonn", "husleie", "skatt", "avgift",
                          "abonnement", "laan", "annet"];
export const GJENTAKELSER = ["engang", "ukentlig", "maanedlig",
                             "kvartalsvis", "aarlig"];
export const REVERSIBILITET = ["reversibel", "delvis_reversibel",
                               "irreversibel"];

const POSTTEKST = {
  lonn: "ui.likviditet.type_lonn",
  husleie: "ui.likviditet.type_husleie",
  skatt: "ui.likviditet.type_skatt",
  avgift: "ui.likviditet.type_avgift",
  abonnement: "ui.likviditet.type_abonnement",
  laan: "ui.likviditet.type_laan",
  annet: "ui.likviditet.type_annet",
};

const GJENTAKELSESTEKST = {
  engang: "ui.likviditet.gjentakelse_engang",
  ukentlig: "ui.likviditet.gjentakelse_ukentlig",
  maanedlig: "ui.likviditet.gjentakelse_maanedlig",
  kvartalsvis: "ui.likviditet.gjentakelse_kvartalsvis",
  aarlig: "ui.likviditet.gjentakelse_aarlig",
};

const REVERSIBILITETSTEKST = {
  reversibel: "ui.likviditet.rev_reversibel",
  delvis_reversibel: "ui.likviditet.rev_delvis",
  irreversibel: "ui.likviditet.rev_irreversibel",
};

const FUNNTEKST = {
  ingen_krav: "ui.likviditet.funn_ingen_krav",
  ingen_gyldig_modell: "ui.likviditet.funn_ingen_modell",
  modell_utloper_snart: "ui.likviditet.funn_modell_snart",
  prognose_uten_maaling: "ui.likviditet.funn_uten_maaling",
  prognose_mot_utdatert_grunnlag: "ui.likviditet.funn_utdatert",
  bane_under_null: "ui.likviditet.funn_under_null",
};

// ØRE TIL KRONER, SOM TEKST — aldri som flyttall i regnestykker.
// Delingen skjer HER, i visningen, og bare her: hele veien inn hit er
// beløpet et heltall i øre, fordi tolv ukers avrundingsfeil er et
// beløp ingen kan forklare.
export function kroner(ore) {
  if (typeof ore !== "number") return "";
  const negativ = ore < 0;
  const abs = Math.abs(ore);
  const hele = Math.floor(abs / 100);
  const rest = String(abs % 100).padStart(2, "0");
  const grupper = String(hele).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  return `${negativ ? "−" : ""}${grupper},${rest}`;
}

// LOKAL DATO, IKKE `toISOString()`. Den siste konverterer til UTC, og
// et skjema åpnet 00:30 norsk tid ville foreslått gårsdagen (124s
// `ilokalDato`, samme grunn).
export function ilokalDato(d = new Date()) {
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
          ? t("ui.likviditet.feil.tilstand")
          : t("ui.likviditet.feil.generell") }));
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


// SAMMENDRAGET. `umaalte` OG TREFFRATEN STÅR FØRST, og rekkefølgen er
// klyngens dom: en prognosemodul som ikke måles blir gradvis dårligere
// uten at noen oppdager det, mens den beholder autoriteten sin.
//
// `laveste_ore` under null står i fet skrift ved siden av. Det er det
// ene tallet modulen finnes for — men det er verdiløst hvis vi ikke
// vet om banene våre pleier å stemme.
export function sammendrag(s) {
  const p = el("p", {});
  p.append(el("strong", {
    text: t("ui.likviditet.umaalte_sum")
      .replace("{n}", String(s.umaalte ?? 0)) }));
  const maalt = (s.treff ?? 0) + (s.bom ?? 0);
  if (maalt > 0) {
    p.append(" ", el("strong", {
      text: t("ui.likviditet.treffrate")
        .replace("{treff}", String(s.treff ?? 0))
        .replace("{av}", String(maalt)) }));
  } else {
    // INGEN MÅLINGER ER IKKE «100 % TREFF». En tom treffrate skal si
    // at vi ikke vet, ikke se ut som at alt stemmer.
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.likviditet.ingen_maalinger") }));
  }
  if (typeof s.laveste_ore === "number" && s.laveste_ore < 0) {
    p.append(" ", el("strong", {
      text: t("ui.likviditet.under_null")
        .replace("{n}", kroner(s.laveste_ore)) }));
  }
  p.append(" ", el("span", {
    text: t("ui.likviditet.tellinger")
      .replace("{prognoser}", String(s.prognoser ?? 0))
      .replace("{aktive}", String(s.aktive ?? 0))
      .replace("{poster}", String(s.poster ?? 0)) }));
  if (s.uvurderte_tiltak > 0) {
    p.append(" ", el("span", {
      text: t("ui.likviditet.uvurderte")
        .replace("{n}", String(s.uvurderte_tiltak)) }));
  }
  if (!s.gyldige_modeller) {
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.likviditet.ingen_gyldig_modell") }));
  }
  if (s.apne_funn > 0) {
    p.append(" ", el("strong", {
      text: t("ui.likviditet.apne_funn_sum")
        .replace("{n}", String(s.apne_funn)) }));
  }
  if (!s.har_krav) {
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.likviditet.krav_mangler") }));
  } else {
    p.append(" ", el("span", { class: "muted",
      text: t("ui.likviditet.horisonten_er")
        .replace("{n}", String(s.horisont_uker ?? 0)) }));
  }
  return p;
}


// BANETABELLEN — BÅNDET I SAMME RAD SOM PUNKTET.
//
// `nedre` og `ovre` står ALLTID, aldri bak et klikk. En bane vist som
// én linje ville sett ut som en presis prognose, og det er nettopp
// den misforståelsen intervallet finnes for å hindre.
export function banetabell(bane, maal) {
  const tbody = el("tbody");
  for (const u of bane) {
    const under = u.punkt_ore < 0;
    const handling = el("td", {});
    if (maal && u.kan_maales) {
      handling.append(knappMed(t("ui.likviditet.maal_uken"),
                               () => maal(u)));
    }
    const fasit = el("td", {});
    if (typeof u.faktisk_ore === "number") {
      fasit.append(el("span", { text: kroner(u.faktisk_ore) }));
      fasit.append(el("br", {}));
      // TRAFF ELLER BOM PÅ INTERVALLET, ikke på punktet. Et punkt
      // bommer alltid; spørsmålet er om sannheten lå innenfor båndet.
      fasit.append(el(u.innenfor_intervall ? "span" : "strong", {
        class: u.innenfor_intervall ? "muted" : null,
        text: u.innenfor_intervall
          ? t("ui.likviditet.traff")
          : t("ui.likviditet.bom") }));
    }
    tbody.append(el("tr", {},
      el("td", { text: String(u.uke_nr) }),
      el("td", { text: u.ukeslutt }),
      el("td", {}, el(under ? "strong" : "span",
                      { text: kroner(u.punkt_ore) })),
      el("td", { class: "muted", text: kroner(u.nedre_ore) }),
      el("td", { class: "muted", text: kroner(u.ovre_ore) }),
      el("td", { text: u.inn_ore ? kroner(u.inn_ore) : "" }),
      el("td", { text: u.ut_ore ? kroner(u.ut_ore) : "" }),
      fasit, handling));
  }
  return el("table", { class: "tabell" },
    el("thead", {}, el("tr", {},
      el("th", { text: t("ui.likviditet.uke") }),
      el("th", { text: t("ui.likviditet.ukeslutt") }),
      el("th", { text: t("ui.likviditet.punkt") }),
      el("th", { text: t("ui.likviditet.nedre") }),
      el("th", { text: t("ui.likviditet.ovre") }),
      el("th", { text: t("ui.likviditet.inn") }),
      el("th", { text: t("ui.likviditet.ut") }),
      el("th", { text: t("ui.likviditet.faktisk") }),
      el("th", { text: "" }))),
    tbody);
}


export function prognosetabell(prognoser, aapne) {
  const tbody = el("tbody");
  for (const p of prognoser) {
    const umaalt = !p.aktiv && p.antall_maalinger === 0;
    const maalt = el("td", {});
    if (umaalt) {
      // HORISONTEN ER PASSERT OG INGEN HAR MÅLT. Det er klyngens
      // funn, og det skal være det mest synlige i raden.
      maalt.append(el("strong", {
        text: t("ui.likviditet.umaalt") }));
    } else {
      maalt.append(el("span", {
        text: t("ui.likviditet.maalinger")
          .replace("{n}", String(p.antall_maalinger))
          .replace("{treff}", String(p.treff)) }));
    }
    const under = typeof p.laveste_ore === "number"
                  && p.laveste_ore < 0;
    tbody.append(el("tr", {},
      el("td", { text: p.laget_dato }),
      el("td", { text: String(p.horisont_uker) }),
      el("td", { text: p.gjelder_til }),
      el("td", { text: p.modellversjon }),
      el("td", {}, el(under ? "strong" : "span",
                      { text: kroner(p.laveste_ore) })),
      maalt,
      el("td", { text: p.opprettet_av }),
      el("td", {}, aapne
        ? knappMed(t("ui.likviditet.vis_bane"), () => aapne(p))
        : null)));
  }
  return el("table", { class: "tabell" },
    el("thead", {}, el("tr", {},
      el("th", { text: t("ui.likviditet.laget") }),
      el("th", { text: t("ui.likviditet.horisont") }),
      el("th", { text: t("ui.likviditet.gjelder_til") }),
      el("th", { text: t("ui.likviditet.modellversjon") }),
      el("th", { text: t("ui.likviditet.laveste") }),
      el("th", { text: t("ui.likviditet.maalt") }),
      el("th", { text: t("ui.likviditet.laget_av") }),
      el("th", { text: "" }))),
    tbody);
}


export function posttabell(poster) {
  const tbody = el("tbody");
  for (const p of poster) {
    tbody.append(el("tr", {},
      el("td", { text: t(POSTTEKST[p.posttype] || p.posttype) }),
      el("td", { text: p.beskrivelse }),
      el("td", {}, el(p.belop_ore < 0 ? "strong" : "span",
                      { text: kroner(p.belop_ore) })),
      el("td", { text: p.forste_forfall }),
      el("td", { text: t(GJENTAKELSESTEKST[p.gjentakelse]
                         || p.gjentakelse) }),
      el("td", { text: p.gjelder_til || "" }),
      // NAVNET PÅ DEN SOM SATTE TALLET. Hele grunnen til at tabellen
      // finnes: huset kan ikke prise lønn, så et menneske gjør det —
      // og den som leser prognosen skal kunne spørre hvem.
      el("td", { text: p.registrert_av }),
      el("td", { text: p.aktiv ? "" : t("ui.likviditet.deaktivert") })));
  }
  return el("table", { class: "tabell" },
    el("thead", {}, el("tr", {},
      el("th", { text: t("ui.likviditet.posttype") }),
      el("th", { text: t("ui.likviditet.beskrivelse") }),
      el("th", { text: t("ui.likviditet.belop") }),
      el("th", { text: t("ui.likviditet.forfall") }),
      el("th", { text: t("ui.likviditet.gjentakelse") }),
      el("th", { text: t("ui.likviditet.gjelder_til") }),
      el("th", { text: t("ui.likviditet.registrert_av") }),
      el("th", { text: "" }))),
    tbody);
}


export function tiltakstabell(tiltak, vurder) {
  const tbody = el("tbody");
  for (const x of tiltak) {
    const handling = el("td", {});
    if (vurder && x.status === "foreslatt") {
      handling.append(knappMed(t("ui.likviditet.vurder"),
                               () => vurder(x)));
    }
    tbody.append(el("tr", {},
      el("td", { text: x.beskrivelse }),
      el("td", { text: kroner(x.forventet_effekt_ore) }),
      // REVERSIBILITETEN STÅR I EGEN KOLONNE, ikke i en detaljvisning.
      // Et irreversibelt tiltak skal være synlig i samme blikk som
      // beløpet, for det er kombinasjonen som er farlig: stor
      // besparelse, ingen vei tilbake.
      el("td", {}, el(x.reversibilitet === "irreversibel"
                        ? "strong" : "span",
                      { text: t(REVERSIBILITETSTEKST[x.reversibilitet]
                                || x.reversibilitet) })),
      el("td", { text: x.grunnlag }),
      el("td", { text: t(`ui.likviditet.status_${x.status}`) }),
      el("td", { text: x.vurdert_av || "" }),
      handling));
  }
  return el("table", { class: "tabell" },
    el("thead", {}, el("tr", {},
      el("th", { text: t("ui.likviditet.beskrivelse") }),
      el("th", { text: t("ui.likviditet.effekt") }),
      el("th", { text: t("ui.likviditet.reversibilitet") }),
      el("th", { text: t("ui.likviditet.grunnlag") }),
      el("th", { text: t("ui.likviditet.status") }),
      el("th", { text: t("ui.likviditet.vurdert_av") }),
      el("th", { text: "" }))),
    tbody);
}


export function modelltabell(modeller) {
  const tbody = el("tbody");
  for (const m of modeller) {
    const status = el("td", {});
    if (m.gyldig_naa) {
      status.append(el("span", { text: t("ui.likviditet.gyldig_naa") }));
      if (typeof m.dogn_til_utlop === "number"
          && m.dogn_til_utlop <= 60) {
        status.append(el("br", {}), el("strong", {
          text: t("ui.likviditet.dogn_til_utlop")
            .replace("{n}", String(m.dogn_til_utlop)) }));
      }
    } else {
      status.append(el("span", { class: "muted",
        text: t("ui.likviditet.avviklet") }));
    }
    tbody.append(el("tr", {},
      el("td", { text: m.navn }),
      el("td", { text: m.versjon }),
      // METODEN STÅR I TABELLEN, ikke bak et klikk. En modell ingen
      // kan lese er en modell ingen kan si er feil.
      el("td", { text: m.metode }),
      el("td", { text: m.baselinje }),
      el("td", { text: m.gyldig_fra }),
      el("td", { text: m.gyldig_til || "" }),
      status,
      el("td", { text: t("ui.likviditet.antall_prognoser")
        .replace("{n}", String(m.antall_prognoser ?? 0)) })));
  }
  return el("table", { class: "tabell" },
    el("thead", {}, el("tr", {},
      el("th", { text: t("ui.likviditet.modellnavn") }),
      el("th", { text: t("ui.likviditet.versjon") }),
      el("th", { text: t("ui.likviditet.metode") }),
      el("th", { text: t("ui.likviditet.baselinje") }),
      el("th", { text: t("ui.likviditet.gyldig_fra") }),
      el("th", { text: t("ui.likviditet.gyldig_til") }),
      el("th", { text: t("ui.likviditet.gyldig_naa") }),
      el("th", { text: t("ui.likviditet.prognoser") }))),
    tbody);
}


// FUNNTABELLEN. `kan_lukkes` KOMMER FRA BASEN, ikke fra en liste her.
export function funntabell(funn, lukk) {
  const tbody = el("tbody");
  for (const f of funn) {
    const handling = el("td", {});
    if (lukk && f.kan_lukkes) {
      handling.append(knappMed(t("ui.likviditet.funn_lukk"),
                               () => lukk(f)));
    } else if (f.kan_lukkes === false) {
      handling.append(el("span", { class: "muted",
        text: t("ui.likviditet.funn_kan_ikke_lukkes") }));
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
      el("th", { text: t("ui.likviditet.funn") }),
      el("th", { text: t("ui.likviditet.detalj") }),
      el("th", { text: "±" }),
      el("th", { text: t("ui.likviditet.forst_sett") }),
      el("th", { text: "" }))),
    tbody);
}


// BANEPANELET — med måleknappen der uken faktisk er over.
export function banepanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  let aktiv = null;

  function maalepanel(uke) {
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    const form = el("form", { class: "skjema" });
    // `required` OG en eksplisitt sjekk (CodeRabbit).
    //
    // `Number("")` er `0`. Et tomt felt sendt inn ville registrert
    // saldoen NULL kroner som det faktiske utfallet — og en måling
    // RETTES IKKE: den står som den ble avgitt. Et uhell her er
    // permanent, og det er den ene grunnen til at et manglende felt
    // ikke kan behandles som en null.
    const faktisk = el("input", { type: "number", id: "likv-faktisk",
                                  name: "faktisk_ore", step: "1",
                                  required: true });
    const baselinje = el("input", { type: "number",
                                    id: "likv-baselinje",
                                    name: "baselinje_ore",
                                    step: "1" });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.likviditet.maal_uken") });
    form.append(
      el("p", { class: "muted",
                text: t("ui.likviditet.maal_forklaring") }),
      felt("likv-faktisk", "ui.likviditet.faktisk", faktisk,
           "ui.likviditet.ore_hjelp"),
      felt("likv-baselinje", "ui.likviditet.baselinje_ore", baselinje,
           "ui.likviditet.baselinje_hjelp"),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.likviditet.maalt_ok",
      send: (idem) => {
        // Den programmatiske veien går utenom `required`, så sjekken
        // står her også. `FeilformetFeil` havner i skjemarammens
        // 4xx-gren, som viser meldingen og nullstiller
        // idempotensnøkkelen.
        if (faktisk.value.trim() === "") {
          throw new FeilformetFeil(400, "faktisk_mangler");
        }
        return registrerMaaling(aktiv.prognose_id, {
          uke_nr: uke.uke_nr,
          faktisk_ore: Number(faktisk.value),
          baselinje_ore: baselinje.value === ""
            ? null : Number(baselinje.value),
        }, idem);
      },
    });
    return form;
  }

  async function aapne(p) {
    aktiv = p;
    // MÅLET FANGES FØR VENTINGEN (CodeRabbit).
    //
    // To raske klikk på «vis banen» gir to hentinger. Uten denne
    // sjekken kunne den TREGESTE svare sist og tegne feil prognoses
    // bane under overskriften til den nyeste — og en kontantbane med
    // feil overskrift er verre enn ingen bane, fordi den ser riktig
    // ut og gjelder noe annet.
    const maal = p.prognose_id;
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.likviditet.bane") }),
      el("p", { class: "muted",
                text: t("ui.likviditet.bane_om")
                  .replace("{dato}", p.laget_dato)
                  .replace("{versjon}", p.modellversjon) }));
    let d;
    try {
      d = await hentJson(
        `/v1/likviditet/prognose/${maal}/bane`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (aktiv.prognose_id !== maal) return;
      node.append(el("p", { role: "alert",
                            text: t("ui.likviditet.feil") }));
      return;
    }
    if (aktiv.prognose_id !== maal) return;
    const bane = d.bane || [];
    if (!bane.length) {
      node.append(el("p", { class: "muted",
                            text: t("ui.likviditet.bane_tom") }));
      return;
    }
    const skriver = harScope(ctx, "bestilling:opprett");
    const plass = el("div", {});
    node.append(banetabell(bane, skriver
      ? (u) => sett(plass, maalepanel(u)) : null), plass);
  }

  return { node, aapne };
}


export function vurderingspanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  let aktivt = null;

  function aapne(x) {
    aktivt = x;
    node.hidden = false;
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    const form = el("form", { class: "skjema" });
    // TO VALG, OG «IVERKSATT» ER IKKE ETT AV DEM.
    const status = velger("likv-vurdering", ["vurdert", "avvist"], {
      vurdert: "ui.likviditet.status_vurdert",
      avvist: "ui.likviditet.status_avvist",
    });
    const notat = el("textarea", { id: "likv-vurderingsnotat",
                                   name: "notat", rows: 3 });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.likviditet.vurder") });
    form.append(
      felt("likv-vurdering", "ui.likviditet.status", status),
      felt("likv-vurderingsnotat", "ui.likviditet.notat", notat),
      el("p", { class: "muted",
                text: t("ui.likviditet.ingen_iverksettelse") }),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.likviditet.vurdert_ok",
      send: (idem) => vurderTiltak(aktivt.tiltak_id, {
        status: status.value, notat: notat.value.trim(),
      }, idem),
    });
    sett(node, el("h2", { text: t("ui.likviditet.vurder") }),
      el("p", {}, el("strong", { text: x.beskrivelse })),
      el("p", { class: "muted",
                text: t(REVERSIBILITETSTEKST[x.reversibilitet]
                        || x.reversibilitet) }),
      form);
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
    const notat = el("textarea", { id: "likv-lukkenotat",
                                   name: "notat", rows: 3 });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.likviditet.funn_lukk") });
    form.append(felt("likv-lukkenotat", "ui.likviditet.funn_notat",
                     notat), knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.likviditet.funn_lukket",
      send: (idem) => lukkLikviditetsfunn(aktivt.funn_id,
        { notat: notat.value.trim() }, idem),
    });
    sett(node, el("h2", { text: t("ui.likviditet.funn_lukk") }),
      el("p", {}, el("strong", {
        text: t(FUNNTEKST[f.funntype] || f.funntype) })),
      form);
  }

  return { node, aapne };
}


export function kravskjema(ctx, last, kvitter, s) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const tall = (id, verdi) => el("input", {
    type: "number", id, name: id, min: "1", value: String(verdi) });
  // ALLE FIRE GRENSENE FORHÅNDSUTFYLLES FRA BILDET (123s lærdom: et
  // skjema som viser mindre enn det lagrer er en felle — grensen som
  // ikke kom med ville blitt overskrevet med standardverdien).
  const h = tall("likv-horisont", s.horisont_uker ?? 13);
  const g = tall("likv-grunnlag", s.grunnlag_maks_alder_dogn ?? 7);
  const m = tall("likv-maalefrist", s.maalefrist_dogn ?? 14);
  const v = tall("likv-modellvarsel", s.modellvarsel_dogn ?? 30);
  const knapp = el("button", { type: "submit",
                               text: t("ui.likviditet.krav_lagre") });
  form.append(
    felt("likv-horisont", "ui.likviditet.horisont_uker", h,
         "ui.likviditet.horisont_hjelp"),
    felt("likv-grunnlag", "ui.likviditet.grunnlagsalder", g),
    felt("likv-maalefrist", "ui.likviditet.maalefrist", m,
         "ui.likviditet.maalefrist_hjelp"),
    felt("likv-modellvarsel", "ui.likviditet.modellvarsel", v),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.likviditet.krav_lagret",
    send: (idem) => settLikviditetskrav({
      horisont_uker: Number(h.value),
      grunnlag_maks_alder_dogn: Number(g.value),
      maalefrist_dogn: Number(m.value),
      modellvarsel_dogn: Number(v.value),
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.likviditet.krav") }), form);
}


export function modellskjema(ctx, last, kvitter) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const navn = el("input", { type: "text", id: "likv-modellnavn",
                             name: "navn" });
  const versjon = el("input", { type: "text", id: "likv-modellversjon",
                                name: "versjon" });
  const metode = el("textarea", { id: "likv-metode", name: "metode",
                                  rows: 3 });
  const baselinje = el("input", { type: "text", id: "likv-baselinje-navn",
                                  name: "baselinje",
                                  value: "samme som forrige uke" });
  const fra = el("input", { type: "date", id: "likv-modell-fra",
                            name: "gyldig_fra", value: ilokalDato() });
  const knapp = el("button", { type: "submit",
                               text: t("ui.likviditet.modell_ny") });
  form.append(
    felt("likv-modellnavn", "ui.likviditet.modellnavn", navn),
    felt("likv-modellversjon", "ui.likviditet.versjon", versjon),
    felt("likv-metode", "ui.likviditet.metode", metode,
         "ui.likviditet.metode_hjelp"),
    felt("likv-baselinje-navn", "ui.likviditet.baselinje", baselinje,
         "ui.likviditet.baselinje_navn_hjelp"),
    felt("likv-modell-fra", "ui.likviditet.gyldig_fra", fra),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.likviditet.modell_lagret",
    tilbakestill: () => { navn.value = ""; versjon.value = "";
                          metode.value = ""; },
    send: (idem) => registrerLikviditetsmodell({
      navn: navn.value.trim(), versjon: versjon.value.trim(),
      metode: metode.value.trim(), baselinje: baselinje.value.trim(),
      gyldig_fra: fra.value,
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.likviditet.modeller") }), form);
}


// FORPLIKTELSESSKJEMAET — det som finnes fordi huset ikke kan prise
// lønn. Hjelpeteksten sier det rett ut: M-39 måler timer, ikke kroner.
export function postskjema(ctx, last, kvitter) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const type = velger("likv-posttype", POSTTYPER, POSTTEKST);
  const beskrivelse = el("input", { type: "text", id: "likv-postnavn",
                                    name: "beskrivelse" });
  // `required` OG en eksplisitt sjekk, av samme grunn som målingen:
  // `Number("")` er `0`, og en forpliktelse på null kroner er en rad
  // som SER registrert ut og ikke belaster banen med noe.
  const belop = el("input", { type: "number", id: "likv-belop",
                              name: "belop_ore", step: "1",
                              required: true });
  const forfall = el("input", { type: "date", id: "likv-forfall",
                                name: "forste_forfall",
                                value: ilokalDato() });
  const gj = velger("likv-gjentakelse", GJENTAKELSER,
                    GJENTAKELSESTEKST);
  const knapp = el("button", { type: "submit",
                               text: t("ui.likviditet.post_ny") });
  form.append(
    el("p", { class: "muted", text: t("ui.likviditet.post_hvorfor") }),
    felt("likv-posttype", "ui.likviditet.posttype", type),
    felt("likv-postnavn", "ui.likviditet.beskrivelse", beskrivelse),
    felt("likv-belop", "ui.likviditet.belop", belop,
         "ui.likviditet.belop_hjelp"),
    felt("likv-forfall", "ui.likviditet.forfall", forfall),
    felt("likv-gjentakelse", "ui.likviditet.gjentakelse", gj),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.likviditet.post_lagret",
    tilbakestill: () => { beskrivelse.value = ""; belop.value = ""; },
    send: (idem) => {
      if (belop.value.trim() === "") {
        throw new FeilformetFeil(400, "belop_mangler");
      }
      return registrerLikviditetspost({
        posttype: type.value,
        beskrivelse: beskrivelse.value.trim(),
        belop_ore: Number(belop.value),
        forste_forfall: forfall.value,
        gjentakelse: gj.value,
      }, idem);
    },
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.likviditet.poster") }), form);
}


export function prognoseskjema(ctx, last, kvitter, modeller) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const gyldige = (modeller || []).filter((m) => m.gyldig_naa);
  const valg = el("select", { id: "likv-modellvalg",
                              name: "modell_id" });
  for (const m of gyldige) {
    valg.append(el("option", { value: m.modell_id,
                               text: `${m.navn} ${m.versjon}` }));
  }
  const usikkerhet = el("input", { type: "number",
                                   id: "likv-usikkerhet",
                                   name: "usikkerhet_bp", min: "1",
                                   max: "10000", value: "1500" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.likviditet.prognose_ny") });
  form.append(
    felt("likv-modellvalg", "ui.likviditet.modell", valg),
    felt("likv-usikkerhet", "ui.likviditet.usikkerhet", usikkerhet,
         "ui.likviditet.usikkerhet_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.likviditet.prognose_laget",
    send: (idem) => lagPrognose({
      modell_id: valg.value,
      usikkerhet_bp: Number(usikkerhet.value),
    }, idem),
  });
  const kort = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.likviditet.prognose_ny") }));
  if (!gyldige.length) {
    // UTEN EN GYLDIG MODELL KAN INGEN PROGNOSE LAGES, og døra nekter.
    // Skjemaet skal si det, ikke la brukeren finne det ut av en 400.
    kort.append(el("p", { role: "alert",
      text: t("ui.likviditet.ingen_gyldig_modell") }));
    return kort;
  }
  kort.append(form);
  return kort;
}


export function visLikviditet(hoved, ctx) {
  const hode = () => flateHode(t("ui.likviditet.tittel"),
    t("ui.likviditet.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/likviditet"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const prognoser = d.prognoser || [];
      const modeller = d.modeller || [];
      const poster = d.poster || [];
      const tiltak = d.tiltak || [];
      const funn = d.funn || [];
      const skriver = harScope(ctx, "bestilling:opprett");
      const bane = banepanel(ctx, last, kvitter);
      const vurdering = vurderingspanel(ctx, last, kvitter);
      const lukking = lukkepanel(ctx, last, kvitter);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.likviditet.sammendrag") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT.
        el("p", { class: "muted",
                  text: t("ui.likviditet.hvorfor") }));

      const funnseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.likviditet.funn") }));
      if (!funn.length) {
        funnseksjon.append(el("p", { class: "muted",
          text: t("ui.likviditet.funn_tomt") }));
      } else {
        funnseksjon.append(funntabell(
          funn, skriver ? lukking.aapne : null));
      }

      const prognoseseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.likviditet.prognoser") }));
      if (!prognoser.length) {
        prognoseseksjon.append(el("p", { class: "muted",
          text: t("ui.likviditet.prognoser_tomt") }));
      } else {
        prognoseseksjon.append(prognosetabell(prognoser, bane.aapne));
      }

      const tiltaksseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.likviditet.tiltak") }));
      if (!tiltak.length) {
        tiltaksseksjon.append(el("p", { class: "muted",
          text: t("ui.likviditet.tiltak_tomt") }));
      } else {
        tiltaksseksjon.append(tiltakstabell(
          tiltak, skriver ? vurdering.aapne : null));
      }

      const postseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.likviditet.poster") }));
      if (!poster.length) {
        postseksjon.append(el("p", { class: "muted",
          text: t("ui.likviditet.poster_tomt") }));
      } else {
        postseksjon.append(posttabell(poster));
      }

      const modellseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.likviditet.modeller") }));
      if (!modeller.length) {
        modellseksjon.append(el("p", { role: "alert",
          text: t("ui.likviditet.modeller_tomt") }));
      } else {
        modellseksjon.append(modelltabell(modeller));
      }

      // FUNNENE FØRST, SÅ PROGNOSENE. Rekkefølgen er en dom: det som
      // haster er det ingen har målt og den banen som går under null
      // — ikke listen over hvor mange prognoser vi har laget.
      const deler = [oversikt, funnseksjon, lukking.node,
                     prognoseseksjon, bane.node, tiltaksseksjon,
                     vurdering.node, postseksjon, modellseksjon];
      if (skriver) {
        deler.push(prognoseskjema(ctx, last, kvitter, modeller),
                   postskjema(ctx, last, kvitter),
                   modellskjema(ctx, last, kvitter),
                   kravskjema(ctx, last, kvitter, s));
      }
      sett(kropp, ...deler);
    });
  return last();
}
