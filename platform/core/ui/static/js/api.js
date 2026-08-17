// Datalag mot lese-API-et (klarsignal V1/V2/V6).
//
// - Same-origin med credentials; ingen bearer i browseren (cookie-økt).
// - 401 og 403 er ULIKE tilstander (V2): 401 → innlogging, 403 → ingen-
//   tilgang. De slås ALDRI sammen.
// - Ingen respons havner i DOM som HTML; kallerne bygger noder fra feltene.
// - CSRF-token leses fra __Host-disponit_csrf (JS-lesbar med hensikt) og
//   sendes i X-Disponit-CSRF på mutasjoner (logout og senere skriveveier).

export class ApiFeil extends Error {
  // `detaljer` er serverens egen begrunnelse (f.eks. valideringsfeillista fra
  // 422 `policy_ugyldig`). Uten den kan en fanger bare si «noe gikk galt» —
  // og da er den eneste som får vite HVA, den som leser serverloggen.
  constructor(status, kode, detaljer = null) {
    super(`api ${status}`);
    this.status = status;
    this.kode = kode;
    this.detaljer = Array.isArray(detaljer) ? detaljer : null;
  }
}
export class UautorisertFeil extends ApiFeil {}      // 401 → innlogging
export class IngenTilgangFeil extends ApiFeil {}     // 403 → ingen tilgang
export class IkkeFunnetFeil extends ApiFeil {}       // 404
export class FeilformetFeil extends ApiFeil {}       // 400
export class UgyldigFeil extends ApiFeil {}          // 422 → validering feilet

