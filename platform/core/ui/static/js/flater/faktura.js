// Fakturakontrollagenten (M-14 v1) — FAKTURAREGISTERET.
//
// FLATENS VIKTIGSTE JOBB er å vise hva kontrollene SÅ: hvilke fakturaer
// som har et mva-avvik, hvilke som ligner en annen faktura fra samme
// leverandør, hvilke som kommer fra noen vi ikke kjenner, og hvilke som
// har stått for lenge uten at et menneske har sett på dem. Som TEKST,
// ikke bare farge (WCAG 1.4.1).
//
// DET FINNES INGEN «BOKFØR»-KNAPP, og fraværet er dommen: policyen vi
// sender ut navngir modulen som verifikatoren `v_regnskap`, betrodd for
// `faktura_godkjent` — og bruker den attestasjonen til å la
// `faktura.bokfor` gå automatisk. v1 registrerer at et menneske SÅ på
// fakturaen; den signerer ingenting. Knappen heter «Avgjør» og har to
// utfall: kontrollert eller avvist. Ordet «bokført» finnes ikke.
//
// FLATEN VISER, DEN REGNER IKKE. `avvik_ore`, `dogn_siden_mottatt` og
// hele treffraten er regnet i BASEN, i samme skann som raden (106s
// lesedører). SÆRLIG MVA-AVRUNDINGEN: regelen er
// `(netto * promille + 500) / 1000`, halv-opp, og den bor i basen. En
// flate som regnet den selv ville hatt en ANDRE avrundingsregel å holde
// i takt — og et flyttall der ville gitt 2499.9999999999995 øre på
// 99,99 kroner netto, altså et avvik på hver eneste faktura.
//
// BELØP FORMATERES I HELTALLSARITMETIKK (101/104/105s form, ordrett).
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, avgjorFaktura, hentJson, nyIdempotensnokkel,
  registrerFaktura, registrerFakturakontroll, settFakturaterskler,
  settMvasats,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// KONTROLLTYPENE, i oversiktens rekkefølge. Lukket sett, speil av
// CHECK-en i 106 — og rekkefølgen er fast, så tabellen har samme form
// hver dag.
const KONTROLLTYPER = ["dublett", "mva", "leverandor", "belopsgrense",
                       "manuell"];

// FUNNTYPE → MERKETEKST. Lukket tabell, ikke strengbygging.
const MERKE = {
  naer_dublett: "ui.faktura.merke_dublett",
  mva_avvik: "ui.faktura.merke_mva",
  ukjent_leverandor: "ui.faktura.merke_ukjent",
  over_belopsgrense: "ui.faktura.merke_stor",
  ukontrollert: "ui.faktura.merke_ukontrollert",
  ingen_mvasats: "ui.faktura.merke_uten_sats",
  ingen_terskel: "ui.faktura.merke_uten_terskel",
};

// BELØP I HELTALLSARITMETIKK, aldri via `/100`.
export function belopTekst(ore) {
  if (typeof ore !== "number" || !Number.isInteger(ore)) return "—";
  const neg = ore < 0;
  const a = Math.abs(ore);
  return `${neg ? "-" : ""}${Math.trunc(a / 100)},`
    + `${String(a % 100).padStart(2, "0")}`;
}

// PROMILLE → PROSENT, også i heltallsaritmetikk. `promille / 10` i
// flyttall gir «24.999999999999996 %» på 250, og en mva-sats som nesten
// stemmer er en sats ingen kan kontrollere mot.
export function satsTekst(promille) {
  if (typeof promille !== "number" || !Number.isInteger(promille)) {
    return "—";
  }
  const neg = promille < 0;
  const a = Math.abs(promille);
  return `${neg ? "-" : ""}${Math.trunc(a / 10)},${a % 10} %`;
}

// Alderskolonnens ORD. ENTALL HAR SIN EGEN NØKKEL (lærdommen fra
// M-21/M-34/M-13/M-17/M-18/M-23/M-24).
export function alderTekst(dogn) {
  if (typeof dogn !== "number") return "—";
  if (dogn === 0) return t("ui.faktura.mottatt_i_dag");
  return dogn === 1
    ? t("ui.faktura.mottatt_i_gaar")
    : t("ui.faktura.mottatt_for").replace("{dogn}", String(dogn));
}

