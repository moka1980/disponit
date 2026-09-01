"""M-30 (099) — fristsveipen over forespørselsregisteret.

`disponit-personvernsveip.timer`, én gang i døgnet, kaller
`m30_sveip_frister(p_grense)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(099): en åpen sak forbi sin gjeldende frist er et `frist_oversittet`-
funn, en åpen sak innenfor varselvinduet er et `frist_naermer_seg`-funn,
en åpen sak uten et eneste av M-4s lagre er et `sak_uten_lagre`-funn, og
ETT funn per (sak, funntype) holdes åpent og oppdateres med
`sist_sett_sveip`. Sveipen bestemmer ikke hva som er oversittet — den
kaller funksjonen som vet det.

DEN VIKTIGSTE SETNINGEN I DENNE FILA ER DEN SOM IKKE STÅR HER: sveipen
SLETTER INGENTING, og den setter aldri en sakstatus. Sletting eies av
M-4s retensjonsregnskap (093) og de seks reaperne som kjører; en ANDRE
slettevei ved siden av dem er nøyaktig det M-4 ble bygget for å hindre.
Og en frist som passerer lukker ingen sak: en oversittet
innsynsforespørsel er et LOVBRUDD, ikke en forsinkelse, og den skal bli
et funn noen ser — ikke en rad som stille skifter status til «ferdig».

Formen er `begrepssveip.py` sin, ordrett — som igjen er
`artefaktrydding.py` sin — og av de samme grunnene:

  * **Advisory-lås.** To sveip overlapper aldri. En kjøring som fant
    nøkkelen opptatt har verken lyktes eller feilet — den gjorde
    ingenting, og `hoppet_over` står PÅ resultatet så kalleren vet at
    feiltelleren skal stå urørt.
  * **To sammenhengende feilede kjøringer → alarm.** En stille
    fristsveip er et register som eldes uten at noen ser det — og en
    oversittet frist ingen fikk vite om er den ene feilen denne modulen
    finnes for å gjøre umulig.
  * **Én JSON-linje per kjøring, exit 1 ved feil.** Sveipen som ikke
    kunne måle rapporterer FUNN, aldri null.

VARSELVINDUET REISER ET FUNN, IKKE EN E-POST. `frist_naermer_seg` er
synlig på flaten og gjennom `m30_apne_funn`; ingenting her køer et
varsel. Begrunnelsen står i 099s hodekommentar og skal leses der — kort
sagt: grensen `m30-v1` ble registrert før koden og har ingen invariant
om varselidempotens, og en varslingsvei uten den er en vei å sende det
samme varselet hver kadens til folk slutter å lese dem. Prisen er ærlig:
den som eier saken må lese flaten.

VARSELVINDUET ER 7 DØGN, og det er kortere enn M-9s 30 med vilje.
Fristen her er én måned totalt (art. 12 nr. 3), ikke et år: et
30-døgnsvindu ville reist funnet i samme øyeblikk saken ble registrert,
og et funn som alltid står er et funn ingen leser. Sju døgn er den siste
uka — tidsnok til å gjøre noe, sent nok til at det betyr noe. Kadensen
er daglig, og de to hører sammen.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Hvor mange døgn før den gjeldende fristen en åpen sak blir et
#: `frist_naermer_seg`-funn. Se modulens hodekommentar for hvorfor
#: tallet er 7 og ikke M-9s 30.
VARSELVINDU_DOGN = 7
#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to personvernsveip overlapper aldri. Tallet følger
#: husets familie (`915_774_2xx`).
ARBEIDERNOKKEL = 915_774_230


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
        # sett ut som en kjøring som fant null oversittede frister, og
        # kalleren ville persistert feiltellingen 0 — altså slettet en
        # alt opptelt feil ved hver overlappende aktivering, og alarmen
        # etter to sammenhengende feil ville aldri nådd fram.
        res.hoppet_over = True
        return res
    try:
        try:
            rad = conn.execute("SELECT * FROM m30_sveip_frister(%s)",
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
