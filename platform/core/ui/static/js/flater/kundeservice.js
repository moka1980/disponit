// Kundeserviceagenten (M-17 v1, PR-A) — HENVENDELSESREGISTERET.
//
// FLATENS VIKTIGSTE JOBB er å vise hva som står og venter: hvilke
// henvendelser som er uklassifiserte, hvilke som er ubesvarte forbi
// fristen, og hvilke som er merket mistenkelige uten å være behandlet.
// Som TEKST, ikke bare farge (WCAG 1.4.1): «Over svarfristen» står som
// ord i sin egen celle.
//
// KØEN OG INNHOLDET ER TO HANDLINGER, og flaten er bygget rundt det.
// Listen viser hvem som spurte (som HASH), når, og hvor gammelt — aldri
// hva de skrev. Teksten hentes av et EGET kall per henvendelse, bak et
// eget scope, når et menneske faktisk åpner den. Et listekall som dro
// med seg hver kundetekst ville gjort ett skjermbilde til en full
// eksport av persondata.
//
// FLATEN VISER, DEN REGNER IKKE. `alder_dogn`, funnlisten og
// sammendragets tall er regnet i BASEN (102s lesedører), nettopp for at
// flaten ikke skal trekke to datoer fra hverandre eller telle en
// avkortet liste (M-16-regelen).
//
// DET FINNES INGEN «SEND»-KNAPP, og fraværet er dommen: flaten sender
// ingenting. Utkastet får tre dommer: `forkastet`, `brukt_manuelt` (et
// MENNESKE sendte selv — sporet som lar henvendelsen lukkes som
// «besvart») og, fra ARC B (160), `godkjent`: et menneskes ja til at
// PLATTFORMEN sender svaret innenfor policyen. Selve sendingen er
// eiermodulens, aldri flatens.
//
// TABELLEN ER EKTE (m16-formen): <caption>, th[scope=col] på kolonnene
// og th[scope=row] på cellen som navngir raden. Wrapperen `.tablewrap`
// er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, avgjorUtkast, hentJson, henvendelseTilUnntakskoe,
  klassifiserHenvendelse, lagreUtkast, lukkHenvendelse,
  tildelHenvendelse, hentHenvendelsesspor, hentMedlemmer,
  hentSvarmaler, lagreSvarmal, slettSvarmal, sendFraPostboks,
  hentSendteSvar,
  hentFullmakter, hentStilleregler, nyIdempotensnokkel,
  settFullmakter, settKundeserviceavsender, settStilleregler,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";
// LESEVISNINGEN ER E-POSTMODULENS (#475): samme tekst, samme rensing,
// samme bryter. En egen variant her var feilen eier meldte 16/9.
import { lesbarTekst } from "./epost.js";

const PRIORITETER = ["kritisk", "hoy", "normal", "lav"];
const TEMAER = ["faktura", "leveranse", "teknisk", "salg", "klage",
                "annet"];
const HANDLINGSTYPER = ["svar_kreves", "til_info", "oppgave", "mote",
                        "nyhetsbrev", "mistenkelig"];
// ET UTKAST VENTER så lenge det er foreslått (på et menneske) eller
// godkjent (på plattformen). Alt annet er spor.
const VENTER = ["foreslatt", "godkjent"];

// FUNNTYPE → MERKETEKST. Kartet er en LUKKET tabell og ikke en
// strengbygging: en funntype flaten ikke kjenner skal ikke bli et merke
// som heter «ui.kundeservice.merke_<noe>» på skjermen. Porten i
// `test_m17_kundeservice.py` måler at basens tre og flatens tre er de
// samme.
const MERKE = {
  uklassifisert_over_grense: "ui.kundeservice.merke_uklassifisert",
  ubesvart_over_grense: "ui.kundeservice.merke_ubesvart",
  mistenkelig_uten_behandling: "ui.kundeservice.merke_mistenkelig",
};

// Alderskolonnens ORD. Ett tall inn, én setning ut — ingen utregning.
//
// ENTALL HAR SIN EGEN NØKKEL (M-21/M-34/M-13-lærdommen): locale-settet
// har ingen pluralmaskineri, og «1 days» ville stått på nøyaktig den
// raden et menneske leser først.
export function alderTekst(dogn) {
  if (typeof dogn !== "number") return "—";
  if (dogn <= 0) return t("ui.kundeservice.alder_i_dag");
  return dogn === 1
    ? t("ui.kundeservice.alder_ett_dogn")
    : t("ui.kundeservice.alder_dogn").replace("{dogn}", String(dogn));
}

// Klassifiseringen som én lesbar celle — eller den ærlige setningen om
// at den mangler. En tom celle ville lest som «normal», og det er
// nettopp den forvekslingen sveipen finnes for å hindre.
export function klassifiseringTekst(h) {
  if (!h.prioritet) return t("ui.kundeservice.uklassifisert");
  return [t(`ui.kundeservice.prioritet.${h.prioritet}`),
          t(`ui.kundeservice.tema.${h.tema}`),
          t(`ui.kundeservice.handlingstype.${h.handlingstype}`)].join(" · ");
}

/** Kort gjenkjennelsesmerke av en leverandørreferanse.
 *
 * SETT PÅ SKJERMEN 15/9: Graphs melding-id-er er ~150 tegn base64, og
 * `celle-id` (22ch, `overflow-wrap: anywhere`) rakk ikke over dem —
 * kolonnen veltet inn i Kanal, Mottatt og Alder, og køen ble uleselig.
 * Testfixturen hadde `MSG-2026-0001`, tretten tegn; porten hadde aldri
 * sett en ekte id.
 *
 * Ingen leser 150 tegn; de MATCHER dem. Derfor hode + hale, som en
 * git-sha: nok til å kjenne igjen, aldri nok til å fylle en rad. Hele
 * verdien står i `title` og i detaljpanelet.
 */
export function kortref(ref) {
  const r = String(ref ?? "");
  if (r.length <= 24) return r;
  return `${r.slice(0, 10)}…${r.slice(-8)}`;
}

/** Avsenderen slik listen kan vise den: masken, aldri adressen. */
export function avsenderTekst(h) {
  return h.har_avsender && h.avsender_maske
    ? h.avsender_maske
    : t("ui.kundeservice.avsender.ukjent");
}

// FUNNENE SOM FORTSATT GJELDER. Sveipen lukker et funn først i NESTE
// runde (én gang i døgnet); en rad som ble klassifisert etter forrige
// sveip — av en regel (204) eller et menneske — bar ellers «over
// klassifiseringsfristen» et døgn etter at grunnen forsvant. SETT PÅ
// SKJERMEN 16/9 på tolv rader. Ingen utregning: raden vet alt selv.
export function funnFor(h) {
  return (h.apne_funn || []).filter(
    (f) => !(f === "uklassifisert_over_grense" && h.prioritet));
}

// HVEM TRENGER ET MENNESKE. Det uklassifiserte, det i unntakskøen, det
// med et åpent funn, og alt et menneske må gjøre noe med. Resten — til
// info og nyhetsbrev — er stille og står samlet under køen, med tallet.
const STILLE = new Set(["til_info", "nyhetsbrev"]);
export function trengerMenneske(h) {
  return !h.prioritet || !!h.i_unntakskoe || funnFor(h).length > 0
    || !STILLE.has(h.handlingstype);
}

// 225: hvem som har saken, som tekst. Et medlem uten visningsnavn får
// «en kollega» — aldri en rå id.
export function hvemTekst(h) {
  if (!h.tildelt) return t("ui.kundeservice.tildelt.ledig");
  return h.tildelt.navn || t("ui.kundeservice.tildelt.uten_navn");
}

// Én sporrad som setning: «Tildelt Kari Nordmann», «Lukket (besvart)».
export function sporTekst(s) {
  let tekst = t(`ui.kundeservice.spor.${s.handling}`,
                t("ui.kundeservice.spor.annet"));
  if (s.handling === "henvendelse.tildelt") {
    tekst = tekst.replace("{til}",
      s.til || t("ui.kundeservice.tildelt.uten_navn"));
  }
  if (s.utfall) {
    tekst += ` (${t(`ui.kundeservice.utfall.${s.utfall}`, s.utfall)})`;
  }
  // 231: HVILKEN ADRESSE svaret gikk fra — det var nettopp det som ikke
  // syntes da et svar gikk fra feil adresse.
  if (s.postboks) {
    tekst += ` · ${t("ui.kundeservice.spor.fra").replace("{postboks}",
      s.postboks)}`;
  }
  if (s.grunn) tekst += ` (${s.grunn})`;
  return tekst;
}

export function sporHvem(s) {
  const h = s.hvem || {};
  if (h.type === "agent") return t("ui.kundeservice.spor.agent");
  if (h.type === "bruker" && h.navn) return h.navn;
  return t("ui.kundeservice.spor.ukjent");
}

