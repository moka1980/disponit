// Nøkkeltall (M-16 v1) — «nøkkeltall regnet fra faktiske beslutninger»:
// flaten VISER hva Disponit gjorde, den analyserer ikke. Hvert tall er en
// telling serveren gjorde i ett skann (suminvarianten bor i defineren og
// kontrolleres i API-laget), radvise varigheter er de eneste
// differansene, og ingenting glattes, interpoleres eller framskrives.
//
// TABELLEN ER TILGANGSFORMEN. Hvert kort er en ekte <table> med <caption>
// og <th scope="col">; søyleraden er en CSS-bredde VED SIDEN AV tallet —
// aldri en bærer av informasjon som ikke også står som tekst. Kategori
// skilles med tekst, aldri kun farge (søylen er én farge fra tokens).
//
// To akser, aldri blandet: vinduskortene teller AKTIVITET i [fra, til);
// «åpne nå» er TILSTAND og står utenfor vindusvelgeren, med egen
// ledetekst — velgeren rører den ikke.
import { el, sett } from "../dom.js";
import { t, harNokkel } from "../i18n.js";
import { hentJson, UautorisertFeil, IngenTilgangFeil } from "../api.js";
import { Tidspunkt, Feiltilstand, TilgangsVakt } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode } from "./felles.js";

const VINDUER = ["24t", "7d", "30d"];

// Én søylerad: tallet som TEKST først, bredden som visuell støtte.
// Bredden er relativ til partisjonens største verdi — en presentasjons-
// skala, ingen avledet størrelse (tallet ved siden av er sannheten).
// Divisjonen her er den ENESTE i denne fila (statisk port): alle
// avledede tall — andeler, snitt — regnes i API-laget og kommer ferdige
// i svaret; flaten deler aldri to av svarets tall på hverandre.
function soyle(antall, maks) {
  const bredde = maks > 0 ? Math.round((antall / maks) * 100) : 0;
  const rot = el("div", { class: "kpi-soyle", "aria-hidden": "true" });
  rot.append(el("div", { class: "kpi-soyle-fyll" }));
  rot.lastChild.style.width = `${bredde}%`;
  return rot;
}

// Andelen kommer FERDIG fra API-laget (0–1, fire desimaler) og skrives
// bare om til prosent for lesbarhet — flaten regner ingen andel selv.
// `null` er «ikke definert» (nevner 0): at ingen ble talt er ikke det
// samme som at andelen er 0, og tomraden i et tomt vindu bærer samme
// tekst av samme grunn.
function andelTekst(verdi) {
  if (verdi == null) return t("ui.nokkeltall.andel_ikke_definert");
  return `${Math.round(verdi * 100)} %`;
}

// Partisjonsverdiene ER databasens maskinkoder («utfort», «over_grense»,
// «hoppet_over»), og de fleste av dem har ALT en oversatt nøkkel andre
// steder i repoet. Uten denne koblingen falt de tilbake til den rå koden,
// og i engelsk visning sto halve flaten på norsk. Hvert kort peker derfor
// på SIN kanoniske nøkkelfamilie i stedet for at nøkkeltallflaten bygger
// et parallelt oversettelsessett som må vedlikeholdes ved siden av.
const NOKKELFAMILIER = {
  oppdrag: (v) => `art.outbox_${v}`,     // oppdragsstatus (jf. lesing.py)
  unntak: (v) => `unntak.${v}`,          // unntakskategori
  tick: (v) => `ui.plan.utfall.${v}`,    // planutfall (044)
};

// Rekkefølgen er SPESIFISITET, ikke opphav: den nøkkelen som vet mest om
// hvilket kort verdien står på, vinner.
//
//   1. kortets egen nøkkel   — sagt om nøyaktig dette kortet
//   2. kortets familie       — sagt om domenet kortet teller over
//   3. flatens generelle     — sagt om ingen av delene, siste tekst
//   4. den rå koden          — siste utvei; en ukjent verdi skal fortsatt
//                              VISES (port 1: aldri stille utenfor
//                              totalen), ikke skjules.
//
// Var listen sortert på opphav — «våre nøkler før repoets» — kunne en
// kortblind nøkkel i tier 3 kapre en verdi den ikke visste hvilket kort
// tilhørte. `ukjent` er nettopp den formen: definerne skriver den som
// NULL-sentinel på HVERT kort (`coalesce(..., 'ukjent')` i 051), men på
// unntakskortet er den samtidig en ekte kategori som resten av repoet
// leser som «Handlingen er ikke definert i policy» (`unntak.ukjent`,
// samme tekst som KategoriTag). Den generelle «ukjent» stjal den
// betydningen. Det gjelder alle tiere, ikke bare denne verdien: en
// framtidig `ui.nokkeltall.verdi.utfort` ville ellers overstyrt
// `art.outbox_utfort` for alle kort samtidig.
function nokkelTekst(kortNokkel, verdi) {
  const familie = NOKKELFAMILIER[kortNokkel];
  const kandidater = [`ui.nokkeltall.verdi.${kortNokkel}.${verdi}`];
  if (familie) kandidater.push(familie(verdi));
  kandidater.push(`ui.nokkeltall.verdi.${verdi}`);
  for (const k of kandidater) {
    if (harNokkel(k)) return t(k);
  }
  return verdi;
}