function _kast(status, kode, detaljer) {
  if (status === 401) throw new UautorisertFeil(status, kode, detaljer);
  if (status === 403) throw new IngenTilgangFeil(status, kode, detaljer);
  if (status === 404) throw new IkkeFunnetFeil(status, kode, detaljer);
  if (status === 400) throw new FeilformetFeil(status, kode, detaljer);
  if (status === 422) throw new UgyldigFeil(status, kode, detaljer);
  throw new ApiFeil(status, kode, detaljer);
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

// Utrullingsplanen for ØKTEN. Den kan ikke ligge i klientpakken: `/ui/{sti}`
// serveres uten sesjonssjekk. Serveren returnerer bare radene økten har rett
// til — egen tenant, eller alle med `platform:admin` — så klienten filtrerer
// ingenting og har aldri en rad den ikke skulle sett.
//
// `?sprak=` følger med fordi «neste steg» er FRITEKST per kunde: den kan ikke
// være en locale-nøkkel uten å legge tenantdata tilbake i en anonymt
// nedlastbar fil, så oversettelsen kommer med raden. `plan` kommer derimot som
// kode og oversettes i flaten — planetiketten er chrome, tildelingen er data.
export const hentUtrulling = (sprak) =>
  hentJson(`/v1/utrulling?sprak=${encodeURIComponent(sprak || "nb")}`);

// Samme kall, men med skallets feilpolitikk: utrullingen er TILLEGGSDATA.
// Nettverksfeil, 403 eller 5xx betyr bare at tenantfeltene står tomme, og
// flatene har sin egen tomtilstand for nettopp det — appen skal ikke falle.
//
// 401 er noe kvalitativt annet og må IKKE slukes: økten kan ha utløpt eller
// blitt tilbakekalt etter at `/v1/sesjon` svarte. Ble den gjort om til et tomt,
// vellykket svar, rendret skallet seg autentisert på øktdata som ikke lenger
// gjelder — blant annet den API-frie kundeflaten, som ikke selv oppdager at
// økten er borte. Da lyver flaten om at brukeren er innlogget. Her får 401 i
// stedet nå den ytre håndteringen i `start()`, altså innloggingsflaten, slik
// alle andre 401-er i klienten gjør (V2: 401 → innlogging, 403 → ingen tilgang).
export const hentUtrullingForSkall = (sprak) =>
  hentUtrulling(sprak).catch((e) => {
    if (e instanceof UautorisertFeil) throw e;
    return {};
  });

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

// PR-013: policyadministrasjon. Muterende ruter → X-Disponit-CSRF (samme
// dobbel-innsending som unntaksbehandlingen). `Idempotency-Key` sendes KUN der
// serveren krever den (attester — den kan aktivere en policy), og genereres av
// kalleren så en nettverksretry gjenbruker samme nøkkel.
async function _muter(sti, metode, kropp, idempotensnokkel) {
  const csrf = lesCookie("__Host-disponit_csrf");
  const headers = {
    "content-type": "application/json", accept: "application/json",
    ...(csrf ? { "X-Disponit-CSRF": csrf } : {}),
    ...(idempotensnokkel ? { "Idempotency-Key": idempotensnokkel } : {}),
  };
  let r;
  try {
    r = await fetch(sti, {
      method: metode, credentials: "same-origin", headers,
      body: kropp != null ? JSON.stringify(kropp) : undefined,
      redirect: "error",
    });
  } catch (e) {
    throw new ApiFeil(0, "nettverk");
  }
  let b = null;
  try { b = await r.json(); } catch { b = null; }
  if (!r.ok) _kast(r.status, b && b.feil, b && b.detaljer);
  return b;
}

// Idempotency-Key er PÅKREVD på ALLE skriveruter (server P1 R3). Nøkkelen MÅ
// være STABIL over en retry av SAMME operasjon (Codex PR-014 R1): et tapt svar
// + nytt klikk skal gjenbruke nøkkelen, så serveren REPLAYer i stedet for å
// duplisere. Derfor tar hver funksjon en valgfri `idem` — kalleren (editoren)
// holder en nøkkel som er stabil så lenge innholdet er uendret. Uten arg
// genereres en fersk (for engangs-klikk der duplikat ikke er en risiko).
export const hentMaler = () => hentJson("/v1/policymaler");

// Hvilken policy GJELDER i dag? Editoren kan ikke bare anta at malens id er
// dagens policy-id: aktivering er per `policy_id`, så en ny id lager en NY
// policyserie ved siden av den som gjelder i stedet for å avløse den — og
// kunder som opprettet policyen med egen id (`acme-netthandel`) har ikke
// malens id i det hele tatt.
//
// Svaret er derfor TREDELT, ikke boolsk. `ukjent` er en egen tilstand med
// vilje: 404 betyr «ingen aktiv i dag», mens 403 (ingen `policy:read`) eller
// 500 (registeret har flere aktive og nekter å velge én) betyr at vi IKKE VET
// — og da skal flaten heller ikke påstå noe.
export async function hentAktivPolicyId() {
  try {
    const d = await hentJson("/v1/policy/aktiv");
    const id = d && typeof d.policy_id === "string" ? d.policy_id : null;
    return id ? { kjent: true, id } : { kjent: false, id: null };
  } catch (e) {
    if (e instanceof UautorisertFeil) throw e;   // 401 er innlogging, ikke data
    if (e instanceof IkkeFunnetFeil) return { kjent: true, id: null };
    return { kjent: false, id: null };
  }
}
export const opprettUtkast = (policyId, innhold, idem = nyIdempotensnokkel()) =>
  _muter("/v1/policyutkast", "POST", { policy_id: policyId, innhold }, idem);
export const redigerUtkast = (uid, utkastversjon, innhold,
                              idem = nyIdempotensnokkel()) =>
  _muter(`/v1/policyutkast/${uid}`, "PUT", { utkastversjon, innhold }, idem);
// valider krever `utkastversjon` i kroppen (server R3: nøkkelen bindes til
// versjonen).
export const validerUtkast = (uid, utkastversjon, idem = nyIdempotensnokkel()) =>
  _muter(`/v1/policyutkast/${uid}/valider`, "POST", { utkastversjon }, idem);
// slett krever identiteten til den aktive policyen flaten VISTE (`versjon` +
// `innholds_hash`) — den optimistiske låsen, som `utkastversjon` er for
// utkastene. Serveren sammenligner under policylåsen: er en ny versjon
// aktivert siden siden ble lastet, avvises slettingen med `policy_endret` i
// stedet for å rive med seg noe operatøren aldri så.
export const slettPolicy = (policyId, versjon, innholdsHash,
                            idem = nyIdempotensnokkel()) =>
  _muter(`/v1/policy/${encodeURIComponent(policyId)}/slett`, "POST",
         { versjon, innholds_hash: innholdsHash }, idem);
export const merkVarselLest = (id, idem = nyIdempotensnokkel()) =>
  _muter(`/v1/varsel/${id}/lest`, "POST", {}, idem);
export const settVarselkanal = (kanal, sprak, idem = nyIdempotensnokkel()) =>
  _muter("/v1/varselvalg", "POST", { kanal, sprak }, idem);
export const forkastUtkast = (uid, utkastversjon, idem = nyIdempotensnokkel()) =>
  _muter(`/v1/policyutkast/${uid}/forkast`, "POST", { utkastversjon }, idem);
export const gjenapneUtkast = (uid, utkastversjon,
                               idem = nyIdempotensnokkel()) =>
  _muter(`/v1/policyutkast/${uid}/gjenapne`, "POST", { utkastversjon }, idem);
export const apneRunde = (uid, idem = nyIdempotensnokkel()) =>
  _muter(`/v1/policyutkast/${uid}/aktiveringsrunde`, "POST", {}, idem);
export const attesterAktivering = (uid, diffHash, idempotensnokkel) =>
  _muter(`/v1/policyutkast/${uid}/attester`, "POST", { diff_hash: diffHash },
         idempotensnokkel);

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
