// Prisbok- og tilbudsagenten (M-26 v1) — PRISBOKA.
//
// FLATENS VIKTIGSTE JOBB er å svare på HVA SOM STO I BOKA DEN DAGEN.
// `priser_fra_prisbok` er en attestasjon om at et tilbud siterte boka,
// og den er verdiløs hvis ingen kan slå opp versjonen som gjaldt da.
// Derfor er prishistorikken en førsteklasses skjerm, ikke en detalj.
//
// DET FINNES INGEN «GENERER TILBUD»-KNAPP, og fraværet er dommen: alle
// tre bransjemalene navngir modulen som `v_prisbok` og bruker
// attestasjonen til å la `tilbud.generer` gå automatisk. Et tilbud er et
// bindende utspill mot en kunde; v1 er boka.
//
// OG FLATEN SETTER INGEN PRIS. `listepris_ore` er tallet et menneske
// skriver — flaten ganger ikke, indekserer ikke og foreslår ingenting.
//
// BELØP FORMATERES I HELTALLSARITMETIKK (101s form, ordrett).
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, hentJson, nyIdempotensnokkel, registrerProdukt,
  settKlausul, settPris, settPrisbokterskler, settProduktAktiv,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// FUNNTYPE → MERKETEKST. Lukket tabell, ikke strengbygging.
const MERKE = {
  uten_gyldig_pris: "ui.prisbok.merke_uten_pris",
  pris_utloper_snart: "ui.prisbok.merke_utloper",
  ingen_terskel: "ui.prisbok.merke_uten_terskel",
};

// BELØP I HELTALLSARITMETIKK, aldri via `/100`.
export function belopTekst(ore) {
  if (typeof ore !== "number" || !Number.isInteger(ore)) return "—";
  const neg = ore < 0;
  const a = Math.abs(ore);
  return `${neg ? "-" : ""}${Math.trunc(a / 100)},`
    + `${String(a % 100).padStart(2, "0")}`;
}

// PROMILLE → PROSENT, også i heltallsaritmetikk.
export function prosentTekst(promille) {
  if (typeof promille !== "number" || !Number.isInteger(promille)) {
    return "—";
  }
  const neg = promille < 0;
  const a = Math.abs(promille);
  return `${neg ? "-" : ""}${Math.trunc(a / 10)},${a % 10} %`;
}

// Gyldighetens ORD. ÅPEN ENDE SIES MED ORD — en tom celle ville sett ut
// som manglende data der den betyr «gjelder fortsatt».
export function gyldighetTekst(fra, til) {
  if (!fra) return t("ui.prisbok.uten_pris");
  return til
    ? t("ui.prisbok.gyldig_til").replace("{fra}", fra).replace("{til}", til)
    : t("ui.prisbok.gyldig_apen").replace("{fra}", fra);
}

// Utløpskolonnens ORD. ENTALL HAR SIN EGEN NØKKEL.
export function utlopTekst(dogn) {
  if (typeof dogn !== "number") return "—";
  if (dogn < 0) {
    const n = Math.abs(dogn);
    return n === 1
      ? t("ui.prisbok.utlopt_ett_dogn")
      : t("ui.prisbok.utlopt_for").replace("{dogn}", String(n));
  }
  if (dogn === 0) return t("ui.prisbok.utloper_i_dag");
  return dogn === 1
    ? t("ui.prisbok.om_ett_dogn")
    : t("ui.prisbok.om_dogn").replace("{dogn}", String(dogn));
}

// KRONER INN, ØRE UT. `Math.round` på produktet er den ENESTE veien.
export function tilOre(verdi) {
  const n = Number(verdi);
  if (!Number.isFinite(n)) return null;
  return Math.round(n * 100);
}

// PROSENT INN, PROMILLE UT.
export function tilPromille(verdi) {
  const n = Number(verdi);
  if (!Number.isFinite(n)) return null;
  return Math.round(n * 10);
}

// …og den andre veien, uten divisjon.
export function promilleTilFelt(promille) {
  if (typeof promille !== "number" || !Number.isInteger(promille)) {
    return "";
  }
  const neg = promille < 0;
  const a = Math.abs(promille);
  return `${neg ? "-" : ""}${Math.trunc(a / 10)}.${a % 10}`;
}

