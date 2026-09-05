"""Sveipeflåtens tilstand, lest fra filsystemet (115).

HVA DENNE FINNES FOR. Plattformen har atten nattlige sveip. Hver av dem
teller sine egne sammenhengende feil i en fil, og skriver ett
`"alarm": 1` i journalen når telleren når to.

DEN ALARMEN HAR ALDRI HATT EN KONSUMENT. Feltet skrives av alle atten
og leses av ingen — et søk gjennom treet finner bare testene.

OG DET FARLIGSTE TILFELLET SKRIVER INGENTING I DET HELE TATT. En sveip
som feiler, etterlater i det minste en linje. En sveip som ALDRI
KJØRER — timeren deaktivert, enheten død ved oppstart, DSN-en borte fra
miljøfila — er helt taus. Den ser nøyaktig ut som en sveip uten funn.

    «En taushet kan per definisjon ikke varsle om seg selv — den må
     observeres utenfra, av en prosess med en annen rolle på en annen
     kadens.»  (`varselsender.py`, 035/090)

DENNE MODULEN ER DEN OBSERVATØREN. Den leser, den regner ikke: hvem som
er taus og hvem som er i alarm avgjøres i basen (115), av samme grunn
som funnreglene bor der i alle sveipene — regelen skal ha ett sted.

SIST KJØRT ER FILENS MTIME, og det er en ærlig kilde med én navngitt
begrensning: en kjøring som fant arbeidernøkkelen opptatt (`hoppet_over`)
skriver med VILJE ikke fila — feiltelleren skal stå urørt — og flytter
derfor ikke mtime. «Sist kjørt» betyr her «sist fullførte kjøring som
ikke ble hoppet over». Vinduene under er satt etter den definisjonen.

ROSTEREN ER EN KONSTANT, IKKE EN KATALOGLISTING. En listing ville bare
funnet sveip som HAR skrevet en fil — altså vært blind for nøyaktig den
tilstanden modulen finnes for. En sveip som aldri har kjørt må stå her
for å kunne savnes, og `test_sveipestatus` binder listen til filene på
disk begge veier.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

#: Hvor tilstandsfilene ligger når systemd ikke oppgir katalogen.
STANDARDKATALOG = Path("/var/lib/disponit")

#: FLÅTEN, med hver sveips VINDU i timer.
#:
#: Vinduet er ikke kadensen — det er kadensen PLUSS slark. En nattlig
#: sveip med 30 minutters spredning kan legitimt gå 24,5 timer mellom
#: to kjøringer, og en vert som var nede en time skal ikke gi varsel.
#: 30 timer for de nattlige er samme tall som 090 bruker for backupen,
#: og av samme grunn: det er trygt over ett døgn og godt under to.
#:
#: DE HYPPIGE JOBBENE HAR SITT EGET TALL. `artefaktrydding` går hvert
#: 15. minutt; et 30-timers vindu ville gjort den usynlig i et helt
#: døgn etter at den døde.
FLAATEN: dict[str, int] = {
    # Nattlige kalendertimere — ett døgn pluss slark.
    "anbudssveip": 30,
    "adressesveip": 30,
    "avstemmingssveip": 30,
    "begrepssveip": 30,
    "betalingssveip": 30,
    "compliancesveip": 30,
    "ehfsveip": 30,
    "fakturasveip": 30,
    "fordringssveip": 30,
    "henvendelsessveip": 30,
    "kampanjesveip": 30,
    "kontovaktsveip": 30,
    "kvalitetsprofilering": 30,
    "lagersveip": 30,
    "leverandorsveip": 30,
    "lonnssveip": 30,
    "merkevaresveip": 30,
    "motpartssveip": 30,
    "onboardingsveip": 30,
    "personvernsveip": 30,
    "prisboksveip": 30,
    "prosjektsveip": 30,
    "sanksjonssveip": 30,
    "retensjonsmaaling": 30,
    "tilskuddssveip": 30,
    "tilgangssveip": 30,
    "tollkodesveip": 30,
    "myndighetssveip": 30,
    "postjournalsveip": 30,
    "hmssveip": 30,
    "likviditetssveip": 30,
    "prognosesveip": 30,
    "optimalisatorsveip": 30,
    # Intervalljobber — vinduet er satt etter DERES kadens, ikke etter
    # døgnet. En jobb som skal gå hvert kvarter og ikke har gått på tre
    # timer, er død nok til at noen skal se på den.
    "artefaktrydding": 3,
}

#: HVA SOM IKKE ER I FLÅTEN, OG HVORFOR.
#:
#: `domenerevalidering` kjøres av `kjor_revalidering` og fører INGEN
#: feilteller — den har ingen tilstandsfil å lese, og en oppføring her
#: ville gitt et evig «taus» om en jobb som går hvert kvarter.
#: `backupstatus` og `selvtest` har sine EGNE taushetssveip fra 090 og
#: 091, som er eldre og mer presise enn denne. `varselsender` er den
#: som SENDER varselet; en observatør som savnet den, kunne ikke ha
#: varslet om det.
#:
#: `test_sveipestatus` binder listen til filene på disk begge veier:
#: en ny jobb med feilteller MÅ føres her, og en oppføring uten en
#: modul er rød.


@dataclass
class Sveiprad:
    sveip: str
    forventet_timer: int
    #: `None` når fila ikke finnes — en annen tilstand enn «kjørte, uten
    #: feil», og den farligere av de to.
    sist_kjort_epoch: float | None
    sammenhengende_feil: int | None
    uten_tilstandsfil: bool
    #: Fila fantes, men lot seg ikke lese som `{"feil": n}`. Da er
    #: telleren ukjent, og det skal ikke se ut som null.
    ulesbar: bool = False


def katalog() -> Path:
    """Hvor filene ligger.

    Unit-filen setter `StateDirectory=disponit`, samme katalog og samme
    eier som sveipene selv — de deler Unix-identitet, og det er nettopp
    derfor denne jobben kan lese dem uten en eneste ekstra rettighet.
    """
    eksplisitt = os.environ.get("DISPONIT_SVEIPETILSTANDSKATALOG")
    if eksplisitt:
        return Path(eksplisitt)
    statedir = os.environ.get("STATE_DIRECTORY")
    if statedir:
        # systemd oppgir en kolonseparert liste når flere er deklarert.
        return Path(statedir.split(":")[0])
    return STANDARDKATALOG


def les_flaaten(kat: Path | None = None) -> list[Sveiprad]:
    """Én rad per sveip i ROSTEREN — også de som ikke har noen fil.

    Rekkefølgen er rosterens, sortert, så to kjøringer gir samme
    rekkefølge og en diff mellom dem er lesbar.
    """
    kat = kat or katalog()
    ut: list[Sveiprad] = []
    for navn in sorted(FLAATEN):
        fil = kat / f"{navn}.json"
        try:
            stat = fil.stat()
        except OSError:
            # FILEN FINNES IKKE. Det er en tilstand, ikke en feil i
            # denne jobben: sveipen har aldri fullført en kjøring på
            # denne verten.
            ut.append(Sveiprad(navn, FLAATEN[navn], None, None, True))
            continue
        feil: int | None = None
        ulesbar = False
        try:
            innhold = json.loads(fil.read_text(encoding="utf-8"))
            lest = int(innhold["feil"])
            if lest < 0:
                raise ValueError("negativ feilteller")
            feil = lest
        except (OSError, ValueError, TypeError, KeyError):
            # `feil` STÅR IGJEN SOM None. Den mellomliggende variabelen
            # finnes nettopp for det: en delvis lest verdi skal ikke bli
            # stående når validering etterpå felte den.
            feil = None
            # FILA FANTES, MEN VAR IKKE KONTRAKTEN. Telleren er da
            # UKJENT — og `None` er det ærlige svaret. Å skrive 0 her
            # ville sagt «kjørte, uten feil» om en fil ingen kan lese.
            ulesbar = True
        ut.append(Sveiprad(navn, FLAATEN[navn], stat.st_mtime, feil,
                           False, ulesbar))
    return ut
