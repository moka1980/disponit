"""M-9 (095) — utløpssveipen for begrepsregisteret.

`disponit-begrepssveip.timer`, én gang i døgnet, kaller
`m9_sveip_utlopte(p_varselvindu_dogn)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(095): et gjeldende begrep forbi `gyldig_til` er et `utlopt`-funn, et
gjeldende begrep innenfor varselvinduet er et `utloper_snart`-funn, og
ETT funn per (begrep, funntype) holdes åpent og oppdateres med
`sist_sett_sveip`. Timeren bestemmer ikke hva som er utløpt — den
kaller funksjonen som vet det.

Formen er `artefaktrydding.py` sin, ordrett, og av de samme grunnene:

  * **Advisory-lås.** To sveip overlapper aldri. En kjøring som fant
    nøkkelen opptatt har verken lyktes eller feilet — den gjorde
    ingenting, og `hoppet_over` står PÅ resultatet så kalleren vet at
    feiltelleren skal stå urørt.
  * **To sammenhengende feilede kjøringer → alarm.** En stille
    utløpssveip er en ordliste som eldes uten at noen ser det — og det
    er nøyaktig tilstanden modulen finnes for å gjøre synlig.
  * **Én JSON-linje per kjøring, exit 1 ved feil.** Sveipen som ikke
    kunne måle rapporterer FUNN, aldri null.

VARSELVINDUET er 30 døgn. Det er ikke et magisk tall, men det korteste
vinduet som er lengre enn en ferie: en begrepseier som er borte tre uker
skal komme tilbake til et varsel, ikke til et utløpt begrep. Kadensen er
daglig, og de to hører sammen — vinduet er 30 × kadensen, samme forhold
som resten av huset bruker mellom en sveip og terskelen den varsler på.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Hvor mange døgn før `gyldig_til` et begrep blir et `utloper_snart`-funn.
VARSELVINDU_DOGN = 30
#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to begrepssveip overlapper aldri.
ARBEIDERNOKKEL = 915_774_209


@dataclass
class Sveipresultat:
    tenanter: int = 0
    nye: int = 0
    oppdaterte: int = 0
    lukkede: int = 0
    feilet: bool = False
    alarm_utlost: bool = False
    #: En kjøring som fant arbeidernøkkelen opptatt har verken lyktes
    #: eller feilet. Skillet må stå PÅ resultatet, ellers kan ikke
    #: kalleren vite at feiltelleren skal stå urørt (artefaktrydding,
    #: Codex P2).
    hoppet_over: bool = False


def kjor(conn, *, varselvindu_dogn: int = VARSELVINDU_DOGN,
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
        # sett ut som en kjøring som fant null utløpte begreper, og
        # kalleren ville persistert feiltellingen 0 — altså slettet en
        # alt opptelt feil ved hver overlappende aktivering, og alarmen
        # etter to sammenhengende feil ville aldri nådd frem.
        res.hoppet_over = True
        return res
    try:
        try:
            rad = conn.execute("SELECT * FROM m9_sveip_utlopte(%s)",
                               (varselvindu_dogn,)).fetchone()
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
        res.tenanter, res.nye, res.oppdaterte, res.lukkede = (
            int(rad[0]), int(rad[1]), int(rad[2]), int(rad[3]))
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
