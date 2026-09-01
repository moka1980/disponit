// Dokument- og malagenten (M-5 v1, migrasjon 094) — malregisteret og
// utfyllingen.
//
// FLATENS VIKTIGSTE JOBB ER Å VISE HULLENE. Et påkrevd felt uten dekning
// i inndataene er ikke en tom plass i en tekst; det er en NAVNGITT rad
// med ordet «Mangler» ved siden av seg, i tekst, og en oppsummering over
// dokumentet som sier hvor mange og hvilke. En malmotor som skjuler et
// hull har akkurat gjort det den finnes for å ikke gjøre.
//
// TEKSTNODER, IKKE SANDKASSE-IFRAME — og valget er ikke bekvemmelighet.
// Rekrutteringsflaten viser kandidatdokumenter i `<iframe sandbox="">`
// med srcdoc/blob (#304/#306, `frame-src blob:` i CSP), og den veien
// finnes altså. Men den finnes for å INNESTENGE markup vi ikke har
// forfattet. M-5 v1 har ingen: `m5_fyll_mal` returnerer en liste av
// KOMPONENTER med ren tekst, det lagres ingen HTML noe sted, og det
// produseres ingen. En iframe her ville ikke bare vært unødvendig — den
// ville motarbeidet flatens viktigste jobb: innholdet i en srcdoc-ramme
// er et EGET dokument, som axe ikke går inn i og som en skjermleser
// annonserer som «ramme». Et manglende felt må stå i den samme
// tilgjengelighetstreet som resten av siden for å kunne bli lest opp,
// telles og hoppes til. Kommer DOCX/PDF-rendring i v2, kommer den med
// markup vi ikke forfattet — og DA er sandkassen svaret.
//
// innerHTML-forbudet (V6) står uansett: all tekst går som tekstnode
// gjennom `el({ text })`, og all etikett gjennom `t()`.
//
// v1-AVGRENSNING SAGT ÆRLIG I FLATEN: versjoner FORFATTES ikke her.
// Å skrive en komponentliste med låste klausuler og feltdeklarasjoner er
// en editor, ikke et skjema, og en halvferdig editor ville laget maler
// ingen kan publisere. Flaten oppretter familier, viser registeret,
// publiserer/trekker tilbake — og fyller ut. Forfattingen går gjennom
// API-et i v1, og seksjonen sier det med en setning i stedet for å
// tilby en knapp som ikke holder.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, fyllMal, hentJson, nyIdempotensnokkel,
  opprettMalfamilie, publiserMalversjon, trekkTilbakeMalversjon,
} from "../api.js";
import { Tidspunkt } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, kvRad, medStatus } from "./felles.js";

// Skrivemyndigheten i registeret. Samme scope serveren krever
// (`bestilling:opprett` i RUTESCOPE) — menyen og knappene lover aldri
// noe API-et svarer 403 på. Utfyllingen står bevisst UTENFOR: den bærer
// lesescopet, fordi den bare leser.
const SKRIVESCOPE = "bestilling:opprett";

function statusOrd(status) {
  // Tilstanden som ORD, aldri som farge alene — og en ukjent status
  // vises RÅ i stedet for som en halvoversatt «ui.dokumentmal…»-streng.
  return t(`ui.dokumentmal.status.${status}`, status);
}

// ---------------------------------------------------------------------
// Utfyllingsvisningen — flatens kjerne
// ---------------------------------------------------------------------

