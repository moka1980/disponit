"""Timerplanen er en DOKTRINE — denne porten gjør den til en MÅLING.

BAKGRUNNEN. Hver eneste `.timer` i `deploy/staging/` bærer den samme
begrunnelsen i prosa, formulert litt forskjellig i hver fil:

    «KLOKKESLETTET ER 07:50 fordi 03:15 til 07:35 alt er tatt. Sveipens
     første steg leser ALLE tenanters subjekter, og to slike skann på
     samme sekund legger seg oppå hverandres I/O uten grunn.»

Doktrinen er riktig. Den har bare aldri vært målt, og med atten
nattlige sveip har den drevet:

  * `disponit-personvernsveip` og `disponit-tilgangssveip` sto BEGGE på
    04:35 UTC — nøyaktig det de to filene hver for seg sier de unngår.
  * `disponit-lagermaaling` sto på `03:17:00` UTEN `UTC`. Alle andre
    kalendertimere er UTC-festet; uten suffikset kjører jobben i
    vertens lokale tid, og «stigen» er ikke lenger en stige på en vert
    som ikke står i UTC.
  * `disponit-kvalitetsprofil` sto på `OnCalendar=daily` — midnatt i
    vertens tid, ikke et punkt på stigen noen hadde valgt.

Det er den samme feilklassen som har gått igjen i hele klynge 4 og 5:
EN REGEL SOM BARE FINNES I PROSA, GJENTATT I MANGE FILER, DRIVER — og
ingen av kopiene vet om de andre.

HVA PORTEN MÅLER, OG HVA DEN IKKE MÅLER.

Den måler KALENDERTIMERE (`OnCalendar=`). Den måler IKKE
INTERVALLTIMERE (`OnUnitActiveSec=`), og det er et bevisst skille:

  * En kalendertimer sikter mot ET TIDSPUNKT. To som sikter mot samme
    sekund kolliderer, hver eneste natt, for alltid.
  * En intervalltimer sikter mot EN AVSTAND fra forrige kjøring. Den
    er allerede spredt av sin egen oppstart, og `RandomizedDelaySec`
    på en femminutters jobb ville vært støy, ikke spredning.

Alle elleve intervalltimerne i treet er derfor unntatt — og porten
teller dem, så et unntak som vokser stille blir synlig.

MERK at `RandomizedDelaySec` IKKE er det som skiller de to gruppene:
`disponit-domenerevalidering` er en intervalltimer og har den likevel,
fordi den sprer FLERE INSTANSER av samme jobb. Skillet går på
`OnCalendar` mot `OnUnitActiveSec`, og porten måler nettopp det.
"""
from __future__ import annotations

import re
from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
TIMERKATALOG = ROT / "deploy" / "staging"

#: Timere som med vilje ikke er kalenderfestet. Hver av dem kjører på
#: AVSTAND fra forrige kjøring, ikke mot et klokkeslett.
_INTERVALL = re.compile(r"^OnUnitActiveSec=", re.M)
_KALENDER = re.compile(r"^OnCalendar=(.+)$", re.M)


def _timere() -> dict[str, str]:
    ut = {}
    for fil in sorted(TIMERKATALOG.glob("*.timer")):
        ut[fil.name] = fil.read_text(encoding="utf-8")
    return ut


def test_porten_finner_timerne():
    """En port som ikke fant noen filer ville vært stille for alltid."""
    alle = _timere()
    assert len(alle) >= 30, f"fant bare {len(alle)} timere"
    kalender = [n for n, t in alle.items() if _KALENDER.search(t)]
    assert len(kalender) >= 20, kalender


def test_hver_kalendertimer_er_utc_festet():
    """UTEN `UTC` KJØRER JOBBEN I VERTENS LOKALE TID.

    Stigen fra 03:15 til 08:20 er bygget i UTC, og hvert klokkeslett er
    valgt fordi de andre var tatt. En timer uten suffikset deltar ikke i
    den stigen i det hele tatt på en vert som ikke står i UTC — og
    verten kan bytte tidssone uten at noen rører en timerfil.

    `OnCalendar=daily` er samme problem i en annen form: den er
    midnatt i vertens tid, og ikke et punkt noen har valgt.

    MUTASJONEN SOM DREPER DENNE: fjern ` UTC` fra én timer.
    """
    uten_utc = []
    for navn, tekst in _timere().items():
        m = _KALENDER.search(tekst)
        if not m:
            continue
        verdi = m.group(1).strip()
        if not verdi.endswith(" UTC"):
            uten_utc.append(f"{navn}: OnCalendar={verdi}")
    assert uten_utc == [], (
        "kalendertimere uten eksplisitt UTC:\n" + "\n".join(uten_utc))


