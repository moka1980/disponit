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
sikre_mac_nokler
sikre_miljo
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
    for n in ALLE + ["DISPONIT_ATT_NOKLER", "DISPONIT_MAC_NOKLER"]:
        assert n in m, f"{n} mangler etter ny installasjon"
    assert oct((tmp_path / "staging.env").stat().st_mode)[-3:] == "600"


@kun_posix
def test_mac_nokler_gyldig_og_idempotent(tmp_path):
    """PR-012: `sikre_mac_nokler` genererer et register som produksjonens egen
    `last_mac_register`-validering godtar (NØYAKTIG én signerer, hemmelighet
    >= 32 tegn), OG en ny kjøring lar en eksisterende nøkkel STÅ. Uten dette
    kan en senere deployendring bytte eller regenerere signeringsnøkkelen
    ubemerket — staging beviser bare happy path, ikke ny-installasjon/
    idempotens/eksisterende-verdi."""
    import json
    from api.mac_register import _valider_register   # produksjonens egen fasit
    r = kjor(tmp_path, OPPSETT)
    assert r.returncode == 0, r.stderr
    raa = les(tmp_path)["DISPONIT_MAC_NOKLER"]
    reg = json.loads(raa)
    _valider_register(reg)          # kaster hvis ikke nøyaktig én signerer / <32 tegn
    signerere = [k for k, v in reg.items() if v.get("rolle") == "signerer"]
    assert len(signerere) == 1, f"forventet én signerer, fikk {signerere}"
    assert all(len(v["hemmelighet"]) >= 32 for v in reg.values())
    # Idempotens: en ny kjøring rører IKKE den eksisterende nøkkelen.
    r2 = kjor(tmp_path, OPPSETT)
    assert r2.returncode == 0, r2.stderr
    assert les(tmp_path)["DISPONIT_MAC_NOKLER"] == raa, \
        "MAC-nøkkelen ble regenerert på ny kjøring — signeringsnøkkelen kan " \
        "byttes ubemerket ved en senere deploy"


@kun_posix
def test_miljoet_settes_trygt_og_overskrives_aldri(tmp_path):
    """Codex P1 til PR #42: `api/policyregister.tillatte_statuser` leser
    `DISPONIT_MILJO`, og mangler den, tar den staging-standarden — `utkast`
    og `validert_pilot` binder da ekte beslutninger. Nøkkelen skrives derfor
    på en fersk install, med den TRYGGE verdien.

    Og den overskrives aldri: en produksjonsvert setter `produksjon` for
    hånd, og en ny kjøring av oppsettskriptet skal ikke stille den tilbake
    til staging — det ville vært nøyaktig den stille nedgraderingen porten
    finnes for å hindre."""
    r = kjor(tmp_path, OPPSETT)
    assert r.returncode == 0, r.stderr
    assert les(tmp_path)["DISPONIT_MILJO"] == "staging", \
        "fersk install skrev ikke DISPONIT_MILJO=staging"

    r2 = kjor(tmp_path, "sett_nokkel DISPONIT_MILJO produksjon\n" + OPPSETT)
    assert r2.returncode == 0, r2.stderr
    assert les(tmp_path)["DISPONIT_MILJO"] == "produksjon", \
        "oppsettskriptet nedgraderte en produksjonsvert til staging"


