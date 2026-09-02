// Identitets- og tilgangsagenten (M-12 v1) — TILGANGSREGISTERET som to
// lister: hvem som har hvilken tilgang til hva, og hvilke avvik sveipen
// har funnet.
//
// FLATEN PROVISJONERER INGENTING, og det skal SES. Katalogteksten lover
// JML — joiner, mover, leaver — og v1 gjør ingen av delene. Derfor
// finnes det ingen «Fjern tilgang»-knapp her, ingen «Flytt til ny
// avdeling», ingen «Opprett i Microsoft 365». En knapp som ikke gjør
// noe i systemet den navngir er en løgn om hva plattformen kan, og på
// akkurat dette området er den løgnen farlig: den som tror en tilgang
// ble fjernet, sjekker ikke om den fortsatt finnes.
//
// DE TRE KNAPPENE SOM FINNES REGISTRERER: et objekt, en tilgang, og en
// gjennomgang. Den siste er attestasjonen «jeg har sett på denne, og
// den skal fortsatt finnes» — signert av den innloggede, i dag. Datoen
// kan ikke tilbakedateres, og navnet kan ikke settes til en annens:
// begge deler håndheves i basen (097s radvakt), ikke her.
//
// FORFALT ER TEKST, ALDRI BARE FARGE. «Forfalt» og «om N døgn» står som
// ord i sin egen celle (WCAG 1.4.1) — og på den forfalte raden står
// ordet i tillegg som et eget merke, fordi det er den ene opplysningen
// som ikke tåler å bli oversett.
//
// FLATEN VISER, DEN REGNER IKKE. `dogn_til_gjennomgang` er regnet i
// BASEN, i samme skann som raden (097s lesedør), nettopp for at flaten
// ikke skal trekke to datoer fra hverandre.
//
// TABELLENE ER EKTE (m16-formen): <caption>, th[scope=col] på kolonnene
// og th[scope=row] på cellen som navngir raden. Begge ligger i en
// `.tablewrap` — uten den gjelder `width: 100%`, og nettleseren klemmer
// kolonnene mot min-content i stedet for å la tabellen scrolle.
// Systemnavn og kontonavn er lange og skal BRYTE, ikke skyve
// sidescrollen ut: `.celle-id` på td-en/th-en (aldri på et span —
// `max-width` gjør ingenting på et inline-element).
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, hentJson, nyIdempotensnokkel, registrerGjennomgang,
  registrerTilgang, registrerTilgangsobjekt,
} from "../api.js";
import { Tidspunkt, meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

const KRITIKALITETER = ["lav", "middels", "hoy", "kritisk"];
const SUBJEKTTYPER = ["person", "tjenestekonto"];
const NIVAER = ["les", "skriv", "admin"];

// Fristkolonnens ORD. Ett tall inn, én setning ut — ingen utregning.
//
// ENTALL HAR SIN EGEN NØKKEL (096s lærdom). Locale-settet har ingen
// pluralmaskineri, og «in 1 days» ville stått på nøyaktig den raden et
// menneske leser først — den som forfaller i morgen. Norsk «døgn» bøyes
// ikke og hadde klart seg; engelsk gjør det, og en oversettelse som er
// riktig bare på det ene språket er ikke riktig.
function fristTekst(dogn) {
  if (typeof dogn !== "number") return "—";
  if (dogn < 0) {
    const n = Math.abs(dogn);
    return n === 1
      ? t("ui.tilgang.forfalt_for_ett_dogn")
      : t("ui.tilgang.forfalt_for").replace("{dogn}", String(n));
  }
  if (dogn === 0) return t("ui.tilgang.forfaller_i_dag");
  if (dogn === 1) return t("ui.tilgang.om_ett_dogn");
  return t("ui.tilgang.om_dogn").replace("{dogn}", String(dogn));
}

function erForfalt(tg) {
  return typeof tg.dogn_til_gjennomgang === "number"
    && tg.dogn_til_gjennomgang < 0;
}

function eierTekst(tg) {
  // Visningsnavnet der IdP-en ga et; bruker-id-en ellers. En tom celle
  // ville vært det eneste svaret som ikke lar noen finne eieren.
  return tg.eier_navn || tg.eier_bruker_id || t("ui.tilgang.ukjent_eier");
}

function objekttekst(tg) {
  return `${tg.system} — ${tg.objektnavn}`;
}

function tilgangsrad(tg, ctx, paaGjennomgang) {
  const rad = el("tr", {});
  // Objektet NAVNGIR raden — det er «hva» i «hvem har tilgang til hva»,
  // og det er kolonnen tabellen er sortert innenfor. `.celle-id` fordi
  // systemnavn er lange og skal BRYTE; klassen står på th-en, ikke på
  // et span inni den, fordi `max-width` ikke gjør noe på inline.
  rad.append(el("th", { scope: "row", class: "celle-id",
                       text: objekttekst(tg) }));
  rad.append(el("td", { text: t(`ui.tilgang.kritikalitet.${tg.kritikalitet}`,
    tg.kritikalitet) }));
  rad.append(el("td", { class: "celle-id", text: tg.subjekt }));
  rad.append(el("td", { text: t(`ui.tilgang.niva.${tg.niva}`, tg.niva) }));
  rad.append(el("td", { class: "celle-id", text: eierTekst(tg) }));
  // HJEMMELEN ER EN KOLONNE, ikke en fotnote eller et hover-felt. Den er
  // halve svaret på «skal denne tilgangen finnes», og den skal kunne
  // leses ved siden av eieren. `.celle-tekst` på td-en gir den en
  // lesbar bredde uten å presse resten av tabellen ut.
  rad.append(el("td", { class: "celle-tekst", text: tg.hjemmel }));
  const fristcelle = el("td", {});
  sett(fristcelle, Tidspunkt(tg.gjennomgang_frist, {}), " ",
    el("span", { class: "muted",
      text: fristTekst(tg.dogn_til_gjennomgang) }));
  if (erForfalt(tg)) {
    // MERKET ER TEKST. En rød rad alene sier ingenting til den som ikke
    // ser farge, og «forfalt» er nettopp den opplysningen som ikke tåler
    // å bli oversett — her betyr den at ingen har svart for at tilgangen
    // fortsatt skal finnes.
    fristcelle.append(" ", el("strong", { class: "merke",
      text: t("ui.tilgang.merke_forfalt") }));
  }
  fristcelle.append(el("p", { class: "muted",
    text: tg.sist_gjennomgatt_av
      ? t("ui.tilgang.gjennomgatt_av")
        .replace("{navn}", tg.sist_gjennomgatt_av)
      : t("ui.tilgang.aldri_gjennomgatt") }));
  rad.append(fristcelle);
  const handling = el("td", {});
  if (harScope(ctx, "bestilling:opprett")) {
    const knapp = el("button", { type: "button",
      text: t("ui.tilgang.knapp.gjennomgang") });
    knapp.addEventListener("click", () => paaGjennomgang(tg));
    handling.append(knapp);
  }
  rad.append(handling);
  return rad;
}

function tilgangstabell(tilganger, ctx, paaGjennomgang) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.tilgang.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.tilgang.kolonne.objekt") }),
    el("th", { scope: "col", text: t("ui.tilgang.kolonne.kritikalitet") }),
    el("th", { scope: "col", text: t("ui.tilgang.kolonne.subjekt") }),
    el("th", { scope: "col", text: t("ui.tilgang.kolonne.niva") }),
    el("th", { scope: "col", text: t("ui.tilgang.kolonne.eier") }),
    el("th", { scope: "col", text: t("ui.tilgang.kolonne.hjemmel") }),
    el("th", { scope: "col", text: t("ui.tilgang.kolonne.gjennomgang") }),
    el("th", { scope: "col", text: t("ui.tilgang.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const tg of tilganger) {
    tbody.append(tilgangsrad(tg, ctx, paaGjennomgang));
  }
  tb.append(tbody);
  // `.tablewrap` er sidescrollens container — uten den er tabellen bundet
  // til `width: 100%` og klemmer kolonnene mot min-content i stedet for
  // å kunne bli bredere (se komponenter.css).
  return el("div", { class: "tablewrap" }, tb);
}

function funntabell(funn) {
  const tb = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.tilgang.funn.caption") }));
  tb.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.tilgang.funnkolonne.subjekt") }),
    el("th", { scope: "col", text: t("ui.tilgang.funnkolonne.system") }),
    el("th", { scope: "col", text: t("ui.tilgang.funnkolonne.funntype") }),
    el("th", { scope: "col", text: t("ui.tilgang.funnkolonne.frist") }),
    el("th", { scope: "col",
      text: t("ui.tilgang.funnkolonne.forst_sett") }))));
  const tbody = el("tbody");
  for (const f of funn) {
    const rad = el("tr", {});
    rad.append(el("th", { scope: "row", class: "celle-id",
                         text: f.subjekt }));
    rad.append(el("td", { class: "celle-id", text: f.system }));
    // FUNNTYPEN ER OVERSATT, aldri en rå maskinkode på skjermen.
    rad.append(el("td", { text: t(`ui.tilgang.funntype.${f.funntype}`,
      f.funntype) }));
    rad.append(el("td", { text: f.frist || "—" }));
    const sett_celle = el("td", {});
    sett(sett_celle, Tidspunkt(f.forst_sett, {}));
    rad.append(sett_celle);
    tbody.append(rad);
  }
  tb.append(tbody);
  return el("div", { class: "tablewrap" }, tb);
}

