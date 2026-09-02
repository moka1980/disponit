"""M-12 (097) — gjennomgangssveipen for tilgangsregisteret.

`disponit-tilgangssveip.timer`, én gang i døgnet, kaller
`m12_sveip_gjennomganger(p_grense)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(097): en tilgang som har stått lenger enn sin egen `gjennomgang_dogn`
uten at noen navngitt har etterprøvd den er et `gjennomgang_utlopt`-funn,
og ETT funn per (tilgang, funntype) holdes åpent og oppdateres med
`sist_sett_sveip`. Timeren bestemmer ikke hva som er utløpt — den kaller
funksjonen som vet det.

DEN OBSERVERER, DEN ENDRER INGENTING. Det er hele modulens v1-dom, og
den er målbar her: sveipen rører nøyaktig ett lager utenfor sine egne
tre tabeller, og det er ingen. Ingen identitetsklient importeres, ingen
tilgang provisjoneres, og ikke engang evidenskjeden skrives — den hører
de menneskelige handlingene til. Invarianten heter
`tilgang_endret_utenfor_registeret`, og porten teller radene utenfor
modulens egne lagre før og etter en kjøring.

Formen er `artefaktrydding.py` sin, ordrett (via 095s `begrepssveip`), og
av de samme grunnene:

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
    gjennomgangssveip er et tilgangsregister som eldes uten at noen ser
    det — og det er nøyaktig tilstanden modulen finnes for å gjøre
    synlig. Verre her enn noe annet sted: den tilgangen ingen har sett på
    er den som gir en tidligere ansatt en nøkkel hun ikke skulle hatt.
  * **Én JSON-linje per kjøring, exit 1 ved feil.** «En jobb som ikke
    kunne måle rapporterer FUNN, aldri null.»

`AVKORTET` ER ET FUNN, IKKE EN DETALJ. Døren tar et tak per tenant på
hvor mange NYE funn én kjøring kan reise, så et register som plutselig
vokser med ti tusen rader ikke gjør én natts kjøring uendelig. Traff
kjøringen taket, har den ikke MÅLT hele registeret — og det står i
JSON-linja. En avkortet kjøring er ikke en feilet kjøring (funnene er
idempotente, neste kjøring tar igjen), men den er heller ikke en kjøring
som kan leses som «alt er sett på».

TAKET er 500 per tenant. Det er ikke et magisk tall: det er stort nok
til at en normal tenants HELE register kan bli funnbelagt i én kjøring
selv første natt etter en import, og lite nok til at en kjøring har en
øvre grense i det hele tatt. Kadensen er daglig, så en tenant som
skulle trenge mer får resten i morgen — og `avkortet` sier fra at det
er det som skjedde.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Hvor mange NYE funn én kjøring kan reise per tenant. Se
#: modulkommentaren for hvorfor taket finnes og hvorfor det er per tenant.
GRENSE_PER_TENANT = 500
#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to tilgangssveip overlapper aldri.
ARBEIDERNOKKEL = 912_097_431


@dataclass
class Sveipresultat:
    tenanter: int = 0
    nye: int = 0
    oppdaterte: int = 0
    lukkede: int = 0
    #: Sant når minst én tenant traff taket sitt. Kjøringen lyktes, men
    #: den har ikke sett hele registeret.
    avkortet: bool = False
    feilet: bool = False
    alarm_utlost: bool = False
    #: En kjøring som fant arbeidernøkkelen opptatt har verken lyktes
    #: eller feilet. Skillet må stå PÅ resultatet, ellers kan ikke
    #: kalleren vite at feiltelleren skal stå urørt (artefaktrydding,
    #: Codex P2).
    hoppet_over: bool = False


def kjor(conn, *, grense: int = GRENSE_PER_TENANT,
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
        # sett ut som en kjøring som fant null utløpte gjennomganger, og
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
            rader = conn.execute("SELECT * FROM m12_sveip_gjennomganger(%s)",
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
        res.tenanter, res.nye, res.oppdaterte, res.lukkede = (
            int(rad[0]), int(rad[1]), int(rad[2]), int(rad[3]))
        res.avkortet = bool(rad[4])
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
