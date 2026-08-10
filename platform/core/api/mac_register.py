"""MAC-nøkkelregister for menneskelige godkjenningskonvolutter (PR-012).

Mennesket avgir en MAC-signert konvolutt (`disponit_human_approval_v1`) som
mates som en NY beslutning gjennom motoren — aldri en statusflipp. MAC-en
(HMAC-SHA-256 over RFC 8785-kanonisk konvolutt) beviser at konvolutten ble
utstedt server-side og ikke er endret. Nøklene bor i APP-STATE (systemd
credential), ALDRI i DB — samme prinsipp som attestasjonsnøklene i
`policy_validator.attestering`.

Register: `{key_id: {"rolle": signerer|verifiserer|pensjonert,
                     "hemmelighet": <streng >= 32 tegn>}}`.
- **Nøyaktig ÉN nøkkel i `signerer`** — håndheves ved last (flere/ingen →
  kast). Rotasjon uten nedetid: ny signerer inn, gammel til `verifiserer`.
- `verifiserer`: gamle nøkler som fortsatt VERIFISERER ubrukte/auditerbare
  konvolutter.
- `pensjonert` eller UKJENT nøkkel ved verifikasjon → **fail-closed** (aldri
  gyldig).
- **Oppstartsperre:** manglende/ugyldig register → prosessen nekter start
  (kalleren i `Tjeneste.__init__`, som `last_nokler`).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from pathlib import Path

from policy_validator import jcs

_ALG = "HMAC-SHA256"
# v2 bandt inn `bundet_grunnkode` (v8 §2) — konvolutten binder nøyaktig ÉN
# (grunnkode, target_action). `kanonisk_konvolutt` er feltagnostisk, så den
# nye nøkkelen inngår i de signerte bytene uten videre.
KONVOLUTTVERSJON = 2                       # disponit_human_approval_v2
_ROLLER = ("signerer", "verifiserer", "pensjonert")
_MAC_FELT = ("mac", "mac_key_id")


def kanonisk_konvolutt(konvolutt: dict) -> bytes:
    """RFC 8785-kanoniske bytes av konvolutten UTEN selve mac-feltene — samme
    kanonisering (JCS) som resten av systemet. `mac`/`mac_key_id` ligger
    utenfor de signerte bytene (de ER signaturen)."""
    uten = {k: v for k, v in konvolutt.items() if k not in _MAC_FELT}
    return jcs.kanoniske_bytes(uten)


class MacRegister:
    """Lastet register + gjeldende signerer-nøkkel. Konstant-tids verifikasjon."""

    def __init__(self, nokler: dict) -> None:
        self._n = nokler
        self.signer_key_id = next(k for k, v in nokler.items()
                                  if v["rolle"] == "signerer")

    def signer(self, konvolutt: dict) -> tuple[str, str]:
        """-> (mac_key_id, mac_hex) over kanonisk konvolutt med GJELDENDE
        signerer-nøkkel. Signering med en `verifiserer`/`pensjonert`-nøkkel
        er umulig — kun én nøkkel har rollen `signerer`."""
        hemmelighet = self._n[self.signer_key_id]["hemmelighet"]
        mac = hmac.new(hemmelighet.encode("utf-8"),
                       kanonisk_konvolutt(konvolutt), hashlib.sha256).hexdigest()
        return self.signer_key_id, mac

    def verifiser(self, konvolutt: dict, mac: str, mac_key_id: str) -> bool:
        """True hvis og bare hvis `mac` er en gyldig HMAC for konvolutten under
        nøkkelen `mac_key_id`. Ukjent eller `pensjonert` nøkkel → False
        (fail-closed). Konstant-tid. Kaster aldri."""
        try:
            e = self._n.get(mac_key_id)
            if not e or e["rolle"] == "pensjonert":
                return False
            forventet = hmac.new(e["hemmelighet"].encode("utf-8"),
                                 kanonisk_konvolutt(konvolutt),
                                 hashlib.sha256).hexdigest()
            return isinstance(mac, str) and hmac.compare_digest(forventet, mac)
        except Exception:
            return False                   # fail-closed


def _valider_register(reg: object) -> dict:
    if not isinstance(reg, dict) or not reg:
        raise ValueError("MAC-registeret må være et ikke-tomt objekt")
    signerere = 0
    for kid, e in reg.items():
        if not isinstance(e, dict):
            raise ValueError(
                f"nøkkel '{kid}' må være objekt {{rolle, hemmelighet}}")
        rolle = e.get("rolle")
        hemmelighet = e.get("hemmelighet")
        if rolle not in _ROLLER:
            raise ValueError(f"nøkkel '{kid}': ukjent rolle {rolle!r}")
        if not isinstance(hemmelighet, str) or len(hemmelighet) < 32:
            raise ValueError(
                f"nøkkel '{kid}': hemmelighet må være streng på minst 32 tegn")
        if rolle == "signerer":
            signerere += 1
    if signerere != 1:
        raise ValueError(
            f"registeret må ha NØYAKTIG én 'signerer' (fant {signerere})")
    return reg


def last_mac_register(kilde: str | None = None) -> MacRegister:
    """Laster MAC-registeret (oppstartsperre). Rekkefølge som `last_nokler`:
    filsti-argument → `DISPONIT_MAC_NOKLER` (JSON) → `DISPONIT_MAC_NOKLER_FIL`
    (sti, 0600/0400-krav). Ugyldig register → kast, og prosessen nekter start.
    """
    if kilde is None and (raa := os.environ.get("DISPONIT_MAC_NOKLER")):
        return MacRegister(_valider_register(json.loads(raa)))
    sti = Path(kilde or os.environ.get("DISPONIT_MAC_NOKLER_FIL", ""))
    if not str(sti) or not sti.is_file():
        raise FileNotFoundError(
            "MAC-register ikke funnet: sett DISPONIT_MAC_NOKLER eller "
            "DISPONIT_MAC_NOKLER_FIL")
    modus = stat.S_IMODE(sti.stat().st_mode)
    if modus & 0o077:
        raise PermissionError(
            f"MAC-nøkkelfilen {sti} har rettigheter {oct(modus)} — krever 0600")
    return MacRegister(
        _valider_register(json.loads(sti.read_text(encoding="utf-8"))))
