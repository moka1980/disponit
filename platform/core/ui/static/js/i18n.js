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

// KUN DEN NYESTE LASTINGEN FÅR SKRIVE (Codex P2). Språkvelgeren står åpen mens
// locale-settet hentes, så på en treg linje kan en bruker rekke å velge to
// ganger — og de to hentingene kan lande i motsatt rekkefølge av valgene.
// Skrev begge, satt den TREGESTE til slutt: `_kart` og `<html lang>` endte på
// et språk brukeren hadde forlatt, mens flaten på skjermen var rendret fra det
// andre. Nummeret gjør rekkefølgen eksplisitt: en henting som ikke lenger er
// den siste som ble startet, forkastes i stedet for å commite.
//
// Merk at `_sprak` settes FØRST etter hentingen, ikke før: satt på forhånd
// ville `sprak()` og `t()` rapportert et språk hvis tekster ennå ikke var
// lastet — og etter en forkastet henting rapportert et språk som aldri kom.
//
// Retur: språket som ble tatt i bruk, eller `null` når hentingen ble forbigått.
// Kallere som rendrer etterpå MÅ sjekke — `null` betyr «et nyere valg eier
// flaten nå, ikke tegn over det».
let _lasteNr = 0;

export async function lastI18n(sprak) {
  const nr = ++_lasteNr;
  const s = SPRAK.includes(sprak) ? sprak : "nb";
  const r = await fetch(`/ui/locale/${s}`, {
    credentials: "same-origin",
    headers: { accept: "application/json" },
  });
  if (!r.ok) throw new Error(`locale ${s}: ${r.status}`);
  const kart = await r.json();
  if (nr !== _lasteNr) return null;
  _sprak = s;
  _kart = kart;
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
