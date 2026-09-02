// Kunde-onboardingagenten (M-18 v1) — ONBOARDINGREGISTERET.
//
// FLATENS VIKTIGSTE JOBB er å vise hvor et løp STÅR: hvor mange steg som
// er gjort av hvor mange, hva som ventes på nå, og hvilke løp som står
// stille. Som TEKST, ikke bare farge (WCAG 1.4.1): «Står stille» og
// «3 av 5» er ord.
//
// FLATEN VISER, DEN REGNER IKKE. `gjort`, `totalt`, `neste_steg`,
// `alder_dogn`, `dogn_over_frist` og `blokkert` er regnet i BASEN, i
// samme skann som raden (103s lesedører), nettopp for at flaten verken
// skal telle en liste den ikke har eller trekke to datoer fra hverandre
// (M-16-regelen).
//
// `blokkert` ER GRUNNEN TIL AT KNAPPEN IKKE LYVER. Et obligatorisk steg
// kan ikke gjøres før et lavere nummerert obligatorisk steg er gjort —
// vakten i 103 feller dommen, og flaten deaktiverer knappen fordi den
// ellers ville lovet noe serveren avviser med 409. Det er ergonomi, ikke
// sikkerhet: regelen bor i basen.
//
// DET FINNES INGEN «PROVISJONER»-KNAPP, og fraværet er dommen: katalogen
// lover 0 minutter per ny kunde, v1 registrerer løpet. Undertittelen sier
// det.
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] på kolonnene
// og th[scope=row] på cellen som navngir raden. `.tablewrap` er
// sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, avsluttOnboardinglop, fullforSteg, hentJson,
  nyIdempotensnokkel, registrerOnboardingmal, settMalsteg,
  startOnboardinglop,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// FUNNTYPE → MERKETEKST. Lukket tabell, ikke strengbygging: en funntype
// flaten ikke kjenner skal ikke bli et merke som heter
// «ui.onboarding.merke_<noe>» på skjermen.
const MERKE = {
  stoppet_lop: "ui.onboarding.merke_stoppet",
  steg_over_frist: "ui.onboarding.merke_forsinket",
  lop_uten_aktiv_eier: "ui.onboarding.merke_uten_eier",
};

// Fristkolonnens ORD. Ett tall inn, én setning ut.
//
// ENTALL HAR SIN EGEN NØKKEL (M-21/M-34/M-13/M-17-lærdommen):
// locale-settet har ingen pluralmaskineri, og «1 days past due» ville
// stått på nøyaktig den raden et menneske leser først.
//
// `null` ER IKKE «NULL DØGN»: et FULLFØRT steg har ingen løpende frist,
// og lesedøren gir NULL for nettopp det. En tom celle er det ærlige
// svaret der — ikke «forfaller i dag».
export function fristTekst(dogn) {
  if (typeof dogn !== "number") return "—";
  if (dogn > 0) {
    return dogn === 1
      ? t("ui.onboarding.frist_ett_dogn_over")
      : t("ui.onboarding.frist_over").replace("{dogn}", String(dogn));
  }
  if (dogn === 0) return t("ui.onboarding.frist_i_dag");
  const n = Math.abs(dogn);
  return n === 1
    ? t("ui.onboarding.frist_om_ett")
    : t("ui.onboarding.frist_om").replace("{dogn}", String(n));
}

export function framdriftTekst(l) {
  return t("ui.onboarding.framdrift")
    .replace("{gjort}", String(l.gjort))
    .replace("{totalt}", String(l.totalt));
}

function eierTekst(l) {
  // Visningsnavnet der IdP-en ga et; bruker-id-en ellers. En tom celle
  // ville vært det eneste svaret som ikke lar noen finne eieren.
  return l.eier_navn || l.eier_bruker_id || t("ui.onboarding.ukjent_eier");
}

