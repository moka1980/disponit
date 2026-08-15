"""PR-015 §2 — resolverarbeider for daglig domenerevalidering.

`disponit-domenerevalidering.timer`, hver time, egen Unix-bruker, rolle
`disponit_domains_admin`.

**Arbeideren har ingen egen autoritet.** Den slår opp DNS og kaller
`revalider_domenekontroll()`. Statusbeslutningen ligger i databasen: funksjonen
setter KUN `siste_vellykkede_revalidering`, og ferskheten i `v_domeneautorisasjon`
gjør resten. Arbeideren setter aldri status, sletter aldri en rad og markerer
aldri noe `utlopt` — en rad som har gått tre døgn uten svar blir liggende,
synlig, med sin gamle tidsstempel.

Planen er AVLEDET, aldri lagret
------------------------------
    revalideringsminutt(hostname) = int(sha256(hostname)[0:8], 16) mod 1440
    retry-slott: minutt · minutt + 4 t · minutt + 8 t   (jitter ±5 min INNENFOR slottet)

Bootstrap og import spres av seg selv; restore fra backup gir identisk plan
(ingen lagret tilstand å miste); et feilforsøk kan ikke forskyve normalplanen
fordi det ikke finnes noen lagret plan å forskyve. Et vellykket forsøk setter
`siste_vellykkede_revalidering`, og senere slott hopper over raden fordi den er
fersk.

Tre køer, streng prioritet (§2.1)
---------------------------------
    1  Sikkerhetsnett  siste_vellykkede_revalidering < now() - 26 t
                       UTENFOR budsjettet. Aldri utsatt, aldri kappet.
    2  Normalslott     slottet falt i vinduet, raden >= 20 t gammel
    3  Etterslep       slott passert mens timeren var nede, eldste først

Kø 1 er ubegrenset RETT TIL Å BLI PLUKKET, ikke ubegrenset arbeid: oppslagene
kjøres med fast samtidighetsgrense C = 8. Ingen rad droppes. Overskrider kø 1
budsjettet, er det en MÅLT hendelse (`sikkerhetsnett.kjoringer_over_K`), ikke en
feil.

Budsjettet (§2.2) er absolutt for kø 2 + kø 3 samlet:

    N = antall rader med status IN ('verifisert','avklaring_kreves')
    K = ceil(0.10 * N)

K håndheves med `LIMIT`, ikke som forventning. Rader fra kø 2 som ikke får plass
blir etterslep og plukkes neste kjøring — slottet er avledet, så ingenting
mistes. Hashskjevhet påvirker hvor mye etterslep som oppstår, men kan aldri
bryte K.

Invariant vs. målt (§2.3): at kø 2 + kø 3 aldri overskrider K er GARANTERT av
scheduleren. Hvor jevnt radene faktisk fordeler seg er en MÅLT driftsegenskap —
`sha256 mod 1440` er tilnærmet uniform, men garanterer ikke at ingen time får
> 10 % av en vilkårlig populasjon. Skjevhet er observasjon, aldri sikkerhetsbevis.
"""
from __future__ import annotations

import hashlib
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Sequence

#: Andel av populasjonen som kan revalideres per kjøring (kø 2 + kø 3).
TAK_ANDEL = 0.10
#: Sikkerhetsnettet: ingen verifisert rad skal gå lenger enn dette uten svar.
SIKKERHETSNETT_TIMER = 26
#: En rad som er ferskere enn dette plukkes ikke av normalslottet.
NORMAL_ALDER_TIMER = 20
#: Fast samtidighetsgrense for DNS-oppslag (gjelder ALLE køer, også kø 1).
SAMTIDIGHET = 8
#: Normalslott + to retry-slott, i timer etter normalslottet.
RETRY_OFFSET_TIMER = (0, 4, 8)
#: Jitter innenfor slottet. Sprer oppslagene; flytter aldri en rad ut av slottet.
JITTER_MINUTTER = 5
#: Minutter i døgnet.
DOGN_MINUTTER = 1440
#: Bred resolverfeil: over denne andelen innen én kjøring gir ÉN driftsalarm.
ALARM_ANDEL = 0.20
#: Advisory-nøkkel for arbeideren. To kjøringer overlapper aldri — også en
#: manuell kjøring tar den samme låsen (§7 «alle veier inn»).
ARBEIDERNOKKEL = 915_774_201


