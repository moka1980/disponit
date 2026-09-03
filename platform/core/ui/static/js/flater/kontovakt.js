// Kontoverifikasjon og transaksjonsvakt (M-42 v1) — KONTOHISTORIKKEN.
//
// FLATENS VIKTIGSTE JOBB er å vise HVEM SOM OPPGA HVILKEN KONTO, NÅR OG
// GJENNOM HVILKEN KANAL — og hvem som verifiserte den, hvordan.
// Svindelen avsløres av HISTORIKKEN, ikke av gjeldende verdi, og
// historikken er derfor en førsteklasses skjerm.
//
// DET FINNES INGEN «SPERR BETALING»-KNAPP, og fraværet er dommen: to av
// tre bransjemaler navngir modulen som `v_kontovakt` og bruker
// `svindelsjekk_bestatt` til å la utgående betalinger gå automatisk.
// DET FARLIGSTE EN BETALINGSVAKT KAN GJØRE ER IKKE Å SLIPPE NOE
// GJENNOM — det er å STOPPE noe. En vakt som blokkerer feil er sin egen
// skade, og en vakt ingen har målt vet ikke hvor ofte den tar feil.
//
// OG FLATEN VERIFISERER INGENTING. Den skriver ned at et menneske
// gjorde det, med hvilken metode og hva de faktisk gjorde. Det finnes
// ingen bankoppslag her.
//
// KONTONUMMERET VISES ALDRI. Det sendes ÉN gang, til en dør som regner
// masken og kaster nummeret — og feltet tømmes etter innsending, slik
// at nummeret ikke blir stående i skjermbildet.
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, hentJson, nyIdempotensnokkel, oppgiKonto,
  registrerMottaker, settKontoterskler, settMottakerAktiv,
  verifiserKonto,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// FUNNTYPE → MERKETEKST. Lukket tabell, ikke strengbygging.
const MERKE = {
  kontoendring: "ui.kontovakt.merke_endring",
  uverifisert_konto: "ui.kontovakt.merke_uverifisert",
  verifikasjon_utlopt: "ui.kontovakt.merke_utlopt",
  ingen_terskel: "ui.kontovakt.merke_uten_terskel",
};

// KANALENE, lukket sett. «Hvordan kom denne kontoen inn» er det første
// spørsmålet i enhver etterforskning av fakturasvindel.
export const KANALER = ["faktura", "epost", "telefon", "portal", "brev",
                        "annet"];

// METODENE, lukket sett. Rekkefølgen er ikke tilfeldig: å ringe et
// nummer man hadde FRA FØR er den eneste metoden som ikke kan
// forfalskes av den som sendte fakturaen.
export const METODER = ["ringte_kjent_nummer", "fysisk_mote",
                        "signert_dokument", "bankbekreftelse", "annet"];

const KANALTEKST = {
  faktura: "ui.kontovakt.kanal.faktura",
  epost: "ui.kontovakt.kanal.epost",
  telefon: "ui.kontovakt.kanal.telefon",
  portal: "ui.kontovakt.kanal.portal",
  brev: "ui.kontovakt.kanal.brev",
  annet: "ui.kontovakt.kanal.annet",
};

const METODETEKST = {
  ringte_kjent_nummer: "ui.kontovakt.metode.ringte_kjent_nummer",
  fysisk_mote: "ui.kontovakt.metode.fysisk_mote",
  signert_dokument: "ui.kontovakt.metode.signert_dokument",
  bankbekreftelse: "ui.kontovakt.metode.bankbekreftelse",
  annet: "ui.kontovakt.metode.annet",
};

// MASKEN, eller ORDENE for at ingen konto er ført. En tom celle ville
// sett ut som manglende data der den betyr «ingen har oppgitt en konto».
export function maskeTekst(maske) {
  if (typeof maske !== "string" || !maske) {
    return t("ui.kontovakt.uten_konto");
  }
  return maske;
}

