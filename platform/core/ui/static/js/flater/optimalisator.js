// M-36 bedriftsoptimalisator (132) — RANGERINGEN ER ET FORSLAG.
//
// FLATENS VIKTIGSTE JOBB ER Å VISE HVA HVERT FORSLAG HVILER PÅ.
//
// Vaktsetningen sier «korrelasjon presenteres ikke som årsak», og det
// er et krav til DATAMODELLEN, ikke til teksten på skjermen. Men
// flaten er der påstanden faktisk møter et menneske, så
// `grunnlagstype` står i SAMME RAD som plasseringen og tallet — ikke
// i en fotnote, ikke bak et klikk. Et forslag som er nummer én på
// grunn av en samvariasjon skal se annerledes ut enn ett som er det
// på grunn av et eksperiment.
//
// BÅNDET TEGNES ALLTID, ALDRI BARE PUNKTET. Et punktestimat uten
// usikkerhet er ikke et presist anslag — det er et upresist anslag som
// har mistet informasjonen om hvor upresist det er. Og en RANGERING av
// slike tall er en rekkefølge som later som den er sikker.
//
// PORTEFØLJESTOPPEN STANSER M-36, IKKE PORTEFØLJEN, og flaten sier det
// rett ut. Navnet lover mer enn stoppen kan holde: det eneste modulen
// lovlig kan stanse er sin egen produksjon — å stanse en annen modul
// ville vært å overstyre dens grense. Virkningen er ekte og målbar
// (ingen ny rangering blir til), men det er ikke en nødbrems for
// driften, og en flate som lot som noe annet ville løyet.
//
// «MÅL EFFEKTEN» FINNES BARE NÅR HORISONTEN ER OVER, og `kan_maales`
// kommer fra BASEN — flaten regner ikke ut selv.
//
// DET FINNES INGEN «IVERKSETT»-KNAPP, OG DET KAN IKKE FINNES. Et
// tiltak kan bli vurdert eller avvist. Utførelsen går gjennom modulen
// som eier handlingen, av et menneske, på M-41s policykontrollerte vei
// — og den veien vet ingenting om denne flaten.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  avviklOptimaliseringsmodell, FeilformetFeil, foreslaTiltak,
  hentJson,
  lagRangering, lukkOptimaliseringsfunn, nyIdempotensnokkel,
  opphevPortefoljestopp, registrerEffekt,
  registrerOptimaliseringsmodell, settOptimaliseringskrav,
  settPortefoljestopp, UautorisertFeil, vurderTiltaksforslag,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

export const GRUNNLAGSTYPER = ["korrelasjon", "eksperiment", "regel"];
export const REVERSIBILITET = ["reversibel", "delvis_reversibel",
                               "irreversibel"];
export const VURDERINGER = ["vurdert", "avvist"];

const GRUNNLAGSTEKST = {
  korrelasjon: "ui.optimalisator.grunnlag_korrelasjon",
  eksperiment: "ui.optimalisator.grunnlag_eksperiment",
  regel: "ui.optimalisator.grunnlag_regel",
};

const REVTEKST = {
  reversibel: "ui.optimalisator.rev_reversibel",
  delvis_reversibel: "ui.optimalisator.rev_delvis",
  irreversibel: "ui.optimalisator.rev_irreversibel",
};

const VURDERINGSTEKST = {
  vurdert: "ui.optimalisator.vurdering_vurdert",
  avvist: "ui.optimalisator.vurdering_avvist",
};

const FUNNTEKST = {
  rangering_uten_maaling: "ui.optimalisator.funn_uten_maaling",
  korrelasjon_alene_paa_topp: "ui.optimalisator.funn_korrelasjon",
  tiltak_uten_reversibilitet: "ui.optimalisator.funn_uten_rev",
  stopp_staar_uten_oppheving: "ui.optimalisator.funn_stopp_staar",
};


// ØRE DELES PÅ 100 KUN I VISNINGEN (husets form).
export function kroner(ore) {
  if (typeof ore !== "number") return "";
  const neg = ore < 0;
  const a = Math.abs(ore);
  const hel = String(Math.floor(a / 100));
  const des = String(a % 100).padStart(2, "0");
  const grupper = hel.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  return `${neg ? "−" : ""}${grupper},${des}`;
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
          ? t("ui.optimalisator.feil.tilstand")
          : t("ui.optimalisator.feil.generell") }));
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


