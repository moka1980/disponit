"""PR-013 CP5c — porten for policyaktivering (fire-øyne på fullmaktsendring).

Å aktivere en policy er å endre hva agenten HAR LOV TIL. Derfor samme
arbeidsdeling som PR-012s unntaksbehandling, men strengere: en aktivering er en
menneskelig godkjent, MAC-signert overgang som til slutt utføres av den herdede
`aktiver_policy`-funksjonen (migrasjon 013, SECURITY DEFINER, eid av
`disponit_policy_eier`). Denne modulen eier:

  1. **Runde-åpning** (`opprett_aktiveringsrunde`): under `policy_hode`-låsen
     utledes diffen (mot aktiv versjon eller `DENY_ALL_V1`) og klassifiseringen
     (UTVIDER/INNSNEVRER/NØYTRAL), og ALT bindes frosset i runden — diff_hash,
     klassifisering_hash, klassifikator-/skjema-/motorsemantikk-versjoner,
     deny-all, og påkrevd antall godkjennere (V6).
  2. **Attestering** (`attester_aktivering`): en godkjenner attesterer DIFFEN
     (diff_hash), aldri versjonsnummeret (v5 §2). Server bygger + MAC-signer
     konvolutten `disponit_policy_activation_v1` fra LÅSTE data, `er_forfatter`
     er server-utledet (DB-triggeren vokter det, V7), og fire-øyne håndheves av
     antallet + at MINST én godkjenner ikke er forfatteren.
  3. **Aktivering**: når terskelen er nådd, REKALKULERES diff/klasse UNDER
     LÅSEN. Har den aktive policyen flyttet seg siden runden åpnet (eller motor-
     semantikken endret seg), avvises aktiveringen — REBASERING kreves. Ellers
     kalles `aktiver_policy` (deaktiver forrige + sett inn ny i SAMME tx).

Kalleren eier transaksjonen (som `behandle_unntakshandling`). Fullmaktsreglene
håndheves av DB-en (triggere + herdet funksjon), ikke av at koden husker dem.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import timedelta

import psycopg

from db.pg import sett_kontekst
from policy_validator import klassifikator, policydiff, semantikk
from policy_validator import schema as _schema

from . import policyregister as _pr
from .autorisasjon import scopes_for_roller
from .mac_register import kanonisk_konvolutt

#: Skjemaversjonen som bindes inn i aktiveringsrunden. Leses fra skjemafilens
#: NAVN (utenfor `SEMANTIKK_MANIFEST`), ikke fra en konstant i `schema.py` —
#: `schema.py` er en manifestfil, og en versjonskonstant der ville tvunget en
#: re-pinning av `MOTOR_SEMANTIKKVERSJON` for en ren metadata-endring. Bytter
#: man til v0.3 uten å oppdatere filnavnet, feiler lasten uansett (fil mangler).
_m = re.search(r"v(\d+\.\d+)", _schema._SKJEMA_STI.name)
POLICYSKJEMA_VERSJON = _m.group(1) if _m else "0"

#: En åpen runde lever innen én arbeidsøkt (fire-øyne skal ikke stå åpent i
#: dager). Utløp lukker runden; attestasjoner slettes aldri.
RUNDE_TTL = timedelta(hours=24)

#: Konvoluttnavnet namespacer aktiveringskonvolutten bort fra
#: `disponit_human_approval_v*`: navnet inngår i de MAC-signerte bytene, så en
#: godkjenningskonvolutt fra unntaksveien kan aldri gjenspilles som aktivering.
KONVOLUTT_TYPE = "disponit_policy_activation_v1"
KONVOLUTTVERSJON = 1

#: Aktiverte policyer settes i produksjonsstatus (den blir den aktive raden).
_AKTIV_STATUS = "produksjon"

_AKTIVER_SCOPE = "policy:activate"

#: Feltene konvolutten binder til de LÅSTE dataene (defense-in-depth: server
#: signerte akkurat over disse selv).
_BINDINGSFELT = ("konvolutt_type", "tenant", "utkast_id", "policy_id", "runde",
                 "diff_hash", "klassifisering_hash", "risikoklasse",
                 "base_policy_hash", "bruker_id", "er_forfatter")


class Aktiveringsfeil(Exception):
    """En avvist policyadmin-handling med en semantisk kode. CP6-endepunktet
    oversetter koden til HTTP; porten holdes fri for HTTP-detaljer."""

    def __init__(self, kode: str, detalj: str = "") -> None:
        super().__init__(kode if not detalj else f"{kode}: {detalj}")
        self.kode = kode
        self.detalj = detalj


# --------------------------------------------------------------------------
# Felles: base-policy (aktiv versjon eller DENY_ALL_V1) + klassifisering.
# --------------------------------------------------------------------------

def _base(conn: psycopg.Connection, tenant: str, policy_id: str,
          aktiv_versjon: str | None) -> tuple[dict, str]:
    """(innhold, innholds_hash) for base-policyen en endring måles mot.

    Ingen aktiv versjon (NULL-peker, evt. helt ny policy) → `DENY_ALL_V1`:
    første policy klassifiseres som en UTVIDELSE fra «ingenting tillatt», ikke
    som en nøytral førstegangsregistrering (V9)."""
    if aktiv_versjon is None:
        return semantikk.DENY_ALL_V1, semantikk.DENY_ALL_HASH
    rad = conn.execute(
        "SELECT innhold, innholds_hash FROM policyer"
        " WHERE tenant=%s AND policy_id=%s AND versjon=%s",
        (tenant, policy_id, aktiv_versjon)).fetchone()
    if rad is None:
        # Pekeren viser på en versjon som ikke finnes — datamodellen skal
        # gjøre dette umulig (kompositt-FK), men porten stoler ikke blindt.
        raise Aktiveringsfeil("base_mangler", f"versjon={aktiv_versjon}")
    innhold, lagret = rad
    if not isinstance(innhold, dict):
        raise Aktiveringsfeil("base_korrupt")
    return innhold, lagret


def _vurder(base_innhold: dict, base_hash: str, ny_innhold: dict) -> dict:
    """Diff + klassifisering + påkrevd antall godkjennere. Ren funksjon av
    inndata (ingen DB) → SAMME resultat ved runde-åpning og ved rekalk under
    låsen; avvik betyr at basen (eller motorsemantikken) flyttet seg."""
    _, dh = policydiff.diff_og_hash(base_innhold, ny_innhold)
    kl = klassifikator.klassifiser(base_innhold, ny_innhold)
    risikoklasse = kl["klasse"]
    # V6: UTVIDER krever to godkjennere (forfatter kan være én, aldri begge —
    # sikret av «minst én ikke-forfatter» + UNIQUE(bruker) per runde).
    # INNSNEVRER/NØYTRAL: én godkjenner ≠ forfatter (samme ikke-forfatter-krav).
    pakrevd = 2 if risikoklasse == klassifikator.UTVIDER else 1
    return {
        "diff": policydiff.strukturert_diff(base_innhold, ny_innhold),
        "diff_hash": dh,
        "risikoklasse": risikoklasse,
        "klassifisering_endringer": kl["endringer"],   # risikoklasse PER endring
        "klassifisering_hash": kl["klassifisering_hash"],
        "klassifikatorversjon": kl["klassifikatorversjon"],
        "base_policy_hash": base_hash,
        "pakrevd_antall_godkjennere": pakrevd,
    }


def _base_med_versjon(conn, tenant, policy_id) -> tuple[dict, str, str | None]:
    """(innhold, hash, aktiv_versjon) for gjeldende aktive base (deny-all om
    ingen). Delt av utkast-detalj og runde-åpning."""
    aktiv = _hode_aktiv_versjon(conn, tenant, policy_id)
    innhold, h = _base(conn, tenant, policy_id, aktiv)
    return innhold, h, aktiv


# --------------------------------------------------------------------------
# Utkast-livssyklus (CP6): opprett → rediger → valider. Et utkast er IKKE en
# policy; det er den ENESTE muterbare tilstanden. Validering fryser
# innholds_hash og låser innholdet (kolonnelåsen i migrasjon 012).
# --------------------------------------------------------------------------

def opprett_utkast(conn: psycopg.Connection, *, tenant: str, aktor: str,
                   request_id: str, policy_id: str, innhold: dict,
                   rollback_av_versjon: str | None = None) -> dict:
    """Opprett et nytt utkast (status `utkast`). Fanger gjeldende aktive versjon
    + hash som `basert_pa_*` for konfliktdeteksjon (§4). Kalleren eier tx."""
    sett_kontekst(conn, tenant, aktor, request_id)
    if not isinstance(innhold, dict):
        conn.rollback()
        raise Aktiveringsfeil("utkast_feilformet")
    _, base_hash, aktiv = _base_med_versjon(conn, tenant, policy_id)
    utkast_id = "u-" + secrets.token_hex(8)
    conn.execute(
        "INSERT INTO policyutkast (tenant, utkast_id, policy_id,"
        " basert_pa_versjon, basert_pa_hash, rollback_av_versjon, innhold,"
        " opprettet_av) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
        (tenant, utkast_id, policy_id, aktiv, base_hash, rollback_av_versjon,
         json.dumps(innhold), aktor))
    conn.commit()
    return {"utkast_id": utkast_id, "policy_id": policy_id,
            "utkastversjon": 1, "status": "utkast", "base_versjon": aktiv}


def rediger_utkast(conn: psycopg.Connection, *, tenant: str, aktor: str,
                   request_id: str, utkast_id: str, forventet_utkastversjon,
                   innhold: dict) -> dict:
    """Rediger innholdet i et `utkast`-utkast (optimistisk lås på
    `utkastversjon`). Et validert utkast er frosset (innholds_hash låst) — da
    lages et nytt utkast i stedet. Kalleren eier tx."""
    sett_kontekst(conn, tenant, aktor, request_id)
    if not isinstance(innhold, dict):
        conn.rollback()
        raise Aktiveringsfeil("utkast_feilformet")
    rad = conn.execute(
        "SELECT status, utkastversjon FROM policyutkast WHERE tenant=%s AND"
        " utkast_id=%s FOR UPDATE", (tenant, utkast_id)).fetchone()
    if rad is None:
        conn.rollback()
        raise Aktiveringsfeil("utkast_ukjent")
    status, ver = rad
    if status != "utkast":
        conn.rollback()
        raise Aktiveringsfeil("utkast_ulovlig_tilstand", f"status={status}")
    if not isinstance(forventet_utkastversjon, int) \
            or forventet_utkastversjon != ver:
        conn.rollback()
        raise Aktiveringsfeil("utkastversjon_utdatert", f"er={ver}")
    ny = ver + 1
    conn.execute(
        "UPDATE policyutkast SET innhold=%s::jsonb, utkastversjon=%s"
        " WHERE tenant=%s AND utkast_id=%s",
        (json.dumps(innhold), ny, tenant, utkast_id))
    conn.commit()
    return {"utkast_id": utkast_id, "utkastversjon": ny, "status": "utkast"}


def valider_utkast(conn: psycopg.Connection, *, tenant: str, aktor: str,
                   request_id: str, utkast_id: str) -> dict:
    """Skjemavalider utkastet; ved suksess fryses `innholds_hash` og status går
    `utkast → validert`. Ugyldig → utfall `ugyldig` med feillisten (ingen
    tilstandsendring). Kalleren eier tx."""
    sett_kontekst(conn, tenant, aktor, request_id)
    rad = conn.execute(
        "SELECT innhold, status FROM policyutkast WHERE tenant=%s AND"
        " utkast_id=%s FOR UPDATE", (tenant, utkast_id)).fetchone()
    if rad is None:
        conn.rollback()
        raise Aktiveringsfeil("utkast_ukjent")
    innhold, status = rad
    if status != "utkast":
        conn.rollback()
        raise Aktiveringsfeil("utkast_ulovlig_tilstand", f"status={status}")
    feil = _schema.valider_policy(innhold)
    if feil:
        conn.rollback()
        return {"utfall": "ugyldig", "utkast_id": utkast_id, "feil": feil}
    h = _pr.innholds_hash(innhold)
    conn.execute(
        "UPDATE policyutkast SET status='validert', innholds_hash=%s"
        " WHERE tenant=%s AND utkast_id=%s", (h, tenant, utkast_id))
    conn.commit()
    return {"utfall": "validert", "utkast_id": utkast_id, "innholds_hash": h}


def hent_utkast_detalj(conn: psycopg.Connection, *, tenant: str, aktor: str,
                       request_id: str, utkast_id: str) -> dict:
    """Utkastet + diffen mot aktiv base + klassifisering + evt. åpen runde med
    attestasjoner. Rent lesende (ruller tilbake til slutt)."""
    sett_kontekst(conn, tenant, aktor, request_id)
    rad = conn.execute(
        "SELECT policy_id, innhold, innholds_hash, status, utkastversjon,"
        " opprettet_av FROM policyutkast WHERE tenant=%s AND utkast_id=%s",
        (tenant, utkast_id)).fetchone()
    if rad is None:
        conn.rollback()
        raise Aktiveringsfeil("utkast_ukjent")
    policy_id, innhold, innholds_hash, status, ver, opprettet_av = rad
    base_innhold, base_hash, aktiv = _base_med_versjon(conn, tenant, policy_id)
    v = _vurder(base_innhold, base_hash, innhold)
    runde = conn.execute(
        "SELECT runde, status, diff_hash, risikoklasse,"
        " pakrevd_antall_godkjennere, utloper FROM aktiveringsrunde"
        " WHERE tenant=%s AND utkast_id=%s ORDER BY runde DESC LIMIT 1",
        (tenant, utkast_id)).fetchone()
    runde_dto = None
    if runde is not None:
        r_nr, r_status, r_diff, r_risiko, r_pakrevd, r_utloper = runde
        rows = conn.execute(
            "SELECT bruker_id, rolle, er_forfatter, ts FROM"
            " aktiveringsattestasjon WHERE tenant=%s AND utkast_id=%s AND"
            " runde=%s ORDER BY id", (tenant, utkast_id, r_nr)).fetchall()
        runde_dto = {
            "runde": r_nr, "status": r_status, "diff_hash": r_diff,
            "risikoklasse": r_risiko,
            "pakrevd_antall_godkjennere": r_pakrevd,
            "utloper": r_utloper.isoformat(),
            "attestasjoner": [
                {"bruker_id": b, "rolle": ro, "er_forfatter": ef,
                 "ts": ts.isoformat()} for b, ro, ef, ts in rows]}
    conn.rollback()
    return {
        "utkast_id": utkast_id, "policy_id": policy_id, "status": status,
        "utkastversjon": ver, "opprettet_av": opprettet_av,
        "innholds_hash": innholds_hash, "base_versjon": aktiv,
        "diff": v["diff"], "diff_hash": v["diff_hash"],
        "risikoklasse": v["risikoklasse"],
        "klassifisering_endringer": v["klassifisering_endringer"],
        "pakrevd_antall_godkjennere": v["pakrevd_antall_godkjennere"],
        "aktiv_runde": runde_dto}


def list_utkast(conn: psycopg.Connection, *, tenant: str, aktor: str,
                request_id: str, policy_id: str | None = None) -> list:
    """Utkastene for tenanten (evt. filtrert på policy_id). Rent lesende."""
    sett_kontekst(conn, tenant, aktor, request_id)
    if policy_id:
        rows = conn.execute(
            "SELECT utkast_id, policy_id, status, utkastversjon, opprettet"
            " FROM policyutkast WHERE tenant=%s AND policy_id=%s"
            " ORDER BY opprettet DESC", (tenant, policy_id)).fetchall()
    else:
        rows = conn.execute(
            "SELECT utkast_id, policy_id, status, utkastversjon, opprettet"
            " FROM policyutkast WHERE tenant=%s ORDER BY opprettet DESC",
            (tenant,)).fetchall()
    conn.rollback()
    return [{"utkast_id": u, "policy_id": p, "status": s, "utkastversjon": vv,
             "opprettet": o.isoformat()} for u, p, s, vv, o in rows]


def _hode_aktiv_versjon(conn, tenant, policy_id) -> str | None:
    """Aktiv versjon fra `policy_hode` (plain SELECT). Runtime har KUN SELECT på
    `policy_hode` (V10) — den kan verken låse eller skrive pekeren, og skal ikke:
    den ekte serialiseringen er den herdede `aktiver_policy` (kjører som
    policy-eieren, låser hoderaden og avviser en flyttet base med
    `serialization_failure`). Finnes ikke hoderaden (helt ny policy), er basen
    deny-all og funksjonen oppretter ankerraden idempotent ved aktivering — vi
    oppretter den ALDRI her (en forkastet runde skal ikke etterlate en tom
    hoderad)."""
    rad = conn.execute(
        "SELECT aktiv_versjon FROM policy_hode WHERE tenant=%s AND policy_id=%s",
        (tenant, policy_id)).fetchone()
    return rad[0] if rad else None


# --------------------------------------------------------------------------
# 1. Runde-åpning.
# --------------------------------------------------------------------------

def opprett_aktiveringsrunde(conn: psycopg.Connection, *, tenant: str,
                             utkast_id: str, aktor: str, request_id: str,
                             naa) -> dict:
    """Åpne en aktiveringsrunde for et VALIDERT utkast. Utleder diff + klasse
    under `policy_hode`-låsen og fryser ALT i runden. Returnerer det
    godkjennerne skal se (diff, risikoklasse, påkrevd antall). Kaster
    `Aktiveringsfeil`. Kalleren eier transaksjonen."""
    sett_kontekst(conn, tenant, aktor, request_id)

    utk = conn.execute(
        "SELECT policy_id, innhold, innholds_hash, status FROM policyutkast"
        " WHERE tenant=%s AND utkast_id=%s FOR UPDATE",
        (tenant, utkast_id)).fetchone()
    if utk is None:
        raise Aktiveringsfeil("utkast_ukjent")
    policy_id, ny_innhold, innholds_hash, status = utk
    if status != "validert":
        # Kun et validert utkast (med frosset innholds_hash) kan aktiveres.
        raise Aktiveringsfeil("utkast_ikke_validert", f"status={status}")
    if innholds_hash is None:
        raise Aktiveringsfeil("utkast_ikke_validert", "mangler innholds_hash")

    aktiv_versjon = _hode_aktiv_versjon(conn, tenant, policy_id)
    base_innhold, base_hash = _base(conn, tenant, policy_id, aktiv_versjon)
    v = _vurder(base_innhold, base_hash, ny_innhold)

    runde = int(conn.execute(
        "SELECT coalesce(max(runde),0)+1 FROM aktiveringsrunde"
        " WHERE tenant=%s AND utkast_id=%s", (tenant, utkast_id)).fetchone()[0])
    try:
        conn.execute(
            "INSERT INTO aktiveringsrunde (tenant, utkast_id, runde, status,"
            " diff_hash, utkast_innholds_hash, base_policy_hash, risikoklasse,"
            " klassifisering_hash, klassifikatorversjon, policyskjema_versjon,"
            " motor_semantikkversjon, deny_all_hash, deny_all_versjon,"
            " pakrevd_antall_godkjennere, utloper)"
            " VALUES (%s,%s,%s,'apen',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (tenant, utkast_id, runde, v["diff_hash"], innholds_hash,
             v["base_policy_hash"], v["risikoklasse"], v["klassifisering_hash"],
             v["klassifikatorversjon"], POLICYSKJEMA_VERSJON,
             semantikk.MOTOR_SEMANTIKKVERSJON, semantikk.DENY_ALL_HASH,
             semantikk.DENY_ALL_VERSJON, v["pakrevd_antall_godkjennere"],
             naa + RUNDE_TTL))
    except psycopg.errors.UniqueViolation:
        # en_aktiv_aktiveringsrunde: allerede en åpen/klar runde for utkastet.
        raise Aktiveringsfeil("runde_allerede_aapen") from None

    return {
        "utkast_id": utkast_id, "policy_id": policy_id, "runde": runde,
        "diff": v["diff"], "diff_hash": v["diff_hash"],
        "risikoklasse": v["risikoklasse"],
        "klassifisering_hash": v["klassifisering_hash"],
        "pakrevd_antall_godkjennere": v["pakrevd_antall_godkjennere"],
        "base_versjon": aktiv_versjon,
    }


# --------------------------------------------------------------------------
# 2+3. Attestering + (ved terskel) aktivering.
# --------------------------------------------------------------------------

def attester_aktivering(conn: psycopg.Connection, mac_register, *,
                        tenant: str, aktor: str, request_id: str,
                        utkast_id: str, forventet_diff_hash: str,
                        idempotency_key: str, input_hash: str, naa) -> dict:
    """En godkjenner attesterer diffen. Når terskelen (V6) er nådd, aktiveres
    policyen via den herdede funksjonen — etter en rekalk under låsen som
    avviser en flyttet base (rebasering). Eier transaksjonen. Kaster
    `Aktiveringsfeil`."""
    sett_kontekst(conn, tenant, aktor, request_id)

    # --- 1. Idempotens: serialiser per nøkkel og claim i eiertransaksjonen ---
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                 (f"{tenant}\x1fpolidem\x1f{idempotency_key}",))
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
            raise Aktiveringsfeil("db_utilgjengelig")
        lagret_hash, istatus, respons = eksist
        if lagret_hash != input_hash:
            conn.rollback()
            raise Aktiveringsfeil("idempotenskonflikt")
        if istatus == "ferdig":
            conn.rollback()
            return {**respons, "replay": True}
        conn.execute("UPDATE idempotens SET request_id=%s, ts=now()"
                     " WHERE tenant=%s AND nokkel=%s",
                     (request_id, tenant, idempotency_key))

    # --- 2. Lås utkastet ---------------------------------------------------
    utk = conn.execute(
        "SELECT policy_id, innhold, innholds_hash, status, opprettet_av"
        " FROM policyutkast WHERE tenant=%s AND utkast_id=%s FOR UPDATE",
        (tenant, utkast_id)).fetchone()
    if utk is None:
        conn.rollback()
        raise Aktiveringsfeil("utkast_ukjent")
    policy_id, ny_innhold, innholds_hash, ustatus, opprettet_av = utk
    if ustatus not in ("validert", "godkjent"):
        conn.rollback()
        raise Aktiveringsfeil("utkast_ulovlig_tilstand", f"status={ustatus}")

    # --- 3. REAUTORISERING ETTER LÅSEN (fail-closed, ingen fallback) -------
    med = conn.execute(
        "SELECT roller, authz_version FROM brukermedlemskap WHERE tenant=%s"
        " AND bruker_id=%s AND aktiv", (tenant, aktor)).fetchone()
    if med is None:
        conn.rollback()
        raise Aktiveringsfeil("mangler_medlemskap")
    roller = list(med[0])
    authz_version = int(med[1])
    if _AKTIVER_SCOPE not in scopes_for_roller(roller):
        conn.rollback()
        raise Aktiveringsfeil("scope_mangler")
    rolle = _revisjonsrolle(roller)

    # --- 4. Lås hodet + den aktive runden ----------------------------------
    aktiv_versjon = _hode_aktiv_versjon(conn, tenant, policy_id)
    runde = conn.execute(
        "SELECT runde, status, diff_hash, klassifisering_hash, risikoklasse,"
        " base_policy_hash, klassifikatorversjon, motor_semantikkversjon,"
        " pakrevd_antall_godkjennere, utloper FROM aktiveringsrunde"
        " WHERE tenant=%s AND utkast_id=%s AND status IN ('apen','klar')"
        " FOR UPDATE", (tenant, utkast_id)).fetchone()
    if runde is None:
        conn.rollback()
        raise Aktiveringsfeil("ingen_aktiv_runde")
    (r_nr, r_status, r_diff_hash, r_klass_hash, r_risiko, r_base_hash,
     r_klassver, r_motorver, r_pakrevd, r_utloper) = runde
    if r_utloper <= naa:
        conn.rollback()
        raise Aktiveringsfeil("runde_utlopt")

    # --- 5. Godkjenneren attesterer DIFFEN, ikke versjonsnummeret (v5 §2) ---
    if forventet_diff_hash != r_diff_hash:
        conn.rollback()
        raise Aktiveringsfeil("diff_utdatert")

    # --- 6. Bygg + MAC-signer konvolutten fra LÅSTE data -------------------
    er_forfatter = (aktor == opprettet_av)
    konvolutt = {
        "konvolutt_type": KONVOLUTT_TYPE, "konvoluttversjon": KONVOLUTTVERSJON,
        "tenant": tenant, "utkast_id": utkast_id, "policy_id": policy_id,
        "runde": r_nr, "diff_hash": r_diff_hash,
        "klassifisering_hash": r_klass_hash, "risikoklasse": r_risiko,
        "base_policy_hash": r_base_hash, "bruker_id": aktor,
        "er_forfatter": er_forfatter, "rolle": rolle,
        "authz_version": authz_version,
        "jti": f"{tenant}-{utkast_id}-r{r_nr}-{aktor}".ljust(22, "j"),
        "utloper": r_utloper.isoformat()}
    mac_key_id, mac = mac_register.signer(konvolutt)
    konvolutt["mac"], konvolutt["mac_key_id"] = mac, mac_key_id

    if not mac_register.verifiser(konvolutt, mac, mac_key_id):
        conn.rollback()
        raise Aktiveringsfeil("sikkerhet", "mac_ugyldig")
    _forventet = {"konvolutt_type": KONVOLUTT_TYPE, "tenant": tenant,
                  "utkast_id": utkast_id, "policy_id": policy_id, "runde": r_nr,
                  "diff_hash": r_diff_hash, "klassifisering_hash": r_klass_hash,
                  "risikoklasse": r_risiko, "base_policy_hash": r_base_hash,
                  "bruker_id": aktor, "er_forfatter": er_forfatter}
    for felt in _BINDINGSFELT:
        if konvolutt.get(felt) != _forventet[felt]:
            conn.rollback()
            raise Aktiveringsfeil("sikkerhet", f"bindingsavvik:{felt}")

    konvolutt_hash = hashlib.sha256(kanonisk_konvolutt(konvolutt)).hexdigest()

    # --- 7. Skriv attestasjonen (append-only; trigger vokter er_forfatter) --
    try:
        conn.execute(
            "INSERT INTO aktiveringsattestasjon (tenant, utkast_id, runde,"
            " bruker_id, rolle, authz_version, er_forfatter, diff_hash,"
            " klassifisering_hash, risikoklasse, konvoluttversjon,"
            " konvolutt_hash, mac, mac_key_id, jti, utloper)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (tenant, utkast_id, r_nr, aktor, rolle, authz_version,
             er_forfatter, r_diff_hash, r_klass_hash, r_risiko,
             KONVOLUTTVERSJON, konvolutt_hash, mac, mac_key_id,
             konvolutt["jti"], r_utloper))
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        raise Aktiveringsfeil("allerede_attestert") from None

    # --- 8. Terskel (V6): antall ≥ påkrevd OG minst én ikke-forfatter -------
    rader = conn.execute(
        "SELECT bruker_id, er_forfatter FROM aktiveringsattestasjon"
        " WHERE tenant=%s AND utkast_id=%s AND runde=%s",
        (tenant, utkast_id, r_nr)).fetchall()
    antall = len(rader)
    ikke_forfatter = sum(1 for _b, ef in rader if not ef)
    if antall < r_pakrevd or ikke_forfatter < 1:
        return _fullfor(conn, tenant, idempotency_key, {
            "utfall": "venter_godkjennere", "utkast_id": utkast_id,
            "runde": r_nr, "antall": antall,
            "gjenstaar": max(0, r_pakrevd - antall),
            "mangler_uavhengig": ikke_forfatter < 1})

    # --- 9. REKALK UNDER LÅSEN: har basen/semantikken flyttet seg? ---------
    # aktiv_versjon ble lest FOR UPDATE i steg 4, så settet er stabilt.
    base_innhold, base_hash = _base(conn, tenant, policy_id, aktiv_versjon)
    v = _vurder(base_innhold, base_hash, ny_innhold)
    if (v["diff_hash"] != r_diff_hash
            or v["base_policy_hash"] != r_base_hash
            or v["klassifisering_hash"] != r_klass_hash
            or v["risikoklasse"] != r_risiko):
        # En konkurrerende aktivering (eller redigert base) flyttet grunnlaget
        # godkjennerne så → runden kanselleres, rebasering kreves.
        conn.execute("UPDATE aktiveringsrunde SET status='kansellert'"
                     " WHERE tenant=%s AND utkast_id=%s AND runde=%s",
                     (tenant, utkast_id, r_nr))
        return _fullfor(conn, tenant, idempotency_key, {
            "utfall": "rebasering_kreves", "utkast_id": utkast_id})
    if (v["klassifikatorversjon"] != r_klassver
            or semantikk.MOTOR_SEMANTIKKVERSJON != r_motorver):
        # Motorsemantikken (og dermed klassifikatoren) endret seg siden runden
        # åpnet → klassifiseringen godkjennerne så er stale. Ny runde kreves.
        conn.execute("UPDATE aktiveringsrunde SET status='kansellert'"
                     " WHERE tenant=%s AND utkast_id=%s AND runde=%s",
                     (tenant, utkast_id, r_nr))
        return _fullfor(conn, tenant, idempotency_key, {
            "utfall": "semantikk_endret", "utkast_id": utkast_id})

    # --- 10. Aktiver via den herdede funksjonen (deaktiver+innsett i én tx) -
    op_id = f"aktiver-{utkast_id}-r{r_nr}"
    if ustatus == "validert":
        conn.execute("UPDATE policyutkast SET status='godkjent'"
                     " WHERE tenant=%s AND utkast_id=%s", (tenant, utkast_id))
    if r_status == "apen":
        conn.execute("UPDATE aktiveringsrunde SET status='klar'"
                     " WHERE tenant=%s AND utkast_id=%s AND runde=%s",
                     (tenant, utkast_id, r_nr))
    try:
        ny_versjon = conn.execute(
            "SELECT aktiver_policy(%s,%s,%s::jsonb,%s,%s,%s)",
            (tenant, policy_id, json.dumps(ny_innhold), innholds_hash,
             _AKTIV_STATUS, aktiv_versjon)).fetchone()[0]
    except psycopg.errors.SerializationFailure:
        # En konkurrerende aktivering vant kappløpet i vinduet mellom rekalk-
        # lesningen og funksjonens egen lås. Funksjonen er serialiseringspunktet
        # (V10) — den flyttede basen betyr rebasering.
        conn.rollback()
        raise Aktiveringsfeil("rebasering_kreves") from None

    # V1-rekkefølge: runde `brukt` (m/ op-id) FØR utkastet lukkes.
    conn.execute(
        "UPDATE aktiveringsrunde SET status='brukt', decision_operation_id=%s"
        " WHERE tenant=%s AND utkast_id=%s AND runde=%s",
        (op_id, tenant, utkast_id, r_nr))
    conn.execute("UPDATE policyutkast SET status='aktivert'"
                 " WHERE tenant=%s AND utkast_id=%s", (tenant, utkast_id))

    return _fullfor(conn, tenant, idempotency_key, {
        "utfall": "aktivert", "utkast_id": utkast_id, "policy_id": policy_id,
        "versjon": ny_versjon, "runde": r_nr, "risikoklasse": r_risiko})


def _revisjonsrolle(roller) -> str:
    """En ekte rolle som gir `policy:activate` (for revisjonssporet). Scope er
    allerede bevist; vi velger den FØRSTE rollen som faktisk bærer scopet, så
    audit-rollen aldri er en rolle uten aktiveringsfullmakt."""
    for r in roller:
        if _AKTIVER_SCOPE in scopes_for_roller([r]):
            return r
    return roller[0]


def _fullfor(conn, tenant, idempotency_key, res: dict) -> dict:
    """Lagre den idempotente responsen og commit. Replay med samme nøkkel og
    input får NØYAKTIG denne responsen — aldri en ny aktivering."""
    conn.execute("UPDATE idempotens SET status='ferdig', respons=%s"
                 " WHERE tenant=%s AND nokkel=%s",
                 (json.dumps(res, ensure_ascii=False), tenant, idempotency_key))
    conn.commit()
    return res
