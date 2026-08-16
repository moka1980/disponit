// Klient-ruting via hash (#/oversikt …). Én skall-rute holder; ingen server-
// side rutekonfig per flate. Ved navigasjon flyttes fokus til main-
// landemerket (WCAG: SPA-navigasjon skal annonseres/flytte fokus).
import { t } from "./i18n.js";
import { settDokumenttittel } from "./komponenter.js";

export function lagRuter(hoved, ctx, flater, settAktiv) {
  // Reserveruten er den FØRSTE flaten økten faktisk har, ikke hardkodet
  // `oversikt`. `flater` er allerede scope-filtrert av `tillatteFlater`, så en
  // økt uten `decisions:read` — f.eks. en ren `platform:admin`-økt — har ingen
  // `oversikt`-oppføring her. Var reserven hardkodet, slo en tom eller ugyldig
  // hash rett i `flater["oversikt"](...)`, altså et kall på `undefined`: appen
  // kastet under bootstrap og ble stående blank. Rekkefølgen er `byggRuter`
  // sin, så en vanlig kundeøkt får fortsatt `oversikt`.
  const reserve = Object.keys(flater)[0] || null;

  function gjeldende() {
    const h = (window.location.hash || "").replace(/^#\/?/, "");
    return flater[h] ? h : reserve;
  }

  let forste = true;
  function naviger() {
    const r = gjeldende();
    // Ingen rute i det hele tatt: en økt hvis roller ikke ga ETT kjent scope
    // (`scopes_for_roller` er default-deny og gir tom mengde for ukjente
    // roller). Da finnes det ingen flate å vise, og alternativet til å la være
    // er å kaste. Skallet står igjen med sin egen tomtilstand.
    if (!r) return;
    settAktiv(r);
    // Samme krav som på forsiden (Codex P2): flatene her er like direkte
    // navigerbare, og uten dette ville forsidens SISTE tittel — «Logg inn» —
    // blitt stående som navn på hver eneste flate bak innlogging.
    settDokumenttittel(t(`ui.nav.${r}`, r));
    flater[r](hoved, ctx);
    if (!forste && typeof hoved.focus === "function") hoved.focus();
    forste = false;
  }

  window.addEventListener("hashchange", naviger);

  // Ruteren MÅ kunne rives ned igjen (Codex P2). Den lever på et globalt
  // `hashchange`, men rendrer inn i ETT bestemt `hoved`-element — og det
  // elementet blir løsrevet i det skallet bygges på nytt (språkbytte,
  // utlogging). Uten opprydding ble hver nye ruter lagt PÅ TOPPEN av de
  // gamle: én navigasjon kalte da alle sammen, med ett sett API-kall per
  // ruter, og alt utenom den nyeste ble skrevet inn i et tre ingen ser.
  // Antallet vokste for hvert bytte. `stopp` er tålig å kalle to ganger.
  let stoppet = false;
  function stopp() {
    if (stoppet) return;
    stoppet = true;
    window.removeEventListener("hashchange", naviger);
  }

  return { naviger, gjeldende, stopp };
}
