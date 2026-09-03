"""M-34 (100) — etterprøvingssveipen for kontrollregisteret.

`disponit-compliancesveip.timer`, én gang i døgnet, kaller
`m34_sveip_etterprovinger(p_grense)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(100): en kontroll forbi `sist_etterprovd + etterproving_dogn` er et
`etterproving_forbigatt`-funn, en kontroll hvis eier ikke lenger er
aktivt medlem er et `kontroll_uten_eier`-funn, en kontroll som står
`oppfylt` uten at evidenshenvisningen svarer til en faktisk
etterprøvingsrad er et `oppfylt_uten_evidens`-funn — og ETT funn per
(kontroll, funntype) holdes åpent og oppdateres med `sist_sett_sveip`.
Timeren bestemmer ikke hva som er forbigått; den kaller funksjonen som
vet det.

Formen er `artefaktrydding.py` sin, ordrett, og av de samme grunnene:

  * **Advisory-lås.** To sveip overlapper aldri. En kjøring som fant
    nøkkelen opptatt har verken lyktes eller feilet — den gjorde
    ingenting, og `hoppet_over` står PÅ resultatet så kalleren vet at
    feiltelleren skal stå urørt.
  * **Kontrakten valideres FØR commit**, og på ALLE radene:
    nøyaktig én rad, ellers rulles det tilbake og kjøringen sier
    feilet. Den forrige formen committet først og oppdaget så at raden
    manglet — en transaksjon som ble stående mens kjøringen rapporterte
    feilet.
  * **To sammenhengende feilede kjøringer → alarm.** En stille
    etterprøvingssveip er et kontrollregister som eldes uten at noen ser
    det — og det er nøyaktig tilstanden modulen finnes for å gjøre
    synlig. Et register der ingenting er forbigått fordi ingen målte, er
    ikke et grønt register.
  * **Én JSON-linje per kjøring, exit 1 ved feil.** En jobb som ikke
    kunne måle rapporterer FUNN, aldri null.

`avkortet` ER OGSÅ EN RAPPORTERT TILSTAND, ikke en stille sannhet. Traff
sveipen taket sitt hos én eller flere tenanter, står tallet i linjen.
Det er ikke en feilet kjøring — funnene er idempotente, så neste kjøring
tar igjen resten — men det er heller ikke «ferdig», og forskjellen skal
være lesbar i journalen uten å måtte telles i basen etterpå.

TAKET er 500 nye funn per tenant per kjøring. Det begrenser
TRANSAKSJONEN, ikke sannheten. Et kontrollregister med mer enn 500 nye
funn på én natt er dessuten i seg selv en opplysning: da er det ikke
enkeltkontroller som er forbigått, det er hele etterlevelsen.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Maks antall NYE funn sveipen reiser per tenant per kjøring.
GRENSE = 500
#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to compliancesveip overlapper aldri.
ARBEIDERNOKKEL = 915_774_234


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
        # kant, og `fetchone()` tidde om den.
        #
        # REKKEFØLGEN ER DOMMEN: den forrige formen committet FØRST og
        # oppdaget så at raden manglet — altså en transaksjon som ble
        # stående mens kjøringen rapporterte feilet. Nå rulles den
        # tilbake, og bare en validert kontrakt committes.
        try:
            rader = conn.execute(
                "SELECT * FROM m34_sveip_etterprovinger(%s)",
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
        # lese som heltall. Konverteringen gjøres FØR commit fordi det
        # er her doktrinen står: en rad som ikke er kontrakten skal
        # RULLE TILBAKE, ikke bli stående mens kjøringen rapporterer
        # feilet. Lå den etter commit, var «feilet» og «transaksjonen
        # står» sanne samtidig (109-rettingen, CodeRabbit).
        #
        # `[:5]` og ikke hele raden: sveipen LESER fem felt, og en dør
        # som en dag returnerer et sjette skal ikke gjøre en gyldig
        # kjøring til en feilet. Den delte kontraktporten mater dessuten
        # alle sveipene et supersett.
        try:
            verdier = tuple(int(v) for v in rader[0][:5])
            if len(verdier) != 5:
                raise ValueError("kontrakten ga ikke fem felt")
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
