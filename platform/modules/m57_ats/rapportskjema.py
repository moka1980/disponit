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
        # AVSKRUINGSSPORET ER DET ENESTE UNNTAKET FRA STRIPPINGEN OVER
        # (Cursor P2, runde 4 på #247). Sporet ble født i runde 3 på
        # per-kandidat-artefaktet, men `bygg` plukker eksplisitt, så det
        # døde på leveransegrensen: rapporten som faktisk sendes til
        # `/v1/artefakt` bar ingenting som pekte tilbake på
        # `revisjonshendelse`-raden — nøyaktig hullformen runde 3 skulle
        # lukke («en revisjonshendelse ingen artefakt peker på, er en
        # logg uten lesere»).
        #
        # PROSESSNIVÅ, IKKE PER KANDIDAT: oppslaget er memoisert til ETT
        # per bunt, så én autorisasjon gjelder hele leveransen. Feltet
        # finnes bare når blindingen faktisk var av — et felt som alltid
        # er der sier ingenting om unntaket det dokumenterer, og derfor
        # står det ikke i `required`.
        #
        # `ts` er MED VILJE ikke med: raden `hendelse_id` peker på eier
        # tidsstempelet, og den er autoritativ. Å kopiere det hit ville
        # gitt en andre sannhet å drifte — og `ts` kommer som `datetime`
        # fra basen, altså en form denne lukkede, JSON-serialiserte
        # rapporten uansett ikke tar imot.
        "avskruing": {
            "type": "object", "additionalProperties": False,
            "required": ["hendelse_id", "aktor"],
            "properties": {
                "hendelse_id": {"type": "string", "minLength": 1},
                "aktor": {"type": "string", "minLength": 1},
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
    rapport = {"rapporttype": "rekruttering.evaluering.rapport",
               "versjon": 1,
               "profil": {"profil_id": profil["profil_id"],
                          "versjon": profil["versjon"],
                          "navn": profil["navn"]},
               "antall_soknader": antall_soknader,
               "rangering": resultat["rangering"],
               "kandidater": kandidater,
               "fremdrift": resultat["fremdrift"]}
    # ... men SPORET krysser (se `SKJEMA["properties"]["avskruing"]`).
    # `blinding_av` er et buntnivåflagg og oppslaget er memoisert, så
    # alle kandidatartefaktene bærer samme rad: den første som har
    # feltet, ER buntens autorisasjon. Bare de to feltene som peker —
    # hvem, og hvilken rad — promoteres.
    spor = next((r["avskruing"] for r in resultat["artefakter"].values()
                 if "avskruing" in r), None)
    if spor is not None:
        rapport["avskruing"] = {"hendelse_id": spor["hendelse_id"],
                                "aktor": spor["aktor"]}
    return rapport


#: -------------------------------------------------------------------
#: V2 — BESLUTNINGSSPORET (#168, eiers A-dom): rapporten bærer aldri
#: kandidatpayload. Funnene, sitatene, intervjuspørsmålene og den
#: blindede kildeteksten er 057-lagrenes payload under kundens frist;
#: det promoterte artefaktet er varig evidens, og de to skal ikke være
#: samme dokument. Etter reapen holder beslutningssporet tokener uten
#: nøkkel — blindet fritekst holder innhold uten navn, og bare det
#: første slutter å være en personopplysning (eiers korreksjon i #168:
#: maskert fritekst identifiserer indirekte i et lite arbeidsmarked).
#:
#: UTKAST TIL ARKITEKTREVIEW — v2 ER IKKE REGISTRERT. Registreringen
#: krever en release M-57 ikke har (registrer-m57-ats.py), og
#: artefakttypens navneform er prefikslukket, så identitetsvalget
#: (samme type med `versjon: 2` mot registreringstuppelens immutabilitet,
#: eller nytt navn med nytt lese-API-par) er arkitektens — se #168.
#: Leseflaten må samtidig hente funnene fra lagrene (#183-nabolaget);
#: den flyttingen er egen PR og hører ikke her.
#:
#: MODELLDIGESTEN er ny og PÅKREVD: `sha256:<64 hex>` — samme form som
#: biasmålingens binding (`krev_biasmaaling`), slik at rapporten selv
#: sier hvilken modell som rangerte. DIGEST ER IKKE BIASBEVIS (arkitekt
#: A3, 30/8): feltet beviser hvilken modellversjon som kjørte, aldri at
#: den er MÅLT — den bindingen bor i akseptporten (port 17 /
#: krev_biasmaaling: imagebytte uten ny måling blokkerer der), og en
#: leser skal aldri slutte fra rapporten til bias. Ærlighetsfeltene
#: står igjen: `antall_soknader`, `fremdrift` (hva som faktisk ble
#: lest) og `avskruing` (blinding var av, med raden som autoriserte
#: det).
#:
#: VERSJONEN ER IDENTITET, IKKE BARE INNHOLD (arkitekt A4, SP-12):
#: lesere MÅ dispatche på `versjon`, og en v1-rapport skal aldri kunne
#: leses SOM v2 — promoterte v1-rapporter finnes og består. Hvordan
#: versjonen bæres i ARTEFAKTETS identitet (registreringstuppelen mot
#: den prefikslukkede navneformen) er arkitektens dom i #168 og hører
#: til registrerings-PR-en.
VERSJON_V2 = 2

SKJEMA_V2 = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": f"disponit:rekruttering.evaluering.rapport:v{VERSJON_V2}",
    "type": "object", "additionalProperties": False,
    "required": ["rapporttype", "versjon", "profil", "antall_soknader",
                 "rangering", "modelldigest", "fremdrift"],
    "properties": {
        "rapporttype": {"const": "rekruttering.evaluering.rapport"},
        "versjon": {"const": VERSJON_V2},
        "profil": SKJEMA["properties"]["profil"],
        "antall_soknader": SKJEMA["properties"]["antall_soknader"],
        # Rangeringen er HELE leveransen i v2: referanse, poeng og
        # nedbrytning per krav — aldri innholdet bak dem. EGEN form, ikke
        # v1s delte (arkitekt A2, 30/8): et lukket skjema avviser ukjente
        # felt, men stopper ikke payload i et KJENT — nedbrytningen er
        # derfor LUKKEDE nøkler (kravnavnets lengdetak) med heltall som
        # verdier. Ingen fritekst, ingen sitatkanal.
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
                        "propertyNames": {"maxLength": 64},
                        "additionalProperties": {"type": "integer",
                                                 "minimum": 0},
                    },
                },
            },
        },
        "modelldigest": {"type": "string",
                         "pattern": "^sha256:[0-9a-f]{64}$"},
        "fremdrift": SKJEMA["properties"]["fremdrift"],
        "avskruing": SKJEMA["properties"]["avskruing"],
    },
}


def bygg_v2(resultat: dict, *, profil: dict, antall_soknader: int,
            modelldigest: str) -> dict:
    """`kjor_bunt`-utfallet → beslutningssporet (#168 A). Payloaden
    plukkes ALDRI: ingen linje her leser `artefakter` for annet enn
    avskruingssporet, så et nytt payloadfelt i utfallet kan ikke lekke
    inn ved et uhell."""
    rapport = {"rapporttype": "rekruttering.evaluering.rapport",
               "versjon": VERSJON_V2,
               "profil": {"profil_id": profil["profil_id"],
                          "versjon": profil["versjon"],
                          "navn": profil["navn"]},
               "antall_soknader": antall_soknader,
               "rangering": resultat["rangering"],
               "modelldigest": modelldigest,
               "fremdrift": resultat["fremdrift"]}
    # Samme avskruingsspor som v1 — unntaket skal synes i evidensen.
    spor = next((r["avskruing"] for r in resultat["artefakter"].values()
                 if "avskruing" in r), None)
    if spor is not None:
        rapport["avskruing"] = {"hendelse_id": spor["hendelse_id"],
                                "aktor": spor["aktor"]}
    return rapport
