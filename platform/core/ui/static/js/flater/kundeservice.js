// Kundeserviceagenten (M-17 v1, PR-A) — HENVENDELSESREGISTERET.
//
// FLATENS VIKTIGSTE JOBB er å vise hva som står og venter: hvilke
// henvendelser som er uklassifiserte, hvilke som er ubesvarte forbi
// fristen, og hvilke som er merket mistenkelige uten å være behandlet.
// Som TEKST, ikke bare farge (WCAG 1.4.1): «Over svarfristen» står som
// ord i sin egen celle.
//
// KØEN OG INNHOLDET ER TO HANDLINGER, og flaten er bygget rundt det.
// Listen viser hvem som spurte (som HASH), når, og hvor gammelt — aldri
// hva de skrev. Teksten hentes av et EGET kall per henvendelse, bak et
// eget scope, når et menneske faktisk åpner den. Et listekall som dro
// med seg hver kundetekst ville gjort ett skjermbilde til en full
// eksport av persondata.
//
// FLATEN VISER, DEN REGNER IKKE. `alder_dogn`, funnlisten og
// sammendragets tall er regnet i BASEN (102s lesedører), nettopp for at
// flaten ikke skal trekke to datoer fra hverandre eller telle en
// avkortet liste (M-16-regelen).
//
// DET FINNES INGEN «SEND»-KNAPP, og fraværet er dommen: flaten sender
// ingenting. Utkastet får tre dommer: `forkastet`, `brukt_manuelt` (et
// MENNESKE sendte selv — sporet som lar henvendelsen lukkes som
// «besvart») og, fra ARC B (160), `godkjent`: et menneskes ja til at
// PLATTFORMEN sender svaret innenfor policyen. Selve sendingen er
// eiermodulens, aldri flatens.
//
// TABELLEN ER EKTE (m16-formen): <caption>, th[scope=col] på kolonnene
// og th[scope=row] på cellen som navngir raden. Wrapperen `.tablewrap`
// er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, avgjorUtkast, hentJson, henvendelseTilUnntakskoe,
  klassifiserHenvendelse, lagreUtkast, lukkHenvendelse,
  hentStilleregler, nyIdempotensnokkel,
  settKundeserviceavsender, settStilleregler,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

const PRIORITETER = ["kritisk", "hoy", "normal", "lav"];
const TEMAER = ["faktura", "leveranse", "teknisk", "salg", "klage",
                "annet"];
const HANDLINGSTYPER = ["svar_kreves", "til_info", "oppgave", "mote",
                        "nyhetsbrev", "mistenkelig"];

// FUNNTYPE → MERKETEKST. Kartet er en LUKKET tabell og ikke en
// strengbygging: en funntype flaten ikke kjenner skal ikke bli et merke
// som heter «ui.kundeservice.merke_<noe>» på skjermen. Porten i
// `test_m17_kundeservice.py` måler at basens tre og flatens tre er de
// samme.
const MERKE = {
  uklassifisert_over_grense: "ui.kundeservice.merke_uklassifisert",
  ubesvart_over_grense: "ui.kundeservice.merke_ubesvart",
  mistenkelig_uten_behandling: "ui.kundeservice.merke_mistenkelig",
};

// Alderskolonnens ORD. Ett tall inn, én setning ut — ingen utregning.
//
// ENTALL HAR SIN EGEN NØKKEL (M-21/M-34/M-13-lærdommen): locale-settet
// har ingen pluralmaskineri, og «1 days» ville stått på nøyaktig den
// raden et menneske leser først.
export function alderTekst(dogn) {
  if (typeof dogn !== "number") return "—";
  if (dogn <= 0) return t("ui.kundeservice.alder_i_dag");
  return dogn === 1
    ? t("ui.kundeservice.alder_ett_dogn")
    : t("ui.kundeservice.alder_dogn").replace("{dogn}", String(dogn));
}

// Klassifiseringen som én lesbar celle — eller den ærlige setningen om
// at den mangler. En tom celle ville lest som «normal», og det er
// nettopp den forvekslingen sveipen finnes for å hindre.
export function klassifiseringTekst(h) {
  if (!h.prioritet) return t("ui.kundeservice.uklassifisert");
  return [t(`ui.kundeservice.prioritet.${h.prioritet}`),
          t(`ui.kundeservice.tema.${h.tema}`),
          t(`ui.kundeservice.handlingstype.${h.handlingstype}`)].join(" · ");
}