// SAMMENDRAGET. STOPPEN FØRST NÅR DEN ER PÅ.
//
// En modul som er slått av skal ikke se ut som en modul uten forslag.
// Deretter det umålte: en rangering ingen har målt effekten av blir
// gradvis mer feil uten at noen oppdager det, mens den beholder
// autoriteten sin.
export function sammendrag(s) {
  const p = el("p", {});
  if (s.stopp_aktiv) {
    p.append(el("strong", { role: "alert",
      text: t("ui.optimalisator.stopp_paa") }), " ");
  }
  p.append(el("strong", {
    text: t("ui.optimalisator.umaalte_sum")
      .replace("{n}", String(s.umaalte ?? 0)) }));
  const maalt = (s.treff ?? 0) + (s.bom ?? 0);
  if (maalt > 0) {
    p.append(" ", el("strong", {
      text: t("ui.optimalisator.treffrate")
        .replace("{treff}", String(s.treff ?? 0))
        .replace("{av}", String(maalt)) }));
  } else {
    // INGEN MÅLINGER ER IKKE «100 % TREFF».
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.optimalisator.ingen_maalinger") }));
  }
  if (s.irreversible_uvurderte > 0) {
    // ET IRREVERSIBELT TILTAK INGEN HAR SETT PÅ ER DET DYRESTE Å
    // OVERSE.
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.optimalisator.irreversible_sum")
        .replace("{n}", String(s.irreversible_uvurderte)) }));
  }
  p.append(" ", el("span", {
    text: t("ui.optimalisator.tellinger")
      .replace("{rangeringer}", String(s.rangeringer ?? 0))
      .replace("{tiltak}", String(s.tiltak ?? 0))
      .replace("{uvurderte}", String(s.uvurderte_tiltak ?? 0)) }));
  // HVOR BREDT MODULEN SER. Et register uten åpne funn er lest, ikke
  // fraværende — og tallet skal si hvor mange den så i, ikke hvor
  // mange som hadde noe å melde.
  p.append(" ", el("span", { class: "muted",
    text: t("ui.optimalisator.grunnlaget")
      .replace("{funn}", String(s.apne_funn_i_huset ?? 0))
      .replace("{registre}", String(s.registre ?? 0)) }));
  if (!s.gyldige_modeller) {
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.optimalisator.ingen_gyldig_modell") }));
  }
  if (s.apne_funn > 0) {
    p.append(" ", el("strong", {
      text: t("ui.optimalisator.apne_funn_sum")
        .replace("{n}", String(s.apne_funn)) }));
  }
  if (!s.har_krav) {
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.optimalisator.krav_mangler") }));
  }
  return p;
}