def test_ingen_to_kalendertimere_deler_klokkeslett():
    """DOKTRINEN, MÅLT.

    Hver av disse jobbene leser ALLE tenanters rader som sitt første
    steg. To slike skann på samme sekund legger seg oppå hverandres
    I/O uten grunn — og `RandomizedDelaySec` hjelper ikke: den sprer
    innenfor vinduet, men to jobber som starter i samme vindu har
    fortsatt overlappende forventet last.

    MUTASJONEN SOM DREPER DENNE: sett to timere til samme klokkeslett.
    """
    per_tid: dict[str, list[str]] = {}
    for navn, tekst in _timere().items():
        m = _KALENDER.search(tekst)
        if not m:
            continue
        per_tid.setdefault(m.group(1).strip(), []).append(navn)
    kolliderer = {tid: navn for tid, navn in per_tid.items()
                  if len(navn) > 1}
    assert kolliderer == {}, (
        "kalendertimere som deler klokkeslett:\n"
        + "\n".join(f"  {tid}: {', '.join(sorted(n))}"
                    for tid, n in sorted(kolliderer.items())))


def test_hver_kalendertimer_har_spredning_og_persistent():
    """`RandomizedDelaySec` sprer lasten INNENFOR vinduet;
    `Persistent=true` gjør at en vert som var nede da tidspunktet
    passerte, kjører én gang når den kommer opp.

    Uten den siste taper en nattlig måling en hel dag hver gang verten
    startes på nytt i feil vindu — stille.
    """
    mangler = []
    for navn, tekst in _timere().items():
        if not _KALENDER.search(tekst):
            continue
        if "RandomizedDelaySec=" not in tekst:
            mangler.append(f"{navn}: RandomizedDelaySec")
        if "Persistent=true" not in tekst:
            mangler.append(f"{navn}: Persistent=true")
    assert mangler == [], "\n".join(mangler)


def test_intervalltimerne_er_et_navngitt_unntak():
    """UNNTAKET SKAL VÆRE TELT, ikke bare tillatt.

    Et unntak som vokser stille er et unntak som til slutt dekker alt.
    Denne porten holder listen fast: en ny intervalltimer må endre
    tallet her, og da må noen se på den.

    Intervalltimerne er unntatt `RandomizedDelaySec` fordi de sikter
    mot en AVSTAND fra forrige kjøring, ikke mot et klokkeslett — men
    de skal heller ikke ha en `OnCalendar`, for da er de begge deler
    og ingen av dem.
    """
    intervall = []
    for navn, tekst in _timere().items():
        if not _INTERVALL.search(tekst):
            continue
        intervall.append(navn)
        assert not _KALENDER.search(tekst), (
            f"{navn} har BÅDE OnCalendar og OnUnitActiveSec")
        assert "OnBootSec=" in tekst, (
            f"{navn} har OnUnitActiveSec uten OnBootSec — den ville"
            " ikke kjørt før første manuelle start")
    assert sorted(intervall) == [
        "disponit-artefaktrydding.timer",
        "disponit-backupstatus.timer",
        "disponit-domenerevalidering.timer",
        "disponit-domeneverifisering.timer",
        "disponit-evidensreaper.timer",
        "disponit-helse.timer",
        "disponit-m57-utsending.timer",
        "disponit-plan.timer",
        "disponit-rydd-pending.timer",
        "disponit-selvtest.timer",
        "disponit-varselsender.timer",
    ], sorted(intervall)


def test_stigen_er_dokumentert_i_fundamentet():
    """KLOKKESLETTENE SKAL STÅ ETT STED SOM ER LESBART FOR MENNESKER.

    Klynge 5s fundament fører tabellen over eier, sveiperolle og
    klokkeslett. Porten binder de fire nyeste til den — resten av
    stigen står i sine egne timerfiler, og de tre portene over holder
    dem konsistente.
    """
    fundament = (ROT / "docs" / "KLYNGE5-FUNDAMENT.md").read_text(
        encoding="utf-8")
    for sveip, tid in (("betalingssveip", "07:35"),
                       ("adressesveip", "07:50"),
                       ("lonnssveip", "08:05"),
                       ("kampanjesveip", "08:20")):
        assert tid in fundament, (sveip, tid)
        fil = (TIMERKATALOG / f"disponit-{sveip}.timer").read_text(
            encoding="utf-8")
        assert f"OnCalendar=*-*-* {tid}:00 UTC" in fil, (sveip, tid)
