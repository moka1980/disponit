// BLI MED I BEDRIFTEN — flaten en invitasjonslenke lander på.
//
// Lenken ser slik ut, og admin sender den slik hun vil:
//
//     https://disponit.com/?visning=blimed&t=<firma>&k=<token>
//
// TOKENET OVERLEVER OIDC-RUNDEN. Den inviterte er som regel IKKE logget
// inn når hun åpner lenken, så den offentlige siden sender henne gjennom
// Google med `retursti` satt til nøyaktig denne adressen. `trygg_retursti`
// (sesjon.py) beholder query-strengen på en lokal path — målt: både `t` og
// `k` kommer helskinnet tilbake.
//
// ADRESSEN RYDDES, MEN ER IKKE VERNET
// `k=` fjernes fra adressefeltet straks den er lest, så lenken ikke blir
// liggende i nettleserhistorikken på en delt maskin. Men ryddingen er
// HYGIENE, ikke sikkerhet: `replaceState` kaster i sandkasser (ruter.js har
// samme `try`), og feiler den, står tokenet der fortsatt.
//
// SIKKERHETEN ER AT TOKENET ER ENGANGS. Har hun først innløst det, er en
// kopi i historikken verdiløs — døra svarer `invitasjon_ugyldig` på andre
// forsøk. Det er den egenskapen vi hviler på, ikke på at adressen ble ren.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { innloesInvitasjon, ApiFeil, UautorisertFeil } from "../api.js";
import { meldLive } from "../komponenter.js";
import { flateHode } from "./felles.js";

export function lesInvitasjon(sok) {
  const q = new URLSearchParams(sok || "");
  const tenant = (q.get("t") || "").trim();
  const token = (q.get("k") || "").trim();
  return tenant && token ? { tenant, token } : null;
}

export function ryddAdressen(win = window) {
  // Fjerner BARE `k`. `t` og `visning` blir stående, så en oppfriskning
  // fortsatt viser riktig flate — og feilmeldingen nevner riktig firma.
  try {
    const u = new URL(win.location.href);
    if (!u.searchParams.has("k")) return false;
    u.searchParams.delete("k");
    win.history.replaceState(null, "", u.pathname + u.search + u.hash);
    return true;
  } catch {
    // Sandkasser nekter `replaceState` (ruter.js: «adressefeltet er ikke
    // vår å stole på»). Tokenet blir stående — og det er greit, fordi
    // engangsbruken er det som verner det.
    return false;
  }
}

export function visBliMed(hoved, ctx = {}) {
  const inv = lesInvitasjon(window.location.search);
  if (!inv) {
    sett(hoved, ...flateHode(t("ui.blimed.tittel"),
                             t("ui.blimed.mangler_undertittel")),
         el("p", { text: t("ui.blimed.mangler_tekst") }));
    return;
  }

  const melding = el("p", { class: "melding", role: "status" });
  const knapp = el("button", { type: "button", class: "knapp primar",
                               text: t("ui.blimed.knapp") });
  sett(hoved, ...flateHode(t("ui.blimed.tittel"),
                           t("ui.blimed.undertittel").replace("{firma}",
                                                              inv.tenant)),
       el("p", { text: t("ui.blimed.tekst").replace("{firma}", inv.tenant) }),
       el("div", { class: "knapperad" }, knapp), melding);

  const visFeil = (nokkel) => {
    melding.classList.add("feil");
    sett(melding, t(nokkel));
    meldLive(t(nokkel));
    knapp.disabled = false;
  };

  knapp.addEventListener("click", async () => {
    knapp.disabled = true;
    melding.classList.remove("feil");
    sett(melding, "");
    try {
      const svar = await innloesInvitasjon(inv.tenant, inv.token);
      // ADRESSEN RYDDES ETTER at innløsningen lyktes — ikke før. Rydder vi
      // først, og kallet feiler, har hun mistet lenken uten å ha blitt
      // medlem, og ingenting å prøve på nytt med.
      ryddAdressen();
      sett(hoved, ...flateHode(t("ui.blimed.ferdig_tittel"),
                               t("ui.blimed.ferdig_undertittel")),
           el("p", { text: t("ui.blimed.ferdig_tekst")
             .replace("{firma}", svar.tenant)
             .replace("{roller}", (svar.roller || []).join(", ")) }),
           el("p", { class: "knapperad" },
             el("a", { class: "knapp primar", href: "/",
                       text: t("ui.blimed.logg_inn_paa_nytt") })));
      meldLive(t("ui.blimed.ferdig_tittel"));
    } catch (f) {
      if (f instanceof UautorisertFeil) visFeil("ui.blimed.feil.okt");
      else if (f instanceof ApiFeil && f.kode === "invitasjon_ugyldig") {
        visFeil("ui.blimed.feil.ugyldig");
      } else visFeil("ui.blimed.feil.ukjent");
    }
  });
}
