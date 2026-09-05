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

// Kandidatdokumentet hentes av FORELDEREN, aldri av visningsrammen selv
// (eiers funn 31/8, runde 2): en `<iframe sandbox="">` har OPAK origin,
// og nettleseren sender ikke øktcookien med rammens dokumentforespørsel
// (SameSite/ITP behandler den som tredjepart) — direkte `src` mot ruten
// ga derfor 401 og en blank side. Her går kallet same-origin med
// credentials som alle andre, og kalleren viser innholdet fra en blob i
// sandkassen — autentiseringen skjer i appens kontekst, rendringen i en
// kontekst uten fullmakter.
export async function hentKandidatdokument(oppdragId, dokumentId) {
  let r;
  try {
    r = await fetch("/v1/rekruttering/kandidatdokument/"
      + `${oppdragId}/${encodeURIComponent(dokumentId)}`, {
      credentials: "same-origin",
      redirect: "error",
    });
  } catch (e) {
    throw new ApiFeil(0, "nettverk");
  }
  if (!r.ok) {
    let kropp = null;
    try { kropp = await r.json(); } catch { kropp = null; }
    _kast(r.status, kropp && kropp.feil);
  }
  // Typen er SERVERENS normaliserte dom (samme verdi som styrer inline/
  // attachment der) — visningsvalget i flaten bygger på den, aldri på
  // filnavnet.
  const innholdstype = ((r.headers && r.headers.get
    && r.headers.get("content-type")) || "")
    .split(";")[0].trim().toLowerCase();
  return { blob: await r.blob(), innholdstype };
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

// M-57 (§8): signeringen binder INNHOLDSHASHEN, aldri bare listen —
// signataren skal signere nøyaktig de bytene dialogen viste kortformen av
// (056s `signer_utsendingsliste`-form).
//
// Blindingsklienten er tatt ut igjen (Codex P2, runde 4): avskruing er en
// auditert handling, og `blinding_endepunkt` kan ikke skrive revisjonsraden
// før #159 har evidensdesignet — den svarer en kodet 409 begge veier. En
// klientfunksjon for en mutasjon ingen kan utføre er død kode; #159 er
// PR-en som bringer den tilbake sammen med skrivingen.
// Stillingsprofilen (#189): lagring er ALLTID en ny, komplett versjon —
// `profilId` null oppretter en ny profil.
export const lagreStillingsprofil = (profilId, navn, krav, idem) =>
  _muter("/v1/rekruttering/stillingsprofiler", "POST",
         { profil_id: profilId, navn, krav },
         idem || nyIdempotensnokkel());

// Evalueringskjeden (#162): reserver bunteplass → last opp ZIP-en rå →
// bestill. Reservasjonen og bestillingen bærer hver sin SP-2-nøkkel
// (stabil per forsøk, holdt av flaten); opplastingen er engangs per
// reservasjon og identifiseres av reservasjonens jti alene.
export const reserverBunt = (idem) =>
  _muter("/v1/inndata/reserver", "POST",
         { eiermodul: "m57_ats", formaal: "soknadsbunt" }, idem);

export async function lastOppBunt(jti, bytes) {
  const csrf = lesCookie("__Host-disponit_csrf");
  let r;
  try {
    r = await fetch(`/v1/inndata/opplast/${encodeURIComponent(jti)}`, {
      method: "PUT", credentials: "same-origin",
      headers: { "content-type": "application/zip",
                 ...(csrf ? { "X-Disponit-CSRF": csrf } : {}) },
      body: bytes, redirect: "error",
    });
  } catch (e) {
    throw new ApiFeil(0, "nettverk");
  }
  let b = null;
  try { b = await r.json(); } catch { b = null; }
  if (!r.ok) _kast(r.status, b && b.feil, b && b.detaljer);
  return b;
}

export const bestillEvaluering = (kropp, idem) =>
  _muter("/v1/bestilling", "POST", kropp, idem);

// M-57s egen rapportflate ("ats"): listen og den promoterte rapporten.
export const hentEvalueringer = (cursor) =>
  hentJson("/v1/rekruttering/evalueringer"
    + (cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""));
export const slettEvaluering = (oppdragId) =>
  _muter(`/v1/rekruttering/evaluering/${encodeURIComponent(oppdragId)}/slett`,
    "POST", {});
export const hentUtsendingstekster = () =>
  hentJson("/v1/rekruttering/utsendingstekster");
export const lagreUtsendingstekst = (tekstId, navn, tekst, idem) =>
  _muter("/v1/rekruttering/utsendingstekster", "POST",
    { ...(tekstId ? { tekst_id: tekstId } : {}), navn, tekst }, idem);
export const slettUtsendingstekst = (tekstId) =>
  _muter(`/v1/rekruttering/utsendingstekst/${encodeURIComponent(tekstId)}`
    + "/slett", "POST", {});
export const slettStillingsprofil = (profilId) =>
  _muter(`/v1/rekruttering/stillingsprofil/${encodeURIComponent(profilId)}`
    + "/slett", "POST", {});
export const avbrytEvaluering = (oppdragId) =>
  _muter(`/v1/rekruttering/evaluering/${encodeURIComponent(oppdragId)}/avbryt`,
    "POST", {});
export const hentEvalueringsrapport = (oppdragId) =>
  hentJson(`/v1/rekruttering/rapport/${encodeURIComponent(oppdragId)}`);

// Simulering (PR-013 v2-punktet): rådgivende lesing — ingen idem-nøkkel.
export const simulerPolicyutkast = (utkastId, hendelse, rolle) =>
  _muter(`/v1/policyutkast/${encodeURIComponent(utkastId)}/simuler`,
         "POST", { hendelse, rolle });

export const opprettUtsendingsliste = (oppdragId, listetype, kandidater,
                                       idem, firmatekst) =>
  _muter("/v1/rekruttering/lister", "POST",
    { oppdrag_id: oppdragId, listetype, kandidater,
      ...(firmatekst ? { firmatekst } : {}) },
    idem || nyIdempotensnokkel());

// M-8 (082): kundens tidsvalg-administrasjon. Kandidatsiden bruker
// ALDRI disse — den er en frittstående flate uten cookies/CSRF og gjør
// sine egne fetch-kall (flater/tidsvalg.js).
export const hentTidsvalg = (prosessId) =>
  hentJson("/v1/rekruttering/tidsvalg", { prosess_id: prosessId });
export const opprettTidsvalgSlots = (prosessId, slots, idem) =>
  _muter("/v1/rekruttering/tidsvalg/slots", "POST",
    { prosess_id: prosessId, slots }, idem || nyIdempotensnokkel());
export const deaktiverTidsvalgSlot = (slotId) =>
  _muter(`/v1/rekruttering/tidsvalg/slot/${encodeURIComponent(slotId)}`
    + "/deaktiver", "POST", {});

export const signerRekrutteringsliste = (listeId, innholdHash, idem) =>
  _muter(`/v1/rekruttering/lister/${encodeURIComponent(listeId)}/signer`,
         "POST", { innhold_hash: innholdHash }, idem || nyIdempotensnokkel());

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
// 047: `rollbackAvVersjon` gjør utkastet til en RULLBAKK — serveren
// henter da selve innholdet fra versjonen (kopien er serverens sannhet,
// port 22), så `innhold` utelates. Uten feltet er kontrakten som før.
//
// `rollbackAvGenerasjon` er den OPTIMISTISKE LÅSEN på kilden (Codex P2),
// søsteren til `slettPolicy`s `versjon`/`innholds_hash`. Et versjonsnummer
// frigjøres av `slett_ubrukt_policy` og kan gjenskapes med annet innhold,
// så nummeret alene sier ikke HVILKEN rad eier så. Slettes og gjenskapes
// den mellom visningen og bekreftelsen, kopierte serveren erstatningen og
// lagret et opphav som var internt konsistent og likevel ikke det eier ba
// om. Generasjonen er identiteten som ikke gjenbrukes; serveren avviser
// med `rullbakk_kilde_endret` (409) når den ikke stemmer.
export const opprettUtkast = (policyId, innhold,
                              idem = nyIdempotensnokkel(),
                              rollbackAvVersjon = null,
                              rollbackAvGenerasjon = null) =>
  _muter("/v1/policyutkast", "POST",
         { policy_id: policyId,
           ...(innhold === undefined ? {} : { innhold }),
           ...(rollbackAvVersjon == null ? {}
               : { rollback_av_versjon: rollbackAvVersjon,
                   rollback_av_generasjon: rollbackAvGenerasjon }) }, idem);
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

// 038 §6: bestillingsveien. Nøkkelen holdes av FLATEN og er stabil så lenge
// skjemainnholdet står urørt — en retry replayer, en endring bestiller nytt.
export const bestill = (kropp, idempotensnokkel) =>
  _muter("/v1/bestilling", "POST", kropp, idempotensnokkel);
// 038 §7: den promoterte rapporten bak et beslutningsoppdrag (lesende).
export const hentRapport = (oppdragId) => hentJson(`/v1/rapport/${oppdragId}`);
// 039: selvbetjent domeneverifisering. Utstedelsen er muterende (CSRF);
// nøkkelen er engangs — TXT-verdien i svaret finnes aldri igjen.
export const hentDomener = () => hentJson("/v1/domener");
// M-6 PR-B: kildeforvaltningen. /start KREVER Idempotency-Key (flaten
// holder nøkkelen stabil til skjemaet endres — replay gir samme
// authorize-URL); deaktivering er naturlig idempotent (enveis) og
// bærer ingen nøkkel, som slett-rutene.
export const hentEpostKilder = () => hentJson("/v1/epost/kilder");
export const startEpostKilde = (postboks, idempotensnokkel) =>
  _muter("/v1/epost/kilder/start", "POST", { postboks }, idempotensnokkel);
export const deaktiverEpostKilde = (kildeId) =>
  _muter(`/v1/epost/kilder/${kildeId}/deaktiver`, "POST", {});
export const leggTilDomene = (hostname) =>
  _muter("/v1/domener", "POST", { hostname });

// 044: planflaten — CSRF-vernede mutasjoner over de herdede funksjonene.
// Opprettelsen KREVER `Idempotency-Key`: et tapt svar + nytt klikk skal
// gjenspille planen, ikke lage plan nummer to med samme parametre og egen
// kvotebruk. Kalleren holder nøkkelen (stabil så lenge kroppen er uendret).
// Overgangene trenger den ikke — de er naturlig idempotente på plan-id-en.
export const opprettPlan = (kropp, idem) =>
  _muter("/v1/plan", "POST", kropp, idem);
export const planHandling = (planId, hva) =>
  _muter(`/v1/plan/${planId}/${hva}`, "POST", {});

// 041 §5: adjudikasjonen — den ENESTE muterende veien i domenesakskøen.
// CSRF (dobbel-innsending) som resten av browsermutasjonene. INGEN
// Idempotency-Key: en gjentatt stemme fra samme aktør avvises av
// primærnøkkelen i basen (`dobbel_attestasjon`), og det svaret skal VISES,
// ikke skjules bak en replay.
//
// 409 KASTES IKKE, den RETURNERES. Endepunktet bruker den til å si noe
// legibelt — «1 av 2 avgitt», «du har alt stemt», «saken er avgjort eller
// foreldet» — og en flate som gjorde det om til «noe gikk galt» ville
// gjenskapt nøyaktig den stillheten PR-015 §4 finnes for å fjerne.
//
// `saksrevisjon` ER EN DEL AV STEMMEN (Codex P1). Sak-id-en er stabil
// gjennom A→B→C→B, så en fane som har stått åpen peker på samme sak, men
// på en helt annen tvist — annen motpart, annen generasjon. Sendes ikke
// revisjonen flaten VISTE, avgir knappen stemme i den konflikten som
// tilfeldigvis står der nå, og to gamle faner kunne fullført en positiv
// tildeling ingen av dem hadde sett. Basen håndhever den under
// hostname-låsen; klienten er den eneste som kan si hva som ble lest.
export async function avgiDomeneattestasjon(unntakId, utfall, vinnendeTenant,
                                            saksrevisjon) {
  const csrf = lesCookie("__Host-disponit_csrf");
  let r;
  try {
    r = await fetch(`/v1/unntak/${unntakId}/domeneattestasjon`, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
        ...(csrf ? { "X-Disponit-CSRF": csrf } : {}),
      },
      body: JSON.stringify({ utfall, vinnende_tenant: vinnendeTenant,
                             saksrevisjon }),
      redirect: "error",
    });
  } catch (e) {
    throw new ApiFeil(0, "nettverk");
  }
  let kropp = null;
  try { kropp = await r.json(); } catch { kropp = null; }
  if (r.status === 409) return kropp || { feil: "krever_to_attestasjoner" };
  if (!r.ok) _kast(r.status, kropp && kropp.feil);
  return kropp;
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
  if (!r.ok) {
    // 043: `oppdrag_utfort` bærer referansen mennesket skal beslutte på
    // nytt med — den rir i `detaljer`, den lukkede feilens eneste bagasje.
    _kast(r.status, kropp && kropp.feil,
          kropp && kropp.feil === "oppdrag_utfort"
            ? [String(kropp.oppdrag_id ?? ""),
               String(kropp.kvitteringsref ?? "")] : undefined);
  }
  return kropp;
}