function lopsrad(l, ctx, apneDetalj) {
  const rad = el("tr", {});
  // KUNDEN NAVNGIR raden. `celle-tekst` på selve <td>-en, ikke på et
  // <span> inni: `max-width` gjør ingenting på et inline-element.
  rad.append(el("th", { scope: "row", class: "celle-tekst",
                        text: l.kunde_ref }));
  rad.append(el("td", { class: "celle-tekst",
                        text: `${l.mal_navn} v${l.mal_versjon}` }));
  rad.append(el("td", { text: l.startet }));
  rad.append(el("td", { class: "celle-tall", text: framdriftTekst(l) }));
  rad.append(el("td", { class: "celle-tekst",
    text: l.neste_steg || t("ui.onboarding.ingen_neste") }));

  const eiercelle = el("td", {}, el("span", { text: eierTekst(l) }));
  if (l.eier_aktiv === false) {
    // EIEREN HAR SLUTTET. Sveipen reiser funnet i natt, men flaten skal
    // ikke vente på den for å si det — og det står som ORD.
    eiercelle.append(" ", el("strong", { class: "merke",
      text: t("ui.onboarding.merke_uten_eier") }));
  }
  rad.append(eiercelle);

  const statuscelle = el("td", {},
    el("span", { text: t(`ui.onboarding.status.${l.status}`) }));
  for (const funn of l.apne_funn || []) {
    // MERKENE ER TEKST. Dette er flatens viktigste opplysning på raden.
    // `lop_uten_aktiv_eier` står alt i eierkolonnen — å gjenta det her
    // ville vært to merker om den samme tingen på samme linje.
    if (MERKE[funn] && funn !== "lop_uten_aktiv_eier") {
      statuscelle.append(" ", el("strong", { class: "merke",
        text: t(MERKE[funn]) }));
    }
  }
  rad.append(statuscelle);

  const handling = el("td", {});
  const knapp = el("button", { type: "button",
    text: t("ui.onboarding.knapp.apne") });
  knapp.addEventListener("click", () => apneDetalj(l));
  handling.append(knapp);
  rad.append(handling);
  return rad;
}

function lopsTabell(lop, ctx, apneDetalj) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.onboarding.lop.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.onboarding.kolonne.kunde") }),
    el("th", { scope: "col", text: t("ui.onboarding.kolonne.mal") }),
    el("th", { scope: "col", text: t("ui.onboarding.kolonne.startet") }),
    el("th", { scope: "col",
               text: t("ui.onboarding.kolonne.framdrift") }),
    el("th", { scope: "col", text: t("ui.onboarding.kolonne.neste") }),
    el("th", { scope: "col", text: t("ui.onboarding.kolonne.eier") }),
    el("th", { scope: "col", text: t("ui.onboarding.kolonne.status") }),
    el("th", { scope: "col",
               text: t("ui.onboarding.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const l of lop) tbody.append(lopsrad(l, ctx, apneDetalj));
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function malTabell(maler) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.onboarding.maler.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.onboarding.kolonne.mal") }),
    el("th", { scope: "col", text: t("ui.onboarding.kolonne.steg") }),
    el("th", { scope: "col",
               text: t("ui.onboarding.kolonne.status") }))));
  const tbody = el("tbody");
  for (const m of maler) {
    const rad = el("tr", {});
    rad.append(el("th", { scope: "row", class: "celle-tekst",
                          text: `${m.navn} v${m.versjon}` }));
    rad.append(el("td", { class: "celle-tall",
      text: t("ui.onboarding.maler.antall_steg")
        .replace("{antall}", String(m.antall_steg)) }));
    // FLATEN SIER HVORFOR malen ikke kan endres, i stedet for å la
    // brukeren møte vaktens feilmelding etter å ha fylt ut skjemaet.
    rad.append(el("td", { class: "celle-tekst",
      text: m.paagaende_lop
        ? t("ui.onboarding.maler.laast")
          .replace("{antall}", String(m.paagaende_lop))
        : "—" }));
    tbody.append(rad);
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
                                  tilbakestill, okNokkel }) {
  // Én nøkkel per intensjon (PR-014 R1): nullstilles når innholdet
  // endres, og ved 4xx — et avvist forsøk har FORBRUKT nøkkelen.
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
          ? t("ui.onboarding.feil.tilstand")
          : t("ui.onboarding.feil.generell") }));
      return;
    }
    idem = null;
    knapp.disabled = false;
    if (tilbakestill) tilbakestill();
    meldLive(t(okNokkel));
    sett(utfall, el("span", { text: t(okNokkel) }));
    last();
  });
}