// Ett dokument, tegnet av komponentlisten serveren returnerte.
//
// Et felt uten dekning blir IKKE en tom plass: raden bærer feltnøkkelen,
// ordet «Mangler» og `aria-label` med samme setning, slik at hullet er
// like tydelig for en skjermleser som for et øye. Verdien fra serveren
// er `null`, og den blir aldri til en tom streng på veien hit.
function dokument(komponenter) {
  const boks = el("div", { class: "kpi-kort" },
    el("h3", { text: t("ui.dokumentmal.utfylling.dokument") }));
  const liste = el("ol", { class: "maldokument" });
  for (const k of komponenter) {
    const li = el("li");
    if (k.komponenttype === "felt") {
      li.append(el("span", { class: "feltnavn", text: k.feltnokkel }), " ");
      if (k.dekket) {
        li.append(el("span", { text: k.tekst }));
      } else {
        // ORDET, ikke en farge og ikke et tomrom.
        li.append(el("strong", {
          text: k.paakrevd
            ? t("ui.dokumentmal.utfylling.mangler")
            : t("ui.dokumentmal.utfylling.ikke_utfylt"),
        }));
      }
    } else {
      li.append(el("span", { text: k.tekst }));
      // Låste klausuler MERKES SOM LÅST, i tekst. Låsen bor i
      // datamodellen (en klausul har ingen feltnøkkel å overstyres
      // gjennom); merket her forteller leseren hvorfor den ikke kan
      // endres, det håndhever den ikke.
      if (k.laast) {
        li.append(" ", el("span", { class: "merke",
          text: t("ui.dokumentmal.utfylling.laast") }));
      }
    }
    liste.append(li);
  }
  boks.append(liste);
  return boks;
}

function manglerListe(mangler, felt) {
  const s = el("section", { class: "kpi-kort" },
    el("h3", { text: t("ui.dokumentmal.utfylling.mangler_tittel") }));
  if (!mangler.length) {
    s.append(el("p", { text: t("ui.dokumentmal.utfylling.fullstendig") }));
    return s;
  }
  // Antallet FØRST — den som fyller ut skal se at noe står igjen uten å
  // lese hele dokumentet.
  s.append(el("p", { role: "status",
    text: t("ui.dokumentmal.utfylling.mangler_antall")
      .replace("{n}", String(mangler.length)) }));
  const ul = el("ul");
  for (const nokkel of mangler) {
    const f = felt.find((x) => x.feltnokkel === nokkel);
    ul.append(el("li", { text: f ? `${nokkel} — ${f.beskrivelse}` : nokkel }));
  }
  s.append(ul);
  return s;
}

function utfyllingsSeksjon(versjon, ctx) {
  const s = el("section", { class: "kpi-kort" },
    el("h3", { text: t("ui.dokumentmal.utfylling.tittel") }));
  const felt = versjon.felt || [];
  const utfall = el("div", { "aria-live": "polite" });
  if (versjon.status !== "publisert") {
    // En knapp som alltid feiler er en løgn om hva systemet kan: bare en
    // mal som ER i kraft kan fylles ut (døren avviser resten uansett).
    s.append(el("p", { class: "muted",
      text: t("ui.dokumentmal.utfylling.kun_publisert") }));
    return s;
  }
  const skjema = el("form", { class: "kv-skjema" });
  const felter = new Map();
  for (const f of felt) {
    const id = `mal-${versjon.versjon_id}-${f.feltnokkel}`;
    const inn = el("input", { id, name: f.feltnokkel, type: "text" });
    felter.set(f.feltnokkel, inn);
    skjema.append(
      el("label", { for: id,
        text: f.paakrevd
          ? t("ui.dokumentmal.utfylling.paakrevd_etikett")
            .replace("{felt}", f.beskrivelse)
          : f.beskrivelse }),
      inn);
  }
  if (!felt.length) {
    skjema.append(el("p", { class: "muted",
      text: t("ui.dokumentmal.utfylling.ingen_felt") }));
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.dokumentmal.utfylling.knapp") });
  skjema.append(knapp);

  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled) return;
    knapp.disabled = true;
    // TOMME FELT SENDES IKKE SOM TOMME STRENGER. En tom streng er
    // nettopp den «utfyllingen» invarianten finnes for å hindre; klienten
    // utelater nøkkelen, og serveren rapporterer den som manglende.
    // (Døren behandler også en tom streng som fravær — dette er
    // ergonomi, ikke porten.)
    const verdier = {};
    for (const [nokkel, inn] of felter) {
      if (inn.value.trim()) verdier[nokkel] = inn.value;
    }
    let svar;
    try {
      svar = await fyllMal(versjon.versjon_id, verdier);
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(utfall, el("p", { role: "alert",
        text: e && e.status === 409
          ? t("ui.dokumentmal.skjema.tilstand_nei")
          : t("ui.dokumentmal.skjema.feil") }));
      return;
    }
    knapp.disabled = false;
    sett(utfall,
      manglerListe(svar.mangler || [], felt),
      dokument(svar.komponenter || []));
  });

  s.append(skjema, utfall);
  return s;
}

