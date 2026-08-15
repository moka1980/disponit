// Klient-ruting via hash (#/oversikt …). Én skall-rute holder; ingen server-
// side rutekonfig per flate. Ved navigasjon flyttes fokus til main-
// landemerket (WCAG: SPA-navigasjon skal annonseres/flytte fokus).
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
    flater[r](hoved, ctx);
    if (!forste && typeof hoved.focus === "function") hoved.focus();
    forste = false;
  }

  window.addEventListener("hashchange", naviger);
  return { naviger, gjeldende };
}
