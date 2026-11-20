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

Alle tolv intervalltimerne i treet er derfor unntatt — og porten
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
        "disponit-arbeidervakt.timer",
        "disponit-artefaktrydding.timer",
        "disponit-backupstatus.timer",
        "disponit-domenerevalidering.timer",
        "disponit-domeneverifisering.timer",
        "disponit-driftsvarsel.timer",
        "disponit-evidensreaper.timer",
        "disponit-helse.timer",
        "disponit-m57-utsending.timer",
        "disponit-modultoken.timer",
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


def test_modultokenene_fornyes_av_en_timer_som_faktisk_kaller_rotasjonen():
    """DEFINERT, TESTET — OG ALDRI KALT (funnet 20.11.2026).

    `/v1/modul/token/roter` har stått siden 035, og tokenene lever i 30
    døgn. Ingenting kalte ruten. Tokenene fra 17.9 utløp 17.10, og alle
    sju arbeiderne feilet i hver runde til noen tilfeldigvis leste
    journalen.

    Porten binder kjeden: timeren finnes, uniten kjører skriptet, og
    skriptet kaller nettopp rotasjonsruten med margin UNDER tokenets
    levetid.

    MUTASJONEN SOM DREPER DENNE: sett MIN_DOGN over TOKEN_DAGER, eller
    pek uniten på et annet skript.
    """
    unit = (TIMERKATALOG / "disponit-modultoken.service").read_text(
        encoding="utf-8")
    skript = (TIMERKATALOG / "roter-modultokener.sh").read_text(
        encoding="utf-8")
    assert ("ExecStart=/bin/bash /opt/disponit/aktiv/deploy/staging/"
            "roter-modultokener.sh") in unit
    assert '"$API/v1/modul/token/roter"' in skript
    margin = int(re.search(r'MIN_DOGN="\$\{MIN_DOGN:-(\d+)\}"',
                           skript).group(1))
    kilde = (ROT / "platform/core/api/modulonboarding.py").read_text(
        encoding="utf-8")
    dager = int(re.search(r"^TOKEN_DAGER = (\d+)$", kilde, re.M).group(1))
    # Margin under levetiden, og minst en uke: en vert som er nede i
    # noen døgn skal ikke komme tilbake til utløpte tokener.
    assert 7 <= margin < dager
    # Et utløpt token kan ikke rotere seg selv — da skal uniten FEILE,
    # ikke rapportere «ok».
    assert "feil=1; continue" in skript and 'exit "$feil"' in skript


def _kjor_arbeidervakt(tmp_path, feil_per_unit, inaktive=()):
    """Kjører vakten mot en falsk vert: `systemctl` og `journalctl` i PATH
    svarer fra testen, så det er SKRIPTET som måles, ikke en kopi av det."""
    import os
    import subprocess

    kat = tmp_path / "etc"
    for navn in ("m14", "wcag"):
        (kat / navn).mkdir(parents=True)
        (kat / navn / "DISPONIT_MODULTOKEN").write_text("mtk_x.y", encoding="utf-8")
    binkat = tmp_path / "bin"
    binkat.mkdir()
    falsk_systemctl = (
        "#!/bin/bash\ncase \" $INAKTIVE \" in *\" $2 \"*) echo inactive;"
        " exit 3;; esac\necho active\n")
    (binkat / "systemctl").write_text(falsk_systemctl, encoding="utf-8")
    (binkat / "journalctl").write_text(
        "#!/bin/bash\nn=$(cat \"$TELLEKAT/$2\" 2>/dev/null || echo 0)\n"
        "for i in $(seq 1 $n); do echo '{\"hendelse\": \"runde_feilet\","
        " \"feiltype\": \"RuntimeError\"}'; done\n"
        "echo '{\"hendelse\": \"arbeider_oppe\"}'\n", encoding="utf-8")
    for f in binkat.iterdir():
        f.chmod(0o755)
    tell = tmp_path / "tell"
    tell.mkdir()
    for unit, n in feil_per_unit.items():
        (tell / unit).write_text(str(n), encoding="utf-8")
    miljo = dict(os.environ, PATH=f"{binkat}:{os.environ['PATH']}",
                 ARBEIDERVAKT_KATALOG=str(kat), ARBEIDERVAKT_TEST="1",
                 TELLEKAT=str(tell), INAKTIVE=" ".join(inaktive))
    return subprocess.run(
        ["bash", str(TIMERKATALOG / "arbeidervakt.sh")], env=miljo,
        capture_output=True, text=True, timeout=30)


def test_arbeidervakten_ser_en_arbeider_som_feiler_i_hver_runde(tmp_path):
    """EN MÅNED UTEN AT NOE SÅ DET (funnet 20.11.2026): alle sju arbeiderne
    logget `runde_feilet` hvert tiende sekund, tjenestene sto «active», og
    `systemctl --failed` var tom.

    MUTASJONEN SOM DREPER DENNE: fjern `feil=1` i terskelgrenen, eller
    tell noe annet enn `runde_feilet`.
    """
    # Frisk vert — og en utrulling gir én eller to feil uten at det er alarm.
    r = _kjor_arbeidervakt(tmp_path / "a", {"disponit-m14": 2})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ok   disponit-m14: 2 feilede" in r.stdout
    assert "ok   disponit-wcag-audit: 0 feilede" in r.stdout

    # Feiler i hver runde: seksti på ti minutter.
    r = _kjor_arbeidervakt(tmp_path / "b", {"disponit-wcag-audit": 60})
    assert r.returncode == 1, r.stdout
    assert "FEIL disponit-wcag-audit: 60 feilede runder" in r.stdout
    assert "ok   disponit-m14" in r.stdout, "en syk arbeider skjulte en frisk"

    # En tjeneste som er stoppet er heller ikke «alt bra».
    r = _kjor_arbeidervakt(tmp_path / "c", {}, inaktive=["disponit-m14"])
    assert r.returncode == 1, r.stdout
    assert "FEIL disponit-m14: tjenesten er «inactive»" in r.stdout


