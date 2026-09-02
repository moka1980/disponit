// Compliance- og sertifiseringsagenten (M-34 v1) — KONTROLLREGISTERET.
//
// FLATENS VIKTIGSTE JOBB er å vise hvilke kontroller som er FORBI sin
// etterprøvingsfrist, og hvor mange døgn. Som TEKST, ikke bare farge
// (WCAG 1.4.1): «forbigått for 41 døgn» står som ord i sin egen celle,
// og på den forbigåtte raden står ordet i tillegg som et eget merke.
// En rød rad alene sier ingenting til den som ikke ser farge, og dette
// er den ene opplysningen som ikke tåler å bli oversett.
//
// EVIDENSEN ER EN EGEN KOLONNE, og det er hele dommen modulen hviler på:
// forskjellen mellom «vi gjør dette» og «vi kan vise at vi gjorde dette»
// er den eneste som betyr noe i en revisjon. En kontroll som står
// «oppfylt» viser henvisningen og datoen ved siden av tilstanden — aldri
// tilstanden alene.
//
// FLATEN VISER, DEN REGNER IKKE. `dogn_over_frist`, `forfaller` og
// funnlisten er regnet i BASEN, i samme skann som raden (100s lesedør),
// nettopp for at flaten ikke skal trekke to datoer fra hverandre.
//
// DET FINNES INGEN «SEND INN»-KNAPP, og fraværet er dommen: katalogen
// lover innsending til sertifiseringsorgan, v1 registrerer kontrollen.
// Undertittelen sier det, så ingen leter etter en knapp som ikke finnes.
//
// TABELLEN ER EKTE (m16-formen): <caption>, th[scope=col] på kolonnene og
// th[scope=row] på cellen som navngir raden. Uten radoverskriften mister
// en skjermleser i frist- og evidenskolonnene hvilken kontroll tallet
// gjelder. Wrapperen `.tablewrap` er sidescrollens container — uten den
// klemmer nettleseren kolonnene mot min-content.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, hentJson, markerIkkeRelevant, nyIdempotensnokkel,
  registrerEtterproving, registrerKontroll,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

const UTFALL = ["oppfylt", "avvik"];

// Fristkolonnens ORD. Ett tall inn, én setning ut — ingen utregning.
//
// ENTALL HAR SIN EGEN NØKKEL (M-21-lærdommen): locale-settet har ingen
// pluralmaskineri, og «by 1 days» ville stått på nøyaktig den raden et
// menneske leser først. Norsk «døgn» bøyes ikke og hadde klart seg;
// engelsk gjør det, og en oversettelse som er riktig bare på det ene
// språket er ikke riktig.
export function fristTekst(dogn) {
  if (typeof dogn !== "number") return "—";
  if (dogn > 0) {
    return dogn === 1
      ? t("ui.compliance.forbigatt_ett_dogn")
      : t("ui.compliance.forbigatt_for").replace("{dogn}", String(dogn));
  }
  if (dogn === 0) return t("ui.compliance.forfaller_i_dag");
  const n = Math.abs(dogn);
  return n === 1
    ? t("ui.compliance.om_ett_dogn")
    : t("ui.compliance.om_dogn").replace("{dogn}", String(n));
}

// En kontroll er FORBIGÅTT når den ikke er markert ikke-relevant og
// fristen er passert. En ikke-relevant kontroll med gammel frist er ikke
// forbigått — den er en skreven beslutning.
export function erForbigatt(k) {
  return k.status !== "ikke_relevant"
    && typeof k.dogn_over_frist === "number" && k.dogn_over_frist > 0;
}

function eierTekst(k) {
  // Visningsnavnet der IdP-en ga et; bruker-id-en ellers. En tom celle
  // ville vært det eneste svaret som ikke lar noen finne eieren.
  return k.eier_navn || k.eier_bruker_id || t("ui.compliance.ukjent_eier");
}

function rammeverkTekst(k) {
  return k.rammeverk_versjon
    ? `${k.rammeverk} ${k.rammeverk_versjon}` : k.rammeverk;
}

