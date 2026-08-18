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


def _verifisert_hostname(conn, tenant: str, hostname: str) -> bool:
    """Positivt autorisert mål VED OPPRETTELSE (portene 9–10): en
    `verifisert`, ikke-utløpt domenekontroll for nøyaktig dette hostnamet.
    Wildcard teller ikke her — bestillingen gjelder ett konkret mål."""
    return conn.execute(
        "SELECT 1 FROM domenekontroll WHERE tenant=%s AND hostname=%s"
        " AND status='verifisert'"
        " AND (utloper IS NULL OR utloper > now())",
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
        except ValueError:
            return _feilsvar("request_feilformet", rid)
        try:
            norm = normaliser(tenant, data)
        except Bestillingsfeil as f:
            tjeneste.logg.hendelse(f.kode, rid, tenant, art="sikkerhet",
                                   flate="bestilling")
            return _feilsvar(f.kode, rid)
        bt = BESTILLINGSTYPER[norm["bestillingstype"]]
        hostname = norm["mal_url"].split("://", 1)[1].split("/", 1)[0]

        from db.pg import sett_kontekst
        sett_kontekst(conn, tenant, f"bruker:{bid}", rid)
        if not _verifisert_hostname(conn, tenant, hostname):
            conn.rollback()
            tjeneste.logg.hendelse("bestilling_hostname_uverifisert", rid,
                                   tenant, art="sikkerhet",
                                   hostname=hostname)
            return _feilsvar("bestilling_hostname_uverifisert", rid)

        hash_ = intensjonshash(norm)
        raa_nokkel = request.headers.get("idempotency-key")
        nokkel = raa_nokkel.strip() if raa_nokkel and raa_nokkel.strip() \
            else None
        if nokkel:
            rad = conn.execute(
                "SELECT intensjonshash, oppdrag_id, beslutning FROM"
                " bestilling_idempotens WHERE tenant=%s AND"
                " idempotensnokkel=%s", (tenant, nokkel)).fetchone()
            if rad is not None:
                conn.rollback()
                if rad[0] != hash_:
                    # 21b/c: ULIK intensjon gjenbruker ALDRI et resultat —
                    # og tar ingen ny beslutning. Kvoten er urørt.
                    return _feilsvar("idempotenskonflikt", rid)
                return kanonisk_json(
                    {"beslutning": rad[2], "oppdrag_id": rad[1],
                     "request_id": rid}, 200,
                    {"x-request-id": rid, "idempotent-replay": "1"})
        conn.rollback()

        # Tenantens AKTIVE policy er beslutningsgrunnlaget — bestilleren
        # velger aldri policy (eller modul, frist, epoch — feltene finnes
        # ikke i kroppen, port 16).
        sett_kontekst(conn, tenant, f"bruker:{bid}", rid)
        prad = conn.execute(
            "SELECT policy_id FROM policyer WHERE tenant=%s AND aktiv",
            (tenant,)).fetchall()
        conn.rollback()
        if len(prad) != 1:
            return _feilsvar("policy_ukjent", rid)

        kjernenokkel = ("bestilling:" + nokkel) if nokkel \
            else "bestilling-eng:" + secrets.token_hex(16)
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
                conn, ctx, policy_id=prad[0][0], event=event,
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
        if nokkel:
            # Raden skrives i transaksjonen som FULLFØRER bestillingen, og
            # dekker ALLE utfall (også stopp/brudd — gjenspill etter
            # timeout gir samme kode uten å brenne kvote; kvotevernet for
            # selve beslutningen bæres av kjernens egen idempotensnøkkel,
            # deterministisk avledet av bestillingsnøkkelen).
            conn.execute(
                "INSERT INTO bestilling_idempotens (tenant,"
                " idempotensnokkel, intensjonshash, oppdrag_id, beslutning)"
                " VALUES (%s,%s,%s,%s,%s)"
                " ON CONFLICT (tenant, idempotensnokkel) DO NOTHING",
                (tenant, nokkel, hash_, oppdrag_id, utfall))
        conn.commit()

        kropp = {"beslutning": utfall, "oppdrag_id": oppdrag_id,
                 "request_id": rid}
        if utfall != "tillat":
            kropp["begrunnelse"] = svar.kropp.get("begrunnelse") or []
        if svar.unntak_id is not None:
            kropp["unntak_id"] = svar.unntak_id
        return kanonisk_json(kropp, 200, {"x-request-id": rid})
    finally:
        tjeneste.pool.gi_tilbake(conn)
