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
