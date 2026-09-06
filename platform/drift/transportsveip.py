"""M-28 (139) — transportsveipen. PLANEN ER PRODUKTET.

`disponit-transportsveip.timer`, én gang i døgnet, kaller
`m28_sveip_transport(p_maks_tenanter)`.

SVEIPEN BESTILLER INGEN TRANSPORT OG OMBOOKER INGENTING. Den sier fra
om at en plan har stått åpen lenger enn tenantens frist, om at et målt
kolli aldri fikk en plan, om at et tungt kolli har en plan ingen har
sett på, og om at det finnes planer til et land som ikke lenger har en
landpakke — og der stopper den.

DET ER EN DOM, IKKE EN MANGEL:

  EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
  ROLLBACK.

Bilen kjører uansett hva basen sier. En booking som ble rullet
tilbake er fortsatt en bil på veien, en pakke i en terminal og en
faktura fra en transportør.

LANDREGISTERET ER M-32s, OG DET RØRES IKKE HERFRA. `landpakke` er
global og tenantløs, og `disponit_transportsveip` har INGEN rettighet
på den — ikke engang SELECT. Sveipen leser den gjennom sin ene dør,
som eies av modulrollen. Landreglene er landets: kunne en nattjobb
endret dem, ville «huset har lest reglene» vært en påstand om oss
selv.

FIRE FUNNTYPER KAN ALDRI REISES AV DENNE SVEIPEN, og at de ikke kan er
beviset: `kolli_bestilt_to_ganger` (partiell unik indeks, og ingenting
bestilles), `fareklasse_utledet_av_maskin`
(`fareklasse_oppgitt_av` NOT NULL), `farlig_gods_uten_landregel`
(`landpakke_regelversjon` NOT NULL med fremmednøkkel til 138) og
`forslag_uten_validert_adresse` (døra krever `utfall = 'godkjent'`).

DET SVEIPEN RYDDER ETTER ER TIDEN. Døra nektet ikke da planen ble
laget — adressen var godkjent og landpakken gjaldt. Så gikk det døgn,
og ingen gjorde noe med planen; eller pakken utløp. Ingen dør kunne
fanget det, fordi ingen kalte noen dør.

SVEIPEN SNAKKER IKKE UT. Denne fila importerer ingenting som kan det:
ingen `httpx`, ingen `requests`, ingen `socket`.

Formen er `skattesveip.py` sin, ordrett:

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
#: `manuell_kontroll_over_gram` og `forslagsfrist_dogn` er TENANTENS og
#: ligger i `transportkrav`. Hva som er tungt nok til å kontrolleres er
#: en forretningsvurdering — en vektgrense låst i en driftsfil ville
#: vært huset som bestemte hvilke pakker kunden skal se på.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to transportsveip overlapper aldri. Tallet er modulens
#: eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 733_509_184

#: Antall felt `m28_sveip_transport` lover. Fire, som resten av flåten.
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
                "SELECT * FROM m28_sveip_transport(%s)", (grense,)).fetchall()
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
