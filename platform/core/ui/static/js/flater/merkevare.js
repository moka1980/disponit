// Merkevare- og IP-overvåkeren (M-55 v1) — BEVISET, IKKE KRAVET.
//
// FLATENS VIKTIGSTE JOBB er å vise et funn som DOKUMENTASJON, ikke som
// en beskyldning.
//
// Et merkevarefunn er en påstand om at NOEN ANDRE bruker noe som
// ligner vårt. Påstanden er verdiløs uten hvor det sto, når det sto
// der, og hva som faktisk sto der — og den tredje er en KOPI. En side
// som er endret eller borte den dagen saken tas opp, er ingen sak.
//
// DERFOR STÅR BEVISET PÅ FUNNET, ikke bak et ekstra klikk: URL,
// tidspunkt og innholdssum står på hver rad. Et funn uten sitt bevis
// synlig er nettopp det modulen finnes for å unngå.
//
// OG VURDERINGEN VISES ALDRI SOM ET TALL ALENE. «87 %» uten
// grunnlaget og uten de to tekstene som ble sammenlignet er en mening
// i tallform. Hvert sted en likhet står, står terskelen den ble målt
// mot og hva likheten hviler på ved siden av.
//
// DET FINNES INGEN «SEND KRAV»-KNAPP, INGEN «SEND KLAGE», INGEN
// MOTTAKER. 120 har ingen kolonne å skrive et krav til, så knappen kan
// ikke finnes. Et krav sendt på et automatisk funn er en ANKLAGE MOT
// EN NAVNGITT PART, og en feilaktig anklage er ikke reversibel ved å
// trekke den.
//
// MODULENS ENESTE UTGANG ER «HENVIS TIL UNNTAKSKØEN» — og der
// beslutter et menneske.
//
// TERSKELEN STÅR IKKE HER. Det finnes ingen tallkonstant i denne fila
// for hvor likt noe må være: den kommer fra `merkevarekrav` gjennom
// API-et, og mangler den, sier flaten det og tilbyr ikke å vurdere.
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, henvisMerkevarefunn, hentJson, lukkMerkevarefunn,
  lukkMerkevarevarsel, nyIdempotensnokkel, registrerBevaringskopi,
  registrerMerkevare, registrerMerkevarefunn, settMerkevareAktiv,
  settMerkevarekrav, vurderMerkevarefunn,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// LUKKEDE SETT, speilet fra 120.
export const ARTER = ["varemerke", "domenenavn", "firmanavn",
                      "produktnavn", "logo", "slagord"];
export const BRUKSFORMER = ["domenenavn", "annonsetekst",
                            "produktnavn", "firmanavn",
                            "sosial_konto", "markedsplassoppforing",
                            "annet"];

const ARTTEKST = {
  varemerke: "ui.merkevare.art_varemerke",
  domenenavn: "ui.merkevare.art_domenenavn",
  firmanavn: "ui.merkevare.art_firmanavn",
  produktnavn: "ui.merkevare.art_produktnavn",
  logo: "ui.merkevare.art_logo",
  slagord: "ui.merkevare.art_slagord",
};

const BRUKSTEKST = {
  domenenavn: "ui.merkevare.bruk_domenenavn",
  annonsetekst: "ui.merkevare.bruk_annonsetekst",
  produktnavn: "ui.merkevare.bruk_produktnavn",
  firmanavn: "ui.merkevare.bruk_firmanavn",
  sosial_konto: "ui.merkevare.bruk_sosial_konto",
  markedsplassoppforing: "ui.merkevare.bruk_markedsplass",
  annet: "ui.merkevare.bruk_annet",
};

const GRUNNLAGSTEKST = {
  identisk_normalisert: "ui.merkevare.grunnlag_identisk",
  redigeringsavstand: "ui.merkevare.grunnlag_avstand",
  delstreng: "ui.merkevare.grunnlag_delstreng",
  ordoverlapp: "ui.merkevare.grunnlag_ordoverlapp",
  samme_bruksform_som_merket: "ui.merkevare.grunnlag_bruksform",
};

const VARSELTEKST = {
  funn_uten_vurdering: "ui.merkevare.varsel_uten_vurdering",
  forveksling_ikke_henvist: "ui.merkevare.varsel_ikke_henvist",
  vurdering_med_utdatert_terskel: "ui.merkevare.varsel_utdatert",
  funn_eldre_enn_frist: "ui.merkevare.varsel_gammelt",
  merkevare_uten_funn: "ui.merkevare.varsel_uten_funn",
  ingen_terskler: "ui.merkevare.varsel_uten_terskler",
};


// STØRRELSEN I BYTE, LESBART — men aldri avrundet bort: et bevis på
// 0 byte og et på 900 er ikke det samme, og «0 kB» ville sagt at
// begge er ingenting.
export function bytesTekst(n) {
  if (n === null || n === undefined) return "–";
  const b = Number(n);
  if (b < 1024) return t("ui.merkevare.bytes").replace("{n}", String(b));
  if (b < 1024 * 1024) {
    return t("ui.merkevare.kilobytes")
      .replace("{n}", String(Math.round(b / 1024)));
  }
  return t("ui.merkevare.megabytes")
    .replace("{n}", String(Math.round(b / (1024 * 1024))));
}