// RANGERINGSTABELLEN. PLASS, TALL, BÅND OG GRUNNLAGSTYPE I SAMME RAD.
export function rangeringstabell(poster, maal) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.optimalisator.plass", "ui.optimalisator.tiltak",
                   "ui.optimalisator.forventet",
                   "ui.optimalisator.baand",
                   "ui.optimalisator.grunnlagstype",
                   "ui.optimalisator.reversibilitet",
                   "ui.optimalisator.horisont_til",
                   "ui.optimalisator.faktisk",
                   "ui.optimalisator.avvik",
                   "ui.optimalisator.traff"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  if (maal) {
    hoderad.append(el("th", { scope: "col",
                              text: t("ui.optimalisator.handling") }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const p of poster) {
    const grunnlag = el("td", {});
    const gn = GRUNNLAGSTEKST[p.grunnlagstype];
    const gt = gn ? t(gn) : String(p.grunnlagstype);
    // KORRELASJON MERKES. Modellen får rangere på den, men et forslag
    // som er der på grunn av en samvariasjon skal ikke se ut som et
    // som er der på grunn av et eksperiment.
    if (p.grunnlagstype === "korrelasjon") {
      grunnlag.append(el("strong", { role: "alert", text: gt }));
    } else {
      grunnlag.append(el("span", { text: gt }));
    }
    const rev = el("td", {});
    const rn = REVTEKST[p.reversibilitet];
    const rt = rn ? t(rn) : String(p.reversibilitet);
    if (p.reversibilitet === "irreversibel") {
      rev.append(el("strong", { text: rt }));
    } else {
      rev.append(el("span", { text: rt }));
    }
    const rad = el("tr", {},
      el("td", { text: String(p.plass) }),
      el("td", { text: p.beskrivelse }),
      el("td", { text: kroner(p.forventet_effekt_ore) }),
      // BÅNDET ER ALLTID MED.
      el("td", { text: t("ui.optimalisator.baand_verdi")
        .replace("{ned}", kroner(p.nedre_effekt_ore))
        .replace("{opp}", kroner(p.ovre_effekt_ore)) }),
      grunnlag, rev,
      el("td", { text: p.ukeslutt }),
      el("td", { text: typeof p.faktisk_effekt_ore === "number"
        ? kroner(p.faktisk_effekt_ore) : "–" }),
      el("td", { text: typeof p.avvik_ore === "number"
        ? kroner(p.avvik_ore) : "–" }),
      el("td", { text: p.innenfor_intervall === null
        || p.innenfor_intervall === undefined
        ? "–"
        : t(p.innenfor_intervall
            ? "ui.optimalisator.traff_ja"
            : "ui.optimalisator.traff_nei") }));
    if (maal) {
      const celle = el("td", {});
      // `kan_maales` KOMMER FRA BASEN.
      if (p.kan_maales) {
        celle.append(knappMed(t("ui.optimalisator.maal_effekten"),
                              () => maal(p)));
      }
      rad.append(celle);
    }
    kropp.append(rad);
  }
  tabell.append(kropp);
  return tabell;
}


export function rangeringstabellen(rangeringer, aapne) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.optimalisator.laget",
                   "ui.optimalisator.modellversjon",
                   "ui.optimalisator.horisont",
                   "ui.optimalisator.grunnlag",
                   "ui.optimalisator.antall_poster",
                   "ui.optimalisator.maalt",
                   "ui.optimalisator.laget_av",
                   "ui.optimalisator.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const r of rangeringer) {
    const handling = el("td", {});
    handling.append(knappMed(t("ui.optimalisator.vis_rangering"),
                             () => aapne(r)));
    kropp.append(el("tr", {},
      el("td", { text: r.laget_dato }),
      el("td", { text: r.modellversjon }),
      el("td", { text: String(r.horisont_uker) }),
      // HVOR BREDT DEN SÅ, ikke bare hva den fant.
      el("td", { text: t("ui.optimalisator.grunnlag_verdi")
        .replace("{funn}", String(r.grunnlag_apne_funn))
        .replace("{registre}", String(r.grunnlag_registre)) }),
      el("td", { text: String(r.antall_poster) }),
      el("td", { text: String(r.antall_maalt) }),
      el("td", { text: r.laget_av }),
      handling));
  }
  tabell.append(kropp);
  return tabell;
}


export function tiltakstabell(tiltak, vurder) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.optimalisator.tiltak",
                   "ui.optimalisator.grunnlagstype",
                   "ui.optimalisator.grunnlag_tekst",
                   "ui.optimalisator.reversibilitet",
                   "ui.optimalisator.kilde",
                   "ui.optimalisator.anslag",
                   "ui.optimalisator.status",
                   "ui.optimalisator.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const x of tiltak) {
    const rev = el("td", {});
    const rn = REVTEKST[x.reversibilitet];
    const rt = rn ? t(rn) : String(x.reversibilitet);
    if (x.reversibilitet === "irreversibel") {
      rev.append(el("strong", { text: rt }));
    } else {
      rev.append(el("span", { text: rt }));
    }
    const handling = el("td", {});
    if (vurder && x.status === "foreslatt") {
      handling.append(knappMed(t("ui.optimalisator.vurder"),
                               () => vurder(x)));
    }
    const gn = GRUNNLAGSTEKST[x.grunnlagstype];
    kropp.append(el("tr", {},
      el("td", { text: x.beskrivelse }),
      el("td", { text: gn ? t(gn) : String(x.grunnlagstype) }),
      el("td", { text: x.grunnlag }),
      rev,
      // KILDEN, slik at forslaget kan spores til målingen som utløste
      // det.
      el("td", { text: t("ui.optimalisator.kilde_verdi")
        .replace("{modul}", x.kilde_modul)
        .replace("{funntype}", x.kilde_funntype) }),
      el("td", { text: kroner(x.anslag_effekt_ore) }),
      el("td", { text: x.status === "foreslatt"
        ? t("ui.optimalisator.status_foreslatt")
        : (VURDERINGSTEKST[x.status]
           ? t(VURDERINGSTEKST[x.status]) : String(x.status)) }),
      handling));
  }
  tabell.append(kropp);
  return tabell;
}


