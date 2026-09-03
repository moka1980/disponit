// Betalings- og abonnementsstatusagenten (M-41 v1) — HISTORIKKEN.
//
// FLATENS VIKTIGSTE JOBB er å vise HVILKEN STATUS SOM GJALDT NÅR, OG
// HVOR DEN KOM FRA. En status uten kilde er en påstand, og
// `betaling_autorisert` ville hvilt på påstanden.
//
// DET FINNES INGEN «REFUNDER»-KNAPP, og fraværet er dommen:
// netthandelsmalen har `refusjon.utfor` stående som `modus: auto`,
// `reversering: irreversibel`, opp til 5000 kroner — gatet på denne
// modulen. En refusjon er penger ut døra og kan ikke kalles tilbake.
// `refundert` kan FØRES her, fordi en refusjon kan ha skjedd; den kan
// ikke UTLØSES.
//
// KORTNUMMERET VISES ALDRI. Det sendes én gang, til en dør som regner
// masken og kaster nummeret — og feltet tømmes etter innsending.
//
// BELØP I HELTALLSARITMETIKK (101s form, ordrett), og avviket regnes
// som `betalt − forventet` i ØRE, aldri som en prosent flaten fant på.
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, hentJson, nyIdempotensnokkel,
  registrerBetalingsstatus, registrerBetalingssubjekt,
  settAbonnementsstatus, settBetalingssubjektAktiv,
  settBetalingsterskler,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

const MERKE = {
  uavklart_betaling: "ui.betaling.merke_uavklart",
  belopsavvik: "ui.betaling.merke_avvik",
  autorisasjon_utlopt: "ui.betaling.merke_utlopt",
  ingen_terskel: "ui.betaling.merke_uten_terskel",
};

// LUKKEDE SETT. `refundert` og `tilbakefort` står her fordi de kan ha
// SKJEDD og skal kunne registreres — ikke fordi flaten utfører dem.
export const STATUSER = ["opprettet", "autorisert", "gjennomfort",
                         "feilet", "refundert", "tilbakefort"];
export const KILDER = ["leverandor", "avstemming", "manuell", "portal"];
export const ABONNEMENTSSTATUSER = ["aktivt", "pauset", "i_restanse",
                                    "avsluttet"];

const STATUSTEKST = {
  opprettet: "ui.betaling.status.opprettet",
  autorisert: "ui.betaling.status.autorisert",
  gjennomfort: "ui.betaling.status.gjennomfort",
  feilet: "ui.betaling.status.feilet",
  refundert: "ui.betaling.status.refundert",
  tilbakefort: "ui.betaling.status.tilbakefort",
};
const KILDETEKST = {
  leverandor: "ui.betaling.kilde.leverandor",
  avstemming: "ui.betaling.kilde.avstemming",
  manuell: "ui.betaling.kilde.manuell",
  portal: "ui.betaling.kilde.portal",
};
const ABOTEKST = {
  aktivt: "ui.betaling.abo.aktivt",
  pauset: "ui.betaling.abo.pauset",
  i_restanse: "ui.betaling.abo.i_restanse",
  avsluttet: "ui.betaling.abo.avsluttet",
};

// BELØP I HELTALLSARITMETIKK, aldri via `/100`.
export function belopTekst(ore) {
  if (typeof ore !== "number" || !Number.isInteger(ore)) return "—";
  const neg = ore < 0;
  const a = Math.abs(ore);
  return `${neg ? "-" : ""}${Math.trunc(a / 100)},`
    + `${String(a % 100).padStart(2, "0")}`;
}

// AVVIKET ER EN DIFFERANSE, ikke en prosent. Fortegnet skal ses: betalt
// for lite og betalt for mye er to helt forskjellige samtaler.
export function avvikTekst(belop, forventet) {
  if (typeof belop !== "number" || !Number.isInteger(belop)
      || typeof forventet !== "number" || !Number.isInteger(forventet)) {
    return t("ui.betaling.uten_forventet");
  }
  const d = belop - forventet;
  if (d === 0) return t("ui.betaling.uten_avvik");
  return t(d > 0 ? "ui.betaling.avvik_over" : "ui.betaling.avvik_under")
    .replace("{belop}", belopTekst(Math.abs(d)));
}

