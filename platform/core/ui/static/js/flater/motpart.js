// Motpartsregisteret (M-48 v1) — REGISTERET, OG ETT OPPSLAG.
//
// DET FINNES EN «SLÅ OPP»-KNAPP HER, OG DEN ER FLATENS VANSKELIGSTE
// ELEMENT.
//
// I M-19 var FRAVÆRET av knappen dommen: et adresseoppslag er en
// utgående kanal med personopplysninger i, og v1 skulle ikke ha den.
// Her er oppslaget koblet på (eierbeslutning 3/9), fordi
// foretaksregisteret er offentlig og et organisasjonsnummer er
// offentlige foretaksdata. Da kan ikke doktrinen vises som et fravær,
// og må vises som noe annet:
//
//   * FORMÅL OG HJEMMEL ER PÅKREVDE FELT. Knappen er deaktivert til
//     begge er fylt ut. Det finnes ingen standardverdi noe sted i
//     denne fila — en forhåndsvalgt «kredittvurdering» ville gjort
//     `oppslag_uten_formaal_og_hjemmel` til pynt, fordi brukeren da
//     aldri måtte ta stilling.
//
//   * FERSKHETSVINDUET STÅR SYNLIG, og knappen sier hvorfor den ikke
//     kan brukes når vinduet er åpent. Et oppslag som blir NEKTET av
//     basen etter et klikk lærer brukeren ingenting; et vindu som
//     står på skjermen lærer dem regelen.
//
//   * ANTALL OPPSLAG SISTE DØGN OG DEN REGISTRERTE VERTEN STÅR ØVERST
//     i sammendraget. Klyngens unntak er begrunnet med at
//     forespørselen er nødvendig — da må antallet forespørsler være
//     det første noen ser, ikke noe man må grave etter.
//
// DET FINNES INGEN «SETT KREDITTGRENSE»-KNAPP, og DET fraværet er
// fortsatt dommen. Spesifikasjonens vakt sier «setter aldri
// kredittgrensen selv», og 116 har ingen kolonne å sette den i.
// Vurderingen heter `foreslatt_grense_ore` overalt, også her.
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, deaktiverMotpart, hentJson, lukkMotpartsfunn,
  nyIdempotensnokkel, registrerMotpart, registrerMotpartsvurdering,
  settMotpartskrav, slaaOppMotpart,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// LUKKEDE SETT, speilet fra 116. Ingen av dem har en standardverdi i
// skjemaene under.
export const FORMAAL = ["kredittvurdering", "onboarding",
                        "periodisk_kontroll", "manuell_gjennomgang"];
export const GRUNNLAG = ["foretaksregister", "manuell_gjennomgang"];

const FORMAALTEKST = {
  kredittvurdering: "ui.motpart.formaal.kredittvurdering",
  onboarding: "ui.motpart.formaal.onboarding",
  periodisk_kontroll: "ui.motpart.formaal.periodisk_kontroll",
  manuell_gjennomgang: "ui.motpart.formaal.manuell_gjennomgang",
};
const GRUNNLAGTEKST = {
  foretaksregister: "ui.motpart.grunnlag.foretaksregister",
  manuell_gjennomgang: "ui.motpart.grunnlag.manuell_gjennomgang",
};
const STATUSTEKST = {
  aktiv: "ui.motpart.status.aktiv",
  under_avvikling: "ui.motpart.status.under_avvikling",
  avviklet: "ui.motpart.status.avviklet",
  slettet: "ui.motpart.status.slettet",
  ukjent: "ui.motpart.status.ukjent",
};
const MERKE = {
  uvurdert_motpart: "ui.motpart.merke_uvurdert",
  utdatert_vurdering: "ui.motpart.merke_utdatert",
  profil_uten_vurdering: "ui.motpart.merke_umaalt",
  motpart_avviklet: "ui.motpart.merke_avviklet",
  forslag_over_tak: "ui.motpart.merke_over_tak",
  oppslag_uten_svar: "ui.motpart.merke_uten_svar",
  gjentatte_oppslagsfeil: "ui.motpart.merke_oppslagsfeil",
  ingen_krav: "ui.motpart.merke_uten_krav",
};

