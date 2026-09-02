// Bankavstemmingsagenten (M-13 v1) — AVSTEMMINGSREGISTERET.
//
// FLATENS VIKTIGSTE JOBB er å vise hva som IKKE stemmer: hvilke
// bankposter som står uavstemt og hvor lenge, og hvilke bilag som er
// forfalt uten full dekning. Som TEKST, ikke bare farge (WCAG 1.4.1):
// «uavstemt i 41 døgn» og «forfalt for 12 døgn» står som ord i sine egne
// celler, og på raden står ordet i tillegg som et eget merke. En rød rad
// alene sier ingenting til den som ikke ser farge.
//
// FLATEN VISER, DEN REGNER IKKE. `alder_dogn`, `dogn_over_forfall`,
// `dekket_ore`, `rest_ore` og funnlisten er regnet i BASEN, i samme
// skann som raden (101s lesedører), nettopp for at flaten ikke skal
// trekke to datoer fra hverandre eller summere penger i JavaScript
// (M-16-regelen).
//
// SAMMENDRAGET KOMMER FRA SIN EGEN DØR og teller over ALT. Listene er
// avkortet på 200; hadde sammendraget vært regnet fra dem, ville flaten
// sagt «tre uavstemte poster» når det var tre hundre — og tallet ville
// vært mest galt nettopp den dagen det betydde mest. Står det mer enn
// listen viser, SIER flaten det.
//
// DET FINNES INGEN «BOKFØR»-KNAPP, og fraværet er dommen: katalogen
// lover automatisk bokføring ved full match, v1 avstemmer og viser.
// Undertittelen sier det, så ingen leter etter en knapp som ikke finnes.
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] på kolonnene
// og th[scope=row] på cellen som navngir raden. Uten radoverskriften
// mister en skjermleser i beløps- og alderskolonnene hvilken post tallet
// gjelder. Wrapperen `.tablewrap` er sidescrollens container — uten den
// klemmer nettleseren kolonnene mot min-content.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, avstem, hentJson, nyIdempotensnokkel,
  registrerBankpost, registrerBilag, registrerKonto,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

const RETNINGER = ["inn", "ut"];
const METODER = ["automatisk", "manuell"];

// BELØP FORMATERES I HELTALLSARITMETIKK, aldri via `/100`. Et flyttall
// her ville gitt «1234,5599999999999» på et beløp som er nøyaktig i
// basen — og en avstemmingsflate som viser et annet tall enn registeret
// er verre enn ingen flate. `Math.trunc` og `%` på et heltall er
// eksakt, og API-taket (10^13 øre) ligger godt under
// `Number.MAX_SAFE_INTEGER`, så tallet kommer helt fram gjennom JSON.
export function belopTekst(ore) {
  if (typeof ore !== "number" || !Number.isInteger(ore)) return "—";
  const neg = ore < 0;
  const a = Math.abs(ore);
  const kr = Math.trunc(a / 100);
  const rest = String(a % 100).padStart(2, "0");
  return `${neg ? "-" : ""}${kr},${rest}`;
}

// Alderskolonnens ORD. Ett tall inn, én setning ut — ingen utregning.
//
// ENTALL HAR SIN EGEN NØKKEL (M-21/M-34-lærdommen): locale-settet har
// ingen pluralmaskineri, og «for 1 days» ville stått på nøyaktig den
// raden et menneske leser først. Norsk «døgn» bøyes ikke og hadde klart
// seg; engelsk gjør det, og en oversettelse som er riktig bare på det
// ene språket er ikke riktig.
export function alderTekst(dogn) {
  if (typeof dogn !== "number") return "—";
  return dogn === 1
    ? t("ui.avstemming.alder_ett_dogn")
    : t("ui.avstemming.alder_dogn").replace("{dogn}", String(dogn));
}

export function forfallTekst(dogn) {
  if (typeof dogn !== "number") return t("ui.avstemming.uten_forfall");
  if (dogn > 0) {
    return dogn === 1
      ? t("ui.avstemming.forfalt_ett_dogn")
      : t("ui.avstemming.forfalt_for").replace("{dogn}", String(dogn));
  }
  if (dogn === 0) return t("ui.avstemming.forfaller_i_dag");
  const n = Math.abs(dogn);
  return n === 1
    ? t("ui.avstemming.om_ett_dogn")
    : t("ui.avstemming.om_dogn").replace("{dogn}", String(n));
}