// VERIFIKASJONENS ORD. «IKKE VERIFISERT» ER ET SVAR, ikke en tom celle —
// og det er nettopp den tilstanden `konto_verifisert` skal kunne
// benekte.
export function verifikasjonTekst(av, metode, dato) {
  if (!av || !dato) return t("ui.kontovakt.ikke_verifisert");
  return t("ui.kontovakt.verifisert_av")
    .replace("{av}", av)
    .replace("{metode}", t(METODETEKST[metode] || "ui.kontovakt.metode.annet"))
    .replace("{dato}", dato);
}

function mottakerrad(m, apneDetalj) {
  const rad = el("tr", {});
  // REFERANSEN NAVNGIR raden — det er den en faktura siterer.
  rad.append(el("th", { scope: "row", class: "celle-id",
                        text: m.ekstern_ref }));
  rad.append(el("td", { class: "celle-tekst", text: m.navn }));
  rad.append(el("td", { class: "celle-id",
                        text: maskeTekst(m.kontonummer_maske) }));
  rad.append(el("td", { text: m.oppgitt_kanal
    ? t(KANALTEKST[m.oppgitt_kanal] || "ui.kontovakt.kanal.annet")
    : "—" }));
  rad.append(el("td", { class: "celle-tekst", text: m.oppgitt_av || "—" }));
  rad.append(el("td", { class: "celle-tekst",
    text: verifikasjonTekst(m.verifisert_av, m.metode,
                            m.verifisert_dato) }));

  const merkecelle = el("td", {},
    el("span", { text: m.aktiv ? t("ui.kontovakt.status.aktiv")
                               : t("ui.kontovakt.status.inaktiv") }));
  for (const funn of m.apne_funn || []) {
    if (!MERKE[funn]) continue;
    // MERKET ER TEKST (WCAG 1.4.1).
    merkecelle.append(" ", el("strong", { class: "merke",
                                          text: t(MERKE[funn]) }));
  }
  rad.append(merkecelle);

  const handling = el("td", {});
  const knapp = el("button", { type: "button",
    text: t("ui.kontovakt.knapp.apne") });
  knapp.addEventListener("click", () => apneDetalj(m));
  handling.append(knapp);
  rad.append(handling);
  return rad;
}

function mottakerTabell(mottakere, apneDetalj) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.kontovakt.liste.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.kontovakt.kolonne.ref") }),
    el("th", { scope: "col", text: t("ui.kontovakt.kolonne.navn") }),
    el("th", { scope: "col", text: t("ui.kontovakt.kolonne.konto") }),
    el("th", { scope: "col", text: t("ui.kontovakt.kolonne.kanal") }),
    el("th", { scope: "col", text: t("ui.kontovakt.kolonne.oppgitt_av") }),
    el("th", { scope: "col", text: t("ui.kontovakt.kolonne.verifisert") }),
    el("th", { scope: "col", text: t("ui.kontovakt.kolonne.status") }),
    el("th", { scope: "col", text: t("ui.kontovakt.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const m of mottakere) tbody.append(mottakerrad(m, apneDetalj));
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function terskelTabell(terskler) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.kontovakt.terskel.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.kontovakt.kolonne.terskel") }),
    el("th", { scope: "col", text: t("ui.kontovakt.kolonne.verdi") }))));
  const tbody = el("tbody");
  const linjer = [
    ["ui.kontovakt.terskel.reverifikasjon", terskler.reverifikasjon_dogn],
    ["ui.kontovakt.terskel.uverifisert", terskler.uverifisert_dogn],
  ];
  for (const [nokkel, verdi] of linjer) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: t(nokkel) }),
      el("td", { class: "celle-tall",
        text: t("ui.kontovakt.dogn").replace("{dogn}", String(verdi)) })));
  }
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function felt(id, tekst, kontroll, hjelp) {
  return el("div", { class: "felt" },
    el("label", { for: id, text: t(tekst) }),
    kontroll,
    hjelp ? el("p", { class: "muted", text: t(hjelp) }) : null);
}

