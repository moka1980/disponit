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
import { t } from "../i18n.js";
import { hentJson, UautorisertFeil, IngenTilgangFeil } from "../api.js";
import { Tidspunkt, Feiltilstand, TilgangsVakt } from "../komponenter.js";
import { flateHode } from "./felles.js";

const VINDUER = ["24t", "7d", "30d"];

// Én søylerad: tallet som TEKST først, bredden som visuell støtte.
// Bredden er relativ til partisjonens største verdi — en presentasjons-
// skala, ingen avledet størrelse (tallet ved siden av er sannheten).
function soyle(antall, maks) {
  const bredde = maks > 0 ? Math.round((antall / maks) * 100) : 0;
  const rot = el("div", { class: "kpi-soyle", "aria-hidden": "true" });
  rot.append(el("div", { class: "kpi-soyle-fyll" }));
  rot.lastChild.style.width = `${bredde}%`;
  return rot;
}

function nokkelTekst(kortNokkel, verdi) {
  const spesifikk = `ui.nokkeltall.verdi.${kortNokkel}.${verdi}`;
  const generell = `ui.nokkeltall.verdi.${verdi}`;
  const s = t(spesifikk);
  if (s !== spesifikk) return s;
  const g = t(generell);
  return g === generell ? verdi : g;
}

// Ett partisjonskort: <table> med rader (nokkel, antall, søyle) + total.
// Tomt vindu er en EKSPLISITT rad: 0 og «ingen» — aldri et skjult kort.
function partisjonstabell(kortNokkel, caption, partisjon) {
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: caption }));
  const thead = el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.nokkeltall.kolonne.hva") }),
    el("th", { scope: "col", text: t("ui.nokkeltall.kolonne.antall") }),
    el("th", { scope: "col", class: "vh",
               text: t("ui.nokkeltall.kolonne.soyle") })));
  const tbody = el("tbody");
  const deler = Object.entries(partisjon.deler);
  const maks = Math.max(0, ...deler.map(([, n]) => n));
  if (!deler.length) {
    tbody.append(el("tr", {},
      el("td", { text: t("ui.nokkeltall.ingen") }),
      el("td", { text: "0" }),
      el("td", {})));
  }
  for (const [nokkel, antall] of deler) {
    tbody.append(el("tr", {},
      el("td", { text: nokkelTekst(kortNokkel, nokkel) }),
      el("td", { text: String(antall) }),
      el("td", {}, soyle(antall, maks))));
  }
  const tfoot = el("tfoot", {}, el("tr", {},
    el("th", { scope: "row", text: t("ui.nokkeltall.total") }),
    el("td", { text: String(partisjon.total) }),
    el("td", {})));
  tabell.append(thead, tbody, tfoot);
  return tabell;
}

// Lukkede saker: RADFAKTA med et visningstak. Taket er aldri stille —
// er totalen i vinduet større enn antall rader, står det i klartekst
// hvor mange av hvor mange som vises, rett ved tabellen.
function lukkedeTabell(rader, totalt, grense) {
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
      el("td", { text: nokkelTekst("unntak", r.kategori) }),
      el("td", {}, Tidspunkt(r.lukket)),
      el("td", { text: varighetTekst(r.varighet_s) })));
  }
  tabell.append(tbody);
  bolk.append(tabell);
  if (totalt > rader.length) {
    bolk.append(el("p", { class: "muted",
      text: t("ui.nokkeltall.lukkede_avkuttet")
        .replace("{vist}", String(rader.length))
        .replace("{totalt}", String(totalt))
        .replace("{grense}", String(grense)) }),
      lenkeTilUnntak());
  }
  return bolk;
}

// Varighet som klartekst — en OMSKRIVING av sekundtallet (presentasjon),
// ingen beregning over flere rader.
function varighetTekst(sek) {
  if (sek < 60) return t("ui.nokkeltall.varighet_sek")
    .replace("{n}", String(sek));
  if (sek < 3600) return t("ui.nokkeltall.varighet_min")
    .replace("{n}", String(Math.floor(sek / 60)));
  if (sek < 86400) return t("ui.nokkeltall.varighet_timer")
    .replace("{n}", String(Math.floor(sek / 3600)));
  return t("ui.nokkeltall.varighet_dogn")
    .replace("{n}", String(Math.floor(sek / 86400)));
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

  const last = () => {
    sett(kropp, el("p", { class: "muted", text: t("ui.laster") }));
    hentJson("/v1/nokkeltall", { vindu }).then((d) => {
      sett(tilstand,
        el("b", { text: String(d.apne_naa) }),
        el("span", { text: " " + t("ui.nokkeltall.apne_naa_tekst") }));
      sett(meta,
        `${t("ui.nokkeltall.vindu_valgt")}: `, Tidspunkt(d.vindu_start),
        " – ", Tidspunkt(d.vindu_slutt), ` (${d.tidssone})`);
      const kort = [];
      const besl = partisjonstabell("beslutning",
        t("ui.nokkeltall.kort.beslutninger"), d.beslutninger);
      kort.push(el("section", { class: "kpi-kort" }, besl,
        el("p", { class: "muted", text:
          `${t("ui.nokkeltall.reservasjoner")}: ${d.frekvensreservasjoner}` }),
        lenkeTilBeslutninger()));
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
        lukkedeTabell(d.unntak_lukkede, d.unntak_lukkede_totalt,
                      d.unntak_lukkede_grense)));
      // Tick-kortet: 0 rader er en SETNING, ikke en tom graf.
      kort.push(el("section", { class: "kpi-kort" },
        d.tick.total === 0
          ? el("div", {},
              el("h3", { text: t("ui.nokkeltall.kort.tick") }),
              el("p", { class: "muted",
                text: t("ui.nokkeltall.ingen_tick") }))
          : partisjonstabell("tick",
              t("ui.nokkeltall.kort.tick"), d.tick)));
      sett(kropp, ...kort);
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e instanceof IngenTilgangFeil) { sett(kropp, TilgangsVakt({})); return; }
      sett(kropp, Feiltilstand({ paaProvIgjen: last }));
    });
  };
  velger.addEventListener("change", () => { vindu = velger.value; last(); });
  last();
}

function lenkeTilBeslutninger() {
  return flateknapp("ui.nokkeltall.til_beslutninger", "#/beslutninger");
}

// Veien videre når lukkede-listen er avkuttet: unntaksflaten har hele
// settet — nøkkeltallflaten paginerer aldri, den VISER.
function lenkeTilUnntak() {
  return flateknapp("ui.nokkeltall.til_unntak", "#/unntak");
}

function flateknapp(nokkel, hash) {
  const b = el("button", { class: "knapp liten", type: "button",
    text: t(nokkel) });
  b.addEventListener("click", () => { window.location.hash = hash; });
  return b;
}
