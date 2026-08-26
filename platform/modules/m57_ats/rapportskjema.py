"""Rapportskjemaet for `rekruttering.evaluering.rapport` — LUKKET form.

Skjemaet er kontraktens fjerde kanon (m56-formen): modulen validerer sin
egen rapport FØR innsending, og plattformen validerer den samme formen
mot `artefakttype_register`s innholdsadresserte hash. Alt er
`additionalProperties: false` — en rapport vi ikke forstår fullt ut er
ingen rapport.

AVMASKERINGEN ER IKKE HER, med vilje: rapporten er det promoterte,
lesbare artefaktet, og avmaskeringstabellen er 057s egen payload
(`kandidat_avmaskering`) med egen reaping — de to skal aldri reise
sammen. `kildetekst` er den BLINDEDE teksten funnenes [start:slutt]
faktisk indekserer; personverdiene finnes ikke i den.
"""
from __future__ import annotations

_FUNN = {
    "type": "object", "additionalProperties": False,
    "required": ["kategori", "kilde"],
    "properties": {
        "kategori": {"type": "string", "minLength": 1, "maxLength": 64},
        "kilde": {
            "type": "object", "additionalProperties": False,
            "required": ["start", "slutt", "sitat"],
            "properties": {
                "start": {"type": "integer", "minimum": 0},
                "slutt": {"type": "integer", "minimum": 1},
                "sitat": {"type": "string", "minLength": 1},
            },
        },
    },
}

_KANDIDAT = {
    "type": "object", "additionalProperties": False,
    "required": ["funn", "intervjusporsmal", "kildetekst"],
    "properties": {
        "funn": {"type": "array", "items": _FUNN, "maxItems": 100},
        "intervjusporsmal": {
            "type": "array", "maxItems": 20,
            "items": {"type": "string", "minLength": 1,
                      "maxLength": 500},
        },
        "kildetekst": {"type": "string"},
    },
}

SKJEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object", "additionalProperties": False,
    "required": ["rapporttype", "versjon", "profil", "antall_soknader",
                 "rangering", "kandidater", "fremdrift"],
    "properties": {
        "rapporttype": {"const": "rekruttering.evaluering.rapport"},
        "versjon": {"const": 1},
        "profil": {
            "type": "object", "additionalProperties": False,
            "required": ["profil_id", "versjon", "navn"],
            "properties": {
                "profil_id": {"type": "string", "minLength": 1},
                "versjon": {"type": "integer", "minimum": 1},
                "navn": {"type": "string", "minLength": 1},
            },
        },
        "antall_soknader": {"type": "integer", "minimum": 1,
                            "maximum": 5000},
        "rangering": {
            "type": "array", "minItems": 1, "maxItems": 5000,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["kandidat_id", "poeng", "nedbrytning"],
                "properties": {
                    "kandidat_id": {"type": "string", "minLength": 1,
                                    "maxLength": 64},
                    "poeng": {"type": "integer", "minimum": 0},
                    "nedbrytning": {
                        "type": "object",
                        "additionalProperties": {"type": "integer",
                                                 "minimum": 0},
                    },
                },
            },
        },
        "kandidater": {
            "type": "object",
            "additionalProperties": _KANDIDAT,
        },
        "fremdrift": {
            "type": "object", "additionalProperties": False,
            "required": ["filer_lest", "filer_totalt", "byte_lest"],
            "properties": {
                "filer_lest": {"type": "integer", "minimum": 0},
                "filer_totalt": {"type": "integer", "minimum": 0},
                "byte_lest": {"type": "integer", "minimum": 0},
            },
        },
    },
}


def bygg(resultat: dict, *, profil: dict, antall_soknader: int) -> dict:
    """`kjor_bunt`-utfallet → rapporten. Avmaskeringen STRIPPES her —
    dette er den ene grensen den aldri skal krysse."""
    kandidater = {
        kid: {"funn": r["funn"],
              "intervjusporsmal": r["intervjusporsmal"],
              "kildetekst": r["kildetekst"]}
        for kid, r in resultat["artefakter"].items()}
    return {"rapporttype": "rekruttering.evaluering.rapport",
            "versjon": 1,
            "profil": {"profil_id": profil["profil_id"],
                       "versjon": profil["versjon"],
                       "navn": profil["navn"]},
            "antall_soknader": antall_soknader,
            "rangering": resultat["rangering"],
            "kandidater": kandidater,
            "fremdrift": resultat["fremdrift"]}
