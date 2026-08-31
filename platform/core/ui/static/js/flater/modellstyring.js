// Modellstyring (M-31 v1) — model card som AVLEDET leseflate (dom 4):
// flaten VISER registerets rader (gjeldende krav, golden-sett-hodet,
// registrerte kjøringer), den regner aldri noe selv. Hvert tall i
// tabellen står i en registerrad; det eneste flaten setter sammen er
// tekst («3 av 20» er to av svarets tall side om side, aldri en
// divisjon — M-16-regelen).
//
// TABELLEN ER TILGANGSFORMEN (m16-formen): ekte <table> med <caption>
// og th scope, utfall som TEKST (aldri kun farge), digest i kortform
// med full verdi i title-attributtet. Ingen mutasjonsknapper: dørene er
// deploy-CLI-er (registrer-m31-golden-sett/kjor-m31-evaluering), ikke
// HTTP.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson } from "../api.js";
import { Tidspunkt } from "../komponenter.js";
import { flateHode, kvRad, medStatus } from "./felles.js";

// Digestens kortform er PRESENTASJON: de første 12 tegnene ved siden av
// full verdi i `title`. Kortformen er aldri en identitet flaten regner
// videre på — sammenlikninger skjer i basen, mot hele verdien.
const DIGEST_KORT = 12;

function digestTekst(digest) {
  if (digest == null) return "—";
  const kort = digest.length > DIGEST_KORT
    ? `${digest.slice(0, DIGEST_KORT)}…` : digest;
  return el("code", { title: digest, text: kort });
}

// Utfallet som TEKST — og kravversjonen ved siden av, fordi «bestått»
// uten «mot hvilket krav» er halve dommen (dom 3: eksakt binding).
function utfallTekst(k) {
  if (k.kravversjon == null) return t("ui.modellstyring.uten_krav");
  return k.bestatt
    ? t("ui.modellstyring.bestatt_ja") : t("ui.modellstyring.bestatt_nei");
}

function kjoringstabell(modulId, kjoringer) {
  if (!kjoringer.length) {
    return el("p", { class: "muted",
      text: t("ui.modellstyring.ingen_kjoringer") });
  }
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.modellstyring.kort_caption")
      .replace("{modul}", modulId) }));
  tabell.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.modellstyring.kolonne.avsluttet") }),
    el("th", { scope: "col", text: t("ui.modellstyring.kolonne.digest") }),
    el("th", { scope: "col",
               text: t("ui.modellstyring.kolonne.kravversjon") }),
    el("th", { scope: "col", text: t("ui.modellstyring.kolonne.bestatt") }),
    el("th", { scope: "col", text: t("ui.modellstyring.kolonne.resultat") }),
    el("th", { scope: "col", text: t("ui.modellstyring.kolonne.p95") }),
    el("th", { scope: "col", text: t("ui.modellstyring.kolonne.modell") }))));
  const tbody = el("tbody");
  for (const k of kjoringer) {
    // Cellen som NAVNGIR raden (kjøringens tidspunkt) er en overskrift
    // (m16-regelen): uten scope="row" mister en skjermleser i tall-
    // kolonnene hvilken kjøring tallet gjelder.
    const radnavn = el("th", { scope: "row" }, Tidspunkt(k.avsluttet_ts, {}));
    tbody.append(el("tr", {},
      radnavn,
      el("td", {}, digestTekst(k.artifact_digest)),
      el("td", { text: k.kravversjon == null
        ? t("ui.modellstyring.uten_krav") : String(k.kravversjon) }),
      el("td", { text: utfallTekst(k) }),
      // To av svarets tall side om side — aldri en utregnet andel.
      el("td", { text: `${k.antall_bestatt} / ${k.antall_eksempler}` }),
      el("td", { text: String(k.p95_ms) }),
      el("td", { text: k.modellnavn })));
  }
  tabell.append(tbody);
  return tabell;
}

// Model card-blokken: gjeldende krav + settet + siste beståtte, som
// dt/dd-par. `null` er en SETNING («ingen gjeldende krav»), aldri et
// skjult felt — en modul under seeding skal kunne leses ærlig.
function modellkort(m) {
  const kort = el("section", { class: "kpi-kort" },
    el("h3", { text: m.modul_id }));
  const dl = el("dl", { class: "kv-liste" });
  if (m.krav) {
    kvRad(dl, t("ui.modellstyring.felt.kravversjon"),
      String(m.krav.kravversjon));
    kvRad(dl, t("ui.modellstyring.felt.sett"),
      `${m.krav.sett_id} v${m.krav.sett_versjon}`);
    kvRad(dl, t("ui.modellstyring.felt.terskel_andel"),
      String(m.krav.terskel_min_andel));
    kvRad(dl, t("ui.modellstyring.felt.terskel_p95"),
      m.krav.terskel_maks_p95_ms == null
        ? t("ui.modellstyring.ikke_satt")
        : String(m.krav.terskel_maks_p95_ms));
    kvRad(dl, t("ui.modellstyring.felt.terskel_modellfeil"),
      String(m.krav.terskel_maks_modellfeil));
  } else {
    kort.append(el("p", { class: "muted",
      text: t("ui.modellstyring.krav_ingen") }));
  }
  if (m.sett) {
    kvRad(dl, t("ui.modellstyring.felt.antall_eksempler"),
      String(m.sett.antall_eksempler));
  }
  const siste = el("span");
  if (m.siste_bestatte) {
    sett(siste, digestTekst(m.siste_bestatte.artifact_digest), " — ",
      Tidspunkt(m.siste_bestatte.avsluttet_ts, {}));
  } else {
    sett(siste, t("ui.modellstyring.siste_bestatte_ingen"));
  }
  kvRad(dl, t("ui.modellstyring.felt.siste_bestatte"), siste);
  kort.append(dl, kjoringstabell(m.modul_id, m.kjoringer));
  return kort;
}

export function visModellstyring(hoved, ctx) {
  sett(hoved, ...flateHode(t("ui.modellstyring.tittel"),
    t("ui.modellstyring.undertittel")));
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kropp);
  medStatus(hoved, ctx,
    () => hentJson("/v1/modellstyring"),
    (d) => {
      sett(hoved, ...flateHode(t("ui.modellstyring.tittel"),
        t("ui.modellstyring.undertittel")), kropp);
      if (!d.moduler.length) {
        sett(kropp, el("p", { class: "muted",
          text: t("ui.modellstyring.ingen") }));
        return;
      }
      sett(kropp, ...d.moduler.map(modellkort));
    });
}
