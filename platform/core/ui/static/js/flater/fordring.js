// Kundefordringsagenten (M-23 v1) — FORDRINGSREGISTERET.
//
// FLATENS VIKTIGSTE JOBB er å vise hva som har PASSERT sitt purretrinn:
// hvilke krav som står og venter på en beslutning, og hvor gamle de er.
// Som TEKST, ikke bare farge (WCAG 1.4.1): «Moden for trinn 2» og
// «forfalt for 41 døgn siden» er ord.
//
// FLATEN VISER, DEN REGNER IKKE. `rest_ore`, `dogn_over_forfall`,
// `moden_for_trinn` og hele aldersfordelingen er regnet i BASEN, i samme
// skann som raden (104s lesedører). Aldersfordelingen kommer fra sin
// EGEN dør og teller ALT — en flate som regnet den fra de 200 viste
// radene ville tegnet et diagram om et utvalg og kalt det virksomhetens
// utestående.
//
// BELØP FORMATERES I HELTALLSARITMETIKK (101s form, ordrett): et
// flyttall ville gitt «1234,5599999999999» på et beløp som er nøyaktig i
// basen, og et krav mot en kunde tåler ikke et tall som nesten stemmer.
//
// DET FINNES INGEN «SEND PURRING»-KNAPP, og fraværet er dommen:
// katalogen lover et forslag om nedbetalingsplan til kunden, v1
// registrerer kravet. Knappen heter «Flytt til neste trinn» og gjør
// nøyaktig det — den flytter et tall i registeret, den sender ingenting.
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] og
// th[scope=row]. `.tablewrap` er sidescrollens container.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, ettergiFordring, hentJson, nesteTrinn,
  nyIdempotensnokkel, registrerBetaling, registrerFordring, settPurreplan,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

const HANDLINGER = ["paaminnelse", "purring", "inkassovarsel", "inkasso"];

// FUNNTYPE → MERKETEKST. Lukket tabell, ikke strengbygging.
const MERKE = {
  trinn_forfalt: "ui.fordring.merke_moden",
  ingen_purreplan: "ui.fordring.merke_uten_plan",
  forfalt_uten_trinn: "ui.fordring.merke_urort",
};

// BELØP I HELTALLSARITMETIKK, aldri via `/100`. `Math.trunc` og `%` på
// et heltall er eksakt, og API-taket (10^13 øre) ligger godt under
// `Number.MAX_SAFE_INTEGER` — tallet kommer helt fram gjennom JSON.
export function belopTekst(ore) {
  if (typeof ore !== "number" || !Number.isInteger(ore)) return "—";
  const neg = ore < 0;
  const a = Math.abs(ore);
  return `${neg ? "-" : ""}${Math.trunc(a / 100)},`
    + `${String(a % 100).padStart(2, "0")}`;
}

// Forfallskolonnens ORD. ENTALL HAR SIN EGEN NØKKEL (lærdommen fra
// M-21/M-34/M-13/M-17/M-18): locale-settet har ingen pluralmaskineri, og
// «overdue by 1 days» ville stått på den raden et menneske leser først.
export function forfallTekst(dogn) {
  if (typeof dogn !== "number") return "—";
  if (dogn > 0) {
    return dogn === 1
      ? t("ui.fordring.forfalt_ett_dogn")
      : t("ui.fordring.forfalt_for").replace("{dogn}", String(dogn));
  }
  if (dogn === 0) return t("ui.fordring.forfaller_i_dag");
  const n = Math.abs(dogn);
  return n === 1
    ? t("ui.fordring.om_ett_dogn")
    : t("ui.fordring.om_dogn").replace("{dogn}", String(n));
}

// En fordring er MODEN når planen har et høyere trinn enn den står på.
// Det er hele opplysningen flaten finnes for — og den er regnet i basen,
// ikke her.
export function erModen(f) {
  return f.status === "apen" && typeof f.moden_for_trinn === "number"
    && f.moden_for_trinn > f.trinn;
}

function trinnTekst(f) {
  if (!f.trinn) return t("ui.fordring.trinn_null");
  return f.trinn_navn ? `${f.trinn}. ${f.trinn_navn}` : String(f.trinn);
}