// ØRE → KRONER, som TEKST. Aldri regnet om til flyttall: beløpet er
// heltall øre hele veien (101s form), og `/100` på et stort tall er
// nøyaktig der presisjonen ville forsvunnet.
export function oreTekst(ore) {
  if (ore === null || ore === undefined) return "–";
  const n = BigInt(ore);
  const neg = n < 0n;
  const a = (neg ? -n : n).toString().padStart(3, "0");
  const kr = a.slice(0, -2);
  const rest = a.slice(-2);
  return `${neg ? "-" : ""}${kr},${rest}`;
}

// ØRE ↔ FELTVERDI, MED HELTALLSMATEMATIKK BEGGE VEIER.
//
// `Math.floor(ore / 100)` og `Number(kr) * 100` er ikke en rundtur:
// 123456 øre vises som 1234 kr, og lagres tilbake som 123400. Femtiseks
// øre forsvinner — STILLE, og på en lagring brukeren gjorde av en helt
// annen grunn (de rettet fristen, ikke beløpet).
//
// Her går begge veier gjennom STRENGER og heltall. Ingen divisjon,
// ingen `Number` på et beløp, ingen avrunding (101s form, og
// invarianten `belop_i_flyttall`).
export function oreTilFelt(ore) {
  if (ore === null || ore === undefined) return "";
  const n = BigInt(ore);
  const neg = n < 0n;
  const a = (neg ? -n : n).toString().padStart(3, "0");
  return `${neg ? "-" : ""}${a.slice(0, -2)}.${a.slice(-2)}`;
}

export function feltTilOre(verdi) {
  const s = String(verdi === null || verdi === undefined ? "" : verdi)
    .trim().replace(",", ".");
  if (!s) return null;
  const m = /^(-?)(\d*)(?:\.(\d{0,2}))?$/.exec(s);
  if (!m) return null;
  const kr = m[2] || "0";
  const ore = (m[3] || "").padEnd(2, "0");
  const tall = BigInt(kr) * 100n + BigInt(ore);
  return Number(m[1] === "-" ? -tall : tall);
}


// «SLIK STO DET DA» — en profil har alltid en kilde og en versjon.
export function profilTekst(rad) {
  if (!rad.siste_registerstatus) return t("ui.motpart.uten_profil");
  const n = STATUSTEKST[rad.siste_registerstatus];
  return n ? t(n) : rad.siste_registerstatus;
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
          ? t("ui.motpart.feil.tilstand")
          : t("ui.motpart.feil.generell") }));
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


// SAMMENDRAGET. Oppslagstallet og verten står FØRST, av grunnen i
// filhodet: et unntak ingen kan telle er ikke et unntak.
export function sammendrag(s) {
  const p = el("p");
  p.append(el("strong", {
    text: t("ui.motpart.oppslag_siste_dogn")
      .replace("{n}", String(s.oppslag_siste_dogn ?? 0)) }));
  p.append(" ", el("span", { class: "muted",
    text: t("ui.motpart.mot_vert")
      .replace("{vert}", String(s.registrert_vert || "–")) }));
  p.append(" ", el("span", {
    text: t("ui.motpart.tellinger")
      .replace("{motparter}", String(s.motparter ?? 0))
      .replace("{aktive}", String(s.aktive ?? 0))
      .replace("{profil}", String(s.med_profil ?? 0))
      .replace("{vurderte}", String(s.vurderte ?? 0)) }));
  if (s.apne_reservasjoner > 0) {
    // EN RESERVASJON UTEN SVAR ER EN FORESPØRSEL VI IKKE VET UTFALLET
    // AV. Den skal se annerledes ut enn et vanlig tall.
    p.append(" ", el("strong", {
      text: t("ui.motpart.apne_reservasjoner")
        .replace("{n}", String(s.apne_reservasjoner)) }));
  }
  if (s.apne_funn > 0) {
    p.append(" ", el("strong", {
      text: t("ui.motpart.apne_funn")
        .replace("{n}", String(s.apne_funn)) }));
  }
  if (s.apne_avviklet > 0) {
    p.append(" ", el("strong", {
      text: t("ui.motpart.apne_avviklet")
        .replace("{n}", String(s.apne_avviklet)) }));
  }
  if (!s.har_krav) {
    p.append(" ", el("strong", { text: t("ui.motpart.ingen_krav") }));
  }
  if (s.vist < s.motparter) {
    p.append(" ", el("strong", {
      text: t("ui.motpart.avkortet").replace("{vist}",
                                             String(s.vist)) }));
  }
  return p;
}


