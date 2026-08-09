"""PR-012 — porten for menneskelig unntaksbehandling.

Mennesket avgir en MAC-signert konvolutt som mates som en NY beslutning
gjennom motoren — ALDRI en statusflipp. Dette er portens side av
arbeidsdelingen (v7 §2): den MAC-verifiserer konvolutten, beviser at feltene
matcher den LÅSTE saksraden, håndterer runde-livssyklusen og idempotensen, og
lar motoren avgjøre om godkjenningen faktisk gir TILLAT. `belop_maks`,
`(grunnkode, handling)`-medlemskap og `krever_rolle` eies av motoren.

`behandle_unntakshandling` speiler `_flyt` inline i én transaksjon (kalleren
eier commit) fordi `kjerne.behandle` eier sin egen commit og ikke kan nestes.
Sikkerhetsevidens ved brudd rutes på EGEN forbindelse etter at
forretningstransaksjonen er rullet tilbake (V3) — ellers vranglåser
evidens-INSERT-en mot FOR UPDATE-låsen forretnings-tx holder.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import psycopg

from db import kryptering
from db.pg import Evidens, sett_kontekst, sikker_beslutning_pg
from policy_validator.engine import (STOPP, TILLAT, UNNTAK, EvaluationContext,
                                     MenneskeligGodkjenning, parse_belop)
from policy_validator.schema import IKKE_MENNESKELIG_GODKJENNBARE

from . import policyregister
from .mac_register import kanonisk_konvolutt

#: Hvor lenge en åpen runde lever før den utløper (fire-øyne innen én
#: arbeidsøkt). Utløp lukker runden; attestasjoner slettes aldri (v3 §5).
RUNDE_TTL = timedelta(hours=24)


class Godkjenningsfeil(Exception):
    """En avvist unntakshandling med en semantisk kode. CP5-endepunktet
    oversetter koden til HTTP; her holdes porten fri for HTTP-detaljer."""

    def __init__(self, kode: str, detalj: str = "") -> None:
        super().__init__(kode if not detalj else f"{kode}: {detalj}")
        self.kode = kode
        self.detalj = detalj


def skriv_sikkerhetsevidens(pool, *, tenant: str, unntak_id: int,
                            hendelse: str, detalj: dict, aktor: str,
                            request_id: str) -> None:
    """Skriv sikkerhetsevidens på EGEN forbindelse med egen commit (V3).

    Poenget er overlevelse: et MAC-/bindingsbrudd skal rulle
    forretningstransaksjonen tilbake OG etterlate et spor. En stderr-linje
    (dagens `Sikkerhetslogg`) overlever fordi den ikke er transaksjonell;
    denne raden overlever fordi den skrives og committes på en annen
    forbindelse enn den som rulles.

    🔴 MÅ kalles ETTER at forretnings-`conn` er rullet tilbake. Skrives den
    før, tar dens INSERT en FOR KEY SHARE-lås på nøyaktig den `unntak`-raden
    forretnings-tx-en holder FOR UPDATE på — og på én tråd blokkerer den
    fremmede forbindelsen da på en lås tråden selv eier: selvvranglås.
    """
    conn = pool.hent()
    try:
        sett_kontekst(conn, tenant, aktor, request_id)
        conn.execute(
            "INSERT INTO unntak_historikk (tenant, unntak_id, hendelse, aktor,"
            " request_id, detalj) VALUES (%s,%s,%s,%s,%s,%s)",
            (tenant, unntak_id, hendelse, aktor, request_id,
             json.dumps(detalj, ensure_ascii=False)))
        conn.commit()
    finally:
        pool.gi_tilbake(conn)


def _siste_grunnkode(begrunnelse: object) -> str | None:
    """Den BLOKKERENDE grunnkoden er den SISTE i begrunnelseskjeden — motorens
    `blokker()` legger den alltid sist; alt foran er `*_ok`-kvitteringer."""
    if not isinstance(begrunnelse, list) or not begrunnelse:
        return None
    siste = begrunnelse[-1]
    kode = siste.get("kode") if isinstance(siste, dict) else None
    return kode if isinstance(kode, str) else None


def _er_godkjennbar(policy: object, grunnkode: str, handling: str) -> bool:
    """(grunnkode, handling) ∈ policyens `godkjennbare` OG ikke i deny-lista.
    Deny-lista er alt håndhevet ved policy-last, men porten stoler ikke blindt
    (defense-in-depth)."""
    if grunnkode in IKKE_MENNESKELIG_GODKJENNBARE:
        return False
    if not isinstance(policy, dict):
        return False
    mo = policy.get("menneskelig_overstyring")
    if not isinstance(mo, dict):
        return False
    return any(isinstance(e, dict) and e.get("grunnkode") == grunnkode
               and e.get("handling") == handling
               for e in mo.get("godkjennbare") or [])


def opprett_godkjenningsrunde(conn: psycopg.Connection, *, tenant: str,
                              unntak_id: int, aktor: str, request_id: str,
                              policy: dict, policy_hash: str, naa) -> int:
    """Åpne en godkjenningsrunde for en manuell, menneskelig godkjennbar sak.

    Server-utleder `bundet_grunnkode` fra sakens begrunnelseskjede (aldri
    klientvalgt), åpner en `apen` runde med den aktive policyens hash frosset
    per runde, og gjør den kontrollerte overgangen `manuell →
    venter_godkjenning`. Returnerer rundenummeret. Kaster `Godkjenningsfeil`.
    Kalleren eier transaksjonen.
    """
    sett_kontekst(conn, tenant, aktor, request_id)
    rad = conn.execute(
        "SELECT status, handling, loggpost_id, intensjon_pakrevd FROM unntak"
        " WHERE tenant=%s AND id=%s FOR UPDATE", (tenant, unntak_id)).fetchone()
    if rad is None:
        raise Godkjenningsfeil("unntak_ukjent")
    status, handling, loggpost_id, intensjon_pakrevd = rad
    if status != "manuell":
        raise Godkjenningsfeil("runde_ulovlig_tilstand", f"status={status}")
    # Godkjenn krever en komplett handlingsintensjon (v2 §1) — ellers kan
    # motoren ikke re-evaluere, og godkjenn er ikke en mulig handling.
    if not intensjon_pakrevd:
        raise Godkjenningsfeil("godkjenn_utilgjengelig", "ingen intensjon")

    lp = conn.execute(
        "SELECT begrunnelse FROM revisjonslogg WHERE tenant=%s AND id=%s",
        (tenant, loggpost_id)).fetchone()
    bundet = _siste_grunnkode(lp[0] if lp else None)
    if bundet is None:
        raise Godkjenningsfeil("godkjenn_utilgjengelig", "ingen blokkerende grunn")
    if not _er_godkjennbar(policy, bundet, handling):
        raise Godkjenningsfeil("godkjenn_utilgjengelig", f"grunnkode={bundet}")

    meta = policy.get("meta") if isinstance(policy.get("meta"), dict) else {}
    policy_versjon = meta.get("versjon") or "?"
    runde = int(conn.execute(
        "SELECT coalesce(max(runde),0)+1 FROM godkjenningsrunde"
        " WHERE tenant=%s AND unntak_id=%s", (tenant, unntak_id)).fetchone()[0])
    # Runden FØRST: den kontrollerte overgangen under krever en apen runde
    # (unntak_kolonnelaas EXISTS-sjekk), og den må være synlig i samme tx.
    try:
        conn.execute(
            "INSERT INTO godkjenningsrunde (tenant, unntak_id, runde, status,"
            " bundet_grunnkode, godkjennings_policy_hash, policy_versjon,"
            " utloper) VALUES (%s,%s,%s,'apen',%s,%s,%s,%s)",
            (tenant, unntak_id, runde, bundet, policy_hash, policy_versjon,
             naa + RUNDE_TTL))
    except psycopg.errors.UniqueViolation:
        # En aktiv runde finnes allerede (delindeks en_aktiv_runde).
        raise Godkjenningsfeil("runde_allerede_aapen") from None
    conn.execute(
        "UPDATE unntak SET status='venter_godkjenning' WHERE tenant=%s AND id=%s",
        (tenant, unntak_id))
    return runde


# --------------------------------------------------------------------------
# Hovedveien: en MAC-signert menneskelig handling matet som en NY beslutning.
# --------------------------------------------------------------------------

_BINDINGSFELT = ("tenant", "target_action", "hi_integritet_hash",
                 "bundet_grunnkode", "godkjennings_policy_hash", "bruker_id")


def _motorutfall(beslutning: str) -> str:
    if beslutning == TILLAT:
        return "TILLAT_OUTBOX"
    if beslutning == UNNTAK:
        return "TIL_UNNTAK"
    return "STOPP"


def behandle_unntakshandling(conn: psycopg.Connection, pool, mac_register, *,
                             tenant: str, aktor: str, request_id: str,
                             konvolutt: dict, naa) -> dict:
    """Portens hovedvei. Eier transaksjonen (speiler `_flyt` inline siden
    `kjerne.behandle` eier sin egen commit).

    En MAC-signert konvolutt verifiseres, bindes til den LÅSTE saken, og —
    for `godkjenn` når fire-øyne-terskelen er nådd — mates som en NY beslutning
    gjennom motorens egne verifiserte faktakanal. MAC-/bindingsbrudd ruller
    transaksjonen tilbake OG skriver sikkerhetsevidens på egen forbindelse (V3).
    Kaster `Godkjenningsfeil` ved tilstands-/formfeil (kalleren mapper til HTTP).
    """
    operatorhandling = konvolutt.get("operatorhandling")
    if operatorhandling not in ("godkjenn", "avvis", "eskaler"):
        raise Godkjenningsfeil("ukjent_operatorhandling")
    unntak_id = konvolutt.get("unntak_id")
    mac, mac_key_id = konvolutt.get("mac"), konvolutt.get("mac_key_id")

    sett_kontekst(conn, tenant, aktor, request_id)
    sak = conn.execute(
        "SELECT status, handling, loggpost_id, intensjon_pakrevd,"
        " handlingsintensjon_kryptert, hi_key_id, hi_nonce, hi_integritet_hash,"
        " hi_skjemaversjon, intensjon_policy_hash, saksversjon"
        " FROM unntak WHERE tenant=%s AND id=%s FOR UPDATE",
        (tenant, unntak_id)).fetchone()
    if sak is None:
        conn.rollback()
        raise Godkjenningsfeil("unntak_ukjent")
    (status, handling, loggpost_id, intensjon_pakrevd, hi_ct, hi_key_id,
     hi_nonce, hi_hash, hi_ver, intensjon_policy_hash, saksversjon) = sak

    runde = conn.execute(
        "SELECT runde, status, bundet_grunnkode, godkjennings_policy_hash,"
        " utloper FROM godkjenningsrunde WHERE tenant=%s AND unntak_id=%s"
        " AND status IN ('apen','klar') FOR UPDATE",
        (tenant, unntak_id)).fetchone()
    if runde is None:
        conn.rollback()
        raise Godkjenningsfeil("ingen_aktiv_runde")
    r_nr, r_status, bundet_grunnkode, godkj_policy_hash, r_utloper = runde
    if r_utloper <= naa:
        conn.rollback()
        raise Godkjenningsfeil("runde_utlopt")
    if konvolutt.get("runde") != r_nr:
        conn.rollback()
        raise Godkjenningsfeil("feil_runde")

    # MAC — portens jobb, aldri motorens. Fail-closed.
    if not (isinstance(mac, str) and isinstance(mac_key_id, str)
            and mac_register.verifiser(konvolutt, mac, mac_key_id)):
        return _sikkerhetsstopp(conn, pool, tenant, unntak_id, aktor,
                                request_id, "mac_ugyldig")

    # Feltbinding: konvolutten må gjelde NØYAKTIG denne saken/runden. Et avvik
    # betyr at en gyldig-signert konvolutt forsøkes brukt på noe annet.
    forventet = {"tenant": tenant, "target_action": handling,
                 "hi_integritet_hash": hi_hash,
                 "bundet_grunnkode": bundet_grunnkode,
                 "godkjennings_policy_hash": godkj_policy_hash,
                 "bruker_id": aktor}
    for felt in _BINDINGSFELT:
        if konvolutt.get(felt) != forventet[felt]:
            return _sikkerhetsstopp(conn, pool, tenant, unntak_id, aktor,
                                    request_id, f"bindingsavvik:{felt}")

    konvolutt_hash = hashlib.sha256(kanonisk_konvolutt(konvolutt)).hexdigest()
    _skriv_attestasjon(conn, tenant, unntak_id, r_nr, operatorhandling,
                       handling, bundet_grunnkode, konvolutt, konvolutt_hash,
                       saksversjon)

    if operatorhandling == "avvis":
        conn.execute("UPDATE godkjenningsrunde SET status='kansellert'"
                     " WHERE tenant=%s AND unntak_id=%s AND runde=%s",
                     (tenant, unntak_id, r_nr))
        conn.execute("UPDATE unntak SET status='avvist' WHERE tenant=%s AND id=%s",
                     (tenant, unntak_id))
        _historikk(conn, tenant, unntak_id, "avvist_handling", aktor, request_id)
        conn.commit()
        return {"utfall": "avvist", "unntak_id": unntak_id}

    if operatorhandling == "eskaler":
        conn.execute("UPDATE godkjenningsrunde SET status='kansellert'"
                     " WHERE tenant=%s AND unntak_id=%s AND runde=%s",
                     (tenant, unntak_id, r_nr))
        # Saken tilbake til manuell for videre eskaleringsbehandling (målets
        # gyldighet håndteres i CP5-endepunktet der eskaleringslista finnes).
        if status != "manuell":
            conn.execute("UPDATE unntak SET status='manuell' WHERE tenant=%s"
                         " AND id=%s", (tenant, unntak_id))
        _historikk(conn, tenant, unntak_id, "eskalert", aktor, request_id)
        conn.commit()
        return {"utfall": "eskalert", "unntak_id": unntak_id}

    # --- godkjenn ---------------------------------------------------------
    policy, policy_hash = policyregister.hent_aktiv(conn, tenant,
                                                    _policy_id(loggpost_id, conn,
                                                               tenant))
    mo = policy.get("menneskelig_overstyring") or {}
    krever_fire = bool(mo.get("krever_fire_oyne"))
    godkjennere = conn.execute(
        "SELECT bruker_id, rolle, authz_version FROM menneskelig_attestasjon"
        " WHERE tenant=%s AND unntak_id=%s AND runde=%s AND operatorhandling="
        "'godkjenn' ORDER BY id", (tenant, unntak_id, r_nr)).fetchall()
    terskel = 2 if krever_fire else 1
    if len(godkjennere) < terskel:
        if status != "venter_andre_godkjenner":
            conn.execute("UPDATE unntak SET status='venter_andre_godkjenner'"
                         " WHERE tenant=%s AND id=%s", (tenant, unntak_id))
        _historikk(conn, tenant, unntak_id, "attestasjon_registrert", aktor,
                   request_id)
        conn.commit()
        return {"utfall": "venter_andre_godkjenner",
                "gjenstaar": terskel - len(godkjennere), "unntak_id": unntak_id}

    # Terskel nådd: mat godkjenningen som en ny beslutning gjennom motoren.
    dek = kryptering.hent_dek(conn, tenant, hi_key_id)
    aad = kryptering.intensjon_aad(unntak_id, handling, hi_ver,
                                   intensjon_policy_hash)
    intensjon = kryptering.dekrypter(dek, bytes(hi_ct), bytes(hi_nonce), tenant,
                                     hi_key_id, ekstra_aad=aad)
    event = dict(intensjon)
    event["hi_integritet_hash"] = hi_hash          # motorens 6-felt-likhet
    aktor_rolle = intensjon.get("aktor_rolle")

    mg = MenneskeligGodkjenning(
        tenant=tenant, target_action=handling,
        ressurs_id=konvolutt.get("ressurs_id"),
        belop=parse_belop(konvolutt.get("belop")),
        valuta=konvolutt.get("valuta"), hi_integritet_hash=hi_hash,
        bundet_grunnkode=bundet_grunnkode, unntak_id=unntak_id, runde=r_nr,
        godkjennere=tuple((b, r, int(a)) for b, r, a in godkjennere),
        godkjennings_policy_hash=godkj_policy_hash, utloper=r_utloper)

    op_id = f"godkj-{unntak_id}-r{r_nr}"
    if r_status == "apen":
        conn.execute("UPDATE godkjenningsrunde SET status='klar' WHERE tenant=%s"
                     " AND unntak_id=%s AND runde=%s", (tenant, unntak_id, r_nr))
    if status != "godkjenning_klar":
        conn.execute("UPDATE unntak SET status='godkjenning_klar' WHERE tenant=%s"
                     " AND id=%s", (tenant, unntak_id))

    ctx = EvaluationContext(tenant_id=tenant, aktor_rolle=aktor_rolle or "",
                            autentisert=True, kilde="human_approval")
    evidens = Evidens(handling=handling, request_id=request_id,
                      idempotency_key=op_id, policy_content_hash=policy_hash)
    d = sikker_beslutning_pg(policy, ctx, event, conn, naa=naa, nokler=None,
                             evidens=evidens, ytre_transaksjon=True,
                             menneskelig_godkjenning=mg)

    # V1-rekkefølge: runde `brukt` (m/ operasjons-id) FØR utfall-insert, så
    # bindingstriggeren har hele kjeden.
    conn.execute(
        "UPDATE godkjenningsrunde SET status='brukt', decision_operation_id=%s"
        " WHERE tenant=%s AND unntak_id=%s AND runde=%s",
        (op_id, tenant, unntak_id, r_nr))
    conn.execute(
        "INSERT INTO godkjenningsutfall (tenant, unntak_id, hi_integritet_hash,"
        " policy_hash, decision_operation_id, motorutfall, beslutning_loggpost_id)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (tenant, unntak_id, hi_hash, godkj_policy_hash, op_id,
         _motorutfall(d.beslutning), evidens.loggpost_id))

    if d.krever_sikkerhetsrouting:
        return _sikkerhetsstopp(conn, pool, tenant, unntak_id, aktor,
                                request_id, "motor_feltavvik")

    if d.beslutning == TILLAT:
        conn.execute("UPDATE unntak SET status='venter_utførelse' WHERE tenant=%s"
                     " AND id=%s", (tenant, unntak_id))
        _historikk(conn, tenant, unntak_id, "godkjent", aktor, request_id)
    else:
        # Motoren sa likevel nei (annet vilkår feilet) → tilbake til manuell,
        # ingen ny selvstendig kø-sak (v3-test).
        conn.execute("UPDATE unntak SET status='manuell' WHERE tenant=%s AND"
                     " id=%s", (tenant, unntak_id))
        _historikk(conn, tenant, unntak_id, "godkjenning_stoppet_av_policy",
                   aktor, request_id)
    conn.commit()
    return {"utfall": d.beslutning, "unntak_id": unntak_id,
            "begrunnelse": [g.kode for g in d.begrunnelse]}


def _policy_id(loggpost_id: int, conn: psycopg.Connection, tenant: str) -> str:
    """Sakens policy-id fra loggposten (revisjonslogg.policy_id er
    `<policy_id>@<versjon>/<handling>`-etiketten; vi trenger ren id)."""
    from policy_validator.engine import les_policyref
    rad = conn.execute("SELECT policy_id FROM revisjonslogg WHERE tenant=%s AND"
                       " id=%s", (tenant, loggpost_id)).fetchone()
    ref = les_policyref(rad[0]) if rad else None
    if ref is None:
        raise Godkjenningsfeil("policy_id_ukjent")
    return ref[0]


def _skriv_attestasjon(conn, tenant, unntak_id, runde, operatorhandling,
                       handling, bundet_grunnkode, konvolutt, konvolutt_hash,
                       saksversjon) -> None:
    """Append-only attestasjonsrad. UNIQUE(tenant,unntak_id,runde,bruker_id)
    håndhever fire-øyne: samme bruker to ganger avvises."""
    ta = handling if operatorhandling == "godkjenn" else None
    bg = bundet_grunnkode if operatorhandling == "godkjenn" else None
    try:
        conn.execute(
            "INSERT INTO menneskelig_attestasjon (tenant, unntak_id, runde,"
            " operatorhandling, target_action, bundet_grunnkode, bruker_id,"
            " rolle, authz_version, konvoluttversjon, konvolutt_hash, mac,"
            " mac_key_id, jti, utloper, saksversjon)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (tenant, unntak_id, runde, operatorhandling, ta, bg,
             konvolutt.get("bruker_id"), konvolutt.get("rolle"),
             konvolutt.get("authz_version"), konvolutt.get("konvoluttversjon"),
             konvolutt_hash, konvolutt.get("mac"), konvolutt.get("mac_key_id"),
             konvolutt.get("jti"), konvolutt.get("utloper"), saksversjon))
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        raise Godkjenningsfeil("allerede_attestert") from None


def _historikk(conn, tenant, unntak_id, hendelse, aktor, request_id) -> None:
    conn.execute(
        "INSERT INTO unntak_historikk (tenant, unntak_id, hendelse, aktor,"
        " request_id) VALUES (%s,%s,%s,%s,%s)",
        (tenant, unntak_id, hendelse, aktor, request_id))


def _sikkerhetsstopp(conn, pool, tenant, unntak_id, aktor, request_id,
                     grunn: str) -> dict:
    """Rull forretnings-tx tilbake, skriv så sikkerhetsevidens på egen
    forbindelse (V3 — rekkefølgen unngår selvvranglås mot FOR UPDATE-låsen)."""
    conn.rollback()
    skriv_sikkerhetsevidens(pool, tenant=tenant, unntak_id=unntak_id,
                            hendelse="godkjenning_stoppet_av_policy",
                            detalj={"grunn": grunn}, aktor=aktor,
                            request_id=request_id)
    return {"utfall": "STOPP", "sikkerhet": grunn, "unntak_id": unntak_id}
