// Personvern- og datasubjektagenten (M-30 v1) — FORESPØRSELSREGISTERET
// som liste.
//
// FLATENS VIKTIGSTE JOBB ER Å VISE HVOR MANGE DAGER SOM GJENSTÅR, og å
// markere de oversittede tydelig — SOM TEKST, ikke bare som farge (WCAG
// 1.4.1). «Fristen er oversittet» står som ord i sin egen celle, og på
// den oversittede raden i tillegg som et eget merke, fordi det er den
// ene opplysningen som ikke tåler å bli oversett: en oversittet
// innsynsforespørsel er et LOVBRUDD, ikke en forsinkelse.
//
// FLATEN VISER, DEN REGNER IKKE. `dogn_til_frist` er regnet i BASEN, i
// samme skann som raden (099s lesedør), nettopp for at flaten ikke skal
// trekke to datoer fra hverandre. Et tall som regnes to steder blir to
// ulike tall den dagen tidssonen spriker — og her ville det tallet vært
// forskjellen mellom «i rute» og «lovbrudd».
//
// DEN GJELDENDE FRISTEN ER `forlenget_til` NÅR DEN FINNES. Flaten viser
// den, og sier med et eget merke AT den er forlenget: en frist som stille
// hadde flyttet seg ville skjult nøyaktig den handlingen art. 12 nr. 3
// krever at noen begrunner.
//
// SUBJEKTET ER EN REFERANSE, ALDRI ET NAVN. Registeret bærer en
// henvisning (saksnummer, arkivreferanse), ikke personopplysninger i
// klartekst — et register over dem som har krevd innsyn i sine
// personopplysninger er selv et av husets mest sensitive lagre, og
// skjemateksten sier det til den som fyller det ut.
//
// FLATEN SLETTER INGENTING, OG DEN TILBYR INGEN KNAPP SOM GJØR DET.
// Sletting eies av M-4s retensjonsregnskap; «Registrer svar» skriver ned
// at forespørselen er besvart, og utførelsen gjøres der lageret eies.
// Hjelpeteksten i dialogen sier det, fordi et menneske som tror knappen
// sletter, ville sluttet å gjøre jobben knappen dokumenterer.
//
// TABELLEN ER EKTE (m16-formen): <caption>, th[scope=col] på kolonnene og
// th[scope=row] på cellen som navngir raden. Uten radoverskriften mister
// en skjermleser i frist- og eierkolonnene hvilken sak tallet gjelder.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, avvisPersonvernsak, besvarPersonvernsak,
  forlengPersonvernfrist, hentJson, nyIdempotensnokkel,
  registrerPersonvernsak,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// GDPR-rettighetene, i lovens rekkefølge (art. 15-21). Speiler det
// lukkede settet i 099 — flaten tilbyr aldri en type registeret nekter.
const SAKSTYPER = ["innsyn", "retting", "sletting", "begrensning",
  "portabilitet", "innsigelse"];

// Fristkolonnens ORD. Ett tall inn, én setning ut — ingen utregning.
//
// ENTALL HAR SIN EGEN NØKKEL (M-21s CodeRabbit-lærdom): locale-settet
// har ingen pluralmaskineri, og «1 days left» ville stått på nøyaktig den
// raden et menneske leser først. Norsk «døgn» bøyes ikke og hadde klart
// seg; engelsk gjør det, og en oversettelse som er riktig bare på det ene
// språket er ikke riktig.
function fristTekst(dogn) {
  if (typeof dogn !== "number") return "—";
  if (dogn < 0) {
    const n = Math.abs(dogn);
    return n === 1
      ? t("ui.personvern.oversittet_for_ett_dogn")
      : t("ui.personvern.oversittet_for").replace("{dogn}", String(n));
  }
  if (dogn === 0) return t("ui.personvern.forfaller_i_dag");
  if (dogn === 1) return t("ui.personvern.om_ett_dogn");
  return t("ui.personvern.om_dogn").replace("{dogn}", String(dogn));
}