export function modelltabell(modeller) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.optimalisator.modell",
                   "ui.optimalisator.versjon",
                   "ui.optimalisator.metode",
                   "ui.optimalisator.baselinje",
                   "ui.optimalisator.usikkerhet",
                   "ui.optimalisator.gyldig_fra",
                   "ui.optimalisator.gyldig_til",
                   "ui.optimalisator.gjelder",
                   "ui.optimalisator.antall_rangeringer"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const m of modeller) {
    kropp.append(el("tr", {},
      el("td", { text: m.navn }),
      el("td", { text: m.versjon }),
      el("td", { text: m.metode }),
      el("td", { text: m.baselinje }),
      el("td", { text: t("ui.optimalisator.usikkerhet_verdi")
        .replace("{bp}", String(m.usikkerhet_bp)) }),
      el("td", { text: m.gyldig_fra }),
      el("td", { text: m.gyldig_til || "–" }),
      el("td", { text: t(m.gjelder ? "ui.optimalisator.gjelder_ja"
                                   : "ui.optimalisator.avviklet") }),
      el("td", { text: String(m.antall_rangeringer) })));
  }
  tabell.append(kropp);
  return tabell;
}


export function funntabell(funn, lukk) {
  const tabell = el("table", { class: "tabell" });
  const hoderad = el("tr", {});
  for (const h of ["ui.optimalisator.funntype",
                   "ui.optimalisator.referanse",
                   "ui.optimalisator.detaljer",
                   "ui.optimalisator.over_grense",
                   "ui.optimalisator.status",
                   "ui.optimalisator.handling"]) {
    hoderad.append(el("th", { scope: "col", text: t(h) }));
  }
  tabell.append(el("thead", {}, hoderad));
  const kropp = el("tbody", {});
  for (const f of funn) {
    const handling = el("td", {});
    // `kan_lukkes` KOMMER FRA BASEN (124s form).
    if (lukk && f.kan_lukkes) {
      handling.append(knappMed(t("ui.optimalisator.lukk"),
                               () => lukk(f)));
    } else if (f.apen && !f.kan_lukkes) {
      // `!f.kan_lukkes` OG IKKE BARE `f.apen` (CodeRabbit).
      //
      // Betingelsen sto på `lukk`-tilbakekallet, som er `null` for en
      // LESER uten skrivescope. Da fikk leseren «lukkes av sveipen»
      // på HVERT åpent funn — også de et menneske faktisk kan lukke.
      // Det er en påstand om hvem som eier funnet, og den var feil.
      handling.append(el("span", { class: "muted",
        text: t("ui.optimalisator.lukkes_av_sveipen") }));
    }
    const nokkel = FUNNTEKST[f.funntype];
    kropp.append(el("tr", {},
      el("td", { text: nokkel ? t(nokkel) : String(f.funntype) }),
      el("td", { text: f.referanse }),
      el("td", { text: f.detaljer }),
      el("td", { text: String(f.over_grense ?? 0) }),
      el("td", { text: f.apen
        ? t("ui.optimalisator.apen")
        : t("ui.optimalisator.lukket_av")
            .replace("{av}", f.lukket_av || "–") }),
      handling));
  }
  tabell.append(kropp);
  return tabell;
}


