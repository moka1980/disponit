// M-33 prediksjons- og scenarioagent (130) — DOMMEN ER PRODUKTET.
//
// FLATENS VIKTIGSTE JOBB ER Å VISE AT MODELLEN KAN TAPE.
//
// Klyngens delte dom: en gal prognose ser nøyaktig ut som en riktig
// prognose — helt til horisonten er passert, og da har alle sluttet å
// se. M-33s egen dom er skarpere: EN MODELL SOM IKKE KAN TAPE, HAR
// IKKE VUNNET. Derfor står `uker_umaalt` og treffraten ØVERST, foran
// alle tallene om hvor mange prognoser vi har laget, og derfor står
// basislinjen i SAMME RAD som punktet i banetabellen — ikke i en
// fotnote.
//
// BÅNDET TEGNES ALLTID, ALDRI BARE PUNKTET. Et punktestimat uten
// usikkerhet er ikke en presis prognose — det er en upresis prognose
// som har mistet informasjonen om hvor upresis den er. Tabellen viser
// nedre og øvre ved siden av punktet, i samme rad, uten at man må
// klikke.
//
// DATAKVALITETEN HAR TRE VERDIER, OG DEN TREDJE ER POENGET. `ren`
// betyr at M-3 har sett og ikke funnet noe; `ukjent` at ingen har
// sett etter. En flate som slo dem sammen til et grønt merke ville
// utstedt et kvalitetsstempel ingen har signert.
//
// «MÅL DENNE UKEN» FINNES BARE NÅR UKEN ER OVER, og `kan_maales`
// kommer fra BASEN — flaten regner ikke ut selv om en uke er passert.
// Regelen bor ett sted (124s `kan_lukkes`-form), og en kopi her ville
// råtnet den dagen målefristen endret seg.
//
// DET FINNES INGEN «ANSETT»- ELLER «SI OPP»-KNAPP, OG DET KAN IKKE
// FINNES. Modulen lager en bane og stopper der. En personalavgjørelse
// går gjennom en egen policykontrollert vei, av et menneske — og den
// veien vet ingenting om denne flaten.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  avviklPrognosemodell, FeilformetFeil, hentJson, lagBemanningsprognose,
  lukkPrognosefunn, nyIdempotensnokkel, registrerBemanningsmaaling,
  registrerPrognosemodell, settPrognosekrav, UautorisertFeil,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

const KVALITETSTEKST = {
  ren: "ui.prognose.kvalitet_ren",
  flagget: "ui.prognose.kvalitet_flagget",
  ukjent: "ui.prognose.kvalitet_ukjent",
};

const FUNNTEKST = {
  prognose_uten_maaling: "ui.prognose.funn_uten_maaling",
  slaar_ikke_naiv_baseline: "ui.prognose.funn_taper",
  prognose_paa_ukjent_datakvalitet: "ui.prognose.funn_ukjent",
  modell_uten_prognose: "ui.prognose.funn_ubrukt",
};


// MINUTTER VISES SOM MINUTTER, ikke som desimaltimer.
//
// M-39s dom (113), arvet: `0.1 + 0.2` er ikke `0.3`. Men det er ikke
// hovedgrunnen her — hovedgrunnen er at «12,7 timer» ser ut som en
// måling med to signifikante siffer, mens «762 minutter» er det tallet
// som faktisk står i basen. Timene settes i parentes fordi mennesker
// planlegger i timer, og tallet i parentes er en AVLEDNING som får se
// avrundet ut.
export function minutter(m) {
  if (typeof m !== "number") return "–";
  const timer = Math.floor(m / 60);
  const rest = m % 60;
  return `${m} (${timer} t ${rest} min)`;
}


function felt(id, tekst, kontroll, hjelp) {
  return el("div", { class: "felt" },
    el("label", { for: id, text: t(tekst) }),
    kontroll,
    hjelp ? el("p", { class: "muted", text: t(hjelp) }) : null);
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
          ? t("ui.prognose.feil.tilstand")
          : t("ui.prognose.feil.generell") }));
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