// 089 (M-35): kontinuitetsflatens tre skriveveier. Hver av dem er ETT
// kall mot en claimer-eid dør i basen — klienten håndhever ingenting
// selv, og skal ikke: etteranalyse-kravet på lukkingen bor i døren, og
// en klient som «hjalp til» ved å skjule knappen ville vært en andre
// sannhet det gikk an å omgå med curl. Knappen skjules likevel bak
// write-scopet, men det er ERGONOMI, ikke sikkerhet.
//
// `idem` er kallerens: en tapt respons + nytt klikk skal GJENSPILLE
// (SP-2 — serveren utleder id-en av nøkkelen), aldri føde en ny
// hendelse eller en dobbel tidslinjepost.
export const apneKontinuitetshendelse = (tekstnokkel, alvor, parametre, idem) =>
  _muter("/v1/kontinuitet/hendelser", "POST",
         { tekstnokkel, alvor, parametre: parametre || {} },
         idem || nyIdempotensnokkel());

export const leggKontinuitetspost = (hendelseId, posttype, tekst, idem) =>
  _muter(`/v1/kontinuitet/hendelse/${encodeURIComponent(hendelseId)}/post`,
         "POST", { posttype, tekst }, idem || nyIdempotensnokkel());

export const lukkKontinuitetshendelse = (hendelseId, tekst, idem) =>
  _muter(`/v1/kontinuitet/hendelse/${encodeURIComponent(hendelseId)}/lukk`,
         "POST", { tekst }, idem || nyIdempotensnokkel());

// 094 (M-5): malregisterets fem skriveveier og utfyllingen.
//
// UTFYLLINGEN GÅR OGSÅ GJENNOM `_muter`, og det er et bevisst valg som
// IKKE gjør den til en mutasjon: verdiene er kundens data og hører i en
// kropp, ikke i en query-streng der de havner i tilgangslogger og
// browserhistorikk. Serveren krever `decisions:read` på ruten — altså
// LESEmyndighet — og `m5_fyll_mal` er STABLE i basen, så kallet kan ikke
// skrive noe uansett hva klienten sender. Den får ingen
// `Idempotency-Key`: en idempotensnøkkel lover at et gjenspill ikke
// skaper noe nytt, og her finnes ingenting å skape.
export const opprettMalfamilie = (navn, beskrivelse, idem) =>
  _muter("/v1/dokumentmal/familier", "POST", { navn, beskrivelse },
         idem || nyIdempotensnokkel());

export const opprettMalversjon = (familieId, komponenter, felt, idem) =>
  _muter("/v1/dokumentmal/versjoner", "POST",
         { familie_id: familieId, komponenter, felt },
         idem || nyIdempotensnokkel());

export const publiserMalversjon = (versjonId, idem) =>
  _muter(`/v1/dokumentmal/versjon/${encodeURIComponent(versjonId)}/publiser`,
         "POST", {}, idem || nyIdempotensnokkel());

export const trekkTilbakeMalversjon = (versjonId, idem) =>
  _muter(
    `/v1/dokumentmal/versjon/${encodeURIComponent(versjonId)}/trekk-tilbake`,
    "POST", {}, idem || nyIdempotensnokkel());

export const fyllMal = (versjonId, verdier) =>
  _muter(`/v1/dokumentmal/versjon/${encodeURIComponent(versjonId)}/utfylling`,
         "POST", { verdier });
// M-21 (096): pliktregisteret. Registreringen bærer en SP-2-nøkkel —
// serveren UTLEDER plikt-id-en av den, så en tapt respons + nytt klikk
// gjenspiller i stedet for å føde plikten en gang til. Kvitteringen og
// bortfallet er idempotente av tilstanden sin (døren avviser en plikt
// som ikke er åpen), men bærer nøkkelen likevel: serveren krever den på
// alle tre, og formen skal være den samme.
export const registrerPlikt = (plikt, idem) =>
  _muter("/v1/plikt", "POST", plikt, idem || nyIdempotensnokkel());

export const lukkPlikt = (pliktId, kvitteringRef, idem) =>
  _muter(`/v1/plikt/${encodeURIComponent(pliktId)}/lukk`, "POST",
         { kvittering_ref: kvitteringRef }, idem || nyIdempotensnokkel());

export const bortfallPlikt = (pliktId, begrunnelse, idem) =>
  _muter(`/v1/plikt/${encodeURIComponent(pliktId)}/bortfall`, "POST",
         { begrunnelse }, idem || nyIdempotensnokkel());
// M-12 (097): tilgangsregisteret. Objekt- og tilgangsregistreringen
// bærer en SP-2-nøkkel — serveren UTLEDER id-en av den, så en tapt
// respons + nytt klikk gjenspiller i stedet for å føde raden en gang
// til. Gjennomgangen er idempotent av sin egen dato (døren returnerer
// samme frist for en gjennomgang som alt er registrert i dag), men
// bærer nøkkelen likevel: serveren krever den på alle tre, og formen
// skal være den samme.
//
// MERK HVA SOM IKKE STÅR HER: ingen `fjernTilgang`, ingen `flyttTilgang`,
// ingen `opprettTilgangISystem`. v1 registrerer; den provisjonerer
// ingenting. En klientfunksjon for noe serveren ikke har en dør til
// ville vært det første steget bort fra den dommen.
export const registrerTilgangsobjekt = (objekt, idem) =>
  _muter("/v1/tilgang/objekt", "POST", objekt, idem || nyIdempotensnokkel());

export const registrerTilgang = (tilgang, idem) =>
  _muter("/v1/tilgang", "POST", tilgang, idem || nyIdempotensnokkel());

export const registrerGjennomgang = (tilgangId, idem) =>
  _muter(`/v1/tilgang/${encodeURIComponent(tilgangId)}/gjennomgang`,
         "POST", {}, idem || nyIdempotensnokkel());

// M-22 (098): lisensregisteret. Samme form som M-21s over — registreringen
// bærer en SP-2-nøkkel, serveren UTLEDER lisens-id-en av den, og en tapt
// respons + nytt klikk gjenspiller i stedet for å føde lisensen en gang
// til. Fornyelsen har sin egen gjenspillgren i døren (samme dato igjen er
// et stille ja), og avslutningen er idempotent av tilstanden sin — men
// alle tre bærer nøkkelen, fordi serveren krever den på alle tre.
//
// MERK HVA SOM IKKE FINNES HER: ingen `siOppLisens`. Modulen sier ikke
// opp noe — `avsluttLisens` fører at et MENNESKE har gjort det.
export const registrerLisens = (lisens, idem) =>
  _muter("/v1/lisens", "POST", lisens, idem || nyIdempotensnokkel());

export const fornyLisens = (lisensId, fornyelsesdato, idem) =>
  _muter(`/v1/lisens/${encodeURIComponent(lisensId)}/fornyelse`, "POST",
         { fornyelsesdato }, idem || nyIdempotensnokkel());

export const avsluttLisens = (lisensId, begrunnelse, idem) =>
  _muter(`/v1/lisens/${encodeURIComponent(lisensId)}/avslutt`, "POST",
         { begrunnelse }, idem || nyIdempotensnokkel());

// M-30 (099): forespørselsregisteret. Registreringen bærer en SP-2-nøkkel
// — serveren UTLEDER sak-id-en av den, så en tapt respons + nytt klikk
// gjenspiller i stedet for å føde forespørselen en gang til. Svaret,
// avslaget og forlengelsen er idempotente av tilstanden sin (dørene
// avviser en sak som ikke er åpen), men bærer nøkkelen likevel: serveren
// krever den på alle fire, og formen skal være den samme.
//
// INGEN AV DEM SLETTER NOE. Å registrere at en sletteforespørsel er
// besvart er ikke det samme som å slette: sletting eies av M-4s
// retensjonsregnskap, og svaret her er henvisningen til at det ble gjort.
export const registrerPersonvernsak = (sak, idem) =>
  _muter("/v1/personvern", "POST", sak, idem || nyIdempotensnokkel());

export const besvarPersonvernsak = (sakId, svarRef, idem) =>
  _muter(`/v1/personvern/${encodeURIComponent(sakId)}/svar`, "POST",
         { svar_ref: svarRef }, idem || nyIdempotensnokkel());