export function motpartTabell(motparter, apne) {
  const tbody = el("tbody");
  for (const m of motparter) {
    // NAVNET ER TEKST, KNAPPEN ER EN OVERSATT ETIKETT (112s form).
    // Legger man motpartsnavnet i knappen, blir tenantens egne data
    // til en kontrolltekst — og pseudo-locale-porten kan ikke lenger
    // skille hardkodet tekst fra data.
    const knapp = el("button", { type: "button",
      text: t("ui.motpart.knapp.apne") });
    knapp.addEventListener("click", () => apne(m));
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: m.navn_oppgitt }),
      el("td", { text: m.organisasjonsnummer }),
      el("td", { text: profilTekst(m) }),
      el("td", { class: "tall",
                 text: oreTekst(m.siste_forslag_ore) }),
      el("td", { text: m.siste_vurdering
                   ? m.siste_vurdering.slice(0, 10) : "–" }),
      el("td", { class: "tall", text: String(m.apne_funn ?? 0) }),
      el("td", { text: m.aktiv ? t("ui.motpart.ja")
                               : t("ui.motpart.nei") }),
      el("td", {}, knapp)));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.motpart.liste.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.motpart.kol.navn") }),
        el("th", { scope: "col", text: t("ui.motpart.kol.orgnr") }),
        el("th", { scope: "col", text: t("ui.motpart.kol.status") }),
        el("th", { scope: "col", text: t("ui.motpart.kol.forslag") }),
        el("th", { scope: "col", text: t("ui.motpart.kol.vurdert") }),
        el("th", { scope: "col", text: t("ui.motpart.kol.funn") }),
        el("th", { scope: "col", text: t("ui.motpart.kol.aktiv") }),
        el("th", { scope: "col",
                   text: t("ui.motpart.kol.handling") }))),
      tbody));
}


export function kravTabell(krav) {
  const rad = (nokkel, verdi) => el("tr", {},
    el("th", { scope: "row", text: t(nokkel) }),
    el("td", { text: verdi }));
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.motpart.krav.tittel") }),
      el("tbody", {},
        rad("ui.motpart.krav.ferskhet",
            String(krav.oppslag_ferskhet_timer)),
        rad("ui.motpart.krav.gyldig",
            String(krav.vurdering_gyldig_dogn)),
        rad("ui.motpart.krav.uvurdert", String(krav.uvurdert_dogn)),
        rad("ui.motpart.krav.tak", oreTekst(krav.maks_forslag_ore)),
        rad("ui.motpart.krav.grunnlag",
            (krav.godkjente_grunnlag || [])
              .map((g) => t(GRUNNLAGTEKST[g] || g)).join(", ")))));
}


export function funnTabell(funn) {
  const tbody = el("tbody");
  for (const f of funn) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: f.navn_oppgitt }),
      el("td", { text: t(MERKE[f.funntype] || f.funntype) }),
      el("td", { class: "tall",
                 text: f.over_grense === null
                   || f.over_grense === undefined
                   ? "–" : String(f.over_grense) }),
      el("td", { text: f.forst_sett ? f.forst_sett.slice(0, 10) : "–" })));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.motpart.funn.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.motpart.kol.navn") }),
        el("th", { scope: "col", text: t("ui.motpart.kol.funntype") }),
        el("th", { scope: "col", text: t("ui.motpart.kol.over") }),
        el("th", { scope: "col",
                   text: t("ui.motpart.kol.forst_sett") }))),
      tbody));
}