function produktrad(p, ctx, apneDetalj) {
  const rad = el("tr", {});
  // KODEN NAVNGIR raden — det er den et tilbud siterer.
  rad.append(el("th", { scope: "row", class: "celle-id", text: p.kode }));
  rad.append(el("td", { class: "celle-tekst", text: p.navn }));
  rad.append(el("td", { text: p.enhet }));
  // INGEN PRIS ER IKKE «0,00». «Gratis» og «ingen pris ført» er to helt
  // forskjellige svar, og et register som blandet dem ville gitt bort
  // produktet.
  rad.append(el("td", { class: "celle-tall",
    text: typeof p.listepris_ore === "number"
      ? belopTekst(p.listepris_ore) : t("ui.prisbok.uten_pris") }));
  rad.append(el("td", {}, el("span", {
    text: gyldighetTekst(p.gyldig_fra, p.gyldig_til) })));
  rad.append(el("td", { class: "celle-tall",
    text: p.versjoner ? String(p.versjoner) : "—" }));

  const merkecelle = el("td", {},
    el("span", { text: p.aktiv ? t("ui.prisbok.status.aktiv")
                               : t("ui.prisbok.status.inaktiv") }));
  for (const funn of p.apne_funn || []) {
    if (!MERKE[funn]) continue;
    // MERKET ER TEKST (WCAG 1.4.1) — og en pris som utløper sier hvor
    // mange døgn det er igjen.
    const tekst = funn === "pris_utloper_snart"
      && typeof p.dogn_til_utlop === "number"
      ? `${t(MERKE[funn])} (${utlopTekst(p.dogn_til_utlop)})`
      : t(MERKE[funn]);
    merkecelle.append(" ", el("strong", { class: "merke", text: tekst }));
  }
  rad.append(merkecelle);

  const handling = el("td", {});
  const knapp = el("button", { type: "button",
    text: t("ui.prisbok.knapp.apne") });
  knapp.addEventListener("click", () => apneDetalj(p));
  handling.append(knapp);
  rad.append(handling);
  return rad;
}

function produktTabell(produkter, ctx, apneDetalj) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.prisbok.liste.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.prisbok.kolonne.kode") }),
    el("th", { scope: "col", text: t("ui.prisbok.kolonne.navn") }),
    el("th", { scope: "col", text: t("ui.prisbok.kolonne.enhet") }),
    el("th", { scope: "col", text: t("ui.prisbok.kolonne.pris") }),
    el("th", { scope: "col", text: t("ui.prisbok.kolonne.gyldig") }),
    el("th", { scope: "col", text: t("ui.prisbok.kolonne.versjoner") }),
    el("th", { scope: "col", text: t("ui.prisbok.kolonne.status") }),
    el("th", { scope: "col", text: t("ui.prisbok.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const p of produkter) {
    tbody.append(produktrad(p, ctx, apneDetalj));
  }
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function klausulTabell(klausuler) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.prisbok.klausul.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.prisbok.kolonne.kode") }),
    el("th", { scope: "col", text: t("ui.prisbok.kolonne.versjoner") }),
    el("th", { scope: "col", text: t("ui.prisbok.kolonne.tittel") }),
    el("th", { scope: "col", text: t("ui.prisbok.kolonne.standard") }),
    el("th", { scope: "col", text: t("ui.prisbok.kolonne.gyldig") }),
    el("th", { scope: "col", text: t("ui.prisbok.kolonne.hash") }))));
  const tbody = el("tbody");
  for (const k of klausuler) {
    const rad = el("tr", {});
    rad.append(el("th", { scope: "row", class: "celle-id", text: k.kode }));
    rad.append(el("td", { class: "celle-tall", text: String(k.versjon) }));
    rad.append(el("td", { class: "celle-tekst", text: k.tittel }));
    rad.append(el("td", { text: t(k.standard ? "ui.prisbok.ja"
                                             : "ui.prisbok.nei") }));
    rad.append(el("td", { text: gyldighetTekst(k.gyldig_fra,
                                               k.gyldig_til) }));
    // HASHEN STÅR I TABELLEN, avkortet. Det er den
    // `laste_klausuler_uendret` til slutt måles mot, og en kolonne som
    // skjulte den ville gjort attestasjonen til noe bare basen kunne se.
    rad.append(el("td", { class: "celle-id",
                          text: String(k.tekst_hash || "").slice(0, 12) }));
    tbody.append(rad);
  }
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function terskelTabell(terskler) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.prisbok.terskel.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.prisbok.kolonne.terskel") }),
    el("th", { scope: "col", text: t("ui.prisbok.kolonne.verdi") }))));
  const tbody = el("tbody");
  const linjer = [
    ["ui.prisbok.terskel.rabatt",
     prosentTekst(terskler.rabattgrense_promille)],
    ["ui.prisbok.terskel.varsel",
     t("ui.prisbok.dogn").replace("{dogn}",
                                  String(terskler.utlop_varsel_dogn))],
    ["ui.prisbok.terskel.utenpris",
     t("ui.prisbok.dogn").replace("{dogn}",
                                  String(terskler.uten_pris_dogn))],
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
          ? t("ui.prisbok.feil.tilstand")
          : t("ui.prisbok.feil.generell") }));
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

