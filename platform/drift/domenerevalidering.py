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
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Sequence

import psycopg

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


def enig_svar(resolvere: Sequence[Resolver],
              hostname: str) -> frozenset[str] | None:
    """Den TXT-mengden ≥2 uavhengige resolvere er ENIGE om, ellers None (§2.4).

    Uenighet → ikke vellykket revalidering. Ikke «flertallet vinner»: to kilder
    som sier ulike ting om en DNS-sone er nettopp tilfellet der vi ikke vet, og
    da skal `siste_vellykkede_revalidering` stå urørt. Et oppslag som KASTER
    teller som uenighet — «vi fikk ikke svar» er ikke «svaret var ja».

    Selve VERDIEN returneres, ikke bare enigheten: enighet alene er ikke
    kontrollbevis (en sone med en hvilken som helst stabil TXT-verdi gir full
    enighet lenge etter at utfordringen er fjernet). Verdiene sendes videre
    til `revalider_domenekontroll(...,TEXT[])`, som holder dem mot den lagrede
    utfordringen. Se migrasjon 019 §3.35.
    """
    svar = []
    for r in resolvere:
        try:
            svar.append(r.slå_opp(hostname))
        except Exception:
            return None
    if len(svar) < 2:
        return None
    if not all(s == svar[0] for s in svar[1:]):
        return None
    return frozenset(svar[0])


#: EIERNAVNET UTFORDRINGEN LIGGER PÅ (Codex P1).
#:
#: Beviset lå før på selve vertsnavnet. For et typisk `www.dittfirma.no` er
#: det et navn kunden IKKE kan legge en TXT-post på: eieren av et CNAME kan
#: per RFC 1034 §3.6.2 ikke ha andre poster ved siden av seg, og et rekursivt
#: TXT-oppslag følger aliaset til målsonen — en leverandørs sone, ikke
#: kundens. Selvbetjeningen ber alltid om NØYAKTIG vertsnavnet
#: (`wildcard=false`), så å bevise apex i stedet er ingen vei rundt: slike
#: nettsteder kunne aldri blitt verifisert.
#:
#: Et eget underetikett-navn kan alltid bære posten, og ligger i kundens egen
#: sone selv når vertsnavnet er et alias. Understreken er med vilje: `_`-navn
#: er reservert for tjenestebruk (RFC 8552) og kolliderer ikke med et
#: vertsnavn noen har.
UTFORDRINGSPREFIKS = "_disponit-challenge"

#: DNS-navnegrensen: 253 tegn i presentasjonsform, uten avsluttende punktum.
#: Samme tall som `er_kanonisk_hostname` (018) gjerder vertsnavnet med.
MAKS_DNS_NAVN = 253

#: Lengste VERTSNAVN som kan bære en utfordring (Codex P2).
#:
#: Utfordringsnavnet er lengre enn vertsnavnet, og grensen gjelder navnet som
#: faktisk slås opp. Et vertsnavn på 234–253 tegn er selv fullt lovlig — 018
#: tar det, basen lagrer det — men `_disponit-challenge.` foran gir et navn
#: over 253, altså et navn kunden ikke KAN publisere og arbeideren aldri kan
#: finne. Utstedelsen svarte likevel 201 med en oppskrift som så riktig ut,
#: og domenet ble stående uverifisert for alltid uten at noe sa hvorfor.
#:
#: Prefiksplassen reserveres derfor i valideringen, og den regnes HER, av
#: prefikset selv: byttes prefikset, flytter grensen seg med det. Porten i
#: `test_domene_selvbetjening` holder API-ets kopi mot denne.
MAKS_UTFORDRET_VERTSNAVN = MAKS_DNS_NAVN - len(UTFORDRINGSPREFIKS) - 1


def utfordringsnavn(hostname: str) -> str:
    """Eiernavnet utfordrings-TXT-en skal publiseres på for `hostname`.

    ÉN kilde til denne sannheten, brukt av alle tre som må være enige om den:
    utstedelsen (`POST /v1/domener` svarer med navnet), førstegangs-
    verifiseringen og revalideringen. Blir de uenige, publiserer kunden på ett
    navn og arbeideren leter på et annet — og domenet står «ikke verifisert»
    uten at noe sier hvorfor. Porten som holder API-svaret mot denne
    funksjonen ligger i `test_domene_selvbetjening`.
    """
    return f"{UTFORDRINGSPREFIKS}.{hostname}"


