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
from api.policyregister import hent_aktiv_bak_loggreferanse
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
#:
#: Utførelsesfristen her er STANDARDEN, ikke fasiten (Codex P1): en type
#: kan deklarere sin egen i `oppdragskontrakt.UTFORELSESFRIST_VALG`, og
#: gjør den det, er det den som skrives på raden. Sto denne konstanten
#: alene, fikk WCAG-kontrollen et døgn på en kontroll manifestet lover
#: 30 eller 60 minutter — og eier-leasen (migrasjon 037), som strekkes
#: til nettopp `utforelsesfrist`, arvet det samme feilaktige døgnet.
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
    #: Hvilken halvdel av tofaseprotokollen saken står i. Kommer fra
    #: STATUSEN i databasen, ikke fra generasjonstelleren:
    #:
    #:   'ny'    — første behandling (eller en sak uten verifikasjonsledd)
    #:   'fase2' — `verifikasjon_klar`: et positivt bevis foreligger
    #:   'retry' — `verifikasjon_retry_klar`: forrige runde slo feil, og
    #:             en NY generasjon skal åpnes. Behandles som fase 1.
    #:
    #: Ingen utledning her — fasen er eksplisitt tilstand i databasen.
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
    # HELE dekrypteringen i ett try. Første utgave fanget bare
    # `dekrypter` og lot `_pakk_ut` (KEK-utpakkingen) stå bar — og en
    # `InvalidTag` derfra propagerte helt ut av arbeiderprosessen og DREPTE
    # den. Én sak plattformen ikke kan lese, f.eks. etter en KEK-rotasjon
    # eller crypto-shredding, ville dermed stoppet hele køen; systemd
    # restarter fem ganger og gir opp.
    #
    # Riktig oppførsel er den koden allerede sikter mot: saken går til
    # `manuell` med `dek_utilgjengelig`, og arbeideren går videre.
    try:
        dek = kryptering._pakk_ut((key_id, nokkel[0]), sak.tenant)[1]
        return kryptering.dekrypter(dek, ct, nonce, sak.tenant, key_id)
    except Exception:
        return None