function fordringsrad(f, ctx, apneDetalj) {
  const rad = el("tr", {});
  // KUNDEN NAVNGIR raden.
  rad.append(el("th", { scope: "row", class: "celle-tekst",
                        text: f.kunde_ref }));
  rad.append(el("td", { class: "celle-id", text: f.fakturanummer }));
  rad.append(el("td", { class: "celle-tall",
                        text: belopTekst(f.belop_ore) }));
  rad.append(el("td", { class: "celle-tall",
                        text: belopTekst(f.rest_ore) }));

  const forfallcelle = el("td", {},
    el("span", { text: forfallTekst(f.dogn_over_forfall) }));
  rad.append(forfallcelle);

  const trinncelle = el("td", {}, el("span", { text: trinnTekst(f) }));
  for (const funn of f.apne_funn || []) {
    if (!MERKE[funn]) continue;
    // MERKET ER TEKST. Dette er flatens viktigste opplysning på raden:
    // kravet står og venter på en beslutning.
    trinncelle.append(" ", el("strong", { class: "merke",
      text: t(MERKE[funn]).replace("{trinn}",
                                   String(f.moden_for_trinn ?? "")) }));
  }
  rad.append(trinncelle);
  rad.append(el("td", { text: t(`ui.fordring.status.${f.status}`) }));

  const handling = el("td", {});
  const knapp = el("button", { type: "button",
    text: t("ui.fordring.knapp.apne") });
  knapp.addEventListener("click", () => apneDetalj(f));
  handling.append(knapp);
  rad.append(handling);
  return rad;
}

function fordringsTabell(fordringer, ctx, apneDetalj) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.fordring.liste.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.fordring.kolonne.kunde") }),
    el("th", { scope: "col", text: t("ui.fordring.kolonne.faktura") }),
    el("th", { scope: "col", text: t("ui.fordring.kolonne.belop") }),
    el("th", { scope: "col", text: t("ui.fordring.kolonne.rest") }),
    el("th", { scope: "col", text: t("ui.fordring.kolonne.forfall") }),
    el("th", { scope: "col", text: t("ui.fordring.kolonne.trinn") }),
    el("th", { scope: "col", text: t("ui.fordring.kolonne.status") }),
    el("th", { scope: "col",
               text: t("ui.fordring.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const f of fordringer) {
    tbody.append(fordringsrad(f, ctx, apneDetalj));
  }
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function aldersTabell(bottene) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.fordring.alder.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.fordring.kolonne.forfall") }),
    el("th", { scope: "col", text: t("ui.fordring.kolonne.antall") }),
    el("th", { scope: "col", text: t("ui.fordring.kolonne.belop") }))));
  const tbody = el("tbody");
  // ALLE BØTTENE TEGNES, også de tomme — døren returnerer dem alle av
  // samme grunn. En fordeling som endret form fra dag til dag kan ingen
  // sammenligne over tid.
  for (const b of bottene) {
    const rad = el("tr", {});
    rad.append(el("th", { scope: "row",
                          text: t(`ui.fordring.alder.${b.botte}`) }));
    rad.append(el("td", { class: "celle-tall", text: String(b.antall) }));
    rad.append(el("td", { class: "celle-tall",
                          text: belopTekst(b.ore) }));
    tbody.append(rad);
  }
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

function planTabell(plan) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.fordring.plan.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.fordring.kolonne.trinn") }),
    el("th", { scope: "col", text: t("ui.fordring.kolonne.dogn") }),
    el("th", { scope: "col", text: t("ui.fordring.kolonne.handling") }),
    el("th", { scope: "col", text: t("ui.fordring.kolonne.gebyr") }))));
  const tbody = el("tbody");
  for (const p of plan) {
    const rad = el("tr", {});
    rad.append(el("th", { scope: "row", class: "celle-tekst",
                          text: `${p.trinn_nr}. ${p.navn}` }));
    rad.append(el("td", { class: "celle-tall",
      text: t("ui.fordring.plan.dogn")
        .replace("{dogn}", String(p.dogn_etter_forfall)) }));
    rad.append(el("td", {
      text: t(`ui.fordring.handling.${p.handling}`) }));
    rad.append(el("td", { class: "celle-tall",
                          text: belopTekst(p.gebyr_ore) }));
    tbody.append(rad);
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

function skjemaramme(ctx, last, { skjema, knapp, utfall, send,
                                  tilbakestill, okNokkel }) {
  // Én nøkkel per intensjon (PR-014 R1): nullstilles ved endring og ved
  // 4xx — et avvist forsøk har FORBRUKT nøkkelen.
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
          ? t("ui.fordring.feil.tilstand")
          : t("ui.fordring.feil.generell") }));
      return;
    }
    idem = null;
    knapp.disabled = false;
    if (tilbakestill) tilbakestill();
    meldLive(t(okNokkel));
    sett(utfall, el("span", { text: t(okNokkel) }));
    last();
  });
}