def utfordringssvar(resolvere: Sequence[Resolver], hostname: str, *,
                    ogsa_vertsnavnet: bool) -> frozenset[str] | None:
    """`enig_svar` for utfordringsnavnet — og for ARVEN, når den gjelder.

    `ogsa_vertsnavnet` skiller de to veiene inn:

    * FØRSTEGANGSVERIFISERINGEN (039) er ny med denne endringen. Kontrakten
      er `txt_navn` i utstedelsessvaret, og det navnet er utfordringsnavnet —
      det finnes ingen kunde som har publisert noe annet sted. Ett oppslag per
      rad, så tidsbudsjettet bak `VERIFISERING_TAK` står urørt.
    * REVALIDERINGEN møter rader som ble verifisert FØR dette navnet fantes,
      med beviset på det bare vertsnavnet. Leter den bare på det nye navnet,
      ville hver eneste av dem mistet autorisasjonen ved neste kjøring. Derfor
      slås begge opp og mengdene slås sammen. Det svekker ingenting: beviset
      er en sha256 databasen holder mot `challenge_token_hash`, så et ekstra
      navn i søket kan ikke fabrikere et treff — det kan bare finne det der
      det faktisk ble lagt.

    Er ETT av oppslagene uenig/nede mens det andre svarer, brukes svaret som
    kom. «Vi fikk ikke kontakt med det ene navnet» skal ikke kunne rive et
    bevis vi faktisk fant på det andre. Er BEGGE None, er svaret None —
    uenighet, ikke «ingen post».
    """
    navn = [utfordringsnavn(hostname)]
    if ogsa_vertsnavnet:
        navn.append(hostname)
    funnet = [s for s in (enig_svar(resolvere, n) for n in navn)
              if s is not None]
    if not funnet:
        return None
    return frozenset().union(*funnet)


def enige(resolvere: Sequence[Resolver], hostname: str) -> bool:
    """Er resolverne enige? Ren enighetsprøve — sier INGENTING om kontroll.

    Beholdt fordi §2.4-diversiteten og enighetsregelen er én ting og
    bevisprøven en annen; kallveien i `_utfor` bruker `enig_svar`, som også
    bærer verdien beviskontrollen trenger.
    """
    return enig_svar(resolvere, hostname) is not None


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

    Codex (P2): resultatene KONSUMERES etter hvert som de fullføres, ikke
    materialiseres først. Kø 1 er bevisst ubegrenset, og unit-filen gir
    kjøringen 45 minutter; ventet vi på hele populasjonen før første commit,
    kunne en stor eller treg kohort bli drept på timeout uten at ÉN eneste rad
    var skrevet — og neste time ville startet på null igjen, i det uendelige.
    Med `as_completed` er hver ferdig revalidering committet i det den er
    ferdig, så et avbrudd koster de radene som ennå ikke er slått opp, ikke
    hele kjøringen. Oppslagene fortsetter i bakgrunnen mens DB-kallet gjøres:
    samtidighetsgrensen C gjelder oppslagene, og DB-en har fortsatt nøyaktig
    én skriver.
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
            # ARVEN TAS MED (Codex P1): rader som ble verifisert før
            # utfordringsnavnet fantes, har beviset på det bare vertsnavnet.
            return rad, utfordringssvar(resolvere, rad[1],
                                        ogsa_vertsnavnet=True)
        finally:
            aktive -= 1

    with ThreadPoolExecutor(max_workers=samtidighet) as pool:
        ventende = [pool.submit(slå_opp, rad) for rad in rader]
        for ferdig in as_completed(ventende):
            (tenant, hostname), txt = ferdig.result()
            _skriv_resultat(conn, tenant, hostname, txt, aktor, res)
    res.maks_samtidighet = topp