export const avvisPersonvernsak = (sakId, begrunnelse, idem) =>
  _muter(`/v1/personvern/${encodeURIComponent(sakId)}/avvis`, "POST",
         { begrunnelse }, idem || nyIdempotensnokkel());

export const forlengPersonvernfrist = (sakId, forlengetTil, begrunnelse,
                                       idem) =>
  _muter(`/v1/personvern/${encodeURIComponent(sakId)}/forleng`, "POST",
         { forlenget_til: forlengetTil, begrunnelse },
         idem || nyIdempotensnokkel());
// M-34 (100): kontrollregisteret. Registreringen OG etterprøvingen bærer
// hver sin SP-2-nøkkel — serveren UTLEDER id-en av den, så en tapt
// respons + nytt klikk gjenspiller i stedet for å føde en kontroll (eller,
// verre, en etterprøving) til. En dobbelt bokført etterprøving ville vært
// et revisjonsspor som lyver om hvor mange ganger noe faktisk ble
// kontrollert.
//
// DET FINNES INGEN INNSENDINGSFUNKSJON HER, og fraværet er dommen:
// katalogteksten lover innsending til sertifiseringsorgan, v1 registrerer
// kontrollen. Et compliance-verktøy som sender inn noe på egen hånd
// skaper en påstand ingen har lest.
export const registrerKontroll = (kontroll, idem) =>
  _muter("/v1/compliance/kontroll", "POST", kontroll,
         idem || nyIdempotensnokkel());

export const registrerEtterproving = (kontrollId, etterproving, idem) =>
  _muter(
    `/v1/compliance/kontroll/${encodeURIComponent(kontrollId)}/etterproving`,
    "POST", etterproving, idem || nyIdempotensnokkel());

export const markerIkkeRelevant = (kontrollId, begrunnelse, idem) =>
  _muter(
    `/v1/compliance/kontroll/${encodeURIComponent(kontrollId)}/ikke-relevant`,
    "POST", { begrunnelse }, idem || nyIdempotensnokkel());
// M-13 (101): avstemmingsregisteret. Alle fire registreringsveiene bærer
// hver sin SP-2-nøkkel — serveren UTLEDER id-en av den, så en tapt
// respons + nytt klikk gjenspiller i stedet for å føde en rad til. En
// dobbelt registrert innbetaling er nøyaktig den feilen som får et
// regnskap til å stemme på papiret og ikke i virkeligheten.
//
// DET FINNES INGEN BOKFØRINGSFUNKSJON HER, og fraværet er dommen:
// katalogteksten lover automatisk bokføring ved full match, v1 avstemmer
// og viser. En automatisk bokføring er en skriving i regnskapet, og et
// regnskap som endres av noe ingen leste er ikke et regnskap.
export const registrerKonto = (konto, idem) =>
  _muter("/v1/avstemming/konto", "POST", konto,
         idem || nyIdempotensnokkel());

export const registrerBankpost = (post, idem) =>
  _muter("/v1/avstemming/bankpost", "POST", post,
         idem || nyIdempotensnokkel());

export const registrerBilag = (bilag, idem) =>
  _muter("/v1/avstemming/bilag", "POST", bilag,
         idem || nyIdempotensnokkel());

export const avstem = (match, idem) =>
  _muter("/v1/avstemming/match", "POST", match,
         idem || nyIdempotensnokkel());

export const opphevAvstemming = (avstemmingId, begrunnelse, idem) =>
  _muter(
    `/v1/avstemming/match/${encodeURIComponent(avstemmingId)}/opphev`,
    "POST", { begrunnelse }, idem || nyIdempotensnokkel());
// M-17 (102): henvendelsesregisteret. INNTAKET og UTKASTET bærer hver
// sin SP-2-nøkkel — serveren utleder id-en av den, så en tapt respons +
// nytt klikk gjenspiller i stedet for å føde en henvendelse til. En
// dobbelt registrert henvendelse ville sett ut som at kunden spurte to
// ganger, og da svarer noen to ganger.
//
// DET FINNES INGEN SENDEFUNKSJON HER, og fraværet er dommen:
// katalogteksten lover automatiske svar, v1 lagrer et utkast. Et
// automatisk svar til en kunde er en uttalelse på firmaets vegne.
// `avgjorUtkast` har to lovlige verdier — `forkastet` og
// `brukt_manuelt` — og ingen av dem heter `sendt`.
export const taImotHenvendelse = (henvendelse, idem) =>
  _muter("/v1/kundeservice/henvendelse", "POST", henvendelse,
         idem || nyIdempotensnokkel());

export const klassifiserHenvendelse = (id, klassifisering, idem) =>
  _muter(
    `/v1/kundeservice/henvendelse/${encodeURIComponent(id)}/klassifiser`,
    "POST", klassifisering, idem || nyIdempotensnokkel());

export const henvendelseTilUnntakskoe = (id, begrunnelse, idem) =>
  _muter(
    `/v1/kundeservice/henvendelse/${encodeURIComponent(id)}/unntakskoe`,
    "POST", { begrunnelse }, idem || nyIdempotensnokkel());

export const lagreUtkast = (id, utkast, idem) =>
  _muter(
    `/v1/kundeservice/henvendelse/${encodeURIComponent(id)}/utkast/ny`,
    "POST", utkast, idem || nyIdempotensnokkel());

export const avgjorUtkast = (utkastId, status, idem) =>
  _muter(`/v1/kundeservice/utkast/${encodeURIComponent(utkastId)}/dom`,
         "POST", { status }, idem || nyIdempotensnokkel());

export const lukkHenvendelse = (id, utfall, idem) =>
  _muter(`/v1/kundeservice/henvendelse/${encodeURIComponent(id)}/lukk`,
         "POST", { utfall }, idem || nyIdempotensnokkel());
// M-18 (103): onboardingregisteret. MALEN og LØPET bærer hver sin
// SP-2-nøkkel — serveren utleder id-en av den, så en tapt respons + nytt
// klikk gjenspiller i stedet for å starte et løp til. Et dobbelt startet
// løp ville gitt to sett steg for den samme kunden, og «hvor står vi» to
// svar.
//
// DET FINNES INGEN PROVISJONERINGSFUNKSJON HER, og fraværet er dommen:
// katalogteksten lover 0 minutter per ny kunde, v1 registrerer løpet.
// En automatisk provisjonering forutsetter at man vet hva et fullført
// løp er.
export const registrerOnboardingmal = (mal, idem) =>
  _muter("/v1/onboarding/mal", "POST", mal, idem || nyIdempotensnokkel());

export const settMalsteg = (malId, steg, idem) =>
  _muter(`/v1/onboarding/mal/${encodeURIComponent(malId)}/steg`,
         "POST", { steg }, idem || nyIdempotensnokkel());

export const startOnboardinglop = (lop, idem) =>
  _muter("/v1/onboarding/lop", "POST", lop, idem || nyIdempotensnokkel());

export const settStegeier = (lopId, stegNr, eierBrukerId, idem) =>
  _muter(
    `/v1/onboarding/lop/${encodeURIComponent(lopId)}/steg/${stegNr}/eier`,
    "POST", { eier_bruker_id: eierBrukerId },
    idem || nyIdempotensnokkel());

export const fullforSteg = (lopId, stegNr, notat, idem) =>
  _muter(
    `/v1/onboarding/lop/${encodeURIComponent(lopId)}/steg/${stegNr}/fullfor`,
    "POST", { notat }, idem || nyIdempotensnokkel());

export const avsluttOnboardinglop = (lopId, status, begrunnelse, idem) =>
  _muter(`/v1/onboarding/lop/${encodeURIComponent(lopId)}/avslutt`,
         "POST", { status, begrunnelse },
         idem || nyIdempotensnokkel());
// M-23 (104): fordringsregisteret. FORDRINGEN og hver HENDELSE bærer sin
// egen SP-2-nøkkel — serveren utleder id-en av den. En dobbelt registrert
// innbetaling ville gjort «hvor mye skylder de» til et tall som er for
// lavt, og et krav ville blitt lukket for tidlig.
//
// `nesteTrinn` TAR INGEN TRINNPARAMETER, og det er dommen: døren flytter
// til NESTE trinn. En funksjon som lot kalleren be om «sett trinn 3»
// ville invitert til nettopp det hoppet vakten i 104 finnes for å
// hindre — og for kunden er forskjellen mellom en påminnelse og et
// inkassovarsel hele saken.
//
// DET FINNES INGEN SENDEFUNKSJON HER, og fraværet er dommen:
// katalogteksten lover et forslag om nedbetalingsplan til kunden, v1
// registrerer fordringen. En purring til feil kunde kan ikke trekkes
// tilbake.
export const settPurreplan = (trinn, idem) =>
  _muter("/v1/fordring/purreplan", "POST", { trinn },
         idem || nyIdempotensnokkel());

export const registrerFordring = (fordring, idem) =>
  _muter("/v1/fordring", "POST", fordring, idem || nyIdempotensnokkel());

export const registrerBetaling = (fordringId, belopOre, inntruffet, idem) =>
  _muter(`/v1/fordring/${encodeURIComponent(fordringId)}/betaling`,
         "POST", { belop_ore: belopOre, inntruffet },
         idem || nyIdempotensnokkel());

export const nesteTrinn = (fordringId, begrunnelse, idem) =>
  _muter(`/v1/fordring/${encodeURIComponent(fordringId)}/neste-trinn`,
         "POST", { begrunnelse }, idem || nyIdempotensnokkel());

export const ettergiFordring = (fordringId, begrunnelse, idem) =>
  _muter(`/v1/fordring/${encodeURIComponent(fordringId)}/ettergi`,
         "POST", { begrunnelse }, idem || nyIdempotensnokkel());

// M-24 (105): leverandør- og SLA-registeret. LEVERANDØREN, AVTALEN og
// hver MÅLING bærer sin egen SP-2-nøkkel — serveren utleder id-en av
// den. En dobbelt registrert måling ville telt det samme bruddet to
// ganger, og et funn som sier «tre brudd» der det var to er et funn
// ingen kan handle på.
//
// DET FINNES INGEN BETALINGSFUNKSJON HER, og fraværet er dommen:
// katalogteksten lover leverandørbetaling innen policygrenser, v1
// registrerer avtalen og måler leveransen. En utgående betaling er den
// ene handlingen i katalogen som er umulig å angre.
//
// OG INGEN PRISFUNKSJON: M-24 oppdager kostnadsøkningen, M-26 foreslår
// ny pris. `prisavvik` som kommer TILBAKE er et avvik mellom to målte
// tall, ikke et forslag.
export const settTerskler = (terskler, idem) =>
  _muter("/v1/leverandor/terskler", "POST", terskler,
         idem || nyIdempotensnokkel());