// KONTROLLENE SOM ORD. «0 av 0» er ingen målt kontroll — det er en
// faktura ingen har kontrollert, og de to er forskjellige tilstander et
// menneske handler ulikt på.
export function kontrollTekst(f) {
  if (!f.kontroller) return t("ui.faktura.ingen_kontroller");
  return t("ui.faktura.avvik_av")
    .replace("{avvik}", String(f.avvik))
    .replace("{kontroller}", String(f.kontroller));
}

// KRONER INN, ØRE UT. `Math.round` på produktet er den ENESTE veien.
export function tilOre(verdi) {
  const n = Number(verdi);
  if (!Number.isFinite(n)) return null;
  return Math.round(n * 100);
}

// PROSENT INN, PROMILLE UT. 12,5 % er 125, ikke 124.99999999999999.
export function tilPromille(verdi) {
  const n = Number(verdi);
  if (!Number.isFinite(n)) return null;
  return Math.round(n * 10);
}

// …og de to andre veiene, til skjemafeltenes verdier: `trunc` og `%`
// gir samme tall uten å gå via en verdi maskinen må runde. Feltene er
// `type="number"`, så desimalskilletegnet er punktum her og komma bare i
// visningen.
export function oreTilFelt(ore) {
  if (typeof ore !== "number" || !Number.isInteger(ore)) return "";
  const neg = ore < 0;
  const a = Math.abs(ore);
  return `${neg ? "-" : ""}${Math.trunc(a / 100)}.`
    + `${String(a % 100).padStart(2, "0")}`;
}

export function promilleTilFelt(promille) {
  if (typeof promille !== "number" || !Number.isInteger(promille)) {
    return "";
  }
  const neg = promille < 0;
  const a = Math.abs(promille);
  return `${neg ? "-" : ""}${Math.trunc(a / 10)}.${a % 10}`;
}

function fakturarad(f, ctx, apneDetalj) {
  const rad = el("tr", {});
  // LEVERANDØREN NAVNGIR raden.
  rad.append(el("th", { scope: "row", class: "celle-tekst",
                        text: f.leverandor_ref }));
  rad.append(el("td", { class: "celle-id", text: f.fakturanummer }));
  rad.append(el("td", { class: "celle-tall",
                        text: belopTekst(f.brutto_ore) }));
  // MVA-EN STÅR VED SIDEN AV NETTO. Et menneske som ser en mva som ikke
  // ligner en fjerdedel av netto, ser det uten å regne.
  rad.append(el("td", { class: "celle-tall",
                        text: belopTekst(f.mva_ore) }));
  rad.append(el("td", { text: f.sats_kode }));
  rad.append(el("td", {}, el("span", { text: kontrollTekst(f) })));

  const alderscelle = el("td", {},
    el("span", { text: alderTekst(f.dogn_siden_mottatt) }));
  for (const funn of f.apne_funn || []) {
    if (!MERKE[funn]) continue;
    // MERKET ER TEKST. Dette er flatens viktigste opplysning på raden.
    alderscelle.append(" ", el("strong", { class: "merke",
                                           text: t(MERKE[funn]) }));
  }
  rad.append(alderscelle);
  rad.append(el("td", { text: t(`ui.faktura.status.${f.status}`) }));

  const handling = el("td", {});
  const knapp = el("button", { type: "button",
    text: t("ui.faktura.knapp.apne") });
  knapp.addEventListener("click", () => apneDetalj(f));
  handling.append(knapp);
  rad.append(handling);
  return rad;
}