function korad(h, ctx, apneDetalj, medEmne) {
  const rad = el("tr", {});
  if (medEmne) {
    // EMNET NAVNGIR RADEN (eiers beslutning 16/9, som #490 i innboksen):
    // det er det man skanner en kø etter. Det følger bare med for en økt
    // som har innholdsscopet (206) — ellers står avsenderen som før.
    rad.append(el("th", { scope: "row", class: "celle-tekst",
                          text: h.emne || "—" }));
    rad.append(el("td", { class: "celle-tekst", text: avsenderTekst(h) }));
  } else {
    // AVSENDEREN NAVNGIR raden. Masken er alt listen får lov å bære;
    // adressen, emnet og referansen kommer når raden åpnes.
    rad.append(el("th", { scope: "row", class: "celle-tekst",
                          text: avsenderTekst(h) }));
  }
  // NÅR, som én celle: dato, kanal og alder — og merkene som TEKST
  // (WCAG 1.4.1), for de er flatens viktigste opplysning på raden.
  const mottatt = el("td", { class: "celle-tekst" },
    el("span", { text: h.mottatt.slice(0, 10) }), " ",
    el("span", { class: "muted",
      text: `${t(`ui.kundeservice.kanal.${h.kanal}`)} · `
        + alderTekst(h.alder_dogn) }));
  for (const funn of funnFor(h)) {
    if (MERKE[funn]) {
      mottatt.append(" ", el("strong", { class: "merke",
        text: t(MERKE[funn]) }));
    }
  }
  if (h.i_unntakskoe) {
    mottatt.append(" ", el("strong", { class: "merke",
      text: t("ui.kundeservice.merke_i_koe") }));
  }
  rad.append(mottatt);

  rad.append(el("td", { class: "celle-tekst",
                        text: klassifiseringTekst(h) }));
  // 225: HVEM HAR SAKEN. «Ledig» er et ord, ikke en tom celle — en tom
  // celle leses som «vet ikke».
  rad.append(el("td", { class: "celle-tekst", text: hvemTekst(h) }));
  const utkastcelle = el("td", { class: "celle-tall",
    text: String(h.antall_utkast) });
  if (h.brukt_utkast) {
    utkastcelle.append(" ", el("span", { class: "muted",
      text: t("ui.kundeservice.utkast_brukt") }));
  }
  rad.append(utkastcelle);

  const handling = el("td", {});
  const knapp = el("button", { type: "button", class: "knapp liten",
    text: t("ui.kundeservice.knapp.apne") });
  knapp.addEventListener("click", () => apneDetalj(h));
  handling.append(knapp);
  rad.append(handling);
  return rad;
}

function koTabell(koe, ctx, apneDetalj, captionNokkel, medEmne) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t(captionNokkel) }));
  tb.append(el("thead", {}, el("tr", {},
    medEmne ? el("th", { scope: "col",
                         text: t("ui.kundeservice.detalj.emne") }) : null,
    el("th", { scope: "col",
               text: t("ui.kundeservice.kolonne.avsender") }),
    el("th", { scope: "col",
               text: t("ui.kundeservice.kolonne.mottatt") }),
    el("th", { scope: "col",
               text: t("ui.kundeservice.kolonne.klassifisering") }),
    el("th", { scope: "col", text: t("ui.kundeservice.kolonne.hvem") }),
    el("th", { scope: "col", text: t("ui.kundeservice.kolonne.utkast") }),
    el("th", { scope: "col",
               text: t("ui.kundeservice.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const h of koe) tbody.append(korad(h, ctx, apneDetalj, medEmne));
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

// KØBLOKKEN: det som trenger et menneske øverst; det stille samlet under
// én åpne/lukke-linje med tallet i, så tolv «til info» aldri skyver den
// ene kunden som venter på svar ut av skjermen.
function koBlokk(koe, ctx, apneDetalj) {
  const blokk = el("div", { class: "ks-koe" });
  if (!koe.length) {
    blokk.append(el("p", { class: "muted",
      text: t("ui.kundeservice.koe.ingen") }));
    return blokk;
  }
  const trenger = koe.filter(trengerMenneske);
  const stille = koe.filter((h) => !trengerMenneske(h));
  // Emnet finnes på radene bare når økten har innholdsscopet (206).
  const medEmne = koe.some((h) => "emne" in h);
  if (trenger.length) {
    blokk.append(koTabell(trenger, ctx, apneDetalj,
                          "ui.kundeservice.koe.caption", medEmne));
  } else {
    blokk.append(el("p", { class: "muted",
      text: t("ui.kundeservice.koe.trenger_ingen") }));
  }
  if (stille.length) {
    blokk.append(el("details", { class: "ks-stille-gruppe" },
      el("summary", { text: t("ui.kundeservice.koe.stille_gruppe")
        .replace("{antall}", String(stille.length)) }),
      koTabell(stille, ctx, apneDetalj,
               "ui.kundeservice.koe.stille_caption", medEmne)));
  }
  return blokk;
}

// HODETS BRIKKER: hvem (masken, eller adressens fravær som ord), når,
// dommen — og merkene som tekst.
function metaBrikker(h) {
  const brikke = (tekst) => el("li", { class: "brikke brikke-tekst",
                                       text: tekst });
  const ut = [
    brikke(h.har_avsender && h.avsender_maske
      ? h.avsender_maske : t("ui.kundeservice.avsender.mangler")),
    brikke(`${t(`ui.kundeservice.kanal.${h.kanal}`)} · `
      + `${h.mottatt.slice(0, 10)} · ${alderTekst(h.alder_dogn)}`),
    brikke(klassifiseringTekst(h)),
  ];
  for (const funn of funnFor(h)) {
    if (MERKE[funn]) {
      ut.push(el("li", { class: "brikke-fri" },
        el("strong", { class: "merke", text: t(MERKE[funn]) })));
    }
  }
  if (h.i_unntakskoe) {
    ut.push(el("li", { class: "brikke-fri" },
      el("strong", { class: "merke",
                     text: t("ui.kundeservice.merke_i_koe") })));
  }
  return ut;
}

function felt(id, tekst, kontroll, hjelp) {
  return el("div", { class: "felt" },
    el("label", { for: id, text: t(tekst) }),
    kontroll,
    hjelp ? el("p", { class: "muted", text: t(hjelp) }) : null);
}

// Rammen skjemaene deler: idempotensnøkkel per intensjon, knappelås,
// feilvisning og gjenlasting.
function skjemaramme(ctx, last, { skjema, knapp, utfall, send,
                                  tilbakestill, okNokkel, kvitter }) {
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
          ? t("ui.kundeservice.feil.tilstand")
          : t("ui.kundeservice.feil.generell") }));
      return;
    }
    idem = null;
    knapp.disabled = false;
    if (tilbakestill) tilbakestill();
    const ok = typeof okNokkel === "function" ? okNokkel() : t(okNokkel);
    meldLive(ok);
    // KVITTERINGEN SKAL OVERLEVE TEGNINGEN. `last()` bygger listen,
    // panelet og skjemaene på nytt, og en melding satt i skjemaets eget
    // `utfall` forsvant i samme øyeblikk den ble satt — brukeren trykket,
    // så skjermen blinke, og satt igjen uten å vite om det gikk bra.
    // Suksessen går derfor til flatens egen kvitteringslinje, som lever
    // utenfor tegningen. FEILEN blir stående i skjemaet, der den hører
    // hjemme: den veien tegner ikke om. (CodeRabbit på M-24, samme feil
    // i alle fem flatene i klyngen.)
    kvitter(ok);
    await last();
  });
}

// DETALJPANELET. Åpnes fra en rad, og henter INNHOLDET i et eget kall —
// det er her persondata faktisk krysser skjermen, og derfor er det her
// scopet gjelder.
//
// UTEN `kundeservice:innhold` vises panelet likevel: klassifisering
// og købeslutning er arbeid som ikke krever å lese teksten, og en flate
// som skjulte hele panelet ville sagt at den som ikke får lese heller
// ikke får jobbe. Setningen i stedet for teksten er den ærlige formen.
export function bestillingstekst(b) {
  const utfall = String(b.utfall || "");
  const nokkel = utfall.startsWith("feil:")
    ? "ui.kundeservice.bestilling.utfall.feil"
    : `ui.kundeservice.bestilling.utfall.${utfall}`;
  return t("ui.kundeservice.bestilling.linje")
    .replace("{utfall}", t(nokkel))
    .replace("{dato}", String(b.bestilt_ts || "").slice(0, 10));
}

// AVSENDEREN ER DET KUNDEN SER. Uten profil sies det høyt — før det
// første svaret går ut i tenantens id i stedet for et navn.
//: Fullmaktene i samme rekkefølge som registreringsskjemaet (207).
const FULLMAKTER = ["kundeservice-svar", "kampanje-send",
                    "purring-inkassovarsel", "tilbud-generer"];

