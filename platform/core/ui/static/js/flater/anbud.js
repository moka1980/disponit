// Anbuds- og konkurransevakten (M-46 v1) — TREFFENE OG UTKASTET,
// IKKE INNSENDINGEN.
//
// FLATENS VIKTIGSTE JOBB er å vise et utkast som IKKE er ferdig, uten
// å tilby en vei til å gjøre det ferdig ved å gjette.
//
// DET FINNES INGEN «SEND INN»-KNAPP, og fraværet er dommen. Et
// innsendt tilbud er BINDENDE, og fristen gjør det irreversibelt på
// den måten som betyr noe: man kan ikke trekke det og sende et bedre
// etterpå. De andre modulenes farligste handlinger kan i det minste
// rettes dagen etter.
//
// DET FINNES HELLER INGEN VEI TIL Å SKRIVE ET FAKTAPUNKT UTEN KILDE.
// Skjemaet krever et kildedokument fra en nedtrekksliste — ikke fordi
// flaten sjekker det, men fordi `utkastpunkt` i 118 ikke har en
// kolonne å legge en påstand i. Feltet finnes ikke, så knappen kan
// ikke finnes.
//
// ET UDEKKET KRAV VISES SOM UDEKKET, ALLTID. Kravene står i én liste
// der de dekkede har sitt sitat og sin kilde, og de udekkede står
// tomme og merket. En flate som filtrerte bort de udekkede ville
// skjult nettopp det som må gjøres — og det er det udekkede ABSOLUTTE
// kravet som gjør et tilbud avvist.
//
// «KLAR TIL GJENNOMGANG» SIER HVA DEN IKKE DEKKER. Knappen nektes av
// døra så lenge et absolutt krav mangler, og når den lykkes forteller
// svaret hvor mange VEKTEDE krav som fortsatt står udekket. Brukeren
// skal vite hva de sender uten.
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, hentJson, lukkAnbudsfunn, merkUtkastKlart,
  nyIdempotensnokkel, opprettAnbudsutkast, registrerAnbud,
  registrerAnbudskrav, registrerAnbudspunkt, registrerKildedokument,
  settAnbudAktiv, settAnbudsprofil,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// LUKKEDE SETT, speilet fra 118.
export const ANBUDSKILDER = ["doffin", "ted", "direkte", "annen"];
export const KRAVTYPER = ["kvalifikasjon", "dokumentasjon", "erfaring",
                          "sertifisering", "okonomi", "annet"];
export const DOKUMENTTYPER = ["sertifikat", "attest", "regnskap",
                              "referanse", "policy", "cv", "annet"];

const KILDETEKST = {
  doffin: "ui.anbud.kilde.doffin",
  ted: "ui.anbud.kilde.ted",
  direkte: "ui.anbud.kilde.direkte",
  annen: "ui.anbud.kilde.annen",
};
const KRAVTYPETEKST = {
  kvalifikasjon: "ui.anbud.kravtype.kvalifikasjon",
  dokumentasjon: "ui.anbud.kravtype.dokumentasjon",
  erfaring: "ui.anbud.kravtype.erfaring",
  sertifisering: "ui.anbud.kravtype.sertifisering",
  okonomi: "ui.anbud.kravtype.okonomi",
  annet: "ui.anbud.kravtype.annet",
};
const DOKTEKST = {
  sertifikat: "ui.anbud.dok.sertifikat",
  attest: "ui.anbud.dok.attest",
  regnskap: "ui.anbud.dok.regnskap",
  referanse: "ui.anbud.dok.referanse",
  policy: "ui.anbud.dok.policy",
  cv: "ui.anbud.dok.cv",
  annet: "ui.anbud.dok.annet",
};
const MERKE = {
  frist_naermer_seg: "ui.anbud.merke_frist_naer",
  frist_passert: "ui.anbud.merke_frist_passert",
  udekket_absolutt_krav: "ui.anbud.merke_udekket_absolutt",
  udekket_krav: "ui.anbud.merke_udekket",
  utlopt_kilde: "ui.anbud.merke_utlopt_kilde",
  ingen_krav_registrert: "ui.anbud.merke_uten_krav",
  utenfor_profil: "ui.anbud.merke_utenfor_profil",
  ingen_profil: "ui.anbud.merke_uten_profil",
};

