// Kampanjeregisteret (M-44 v1) — REGISTERET, IKKE UTSENDINGEN.
//
// FLATENS VIKTIGSTE JOBB er å vise SAMTYKKETS TILSTAND MED SIN KANAL,
// og HVOR MANGE KAMPANJER EN MOTTAKER STÅR OPPFØRT TIL. «Hadde vi lov
// til å sende dette den dagen» er hele spørsmålet et tilsyn stiller.
//
// DET FINNES INGEN «SEND»-KNAPP, og fraværet er dommen. M-44 er en
// annen figur enn de tre andre i klynge 5: de er manglende
// VERIFIKATORER, denne er den manglende AKTØREN — malen fører modulen
// som `modul:` på en `auto`-handling. Modulen finnes FOR å sende, og v1
// sender null.
//
// Og se på reverseringen malen foreslår: `kompenserende`, med
// `kampanje.send_korreksjon`. Botemiddelet for en feilsendt e-post er å
// sende en TIL — en andre e-post til noen som ikke ville ha den første.
//
// KONTAKTPUNKTET VISES ALDRI I KLARTEKST. Det sendes én gang, til en
// dør som regner masken og kaster adressen — og feltet tømmes etter
// innsending.
//
// EN AVMELDING ER EN HENDELSE MAN REGISTRERER, ikke en rad man sletter.
// «Meld av» er derfor et valg i samtykkeskjemaet, ikke en slettknapp.
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, avlysKampanje, hentJson, leggIKampanjeplan,
  nyIdempotensnokkel, registrerKampanje, registrerKampanjemottaker,
  registrerSamtykke, settKampanjegrense, settKampanjemottakerAktiv,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

const MERKE = {
  over_frekvensgrense: "ui.kampanje.merke_over_tak",
  uten_samtykke: "ui.kampanje.merke_uten_samtykke",
  samtykke_trukket: "ui.kampanje.merke_trukket",
  samtykke_utlopt: "ui.kampanje.merke_utlopt",
  ingen_grense: "ui.kampanje.merke_uten_grense",
};

// LUKKEDE SETT. `trukket` står her fordi en avmelding er en hendelse
// man REGISTRERER — aldri en rad man sletter.
export const TILSTANDER = ["gitt", "bekreftet", "trukket",
                           "utlopt_markert"];
export const KANALER = ["kasse", "preferanseside", "skjema", "import",
                        "manuell", "avmeldingslenke"];

const TILSTANDTEKST = {
  gitt: "ui.kampanje.tilstand.gitt",
  bekreftet: "ui.kampanje.tilstand.bekreftet",
  trukket: "ui.kampanje.tilstand.trukket",
  utlopt_markert: "ui.kampanje.tilstand.utlopt_markert",
};
const KANALTEKST = {
  kasse: "ui.kampanje.kanal.kasse",
  preferanseside: "ui.kampanje.kanal.preferanseside",
  skjema: "ui.kampanje.kanal.skjema",
  import: "ui.kampanje.kanal.import",
  manuell: "ui.kampanje.kanal.manuell",
  avmeldingslenke: "ui.kampanje.kanal.avmeldingslenke",
};

// SAMTYKKETS ORD, MED KANALEN. Hvor det kom fra avgjør om det er et
// samtykke i det hele tatt — avkryssingen i kassa og en importert liste
// er ikke samme grunnlag — og flaten viser derfor aldri det ene uten
// det andre.
export function samtykkeTekst(tilstand, kanal) {
  if (!tilstand) return t("ui.kampanje.uten_samtykke");
  const s = t(TILSTANDTEKST[tilstand] || "ui.kampanje.tilstand.ukjent");
  if (!kanal) return s;
  return t("ui.kampanje.samtykke_fra")
    .replace("{tilstand}", s)
    .replace("{kanal}", t(KANALTEKST[kanal] || "ui.kampanje.kanal.ukjent"));
}

// MASKEN, eller ordene for at ingen er ført.
export function maskeTekst(maske) {
  if (typeof maske !== "string" || !maske) {
    return t("ui.kampanje.uten_kontakt");
  }
  return maske;
}

// «2 av 2» — ført tall mot tenantens eget tak, aldri en prosent.
export function takTekst(antall, maks) {
  if (typeof antall !== "number") return "—";
  if (typeof maks !== "number") return String(antall);
  return t("ui.kampanje.av_tak")
    .replace("{n}", String(antall))
    .replace("{maks}", String(maks));
}

