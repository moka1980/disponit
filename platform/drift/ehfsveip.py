"""M-54 (121) — EHF-sveipen for regler som er gått ut.

`disponit-ehfsveip.timer`, én gang i døgnet, kaller
`m54_sveip_ehf(p_grense)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(121): et utløpt regelsett UTEN en gyldig etterfølger er
`regelsett_utlopt`; et som går ut innen tenantens varselvindu er
`regelsett_utloper_snart`; en dom felt under et sett som siden har
gått ut er `validering_mot_utlopt_regelsett`; et dokument ingen har
validert innen fristen er `dokument_uten_validering`; en formfeil uten
klargjort retting er `avvik_uten_retting`; en retting som ikke er
merket klar er `retting_ikke_klar`; og en tenant uten terskler er
`ingen_krav`.

SVEIPEN VALIDERER IKKE, OG DET ER MODULENS VIKTIGSTE FRAVÆR.

En automatisk revalidering ville felt nye dommer om natten uten at
noen ba om det — og en dom er inngangen til en RETTING AV EN KUNDES
FAKTURA. En faktura er et betalingskrav, og en rettet faktura som gikk
ut uten at noen så på rettingen er et dobbelt krav.

OG SVEIPEN RETTER IKKE, OG MERKER INGENTING KLART. «Klar til
signering» er en tilstand et menneske setter.

DEN ENE TINGEN SVEIPEN LUKKER er funn hvis tilstand er borte, og
`validering_mot_utlopt_regelsett` er med der — men BARE der. Døra
`m54_lukk_funn` nekter et menneske å lukke nettopp det funnet.
Forskjellen er hele poenget: sveipen lukker det som ER løst, fordi
dokumentet er validert på nytt mot et gyldig sett. Et menneske kan
ikke lukke det som ikke er løst.

SVEIPEN HENTER HELLER INGEN REGELSETT. Standarden er myndighetens, og
en modul som lastet ned den nyeste versjonen selv ville tatt ansvaret
for at NØYAKTIG den er den gjeldende. Regelsettet registreres av et
menneske som har lest den — porten `modulen_hentet_eksternt` finnes
ikke i `m54-v1`, men fraværet av utgående import står likevel her,
fordi grunnen er den samme.

DERFOR IMPORTERER DENNE FILA INGENTING SOM KAN SNAKKE UT. Ingen
`httpx`, ingen `requests`, ingen `socket`.

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
#: INGEN REGEL HER, og det er poenget dobbelt opp: reglene er
#: MYNDIGHETENS og ligger i `ehfregelsett`/`ehfregel`, og tersklene for
#: NÅR noe blir et funn er TENANTENS og ligger i `ehfkrav`. En konstant
#: her ville vært en modul som mente noe om en standard den ikke eier.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to ehfsveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 512_907_331

#: Antall felt `m54_sveip_ehf` lover. Fire, som M-46, M-49, M-51 og
#: M-55: modulen har ingen rad å rydde tilsvarende M-48s forlatte
#: reservasjoner.
KONTRAKTFELT = 4


@dataclass
class Sveipresultat:
    tenanter: int = 0
    nye: int = 0
    #: Varsler som alt sto der og bare ble sett på nytt.
    oppdaterte: int = 0
    lukkede: int = 0
    #: INGEN `forlatte`: M-48 rydder forlatte reservasjoner, M-54 har
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
                "SELECT * FROM m54_sveip_ehf(%s)",
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