// Et bilag er FORFALT når fristen er passert. `dogn_over_forfall` er
// `null` for bilag uten forfallsdato — de er åpne, ikke forbigåtte, og
// et merke på dem ville sagt at noen har oversittet noe ingen avtalte.
export function erForfalt(b) {
  return typeof b.dogn_over_forfall === "number" && b.dogn_over_forfall > 0;
}

function postrad(p, ctx, apneMatch) {
  const rad = el("tr", {});
  // BANKREFERANSEN NAVNGIR raden. Den er bankens egen id — det et
  // menneske slår opp i nettbanken — og den skal BRYTE på lange
  // strenger: `celle-id` står på <th>, ikke på et <span> inni, fordi
  // `max-width` ikke gjør noe på et inline-element.
  rad.append(el("th", { scope: "row", class: "celle-id",
                        text: p.ekstern_ref }));
  rad.append(el("td", { text: `${p.konto_navn} - ${p.konto_hale}` }));
  rad.append(el("td", { text: p.bokfort }));
  // BELØPET HØYREJUSTERES OG BRUKER TABULARE SIFRE (`celle-tall`):
  // kolonner med tall som ikke står under hverandre kan ikke skummes,
  // og det er hele grunnen til at kolonnen finnes.
  rad.append(el("td", { class: "celle-tall",
    text: `${belopTekst(p.belop_ore)} ${p.valuta}` }));
  rad.append(el("td", { class: "celle-tekst", text: p.tekst }));
  rad.append(el("td", { class: "celle-tekst", text: p.motpart || "—" }));

  const alderscelle = el("td", {},
    el("span", { text: alderTekst(p.alder_dogn) }));
  if ((p.apne_funn || []).includes("uavstemt_post_over_grense")) {
    // MERKET ER TEKST. Dette er flatens viktigste opplysning på denne
    // raden, og den skal ikke være en farge.
    alderscelle.append(" ", el("strong", { class: "merke",
      text: t("ui.avstemming.merke_over_grense") }));
  }
  rad.append(alderscelle);

  const handling = el("td", {});
  if (harScope(ctx, "bestilling:opprett")) {
    const knapp = el("button", { type: "button",
      text: t("ui.avstemming.knapp.avstem") });
    knapp.addEventListener("click", () => apneMatch(p));
    handling.append(knapp);
  }
  rad.append(handling);
  return rad;
}

function bilagsrad(b) {
  const rad = el("tr", {});
  rad.append(el("th", { scope: "row", class: "celle-id",
                        text: b.bilagsnummer }));
  rad.append(el("td", { text: t(`ui.avstemming.retning.${b.retning}`) }));
  rad.append(el("td", { class: "celle-tekst", text: b.motpart }));
  rad.append(el("td", { class: "celle-tall",
                        text: belopTekst(b.belop_ore) }));
  rad.append(el("td", { class: "celle-tall",
                        text: belopTekst(b.dekket_ore) }));
  // RESTBELØPET er tallet raden finnes for. Det er regnet i basen som
  // `belop_ore - dekket_ore`; flaten gjentar ikke regnestykket.
  rad.append(el("td", { class: "celle-tall",
                        text: belopTekst(b.rest_ore) }));

  const forfallcelle = el("td", {},
    el("span", { text: forfallTekst(b.dogn_over_forfall) }));
  const funn = b.apne_funn || [];
  if (funn.includes("forfalt_bilag_uten_dekning")) {
    forfallcelle.append(" ", el("strong", { class: "merke",
      text: t("ui.avstemming.merke_uten_dekning") }));
  } else if (funn.includes("delvis_dekket_bilag")) {
    // TO FUNNTYPER, TO MERKER, og de utelukker hverandre: et bilag er
    // enten helt udekket eller delvis dekket. Ett felles merke ville
    // skjult hvilken av de to handlingene raden ber om — å purre, eller
    // å lete etter den manglende posten.
    forfallcelle.append(" ", el("strong", { class: "merke",
      text: t("ui.avstemming.merke_delvis") }));
  }
  rad.append(forfallcelle);
  return rad;
}

