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

    def to_dict(self) -> dict[str, Any]:
        return {"beslutning": self.beslutning, "handling": self.handling,
                "policy_id": self.policy_id,
                "begrunnelse": [g.to_dict() for g in self.begrunnelse],
                "unntak_kategori": self.unntak_kategori, "effekt": self.effekt}


class TellerLager:
    """Betrodd, atomisk frekvensteller. Hendelsen kan aldri levere telleren."""

    def antall(self, nokkel: tuple[str, ...], siden: datetime) -> int:
        raise NotImplementedError

    def registrer(self, nokkel: tuple[str, ...], tidspunkt: datetime) -> None:
        raise NotImplementedError


class MinneTellerLager(TellerLager):
    """For tester og staging. PostgreSQL-implementasjon kommer i PR-004."""

    def __init__(self) -> None:
        self._hendelser: dict[tuple[str, ...], list[datetime]] = {}

    def antall(self, nokkel, siden):
        return sum(1 for t in self._hendelser.get(nokkel, []) if t >= siden)

    def registrer(self, nokkel, tidspunkt):
        self._hendelser.setdefault(nokkel, []).append(tidspunkt)


def _pid(policy: dict, handling_id: str) -> str:
    meta = policy.get("meta") or {}
    return f"{meta.get('policy_id', 'ukjent')}@{meta.get('versjon', '?')}/{handling_id}"


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


def evaluate(policy: dict, context: EvaluationContext | None, event: dict,
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

    def blokker(kategori: str, grunn: Grunn,
                tving_stopp: bool = False) -> Decision:
        beslutning, effekt = (STOPP, None) if tving_stopp else \
            _VED_BRUDD.get(h.get("ved_brudd", "unntakskø"), (UNNTAK, None))
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
        antall = teller.antall(frekvensnokkel, vindu_start)
        if antall >= fr["maks"]:
            return blokker("over_grense", Grunn(
                "frekvensgrense_naadd", {"antall": antall, "maks": fr["maks"]}))
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
                    frekvensnokkel=frekvensnokkel)