// ÉN GRUPPE PER FELT (klynge 1s lærdom 4): etikett, kontroll og
// hjelpetekst hører sammen. Ligger de som løse søsken, sprer rutenettet
// dem i hver sin celle, og etiketten mister den visuelle koblingen til
// feltet sitt uansett hva `for`-attributtet sier.
function felt(id, etikett, kontroll, hjelp) {
  return el("div", { class: "felt" },
    el("label", { for: id, text: etikett }),
    kontroll,
    hjelp ? el("p", { class: "muted", text: hjelp }) : null);
}

function valgfelt(id, navn, verdier, nokkelprefiks) {
  const s = el("select", { id, name: navn });
  for (const v of verdier) {
    s.append(el("option", { value: v, text: t(`${nokkelprefiks}.${v}`, v) }));
  }
  return s;
}

// Registreringsskjemaet for OBJEKTET. Det står først fordi rekkefølgen
// er ekte: en tilgang kan ikke registreres til et objekt som ikke
// finnes, og registeret nekter (fremmednøkkelen i 097).
function objektskjema(ctx, last) {
  const boks = el("div", { class: "skjemaboks" });
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const system = el("input", { id: "tilgang-system", name: "system",
    type: "text", required: true, maxlength: 200 });
  const navn = el("input", { id: "tilgang-objektnavn", name: "navn",
    type: "text", required: true, maxlength: 200 });
  const krit = valgfelt("tilgang-kritikalitet", "kritikalitet",
    KRITIKALITETER, "ui.tilgang.kritikalitet");
  const knapp = el("button", { type: "submit",
    text: t("ui.tilgang.knapp.registrer_objekt") });
  skjema.append(
    felt("tilgang-system", t("ui.tilgang.objektskjema.system"), system,
      t("ui.tilgang.objektskjema.systemhjelp")),
    felt("tilgang-objektnavn", t("ui.tilgang.objektskjema.navn"), navn,
      t("ui.tilgang.objektskjema.navnhjelp")),
    felt("tilgang-kritikalitet", t("ui.tilgang.objektskjema.kritikalitet"),
      krit),
    // Knappen står i en egen bunnrad over hele bredden: en send-knapp
    // inne i en feltkolonne leses som «send inn DETTE feltet».
    el("div", { class: "skjema-bunn" }, knapp));

  // Én nøkkel per intensjon (PR-014 R1): nullstilles når innholdet
  // endres, og ved 4xx — et avvist forsøk har FORBRUKT nøkkelen, og et
  // rettet objekt er en ny intensjon som skal ha sin egen.
  let idem = null;
  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled) return;
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    try {
      await registrerTilgangsobjekt({
        system: system.value, navn: navn.value,
        kritikalitet: krit.value,
      }, idem);
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.tilgang.feil.tilstand")
          : t("ui.tilgang.feil.generell") }));
      return;
    }
    system.value = ""; navn.value = "";
    idem = null;
    knapp.disabled = false;
    meldLive(t("ui.tilgang.objektskjema.ok"));
    sett(utfall, el("span", { text: t("ui.tilgang.objektskjema.ok") }));
    last();
  });
  boks.append(el("h3", { text: t("ui.tilgang.objektskjema.tittel") }),
    skjema, utfall);
  return boks;
}