// SAMMENDRAGET. `uker_umaalt` OG TREFFRATEN STÅR FØRST, og
// rekkefølgen er klyngens dom: en prognosemodul som ikke måles blir
// gradvis dårligere uten at noen oppdager det, mens den beholder
// autoriteten sin.
export function sammendrag(s) {
  const p = el("p", {});
  p.append(el("strong", {
    text: t("ui.prognose.umaalte_sum")
      .replace("{n}", String(s.uker_umaalt ?? 0)) }));
  const maalt = (s.treff ?? 0) + (s.bom ?? 0);
  if (maalt > 0) {
    p.append(" ", el("strong", {
      text: t("ui.prognose.treffrate")
        .replace("{treff}", String(s.treff ?? 0))
        .replace("{av}", String(maalt)) }));
  } else {
    // INGEN MÅLINGER ER IKKE «100 % TREFF». En tom treffrate skal si
    // at vi ikke vet, ikke se ut som at alt stemmer.
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.prognose.ingen_maalinger") }));
  }
  if (s.prognoser_ukjent_kvalitet > 0) {
    // «INGEN FUNN» OG «INGEN HAR SETT ETTER» ER IKKE SAMME TILSTAND,
    // og forskjellen står her fordi den ellers forsvinner.
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.prognose.ukjent_kvalitet_sum")
        .replace("{n}", String(s.prognoser_ukjent_kvalitet)) }));
  }
  p.append(" ", el("span", {
    text: t("ui.prognose.tellinger")
      .replace("{prognoser}", String(s.prognoser ?? 0))
      .replace("{uker}", String(s.uker_totalt ?? 0))
      .replace("{modeller}", String(s.modeller ?? 0)) }));
  if (!s.gyldige_modeller) {
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.prognose.ingen_gyldig_modell") }));
  }
  if (s.apne_funn > 0) {
    p.append(" ", el("strong", {
      text: t("ui.prognose.apne_funn_sum")
        .replace("{n}", String(s.apne_funn)) }));
  }
  if (!s.har_krav) {
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.prognose.krav_mangler") }));
  } else {
    p.append(" ", el("span", { class: "muted",
      text: t("ui.prognose.grensene_er")
        .replace("{horisont}", String(s.horisont_uker ?? 0))
        .replace("{grunnlag}", String(s.grunnlag_uker ?? 0))
        .replace("{dom}", String(s.domsgrunnlag_uker ?? 0)) }));
  }
  return p;
}


// BANETABELLEN. PUNKTET, BÅNDET OG BASISLINJEN I SAMME RAD.
//
// Basislinjen står ved siden av punktet — ikke fordi det er pent,
// men fordi «slår modellen basislinjen?» ellers krever hoderegning.
// Er de to tallene like langt fra det faktiske, har modellen tapt,
// og da skal man se det uten å regne.
export function banetabell(bane, maal) {
  const tabell = el("table", { class: "tabell" });
  const hoder = ["ui.prognose.uke", "ui.prognose.ukeslutt",
                 "ui.prognose.punkt", "ui.prognose.baand",
                 "ui.prognose.baselinje", "ui.prognose.faktisk",
                 "ui.prognose.avvik", "ui.prognose.baselineavvik",
                 "ui.prognose.traff"];
  const hoderad = el("tr", {});
  for (const h of hoder) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  if (maal) {
    hoderad.append(el("th", { scope: "col",
                              text: t("ui.prognose.handling") }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const u of bane) {
    const rad = el("tr", {},
      el("td", { text: String(u.uke_nr) }),
      el("td", { text: u.ukeslutt }),
      el("td", { text: minutter(u.forventet_minutter) }),
      // BÅNDET ER ALLTID MED. Det står aldri en tom celle her: et
      // punkt uten spenn er et tall som påstår å være et faktum.
      el("td", { text: t("ui.prognose.baand_verdi")
        .replace("{ned}", String(u.nedre_minutter))
        .replace("{opp}", String(u.ovre_minutter)) }),
      el("td", { text: minutter(u.baseline_minutter) }),
      el("td", { text: typeof u.faktisk_minutter === "number"
        ? minutter(u.faktisk_minutter) : "–" }),
      el("td", { text: typeof u.avvik_minutter === "number"
        ? String(u.avvik_minutter) : "–" }),
      el("td", { text: typeof u.baseline_avvik_minutter === "number"
        ? String(u.baseline_avvik_minutter) : "–" }),
      el("td", { text: u.innenfor_intervall === null
        || u.innenfor_intervall === undefined
        ? "–"
        : t(u.innenfor_intervall
            ? "ui.prognose.traff_ja" : "ui.prognose.traff_nei") }));
    if (maal) {
      const celle = el("td", {});
      // `kan_maales` KOMMER FRA BASEN. Flaten regner ikke ut selv om
      // uken er over — gjorde den det, ville knappen blitt aktiv en
      // dag før døra sier ja.
      if (u.kan_maales) {
        celle.append(knappMed(t("ui.prognose.maal_uken"),
                              () => maal(u)));
      }
      rad.append(celle);
    }
    kropp.append(rad);
  }
  tabell.append(kropp);
  return tabell;
}


export function prognosetabell(prognoser, aapne) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.prognose.laget", "ui.prognose.modellversjon",
                   "ui.prognose.horisont", "ui.prognose.grunnlag",
                   "ui.prognose.datakvalitet", "ui.prognose.maalt",
                   "ui.prognose.laget_av", "ui.prognose.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const p of prognoser) {
    const kvalitet = el("td", {});
    const nokkel = KVALITETSTEKST[p.datakvalitet];
    // EN UKJENT VERDI VISES SOM SEG SELV, ikke som ingenting. Faller
    // en ny verdi inn i det lukkede settet uten at flaten er
    // oppdatert, skal den være SYNLIG — en tom celle ville skjult
    // nettopp det som må ses.
    const tekst = nokkel ? t(nokkel) : String(p.datakvalitet);
    if (p.datakvalitet === "ren") {
      kvalitet.append(el("span", { text: tekst }));
    } else {
      kvalitet.append(el("strong", { role: "alert",
        text: p.datakvalitet === "flagget"
          ? `${tekst} (${p.datakvalitet_antall})` : tekst }));
    }
    const handling = el("td", {});
    handling.append(knappMed(t("ui.prognose.vis_bane"),
                             () => aapne(p)));
    kropp.append(el("tr", {},
      el("td", { text: p.laget_dato }),
      el("td", { text: p.modellversjon }),
      el("td", { text: String(p.horisont_uker) }),
      el("td", { text: t("ui.prognose.grunnlag_verdi")
        .replace("{uker}", String(p.grunnlag_antall_uker))
        .replace("{av}", String(p.grunnlag_uker))
        .replace("{dato}", p.grunnlag_siste_dato) }),
      kvalitet,
      el("td", { text: String(p.antall_maalt) }),
      el("td", { text: p.laget_av }),
      handling));
  }
  tabell.append(kropp);
  return tabell;
}


