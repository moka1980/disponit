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
    for sveip, tid in (("betalingssveip", "05:10"),
                       ("adressesveip", "05:15"),
                       ("lonnssveip", "05:20"),
                       ("kampanjesveip", "05:25")):
        # RADEN, IKKE DOKUMENTET. Første utkast sjekket bare at
        # klokkeslettet fantes ET STED i fundamentet — og da ville
        # porten vært grønn av en helt urelatert setning som tilfeldig
        # nevnte samme tall. CodeRabbit fant det 6/9.
        rad = [l for l in fundament.splitlines()
               if l.startswith("|") and f"`disponit_{sveip}`" in l]
        assert len(rad) == 1, (sveip, len(rad))
        assert tid in rad[0], (sveip, tid, rad[0])
        fil = (TIMERKATALOG / f"disponit-{sveip}.timer").read_text(
            encoding="utf-8")
        assert f"OnCalendar=*-*-* {tid}:00 UTC" in fil, (sveip, tid)


def test_hver_kalendertimer_har_presisjon_nok_til_aa_bli_spredt():
    """SPREDNINGEN KAN OPPHEVES AV DEN SOM SKAL HÅNDHEVE DEN.

    systemds `AccuracySec` er ETT MINUTT som standard, og den er ikke
    en presisjonsgrense — den er en LISENS TIL Å SLÅ SAMMEN. systemd
    forskyver timere innenfor vinduet for å vekke maskinen færre
    ganger, og med 35 kalendertimere betyr det at flere av dem kan fyre
    i samme sekund.

    Da ville `RandomizedDelaySec` vært meningsløs: stigen hviler på at
    planlagte tider ligger fra hverandre, og spredningen på fire
    minutter blir borte hvis systemd samler resultatet tilbake til
    minuttet.

    CodeRabbit fant det 5/9, samme natt som spredningen ble strammet
    fra 30 til 4 — altså i samme runde der jeg trodde jeg hadde løst
    overlappen.

    MUTASJONEN SOM DREPER DENNE: fjern `AccuracySec` fra én timer.
    """
    mangler = []
    for navn, tekst in _timere().items():
        if not _KALENDER.search(tekst):
            continue
        m = re.search(r"^AccuracySec=(.+)$", tekst, re.M)
        if not m:
            mangler.append(f"{navn}: mangler AccuracySec")
        elif m.group(1).strip() not in ("1us", "1ms"):
            mangler.append(f"{navn}: AccuracySec={m.group(1).strip()}")
    assert mangler == [], (
        "kalendertimere som systemd kan slå sammen:\n"
        + "\n".join(mangler))


def test_oppstartsbygen_er_dokumentert_og_ikke_bare_akseptert():
    """`Persistent=true` PÅ 35 TIMERE ER EN BYGE VED OPPSTART.

    En vert som har vært nede over natten kjører ALLE de tapte
    sveipene når den kommer opp. `RandomizedDelaySec` gjelder også for
    innhentingen, så bygen er spredt over fire minutter — men det er
    fortsatt 35 sveip på fire minutter, mot ett trinn hvert femte
    minutt i normal drift.

    DET ER AKSEPTERT, OG GRUNNEN SKAL STÅ SKREVET. Hver sveip er ÉN
    tilkobling som gjør ETT funksjonskall, og hver har sin egen
    advisory-lås som hindrer at den overlapper SEG SELV. En byge gir et
    tregere oppstartsminutt, aldri et galt funn — og alternativet,
    å droppe `Persistent`, ville latt en nattlig måling forsvinne
    stille hver gang verten startet på nytt i feil vindu.

    Porten krever at avveiningen står i `disponit-sveipestatus.timer`,
    ikke at den er løst. Et akseptert vilkår som ingen har skrevet ned
    er et vilkår ingen kan revurdere.
    """
    tekst = (TIMERKATALOG / "disponit-sveipestatus.timer").read_text(
        encoding="utf-8")
    assert "OPPSTARTSBYGEN" in tekst, (
        "avveiningen rundt Persistent staar ikke skrevet noe sted")
    for ord_ in ("advisory", "Persistent"):
        assert ord_ in tekst, ord_