/** Kort gjenkjennelsesmerke av en leverandørreferanse.
 *
 * SETT PÅ SKJERMEN 15/9: Graphs melding-id-er er ~150 tegn base64, og
 * `celle-id` (22ch, `overflow-wrap: anywhere`) rakk ikke over dem —
 * kolonnen veltet inn i Kanal, Mottatt og Alder, og køen ble uleselig.
 * Testfixturen hadde `MSG-2026-0001`, tretten tegn; porten hadde aldri
 * sett en ekte id.
 *
 * Ingen leser 150 tegn; de MATCHER dem. Derfor hode + hale, som en
 * git-sha: nok til å kjenne igjen, aldri nok til å fylle en rad. Hele
 * verdien står i `title` og i detaljpanelet.
 */
export function kortref(ref) {
  const r = String(ref ?? "");
  if (r.length <= 24) return r;
  return `${r.slice(0, 10)}…${r.slice(-8)}`;
}

/** Avsenderen slik listen kan vise den: masken, aldri adressen. */
export function avsenderTekst(h) {
  return h.har_avsender && h.avsender_maske
    ? h.avsender_maske
    : t("ui.kundeservice.avsender.ukjent");
}

function korad(h, ctx, apneDetalj) {
  const rad = el("tr", {});
  // REFERANSEN NAVNGIR raden — det er den et menneske slår opp i
  // innboksen. `celle-id` står på <th>, ikke på et <span> inni, fordi
  // `max-width` ikke gjør noe på et inline-element.
  // AVSENDEREN FØRST. Det et menneske skanner en kø etter er HVEM —
  // ikke leverandørens nøkkel. Masken er alt listen får lov å bære
  // (listen bærer aldri kundeteksten); adressen og emnet kommer først
  // når raden åpnes.
  rad.append(el("th", { scope: "row", class: "celle-tekst",
                        text: avsenderTekst(h) }));
  rad.append(el("td", { class: "celle-id", title: h.ekstern_ref,
                        text: kortref(h.ekstern_ref) }));
  rad.append(el("td", { text: t(`ui.kundeservice.kanal.${h.kanal}`) }));
  rad.append(el("td", { text: h.mottatt.slice(0, 10) }));

  const alderscelle = el("td", {},
    el("span", { text: alderTekst(h.alder_dogn) }));
  for (const funn of h.apne_funn || []) {
    // MERKENE ER TEKST. Dette er flatens viktigste opplysning på raden.
    if (MERKE[funn]) {
      alderscelle.append(" ", el("strong", { class: "merke",
        text: t(MERKE[funn]) }));
    }
  }
  if (h.i_unntakskoe) {
    alderscelle.append(" ", el("strong", { class: "merke",
      text: t("ui.kundeservice.merke_i_koe") }));
  }
  rad.append(alderscelle);

  rad.append(el("td", { class: "celle-tekst",
                        text: klassifiseringTekst(h) }));
  const utkastcelle = el("td", { class: "celle-tall",
    text: String(h.antall_utkast) });
  if (h.brukt_utkast) {
    utkastcelle.append(" ", el("span", { class: "muted",
      text: t("ui.kundeservice.utkast_brukt") }));
  }
  rad.append(utkastcelle);

  const handling = el("td", {});
  const knapp = el("button", { type: "button",
    text: t("ui.kundeservice.knapp.apne") });
  knapp.addEventListener("click", () => apneDetalj(h));
  handling.append(knapp);
  rad.append(handling);
  return rad;
}

