// Prosjekt- og kontraktagenten (M-25 v1) — PROSJEKTREGISTERET.
//
// FLATENS VIKTIGSTE JOBB er å vise TO TALL SOM IKKE ER DET SAMME:
// FORBRUKET mot budsjettet (hva prosjektet koster oss) og
// BETALINGSPLANEN (hva kontrakten lar oss kreve). Et register som la dem
// i samme kolonne ville gjort «går prosjektet i pluss» til et spørsmål
// ingen kunne svare på.
//
// DET FINNES INGEN «FAKTURER»-KNAPP, og fraværet er dommen: policyen vi
// sender ut navngir modulen som verifikatoren `v_prosjekt`, betrodd for
// `milepael_dokumentert`, og bruker den attestasjonen til å la
// `ordre.bekreft_og_fakturer` gå automatisk. En automatisk faktura på en
// milepæl ingen har dokumentert er penger krevd for arbeid som kanskje
// ikke er gjort.
//
// DOKUMENTASJONEN ER OBLIGATORISK når en milepæl merkes nådd, og
// feltet er `required` her av samme grunn som CHECK-en er det i basen.
//
// FLATEN VISER, DEN REGNER IKKE. `forbruk_ore`, `klar_ore`,
// `dogn_til_slutt` og `dogn_over_frist` er regnet i BASEN, i samme skann
// som raden (107s lesedører).
//
// BELØP FORMATERES I HELTALLSARITMETIKK, og TIMER I HELE MINUTTER: «1,5
// time» er 90 minutter og ikke 1.4999999999999998.
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, avsluttProsjekt, hentJson, naaMilepael,
  nyIdempotensnokkel, registrerArbeid, registrerProsjekt,
  settBetalingsplan, settProsjektterskler,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// FUNNTYPE → MERKETEKST. Lukket tabell, ikke strengbygging.
const MERKE = {
  milepael_over_frist: "ui.prosjekt.merke_milepael",
  budsjett_overskredet: "ui.prosjekt.merke_budsjett",
  ingen_arbeid_registrert: "ui.prosjekt.merke_stille",
  betalingsplan_mangler: "ui.prosjekt.merke_uten_plan",
  ingen_terskel: "ui.prosjekt.merke_uten_terskel",
};

// BELØP I HELTALLSARITMETIKK, aldri via `/100`.
export function belopTekst(ore) {
  if (typeof ore !== "number" || !Number.isInteger(ore)) return "—";
  const neg = ore < 0;
  const a = Math.abs(ore);
  return `${neg ? "-" : ""}${Math.trunc(a / 100)},`
    + `${String(a % 100).padStart(2, "0")}`;
}

// MINUTTER → TIMER OG MINUTTER, i heltallsaritmetikk. «7,5 time» som
// flyttall er 7.499999999999999 på veien tilbake fra 450 minutter, og et
// timeregnskap tåler ikke et tall som nesten stemmer.
export function timeTekst(minutter) {
  if (typeof minutter !== "number" || !Number.isInteger(minutter)) {
    return "—";
  }
  const neg = minutter < 0;
  const a = Math.abs(minutter);
  return t("ui.prosjekt.timer")
    .replace("{timer}", `${neg ? "-" : ""}${Math.trunc(a / 60)}`)
    .replace("{minutter}", String(a % 60).padStart(2, "0"));
}

// Sluttkolonnens ORD. ENTALL HAR SIN EGEN NØKKEL.
export function sluttTekst(dogn) {
  if (typeof dogn !== "number") return "—";
  if (dogn < 0) {
    const n = Math.abs(dogn);
    return n === 1
      ? t("ui.prosjekt.over_ett_dogn")
      : t("ui.prosjekt.over_for").replace("{dogn}", String(n));
  }
  if (dogn === 0) return t("ui.prosjekt.slutter_i_dag");
  return dogn === 1
    ? t("ui.prosjekt.om_ett_dogn")
    : t("ui.prosjekt.om_dogn").replace("{dogn}", String(dogn));
}