function mottakerrad(m, maks, apneDetalj) {
  const rad = el("tr", {});
  rad.append(el("th", { scope: "row", class: "celle-id",
                        text: m.ekstern_ref }));
  rad.append(el("td", { class: "celle-tekst", text: m.navn }));
  rad.append(el("td", { class: "celle-id",
                        text: maskeTekst(m.kontakt_maske) }));
  rad.append(el("td", { class: "celle-tekst",
                        text: samtykkeTekst(m.tilstand, m.kanal) }));
  rad.append(el("td", { class: "celle-id",
    text: m.siste_samtykke || t("ui.kampanje.uten_samtykke") }));
  rad.append(el("td", { class: "celle-tall",
                        text: takTekst(m.i_planer, maks) }));

  const merkecelle = el("td", {},
    el("span", { text: m.aktiv ? t("ui.kampanje.status.aktiv")
                               : t("ui.kampanje.status.inaktiv") }));
  for (const funn of m.apne_funn || []) {
    if (!MERKE[funn]) continue;
    // MERKET ER TEKST (WCAG 1.4.1).
    merkecelle.append(" ", el("strong", { class: "merke",
                                          text: t(MERKE[funn]) }));
  }
  rad.append(merkecelle);

  const handling = el("td", {});
  const knapp = el("button", { type: "button",
    text: t("ui.kampanje.knapp.apne") });
  knapp.addEventListener("click", () => apneDetalj(m));
  handling.append(knapp);
  rad.append(handling);
  return rad;
}

function mottakerTabell(mottakere, maks, apneDetalj) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.kampanje.liste.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.kampanje.kolonne.ref") }),
    el("th", { scope: "col", text: t("ui.kampanje.kolonne.navn") }),
    el("th", { scope: "col", text: t("ui.kampanje.kolonne.kontakt") }),
    el("th", { scope: "col", text: t("ui.kampanje.kolonne.samtykke") }),
    el("th", { scope: "col", text: t("ui.kampanje.kolonne.dato") }),
    el("th", { scope: "col", text: t("ui.kampanje.kolonne.planer") }),
    el("th", { scope: "col", text: t("ui.kampanje.kolonne.merker") }),
    el("th", { scope: "col",
               text: t("ui.kampanje.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const m of mottakere) {
    tbody.append(mottakerrad(m, maks, apneDetalj));
  }
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function kampanjeTabell(kampanjer, avlys, skriver) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.kampanje.kampanjer.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.kampanje.kolonne.ref") }),
    el("th", { scope: "col", text: t("ui.kampanje.kolonne.navn") }),
    el("th", { scope: "col", text: t("ui.kampanje.kolonne.planlagt") }),
    el("th", { scope: "col", text: t("ui.kampanje.kolonne.avmelding") }),
    el("th", { scope: "col", text: t("ui.kampanje.kolonne.mottakere") }),
    el("th", { scope: "col", text: t("ui.kampanje.kolonne.status") }),
    el("th", { scope: "col",
               text: t("ui.kampanje.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const k of kampanjer) {
    const rad = el("tr", {});
    rad.append(el("th", { scope: "row", class: "celle-id",
                          text: k.ekstern_ref }));
    rad.append(el("td", { class: "celle-tekst", text: k.navn }));
    rad.append(el("td", { class: "celle-id", text: k.planlagt_sendt }));
    // AVMELDINGSLENKEN VISES SOM TEKST, ikke som en lenke å trykke på:
    // den hører til i e-posten, ikke i administrasjonsflaten, og en
    // klikkbar lenke her ville vært en avmelding gjort av feil person.
    rad.append(el("td", { class: "celle-id",
                          text: k.avmeldingslenke }));
    rad.append(el("td", { class: "celle-tall",
                          text: String(k.mottakere) }));
    rad.append(el("td", { class: "celle-tekst",
      text: t(k.status === "avlyst" ? "ui.kampanje.kampanje.avlyst"
                                    : "ui.kampanje.kampanje.registrert") }));
    const handling = el("td", {});
    if (skriver && k.status !== "avlyst") {
      const knapp = el("button", { type: "button",
        text: t("ui.kampanje.knapp.avlys") });
      knapp.addEventListener("click", () => avlys(k));
      handling.append(knapp);
    }
    rad.append(handling);
    tbody.append(rad);
  }
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function grenseTabell(grense) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.kampanje.grense.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.kampanje.kolonne.grense") }),
    el("th", { scope: "col", text: t("ui.kampanje.kolonne.verdi") }))));
  const tbody = el("tbody");
  const linjer = [
    ["ui.kampanje.grense.maks", String(grense.maks_per_periode)],
    ["ui.kampanje.grense.periode",
     t("ui.kampanje.dogn").replace("{dogn}",
                                   String(grense.periode_dogn))],
    ["ui.kampanje.grense.gyldig",
     t("ui.kampanje.dogn").replace(
       "{dogn}", String(grense.samtykke_gyldig_dogn))],
  ];
  for (const [nokkel, verdi] of linjer) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: t(nokkel) }),
      el("td", { class: "celle-tall", text: verdi })));
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
          ? t("ui.kampanje.feil.tilstand")
          : t("ui.kampanje.feil.generell") }));
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