// STATUSENS ORD, med KILDEN. En status uten kilde er en påstand — og
// flaten viser derfor aldri den ene uten den andre.
export function statusTekst(status, kilde) {
  if (!status) return t("ui.betaling.uten_status");
  const s = t(STATUSTEKST[status] || "ui.betaling.status.ukjent");
  if (!kilde) return s;
  return t("ui.betaling.status_fra")
    .replace("{status}", s)
    .replace("{kilde}", t(KILDETEKST[kilde] || "ui.betaling.kilde.ukjent"));
}

// MASKEN, eller ordene for at ingen er ført.
export function maskeTekst(maske) {
  if (typeof maske !== "string" || !maske) {
    return t("ui.betaling.uten_middel");
  }
  return maske;
}

function subjektrad(s, apneDetalj) {
  const rad = el("tr", {});
  rad.append(el("th", { scope: "row", class: "celle-id",
                        text: s.ekstern_ref }));
  rad.append(el("td", { class: "celle-tekst", text: s.navn }));
  rad.append(el("td", { class: "celle-tekst",
                        text: statusTekst(s.status, s.kilde) }));
  rad.append(el("td", { class: "celle-tall",
    text: typeof s.belop_ore === "number"
      ? belopTekst(s.belop_ore) : "—" }));
  rad.append(el("td", { class: "celle-tekst",
    text: avvikTekst(s.belop_ore, s.forventet_ore) }));
  rad.append(el("td", { class: "celle-id",
                        text: maskeTekst(s.betalingsmiddel_maske) }));
  rad.append(el("td", { text: s.abonnementsstatus
    ? t(ABOTEKST[s.abonnementsstatus] || "ui.betaling.abo.ukjent")
    : t("ui.betaling.uten_abonnement") }));

  const merkecelle = el("td", {},
    el("span", { text: s.aktiv ? t("ui.betaling.status.aktiv")
                               : t("ui.betaling.status.inaktiv") }));
  for (const funn of s.apne_funn || []) {
    if (!MERKE[funn]) continue;
    // MERKET ER TEKST (WCAG 1.4.1).
    merkecelle.append(" ", el("strong", { class: "merke",
                                          text: t(MERKE[funn]) }));
  }
  rad.append(merkecelle);

  const handling = el("td", {});
  const knapp = el("button", { type: "button",
    text: t("ui.betaling.knapp.apne") });
  knapp.addEventListener("click", () => apneDetalj(s));
  handling.append(knapp);
  rad.append(handling);
  return rad;
}

