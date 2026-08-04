"""Transaksjonell beslutningsvei for API-et (v2 Del 3-5, v3-delta pkt. 5-6,
PR-005b korreksjon 1 og 3).

`behandle()` er API-ets ENESTE vei til motoren, og ENESTE sted commit og
rollback skjer i forespørselsveien. Alt inne i den — `sikker_beslutning_pg`,
`PgTellerLager`, kryptering, unntaksrad — kjører som savepoints i den ene
ytre transaksjonen den eier.

Hvorfor eierskapet er en egen regel og ikke en detalj: skriver vi loggpost,
unntaksrad, jti-konsumering og idempotenssvar i hver sin transaksjon, kan et
krasj mellom to av dem etterlate en beslutning som er halvveis sann — en
loggpost som sier TILLAT uten at frekvensplassen ble reservert, eller en
idempotensrad som lover et svar det ikke finnes evidens for. Én transaksjon
gjør «alt eller ingenting» til en egenskap ved databasen framfor en påstand
i en README.

`behandle()` REIMPLEMENTERER ikke beslutningsflyten. Den pakker
`sikker_beslutning_pg` i idempotens-claimet og legger til det API-et trenger
rundt: attestasjonsbinding, policyoppslag, jti-replayvern, routing og
kryptert unntaksrad.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import psycopg

from db import kryptering
from db.pg import Evidens, Portbrudd, sett_kontekst, sikker_beslutning_pg
from policy_validator import attestering
from policy_validator.engine import STOPP, TILLAT, Grunn

from . import feil as feiltabell
from . import minimering, policyregister


class Transaksjonsbrudd(RuntimeError):
    """Noe under `behandle()` forsøkte å styre transaksjonen selv."""


class Transaksjonsvakt:
    """Tilkoblingen slik alt UNDER `behandle()` får se den.

    Korreksjon 1 sier at `kjerne.behandle()` er eneste sted commit og
    rollback skjer. Den regelen kan ikke bevises med grep: `PgTellerLager`
    har en rollback som er riktig frittstående og feil under en ytre
    transaksjon, og forskjellen er en `if`-test — ikke et tekstmønster.

    Vakten gjør regelen til en kjøretidsegenskap i stedet. Legger noen inn
    en `conn.commit()` i `sikker_beslutning_pg`, i krypteringen eller i en
    fremtidig gren av kjernen, feiler forespørselen HØYLYTT her, framfor å
    committe en halv beslutning og se vellykket ut. Det er samme grep som
    resten av plattformen bruker: gjør den ulovlige tilstanden umulig
    framfor å be folk om å la være.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn: psycopg.Connection) -> None:
        object.__setattr__(self, "_conn", conn)

    def execute(self, *a, **kw):
        return self._conn.execute(*a, **kw)

    def cursor(self, *a, **kw):
        return self._conn.cursor(*a, **kw)

    def transaction(self, *a, **kw):
        # Nesting gir SAVEPOINT — som er nettopp det kontrakten vil ha.
        return self._conn.transaction(*a, **kw)

    def commit(self):
        raise Transaksjonsbrudd(
            "commit() under behandle() — kun behandle() eier transaksjonen")

    def rollback(self):
        raise Transaksjonsbrudd(
            "rollback() under behandle() — kun behandle() eier transaksjonen")

    def __getattr__(self, navn):
        return getattr(self._conn, navn)


class Feilsvar(Exception):
    """En feilvei fra v2 Del 4 som ikke er en beslutning.

    Bæres opp til `app.py`, som oversetter til HTTP. Skilt fra vanlige
    exceptions med vilje: dette er en KJENT tilstand med en definert rad i
    feilveitabellen, ikke en uventet feil.
    """

    def __init__(self, kode: str, detalj: str = "") -> None:
        super().__init__(kode if not detalj else f"{kode}: {detalj}")
        self.kode = kode
        self.detalj = detalj
        self.rad = feiltabell.FEIL[kode]


@dataclass
class Svar:
    http: int
    kropp: dict
    unntak_id: int | None = None
    replay: bool = False
    sikkerhetshendelse: str | None = None   # kode til sikkerhetsloggen
    driftshendelse: str | None = None


