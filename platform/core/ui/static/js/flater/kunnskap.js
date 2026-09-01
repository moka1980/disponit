// Kunnskap (M-9, 095) — bedriftens ordliste med KILDEKRAV, og et
// fritekstsøk over den.
//
// KILDEN ER EN KOLONNE, IKKE EN FOTNOTE. Hvert treff viser term,
// forklaring, EIER, KILDE og GYLDIGHETSDATO — i tabellens egne kolonner,
// ikke i en tooltip eller en «vis mer». Det er hele modulens dom:
// «svar uten tilstrekkelig kildegrunnlag avvises» er en NOT NULL i
// basen (095), og da skal skjermen bære den også. En ordliste der
// kilden er gjemt, er en ordliste ingen kan etterprøve.
//
// UTLØPT ER TEKST, ALDRI BARE FARGE. Et begrep forbi gyldighetsdato får
// ordet «Utløpt» i sin egen celle. WCAG 1.4.1 krever det, men det er
// ikke bare et krav: «utløpt» er en påstand om at teksten ikke lenger
// gjelder, og en fargenyanse kan ikke si det til noen som skriver ut
// siden, leser den i gråtoner eller hører den lest opp.
//
// FLATEN REGNER IKKE. `utlopt` kommer ferdig som boolean fra basen
// (095 regner den i samme skann som radene), rangeringen er dørens, og
// rekkefølgen sorteres aldri om her. Det eneste som skjer under er
// PRESENTASJON: en boolean blir en setning, og en funntype blir en
// setning.
//
// SØKET SENDES NÅR BRUKEREN SIER FRA, ikke på hvert tastetrykk. Et
// søkefelt som spør serveren for hver bokstav gir en resultatliste som
// endrer seg under fingrene på en skjermleserbruker — og `aria-live` på
// tellingen ville da lest opp seks halvferdige svar på veien til ett.
// Skjemaet har en submit-knapp, og Enter i feltet er den samme veien.
//
// TELLINGEN HAR `aria-live`. En som ikke ser listen skal få vite at det
// KOM et svar, og hvor stort det var — ellers er forskjellen på «ingen
// treff» og «ingenting skjedde» usynlig.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson } from "../api.js";
import { Tidspunkt } from "../komponenter.js";
import { flateHode, medStatus } from "./felles.js";

const FELT_ID = "kunnskap-sok";
const TELLING_ID = "kunnskap-telling";

// Funntypen → setning. Koden er basens lukkede sett (CHECKen i 095), og
// reserven er koden selv: en tredje funntype skal bli SYNLIG som en
// ukjent kode, ikke falle ut av listen.
function funntypeTekst(kode) {
  return t(`ui.kunnskap.funntype.${kode}`, kode);
}

function tomtSvar() {
  return { sporring: "", begreper: [], funn: [] };
}

function begrepstabell(begreper) {
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.kunnskap.tabell.caption") }));
  tabell.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.kunnskap.kolonne.term") }),
    el("th", { scope: "col", text: t("ui.kunnskap.kolonne.forklaring") }),
    el("th", { scope: "col", text: t("ui.kunnskap.kolonne.eier") }),
    el("th", { scope: "col", text: t("ui.kunnskap.kolonne.kilde") }),
    el("th", { scope: "col", text: t("ui.kunnskap.kolonne.gyldig_til") }),
    el("th", { scope: "col", text: t("ui.kunnskap.kolonne.status") }))));
  const tbody = el("tbody");
  for (const b of begreper) {
    tbody.append(el("tr", {},
      // Termen NAVNGIR raden: uten `th scope="row"` mister en
      // skjermleser i kilde- og datokolonnene hvilket begrep den leser.
      el("th", { scope: "row", text: b.term }),
      el("td", { class: "celle-tekst", text: b.forklaring }),
      el("td", { text: b.eier }),
      // Kilden er RÅ TEKST, aldri en lenke. Den er en referanse kunden
      // selv har skrevet («kilde://intern/…», et saksnummer, et
      // dokumentnavn) — å gjøre den klikkbar ville vært å påstå at
      // flaten vet hva den peker på.
      el("td", { text: b.kilde }),
      el("td", { text: b.gyldig_til || "—" }),
      el("td", { text: b.utlopt
        ? t("ui.kunnskap.status.utlopt")
        : t("ui.kunnskap.status.gjeldende") })));
  }
  tabell.append(tbody);
  // `.tablewrap` er sidescrollens container — uten den er tabellen
  // bundet til `width: 100%` og klemmer kolonnene mot min-content i
  // stedet for å kunne bli bredere (se komponenter.css). Den manglet
  // på alle tabellene her; eier så det som «ser ikke bra ut».
  return el("div", { class: "tablewrap" }, tabell);
}

