"""Rapportskjemaet for `kontroll.wcag.rapport` (PR-014c §7) — lukket,
versjonert, med PÅKREVDE ærlighetsfelter.

Skjemaet er innholdsadressert: `skjema_hash()` er sha256 over JCS-kanonisk
form, registrert i `artefaktskjema` ved deploy (aldri i migrasjonen) og
bundet av artefakttypen. Rapporten sier aldri «oppfyller WCAG» — den sier
hvilket regelsett som kjørte, mot hvilke sider, med hvilke versjoner, hva
som ble funnet, og hva som IKKE ble vurdert:

  * `manuelle_kriterier_vurdert` er alltid false i v1 — feltet finnes for
    at rapporten skal si det selv, ikke bare dokumentasjonen;
  * `dekningsbegrensninger` er påkrevd (014b B3): blokkerte eksterne
    subressurser med antall og vert, uten path/query. Tom liste betyr
    «ingen kjente begrensninger», ikke «ikke sjekket»;
  * `avkortet` bæres fra proxyens taktelling (014b §5);
  * `miljo`-digestene kommer fra SERVERKONTEKSTEN (controllerens config),
    aldri fra motorens utdata — samme disiplin som `artifact_digest`.

Grensene (maks 500 funn, 10 eksempler per regel, selektor ≤ 200 tegn,
1 MiB total) håndheves av skjemaet OG av `rapport.bygg()` som sanitering —
skjemaet avviser, byggingen kutter ærlig (og sier fra i `avkortet`).
"""
from __future__ import annotations

import hashlib

VERSJON = 1

_HOSTNAME = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"

SKJEMA: dict = {
    "$id": f"disponit:kontroll.wcag.rapport:v{VERSJON}",
    "type": "object",
    "additionalProperties": False,
    "required": ["kravsett", "regelsett_versjon", "kjort_ts", "varighet_ms",
                 "sider_kontrollert", "funn", "sammendrag", "avkortet",
                 "dekningsbegrensninger", "miljo",
                 "manuelle_kriterier_vurdert"],
    "properties": {
        "kravsett": {"enum": ["wcag21_aa"]},
        "regelsett_versjon": {"type": "string", "minLength": 1,
                              "maxLength": 64},
        "kjort_ts": {"type": "string", "format": "date-time"},
        "varighet_ms": {"type": "integer", "minimum": 0},
        "sider_kontrollert": {
            "type": "array", "minItems": 1, "maxItems": 50,
            "items": {"type": "object", "additionalProperties": False,
                      "required": ["url", "status"],
                      "properties": {
                          # URL uten query og fragment — håndhevet av
                          # saneringen og av mønsteret her.
                          "url": {"type": "string", "maxLength": 2000,
                                  "pattern": r"^https://[^?#]+$"},
                          "status": {"enum": ["ok", "feilet"]}}}},
        "funn": {
            "type": "array", "maxItems": 500,
            "items": {"type": "object", "additionalProperties": False,
                      "required": ["regel_id", "alvorlighet", "antall",
                                   "eksempler"],
                      "properties": {
                          "regel_id": {"type": "string", "minLength": 1,
                                       "maxLength": 128},
                          "alvorlighet": {"enum": ["kritisk", "alvorlig",
                                                   "moderat", "lav"]},
                          "antall": {"type": "integer", "minimum": 1},
                          "eksempler": {
                              "type": "array", "maxItems": 10,
                              "items": {"type": "string",
                                        "maxLength": 200}}}}},
        "sammendrag": {
            "type": "object", "additionalProperties": False,
            "required": ["kritisk", "alvorlig", "moderat", "lav"],
            "properties": {k: {"type": "integer", "minimum": 0}
                           for k in ("kritisk", "alvorlig", "moderat",
                                     "lav")}},
        "avkortet": {
            "type": "object", "additionalProperties": False,
            "required": ["truffet", "tak", "verdi"],
            "properties": {
                "truffet": {"type": "boolean"},
                "tak": {"type": ["integer", "null"], "minimum": 0},
                "verdi": {"type": ["integer", "null"], "minimum": 0}}},
        "dekningsbegrensninger": {
            "type": "array", "maxItems": 200,
            "items": {"type": "object", "additionalProperties": False,
                      "required": ["vert", "antall", "art"],
                      "properties": {
                          # Vert, aldri path/query (014b B3).
                          "vert": {"type": "string", "maxLength": 253,
                                   "pattern": _HOSTNAME},
                          "antall": {"type": "integer", "minimum": 1},
                          "art": {"enum": ["stilark", "font", "skript",
                                           "bilde", "annet"]}}}},
        "miljo": {
            "type": "object", "additionalProperties": False,
            "required": ["axe_versjon", "chromium_versjon",
                         "container_image_digest", "viewport", "locale",
                         "timezone"],
            "properties": {
                "axe_versjon": {"type": "string", "minLength": 1,
                                "maxLength": 64},
                "chromium_versjon": {"type": "string", "minLength": 1,
                                     "maxLength": 64},
                "container_image_digest": {
                    "type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"},
                "viewport": {"type": "string", "maxLength": 32},
                "locale": {"type": "string", "maxLength": 16},
                "timezone": {"type": "string", "maxLength": 64}}},
        # Alltid false i v1 — rapporten sier selv hva den ikke vurderte.
        "manuelle_kriterier_vurdert": {"const": False},
    },
}


def kanonisk() -> bytes:
    from policy_validator import jcs
    return jcs.kanoniske_bytes(SKJEMA)


def skjema_hash() -> str:
    return hashlib.sha256(kanonisk()).hexdigest()
