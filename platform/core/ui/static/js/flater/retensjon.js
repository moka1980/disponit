// M-4 (093) retensjonsregnskapet — REN LESEFLATE. Ingen
// mutasjonsknapper, ingen skjema, ingen bekreftelsesdialog: flaten
// muterer ingenting, og det er ikke en mangel. Registerets dommer felles
// i migrasjon, og målingen skrives av en timer med sin egen rolle — det
// finnes ingen HTTP-vei inn.
//
// DEN ENESTE knappen på flaten er kolonnesorteringen, og den står her
// fordi `aria-sort` ellers ville vært pynt: en tabell som melder en
// sorteringstilstand ingen kan endre, forteller skjermleseren om en
// affordans som ikke finnes. Sorteringen endrer rekkefølgen på det
// leseren alt har fått — den ber aldri serveren om noe.
//
// TRE DELER, i den rekkefølgen en leser trenger dem:
//   1. MÅLINGENS HODE, som SIER MED TEKST om siste kjøring var avbrutt.
//      En avbrutt kjøring rapporteres som avbrutt, aldri som komplett —
//      og en flate som viste tallene uten den setningen ville gjort en
//      halv måling om til et grønt bilde.
//   2. LAGERTABELLEN: hvert lager med sin klasse, frist, reaper og dom.
//      Estimattall er merket SOM TEKST («estimat»), aldri med farge eller
//      kursiv alene — `reltuples` er ANALYZE-ens siste gjetning, og en
//      leser som ikke ser forskjell på den og en telling leser feil tall.
//   3. FUNNLISTEN, kun for `platform:admin`. Funntypen står som TEKST;
//      fargeklassen er en redundant koding oppå, aldri det eneste som
//      skiller et funn fra et annet.
//
// TABELLEN ER HÅNDBYGD og ikke `DataTabell`: komponenten legger hver
// celle i en `<td>`, mens lagernavnet er RADENS NAVN og må være
// `<th scope="row">` (m16-regelen) — uten den mister en skjermleser i
// tallkolonnene hvilket lager tallet gjelder. Sorteringen er den samme
// mekanikken som `tabell.js` bruker: `aria-sort` på et tastaturbetjent
// knappe-th, oppdatert PÅ PLASS så fokus ikke kastes.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson } from "../api.js";
import { Tidspunkt } from "../komponenter.js";
import { flateHode, kvRad, medStatus } from "./felles.js";

const KOLONNER = [
  { nokkel: "lager_id", i18n: "lager", sorterbar: true, radnavn: true },
  { nokkel: "klasse", i18n: "klasse", sorterbar: true },
  { nokkel: "frist", i18n: "frist", sorterbar: true },
  { nokkel: "reaper", i18n: "reaper", sorterbar: false },
  { nokkel: "dom", i18n: "dom", sorterbar: true },
  { nokkel: "ureapet", i18n: "ureapet", sorterbar: true },
  { nokkel: "eldste", i18n: "eldste", sorterbar: true },
];

function tekst(verdi) {
  return verdi == null || verdi === "" ? "—" : String(verdi);
}

// Sorteringen holdes AV FLATEN, ikke av tabellen: tabellen bygges på nytt
// ved hver tegning og kan ikke huske et valg den ikke overlever.
let sortValg = { nokkel: "lager_id", retning: "ascending" };

function maalingshode(m) {
  const dl = el("dl", { class: "kv-liste" });
  if (!m) {
    // Ikke en tom tabell: fraværet av en måling er sin egen setning.
    return el("section", { class: "kpi-kort" },
      el("h3", { text: t("ui.retensjon.maaling") }),
      el("p", { class: "muted", text: t("ui.retensjon.maaling_ingen") }));
  }
  const kort = el("section", { class: "kpi-kort" },
    el("h3", { text: t("ui.retensjon.maaling") }));
  kvRad(dl, t("ui.retensjon.felt.startet"), Tidspunkt(m.startet_ts, {}));
  kvRad(dl, t("ui.retensjon.felt.fullfort"),
    m.fullfort_ts ? Tidspunkt(m.fullfort_ts, {})
      : t("ui.retensjon.ikke_fullfort"));
  kvRad(dl, t("ui.retensjon.felt.antall_lagre"), String(m.antall_lagre));
  kvRad(dl, t("ui.retensjon.felt.umaalbare"), String(m.antall_umaalbare));
  kvRad(dl, t("ui.retensjon.felt.funn"), String(m.antall_funn));
  kort.append(dl);
  // DEN BÆRENDE SETNINGEN. Den står som en egen avsnittstekst, ikke som
  // et fravær av grønt: «avbrutt» skal kunne LESES, også av en som bare
  // hører siden.
  // Ingen fargeklasse: setningen bærer dommen alene. Den avbrutte
  // varianten står i ordinær tekstfarge (ikke `muted`), så den er den mest
  // framtredende linjen i kortet uten at farge er det som skiller dem.
  kort.append(m.avbrutt
    ? el("p", { text: t("ui.retensjon.avbrutt_ja") })
    : el("p", { class: "muted", text: t("ui.retensjon.avbrutt_nei") }));
  return kort;
}