// GJENVALGET (208): der varsellinjen står, står også veien ut av den —
// for den som har `policy:activate`, og bare mens policyen er den urørte
// bootstrap-raden. Listen hentes FØRST (aldri «[] + den nye», 204s
// lærdom), og boksene er de samme som ved registreringen.
function fullmaktSkjema(ctx, last, kvitter) {
  const boks = el("div", { class: "fullmakt-gjenvalg" },
    el("p", { class: "muted", text: t("ui.kundeservice.fullmakt.laster") }));
  (async () => {
    let d;
    try { d = await hentFullmakter(); } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(boks, el("p", { role: "alert",
        text: t("ui.kundeservice.feil.generell") }));
      return;
    }
    if (!d.kan_velge_om) {
      sett(boks, el("p", { class: "muted",
        text: t("ui.kundeservice.fullmakt.historikk") }));
      return;
    }
    const skjema = el("form", { class: "kv-skjema" });
    const bokser = FULLMAKTER.filter((n) => d.lov.includes(n)).map((navn) => {
      const b = el("input", { type: "checkbox", id: `ks-fm-${navn}`,
                              name: "fullmakter", value: navn });
      b.checked = d.valgte.includes(navn) || navn === "kundeservice-svar";
      return el("label", { class: "avkryssing", for: `ks-fm-${navn}` },
        b, el("span", { text: t(`ui.firmareg.fullmakt.${navn}`) }));
    });
    const utfall = el("p", { "aria-live": "polite" });
    const knapp = el("button", { type: "submit", class: "knapp primar",
      text: t("ui.kundeservice.fullmakt.knapp") });
    skjema.append(
      el("fieldset", { class: "fullmakter" },
        el("legend", { text: t("ui.kundeservice.fullmakt.tittel") }),
        ...bokser,
        el("p", { class: "muted skjema-hjelp",
                  text: t("ui.kundeservice.fullmakt.hjelp") })),
      el("div", { class: "skjema-bunn" }, knapp), utfall);
    skjemaramme(ctx, last, {
      skjema, knapp, utfall, kvitter,
      okNokkel: "ui.kundeservice.fullmakt.ok",
      send: (idem) => settFullmakter(
        bokser.map((l) => l.querySelector("input"))
          .filter((b) => b.checked).map((b) => b.value), idem),
    });
    sett(boks, skjema);
  })();
  return boks;
}

function avsenderSeksjon(a, s, ctx, last, kvitter) {
  const boks = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.kundeservice.avsender.tittel") }));
  // HAR PLATTFORMEN FULLMAKT? (207) Uten den sender planrunden ingenting,
  // uansett hvor mange svar som godkjennes — og det skal stå HER, som ord,
  // ikke oppdages ved at ingenting skjer. Fullmakten velges ved
  // registreringen eller gis gjennom policyen med fire øyne.
  if (s && "svar_fullmakt" in s) {
    boks.append(el("p", { class: s.svar_fullmakt ? "" : "hv-varsel" },
      el("strong", { text: t(s.svar_fullmakt
        ? "ui.kundeservice.avsender.fullmakt_ja"
        : "ui.kundeservice.avsender.fullmakt_nei") })));
    if (!s.svar_fullmakt && harScope(ctx, "policy:activate")) {
      boks.append(fullmaktSkjema(ctx, last, kvitter));
    }
  }
  if (!a || !a.avsender_navn) {
    boks.append(el("p", {}, el("strong", {
      text: t("ui.kundeservice.avsender.ingen") })));
    return boks;
  }
  boks.append(el("p", {
    text: t("ui.kundeservice.avsender.navn").replace("{navn}", a.avsender_navn) }));
  boks.append(el("p", { class: a.svar_til ? "" : "muted",
    text: a.svar_til
      ? t("ui.kundeservice.avsender.svar_til").replace("{adresse}", a.svar_til)
      : t("ui.kundeservice.avsender.uten_svar_til") }));
  if (a.signatur) {
    boks.append(el("p", { class: "muted",
      text: t("ui.kundeservice.avsender.signatur").replace("{signatur}", a.signatur) }));
  }
  return boks;
}

function avsenderSkjema(ctx, last, kvitter, a) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const navn = el("input", { id: "ks-avs-navn", name: "avsender_navn",
    type: "text", required: true, maxlength: 120,
    value: (a && a.avsender_navn) || "" });
  const svarTil = el("input", { id: "ks-avs-svar", name: "svar_til",
    type: "email", maxlength: 254, value: (a && a.svar_til) || "" });
  const signatur = el("textarea", { id: "ks-avs-signatur", name: "signatur",
    maxlength: 500, rows: "3" });
  signatur.value = (a && a.signatur) || "";
  const knapp = el("button", { type: "submit",
    text: t("ui.kundeservice.knapp.lagre_avsender") });
  skjema.append(
    felt("ks-avs-navn", "ui.kundeservice.skjema.avsender_navn", navn,
         "ui.kundeservice.skjema.avsender_navn_hjelp"),
    felt("ks-avs-svar", "ui.kundeservice.skjema.svar_til", svarTil,
         "ui.kundeservice.skjema.svar_til_hjelp"),
    felt("ks-avs-signatur", "ui.kundeservice.skjema.signatur", signatur,
         "ui.kundeservice.skjema.signatur_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.kundeservice.skjema.avsender_ok",
    send: (idem) => settKundeserviceavsender({
      avsender_navn: navn.value.trim(),
      svar_til: svarTil.value.trim() || null,
      signatur: signatur.value.trim() || null,
    }, idem),
    tilbakestill: () => {},
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.kundeservice.skjema.avsender_tittel") }),
    skjema, utfall);
}

//: Stille avsendere (204). Det tenanten kan si om en avsender.
const REGELARTER = ["domene", "adresse"];
const REGELHANDLINGER = ["til_info", "nyhetsbrev", "oppgave", "mistenkelig"];

/** Én regel som tekst — domenet som det er, adressen som «hash …»:
 *  adressen ble aldri lagret, og kan derfor ikke vises igjen. */
export function regelTekst(r) {
  const hva = r.art === "adresse"
    ? t("ui.kundeservice.stille.adresse_hash").replace("{hash}",
                                                       r.monster.slice(0, 8))
    : r.monster;
  return `${hva} → ${t(`ui.kundeservice.handlingstype.${r.handlingstype}`)}`;
}

/** Reglene slik de sendes tilbake: bare det API-et tar imot. For en
 *  adresseregel som alt finnes, er «monster» hashen — API-et hasher
 *  bare det som ser ut som en adresse, så hashen går uendret gjennom. */
function tilKropp(regler) {
  return regler.map((r) => ({ art: r.art, monster: r.monster,
                              handlingstype: r.handlingstype }));
}

