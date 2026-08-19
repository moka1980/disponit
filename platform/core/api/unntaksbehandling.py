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
from datetime import datetime, timedelta, timezone

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
                              policy: dict, policy_hash: str, naa,
                              krev_godkjennbar: bool = True) -> int:
    """Åpne en godkjenningsrunde for en manuell sak.

    Server-utleder `bundet_grunnkode` fra sakens begrunnelseskjede (aldri
    klientvalgt), åpner en `apen` runde med den aktive policyens hash frosset
    per runde, og gjør den kontrollerte overgangen `manuell →
    venter_godkjenning`. Returnerer rundenummeret. Kaster `Godkjenningsfeil`.
    Kalleren eier transaksjonen.

    `krev_godkjennbar` (default True): for GODKJENN må saken ha en komplett
    handlingsintensjon OG en godkjennbar blokkerende grunnkode. For
    avvis/eskaler er ingen av delene nødvendig — et menneske kan alltid avvise
    eller eskalere en manuell sak — så endepunktet åpner runden med False.
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
    if krev_godkjennbar and not intensjon_pakrevd:
        # Godkjenn krever en komplett handlingsintensjon (v2 §1) — ellers kan
        # motoren ikke re-evaluere, og godkjenn er ikke en mulig handling.
        raise Godkjenningsfeil("godkjenn_utilgjengelig", "ingen intensjon")

    lp = conn.execute(
        "SELECT begrunnelse FROM revisjonslogg WHERE tenant=%s AND id=%s",
        (tenant, loggpost_id)).fetchone()
    bundet = _siste_grunnkode(lp[0] if lp else None)
    if bundet is None:
        raise Godkjenningsfeil("godkjenn_utilgjengelig", "ingen blokkerende grunn")
    if krev_godkjennbar and not _er_godkjennbar(policy, bundet, handling):
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


_OP_SCOPE = {"godkjenn": "exceptions:approve", "avvis": "exceptions:reject",
             "eskaler": "exceptions:escalate"}


def behandle_unntakshandling(conn: psycopg.Connection, pool, mac_register, *,
                             tenant: str, aktor: str, request_id: str,
                             unntak_id: int, operatorhandling: str,
                             forventet_saksversjon, idempotency_key: str,
                             input_hash: str, naa) -> dict:
    """Portens hovedvei. Eier transaksjonen (speiler `_flyt` inline siden
    `kjerne.behandle` eier sin egen commit).

    ALT skjer under saks­låsen: idempotens-claim → `FOR UPDATE` → REAUTORISERING
    (medlemskap/scope revalideres etter låsen, fail-closed, uten fallback) →
    optimistisk lås (`saksversjon`) → runde → konvolutt bygges OG MAC-signeres
    server-side fra autentiske låste data → beslutning via motorens verifiserte
    faktakanal. MAC-/bindingsbrudd og avvikende idempotens-replay ruller tilbake
    OG skriver sikkerhetsevidens på egen forbindelse (V3). Kaster
    `Godkjenningsfeil` ved tilstands-/formfeil.
    """
    from .autorisasjon import scopes_for_roller

    if operatorhandling not in _OP_SCOPE:
        conn.rollback()
        raise Godkjenningsfeil("ukjent_operatorhandling")

    sett_kontekst(conn, tenant, aktor, request_id)

    # --- 1. Idempotens: serialiser per nøkkel og claim i EIERTRANSAKSJONEN --
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                 (f"{tenant}\x1fidem\x1f{idempotency_key}",))
    claim = conn.execute(
        "INSERT INTO idempotens (tenant, nokkel, input_hash, status, request_id)"
        " VALUES (%s,%s,%s,'paagaar',%s) ON CONFLICT (tenant, nokkel)"
        " DO NOTHING RETURNING nokkel",
        (tenant, idempotency_key, input_hash, request_id)).fetchone()
    if claim is None:
        eksist = conn.execute(
            "SELECT input_hash, status, respons FROM idempotens"
            " WHERE tenant=%s AND nokkel=%s",
            (tenant, idempotency_key)).fetchone()
        if eksist is None:
            conn.rollback()
            raise Godkjenningsfeil("db_utilgjengelig")
        lagret_hash, istatus, respons = eksist
        if lagret_hash != input_hash:
            # Samme nøkkel, ANNET input → sikkerhetssak (v3/v4), aldri en
            # stille ny operasjon.
            return _sikkerhetsstopp(conn, pool, tenant, unntak_id, aktor,
                                    request_id, "idempotenskonflikt")
        if istatus == "ferdig":
            conn.rollback()
            return {**respons, "replay": True}
        # `paagaar` OG vi holder låsen ⇒ vinneren finnes ikke lenger; overta.
        conn.execute("UPDATE idempotens SET request_id=%s, ts=now()"
                     " WHERE tenant=%s AND nokkel=%s",
                     (request_id, tenant, idempotency_key))

    # --- 2. Lås saken -----------------------------------------------------
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

    # --- 3. REAUTORISERING ETTER LÅSEN (V3 §6 / port 9) -------------------
    # Medlemskap + scope revalideres NÅ, ikke ved den innledende auth-en: en
    # rolle som fjernes i vinduet mellom auth og lås skal stoppe handlingen.
    # Fail-closed, ingen fallback-rolle.
    med = conn.execute(
        "SELECT roller, authz_version FROM brukermedlemskap WHERE tenant=%s"
        " AND bruker_id=%s AND aktiv", (tenant, aktor)).fetchone()
    if med is None:
        conn.rollback()
        raise Godkjenningsfeil("mangler_medlemskap")
    roller = list(med[0])
    authz_version = int(med[1])
    if _OP_SCOPE[operatorhandling] not in scopes_for_roller(roller):
        conn.rollback()
        raise Godkjenningsfeil("scope_mangler")

    # --- 4. Optimistisk lås: klientens saksversjon mot den LÅSTE raden -----
    # Etter FOR UPDATE, før runde/attestasjon (spec §7). En operatør som
    # handler fra en stale dialog stoppes uten sideeffekt.
    if not isinstance(forventet_saksversjon, int) \
            or forventet_saksversjon != saksversjon:
        conn.rollback()
        raise Godkjenningsfeil("saksversjon_utdatert")

    # --- 5. Policy (krever_rolle + godkjennbarhet) ------------------------
    policy, policy_hash = policyregister.hent_aktiv(
        conn, tenant, _policy_id(loggpost_id, conn, tenant))
    mo = policy.get("menneskelig_overstyring") or {}

    # --- 6. Forretningsrolle (reautorisert, ingen fallback) ---------------
    if operatorhandling == "godkjenn":
        krever_rolle = mo.get("krever_rolle")
        if not krever_rolle or krever_rolle not in roller:
            conn.rollback()
            raise Godkjenningsfeil("mangler_rolle", str(krever_rolle))
        rolle = krever_rolle
    else:
        rolle = roller[0]   # scope er bevist; audit-rollen er en ekte rolle

    # --- 6b. Gate 14a: avvis krever at HVER relatert oppdrag/kapabilitet-rad
    # positivt er trygg (intet, kansellert oppdrag, eller terminal kapabilitet).
    # Én levende rad → `avklaring_kreves` + 409, ALDRI `avvist`. Saks­låsen (FOR
    # UPDATE over) serialiserer mot phantom-innsetting (P2). LIGGER FØR
    # runde-åpning + attestasjon: en blokkert avvis skal ikke åpne en runde,
    # etterlate en attestasjon, eller skrive ny historikk ved gjentatt forsøk
    # (P3).
    opplosning_detalj = None
    if operatorhandling == "avvis":
        # 043 (Gate 14b): et levende OPPDRAG er ikke lenger en blindvei —
        # nei-et løses opp i samme transaksjon: kapabiliteten brennes
        # `avvist`, claimet fences, oppdraget kanselleres. 14a-svaret (409)
        # står igjen for LEVENDE ARBEIDSKAPABILITETER — de har ingen
        # oppløsningsvei ennå, og en vakt uten utvei er bedre enn en stille
        # avvisning av evidens.
        rader = conn.execute(
            "SELECT kilde, ref, status FROM sak_utestaaende(%s,%s)",
            (tenant, unntak_id)).fetchall()
        levende_opp = [int(ref) for kilde, ref, st in rader
                       if kilde == "oppdrag" and st in ("opprettet",
                                                        "plukket")]
        levende_kap = [ref for kilde, ref, st in rader
                       if kilde == "kapabilitet"]
        terminale = {int(ref): st for kilde, ref, st in rader
                     if kilde == "oppdrag"
                     and st not in ("opprettet", "plukket")}
        # ... og vakten står på KAPABILITETEN alene (Codex P2). Første
        # utgave betinget den på `not levende_opp`: fantes det både en
        # levende arbeidskapabilitet OG et kansellerbart oppdrag, hoppet
        # avvis-veien forbi `_flagg_avklaring`, kansellerte oppdragene og
        # merket saken `avvist` mens kapabiliteten sto igjen BRUKBAR. Da
        # kunne den autoriserte handlingen fortsatt utføres etter det
        # menneskelige nei-et — nøyaktig det 14a finnes for å hindre. En
        # levende kapabilitet uten implementert oppløsningsvei blokkerer
        # avvis, også når det finnes oppdrag ved siden av; oppdragene er
        # da urørt, og det er riktig: en delvis oppløsning er ikke det
        # mennesket sa nei til.
        if levende_kap:
            utestaaende = _sjekk_utestaaende(conn, tenant, unntak_id)
            return _flagg_avklaring(conn, tenant, unntak_id, utestaaende,
                                    aktor, request_id, idempotency_key)
        # Selve oppløsningen skjer i TERMINALGRENEN under (etter runde-
        # og attestasjonsstegene): en avvis som uansett felles av
        # Godkjenningsfeil skal aldri ha rørt et oppdrag først.
        if terminale:
            # Utenfor kappløpet (port 13): terminalt oppdrag er ordinært
            # avvis — men hendelsen skal bære hva mennesket visste.
            # BETINGELSEN `not levende_opp` er borte (Codex P2, runde 2):
            # hadde saken både et levende og et alt terminalt oppdrag, ble
            # de terminale radene lest her og så kastet, og revisjonen
            # fortalte bare om det som ble kansellert. Statusen mennesket
            # faktisk handlet på er evidens uansett hva som står ved siden
            # av; oppløsningsgrenen under FYLLER PÅ denne, den erstatter
            # den ikke.
            opplosning_detalj = {
                "oppdrag_status_ved_avvis": [
                    {"oppdrag_id": oid, "status": st}
                    for oid, st in sorted(terminale.items())]}

    # --- 7. Aktiv runde (åpne under låsen ved behov) ----------------------
    runde = conn.execute(
        "SELECT runde, status, bundet_grunnkode, godkjennings_policy_hash,"
        " utloper FROM godkjenningsrunde WHERE tenant=%s AND unntak_id=%s"
        " AND status IN ('apen','klar') FOR UPDATE",
        (tenant, unntak_id)).fetchone()
    if runde is None:
        if status != "manuell":
            conn.rollback()
            raise Godkjenningsfeil("runde_ulovlig_tilstand", f"status={status}")
        opprett_godkjenningsrunde(conn, tenant=tenant, unntak_id=unntak_id,
                                  aktor=aktor, request_id=request_id,
                                  policy=policy, policy_hash=policy_hash, naa=naa,
                                  krev_godkjennbar=(operatorhandling == "godkjenn"))
        status = "venter_godkjenning"
        runde = conn.execute(
            "SELECT runde, status, bundet_grunnkode, godkjennings_policy_hash,"
            " utloper FROM godkjenningsrunde WHERE tenant=%s AND unntak_id=%s"
            " AND status IN ('apen','klar') FOR UPDATE",
            (tenant, unntak_id)).fetchone()
    r_nr, r_status, bundet_grunnkode, godkj_policy_hash, r_utloper = runde
    if r_utloper <= naa:
        conn.rollback()
        raise Godkjenningsfeil("runde_utlopt")

    # --- 8. Bygg + MAC-signer konvolutten fra AUTENTISKE, LÅSTE data ------
    belop = valuta = ressurs_id = None
    if operatorhandling == "godkjenn" and intensjon_pakrevd and hi_ct is not None:
        dek = kryptering.hent_dek(conn, tenant, hi_key_id)
        aad = kryptering.intensjon_aad(unntak_id, handling, hi_ver,
                                       intensjon_policy_hash)
        forhaand = kryptering.dekrypter(dek, bytes(hi_ct), bytes(hi_nonce),
                                        tenant, hi_key_id, ekstra_aad=aad)
        belop, valuta = forhaand.get("belop"), forhaand.get("valuta")
        ressurs_id = forhaand.get("ressurs_id")
    konvolutt = {
        "konvoluttversjon": 2, "operatorhandling": operatorhandling,
        "tenant": tenant, "unntak_id": unntak_id, "runde": r_nr,
        "target_action": handling, "ressurs_id": ressurs_id, "belop": belop,
        "valuta": valuta, "hi_integritet_hash": hi_hash,
        "bundet_grunnkode": bundet_grunnkode,
        "godkjennings_policy_hash": godkj_policy_hash, "bruker_id": aktor,
        "rolle": rolle, "authz_version": authz_version,
        "jti": f"{aktor}-{unntak_id}-r{r_nr}-{operatorhandling}".ljust(22, "j"),
        "utloper": (naa + RUNDE_TTL).isoformat(), "saksversjon": saksversjon}
    mac_key_id, mac = mac_register.signer(konvolutt)
    konvolutt["mac"], konvolutt["mac_key_id"] = mac, mac_key_id

    # MAC + binding (defense-in-depth; server signerte akkurat over disse data).
    if not mac_register.verifiser(konvolutt, mac, mac_key_id):
        return _sikkerhetsstopp(conn, pool, tenant, unntak_id, aktor,
                                request_id, "mac_ugyldig")
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
        if levende_opp:
            # 043: nei-et og beviset i ÉN transaksjon — kapabiliteten
            # brennes `avvist`, claimet fences, oppdraget kanselleres.
            res = conn.execute(
                "SELECT utfall, oppdrag_id, oppdrag_status_ved_avvis,"
                " kvitteringsref FROM avvis_med_opplosning(%s,%s,%s,%s,%s)",
                (tenant, unntak_id, levende_opp, aktor,
                 request_id)).fetchall()
            vunnet = [r for r in res if r[0] == "oppdrag_utfort"]
            if vunnet:
                # Kvitteringen vant kappløpet: kansellér INGENTING —
                # rollbacken tar attestasjonen, runden og eventuelle andre
                # oppdrags oppløsning i samme kall, for et delvis nei er
                # ikke det mennesket sa nei til. Idempotensraden rulles
                # også tilbake: en retry skal møte den NYE tilstanden
                # (terminalt oppdrag → ordinært avvis).
                conn.rollback()
                return {"utfall": "oppdrag_utfort",
                        "unntak_id": unntak_id,
                        "oppdrag_id": int(vunnet[0][1]),
                        "kvitteringsref": vunnet[0][3]}
            # `alt_terminal` er det ANDRE tapte sporet (Codex P2, runde 2):
            # et oppdrag som var levende under prescreeningen, men rakk å
            # bli `feilet`/`kansellert` før oppdragslåsen, kommer tilbake
            # herfra — ikke som `kansellert`. Filtreres det bort, forsvinner
            # raden helt ut av revisjonen. Den hører hjemme i samme
            # statuslista som de prescreenede terminale: ingenting ble løst
            # opp, men mennesket handlet på en sak som hadde den raden.
            terminale.update({int(r[1]): r[2] for r in res
                              if r[0] == "alt_terminal"})
            opplosning_detalj = {
                "opplost": [{"oppdrag_id": int(r[1]),
                             "oppdrag_status_ved_avvis": r[2]}
                            for r in res if r[0] == "kansellert"]}
            if terminale:
                opplosning_detalj["oppdrag_status_ved_avvis"] = [
                    {"oppdrag_id": oid, "status": st}
                    for oid, st in sorted(terminale.items())]
        conn.execute("UPDATE godkjenningsrunde SET status='kansellert'"
                     " WHERE tenant=%s AND unntak_id=%s AND runde=%s",
                     (tenant, unntak_id, r_nr))
        conn.execute("UPDATE unntak SET status='avvist' WHERE tenant=%s AND id=%s",
                     (tenant, unntak_id))
        if opplosning_detalj is not None:
            # Port 7/13: hendelsen bærer oppløsningen — begge hoppene står
            # alt i historikken (oppdrag_fencet + oppdrag_kansellert,
            # skrevet av avvis_med_opplosning i SAMME transaksjon).
            conn.execute(
                "INSERT INTO unntak_historikk (tenant, unntak_id, hendelse,"
                " aktor, request_id, detalj) VALUES"
                " (%s,%s,'avvist_handling',%s,%s,%s)",
                (tenant, unntak_id, aktor, request_id,
                 json.dumps(opplosning_detalj, ensure_ascii=False)))
        else:
            _historikk(conn, tenant, unntak_id, "avvist_handling", aktor,
                       request_id)
        return _fullfor(conn, tenant, idempotency_key,
                        {"utfall": "avvist", "unntak_id": unntak_id,
                         **({"opplosning": opplosning_detalj["opplost"]}
                            if opplosning_detalj
                            and "opplost" in opplosning_detalj else {})})

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
        return _fullfor(conn, tenant, idempotency_key,
                        {"utfall": "eskalert", "unntak_id": unntak_id})

    # --- godkjenn ---------------------------------------------------------
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
        return _fullfor(conn, tenant, idempotency_key,
                        {"utfall": "venter_andre_godkjenner",
                         "gjenstaar": terskel - len(godkjennere),
                         "unntak_id": unntak_id})

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
    return _fullfor(conn, tenant, idempotency_key,
                    {"utfall": d.beslutning, "unntak_id": unntak_id,
                     "begrunnelse": [g.kode for g in d.begrunnelse]})


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


def _sjekk_utestaaende(conn, tenant: str, unntak_id: int) -> str | None:
    """Gate 14a (P1): None hvis saken er TRYGG å avvise (HVER relatert rad er
    positivt trygg), ellers en hash av den utestående tilstanden.

    Trygt oppdrag = `kansellert` (før claim). Trygg kapabilitet = terminal
    (`brukt`/`feilet`). Alt annet — inkl. en ukjent/fremtidig status — er
    levende og utrygt. Ingen `MAX`, ingen entallsrad: leser ALLE rader for
    saken (saks­låsen holdes allerede av kalleren, så settet er stabilt)."""
    # `arbeidskapabiliteter` er off-limits for runtime; lesningen går gjennom
    # `sak_utestaaende` (SECURITY DEFINER, eid av m37_claimer). Kalleren holder
    # saks­låsen, så settet er stabilt.
    rader = conn.execute("SELECT kilde, ref, status FROM sak_utestaaende(%s,%s)",
                         (tenant, unntak_id)).fetchall()
    if not rader:
        return None
    payload = json.dumps([[k, r, s] for k, r, s in rader], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _flagg_avklaring(conn, tenant, unntak_id, utestaaende_hash, aktor,
                     request_id, idempotency_key) -> dict:
    """Gate 14a: sett `avklaring_kreves` — men KUN hvis ikke allerede satt for
    NØYAKTIG samme utestående tilstand (P3): da ingen ny versjonsøkning og
    ingen ny historikkrad. Invarianten ligger på SAKEN (hash av utestående
    tilstand), ikke på idempotensnøkkelen — ulike nøkler flagger ikke om og
    om igjen. Committer FØR 409-et returneres."""
    siste = conn.execute(
        "SELECT detalj->>'utestaaende_hash' FROM unntak_historikk WHERE"
        " tenant=%s AND unntak_id=%s AND hendelse='avklaring_kreves'"
        " ORDER BY id DESC LIMIT 1", (tenant, unntak_id)).fetchone()
    if siste is None or siste[0] != utestaaende_hash:
        conn.execute("UPDATE unntak SET saksversjon=saksversjon+1 WHERE"
                     " tenant=%s AND id=%s", (tenant, unntak_id))
        conn.execute(
            "INSERT INTO unntak_historikk (tenant, unntak_id, hendelse, aktor,"
            " request_id, detalj) VALUES (%s,%s,'avklaring_kreves',%s,%s,%s)",
            (tenant, unntak_id, aktor, request_id,
             json.dumps({"utestaaende_hash": utestaaende_hash})))
    # Committet resultat: saken er flagget for avklaring, IKKE avvist. Bæres
    # som `utfall`-diskriminator (ikke et rått `http`-DTO-felt); endepunktet
    # oversetter dette til det LUKKEDE feilobjektet `{"feil": ...}` med 409,
    # slik at UI-et skiller avklaring fra generisk 409 og viser rett tekst.
    return _fullfor(conn, tenant, idempotency_key,
                    {"utfall": "utestaaende_oppdrag", "unntak_id": unntak_id})


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


def _fullfor(conn, tenant, idempotency_key, res: dict) -> dict:
    """Lagre den idempotente responsen og commit. En replay med samme nøkkel og
    samme input får NØYAKTIG denne lagrede responsen tilbake — aldri en ny
    operasjon."""
    conn.execute("UPDATE idempotens SET status='ferdig', respons=%s"
                 " WHERE tenant=%s AND nokkel=%s",
                 (json.dumps(res, ensure_ascii=False), tenant, idempotency_key))
    conn.commit()
    return res


# --------------------------------------------------------------------------
# HTTP-endepunktet (CP5): POST /v1/unntak/{id}/handling
# --------------------------------------------------------------------------

_HANDLING_SCOPE = {"godkjenn": "exceptions:approve",
                   "avvis": "exceptions:reject",
                   "eskaler": "exceptions:escalate"}

#: Godkjenningsfeil-kode → HTTP. Ukjent kode → 409 (tilstandskonflikt).
_FEIL_HTTP = {"unntak_ukjent": 404, "ingen_aktiv_runde": 409,
              "runde_utlopt": 409, "feil_runde": 409,
              "runde_ulovlig_tilstand": 409, "godkjenn_utilgjengelig": 409,
              "allerede_attestert": 409, "ukjent_operatorhandling": 400,
              "policy_id_ukjent": 409, "mangler_rolle": 403,
              "mangler_medlemskap": 403, "scope_mangler": 403,
              "saksversjon_utdatert": 409, "runde_allerede_aapen": 409,
              # 14a: committet avklaringsflagg (ikke en kastet feil).
              "utestaaende_oppdrag": 409}


def handling_endepunkt(tjeneste, request, unntak_id: int):
    """POST /v1/unntak/{id}/handling — autentisert, CSRF-vernet, idempotent.

    Endepunktet er tynt: form + auth + CSRF, så delegeres ALT (idempotens,
    lås, REAUTORISERING etter lås, saksversjon, konvolutt-signering, beslutning)
    til `behandle_unntakshandling` i én eiertransaksjon. Klienten sender
    `operatorhandling` + `saksversjon` (den den viste) + `Idempotency-Key`.
    """
    from starlette.responses import JSONResponse

    from . import kjerne
    from . import sesjon as sesjonmodul
    from .app import _autentiser, _feilsvar, _rid

    rid = _rid(request)
    raa = request.scope.get("state", {}).get("kropp", b"")
    try:
        body = json.loads(raa.decode("utf-8"))
    except Exception:
        return _feilsvar("request_feilformet", rid)
    if not isinstance(body, dict):
        return _feilsvar("request_feilformet", rid)
    oh = body.get("operatorhandling")
    scope = _HANDLING_SCOPE.get(oh)
    # `saksversjon` er PÅKREVD: uten den kan klienten ikke bevise hvilken
    # tilstand operatøren så, og den optimistiske låsen ville vært tannløs.
    saksversjon = body.get("saksversjon")
    if scope is None or not isinstance(saksversjon, int) \
            or isinstance(saksversjon, bool):
        return _feilsvar("request_feilformet", rid)

    idem = request.headers.get("idempotency-key")
    if not idem or not idem.strip():
        return _feilsvar("idempotensnokkel_mangler", rid)
    idem = idem.strip()

    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift")
        return _feilsvar("db_utilgjengelig", rid)
    try:
        try:
            auth = _autentiser(tjeneste, request, conn, rid, scope)
        except kjerne.Feilsvar as f:
            return _feilsvar(f.kode, rid)

        # CSRF (dobbel-innsending) — browsersesjonens muterende vei.
        sesjon_cookie = request.cookies.get(sesjonmodul.C_SESJON)
        rad = conn.execute("SELECT csrf_hash FROM slaa_opp_sesjon(%s)",
                           (sesjonmodul._hash(sesjon_cookie),)).fetchone() \
            if sesjon_cookie else None
        conn.rollback()
        if rad is None or not sesjonmodul.csrf_matcher(rad[0], request):
            tjeneste.logg.hendelse("csrf_ugyldig", rid)
            return _feilsvar("csrf_ugyldig", rid)

        tenant = auth.tenant
        bid = auth.token_id.split("sesjon:", 1)[-1]
        # Idempotensnøkkelen bindes til HELE inputtet: samme nøkkel + annet
        # input = sikkerhetssak, ikke en ny operasjon.
        input_hash = hashlib.sha256(
            f"{tenant}\x1f{bid}\x1f{unntak_id}\x1f{oh}\x1f{saksversjon}"
            .encode("utf-8")).hexdigest()
        try:
            res = behandle_unntakshandling(
                conn, tjeneste.pool, tjeneste.mac_register, tenant=tenant,
                aktor=bid, request_id=rid, unntak_id=unntak_id,
                operatorhandling=oh, forventet_saksversjon=saksversjon,
                idempotency_key=idem, input_hash=input_hash,
                naa=datetime.now(timezone.utc))
        except Godkjenningsfeil as g:
            conn.rollback()
            return _feilsvar_kode(g.kode, rid)
        # 14a: avklaringsflagget er alt COMMITTET (kan ikke kastes+rulles
        # tilbake som en vanlig feil). Oversett det til det LUKKEDE feilobjektet
        # `{"feil": "utestaaende_oppdrag", "request_id": ...}` med 409 — samme
        # wire-format som resten av API-et, så `postHandling` leser `kropp.feil`
        # og UI-et viser avklaringsteksten i stedet for en generisk 409.
        if res.get("utfall") == "utestaaende_oppdrag":
            return _feilsvar_kode("utestaaende_oppdrag", rid)
        # 043: kvitteringen vant kappløpet. Kan systemet bevise at
        # handlingen ble utført, skal det ikke skrive «avvist» som om
        # nei-et rakk fram — mennesket beslutter på nytt, med referansen.
        if res.get("utfall") == "oppdrag_utfort":
            return JSONResponse(
                {"feil": "oppdrag_utfort",
                 "oppdrag_id": res.get("oppdrag_id"),
                 "kvitteringsref": res.get("kvitteringsref"),
                 "request_id": rid},
                status_code=409, headers={"x-request-id": rid})
        return JSONResponse(res, status_code=200,
                            headers={"x-request-id": rid})
    finally:
        tjeneste.pool.gi_tilbake(conn)


def _feilsvar_kode(kode: str, rid: str):
    from starlette.responses import JSONResponse
    return JSONResponse({"feil": kode, "request_id": rid},
                        status_code=_FEIL_HTTP.get(kode, 409),
                        headers={"x-request-id": rid})