// ØRE → KRONER som TEKST, aldri via flyttall (101s form). `/100` på
// et stort tall er nøyaktig der presisjonen ville forsvunnet.
export function oreTekst(ore) {
  if (ore === null || ore === undefined) return "–";
  const n = BigInt(ore);
  const neg = n < 0n;
  const a = (neg ? -n : n).toString().padStart(3, "0");
  return `${neg ? "-" : ""}${a.slice(0, -2)},${a.slice(-2)}`;
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


// FRISTEN SOM TEKST, med retning. «Om 3 døgn» og «3 døgn siden» er
// ikke samme sak, og et negativt tall alene ville krevd at leseren
// regnet ut hva minustegnet betyr.
export function fristTekst(dogn) {
  if (dogn === null || dogn === undefined) return "–";
  if (dogn < 0) {
    return t("ui.anbud.frist_passert").replace("{n}", String(-dogn));
  }
  if (dogn === 0) return t("ui.anbud.frist_i_dag");
  return t("ui.anbud.frist_om").replace("{n}", String(dogn));
}

// DEKNINGEN SOM TEKST. «Ingen krav registrert» er noe ANNET enn «alle
// krav dekket» (WCAG 1.4.1 og alminnelig ærlighet): et anbud ingen
// har lest kravene ut av skal ikke se ferdig ut.
export function dekningTekst(rad) {
  if (!rad.antall_krav) return t("ui.anbud.uten_krav");
  if (rad.udekkede_absolutte > 0) {
    return t("ui.anbud.udekkede_absolutte")
      .replace("{n}", String(rad.udekkede_absolutte));
  }
  if (!rad.siste_utkast) return t("ui.anbud.uten_utkast");
  return rad.klar ? t("ui.anbud.klart") : t("ui.anbud.under_arbeid");
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
          ? t("ui.anbud.feil.tilstand")
          : t("ui.anbud.feil.generell") }));
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


// SAMMENDRAGET. Udekkede absolutte krav står FØRST: det er de som
// gjør et tilbud avvist, og nærmeste frist er den ene datoen som ikke
// kan flyttes.
export function sammendrag(s) {
  const p = el("p");
  if (s.udekkede_absolutte > 0) {
    p.append(el("strong", {
      text: t("ui.anbud.udekkede_absolutte_sum")
        .replace("{n}", String(s.udekkede_absolutte)) }));
    p.append(" ");
  }
  p.append(el("span", {
    text: t("ui.anbud.tellinger")
      .replace("{anbud}", String(s.anbud ?? 0))
      .replace("{aktive}", String(s.aktive ?? 0))
      .replace("{utkast}", String(s.med_utkast ?? 0))
      .replace("{klare}", String(s.klare ?? 0)) }));
  if (s.naermeste_frist) {
    p.append(" ", el("span", { class: "muted",
      text: t("ui.anbud.naermeste_frist")
        .replace("{dato}", s.naermeste_frist.slice(0, 10)) }));
  }
  if (s.utlopte_kilder > 0) {
    // ET UTLØPT SERTIFIKAT ER IKKE DOKUMENTASJON, og et utkast som
    // siterer det påstår noe kilden ikke lenger bærer.
    p.append(" ", el("strong", {
      text: t("ui.anbud.utlopte_kilder")
        .replace("{n}", String(s.utlopte_kilder)) }));
  }
  if (s.apne_funn > 0) {
    p.append(" ", el("strong", {
      text: t("ui.anbud.apne_funn")
        .replace("{n}", String(s.apne_funn)) }));
  }
  if (!s.har_profil) {
    p.append(" ", el("strong", { text: t("ui.anbud.ingen_profil") }));
  }
  if (s.vist < s.anbud) {
    p.append(" ", el("strong", {
      text: t("ui.anbud.avkortet").replace("{vist}",
                                           String(s.vist)) }));
  }
  return p;
}


export function anbudTabell(anbud, apne) {
  const tbody = el("tbody");
  for (const a of anbud) {
    const knapp = el("button", { type: "button",
      text: t("ui.anbud.knapp.apne") });
    knapp.addEventListener("click", () => apne(a));
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: a.tittel }),
      el("td", { text: a.oppdragsgiver }),
      el("td", { text: t(KILDETEKST[a.kilde] || a.kilde) }),
      el("td", { text: a.frist.slice(0, 10) }),
      el("td", { text: fristTekst(a.dogn_til_frist) }),
      el("td", { class: "tall", text: oreTekst(a.verdi_ore) }),
      el("td", { text: dekningTekst(a) }),
      el("td", { class: "tall", text: String(a.apne_funn ?? 0) }),
      el("td", { text: a.aktiv ? t("ui.anbud.ja")
                               : t("ui.anbud.nei") }),
      el("td", {}, knapp)));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.anbud.liste.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.anbud.kol.tittel") }),
        el("th", { scope: "col",
                   text: t("ui.anbud.kol.oppdragsgiver") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.kilde") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.frist") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.frist_om") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.verdi") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.dekning") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.funn") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.aktiv") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.handling") }))),
      tbody));
}


