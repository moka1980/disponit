"""M-1 Policy- og fullmaktsmotor — beslutningsmotor v0.1.

Deterministisk. Ingen LLM, ingen integrasjoner, ingen sideeffekter.
Evaluerer én hendelse mot én policy og svarer TILLAT / STOPP / UNNTAK
med maskinlesbar begrunnelse.

Designregler (fra prototype v7.2, M-1):
  - Deny by default: udefinert handling er forbudt.
  - Konflikt eller tvil gir alltid stopp, aldri gjetting.
  - Hver beslutning har policy-ID og begrunnelse (kravet i M-1 aksept).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

TILLAT = "TILLAT"
STOPP = "STOPP"
UNNTAK = "UNNTAK"

_VED_BRUDD_TIL_BESLUTNING = {
    "unntakskø": (UNNTAK, None),
    "stopp_og_varsle": (STOPP, "varsle"),
    "frys": (STOPP, "frys"),
}

_DAGER = ["man", "tir", "ons", "tor", "fre", "lor", "son"]


@dataclass
class Decision:
    beslutning: str
    handling: str
    policy_id: str
    begrunnelse: list[str] = field(default_factory=list)
    unntak_kategori: str | None = None
    effekt: str | None = None  # "frys" | "varsle" | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "beslutning": self.beslutning,
            "handling": self.handling,
            "policy_id": self.policy_id,
            "begrunnelse": self.begrunnelse,
            "unntak_kategori": self.unntak_kategori,
            "effekt": self.effekt,
        }


def _policy_id(policy: dict, handling_id: str) -> str:
    mal = (policy.get("meta") or {}).get("bransjemal") or "ukjent-mal"
    ver = policy.get("schema_version", "?")
    return f"{mal}/{handling_id}@{ver}"


def _i_tidsvindu(vindu: str, tidspunkt: datetime) -> bool:
    """Tolker f.eks. 'man-fre 07:00-17:00'. Ukjent format => False (stopp)."""
    try:
        dager_del, klokke_del = vindu.strip().split()
        start_dag, slutt_dag = dager_del.split("-")
        d0, d1 = _DAGER.index(start_dag), _DAGER.index(slutt_dag)
        if not (d0 <= tidspunkt.weekday() <= d1):
            return False
        start_kl, slutt_kl = klokke_del.split("-")
        t = tidspunkt.strftime("%H:%M")
        return start_kl <= t <= slutt_kl
    except (ValueError, IndexError):
        return False  # uleselig vindu skal aldri åpne for handling


def _sjekk_vilkaar(krav: Any, oppgitt: dict) -> tuple[bool, str | None, str]:
    """Ett vilkår. Returnerer (ok, unntak_kategori, tekst)."""
    if isinstance(krav, str):
        if krav not in oppgitt:
            return False, "manglende_data", f"vilkår '{krav}' mangler i hendelsen"
        if oppgitt[krav] is not True:
            return False, "regelkonflikt", f"vilkår '{krav}' er ikke oppfylt"
        return True, None, f"vilkår '{krav}' oppfylt"
    if isinstance(krav, dict) and len(krav) == 1:
        navn, terskel = next(iter(krav.items()))
        if navn not in oppgitt:
            return False, "manglende_data", f"vilkår '{navn}' mangler i hendelsen"
        verdi = oppgitt[navn]
        if not isinstance(verdi, (int, float)) or verdi < terskel:
            return False, "regelkonflikt", (
                f"vilkår '{navn}'={verdi} under terskel {terskel}")
        return True, None, f"vilkår '{navn}'={verdi} >= {terskel}"
    return False, "regelkonflikt", f"uleselig vilkår: {krav!r}"


def evaluate(policy: dict, event: dict) -> Decision:
    """Evaluerer en hendelse. `event` er ren data — motoren stoler aldri
    på den utover å lese felt; manglende felt gir stopp, aldri antakelse.

    Forventede event-felt:
      handling (str, påkrevd), aktor_rolle (str), belop (tall), valuta (str),
      tidspunkt (ISO 8601-str), dataklasser (list), vilkaar (dict),
      frekvens_teller (int, antall allerede utført i vinduet)
    """
    handling_id = event.get("handling") or "<mangler>"
    handlinger = {h.get("id"): h for h in (policy.get("handlinger") or [])}

    # 1) Deny by default
    h = handlinger.get(handling_id)
    if h is None:
        return Decision(UNNTAK, handling_id, _policy_id(policy, handling_id),
                        ["handlingen er ikke definert i policy (deny by default)"],
                        unntak_kategori="ukjent")

    pid = _policy_id(policy, handling_id)
    grunner: list[str] = []

    def blokker(kategori: str, tekst: str) -> Decision:
        ved_brudd = h.get("ved_brudd", "unntakskø")
        beslutning, effekt = _VED_BRUDD_TIL_BESLUTNING.get(
            ved_brudd, (UNNTAK, None))
        return Decision(beslutning, handling_id, pid, grunner + [tekst],
                        unntak_kategori=kategori if beslutning == UNNTAK else None,
                        effekt=effekt)

    # 2) Modus
    modus = h.get("modus", "alltid_stopp")
    if modus == "alltid_stopp":
        return blokker("regelkonflikt", "modus er alltid_stopp for denne handlingen")

    # 3) Rolle
    tillatt_for = h.get("tillatt_for") or []
    rolle = event.get("aktor_rolle")
    if rolle not in tillatt_for:
        return blokker("regelkonflikt",
                       f"rolle '{rolle}' er ikke i tillatt_for {tillatt_for}")
    grunner.append(f"rolle '{rolle}' tillatt")

    # 4) Grenser
    grenser = h.get("grenser") or {}
    belop_maks = grenser.get("belop_maks")
    if belop_maks is not None:
        belop = event.get("belop")
        if not isinstance(belop, (int, float)):
            return blokker("manglende_data", "beløp mangler, men grense er satt")
        if belop > belop_maks:
            return blokker("over_grense", f"beløp {belop} > grense {belop_maks}")
        grunner.append(f"beløp {belop} <= grense {belop_maks}")
    valutaer = grenser.get("valuta")
    if valutaer:
        if event.get("valuta") not in valutaer:
            return blokker("regelkonflikt",
                           f"valuta '{event.get('valuta')}' ikke i {valutaer}")
        grunner.append(f"valuta '{event.get('valuta')}' godkjent")
    vindu = grenser.get("tidsvindu")
    if vindu:
        ts = event.get("tidspunkt")
        try:
            tid = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            return blokker("manglende_data",
                           "tidspunkt mangler/uleselig, men tidsvindu er satt")
        if not _i_tidsvindu(vindu, tid):
            return blokker("over_grense", f"tidspunkt {ts} utenfor vindu '{vindu}'")
        grunner.append(f"innenfor tidsvindu '{vindu}'")
    frekvens = grenser.get("frekvens_maks")
    if isinstance(frekvens, int):
        teller = event.get("frekvens_teller")
        if not isinstance(teller, int):
            return blokker("manglende_data",
                           "frekvens_teller mangler, men frekvensgrense er satt")
        if teller >= frekvens:
            return blokker("over_grense",
                           f"frekvens {teller} har nådd grensen {frekvens}")
        grunner.append(f"frekvens {teller} < {frekvens}")

    # 5) Dataklasser
    tillatte_klasser = h.get("dataklasser_tillatt")
    if tillatte_klasser:
        brukte = set(event.get("dataklasser") or [])
        ulovlige = brukte - set(tillatte_klasser)
        if ulovlige:
            return blokker("regelkonflikt",
                           f"dataklasser {sorted(ulovlige)} ikke tillatt")
        grunner.append("dataklasser godkjent")

    # 6) Vilkår — alle må bestå; første brudd stopper
    oppgitt = event.get("vilkaar") or {}
    for krav in h.get("vilkaar") or []:
        ok, kategori, tekst = _sjekk_vilkaar(krav, oppgitt)
        if not ok:
            return blokker(kategori, tekst)
        grunner.append(tekst)

    grunner.append("alle policykontroller bestått")
    return Decision(TILLAT, handling_id, pid, grunner)