// En sak er OVERSITTET når den fortsatt er åpen og den gjeldende fristen
// er passert. En besvart sak med en gammel frist er ikke oversittet — den
// er gjort, og et merke på den ville gjort merket til støy.
function erOversittet(s) {
  return s.status === "apen" && typeof s.dogn_til_frist === "number"
    && s.dogn_til_frist < 0;
}

function eierTekst(s) {
  // Visningsnavnet der IdP-en ga et; bruker-id-en ellers. En tom celle
  // ville vært det eneste svaret som ikke lar noen finne eieren.
  return s.eier_navn || s.eier_bruker_id || t("ui.personvern.ukjent_eier");
}

function sakrad(s, ctx, apneDialog) {
  const rad = el("tr", {});
  // Saksreferansen NAVNGIR raden — det er den identiteten et menneske
  // leser tabellen etter. `.celle-tekst` står på selve <th>-en: en
  // `max-width` gjør ingenting på et inline-element.
  rad.append(el("th", { scope: "row", class: "celle-tekst",
                       text: s.subjekt_ref }));
  rad.append(el("td", { class: "celle-tekst",
    text: t(`ui.personvern.type.${s.type}`, s.type) }));

  const fristcelle = el("td", { class: "celle-tekst" });
  // DATOEN SOM MASKINLESBAR VERDI ved siden av den formaterte. Fristen
  // er en DAG, ikke et tidspunkt — loven teller måneder — så den står
  // som en ren <time datetime="YYYY-MM-DD">, uten klokkeslett.
  fristcelle.append(
    el("time", { datetime: s.gjeldende_frist, text: s.gjeldende_frist }),
    " ",
    el("span", { class: "muted", text: fristTekst(s.dogn_til_frist) }));
  if (s.forlenget_til) {
    // AT FRISTEN ER FORLENGET SKAL SYNES. En frist som stille hadde
    // flyttet seg ville skjult nøyaktig den handlingen loven krever at
    // noen begrunner.
    fristcelle.append(" ", el("span", { class: "muted",
      text: t("ui.personvern.forlenget") }));
  }
  if (erOversittet(s)) {
    // MERKET ER TEKST. En rød rad alene sier ingenting til den som ikke
    // ser farge, og «oversittet» er den ene opplysningen her som er et
    // lovbrudd og ikke en forsinkelse.
    fristcelle.append(" ", el("strong", { class: "merke",
      text: t("ui.personvern.merke_oversittet") }));
  }
  rad.append(fristcelle);

  rad.append(el("td", { class: "celle-tekst", text: eierTekst(s) }));

  // LAGRENE SAKEN DEKKER — koblingen mot M-4, og det som gjør at noen
  // kan etterprøve om svaret var fullstendig. `.celle-id` fordi
  // lager-id-ene er identifikatorer som må kunne brytes (`overflow-wrap:
  // anywhere`); uten den blir sidescrollen like bred som den lengste
  // strengen.
  const lagre = Array.isArray(s.lager_id) ? s.lager_id : [];
  rad.append(el("td", { class: "celle-id",
    text: lagre.length ? lagre.join(", ")
                       : t("ui.personvern.ingen_lagre") }));

  const statuscelle = el("td", { class: "celle-tekst" });
  statuscelle.append(el("span", {
    text: t(`ui.personvern.status.${s.status}`, s.status) }));
  // De ÅPNE FUNNENE står på raden, ikke bare i en journal. Et funn ingen
  // kan se er ikke et funn — det er en rad.
  for (const f of (Array.isArray(s.apne_funn) ? s.apne_funn : [])) {
    statuscelle.append(" ", el("span", { class: "muted",
      text: t(`ui.personvern.funn.${f}`, f) }));
  }
  rad.append(statuscelle);

  const handling = el("td", {});
  if (s.status === "apen" && harScope(ctx, "bestilling:opprett")) {
    // Knappene finnes bare på ÅPNE saker: dørene i 099 avviser en sak
    // som alt er besvart eller avvist, og en knapp som alltid feiler er
    // en løgn om hva systemet kan.
    for (const [modus, nokkel] of [["svar", "ui.personvern.knapp.svar"],
                                   ["avvis", "ui.personvern.knapp.avvis"],
                                   ["forleng",
                                    "ui.personvern.knapp.forleng"]]) {
      const knapp = el("button", { type: "button", text: t(nokkel) });
      knapp.addEventListener("click", () => apneDialog(s, modus));
      handling.append(knapp, " ");
    }
  }
  rad.append(handling);
  return rad;
}