function subjektTabell(subjekter, apneDetalj) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.betaling.liste.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.betaling.kolonne.ref") }),
    el("th", { scope: "col", text: t("ui.betaling.kolonne.navn") }),
    el("th", { scope: "col", text: t("ui.betaling.kolonne.status") }),
    el("th", { scope: "col", text: t("ui.betaling.kolonne.belop") }),
    el("th", { scope: "col", text: t("ui.betaling.kolonne.avvik") }),
    el("th", { scope: "col", text: t("ui.betaling.kolonne.middel") }),
    el("th", { scope: "col", text: t("ui.betaling.kolonne.abonnement") }),
    el("th", { scope: "col", text: t("ui.betaling.kolonne.merker") }),
    el("th", { scope: "col", text: t("ui.betaling.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const s of subjekter) tbody.append(subjektrad(s, apneDetalj));
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function terskelTabell(terskler) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.betaling.terskel.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.betaling.kolonne.terskel") }),
    el("th", { scope: "col", text: t("ui.betaling.kolonne.verdi") }))));
  const tbody = el("tbody");
  const linjer = [
    ["ui.betaling.terskel.uavklart",
     t("ui.betaling.dogn").replace("{dogn}",
                                   String(terskler.uavklart_dogn))],
    ["ui.betaling.terskel.avvik", belopTekst(terskler.belopsavvik_ore)],
    ["ui.betaling.terskel.reautorisasjon",
     t("ui.betaling.dogn").replace(
       "{dogn}", String(terskler.reautorisasjon_dogn))],
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
          ? t("ui.betaling.feil.tilstand")
          : t("ui.betaling.feil.generell") }));
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
  // ÅPNINGSTELLEREN (109s lærdom): åpner noen subjekt B mens As
  // historikk er underveis, ville As linjer blitt tegnet inn i Bs
  // panel — en betalingshistorikk som ser ut til å høre til en annen.
  let apningsnr = 0;

  const merkelinje = el("p", { class: "muted" });
  const historikk = el("div", {});

  // --- ny status ---
  const sSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const sStatus = velger("bt-st-status", "status", STATUSER, STATUSTEKST);
  const sBelop = el("input", { id: "bt-st-belop", name: "belop",
    type: "number", required: true, step: "0.01", min: "0" });
  const sForventet = el("input", { id: "bt-st-forventet",
    name: "forventet", type: "number", step: "0.01", min: "0" });
  const sMiddel = el("input", { id: "bt-st-middel", name: "middel",
    type: "text", maxlength: 64, autocomplete: "off",
    spellcheck: "false" });
  const sKilde = velger("bt-st-kilde", "kilde", KILDER, KILDETEKST);
  const sKildeRef = el("input", { id: "bt-st-kilderef",
    name: "kilde_ref", type: "text", required: true, maxlength: 100 });
  const sDato = el("input", { id: "bt-st-dato", name: "inntruffet",
    type: "date", required: true });
  const sNotat = el("input", { id: "bt-st-notat", name: "notat",
    type: "text", required: true, maxlength: 2000 });
  const sKnapp = el("button", { type: "submit",
    text: t("ui.betaling.knapp.ny_status") });
  sSkjema.append(
    felt("bt-st-status", "ui.betaling.skjema.status", sStatus,
         "ui.betaling.skjema.status_hjelp"),
    felt("bt-st-belop", "ui.betaling.skjema.belop", sBelop),
    felt("bt-st-forventet", "ui.betaling.skjema.forventet", sForventet,
         "ui.betaling.skjema.forventet_hjelp"),
    felt("bt-st-middel", "ui.betaling.skjema.middel", sMiddel,
         "ui.betaling.skjema.middel_hjelp"),
    felt("bt-st-kilde", "ui.betaling.skjema.kilde", sKilde,
         "ui.betaling.skjema.kilde_hjelp"),
    felt("bt-st-kilderef", "ui.betaling.skjema.kilde_ref", sKildeRef),
    felt("bt-st-dato", "ui.betaling.skjema.inntruffet", sDato,
         "ui.betaling.skjema.inntruffet_hjelp"),
    felt("bt-st-notat", "ui.betaling.skjema.notat", sNotat),
    el("div", { class: "skjema-bunn" }, sKnapp));
  skjemaramme(ctx, last, {
    skjema: sSkjema, knapp: sKnapp, utfall, kvitter,
    okNokkel: "ui.betaling.skjema.status_ok",
    send: (idem) => registrerBetalingsstatus(gjeldende.subjekt_id, {
      status: sStatus.value, belop_ore: tilOre(sBelop.value),
      forventet_ore: tilOre(sForventet.value), valuta: "NOK",
      betalingsmiddel: sMiddel.value.trim() || null,
      kilde: sKilde.value, kilde_ref: sKildeRef.value,
      inntruffet: sDato.value, notat: sNotat.value,
    }, idem),
    // MIDDELET TØMMES. Det skal ikke bli stående i skjermbildet etter
    // at basen har regnet masken og kastet det.
    tilbakestill: () => {
      sMiddel.value = ""; sKildeRef.value = ""; sNotat.value = "";
    },
  });

  // --- abonnement ---
  const aSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const aStatus = velger("bt-ab-status", "status",
                         ABONNEMENTSSTATUSER, ABOTEKST);
  const aFra = el("input", { id: "bt-ab-fra", name: "gyldig_fra",
    type: "date", required: true });
  const aGrunn = el("input", { id: "bt-ab-grunn", name: "begrunnelse",
    type: "text", required: true, maxlength: 2000 });
  const aKnapp = el("button", { type: "submit",
    text: t("ui.betaling.knapp.nytt_abonnement") });
  aSkjema.append(
    felt("bt-ab-status", "ui.betaling.skjema.abo_status", aStatus),
    felt("bt-ab-fra", "ui.betaling.skjema.gyldig_fra", aFra,
         "ui.betaling.skjema.gyldig_fra_hjelp"),
    felt("bt-ab-grunn", "ui.betaling.skjema.begrunnelse", aGrunn,
         "ui.betaling.skjema.begrunnelse_hjelp"),
    el("div", { class: "skjema-bunn" }, aKnapp));
  skjemaramme(ctx, last, {
    skjema: aSkjema, knapp: aKnapp, utfall, kvitter,
    okNokkel: "ui.betaling.skjema.abo_ok",
    send: (idem) => settAbonnementsstatus(gjeldende.subjekt_id, {
      status: aStatus.value, gyldig_fra: aFra.value,
      begrunnelse: aGrunn.value,
    }, idem),
    tilbakestill: () => { aGrunn.value = ""; },
  });

  // --- aktiv/inaktiv ---
  const kSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const kKnapp = el("button", { type: "submit",
    text: t("ui.betaling.knapp.deaktiver") });
  kSkjema.append(
    el("p", { class: "muted",
              text: t("ui.betaling.skjema.aktiv_hjelp") }),
    el("div", { class: "skjema-bunn" }, kKnapp));
  skjemaramme(ctx, last, {
    skjema: kSkjema, knapp: kKnapp, utfall, kvitter,
    okNokkel: "ui.betaling.skjema.aktiv_ok",
    send: (idem) => settBetalingssubjektAktiv(gjeldende.subjekt_id,
                                              !gjeldende.aktiv, idem),
    tilbakestill: () => { settApen(null); innhold.hidden = true; },
  });

  const skriver = harScope(ctx, "bestilling:opprett");
  innhold.append(el("h3", { text: t("ui.betaling.detalj.tittel") }),
    merkelinje, historikk);
  if (skriver) {
    innhold.append(
      el("h4", { text: t("ui.betaling.skjema.status_tittel") }), sSkjema,
      el("h4", { text: t("ui.betaling.skjema.abo_tittel") }), aSkjema,
      el("h4", { text: t("ui.betaling.skjema.aktiv_tittel") }), kSkjema);
  }
  boks.append(innhold, utfall);
  innhold.hidden = true;

  function historikkTabell(rader) {
    const tb = el("table", { class: "kpi-tabell" },
      el("caption", { text: t("ui.betaling.historikk.caption") }));
    tb.append(el("thead", {}, el("tr", {},
      el("th", { scope: "col",
                 text: t("ui.betaling.kolonne.inntruffet") }),
      el("th", { scope: "col", text: t("ui.betaling.kolonne.status") }),
      el("th", { scope: "col", text: t("ui.betaling.kolonne.belop") }),
      el("th", { scope: "col", text: t("ui.betaling.kolonne.middel") }),
      el("th", { scope: "col",
                 text: t("ui.betaling.kolonne.kilde_ref") }),
      el("th", { scope: "col", text: t("ui.betaling.kolonne.notat") }))));
    const tbody = el("tbody");
    for (const h of rader) {
      const rad = el("tr", {});
      rad.append(el("th", { scope: "row", class: "celle-id",
                            text: h.inntruffet }));
      const statuscelle = el("td", { class: "celle-tekst" },
        el("span", { text: statusTekst(h.status, h.kilde) }));
      // SKIFTET ER MERKET, MED ORD.
      if (h.endret) {
        statuscelle.append(" ", el("strong", { class: "merke",
          text: t("ui.betaling.merke_skifte") }));
      }
      rad.append(statuscelle);
      rad.append(el("td", { class: "celle-tall",
                            text: belopTekst(h.belop_ore) }));
      const middelcelle = el("td", { class: "celle-id" },
        el("span", { text: maskeTekst(h.betalingsmiddel_maske) }));
      // …og et BYTTE AV BETALINGSMIDDEL likeså. Det er grunnlaget
      // `samme_betalingsmiddel` en dag skal hvile på.
      if (h.middel_endret) {
        middelcelle.append(" ", el("strong", { class: "merke",
          text: t("ui.betaling.merke_middelskifte") }));
      }
      rad.append(middelcelle);
      rad.append(el("td", { class: "celle-id", text: h.kilde_ref }));
      rad.append(el("td", { class: "celle-tekst", text: h.notat }));
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
      settApen(s.subjekt_id);
      sett(utfall);
      sett(historikk);
      merkelinje.textContent = `${s.ekstern_ref} · ${s.navn}`;
      kKnapp.textContent = t(s.aktiv ? "ui.betaling.knapp.deaktiver"
                                     : "ui.betaling.knapp.aktiver");
      // ET DEAKTIVERT SUBJEKT TAR IKKE IMOT NYE STATUSER — men det KAN
      // aktiveres igjen, så den knappen står levende.
      sKnapp.disabled = !s.aktiv;
      aKnapp.disabled = !s.aktiv;
      innhold.hidden = false;
      let d;
      try {
        d = await hentJson(
          `/v1/betaling/${encodeURIComponent(s.subjekt_id)}/historikk`);
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        if (nr !== apningsnr) return;
        sett(historikk, el("p", { class: "muted",
          text: t("ui.betaling.feil.generell") }));
        return;
      }
      if (nr !== apningsnr) return;
      const liste = d.hendelser || [];
      if (!liste.length) {
        sett(historikk, el("p", { class: "muted",
          text: t("ui.betaling.detalj.ingen") }));
        return;
      }
      sett(historikk, historikkTabell(liste));
    },
  };
}

