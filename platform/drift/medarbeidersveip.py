"""M-40 (140) — medarbeidersveipen. DEN SISTE I FLÅTEN.

`disponit-medarbeidersveip.timer`, én gang i døgnet, kaller
`m40_sveip_medarbeider(p_maks_tenanter)`.

SVEIPEN AVGJØR INGENTING OG VARSLER INGEN. Den sier fra om at en
førsteuke har stått åpen over fristen, om at en aktiv ansatt aldri fikk
et løp, om at en måling ble samlet inn uten at en eneste gruppe ble
stor nok til å kunne leses, og om at en kontrakt hviler på en mal noen
siden har trukket tilbake — og der stopper den.

DET ER EN DOM, IKKE EN MANGEL:

  EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
  ROLLBACK.

M-28 sa det om en bil på veien. Her er det tyngre: en oppsigelse som
ble rullet tilbake er fortsatt en samtale som fant sted, en beskjed som
ble lest og et menneske som brukte kvelden på den.

SVEIPEN LESER ALDRI EN PULSVERDI. Den teller grupper og sammenligner
med målingens terskel; den ser aldri hva noen svarte. Det er ikke en
forsiktighet — det er den samme grensen aggregatdøra har, og en sveip
som var unntatt ville vært hullet i den. En port leser sveipens SQL og
krever at `s.verdi` ikke er nevnt.

ANSATTREGISTERET ER M-39s, OG DET RØRES IKKE HERFRA.
`disponit_medarbeidersveip` har INGEN rettighet på `lonnstaker` — ikke
engang SELECT. Sveipen leser det gjennom sin ene dør, som eies av
modulrollen og har en kolonnegrant uten `navn`.

SEKS FUNNTYPER KAN ALDRI REISES AV DENNE SVEIPEN, og at de ikke kan er
beviset: `beslutning_med_rettsvirkning` (det finnes ingen
beslutningsdør), `individprofil_bygget` (ingen kolonne bærer et tall om
et menneske), `puls_identifiserte_en_person` (`pulssvar` har ingen
personnøkkel), `gruppeterskel_endret` (ingen har UPDATE på kolonnen),
`kontrakt_uten_malversjon` (NOT NULL) og `krav_mangler` (løkka går over
tenanter som HAR et krav).

DET SVEIPEN RYDDER ETTER ER TIDEN. Døra nektet ikke da løpet ble
startet, og ikke da målingen ble åpnet. Så gikk det døgn, og ingen
gjorde noe; eller for få svarte. Ingen dør kunne fanget det, fordi
ingen kalte noen dør.

SVEIPEN SNAKKER IKKE UT. Denne fila importerer ingenting som kan det:
ingen `httpx`, ingen `requests`, ingen `socket`.

Formen er `transportsveip.py` sin, ordrett:

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
#: INGEN TERSKEL OG INGEN FRIST HER, og det er poenget: både
#: `gruppeterskel_min` og `apent_lop_frist_dogn` er TENANTENS og ligger
#: i `medarbeiderkrav` — og terskelen som faktisk gjelder for en måling
#: ligger på MÅLINGEN. En k-anonymitetsterskel låst i en driftsfil
#: ville vært huset som bestemte hvor godt kundens ansatte er vernet,
#: og den kunne vært endret ved å redigere en fil.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to medarbeidersveip overlapper aldri. Tallet er modulens
#: eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 641_802_355

#: Antall felt `m40_sveip_medarbeider` lover. Fire, som resten av flåten.
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
        try:
            rader = conn.execute(
                "SELECT * FROM m40_sveip_medarbeider(%s)", (grense,)).fetchall()
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
        (res.tenanter, res.nye, res.oppdaterte, res.lukkede) = verdier
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
