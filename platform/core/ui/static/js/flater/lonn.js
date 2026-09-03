// Lønnsgrunnlaget (M-39 v1) — GRUNNLAGET, IKKE LØNNSKJØRINGEN.
//
// FLATENS VIKTIGSTE JOBB er å vise FØRT TID OG PLANLAGT TID PÅ SAMME
// LINJE. `timer_mot_arbeidsplan` er et spørsmål om en SAMMENLIGNING, og
// en flate som bare viste det ene tallet ville gjort sammenligningen
// umulig å etterprøve.
//
// DET FINNES INGEN «GENERER LØNNSFIL»-KNAPP, og fraværet er dommen: en
// lønnsfil er ikke en betaling — det er en fil. Den ser harmløs ut, den
// kan «bare genereres», og den er nettopp derfor farligere enn en
// enkelt utbetaling: den rammer ALLE på én gang, og den rammer noen som
// har regnet med beløpet.
//
// DET FINNES HELLER INGEN «MERK SOM OVERTID»-KNAPP. Overtid utledes av
// timene mot tenantens egen normaltid og blir et FUNN noen må se på —
// et flagg en bruker satte ville vært nøyaktig den attestasjonen
// `overtid_flagget` skal hvile på.
//
// TIMER I HELTALLSARITMETIKK. Klienten regner om fra timer til
// MINUTTER én gang, i `tilMinutter`, og sender bare heltall. «7,5 time»
// som flyttall er 7.499999999999999 på veien tilbake.
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, hentJson, nyIdempotensnokkel, registrerLonnstaker,
  registrerTimer, settArbeidsplan, settLonnstakerAktiv,
  settLonnsterskler,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

const MERKE = {
  time_uten_arbeidsplan: "ui.lonn.merke_uten_plan",
  avvik_mot_plan: "ui.lonn.merke_avvik",
  overtid: "ui.lonn.merke_overtid",
  ukjent_prosjektkode: "ui.lonn.merke_kode",
  ingen_terskel: "ui.lonn.merke_uten_terskel",
};

// LUKKET SETT. `korreksjon` står her fordi en feilført time rettes med
// en NY rad — ikke fordi flaten utfører en korreksjon mot noen.
export const KILDER = ["fort_av_ansatt", "fort_av_leder", "import",
                       "korreksjon"];

const KILDETEKST = {
  fort_av_ansatt: "ui.lonn.kilde.fort_av_ansatt",
  fort_av_leder: "ui.lonn.kilde.fort_av_leder",
  import: "ui.lonn.kilde.import",
  korreksjon: "ui.lonn.kilde.korreksjon",
};

// MINUTTER UT SOM «7:30». Heltallsaritmetikk hele veien: ingen
// divisjon som gir desimaler, ingen avrunding som driver.
export function timeTekst(minutter) {
  if (typeof minutter !== "number" || !Number.isInteger(minutter)) {
    return "—";
  }
  const neg = minutter < 0;
  const a = Math.abs(minutter);
  return `${neg ? "-" : ""}${Math.trunc(a / 60)}:`
    + `${String(a % 60).padStart(2, "0")}`;
}

// TIMER INN, MINUTTER UT. `Math.round` på produktet er den ENESTE
// veien, og den skjer HER — én gang, i klienten. Et tomt felt er
// `null`, ikke null minutter.
export function tilMinutter(verdi) {
  if (verdi === "" || verdi === null || verdi === undefined) return null;
  const n = Number(verdi);
  if (!Number.isFinite(n)) return null;
  return Math.round(n * 60);
}

// AVVIKET ER EN DIFFERANSE I MINUTTER, ikke en prosent. Fortegnet skal
// ses: mindre enn planlagt og mer enn planlagt er to helt forskjellige
// samtaler — den ene er et spørsmål om fravær, den andre om overtid.
export function avvikTekst(avvik) {
  if (typeof avvik !== "number" || !Number.isInteger(avvik)) {
    // INGEN PLAN ER IKKE «STEMMER». Uten en plan finnes det ingen
    // sammenligning, og flaten later ikke som noe annet.
    return t("ui.lonn.uten_plan");
  }
  if (avvik === 0) return t("ui.lonn.uten_avvik");
  return t(avvik > 0 ? "ui.lonn.avvik_over" : "ui.lonn.avvik_under")
    .replace("{tid}", timeTekst(Math.abs(avvik)));
}

