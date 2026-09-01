// Datakvalitet (M-3, 092) — profilen av plattformens egne tenant-tabeller.
//
// FLATEN VISER, DEN REGNER IKKE. Hvert tall her står i en rad API-et
// leverte: `rader_vurdert`, `rader_avvik` og `andel_avvik` kommer alle
// tre fra basen, og andelen er en GENERERT kolonne der — flaten deler
// aldri to av svarets tall på hverandre (M-16-regelen). Det eneste som
// skjer under er presentasjon: en andel i [0, 1] vises som prosent med
// én desimal, og en maskinkode slås opp som setning.
//
// AVVIKSANDEL SOM TALL OG TEKST, ALDRI TRAFIKKLYS ALENE. Hver rad viser
// «3 av 128 (2,3 %)» i sin egen celle. Et fargefelt kunne stått ved
// siden av; det kan ikke stå i stedet for (WCAG 1.4.1) — og en andel
// uten sine tellere er et tall ingen kan etterprøve.
//
// «IKKE MÅLT» ER IKKE «0». Modulens bærende regel, rendret: en regel som
// står i kjøringens `umaalbare_regler` får teksten «ikke målt», ikke en
// tom celle og ikke en null. En regel uten profilrad som HELLER ikke er
// umålbar, har ingen rader hos denne tenanten — og det er en tredje
// tilstand med sin egen setning. Tre tilstander, tre setninger; ingen av
// dem er en tom celle.
//
// KJØRINGENS HODE SIER `avbrutt` MED TEKST. En runde som ikke rakk
// gjennom registeret er ikke en grønn runde med få regler, og det står
// som en setning i hodet — ikke som et ikon, og ikke bare som et tall.
//
// TABELLENE ER EKTE TABELLER: <caption>, th scope="col" og en
// th scope="row" som navngir hver rad. `aria-sort` står på kolonnen
// SERVEREN sorterte på — dørene i 092 leverer regler alfabetisk og funn
// nyeste først — og den er derfor sann uten at flaten sorterer om. Det
// er også grunnen til at det ikke finnes en sorteringsknapp her: en
// `aria-sort` som lyver er verre enn ingen.
//
// INGEN KNAPPER. Profileringen skrives av
// `disponit-kvalitetsprofil.service` med sin egen DB-rolle, og
// kvalitetsregisteret endres kun i migrasjon. Det finnes ingen HTTP-dør
// å tegne en knapp til — og v1 RETTER ingenting, slår ingenting sammen
// og blokkerer ingen bestilling. Det er en dom, ikke en manglende
// funksjon, og flaten skal ikke antyde noe annet.
import { el, sett } from "../dom.js";
import { t, sprak } from "../i18n.js";
import { hentJson } from "../api.js";
import { Tidspunkt } from "../komponenter.js";
import { flateHode, medStatus } from "./felles.js";

// Maskinkoder → setninger. Reserven er koden selv: en femte funntype
// eller en ny regeltype skal bli SYNLIG som en ukjent kode, ikke falle
// ut av tabellen.
function funntypeTekst(kode) {
  return t(`ui.datakvalitet.funntype.${kode}`, kode);
}
function regeltypeTekst(kode) {
  return t(`ui.datakvalitet.regeltype.${kode}`, kode);
}
function alvorlighetTekst(kode) {
  return t(`ui.datakvalitet.alvorlighet.${kode}`, kode);
}

// Andel i [0, 1] → prosent med én desimal. EN verdi omregnet til en
// annen enhet, ikke et forhold mellom to av svarets tall.
//
// Desimalskilletegnet er LOCALENS, ikke en hardkodet komma (CodeRabbit):
// «2,3 %» er riktig på norsk og feil på engelsk, og et tall som leses
// feil er verre enn intet tall. `Intl.NumberFormat` gjør både skilletegn,
// tusenskille og prosenttegnets plassering til språkets ansvar.
function prosent(andel) {
  if (typeof andel !== "number") return "—";
  return new Intl.NumberFormat(sprak(), {
    style: "percent", minimumFractionDigits: 1, maximumFractionDigits: 1,
  }).format(andel);
}

// Avvikscellen: tallene FØRST, andelen etter. Leses «3 av 128 (2,3 %)».
function avvikTekst(p) {
  return t("ui.datakvalitet.avvik")
    .replace("{avvik}", String(p.rader_avvik))
    .replace("{vurdert}", String(p.rader_vurdert))
    .replace("{andel}", prosent(p.andel_avvik));
}