export function rangeringspanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  let aktiv = null;

  function effektpanel(post) {
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    const form = el("form", { class: "skjema" });
    // `required` OG en eksplisitt sjekk (128s CodeRabbit-funn).
    //
    // `Number("")` er `0`. Et tomt felt sendt inn ville registrert
    // NULL EFFEKT som resultatet — og en måling RETTES IKKE: den står
    // som den ble avgitt. Et uhell her er permanent.
    const faktisk = el("input", { type: "number", id: "opti-faktisk",
                                  name: "faktisk_effekt_ore",
                                  step: "1", required: true });
    const knapp = el("button", { type: "submit",
      text: t("ui.optimalisator.maal_effekten") });
    form.append(
      el("p", { class: "muted",
                text: t("ui.optimalisator.maal_forklaring") }),
      felt("opti-faktisk", "ui.optimalisator.faktisk", faktisk,
           "ui.optimalisator.ore_hjelp"),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.optimalisator.maalt_ok",
      send: (idem) => {
        // Den programmatiske veien går utenom `required`.
        if (faktisk.value.trim() === "") {
          throw new FeilformetFeil(400, "faktisk_mangler");
        }
        return registrerEffekt(aktiv.rangering_id, {
          plass: post.plass,
          faktisk_effekt_ore: Number(faktisk.value),
        }, idem);
      },
    });
    return form;
  }

  async function aapne(r) {
    aktiv = r;
    // MÅLET FANGES FØR VENTINGEN (128s CodeRabbit-funn). To raske
    // klikk gir to hentinger, og den TREGESTE kunne svart sist og
    // tegnet feil rangering under overskriften til den nyeste.
    const maal = r.rangering_id;
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.optimalisator.rangeringen") }),
      el("p", { class: "muted",
                text: t("ui.optimalisator.rangering_om")
                  .replace("{dato}", r.laget_dato)
                  .replace("{versjon}", r.modellversjon) }));
    let d;
    try {
      d = await hentJson(`/v1/optimalisator/rangering/${maal}`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (aktiv.rangering_id !== maal) return;
      node.append(el("p", { role: "alert",
                            text: t("ui.optimalisator.feil") }));
      return;
    }
    if (aktiv.rangering_id !== maal) return;
    const poster = d.poster || [];
    if (!poster.length) {
      node.append(el("p", { class: "muted",
        text: t("ui.optimalisator.rangering_tom") }));
      return;
    }
    const skriver = harScope(ctx, "bestilling:opprett");
    const plass = el("div", {});
    node.append(rangeringstabell(poster, skriver
      ? (p) => sett(plass, effektpanel(p)) : null), plass);
  }

  return { node, aapne };
}


export function vurderingspanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  let aktiv = null;

  function aapne(x) {
    aktiv = x;
    node.hidden = false;
    const utfall = el("p", { class: "muted", "aria-live": "polite" });
    const form = el("form", { class: "skjema" });
    const status = velger("opti-vurdering", VURDERINGER,
                          VURDERINGSTEKST);
    const notat = el("textarea", { id: "opti-vurderingsnotat",
                                   name: "vurderingsnotat",
                                   rows: "3", required: true });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.optimalisator.vurder") });
    form.append(
      felt("opti-vurdering", "ui.optimalisator.status", status),
      felt("opti-vurderingsnotat", "ui.optimalisator.notat", notat,
           "ui.optimalisator.notat_hjelp"),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.optimalisator.vurdert_ok",
      send: (idem) => vurderTiltaksforslag(aktiv.tiltak_id, {
        status: status.value, vurderingsnotat: notat.value,
      }, idem),
    });
    sett(node, el("h2", { text: t("ui.optimalisator.vurder") }),
      // DET FINNES INGEN TREDJE VERDI, og panelet sier hvorfor.
      el("p", { class: "muted",
                text: t("ui.optimalisator.vurder_om") }),
      form);
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
    const begrunnelse = el("textarea", { id: "opti-lukkegrunn",
                                         name: "begrunnelse",
                                         rows: "3", required: true });
    const knapp = el("button", { type: "submit",
                                 text: t("ui.optimalisator.lukk") });
    form.append(
      felt("opti-lukkegrunn", "ui.optimalisator.begrunnelse",
           begrunnelse, "ui.optimalisator.begrunnelse_hjelp"),
      knapp, utfall);
    skjemaramme(ctx, last, {
      skjema: form, knapp, utfall, kvitter,
      okNokkel: "ui.optimalisator.lukket_ok",
      send: (idem) => lukkOptimaliseringsfunn(
        aktiv.funn_id, { begrunnelse: begrunnelse.value }, idem),
    });
    sett(node, el("h2", { text: t("ui.optimalisator.lukk_funn") }),
      el("p", { class: "muted",
                text: t("ui.optimalisator.lukk_om")
                  .replace("{type}", f.funntype) }),
      form);
  }

  return { node, aapne };
}


