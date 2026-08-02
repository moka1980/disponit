"""Testmatrise for deploy/staging/lib-miljofil.sh — tilstandsmaskinen for
hemmelighetene i /etc/disponit/staging.env.

Bakgrunn (Codex-review av PR-004): fem reelle feil er funnet i dette
oppsettet, alle i denne logikken, og alle ved manuell kjøring på staging.
To av dem oppsto som følge av å fikse en tidligere feil. Manuell prøve er
ingen port — derfor kjøres tilstandsmaskinen her, i CI, uten root, uten apt
og uten PostgreSQL.

Databasen erstattes av etterlignere: `rotere_passord` skriver til en liten
fasitfil, og `dsn_virker` slår opp i den. Da kan vi drive maskinen gjennom
tilstander som er upraktiske å fremkalle på en ekte server — særlig avbrudd
midt i en rotasjon.
"""
import os
import shutil
import subprocess
import textwrap

import pytest

# Testene driver et bash-skript med Unix-stier. På Windows kjenner verken
# stiene eller `bash` seg igjen — WSL-bash forstår ikke «C:\Users\...», og
# hele fila feilet der (Codex P2). Vi hopper eksplisitt i stedet for å late
# som: skriptet er et Linux-serveroppsett, og CI kjører på Linux, så porten
# er uendret.
kun_posix = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="deploy-skriptet er Linux-only; bash mangler eller stiene er "
           "Windows-stier. Kjøres i CI og på staging.")

LIB = (os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))) +
    "/deploy/staging/lib-miljofil.sh")

# Etterlignere: rollens «faktiske» passord lagres i $FASIT, én linje per
# rolle. dsn_virker godtar en DSN bare hvis passordet i den stemmer.
STUBBER = r"""
FASIT="$KAT/roller.fasit"
touch "$FASIT"
rotere_passord() {
  grep -v "^$1=" "$FASIT" > "$FASIT.ny" 2>/dev/null || true
  echo "$1=$2" >> "$FASIT.ny"; mv "$FASIT.ny" "$FASIT"
}
dsn_virker() {
  local dsn="$1" bruker passord
  bruker=$(printf '%s' "$dsn" | sed -n 's/.*user=\([^ ]*\).*/\1/p')
  passord=$(printf '%s' "$dsn" | sed -n 's/.*password=\([^ ]*\).*/\1/p')
  [ -n "$bruker" ] || return 1
  grep -qx "$bruker=$passord" "$FASIT"
}
"""


def kjor(tmp_path, skall: str) -> subprocess.CompletedProcess:
    miljofil = tmp_path / "staging.env"
    skript = tmp_path / "kjor.sh"
    tekst = textwrap.dedent(f"""\
        set -eu
        KAT="{tmp_path}"
        MILJOFIL="{miljofil}"
        DB=disponit
        BRUKER=disponit
        MIGRATOR=disponit_migrator
        touch "$MILJOFIL"; chmod 600 "$MILJOFIL"
        . "{LIB}"
        {STUBBER}
        RUNTIME=("DATABASE_URL=$DB" "DISPONIT_TEST_DSN=${{DB}}_test")
        MIGRATOR_DSN=("DISPONIT_MIGRATOR_URL=$DB" "DISPONIT_TEST_MIGRATOR_DSN=${{DB}}_test")
        {skall}
        """)
    skript.write_text(tekst, encoding="utf-8")
    return subprocess.run(["bash", str(skript)], capture_output=True, text=True)


OPPSETT = """
sikre_rolle_dsn "$BRUKER"   "${RUNTIME[@]}"
sikre_rolle_dsn "$MIGRATOR" "${MIGRATOR_DSN[@]}"
sikre_attestasjonsnokler
verifiser_og_reparer "$BRUKER"   "${RUNTIME[@]}"
verifiser_og_reparer "$MIGRATOR" "${MIGRATOR_DSN[@]}"
"""


def les(tmp_path) -> dict[str, str]:
    ut = {}
    for linje in (tmp_path / "staging.env").read_text(encoding="utf-8").splitlines():
        if "=" in linje:
            n, _, v = linje.partition("=")
            ut[n] = v.strip("'")
    return ut


def passord(dsn: str) -> str:
    return dsn.split("password=")[1].split()[0]


ALLE = ["DATABASE_URL", "DISPONIT_TEST_DSN",
        "DISPONIT_MIGRATOR_URL", "DISPONIT_TEST_MIGRATOR_DSN"]