function koTabell(koe, ctx, apneDetalj) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.kundeservice.koe.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col",
               text: t("ui.kundeservice.kolonne.avsender") }),
    el("th", { scope: "col",
               text: t("ui.kundeservice.kolonne.referanse") }),
    el("th", { scope: "col", text: t("ui.kundeservice.kolonne.kanal") }),
    el("th", { scope: "col",
               text: t("ui.kundeservice.kolonne.mottatt") }),
    el("th", { scope: "col", text: t("ui.kundeservice.kolonne.alder") }),
    el("th", { scope: "col",
               text: t("ui.kundeservice.kolonne.klassifisering") }),
    el("th", { scope: "col", text: t("ui.kundeservice.kolonne.utkast") }),
    el("th", { scope: "col",
               text: t("ui.kundeservice.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const h of koe) tbody.append(korad(h, ctx, apneDetalj));
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function felt(id, tekst, kontroll, hjelp) {
  return el("div", { class: "felt" },
    el("label", { for: id, text: t(tekst) }),
    kontroll,
    hjelp ? el("p", { class: "muted", text: t(hjelp) }) : null);
}

// Rammen skjemaene deler: idempotensnøkkel per intensjon, knappelås,
// feilvisning og gjenlasting.
function skjemaramme(ctx, last, { skjema, knapp, utfall, send,
                                  tilbakestill, okNokkel, kvitter }) {
  // Én nøkkel per intensjon (PR-014 R1): nullstilles når innholdet
  // endres, og ved 4xx — et avvist forsøk har FORBRUKT nøkkelen.
  let idem = null;
  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("change", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled) return;
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    try {
      await send(idem);
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.kundeservice.feil.tilstand")
          : t("ui.kundeservice.feil.generell") }));
      return;
    }
    idem = null;
    knapp.disabled = false;
    if (tilbakestill) tilbakestill();
    meldLive(t(okNokkel));
    // KVITTERINGEN SKAL OVERLEVE TEGNINGEN. `last()` bygger listen,
    // panelet og skjemaene på nytt, og en melding satt i skjemaets eget
    // `utfall` forsvant i samme øyeblikk den ble satt — brukeren trykket,
    // så skjermen blinke, og satt igjen uten å vite om det gikk bra.
    // Suksessen går derfor til flatens egen kvitteringslinje, som lever
    // utenfor tegningen. FEILEN blir stående i skjemaet, der den hører
    // hjemme: den veien tegner ikke om. (CodeRabbit på M-24, samme feil
    // i alle fem flatene i klyngen.)
    kvitter(t(okNokkel));
    await last();
  });
}

// DETALJPANELET. Åpnes fra en rad, og henter INNHOLDET i et eget kall —
// det er her persondata faktisk krysser skjermen, og derfor er det her
// scopet gjelder.
//
// UTEN `kundeservice:innhold` vises panelet likevel: klassifisering
// og købeslutning er arbeid som ikke krever å lese teksten, og en flate
// som skjulte hele panelet ville sagt at den som ikke får lese heller
// ikke får jobbe. Setningen i stedet for teksten er den ærlige formen.
export function bestillingstekst(b) {
  const utfall = String(b.utfall || "");
  const nokkel = utfall.startsWith("feil:")
    ? "ui.kundeservice.bestilling.utfall.feil"
    : `ui.kundeservice.bestilling.utfall.${utfall}`;
  return t("ui.kundeservice.bestilling.linje")
    .replace("{utfall}", t(nokkel))
    .replace("{dato}", String(b.bestilt_ts || "").slice(0, 10));
}

// AVSENDEREN ER DET KUNDEN SER. Uten profil sies det høyt — før det
// første svaret går ut i tenantens id i stedet for et navn.
function avsenderSeksjon(a) {
  const boks = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.kundeservice.avsender.tittel") }));
  if (!a || !a.avsender_navn) {
    boks.append(el("p", {}, el("strong", {
      text: t("ui.kundeservice.avsender.ingen") })));
    return boks;
  }
  boks.append(el("p", {
    text: t("ui.kundeservice.avsender.navn").replace("{navn}", a.avsender_navn) }));
  boks.append(el("p", { class: a.svar_til ? "" : "muted",
    text: a.svar_til
      ? t("ui.kundeservice.avsender.svar_til").replace("{adresse}", a.svar_til)
      : t("ui.kundeservice.avsender.uten_svar_til") }));
  if (a.signatur) {
    boks.append(el("p", { class: "muted",
      text: t("ui.kundeservice.avsender.signatur").replace("{signatur}", a.signatur) }));
  }
  return boks;
}