function stilleSeksjon(ctx, last, kvitter) {
  const boks = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.kundeservice.stille.tittel") }),
    el("p", { class: "muted", text: t("ui.kundeservice.stille.forklaring") }));
  // BRIKKER, IKKE LISTE. Et sett på 3–30 regler er én flytende rad; en
  // punktliste med knapper som vandrer etter tekstlengden er en dump.
  const rad = el("ul", { class: "brikkerad",
                         "aria-label": t("ui.kundeservice.stille.tittel") });
  const status = el("p", { "aria-live": "polite" });
  boks.append(rad, status);
  let regler = [];
  // INGEN SKRIVING FØR LISTEN ER LASTET (CodeRabbit, major). Settet
  // sendes HELT hver gang, så en innsending før GET-en var ferdig ville
  // sendt «[] + den nye» — og slettet alle eksisterende regler i
  // stillhet. Knappene finnes derfor ikke, eller er sperret, til
  // `lastet` er sann. Og ÉN lagring om gangen.
  let lastet = false;
  let lagrer = false;
  const kanSkrive = harScope(ctx, "bestilling:opprett");

  // HELE SETTET, HVER GANG (purreplanformen). Idempotensnøkkel per
  // innsending.
  const lagre = async (nye) => {
    if (!lastet || lagrer) return;
    lagrer = true;
    try {
      await settStilleregler(tilKropp(nye), nyIdempotensnokkel());
    } finally {
      lagrer = false;
    }
    const ok = t("ui.kundeservice.stille.ok");
    meldLive(ok);
    kvitter(ok);
    await last();
  };

  const tegn = () => {
    sett(rad);
    if (!regler.length) {
      rad.append(el("li", { class: "muted",
                            text: t("ui.kundeservice.stille.ingen") }));
      return;
    }
    for (const r of regler) {
      const navn = r.art === "adresse"
        ? t("ui.kundeservice.stille.adresse_hash")
            .replace("{hash}", r.monster.slice(0, 8))
        : r.monster;
      const li = el("li", { class: "brikke" },
        el("span", { class: "brikke-navn", text: navn }),
        el("span", { class: "muted",
          text: t(`ui.kundeservice.handlingstype.${r.handlingstype}`) }));
      if (kanSkrive) {
        const fjern = el("button", { type: "button", class: "brikke-x",
          "aria-label": t("ui.kundeservice.stille.fjern_aria")
            .replace("{regel}", regelTekst(r)),
          text: "×" });
        fjern.addEventListener("click", async () => {
          fjern.disabled = true;
          try {
            await lagre(regler.filter((x) => x.regel_id !== r.regel_id));
          } catch (e) {
            fjern.disabled = false;
            if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
            sett(status, el("span", { role: "alert",
              text: t("ui.kundeservice.feil.generell") }));
          }
        });
        li.append(fjern);
      }
      rad.append(li);
    }
  };

  // `knapp` deklareres FØR lastingen fullfører, så den kan låses opp
  // derfra; uten skriverett finnes den ikke.
  let knapp = null;
  hentStilleregler().then((d) => {
    regler = d.regler || [];
    lastet = true;
    tegn();
    if (knapp) knapp.disabled = false;
  }).catch((e) => {
    if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
    sett(status, el("span", { role: "alert",
      text: t("ui.kundeservice.feil.generell") }));
  });

  if (!kanSkrive) return boks;

  // ÉN LINJE: type · verdi · handling · knapp. Etikettene står (WCAG),
  // hjelpen er én linje under hele raden.
  const skjema = el("form", { class: "kv-skjema kv-skjema-linje" });
  const art = el("select", { id: "ks-stille-art", name: "art" });
  for (const v of REGELARTER) {
    art.append(el("option", { value: v,
      text: t(`ui.kundeservice.stille.art.${v}`) }));
  }
  const monster = el("input", { id: "ks-stille-monster", name: "monster",
                                type: "text", required: "", maxlength: "400",
                                autocomplete: "off",
                                "aria-describedby": "ks-stille-hjelp" });
  const handling = el("select", { id: "ks-stille-handling",
                                  name: "handlingstype" });
  for (const v of REGELHANDLINGER) {
    handling.append(el("option", { value: v,
      text: t(`ui.kundeservice.handlingstype.${v}`) }));
  }
  // SPERRET til listen er lastet — `skjemaramme` sender ikke når knappen
  // er disabled, og det er hele vernet mot «[] + den nye».
  knapp = el("button", { type: "submit", class: "knapp primar",
                         disabled: "",
                         text: t("ui.kundeservice.stille.legg_til") });
  const utfall = el("p", { "aria-live": "polite" });
  skjema.append(
    el("div", { class: "felt" },
      el("label", { for: "ks-stille-art",
                    text: t("ui.kundeservice.stille.art") }), art),
    el("div", { class: "felt" },
      el("label", { for: "ks-stille-monster",
                    text: t("ui.kundeservice.stille.monster") }), monster),
    el("div", { class: "felt" },
      el("label", { for: "ks-stille-handling",
                    text: t("ui.kundeservice.skjema.handlingstype") }),
      handling),
    el("div", { class: "felt" }, knapp),
    el("p", { class: "muted skjema-hjelp", id: "ks-stille-hjelp",
              text: t("ui.kundeservice.stille.monster_hjelp") }));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.kundeservice.stille.ok",
    send: async (idem) => {
      if (!lastet || lagrer) {
        const e = new Error("stilleregler ikke lastet"); e.status = 409;
        throw e;
      }
      lagrer = true;
      try {
        await settStilleregler(
          [...tilKropp(regler),
           { art: art.value, monster: monster.value.trim(),
             handlingstype: handling.value }], idem);
      } finally {
        lagrer = false;
      }
    },
    tilbakestill: () => { monster.value = ""; },
  });
  boks.append(el("h3", { text: t("ui.kundeservice.stille.skjema_tittel") }),
              skjema, utfall);
  return boks;
}