@kun_posix
def test_ny_installasjon_gir_alle_nokler(tmp_path):
    r = kjor(tmp_path, OPPSETT)
    assert r.returncode == 0, r.stderr
    m = les(tmp_path)
    for n in ALLE + ["DISPONIT_ATT_NOKLER"]:
        assert n in m, f"{n} mangler etter ny installasjon"
    assert oct((tmp_path / "staging.env").stat().st_mode)[-3:] == "600"


@kun_posix
def test_oppgradering_legger_til_migrator_uten_aa_roere_runtime(tmp_path):
    """Codex P1: en eldre installasjon hadde bare runtime-nøklene, og
    skriptet skrev fila kun når den ikke fantes."""
    kjor(tmp_path, OPPSETT)
    foer = les(tmp_path)
    (tmp_path / "staging.env").write_text(
        "\n".join(f"{n}='{foer[n]}'" for n in
                  ("DATABASE_URL", "DISPONIT_TEST_DSN", "DISPONIT_ATT_NOKLER")) + "\n",
        encoding="utf-8")
    r = kjor(tmp_path, OPPSETT)
    assert r.returncode == 0, r.stderr
    etter = les(tmp_path)
    assert all(n in etter for n in ALLE)
    assert etter["DATABASE_URL"] == foer["DATABASE_URL"], \
        "runtime-passordet ble rotert selv om nøklene fantes"


@pytest.mark.parametrize("slettet,rolle_dsn", [
    ("DATABASE_URL", ("DATABASE_URL", "DISPONIT_TEST_DSN")),
    ("DISPONIT_TEST_DSN", ("DATABASE_URL", "DISPONIT_TEST_DSN")),
    ("DISPONIT_MIGRATOR_URL",
     ("DISPONIT_MIGRATOR_URL", "DISPONIT_TEST_MIGRATOR_DSN")),
    ("DISPONIT_TEST_MIGRATOR_DSN",
     ("DISPONIT_MIGRATOR_URL", "DISPONIT_TEST_MIGRATOR_DSN")),
])
@kun_posix
def test_rotasjon_skriver_soesken_samlet(tmp_path, slettet, rolle_dsn):
    """Codex P1: roterte passordet, men skrev bare den manglende linja —
    søskenlinja sto igjen med det gamle passordet. Dette er nøyaktig den
    rotasjonsprosedyren DEPLOY.md beskriver."""
    kjor(tmp_path, OPPSETT)
    foer = les(tmp_path)
    linjer = [l for l in (tmp_path / "staging.env").read_text(encoding="utf-8")
              .splitlines() if not l.startswith(f"{slettet}=")]
    (tmp_path / "staging.env").write_text("\n".join(linjer) + "\n", encoding="utf-8")

    r = kjor(tmp_path, OPPSETT)
    assert r.returncode == 0, r.stderr
    etter = les(tmp_path)
    a, b = rolle_dsn
    assert passord(etter[a]) == passord(etter[b]), \
        "søsken-DSN-ene har ulike passord etter rotasjon"
    assert passord(etter[a]) != passord(foer[a]), "passordet ble ikke rotert"
    # den andre rollen skal være urørt
    urort = [n for n in ALLE if n not in rolle_dsn]
    for n in urort:
        assert etter[n] == foer[n], f"{n} ble rotert uten grunn"


@kun_posix
def test_ingen_rotasjon_naar_ingenting_mangler(tmp_path):
    kjor(tmp_path, OPPSETT)
    foer = (tmp_path / "staging.env").read_text(encoding="utf-8")
    r = kjor(tmp_path, OPPSETT)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "staging.env").read_text(encoding="utf-8") == foer, \
        "andre kjøring endret fila — passord roteres uten grunn"


@kun_posix
def test_avbrutt_rotasjon_oppdages_og_repareres(tmp_path):
    """Codex P1: passordet roteres FØR fila skrives. Blir vi avbrutt imellom,
    har rollen nytt passord mens fila har gammelt — og nøkkelnavnene finnes
    fortsatt, så en maskin som bare ser etter navn oppdager det aldri.

    Her fremkalles nøyaktig den tilstanden: rollens passord endres i fasiten
    uten at fila oppdateres. Neste kjøring skal oppdage det ved å KOBLE TIL,
    ikke ved å telle nøkler, og reparere."""
    kjor(tmp_path, OPPSETT)
    foer = les(tmp_path)

    # simuler avbrudd: databasen har rotert, fila henger igjen
    r = kjor(tmp_path, 'rotere_passord "$BRUKER" avbrutt_nytt_passord\n')
    assert r.returncode == 0, r.stderr
    assert les(tmp_path)["DATABASE_URL"] == foer["DATABASE_URL"]

    r = kjor(tmp_path, OPPSETT)
    assert r.returncode == 0, r.stderr
    assert "ute av takt" in r.stdout, \
        "avbruddet ble ikke oppdaget — maskinen stoler på nøkkelnavn"
    etter = les(tmp_path)
    assert passord(etter["DATABASE_URL"]) == passord(etter["DISPONIT_TEST_DSN"])
    assert passord(etter["DATABASE_URL"]) != passord(foer["DATABASE_URL"])
    # migrator skal ikke ha blitt rørt av naboens problem
    assert etter["DISPONIT_MIGRATOR_URL"] == foer["DISPONIT_MIGRATOR_URL"]