function kontrollrad(k, ctx, apneEtterproving, apneIkkeRelevant) {
  const rad = el("tr", {});
  // Kravreferansen NAVNGIR raden — det er identiteten et menneske leser
  // registeret etter («A.8.16»), og den skal BRYTE på lange numre:
  // `celle-id` står på <th>, ikke på et <span> inni, fordi `max-width`
  // ikke gjør noe på et inline-element.
  rad.append(el("th", { scope: "row", class: "celle-id",
                        text: k.krav_ref }));
  rad.append(el("td", { text: rammeverkTekst(k) }));
  // `celle-tekst` på selve <td>-en, av samme grunn.
  rad.append(el("td", { class: "celle-tekst", text: k.beskrivelse }));

  const eiercelle = el("td", {}, el("span", { text: eierTekst(k) }));
  if (k.eier_aktiv === false) {
    // EIEREN HAR SLUTTET. Sveipen reiser funnet i natt, men flaten skal
    // ikke vente på den for å si det — og det står som ORD, ikke som en
    // blek celle.
    eiercelle.append(" ", el("strong", { class: "merke",
      text: t("ui.compliance.merke_uten_eier") }));
  }
  rad.append(eiercelle);

  // EN IKKE-RELEVANT KONTROLL HAR INGEN LØPENDE FRIST. `m34_kontrollbilde`
  // regner `dogn_over_frist` for HVER rad — også de som er formelt vurdert
  // ut — så uten dette sa cellen «forbigått for 12 døgn» om en beslutning
  // som står skrevet ned. `erForbigatt` holdt allerede merket borte;
  // setningen ved siden av motsa det.
  const fristcelle = el("td", {},
    el("span", { text: k.status === "ikke_relevant"
      ? fristTekst(null) : fristTekst(k.dogn_over_frist) }));
  fristcelle.append(" ", el("span", { class: "muted",
    text: t("ui.compliance.intervall").replace(
      "{dogn}", String(k.etterproving_dogn)) }));
  if (erForbigatt(k)) {
    // MERKET ER TEKST. Dette er flatens viktigste opplysning.
    fristcelle.append(" ", el("strong", { class: "merke",
      text: t("ui.compliance.merke_forbigatt") }));
  }
  rad.append(fristcelle);

  // EVIDENSKOLONNEN. Henvisning OG dato, eller den ærlige setningen om at
  // ingen finnes. En tom celle her ville sett ut som «vi har ikke fylt
  // ut feltet», mens sannheten er «vi kan ikke vise at vi gjorde det».
  const evidens = el("td", { class: "celle-id" });
  if (k.evidens_ref && k.sist_etterprovd) {
    sett(evidens, el("span", { text: k.evidens_ref }), " ",
      el("span", { class: "muted", text: k.sist_etterprovd }));
  } else {
    sett(evidens, el("span", { class: "muted",
      text: t("ui.compliance.ingen_evidens") }));
  }
  rad.append(evidens);

  const tilstand = el("td", {},
    el("span", { text: t(`ui.compliance.status.${k.status}`, k.status) }));
  if (k.status === "ikke_relevant" && k.ikke_relevant_begrunnelse) {
    tilstand.append(el("p", { class: "muted celle-tekst",
      text: k.ikke_relevant_begrunnelse }));
  }
  if (k.siste_utfall === "avvik" && k.siste_avvik) {
    tilstand.append(el("p", { class: "muted celle-tekst",
      text: k.siste_avvik }));
  }
  rad.append(tilstand);

  const handling = el("td", {});
  if (harScope(ctx, "bestilling:opprett")) {
    const etterprov = el("button", { type: "button",
      text: t("ui.compliance.knapp.etterprov") });
    etterprov.addEventListener("click", () => apneEtterproving(k));
    handling.append(etterprov);
    // «Ikke relevant» tilbys bare der den betyr noe: døren avviser en
    // kontroll som alt står slik, og en knapp som alltid feiler er en
    // løgn om hva systemet kan.
    if (k.status !== "ikke_relevant") {
      const bort = el("button", { type: "button",
        text: t("ui.compliance.knapp.ikke_relevant") });
      bort.addEventListener("click", () => apneIkkeRelevant(k));
      handling.append(" ", bort);
    }
  }
  rad.append(handling);
  return rad;
}