function kravSkjema(ctx, last, krav, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const ferskhet = el("input", { id: "mp-k-ferskhet",
    name: "ferskhet", type: "number", required: true, step: "1",
    min: "0", max: "8760" });
  const gyldig = el("input", { id: "mp-k-gyldig", name: "gyldig",
    type: "number", required: true, step: "1", min: "1", max: "3650" });
  const uvurdert = el("input", { id: "mp-k-uvurdert",
    name: "uvurdert", type: "number", required: true, step: "1",
    min: "0", max: "3650" });
  // `step: "0.01"` fordi feltet bærer ØRE-presisjon (CodeRabbit
  // fant rundturtapet i 118; samme feil sto her fra 116).
  const tak = el("input", { id: "mp-k-tak", name: "tak",
    type: "number", required: true, step: "0.01", min: "0",
    max: "1000000000" });
  if (krav) {
    ferskhet.value = String(krav.oppslag_ferskhet_timer);
    gyldig.value = String(krav.vurdering_gyldig_dogn);
    uvurdert.value = String(krav.uvurdert_dogn);
    tak.value = oreTilFelt(krav.maks_forslag_ore);
  }
  const grunnlagsboks = el("fieldset", { class: "felt" },
    el("legend", { text: t("ui.motpart.krav.grunnlag") }));
  const bokser = {};
  for (const g of GRUNNLAG) {
    const id = `mp-k-g-${g}`;
    const boks = el("input", { id, name: "grunnlag", type: "checkbox",
                               value: g });
    if (krav && (krav.godkjente_grunnlag || []).includes(g)) {
      boks.checked = true;
    } else if (!krav && g === "foretaksregister") {
      boks.checked = true;
    }
    bokser[g] = boks;
    grunnlagsboks.append(el("div", { class: "felt-avkrysning" }, boks,
      el("label", { for: id, text: t(GRUNNLAGTEKST[g]) })));
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.motpart.knapp.lagre_krav") });
  skjema.append(
    felt("mp-k-ferskhet", "ui.motpart.krav.ferskhet", ferskhet,
         "ui.motpart.krav.ferskhet_hjelp"),
    felt("mp-k-gyldig", "ui.motpart.krav.gyldig", gyldig,
         "ui.motpart.krav.gyldig_hjelp"),
    felt("mp-k-uvurdert", "ui.motpart.krav.uvurdert", uvurdert,
         "ui.motpart.krav.uvurdert_hjelp"),
    felt("mp-k-tak", "ui.motpart.krav.tak_kr", tak,
         "ui.motpart.krav.tak_hjelp"),
    grunnlagsboks,
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.motpart.skjema.krav_ok",
    // KRONER INN, ØRE UT. Multiplikasjonen skjer på et HELTALL og
    // aldri på et flyttall — `Math.round(x * 100)` på 12.34 gir 1234,
    // men veien dit går innom 1233.9999999999998.
    send: (idem) => settMotpartskrav({
      oppslag_ferskhet_timer: Number(ferskhet.value),
      vurdering_gyldig_dogn: Number(gyldig.value),
      uvurdert_dogn: Number(uvurdert.value),
      maks_forslag_ore: feltTilOre(tak.value),
      godkjente_grunnlag: GRUNNLAG.filter((g) => bokser[g].checked),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.motpart.krav.tittel") }), skjema, utfall);
}


function motpartSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const orgnr = el("input", { id: "mp-n-orgnr", name: "orgnr",
    type: "text", required: true, inputmode: "numeric",
    pattern: "[0-9]{9}", maxlength: "9" });
  const navn = el("input", { id: "mp-n-navn", name: "navn",
    type: "text", required: true, maxlength: "300" });
  const knapp = el("button", { type: "submit",
    text: t("ui.motpart.knapp.registrer") });
  skjema.append(
    felt("mp-n-orgnr", "ui.motpart.ny.orgnr", orgnr,
         "ui.motpart.ny.orgnr_hjelp"),
    felt("mp-n-navn", "ui.motpart.ny.navn", navn,
         "ui.motpart.ny.navn_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.motpart.skjema.motpart_ok",
    tilbakestill: () => { orgnr.value = ""; navn.value = ""; },
    send: (idem) => registrerMotpart({
      organisasjonsnummer: orgnr.value.trim(),
      navn_oppgitt: navn.value.trim(),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.motpart.ny.tittel") }), skjema, utfall);
}


// OPPSLAGSSKJEMAET. Se filhodet: her er doktrinen SYNLIG i stedet for
// å være et fravær.
//
// KNAPPEN ER DEAKTIVERT TIL FORMÅL OG HJEMMEL ER FYLT UT, og ingen av
// dem har en forhåndsvalgt verdi. `<select>`-en åpner på en tom
// «velg»-linje som ikke er et lovlig formål — brukeren MÅ ta stilling.
// Hadde vi forhåndsvalgt «kredittvurdering», ville
// `oppslag_uten_formaal_og_hjemmel` vært en port ingen noen gang
// merket, fordi feltet alltid hadde vært fylt ut av oss.
function oppslagSkjema(ctx, last, motpart, krav, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const formaal = el("select", { id: "mp-o-formaal", name: "formaal",
                                 required: true });
  formaal.append(el("option", { value: "",
    text: t("ui.motpart.oppslag.velg_formaal") }));
  for (const f of FORMAAL) {
    formaal.append(el("option", { value: f, text: t(FORMAALTEKST[f]) }));
  }
  const hjemmel = el("input", { id: "mp-o-hjemmel", name: "hjemmel",
    type: "text", required: true, minlength: "8", maxlength: "500" });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.motpart.knapp.slaa_opp") });
  const vurder = () => {
    knapp.disabled = !formaal.value
      || hjemmel.value.trim().length < 8;
  };
  formaal.addEventListener("change", vurder);
  hjemmel.addEventListener("input", vurder);

  // FERSKHETSVINDUET STÅR SYNLIG. Et oppslag som blir nektet av basen
  // etter et klikk lærer brukeren ingenting; regelen på skjermen gjør
  // det. Teksten er tenantens eget tall, ikke en konstant.
  const vindu = el("p", { class: "muted",
    text: krav
      ? t("ui.motpart.oppslag.vindu")
          .replace("{timer}", String(krav.oppslag_ferskhet_timer))
      : t("ui.motpart.oppslag.uten_krav") });

  skjema.append(
    felt("mp-o-formaal", "ui.motpart.oppslag.formaal", formaal,
         "ui.motpart.oppslag.formaal_hjelp"),
    felt("mp-o-hjemmel", "ui.motpart.oppslag.hjemmel", hjemmel,
         "ui.motpart.oppslag.hjemmel_hjelp"),
    vindu,
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.motpart.skjema.oppslag_ok",
    tilbakestill: () => {
      formaal.value = ""; hjemmel.value = ""; knapp.disabled = true;
    },
    send: (idem) => slaaOppMotpart(motpart.motpart_id, formaal.value,
                                   hjemmel.value.trim(), idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.motpart.oppslag.tittel") }),
    el("p", { class: "muted", text: t("ui.motpart.oppslag.hvorfor") }),
    skjema, utfall);
}


// VURDERINGSSKJEMAET. Feltet heter FORSLAG, ikke grense — og det er
// ikke ordkløveri: det finnes ingen kolonne for en grense i 116, og
// spesifikasjonens vakt sier «setter aldri kredittgrensen selv».
function vurderingSkjema(ctx, last, versjonId, krav, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const grunnlag = el("select", { id: "mp-v-grunnlag",
    name: "grunnlag", required: true });
  grunnlag.append(el("option", { value: "",
    text: t("ui.motpart.vurdering.velg_grunnlag") }));
  for (const g of (krav ? krav.godkjente_grunnlag || [] : GRUNNLAG)) {
    grunnlag.append(el("option", { value: g,
      text: t(GRUNNLAGTEKST[g] || g) }));
  }
  const belop = el("input", { id: "mp-v-belop", name: "belop",
    type: "number", required: true, step: "0.01", min: "0",
    max: "1000000000" });
  const begrunnelse = el("input", { id: "mp-v-begrunnelse",
    name: "begrunnelse", type: "text", required: true,
    minlength: "8", maxlength: "2000" });
  const knapp = el("button", { type: "submit",
    text: t("ui.motpart.knapp.vurder") });
  skjema.append(
    felt("mp-v-grunnlag", "ui.motpart.vurdering.grunnlag", grunnlag,
         null),
    felt("mp-v-belop", "ui.motpart.vurdering.forslag_kr", belop,
         "ui.motpart.vurdering.forslag_hjelp"),
    felt("mp-v-begrunnelse", "ui.motpart.vurdering.begrunnelse",
         begrunnelse, "ui.motpart.vurdering.begrunnelse_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.motpart.skjema.vurdering_ok",
    tilbakestill: () => {
      grunnlag.value = ""; belop.value = ""; begrunnelse.value = "";
    },
    send: (idem) => registrerMotpartsvurdering(versjonId, {
      grunnlag: grunnlag.value,
      foreslatt_grense_ore: feltTilOre(belop.value),
      begrunnelse: begrunnelse.value.trim(),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.motpart.vurdering.tittel") }),
    el("p", { class: "muted",
              text: t("ui.motpart.vurdering.hvorfor") }),
    skjema, utfall);
}


// DETALJPANELET. Historikken og OPPSLAGSLOGGEN står side om side,
// fordi de svarer på hvert sitt spørsmål: «hva sier registeret om
// denne» og «hvor mange ganger har vi spurt».
function detaljpanel(ctx, last, kvitter, settApen, krav) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  const apne = async (motpart) => {
    settApen(motpart.motpart_id);
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.motpart.detalj.tittel") }),
         el("p", { class: "muted", text: t("ui.motpart.laster") }));
    let hist = { versjoner: [] };
    let logg = { oppslag: [] };
    try {
      const id = encodeURIComponent(motpart.motpart_id);
      hist = await hentJson(`/v1/motpart/${id}/historikk`);
      logg = await hentJson(`/v1/motpart/${id}/oppslagslogg`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.motpart.detalj.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.motpart.feil.generell") }));
      return;
    }
    const versjoner = hist.versjoner || [];
    const vTbody = el("tbody");
    for (const v of versjoner) {
      vTbody.append(el("tr", {},
        el("th", { scope: "row", text: v.gjelder_fra }),
        el("td", { text: v.navn_registrert }),
        el("td", { text: v.organisasjonsform }),
        el("td", { text: t(STATUSTEKST[v.registerstatus]
                           || v.registerstatus) }),
        el("td", { text: v.kildeversjon })));
    }
    const oTbody = el("tbody");
    for (const o of logg.oppslag || []) {
      oTbody.append(el("tr", {},
        el("th", { scope: "row",
                   text: o.reservert.slice(0, 19).replace("T", " ") }),
        el("td", { text: o.vert }),
        el("td", { text: t(FORMAALTEKST[o.formaal] || o.formaal) }),
        el("td", { text: o.hjemmel }),
        el("td", { text: o.svarstatus })));
    }
    // OVERSKRIFTEN ER EN OVERSATT TITTEL, ikke motpartsnavnet:
    // tenantens egne data hører hjemme som data, ikke som
    // kontrolltekst (samme grunn som radknappen over).
    const deler = [
      el("h2", { text: t("ui.motpart.detalj.tittel") }),
      el("p", { class: "muted",
                text: `${motpart.navn_oppgitt} · `
                      + motpart.organisasjonsnummer }),
      el("div", { class: "tablewrap" }, el("table", {},
        el("caption", { text: t("ui.motpart.historikk.tittel") }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col",
                     text: t("ui.motpart.kol.gjelder_fra") }),
          el("th", { scope: "col", text: t("ui.motpart.kol.navn") }),
          el("th", { scope: "col", text: t("ui.motpart.kol.form") }),
          el("th", { scope: "col", text: t("ui.motpart.kol.status") }),
          el("th", { scope: "col",
                     text: t("ui.motpart.kol.kildeversjon") }))),
        vTbody)),
      // LOGGEN ER TENANTENS. Se `oppslagslogg_endepunkt`: et unntak
      // ingen kan etterprøve er ikke et unntak, det er et løfte.
      el("div", { class: "tablewrap" }, el("table", {},
        el("caption", { text: t("ui.motpart.logg.tittel") }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col",
                     text: t("ui.motpart.kol.reservert") }),
          el("th", { scope: "col", text: t("ui.motpart.kol.vert") }),
          el("th", { scope: "col", text: t("ui.motpart.kol.formaal") }),
          el("th", { scope: "col", text: t("ui.motpart.kol.hjemmel") }),
          el("th", { scope: "col", text: t("ui.motpart.kol.svar") }))),
        oTbody)),
    ];
    if (harScope(ctx, "bestilling:opprett") && motpart.aktiv) {
      deler.push(oppslagSkjema(ctx, last, motpart, krav, kvitter));
      if (versjoner.length) {
        deler.push(vurderingSkjema(ctx, last, versjoner[0].versjon_id,
                                   krav, kvitter));
      }
      const deakt = el("button", { type: "button",
        text: t("ui.motpart.knapp.deaktiver") });
      deakt.addEventListener("click", async () => {
        deakt.disabled = true;
        try {
          await deaktiverMotpart(motpart.motpart_id);
        } catch (e) {
          deakt.disabled = false;
          if (e instanceof UautorisertFeil) {
            ctx.paaUautorisert(); return;
          }
          // EN FEILET HANDLING MÅ SIES HØYT (CodeRabbit fant den i
          // 117; samme feil sto her fra 116). Uten dette re-aktiveres
          // knappen og ingenting skjer på skjermen — brukeren tror
          // deaktiveringen gikk gjennom.
          const m = e && e.status === 409
            ? t("ui.motpart.feil.tilstand")
            : t("ui.motpart.feil.generell");
          kvitter(m);
          meldLive(m);
          return;
        }
        kvitter(t("ui.motpart.skjema.deaktivert_ok"));
        meldLive(t("ui.motpart.skjema.deaktivert_ok"));
        await last();
      });
      deler.push(el("div", { class: "skjema-bunn" }, deakt));
    }
    sett(node, ...deler);
  };
  return { node, apne };
}


// FUNNSEKSJONEN. Den lastes for seg fordi funnene er
// KRYSS-MOTPART: «hvilke motparter trenger noen å se på i dag» er et
// annet spørsmål enn «hva vet vi om denne ene».
//
// LUKKING KREVER ET NOTAT, og feltet er påkrevd her som i basen. En
// sveip som produserer funn ingen kan lukke blir et varsel ingen
// leser — og et funn som lukkes uten begrunnelse er et funn som ble
// gjemt, ikke løst.
function funnseksjon(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.motpart.funn.tittel") }),
    el("p", { class: "muted", text: t("ui.motpart.laster") }));
  (async () => {
    let d;
    try {
      d = await hentJson("/v1/motpart/funn");
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.motpart.funn.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.motpart.feil.generell") }));
      return;
    }
    const funn = d.funn || [];
    const deler = [el("h2", { text: t("ui.motpart.funn.tittel") })];
    if (!funn.length) {
      deler.push(el("p", { class: "muted",
        text: t("ui.motpart.funn.ingen") }));
    } else {
      deler.push(funnTabell(funn));
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(lukkSkjema(ctx, last, funn, kvitter));
      }
    }
    sett(node, ...deler);
  })();
  return node;
}


function lukkSkjema(ctx, last, funn, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const valg = el("select", { id: "mp-f-valg", name: "funn",
                              required: true });
  valg.append(el("option", { value: "",
    text: t("ui.motpart.funn.velg") }));
  for (const f of funn) {
    valg.append(el("option", {
      value: `${f.motpart_id}\u001f${f.funntype}`,
      text: `${f.navn_oppgitt} — ${t(MERKE[f.funntype] || f.funntype)}`,
    }));
  }
  const notat = el("input", { id: "mp-f-notat", name: "notat",
    type: "text", required: true, minlength: "4", maxlength: "2000" });
  const knapp = el("button", { type: "submit",
    text: t("ui.motpart.knapp.lukk_funn") });
  skjema.append(
    felt("mp-f-valg", "ui.motpart.funn.hvilket", valg, null),
    felt("mp-f-notat", "ui.motpart.funn.notat", notat,
         "ui.motpart.funn.notat_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.motpart.skjema.funn_lukket",
    tilbakestill: () => { valg.value = ""; notat.value = ""; },
    send: (idem) => {
      const [id, type] = valg.value.split("\u001f");
      return lukkMotpartsfunn(id, type, notat.value.trim(), idem);
    },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.motpart.funn.lukk_tittel") }), skjema,
    utfall);
}


export function visMotpart(hoved, ctx) {
  const hode = () => flateHode(t("ui.motpart.tittel"),
    t("ui.motpart.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenRad = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const settApen = (id) => { apenRad = id; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/motpart"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const motparter = d.motparter || [];
      const detalj = detaljpanel(ctx, last, kvitter, settApen, d.krav);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.motpart.oversikt.tittel") }),
        sammendrag(s),
        el("p", { class: "muted",
                  text: t("ui.motpart.oversikt.hvorfor") }));

      const liste = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.motpart.liste.tittel") }));
      if (!motparter.length) {
        liste.append(el("p", { class: "muted",
          text: t("ui.motpart.liste.ingen") }));
      } else {
        liste.append(motpartTabell(motparter, detalj.apne));
      }

      const kravseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.motpart.krav.tittel") }));
      if (!d.krav) {
        kravseksjon.append(el("p", { class: "muted",
          text: t("ui.motpart.ingen_krav") }));
      } else {
        kravseksjon.append(
          el("p", { class: "muted",
            text: t("ui.motpart.krav.versjon")
              .replace("{versjon}", String(d.krav.versjon)) }),
          kravTabell(d.krav));
      }

      const deler = [oversikt, liste, kravseksjon, detalj.node,
                     funnseksjon(ctx, last, kvitter)];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(motpartSkjema(ctx, last, kvitter),
                   kravSkjema(ctx, last, d.krav, kvitter));
      }
      sett(kropp, ...deler);
      if (apenRad) {
        const rad = motparter.find((x) => x.motpart_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
