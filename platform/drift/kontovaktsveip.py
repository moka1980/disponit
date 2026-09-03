"""M-42 (110) — kontovaktsveipen for kontoendringer og verifikasjoner.

`disponit-kontovaktsveip.timer`, én gang i døgnet, kaller
`m42_sveip_konto(p_grense)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(110): en mottaker der den siste oppgitte kontoen er en ANNEN enn den
forrige og ingen har verifisert den, er `kontoendring`; en konto ingen
har sjekket innen tenantens frist er `uverifisert_konto`; en
verifikasjon eldre enn `reverifikasjon_dogn` er `verifikasjon_utlopt`;
og en tenant uten grenser er `ingen_terskel`.

SVEIPEN STOPPER INGEN BETALING OG VERIFISERER INGENTING. Den kunne,
teknisk — den vet nøyaktig hvilke mottakere som byttet konto i går. Men
DET FARLIGSTE EN BETALINGSVAKT KAN GJØRE ER IKKE Å SLIPPE NOE GJENNOM;
det er å STOPPE noe. En vakt som blokkerer feil er sin egen skade, og en
vakt ingen har målt vet ikke hvor ofte den tar feil.

OG DEN ER IKKE DEN FØRSTE SOM SER EN KONTOENDRING. Det funnet skrives av
DØREN, i samme transaksjon som oppgaven — et døgns forsinkelse er et
døgn der pengene kan gå. Sveipen holder funnet åpent og legger
tidsfunnene til.

GRENSENE ER TENANTENS, IKKE MODULENS. Reverifikasjonsvinduet og fristen
for en uverifisert konto ligger i basen, satt gjennom en dør av tenanten
selv. Denne fila bærer derfor ingen av dem — en konstant her ville vært
nøyaktig den fullmakten invarianten `verifikasjonskrav_hardkodet`
forbyr.

Formen er `leverandorsveip.py` sin, ordrett, og av de samme grunnene:

  * **Advisory-lås.** To sveip overlapper aldri; `hoppet_over` står PÅ
    resultatet så kalleren vet at feiltelleren skal stå urørt.
  * **Kontrakten valideres FØR commit**, og på ALLE radene — også radens
    FORM (109s retting).
  * **To sammenhengende feilede kjøringer → alarm.** En stille
    kontovaktsveip er en kontoendring ingen ser.
  * **Én JSON-linje per kjøring, exit 1 ved feil.**

TAKET er 500 nye funn per tenant per kjøring. Det begrenser
TRANSAKSJONEN, ikke sannheten.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Maks antall NYE funn sveipen reiser per tenant per kjøring.
GRENSE = 500
#: INGEN TERSKEL HER, og det er poenget: grensene er TENANTENS, og de
#: ligger i `kontoterskel` i basen. En konstant i denne fila ville vært
#: nøyaktig den fullmakten invarianten `verifikasjonskrav_hardkodet`
#: forbyr — «en verifikasjon holder i ett år» er en
#: forretningsbeslutning.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to kontovaktsveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip — to jobber som
#: låser på samme nøkkel ville blokkert hverandre uten grunn.
ARBEIDERNOKKEL = 613_209_774


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
                "SELECT * FROM m42_sveip_konto(%s)",
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
        # `[:5]` og ikke hele raden: sveipen LESER fem felt, og en dør
        # som en dag returnerer et sjette skal ikke gjøre en gyldig
        # kjøring til en feilet. Det som måles er at DE FEM finnes og
        # lar seg lese som heltall.
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