function detaljpanel(ctx, last, kvitter, settApen, visKoe, sammendrag) {
  const boks = el("div", { class: "hv-detalj" });
  const innhold = el("div", { class: "hv-detalj-innhold" });
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;

  // HODET: emnet som det store, resten som brikker. Emnet er kundens
  // tekst, ikke flatens — derfor en `p`, ikke en overskrift; flatens
  // egen overskrift står som en liten linje over. Referansen står kort
  // (`kortref`) og hel i title.
  const overskrift = el("h3", { class: "hv-eyebrow",
                                text: t("ui.kundeservice.detalj.tittel") });
  const emne = el("p", { class: "hv-emne" });
  const meta = el("ul", { class: "brikkerad" });
  const ref = el("p", { class: "muted hv-ref" });
  const kropp = el("pre", { class: "epost-kropp hv-tekst" });
  // BRYTEREN VEKSLER, den legger ikke til (#475): én tekst på skjermen,
  // knappen bytter hvilken av de to man ser. Den finnes bare når
  // rensingen faktisk tok noe.
  const bytt = el("button", { type: "button", class: "knapp liten",
    text: t("ui.epost.meldinger.vis_raa"), "aria-pressed": "false" });
  const bytterad = el("div", { class: "knapperad" }, bytt);
  bytterad.hidden = true;
  let raaTekst = "";
  let renTekst = "";
  let raatt = false;
  const visKropp = () => {
    kropp.textContent = raatt
      ? raaTekst : (renTekst || t("ui.epost.meldinger.tom_kropp"));
    bytt.textContent = t(raatt ? "ui.epost.meldinger.vis_lesbar"
                               : "ui.epost.meldinger.vis_raa");
    bytt.setAttribute("aria-pressed", String(raatt));
  };
  bytt.addEventListener("click", () => { raatt = !raatt; visKropp(); });
  const utkastliste = el("div", { class: "hv-utkast" });
  // VEIEN TILBAKE. Detaljen bytter plass med køen (se visKundeservice),
  // så den må selv kunne gi plassen fra seg.
  const lukk = () => { innhold.hidden = true; settApen(null); visKoe(true); };
  const tilbake = el("button", { type: "button",
    class: "knapp liten hv-tilbake", text: t("ui.kundeservice.koe.tilbake") });
  tilbake.addEventListener("click", lukk);

  // --- klassifisering ---
  const kSkjema = el("form", { class: "kv-skjema kv-skjema-linje" });
  const prioritet = el("select", { id: "ks-prioritet", name: "prioritet",
    required: true });
  for (const v of PRIORITETER) {
    prioritet.append(el("option", { value: v,
      text: t(`ui.kundeservice.prioritet.${v}`) }));
  }
  prioritet.value = "normal";
  const tema = el("select", { id: "ks-tema", name: "tema",
    required: true });
  for (const v of TEMAER) {
    tema.append(el("option", { value: v,
      text: t(`ui.kundeservice.tema.${v}`) }));
  }
  const handlingstype = el("select", { id: "ks-handlingstype",
    name: "handlingstype", required: true });
  for (const v of HANDLINGSTYPER) {
    handlingstype.append(el("option", { value: v,
      text: t(`ui.kundeservice.handlingstype.${v}`) }));
  }
  const kKnapp = el("button", { type: "submit",
    text: t("ui.kundeservice.knapp.klassifiser") });
  kSkjema.append(
    felt("ks-prioritet", "ui.kundeservice.skjema.prioritet", prioritet),
    felt("ks-tema", "ui.kundeservice.skjema.tema", tema),
    felt("ks-handlingstype", "ui.kundeservice.skjema.handlingstype",
         handlingstype),
    el("div", { class: "felt" }, kKnapp),
    // HJELPETEKSTEN SIER HVA VALGET GJØR. «Svar kreves» er det som gjør
    // en ubesvart henvendelse til et funn; uten forklaringen ville
    // valget sett ut som en etikett. Én linje under hele raden.
    el("p", { class: "muted skjema-hjelp",
      text: t("ui.kundeservice.skjema.handlingstypehjelp") }));
  skjemaramme(ctx, last, {
    skjema: kSkjema, knapp: kKnapp, utfall, kvitter,
    okNokkel: "ui.kundeservice.skjema.klassifisering_ok",
    send: (idem) => klassifiserHenvendelse(gjeldende.henvendelse_id, {
      prioritet: prioritet.value, tema: tema.value,
      handlingstype: handlingstype.value,
    }, idem),
  });

  // --- utkast ---
  const uSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const utkasttekst = el("textarea", { id: "ks-utkast", name: "tekst",
    required: true, rows: "6" });
  // Å GODKJENNE ER ETT KLIKK (#490 speilet): før måtte man skrive,
  // «Lagre utkast», finne utkastet i lista og «Godkjenn». Primærknappen
  // gjør begge stegene; utkastet er fortsatt sannheten i basen, og
  // godkjenningen er fortsatt et menneskes ja til at PLATTFORMEN sender
  // innenfor policyen — flaten sender ingenting, og knappen sier det
  // (porten «utkastets tre dommer» måler ordet). «Lagre utkast» står
  // ved siden av for det man vil skrive ferdig senere.
  const uKnapp = el("button", { type: "submit", class: "knapp primar",
    text: t("ui.kundeservice.knapp.godkjenn_svar") });
  const uLagre = el("button", { type: "button",
    text: t("ui.kundeservice.knapp.utkast") });
  // 230: START FRA EN MAL. Firmaets egne formuleringer, valgt av den som
  // har saken — malen fylles inn i tekstfeltet og er derfra et helt vanlig
  // utkast: den kan endres fritt, og ingenting sendes før et menneske har
  // godkjent. Velgeren finnes bare når firmaet HAR maler.
  const malvelger = el("select", { id: "ks-mal", name: "mal" });
  const malfelt = felt("ks-mal", "ui.kundeservice.mal.velg", malvelger);
  malfelt.hidden = true;
  let malene = [];
  const tegnMalvelger = (tema) => {
    // Malene for sakens tema står ØVERST; resten følger alfabetisk.
    const sortert = [...malene].sort((a, b) =>
      (b.tema === tema && !!tema) - (a.tema === tema && !!tema));
    sett(malvelger, el("option", { value: "",
      text: t("ui.kundeservice.mal.velg_ingen") }),
      ...sortert.map((m) => el("option", { value: m.mal_id,
        text: m.tema ? `${m.navn} (${t(`ui.kundeservice.tema.${m.tema}`)})`
          : m.navn })));
    malfelt.hidden = malene.length === 0;
  };
  malvelger.addEventListener("change", () => {
    const m = malene.find((x) => x.mal_id === malvelger.value);
    malvelger.value = "";
    if (!m) return;
    // Har hun alt skrevet noe, legges malen TIL — den skriver aldri over.
    const fra = utkasttekst.value.trimEnd();
    utkasttekst.value = fra ? `${fra}\n\n${m.tekst}` : m.tekst;
    utkasttekst.dispatchEvent(new window.Event("input", { bubbles: true }));
    utkasttekst.focus();
  });
  uSkjema.append(
    malfelt,
    felt("ks-utkast", "ui.kundeservice.skjema.utkast_tekst", utkasttekst,
         "ui.kundeservice.skjema.utkast_teksthjelp"),
    el("div", { class: "skjema-bunn" }, uKnapp, uLagre));
  const utenPolicy = !!(sammendrag && "svar_fullmakt" in sammendrag
                        && !sammendrag.svar_fullmakt);
  // 231: HVOR SVARET GÅR FRA. Kom saken inn på en postboks som kan sende,
  // går svaret derfra — og da trengs ingen fullmakt: det er menneskets
  // eget svar (eiervedtak 10/9). Ellers går det fra plattformens adresse
  // med firmaets navn, og fullmakten gjelder som før (207).
  const fraPostboks = () => !!(gjeldende && gjeldende.svarpostboks
    && harScope(ctx, "epost:utkast:behandle"));
  let utenFullmakt = utenPolicy;
  const fullmaktNei = el("p", { class: "muted hv-varsel",
    text: t("ui.kundeservice.avsender.fullmakt_nei") });
  const svarvei = el("p", { class: "muted hv-svarvei" });
  uSkjema.append(svarvei, fullmaktNei);
  const tegnSvarvei = () => {
    const pb = fraPostboks();
    utenFullmakt = !pb && utenPolicy;
    uKnapp.textContent = pb
      ? t("ui.kundeservice.knapp.send_fra").replace("{postboks}",
        gjeldende.svarpostboks)
      : t("ui.kundeservice.knapp.godkjenn_svar");
    svarvei.textContent = pb
      ? t("ui.kundeservice.svarvei.postboks").replace("{postboks}",
        gjeldende.svarpostboks)
      : t("ui.kundeservice.svarvei.huset");
    fullmaktNei.hidden = !utenFullmakt;
    uKnapp.disabled = utenFullmakt;
    if (utenFullmakt) uKnapp.dataset.utenFullmakt = "1";
    else delete uKnapp.dataset.utenFullmakt;
  };
  tegnSvarvei();
  // TO KALL, ÉN INTENSJON (CodeRabbit): feiler godkjenningen etter at
  // utkastet er lagret, holder rammen nøkkelen — og neste klikk skal
  // godkjenne DET utkastet, ikke lagre et til. Husket per nøkkel til
  // godkjenningen lykkes; en endret tekst er en ny nøkkel og et nytt
  // utkast.
  let lagret = null;
  skjemaramme(ctx, last, {
    skjema: uSkjema, knapp: uKnapp, utfall, kvitter,
    okNokkel: () => (fraPostboks()
      ? t("ui.kundeservice.svar.postboks_ok").replace("{postboks}",
        gjeldende.svarpostboks)
      : t("ui.kundeservice.svar.sendt_ok")),
    send: async (idem) => {
      let svar;
      // 231: FRA POSTBOKSEN SAKEN KOM INN PÅ. Menneskets eget svar, i
      // postboksens kø — ett kall, og saken lukkes når svaret ER ute.
      if (fraPostboks()) {
        return sendFraPostboks(gjeldende.henvendelse_id,
                               utkasttekst.value, idem);
      }
      if (lagret && lagret.idem === idem) {
        svar = { utkast_id: lagret.utkast_id };
      } else {
        svar = await lagreUtkast(gjeldende.henvendelse_id,
                                 { tekst: utkasttekst.value }, idem);
        if (svar && svar.utkast_id) {
          lagret = { idem, utkast_id: svar.utkast_id };
        }
      }
      if (svar && svar.utkast_id) {
        await avgjorUtkast(svar.utkast_id, "godkjent", nyIdempotensnokkel());
      }
      lagret = null;
      return svar;
    },
    tilbakestill: () => { utkasttekst.value = ""; },
  });
  // UTEN FULLMAKT ER «GODKJENN» EN LØGN (207, CodeRabbit): planrunden
  // bestiller ingenting, og en knapp som lover sending skal ikke kunne
  // trykkes. Utkastet kan fortsatt lagres — det er menneskets, ikke
  // plattformens. Grunnen står som ord under knappen.
  // «Lagre utkast» venter bare på et kall i lufta — ikke på en godkjenn-
  // knapp som er avslått av fullmaktsgrunner (CodeRabbit).
  const opptatt = () => uLagre.disabled || (uKnapp.disabled && !utenFullmakt);
  uLagre.addEventListener("click", async () => {
    if (!utkasttekst.value.trim() || opptatt()) return;
    uLagre.disabled = true; uKnapp.disabled = true;
    const slipp = () => { uLagre.disabled = false; uKnapp.disabled = utenFullmakt; };
    try {
      await lagreUtkast(gjeldende.henvendelse_id,
                        { tekst: utkasttekst.value }, nyIdempotensnokkel());
    } catch (e) {
      slipp();
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(utfall, el("span", { role: "alert",
        text: t("ui.kundeservice.feil.generell") }));
      return;
    }
    slipp();
    utkasttekst.value = "";
    meldLive(t("ui.kundeservice.skjema.utkast_ok"));
    kvitter(t("ui.kundeservice.skjema.utkast_ok"));
    await last();
  });

  // --- unntakskø ---
  const qSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const qBegrunnelse = el("input", { id: "ks-koe", name: "begrunnelse",
    type: "text", required: true, maxlength: 2000 });
  const qKnapp = el("button", { type: "submit",
    text: t("ui.kundeservice.knapp.unntakskoe") });
  qSkjema.append(
    felt("ks-koe", "ui.kundeservice.skjema.unntakskoe_begrunnelse",
         qBegrunnelse, "ui.kundeservice.skjema.unntakskoe_hjelp"),
    el("div", { class: "skjema-bunn" }, qKnapp));
  skjemaramme(ctx, last, {
    skjema: qSkjema, knapp: qKnapp, utfall, kvitter,
    okNokkel: "ui.kundeservice.skjema.unntakskoe_ok",
    send: (idem) => henvendelseTilUnntakskoe(gjeldende.henvendelse_id,
                                             qBegrunnelse.value, idem),
    tilbakestill: () => { qBegrunnelse.value = ""; },
  });

  // --- lukking ---
  const lukkerad = el("div", { class: "skjema-bunn" });
  const lukkKnapp = (utfallverdi, nokkel) => {
    const b = el("button", { type: "button", text: t(nokkel) });
    b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await lukkHenvendelse(gjeldende.henvendelse_id, utfallverdi,
                              nyIdempotensnokkel());
      } catch (e) {
        b.disabled = false;
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        sett(utfall, el("span", { role: "alert",
          text: e && e.status === 409
            ? t("ui.kundeservice.feil.tilstand")
            : t("ui.kundeservice.feil.generell") }));
        return;
      }
      b.disabled = false;
      lukk();
      meldLive(t("ui.kundeservice.lukk_ok"));
      // SAMME DOM SOM I `skjemaramme`: kvitteringen hører til flaten,
      // ikke til panelet som lukker seg i neste linje.
      kvitter(t("ui.kundeservice.lukk_ok"));
      await last();
    });
    return b;
  };
  lukkerad.append(lukkKnapp("besvart", "ui.kundeservice.knapp.lukk_besvart"),
                  lukkKnapp("ikke_aktuell",
                            "ui.kundeservice.knapp.lukk_ikke_aktuell"));

  const skriver = harScope(ctx, "bestilling:opprett");
  const leser = harScope(ctx, "kundeservice:innhold");
  // LESEFELT TIL VENSTRE, HANDLINGER TIL HØYRE fra 64rem; under
  // hverandre på en telefon (`.hv-rutenett`).
  const melding = el("article", { class: "hv-melding" });
  if (leser) {
    melding.append(kropp, bytterad,
      el("h4", { text: t("ui.kundeservice.detalj.utkast") }), utkastliste);
  } else {
    // ÆRLIG OM HVA SOM MANGLER: ikke en tom boks, men en setning om at
    // køen er synlig og teksten ikke.
    melding.append(el("p", { class: "muted",
      text: t("ui.kundeservice.detalj.uten_innsyn") }));
  }
  const rutenett = el("div", { class: "hv-rutenett" }, melding);
  const hode = el("header", { class: "hv-hode" }, overskrift, emne, meta, ref);

  // --- 225: HVEM HAR SAKEN, og hva som er gjort med den ---
  // Én linje: hvem, og det ene man kan gjøre med det. Kollegalista hentes
  // først når noen åpner «Gi til en kollega» — køen skal ikke koste et
  // medlemsoppslag per rad.
  const hvem = el("p", { class: "hv-hvem" });
  const tildelrad = el("div", { class: "knapperad hv-tildel" });
  const sporliste = el("ol", { class: "hv-spor" });
  const spor = el("details", { class: "hv-flere hv-spor-boks" },
    el("summary", { text: t("ui.kundeservice.spor.tittel") }),
    el("p", { class: "muted", text: t("ui.kundeservice.spor.hjelp") }),
    sporliste);
  const lastSpor = async () => {
    if (!gjeldende || !spor.open) return;
    const hid = gjeldende.henvendelse_id;
    try {
      const d = await hentHenvendelsesspor(hid);
      if (!fortsatt(hid)) return;
      sett(sporliste, ...(d.spor || []).map((r) => el("li", {},
        el("span", { class: "hv-spor-naar",
          text: r.naar.slice(0, 16).replace("T", " ") }), " ",
        el("strong", { text: sporTekst(r) }), " ",
        el("span", { class: "muted", text: `— ${sporHvem(r)}` }))));
      if (d.avkortet) {
        sporliste.append(el("li", { class: "muted",
          text: t("ui.kundeservice.spor.avkortet") }));
      }
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(sporliste, el("li", { role: "alert",
        text: t("ui.kundeservice.feil.generell") }));
    }
  };
  spor.addEventListener("toggle", lastSpor);
  const tildel = async (brukerId, kvittering) => {
    const hid = gjeldende.henvendelse_id;
    for (const k of tildelrad.querySelectorAll("button, select")) {
      k.disabled = true;
    }
    try {
      await tildelHenvendelse(hid, brukerId);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(utfall, el("span", { role: "alert",
        text: t("ui.kundeservice.feil.tilstand") }));
      tegnTildeling(gjeldende);
      return;
    }
    meldLive(kvittering);
    kvitter(kvittering);
    await last();
  };
  const tegnTildeling = (h) => {
    hvem.textContent = h.tildelt
      ? t("ui.kundeservice.tildelt.har").replace("{navn}", hvemTekst(h))
      : t("ui.kundeservice.tildelt.ingen");
    sett(tildelrad);
    if (!skriver) return;
    const ta = el("button", { type: "button", class: "knapp liten primar",
      text: t("ui.kundeservice.tildelt.ta") });
    ta.addEventListener("click",
      () => tildel("meg", t("ui.kundeservice.tildelt.tatt")));
    tildelrad.append(ta);
    const velg = el("select", { class: "hv-tildel-velg",
      "aria-label": t("ui.kundeservice.tildelt.gi") });
    velg.append(el("option", { value: "",
      text: t("ui.kundeservice.tildelt.gi") }));
    let lastet = false;
    const lastKolleger = async () => {
      if (lastet) return;
      lastet = true;
      try {
        const d = await hentMedlemmer();
        for (const m of d.medlemmer || []) {
          if (!m.navn) continue;
          velg.append(el("option", { value: m.bruker_id, text: m.navn }));
        }
      } catch (e) {
        lastet = false;
        if (e instanceof UautorisertFeil) ctx.paaUautorisert();
      }
    };
    velg.addEventListener("focus", lastKolleger);
    velg.addEventListener("pointerdown", lastKolleger);
    velg.addEventListener("change", () => {
      if (velg.value) tildel(velg.value, t("ui.kundeservice.tildelt.gitt"));
    });
    tildelrad.append(velg);
    if (h.tildelt) {
      const frigi = el("button", { type: "button", class: "knapp liten",
        text: t("ui.kundeservice.tildelt.frigi") });
      frigi.addEventListener("click",
        () => tildel(null, t("ui.kundeservice.tildelt.frigitt")));
      tildelrad.append(frigi);
    }
  };
  hode.append(hvem, tildelrad, spor);
  if (skriver) {
    // DOMMEN ER ÉN LINJE UNDER HODET: prioritet · tema · handlingstype ·
    // lagre. Svaret er det man gjør oftest og står øverst til høyre;
    // det sjeldne — unntakskøen og lukkingen — ligger sammenklappet.
    hode.append(el("h4", { text: t("ui.kundeservice.skjema.klassifisering") }),
                kSkjema);
    rutenett.append(el("aside", { class: "hv-handlinger" },
      el("h4", { text: t("ui.kundeservice.skjema.utkast_tittel") }),
      uSkjema,
      el("details", { class: "hv-flere" },
        el("summary", { text: t("ui.kundeservice.detalj.flere") }),
        el("h4", { text: t("ui.kundeservice.skjema.unntakskoe") }),
        qSkjema, lukkerad)));
  }
  innhold.append(el("div", { class: "hv-topp" }, tilbake), hode, rutenett);
  boks.append(innhold, utfall);
  innhold.hidden = true;

  // ER RADEN FORTSATT DEN ÅPNE? To klikk rett etter hverandre gir to
  // svar i vilkårlig rekkefølge; det som kom sist for A skal ikke stå
  // under B. (CodeRabbit 16/9.)
  const fortsatt = (hid) => gjeldende && gjeldende.henvendelse_id === hid;

  async function tegnUtkast(hid) {
    sett(utkastliste);
    let d;
    try {
      d = await hentJson(
        `/v1/kundeservice/henvendelse/${encodeURIComponent(hid)}/utkast`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      return;
    }
    if (!fortsatt(hid)) return;
    sett(utkastliste);
    const alle = d.utkast || [];
    if (!alle.length) {
      utkastliste.append(el("p", { class: "muted",
        text: t("ui.kundeservice.detalj.ingen_utkast") }));
      return;
    }
    // Utkast man er ferdig med er SPOR, ikke arbeid (#490): de ligger
    // sammenklappet under det som venter, ikke ved siden av med samme vekt.
    const avsluttede = alle.filter((u) => !VENTER.includes(u.status));
    let mal = utkastliste;
    if (avsluttede.length) {
      mal = el("div", {});
      utkastliste.append(mal, el("details", { class: "hv-flere" },
        el("summary", { text: t("ui.kundeservice.detalj.avsluttede")
          .replace("{n}", String(avsluttede.length)) })));
    }
    const detaljer = utkastliste.querySelector("details");
    for (const u of alle) {
      // ARC B: statusen som ord, og hva plattformen gjorde med et
      // godkjent utkast (bestilt, i unntakskøen, sendt) — som tekst.
      const kort = el("div", { class: "skjemaboks" },
        el("p", { class: "celle-tekst", text: u.tekst }),
        el("p", { class: "muted",
          text: `${u.opprettet.slice(0, 10)} · ${u.kilde} · `
            + t(`ui.kundeservice.utkaststatus.${u.status}`) }));
      if (u.bestilling) {
        kort.append(el("p", { class: "muted", text: bestillingstekst(u.bestilling) }));
      }
      if (skriver && u.status === "foreslatt") {
        for (const [status, nokkel] of [
          ["forkastet", "ui.kundeservice.knapp.forkast"],
          ["brukt_manuelt", "ui.kundeservice.knapp.brukt"],
          ["godkjent", "ui.kundeservice.knapp.godkjenn"]]) {
          const b = el("button", { type: "button", text: t(nokkel) });
          b.addEventListener("click", async () => {
            b.disabled = true;
            try {
              await avgjorUtkast(u.utkast_id, status,
                                 nyIdempotensnokkel());
            } catch (e) {
              b.disabled = false;
              if (e instanceof UautorisertFeil) {
                ctx.paaUautorisert(); return;
              }
              sett(utfall, el("span", { role: "alert",
                text: t("ui.kundeservice.feil.tilstand") }));
              return;
            }
            meldLive(t("ui.kundeservice.utkast_ok"));
            kvitter(t("ui.kundeservice.utkast_ok"));
            await last();
          });
          kort.append(b);
        }
      }
      (VENTER.includes(u.status) ? mal : detaljer).append(kort);
    }
  }

  return {
    node: boks,
    async apne(h) {
      gjeldende = h;
      lagret = null;
      settApen(h.henvendelse_id);
      visKoe(false);
      sett(utfall);
      // 160: masken, aldri adressen — og adressens fravær som ord.
      sett(meta, ...metaBrikker(h));
      tegnMalvelger(h.tema);
      tegnSvarvei();
      if (skriver) {
        hentSvarmaler().then((d) => {
          malene = d.maler || [];
          if (fortsatt(h.henvendelse_id)) tegnMalvelger(h.tema);
        }).catch(() => { /* uten maler skriver hun som før */ });
      }
      tegnTildeling(h);
      spor.open = false;
      sett(sporliste);
      ref.textContent = `${t("ui.kundeservice.kolonne.referanse")}: `
        + kortref(h.ekstern_ref);
      ref.setAttribute("title", h.ekstern_ref);
      if (h.prioritet) {
        prioritet.value = h.prioritet;
        tema.value = h.tema;
        handlingstype.value = h.handlingstype;
      } else {
        // UKLASSIFISERT SKAL SE UKLASSIFISERT UT. Panelet gjenbrukes for
        // hver rad, så uten dette bærer skjemaet FORRIGE henvendelses
        // dom — og ett klikk på «Lagre klassifisering» ville skrevet den
        // over på denne. Det er ikke en visningsfeil, det er feil data.
        prioritet.value = "normal";
        tema.value = TEMAER[TEMAER.length - 1];
        handlingstype.value = HANDLINGSTYPER[0];
      }
      innhold.hidden = false;
      if (!leser) return;
      emne.textContent = "";
      raaTekst = ""; renTekst = ""; raatt = false;
      sett(kropp);
      bytterad.hidden = true;
      sett(utkastliste);
      const hid = h.henvendelse_id;
      try {
        const d = await hentJson(
          `/v1/kundeservice/henvendelse/${encodeURIComponent(hid)}/innhold`);
        if (!fortsatt(hid)) return;
        emne.textContent = d.emne;
        raaTekst = d.kropp || "";
        renTekst = lesbarTekst(raaTekst);
        visKropp();
        bytterad.hidden = renTekst === raaTekst.trim();
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        if (!fortsatt(hid)) return;
        sett(utfall, el("span", { role: "alert",
          text: t("ui.kundeservice.feil.generell") }));
        return;
      }
      await tegnUtkast(hid);
    },
  };
}