export const registrerLeverandor = (leverandor, idem) =>
  _muter("/v1/leverandor/part", "POST", leverandor,
         idem || nyIdempotensnokkel());

export const registrerAvtale = (avtale, idem) =>
  _muter("/v1/leverandor/avtale", "POST", avtale,
         idem || nyIdempotensnokkel());

export const registrerLeveranse = (avtaleId, maling, idem) =>
  _muter(`/v1/leverandor/${encodeURIComponent(avtaleId)}/leveranse`,
         "POST", maling, idem || nyIdempotensnokkel());

export const avsluttAvtale = (avtaleId, begrunnelse, idem) =>
  _muter(`/v1/leverandor/${encodeURIComponent(avtaleId)}/avslutt`,
         "POST", { begrunnelse }, idem || nyIdempotensnokkel());

// M-14 (106): fakturakontrollen. FAKTURAEN og hver KONTROLL bærer sin
// egen SP-2-nøkkel — serveren utleder id-en av den. En dobbelt
// registrert faktura er nøyaktig det modulen finnes for å hindre.
//
// DET FINNES INGEN BOKFØRINGSFUNKSJON HER, OG INGEN SIGNERING. Policyen
// vi sender ut navngir modulen som verifikatoren `v_regnskap`, betrodd
// for `faktura_godkjent`, og bruker den attestasjonen til å la
// `faktura.bokfor` gå automatisk. `avgjorFaktura` tar to utfall —
// `kontrollert` og `avvist` — og ingen av dem er en bokføring.
export const settFakturaterskler = (terskler, idem) =>
  _muter("/v1/faktura/terskler", "POST", terskler,
         idem || nyIdempotensnokkel());

export const settMvasats = (sats, idem) =>
  _muter("/v1/faktura/mvasats", "POST", sats,
         idem || nyIdempotensnokkel());

export const registrerFaktura = (faktura, idem) =>
  _muter("/v1/faktura", "POST", faktura, idem || nyIdempotensnokkel());

export const registrerFakturakontroll = (fakturaId, utfall, notat, idem) =>
  _muter(`/v1/faktura/${encodeURIComponent(fakturaId)}/kontroll`,
         "POST", { utfall, notat }, idem || nyIdempotensnokkel());

export const avgjorFaktura = (fakturaId, status, begrunnelse, idem) =>
  _muter(`/v1/faktura/${encodeURIComponent(fakturaId)}/avgjor`,
         "POST", { status, begrunnelse }, idem || nyIdempotensnokkel());
// M-25 (107): prosjekt- og kontraktregisteret. PROSJEKTET og hver
// ARBEIDSFØRING bærer sin egen SP-2-nøkkel — serveren utleder id-en av
// den. En dobbelt ført time er et forbruk som er for høyt, og et
// budsjett som ser sprukket ut uten å være det.
//
// DET FINNES INGEN FAKTURAFUNKSJON HER, og fraværet er dommen: policyen
// vi sender ut navngir modulen som `v_prosjekt`, betrodd for
// `milepael_dokumentert`, og bruker den attestasjonen til å la
// `ordre.bekreft_og_fakturer` gå automatisk. `naaMilepael` KREVER en
// dokumentasjonsreferanse og stiller ingen krav.
export const settProsjektterskler = (terskler, idem) =>
  _muter("/v1/prosjekt/terskler", "POST", terskler,
         idem || nyIdempotensnokkel());

export const registrerProsjekt = (prosjekt, idem) =>
  _muter("/v1/prosjekt", "POST", prosjekt, idem || nyIdempotensnokkel());

export const settBetalingsplan = (prosjektId, milepaeler, idem) =>
  _muter(`/v1/prosjekt/${encodeURIComponent(prosjektId)}/betalingsplan`,
         "POST", { milepaeler }, idem || nyIdempotensnokkel());

export const naaMilepael = (prosjektId, milepaelNr, dokumentasjonRef,
                            idem) =>
  _muter(`/v1/prosjekt/${encodeURIComponent(prosjektId)}/milepael`,
         "POST", { milepael_nr: milepaelNr,
                   dokumentasjon_ref: dokumentasjonRef },
         idem || nyIdempotensnokkel());

export const registrerArbeid = (prosjektId, arbeid, idem) =>
  _muter(`/v1/prosjekt/${encodeURIComponent(prosjektId)}/arbeid`,
         "POST", arbeid, idem || nyIdempotensnokkel());

export const avsluttProsjekt = (prosjektId, begrunnelse, idem) =>
  _muter(`/v1/prosjekt/${encodeURIComponent(prosjektId)}/avslutt`,
         "POST", { begrunnelse }, idem || nyIdempotensnokkel());

// M-26 (108): prisboka. PRODUKTET bærer sin egen SP-2-nøkkel; prisen og
// klausulen gjør det ikke — versjonen ER nøkkelen der, og en gjentatt
// prisendring gir en ny versjon fordi den ER en ny beslutning.
//
// DET FINNES INGEN TILBUDSFUNKSJON HER, og fraværet er dommen: alle tre
// bransjemalene navngir modulen som `v_prisbok` og bruker
// `priser_fra_prisbok` til å la `tilbud.generer` gå automatisk. v1 er
// boka; et tilbud er et bindende utspill mot en kunde.
//
// OG `settKlausul` SENDER INGEN HASH. Den regnes i basen, av teksten
// selv — en hash flaten oppga ville vært en påstand om innholdet, ikke
// en måling av det.
export const settPrisbokterskler = (terskler, idem) =>
  _muter("/v1/prisbok/terskler", "POST", terskler,
         idem || nyIdempotensnokkel());

export const registrerProdukt = (produkt, idem) =>
  _muter("/v1/prisbok/produkt", "POST", produkt,
         idem || nyIdempotensnokkel());

export const settKlausul = (klausul, idem) =>
  _muter("/v1/prisbok/klausul", "POST", klausul,
         idem || nyIdempotensnokkel());

export const settPris = (produktId, pris, idem) =>
  _muter(`/v1/prisbok/${encodeURIComponent(produktId)}/pris`,
         "POST", pris, idem || nyIdempotensnokkel());

export const settProduktAktiv = (produktId, aktiv, idem) =>
  _muter(`/v1/prisbok/${encodeURIComponent(produktId)}/aktiv`,
         "POST", { aktiv }, idem || nyIdempotensnokkel());

// M-27 (109): lagerregisteret. ALLE skriveveiene sender en
// Idempotency-Key, slik resten av modulene gjør. Forskjellen ligger i
// hva API-et gjør med den: for VAREN og BEVEGELSENE utledes id-en
// deterministisk av nøkkelen (SP-2), mens BESTILLINGSPUNKTET er
// versjonerende og har versjonen som identitet i basen.
//
// FOR BEVEGELSENE ER DEN UTLEDEDE ID-EN STRENGT NØDVENDIG: en gjentatt
// POST må ikke bli to linjer i hovedboken, for da er beholdningen feil.
//
// DET FINNES INGEN BESTILLINGSFUNKSJON HER, og fraværet er dommen: to
// av tre bransjemaler navngir modulen som `v_lager` og bruker
// `lager_reservert` til å la `lager.bestill_pafyll` gå automatisk. v1
// skriver funnet; en bestilling binder virksomheten økonomisk.
//
// OG DET FINNES INGEN «SETT BEHOLDNING». En telling sender det TALTE
// antallet, og basen skriver differansen som en linje.
export const settLagerterskler = (terskler, idem) =>
  _muter("/v1/lager/terskler", "POST", terskler,
         idem || nyIdempotensnokkel());

export const registrerVare = (vare, idem) =>
  _muter("/v1/lager/vare", "POST", vare, idem || nyIdempotensnokkel());

export const settBestillingspunkt = (vareId, punkt, idem) =>
  _muter(`/v1/lager/${encodeURIComponent(vareId)}/punkt`,
         "POST", punkt, idem || nyIdempotensnokkel());

export const registrerBevegelse = (vareId, bevegelse, idem) =>
  _muter(`/v1/lager/${encodeURIComponent(vareId)}/bevegelse`,
         "POST", bevegelse, idem || nyIdempotensnokkel());

export const registrerTelling = (vareId, telling, idem) =>
  _muter(`/v1/lager/${encodeURIComponent(vareId)}/telling`,
         "POST", telling, idem || nyIdempotensnokkel());

export const settVareAktiv = (vareId, aktiv, idem) =>
  _muter(`/v1/lager/${encodeURIComponent(vareId)}/aktiv`,
         "POST", { aktiv }, idem || nyIdempotensnokkel());

// M-42 (110): kontoregisteret. ALLE skriveveiene sender en
// Idempotency-Key, og for MOTTAKEREN, KONTOOPPGAVEN og VERIFIKASJONEN
// utledes id-en deterministisk av den (SP-2). For kontooppgaven er det
// strengt nødvendig: en gjentatt POST må ikke bli to linjer i en
// historikk som ER beviset.
//
// DET FINNES INGEN SPERREFUNKSJON HER, og fraværet er dommen: to av tre
// bransjemaler navngir modulen som `v_kontovakt` og bruker
// `svindelsjekk_bestatt` til å la utgående betalinger gå automatisk.
// Det farligste en betalingsvakt kan gjøre er ikke å slippe noe gjennom
// — det er å stoppe noe.
//
// OG `oppgiKonto` SENDER KONTONUMMERET ÉN GANG, til en dør som
// normaliserer det, regner masken og hashen, og kaster det. Svaret er
// MASKEN; klienten får aldri nummeret tilbake.
export const settKontoterskler = (terskler, idem) =>
  _muter("/v1/kontovakt/terskler", "POST", terskler,
         idem || nyIdempotensnokkel());

export const registrerMottaker = (mottaker, idem) =>
  _muter("/v1/kontovakt/mottaker", "POST", mottaker,
         idem || nyIdempotensnokkel());

export const oppgiKonto = (mottakerId, konto, idem) =>
  _muter(`/v1/kontovakt/${encodeURIComponent(mottakerId)}/konto`,
         "POST", konto, idem || nyIdempotensnokkel());

export const verifiserKonto = (oppgaveId, verifikasjon, idem) =>
  _muter(`/v1/kontovakt/oppgave/${encodeURIComponent(oppgaveId)}`
         + "/verifikasjon",
         "POST", verifikasjon, idem || nyIdempotensnokkel());

export const settMottakerAktiv = (mottakerId, aktiv, idem) =>
  _muter(`/v1/kontovakt/${encodeURIComponent(mottakerId)}/aktiv`,
         "POST", { aktiv }, idem || nyIdempotensnokkel());