def _aktiv_policy(conn, sak: Sak) -> tuple[dict, str] | None:
    """Den AKTIVE policyen for saken, revalidert.

    Behandlingen re-evaluerer alltid mot aktiv policy (v2-delta pkt. 6).
    Snapshotet på saken styrer retry, ikke hvilke regler som gjelder nå —
    ellers ville en rettet policy ikke fått virkning på saker som allerede
    lå i kø, som er nettopp de sakene rettelsen ofte er til for.

    Oppslaget går gjennom `policyregister.hent_aktiv_bak_loggreferanse`, som
    er den ene definisjonen av «den aktive policyen en revisjonsreferanse
    navngir» — se den for hvorfor denne veien ikke tar policylåsen mot
    sletting (Codex P1): loggraden under ER referansen slettingen teller, og
    `revisjonslogg` er append-only.
    """
    rad = conn.execute(
        "SELECT r.policy_id FROM revisjonslogg r"
        " WHERE r.tenant=%s AND r.id=%s", (sak.tenant, sak.loggpost_id)
    ).fetchone()
    # Kolonnen bærer en POLICYREFERANSE, ikke en policy-id. Uten
    # `les_policyref` traff oppslaget aldri noe, og arbeideren
    # klassifiserte HVER sak som `manuell` med `aktiv_policy_utilgjengelig`
    # — altså behandlet den ingenting i det hele tatt. Tolkningen bor nå
    # inne i oppslaget, sammen med selve lesingen.
    p = hent_aktiv_bak_loggreferanse(conn, sak.tenant, rad[0] if rad else None)
    if p is None:
        return None
    if valider_policy(p[0]):
        return None            # korrupt aktiv policy: ingen automatikk
    return p


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
    # 'retry' faller gjennom til ordinær klassifisering. En ny generasjon
    # er et FRISKT løp fra bunnen (v7 pkt. 3) — ikke en lapping av det
    # forrige — så den går nøyaktig samme vei som første gang.

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
        plan = reparasjoner.planlegg_verifikasjon(
            kl, payload, policy,
            policy_id=(policy.get("meta") or {}).get("policy_id", ""))
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
    # Generasjonsnummeret telles fra ALLE tidligere operasjoner, ikke bare
    # den aktive. Fase 2 kommer etter at fase 1 er supersedet, og med et
    # oppslag som bare ser aktive rader ville den fått generasjon 0 på
    # nytt og kollidert med fase 1 i `reparasjon_generasjon_unik`.
    # Målt på firekjeden: `duplicate key value violates ...`.
    siste = conn.execute(
        "SELECT max(repair_generation) FROM reparasjonsoperasjoner"
        " WHERE tenant=%s AND unntak_id=%s", (sak.tenant, sak.id)).fetchone()[0]
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
        # Et AVSLUTTET VERIFIKASJONSOPPDRAG blokkerer ikke (PR-007).
        #
        # Regelen fra v3 pkt. 4 verner mot å starte en ny reparasjon oppå en
        # forretningshandling som kan være delvis utført. Et
        # verifikasjonsoppdrag har per definisjon INGEN forretnings-
        # fullmakter — fase 1 kontrollerer og attesterer, den utfører aldri
        # noe — så et `utfort` verifikasjonsoppdrag har ingen effekt å
        # kollidere med. Det er tvert imot nøyaktig forutsetningen for
        # `verifikasjon_retry_klar`.
        #
        # `plukket` blokkerer fortsatt, uansett type: da er oppdraget ute
        # hos en modul, og to samtidige verifikasjonsrunder på samme sak er
        # like uønsket som to samtidige utførelser.
        #
        # MÅLT: uten skillet ga hver eneste retry `manuell:
        # generation_blokkert_aktiv_utforelse`, og retry-veien var
        # uoppnåelig — andre halvdel av den samme feilen som gjorde at
        # `fase` ble utledet fra generasjonstelleren.
        aktivt = conn.execute(
            "SELECT 1 FROM oppdrag WHERE tenant=%s AND repair_operation_id=%s"
            "   AND (status = 'plukket'"
            "        OR (status = 'utfort' AND oppdragstype <> 'verifikasjon'))",
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
        generasjon = (siste if siste is not None else -1) + 1
    else:
        generasjon = (siste + 1) if siste is not None else 0

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
    # Legg merke til hva som IKKE er parameter nummer to heller: ROLLEN.
    # Funksjonen henter den fra sakens egen loggpost, slik at arbeideren
    # ikke kan uttrykke «gjør dette som daglig leder».
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
        oppdrag_id = _opprett_oppdrag(vakt, sak, plan, rid, sak.loggpost_id,
                                      beslutning_loggpost_id=
                                      _beslutningsloggpost(vakt, sak, rid))
        _historikk(vakt, sak, "oppdrag_opprettet",
                   {"oppdrag_id": oppdrag_id, "repair_operation_id": rid},
                   claim_id=claim_id)

    _krev_fencing(vakt, sak, claim_id, "status='venter_utførelse'")
    conn.commit()
    return Behandlingsresultat(sak, "venter_utførelse", "oppdrag_lagt_ut",
                               rid, oppdrag_id)


def _beslutningsloggpost(conn, sak: Sak, rid: str) -> int:
    """Loggposten til beslutningen som skapte DETTE oppdraget (PR-008 V1).

    Slås opp i databasen — aldri fra svarkroppen, aldri fra sakens egen
    loggpost. Sakens `loggpost_id` peker på beslutningen som skapte
    UNNTAKET; fase 2-beslutningen som faktisk bestilte oppdraget er en
    ANNEN loggpost, og en kobling via tidsnærhet eller «nærmeste rad» er
    forbudt (v3 pkt. 1). Nøkkelen er den samme som migrasjon 008s backfill
    bruker: `idempotency_key = repair_operation_id`, `kilde =
    'arbeidskapabilitet'`, TILLAT.

    NØYAKTIG ÉN rad er kontrakten. Null rader betyr at API-svaret vi
    nettopp fikk ikke har noen committet loggpost — en beslutning uten
    revisjonsspor er ingen beslutning, og da skal det heller ikke finnes
    et oppdrag. Flere rader skal være umulig (idempotensporten skriver
    loggpost og idempotensrad i samme transaksjon); står det likevel to
    der, velger vi ALDRI en av dem.
    """
    rader = conn.execute(
        "SELECT id FROM revisjonslogg"
        " WHERE tenant=%s AND idempotency_key=%s"
        "   AND kilde='arbeidskapabilitet' AND beslutning='TILLAT'",
        (sak.tenant, rid)).fetchall()
    if len(rader) != 1:
        raise RuntimeError(
            f"beslutningsloggpost for {rid}: fant {len(rader)} kandidater, "
            "krever nøyaktig 1 — oppdrag opprettes ikke")
    return int(rader[0][0])


def _opprett_oppdrag(conn, sak: Sak, plan, rid: str, loggpost_id: int, *,
                     beslutning_loggpost_id: int | None = None) -> int:
    """Oppdragsraden — kryptert, som alt annet saksinnhold.

    Eiermodulen får ALDRI ciphertext og aldri en nøkkel (v4-delta pkt. 4).
    Den får minimert klartekst fra API-laget, over den godkjente
    transporten, etter at API-et har dekryptert internt. Derfor krypteres
    payloaden her selv om den skal leses av en annen prosess: den ligger i
    databasen i mellomtiden, og en outbox i klartekst ville vært en kopi av
    saksgrunnlaget uten kryptering.
    """
    # PR-008: koblingsstatusen er lukket og toveis-bundet til oppdragstypen.
    # Sjekken står FØR kryptering og DEK-oppslag — et oppdrag som ikke kan
    # skrives skal heller ikke rekke å opprette en tenantnøkkel.
    if plan.oppdragstype == "verifikasjon":
        kobling, beslutning_loggpost_id = "VERIFIKASJON", None
    else:
        kobling = "KOBLET"
        if beslutning_loggpost_id is None:
            raise RuntimeError(
                "forretningsoppdrag uten beslutning_loggpost_id — V1 "
                "forbyr det")
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, sak.tenant)
    ct, nonce = kryptering.krypter(dek, plan.reparasjonsinput, sak.tenant,
                                   key_id)
    # Oppdragets EGEN handling, ikke målhandlingen.
    #
    # For et verifikasjonsoppdrag er de to forskjellige: oppdraget ber om
    # `verifiser.<vilkaar>`, mens målhandlingen er det fase 2 senere skal
    # be om (`purring.send`). Brukte vi målhandlingen her, ville
    # verifikasjonsoppdraget fått prefikset til forretningshandlingen — og
    # havnet i UTFØRER-modulens kø i stedet for verifikatorens.
    #
    # Målt: oppdraget fikk `eiermodul:reinnsending`, den syntetiske
    # eiermodulen plukket det, og verifikatoren fant en tom kø.
    oppdragshandling = plan.reparasjonsinput.get("handling") or plan.maalhandling
    naa = datetime.now(timezone.utc)
    # TYPENS EGEN FRIST VINNER (Codex P1). Se `UTFORELSESFRIST_S`: den er
    # standarden for typer som ikke sier noe selv, ikke en frist alle
    # oppdrag skal ha. Fristen skrives på RADEN, og både eier-leasen
    # (037), claimens kapabiliteter og reclaim-veien leser den derfra —
    # så dette ene feltet er det som gjør en annonsert frist til en
    # frist som faktisk gjelder.
    frist_s = oppdragsskjema.utforelsesfrist_s(
        plan.oppdragstype, plan.reparasjonsinput) or UTFORELSESFRIST_S
    rad = conn.execute(
        "INSERT INTO oppdrag (tenant, unntak_id, loggpost_id,"
        " repair_operation_id, oppdragstype, handling, eiermodul,"
        " payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist,"
        " beslutning_loggpost_id, koblingsstatus)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (sak.tenant, sak.id, loggpost_id, rid, plan.oppdragstype,
         oppdragshandling, _eiermodul_for(oppdragshandling), ct, key_id,
         nonce, naa + timedelta(seconds=frist_s),
         naa + timedelta(seconds=EVIDENSFRIST_S),
         beslutning_loggpost_id, kobling)).fetchone()
    return int(rad[0])


