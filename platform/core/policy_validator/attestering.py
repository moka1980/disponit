"""Kryptografisk attestasjonsverifikasjon (PR-004, ADR-001 krav 3).

Allowlisten i policyen hindrer UKJENTE verifikatorer. Den hindrer ikke en
kompromittert innsender i å DIKTE en attestasjon i en kjent verifikators
navn. Denne modulen tetter det: hver attestasjon bærer HMAC-SHA256 over
kanonisk innhold, med hemmelig nøkkel per verifikator. Uten gyldig
signatur er attestasjonen verdiløs.

Nøkkelregister: {verifikator_id: {nokkel_id: hemmelighet}}. Flere nøkler
per verifikator muliggjør rotasjon uten nedetid: ny nøkkel legges til,
verifikatoren bytter, gammel fjernes. Lastes fra miljøvariabelen
DISPONIT_ATT_NOKLER (JSON) eller fil med 0600-rettigheter — aldri fra
repo, aldri fra hendelsen.

Verifisering skjer i inngangsporten (sikker_beslutning_pg), FØR motoren
evaluerer — motoren selv er uendret.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from pathlib import Path

from . import jcs
from .engine import Grunn

_SIGNATURFELT = "signatur"
_ALG = "HMAC-SHA256"

#: Feltet som sier hvilken kanonisering signaturen ble laget over.
#: LUKKET format fra PR-006: manglende eller ukjent verdi avvises på
#: nettverksveien. Uten feltet måtte verifikatoren gjette hvilke bytes
#: avsenderen signerte, og «prøv begge» er ikke en kontroll — det er to
#: sjanser til å slippe gjennom.
KANONISERINGSFELT = "kanonisering"


def kanonisk_bytes(att: dict) -> bytes:
    """RFC 8785-kanoniske bytes av attestasjonen UTEN signaturfeltet.

    PR-006 §4: byttet fra `json.dumps(..., default=str)` til ekte JCS.
    Forskjellen er ikke kosmetisk — se `jcs`-modulen for de tre stedene de
    to avviker (nøkkelsortering på UTF-16, ES6-talformat, og at
    `default=str` gjorde en ikke-JSON-type til en streng i stillhet i
    stedet for å være en feil).

    Kaster `jcs.Ikkekanoniserbar` på verdier JSON ikke kan uttrykke. Det er
    den tiltenkte oppførselen: en signatur over en gjetning er verdiløs.
    """
    uten = {k: v for k, v in att.items() if k != _SIGNATURFELT}
    return jcs.kanoniske_bytes(uten)


def signer(att: dict, nokkel_id: str, hemmelighet: str) -> dict:
    """Returnerer kopi av attestasjonen med signatur. Brukes av
    verifikator-modulene (M-14, M-42, …) når de utsteder attestasjoner.

    `kanonisering` stemples PÅ attestasjonen FØR signeringen, slik at
    feltet ligger INNE i de signerte bytene. Sto det utenfor, kunne
    hvem som helst endre det uten at signaturen røk — og et
    format-felt man kan endre fritt er ingen formatkontroll.
    """
    ut = dict(att)
    ut[KANONISERINGSFELT] = jcs.KANONISERING
    mac = hmac.new(hemmelighet.encode("utf-8"), kanonisk_bytes(ut),
                   hashlib.sha256).hexdigest()
    ut[_SIGNATURFELT] = {"alg": _ALG, "nokkel_id": nokkel_id, "verdi": mac}
    return ut


def verifiser(att: dict, nokler: dict) -> bool:
    """True hvis og bare hvis signaturen er gyldig for attestasjonens
    verifikator. Konstant-tids sammenligning. Kaster aldri."""
    try:
        sig = att.get(_SIGNATURFELT)
        if not isinstance(sig, dict) or sig.get("alg") != _ALG:
            return False
        # Lukket format. En ukjent kanonisering er ikke «prøv standarden» —
        # den er et avslag. Feltet ligger inne i de signerte bytene, så en
        # angriper kan ikke bytte det uten å ødelegge signaturen; denne
        # kontrollen stopper den som signerer med en ANNEN kanonisering og
        # håper vi tolker bytene likt.
        if att.get(KANONISERINGSFELT) != jcs.KANONISERING:
            return False
        verifikator = att.get("verifikator")
        hemmelighet = (nokler.get(verifikator) or {}).get(sig.get("nokkel_id"))
        if not isinstance(hemmelighet, str) or not hemmelighet:
            return False
        forventet = hmac.new(hemmelighet.encode("utf-8"), kanonisk_bytes(att),
                             hashlib.sha256).hexdigest()
        verdi = sig.get("verdi")
        return isinstance(verdi, str) and hmac.compare_digest(forventet, verdi)
    except Exception:
        return False  # fail-closed


def kontroller_hendelse(event: dict, nokler: dict) -> Grunn | None:
    """Porten sikker_beslutning_pg kaller. None == alle attestasjoner har
    gyldig signatur. Ellers: første brudd som Grunn (=> STOPP).
    En hendelse uten attestasjoner passerer porten (motoren håndhever
    selv at påkrevde vilkår har attestasjon)."""
    attester = event.get("attestasjoner")
    if attester is None:
        return None
    if not isinstance(attester, dict):
        return Grunn("attestasjoner_feilformet")
    for navn, att in attester.items():
        if not isinstance(att, dict):
            return Grunn("attestasjon_feilformet", {"vilkaar": str(navn)})
        if _SIGNATURFELT not in att:
            return Grunn("attestasjon_uten_signatur", {"vilkaar": str(navn)})
        if att.get(KANONISERINGSFELT) != jcs.KANONISERING:
            # Egen grunnkode, ikke bare «signatur ugyldig». Forskjellen er
            # diagnostisk viktig: en verifikator som fortsatt bruker gammelt
            # format skal kunne se AT det er formatet, ikke lete etter en
            # nøkkelfeil som ikke finnes.
            return Grunn("attestasjon_kanonisering_ukjent",
                         {"vilkaar": str(navn),
                          "oppgitt": str(att.get(KANONISERINGSFELT)),
                          "kreves": jcs.KANONISERING})
        if not verifiser(att, nokler):
            return Grunn("attestasjon_signatur_ugyldig", {"vilkaar": str(navn)})
    return None


def last_nokler(kilde: str | None = None) -> dict:
    """Laster nøkkelregisteret.

    Rekkefølge: eksplisitt filsti-argument -> miljøvariabelen
    DISPONIT_ATT_NOKLER (JSON-innhold) -> miljøvariabelen
    DISPONIT_ATT_NOKLER_FIL (sti). Filer må ha 0600/0400 — for åpne
    rettigheter er en feil, ikke en advarsel (fail-closed).
    """
    if kilde is None and (raa := os.environ.get("DISPONIT_ATT_NOKLER")):
        return _valider_register(json.loads(raa))
    sti = Path(kilde or os.environ.get("DISPONIT_ATT_NOKLER_FIL", ""))
    if not sti or not sti.is_file():
        raise FileNotFoundError(
            "nøkkelregister ikke funnet: sett DISPONIT_ATT_NOKLER eller "
            "DISPONIT_ATT_NOKLER_FIL")
    modus = stat.S_IMODE(sti.stat().st_mode)
    if modus & 0o077:
        raise PermissionError(
            f"nøkkelfilen {sti} har rettigheter {oct(modus)} — krever 0600")
    return _valider_register(json.loads(sti.read_text(encoding="utf-8")))


def _valider_register(reg: object) -> dict:
    if not isinstance(reg, dict) or not reg:
        raise ValueError("nøkkelregisteret må være et ikke-tomt objekt")
    for vid, nokler in reg.items():
        if not isinstance(nokler, dict) or not nokler:
            raise ValueError(f"verifikator '{vid}' mangler nøkler")
        for nid, hemmelighet in nokler.items():
            if not isinstance(hemmelighet, str) or len(hemmelighet) < 32:
                raise ValueError(
                    f"nøkkel '{vid}/{nid}' må være streng på minst 32 tegn")
    return reg


# ---------------------------------------------------------------------------
# v2: bindingsfelter (PR-005). En gyldig signatur beviser at attestasjonen
# er UTSTEDT av en betrodd verifikator — den beviser ikke at den gjelder
# DENNE forespørselen. Uten binding kan en ekte, signert attestasjon
# gjenbrukes på en annen tenant, en annen handling, en annen ressurs eller
# på et senere tidspunkt. Bindingsfeltene ligger inne i de signerte bytene,
# så de kan ikke endres uten at signaturen ryker.
# ---------------------------------------------------------------------------

BINDINGSFELT = ("tenant_id", "handling", "vilkaar", "ressurs_id",
                "policy_id", "utstedt", "utloper", "jti")


def tid_med_sone(verdi: object):
    """ISO 8601 MED tidssone, ellers None. Naive tidsstempler avvises —
    samme regel som motoren (PR-002-funn 3a).

    Offentlig fordi API-veien (api.kjerne) trenger nøyaktig samme tolkning
    når den regner ut hvor lenge en konsumert jti må stå i replay-tabellen.
    En egen parser der ville vært en andre kopi av en tidsregel som
    allerede har kostet oss ett funn.
    """
    from datetime import datetime
    if not isinstance(verdi, str):
        return None
    try:
        t = datetime.fromisoformat(verdi)
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


_tid = tid_med_sone       # internt navn, brukt av kontroller_binding


def kontroller_binding(event: dict, context, handling: str, policy_id: str,
                       naa) -> Grunn | None:
    """None == alle attestasjoner er bundet til DENNE forespørselen.

    Kalles av API-veien ETTER signaturkontrollen. Rekkefølgen er ikke
    tilfeldig: er signaturen ugyldig, er feltene ikke til å stole på, og da
    er det meningsløst å sammenligne dem.

    En attestasjon i PR-004-format mangler bindingsfeltene og avvises her —
    det er den tiltenkte måten å nekte gammelt format på nettverksveien.
    """
    attester = event.get("attestasjoner")
    if attester is None:
        return None
    if not isinstance(attester, dict):
        return Grunn("attestasjoner_feilformet")

    for navn, att in attester.items():
        if not isinstance(att, dict):
            return Grunn("attestasjon_feilformet", {"vilkaar": str(navn)})

        mangler = [f for f in BINDINGSFELT if f not in att]
        if mangler:
            return Grunn("attestasjon_mangler_binding",
                         {"vilkaar": str(navn), "felt": mangler})

        forventet = (
            ("tenant_id", getattr(context, "tenant_id", None),
             "attestasjon_feil_tenant"),
            ("handling", handling, "attestasjon_feil_handling"),
            ("vilkaar", navn, "attestasjon_feil_vilkaar"),
            ("policy_id", policy_id, "attestasjon_feil_policy"),
            ("ressurs_id", event.get("ressurs_id"),
             "attestasjon_feil_ressurs"),
        )
        for felt, fasit, kode in forventet:
            if att.get(felt) != fasit:
                return Grunn(kode, {"vilkaar": str(navn),
                                    "i_attestasjon": str(att.get(felt)),
                                    "i_forespoersel": str(fasit)})

        utstedt, utloper = _tid(att.get("utstedt")), _tid(att.get("utloper"))
        if utstedt is None or utloper is None:
            return Grunn("attestasjon_tid_ugyldig", {"vilkaar": str(navn)})
        if utloper <= utstedt:
            return Grunn("attestasjon_tid_ugyldig", {"vilkaar": str(navn)})
        if not (utstedt <= naa < utloper):
            return Grunn("attestasjon_utenfor_gyldighet",
                         {"vilkaar": str(navn)})

        # Minstelengden er 22 fordi tabellen attestasjon_jti har
        # CHECK (length(jti) >= 22). Var koden mildere enn databasen, ville
        # en for kort jti passert porten og feilet nede i INSERT-en i stedet
        # — sen, stygg feil i stedet for ren STOPP med begrunnelse.
        jti = att.get("jti")
        if not isinstance(jti, str) or len(jti.strip()) < 22:
            return Grunn("attestasjon_jti_ugyldig", {"vilkaar": str(navn)})

    return None


def jti_liste(event: dict) -> list[tuple[str, str]]:
    """(vilkaar, jti) for hver attestasjon — det API-veien konsumerer mot
    tabellen `attestasjon_jti` for å hindre replay av irreversible
    handlinger. Rekkefølgen er stabil (sortert) så to samtidige
    forespørsler tar låsene i samme rekkefølge og ikke kan vranglåse."""
    attester = event.get("attestasjoner") or {}
    if not isinstance(attester, dict):
        return []
    ut = [(str(n), a.get("jti")) for n, a in attester.items()
          if isinstance(a, dict) and isinstance(a.get("jti"), str)]
    return sorted(ut, key=lambda p: (p[1], p[0]))
