// Innloggingsflate. OIDC-start skjer som TOPPNIVÅ-NAVIGASJON via et ordinært
// same-origin <form method="post"> (klarsignal V2) — ALDRI fetch(), som ville
// prøvd å følge 303-redirecten til IdP-en gjennom CORS og feilet stille.
// provider_id kommer fra /ui/oppsett.json (deploy-satt per arbeidsområde),
// aldri hardkodet i klienten.
import { el, sett } from "./dom.js";
import { t } from "./i18n.js";
import { hentJson } from "./api.js";
import { Feiltilstand } from "./komponenter.js";

export async function visInnlogging() {
  const app = document.getElementById("app");
  let provider = null;
  try {
    const o = await hentJson("/ui/oppsett.json");
    provider = o && typeof o.provider_id === "string" ? o.provider_id : null;
  } catch { provider = null; }

  const hoved = el("main", { id: "hovedinnhold", class: "skall-hoved innlogging",
    tabindex: "-1" },
    el("h1", { text: t("ui.logg_inn_tittel") }),
    el("p", { text: t("ui.logg_inn_tekst") }));

  if (provider) {
    const form = el("form", { class: "innlogging-form", method: "post",
      action: "/v1/oidc/start" });
    form.append(
      el("input", { type: "hidden", name: "provider_id", value: provider }),
      // Lokal retur-sti (validert server-side av trygg_retursti).
      el("input", { type: "hidden", name: "retursti", value: "/" }),
      el("button", { type: "submit", class: "knapp primar",
        text: t("ui.logg_inn") }));
    hoved.append(form);
  } else {
    hoved.append(Feiltilstand({ tittel: t("ui.feil_tittel"),
      tekst: t("ui.logg_inn_utilgjengelig") }));
  }

  sett(app, hoved);
  app.setAttribute("aria-busy", "false");
  document.documentElement.setAttribute("data-visning", "innlogging");
}