// ---------------------------------------------------------------------
// Registeret
// ---------------------------------------------------------------------

function overgangsknapper(versjon, ctx, last) {
  const rad = el("div", { class: "knapperad" });
  const utfall = el("span", { "aria-live": "polite" });
  const lag = (nokkel, kall) => {
    const knapp = el("button", { type: "button",
      text: t(`ui.dokumentmal.knapp.${nokkel}`) });
    // Én nøkkel per intensjon (PR-014 R1): nullstilles ved 4xx, fordi et
    // avvist forsøk har forbrukt nøkkelen.
    let idem = null;
    knapp.addEventListener("click", async () => {
      if (knapp.disabled) return;
      knapp.disabled = true;
      if (!idem) idem = nyIdempotensnokkel();
      try {
        await kall(idem);
      } catch (e) {
        knapp.disabled = false;
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        if (e && e.status >= 400 && e.status < 500) idem = null;
        sett(utfall, el("span", { role: "alert",
          text: e && e.status === 409
            ? t("ui.dokumentmal.skjema.tilstand_nei")
            : t("ui.dokumentmal.skjema.feil") }));
        return;
      }
      idem = null;
      knapp.disabled = false;
      last();
    });
    return knapp;
  };
  // Knappene følger TILSTANDEN, ikke bare scopet: et utkast publiseres,
  // en publisert versjon trekkes tilbake, en tilbaketrukket er terminal.
  if (versjon.status === "utkast") {
    rad.append(lag("publiser",
      (idem) => publiserMalversjon(versjon.versjon_id, idem)));
  } else if (versjon.status === "publisert") {
    rad.append(lag("trekk_tilbake",
      (idem) => trekkTilbakeMalversjon(versjon.versjon_id, idem)));
  }
  rad.append(utfall);
  return rad;
}

function versjonstabell(familie) {
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.dokumentmal.versjoner.caption")
      .replace("{familie}", familie.navn) }));
  tabell.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.dokumentmal.kolonne.versjon") }),
    el("th", { scope: "col", text: t("ui.dokumentmal.kolonne.status") }),
    el("th", { scope: "col", text: t("ui.dokumentmal.kolonne.komponenter") }),
    el("th", { scope: "col", text: t("ui.dokumentmal.kolonne.felt") }),
    el("th", { scope: "col", text: t("ui.dokumentmal.kolonne.opprettet") }),
    el("th", { scope: "col", text: t("ui.dokumentmal.kolonne.publisert") }))));
  const tbody = el("tbody");
  for (const v of familie.versjoner) {
    const laaste = (v.komponenter || []).filter((k) => k.laast).length;
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: String(v.versjonsnr) }),
      el("td", { text: statusOrd(v.status) }),
      // Antall låste klausuler står i REGISTERET også, ikke bare i
      // dokumentet: den som velger en mal skal se at den bærer bundet
      // tekst før hun fyller den ut.
      el("td", { text: laaste
        ? t("ui.dokumentmal.komponenter_med_laaste")
          .replace("{n}", String((v.komponenter || []).length))
          .replace("{l}", String(laaste))
        : String((v.komponenter || []).length) }),
      el("td", { text: String((v.felt || []).length) }),
      el("td", {}, Tidspunkt(v.opprettet, {})),
      el("td", {}, v.publisert
        ? Tidspunkt(v.publisert, {})
        : el("span", { text: t("ui.dokumentmal.ikke_publisert") }))));
  }
  tabell.append(tbody);
  return tabell;
}