// Cellen som NAVNGIR raden er en overskrift, ikke data — «over_grense»
// er ikke en verdi i kolonnen, det er hva de andre cellene i raden
// handler om. Uten dette knytter en skjermleser i tall- eller
// søylekolonnen bare kolonneoverskriften til tallet, og leseren mister
// hvilken kategori tallet gjelder.
//
// Regelen bodde allerede i filen, men bare på totalraden. Den er derfor
// ÉN funksjon som hver radnavncelle går gjennom — begge tabellformene og
// tomraden — i stedet for en `scope`-verdi som må huskes på nytt hvert
// sted en rad bygges. Det var nettopp det som glapp.
function radnavn(tekst) {
  return el("th", { scope: "row", text: tekst });
}

// Ett partisjonskort: <table> med rader (nokkel, antall, andel, søyle)
// + total. Tomt vindu er en EKSPLISITT rad: 0 og «ingen» — aldri et
// skjult kort. Andelskolonnen er GENERISK: hver partisjon bærer den,
// aldri et kuratert utvalg — og hver andel kan leses tilbake til teller
// og nevner som står i samme tabell (rad og totalrad).
function partisjonstabell(kortNokkel, caption, partisjon) {
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: caption }));
  const thead = el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.nokkeltall.kolonne.hva") }),
    el("th", { scope: "col", text: t("ui.nokkeltall.kolonne.antall") }),
    el("th", { scope: "col", text: t("ui.nokkeltall.kolonne.andel") }),
    el("th", { scope: "col", class: "vh",
               text: t("ui.nokkeltall.kolonne.soyle") })));
  const tbody = el("tbody");
  const deler = Object.entries(partisjon.deler);
  const andeler = partisjon.andeler ?? {};
  const maks = Math.max(0, ...deler.map(([, n]) => n));
  if (!deler.length) {
    tbody.append(el("tr", {},
      radnavn(t("ui.nokkeltall.ingen")),
      el("td", { text: "0" }),
      el("td", { text: t("ui.nokkeltall.andel_ikke_definert") }),
      el("td", {})));
  }
  for (const [nokkel, antall] of deler) {
    tbody.append(el("tr", {},
      radnavn(nokkelTekst(kortNokkel, nokkel)),
      el("td", { text: String(antall) }),
      el("td", { text: andelTekst(andeler[nokkel]) }),
      el("td", {}, soyle(antall, maks))));
  }
  const tfoot = el("tfoot", {}, el("tr", {},
    radnavn(t("ui.nokkeltall.total")),
    el("td", { text: String(partisjon.total) }),
    el("td", {}),
    el("td", {})));
  tabell.append(thead, tbody, tfoot);
  return tabell;
}