function tabell(saker, ctx, apneDialog) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.personvern.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.personvern.kolonne.subjekt") }),
    el("th", { scope: "col", text: t("ui.personvern.kolonne.type") }),
    el("th", { scope: "col", text: t("ui.personvern.kolonne.frist") }),
    el("th", { scope: "col", text: t("ui.personvern.kolonne.eier") }),
    el("th", { scope: "col", text: t("ui.personvern.kolonne.lagre") }),
    el("th", { scope: "col", text: t("ui.personvern.kolonne.status") }),
    el("th", { scope: "col",
      text: t("ui.personvern.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const s of saker) tbody.append(sakrad(s, ctx, apneDialog));
  tb.append(tbody);
  // `.tablewrap` er sidescrollens container — uten den er tabellen
  // bundet til `width: 100%` og klemmer kolonnene mot min-content i
  // stedet for å kunne bli bredere (se komponenter.css).
  return el("div", { class: "tablewrap" }, tb);
}

// Registreringsskjemaet. EIEREN VELGES EKSPLISITT — feltet er påkrevd og
// har ingen forhåndsutfylt verdi. En flate som stille satte innloggeren
// som eier ville gjort «forespørsler uten eier» sann på papiret og falsk
// i praksis: den som tar imot forespørselen er ofte ikke den som skal
// besvare den.
//
// FRISTEN ER IKKE ET FELT. Registeret regner den av `mottatt` (én måned,
// art. 12 nr. 3). En frist noen kunne skrive fritt ville gjort
// «oversittet» til en mening i stedet for et faktum.
function registrerSkjema(ctx, last) {
  const boks = el("div", { class: "skjemaboks" });
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const type = el("select", { id: "pv-type", name: "type" });
  for (const s of SAKSTYPER) {
    type.append(el("option", { value: s,
      text: t(`ui.personvern.type.${s}`) }));
  }
  const subjekt = el("input", { id: "pv-subjekt", name: "subjekt_ref",
    type: "text", required: true, maxlength: 200 });
  const eier = el("input", { id: "pv-eier", name: "eier_bruker_id",
    type: "text", required: true, maxlength: 128 });
  const mottatt = el("input", { id: "pv-mottatt", name: "mottatt",
    type: "date", required: true });
  const lagre = el("input", { id: "pv-lagre", name: "lager_id",
    type: "text", maxlength: 2000 });
  const knapp = el("button", { type: "submit",
    text: t("ui.personvern.knapp.registrer") });
  // ÉN GRUPPE PER FELT: etikett, kontroll og hjelpetekst hører sammen —
  // ligger de som løse søsken, sprer rutenettet dem i hver sin celle, og
  // etiketten mister den visuelle koblingen til feltet sitt uansett hva
  // `for`-attributtet sier.
  const felt = (id, nokkel, kontroll, hjelp) => el("div", { class: "felt" },
    el("label", { for: id, text: t(`ui.personvern.skjema.${nokkel}`) }),
    kontroll,
    hjelp ? el("p", { class: "muted",
      text: t(`ui.personvern.skjema.${hjelp}`) }) : null);

  skjema.append(
    felt("pv-type", "type", type),
    felt("pv-subjekt", "subjekt", subjekt, "subjekthjelp"),
    felt("pv-eier", "eier", eier, "eierhjelp"),
    felt("pv-mottatt", "mottatt", mottatt, "mottatthjelp"),
    felt("pv-lagre", "lagre", lagre, "lagrehjelp"),
    // Knappen står i en egen bunnrad over hele bredden: en send-knapp
    // inne i en feltkolonne leses som «send inn DETTE feltet».
    el("div", { class: "skjema-bunn" }, knapp));

  // Én nøkkel per intensjon (PR-014 R1): nullstilles når innholdet
  // endres, og ved 4xx — et avvist forsøk har FORBRUKT nøkkelen, og en
  // rettet forespørsel er en ny intensjon som skal ha sin egen.
  let idem = null;
  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled) return;
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    try {
      await registrerPersonvernsak({
        type: type.value,
        subjekt_ref: subjekt.value,
        eier_bruker_id: eier.value,
        // `<input type=date>` gir «YYYY-MM-DD», som er nøyaktig formen
        // basen tar imot: fristen er en DAG, og en tidssone som flyttet
        // den et halvt døgn ville vært en presisjon som ikke finnes i
        // hjemmelen.
        mottatt: mottatt.value,
        lager_id: lagre.value.split(",").map((l) => l.trim())
          .filter((l) => l.length),
      }, idem);
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.personvern.feil.tilstand")
          : t("ui.personvern.feil.generell") }));
      return;
    }
    subjekt.value = ""; eier.value = ""; mottatt.value = "";
    lagre.value = "";
    idem = null;
    knapp.disabled = false;
    meldLive(t("ui.personvern.skjema.ok"));
    sett(utfall, el("span", { text: t("ui.personvern.skjema.ok") }));
    last();
  });
  boks.append(el("h3", { text: t("ui.personvern.skjema.tittel_seksjon") }),
    skjema, utfall);
  return boks;
}