function tabell(kontroller, ctx, apneEtterproving, apneIkkeRelevant) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.compliance.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.compliance.kolonne.krav") }),
    el("th", { scope: "col", text: t("ui.compliance.kolonne.rammeverk") }),
    el("th", { scope: "col",
      text: t("ui.compliance.kolonne.beskrivelse") }),
    el("th", { scope: "col", text: t("ui.compliance.kolonne.eier") }),
    el("th", { scope: "col",
      text: t("ui.compliance.kolonne.etterproving") }),
    el("th", { scope: "col", text: t("ui.compliance.kolonne.evidens") }),
    el("th", { scope: "col", text: t("ui.compliance.kolonne.status") }),
    el("th", { scope: "col", text: t("ui.compliance.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const k of kontroller) {
    tbody.append(kontrollrad(k, ctx, apneEtterproving, apneIkkeRelevant));
  }
  tb.append(tbody);
  // `.tablewrap` er sidescrollens container — uten den er tabellen bundet
  // til `width: 100%` og klemmer kolonnene mot min-content i stedet for å
  // kunne bli bredere (se komponenter.css).
  return el("div", { class: "tablewrap" }, tb);
}

// Registreringsskjemaet. EIEREN VELGES EKSPLISITT — feltet er påkrevd og
// har ingen forhåndsutfylt verdi. En flate som stille satte innloggeren
// som eier ville gjort «kontroller uten eier» sann på papiret og falsk i
// praksis: den som skriver ned en kontroll er ofte ikke den som skal
// utføre den.
function registrerSkjema(ctx, last) {
  const boks = el("div", { class: "skjemaboks" });
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const rammeverk = el("input", { id: "kontroll-rammeverk",
    name: "rammeverk", type: "text", required: true, maxlength: 200 });
  const versjon = el("input", { id: "kontroll-versjon",
    name: "rammeverk_versjon", type: "text", maxlength: 60 });
  const krav = el("input", { id: "kontroll-krav", name: "krav_ref",
    type: "text", required: true, maxlength: 200 });
  const beskrivelse = el("input", { id: "kontroll-beskrivelse",
    name: "beskrivelse", type: "text", required: true, maxlength: 2000 });
  const eier = el("input", { id: "kontroll-eier", name: "eier_bruker_id",
    type: "text", required: true, maxlength: 128 });
  const dogn = el("input", { id: "kontroll-dogn",
    name: "etterproving_dogn", type: "number", required: true,
    min: "1", max: "3650", value: "365" });
  const knapp = el("button", { type: "submit",
    text: t("ui.compliance.knapp.registrer") });
  // ÉN GRUPPE PER FELT: etikett, kontroll og hjelpetekst hører sammen —
  // ligger de som løse søsken, sprer rutenettet dem i hver sin celle, og
  // etiketten mister den visuelle koblingen til feltet sitt uansett hva
  // `for`-attributtet sier.
  const felt = (id, nokkel, kontroll, hjelp) => el("div", { class: "felt" },
    el("label", { for: id, text: t(`ui.compliance.skjema.${nokkel}`) }),
    kontroll,
    hjelp ? el("p", { class: "muted",
      text: t(`ui.compliance.skjema.${hjelp}`) }) : null);

  skjema.append(
    felt("kontroll-rammeverk", "rammeverk", rammeverk, "rammeverkhjelp"),
    felt("kontroll-versjon", "versjon", versjon),
    felt("kontroll-krav", "krav", krav, "kravhjelp"),
    felt("kontroll-beskrivelse", "beskrivelse", beskrivelse),
    felt("kontroll-eier", "eier", eier, "eierhjelp"),
    felt("kontroll-dogn", "dogn", dogn, "dognhjelp"),
    // Knappen står i en egen bunnrad over hele bredden: en send-knapp
    // inne i en feltkolonne leses som «send inn DETTE feltet».
    el("div", { class: "skjema-bunn" }, knapp,
      el("p", { class: "muted",
        text: t("ui.compliance.skjema.starthjelp") })));

  // Én nøkkel per intensjon (PR-014 R1): nullstilles når innholdet endres,
  // og ved 4xx — et avvist forsøk har FORBRUKT nøkkelen, og en rettet
  // kontroll er en ny intensjon som skal ha sin egen.
  let idem = null;
  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled) return;
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    try {
      await registrerKontroll({
        rammeverk: rammeverk.value,
        rammeverk_versjon: versjon.value || null,
        krav_ref: krav.value,
        beskrivelse: beskrivelse.value,
        eier_bruker_id: eier.value,
        etterproving_dogn: Number(dogn.value),
      }, idem);
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.compliance.feil.tilstand")
          : t("ui.compliance.feil.generell") }));
      return;
    }
    krav.value = ""; beskrivelse.value = ""; eier.value = "";
    idem = null;
    knapp.disabled = false;
    meldLive(t("ui.compliance.skjema.ok"));
    sett(utfall, el("span", { text: t("ui.compliance.skjema.ok") }));
    last();
  });
  boks.append(el("h3", { text: t("ui.compliance.skjema.tittel_seksjon") }),
    skjema, utfall);
  return boks;
}