function regelseksjon(regler) {
  const seksjon = el("section", { class: "kpi-kort" },
    el("h3", { text: t("ui.datakvalitet.regler.tittel") }));
  if (!regler.length) {
    seksjon.append(el("p", { class: "muted",
      text: t("ui.datakvalitet.regler.ingen") }));
    return seksjon;
  }
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.datakvalitet.regler.caption") }));
  tabell.append(el("thead", {}, el("tr", {},
    // Dørens rekkefølge ER alfabetisk på regel-ID (092). aria-sort sier
    // det, og flaten sorterer ikke om.
    el("th", { scope: "col", "aria-sort": "ascending",
      text: t("ui.datakvalitet.regler.kolonne.regel") }),
    el("th", { scope: "col",
      text: t("ui.datakvalitet.regler.kolonne.maal") }),
    el("th", { scope: "col",
      text: t("ui.datakvalitet.regler.kolonne.type") }),
    el("th", { scope: "col",
      text: t("ui.datakvalitet.regler.kolonne.alvorlighet") }),
    el("th", { scope: "col",
      text: t("ui.datakvalitet.regler.kolonne.begrunnelse") }))));
  const tbody = el("tbody");
  for (const r of regler) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: r.regel_id }),
      el("td", { text: `${r.relasjon}.${r.kolonne}` }),
      el("td", { text: regeltypeTekst(r.regeltype) }),
      el("td", { text: alvorlighetTekst(r.alvorlighet) }),
      el("td", { class: "celle-tekst", text: r.begrunnelse })));
  }
  tabell.append(tbody);
  seksjon.append(tabell);
  return seksjon;
}

// Hodet for siste kjøring. Fire tall og TO setninger: én om når, én om
// hvorvidt runden rakk gjennom registeret.
function kjoringshode(k) {
  const hode = el("div", {});
  const naar = el("p", {});
  sett(naar, t("ui.datakvalitet.kjoring.naar"), " ",
    Tidspunkt(k.startet_ts, {}));
  const tall = el("p", {
    text: t("ui.datakvalitet.kjoring.tall")
      .replace("{regler}", String(k.antall_regler))
      .replace("{umaalbare}", String(k.antall_umaalbare))
      .replace("{funn}", String(k.antall_funn)),
  });
  // `avbrutt` SOM TEKST. Den fullførte runden sier det også — ellers
  // ville fraværet av en advarsel vært det eneste signalet, og et
  // fravær er ikke en melding.
  const dom = el("p", {
    // Ingen egen fargeklasse: dommen ER teksten. En klasse som bare
    // fantes for å farge den ville vært trafikklyset denne flaten
    // nekter å bruke, og «avbrutt» må uansett leses for å forstås.
    class: "muted",
    text: k.avbrutt
      ? t("ui.datakvalitet.kjoring.avbrutt")
      : t("ui.datakvalitet.kjoring.fullfort"),
  });
  hode.append(naar, tall, dom);
  return hode;
}

// Profiltabellen for ÉN kjøring, satt sammen med registeret: hver regel
// får en rad, også de uten profiltall. Det er hele poenget — en regel
// som mangler skal SI hvorfor den mangler.
function profiltabell(k, regler) {
  const perRegel = new Map();
  for (const p of k.profiler || []) perRegel.set(p.regel_id, p);
  const umaalbare = new Set(k.umaalbare_regler || []);
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.datakvalitet.profil.caption") }));
  tabell.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", "aria-sort": "ascending",
      text: t("ui.datakvalitet.profil.kolonne.regel") }),
    el("th", { scope: "col",
      text: t("ui.datakvalitet.profil.kolonne.vurdert") }),
    el("th", { scope: "col",
      text: t("ui.datakvalitet.profil.kolonne.avvik") }))));
  const tbody = el("tbody");
  for (const r of regler) {
    const p = perRegel.get(r.regel_id);
    let vurdert;
    let avvik;
    if (umaalbare.has(r.regel_id)) {
      // MODULENS BÆRENDE REGEL PÅ SKJERMEN: en regel som ikke kunne
      // måles står som «ikke målt», aldri som 0.
      vurdert = t("ui.datakvalitet.profil.ikke_maalt");
      avvik = t("ui.datakvalitet.profil.ikke_maalt");
    } else if (!p) {
      // Målt, men tenanten har ingen rader i relasjonen. En tredje
      // tilstand, og ikke det samme som «ikke målt».
      vurdert = t("ui.datakvalitet.profil.ingen_rader");
      avvik = t("ui.datakvalitet.profil.ingen_rader");
    } else {
      vurdert = String(p.rader_vurdert);
      avvik = avvikTekst(p);
    }
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: r.regel_id }),
      el("td", { text: vurdert }),
      el("td", { text: avvik })));
  }
  tabell.append(tbody);
  return tabell;
}