// Lukkede saker: RADFAKTA med et visningstak. Taket er aldri stille —
// er totalen i vinduet større enn antall rader, står det i klartekst
// hvor mange av hvor mange som vises, rett ved tabellen.
// `tidssone` er den samme som vindusledeteksten merkes med: radene ligger
// PER DEFINISJON i vinduet, så tegnes de i en annen sone enn grensene, ser
// en leser utenfor UTC rader som faller utenfor vinduet de er talt i.
function lukkedeTabell(ctx, rader, totalt, grense, tidssone, lukketid) {
  if (!rader.length) {
    return el("p", { class: "muted",
      text: t("ui.nokkeltall.ingen_lukkede") });
  }
  const bolk = el("div");
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.nokkeltall.lukkede_caption") }));
  tabell.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.nokkeltall.kolonne.kategori") }),
    el("th", { scope: "col", text: t("ui.nokkeltall.kolonne.lukket") }),
    el("th", { scope: "col",
               text: t("ui.nokkeltall.kolonne.varighet") }))));
  const tbody = el("tbody");
  for (const r of rader) {
    // Radvis varighet — én saks egen differanse, aldri et snitt.
    tbody.append(el("tr", {},
      radnavn(nokkelTekst("unntak", r.kategori)),
      el("td", {}, Tidspunkt(r.lukket, { tidssone })),
      el("td", { text: varighetTekst(r.varighet_s) })));
  }
  tabell.append(tbody);
  bolk.append(tabell);
  // Snittet er API-lagets omskriving av to tall som BEGGE står i svaret
  // (varighetssummen og tellingen, samme skann som radene) — flaten
  // regner det aldri selv. Det gjelder HELE vinduet, ikke utsnittet
  // over, og settes derfor ved siden av tellingen det er delt på.
  // `null` (ingen lukkede) når aldri hit — da står «ingen»-setningen.
  if (lukketid && lukketid.gjennomsnitt_s != null) {
    bolk.append(el("p", { class: "muted",
      text: t("ui.nokkeltall.lukketid_gjennomsnitt")
        .replace("{varighet}", varighetTekst(lukketid.gjennomsnitt_s))
        .replace("{antall}", String(lukketid.antall)) }));
  }
  if (totalt > rader.length) {
    // Setningen står uansett hvem som leser: at utsnittet er avkuttet er
    // sant for alle, og den påstanden avhenger ikke av at det finnes en
    // knapp ved siden av. Veien videre gjør — den tegnes bare for en økt
    // som faktisk kan gå den.
    bolk.append(el("p", { class: "muted",
      text: t("ui.nokkeltall.lukkede_avkuttet")
        .replace("{vist}", String(rader.length))
        .replace("{totalt}", String(totalt))
        .replace("{grense}", String(grense)) }));
    const videre = lenkeTilUnntak(ctx);
    if (videre) bolk.append(videre);
  }
  return bolk;
}

const VARIGHETSLEDD = [[86400, "ui.nokkeltall.varighet_dogn"],
                       [3600, "ui.nokkeltall.varighet_timer"],
                       [60, "ui.nokkeltall.varighet_min"],
                       [1, "ui.nokkeltall.varighet_sek"]];

// Varighet som klartekst — en OMSKRIVING av sekundtallet (presentasjon),
// ingen beregning over flere rader.
//
// Omskrivingen er TAPSFRI: den bryter sekundtallet ned i alle leddene som
// er større enn null, ned til sekundet. Å bare vise det største leddet
// kastet resten — 119 s ble «1 min» og 86 399 s ble «23 timer» — og
// kortet lover nettopp den enkelte sakens FAKTISKE varighet, ikke et
// avrundet anslag. Et tall denne flaten viser skal kunne leses tilbake.
function varighetTekst(sek) {
  // 0 og et negativt tall (status_ts før ts) vises som det er: en
  // dataanomali skal SES, ikke glattes bort til «0 sek».
  if (!(sek > 0)) {
    return t("ui.nokkeltall.varighet_sek").replace("{n}", String(sek));
  }
  const ledd = [];
  let rest = sek;
  for (const [storrelse, nokkel] of VARIGHETSLEDD) {
    // Nedbrytingen skrives uten divisjonstegn: den statiske porten
    // («eneste `/` i denne fila er soyle()s presentasjonsskala») skal
    // kunne leses uten unntaksliste. Men heller ikke som naiv én-og-én-
    // subtraksjon (CodeRabbit): en dataanomali med enorm varighet skal
    // VISES, ikke fryse flaten i millioner av runder. Leddet dobles så
    // lenge det får plass — binær kvotient, eksakt heltallsaritmetikk,
    // logaritmisk antall steg, taps- og restfritt.
    let n = 0;
    while (rest >= storrelse) {
      let steg = storrelse;
      let telt = 1;
      while (rest - steg >= steg) { steg += steg; telt += telt; }
      rest -= steg;
      n += telt;
    }
    if (n > 0) ledd.push(t(nokkel).replace("{n}", String(n)));
  }
  return ledd.join(" ");
}