def test_utrullingen_gater_miljoet_og_gir_det_til_api_prosessen():
    """Codex P1 til PR #42: å erklære en modul `i_drift`/`produksjon` er
    tomt så lenge INGEN vei setter `DISPONIT_MILJO` på prosessen. Da tar
    `tillatte_statuser` staging-standarden, og en policy merket `utkast`
    kan binde en ekte beslutning uten at noe krasjer.

    Tre ledd må henge sammen, og alle tre låses her: utrullingen må GATE
    verdien før første mutasjon (en skrivefeil skal ikke falle tilbake til
    staging), MATERIALISERE den som credential, og unit-fila må LASTE den.
    Ryker ett av dem, er de to andre virkningsløse."""
    rot = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    with open(rot + "/deploy/staging/opp.sh", encoding="utf-8") as f:
        linjer = f.read().splitlines()
    with open(rot + "/deploy/staging/disponit-api.service",
              encoding="utf-8") as f:
        unit = f.read()

    def forste(nal):
        for nr, l in enumerate(linjer):
            if nal in l and not l.strip().startswith("#"):
                return nr
        return None

    gate = forste('[ "$MILJO" != staging ]')
    skriving = forste("skriv_cred api DISPONIT_MILJO")
    # Første aktive mutasjon i skriptet: Unix-identitetene opprettes rett
    # etter «HERFRA MUTERES SYSTEMET»-skillet. Kommentaren selv duger ikke
    # som anker — `forste` hopper over kommentarlinjer med vilje.
    mutasjon = forste("groupadd --system disponit-proxy")
    assert gate is not None, "opp.sh gater ikke DISPONIT_MILJO i det hele tatt"
    assert skriving is not None, \
        "opp.sh gir aldri DISPONIT_MILJO videre til API-prosessen"
    assert gate < mutasjon, (
        f"miljøporten (linje {gate + 1}) står etter første mutasjon "
        f"(linje {mutasjon + 1}) — en avvist utrulling ville alt ha endret "
        f"systemet")
    assert "LoadCredential=DISPONIT_MILJO:" in unit, \
        "disponit-api.service laster ikke DISPONIT_MILJO — credentialen " \
        "skrives, men når aldri prosessen"


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
    # Første bruk av en migrator-DSN er nå kallet til migrasjonskjøreren
    # (`migrer.py`), ikke en psql-kommando. Testen følger etter flyttingen —
    # egenskapen den vokter er den samme: verifiser før bruk.
    kandidater = [n for n in (forste("migrer.py"),
                              forste('psql "$DISPONIT_MIGRATOR_URL"'),
                              forste('psql "$DISPONIT_TEST_MIGRATOR_DSN"'))
                  if n is not None]
    assert kandidater, "fant ingen bruk av migrator-DSN i skriptet"
    bruk = min(kandidater)
    assert verifikasjon is not None, "skriptet verifiserer ikke DSN-ene i det hele tatt"
    assert verifikasjon < bruk, (
        f"verifiser_og_reparer (linje {verifikasjon + 1}) kjører etter første "
        f"bruk av en migrator-DSN (linje {bruk + 1}) — ved avbrudd stopper "
        f"set -e skriptet før reparasjonen nås")


def test_ingen_kjorer_migrasjoner_utenom_den_herdede_kjoreren():
    """Codex' P1: oppsettskriptet og CI kjørte migrasjonsfilene med
    `psql -f`. Filene er idempotente, så det ser uskyldig ut — men det
    omgår advisory-låsen, transaksjonen kjøreren eier fra versjon 3,
    checksum-registreringen og avvisningen av endret historikk. Og verre:
    CI testet da en annen vei enn den staging og produksjon bruker.

    En herdet kjører som kan omgås av oppsettet sitt eget skript er ingen
    kjører. Denne testen låser at det bare finnes én vei inn."""
    import re
    rot = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    mistenkt = re.compile(r"-f\s+\S*migrations/|migrations/\S*\.sql")
    for sti in ("deploy/staging/oppsett-postgresql.sh",
                ".github/workflows/ci.yml"):
        with open(os.path.join(rot, sti), encoding="utf-8") as f:
            for nr, linje in enumerate(f, 1):
                if linje.lstrip().startswith("#"):
                    continue
                assert not mistenkt.search(linje), (
                    f"{sti}:{nr} kjører en migrasjonsfil direkte:\n  "
                    f"{linje.strip()}\nBruk deploy/staging/migrer.py")


def test_oppsettskriptet_setter_rettigheter_etter_migrasjonene():
    """Feil nr. 6: GRANT på en tabell som ikke finnes ennå er stille
    virkningsløs. Rettighetene settes derfor av `migrer.py`, etter at
    kjøreren har opprettet tabellene — ikke i en egen psql-blokk i
    skriptet."""
    rot = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    with open(rot + "/deploy/staging/oppsett-postgresql.sh",
              encoding="utf-8") as f:
        skript = f.read()
    assert "migrer.py" in skript, "skriptet kaller ikke migrasjonskjøreren"
    assert "GRANT SELECT, INSERT ON revisjonslogg" not in skript, (
        "rettighetene settes fortsatt i skriptet — de hører i migrer.py, "
        "etter migrasjonene")
