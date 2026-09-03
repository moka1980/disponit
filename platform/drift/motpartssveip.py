"""M-48 (116) — motpartssveipen for uvurderte og forverrede motparter.

`disponit-motpartssveip.timer`, én gang i døgnet, kaller
`m48_sveip_motparter(p_grense)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(116): en aktiv motpart uten vurdering innen tenantens frist er
`uvurdert_motpart`; en vurdering eldre enn gyldighetsvinduet er
`utdatert_vurdering`; en ny registerprofil ingen har målt mot policyen
er `profil_uten_vurdering`; en motpart registeret sier er på vei ut er
`motpart_avviklet`; et forslag over tenantens tak er
`forslag_over_tak`; en reservasjon uten svar er `oppslag_uten_svar`;
fem eller flere mislykkede forespørsler på ett døgn er
`gjentatte_oppslagsfeil`; og en tenant uten policy er `ingen_krav`.

DENNE FILA GJØR INGEN UTGÅENDE FORESPØRSEL, OG DET ER MODULENS MEST
UTSATTE STED.

M-48 HAR en utgående kanal — den eneste i klynge 6 — og sveipen er
nettopp der det ville vært lettest å misbruke den. Den vet til enhver
tid hvilke motparter som står uvurderte, og en nattlig oppfriskning av
alle sammen ville «løst» dem på én kjøring.

Det ville vært doktrinens verste tilfelle, ikke dens oppfyllelse:
«den unødvendige forespørselen ER skaden», og en sveip som spør om alt
hver natt gjør nettopp unødvendige forespørsler i industriell skala.
Oppslaget hører hjemme der noen HAR et formål og en hjemmel å oppgi —
i `/v1/motpart/{id}/oppslag`, én gang, med begge deler på raden.

DERFOR IMPORTERER DENNE FILA INGENTING SOM KAN SNAKKE UT. Ingen
`httpx`, ingen `requests`, ingen `socket`, og ikke
`api.foretaksregister`. Det er ikke en forglemmelse; det er porten
`oppslag_uten_formaal_og_hjemmel` skrevet som kode — en sveip har
ingen av delene å oppgi.

DEN ENE TINGEN SVEIPEN RYDDER er forlatte reservasjoner. En klient som
døde mellom reservasjonen og fullføringen etterlater en rad ingen
fyller ut, og uten en vei ut ville `oppslag_uten_svar` stått åpent for
alltid — M-39s felle (113): en funntype uten øvre grense OG uten
botemiddel er et varsel som aldri kan lukkes, og et varsel som aldri
lukkes blir et varsel ingen leser. Ryddingen setter `forlatt`, som er
ærligere enn `feil`: den sier at forespørselen gikk ut og at vi aldri
registrerte hva som kom tilbake.

POLICYEN ER TENANTENS, IKKE MODULENS. Ferskhetsvinduet, fristene og
taket ligger i `motpartskrav` i basen, satt gjennom en dør. Denne fila
bærer derfor ingen av dem — en konstant her ville vært nøyaktig den
fullmakten invarianten `kredittpolicy_hardkodet` forbyr.

Formen er `adressesveip.py` sin, ordrett, og av de samme grunnene:

  * **Advisory-lås.** To sveip overlapper aldri; `hoppet_over` står PÅ
    resultatet så kalleren vet at feiltelleren skal stå urørt.
  * **Kontrakten valideres FØR commit**, og på ALLE radene — også radens
    FORM (109s retting).
  * **To sammenhengende feilede kjøringer → alarm.**
  * **Én JSON-linje per kjøring, exit 1 ved feil.**

TAKET er 500 tenanter per kjøring. Det begrenser TRANSAKSJONEN, ikke
sannheten.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Maks antall tenanter sveipen tar per kjøring.
GRENSE = 500
#: INGEN FRIST HER, og det er poenget: policyen er TENANTENS, og den
#: ligger i `motpartskrav` i basen.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to motpartssveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 648_113_902

#: Antall felt `m48_sveip_motparter` lover. Fem, ikke fire: `forlatte`
#: kom til fordi ryddingen av reservasjoner er en egen handling og skal
#: telles for seg — en natt der sveipen forlot tjue oppslag ser ellers
#: ut som en helt vanlig natt.
KONTRAKTFELT = 5


@dataclass
class Sveipresultat:
    tenanter: int = 0
    nye: int = 0
    #: Funn som alt sto der og bare ble friskmeldt.
    oppdaterte: int = 0
    lukkede: int = 0
    #: Reservasjoner som ble satt til `forlatt`. Står for seg fordi
    #: dette er den ENESTE raden sveipen endrer utenfor funntabellen.
    forlatte: int = 0
    feilet: bool = False
    alarm_utlost: bool = False
    #: En kjøring som fant arbeidernøkkelen opptatt har verken lyktes
    #: eller feilet.
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
        # persistert feiltellingen 0 — altså slettet en alt opptelt feil.
        res.hoppet_over = True
        return res
    try:
        # KONTRAKTEN VALIDERES FØR COMMIT, og på ALLE radene.
        # REKKEFØLGEN ER DOMMEN: bare en validert kontrakt committes.
        try:
            rader = conn.execute(
                "SELECT * FROM m48_sveip_motparter(%s)",
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
        # …OG RADENS FORM ER EN DEL AV KONTRAKTEN. `[:KONTRAKTFELT]` og
        # ikke hele raden: sveipen LESER fem felt, og en dør som en dag
        # returnerer et sjette skal ikke gjøre en gyldig kjøring til en
        # feilet (#358s lærdom).
        try:
            verdier = tuple(int(v) for v in rader[0][:KONTRAKTFELT])
            if len(verdier) != KONTRAKTFELT:
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
         res.forlatte) = verdier
        return res
    finally:
        # Opplåsingen er BEST EFFORT. Låsen er sesjonsscopet: en død
        # sesjon slipper den uansett.
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