// STEGENE SKRIVES SOM LINJER, ikke som JSON. Et JSON-felt i en flate er
// et felt bare den som skrev API-et kan fylle ut — og malen er nettopp
// det en kundeansvarlig skal kunne sette opp selv.
//
// Formen er `navn | beskrivelse | døgn | V`, der V-en betyr VALGFRITT
// steg og kan utelates. Merket er på det valgfrie og ikke på det
// obligatoriske, fordi standarden er den strenge: et steg man glemte å
// merke skal blokkere, ikke stilltiende hoppes over.
// Parseren er EKSPORTERT for at porten skal kunne måle den uten å tegne
// en skjerm.
export function parseSteglinjer(tekst) {
  const ut = [];
  for (const raa of String(tekst || "").split("\n")) {
    const linje = raa.trim();
    if (!linje) continue;
    const deler = linje.split("|").map((d) => d.trim());
    if (deler.length < 3) return null;
    const dogn = Number(deler[2]);
    if (!deler[0] || !deler[1] || !Number.isInteger(dogn) || dogn < 0) {
      return null;
    }
    ut.push({
      navn: deler[0], beskrivelse: deler[1], frist_dogn: dogn,
      // ALT ER OBLIGATORISK MED MINDRE NOEN SIER NOE ANNET. Standarden
      // er den strenge: et steg man glemte å merke skal blokkere, ikke
      // stilltiende hoppes over.
      obligatorisk: deler.length < 4 || deler[3].toUpperCase() !== "V",
    });
  }
  return ut.length ? ut : null;
}