// ETTERPRØVINGSDIALOGEN. EVIDENSHENVISNINGEN OG DATOEN ER PÅKREVDE, og
// hjelpeteksten sier HVORFOR — ikke «feltet er påkrevd», men «en kontroll
// er oppfylt bare med en skreven henvisning og en dato». Det er dommen
// modulen er bygget på, og et menneske som får den forklart én gang
// slutter å oppleve den som en irritasjon. Serveren feller dommen uansett
// (døren, vakten og CHECK-en i 100); skjemaet er ergonomi, ikke
// sikkerhet.
function etterprovingsdialog(ctx, last) {
  // UTFALLET LIGGER UTENFOR DET SOM SKJULES: boksen lukker seg når
  // etterprøvingen er registrert — og lå live-regionen inne i den, ble
  // bekreftelsen både usynlig og uannonsert i nøyaktig det øyeblikket den
  // hadde noe å si. Det er `innhold` som skjules, aldri `boks`.
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;
  let idem = null;

  const beskrivelse = el("p", { class: "muted" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const dato = el("input", { id: "etterproving-dato", name: "utfort",
    type: "date", required: true });
  const utforer = el("input", { id: "etterproving-utforer",
    name: "utfort_av_bruker_id", type: "text", required: true,
    maxlength: 128 });
  const evidens = el("input", { id: "etterproving-evidens",
    name: "evidens_ref", type: "text", required: true, maxlength: 500 });
  const valg = el("select", { id: "etterproving-utfall", name: "utfall" });
  for (const u of UTFALL) {
    valg.append(el("option", { value: u,
      text: t(`ui.compliance.utfall.${u}`) }));
  }
  const avvik = el("input", { id: "etterproving-avvik",
    name: "avviksbeskrivelse", type: "text", maxlength: 4000 });
  const knapp = el("button", { type: "submit",
    text: t("ui.compliance.knapp.lagre_etterproving") });
  const felt = (id, nokkel, kontroll, hjelp) => el("div", { class: "felt" },
    el("label", { for: id, text: t(`ui.compliance.dialog.${nokkel}`) }),
    kontroll,
    hjelp ? el("p", { class: "muted",
      text: t(`ui.compliance.dialog.${hjelp}`) }) : null);
  const avviksfelt = felt("etterproving-avvik", "avvik", avvik,
                          "avvikhjelp");
  skjema.append(
    felt("etterproving-dato", "dato", dato, "datohjelp"),
    felt("etterproving-utforer", "utforer", utforer),
    felt("etterproving-evidens", "evidens", evidens, "evidenshjelp"),
    felt("etterproving-utfall", "utfall", valg),
    avviksfelt,
    el("div", { class: "skjema-bunn" }, knapp));
  innhold.append(el("h3", { text: t("ui.compliance.dialog.tittel") }),
    beskrivelse, skjema);
  boks.append(innhold, utfall);
  innhold.hidden = true;

  // Avviksfeltet finnes bare når utfallet er `avvik`: et alltid synlig
  // felt som bare noen ganger er påkrevd er en felle, og døren avviser
  // uansett et avvik uten beskrivelse.
  function visAvvik() {
    avviksfelt.hidden = valg.value !== "avvik";
    if (valg.value === "avvik") avvik.setAttribute("required", "");
    else avvik.removeAttribute("required");
  }
  valg.addEventListener("change", visAvvik);

  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled || !gjeldende) return;
    // Flatens EGEN nei, og den sier hvorfor. Serveren sier det samme
    // (døren avviser en tom henvisning), men et menneske skal ikke måtte
    // vente på et nettverkskall for å få vite at feltet betyr noe.
    if (!evidens.value.trim() || !dato.value) {
      sett(utfall, el("span", { role: "alert",
        text: t("ui.compliance.feil.evidens_kreves") }));
      return;
    }
    if (valg.value === "avvik" && !avvik.value.trim()) {
      sett(utfall, el("span", { role: "alert",
        text: t("ui.compliance.feil.avvik_kreves") }));
      return;
    }
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    try {
      await registrerEtterproving(gjeldende.kontroll_id, {
        utfort: dato.value,
        utfort_av_bruker_id: utforer.value,
        evidens_ref: evidens.value,
        utfall: valg.value,
        avviksbeskrivelse: valg.value === "avvik" ? avvik.value : null,
      }, idem);
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.compliance.feil.tilstand")
          : t("ui.compliance.feil.generell") }));
      return;
    }
    evidens.value = ""; avvik.value = "";
    idem = null;
    knapp.disabled = false;
    innhold.hidden = true;
    // BEKREFTELSEN MELDES I APPENS EGEN LIVE-REGION, ikke bare i boksen:
    // `last()` tegner hele flaten på nytt, så en melding som bare sto her
    // ville rukket å bli skrevet og revet bort i samme tikk.
    meldLive(t("ui.compliance.dialog.ok"));
    sett(utfall, el("span", { text: t("ui.compliance.dialog.ok") }));
    last();
  });

  return {
    node: boks,
    apne(kontroll) {
      gjeldende = kontroll;
      idem = null;
      // ALLE feltene nullstilles, ikke bare de som ble sendt sist
      // (CodeRabbit). Dialogen gjenbrukes for hver rad, og en dato eller
      // en utfører som ble stående igjen fra FORRIGE kontroll ville blitt
      // bokført på DENNE — en etterprøving med feil dato er verre enn
      // ingen, fordi den ser riktig ut i registeret.
      dato.value = ""; utforer.value = "";
      evidens.value = ""; avvik.value = ""; valg.value = "oppfylt";
      visAvvik();
      beskrivelse.textContent = `${kontroll.krav_ref} — `
        + `${kontroll.beskrivelse}`;
      sett(utfall);
      innhold.hidden = false;
      evidens.focus();
    },
  };
}

