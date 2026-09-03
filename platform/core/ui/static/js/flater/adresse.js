// Adressevalideringen (M-19 v1) — REGISTERET, IKKE OPPSLAGET.
//
// FLATENS VIKTIGSTE JOBB er å vise ADRESSEN SLIK DEN BLE OPPGITT, og
// HVEM SOM KONTROLLERTE DEN HVORDAN. En «validert» adresse uten hvem og
// hvordan er ikke en måling, og `adresse_validert` ville hvilt på
// påstanden.
//
// DET FINNES INGEN «SLÅ OPP»-KNAPP, og fraværet er dommen:
// netthandelsmalen navngir modulen som `v_adresse` og lar M-25s
// `ordre.bekreft_og_fakturer` gå automatisk på det vilkåret. Et oppslag
// er en utgående kanal med personopplysninger i — kundens navn og
// adresse ut av huset, til en tredjepart vi ikke har
// databehandleravtale med. Og at en adresse FINNES i et register sier
// uansett ikke at pakken kommer fram til den som skal ha den.
//
// ORIGINALEN ER DET SOM VISES. Normaliseringen regnes i basen og er noe
// vi SAMMENLIGNER på, ikke noe vi presenterer som kundens adresse.
// Blander man dem, kan ingen etterpå se om en feillevering skyldtes det
// kunden skrev eller det vi gjorde med det.
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, hentJson, nyIdempotensnokkel, registrerAdresse,
  registrerAdressekontroll, registrerAdressesubjekt, settAdressekrav,
  settAdressesubjektAktiv,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

const MERKE = {
  ukontrollert_adresse: "ui.adresse.merke_ukontrollert",
  kontroll_utlopt: "ui.adresse.merke_utlopt",
  avvist_adresse: "ui.adresse.merke_avvist",
  utilstrekkelig_metode: "ui.adresse.merke_svak",
  ingen_krav: "ui.adresse.merke_uten_krav",
};

// LUKKEDE SETT — OG INGEN AV METODENE ER ET OPPSLAG. Det er ikke en
// forglemmelse: settet ER v1-dommen, skrevet ut.
export const KILDER = ["oppgitt_av_kunde", "ordre", "manuell", "import"];
export const METODER = ["visuell", "bekreftet_av_kunde", "dokumentert",
                        "levering_bekreftet"];
export const UTFALL = ["godkjent", "avvist", "ukontrollerbar"];

const KILDETEKST = {
  oppgitt_av_kunde: "ui.adresse.kilde.oppgitt_av_kunde",
  ordre: "ui.adresse.kilde.ordre",
  manuell: "ui.adresse.kilde.manuell",
  import: "ui.adresse.kilde.import",
};
const METODETEKST = {
  visuell: "ui.adresse.metode.visuell",
  bekreftet_av_kunde: "ui.adresse.metode.bekreftet_av_kunde",
  dokumentert: "ui.adresse.metode.dokumentert",
  levering_bekreftet: "ui.adresse.metode.levering_bekreftet",
};
const UTFALLTEKST = {
  godkjent: "ui.adresse.utfall.godkjent",
  avvist: "ui.adresse.utfall.avvist",
  ukontrollerbar: "ui.adresse.utfall.ukontrollerbar",
};

// ADRESSEN PÅ ÉN LINJE, SLIK DEN BLE OPPGITT. Ingen retting, ingen
// utviding av forkortelser — bare feltene satt sammen i den rekkefølgen
// en konvolutt leses.
export function adresseTekst(a) {
  if (!a || !a.linje1) return t("ui.adresse.uten_adresse");
  const deler = [a.linje1];
  if (a.linje2) deler.push(a.linje2);
  deler.push(`${a.postnr} ${a.poststed}`.trim());
  if (a.land) deler.push(a.land);
  return deler.join(", ");
}

// KONTROLLENS ORD, med METODEN. Et utfall uten metode er en påstand —
// og flaten viser derfor aldri det ene uten det andre.
export function kontrollTekst(utfall, metode) {
  if (!utfall) return t("ui.adresse.uten_kontroll");
  const u = t(UTFALLTEKST[utfall] || "ui.adresse.utfall.ukjent");
  if (!metode) return u;
  return t("ui.adresse.utfall_ved")
    .replace("{utfall}", u)
    .replace("{metode}", t(METODETEKST[metode] || "ui.adresse.metode.ukjent"));
}