export function modelltabell(modeller) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.prognose.modell", "ui.prognose.versjon",
                   "ui.prognose.metode", "ui.prognose.baselinje",
                   "ui.prognose.gyldig_fra", "ui.prognose.gyldig_til",
                   "ui.prognose.gjelder",
                   "ui.prognose.antall_prognoser"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const m of modeller) {
    kropp.append(el("tr", {},
      el("td", { text: m.navn }),
      el("td", { text: m.versjon }),
      el("td", { text: m.metode }),
      // BASISLINJEN STÅR I MODELLTABELLEN OGSÅ. En modell uten en
      // navngitt basislinje kan ikke måles mot noe, og da er
      // `slaar_ikke_naiv_baseline` et funn ingen kan reise.
      el("td", { text: m.baselinje }),
      el("td", { text: m.gyldig_fra }),
      el("td", { text: m.gyldig_til || "–" }),
      el("td", { text: t(m.gjelder ? "ui.prognose.gjelder_ja"
                                   : "ui.prognose.avviklet") }),
      el("td", { text: String(m.antall_prognoser) })));
  }
  tabell.append(kropp);
  return tabell;
}


export function funntabell(funn, lukk) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.prognose.funntype", "ui.prognose.referanse",
                   "ui.prognose.detaljer", "ui.prognose.over_grense",
                   "ui.prognose.status", "ui.prognose.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const f of funn) {
    const handling = el("td", {});
    // `kan_lukkes` KOMMER FRA BASEN (124s form). Flaten husker ikke
    // hvilke funn som er sveipens — gjorde den det, ville lista her
    // og lista i 130 kunne gli fra hverandre.
    if (lukk && f.kan_lukkes) {
      handling.append(knappMed(t("ui.prognose.lukk"), () => lukk(f)));
    } else if (f.apen) {
      handling.append(el("span", { class: "muted",
        text: t("ui.prognose.lukkes_av_sveipen") }));
    }
    const nokkel = FUNNTEKST[f.funntype];
    kropp.append(el("tr", {},
      el("td", { text: nokkel ? t(nokkel) : String(f.funntype) }),
      el("td", { text: f.referanse }),
      el("td", { text: f.detaljer }),
      el("td", { text: String(f.over_grense ?? 0) }),
      el("td", { text: f.apen
        ? t("ui.prognose.apen")
        : t("ui.prognose.lukket_av").replace("{av}",
                                             f.lukket_av || "–") }),
      handling));
  }
  tabell.append(kropp);
  return tabell;
}


