"""M-45 (136) — ESG-sveipen. GRUNNLAGET ER PRODUKTET.

`disponit-esgsveip.timer`, én gang i døgnet, kaller
`m45_sveip_esg(p_maks_tenanter)`.

SVEIPEN SENDER INGEN RAPPORT, ERSTATTER INGEN ESTIMATER OG LUKKER
INGEN PERIODE. Den sier fra om at et estimat har stått lenger enn
tenantens frist uten å bli erstattet, om at en åpen periode er låst til
en standardversjon som har flyttet seg, og om at for mye av utslippet
hviler på gjetning — og der stopper den.

DET ER EN DOM, IKKE EN MANGEL. En sveip som låste om perioden til en
nyere standardversjon ville endret hvert tall i den uten at noen rørte
dem, i en rapport et tilsyn skal lese. LÅSEN ER DOMMEN, og den skal
ikke kunne endres av en nattjobb.

  EN YTRING AVGITT I HUSETS NAVN KAN IKKE TAS TILBAKE — OG DEN SOM
  LESER DEN VET IKKE AT EN MASKIN SKREV DEN.

En bærekraftsrapport leses av investorer, kunder og et tilsyn. Et
estimat lest som en måling er grønnvasking, uansett hva som var ment.

M-45s EGEN: MODULEN SENDER INGEN RAPPORT. Det finnes ingen kolonne for
«sendt» i hele modulen og ingen dør som setter en; innsendingen hører
hjemme i M-47.

FEM FUNNTYPER KAN ALDRI REISES AV DENNE SVEIPEN, og at de ikke kan er
beviset: `tall_uten_kilde`, `tall_uten_faktorversjon`,
`estimat_ikke_merket`, `paastand_uten_kilde` og
`modulen_sendte_rapport`. Alle fem er utelukket av datamodellen.

DET SVEIPEN RYDDER ETTER ER TIDEN. ET ESTIMAT ER LOV — DET ER
MIDLERTIDIGHETEN SOM GJØR DET LOVLIG. Et estimat som har stått i to år
er ikke et estimat lenger; det er et tall huset har bestemt seg for.

TO AV TRE LUKKES BARE HERFRA. `estimat_ikke_erstattet_over_frist`
forsvinner når en måling erstatter estimatet,
`standardversjon_foreldet_i_apen_periode` når perioden lukkes.
`estimatandel_over_terskel_uavklart` KAN lukkes av et menneske — «vi
vet, og det står i rapporten» er en legitim avklaring med et navn på —
og 125/126s vakt sørger for at den lukkingen står natten over.

SVEIPEN SNAKKER IKKE UT. Denne fila importerer ingenting som kan det:
ingen `httpx`, ingen `requests`, ingen `socket`.

Formen er `telefonisveip.py` sin, ordrett.

TAKET er 500 tenanter per kjøring. Det begrenser TRANSAKSJONEN, ikke
sannheten.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Maks antall tenanter sveipen tar per kjøring.
GRENSE = 500
#: INGEN TERSKEL OG INGEN FRIST HER, og det er poenget: alle tre —
#: `estimatterskel_bp`, `estimatfrist_dogn` og `kilde_gyldig_dogn` —
#: er TENANTENS og ligger i `esgkrav`. En estimatfrist låst i en
#: driftsfil ville vært en påstand om hvor lenge et gjettet tall kan
#: stå i en rapport et tilsyn leser — og et lite verksted og et
#: børsnotert konsern tåler ikke det samme.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to esgsveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 657_310_446

#: Antall felt `m45_sveip_esg` lover. Fire, som resten av flåten.
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
                "SELECT * FROM m45_sveip_esg(%s)",
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
