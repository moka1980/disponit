"""Strukturelle porter for `deploy/staging/backup-db.sh` (Codex P1 ×4, #229).

Skriptet kjører som root på VPS-en og kan ikke prøves fra en pytest-økt:
det trenger `pg_dump`, `age`, en levende base og en delt disk. Portene her
måler det som likevel er målbart — REKKEFØLGEN og PLASSERINGEN — og det er
nettopp de to tingene Codex-rundene på #229 fant feil ved. Samme form som
§11/§12-portene i `test_rutiner_natt` og `test_rutiner_leveransetakt`: en
regel som bare finnes i prosa, glir fra flaten den styrer.
"""
import re
from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
SKRIPT = (ROT / "deploy" / "staging" / "backup-db.sh").read_text(
    encoding="utf-8")


def _pos(nal: str) -> int:
    """Posisjonen til `nal`, med en feilmelding som navngir den."""
    i = SKRIPT.find(nal)
    assert i != -1, f"fant ikke {nal!r} i backup-db.sh"
    return i


def test_retensjonen_star_for_diskporten():
    """Codex P1: porten hindret sin egen forutsetning.

    Sto sveipen sist, var den uoppnåelig nettopp når den trengtes: fylte
    de beholdte parene katalogen så et nytt par ikke fikk plass,
    avsluttet diskporten FØR sveipen kunne slette noe. Neste kjøring så
    samme opptatte plass og avsluttet på samme sted — permanent, uten at
    noe var galt annet enn rekkefølgen.

    MUTASJONEN SOM DREPER DENNE: flytt retention-blokken tilbake til
    slutten av skriptet.
    """
    assert _pos('UTLOPTE=$(find "$KATALOG"') < _pos("LEDIG_KIB="), (
        "diskporten måler før retensjonen har frigjort noe — en full"
        " katalog blir da permanent full, med en feilmelding over seg")
    # ... og sveipen må fortsatt komme ETTER låsen som eier katalogen,
    # ellers sletter to kjøringer hverandres par.
    assert _pos("flock") < _pos('UTLOPTE=$(find "$KATALOG"'), \
        "retensjonen kjører utenfor flock-en som eier katalogen"


def test_tenantlistene_bor_i_den_private_katalogen():
    """Codex P1 ×2: klartekst-stier i `/tmp`, og et symlenke-kappløp.

    `mktemp` uten `-p` legger fila i `/tmp` — verdensskrivbar og
    PERSISTENT. Fila og de to avledede bærer hver tenant-ID og hver
    arkivmedlemssti i klartekst, altså nøyaktig det arkivet krypteres for
    å skjule. Og `mktemp` sikrer bare det FØRSTE navnet: `.sett` og
    `.krav` kan gjettes fra det og pre-opprettes som symlenker under den
    lange dump/arkiv-fasen, så root skriver gjennom lenken.

    `$RAA_KAT` er `mktemp -d -p /dev/shm` + `chmod 700`, root-eid: ingen
    andre kommer inn, så hverken lesningen eller kappløpet finnes.

    MUTASJONEN SOM DREPER DENNE: `LISTE=$(mktemp)`.
    """
    m = re.search(r"^LISTE=(.*)$", SKRIPT, re.M)
    assert m, "ingen LISTE-tilordning i backup-db.sh"
    assert m.group(1).strip() == '"$RAA_KAT/medlemmer"', (
        f"listen bor utenfor den private katalogen: {m.group(1).strip()}")
    # Katalogen må være privat OG root-eid før listen legges i den.
    assert _pos('chmod 700 "$RAA_KAT"') < m.start(), \
        "listen legges i katalogen før den er 0700"
    # `.sett` og `.krav` er avledet av samme navn — de arver katalogen.
    for avledet in ('"$LISTE.sett"', '"$LISTE.krav"'):
        assert avledet in SKRIPT, f"{avledet} finnes ikke lenger"


