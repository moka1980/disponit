// Leverandør- og innkjøpsagenten (M-24 v1) — LEVERANDØRREGISTERET.
//
// FLATENS VIKTIGSTE JOBB er å vise hvor forholdet til en leverandør har
// glidd: hvilke avtaler som står i SLA-brudd, hvilke priser som har
// steget over tenantens terskel, og hvilke avtaler som er i ferd med å
// løpe ut. Som TEKST, ikke bare farge (WCAG 1.4.1): «2 av 5 leveranser i
// brudd» og «utløpt for 12 døgn siden» er ord.
//
// FLATEN VISER, DEN REGNER IKKE. `brudd`, `prisavvik_promille`,
// `dogn_til_utlop` og hele SLA-oversikten er regnet i BASEN, i samme
// skann som raden (105s lesedører). Særlig BRUDD-DOMMEN: retningen på et
// SLA er en lukket tabell i basen (`m24_bryter_sla`), og en flate som
// regnet den selv ville hatt en andre retningstabell å holde i takt. Et
// brudd regnet med feil fortegn er STILLE — det ser ut som at alt er i
// orden.
//
// BELØP OG PROMILLE FORMATERES I HELTALLSARITMETIKK (101/104s form,
// ordrett): et flyttall ville gitt «19,999999999999996 %» på et avvik
// som er nøyaktig 200 promille i basen.
//
// DET FINNES INGEN «BETAL»-KNAPP, og fraværet er dommen: katalogen lover
// leverandørbetaling innen policygrenser, v1 registrerer avtalen og
// måler leveransen. En utgående betaling er den ene handlingen i
// katalogen som er umulig å angre.
//
// OG DET FINNES INGEN «FORESLÅ NY PRIS»: katalogen deler
// marginbeskyttelsen — M-24 oppdager, M-26 foreslår. `prisavvik` er
// AVVIKET mellom to målte tall, ikke et forslag.
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, avsluttAvtale, hentJson, nyIdempotensnokkel,
  registrerAvtale, registrerLeverandor, registrerLeveranse, settTerskler,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// SLA-TYPENE. Lukket sett, speil av CHECK-en i 105 — og rekkefølgen er
// oversiktens, så tabellen har samme form hver dag.
const SLA_TYPER = ["leveringstid_dogn", "responstid_timer",
                   "feilrate_promille", "oppetid_promille"];

// FUNNTYPE → MERKETEKST. Lukket tabell, ikke strengbygging.
const MERKE = {
  sla_brudd: "ui.leverandor.merke_brudd",
  pris_over_terskel: "ui.leverandor.merke_pris",
  avtale_utlopt: "ui.leverandor.merke_utlop",
  avtale_uten_maling: "ui.leverandor.merke_umalt",
  ingen_terskel: "ui.leverandor.merke_uten_terskel",
};

// BELØP I HELTALLSARITMETIKK, aldri via `/100`. `Math.trunc` og `%` på
// et heltall er eksakt, og API-taket (10^13 øre) ligger godt under
// `Number.MAX_SAFE_INTEGER` — tallet kommer helt fram gjennom JSON.
export function belopTekst(ore) {
  if (typeof ore !== "number" || !Number.isInteger(ore)) return "—";
  const neg = ore < 0;
  const a = Math.abs(ore);
  return `${neg ? "-" : ""}${Math.trunc(a / 100)},`
    + `${String(a % 100).padStart(2, "0")}`;
}

// PROMILLE → PROSENT, også i heltallsaritmetikk. `promille / 10` i
// flyttall gir «19,999999999999996 %» på 200 promille, og et prisavvik
// mot en leverandør tåler ikke et tall som nesten stemmer. Basen regner
// i promille nettopp for å slippe desimaler; flaten deler på ti med
// `trunc` og `%`, aldri med `/`.
export function prosentTekst(promille) {
  if (typeof promille !== "number" || !Number.isInteger(promille)) {
    return "—";
  }
  const neg = promille < 0;
  const a = Math.abs(promille);
  return `${neg ? "-" : ""}${Math.trunc(a / 10)},${a % 10} %`;
}