function avsenderSkjema(ctx, last, kvitter, a) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const navn = el("input", { id: "ks-avs-navn", name: "avsender_navn",
    type: "text", required: true, maxlength: 120,
    value: (a && a.avsender_navn) || "" });
  const svarTil = el("input", { id: "ks-avs-svar", name: "svar_til",
    type: "email", maxlength: 254, value: (a && a.svar_til) || "" });
  const signatur = el("textarea", { id: "ks-avs-signatur", name: "signatur",
    maxlength: 500, rows: "3" });
  signatur.value = (a && a.signatur) || "";
  const knapp = el("button", { type: "submit",
    text: t("ui.kundeservice.knapp.lagre_avsender") });
  skjema.append(
    felt("ks-avs-navn", "ui.kundeservice.skjema.avsender_navn", navn,
         "ui.kundeservice.skjema.avsender_navn_hjelp"),
    felt("ks-avs-svar", "ui.kundeservice.skjema.svar_til", svarTil,
         "ui.kundeservice.skjema.svar_til_hjelp"),
    felt("ks-avs-signatur", "ui.kundeservice.skjema.signatur", signatur,
         "ui.kundeservice.skjema.signatur_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.kundeservice.skjema.avsender_ok",
    send: (idem) => settKundeserviceavsender({
      avsender_navn: navn.value.trim(),
      svar_til: svarTil.value.trim() || null,
      signatur: signatur.value.trim() || null,
    }, idem),
    tilbakestill: () => {},
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.kundeservice.skjema.avsender_tittel") }),
    skjema, utfall);
}

//: Stille avsendere (204). Det tenanten kan si om en avsender.
const REGELARTER = ["domene", "adresse"];
const REGELHANDLINGER = ["til_info", "nyhetsbrev", "oppgave", "mistenkelig"];

/** Én regel som tekst — domenet som det er, adressen som «hash …»:
 *  adressen ble aldri lagret, og kan derfor ikke vises igjen. */
export function regelTekst(r) {
  const hva = r.art === "adresse"
    ? t("ui.kundeservice.stille.adresse_hash").replace("{hash}",
                                                       r.monster.slice(0, 8))
    : r.monster;
  return `${hva} → ${t(`ui.kundeservice.handlingstype.${r.handlingstype}`)}`;
}

/** Reglene slik de sendes tilbake: bare det API-et tar imot. For en
 *  adresseregel som alt finnes, er «monster» hashen — API-et hasher
 *  bare det som ser ut som en adresse, så hashen går uendret gjennom. */
function tilKropp(regler) {
  return regler.map((r) => ({ art: r.art, monster: r.monster,
                              handlingstype: r.handlingstype }));
}