// FORBRUKET SOM ORD, mot budsjettet. «0 av 0» er ingen målt kostnad —
// det er et prosjekt ingen har ført timer på, og de to er forskjellige
// tilstander et menneske handler ulikt på.
export function forbrukTekst(p) {
  if (!p.budsjett_ore) return t("ui.prosjekt.uten_budsjett");
  return t("ui.prosjekt.forbruk_av")
    .replace("{forbruk}", belopTekst(p.forbruk_ore))
    .replace("{budsjett}", belopTekst(p.budsjett_ore));
}

// BETALINGSPLANEN SOM ORD. Dette er DEN ANDRE størrelsen, og setningen
// sier eksplisitt at den er noe annet enn forbruket.
export function planTekst(p) {
  if (!p.milepaeler) return t("ui.prosjekt.uten_plan");
  return t("ui.prosjekt.klar_av")
    .replace("{naadde}", String(p.naadde))
    .replace("{milepaeler}", String(p.milepaeler))
    .replace("{klar}", belopTekst(p.klar_ore));
}

// KRONER INN, ØRE UT. `Math.round` på produktet er den ENESTE veien.
export function tilOre(verdi) {
  const n = Number(verdi);
  if (!Number.isFinite(n)) return null;
  return Math.round(n * 100);
}

// …og den andre veien, uten divisjon.
export function oreTilFelt(ore) {
  if (typeof ore !== "number" || !Number.isInteger(ore)) return "";
  const neg = ore < 0;
  const a = Math.abs(ore);
  return `${neg ? "-" : ""}${Math.trunc(a / 100)}.`
    + `${String(a % 100).padStart(2, "0")}`;
}

// TIMER INN, MINUTTER UT. `Math.round` av produktet: 1,5 time er 90
// minutter, ikke 89.99999999999999.
export function tilMinutter(verdi) {
  const n = Number(verdi);
  if (!Number.isFinite(n)) return null;
  return Math.round(n * 60);
}

// BETALINGSPLANEN SKRIVES SOM LINJER, ikke som JSON. Et JSON-felt i en
// flate er et felt bare den som skrev API-et kan fylle ut — og
// betalingsplanen er nettopp det kontrakten sier.
//
// Formen er `navn | dato | beløp i kroner`. Parseren er EKSPORTERT for
// at porten skal måle den uten å tegne en skjerm.
export function parsePlanlinjer(tekst) {
  const ut = [];
  for (const raa of String(tekst || "").split("\n")) {
    const linje = raa.trim();
    if (!linje) continue;
    const d = linje.split("|").map((x) => x.trim());
    if (d.length !== 3) return null;
    if (!d[0]) return null;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(d[1])) return null;
    const belop = tilOre(d[2]);
    if (belop === null || belop < 0) return null;
    ut.push({ navn: d[0], planlagt_dato: d[1], belop_ore: belop });
  }
  return ut.length ? ut : null;
}

function prosjektrad(p, ctx, apneDetalj) {
  const rad = el("tr", {});
  // KUNDEN NAVNGIR raden.
  rad.append(el("th", { scope: "row", class: "celle-tekst",
                        text: p.kunde_ref }));
  rad.append(el("td", { class: "celle-tekst", text: p.navn }));
  // DE TO TALLENE STÅR I HVER SIN KOLONNE, og de heter forskjellige
  // ting: forbruk mot budsjett er hva prosjektet KOSTER, betalingsplan
  // er hva vi kan KREVE.
  rad.append(el("td", {}, el("span", { text: forbrukTekst(p) })));
  rad.append(el("td", {}, el("span", { text: planTekst(p) })));
  rad.append(el("td", { class: "celle-tall",
                        text: timeTekst(p.minutter) }));

  const sluttcelle = el("td", {},
    el("span", { text: sluttTekst(p.dogn_til_slutt) }));
  for (const funn of p.apne_funn || []) {
    if (!MERKE[funn]) continue;
    // MERKET ER TEKST (WCAG 1.4.1).
    sluttcelle.append(" ", el("strong", { class: "merke",
                                          text: t(MERKE[funn]) }));
  }
  rad.append(sluttcelle);
  rad.append(el("td", { text: t(`ui.prosjekt.status.${p.status}`) }));

  const handling = el("td", {});
  const knapp = el("button", { type: "button",
    text: t("ui.prosjekt.knapp.apne") });
  knapp.addEventListener("click", () => apneDetalj(p));
  handling.append(knapp);
  rad.append(handling);
  return rad;
}