// «Ikke relevant»-dialogen. EN BESLUTNING, IKKE ET FRAVÆR — og derfor
// koster den en skreven begrunnelse. Uten den ville det vært en gratis vei
// ut av enhver kontroll, og registeret en liste over ting man kan klikke
// bort.
function ikkeRelevantDialog(ctx, last) {
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;
  let idem = null;

  const beskrivelse = el("p", { class: "muted" });
  const skjema = el("form", { class: "kv-skjema" });
  const felt = el("input", { id: "kontroll-begrunnelse",
    name: "begrunnelse", type: "text", required: true, maxlength: 2000 });
  const knapp = el("button", { type: "submit",
    text: t("ui.compliance.knapp.ikke_relevant") });
  skjema.append(
    el("label", { for: "kontroll-begrunnelse",
      text: t("ui.compliance.dialog.begrunnelse") }),
    felt,
    el("p", { class: "muted",
      text: t("ui.compliance.dialog.begrunnelsehjelp") }),
    knapp);
  innhold.append(
    el("h3", { text: t("ui.compliance.dialog.ikke_relevant_tittel") }),
    beskrivelse, skjema);
  boks.append(innhold, utfall);
  innhold.hidden = true;

  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled || !gjeldende) return;
    if (!felt.value.trim()) {
      sett(utfall, el("span", { role: "alert",
        text: t("ui.compliance.feil.begrunnelse_kreves") }));
      return;
    }
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    try {
      await markerIkkeRelevant(gjeldende.kontroll_id, felt.value, idem);
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.compliance.feil.tilstand")
          : t("ui.compliance.feil.generell") }));
      return;
    }
    felt.value = "";
    idem = null;
    knapp.disabled = false;
    innhold.hidden = true;
    meldLive(t("ui.compliance.dialog.ikke_relevant_ok"));
    sett(utfall, el("span", {
      text: t("ui.compliance.dialog.ikke_relevant_ok") }));
    last();
  });

  return {
    node: boks,
    apne(kontroll) {
      gjeldende = kontroll;
      idem = null;
      felt.value = "";
      beskrivelse.textContent = `${kontroll.krav_ref} — `
        + `${kontroll.beskrivelse}`;
      sett(utfall);
      innhold.hidden = false;
      felt.focus();
    },
  };
}

