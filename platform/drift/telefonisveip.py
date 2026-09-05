"""M-43 (135) — telefonisveipen. IDENTIFIKASJONEN ER PRODUKTET.

`disponit-telefonisveip.timer`, én gang i døgnet, kaller
`m43_sveip_telefoni(p_maks_tenanter)`.

SVEIPEN RINGER INGEN, LUKKER INGEN ESKALERING OG AVSLUTTER INGEN
SAMTALE. Den sier fra om at en samtale har stått åpen lenger enn
tenantens tak, om at en eskalering ingen tok har stått over fristen,
og om at en linje maskinen var usikker på verken er rettet eller
avklart — og der stopper den.

DET ER EN DOM, IKKE EN MANGEL. En sveip som lukket eskaleringen selv
ville sagt at saken var håndtert fordi ingen gjorde noe, og det er
nøyaktig det klyngens dom advarer mot:

  EN YTRING AVGITT I HUSETS NAVN KAN IKKE TAS TILBAKE — OG DEN SOM
  LESER DEN VET IKKE AT EN MASKIN SKREV DEN.

HER ER DEN BOKSTAVELIG: den andre parten HØRER en stemme, og en stemme
høres ikke ut som en maskin lenger.

M-43s EGEN: MODULEN INNGÅR INGEN AVTALE OG GIR INGEN ØKONOMISKE
LØFTER. Det finnes ingen kolonne for et beløp og ingen dør som binder
noe — ikke en avslått vei, ikke en vei bak en bryter.

FIRE FUNNTYPER KAN ALDRI REISES AV DENNE SVEIPEN, og at de ikke kan er
beviset: `opptak_uten_hjemmel` og `opptak_uten_varsling` (begge NOT
NULL med fremmednøkkel og rekkefølgen håndhevet i basen),
`agenten_skjulte_at_den_er_automatisert` (`identifisert_ts` er NOT
NULL, og ingen linje kan dateres før den) og `eskalering_uten_regel`
(`regel_id` er NOT NULL mot tenantens egen regel).

DET SVEIPEN RYDDER ETTER ER TIDEN. Døra nektet da samtalen startet, da
opptaket startet, og da hver linje ble skrevet. Så gikk det timer, og
integrasjonen hang — eller det gikk døgn, og ingen tok eskaleringen.
Ingen dør kunne fanget det, fordi ingen kalte noen dør.

TO AV TRE LUKKES BARE HERFRA. `samtale_uten_avslutning` forsvinner når
samtalen avsluttes, `eskalering_over_frist` når den lukkes.
`ubekreftet_linje_uavklart` KAN lukkes av et menneske — «vi har hørt
opptaket, det stemmer» er en legitim avklaring med et navn på — og
125/126s vakt sørger for at den lukkingen står natten over.

SVEIPEN SNAKKER IKKE UT. Denne fila importerer ingenting som kan det:
ingen `httpx`, ingen `requests`, ingen `socket`.

Formen er `innholdssveip.py` sin, ordrett:

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
#: `sikkerhetsterskel_bp`, `identifikasjonsfrist_sek`,
#: `eskaleringsfrist_dogn` og `samtaletak_timer` — er TENANTENS og
#: ligger i `telefonikrav`. En identifikasjonsfrist låst i en driftsfil
#: ville vært en påstand om hvor lenge det er greit at noen snakker med
#: en maskin uten å vite det — og en bestilling av pizza og en samtale
#: om oppsigelse tåler ikke det samme.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to telefonisveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 651_884_302

#: Antall felt `m43_sveip_telefoni` lover. Fire, som resten av flåten.
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
                "SELECT * FROM m43_sveip_telefoni(%s)",
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