function funntabell(funn) {
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.kunnskap.funn.caption") }));
  tabell.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.kunnskap.kolonne.term") }),
    el("th", { scope: "col", text: t("ui.kunnskap.kolonne.funntype") }),
    el("th", { scope: "col", text: t("ui.kunnskap.kolonne.gyldig_til") }),
    el("th", { scope: "col", text: t("ui.kunnskap.kolonne.forst_sett") }))));
  const tbody = el("tbody");
  for (const f of funn) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: f.term }),
      el("td", { text: funntypeTekst(f.funntype) }),
      el("td", { text: f.gyldig_til || "—" }),
      el("td", {}, Tidspunkt(f.forst_sett, {}))));
  }
  tabell.append(tbody);
  // `.tablewrap` er sidescrollens container — uten den er tabellen
  // bundet til `width: 100%` og klemmer kolonnene mot min-content i
  // stedet for å kunne bli bredere (se komponenter.css). Den manglet
  // på alle tabellene her; eier så det som «ser ikke bra ut».
  return el("div", { class: "tablewrap" }, tabell);
}

function funnseksjon(funn) {
  const seksjon = el("section", { class: "kpi-kort" },
    el("h3", { text: t("ui.kunnskap.funn.tittel") }));
  if (!funn.length) {
    // ÆRLIG TOMTILSTAND: ingen funn er her faktisk godt nytt, men
    // setningen sier HVA fraværet betyr — ikke bare at listen er tom.
    // Den sier også at sveipen er det som fyller den, så en tom liste
    // på en vert der timeren aldri har kjørt ikke leses som «alt i
    // orden».
    seksjon.append(el("p", { class: "muted",
      text: t("ui.kunnskap.funn.ingen") }));
    return seksjon;
  }
  seksjon.append(funntabell(funn));
  return seksjon;
}

export function visKunnskap(hoved, ctx) {
  sett(hoved, ...flateHode(t("ui.kunnskap.tittel"),
    t("ui.kunnskap.undertittel")));

  const felt = el("input", { type: "search", id: FELT_ID, name: "q" });
  const knapp = el("button", { class: "knapp", type: "submit",
    text: t("ui.kunnskap.sok_knapp") });
  const skjema = el("form", { class: "kpi-kontroll", novalidate: true,
    role: "search" },
    el("label", { for: FELT_ID, text: t("ui.kunnskap.sok_ledetekst") }),
    felt, knapp);
  // TELLINGEN er den eneste `aria-live`-en på flaten. `polite` og ikke
  // `assertive`: et søkeresultat skal ikke avbryte den som holder på å
  // lese noe annet — det skal annonseres når det passer.
  const telling = el("p", { id: TELLING_ID, class: "muted",
    "aria-live": "polite", "aria-atomic": "true" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(skjema, telling, kropp);

  function tegn(data) {
    const d = data || tomtSvar();
    const begreper = d.begreper || [];
    telling.textContent = begreper.length
      ? t("ui.kunnskap.treff").replace("{antall}", String(begreper.length))
      : t("ui.kunnskap.ingen_treff");
    const seksjon = el("section", { class: "kpi-kort" },
      el("h3", { text: t("ui.kunnskap.ordliste.tittel") }));
    if (begreper.length) {
      seksjon.append(begrepstabell(begreper));
    } else {
      seksjon.append(el("p", { class: "muted",
        text: t("ui.kunnskap.ordliste.ingen") }));
    }
    sett(kropp, seksjon, funnseksjon(d.funn || []));
  }

  function hent(sporring, fraBruker) {
    // ÉN RAMME OM ETT KALL. Ordlisten og funnene kommer i samme svar
    // (én rute, to dører i basen), nettopp for at skjermen ikke skal
    // kunne stå halvt tegnet: et søk som lykkes mens funnlisten feilet
    // ville sagt «alt i orden» om en ordliste med utløpte begreper.
    medStatus(hoved, ctx,
      () => hentJson("/v1/kunnskap", { q: sporring || null }),
      (data) => {
        // REKKEFØLGEN ER IKKE VILKÅRLIG. Rammen river `hoved` for
        // lastetilstanden, så `telling` er ute av dokumentet mens
        // kallet står på. Den settes derfor INN igjen her, FØR
        // `tegn()` skriver teksten: en `aria-live`-region som får
        // innholdet sitt mens den står utenfor dokumentet, annonserer
        // ingenting — det er selve endringen i en tilkoblet region som
        // leses opp.
        sett(hoved, ...flateHode(t("ui.kunnskap.tittel"),
          t("ui.kunnskap.undertittel")), skjema, telling, kropp);
        tegn(data);
        // …og fokus følger med tilbake. Rammen river den fokuserte
        // søkeknappen synkront, så en tastaturbruker som trykker Enter
        // ville ellers havnet på `body` og måttet finne veien til
        // feltet sitt igjen for hvert søk. Bare når søket kom FRA
        // brukeren: å stjele fokus på første tegning ville hoppet over
        // overskriften ingen da hadde lest.
        if (fraBruker) felt.focus();
      });
  }

  skjema.addEventListener("submit", (e) => {
    e.preventDefault();
    hent(felt.value, true);
  });
  hent("", false);
}