function fristTekst(l) {
  if (l.frist_dogn == null) return t("ui.retensjon.frist_ingen");
  return t("ui.retensjon.frist_dogn").replace("{n}", String(l.frist_dogn));
}

function ureapetCelle(l) {
  // BEGGE tallene må finnes. Ett av dem alene ville gitt «7 / null» i
  // cellen — en tekst som ser ut som en måling og ikke er det.
  if (l.rader_ureapet == null || l.rader == null) {
    // Et lager UTEN reap-markør telles bevisst ikke — og det skal stå
    // som en setning, ikke som en tom celle en leser kan tro er null.
    return el("span", { class: "muted",
      text: t("ui.retensjon.ikke_talt") });
  }
  return el("span", {},
    el("span", { text: `${l.rader_ureapet} / ${l.rader}` }));
}

function lagerrad(l) {
  const tr = el("tr");
  for (const kol of KOLONNER) {
    let innhold;
    if (kol.nokkel === "lager_id") innhold = el("code", { text: l.lager_id });
    else if (kol.nokkel === "klasse") {
      innhold = el("span", { text: t(`ui.retensjon.klasse.${l.klasse}`,
                                     l.klasse) });
    } else if (kol.nokkel === "frist") innhold = el("span", {},
      el("span", { text: fristTekst(l) }),
      el("br"),
      el("span", { class: "muted", text: tekst(l.fristkilde) }));
    else if (kol.nokkel === "reaper") {
      innhold = l.reaper ? el("code", { text: l.reaper })
        : el("span", { class: "muted", text: t("ui.retensjon.reaper_ingen") });
    } else if (kol.nokkel === "dom") {
      // Dommen som TEKST, og begrunnelsen med den. En dom uten
      // begrunnelse er en påstand, og registeret nekter å bære en.
      // `celle-tekst` på den YTRE beholderen, og den er `div` og ikke
      // `span`: `max-width` gjør ingenting på et inline-element, så
      // klassen på begrunnelses-spannet alene ville sett riktig ut i
      // diffen og ikke begrenset noe som helst. Dommen og begrunnelsen
      // deler tak, som de deler celle.
      innhold = el("div", { class: "celle-tekst" },
        el("span", { text: t(`ui.retensjon.dom.${l.dom}`, l.dom) }),
        el("br"),
        el("span", { class: "muted", text: tekst(l.dom_begrunnelse) }));
    } else if (kol.nokkel === "ureapet") innhold = ureapetCelle(l);
    else if (kol.nokkel === "eldste") {
      innhold = l.eldste_ureapet_ts ? Tidspunkt(l.eldste_ureapet_ts, {})
        : el("span", { class: "muted", text: "—" });
    }
    tr.append(kol.radnavn ? el("th", { scope: "row" }, innhold)
      : el("td", {}, innhold));
  }
  return tr;
}

function sorter(rader) {
  const nokkel = sortValg.nokkel;
  const verdi = (l) => {
    if (nokkel === "frist") return l.frist_dogn == null ? -1 : l.frist_dogn;
    if (nokkel === "ureapet") {
      return l.rader_ureapet == null ? -1 : l.rader_ureapet;
    }
    if (nokkel === "eldste") return l.eldste_ureapet_ts || "";
    return l[nokkel] || "";
  };
  const retning = sortValg.retning === "descending" ? -1 : 1;
  return rader.slice().sort((a, b) => {
    const va = verdi(a); const vb = verdi(b);
    if (va < vb) return -retning;
    if (va > vb) return retning;
    return 0;
  });
}

function lagertabell(lagre) {
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.retensjon.tabell_caption") }));
  const thead = el("thead");
  const tbody = el("tbody");
  const sortbareTh = new Map();
  const trHode = el("tr");
  for (const kol of KOLONNER) {
    const th = el("th", { scope: "col" });
    const tittel = t(`ui.retensjon.kolonne.${kol.i18n}`);
    if (kol.sorterbar) {
      th.setAttribute("aria-sort",
        sortValg.nokkel === kol.nokkel ? sortValg.retning : "none");
      sortbareTh.set(kol.nokkel, th);
      const b = el("button", { class: "sort-knapp", type: "button",
        text: tittel });
      // Knappen SORTERER — den muterer ingenting på serveren. Det er den
      // eneste interaksjonen flaten har, og den er tastaturbetjent.
      b.addEventListener("click", () => {
        if (sortValg.nokkel === kol.nokkel) {
          sortValg = { nokkel: kol.nokkel,
            retning: sortValg.retning === "ascending"
              ? "descending" : "ascending" };
        } else {
          sortValg = { nokkel: kol.nokkel, retning: "ascending" };
        }
        // Indikatorene oppdateres PÅ PLASS, og bare kroppen tegnes på
        // nytt: en gjenoppbygd thead ville kastet tastaturfokus bort fra
        // knappen leseren nettopp trykket.
        for (const [n, e] of sortbareTh) {
          e.setAttribute("aria-sort",
            sortValg.nokkel === n ? sortValg.retning : "none");
        }
        sett(tbody, ...sorter(lagre).map(lagerrad));
      });
      th.append(b);
    } else {
      th.textContent = tittel;
    }
    trHode.append(th);
  }
  sett(thead, trHode);
  sett(tbody, ...sorter(lagre).map(lagerrad));
  tabell.append(thead, tbody);
  return el("div", { class: "tablewrap" }, tabell);
}

