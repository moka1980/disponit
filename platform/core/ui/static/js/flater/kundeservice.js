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
// DET FINNES INGEN «SEND»-KNAPP, og fraværet er dommen: katalogen lover
// automatiske svar, v1 lagrer et utkast. Undertittelen sier det, og de
// eneste to dommene et utkast kan få heter `forkastet` og
// `brukt_manuelt`. «Merk som brukt» er sporet etter at et MENNESKE
// sendte noe — og det er nettopp det sporet som gjør at en henvendelse
// i det hele tatt kan lukkes som «besvart».
//
// TABELLEN ER EKTE (m16-formen): <caption>, th[scope=col] på kolonnene
// og th[scope=row] på cellen som navngir raden. Wrapperen `.tablewrap`
// er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, avgjorUtkast, hentJson, henvendelseTilUnntakskoe,
  klassifiserHenvendelse, lagreUtkast, lukkHenvendelse,
  nyIdempotensnokkel,
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

function korad(h, ctx, apneDetalj) {
  const rad = el("tr", {});
  // REFERANSEN NAVNGIR raden — det er den et menneske slår opp i
  // innboksen. `celle-id` står på <th>, ikke på et <span> inni, fordi
  // `max-width` ikke gjør noe på et inline-element.
  rad.append(el("th", { scope: "row", class: "celle-id",
                        text: h.ekstern_ref }));
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
      const kort = el("div", { class: "skjemaboks" },
        el("p", { class: "celle-tekst", text: u.tekst }),
        el("p", { class: "muted",
          text: `${u.opprettet.slice(0, 10)} · ${u.kilde} · ${u.status}` }));
      if (skriver && u.status === "foreslatt") {
        for (const [status, nokkel] of [
          ["forkastet", "ui.kundeservice.knapp.forkast"],
          ["brukt_manuelt", "ui.kundeservice.knapp.brukt"]]) {
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
      merkelinje.textContent = `${h.ekstern_ref} · `
        + `${t(`ui.kundeservice.kanal.${h.kanal}`)} · `
        + `${alderTekst(h.alder_dogn)}`;
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
      sett(kropp, oversikt, koseksjon, detalj.node);
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