function detaljpanel(ctx, last, kvitter, settApen, kampanjer) {
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;
  // ÅPNINGSTELLEREN (109s lærdom): åpner noen mottaker B mens As
  // historikk er underveis, ville As linjer blitt tegnet inn i Bs
  // panel — en samtykkehistorikk som ser ut til å høre til en annen.
  let apningsnr = 0;

  const merkelinje = el("p", { class: "muted" });
  const historikk = el("div", {});

  // --- ny samtykkehendelse ---
  //
  // «MELD AV» ER ET VALG HER, IKKE EN SLETTKNAPP. En avmelding er en
  // hendelse man registrerer; sletting ville fjernet svaret på om vi
  // hadde lov den dagen.
  const sSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const sTilstand = velger("kp-s-tilstand", "tilstand", TILSTANDER,
                           TILSTANDTEKST);
  const sKanal = velger("kp-s-kanal", "kanal", KANALER, KANALTEKST);
  const sKildeRef = el("input", { id: "kp-s-kilderef",
    name: "kilde_ref", type: "text", required: true, maxlength: 100 });
  const sFormal = el("input", { id: "kp-s-formal", name: "formal",
    type: "text", required: true, maxlength: 2000 });
  const sDato = el("input", { id: "kp-s-dato", name: "inntruffet",
    type: "date", required: true });
  const sNotat = el("input", { id: "kp-s-notat", name: "notat",
    type: "text", required: true, maxlength: 2000 });
  const sKnapp = el("button", { type: "submit",
    text: t("ui.kampanje.knapp.nytt_samtykke") });
  sSkjema.append(
    felt("kp-s-tilstand", "ui.kampanje.skjema.tilstand", sTilstand,
         "ui.kampanje.skjema.tilstand_hjelp"),
    felt("kp-s-kanal", "ui.kampanje.skjema.kanal", sKanal,
         "ui.kampanje.skjema.kanal_hjelp"),
    felt("kp-s-kilderef", "ui.kampanje.skjema.kilde_ref", sKildeRef),
    felt("kp-s-formal", "ui.kampanje.skjema.formal", sFormal,
         "ui.kampanje.skjema.formal_hjelp"),
    felt("kp-s-dato", "ui.kampanje.skjema.inntruffet", sDato,
         "ui.kampanje.skjema.inntruffet_hjelp"),
    felt("kp-s-notat", "ui.kampanje.skjema.notat", sNotat),
    el("div", { class: "skjema-bunn" }, sKnapp));
  skjemaramme(ctx, last, {
    skjema: sSkjema, knapp: sKnapp, utfall, kvitter,
    okNokkel: "ui.kampanje.skjema.samtykke_ok",
    send: (idem) => registrerSamtykke(gjeldende.mottaker_id, {
      tilstand: sTilstand.value, kanal: sKanal.value,
      kilde_ref: sKildeRef.value, formal: sFormal.value,
      inntruffet: sDato.value, notat: sNotat.value,
    }, idem),
    tilbakestill: () => { sKildeRef.value = ""; sNotat.value = ""; },
  });

  // --- legg i en kampanjeplan ---
  const pSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const pKampanje = el("select", { id: "kp-p-kampanje",
    name: "kampanje", required: true });
  for (const k of kampanjer.filter((x) => x.status !== "avlyst")) {
    pKampanje.append(el("option", { value: k.kampanje_id,
      text: `${k.ekstern_ref} · ${k.planlagt_sendt}` }));
  }
  const pKnapp = el("button", { type: "submit",
    text: t("ui.kampanje.knapp.legg_i_plan") });
  pSkjema.append(
    felt("kp-p-kampanje", "ui.kampanje.skjema.kampanje", pKampanje,
         "ui.kampanje.skjema.kampanje_hjelp"),
    el("div", { class: "skjema-bunn" }, pKnapp));
  skjemaramme(ctx, last, {
    skjema: pSkjema, knapp: pKnapp, utfall, kvitter,
    okNokkel: "ui.kampanje.skjema.plan_ok",
    send: (idem) => leggIKampanjeplan(pKampanje.value,
                                      gjeldende.mottaker_id, idem),
  });

  // --- aktiv/inaktiv ---
  const aSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const aKnapp = el("button", { type: "submit",
    text: t("ui.kampanje.knapp.deaktiver") });
  aSkjema.append(
    el("p", { class: "muted",
              text: t("ui.kampanje.skjema.aktiv_hjelp") }),
    el("div", { class: "skjema-bunn" }, aKnapp));
  skjemaramme(ctx, last, {
    skjema: aSkjema, knapp: aKnapp, utfall, kvitter,
    okNokkel: "ui.kampanje.skjema.aktiv_ok",
    send: (idem) => settKampanjemottakerAktiv(gjeldende.mottaker_id,
                                              !gjeldende.aktiv, idem),
    tilbakestill: () => { settApen(null); innhold.hidden = true; },
  });

  const skriver = harScope(ctx, "bestilling:opprett");
  innhold.append(el("h3", { text: t("ui.kampanje.detalj.tittel") }),
    merkelinje, historikk);
  if (skriver) {
    innhold.append(
      el("h4", { text: t("ui.kampanje.skjema.samtykke_tittel") }),
      sSkjema,
      el("h4", { text: t("ui.kampanje.skjema.plan_tittel") }), pSkjema,
      el("h4", { text: t("ui.kampanje.skjema.aktiv_tittel") }), aSkjema);
  }
  boks.append(innhold, utfall);
  innhold.hidden = true;

  function historikkTabell(rader) {
    const tb = el("table", { class: "kpi-tabell" },
      el("caption", { text: t("ui.kampanje.historikk.caption") }));
    tb.append(el("thead", {}, el("tr", {},
      el("th", { scope: "col",
                 text: t("ui.kampanje.kolonne.inntruffet") }),
      el("th", { scope: "col",
                 text: t("ui.kampanje.kolonne.samtykke") }),
      el("th", { scope: "col", text: t("ui.kampanje.kolonne.formal") }),
      el("th", { scope: "col",
                 text: t("ui.kampanje.kolonne.kilde_ref") }),
      el("th", { scope: "col",
                 text: t("ui.kampanje.kolonne.notat") }))));
    const tbody = el("tbody");
    for (const h of rader) {
      const rad = el("tr", {});
      rad.append(el("th", { scope: "row", class: "celle-id",
                            text: h.inntruffet }));
      const celle = el("td", { class: "celle-tekst" },
        el("span", { text: samtykkeTekst(h.tilstand, h.kanal) }));
      // SKIFTET ER MERKET, MED ORD.
      if (h.endret) {
        celle.append(" ", el("strong", { class: "merke",
          text: t("ui.kampanje.merke_skifte") }));
      }
      rad.append(celle);
      rad.append(el("td", { class: "celle-tekst", text: h.formal }));
      rad.append(el("td", { class: "celle-id", text: h.kilde_ref }));
      rad.append(el("td", { class: "celle-tekst", text: h.notat }));
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
      settApen(m.mottaker_id);
      sett(utfall);
      sett(historikk);
      merkelinje.textContent = `${m.ekstern_ref} · ${m.navn}`;
      aKnapp.textContent = t(m.aktiv ? "ui.kampanje.knapp.deaktiver"
                                     : "ui.kampanje.knapp.aktiver");
      // EN DEAKTIVERT MOTTAKER LEGGES IKKE I NYE PLANER — men en
      // AVMELDING tas alltid imot, også fra hen. Å nekte den ville
      // vært å nekte noen å trekke samtykket sitt.
      pKnapp.disabled = !m.aktiv || pKampanje.options.length === 0;
      innhold.hidden = false;
      let d;
      try {
        d = await hentJson(
          `/v1/kampanje/mottaker/${encodeURIComponent(m.mottaker_id)}`
          + "/samtykke");
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        if (nr !== apningsnr) return;
        sett(historikk, el("p", { class: "muted",
          text: t("ui.kampanje.feil.generell") }));
        return;
      }
      if (nr !== apningsnr) return;
      const liste = d.hendelser || [];
      if (!liste.length) {
        sett(historikk, el("p", { class: "muted",
          text: t("ui.kampanje.detalj.ingen") }));
        return;
      }
      sett(historikk, historikkTabell(liste));
    },
  };
}

function mottakerSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const ref = el("input", { id: "kp-ny-ref", name: "ekstern_ref",
    type: "text", required: true, maxlength: 100 });
  const navn = el("input", { id: "kp-ny-navn", name: "navn",
    type: "text", required: true, maxlength: 300 });
  const kontakt = el("input", { id: "kp-ny-kontakt", name: "kontakt",
    type: "text", required: true, maxlength: 320, autocomplete: "off",
    spellcheck: "false" });
  const knapp = el("button", { type: "submit",
    text: t("ui.kampanje.knapp.ny_mottaker") });
  skjema.append(
    felt("kp-ny-ref", "ui.kampanje.skjema.ref", ref,
         "ui.kampanje.skjema.ref_hjelp"),
    felt("kp-ny-navn", "ui.kampanje.skjema.navn", navn),
    felt("kp-ny-kontakt", "ui.kampanje.skjema.kontakt", kontakt,
         "ui.kampanje.skjema.kontakt_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.kampanje.skjema.mottaker_ok",
    send: (idem) => registrerKampanjemottaker({
      ekstern_ref: ref.value, navn: navn.value,
      kontakt: kontakt.value }, idem),
    // KONTAKTPUNKTET TØMMES. Det skal ikke bli stående i skjermbildet
    // etter at basen har regnet masken og kastet adressen.
    tilbakestill: () => {
      ref.value = ""; navn.value = ""; kontakt.value = "";
    },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.kampanje.skjema.mottaker_tittel") }),
    skjema, utfall);
}

function kampanjeSkjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const ref = el("input", { id: "kp-k-ref", name: "ekstern_ref",
    type: "text", required: true, maxlength: 100 });
  const navn = el("input", { id: "kp-k-navn", name: "navn",
    type: "text", required: true, maxlength: 300 });
  const formal = el("input", { id: "kp-k-formal", name: "formal",
    type: "text", required: true, maxlength: 2000 });
  // AVMELDINGSLENKEN ER PÅKREVD, og `type: url` med `pattern` gir
  // nettleseren samme dom som basen: https, ikke http.
  const lenke = el("input", { id: "kp-k-lenke", name: "avmeldingslenke",
    type: "url", required: true, maxlength: 2000,
    pattern: "https://.+" });
  const dato = el("input", { id: "kp-k-dato", name: "planlagt_sendt",
    type: "date", required: true });
  const knapp = el("button", { type: "submit",
    text: t("ui.kampanje.knapp.ny_kampanje") });
  skjema.append(
    felt("kp-k-ref", "ui.kampanje.skjema.ref", ref),
    felt("kp-k-navn", "ui.kampanje.skjema.navn", navn),
    felt("kp-k-formal", "ui.kampanje.skjema.formal", formal),
    felt("kp-k-lenke", "ui.kampanje.skjema.avmelding", lenke,
         "ui.kampanje.skjema.avmelding_hjelp"),
    felt("kp-k-dato", "ui.kampanje.skjema.planlagt", dato,
         "ui.kampanje.skjema.planlagt_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.kampanje.skjema.kampanje_ok",
    send: (idem) => registrerKampanje({
      ekstern_ref: ref.value, navn: navn.value, formal: formal.value,
      avmeldingslenke: lenke.value, planlagt_sendt: dato.value,
    }, idem),
    tilbakestill: () => { ref.value = ""; navn.value = ""; },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.kampanje.skjema.kampanje_tittel") }),
    skjema, utfall);
}