// M-41 (111): betalingsregisteret. SUBJEKTET og STATUSHENDELSEN bærer
// sin egen SP-2-nøkkel; abonnementsperioden gjør det ikke — versjonen
// ER nøkkelen der, og en ny periode er en ny beslutning.
//
// FOR STATUSHENDELSEN ER SP-2 STRENGT NØDVENDIG: en gjentatt POST må
// ikke bli to statusskift i en historikk som ER beviset.
//
// DET FINNES INGEN REFUSJONSFUNKSJON HER, og fraværet er dommen:
// netthandelsmalen har `refusjon.utfor` stående som `modus: auto` og
// `reversering: irreversibel` opp til 5000 NOK, gatet på denne
// modulen. En refusjon er penger ut døra og kan ikke kalles tilbake.
//
// OG `registrerStatus` SENDER BETALINGSMIDDELET ÉN GANG, til en dør som
// normaliserer det, regner masken og hashen, og kaster det. Svaret er
// MASKEN; klienten får aldri nummeret tilbake.
export const settBetalingsterskler = (terskler, idem) =>
  _muter("/v1/betaling/terskler", "POST", terskler,
         idem || nyIdempotensnokkel());

export const registrerBetalingssubjekt = (subjekt, idem) =>
  _muter("/v1/betaling/subjekt", "POST", subjekt,
         idem || nyIdempotensnokkel());

export const registrerBetalingsstatus = (subjektId, hendelse, idem) =>
  _muter(`/v1/betaling/${encodeURIComponent(subjektId)}/status`,
         "POST", hendelse, idem || nyIdempotensnokkel());

export const settAbonnementsstatus = (subjektId, periode, idem) =>
  _muter(`/v1/betaling/${encodeURIComponent(subjektId)}/abonnement`,
         "POST", periode, idem || nyIdempotensnokkel());

export const settBetalingssubjektAktiv = (subjektId, aktiv, idem) =>
  _muter(`/v1/betaling/${encodeURIComponent(subjektId)}/aktiv`,
         "POST", { aktiv }, idem || nyIdempotensnokkel());

// M-19 (112): adresseregisteret. SUBJEKTET, ADRESSEVERSJONEN og
// KONTROLLEN bærer hver sin SP-2-nøkkel.
//
// FOR ADRESSEVERSJONEN OG KONTROLLEN ER SP-2 STRENGT NØDVENDIG: en
// gjentatt POST må ikke bli to versjoner i en historikk som ER beviset
// på hva kunden faktisk oppga.
//
// DET FINNES INGEN OPPSLAGSFUNKSJON HER, og fraværet er dommen:
// netthandelsmalen navngir modulen som `v_adresse` og lar M-25s
// `ordre.bekreft_og_fakturer` gå automatisk på `adresse_validert`. Et
// oppslag mot et adresseregister er en utgående kanal med
// personopplysninger i — og at en adresse FINNES sier uansett ikke at
// pakken kommer fram til den som skal ha den.
//
// OG `registrerAdresse` SENDER ADRESSEN SLIK DEN BLE OPPGITT.
// Normaliseringen regnes i BASEN, av `m19_normaliser`; den er ikke et
// felt klienten kan sende, for da kunne den vært hva som helst.
export const settAdressekrav = (krav, idem) =>
  _muter("/v1/adresse/krav", "POST", krav,
         idem || nyIdempotensnokkel());

export const registrerAdressesubjekt = (subjekt, idem) =>
  _muter("/v1/adresse/subjekt", "POST", subjekt,
         idem || nyIdempotensnokkel());

export const registrerAdresse = (subjektId, adresse, idem) =>
  _muter(`/v1/adresse/${encodeURIComponent(subjektId)}/versjon`,
         "POST", adresse, idem || nyIdempotensnokkel());

export const registrerAdressekontroll = (versjonId, kontroll, idem) =>
  _muter(`/v1/adresse/versjon/${encodeURIComponent(versjonId)}/kontroll`,
         "POST", kontroll, idem || nyIdempotensnokkel());

export const settAdressesubjektAktiv = (subjektId, aktiv, idem) =>
  _muter(`/v1/adresse/${encodeURIComponent(subjektId)}/aktiv`,
         "POST", { aktiv }, idem || nyIdempotensnokkel());

// M-51 (119): tilskudds- og støtteordningsvakten. Alle bærer
// SP-2-nøkkel.
//
// DET FINNES INGEN `sendSoknad` HER, OG DET KAN IKKE FINNES: 119 har
// ingen «sendt»-kolonne. `ferdigstillEstimat` setter en tilstand hos
// oss, og døra nekter uten minst én forutsetning.
//
// OG `leggTilEstimatpost` TAR ALLTID EN `kildepost_id`. Det er ikke en
// validering her — `tilskuddsestimat` har ingen beløpskolonne, så
// summen ER summen av postene.
export const settTilskuddskrav = (krav, idem) =>
  _muter("/v1/tilskudd/krav", "POST", krav,
         idem || nyIdempotensnokkel());

export const registrerOrdning = (ordning, idem) =>
  _muter("/v1/tilskudd/ordning", "POST", ordning,
         idem || nyIdempotensnokkel());

export const registrerKildepost = (post, idem) =>
  _muter("/v1/tilskudd/kildepost", "POST", post,
         idem || nyIdempotensnokkel());

export const opprettTilskuddsestimat = (ordningId, periode, idem) =>
  _muter(`/v1/tilskudd/${encodeURIComponent(ordningId)}/estimat`,
         "POST", periode, idem || nyIdempotensnokkel());

export const leggTilEstimatpost = (estimatId, post, idem) =>
  _muter(`/v1/tilskudd/estimat/${encodeURIComponent(estimatId)}/post`,
         "POST", post, idem || nyIdempotensnokkel());

export const leggTilForutsetning = (estimatId, forutsetning, idem) =>
  _muter(`/v1/tilskudd/estimat/${encodeURIComponent(estimatId)}`
         + "/forutsetning",
         "POST", forutsetning, idem || nyIdempotensnokkel());

export const ferdigstillEstimat = (estimatId, idem) =>
  _muter(`/v1/tilskudd/estimat/${encodeURIComponent(estimatId)}`
         + "/ferdigstill",
         "POST", {}, idem || nyIdempotensnokkel());

export const settOrdningAktiv = (ordningId, aktiv, idem) =>
  _muter(`/v1/tilskudd/${encodeURIComponent(ordningId)}/aktiv`,
         "POST", { aktiv }, idem || nyIdempotensnokkel());

export const lukkTilskuddsfunn = (ordningId, funntype, notat, idem) =>
  _muter(`/v1/tilskudd/${encodeURIComponent(ordningId)}/funn/lukk`,
         "POST", { funntype, notat }, idem || nyIdempotensnokkel());

// M-46 (118): anbuds- og konkurransevakten. Alle bærer SP-2-nøkkel.
//
// DET FINNES INGEN `sendAnbud` HER, OG DET KAN IKKE FINNES: 118 har
// ingen «sendt»-kolonne å skrive til. `merkUtkastKlart` setter en
// tilstand HOS OSS, og døra nekter så lenge et absolutt krav mangler.
//
// OG `registrerAnbudspunkt` TAR ALLTID EN `kilde_id`. Det er ikke en
// validering her — `utkastpunkt` har ingen fritekstkolonne, så et
// punkt uten kilde kan ikke uttrykkes.
export const settAnbudsprofil = (profil, idem) =>
  _muter("/v1/anbud/profil", "POST", profil,
         idem || nyIdempotensnokkel());

export const registrerAnbud = (anbud, idem) =>
  _muter("/v1/anbud/registrer", "POST", anbud,
         idem || nyIdempotensnokkel());

export const registrerKildedokument = (kilde, idem) =>
  _muter("/v1/anbud/kilde", "POST", kilde,
         idem || nyIdempotensnokkel());

export const registrerAnbudskrav = (anbudId, krav, idem) =>
  _muter(`/v1/anbud/${encodeURIComponent(anbudId)}/krav/ny`,
         "POST", krav, idem || nyIdempotensnokkel());

export const opprettAnbudsutkast = (anbudId, idem) =>
  _muter(`/v1/anbud/${encodeURIComponent(anbudId)}/utkast/ny`,
         "POST", {}, idem || nyIdempotensnokkel());

export const registrerAnbudspunkt = (utkastId, punkt, idem) =>
  _muter(`/v1/anbud/utkast/${encodeURIComponent(utkastId)}/punkt`,
         "POST", punkt, idem || nyIdempotensnokkel());

export const merkUtkastKlart = (utkastId, idem) =>
  _muter(`/v1/anbud/utkast/${encodeURIComponent(utkastId)}/klart`,
         "POST", {}, idem || nyIdempotensnokkel());

export const settAnbudAktiv = (anbudId, aktiv, idem) =>
  _muter(`/v1/anbud/${encodeURIComponent(anbudId)}/aktiv`,
         "POST", { aktiv }, idem || nyIdempotensnokkel());

export const lukkAnbudsfunn = (anbudId, funntype, notat, idem) =>
  _muter(`/v1/anbud/${encodeURIComponent(anbudId)}/funn/lukk`,
         "POST", { funntype, notat }, idem || nyIdempotensnokkel());

// M-49 (117): sanksjonskontrollen. Alle bærer SP-2-nøkkel, og for
// AVKLARINGEN er den strengt nødvendig: en gjentatt POST må ikke bli
// to dommer over samme treff.
//
// DET FINNES INGEN `blokkerMotpart` HER, OG INGEN MASSEAVKLARING.
// Fraværene er portene `modulen_blokkerte_motpart` og
// `modulen_avfeide_navnelikhet` — se toppen av `flater/sanksjon.js`.
export const settSanksjonskrav = (krav, idem) =>
  _muter("/v1/sanksjon/krav", "POST", krav,
         idem || nyIdempotensnokkel());

export const registrerSanksjonsliste = (liste, idem) =>
  _muter("/v1/sanksjon/liste", "POST", liste,
         idem || nyIdempotensnokkel());

export const registrerSanksjonssubjekt = (subjekt, idem) =>
  _muter("/v1/sanksjon/subjekt", "POST", subjekt,
         idem || nyIdempotensnokkel());

export const registrerSanksjonskontroll = (subjektId, kontroll, idem) =>
  _muter(`/v1/sanksjon/${encodeURIComponent(subjektId)}/kontroll`,
         "POST", kontroll, idem || nyIdempotensnokkel());

// KONKLUSJON OG BEGRUNNELSE ER ARGUMENTER, IKKE STANDARDVERDIER. Det
// finnes ingen `konklusjon = "ikke_samme_part"` her: en forhåndsvalgt
// konklusjon ville gjort `modulen_avfeide_navnelikhet` til pynt.
export const avklarSanksjonstreff = (treffId, konklusjon, begrunnelse,
                                     idem) =>
  _muter(`/v1/sanksjon/treff/${encodeURIComponent(treffId)}/avklaring`,
         "POST", { konklusjon, begrunnelse },
         idem || nyIdempotensnokkel());

