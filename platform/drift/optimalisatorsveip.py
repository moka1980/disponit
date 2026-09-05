"""M-36 (132) — optimalisatorsveipen. MÅLINGEN AV EFFEKTEN ER
PRODUKTET.

`disponit-optimalisatorsveip.timer`, én gang i døgnet, kaller
`m36_sveip_optimalisering(p_maks_tenanter)`.

SVEIPEN RANGERER INGENTING OG IVERKSETTER INGENTING. Den sier fra om
at en effekt ikke er målt, om at det øverste forslaget hviler bare på
korrelasjon, og om at en porteføljestopp har blitt stående — og der
stopper den.

M-36s VAKTSETNING ER EN ADVARSEL, IKKE EN SELVFØLGE:

  EN OPTIMALISATOR SOM FINNER AT DEN BESTE FORBEDRINGEN ER «GI M-36
  LOV TIL Å GJØRE X», ER IKKE ØDELAGT. DEN GJØR NØYAKTIG DET DEN BLE
  BEDT OM.

Derfor er fullmaktsutvidelse UREPRESENTERBAR og ikke frarådet:
sveiperollen har nøyaktig ÉN rettighet — EXECUTE på
`m36_sveip_optimalisering` — og modulrollen har ingen rettighet på
`policyer`, `policyutkast` eller `policyaktivering`. Denne fila
importerer heller ingenting som kan snakke ut.

KLYNGENS DELTE DOM: EN GAL PROGNOSE SER NØYAKTIG UT SOM EN RIKTIG
PROGNOSE — helt til horisonten er passert, og da har alle sluttet å
se. `rangering_uten_maaling` er derfor et funn ingen kan lukke, og den
eneste veien ut av det er at effekten faktisk registreres.

«KORRELASJON PRESENTERES IKKE SOM ÅRSAK» ER ET KRAV TIL DATAMODELLEN,
ikke til teksten på skjermen. `grunnlagstype` er et lukket sett på tre,
kopiert inn i rangeringen ved avgivelse — og `korrelasjon_alene_paa_topp`
gjør det observerbart: modellen FÅR rangere på korrelasjon, men at det
ØVERSTE forslaget gjør det, skal noen se.

TO AV TRE FUNN LUKKES BARE HERFRA. `rangering_uten_maaling` forsvinner
når effekten måles, `korrelasjon_alene_paa_topp` når toppen endrer seg.
`stopp_staar_uten_oppheving` KAN lukkes av et menneske — «vi vet, den
skal stå» er en legitim beslutning — og 125/126s vakt sørger for at den
lukkingen står natten over.

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
#: INGEN HORISONT OG INGEN MÅLEFRIST HER: begge er TENANTENS og ligger
#: i `optimaliseringskrav`. En målefrist låst i en driftsfil ville vært
#: en påstand om hvor lenge det er greit å la en effekt stå umålt — og
#: det er en faglig vurdering, ikke en driftsparameter.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to optimalisatorsveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 508_213_667

#: Antall felt `m36_sveip_optimalisering` lover. Fire, som resten
#: av flåten.
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
    # TAKET HÅNDHEVES, DET FORESLÅS IKKE (CodeRabbit).
    #
    # `GRENSE` var bare en STANDARDVERDI: en kaller som ba om 100 000
    # tenanter fikk 100 000, og døra har `greatest(p_maks, 1)` uten
    # øvre grense. Kjøreren gjør det ikke i dag — men et tak som bare
    # gjelder når ingen ber om noe annet, er ikke et tak.
    #
    # Taket begrenser TRANSAKSJONEN, ikke sannheten: funnene er
    # idempotente, så neste døgn tar kjøringen igjen.
    grense = min(grense, GRENSE)
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
                "SELECT * FROM m36_sveip_optimalisering(%s)",
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