// INNHOLDSSUMMEN, FORKORTET FOR ØYET — men alltid med hele summen i
// `title`, fordi det er den som binder raden til bytene.
export function summenTekst(sha) {
  if (!sha) return "–";
  return `${sha.slice(0, 12)}…`;
}


// LIKHETEN, ALDRI ALENE.
//
// MUTASJONEN SOM DREPER PORTEN: returner bare prosenten. Et tall uten
// terskelen det ble målt mot er ikke en vurdering — det er en mening
// i tallform, og terskelen er tenantens egen.
export function likhetTekst(rad) {
  if (!rad || rad.likhet === null || rad.likhet === undefined) {
    return t("ui.merkevare.uvurdert");
  }
  // DOMMEN UTLEDES NÅR FLAGGET IKKE ER MED (CodeRabbit).
  //
  // `m55_varslene` returnerer likhet og terskel, men ikke
  // `over_terskel` — den er en generert kolonne på vurderingen, ikke
  // på varselet. Uten dette leste varseltabellen `undefined` som
  // usant og skrev «87 % — UNDER terskelen på 80 %» om nøyaktig den
  // forvekslingen modulen finnes for å vise.
  //
  // Regelen er basens egen: `likhet >= terskel_brukt`.
  const over = rad.over_terskel === null
      || rad.over_terskel === undefined
    ? rad.likhet >= rad.terskel_brukt
    : rad.over_terskel;
  return t(over ? "ui.merkevare.likhet_over"
                : "ui.merkevare.likhet_under")
    .replace("{likhet}", String(rad.likhet))
    .replace("{terskel}", String(rad.terskel_brukt));
}


// GRUNNLAGET, I ORD. Listen er faktapåstander om paret, ikke vekter:
// hver av dem kan sjekkes av hvem som helst med de to tekstene i hånd.
export function grunnlagTekst(grunnlag) {
  if (!grunnlag || !grunnlag.length) return "–";
  return grunnlag.map((g) => t(GRUNNLAGSTEKST[g] || g)).join(", ");
}


// FUNNETS TILSTAND, MED NAVN PÅ DET SOM MANGLER.
export function tilstandTekst(f) {
  if (f.lukket_ts) return t("ui.merkevare.lukket");
  if (f.likhet === null || f.likhet === undefined) {
    return t("ui.merkevare.uvurdert");
  }
  if (f.over_terskel && !f.henvist_unntak_id) {
    return t("ui.merkevare.venter_paa_henvisning");
  }
  if (f.henvist_unntak_id) return t("ui.merkevare.henvist");
  return t("ui.merkevare.under_terskel");
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
          ? t("ui.merkevare.feil.tilstand")
          : t("ui.merkevare.feil.generell") }));
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


// SAMMENDRAGET. `uhenviste` står FØRST og i fet skrift: det er det ene
// tallet som sier hvor mange saker som venter på et menneske.
export function sammendrag(s) {
  const p = el("p");
  p.append(el("strong", {
    text: t("ui.merkevare.uhenviste_sum")
      .replace("{n}", String(s.uhenviste ?? 0)) }));
  p.append(" ", el("span", {
    text: t("ui.merkevare.tellinger")
      .replace("{merker}", String(s.merker ?? 0))
      .replace("{aktive}", String(s.aktive ?? 0))
      .replace("{funn}", String(s.apne_funn ?? 0)) }));
  if (s.uvurderte > 0) {
    p.append(" ", el("strong", {
      text: t("ui.merkevare.uvurderte_sum")
        .replace("{n}", String(s.uvurderte)) }));
  }
  if (s.ubrukte_kopier > 0) {
    // BEVIS VI BETALER FOR Å LAGRE UTEN Å BRUKE.
    p.append(" ", el("span", { class: "muted",
      text: t("ui.merkevare.ubrukte_kopier")
        .replace("{n}", String(s.ubrukte_kopier)) }));
  }
  if (s.apne_varsler > 0) {
    p.append(" ", el("strong", {
      text: t("ui.merkevare.apne_varsler")
        .replace("{n}", String(s.apne_varsler)) }));
  }
  if (!s.har_krav) {
    // UTEN TERSKEL BLIR INGENTING VURDERT, og det er ikke en detalj.
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.merkevare.ingen_terskel_varsel") }));
  } else {
    p.append(" ", el("span", { class: "muted",
      text: t("ui.merkevare.terskelen_er")
        .replace("{n}", String(s.terskel)) }));
  }
  if (s.vist < s.merker) {
    p.append(" ", el("strong", {
      text: t("ui.merkevare.avkortet")
        .replace("{vist}", String(s.vist)) }));
  }
  return p;
}