// Registreringsskjemaet for TILGANGEN.
//
// EIEREN VELGES EKSPLISITT — feltet er påkrevd og har ingen
// forhåndsutfylt verdi. Den som fører tilgangen inn i registeret er
// sjelden den som skal svare for at den finnes, og en flate som stille
// satte innloggeren som eier ville gjort «tilganger uten eier» sann på
// papiret og falsk i praksis.
function tilgangsskjema(ctx, objekter, last) {
  const boks = el("div", { class: "skjemaboks" });
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  // Objektet VELGES fra listen, det skrives ikke inn som en UUID: et
  // fritekstfelt for id-en er den korteste veien til at en tilgang blir
  // registrert på feil system, og en tilgang på feil system er en
  // tilgang ingen finner igjen når den skal etterprøves.
  const objekt = el("select", { id: "tilgang-objekt", name: "objekt_id" });
  for (const o of objekter) {
    objekt.append(el("option", { value: o.objekt_id,
      text: `${o.system} — ${o.navn}` }));
  }
  const subjekt = el("input", { id: "tilgang-subjekt", name: "subjekt",
    type: "text", required: true, maxlength: 320 });
  const subjekttype = valgfelt("tilgang-subjekttype", "subjekttype",
    SUBJEKTTYPER, "ui.tilgang.subjekttype");
  const niva = valgfelt("tilgang-niva", "niva", NIVAER, "ui.tilgang.niva");
  const eier = el("input", { id: "tilgang-eier", name: "eier_bruker_id",
    type: "text", required: true, maxlength: 128 });
  const hjemmel = el("input", { id: "tilgang-hjemmel", name: "hjemmel",
    type: "text", required: true, maxlength: 500 });
  const dogn = el("input", { id: "tilgang-dogn", name: "gjennomgang_dogn",
    type: "number", required: true, min: 1, max: 3650, value: "90" });
  const knapp = el("button", { type: "submit",
    text: t("ui.tilgang.knapp.registrer_tilgang") });
  skjema.append(
    felt("tilgang-objekt", t("ui.tilgang.tilgangsskjema.objekt"), objekt,
      t("ui.tilgang.tilgangsskjema.objekthjelp")),
    felt("tilgang-subjekt", t("ui.tilgang.tilgangsskjema.subjekt"), subjekt,
      t("ui.tilgang.tilgangsskjema.subjekthjelp")),
    felt("tilgang-subjekttype", t("ui.tilgang.tilgangsskjema.subjekttype"),
      subjekttype),
    felt("tilgang-niva", t("ui.tilgang.tilgangsskjema.niva"), niva),
    felt("tilgang-eier", t("ui.tilgang.tilgangsskjema.eier"), eier,
      t("ui.tilgang.tilgangsskjema.eierhjelp")),
    felt("tilgang-hjemmel", t("ui.tilgang.tilgangsskjema.hjemmel"), hjemmel,
      t("ui.tilgang.tilgangsskjema.hjemmelhjelp")),
    felt("tilgang-dogn", t("ui.tilgang.tilgangsskjema.gjennomgang_dogn"),
      dogn, t("ui.tilgang.tilgangsskjema.gjennomganghjelp")),
    el("div", { class: "skjema-bunn" }, knapp));

  let idem = null;
  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled || !objekt.value) return;
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    try {
      await registrerTilgang({
        objekt_id: objekt.value, subjekt: subjekt.value,
        subjekttype: subjekttype.value, niva: niva.value,
        eier_bruker_id: eier.value, hjemmel: hjemmel.value,
        gjennomgang_dogn: Number(dogn.value),
      }, idem);
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.tilgang.feil.tilstand")
          : t("ui.tilgang.feil.generell") }));
      return;
    }
    subjekt.value = ""; eier.value = ""; hjemmel.value = "";
    idem = null;
    knapp.disabled = false;
    meldLive(t("ui.tilgang.tilgangsskjema.ok"));
    sett(utfall, el("span", { text: t("ui.tilgang.tilgangsskjema.ok") }));
    last();
  });
  boks.append(el("h3", { text: t("ui.tilgang.tilgangsskjema.tittel") }),
    skjema, utfall);
  return boks;
}

