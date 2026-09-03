"""M-27 (109) — lagersveipen for beholdning og bestillingspunkt.

`disponit-lagersveip.timer`, én gang i døgnet, kaller
`m27_sveip_lager(p_grense)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(109): en aktiv vare på eller under sitt bestillingspunkt er
`under_bestillingspunkt`; en vare uten et punkt på tenantens grense er
`uten_bestillingspunkt`; en vare uten én eneste bevegelse på
`stille_dogn` er `uten_bevegelse`; en vare ingen har talt på
`telleintervall_dogn` er `ikke_talt`; og en tenant uten grenser er
`ingen_terskel`.

SVEIPEN BESTILLER INGENTING OG JUSTERER INGEN BEHOLDNING. Den kunne,
teknisk — den vet nøyaktig hvilke varer som er under punktet sitt, og
hvor mye som mangler. Men en bestilling binder virksomheten økonomisk,
og en jobb som gjorde det om natten ville tatt den beslutningen på ingens
vegne. Det er klyngens dom: her holder vi igjen på å AUTORISERE, ikke
bare på å utføre.

OG DEN BEREGNER INGEN PROGNOSE. Det finnes ikke ett glidende
gjennomsnitt her, ingen forbruksrate, ingen ekstrapolering — og det er
med vilje: `prognose_konfidens` uten en målt treffrate bak seg er et
tall som ser ut som kunnskap.

GRENSENE ER TENANTENS, IKKE MODULENS. Stillhetsvinduet, punktvinduet og
telleintervallet ligger i basen, satt gjennom en dør av tenanten selv.
Denne fila bærer derfor ingen av dem — en konstant her ville vært
nøyaktig den fullmakten invarianten `bestillingspunkt_hardkodet` forbyr.

Formen er `leverandorsveip.py` sin, ordrett, og av de samme grunnene:

  * **Advisory-lås.** To sveip overlapper aldri; `hoppet_over` står PÅ
    resultatet så kalleren vet at feiltelleren skal stå urørt.
  * **Kontrakten valideres FØR commit**, og på ALLE radene.
  * **To sammenhengende feilede kjøringer → alarm.** En stille
    lagersveip er en vare som går tom uten at noen merker det.
  * **Én JSON-linje per kjøring, exit 1 ved feil.**

TAKET er 500 nye funn per tenant per kjøring. Det begrenser
TRANSAKSJONEN, ikke sannheten.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Maks antall NYE funn sveipen reiser per tenant per kjøring.
GRENSE = 500
#: INGEN TERSKEL HER, og det er poenget: grensene er TENANTENS, og de
#: ligger i `lagerterskel` i basen. En konstant i denne fila ville vært
#: nøyaktig den fullmakten invarianten `bestillingspunkt_hardkodet`
#: forbyr — «et halvt år uten bevegelse er dødt lager» er en
#: forretningsbeslutning.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to lagersveip overlapper aldri. Tallet er modulens
#: eget og deles ikke med noen annen sveip — to jobber som låser på
#: samme nøkkel ville blokkert hverandre uten grunn.
ARBEIDERNOKKEL = 927_446_180


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
        # sett ut som en kjøring som fant null avvik, og
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
                "SELECT * FROM m27_sveip_lager(%s)",
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
        # …OG RADENS FORM ER EN DEL AV KONTRAKTEN. Fem felt som lar seg
        # lese som heltall — ikke fire, ikke en NULL. Konverteringen
        # gjøres FØR commit fordi det er her doktrinen står: en rad som
        # ikke er kontrakten skal rulle tilbake, ikke bli stående mens
        # kjøringen rapporterer feilet (CodeRabbit, 109).
        try:
            verdier = tuple(int(v) for v in rader[0])
            if len(verdier) != 5:
                raise ValueError("m27_sveip_lager ga ikke fem felt")
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
        (res.tenanter, res.nye, res.oppdaterte, res.lukkede,
         res.avkortet) = verdier
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
