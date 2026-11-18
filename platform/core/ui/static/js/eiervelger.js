// Eiervelgeren: seks registreringsskjemaer ba om eieren som rå bruker-id,
// en verdi ingen kunde kjenner. Feltet BERIKES i stedet for å byttes ut:
// når medlemslisten er hentet, står en nedtrekksliste med navn der feltet
// sto, og feltobjektet skjemaet holder blir en stedfortreder for den —
// `eier.value` leses og nullstilles som før, så ingen skjemakode endres.
//
// Doktrinen står: feltet er PÅKREVD og TOMT. Første valg er «Velg eier»
// med tom verdi, og den innloggede velges aldri for brukeren.
//
// Feiler hentingen, eller er lista tom, står det opprinnelige feltet urørt.
import { el } from "./dom.js";
import { t } from "./i18n.js";
import { hentMedlemmer } from "./api.js";

export function lagVelger(input, medlemmer) {
  const valg = el("select", {
    id: input.id || null, name: input.name || null,
    required: input.required ? true : null,
    "aria-describedby": input.getAttribute("aria-describedby"),
  });
  valg.append(el("option", { value: "", text: t("ui.eier.velg") }));
  for (const m of medlemmer) {
    valg.append(el("option", { value: m.bruker_id,
      text: m.navn || t("ui.eier.uten_navn") }));
  }
  if (input.value) valg.value = input.value;
  Object.defineProperty(input, "value", {
    configurable: true,
    get: () => valg.value,
    set: (v) => { valg.value = v == null ? "" : String(v); },
  });
  input.replaceWith(valg);
  return valg;
}

export function berikEierfelt(input) {
  return hentMedlemmer().then((d) => {
    const medlemmer = Array.isArray(d && d.medlemmer) ? d.medlemmer : [];
    if (!medlemmer.length || !input.parentNode) return null;
    return lagVelger(input, medlemmer);
  }).catch(() => null);
}
