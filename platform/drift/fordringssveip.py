"""M-23 (104) — fordringssveipen for kundefordringene.

`disponit-fordringssveip.timer`, én gang i døgnet, kaller
`m23_sveip_fordringer(p_grense)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(104): en åpen fordring som har passert et HØYERE purretrinn enn den står
på er et `trinn_forfalt`-funn; en forfalt fordring i en tenant uten
purreplan er `ingen_purreplan`; og en fordring forfalt mer enn 90 døgn
og fortsatt på trinn 0 er `forfalt_uten_trinn`.

SVEIPEN FLYTTER INGEN TRINN. Den kunne — den vet hvilke fordringer som
er modne — men et trinn er en ESKALERING MOT EN KUNDE. En purring sendt
for tidlig, til feil kunde, eller på et krav som alt er betalt, kan ikke
trekkes tilbake, og en jobb som eskalerer om natten er nøyaktig den
fullmakten v1 ikke gir seg selv. Den skriver funn; et menneske flytter
trinnet.

GRENSENE ER TENANTENS, IKKE MODULENS. Purretrinnene ligger i basen, satt
gjennom en dør av tenanten selv. Denne fila bærer derfor ingen
døgngrense — en konstant her ville vært nøyaktig den fullmakten
invarianten `purretrinn_hardkodet` forbyr.

Formen er `onboardingsveip.py` sin, ordrett, og av de samme grunnene:

  * **Advisory-lås.** To sveip overlapper aldri; `hoppet_over` står PÅ
    resultatet så kalleren vet at feiltelleren skal stå urørt.
  * **Kontrakten valideres FØR commit**, og på ALLE radene: nøyaktig én
    rad, ellers rulles det tilbake og kjøringen sier feilet.
  * **To sammenhengende feilede kjøringer → alarm.** En stille
    fordringssveip er utestående som eldes uten at noen ser det — og for
    penger er det nettopp tiden som er skaden.
  * **Én JSON-linje per kjøring, exit 1 ved feil.**

TAKET er 500 nye funn per tenant per kjøring. Det begrenser
TRANSAKSJONEN, ikke sannheten.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Maks antall NYE funn sveipen reiser per tenant per kjøring.
GRENSE = 500
#: INGEN DØGNGRENSE HER, og det er poenget: grensene er TENANTENS, og
#: de ligger i `purretrinn` i basen. En konstant i denne fila ville vært
#: nøyaktig den fullmakten invarianten `purretrinn_hardkodet` forbyr —
#: «etter 14 døgn purrer vi» er en forretningsbeslutning, ikke en
#: driftsvurdering.
#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to avstemmingssveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip — to jobber som
#: låser på samme nøkkel ville blokkert hverandre uten grunn.
ARBEIDERNOKKEL = 884_310_562


@dataclass
class Sveipresultat:
    tenanter: int = 0
    nye: int = 0
    oppdaterte: int = 0
    lukkede: int = 0
    #: Antall tenanter der sveipen traff taket sitt. Ikke en feil — men
    #: heller ikke «ferdig», og den forskjellen skal stå i linjen.
    avkortet: int = 0
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
        # sett ut som en kjøring som fant null forbigåtte kontroller, og
        # kalleren ville persistert feiltellingen 0 — altså slettet en
        # alt opptelt feil ved hver overlappende aktivering, og alarmen
        # etter to sammenhengende feil ville aldri nådd frem.
        res.hoppet_over = True
        return res
    try:
        # KONTRAKTEN VALIDERES FØR COMMIT, og på ALLE radene.
        #
        # Døren returnerer NØYAKTIG ÉN rad. Ingen rad er ikke «null
        # funn» — det er en dør som ikke oppførte seg som kontrakten, og
        # da skal kjøringen si feilet framfor å rapportere nuller den
        # ikke har målt («en jobb som ikke kunne måle rapporterer FUNN,
        # aldri null»). FLERE rader er den samme feilen fra motsatt
        # kant, og `fetchone()` ville tiet om den.
        #
        # REKKEFØLGEN ER DOMMEN: den forrige formen committet FØRST og
        # oppdaget så at raden manglet — altså en transaksjon som ble
        # stående mens kjøringen rapporterte feilet. Nå rulles den
        # tilbake, og bare en validert kontrakt committes.
        try:
            rader = conn.execute(
                "SELECT * FROM m23_sveip_fordringer(%s)",
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
        rad = rader[0]
        try:
            conn.commit()
        except Exception:
            _rull_tilbake(conn)
            res.feilet = True
            res.alarm_utlost = tidligere_feil + 1 >= ALARM_ETTER_FEIL
            return res
        (res.tenanter, res.nye, res.oppdaterte, res.lukkede,
         res.avkortet) = (int(rad[0]), int(rad[1]), int(rad[2]),
                          int(rad[3]), int(rad[4]))
        return res
    finally:
        # Opplåsingen er BEST EFFORT. Er tilkoblingen borte, feiler også
        # denne — og et unntak herfra ville erstattet resultatet kalleren
        # skal rapportere og persistere telleren fra. Låsen er
        # sesjonsscopet: en død sesjon slipper den uansett.
        try:
            conn.execute("SELECT pg_advisory_unlock(%s)", (ARBEIDERNOKKEL,))
            conn.commit()
        except Exception:
            pass


def _rull_tilbake(conn) -> None:
    """Rollback som aldri kaster. En død tilkobling kan ikke rulles tilbake."""
    try:
        conn.rollback()
    except Exception:
        pass