export function kildeTekst(kilde) {
  if (!kilde) return t("ui.adresse.uten_kilde");
  return t(KILDETEKST[kilde] || "ui.adresse.kilde.ukjent");
}

function subjektrad(s, apneDetalj) {
  const rad = el("tr", {});
  rad.append(el("th", { scope: "row", class: "celle-id",
                        text: s.ekstern_ref }));
  rad.append(el("td", { class: "celle-tekst", text: s.navn }));
  rad.append(el("td", { class: "celle-tekst", text: adresseTekst(s) }));
  rad.append(el("td", { class: "celle-tekst",
                        text: kildeTekst(s.kilde) }));
  rad.append(el("td", { class: "celle-tekst",
    text: kontrollTekst(s.siste_utfall, s.siste_metode) }));
  rad.append(el("td", { class: "celle-id",
    text: s.siste_kontrollert || t("ui.adresse.uten_kontroll") }));

  const merkecelle = el("td", {},
    el("span", { text: s.aktiv ? t("ui.adresse.status.aktiv")
                               : t("ui.adresse.status.inaktiv") }));
  for (const funn of s.apne_funn || []) {
    if (!MERKE[funn]) continue;
    // MERKET ER TEKST (WCAG 1.4.1).
    merkecelle.append(" ", el("strong", { class: "merke",
                                          text: t(MERKE[funn]) }));
  }
  rad.append(merkecelle);

  const handling = el("td", {});
  const knapp = el("button", { type: "button",
    text: t("ui.adresse.knapp.apne") });
  knapp.addEventListener("click", () => apneDetalj(s));
  handling.append(knapp);
  rad.append(handling);
  return rad;
}

function subjektTabell(subjekter, apneDetalj) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.adresse.liste.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.adresse.kolonne.ref") }),
    el("th", { scope: "col", text: t("ui.adresse.kolonne.navn") }),
    el("th", { scope: "col", text: t("ui.adresse.kolonne.adresse") }),
    el("th", { scope: "col", text: t("ui.adresse.kolonne.kilde") }),
    el("th", { scope: "col", text: t("ui.adresse.kolonne.kontroll") }),
    el("th", { scope: "col", text: t("ui.adresse.kolonne.kontrollert") }),
    el("th", { scope: "col", text: t("ui.adresse.kolonne.merker") }),
    el("th", { scope: "col", text: t("ui.adresse.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const s of subjekter) tbody.append(subjektrad(s, apneDetalj));
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function kravTabell(krav) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.adresse.krav.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.adresse.kolonne.krav") }),
    el("th", { scope: "col", text: t("ui.adresse.kolonne.verdi") }))));
  const tbody = el("tbody");
  const linjer = [
    ["ui.adresse.krav.ukontrollert",
     t("ui.adresse.dogn").replace("{dogn}",
                                  String(krav.ukontrollert_dogn))],
    ["ui.adresse.krav.gyldig",
     t("ui.adresse.dogn").replace("{dogn}",
                                  String(krav.kontroll_gyldig_dogn))],
    ["ui.adresse.krav.metoder",
     (krav.godkjente_metoder || [])
       .map((m) => t(METODETEKST[m] || "ui.adresse.metode.ukjent"))
       .join(", ")],
  ];
  for (const [nokkel, verdi] of linjer) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: t(nokkel) }),
      el("td", { class: "celle-tekst", text: verdi })));
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
          ? t("ui.adresse.feil.tilstand")
          : t("ui.adresse.feil.generell") }));
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