export function visNokkeltall(hoved, ctx) {
  let vindu = "24t";
  sett(hoved, ...flateHode(t("ui.nokkeltall.tittel"),
    t("ui.nokkeltall.undertittel")));

  // TILSTANDSBLOKKEN — utenfor vindusvelgeren, med egen ledetekst.
  const tilstand = el("section", { class: "kpi-tilstand",
    "aria-label": t("ui.nokkeltall.apne_naa") });
  // Vinduskontrollen: <select> med <label>; valgt vindu og tidssone i
  // klartekst ved tallene.
  const velgerId = "nokkeltall-vindu";
  const velger = el("select", { id: velgerId });
  for (const v of VINDUER) {
    velger.append(el("option", { value: v,
      text: t(`ui.nokkeltall.vindu.${v}`) }));
  }
  const kontroll = el("div", { class: "kpi-kontroll" },
    el("label", { for: velgerId, text: t("ui.nokkeltall.vindu_ledetekst") }),
    velger);
  const meta = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(tilstand, kontroll, meta, kropp);

  // Bare det SISTE vindusvalget får skrive til flaten. Uten dette lever
  // to kall side om side når velgeren endres mens et svar er underveis,
  // og et sent svar for det GAMLE vinduet kan overskrive det nye — eller
  // en gammel feil erstatte et ferskt resultat. Da ville tallene stått
  // under en annen vindusledetekst enn de er talt i, og flaten vist noe
  // som aldri var sant. Hver last får derfor et løpenummer, og et svar
  // som ikke lenger er det siste, kastes: velgeren er sannheten.
  //
  // Flaten er ÉN LESNING. «Åpne nå», vindusgrensene og kortene kommer fra
  // samme svar, talt i samme skann — men lastingen og feilveien ryddet bare
  // kortene (Codex P2). Et vindusvalg som feilet lot derfor forrige svars
  // «åpne nå» og forrige vindus grenser bli stående, under velgerens NYE
  // vindusnavn: et tall flaten ikke har fått, og grenser for et annet
  // utsnitt enn det som er valgt — nettopp den formen «to akser, aldri
  // blandet» finnes for å hindre.
  //
  // De tømmes derfor sammen med kortene, i det lastingen starter. At
  // «åpne nå» ikke er talt over vinduet betyr at velgeren ikke SKOPER den,
  // ikke at den overlever et svar som aldri kom: «nå» er en påstand om
  // tidspunktet, og den holder bare så lenge svaret bak den gjør det.
  let siste = 0;
  const last = () => {
    const min = ++siste;
    const utdatert = () => min !== siste;
    sett(tilstand);
    sett(meta);
    sett(kropp, el("p", { class: "muted", text: t("ui.laster") }));
    hentJson("/v1/nokkeltall", { vindu }).then((d) => {
      if (utdatert()) return;
      sett(tilstand,
        el("b", { text: String(d.apne_naa) }),
        el("span", { text: " " + t("ui.nokkeltall.apne_naa_tekst") }));
      sett(meta,
        `${t("ui.nokkeltall.vindu_valgt")}: `,
        Tidspunkt(d.vindu_start, { tidssone: d.tidssone }),
        " – ", Tidspunkt(d.vindu_slutt, { tidssone: d.tidssone }));
      const kort = [];
      const besl = partisjonstabell("beslutning",
        t("ui.nokkeltall.kort.beslutninger"), d.beslutninger);
      const beslKort = el("section", { class: "kpi-kort" }, besl);
      const tilBesl = lenkeTilBeslutninger(ctx);
      if (tilBesl) beslKort.append(tilBesl);
      kort.push(beslKort);
      // Frekvenskortet: EGET kort (før var det én skalarlinje i
      // beslutningskortet). Nøklene er tenantens egne handlingskoder —
      // ingen oversettelsesfamilie finnes, så de vises rå, som enhver
      // verdi utenfor det kjente domenet (port 1: aldri skjult).
      kort.push(el("section", { class: "kpi-kort" },
        partisjonstabell("frekvens",
          t("ui.nokkeltall.kort.frekvens"), d.frekvens)));
      const akt = el("section", { class: "kpi-kort" });
      for (const [partisjon, data] of Object.entries(d.aktiveringer)) {
        akt.append(partisjonstabell("aktivering",
          t(`ui.nokkeltall.kort.aktiveringer.${partisjon}`), data));
      }
      kort.push(akt);
      kort.push(el("section", { class: "kpi-kort" },
        partisjonstabell("oppdrag",
          t("ui.nokkeltall.kort.oppdrag"), d.oppdrag)));
      kort.push(el("section", { class: "kpi-kort" },
        partisjonstabell("unntak",
          t("ui.nokkeltall.kort.unntak_aktivitet"), d.unntak_aktivitet),
        lukkedeTabell(ctx, d.unntak_lukkede, d.unntak_lukkede_totalt,
                      d.unntak_lukkede_grense, d.tidssone,
                      d.unntak_lukketid)));
      // Tick-kortet: 0 rader er en SETNING, ikke en tom graf — og
      // setningen får ikke påstå mer enn det som er MÅLT. Vinduet og
      // all tid er to tellinger fra hver sin definer, og de skilles i
      // to setninger: er begge 0, har ingen plan noen gang kjørt — det
      // er nå en målt påstand (tick_alltid_totalt). Er bare vinduet 0,
      // sies det med tallet for all tid ved siden av, så et gyldig tomt
      // utsnitt aldri leses som et fravær over all tid.
      kort.push(el("section", { class: "kpi-kort" },
        d.tick.total === 0
          ? el("div", {},
              el("h3", { text: t("ui.nokkeltall.kort.tick") }),
              el("p", { class: "muted",
                text: d.tick_alltid_totalt === 0
                  ? t("ui.nokkeltall.ingen_tick_alltid")
                  : t("ui.nokkeltall.ingen_tick_vindu_alltid")
                      .replace("{antall}",
                               String(d.tick_alltid_totalt)) }))
          : partisjonstabell("tick",
              t("ui.nokkeltall.kort.tick"), d.tick)));
      sett(kropp, ...kort);
    }).catch((e) => {
      // Uautorisert er en ØKTTILSTAND, ikke et vindusresultat: den gjelder
      // uansett hvilket kall som oppdaget den, og slippes derfor gjennom.
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (utdatert()) return;
      if (e instanceof IngenTilgangFeil) { sett(kropp, TilgangsVakt({})); return; }
      sett(kropp, Feiltilstand({ paaProvIgjen: last }));
    });
  };
  velger.addEventListener("change", () => { vindu = velger.value; last(); });
  last();
}