def _skriv_resultat(conn, tenant, hostname, txt, aktor,
                    res: Revalideringsresultat) -> None:
    """Én ferdig rad: commit eller rollback, og tellerne som følger med."""
    if txt is None:
        res.uenige_resolvere += 1
        return
    try:
        # Tenantkonteksten er TRANSAKSJONSLOKAL (`set_config(..., true)`) og
        # settes derfor på nytt for hver rad — commiten under nullstiller
        # den. Det er riktig vei rundt: en kontekst som overlevde commiten,
        # ville latt neste rads kall arve forrige tenants RLS-vindu.
        conn.execute(
            "SELECT set_config('disponit.tenant',%s,true),"
            "       set_config('disponit.aktor',%s,true)",
            (tenant, aktor))
        # Bevisformen (019 §3.35), ikke 016s 3-argumentsform: den
        # avtalte TXT-mengden sendes MED, og databasen holder den mot
        # `challenge_token_hash`. Enighet alene er ikke kontrollbevis —
        # en sone som fortsatt har en hvilken som helst stabil TXT-verdi
        # (SPF) gir full enighet lenge etter at utfordringen er borte.
        conn.execute("SELECT revalider_domenekontroll(%s,%s,%s,%s)",
                     (tenant, hostname, aktor, sorted(txt)))
        conn.commit()
        res.vellykket += 1
    except Exception:
        # To tilfeller, samme svar: raden ble tilbakekalt/overtatt mellom
        # plukk og kall (016 nekter da å registrere revalideringen), eller
        # beviset manglet i TXT-svaret. Begge betyr «ingen bevist kontroll
        # nå», og arbeideren skal ikke påstå suksess etter at
        # autorisasjonen er trukket. Telles som oppslagsfeil, ikke som
        # uenighet — resolverne var jo enige.
        conn.rollback()
        res.oppslagsfeil += 1

# ---------------------------------------------------------------------------
# 039 — førstegangsverifisering av selvbetjente challenges
# ---------------------------------------------------------------------------

#: Egen arbeidernøkkel: verifiseringspasset er lite og hyppig (5 min) og
#: skal ikke vente på — eller blokkere — den timeplanlagte revalideringen.
VERIFISERINGSNOKKEL = 915_774_203

#: DE FORVENTEDE UTFALLENE, navngitt (Codex P2).
#:
#: Et `except Exception` rundt bekreftelseskallet gjorde ALT til «ikke bevist»:
#: en funksjon som ikke er utrullet, et grant eller et eierskap som er feil, en
#: programmeringsfeil i SQL-en. Hver rad ble rullet tilbake, telleren gikk opp,
#: og `main()` returnerte 0 — så systemd noterte et vellykket pass mens
#: HVER ENESTE challenge sto ubehandlet, i det uendelige, uten en rød unit.
#:
#: `bekreft_domenechallenge` reiser `invalid_parameter_value` for de tre
#: ordinære neiene (raden finnes ikke, utfordringen er utløpt/aldri utstedt,
#: beviset står ikke i TXT-svaret); `no_data_found` er formen 016-veiene
#: bruker for «raden er borte». Alt annet er ikke et svar om DNS-bevis, og
#: skal felle oneshot-unitten.
MANGLENDE_BEVIS = (psycopg.errors.InvalidParameterValue,
                   psycopg.errors.NoDataFound)

#: Kappløpene: en annen tenant verifiserte samme hostname i det vi skrev
#: (delindeksen `en_verifisert_per_hostname`), eller låsingen kolliderte.
#: Forventet under samtidighet, og riktig svar er «prøv igjen neste pass» —
#: men det er ikke det samme som at beviset manglet, og skal ikke telles der.
KAPPLOP = (psycopg.errors.UniqueViolation,
           psycopg.errors.SerializationFailure,
           psycopg.errors.DeadlockDetected,
           psycopg.errors.LockNotAvailable)

#: FRISTEN ER PASSETS, IKKE SYSTEMDS (Codex P2).
#:
#: `disponit-domeneverifisering.service` gir kjøringen 4 minutter
#: (TimeoutStartSec). Passet stanser derfor seg selv godt innenfor, og det gjør
#: det MELLOM to ferdige oppslag — det ene punktet der ingenting er halvveis
#: skrevet. Resten av køen står `ventende` og tas av neste timerkjøring om fem
#: minutter; køen ER tilstanden, og en oneshot som stanser er ikke en jobb som
#: mislyktes. TimeoutStartSec blir da et sikkerhetsnett som ikke skal utløses.
#: Tallene hører sammen og skal endres sammen.
VERIFISERING_FRIST_S = 180