// KRAVTABELLEN. DEKKEDE OG UDEKKEDE I SAMME LISTE.
//
// Et udekket krav har tomme sitat- og kildeceller og et merke. En
// flate som filtrerte dem bort ville skjult nettopp det som må gjøres.
export function kravTabell(krav, dekk) {
  const tbody = el("tbody");
  for (const k of krav) {
    const handling = el("td", {});
    if (!k.punkt_id && dekk) {
      const knapp = el("button", { type: "button",
        text: t("ui.anbud.knapp.dekk") });
      knapp.addEventListener("click", () => dekk(k));
      handling.append(knapp);
    }
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: k.kravnummer }),
      el("td", { text: k.kravtekst }),
      el("td", { text: t(KRAVTYPETEKST[k.kravtype] || k.kravtype) }),
      // ABSOLUTT VISES SOM TEKST, ikke som en farge alene: forskjellen
      // mellom «ulempe» og «avvisning» må kunne leses (WCAG 1.4.1).
      el("td", { text: k.absolutt ? t("ui.anbud.absolutt")
                                  : t("ui.anbud.vektet") }),
      el("td", { text: k.punkt_id ? k.sitat
                                  : t("ui.anbud.udekket") }),
      el("td", { text: k.punkt_id
                   ? `${k.kildetittel} (${k.sidereferanse})` : "–" }),
      handling));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.anbud.krav.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.anbud.kol.kravnummer") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.kravtekst") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.kravtype") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.absolutt") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.sitat") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.kilde_dok") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.handling") }))),
      tbody));
}


export function kildeTabell(kilder) {
  const tbody = el("tbody");
  for (const k of kilder) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: k.tittel }),
      el("td", { text: t(DOKTEKST[k.dokumenttype]
                         || k.dokumenttype) }),
      el("td", { text: k.gyldig_til || t("ui.anbud.uten_utlop") }),
      // GYLDIG NÅ ER TRE TILSTANDER, ikke to: `null` betyr at
      // tenanten mangler profil, så vinduet ikke kan regnes.
      el("td", { text: k.gyldig_naa === null
                   || k.gyldig_naa === undefined
                   ? t("ui.anbud.ukjent")
                   : (k.gyldig_naa ? t("ui.anbud.ja")
                                   : t("ui.anbud.nei")) }),
      el("td", { text: (k.innhold_sha256 || "").slice(0, 12) }),
      el("td", { class: "tall", text: String(k.brukt_i_punkter) })));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.anbud.kilder.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.anbud.kol.dok_tittel") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.dok_type") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.gyldig_til") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.gyldig_naa") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.sum") }),
        el("th", { scope: "col", text: t("ui.anbud.kol.brukt") }))),
      tbody));
}


// PUNKTSKJEMAET. FLATENS VIKTIGSTE ELEMENT.
//
// KILDEN VELGES FRA EN NEDTREKKSLISTE, og lista inneholder BARE
// dokumenter som er gyldige nå. Et utløpt sertifikat er ikke
// dokumentasjon, og døra ville nektet det uansett — men en knapp som
// alltid feiler er verre enn en valgmulighet som ikke finnes.
//
// DET FINNES INGEN «SKRIV FRITT»-VALG. Ikke fordi vi har fjernet det,
// men fordi `utkastpunkt` i 118 ikke har en kolonne å legge en
// kildeløs påstand i. Feltet finnes ikke, så valget kan ikke finnes.
function punktSkjema(ctx, last, utkastId, krav, kilder, kvitter,
                     lukk) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const gyldige = kilder.filter((k) => k.gyldig_naa === true);
  const kilde = el("select", { id: "an-p-kilde", name: "kilde",
                               required: true });
  kilde.append(el("option", { value: "",
    text: t("ui.anbud.punkt.velg_kilde") }));
  for (const k of gyldige) {
    kilde.append(el("option", { value: k.kilde_id,
      text: `${k.tittel} — ${t(DOKTEKST[k.dokumenttype]
                               || k.dokumenttype)}` }));
  }
  const sitat = el("input", { id: "an-p-sitat", name: "sitat",
    type: "text", required: true, minlength: "4", maxlength: "4000" });
  const side = el("input", { id: "an-p-side", name: "side",
    type: "text", required: true, maxlength: "200" });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.anbud.knapp.lagre_punkt") });
  const vurder = () => {
    knapp.disabled = !kilde.value || sitat.value.trim().length < 4
      || !side.value.trim();
  };
  for (const k of [kilde, sitat, side]) {
    k.addEventListener("change", vurder);
    k.addEventListener("input", vurder);
  }
  const deler = [
    el("p", { text: t("ui.anbud.punkt.gjelder")
                .replace("{nummer}", krav.kravnummer)
                .replace("{tekst}", krav.kravtekst) }),
  ];
  if (!gyldige.length) {
    // INGEN GYLDIGE KILDER: si det, i stedet for å vise et skjema som
    // ikke kan fullføres.
    deler.push(el("p", { role: "alert",
      text: t("ui.anbud.punkt.ingen_gyldige_kilder") }));
  } else {
    deler.push(
      felt("an-p-kilde", "ui.anbud.punkt.kilde", kilde,
           "ui.anbud.punkt.kilde_hjelp"),
      felt("an-p-sitat", "ui.anbud.punkt.sitat", sitat,
           "ui.anbud.punkt.sitat_hjelp"),
      felt("an-p-side", "ui.anbud.punkt.side", side, null),
      el("div", { class: "skjema-bunn" }, knapp));
  }
  skjema.append(...deler);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.anbud.skjema.punkt_ok",
    tilbakestill: () => {
      kilde.value = ""; sitat.value = ""; side.value = "";
      knapp.disabled = true;
      if (lukk) lukk();
    },
    send: (idem) => registrerAnbudspunkt(utkastId, {
      krav_id: krav.krav_id,
      kilde_id: kilde.value,
      sitat: sitat.value.trim(),
      sidereferanse: side.value.trim(),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.anbud.punkt.tittel") }),
    el("p", { class: "muted", text: t("ui.anbud.punkt.hvorfor") }),
    skjema, utfall);
}


