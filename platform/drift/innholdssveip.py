"""M-20 (134) — innholdssveipen. KILDEN ER PRODUKTET.

`disponit-innholdssveip.timer`, én gang i døgnet, kaller
`m20_sveip_innhold(p_maks_tenanter)`.

SVEIPEN PUBLISERER INGENTING, RULLER INGENTING TILBAKE OG FORNYER
INGEN KILDE. Den sier fra om at en levende side hviler på et dokument
som har utløpt, om at et utkast er merket klart uten at noen har sett
det, og om at en kilde som bærer en levende påstand snart går ut — og
der stopper den.

DET ER EN DOM, IKKE EN MANGEL. En sveip som avpubliserte siden selv
ville tatt en beslutning om hva huset sier til verden, uten et navn på
den — og det er nøyaktig det klyngens dom advarer mot:

  EN YTRING AVGITT I HUSETS NAVN KAN IKKE TAS TILBAKE — OG DEN SOM
  LESER DEN VET IKKE AT EN MASKIN SKREV DEN.

M-20s EGEN: MODULEN PUBLISERER INGENTING SELV.
`innholdspublisering.publisert_av` er `NOT NULL` fordi en publisering
uten et navn bak er en publisering modulen gjorde.

TRE FUNNTYPER KAN ALDRI REISES AV DENNE SVEIPEN, og at de ikke kan er
beviset: `paastand_uten_kilde` (fremmednøkkel, NOT NULL),
`publisering_uten_forhaandsvisning` (fremmednøkkel, NOT NULL) og
`publisering_uten_rollbackvei` (lukket sett med to verdier, begge en
vei). Å oppdage en udokumentert påstand i en nattlig sveip er å
oppdage en skade, ikke å hindre den.

DET SVEIPEN FAKTISK RYDDER ETTER ER TIDEN. Døra nektet da påstanden
ble skrevet, ved `klar`, og ved publisering. Så gikk det måneder, og
databladet gikk ut mens siden sto ute. Ingen dør kunne fanget det,
fordi ingen kalte noen dør.

TO AV TRE LUKKES BARE HERFRA. `publisert_paastand_uten_gyldig_kilde`
forsvinner når kilden fornyes eller siden avpubliseres,
`klart_utkast_uten_forhaandsvisning` når noen ser utkastet.
`kilde_utloper_snart_uavklart` KAN lukkes av et menneske — «vi har
sjekket, dokumentet står seg» er en legitim avklaring med et navn på —
og 125/126s vakt sørger for at den lukkingen står natten over.

SVEIPEN SNAKKER IKKE UT. Denne fila importerer ingenting som kan det:
ingen `httpx`, ingen `requests`, ingen `socket`.

Formen er `motesveip.py` sin, ordrett:

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
#: Advisory-nøkkel: to innholdssveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 648_203_517

#: Antall felt `m20_sveip_innhold` lover. Fire, som resten av flåten.
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
                "SELECT * FROM m20_sveip_innhold(%s)",
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
