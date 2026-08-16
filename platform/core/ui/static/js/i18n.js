// i18n — locales/ er ENESTE tekstkilde (klarsignal: ingen hardkodet tekst).
// Nøklene er flate, punktseparerte strenger; motorens maskinkoder ER nøkler.
// Ukjent nøkkel → trygt fall-back (oppgitt reserve, ellers nøkkelen selv),
// aldri tom streng og aldri et kast (konsolidert grunnlag §3).

const SPRAK = ["nb", "en"];
let _kart = {};
let _sprak = "nb";

// SPRÅKET REISER I URL-EN, IKKE BARE I LAGERET (Codex P2). `lagreSprak` er
// best effort — privat modus, blokkerte tredjepartslagre, en herdet nettleser
// — og de offentlige lenkene er ordinære `href`-er som laster dokumentet på
// nytt. Uten URL-leddet leste den neste siden ingenting av valget og falt
// tilbake til `data-sprak="nb"` i `index.html`: en besøkende som byttet til
// engelsk fikk norsk igjen ved første klikk i navigasjonen. URL-en leses FØR
// lageret fordi den er det eksplisitte valget for NETTOPP denne navigasjonen
// — og fordi en delt lenke da åpner på språket den ble delt på, uansett hva
// mottakerens forrige besøk la igjen.
export function velgSprak() {
  // Rekkefølge: URL → lagret valg → <html data-sprak> → nettleser → nb.
  let s = null;
  try {
    s = new URLSearchParams(window.location.search).get("sprak");
  } catch { s = null; }
  if (SPRAK.includes(s)) return s;
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

// Å HENTE ET SPRÅK OG Å TA DET I BRUK ER TO STEG (Codex P2). Før var det ett:
// hentingen commitet `_kart` og `<html lang>` i samme øyeblikk svaret kom, mens
// flaten som skulle BÆRE språket først ble byttet etter kallerens neste
// ventepunkt — `/ui/oppsett.json` på forsiden, økt og utrulling i skallet.
// I det gapet sto siden med tekst på ett språk og `lang` på et annet: henger
// oppsettskallet, står den norske forsiden merket engelsk på ubestemt tid, og i
// skallet kunne en navigasjon rendre engelske fragmenter inn i det norske
// treet. Gapet forsvinner når `taIBruk()` kalles rett før treet bygges: språket
// og flaten skifter i samme omgang.
//
// Det samme skillet er svaret på en forlatt lasting (Codex P2): en omstart som
// er forbigått — utlogging mens den ventet — trekker seg FØR `taIBruk()`, og da
// commiteres ingenting. Før eide hentingen commiten alene, så en lasting ingen
// lenger hadde bruk for kunne sette `<html lang>` og `_kart` etter at
// innloggingsflaten var tegnet på det gamle språket.
//
// KUN DEN NYESTE LASTINGEN FÅR SKRIVE. Språkvelgeren står åpen mens settet
// hentes, så på en treg linje kan en bruker rekke å velge to ganger — og de to
// hentingene kan lande i motsatt rekkefølge av valgene. Nummeret gjør
// rekkefølgen eksplisitt: `taIBruk()` fra en henting som ikke lenger er den
// siste som ble startet, forkaster i stedet for å commite, og svarer `null`.
// Kallere som rendrer etterpå MÅ sjekke — `null` betyr «et nyere valg eier
// flaten nå, ikke tegn over det».
//
// Merk at `_sprak` settes i `taIBruk`, ikke før hentingen: satt på forhånd
// ville `sprak()` og `t()` rapportert et språk hvis tekster ennå ikke var
// lastet — og etter en forkastet henting rapportert et språk som aldri kom.
let _lasteNr = 0;

export async function hentI18n(sprak) {
  const nr = ++_lasteNr;
  const s = SPRAK.includes(sprak) ? sprak : "nb";
  const r = await fetch(`/ui/locale/${s}`, {
    credentials: "same-origin",
    headers: { accept: "application/json" },
  });
  if (!r.ok) throw new Error(`locale ${s}: ${r.status}`);
  const kart = await r.json();
  // `sprak` gis ut fordi kalleren trenger språket FØR det er tatt i bruk:
  // utrullingen hentes på språket som er på vei inn, ikke på det som står.
  return {
    sprak: s,
    taIBruk() {
      if (nr !== _lasteNr) return null;
      _sprak = s;
      _kart = kart;
      document.documentElement.setAttribute("lang", _sprak);
      document.documentElement.setAttribute("data-sprak", _sprak);
      return _sprak;
    },
  };
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