def test_finaliseringen_er_fsynket_i_par_rekkefolge():
    """Codex P1: `sync` alene gir ingen REKKEFØLGE.

    Den tømte køen én gang, før begge `mv`-ene; etterpå var de to
    katalogpostene usynkede, og et strømbrudd kunne la filsystemet
    gjenopprette dumpens endelige navn UTEN arkivets. Da er «ser ut som
    dagens backup»-løgnen tilbake, med filsystemet som årsak i stedet for
    signalet.

    Kravet er derfor ledd for ledd: innholdet i begge filene, så arkivets
    navn, så en katalog-fsync, så dumpens navn.

    MUTASJONEN SOM DREPER DENNE: én naken `sync` foran de to `mv`-ene.
    """
    innhold = _pos('sync "$ARKIV_DELVIS" "$DELVIS"')
    mv_arkiv = _pos('mv "$ARKIV_DELVIS" "$ARKIV"')
    mv_dump = _pos('mv "$DELVIS" "$FIL"')
    # Begge formene teller: den bare `sync "$KATALOG"` mellom `mv`-ene, og
    # den PORTEDE `if ! sync "$KATALOG"; then` etter den siste (runde 9).
    katalogsyncer = [m.start() for m in
                     re.finditer(r'sync "\$KATALOG"', SKRIPT)]
    assert innhold < mv_arkiv < mv_dump, \
        "innholdet fsynkes ikke før navnene settes"
    # DEN SISTE SYNC-EN MÅ VÆRE EN PORT (Codex P1, runde 9). `PAR_KLAR`
    # ble satt uansett hva den returnerte, og trapen spør DISKEN — så et
    # par som aldri passerte holdbarhetskravet ble stående med endelige
    # navn mens tjenesten meldte feil.
    assert 'if ! sync "$KATALOG"; then' in SKRIPT, (
        "den siste katalog-syncen er ikke portet — et par kan publiseres"
        " uten at holdbarheten er bekreftet")
    feilarm = SKRIPT[SKRIPT.index('if ! sync "$KATALOG"; then'):]
    feilarm = feilarm[:feilarm.index("PAR_KLAR=1")]
    assert 'rm -f "$FIL" "$ARKIV"' in feilarm, (
        "feilarmen rydder ikke — trapen ser da to endelige navn og lar"
        " paret stå")
    assert "exit 1" in feilarm, "feilen forplanter seg ikke"

    assert any(mv_arkiv < p < mv_dump for p in katalogsyncer), (
        "ingen katalog-fsync MELLOM de to `mv`-ene — da kan dumpens navn"
        " overleve et strømbrudd uten arkivets, som er hele løgnen"
        " arbeidsnavnene finnes for å hindre")
    assert any(p > mv_dump for p in katalogsyncer), \
        "paret publiseres uten at det siste navnet er festet"
    # ARKIVET FØRST, DUMPEN SIST — retningen er utledet, ikke valgt:
    # dumpen er det retention og operatøren leter etter.
    assert mv_arkiv < mv_dump, "dumpen får endelig navn før arkivet"


def test_det_nyeste_utlopte_paret_lever_til_det_nye_star():
    """Codex P1: sveipen foran porten åpnet en verre dør.

    Å flytte retensjonen foran diskporten løste en deadlock — men er ALLE
    par eldre enn 30 dager, etter en timer som har stått eller en vert som
    har vært nede, slettet sveipen hvert eneste gjenopprettingspunkt FØR
    `pg_dump`, verifiseringen og arkivet hadde lykkes. En transient feil
    etterpå etterlot installasjonen uten backup i det hele tatt.

    Begge hullene lukkes av å SPARE DEN NYESTE: sveipen tar alt unntatt
    den, og den siste faller først når det nye paret har fått navnene
    sine og er fsynket.

    MUTASJONEN SOM DREPER DENNE: fjern `[ "$gammel" != "$SPART" ]` (da er
    vi tilbake i «alle slettes før dumpen»), eller flytt den avsluttende
    `slett_par "$SPART"` opp foran finaliseringen.
    """
    # BARE KOMPLETTE PAR KAN SPARES (Codex P2, runde 9). Er den nyeste
    # utløpte dumpen uten arkiv, er den ikke et gjenopprettingspunkt — å
    # spare den og slette et eldre KOMPLETT par etterlater noe som per
    # definisjon ikke kan gjenopprettes.
    assert '.inndata.tar.age" ]; then' in SKRIPT, (
        "utvelgelsen sjekker ikke at kandidaten har begge halvdelene")
    # ... og utvelgelsen må ikke dø på SIGPIPE: `sort | head -1` gir 141
    # under `pipefail`, og retensjonen ville låst seg permanent.
    # KOMMANDOEN, ikke ordet: begrunnelsen over NEVNER `sort | head -1`,
    # og en tekstsøk over hele skriptet leste sin egen forklaring som et
    # treff. Vi ser bare på linjer som faktisk kjører noe.
    kjorende = [l for l in SKRIPT.splitlines()
                if not l.lstrip().startswith("#")]
    # KLASSEN, ikke det ene tallet (Cursor P2): `head -5` på feilveiene
    # lukker røret på nøyaktig samme måte. Der dør blokken på 141 FØR sin
    # egen `exit 1`, så avbruddet går utenom AVBRUTT-stien. Porten
    # forbyr derfor enhver `| head` i kjørende linjer — les strømmen
    # ferdig med `sed -n`.
    assert not [l for l in kjorende if "| head" in l], (
        "`head` lukker røret og gir avsenderen SIGPIPE — med pipefail dør"
        " kjøringen: retensjonen låser seg permanent, og feilveiene"
        " avbryter på 141 i stedet for gjennom sin egen exit 1")
    assert '[ "$gammel" != "$SPART" ] || continue' in SKRIPT, \
        "sveipen sletter også den som skal spares"
    # Den spartes fall må komme ETTER at begge navnene er satt.
    fall = SKRIPT.index('  slett_par "$SPART"')
    assert _pos('mv "$DELVIS" "$FIL"') < fall, (
        "det siste gamle paret slettes før det nye har fått navnene sine"
        " — da finnes det et vindu uten noe gjenopprettingspunkt")
    assert _pos('PAR_KLAR=1') < fall, \
        "den sparte faller før paret er erklært ferdig"


