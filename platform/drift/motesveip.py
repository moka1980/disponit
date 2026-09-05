"""M-7 (133) — møtesveipen. REFERATET ER PRODUKTET.

`disponit-motesveip.timer`, én gang i døgnet, kaller
`m7_sveip_moter(p_maks_tenanter)`.

SVEIPEN SKRIVER INGEN REFERATER OG LUKKER INGEN AKSJONER. Den sier fra
om at et møte er over uten at noen har skrevet noe, om at en aksjon
står over fristen, og om at et punkt maskinen var usikker på verken er
rettet eller bekreftet — og der stopper den.

DET ER EN DOM, IKKE EN MANGEL. En sveip som skrev referatet selv ville
gjort maskinens gjengivelse til fasit uten at noen leste den, og det er
nøyaktig det klyngens dom advarer mot:

  EN YTRING AVGITT I HUSETS NAVN KAN IKKE TAS TILBAKE — OG DEN SOM
  LESER DEN VET IKKE AT EN MASKIN SKREV DEN.

M-7s EGEN: MODULEN FATTER INGEN BESLUTNING. En «beslutning» i et
referat er noe MENNESKER tok, og `motebeslutning.besluttet_av` er
`NOT NULL` fordi en beslutning uten et navn bak er en beslutning
modulen fattet.

`opptak_uten_hjemmel` KAN ALDRI REISES AV DENNE SVEIPEN, og at den
ikke kan er beviset: `moteopptak.hjemmel_id` er `NOT NULL` med
fremmednøkkel, og `m7_start_opptak` nekter FØR raden finnes. ET NEKT
SOM KOMMER ETTER MIKROFONEN ER IKKE ET NEKT — å oppdage et ulovlig
opptak i en nattlig sveip er å oppdage en skade, ikke å hindre den.

TO AV TRE FUNN LUKKES BARE HERFRA. `mote_uten_referat` forsvinner når
referatet skrives, `aksjon_over_frist` når aksjonen lukkes.
`ubekreftet_punkt_uavklart` KAN lukkes av et menneske — «vi har lest
det, det stemmer» er en legitim avklaring med et navn på — og 125/126s
vakt sørger for at den lukkingen står natten over.

SVEIPEN SNAKKER IKKE UT. Denne fila importerer ingenting som kan det:
ingen `httpx`, ingen `requests`, ingen `socket`.

Formen er `prognosesveip.py` sin, ordrett:

  * **Advisory-lås.** To sveip overlapper aldri; `hoppet_over` står PÅ
    resultatet så kalleren vet at feiltelleren skal stå urørt.
  * **Kontrakten valideres FØR commit**, og på ALLE radene.
  * **To sammenhengende feilede kjøringer → alarm.**
  * **Én JSON-linje per kjøring, exit 1 ved feil.**

TAKET er 500 tenanter per kjøring. Det begrenser TRANSAKSJONEN, ikke
sannheten.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Maks antall tenanter sveipen tar per kjøring.
GRENSE = 500
#: INGEN REFERATFRIST OG INGEN SIKKERHETSTERSKEL HER, og det er
#: poenget: begge er TENANTENS og ligger i `motekrav`. En terskel låst
#: i en driftsfil ville vært en påstand om hvor mye det koster å ta
#: feil i ET REFERAT — og et styremøte og en ukentlig statusrunde tåler
#: ikke det samme.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to møtesveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 642_118_905

#: Antall felt `m7_sveip_moter` lover. Fire, som resten av flåten.
KONTRAKTFELT = 4


@dataclass
class Sveipresultat:
    tenanter: int = 0
    nye: int = 0
    #: Funn som alt sto der og bare ble sett på nytt.
    oppdaterte: int = 0
    lukkede: int = 0
    feilet: bool = False
    alarm_utlost: bool = False
    #: En kjøring som fant arbeidernøkkelen opptatt har verken lyktes
    #: eller feilet.
    hoppet_over: bool = False


def kjor(conn, *, grense: int = GRENSE,
         tidligere_feil: int = 0) -> Sveipresultat:
    """Én sveipekjøring.

    `tidligere_feil` er antall sammenhengende feilede kjøringer FØR
    denne; kalleren (timeren) bærer den telleren mellom kjøringer, siden
    hver kjøring er en egen prosess.
    """
    res = Sveipresultat()
    fikk_lås = conn.execute("SELECT pg_try_advisory_lock(%s)",
                            (ARBEIDERNOKKEL,)).fetchone()[0]
    if not fikk_lås:
        # HOPPET OVER, ikke vellykket. Et rent standardresultat her ville
        # sett ut som en kjøring som fant null funn, og kalleren ville
        # persistert feiltellingen 0 — altså slettet en alt opptelt feil.
        res.hoppet_over = True
        return res
    try:
        # KONTRAKTEN VALIDERES FØR COMMIT, og på ALLE radene.
        # REKKEFØLGEN ER DOMMEN: bare en validert kontrakt committes.
        try:
            rader = conn.execute(
                "SELECT * FROM m7_sveip_moter(%s)",
                (grense,)).fetchall()
        except Exception:
            _rull_tilbake(conn)
            res.feilet = True
            res.alarm_utlost = tidligere_feil + 1 >= ALARM_ETTER_FEIL
            return res
        if len(rader) != 1:
            _rull_tilbake(conn)
            res.feilet = True
            res.alarm_utlost = tidligere_feil + 1 >= ALARM_ETTER_FEIL
            return res
        # …OG RADENS FORM ER EN DEL AV KONTRAKTEN. `[:KONTRAKTFELT]` og
        # ikke hele raden: sveipen LESER fire felt, og en dør som en dag
        # returnerer et femte skal ikke gjøre en gyldig kjøring til en
        # feilet (#358s lærdom).
        try:
            verdier = tuple(int(v) for v in rader[0][:KONTRAKTFELT])
            if len(verdier) != KONTRAKTFELT:
                raise ValueError("kontrakten ga ikke fire felt")
        except (IndexError, TypeError, ValueError):
            _rull_tilbake(conn)
            res.feilet = True
            res.alarm_utlost = tidligere_feil + 1 >= ALARM_ETTER_FEIL
            return res
        try:
            conn.commit()
        except Exception:
            _rull_tilbake(conn)
            res.feilet = True
            res.alarm_utlost = tidligere_feil + 1 >= ALARM_ETTER_FEIL
            return res
        (res.tenanter, res.nye, res.oppdaterte,
         res.lukkede) = verdier
        return res
    finally:
        # Opplåsingen er BEST EFFORT. Låsen er sesjonsscopet: en død
        # sesjon slipper den uansett.
        try:
            conn.execute("SELECT pg_advisory_unlock(%s)",
                         (ARBEIDERNOKKEL,))
            conn.commit()
        except Exception:
            pass


def _rull_tilbake(conn) -> None:
    """Rollback som aldri kaster. En død tilkobling kan ikke rulles tilbake."""
    try:
        conn.rollback()
    except Exception:
        pass