// Svar-/avslags-/forlengelsesdialogen. ÉN boks med tre former, fordi de
// er samme handling for brukeren («jeg gjør noe med denne saken») og tre
// helt ulike påstander for registeret: et svar sier at forespørselen ER
// BESVART, et avslag at den IKKE ETTERKOMMES, en forlengelse at den
// trenger mer tid MOT en årsak. Teksten skiller dem, og alle tre feltene
// er påkrevde.
function dialogboks(ctx, last) {
  // UTFALLET LIGGER UTENFOR DET SOM SKJULES. Boksen lukker seg når
  // handlingen er registrert — og lå live-regionen inne i den, ble
  // bekreftelsen både usynlig og uannonsert i nøyaktig det øyeblikket
  // den hadde noe å si. Det er `innhold` som skjules, aldri `boks`.
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;
  let modus = "svar";
  let idem = null;

  const overskrift = el("h3", { text: t("ui.personvern.dialog.tittel") });
  const beskrivelse = el("p", { class: "muted" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const etikett = el("label", { for: "pv-referanse" });
  const felt = el("input", { id: "pv-referanse", name: "referanse",
    type: "text", required: true, maxlength: 2000 });
  const hjelp = el("p", { class: "muted" });
  const datoetikett = el("label", { for: "pv-nyfrist",
    text: t("ui.personvern.dialog.forlengdato") });
  const dato = el("input", { id: "pv-nyfrist", name: "forlenget_til",
    type: "date" });
  const datofelt = el("div", { class: "felt" }, datoetikett, dato);
  const knapp = el("button", { type: "submit" });
  skjema.append(
    el("div", { class: "felt" }, etikett, felt, hjelp),
    datofelt,
    el("div", { class: "skjema-bunn" }, knapp));
  innhold.append(overskrift, beskrivelse, skjema);
  boks.append(innhold, utfall);
  innhold.hidden = true;

  function tegn() {
    beskrivelse.textContent = gjeldende ? gjeldende.subjekt_ref : "";
    etikett.textContent = t(`ui.personvern.dialog.${
      modus === "svar" ? "svar" : modus === "avvis" ? "avvis" : "forleng"}`);
    // HJELPETEKSTEN ER BEGRUNNELSEN, ikke en gjentakelse av etiketten.
    // Den sier hvorfor feltet ikke kan stå tomt — og for svaret sier den
    // dessuten hva knappen IKKE gjør: den sletter ingenting.
    hjelp.textContent = t(`ui.personvern.dialog.${
      modus === "svar" ? "svarhjelp"
        : modus === "avvis" ? "avvishjelp" : "forlenghjelp"}`);
    knapp.textContent = t(`ui.personvern.knapp.${modus}`);
    // Datofeltet hører BARE forlengelsen til. Et felt som står synlig i
    // to av tre former og betyr noe i én, er et felt folk fyller ut i
    // feil form.
    datofelt.hidden = modus !== "forleng";
    dato.required = modus === "forleng";
  }

  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled || !gjeldende) return;
    if (!felt.value.trim()) {
      // Flatens EGEN nei, og den sier hvorfor. Serveren sier det samme
      // (dørene avviser en tom referanse og en tom begrunnelse), men et
      // menneske skal ikke måtte vente på et nettverkskall for å få vite
      // at feltet betyr noe.
      sett(utfall, el("span", { role: "alert",
        text: modus === "svar"
          ? t("ui.personvern.feil.svar_kreves")
          : t("ui.personvern.feil.begrunnelse_kreves") }));
      return;
    }
    if (modus === "forleng" && !dato.value) {
      sett(utfall, el("span", { role: "alert",
        text: t("ui.personvern.feil.dato_kreves") }));
      return;
    }
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    try {
      if (modus === "svar") {
        await besvarPersonvernsak(gjeldende.sak_id, felt.value, idem);
      } else if (modus === "avvis") {
        await avvisPersonvernsak(gjeldende.sak_id, felt.value, idem);
      } else {
        await forlengPersonvernfrist(gjeldende.sak_id, dato.value,
                                     felt.value, idem);
      }
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.personvern.feil.tilstand")
          : t("ui.personvern.feil.generell") }));
      return;
    }
    felt.value = "";
    dato.value = "";
    idem = null;
    knapp.disabled = false;
    innhold.hidden = true;
    // BEKREFTELSEN MELDES I APPENS EGEN LIVE-REGION, ikke bare i boksen.
    // `last()` tegner hele flaten på nytt, så en melding som bare sto her
    // ville rukket å bli skrevet og revet bort i samme tikk — synlig for
    // ingen, annonsert for ingen. `meldLive` lever i skallet og overlever
    // opptegningen.
    meldLive(t("ui.personvern.dialog.ok"));
    sett(utfall, el("span", { text: t("ui.personvern.dialog.ok") }));
    last();
  });

  return {
    node: boks,
    apne(sak, nyModus) {
      gjeldende = sak;
      modus = nyModus;
      idem = null;
      felt.value = "";
      dato.value = "";
      sett(utfall);
      innhold.hidden = false;
      tegn();
      felt.focus();
    },
  };
}

export function visPersonvern(hoved, ctx) {
  const hode = () => flateHode(t("ui.personvern.tittel"),
    t("ui.personvern.undertittel"));
  sett(hoved, ...hode());
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kropp);
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/personvern"),
    (d) => {
      sett(hoved, ...hode(), kropp);
      const saker = d.saker || [];
      const seksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.personvern.liste.tittel") }));
      const dialog = dialogboks(ctx, last);
      if (!saker.length) {
        // ÆRLIG TOMTILSTAND: et tomt register er ikke «ingen har spurt»
        // — det er «ingen har skrevet det ned», og en forespørsel som
        // ikke står her har ingen frist noen måler.
        seksjon.append(el("p", { class: "muted",
          text: t("ui.personvern.liste.ingen") }));
      } else {
        seksjon.append(tabell(saker, ctx, dialog.apne));
      }
      const deler = [seksjon];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(dialog.node, registrerSkjema(ctx, last));
      }
      sett(kropp, ...deler);
    });
  last();
}