export const settSanksjonssubjektAktiv = (subjektId, aktiv, idem) =>
  _muter(`/v1/sanksjon/${encodeURIComponent(subjektId)}/aktiv`,
         "POST", { aktiv }, idem || nyIdempotensnokkel());

export const lukkSanksjonsfunn = (subjektId, funntype, notat, idem) =>
  _muter(`/v1/sanksjon/${encodeURIComponent(subjektId)}/funn/lukk`,
         "POST", { funntype, notat }, idem || nyIdempotensnokkel());

// M-48 (116): motpartsregisteret. Alle bærer SP-2-nøkkel, og for
// OPPSLAGET er den strengt nødvendig: en gjentatt POST må ikke bli to
// utgående forespørsler. Det er forskjellen på en dobbeltklikk og to
// oppslag noen må svare for.
export const settMotpartskrav = (krav, idem) =>
  _muter("/v1/motpart/krav", "POST", krav,
         idem || nyIdempotensnokkel());

export const registrerMotpart = (motpart, idem) =>
  _muter("/v1/motpart/registrer", "POST", motpart,
         idem || nyIdempotensnokkel());

// FORMÅL OG HJEMMEL ER ARGUMENTER, IKKE STANDARDVERDIER. Det finnes
// ingen `formaal = "kredittvurdering"` her: et oppslag uten en
// oppgitt grunn er nettopp det `oppslag_uten_formaal_og_hjemmel`
// forbyr, og en standardverdi ville gjort porten til pynt.
export const slaaOppMotpart = (motpartId, formaal, hjemmel, idem) =>
  _muter(`/v1/motpart/${encodeURIComponent(motpartId)}/oppslag`,
         "POST", { formaal, hjemmel }, idem || nyIdempotensnokkel());

export const registrerMotpartsvurdering = (versjonId, vurdering, idem) =>
  _muter(`/v1/motpart/versjon/${encodeURIComponent(versjonId)}/vurdering`,
         "POST", vurdering, idem || nyIdempotensnokkel());

export const deaktiverMotpart = (motpartId, idem) =>
  _muter(`/v1/motpart/${encodeURIComponent(motpartId)}/deaktiver`,
         "POST", {}, idem || nyIdempotensnokkel());

export const lukkMotpartsfunn = (motpartId, funntype, notat, idem) =>
  _muter(`/v1/motpart/${encodeURIComponent(motpartId)}/funn/lukk`,
         "POST", { funntype, notat }, idem || nyIdempotensnokkel());

// M-39 (113): lønnsgrunnlaget. TAKEREN, PLANEN og TIMEN bærer hver sin
// SP-2-nøkkel.
//
// FOR TIMEN ER SP-2 STRENGT NØDVENDIG: en gjentatt POST må ikke bli to
// arbeidsdager i et grunnlag noen skal få betalt etter.
//
// DET FINNES INGEN UTBETALINGSFUNKSJON OG INGEN EKSPORTFUNKSJON HER, og
// fraværet er dommen: håndverk/bygg-malen navngir modulen som `v_lonn`
// og bruker alle tre vilkårene den er betrodd for til å la
// `timeliste.samle_og_valider` gå automatisk. En lønnsfil er ikke en
// betaling — det er en fil, den ser harmløs ut, den kan «bare
// genereres», og den rammer alle på én gang.
//
// OG `registrerTimer` SENDER MINUTTER, aldri timer med desimaler.
// Konverteringen skjer i `tilMinutter`, én gang, og API-et ser aldri et
// flyttall.
export const settLonnsterskler = (terskler, idem) =>
  _muter("/v1/lonn/terskler", "POST", terskler,
         idem || nyIdempotensnokkel());

export const registrerLonnstaker = (taker, idem) =>
  _muter("/v1/lonn/taker", "POST", taker, idem || nyIdempotensnokkel());

export const settArbeidsplan = (takerId, plan, idem) =>
  _muter(`/v1/lonn/${encodeURIComponent(takerId)}/plan`,
         "POST", plan, idem || nyIdempotensnokkel());

export const registrerTimer = (takerId, timer, idem) =>
  _muter(`/v1/lonn/${encodeURIComponent(takerId)}/timer`,
         "POST", timer, idem || nyIdempotensnokkel());

export const settLonnstakerAktiv = (takerId, aktiv, idem) =>
  _muter(`/v1/lonn/${encodeURIComponent(takerId)}/aktiv`,
         "POST", { aktiv }, idem || nyIdempotensnokkel());

// M-44 (114): kampanjeregisteret. MOTTAKEREN, SAMTYKKEHENDELSEN og
// KAMPANJEN bærer hver sin SP-2-nøkkel.
//
// FOR SAMTYKKEHENDELSEN ER SP-2 STRENGT NØDVENDIG: en gjentatt POST må
// ikke bli to samtykker i en historikk som ER svaret på om vi hadde
// lov.
//
// DET FINNES INGEN SENDEFUNKSJON HER, og fraværet er dommen. M-44 er en
// annen figur enn de tre andre i klyngen: de er manglende
// VERIFIKATORER, denne er den manglende AKTØREN. Modulen finnes FOR å
// sende, og v1 sender null. Og botemiddelet malen foreslår for en
// feilsendt e-post er å sende en TIL.
//
// `leggIKampanjeplan` SENDER INGENTING. Den skriver ned at mottakeren
// VAR MENT å få kampanjen — og svarer med hvor mange hen da står
// oppført til i tenantens periode.
export const settKampanjegrense = (grense, idem) =>
  _muter("/v1/kampanje/grense", "POST", grense,
         idem || nyIdempotensnokkel());

export const registrerKampanjemottaker = (mottaker, idem) =>
  _muter("/v1/kampanje/mottaker", "POST", mottaker,
         idem || nyIdempotensnokkel());

export const registrerSamtykke = (mottakerId, hendelse, idem) =>
  _muter(`/v1/kampanje/mottaker/${encodeURIComponent(mottakerId)}/samtykke`,
         "POST", hendelse, idem || nyIdempotensnokkel());

export const registrerKampanje = (kampanje, idem) =>
  _muter("/v1/kampanje/kampanje", "POST", kampanje,
         idem || nyIdempotensnokkel());

export const avlysKampanje = (kampanjeId, idem) =>
  _muter(`/v1/kampanje/kampanje/${encodeURIComponent(kampanjeId)}/avlys`,
         "POST", {}, idem || nyIdempotensnokkel());

export const leggIKampanjeplan = (kampanjeId, mottakerId, idem) =>
  _muter(`/v1/kampanje/kampanje/${encodeURIComponent(kampanjeId)}/plan`,
         "POST", { mottaker_id: mottakerId },
         idem || nyIdempotensnokkel());

export const settKampanjemottakerAktiv = (mottakerId, aktiv, idem) =>
  _muter(`/v1/kampanje/mottaker/${encodeURIComponent(mottakerId)}/aktiv`,
         "POST", { aktiv }, idem || nyIdempotensnokkel());

// M-55 (120): merkevare- og IP-overvåkeren. Alle bærer SP-2-nøkkel.
//
// DET FINNES INGEN `sendKrav` HER, INGEN `sendKlage`, INGEN MOTTAKER —
// OG DET KAN IKKE FINNES: 120 har ingen kolonne å skrive et krav til.
// Et krav sendt på et automatisk funn er en ANKLAGE MOT EN NAVNGITT
// PART, og en feilaktig anklage er ikke reversibel ved å trekke den.
//
// MODULENS ENESTE UTGANG ER `henvisMerkevarefunn`, som fester en peker
// til en sak i M-37s unntakskø. Der beslutter et menneske.
//
// OG `registrerMerkevarefunn` TAR ALLTID EN `kopi_id`. Det er ikke en
// validering her — `merkevarefunn.kopi_id` er NOT NULL med
// fremmednøkkel, så et funn uten bevaringskopi kan ikke uttrykkes.
export const settMerkevarekrav = (krav, idem) =>
  _muter("/v1/merkevare/krav", "POST", krav,
         idem || nyIdempotensnokkel());

export const registrerMerkevare = (merke, idem) =>
  _muter("/v1/merkevare/merke", "POST", merke,
         idem || nyIdempotensnokkel());

export const registrerBevaringskopi = (kopi, idem) =>
  _muter("/v1/merkevare/bevaringskopi", "POST", kopi,
         idem || nyIdempotensnokkel());

export const registrerMerkevarefunn = (funn, idem) =>
  _muter("/v1/merkevare/funn", "POST", funn,
         idem || nyIdempotensnokkel());

export const vurderMerkevarefunn = (funnId, idem) =>
  _muter(`/v1/merkevare/funn/${encodeURIComponent(funnId)}/vurder`,
         "POST", {}, idem || nyIdempotensnokkel());

export const henvisMerkevarefunn = (funnId, unntakId, idem) =>
  _muter(`/v1/merkevare/funn/${encodeURIComponent(funnId)}/henvis`,
         "POST", { unntak_id: unntakId },
         idem || nyIdempotensnokkel());

export const lukkMerkevarefunn = (funnId, begrunnelse, idem) =>
  _muter(`/v1/merkevare/funn/${encodeURIComponent(funnId)}/lukk`,
         "POST", { begrunnelse }, idem || nyIdempotensnokkel());

export const settMerkevareAktiv = (merkevareId, aktiv, idem) =>
  _muter(`/v1/merkevare/${encodeURIComponent(merkevareId)}/aktiv`,
         "POST", { aktiv }, idem || nyIdempotensnokkel());

export const lukkMerkevarevarsel = (varselId, notat, idem) =>
  _muter(`/v1/merkevare/varsel/${encodeURIComponent(varselId)}/lukk`,
         "POST", { notat }, idem || nyIdempotensnokkel());

// M-54 (121): EHF- og Peppol-avviksretteren. Alle bærer SP-2-nøkkel.
//
// DET FINNES INGEN `sendFaktura` HER, OG DET KAN IKKE FINNES: 121 har
// ingen mottaker, ingen utboks og ingen «sendt»-kolonne. En faktura
// sendt to ganger er et DOBBELT BETALINGSKRAV.
//
// `merkRettingKlar` SETTER EN TILSTAND HOS OSS. Signaturen hører til
// v2, og forutsetningen for v2 er målt: hvor ofte klargjøringen er
// feil.
//
// `settEhfGyldigTil` FINNES FORDI REGELEN ER MYNDIGHETENS: et
// standardorgan som kunngjør i juni at EHF 3.0 trekkes 31. desember,
// er nettopp den endringen modulen skal følge med på. Alt annet ved
// regelsettet er frosset.
export const settEhfkrav = (krav, idem) =>
  _muter("/v1/ehf/krav", "POST", krav, idem || nyIdempotensnokkel());