// KRONER INN, ØRE UT. `Math.round` på produktet er den ENESTE veien: en
// `parseFloat` uten avrunding gir 814.9999999999999 øre på 8,15 kroner,
// og et krav mot en kunde tåler ikke et flyttall som nesten stemmer.
export function tilOre(verdi) {
  const n = Number(verdi);
  if (!Number.isFinite(n)) return null;
  return Math.round(n * 100);
}

// PURREPLANEN SKRIVES SOM LINJER, ikke som JSON. Et JSON-felt i en flate
// er et felt bare den som skrev API-et kan fylle ut — og purreplanen er
// nettopp det tenanten skal eie selv.
//
// Formen er `navn | døgn | handling | gebyr`, der gebyret kan utelates.
// Parseren er EKSPORTERT for at porten skal måle den uten å tegne en
// skjerm.
export function parsePlanlinjer(tekst) {
  const ut = [];
  for (const raa of String(tekst || "").split("\n")) {
    const linje = raa.trim();
    if (!linje) continue;
    const d = linje.split("|").map((x) => x.trim());
    if (d.length < 3) return null;
    const dogn = Number(d[1]);
    if (!d[0] || !Number.isInteger(dogn) || dogn < 0) return null;
    if (!HANDLINGER.includes(d[2])) return null;
    let gebyr = 0;
    if (d.length >= 4 && d[3] !== "") {
      gebyr = tilOre(d[3]);
      if (gebyr === null || gebyr < 0) return null;
    }
    ut.push({ navn: d[0], dogn_etter_forfall: dogn, handling: d[2],
              gebyr_ore: gebyr });
  }
  return ut.length ? ut : null;
}