// KRONER INN, ØRE UT. `Math.round` på produktet er den ENESTE veien.
// Et tomt felt er `null`, ikke null kroner — «ingen forventning ført»
// og «forventet null» er to helt forskjellige svar.
export function tilOre(verdi) {
  if (verdi === "" || verdi === null || verdi === undefined) return null;
  const n = Number(verdi);
  if (!Number.isFinite(n)) return null;
  return Math.round(n * 100);
}

function subjektSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const ref = el("input", { id: "bt-ny-ref", name: "ekstern_ref",
    type: "text", required: true, maxlength: 100 });
  const navn = el("input", { id: "bt-ny-navn", name: "navn",
    type: "text", required: true, maxlength: 300 });
  const knapp = el("button", { type: "submit",
    text: t("ui.betaling.knapp.nytt_subjekt") });
  skjema.append(
    felt("bt-ny-ref", "ui.betaling.skjema.ref", ref,
         "ui.betaling.skjema.ref_hjelp"),
    felt("bt-ny-navn", "ui.betaling.skjema.navn", navn),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.betaling.skjema.subjekt_ok",
    send: (idem) => registrerBetalingssubjekt({
      ekstern_ref: ref.value, navn: navn.value }, idem),
    tilbakestill: () => { ref.value = ""; navn.value = ""; },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.betaling.skjema.subjekt_tittel") }), skjema,
    utfall);
}

