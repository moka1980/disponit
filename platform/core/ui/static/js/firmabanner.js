// PRØVEPERIODEN I SKALLET (eiers vedtak 27/11: «Pause agentene, les +
// eksport»). Før sto fristen ingen steder kunden så den, og når den gikk
// ut, skjedde ingenting. Nå:
//   * de siste sju dagene: en rolig nedtelling,
//   * når den er over: at agentene står stille, at alt fortsatt kan ses
//     og eksporteres, og hvem man tar kontakt med.
// Banneret er en rad MELLOM toppmenyen og innholdet, så det står på hver
// flate uten å være en del av noen av dem (ruteren skriver om `hoved`).
import { el } from "./dom.js";
import { t } from "./i18n.js";

function dato(iso) {
  return iso ? iso.slice(0, 10).split("-").reverse().join(".") : "";
}

// REN funksjon: svaret fra /v1/firma/status → banneret, eller null.
export function firmabanner(s) {
  if (!s || !s.vis) return null;
  const kontakt = s.kontakt_epost
    ? [" ", el("a", { href: `mailto:${s.kontakt_epost}`,
        text: s.kontakt_epost })] : [];
  if (s.pauset) {
    const nokkel = s.status === "stengt" ? "ui.firmabanner.stengt"
      : "ui.firmabanner.utlopt";
    return el("div", { class: "banner guard firmabanner",
      role: "alert" },
      el("p", {}, el("strong", { text: t(`${nokkel}.tittel`) }), " ",
        t(`${nokkel}.tekst`), ...(kontakt.length
          ? [" ", t("ui.firmabanner.kontakt"), ...kontakt] : [])));
  }
  const n = s.dogn_igjen;
  const igjen = n <= 0 ? t("ui.firmabanner.siste_dag")
    : n === 1 ? t("ui.firmabanner.en_dag")
      : t("ui.firmabanner.dager").replace("{n}", String(n));
  return el("div", { class: "banner info firmabanner", role: "status" },
    el("p", {}, t("ui.firmabanner.prove").replace("{dato}",
      dato(s.prove_utloper)).replace("{igjen}", igjen),
    ...(kontakt.length ? [" ", t("ui.firmabanner.kontakt"), ...kontakt]
      : [])));
}