function postTabell(poster, ctx, apneMatch) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.avstemming.poster.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.avstemming.kolonne.referanse") }),
    el("th", { scope: "col", text: t("ui.avstemming.kolonne.konto") }),
    el("th", { scope: "col", text: t("ui.avstemming.kolonne.bokfort") }),
    el("th", { scope: "col", text: t("ui.avstemming.kolonne.belop") }),
    el("th", { scope: "col", text: t("ui.avstemming.kolonne.tekst") }),
    el("th", { scope: "col", text: t("ui.avstemming.kolonne.motpart") }),
    el("th", { scope: "col", text: t("ui.avstemming.kolonne.alder") }),
    el("th", { scope: "col",
               text: t("ui.avstemming.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const p of poster) tbody.append(postrad(p, ctx, apneMatch));
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function bilagsTabell(bilag) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.avstemming.bilag.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col",
               text: t("ui.avstemming.kolonne.bilagsnummer") }),
    el("th", { scope: "col", text: t("ui.avstemming.kolonne.retning") }),
    el("th", { scope: "col", text: t("ui.avstemming.kolonne.motpart") }),
    el("th", { scope: "col", text: t("ui.avstemming.kolonne.belop") }),
    el("th", { scope: "col", text: t("ui.avstemming.kolonne.dekket") }),
    el("th", { scope: "col", text: t("ui.avstemming.kolonne.rest") }),
    el("th", { scope: "col",
               text: t("ui.avstemming.kolonne.forfall") }))));
  const tbody = el("tbody");
  for (const b of bilag) tbody.append(bilagsrad(b));
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

// ÉN GRUPPE PER FELT (M-34-formen): etikett, kontroll og hjelpetekst
// hører sammen — ligger de som løse søsken, sprer rutenettet dem i hver
// sin celle, og etiketten mister den visuelle koblingen til feltet sitt
// uansett hva `for`-attributtet sier.
function felt(id, tekst, kontroll, hjelp) {
  return el("div", { class: "felt" },
    el("label", { for: id, text: t(tekst) }),
    kontroll,
    hjelp ? el("p", { class: "muted", text: t(hjelp) }) : null);
}

// Rammen alle skjemaene deler: idempotensnøkkel per intensjon,
// knappelås, feilvisning og gjenlasting. Fem nesten like kopier er fire
// steder å glemme at en 4xx FORBRUKER nøkkelen.
function skjemaramme(ctx, last, { skjema, knapp, utfall, send,
                                  tilbakestill, okNokkel }) {
  // Én nøkkel per intensjon (PR-014 R1): nullstilles når innholdet
  // endres, og ved 4xx — et avvist forsøk har FORBRUKT nøkkelen, og et
  // rettet skjema er en ny intensjon som skal ha sin egen.
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
          ? t("ui.avstemming.feil.tilstand")
          : t("ui.avstemming.feil.generell") }));
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

// KRONER INN, ØRE UT. `Math.round` på produktet er den ENESTE veien: en
// `parseFloat` uten avrunding gir 1234.5599999999999 øre på 12,3456
// kroner, og et regnskap tåler ikke et flyttall som nesten stemmer.
// `Number.isFinite` fanger tomt felt og søppel før avrundingen, så
// `NaN` aldri når API-et som et «beløp».
function tilOre(verdi) {
  const n = Number(verdi);
  if (!Number.isFinite(n)) return null;
  return Math.round(n * 100);
}

function kontoSkjema(ctx, last) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const navn = el("input", { id: "avst-konto-navn", name: "navn",
    type: "text", required: true, maxlength: 200 });
  const nummer = el("input", { id: "avst-konto-nummer",
    name: "kontonummer", type: "text", required: true, maxlength: 64 });
  const valuta = el("input", { id: "avst-konto-valuta", name: "valuta",
    type: "text", required: true, maxlength: 8, value: "NOK" });
  const knapp = el("button", { type: "submit",
    text: t("ui.avstemming.knapp.registrer_konto") });
  skjema.append(
    felt("avst-konto-navn", "ui.avstemming.skjema.konto_navn", navn),
    // HJELPETEKSTEN SIER HVA SOM SKJER MED NUMMERET. Uten den ser feltet
    // ut som enhver annen lagring av et kontonummer, og brukeren har
    // ingen måte å vite at bare de fire siste sifrene blir liggende.
    felt("avst-konto-nummer", "ui.avstemming.skjema.konto_nummer", nummer,
         "ui.avstemming.skjema.konto_nummerhjelp"),
    felt("avst-konto-valuta", "ui.avstemming.skjema.konto_valuta", valuta),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall,
    okNokkel: "ui.avstemming.skjema.konto_ok",
    send: (idem) => registrerKonto({
      navn: navn.value, kontonummer: nummer.value,
      valuta: valuta.value.toUpperCase(),
    }, idem),
    tilbakestill: () => { navn.value = ""; nummer.value = ""; },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.avstemming.skjema.konto_tittel") }),
    skjema, utfall);
}