function fakturaTabell(fakturaer, ctx, apneDetalj) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.faktura.liste.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.faktura.kolonne.leverandor") }),
    el("th", { scope: "col", text: t("ui.faktura.kolonne.nummer") }),
    el("th", { scope: "col", text: t("ui.faktura.kolonne.brutto") }),
    el("th", { scope: "col", text: t("ui.faktura.kolonne.mva") }),
    el("th", { scope: "col", text: t("ui.faktura.kolonne.sats") }),
    el("th", { scope: "col", text: t("ui.faktura.kolonne.kontroller") }),
    el("th", { scope: "col", text: t("ui.faktura.kolonne.mottatt") }),
    el("th", { scope: "col", text: t("ui.faktura.kolonne.status") }),
    el("th", { scope: "col", text: t("ui.faktura.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const f of fakturaer) {
    tbody.append(fakturarad(f, ctx, apneDetalj));
  }
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

// TREFFRATEN, og den er modulens egentlige leveranse i v1: «en
// dublettsjekk ingen har målt er ikke en kontroll, det er en påstand».
function treffrateTabell(rader) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.faktura.treffrate.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.faktura.kolonne.kontrolltype") }),
    el("th", { scope: "col", text: t("ui.faktura.kolonne.kjort") }),
    el("th", { scope: "col", text: t("ui.faktura.kolonne.avvik") }))));
  const tbody = el("tbody");
  // ALLE FEM TYPENE TEGNES, også de tenanten ikke har kjørt — døren
  // returnerer dem alle av samme grunn. En oversikt som endret form fra
  // dag til dag kan ingen sammenligne over tid.
  for (const r of rader) {
    const rad = el("tr", {});
    rad.append(el("th", { scope: "row",
      text: t(`ui.faktura.kontrolltype.${r.kontrolltype}`) }));
    rad.append(el("td", { class: "celle-tall", text: String(r.kjort) }));
    rad.append(el("td", { class: "celle-tall", text: String(r.avvik) }));
    tbody.append(rad);
  }
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function satsTabell(satser) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.faktura.sats.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.faktura.kolonne.satskode") }),
    el("th", { scope: "col", text: t("ui.faktura.kolonne.sats") }),
    el("th", { scope: "col", text: t("ui.faktura.kolonne.gyldig") }),
    el("th", { scope: "col", text: t("ui.faktura.kolonne.naa") }))));
  const tbody = el("tbody");
  for (const s of satser) {
    const rad = el("tr", {});
    rad.append(el("th", { scope: "row", text: s.sats_kode }));
    rad.append(el("td", { class: "celle-tall",
                          text: satsTekst(s.promille) }));
    // ÅPEN ENDE SIES MED ORD. En tom celle ville sett ut som manglende
    // data der den betyr «gjelder fortsatt».
    rad.append(el("td", { text: s.gyldig_til
      ? `${s.gyldig_fra} – ${s.gyldig_til}`
      : t("ui.faktura.sats.apen").replace("{fra}", s.gyldig_fra) }));
    rad.append(el("td", { text: t(s.gjelder_i_dag
      ? "ui.faktura.sats.gjelder" : "ui.faktura.sats.historisk") }));
    tbody.append(rad);
  }
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function terskelTabell(terskler) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.faktura.terskel.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.faktura.kolonne.terskel") }),
    el("th", { scope: "col", text: t("ui.faktura.kolonne.verdi") }))));
  const tbody = el("tbody");
  const linjer = [
    ["ui.faktura.terskel.slingring",
     belopTekst(terskler.mva_slingring_ore)],
    ["ui.faktura.terskel.belopsgrense",
     belopTekst(terskler.belopsgrense_ore)],
    ["ui.faktura.terskel.kontrollfrist",
     t("ui.faktura.dogn").replace("{dogn}",
                                  String(terskler.kontrollfrist_dogn))],
    ["ui.faktura.terskel.dublettvindu",
     t("ui.faktura.dogn").replace("{dogn}",
                                  String(terskler.dublettvindu_dogn))],
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
          ? t("ui.faktura.feil.tilstand")
          : t("ui.faktura.feil.generell") }));
      return;
    }
    idem = null;
    knapp.disabled = false;
    if (tilbakestill) tilbakestill();
    meldLive(t(okNokkel));
    // KVITTERINGEN SKAL OVERLEVE TEGNINGEN — flatens egen linje lever
    // utenfor `kropp`, som bygges på nytt ved hver `last()`. FEILEN blir
    // stående i skjemaet: den veien tegner ikke om. (Klynge 3-rettingen.)
    kvitter(t(okNokkel));
    await last();
  });
}

