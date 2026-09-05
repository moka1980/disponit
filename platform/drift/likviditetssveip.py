"""M-15 (128) — likviditetssveipen. MÅLINGEN ER PRODUKTET.

`disponit-likviditetssveip.timer`, én gang i døgnet, kaller
`m15_sveip_likviditet(p_grense)`.

SVEIPEN LAGER INGEN PROGNOSE, OG DEN MÅLER INGEN. Den SIER FRA om at
en prognose ikke er målt — og der stopper den.

DET ER EN DOM, IKKE EN MANGEL. En sveip som målte selv, ville hentet
saldoen og sammenlignet i det stille, og da hadde modulen gitt seg
selv karakter. Målingen er en handling med en ansvarlig:
`m15_registrer_maaling` kalles av et menneske gjennom flaten, og
navnet står på raden.

KLYNGENS DELTE DOM: EN GAL PROGNOSE SER NØYAKTIG UT SOM EN RIKTIG
PROGNOSE — helt til horisonten er passert, og da har alle sluttet å
se. `prognose_uten_maaling` er derfor et funn INGEN kan lukke, og den
eneste veien til å lukke det er at målingen faktisk registreres.

SVEIPEN SIER HELLER IKKE OPP NOE OG BETALER INGENTING. Denne fila
importerer ingenting som kan snakke ut: ingen `httpx`, ingen
`requests`, ingen `socket`. Katalogens vaktsetning sier at
oppsigelser og betalinger går gjennom egne policykontrollerte
moduler, og gjerdet står i koden.

Regelen eies av databasen (128): en prognose hvis horisont er passert
med nådefristen og som ingen har målt er `prognose_uten_maaling`; en
regnet på et grunnlag eldre enn tenantens tak er
`prognose_mot_utdatert_grunnlag`; en bane som går under null innenfor
horisonten er `bane_under_null`; en modell som avvikles innen
varselvinduet er `modell_utloper_snart`; og en tenant uten grenser er
`ingen_krav`.

TO FUNN LUKKES BARE HERFRA. `prognose_uten_maaling` forsvinner når
målingen kommer, `prognose_mot_utdatert_grunnlag` når det kommer
ferske bevegelser. `bane_under_null` KAN lukkes av et menneske —
«kassekreditt er avtalt» er en legitim beslutning om noe som ennå
ikke er brutt — og 125/126s vakt sørger for at den lukkingen står
natten over.

Formen er `hmssveip.py` sin, ordrett:

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
#: INGEN HORISONT OG INGEN MÅLEFRIST HER, og det er poenget: begge er
#: TENANTENS og ligger i `likviditetskrav`. En horisont vi låste i en
#: driftsfil ville vært en fullmakt modulen ga seg selv over kundens
#: planlegging — og et byggefirma med kvartalsvise innbetalinger har
#: ikke samme planleggingshorisont som en abonnementsbedrift.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to likviditetssveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 714_552_306

#: Antall felt `m15_sveip_likviditet` lover. Fire, som resten av
#: flåten: modulen har ingen rad å rydde tilsvarende M-48s forlatte
#: reservasjoner.
KONTRAKTFELT = 4


@dataclass
class Sveipresultat:
    tenanter: int = 0
    nye: int = 0
    #: Varsler som alt sto der og bare ble sett på nytt.
    oppdaterte: int = 0
    lukkede: int = 0
    #: INGEN `forlatte`: M-48 rydder forlatte reservasjoner, M-15 har
    #: ingenting tilsvarende. Å bære med seg feltet med verdien 0
    #: ville vært en linje som lot som den målte noe.
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
                "SELECT * FROM m15_sveip_likviditet(%s)",
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