function bankpostSkjema(ctx, last, kontoer) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const konto = el("select", { id: "avst-post-konto", name: "konto_id",
    required: true });
  for (const k of kontoer) {
    konto.append(el("option", { value: k.konto_id,
      text: `${k.navn} - ${k.kontonummer_hale} (${k.valuta})` }));
  }
  const ref = el("input", { id: "avst-post-ref", name: "ekstern_ref",
    type: "text", required: true, maxlength: 200 });
  const bokfort = el("input", { id: "avst-post-bokfort", name: "bokfort",
    type: "date", required: true });
  const belop = el("input", { id: "avst-post-belop", name: "belop",
    type: "number", required: true, step: "0.01" });
  const tekst = el("input", { id: "avst-post-tekst", name: "tekst",
    type: "text", required: true, maxlength: 1000 });
  const motpart = el("input", { id: "avst-post-motpart", name: "motpart",
    type: "text", maxlength: 300 });
  const knapp = el("button", { type: "submit",
    text: t("ui.avstemming.knapp.registrer_post") });
  // INGEN KONTO ER EN ÆRLIG BESKJED, ikke en tom nedtrekk. Første gang
  // finnes ingen konto, og da skal skjemaet si hvorfor det ikke kan
  // brukes ennå — ikke la brukeren fylle ut fem felt og få 404.
  if (!kontoer.length) knapp.disabled = true;
  skjema.append(
    felt("avst-post-konto", "ui.avstemming.skjema.post_konto", konto,
         kontoer.length ? null : "ui.avstemming.skjema.post_ingen_konto"),
    // BANKENS REFERANSE ER DEN VIRKELIGE IDEMPOTENSEN, og hjelpeteksten
    // sier det: samme kontoutskrift lastet to ganger gir de samme
    // radene, ikke dobbelt så mange.
    felt("avst-post-ref", "ui.avstemming.skjema.post_ref", ref,
         "ui.avstemming.skjema.post_refhjelp"),
    felt("avst-post-bokfort", "ui.avstemming.skjema.post_bokfort",
         bokfort),
    felt("avst-post-belop", "ui.avstemming.skjema.post_belop", belop,
         "ui.avstemming.skjema.post_belophjelp"),
    felt("avst-post-tekst", "ui.avstemming.skjema.post_tekst", tekst),
    felt("avst-post-motpart", "ui.avstemming.skjema.post_motpart",
         motpart),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall,
    okNokkel: "ui.avstemming.skjema.post_ok",
    send: (idem) => registrerBankpost({
      konto_id: konto.value, ekstern_ref: ref.value,
      bokfort: bokfort.value, belop_ore: tilOre(belop.value),
      tekst: tekst.value, motpart: motpart.value || null,
    }, idem),
    tilbakestill: () => {
      ref.value = ""; belop.value = ""; tekst.value = "";
      motpart.value = "";
    },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.avstemming.skjema.post_tittel") }),
    skjema, utfall);
}