function stilleSeksjon(ctx, last, kvitter) {
  const boks = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.kundeservice.stille.tittel") }),
    el("p", { class: "muted", text: t("ui.kundeservice.stille.forklaring") }));
  const liste = el("ul", { class: "stille-liste" });
  const status = el("p", { "aria-live": "polite" });
  boks.append(liste, status);
  let regler = [];
  // INGEN SKRIVING FØR LISTEN ER LASTET (CodeRabbit, major). Settet
  // sendes HELT hver gang, så en innsending før GET-en var ferdig ville
  // sendt «[] + den nye» — og slettet alle eksisterende regler i
  // stillhet. Knappene finnes derfor ikke, eller er sperret, til
  // `lastet` er sann. Og ÉN lagring om gangen: to samtidige ville
  // konkurrert om hvilket snapshot som vinner.
  let lastet = false;
  let lagrer = false;
  const kanSkrive = harScope(ctx, "bestilling:opprett");

  // HELE SETTET, HVER GANG. Å sende én regel om gangen ville krevd en dør
  // som lar listen stå halvferdig mellom to kall; purreplanen valgte det
  // samme. Idempotensnøkkelen er per innsending.
  const lagre = async (nye) => {
    if (!lastet || lagrer) return;
    lagrer = true;
    try {
      await settStilleregler(tilKropp(nye), nyIdempotensnokkel());
    } finally {
      lagrer = false;
    }
    const ok = t("ui.kundeservice.stille.ok");
    meldLive(ok);
    kvitter(ok);
    await last();
  };

  const tegn = () => {
    sett(liste);
    if (!regler.length) {
      liste.append(el("li", { class: "muted",
                              text: t("ui.kundeservice.stille.ingen") }));
      return;
    }
    for (const r of regler) {
      const li = el("li", {}, el("span", { text: regelTekst(r) }));
      if (kanSkrive) {
        const fjern = el("button", { type: "button", class: "knapp",
          "aria-label": t("ui.kundeservice.stille.fjern_aria")
            .replace("{regel}", regelTekst(r)),
          text: t("ui.kundeservice.stille.fjern") });
        fjern.addEventListener("click", async () => {
          fjern.disabled = true;
          try {
            await lagre(regler.filter((x) => x.regel_id !== r.regel_id));
          } catch (e) {
            fjern.disabled = false;
            if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
            sett(status, el("span", { role: "alert",
              text: t("ui.kundeservice.feil.generell") }));
          }
        });
        li.append(" ", fjern);
      }
      liste.append(li);
    }
  };

  hentStilleregler().then((d) => {
    regler = d.regler || [];
    lastet = true;
    tegn();
    if (knapp) knapp.disabled = false;
  })
    .catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(status, el("span", { role: "alert",
        text: t("ui.kundeservice.feil.generell") }));
    });

  // `knapp` deklareres FØR lastingen fullfører (over), så den kan låses
  // opp derfra; uten skriverett finnes den ikke.
  let knapp = null;
  if (!kanSkrive) return boks;

  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const art = el("select", { id: "ks-stille-art", name: "art" });
  for (const v of REGELARTER) {
    art.append(el("option", { value: v,
      text: t(`ui.kundeservice.stille.art.${v}`) }));
  }
  const monster = el("input", { id: "ks-stille-monster", name: "monster",
                                type: "text", required: "", maxlength: "400",
                                autocomplete: "off" });
  const handling = el("select", { id: "ks-stille-handling",
                                  name: "handlingstype" });
  for (const v of REGELHANDLINGER) {
    handling.append(el("option", { value: v,
      text: t(`ui.kundeservice.handlingstype.${v}`) }));
  }
  // SPERRET til listen er lastet — `skjemaramme` sender ikke når knappen
  // er disabled, og det er hele vernet mot «[] + den nye».
  knapp = el("button", { type: "submit", class: "knapp knapp-primaer",
                         disabled: "",
                         text: t("ui.kundeservice.stille.legg_til") });
  const utfall = el("p", { "aria-live": "polite" });
  skjema.append(
    el("div", { class: "felt" },
      el("label", { for: "ks-stille-art",
                    text: t("ui.kundeservice.stille.art") }), art),
    el("div", { class: "felt" },
      el("label", { for: "ks-stille-monster",
                    text: t("ui.kundeservice.stille.monster") }), monster,
      el("p", { class: "hjelp", id: "ks-stille-monster-hjelp",
                text: t("ui.kundeservice.stille.monster_hjelp") })),
    el("div", { class: "felt" },
      el("label", { for: "ks-stille-handling",
                    text: t("ui.kundeservice.skjema.handlingstype") }),
      handling),
    el("div", { class: "skjema-bunn" }, knapp));
  monster.setAttribute("aria-describedby", "ks-stille-monster-hjelp");
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.kundeservice.stille.ok",
    send: async (idem) => {
      if (!lastet || lagrer) {
        // Skal ikke kunne skje (knappen er sperret), men et løp mellom
        // to hendelser skal aldri koste kundens regler.
        const e = new Error("stilleregler ikke lastet"); e.status = 409;
        throw e;
      }
      lagrer = true;
      try {
        await settStilleregler(
          [...tilKropp(regler),
           { art: art.value, monster: monster.value.trim(),
             handlingstype: handling.value }], idem);
      } finally {
        lagrer = false;
      }
    },
    tilbakestill: () => { monster.value = ""; },
  });
  boks.append(el("h3", { text: t("ui.kundeservice.stille.skjema_tittel") }),
              skjema, utfall);
  return boks;
}