def _eiermodul_for(handling: str) -> str:
    """Hvilken eiermodul oppdraget bindes til ved OPPRETTELSEN.

    Bindingen skjer her og ikke ved claim: `claim_neste_oppdrag` filtrerer
    på `eiermodul = modul_id`, så et ubundet oppdrag ville vært synlig for
    alle moduler. Kolonnen er NOT NULL nettopp for at «ubundet» ikke skal
    finnes som tilstand.

    ER EIEREN DEKLARERT, ER DET DEN SOM GJELDER (Codex P1). PR-014c ga
    `Oppdragstype` et `eiermodul`-felt med en EKTE modul-id
    (`m_wcag_audit`), og det er den id-en kontrakten, deploymenten og
    tokenet er registrert på. `eiermodul:<typenavn>` ville skrevet
    `eiermodul:kontroll.wcag.nettsted` i raden, og siden claim krever
    `oppdrag.eiermodul = auth.modul_id`, kunne controlleren aldri claimet
    sitt eget oppdrag — det ville ligget til fristen uten at noen så det.

    De eierløse legacy-typene beholder det SYNTETISKE navnet: for dem
    finnes det ingen modulrad å peke på, og eksisterende rader
    (`eiermodul:reinnsending`, `eiermodul:verifikasjon`) skal fortsatt
    kunne claimes av de samme tokenene.
    """
    t = oppdragsskjema.type_for_handling(handling)
    if t is None:
        return "eiermodul:ukjent"
    return t.eiermodul or f"eiermodul:{t.navn}"


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


