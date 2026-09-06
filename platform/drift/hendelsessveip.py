"""M-29 (137) — hendelsessveipen. KORRELASJONEN ER PRODUKTET.

`disponit-hendelsessveip.timer`, én gang i døgnet, kaller
`m29_sveip_hendelse(p_maks_tenanter)`.

SVEIPEN ISOLERER INGEN KONTO, ROTERER INGEN HEMMELIGHET OG KJØRER
INGEN KOMMANDO. Den sier fra om at en hendelse har stått åpen lenger
enn tenantens frist, om at en hendelse OVER TERSKEL står uten et
eneste inngrepsforslag, om at en playbook har mistet stegene sine, og
om at en regel har stått i tre måneder uten et treff — og der stopper
den.

DET ER EN DOM, IKKE EN MANGEL:

  EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
  ROLLBACK.

Klynge 9s ytring kunne ikke tas tilbake fordi noen hadde LEST den.
Denne trenger ingen leser: kontoen er stengt, hemmeligheten er rullet,
og tokenet den gamle klienten holdt er dødt. Databasen kan rulles
tilbake til sekundet før — klienten er fortsatt logget ut.

DENNE SVEIPEN ER FLÅTENS FARLIGSTE Å GI FOR MYE. Fullmaktsmålene
ligger allerede i basen: `api_tokener`, `modultoken`, `brukersesjon`,
`tenant_pseudonymnokkel` og `brukeridentitet`. Sveiperollen
`disponit_hendelsessveip` har INGEN rettighet på noen av dem — ikke
engang SELECT — og den når ÉN funksjon i hele modulen.

FIRE FUNNTYPER KAN ALDRI REISES AV DENNE SVEIPEN, og at de ikke kan er
beviset: `inngrep_uten_playbook` (`playbook_id` NOT NULL med
fremmednøkkel, og `inngrepsforslag` har ingen `utfort_ts` i det hele
tatt), `fri_kommando_kjort` (`playbooksteg.stegtype` er et lukket sett
uten fritekstfølge), `hendelse_uten_score` og `score_uten_regel`
(begge NOT NULL).

DET SVEIPEN RYDDER ETTER ER TIDEN. Døra nektet da regelen ble
registrert, da playbooken ble skrevet og da korrelasjonen ble gjort.
Så gikk det døgn, og ingen så på hendelsen. Ingen dør kunne fanget
det, fordi ingen kalte noen dør.

SVEIPEN SNAKKER IKKE UT. Denne fila importerer ingenting som kan det:
ingen `httpx`, ingen `requests`, ingen `socket`.

Formen er `telefonisveip.py` sin, ordrett:

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
#: INGEN TERSKEL OG INGEN FRIST HER, og det er poenget: alle fire —
#: `korrelasjonsvindu_min`, `alvorsterskel`, `apen_hendelse_frist_dogn`
#: og `signaltak` — er TENANTENS og ligger i `hendelseskrav`. En
#: alvorsterskel låst i en driftsfil ville vært huset som bestemte hvor
#: mange falske alarmer kunden tåler, og en tannlegeklinikk og en bank
#: tåler ikke det samme.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to hendelsessveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 297_413_566

#: Antall felt `m29_sveip_hendelse` lover. Fire, som resten av flåten.
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
                "SELECT * FROM m29_sveip_hendelse(%s)",
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
