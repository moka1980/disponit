"""M-51 (119) — tilskuddssveipen for frister og estimater uten grunnlag.

`disponit-tilskuddssveip.timer`, én gang i døgnet, kaller
`m51_sveip_tilskudd(p_grense)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(119): en ordning som nærmer seg søknadsfristen uten ferdigstilt
estimat er `frist_naermer_seg`; en frist som gikk er `frist_passert`;
et estimat uten en eneste post er `estimat_uten_poster`; en sum over
ordningens tak er `estimat_over_ordningstak`; en post som peker på en
kildepost som er blitt for gammel er `utdatert_kildepost`; en aktiv
ordning uten estimat er `ingen_estimat`; og en tenant uten terskler er
`ingen_krav`.

SVEIPEN REGNER INGEN ESTIMATER, OG DET ER MODULENS VIKTIGSTE FRAVÆR.

Den ser hver ordning, hver kildepost og hvert tak. En «hjelpsom»
automatikk som fylte inn poster for å nå taket, ville produsert et
estimat ingen har tatt stilling til — og et estimat er et TALL EN
BEDRIFT PLANLEGGER ETTER. Sier vi «dere kan få 400 000», og bedriften
ansetter på det grunnlaget, er avstanden mellom estimat og lovnad ikke
akademisk: den er lønnsutbetalinger.

Et estimat bygges bare av `m51_legg_til_post`, der hvert beløp peker
på en kildepost gjennom en NOT NULL fremmednøkkel. Fraværet av enhver
annen vei ER porten `belop_uten_kildepost`.

OG SVEIPEN FERDIGSTILLER INGENTING. `m51_ferdigstill_estimat` nekter
uten minst én forutsetning — «estimat presenteres som estimat MED
FORUTSETNINGER, aldri som lovnad» — og et estimat uten forutsetninger
er nettopp en lovnad, fordi ingenting sier hva tallet hviler på. En
sveip som ferdigstilte automatisk ville omgått den vakten.

SVEIPEN SENDER HELLER INGEN SØKNAD. 119 har ingen «sendt»-kolonne.

DERFOR IMPORTERER DENNE FILA INGENTING SOM KAN SNAKKE UT. Ingen
`httpx`, ingen `requests`, ingen `socket`. Ordningene registreres av et
menneske som har lest regelverket, med versjon og innholdssum — porten
`modulen_hentet_eksternt`. Et regelverk som endres gjør gårsdagens
estimat feil uten at noe i systemet vet det; en modul som hentet
automatisk ville tatt ansvaret for at NØYAKTIG den versjonen er den
gjeldende.

POLICYEN ER TENANTENS. Fristvarselet, kildepostvinduet og usikkerheten
ligger i `tilskuddskrav`; ordningens EGNE frister, satser og maksbeløp
står på ordningsraden. En konstant her ville vært nøyaktig den
fullmakten invarianten `ordningskrav_hardkodet` forbyr.

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
#: ligger i `tilskuddskrav` i basen.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to tilskuddssveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 671_552_904

#: Antall felt `m51_sveip_tilskudd` lover. Fire, som M-46 og
#: M-49: modulen har ingen rad å rydde tilsvarende M-48s forlatte
#: reservasjoner.
KONTRAKTFELT = 4


@dataclass
class Sveipresultat:
    tenanter: int = 0
    nye: int = 0
    #: Funn som alt sto der og bare ble friskmeldt.
    oppdaterte: int = 0
    lukkede: int = 0
    #: INGEN `forlatte`: M-48 rydder forlatte reservasjoner, M-51 har
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
                "SELECT * FROM m51_sveip_tilskudd(%s)",
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