export const registrerEhfregelsett = (sett, idem) =>
  _muter("/v1/ehf/regelsett", "POST", sett,
         idem || nyIdempotensnokkel());

export const settEhfGyldigTil = (regelsettId, gyldigTil, idem) =>
  _muter(`/v1/ehf/regelsett/${encodeURIComponent(regelsettId)}`
         + "/gyldig-til",
         "POST", { gyldig_til: gyldigTil },
         idem || nyIdempotensnokkel());

export const registrerEhfregel = (regel, idem) =>
  _muter("/v1/ehf/regel", "POST", regel,
         idem || nyIdempotensnokkel());

export const registrerEhfdokument = (dokument, idem) =>
  _muter("/v1/ehf/dokument", "POST", dokument,
         idem || nyIdempotensnokkel());

export const registrerEhffelter = (dokumentId, felter, idem) =>
  _muter(`/v1/ehf/dokument/${encodeURIComponent(dokumentId)}/felter`,
         "POST", { felter }, idem || nyIdempotensnokkel());

export const validerEhfdokument = (dokumentId, regelsettId, idem) =>
  _muter(`/v1/ehf/dokument/${encodeURIComponent(dokumentId)}/valider`,
         "POST", { regelsett_id: regelsettId },
         idem || nyIdempotensnokkel());

export const registrerEhfretting = (avvikId, retting, idem) =>
  _muter(`/v1/ehf/avvik/${encodeURIComponent(avvikId)}/retting`,
         "POST", retting, idem || nyIdempotensnokkel());

export const merkRettingKlar = (rettingId, idem) =>
  _muter(`/v1/ehf/retting/${encodeURIComponent(rettingId)}/klar`,
         "POST", {}, idem || nyIdempotensnokkel());

export const lukkEhffunn = (funnId, notat, idem) =>
  _muter(`/v1/ehf/funn/${encodeURIComponent(funnId)}/lukk`,
         "POST", { notat }, idem || nyIdempotensnokkel());

// M-52 (122): toll- og HS-kodeagenten. Alle bærer SP-2-nøkkel.
//
// DET FINNES INGEN `deklarer` HER, OG DET KAN IKKE FINNES: 122 har
// ingen «deklarert»-kolonne, ingen mottaker og ingen utboks. En HS-kode
// er en RETTSLIG PÅSTAND om hva en vare er, og feil kode gir bot — som
// treffer KUNDEN.
//
// `merkForslagKlart` SETTER EN TILSTAND HOS OSS. Deklarasjonen hører
// til v2.
//
// OG `avgiTollforslag` TAR ALLTID MINST ÉN GRUNN. Det er ikke en
// validering her — døra skriver forslaget og grunnene i SAMME setning,
// så et forslag uten grunnlag kan ikke oppstå.
export const settTollkrav = (krav, idem) =>
  _muter("/v1/toll/krav", "POST", krav, idem || nyIdempotensnokkel());

export const registrerNomenklatur = (nomenklatur, idem) =>
  _muter("/v1/toll/nomenklatur", "POST", nomenklatur,
         idem || nyIdempotensnokkel());

export const settTollGyldigTil = (nomenklaturId, gyldigTil, idem) =>
  _muter(`/v1/toll/nomenklatur/${encodeURIComponent(nomenklaturId)}`
         + "/gyldig-til",
         "POST", { gyldig_til: gyldigTil },
         idem || nyIdempotensnokkel());

export const registrerVarenummer = (varenummer, idem) =>
  _muter("/v1/toll/varenummer", "POST", varenummer,
         idem || nyIdempotensnokkel());

export const registrerTollvare = (vare, idem) =>
  _muter("/v1/toll/vare", "POST", vare,
         idem || nyIdempotensnokkel());

export const avgiTollforslag = (vareId, forslag, idem) =>
  _muter(`/v1/toll/vare/${encodeURIComponent(vareId)}/forslag`,
         "POST", forslag, idem || nyIdempotensnokkel());

export const merkForslagKlart = (forslagId, idem) =>
  _muter(`/v1/toll/forslag/${encodeURIComponent(forslagId)}/klart`,
         "POST", {}, idem || nyIdempotensnokkel());

export const lukkTollfunn = (funnId, notat, idem) =>
  _muter(`/v1/toll/funn/${encodeURIComponent(funnId)}/lukk`,
         "POST", { notat }, idem || nyIdempotensnokkel());

// M-47 (123): myndighetsrapporteringsagenten. Alle bærer SP-2-nøkkel.
//
// DET FINNES INGEN `sendInn` HER, OG DET KAN IKKE FINNES: 123 har ingen
// mottaker, ingen utboks og ingen signatur. En innsending til en
// myndighet er BINDENDE og kan ikke kalles tilbake.
//
// `registrerPliktbevis` SENDER IKKE. Den registrerer at et MENNESKE har
// sendt inn, et annet sted, og bærer kvitteringsreferansen myndigheten
// ga DEM. Vi har ingen kanal til myndigheten og påstår ikke å ha det.
//
// MEN HER ER FRAVÆRET IKKE NOK: en frist som går uten innsending er
// nøyaktig det modulen ble bygget for å hindre. Derfor finnes `frist`
// på hver rad, `dogn_til_frist` med fortegn, og to funn ingen kan lukke.
export const settMyndighetskrav = (krav, idem) =>
  _muter("/v1/myndighet/krav", "POST", krav,
         idem || nyIdempotensnokkel());

export const registrerRegelverk = (regelverk, idem) =>
  _muter("/v1/myndighet/regelverk", "POST", regelverk,
         idem || nyIdempotensnokkel());

export const settRegelverkGyldigTil = (regelverkId, gyldigTil, idem) =>
  _muter(`/v1/myndighet/regelverk/${encodeURIComponent(regelverkId)}`
         + "/gyldig-til",
         "POST", { gyldig_til: gyldigTil },
         idem || nyIdempotensnokkel());

export const registrerPlikttype = (plikttype, idem) =>
  _muter("/v1/myndighet/plikttype", "POST", plikttype,
         idem || nyIdempotensnokkel());

// NAVNET ER `registrerRapportplikt`, IKKE `registrerPlikt`: M-21 eier
// det navnet, og det er ikke bare en kollisjon — det er GRENSEN mellom
// modulene. M-21s plikter er avtalefrister, altså våre egne kontrakter.
// M-47s er lovpålagte innsendinger. Forskjellen er hvem som
// sanksjonerer, og to funksjoner med samme navn ville skjult den.
export const registrerRapportplikt = (plikt, idem) =>
  _muter("/v1/myndighet/plikt", "POST", plikt,
         idem || nyIdempotensnokkel());

export const registrerPliktbevis = (pliktId, bevis, idem) =>
  _muter(`/v1/myndighet/plikt/${encodeURIComponent(pliktId)}/bevis`,
         "POST", bevis, idem || nyIdempotensnokkel());

export const lukkMyndighetsfunn = (funnId, notat, idem) =>
  _muter(`/v1/myndighet/funn/${encodeURIComponent(funnId)}/lukk`,
         "POST", { notat }, idem || nyIdempotensnokkel());

// M-50 (124): postjournal- og innsynsvakten. Alle bærer SP-2-nøkkel.
//
// DET FINNES INGEN `hent` HER, OG DET KAN IKKE FINNES: 124 har ingen
// `hentet_automatisk` og ingen utgående vei. Postjournaler ER
// offentlige — det som treffer er at ti tusen oppslag sammenstilt i et
// register er en PROFIL, og profilen er vår, ikke kommunens.
//
// `registrerJournalpost` TAR ALLTID PERSONENE MED. Det er ikke en
// validering her — døra skriver posten og personene i SAMME setning,
// så en journalpost med navngitte privatpersoner ikke kan eksistere
// uten slettefrister.
//
// `anonymiserPerson` SLETTER IKKE. Den tømmer navnet og setter et
// spor: at vi HAR oppbevart noen skal fortsatt kunne leses, uten
// navnet. Sletting ville fjernet beviset på at vi hadde den.
export const settJournalkrav = (krav, idem) =>
  _muter("/v1/journal/krav", "POST", krav,
         idem || nyIdempotensnokkel());

export const registrerJournalkilde = (kilde, idem) =>
  _muter("/v1/journal/kilde", "POST", kilde,
         idem || nyIdempotensnokkel());

export const settKildeGyldigTil = (kildeId, gyldigTil, idem) =>
  _muter(`/v1/journal/kilde/${encodeURIComponent(kildeId)}`
         + "/gyldig-til",
         "POST", { gyldig_til: gyldigTil },
         idem || nyIdempotensnokkel());

export const opprettJournalsak = (sak, idem) =>
  _muter("/v1/journal/sak", "POST", sak,
         idem || nyIdempotensnokkel());

export const registrerJournalpost = (post, idem) =>
  _muter("/v1/journal/post", "POST", post,
         idem || nyIdempotensnokkel());

export const anonymiserPerson = (personId, idem) =>
  _muter(`/v1/journal/person/${encodeURIComponent(personId)}`
         + "/anonymiser",
         "POST", {}, idem || nyIdempotensnokkel());

export const lukkJournalfunn = (funnId, notat, idem) =>
  _muter(`/v1/journal/funn/${encodeURIComponent(funnId)}/lukk`,
         "POST", { notat }, idem || nyIdempotensnokkel());


// ---------------------------------------------------------------------
// M-53 HMS- OG AVVIKSMOTTAK (127).
//
// `meldAvvik` TAR EN FERDIG KROPP OG SENDER DEN SOM DEN ER. Den legger
// IKKE til et melderavn den ikke fikk, og den fyller ingen felt.
// Flaten bestemmer hva som er med — for et anonymt avvik er
// `melder_navn` ikke `null`, den er FRAVÆRENDE. Et lag som «hjelpsomt»
// normaliserte kroppen ville vært stedet et navn kunne snike seg inn.
//
// `anonymiserAvvik` SLETTER IKKE. Den tømmer navnet og setter et spor:
// at vi HAR hatt avviket er nøyaktig det Arbeidstilsynet etterprøver.
// Sletting ville fjernet beviset på at vi hadde det.
// ---------------------------------------------------------------------
export const settHmskrav = (krav, idem) =>
  _muter("/v1/hms/krav", "POST", krav, idem || nyIdempotensnokkel());

export const registrerHmsregel = (regel, idem) =>
  _muter("/v1/hms/regelverk", "POST", regel,
         idem || nyIdempotensnokkel());

export const meldAvvik = (avvik, idem) =>
  _muter("/v1/hms/avvik", "POST", avvik, idem || nyIdempotensnokkel());

export const registrerTiltak = (avvikId, tiltak, idem) =>
  _muter(`/v1/hms/avvik/${encodeURIComponent(avvikId)}/tiltak`,
         "POST", tiltak, idem || nyIdempotensnokkel());

