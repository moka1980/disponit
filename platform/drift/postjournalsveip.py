"""M-50 (124) — postjournalsveipen. VAKTEN, IKKE HØSTEMASKINEN.

`disponit-postjournalsveip.timer`, én gang i døgnet, kaller
`m50_sveip_postjournal(p_grense)`.

SVEIPEN HENTER INGENTING, OG DET ER MODULENS VIKTIGSTE FRAVÆR.

DEN NÆRLIGGENDE BEGRUNNELSEN TREFFER IKKE: postjournaler ER
offentlige. Innvendingen «vi har ikke lov til å se på det» gjelder
ikke.

DET SOM TREFFER ER NOE ANNET. En postjournal inneholder NAVNGITTE
PRIVATPERSONER, og en systematisk høsting er en HELT ANNEN BEHANDLING
enn det enkeltoppslag et menneske gjør. Ett oppslag er innsyn; ti tusen
oppslag sammenstilt i et register er en PROFIL — og profilen er VÅR,
ikke kommunens.

En sveip som hentet selv ville vært nettopp den høstemaskinen. Derfor
importerer denne fila ingenting som kan snakke ut: ingen `httpx`, ingen
`requests`, ingen `socket`.

SVEIPEN ANONYMISERER HELLER IKKE. Å slette en personopplysning
automatisk ville sett riktig ut, og vært galt: sletting er en handling
med en ansvarlig, og `m50_anonymiser` kalles av et menneske gjennom
flaten. Sveipen SIER FRA at fristen er passert; den rydder ikke etter
oss uten at noen vet det.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(124): en avviklet kildeversjon UTEN gyldig etterfølger er
`kilde_utlopt`; en som avvikles innen tenantens varselvindu er
`kilde_utloper_snart`; en post lest i et format som siden er lagt om er
`post_mot_utlopt_kilde`; en slettefrist innenfor varselvinduet er
`slettefrist_naermer_seg`; en slettefrist som HAR gått mens raden
fortsatt bærer et navn er `slettefrist_passert`; og en tenant uten
oppbevaringsgrenser er `ingen_krav`.

TO FUNN LUKKES BARE HERFRA. `post_mot_utlopt_kilde` forsvinner når
posten er registrert på nytt mot gjeldende kildeversjon;
`slettefrist_passert` når raden faktisk er ANONYMISERT. Døra
`m50_lukk_funn` nekter et menneske begge — sveipen lukker det som ER
løst, og et menneske kan ikke lukke det som ikke er løst.

`slettefrist_naermer_seg` KAN lukkes av et menneske: «jeg har sett den,
den skal forlenges» er en legitim beslutning om noe som ennå ikke er
brutt.

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
#: INGEN OPPBEVARINGSGRENSE HER, og det er poenget dobbelt opp:
#: journalformatet er KOMMUNENS og ligger i `journalkilde`, og hvor
#: lenge vi kan oppbevare er TENANTENS og ligger i `journalkrav`. En
#: konstant her ville vært en fullmakt modulen ga seg selv over kundens
#: etterlevelse — og over navngitte privatpersoners opplysninger.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to postjournalsveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 509_337_184

#: Antall felt `m50_sveip_postjournal` lover. Fire, som M-46,
#: M-49, M-51, M-55, M-54, M-52 og M-47: modulen har ingen rad å rydde
#: tilsvarende M-48s forlatte reservasjoner.
KONTRAKTFELT = 4


@dataclass
class Sveipresultat:
    tenanter: int = 0
    nye: int = 0
    #: Varsler som alt sto der og bare ble sett på nytt.
    oppdaterte: int = 0
    lukkede: int = 0
    #: INGEN `forlatte`: M-48 rydder forlatte reservasjoner, M-50 har
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
                "SELECT * FROM m50_sveip_postjournal(%s)",
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
