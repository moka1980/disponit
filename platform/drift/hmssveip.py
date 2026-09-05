"""M-53 (127) — HMS-sveipen. TAUSHETEN ER OGSÅ EN LEKKASJE.

`disponit-hmssveip.timer`, én gang i døgnet, kaller
`m53_sveip_hms(p_grense)`.

SVEIPEN VARSLER INGEN MYNDIGHET, OG DEN LUKKER INGEN AVVIK. Denne
fila importerer ingenting som kan snakke ut: ingen `httpx`, ingen
`requests`, ingen `socket`. Arbeidstilsynet får ingenting fra oss i
v1, og et avviksmottak som lukket avvik selv ville vært en
HMS-avdeling uten mennesker i.

MEN SKADEN ER OGSÅ Å LA VÆRE (M-47s dom, 123). Et avvik ingen har
gjort noe med er nøyaktig det modulen ble bygget for å fange, og en
stille sveip er verre enn ingen sveip: noen stolte på at den så etter.

SVEIPEN ANONYMISERER IKKE. Å tømme et navn automatisk ville sett
riktig ut, og vært galt to ganger: sletting er en handling med en
ansvarlig, OG en for tidlig sletting er et brudd på
oppbevaringsplikten. `m53_anonymiser` kalles av et menneske gjennom
flaten. Sveipen SIER FRA at fristen er passert.

TRE FUNN LUKKES IKKE AV ET MENNESKE:

  * `oppbevaring_utlopt` — vi holder en identifiserbar
    HMS-opplysning lenger enn vår egen hjemmel rekker. Lukkes av at
    raden ANONYMISERES.
  * `for_tidlig_anonymisert` — raden forsvant FØR fristen. Som regel
    lovlig (en M-30-sak ga grunnlaget), og fortsatt et hull:
    Arbeidstilsynet spør ikke hvorfor beviset er borte, det spør om
    det er der. Kan aldri lukkes; det er ikke en oppgave, det er det
    eneste sporet av at avviket fantes.
  * `avvik_mot_utlopt_regelverk` — klyngens delte funn. En foreldet
    regel ser nøyaktig ut som en riktig regel.

`avvik_ubehandlet`, `oppbevaring_naermer_seg`, `ingen_krav`,
`regelverk_utlopt` og `regelverk_utloper_snart` KAN
lukkes av et menneske: «jeg har sett den» er en legitim beslutning om
noe som ennå ikke er brutt. 125s vakt sørger for at den lukkingen
står natten over.

Formen er `postjournalsveip.py` sin, ordrett:

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
#: INGEN TILTAKSFRIST OG INGEN OPPBEVARINGSGRENSE HER, og det er
#: poenget dobbelt opp: oppbevaringshjemmelen er LOVENS og ligger i
#: `hmsregelverk`, og hvor lenge et avvik kan stå ubehandlet er
#: TENANTENS og ligger i `hmskrav`. En konstant her ville vært en
#: fullmakt modulen ga seg selv over kundens etterlevelse — og over
#: helseopplysninger etter GDPR art. 9.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to HMS-sveip overlapper aldri. Tallet er modulens
#: eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 610_448_295

#: Antall felt `m53_sveip_hms` lover. Fire, som M-46, M-49, M-51,
#: M-55, M-54, M-52, M-47 og M-50: modulen har ingen rad å rydde
#: tilsvarende M-48s forlatte reservasjoner.
KONTRAKTFELT = 4


@dataclass
class Sveipresultat:
    tenanter: int = 0
    nye: int = 0
    #: Varsler som alt sto der og bare ble sett på nytt.
    oppdaterte: int = 0
    lukkede: int = 0
    #: INGEN `forlatte`: M-48 rydder forlatte reservasjoner, M-53 har
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
                "SELECT * FROM m53_sveip_hms(%s)",
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