// 230: SVARMALENE — firmaets egne formuleringer. Kortet viser malene som
// finnes, og ett skjema som både lager en ny og endrer en som finnes
// («Endre» fyller skjemaet). En leser ser malene; skrivekontrollene vises
// bare for den som kan bruke dem.
// 231: SENDTE SVAR. Hvert svar som er sendt, ligger i kø eller feilet —
// begge veier, med adressen det gikk fra. Før fantes ingen sted å se det.
export function sendtStatus(r) {
  if (r.handling === "svar.til_postboks") return t("ui.kundeservice.sendte.i_ko");
  if (r.handling === "svar.feilet") return t("ui.kundeservice.sendte.feilet");
  return t("ui.kundeservice.sendte.sendt");
}

function sendteSeksjon(ctx) {
  const medEmne = harScope(ctx, "kundeservice:innhold");
  const liste = el("div", { class: "ks-sendte" },
    el("p", { class: "muted", text: t("ui.kundeservice.sendte.laster") }));
  const boks = el("section", { class: "kpi-kort ks-sendte-boks" },
    el("h2", { text: t("ui.kundeservice.sendte.tittel") }),
    el("p", { class: "muted", text: t("ui.kundeservice.sendte.forklaring") }),
    liste);
  hentSendteSvar().then((d) => {
    const rader = d.sendte || [];
    if (!rader.length) {
      sett(liste, el("p", { class: "muted",
        text: t("ui.kundeservice.sendte.ingen") }));
      return;
    }
    const tb = el("table", { class: "kpi-tabell" },
      el("caption", { text: t("ui.kundeservice.sendte.caption") }),
      el("thead", {}, el("tr", {},
        medEmne ? el("th", { scope: "col",
          text: t("ui.kundeservice.detalj.emne") }) : null,
        el("th", { scope: "col", text: t("ui.kundeservice.sendte.status") }),
        el("th", { scope: "col", text: t("ui.kundeservice.sendte.fra") }),
        el("th", { scope: "col", text: t("ui.kundeservice.sendte.til") }),
        el("th", { scope: "col", text: t("ui.kundeservice.sendte.av") }),
        el("th", { scope: "col", text: t("ui.kundeservice.sendte.naar") }))));
    const tbody = el("tbody");
    for (const r of rader) {
      tbody.append(el("tr", {},
        medEmne ? el("td", { class: "celle-tekst",
          text: r.emne || t("ui.kundeservice.detalj.uten_emne") }) : null,
        el("td", { class: "celle-tekst", text: sendtStatus(r)
          + (r.grunn ? ` (${r.grunn})` : "") }),
        el("td", { class: "celle-tekst", text: r.postboks
          || t("ui.kundeservice.sendte.fra_huset") }),
        el("td", { class: "celle-tekst", text: r.mottaker_maske || "–" }),
        el("td", { class: "celle-tekst", text: sporHvem(r) }),
        el("td", { class: "celle-tekst",
          text: r.ts.slice(0, 16).replace("T", " ") })));
    }
    tb.append(tbody);
    sett(liste, el("div", { class: "tablewrap" }, tb));
  }).catch((e) => {
    if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
    sett(liste, el("p", { role: "alert",
      text: t("ui.kundeservice.feil.generell") }));
  });
  return boks;
}

