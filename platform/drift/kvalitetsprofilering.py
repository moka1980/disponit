"""M-3 (092) — profileringsjobben. `disponit-kvalitetsprofil.timer`, daglig.

Formen er `artefaktrydding.py` sin, ordrett, og det er med vilje: en ny
driftsjobb skal ikke oppfinne en ny måte å være en driftsjobb på.

  * **Advisory-lås.** To profileringer overlapper aldri. En kjøring som
    fant nøkkelen opptatt har verken lyktes eller feilet — den gjorde
    ingenting, og `hoppet_over` står PÅ resultatet slik at kalleren vet
    at feiltelleren skal stå urørt.
  * **Batchgrense.** `m3_profiler(p_grense)` måler høyst så mange regler
    per kjøring. Rakk den ikke gjennom registeret, sier kjøringen
    `avbrutt` — grensen er en egenskap ved runden, ikke en stille
    forkorting av den.
  * **To sammenhengende feilede kjøringer → alarm.** En stille
    kvalitetsmåler er en base ingen måler.
  * **`statement_timeout` på sesjonen FØR kallet.** Dette er det HARDE
    taket rundt hele runden, og det ene laget som faktisk kan avbryte en
    spørring som løper løpsk: `statement_timeout` armes én gang per
    toppnivåsetning, så en `SET LOCAL` inne i `m3_profiler` gjelder ikke
    funksjonens egne setninger (målt, se migrasjonens §7). Per-regel-
    budsjettet inne i funksjonen forkaster en for dyr regel og
    rapporterer den `umaalbar`; dette taket felles HELE runden, og det
    skal det: en runde som løper i timevis er en feilet runde.

DEN BÆRENDE REGELEN, som i M-4: en regel som ikke kunne måles
rapporteres som FUNN, aldri som 0 avvik. Jobben legger ingen logikk oppå
den — regelen eies av basen (`m3_profiler`), og jobben teller ikke selv.
Den bestemmer bare NÅR, hvor lenge og hvor mye per gang.

JOBBEN SKRIVER INGENTING SELV. Den har ett kall og ingen tabellrettighet
i hele basen (rollen `disponit_kvalitetsmaaler`, se migrer.py). Et
kompromittert profileringsverktøy kan telle, og ingenting annet.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Maks antall REGLER målt per kjøring. Registeret er lite og vokser med
#: en migrasjon om gangen; grensen finnes for at en fremtidig
#: registervekst ikke skal gjøre én kjøring vilkårlig lang, og et treff
#: på den er en `avbrutt` runde og ikke en stille forkortet en.
BATCHGRENSE = 50
#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to profileringer overlapper aldri. Egen nøkkel, ikke
#: ryddejobbens — to jobber som deler lås blokkerer hverandre uten at
#: noen av dem har noe med den andre å gjøre.
#:
#: Tallet følger husets familie (`915_774_2xx`, se
#: `domenerevalidering.py` og `artefaktrydding.py`), men slutter på
#: MIGRASJONSNUMMERET og ikke på neste ledige. Grunnen er konkret:
#: klyngen bygges av fem spor samtidig, og «neste ledige» er nøyaktig
#: det alle fem ville valgt. Migrasjonsnumrene er allerede tildelt én
#: gang i `docs/KLYNGE-FUNDAMENT.md`, så de kan ikke kollidere — og en
#: kollisjon her ville ikke korrumpert noe, men fått to urelaterte
#: driftsjobber til å stå i kø for hverandre uten at noen forsto hvorfor.
ARBEIDERNOKKEL = 915_774_292
#: Hardt tak rundt HELE runden (ms). Dagskadens gir god margin; en runde
#: som bruker mer enn dette har møtt noe annet enn data.
RUNDEGRENSE_MS = 120_000
#: Per-regel-budsjettet `m3_profiler` håndhever (ms). Sendes som GUC
#: fordi funksjonssignaturen er låst til `p_grense` alene — og fordi
#: drift da kan stramme den uten en ny migrasjon.
REGELGRENSE_MS = 5_000


@dataclass
class Profilresultat:
    kjoring_id: str | None = None
    antall_regler: int = 0
    antall_umaalbare: int = 0
    antall_funn: int = 0
    #: Rundens egen dom over seg selv: rakk den gjennom registeret?
    avbrutt: bool = False
    feilet: bool = False
    alarm_utlost: bool = False
    #: En kjøring som fant arbeidernøkkelen opptatt har verken lyktes
    #: eller feilet. Skillet må stå PÅ resultatet, ellers kan ikke
    #: kalleren vite at feiltelleren skal stå urørt.
    hoppet_over: bool = False


def kjor(conn, *, grense: int = BATCHGRENSE, tidligere_feil: int = 0,
         rundegrense_ms: int = RUNDEGRENSE_MS,
         regelgrense_ms: int = REGELGRENSE_MS) -> Profilresultat:
    """Én profileringskjøring.

    `tidligere_feil` er antall sammenhengende feilede kjøringer FØR
    denne; kalleren (timeren) bærer den telleren mellom kjøringer, siden
    hver kjøring er en egen prosess.
    """
    res = Profilresultat()
    fikk_lås = conn.execute("SELECT pg_try_advisory_lock(%s)",
                           (ARBEIDERNOKKEL,)).fetchone()[0]
    if not fikk_lås:
        # HOPPET OVER, ikke vellykket. Et rent standardresultat her så ut
        # som en kjøring som målte null regler, og kalleren persisterte
        # da feiltellingen 0 — hver forbigåtte aktivering ville slettet
        # en alt opptelt feil og rapportert suksess uten å ha målt noe.
        res.hoppet_over = True
        return res
    try:
        try:
            # Rammen rundt kallet settes i SAMME transaksjon som kallet.
            # `SET LOCAL` faller bort ved commit/rollback, så en
            # feilende kjøring etterlater ingen innstilling på en
            # gjenbrukt tilkobling.
            # `set_config`, ikke `SET LOCAL ... = %s`: SET er en
            # utility-setning og tar ikke parametre i den utvidede
            # protokollen. Skrevet som SET ville linjen feilt med en
            # syntaksfeil ved FØRSTE ekte kjøring — og en driftsjobb som
            # aldri kommer forbi sin egen første setning er en jobb som
            # ser ut som en databasefeil.
            conn.execute("SELECT set_config('statement_timeout', %s, true)",
                         (str(int(rundegrense_ms)),))
            conn.execute(
                "SELECT set_config('disponit.kvalitet_tidsgrense_ms',"
                " %s, true)", (str(int(regelgrense_ms)),))
            rad = conn.execute("SELECT * FROM m3_profiler(%s)",
                               (grense,)).fetchone()
            conn.commit()
        except Exception:
            _rull_tilbake(conn)
            res.feilet = True
            res.alarm_utlost = tidligere_feil + 1 >= ALARM_ETTER_FEIL
            return res
        if rad is None:
            # Døren returnerer ALLTID nøyaktig én rad. Ingen rad betyr at
            # noe annet enn en måling skjedde, og det er en feilet
            # kjøring — ikke en runde med null regler.
            res.feilet = True
            res.alarm_utlost = tidligere_feil + 1 >= ALARM_ETTER_FEIL
            return res
        res.kjoring_id = str(rad[0])
        res.antall_regler = int(rad[1])
        res.antall_umaalbare = int(rad[2])
        res.antall_funn = int(rad[3])
        res.avbrutt = bool(rad[4])
        return res
    finally:
        # Opplåsingen er BEST EFFORT. Er tilkoblingen borte, feiler også
        # denne — og et unntak herfra ville erstattet resultatet kalleren
        # skal rapportere og persistere telleren fra. Låsen er
        # sesjonsscopet: en død sesjon slipper den når tilkoblingen
        # lukkes.
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