// DETALJPANELET: historikken, og de tre handlingene.
function detaljpanel(ctx, last) {
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  let gjeldende = null;

  const merkelinje = el("p", { class: "muted" });
  const historikk = el("div", {});

  // --- innbetaling ---
  const bSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const bBelop = el("input", { id: "fo-bet-belop", name: "belop",
    type: "number", required: true, step: "0.01", min: "0.01" });
  const bDato = el("input", { id: "fo-bet-dato", name: "inntruffet",
    type: "date", required: true });
  const bKnapp = el("button", { type: "submit",
    text: t("ui.fordring.knapp.betaling") });
  bSkjema.append(
    felt("fo-bet-belop", "ui.fordring.skjema.betaling_belop", bBelop,
         "ui.fordring.skjema.betaling_belophjelp"),
    felt("fo-bet-dato", "ui.fordring.skjema.betaling_dato", bDato),
    el("div", { class: "skjema-bunn" }, bKnapp));
  skjemaramme(ctx, last, {
    skjema: bSkjema, knapp: bKnapp, utfall,
    okNokkel: "ui.fordring.skjema.betaling_ok",
    send: (idem) => registrerBetaling(gjeldende.fordring_id,
                                      tilOre(bBelop.value), bDato.value,
                                      idem),
    tilbakestill: () => { bBelop.value = ""; },
  });

  // --- neste trinn ---
  const tSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const tGrunn = el("input", { id: "fo-trinn-grunn", name: "begrunnelse",
    type: "text", maxlength: 2000 });
  const tKnapp = el("button", { type: "submit",
    text: t("ui.fordring.knapp.neste_trinn") });
  tSkjema.append(
    // HJELPETEKSTEN SIER HVA KNAPPEN GJØR: ETT hakk. Uten den ville en
    // bruker trodd den kunne velge trinn, og møtt en 409 uten å forstå.
    felt("fo-trinn-grunn", "ui.fordring.skjema.trinn_begrunnelse", tGrunn,
         "ui.fordring.skjema.trinn_hjelp"),
    el("div", { class: "skjema-bunn" }, tKnapp));
  skjemaramme(ctx, last, {
    skjema: tSkjema, knapp: tKnapp, utfall,
    okNokkel: "ui.fordring.skjema.trinn_ok",
    send: (idem) => nesteTrinn(gjeldende.fordring_id,
                               tGrunn.value || null, idem),
    tilbakestill: () => { tGrunn.value = ""; },
  });

  // --- ettergivelse ---
  const eSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const eGrunn = el("input", { id: "fo-etter-grunn", name: "begrunnelse",
    type: "text", required: true, maxlength: 2000 });
  const eKnapp = el("button", { type: "submit",
    text: t("ui.fordring.knapp.ettergi") });
  eSkjema.append(
    felt("fo-etter-grunn", "ui.fordring.skjema.ettergi_begrunnelse",
         eGrunn, "ui.fordring.skjema.ettergi_hjelp"),
    el("div", { class: "skjema-bunn" }, eKnapp));
  skjemaramme(ctx, last, {
    skjema: eSkjema, knapp: eKnapp, utfall,
    okNokkel: "ui.fordring.skjema.ettergi_ok",
    send: (idem) => ettergiFordring(gjeldende.fordring_id, eGrunn.value,
                                    idem),
    tilbakestill: () => { eGrunn.value = ""; innhold.hidden = true; },
  });

  const skriver = harScope(ctx, "bestilling:opprett");
  innhold.append(el("h3", { text: t("ui.fordring.detalj.tittel") }),
    merkelinje, historikk);
  if (skriver) {
    innhold.append(
      el("h4", { text: t("ui.fordring.skjema.betaling_tittel") }), bSkjema,
      el("h4", { text: t("ui.fordring.knapp.neste_trinn") }), tSkjema,
      el("h4", { text: t("ui.fordring.knapp.ettergi") }), eSkjema);
  }
  boks.append(innhold, utfall);
  innhold.hidden = true;

  function hendelsestekst(h) {
    if (h.art === "trinn") {
      return t("ui.fordring.detalj.art.trinn")
        .replace("{trinn}", String(h.trinn));
    }
    if (h.art === "betaling") {
      return `${t("ui.fordring.detalj.art.betaling")} `
        + `${belopTekst(h.belop_ore)}`;
    }
    return t("ui.fordring.detalj.art.ettergitt");
  }

  return {
    node: boks,
    async apne(f) {
      gjeldende = f;
      sett(utfall);
      sett(historikk);
      merkelinje.textContent = `${f.kunde_ref} · ${f.fakturanummer} · `
        + `${belopTekst(f.rest_ore)} · ${forfallTekst(f.dogn_over_forfall)}`;
      // ET AVSLUTTET KRAV TAR IKKE IMOT NOE. Knappene deaktiveres i
      // stedet for å love noe serveren avviser med 409.
      const apen = f.status === "apen";
      bKnapp.disabled = !apen;
      tKnapp.disabled = !apen;
      eKnapp.disabled = !apen;
      innhold.hidden = false;
      let d;
      try {
        d = await hentJson(
          `/v1/fordring/${encodeURIComponent(f.fordring_id)}/hendelser`);
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        sett(utfall, el("span", { role: "alert",
          text: t("ui.fordring.feil.generell") }));
        return;
      }
      const liste = d.hendelser || [];
      if (!liste.length) {
        sett(historikk, el("p", { class: "muted",
          text: t("ui.fordring.detalj.ingen") }));
        return;
      }
      const ul = el("ul", {});
      for (const h of liste) {
        ul.append(el("li", {},
          el("span", { text: `${h.inntruffet} — ${hendelsestekst(h)}` }),
          el("span", { class: "muted",
            text: ` · ${h.opprettet_av}`
              + (h.begrunnelse ? ` · ${h.begrunnelse}` : "") })));
      }
      sett(historikk, ul);
    },
  };
}

function nySkjema(ctx, last) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const kunde = el("input", { id: "fo-ny-kunde", name: "kunde_ref",
    type: "text", required: true, maxlength: 300 });
  const faktura = el("input", { id: "fo-ny-faktura",
    name: "fakturanummer", type: "text", required: true, maxlength: 100 });
  const belop = el("input", { id: "fo-ny-belop", name: "belop",
    type: "number", required: true, step: "0.01", min: "0.01" });
  const utstedt = el("input", { id: "fo-ny-utstedt", name: "utstedt",
    type: "date", required: true });
  const forfall = el("input", { id: "fo-ny-forfall", name: "forfall",
    type: "date", required: true });
  const knapp = el("button", { type: "submit",
    text: t("ui.fordring.knapp.ny") });
  skjema.append(
    felt("fo-ny-kunde", "ui.fordring.skjema.kunde", kunde),
    felt("fo-ny-faktura", "ui.fordring.skjema.faktura", faktura),
    felt("fo-ny-belop", "ui.fordring.skjema.belop", belop),
    felt("fo-ny-utstedt", "ui.fordring.skjema.utstedt", utstedt),
    felt("fo-ny-forfall", "ui.fordring.skjema.forfall", forfall),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall,
    okNokkel: "ui.fordring.skjema.ny_ok",
    send: (idem) => registrerFordring({
      kunde_ref: kunde.value, fakturanummer: faktura.value,
      belop_ore: tilOre(belop.value), utstedt: utstedt.value,
      forfall: forfall.value,
    }, idem),
    tilbakestill: () => {
      kunde.value = ""; faktura.value = ""; belop.value = "";
    },
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.fordring.skjema.ny_tittel") }), skjema, utfall);
}