function bilagSkjema(ctx, last) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const nummer = el("input", { id: "avst-bilag-nummer",
    name: "bilagsnummer", type: "text", required: true, maxlength: 100 });
  const retning = el("select", { id: "avst-bilag-retning",
    name: "retning", required: true });
  for (const r of RETNINGER) {
    retning.append(el("option", { value: r,
      text: t(`ui.avstemming.retning.${r}`) }));
  }
  const belop = el("input", { id: "avst-bilag-belop", name: "belop",
    type: "number", required: true, step: "0.01", min: "0.01" });
  const motpart = el("input", { id: "avst-bilag-motpart", name: "motpart",
    type: "text", required: true, maxlength: 300 });
  const utstedt = el("input", { id: "avst-bilag-utstedt", name: "utstedt",
    type: "date", required: true });
  const forfall = el("input", { id: "avst-bilag-forfall", name: "forfall",
    type: "date" });
  const knapp = el("button", { type: "submit",
    text: t("ui.avstemming.knapp.registrer_bilag") });
  skjema.append(
    felt("avst-bilag-nummer", "ui.avstemming.skjema.bilag_nummer", nummer),
    // RETNINGEN BÆRER FORTEGNET, og hjelpeteksten sier det: beløpet er
    // alltid positivt her. Uten den ville en bruker skrevet -5000 på en
    // leverandørfaktura og fått en 400 uten å forstå hvorfor.
    felt("avst-bilag-retning", "ui.avstemming.skjema.bilag_retning",
         retning, "ui.avstemming.skjema.bilag_retninghjelp"),
    felt("avst-bilag-belop", "ui.avstemming.skjema.bilag_belop", belop),
    felt("avst-bilag-motpart", "ui.avstemming.skjema.bilag_motpart",
         motpart),
    felt("avst-bilag-utstedt", "ui.avstemming.skjema.bilag_utstedt",
         utstedt),
    felt("avst-bilag-forfall", "ui.avstemming.skjema.bilag_forfall",
         forfall),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall,
    okNokkel: "ui.avstemming.skjema.bilag_ok",
    send: (idem) => registrerBilag({
      bilagsnummer: nummer.value, retning: retning.value,
      belop_ore: tilOre(belop.value), motpart: motpart.value,
      utstedt: utstedt.value, forfall: forfall.value || null,
    }, idem),
    tilbakestill: () => {
      nummer.value = ""; belop.value = ""; motpart.value = "";
    },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.avstemming.skjema.bilag_tittel") }),
    skjema, utfall);
}

