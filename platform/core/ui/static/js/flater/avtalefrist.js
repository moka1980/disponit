// Avtale- og fristagenten (M-21 v1) — FORPLIKTELSESREGISTERET som liste.
//
// ÅRSHJULET ER v2. v1 er listen, sortert på frist, med eier og kilde som
// egne kolonner og en tydelig markering av hva som er forfalt. Et årshjul
// er en vakker måte å vise tolv måneder på og en dårlig måte å se hva som
// brenner i dag; listen er den ærlige v1, og den er den som gjør at eier
// og kilde faktisk kan leses ved siden av hverandre.
//
// FORFALT ER TEKST, ALDRI BARE FARGE. «Forfalt» og «forfaller om N døgn»
// står som ord i sin egen celle (WCAG 1.4.1) — og på den forfalte raden
// står ordet i tillegg som et eget merke, fordi det er den ene
// opplysningen som ikke tåler å bli oversett.
//
// FLATEN VISER, DEN REGNER IKKE. `dogn_til_frist` er regnet i BASEN, i
// samme skann som raden (096s lesedør), nettopp for at flaten ikke skal
// trekke to tidspunkter fra hverandre.
//
// TABELLEN ER EKTE (m16-formen): <caption>, th[scope=col] på kolonnene og
// th[scope=row] på cellen som navngir raden. Uten radoverskriften mister
// en skjermleser i frist- og eierkolonnene hvilken plikt tallet gjelder.
//
// LUKKEDIALOGEN KREVER KVITTERINGSREFERANSEN. Feltet er `required`, og
// feilteksten sier HVORFOR — ikke «feltet er påkrevd», men «en frist
// lukkes av en kvittering, aldri av at tiden går». Det er akseptkravet
// modulen er bygget på, og et menneske som får det forklart én gang
// slutter å oppleve det som en irritasjon. Serveren feller dommen uansett
// (døren i 096 avviser en tom referanse); skjemaet er ergonomi, ikke
// sikkerhet.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, bortfallPlikt, hentJson, lukkPlikt, nyIdempotensnokkel,
  registrerPlikt,
} from "../api.js";
import { Tidspunkt, meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

const GJENTAKELSER = ["engang", "aarlig", "kvartalsvis", "manedlig"];

// Fristkolonnens ORD. Ett tall inn, én setning ut — ingen utregning.
//
// ENTALL HAR SIN EGEN NØKKEL (CodeRabbit). Locale-settet har ingen
// pluralmaskineri, og «in 1 days» ville stått på nøyaktig den raden et
// menneske leser først — den som forfaller i morgen. Norsk «døgn» bøyes
// ikke og hadde klart seg; engelsk gjør det, og en oversettelse som er
// riktig bare på det ene språket er ikke riktig. To ekstra nøkler er
// billigere enn et pluralrammeverk denne modulen ikke har mandat til å
// innføre for hele plattformen.
function fristTekst(dogn) {
  if (typeof dogn !== "number") return "—";
  if (dogn < 0) {
    const n = Math.abs(dogn);
    return n === 1
      ? t("ui.avtalefrist.forfalt_for_ett_dogn")
      : t("ui.avtalefrist.forfalt_for").replace("{dogn}", String(n));
  }
  if (dogn === 0) return t("ui.avtalefrist.forfaller_i_dag");
  if (dogn === 1) return t("ui.avtalefrist.om_ett_dogn");
  return t("ui.avtalefrist.om_dogn").replace("{dogn}", String(dogn));
}

// En plikt er FORFALT når den fortsatt er åpen og fristen er passert. En
// lukket plikt med en gammel frist er ikke forfalt — den er gjort.
function erForfalt(p) {
  return p.status === "apen" && typeof p.dogn_til_frist === "number"
    && p.dogn_til_frist < 0;
}

function eierTekst(p) {
  // Visningsnavnet der IdP-en ga et; bruker-id-en ellers. En tom celle
  // ville vært det eneste svaret som ikke lar noen finne eieren.
  return p.eier_navn || p.eier_bruker_id || t("ui.avtalefrist.ukjent_eier");
}

function pliktrad(p, ctx, apneDialog) {
  const rad = el("tr", {});
  // Tittelen NAVNGIR raden — det er den identiteten et menneske leser
  // tabellen etter.
  rad.append(el("th", { scope: "row", class: "celle-tekst",
                       text: p.tittel }));
  const fristcelle = el("td", {});
  sett(fristcelle, Tidspunkt(p.frist, {}), " ",
    el("span", { class: "muted", text: fristTekst(p.dogn_til_frist) }));
  if (erForfalt(p)) {
    // MERKET ER TEKST. En rød rad alene sier ingenting til den som ikke
    // ser farge, og «forfalt» er nettopp den opplysningen som ikke tåler
    // å bli oversett.
    fristcelle.append(" ", el("strong", { class: "merke",
      text: t("ui.avtalefrist.merke_forfalt") }));
  }
  rad.append(fristcelle);
  rad.append(el("td", { text: eierTekst(p) }));
  rad.append(el("td", { text: p.kilde }));
  rad.append(el("td", { text: t(`ui.avtalefrist.gjentakelse.${p.gjentakelse}`,
    p.gjentakelse) }));
  rad.append(el("td", { text: t(`ui.avtalefrist.status.${p.status}`,
    p.status) }));
  const handling = el("td", {});
  if (p.status === "apen" && harScope(ctx, "bestilling:opprett")) {
    // Knappene finnes bare på ÅPNE plikter: dørene i 096 avviser en
    // plikt som alt er lukket eller bortfalt, og en knapp som alltid
    // feiler er en løgn om hva systemet kan.
    const lukk = el("button", { type: "button",
      text: t("ui.avtalefrist.knapp.lukk") });
    lukk.addEventListener("click", () => apneDialog(p, "lukk"));
    const bortfall = el("button", { type: "button",
      text: t("ui.avtalefrist.knapp.bortfall") });
    bortfall.addEventListener("click", () => apneDialog(p, "bortfall"));
    handling.append(lukk, " ", bortfall);
  }
  rad.append(handling);
  return rad;
}

function tabell(plikter, ctx, apneDialog) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.avtalefrist.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.avtalefrist.kolonne.tittel") }),
    el("th", { scope: "col", text: t("ui.avtalefrist.kolonne.frist") }),
    el("th", { scope: "col", text: t("ui.avtalefrist.kolonne.eier") }),
    el("th", { scope: "col", text: t("ui.avtalefrist.kolonne.kilde") }),
    el("th", { scope: "col",
      text: t("ui.avtalefrist.kolonne.gjentakelse") }),
    el("th", { scope: "col", text: t("ui.avtalefrist.kolonne.status") }),
    el("th", { scope: "col", text: t("ui.avtalefrist.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const p of plikter) tbody.append(pliktrad(p, ctx, apneDialog));
  tb.append(tbody);
  // `.tablewrap` er sidescrollens container — uten den er tabellen
  // bundet til `width: 100%` og klemmer kolonnene mot min-content i
  // stedet for å kunne bli bredere (se komponenter.css). Den manglet
  // på alle tabellene her; eier så det som «ser ikke bra ut».
  return el("div", { class: "tablewrap" }, tb);
}

// Registreringsskjemaet. EIEREN VELGES EKSPLISITT — feltet er påkrevd og
// har ingen forhåndsutfylt verdi. En flate som stille satte innloggeren
// som eier ville gjort «plikter uten eier»-KPI-en sann på papiret og falsk
// i praksis: den som registrerer plikten er ofte ikke den som skal gjøre
// den.
function registrerSkjema(ctx, last) {
  const boks = el("div", { class: "skjemaboks" });
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const tittel = el("input", { id: "plikt-tittel", name: "tittel",
    type: "text", required: true, maxlength: 200 });
  const eier = el("input", { id: "plikt-eier", name: "eier_bruker_id",
    type: "text", required: true, maxlength: 128 });
  const kilde = el("input", { id: "plikt-kilde", name: "kilde",
    type: "text", required: true, maxlength: 500 });
  const frist = el("input", { id: "plikt-frist", name: "frist",
    type: "date", required: true });
  const gjentakelse = el("select", { id: "plikt-gjentakelse",
    name: "gjentakelse" });
  for (const g of GJENTAKELSER) {
    gjentakelse.append(el("option", { value: g,
      text: t(`ui.avtalefrist.gjentakelse.${g}`) }));
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.avtalefrist.knapp.registrer") });
  // ÉN GRUPPE PER FELT (eiervedtak 1/9: «feltene og knappene bør stå ved
  // siden av hverandre»). Etikett, kontroll og hjelpetekst hører sammen —
  // ligger de som løse søsken, sprer rutenettet dem i hver sin celle, og
  // etiketten mister den visuelle koblingen til feltet sitt uansett hva
  // `for`-attributtet sier.
  const felt = (id, nokkel, kontroll, hjelp) => el("div", { class: "felt" },
    el("label", { for: id, text: t(`ui.avtalefrist.skjema.${nokkel}`) }),
    kontroll,
    hjelp ? el("p", { class: "muted",
      text: t(`ui.avtalefrist.skjema.${hjelp}`) }) : null);

  skjema.append(
    felt("plikt-tittel", "tittel", tittel),
    felt("plikt-eier", "eier", eier, "eierhjelp"),
    felt("plikt-kilde", "kilde", kilde, "kildehjelp"),
    felt("plikt-frist", "frist", frist),
    felt("plikt-gjentakelse", "gjentakelse", gjentakelse),
    // Knappen og varselhjelpen står i en egen bunnrad over hele bredden:
    // en send-knapp inne i en feltkolonne leses som «send inn DETTE
    // feltet».
    el("div", { class: "skjema-bunn" }, knapp,
      el("p", { class: "muted",
        text: t("ui.avtalefrist.skjema.varselhjelp") })));

  // Én nøkkel per intensjon (PR-014 R1): nullstilles når innholdet
  // endres, og ved 4xx — et avvist forsøk har FORBRUKT nøkkelen, og en
  // rettet plikt er en ny intensjon som skal ha sin egen.
  let idem = null;
  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled) return;
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    try {
      await registrerPlikt({
        tittel: tittel.value, eier_bruker_id: eier.value,
        kilde: kilde.value,
        // `<input type=date>` gir «YYYY-MM-DD». Fristen er en DAG, ikke
        // et klokkeslett — vi sender datoen som midnatt UTC og lar basen
        // være den ene som kjenner tidssonen. En lokal midnatt ville
        // gjort samme frist til to ulike tidspunkter for to kolleger.
        frist: `${frist.value}T00:00:00Z`,
        gjentakelse: gjentakelse.value,
      }, idem);
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.avtalefrist.feil.tilstand")
          : t("ui.avtalefrist.feil.generell") }));
      return;
    }
    tittel.value = ""; eier.value = ""; kilde.value = ""; frist.value = "";
    idem = null;
    knapp.disabled = false;
    meldLive(t("ui.avtalefrist.skjema.ok"));
    sett(utfall, el("span", { text: t("ui.avtalefrist.skjema.ok") }));
    last();
  });
  boks.append(el("h3", { text: t("ui.avtalefrist.skjema.tittel_seksjon") }),
    skjema, utfall);
  return boks;
}