// Utløpskolonnens ORD. ENTALL HAR SIN EGEN NØKKEL (lærdommen fra
// M-21/M-34/M-13/M-17/M-18/M-23): locale-settet har ingen pluralmaskineri,
// og «expires in 1 days» ville stått på den raden et menneske leser først.
export function utlopTekst(dogn) {
  if (typeof dogn !== "number") return "—";
  if (dogn < 0) {
    const n = Math.abs(dogn);
    return n === 1
      ? t("ui.leverandor.utlopt_ett_dogn")
      : t("ui.leverandor.utlopt_for").replace("{dogn}", String(n));
  }
  if (dogn === 0) return t("ui.leverandor.utloper_i_dag");
  return dogn === 1
    ? t("ui.leverandor.om_ett_dogn")
    : t("ui.leverandor.om_dogn").replace("{dogn}", String(dogn));
}

// BRUDDENE SOM ORD, ikke som et bart tall i en kolonne. «0 av 0» er
// ingen målt kvalitet — det er en avtale ingen har målt, og de to er
// forskjellige tilstander et menneske handler ulikt på.
export function bruddTekst(a) {
  if (!a.malinger) return t("ui.leverandor.ingen_malinger");
  return t("ui.leverandor.brudd_av")
    .replace("{brudd}", String(a.brudd))
    .replace("{malinger}", String(a.malinger));
}

// SLA-VERDIEN MED SIN ENHET. Enheten står i typenavnet, og
// oversettelsen bærer den — en egen enhetskolonne ville vært to kilder
// til samme sannhet.
function verdiTekst(slaType, verdi) {
  if (typeof verdi !== "number") return "—";
  return t(`ui.leverandor.verdi.${slaType}`).replace("{verdi}",
                                                     String(verdi));
}

function avtalerad(a, ctx, apneDetalj) {
  const rad = el("tr", {});
  // LEVERANDØREN NAVNGIR raden.
  rad.append(el("th", { scope: "row", class: "celle-tekst",
                        text: a.leverandor_navn }));
  rad.append(el("td", { class: "celle-tekst", text: a.ytelse }));
  rad.append(el("td", {
    text: `${t(`ui.leverandor.sla.${a.sla_type}`)}: `
      + verdiTekst(a.sla_type, a.avtalt_verdi) }));

  const bruddcelle = el("td", {}, el("span", { text: bruddTekst(a) }));
  rad.append(bruddcelle);

  rad.append(el("td", { class: "celle-tall",
                        text: belopTekst(a.avtalt_pris_ore) }));
  // PRISAVVIKET ER ET AVVIK, ikke et forslag. `null` når den avtalte
  // prisen er null: «hvor mange promille over null» har intet svar, og
  // et oppdiktet tall ville sett ut som en måling.
  rad.append(el("td", { class: "celle-tall",
                        text: prosentTekst(a.prisavvik_promille) }));

  const utlopcelle = el("td", {},
    el("span", { text: utlopTekst(a.dogn_til_utlop) }));
  for (const funn of a.apne_funn || []) {
    if (!MERKE[funn]) continue;
    // MERKET ER TEKST. Dette er flatens viktigste opplysning på raden:
    // forholdet har glidd, og noen må se på det.
    utlopcelle.append(" ", el("strong", { class: "merke",
                                          text: t(MERKE[funn]) }));
  }
  rad.append(utlopcelle);
  rad.append(el("td", { text: t(`ui.leverandor.status.${a.status}`) }));

  const handling = el("td", {});
  const knapp = el("button", { type: "button",
    text: t("ui.leverandor.knapp.apne") });
  knapp.addEventListener("click", () => apneDetalj(a));
  handling.append(knapp);
  rad.append(handling);
  return rad;
}