export function merketabell(merker, apne) {
  const tbody = el("tbody");
  for (const m of merker) {
    const knapp = el("button", { type: "button",
      text: t("ui.merkevare.knapp.apne") });
    knapp.addEventListener("click", () => apne(m));
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: m.navn }),
      el("td", { text: t(ARTTEKST[m.art] || m.art) }),
      // REGISTRERT ELLER IKKE ER IKKE EN DETALJ: et registrert
      // varemerke og et innarbeidet kjennetegn har ikke samme vern.
      el("td", { text: m.registernummer
                   ? `${m.registerfoerer} ${m.registernummer}`
                   : t("ui.merkevare.uregistrert") }),
      el("td", { text: (m.vareklasser || []).join(", ") || "–" }),
      el("td", { class: "tall", text: String(m.apne_funn ?? 0) }),
      el("td", { class: "tall", text: String(m.uvurderte ?? 0) }),
      el("td", { class: "tall", text: String(m.uhenviste ?? 0) }),
      el("td", { text: m.hoyeste_likhet === null
                       || m.hoyeste_likhet === undefined
                   ? "–"
                   : t("ui.merkevare.prosent")
                       .replace("{n}", String(m.hoyeste_likhet)) }),
      el("td", { class: "tall", text: String(m.apne_varsler ?? 0) }),
      el("td", { text: m.aktiv ? t("ui.merkevare.ja")
                               : t("ui.merkevare.nei") }),
      el("td", {}, knapp)));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.merkevare.liste.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.merkevare.kol.merke") }),
        el("th", { scope: "col", text: t("ui.merkevare.kol.art") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.registrering") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.vareklasser") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.apne_funn") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.uvurderte") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.uhenviste") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.hoyeste") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.varsler") }),
        el("th", { scope: "col", text: t("ui.merkevare.kol.aktiv") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.handling") }))),
      tbody));
}


// FUNNTABELLEN. BEVISET STÅR PÅ RADEN — URL, tidspunkt og
// innholdssum — og vurderingen står ALDRI som et tall alene.
export function funntabell(funn) {
  const tbody = el("tbody");
  for (const f of funn) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: f.observert_navn }),
      el("td", { text: t(BRUKSTEKST[f.bruksform] || f.bruksform) }),
      el("td", { text: f.motpart || t("ui.merkevare.ukjent_motpart") }),
      // BEVISET. URL-en står som TEKST, ikke som lenke: en klikkbar
      // lenke til den påståtte krenkerens side ville vært en utgående
      // forespørsel flaten inviterer til, og modulen gjør ingen.
      el("td", { class: "brytord", text: f.kilde_url }),
      el("td", { text: f.hentet_ts.slice(0, 16).replace("T", " ") }),
      el("td", { title: f.innhold_sha256,
                 text: summenTekst(f.innhold_sha256) }),
      el("td", { text: bytesTekst(f.innhold_bytes) }),
      // VURDERINGEN, MED TERSKELEN.
      el("td", { text: likhetTekst(f) }),
      el("td", { text: grunnlagTekst(f.grunnlag) }),
      el("td", { text: tilstandTekst(f) })));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.merkevare.funn.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.observert") }),
        el("th", { scope: "col", text: t("ui.merkevare.kol.bruk") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.motpart") }),
        el("th", { scope: "col", text: t("ui.merkevare.kol.url") }),
        el("th", { scope: "col", text: t("ui.merkevare.kol.hentet") }),
        el("th", { scope: "col", text: t("ui.merkevare.kol.sum") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.storrelse") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.likhet") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.grunnlag") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.tilstand") }))),
      tbody));
}


// VURDERINGSREKKEN. HELE rekken, ikke bare den nyeste: en ny algoritme
// eller en ny terskel gir en ny rad, og det er der «hva mente vi da»
// står. De to tekstene som ble sammenlignet står PÅ raden, så tallet
// kan regnes etter.
export function vurderingstabell(vurderinger) {
  const tbody = el("tbody");
  for (const v of vurderinger) {
    tbody.append(el("tr", {},
      el("th", { scope: "row",
                 text: v.vurdert.slice(0, 16).replace("T", " ") }),
      el("td", { text: likhetTekst(v) }),
      el("td", { text: grunnlagTekst(v.grunnlag) }),
      el("td", { text: v.merkenavn_ved_vurdering }),
      el("td", { text: v.observert_ved_vurdering }),
      el("td", { text: v.algoritmeversjon }),
      el("td", { class: "tall", text: String(v.kravversjon) }),
      el("td", { text: v.vurdert_av })));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.merkevare.vurderinger.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.vurdert") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.likhet") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.grunnlag") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.merkenavn_da") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.observert_da") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.algoritme") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.kravversjon") }),
        el("th", { scope: "col", text: t("ui.merkevare.kol.av") }))),
      tbody));
}


export function kopitabell(kopier) {
  const tbody = el("tbody");
  for (const k of kopier) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", class: "brytord", text: k.kilde_url }),
      el("td", { text: k.hentet_ts.slice(0, 16).replace("T", " ") }),
      el("td", { title: k.innhold_sha256,
                 text: summenTekst(k.innhold_sha256) }),
      el("td", { text: bytesTekst(k.innhold_bytes) }),
      el("td", { text: k.medietype }),
      el("td", { class: "brytord", text: k.lagringsnokkel }),
      el("td", { class: "tall", text: String(k.brukt_i_funn) })));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.merkevare.kopier.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.merkevare.kol.url") }),
        el("th", { scope: "col", text: t("ui.merkevare.kol.hentet") }),
        el("th", { scope: "col", text: t("ui.merkevare.kol.sum") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.storrelse") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.medietype") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.lagring") }),
        el("th", { scope: "col",
                   text: t("ui.merkevare.kol.brukt") }))),
      tbody));
}


function merkeskjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const navn = el("input", { id: "mv-m-navn", name: "navn",
    type: "text", required: true, maxlength: "500" });
  const art = el("select", { id: "mv-m-art", name: "art",
                             required: true });
  art.append(el("option", { value: "",
    text: t("ui.merkevare.merke.velg_art") }));
  for (const a of ARTER) {
    art.append(el("option", { value: a,
      text: t(ARTTEKST[a] || a) }));
  }
  const nummer = el("input", { id: "mv-m-nummer", name: "nummer",
    type: "text", maxlength: "200" });
  const foerer = el("input", { id: "mv-m-foerer", name: "foerer",
    type: "text", maxlength: "500" });
  const klasser = el("input", { id: "mv-m-klasser", name: "klasser",
    type: "text", maxlength: "500" });
  const fra = el("input", { id: "mv-m-fra", name: "fra",
    type: "date", required: true });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.merkevare.knapp.lagre_merke") });
  const vurder = () => {
    // REGISTRERINGEN HENGER SAMMEN: nummer uten fører, eller fører
    // uten nummer, er en halv opplysning — og 120 nekter den.
    const halv = Boolean(nummer.value.trim())
      !== Boolean(foerer.value.trim());
    knapp.disabled = !navn.value.trim() || !art.value || !fra.value
      || halv;
  };
  for (const k of [navn, art, nummer, foerer, fra]) {
    k.addEventListener("change", vurder);
    k.addEventListener("input", vurder);
  }
  skjema.append(
    felt("mv-m-navn", "ui.merkevare.merke.navn", navn, null),
    felt("mv-m-art", "ui.merkevare.merke.art", art, null),
    felt("mv-m-nummer", "ui.merkevare.merke.nummer", nummer,
         "ui.merkevare.merke.nummer_hjelp"),
    felt("mv-m-foerer", "ui.merkevare.merke.foerer", foerer, null),
    felt("mv-m-klasser", "ui.merkevare.merke.klasser", klasser,
         "ui.merkevare.merke.klasser_hjelp"),
    felt("mv-m-fra", "ui.merkevare.merke.fra", fra, null),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.merkevare.skjema.merke_ok",
    tilbakestill: () => {
      navn.value = ""; art.value = ""; nummer.value = "";
      foerer.value = ""; klasser.value = ""; fra.value = "";
      knapp.disabled = true;
    },
    send: (idem) => registrerMerkevare({
      navn: navn.value.trim(), art: art.value,
      registernummer: nummer.value.trim() || null,
      registerfoerer: foerer.value.trim() || null,
      vareklasser: klasser.value.split(",").map((x) => x.trim())
        .filter(Boolean),
      gjelder_fra: fra.value,
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.merkevare.merke.tittel") }), skjema,
    utfall);
}


// BEVARINGSKOPISKJEMAET. MODULEN HENTER IKKE — kopien registreres av
// den som TOK den, med innholdssum og størrelse. Hjelpeteksten sier
// hvorfor: uten dem kan raden peke på hva som helst.
function kopiskjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const url = el("input", { id: "mv-k-url", name: "url",
    type: "url", required: true, maxlength: "2000" });
  const hentet = el("input", { id: "mv-k-hentet", name: "hentet",
    type: "datetime-local", required: true });
  const sum = el("input", { id: "mv-k-sum", name: "sum",
    type: "text", required: true, minlength: "64", maxlength: "64",
    pattern: "[0-9a-fA-F]{64}" });
  const storrelse = el("input", { id: "mv-k-bytes", name: "bytes",
    type: "number", required: true, min: "1", step: "1" });
  const medietype = el("input", { id: "mv-k-medietype",
    name: "medietype", type: "text", required: true,
    maxlength: "200" });
  const lagring = el("input", { id: "mv-k-lagring", name: "lagring",
    type: "text", required: true, maxlength: "200" });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.merkevare.knapp.lagre_kopi") });
  const gyldigUrl = (v) => v.startsWith("http://")
    || v.startsWith("https://");
  const vurder = () => {
    knapp.disabled = !gyldigUrl(url.value.trim())
      || !hentet.value
      || !/^[0-9a-fA-F]{64}$/.test(sum.value.trim())
      || !(Number(storrelse.value) >= 1)
      || !medietype.value.includes("/")
      || !lagring.value.trim();
  };
  for (const k of [url, hentet, sum, storrelse, medietype, lagring]) {
    k.addEventListener("change", vurder);
    k.addEventListener("input", vurder);
  }
  skjema.append(
    felt("mv-k-url", "ui.merkevare.kopi.url", url,
         "ui.merkevare.kopi.url_hjelp"),
    felt("mv-k-hentet", "ui.merkevare.kopi.hentet", hentet,
         "ui.merkevare.kopi.hentet_hjelp"),
    felt("mv-k-sum", "ui.merkevare.kopi.sum", sum,
         "ui.merkevare.kopi.sum_hjelp"),
    felt("mv-k-bytes", "ui.merkevare.kopi.bytes", storrelse, null),
    felt("mv-k-medietype", "ui.merkevare.kopi.medietype", medietype,
         null),
    felt("mv-k-lagring", "ui.merkevare.kopi.lagring", lagring,
         "ui.merkevare.kopi.lagring_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.merkevare.skjema.kopi_ok",
    tilbakestill: () => {
      url.value = ""; hentet.value = ""; sum.value = "";
      storrelse.value = ""; medietype.value = ""; lagring.value = "";
      knapp.disabled = true;
    },
    send: (idem) => registrerBevaringskopi({
      kilde_url: url.value.trim(),
      // `datetime-local` gir lokal tid uten sone; `Z` gjør den
      // entydig. Et bevis med tvetydig tidspunkt er et bevis med et
      // hull i seg.
      hentet_ts: new Date(hentet.value).toISOString(),
      innhold_sha256: sum.value.trim().toLowerCase(),
      innhold_bytes: Math.trunc(Number(storrelse.value)),
      medietype: medietype.value.trim().toLowerCase(),
      lagringsnokkel: lagring.value.trim(),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.merkevare.kopi.tittel") }),
    el("p", { class: "muted", text: t("ui.merkevare.kopi.hvorfor") }),
    skjema, utfall);
}


// FUNNSKJEMAET. KOPIEN VELGES FRA EN LISTE, og det finnes ingen
// «registrer uten kopi»-vei — ikke fordi flaten sjekker det, men fordi
// `merkevarefunn.kopi_id` er NOT NULL med fremmednøkkel i 120.
function funnskjema(ctx, last, merkevareId, kopier, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const kopi = el("select", { id: "mv-f-kopi", name: "kopi",
                              required: true });
  kopi.append(el("option", { value: "",
    text: t("ui.merkevare.funn.velg_kopi") }));
  for (const k of kopier) {
    kopi.append(el("option", { value: k.kopi_id,
      text: `${k.kilde_url} — ${k.hentet_ts.slice(0, 10)}`
            + ` (${bytesTekst(k.innhold_bytes)})` }));
  }
  const observert = el("input", { id: "mv-f-observert",
    name: "observert", type: "text", required: true,
    maxlength: "500" });
  const bruksform = el("select", { id: "mv-f-bruksform",
    name: "bruksform", required: true });
  bruksform.append(el("option", { value: "",
    text: t("ui.merkevare.funn.velg_bruksform") }));
  for (const b of BRUKSFORMER) {
    bruksform.append(el("option", { value: b,
      text: t(BRUKSTEKST[b] || b) }));
  }
  const kontekst = el("input", { id: "mv-f-kontekst",
    name: "kontekst", type: "text", required: true, minlength: "4",
    maxlength: "4000" });
  const motpart = el("input", { id: "mv-f-motpart", name: "motpart",
    type: "text", maxlength: "500" });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.merkevare.knapp.lagre_funn") });
  const vurder = () => {
    knapp.disabled = !kopi.value || !observert.value.trim()
      || !bruksform.value || kontekst.value.trim().length < 4;
  };
  for (const k of [kopi, observert, bruksform, kontekst]) {
    k.addEventListener("change", vurder);
    k.addEventListener("input", vurder);
  }
  const deler = [];
  if (!kopier.length) {
    // INGEN KOPI, INGEN KNAPP. Et skjema som lot en registrere et funn
    // og så feilet på døra, ville lært brukeren at systemet er
    // upålitelig — når det egentlig gjorde nøyaktig det det skal.
    deler.push(el("p", { role: "alert",
      text: t("ui.merkevare.funn.ingen_kopier") }));
  } else {
    deler.push(
      felt("mv-f-kopi", "ui.merkevare.funn.kopi", kopi,
           "ui.merkevare.funn.kopi_hjelp"),
      felt("mv-f-observert", "ui.merkevare.funn.observert", observert,
           "ui.merkevare.funn.observert_hjelp"),
      felt("mv-f-bruksform", "ui.merkevare.funn.bruksform",
           bruksform, "ui.merkevare.funn.bruksform_hjelp"),
      felt("mv-f-kontekst", "ui.merkevare.funn.kontekst", kontekst,
           "ui.merkevare.funn.kontekst_hjelp"),
      felt("mv-f-motpart", "ui.merkevare.funn.motpart", motpart,
           "ui.merkevare.funn.motpart_hjelp"),
      el("div", { class: "skjema-bunn" }, knapp));
  }
  skjema.append(...deler);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.merkevare.skjema.funn_ok",
    tilbakestill: () => {
      kopi.value = ""; observert.value = ""; bruksform.value = "";
      kontekst.value = ""; motpart.value = "";
      knapp.disabled = true;
    },
    send: (idem) => registrerMerkevarefunn({
      merkevare_id: merkevareId,
      kopi_id: kopi.value,
      observert_navn: observert.value.trim(),
      bruksform: bruksform.value,
      kontekst: kontekst.value.trim(),
      motpart: motpart.value.trim() || null,
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.merkevare.funn.skjema_tittel") }),
    el("p", { class: "muted", text: t("ui.merkevare.funn.hvorfor") }),
    skjema, utfall);
}


function kravskjema(ctx, last, krav, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const terskel = el("input", { id: "mv-t-terskel", name: "terskel",
    type: "number", required: true, min: "1", max: "100", step: "1" });
  const funnfrist = el("input", { id: "mv-t-funnfrist",
    name: "funnfrist", type: "number", required: true, min: "1",
    max: "365", step: "1" });
  const henvfrist = el("input", { id: "mv-t-henvfrist",
    name: "henvfrist", type: "number", required: true, min: "1",
    max: "365", step: "1" });
  // VERDIENE KOMMER FRA BASEN, ikke fra en konstant her. Er de ikke
  // satt, står feltene TOMME — et forhåndsutfylt tall ville vært
  // nøyaktig den hardkodede terskelen invarianten forbyr.
  terskel.value = krav ? String(krav.forvekslingsterskel) : "";
  funnfrist.value = krav ? String(krav.funnfrist_dogn) : "";
  henvfrist.value = krav ? String(krav.henvisningsfrist_dogn) : "";
  const knapp = el("button", { type: "submit",
    text: t("ui.merkevare.knapp.lagre_krav") });
  skjema.append(
    felt("mv-t-terskel", "ui.merkevare.krav.terskel", terskel,
         "ui.merkevare.krav.terskel_hjelp"),
    felt("mv-t-funnfrist", "ui.merkevare.krav.funnfrist", funnfrist,
         "ui.merkevare.krav.funnfrist_hjelp"),
    felt("mv-t-henvfrist", "ui.merkevare.krav.henvfrist", henvfrist,
         "ui.merkevare.krav.henvfrist_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.merkevare.skjema.krav_ok",
    send: (idem) => settMerkevarekrav({
      forvekslingsterskel: Math.trunc(Number(terskel.value)),
      funnfrist_dogn: Math.trunc(Number(funnfrist.value)),
      henvisningsfrist_dogn: Math.trunc(Number(henvfrist.value)),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.merkevare.krav.tittel") }),
    el("p", { class: "muted", text: t("ui.merkevare.krav.hvorfor") }),
    skjema, utfall);
}


// DETALJPANELET. Funnene med sine bevis, vurderingsrekken for det
// valgte funnet, og de tre handlingene som finnes: VURDER, HENVIS,
// LUKK. Ingen fjerde.
function detaljpanel(ctx, last, kvitter, settApen, harTerskel) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  const apne = async (merke) => {
    settApen(merke.merkevare_id);
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.merkevare.detalj.tittel") }),
         el("p", { class: "muted", text: t("ui.merkevare.laster") }));
    let funn = { funn: [] };
    let kopier = { bevaringskopier: [] };
    try {
      const id = encodeURIComponent(merke.merkevare_id);
      funn = await hentJson(`/v1/merkevare/${id}/funn`);
      kopier = await hentJson("/v1/merkevare/bevaringskopier");
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.merkevare.detalj.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.merkevare.feil.generell") }));
      return;
    }
    const rader = funn.funn || [];
    const skriver = harScope(ctx, "bestilling:opprett");
    const deler = [
      el("h2", { text: t("ui.merkevare.detalj.tittel") }),
      el("p", { class: "muted",
                text: `${merke.navn} · ${t(ARTTEKST[merke.art]
                                           || merke.art)}` }),
    ];
    if (!rader.length) {
      deler.push(el("p", { class: "muted",
        text: t("ui.merkevare.funn.ingen") }));
    } else {
      deler.push(funntabell(rader));
    }

    // VURDERINGSREKKEN for det funnet som trenger et menneske mest:
    // det uhenviste over terskel, ellers det uvurderte, ellers det
    // nyeste. Rekkefølgen er dørenes egen (`m55_funnene` sorterer
    // slik), så panelet viser det lista alt har lagt øverst.
    const valgt = rader[0];
    if (valgt) {
      let vurderinger = { vurderinger: [] };
      try {
        const fid = encodeURIComponent(valgt.funn_id);
        vurderinger = await hentJson(
          `/v1/merkevare/funn/${fid}/vurderinger`);
      } catch (e) {
        if (e instanceof UautorisertFeil) {
          ctx.paaUautorisert(); return;
        }
      }
      deler.push(el("p", {
        text: t("ui.merkevare.detalj.valgt")
          .replace("{observert}", valgt.observert_navn)
          .replace("{tilstand}", tilstandTekst(valgt)) }));
      if ((vurderinger.vurderinger || []).length) {
        deler.push(vurderingstabell(vurderinger.vurderinger));
      } else {
        // FRAVÆRET SIES HØYT: et uvurdert funn er ikke et ufarlig
        // funn, det er et funn ingen har tatt stilling til.
        deler.push(el("p", { role: "alert",
          text: t("ui.merkevare.uten_vurdering_varsel") }));
      }

      if (skriver) {
        if (!harTerskel) {
          // UTEN TERSKEL FINNES INGEN VURDER-KNAPP. Døra ville
          // nektet, og en knapp som alltid feiler er verre enn en
          // som ikke finnes.
          deler.push(el("p", { role: "alert",
            text: t("ui.merkevare.ingen_terskel_varsel") }));
        } else if (!valgt.lukket_ts) {
          const vurderKnapp = el("button", { type: "button",
            text: t("ui.merkevare.knapp.vurder") });
          vurderKnapp.addEventListener("click", async () => {
            vurderKnapp.disabled = true;
            let svar;
            try {
              svar = await vurderMerkevarefunn(valgt.funn_id);
            } catch (e) {
              vurderKnapp.disabled = false;
              if (e instanceof UautorisertFeil) {
                ctx.paaUautorisert(); return;
              }
              const m = e && e.status === 409
                ? t("ui.merkevare.feil.vurdering")
                : t("ui.merkevare.feil.generell");
              kvitter(m); meldLive(m);
              return;
            }
            // SVARET BÆRER DOMMEN, IKKE BARE «OK». Den som vurderer
            // skal se hva vurderingen SIER — likheten, terskelen den
            // ble målt mot, og hva den hviler på.
            const m = t("ui.merkevare.skjema.vurdert")
              .replace("{likhet}", String(svar.likhet))
              .replace("{terskel}", String(svar.terskel_brukt))
              .replace("{grunnlag}", grunnlagTekst(svar.grunnlag));
            kvitter(m); meldLive(m);
            await last();
          });
          deler.push(el("div", { class: "skjema-bunn" }, vurderKnapp,
            el("p", { class: "muted",
                      text: t("ui.merkevare.vurder_hjelp") })));
        }
      }

      if (skriver && !valgt.lukket_ts && !valgt.henvist_unntak_id) {
        deler.push(henvisskjema(ctx, last, valgt, kvitter));
      }
      if (skriver && !valgt.lukket_ts) {
        deler.push(lukkfunnskjema(ctx, last, valgt, kvitter));
      }
    }

    if (skriver) {
      deler.push(funnskjema(ctx, last, merke.merkevare_id,
                            (kopier.bevaringskopier || []), kvitter));
      const aktiv = el("button", { type: "button",
        text: merke.aktiv ? t("ui.merkevare.knapp.deaktiver")
                          : t("ui.merkevare.knapp.aktiver") });
      aktiv.addEventListener("click", async () => {
        aktiv.disabled = true;
        try {
          await settMerkevareAktiv(merke.merkevare_id, !merke.aktiv);
        } catch (e) {
          aktiv.disabled = false;
          if (e instanceof UautorisertFeil) {
            ctx.paaUautorisert(); return;
          }
          const m = t("ui.merkevare.feil.generell");
          kvitter(m); meldLive(m);
          return;
        }
        kvitter(t("ui.merkevare.skjema.aktiv_ok"));
        meldLive(t("ui.merkevare.skjema.aktiv_ok"));
        await last();
      });
      deler.push(el("div", { class: "skjema-bunn" }, aktiv));
    }
    sett(node, ...deler);
  };
  return { node, apne };
}


// HENVISNINGEN — MODULENS ENESTE UTGANG.
//
// Den sender ingenting. Den fester en peker til en sak i M-37s
// unntakskø, og der beslutter et menneske. Hjelpeteksten sier det
// rett ut, fordi ordet «henvis» ellers kunne leses som «send».
function henvisskjema(ctx, last, funn, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const unntak = el("input", { id: "mv-h-unntak", name: "unntak",
    type: "text", required: true, maxlength: "36" });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.merkevare.knapp.henvis") });
  const h = "[0-9a-fA-F]";
  const uuidmal = new RegExp(
    `^${h}{8}-${h}{4}-${h}{4}-${h}{4}-${h}{12}$`);
  const vurder = () => {
    knapp.disabled = !uuidmal.test(unntak.value.trim());
  };
  unntak.addEventListener("input", vurder);
  unntak.addEventListener("change", vurder);
  skjema.append(
    felt("mv-h-unntak", "ui.merkevare.henvis.unntak", unntak,
         "ui.merkevare.henvis.unntak_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.merkevare.skjema.henvist",
    tilbakestill: () => { unntak.value = ""; knapp.disabled = true; },
    send: (idem) => henvisMerkevarefunn(funn.funn_id,
                                        unntak.value.trim(), idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.merkevare.henvis.tittel") }),
    el("p", { class: "muted",
              text: t("ui.merkevare.henvis.hvorfor") }),
    skjema, utfall);
}


function lukkfunnskjema(ctx, last, funn, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const begrunnelse = el("input", { id: "mv-l-begrunnelse",
    name: "begrunnelse", type: "text", required: true,
    minlength: "4", maxlength: "4000" });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.merkevare.knapp.lukk_funn") });
  const vurder = () => {
    knapp.disabled = begrunnelse.value.trim().length < 4;
  };
  begrunnelse.addEventListener("input", vurder);
  skjema.append(
    felt("mv-l-begrunnelse", "ui.merkevare.lukk.begrunnelse",
         begrunnelse, "ui.merkevare.lukk.begrunnelse_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.merkevare.skjema.funn_lukket",
    tilbakestill: () => {
      begrunnelse.value = ""; knapp.disabled = true;
    },
    send: (idem) => lukkMerkevarefunn(funn.funn_id,
                                      begrunnelse.value.trim(), idem),
  });
  const deler = [
    el("h3", { text: t("ui.merkevare.lukk.tittel") }),
  ];
  // DØRA NEKTER, OG FLATEN SIER HVORFOR PÅ FORHÅND. Et funn over
  // tenantens egen terskel som ikke er henvist kan ikke lukkes —
  // modulen har én utgang, og kunne den lukkes forbi, ville modulens
  // eneste virkning vært viskbar.
  if (funn.over_terskel && !funn.henvist_unntak_id) {
    deler.push(el("p", { role: "alert",
      text: t("ui.merkevare.lukk.nektes")
        .replace("{likhet}", String(funn.likhet))
        .replace("{terskel}", String(funn.terskel_brukt)) }));
  } else {
    deler.push(skjema, utfall);
  }
  return el("div", { class: "skjemaboks" }, ...deler);
}


// VARSELSEKSJONEN. `forveksling_ikke_henvist` KAN IKKE LUKKES — døra
// nekter det, av samme grunn som M-49s bekreftede treff (117), M-46s
// udekkede absolutte krav (118) og M-51s takfunn (119).
function varselseksjon(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.merkevare.varsler.tittel") }),
    el("p", { class: "muted", text: t("ui.merkevare.laster") }));
  (async () => {
    let d;
    try {
      d = await hentJson("/v1/merkevare/varsler");
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.merkevare.varsler.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.merkevare.feil.generell") }));
      return;
    }
    const varsler = d.varsler || [];
    const deler = [el("h2", { text: t("ui.merkevare.varsler.tittel") })];
    if (!varsler.length) {
      deler.push(el("p", { class: "muted",
        text: t("ui.merkevare.varsler.ingen") }));
    } else {
      const tbody = el("tbody");
      for (const v of varsler) {
        tbody.append(el("tr", {},
          el("th", { scope: "row", text: v.merkenavn }),
          el("td", { text: v.observert_navn || "–" }),
          el("td", { text: t(VARSELTEKST[v.varseltype]
                             || v.varseltype) }),
          // LIKHETEN STÅR PÅ VARSELET, med terskelen: «over terskel»
          // uten å si hvor mye er en beskjed man ikke kan handle på.
          el("td", { text: likhetTekst(v) }),
          el("td", { class: "tall",
                     text: v.over_grense === null
                           || v.over_grense === undefined
                       ? "–" : String(v.over_grense) }),
          el("td", { text: v.detalj || "–" })));
      }
      deler.push(el("div", { class: "tablewrap" }, el("table", {},
        el("caption", { text: t("ui.merkevare.varsler.tittel") }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col", text: t("ui.merkevare.kol.merke") }),
          el("th", { scope: "col",
                     text: t("ui.merkevare.kol.observert") }),
          el("th", { scope: "col",
                     text: t("ui.merkevare.kol.varseltype") }),
          el("th", { scope: "col",
                     text: t("ui.merkevare.kol.likhet") }),
          el("th", { scope: "col",
                     text: t("ui.merkevare.kol.dogn") }),
          el("th", { scope: "col",
                     text: t("ui.merkevare.kol.detalj") }))),
        tbody)));
      if (harScope(ctx, "bestilling:opprett")) {
        const lukkbare = varsler.filter(
          (v) => v.varseltype !== "forveksling_ikke_henvist");
        if (lukkbare.length) {
          deler.push(lukkvarselskjema(ctx, last, lukkbare, kvitter));
        }
      }
    }
    sett(node, ...deler);
  })();
  return node;
}


function lukkvarselskjema(ctx, last, varsler, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const valg = el("select", { id: "mv-v-valg", name: "varsel",
                              required: true });
  valg.append(el("option", { value: "",
    text: t("ui.merkevare.varsler.velg") }));
  for (const v of varsler) {
    valg.append(el("option", { value: v.varsel_id,
      text: `${v.merkenavn} — ${t(VARSELTEKST[v.varseltype]
                                  || v.varseltype)}` }));
  }
  const notat = el("input", { id: "mv-v-notat", name: "notat",
    type: "text", required: true, minlength: "4", maxlength: "4000" });
  const knapp = el("button", { type: "submit",
    text: t("ui.merkevare.knapp.lukk_varsel") });
  skjema.append(
    felt("mv-v-valg", "ui.merkevare.varsler.hvilket", valg, null),
    felt("mv-v-notat", "ui.merkevare.varsler.notat", notat,
         "ui.merkevare.varsler.notat_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.merkevare.skjema.varsel_lukket",
    tilbakestill: () => { valg.value = ""; notat.value = ""; },
    send: (idem) => lukkMerkevarevarsel(valg.value,
                                        notat.value.trim(), idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.merkevare.varsler.lukk_tittel") }),
    el("p", { class: "muted",
              text: t("ui.merkevare.varsler.lukk_hvorfor") }),
    skjema, utfall);
}


export function visMerkevare(hoved, ctx) {
  const hode = () => flateHode(t("ui.merkevare.tittel"),
    t("ui.merkevare.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenRad = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const settApen = (id) => { apenRad = id; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/merkevare"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const merker = d.merker || [];
      const kopier = d.bevaringskopier || [];
      const detalj = detaljpanel(ctx, last, kvitter, settApen,
                                 Boolean(s.har_krav));

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.merkevare.oversikt.tittel") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT.
        el("p", { class: "muted",
                  text: t("ui.merkevare.oversikt.hvorfor") }));

      const liste = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.merkevare.liste.tittel") }));
      if (!merker.length) {
        liste.append(el("p", { class: "muted",
          text: t("ui.merkevare.liste.ingen") }));
      } else {
        liste.append(merketabell(merker, detalj.apne));
      }

      const kopiseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.merkevare.kopier.tittel") }));
      if (!kopier.length) {
        kopiseksjon.append(el("p", { class: "muted",
          text: t("ui.merkevare.kopier.ingen") }));
      } else {
        kopiseksjon.append(kopitabell(kopier));
      }

      const kravseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.merkevare.krav.seksjon") }));
      if (!d.krav) {
        kravseksjon.append(el("p", { role: "alert",
          text: t("ui.merkevare.ingen_terskel_varsel") }));
      } else {
        kravseksjon.append(el("p", { class: "muted",
          text: t("ui.merkevare.krav.versjon")
            .replace("{versjon}", String(d.krav.versjon))
            .replace("{terskel}",
                     String(d.krav.forvekslingsterskel)) }));
      }

      const deler = [oversikt, liste, kopiseksjon, kravseksjon,
                     detalj.node, varselseksjon(ctx, last, kvitter)];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(merkeskjema(ctx, last, kvitter),
                   kopiskjema(ctx, last, kvitter),
                   kravskjema(ctx, last, d.krav, kvitter));
      }
      sett(kropp, ...deler);
      if (apenRad) {
        const rad = merker.find((x) => x.merkevare_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