def skriv_heartbeat(sti: str, syklus_nr: int, siste_status: str) -> None:
    """Atomisk heartbeat for helsetimeren (PR-009 v3 §1).

    Skrives av HOVEDLØKKEN — aldri av en egen tråd som kan leve videre
    mens arbeidet henger; da ville heartbeaten målt trådplanleggeren, ikke
    arbeideren. `.tmp` + `rename()` gjør at leseren aldri ser en halv fil.
    Innholdet er drift, ikke saksdata.
    """
    import os
    tmp = sti + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "syklus_nr": syklus_nr,
                   "siste_status": siste_status}, f)
    os.replace(tmp, sti)


def kjor(dsn: str, basis_url: str, *, intervall_s: float = 1.0,
         maks_runder: int | None = None,
         heartbeat_sti: str | None = None) -> int:
    """Arbeiderprosessens hovedløkke. Kalles av systemd-unitten.

    `maks_runder` finnes for testene og for engangskjøringer i
    feilinjiseringen. I drift står den på None og løkken går til prosessen
    stoppes. `heartbeat_sti` settes av unitten (RuntimeDirectory) —
    heartbeat skrives etter HVER syklus, også når køen er tom, og med
    `db_utilgjengelig` når databasen er borte: en DB-feil er ikke en hengt
    worker, og skal aldri utløse en restart som ikke løser noe.
    """
    conn = None
    klient = Beslutningsklient(basis_url)
    behandlet = 0
    runder = 0
    try:
        while maks_runder is None or runder < maks_runder:
            runder += 1
            status = "ok"
            try:
                if conn is None or conn.closed:
                    conn = koble(dsn)
                frigi_utlopte(conn)
                res = behandle_en(conn, klient)
            except psycopg.OperationalError:
                status, res = "db_utilgjengelig", None
                if conn is not None and not conn.closed:
                    conn.close()
                conn = None
            if heartbeat_sti:
                skriv_heartbeat(heartbeat_sti, runder, status)
            if res is None:
                if maks_runder is not None:
                    break
                time.sleep(intervall_s)
                continue
            behandlet += 1
    finally:
        if conn is not None:
            conn.close()
    return behandlet




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
        "SELECT start_verifikasjonsgenerasjon(%s,%s,%s,%s,%s,%s,%s,%s)",
        (sak.tenant, sak.id, claim_id, sak.claim_generation,
         json.dumps(plan.krav_sett), plan.krav_sett_hash,
         plan.valgt_verifikator, plan.autoritetsversjon)).fetchone()[0]
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

    rid = reparasjoner.fase1_id(sak.tenant, sak.id, plan.krav_sett_hash,
                                kl.handler.id_med_versjon, generasjon)
    inp_hash = reparasjoner.input_hash(plan.reparasjonsinput)
    _registrer_reparasjon(vakt, sak, kl, plan, rid, inp_hash, claim_id)

    oppdrag_id = _opprett_oppdrag(vakt, sak, plan, rid, sak.loggpost_id)
    if vakt.execute("SELECT knytt_verifikasjonsoppdrag(%s,%s,%s,%s,%s)",
                    (sak.tenant, sak.id, "*sett*", generasjon,
                     oppdrag_id)).fetchone()[0] is not True:
        # Generasjonen har alt et oppdrag, eller er ikke lenger aktiv.
        # Da har noen andre vunnet, og vi skal ikke skrive noe.
        raise Leasetap("verifikasjonsgenerasjonen kunne ikke knyttes")

    _historikk(vakt, sak, "verifikasjon_bestilt",
               {"vilkaar_sett": plan.reparasjonsinput.get("vilkaar_sett"),
                "verifikator": plan.valgt_verifikator,
                "generation": generasjon, "oppdrag_id": oppdrag_id},
               claim_id=claim_id)
    _krev_fencing(vakt, sak, claim_id, "status='venter_verifikasjon'")
    conn.commit()
    return plan, rid