export function planTekst(minutter, kode) {
  if (typeof minutter !== "number") return t("ui.lonn.uten_plan");
  return t("ui.lonn.plan_med_kode")
    .replace("{tid}", timeTekst(minutter))
    .replace("{kode}", kode || "—");
}

export function kildeTekst(kilde) {
  if (!kilde) return t("ui.lonn.uten_kilde");
  return t(KILDETEKST[kilde] || "ui.lonn.kilde.ukjent");
}

function takerrad(s, apneDetalj) {
  const rad = el("tr", {});
  rad.append(el("th", { scope: "row", class: "celle-id",
                        text: s.ekstern_ref }));
  rad.append(el("td", { class: "celle-tekst", text: s.navn }));
  rad.append(el("td", { class: "celle-tekst",
    text: planTekst(s.planlagt_minutter_dag, s.plan_prosjektkode) }));
  rad.append(el("td", { class: "celle-tall",
                        text: timeTekst(s.sum_minutter) }));
  rad.append(el("td", { class: "celle-tall", text: String(s.dager) }));
  rad.append(el("td", { class: "celle-id",
    text: s.siste_dato || t("ui.lonn.uten_timer") }));

  const merkecelle = el("td", {},
    el("span", { text: s.aktiv ? t("ui.lonn.status.aktiv")
                               : t("ui.lonn.status.inaktiv") }));
  for (const funn of s.apne_funn || []) {
    if (!MERKE[funn]) continue;
    // MERKET ER TEKST (WCAG 1.4.1).
    merkecelle.append(" ", el("strong", { class: "merke",
                                          text: t(MERKE[funn]) }));
  }
  rad.append(merkecelle);

  const handling = el("td", {});
  const knapp = el("button", { type: "button",
    text: t("ui.lonn.knapp.apne") });
  knapp.addEventListener("click", () => apneDetalj(s));
  handling.append(knapp);
  rad.append(handling);
  return rad;
}

function takerTabell(takere, apneDetalj) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.lonn.liste.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.lonn.kolonne.ref") }),
    el("th", { scope: "col", text: t("ui.lonn.kolonne.navn") }),
    el("th", { scope: "col", text: t("ui.lonn.kolonne.plan") }),
    el("th", { scope: "col", text: t("ui.lonn.kolonne.sum") }),
    el("th", { scope: "col", text: t("ui.lonn.kolonne.dager") }),
    el("th", { scope: "col", text: t("ui.lonn.kolonne.siste") }),
    el("th", { scope: "col", text: t("ui.lonn.kolonne.merker") }),
    el("th", { scope: "col", text: t("ui.lonn.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const s of takere) tbody.append(takerrad(s, apneDetalj));
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function terskelTabell(terskler) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.lonn.terskel.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.lonn.kolonne.terskel") }),
    el("th", { scope: "col", text: t("ui.lonn.kolonne.verdi") }))));
  const tbody = el("tbody");
  const linjer = [
    ["ui.lonn.terskel.dag", timeTekst(terskler.normaltid_minutter_dag)],
    ["ui.lonn.terskel.uke", timeTekst(terskler.normaltid_minutter_uke)],
    ["ui.lonn.terskel.avvik", timeTekst(terskler.avvik_minutter)],
    ["ui.lonn.terskel.uten_plan",
     t("ui.lonn.dogn").replace("{dogn}",
                               String(terskler.uten_plan_dogn))],
    ["ui.lonn.terskel.vindu",
     t("ui.lonn.dogn").replace(
       "{dogn}", String(terskler.vurderingsvindu_dogn))],
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

function velger(id, navn, verdier, tekster) {
  const s = el("select", { id, name: navn, required: true });
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
          ? t("ui.lonn.feil.tilstand")
          : t("ui.lonn.feil.generell") }));
      return;
    }
    idem = null;
    knapp.disabled = false;
    if (tilbakestill) tilbakestill();
    meldLive(t(okNokkel));
    // KVITTERINGEN SKAL OVERLEVE TEGNINGEN (klynge 3-rettingen).
    kvitter(t(okNokkel));
    await last();
  });
}

