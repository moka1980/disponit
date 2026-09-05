// M-47 myndighetsrapporteringsagenten (123) — FRISTEN ER PRODUKTET.
//
// FLATENS VIKTIGSTE JOBB er å vise HVA SOM HAR GÅTT GALT, ikke hva som
// er i orden. Det er ikke en stilistisk preferanse — det er modulens
// dom:
//
//   For klynge 6 var skaden å HANDLE, og avholdenhet var hele svaret.
//   HER ER SKADEN OGSÅ Å LA VÆRE. En frist som går uten innsending er
//   nøyaktig det modulen ble bygget for å hindre.
//
//     EN STILLE M-47 ER VERRE ENN INGEN M-47.
//
// Derfor står `frist_passert` FØRST og i fet skrift i sammendraget, og
// derfor sorterer basen de passerte fristene øverst. En liste sortert
// på registreringstidspunkt ville begravd avviket under alt som er i
// orden — og en flate som gjorde det, ville vært en flate som lot
// fristen gå.
//
// DET FINNES INGEN «SEND INN»-KNAPP, OG DET KAN IKKE FINNES. En
// innsending til en myndighet er BINDENDE og kan ikke kalles tilbake.
// «Registrer bevis» er det motsatte: den skriver ned at ET MENNESKE
// har sendt inn, et annet sted, med kvitteringsreferansen myndigheten
// ga DEM.
//
// HJEMMELEN STÅR PÅ HVER RAD. En frist uten hjemmel er en påstand om at
// noen må gjøre noe, uten å si hvem som har bestemt det — og den som
// blir bedt om å sende inn skal kunne se hvem som krever det.
//
// OG REGELVERKET BÆRER OM DET GJELDER I DAG. En foreldet regel ser
// nøyaktig ut som en riktig regel; versjonen alene er nettopp
// opplysningen som gjør dem umulige å skille.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  hentJson, lukkMyndighetsfunn, nyIdempotensnokkel,
  registrerPliktbevis, registrerPlikttype, registrerRapportplikt,
  registrerRegelverk, settMyndighetskrav, settRegelverkGyldigTil,
  UautorisertFeil,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

export const MYNDIGHETER = ["skatteetaten", "altinn", "brreg", "ssb",
                            "nav", "arbeidstilsynet", "annen"];
export const FREKVENSER = ["maanedlig", "to_maanedlig", "kvartalsvis",
                           "halvaarlig", "aarlig", "ved_hendelse"];

const MYNDIGHETSTEKST = {
  skatteetaten: "ui.myndighet.myndighet_skatteetaten",
  altinn: "ui.myndighet.myndighet_altinn",
  brreg: "ui.myndighet.myndighet_brreg",
  ssb: "ui.myndighet.myndighet_ssb",
  nav: "ui.myndighet.myndighet_nav",
  arbeidstilsynet: "ui.myndighet.myndighet_arbeidstilsynet",
  annen: "ui.myndighet.myndighet_annen",
};

const FREKVENSTEKST = {
  maanedlig: "ui.myndighet.frekvens_maanedlig",
  to_maanedlig: "ui.myndighet.frekvens_to_maanedlig",
  kvartalsvis: "ui.myndighet.frekvens_kvartalsvis",
  halvaarlig: "ui.myndighet.frekvens_halvaarlig",
  aarlig: "ui.myndighet.frekvens_aarlig",
  ved_hendelse: "ui.myndighet.frekvens_hendelse",
};

const FUNNTEKST = {
  ingen_krav: "ui.myndighet.funn_uten_krav",
  regelverk_utlopt: "ui.myndighet.funn_regelverk_utlopt",
  regelverk_utloper_snart: "ui.myndighet.funn_regelverk_snart",
  plikt_mot_utlopt_regelverk: "ui.myndighet.funn_plikt_utlopt",
  frist_naermer_seg: "ui.myndighet.funn_frist_naer",
  frist_passert_uten_bevis: "ui.myndighet.funn_frist_passert",
};


// REGELVERKET, MED GYLDIGHETEN SIN — ALDRI VERSJONEN ALENE.
//
// MUTASJONEN SOM DREPER PORTEN: returner bare «MVA-melding 2026-01».
// En versjon uten om hjemmelen fortsatt gjelder er nettopp den
// opplysningen som gjør en avviklet regel umulig å skille fra en
// gyldig — og en plikt registrert mot den ser velformet ut.
export function regelverkTekst(rad) {
  if (!rad || !rad.navn) return t("ui.myndighet.uten_regelverk");
  const navn = `${rad.navn} ${rad.versjon}`;
  if (rad.gyldig_naa === null || rad.gyldig_naa === undefined) {
    return navn;
  }
  return t(rad.gyldig_naa ? "ui.myndighet.regelverk_gyldig"
                          : "ui.myndighet.regelverk_utlopt")
    .replace("{navn}", navn);
}


