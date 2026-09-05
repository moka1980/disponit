"""M-47 (123) — myndighetssveipen. MODULENS PRODUKT, IKKE ET ANDRE GJERDE.

`disponit-myndighetssveip.timer`, én gang i døgnet, kaller
`m47_sveip_myndighetsplikt(p_grense)`.

FOR DE ANDRE MODULENE ER SVEIPEN ET ANDRE GJERDE. HER ER DEN
PRODUKTET. En plikt som ligger i registeret uten at noen ser på den er
ikke overvåket — den er arkivert. Det er sveipen som gjør den til en
frist noen VET om.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(123): et avviklet regelverk UTEN gyldig etterfølger er
`regelverk_utlopt`; et som avvikles innen tenantens varselvindu er
`regelverk_utloper_snart`; en plikt som hviler på et regelverk som
siden er avviklet er `plikt_mot_utlopt_regelverk`; en frist innenfor
tenantens varselvindu er `frist_naermer_seg`; en frist som HAR gått
uten at noen sendte inn er `frist_passert_uten_bevis`; og en tenant
uten varselfrist er `ingen_krav`.

SVEIPEN SENDER IKKE INN, OG DET ER MODULENS VIKTIGSTE FRAVÆR.

En innsending til en myndighet er BINDENDE og kan ikke kalles tilbake.
Lot vi sveipen sende, ville bindende innsendinger oppstått om natten
uten at noen ba om det. `m47_registrer_bevis` registrerer at et
MENNESKE har sendt inn — den sender ingenting.

MEN HER ER FRAVÆRET IKKE NOK, OG DET SKILLER M-47 FRA KLYNGE 6.

For de fem der var skaden å HANDLE, og avholdenhet var hele svaret.
Her er skaden OGSÅ Å LA VÆRE: en frist som går uten innsending er
nøyaktig det modulen ble bygget for å hindre. En sveip som feiler
stille lar fristen gå — og har forårsaket skaden den skulle avverge.

  EN STILLE M-47 ER VERRE ENN INGEN M-47.

DEN FEILEN ER MÅLT I DETTE HUSET, IKKE TENKT UT. Plattformens
auto-utrulling til staging feilet hver eneste natt fra 4. september, i
fem kjøringer, på samme manglende DSN. Den returnerte feilkode. Ingen
så det. Serveren sto med kode fra flere moduler tilbake mens arbeidet
gikk videre, og det ble oppdaget først da eier spurte om noe helt
annet. Det er `sveipefeil_uten_stoy`, i vår egen drift — og det er
grunnen til at feiltelleren her er en INVARIANT og ikke en
bekvemmelighet.

TO FUNN LUKKES BARE HERFRA. `plikt_mot_utlopt_regelverk` forsvinner
når plikten er registrert på nytt mot gjeldende regelverk;
`frist_passert_uten_bevis` når et BEVIS er registrert. Døra
`m47_lukk_funn` nekter et menneske begge. Forskjellen er hele poenget:
sveipen lukker det som ER løst. Et menneske kan ikke lukke det som ikke
er løst.

`frist_naermer_seg` KAN lukkes av et menneske — «jeg har sett den, jeg
gjør den på fredag» er en legitim beslutning om noe som ennå ikke har
gått galt. Det er skillet mellom en påminnelse og et avvik.

SVEIPEN HENTER HELLER INGEN FRISTER. Innsendingsfristene er
myndighetens, de flyttes, og en modul som lastet dem ned selv ville
tatt ansvaret for at NØYAKTIG de er de gjeldende.

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
#: INGEN VARSELFRIST HER, og det er poenget dobbelt opp: regelverket er
#: MYNDIGHETENS og ligger i `regelverk`, og varselfristen er TENANTENS
#: og ligger i `myndighetskrav`. En konstant her ville vært en fullmakt
#: modulen ga seg selv over kundens forsinkelsesgebyr.

#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to myndighetssveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip.
ARBEIDERNOKKEL = 471_930_662

#: Antall felt `m47_sveip_myndighetsplikt` lover. Fire, som M-46,
#: M-49, M-51, M-55, M-54 og M-52: modulen har ingen rad å rydde
#: tilsvarende M-48s forlatte reservasjoner.
KONTRAKTFELT = 4


@dataclass
class Sveipresultat:
    tenanter: int = 0
    nye: int = 0
    #: Varsler som alt sto der og bare ble sett på nytt.
    oppdaterte: int = 0
    lukkede: int = 0
    #: INGEN `forlatte`: M-48 rydder forlatte reservasjoner, M-47 har
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
                "SELECT * FROM m47_sveip_myndighetsplikt(%s)",
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
