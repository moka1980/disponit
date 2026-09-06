"""M-32 (138) — skattesveipen. REGELVERSJONEN ER PRODUKTET.

`disponit-skattesveip.timer`, én gang i døgnet, kaller
`m32_sveip_skatt(p_maks_tenanter)`.

SVEIPEN INNBERETTER INGENTING OG RETTER INGEN BEREGNING. Den sier fra
om at en stor vurdering har stått ukontrollert lenger enn tenantens
frist, om at en landpakke utløper snart, om at en pakke mangler
satser, og om at en tenant handler med et land huset ikke har en pakke
for — og der stopper den.

DET ER EN DOM, IKKE EN MANGEL:

  EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
  ROLLBACK.

En innberettet mva-oppgave er hos skattemyndigheten. En rollback gjør
den ikke usendt; den gjør bare at vi ikke lenger vet hva vi sendte.

LANDREGISTERET RØRES IKKE HERFRA. `landpakke` og `landsats` er globale
og tenantløse, og `disponit_skattesveip` har INGEN rettighet på dem —
ikke engang SELECT. Sveipen leser dem gjennom sin ene dør, som eies av
modulrollen. En skattesats er en REGEL, ikke data: kunne en nattjobb
endret den, ville regelen ikke lenger vært landets.

FIRE FUNNTYPER KAN ALDRI REISES AV DENNE SVEIPEN, og at de ikke kan er
beviset: `transaksjon_uten_jurisdiksjon` og `sats_uten_regelversjon`
(begge NOT NULL), `sats_uten_komplett_landpakke` (fremmednøkkel fra
`landsats` til `landpakke`) og `landpakke_endret_gjennom_dor`
(rettigheten finnes ikke).

DET SVEIPEN RYDDER ETTER ER TIDEN. Døra nektet da beregningen ble
gjort: uten pakke, ingen sats. Så utløp pakken, eller ingen så på den
store vurderingen. Ingen dør kunne fanget det, fordi ingen kalte noen
dør.

SVEIPEN SNAKKER IKKE UT. Denne fila importerer ingenting som kan det:
ingen `httpx`, ingen `requests`, ingen `socket`.

Formen er `hendelsessveip.py` sin, ordrett:

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
#: INGEN GRENSE OG INGEN FRIST HER, og det er poenget: både
#: `manuell_kontroll_over_ore` og `kontrollfrist_dogn` er TENANTENS og
#: ligger i `skattekrav`. Hva som er stort nok til å kontrolleres er en
#: forretningsvurdering — en beløpsgrense låst i en driftsfil ville
#: vært huset som bestemte hvilke transaksjoner kunden skal se på.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to skattesveip overlapper aldri. Tallet er modulens
#: eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 418_267_915

#: Antall felt `m32_sveip_skatt` lover. Fire, som resten av flåten.
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
                "SELECT * FROM m32_sveip_skatt(%s)", (grense,)).fetchall()
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
