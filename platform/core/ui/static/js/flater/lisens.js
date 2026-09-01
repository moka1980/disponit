// SaaS- og lisensagenten (M-22 v1) — LISENSREGISTERET som liste.
//
// KOSTNADSDASHBORDET ER v2. v1 er listen, sortert på beslutningsdato, med
// eier, kostnad og oppsigelsesfrist som egne kolonner. Et dashbord med
// totalsummer er en vakker måte å se et budsjett på og en dårlig måte å
// se hva som må besluttes denne måneden; listen er den ærlige v1.
//
// MODULEN SIER IKKE OPP NOE, og flaten later ikke som den gjør det.
// «Marker avsluttet» fører at et menneske HAR avsluttet lisensen — den
// snakker ikke med noen leverandør, og teksten sier det. Katalogens egen
// guard krever unntaksregister, angrefrist og gjenopprettingsvei før noe
// kan fjernes automatisk; tre mekanismer som ikke finnes.
//
// BESLUTNINGSDATOEN ER KOLONNEN SOM BETYR NOE. Fornyelsen står ved siden
// av, men det er `fornyelsesdato - oppsigelsesfrist` et menneske må
// handle på: en avtale med 90 døgns oppsigelsesfrist er ute av din
// kontroll 90 døgn før den fornyes. Flaten viser BEGGE, og fristen som
// ord i sin egen celle — en tabell som bare viste fornyelsen ville
// gjentatt nøyaktig feilen modulen finnes for å hindre.
//
// UTLØPT ER TEKST, ALDRI BARE FARGE. «Fristen er ute» og «beslutning om
// N døgn» står som ord (WCAG 1.4.1) — og på den forfalte raden står
// ordet i tillegg som et eget merke, fordi det er den ene opplysningen
// som ikke tåler å bli oversett.
//
// FLATEN VISER, DEN REGNER IKKE. `beslutningsdato` og
// `dogn_til_beslutning` er regnet i BASEN, i samme skann som raden (098s
// lesedør), nettopp for at flaten ikke skal trekke to datoer fra
// hverandre. Kostnaden kommer som streng fra et NUMERIC og formateres —
// den summeres ikke.
//
// TABELLEN ER EKTE (m16/m21-formen): <caption>, th[scope=col] på
// kolonnene og th[scope=row] på cellen som navngir raden. Uten
// radoverskriften mister en skjermleser i frist- og kostnadskolonnene
// hvilken lisens tallet gjelder.
import { el, sett } from "../dom.js";
import { t, sprak } from "../i18n.js";
import {
  UautorisertFeil, avsluttLisens, fornyLisens, hentJson, nyIdempotensnokkel,
  registrerLisens,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

const FORNYELSESTYPER = ["automatisk", "manuell", "engang"];
const VALUTAER = ["NOK", "EUR", "USD", "GBP", "SEK", "DKK", "CHF"];

// En DATO er en DAG. `Tidspunkt` i komponenter.js formaterer med
// klokkeslett, og midnatt UTC på en fornyelsesdato ville stått som
// «01:00» for en norsk leser — et klokkeslett ingen har oppgitt og ingen
// skal tro på. Egen liten hjelper med `dateStyle` alene, og `datetime`
// bærer ISO-dagen maskinen leser.
function Dato(iso) {
  let vis = iso;
  try {
    vis = new Intl.DateTimeFormat(sprak() === "en" ? "en-GB" : "nb-NO",
      { dateStyle: "medium", timeZone: "UTC" })
      .format(new Date(`${iso}T00:00:00Z`));
  } catch { /* behold iso som fallback */ }
  return el("time", { datetime: iso, text: vis });
}

// Beslutningskolonnens ORD. Ett tall inn, én setning ut — ingen
// utregning.
//
// ENTALL HAR SIN EGEN NØKKEL (096s CodeRabbit-lærdom): locale-settet har
// ingen pluralmaskineri, og «in 1 days» ville stått på nøyaktig den raden
// et menneske leser først — den som må besluttes i morgen. Norsk «døgn»
// bøyes ikke og hadde klart seg; engelsk gjør det, og en oversettelse som
// er riktig bare på det ene språket er ikke riktig.
function beslutningTekst(dogn) {
  if (typeof dogn !== "number") return "—";
  if (dogn < 0) {
    const n = Math.abs(dogn);
    return n === 1
      ? t("ui.lisens.utlopt_for_ett_dogn")
      : t("ui.lisens.utlopt_for").replace("{dogn}", String(n));
  }
  if (dogn === 0) return t("ui.lisens.beslutning_i_dag");
  if (dogn === 1) return t("ui.lisens.om_ett_dogn");
  return t("ui.lisens.om_dogn").replace("{dogn}", String(dogn));
}

// En lisens har PASSERT beslutningspunktet når den fortsatt er aktiv og
// beslutningsdatoen er forbi. En avsluttet lisens med gammel dato er ikke
// forfalt — den er avgjort.
function erPassert(l) {
  return l.status === "aktiv" && typeof l.dogn_til_beslutning === "number"
    && l.dogn_til_beslutning < 0;
}

function eierTekst(l) {
  // Visningsnavnet der IdP-en ga et; bruker-id-en ellers. En tom celle
  // ville vært det eneste svaret som ikke lar noen finne eieren.
  return l.eier_navn || l.eier_bruker_id || t("ui.lisens.ukjent_eier");
}

// Kostnaden formateres, den regnes ikke. Uten beløp står en strek — «0»
// ville vært en LØGN der «vi vet ikke» er sannheten.
function kostnadTekst(l) {
  if (!l.kostnad_aar) return "—";
  const n = Number(l.kostnad_aar);
  if (!Number.isFinite(n)) return `${l.kostnad_aar} ${l.valuta || ""}`.trim();
  let tall = l.kostnad_aar;
  try {
    tall = new Intl.NumberFormat(sprak() === "en" ? "en-GB" : "nb-NO",
      { minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(n);
  } catch { /* behold råstrengen */ }
  return l.valuta ? `${tall} ${l.valuta}` : tall;
}

// Oppsigelsesfristen som ord. NULL er ikke null døgn — det er «ingen
// frist avtalt», og de to betyr helt forskjellige ting for den som skal
// komme seg ut av avtalen.
function fristTekst(l) {
  if (typeof l.oppsigelsesfrist_dogn !== "number") {
    return t("ui.lisens.frist_ingen");
  }
  if (l.oppsigelsesfrist_dogn === 1) return t("ui.lisens.frist_ett_dogn");
  return t("ui.lisens.frist_dogn")
    .replace("{dogn}", String(l.oppsigelsesfrist_dogn));
}

function lisensrad(l, ctx, apneDialog) {
  const rad = el("tr", {});
  // Produktet NAVNGIR raden — det er den identiteten et menneske leser
  // tabellen etter. `.celle-tekst` står på TH-en selv: `max-width` gjør
  // ingenting på et inline-element, så et span inni ville ikke brutt.
  rad.append(el("th", { scope: "row", class: "celle-tekst",
                       text: l.produkt }));
  rad.append(el("td", { class: "celle-tekst", text: l.leverandor }));
  rad.append(el("td", { text: eierTekst(l) }));
  // BESLUTNINGSCELLEN. Datoen, ordene og — når den er passert — merket.
  const beslutning = el("td", {});
  sett(beslutning, Dato(l.beslutningsdato), " ",
    el("span", { class: "muted",
      text: beslutningTekst(l.dogn_til_beslutning) }));
  if (erPassert(l)) {
    // MERKET ER TEKST. En rød rad alene sier ingenting til den som ikke
    // ser farge, og «fristen er ute» er nettopp den opplysningen som
    // ikke tåler å bli oversett.
    beslutning.append(" ", el("strong", { class: "merke",
      text: t("ui.lisens.merke_utlopt") }));
  }
  rad.append(beslutning);
  const fornyelse = el("td", {});
  sett(fornyelse, Dato(l.fornyelsesdato), " ",
    el("span", { class: "muted", text: fristTekst(l) }));
  rad.append(fornyelse);
  rad.append(el("td", { text: kostnadTekst(l) }));
  rad.append(el("td", {
    text: typeof l.antall_seter === "number" ? String(l.antall_seter) : "—" }));
  rad.append(el("td", { class: "celle-tekst", text: l.kilde }));
  rad.append(el("td", { text: t(`ui.lisens.status.${l.status}`, l.status) }));
  const handling = el("td", {});
  if (l.status === "aktiv" && harScope(ctx, "bestilling:opprett")) {
    // Knappene finnes bare på AKTIVE lisenser: dørene i 098 avviser en
    // lisens som alt er avsluttet, og en knapp som alltid feiler er en
    // løgn om hva systemet kan.
    const forny = el("button", { type: "button",
      text: t("ui.lisens.knapp.forny") });
    forny.addEventListener("click", () => apneDialog(l, "forny"));
    const avslutt = el("button", { type: "button",
      text: t("ui.lisens.knapp.avslutt") });
    avslutt.addEventListener("click", () => apneDialog(l, "avslutt"));
    handling.append(forny, " ", avslutt);
  }
  rad.append(handling);
  return rad;
}

function tabell(lisenser, ctx, apneDialog) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.lisens.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.lisens.kolonne.produkt") }),
    el("th", { scope: "col", text: t("ui.lisens.kolonne.leverandor") }),
    el("th", { scope: "col", text: t("ui.lisens.kolonne.eier") }),
    el("th", { scope: "col", text: t("ui.lisens.kolonne.beslutning") }),
    el("th", { scope: "col", text: t("ui.lisens.kolonne.fornyelse") }),
    el("th", { scope: "col", text: t("ui.lisens.kolonne.kostnad") }),
    el("th", { scope: "col", text: t("ui.lisens.kolonne.seter") }),
    el("th", { scope: "col", text: t("ui.lisens.kolonne.kilde") }),
    el("th", { scope: "col", text: t("ui.lisens.kolonne.status") }),
    el("th", { scope: "col", text: t("ui.lisens.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const l of lisenser) tbody.append(lisensrad(l, ctx, apneDialog));
  tb.append(tbody);
  // `.tablewrap` er sidescrollens container — uten den er tabellen
  // bundet til `width: 100%` og klemmer kolonnene mot min-content i
  // stedet for å kunne bli bredere (se komponenter.css). Ti kolonner
  // gjør den obligatorisk, ikke valgfri.
  return el("div", { class: "tablewrap" }, tb);
}

// Registreringsskjemaet. EIEREN VELGES EKSPLISITT — feltet er påkrevd og
// har ingen forhåndsutfylt verdi. Den som fører opp en lisens er ofte
// innkjøperen, mens eieren er den som forvalter verktøyet og skal ta
// valget ved fornyelse.
function registrerSkjema(ctx, last) {
  const boks = el("div", { class: "skjemaboks" });
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const produkt = el("input", { id: "lisens-produkt", name: "produkt",
    type: "text", required: true, maxlength: 200 });
  const leverandor = el("input", { id: "lisens-leverandor",
    name: "leverandor", type: "text", required: true, maxlength: 200 });
  const eier = el("input", { id: "lisens-eier", name: "eier_bruker_id",
    type: "text", required: true, maxlength: 128 });
  const kilde = el("input", { id: "lisens-kilde", name: "kilde",
    type: "text", required: true, maxlength: 500 });
  const fornyelsesdato = el("input", { id: "lisens-fornyelsesdato",
    name: "fornyelsesdato", type: "date", required: true });
  const frist = el("input", { id: "lisens-frist",
    name: "oppsigelsesfrist_dogn", type: "number", min: 0, max: 3650 });
  const seter = el("input", { id: "lisens-seter", name: "antall_seter",
    type: "number", min: 1 });
  const kostnad = el("input", { id: "lisens-kostnad", name: "kostnad_aar",
    type: "number", min: 0, step: "0.01" });
  const valuta = el("select", { id: "lisens-valuta", name: "valuta" });
  valuta.append(el("option", { value: "",
    text: t("ui.lisens.skjema.valuta_ingen") }));
  for (const v of VALUTAER) valuta.append(el("option", { value: v, text: v }));
  const fornyelsestype = el("select", { id: "lisens-fornyelsestype",
    name: "fornyelsestype" });
  for (const f of FORNYELSESTYPER) {
    fornyelsestype.append(el("option", { value: f,
      text: t(`ui.lisens.fornyelsestype.${f}`) }));
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.lisens.knapp.registrer") });
  // ÉN GRUPPE PER FELT: etikett, kontroll og hjelpetekst hører sammen —
  // ligger de som løse søsken, sprer rutenettet dem i hver sin celle, og
  // etiketten mister den visuelle koblingen til feltet sitt uansett hva
  // `for`-attributtet sier.
  const felt = (id, nokkel, kontroll, hjelp) => el("div", { class: "felt" },
    el("label", { for: id, text: t(`ui.lisens.skjema.${nokkel}`) }),
    kontroll,
    hjelp ? el("p", { class: "muted",
      text: t(`ui.lisens.skjema.${hjelp}`) }) : null);

  skjema.append(
    felt("lisens-produkt", "produkt", produkt),
    felt("lisens-leverandor", "leverandor", leverandor),
    felt("lisens-eier", "eier", eier, "eierhjelp"),
    felt("lisens-kilde", "kilde", kilde, "kildehjelp"),
    felt("lisens-fornyelsesdato", "fornyelsesdato", fornyelsesdato),
    // FRISTEN ER MODULENS POENG, og hjelpeteksten sier hvorfor: det er
    // den som avgjør NÅR varselet kommer.
    felt("lisens-frist", "frist", frist, "fristhjelp"),
    felt("lisens-fornyelsestype", "fornyelsestype", fornyelsestype),
    felt("lisens-seter", "seter", seter),
    felt("lisens-kostnad", "kostnad", kostnad, "kostnadhjelp"),
    felt("lisens-valuta", "valuta", valuta),
    // Knappen og varselhjelpen står i en egen bunnrad over hele bredden:
    // en send-knapp inne i en feltkolonne leses som «send inn DETTE
    // feltet».
    el("div", { class: "skjema-bunn" }, knapp,
      el("p", { class: "muted",
        text: t("ui.lisens.skjema.varselhjelp") })));

  // Én nøkkel per intensjon (PR-014 R1): nullstilles når innholdet
  // endres, og ved 4xx — et avvist forsøk har FORBRUKT nøkkelen, og en
  // rettet lisens er en ny intensjon som skal ha sin egen.
  let idem = null;
  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled) return;
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    const kropp = {
      produkt: produkt.value, leverandor: leverandor.value,
      eier_bruker_id: eier.value, kilde: kilde.value,
      // `<input type=date>` gir «YYYY-MM-DD», og det er nøyaktig formen
      // basen vil ha: fornyelsesdatoen er en DAG, ikke et tidspunkt.
      fornyelsesdato: fornyelsesdato.value,
      fornyelsestype: fornyelsestype.value,
    };
    // De valgfrie feltene sendes bare når de er fylt ut. En tom streng
    // ville blitt 0 eller en 400 — og NULL er opplysningen «vi vet
    // ikke», som er noe helt annet enn null kroner og null døgn.
    if (frist.value !== "") {
      kropp.oppsigelsesfrist_dogn = Number(frist.value);
    }
    if (seter.value !== "") kropp.antall_seter = Number(seter.value);
    if (kostnad.value !== "") {
      // STRENG, ikke tall: NUMERIC(14,2) er eksakt, og en JSON-flyttall
      // hører ikke hjemme i et kostnadsregister.
      kropp.kostnad_aar = String(kostnad.value);
      kropp.valuta = valuta.value || "NOK";
    }
    try {
      await registrerLisens(kropp, idem);
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.lisens.feil.tilstand")
          : t("ui.lisens.feil.generell") }));
      return;
    }
    produkt.value = ""; leverandor.value = ""; eier.value = "";
    kilde.value = ""; fornyelsesdato.value = ""; frist.value = "";
    seter.value = ""; kostnad.value = "";
    idem = null;
    knapp.disabled = false;
    meldLive(t("ui.lisens.skjema.ok"));
    sett(utfall, el("span", { text: t("ui.lisens.skjema.ok") }));
    last();
  });
  boks.append(el("h3", { text: t("ui.lisens.skjema.tittel_seksjon") }),
    skjema, utfall);
  return boks;
}