@dataclass
class Sporing:
    """Det testene trenger for å bevise at flyten gikk som spesifisert,
    uten å måtte lese loggposter tilbake. Særlig `motorkall` er bindende:
    v3-delta pkt. 6 krever at 20 samtidige like forespørsler gir NØYAKTIG
    én evaluering, og det kan bare telles her."""
    motorkall: int = 0
    loggpost_id: int | None = None
    jti_konsumert: list[str] = field(default_factory=list)
    steg: list[str] = field(default_factory=list)


def input_hash(policy_id: str, event: dict) -> str:
    """Kanonisk hash over det som faktisk avgjør svaret.

    Idempotenskontrakten er «samme nøkkel + samme input => samme svar».
    Hva som er «samme input» må derfor være entydig — derav sorterte
    nøkler og ingen mellomrom, samme regler som `audit.input_hash`.
    """
    return hashlib.sha256(json.dumps(
        {"policy_id": policy_id, "event": event},
        sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def attestation_set_hash(event: dict) -> str | None:
    """SHA-256 over de SORTERTE attestasjonssignaturene i hendelsen.

    Kobler loggposten til nøyaktig hvilke bevis som forelå. Uten den kan
    man i ettertid se at en beslutning var TILLAT, men ikke hvilke
    attestasjoner som bar den.
    """
    attester = event.get("attestasjoner")
    if not isinstance(attester, dict) or not attester:
        return None
    verdier = sorted(
        str((a.get("signatur") or {}).get("verdi"))
        for a in attester.values() if isinstance(a, dict))
    return hashlib.sha256("\x1f".join(verdier).encode("utf-8")).hexdigest()


def _handling_i_policy(policy: dict, handling_id: str) -> dict | None:
    for h in policy.get("handlinger") or []:
        if isinstance(h, dict) and h.get("id") == handling_id:
            return h
    return None


def _er_irreversibel(policy: dict, handling_id: str) -> bool:
    h = _handling_i_policy(policy, handling_id) or {}
    return (h.get("reversering") or {}).get("type") == "irreversibel"


def _svarkropp(d, policy_content_hash: str | None, request_id: str,
               unntak_id: int | None) -> dict:
    """Svaret per v2 Del 3.1 — KUN koder i begrunnelsen.

    `Grunn.params` kan inneholde beløp, gruppeverdier og andre biter av
    hendelsen. De hører hjemme i revisjonsloggen, som er tilgangsstyrt, og
    ikke i et HTTP-svar som kan havne i en klientlogg.
    """
    kropp = {
        "beslutning": d.beslutning,
        "policy_id": d.policy_id,
        "policy_content_hash": policy_content_hash,
        "begrunnelse": [g.kode for g in d.begrunnelse],
        "request_id": request_id,
    }
    if unntak_id is not None:
        kropp["unntak_id"] = unntak_id
    return kropp


# ---------------------------------------------------------------------------
# Unntaksraden
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Policysnapshot:
    """Policykonteksten saken FØDES med (PR-006 v2 pkt. 6).

    Uten snapshot bestemmes en gammel saks retrysemantikk av policyen slik
    den ser ut NÅ. Da kan en redigering i dag endre hvor mange automatiske
    forsøk en sak fra i fjor får — altså i ettertid, uten spor. Snapshotet
    fryser tre ting på saken: hvilken policyversjon som gjaldt, hashen av
    innholdet, og forsøksgrensen.

    Behandlingen re-evaluerer likevel alltid mot AKTIV policy; snapshotet
    styrer bare retry og gjør avvik synlige (historikkhendelsen
    `policy_endret_siden_opprettelse`).
    """
    versjon: str
    innholds_hash: str
    maks_auto_forsok: int

    @classmethod
    def fra_policy(cls, policy: dict, innholds_hash: str) -> "Policysnapshot":
        meta = policy.get("meta") or {}
        maks = (policy.get("unntak") or {}).get("maks_auto_forsok")
        # `bool` er subklasse av `int` — samme felle som i manifestporten.
        if isinstance(maks, bool) or not isinstance(maks, int) or maks < 0:
            return UKJENT_SNAPSHOT
        versjon = meta.get("versjon")
        if not isinstance(versjon, str) or not versjon:
            return UKJENT_SNAPSHOT
        return cls(versjon, innholds_hash, maks)


#: Saker som oppstår FØR policyen er lastet — attestasjonsbrudd i steg 4 —
#: har ingen policykontekst å fryse. De får en reservert verdi og
#: forsøksgrense 0.
#:
#: Null er ikke en tilfeldig standardverdi: claim-funksjonen krever
#: `forsok < LEAST(snapshot, 3)`, som er usann for enhver forsok-verdi når
#: snapshotet er 0. En sak uten kjent policykontekst kan altså ikke
#: behandles automatisk — ikke fordi noen husket å sjekke, men fordi
#: aritmetikken gjør det umulig. (De er dessuten `sakstype='sikkerhet'`,
#: som normal-arbeideren aldri claimer. To uavhengige grunner.)
UKJENT_SNAPSHOT = Policysnapshot("<ukjent>", "<ukjent>", 0)


def _skriv_unntak(conn: psycopg.Connection, tenant: str, loggpost_id: int,
                  handling: str, kategori: str, sakstype: str,
                  prioritet: str, payload: dict,
                  snapshot: "Policysnapshot") -> int:
    """Kryptert unntaksrad i SAMME transaksjon som loggposten.

    Rekkefølgen er tvungen av databasen: fremmednøkkelen
    (tenant, loggpost_id) -> revisjonslogg (tenant, id) betyr at loggposten
    må være skrevet først. Det er samme rekkefølge som kontrakten krever —
    evidensen finnes før saken som viser til den.
    """
    try:
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
    except psycopg.Error:
        raise
    except Exception as e:
        # Manglende/ubrukelig KEK er ikke en databasefeil, men den har samme
        # utfall: ingen kryptering => ingen lagring. ALDRI klartekst.
        raise Feilsvar("tenantnokkel_mangler", type(e).__name__) from e

    ct, nonce = kryptering.krypter(dek, payload, tenant, key_id)
    rad = conn.execute(
        "INSERT INTO unntak (tenant, loggpost_id, handling, kategori,"
        " sakstype, prioritet, payload_kryptert, key_id, alg, nonce,"
        " maks_auto_forsok_snapshot, policy_versjon, policy_content_hash)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'AES-256-GCM',%s,%s,%s,%s)"
        " RETURNING id",
        (tenant, loggpost_id, handling, kategori, sakstype, prioritet,
         ct, key_id, nonce, snapshot.maks_auto_forsok, snapshot.versjon,
         snapshot.innholds_hash)).fetchone()
    return int(rad[0])


# ---------------------------------------------------------------------------
# Hovedflyten
# ---------------------------------------------------------------------------

def _krev_kapabilitet(conn: psycopg.Connection, kap, event: dict,
                      idempotency_key: str, handling: str) -> None:
    """Revalider arbeidskapabiliteten mot UNNTAKSRADEN (v4-delta pkt. 1.2).

    Utstedelsesverdiene alene er ikke nok, og det er hele forskjellen på en
    kapabilitet og et bearer-token. Mellom utstedelse og bruk kan leasen ha
    gått tapt, saken kan ha fått ny generasjon, og en annen arbeider kan ha
    overtatt. Sjekker vi bare det kapabiliteten SIER om seg selv, godkjenner
    vi en påstand fra utstedelsestidspunktet som om den var sann nå.

    Tre bindinger håndheves i tillegg, alle fra v3-delta pkt. 1:
      * handlingen må være NØYAKTIG den kapabiliteten ble utstedt for,
      * idempotensnøkkelen må VÆRE repair_operation_id — ellers kan én
        kapabilitet gi to ulike forretningshandlinger,
      * tenanten kommer fra kapabiliteten, aldri fra kroppen.
    """
    if handling != kap.tillatt_handling:
        raise Feilsvar("kapabilitet_feil_handling",
                       f"{handling} != {kap.tillatt_handling}")
    if idempotency_key != kap.repair_operation_id:
        raise Feilsvar("kapabilitet_feil_idempotensnokkel")
    rad = conn.execute(
        "SELECT 1 FROM unntak WHERE tenant=%s AND id=%s AND claim_id=%s"
        "   AND claim_generation=%s AND status='under_behandling'"
        "   AND claim_utloper > now()",
        (kap.tenant, kap.unntak_id, kap.claim_id,
         kap.claim_generation)).fetchone()
    if rad is None:
        raise Feilsvar("kapabilitet_fencing_tapt")


def behandle(conn: psycopg.Connection, ctx, *, policy_id: str, event: dict,
             idempotency_key: str, request_id: str, aktor: str,
             nokler: dict, naa: datetime | None = None,
             sporing: Sporing | None = None, kapabilitet=None) -> Svar:
    """De elleve stegene. Én transaksjon, ett commit-punkt.

    Kaller aldri `conn.commit()`/`conn.rollback()` andre steder enn her, og
    ingenting den kaller har lov til det heller — derfor sendes
    `ytre_transaksjon=True` videre til `sikker_beslutning_pg`.
    """
    naa = naa or datetime.now(timezone.utc)
    sporing = sporing if sporing is not None else Sporing()
    tenant = ctx.tenant_id
    handling = event.get("handling") if isinstance(event.get("handling"), str) \
        else "<mangler>"
    ih = input_hash(policy_id, event)

    vakt = Transaksjonsvakt(conn)
    try:
        svar = _flyt(vakt, ctx, tenant=tenant, policy_id=policy_id, event=event,
                     handling=handling, idempotency_key=idempotency_key,
                     request_id=request_id, aktor=aktor, nokler=nokler,
                     naa=naa, ih=ih, sporing=sporing,
                     kapabilitet=kapabilitet)
        conn.commit()
        return svar
    except Feilsvar:
        conn.rollback()
        raise
    except BaseException:
        # Alt annet — psycopg.Error, programmeringsfeil, KeyboardInterrupt.
        # Rullingen skjer HER, hos eieren, aldri nede i et savepoint som
        # deretter committes. `app.py` oversetter til 500/503 og nødlogg.
        conn.rollback()
        raise


def _flyt(conn: psycopg.Connection, ctx, *, tenant: str, policy_id: str,
          event: dict, handling: str, idempotency_key: str, request_id: str,
          aktor: str, nokler: dict, naa: datetime, ih: str,
          sporing: Sporing, kapabilitet=None) -> Svar:
    steg = sporing.steg

    # --- 1. Sesjonskontekst FØRST i den autentiserte transaksjonen -------
    # Korreksjon 3: dette er den andre transaksjonen. Pre-auth er ferdig og
    # committet; tenanten er kjent og verifisert, og kommer aldri fra body.
    sett_kontekst(conn, tenant, aktor, request_id)
    steg.append("kontekst")

    # --- 1b. Arbeidskapabilitet: revalider FØR idempotens-claimet --------
    # Rekkefølgen er ikke smakssak. Claimer vi idempotensnøkkelen først og
    # kapabiliteten så viser seg ugyldig, har en avvist forespørsel likevel
    # lagt beslag på repair_operation_id — og arbeiderens neste, gyldige
    # forsøk ville møtt sitt eget spøkelse.
    #
    # Den må derimot ligge ETTER `sett_kontekst`: oppslaget mot
    # `unntak` treffer row level security, og uten tenant i konteksten ser
    # den null rader og ville avvist enhver gyldig kapabilitet.
    if kapabilitet is not None:
        _krev_kapabilitet(conn, kapabilitet, event, idempotency_key, handling)
        steg.append("kapabilitet_revalidert")

    # --- 2. Serialiser per idempotensnøkkel ------------------------------
    # Xact-varianten: låsen slippes av commit/rollback i `behandle`, aldri
    # av noe under. Ingen opprydding å glemme, ingen foreldreløse låser.
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                 (f"{tenant}\x1fidem\x1f{idempotency_key}",))
    steg.append("laas")

    # --- 3. Idempotens-claim ---------------------------------------------
    claim = conn.execute(
        "INSERT INTO idempotens (tenant, nokkel, input_hash, status, request_id)"
        " VALUES (%s,%s,%s,'paagaar',%s)"
        " ON CONFLICT (tenant, nokkel) DO NOTHING RETURNING nokkel",
        (tenant, idempotency_key, ih, request_id)).fetchone()
    if claim is None:
        eksisterende = conn.execute(
            "SELECT input_hash, status, respons FROM idempotens"
            " WHERE tenant=%s AND nokkel=%s",
            (tenant, idempotency_key)).fetchone()
        if eksisterende is None:
            # Raden finnes for en ANNEN tenant (unik nøkkel er (tenant,
            # nokkel), så dette kan ikke skje) eller RLS skjuler den.
            # Uansett: vi vet ikke nok til å svare, og da svarer vi ikke.
            raise Feilsvar("db_utilgjengelig", "idempotensraden er usynlig")
        lagret_hash, status, respons = eksisterende
        if lagret_hash != ih:
            raise Feilsvar("idempotenskonflikt")
        if status == "ferdig":
            steg.append("replay")
            # Byte-identisk svar, inkludert det OPPRINNELIGE request_id-et.
            # Det er ikke en glipp: v3-delta pkt. 6 krever at alle svar er
            # identiske, og et svar som varierer per forsøk er ikke det.
            # Gjeldende request_id følger med som HTTP-header i app.py.
            return Svar(int(respons.get("http", 200)), dict(respons),
                        respons.get("unntak_id"), replay=True)
        # `paagaar` OG vi holder låsen => vinneren finnes ikke lenger.
        # (Hadde den levd, ville den holdt låsen; hadde den rullet tilbake
        # før commit, ville raden ikke vært synlig.) Vi overtar claimet.
        conn.execute(
            "UPDATE idempotens SET request_id=%s, ts=now()"
            " WHERE tenant=%s AND nokkel=%s",
            (request_id, tenant, idempotency_key))
        steg.append("reclaim")
    else:
        steg.append("claim")

    evidens = Evidens(handling=handling, request_id=request_id,
                      idempotency_key=idempotency_key,
                      attestation_set_hash=attestation_set_hash(event))

    def portstopp(grunn: Grunn, policy: dict, label: str,
                  hash_: str | None = None, http: int = 200,
                  feilkode: str | None = None,
                  snapshot: Policysnapshot = UKJENT_SNAPSHOT) -> Svar:
        """Felles utgang for alt som er avgjort før motoren.

        Loggposten skrives av `sikker_beslutning_pg` — den samme ene
        skriveveien som alle andre beslutninger bruker.

        Standardverdien for `snapshot` er UKJENT med vilje: portstopp
        brukes både før og etter at policyen er lastet, og den eneste
        trygge standarden er den som gjør saken uclaimbar. Kallestedene
        som HAR en policy sender den inn eksplisitt.
        """
        evidens.policy_content_hash = hash_
        d = sikker_beslutning_pg(policy, ctx, event, conn, naa=naa,
                                 nokler=None, evidens=evidens,
                                 ytre_transaksjon=True,
                                 portbrudd=Portbrudd(grunn, label))
        return _avslutt(conn, d, event, tenant, handling, evidens,
                        idempotency_key, request_id, hash_, sporing,
                        snapshot, http=http, feilkode=feilkode,
                        kapabilitet=kapabilitet)

    # --- 4. Attestasjonsporten: signatur FØR binding ----------------------
    # Rekkefølgen er ikke smakssak. Er signaturen ugyldig, er feltene i
    # attestasjonen ikke til å stole på — og da er det meningsløst å
    # sammenligne dem med noe som helst.
    signaturbrudd = attestering.kontroller_hendelse(event, nokler)
    if signaturbrudd is not None:
        steg.append("signaturbrudd")
        return portstopp(signaturbrudd, {}, "signaturport")
    bindingsbrudd = attestering.kontroller_binding(event, ctx, handling,
                                                   policy_id, naa)
    if bindingsbrudd is not None:
        steg.append("bindingsbrudd")
        return portstopp(bindingsbrudd, {}, "bindingsport")
    steg.append("attestasjonsport_ok")

    # --- 5. Policy fra registeret, revalidert -----------------------------
    try:
        policy, policy_hash = policyregister.hent_aktiv(conn, tenant, policy_id)
    except policyregister.PolicyUkjent:
        raise Feilsvar("policy_ukjent") from None
    except policyregister.PolicyKorrupt as e:
        steg.append("policy_korrupt")
        # Tenanten er kjent, så saken KAN føres — v2 Del 4 sier «drift + m37».
        # Derfor ikke en Feilsvar (som ville rullet tilbake og etterlatt oss
        # uten spor av at policyen er ødelagt), men en normal portstopp som
        # committes, etterfulgt av 500 i svaret.
        #
        # Bare `meta` brukes til loggposten, og en korrupt policy kan ha hva
        # som helst der. Vi sender derfor inn en renset variant: loggingen
        # skal ikke kunne kastes av det samme innholdet den skal bevise.
        raa = e.raa if isinstance(e.raa, dict) else {}
        trygg = {"meta": raa.get("meta") if isinstance(raa.get("meta"), dict)
                 else {},
                 "schema_version": raa.get("schema_version")
                 if isinstance(raa.get("schema_version"), str) else None}
        svar = portstopp(Grunn("policy_korrupt", {"antall_feil": len(e.feil)}),
                         trygg, "policyregister",
                         http=feiltabell.FEIL["policy_korrupt"].http,
                         feilkode="policy_korrupt")
        svar.driftshendelse = "policy_korrupt"
        return svar
    evidens.policy_content_hash = policy_hash
    snapshot = Policysnapshot.fra_policy(policy, policy_hash)
    steg.append("policy")

    # --- 5b. jti-replay: LÅS først, sjekk så ------------------------------
    # Konsumeringen selv skjer i steg 7, men kontrollen må skje her, FØR
    # motoren evaluerer og loggposten skrives. Oppdaget vi replay etter at
    # loggposten var skrevet, ville evidensen sagt TILLAT om en forespørsel
    # vi avviste.
    #
    # Låsen tas i den sorterte rekkefølgen `jti_liste` gir. Det er derfor
    # den er sortert: to samtidige forespørsler med overlappende jti-er tar
    # låsene i samme rekkefølge og kan ikke vranglåse hverandre.
    irreversibel = _er_irreversibel(policy, handling)
    jtier = attestering.jti_liste(event) if irreversibel else []
    for _, jti in jtier:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                     (f"{tenant}\x1fjti\x1f{jti}",))
    for _, jti in jtier:
        brukt = conn.execute(
            "SELECT 1 FROM attestasjon_jti WHERE tenant=%s AND jti=%s",
            (tenant, jti)).fetchone()
        if brukt is not None:
            steg.append("jti_replay")
            return portstopp(Grunn("attestasjon_replay", {"jti_prefiks": jti[:8]}),
                             policy, policy_id, policy_hash, snapshot=snapshot)
    steg.append("jti_ok")

    # --- 6. Motoren, via den ene lovlige inngangen ------------------------
    sporing.motorkall += 1
    d = sikker_beslutning_pg(policy, ctx, event, conn, naa=naa, nokler=nokler,
                             evidens=evidens, ytre_transaksjon=True)
    steg.append(f"beslutning:{d.beslutning}")

    # --- 7. Konsumer jti — kun ved TILLAT ---------------------------------
    # Bevisst ikke ved STOPP/UNNTAK: ellers kunne hvem som helst brenne opp
    # en gyldig attestasjon ved å sende en forespørsel som uansett blir
    # avvist. Låsen fra 5b holder til commit, så INSERT-en kan ikke tape et
    # kappløp her.
    if d.beslutning == TILLAT and jtier:
        for vilkaar, jti in jtier:
            conn.execute(
                "INSERT INTO attestasjon_jti (tenant, jti, utloper)"
                " VALUES (%s,%s,%s)",
                (tenant, jti, _utlop_for(event, vilkaar, naa)))
            sporing.jti_konsumert.append(jti)
        steg.append("jti_konsumert")

    return _avslutt(conn, d, event, tenant, handling, evidens,
                    idempotency_key, request_id, evidens.policy_content_hash,
                    sporing, snapshot, kapabilitet=kapabilitet)


