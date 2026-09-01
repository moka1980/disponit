"""M-4 (093) — retensjonsmålingen, husets regnskap over egne lagre.

Formen er `artefaktrydding.py` sin, ORDRETT: advisory-lås, `hoppet_over`
skilt fra BÅDE suksess og feil, alarm etter to sammenhengende feilede
kjøringer, og en batchgrense som begrenser TRANSAKSJONEN, ikke jobben.
Grunnen til at den formen gjentas i stedet for å gjenoppfinnes er den
samme som §6 lærte huset: hver av de tre tingene ble lagt til fordi den
manglet en gang, og en ny driftsjobb som utelater én av dem gjentar
nøyaktig den feilen.

TO TING ER M-4s EGNE:

  * **`statement_timeout` per lager.** Grensen kan ikke bo inne i
    SQL-funksjonen: `statement_timeout` gjelder den YTTERSTE setningen, så
    en per-lager-grense i en plpgsql-løkke ville vært grensen for hele
    løkken. Derfor måler jobben ETT lager per setning, med grensen satt
    på tilkoblingen, og committer hvert skritt for seg.

  * **Et lager som ikke kunne måles blir et FUNN, aldri en null.** Det er
    modulens bærende regel, og den bor her: slår målingen av ett lager
    feil — timeout, manglende grant, en relasjon som forsvant — ruller vi
    tilbake og bokfører `umaalbar` i en EGEN transaksjon. Vi går videre
    til neste lager. En kjøring som stanset på det første tunge lageret
    ville rapportert null for alle de andre, og null er nettopp det
    tallet modulen finnes for å ikke levere.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Maks antall lagre målt per kjøring. Grensen begrenser transaksjonen
#: (ett lager per commit) og kjøringens lengde — ikke hvor mye som til
#: slutt blir målt: neste aktivering tar de eldste først.
BATCHGRENSE = 50
#: Millisekunder per lager. Et lager som ikke lar seg måle innenfor dette
#: er `umaalbar` — et FUNN, ikke et tall.
TIDSGRENSE_MS = 5000
#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Tidsgrensen settes med `set_config`, ikke `SET` — se kjor().
SETT_TIDSGRENSE = "SELECT set_config('statement_timeout', %s, false)"
NULLSTILL_TIDSGRENSE = "SELECT set_config('statement_timeout', '0', false)"
#: Advisory-nøkkel: to målekjøringer overlapper aldri.
ARBEIDERNOKKEL = 409_331_774


@dataclass
class Maaleresultat:
    maaling_id: str | None = None
    malt: int = 0
    umaalbare: int = 0
    ferdig: bool = False
    feilet: bool = False
    alarm_utlost: bool = False
    #: En kjøring som fant arbeidernøkkelen opptatt har verken lyktes
    #: eller feilet — den gjorde ingenting. Skillet må stå PÅ resultatet,
    #: ellers kan ikke kalleren vite at feiltelleren skal stå urørt.
    hoppet_over: bool = False
    umaalbare_lagre: list[str] = field(default_factory=list)


def kjor(conn, *, grense: int = BATCHGRENSE,
         tidsgrense_ms: int = TIDSGRENSE_MS,
         tidligere_feil: int = 0) -> Maaleresultat:
    """Én målekjøring.

    `tidligere_feil` er antall sammenhengende feilede kjøringer FØR
    denne; kalleren (timeren) bærer den telleren mellom kjøringer, siden
    hver kjøring er en egen prosess.
    """
    res = Maaleresultat()
    fikk_lås = conn.execute("SELECT pg_try_advisory_lock(%s)",
                            (ARBEIDERNOKKEL,)).fetchone()[0]
    if not fikk_lås:
        # HOPPET OVER, ikke vellykket. Et rent standardresultat her ser ut
        # som en kjøring som målte null lagre, og kalleren ville da
        # persistert feiltellingen 0 — hvorpå en henger som holder låsen
        # kunne slettet en alt opptelt feil ved hver aktivering, og
        # alarmen etter to sammenhengende feil aldri nådd frem.
        res.hoppet_over = True
        return res
    try:
        for _ in range(grense):
            try:
                # `SET ... = %s` finnes ikke: SET tar ikke parametre i
                # den utvidede protokollen. `set_config` gjør det, og er
                # dermed den eneste veien til en grense fra en variabel
                # som ikke er strenginterpolering inn i en setning.
                conn.execute(SETT_TIDSGRENSE, (str(int(tidsgrense_ms)),))
                rader = conn.execute(
                    "SELECT maaling_id::text, lager_id, utfall, ferdig"
                    "  FROM m4_mal_lagre(1)").fetchall()
                conn.commit()
            except Exception:
                _rull_tilbake(conn)
                # DEN BÆRENDE REGELEN. Lageret som ikke lot seg måle skal
                # stå som funn. Bokføringen skjer i en EGEN transaksjon
                # uten tidsgrense — den skriver én rad og leser ingen.
                if not _bokfor_umaalbar(conn, res):
                    res.feilet = True
                    res.alarm_utlost = \
                        tidligere_feil + 1 >= ALARM_ETTER_FEIL
                    return res
                continue
            if not rader:
                break
            for maaling_id, lager_id, utfall, ferdig in rader:
                res.maaling_id = maaling_id
                if utfall == "malt":
                    res.malt += 1
                elif utfall == "umaalbar":
                    res.umaalbare += 1
                    if lager_id:
                        res.umaalbare_lagre.append(lager_id)
                if ferdig:
                    res.ferdig = True
            if res.ferdig:
                break
        return res
    finally:
        # Opplåsingen er BEST EFFORT. Er tilkoblingen borte, feiler også
        # denne — og et unntak herfra ville erstattet resultatet kalleren
        # skal rapportere og persistere telleren fra. Låsen er
        # sesjonsscopet: en død sesjon slipper den uansett.
        try:
            conn.execute(NULLSTILL_TIDSGRENSE)
            conn.execute("SELECT pg_advisory_unlock(%s)", (ARBEIDERNOKKEL,))
            conn.commit()
        except Exception:
            pass


def _bokfor_umaalbar(conn, res: Maaleresultat) -> bool:
    """Bokfører DET NESTE umålte lageret som `umaalbar`. True ved hell.

    Kalleren vet ikke hvilket lager som nettopp feilet — setningen ble
    avbrutt før den rakk å svare. Den trenger det heller ikke: funksjonen
    velger med SAMME rekkefølge som målingen selv, så lageret den
    bokfører ER lageret som feilet. Å gjette navnet i Python ville vært
    en andre kilde til den samme sannheten.
    """
    try:
        conn.execute(NULLSTILL_TIDSGRENSE)
        rad = conn.execute(
            "SELECT maaling_id::text, lager_id, utfall, ferdig"
            "  FROM m4_mal_lagre(0, true)").fetchone()
        conn.commit()
    except Exception:
        _rull_tilbake(conn)
        return False
    if rad:
        res.maaling_id = rad[0]
        if rad[2] == "umaalbar":
            res.umaalbare += 1
            if rad[1]:
                res.umaalbare_lagre.append(rad[1])
    return True


def _rull_tilbake(conn) -> None:
    """Rollback som aldri kaster. En død tilkobling kan ikke rulles tilbake."""
    try:
        conn.rollback()
    except Exception:
        pass
