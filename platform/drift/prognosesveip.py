"""M-33 (130) — prognosesveipen. DOMMEN ER PRODUKTET.

`disponit-prognosesveip.timer`, én gang i døgnet, kaller
`m33_sveip_prognose(p_maks_tenanter)`.

SVEIPEN LAGER INGEN PROGNOSE OG MÅLER INGEN UKE. Den sier fra om at en
uke ikke er målt, og den feller dom over modellen når nok uker ER målt
— og der stopper den.

DET ER EN DOM, IKKE EN MANGEL. En sveip som målte selv ville hentet
timelistene og sammenlignet i det stille, og da hadde modulen gitt seg
selv karakter. Målingen er en handling med en ansvarlig:
`m33_registrer_maaling` kalles av et menneske gjennom flaten, og navnet
står på raden — for alltid, siden målingen ikke kan rettes.

KLYNGENS DELTE DOM: EN GAL PROGNOSE SER NØYAKTIG UT SOM EN RIKTIG
PROGNOSE — helt til horisonten er passert, og da har alle sluttet å se.

M-33s EGEN DOM, OG DEN LEVER I DENNE FILA: EN MODELL SOM IKKE KAN
TAPE, HAR IKKE VUNNET. `slaar_ikke_naiv_baseline` er funnet ingen kan
lukke, og det er bare ekte fordi v1-modellen (glidende snitt) og
basislinjen («samme som forrige uke») er FORSKJELLIGE tall. En
«prognose» som kopierte forrige uke ville hatt null avvik mot
basislinjen for alltid, og invarianten ville vært grønn uten å måle
noe.

SVEIPEN TAR INGEN PERSONALAVGJØRELSE. Vaktsetningen sier det rett ut,
og gjerdet står i koden: denne fila importerer ingenting som kan
snakke ut — ingen `httpx`, ingen `requests`, ingen `socket` — og den
eneste døra sveiperollen har EXECUTE på, skriver funn.

Regelen eies av databasen (130): en uke som er over med målefristen og
som ingen har målt er `prognose_uten_maaling`; en modellversjon hvis
samlede absoluttavvik over minst `domsgrunnlag_uker` målte uker er
større enn ELLER LIK basislinjens er `slaar_ikke_naiv_baseline`; og en
prognose laget uten at M-3 noensinne har profilert tenanten er
`prognose_paa_ukjent_datakvalitet`.

TO AV DE TRE LUKKES BARE HERFRA. `prognose_uten_maaling` forsvinner når
målingen kommer, `slaar_ikke_naiv_baseline` når modellen faktisk slår
basislinjen. Det tredje KAN lukkes av et menneske — «vi vet at M-3
aldri har sett på dette, vi planlegger likevel» er en legitim
beslutning — og 125/126s vakt sørger for at den lukkingen står natten
over.

Formen er `likviditetssveip.py` sin, ordrett:

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
#: INGEN HORISONT, INGEN MÅLEFRIST OG INGET DOMSGRUNNLAG HER, og det er
#: poenget: alle tre er TENANTENS og ligger i `prognosekrav`. Et
#: domsgrunnlag vi låste i en driftsfil ville vært en påstand om hvor
#: mange uker som skal til før vi tør felle dom over en modell — og det
#: er en faglig vurdering, ikke en driftsparameter.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to prognosesveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 331_907_442

#: Antall felt `m33_sveip_prognose` lover. Fire, som resten av flåten.
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
                "SELECT * FROM m33_sveip_prognose(%s)",
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
