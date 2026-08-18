"""Motorgrensesnittet (PR-014c §2): modulen er KUNDE av kontrollmotoren,
ikke en kopi. Motoren (axe-core i headless Chromium) bor i
wcag_checker-repoet og kjører i browser-containeren (014b §6) uten
credentials; controlleren styrer den og stoler ALDRI på utdataene —
alt herfra er ubetrodd inndata som skjemavalideres av controlleren.

`Motorresultat` er den RÅ tellingen; sanitering og ærlighetsfelter legges
av `rapport.bygg()`. Versjons- og containerdigester er BEVISST ikke en
del av resultatet: de kommer fra serverkonteksten (controllerens config),
aldri fra motoren — en kompromittert motor skal ikke kunne attestere sin
egen identitet.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from dataclasses import dataclass

#: Taket på hva controlleren i det hele tatt TAR IMOT fra motoren.
#: Rapportskjemaets 1 MiB-grense gjelder det ferdige artefaktet og
#: beskytter ingenting her: minnet er brukt lenge før JSON-parsingen
#: begynner. En kompromittert — eller bare altfor pratsom — motor skal
#: ikke kunne spise minnet i den CREDENTIAL-BÆRENDE prosessen.
MAKS_STDOUT = 1 << 20
#: stderr leses aldri som data; vi beholder en snipp til feilmeldingen og
#: kaster resten.
MAKS_STDERR = 4096
#: Dypeste nøsting vi TAR IMOT (Codex P1). Rapportstrukturen er flat —
#: `funn[].eksempler[]` er det dypeste leddet, godt under ti — så taket er
#: rundhåndet mot ekte motorer og likevel langt under enhver rekursjons-
#: grense. Uten det kunne en motor sende `[[[[...]]]]` godt innenfor
#: MAKS_STDOUT og velte controlleren med RecursionError i stedet for å få
#: den dokumenterte feilkvitteringen: `json.loads` er bare det FØRSTE
#: stedet som rekurserer, `jsonschema`-valideringen og JCS-kanoniseringen
#: gjør det samme lenger nede. Derfor stanses dybden ved INNGANGEN, ikke
#: ved hvert enkelt rekursjonspunkt.
MAKS_DYBDE = 32


def _les_med_tak(strom, tak: int) -> tuple[bytes, bool]:
    """-> (bytes, sprengt). Leser ETT byte over taket, slik at «akkurat
    innenfor» kan skilles fra «for stort» uten å bufre resten."""
    biter, n = [], 0
    while n <= tak:
        b = strom.read(min(65536, tak + 1 - n))
        if not b:
            break
        biter.append(b)
        n += len(b)
    return b"".join(biter), n > tak


def _drener(strom, behold: int, ut: list) -> None:
    """Les strømmen TOM, men behold bare de første `behold` bytene.

    Å slutte å lese ville blokkert motoren så snart rørbufferen fylles —
    da henger kjøringen til tidsavbruddet i stedet for å feile ærlig — og
    å beholde alt er nettopp det taket skal hindre."""
    biter, n = [], 0
    try:
        while True:
            b = strom.read(65536)
            if not b:
                break
            if n < behold:
                del_ = b[:behold - n]
                biter.append(del_)
                n += len(del_)
    except (OSError, ValueError):
        pass                       # strømmen ble lukket under oss
    ut.append(b"".join(biter))


def _for_dypt(raa: bytes, tak: int) -> bool:
    """Er nøstingen i JSON-teksten dypere enn `tak`?

    En løkke over bytene, ikke rekursjon — poenget er nettopp å svare UTEN
    å bruke stakken selv. Klammer inne i strenger teller ikke; en
    JSON-streng kan inneholde `[` og `\\"` uten at det er struktur.

    Svaret trenger ikke være presist for ugyldig JSON: er teksten søppel,
    avvises den uansett av `json.loads` rett etterpå. Vakten skal bare
    hindre at parseren i det hele tatt får begynne på noe for dypt."""
    dybde = maks = 0
    i_streng = escape = False
    for b in raa:
        if i_streng:
            if escape:
                escape = False
            elif b == 0x5C:                      # \
                escape = True
            elif b == 0x22:                      # "
                i_streng = False
            continue
        if b == 0x22:
            i_streng = True
        elif b in (0x5B, 0x7B):                  # [ {
            dybde += 1
            if dybde > maks:
                maks = dybde
                if maks > tak:
                    return True
        elif b in (0x5D, 0x7D):                  # ] }
            dybde -= 1
    return False


#: Egen prosessgruppe for motoren (Codex P1). Uten den er `p.kill()` bare
#: ETT drap: den ekte motoren starter Chromium, Chromium arver stdout, og
#: barnebarnet lever videre med rørenden åpen. `_les_med_tak` venter på EOF
#: og får den aldri — så den annonserte fristen kan overskrides uten grense
#: mens foreldreløse nettlesere fortsetter å kjøre. Med egen gruppe treffer
#: SIGKILL hele treet, rørene lukkes, og lesingen slipper løs.
_EGEN_GRUPPE = hasattr(os, "setsid") and hasattr(os, "killpg")


def _motorgruppe(p):
    """Motorens prosessgruppe, lest MENS barnet er ureapet — eller None.

    Den leses én gang, rett etter oppstart, og bæres videre som et tall.
    Slås den opp på nytt etter at `p.wait()` har høstet barnet, spør vi om
    en pid vi ikke lenger eier — og et gjenbrukt pid-nummer ville pekt på
    en fremmed gruppe. Så lenge gruppa har medlemmer, holder kjernen
    nummeret reservert, så den fanget verdien er den trygge.

    Er gruppa VÅR EGEN, gir vi None. Det er ikke pynt: slo
    `start_new_session` feil, ville et `killpg` her tatt ned den
    credential-bærende controllerhosten selv.
    """
    if not _EGEN_GRUPPE:
        return None
    try:
        gruppe = os.getpgid(p.pid)
        return None if gruppe == os.getpgid(0) else gruppe
    except OSError:
        return None


def _drep_treet(p, gruppe) -> None:
    """SIGKILL til HELE prosessgruppa — ikke bare motorprosessen.

    Uten gruppe (eller om gruppa alt er tom) faller vi tilbake til
    `p.kill()`: samme oppførsel som før, aldri verre."""
    if gruppe is not None:
        try:
            os.killpg(gruppe, signal.SIGKILL)
            return
        except OSError:
            pass          # gruppa er alt tom, eller vi mangler rett
    try:
        p.kill()
    except OSError:
        pass


#: Miljøet motoren FÅR (Codex P1). `Popen` arver ellers HELE controllerens
#: miljø, og `db.hemmeligheter.last_credentials` hydrerer nettopp
#: systemd-credentials — modultoken, signeringsnøkler, DSN med passord —
#: inn i `os.environ` ved oppstart. Motoren er ubetrodd kode (axe-core i
#: headless Chromium, som selv kjører ubetrodde kundesider): «motoren har
#: ingen credentials» var derfor et løfte bare kommandolinjen holdt, mens
#: `os.environ` rakte den controllerens fulle plattformautoritet.
#:
#: Listen er en ALLOWLIST, ikke en denylist: en ny hemmelighet skal ikke
#: kunne lekke ut ved at noen glemte å føre den opp. Bare det en
#: container-runtime trenger for i det hele tatt å starte står her, og
#: ingen `DISPONIT_*` — modulens egen konfigurasjon når motoren gjennom
#: payloaden på stdin, ikke gjennom miljøet.
MILJO_ALLOWLIST = (
    "PATH",                # å finne runtime-binæren
    "HOME",                # runtimens egen config-katalog
    "TMPDIR",              # Chromium skriver mye midlertidig
    "XDG_RUNTIME_DIR",     # rootless podman
    "DOCKER_HOST", "CONTAINER_HOST",   # eksplisitt socket
    "LANG", "LC_ALL", "TZ",            # lokalisering av utdata
)


def _motormiljo() -> dict:
    """Det allowlistede miljøet, med de variablene som faktisk er satt."""
    return {n: os.environ[n] for n in MILJO_ALLOWLIST if n in os.environ}


@dataclass(frozen=True)
class Motorresultat:
    regelsett_versjon: str
    varighet_ms: int
    #: [{url, status}]
    sider: tuple = ()
    #: [{regel_id, alvorlighet, antall, eksempler[]}] — RÅTT, usanert.
    funn: tuple = ()
    #: [{vert, antall, art}] — blokkerte subressurser fra proxyens telling.
    blokkert: tuple = ()
    #: proxyens taktelling: (truffet, tak, verdi)
    avkortet: tuple = (False, None, None)


class Motorfeil(Exception):
    """Motoren fullførte ikke — oppdraget skal FEILE (status avbrutt i
    kvitteringen), aldri produsere et delvis artefakt (§10 siste rad)."""


#: Største heltall JCS kan kanonisere tapsfritt: `jcs._tall` avviser
#: `abs(x) > 2**53 - 1` som `Ikkekanoniserbar`. Rapportskjemaet setter INGEN
#: øvre grense på `antall`/`varighet_ms`, så et tall herfra kunne passere
#: både konverteringen og skjemaet, og først smelle under kanoniseringen
#: rett før opplasting — som en `TypeError`, utenfor det `controller.kjor_en`
#: fanger. Grensen hører derfor til HER, ved inngangen fra ubetrodde data.
MAKS_HELTALL = 2 ** 53 - 1


def heltall(raa) -> int:
    """Ubetrodd tall fra motoren som kanoniserbart heltall — eller
    Motorfeil, ALDRI en naken ValueError, TypeError eller OverflowError.

    De tre feilmodusene er samme feil sett fra tre kanter, og alle tre
    slipper forbi `controller.kjor_en` (som kun fanger Motorfeil og
    ValidationError) og lar det claimede oppdraget stå ufullført til
    fristen — nøyaktig taushetens utfall §10 forbyr:

      * `"ukjent"` -> ValueError
      * `{"a": 1}` / None -> TypeError
      * `1e309` (JSON-tall som blir `inf`) -> OverflowError
      * `10**20` -> konverterer fint, men er `Ikkekanoniserbar` senere.

    Derfor: én port, brukt av BÅDE motoravlesningen og `rapport._antall`.

    BRØKTALL AVVISES (Codex P1). `int()` trunkerer mot null i STILLHET, og
    stillheten er skaden: `antall: 0.9` ble `0`, og `rapport.bygg` hopper
    over funn med `antall < 1` — et funn motoren FANT forsvant ut av en
    rapport som ellers ser komplett ut, uten et ærlighetsfelt som sier
    fra. `antall: 1.9` ble `1` og understøttet `sammendrag`, og
    `varighet_ms: 12.7` ble skrevet om på samme vis. Feltene er heltall i
    kontrakten, så et ikke-helt tall er utdata vi ikke kan lese — altså
    Motorfeil, ikke en avrunding modulen finner på selv.
    """
    if isinstance(raa, bool) or not isinstance(raa, (int, float, str)):
        raise Motorfeil("tall fra motoren er ikke et tall")
    # `float.is_integer()` er False for både `inf` og `nan`, så vakten
    # dekker dem også — men `int()` under står igjen som selvstendig
    # skanse, ikke som eneste.
    if isinstance(raa, float) and not raa.is_integer():
        raise Motorfeil("tall fra motoren er ikke et heltall")
    try:
        n = int(raa)
    except (TypeError, ValueError, OverflowError) as e:
        raise Motorfeil("tall fra motoren er ikke et tall") from e
    if abs(n) > MAKS_HELTALL:
        raise Motorfeil(
            f"tall fra motoren er utenfor det kanoniserbare området"
            f" (|n| > {MAKS_HELTALL})")
    return n


def regelsettversjon(raa) -> str:
    """Motorens regelsettversjon som ekte streng — eller Motorfeil.

    `str(raa)[:64]` var en STILLE OPPFINNELSE (Codex P1): `null` ble
    `"None"`, `4.10` ble `"4.1"`, og `{"a": 1}` ble `"{'a': 1}"` — alle
    tre passerer skjemaets `minLength: 1`, så rapporten ble PROMOTERT med
    en fabrikkert versjon. Og versjonen er ikke pynt: den er hele
    proveniensen som gjør evidensen tolkbar og etterprøvbar. En leser som
    ser `axe-4.10` vet hvilke regler som gjaldt; en som ser `None` tror
    det står noe der.

    En motor som ikke kan oppgi sin egen regelsettversjon, har ikke levert
    en kontroll vi kan lese — altså Motorfeil, med den dokumenterte
    feilkvitteringen, ikke en rapport med gjettet innhold.
    """
    if not isinstance(raa, str):
        raise Motorfeil("regelsett_versjon fra motoren er ikke en streng")
    v = raa.strip()
    if not v:
        raise Motorfeil("regelsett_versjon fra motoren er tom")
    return v[:64]


def avkortet(raa) -> tuple:
    """Proxyens taktelling som EKTE triplett — eller Motorfeil (Codex P2).

    `tuple(raa or (False, None, None))` var stille på tre måter, og alle
    tre endte som skjemagyldig `avkortet` i PROMOTERT evidens:

      * `"false"` er iterabel, så trippelen ble `('f','a','l','s','e')`
        og `truffet` ble tegnet `'f'` — sant for `bool(...)`. Motoren sa
        «ikke avkortet» og rapporten sa «avkortet».
      * `{"truffet": false}` ble `('truffet',)` — nøklene, ikke verdiene.
      * en lengre liste ble stille kappet til tre ledd, altså utdata vi
        ikke leste ferdig.

    Dekningssignalet er den ene tingen leseren bruker for å vite hva
    rapporten IKKE dekker (014b B3). Kan vi ikke lese det, har vi ikke en
    rapport — vi har en gjetning, og da er et ærlig feilet oppdrag riktig
    utfall. Selve TRIPPELEN kontrolleres i `rapport.bygg`, der taket måles;
    her står bare formen, fordi det er her den ble ødelagt.
    """
    if raa is None:
        return (False, None, None)
    if not isinstance(raa, (list, tuple)):
        raise Motorfeil("avkortet fra motoren er ikke en liste")
    if not raa:
        return (False, None, None)
    if len(raa) > 3:
        raise Motorfeil("avkortet fra motoren har mer enn tre ledd")
    return tuple(raa)


#: Motorens standardfrist. IKKE 3600 (Codex P1, runde 12).
#:
#: 3600 s er TAKET for hele claimet, ikke for skanningen: både
#: opplastingskapabiliteten claimet utsteder (migrasjon 017) og eier-leasen
#: (migrasjon 037) klemmes til 3600 s, og ingen av dem har en
#: fornyelsesvei. En motor som fikk bruke hele taket, ble derfor ferdig
#: nøyaktig når kapabiliteten den skal laste opp med var utløpt — hele
#: jobben gjort, ingen evidens levert.
#:
#: Fristen her dekker SKANNINGEN. De siste fem minuttene er kanonisering,
#: opplasting og signert kvittering, altså det som gjør et fullført arbeid
#: til et avsluttet oppdrag. Manifestets annonserte 30/60 min er fristen
#: for OPPDRAGET; motoren får det som er igjen når avslutningen er trukket
#: fra.
STANDARD_TIDSAVBRUDD_S = 3300
#: Avslutningen (kanonisering + opplasting + kvittering) — differansen
#: mellom motorfristen og 3600 s-taket, uttalt så den ikke kan bli borte.
AVSLUTNINGSMARGIN_S = 300


class Kommandomotor:
    """Kjør den konfigurerte motorkommandoen (containeren) og les JSON på
    stdout. Kommandoen kommer fra drift-config (DISPONIT_WCAG_MOTOR), aldri
    fra oppdraget. Utdata er ubetrodd: alt går videre til `rapport.bygg` +
    skjemavalidering; en motor som skriver søppel gir Motorfeil, ikke en
    rapport.

    Fristen er `STANDARD_TIDSAVBRUDD_S`, ikke claimets 3600 s-tak: se der
    for hvorfor de siste fem minuttene tilhører opplastingen."""

    def __init__(self, kommando: list[str],
                 tidsavbrudd_s: int = STANDARD_TIDSAVBRUDD_S):
        self.kommando = list(kommando)
        self.tidsavbrudd_s = tidsavbrudd_s

    def kjor(self, payload: dict) -> Motorresultat:
        # BUNDET fangst (Codex P1): `subprocess.run(capture_output=True)`
        # bufret stdout og stderr uten tak i opptil en time. Her leses
        # stdout mot MAKS_STDOUT, stderr dreneres mot MAKS_STDERR, og en
        # vakthund dreper prosessen ved tidsavbruddet — så en motor som
        # spyr ut data møter en Motorfeil, ikke en tom controllerhost.
        #
        # EGEN PROSESSGRUPPE (Codex P1): motoren er en container som selv
        # starter Chromium. Uten `start_new_session` ligger barnebarnet i
        # VÅR gruppe, og drapet ved fristen traff bare mellomleddet — se
        # `_drep_treet`.
        #
        # VASKET MILJØ (Codex P1): `env=` er eksplisitt, aldri arvet — se
        # `MILJO_ALLOWLIST`.
        try:
            p = subprocess.Popen(
                self.kommando, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=_motormiljo(),
                start_new_session=_EGEN_GRUPPE)
        except OSError as e:
            raise Motorfeil(f"motorkjøring: {type(e).__name__}") from e

        gruppe = _motorgruppe(p)
        drept = threading.Event()

        def _tidsavbrudd():
            drept.set()
            _drep_treet(p, gruppe)

        vakt = threading.Timer(self.tidsavbrudd_s, _tidsavbrudd)
        vakt.daemon = True
        vakt.start()
        stderr_ut: list[bytes] = []
        drener = threading.Thread(
            target=_drener, args=(p.stderr, MAKS_STDERR, stderr_ut),
            daemon=True)
        drener.start()
        try:
            try:
                # Payloaden er de fire lukkede feltene — langt under
                # rørbufferen, så skriv-så-les kan ikke vranglåse her.
                p.stdin.write(json.dumps(payload).encode("utf-8"))
                p.stdin.close()
                ut, sprengt = _les_med_tak(p.stdout, MAKS_STDOUT)
            except OSError as e:
                raise Motorfeil(f"motorkjøring: {type(e).__name__}") from e
            if sprengt:
                raise Motorfeil(
                    f"motoren skrev mer enn {MAKS_STDOUT} byte på stdout")
            p.wait()          # stdout er tom; vakthunden bærer fristen
        finally:
            vakt.cancel()
            # HELE treet, ALLTID — også når motorprosessen selv alt har
            # avsluttet (Codex P1). `if p.poll() is None` var nettopp
            # hullet: den ekte motoren kan avslutte i det den har skrevet
            # JSON-en, mens Chromium lever videre med rørenden åpen. Da så
            # vakten en ferdig prosess og lot barnebarnet stå igjen som en
            # foreldreløs nettleser. Er gruppa tom, er `killpg` en no-op.
            _drep_treet(p, gruppe)
            p.wait()
            drener.join(timeout=1)
            for s in (p.stdin, p.stdout, p.stderr):
                try:
                    s.close()
                except OSError:
                    pass

        if drept.is_set():
            raise Motorfeil("motorkjøring: TimeoutExpired")
        if p.returncode != 0:
            # Snippen fra stderr er DIAGNOSTIKK, ikke data: kappet, kun
            # printbare tegn, og den når aldri lenger enn til driftsloggen
            # (controlleren rapporterer bare unntakstypen videre).
            raa = (stderr_ut[0] if stderr_ut else b"").decode("utf-8",
                                                              "replace")
            hale = "".join(c for c in raa if c.isprintable())[:200].strip()
            raise Motorfeil(f"motor exit {p.returncode}"
                            + (f": {hale}" if hale else ""))
        if _for_dypt(ut, MAKS_DYBDE):
            raise Motorfeil(
                f"motorutdata er nøstet dypere enn {MAKS_DYBDE} nivåer")
        try:
            d = json.loads(ut.decode("utf-8"))
            return Motorresultat(
                # `regelsettversjon` kaster Motorfeil direkte: en fabrikkert
                # `"None"` ville passert skjemaet og blitt promotert som
                # proveniens.
                regelsett_versjon=regelsettversjon(d["regelsett_versjon"]),
                # `heltall` kaster Motorfeil direkte (ikke ValueError), så
                # `varighet_ms: 1e309` gir den dokumenterte feilkvitteringen
                # i stedet for en OverflowError ut av kjøreløkka.
                varighet_ms=max(0, heltall(d["varighet_ms"])),
                sider=tuple(d.get("sider") or ()),
                funn=tuple(d.get("funn") or ()),
                blokkert=tuple(d.get("blokkert") or ()),
                avkortet=avkortet(d.get("avkortet")))
        except (ValueError, KeyError, TypeError, RecursionError) as e:
            # `RecursionError` står her som ANDRE skanse (Codex P1):
            # dybdevakten over skal ha tatt nøstingen først, men et unntak
            # herfra slipper forbi `controller.kjor_en` — som bare fanger
            # Motorfeil og ValidationError — og etterlater det claimede
            # oppdraget ufullført til fristen i stedet for å kvittere.
            raise Motorfeil("motorutdata uleselig") from e