export function banepanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  let aktiv = null;

  function maalepanel(uke) {
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    const form = el("form", { class: "skjema" });
    // `required` OG en eksplisitt sjekk (128s CodeRabbit-funn).
    //
    // `Number("")` er `0`. Et tomt felt sendt inn ville registrert
    // NULL MINUTTER som ukens faktiske arbeid — og en måling RETTES
    // IKKE: den står som den ble avgitt. Et uhell her er permanent,
    // og det er den ene grunnen til at et manglende felt ikke kan
    // behandles som en null.
    const faktisk = el("input", { type: "number", id: "prog-faktisk",
                                  name: "faktisk_minutter", step: "1",
                                  min: "0", required: true });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.prognose.maal_uken") });
    form.append(
      el("p", { class: "muted",
                text: t("ui.prognose.maal_forklaring") }),
      felt("prog-faktisk", "ui.prognose.faktisk", faktisk,
           "ui.prognose.minutt_hjelp"),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.prognose.maalt_ok",
      send: (idem) => {
        // Den programmatiske veien går utenom `required`, så sjekken
        // står her også. `FeilformetFeil` havner i skjemarammens
        // 4xx-gren, som viser meldingen og nullstiller
        // idempotensnøkkelen.
        if (faktisk.value.trim() === "") {
          throw new FeilformetFeil(400, "faktisk_mangler");
        }
        return registrerBemanningsmaaling(aktiv.prognose_id, {
          uke_nr: uke.uke_nr,
          faktisk_minutter: Number(faktisk.value),
        }, idem);
      },
    });
    return form;
  }

  async function aapne(p) {
    aktiv = p;
    // MÅLET FANGES FØR VENTINGEN (128s CodeRabbit-funn).
    //
    // To raske klikk på «vis banen» gir to hentinger. Uten denne
    // sjekken kunne den TREGESTE svare sist og tegne feil prognoses
    // bane under overskriften til den nyeste — og en bane med feil
    // overskrift er verre enn ingen bane, fordi den ser riktig ut og
    // gjelder noe annet.
    const maal = p.prognose_id;
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.prognose.bane") }),
      el("p", { class: "muted",
                text: t("ui.prognose.bane_om")
                  .replace("{dato}", p.laget_dato)
                  .replace("{versjon}", p.modellversjon) }));
    let d;
    try {
      d = await hentJson(`/v1/prognose/prognose/${maal}/bane`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (aktiv.prognose_id !== maal) return;
      node.append(el("p", { role: "alert",
                            text: t("ui.prognose.feil") }));
      return;
    }
    if (aktiv.prognose_id !== maal) return;
    const bane = d.bane || [];
    if (!bane.length) {
      node.append(el("p", { class: "muted",
                            text: t("ui.prognose.bane_tom") }));
      return;
    }
    const skriver = harScope(ctx, "bestilling:opprett");
    const plass = el("div", {});
    node.append(banetabell(bane, skriver
      ? (u) => sett(plass, maalepanel(u)) : null), plass);
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
    const begrunnelse = el("textarea", { id: "prog-lukkegrunn",
                                         name: "begrunnelse",
                                         rows: "3", required: true });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.prognose.lukk") });
    form.append(
      felt("prog-lukkegrunn", "ui.prognose.begrunnelse", begrunnelse,
           "ui.prognose.begrunnelse_hjelp"),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.prognose.lukket_ok",
      send: (idem) => lukkPrognosefunn(
        aktiv.funn_id, { begrunnelse: begrunnelse.value }, idem),
    });
    sett(node, el("h2", { text: t("ui.prognose.lukk_funn") }),
      el("p", { class: "muted",
                text: t("ui.prognose.lukk_om")
                  .replace("{type}", f.funntype) }),
      form);
  }

  return { node, aapne };
}