function terskelSkjema(ctx, last, terskler, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const uavklart = el("input", { id: "bt-t-uavklart", name: "uavklart",
    type: "number", required: true, step: "1", min: "0", max: "3650" });
  const avvik = el("input", { id: "bt-t-avvik", name: "avvik",
    type: "number", required: true, step: "0.01", min: "0" });
  const reaut = el("input", { id: "bt-t-reaut", name: "reaut",
    type: "number", required: true, step: "1", min: "0", max: "3650" });
  if (terskler) {
    uavklart.value = String(terskler.uavklart_dogn);
    avvik.value = (terskler.belopsavvik_ore / 100).toFixed(2);
    reaut.value = String(terskler.reautorisasjon_dogn);
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.betaling.knapp.lagre_terskler") });
  skjema.append(
    felt("bt-t-uavklart", "ui.betaling.terskel.uavklart", uavklart,
         "ui.betaling.terskel.uavklart_hjelp"),
    felt("bt-t-avvik", "ui.betaling.terskel.avvik", avvik,
         "ui.betaling.terskel.avvik_hjelp"),
    felt("bt-t-reaut", "ui.betaling.terskel.reautorisasjon", reaut,
         "ui.betaling.terskel.reautorisasjon_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.betaling.skjema.terskel_ok",
    send: (idem) => settBetalingsterskler({
      uavklart_dogn: Number(uavklart.value),
      belopsavvik_ore: tilOre(avvik.value),
      reautorisasjon_dogn: Number(reaut.value),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.betaling.terskel.tittel") }), skjema, utfall);
}

function sammendrag(s) {
  const p = el("p", {
    text: t("ui.betaling.sammendrag")
      .replace("{aktive}", String(s.aktive))
      .replace("{medstatus}", String(s.med_status))
      .replace("{gjennomforte}", String(s.gjennomforte))
      .replace("{funn}", String(s.apne_funn)) });
  // AVVIKENE STÅR FOR SEG: et beløpsavvik er den ene funntypen der
  // pengene alt er borte.
  if (s.apne_avvik > 0) {
    p.append(" ", el("strong", {
      text: t("ui.betaling.apne_avvik").replace("{n}",
                                                String(s.apne_avvik)) }));
  }
  if (!s.har_terskel) {
    p.append(" ", el("strong", { text: t("ui.betaling.ingen_terskler") }));
  }
  if (s.vist < s.subjekter) {
    p.append(" ", el("strong", {
      text: t("ui.betaling.avkortet").replace("{vist}",
                                              String(s.vist)) }));
  }
  return p;
}

export function visBetaling(hoved, ctx) {
  const hode = () => flateHode(t("ui.betaling.tittel"),
    t("ui.betaling.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenRad = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const settApen = (id) => { apenRad = id; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/betaling"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const subjekter = d.subjekter || [];
      const detalj = detaljpanel(ctx, last, kvitter, settApen);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.betaling.oversikt.tittel") }),
        sammendrag(s),
        el("p", { class: "muted",
                  text: t("ui.betaling.oversikt.hvorfor") }));

      const liste = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.betaling.liste.tittel") }));
      if (!subjekter.length) {
        liste.append(el("p", { class: "muted",
          text: t("ui.betaling.liste.ingen") }));
      } else {
        liste.append(subjektTabell(subjekter, detalj.apne));
      }

      const terskelseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.betaling.terskel.tittel") }));
      if (!d.terskler) {
        terskelseksjon.append(el("p", { class: "muted",
          text: t("ui.betaling.ingen_terskler") }));
      } else {
        terskelseksjon.append(
          el("p", { class: "muted",
            text: t("ui.betaling.terskel.versjon")
              .replace("{versjon}", String(d.terskler.versjon)) }),
          terskelTabell(d.terskler));
      }

      const deler = [oversikt, liste, terskelseksjon, detalj.node];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(subjektSkjema(ctx, last, kvitter),
                   terskelSkjema(ctx, last, d.terskler, kvitter));
      }
      sett(kropp, ...deler);
      if (apenRad) {
        const rad = subjekter.find((x) => x.subjekt_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
