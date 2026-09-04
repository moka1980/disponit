"""M-46 (118) — anbudssveipen for frister og udekkede krav.

`disponit-anbudssveip.timer`, én gang i døgnet, kaller
`m46_sveip_anbud(p_grense)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(118): et anbud som nærmer seg fristen uten klart utkast er
`frist_naermer_seg`; en frist som gikk er `frist_passert`; et absolutt
krav uten et punkt som dekker det er `udekket_absolutt_krav`; et
vektet er `udekket_krav`; et punkt som peker på et utløpt dokument er
`utlopt_kilde`; et anbud uten kravpunkter er `ingen_krav_registrert`;
et anbud utenfor søkeprofilen er `utenfor_profil`; og en tenant uten
profil er `ingen_profil`.

SVEIPEN FYLLER INGENTING INN, OG DET ER MODULENS VIKTIGSTE FRAVÆR.

Den ser hvert udekket krav og hvert kildedokument tenanten har. En
«hjelpsom» automatikk som fant nærmeste passende kilde og skrev et
punkt, ville vært nøyaktig det spesifikasjonens vakt forbyr: «udekkede
krav blir unntak, ALDRI UTFYLT GJETNING». Og feilen ville vært
usynlig — et utkast uten hull ser ferdig ut.

Et punkt skrives bare av `m46_registrer_punkt`, med en NOT NULL
fremmednøkkel til et kildedokument som er gyldig NÅ. Fraværet av
enhver annen vei ER porten `utkastpunkt_uten_kilde`.

SVEIPEN SENDER HELLER INGENTING. Et innsendt tilbud er bindende, og
fristen gjør det irreversibelt: man kan ikke trekke det og sende et
bedre etterpå. Det finnes ingen «sendt»-kolonne i 118 å skrive til.

FRISTFUNNET ER MODULENS MEST BETENTE. En frist som passerer er den ene
feilen som ikke kan rettes dagen etter, og derfor er
`frist_varsel_dogn` tenantens tall og ikke vårt.

DERFOR IMPORTERER DENNE FILA INGENTING SOM KAN SNAKKE UT. Ingen
`httpx`, ingen `requests`, ingen `socket`. Anbudene registreres av et
menneske som har lest dem i portalen — porten `modulen_hentet_eksternt`.
M-48 fikk klyngens ene unntak; M-46 fikk det ikke, og grunnen er at
Doffin og TED ikke er ETT oppslag, men et ABONNEMENT: en søkeprofil
som kjører kontinuerlig og henter alt som matcher.

POLICYEN ER TENANTENS. Søkeprofilen, fristvarselet og
kildegyldigheten ligger i `anbudsprofil` i basen. En konstant her ville
vært nøyaktig den fullmakten invarianten `sokeprofil_hardkodet` forbyr.

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
#: ligger i `anbudsprofil` i basen.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to anbudssveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 662_814_337

#: Antall felt `m46_sveip_anbud` lover. Fire, som M-49: modulen har
#: ingen rad å rydde tilsvarende M-48s forlatte reservasjoner.
KONTRAKTFELT = 4


@dataclass
class Sveipresultat:
    tenanter: int = 0
    nye: int = 0
    #: Funn som alt sto der og bare ble friskmeldt.
    oppdaterte: int = 0
    lukkede: int = 0
    #: INGEN `forlatte`: M-48 rydder forlatte reservasjoner, M-46 har
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
                "SELECT * FROM m46_sveip_anbud(%s)",
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