// DETALJPANELET: kontrollene, og de to handlingene.
function detaljpanel(ctx, last, kvitter, settApen) {
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;

  const merkelinje = el("p", { class: "muted" });
  const historikk = el("div", {});

  // --- manuell kontroll ---
  const kSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const kUtfall = el("select", { id: "fa-k-utfall", name: "utfall",
    required: true });
  for (const u of ["ok", "avvik"]) {
    kUtfall.append(el("option", { value: u,
      text: t(`ui.faktura.utfall.${u}`) }));
  }
  const kNotat = el("input", { id: "fa-k-notat", name: "notat",
    type: "text", required: true, maxlength: 2000 });
  const kKnapp = el("button", { type: "submit",
    text: t("ui.faktura.knapp.kontroll") });
  kSkjema.append(
    felt("fa-k-utfall", "ui.faktura.skjema.kontroll_utfall", kUtfall),
    felt("fa-k-notat", "ui.faktura.skjema.kontroll_notat", kNotat,
         "ui.faktura.skjema.kontroll_hjelp"),
    el("div", { class: "skjema-bunn" }, kKnapp));
  skjemaramme(ctx, last, {
    skjema: kSkjema, knapp: kKnapp, utfall, kvitter,
    okNokkel: "ui.faktura.skjema.kontroll_ok",
    send: (idem) => registrerFakturakontroll(
      gjeldende.faktura_id, kUtfall.value, kNotat.value, idem),
    tilbakestill: () => { kNotat.value = ""; },
  });

  // --- avgjørelse ---
  const aSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const aStatus = el("select", { id: "fa-a-status", name: "status",
    required: true });
  // TO UTFALL, OG «BOKFØRT» ER IKKE ETT AV DEM. Fraværet er dommen.
  for (const s of ["kontrollert", "avvist"]) {
    aStatus.append(el("option", { value: s,
      text: t(`ui.faktura.status.${s}`) }));
  }
  const aGrunn = el("input", { id: "fa-a-grunn", name: "begrunnelse",
    type: "text", required: true, maxlength: 2000 });
  const aKnapp = el("button", { type: "submit",
    text: t("ui.faktura.knapp.avgjor") });
  aSkjema.append(
    felt("fa-a-status", "ui.faktura.skjema.avgjor_status", aStatus,
         "ui.faktura.skjema.avgjor_hjelp"),
    felt("fa-a-grunn", "ui.faktura.skjema.avgjor_begrunnelse", aGrunn),
    el("div", { class: "skjema-bunn" }, aKnapp));
  skjemaramme(ctx, last, {
    skjema: aSkjema, knapp: aKnapp, utfall, kvitter,
    okNokkel: "ui.faktura.skjema.avgjor_ok",
    send: (idem) => avgjorFaktura(gjeldende.faktura_id, aStatus.value,
                                  aGrunn.value, idem),
    // EN AVGJORT FAKTURA SKAL IKKE GJENÅPNES i flaten heller: begge
    // knappene ville vært døde.
    tilbakestill: () => {
      aGrunn.value = ""; innhold.hidden = true; settApen(null);
    },
  });

  const skriver = harScope(ctx, "bestilling:opprett");
  innhold.append(el("h3", { text: t("ui.faktura.detalj.tittel") }),
    merkelinje, historikk);
  if (skriver) {
    innhold.append(
      el("h4", { text: t("ui.faktura.skjema.kontroll_tittel") }), kSkjema,
      el("h4", { text: t("ui.faktura.knapp.avgjor") }), aSkjema);
  }
  boks.append(innhold, utfall);
  innhold.hidden = true;

  return {
    node: boks,
    async apne(f) {
      gjeldende = f;
      settApen(f.faktura_id);
      sett(utfall);
      sett(historikk);
      merkelinje.textContent = `${f.leverandor_ref} · ${f.fakturanummer}`
        + ` · ${belopTekst(f.netto_ore)} + ${belopTekst(f.mva_ore)}`
        + ` = ${belopTekst(f.brutto_ore)} · ${f.valuta}`;
      // EN AVGJORT FAKTURA TAR IKKE IMOT NOE.
      const apen = f.status === "mottatt";
      kKnapp.disabled = !apen;
      aKnapp.disabled = !apen;
      innhold.hidden = false;
      let d;
      try {
        d = await hentJson(
          `/v1/faktura/${encodeURIComponent(f.faktura_id)}/kontroller`);
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        sett(utfall, el("span", { role: "alert",
          text: t("ui.faktura.feil.generell") }));
        return;
      }
      const liste = d.kontroller || [];
      if (!liste.length) {
        sett(historikk, el("p", { class: "muted",
          text: t("ui.faktura.detalj.ingen") }));
        return;
      }
      const ul = el("ul", {});
      for (const k of liste) {
        const li = el("li", {},
          el("span", { text:
            `${t(`ui.faktura.kontrolltype.${k.kontrolltype}`)}: ` }));
        li.append(el("strong", {
          text: t(`ui.faktura.utfall.${k.utfall}`) }));
        // AVVIKET STÅR I KRONER når det er et beløp. `null` betyr at
        // typen ikke har et tall — en leverandørkontroll måler ingen
        // kroner — og en «0,00» der ville vært en oppdiktet måling.
        if (typeof k.avvik_ore === "number") {
          li.append(" ", el("span", {
            text: t("ui.faktura.detalj.avvik")
              .replace("{belop}", belopTekst(k.avvik_ore)) }));
        }
        li.append(el("span", { class: "muted",
          text: ` · ${k.kjort_av}` + (k.notat ? ` · ${k.notat}` : "") }));
        ul.append(li);
      }
      sett(historikk, ul);
    },
  };
}