#: Batchtaket per pass. UTLEDET, ikke valgt: med C = `SAMTIDIGHET` oppslag i
#: parallell og et verste tilfelle på ~10 s per hostname (to resolvere à 5 s
#: levetid, serielt i `enig_svar`) bruker et fullt tak
#: ceil(100/8) · 10 s = 130 s — innenfor fristen, med margin for DB-skrivingene.
#: Taket lå før på 200 SERIELLE oppslag, altså opptil 2000 s mot en unit som
#: dør etter 240: en kohort med trege navn foran i `challenge_utstedt`-
#: rekkefølgen spiste hele vinduet, neste kjøring plukket de SAMME radene, og
#: kundene bak dem ble sultet til utfordringen deres utløp.
#:
#: Taket ALENE er likevel bare et nytt gjerde (Codex P1): står det flere gyldige
#: utfordringer enn taket og de eldste kundene aldri publiserer TXT-posten sin,
#: er «de eldste først» de SAMME radene hver kjøring — en manglende post flytter
#: jo ingenting. Derfor roterer plukket: `ventende_domenechallenges` stempler
#: radene den returnerer (`challenge_forsokt`, 039) og tar de minst nylig
#: forsøkte først, så hele populasjonen kommer gjennom taket. Taket bestemmer
#: hvor mye ETT pass rekker; stempelet bestemmer at det blir en ANNEN kohort
#: neste gang.
VERIFISERING_TAK = 100