export function visTilgang(hoved, ctx) {
  const hode = () => flateHode(t("ui.tilgang.tittel"),
    t("ui.tilgang.undertittel"));
  sett(hoved, ...hode());
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kropp);
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/tilgang"),
    (d) => {
      sett(hoved, ...hode(), kropp);
      const tilganger = d.tilganger || [];
      const objekter = d.objekter || [];
      const funn = d.funn || [];

      // BEKREFTELSEN MELDES I APPENS EGEN LIVE-REGION når en gjennomgang
      // registreres: `last()` tegner hele flaten på nytt, så en melding
      // som bare sto i en boks her ville rukket å bli skrevet og revet
      // bort i samme tikk — synlig for ingen, annonsert for ingen.
      const paaGjennomgang = async (tg) => {
        try {
          await registrerGjennomgang(tg.tilgang_id);
        } catch (e) {
          if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
          meldLive(e && e.status === 409
            ? t("ui.tilgang.feil.tilstand")
            : t("ui.tilgang.feil.generell"));
          return;
        }
        meldLive(t("ui.tilgang.gjennomgang.ok"));
        last();
      };

      const registeret = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.tilgang.liste.tittel") }));
      if (!tilganger.length) {
        // ÆRLIG TOMTILSTAND: et tomt tilgangsregister betyr ikke at
        // ingen har tilgang — det betyr at ingen har skrevet ned hvem
        // som har det, og setningen sier nettopp det.
        registeret.append(el("p", { class: "muted",
          text: t("ui.tilgang.liste.ingen") }));
      } else {
        registeret.append(tilgangstabell(tilganger, ctx, paaGjennomgang));
      }

      const funnseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.tilgang.funn.tittel") }));
      if (!funn.length) {
        // …og en tom funnliste sier at det er SVEIPEN som fyller den, så
        // en vert der timeren aldri har kjørt ikke leses som «i orden».
        funnseksjon.append(el("p", { class: "muted",
          text: t("ui.tilgang.funn.ingen") }));
      } else {
        funnseksjon.append(funntabell(funn));
      }

      const deler = [funnseksjon, registeret];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(objektskjema(ctx, last));
        // Tilgangsskjemaet finnes bare når det ER et objekt å velge:
        // et skjema med en tom nedtrekksliste er en knapp som alltid
        // feiler, og objektskjemaet over sier hva som mangler.
        if (objekter.length) {
          deler.push(tilgangsskjema(ctx, objekter, last));
        }
      }
      sett(kropp, ...deler);
    });
  last();
}