function lenkeTilBeslutninger(ctx) {
  return flateknapp(ctx, "ui.nokkeltall.til_beslutninger", "#/beslutninger",
                    "decisions:read");
}

// Veien videre når lukkede-listen er avkuttet: unntaksflaten har hele
// settet — nøkkeltallflaten paginerer aldri, den VISER.
//
// «Hele settet» krever at flaten når de SAMME køene tallene er talt over.
// Begge sider avleder køene av `security:read` — nøkkeltallene gjennom
// `synlige_sakstyper` (app.py) og unntaksflaten gjennom `synligeSakstyper`
// (sitekart.js) — så en økt som teller sikkerhets- og driftssaker her også
// kan åpne dem der. Da flaten var låst til `normal`, kunne avkuttingen være
// forårsaket av rader knappen ikke hadde noen vei til.
function lenkeTilUnntak(ctx) {
  return flateknapp(ctx, "ui.nokkeltall.til_unntak", "#/unntak",
                    "exceptions:read");
}

// EN VEI VIDERE ER ET LØFTE OM AT FLATEN FINNES (Codex P2).
//
// Både menyen og ruteren bygges av `sitekart` fra ØKTENS scopes, så en
// rute økten ikke har finnes rett og slett ikke: `lagRuter` leser
// `#/unntak` som en ukjent adresse og tegner reserveflaten (Oversikt).
// En `policyforvalter` har `decisions:read` og ser derfor nøkkeltallene,
// men mangler `exceptions:read` — «Til unntakslisten» sendte henne til
// Oversikt, uten et ord om hvorfor, etter at flaten nettopp hadde lovet
// henne resten av listen der.
//
// Knappen bærer derfor scopet til flaten den peker på, og finnes ikke
// uten det. Regelen ligger her og ikke på hvert kallsted, fordi en
// knapp uten scope er nøyaktig den formen som glapp: `#/beslutninger`
// har scopet nøkkeltallflaten selv krever og var trygg ved en
// tilfeldighet, ikke ved en kontroll. En ny knapp må nå navngi hvor den
// fører.
function flateknapp(ctx, nokkel, hash, scope) {
  if (!harScope(ctx, scope)) return null;
  const b = el("button", { class: "knapp liten", type: "button",
    text: t(nokkel) });
  b.addEventListener("click", () => { window.location.hash = hash; });
  return b;
}