function familieSeksjon(familie, ctx, last) {
  const kanSkrive = harScope(ctx, SKRIVESCOPE);
  const art = el("article", { class: "kpi-kort" },
    el("h2", { text: familie.navn }));
  const dl = el("dl", { class: "kv-liste" });
  kvRad(dl, t("ui.dokumentmal.familie.beskrivelse"),
    familie.beskrivelse || t("ui.dokumentmal.familie.ingen_beskrivelse"));
  kvRad(dl, t("ui.dokumentmal.familie.opprettet"),
    Tidspunkt(familie.opprettet, {}));
  art.append(dl);
  if (!familie.versjoner.length) {
    art.append(el("p", { class: "muted",
      text: t("ui.dokumentmal.versjoner.ingen") }));
    return art;
  }
  art.append(versjonstabell(familie));
  for (const v of familie.versjoner) {
    const vart = el("section", { class: "kpi-kort" },
      el("h3", { text: t("ui.dokumentmal.versjon.tittel")
        .replace("{n}", String(v.versjonsnr))
        .replace("{status}", statusOrd(v.status)) }));
    if (kanSkrive) vart.append(overgangsknapper(v, ctx, last));
    vart.append(utfyllingsSeksjon(v, ctx));
    art.append(vart);
  }
  return art;
}

function nyFamilieSkjema(ctx, last) {
  const boks = el("div", { class: "skjemaboks" });
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema" });
  const navnFelt = el("input", { id: "ny-malfamilie-navn", name: "navn",
    type: "text", required: true });
  const beskrivelseFelt = el("input", { id: "ny-malfamilie-beskrivelse",
    name: "beskrivelse", type: "text" });
  const knapp = el("button", { type: "submit",
    text: t("ui.dokumentmal.knapp.ny_familie") });
  skjema.append(
    el("label", { for: "ny-malfamilie-navn",
      text: t("ui.dokumentmal.familie.navn") }), navnFelt,
    el("label", { for: "ny-malfamilie-beskrivelse",
      text: t("ui.dokumentmal.familie.beskrivelse") }), beskrivelseFelt,
    knapp);
  let idem = null;
  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled) return;
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    try {
      await opprettMalfamilie(navnFelt.value,
        beskrivelseFelt.value.trim() || null, idem);
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      sett(utfall, el("span", { role: "alert",
        text: t("ui.dokumentmal.skjema.feil") }));
      return;
    }
    navnFelt.value = "";
    beskrivelseFelt.value = "";
    idem = null;
    knapp.disabled = false;
    sett(utfall, el("span", { text: t("ui.dokumentmal.skjema.ok") }));
    last();
  });
  boks.append(el("h2", { text: t("ui.dokumentmal.ny_familie.tittel") }),
    skjema,
    // Den ærlige v1-setningen, ikke en knapp som ikke holder.
    el("p", { class: "muted", text: t("ui.dokumentmal.ny_versjon.v1notat") }),
    utfall);
  return boks;
}

export function visDokumentmal(hoved, ctx) {
  const hode = () => flateHode(t("ui.dokumentmal.tittel"),
    t("ui.dokumentmal.undertittel"));
  sett(hoved, ...hode());
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kropp);
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/dokumentmal"),
    (d) => {
      sett(hoved, ...hode(), kropp);
      const familier = d.familier || [];
      const deler = [];
      if (harScope(ctx, SKRIVESCOPE)) deler.push(nyFamilieSkjema(ctx, last));
      if (!familier.length) {
        deler.push(el("p", { class: "muted",
          text: t("ui.dokumentmal.ingen_familier") }));
      }
      for (const f of familier) deler.push(familieSeksjon(f, ctx, last));
      sett(kropp, ...deler);
    });
  last();
}
