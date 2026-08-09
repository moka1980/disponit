// Klient-ruting via hash (#/oversikt …). Én skall-rute holder; ingen server-
// side rutekonfig per flate. Ved navigasjon flyttes fokus til main-
// landemerket (WCAG: SPA-navigasjon skal annonseres/flytte fokus).
export function lagRuter(hoved, ctx, flater, settAktiv) {
  function gjeldende() {
    const h = (window.location.hash || "").replace(/^#\/?/, "");
    return flater[h] ? h : "oversikt";
  }

  let forste = true;
  function naviger() {
    const r = gjeldende();
    settAktiv(r);
    flater[r](hoved, ctx);
    if (!forste && typeof hoved.focus === "function") hoved.focus();
    forste = false;
  }

  window.addEventListener("hashchange", naviger);
  return { naviger, gjeldende };
}
