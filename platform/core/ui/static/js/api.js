// Datalag mot lese-API-et (klarsignal V1/V2/V6).
//
// - Same-origin med credentials; ingen bearer i browseren (cookie-økt).
// - 401 og 403 er ULIKE tilstander (V2): 401 → innlogging, 403 → ingen-
//   tilgang. De slås ALDRI sammen.
// - Ingen respons havner i DOM som HTML; kallerne bygger noder fra feltene.
// - CSRF-token leses fra __Host-disponit_csrf (JS-lesbar med hensikt) og
//   sendes i X-Disponit-CSRF på mutasjoner (logout og senere skriveveier).

export class ApiFeil extends Error {
  constructor(status, kode) { super(`api ${status}`); this.status = status; this.kode = kode; }
}
export class UautorisertFeil extends ApiFeil {}      // 401 → innlogging
export class IngenTilgangFeil extends ApiFeil {}     // 403 → ingen tilgang
export class IkkeFunnetFeil extends ApiFeil {}       // 404
export class FeilformetFeil extends ApiFeil {}       // 400

function _kast(status, kode) {
  if (status === 401) throw new UautorisertFeil(status, kode);
  if (status === 403) throw new IngenTilgangFeil(status, kode);
  if (status === 404) throw new IkkeFunnetFeil(status, kode);
  if (status === 400) throw new FeilformetFeil(status, kode);
  throw new ApiFeil(status, kode);
}

export function lesCookie(navn) {
  const rader = (document.cookie || "").split(";");
  for (const rad of rader) {
    const i = rad.indexOf("=");
    if (i < 0) continue;
    if (rad.slice(0, i).trim() === navn) return decodeURIComponent(rad.slice(i + 1));
  }
  return null;
}

export async function hentJson(sti, sok = null) {
  let url = sti;
  if (sok) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(sok)) if (v != null) q.set(k, v);
    const s = q.toString();
    if (s) url += `?${s}`;
  }
  let r;
  try {
    r = await fetch(url, {
      credentials: "same-origin",
      headers: { accept: "application/json" },
      redirect: "error",              // en 3xx her er en feil, ikke en flyt
    });
  } catch (e) {
    throw new ApiFeil(0, "nettverk");
  }
  let kropp = null;
  try { kropp = await r.json(); } catch { kropp = null; }
  if (!r.ok) _kast(r.status, kropp && kropp.feil);
  return kropp;
}

export async function loggUt() {
  const csrf = lesCookie("__Host-disponit_csrf");
  const r = await fetch("/v1/sesjon", {
    method: "DELETE",
    credentials: "same-origin",
    headers: csrf ? { "X-Disponit-CSRF": csrf } : {},
    redirect: "error",
  });
  // 204 forventet; 401 betyr allerede utlogget — begge er «du er ute».
  return r.status === 204 || r.status === 401;
}

export function nyIdempotensnokkel() {
  if (globalThis.crypto && crypto.randomUUID) return crypto.randomUUID();
  return `idem-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

// PR-012: menneskelig unntaksbehandling. Muterende → X-Disponit-CSRF
// (dobbel-innsending). Klienten sender handlingen, `saksversjon` (den den
// VISTE, for den optimistiske låsen) og en `Idempotency-Key`. Nøkkelen
// GENERERES AV KALLEREN og gjenbrukes ved retry — ellers ville en
// nettverksretry blitt en ny operasjon (server-idempotensen ville aldri sett
// samme nøkkel). Konvolutten bygges og MAC-signeres SERVER-side.
export async function postHandling(uid, operatorhandling, saksversjon,
                                   idempotensnokkel) {
  const csrf = lesCookie("__Host-disponit_csrf");
  let r;
  try {
    r = await fetch(`/v1/unntak/${uid}/handling`, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
        "Idempotency-Key": idempotensnokkel,
        ...(csrf ? { "X-Disponit-CSRF": csrf } : {}),
      },
      body: JSON.stringify({ operatorhandling, saksversjon }),
      redirect: "error",
    });
  } catch (e) {
    throw new ApiFeil(0, "nettverk");
  }
  let kropp = null;
  try { kropp = await r.json(); } catch { kropp = null; }
  if (!r.ok) _kast(r.status, kropp && kropp.feil);
  return kropp;
}