def _utlop_for(event: dict, vilkaar: str, naa: datetime) -> datetime:
    """Når DENNE jti-raden kan ryddes bort (PR-006-vedlikehold).

    Utløpstiden hentes fra attestasjonen jti-en tilhører — ikke en felles
    tid for hele hendelsen. Med én felles tid ville jti-en til en
    kortlevd attestasjon overlevd sin egen attestasjon (unødvendig, men
    ufarlig) eller blitt ryddet FØR den (farlig: replay blir mulig igjen).
    `kontroller_binding` har allerede bevist at feltet finnes og har
    tidssone, så fallbacken under er kun en sikring.
    """
    from datetime import timedelta
    att = (event.get("attestasjoner") or {}).get(vilkaar)
    t = attestering.tid_med_sone(att.get("utloper")) \
        if isinstance(att, dict) else None
    return t if t is not None else naa + timedelta(days=1)


def _avslutt(conn: psycopg.Connection, d, event: dict, tenant: str,
             handling: str, evidens: Evidens, idempotency_key: str,
             request_id: str, policy_hash: str | None,
             sporing: Sporing, snapshot: "Policysnapshot",
             http: int = 200, feilkode: str | None = None,
             kapabilitet=None) -> Svar:
    """Steg 9-11: routing, unntaksrad, idempotens ferdig."""
    sporing.loggpost_id = evidens.loggpost_id
    siste = d.begrunnelse[-1].kode if d.begrunnelse else None
    sakstype, prioritet = feiltabell.sakstype_for(d.beslutning, siste, d.effekt)

    unntak_id = None
    if sakstype is not None:
        if evidens.loggpost_id is None:
            # Uten loggpost finnes det ingen evidens å feste saken til, og
            # fremmednøkkelen ville avvist raden uansett. At vi kommer hit
            # betyr at loggskrivingen ble hoppet over — en programmeringsfeil.
            raise RuntimeError("sak uten loggpost — evidenskjeden er brutt")
        kategori = d.unntak_kategori or (siste or "ukjent")
        payload = minimering.minimer_payload(
            event, d.unntak_kategori, [g.kode for g in d.begrunnelse])
        try:
            unntak_id = _skriv_unntak(conn, tenant, evidens.loggpost_id,
                                      handling, kategori, sakstype, prioritet,
                                      payload, snapshot)
        except Feilsvar:
            raise
        except psycopg.Error as e:
            raise Feilsvar("unntaksskriv_feilet", type(e).__name__) from e
        sporing.steg.append(f"sak:{sakstype}")

    if feilkode is None:
        kropp = _svarkropp(d, policy_hash, request_id, unntak_id)
    else:
        # Feilveier som IKKE er beslutningssvar (i praksis policy_korrupt):
        # saken og loggposten skal bestå, men klienten får feilkroppen.
        # Den lagres som idempotenssvar slik at en replay gir NØYAKTIG det
        # samme — ikke et beslutningssvar med status 200.
        kropp = {"feil": feilkode, "http": http, "request_id": request_id}
        if unntak_id is not None:
            kropp["unntak_id"] = unntak_id

    # --- 11. Idempotens ferdig — ETTER at alt annet er skrevet ------------
    # Rekkefølgen er bindende (korreksjon 1): lagres svaret først, kan et
    # kappløp lese ut et «ferdig»-svar for en beslutning hvis loggpost og
    # sak ennå ikke finnes. Her er alt i samme transaksjon, men rekkefølgen
    # holdes uansett — den koster ingenting og gjør invarianten lesbar.
    conn.execute(
        "UPDATE idempotens SET status='ferdig', respons=%s"
        " WHERE tenant=%s AND nokkel=%s",
        (json.dumps(kropp, ensure_ascii=False), tenant, idempotency_key))
    sporing.steg.append("idempotens_ferdig")

    # --- 12. Brenn arbeidskapabiliteten — SAMME COMMIT ------------------
    # v4-delta pkt. 1 punkt 3, og rekkefølgen er bindende på begge sider:
    #
    #   FØR loggposten  => en brent engangsfullmakt uten evidens for hva
    #                      den ble brukt til. Arbeideren kan ikke gjenoppta
    #                      (kapabiliteten er borte) og revisor kan ikke
    #                      rekonstruere (loggposten finnes ikke).
    #   ETTER commit    => to forespørsler kan rekke å bruke den samme
    #                      kapabiliteten, og engangsegenskapen er en
    #                      påstand snarere enn en egenskap.
    #
    # Her ligger den inne i den ene transaksjonen `behandle()` eier, etter
    # at loggpost, sak og idempotenssvar er skrevet. Feiler forbruket, er
    # kapabiliteten enten utløpt eller overtatt av noen andre midt i flyten
    # — og da skal HELE forespørselen rulles, ikke fullføres med et svar
    # som ser auditert ut.
    if kapabilitet is not None:
        ok = conn.execute("SELECT bruk_kapabilitet(%s, %s)",
                          (kapabilitet.jti, request_id)).fetchone()
        if not ok or ok[0] is not True:
            raise Feilsvar("kapabilitet_ugyldig", "forbruk feilet i commit")
        sporing.steg.append("kapabilitet_brukt")

    svar = Svar(http, kropp, unntak_id)
    if sakstype == "sikkerhet" or siste in feiltabell.SIKKERHETSKODER:
        svar.sikkerhetshendelse = siste
    if sakstype == "drift":
        svar.driftshendelse = siste
    return svar
