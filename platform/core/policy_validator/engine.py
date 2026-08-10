"""M-1 Policy- og fullmaktsmotor — beslutningsmotor v0.2.

Endringer fra v0.1 (ChatGPT-review PR-001, alle funn adressert):
  1  Autentisert EvaluationContext — rolle leses ALDRI fra hendelsen.
  2  Vilkår bevises med attestasjoner fra betrodde verifikatorer
     (policy-allowlist), med ressursbinding og utløpstid.
     Innsender kan ikke attestere egne vilkår.
  3  Beløp er Decimal: bool, negative, ikke-endelige og for presise
     verdier avvises som ugyldig_data.
  4  Frekvens er strukturert og telles i et betrodd, atomisk lager —
     aldri levert av hendelsen. Regel uten lager => STOPP (fail-closed).
  5  Dataklasser er fail-closed: mangler klassifisering eller betrodd
     kilde => UNNTAK.
  6  Tidsvinduer evalueres i policyens IANA-tidssone; naive tidsstempler
     avvises.
  7  Begrunnelser er maskinkoder + parametre (i18n: locales/ oversetter).

Kontrakt: KUN beslutningen TILLAT kan utløse sideeffekt — og kun via
audit.sikker_beslutning, som garanterer logg-før-utførelse. STOPP,
UNNTAK, exception og timeout er alle fail-closed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

TILLAT = "TILLAT"
STOPP = "STOPP"
UNNTAK = "UNNTAK"

_VED_BRUDD = {"unntakskø": (UNNTAK, None),
              "stopp_og_varsle": (STOPP, "varsle"),
              "frys": (STOPP, "frys")}
_DAGER = ["man", "tir", "ons", "tor", "fre", "lor", "son"]
_PERIODE = {"minutter": 60, "timer": 3600, "dager": 86400, "uker": 604800}
BETRODDE_DATAKLASSE_KILDER = {"connector", "ressurs"}


@dataclass(frozen=True)
class EvaluationContext:
    """Autentisert kontekst — settes av plattformens sesjonslag etter
    verifisert innlogging/tokenvalidering, aldri av innkommende data."""
    tenant_id: str
    aktor_rolle: str
    autentisert: bool
    kilde: str  # f.eks. "api_token", "system_jobb"


@dataclass(frozen=True)
class MenneskeligGodkjenning:
    """Et ALLEREDE MAC-verifisert menneskelig godkjenningsfaktum.

    PR-012 form (C): motorens EGEN, separate inngang for verifiserte
    menneskefakta. Den ligger ALDRI i `event["attestasjoner"]` — att-nøkkelen
    kan dermed aldri prege en menneskelig godkjenning, og en verifikator kan
    aldri utgi seg for et menneske (v7 §1). Motoren verifiserer ALDRI MAC-en
    selv; `behandle_unntakshandling` (porten) gjør det FØR kallet og
    populerer denne strukturen. Ingen API-rute, arbeider eller klient kan nå
    den (Codex-port 5).

    Feltene er de MAC-signerte konvoluttfeltene. `tenant` ligger eksplisitt i
    typen (ikke bare i konvolutten) fordi motorens likhetskontroll trenger den.
    `bundet_grunnkode` binder godkjenningen til nøyaktig ÉN blokkerende
    grunnkode (v8 §2) — motoren kan løfte KUN den.
    """
    tenant: str
    target_action: str
    ressurs_id: str | None
    belop: Decimal | None
    valuta: str | None
    hi_integritet_hash: str
    bundet_grunnkode: str
    unntak_id: int
    runde: int
    godkjennere: tuple[tuple[str, str, int], ...]  # (bruker_id, rolle, authz_version)
    godkjennings_policy_hash: str
    utloper: datetime | None


@dataclass
class Grunn:
    kode: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kode": self.kode, "params": self.params}


@dataclass
class Decision:
    beslutning: str
    handling: str
    policy_id: str
    begrunnelse: list[Grunn] = field(default_factory=list)
    unntak_kategori: str | None = None
    effekt: str | None = None
    frekvensnokkel: tuple[str, ...] | None = None  # settes ved TILLAT m/frekvens
    # Reservasjonsordre til det betrodde telleret: (nokkel, vindu_start, maks).
    # evaluate() sitt teller-oppslag er RÅDGIVENDE; den bindende kontrollen er
    # den atomiske reserver()-en i sikker_beslutning (Codex P1: TOCTOU).
    frekvensreservasjon: tuple[tuple[str, ...], datetime, int] | None = None
    # Settes KUN av den menneskelige godkjenningsgrenen når et konvoluttfelt
    # ikke stemmer med hendelsen (v8 §1): porten skal da rute sikkerhetsevidens
    # på egen forbindelse, ikke stille avvise. Default False => eksisterende
    # beslutninger uendret; feltet er ikke med i to_dict (bit-identisk logg).
    krever_sikkerhetsrouting: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"beslutning": self.beslutning, "handling": self.handling,
                "policy_id": self.policy_id,
                "begrunnelse": [g.to_dict() for g in self.begrunnelse],
                "unntak_kategori": self.unntak_kategori, "effekt": self.effekt}


class TellerLager:
    """Betrodd frekvensteller. Hendelsen kan aldri levere telleren.

    `reserver` er den BINDENDE kontrollen og MÅ være atomisk: den skal lese
    antallet og registrere forekomsten i én udelelig operasjon. Å tilby
    `antall` + `registrer` hver for seg som håndhevingsvei er nettopp
    TOCTOU-en Codex fant — to samtidige forespørsler rekker begge å lese
    «under grensen» før noen av dem registrerer. `antall` er derfor kun
    rådgivende (til beslutningsgrunnlag og logg), aldri til håndheving.
    """

    def antall(self, nokkel: tuple[str, ...], siden: datetime) -> int:
        """Rådgivende oppslag. Ikke bruk til håndheving."""
        raise NotImplementedError

    def reserver(self, nokkel: tuple[str, ...], siden: datetime, maks: int,
                 tidspunkt: datetime) -> bool:
        """Atomisk: registrer forekomsten HVIS antallet siden `siden` er < maks.

        MÅ returnere en ekte bool: `True` = plassen er reservert, `False` =
        grensen er nådd. Ingen annen returverdi er lovlig. `sikker_beslutning`
        sjekker identitet (`is True` / `is False`) og behandler alt annet —
        inkludert `None` fra en implementasjon som glemmer å returnere — som
        tellerfeil og STOPP. Sannhetsverdier som `1` eller `"ja"` teller altså
        ikke som suksess; det er med vilje, så en slurvete implementasjon
        feiler lukket i stedet for å slippe gjennom uten reservasjon.

        Implementasjoner MÅ gjøre lesing og skriving i én transaksjon/lås.
        """
        raise NotImplementedError

    def registrer(self, nokkel: tuple[str, ...], tidspunkt: datetime) -> None:
        """Ubetinget registrering — kun for testoppsett og migrering av
        historikk. ALDRI en håndhevingsvei; bruk `reserver`."""
        raise NotImplementedError


class MinneTellerLager(TellerLager):
    """For tester og staging. PostgreSQL-implementasjon kommer i PR-004.

    Atomisiteten her hviler på en prosesslokal lås, som holder så lenge alt
    kjører i én prosess. Flere prosesser eller pods deler ikke låsen —
    derfor er dette lageret eksplisitt ikke produksjonsklart, og PR-004 må
    gjøre `reserver` atomisk i databasen (én SQL-setning eller transaksjon
    med riktig isolasjonsnivå), ikke bare bytte lagringssted.
    """

    def __init__(self) -> None:
        self._hendelser: dict[tuple[str, ...], list[datetime]] = {}
        self._laas = threading.Lock()

    def antall(self, nokkel, siden):
        with self._laas:
            return sum(1 for t in self._hendelser.get(nokkel, []) if t >= siden)

    def reserver(self, nokkel, siden, maks, tidspunkt):
        with self._laas:
            if sum(1 for t in self._hendelser.get(nokkel, []) if t >= siden) >= maks:
                return False
            self._hendelser.setdefault(nokkel, []).append(tidspunkt)
            return True

    def registrer(self, nokkel, tidspunkt):
        with self._laas:
            self._hendelser.setdefault(nokkel, []).append(tidspunkt)


def _pid(policy: dict, handling_id: str) -> str:
    """Beslutningens POLICYREFERANSE: `<policy_id>@<versjon>/<handling>`.

    Dette er en ETIKETT, ikke en policy-id — og forskjellen kostet oss et
    P1 etter at PR-006 var merget. Verdien havner i
    `revisjonslogg.policy_id`, og tre steder i M-37 leste den kolonnen som
    om den var en ren policy-id. Oppslaget `WHERE policy_id = <etikett>`
    traff da aldri noe, og HVER eneste sak ble klassifisert `manuell`.

    Parseren står rett under, med vilje: bygger og leser hører sammen, og
    en endring i formatet skal være umulig å gjøre uten å se begge.
    """
    meta = policy.get("meta") or {}
    return f"{meta.get('policy_id', 'ukjent')}@{meta.get('versjon', '?')}/{handling_id}"


def les_policyref(ref: object) -> tuple[str, str] | None:
    """`<policy_id>@<versjon>/<handling>` -> (policy_id, versjon), ellers None.

    Formatet er entydig fordi skjemaet gjør det: `policy_id` matcher
    `^[a-z0-9-]+$` og `versjon` matcher `^\\d+\\.\\d+\\.\\d+$`. Verken `@`
    eller `/` kan forekomme i noen av dem, så første `@` og første `/`
    etter den deler strengen riktig uansett hva handlingen heter.

    Returnerer None — aldri en gjetning — når strengen ikke har formen.
    Kallerne behandler None som «ingen verifiserbar policyidentitet», og
    det er den fail-closed veien som allerede finnes.
    """
    if not isinstance(ref, str):
        return None
    pid, sep, resten = ref.partition("@")
    if not sep:
        return None
    # `partition` gir HELE resten etter FØRSTE skråstrek som handlingsdel.
    # Det er med vilje: `a@1.2.3/purring.send/noe` skal avvises, ikke
    # avkortes til `purring.send`. Tar vi bare første ledd, godtar vi en
    # streng `_pid` aldri kunne produsert.
    versjon, sep2, handling = resten.partition("/")
    if not sep2 or not handling:
        # Avkortet form. `a@1.2.3` og `a@1.2.3/` er ikke policyreferanser —
        # de er evidens som mangler et ledd, og en parser som fyller inn
        # det manglende leddet med stillhet er ikke fail-closed.
        return None
    if not _POLICY_ID_MONSTER.fullmatch(pid) \
            or not _VERSJON_MONSTER.fullmatch(versjon) \
            or not _HANDLING_MONSTER.fullmatch(handling):
        return None
    return pid, versjon


#: Mønstrene er KOPIER av policy-skjemaets egne (`policies/
#: policy-schema-v0.2.json`): `meta.policy_id`, `meta.versjon` og
#: `handlinger[].id`. `test_policyref_monstre_speiler_skjemaet` binder dem
#: sammen, så en endring i skjemaet ikke kan gli fra parseren.
_POLICY_ID_MONSTER = re.compile(r"[a-z0-9-]+")
_VERSJON_MONSTER = re.compile(r"\d+\.\d+\.\d+")
_HANDLING_MONSTER = re.compile(r"[a-z0-9_]+(\.[a-z0-9_]+)+")


def brudd_utfall(policy: dict, handling_id: str) -> tuple[str, str | None]:
    """Handlingens `ved_brudd` oversatt til (beslutning, effekt).

    ÉN kilde for hele motoren. Codex fant at reservasjonsgrenen i
    `sikker_beslutning` hardkodet UNNTAK: en handling med `stopp_og_varsle`
    eller `frys` ble dermed nedgradert til unntakskø når den tapte kappløpet
    om siste frekvensplass — altså akkurat i konkurransetilfellet, der den
    strengeste håndteringen er mest påkrevd. Alle blokkerende grener skal
    slå opp her, aldri gjenskape mappingen lokalt.
    """
    h = next((x for x in (policy.get("handlinger") or [])
              if isinstance(x, dict) and x.get("id") == handling_id), None)
    return _VED_BRUDD.get((h or {}).get("ved_brudd", "unntakskø"), (UNNTAK, None))


def parse_belop(verdi: Any) -> Decimal | None:
    """Decimal eller None ved ugyldig. bool avvises eksplisitt (bool er
    subtype av int i Python — review-funn D). Maks 2 desimaler, >= 0."""
    if isinstance(verdi, bool) or verdi is None:
        return None
    if not isinstance(verdi, (int, str)):
        return None  # float avvises: binær flyttall er upresist for penger
    try:
        d = Decimal(str(verdi))
    except InvalidOperation:
        return None
    if not d.is_finite() or d < 0:
        return None
    if -d.as_tuple().exponent > 2:
        return None
    return d


def _aware(ts: Any) -> datetime | None:
    """ISO 8601 MED tidssoneinfo. Naive tidsstempler avvises (funn: DST)."""
    if not isinstance(ts, str):
        return None
    try:
        t = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def _i_vindu(vindu: str, t: datetime, sone: ZoneInfo) -> bool:
    lokal = t.astimezone(sone)
    dager, klokke = vindu.split()
    d0, d1 = (_DAGER.index(x) for x in dager.split("-"))
    if not (d0 <= lokal.weekday() <= d1):
        return False
    start, slutt = klokke.split("-")
    return start <= lokal.strftime("%H:%M") <= slutt


def _evaluer(policy: dict, context: EvaluationContext | None, event: dict,
             teller: TellerLager | None = None,
             naa: datetime | None = None) -> Decision:
    naa = naa or datetime.now(timezone.utc)
    handling_id = event.get("handling") if isinstance(event.get("handling"), str) \
        else "<mangler>"

    # 0) Autentisert kontekst — før alt annet (funn 2/uautentisert rolle)
    if (context is None or not context.autentisert
            or not context.tenant_id or not context.aktor_rolle):
        return Decision(STOPP, handling_id, _pid(policy, handling_id),
                        [Grunn("uautentisert_kontekst")])

    handlinger = {h.get("id"): h for h in (policy.get("handlinger") or [])}
    h = handlinger.get(handling_id)
    if h is None:  # 1) deny by default
        return Decision(UNNTAK, handling_id, _pid(policy, handling_id),
                        [Grunn("ukjent_handling", {"handling": handling_id})],
                        unntak_kategori="ukjent")

    pid = _pid(policy, handling_id)
    ok_grunner: list[Grunn] = []
    frekvensnokkel: tuple[str, ...] | None = None
    frekvensreservasjon: tuple[tuple[str, ...], datetime, int] | None = None

    def blokker(kategori: str, grunn: Grunn,
                tving_stopp: bool = False) -> Decision:
        beslutning, effekt = (STOPP, None) if tving_stopp else \
            brudd_utfall(policy, handling_id)
        return Decision(beslutning, handling_id, pid, ok_grunner + [grunn],
                        unntak_kategori=kategori if beslutning == UNNTAK else None,
                        effekt=effekt)

    # 2) Modus
    if h.get("modus", "alltid_stopp") == "alltid_stopp":
        return blokker("regelkonflikt", Grunn("modus_alltid_stopp"))

    # 3) Rolle — fra autentisert kontekst, aldri fra event
    if context.aktor_rolle not in (h.get("tillatt_for") or []):
        return blokker("regelkonflikt",
                       Grunn("rolle_ikke_tillatt", {"rolle": context.aktor_rolle}))
    ok_grunner.append(Grunn("rolle_ok", {"rolle": context.aktor_rolle}))

    grenser = h.get("grenser") or {}

    # 4) Beløp (Decimal, funn D)
    if grenser.get("belop_maks") is not None:
        maks = parse_belop(grenser["belop_maks"])
        belop = parse_belop(event.get("belop"))
        if maks is None:
            return blokker("teknisk_feil", Grunn("policy_belopsgrense_ugyldig"),
                           tving_stopp=True)
        if belop is None:
            return blokker("ugyldig_data",
                           Grunn("belop_ugyldig", {"verdi": repr(event.get("belop"))}))
        if belop > maks:
            return blokker("over_grense", Grunn(
                "belop_over_grense", {"belop": str(belop), "grense": str(maks)}))
        ok_grunner.append(Grunn("belop_ok", {"belop": str(belop)}))

    # 5) Valuta
    if grenser.get("valuta"):
        if event.get("valuta") not in grenser["valuta"]:
            return blokker("regelkonflikt", Grunn(
                "valuta_ikke_tillatt", {"valuta": str(event.get("valuta"))}))
        ok_grunner.append(Grunn("valuta_ok", {"valuta": event["valuta"]}))

    # 6) Tidsvindu — i policyens IANA-sone; naive tidsstempler avvises
    if grenser.get("tidsvindu"):
        try:
            sone = ZoneInfo(str(policy.get("tidssone")))
        except Exception:
            return blokker("teknisk_feil", Grunn("policy_tidssone_ugyldig"),
                           tving_stopp=True)
        t = _aware(event.get("tidspunkt"))
        if t is None:
            return blokker("ugyldig_data", Grunn("tidspunkt_ugyldig_eller_naivt"))
        if not _i_vindu(grenser["tidsvindu"], t, sone):
            return blokker("over_grense", Grunn(
                "utenfor_tidsvindu", {"vindu": grenser["tidsvindu"]}))
        ok_grunner.append(Grunn("tidsvindu_ok"))

    # 7) Frekvens — strukturert regel + betrodd teller (funn A)
    fr = grenser.get("frekvens")
    if fr:
        if teller is None:
            return blokker("teknisk_feil", Grunn("frekvens_uten_tellerlager"),
                           tving_stopp=True)
        nokkel_felt = fr["grupperingsnokkel"]
        gruppe = event.get(nokkel_felt)
        if not isinstance(gruppe, str) or not gruppe:
            return blokker("manglende_data", Grunn(
                "frekvens_grupperingsverdi_mangler", {"felt": nokkel_felt}))
        frekvensnokkel = (context.tenant_id, handling_id, nokkel_felt, gruppe)
        vindu_start = naa - timedelta(
            seconds=fr["periode_antall"] * _PERIODE[fr["periode_enhet"]])
        # Rådgivende oppslag: avviser det åpenbare tidlig og gir loggen tall.
        # Den BINDENDE kontrollen er reservasjonen under, som sikker_beslutning
        # utfører atomisk før noen sideeffekt kan skje.
        antall = teller.antall(frekvensnokkel, vindu_start)
        if antall >= fr["maks"]:
            return blokker("over_grense", Grunn(
                "frekvensgrense_naadd", {"antall": antall, "maks": fr["maks"]}))
        frekvensreservasjon = (frekvensnokkel, vindu_start, fr["maks"])
        ok_grunner.append(Grunn("frekvens_ok", {"antall": antall, "maks": fr["maks"]}))

    # 8) Dataklasser — fail-closed (funn C)
    tillatt = h.get("dataklasser_tillatt") or []
    if tillatt:
        brukte = event.get("dataklasser")
        kilde = event.get("dataklasser_kilde")
        if not brukte or not isinstance(brukte, list):
            return blokker("manglende_data", Grunn("dataklassifisering_mangler"))
        if kilde not in BETRODDE_DATAKLASSE_KILDER:
            return blokker("manglende_data", Grunn(
                "dataklassifisering_ubetrodd_kilde", {"kilde": str(kilde)}))
        ulovlige = set(brukte) - set(tillatt)
        if ulovlige:
            return blokker("regelkonflikt", Grunn(
                "dataklasse_ikke_tillatt", {"klasser": sorted(ulovlige)}))
        ok_grunner.append(Grunn("dataklasser_ok"))

    # 9) Vilkår — attestasjoner fra betrodde verifikatorer (funn 2)
    verifikatorer = policy.get("verifikatorer") or {}
    attester = event.get("attestasjoner") or {}
    ressurs = event.get("ressurs_id")
    for vk in h.get("vilkaar") or []:
        navn = vk["navn"]
        att = attester.get(navn)
        if not isinstance(att, dict):
            return blokker("manglende_data", Grunn(
                "attestasjon_mangler", {"vilkaar": navn}))
        vid = att.get("verifikator")
        betrodd = verifikatorer.get(vid)
        if not betrodd or navn not in betrodd.get("betrodd_for", []):
            return blokker("regelkonflikt", Grunn(
                "verifikator_ikke_betrodd", {"vilkaar": navn,
                                             "verifikator": str(vid)}),
                tving_stopp=True)
        if not isinstance(ressurs, str) or not ressurs \
                or att.get("ressurs_id") != ressurs:
            return blokker("regelkonflikt", Grunn(
                "attestasjon_feil_ressurs", {"vilkaar": navn}))
        utloper = _aware(att.get("utloper"))
        if utloper is None or utloper <= naa:
            return blokker("manglende_data", Grunn(
                "attestasjon_utlopt", {"vilkaar": navn}))
        if "min" in vk:
            verdi = att.get("verdi")
            if isinstance(verdi, bool) or not isinstance(verdi, (int, float)):
                return blokker("ugyldig_data", Grunn(
                    "attestasjon_verdi_ugyldig", {"vilkaar": navn}))
            if verdi < vk["min"]:
                return blokker("regelkonflikt", Grunn(
                    "attestasjon_under_terskel",
                    {"vilkaar": navn, "verdi": verdi, "min": vk["min"]}))
        elif att.get("resultat") is not True:
            return blokker("regelkonflikt", Grunn(
                "attestasjon_negativ", {"vilkaar": navn}))
        ok_grunner.append(Grunn("vilkaar_ok", {"vilkaar": navn}))

    ok_grunner.append(Grunn("alle_kontroller_bestatt"))
    return Decision(TILLAT, handling_id, pid, ok_grunner,
                    frekvensnokkel=frekvensnokkel,
                    frekvensreservasjon=frekvensreservasjon)


# --------------------------------------------------------------------------
# PR-012 (C): motorens EGEN inngang for verifiserte menneskefakta.
#
# Presisering 3 (den skarpeste): den nye grenen legger seg ETTER den ordinære
# evalueringen og endrer den ALDRI. `evaluate` uten `menneskelig_godkjenning`
# er nøyaktig `_evaluer` — samme beslutning OG samme begrunnelseskjede,
# bit-identisk (regresjonsport 6). Fristelsen ved «samle alle blokkerende
# grunner» er å restrukturere evalueringsløkken; det ville endret
# begrunnelseskjeden for HVER beslutning i systemet. Derfor kjører vi den
# ordinære veien uendret, og — kun ved en godkjenning — kjører vi den EN GANG
# TIL mot en policy der nøyaktig den ene bundne kontrollen er hevet. «Flere
# blokkerende grunner» faller da naturlig ut: pass 2 treffer neste blokk og
# gir ingen TILLAT.
# --------------------------------------------------------------------------

def evaluate(policy: dict, context: EvaluationContext | None, event: dict,
             teller: TellerLager | None = None, naa: datetime | None = None,
             *, menneskelig_godkjenning: "MenneskeligGodkjenning | None" = None
             ) -> Decision:
    """Motorens ene inngang.

    Uten `menneskelig_godkjenning`: identisk med `_evaluer` (samme utfall og
    begrunnelseskjede — PR-012 P3/port 6). Parameteren kan KUN settes av
    `behandle_unntakshandling`, som har MAC-verifisert konvolutten på forhånd;
    motoren verifiserer aldri MAC-en selv.
    """
    naa = naa or datetime.now(timezone.utc)
    grunnvedtak = _evaluer(policy, context, event, teller, naa)
    if menneskelig_godkjenning is None:
        return grunnvedtak
    return _anvend_menneskelig_godkjenning(
        policy, context, event, teller, naa, grunnvedtak, menneskelig_godkjenning)


def _policy_innholds_hash(policy: dict) -> str:
    """Kanonisk SHA-256 over policyen. MÅ være bit-identisk med
    `api.policyregister.innholds_hash`. Duplisert her — ikke importert —
    fordi `policy_validator` ikke skal avhenge av `api`;
    `test_policy_innholds_hash_speiler_registeret` binder de to sammen så en
    endring ikke kan gli fra hverandre."""
    return hashlib.sha256(json.dumps(
        policy, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _menneskelig_feltavvik(mg: "MenneskeligGodkjenning",
                           context: EvaluationContext | None,
                           event: dict) -> str | None:
    """Navnet på det FØRSTE konvoluttfeltet som ikke stemmer med hendelsen,
    ellers None. Alle seks må være eksakt like (v8 §1) — et avvik betyr at
    konvolutten forsøkes brukt på noe annet enn den ble gitt for."""
    handling = event.get("handling") if isinstance(event.get("handling"), str) \
        else None
    if mg.tenant != (context.tenant_id if context else None):
        return "tenant"
    if mg.target_action != handling:
        return "target_action"
    if mg.ressurs_id != event.get("ressurs_id"):
        return "ressurs_id"
    if mg.belop != parse_belop(event.get("belop")):
        return "belop"
    if mg.valuta != event.get("valuta"):
        return "valuta"
    if mg.hi_integritet_hash != event.get("hi_integritet_hash"):
        return "hi_integritet_hash"
    return None


def _finn_godkjennbar(policy: dict, grunnkode: str,
                      handling_id: str) -> tuple[dict | None, dict]:
    """(godkjennbar-oppføring for (grunnkode, handling), menneskelig_overstyring).
    Oppføringen er None når paret ikke er godkjennbart — da er godkjenningen
    usynlig for motoren."""
    mo = policy.get("menneskelig_overstyring")
    if not isinstance(mo, dict):
        return None, {}
    for e in mo.get("godkjennbare") or []:
        if isinstance(e, dict) and e.get("grunnkode") == grunnkode \
                and e.get("handling") == handling_id:
            return e, mo
    return None, mo


def _loft_policy(policy: dict, handling_id: str, grunnkode: str,
                 entry: dict) -> dict | None:
    """Kopi av policyen der NØYAKTIG den kontrollen som ga `grunnkode` er
    hevet for `handling_id` — resten uendret. None hvis grunnkoden ikke lar
    seg uttrykke som et løft (fail-closed: ingen overstyring).

    Motoren løfter kun det schemaet kan uttrykke (belop_maks / valuta). En
    grunnkode fra en kontroll uten et slikt uttrykk (tidsvindu, frekvens,
    dataklasse) gir None og dermed ingen TILLAT — presis den fail-closed
    oppførselen v8 §2 krever."""
    ny = dict(policy)
    handlinger = []
    truffet = False
    for h in policy.get("handlinger") or []:
        if isinstance(h, dict) and h.get("id") == handling_id:
            h = copy.deepcopy(h)
            grenser = h.setdefault("grenser", {})
            if grunnkode == "belop_over_grense":
                if entry.get("belop_maks") is None:
                    return None
                grenser["belop_maks"] = entry["belop_maks"]
            elif grunnkode == "valuta_ikke_tillatt":
                if entry.get("valuta") is None:
                    return None
                vs = list(grenser.get("valuta") or [])
                if entry["valuta"] not in vs:
                    vs.append(entry["valuta"])
                grenser["valuta"] = vs
            else:
                return None
            truffet = True
        handlinger.append(h)
    if not truffet:
        return None
    ny["handlinger"] = handlinger
    return ny


def _anvend_menneskelig_godkjenning(
        policy: dict, context: EvaluationContext | None, event: dict,
        teller: TellerLager | None, naa: datetime,
        grunnvedtak: Decision, mg: "MenneskeligGodkjenning") -> Decision:
    handling_id = event.get("handling") if isinstance(event.get("handling"), str) \
        else "<mangler>"
    pid = _pid(policy, handling_id)

    def stopp(grunn: Grunn, *, sikkerhet: bool = False) -> Decision:
        return Decision(STOPP, handling_id, pid, [grunn],
                        krever_sikkerhetsrouting=sikkerhet)

    # Var saken TILLAT allerede? Da er den bundne grunnen ikke blokkerende
    # lenger (v8 §2) — dagens utfall står, godkjenningen er usynlig.
    if grunnvedtak.beslutning == TILLAT:
        return grunnvedtak
    blokk_grunn = grunnvedtak.begrunnelse[-1].kode if grunnvedtak.begrunnelse \
        else None

    # 1) Eksakt likhet på alle seks konvoluttfelt (v8 §1). Ett avvik =>
    #    STOPP + sikkerhetsrouting.
    avvik = _menneskelig_feltavvik(mg, context, event)
    if avvik is not None:
        return stopp(Grunn("godkjenning_feltavvik", {"felt": avvik}),
                     sikkerhet=True)

    # 2) Policyhash: godkjenningen ble gitt mot en bestemt policy (v7 §4).
    if mg.godkjennings_policy_hash != _policy_innholds_hash(policy):
        return stopp(Grunn("godkjenning_policy_avvik"), sikkerhet=True)

    # 3) Utløp — porten sjekker også, men motoren stoler ikke blindt.
    if mg.utloper is None or mg.utloper <= naa:
        return stopp(Grunn("godkjenning_utlopt"))

    # 4) Er den bundne grunnkoden faktisk den saken stoppet på? Ellers er
    #    situasjonen en annen enn den mennesket vurderte => ingen overstyring.
    if mg.bundet_grunnkode != blokk_grunn:
        return grunnvedtak

    # 5) Er (grunnkode, handling) godkjennbart? Ellers usynlig for motoren.
    entry, mo = _finn_godkjennbar(policy, mg.bundet_grunnkode, handling_id)
    if entry is None:
        return grunnvedtak

    # 6) krever_rolle: minst én godkjenner må ha den påkrevde rollen.
    krever_rolle = mo.get("krever_rolle")
    if krever_rolle and not any(r == krever_rolle for _, r, _ in mg.godkjennere):
        return stopp(Grunn("godkjenning_rolle_mangler",
                           {"krever_rolle": krever_rolle}), sikkerhet=True)

    # 7) Grensen EIES av motoren (v7 §2), mot hendelsens autoritative verdier
    #    (likheten er bevist i steg 1).
    if entry.get("valuta") is not None and event.get("valuta") != entry["valuta"]:
        return stopp(Grunn("godkjenning_valuta_avvik",
                           {"valuta": str(event.get("valuta"))}))
    maks = entry.get("belop_maks")
    if maks is not None:
        maksd = parse_belop(maks)
        if maksd is None:
            return stopp(Grunn("godkjenning_belopsgrense_ugyldig"),
                         sikkerhet=True)
        belop = parse_belop(event.get("belop"))
        if belop is None or belop > maksd:
            return stopp(Grunn("godkjenning_belop_over_maks",
                               {"belop": repr(event.get("belop")),
                                "grense": str(maksd)}))

    # 8) Løft KUN den bundne grunnkoden og kjør den ordinære veien på nytt.
    loftet = _loft_policy(policy, handling_id, mg.bundet_grunnkode, entry)
    if loftet is None:
        return grunnvedtak
    nytt = _evaluer(loftet, context, event, teller, naa)
    if nytt.beslutning != TILLAT:
        return nytt  # flere blokkerende grunner => ingen TILLAT (v8 §2)

    # 9) TILLAT: loggfør den BUNDNE grunnkoden fra konvolutten (v8 §2), ikke
    #    den motoren tilfeldigvis anvendte etterpå.
    grunner = list(nytt.begrunnelse) + [Grunn("menneskelig_godkjenning_anvendt", {
        "runde": mg.runde,
        "godkjennere": [b for b, _, _ in mg.godkjennere],
        "bundet_grunnkode": mg.bundet_grunnkode,
        "belop_maks": str(parse_belop(maks)) if maks is not None else None,
        "godkjennings_policy_hash": mg.godkjennings_policy_hash})]
    return Decision(TILLAT, handling_id, pid, grunner,
                    frekvensnokkel=nytt.frekvensnokkel,
                    frekvensreservasjon=nytt.frekvensreservasjon)