function velger(id, navn, verdier, tekster) {
  const s = el("select", { id, name: navn, required: true });
  for (const v of verdier) {
    s.append(el("option", { value: v, text: t(tekster[v]) }));
  }
  return s;
}

function skjemaramme(ctx, last, { skjema, knapp, utfall, send,
                                  tilbakestill, okNokkel, kvitter }) {
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
          ? t("ui.kontovakt.feil.tilstand")
          : t("ui.kontovakt.feil.generell") }));
      return;
    }
    idem = null;
    knapp.disabled = false;
    if (tilbakestill) tilbakestill();
    meldLive(t(okNokkel));
    // KVITTERINGEN SKAL OVERLEVE TEGNINGEN (klynge 3-rettingen).
    kvitter(t(okNokkel));
    await last();
  });
}

// DETALJPANELET: kontohistorikken, og de tre handlingene.
function detaljpanel(ctx, last, kvitter, settApen) {
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;
  let sisteOppgave = null;
  // ÅPNINGSTELLEREN (app.js sin `varseltallNr`-form). Åpner noen mottaker
  // B mens As historikk er underveis, ville As linjer blitt tegnet inn i
  // Bs panel — altså en kontohistorikk som ser ut til å høre til en
  // annen part. I DETTE registeret er det ikke en kosmetisk feil.
  let apningsnr = 0;

  const merkelinje = el("p", { class: "muted" });
  const historikk = el("div", {});

  // --- ny konto ---
  const kSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const kNummer = el("input", { id: "ko-konto-nummer", name: "konto",
    type: "text", required: true, maxlength: 64, autocomplete: "off",
    spellcheck: "false" });
  const kAv = el("input", { id: "ko-konto-av", name: "oppgitt_av",
    type: "text", required: true, maxlength: 300 });
  const kKanal = velger("ko-konto-kanal", "kanal", KANALER, KANALTEKST);
  const kDato = el("input", { id: "ko-konto-dato", name: "oppgitt_dato",
    type: "date", required: true });
  const kNotat = el("input", { id: "ko-konto-notat", name: "notat",
    type: "text", required: true, maxlength: 2000 });
  const kKnapp = el("button", { type: "submit",
    text: t("ui.kontovakt.knapp.ny_konto") });
  kSkjema.append(
    felt("ko-konto-nummer", "ui.kontovakt.skjema.kontonummer", kNummer,
         "ui.kontovakt.skjema.kontonummer_hjelp"),
    felt("ko-konto-av", "ui.kontovakt.skjema.oppgitt_av", kAv,
         "ui.kontovakt.skjema.oppgitt_av_hjelp"),
    felt("ko-konto-kanal", "ui.kontovakt.skjema.kanal", kKanal,
         "ui.kontovakt.skjema.kanal_hjelp"),
    felt("ko-konto-dato", "ui.kontovakt.skjema.oppgitt_dato", kDato),
    felt("ko-konto-notat", "ui.kontovakt.skjema.notat", kNotat),
    el("div", { class: "skjema-bunn" }, kKnapp));
  skjemaramme(ctx, last, {
    skjema: kSkjema, knapp: kKnapp, utfall, kvitter,
    okNokkel: "ui.kontovakt.skjema.konto_ok",
    send: (idem) => oppgiKonto(gjeldende.mottaker_id, {
      kontonummer: kNummer.value, oppgitt_av: kAv.value,
      oppgitt_kanal: kKanal.value, oppgitt_dato: kDato.value,
      notat: kNotat.value,
    }, idem),
    // NUMMERET TØMMES. Det skal ikke bli stående i skjermbildet etter at
    // basen har regnet masken og kastet det.
    tilbakestill: () => {
      kNummer.value = ""; kAv.value = ""; kNotat.value = "";
    },
  });

  // --- verifikasjon ---
  const vSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const vMetode = velger("ko-ver-metode", "metode", METODER, METODETEKST);
  const vAv = el("input", { id: "ko-ver-av", name: "verifisert_av",
    type: "text", required: true, maxlength: 300 });
  const vNotat = el("input", { id: "ko-ver-notat", name: "notat",
    type: "text", required: true, maxlength: 2000 });
  const vDato = el("input", { id: "ko-ver-dato", name: "verifisert_dato",
    type: "date", required: true });
  const vKnapp = el("button", { type: "submit",
    text: t("ui.kontovakt.knapp.verifiser") });
  vSkjema.append(
    felt("ko-ver-metode", "ui.kontovakt.skjema.metode", vMetode,
         "ui.kontovakt.skjema.metode_hjelp"),
    felt("ko-ver-av", "ui.kontovakt.skjema.verifisert_av", vAv,
         "ui.kontovakt.skjema.verifisert_av_hjelp"),
    felt("ko-ver-notat", "ui.kontovakt.skjema.ver_notat", vNotat,
         "ui.kontovakt.skjema.ver_notat_hjelp"),
    felt("ko-ver-dato", "ui.kontovakt.skjema.verifisert_dato", vDato),
    el("div", { class: "skjema-bunn" }, vKnapp));
  skjemaramme(ctx, last, {
    skjema: vSkjema, knapp: vKnapp, utfall, kvitter,
    okNokkel: "ui.kontovakt.skjema.verifikasjon_ok",
    // VERIFIKASJONEN GJELDER DEN SISTE OPPGAVEN. Å verifisere en gammel
    // oppgave sier ingenting om kontoen som står der nå.
    send: (idem) => verifiserKonto(sisteOppgave, {
      metode: vMetode.value, verifisert_av: vAv.value,
      notat: vNotat.value, verifisert_dato: vDato.value,
    }, idem),
    tilbakestill: () => { vAv.value = ""; vNotat.value = ""; },
  });

  // --- aktiv/inaktiv ---
  const aSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const aKnapp = el("button", { type: "submit",
    text: t("ui.kontovakt.knapp.deaktiver") });
  aSkjema.append(
    el("p", { class: "muted",
              text: t("ui.kontovakt.skjema.aktiv_hjelp") }),
    el("div", { class: "skjema-bunn" }, aKnapp));
  skjemaramme(ctx, last, {
    skjema: aSkjema, knapp: aKnapp, utfall, kvitter,
    okNokkel: "ui.kontovakt.skjema.aktiv_ok",
    send: (idem) => settMottakerAktiv(gjeldende.mottaker_id,
                                      !gjeldende.aktiv, idem),
    tilbakestill: () => { settApen(null); innhold.hidden = true; },
  });

  const skriver = harScope(ctx, "bestilling:opprett");
  innhold.append(el("h3", { text: t("ui.kontovakt.detalj.tittel") }),
    merkelinje, historikk);
  if (skriver) {
    innhold.append(
      el("h4", { text: t("ui.kontovakt.skjema.konto_tittel") }), kSkjema,
      el("h4", { text: t("ui.kontovakt.skjema.verifikasjon_tittel") }),
      vSkjema,
      el("h4", { text: t("ui.kontovakt.skjema.aktiv_tittel") }), aSkjema);
  }
  boks.append(innhold, utfall);
  innhold.hidden = true;

  function historikkTabell(rader) {
    const tb = el("table", { class: "kpi-tabell" },
      el("caption", { text: t("ui.kontovakt.historikk.caption") }));
    tb.append(el("thead", {}, el("tr", {},
      el("th", { scope: "col",
                 text: t("ui.kontovakt.kolonne.oppgitt_dato") }),
      el("th", { scope: "col", text: t("ui.kontovakt.kolonne.konto") }),
      el("th", { scope: "col", text: t("ui.kontovakt.kolonne.kanal") }),
      el("th", { scope: "col",
                 text: t("ui.kontovakt.kolonne.oppgitt_av") }),
      el("th", { scope: "col",
                 text: t("ui.kontovakt.kolonne.verifisert") }),
      el("th", { scope: "col", text: t("ui.kontovakt.kolonne.notat") }))));
    const tbody = el("tbody");
    for (const o of rader) {
      const rad = el("tr", {});
      rad.append(el("th", { scope: "row", class: "celle-id",
                            text: o.oppgitt_dato }));
      const kontocelle = el("td", { class: "celle-id" },
        el("span", { text: maskeTekst(o.kontonummer_maske) }));
      // BYTTET ER MERKET, MED ORD. Uten det måtte leseren sammenligne
      // maskene selv — og en maske kan gjenta seg.
      if (o.endret) {
        kontocelle.append(" ", el("strong", { class: "merke",
          text: t("ui.kontovakt.merke_endring") }));
      }
      rad.append(kontocelle);
      rad.append(el("td", { text: t(KANALTEKST[o.oppgitt_kanal]
                                    || "ui.kontovakt.kanal.annet") }));
      rad.append(el("td", { class: "celle-tekst", text: o.oppgitt_av }));
      rad.append(el("td", { class: "celle-tekst",
        text: verifikasjonTekst(o.verifisert_av, o.metode,
                                o.verifisert_dato) }));
      rad.append(el("td", { class: "celle-tekst", text: o.notat }));
      tbody.append(rad);
    }
    tb.append(tbody);
    return el("div", { class: "tablewrap" }, tb);
  }

  return {
    node: boks,
    async apne(m) {
      const nr = ++apningsnr;
      gjeldende = m;
      sisteOppgave = null;
      settApen(m.mottaker_id);
      sett(utfall);
      sett(historikk);
      merkelinje.textContent = `${m.ekstern_ref} · ${m.navn}`;
      aKnapp.textContent = t(m.aktiv ? "ui.kontovakt.knapp.deaktiver"
                                     : "ui.kontovakt.knapp.aktiver");
      // EN DEAKTIVERT MOTTAKER TAR IKKE IMOT NYE KONTOER — men den KAN
      // aktiveres igjen, så den knappen står levende.
      kKnapp.disabled = !m.aktiv;
      // …og det finnes ingenting å verifisere før en konto er ført.
      vKnapp.disabled = true;
      innhold.hidden = false;
      let d;
      try {
        d = await hentJson(
          `/v1/kontovakt/${encodeURIComponent(m.mottaker_id)}/historikk`);
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        if (nr !== apningsnr) return;
        sett(historikk, el("p", { class: "muted",
          text: t("ui.kontovakt.feil.generell") }));
        return;
      }
      if (nr !== apningsnr) return;
      const liste = d.oppgaver || [];
      if (!liste.length) {
        sett(historikk, el("p", { class: "muted",
          text: t("ui.kontovakt.detalj.ingen") }));
        return;
      }
      // DEN SISTE OPPGAVEN ER DEN VERIFIKASJONEN GJELDER.
      sisteOppgave = liste[0].oppgave_id;
      vKnapp.disabled = !m.aktiv;
      sett(historikk, historikkTabell(liste));
    },
  };
}

function mottakerSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const ref = el("input", { id: "ko-ny-ref", name: "ekstern_ref",
    type: "text", required: true, maxlength: 100 });
  const navn = el("input", { id: "ko-ny-navn", name: "navn",
    type: "text", required: true, maxlength: 300 });
  const knapp = el("button", { type: "submit",
    text: t("ui.kontovakt.knapp.ny_mottaker") });
  skjema.append(
    felt("ko-ny-ref", "ui.kontovakt.skjema.ref", ref,
         "ui.kontovakt.skjema.ref_hjelp"),
    felt("ko-ny-navn", "ui.kontovakt.skjema.navn", navn),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.kontovakt.skjema.mottaker_ok",
    send: (idem) => registrerMottaker({
      ekstern_ref: ref.value, navn: navn.value }, idem),
    tilbakestill: () => { ref.value = ""; navn.value = ""; },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.kontovakt.skjema.mottaker_tittel") }), skjema,
    utfall);
}

function terskelSkjema(ctx, last, terskler, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const rever = el("input", { id: "ko-t-rever", name: "rever",
    type: "number", required: true, step: "1", min: "0", max: "3650" });
  const uver = el("input", { id: "ko-t-uver", name: "uver",
    type: "number", required: true, step: "1", min: "0", max: "3650" });
  if (terskler) {
    rever.value = String(terskler.reverifikasjon_dogn);
    uver.value = String(terskler.uverifisert_dogn);
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.kontovakt.knapp.lagre_terskler") });
  skjema.append(
    felt("ko-t-rever", "ui.kontovakt.terskel.reverifikasjon", rever,
         "ui.kontovakt.terskel.reverifikasjon_hjelp"),
    felt("ko-t-uver", "ui.kontovakt.terskel.uverifisert", uver,
         "ui.kontovakt.terskel.uverifisert_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.kontovakt.skjema.terskel_ok",
    send: (idem) => settKontoterskler({
      reverifikasjon_dogn: Number(rever.value),
      uverifisert_dogn: Number(uver.value),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.kontovakt.terskel.tittel") }), skjema, utfall);
}

function sammendrag(s) {
  const p = el("p", {
    text: t("ui.kontovakt.sammendrag")
      .replace("{aktive}", String(s.aktive))
      .replace("{medkonto}", String(s.med_konto))
      .replace("{verifiserte}", String(s.verifiserte))
      .replace("{funn}", String(s.apne_funn)) });
  // KONTOENDRINGENE STÅR FOR SEG. Det er det høyeste signalet i
  // svindelklassen, og et tall som druknet i «åpne funn» ville vært
  // usynlig.
  if (s.apne_endringer > 0) {
    p.append(" ", el("strong", {
      text: t("ui.kontovakt.apne_endringer")
        .replace("{n}", String(s.apne_endringer)) }));
  }
  if (!s.har_terskel) {
    p.append(" ", el("strong", { text: t("ui.kontovakt.ingen_terskler") }));
  }
  if (s.vist < s.mottakere) {
    p.append(" ", el("strong", {
      text: t("ui.kontovakt.avkortet").replace("{vist}",
                                               String(s.vist)) }));
  }
  return p;
}

export function visKontovakt(hoved, ctx) {
  const hode = () => flateHode(t("ui.kontovakt.tittel"),
    t("ui.kontovakt.undertittel"));
  sett(hoved, ...hode());
  // KVITTERINGEN OG DEN ÅPNE RADEN LEVER UTENFOR TEGNINGEN.
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenRad = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const settApen = (id) => { apenRad = id; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/kontovakt"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const mottakere = d.mottakere || [];
      const detalj = detaljpanel(ctx, last, kvitter, settApen);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.kontovakt.oversikt.tittel") }),
        sammendrag(s),
        // HVORFOR REGISTERET FINNES, sagt på flaten.
        el("p", { class: "muted",
                  text: t("ui.kontovakt.oversikt.hvorfor") }));

      const liste = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.kontovakt.liste.tittel") }));
      if (!mottakere.length) {
        liste.append(el("p", { class: "muted",
          text: t("ui.kontovakt.liste.ingen") }));
      } else {
        liste.append(mottakerTabell(mottakere, detalj.apne));
      }

      const terskelseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.kontovakt.terskel.tittel") }));
      if (!d.terskler) {
        terskelseksjon.append(el("p", { class: "muted",
          text: t("ui.kontovakt.ingen_terskler") }));
      } else {
        terskelseksjon.append(
          el("p", { class: "muted",
            text: t("ui.kontovakt.terskel.versjon")
              .replace("{versjon}", String(d.terskler.versjon)) }),
          terskelTabell(d.terskler));
      }

      const deler = [oversikt, liste, terskelseksjon, detalj.node];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(mottakerSkjema(ctx, last, kvitter),
                   terskelSkjema(ctx, last, d.terskler, kvitter));
      }
      sett(kropp, ...deler);
      if (apenRad) {
        const rad = mottakere.find((x) => x.mottaker_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