@kun_posix
def test_midlertidig_fil_lages_i_samme_katalog(tmp_path):
    """Codex P1: mktemp i /tmp etterfulgt av mv til /etc krysser
    filsystemgrenser, og da er `mv` ikke atomisk. Den midlertidige fila skal
    lages ved siden av målet."""
    r = kjor(tmp_path, 'mktemp() { echo "MKTEMP-KALT: $*" >&2; command mktemp "$@"; }\n'
                       'export -f mktemp 2>/dev/null || true\n'
                       'sett_nokkel PROEVE verdi\n')
    assert r.returncode == 0, r.stderr
    assert f"MKTEMP-KALT: {tmp_path}/" in r.stderr, \
        f"mktemp ble ikke kalt med målkatalogen: {r.stderr!r}"


@kun_posix
def test_ingen_rester_etter_skriving(tmp_path):
    kjor(tmp_path, OPPSETT)
    rester = [f.name for f in tmp_path.iterdir() if f.name.startswith(".staging.env.")]
    assert rester == [], f"midlertidige filer ble liggende: {rester}"


@kun_posix
def test_ingen_noekkel_finnes_i_to_utgaver(tmp_path):
    """Skrives en nøkkel uten å fjerne den gamle linja, virker alt: shell tar
    den siste verdien. Men da blir utdaterte passord liggende i klartekst i
    hemmelighetsfila, og den vokser for hver rotasjon. Fanget av
    mutasjonstest — de andre testene så det ikke, fordi de leser fila inn i
    et oppslagsverk der siste verdi vinner."""
    kjor(tmp_path, OPPSETT)
    for _ in range(3):
        linjer = [l for l in (tmp_path / "staging.env").read_text(encoding="utf-8")
                  .splitlines() if not l.startswith("DATABASE_URL=")]
        (tmp_path / "staging.env").write_text("\n".join(linjer) + "\n", encoding="utf-8")
        kjor(tmp_path, OPPSETT)

    navn = [l.split("=", 1)[0] for l in
            (tmp_path / "staging.env").read_text(encoding="utf-8").splitlines() if "=" in l]
    duplikater = {n for n in navn if navn.count(n) > 1}
    assert not duplikater, f"utdaterte hemmeligheter ligger igjen i fila: {duplikater}"


def test_verifikasjonen_kjores_for_dsn_ene_tas_i_bruk():
    """Codex P1: `verifiser_og_reparer` sto bare til slutt. Var forrige
    kjøring avbrutt, pekte migrator-DSN-en på feil passord, migrasjonen
    feilet, og `set -e` avsluttet skriptet FØR reparasjonen ble nådd —
    reparasjonen var utilgjengelig nøyaktig i tilstanden den fantes for.

    Rekkefølgen er en egenskap ved skriptet, ikke ved biblioteket, og låses
    derfor her: første verifikasjon skal komme før første bruk av en DSN.
    Denne testen trenger ingen bash og kjører derfor overalt."""
    skript = (os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))) +
        "/deploy/staging/oppsett-postgresql.sh")
    with open(skript, encoding="utf-8") as f:
        linjer = f.read().splitlines()

    def forste(nal):
        for nr, l in enumerate(linjer):
            if nal in l and not l.strip().startswith("#"):
                return nr
        return None

    verifikasjon = forste("verifiser_og_reparer")
    bruk = min(n for n in (forste('psql "$DISPONIT_MIGRATOR_URL"'),
                           forste('psql "$DISPONIT_TEST_MIGRATOR_DSN"'))
               if n is not None)
    assert verifikasjon is not None, "skriptet verifiserer ikke DSN-ene i det hele tatt"
    assert verifikasjon < bruk, (
        f"verifiser_og_reparer (linje {verifikasjon + 1}) kjører etter første "
        f"bruk av en migrator-DSN (linje {bruk + 1}) — ved avbrudd stopper "
        f"set -e skriptet før reparasjonen nås")