function detaljpanel(ctx, last, kvitter, settApen) {
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;
  // ÅPNINGSTELLEREN (109s lærdom): åpner noen taker B mens As dager er
  // underveis, ville As linjer blitt tegnet inn i Bs panel — et
  // timegrunnlag som ser ut til å høre til en annen ansatt.
  let apningsnr = 0;

  const merkelinje = el("p", { class: "muted" });
  const dager = el("div", {});

  // --- nye timer ---
  const tSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const tDato = el("input", { id: "ln-t-dato", name: "dato",
    type: "date", required: true });
  // TIMER INN, MINUTTER UT. `step="0.25"` er et kvarter — det minste
  // en timeliste normalt føres i — og `tilMinutter` runder av ÉN gang.
  const tTimer = el("input", { id: "ln-t-timer", name: "timer",
    type: "number", required: true, step: "0.25", min: "0", max: "24" });
  const tKode = el("input", { id: "ln-t-kode", name: "prosjektkode",
    type: "text", required: true, maxlength: 60 });
  const tKilde = velger("ln-t-kilde", "kilde", KILDER, KILDETEKST);
  const tKildeRef = el("input", { id: "ln-t-kilderef",
    name: "kilde_ref", type: "text", required: true, maxlength: 100 });
  const tNotat = el("input", { id: "ln-t-notat", name: "notat",
    type: "text", required: true, maxlength: 2000 });
  const tKnapp = el("button", { type: "submit",
    text: t("ui.lonn.knapp.nye_timer") });
  tSkjema.append(
    felt("ln-t-dato", "ui.lonn.skjema.dato", tDato,
         "ui.lonn.skjema.dato_hjelp"),
    felt("ln-t-timer", "ui.lonn.skjema.timer", tTimer,
         "ui.lonn.skjema.timer_hjelp"),
    felt("ln-t-kode", "ui.lonn.skjema.prosjektkode", tKode,
         "ui.lonn.skjema.prosjektkode_hjelp"),
    felt("ln-t-kilde", "ui.lonn.skjema.kilde", tKilde,
         "ui.lonn.skjema.kilde_hjelp"),
    felt("ln-t-kilderef", "ui.lonn.skjema.kilde_ref", tKildeRef),
    felt("ln-t-notat", "ui.lonn.skjema.notat", tNotat),
    el("div", { class: "skjema-bunn" }, tKnapp));
  // DET FINNES INGEN OVERTIDSAVKRYSNING HER, og fraværet er dommen.
  skjemaramme(ctx, last, {
    skjema: tSkjema, knapp: tKnapp, utfall, kvitter,
    okNokkel: "ui.lonn.skjema.timer_ok",
    send: (idem) => registrerTimer(gjeldende.taker_id, {
      dato: tDato.value, minutter: tilMinutter(tTimer.value),
      prosjektkode: tKode.value, kilde: tKilde.value,
      kilde_ref: tKildeRef.value, notat: tNotat.value,
    }, idem),
    tilbakestill: () => { tKildeRef.value = ""; tNotat.value = ""; },
  });

  // --- ny arbeidsplan ---
  const pSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const pTimer = el("input", { id: "ln-p-timer", name: "planlagt",
    type: "number", required: true, step: "0.25", min: "0", max: "24" });
  const pKode = el("input", { id: "ln-p-kode", name: "prosjektkode",
    type: "text", required: true, maxlength: 60 });
  const pFra = el("input", { id: "ln-p-fra", name: "gyldig_fra",
    type: "date", required: true });
  const pGrunn = el("input", { id: "ln-p-grunn", name: "begrunnelse",
    type: "text", required: true, maxlength: 2000 });
  const pKnapp = el("button", { type: "submit",
    text: t("ui.lonn.knapp.ny_plan") });
  pSkjema.append(
    felt("ln-p-timer", "ui.lonn.skjema.planlagt", pTimer,
         "ui.lonn.skjema.planlagt_hjelp"),
    felt("ln-p-kode", "ui.lonn.skjema.prosjektkode", pKode),
    felt("ln-p-fra", "ui.lonn.skjema.gyldig_fra", pFra,
         "ui.lonn.skjema.gyldig_fra_hjelp"),
    felt("ln-p-grunn", "ui.lonn.skjema.begrunnelse", pGrunn,
         "ui.lonn.skjema.begrunnelse_hjelp"),
    el("div", { class: "skjema-bunn" }, pKnapp));
  skjemaramme(ctx, last, {
    skjema: pSkjema, knapp: pKnapp, utfall, kvitter,
    okNokkel: "ui.lonn.skjema.plan_ok",
    send: (idem) => settArbeidsplan(gjeldende.taker_id, {
      planlagt_minutter_dag: tilMinutter(pTimer.value),
      prosjektkode: pKode.value, gyldig_fra: pFra.value,
      begrunnelse: pGrunn.value,
    }, idem),
    tilbakestill: () => { pGrunn.value = ""; },
  });

  // --- aktiv/inaktiv ---
  const aSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const aKnapp = el("button", { type: "submit",
    text: t("ui.lonn.knapp.deaktiver") });
  aSkjema.append(
    el("p", { class: "muted", text: t("ui.lonn.skjema.aktiv_hjelp") }),
    el("div", { class: "skjema-bunn" }, aKnapp));
  skjemaramme(ctx, last, {
    skjema: aSkjema, knapp: aKnapp, utfall, kvitter,
    okNokkel: "ui.lonn.skjema.aktiv_ok",
    send: (idem) => settLonnstakerAktiv(gjeldende.taker_id,
                                        !gjeldende.aktiv, idem),
    tilbakestill: () => { settApen(null); innhold.hidden = true; },
  });

  const skriver = harScope(ctx, "bestilling:opprett");
  innhold.append(el("h3", { text: t("ui.lonn.detalj.tittel") }),
    merkelinje, dager);
  if (skriver) {
    innhold.append(
      el("h4", { text: t("ui.lonn.skjema.timer_tittel") }), tSkjema,
      el("h4", { text: t("ui.lonn.skjema.plan_tittel") }), pSkjema,
      el("h4", { text: t("ui.lonn.skjema.aktiv_tittel") }), aSkjema);
  }
  boks.append(innhold, utfall);
  innhold.hidden = true;

  function dagTabell(rader) {
    const tb = el("table", { class: "kpi-tabell" },
      el("caption", { text: t("ui.lonn.dager.caption") }));
    tb.append(el("thead", {}, el("tr", {},
      el("th", { scope: "col", text: t("ui.lonn.kolonne.dato") }),
      el("th", { scope: "col", text: t("ui.lonn.kolonne.fort") }),
      el("th", { scope: "col", text: t("ui.lonn.kolonne.planlagt") }),
      el("th", { scope: "col", text: t("ui.lonn.kolonne.avvik") }),
      el("th", { scope: "col", text: t("ui.lonn.kolonne.koder") }),
      el("th", { scope: "col", text: t("ui.lonn.kolonne.poster") }))));
    const tbody = el("tbody");
    for (const d of rader) {
      const rad = el("tr", {});
      rad.append(el("th", { scope: "row", class: "celle-id",
                            text: d.dato }));
      // FØRT TID OG PLANLAGT TID PÅ SAMME LINJE — det er hele
      // sammenligningen `timer_mot_arbeidsplan` ville hvilt på.
      rad.append(el("td", { class: "celle-tall",
                            text: timeTekst(d.minutter) }));
      rad.append(el("td", { class: "celle-tall",
        text: typeof d.planlagt_minutter === "number"
          ? timeTekst(d.planlagt_minutter) : t("ui.lonn.uten_plan") }));
      rad.append(el("td", { class: "celle-tekst",
                            text: avvikTekst(d.avvik_minutter) }));
      const kodecelle = el("td", { class: "celle-tekst" },
        el("span", { text: (d.prosjektkoder || []).join(", ") || "—" }));
      // KODEN SOM IKKE ER PLANENS ER MERKET, MED ORD.
      if (d.ukjent_prosjektkode) {
        kodecelle.append(" ", el("strong", { class: "merke",
          text: t("ui.lonn.merke_kode") }));
      }
      rad.append(kodecelle);
      rad.append(el("td", { class: "celle-tall", text: String(d.poster) }));
      tbody.append(rad);
    }
    tb.append(tbody);
    return el("div", { class: "tablewrap" }, tb);
  }

  return {
    node: boks,
    async apne(s) {
      const nr = ++apningsnr;
      gjeldende = s;
      settApen(s.taker_id);
      sett(utfall);
      sett(dager);
      merkelinje.textContent = `${s.ekstern_ref} · ${s.navn}`;
      aKnapp.textContent = t(s.aktiv ? "ui.lonn.knapp.deaktiver"
                                     : "ui.lonn.knapp.aktiver");
      // EN DEAKTIVERT TAKER TAR IKKE IMOT NYE TIMER — men hen KAN
      // aktiveres igjen, så den knappen står levende.
      tKnapp.disabled = !s.aktiv;
      pKnapp.disabled = !s.aktiv;
      innhold.hidden = false;
      let d;
      try {
        d = await hentJson(
          `/v1/lonn/${encodeURIComponent(s.taker_id)}/dager`);
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        if (nr !== apningsnr) return;
        sett(dager, el("p", { class: "muted",
          text: t("ui.lonn.feil.generell") }));
        return;
      }
      if (nr !== apningsnr) return;
      const liste = d.dager || [];
      if (!liste.length) {
        sett(dager, el("p", { class: "muted",
          text: t("ui.lonn.detalj.ingen") }));
        return;
      }
      sett(dager, dagTabell(liste));
    },
  };
}

function takerSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const ref = el("input", { id: "ln-ny-ref", name: "ekstern_ref",
    type: "text", required: true, maxlength: 100 });
  const navn = el("input", { id: "ln-ny-navn", name: "navn",
    type: "text", required: true, maxlength: 300 });
  const knapp = el("button", { type: "submit",
    text: t("ui.lonn.knapp.ny_taker") });
  skjema.append(
    felt("ln-ny-ref", "ui.lonn.skjema.ref", ref,
         "ui.lonn.skjema.ref_hjelp"),
    felt("ln-ny-navn", "ui.lonn.skjema.navn", navn),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.lonn.skjema.taker_ok",
    send: (idem) => registrerLonnstaker({
      ekstern_ref: ref.value, navn: navn.value }, idem),
    tilbakestill: () => { ref.value = ""; navn.value = ""; },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.lonn.skjema.taker_tittel") }), skjema,
    utfall);
}

function terskelSkjema(ctx, last, terskler, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const dag = el("input", { id: "ln-k-dag", name: "dag",
    type: "number", required: true, step: "0.25", min: "0", max: "24" });
  const uke = el("input", { id: "ln-k-uke", name: "uke",
    type: "number", required: true, step: "0.25", min: "0", max: "168" });
  const avvik = el("input", { id: "ln-k-avvik", name: "avvik",
    type: "number", required: true, step: "0.25", min: "0", max: "24" });
  const utenPlan = el("input", { id: "ln-k-utenplan", name: "utenplan",
    type: "number", required: true, step: "1", min: "0", max: "3650" });
  const vindu = el("input", { id: "ln-k-vindu", name: "vindu",
    type: "number", required: true, step: "1", min: "1", max: "3650" });
  if (terskler) {
    dag.value = (terskler.normaltid_minutter_dag / 60).toFixed(2);
    uke.value = (terskler.normaltid_minutter_uke / 60).toFixed(2);
    avvik.value = (terskler.avvik_minutter / 60).toFixed(2);
    utenPlan.value = String(terskler.uten_plan_dogn);
    vindu.value = String(terskler.vurderingsvindu_dogn);
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.lonn.knapp.lagre_terskler") });
  skjema.append(
    felt("ln-k-dag", "ui.lonn.terskel.dag", dag,
         "ui.lonn.terskel.dag_hjelp"),
    felt("ln-k-uke", "ui.lonn.terskel.uke", uke,
         "ui.lonn.terskel.uke_hjelp"),
    felt("ln-k-avvik", "ui.lonn.terskel.avvik", avvik,
         "ui.lonn.terskel.avvik_hjelp"),
    felt("ln-k-utenplan", "ui.lonn.terskel.uten_plan", utenPlan,
         "ui.lonn.terskel.uten_plan_hjelp"),
    felt("ln-k-vindu", "ui.lonn.terskel.vindu", vindu,
         "ui.lonn.terskel.vindu_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.lonn.skjema.terskel_ok",
    send: (idem) => settLonnsterskler({
      normaltid_minutter_dag: tilMinutter(dag.value),
      normaltid_minutter_uke: tilMinutter(uke.value),
      avvik_minutter: tilMinutter(avvik.value),
      uten_plan_dogn: Number(utenPlan.value),
      vurderingsvindu_dogn: Number(vindu.value),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.lonn.terskel.tittel") }), skjema, utfall);
}

function sammendrag(s) {
  const p = el("p", {
    text: t("ui.lonn.sammendrag")
      .replace("{aktive}", String(s.aktive))
      .replace("{medtimer}", String(s.med_timer))
      .replace("{medplan}", String(s.med_plan))
      .replace("{funn}", String(s.apne_funn)) });
  // OVERTIDEN STÅR FOR SEG: det er den ene funntypen der noen alt har
  // jobbet timene, og der spørsmålet er om de er avtalt.
  if (s.apne_overtid > 0) {
    p.append(" ", el("strong", {
      text: t("ui.lonn.apne_overtid").replace("{n}",
                                              String(s.apne_overtid)) }));
  }
  if (!s.har_terskel) {
    p.append(" ", el("strong", { text: t("ui.lonn.ingen_terskler") }));
  }
  if (s.vist < s.takere) {
    p.append(" ", el("strong", {
      text: t("ui.lonn.avkortet").replace("{vist}", String(s.vist)) }));
  }
  return p;
}

export function visLonn(hoved, ctx) {
  const hode = () => flateHode(t("ui.lonn.tittel"),
    t("ui.lonn.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenRad = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const settApen = (id) => { apenRad = id; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/lonn"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const takere = d.takere || [];
      const detalj = detaljpanel(ctx, last, kvitter, settApen);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.lonn.oversikt.tittel") }),
        sammendrag(s),
        el("p", { class: "muted",
                  text: t("ui.lonn.oversikt.hvorfor") }));

      const liste = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.lonn.liste.tittel") }));
      if (!takere.length) {
        liste.append(el("p", { class: "muted",
          text: t("ui.lonn.liste.ingen") }));
      } else {
        liste.append(takerTabell(takere, detalj.apne));
      }

      const terskelseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.lonn.terskel.tittel") }));
      if (!d.terskler) {
        terskelseksjon.append(el("p", { class: "muted",
          text: t("ui.lonn.ingen_terskler") }));
      } else {
        terskelseksjon.append(
          el("p", { class: "muted",
            text: t("ui.lonn.terskel.versjon")
              .replace("{versjon}", String(d.terskler.versjon)) }),
          terskelTabell(d.terskler));
      }

      const deler = [oversikt, liste, terskelseksjon, detalj.node];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(takerSkjema(ctx, last, kvitter),
                   terskelSkjema(ctx, last, d.terskler, kvitter));
      }
      sett(kropp, ...deler);
      if (apenRad) {
        const rad = takere.find((x) => x.taker_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