function avtaleTabell(avtaler, ctx, apneDetalj) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.leverandor.liste.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.leverandor.kolonne.leverandor") }),
    el("th", { scope: "col", text: t("ui.leverandor.kolonne.ytelse") }),
    el("th", { scope: "col", text: t("ui.leverandor.kolonne.avtalt") }),
    el("th", { scope: "col", text: t("ui.leverandor.kolonne.brudd") }),
    el("th", { scope: "col", text: t("ui.leverandor.kolonne.pris") }),
    el("th", { scope: "col", text: t("ui.leverandor.kolonne.prisavvik") }),
    el("th", { scope: "col", text: t("ui.leverandor.kolonne.utlop") }),
    el("th", { scope: "col", text: t("ui.leverandor.kolonne.status") }),
    el("th", { scope: "col",
               text: t("ui.leverandor.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const a of avtaler) {
    tbody.append(avtalerad(a, ctx, apneDetalj));
  }
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function slaTabell(rader) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.leverandor.sla.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.leverandor.kolonne.slatype") }),
    el("th", { scope: "col", text: t("ui.leverandor.kolonne.avtaler") }),
    el("th", { scope: "col", text: t("ui.leverandor.kolonne.malinger") }),
    el("th", { scope: "col", text: t("ui.leverandor.kolonne.brudd") }))));
  const tbody = el("tbody");
  // ALLE FIRE TYPENE TEGNES, også de tenanten ikke bruker — døren
  // returnerer dem alle av samme grunn. En oversikt som endret form fra
  // dag til dag kan ingen sammenligne over tid.
  for (const r of rader) {
    const rad = el("tr", {});
    rad.append(el("th", { scope: "row",
                          text: t(`ui.leverandor.sla.${r.sla_type}`) }));
    rad.append(el("td", { class: "celle-tall", text: String(r.avtaler) }));
    rad.append(el("td", { class: "celle-tall", text: String(r.malinger) }));
    rad.append(el("td", { class: "celle-tall", text: String(r.brudd) }));
    tbody.append(rad);
  }
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function terskelTabell(terskler) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.leverandor.terskel.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.leverandor.kolonne.terskel") }),
    el("th", { scope: "col", text: t("ui.leverandor.kolonne.verdi") }))));
  const tbody = el("tbody");
  const linjer = [
    ["ui.leverandor.terskel.prisstigning",
     prosentTekst(terskler.prisstigning_promille)],
    ["ui.leverandor.terskel.bruddgrense",
     t("ui.leverandor.terskel.bruddgrense_verdi")
       .replace("{n}", String(terskler.sla_brudd_grense))],
    ["ui.leverandor.terskel.varsel",
     t("ui.leverandor.dogn").replace("{dogn}",
                                     String(terskler.avtale_varsel_dogn))],
    ["ui.leverandor.terskel.stillhet",
     t("ui.leverandor.dogn").replace(
       "{dogn}", String(terskler.maling_stillhet_dogn))],
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

function velger(id, navn, verdier, prefiks) {
  const s = el("select", { id, name: navn });
  for (const v of verdier) {
    s.append(el("option", { value: v, text: t(`${prefiks}.${v}`) }));
  }
  return s;
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
          ? t("ui.leverandor.feil.tilstand")
          : t("ui.leverandor.feil.generell") }));
      return;
    }
    idem = null;
    knapp.disabled = false;
    if (tilbakestill) tilbakestill();
    meldLive(t(okNokkel));
    // KVITTERINGEN SKAL OVERLEVE TEGNINGEN. `last()` bygger listen,
    // detaljpanelet og skjemaene på nytt, og en melding satt i skjemaets
    // eget `utfall` forsvant i samme øyeblikk den ble satt — brukeren
    // trykket «Registrer måling», så skjermen blinke, og satt igjen uten
    // å vite om det gikk bra. Suksessen går derfor til flatens egen
    // kvitteringslinje, som lever utenfor tegningen. FEILEN blir
    // stående i skjemaet, der den hører hjemme: den veien tegner ikke om.
    // (CodeRabbit, PR M-24.)
    kvitter(t(okNokkel));
    await last();
  });
}

// KRONER INN, ØRE UT. `Math.round` på produktet er den ENESTE veien: en
// `parseFloat` uten avrunding gir 814.9999999999999 øre på 8,15 kroner.
export function tilOre(verdi) {
  const n = Number(verdi);
  if (!Number.isFinite(n)) return null;
  return Math.round(n * 100);
}