def test_en_paagaaende_opplasting_dreper_ikke_backupen():
    """Codex P2: `tar` returnerer 1 når en fil endres mens den leses.

    `inndata.py` skriver ciphertexten til `<bunt>.bin.tmp` og gjør
    `os.replace` først når den er hel. Overlapper en opplasting
    arkivpasset, leser `tar` den midlertidige fila mens den vokser eller
    byttes ut, melder «file changed as we read it» og returnerer status
    1 — og med `pipefail` dør HELE nattbackupen av en fil dumpen ikke
    engang refererer til.

    Ekskluderingen løser det ved roten: en `.tmp` er per konstruksjon
    ikke en bunt ennå. En `.bin` dumpen KREVER er ferdig skrevet og
    omdøpt før raden ble committet, så ekskluderingen kan ikke skjule noe
    porten trenger.

    MUTASJONEN SOM DREPER DENNE: fjern `--exclude`, eller utvid den til
    `*.bin` og skjul dermed det porten måler.
    """
    tar_linje = next(l for l in SKRIPT.splitlines()
                     if l.lstrip().startswith("tar --create"))
    assert "--exclude='./*/*.bin.tmp'" in tar_linje, (
        f"arkivet leser opplastingens midlertidige filer: {tar_linje}")
    # MØNSTERET MÅ VÆRE SMALT (Codex P2, runde 8). `tar --exclude`
    # matcher mot HELE medlemsstien, og `_stikomponent` tillater en
    # tenant-ID som `customer.tmp` — `*.tmp` ville tatt hele den kundens
    # katalog med alle ferdige bunter, og `comm`-porten avbrutt backupen
    # hver natt for den installasjonen.
    # ANKRET, IKKE BARE SMALT (Codex P2, runde 9). `--exclude` er uankret
    # etter enhver `/`, så et rent suffiksmønster matcher KATALOGNAVN like
    # godt som filnavn — `*.tmp` tok en tenant som het `noe.tmp`,
    # `*.bin.tmp` tok en som het `noe.bin.tmp`. `_stikomponent` tillater
    # begge. `./*/` sier at det må være en fil på løvnivå under en
    # tenantkatalog.
    for uankret in ("--exclude='*.tmp'", "--exclude='*.bin.tmp'"):
        assert uankret not in tar_linje, (
            f"uankret mønster {uankret} — en tenant med det navnet mister"
            f" hele arkivet sitt: {tar_linje}")
    # ... og den skal ikke ekskludere ferdige bunter.
    assert "--exclude='*.bin'" not in tar_linje, (
        "ekskluderingen dekker buntfilene — da arkiverer backupen"
        f" ingenting av det den finnes for: {tar_linje}")
    # `pipefail` er fortsatt på; det er dét som gjorde `tar`-statusen
    # dødelig, og det skal den være for ekte feil.
    assert "pipefail" in SKRIPT, \
        "pipefail er slått av — da skjules ekte tar-feil i stedet"