def revalideringsminutt(hostname: str) -> int:
    """Minuttet i døgnet raden normalt revalideres på. Ren funksjon av navnet.

    Ingenting lagres: samme hostname gir samme minutt på enhver maskin, i
    ethvert miljø, og etter enhver restore. Det er nettopp derfor en restore fra
    backup gir identisk plan (port 8) — det finnes ingen plan å gjenopprette.
    """
    fordøyd = hashlib.sha256(hostname.encode("utf-8")).hexdigest()
    return int(fordøyd[0:8], 16) % DOGN_MINUTTER


def slott_minutter(hostname: str) -> tuple[int, int, int]:
    """De tre slottene: normalslott, +4 t, +8 t (alle mod døgnet)."""
    m = revalideringsminutt(hostname)
    return tuple((m + t * 60) % DOGN_MINUTTER for t in RETRY_OFFSET_TIMER)  # type: ignore[return-value]


def jitter_minutt(hostname: str, slott: int) -> int:
    """Deterministisk jitter ±5 min INNENFOR slottet.

    Deterministisk, ikke tilfeldig: en tilfeldig jitter ville gjort planen
    uforutsigbar mellom to kjøringer og dermed umulig å verifisere mot port 8.
    Utledes av (hostname, slott), så de tre slottene til samme rad jitrer ulikt.
    """
    frø = hashlib.sha256(f"{hostname}:{slott}".encode("utf-8")).hexdigest()
    spenn = 2 * JITTER_MINUTTER + 1
    return int(frø[8:16], 16) % spenn - JITTER_MINUTTER


# `revalideringsminutt` gjentatt i SQL, byte for byte. get_byte er 0-indeksert og
# big-endian-sammensetningen her ER `int(sha256(h).hexdigest()[0:8], 16)` — uten
# omveien om bit(32)::bigint, som gir NEGATIVE tall når høyeste bit er satt og
# dermed ville flyttet ~halve populasjonen til feil minutt. `mod()` og ikke `%`:
# operatoren kolliderer med psycopgs parameterplassholdere.
#
# Dette uttrykket er testet mot Python-utgaven rad for rad
# (`test_sql_minutt_er_identisk_med_python_minutt`). Divergerer de to, plukker
# scheduleren andre rader enn den rapporterer, og begge deler ser riktige ut
# hver for seg.
_MINUTT_SQL = """
    mod(get_byte(sha256(convert_to(hostname, 'UTF8')), 0)::BIGINT * 16777216
      + get_byte(sha256(convert_to(hostname, 'UTF8')), 1)::BIGINT * 65536
      + get_byte(sha256(convert_to(hostname, 'UTF8')), 2)::BIGINT * 256
      + get_byte(sha256(convert_to(hostname, 'UTF8')), 3)::BIGINT, 1440)
"""


class Diversitetsfeil(RuntimeError):
    """Resolverkonfigurasjonen bryter diversitetskravet. Oppstart nektes."""


@dataclass(frozen=True)
class Resolver:
    """Én navnetjener, med det som gjør den uavhengig av de andre.

    `operator` og `nett` er det diversitetsporten måler. To resolvere hos samme
    operatør er én feilkilde med to adresser — de teller som én.
    """
    navn: str
    operator: str
    nett: str
    slå_opp: Callable[[str], frozenset[str]]


@dataclass
class Revalideringsresultat:
    """Alt porten og evidensgrensen måler. Tellere, ikke bestått/ikke bestått."""
    plukket_ko1: int = 0
    plukket_ko2: int = 0
    plukket_ko3: int = 0
    budsjett_K: int = 0
    populasjon_N: int = 0
    vellykket: int = 0
    uenige_resolvere: int = 0
    oppslagsfeil: int = 0
    alarm_utlost: bool = False
    maks_samtidighet: int = 0
    fordeling_per_time: dict[int, int] = field(default_factory=dict)

    @property
    def ko2_pluss_ko3(self) -> int:
        return self.plukket_ko2 + self.plukket_ko3

    @property
    def kjoring_over_K(self) -> bool:
        """Sant når sikkerhetsnettet dyttet TOTALEN over K.

        Dette er en MÅLT hendelse (§2.1), ikke en feil: kø 1 kappes aldri.
        Invarianten gjelder kø 2 + kø 3, som `ko2_pluss_ko3 <= budsjett_K`.
        """
        return self.plukket_ko1 + self.ko2_pluss_ko3 > self.budsjett_K


