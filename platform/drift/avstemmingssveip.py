"""M-13 (101) — avstemmingssveipen for bank- og bilagsregisteret.

`disponit-avstemmingssveip.timer`, én gang i døgnet, kaller
`m13_sveip_avstemming(p_grense, p_dogn_grense)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(101): en bankpost uten en ikke-opphevet matchrad, eldre enn
aldersgrensen, er et `uavstemt_post_over_grense`-funn; et forfalt bilag
uten en eneste krone dekket er et `forfalt_bilag_uten_dekning`-funn; et
forfalt bilag med delvis dekning er et `delvis_dekket_bilag`-funn — og
ETT funn per (objekt, funntype) holdes åpent og oppdateres med
`sist_sett_sveip`. Timeren bestemmer ikke hva som er uavstemt; den
kaller funksjonen som vet det.

TO FUNNTYPER PÅ FORFALTE BILAG, ikke én, og det er en dom: et bilag der
ingenting er betalt kan være en faktura ingen har sendt penger for,
mens et bilag med delvis dekning nesten alltid er en avstemming som
mangler sin siste post. De fører til to forskjellige handlinger, og en
felles funntype ville skjult hvilken.

Formen er `compliancesveip.py` sin, ordrett, og av de samme grunnene:

  * **Advisory-lås.** To sveip overlapper aldri. En kjøring som fant
    nøkkelen opptatt har verken lyktes eller feilet — den gjorde
    ingenting, og `hoppet_over` står PÅ resultatet så kalleren vet at
    feiltelleren skal stå urørt.
  * **To sammenhengende feilede kjøringer → alarm.** En stille
    avstemmingssveip er et register som eldes uten at noen ser det — og
    det er nøyaktig tilstanden modulen finnes for å gjøre synlig. Et
    register der ingenting er uavstemt fordi ingen målte, er ikke et
    grønt register.
  * **Én JSON-linje per kjøring, exit 1 ved feil.** En jobb som ikke
    kunne måle rapporterer FUNN, aldri null.

SVEIPEN BOKFØRER INGENTING. Den kaller én lesende-og-funnskrivende
definer og gjør ikke ett eneste kall som rører et regnskap. Det er
v1-dommen, og den er en egenskap ved denne filen: ingen HTTP-klient,
ingen hovedbokskobling, ingen utgående vei.

`avkortet` ER OGSÅ EN RAPPORTERT TILSTAND, ikke en stille sannhet. Traff
sveipen taket sitt hos én eller flere tenanter, står tallet i linjen.
Det er ikke en feilet kjøring — funnene er idempotente, så neste kjøring
tar igjen resten — men det er heller ikke «ferdig», og forskjellen skal
være lesbar i journalen uten å måtte telles i basen etterpå.

TAKET er 500 nye funn per tenant per kjøring. Det begrenser
TRANSAKSJONEN, ikke sannheten. Et avstemmingsregister med mer enn 500
nye funn på én natt er dessuten i seg selv en opplysning: da er det ikke
enkeltposter som står uavstemt, det er hele avstemmingen.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Maks antall NYE funn sveipen reiser per tenant per kjøring.
GRENSE = 500
#: Alderen en uavstemt bankpost må passere før den blir et funn. Tretti
#: døgn er én måned kontoutskrift. Tallet står HER og ikke i dørens kropp
#: nettopp fordi det er en driftsvurdering og ikke en invariant: en tenant
#: som avstemmer ukentlig vil ha noe annet, og den dagen dette blir en
#: policyverdi (M-1), er endringen ett kall — ikke en migrasjon.
DOGN_GRENSE = 30
#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to avstemmingssveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip — to jobber som
#: låser på samme nøkkel ville blokkert hverandre uten grunn.
ARBEIDERNOKKEL = 731_205_918


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
         dogn_grense: int = DOGN_GRENSE,
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
        try:
            rad = conn.execute(
                "SELECT * FROM m13_sveip_avstemming(%s, %s)",
                (grense, dogn_grense)).fetchone()
            conn.commit()
        except Exception:
            _rull_tilbake(conn)
            res.feilet = True
            res.alarm_utlost = tidligere_feil + 1 >= ALARM_ETTER_FEIL
            return res
        # Døren returnerer nøyaktig én rad. Ingen rad er ikke «null
        # funn» — det er en dør som ikke oppførte seg som kontrakten, og
        # da skal kjøringen si feilet framfor å rapportere nuller den
        # ikke har målt («en jobb som ikke kunne måle rapporterer FUNN,
        # aldri null»).
        if rad is None:
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