def test_tenantstier_gaar_ikke_i_loggen():
    """Codex P2: stiene havnet i journald.

    `disponit-backup.service` overstyrer ikke strømmene, så stderr går i
    journalen — og på en vert med persistent journal blir tenant-ID-ene
    liggende på disk. Det er nøyaktig lekkasjen listene ble flyttet til
    tmpfs for å unngå, og arkivet krypteres for å hindre.

    Antall og korthash er nok til å finne igjen raden og til å
    sammenligne to kjøringer, men kan ikke leses tilbake til en kunde.

    MUTASJONEN SOM DREPER DENNE: skriv `printf '%s\\n' "$MANGLER" |
    head -5 >&2` igjen.
    """
    # Ingen av feilveiene skal sende en RÅ sti til stderr.
    raa = [l for l in SKRIPT.splitlines()
           if ">&2" in l and ("$MANGLER" in l or "$mens_manglet" in l)
           and "sha256sum" not in l and "wc -l" not in l
           and "grep -c" not in l]
    assert not raa, f"rå tenant-stier skrives til stderr: {raa}"
    assert SKRIPT.count("sha256sum | cut -c1-12") == 2, (
        "begge feilveiene skal hashe stien — én av dem lekker fortsatt")
    # Operatøren må få vite HVOR klarteksten finnes, ellers er
    # redaksjonen en forringelse og ikke en beskyttelse.
    assert "$LISTE.krav" in SKRIPT and "tmpfs for klartekst" in SKRIPT, \
        "meldingen sier ikke hvor operatøren finner de faktiske stiene"


def test_arkivporten_maler_innhold_ikke_bare_navn():
    """Codex P1: en tømt `.bin` passerte som gjenopprettingsverifisert.

    `comm`-porten måler at hver påkrevd sti STÅR i arkivet. En fil som er
    blitt avkortet eller tømt før backupen kjørte, arkiveres like lydig
    som en hel: `tar` lykkes, navnet står i listen, og paret publiseres
    som verifisert mens den restaurerte radens nonce peker på ingenting.

    Porten må stå FØR finaliseringen — ellers har paret alt fått
    backupnavnene sine når den feller.

    MUTASJONEN SOM DREPER DENNE: fjern `-s`-testen, eller flytt blokken
    ned etter `mv`-ene.
    """
    innholdsport = _pos('mens_manglet="$mens_manglet$sti"')
    assert '[ ! -s "$LAGER/$sti" ]' in SKRIPT, (
        "porten spør ikke om filen har innhold — en tømt bunt passerer")
    assert '[ ! -f "$LAGER/$sti" ]' in SKRIPT, \
        "porten spør ikke om stien er en vanlig fil"
    # SYMLENKEN FØRST (Codex P1, runde 6). Både `-f` og `-s` FØLGER
    # lenken, så en `.bin` byttet ut med en symlenke til hvilken som helst
    # ikke-tom fil passerte begge — mens `tar` uten `--dereference`
    # arkiverer LENKEN. Paret ble meldt verifisert og gjenopprettet en
    # peker.
    assert '[ -L "$LAGER/$sti" ]' in SKRIPT, (
        "porten avviser ikke symlenker — `-f`/`-s` følger dem, mens `tar`"
        " arkiverer lenken og ikke ciphertexten")
    # ... og `tar` må FORTSATT ikke dereferere: gjorde den det, ville
    # lageret kunne arkivere filer utenfor seg selv gjennom en lenke.
    # Målt på selve kommandolinjen, ikke på prosaen rundt den — kommen-
    # taren over NEVNER flagget, og en tekstsøk over hele skriptet ville
    # lest sin egen begrunnelse som et treff.
    tar_linje = next(l for l in SKRIPT.splitlines()
                     if l.lstrip().startswith("tar --create"))
    assert "--dereference" not in tar_linje and " -h" not in tar_linje, (
        f"arkivet følger nå lenker: {tar_linje}")
    assert innholdsport < _pos('mv "$ARKIV_DELVIS" "$ARKIV"'), (
        "innholdsporten står etter finaliseringen — da er paret alt"
        " publisert når den feller")
    # ÆRLIGHETEN OM RESTKLASSEN står i skriptet, ikke bare i PR-en:
    # ciphertexten har ingen lagret digest å måles mot, så DELVIS
    # avkorting er utenfor rekkevidde til skrivesiden bærer en.
    assert "HVA DEN IKKE GJØR" in SKRIPT, (
        "restklassen er ikke skrevet ned — neste leser tror porten"
        " dekker byte-korrupsjon")