// STOPPANELET. Det sier hva stoppen GJØR, ikke hva navnet lover.
export function stoppanel(ctx, last, kvitter, s, stopp) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const kort = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.optimalisator.stopp") }),
    // NAVNET LOVER MER ENN STOPPEN KAN HOLDE, og det står her.
    el("p", { class: "muted",
              text: t("ui.optimalisator.stopp_forklaring") }));
  const aktiv = (stopp || []).find((x) => x.aktiv);
  const form = el("form", { class: "skjema" });
  const begrunnelse = el("textarea", { id: "opti-stoppgrunn",
                                       name: "begrunnelse",
                                       rows: "2", required: true });
  const knapp = el("button", { type: "submit",
    text: t(aktiv ? "ui.optimalisator.opphev"
                  : "ui.optimalisator.stopp_sett") });
  form.append(
    felt("opti-stoppgrunn", "ui.optimalisator.begrunnelse",
         begrunnelse, "ui.optimalisator.stoppgrunn_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: aktiv ? "ui.optimalisator.opphevet_ok"
                    : "ui.optimalisator.stoppet_ok",
    send: (idem) => (aktiv
      ? opphevPortefoljestopp(aktiv.stopp_id,
                              { begrunnelse: begrunnelse.value }, idem)
      : settPortefoljestopp({ begrunnelse: begrunnelse.value }, idem)),
  });
  if (aktiv) {
    kort.append(el("p", { role: "alert",
      text: t("ui.optimalisator.stopp_staar")
        .replace("{av}", aktiv.satt_av)
        .replace("{grunn}", aktiv.begrunnelse) }));
  }
  kort.append(form);
  return kort;
}