def _fase2(conn, vakt, sak: Sak,
           claim_id: str) -> tuple["reparasjoner.Reparasjonsplan", str | None]:
    """Bygg den nye hendelsen av minimert payload + HELE det verifiserte settet.

    Fire porter før hendelsen bygges, i denne rekkefølgen:

      1. **Komplett sett.** Alle bevis i generasjonen hentes VIA
         generasjonsraden (GO-vilkår V4 — det finnes ingen `ventet_bevis_id`).
         Mangler ett, eller er ett utløpt, bygges INGEN hendelse: saken går
         til retry eller manuell. Fase 2 sender aldri et delvis sett.
      2. **Aktiv autoritet** (GO-vilkår V1). Den valgte verifikatoren må
         FORTSATT være betrodd for hvert vilkår, målt mot aktiv policy —
         ikke mot snapshotet. Snapshotet beviser forsøket; en tilbaketrukket
         fullmakt må fanges på nåtid.
      3. **Aktiv policy** (GO-vilkår V3). Har policyen fjernet et vilkår,
         sendes den gamle attestasjonen ALDRI videre. Har den lagt til
         eller skjerpet noe, er settet utdatert og saken re-klassifiseres.
      4. **Utførelsesklassen** slås opp som DATA. Ukjent par ⇒ manuell.

    Motoren er likevel siste ord (v7 pkt. 1): kontrollene her er billige
    forhåndssjekker, ikke den autoritative avgjørelsen. En attestasjon kan
    bli ugyldig før den utløper, og bare policyporten ser det.
    """
    sett_kontekst(vakt, sak.tenant, AKTOR, claim_id)

    gen = vakt.execute(
        "SELECT generation, krav_sett_hash, valgt_verifikator"
        "  FROM verifikasjonsgenerasjon"
        " WHERE tenant=%s AND unntak_id=%s AND vilkaar='*sett*'"
        "   AND status='positiv' ORDER BY generation DESC LIMIT 1",
        (sak.tenant, sak.id)).fetchone()
    if gen is None:
        return _fase2_stopp(conn, vakt, sak, claim_id, "intet_positivt_bevis",
                            "frist_utlopt")
    generasjon, sett_hash, valgt_verifikator = gen

    bevis = vakt.execute(
        "SELECT bevis_vilkaar, attestasjon_kryptert, key_id, nonce, gyldig_til"
        "  FROM verifikasjonsbevis"
        " WHERE tenant=%s AND unntak_id=%s AND vilkaar='*sett*'"
        "   AND generation=%s ORDER BY bevis_vilkaar",
        (sak.tenant, sak.id, generasjon)).fetchall()
    if not bevis:
        return _fase2_stopp(conn, vakt, sak, claim_id, "ingen_bevis_i_generasjon",
                            "frist_utlopt")

    naa = datetime.now(timezone.utc)
    utlopte = [b[0] for b in bevis if b[4] <= naa]
    if utlopte:
        # Ett utløpt bevis ⇒ hele settet re-verifiseres i en NY generasjon.
        # Vi patcher aldri et gammelt sett (v7 pkt. 3) — en ny generasjon
        # er et friskt løp fra bunnen.
        return _fase2_retry(conn, vakt, sak, claim_id,
                            {"grunn": "bevis_utlopt", "vilkaar": utlopte,
                             "generation": generasjon})

    # --- Port 2 og 3: aktiv policy og aktiv autoritet -------------------
    aktiv = _aktiv_policy(vakt, sak)
    if aktiv is None:
        return _fase2_stopp(conn, vakt, sak, claim_id,
                            "aktiv_policy_utilgjengelig", "frist_utlopt")
    policy, aktiv_policy_hash = aktiv
    krav_sett = vakt.execute(
        "SELECT krav_sett FROM unntak WHERE tenant=%s AND id=%s",
        (sak.tenant, sak.id)).fetchone()[0]
    if not krav_sett:
        return _fase2_stopp(conn, vakt, sak, claim_id, "krav_sett_mangler",
                            "frist_utlopt")

    maal = (vakt.execute("SELECT handling FROM unntak WHERE tenant=%s AND id=%s",
                         (sak.tenant, sak.id)).fetchone() or [None])[0]
    # Hvilke vilkår krever den AKTIVE policyen nå?
    aktive_vilkaar = {
        vk["navn"] for h in (policy.get("handlinger") or [])
        if isinstance(h, dict) and h.get("id") == maal
        for vk in (h.get("vilkaar") or [])
        if isinstance(vk, dict) and isinstance(vk.get("navn"), str)}
    frosne_vilkaar = {e["vilkaar"] for e in krav_sett.get("krav") or []}

    if aktive_vilkaar - frosne_vilkaar:
        # STRENGERE: policyen krever noe settet aldri dekket. Saken må
        # re-klassifiseres mot det nye settet — fase 2 bygger ALDRI mot et
        # utdatert kravsett (Scope v2 pkt. 2).
        return _fase2_retry(conn, vakt, sak, claim_id,
                            {"grunn": "policy_skjerpet",
                             "nye_vilkaar": sorted(aktive_vilkaar - frosne_vilkaar)})

    # GO-vilkår V1: er den valgte verifikatoren FORTSATT betrodd for hvert
    # vilkår vi faktisk skal sende? Mot AKTIV policy, ikke mot snapshotet.
    sendes = sorted(aktive_vilkaar & {b[0] for b in bevis})
    for vilkaar in sendes:
        if valgt_verifikator not in reparasjoner.betrodde_for(policy, vilkaar):
            _historikk(vakt, sak, "sikkerhetsfrysing",
                       {"grunn": "autoritet_tilbakekalt_for_fase2",
                        "verifikator": valgt_verifikator, "vilkaar": vilkaar},
                       claim_id=claim_id)
            _krev_fencing(vakt, sak, claim_id, "status='manuell'")
            conn.commit()
            return reparasjoner.Reparasjonsplan(
                "manuell", "autoritet_tilbakekalt"), None

    # --- Dekrypter det settet som faktisk skal sendes -------------------
    attestasjoner = {}
    for bevis_vilkaar, ct, key_id, nonce, _gyldig in bevis:
        if bevis_vilkaar not in sendes:
            # GO-vilkår V3: policyen har FJERNET vilkåret. Den gamle
            # attestasjonen sendes aldri videre — en overflødig attestasjon
            # er evidens for et krav som ikke finnes.
            _historikk(vakt, sak, "policy_endret_siden_opprettelse",
                       {"grunn": "vilkaar_fjernet_i_aktiv_policy",
                        "vilkaar": bevis_vilkaar}, claim_id=claim_id)
            continue
        nokkel = vakt.execute(
            "SELECT wrapped_dek FROM tenant_nokler WHERE tenant=%s AND key_id=%s",
            (sak.tenant, key_id)).fetchone()
        if nokkel is None or nokkel[0] is None:
            return _fase2_stopp(conn, vakt, sak, claim_id, "bevis_dek_borte",
                                "dek_utilgjengelig")
        try:
            dek = kryptering._pakk_ut((key_id, nokkel[0]), sak.tenant)[1]
            attestasjoner[bevis_vilkaar] = kryptering.dekrypter(
                dek, ct, nonce, sak.tenant, key_id)
        except Exception:
            return _fase2_stopp(conn, vakt, sak, claim_id, "bevis_uleselig",
                                "dek_utilgjengelig")

    if not attestasjoner:
        return _fase2_stopp(conn, vakt, sak, claim_id, "intet_gyldig_bevis",
                            "frist_utlopt")

    payload = _hent_payload(vakt, sak)
    if payload is None:
        return _fase2_stopp(conn, vakt, sak, claim_id, "dek_utilgjengelig",
                            "dek_utilgjengelig")

    klasse = vakt.execute(
        "SELECT klasse FROM utforelsesklasser WHERE handler_id=%s"
        "   AND target_action=%s", (reparasjoner.R1.handler_id, maal)).fetchone()
    if klasse is None:
        return _fase2_stopp(conn, vakt, sak, claim_id, "ukjent_utforelsesklasse",
                            "frist_utlopt")

    hendelse = {k: v for k, v in payload.items()
                if k not in ("begrunnelse", "kategori", "manglende_vilkaar")}
    hendelse["attestasjoner"] = attestasjoner

    rid = reparasjoner.fase2_id(
        sak.tenant, sak.id, maal, generasjon, aktiv_policy_hash,
        reparasjoner.autoritetsregister_hash(policy), sett_hash)
    plan = reparasjoner.Reparasjonsplan(
        "oppdrag" if klasse[0] == "krever_outbox" else "lost",
        f"fase2_{klasse[0]}", maalhandling=maal,
        oppdragstype="reinnsending", reparasjonsinput=hendelse)

    # Fase 1 er terminal før fase 2 registreres (v1 §1) — det er
    # sekvensialiteten som lar `en_aktiv_reparasjon_per_sak` bestå.
    vakt.execute(
        "UPDATE reparasjonsoperasjoner SET status='superseded'"
        " WHERE tenant=%s AND unntak_id=%s AND status='aktiv'",
        (sak.tenant, sak.id))
    _registrer_reparasjon(vakt, sak, _FASE2KLASSE, plan, rid,
                          reparasjoner.input_hash(hendelse), claim_id)
    _historikk(vakt, sak, "verifikasjon_positiv",
               {"generation": generasjon, "vilkaar": sendes,
                "utforelsesklasse": klasse[0]}, claim_id=claim_id)
    conn.commit()
    return plan, rid


