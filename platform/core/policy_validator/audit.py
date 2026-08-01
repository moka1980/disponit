"""M-2 Revisjonslogg og evidens — loggformat v0.1.

Krav fra M-1-aksept i prototype v7.2:
  «100 % av skrivehandlinger har policy-ID, aktør, input-hash og
   begrunnelse; blokkerte handlinger utføres aldri.»

Loggen er append-only JSONL. Hashen er deterministisk (kanonisk JSON),
slik at samme hendelse alltid gir samme input-hash — det gjør
beslutninger etterprøvbare og dubletter oppdagbare.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:                        # POSIX: gir oss lås også mellom prosesser
    import fcntl
except ImportError:         # Windows: kun prosesslokal lås (se skriv())
    fcntl = None            # type: ignore[assignment]

from .engine import Decision


def input_hash(event: dict) -> str:
    kanonisk = json.dumps(event, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), default=str)
    return hashlib.sha256(kanonisk.encode("utf-8")).hexdigest()


def lag_loggpost(decision: Decision, event: dict, policy: dict,
                 context=None) -> dict[str, Any]:
    """Loggposten for M-2.

    `aktor`, `tenant` og `kilde` hentes fra den AUTENTISERTE konteksten —
    aldri fra hendelsen (Codex P1). Motoren nektet allerede å lese rollen
    fra payloaden; gjorde loggen det, kunne en innsender skrive hvilken som
    helst aktør inn i revisjonssporet og dermed peke skylden et annet sted.
    Uten kontekst logges `null`, ikke en selvrapportert verdi.
    """
    meta = policy.get("meta") or {}
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "input_hash": input_hash(event),
        "aktor": getattr(context, "aktor_rolle", None),
        "tenant": getattr(context, "tenant_id", None),
        "kilde": getattr(context, "kilde", None),
        "policy_id": decision.policy_id,
        "bransjemal": meta.get("bransjemal"),
        "mal_status": meta.get("status"),
        "schema_version": policy.get("schema_version"),
        "beslutning": decision.beslutning,
        "unntak_kategori": decision.unntak_kategori,
        "effekt": decision.effekt,
        "begrunnelse": [g.to_dict() for g in decision.begrunnelse],
    }


_FILLAASER: dict[str, threading.Lock] = {}
_FILLAASER_LAAS = threading.Lock()


def _fillaas(sti: Path) -> threading.Lock:
    """Én lås per loggfil, delt av alle tråder i prosessen."""
    nokkel = str(Path(sti).resolve())
    with _FILLAASER_LAAS:
        return _FILLAASER.setdefault(nokkel, threading.Lock())


def _skriv_raa(fd: int, data: bytes) -> None:
    """Skriver hele bufferet i ett kall.

    Egen funksjon fordi den er sømmen testene trenger: en test erstatter
    denne med en variant som deler skrivingen i to med en pause imellom, og
    beviser dermed at låsen holder rundt HELE operasjonen. Uten en slik søm
    blir samtidighetstesten tidsavhengig — og en tidsavhengig test godkjente
    allerede én gang koden som ble underkjent i review.
    """
    os.write(fd, data)


def skriv(loggfil: Path, post: dict) -> None:
    """Append-only, og serialisert.

    Codex fant at 20 samtidige beslutninger ga 19 loggposter: hvert kall
    åpnet sin egen bufrede filhåndtak, så to skrivinger kunne flettes inn i
    hverandre og ødelegge en linje. Da kan TILLAT returneres uten en
    tilhørende revisjonspost — brudd på 1:1-kontrakten, som er selve
    beviset M-2 skal levere.

    Tre lag nå:
      1. prosesslokal lås per fil — serialiserer trådene
      2. `flock` der plattformen har det — serialiserer også prosesser
      3. ett `os.write` med hele linjen, uten Pythons bufring, etterfulgt av
         `fsync` slik at posten faktisk står på disk før kallet returnerer

    Uten `fcntl` (Windows) faller den tilbake til lag 1 og 3. Det dekker én
    prosess; flere prosesser mot samme fil krever lag 2, og produksjons-
    loggen i PR-004 bør uansett være en database med samme garanti.
    """
    linje = (json.dumps(post, ensure_ascii=False) + "\n").encode("utf-8")
    loggfil = Path(loggfil)
    loggfil.parent.mkdir(parents=True, exist_ok=True)
    with _fillaas(loggfil):
        fd = os.open(loggfil, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            _skriv_raa(fd, linje)
            os.fsync(fd)
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)


# ---------------------------------------------------------------------------
# PR-002: logg-før-utførelse-kontrakten (review spm. 3: revisjonsloggfeil
# og «bare TILLAT kan utløse sideeffekt»).
# ---------------------------------------------------------------------------
from .engine import (STOPP, UNNTAK, Decision, EvaluationContext,  # noqa: E402
                     Grunn, TellerLager, brudd_utfall)


def sikker_beslutning(policy: dict, context, event: dict, loggfil: Path,
                      teller: "TellerLager | None" = None,
                      naa=None) -> Decision:
    """ENESTE lovlige inngang for moduler som skal utføre skrivehandlinger.

    Kontrakt (fail-closed i alle grener):
      1. evaluate() kastes aldri videre — uventet exception => STOPP.
      2. Beslutningen logges FØR den returneres. Kan loggen ikke skrives,
         returneres STOPP (teknisk_feil) — en skrivehandling uten sikret
         revisjonslogg er forbudt (M-1-aksept).
      3. Frekvensplassen reserveres ATOMISK i det betrodde telleret FØR
         loggen skrives og før TILLAT returneres. Rekkefølgen er bevisst:
         reserver -> logg -> retur. Taper hendelsen kappløpet om siste
         plass, blir beslutningen blokkert og LOGGEN VISER det som faktisk
         ble bestemt. Feiler telleret, er det STOPP — aldri gjetting
         (Codex P1: registreringen lå utenfor try og kunne kaste etter at
         TILLAT allerede var logget).
      4. Kalleren får utføre sideeffekten HVIS OG BARE HVIS returverdien
         er TILLAT. STOPP, UNNTAK, exception og timeout er alle nei.

    Kjent skjevhet, bevisst valgt: feiler loggskrivingen ETTER en vellykket
    reservasjon, returneres STOPP mens plassen er brukt opp. Det teller for
    strengt, aldri for mildt — riktig vei å bomme for en fullmaktsmotor.
    """
    from .engine import evaluate  # lokal import unngår sirkularitet
    from datetime import datetime, timezone
    naa = naa or datetime.now(timezone.utc)
    try:
        d = evaluate(policy, context, event, teller=teller, naa=naa)
    except Exception as e:  # fail-closed, aldri gjetting
        d = Decision(STOPP, str(event.get("handling")), "ukjent",
                     [Grunn("motor_exception", {"type": type(e).__name__})])

    # Bindende frekvenskontroll: atomisk, før logg og retur.
    if d.beslutning == "TILLAT" and d.frekvensreservasjon is not None:
        nokkel, vindu_start, maks = d.frekvensreservasjon
        if teller is None:
            d = Decision(STOPP, d.handling, d.policy_id,
                         d.begrunnelse + [Grunn("frekvens_uten_tellerlager")])
        else:
            try:
                fikk_plass = teller.reserver(nokkel, vindu_start, maks, naa)
            except Exception as e:
                d = Decision(STOPP, d.handling, d.policy_id,
                             d.begrunnelse + [Grunn(
                                 "tellerfeil", {"type": type(e).__name__})])
            else:
                # Nøyaktig tre utfall er lovlige. Å behandle «ikke False» som
                # suksess er fail-OPEN: en implementasjon som glemmer å
                # returnere gir None, og None ville gitt TILLAT uten at noen
                # plass faktisk ble reservert (Codex P1, runde 3).
                if fikk_plass is True:
                    pass                       # plassen er bekreftet reservert
                elif fikk_plass is False:
                    # Samme ved_brudd-håndtering som evaluate() ville gitt.
                    # Hardkodet UNNTAK her nedgraderte stopp_og_varsle og frys
                    # (Codex P1, runde 2) — nettopp i konkurransetilfellet.
                    beslutning, effekt = brudd_utfall(policy, d.handling)
                    d = Decision(beslutning, d.handling, d.policy_id,
                                 d.begrunnelse + [Grunn(
                                     "frekvensgrense_naadd_ved_reservasjon",
                                     {"maks": maks})],
                                 unntak_kategori=("over_grense"
                                                  if beslutning == UNNTAK
                                                  else None),
                                 effekt=effekt)
                else:
                    d = Decision(STOPP, d.handling, d.policy_id,
                                 d.begrunnelse + [Grunn(
                                     "tellerfeil",
                                     {"type": "ugyldig_returverdi",
                                      "verdi": repr(fikk_plass)})])

    try:
        skriv(loggfil, lag_loggpost(d, event, policy, context))
    except Exception:
        return Decision(STOPP, d.handling, d.policy_id,
                        d.begrunnelse + [Grunn("logging_feilet")])
    return d