function prosjektTabell(prosjekter, ctx, apneDetalj) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.prosjekt.liste.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.prosjekt.kolonne.kunde") }),
    el("th", { scope: "col", text: t("ui.prosjekt.kolonne.navn") }),
    el("th", { scope: "col", text: t("ui.prosjekt.kolonne.forbruk") }),
    el("th", { scope: "col", text: t("ui.prosjekt.kolonne.plan") }),
    el("th", { scope: "col", text: t("ui.prosjekt.kolonne.timer") }),
    el("th", { scope: "col", text: t("ui.prosjekt.kolonne.slutt") }),
    el("th", { scope: "col", text: t("ui.prosjekt.kolonne.status") }),
    el("th", { scope: "col", text: t("ui.prosjekt.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const p of prosjekter) {
    tbody.append(prosjektrad(p, ctx, apneDetalj));
  }
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function terskelTabell(terskler) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.prosjekt.terskel.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.prosjekt.kolonne.terskel") }),
    el("th", { scope: "col", text: t("ui.prosjekt.kolonne.verdi") }))));
  const tbody = el("tbody");
  const linjer = [
    ["ui.prosjekt.terskel.budsjett",
     t("ui.prosjekt.promille")
       .replace("{promille}", String(terskler.budsjettvarsel_promille))],
    ["ui.prosjekt.terskel.milepaelfrist",
     t("ui.prosjekt.dogn")
       .replace("{dogn}", String(terskler.milepael_frist_dogn))],
    ["ui.prosjekt.terskel.stillhet",
     t("ui.prosjekt.dogn").replace("{dogn}",
                                   String(terskler.stillhet_dogn))],
  ];
  for (const [nokkel, verdi] of linjer) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: t(nokkel) }),
      el("td", { class: "celle-tall", text: verdi })));
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
  // Én nøkkel per intensjon (PR-014 R1): nullstilles ved endring og ved
  // 4xx — et avvist forsøk har FORBRUKT nøkkelen.
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
          ? t("ui.prosjekt.feil.tilstand")
          : t("ui.prosjekt.feil.generell") }));
      return;
    }
    idem = null;
    knapp.disabled = false;
    if (tilbakestill) tilbakestill();
    meldLive(t(okNokkel));
    // KVITTERINGEN SKAL OVERLEVE TEGNINGEN (klynge 3-rettingen). FEILEN
    // blir stående i skjemaet: den veien tegner ikke om.
    kvitter(t(okNokkel));
    await last();
  });
}