// Sammendraget over tabellen. ETT TALL SOM ER VERDT Å LESE FØRST: hvor
// mange kontroller er forbi sin etterprøvingsfrist. Setningen sier det
// som ord, ikke som et tall i en boks — «3 av 17 kontroller er forbigått»
// er en påstand et menneske kan handle på.
function sammendrag(kontroller) {
  const forbigatt = kontroller.filter(erForbigatt).length;
  const utenEvidens = kontroller.filter(
    (k) => k.status === "oppfylt" && !k.evidens_ref).length;
  const p = el("p", {
    text: t("ui.compliance.sammendrag")
      .replace("{forbigatt}", String(forbigatt))
      .replace("{antall}", String(kontroller.length)) });
  if (utenEvidens) {
    // Skal aldri kunne skje (CHECK-en i 100 gjør det urepresenterbart) —
    // og nettopp derfor står setningen her: ser noen den, har noe skrevet
    // utenom vakten, og da er DET beskjeden.
    p.append(" ", el("strong", { text: t("ui.compliance.uten_evidens")
      .replace("{antall}", String(utenEvidens)) }));
  }
  return p;
}

export function visCompliance(hoved, ctx) {
  const hode = () => flateHode(t("ui.compliance.tittel"),
    t("ui.compliance.undertittel"));
  sett(hoved, ...hode());
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kropp);
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/compliance"),
    (d) => {
      sett(hoved, ...hode(), kropp);
      const kontroller = d.kontroller || [];
      const seksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.compliance.liste.tittel") }));
      const etterprov = etterprovingsdialog(ctx, last);
      const bort = ikkeRelevantDialog(ctx, last);
      if (!kontroller.length) {
        // ÆRLIG TOMTILSTAND: et tomt kontrollregister er ikke «vi har
        // ingen krav» — det er «ingen har skrevet ned hvilke krav vi
        // etterlever», og setningen sier nettopp det.
        seksjon.append(el("p", { class: "muted",
          text: t("ui.compliance.liste.ingen") }));
      } else {
        seksjon.append(sammendrag(kontroller),
          tabell(kontroller, ctx, etterprov.apne, bort.apne));
      }
      const deler = [seksjon];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(etterprov.node, bort.node, registrerSkjema(ctx, last));
      }
      sett(kropp, ...deler);
    });
  last();
}
