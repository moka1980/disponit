"""M-37-arbeideren: claim-løkken. EGEN PROSESS, aldri i API-prosessen.

Arbeideren er en NY vei inn i databasen, og får derfor NØYAKTIG de samme
vaktene som de to eksisterende (brief-spørsmål 1):

  * `sett_kontekst()` som FØRSTE databaseoperasjon i behandlings-
    transaksjonen — tenant hentes fra den claimede sakens rad.
  * `Transaksjonsvakt` gjenbrukes: én eier av commit per flyt. Her er det
    behandlingsløkken, aldri en reparasjonshandler.
  * Runtime-rollens eksisterende rettigheter utvides ikke ut over det
    migrasjon 005 gir på `oppdrag`/`reparasjonsoperasjoner` og EXECUTE på
    de herdede funksjonene.

TRE TRANSAKSJONER, ikke én, og grensene er ikke tilfeldige:

  (a) CLAIM     — kort. `claim_neste_sak()` er atomisk i én setning.
  (b) PLANLEGG  — dekrypter, klassifiser, registrer reparasjonen, utsted
                  arbeidskapabilitet. Commit FØR nettverkskallet.
  (c) FULLFØR   — fencing-sjekk, oppdragsrad, statusskifte.

Grunnen til at (b) committes før nettverkskallet er konkret: en åpen
transaksjon over et HTTP-kall holder radlåser mens en fremmed prosess
bruker den tiden den vil. Prisen er at leasen kan gå tapt i mellomtiden —
og det er nettopp derfor hver eneste skriving i (c) bærer full
fencing-WHERE (claim_id OG generasjon OG status OG levende lease). Null
rader truffet == tapt lease == full abort, ingen statusskriv, ingen
oppdragsopprettelse.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import psycopg

from api.kjerne import Transaksjonsvakt
from db import kryptering
from db.pg import koble, sett_kontekst
from policy_validator.engine import les_policyref
from policy_validator.schema import valider_policy

import oppdragskontrakt as oppdragsskjema

from . import reparasjoner
from .taksonomi import PLATTFORM_MAKS_FORSOK, grunnkode_for

AKTOR = "m37-arbeider"

#: Standard lease. Klemmes uansett av `claim_neste_sak` til [30, 600] —
#: tallet her er et ønske, databasen er grensen.
LEASE_S = 120

#: Hvor lenge en arbeidskapabilitet lever. `utsted_arbeidskapabilitet`
#: klemmer den mot claim_utloper (GO-vilkår V1), så dette er et tak og
#: aldri en forlengelse.
KAPABILITET_S = 60

#: Frister på oppdrag (v4-delta pkt. 2). To frister, ikke én: den første
#: er siste tidspunkt et resultat kan endre status AUTOMATISK, den andre er
#: siste tidspunkt en signert kvittering mottas som EVIDENS.
UTFORELSESFRIST_S = 24 * 3600
EVIDENSFRIST_S = 30 * 24 * 3600


class Leasetap(RuntimeError):
    """Fencing-WHERE traff null rader. Alt arbeid på saken avbrytes."""


class _Kompensasjonsklasse:
    """Klassifiseringen kompensasjonsveien registreres under.

    Kompensasjon går ikke gjennom reparasjonsregisteret — den er ikke en
    reparasjon av en feil, men en reversering av en effekt. Den trenger
    likevel en registrert klassifisering, fordi det er DEN
    `utsted_arbeidskapabilitet` utleder `tillatt_handling` fra. Uten den
    ville arbeideren måttet sende ønsket handling som parameter, og hele
    parameterherdingen fra v4-delta pkt. 1 ville vært borte.
    """
    handler_id = "kompensasjon"
    versjon = "1"
    id_med_versjon = "kompensasjon@1"


class _Klasse:
    __slots__ = ("handler", "kategori", "grunnkode")

    def __init__(self, handler, kategori, grunnkode):
        self.handler, self.kategori = handler, kategori
        self.grunnkode = grunnkode


_KOMPENSASJONSKLASSE = _Klasse(_Kompensasjonsklasse(), "ukjent",
                               "kompenserende_reversering")


@dataclass
class Beslutningssvar:
    """Svaret fra `POST /v1/beslutning`, slik arbeideren trenger det."""
    http: int
    beslutning: str | None
    kropp: dict = field(default_factory=dict)


class Beslutningsklient:
    """Arbeiderens ENESTE vei til å be om en handling.

    Den er en egen klasse — og injiseres — av to grunner. Den ene er
    testbarhet. Den andre er viktigere: den gjør det synlig i typene at
    arbeideren ikke har noen annen kanal. Det finnes ingen
    `conn.execute("INSERT INTO ...")` som utfører en forretningshandling,
    fordi det ikke finnes noen forretningshandling arbeideren kan utføre.
    """

    def __init__(self, basis_url: str) -> None:
        self.basis_url = basis_url.rstrip("/")

    def beslutt(self, *, kapabilitet_jti: str, policy_id: str, event: dict,
                idempotency_key: str) -> Beslutningssvar:
        import httpx
        r = httpx.post(
            f"{self.basis_url}/v1/beslutning",
            headers={"authorization": f"Kapabilitet {kapabilitet_jti}",
                     "idempotency-key": idempotency_key,
                     "content-type": "application/json"},
            content=json.dumps({"policy_id": policy_id, "event": event},
                               ensure_ascii=False).encode("utf-8"),
            timeout=30.0)
        try:
            kropp = r.json()
        except Exception:
            kropp = {}
        return Beslutningssvar(r.status_code, kropp.get("beslutning"), kropp)


@dataclass
class Sak:
    """Metadataene claim-funksjonen returnerer. ALDRI payload."""
    tenant: str
    id: int
    handling: str
    kategori: str
    loggpost_id: int
    claim_generation: int
    claim_utloper: datetime
    forsok: int
    maks_auto_forsok_snapshot: int
    #: 'ny' = første behandling, 'fase2' = saken har en verifikasjons-
    #: generasjon bak seg. Kommer fra claim-funksjonen, ikke fra en
    #: utledning her — fasen er eksplisitt tilstand i databasen.
    fase: str = "ny"
    verification_generation: int = 0


@dataclass
class Behandlingsresultat:
    sak: Sak
    utfall: str
    grunn: str
    repair_operation_id: str | None = None
    oppdrag_id: int | None = None


# ---------------------------------------------------------------------------
# Hjelpere
# ---------------------------------------------------------------------------

def _claim_id() -> str:
    """CSPRNG, 32 hex-tegn. Formatet håndheves av `claim_neste_sak`, som
    avviser alt annet enn `^[0-9a-f]{32,}$` — en gjettbar claim_id er ikke
    et fencing-token."""
    return secrets.token_hex(16)


def _historikk(conn, sak: Sak, hendelse: str, detalj: dict | None = None,
               claim_id: str | None = None) -> None:
    """Én ekstra historikkrad. ALDRI en oppdatering av en gammel.

    Statusskiftene skriver historikk av seg selv (triggeren fra 003/005).
    Denne er for hendelsene som IKKE er statusskifter, og som ellers ville
    vært usynlige: at policyen er endret siden saken oppsto, at DEK-en er
    borte, at en generasjon ble blokkert.
    """
    conn.execute(
        "INSERT INTO unntak_historikk (tenant, unntak_id, hendelse, aktor,"
        " request_id, claim_id, claim_generation, detalj)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (sak.tenant, sak.id, hendelse, AKTOR, claim_id, claim_id,
         sak.claim_generation,
         json.dumps(detalj or {}, ensure_ascii=False)))


def _fencing(conn, sak: Sak, claim_id: str, sql_hale: str,
             args: tuple = ()) -> int:
    """Enhver skriving på saken, med FULL fencing-WHERE.

    Alle fire leddene må stå: claim_id OG generasjon OG status OG levende
    lease. Utelates ett av dem, er dette bare et oppslag med ekstra tekst.
    Særlig generasjonen: `claim_id` alene er ikke fencing, fordi den samme
    arbeideren kan re-claime samme sak og da ville et gammelt token vært
    gyldig igjen.
    """
    res = conn.execute(
        f"UPDATE unntak SET {sql_hale}"
        "  WHERE tenant=%s AND id=%s AND claim_id=%s AND claim_generation=%s"
        "    AND status='under_behandling' AND claim_utloper > now()",
        (*args, sak.tenant, sak.id, claim_id, sak.claim_generation))
    return res.rowcount


def _krev_fencing(conn, sak: Sak, claim_id: str, sql_hale: str,
                  args: tuple = ()) -> None:
    if _fencing(conn, sak, claim_id, sql_hale, args) != 1:
        raise Leasetap(
            f"lease tapt for {sak.tenant}/{sak.id} (generasjon"
            f" {sak.claim_generation}) — ingen skriving utført")


# ---------------------------------------------------------------------------
# (a) Claim
# ---------------------------------------------------------------------------

def claim(conn: psycopg.Connection, claim_id: str,
          lease_s: int = LEASE_S) -> Sak | None:
    """Én sak, eller None. Egen kort transaksjon.

    `disponit.aktor` MÅ settes før kallet: claim-funksjonen oppdaterer
    `unntak`, og historikktriggeren nekter å skrive en historikkrad uten
    aktør. Tenant kan ikke settes her — den er ukjent til claimen har
    skjedd, og det er hele grunnen til at dispatcher-policyen i migrasjon
    005 finnes.
    """
    conn.execute("SELECT set_config('disponit.aktor', %s, true),"
                 "       set_config('disponit.request_id', %s, true)",
                 (AKTOR, claim_id))
    rad = conn.execute(
        "SELECT tenant, id, handling, kategori, loggpost_id, claim_generation,"
        " claim_utloper, forsok, maks_auto_forsok_snapshot, fase,"
        " verification_generation"
        "  FROM claim_neste_sak(%s, %s)", (claim_id, lease_s)).fetchone()
    conn.commit()
    return Sak(*rad) if rad else None


def frigi_utlopte(conn: psycopg.Connection) -> int:
    """Saker med utløpt lease tilbake til køen.

    Dette er gjenopptaksveien etter et krasj mellom (a) og (b): saken står
    `under_behandling` uten at noen jobber med den. Statusmaskinen i
    migrasjon 005 tillater `under_behandling -> ny` KUN når leasen faktisk
    er utløpt, så denne funksjonen kan ikke rive en sak fra en arbeider som
    fortsatt lever — heller ikke ved en programmeringsfeil her.
    """
    conn.execute("SELECT set_config('disponit.aktor', %s, true)", (AKTOR,))
    # Gjennom den herdede funksjonen, ikke som en rå UPDATE herfra.
    # Første utkast gjorde det siste, og den traff ALLTID null rader:
    # `unntak` har RLS med FORCE, og opprydningen kan ikke sette
    # `disponit.tenant` på forhånd — den vet jo ikke hvilke tenanter som
    # har hengende saker. Det så ut som en gjenopptaksvei som kjørte, og
    # var en som aldri gjorde noe. Fanget av testen, ikke av lesing.
    antall = conn.execute("SELECT frigi_utlopte_claims()").fetchone()[0]
    conn.commit()
    return int(antall)


# ---------------------------------------------------------------------------
# (b) Planlegg
# ---------------------------------------------------------------------------

def _hent_payload(conn, sak: Sak) -> dict | None:
    """Dekryptert saksgrunnlag, eller None hvis DEK-en er borte.

    ALDRI til disk, aldri til logg. Verdien returneres og lever i minnet
    så lenge behandlingen varer.
    """
    rad = conn.execute(
        "SELECT payload_kryptert, key_id, nonce FROM unntak"
        " WHERE tenant=%s AND id=%s", (sak.tenant, sak.id)).fetchone()
    if rad is None:
        return None
    ct, key_id, nonce = rad
    nokkel = conn.execute(
        "SELECT wrapped_dek FROM tenant_nokler WHERE tenant=%s AND key_id=%s",
        (sak.tenant, key_id)).fetchone()
    if nokkel is None or nokkel[0] is None:
        return None            # crypto-shredding har vært her
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
    dek = kryptering._pakk_ut((key_id, nokkel[0]), sak.tenant)[1]
    try:
        return kryptering.dekrypter(dek, ct, nonce, sak.tenant, key_id)
    except Exception:
        return None


def _aktiv_policy(conn, sak: Sak) -> tuple[dict, str] | None:
    """Den AKTIVE policyen for saken, revalidert.

    Behandlingen re-evaluerer alltid mot aktiv policy (v2-delta pkt. 6).
    Snapshotet på saken styrer retry, ikke hvilke regler som gjelder nå —
    ellers ville en rettet policy ikke fått virkning på saker som allerede
    lå i kø, som er nettopp de sakene rettelsen ofte er til for.
    """
    rad = conn.execute(
        "SELECT r.policy_id FROM revisjonslogg r"
        " WHERE r.tenant=%s AND r.id=%s", (sak.tenant, sak.loggpost_id)
    ).fetchone()
    # Kolonnen bærer en POLICYREFERANSE, ikke en policy-id. Uten
    # `les_policyref` traff oppslaget aldri noe, og arbeideren
    # klassifiserte HVER sak som `manuell` med `aktiv_policy_utilgjengelig`
    # — altså behandlet den ingenting i det hele tatt.
    ref = les_policyref(rad[0]) if rad else None
    if ref is None:
        return None
    p = conn.execute(
        "SELECT innhold, innholds_hash FROM policyer"
        " WHERE tenant=%s AND policy_id=%s AND aktiv",
        (sak.tenant, ref[0])).fetchone()
    if p is None or not isinstance(p[0], dict):
        return None
    if valider_policy(p[0]):
        return None            # korrupt aktiv policy: ingen automatikk
    return p[0], p[1]


def planlegg(conn: psycopg.Connection, sak: Sak, claim_id: str
             ) -> tuple[reparasjoner.Reparasjonsplan, str | None]:
    """-> (plan, repair_operation_id). Egen transaksjon, committes her.

    `sett_kontekst` er FØRSTE databaseoperasjon. Ikke rett før første
    SELECT, men først: den dagen noen legger inn et oppslag over, ligger
    konteksten allerede foran det. Samme plassering og samme begrunnelse
    som i `api.app._unntak` etter korreksjonen på PR #7.
    """
    vakt = Transaksjonsvakt(conn)
    sett_kontekst(vakt, sak.tenant, AKTOR, claim_id)

    if sak.fase == "fase2":
        return _fase2(conn, vakt, sak, claim_id)

    payload = _hent_payload(vakt, sak)
    if payload is None:
        _historikk(vakt, sak, "dek_utilgjengelig", claim_id=claim_id)
        _krev_fencing(vakt, sak, claim_id, "status='manuell'")
        conn.commit()
        return reparasjoner.Reparasjonsplan("manuell", "dek_utilgjengelig"), None

    aktiv = _aktiv_policy(vakt, sak)
    if aktiv is None:
        _historikk(vakt, sak, "frist_utlopt",
                   {"grunn": "aktiv_policy_utilgjengelig"}, claim_id=claim_id)
        _krev_fencing(vakt, sak, claim_id, "status='manuell'")
        conn.commit()
        return reparasjoner.Reparasjonsplan(
            "manuell", "aktiv_policy_utilgjengelig"), None
    policy, aktiv_hash = aktiv

    lagret_hash = vakt.execute(
        "SELECT policy_content_hash FROM unntak WHERE tenant=%s AND id=%s",
        (sak.tenant, sak.id)).fetchone()[0]
    if lagret_hash != aktiv_hash:
        # Ikke en feil — en OPPLYSNING. Saken behandles mot aktiv policy,
        # og historikken viser at grunnlaget har endret seg siden den
        # oppsto. Uten raden er den forskjellen usynlig i ettertid.
        _historikk(vakt, sak, "policy_endret_siden_opprettelse",
                   {"ved_opprettelse": lagret_hash, "aktiv": aktiv_hash},
                   claim_id=claim_id)

    # --- Kompenserende reversering FØR ordinær klassifisering -----------
    # Rekkefølgen er bindende: bærer saken spor av en DELVIS UTFØRT
    # handling, er det ikke en reparasjon som mangler — det er en effekt
    # som må nulles. Kjørte vi R1 først, ville vi bedt om å utføre
    # handlingen på nytt oppå en halvveis utført handling.
    if reparasjoner.krever_kompensasjon(payload):
        plan = reparasjoner.planlegg_kompensasjon(
            policy, opprinnelig_handling=sak.handling, unntak_id=sak.id,
            loggpost_id=sak.loggpost_id, sak_ts=_sak_ts(vakt, sak),
            naa=datetime.now(timezone.utc), payload=payload)
        _historikk(vakt, sak, "klassifisert",
                   {"vei": "kompensasjon", "utfall": plan.utfall,
                    "grunn": plan.grunn}, claim_id=claim_id)
        if plan.utfall != "oppdrag":
            _krev_fencing(vakt, sak, claim_id, "status='manuell'")
            conn.commit()
            return plan, None
        # Kompensasjonen har sin EGEN stabile identitet (v2-delta pkt. 4),
        # avledet av kompensasjonsnøkkelen og ikke av en handler.
        inp_hash = reparasjoner.input_hash(plan.reparasjonsinput)
        rid = reparasjoner.repair_operation_id(
            sak.tenant, sak.id, "kompensasjon@1", plan.maalhandling, inp_hash)
        _registrer_reparasjon(vakt, sak, _KOMPENSASJONSKLASSE, plan, rid,
                              inp_hash, claim_id)
        conn.commit()
        return plan, rid

    grunnkode = grunnkode_for(sak.kategori, payload.get("begrunnelse") or [])
    kl = reparasjoner.klassifiser(
        sak.kategori, grunnkode,
        frozenset((policy.get("unntak") or {}).get("kategorier") or ()))
    _historikk(vakt, sak, "klassifisert",
               {"utfall": kl.utfall, "grunn": kl.grunn,
                "handler": kl.handler.id_med_versjon if kl.handler else None,
                "grunnkode": grunnkode}, claim_id=claim_id)

    # --- Tofaseveien: R1 ber om VERIFIKASJON før noen ny beslutning ----
    # Rekkefølgen er hele PR-007: en ny beslutning kan ikke bli TILLAT før
    # det manglende beviset finnes, og beviset kan ikke skaffes gjennom en
    # utførelses-outbox som først opprettes ETTER en TILLAT.
    if kl.utfall == "behandle" and kl.handler is not None \
            and kl.handler.handler_id == reparasjoner.R1.handler_id:
        plan = reparasjoner.planlegg_verifikasjon(kl, payload)
        if plan.utfall == "verifikasjon":
            return _start_fase1(conn, vakt, sak, claim_id, kl, plan)
        _historikk(vakt, sak, "klassifisert",
                   {"vei": "r1_avvist", "grunn": plan.grunn},
                   claim_id=claim_id)
        _krev_fencing(vakt, sak, claim_id, "status='manuell'")
        conn.commit()
        return plan, None

    plan = reparasjoner.planlegg(kl, payload)
    if plan.utfall == "manuell":
        _krev_fencing(vakt, sak, claim_id, "status='manuell'")
        conn.commit()
        return plan, None
    if plan.utfall == "lost":
        _krev_fencing(vakt, sak, claim_id, "status='løst'")
        conn.commit()
        return plan, None
    if plan.utfall == "ko":
        # Tilbake til køen NÅ, ikke om to minutter. Statusmaskinen krever
        # at leasen er utløpt for `under_behandling -> ny`, så leasen
        # avkortes først — i samme transaksjon, med fencing på begge
        # skrivingene.
        _krev_fencing(vakt, sak, claim_id, "claim_utloper=now() - interval '1 s'")
        vakt.execute(
            "UPDATE unntak SET status='ny', claim_id=NULL"
            " WHERE tenant=%s AND id=%s AND claim_id=%s"
            "   AND claim_generation=%s AND status='under_behandling'",
            (sak.tenant, sak.id, claim_id, sak.claim_generation))
        conn.commit()
        return plan, None

    # --- Oppdragsveien: registrer reparasjonen og utsted kapabiliteten ---
    inp_hash = reparasjoner.input_hash(plan.reparasjonsinput)
    rid = reparasjoner.repair_operation_id(
        sak.tenant, sak.id, kl.handler.id_med_versjon, plan.maalhandling,
        inp_hash)
    _registrer_reparasjon(vakt, sak, kl, plan, rid, inp_hash, claim_id)
    conn.commit()
    return plan, rid


def _sak_ts(conn, sak: Sak):
    """Sakens opprettelsestidspunkt — fasit for `frist_sekunder`.

    Leses fra raden og ikke fra `claim_utloper` eller `now()`: fristen
    løper fra da hendelsen skjedde, ikke fra da vi rakk å se på den. Med
    claim-tidspunktet som utgangspunkt ville en sak som lå lenge i kø fått
    forlenget kompensasjonsfrist — altså det motsatte av hva fristen er til
    for.
    """
    rad = conn.execute("SELECT ts FROM unntak WHERE tenant=%s AND id=%s",
                       (sak.tenant, sak.id)).fetchone()
    return rad[0] if rad else None


def _registrer_reparasjon(conn, sak: Sak, kl, plan, rid: str, inp_hash: str,
                          claim_id: str) -> None:
    """Klassifiseringen som `utsted_arbeidskapabilitet` utleder fra.

    Rekkefølgen er bindende: kapabiliteten kan ikke utstedes før
    reparasjonen er registrert, fordi funksjonen henter `tillatt_handling`
    og `repair_operation_id` HERFRA. Det er hele parameterherdingen fra
    v4-delta pkt. 1 — arbeideren kan ikke sende inn ønsket handling, den
    kan bare registrere en klassifisering og be om å få utføre DEN.
    """
    eksisterende = conn.execute(
        "SELECT repair_operation_id, repair_generation FROM"
        " reparasjonsoperasjoner WHERE tenant=%s AND unntak_id=%s"
        "   AND status='aktiv'", (sak.tenant, sak.id)).fetchone()
    if eksisterende is not None:
        if eksisterende[0] == rid:
            return                      # samme reparasjon, idempotent replay
        # Ny generasjon: nye data har endret reparasjonen. Den gamle må
        # supersedes FØR den nye opprettes (v2-delta pkt. 4), og har den
        # gamle allerede et oppdrag som er plukket, kan utførelse pågå —
        # da opprettes ingen ny generasjon i det hele tatt (v3 pkt. 4).
        aktivt = conn.execute(
            "SELECT 1 FROM oppdrag WHERE tenant=%s AND repair_operation_id=%s"
            "   AND status IN ('plukket','utfort')",
            (sak.tenant, eksisterende[0])).fetchone()
        if aktivt is not None:
            _historikk(conn, sak, "generation_blokkert_aktiv_utforelse",
                       {"gammel": eksisterende[0]}, claim_id=claim_id)
            _krev_fencing(conn, sak, claim_id, "status='manuell'")
            raise Leasetap("generasjon blokkert av aktiv utførelse")
        conn.execute(
            "UPDATE oppdrag SET status='kansellert'"
            " WHERE tenant=%s AND repair_operation_id=%s AND status='opprettet'",
            (sak.tenant, eksisterende[0]))
        conn.execute(
            "UPDATE reparasjonsoperasjoner SET status='superseded'"
            " WHERE tenant=%s AND repair_operation_id=%s",
            (sak.tenant, eksisterende[0]))
        _historikk(conn, sak, "repair_generation_ny",
                   {"gammel": eksisterende[0], "ny": rid}, claim_id=claim_id)
        generasjon = eksisterende[1] + 1
    else:
        generasjon = 0

    conn.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant, unntak_id,"
        " repair_operation_id, repair_generation, handler_id, handler_versjon,"
        " maalhandling, input_hash, kategori, grunnkode)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (sak.tenant, sak.id, rid, generasjon, kl.handler.handler_id,
         kl.handler.versjon, plan.maalhandling, inp_hash, kl.kategori,
         kl.grunnkode))


# ---------------------------------------------------------------------------
# (c) Fullfør — kapabilitet, beslutning, oppdrag
# ---------------------------------------------------------------------------

def utsted_kapabilitet(conn: psycopg.Connection, sak: Sak, claim_id: str,
                       levetid_s: int = KAPABILITET_S) -> str | None:
    """En engangs, fencing-bundet arbeidskapabilitet. -> jti eller None.

    Arbeideren har INGEN egen identitet API-et kjenner. Et globalt
    M-37-token ville vært en fullmakt på tvers av alle tenanter — altså
    nøyaktig det null-fullmaktsprinsippet forbyr. Kapabiliteten er i stedet
    bundet til én sak, én claim-generasjon, én handling og ett lease-vindu.

    Legg merke til hva som IKKE er parametre: handlingen. Den utledes
    server-side fra den registrerte reparasjonsklassifiseringen (v4-delta
    pkt. 1). Den negative testen «arbeideren ber om en annen handling» er
    triviell fordi angrepet ikke lar seg uttrykke i signaturen.
    """
    jti = secrets.token_hex(16)
    rad = conn.execute(
        "SELECT jti FROM utsted_arbeidskapabilitet(%s,%s,%s,%s)",
        (claim_id, sak.claim_generation, jti, levetid_s)).fetchone()
    conn.commit()
    return rad[0] if rad else None


def fullfor(conn: psycopg.Connection, sak: Sak, claim_id: str,
            plan: reparasjoner.Reparasjonsplan, rid: str,
            svar: Beslutningssvar) -> Behandlingsresultat:
    """Oppdragsraden og statusskiftet, etter at API-et har svart.

    Alt her er fencing-bundet. Gikk leasen tapt mens vi ventet på svaret,
    treffer skrivingene null rader og hele behandlingen avbrytes — uten
    statusskriv, uten oppdrag. Den nye eieren av saken starter på nytt, og
    `repair_operation_id` er andre forsvarslinje: den er UNIQUE per tenant,
    så selv om fencingen skulle glippe kan det ikke bli to oppdrag.
    """
    vakt = Transaksjonsvakt(conn)
    sett_kontekst(vakt, sak.tenant, AKTOR, claim_id)

    if svar.beslutning != "TILLAT":
        # Motoren sa nei til reparasjonen. Det er et gyldig svar, ikke en
        # feil: reparasjonen er en ordinær, policystyrt handling og har
        # ingen forrang. Saken går til manuell kø.
        _historikk(vakt, sak, "frist_utlopt",
                   {"grunn": "reparasjon_ikke_tillatt",
                    "beslutning": svar.beslutning, "http": svar.http},
                   claim_id=claim_id)
        _krev_fencing(vakt, sak, claim_id, "status='manuell'")
        conn.commit()
        return Behandlingsresultat(sak, "manuell", "reparasjon_ikke_tillatt",
                                   rid)

    # Idempotent gjenopptak: krasjet vi mellom API-svaret og statusskrivet,
    # finnes oppdraget allerede. Da er dette et replay, ikke en ny
    # forretningshandling — nøkkelen stopper dubletten (v2-delta,
    # feilveitabellen: «Krasj etter oppdrag, før statusskriv»).
    finnes = vakt.execute(
        "SELECT id FROM oppdrag WHERE tenant=%s AND repair_operation_id=%s",
        (sak.tenant, rid)).fetchone()
    if finnes is not None:
        oppdrag_id = int(finnes[0])
    else:
        loggpost_id = svar.kropp.get("loggpost_id") or sak.loggpost_id
        oppdrag_id = _opprett_oppdrag(vakt, sak, plan, rid, loggpost_id)
        _historikk(vakt, sak, "oppdrag_opprettet",
                   {"oppdrag_id": oppdrag_id, "repair_operation_id": rid},
                   claim_id=claim_id)

    _krev_fencing(vakt, sak, claim_id, "status='venter_utførelse'")
    conn.commit()
    return Behandlingsresultat(sak, "venter_utførelse", "oppdrag_lagt_ut",
                               rid, oppdrag_id)


def _opprett_oppdrag(conn, sak: Sak, plan, rid: str, loggpost_id: int) -> int:
    """Oppdragsraden — kryptert, som alt annet saksinnhold.

    Eiermodulen får ALDRI ciphertext og aldri en nøkkel (v4-delta pkt. 4).
    Den får minimert klartekst fra API-laget, over den godkjente
    transporten, etter at API-et har dekryptert internt. Derfor krypteres
    payloaden her selv om den skal leses av en annen prosess: den ligger i
    databasen i mellomtiden, og en outbox i klartekst ville vært en kopi av
    saksgrunnlaget uten kryptering.
    """
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, sak.tenant)
    ct, nonce = kryptering.krypter(dek, plan.reparasjonsinput, sak.tenant,
                                   key_id)
    naa = datetime.now(timezone.utc)
    rad = conn.execute(
        "INSERT INTO oppdrag (tenant, unntak_id, loggpost_id,"
        " repair_operation_id, oppdragstype, handling, eiermodul,"
        " payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (sak.tenant, sak.id, loggpost_id, rid, plan.oppdragstype,
         plan.maalhandling, _eiermodul_for(plan.maalhandling), ct, key_id,
         nonce, naa + timedelta(seconds=UTFORELSESFRIST_S),
         naa + timedelta(seconds=EVIDENSFRIST_S))).fetchone()
    return int(rad[0])


def _eiermodul_for(handling: str) -> str:
    """Hvilken eiermodul oppdraget bindes til ved OPPRETTELSEN.

    Bindingen skjer her og ikke ved claim: `claim_neste_oppdrag` filtrerer
    på `eiermodul = modul_id`, så et ubundet oppdrag ville vært synlig for
    alle moduler. Kolonnen er NOT NULL nettopp for at «ubundet» ikke skal
    finnes som tilstand.
    """
    t = oppdragsskjema.type_for_handling(handling)
    return f"eiermodul:{t.navn}" if t is not None else "eiermodul:ukjent"


# ---------------------------------------------------------------------------
# Løkken
# ---------------------------------------------------------------------------

def behandle_en(conn: psycopg.Connection, klient: Beslutningsklient,
                policy_id_for: "callable | None" = None) -> Behandlingsresultat | None:
    """Claim én sak og kjør den helt ferdig. -> None når køen er tom.

    Feilhåndteringen har én regel: `Leasetap` er IKKE en feil. Den betyr at
    en annen arbeider har overtatt saken, og da er riktig oppførsel å
    slippe den — ikke å skrive noe, ikke å prøve igjen på den samme.
    """
    cid = _claim_id()
    sak = claim(conn, cid)
    if sak is None:
        return None
    try:
        plan, rid = planlegg(conn, sak, cid)
        if plan.utfall != "oppdrag":
            return Behandlingsresultat(sak, plan.utfall, plan.grunn)

        jti = utsted_kapabilitet(conn, sak, cid)
        if jti is None:
            raise Leasetap("kapabilitet kunne ikke utstedes — lease tapt")

        # NETTVERKSKALLET. Ingen åpen transaksjon her: en radlås holdt over
        # et fremmed HTTP-kall er en lås en fremmed prosess bestemmer
        # varigheten av.
        svar = klient.beslutt(
            kapabilitet_jti=jti,
            policy_id=(policy_id_for(sak) if policy_id_for else
                       _policy_id(conn, sak)),
            event=dict(plan.reparasjonsinput),
            idempotency_key=rid)
        return fullfor(conn, sak, cid, plan, rid, svar)
    except Leasetap:
        conn.rollback()
        return Behandlingsresultat(sak, "avbrutt", "lease_tapt")


def _policy_id(conn: psycopg.Connection, sak: Sak) -> str:
    """Policy-id-en API-et skal få — ikke referansen loggen bærer.

    Sendte vi referansen videre til `/v1/beslutning`, ville hver eneste
    reparasjon fått 404 `policy_ukjent`.

    KONTEKSTEN SETTES HER. Funksjonen kalles ETTER at `planlegg()` har
    committet, og `SET LOCAL` forsvinner ved commit — så tilkoblingen har
    ingen `disponit.tenant` på dette tidspunktet. Uten den ser row level
    security null rader, oppslaget ga `''`, og API-et svarte 404
    `policy_ukjent` på HVER reparasjon. Outbox-veien var dermed uoppnåelig
    i produksjon: ingen oppdrag kunne noensinne opprettes.

    Funnet ved å kjøre hele kjeden som tre prosesser — API, arbeider og
    eiermodul — ikke ved å lese. Enhetstestene så det aldri, fordi de
    kaller funksjonene på en tilkobling der konteksten alt er satt.
    """
    sett_kontekst(conn, sak.tenant, AKTOR, "policyoppslag")
    rad = conn.execute(
        "SELECT policy_id FROM revisjonslogg WHERE tenant=%s AND id=%s",
        (sak.tenant, sak.loggpost_id)).fetchone()
    conn.rollback()
    ref = les_policyref(rad[0]) if rad else None
    return ref[0] if ref else ""


def kjor(dsn: str, basis_url: str, *, intervall_s: float = 1.0,
         maks_runder: int | None = None) -> int:
    """Arbeiderprosessens hovedløkke. Kalles av systemd-unitten.

    `maks_runder` finnes for testene og for engangskjøringer i
    feilinjiseringen. I drift står den på None og løkken går til prosessen
    stoppes.
    """
    conn = koble(dsn)
    klient = Beslutningsklient(basis_url)
    behandlet = 0
    runder = 0
    try:
        while maks_runder is None or runder < maks_runder:
            runder += 1
            frigi_utlopte(conn)
            res = behandle_en(conn, klient)
            if res is None:
                if maks_runder is not None:
                    break
                time.sleep(intervall_s)
                continue
            behandlet += 1
    finally:
        conn.close()
    return behandlet


if __name__ == "__main__":       # pragma: no cover — systemd-inngangen
    import sys
    dsn = os.environ.get("DATABASE_URL") or ""
    url = os.environ.get("DISPONIT_API_URL", "http://127.0.0.1:8099")
    if not dsn:
        print("DATABASE_URL mangler", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(0 if kjor(dsn, url) >= 0 else 1)


# ---------------------------------------------------------------------------
# PR-007: fase 1 (verifikasjon) og fase 2 (ny beslutning)
# ---------------------------------------------------------------------------

def _start_fase1(conn, vakt, sak: Sak, claim_id: str, kl,
                 plan) -> tuple["reparasjoner.Reparasjonsplan", str | None]:
    """Bestill verifikasjon av det manglende vilkåret.

    KUN arbeideren oppretter generasjoner og oppdrag (v4-delta pkt. 1).
    Ingest og utløpsjobben gjør det aldri — hadde de kunnet, ville to
    komponenter kunnet bestille hver sin verifikasjon for samme generasjon,
    og delindeksen ville avvist den ene med en unikfeil i stedet for at
    rekkefølgen var riktig i utgangspunktet.
    """
    generasjon = vakt.execute(
        "SELECT start_verifikasjonsgenerasjon(%s,%s,%s,%s,%s)",
        (sak.tenant, sak.id, claim_id, sak.claim_generation,
         plan.vilkaar)).fetchone()[0]
    if generasjon is None:
        # Enten tapt lease, eller retry-budsjettet er brukt opp. Begge
        # ender samme sted, og fail-closed er retningen.
        _historikk(vakt, sak, "frist_utlopt",
                   {"grunn": "ingen_verifikasjonsgenerasjon",
                    "vilkaar": plan.vilkaar}, claim_id=claim_id)
        _krev_fencing(vakt, sak, claim_id, "status='manuell'")
        conn.commit()
        return reparasjoner.Reparasjonsplan(
            "manuell", "verifikasjonsbudsjett_brukt"), None

    rid = reparasjoner.fase1_id(sak.tenant, sak.id, plan.vilkaar,
                                kl.handler.id_med_versjon, generasjon)
    inp_hash = reparasjoner.input_hash(plan.reparasjonsinput)
    _registrer_reparasjon(vakt, sak, kl, plan, rid, inp_hash, claim_id)

    oppdrag_id = _opprett_oppdrag(vakt, sak, plan, rid, sak.loggpost_id)
    if vakt.execute("SELECT knytt_verifikasjonsoppdrag(%s,%s,%s,%s,%s)",
                    (sak.tenant, sak.id, plan.vilkaar, generasjon,
                     oppdrag_id)).fetchone()[0] is not True:
        # Generasjonen har alt et oppdrag, eller er ikke lenger aktiv.
        # Da har noen andre vunnet, og vi skal ikke skrive noe.
        raise Leasetap("verifikasjonsgenerasjonen kunne ikke knyttes")

    _historikk(vakt, sak, "verifikasjon_bestilt",
               {"vilkaar": plan.vilkaar, "generation": generasjon,
                "oppdrag_id": oppdrag_id}, claim_id=claim_id)
    _krev_fencing(vakt, sak, claim_id, "status='venter_verifikasjon'")
    conn.commit()
    return plan, rid


def _fase2(conn, vakt, sak: Sak,
           claim_id: str) -> tuple["reparasjoner.Reparasjonsplan", str | None]:
    """Bygg den nye hendelsen = minimert payload + VERIFISERT attestasjon.

    Beviset hentes VIA generasjonsraden (GO-vilkår V4): den `positiv`
    generasjonen med matchende kontekst. Ikke via en ubundet peker på
    saken — den finnes ikke, med vilje.

    Modell (b): originalhendelsen er minimert bort og finnes ikke. Det er
    nettopp derfor tofaseveien er avgrenset til saker der det manglende ER
    en attestasjon; en manglende VERDI kan ikke rekonstrueres, og de
    sakene gikk til `manuell` allerede i klassifiseringen.
    """
    sett_kontekst(vakt, sak.tenant, AKTOR, claim_id)

    rad = vakt.execute(
        "SELECT vg.vilkaar, vg.generation, b.id, b.attestasjon_kryptert,"
        " b.key_id, b.nonce, b.gyldig_til"
        "  FROM verifikasjonsgenerasjon vg"
        "  JOIN verifikasjonsbevis b"
        "    ON b.tenant = vg.tenant AND b.unntak_id = vg.unntak_id"
        "   AND b.vilkaar = vg.vilkaar AND b.generation = vg.generation"
        "   AND b.id = vg.bevis_id"
        " WHERE vg.tenant=%s AND vg.unntak_id=%s AND vg.status='positiv'"
        " ORDER BY vg.generation DESC LIMIT 1",
        (sak.tenant, sak.id)).fetchone()
    if rad is None:
        _historikk(vakt, sak, "frist_utlopt", {"grunn": "intet_positivt_bevis"},
                   claim_id=claim_id)
        _krev_fencing(vakt, sak, claim_id, "status='manuell'")
        conn.commit()
        return reparasjoner.Reparasjonsplan("manuell", "intet_positivt_bevis"), None

    vilkaar, generasjon, bevis_id, ct, key_id, nonce, gyldig_til = rad
    if gyldig_til <= datetime.now(timezone.utc):
        # Et utløpt bevis starter ikke fase 2. Attestasjonen hadde en
        # gyldighetstid, og å bruke den etterpå ville vært å behandle et
        # utløpt bevis som gyldig fordi vi tilfeldigvis rakk å lagre det.
        _historikk(vakt, sak, "verifikasjon_utlopt",
                   {"vilkaar": vilkaar, "generation": generasjon},
                   claim_id=claim_id)
        _krev_fencing(vakt, sak, claim_id, "status='manuell'")
        conn.commit()
        return reparasjoner.Reparasjonsplan("manuell", "bevis_utlopt"), None

    payload = _hent_payload(vakt, sak)
    if payload is None:
        _historikk(vakt, sak, "dek_utilgjengelig", claim_id=claim_id)
        _krev_fencing(vakt, sak, claim_id, "status='manuell'")
        conn.commit()
        return reparasjoner.Reparasjonsplan("manuell", "dek_utilgjengelig"), None

    nokkel = vakt.execute(
        "SELECT wrapped_dek FROM tenant_nokler WHERE tenant=%s AND key_id=%s",
        (sak.tenant, key_id)).fetchone()
    if nokkel is None or nokkel[0] is None:
        _krev_fencing(vakt, sak, claim_id, "status='manuell'")
        conn.commit()
        return reparasjoner.Reparasjonsplan("manuell", "bevis_dek_borte"), None
    dek = kryptering._pakk_ut((key_id, nokkel[0]), sak.tenant)[1]
    try:
        attestasjon = kryptering.dekrypter(dek, ct, nonce, sak.tenant, key_id)
    except Exception:
        _krev_fencing(vakt, sak, claim_id, "status='manuell'")
        conn.commit()
        return reparasjoner.Reparasjonsplan("manuell", "bevis_uleselig"), None

    # Utførelsesklassen er DATA som slås opp (v3-delta pkt. 4) — M-37 kan
    # verken velge eller overstyre den. Ukjent par → fail-closed manuell.
    maal = payload.get("handling")
    klasse = vakt.execute(
        "SELECT klasse FROM utforelsesklasser WHERE handler_id=%s"
        "   AND target_action=%s",
        (reparasjoner.R1.handler_id, maal)).fetchone()
    if klasse is None:
        _historikk(vakt, sak, "frist_utlopt",
                   {"grunn": "ukjent_utforelsesklasse", "maalhandling": maal},
                   claim_id=claim_id)
        _krev_fencing(vakt, sak, claim_id, "status='manuell'")
        conn.commit()
        return reparasjoner.Reparasjonsplan("manuell",
                                            "ukjent_utforelsesklasse"), None

    # Den nye hendelsen: minimert payload + den verifiserte attestasjonen.
    hendelse = {k: v for k, v in payload.items()
                if k not in ("begrunnelse", "kategori", "manglende_vilkaar")}
    hendelse["attestasjoner"] = {vilkaar: attestasjon}

    rid = reparasjoner.fase2_id(sak.tenant, sak.id, maal, bevis_id)
    plan = reparasjoner.Reparasjonsplan(
        "oppdrag" if klasse[0] == "krever_outbox" else "lost",
        f"fase2_{klasse[0]}", maalhandling=maal,
        oppdragstype="reinnsending", reparasjonsinput=hendelse)

    _registrer_reparasjon(vakt, sak, _FASE2KLASSE, plan, rid,
                          reparasjoner.input_hash(hendelse), claim_id)
    _historikk(vakt, sak, "verifikasjon_positiv",
               {"vilkaar": vilkaar, "generation": generasjon,
                "bevis_id": bevis_id, "utforelsesklasse": klasse[0]},
               claim_id=claim_id)
    conn.commit()
    return plan, rid


class _Fase2klasse:
    """Klassifiseringen fase 2 registreres under.

    Egen identitet fordi `utsted_arbeidskapabilitet` utleder
    `tillatt_handling` fra den registrerte klassifiseringen — arbeideren
    kan aldri sende ønsket handling som parameter (v4-delta pkt. 1 i
    PR-006). Fase 2 er en annen operasjon enn fase 1 og skal ha sin egen.
    """
    handler_id = "r1_fase2"
    versjon = "1"
    id_med_versjon = "r1_fase2@1"


_FASE2KLASSE = _Klasse(_Fase2klasse(), "manglende_data", "attestasjon_verifisert")