def _fase2_stopp(conn, vakt, sak: Sak, claim_id: str, grunn: str,
                 hendelse: str):
    """Fase 2 kan ikke bygge en hendelse. Saken går manuelt."""
    _historikk(vakt, sak, hendelse, {"grunn": grunn}, claim_id=claim_id)
    _krev_fencing(vakt, sak, claim_id, "status='manuell'")
    conn.commit()
    return reparasjoner.Reparasjonsplan("manuell", grunn), None


def _fase2_retry(conn, vakt, sak: Sak, claim_id: str, detalj: dict):
    """Settet må verifiseres på nytt — ny generasjon, ikke lapping.

    Budsjettet teller RUNDER over hele settet (v5 pkt. 5): et vilkår som
    gjentatte ganger ikke lar seg attestere bruker budsjett på vegne av
    settet, fordi saken uansett ikke kan løses uten det.
    """
    ny = sak.verification_generation < min(sak.maks_auto_forsok_snapshot or 0, 3)
    _historikk(vakt, sak, "verifikasjon_retry" if ny else "frist_utlopt",
               detalj, claim_id=claim_id)
    _krev_fencing(vakt, sak, claim_id,
                  "status='verifikasjon_retry_klar'" if ny
                  else "status='manuell'")
    conn.commit()
    return reparasjoner.Reparasjonsplan(
        "manuell", detalj.get("grunn", "retry")), None


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