function planSkjema(ctx, last) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const linjer = el("textarea", { id: "fo-plan-linjer", name: "trinn",
    required: true, rows: "5" });
  const knapp = el("button", { type: "submit",
    text: t("ui.fordring.knapp.lagre_plan") });
  skjema.append(
    felt("fo-plan-linjer", "ui.fordring.skjema.plan_linjer", linjer,
         "ui.fordring.skjema.plan_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall,
    okNokkel: "ui.fordring.skjema.plan_ok",
    send: (idem) => {
      const trinn = parsePlanlinjer(linjer.value);
      if (!trinn) {
        // FORMATFEILEN FANGES HER, med en setning om hva som mangler.
        // Sendt videre ville den blitt «request_feilformet», og brukeren
        // ville ikke visst hvilken linje som var gal.
        const feil = new Error("format");
        feil.status = 400;
        throw feil;
      }
      return settPurreplan(trinn, idem);
    },
    tilbakestill: () => { linjer.value = ""; },
  });
  skjema.addEventListener("submit", () => {
    if (linjer.value && !parsePlanlinjer(linjer.value)) {
      sett(utfall, el("span", { role: "alert",
        text: t("ui.fordring.skjema.plan_feil") }));
    }
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.fordring.skjema.plan_tittel") }),
    skjema, utfall);
}

// Sammendraget. TALLENE KOMMER FRA SIN EGEN DØR og gjelder ALT.
function sammendrag(s) {
  const p = el("p", {
    text: t("ui.fordring.sammendrag")
      .replace("{apne}", String(s.apne))
      .replace("{apent}", belopTekst(s.apent_ore))
      .replace("{forfalte}", String(s.forfalte))
      .replace("{forfalt}", belopTekst(s.forfalt_ore))
      .replace("{i_purring}", String(s.i_purring)) });
  if (!s.har_purreplan) {
    // UTEN PLAN VET INGEN NÅR NOE ESKALERER. Setningen står som ord,
    // ikke som en tom tabell lenger nede.
    p.append(" ", el("strong", {
      text: t("ui.fordring.ingen_purreplan") }));
  }
  // AVKORTINGEN SIES HØYT.
  if (s.vist < s.apne) {
    p.append(" ", el("strong", {
      text: t("ui.fordring.avkortet").replace("{vist}", String(s.vist)) }));
  }
  return p;
}

export function visFordring(hoved, ctx) {
  const hode = () => flateHode(t("ui.fordring.tittel"),
    t("ui.fordring.undertittel"));
  sett(hoved, ...hode());
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kropp);
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/fordring"),
    (d) => {
      sett(hoved, ...hode(), kropp);
      const s = d.sammendrag || {};
      const fordringer = d.fordringer || [];
      const plan = d.purreplan || [];
      const detalj = detaljpanel(ctx, last);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.fordring.oversikt.tittel") }),
        sammendrag(s));

      const alder = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.fordring.alder.tittel") }),
        aldersTabell(d.aldersfordeling || []));

      const liste = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.fordring.liste.tittel") }));
      if (!fordringer.length) {
        liste.append(el("p", { class: "muted",
          text: t("ui.fordring.liste.ingen") }));
      } else {
        liste.append(fordringsTabell(fordringer, ctx, detalj.apne));
      }

      const planseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.fordring.plan.tittel") }));
      if (!plan.length) {
        planseksjon.append(el("p", { class: "muted",
          text: t("ui.fordring.ingen_purreplan") }));
      } else {
        planseksjon.append(
          el("p", { class: "muted",
            text: t("ui.fordring.plan.versjon")
              .replace("{versjon}", String(plan[0].versjon)) }),
          planTabell(plan));
      }

      const deler = [oversikt, alder, liste, planseksjon, detalj.node];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(nySkjema(ctx, last), planSkjema(ctx, last));
      }
      sett(kropp, ...deler);
    });
  last();
}