export function kravskjema(ctx, last, kvitter, s) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  // ALLE FIRE GRENSENE FORHÅNDSUTFYLLES FRA BASEN (123s lærdom): et
  // skjema som viser mindre enn det lagrer er en felle, fordi et
  // innsendt skjema setter ALLE fire — også dem brukeren ikke så.
  const horisont = el("input", { type: "number", id: "prog-horisont",
                                 name: "horisont_uker", min: "1",
                                 max: "52", required: true,
                                 value: String(s.horisont_uker ?? 8) });
  const grunnlag = el("input", { type: "number", id: "prog-grunnlag",
                                 name: "grunnlag_uker", min: "2",
                                 max: "104", required: true,
                                 value: String(s.grunnlag_uker ?? 8) });
  const frist = el("input", { type: "number", id: "prog-frist",
                              name: "maalefrist_dogn", min: "1",
                              max: "180", required: true,
                              value: String(s.maalefrist_dogn ?? 14) });
  const dom = el("input", { type: "number", id: "prog-dom",
                            name: "domsgrunnlag_uker", min: "2",
                            max: "52", required: true,
                            value: String(s.domsgrunnlag_uker ?? 4) });
  const knapp = el("button", { type: "submit",
                               text: t("ui.prognose.krav_lagre") });
  form.append(
    felt("prog-horisont", "ui.prognose.horisont", horisont,
         "ui.prognose.horisont_hjelp"),
    felt("prog-grunnlag", "ui.prognose.grunnlag_uker", grunnlag,
         "ui.prognose.grunnlag_hjelp"),
    felt("prog-frist", "ui.prognose.maalefrist", frist,
         "ui.prognose.maalefrist_hjelp"),
    felt("prog-dom", "ui.prognose.domsgrunnlag", dom,
         "ui.prognose.domsgrunnlag_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.prognose.krav_lagret",
    send: (idem) => settPrognosekrav({
      horisont_uker: Number(horisont.value),
      grunnlag_uker: Number(grunnlag.value),
      maalefrist_dogn: Number(frist.value),
      domsgrunnlag_uker: Number(dom.value),
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.prognose.krav") }), form);
}


export function modellskjema(ctx, last, kvitter) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const navn = el("input", { type: "text", id: "prog-modellnavn",
                             name: "navn", required: true });
  const versjon = el("input", { type: "text", id: "prog-modellversjon",
                                name: "versjon", required: true });
  const metode = el("textarea", { id: "prog-metode", name: "metode",
                                  rows: "3", required: true });
  const baselinje = el("input", { type: "text", id: "prog-baselinje",
                                  name: "baselinje", required: true });
  const fra = el("input", { type: "date", id: "prog-gyldigfra",
                            name: "gyldig_fra", required: true });
  const til = el("input", { type: "date", id: "prog-gyldigtil",
                            name: "gyldig_til" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.prognose.modell_ny") });
  form.append(
    felt("prog-modellnavn", "ui.prognose.modell", navn),
    felt("prog-modellversjon", "ui.prognose.versjon", versjon),
    felt("prog-metode", "ui.prognose.metode", metode,
         "ui.prognose.metode_hjelp"),
    // BASISLINJEN ER OBLIGATORISK, og hjelpeteksten sier hvorfor:
    // uten et navn på det modellen måles mot, er «slår den
    // basislinjen?» et spørsmål uten referanse.
    felt("prog-baselinje", "ui.prognose.baselinje", baselinje,
         "ui.prognose.baselinje_hjelp"),
    felt("prog-gyldigfra", "ui.prognose.gyldig_fra", fra),
    felt("prog-gyldigtil", "ui.prognose.gyldig_til", til,
         "ui.prognose.gyldig_til_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.prognose.modell_lagret",
    send: (idem) => registrerPrognosemodell({
      navn: navn.value, versjon: versjon.value,
      metode: metode.value, baselinje: baselinje.value,
      gyldig_fra: fra.value,
      gyldig_til: til.value === "" ? null : til.value,
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.prognose.modell_ny") }), form);
}


export function avviklingsskjema(ctx, last, kvitter, modeller) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const gyldige = (modeller || []).filter((m) => m.gjelder);
  const valg = el("select", { id: "prog-avviklvalg",
                              name: "modell_id" });
  for (const m of gyldige) {
    valg.append(el("option", { value: m.modell_id,
                               text: `${m.navn} ${m.versjon}` }));
  }
  const til = el("input", { type: "date", id: "prog-avvikltil",
                            name: "gyldig_til", required: true });
  const knapp = el("button", { type: "submit",
                               text: t("ui.prognose.avvikl") });
  form.append(
    felt("prog-avviklvalg", "ui.prognose.modell", valg),
    // AVVIKLING ER ENVEIS, og skjemaet sier det FØR knappen. En
    // handling som ikke kan angres, skal ikke oppdages som
    // uangrelig etterpå.
    felt("prog-avvikltil", "ui.prognose.gyldig_til", til,
         "ui.prognose.avvikl_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.prognose.avviklet_ok",
    send: (idem) => avviklPrognosemodell(
      valg.value, { gyldig_til: til.value }, idem),
  });
  const kort = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.prognose.avvikl") }));
  if (!gyldige.length) {
    kort.append(el("p", { class: "muted",
      text: t("ui.prognose.ingen_aa_avvikle") }));
    return kort;
  }
  kort.append(form);
  return kort;
}


export function prognoseskjema(ctx, last, kvitter, modeller) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const gyldige = (modeller || []).filter((m) => m.gjelder);
  const valg = el("select", { id: "prog-modellvalg",
                              name: "modell_id" });
  for (const m of gyldige) {
    valg.append(el("option", { value: m.modell_id,
                               text: `${m.navn} ${m.versjon}` }));
  }
  const knapp = el("button", { type: "submit",
                               text: t("ui.prognose.prognose_ny") });
  form.append(
    felt("prog-modellvalg", "ui.prognose.modell", valg),
    // DØRA NEKTER PÅ TOM HISTORIKK, og skjemaet sier det på forhånd.
    // «Null timer neste uke» fordi ingen har ført timer, er den
    // reneste formen for en prognose presentert som et faktum.
    el("p", { class: "muted",
              text: t("ui.prognose.prognose_krever_historikk") }),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.prognose.prognose_laget",
    send: (idem) => lagBemanningsprognose({
      modell_id: valg.value,
    }, idem),
  });
  const kort = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.prognose.prognose_ny") }));
  if (!gyldige.length) {
    // UTEN EN GYLDIG MODELL KAN INGEN PROGNOSE LAGES, og døra nekter.
    // Skjemaet skal si det, ikke la brukeren finne det ut av en 400.
    kort.append(el("p", { role: "alert",
      text: t("ui.prognose.ingen_gyldig_modell") }));
    return kort;
  }
  kort.append(form);
  return kort;
}


export function visPrognose(hoved, ctx) {
  const hode = () => flateHode(t("ui.prognose.tittel"),
    t("ui.prognose.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/prognose"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const prognoser = d.prognoser || [];
      const modeller = d.modeller || [];
      const funn = d.funn || [];
      const skriver = harScope(ctx, "bestilling:opprett");
      const bane = banepanel(ctx, last, kvitter);
      const lukking = lukkepanel(ctx, last, kvitter);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.prognose.sammendrag") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT.
        el("p", { class: "muted",
                  text: t("ui.prognose.hvorfor") }));

      const funnseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.prognose.funn") }));
      if (!funn.length) {
        funnseksjon.append(el("p", { class: "muted",
          text: t("ui.prognose.funn_tomt") }));
      } else {
        funnseksjon.append(funntabell(
          funn, skriver ? lukking.aapne : null));
      }

      const prognoseseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.prognose.prognoser") }));
      if (!prognoser.length) {
        prognoseseksjon.append(el("p", { class: "muted",
          text: t("ui.prognose.prognoser_tomt") }));
      } else {
        prognoseseksjon.append(prognosetabell(prognoser, bane.aapne));
      }

      const modellseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.prognose.modeller") }));
      if (!modeller.length) {
        modellseksjon.append(el("p", { role: "alert",
          text: t("ui.prognose.modeller_tomt") }));
      } else {
        modellseksjon.append(modelltabell(modeller));
      }

      // FUNNENE FØRST, SÅ PROGNOSENE. Rekkefølgen er en dom: det som
      // haster er den uken ingen har målt og den modellen som taper
      // for basislinjen — ikke listen over hvor mange prognoser vi
      // har laget.
      const deler = [oversikt, funnseksjon, lukking.node,
                     prognoseseksjon, bane.node, modellseksjon];
      if (skriver) {
        deler.push(prognoseskjema(ctx, last, kvitter, modeller),
                   modellskjema(ctx, last, kvitter),
                   avviklingsskjema(ctx, last, kvitter, modeller),
                   kravskjema(ctx, last, kvitter, s));
      }
      sett(kropp, ...deler);
    });
  return last();
}