// DETALJPANELET: løpets steg, med fullføringsknapp per steg.
function detaljpanel(ctx, last) {
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;

  const overskrift = el("h3", { text: t("ui.onboarding.detalj.tittel") });
  const merkelinje = el("p", { class: "muted" });
  const stegliste = el("div", {});
  const avbrytSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const avbrytGrunn = el("input", { id: "ob-avbryt", name: "begrunnelse",
    type: "text", required: true, maxlength: 2000 });
  const avbrytKnapp = el("button", { type: "submit",
    text: t("ui.onboarding.knapp.avslutt_avbrutt") });
  avbrytSkjema.append(
    felt("ob-avbryt", "ui.onboarding.skjema.avbryt_begrunnelse",
         avbrytGrunn, "ui.onboarding.skjema.avbryt_hjelp"),
    el("div", { class: "skjema-bunn" }, avbrytKnapp));
  skjemaramme(ctx, last, {
    skjema: avbrytSkjema, knapp: avbrytKnapp, utfall,
    okNokkel: "ui.onboarding.avslutt_ok",
    send: (idem) => avsluttOnboardinglop(gjeldende.lop_id, "avbrutt",
                                         avbrytGrunn.value, idem),
    tilbakestill: () => {
      avbrytGrunn.value = ""; innhold.hidden = true;
    },
  });

  const fullforKnapp = el("button", { type: "button",
    text: t("ui.onboarding.knapp.avslutt_fullfort") });
  fullforKnapp.addEventListener("click", async () => {
    fullforKnapp.disabled = true;
    try {
      await avsluttOnboardinglop(gjeldende.lop_id, "fullfort", null,
                                 nyIdempotensnokkel());
    } catch (e) {
      fullforKnapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.onboarding.feil.tilstand")
          : t("ui.onboarding.feil.generell") }));
      return;
    }
    fullforKnapp.disabled = false;
    innhold.hidden = true;
    meldLive(t("ui.onboarding.avslutt_ok"));
    sett(utfall, el("span", { text: t("ui.onboarding.avslutt_ok") }));
    last();
  });

  const skriver = harScope(ctx, "bestilling:opprett");
  innhold.append(overskrift, merkelinje, stegliste);
  if (skriver) {
    innhold.append(el("div", { class: "skjema-bunn" }, fullforKnapp),
      avbrytSkjema);
  }
  boks.append(innhold, utfall);
  innhold.hidden = true;

  function stegrad(s) {
    const rad = el("tr", {});
    rad.append(el("th", { scope: "row", class: "celle-tekst",
      text: `${s.steg_nr}. ${s.navn}` }));
    rad.append(el("td", { class: "celle-tekst", text: s.beskrivelse }));
    rad.append(el("td", { text: s.eier_navn || s.eier_bruker_id }));

    const fristcelle = el("td", {},
      el("span", { text: fristTekst(s.dogn_over_frist) }));
    fristcelle.append(" ", el("span", { class: "muted",
      text: t(s.obligatorisk ? "ui.onboarding.merke_obligatorisk"
                             : "ui.onboarding.merke_valgfritt") }));
    rad.append(fristcelle);

    const handling = el("td", {});
    if (s.fullfort_ts) {
      handling.append(el("span", {
        text: t("ui.onboarding.detalj.gjort_av")
          .replace("{av}", s.fullfort_av) }));
    } else if (s.blokkert) {
      // BLOKKERT SIES SOM ORD, ikke som en grå knapp uten forklaring.
      // Uten setningen ville brukeren trodd flaten var i stykker.
      handling.append(el("strong", { class: "merke",
        text: t("ui.onboarding.merke_blokkert") }));
    } else if (skriver) {
      const knapp = el("button", { type: "button",
        text: t("ui.onboarding.knapp.fullfor") });
      knapp.addEventListener("click", async () => {
        knapp.disabled = true;
        try {
          await fullforSteg(gjeldende.lop_id, s.steg_nr, null,
                            nyIdempotensnokkel());
        } catch (e) {
          knapp.disabled = false;
          if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
          sett(utfall, el("span", { role: "alert",
            text: e && e.status === 409
              ? t("ui.onboarding.feil.tilstand")
              : t("ui.onboarding.feil.generell") }));
          return;
        }
        meldLive(t("ui.onboarding.steg_ok"));
        last();
      });
      handling.append(knapp);
    }
    rad.append(handling);
    return rad;
  }

  return {
    node: boks,
    async apne(l) {
      gjeldende = l;
      sett(utfall);
      sett(stegliste);
      merkelinje.textContent = `${l.kunde_ref} · ${l.mal_navn} `
        + `v${l.mal_versjon} · ${framdriftTekst(l)}`;
      innhold.hidden = false;
      let d;
      try {
        d = await hentJson(
          `/v1/onboarding/lop/${encodeURIComponent(l.lop_id)}/steg`);
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        sett(utfall, el("span", { role: "alert",
          text: t("ui.onboarding.feil.generell") }));
        return;
      }
      const tb = el("table", { class: "kpi-tabell" },
        el("caption", { text: t("ui.onboarding.detalj.tittel") }));
      tb.append(el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.onboarding.kolonne.steg") }),
        el("th", { scope: "col",
                   text: t("ui.onboarding.kolonne.mal") }),
        el("th", { scope: "col", text: t("ui.onboarding.kolonne.eier") }),
        el("th", { scope: "col", text: t("ui.onboarding.kolonne.frist") }),
        el("th", { scope: "col",
                   text: t("ui.onboarding.kolonne.handling") }))));
      const tbody = el("tbody");
      for (const s of d.steg || []) tbody.append(stegrad(s));
      tb.append(tbody);
      sett(stegliste, el("div", { class: "tablewrap" }, tb));
    },
  };
}