export function kravskjema(ctx, last, kvitter, s) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  // ALLE TRE GRENSENE FORHÅNDSUTFYLLES FRA BASEN (123s lærdom).
  const horisont = el("input", { type: "number", id: "opti-horisont",
                                 name: "horisont_uker", min: "1",
                                 max: "104", required: true,
                                 value: String(s.horisont_uker ?? 12) });
  const frist = el("input", { type: "number", id: "opti-frist",
                              name: "maalefrist_dogn", min: "1",
                              max: "180", required: true,
                              value: String(s.maalefrist_dogn ?? 14) });
  const maks = el("input", { type: "number", id: "opti-maks",
                             name: "maks_i_rangering", min: "1",
                             max: "100", required: true,
                             value: String(s.maks_i_rangering ?? 10) });
  const knapp = el("button", { type: "submit",
    text: t("ui.optimalisator.krav_lagre") });
  form.append(
    felt("opti-horisont", "ui.optimalisator.horisont", horisont,
         "ui.optimalisator.horisont_hjelp"),
    felt("opti-frist", "ui.optimalisator.maalefrist", frist,
         "ui.optimalisator.maalefrist_hjelp"),
    felt("opti-maks", "ui.optimalisator.maks", maks,
         "ui.optimalisator.maks_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.optimalisator.krav_lagret",
    send: (idem) => settOptimaliseringskrav({
      horisont_uker: Number(horisont.value),
      maalefrist_dogn: Number(frist.value),
      maks_i_rangering: Number(maks.value),
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.optimalisator.krav") }), form);
}


export function modellskjema(ctx, last, kvitter) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const navn = el("input", { type: "text", id: "opti-modellnavn",
                             name: "navn", required: true });
  const versjon = el("input", { type: "text", id: "opti-modellversjon",
                                name: "versjon", required: true });
  const metode = el("textarea", { id: "opti-metode", name: "metode",
                                  rows: "3", required: true });
  const baselinje = el("input", { type: "text", id: "opti-baselinje",
                                  name: "baselinje", required: true });
  const usikkerhet = el("input", { type: "number",
                                   id: "opti-usikkerhet",
                                   name: "usikkerhet_bp", min: "1",
                                   max: "10000", required: true,
                                   value: "2000" });
  const fra = el("input", { type: "date", id: "opti-gyldigfra",
                            name: "gyldig_fra", required: true });
  const til = el("input", { type: "date", id: "opti-gyldigtil",
                            name: "gyldig_til" });
  const knapp = el("button", { type: "submit",
    text: t("ui.optimalisator.modell_ny") });
  form.append(
    felt("opti-modellnavn", "ui.optimalisator.modell", navn),
    felt("opti-modellversjon", "ui.optimalisator.versjon", versjon),
    felt("opti-metode", "ui.optimalisator.metode", metode,
         "ui.optimalisator.metode_hjelp"),
    felt("opti-baselinje", "ui.optimalisator.baselinje", baselinje,
         "ui.optimalisator.baselinje_hjelp"),
    felt("opti-usikkerhet", "ui.optimalisator.usikkerhet", usikkerhet,
         "ui.optimalisator.usikkerhet_hjelp"),
    felt("opti-gyldigfra", "ui.optimalisator.gyldig_fra", fra),
    felt("opti-gyldigtil", "ui.optimalisator.gyldig_til", til,
         "ui.optimalisator.gyldig_til_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.optimalisator.modell_lagret",
    send: (idem) => registrerOptimaliseringsmodell({
      navn: navn.value, versjon: versjon.value,
      metode: metode.value, baselinje: baselinje.value,
      usikkerhet_bp: Number(usikkerhet.value),
      gyldig_fra: fra.value,
      gyldig_til: til.value === "" ? null : til.value,
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.optimalisator.modell_ny") }), form);
}


export function avviklingsskjema(ctx, last, kvitter, modeller) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const gyldige = (modeller || []).filter((m) => m.gjelder);
  const valg = el("select", { id: "opti-avviklvalg",
                              name: "modell_id" });
  for (const m of gyldige) {
    valg.append(el("option", { value: m.modell_id,
                               text: `${m.navn} ${m.versjon}` }));
  }
  const til = el("input", { type: "date", id: "opti-avvikltil",
                            name: "gyldig_til", required: true });
  const knapp = el("button", { type: "submit",
                               text: t("ui.optimalisator.avvikl") });
  form.append(
    felt("opti-avviklvalg", "ui.optimalisator.modell", valg),
    felt("opti-avvikltil", "ui.optimalisator.gyldig_til", til,
         "ui.optimalisator.avvikl_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.optimalisator.avviklet_ok",
    send: (idem) => avviklOptimaliseringsmodell(
      valg.value, { gyldig_til: til.value }, idem),
  });
  const kort = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.optimalisator.avvikl") }));
  if (!gyldige.length) {
    kort.append(el("p", { class: "muted",
      text: t("ui.optimalisator.ingen_aa_avvikle") }));
    return kort;
  }
  kort.append(form);
  return kort;
}


export function tiltaksskjema(ctx, last, kvitter) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const beskrivelse = el("textarea", { id: "opti-beskrivelse",
                                       name: "beskrivelse", rows: "2",
                                       required: true });
  const grunnlagstype = velger("opti-grunnlagstype", GRUNNLAGSTYPER,
                               GRUNNLAGSTEKST);
  const grunnlag = el("textarea", { id: "opti-grunnlag",
                                    name: "grunnlag", rows: "2",
                                    required: true });
  const rev = velger("opti-rev", REVERSIBILITET, REVTEKST);
  const modul = el("input", { type: "text", id: "opti-kildemodul",
                              name: "kilde_modul", required: true });
  const funntype = el("input", { type: "text", id: "opti-kildefunn",
                                 name: "kilde_funntype",
                                 required: true });
  const anslag = el("input", { type: "number", id: "opti-anslag",
                               name: "anslag_effekt_ore", step: "1",
                               required: true });
  const knapp = el("button", { type: "submit",
                               text: t("ui.optimalisator.tiltak_ny") });
  form.append(
    felt("opti-beskrivelse", "ui.optimalisator.tiltak", beskrivelse),
    // GRUNNLAGSTYPEN ER OBLIGATORISK, og hjelpeteksten sier hvorfor.
    felt("opti-grunnlagstype", "ui.optimalisator.grunnlagstype",
         grunnlagstype, "ui.optimalisator.grunnlagstype_hjelp"),
    felt("opti-grunnlag", "ui.optimalisator.grunnlag_tekst", grunnlag),
    felt("opti-rev", "ui.optimalisator.reversibilitet", rev,
         "ui.optimalisator.rev_hjelp"),
    felt("opti-kildemodul", "ui.optimalisator.kilde_modul", modul,
         "ui.optimalisator.kilde_hjelp"),
    felt("opti-kildefunn", "ui.optimalisator.kilde_funntype",
         funntype),
    felt("opti-anslag", "ui.optimalisator.anslag", anslag,
         "ui.optimalisator.anslag_hjelp"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.optimalisator.tiltak_lagret",
    send: (idem) => {
      if (anslag.value.trim() === "") {
        throw new FeilformetFeil(400, "anslag_mangler");
      }
      return foreslaTiltak({
        beskrivelse: beskrivelse.value,
        grunnlagstype: grunnlagstype.value,
        grunnlag: grunnlag.value,
        reversibilitet: rev.value,
        kilde_modul: modul.value,
        kilde_funntype: funntype.value,
        anslag_effekt_ore: Number(anslag.value),
      }, idem);
    },
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.optimalisator.tiltak_ny") }), form);
}


export function rangeringsskjema(ctx, last, kvitter, modeller, s) {
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const form = el("form", { class: "skjema" });
  const gyldige = (modeller || []).filter((m) => m.gjelder);
  const valg = el("select", { id: "opti-modellvalg",
                              name: "modell_id" });
  for (const m of gyldige) {
    valg.append(el("option", { value: m.modell_id,
                               text: `${m.navn} ${m.versjon}` }));
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.optimalisator.rangering_ny") });
  form.append(
    felt("opti-modellvalg", "ui.optimalisator.modell", valg),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema: form, knapp, utfall, kvitter,
    okNokkel: "ui.optimalisator.rangering_laget",
    send: (idem) => lagRangering({ modell_id: valg.value }, idem),
  });
  const kort = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.optimalisator.rangering_ny") }));
  if (s.stopp_aktiv) {
    // DØRA NEKTER MED AKTIV STOPP, og skjemaet sier det i stedet for
    // å la brukeren finne det ut av en 400.
    kort.append(el("p", { role: "alert",
      text: t("ui.optimalisator.stopp_hindrer") }));
    return kort;
  }
  if (!gyldige.length) {
    kort.append(el("p", { role: "alert",
      text: t("ui.optimalisator.ingen_gyldig_modell") }));
    return kort;
  }
  kort.append(form);
  return kort;
}


export function visOptimalisator(hoved, ctx) {
  const hode = () => flateHode(t("ui.optimalisator.tittel"),
    t("ui.optimalisator.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/optimalisator"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const rangeringer = d.rangeringer || [];
      const tiltak = d.tiltak || [];
      const modeller = d.modeller || [];
      const stopp = d.stopp || [];
      const funn = d.funn || [];
      const skriver = harScope(ctx, "bestilling:opprett");
      const rang = rangeringspanel(ctx, last, kvitter);
      const vurdering = vurderingspanel(ctx, last, kvitter);
      const lukking = lukkepanel(ctx, last, kvitter);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.optimalisator.sammendrag") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT.
        el("p", { class: "muted",
                  text: t("ui.optimalisator.hvorfor") }));

      const funnseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.optimalisator.funn") }));
      if (!funn.length) {
        funnseksjon.append(el("p", { class: "muted",
          text: t("ui.optimalisator.funn_tomt") }));
      } else {
        funnseksjon.append(funntabell(
          funn, skriver ? lukking.aapne : null));
      }

      const rangeringsseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.optimalisator.rangeringer") }));
      if (!rangeringer.length) {
        rangeringsseksjon.append(el("p", { class: "muted",
          text: t("ui.optimalisator.rangeringer_tomt") }));
      } else {
        rangeringsseksjon.append(
          rangeringstabellen(rangeringer, rang.aapne));
      }

      const tiltaksseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.optimalisator.tiltak_liste") }));
      if (!tiltak.length) {
        tiltaksseksjon.append(el("p", { class: "muted",
          text: t("ui.optimalisator.tiltak_tomt") }));
      } else {
        tiltaksseksjon.append(tiltakstabell(
          tiltak, skriver ? vurdering.aapne : null));
      }

      const modellseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.optimalisator.modeller") }));
      if (!modeller.length) {
        modellseksjon.append(el("p", { role: "alert",
          text: t("ui.optimalisator.modeller_tomt") }));
      } else {
        modellseksjon.append(modelltabell(modeller));
      }

      // FUNNENE FØRST, SÅ RANGERINGENE. Det som haster er den
      // effekten ingen har målt og den stoppen som blir stående —
      // ikke listen over hvor mange rangeringer vi har laget.
      const deler = [oversikt, funnseksjon, lukking.node,
                     rangeringsseksjon, rang.node, tiltaksseksjon,
                     vurdering.node, modellseksjon];
      if (skriver) {
        deler.push(stoppanel(ctx, last, kvitter, s, stopp),
                   rangeringsskjema(ctx, last, kvitter, modeller, s),
                   tiltaksskjema(ctx, last, kvitter),
                   modellskjema(ctx, last, kvitter),
                   avviklingsskjema(ctx, last, kvitter, modeller),
                   kravskjema(ctx, last, kvitter, s));
      }
      sett(kropp, ...deler);
    });
  return last();
}