def krev_diversitet(resolvere: Sequence[Resolver]) -> None:
    """Deploy-port (§2.4): minst to resolvere hos ULIKE operatører og ULIKE nett.

    Kalles ved oppstart. Bryter konfigurasjonen kravet, nektes oppstart — en
    arbeider som kjører videre med to resolvere hos samme operatør ville
    rapportert «to uavhengige kilder er enige» om noe som er én kilde.
    """
    if len(resolvere) < 2:
        raise Diversitetsfeil(
            f"resolverkonfigurasjon: {len(resolvere)} resolver(e) — krever minst 2")
    if len({r.operator for r in resolvere}) < 2:
        raise Diversitetsfeil(
            "resolverkonfigurasjon: alle resolvere hos samme operatør "
            f"({sorted({r.operator for r in resolvere})}) — krever minst 2 ulike")
    if len({r.nett for r in resolvere}) < 2:
        raise Diversitetsfeil(
            "resolverkonfigurasjon: alle resolvere på samme nett "
            f"({sorted({r.nett for r in resolvere})}) — krever minst 2 ulike")


def enige(resolvere: Sequence[Resolver], hostname: str) -> bool:
    """≥2 uavhengige resolvere må gi SAMME svar (§2.4).

    Uenighet → ikke vellykket revalidering. Ikke «flertallet vinner»: to kilder
    som sier ulike ting om en DNS-sone er nettopp tilfellet der vi ikke vet, og
    da skal `siste_vellykkede_revalidering` stå urørt. Et oppslag som KASTER
    teller som uenighet — «vi fikk ikke svar» er ikke «svaret var ja».
    """
    svar = []
    for r in resolvere:
        try:
            svar.append(r.slå_opp(hostname))
        except Exception:
            return False
    if len(svar) < 2:
        return False
    return all(s == svar[0] for s in svar[1:])


def kandidater(conn, minutt_fra: int, minutt_til: int, K: int
               ) -> list[tuple[str, str, int]]:
    """(tenant, hostname, kø) fra DB-scheduleren.

    Utvalget ligger i `revalideringskandidater()` (migrasjon 019), ikke her:
    `domenekontroll` er RLS-scopet, og en arbeider som leste gjennom RLS ville
    sett én tenant om gangen og regnet budsjettet på feil nevner. Der ligger
    også `LIMIT`, slik at K-invarianten ikke kan brytes av en endring i denne
    orkestreringen.
    """
    return conn.execute(
        "SELECT tenant, hostname, ko FROM revalideringskandidater(%s,%s,%s,%s,%s)",
        (minutt_fra, minutt_til, K, SIKKERHETSNETT_TIMER,
         NORMAL_ALDER_TIMER)).fetchall()


def budsjett(conn) -> tuple[int, int]:
    """(N, K). K = ceil(0.10 * N), hardt tak for kø 2 + kø 3 SAMLET."""
    N = int(conn.execute("SELECT revalideringspopulasjon()").fetchone()[0])
    return N, math.ceil(TAK_ANDEL * N)


