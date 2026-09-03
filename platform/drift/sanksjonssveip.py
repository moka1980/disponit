"""M-49 (117) — sanksjonssveipen for uavklarte treff og gamle kontroller.

`disponit-sanksjonssveip.timer`, én gang i døgnet, kaller
`m49_sveip_sanksjoner(p_grense)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(117): et treff ingen har avklart innen tenantens frist er
`uavklart_treff`; et subjekt ingen har kontrollert er
`ukontrollert_subjekt`; en kontroll eldre enn gyldighetsvinduet er
`kontroll_utlopt`; en kontroll mot en listeversjon som ikke lenger er
den nyeste er `kontroll_mot_gammel_liste`; et treff et menneske har
bekreftet er `bekreftet_treff`; og en tenant uten policy eller uten
listeversjon er `ingen_krav` eller `ingen_liste`.

SVEIPEN AVKLARER INGENTING, OG DET ER MODULENS VIKTIGSTE FRAVÆR.

Den ser hvert eneste uavklarte treff, og den vet likheten på hvert av
dem. Å lukke alle under 90 % ville tømt køen på én natt. Det ville
vært nøyaktig det spesifikasjonen forbyr — «navnelikhet er aldri
automatisk avfeid» — og feilen ville vært usynlig: en kø som går ned
ser ut som saksbehandling.

Et treff lukkes bare av `m49_avklar_treff`, med en aktør og en
begrunnelse på minst tolv tegn. Fraværet av enhver annen vei ER porten
`modulen_avfeide_navnelikhet`.

SVEIPEN BLOKKERER HELLER INGENTING. v1 blokkerer ikke — beslutningen,
begrunnelsen og utløseren står i migrasjonens topp. Den korte
versjonen: et register stanser ingen handel, og et flagg ingen leser
er `alarm`-feltet fra 115 om igjen.

DERFOR IMPORTERER DENNE FILA INGENTING SOM KAN SNAKKE UT. Ingen
`httpx`, ingen `requests`, ingen `socket`. Sanksjonslistene registreres
av et menneske som har lastet dem ned, med kilde, versjon og
innholdssum — porten `modulen_hentet_eksternt`. M-48 fikk klyngens ene
unntak; M-49 fikk det ikke.

POLICYEN ER TENANTENS. Matchterskelen, fristene og gyldighetsvinduet
ligger i `sanksjonskrav` i basen. En konstant her ville vært nøyaktig
den fullmakten invarianten `matchterskel_hardkodet` forbyr.

Formen er `adressesveip.py` sin, ordrett:

  * **Advisory-lås.** To sveip overlapper aldri; `hoppet_over` står PÅ
    resultatet så kalleren vet at feiltelleren skal stå urørt.
  * **Kontrakten valideres FØR commit**, og på ALLE radene.
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
#: ligger i `sanksjonskrav` i basen.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to sanksjonssveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 655_390_118

#: Antall felt `m49_sveip_sanksjoner` lover. FIRE, ikke fem: M-49 har
#: ingen rad å rydde tilsvarende M-48s forlatte reservasjoner. Et
#: uavklart treff RYDDES IKKE av noen — det er hele poenget.
KONTRAKTFELT = 4


@dataclass
class Sveipresultat:
    tenanter: int = 0
    nye: int = 0
    #: Funn som alt sto der og bare ble friskmeldt.
    oppdaterte: int = 0
    lukkede: int = 0
    #: INGEN `forlatte`: M-48 rydder forlatte reservasjoner, M-49 har
    #: ingenting tilsvarende. Å bære med seg feltet med verdien 0
    #: ville vært en linje som lot som den målte noe.
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
                "SELECT * FROM m49_sveip_sanksjoner(%s)",
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
        # ikke hele raden: sveipen LESER fire felt, og en dør som en dag
        # returnerer et femte skal ikke gjøre en gyldig kjøring til en
        # feilet (#358s lærdom).
        try:
            verdier = tuple(int(v) for v in rader[0][:KONTRAKTFELT])
            if len(verdier) != KONTRAKTFELT:
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