function katalogtabell(katalog) {
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.retensjon.katalog_caption") }));
  tabell.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.retensjon.kolonne.lager") }),
    el("th", { scope: "col", text: t("ui.retensjon.kolonne.tenant") }),
    el("th", { scope: "col", text: t("ui.retensjon.kolonne.bytes") }),
    el("th", { scope: "col", text: t("ui.retensjon.kolonne.estimat") }),
    el("th", { scope: "col", text: t("ui.retensjon.kolonne.ureapet") }))));
  const tbody = el("tbody");
  for (const k of katalog) {
    tbody.append(el("tr", {},
      el("th", { scope: "row" }, el("code", { text: k.lager_id })),
      el("td", { text: k.tenant === "" ? t("ui.retensjon.global")
        : k.tenant }),
      el("td", { text: k.bytes_totalt == null ? "—"
        : String(k.bytes_totalt) }),
      // ESTIMATET ER MERKET SOM TEKST. `reltuples` er ANALYZE-ens siste
      // gjetning; et tall som ser ut som en telling blir lest som en.
      el("td", {}, k.rader_estimat == null ? "—"
        : el("span", {},
          el("span", { text: String(k.rader_estimat) }),
          " ",
          el("span", { class: "muted",
            text: t("ui.retensjon.estimat_merke") }))),
      el("td", { text: k.rader_ureapet == null ? "—"
        : `${k.rader_ureapet} / ${k.rader}` })));
  }
  tabell.append(tbody);
  return el("div", { class: "tablewrap" }, tabell);
}

function funnliste(funn) {
  const seksjon = el("section", { class: "kpi-kort" },
    el("h3", { text: t("ui.retensjon.funn") }));
  if (!funn.length) {
    seksjon.append(el("p", { class: "muted",
      text: t("ui.retensjon.funn_ingen") }));
    return seksjon;
  }
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.retensjon.funn_caption") }));
  tabell.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.retensjon.kolonne.lager") }),
    el("th", { scope: "col", text: t("ui.retensjon.kolonne.funntype") }),
    el("th", { scope: "col", text: t("ui.retensjon.kolonne.relasjon") }),
    el("th", { scope: "col", text: t("ui.retensjon.kolonne.oppdaget") }))));
  const tbody = el("tbody");
  for (const f of funn) {
    tbody.append(el("tr", {},
      el("th", { scope: "row" }, el("code", { text: f.lager_id })),
      // FUNNTYPEN SOM TEKST, aldri et trafikklys alene.
      el("td", { text: t(`ui.retensjon.funntype.${f.funntype}`,
                         f.funntype) }),
      el("td", {}, el("code", { text: f.relasjon })),
      el("td", {}, Tidspunkt(f.oppdaget_ts, {}))));
  }
  tabell.append(tbody);
  seksjon.append(el("div", { class: "tablewrap" }, tabell));
  return seksjon;
}

export function visRetensjon(hoved, ctx) {
  sett(hoved, ...flateHode(t("ui.retensjon.tittel"),
    t("ui.retensjon.undertittel")));
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kropp);
  medStatus(hoved, ctx,
    () => hentJson("/v1/retensjon"),
    (d) => {
      sett(hoved, ...flateHode(t("ui.retensjon.tittel"),
        t("ui.retensjon.undertittel")), kropp);
      const deler = [maalingshode(d.maaling)];
      if (!d.lagre.length) {
        deler.push(el("p", { class: "muted",
          text: t("ui.retensjon.ingen_lagre") }));
      } else {
        deler.push(lagertabell(d.lagre));
      }
      // `null` fra serveren betyr «du ser ikke denne delen»; `[]` betyr
      // «det finnes ingen». Flaten skiller dem, ellers ville en tom
      // funnliste sett ut som manglende tilgang og omvendt.
      if (d.katalog != null) deler.push(katalogtabell(d.katalog));
      if (d.funn != null) deler.push(funnliste(d.funn));
      sett(kropp, ...deler);
    });
}
