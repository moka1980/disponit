"""M-52 (122) — tollkodesveipen for regelverk som er avviklet.

`disponit-tollkodesveip.timer`, én gang i døgnet, kaller
`m52_sveip_tollkode(p_grense)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(122): et avviklet regelverk UTEN en gyldig etterfølger er
`nomenklatur_utlopt`; et som avvikles innen tenantens varselvindu er
`nomenklatur_utloper_snart`; en kode som hviler på et regelverk som
siden er avviklet er `forslag_mot_utlopt_nomenklatur`; en vare ingen
har klassifisert innen fristen er `vare_uten_forslag`; et forslag med
lavere sikkerhet enn tenantens NÅVÆRENDE terskel er
`forslag_under_terskel`; et forslag over terskel som ingen har merket
klart er `forslag_ikke_klart`; og en tenant uten terskler er
`ingen_krav`.

SVEIPEN KLASSIFISERER IKKE, OG DET ER MODULENS VIKTIGSTE FRAVÆR.

EN HS-KODE ER EN RETTSLIG PÅSTAND OM HVA EN VARE ER. Lot vi sveipen
klassifisere selv, ville slike påstander oppstått om natten uten at
noen ba om det — og feil kode gir bot, som treffer KUNDEN.
`m52_avgi_forslag` kalles av et menneske gjennom flaten, aldri herfra.

OG SVEIPEN MERKER INGENTING KLART. «Klar til deklarering» er en
tilstand et menneske setter; deklarasjonen selv finnes ikke i v1.

DEN ENE TINGEN SVEIPEN LUKKER er funn hvis tilstand er borte, og
`forslag_mot_utlopt_nomenklatur` er med der — men BARE der. Døra
`m52_lukk_funn` nekter et menneske å lukke nettopp det funnet.
Forskjellen er hele poenget: sveipen lukker det som ER løst, fordi
varen er klassifisert på nytt mot en gyldig nomenklatur. Et menneske
kan ikke lukke det som ikke er løst.

SVEIPEN HENTER HELLER INGEN NOMENKLATUR. Tolltariffen er myndighetens,
og en modul som lastet ned den nyeste versjonen selv ville tatt
ansvaret for at NØYAKTIG den er den gjeldende.

DERFOR IMPORTERER DENNE FILA INGENTING SOM KAN SNAKKE UT. Ingen
`httpx`, ingen `requests`, ingen `socket`.

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
#: INGEN TERSKEL HER, og det er poenget dobbelt opp: nomenklaturen er
#: MYNDIGHETENS og ligger i `nomenklatur`/`varenummer`, og
#: sikkerhetsterskelen er TENANTENS og ligger i `tollkrav`. En konstant
#: her ville vært en fullmakt modulen ga seg selv over kundens bøter.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to tollkodesveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 638_204_915

#: Antall felt `m52_sveip_tollkode` lover. Fire, som M-46, M-49, M-51,
#: M-55 og M-54: modulen har ingen rad å rydde tilsvarende M-48s
#: forlatte reservasjoner.
KONTRAKTFELT = 4


@dataclass
class Sveipresultat:
    tenanter: int = 0
    nye: int = 0
    #: Varsler som alt sto der og bare ble sett på nytt.
    oppdaterte: int = 0
    lukkede: int = 0
    #: INGEN `forlatte`: M-48 rydder forlatte reservasjoner, M-52 har
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
                "SELECT * FROM m52_sveip_tollkode(%s)",
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