// PROSENT INN, PROMILLE UT. Samme dom, én desimal: 12,5 % er 125
// promille, ikke 124.99999999999999.
export function tilPromille(verdi) {
  const n = Number(verdi);
  if (!Number.isFinite(n)) return null;
  return Math.round(n * 10);
}

// …OG DEN ANDRE VEIEN, til skjemafeltets verdi. `promille / 10` ville
// vært en flyttallsdivisjon i den ene retningen der vi har et heltall —
// `trunc` og `%` gir samme tall uten å gå via en verdi maskinen må
// runde. Feltet er `type="number"`, så desimalskilletegnet er punktum
// her og komma bare i visningen.
export function promilleTilFelt(promille) {
  if (typeof promille !== "number" || !Number.isInteger(promille)) {
    return "";
  }
  const neg = promille < 0;
  const a = Math.abs(promille);
  return `${neg ? "-" : ""}${Math.trunc(a / 10)}.${a % 10}`;
}

// DETALJPANELET: målingene, og de to handlingene.
function detaljpanel(ctx, last, kvitter, settApen) {
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;

  const merkelinje = el("p", { class: "muted" });
  const historikk = el("div", {});

  // --- ny måling ---
  const mSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const mDato = el("input", { id: "lv-mal-dato", name: "levert",
    type: "date", required: true });
  const mVerdi = el("input", { id: "lv-mal-verdi", name: "faktisk_verdi",
    type: "number", required: true, step: "1", min: "0" });
  const mPris = el("input", { id: "lv-mal-pris", name: "faktisk_pris",
    type: "number", required: true, step: "0.01", min: "0" });
  const mRef = el("input", { id: "lv-mal-ref", name: "referanse",
    type: "text", maxlength: 200 });
  const mKnapp = el("button", { type: "submit",
    text: t("ui.leverandor.knapp.maling") });
  mSkjema.append(
    felt("lv-mal-dato", "ui.leverandor.skjema.levert", mDato,
         "ui.leverandor.skjema.levert_hjelp"),
    felt("lv-mal-verdi", "ui.leverandor.skjema.faktisk_verdi", mVerdi),
    felt("lv-mal-pris", "ui.leverandor.skjema.faktisk_pris", mPris),
    felt("lv-mal-ref", "ui.leverandor.skjema.referanse", mRef),
    el("div", { class: "skjema-bunn" }, mKnapp));
  skjemaramme(ctx, last, {
    skjema: mSkjema, knapp: mKnapp, utfall, kvitter,
    okNokkel: "ui.leverandor.skjema.maling_ok",
    send: (idem) => registrerLeveranse(gjeldende.avtale_id, {
      levert: mDato.value,
      faktisk_verdi: Number(mVerdi.value),
      faktisk_pris_ore: tilOre(mPris.value),
      referanse: mRef.value || null,
    }, idem),
    tilbakestill: () => { mVerdi.value = ""; mPris.value = "";
                          mRef.value = ""; },
  });

  // --- avslutt avtalen ---
  const aSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const aGrunn = el("input", { id: "lv-slutt-grunn", name: "begrunnelse",
    type: "text", required: true, maxlength: 2000 });
  const aKnapp = el("button", { type: "submit",
    text: t("ui.leverandor.knapp.avslutt") });
  aSkjema.append(
    felt("lv-slutt-grunn", "ui.leverandor.skjema.avslutt_begrunnelse",
         aGrunn, "ui.leverandor.skjema.avslutt_hjelp"),
    el("div", { class: "skjema-bunn" }, aKnapp));
  skjemaramme(ctx, last, {
    skjema: aSkjema, knapp: aKnapp, utfall, kvitter,
    okNokkel: "ui.leverandor.skjema.avslutt_ok",
    send: (idem) => avsluttAvtale(gjeldende.avtale_id, aGrunn.value, idem),
    // …og AVTALEN LUKKES OGSÅ I FLATEN. Å gjenåpne panelet på en avtale
    // som nettopp ble avsluttet ville tilbudt to knapper som begge er
    // døde.
    tilbakestill: () => {
      aGrunn.value = ""; innhold.hidden = true; settApen(null);
    },
  });

  const skriver = harScope(ctx, "bestilling:opprett");
  innhold.append(el("h3", { text: t("ui.leverandor.detalj.tittel") }),
    merkelinje, historikk);
  if (skriver) {
    innhold.append(
      el("h4", { text: t("ui.leverandor.skjema.maling_tittel") }), mSkjema,
      el("h4", { text: t("ui.leverandor.knapp.avslutt") }), aSkjema);
  }
  boks.append(innhold, utfall);
  innhold.hidden = true;

  return {
    node: boks,
    async apne(a) {
      gjeldende = a;
      settApen(a.avtale_id);
      sett(utfall);
      sett(historikk);
      merkelinje.textContent = `${a.leverandor_navn} · ${a.ytelse} · `
        + `${t(`ui.leverandor.sla.${a.sla_type}`)} `
        + `${verdiTekst(a.sla_type, a.avtalt_verdi)} · `
        + `${belopTekst(a.avtalt_pris_ore)}`;
      // EN AVSLUTTET AVTALE TAR IKKE IMOT NOE. Knappene deaktiveres i
      // stedet for å love noe serveren avviser med 409.
      const aktiv = a.status === "aktiv";
      mKnapp.disabled = !aktiv;
      aKnapp.disabled = !aktiv;
      innhold.hidden = false;
      let d;
      try {
        d = await hentJson(
          `/v1/leverandor/${encodeURIComponent(a.avtale_id)}/leveranser`);
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        sett(utfall, el("span", { role: "alert",
          text: t("ui.leverandor.feil.generell") }));
        return;
      }
      const liste = d.leveranser || [];
      if (!liste.length) {
        sett(historikk, el("p", { class: "muted",
          text: t("ui.leverandor.detalj.ingen") }));
        return;
      }
      const ul = el("ul", {});
      for (const m of liste) {
        // BRUDD-DOMMEN KOMMER FRA BASEN og står som ORD på linjen.
        const li = el("li", {},
          el("span", { text: `${m.levert} — `
            + `${verdiTekst(a.sla_type, m.faktisk_verdi)} · `
            + `${belopTekst(m.faktisk_pris_ore)}` }));
        li.append(" ", el("strong", {
          text: t(m.brudd ? "ui.leverandor.detalj.brudd"
                          : "ui.leverandor.detalj.innenfor") }));
        li.append(el("span", { class: "muted",
          text: ` · ${m.registrert_av}`
            + (m.referanse ? ` · ${m.referanse}` : "") }));
        ul.append(li);
      }
      sett(historikk, ul);
    },
  };
}

function partSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const navn = el("input", { id: "lv-ny-navn", name: "navn",
    type: "text", required: true, maxlength: 300 });
  const ref = el("input", { id: "lv-ny-ref", name: "ekstern_ref",
    type: "text", maxlength: 200 });
  const knapp = el("button", { type: "submit",
    text: t("ui.leverandor.knapp.ny_part") });
  skjema.append(
    felt("lv-ny-navn", "ui.leverandor.skjema.navn", navn),
    felt("lv-ny-ref", "ui.leverandor.skjema.ekstern_ref", ref,
         "ui.leverandor.skjema.ekstern_ref_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.leverandor.skjema.part_ok",
    send: (idem) => registrerLeverandor(
      { navn: navn.value, ekstern_ref: ref.value || null }, idem),
    tilbakestill: () => { navn.value = ""; ref.value = ""; },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.leverandor.skjema.part_tittel") }), skjema,
    utfall);
}

function avtaleSkjema(ctx, last, leverandorer, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const part = el("select", { id: "lv-avt-part", name: "leverandor_id",
    required: true });
  for (const l of leverandorer) {
    if (!l.aktiv) continue;
    part.append(el("option", { value: l.leverandor_id, text: l.navn }));
  }
  const ytelse = el("input", { id: "lv-avt-ytelse", name: "ytelse",
    type: "text", required: true, maxlength: 300 });
  const slaType = velger("lv-avt-sla", "sla_type", SLA_TYPER,
                         "ui.leverandor.sla");
  const verdi = el("input", { id: "lv-avt-verdi", name: "avtalt_verdi",
    type: "number", required: true, step: "1", min: "0" });
  const pris = el("input", { id: "lv-avt-pris", name: "avtalt_pris",
    type: "number", required: true, step: "0.01", min: "0" });
  const fra = el("input", { id: "lv-avt-fra", name: "gyldig_fra",
    type: "date", required: true });
  const til = el("input", { id: "lv-avt-til", name: "gyldig_til",
    type: "date", required: true });
  const knapp = el("button", { type: "submit",
    text: t("ui.leverandor.knapp.ny_avtale") });
  skjema.append(
    felt("lv-avt-part", "ui.leverandor.skjema.leverandor", part),
    felt("lv-avt-ytelse", "ui.leverandor.skjema.ytelse", ytelse),
    felt("lv-avt-sla", "ui.leverandor.skjema.sla_type", slaType,
         "ui.leverandor.skjema.sla_hjelp"),
    felt("lv-avt-verdi", "ui.leverandor.skjema.avtalt_verdi", verdi),
    felt("lv-avt-pris", "ui.leverandor.skjema.avtalt_pris", pris),
    felt("lv-avt-fra", "ui.leverandor.skjema.gyldig_fra", fra),
    felt("lv-avt-til", "ui.leverandor.skjema.gyldig_til", til),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.leverandor.skjema.avtale_ok",
    send: (idem) => registrerAvtale({
      leverandor_id: part.value, ytelse: ytelse.value,
      sla_type: slaType.value, avtalt_verdi: Number(verdi.value),
      avtalt_pris_ore: tilOre(pris.value),
      gyldig_fra: fra.value, gyldig_til: til.value,
    }, idem),
    tilbakestill: () => { ytelse.value = ""; verdi.value = "";
                          pris.value = ""; },
  });
  const boks = el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.leverandor.skjema.avtale_tittel") }));
  if (!part.options.length) {
    // INGEN LEVERANDØR, INGEN AVTALE. En setning er ærligere enn et tomt
    // nedtrekk som ser ut som en feil.
    boks.append(el("p", { class: "muted",
      text: t("ui.leverandor.skjema.ingen_part") }));
    return boks;
  }
  boks.append(skjema, utfall);
  return boks;
}

