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

from datetime import timedelta

import psycopg

from db.pg import sett_kontekst
from policy_validator.schema import IKKE_MENNESKELIG_GODKJENNBARE

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