def kjor(conn, resolvere: Sequence[Resolver], *, aktor: str = "domenerevalidering",
         naa_minutt: int | None = None,
         samtidighet: int = SAMTIDIGHET) -> Revalideringsresultat:
    """Én kjøring. Tar arbeidernøkkelen — to kjøringer overlapper aldri.

    Låsen er SESJONSscopet og tas med `pg_try_advisory_lock`: kjører en annen
    instans allerede, returnerer denne umiddelbart med et tomt resultat i
    stedet for å stå i kø. En time senere kommer neste kjøring uansett, og
    planen er avledet — ingenting mistes av å hoppe over.
    """
    res = Revalideringsresultat()
    fikk_lås = conn.execute("SELECT pg_try_advisory_lock(%s)",
                            (ARBEIDERNOKKEL,)).fetchone()[0]
    if not fikk_lås:
        return res
    try:
        res.populasjon_N, res.budsjett_K = budsjett(conn)

        if naa_minutt is None:
            naa_minutt = int(conn.execute(
                "SELECT (EXTRACT(HOUR FROM now())*60 + EXTRACT(MINUTE FROM now()))::INT"
            ).fetchone()[0])
        fra = (naa_minutt - 60) % DOGN_MINUTTER

        rader = kandidater(conn, fra, naa_minutt, res.budsjett_K)
        res.plukket_ko1 = sum(1 for r in rader if r[2] == 1)
        res.plukket_ko2 = sum(1 for r in rader if r[2] == 2)
        res.plukket_ko3 = sum(1 for r in rader if r[2] == 3)

        # INVARIANT, ikke forventning: kø 2 + kø 3 kan ikke overskride K.
        # Assert-en er billig og fanger en fremtidig LIMIT-regresjon der den
        # oppstår, ikke tre lag lenger ute i en evidensrapport.
        assert res.ko2_pluss_ko3 <= res.budsjett_K, (
            f"budsjettbrudd: {res.ko2_pluss_ko3} > K={res.budsjett_K}")

        alle = [(t, h) for t, h, _ in rader]
        for _, h in alle:
            time = revalideringsminutt(h) // 60
            res.fordeling_per_time[time] = res.fordeling_per_time.get(time, 0) + 1

        _utfor(conn, alle, resolvere, aktor, res, samtidighet)

        # Bred feil dedupliserer VARSLINGEN, ikke tilstanden (§2.4): ingen
        # M-37-sak opprettes, ingen rad endres, og `tenant X / hostname Y` er
        # fortsatt individuelt synlig med sine tre døgn uten suksess.
        feilet = res.uenige_resolvere + res.oppslagsfeil
        if alle and feilet / len(alle) > ALARM_ANDEL:
            res.alarm_utlost = True
        return res
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (ARBEIDERNOKKEL,))


def _utfor(conn, rader, resolvere, aktor, res: Revalideringsresultat,
           samtidighet: int) -> None:
    """DNS-oppslagene med fast samtidighetsgrense; DB-kallene serielt.

    Oppslagene er I/O og parallelliseres — men aldri mer enn C samtidig, heller
    ikke for kø 1 (port 10b). `revalider_domenekontroll()` kalles deretter
    serielt på ÉN tilkobling: psycopg-tilkoblinger er ikke trådsikre, og en
    delt tilkobling ville gjort «to kjøringer overlapper aldri» til en løgn
    inne i én kjøring.
    """
    if not rader:
        return
    aktive = 0
    topp = 0

    def slå_opp(rad):
        nonlocal aktive, topp
        aktive += 1
        topp = max(topp, aktive)
        try:
            return rad, enige(resolvere, rad[1])
        finally:
            aktive -= 1

    with ThreadPoolExecutor(max_workers=samtidighet) as pool:
        svar = list(pool.map(slå_opp, rader))
    res.maks_samtidighet = topp

    for (tenant, hostname), ok in svar:
        if not ok:
            res.uenige_resolvere += 1
            continue
        try:
            # Tenantkonteksten er TRANSAKSJONSLOKAL (`set_config(..., true)`) og
            # settes derfor på nytt for hver rad — commiten under nullstiller
            # den. Det er riktig vei rundt: en kontekst som overlevde commiten,
            # ville latt neste rads kall arve forrige tenants RLS-vindu.
            conn.execute(
                "SELECT set_config('disponit.tenant',%s,true),"
                "       set_config('disponit.aktor',%s,true)",
                (tenant, aktor))
            conn.execute("SELECT revalider_domenekontroll(%s,%s,%s)",
                         (tenant, hostname, aktor))
            conn.commit()
            res.vellykket += 1
        except Exception:
            # Raden ble tilbakekalt/overtatt mellom plukk og kall. Funksjonen
            # nekter da å registrere en revalidering (016), og det er riktig:
            # arbeideren skal ikke påstå suksess etter at autorisasjonen er
            # trukket. Telles som oppslagsfeil, ikke som uenighet.
            conn.rollback()
            res.oppslagsfeil += 1
