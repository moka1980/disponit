"""M-39 (113) — lønnssveipen for timer uten plan, avvik og overtid.

`disponit-lonnssveip.timer`, én gang i døgnet, kaller
`m39_sveip_lonnsgrunnlag(p_grense)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(113): timer ført uten en plan å måles mot, når fristen er ute, er
`time_uten_arbeidsplan`; ført tid som avviker fra planen utover
tenantens grense er `avvik_mot_plan`; tid over normaltid — per dag
ELLER per uke — er `overtid`; en dag ført på en annen kode enn planens
er `ukjent_prosjektkode`; og en tenant uten grenser er `ingen_terskel`.

SVEIPEN UTBETALER INGENTING OG PRODUSERER INGEN LØNNSFIL. Den kunne,
teknisk — den vet nøyaktig hvor mange minutter hver ansatt har ført.
Men en lønnsfil er ikke en betaling, det er en FIL: den ser harmløs ut,
den kan «bare genereres», og den er nettopp derfor farligere enn en
enkelt utbetaling. Den rammer ALLE på én gang, og den rammer noen som
har regnet med beløpet. En feil i en faktura oppdages av en kunde som
klager. En feil i en lønnsfil oppdages av noen som ikke fikk husleia.

DERFOR SKRIVER DENNE FILA INGENTING UT AV BASEN. Ingen `csv`, ingen
`open()`, ingen `pathlib` — filmodulene er ikke utelatt ved en
forglemmelse; de er invarianten `modulen_produserte_lonnsfil`, skrevet
som kode.

OVERTID ER ET FUNN, IKKE ET FLAGG. Sveipen setter ingen kolonne og går
videre; den skriver en rad noen må se på.

ALT REGNES I HELE MINUTTER (M-25s dom, 107). Ingen divisjon, ingen
prosent sveipen fant på selv.

GRENSENE ER TENANTENS, IKKE MODULENS. Normaltid per dag og uke,
avviksgrensen og fristen for en plan ligger i basen, satt gjennom en
dør. Denne fila bærer derfor ingen av dem — en konstant her ville vært
nøyaktig den fullmakten invarianten `timegrense_hardkodet` forbyr.

Formen er `adressesveip.py` sin, ordrett, og av de samme grunnene:

  * **Advisory-lås.** To sveip overlapper aldri; `hoppet_over` står PÅ
    resultatet så kalleren vet at feiltelleren skal stå urørt.
  * **Kontrakten valideres FØR commit**, og på ALLE radene — også radens
    FORM (109s retting).
  * **To sammenhengende feilede kjøringer → alarm.** En stille
    lønnssveip er overtid ingen har sett.
  * **Én JSON-linje per kjøring, exit 1 ved feil.**

TAKET er 500 tenanter per kjøring. Det begrenser TRANSAKSJONEN, ikke
sannheten.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Maks antall tenanter sveipen tar per kjøring.
GRENSE = 500
#: INGEN NORMALTID HER, og det er poenget: grensene er TENANTENS, og
#: de ligger i `lonnsterskel` i basen. En konstant i denne fila ville
#: vært nøyaktig den fullmakten invarianten `timegrense_hardkodet`
#: forbyr — «en arbeidsdag er 7,5 time» er en forretningsbeslutning, og
#: en bedrift med rotasjonsturnus har en annen.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to lonnssveip overlapper aldri. Tallet er modulens
#: eget og deles ikke med noen annen sveip — to jobber som låser på
#: samme nøkkel ville blokkert hverandre uten grunn.
ARBEIDERNOKKEL = 350_617_882


@dataclass
class Sveipresultat:
    tenanter: int = 0
    nye: int = 0
    #: Funn som alt sto der og bare ble friskmeldt. «Fem funn» og «fem
    #: funn som alt sto der i går» er ikke samme natt.
    oppdaterte: int = 0
    lukkede: int = 0
    feilet: bool = False
    alarm_utlost: bool = False
    #: En kjøring som fant arbeidernøkkelen opptatt har verken lyktes
    #: eller feilet. Skillet må stå PÅ resultatet, ellers kan ikke
    #: kalleren vite at feiltelleren skal stå urørt (artefaktrydding,
    #: Codex P2).
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
        # persistert feiltellingen 0 — altså slettet en alt opptelt feil
        # ved hver overlappende aktivering, og alarmen etter to
        # sammenhengende feil ville aldri nådd frem.
        res.hoppet_over = True
        return res
    try:
        # KONTRAKTEN VALIDERES FØR COMMIT, og på ALLE radene.
        #
        # Døren returnerer NØYAKTIG ÉN rad. Ingen rad er ikke «null
        # funn» — det er en dør som ikke oppførte seg som kontrakten, og
        # da skal kjøringen si feilet framfor å rapportere nuller den
        # ikke har målt. FLERE rader er den samme feilen fra motsatt
        # kant, og `fetchone()` ville tiet om den.
        #
        # REKKEFØLGEN ER DOMMEN: bare en validert kontrakt committes.
        try:
            rader = conn.execute(
                "SELECT * FROM m39_sveip_lonnsgrunnlag(%s)",
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
        # …OG RADENS FORM ER EN DEL AV KONTRAKTEN. Fire felt som lar
        # seg lese som heltall. Konverteringen gjøres FØR commit fordi det
        # er her doktrinen står: en rad som ikke er kontrakten skal
        # rulle tilbake, ikke bli stående mens kjøringen rapporterer
        # feilet (CodeRabbit, 109).
        # `[:4]` og ikke hele raden: sveipen LESER fire felt, og en dør
        # som en dag returnerer et femte skal ikke gjøre en gyldig
        # kjøring til en feilet (#358s lærdom — den delte
        # kontraktporten mater en SUPERSETT-rad).
        try:
            verdier = tuple(int(v) for v in rader[0][:4])
            if len(verdier) != 4:
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
        # Opplåsingen er BEST EFFORT. Er tilkoblingen borte, feiler også
        # denne — og et unntak herfra ville erstattet resultatet kalleren
        # skal rapportere og persistere telleren fra. Låsen er
        # sesjonsscopet: en død sesjon slipper den uansett.
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