function svarmalSeksjon(ctx, kvitter) {
  const kanSkrive = harScope(ctx, "bestilling:opprett");
  const boks = el("section", { class: "kpi-kort ks-maler" },
    el("h2", { text: t("ui.kundeservice.mal.tittel") }),
    el("p", { class: "muted", text: t("ui.kundeservice.mal.forklaring") }));
  const liste = el("div", { class: "ks-malliste" });
  const utfall = el("p", { "aria-live": "polite" });
  boks.append(liste);

  const navn = el("input", { id: "ks-mal-navn", type: "text",
    maxlength: "80", required: true });
  const tema = el("select", { id: "ks-mal-tema" },
    el("option", { value: "", text: t("ui.kundeservice.mal.tema_alle") }),
    ...TEMAER.map((v) => el("option", { value: v,
      text: t(`ui.kundeservice.tema.${v}`) })));
  const tekst = el("textarea", { id: "ks-mal-tekst", rows: "6",
    maxlength: "4000", required: true });
  const lagre = el("button", { type: "submit", class: "knapp primar",
    text: t("ui.kundeservice.mal.lagre") });
  const avbryt = el("button", { type: "button", class: "knapp",
    text: t("ui.kundeservice.mal.avbryt") });
  avbryt.hidden = true;
  let endrer = null;
  let idem = null;
  const tom = () => {
    endrer = null; idem = null;
    navn.value = ""; tema.value = ""; tekst.value = "";
    avbryt.hidden = true;
    lagre.textContent = t("ui.kundeservice.mal.lagre");
  };
  avbryt.addEventListener("click", tom);
  const skjema = el("form", { class: "kv-skjema ks-malskjema" },
    el("h3", { text: t("ui.kundeservice.mal.ny") }),
    felt("ks-mal-navn", "ui.kundeservice.mal.navn", navn),
    felt("ks-mal-tema", "ui.kundeservice.mal.tema", tema,
         "ui.kundeservice.mal.tema_hjelp"),
    felt("ks-mal-tekst", "ui.kundeservice.mal.tekst", tekst),
    el("div", { class: "skjema-bunn" }, lagre, avbryt), utfall);
  skjema.addEventListener("input", () => { idem = null; });

  const tegn = (maler) => {
    if (!maler.length) {
      sett(liste, el("p", { class: "muted",
        text: t("ui.kundeservice.mal.ingen") }));
      return;
    }
    sett(liste, el("ul", { class: "ks-maler-ul" }, ...maler.map((m) => {
      const rad = el("li", { class: "ks-mal" },
        el("div", {},
          el("strong", { text: m.navn }), " ",
          m.tema ? el("span", { class: "brikke brikke-tekst",
            text: t(`ui.kundeservice.tema.${m.tema}`) }) : null,
          el("p", { class: "muted ks-mal-utdrag", text:
            m.tekst.length > 140 ? `${m.tekst.slice(0, 140)}…` : m.tekst })));
      if (kanSkrive) {
        const endre = el("button", { type: "button", class: "knapp liten",
          text: t("ui.kundeservice.mal.endre"),
          "aria-label": `${t("ui.kundeservice.mal.endre")}: ${m.navn}` });
        endre.addEventListener("click", () => {
          endrer = m.mal_id; idem = null;
          navn.value = m.navn; tema.value = m.tema || "";
          tekst.value = m.tekst;
          avbryt.hidden = false;
          lagre.textContent = t("ui.kundeservice.mal.lagre_endring");
          navn.focus();
        });
        const slett = el("button", { type: "button", class: "knapp liten",
          text: t("ui.kundeservice.mal.slett"),
          "aria-label": `${t("ui.kundeservice.mal.slett")}: ${m.navn}` });
        slett.addEventListener("click", () => {
          slett.disabled = true;
          slettSvarmal(m.mal_id).then(() => {
            kvitter(t("ui.kundeservice.mal.slettet"));
            meldLive(t("ui.kundeservice.mal.slettet"));
            if (endrer === m.mal_id) tom();
            return hent();
          }).catch((e) => {
            slett.disabled = false;
            if (e instanceof UautorisertFeil) ctx.paaUautorisert();
          });
        });
        rad.append(el("div", { class: "knapperad" }, endre, slett));
      }
      return rad;
    })));
  };
  const hent = () => hentSvarmaler().then((d) => tegn(d.maler || []))
    .catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(liste, el("p", { role: "alert",
        text: t("ui.kundeservice.feil.generell") }));
    });

  if (kanSkrive) {
    boks.append(skjema);
    skjema.addEventListener("submit", (ev) => {
      ev.preventDefault();
      if (lagre.disabled) return;
      const kropp = { navn: navn.value.trim(), tekst: tekst.value.trim() };
      if (!kropp.navn || !kropp.tekst) {
        sett(utfall, el("span", { role: "alert",
          text: t("ui.kundeservice.mal.feil.tom") }));
        return;
      }
      if (tema.value) kropp.tema = tema.value;
      if (endrer) kropp.mal_id = endrer;
      if (!idem) idem = nyIdempotensnokkel();
      lagre.disabled = true;
      sett(utfall);
      lagreSvarmal(kropp, idem).then(() => {
        lagre.disabled = false;
        tom();
        kvitter(t("ui.kundeservice.mal.lagret"));
        meldLive(t("ui.kundeservice.mal.lagret"));
        return hent();
      }).catch((e) => {
        lagre.disabled = false;
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        if (e && e.status >= 400 && e.status < 500) idem = null;
        sett(utfall, el("span", { role: "alert", text: t(
          e && e.status === 409 ? "ui.kundeservice.mal.feil.navn"
            : "ui.kundeservice.feil.generell") }));
      });
    });
  }
  hent();
  return boks;
}