function malSkjema(ctx, last) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const navn = el("input", { id: "ob-mal-navn", name: "navn",
    type: "text", required: true, maxlength: 200 });
  const knapp = el("button", { type: "submit",
    text: t("ui.onboarding.knapp.ny_mal") });
  skjema.append(
    felt("ob-mal-navn", "ui.onboarding.skjema.mal_navn", navn),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall,
    okNokkel: "ui.onboarding.skjema.mal_ok",
    send: (idem) => registrerOnboardingmal({ navn: navn.value }, idem),
    tilbakestill: () => { navn.value = ""; },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.onboarding.skjema.mal_tittel") }),
    skjema, utfall);
}

function stegSkjema(ctx, last, maler) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const mal = el("select", { id: "ob-steg-mal", name: "mal_id",
    required: true });
  for (const m of maler) {
    // MALER MED PÅGÅENDE LØP KAN IKKE ENDRES (vakten i 103). De står i
    // nedtrekket med grunnen, i stedet for å forsvinne — en mal som
    // bare er borte, leses som at noen slettet den.
    mal.append(el("option", { value: m.mal_id,
      text: m.paagaende_lop
        ? `${m.navn} — ${t("ui.onboarding.maler.laast")
            .replace("{antall}", String(m.paagaende_lop))}`
        : m.navn }));
  }
  const linjer = el("textarea", { id: "ob-steg-linjer", name: "steg",
    required: true, rows: "6" });
  const knapp = el("button", { type: "submit",
    text: t("ui.onboarding.knapp.lagre_steg") });
  if (!maler.length) knapp.disabled = true;
  skjema.append(
    felt("ob-steg-mal", "ui.onboarding.skjema.steg_mal", mal),
    felt("ob-steg-linjer", "ui.onboarding.skjema.steg_json", linjer,
         "ui.onboarding.skjema.steg_jsonhjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall,
    okNokkel: "ui.onboarding.skjema.steg_ok",
    send: (idem) => {
      const steg = parseSteglinjer(linjer.value);
      if (!steg) {
        // FORMATFEILEN FANGES HER, med en setning om hva som mangler.
        // Sendt videre ville den blitt 400 «request_feilformet», og
        // brukeren ville ikke visst hvilken linje som var gal.
        const feil = new Error("format");
        feil.status = 400;
        feil.lokal = true;
        throw feil;
      }
      return settMalsteg(mal.value, steg, idem);
    },
    tilbakestill: () => { linjer.value = ""; },
  });
  // Den lokale formatfeilen får sin EGEN setning; rammen over ville
  // sagt «noe gikk galt», som er sant og ubrukelig.
  skjema.addEventListener("submit", () => {
    if (linjer.value && !parseSteglinjer(linjer.value)) {
      sett(utfall, el("span", { role: "alert",
        text: t("ui.onboarding.skjema.steg_feil") }));
    }
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.onboarding.skjema.steg_tittel") }),
    skjema, utfall);
}