function profilSkjema(ctx, last, profil, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const nace = el("input", { id: "an-pr-nace", name: "nace",
    type: "text", required: true, maxlength: "500" });
  const geo = el("input", { id: "an-pr-geo", name: "geo",
    type: "text", required: true, maxlength: "500" });
  // `step: "0.01"` fordi feltet bærer ØRE-presisjon. Med `step: 1`
  // ville nettleseren avvist en lagret verdi som ikke er hele kroner.
  const minv = el("input", { id: "an-pr-min", name: "min",
    type: "number", required: true, step: "0.01", min: "0" });
  const maksv = el("input", { id: "an-pr-maks", name: "maks",
    type: "number", required: true, step: "0.01", min: "0" });
  const frist = el("input", { id: "an-pr-frist", name: "frist",
    type: "number", required: true, step: "1", min: "1", max: "365" });
  const kilde = el("input", { id: "an-pr-kilde", name: "kilde",
    type: "number", required: true, step: "1", min: "1", max: "3650" });
  if (profil) {
    nace.value = (profil.nace_koder || []).join(", ");
    geo.value = (profil.geografi || []).join(", ");
    minv.value = oreTilFelt(profil.min_verdi_ore);
    maksv.value = oreTilFelt(profil.maks_verdi_ore);
    frist.value = String(profil.frist_varsel_dogn);
    kilde.value = String(profil.kilde_gyldig_dogn);
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.anbud.knapp.lagre_profil") });
  skjema.append(
    felt("an-pr-nace", "ui.anbud.profil.nace", nace,
         "ui.anbud.profil.nace_hjelp"),
    felt("an-pr-geo", "ui.anbud.profil.geografi", geo,
         "ui.anbud.profil.geografi_hjelp"),
    felt("an-pr-min", "ui.anbud.profil.min_kr", minv, null),
    felt("an-pr-maks", "ui.anbud.profil.maks_kr", maksv, null),
    felt("an-pr-frist", "ui.anbud.profil.frist", frist,
         "ui.anbud.profil.frist_hjelp"),
    felt("an-pr-kilde", "ui.anbud.profil.kilde_gyldig", kilde,
         "ui.anbud.profil.kilde_gyldig_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  const liste = (s) => s.split(",").map((x) => x.trim())
    .filter(Boolean);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.anbud.skjema.profil_ok",
    // KRONER INN, ØRE UT — multiplikasjon på et HELTALL.
    send: (idem) => settAnbudsprofil({
      nace_koder: liste(nace.value),
      geografi: liste(geo.value),
      min_verdi_ore: feltTilOre(minv.value),
      maks_verdi_ore: feltTilOre(maksv.value),
      frist_varsel_dogn: Number(frist.value),
      kilde_gyldig_dogn: Number(kilde.value),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.anbud.profil.tittel") }), skjema, utfall);
}


function anbudSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const ref = el("input", { id: "an-n-ref", name: "ref", type: "text",
    required: true, maxlength: "200" });
  const kilde = el("select", { id: "an-n-kilde", name: "kilde",
                               required: true });
  kilde.append(el("option", { value: "",
    text: t("ui.anbud.ny.velg_kilde") }));
  for (const k of ANBUDSKILDER) {
    kilde.append(el("option", { value: k, text: t(KILDETEKST[k]) }));
  }
  const tittel = el("input", { id: "an-n-tittel", name: "tittel",
    type: "text", required: true, maxlength: "500" });
  const giver = el("input", { id: "an-n-giver", name: "giver",
    type: "text", required: true, maxlength: "500" });
  const nace = el("input", { id: "an-n-nace", name: "nace",
    type: "text", required: true, maxlength: "40" });
  const geo = el("input", { id: "an-n-geo", name: "geo", type: "text",
    required: true, maxlength: "200" });
  const verdi = el("input", { id: "an-n-verdi", name: "verdi",
    type: "number", step: "0.01", min: "0" });
  const frist = el("input", { id: "an-n-frist", name: "frist",
    type: "datetime-local", required: true });
  const knapp = el("button", { type: "submit",
    text: t("ui.anbud.knapp.registrer") });
  skjema.append(
    felt("an-n-ref", "ui.anbud.ny.ref", ref, "ui.anbud.ny.ref_hjelp"),
    felt("an-n-kilde", "ui.anbud.ny.kilde", kilde, null),
    felt("an-n-tittel", "ui.anbud.ny.tittel", tittel, null),
    felt("an-n-giver", "ui.anbud.ny.oppdragsgiver", giver, null),
    felt("an-n-nace", "ui.anbud.ny.nace", nace, null),
    felt("an-n-geo", "ui.anbud.ny.geografi", geo, null),
    // VERDIEN ER VALGFRI, og hjelpeteksten sier hvorfor: et anbud uten
    // oppgitt verdi er ikke et gratisanbud.
    felt("an-n-verdi", "ui.anbud.ny.verdi_kr", verdi,
         "ui.anbud.ny.verdi_hjelp"),
    felt("an-n-frist", "ui.anbud.ny.frist", frist,
         "ui.anbud.ny.frist_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.anbud.skjema.anbud_ok",
    tilbakestill: () => {
      ref.value = ""; kilde.value = ""; tittel.value = "";
      giver.value = ""; nace.value = ""; geo.value = "";
      verdi.value = ""; frist.value = "";
    },
    send: (idem) => registrerAnbud({
      ekstern_ref: ref.value.trim(),
      kilde: kilde.value,
      tittel: tittel.value.trim(),
      oppdragsgiver: giver.value.trim(),
      nace_kode: nace.value.trim(),
      geografi: geo.value.trim(),
      verdi_ore: feltTilOre(verdi.value),
      frist: new Date(frist.value).toISOString(),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.anbud.ny.tittel_ny") }), skjema, utfall);
}


function kildeSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const tittel = el("input", { id: "an-k-tittel", name: "tittel",
    type: "text", required: true, maxlength: "500" });
  const type = el("select", { id: "an-k-type", name: "type",
                              required: true });
  type.append(el("option", { value: "",
    text: t("ui.anbud.kilde.velg_type") }));
  for (const d of DOKUMENTTYPER) {
    type.append(el("option", { value: d, text: t(DOKTEKST[d]) }));
  }
  const gyldig = el("input", { id: "an-k-gyldig", name: "gyldig",
                               type: "date" });
  const sum = el("input", { id: "an-k-sum", name: "sum", type: "text",
    required: true, pattern: "[0-9a-fA-F]{64}", maxlength: "64" });
  const knapp = el("button", { type: "submit",
    text: t("ui.anbud.knapp.registrer_kilde") });
  skjema.append(
    felt("an-k-tittel", "ui.anbud.kilde.tittel", tittel, null),
    felt("an-k-type", "ui.anbud.kilde.type", type, null),
    felt("an-k-gyldig", "ui.anbud.kilde.gyldig_til", gyldig,
         "ui.anbud.kilde.gyldig_hjelp"),
    felt("an-k-sum", "ui.anbud.kilde.sum", sum,
         "ui.anbud.kilde.sum_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.anbud.skjema.kilde_ok",
    tilbakestill: () => {
      tittel.value = ""; type.value = ""; gyldig.value = "";
      sum.value = "";
    },
    send: (idem) => registrerKildedokument({
      tittel: tittel.value.trim(),
      dokumenttype: type.value,
      gyldig_til: gyldig.value || null,
      innhold_sha256: sum.value.trim().toLowerCase(),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.anbud.kilde.tittel_ny") }),
    el("p", { class: "muted", text: t("ui.anbud.kilde.hvorfor") }),
    skjema, utfall);
}


function kravSkjema(ctx, last, anbudId, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const nummer = el("input", { id: "an-kr-nummer", name: "nummer",
    type: "text", required: true, maxlength: "200" });
  const tekst = el("input", { id: "an-kr-tekst", name: "tekst",
    type: "text", required: true, minlength: "4", maxlength: "4000" });
  const type = el("select", { id: "an-kr-type", name: "type",
                              required: true });
  type.append(el("option", { value: "",
    text: t("ui.anbud.krav.velg_type") }));
  for (const k of KRAVTYPER) {
    type.append(el("option", { value: k, text: t(KRAVTYPETEKST[k]) }));
  }
  // ABSOLUTT HAR INGEN FORHÅNDSVALGT VERDI. Forskjellen avgjør om et
  // udekket krav er en ulempe eller en AVVISNING, og et forhåndsvalg
  // ville tatt den vurderingen fra den som leser grunnlaget.
  const absolutt = el("select", { id: "an-kr-abs", name: "abs",
                                  required: true });
  absolutt.append(
    el("option", { value: "", text: t("ui.anbud.krav.velg_absolutt") }),
    el("option", { value: "ja", text: t("ui.anbud.absolutt") }),
    el("option", { value: "nei", text: t("ui.anbud.vektet") }));
  const knapp = el("button", { type: "submit",
    text: t("ui.anbud.knapp.registrer_krav") });
  skjema.append(
    felt("an-kr-nummer", "ui.anbud.krav.nummer", nummer,
         "ui.anbud.krav.nummer_hjelp"),
    felt("an-kr-tekst", "ui.anbud.krav.tekst", tekst, null),
    felt("an-kr-type", "ui.anbud.krav.type", type, null),
    felt("an-kr-abs", "ui.anbud.krav.absolutt", absolutt,
         "ui.anbud.krav.absolutt_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.anbud.skjema.krav_ok",
    tilbakestill: () => {
      nummer.value = ""; tekst.value = ""; type.value = "";
      absolutt.value = "";
    },
    send: (idem) => registrerAnbudskrav(anbudId, {
      kravnummer: nummer.value.trim(),
      kravtekst: tekst.value.trim(),
      kravtype: type.value,
      absolutt: absolutt.value === "ja",
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.anbud.krav.tittel_ny") }), skjema, utfall);
}


// DETALJPANELET. Kravene, utkastene og dekningen står sammen, fordi
// det er dekningen som avgjør om anbudet kan besvares i det hele tatt.
function detaljpanel(ctx, last, kvitter, settApen, kilder) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  let apentKrav = null;
  const apne = async (anbud) => {
    settApen(anbud.anbud_id);
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.anbud.detalj.tittel") }),
         el("p", { class: "muted", text: t("ui.anbud.laster") }));
    let utkast = { utkast: [] };
    let krav = { krav: [] };
    try {
      const id = encodeURIComponent(anbud.anbud_id);
      utkast = await hentJson(`/v1/anbud/${id}/utkast`);
      const nyeste = (utkast.utkast || [])[0];
      const q = nyeste
        ? `?utkast=${encodeURIComponent(nyeste.utkast_id)}` : "";
      krav = await hentJson(`/v1/anbud/${id}/krav${q}`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.anbud.detalj.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.anbud.feil.generell") }));
      return;
    }
    const nyeste = (utkast.utkast || [])[0];
    const skriver = harScope(ctx, "bestilling:opprett");
    const uTbody = el("tbody");
    for (const u of utkast.utkast || []) {
      uTbody.append(el("tr", {},
        el("th", { scope: "row", text: String(u.versjon) }),
        el("td", { class: "tall", text: String(u.antall_punkter) }),
        el("td", { text: u.klar_til_gjennomgang
                     ? t("ui.anbud.klart") : t("ui.anbud.under_arbeid") }),
        el("td", { text: u.klar_av || "–" }),
        el("td", { text: u.opprettet.slice(0, 10) })));
    }
    const dekk = (skriver && nyeste && !nyeste.klar_til_gjennomgang)
      ? (k) => { apentKrav = k; apne(anbud); }
      : null;
    const deler = [
      el("h2", { text: t("ui.anbud.detalj.tittel") }),
      el("p", { class: "muted",
                text: `${anbud.tittel} · ${anbud.ekstern_ref}` }),
      kravTabell(krav.krav || [], dekk),
      el("div", { class: "tablewrap" }, el("table", {},
        el("caption", { text: t("ui.anbud.utkast.tittel") }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col", text: t("ui.anbud.kol.versjon") }),
          el("th", { scope: "col", text: t("ui.anbud.kol.punkter") }),
          el("th", { scope: "col", text: t("ui.anbud.kol.status") }),
          el("th", { scope: "col", text: t("ui.anbud.kol.klar_av") }),
          el("th", { scope: "col",
                     text: t("ui.anbud.kol.opprettet") }))),
        uTbody)),
    ];
    if (apentKrav && nyeste) {
      const fortsatt = (krav.krav || []).find(
        (x) => x.krav_id === apentKrav.krav_id && !x.punkt_id);
      if (fortsatt) {
        deler.push(punktSkjema(ctx, last, nyeste.utkast_id, fortsatt,
                               kilder, kvitter,
                               () => { apentKrav = null; }));
      } else {
        apentKrav = null;
      }
    }
    if (skriver) {
      deler.push(kravSkjema(ctx, last, anbud.anbud_id, kvitter));
      const knapper = el("div", { class: "skjema-bunn" });
      if (!nyeste || nyeste.klar_til_gjennomgang) {
        const nytt = el("button", { type: "button",
          text: t("ui.anbud.knapp.nytt_utkast") });
        nytt.addEventListener("click", async () => {
          nytt.disabled = true;
          try {
            await opprettAnbudsutkast(anbud.anbud_id);
          } catch (e) {
            nytt.disabled = false;
            if (e instanceof UautorisertFeil) {
              ctx.paaUautorisert(); return;
            }
            const m = t("ui.anbud.feil.generell");
            kvitter(m); meldLive(m);
            return;
          }
          kvitter(t("ui.anbud.skjema.utkast_ok"));
          meldLive(t("ui.anbud.skjema.utkast_ok"));
          await last();
        });
        knapper.append(nytt);
      } else {
        // «KLAR TIL GJENNOMGANG» ER IKKE «SEND INN». Knappen sier det,
        // og hjelpeteksten sier hva den ikke gjør.
        const klart = el("button", { type: "button",
          text: t("ui.anbud.knapp.merk_klart") });
        klart.addEventListener("click", async () => {
          klart.disabled = true;
          let svar;
          try {
            svar = await merkUtkastKlart(nyeste.utkast_id);
          } catch (e) {
            klart.disabled = false;
            if (e instanceof UautorisertFeil) {
              ctx.paaUautorisert(); return;
            }
            const m = e && e.status === 409
              ? t("ui.anbud.feil.udekket_absolutt")
              : t("ui.anbud.feil.generell");
            kvitter(m); meldLive(m);
            return;
          }
          // SVARET SIER HVA UTKASTET IKKE DEKKER. Den som merker
          // klart skal vite hva de sender uten.
          const n = (svar && svar.udekkede_vektede) || 0;
          const m = n > 0
            ? t("ui.anbud.skjema.klart_med_hull")
                .replace("{n}", String(n))
            : t("ui.anbud.skjema.klart_ok");
          kvitter(m); meldLive(m);
          await last();
        });
        knapper.append(klart,
          el("p", { class: "muted",
                    text: t("ui.anbud.klart_hjelp") }));
      }
      const aktiv = el("button", { type: "button",
        text: anbud.aktiv ? t("ui.anbud.knapp.deaktiver")
                          : t("ui.anbud.knapp.aktiver") });
      aktiv.addEventListener("click", async () => {
        aktiv.disabled = true;
        try {
          await settAnbudAktiv(anbud.anbud_id, !anbud.aktiv);
        } catch (e) {
          aktiv.disabled = false;
          if (e instanceof UautorisertFeil) {
            ctx.paaUautorisert(); return;
          }
          const m = t("ui.anbud.feil.generell");
          kvitter(m); meldLive(m);
          return;
        }
        kvitter(t("ui.anbud.skjema.aktiv_ok"));
        meldLive(t("ui.anbud.skjema.aktiv_ok"));
        await last();
      });
      knapper.append(aktiv);
      deler.push(knapper);
    }
    sett(node, ...deler);
  };
  return { node, apne };
}


// FUNNSEKSJONEN. Lastes for seg fordi funnene er KRYSS-ANBUD.
//
// `udekket_absolutt_krav` KAN IKKE LUKKES — døra nekter det, av samme
// grunn som M-49s bekreftede treff: et absolutt krav uten
// dokumentasjon fører til avvisning av tilbudet, og en knapp som
// gjorde den observasjonen borte ville sett ut som saksbehandling.
function funnseksjon(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.anbud.funn.tittel") }),
    el("p", { class: "muted", text: t("ui.anbud.laster") }));
  (async () => {
    let d;
    try {
      d = await hentJson("/v1/anbud/funn");
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.anbud.funn.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.anbud.feil.generell") }));
      return;
    }
    const funn = d.funn || [];
    const deler = [el("h2", { text: t("ui.anbud.funn.tittel") })];
    if (!funn.length) {
      deler.push(el("p", { class: "muted",
        text: t("ui.anbud.funn.ingen") }));
    } else {
      const tbody = el("tbody");
      for (const f of funn) {
        tbody.append(el("tr", {},
          el("th", { scope: "row", text: f.tittel }),
          el("td", { text: t(MERKE[f.funntype] || f.funntype) }),
          el("td", { text: f.frist.slice(0, 10) }),
          el("td", { class: "tall",
                     text: f.over_grense === null
                       || f.over_grense === undefined
                       ? "–" : String(f.over_grense) }),
          el("td", { text: f.detalj || "–" })));
      }
      deler.push(el("div", { class: "tablewrap" }, el("table", {},
        el("caption", { text: t("ui.anbud.funn.tittel") }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col", text: t("ui.anbud.kol.tittel") }),
          el("th", { scope: "col", text: t("ui.anbud.kol.funntype") }),
          el("th", { scope: "col", text: t("ui.anbud.kol.frist") }),
          el("th", { scope: "col", text: t("ui.anbud.kol.over") }),
          el("th", { scope: "col", text: t("ui.anbud.kol.detalj") }))),
        tbody)));
      if (harScope(ctx, "bestilling:opprett")) {
        // BARE FUNN SOM FAKTISK KAN LUKKES TILBYS.
        const lukkbare = funn.filter(
          (f) => f.funntype !== "udekket_absolutt_krav");
        if (lukkbare.length) {
          deler.push(lukkSkjema(ctx, last, lukkbare, kvitter));
        }
      }
    }
    sett(node, ...deler);
  })();
  return node;
}


function lukkSkjema(ctx, last, funn, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const valg = el("select", { id: "an-f-valg", name: "funn",
                              required: true });
  valg.append(el("option", { value: "",
    text: t("ui.anbud.funn.velg") }));
  for (const f of funn) {
    valg.append(el("option", {
      value: `${f.anbud_id}\u001f${f.funntype}`,
      text: `${f.tittel} — ${t(MERKE[f.funntype] || f.funntype)}`,
    }));
  }
  const notat = el("input", { id: "an-f-notat", name: "notat",
    type: "text", required: true, minlength: "4", maxlength: "4000" });
  const knapp = el("button", { type: "submit",
    text: t("ui.anbud.knapp.lukk_funn") });
  skjema.append(
    felt("an-f-valg", "ui.anbud.funn.hvilket", valg, null),
    felt("an-f-notat", "ui.anbud.funn.notat", notat,
         "ui.anbud.funn.notat_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.anbud.skjema.funn_lukket",
    tilbakestill: () => { valg.value = ""; notat.value = ""; },
    send: (idem) => {
      const [id, type] = valg.value.split("\u001f");
      return lukkAnbudsfunn(id, type, notat.value.trim(), idem);
    },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.anbud.funn.lukk_tittel") }), skjema,
    utfall);
}


export function visAnbud(hoved, ctx) {
  const hode = () => flateHode(t("ui.anbud.tittel"),
    t("ui.anbud.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenRad = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const settApen = (id) => { apenRad = id; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/anbud"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const anbud = d.anbud || [];
      const kilder = d.kilder || [];
      const detalj = detaljpanel(ctx, last, kvitter, settApen, kilder);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.anbud.oversikt.tittel") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT PÅ SKJERMEN.
        el("p", { class: "muted",
                  text: t("ui.anbud.oversikt.hvorfor") }));

      const liste = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.anbud.liste.tittel") }));
      if (!anbud.length) {
        liste.append(el("p", { class: "muted",
          text: t("ui.anbud.liste.ingen") }));
      } else {
        liste.append(anbudTabell(anbud, detalj.apne));
      }

      const kildeseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.anbud.kilder.tittel") }));
      if (!kilder.length) {
        kildeseksjon.append(el("p", { class: "muted",
          text: t("ui.anbud.kilder.ingen") }));
      } else {
        kildeseksjon.append(kildeTabell(kilder));
      }

      const profilseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.anbud.profil.tittel") }));
      if (!d.profil) {
        profilseksjon.append(el("p", { class: "muted",
          text: t("ui.anbud.ingen_profil") }));
      } else {
        profilseksjon.append(el("p", { class: "muted",
          text: t("ui.anbud.profil.versjon")
            .replace("{versjon}", String(d.profil.versjon)) }));
      }

      const deler = [oversikt, liste, kildeseksjon, profilseksjon,
                     detalj.node, funnseksjon(ctx, last, kvitter)];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(anbudSkjema(ctx, last, kvitter),
                   kildeSkjema(ctx, last, kvitter),
                   profilSkjema(ctx, last, d.profil, kvitter));
      }
      sett(kropp, ...deler);
      if (apenRad) {
        const rad = anbud.find((x) => x.anbud_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
