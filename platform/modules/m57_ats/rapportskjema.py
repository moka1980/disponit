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

#: Funnenes tak, navngitt her fordi det er DENNE grensen skriveveiens
#: budsjett er utledet av (`app.MAKS_KANDIDATARTEFAKT_KROPP`).
FUNN_MAKS = 100
#: SITATENE ER IKKE DISJUNKTE (Codex P2, #173). `sitat` sto uten
#: `maxLength`, og hvert funn kan uavhengig sitere HVILKEN SOM HELST del
#: av `kildetekst` — hundre funn kan altså sitere hele teksten hundre
#: ganger. Skriveveiens budsjett var derimot dimensjonert som «teksten én
#: gang + sitatene én gang», altså på en antakelse om at utsnittene til
#: sammen ikke overstiger kilden. En skjemagyldig 20 MiB-kandidat med to
#: fulltekstsitater sprengte da dørens 50 MiB, fikk `request_feilformet`,
#: og `lagre_kandidat` felte HELE den ellers gyldige evalueringen.
#:
#: Kontrakten bærer nå grensen selv, som Codex' første utvei: sitatene er
#: EVIDENS for ett funn, ikke en kopi av dokumentet, og 4096 tegn er et
#: par siders utdrag. Det samlede sitatbudsjettet er dermed bundet —
#: `FUNN_MAKS * SITAT_MAKS` tegn, verste fall 4 byte per tegn i UTF-8 =
#: 1,6 MiB — og døren kan regne på et tall i stedet for på en antakelse.
#: Håndheves ved GRENSEN (`modell.Ollamamodell.vurder` dropper et for
#: langt sitat, som den dropper et som ikke står ordrett i teksten), så
#: en ordrik modell feller ikke en gyldig evaluering på skjemaporten.
#:
#: OG DEN STÅR IKKE I `SKJEMA` (Codex P1, #173). Første form la
#: `"maxLength": SITAT_MAKS` på `sitat`-noden. Det endret `SKJEMA` som
#: DOKUMENT, altså `registrer(...)`-hashen, mens artefakttypen
#: `rekruttering.evaluering.rapport` og `versjon` sto stille — og
#: `registrer_artefakttype` (036) sammenligner HELE den immutable
#: tuppelen `(eiermodul, kontraktversjon, kontrakt_hash, skjema_hash)`.
#: I ethvert miljø der typen alt er registrert ville
#: `registrer-m57-ats.py` da dødd på `unique_violation` og rullet HELE
#: release-registreringen tilbake. En ny identitet er heller ikke
#: utveien uten videre: navneformen er prefikslukket, så
#: `…rapport.v2` avvises som overlapp med `…rapport`, og et nytt navn
#: må følges av lese-API-ets par (`lesing.py`) — egen maskin, eget
#: issue.
#:
#: Grensen er derfor der den ALLTID ble håndhevet, ved modellgrensen, og
#: `SKJEMA` er byte-identisk med den registrerte v1-formen. Døren regner
#: fortsatt med `FUNN_MAKS * SITAT_MAKS` (`app._KANDIDAT_ARTEFAKT_MAKS`)
#: — det tallet er utledet av det som faktisk håndheves, ikke av et
#: skjemanøkkelord ingen rapport passerer uten å ha vært gjennom
#: `vurder` først.
SITAT_MAKS = 4096

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
                # `maxLength` hører IKKE hjemme her — se SITAT_MAKS.
                "sitat": {"type": "string", "minLength": 1},
            },
        },
    },
}

_KANDIDAT = {
    "type": "object", "additionalProperties": False,
    "required": ["funn", "intervjusporsmal", "kildetekst"],
    "properties": {
        "funn": {"type": "array", "items": _FUNN, "maxItems": FUNN_MAKS},
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
