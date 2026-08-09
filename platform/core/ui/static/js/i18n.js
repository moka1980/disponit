// i18n — locales/ er ENESTE tekstkilde (klarsignal: ingen hardkodet tekst).
// Nøklene er flate, punktseparerte strenger; motorens maskinkoder ER nøkler.
// Ukjent nøkkel → trygt fall-back (oppgitt reserve, ellers nøkkelen selv),
// aldri tom streng og aldri et kast (konsolidert grunnlag §3).

const SPRAK = ["nb", "en"];
let _kart = {};
let _sprak = "nb";

export function velgSprak() {
  // Rekkefølge: lagret valg → <html data-sprak> → nettleser → nb.
  let s = null;
  try { s = window.localStorage.getItem("disponit_sprak"); } catch { s = null; }
  if (!SPRAK.includes(s)) s = document.documentElement.getAttribute("data-sprak");
  if (!SPRAK.includes(s)) {
    const nav = (navigator.language || "nb").slice(0, 2);
    s = SPRAK.includes(nav) ? nav : "nb";
  }
  return s;
}

export function lagreSprak(s) {
  if (!SPRAK.includes(s)) return;
  try { window.localStorage.setItem("disponit_sprak", s); } catch { /* ignore */ }
}

export async function lastI18n(sprak) {
  _sprak = SPRAK.includes(sprak) ? sprak : "nb";
  const r = await fetch(`/ui/locale/${_sprak}`, {
    credentials: "same-origin",
    headers: { accept: "application/json" },
  });
  if (!r.ok) throw new Error(`locale ${_sprak}: ${r.status}`);
  _kart = await r.json();
  document.documentElement.setAttribute("lang", _sprak);
  document.documentElement.setAttribute("data-sprak", _sprak);
  return _sprak;
}

// For tester (jsdom): sett kartet direkte uten nettverk.
export function settI18nForTest(kart, sprak = "nb") { _kart = kart; _sprak = sprak; }

export function t(nokkel, reserve) {
  const v = _kart[nokkel];
  if (typeof v === "string" && v.length) return v;
  return reserve != null ? reserve : nokkel;
}

export function harNokkel(nokkel) {
  return typeof _kart[nokkel] === "string" && _kart[nokkel].length > 0;
}

export function sprak() { return _sprak; }
export function meta() { return _kart._meta || {}; }
