"""M-17 (102) — henvendelsessveipen for kundeserviceregisteret.

`disponit-henvendelsessveip.timer`, én gang i døgnet, kaller
`m17_sveip_henvendelser(p_grense, p_dogn_uklassifisert, p_dogn_ubesvart)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(102): en åpen henvendelse uten klassifisering, eldre enn grensen, er et
`uklassifisert_over_grense`-funn; en klassifisert som SIER at svar kreves
og som ingen har svart på, er `ubesvart_over_grense`; og en klassifisert
som `mistenkelig` uten at den er satt i sikkerhetskøen er
`mistenkelig_uten_behandling`. Timeren bestemmer ikke hva som er
oversett; den kaller funksjonen som vet det.

KRAVET OM SVAR ER KLASSIFISERINGENS, IKKE SVEIPENS. En `til_info`-
henvendelse blir aldri et `ubesvart`-funn — den ba ikke om svar. Uten
det leddet ville funnlisten fylt seg med nyhetsbrev, og de virkelige
ubesvarte druknet.

SVEIPEN LESER INGEN KUNDETEKST. Den kaller én definer og får fem tall
tilbake. Henvendelsenes innhold er kryptert med tenantens DEK, og DEK-en
finnes bare i API-lagets minne — sveipen kunne ikke åpnet en tekst om
den ville.

Formen er `avstemmingssveip.py` sin, ordrett, og av de samme grunnene:

  * **Advisory-lås.** To sveip overlapper aldri. En kjøring som fant
    nøkkelen opptatt har verken lyktes eller feilet — `hoppet_over` står
    PÅ resultatet så kalleren vet at feiltelleren skal stå urørt.
  * **To sammenhengende feilede kjøringer → alarm.** En stille
    henvendelsessveip er en kø som eldes uten at noen ser det.
  * **Én JSON-linje per kjøring, exit 1 ved feil.** En jobb som ikke
    kunne måle rapporterer FUNN, aldri null.

TAKET er 500 nye funn per tenant per kjøring. Det begrenser
TRANSAKSJONEN, ikke sannheten. En kundeservicekø med mer enn 500 nye
funn på én natt er dessuten i seg selv en opplysning.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Maks antall NYE funn sveipen reiser per tenant per kjøring.
GRENSE = 500
#: Døgnene en åpen henvendelse kan stå uklassifisert og ubesvart før den
#: blir et funn. To og fem er alminnelige servicemål. Tallene står HER og
#: ikke i dørens kropp nettopp fordi de er driftsvurderinger og ikke
#: invarianter: en tenant med en annen SLA vil ha noe annet, og den dagen
#: dette blir policyverdier (M-1), er endringen ett kall — ikke en
#: migrasjon.
DOGN_UKLASSIFISERT = 2
DOGN_UBESVART = 5
#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to avstemmingssveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip — to jobber som
#: låser på samme nøkkel ville blokkert hverandre uten grunn.
ARBEIDERNOKKEL = 448_913_607


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
         dogn_uklassifisert: int = DOGN_UKLASSIFISERT,
         dogn_ubesvart: int = DOGN_UBESVART,
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
                "SELECT * FROM m17_sveip_henvendelser(%s, %s, %s)",
                (grense, dogn_uklassifisert, dogn_ubesvart)).fetchone()
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
