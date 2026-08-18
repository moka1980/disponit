"""M-2 Revisjonslogg og evidens — loggformat v0.1.

Krav fra M-1-aksept i prototypen (ordlyden er uendret fra v7.2 til v8):
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

import tekstbytes

from .engine import Decision


def input_hash(event: dict) -> str:
    """Kanonisk hash over hendelsen, til loggposten.

    `tekstbytes.utf8` og ikke `str.encode` (Codex P2): hendelsen er
    ubetrodd, og et ensomt surrogat i et hvilket som helst felt gjorde
    denne linjen til et unntak. Den kjører inne i loggskrivingens vakt, så
    utfallet var at beslutningen ble til `logging_feilet` — og da er det
    ikke bare hashen som mangler, hele revisjonsposten uteble. En avsender
    som ville unngå å bli logget for et brudd trengte altså bare å legge
    `"\\ud800"` i et felt.
    """
    kanonisk = json.dumps(event, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), default=str)
    return hashlib.sha256(tekstbytes.utf8(kanonisk)).hexdigest()


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
    """Skriver HELE bufferet, eller kaster.

    `os.write` er ikke write-all: den kan returnere færre bytes enn bedt om
    uten å kaste. Ignorerer man returverdien, blir en avkortet loggpost
    behandlet som vellykket — Codex demonstrerte nettopp det på `7ea2955`
    (240 bytes skrevet, linjen manglet avsluttende linjeskift). En halv
    loggpost er verre enn ingen: den ser ut som evidens.

    Null bytes skrevet er ingen fremgang; da kastes det i stedet for å
    spinne i en evig løkke. `InterruptedError` (EINTR) er derimot normal
    signalhåndtering og forsøkes på nytt.

    Egen funksjon fordi den også er sømmen testene trenger: de erstatter den
    for å bevise at fillåsen holder rundt hele operasjonen.
    """
    skrevet = 0
    n_data = len(data)
    while skrevet < n_data:
        try:
            n = os.write(fd, data[skrevet:])
        except InterruptedError:      # EINTR — ikke en feil, prøv igjen
            continue
        if n <= 0:                    # ingen fremgang: kast, aldri løkk evig
            raise OSError(
                f"os.write skrev {n} bytes av {n_data - skrevet} gjenstående "
                f"— revisjonsposten er ufullstendig")
        skrevet += n


def _mangler_avsluttende_linjeskift(fd: int) -> bool:
    """Er filen ikke-tom OG slutter på noe annet enn linjeskift?

    Da ble forrige post avkortet (delvis skriving som feilet), og neste post
    ville smeltet sammen med den. Én uleselig post er ille nok; to er verre.
    """
    try:
        storrelse = os.fstat(fd).st_size
        if storrelse == 0:
            return False
        if hasattr(os, "pread"):
            siste = os.pread(fd, 1, storrelse - 1)
        else:                       # Windows
            os.lseek(fd, storrelse - 1, os.SEEK_SET)
            siste = os.read(fd, 1)
        return siste != b"\n"
    except OSError:
        return False                # kan vi ikke lese, gjetter vi ikke


def skriv(loggfil: Path, post: dict) -> None:
    """Append-only, og serialisert. **KUN UTVIKLING** (ADR-001).

    Denne filbaserte loggen er utviklingsverktøy: lokal kjøring og
    `run_synthetic.py`. Produksjons- og stagingveien er PostgreSQL, se
    `platform/core/db/pg.py`. ADR-001 slår fast at nye feilklasser her
    lukkes ved å henvise til databasevarianten, ikke ved å lappe filen
    videre — fire av seks P1-funn i PR-002 lå nettopp her.

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
        # O_RDWR fordi vi må kunne LESE siste byte, se _avslutt_halv_linje.
        # O_APPEND gjelder uansett: skrivinger går alltid til slutten,
        # uavhengig av hvor lesingen har flyttet posisjonen.
        fd = os.open(loggfil, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            if _mangler_avsluttende_linjeskift(fd):
                # Forrige skriving stoppet midtveis. Reparasjonen skjer HER,
                # i den neste skrivingen som faktisk lykkes — ikke i
                # feilhåndteringen til den som feilet. Det var mitt første
                # forsøk, og testen viste hvorfor det ikke holder: når
                # skrivingen feiler fordi disken er full, feiler også det
                # ekstra linjeskiftet, og den halve posten slukte den neste.
                linje = b"\n" + linje
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
    """ENESTE lovlige inngang for skrivehandlinger — **KUN UTVIKLING**.

    ADR-001: på staging og i produksjon er inngangen
    `platform.core.db.pg.sikker_beslutning_pg`, som i tillegg gir
    reservasjon og loggpost i samme transaksjon og håndhever
    attestasjonssignaturer. Denne varianten beholdes for lokal kjøring.

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
