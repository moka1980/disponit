"""Bestillingsveien (014c v4 §6): kundeflaten bestiller en kontroll, og
veien videre er `kjerne.behandle()` — aldri en snarvei.

Endepunktet er EN NY PRODUSENT inn i beslutningsveien: bestillingen blir
en policy-evaluering (frekvensgrensen håndheves KUN i motorens betrodde
teller — endepunktet teller aldri selv), TILLAT blir et
beslutningsoppdrag i outboxen (038), STOPP blir en strukturert kode til
flaten, BRUDD går policyens vei til unntakskøen.

LUKKET KROPP, og klienten sender aldri en URL: serveren komponerer
`mal_url` av `hostname` + normalisert `sti`, så klassen «URL med query,
fragment, credentials eller port» ikke kan uttrykkes. Hostnamet må være
`verifisert` for tenanten VED OPPRETTELSE — det er ikke policyens ansvar.

Idempotensen binder HELE den normaliserte intensjonen (hash over den
serverkomponerte formen): samme nøkkel + samme hash → samme resultat
(også STOPP/BRUDD — et gjenspill etter timeout brenner aldri kvote);
samme nøkkel + annen hash → 409 uten at noen beslutning tas. Kjernens
egen idempotensnøkkel avledes deterministisk av bestillingsnøkkelen, så
en retry som mistet svaret replayer BESLUTNINGEN i kjernen (ingen ny
frekvensreservasjon) før oppdraget sikres idempotent
(`oppdrag_en_per_beslutning`, 008).
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg
from starlette.requests import Request
from starlette.responses import Response

#: Lukket kropp — nøyaktig disse feltene, aldri en URL.
SKJEMAFELT = frozenset({"bestillingstype", "hostname", "sti", "kravsett",
                        "omfang", "maks_sider"})
#: Den normaliserte intensjonen hashen bygges fra. MEKANISK avledet av
#: skjemaet: hostname+sti kollapser til den serverkomponerte `mal_url`,
#: resten følger med som de er (normalisert). Den statiske porten 21f
#: krysser de to mengdene, så et fremtidig semantisk felt ikke kan falle
#: utenfor hashen i stillhet.
INTENSJONSFELT = ("tenant", "bestillingstype", "mal_url", "kravsett",
                  "omfang", "maks_sider")
#: Hvilke skjemafelt hvert intensjonsfelt dekker (for den statiske porten).
FELTDEKNING = {"bestillingstype": ("bestillingstype",),
               "mal_url": ("hostname", "sti"),
               "kravsett": ("kravsett",), "omfang": ("omfang",),
               "maks_sider": ("maks_sider",), "tenant": ()}

#: Lengdegrensene for idempotensnøkkelen. Speiler CHECKen på
#: `bestilling_idempotens.idempotensnokkel` (038 §3), og de to holdes
#: sammen av `test_idempotensnokkelens_grenser_speiler_lagringen`: går de
#: fra hverandre, er det igjen mulig å ta en beslutning som ikke kan
#: bokføres.
IDEMPOTENSNOKKEL_MIN = 8
IDEMPOTENSNOKKEL_MAKS = 200


@dataclass(frozen=True)
class Bestillingstype:
    handling: str
    oppdragstype: str
    eiermodul: str
    kravsett: tuple[str, ...]
    omfang: tuple[str, ...]


#: Kodefestet og lukket (port 13); deploy-porten krysser mot
#: `oppdragstype_register` (port 14) i deployport-modultyper.py.
#:
#: FRISTEN STÅR IKKE HER (Codex P1). Første utgave bar sin egen
#: `frister_s`-tabell med 90 min for `nettsted`, og den var både en
#: DUPLIKAT og feil: `oppdragskontrakt.UTFORELSESFRIST_VALG` deklarerer
#: 30/60 min, WCAG-manifestet lover det samme, og 90-minutterstallet var
#: nettopp det som ble NEDJUSTERT fordi stacken ikke kan holde det —
#: claimets eier-lease (037) og opplastingskapabiliteten (017) klemmes
#: begge til 3600 s, og ingen av dem har en fornyelsesvei. En kontroll
#: som lovlig brukte 90 min mistet dermed opplastingstokenet OG leasen
#: sin mens det nye 90-minutters utførelsesvinduet fortsatt sto åpent:
#: en annen controller kunne reclaime det samme oppdraget og starte
#: duplisert trafikk mot kundens nettsted, før den første uansett feilet
#: på opplastingen med hele jobben gjort.
#:
#: Fristen hører til KONTRAKTEN, og det er dét som gjør den til én frist:
#: samme tabell som arbeideren (`m37.arbeider._opprett_oppdrag`) og
#: controlleren leser. Et nytt omfang kan da ikke få en frist her som
#: ingen annen del av stacken kjenner.
BESTILLINGSTYPER: dict[str, Bestillingstype] = {
    "kontroll.wcag.nettsted": Bestillingstype(
        handling="kontroll.wcag.nettsted",
        oppdragstype="kontroll.wcag.nettsted",
        eiermodul="m_wcag_audit",
        kravsett=("wcag21_aa",),
        omfang=("enkeltside", "nettsted")),
}

_HOSTNAME = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
    r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
#: Stien: absolutt, allerede normalisert. `..`, prosentkoding, query,
#: fragment, doble skråstreker og rare tegn AVVISES — vi normaliserer
#: ikke i det stille, for da ville to skrivemåter av samme intensjon
#: passert med hver sin hash.
_STI = re.compile(r"^/(?:[A-Za-z0-9._~-]+/?)*$")


class Bestillingsfeil(Exception):
    def __init__(self, kode: str, http: int = 400):
        self.kode, self.http = kode, http


def normaliser(tenant: str, data: dict) -> dict:
    """Lukket kropp → normalisert intensjon. Kaster Bestillingsfeil."""
    if not isinstance(data, dict) or set(data) - SKJEMAFELT:
        raise Bestillingsfeil("request_feilformet")
    bt = BESTILLINGSTYPER.get(data.get("bestillingstype"))
    if bt is None:
        raise Bestillingsfeil("request_feilformet")
    host = data.get("hostname")
    if not isinstance(host, str) or "@" in host or ":" in host \
            or "/" in host or not _HOSTNAME.fullmatch(host.lower()) \
            or host != host.lower():
        # A-label, små bokstaver, aldri credentials/port/sti i feltet.
        raise Bestillingsfeil("request_feilformet")
    sti = data.get("sti")
    if sti is None:
        sti = "/"
    if not isinstance(sti, str) or ".." in sti or "//" in sti \
            or not _STI.fullmatch(sti):
        raise Bestillingsfeil("request_feilformet")
    if data.get("kravsett") not in bt.kravsett:
        raise Bestillingsfeil("request_feilformet")
    omfang = data.get("omfang")
    if omfang not in bt.omfang:
        raise Bestillingsfeil("request_feilformet")
    maks = data.get("maks_sider")
    if maks is None:
        maks = 1 if omfang == "enkeltside" else 50
    if not isinstance(maks, int) or isinstance(maks, bool) \
            or not 1 <= maks <= 50:
        raise Bestillingsfeil("request_feilformet")
    return {"tenant": tenant, "bestillingstype": data["bestillingstype"],
            "mal_url": f"https://{host}{sti}",
            "kravsett": data["kravsett"], "omfang": omfang,
            "maks_sider": maks}


def intensjonshash(normalisert: dict) -> str:
    """SHA-256(JCS(normalisert intensjon)) — ETTER normalisering, aldri på
    rå request: to ekvivalente skrivemåter er samme intensjon."""
    from policy_validator import jcs
    intensjon = {k: normalisert[k] for k in INTENSJONSFELT}
    return hashlib.sha256(jcs.kanoniske_bytes(intensjon)).hexdigest()


#: Gyldighetspredikatet, ORDRETT fra `v_domeneautorisasjon.gyldig`
#: (016 §6). Egress-autoriteten slipper bare gjennom et domene som ER
#: dette, og bestillingsporten må stille samme spørsmål: en bestilling
#: mot et mål egress vil avvise, brenner kvote og legger seg i køen for
#: å dø der ute. Den utelatte biten var ferskheten — et domene kan stå
#: `verifisert` med uutløpt `utloper` i månedsvis etter at den daglige
#: revalideringen sluttet å lykkes, og nettopp det er tilfellet porten
#: finnes for. `test_domenepredikatet_speiler_visningen` krysser de to
#: tekstene mekanisk, så de ikke kan gli fra hverandre i stillhet.
DOMENE_GYLDIG_SQL = (
    "status = 'verifisert' AND now() < utloper"
    " AND siste_vellykkede_revalidering > now() - interval '72 hours'")


def _verifisert_hostname(conn, tenant: str, hostname: str) -> bool:
    """Positivt autorisert mål VED OPPRETTELSE (portene 9–10): en
    `verifisert`, ikke-utløpt, FERSK revalidert domenekontroll for
    nøyaktig dette hostnamet. Wildcard teller ikke her — bestillingen
    gjelder ett konkret mål.

    NULL-veiene er fail-closed, som i visningen: mangler `utloper` eller
    `siste_vellykkede_revalidering`, er raden ikke et bevis på noe."""
    return conn.execute(
        "SELECT 1 FROM domenekontroll WHERE tenant=%s AND hostname=%s"
        " AND (" + DOMENE_GYLDIG_SQL + ")",
        (tenant, hostname)).fetchone() is not None


def bestill_endepunkt(tjeneste, request: Request) -> Response:
    from . import kjerne
    from .app import _feilsvar, _rid, kanonisk_json
    from .policyadmin_http import _Avbrudd, _browserkontekst
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        return _feilsvar("db_utilgjengelig", rid)
    try:
        try:
            tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                           "bestilling:opprett")
        except _Avbrudd as a:
            return a.respons
        raa = request.scope.get("state", {}).get("kropp", b"")
        try:
            data = json.loads(raa.decode("utf-8"))
        except (ValueError, RecursionError):
            # `json.loads` er REKURSIV (Codex P2). Et syntaktisk gyldig,
            # dypt nøstet dokument på noen få kilobyte ligger godt under
            # kroppsgrensen og treffer likevel rekursjonsgrensen —
            # RecursionError er en RuntimeError, ikke en ValueError, så
            # `except ValueError` alene slapp klientinput ut som generisk
            # 500 i stedet for det dokumenterte `request_feilformet`.
            # Dybde ER klientinput; de andre JSON-endepunktene (035,
            # onboarding, artefakt) fanger den allerede, og denne
            # parseren skal ikke være unntaket.
            return _feilsvar("request_feilformet", rid)
        try:
            norm = normaliser(tenant, data)
        except Bestillingsfeil as f:
            tjeneste.logg.hendelse(f.kode, rid, tenant, art="sikkerhet",
                                   flate="bestilling")
            return _feilsvar(f.kode, rid)
        bt = BESTILLINGSTYPER[norm["bestillingstype"]]
        hostname = norm["mal_url"].split("://", 1)[1].split("/", 1)[0]

        hash_ = intensjonshash(norm)
        raa_nokkel = request.headers.get("idempotency-key")
        nokkel = raa_nokkel.strip() if raa_nokkel and raa_nokkel.strip() \
            else None
        # LENGDEN VALIDERES FØR BESLUTNINGEN (Codex P2). Lagringen krever
        # 8–200 tegn, men enhver ikke-blank verdi slapp gjennom hit. Med en
        # for kort eller for lang nøkkel COMMITTET `kjerne.behandle`
        # beslutningen først, og CHECKen slo til på innsettingen etterpå:
        # outbox-transaksjonen rullet tilbake, klienten fikk en 500, og
        # HVER retry gjentok nøyaktig det samme — en committet beslutning
        # (frekvenskvote brent) uten en bestilling som noen gang kan
        # fullføres. En nøkkel vi ikke kan lagre må avvises før den kan
        # utløse noe.
        if nokkel is not None and not (
                IDEMPOTENSNOKKEL_MIN <= len(nokkel) <= IDEMPOTENSNOKKEL_MAKS):
            tjeneste.logg.hendelse("request_feilformet", rid, tenant,
                                   art="sikkerhet", flate="bestilling",
                                   grunn="idempotensnokkel_lengde")
            return _feilsvar("request_feilformet", rid)

        from db.pg import sett_kontekst
        sett_kontekst(conn, tenant, f"bruker:{bid}", rid)
        # GJENSPILL FØR HOSTNAME-PORTEN (Codex P2). Porten er en
        # OPPRETTELSES-regel: målet må være positivt autorisert i det
        # bestillingen tas imot. Et gjenspill oppretter ingenting — det
        # leverer et resultat som alt er committet. Sto porten først,
        # ville en bestilling som ble utført, men mistet svaret sitt, fått
        # `bestilling_hostname_uverifisert` på retryen om
        # domeneverifikasjonen i mellomtiden utløp eller ble trukket —
        # samme nøkkel og samme intensjon, nytt svar. Modulens egen
        # kontrakt sier at samme nøkkel + samme hash gir samme resultat,
        # og en verifisering som utløper etterpå gjør ikke det som skjedde
        # ugjort.
        if nokkel:
            rad = conn.execute(
                "SELECT intensjonshash, svarkropp, beslutning, oppdrag_id"
                " FROM bestilling_idempotens WHERE tenant=%s AND"
                " idempotensnokkel=%s", (tenant, nokkel)).fetchone()
            if rad is not None:
                conn.rollback()
                if rad[0] != hash_:
                    # 21b/c: ULIK intensjon gjenbruker ALDRI et resultat —
                    # og tar ingen ny beslutning. Kvoten er urørt.
                    return _feilsvar("idempotenskonflikt", rid)
                # HELE det opprinnelige svaret, ikke en redusert form:
                # `begrunnelse` for STOPP/BRUDD og `unntak_id` for BRUDD
                # er nettopp det klienten mistet da svaret forsvant.
                # Tom kropp finnes bare på rader skrevet FØR kolonnen
                # (en base midt i 038); de får den gamle formen, som er
                # alt som noen gang ble lagret om dem.
                lagret = rad[1] or {"beslutning": rad[2],
                                    "oppdrag_id": rad[3]}
                return kanonisk_json(
                    {**lagret, "request_id": rid}, 200,
                    {"x-request-id": rid, "idempotent-replay": "1"})
        if not _verifisert_hostname(conn, tenant, hostname):
            conn.rollback()
            tjeneste.logg.hendelse("bestilling_hostname_uverifisert", rid,
                                   tenant, art="sikkerhet",
                                   hostname=hostname)
            return _feilsvar("bestilling_hostname_uverifisert", rid)
        conn.rollback()

        kjernenokkel = ("bestilling:" + nokkel) if nokkel \
            else "bestilling-eng:" + secrets.token_hex(16)
        # Tenantens AKTIVE policy er beslutningsgrunnlaget — bestilleren
        # velger aldri policy (eller modul, frist, epoch — feltene finnes
        # ikke i kroppen, port 16).
        #
        # GJENOPPRETTING FØLGER BESLUTNINGEN, IKKE KLOKKA (Codex P2).
        # Dør prosessen etter at `kjerne.behandle` har committet, men før
        # oppdraget og `bestilling_idempotens` er skrevet, er kjernens
        # egen idempotensrad det eneste sporet av forsøket. Kjernens
        # input-hash dekker policy-id-en, så en retry etter at tenanten
        # har byttet til en ANNEN policy ville regnet ut en ny hash og
        # fått `idempotenskonflikt` — for alltid, og med en committet
        # TILLAT-beslutning stående uten oppdraget sitt. Har nøkkelen alt
        # en beslutning, gjenoppretter vi derfor med DEN policyen: da
        # treffer hashen, kjernen gjenspiller sitt eget svar, og halen
        # under fullfører bestillingen. Loggposten er den samme raden
        # halen leser oppdraget fra, og dens `policy_id` er en
        # policyREFERANSE (`<id>@<versjon>/<handling>`) — ren id hentes ut
        # med `les_policyref`, som i unntaksveien.
        sett_kontekst(conn, tenant, f"bruker:{bid}", rid)
        policy_id = None
        if nokkel:
            from policy_validator.engine import les_policyref
            forrige = conn.execute(
                "SELECT policy_id FROM revisjonslogg WHERE tenant=%s AND"
                " idempotency_key=%s ORDER BY id DESC LIMIT 1",
                (tenant, kjernenokkel)).fetchone()
            ref = les_policyref(forrige[0]) if forrige else None
            if ref is not None:
                policy_id = ref[0]
        if policy_id is None:
            prad = conn.execute(
                "SELECT policy_id FROM policyer WHERE tenant=%s AND aktiv",
                (tenant,)).fetchall()
            if len(prad) != 1:
                conn.rollback()
                return _feilsvar("policy_ukjent", rid)
            policy_id = prad[0][0]
        conn.rollback()

        # `ressurs_id` er MALBINDINGSFELT: `malbindingsbrudd` krever at den
        # ER det normaliserte vertsnavnet fra `mal_url` — ikke URL-en.
        event = {"handling": bt.handling, "ressurs_id": hostname,
                 "mal_url": norm["mal_url"], "kravsett": norm["kravsett"],
                 "omfang": norm["omfang"],
                 "maks_sider": norm["maks_sider"],
                 "dataklasser": ["offentlig"],
                 "dataklasser_kilde": "connector"}
        from policy_validator.engine import EvaluationContext
        ctx = EvaluationContext(
            tenant_id=tenant, aktor_rolle="bestiller", autentisert=True,
            kilde="api_token")
        try:
            svar = kjerne.behandle(
                conn, ctx, policy_id=policy_id, event=event,
                idempotency_key=kjernenokkel, request_id=rid,
                aktor=f"bruker:{bid}", nokler=tjeneste.nokler)
        except kjerne.Feilsvar as f:
            tjeneste.logg.hendelse(f.kode, rid, tenant, art="sikkerhet")
            return _feilsvar(f.kode, rid)

        beslutning = str(svar.kropp.get("beslutning") or "").upper()
        utfall = {"TILLAT": "tillat", "STOPP": "stopp"}.get(
            beslutning, "brudd")
        oppdrag_id = None
        sett_kontekst(conn, tenant, f"bruker:{bid}", rid)
        if utfall == "tillat":
            logg = conn.execute(
                "SELECT id FROM revisjonslogg WHERE tenant=%s AND"
                " idempotency_key=%s ORDER BY id DESC LIMIT 1",
                (tenant, kjernenokkel)).fetchone()
            if logg is None:
                conn.rollback()
                return _feilsvar("logging_feilet", rid)
            from db import kryptering
            key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn,
                                                                  tenant)
            payload = {k: norm[k] for k in ("mal_url", "kravsett", "omfang",
                                            "maks_sider")}
            ct, nonce = kryptering.krypter(dek, payload, tenant, key_id)
            # Typens EGEN frist, fra kontrakten — samme oppslag og samme
            # tabell som arbeiderveien bruker (Codex P1). `payload` er
            # nøyaktig den minimerte formen `utforelsesfrist_s` velger på.
            import oppdragskontrakt
            frist_s = oppdragskontrakt.utforelsesfrist_s(bt.oppdragstype,
                                                         payload)
            if frist_s is None:
                # Uoppnåelig så lenge den statiske porten under står (se
                # `test_bestillingstyper_arver_kontraktens_frist`), og
                # bevisst en 500 og ikke en stille reservefrist: en type
                # uten deklarert frist ville arvet den generiske
                # 24-timersfristen, og for en `ekstern_lesing`-kanal mot
                # kundens nettsted er det ikke en romslig frist — det er
                # et døgnlangt vindu bestillingen aldri ba om.
                conn.rollback()
                tjeneste.logg.hendelse("intern_feil", rid, tenant,
                                       art="drift",
                                       grunn="utforelsesfrist_mangler",
                                       oppdragstype=bt.oppdragstype)
                return _feilsvar("intern_feil", rid)
            naa = datetime.now(timezone.utc)
            try:
                oppdrag_id = int(conn.execute(
                    "SELECT opprett_beslutningsoppdrag(%s,%s,%s,%s,%s,"
                    "%s,%s,%s,%s,%s)",
                    (tenant, logg[0], bt.oppdragstype, bt.handling,
                     bt.eiermodul, ct, key_id, nonce,
                     naa + timedelta(seconds=frist_s),
                     naa + timedelta(seconds=frist_s))).fetchone()[0])
            except psycopg.errors.UniqueViolation:
                # `oppdrag_en_per_beslutning` (008): en retry som mistet
                # svaret ETTER at oppdraget ble skrevet — vinnerens rad er
                # svaret, aldri et oppdrag nummer to (21/21-lik).
                conn.rollback()
                sett_kontekst(conn, tenant, f"bruker:{bid}", rid)
                oppdrag_id = int(conn.execute(
                    "SELECT id FROM oppdrag WHERE tenant=%s AND"
                    " beslutning_loggpost_id=%s", (tenant,
                                                   logg[0])).fetchone()[0])
        # Svaret bygges FØR bokføringen, så det som lagres er nøyaktig det
        # som sendes — ikke en andre konstruksjon av den samme formen.
        # `request_id` er UTENFOR kroppen: det er forespørselens, ikke
        # bestillingens, og legges på begge veier ut (her og i gjenspillet).
        kropp = {"beslutning": utfall, "oppdrag_id": oppdrag_id}
        if utfall != "tillat":
            kropp["begrunnelse"] = svar.kropp.get("begrunnelse") or []
        if svar.unntak_id is not None:
            kropp["unntak_id"] = svar.unntak_id
        if nokkel:
            # Raden skrives i transaksjonen som FULLFØRER bestillingen, og
            # dekker ALLE utfall (også stopp/brudd — gjenspill etter
            # timeout gir samme kode uten å brenne kvote; kvotevernet for
            # selve beslutningen bæres av kjernens egen idempotensnøkkel,
            # deterministisk avledet av bestillingsnøkkelen).
            conn.execute(
                "INSERT INTO bestilling_idempotens (tenant,"
                " idempotensnokkel, intensjonshash, oppdrag_id, beslutning,"
                " svarkropp) VALUES (%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (tenant, idempotensnokkel) DO NOTHING",
                (tenant, nokkel, hash_, oppdrag_id, utfall,
                 json.dumps(kropp, ensure_ascii=False)))
        conn.commit()
        return kanonisk_json({**kropp, "request_id": rid}, 200,
                             {"x-request-id": rid})
    finally:
        tjeneste.pool.gi_tilbake(conn)