def kjor_ventende(conn, resolvere, *, aktor: str = "domeneverifisering",
                  grense: int = VERIFISERING_TAK,
                  samtidighet: int = SAMTIDIGHET,
                  frist_s: float = VERIFISERING_FRIST_S) -> dict:
    """Ett verifiseringspass: plukk challenges kryss-tenant
    (`ventende_domenechallenges`, 039), slå opp TXT med samme
    diversitetskrav som revalideringen (≥2 enige, uavhengige resolvere),
    og la DATABASEN holde svaret mot hashen (`bekreft_domenechallenge`).

    Plukket er `ventende` OG den M-37-AVVISTE kandidaten (`tilbakekalt` med
    motpart) — den siste beholder statusen sin hele veien, for det er nettopp
    den 018 kjenner igjen som en reapplikasjon. Hennes bevis fører derfor til
    en ny avklaringsgenerasjon (`konflikt:*`), aldri til `verifisert`.

    Arbeideren kan ikke fabrikere et bevis: funksjonen sammenligner
    sha256 av de fergede TXT-verdiene mot `challenge_token_hash` den selv
    lagrer, og statusovergangen eies av `verifiser_domenekontroll` med
    alle avklarings-/overtakelsesportene urørt.

    `konflikt:*`-svar TELLES OG NAVNGIS, men saken opprettes ikke herfra:
    `opprett_overtakelsessak` krypterer payloaden med tenantens DEK og
    skriver `revisjonslogg` + `unntak` — runtime-autoritet med
    nøkkelmateriale, som denne rollen med vilje ikke har.

    Den blir likevel opprettet (Codex P1). Konflikten er ikke en melding
    som må videreformidles, men en TILSTAND: raden står `avklaring_kreves`
    med `konflikt_motpart`, og M-37-arbeideren — som HAR både DEK og
    runtime-DML — drenerer nøyaktig de radene til saker
    (`sikre_ventende_overtakelsessaker`, migrasjon 039). Loggposten her er
    derfor et driftsspor, ikke den eneste sporen av konflikten: dør denne
    prosessen rett etter commiten, finner dreneringen raden uansett.

    Bare de FORVENTEDE utfallene fanges per rad (`MANGLENDE_BEVIS`,
    `KAPPLOP`). En manglende funksjon, et feil grant eller en SQL-feil er
    ikke «ikke bevist» — den slipper ut og feller unitten, for et pass som
    rapporterer 0 mens ingenting ble behandlet er verre enn et rødt pass.

    Oppslagene kjøres med fast samtidighetsgrense og passet har sin EGEN
    frist — se `_verifiser_rader`.
    """
    res = {"plukket": 0, "verifisert": 0, "konflikt": 0, "uenige": 0,
           "ikke_bevist": 0, "kapplop": 0, "annet": 0, "ubehandlet": 0}
    fikk = conn.execute("SELECT pg_try_advisory_lock(%s)",
                        (VERIFISERINGSNOKKEL,)).fetchone()[0]
    if not fikk:
        res["hoppet_over"] = True
        return res
    try:
        rader = conn.execute(
            "SELECT tenant, hostname FROM ventende_domenechallenges(%s)",
            (grense,)).fetchall()
        # COMMIT, ikke rollback (Codex P1). Plukket STEMPLER radene
        # (`challenge_forsokt`, 039) — det er stempelet som gjør utvalget til
        # en roterende kø i stedet for de samme eldste radene hvert femte
        # minutt. Rulles det tilbake, er taket igjen et gjerde kundene bak
        # aldri kommer forbi. Egen transaksjon, før oppslagene: bekreftelsene
        # under committer én rad om gangen og skal ikke kunne dra plukket med
        # seg i en rollback.
        conn.commit()
        res["plukket"] = len(rader)
        _verifiser_rader(conn, rader, resolvere, aktor, res, samtidighet,
                         frist_s)
        # BRED RESOLVERFEIL ER EN ALARM, ikke en teller (Codex P2). Samme
        # terskel og samme kontrakt som revalideringens §2.4-alarm: uten en
        # konsument var `uenige` et felt ingen leser, og et pass der BEGGE
        # resolverne var nede så nøyaktig ut som et pass der ingen kunde ennå
        # hadde lagt ut TXT-posten sin — begge «vellykket», mens hver eneste
        # selvbetjening sto stille.
        #
        # Nevneren er radene som faktisk BLE slått opp: rader vi ikke rakk før
        # fristen sier ingenting om resolverne. Og `uenige` betyr her nettopp
        # transportsvikt eller uenighet — et autoritativt «ingen TXT-post»
        # bæres som et TOMT svar (`_txt_oppslag`) og teller som `ikke_bevist`,
        # som er kundens normaltilstand rett etter utstedelsen.
        res["vurdert"] = res["plukket"] - res["ubehandlet"]
        res["alarm_utlost"] = bool(
            res["vurdert"] and res["uenige"] / res["vurdert"] > ALARM_ANDEL)
        return res
    finally:
        # ROLLBACK FØRST (Codex P2). Slipper en uventet feil ut av løkka, står
        # transaksjonen abortert, og et `SELECT` her ville feilet med
        # InFailedSqlTransaction og MASKERT den egentlige årsaken — nøyaktig
        # den feilen unitten skal melde. Låsen er sesjonsscopet og overlever
        # rollbacken, så den skal fortsatt slippes; er forbindelsen borte, dør
        # den med sesjonen uansett, og da er det ikke opplåsingen som er
        # nyheten.
        try:
            conn.rollback()
            conn.execute("SELECT pg_advisory_unlock(%s)",
                         (VERIFISERINGSNOKKEL,))
            conn.commit()
        except psycopg.Error:
            pass