function detaljpanel(ctx, last, kvitter, settApen) {
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;
  // ÅPNINGSTELLEREN (109s lærdom): åpner noen subjekt B mens As
  // historikk er underveis, ville As linjer blitt tegnet inn i Bs
  // panel — en adressehistorikk som ser ut til å høre til en annen.
  let apningsnr = 0;

  const merkelinje = el("p", { class: "muted" });
  const historikk = el("div", {});

  // --- ny adresseversjon ---
  const vSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const vLinje1 = el("input", { id: "ad-v-linje1", name: "linje1",
    type: "text", required: true, maxlength: 200 });
  const vLinje2 = el("input", { id: "ad-v-linje2", name: "linje2",
    type: "text", maxlength: 200 });
  const vPostnr = el("input", { id: "ad-v-postnr", name: "postnr",
    type: "text", required: true, maxlength: 20 });
  const vPoststed = el("input", { id: "ad-v-poststed", name: "poststed",
    type: "text", required: true, maxlength: 100 });
  const vLand = el("input", { id: "ad-v-land", name: "land",
    type: "text", required: true, maxlength: 2, minlength: 2,
    value: "NO" });
  const vKilde = velger("ad-v-kilde", "kilde", KILDER, KILDETEKST);
  const vKildeRef = el("input", { id: "ad-v-kilderef",
    name: "kilde_ref", type: "text", required: true, maxlength: 100 });
  const vFra = el("input", { id: "ad-v-fra", name: "gjelder_fra",
    type: "date", required: true });
  const vNotat = el("input", { id: "ad-v-notat", name: "notat",
    type: "text", required: true, maxlength: 2000 });
  const vKnapp = el("button", { type: "submit",
    text: t("ui.adresse.knapp.ny_adresse") });
  vSkjema.append(
    felt("ad-v-linje1", "ui.adresse.skjema.linje1", vLinje1,
         "ui.adresse.skjema.linje1_hjelp"),
    felt("ad-v-linje2", "ui.adresse.skjema.linje2", vLinje2),
    felt("ad-v-postnr", "ui.adresse.skjema.postnr", vPostnr),
    felt("ad-v-poststed", "ui.adresse.skjema.poststed", vPoststed),
    felt("ad-v-land", "ui.adresse.skjema.land", vLand,
         "ui.adresse.skjema.land_hjelp"),
    felt("ad-v-kilde", "ui.adresse.skjema.kilde", vKilde,
         "ui.adresse.skjema.kilde_hjelp"),
    felt("ad-v-kilderef", "ui.adresse.skjema.kilde_ref", vKildeRef),
    felt("ad-v-fra", "ui.adresse.skjema.gjelder_fra", vFra,
         "ui.adresse.skjema.gjelder_fra_hjelp"),
    felt("ad-v-notat", "ui.adresse.skjema.notat", vNotat),
    el("div", { class: "skjema-bunn" }, vKnapp));
  skjemaramme(ctx, last, {
    skjema: vSkjema, knapp: vKnapp, utfall, kvitter,
    okNokkel: "ui.adresse.skjema.adresse_ok",
    // ADRESSEN SENDES SLIK DEN BLE SKREVET. Flaten trimmer ikke og
    // retter ikke — API-et trimmer ytterkantene, og basen regner
    // normaliseringen. Ingen av leddene gjetter.
    send: (idem) => registrerAdresse(gjeldende.subjekt_id, {
      linje1: vLinje1.value, linje2: vLinje2.value || null,
      postnr: vPostnr.value, poststed: vPoststed.value,
      land: vLand.value, kilde: vKilde.value,
      kilde_ref: vKildeRef.value, gjelder_fra: vFra.value,
      notat: vNotat.value,
    }, idem),
    tilbakestill: () => {
      vKildeRef.value = ""; vNotat.value = "";
    },
  });

  // --- ny kontroll ---
  //
  // KONTROLLEN GJELDER DEN GJELDENDE VERSJONEN. Endrer kunden adresse,
  // er den gamle kontrollen fortsatt sann om den GAMLE adressen — så
  // det er versjonen, ikke subjektet, kontrollen henges på.
  const kSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const kMetode = velger("ad-k-metode", "metode", METODER, METODETEKST);
  const kUtfall = velger("ad-k-utfall", "utfall", UTFALL, UTFALLTEKST);
  const kHvem = el("input", { id: "ad-k-hvem", name: "kontrollor",
    type: "text", required: true, maxlength: 300 });
  const kKildeRef = el("input", { id: "ad-k-kilderef",
    name: "kilde_ref", type: "text", required: true, maxlength: 100 });
  const kGrunn = el("input", { id: "ad-k-grunn", name: "begrunnelse",
    type: "text", maxlength: 2000 });
  const kDato = el("input", { id: "ad-k-dato", name: "kontrollert",
    type: "date", required: true });
  const kKnapp = el("button", { type: "submit",
    text: t("ui.adresse.knapp.ny_kontroll") });
  // BEGRUNNELSEN BLIR PÅKREVD NÅR UTFALLET IKKE ER «godkjent» — et
  // avslag uten begrunnelse er en påstand, ikke en vurdering.
  const oppdaterGrunn = () => {
    kGrunn.required = kUtfall.value !== "godkjent";
  };
  kUtfall.addEventListener("change", oppdaterGrunn);
  oppdaterGrunn();
  kSkjema.append(
    felt("ad-k-metode", "ui.adresse.skjema.metode", kMetode,
         "ui.adresse.skjema.metode_hjelp"),
    felt("ad-k-utfall", "ui.adresse.skjema.utfall", kUtfall,
         "ui.adresse.skjema.utfall_hjelp"),
    felt("ad-k-hvem", "ui.adresse.skjema.kontrollor", kHvem,
         "ui.adresse.skjema.kontrollor_hjelp"),
    felt("ad-k-kilderef", "ui.adresse.skjema.kilde_ref", kKildeRef),
    felt("ad-k-grunn", "ui.adresse.skjema.begrunnelse", kGrunn,
         "ui.adresse.skjema.begrunnelse_hjelp"),
    felt("ad-k-dato", "ui.adresse.skjema.kontrollert", kDato),
    el("div", { class: "skjema-bunn" }, kKnapp));
  skjemaramme(ctx, last, {
    skjema: kSkjema, knapp: kKnapp, utfall, kvitter,
    okNokkel: "ui.adresse.skjema.kontroll_ok",
    send: (idem) => registrerAdressekontroll(gjeldende.versjon_id, {
      metode: kMetode.value, utfall: kUtfall.value,
      kontrollor: kHvem.value, kilde_ref: kKildeRef.value,
      begrunnelse: kGrunn.value || null, kontrollert: kDato.value,
    }, idem),
    tilbakestill: () => { kKildeRef.value = ""; kGrunn.value = ""; },
  });

  // --- aktiv/inaktiv ---
  const aSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const aKnapp = el("button", { type: "submit",
    text: t("ui.adresse.knapp.deaktiver") });
  aSkjema.append(
    el("p", { class: "muted",
              text: t("ui.adresse.skjema.aktiv_hjelp") }),
    el("div", { class: "skjema-bunn" }, aKnapp));
  skjemaramme(ctx, last, {
    skjema: aSkjema, knapp: aKnapp, utfall, kvitter,
    okNokkel: "ui.adresse.skjema.aktiv_ok",
    send: (idem) => settAdressesubjektAktiv(gjeldende.subjekt_id,
                                            !gjeldende.aktiv, idem),
    tilbakestill: () => { settApen(null); innhold.hidden = true; },
  });

  const skriver = harScope(ctx, "bestilling:opprett");
  innhold.append(el("h3", { text: t("ui.adresse.detalj.tittel") }),
    merkelinje, historikk);
  if (skriver) {
    innhold.append(
      el("h4", { text: t("ui.adresse.skjema.adresse_tittel") }), vSkjema,
      el("h4", { text: t("ui.adresse.skjema.kontroll_tittel") }), kSkjema,
      el("h4", { text: t("ui.adresse.skjema.aktiv_tittel") }), aSkjema);
  }
  boks.append(innhold, utfall);
  innhold.hidden = true;

  function historikkTabell(rader) {
    const tb = el("table", { class: "kpi-tabell" },
      el("caption", { text: t("ui.adresse.historikk.caption") }));
    tb.append(el("thead", {}, el("tr", {},
      el("th", { scope: "col",
                 text: t("ui.adresse.kolonne.gjelder_fra") }),
      el("th", { scope: "col", text: t("ui.adresse.kolonne.adresse") }),
      el("th", { scope: "col", text: t("ui.adresse.kolonne.kilde") }),
      el("th", { scope: "col", text: t("ui.adresse.kolonne.kontroll") }),
      el("th", { scope: "col",
                 text: t("ui.adresse.kolonne.kilde_ref") }),
      el("th", { scope: "col", text: t("ui.adresse.kolonne.notat") }))));
    const tbody = el("tbody");
    for (const v of rader) {
      const rad = el("tr", {});
      rad.append(el("th", { scope: "row", class: "celle-id",
                            text: v.gjelder_fra }));
      // ORIGINALEN, aldri normaliseringen.
      const adressecelle = el("td", { class: "celle-tekst" },
        el("span", { text: adresseTekst(v) }));
      // SKIFTET ER MERKET, MED ORD — og det er målt på den
      // NORMALISERTE formen, så en renskrevet skrivemåte ikke ser ut
      // som en flytting.
      if (v.endret) {
        adressecelle.append(" ", el("strong", { class: "merke",
          text: t("ui.adresse.merke_skifte") }));
      }
      rad.append(adressecelle);
      rad.append(el("td", { class: "celle-tekst",
                            text: kildeTekst(v.kilde) }));
      const kontrollcelle = el("td", { class: "celle-tekst" },
        el("span", { text: kontrollTekst(v.siste_utfall,
                                         v.siste_metode) }));
      if (v.kontroller > 1) {
        kontrollcelle.append(" ", el("span", { class: "muted",
          text: t("ui.adresse.flere_kontroller")
            .replace("{n}", String(v.kontroller)) }));
      }
      rad.append(kontrollcelle);
      rad.append(el("td", { class: "celle-id", text: v.kilde_ref }));
      rad.append(el("td", { class: "celle-tekst", text: v.notat }));
      tbody.append(rad);
    }
    tb.append(tbody);
    return el("div", { class: "tablewrap" }, tb);
  }

  return {
    node: boks,
    async apne(s) {
      const nr = ++apningsnr;
      gjeldende = s;
      settApen(s.subjekt_id);
      sett(utfall);
      sett(historikk);
      merkelinje.textContent = `${s.ekstern_ref} · ${s.navn}`;
      aKnapp.textContent = t(s.aktiv ? "ui.adresse.knapp.deaktiver"
                                     : "ui.adresse.knapp.aktiver");
      // ET DEAKTIVERT SUBJEKT TAR IKKE IMOT NYE ADRESSER — men det KAN
      // aktiveres igjen, så den knappen står levende.
      vKnapp.disabled = !s.aktiv;
      // …OG EN KONTROLL TRENGER EN VERSJON Å GJELDE. Uten adresse
      // finnes det ingenting å kontrollere, og knappen sier det ved å
      // stå død framfor å gi en 404 når noen trykker.
      kKnapp.disabled = !s.aktiv || !s.versjon_id;
      innhold.hidden = false;
      let d;
      try {
        d = await hentJson(
          `/v1/adresse/${encodeURIComponent(s.subjekt_id)}/historikk`);
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        if (nr !== apningsnr) return;
        sett(historikk, el("p", { class: "muted",
          text: t("ui.adresse.feil.generell") }));
        return;
      }
      if (nr !== apningsnr) return;
      const liste = d.versjoner || [];
      if (!liste.length) {
        sett(historikk, el("p", { class: "muted",
          text: t("ui.adresse.detalj.ingen") }));
        return;
      }
      sett(historikk, historikkTabell(liste));
    },
  };
}

function subjektSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const ref = el("input", { id: "ad-ny-ref", name: "ekstern_ref",
    type: "text", required: true, maxlength: 100 });
  const navn = el("input", { id: "ad-ny-navn", name: "navn",
    type: "text", required: true, maxlength: 300 });
  const knapp = el("button", { type: "submit",
    text: t("ui.adresse.knapp.nytt_subjekt") });
  skjema.append(
    felt("ad-ny-ref", "ui.adresse.skjema.ref", ref,
         "ui.adresse.skjema.ref_hjelp"),
    felt("ad-ny-navn", "ui.adresse.skjema.navn", navn),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.adresse.skjema.subjekt_ok",
    send: (idem) => registrerAdressesubjekt({
      ekstern_ref: ref.value, navn: navn.value }, idem),
    tilbakestill: () => { ref.value = ""; navn.value = ""; },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.adresse.skjema.subjekt_tittel") }), skjema,
    utfall);
}

function kravSkjema(ctx, last, krav, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const ukontrollert = el("input", { id: "ad-k-ukontrollert",
    name: "ukontrollert", type: "number", required: true, step: "1",
    min: "0", max: "3650" });
  const gyldig = el("input", { id: "ad-k-gyldig", name: "gyldig",
    type: "number", required: true, step: "1", min: "0", max: "3650" });
  if (krav) {
    ukontrollert.value = String(krav.ukontrollert_dogn);
    gyldig.value = String(krav.kontroll_gyldig_dogn);
  }
  // METODENE SOM TELLER — avkrysning, ikke fritekst. En tenant kan
  // ikke skrive «oppslag» her, og det er ikke et hinder for
  // brukervennlighet: det ER v1-dommen.
  const metodeboks = el("fieldset", { class: "felt" },
    el("legend", { text: t("ui.adresse.krav.metoder") }));
  const bokser = {};
  for (const m of METODER) {
    const id = `ad-k-m-${m}`;
    const boks = el("input", { id, name: "metode", type: "checkbox",
                               value: m });
    if (krav && (krav.godkjente_metoder || []).includes(m)) {
      boks.checked = true;
    } else if (!krav && (m === "bekreftet_av_kunde"
                         || m === "dokumentert")) {
      boks.checked = true;
    }
    bokser[m] = boks;
    metodeboks.append(el("div", { class: "felt-avkrysning" }, boks,
      el("label", { for: id, text: t(METODETEKST[m]) })));
  }
  metodeboks.append(el("p", { class: "muted",
    text: t("ui.adresse.krav.metoder_hjelp") }));
  const knapp = el("button", { type: "submit",
    text: t("ui.adresse.knapp.lagre_krav") });
  skjema.append(
    felt("ad-k-ukontrollert", "ui.adresse.krav.ukontrollert",
         ukontrollert, "ui.adresse.krav.ukontrollert_hjelp"),
    felt("ad-k-gyldig", "ui.adresse.krav.gyldig", gyldig,
         "ui.adresse.krav.gyldig_hjelp"),
    metodeboks,
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.adresse.skjema.krav_ok",
    send: (idem) => settAdressekrav({
      ukontrollert_dogn: Number(ukontrollert.value),
      kontroll_gyldig_dogn: Number(gyldig.value),
      godkjente_metoder: METODER.filter((m) => bokser[m].checked),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.adresse.krav.tittel") }), skjema, utfall);
}

function sammendrag(s) {
  const p = el("p", {
    text: t("ui.adresse.sammendrag")
      .replace("{aktive}", String(s.aktive))
      .replace("{medadresse}", String(s.med_adresse))
      .replace("{kontrollerte}", String(s.kontrollerte))
      .replace("{funn}", String(s.apne_funn)) });
  // AVSLAGENE STÅR FOR SEG: en avvist adresse er den ene funntypen der
  // noen alt har sett på den og sagt nei.
  if (s.apne_avvist > 0) {
    p.append(" ", el("strong", {
      text: t("ui.adresse.apne_avvist").replace("{n}",
                                                String(s.apne_avvist)) }));
  }
  if (!s.har_krav) {
    p.append(" ", el("strong", { text: t("ui.adresse.ingen_krav") }));
  }
  if (s.vist < s.subjekter) {
    p.append(" ", el("strong", {
      text: t("ui.adresse.avkortet").replace("{vist}",
                                             String(s.vist)) }));
  }
  return p;
}

export function visAdresse(hoved, ctx) {
  const hode = () => flateHode(t("ui.adresse.tittel"),
    t("ui.adresse.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenRad = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const settApen = (id) => { apenRad = id; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/adresse"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const subjekter = d.subjekter || [];
      const detalj = detaljpanel(ctx, last, kvitter, settApen);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.adresse.oversikt.tittel") }),
        sammendrag(s),
        el("p", { class: "muted",
                  text: t("ui.adresse.oversikt.hvorfor") }));

      const liste = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.adresse.liste.tittel") }));
      if (!subjekter.length) {
        liste.append(el("p", { class: "muted",
          text: t("ui.adresse.liste.ingen") }));
      } else {
        liste.append(subjektTabell(subjekter, detalj.apne));
      }

      const kravseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.adresse.krav.tittel") }));
      if (!d.krav) {
        kravseksjon.append(el("p", { class: "muted",
          text: t("ui.adresse.ingen_krav") }));
      } else {
        kravseksjon.append(
          el("p", { class: "muted",
            text: t("ui.adresse.krav.versjon")
              .replace("{versjon}", String(d.krav.versjon)) }),
          kravTabell(d.krav));
      }

      const deler = [oversikt, liste, kravseksjon, detalj.node];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(subjektSkjema(ctx, last, kvitter),
                   kravSkjema(ctx, last, d.krav, kvitter));
      }
      sett(kropp, ...deler);
      if (apenRad) {
        const rad = subjekter.find((x) => x.subjekt_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