function profilseksjon(d) {
  const seksjon = el("section", { class: "kpi-kort" },
    el("h3", { text: t("ui.datakvalitet.profil.tittel") }));
  const kjoringer = (d && d.kjoringer) || [];
  if (!kjoringer.length) {
    // ÆRLIG TOMTILSTAND: ingen profilering er ikke «alt i orden». Det er
    // en base ingen har målt, og setningen sier det.
    seksjon.append(el("p", { class: "muted",
      text: t("ui.datakvalitet.profil.ingen") }));
    return seksjon;
  }
  // Nyeste først er dørens rekkefølge (092) — flaten sorterer ikke om.
  const siste = kjoringer[0];
  seksjon.append(kjoringshode(siste),
    profiltabell(siste, (d && d.regler) || []));
  return seksjon;
}

function funntabell(funn, medTenant) {
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: medTenant
      ? t("ui.datakvalitet.funn.caption_tverrgaaende")
      : t("ui.datakvalitet.funn.caption") }));
  const hoderad = el("tr", {},
    // Dørens rekkefølge er `sist_sett_ts DESC` (092).
    el("th", { scope: "col", "aria-sort": "descending",
      text: t("ui.datakvalitet.funn.kolonne.sist_sett") }));
  if (medTenant) {
    hoderad.append(el("th", { scope: "col",
      text: t("ui.datakvalitet.funn.kolonne.tenant") }));
  }
  hoderad.append(
    el("th", { scope: "col", text: t("ui.datakvalitet.funn.kolonne.regel") }),
    el("th", { scope: "col", text: t("ui.datakvalitet.funn.kolonne.type") }),
    el("th", { scope: "col",
      text: t("ui.datakvalitet.funn.kolonne.ganger") }));
  tabell.append(el("thead", {}, hoderad));
  const tbody = el("tbody");
  for (const f of funn) {
    const rad = el("tr", {},
      el("th", { scope: "row" }, Tidspunkt(f.sist_sett_ts, {})));
    if (medTenant) rad.append(el("td", { text: f.tenant }));
    rad.append(
      el("td", { text: f.regel_id }),
      el("td", { text: funntypeTekst(f.funntype) }),
      el("td", { text: String(f.ganger_sett) }));
    tbody.append(rad);
  }
  tabell.append(tbody);
  return tabell;
}

function funnseksjon(d) {
  const seksjon = el("section", { class: "kpi-kort" },
    el("h3", { text: t("ui.datakvalitet.funn.tittel") }));
  const funn = (d && d.funn) || [];
  if (!funn.length) {
    seksjon.append(el("p", { class: "muted",
      text: t("ui.datakvalitet.funn.ingen") }));
  } else {
    seksjon.append(funntabell(funn, false));
  }
  return seksjon;
}

// Plattformdriftens seksjon. Den tegnes KUN når serveren sa
// `plattformdrift: true` — og den SIER at den er tverrgående, fordi en
// tabell som stille inneholder andre kunders rader er verre enn en som
// forklarer hvorfor den gjør det.
function tverrgaaendeSeksjon(d) {
  if (!d || !d.plattformdrift) return null;
  const seksjon = el("section", { class: "kpi-kort" },
    el("h3", { text: t("ui.datakvalitet.tverrgaaende.tittel") }),
    el("p", { class: "muted",
      text: t("ui.datakvalitet.tverrgaaende.forklaring") }));
  const funn = d.tverrgaaende_funn || [];
  if (!funn.length) {
    seksjon.append(el("p", { class: "muted",
      text: t("ui.datakvalitet.tverrgaaende.ingen") }));
  } else {
    seksjon.append(funntabell(funn, true));
  }
  return seksjon;
}

export function visDatakvalitet(hoved, ctx) {
  sett(hoved, ...flateHode(t("ui.datakvalitet.tittel"),
    t("ui.datakvalitet.undertittel")));
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kropp);
  medStatus(hoved, ctx,
    () => hentJson("/v1/datakvalitet"),
    (data) => {
      sett(hoved, ...flateHode(t("ui.datakvalitet.tittel"),
        t("ui.datakvalitet.undertittel")), kropp);
      const seksjoner = [profilseksjon(data), funnseksjon(data),
                         regelseksjon((data && data.regler) || [])];
      const tverr = tverrgaaendeSeksjon(data);
      if (tverr) seksjoner.push(tverr);
      sett(kropp, ...seksjoner);
    });
}
