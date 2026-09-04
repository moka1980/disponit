"""M-55 (120) — merkevaresveipen for funn ingen har sett på.

`disponit-merkevaresveip.timer`, én gang i døgnet, kaller
`m55_sveip_merkevare(p_grense)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(120): et åpent funn uten vurdering er `funn_uten_vurdering`; en
forveksling over tenantens egen terskel som ingen har henvist er
`forveksling_ikke_henvist`; en vurdering gjort under en annen terskel
enn den som gjelder nå er `vurdering_med_utdatert_terskel`; et åpent
funn eldre enn tenantens frist er `funn_eldre_enn_frist`; et aktivt
merke uten et eneste funn er `merkevare_uten_funn`; og en tenant uten
terskler er `ingen_terskler`.

SVEIPEN VURDERER IKKE, OG DET ER MODULENS VIKTIGSTE FRAVÆR.

En vurdering er inngangen til en ANKLAGE MOT EN NAVNGITT PART. Lot vi
sveipen regne selv, ville forvekslingsvurderinger oppstått om natten
uten at noen ba om det — og spesifikasjonens vakt sier at modulen
dokumenterer og rapporterer; enhver reaksjon besluttes av menneske.
`m55_vurder_funn` kalles av et menneske gjennom flaten, aldri herfra.

OG SVEIPEN HENVISER IKKE. Henvisningen til M-37s unntakskø er modulens
eneste utgang, og en automatisk henvisning ville vært en sak reist av
en tidsplan. Sveipen MELDER at et funn ikke er henvist; den gjør det
ikke selv.

SVEIPEN SENDER INGENTING. 120 har ingen mottaker, ingen kravtekst og
ingen utboks — `modulen_sendte_krav` er ikke en regel vi håndhever, det
er en handling som ikke finnes.

DEN ENE TINGEN SVEIPEN LUKKER er varsler hvis tilstand er borte, og
`forveksling_ikke_henvist` er med der — men BARE der. Døra
`m55_lukk_varsel` nekter et menneske å lukke nettopp det varselet.
Forskjellen er hele poenget: sveipen lukker det som ER løst, fordi
funnet ble henvist eller lukket. Et menneske kan ikke lukke det som
ikke er løst.

DERFOR IMPORTERER DENNE FILA INGENTING SOM KAN SNAKKE UT. Ingen
`httpx`, ingen `requests`, ingen `socket`. Bevaringskopiene registreres
av den som tok dem — porten `modulen_hentet_eksternt`. Et
overvåkingsoppslag mot tredjeparts annonseplattformer og domeneregistre
hører hjemme i oppdragskontraktens `ekstern_lesing` med
målautorisasjon, ikke i en modulfil.

POLICYEN ER TENANTENS. Forvekslingsterskelen, funnfristen og
henvisningsfristen ligger i `merkevarekrav`. En konstant her ville vært
nøyaktig den fullmakten invarianten `forvekslingsterskel_hardkodet`
forbyr — og hvor likt noe må være før det er forveksling er en
juridisk vurdering, ikke et tall en modul kan velge.

Formen er `adressesveip.py` sin, ordrett:

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
#: INGEN TERSKEL HER, og det er poenget: policyen er TENANTENS, og den
#: ligger i `merkevarekrav` i basen.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to merkevaresveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 804_113_267

#: Antall felt `m55_sveip_merkevare` lover. Fire, som M-46, M-49 og
#: M-51: modulen har ingen rad å rydde tilsvarende M-48s forlatte
#: reservasjoner.
KONTRAKTFELT = 4


@dataclass
class Sveipresultat:
    tenanter: int = 0
    nye: int = 0
    #: Varsler som alt sto der og bare ble sett på nytt.
    oppdaterte: int = 0
    lukkede: int = 0
    #: INGEN `forlatte`: M-48 rydder forlatte reservasjoner, M-55 har
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
                "SELECT * FROM m55_sveip_merkevare(%s)",
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