function grenseSkjema(ctx, last, grense, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const maks = el("input", { id: "kp-g-maks", name: "maks",
    type: "number", required: true, step: "1", min: "0", max: "1000" });
  const periode = el("input", { id: "kp-g-periode", name: "periode",
    type: "number", required: true, step: "1", min: "1", max: "3650" });
  const gyldig = el("input", { id: "kp-g-gyldig", name: "gyldig",
    type: "number", required: true, step: "1", min: "1", max: "3650" });
  if (grense) {
    maks.value = String(grense.maks_per_periode);
    periode.value = String(grense.periode_dogn);
    gyldig.value = String(grense.samtykke_gyldig_dogn);
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.kampanje.knapp.lagre_grense") });
  skjema.append(
    felt("kp-g-maks", "ui.kampanje.grense.maks", maks,
         "ui.kampanje.grense.maks_hjelp"),
    felt("kp-g-periode", "ui.kampanje.grense.periode", periode,
         "ui.kampanje.grense.periode_hjelp"),
    felt("kp-g-gyldig", "ui.kampanje.grense.gyldig", gyldig,
         "ui.kampanje.grense.gyldig_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.kampanje.skjema.grense_ok",
    send: (idem) => settKampanjegrense({
      maks_per_periode: Number(maks.value),
      periode_dogn: Number(periode.value),
      samtykke_gyldig_dogn: Number(gyldig.value),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.kampanje.grense.tittel") }), skjema, utfall);
}

function sammendrag(s) {
  const p = el("p", {
    text: t("ui.kampanje.sammendrag")
      .replace("{aktive}", String(s.aktive))
      .replace("{medsamtykke}", String(s.med_samtykke))
      .replace("{planlagte}", String(s.planlagte))
      .replace("{funn}", String(s.apne_funn)) });
  // FREKVENSBRUDDENE STÅR FOR SEG: det er den ene funntypen der noen
  // alt er satt opp til å få mer enn tenanten selv har bestemt.
  if (s.apne_over_tak > 0) {
    p.append(" ", el("strong", {
      text: t("ui.kampanje.apne_over_tak")
        .replace("{n}", String(s.apne_over_tak)) }));
  }
  if (!s.har_grense) {
    p.append(" ", el("strong", { text: t("ui.kampanje.ingen_grense") }));
  }
  if (s.vist < s.mottakere) {
    p.append(" ", el("strong", {
      text: t("ui.kampanje.avkortet").replace("{vist}",
                                              String(s.vist)) }));
  }
  return p;
}

export function visKampanje(hoved, ctx) {
  const hode = () => flateHode(t("ui.kampanje.tittel"),
    t("ui.kampanje.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenRad = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const settApen = (id) => { apenRad = id; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/kampanje"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const mottakere = d.mottakere || [];
      const kampanjer = d.kampanjer || [];
      const maks = d.grense ? d.grense.maks_per_periode : null;
      const detalj = detaljpanel(ctx, last, kvitter, settApen,
                                 kampanjer);
      const skriver = harScope(ctx, "bestilling:opprett");

      let idemAvlys = null;
      const avlys = async (k) => {
        if (!idemAvlys) idemAvlys = nyIdempotensnokkel();
        try {
          await avlysKampanje(k.kampanje_id, idemAvlys);
        } catch (e) {
          if (e instanceof UautorisertFeil) {
            ctx.paaUautorisert(); return;
          }
          idemAvlys = null;
          kvitter(t("ui.kampanje.feil.generell"));
          return;
        }
        idemAvlys = null;
        meldLive(t("ui.kampanje.skjema.avlyst_ok"));
        kvitter(t("ui.kampanje.skjema.avlyst_ok"));
        await last();
      };

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.kampanje.oversikt.tittel") }),
        sammendrag(s),
        el("p", { class: "muted",
                  text: t("ui.kampanje.oversikt.hvorfor") }));

      const liste = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.kampanje.liste.tittel") }));
      if (!mottakere.length) {
        liste.append(el("p", { class: "muted",
          text: t("ui.kampanje.liste.ingen") }));
      } else {
        liste.append(mottakerTabell(mottakere, maks, detalj.apne));
      }

      const kampanjeseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.kampanje.kampanjer.tittel") }));
      if (!kampanjer.length) {
        kampanjeseksjon.append(el("p", { class: "muted",
          text: t("ui.kampanje.kampanjer.ingen") }));
      } else {
        kampanjeseksjon.append(
          kampanjeTabell(kampanjer, avlys, skriver));
      }

      const grenseseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.kampanje.grense.tittel") }));
      if (!d.grense) {
        grenseseksjon.append(el("p", { class: "muted",
          text: t("ui.kampanje.ingen_grense") }));
      } else {
        grenseseksjon.append(
          el("p", { class: "muted",
            text: t("ui.kampanje.grense.versjon")
              .replace("{versjon}", String(d.grense.versjon)) }),
          grenseTabell(d.grense));
      }

      const deler = [oversikt, liste, kampanjeseksjon, grenseseksjon,
                     detalj.node];
      if (skriver) {
        deler.push(mottakerSkjema(ctx, last, kvitter),
                   kampanjeSkjema(ctx, last, kvitter),
                   grenseSkjema(ctx, last, d.grense, kvitter));
      }
      sett(kropp, ...deler);
      if (apenRad) {
        const rad = mottakere.find((x) => x.mottaker_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