// MATCHDIALOGEN. Åpnes fra en postrad, og bilaget velges fra listen over
// dem som faktisk har restbeløp — en flate som lot deg velge et fullt
// dekket bilag ville gitt 409 fra døren og ingen forklaring.
//
// FORTEGNET FILTRERER LISTEN: en inngående post kan bare dekke et
// `inn`-bilag. Døren og vakten feller dommen uansett; filteret her er
// ergonomi, ikke sikkerhet — det fjerner valgene som garantert blir 409.
function matchdialog(ctx, last, bilag) {
  // UTFALLET LIGGER UTENFOR DET SOM SKJULES (M-34-lærdommen): boksen
  // lukker seg når matchen er registrert — og lå live-regionen inne i
  // den, ble bekreftelsen både usynlig og uannonsert i nøyaktig det
  // øyeblikket den hadde noe å si. Det er `innhold` som skjules.
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;

  const beskrivelse = el("p", { class: "muted" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const valg = el("select", { id: "avst-match-bilag", name: "bilag_id",
    required: true });
  const metode = el("select", { id: "avst-match-metode", name: "metode",
    required: true });
  for (const m of METODER) {
    metode.append(el("option", { value: m,
      text: t(`ui.avstemming.metode.${m}`) }));
  }
  metode.value = "manuell";
  const begrunnelse = el("input", { id: "avst-match-begrunnelse",
    name: "begrunnelse", type: "text", maxlength: 2000 });
  const knapp = el("button", { type: "submit",
    text: t("ui.avstemming.knapp.match") });
  skjema.append(
    felt("avst-match-bilag", "ui.avstemming.dialog.velg_bilag", valg),
    felt("avst-match-metode", "ui.avstemming.dialog.metode", metode),
    // EN MANUELL MATCH KREVER EN BEGRUNNELSE, og hjelpeteksten sier
    // hvorfor — ikke «feltet er påkrevd», men «en manuell match er
    // nettopp det tilfellet der regelen ikke traff». CHECK-en i 101
    // feller dommen; skjemaet er ergonomi.
    felt("avst-match-begrunnelse", "ui.avstemming.dialog.begrunnelse",
         begrunnelse, "ui.avstemming.dialog.begrunnelsehjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  innhold.append(el("h3", { text: t("ui.avstemming.dialog.tittel") }),
    beskrivelse, skjema);
  boks.append(innhold, utfall);
  innhold.hidden = true;

  skjemaramme(ctx, last, {
    skjema, knapp, utfall,
    okNokkel: "ui.avstemming.dialog.ok",
    send: (idem) => avstem({
      post_id: gjeldende.post_id, bilag_id: valg.value,
      metode: metode.value, begrunnelse: begrunnelse.value || null,
    }, idem),
    tilbakestill: () => { begrunnelse.value = ""; innhold.hidden = true; },
  });

  return {
    node: boks,
    apne(post) {
      gjeldende = post;
      begrunnelse.value = "";
      sett(valg);
      const inn = post.belop_ore > 0;
      const aktuelle = bilag.filter(
        (b) => (b.retning === "inn") === inn && b.rest_ore > 0);
      for (const b of aktuelle) {
        valg.append(el("option", { value: b.bilag_id,
          text: `${b.bilagsnummer} - ${b.motpart} - `
            + `${t("ui.avstemming.dialog.rest")} `
            + `${belopTekst(b.rest_ore)}` }));
      }
      // INGEN AKTUELLE BILAG ER EN ÆRLIG BESKJED, ikke en tom nedtrekk.
      // En tom liste uten forklaring leses som at flaten er ødelagt.
      knapp.disabled = aktuelle.length === 0;
      beskrivelse.textContent = aktuelle.length
        ? `${post.ekstern_ref} - ${belopTekst(post.belop_ore)} `
          + `- ${post.tekst}`
        : t("ui.avstemming.dialog.ingen_bilag");
      sett(utfall);
      innhold.hidden = false;
      valg.focus();
    },
  };
}

// Sammendraget over tabellene. TALLENE KOMMER FRA SIN EGEN DØR og
// gjelder ALT — ikke bare det listene viser. Setningen sier det som ord,
// ikke som tall i bokser: «12 av 340 bankposter står uavstemt» er en
// påstand et menneske kan handle på.
function sammendrag(s) {
  const p = el("p", {
    text: t("ui.avstemming.sammendrag")
      .replace("{uavstemt}", String(s.poster_uavstemt))
      .replace("{poster}", String(s.poster_totalt))
      .replace("{belop}", belopTekst(s.uavstemt_ore))
      .replace("{bilag}", String(s.bilag_apne))
      .replace("{rest}", belopTekst(s.rest_ore)) });
  // AVKORTINGEN SIES HØYT. Uten dette ville flaten sett komplett ut
  // nettopp når den var det minst — og «vi har tolv uavstemte poster»
  // ville vært en påstand ingen kunne stole på.
  const avkortet = [];
  if (s.poster_vist < s.poster_uavstemt) {
    avkortet.push(t("ui.avstemming.avkortet_poster")
      .replace("{vist}", String(s.poster_vist)));
  }
  if (s.bilag_vist < s.bilag_apne) {
    avkortet.push(t("ui.avstemming.avkortet_bilag")
      .replace("{vist}", String(s.bilag_vist)));
  }
  if (avkortet.length) {
    p.append(" ", el("strong", { text: avkortet.join(" ") }));
  }
  return p;
}

export function visAvstemming(hoved, ctx) {
  const hode = () => flateHode(t("ui.avstemming.tittel"),
    t("ui.avstemming.undertittel"));
  sett(hoved, ...hode());
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kropp);
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/avstemming"),
    (d) => {
      sett(hoved, ...hode(), kropp);
      const s = d.sammendrag || {};
      const kontoer = d.kontoer || [];
      const poster = d.poster || [];
      const bilag = d.bilag || [];
      const match = matchdialog(ctx, last, bilag);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.avstemming.oversikt.tittel") }),
        sammendrag(s));

      const postseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.avstemming.poster.tittel") }));
      if (!poster.length) {
        // ÆRLIG TOMTILSTAND: null uavstemte poster kan bety at alt
        // stemmer — eller at ingen har importert en kontoutskrift. De to
        // er ikke det samme, og setningen skiller dem.
        postseksjon.append(el("p", { class: "muted",
          text: s.poster_totalt
            ? t("ui.avstemming.poster.alt_avstemt")
            : t("ui.avstemming.poster.ingen") }));
      } else {
        postseksjon.append(postTabell(poster, ctx, match.apne));
      }

      const bilagsseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.avstemming.bilag.tittel") }));
      if (!bilag.length) {
        bilagsseksjon.append(el("p", { class: "muted",
          text: t("ui.avstemming.bilag.ingen") }));
      } else {
        bilagsseksjon.append(bilagsTabell(bilag));
      }

      const deler = [oversikt, postseksjon, bilagsseksjon];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(match.node, kontoSkjema(ctx, last),
          bankpostSkjema(ctx, last, kontoer), bilagSkjema(ctx, last));
      }
      sett(kropp, ...deler);
    });
  last();
}