def test_arbeidervakten_er_ikke_gronn_naar_den_ikke_fant_noe(tmp_path):
    """En vakt som ikke fant noen å vokte har ikke målt noe — og et grønt
    svar da er det samme som blindheten den ble laget for å fjerne."""
    import os
    import subprocess

    (tmp_path / "tom").mkdir()
    r = subprocess.run(
        ["bash", str(TIMERKATALOG / "arbeidervakt.sh")],
        env=dict(os.environ, ARBEIDERVAKT_KATALOG=str(tmp_path / "tom"),
                 ARBEIDERVAKT_TEST="1"),
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 1 and "fant ingen arbeidere" in r.stdout


def test_arbeidervakten_kjores_av_en_timer_og_installeres():
    unit = (TIMERKATALOG / "disponit-arbeidervakt.service").read_text(
        encoding="utf-8")
    assert ("ExecStart=/bin/bash /opt/disponit/aktiv/deploy/staging/"
            "arbeidervakt.sh") in unit
    oppsett = (TIMERKATALOG / "modultoken-oppsett.sh").read_text(
        encoding="utf-8")
    assert "systemctl enable --now disponit-arbeidervakt.timer" in oppsett


def _driftsvarsel():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "driftsvarsel", TIMERKATALOG / "driftsvarsel.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_driftsvarselet_sender_ved_ny_feil_paaminner_og_friskmelder(tmp_path):
    """En feilet unit ingen ser, er en feil ingen retter: prod var nede i
    fire uker i oktober, og arbeiderne døde i en måned. Dette er leddet som
    gjør `systemctl --failed` til en e-post.

    MUTASJONEN SOM DREPER DENNE: la `avgjor` svare None for nye units, eller
    skriv tilstanden før sendingen lyktes.
    """
    dv = _driftsvarsel()
    fil = tmp_path / "tilstand.json"
    sendt, feilet, klokke = [], [], [1000.0]

    def kjor(send_feiler=False):
        def send(til, emne, tekst):
            if send_feiler:
                raise OSError("smtp nede")
            sendt.append((til, emne, tekst))
        return dv.kjor(fil, feilede=lambda: list(feilet), send=send,
                       til="eier@example.test", naa=lambda: klokke[0],
                       vert="verten", detaljer=lambda u: "siste linje")

    # Frisk vert: stille.
    assert kjor() == 0 and sendt == []

    # Ny feil: én e-post, med unitnavnet og de siste logglinjene.
    feilet.append("disponit-arbeidervakt.service")
    assert kjor() == 0 and len(sendt) == 1
    til, emne, tekst = sendt[0]
    assert til == "eier@example.test" and "FEIL" in emne and "verten" in emne
    assert "disponit-arbeidervakt.service" in tekst and "siste linje" in tekst

    # Samme feil fem minutter senere: stille — ikke én e-post per kjøring.
    klokke[0] += 300
    assert kjor() == 0 and len(sendt) == 1

    # En NY unit feiler i tillegg: ny e-post med begge.
    feilet.append("disponit-backup.service")
    assert kjor() == 0 and len(sendt) == 2
    assert "disponit-backup.service" in sendt[1][2]

    # Et døgn uten endring: påminnelse.
    klokke[0] += dv.PAAMINN_S
    assert kjor() == 0 and len(sendt) == 3
    assert "FORTSATT FEIL" in sendt[2][1]

    # Alt friskt: friskmelding, og deretter stille igjen.
    feilet.clear()
    assert kjor() == 0 and len(sendt) == 4
    assert "i orden igjen" in sendt[3][1]
    assert kjor() == 0 and len(sendt) == 4

    # En sending som feiler, prøves igjen: tilstanden er IKKE skrevet.
    feilet.append("disponit-api.service")
    assert kjor(send_feiler=True) == 1 and len(sendt) == 4
    assert kjor() == 0 and len(sendt) == 5
    assert "disponit-api.service" in sendt[4][2]


def test_driftsvarselet_er_blindt_og_sier_det(tmp_path, monkeypatch):
    """Uten mottaker, SMTP-oppsett eller svar fra systemd har vakten ikke
    målt noe. Da er svaret exit 2 — aldri et stille 0."""
    dv = _driftsvarsel()
    fil = tmp_path / "tilstand.json"
    for k in ("VERT", "PORT", "BRUKER", "PASSORD", "AVSENDER"):
        monkeypatch.delenv(f"DISPONIT_SMTP_{k}", raising=False)
    monkeypatch.delenv("DISPONIT_DRIFTSVARSEL_TIL", raising=False)
    assert dv.kjor(fil, feilede=lambda: []) == 2
    assert dv.kjor(fil, feilede=lambda: [], til="x@example.test") == 2
    assert dv.kjor(fil, feilede=lambda: None, til="x@example.test",
                   send=lambda *a: None) == 2
    # Oss selv varsler vi ikke om, og mottakeren står aldri i repoet.
    assert dv.SELV == "disponit-driftsvarsel.service"
    for navn in ("driftsvarsel.py", "driftsvarsel-oppsett.sh",
                 "disponit-driftsvarsel.service"):
        assert "@gmail" not in (TIMERKATALOG / navn).read_text(
            encoding="utf-8")
    unit = (TIMERKATALOG / "disponit-driftsvarsel.service").read_text(
        encoding="utf-8")
    assert ("ExecStart=/usr/bin/python3 /opt/disponit/aktiv/deploy/staging/"
            "driftsvarsel.py") in unit
