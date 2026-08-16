"""Semantikkmanifest + versjonsbinding (PR-013 CP4, vilkår V8/V9, port 3/13).

Samme policyendring kan bety noe ANNET hvis motorens tolkning endres — selv med
urørt klassifikatorkode. Derfor bindes tre versjoner (skjema, klassifikator,
motorsemantikk), og:

- `MOTOR_SEMANTIKKVERSJON` er en PINNET checksum over de filene som bærer
  policysemantikk (+ `DENY_ALL_V1`). CI-porten
  `test_semantikk_checksum_pinnet` feiler hvis en manifestfil endres uten at
  pinnen (og dermed versjonen) oppdateres. Manifestet er selv reviewet: å fjerne
  en fil fra det er en synlig handling i diffen.
- `KLASSIFIKATORVERSJON` UTLEDES av motorversjonen + klassifikatorkilden, så en
  motorendring som påvirker policysemantikk ALLTID bumper klassifikatorversjonen
  (v3 §4) — uten at man kan glemme det.
- `DENY_ALL_V1` er den effektive motorpolicyen for en tenant uten aktiv policy
  (V9). Å endre konstanten er en motorsemantikkendring (den er i checksummen).
- **tzdata/timezone-versjonen** verifiseres ved oppstart mot det releasen ble
  bygget med — en CI-port beskytter ikke produksjon om verten kjører annen
  tzdata (V8, port 13). Se `verifiser_oppstartsmiljo`.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

_ROT = Path(__file__).resolve().parents[3]          # repo-rot


class SemantikkAvvik(RuntimeError):
    """Kjøremiljøet avviker fra det releasen ble bygget med — fail-closed."""


# ---------------------------------------------------------------------------
# DENY_ALL_V1 — effektiv motorpolicy uten aktiv policy (V9)
# ---------------------------------------------------------------------------
#: Ingen handlinger, ingen roller med fullmakt, ingen unntakskategorier. Enhver
#: beslutning treffer «ukjent handling» → STOPP. Plattformkonstant, ikke
#: kundedata; kan aldri redigeres eller aktiveres som kundepolicy.
DENY_ALL_V1: dict = {
    "schema_version": "0.2",
    "meta": {"policy_id": "DENY_ALL_V1", "versjon": "1",
             "bransjemal": "plattform", "status": "produksjon"},
    "tidssone": "UTC",
    "roller": [],
    "dataklasser": [],
    "verifikatorer": {},
    "handlinger": [],
    "unntak": {"kategorier": [], "maks_auto_forsok": 0, "eskalering": "unntakskø"},
}
DENY_ALL_VERSJON = "1"


def deny_all_hash() -> str:
    return hashlib.sha256(json.dumps(
        DENY_ALL_V1, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()


DENY_ALL_HASH = deny_all_hash()


# ---------------------------------------------------------------------------
# Semantikkmanifest — reviewet liste over filene som bærer policysemantikk
# ---------------------------------------------------------------------------
#: Stier RELATIVT til repo-rot. Endres innholdet i noen av dem (eller listen
#: selv), endres `kildechecksum()`. `klassifikator.py` er BEVISST utelatt her —
#: klassifikatorens egen versjon utledes av denne + klassifikatorkilden, så
#: motorendringer tvinger klassifikatorbump, ikke omvendt.
SEMANTIKK_MANIFEST: tuple[str, ...] = (
    "platform/core/policy_validator/engine.py",
    "platform/core/policy_validator/schema.py",
    "platform/core/policy_validator/tidsvindu.py",
    "policies/policy-schema-v0.2.json",
)


def _fil_sha(rel: str) -> str:
    return hashlib.sha256((_ROT / rel).read_bytes()).hexdigest()


def kildechecksum() -> str:
    """SHA-256 over: den kanoniske manifestlista (inkl. filstier, så fjerning av
    en oppføring endrer summen) · innholdet i hver listede fil · `DENY_ALL_V1`.
    Deterministisk gitt kildekoden — samme på enhver maskin (miljøavhengige
    deler, som tzdata, håndteres separat ved oppstart)."""
    biter = [f"{rel}={_fil_sha(rel)}" for rel in SEMANTIKK_MANIFEST]
    biter.append(f"DENY_ALL_V1={DENY_ALL_HASH}")
    return hashlib.sha256("\n".join(biter).encode("utf-8")).hexdigest()


#: PINNET motorsemantikkversjon. MÅ være lik `kildechecksum()`; CI-porten
#: håndhever det. Oppdateres BEVISST når en manifestfil endres (= versjonsbump).
#: (CP3: nattvindu-støtte i tidsvindu/engine er innbakt i denne.)
#: (Codex P2 på #61: `propertyNames` på `verifikatorer`/`verifikator_prioritet`
#: — verifikator-id-en kan ikke lenger inneholde skilletegnene i diffstien.)
MOTOR_SEMANTIKKVERSJON = "3fc010fbabdf26ddebead7f6eb8bd4f230b6d972fbe82be08af5d633a92c6c5a"


# ---------------------------------------------------------------------------
# Miljøsignatur — tzdata + semantiske biblioteker (verifiseres ved oppstart)
# ---------------------------------------------------------------------------
def tzdata_versjon() -> str:
    """Versjonen av tidssonedatabasen som styrer DST-semantikken. `tzdata`-pakken
    om installert (deterministisk), ellers systemets `/usr/share/zoneinfo`
    tzdata.zi-versjon, ellers 'ukjent'."""
    try:
        from importlib.metadata import version
        return f"tzdata-pip:{version('tzdata')}"
    except Exception:
        pass
    # Ingen pip-tzdata: bind til systemets tz-database. `+VERSION`/`tzdata.zi`
    # bærer utgivelsen (f.eks. «2024a»); finnes ingen, hash hele `tzdata.zi` så
    # enhver endring i DST-reglene endrer signaturen.
    try:
        vfil = Path("/usr/share/zoneinfo/+VERSION")
        if vfil.is_file():
            return f"system:{vfil.read_text(encoding='utf-8').strip()}"
    except Exception:
        pass
    try:
        zi = Path("/usr/share/zoneinfo/tzdata.zi").read_bytes()
        return "system-sha:" + hashlib.sha256(zi).hexdigest()[:16]
    except Exception:
        return "ukjent"


def miljosignatur() -> str:
    """SHA over de miljøavhengige, semantikk-styrende delene: tzdata-versjonen.
    (Lockfil-hasher for semantiske avhengigheter kan legges til her når et
    lockfil-regime finnes; i dag er zoneinfo/stdlib den eneste DST-kilden.)"""
    return hashlib.sha256(f"tzdata={tzdata_versjon()}".encode("utf-8")).hexdigest()


def verifiser_oppstartsmiljo() -> None:
    """Fail-closed oppstartssjekk (V8, port 13). Kalles ved app-boot.

    1. Kildechecksum MÅ stemme med den pinnede motorversjonen — ellers kjører
       prosessen kode som er endret uten at semantikkversjonen ble bumpet.
    2. Avviker vertens `miljosignatur()` fra det releasen ble bygget med
       (`DISPONIT_SEMANTIKK_MILJO`), STOPPER oppstarten — verten kan tolke
       tidsvinduer annerledes enn klassifikatoren beviste.

    Utenfor en release (env uten `DISPONIT_SEMANTIKK_MILJO`) hoppes miljøsjekken
    — dev/CI kjører på egen tzdata; kildesjekken gjelder alltid."""
    faktisk = kildechecksum()
    if faktisk != MOTOR_SEMANTIKKVERSJON:
        raise SemantikkAvvik(
            "kildechecksum != MOTOR_SEMANTIKKVERSJON — en semantikkfil er "
            "endret uten at versjonen ble bumpet (oppdater semantikk.py)")
    forventet_miljo = os.environ.get("DISPONIT_SEMANTIKK_MILJO")
    if forventet_miljo and forventet_miljo != miljosignatur():
        raise SemantikkAvvik(
            f"miljøavvik: verten har {miljosignatur()} men releasen ble bygget "
            f"med {forventet_miljo} — tzdata/biblioteker divergerer, STOPP")