// DETALJPANELET: milepælene, arbeidet, og de fire handlingene.
function detaljpanel(ctx, last, kvitter, settApen) {
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;

  const merkelinje = el("p", { class: "muted" });
  const milepaelboks = el("div", {});
  const arbeidsboks = el("div", {});

  // --- betalingsplan ---
  const pSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const pLinjer = el("textarea", { id: "pr-plan-linjer",
    name: "milepaeler", required: true, rows: "5" });
  const pKnapp = el("button", { type: "submit",
    text: t("ui.prosjekt.knapp.lagre_plan") });
  pSkjema.append(
    felt("pr-plan-linjer", "ui.prosjekt.skjema.plan_linjer", pLinjer,
         "ui.prosjekt.skjema.plan_hjelp"),
    el("div", { class: "skjema-bunn" }, pKnapp));
  skjemaramme(ctx, last, {
    skjema: pSkjema, knapp: pKnapp, utfall, kvitter,
    okNokkel: "ui.prosjekt.skjema.plan_ok",
    send: (idem) => {
      const milepaeler = parsePlanlinjer(pLinjer.value);
      if (!milepaeler) {
        // FORMATFEILEN FANGES HER, med en setning om hva som mangler.
        const feil = new Error("format");
        feil.status = 400;
        throw feil;
      }
      return settBetalingsplan(gjeldende.prosjekt_id, milepaeler, idem);
    },
    tilbakestill: () => { pLinjer.value = ""; },
  });
  pSkjema.addEventListener("submit", () => {
    if (pLinjer.value && !parsePlanlinjer(pLinjer.value)) {
      sett(utfall, el("span", { role: "alert",
        text: t("ui.prosjekt.skjema.plan_feil") }));
    }
  });

  // --- milepæl nådd ---
  const mSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const mNr = el("input", { id: "pr-mp-nr", name: "milepael_nr",
    type: "number", required: true, step: "1", min: "1", max: "50" });
  const mDok = el("input", { id: "pr-mp-dok", name: "dokumentasjon_ref",
    type: "text", required: true, maxlength: 500 });
  const mKnapp = el("button", { type: "submit",
    text: t("ui.prosjekt.knapp.naa_milepael") });
  mSkjema.append(
    felt("pr-mp-nr", "ui.prosjekt.skjema.milepael_nr", mNr),
    // DOKUMENTASJONEN ER OBLIGATORISK, og hjelpeteksten sier HVORFOR —
    // ikke «feltet er påkrevd», men hva som står på spill.
    felt("pr-mp-dok", "ui.prosjekt.skjema.dokumentasjon", mDok,
         "ui.prosjekt.skjema.dokumentasjon_hjelp"),
    el("div", { class: "skjema-bunn" }, mKnapp));
  skjemaramme(ctx, last, {
    skjema: mSkjema, knapp: mKnapp, utfall, kvitter,
    okNokkel: "ui.prosjekt.skjema.milepael_ok",
    send: (idem) => naaMilepael(gjeldende.prosjekt_id,
                                Number(mNr.value), mDok.value, idem),
    tilbakestill: () => { mNr.value = ""; mDok.value = ""; },
  });

  // --- arbeid ---
  const aSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const aDato = el("input", { id: "pr-ar-dato", name: "utfort",
    type: "date", required: true });
  const aTimer = el("input", { id: "pr-ar-timer", name: "timer",
    type: "number", required: true, step: "0.25", min: "0.25",
    max: "24" });
  const aKost = el("input", { id: "pr-ar-kost", name: "kostnad",
    type: "number", required: true, step: "0.01", min: "0" });
  const aTekst = el("input", { id: "pr-ar-tekst", name: "beskrivelse",
    type: "text", required: true, maxlength: 2000 });
  const aKnapp = el("button", { type: "submit",
    text: t("ui.prosjekt.knapp.arbeid") });
  aSkjema.append(
    felt("pr-ar-dato", "ui.prosjekt.skjema.utfort", aDato),
    felt("pr-ar-timer", "ui.prosjekt.skjema.timer", aTimer,
         "ui.prosjekt.skjema.timer_hjelp"),
    felt("pr-ar-kost", "ui.prosjekt.skjema.kostnad", aKost),
    felt("pr-ar-tekst", "ui.prosjekt.skjema.beskrivelse", aTekst),
    el("div", { class: "skjema-bunn" }, aKnapp));
  skjemaramme(ctx, last, {
    skjema: aSkjema, knapp: aKnapp, utfall, kvitter,
    okNokkel: "ui.prosjekt.skjema.arbeid_ok",
    send: (idem) => registrerArbeid(gjeldende.prosjekt_id, {
      utfort: aDato.value, minutter: tilMinutter(aTimer.value),
      kostnad_ore: tilOre(aKost.value), beskrivelse: aTekst.value,
    }, idem),
    tilbakestill: () => { aTimer.value = ""; aKost.value = "";
                          aTekst.value = ""; },
  });

  // --- avslutning ---
  const sSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const sGrunn = el("input", { id: "pr-slutt-grunn",
    name: "begrunnelse", type: "text", required: true, maxlength: 2000 });
  const sKnapp = el("button", { type: "submit",
    text: t("ui.prosjekt.knapp.avslutt") });
  sSkjema.append(
    felt("pr-slutt-grunn", "ui.prosjekt.skjema.avslutt_begrunnelse",
         sGrunn, "ui.prosjekt.skjema.avslutt_hjelp"),
    el("div", { class: "skjema-bunn" }, sKnapp));
  skjemaramme(ctx, last, {
    skjema: sSkjema, knapp: sKnapp, utfall, kvitter,
    okNokkel: "ui.prosjekt.skjema.avslutt_ok",
    send: (idem) => avsluttProsjekt(gjeldende.prosjekt_id,
                                    sGrunn.value, idem),
    tilbakestill: () => {
      sGrunn.value = ""; innhold.hidden = true; settApen(null);
    },
  });

  const skriver = harScope(ctx, "bestilling:opprett");
  innhold.append(el("h3", { text: t("ui.prosjekt.detalj.tittel") }),
    merkelinje, milepaelboks,
    el("h4", { text: t("ui.prosjekt.detalj.arbeid") }), arbeidsboks);
  if (skriver) {
    innhold.append(
      el("h4", { text: t("ui.prosjekt.skjema.plan_tittel") }), pSkjema,
      el("h4", { text: t("ui.prosjekt.knapp.naa_milepael") }), mSkjema,
      el("h4", { text: t("ui.prosjekt.skjema.arbeid_tittel") }), aSkjema,
      el("h4", { text: t("ui.prosjekt.knapp.avslutt") }), sSkjema);
  }
  boks.append(innhold, utfall);
  innhold.hidden = true;

  function milepaelTabell(rader) {
    const tb = el("table", { class: "kpi-tabell" },
      el("caption", { text: t("ui.prosjekt.milepael.caption") }));
    tb.append(el("thead", {}, el("tr", {},
      el("th", { scope: "col", text: t("ui.prosjekt.kolonne.nr") }),
      el("th", { scope: "col", text: t("ui.prosjekt.kolonne.navn") }),
      el("th", { scope: "col", text: t("ui.prosjekt.kolonne.dato") }),
      el("th", { scope: "col", text: t("ui.prosjekt.kolonne.belop") }),
      el("th", { scope: "col", text: t("ui.prosjekt.kolonne.naadd") }),
      el("th", { scope: "col",
                 text: t("ui.prosjekt.kolonne.dokumentasjon") }))));
    const tbody = el("tbody");
    for (const m of rader) {
      const rad = el("tr", {});
      rad.append(el("th", { scope: "row", class: "celle-tall",
                            text: String(m.milepael_nr) }));
      rad.append(el("td", { class: "celle-tekst", text: m.navn }));
      rad.append(el("td", { text: m.planlagt_dato }));
      rad.append(el("td", { class: "celle-tall",
                            text: belopTekst(m.belop_ore) }));
      // NÅDD ELLER IKKE, SOM ORD — og en unådd milepæl forbi sin dato
      // sier hvor langt over den er.
      rad.append(el("td", { text: m.naadd_ts
        ? t("ui.prosjekt.milepael.naadd").replace("{av}", m.naadd_av)
        : (typeof m.dogn_over_frist === "number"
           && m.dogn_over_frist > 0
             ? t("ui.prosjekt.milepael.over")
                 .replace("{dogn}", String(m.dogn_over_frist))
             : t("ui.prosjekt.milepael.venter")) }));
      // DOKUMENTASJONEN ER KOLONNEN SOM BETYR NOE: uten den er «nådd»
      // en påstand, og den påstanden er grunnlaget for et krav.
      rad.append(el("td", { class: "celle-tekst",
        text: m.dokumentasjon_ref || "—" }));
      tbody.append(rad);
    }
    tb.append(tbody);
    return el("div", { class: "tablewrap" }, tb);
  }

  return {
    node: boks,
    async apne(p) {
      gjeldende = p;
      settApen(p.prosjekt_id);
      sett(utfall);
      sett(milepaelboks);
      sett(arbeidsboks);
      merkelinje.textContent = `${p.kunde_ref} · ${p.navn} · `
        + `${t("ui.prosjekt.detalj.budsjett")} `
        + `${belopTekst(p.budsjett_ore)} · `
        + `${t("ui.prosjekt.detalj.plan")} ${belopTekst(p.plan_ore)}`;
      // ET AVSLUTTET PROSJEKT TAR IKKE IMOT NOE.
      const aktiv = p.status === "aktiv";
      for (const k of [pKnapp, mKnapp, aKnapp, sKnapp]) {
        k.disabled = !aktiv;
      }
      innhold.hidden = false;
      let mp;
      let ar;
      try {
        const id = encodeURIComponent(p.prosjekt_id);
        mp = await hentJson(`/v1/prosjekt/${id}/milepaeler`);
        ar = await hentJson(`/v1/prosjekt/${id}/arbeidsliste`);
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        sett(utfall, el("span", { role: "alert",
          text: t("ui.prosjekt.feil.generell") }));
        return;
      }
      const liste = mp.milepaeler || [];
      if (!liste.length) {
        sett(milepaelboks, el("p", { class: "muted",
          text: t("ui.prosjekt.detalj.ingen_milepaeler") }));
      } else {
        sett(milepaelboks, milepaelTabell(liste));
      }
      const arbeid = ar.arbeid || [];
      if (!arbeid.length) {
        sett(arbeidsboks, el("p", { class: "muted",
          text: t("ui.prosjekt.detalj.ingen_arbeid") }));
        return;
      }
      const ul = el("ul", {});
      for (const a of arbeid) {
        ul.append(el("li", {},
          el("span", { text: `${a.utfort} — ${timeTekst(a.minutter)} · `
            + `${belopTekst(a.kostnad_ore)} · ${a.beskrivelse}` }),
          el("span", { class: "muted", text: ` · ${a.registrert_av}` })));
      }
      sett(arbeidsboks, ul);
    },
  };
}

function nySkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const kunde = el("input", { id: "pr-ny-kunde", name: "kunde_ref",
    type: "text", required: true, maxlength: 300 });
  const navn = el("input", { id: "pr-ny-navn", name: "navn",
    type: "text", required: true, maxlength: 300 });
  const kontrakt = el("input", { id: "pr-ny-kontrakt",
    name: "kontrakt_ref", type: "text", maxlength: 300 });
  const budsjett = el("input", { id: "pr-ny-budsjett", name: "budsjett",
    type: "number", required: true, step: "0.01", min: "0" });
  const start = el("input", { id: "pr-ny-start", name: "start",
    type: "date", required: true });
  const slutt = el("input", { id: "pr-ny-slutt", name: "planlagt_slutt",
    type: "date", required: true });
  const knapp = el("button", { type: "submit",
    text: t("ui.prosjekt.knapp.ny") });
  skjema.append(
    felt("pr-ny-kunde", "ui.prosjekt.skjema.kunde", kunde),
    felt("pr-ny-navn", "ui.prosjekt.skjema.navn", navn),
    felt("pr-ny-kontrakt", "ui.prosjekt.skjema.kontrakt", kontrakt),
    felt("pr-ny-budsjett", "ui.prosjekt.skjema.budsjett", budsjett,
         "ui.prosjekt.skjema.budsjett_hjelp"),
    felt("pr-ny-start", "ui.prosjekt.skjema.start", start),
    felt("pr-ny-slutt", "ui.prosjekt.skjema.slutt", slutt),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.prosjekt.skjema.ny_ok",
    send: (idem) => registrerProsjekt({
      kunde_ref: kunde.value, navn: navn.value,
      kontrakt_ref: kontrakt.value || null,
      budsjett_ore: tilOre(budsjett.value),
      start: start.value, planlagt_slutt: slutt.value,
    }, idem),
    tilbakestill: () => {
      navn.value = ""; kontrakt.value = ""; budsjett.value = "";
    },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.prosjekt.skjema.ny_tittel") }), skjema, utfall);
}