// DETALJPANELET: prishistorikken, og de to handlingene.
function detaljpanel(ctx, last, kvitter, settApen) {
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;

  const merkelinje = el("p", { class: "muted" });
  const historikk = el("div", {});

  // --- ny pris ---
  const pSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const pBelop = el("input", { id: "pb-pris-belop", name: "listepris",
    type: "number", required: true, step: "0.01", min: "0" });
  const pFra = el("input", { id: "pb-pris-fra", name: "gyldig_fra",
    type: "date", required: true });
  const pGrunn = el("input", { id: "pb-pris-grunn", name: "begrunnelse",
    type: "text", required: true, maxlength: 2000 });
  const pKnapp = el("button", { type: "submit",
    text: t("ui.prisbok.knapp.ny_pris") });
  pSkjema.append(
    felt("pb-pris-belop", "ui.prisbok.skjema.listepris", pBelop),
    felt("pb-pris-fra", "ui.prisbok.skjema.gyldig_fra", pFra,
         "ui.prisbok.skjema.gyldig_fra_hjelp"),
    felt("pb-pris-grunn", "ui.prisbok.skjema.begrunnelse", pGrunn,
         "ui.prisbok.skjema.begrunnelse_hjelp"),
    el("div", { class: "skjema-bunn" }, pKnapp));
  skjemaramme(ctx, last, {
    skjema: pSkjema, knapp: pKnapp, utfall, kvitter,
    okNokkel: "ui.prisbok.skjema.pris_ok",
    send: (idem) => settPris(gjeldende.produkt_id, {
      listepris_ore: tilOre(pBelop.value), valuta: "NOK",
      gyldig_fra: pFra.value, begrunnelse: pGrunn.value,
    }, idem),
    tilbakestill: () => { pBelop.value = ""; pGrunn.value = ""; },
  });

  // --- aktiv/inaktiv ---
  const aSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const aKnapp = el("button", { type: "submit",
    text: t("ui.prisbok.knapp.deaktiver") });
  aSkjema.append(
    el("p", { class: "muted", text: t("ui.prisbok.skjema.aktiv_hjelp") }),
    el("div", { class: "skjema-bunn" }, aKnapp));
  skjemaramme(ctx, last, {
    skjema: aSkjema, knapp: aKnapp, utfall, kvitter,
    okNokkel: "ui.prisbok.skjema.aktiv_ok",
    send: (idem) => settProduktAktiv(gjeldende.produkt_id,
                                     !gjeldende.aktiv, idem),
    tilbakestill: () => { settApen(null); innhold.hidden = true; },
  });

  const skriver = harScope(ctx, "bestilling:opprett");
  innhold.append(el("h3", { text: t("ui.prisbok.detalj.tittel") }),
    merkelinje, historikk);
  if (skriver) {
    innhold.append(
      el("h4", { text: t("ui.prisbok.skjema.pris_tittel") }), pSkjema,
      el("h4", { text: t("ui.prisbok.skjema.aktiv_tittel") }), aSkjema);
  }
  boks.append(innhold, utfall);
  innhold.hidden = true;

  function historikkTabell(rader) {
    const tb = el("table", { class: "kpi-tabell" },
      el("caption", { text: t("ui.prisbok.historikk.caption") }));
    tb.append(el("thead", {}, el("tr", {},
      el("th", { scope: "col", text: t("ui.prisbok.kolonne.versjon") }),
      el("th", { scope: "col", text: t("ui.prisbok.kolonne.pris") }),
      el("th", { scope: "col", text: t("ui.prisbok.kolonne.gyldig") }),
      el("th", { scope: "col",
                 text: t("ui.prisbok.kolonne.begrunnelse") }),
      el("th", { scope: "col", text: t("ui.prisbok.kolonne.satt_av") }))));
    const tbody = el("tbody");
    for (const v of rader) {
      const rad = el("tr", {});
      rad.append(el("th", { scope: "row", class: "celle-tall",
                            text: String(v.versjon) }));
      rad.append(el("td", { class: "celle-tall",
                            text: belopTekst(v.listepris_ore) }));
      rad.append(el("td", { text: gyldighetTekst(v.gyldig_fra,
                                                 v.gyldig_til) }));
      // BEGRUNNELSEN STÅR I TABELLEN. En prisendring uten begrunnelse
      // er en beslutning ingen kan etterprøve — og prisen er det
      // virksomheten tjener på.
      rad.append(el("td", { class: "celle-tekst", text: v.begrunnelse }));
      rad.append(el("td", { class: "muted", text: v.opprettet_av }));
      tbody.append(rad);
    }
    tb.append(tbody);
    return el("div", { class: "tablewrap" }, tb);
  }

  return {
    node: boks,
    async apne(p) {
      gjeldende = p;
      settApen(p.produkt_id);
      sett(utfall);
      sett(historikk);
      merkelinje.textContent = `${p.kode} · ${p.navn} · ${p.enhet}`;
      aKnapp.textContent = t(p.aktiv ? "ui.prisbok.knapp.deaktiver"
                                     : "ui.prisbok.knapp.aktiver");
      // ET INAKTIVT PRODUKT TAR IKKE IMOT NY PRIS — men det KAN
      // aktiveres igjen, så den knappen står levende.
      pKnapp.disabled = !p.aktiv;
      innhold.hidden = false;
      let d;
      try {
        d = await hentJson(
          `/v1/prisbok/${encodeURIComponent(p.produkt_id)}/historikk`);
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        sett(utfall, el("span", { role: "alert",
          text: t("ui.prisbok.feil.generell") }));
        return;
      }
      const liste = d.versjoner || [];
      if (!liste.length) {
        sett(historikk, el("p", { class: "muted",
          text: t("ui.prisbok.detalj.ingen") }));
        return;
      }
      sett(historikk, historikkTabell(liste));
    },
  };
}

function produktSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const kode = el("input", { id: "pb-ny-kode", name: "kode",
    type: "text", required: true, maxlength: 100 });
  const navn = el("input", { id: "pb-ny-navn", name: "navn",
    type: "text", required: true, maxlength: 300 });
  const enhet = el("input", { id: "pb-ny-enhet", name: "enhet",
    type: "text", required: true, maxlength: 60 });
  const knapp = el("button", { type: "submit",
    text: t("ui.prisbok.knapp.ny_produkt") });
  skjema.append(
    felt("pb-ny-kode", "ui.prisbok.skjema.kode", kode,
         "ui.prisbok.skjema.kode_hjelp"),
    felt("pb-ny-navn", "ui.prisbok.skjema.navn", navn),
    felt("pb-ny-enhet", "ui.prisbok.skjema.enhet", enhet,
         "ui.prisbok.skjema.enhet_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.prisbok.skjema.produkt_ok",
    send: (idem) => registrerProdukt({
      kode: kode.value, navn: navn.value, enhet: enhet.value }, idem),
    tilbakestill: () => {
      kode.value = ""; navn.value = ""; enhet.value = "";
    },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.prisbok.skjema.produkt_tittel") }), skjema,
    utfall);
}

function klausulSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const kode = el("input", { id: "pb-kl-kode", name: "kode",
    type: "text", required: true, maxlength: 100 });
  const tittel = el("input", { id: "pb-kl-tittel", name: "tittel",
    type: "text", required: true, maxlength: 300 });
  const tekst = el("textarea", { id: "pb-kl-tekst", name: "tekst",
    required: true, rows: "5" });
  const standard = el("input", { id: "pb-kl-standard", name: "standard",
    type: "checkbox" });
  const fra = el("input", { id: "pb-kl-fra", name: "gyldig_fra",
    type: "date", required: true });
  const knapp = el("button", { type: "submit",
    text: t("ui.prisbok.knapp.ny_klausul") });
  skjema.append(
    felt("pb-kl-kode", "ui.prisbok.skjema.kode", kode),
    felt("pb-kl-tittel", "ui.prisbok.skjema.tittel", tittel),
    felt("pb-kl-tekst", "ui.prisbok.skjema.tekst", tekst,
         "ui.prisbok.skjema.tekst_hjelp"),
    felt("pb-kl-standard", "ui.prisbok.skjema.standard", standard,
         "ui.prisbok.skjema.standard_hjelp"),
    felt("pb-kl-fra", "ui.prisbok.skjema.gyldig_fra", fra),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.prisbok.skjema.klausul_ok",
    // INGEN HASH SENDES. Den regnes i basen, av teksten selv — en hash
    // flaten oppga ville vært en påstand om innholdet.
    send: (idem) => settKlausul({
      kode: kode.value, tittel: tittel.value, tekst: tekst.value,
      standard: standard.checked, gyldig_fra: fra.value }, idem),
    tilbakestill: () => { tekst.value = ""; },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.prisbok.klausul.tittel") }), skjema, utfall);
}