export const anonymiserAvvik = (avvikId, kropp, idem) =>
  _muter(`/v1/hms/avvik/${encodeURIComponent(avvikId)}/anonymiser`,
         "POST", kropp || {}, idem || nyIdempotensnokkel());

export const lukkHmsfunn = (funnId, kropp, idem) =>
  _muter(`/v1/hms/funn/${encodeURIComponent(funnId)}/lukk`,
         "POST", kropp, idem || nyIdempotensnokkel());


// ---------------------------------------------------------------------
// M-15 LIKVIDITETS- OG KOSTNADSAGENT (128).
//
// DET FINNES INGEN `iverksettTiltak`. `vurderTiltak` tar `vurdert`
// eller `avvist`, og der stopper modulen — oppsigelsen av et
// abonnement går gjennom M-41s policykontrollerte vei.
//
// `registrerMaaling` ER DEN ENESTE VEIEN TIL Å LUKKE
// `prognose_uten_maaling`, klyngens funn ingen kan klikke bort. Den
// tar det FAKTISKE tallet og ingenting annet: om målingen traff
// intervallet regnes av båndet som står på raden, ikke av kalleren.
// ---------------------------------------------------------------------
export const settLikviditetskrav = (krav, idem) =>
  _muter("/v1/likviditet/krav", "POST", krav,
         idem || nyIdempotensnokkel());

export const registrerLikviditetsmodell = (modell, idem) =>
  _muter("/v1/likviditet/modell", "POST", modell,
         idem || nyIdempotensnokkel());

export const registrerLikviditetspost = (post, idem) =>
  _muter("/v1/likviditet/post", "POST", post,
         idem || nyIdempotensnokkel());

export const lagPrognose = (kropp, idem) =>
  _muter("/v1/likviditet/prognose", "POST", kropp,
         idem || nyIdempotensnokkel());

export const registrerMaaling = (prognoseId, kropp, idem) =>
  _muter(`/v1/likviditet/prognose/${encodeURIComponent(prognoseId)}`
         + "/maaling",
         "POST", kropp, idem || nyIdempotensnokkel());

export const foreslaaTiltak = (tiltak, idem) =>
  _muter("/v1/likviditet/tiltak", "POST", tiltak,
         idem || nyIdempotensnokkel());

export const vurderTiltak = (tiltakId, kropp, idem) =>
  _muter(`/v1/likviditet/tiltak/${encodeURIComponent(tiltakId)}`
         + "/vurder",
         "POST", kropp, idem || nyIdempotensnokkel());

export const lukkLikviditetsfunn = (funnId, kropp, idem) =>
  _muter(`/v1/likviditet/funn/${encodeURIComponent(funnId)}/lukk`,
         "POST", kropp, idem || nyIdempotensnokkel());


// ---------------------------------------------------------------------
// M-33 PREDIKSJONS- OG SCENARIOAGENT (130).
//
// DET FINNES INGEN `ansett`, INGEN `siOpp` OG INGEN `flyttVakt`.
// Modulen lager en bane og stopper der — vaktsetningen sier at ingen
// personalavgjørelse tas uten separat policy, og fraværet av en slik
// funksjon her ER håndhevelsen.
//
// `registrerBemanningsmaaling` ER DEN ENESTE VEIEN TIL Å LUKKE
// `prognose_uten_maaling`. Den tar det FAKTISKE tallet og ingenting
// annet: om målingen traff intervallet regnes av båndet som står på
// raden, ikke av kalleren.
//
// DET FINNES HELLER INGEN VEI TIL Å LUKKE `slaar_ikke_naiv_baseline`.
// Den lukkes av at modellen faktisk blir bedre — ikke av et klikk.
// ---------------------------------------------------------------------
export const settPrognosekrav = (krav, idem) =>
  _muter("/v1/prognose/krav", "POST", krav,
         idem || nyIdempotensnokkel());

export const registrerPrognosemodell = (modell, idem) =>
  _muter("/v1/prognose/modell", "POST", modell,
         idem || nyIdempotensnokkel());

export const avviklPrognosemodell = (modellId, kropp, idem) =>
  _muter(`/v1/prognose/modell/${encodeURIComponent(modellId)}`
         + "/avvikle",
         "POST", kropp, idem || nyIdempotensnokkel());

export const lagBemanningsprognose = (kropp, idem) =>
  _muter("/v1/prognose/prognose", "POST", kropp,
         idem || nyIdempotensnokkel());

export const registrerBemanningsmaaling = (prognoseId, kropp, idem) =>
  _muter(`/v1/prognose/prognose/${encodeURIComponent(prognoseId)}`
         + "/maaling",
         "POST", kropp, idem || nyIdempotensnokkel());

export const lukkPrognosefunn = (funnId, kropp, idem) =>
  _muter(`/v1/prognose/funn/${encodeURIComponent(funnId)}/lukk`,
         "POST", kropp, idem || nyIdempotensnokkel());


// ---------------------------------------------------------------------
// M-36 BEDRIFTSOPTIMALISATOR (132).
//
// DET FINNES INGEN `iverksettTiltak`, OG INGEN VEI MOT EN POLICY.
// `vurderTiltaksforslag` tar `vurdert` eller `avvist`, og der stopper
// modulen — utførelsen går gjennom modulen som EIER handlingen, på
// M-41s policykontrollerte vei.
//
// Vaktsetningen sier «kan aldri utvide egen fullmakt», og fraværet av
// en slik funksjon HER er en del av håndhevelsen: den andre delen er
// at modulrollen ikke har rettigheter på policytabellene.
//
// `settPortefoljestopp` VIRKER: med aktiv stopp nekter `lagRangering`.
// Det er det eneste modulen lovlig kan stanse.
// ---------------------------------------------------------------------
export const settOptimaliseringskrav = (krav, idem) =>
  _muter("/v1/optimalisator/krav", "POST", krav,
         idem || nyIdempotensnokkel());

export const registrerOptimaliseringsmodell = (modell, idem) =>
  _muter("/v1/optimalisator/modell", "POST", modell,
         idem || nyIdempotensnokkel());

export const avviklOptimaliseringsmodell = (modellId, kropp, idem) =>
  _muter(`/v1/optimalisator/modell/${encodeURIComponent(modellId)}`
         + "/avvikle",
         "POST", kropp, idem || nyIdempotensnokkel());

export const foreslaTiltak = (tiltak, idem) =>
  _muter("/v1/optimalisator/tiltak", "POST", tiltak,
         idem || nyIdempotensnokkel());

export const vurderTiltaksforslag = (tiltakId, kropp, idem) =>
  _muter(`/v1/optimalisator/tiltak/${encodeURIComponent(tiltakId)}`
         + "/vurder",
         "POST", kropp, idem || nyIdempotensnokkel());

export const settPortefoljestopp = (kropp, idem) =>
  _muter("/v1/optimalisator/stopp", "POST", kropp,
         idem || nyIdempotensnokkel());

export const opphevPortefoljestopp = (stoppId, kropp, idem) =>
  _muter(`/v1/optimalisator/stopp/${encodeURIComponent(stoppId)}`
         + "/opphev",
         "POST", kropp, idem || nyIdempotensnokkel());

export const lagRangering = (kropp, idem) =>
  _muter("/v1/optimalisator/rangering", "POST", kropp,
         idem || nyIdempotensnokkel());

export const registrerEffekt = (rangeringId, kropp, idem) =>
  _muter(`/v1/optimalisator/rangering/`
         + `${encodeURIComponent(rangeringId)}/effekt`,
         "POST", kropp, idem || nyIdempotensnokkel());

export const lukkOptimaliseringsfunn = (funnId, kropp, idem) =>
  _muter(`/v1/optimalisator/funn/${encodeURIComponent(funnId)}/lukk`,
         "POST", kropp, idem || nyIdempotensnokkel());


// ---------------------------------------------------------------------
// M-7 MØTEOPERASJONSAGENT (133).
//
// DET FINNES INGEN `fattBeslutning`. `registrerBeslutning` KREVER
// `besluttet_av`: modulen skriver ned beslutningen menneskene tok, den
// fatter den ikke.
//
// `startOpptak` ER DEN ENESTE HANDLINGEN I MODULEN SOM IKKE KAN GJØRES
// UGJORT. Døra nekter på fire ting FØR raden finnes — manglende
// hjemmel, utløpt hjemmel, ingen varslet, og varsling som kom etter at
// opptaket startet. ET NEKT SOM KOMMER ETTER MIKROFONEN ER IKKE ET
// NEKT.
//
// `registrerReferatpunkt` tar IKKE terskelen: døra leser den fra
// tenantens krav. En kaller som fikk sette sin egen kunne satt den til
// 1 og fått alt bekreftet.
// ---------------------------------------------------------------------
export const settMotekrav = (krav, idem) =>
  _muter("/v1/mote/krav", "POST", krav,
         idem || nyIdempotensnokkel());

export const registrerOpptakshjemmel = (hjemmel, idem) =>
  _muter("/v1/mote/hjemmel", "POST", hjemmel,
         idem || nyIdempotensnokkel());

export const avsluttOpptakshjemmel = (hjemmelId, kropp, idem) =>
  _muter(`/v1/mote/hjemmel/${encodeURIComponent(hjemmelId)}/avslutt`,
         "POST", kropp, idem || nyIdempotensnokkel());

export const registrerMote = (mote, idem) =>
  _muter("/v1/mote/mote", "POST", mote,
         idem || nyIdempotensnokkel());

export const startOpptak = (moteId, kropp, idem) =>
  _muter(`/v1/mote/${encodeURIComponent(moteId)}/opptak`,
         "POST", kropp, idem || nyIdempotensnokkel());

export const registrerReferatpunkt = (moteId, kropp, idem) =>
  _muter(`/v1/mote/${encodeURIComponent(moteId)}/referatpunkt`,
         "POST", kropp, idem || nyIdempotensnokkel());

export const registrerBeslutning = (moteId, kropp, idem) =>
  _muter(`/v1/mote/${encodeURIComponent(moteId)}/beslutning`,
         "POST", kropp, idem || nyIdempotensnokkel());

export const registrerMoteaksjon = (moteId, kropp, idem) =>
  _muter(`/v1/mote/${encodeURIComponent(moteId)}/aksjon`,
         "POST", kropp, idem || nyIdempotensnokkel());

export const lukkMoteaksjon = (aksjonId, kropp, idem) =>
  _muter(`/v1/mote/aksjon/${encodeURIComponent(aksjonId)}/lukk`,
         "POST", kropp, idem || nyIdempotensnokkel());

export const lukkMotefunn = (funnId, kropp, idem) =>
  _muter(`/v1/mote/funn/${encodeURIComponent(funnId)}/lukk`,
         "POST", kropp, idem || nyIdempotensnokkel());
