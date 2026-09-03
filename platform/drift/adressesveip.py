"""M-19 (112) — adressesveipen for ukontrollerte og utløpte adresser.

`disponit-adressesveip.timer`, én gang i døgnet, kaller
`m19_sveip_adresser(p_grense)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(112): en gjeldende adresse som ingen har kontrollert innen tenantens
frist er `ukontrollert_adresse`; en som ble godkjent for lenge siden er
`kontroll_utlopt`; en noen så på og sa nei til er `avvist_adresse`; en
godkjent med en metode tenantens eget krav ikke godtar er
`utilstrekkelig_metode`; og en tenant uten krav er `ingen_krav`.

SVEIPEN SLÅR INGENTING OPP. Den kunne, teknisk — den vet nøyaktig
hvilke adresser som står ukontrollerte, og et oppslag ville «løst» dem
alle på én natt. Men et oppslag er en utgående kanal med
personopplysninger i: kundens navn og adresse ut av huset, til en
tredjepart vi ikke har databehandleravtale med. Og svaret ville vært
feil vare uansett — at en adresse FINNES i et register sier ikke at
pakken kommer fram til den som skal ha den.

DERFOR IMPORTERER DENNE FILA INGENTING SOM KAN SNAKKE UT. Ingen
`httpx`, ingen `requests`, ingen `socket`. Det er ikke en forglemmelse;
det er invarianten `modulen_slo_opp_eksternt`, skrevet som kode.

«UKONTROLLERBAR» TELLER IKKE SOM EN KONTROLL. En adresse noen forsøkte
å kontrollere uten å få det til blir stående som `ukontrollert_adresse`
— å telle den som kontrollert ville gjort «vi klarte ikke å sjekke» om
til «vi har sjekket».

KRAVET ER TENANTENS, IKKE MODULENS. Fristen, gyldighetsvinduet og
hvilke metoder som teller ligger i basen, satt gjennom en dør. Denne
fila bærer derfor ingen av dem — en konstant her ville vært nøyaktig
den fullmakten invarianten `valideringskrav_hardkodet` forbyr.

Formen er `betalingssveip.py` sin, ordrett, og av de samme grunnene:

  * **Advisory-lås.** To sveip overlapper aldri; `hoppet_over` står PÅ
    resultatet så kalleren vet at feiltelleren skal stå urørt.
  * **Kontrakten valideres FØR commit**, og på ALLE radene — også radens
    FORM (109s retting).
  * **To sammenhengende feilede kjøringer → alarm.** En stille
    adressesveip er leveranser ingen har sett på.
  * **Én JSON-linje per kjøring, exit 1 ved feil.**

TAKET er 500 tenanter per kjøring. Det begrenser TRANSAKSJONEN, ikke
sannheten.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Maks antall tenanter sveipen tar per kjøring.
GRENSE = 500
#: INGEN FRIST HER, og det er poenget: kravene er TENANTENS, og de
#: ligger i `adressekrav` i basen. En konstant i denne fila ville vært
#: nøyaktig den fullmakten invarianten `valideringskrav_hardkodet`
#: forbyr — «en visuell sjekk holder» er en forretningsbeslutning.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to adressesveip overlapper aldri. Tallet er modulens
#: eget og deles ikke med noen annen sveip — to jobber som låser på
#: samme nøkkel ville blokkert hverandre uten grunn.
ARBEIDERNOKKEL = 619_204_773


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
                "SELECT * FROM m19_sveip_adresser(%s)",
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