function terskelSkjema(ctx, last, terskler, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const pris = el("input", { id: "lv-t-pris", name: "prisstigning",
    type: "number", required: true, step: "0.1", min: "0", max: "10000" });
  const grense = el("input", { id: "lv-t-grense", name: "sla_brudd_grense",
    type: "number", required: true, step: "1", min: "1", max: "1000" });
  const varsel = el("input", { id: "lv-t-varsel", name: "avtale_varsel",
    type: "number", required: true, step: "1", min: "0", max: "3650" });
  const stillhet = el("input", { id: "lv-t-stillhet", name: "stillhet",
    type: "number", required: true, step: "1", min: "1", max: "3650" });
  if (terskler) {
    pris.value = promilleTilFelt(terskler.prisstigning_promille);
    grense.value = String(terskler.sla_brudd_grense);
    varsel.value = String(terskler.avtale_varsel_dogn);
    stillhet.value = String(terskler.maling_stillhet_dogn);
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.leverandor.knapp.lagre_terskler") });
  skjema.append(
    felt("lv-t-pris", "ui.leverandor.terskel.prisstigning", pris,
         "ui.leverandor.terskel.prisstigning_hjelp"),
    felt("lv-t-grense", "ui.leverandor.terskel.bruddgrense", grense,
         "ui.leverandor.terskel.bruddgrense_hjelp"),
    felt("lv-t-varsel", "ui.leverandor.terskel.varsel", varsel),
    felt("lv-t-stillhet", "ui.leverandor.terskel.stillhet", stillhet,
         "ui.leverandor.terskel.stillhet_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.leverandor.skjema.terskel_ok",
    send: (idem) => settTerskler({
      prisstigning_promille: tilPromille(pris.value),
      sla_brudd_grense: Number(grense.value),
      avtale_varsel_dogn: Number(varsel.value),
      maling_stillhet_dogn: Number(stillhet.value),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.leverandor.terskel.tittel") }), skjema, utfall);
}

// Sammendraget. TALLENE KOMMER FRA SIN EGEN DØR og gjelder ALT.
function sammendrag(s) {
  const p = el("p", {
    text: t("ui.leverandor.sammendrag")
      .replace("{avtaler}", String(s.aktive_avtaler))
      .replace("{leverandorer}", String(s.leverandorer))
      .replace("{avtalt}", belopTekst(s.avtalt_ore))
      .replace("{brudd}", String(s.avtaler_med_brudd))
      .replace("{funn}", String(s.apne_funn)) });
  if (!s.har_terskel) {
    // UTEN TERSKLER VET INGEN HVA «FOR DYRT» BETYR. Setningen står som
    // ord, ikke som en tom tabell lenger nede.
    p.append(" ", el("strong", {
      text: t("ui.leverandor.ingen_terskler") }));
  }
  // AVKORTINGEN SIES HØYT.
  if (s.vist < s.aktive_avtaler) {
    p.append(" ", el("strong", {
      text: t("ui.leverandor.avkortet").replace("{vist}",
                                                String(s.vist)) }));
  }
  return p;
}

export function visLeverandor(hoved, ctx) {
  const hode = () => flateHode(t("ui.leverandor.tittel"),
    t("ui.leverandor.undertittel"));
  sett(hoved, ...hode());
  // KVITTERINGEN OG DEN ÅPNE AVTALEN LEVER UTENFOR TEGNINGEN. Alt inne i
  // `kropp` bygges på nytt ved hver `last()`, og før dette forsvant både
  // kvitteringen og detaljpanelet i det en måling ble registrert — så
  // neste måling krevde at brukeren fant fram til raden igjen.
  //
  // INGEN `aria-live` her: `meldLive` eier opplesningen, og to regioner
  // ville lest den samme setningen to ganger.
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenRad = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const settApen = (id) => { apenRad = id; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/leverandor"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const avtaler = d.avtaler || [];
      const leverandorer = d.leverandorer || [];
      const detalj = detaljpanel(ctx, last, kvitter, settApen);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.leverandor.oversikt.tittel") }),
        sammendrag(s));

      const sla = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.leverandor.sla.tittel") }),
        slaTabell(d.slaoversikt || []));

      const liste = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.leverandor.liste.tittel") }));
      if (!avtaler.length) {
        liste.append(el("p", { class: "muted",
          text: t("ui.leverandor.liste.ingen") }));
      } else {
        liste.append(avtaleTabell(avtaler, ctx, detalj.apne));
      }

      const terskelseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.leverandor.terskel.tittel") }));
      if (!d.terskler) {
        terskelseksjon.append(el("p", { class: "muted",
          text: t("ui.leverandor.ingen_terskler") }));
      } else {
        terskelseksjon.append(
          el("p", { class: "muted",
            text: t("ui.leverandor.terskel.versjon")
              .replace("{versjon}", String(d.terskler.versjon)) }),
          terskelTabell(d.terskler));
      }

      const deler = [oversikt, sla, liste, terskelseksjon, detalj.node];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(partSkjema(ctx, last, kvitter),
                   avtaleSkjema(ctx, last, leverandorer, kvitter),
                   terskelSkjema(ctx, last, d.terskler, kvitter));
      }
      sett(kropp, ...deler);
      // GJENÅPNE PANELET på den avtalen som sto åpen. Finnes den ikke
      // lenger i listen — avsluttet, eller falt utenfor avkortingen —
      // slippes den, framfor å åpne et panel på en rad ingen ser.
      if (apenRad) {
        const rad = avtaler.find((x) => x.avtale_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