// Lukke-/bortfallsdialogen. ÉN boks med to former, fordi de to er samme
// handling for brukeren («jeg er ferdig med denne») og to helt ulike
// påstander for registeret: en kvittering sier at plikten ER GJORT, en
// bortfallsbegrunnelse at den IKKE LENGER GJELDER. Teksten skiller dem,
// og begge feltene er påkrevde.
function dialogboks(ctx, last) {
  // UTFALLET LIGGER UTENFOR DET SOM SKJULES (CodeRabbit). Boksen lukker
  // seg når kvitteringen er registrert — og lå live-regionen inne i den,
  // ble bekreftelsen både usynlig og uannonsert i nøyaktig det øyeblikket
  // den hadde noe å si. Det er `innhold` som skjules, aldri `boks`.
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;
  let modus = "lukk";
  let idem = null;

  const overskrift = el("h3", { text: t("ui.avtalefrist.dialog.tittel") });
  const beskrivelse = el("p", { class: "muted" });
  const skjema = el("form", { class: "kv-skjema" });
  const etikett = el("label", { for: "plikt-referanse" });
  const felt = el("input", { id: "plikt-referanse", name: "referanse",
    type: "text", required: true, maxlength: 2000 });
  const hjelp = el("p", { class: "muted" });
  const knapp = el("button", { type: "submit" });
  skjema.append(etikett, felt, hjelp, knapp);
  innhold.append(overskrift, beskrivelse, skjema);
  boks.append(innhold, utfall);
  innhold.hidden = true;

  function tegn() {
    beskrivelse.textContent = gjeldende ? gjeldende.tittel : "";
    etikett.textContent = modus === "lukk"
      ? t("ui.avtalefrist.dialog.kvittering")
      : t("ui.avtalefrist.dialog.begrunnelse");
    // HJELPETEKSTEN ER BEGRUNNELSEN, ikke en gjentakelse av etiketten.
    // Den sier hvorfor feltet ikke kan stå tomt.
    hjelp.textContent = modus === "lukk"
      ? t("ui.avtalefrist.dialog.kvitteringhjelp")
      : t("ui.avtalefrist.dialog.begrunnelsehjelp");
    knapp.textContent = modus === "lukk"
      ? t("ui.avtalefrist.knapp.lukk")
      : t("ui.avtalefrist.knapp.bortfall");
  }

  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled || !gjeldende) return;
    if (!felt.value.trim()) {
      // Flatens EGEN nei, og den sier hvorfor. Serveren sier det samme
      // (døren avviser en tom referanse), men et menneske skal ikke måtte
      // vente på et nettverkskall for å få vite at feltet betyr noe.
      sett(utfall, el("span", { role: "alert",
        text: modus === "lukk"
          ? t("ui.avtalefrist.feil.kvittering_kreves")
          : t("ui.avtalefrist.feil.begrunnelse_kreves") }));
      return;
    }
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    try {
      await (modus === "lukk"
        ? lukkPlikt(gjeldende.plikt_id, felt.value, idem)
        : bortfallPlikt(gjeldende.plikt_id, felt.value, idem));
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.avtalefrist.feil.tilstand")
          : t("ui.avtalefrist.feil.generell") }));
      return;
    }
    felt.value = "";
    idem = null;
    knapp.disabled = false;
    innhold.hidden = true;
    // BEKREFTELSEN MELDES I APPENS EGEN LIVE-REGION, ikke bare i boksen.
    // `last()` tegner hele flaten på nytt, så en melding som bare sto her
    // ville rukket å bli skrevet og revet bort i samme tikk — synlig for
    // ingen, annonsert for ingen. `meldLive` lever i skallet og overlever
    // opptegningen. Teksten står også lokalt, for den som ser skjermen i
    // det korte vinduet før listen kommer tilbake.
    meldLive(t("ui.avtalefrist.dialog.ok"));
    sett(utfall, el("span", { text: t("ui.avtalefrist.dialog.ok") }));
    last();
  });

  return {
    node: boks,
    apne(plikt, nyModus) {
      gjeldende = plikt;
      modus = nyModus;
      idem = null;
      felt.value = "";
      sett(utfall);
      innhold.hidden = false;
      tegn();
      felt.focus();
    },
  };
}

export function visAvtalefrist(hoved, ctx) {
  const hode = () => flateHode(t("ui.avtalefrist.tittel"),
    t("ui.avtalefrist.undertittel"));
  sett(hoved, ...hode());
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kropp);
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/plikt"),
    (d) => {
      sett(hoved, ...hode(), kropp);
      const plikter = d.plikter || [];
      const seksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.avtalefrist.liste.tittel") }));
      const dialog = dialogboks(ctx, last);
      if (!plikter.length) {
        // ÆRLIG TOMTILSTAND: et tomt register er ikke «ingenting å
        // gjøre» — det er «ingen har skrevet ned hva som skal gjøres»,
        // og setningen sier nettopp det.
        seksjon.append(el("p", { class: "muted",
          text: t("ui.avtalefrist.liste.ingen") }));
      } else {
        seksjon.append(tabell(plikter, ctx, dialog.apne));
      }
      const deler = [seksjon];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(dialog.node, registrerSkjema(ctx, last));
      }
      sett(kropp, ...deler);
    });
  last();
}