function nySkjema(ctx, last, satser, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const ref = el("input", { id: "fa-ny-ref", name: "leverandor_ref",
    type: "text", required: true, maxlength: 300 });
  const nummer = el("input", { id: "fa-ny-nummer", name: "fakturanummer",
    type: "text", required: true, maxlength: 100 });
  const netto = el("input", { id: "fa-ny-netto", name: "netto",
    type: "number", required: true, step: "0.01", min: "0" });
  const mva = el("input", { id: "fa-ny-mva", name: "mva",
    type: "number", required: true, step: "0.01", min: "0" });
  const brutto = el("input", { id: "fa-ny-brutto", name: "brutto",
    type: "number", required: true, step: "0.01", min: "0" });
  const sats = el("select", { id: "fa-ny-sats", name: "sats_kode",
    required: true });
  // BARE SATSER SOM GJELDER I DAG. En historisk sats i nedtrekket ville
  // invitert til en kontroll mot noe som ikke lenger er sant.
  for (const s of satser) {
    if (!s.gjelder_i_dag) continue;
    sats.append(el("option", { value: s.sats_kode,
      text: `${s.sats_kode} (${satsTekst(s.promille)})` }));
  }
  const utstedt = el("input", { id: "fa-ny-utstedt", name: "utstedt",
    type: "date", required: true });
  const forfall = el("input", { id: "fa-ny-forfall", name: "forfall",
    type: "date", required: true });
  const mottatt = el("input", { id: "fa-ny-mottatt", name: "mottatt",
    type: "date", required: true });
  const knapp = el("button", { type: "submit",
    text: t("ui.faktura.knapp.ny") });
  skjema.append(
    felt("fa-ny-ref", "ui.faktura.skjema.leverandor", ref,
         "ui.faktura.skjema.leverandor_hjelp"),
    felt("fa-ny-nummer", "ui.faktura.skjema.nummer", nummer),
    felt("fa-ny-netto", "ui.faktura.skjema.netto", netto),
    felt("fa-ny-mva", "ui.faktura.skjema.mva", mva,
         "ui.faktura.skjema.mva_hjelp"),
    felt("fa-ny-brutto", "ui.faktura.skjema.brutto", brutto),
    felt("fa-ny-sats", "ui.faktura.skjema.sats", sats),
    felt("fa-ny-utstedt", "ui.faktura.skjema.utstedt", utstedt),
    felt("fa-ny-forfall", "ui.faktura.skjema.forfall", forfall),
    felt("fa-ny-mottatt", "ui.faktura.skjema.mottatt", mottatt),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.faktura.skjema.ny_ok",
    send: (idem) => registrerFaktura({
      leverandor_ref: ref.value, fakturanummer: nummer.value,
      netto_ore: tilOre(netto.value), mva_ore: tilOre(mva.value),
      brutto_ore: tilOre(brutto.value), sats_kode: sats.value,
      valuta: "NOK", utstedt: utstedt.value, forfall: forfall.value,
      mottatt: mottatt.value,
    }, idem),
    tilbakestill: () => {
      nummer.value = ""; netto.value = ""; mva.value = "";
      brutto.value = "";
    },
  });
  const boks = el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.faktura.skjema.ny_tittel") }));
  if (!sats.options.length) {
    // INGEN GJELDENDE SATS, INGEN FAKTURA. En setning er ærligere enn
    // et tomt nedtrekk som ser ut som en feil.
    boks.append(el("p", { class: "muted",
      text: t("ui.faktura.skjema.ingen_sats") }));
    return boks;
  }
  boks.append(skjema, utfall);
  return boks;
}

function satsSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const kode = el("input", { id: "fa-s-kode", name: "sats_kode",
    type: "text", required: true, maxlength: 60 });
  const prosent = el("input", { id: "fa-s-prosent", name: "prosent",
    type: "number", required: true, step: "0.1", min: "0", max: "100" });
  const fra = el("input", { id: "fa-s-fra", name: "gyldig_fra",
    type: "date", required: true });
  const til = el("input", { id: "fa-s-til", name: "gyldig_til",
    type: "date" });
  const knapp = el("button", { type: "submit",
    text: t("ui.faktura.knapp.ny_sats") });
  skjema.append(
    felt("fa-s-kode", "ui.faktura.skjema.satskode", kode),
    felt("fa-s-prosent", "ui.faktura.skjema.prosent", prosent,
         "ui.faktura.skjema.prosent_hjelp"),
    felt("fa-s-fra", "ui.faktura.skjema.gyldig_fra", fra),
    felt("fa-s-til", "ui.faktura.skjema.gyldig_til", til,
         "ui.faktura.skjema.gyldig_til_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.faktura.skjema.sats_ok",
    send: (idem) => settMvasats({
      sats_kode: kode.value, promille: tilPromille(prosent.value),
      gyldig_fra: fra.value, gyldig_til: til.value || null,
    }, idem),
    tilbakestill: () => { kode.value = ""; prosent.value = ""; },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.faktura.sats.tittel") }), skjema, utfall);
}

function terskelSkjema(ctx, last, terskler, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const slingring = el("input", { id: "fa-t-slingring",
    name: "slingring", type: "number", required: true, step: "0.01",
    min: "0", max: "10" });
  const grense = el("input", { id: "fa-t-grense", name: "belopsgrense",
    type: "number", required: true, step: "0.01", min: "0" });
  const frist = el("input", { id: "fa-t-frist", name: "kontrollfrist",
    type: "number", required: true, step: "1", min: "0", max: "3650" });
  const vindu = el("input", { id: "fa-t-vindu", name: "dublettvindu",
    type: "number", required: true, step: "1", min: "0", max: "365" });
  if (terskler) {
    // TENANTENS EGNE TALL FORHÅNDSUTFYLT, ikke modulens standardverdier:
    // et skjema som viste noe annet ville stilltiende endret grensen
    // ved neste lagring.
    slingring.value = oreTilFelt(terskler.mva_slingring_ore);
    grense.value = oreTilFelt(terskler.belopsgrense_ore);
    frist.value = String(terskler.kontrollfrist_dogn);
    vindu.value = String(terskler.dublettvindu_dogn);
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.faktura.knapp.lagre_terskler") });
  skjema.append(
    felt("fa-t-slingring", "ui.faktura.terskel.slingring", slingring,
         "ui.faktura.terskel.slingring_hjelp"),
    felt("fa-t-grense", "ui.faktura.terskel.belopsgrense", grense,
         "ui.faktura.terskel.belopsgrense_hjelp"),
    felt("fa-t-frist", "ui.faktura.terskel.kontrollfrist", frist,
         "ui.faktura.terskel.kontrollfrist_hjelp"),
    felt("fa-t-vindu", "ui.faktura.terskel.dublettvindu", vindu,
         "ui.faktura.terskel.dublettvindu_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.faktura.skjema.terskel_ok",
    send: (idem) => settFakturaterskler({
      mva_slingring_ore: tilOre(slingring.value),
      belopsgrense_ore: tilOre(grense.value),
      kontrollfrist_dogn: Number(frist.value),
      dublettvindu_dogn: Number(vindu.value),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.faktura.terskel.tittel") }), skjema, utfall);
}

// Sammendraget. TALLENE KOMMER FRA SIN EGEN DØR og gjelder ALT.
function sammendrag(s) {
  const p = el("p", {
    text: t("ui.faktura.sammendrag")
      .replace("{mottatte}", String(s.mottatte))
      .replace("{belop}", belopTekst(s.mottatt_ore))
      .replace("{ukontrollerte}", String(s.ukontrollerte))
      .replace("{funn}", String(s.apne_funn)) });
  if (!s.har_terskel) {
    p.append(" ", el("strong", { text: t("ui.faktura.ingen_terskler") }));
  }
  if (!s.satser) {
    // UTEN EN MVASATS KAN INGEN MVA KONTROLLERES. Setningen står som
    // ord, ikke som en tom tabell lenger nede.
    p.append(" ", el("strong", { text: t("ui.faktura.ingen_satser") }));
  }
  if (s.vist < s.mottatte) {
    p.append(" ", el("strong", {
      text: t("ui.faktura.avkortet").replace("{vist}", String(s.vist)) }));
  }
  return p;
}

export function visFaktura(hoved, ctx) {
  const hode = () => flateHode(t("ui.faktura.tittel"),
    t("ui.faktura.undertittel"));
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
    () => hentJson("/v1/faktura"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const fakturaer = d.fakturaer || [];
      const satser = d.satser || [];
      const detalj = detaljpanel(ctx, last, kvitter, settApen);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.faktura.oversikt.tittel") }),
        sammendrag(s));

      const treff = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.faktura.treffrate.tittel") }),
        // TREFFRATEN ER MODULENS EGENTLIGE LEVERANSE i v1, og
        // setningen sier hvorfor.
        el("p", { class: "muted", text: t("ui.faktura.treffrate.hvorfor") }),
        treffrateTabell(d.treffrate || []));

      const liste = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.faktura.liste.tittel") }));
      if (!fakturaer.length) {
        liste.append(el("p", { class: "muted",
          text: t("ui.faktura.liste.ingen") }));
      } else {
        liste.append(fakturaTabell(fakturaer, ctx, detalj.apne));
      }

      const satsseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.faktura.sats.tittel") }));
      if (!satser.length) {
        satsseksjon.append(el("p", { class: "muted",
          text: t("ui.faktura.ingen_satser") }));
      } else {
        satsseksjon.append(satsTabell(satser));
      }

      const terskelseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.faktura.terskel.tittel") }));
      if (!d.terskler) {
        terskelseksjon.append(el("p", { class: "muted",
          text: t("ui.faktura.ingen_terskler") }));
      } else {
        terskelseksjon.append(
          el("p", { class: "muted",
            text: t("ui.faktura.terskel.versjon")
              .replace("{versjon}", String(d.terskler.versjon)) }),
          terskelTabell(d.terskler));
      }

      const deler = [oversikt, treff, liste, satsseksjon, terskelseksjon,
                     detalj.node];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(nySkjema(ctx, last, satser, kvitter),
                   satsSkjema(ctx, last, kvitter),
                   terskelSkjema(ctx, last, d.terskler, kvitter));
      }
      sett(kropp, ...deler);
      // GJENÅPNE PANELET på raden som sto åpen.
      if (apenRad) {
        const rad = fakturaer.find((x) => x.faktura_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