// FRISTEN, MED RETNING. Fortegnet er hele beskjeden: en frist om sju
// døgn og en som gikk for sju døgn siden er to helt forskjellige
// tilstander, og et tall uten retning ville skjult hvilken.
export function fristTekst(dogn) {
  if (dogn === null || dogn === undefined) {
    return t("ui.myndighet.uten_frist");
  }
  if (dogn < 0) {
    return t("ui.myndighet.frist_passert").replace("{n}", String(-dogn));
  }
  if (dogn === 0) return t("ui.myndighet.frist_i_dag");
  return t("ui.myndighet.frist_om").replace("{n}", String(dogn));
}


// DAGENS DATO I BRUKERENS EGET DØGN.
//
// `toISOString()` ville gitt UTC, og forskjellen er ikke teoretisk: i
// Norge er den én til to timer, og den slår inn nøyaktig ved midnatt —
// altså for den som sender inn sent, som er hele grunnen til at
// modulen finnes.
export function ilokalDato(d) {
  const to = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${to(d.getMonth() + 1)}-${to(d.getDate())}`;
}


// PLIKTENS TILSTAND, MED NAVN PÅ DET SOM MANGLER.
//
// REKKEFØLGEN ER EN DOM. «Sendt inn» sjekkes FØRST: en plikt som ER
// sendt inn er ferdig, uansett hva regelverket har gjort siden. Deretter
// den passerte fristen — avviket — og først til slutt påminnelsen.
export function tilstandTekst(p) {
  if (p.bevis_id) {
    return p.dogn_etter_frist > 0
      ? t("ui.myndighet.sendt_for_sent")
        .replace("{n}", String(p.dogn_etter_frist))
      : t("ui.myndighet.sendt");
  }
  if (p.dogn_til_frist < 0) return t("ui.myndighet.ikke_sendt_passert");
  if (p.regelverk_gyldig_naa === false) {
    return t("ui.myndighet.hjemmel_utlopt");
  }
  return t("ui.myndighet.venter");
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
          ? t("ui.myndighet.feil.tilstand")
          : t("ui.myndighet.feil.generell") }));
      return;
    }
    idem = null;
    knapp.disabled = false;
    if (tilbakestill) tilbakestill();
    meldLive(t(okNokkel));
    kvitter(t(okNokkel));
    await last();
  });
}


// SAMMENDRAGET. `frist_passert` STÅR FØRST OG I FET SKRIFT.
//
// Det er det ene tallet modulen finnes for: plikter der fristen har
// gått uten at noen sendte inn. Et sammendrag som begynte med «12
// plikter registrert» ville fortalt hvor flittige vi har vært, ikke
// hva som er galt.
export function sammendrag(s) {
  const p = el("p");
  p.append(el("strong", {
    text: t("ui.myndighet.passert_sum")
      .replace("{n}", String(s.frist_passert ?? 0)) }));
  if (s.frist_naer > 0) {
    p.append(" ", el("strong", {
      text: t("ui.myndighet.naer_sum")
        .replace("{n}", String(s.frist_naer)) }));
  }
  p.append(" ", el("span", {
    text: t("ui.myndighet.tellinger")
      .replace("{plikter}", String(s.plikter ?? 0))
      .replace("{beviste}", String(s.beviste ?? 0))
      .replace("{ubeviste}", String(s.ubeviste ?? 0)) }));
  if (s.utlopte > 0) {
    p.append(" ", el("span", { class: "muted",
      text: t("ui.myndighet.utlopte_regelverk")
        .replace("{n}", String(s.utlopte)) }));
  }
  if (!s.gyldige) {
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.myndighet.ingen_gyldig_regelverk") }));
  }
  if (s.apne_funn > 0) {
    p.append(" ", el("strong", {
      text: t("ui.myndighet.apne_funn")
        .replace("{n}", String(s.apne_funn)) }));
  }
  if (!s.har_krav) {
    // UTEN VARSELFRIST FINNES DET INGEN FRIST Å VARSLE PÅ, og da er
    // hele registeret uovervåket. Døra nekter nye plikter, men de som
    // alt står der skal si fra om hvorfor ingenting skjer.
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.myndighet.ingen_varselfrist") }));
  } else {
    p.append(" ", el("span", { class: "muted",
      text: t("ui.myndighet.varselfristen_er")
        .replace("{n}", String(s.varselfrist_dogn)) }));
  }
  if (s.vist < s.plikter) {
    p.append(" ", el("strong", {
      text: t("ui.myndighet.avkortet")
        .replace("{vist}", String(s.vist)) }));
  }
  return p;
}


export function regelverkstabell(regelverk, aapne) {
  const tbody = el("tbody");
  for (const r of regelverk) {
    const knapp = aapne
      ? el("button", { type: "button",
          text: t("ui.myndighet.knapp.sett_sluttdato") })
      : null;
    if (knapp) knapp.addEventListener("click", () => aapne(r));
    tbody.append(el("tr", {},
      el("th", { scope: "row",
                 text: t(MYNDIGHETSTEKST[r.myndighet]
                         || r.myndighet) }),
      el("td", { text: r.navn }),
      el("td", { text: r.versjon }),
      // HJEMMELEN ER EN KOLONNE, ikke en detalj man må klikke seg til.
      el("td", { text: r.hjemmel }),
      el("td", { text: r.gyldig_fra }),
      el("td", { text: r.gyldig_til || t("ui.myndighet.uten_sluttdato") }),
      el("td", { text: r.gyldig_naa ? t("ui.myndighet.ja")
                                    : t("ui.myndighet.nei") }),
      el("td", { class: "tall", text: String(r.antall_plikter) }),
      knapp ? el("td", {}, knapp) : null));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.myndighet.regelverk.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col",
                   text: t("ui.myndighet.kol.myndighet") }),
        el("th", { scope: "col", text: t("ui.myndighet.kol.navn") }),
        el("th", { scope: "col", text: t("ui.myndighet.kol.versjon") }),
        el("th", { scope: "col", text: t("ui.myndighet.kol.hjemmel") }),
        el("th", { scope: "col", text: t("ui.myndighet.kol.fra") }),
        el("th", { scope: "col", text: t("ui.myndighet.kol.til") }),
        el("th", { scope: "col",
                   text: t("ui.myndighet.kol.gyldig_naa") }),
        el("th", { scope: "col", text: t("ui.myndighet.kol.plikter") }),
        aapne ? el("th", { scope: "col",
                           text: t("ui.myndighet.kol.handling") })
              : null)),
      tbody));
}


// PLIKTTABELLEN. FRISTEN, HJEMMELEN OG BEVISET — aldri fristen alene.
export function plikttabell(plikter, aapne) {
  const tbody = el("tbody");
  for (const p of plikter) {
    const knapp = aapne && !p.bevis_id
      ? el("button", { type: "button",
          text: t("ui.myndighet.knapp.registrer_bevis") })
      : null;
    if (knapp) knapp.addEventListener("click", () => aapne(p));
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: p.typenavn }),
      el("td", { text: `${p.periode_fra} – ${p.periode_til}` }),
      el("td", { text: p.frist }),
      // FORTEGNET ER BESKJEDEN.
      el("td", { text: fristTekst(p.dogn_til_frist) }),
      el("td", { text: t(MYNDIGHETSTEKST[p.myndighet]
                         || p.myndighet) }),
      el("td", { text: regelverkTekst({
        navn: p.regelnavn, versjon: p.regelversjon,
        gyldig_naa: p.regelverk_gyldig_naa }) }),
      el("td", { text: p.hjemmel }),
      el("td", { text: tilstandTekst(p) }),
      el("td", { text: p.kvittering_ref || "–" }),
      knapp ? el("td", {}, knapp) : el("td", {})));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.myndighet.plikter.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.myndighet.kol.type") }),
        el("th", { scope: "col", text: t("ui.myndighet.kol.periode") }),
        el("th", { scope: "col", text: t("ui.myndighet.kol.frist") }),
        el("th", { scope: "col", text: t("ui.myndighet.kol.igjen") }),
        el("th", { scope: "col",
                   text: t("ui.myndighet.kol.myndighet") }),
        el("th", { scope: "col",
                   text: t("ui.myndighet.kol.regelverk") }),
        el("th", { scope: "col", text: t("ui.myndighet.kol.hjemmel") }),
        el("th", { scope: "col", text: t("ui.myndighet.kol.tilstand") }),
        el("th", { scope: "col",
                   text: t("ui.myndighet.kol.kvittering") }),
        el("th", { scope: "col",
                   text: t("ui.myndighet.kol.handling") }))),
      tbody));
}


// FUNNSEKSJONEN. `kan_lukkes` KOMMER FRA BASEN, ikke fra en liste her.
//
// To funntyper lukkes bare av sveipen, og regelen bor ÉTT sted
// (`m47_funn_er_sveipens`). En kopi i klienten ville vært en andre
// regel som kunne komme i utakt — og da hadde flaten tilbudt en knapp
// som alltid feiler, som er verre enn en valgmulighet som ikke finnes.
function funnseksjon(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.myndighet.funn.tittel") }),
    el("p", { class: "muted", text: t("ui.myndighet.laster") }));
  (async () => {
    let d;
    try {
      d = await hentJson("/v1/myndighet/funn");
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.myndighet.funn.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.myndighet.feil.generell") }));
      return;
    }
    const funn = d.funn || [];
    const deler = [el("h2", { text: t("ui.myndighet.funn.tittel") })];
    if (!funn.length) {
      deler.push(el("p", { class: "muted",
        text: t("ui.myndighet.funn.ingen") }));
    } else {
      const tbody = el("tbody");
      for (const f of funn) {
        tbody.append(el("tr", {},
          el("th", { scope: "row",
                     text: t(FUNNTEKST[f.funntype] || f.funntype) }),
          el("td", { text: f.typenavn || f.regelnavn || "–" }),
          el("td", { text: f.frist || "–" }),
          el("td", { class: "tall",
                     text: f.over_grense === null
                           || f.over_grense === undefined
                       ? "–" : String(f.over_grense) }),
          el("td", { text: f.detalj || "–" }),
          // HVORFOR NOEN FUNN IKKE KAN LUKKES, SAGT PÅ RADEN. Uten
          // dette ville en manglende knapp sett ut som en feil.
          el("td", { text: f.kan_lukkes
            ? t("ui.myndighet.funn.kan_lukkes")
            : t("ui.myndighet.funn.sveipens") })));
      }
      deler.push(el("div", { class: "tablewrap" }, el("table", {},
        el("caption", { text: t("ui.myndighet.funn.tittel") }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col",
                     text: t("ui.myndighet.kol.funntype") }),
          el("th", { scope: "col",
                     text: t("ui.myndighet.kol.gjelder") }),
          el("th", { scope: "col", text: t("ui.myndighet.kol.frist") }),
          el("th", { scope: "col", text: t("ui.myndighet.kol.over") }),
          el("th", { scope: "col", text: t("ui.myndighet.kol.detalj") }),
          el("th", { scope: "col",
                     text: t("ui.myndighet.kol.lukking") }))),
        tbody)));
      if (harScope(ctx, "bestilling:opprett")) {
        // BARE DE BASEN SIER KAN LUKKES. Filteret leser `kan_lukkes`
        // fra svaret; det finnes ingen liste over funntyper her.
        const lukkbare = funn.filter((f) => f.kan_lukkes);
        if (lukkbare.length) {
          deler.push(lukkskjema(ctx, last, lukkbare, kvitter));
        }
      }
    }
    sett(node, ...deler);
  })();
  return node;
}


function lukkskjema(ctx, last, funn, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const valg = el("select", { id: "my-l-valg", name: "funn",
    required: true });
  valg.append(el("option", { value: "",
    text: t("ui.myndighet.funn.velg") }));
  for (const f of funn) {
    valg.append(el("option", { value: f.funn_id,
      text: `${t(FUNNTEKST[f.funntype] || f.funntype)}`
            + ` — ${f.typenavn || f.regelnavn || ""}` }));
  }
  const notat = el("input", { id: "my-l-notat", name: "notat",
    type: "text", required: true, minlength: "4", maxlength: "4000" });
  const knapp = el("button", { type: "submit",
    text: t("ui.myndighet.knapp.lukk_funn") });
  skjema.append(
    felt("my-l-valg", "ui.myndighet.funn.hvilket", valg, null),
    felt("my-l-notat", "ui.myndighet.funn.notat", notat,
         "ui.myndighet.funn.notat_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.myndighet.skjema.funn_lukket",
    tilbakestill: () => { valg.value = ""; notat.value = ""; },
    send: (idem) =>
      lukkMyndighetsfunn(valg.value, notat.value.trim(), idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.myndighet.funn.lukk_tittel") }),
    el("p", { class: "muted",
              text: t("ui.myndighet.funn.lukk_hvorfor") }),
    skjema, utfall);
}


function kravskjema(ctx, last, krav, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const varsel = el("input", { id: "my-k-varsel", name: "varsel",
    type: "number", required: true, min: "1", max: "365", step: "1" });
  const esk = el("input", { id: "my-k-esk", name: "eskalering",
    type: "number", required: true, min: "1", max: "90", step: "1" });
  const regel = el("input", { id: "my-k-regel", name: "regelvarsel",
    type: "number", required: true, min: "1", max: "730", step: "1" });
  // VERDIENE KOMMER FRA SVARET, ALDRI FRA EN KONSTANT HER. En
  // varselfrist hardkodet i klienten ville vært en fullmakt flaten ga
  // seg selv over kundens forsinkelsesgebyr.
  varsel.value = krav ? String(krav.varselfrist_dogn) : "";
  esk.value = krav ? String(krav.eskaleringsfrist_dogn ?? "") : "";
  regel.value = krav ? String(krav.regelvarsel_dogn ?? "") : "";
  const knapp = el("button", { type: "submit",
    text: t("ui.myndighet.knapp.sett_krav") });
  skjema.append(
    felt("my-k-varsel", "ui.myndighet.krav.varsel", varsel,
         "ui.myndighet.krav.varsel_hjelp"),
    felt("my-k-esk", "ui.myndighet.krav.eskalering", esk,
         "ui.myndighet.krav.eskalering_hjelp"),
    felt("my-k-regel", "ui.myndighet.krav.regelvarsel", regel,
         "ui.myndighet.krav.regelvarsel_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.myndighet.skjema.krav_ok",
    send: (idem) => settMyndighetskrav({
      varselfrist_dogn: Math.trunc(Number(varsel.value)),
      eskaleringsfrist_dogn: Math.trunc(Number(esk.value)),
      regelvarsel_dogn: Math.trunc(Number(regel.value)),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.myndighet.krav.tittel") }),
    el("p", { class: "muted", text: t("ui.myndighet.krav.hvorfor") }),
    skjema, utfall);
}


function regelverkskjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const myndighet = el("select", { id: "my-r-myndighet",
    name: "myndighet", required: true });
  for (const m of MYNDIGHETER) {
    myndighet.append(el("option", { value: m,
      text: t(MYNDIGHETSTEKST[m] || m) }));
  }
  const navn = el("input", { id: "my-r-navn", name: "navn",
    type: "text", required: true, maxlength: "500" });
  const versjon = el("input", { id: "my-r-versjon", name: "versjon",
    type: "text", required: true, maxlength: "500" });
  const hjemmel = el("input", { id: "my-r-hjemmel", name: "hjemmel",
    type: "text", required: true, maxlength: "4000" });
  const fra = el("input", { id: "my-r-fra", name: "fra",
    type: "date", required: true });
  const til = el("input", { id: "my-r-til", name: "til",
    type: "date" });
  const sha = el("input", { id: "my-r-sha", name: "sha", type: "text",
    required: true, minlength: "64", maxlength: "64",
    pattern: "[0-9a-fA-F]{64}" });
  const url = el("input", { id: "my-r-url", name: "url", type: "url",
    maxlength: "2000" });
  const knapp = el("button", { type: "submit",
    text: t("ui.myndighet.knapp.registrer_regelverk") });
  skjema.append(
    felt("my-r-myndighet", "ui.myndighet.regelverk.myndighet",
         myndighet, null),
    felt("my-r-navn", "ui.myndighet.regelverk.navn", navn, null),
    felt("my-r-versjon", "ui.myndighet.regelverk.versjon", versjon,
         "ui.myndighet.regelverk.versjon_hjelp"),
    felt("my-r-hjemmel", "ui.myndighet.regelverk.hjemmel", hjemmel,
         "ui.myndighet.regelverk.hjemmel_hjelp"),
    felt("my-r-fra", "ui.myndighet.regelverk.fra", fra, null),
    felt("my-r-til", "ui.myndighet.regelverk.til", til,
         "ui.myndighet.regelverk.til_hjelp"),
    felt("my-r-sha", "ui.myndighet.regelverk.sha", sha,
         "ui.myndighet.regelverk.sha_hjelp"),
    felt("my-r-url", "ui.myndighet.regelverk.url", url, null),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.myndighet.skjema.regelverk_ok",
    tilbakestill: () => {
      navn.value = ""; versjon.value = ""; hjemmel.value = "";
      fra.value = ""; til.value = ""; sha.value = ""; url.value = "";
    },
    send: (idem) => registrerRegelverk({
      myndighet: myndighet.value,
      navn: navn.value.trim(),
      versjon: versjon.value.trim(),
      hjemmel: hjemmel.value.trim(),
      gyldig_fra: fra.value,
      gyldig_til: til.value || null,
      innhold_sha256: sha.value.trim().toLowerCase(),
      kilde_url: url.value.trim() || null,
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.myndighet.regelverk.skjema_tittel") }),
    // ET ALT AVVIKLET REGELVERK KAN REGISTRERES. Arkivet skal kunne
    // svare på hva regelen sa den gangen — skillet går ved plikten.
    el("p", { class: "muted",
              text: t("ui.myndighet.regelverk.hvorfor") }),
    skjema, utfall);
}


function plikttypeskjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const nokkel = el("input", { id: "my-t-nokkel", name: "nokkel",
    type: "text", required: true, minlength: "3", maxlength: "64",
    pattern: "[a-z][a-z0-9_]{2,63}" });
  const navn = el("input", { id: "my-t-navn", name: "navn",
    type: "text", required: true, maxlength: "500" });
  const frekvens = el("select", { id: "my-t-frekvens",
    name: "frekvens", required: true });
  for (const f of FREKVENSER) {
    frekvens.append(el("option", { value: f,
      text: t(FREKVENSTEKST[f] || f) }));
  }
  const beskrivelse = el("input", { id: "my-t-beskrivelse",
    name: "beskrivelse", type: "text", maxlength: "4000" });
  const knapp = el("button", { type: "submit",
    text: t("ui.myndighet.knapp.registrer_plikttype") });
  skjema.append(
    felt("my-t-nokkel", "ui.myndighet.plikttype.nokkel", nokkel,
         "ui.myndighet.plikttype.nokkel_hjelp"),
    felt("my-t-navn", "ui.myndighet.plikttype.navn", navn, null),
    felt("my-t-frekvens", "ui.myndighet.plikttype.frekvens", frekvens,
         "ui.myndighet.plikttype.frekvens_hjelp"),
    felt("my-t-beskrivelse", "ui.myndighet.plikttype.beskrivelse",
         beskrivelse, null),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.myndighet.skjema.plikttype_ok",
    tilbakestill: () => {
      nokkel.value = ""; navn.value = ""; beskrivelse.value = "";
    },
    send: (idem) => registrerPlikttype({
      nokkel: nokkel.value.trim().toLowerCase(),
      navn: navn.value.trim(),
      frekvens: frekvens.value,
      beskrivelse: beskrivelse.value.trim() || null,
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.myndighet.plikttype.tittel") }),
    el("p", { class: "muted",
              text: t("ui.myndighet.plikttype.hvorfor") }),
    skjema, utfall);
}


// PLIKTSKJEMAET. BARE GYLDIGE REGELVERK TILBYS.
//
// Døra nekter mot et avviklet regelverk, og en knapp som alltid feiler
// er verre enn en valgmulighet som ikke finnes. Arkivet står fortsatt i
// tabellen over — det er BRUKEN som er stengt, ikke minnet.
function pliktskjema(ctx, last, regelverk, plikttyper, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const gyldige = regelverk.filter((r) => r.gyldig_naa === true);
  const type = el("select", { id: "my-p-type", name: "type",
    required: true });
  type.append(el("option", { value: "",
    text: t("ui.myndighet.plikt.velg_type") }));
  for (const p of plikttyper) {
    type.append(el("option", { value: p.plikttype_id, text: p.navn }));
  }
  const regel = el("select", { id: "my-p-regel", name: "regelverk",
    required: true });
  regel.append(el("option", { value: "",
    text: t("ui.myndighet.plikt.velg_regelverk") }));
  for (const r of gyldige) {
    regel.append(el("option", { value: r.regelverk_id,
      text: `${r.navn} ${r.versjon} — ${r.hjemmel}` }));
  }
  const fra = el("input", { id: "my-p-fra", name: "fra", type: "date",
    required: true });
  const til = el("input", { id: "my-p-til", name: "til", type: "date",
    required: true });
  const frist = el("input", { id: "my-p-frist", name: "frist",
    type: "date", required: true });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.myndighet.knapp.registrer_plikt") });
  const vurder = () => {
    knapp.disabled = !type.value || !regel.value || !fra.value
      || !til.value || !frist.value;
  };
  for (const k of [type, regel, fra, til, frist]) {
    k.addEventListener("change", vurder);
    k.addEventListener("input", vurder);
  }

  const deler = [];
  if (!gyldige.length) {
    deler.push(el("p", { role: "alert",
      text: t("ui.myndighet.plikt.ingen_gyldige") }));
  } else if (!plikttyper.length) {
    deler.push(el("p", { role: "alert",
      text: t("ui.myndighet.plikt.ingen_typer") }));
  } else {
    deler.push(
      felt("my-p-type", "ui.myndighet.plikt.type", type, null),
      felt("my-p-regel", "ui.myndighet.plikt.regelverk", regel,
           "ui.myndighet.plikt.regelverk_hjelp"),
      felt("my-p-fra", "ui.myndighet.plikt.fra", fra, null),
      felt("my-p-til", "ui.myndighet.plikt.til", til, null),
      felt("my-p-frist", "ui.myndighet.plikt.frist", frist,
           "ui.myndighet.plikt.frist_hjelp"),
      el("div", { class: "skjema-bunn" }, knapp));
  }
  skjema.append(...deler);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.myndighet.skjema.plikt_ok",
    tilbakestill: () => {
      fra.value = ""; til.value = ""; frist.value = "";
      knapp.disabled = true;
    },
    send: (idem) => registrerRapportplikt({
      plikttype_id: type.value,
      regelverk_id: regel.value,
      periode_fra: fra.value,
      periode_til: til.value,
      frist: frist.value,
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.myndighet.plikt.tittel") }),
    el("p", { class: "muted", text: t("ui.myndighet.plikt.hvorfor") }),
    skjema, utfall);
}


// BEVISSKJEMAET. MODULENS SKARPESTE FLATE.
//
// DETTE ER IKKE «SEND INN» MED ET ANNET NAVN. Skjemaet registrerer at
// et MENNESKE har sendt inn, et annet sted, og krever
// kvitteringsreferansen myndigheten ga DEM. Uten den er beviset en
// påstand; med den er det noe man kan slå opp hos den som mottok.
//
// DATOEN KAN IKKE VÆRE I FRAMTIDEN, og det er døra som nekter — ikke
// bare `max` her. Et bevis datert i morgen er ikke et bevis, det er en
// plan, og en plan lukker ikke et fristfunn.
//
// TEKSTEN SIER HVA DETTE ER. Hjelpeteksten er ikke pynt: den som fyller
// ut skjemaet skal ikke kunne tro at systemet sender noe.
function bevispanel(ctx, last, kvitter, settApen) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  const aapne = (plikt) => {
    settApen(plikt.plikt_id);
    node.hidden = false;
    const utfall = el("p", { "aria-live": "polite" });
    const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
    const dato = el("input", { id: "my-b-dato", name: "dato",
      type: "date", required: true });
    // I DAG ER SISTE LOVLIGE DATO. Døra nekter uansett; dette er
    // vennligheten, ikke gjerdet.
    //
    // LOKAL DATO, IKKE UTC (CodeRabbit). `toISOString()` gir UTC-datoen,
    // og klokka halv ett på natta norsk tid er den fortsatt I GÅR —
    // feltet ville nektet dagens lovlige innsending. Det er nettopp
    // brukstilfellet modulen handler om: noen som sender inn sent.
    // Døra sammenligner mot basens `current_date`, så klienten må
    // spørre om det samme døgnet et menneske står i.
    dato.max = ilokalDato(new Date());
    const kvittering = el("input", { id: "my-b-kvittering",
      name: "kvittering", type: "text", required: true,
      maxlength: "200" });
    const person = el("input", { id: "my-b-person", name: "person",
      type: "text", required: true, maxlength: "500" });
    const notat = el("input", { id: "my-b-notat", name: "notat",
      type: "text", maxlength: "4000" });
    const knapp = el("button", { type: "submit",
      text: t("ui.myndighet.knapp.registrer_bevis") });
    skjema.append(
      felt("my-b-dato", "ui.myndighet.bevis.dato", dato,
           "ui.myndighet.bevis.dato_hjelp"),
      felt("my-b-kvittering", "ui.myndighet.bevis.kvittering",
           kvittering, "ui.myndighet.bevis.kvittering_hjelp"),
      felt("my-b-person", "ui.myndighet.bevis.person", person,
           "ui.myndighet.bevis.person_hjelp"),
      felt("my-b-notat", "ui.myndighet.bevis.notat", notat, null),
      el("div", { class: "skjema-bunn" }, knapp));
    skjemaramme(ctx, last, {
      skjema, knapp, utfall, kvitter,
      okNokkel: "ui.myndighet.skjema.bevis_ok",
      send: (idem) => registrerPliktbevis(plikt.plikt_id, {
        innsendt_dato: dato.value,
        kvittering_ref: kvittering.value.trim(),
        innsendt_av_person: person.value.trim(),
        notat: notat.value.trim() || null,
      }, idem),
    });
    const deler = [
      el("h2", { text: t("ui.myndighet.bevis.tittel") }),
      el("p", { class: "muted",
        text: `${plikt.typenavn} · ${plikt.periode_fra} – `
              + `${plikt.periode_til} · ${plikt.frist}` }),
      // FRISTEN, MED RETNING, RETT OVER SKJEMAET. Den som registrerer
      // et bevis for en passert frist skal se at den ER passert.
      el("p", { class: plikt.dogn_til_frist < 0 ? "" : "muted",
                role: plikt.dogn_til_frist < 0 ? "alert" : null,
                text: fristTekst(plikt.dogn_til_frist) }),
      el("p", { class: "muted",
                text: t("ui.myndighet.bevis.hvorfor") }),
      skjema, utfall,
    ];
    sett(node, ...deler);
  };
  return { node, aapne };
}


function sluttdatopanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  const aapne = (regelverk) => {
    node.hidden = false;
    const utfall = el("p", { "aria-live": "polite" });
    const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
    const til = el("input", { id: "my-s-til", name: "til",
      type: "date" });
    til.value = regelverk.gyldig_til || "";
    const knapp = el("button", { type: "submit",
      text: t("ui.myndighet.knapp.sett_sluttdato") });
    skjema.append(
      felt("my-s-til", "ui.myndighet.sluttdato.dato", til,
           "ui.myndighet.sluttdato.dato_hjelp"),
      el("div", { class: "skjema-bunn" }, knapp));
    skjemaramme(ctx, last, {
      skjema, knapp, utfall, kvitter,
      okNokkel: "ui.myndighet.skjema.sluttdato_ok",
      // NØKKELEN SENDES ALLTID, også når verdien er tom (121s lærdom).
      // Utelatt felt og eksplisitt null er to forskjellige ting.
      send: (idem) => settRegelverkGyldigTil(
        regelverk.regelverk_id, til.value || null, idem),
    });
    sett(node,
      el("h2", { text: t("ui.myndighet.sluttdato.tittel") }),
      el("p", { class: "muted", text: regelverkTekst(regelverk) }),
      el("p", { class: "muted",
                text: t("ui.myndighet.sluttdato.hvorfor") }),
      skjema, utfall);
  };
  return { node, aapne };
}


export function visMyndighet(hoved, ctx) {
  const hode = () => flateHode(t("ui.myndighet.tittel"),
    t("ui.myndighet.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenPlikt = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/myndighet"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const regelverk = d.regelverk || [];
      const plikttyper = d.plikttyper || [];
      const plikter = d.plikter || [];
      const skriver = harScope(ctx, "bestilling:opprett");
      const bevis = bevispanel(ctx, last, kvitter,
                               (id) => { apenPlikt = id; });
      const sluttdato = sluttdatopanel(ctx, last, kvitter);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.myndighet.oversikt.tittel") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT.
        el("p", { class: "muted",
                  text: t("ui.myndighet.oversikt.hvorfor") }));

      const pliktseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.myndighet.plikter.tittel") }));
      if (!plikter.length) {
        pliktseksjon.append(el("p", { class: "muted",
          text: t("ui.myndighet.plikter.ingen") }));
      } else {
        pliktseksjon.append(plikttabell(
          plikter, skriver ? bevis.aapne : null));
      }

      const regelseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.myndighet.regelverk.tittel") }));
      if (!regelverk.length) {
        regelseksjon.append(el("p", { role: "alert",
          text: t("ui.myndighet.regelverk.ingen") }));
      } else {
        regelseksjon.append(regelverkstabell(
          regelverk, skriver ? sluttdato.aapne : null));
      }

      // PLIKTENE FØRST, REGELVERKET ETTER. Rekkefølgen er en dom: det
      // som haster er fristene, ikke registeret de hviler på.
      const deler = [oversikt, pliktseksjon, bevis.node, regelseksjon,
                     sluttdato.node,
                     funnseksjon(ctx, last, kvitter)];
      if (skriver) {
        deler.push(pliktskjema(ctx, last, regelverk, plikttyper,
                               kvitter),
                   plikttypeskjema(ctx, last, kvitter),
                   regelverkskjema(ctx, last, kvitter),
                   kravskjema(ctx, last, d.krav, kvitter));
      }
      sett(kropp, ...deler);
      if (apenPlikt) {
        const rad = plikter.find((x) => x.plikt_id === apenPlikt);
        if (rad && !rad.bevis_id) bevis.aapne(rad);
        else apenPlikt = null;
      }
    });
  last();
}