def _verifiser_rader(conn, rader, resolvere, aktor, res: dict,
                     samtidighet: int, frist_s: float) -> None:
    """Oppslagene med fast samtidighetsgrense; DB-kallene serielt (Codex P2).

    Passet var SERIELT: opptil 200 hostnames etter hverandre, hvert med
    resolverkall som hver har fem sekunders levetid, mot en unit som dør etter
    fire minutter. En kohort med trege eller tidsavbrutte navn foran i
    `challenge_utstedt`-rekkefølgen spiste hele vinduet før de friske bak dem
    ble nådd — og fordi utvalget alltid tar de ELDSTE først, plukket neste
    kjøring nøyaktig de samme radene. Kundene bak dem ble sultet helt til
    utfordringen deres utløp.

    Tre ting løser det, og de virker sammen:

    1. `SAMTIDIGHET` parallelle oppslag, samme grense og samme grunn som
       `_utfor`: oppslagene er I/O, DB-en har fortsatt nøyaktig én skriver.
    2. `as_completed`, ikke innsendingsrekkefølge. De FRISKE navnene fullfører
       først og skrives først; et navn som står og venter på timeout blokkerer
       ikke lenger noen bak seg. Det er dette som fjerner selve sultingen —
       fristen under er bare et nett.
    3. Passets EGEN frist, sjekket MELLOM to ferdige oppslag: det ene punktet
       der ingenting er halvveis skrevet. Radene vi ikke rakk telles som
       `ubehandlet` og står `ventende` til neste kjøring om fem minutter.
       Traff systemd-timeouten i stedet, ville et SIGTERM landet hvor som
       helst — også mellom bekreftelsen og commiten.
    """
    if not rader:
        return
    frist = time.monotonic() + frist_s
    pool = ThreadPoolExecutor(max_workers=samtidighet)
    try:
        # `utfordringssvar` slår opp UTFORDRINGSNAVNET (Codex P1), ikke
        # vertsnavnet: det er navnet utstedelsen ga kunden, og det eneste hun
        # kan legge en TXT-post på når vertsnavnet er et CNAME. Ett oppslag per
        # rad — arven under `ogsa_vertsnavnet` hører til revalideringen, der
        # det finnes rader eldre enn navnet; her finnes det ingen.
        #
        # `enig_svar` leses opp inne i `utfordringssvar`, altså ved kall og
        # ikke ved import: testene bytter den ut på modulen, og en tidlig
        # binding ville gjort dem tannløse.
        oppslag = {pool.submit(utfordringssvar, resolvere, h,
                               ogsa_vertsnavnet=False): (t, h)
                   for t, h in rader}
        behandlet = 0
        for ferdig in as_completed(oppslag):
            if time.monotonic() >= frist:
                res["frist_naadd"] = True
                break
            tenant, hostname = oppslag[ferdig]
            behandlet += 1
            _skriv_bekreftelse(conn, tenant, hostname, ferdig.result(), aktor,
                               res)
        res["ubehandlet"] = len(rader) - behandlet
    finally:
        # Ikke `with`: de oppslagene som fortsatt er i luften når fristen slår
        # inn skal ikke ventes ut. `cancel_futures` tar det som ikke har
        # startet; de som kjører dør med prosessen, og har ingen tilstand å
        # etterlate — de har ikke rørt basen.
        pool.shutdown(wait=False, cancel_futures=True)


def _skriv_bekreftelse(conn, tenant, hostname, txt, aktor,
                       res: dict) -> None:
    """Ett ferdig oppslag: commit eller rollback, og telleren som følger med."""
    if txt is None:
        res["uenige"] += 1
        return
    try:
        svar = conn.execute(
            "SELECT bekreft_domenechallenge(%s,%s,%s,%s)",
            (tenant, hostname, aktor, sorted(txt))).fetchone()[0]
        conn.commit()
    except MANGLENDE_BEVIS:
        # Bevis ikke funnet i TXT, utfordringen utløpt, eller raden finnes
        # ikke lenger. Alt dette er ORDINÆRE utfall: ingen påstand om
        # suksess, prøv igjen neste pass.
        conn.rollback()
        res["ikke_bevist"] += 1
        return
    except KAPPLOP:
        # Raden flyttet seg under oss (annen tenant verifiserte samme
        # hostname, vranglås, låsen var tatt). Telles for seg: et kappløp er
        # ikke det samme som «beviset sto ikke i DNS», og de to skal ikke
        # kunne skjule hverandre i én teller.
        conn.rollback()
        res["kapplop"] += 1
        return
    if svar == "verifisert":
        res["verifisert"] += 1
    elif isinstance(svar, str) and svar.startswith("konflikt:"):
        res["konflikt"] += 1
        print(json.dumps({"hendelse": "domene_overtakelseskonflikt",
                          "hostname": hostname,
                          "motpart": svar.split(":", 1)[1]}), flush=True)
    else:
        res["annet"] += 1
