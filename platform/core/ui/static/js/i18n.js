// i18n — locales/ er ENESTE tekstkilde (klarsignal: ingen hardkodet tekst).
// Nøklene er flate, punktseparerte strenger; motorens maskinkoder ER nøkler.
// Ukjent nøkkel → trygt fall-back (oppgitt reserve, ellers nøkkelen selv),
// aldri tom streng og aldri et kast (konsolidert grunnlag §3).

const SPRAK = ["nb", "en"];
let _kart = {};
let _sprak = "nb";
// Bare det SISTE språkvalget får skrive (Codex P2 til PR #42). Tilstanden her
// er delt av hele flaten, og `lastI18n` er asynkron: to bytter i rask
// rekkefølge ga to hentinger som kunne svare i motsatt rekkefølge av den de
// ble bedt om. Uten dette telleverket committet den TREGESTE sist, og flaten
// ble stående med ett språks tekster, et annet i `<html lang>` og et tredje i
// velgeren. Hver henting merker seg sitt nummer og trekker seg hvis et nyere
// kall har startet.
let _generasjon = 0;

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

// Returnerer språket som FAKTISK gjelder etterpå — ikke nødvendigvis det som
// ble bedt om. Ble kallet forbigått av et nyere, rører det ingenting og gir
// tilbake det gjeldende språket; kalleren ser da at svaret ikke er dens eget
// og lar være å rendre. Ingenting settes før hentingen er i havn, så et
// forbigått eller feilende kall etterlater seg ingen halv tilstand.
export async function lastI18n(sprak) {
  const onsket = SPRAK.includes(sprak) ? sprak : "nb";
  const min = ++_generasjon;
  const r = await fetch(`/ui/locale/${onsket}`, {
    credentials: "same-origin",
    headers: { accept: "application/json" },
  });
  // Forbigått: også en FEIL herfra er uinteressant nå. Kastet den, ville et
  // tregt 500-svar på et forlatt språk revet ned flaten som nettopp rendret
  // riktig på det språket brukeren endte på.
  if (min !== _generasjon) return _sprak;
  if (!r.ok) throw new Error(`locale ${onsket}: ${r.status}`);
  const kart = await r.json();
  if (min !== _generasjon) return _sprak;
  _kart = kart;
  _sprak = onsket;
  document.documentElement.setAttribute("lang", _sprak);
  document.documentElement.setAttribute("data-sprak", _sprak);
  return _sprak;
}

// Ugyldiggjør en henting som fortsatt er underveis (Codex P2 til PR #42).
// Telleverket over kjente bare ÉN grunn til å trekke seg: at et nyere
// språkvalg hadde startet. Men et språkvalg kan også bli irrelevant fordi
// flaten som bad om det er borte — en utlogging midt i hentingen. Da var det
// ingen nyere henting til å telle opp, og den gamle committet kartet og
// `<html lang>` over en innloggingsflate som allerede sto rendret på det
// forrige språket: siden så norsk ut og var merket engelsk. Den som river ned
// en flate teller derfor opp her, på samme måte som den teller opp
// flategenerasjonen sin.
export function ugyldiggjorSprakhenting() {
  _generasjon += 1;
}

// For tester (jsdom): sett kartet direkte uten nettverk. Ugyldiggjør samtidig
// hentinger som er underveis, så de ikke kan overskrive kartet testen nettopp
// satte.
export function settI18nForTest(kart, sprak = "nb") {
  ugyldiggjorSprakhenting();
  _kart = kart;
  _sprak = sprak;
}

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