function startSkjema(ctx, last, maler) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const mal = el("select", { id: "ob-start-mal", name: "mal_id",
    required: true });
  for (const m of maler) {
    // Bare maler med steg kan starte et løp — døren avviser resten, og
    // et valg som garantert gir 409 hører ikke hjemme i nedtrekket.
    if (m.aktiv && m.antall_steg > 0) {
      mal.append(el("option", { value: m.mal_id,
        text: `${m.navn} (${t("ui.onboarding.maler.antall_steg")
          .replace("{antall}", String(m.antall_steg))})` }));
    }
  }
  const kunde = el("input", { id: "ob-start-kunde", name: "kunde_ref",
    type: "text", required: true, maxlength: 300 });
  const eier = el("input", { id: "ob-start-eier", name: "eier_bruker_id",
    type: "text", required: true, maxlength: 128 });
  const dato = el("input", { id: "ob-start-dato", name: "startet",
    type: "date", required: true });
  const knapp = el("button", { type: "submit",
    text: t("ui.onboarding.knapp.start") });
  if (!mal.options.length) knapp.disabled = true;
  skjema.append(
    felt("ob-start-mal", "ui.onboarding.skjema.start_mal", mal),
    felt("ob-start-kunde", "ui.onboarding.skjema.start_kunde", kunde),
    // EIEREN VELGES EKSPLISITT — feltet er påkrevd og har ingen
    // forhåndsutfylt verdi. En flate som stille satte innloggeren som
    // eier ville gjort «løp uten eier» sann på papiret og falsk i
    // praksis (M-34s form).
    felt("ob-start-eier", "ui.onboarding.skjema.start_eier", eier,
         "ui.onboarding.skjema.start_eierhjelp"),
    felt("ob-start-dato", "ui.onboarding.skjema.start_dato", dato),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall,
    okNokkel: "ui.onboarding.skjema.start_ok",
    send: (idem) => startOnboardinglop({
      mal_id: mal.value, kunde_ref: kunde.value,
      eier_bruker_id: eier.value, startet: dato.value,
    }, idem),
    tilbakestill: () => { kunde.value = ""; eier.value = ""; },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.onboarding.skjema.start_tittel") }),
    skjema, utfall);
}

// Sammendraget over tabellene. TALLENE KOMMER FRA SIN EGEN DØR og
// gjelder ALT — ikke bare det listen viser.
function sammendrag(s) {
  const p = el("p", {
    text: t("ui.onboarding.sammendrag")
      .replace("{paagaende}", String(s.paagaende))
      .replace("{stoppede}", String(s.stoppede))
      .replace("{fullforte}", String(s.fullforte))
      .replace("{avbrutte}", String(s.avbrutte))
      .replace("{maler}", String(s.maler)) });
  // AVKORTINGEN SIES HØYT. Uten dette ville flaten sett komplett ut
  // nettopp når den var det minst.
  const totalt = s.paagaende + s.fullforte + s.avbrutte;
  if (s.vist < totalt) {
    p.append(" ", el("strong", {
      text: t("ui.onboarding.avkortet")
        .replace("{vist}", String(s.vist)) }));
  }
  return p;
}

export function visOnboarding(hoved, ctx) {
  const hode = () => flateHode(t("ui.onboarding.tittel"),
    t("ui.onboarding.undertittel"));
  sett(hoved, ...hode());
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kropp);
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/onboarding"),
    (d) => {
      sett(hoved, ...hode(), kropp);
      const s = d.sammendrag || {};
      const lop = d.lop || [];
      const maler = d.maler || [];
      const detalj = detaljpanel(ctx, last);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.onboarding.oversikt.tittel") }),
        sammendrag(s));

      const lopseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.onboarding.lop.tittel") }));
      if (!lop.length) {
        lopseksjon.append(el("p", { class: "muted",
          text: t("ui.onboarding.lop.ingen") }));
      } else {
        lopseksjon.append(lopsTabell(lop, ctx, detalj.apne));
      }

      const malseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.onboarding.maler.tittel") }));
      if (!maler.length) {
        // ÆRLIG TOMTILSTAND: uten en mal kan ingen starte et løp, og
        // setningen sier hvorfor i stedet for å vise en tom tabell.
        malseksjon.append(el("p", { class: "muted",
          text: t("ui.onboarding.maler.ingen") }));
      } else {
        malseksjon.append(malTabell(maler));
      }

      const deler = [oversikt, lopseksjon, malseksjon, detalj.node];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(startSkjema(ctx, last, maler), malSkjema(ctx, last),
                   stegSkjema(ctx, last, maler));
      }
      sett(kropp, ...deler);
    });
  last();
}