// Sammendraget over tabellen. TALLENE KOMMER FRA SIN EGEN DØR og gjelder
// ALT — ikke bare det listen viser.
function sammendrag(s) {
  const p = el("p", {
    text: t("ui.kundeservice.sammendrag")
      .replace("{apne}", String(s.apne))
      .replace("{uklassifiserte}", String(s.uklassifiserte))
      .replace("{kritiske}", String(s.kritiske))
      .replace("{koe}", String(s.i_unntakskoe)) });
  // AVKORTINGEN SIES HØYT. Uten dette ville flaten sett komplett ut
  // nettopp når den var det minst.
  if (s.vist < s.apne) {
    p.append(" ", el("strong", {
      text: t("ui.kundeservice.avkortet")
        .replace("{vist}", String(s.vist)) }));
  }
  return p;
}

// 226: minutter som tid et menneske leser. Under en time i minutter, under
// et døgn i timer og minutter, ellers døgn og timer.
export function svartidTekst(min) {
  if (min == null) return null;
  const m = Math.max(0, Math.round(min));
  if (m < 60) {
    return t("ui.kundeservice.maal.min").replace("{m}", String(m));
  }
  if (m < 1440) {
    return t("ui.kundeservice.maal.timer")
      .replace("{t}", String(Math.floor(m / 60)))
      .replace("{m}", String(m % 60));
  }
  return t("ui.kundeservice.maal.dogn")
    .replace("{d}", String(Math.floor(m / 1440)))
    .replace("{t}", String(Math.floor((m % 1440) / 60)));
}

// MÅLTALLENE: det kunden kan vise fram — hvor mye som kom inn, hvor mye
// som ble besvart, og hvor lang tid det typisk tok. «Ingen besvart ennå»
// er et ord, ikke «0 min»: null minutter ville vært en påstand.
function maaltall(mt) {
  if (!mt) return null;
  const ut = el("p", { class: "ks-maaltall" });
  ut.append(t("ui.kundeservice.maal.volum")
    .replace("{dogn}", String(mt.dogn))
    .replace("{mottatt}", String(mt.mottatt))
    .replace("{besvart}", String(mt.besvart)), " ");
  const tid = svartidTekst(mt.median_svartid_min);
  ut.append(tid
    ? el("strong", { text: t("ui.kundeservice.maal.svartid")
        .replace("{tid}", tid) })
    : t("ui.kundeservice.maal.ingen_besvart"));
  if (mt.eldste_apne_dogn != null && mt.eldste_apne_dogn > 0) {
    ut.append(" ", t("ui.kundeservice.maal.eldste")
      .replace("{dogn}", String(mt.eldste_apne_dogn)));
  }
  return ut;
}

export function visKundeservice(hoved, ctx) {
  const hode = () => flateHode(t("ui.kundeservice.tittel"),
    t("ui.kundeservice.undertittel"));
  sett(hoved, ...hode());
  // KVITTERINGEN LEVER UTENFOR TEGNINGEN. Alt inne i `kropp` bygges på
  // nytt ved hver `last()`, og før dette forsvant kvitteringen i samme
  // øyeblikk den ble satt — brukeren trykket, så skjermen blinke, og
  // satt igjen uten å vite om det gikk bra.
  //
  // INGEN `aria-live` her: `meldLive` eier opplesningen, og to regioner
  // ville lest den samme setningen to ganger.
  const kvittering = el("p", { class: "muted" });
  // ÉN KOLONNE: køen er flatens arbeidsbord og trenger hele bredden —
  // i tre kolonner ble tabellen klemt og detaljpanelet kuttet (målt på
  // skjermbilde 20.11.2026).
  const kropp = el("div", { class: "kpi-kort-liste en-kolonne" });
  hoved.append(kvittering, kropp);
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  // …OG DEN ÅPNE RADEN OGSÅ. Uten dette lukket detaljpanelet seg ved
  // hver skriving, og neste handling krevde at brukeren fant fram til
  // raden igjen.
  let apenRad = null;
  const settApen = (id) => { apenRad = id; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/kundeservice"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const koe = d.koe || [];
      // DETALJEN BOR I KØSEKSJONEN og bytter plass med køen: åpnes en
      // rad, står henvendelsen der køen sto, med veien tilbake øverst.
      // Før lå panelet utenfor fanene og hang under hver eneste fane.
      let koblokk = null;
      const visKoe = (synlig) => { if (koblokk) koblokk.hidden = !synlig; };
      const detalj = detaljpanel(ctx, last, kvitter, settApen, visKoe, s);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.kundeservice.oversikt.tittel") }),
        sammendrag(s), maaltall(d.maaltall));

      koblokk = koBlokk(koe, ctx, detalj.apne);
      const koseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.kundeservice.koe.tittel") }),
        koblokk, detalj.node);
      const deler = [oversikt, koseksjon,
                     sendteSeksjon(ctx),
                     svarmalSeksjon(ctx, kvitter),
                     avsenderSeksjon(d.avsenderprofil, s, ctx, last, kvitter),
                     stilleSeksjon(ctx, last, kvitter)];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(avsenderSkjema(ctx, last, kvitter, d.avsenderprofil));
      }
      sett(kropp, ...deler);
      // GJENÅPNE PANELET på raden som sto åpen. Finnes den ikke lenger
      // i listen — avsluttet, eller falt utenfor avkortingen — slippes
      // den, framfor å åpne et panel på en rad ingen ser.
      if (apenRad) {
        const rad = koe.find((x) => x.henvendelse_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