function terskelSkjema(ctx, last, terskler, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const budsjett = el("input", { id: "pr-t-budsjett", name: "budsjett",
    type: "number", required: true, step: "1", min: "0", max: "10000" });
  const frist = el("input", { id: "pr-t-frist", name: "frist",
    type: "number", required: true, step: "1", min: "0", max: "3650" });
  const stillhet = el("input", { id: "pr-t-stillhet", name: "stillhet",
    type: "number", required: true, step: "1", min: "1", max: "3650" });
  if (terskler) {
    budsjett.value = String(terskler.budsjettvarsel_promille);
    frist.value = String(terskler.milepael_frist_dogn);
    stillhet.value = String(terskler.stillhet_dogn);
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.prosjekt.knapp.lagre_terskler") });
  skjema.append(
    felt("pr-t-budsjett", "ui.prosjekt.terskel.budsjett", budsjett,
         "ui.prosjekt.terskel.budsjett_hjelp"),
    felt("pr-t-frist", "ui.prosjekt.terskel.milepaelfrist", frist),
    felt("pr-t-stillhet", "ui.prosjekt.terskel.stillhet", stillhet,
         "ui.prosjekt.terskel.stillhet_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.prosjekt.skjema.terskel_ok",
    send: (idem) => settProsjektterskler({
      budsjettvarsel_promille: Number(budsjett.value),
      milepael_frist_dogn: Number(frist.value),
      stillhet_dogn: Number(stillhet.value),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.prosjekt.terskel.tittel") }), skjema, utfall);
}

// Sammendraget. TALLENE KOMMER FRA SIN EGEN DØR og gjelder ALT.
function sammendrag(s) {
  const p = el("p", {
    text: t("ui.prosjekt.sammendrag")
      .replace("{aktive}", String(s.aktive))
      .replace("{budsjett}", belopTekst(s.budsjett_ore))
      .replace("{forbruk}", belopTekst(s.forbruk_ore))
      .replace("{klar}", belopTekst(s.klar_ore))
      .replace("{funn}", String(s.apne_funn)) });
  if (!s.har_terskel) {
    p.append(" ", el("strong", { text: t("ui.prosjekt.ingen_terskler") }));
  }
  if (s.vist < s.aktive) {
    p.append(" ", el("strong", {
      text: t("ui.prosjekt.avkortet").replace("{vist}", String(s.vist)) }));
  }
  return p;
}

export function visProsjekt(hoved, ctx) {
  const hode = () => flateHode(t("ui.prosjekt.tittel"),
    t("ui.prosjekt.undertittel"));
  sett(hoved, ...hode());
  // KVITTERINGEN OG DEN ÅPNE RADEN LEVER UTENFOR TEGNINGEN (klynge
  // 3-rettingen). INGEN `aria-live` her: `meldLive` eier opplesningen.
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenRad = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const settApen = (id) => { apenRad = id; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/prosjekt"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const prosjekter = d.prosjekter || [];
      const detalj = detaljpanel(ctx, last, kvitter, settApen);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.prosjekt.oversikt.tittel") }),
        sammendrag(s),
        // DE TO TALLENE ER IKKE DET SAMME, og setningen sier det.
        el("p", { class: "muted", text: t("ui.prosjekt.oversikt.skille") }));

      const liste = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.prosjekt.liste.tittel") }));
      if (!prosjekter.length) {
        liste.append(el("p", { class: "muted",
          text: t("ui.prosjekt.liste.ingen") }));
      } else {
        liste.append(prosjektTabell(prosjekter, ctx, detalj.apne));
      }

      const terskelseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.prosjekt.terskel.tittel") }));
      if (!d.terskler) {
        terskelseksjon.append(el("p", { class: "muted",
          text: t("ui.prosjekt.ingen_terskler") }));
      } else {
        terskelseksjon.append(
          el("p", { class: "muted",
            text: t("ui.prosjekt.terskel.versjon")
              .replace("{versjon}", String(d.terskler.versjon)) }),
          terskelTabell(d.terskler));
      }

      const deler = [oversikt, liste, terskelseksjon, detalj.node];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(nySkjema(ctx, last, kvitter),
                   terskelSkjema(ctx, last, d.terskler, kvitter));
      }
      sett(kropp, ...deler);
      if (apenRad) {
        const rad = prosjekter.find((x) => x.prosjekt_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