// Fornyelses-/avslutningsdialogen. ÉN boks med to former, fordi de to er
// samme handling for brukeren («jeg har tatt valget») og to helt ulike
// påstander for registeret: en fornyelse sier at avtalen LØPER VIDERE i
// en ny periode, en avslutning at den IKKE LENGER GJELDER. Teksten
// skiller dem, og begge feltene er påkrevde.
function dialogboks(ctx, last) {
  // UTFALLET LIGGER UTENFOR DET SOM SKJULES. Boksen lukker seg når
  // handlingen er registrert — og lå live-regionen inne i den, ble
  // bekreftelsen både usynlig og uannonsert i nøyaktig det øyeblikket den
  // hadde noe å si. Det er `innhold` som skjules, aldri `boks`.
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;
  let modus = "forny";
  let idem = null;

  const overskrift = el("h3", { text: t("ui.lisens.dialog.tittel") });
  const beskrivelse = el("p", { class: "muted" });
  const skjema = el("form", { class: "kv-skjema" });
  const etikett = el("label", { for: "lisens-dialogfelt" });
  // Datofeltet og tekstfeltet er TO kontroller, ikke én med skiftende
  // `type`: en `<input>` som bytter type beholder verdien sin, og en
  // dato som ble stående igjen som begrunnelse er nøyaktig den feilen
  // ingen ser før den står i registeret.
  const datofelt = el("input", { id: "lisens-dialogfelt",
    name: "fornyelsesdato", type: "date", required: true });
  const tekstfelt = el("input", { id: "lisens-dialogfelt",
    name: "begrunnelse", type: "text", required: true, maxlength: 2000 });
  const hjelp = el("p", { class: "muted" });
  const knapp = el("button", { type: "submit" });
  const feltplass = el("div", {});
  skjema.append(etikett, feltplass, hjelp, knapp);
  innhold.append(overskrift, beskrivelse, skjema);
  boks.append(innhold, utfall);
  innhold.hidden = true;

  const felt = () => (modus === "forny" ? datofelt : tekstfelt);

  function tegn() {
    beskrivelse.textContent = gjeldende
      ? `${gjeldende.produkt} — ${gjeldende.leverandor}` : "";
    etikett.textContent = modus === "forny"
      ? t("ui.lisens.dialog.fornyelsesdato")
      : t("ui.lisens.dialog.begrunnelse");
    // HJELPETEKSTEN ER BEGRUNNELSEN, ikke en gjentakelse av etiketten.
    // For avslutningen sier den dessuten hva handlingen IKKE er: den
    // sier ikke opp noe hos leverandøren.
    hjelp.textContent = modus === "forny"
      ? t("ui.lisens.dialog.fornyelsehjelp")
      : t("ui.lisens.dialog.begrunnelsehjelp");
    knapp.textContent = modus === "forny"
      ? t("ui.lisens.knapp.forny")
      : t("ui.lisens.knapp.avslutt");
    sett(feltplass, felt());
  }

  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled || !gjeldende) return;
    const verdi = felt().value.trim();
    if (!verdi) {
      // Flatens EGEN nei, og den sier hvorfor. Serveren sier det samme
      // (døren avviser en tom begrunnelse), men et menneske skal ikke
      // måtte vente på et nettverkskall for å få vite at feltet betyr
      // noe.
      sett(utfall, el("span", { role: "alert",
        text: modus === "forny"
          ? t("ui.lisens.feil.dato_kreves")
          : t("ui.lisens.feil.begrunnelse_kreves") }));
      return;
    }
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    try {
      await (modus === "forny"
        ? fornyLisens(gjeldende.lisens_id, verdi, idem)
        : avsluttLisens(gjeldende.lisens_id, verdi, idem));
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.lisens.feil.tilstand")
          : t("ui.lisens.feil.generell") }));
      return;
    }
    datofelt.value = ""; tekstfelt.value = "";
    idem = null;
    knapp.disabled = false;
    innhold.hidden = true;
    // BEKREFTELSEN MELDES I APPENS EGEN LIVE-REGION, ikke bare i boksen.
    // `last()` tegner hele flaten på nytt, så en melding som bare sto her
    // ville rukket å bli skrevet og revet bort i samme tikk — synlig for
    // ingen, annonsert for ingen.
    meldLive(t("ui.lisens.dialog.ok"));
    sett(utfall, el("span", { text: t("ui.lisens.dialog.ok") }));
    last();
  });

  return {
    node: boks,
    apne(lisens, nyModus) {
      gjeldende = lisens;
      modus = nyModus;
      idem = null;
      datofelt.value = ""; tekstfelt.value = "";
      sett(utfall);
      innhold.hidden = false;
      tegn();
      felt().focus();
    },
  };
}

export function visLisens(hoved, ctx) {
  const hode = () => flateHode(t("ui.lisens.tittel"),
    t("ui.lisens.undertittel"));
  sett(hoved, ...hode());
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kropp);
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/lisens"),
    (d) => {
      sett(hoved, ...hode(), kropp);
      const lisenser = d.lisenser || [];
      const seksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.lisens.liste.tittel") }));
      const dialog = dialogboks(ctx, last);
      if (!lisenser.length) {
        // ÆRLIG TOMTILSTAND: et tomt register er ikke «vi betaler ikke
        // for noe» — det er «ingen har skrevet ned hva vi betaler for»,
        // og setningen sier nettopp det.
        seksjon.append(el("p", { class: "muted",
          text: t("ui.lisens.liste.ingen") }));
      } else {
        seksjon.append(tabell(lisenser, ctx, dialog.apne));
      }
      const deler = [seksjon];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(dialog.node, registrerSkjema(ctx, last));
      }
      sett(kropp, ...deler);
    });
  last();
}