# ---------------------------------------------------------------------------
# Systemd-inngangen — MÅ staa sist i filen.
#
# Den laa tidligere midt i modulen, og da kjoerte den FOER definisjonene
# under: `python -m m37.arbeider` gav `NameError: _start_fase1`. Moduler
# kjoeres topp til bunn, saa en `__main__`-blokk som ikke staar sist ser en
# halvferdig modul. Enhetstestene merket ingenting — de importerer, og en
# import kjoerer ikke `__main__`.
# ---------------------------------------------------------------------------

if __name__ == "__main__":       # pragma: no cover — systemd-inngangen
    import sys
    from db.hemmeligheter import last_credentials
    last_credentials()           # PR-009: LoadCredential før env-lesing
    dsn = os.environ.get("DATABASE_URL") or ""
    url = os.environ.get("DISPONIT_API_URL", "http://127.0.0.1:8099")
    # Heartbeat-sti fra unitens RuntimeDirectory (v3 §1) — utenfor systemd
    # settes den ikke, og løkken skriver da ingen heartbeat.
    rt = os.environ.get("RUNTIME_DIRECTORY", "").split(":")[0]
    hb = os.path.join(rt, "heartbeat") if rt else None
    if not dsn:
        print("DATABASE_URL mangler", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(0 if kjor(dsn, url, heartbeat_sti=hb) >= 0 else 1)
