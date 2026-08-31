"""M-10 (090) — lesejobben som fører backupens verifisering inn i basen.

Backupen VET at den lyktes: `backup-db.sh` restaurerer dumpen til en
engangsbase, teller tabellene og måler filen, og feller kjøringen på de
samme grensene tabellens CHECK-er håndhever (>= 10 tabeller, > 1024 B).
Den kunnskapen døde i journalen. Denne jobben er broen, og den er BARE
en bro: den måler ingenting selv, den regner ingenting om, og den fyller
aldri ut noe som mangler.

FAIL-CLOSED, I TRE TILSTANDER SOM IKKE KAN FORVEKSLES (dommen):

  * RAPPORTEN FINNES IKKE → 0 rader, exit 0. En installasjon som ennå
    ikke har kjørt en backup er ikke en feilet lesejobb. Tilstanden er
    ikke usett av den grunn: `varsle_backupverifisering_uteblitt` varsler
    på nøyaktig 30 timers taushet, og en tom tabell varsles på samme
    terskel som en foreldet (fravær er feil i v1). En exit 1 her ville
    bare gjort en frisk vert rød hvert 30. minutt.

  * RAPPORTEN ER UGYLDIG → 0 rader, exit 1. Ugyldig JSON, et manglende
    felt, en verdi av feil type, eller en verdi tabellens CHECK avviser.
    ALDRI GJETT: en manglende `tabeller` blir ikke 0, en uparsbar
    `restore_varighet_s` blir ikke NULL, og en rad skrives ikke «med
    forbehold». En verifisering vi ikke kan lese er ikke en verifisering,
    og en rad som later som noe annet ville gjort hele innsynet verdiløst
    — det er nettopp «ser backupen ut til å virke» flaten skal svare på.

  * RAPPORTEN ER GYLDIG → én rad, eller null hvis den alt står der.
    `registrer_backupverifisering` er idempotent på `backup_ts`
    (ON CONFLICT DO NOTHING), så jobben kan kjøre hvert 30. minutt over
    den samme dagsferske rapporten uten å skrive noe nytt. Det er hele
    grunnen til at kadensen kan være tettere enn backupens egen.

CHECK-EN ER BASENS, IKKE JOBBENS. Grensene duplikeres bevisst ikke her:
en kopi ville drevet fra migrasjonen første gang en av dem endres, og da
hadde jobben avvist rader basen ville tatt imot — eller verre, tatt imot
rader den ikke ville. Vi validerer TYPENE (det JSON-laget må avgjøre for
å kunne sende et argument i det hele tatt) og lar `CheckViolation` bli
exit 1.

Rollen er `disponit_driftstatus`, og den har nøyaktig ÉN rettighet:
EXECUTE på skrivedøren. Ingen tabellrettigheter, ingen lesedør, ingen
sveip. Et kompromittert lesejobbmiljø kan skrive en verifisering det ikke
har gjort — og ikke lese en eneste kunderad.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

#: Hvor `backup-db.sh` legger rapporten. Katalogen er backupens egen, og
#: uniten monterer den ReadOnly: jobben skal kunne lese sitt eget grunnlag
#: og ingenting annet i den.
STANDARDSTI = "/var/backups/disponit/siste-verifisering.json"

#: Feltene rapporten MÅ ha, med typen argumentet sendes som. Rekkefølgen
#: er kallets. En rapport med ekstra felt er ikke ugyldig — den er skrevet
#: av en nyere backup enn denne jobben kjenner, og de fem under er
#: fortsatt sanne. En rapport som MANGLER ett av dem er ugyldig, punktum.
FELTER: tuple[tuple[str, type | tuple[type, ...]], ...] = (
    ("backup_ts", str),
    ("verifisert_ts", str),
    # Varigheten kan være heltall eller desimaltall i JSON; begge er
    # `numeric` i basen. `bool` er en `int` i Python og utelukkes derfor
    # eksplisitt i `_gyldig` — `true` er ikke en varighet.
    ("restore_varighet_s", (int, float)),
    ("tabeller", int),
    ("storrelse_b", int),
)


class UgyldigRapport(Exception):
    """Rapporten finnes, men kan ikke leses som en verifisering."""


@dataclass
class Resultat:
    #: Rader faktisk skrevet: 1 ved ny verifisering, 0 ved gjenspilling.
    skrevet: int = 0
    #: Sann når rapportfilen ikke fantes — den milde tilstanden.
    mangler: bool = False
    #: Satt når rapporten fantes og var ugyldig; teksten er kort og
    #: fri for filinnhold (en korrupt rapport skal ikke ekko-es til
    #: journalen).
    grunn: str | None = None


def _gyldig(rapport: object) -> dict:
    """Rapport → kallets argumenter, eller `UgyldigRapport`.

    Ingen konvertering av verdier som ikke ALT er riktig type: en
    `"137"` blir ikke 137 her. Backupen skriver tallene som tall, og en
    streng der er et tegn på at rapporten kommer fra noe annet enn
    `backup-db.sh` — da skal jobben stoppe, ikke tolke.
    """
    if not isinstance(rapport, dict):
        raise UgyldigRapport(
            f"rapporten er {type(rapport).__name__}, ikke et objekt")
    verdier = []
    for navn, typer in FELTER:
        if navn not in rapport:
            raise UgyldigRapport(f"feltet {navn} mangler")
        verdi = rapport[navn]
        # `True` er en `int` for `isinstance`. En backup som rapporterer
        # `"tabeller": true` er ødelagt, ikke sann.
        if isinstance(verdi, bool) or not isinstance(verdi, typer):
            raise UgyldigRapport(f"feltet {navn} har feil type")
        verdier.append(verdi)
    return {navn: verdi for (navn, _), verdi in zip(FELTER, verdier)}


def les_rapport(sti: str | Path = STANDARDSTI) -> dict | None:
    """Rapporten som argumenter, eller `None` når filen ikke finnes.

    De to utfallene er BEVISST ulike: `None` er den milde tilstanden
    (exit 0), et unntak er den harde (exit 1). En tom fil er ikke
    fravær — den er en rapport som ikke er JSON.
    """
    p = Path(sti)
    try:
        raa = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        # En uleselig fil FINNES. Det er ikke fravær, og en jobb som
        # svelget det ville skjult en rettighetsfeil på den ene filen
        # den er satt til å lese.
        raise UgyldigRapport(f"kunne ikke leses: {type(e).__name__}") from e
    try:
        rapport = json.loads(raa)
    except (ValueError, UnicodeDecodeError) as e:
        raise UgyldigRapport(f"ikke gyldig JSON: {type(e).__name__}") from e
    return _gyldig(rapport)


def kjor(conn, *, sti: str | Path = STANDARDSTI) -> Resultat:
    """Les rapporten og registrer den. -> `Resultat`.

    Én transaksjon: enten står raden, eller så står ingenting. Det er
    ikke en formalitet — `registrer_backupverifisering` er den eneste
    skrivingen jobben gjør, og et halvt utfall finnes ikke å rulle
    fremover fra.
    """
    try:
        argumenter = les_rapport(sti)
    except UgyldigRapport as e:
        return Resultat(grunn=str(e))
    if argumenter is None:
        return Resultat(mangler=True)
    try:
        # TYPENE CASTES EKSPLISITT. psycopg utleder typen av VERDIEN:
        # en Python-float blir `double precision` (som ikke har en
        # implisitt vei til `numeric`), og et lite heltall blir
        # `smallint` (som ikke matcher `bigint`). Uten castene finner
        # PostgreSQL ingen overload i det hele tatt, og jobben ville
        # feilet med «function does not exist» på en helt gyldig rapport.
        rad = conn.execute(
            "SELECT registrer_backupverifisering("
            "%s::timestamptz,%s::timestamptz,%s::numeric,%s::int,"
            "%s::bigint)",
            (argumenter["backup_ts"], argumenter["verifisert_ts"],
             argumenter["restore_varighet_s"], argumenter["tabeller"],
             argumenter["storrelse_b"])).fetchone()
        conn.commit()
    except Exception as e:                                    # noqa: BLE001
        # CHECK-brudd, et tidsstempel basen ikke kan tolke, en manglende
        # rettighet — alle er den samme dommen for denne jobben: vi har
        # IKKE registrert en verifisering, og skal si det med exit 1.
        # Feiltypen logges, aldri rapportens innhold.
        conn.rollback()
        return Resultat(grunn=f"avvist av basen: {type(e).__name__}")
    return Resultat(skrevet=int(rad[0]))