function terskelSkjema(ctx, last, terskler, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const rabatt = el("input", { id: "pb-t-rabatt", name: "rabatt",
    type: "number", required: true, step: "0.1", min: "0", max: "100" });
  const varsel = el("input", { id: "pb-t-varsel", name: "varsel",
    type: "number", required: true, step: "1", min: "0", max: "3650" });
  const utenpris = el("input", { id: "pb-t-utenpris", name: "utenpris",
    type: "number", required: true, step: "1", min: "0", max: "3650" });
  if (terskler) {
    rabatt.value = promilleTilFelt(terskler.rabattgrense_promille);
    varsel.value = String(terskler.utlop_varsel_dogn);
    utenpris.value = String(terskler.uten_pris_dogn);
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.prisbok.knapp.lagre_terskler") });
  skjema.append(
    felt("pb-t-rabatt", "ui.prisbok.terskel.rabatt", rabatt,
         "ui.prisbok.terskel.rabatt_hjelp"),
    felt("pb-t-varsel", "ui.prisbok.terskel.varsel", varsel),
    felt("pb-t-utenpris", "ui.prisbok.terskel.utenpris", utenpris,
         "ui.prisbok.terskel.utenpris_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.prisbok.skjema.terskel_ok",
    send: (idem) => settPrisbokterskler({
      rabattgrense_promille: tilPromille(rabatt.value),
      utlop_varsel_dogn: Number(varsel.value),
      uten_pris_dogn: Number(utenpris.value),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.prisbok.terskel.tittel") }), skjema, utfall);
}

function sammendrag(s) {
  const p = el("p", {
    text: t("ui.prisbok.sammendrag")
      .replace("{aktive}", String(s.aktive))
      .replace("{medpris}", String(s.med_gyldig_pris))
      .replace("{klausuler}", String(s.klausuler))
      .replace("{standard}", String(s.standardklausuler))
      .replace("{funn}", String(s.apne_funn)) });
  if (!s.har_terskel) {
    p.append(" ", el("strong", { text: t("ui.prisbok.ingen_terskler") }));
  }
  if (s.vist < s.produkter) {
    p.append(" ", el("strong", {
      text: t("ui.prisbok.avkortet").replace("{vist}", String(s.vist)) }));
  }
  return p;
}

export function visPrisbok(hoved, ctx) {
  const hode = () => flateHode(t("ui.prisbok.tittel"),
    t("ui.prisbok.undertittel"));
  sett(hoved, ...hode());
  // KVITTERINGEN OG DEN ÅPNE RADEN LEVER UTENFOR TEGNINGEN.
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenRad = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const settApen = (id) => { apenRad = id; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/prisbok"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const produkter = d.produkter || [];
      const klausuler = d.klausuler || [];
      const detalj = detaljpanel(ctx, last, kvitter, settApen);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.prisbok.oversikt.tittel") }),
        sammendrag(s),
        // HVORFOR BOKA FINNES, sagt på flaten.
        el("p", { class: "muted", text: t("ui.prisbok.oversikt.hvorfor") }));

      const liste = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.prisbok.liste.tittel") }));
      if (!produkter.length) {
        liste.append(el("p", { class: "muted",
          text: t("ui.prisbok.liste.ingen") }));
      } else {
        liste.append(produktTabell(produkter, ctx, detalj.apne));
      }

      const klausulseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.prisbok.klausul.tittel") }));
      if (!klausuler.length) {
        klausulseksjon.append(el("p", { class: "muted",
          text: t("ui.prisbok.klausul.ingen") }));
      } else {
        klausulseksjon.append(klausulTabell(klausuler));
      }

      const terskelseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.prisbok.terskel.tittel") }));
      if (!d.terskler) {
        terskelseksjon.append(el("p", { class: "muted",
          text: t("ui.prisbok.ingen_terskler") }));
      } else {
        terskelseksjon.append(
          el("p", { class: "muted",
            text: t("ui.prisbok.terskel.versjon")
              .replace("{versjon}", String(d.terskler.versjon)) }),
          terskelTabell(d.terskler));
      }

      const deler = [oversikt, liste, klausulseksjon, terskelseksjon,
                     detalj.node];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(produktSkjema(ctx, last, kvitter),
                   klausulSkjema(ctx, last, kvitter),
                   terskelSkjema(ctx, last, d.terskler, kvitter));
      }
      sett(kropp, ...deler);
      if (apenRad) {
        const rad = produkter.find((x) => x.produkt_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