function detaljpanel(ctx, last, kvitter, settApen) {
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;

  const overskrift = el("h3", { text: t("ui.kundeservice.detalj.tittel") });
  const merkelinje = el("p", { class: "muted" });
  const emne = el("p", {});
  const kropp = el("p", { class: "celle-tekst" });
  const utkastliste = el("div", {});

  // --- klassifisering ---
  const kSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const prioritet = el("select", { id: "ks-prioritet", name: "prioritet",
    required: true });
  for (const v of PRIORITETER) {
    prioritet.append(el("option", { value: v,
      text: t(`ui.kundeservice.prioritet.${v}`) }));
  }
  prioritet.value = "normal";
  const tema = el("select", { id: "ks-tema", name: "tema",
    required: true });
  for (const v of TEMAER) {
    tema.append(el("option", { value: v,
      text: t(`ui.kundeservice.tema.${v}`) }));
  }
  const handlingstype = el("select", { id: "ks-handlingstype",
    name: "handlingstype", required: true });
  for (const v of HANDLINGSTYPER) {
    handlingstype.append(el("option", { value: v,
      text: t(`ui.kundeservice.handlingstype.${v}`) }));
  }
  const kKnapp = el("button", { type: "submit",
    text: t("ui.kundeservice.knapp.klassifiser") });
  kSkjema.append(
    felt("ks-prioritet", "ui.kundeservice.skjema.prioritet", prioritet),
    felt("ks-tema", "ui.kundeservice.skjema.tema", tema),
    // HJELPETEKSTEN SIER HVA VALGET GJØR. «Svar kreves» er det som gjør
    // en ubesvart henvendelse til et funn; uten forklaringen ville
    // valget sett ut som en etikett.
    felt("ks-handlingstype", "ui.kundeservice.skjema.handlingstype",
         handlingstype, "ui.kundeservice.skjema.handlingstypehjelp"),
    el("div", { class: "skjema-bunn" }, kKnapp));
  skjemaramme(ctx, last, {
    skjema: kSkjema, knapp: kKnapp, utfall, kvitter,
    okNokkel: "ui.kundeservice.skjema.klassifisering_ok",
    send: (idem) => klassifiserHenvendelse(gjeldende.henvendelse_id, {
      prioritet: prioritet.value, tema: tema.value,
      handlingstype: handlingstype.value,
    }, idem),
  });

  // --- utkast ---
  const uSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const utkasttekst = el("textarea", { id: "ks-utkast", name: "tekst",
    required: true, rows: "6" });
  const uKnapp = el("button", { type: "submit",
    text: t("ui.kundeservice.knapp.utkast") });
  uSkjema.append(
    felt("ks-utkast", "ui.kundeservice.skjema.utkast_tekst", utkasttekst,
         "ui.kundeservice.skjema.utkast_teksthjelp"),
    el("div", { class: "skjema-bunn" }, uKnapp));
  skjemaramme(ctx, last, {
    skjema: uSkjema, knapp: uKnapp, utfall, kvitter,
    okNokkel: "ui.kundeservice.skjema.utkast_ok",
    send: (idem) => lagreUtkast(gjeldende.henvendelse_id,
                                { tekst: utkasttekst.value }, idem),
    tilbakestill: () => { utkasttekst.value = ""; },
  });

  // --- unntakskø ---
  const qSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const qBegrunnelse = el("input", { id: "ks-koe", name: "begrunnelse",
    type: "text", required: true, maxlength: 2000 });
  const qKnapp = el("button", { type: "submit",
    text: t("ui.kundeservice.knapp.unntakskoe") });
  qSkjema.append(
    felt("ks-koe", "ui.kundeservice.skjema.unntakskoe_begrunnelse",
         qBegrunnelse, "ui.kundeservice.skjema.unntakskoe_hjelp"),
    el("div", { class: "skjema-bunn" }, qKnapp));
  skjemaramme(ctx, last, {
    skjema: qSkjema, knapp: qKnapp, utfall, kvitter,
    okNokkel: "ui.kundeservice.skjema.unntakskoe_ok",
    send: (idem) => henvendelseTilUnntakskoe(gjeldende.henvendelse_id,
                                             qBegrunnelse.value, idem),
    tilbakestill: () => { qBegrunnelse.value = ""; },
  });

  // --- lukking ---
  const lukkerad = el("div", { class: "skjema-bunn" });
  const lukkKnapp = (utfallverdi, nokkel) => {
    const b = el("button", { type: "button", text: t(nokkel) });
    b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await lukkHenvendelse(gjeldende.henvendelse_id, utfallverdi,
                              nyIdempotensnokkel());
      } catch (e) {
        b.disabled = false;
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        sett(utfall, el("span", { role: "alert",
          text: e && e.status === 409
            ? t("ui.kundeservice.feil.tilstand")
            : t("ui.kundeservice.feil.generell") }));
        return;
      }
      b.disabled = false;
      innhold.hidden = true;
      settApen(null);
      meldLive(t("ui.kundeservice.lukk_ok"));
      // SAMME DOM SOM I `skjemaramme`: kvitteringen hører til flaten,
      // ikke til panelet som lukker seg i neste linje.
      kvitter(t("ui.kundeservice.lukk_ok"));
      await last();
    });
    return b;
  };
  lukkerad.append(lukkKnapp("besvart", "ui.kundeservice.knapp.lukk_besvart"),
                  lukkKnapp("ikke_aktuell",
                            "ui.kundeservice.knapp.lukk_ikke_aktuell"));

  const skriver = harScope(ctx, "bestilling:opprett");
  const leser = harScope(ctx, "kundeservice:innhold");
  innhold.append(overskrift, merkelinje);
  if (leser) {
    innhold.append(el("h4", { text: t("ui.kundeservice.detalj.emne") }),
      emne, el("h4", { text: t("ui.kundeservice.detalj.innhold") }),
      kropp, el("h4", { text: t("ui.kundeservice.detalj.utkast") }),
      utkastliste);
  } else {
    // ÆRLIG OM HVA SOM MANGLER: ikke en tom boks, men en setning om at
    // køen er synlig og teksten ikke.
    innhold.append(el("p", { class: "muted",
      text: t("ui.kundeservice.detalj.uten_innsyn") }));
  }
  if (skriver) {
    innhold.append(
      el("h4", { text: t("ui.kundeservice.skjema.klassifisering") }),
      kSkjema,
      el("h4", { text: t("ui.kundeservice.skjema.utkast_tittel") }),
      uSkjema,
      el("h4", { text: t("ui.kundeservice.skjema.unntakskoe") }),
      qSkjema, lukkerad);
  }
  boks.append(innhold, utfall);
  innhold.hidden = true;

  async function tegnUtkast(hid) {
    sett(utkastliste);
    let d;
    try {
      d = await hentJson(
        `/v1/kundeservice/henvendelse/${encodeURIComponent(hid)}/utkast`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      return;
    }
    const liste = d.utkast || [];
    if (!liste.length) {
      utkastliste.append(el("p", { class: "muted",
        text: t("ui.kundeservice.detalj.ingen_utkast") }));
      return;
    }
    for (const u of liste) {
      // ARC B: statusen som ord, og hva plattformen gjorde med et
      // godkjent utkast (bestilt, i unntakskøen, sendt) — som tekst.
      const kort = el("div", { class: "skjemaboks" },
        el("p", { class: "celle-tekst", text: u.tekst }),
        el("p", { class: "muted",
          text: `${u.opprettet.slice(0, 10)} · ${u.kilde} · `
            + t(`ui.kundeservice.utkaststatus.${u.status}`) }));
      if (u.bestilling) {
        kort.append(el("p", { class: "muted", text: bestillingstekst(u.bestilling) }));
      }
      if (skriver && u.status === "foreslatt") {
        for (const [status, nokkel] of [
          ["forkastet", "ui.kundeservice.knapp.forkast"],
          ["brukt_manuelt", "ui.kundeservice.knapp.brukt"],
          ["godkjent", "ui.kundeservice.knapp.godkjenn"]]) {
          const b = el("button", { type: "button", text: t(nokkel) });
          b.addEventListener("click", async () => {
            b.disabled = true;
            try {
              await avgjorUtkast(u.utkast_id, status,
                                 nyIdempotensnokkel());
            } catch (e) {
              b.disabled = false;
              if (e instanceof UautorisertFeil) {
                ctx.paaUautorisert(); return;
              }
              sett(utfall, el("span", { role: "alert",
                text: t("ui.kundeservice.feil.tilstand") }));
              return;
            }
            meldLive(t("ui.kundeservice.utkast_ok"));
            kvitter(t("ui.kundeservice.utkast_ok"));
            await last();
          });
          kort.append(b);
        }
      }
      utkastliste.append(kort);
    }
  }

  return {
    node: boks,
    async apne(h) {
      gjeldende = h;
      settApen(h.henvendelse_id);
      sett(utfall);
      // 160: adressen finnes eller mangler — masken, aldri adressen.
      merkelinje.textContent = `${h.ekstern_ref} · `
        + `${t(`ui.kundeservice.kanal.${h.kanal}`)} · `
        + `${alderTekst(h.alder_dogn)} · `
        + (h.har_avsender
          ? t("ui.kundeservice.avsender.satt")
            .replace("{maske}", h.avsender_maske || "")
          : t("ui.kundeservice.avsender.mangler"));
      if (h.prioritet) {
        prioritet.value = h.prioritet;
        tema.value = h.tema;
        handlingstype.value = h.handlingstype;
      } else {
        // UKLASSIFISERT SKAL SE UKLASSIFISERT UT. Panelet gjenbrukes for
        // hver rad, så uten dette bærer skjemaet FORRIGE henvendelses
        // dom — og ett klikk på «Lagre klassifisering» ville skrevet den
        // over på denne. Det er ikke en visningsfeil, det er feil data.
        prioritet.value = "normal";
        tema.value = TEMAER[TEMAER.length - 1];
        handlingstype.value = HANDLINGSTYPER[0];
      }
      innhold.hidden = false;
      if (!leser) return;
      emne.textContent = "";
      kropp.textContent = "";
      try {
        const d = await hentJson(
          "/v1/kundeservice/henvendelse/"
          + `${encodeURIComponent(h.henvendelse_id)}/innhold`);
        emne.textContent = d.emne;
        kropp.textContent = d.kropp;
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        sett(utfall, el("span", { role: "alert",
          text: t("ui.kundeservice.feil.generell") }));
        return;
      }
      await tegnUtkast(h.henvendelse_id);
    },
  };
}

// Sammendraget over tabellen. TALLENE KOMMER FRA SIN EGEN DØR og gjelder
// ALT — ikke bare det listen viser.
function sammendrag(s) {
  const p = el("p", {
    text: t("ui.kundeservice.sammendrag")
      .replace("{apne}", String(s.apne))
      .replace("{uklassifiserte}", String(s.uklassifiserte))
      .replace("{kritiske}", String(s.kritiske))
      .replace("{koe}", String(s.i_unntakskoe)) });
  // AVKORTINGEN SIES HØYT. Uten dette ville flaten sett komplett ut
  // nettopp når den var det minst.
  if (s.vist < s.apne) {
    p.append(" ", el("strong", {
      text: t("ui.kundeservice.avkortet")
        .replace("{vist}", String(s.vist)) }));
  }
  return p;
}

export function visKundeservice(hoved, ctx) {
  const hode = () => flateHode(t("ui.kundeservice.tittel"),
    t("ui.kundeservice.undertittel"));
  sett(hoved, ...hode());
  // KVITTERINGEN LEVER UTENFOR TEGNINGEN. Alt inne i `kropp` bygges på
  // nytt ved hver `last()`, og før dette forsvant kvitteringen i samme
  // øyeblikk den ble satt — brukeren trykket, så skjermen blinke, og
  // satt igjen uten å vite om det gikk bra.
  //
  // INGEN `aria-live` her: `meldLive` eier opplesningen, og to regioner
  // ville lest den samme setningen to ganger.
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  // …OG DEN ÅPNE RADEN OGSÅ. Uten dette lukket detaljpanelet seg ved
  // hver skriving, og neste handling krevde at brukeren fant fram til
  // raden igjen.
  let apenRad = null;
  const settApen = (id) => { apenRad = id; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/kundeservice"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const koe = d.koe || [];
      const detalj = detaljpanel(ctx, last, kvitter, settApen);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.kundeservice.oversikt.tittel") }),
        sammendrag(s));

      const koseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.kundeservice.koe.tittel") }));
      if (!koe.length) {
        koseksjon.append(el("p", { class: "muted",
          text: t("ui.kundeservice.koe.ingen") }));
      } else {
        koseksjon.append(koTabell(koe, ctx, detalj.apne));
      }
      const deler = [oversikt, koseksjon, avsenderSeksjon(d.avsenderprofil),
                     stilleSeksjon(ctx, last, kvitter),
                     detalj.node];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(avsenderSkjema(ctx, last, kvitter, d.avsenderprofil));
      }
      sett(kropp, ...deler);
      // GJENÅPNE PANELET på raden som sto åpen. Finnes den ikke lenger
      // i listen — avsluttet, eller falt utenfor avkortingen — slippes
      // den, framfor å åpne et panel på en rad ingen ser.
      if (apenRad) {
        const rad = koe.find((x) => x.henvendelse_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
