"""PR-009: tokenstatus-migrasjonen og PENDING-seremonien.

Klarsignalets syv Codex-porter for kodelaget. Driftslaget (units, opp.sh,
helsetimer) måles på staging — det som KAN måles i suiten, måles her.
"""
import importlib.util
import io
import secrets
from pathlib import Path

import pytest

from .test_api import (DSN, MIGRATOR_DSN, PEPPER, TENANT,  # noqa: F401
                       _lag_token, _rydd, migrator, miljo, token)
from .test_kjorer_og_kryptering import _nullstill  # noqa: F401
from .test_pr008 import _gjenopprett_rettigheter  # noqa: F401

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

ROT = Path(__file__).resolve().parents[3]


def _cli():
    spek = importlib.util.spec_from_file_location(
        "token_cli", ROT / "deploy/staging/token-cli.py")
    modul = importlib.util.module_from_spec(spek)
    spek.loader.exec_module(modul)
    return modul


# ---------------------------------------------------------------------------
# Port 1: ingen tokenkode leser/skriver gammel `aktiv`
# ---------------------------------------------------------------------------

def test_port1_ingen_tokenvei_bruker_aktiv():
    """Grep-porten. `aktiv` på api_tokener finnes ikke lenger; enhver
    kodevei som fortsatt refererer den ville lest en kolonne som ikke
    eksisterer. Policyregisterets `policyer.aktiv` er en ANNEN kolonne og
    unntas via kontekst (api_tokener-nærhet)."""
    # Migrasjon 009 er UNNTATT: backfillen dens LESER `aktiv` med vilje,
    # før den dropper kolonnen — porten gjelder runtime-veiene etterpå.
    filer = [ROT / "deploy/staging/token-cli.py",
             ROT / "deploy/staging/migrer.py",
             ROT / "platform/core/api/app.py"]
    for fil in filer:
        tekst = fil.read_text(encoding="utf-8")
        for i, linje in enumerate(tekst.splitlines(), 1):
            if "api_tokener" in linje and "aktiv" in linje \
                    and "DROP COLUMN" not in linje and "--" != linje.strip()[:2]:
                # api_tokener og aktiv på samme linje er den farlige formen.
                raise AssertionError(f"{fil.name}:{i}: {linje.strip()}")


# ---------------------------------------------------------------------------
# Port 3: migrasjonen bevarer status for eksisterende tokens
# ---------------------------------------------------------------------------

@pg
def test_port3_migrasjonen_bevarer_aktive_og_tilbakekalte(migrator, miljo):
    """Oppgraderingsvei: base på 008 med et aktivt og et deaktivert token
    (gammel `aktiv`-kolonne) → migrasjon 009 → AKTIV/TILBAKEKALT, og det
    aktive tokenet autentiserer ETTER migrasjonen. En samlet DEFAULT
    'PENDING' ville sperret hver kunde — det er nettopp det trinnene
    hindrer."""
    from db import kjorer
    _nullstill(migrator, med_legacy_uten_checksum=False)
    kjorer.migrer(migrator, til_og_med=8)

    import hashlib
    import hmac as hmaclib
    secret = "s" * 43
    mac = hmaclib.new(PEPPER.encode(), secret.encode(),
                      hashlib.sha256).hexdigest()
    migrator.execute(
        "INSERT INTO api_tokener (token_id, tenant, rolle, scopes,"
        " secret_mac, aktiv) VALUES ('tk_gammelaktiv',%s,'agent',"
        " ARRAY['decision:write'],%s,true)", (TENANT, mac))
    migrator.execute(
        "INSERT INTO api_tokener (token_id, tenant, rolle, scopes,"
        " secret_mac, aktiv) VALUES ('tk_gammeldod',%s,'agent',"
        " ARRAY['decision:write'],%s,false)", (TENANT, mac))
    migrator.commit()

    kjort = kjorer.migrer(migrator)
    assert 9 in kjort
    _gjenopprett_rettigheter(migrator)

    rader = dict(migrator.execute(
        "SELECT token_id, status FROM api_tokener WHERE token_id LIKE"
        " 'tk_gammel%'").fetchall())
    assert rader == {"tk_gammelaktiv": "AKTIV", "tk_gammeldod": "TILBAKEKALT"}
    kolonner = {r[0] for r in migrator.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name='api_tokener'").fetchall()}
    assert "aktiv" not in kolonner, "v5 §1: aktiv skal være borte"
    migrator.rollback()

    # Det gamle aktive tokenet VIRKER etter migrasjonen (via runtime-veien).
    from db.pg import koble
    runtime = koble(DSN)
    try:
        rad = runtime.execute("SELECT tenant FROM verifiser_token(%s,%s)",
                              ("tk_gammelaktiv", mac)).fetchone()
        assert rad == (TENANT,)
        assert runtime.execute("SELECT tenant FROM verifiser_token(%s,%s)",
                               ("tk_gammeldod", mac)).fetchone() is None
    finally:
        runtime.close()


# ---------------------------------------------------------------------------
# Port 4 (DB-laget) + V2: PENDING
# ---------------------------------------------------------------------------

@pg
def test_port4_pending_avvises_av_verifikatoren(migrator, miljo):
    """PENDING er aldri en API-principal — målt direkte på verifikatoren
    (API-laget måles i test_api_porter sin CLI-rundtur)."""
    import hashlib
    import hmac as hmaclib
    secret = "p" * 43
    mac = hmaclib.new(PEPPER.encode(), secret.encode(),
                      hashlib.sha256).hexdigest()
    migrator.execute(
        "INSERT INTO api_tokener (token_id, tenant, rolle, scopes,"
        " secret_mac) VALUES ('tk_pendingtest',%s,'agent',"
        " ARRAY['decision:write'],%s)", (TENANT, mac))
    migrator.commit()
    status = migrator.execute(
        "SELECT status FROM api_tokener WHERE token_id='tk_pendingtest'"
    ).fetchone()[0]
    assert status == "PENDING", "default skal være PENDING"
    from db.pg import koble
    runtime = koble(DSN)
    try:
        assert runtime.execute(
            "SELECT tenant FROM verifiser_token('tk_pendingtest',%s)",
            (mac,)).fetchone() is None
    finally:
        runtime.close()


# ---------------------------------------------------------------------------
# Port 5: pepper finnes ikke i DB og er aldri funksjonsargument
# ---------------------------------------------------------------------------

@pg
def test_port5_pepper_aldri_i_databasen(migrator, miljo):
    argnavn = migrator.execute(
        "SELECT p.proname, pg_get_function_identity_arguments(p.oid)"
        "  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname='public' AND lower(pg_get_function_identity_"
        "arguments(p.oid)) LIKE '%pepper%'").fetchall()
    assert argnavn == [], f"pepper som funksjonsargument: {argnavn}"
    kolonner = migrator.execute(
        "SELECT table_name, column_name FROM information_schema.columns"
        " WHERE table_schema='public' AND column_name ILIKE '%pepper%'"
    ).fetchall()
    assert kolonner == [], f"pepper som kolonne: {kolonner}"
    migrator.rollback()


@pg
def test_v2_hent_pending_token_gir_kun_pending(migrator, miljo, token):
    """Den avgrensede funksjonen svarer for PENDING og TIER for AKTIV —
    et aktivt tokens MAC skal ikke kunne leses ut via CLI-veien."""
    from db.pg import koble
    cli = _cli()
    from .test_api_porter import TOKEN_ADMIN_DSN
    admin = koble(TOKEN_ADMIN_DSN)
    try:
        token_id, secret = cli.opprett(admin, PEPPER, TENANT, "agent",
                                       ["decision:write"])
        admin.commit()
        assert admin.execute("SELECT tenant FROM hent_pending_token(%s)",
                             (token_id,)).fetchone() == (TENANT,)
        cli.verifiser_pending(admin, PEPPER, token_id, secret)
        cli.aktiver(admin, token_id)
        admin.commit()
        assert admin.execute("SELECT tenant FROM hent_pending_token(%s)",
                             (token_id,)).fetchone() is None, \
            "AKTIV skal aldri kunne leses via PENDING-veien"
    finally:
        admin.close()


# ---------------------------------------------------------------------------
# Port 6/7 (CLI-laget): TTY-regler + V3 rydd-pending
# ---------------------------------------------------------------------------

@pg
def test_seremonien_uten_tty_produserer_ingen_hemmelighet(migrator, miljo,
                                                          monkeypatch):
    """v4 §1.1: TTY bekreftes FØR token genereres — avbruddet skjer før
    det finnes noe å miste."""
    from db.pg import koble
    cli = _cli()
    from .test_api_porter import TOKEN_ADMIN_DSN
    admin = koble(TOKEN_ADMIN_DSN)
    try:
        antall_for = admin.execute(
            "SELECT count(*) FROM api_tokener WHERE tenant=%s",
            (TENANT,)).fetchone()[0]
        admin.rollback()
        monkeypatch.setattr("sys.stdout.isatty", lambda: False,
                            raising=False)
        with pytest.raises(SystemExit, match="terminal"):
            cli._seremoni_ny(admin, PEPPER, False,
                             lambda: cli.opprett(admin, PEPPER, TENANT,
                                                 "agent", ["decision:write"]))
        antall_etter = admin.execute(
            "SELECT count(*) FROM api_tokener WHERE tenant=%s",
            (TENANT,)).fetchone()[0]
        admin.rollback()
        assert antall_for == antall_etter, \
            "ingen rad skal fødes når hemmeligheten ikke kan leveres"
    finally:
        admin.close()


@pg
def test_avbrutt_bekreftelse_tilbakekaller_pending(migrator, miljo,
                                                   monkeypatch):
    """v4-krasjmatrisen: bekreftes ikke lagringen, tilbakekalles tokenet —
    ingen foreldreløs hemmelighet, ingen aktiv rad."""
    from db.pg import koble
    cli = _cli()
    from .test_api_porter import TOKEN_ADMIN_DSN
    admin = koble(TOKEN_ADMIN_DSN)
    try:
        monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
        monkeypatch.setattr("builtins.input", lambda *_: "nei")
        with pytest.raises(SystemExit, match="ikke bekreftet"):
            cli._seremoni_ny(admin, PEPPER, False,
                             lambda: cli.opprett(admin, PEPPER, TENANT,
                                                 "agent", ["decision:write"]))
        statuser = [r[0] for r in admin.execute(
            "SELECT status FROM api_tokener WHERE tenant=%s",
            (TENANT,)).fetchall()]
        admin.rollback()
        assert "PENDING" not in statuser and "AKTIV" not in statuser, \
            f"etterlatt token: {statuser}"
    finally:
        admin.close()


# ---------------------------------------------------------------------------
# Review-runde 1, to P1 i opp.sh — logikken bor nå i lib-opp.sh og måles her
# (samme mønster som lib-miljofil.sh: inline-bash ingen test så, feilet).
# ---------------------------------------------------------------------------

LIB_OPP = ROT / "deploy/staging/lib-opp.sh"


def _bash(skript: str) -> "subprocess.CompletedProcess":
    import subprocess
    return subprocess.run(["bash", "-c", skript], capture_output=True,
                          text=True)


def test_p1_rollback_dommen_felles_over_unionen(tmp_path):
    """P1-scenarioet ordrett: ny migrasjon KUN i runtime-basen, testbasen
    sier «ingen» sist — dommen skal være FORBUDT, aldri kompatibel."""
    runtime = tmp_path / "runtime"
    testbase = tmp_path / "test"

    def dom(runtime_tekst, test_tekst):
        runtime.write_text(f"migrasjoner kjørt: {runtime_tekst}\n", encoding="utf-8")
        testbase.write_text(f"migrasjoner kjørt: {test_tekst}\n", encoding="utf-8")
        r = _bash(f". {LIB_OPP}; vurder_migrasjoner runtime {runtime} "
                  f"test {testbase}; printf 'NYE=%s' \"$NYE_MIGRASJONER\"")
        assert r.returncode == 0, r.stderr
        return r.stdout.split("NYE=", 1)[1]

    assert dom("[9]", "ingen — alt var oppdatert") != "", \
        "runtime-only migrasjon skal gi FORBUDT (selve P1-en)"
    assert dom("ingen — alt var oppdatert", "[9]") != "", \
        "test-only skal også gi FORBUDT — unionen, ikke en av dem"
    assert dom("ingen — alt var oppdatert",
               "ingen — alt var oppdatert") == "", \
        "ingen nye noe sted er den ENESTE kompatible dommen"


def test_p1_preflight_gater_og_er_sideeffektfri(tmp_path):
    """Runde 1: `|| true` slapp ugyldige units gjennom. Runde 2: fiksen
    installerte hjelperskript FØR gaten — en mutasjon i preflighten. Nå:
    verifiseringen skjer i en temporær falsk rot (`verify --root`), og
    testen måler BEGGE egenskapene — gaten OG at ingenting utenfor
    temp-roten røres, heller ikke når gaten avviser."""
    import shutil
    if shutil.which("systemd-analyze") is None:
        pytest.skip("systemd-analyze finnes ikke i dette miljøet")

    kilde = tmp_path / "kilde"
    (kilde / "deploy/staging").mkdir(parents=True)
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python").write_text("#!/bin/sh\n", encoding="utf-8")
    (venv / "bin/python").chmod(0o755)
    for navn in ("helse-sjekk.sh", "restart-helper.sh"):
        (kilde / "deploy/staging" / navn).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (kilde / "deploy/staging/gyldig.service").write_text(
        "[Unit]\nDescription=t\n[Service]\n"
        "ExecStart=/opt/disponit/.venv/bin/python -c pass\n", encoding="utf-8")
    (kilde / "deploy/staging/hjelper.service").write_text(
        "[Unit]\nDescription=t\n[Service]\n"
        "ExecStart=/usr/local/lib/disponit-helse-sjekk\n", encoding="utf-8")
    (kilde / "deploy/staging/ugyldig.service").write_text(
        "[Unit]\nDescription=t\n[Service]\nExecStart=/finnes/ikke/abc\n", encoding="utf-8")

    live = [Path("/usr/local/lib/disponit-helse-sjekk"),
            Path("/usr/local/lib/disponit-restart-helper")]
    for_tilstand = [(p.exists(), p.stat().st_mtime if p.exists() else None)
                    for p in live]

    ok = _bash(f". {LIB_OPP}; preflight_units {kilde} {venv} "
               "gyldig.service hjelper.service")
    assert ok.returncode == 0, ok.stderr + ok.stdout
    # hjelper.service verifiserte mot KANDIDATENS skript i den falske
    # roten — uten at /usr/local/lib ble rørt (målingen under).

    daarlig = _bash(f". {LIB_OPP}; preflight_units {kilde} {venv} "
                    "ugyldig.service gyldig.service")
    assert daarlig.returncode != 0, \
        "en unit med ikke-eksekverbar ExecStart skal stoppe deployen"

    borte = _bash(f". {LIB_OPP}; preflight_units {kilde} {venv} "
                  "mangler.service")
    assert borte.returncode != 0 and "finnes ikke" in borte.stderr

    etter_tilstand = [(p.exists(), p.stat().st_mtime if p.exists() else None)
                      for p in live]
    assert for_tilstand == etter_tilstand, \
        "preflighten rørte levende stier — den skal være sideeffektfri"


def test_p1_preflight_skjer_for_forste_mutasjon():
    """Rekkefølgen ER kontrakten (runde 2): preflight-kallet i opp.sh står
    FØR hver muterende kommando — målt på kilden, så en omflytting ikke
    kan skje stille.

    #178 (Codex P1): checksum-porten måles av SAMME regel. Den sto først som
    «steg 4z» — før vedlikeholdsvinduet, men etter `useradd` og `skriv_cred`
    — mens avbruddsmeldingen lovet at forrige release kjørte som før. Gaten
    her er derfor den SISTE av de to portene: alt som muterer skal ligge etter
    begge. Ankeret er den kjørende linja, ikke kommentaroverskriften, så en
    port kan ikke flyttes ned med overskriften stående igjen.

    Kommentarlinjene fjernes før målingen. Porten leste tidligere hele fila,
    og da var en KOMMENTAR som nevner `useradd` nok til å felle den — den
    målte omtale, ikke kjøring. Det er ikke en strengere port, bare en
    upresis: den ville tvunget kommentarer til å unngå ordene de forklarer.
    """
    opp = "\n".join(
        linje for linje in (ROT / "deploy/staging/opp.sh").read_text(
            encoding="utf-8").splitlines()
        if not linje.lstrip().startswith("#"))
    sjekksumport = opp.index("for base in runtime test; do")
    gate = max(opp.index("preflight_units"),
               opp.index("\ndone\n", sjekksumport))
    for mutasjon in ("groupadd", "useradd", "usermod", "skriv_cred api",
                     "systemctl stop", "install -m 755", "install -m 644",
                     "install -m 440", "ln -sfn"):
        pos = opp.index(mutasjon)
        assert gate < pos, \
            f"mutasjonen {mutasjon!r} står FØR preflight-gaten"


def test_p2_varsel_dsn_regates_paa_verdien_som_skrives(tmp_path):
    """Codex P2: preflighten leser miljøfila i en SUBSHELL og kaster verdien;
    materialiseringen leser fila PÅ NYTT. Byttes fila mellom de to lesingene,
    godkjente preflighten en verdi som aldri skrives — samme funn som
    DISPONIT_MILJO-porten allerede dekker, og løsningen speiler den: sjekken
    står på samme shell-variabel som skrives.

    Beviset er atferd, ikke løfte: credentialblokken kjøres UTEN
    DISPONIT_VARSEL_URL i miljøet — som er nøyaktig tilstanden etter et
    filbytte — og skal avbryte uten å etterlate en varsel-credential.
    """
    rot = tmp_path / "etc-disponit"
    venv = tmp_path / "rot/.venv/bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\necho signatur-stub\n",
                                 encoding="utf-8")
    (venv / "python").chmod(0o755)
    blokk = _credentialblokken().replace("/etc/disponit", str(rot))
    env = {"ROT": str(tmp_path / "rot"), "KILDE": str(ROT),
           "PATH": "/usr/bin:/bin"}
    env.update({n: f"verdi-{n}" for n in (
        "DATABASE_URL", "DISPONIT_KEK", "DISPONIT_TOKEN_PEPPER",
        "DISPONIT_ATT_NOKLER", "DISPONIT_MAC_NOKLER",
        "DISPONIT_TOKEN_ADMIN_URL", "DISPONIT_DOMAINS_URL")})
    # BEVISST TOM, ikke bare fraværende: en fraværende variabel stoppes
    # uansett av `set -u` ved skrivingen — med en dårligere melding, men
    # stoppet. Den TOMME er tilfellet bare re-gaten fanger: `set -u` slipper
    # den gjennom, og uten gaten materialiseres en tom credential som
    # senderen først oppdager ved neste timerkjøring. Første utgave av denne
    # testen brukte fraværende og var grønn også uten gaten — den målte
    # `set -u`, ikke rettelsen.
    env["DISPONIT_VARSEL_URL"] = ""
    import subprocess
    res = subprocess.run(["bash", "-c", "set -eu\n" + blokk],
                         capture_output=True, text=True, env=env)
    assert res.returncode != 0, \
        "blokken skrev credentials med en DSN preflighten aldri så"
    assert "DISPONIT_VARSEL_URL" in res.stdout + res.stderr
    assert not (rot / "varsel/DISPONIT_DATABASE_URL").exists(), \
        "en tom/manglende DSN ble materialisert likevel"


def test_p2_ingen_ny_fillesing_mellom_regaten_og_skrivingen():
    """Og plasseringen, målt på kilden som de andre plasseringstestene:
    mellom re-gaten og `skriv_cred varsel` leses miljøfila ikke igjen — da
    finnes det ikke noe vindu mellom godkjenningen og verdien."""
    opp = (ROT / "deploy/staging/opp.sh").read_text(encoding="utf-8")
    regate = opp.index('[ -z "${DISPONIT_VARSEL_URL:-}" ]')
    skriving = opp.index("skriv_cred varsel DISPONIT_DATABASE_URL")
    assert regate < skriving, "re-gaten står ETTER skrivingen"
    mellom = opp[regate:skriving]
    assert '. "$MILJOFIL"' not in mellom, \
        "miljøfila leses på nytt mellom re-gaten og skrivingen"


def test_p1_varsel_dsn_gates_for_forste_mutasjon():
    """Codex P1 på #68, samme kontrakt som testen over: porten for
    DISPONIT_VARSEL_URL står FØR hver muterende kommando. Første utgave
    kontrollerte den nede ved `skriv_cred` — midt i den muterende fasen,
    etter at tjenester var stoppet — og en preflight som feiler etter første
    mutasjon er ingen preflight. Målt på kilden, så en omflytting ikke kan
    skje stille."""
    opp = (ROT / "deploy/staging/opp.sh").read_text(encoding="utf-8")
    gate = opp.index('[ -n "${DISPONIT_VARSEL_URL:-}" ]')
    for mutasjon in ("groupadd", "useradd", "usermod", "skriv_cred api",
                     "systemctl stop", "install -m 755", "install -m 644",
                     "install -m 440", "ln -sfn"):
        pos = opp.index(mutasjon)
        assert gate < pos, \
            f"mutasjonen {mutasjon!r} står FØR varsel-DSN-porten"


# ---------------------------------------------------------------------------
# Codex P1 (PR-068): credential-katalogen må finnes FØR den skrives i.
#
# `skriv_cred` er `printf > /etc/disponit/<kat>/<navn>` — ingen katalog, ingen
# fil, og feilen kommer i den MUTERENDE fasen, lenge etter preflighten. På en
# vert som har rullet ut før, ligger katalogen igjen fra forrige gang og
# hullet er usynlig. Det er den FERSKE verten som treffer det, og det er
# derfor den som måles her.
# ---------------------------------------------------------------------------

# Blokken starter på SNAPSHOTET, ikke på den første `install -d` (Codex P1,
# runde 4): snapshotet av forrige releases credentials er en del av
# materialiseringen — det er det eneste stedet «verdien før overskrivingen»
# finnes — og det skal derfor kjøres av de samme atferdstestene, inkludert
# den mot en fersk, tom rot.
CRED_START = "CRED_FORVINDU=/etc/disponit/.forvindu"
CRED_SLUTT = "skriv_cred domener DISPONIT_RESOLVERE"


def _credentialblokken() -> str:
    """Materialiseringen av credentials, ordrett fra opp.sh."""
    opp = (ROT / "deploy/staging/opp.sh").read_text(encoding="utf-8")
    start = opp.index(CRED_START)
    slutt = opp.index("\n", opp.index(CRED_SLUTT, start))
    return opp[start:slutt]


def test_p1_hver_skriv_cred_katalog_opprettes_forst():
    """Kilden: hver `skriv_cred <kat>` har en `install -d` av NØYAKTIG den
    katalogen FØR seg. Porten er generell med vilje — den neste
    credential-katalogen noen legger til er dekket uten at noen husker det."""
    blokk = _credentialblokken()
    import re
    opprettet: dict[str, int] = {}
    for m in re.finditer(r"^install -d -m 700 (.+)$", blokk, re.M):
        for sti in m.group(1).split():
            opprettet.setdefault(sti.rsplit("/", 1)[-1], m.start())
    for m in re.finditer(r"^skriv_cred (\w+) ", blokk, re.M):
        kat = m.group(1)
        assert kat in opprettet, \
            f"skriv_cred skriver i /etc/disponit/{kat}, som aldri opprettes"
        assert opprettet[kat] < m.start(), \
            f"/etc/disponit/{kat} opprettes ETTER at det skrives i"


def test_p1_credentials_materialiseres_mot_en_fersk_rot(tmp_path):
    """Og beviset: kjør blokken mot en TOM falsk rot. En manglende katalog
    er da en ikke-null exit, ikke en fil ingen la merke til manglet."""
    rot = tmp_path / "etc-disponit"
    venv = tmp_path / "rot/.venv/bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\necho signatur-stub\n",
                                 encoding="utf-8")
    (venv / "python").chmod(0o755)
    blokk = _credentialblokken().replace("/etc/disponit", str(rot))
    env = {"ROT": str(tmp_path / "rot"), "KILDE": str(ROT),
           "PATH": "/usr/bin:/bin"}
    env.update({n: f"verdi-{n}" for n in (
        "DATABASE_URL", "DISPONIT_KEK", "DISPONIT_TOKEN_PEPPER",
        "DISPONIT_ATT_NOKLER", "DISPONIT_MAC_NOKLER",
        "DISPONIT_TOKEN_ADMIN_URL", "DISPONIT_DOMAINS_URL",
        "DISPONIT_VARSEL_URL", "DISPONIT_PLAN_URL")})
    import subprocess
    res = subprocess.run(["bash", "-c", "set -eu\n" + blokk],
                         capture_output=True, text=True, env=env)
    assert res.returncode == 0, \
        f"credential-materialiseringen feilet på en fersk rot:\n{res.stderr}"
    # SENDERENS dsn, aldri API-ets (Codex P1). Denne asserten sa tidligere
    # `verdi-DATABASE_URL` — den KODIFISERTE feilen reviewet fant: at
    # senderen fikk web-API-rollens DSN og dermed dens rettigheter.
    assert (rot / "varsel/DISPONIT_DATABASE_URL").read_text(
        encoding="utf-8") == "verdi-DISPONIT_VARSEL_URL"
    # 048 (#108): plan-arbeiderens DSN, aldri API-ets — samme klasse.
    assert (rot / "plan/DISPONIT_DATABASE_URL").read_text(
        encoding="utf-8") == "verdi-DISPONIT_PLAN_URL"


def test_hver_installert_timer_blir_ogsa_startet():
    """En timer i `UNITS` som ingen `enable --now` nevner, er en jobb som
    aldri kjører.

    Verre enn det, etter at vedlikeholdsvinduet lærte å stoppe den: da er
    utrullingen selv det som slår jobben AV — og ingenting slår den på igjen.
    Installasjonen (`UNITS`) og oppstarten sto som to lister ingen sammenlignet;
    her er sammenligningen.
    """
    import re
    opp = (ROT / "deploy/staging/opp.sh").read_text(encoding="utf-8")
    # Linjefortsettelser først: enable-listene er brukket over flere linjer,
    # og en port som bare leser den første linjen måler halve lista.
    opp = opp.replace("\\\n", " ")
    units = re.search(r'^UNITS="(.*?)"', opp, re.M | re.S).group(1).split()
    startet = " ".join(re.findall(r"systemctl enable --now (.*)", opp))
    for u in units:
        if u.endswith(".timer"):
            assert u in startet, \
                f"{u} installeres, men blir aldri startet av opp.sh"


def test_selvrevers_speiler_vedlikeholdsvinduet():
    """Codex P1 (#178): reverseringen skal gjenopprette det vinduet stoppet
    — HELE det, ikke et utvalg.

    Søskenporten over dekker suksessløypa: en timer som installeres, må
    også startes. Feilløypa hadde samme hull og ingen port: steg 5 stopper
    elleve enheter, `selvrevers()` startet fire, og dommen ble felt på
    `disponit-api.service` alene. Meldingen «SELVREVERSERT: forrige release
    kjører igjen» kunne derfor være usann for varselsenderen, plan-
    materialisereren, evidensreaperen og domeneverifiseringen samtidig.

    Stopplista og reverseringslista sammenlignes maskinelt, så neste enhet
    noen legger inn i vinduet er dekket uten at noen husker det.
    Oneshot-tjenestene bak en timer er UNNTATT med vilje: de startes av
    timeren sin, aldri direkte — det er formen steg 8 bruker, og å starte
    en oneshot her ville kjørt jobben nå i stedet for å gjenopprette
    timeplanen.
    """
    import re
    opp = (ROT / "deploy/staging/opp.sh").read_text(encoding="utf-8")
    opp = opp.replace("\\\n", " ")
    stoppet = {e for liste in re.findall(r"systemctl stop (.*)", opp)
               for e in liste.split() if e.startswith("disponit-")}
    assert stoppet, "fant ingen `systemctl stop` — porten måler ingenting"

    blokk = opp[opp.index("SELVREVERS_ENHETER="):
                opp.index("\n}\n", opp.index("selvrevers() {"))]
    reversert = {e for liste in re.findall(r"systemctl start (.*)", blokk)
                 for e in liste.split() if e.startswith("disponit-")}
    reversert.update(re.search(r'SELVREVERS_ENHETER="(.*?)"',
                               blokk, re.S).group(1).split())

    for enhet in stoppet:
        if enhet.endswith(".service") and \
                enhet[:-len(".service")] + ".timer" in stoppet:
            continue    # oneshot bak en timer — timerens å starte
        assert enhet in reversert, \
            f"{enhet} stoppes av vedlikeholdsvinduet, men selvrevers() " \
            f"starter den aldri igjen"

    # Codex P1, andre halvdel: «SELVREVERSERT» skal ikke kunne skrives på
    # API-et alene. Dommen måles på NØYAKTIG det settet som ble startet —
    # samme variabel, ikke en parallell liste som kan drifte fra den.
    startet = blokk[:blokk.index('NEDE=""')]
    maalt = blokk[blokk.index('NEDE=""'):blokk.index('if [ -z "$NEDE" ]')]
    settet = "$AKTIVE_FOR_VINDUET"
    assert settet in startet, \
        "startlista leser ikke snapshotet av det som var i drift"
    assert settet in maalt, \
        "reverseringsdommen måler ikke det samme settet den nettopp startet"


def test_selvrevers_gjenoppretter_settet_som_var_i_drift():
    """Codex P2 (#178, runde 2): `is-enabled` er ikke «var i drift».

    `systemctl --help` skiller de to eksplisitt: `is-enabled` sjekker
    unit-FILEN, `is-active` kjøretilstanden. Reverseringen brukte den
    første som proxy for den andre, og gjetningen er feil i BEGGE
    retninger — en timer eller wcag-arbeider en operatør bevisst hadde
    stoppet er fortsatt enablet og ble dermed AKTIVERT av en mislykket
    deploy, mens en enhet som kjørte uten å være enablet ble stående nede.
    Utrullingen har mandat til å gjenopprette, aldri til å innføre.

    Tilstanden finnes bare ett sted, og bare før steg 5 river den: i
    driften. Porten måler at snapshotet tas DER — før første `systemctl
    stop` — og at reverseringen ikke lenger spør unit-fila om hva som
    kjørte.
    """
    opp = "\n".join(
        linje for linje in (ROT / "deploy/staging/opp.sh").read_text(
            encoding="utf-8").splitlines()
        if not linje.lstrip().startswith("#"))
    i_snapshot = opp.index("AKTIVE_FOR_VINDUET=\"$AKTIVE_FOR_VINDUET")
    assert i_snapshot < opp.index("systemctl stop"), \
        "snapshotet tas ETTER at vinduet har stoppet enhetene — da måler" \
        " det tilstanden vinduet selv lagde, ikke den det skal gjenopprette"
    snapshot = opp[opp.rindex("for enhet in", 0, i_snapshot):i_snapshot]
    assert "is-active" in snapshot, \
        "snapshotet leser ikke kjøretilstanden (`is-active`)"

    blokk = opp[opp.index("selvrevers() {"):
                opp.index("\n}\n", opp.index("selvrevers() {"))]
    assert "is-enabled" not in blokk, \
        "selvrevers() spør fortsatt unit-fila (`is-enabled`) om hva som" \
        " kjørte før vinduet — det er ikke den samme opplysningen"
    assert "$AKTIVE_FOR_VINDUET" in blokk, \
        "selvrevers() bruker ikke snapshotet i det hele tatt"


def test_selvrevers_maler_rullbakk_for_forste_start():
    """Codex P1 (#178, runde 2): gamle arbeidere skal ikke startes mot et
    nytt skjema.

    Steg 6 migrerer runtime FØR test, og steg 6b kommer etter begge. Går
    runtime grønt og noe etterpå rødt, står runtime-basen på kandidatens
    forward-only-sett mens `aktiv` peker på forrige release. API-et nekter
    selv (`krev_migrasjonstilstand`), men M-37 og timerne har ingen slik
    bootport: de ville kjørt videre mot et skjema skriptet nettopp erklærte
    inkompatibelt, og feilgrenen stopper dem aldri igjen.

    Porten måler REKKEFØLGEN — dommen skal felles før første `systemctl
    start`, ikke etterpå. En gate som ligger etter starten er ingen gate,
    samme dom `test_p1_preflight_skjer_for_forste_mutasjon` feller for
    preflighten.
    """
    opp = "\n".join(
        linje for linje in (ROT / "deploy/staging/opp.sh").read_text(
            encoding="utf-8").splitlines()
        if not linje.lstrip().startswith("#"))
    blokk = opp[opp.index("selvrevers() {"):
                opp.index("\n}\n", opp.index("selvrevers() {"))]
    assert "rollbackmaal_kompatibelt" in blokk, \
        "selvrevers() måler ikke rullbakk-kompatibiliteten i det hele tatt" \
        " — den ville startet gamle arbeidere mot et migrert skjema"
    assert blokk.index("rollbackmaal_kompatibelt") \
        < blokk.index("systemctl start"), \
        "rullbakk-gaten ligger ETTER første `systemctl start` — da er" \
        " arbeiderne alt i drift mot skjemaet gaten skulle nektet dem"
    # Dommen skal AVBRYTE, ikke advare: en rød gate med exit 0 lar
    # løypa fortsette ned i den samme starten.
    gate = blokk[blokk.index("rollbackmaal_kompatibelt"):
                 blokk.index("systemctl start")]
    assert "exit 1" in gate, \
        "rullbakk-gaten advarer, men avbryter ikke — enhetene startes" \
        " likevel like etterpå"


def test_rullbakkdommen_males_pa_basen_som_faktisk_bootes():
    """Codex P2 (#178, runde 6): gaten og kjøreren skal lese samme base.

    F13 (runde 4) gjorde at forrige release booter på den TILBAKESTILTE
    `DATABASE_URL` fra credential-snapshotet. Dommen leste likevel
    kandidatens `DISPONIT_MIGRATOR_URL`. Peker de to på samme base — som
    de gjør når ingenting er flyttet — er det to roller mot samme rader
    og dommen er den samme. Flyttet DENNE utrullingen basen, måler gaten
    kandidatens base mens kjøreren starter mot forrige releases: hver
    enhet blir stående stoppet på et migrasjonssett ingen av dem
    noensinne vil se.

    Porten måler KILDEN gaten leser, ikke bare at den finnes: DSN-en skal
    komme fra snapshotet — `api/DATABASE_URL`, nøyaktig fila
    `disponit-api.service` sin `LoadCredential` peker på og
    `krev_migrasjonstilstand` feller sin dom gjennom — og lesingen skal
    skje FØR dommen felles. Fallbacken til kandidatens migrator-DSN blir
    stående for den ferske verten som ikke hadde noen credentials før
    vinduet.
    """
    opp = "\n".join(
        linje for linje in (ROT / "deploy/staging/opp.sh").read_text(
            encoding="utf-8").splitlines()
        if not linje.lstrip().startswith("#"))
    blokk = opp[opp.index("selvrevers() {"):
                opp.index("\n}\n", opp.index("selvrevers() {"))]
    assert '$CRED_FORVINDU/api/DATABASE_URL' in blokk, \
        "rullbakk-gaten leser ikke DSN-en fra credential-snapshotet — den" \
        " måler en annen base enn den forrige release faktisk bootes mot"
    i_kilde = blokk.index('$CRED_FORVINDU/api/DATABASE_URL')
    i_dom = blokk.index("rollbackmaal_kompatibelt")
    assert i_kilde < i_dom, \
        "snapshot-DSN-en leses ETTER at dommen er felt — da er lesingen" \
        " uten virkning på dommen"
    kall = blokk[i_dom:blokk.index("\n", i_dom)]
    assert "DISPONIT_MIGRATOR_URL" not in kall, \
        "dommen felles fortsatt direkte mot kandidatens migrator-DSN"
    # Fallbacken skal bestå: uten den står den ferske verten uten noen
    # base å måle mot, og «umålt er ikke kompatibelt» ville nektet en
    # reversering som i dag er tillatt.
    assert "DISPONIT_MIGRATOR_URL" in blokk, \
        "fallbacken til kandidatens migrator-DSN er borte — en fersk vert" \
        " uten credentials før vinduet har da ingen base å måle mot"


def test_hver_feil_i_vinduet_kaller_selvrevers():
    """Cursor P2 5: koblingen mellom vinduet og reverseringen måles.

    `selvrevers()` kan være perfekt og likevel aldri kalles. Feilsonen er
    avgrenset og kjent: fra `systemctl stop` (steg 5) til `ln -sfn` (steg 7,
    release-byttet). Hver kommando som kan feile DER, må gå til reverseringen
    — ellers etterlater et avbrudd tjenestene nede med symlinken pekende på
    en release ingenting starter.

    Steg 7 og utover er UTENFOR: der er symlinken alt byttet, og å starte
    forrige release ville ikke lenger vært å boote den. Det er notert som
    pre-existing og ligger utenfor #172s ramme.
    """
    opp = "\n".join(
        linje for linje in (ROT / "deploy/staging/opp.sh").read_text(
            encoding="utf-8").splitlines()
        if not linje.lstrip().startswith("#"))
    vindu = opp[opp.index("systemctl stop"):opp.index("ln -sfn")]
    # Migrasjonene (steg 6) og deploy-porten (6b) er de to kommandoene i
    # vinduet som kan feile. Begge skal ende i reverseringen.
    assert "migrer.py" in vindu and "deployport-modultyper.py" in vindu, \
        "vinduet inneholder ikke lenger de kommandoene porten er skrevet for"
    for kall in ('selvrevers "migrasjon', 'selvrevers "deploy-port'):
        assert kall in vindu, \
            f"{kall}...\" mangler mellom vedlikeholdsvinduet og release-byttet"
    assert vindu.count("selvrevers ") == 2, \
        "antall selvrevers-kall i vinduet har endret seg — er en ny " \
        "feilkilde lagt til uten reversering, eller en fjernet?"


def test_p2_feilsonens_forutsetninger_gates_for_vinduet():
    """Cursor P2-2/P2-3 (#178, runde 4): det vinduet TRENGER, måles før
    vinduet åpnes.

    To forutsetninger manglet en gate, og begge ville først vist seg INNE i
    feilsonen — der utfallet ikke er «avbrutt med alt urørt», men en
    reversering som selv er svekket:

    * Migrasjons-DSN-ene. `DISPONIT_VARSEL_URL` og `DISPONIT_PLAN_URL`
      re-gates fordi preflighten leser miljøfila i en SUBSHELL og den
      autoritative lesingen leser den på nytt; de to migrasjons-DSN-ene ble
      lest på nøyaktig samme måte, uten tilsvarende gate. En tom verdi etter
      et filbytte feller steg 6 — etter tjenestestoppen.
    * `psql`. #172 gjorde `selvrevers()` avhengig av
      `rollbackmaal_kompatibelt`, som leser basen med `psql`. Mangler
      klienten, er dommen «umålt», porten er fail-closed, og HVER enhet blir
      stående stoppet. En manglende pakke ble dermed full nedetid.
    """
    opp = "\n".join(
        linje for linje in (ROT / "deploy/staging/opp.sh").read_text(
            encoding="utf-8").splitlines()
        if not linje.lstrip().startswith("#"))
    vindu = opp.index("systemctl stop")
    for gate, hva in (
            ("DISPONIT_MIGRATOR_URL DISPONIT_TEST_MIGRATOR_DSN",
             "migrasjons-DSN-ene re-gates ikke på den autoritative lesingen"),
            ("command -v psql",
             "psql — som rullbakk-gaten i selvrevers() leser basen med —"
             " sjekkes ikke før vinduet")):
        assert gate in opp, hva
        assert opp.index(gate) < vindu, \
            f"{hva}: gaten står ETTER at vedlikeholdsvinduet har stoppet" \
            f" tjenestene, og da er utfallet ikke lenger «alt urørt»"


def _migrasjonsdsn_regaten() -> str:
    """Re-gaten på migrasjons-DSN-ene, ORDRETT fra opp.sh.

    Samme grep som `_credentialblokken()`: fragmentet testes der det bor.
    """
    opp = (ROT / "deploy/staging/opp.sh").read_text(encoding="utf-8")
    start = opp.index("for dsn_navn in DISPONIT_MIGRATOR_URL")
    return opp[start:opp.index("\ndone\n", start) + len("\ndone\n")]


def test_migrasjonsdsn_regaten_maler_identitet_ikke_bare_tomhet():
    """Codex P2 (#178, runde 7): re-gaten skal binde migrasjonen til den
    basen checksum-porten FAKTISK målte.

    Runde 4 lukket den tomme verdien: forsvant en DSN i et filbytte mellom
    preflightens subshell-lesing og den autoritative lesingen, ble det først
    oppdaget i steg 6 — inne i vinduet. Men en konfigurasjonsstyring som
    bytter fila skriver sjelden en TOM verdi; den skriver en ANNEN. Da var
    gaten grønn, og steg 6 migrerte en base ingen port hadde lest historikken
    til: nøyaktig 23/8-klassen (endret kjørt migrasjon) sluppet inn igjen bak
    porten som finnes for å stoppe den, og oppdaget etter tjenestestoppen.

    Beviset er atferd: fragmentet kjøres med en DSN som er ikke-tom og
    forskjellig fra den målte. Tomhetssjekken alene ville vært grønn her.
    """
    import subprocess
    gate = _migrasjonsdsn_regaten()
    maalt = "postgresql://migrator@/disponit"
    testdsn = "postgresql://migrator@/disponit_test"

    def kjor(migrator_url):
        return subprocess.run(
            ["bash", "-c", "set -eu\n" + gate], capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin",
                 "MILJOFIL": "/etc/disponit/staging.env",
                 "PREFLIGHT_DISPONIT_MIGRATOR_URL": maalt,
                 "PREFLIGHT_DISPONIT_TEST_MIGRATOR_DSN": testdsn,
                 "DISPONIT_MIGRATOR_URL": migrator_url,
                 "DISPONIT_TEST_MIGRATOR_DSN": testdsn})

    uendret = kjor(maalt)
    assert uendret.returncode == 0, \
        "gaten avbrøt en utrulling der miljøfila sto helt stille: " \
        + uendret.stdout + uendret.stderr
    byttet = kjor("postgresql://migrator@/en-helt-annen-base")
    assert byttet.returncode != 0, \
        "steg 6 ville migrert en base checksum-porten aldri leste"
    assert "DISPONIT_MIGRATOR_URL" in byttet.stdout + byttet.stderr, \
        "operatøren får ikke vite HVILKEN DSN som flyttet seg"
    # Og den tomme, som runde 4 lukket, står fortsatt: den har en egen jobb.
    # `psycopg.connect("")` faller tilbake på libpq-defaultene, så to tomme
    # verdier er identiske uten å være den samme basen.
    assert kjor("").returncode != 0, "en tom DSN slipper gjennom igjen"


def test_checksumporten_maler_dsn_snapshotet_regaten_sammenligner_med():
    """Samme funn, andre halvdel: identitetsgaten er bare verdt noe hvis
    porten målte NØYAKTIG den verdien den sammenlignes med.

    Leste checksum-løkka sin egen DSN rett fra miljøfila i subshellen, kunne
    de to basene i løkka til og med vært lest fra hver sin fil-versjon. Målt
    på kilden: snapshotet tas ÉN gang, før løkka, og løkka bruker det.
    """
    opp = (ROT / "deploy/staging/opp.sh").read_text(encoding="utf-8")
    lokke = opp.index("for base in runtime test; do")
    assert opp.index("PREFLIGHT_DISPONIT_MIGRATOR_URL=$(") < lokke, \
        "DSN-snapshotet tas inne i løkka — da måles basene mot hver sin lesing"
    blokk = opp[lokke:opp.index("\ndone\n", lokke)]
    for var in ("DISPONIT_MIGRATOR_URL", "DISPONIT_TEST_MIGRATOR_DSN"):
        assert f"url=$PREFLIGHT_{var}" in blokk, \
            f"checksum-porten måler ikke snapshotet av {var}"
        assert f"url=${var}" not in blokk, \
            f"{var} leses på nytt i løkka — porten kan da måle en annen base" \
            f" enn den re-gaten i steg 4 slipper videre"


def _credgjenoppretting() -> str:
    """Tilbakestillingen av credentials i `selvrevers()`, ordrett fra opp.sh.

    Samme grep som `_credentialblokken()`: fragmentet testes der det bor.
    """
    opp = (ROT / "deploy/staging/opp.sh").read_text(encoding="utf-8")
    blokk = opp[opp.index("selvrevers() {"):
                opp.index("\n}\n", opp.index("selvrevers() {"))]
    start = blokk.index('GJENOPPRETTET=""')
    slutt = blokk.index("\n", blokk.index("done", start))
    return blokk[start:slutt]


def test_selvrevers_gjenoppretter_credentialene_fra_for_vinduet(tmp_path):
    """Codex P1 (#178, runde 4): reverseringen skal starte forrige release på
    forrige releases KONFIGURASJON, ikke bare på forrige releases binær.

    Steg 4 overskriver `/etc/disponit/*` med kandidatens verdier, og
    `LoadCredential` leser fila på nytt ved HVER aktivering. `selvrevers()`
    starter så forrige release fra nøyaktig de filene. Skarpeste tilfellet er
    `DISPONIT_SEMANTIKK_MILJO`: den regnes ut med KANDIDATENS kode (`$KILDE`)
    og måles ved oppstart av FORRIGE releases egen
    `verifiser_oppstartsmiljo()` — så en signaturform som endret seg mellom
    releasene gjør at forrige release nekter å starte, på en verdi denne
    utrullingen selv skrev. Samme klasse: en nøkkel rotert i miljøfila, eller
    en DSN som peker et nytt sted.

    De tre forrige rundene på denne funksjonen målte hvilke ENHETER som
    startes. Denne måler tilstanden de startes MOT, og den måles som
    ATFERD — en full rundtur: gammel verdi på disk → materialiseringen
    overskriver → tilbakestillingen henter den tilbake.
    """
    rot = tmp_path / "etc-disponit"
    (rot / "api").mkdir(parents=True)
    (rot / "api/DISPONIT_SEMANTIKK_MILJO").write_text(
        "signatur-fra-forrige-release", encoding="utf-8")
    venv = tmp_path / "rot/.venv/bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\necho signatur-fra-kandidaten\n",
                                 encoding="utf-8")
    (venv / "python").chmod(0o755)
    env = {"ROT": str(tmp_path / "rot"), "KILDE": str(ROT),
           "PATH": "/usr/bin:/bin"}
    env.update({n: f"verdi-{n}" for n in (
        "DATABASE_URL", "DISPONIT_KEK", "DISPONIT_TOKEN_PEPPER",
        "DISPONIT_ATT_NOKLER", "DISPONIT_MAC_NOKLER",
        "DISPONIT_TOKEN_ADMIN_URL", "DISPONIT_DOMAINS_URL",
        "DISPONIT_VARSEL_URL", "DISPONIT_PLAN_URL")})
    import subprocess

    def kjor(fragment: str, ekstra: dict[str, str] | None = None):
        return subprocess.run(
            ["bash", "-c", "set -eu\n"
             + fragment.replace("/etc/disponit", str(rot))],
            capture_output=True, text=True, env={**env, **(ekstra or {})})

    res = kjor(_credentialblokken())
    assert res.returncode == 0, \
        f"materialiseringen feilet:\n{res.stdout}\n{res.stderr}"
    assert (rot / "api/DISPONIT_SEMANTIKK_MILJO").read_text(
        encoding="utf-8") == "signatur-fra-kandidaten", \
        "materialiseringen skrev ikke kandidatens verdi — testen måler ikke" \
        " lenger tilfellet den er skrevet for"

    res = kjor(_credgjenoppretting(),
               {"CRED_FORVINDU": str(rot / ".forvindu")})
    assert res.returncode == 0, \
        f"tilbakestillingen feilet:\n{res.stdout}\n{res.stderr}"
    assert (rot / "api/DISPONIT_SEMANTIKK_MILJO").read_text(
        encoding="utf-8") == "signatur-fra-forrige-release", \
        "selvrevers() starter forrige release på KANDIDATENS credentials —" \
        " symlinken er urørt, men konfigurasjonen prosessen booter på er det" \
        " ikke, og «SELVREVERSERT» lover det motsatte"
    # Skriver over, rydder ikke: en credential kandidaten la til blir
    # liggende. Forrige releases units laster bare det deres egen
    # `LoadCredential` navngir, så den er inert — mens et `rm -rf` her ville
    # lagt et destruktivt steg inn i selve feilhåndteringen.
    assert (rot / "api/DISPONIT_KEK").exists(), \
        "tilbakestillingen slettet en credential i stedet for å skrive over"


def test_selvrevers_credentialsnapshotet_tas_for_forste_skriving():
    """Og plasseringen, målt på kilden: snapshotet står FØR første
    `skriv_cred`, og tilbakestillingen FØR første `systemctl start`.

    Et snapshot tatt etter den første skrivingen måler kandidatens verdier og
    kaller dem «før vinduet» — samme feilklasse som `is-enabled`-gjetningen i
    runde 2. En tilbakestilling etter første start booter forrige release på
    ny konfigurasjon og retter den først etterpå.

    Snapshotet måles også som GENERISK: en enumerert liste over dagens
    credential-kataloger drifter fra `skriv_cred` neste gang noen legger til
    en, og det er nøyaktig hullet `test_p1_hver_skriv_cred_katalog_opprettes_forst`
    finnes for på skrivesiden.
    """
    opp = "\n".join(
        linje for linje in (ROT / "deploy/staging/opp.sh").read_text(
            encoding="utf-8").splitlines()
        if not linje.lstrip().startswith("#"))
    snapshot = opp.index("CRED_FORVINDU=/etc/disponit/.forvindu")
    assert snapshot < opp.index("skriv_cred api"), \
        "credential-snapshotet tas ETTER at credentials er skrevet — da er" \
        " det kandidatens verdier det bevarer, ikke forrige releases"
    assert "for kat in /etc/disponit/*/" in opp, \
        "snapshotet enumererer kataloger i stedet for å globbe — neste" \
        " skriv_cred-katalog blir da stående ubevart uten at noen ser det"

    blokk = opp[opp.index("selvrevers() {"):
                opp.index("\n}\n", opp.index("selvrevers() {"))]
    assert "$CRED_FORVINDU" in blokk, \
        "selvrevers() rører ikke credentialene — den starter forrige" \
        " release på kandidatens konfigurasjon"
    assert blokk.index("$CRED_FORVINDU") < blokk.index("systemctl start"), \
        "credentialene tilbakestilles ETTER første `systemctl start` — da" \
        " har forrige release alt lastet kandidatens verdier"


def _checksumporten() -> str:
    """Deploy-sidens checksum-preflight, ORDRETT fra opp.sh.

    Samme grep som `_credentialblokken()`: koden testes der den bor, ikke i
    en kopi som kan drifte fra den. Interpreteren byttes (suitens, ikke
    `$ROT/.venv/bin/python`) — det er harnesset, ikke koden.
    """
    import re
    opp = (ROT / "deploy/staging/opp.sh").read_text(encoding="utf-8")
    return re.search(r"<<'PYPRE'\n(.*?)\nPYPRE\n", opp, re.S).group(1)


@pg
def test_checksumporten_feller_endret_kjort_migrasjon(migrator, tmp_path):
    """056-hendelsen (23/8) spilt av mot en EKTE base, gjennom samme kode
    deployen kjører.

    CI-porten i `test_migrasjonsfasit` feller klassen før merge, målt mot
    fasitfila. Denne halvdelen måles mot BASENS egne rader og er den siste
    skansen: den er det som skal ha stoppet deployen 23/8, før tjenestene
    ble stoppet på en kommentarlinje. Inline-Python i en heredoc hadde ingen
    negativ test i det hele tatt — samme hullklasse som runde 1s inline
    bash.

    Rekkefølgen er grønn først, så rød: en negativ test alene beviser ikke
    at porten kan si ja, og en port som alltid sier nei ville vært like
    ubrukelig.
    """
    import hashlib
    import os
    import subprocess
    import sys
    port = tmp_path / "checksumport.py"
    port.write_text(_checksumporten(), encoding="utf-8")

    def kjor():
        return subprocess.run(
            [sys.executable, str(port)], cwd=ROT, capture_output=True,
            text=True,
            env={**os.environ, "DISPONIT_MIGRATOR_URL": MIGRATOR_DSN})

    gronn = kjor()
    assert gronn.returncode == 0, \
        f"porten er rød på en base CI selv har migrert fra treet:\n" \
        f"{gronn.stdout}{gronn.stderr}"
    assert "byte-identiske" in gronn.stdout, gronn.stdout

    # Den nyeste kjørte migrasjonen: minst sannsynlig å bli kjørt om av en
    # annen test i vinduet der checksummen står feil.
    versjon, fasit = migrator.execute(
        "SELECT versjon, checksum FROM migrasjoner"
        " WHERE checksum IS NOT NULL ORDER BY versjon DESC LIMIT 1").fetchone()
    fil = next((ROT / "platform/core/db/migrations").glob(f"{versjon:03d}_*.sql"))
    assert hashlib.sha256(fil.read_bytes()).hexdigest() == fasit, \
        f"{fil.name} er alt i utakt med basen — testen måler ikke det den tror"
    try:
        # Basen sier én ting, fila en annen: nøyaktig 056-tilstanden.
        # Gjenopprettes i `finally` — de andre kjører-testene leser samme rad
        # (samme mønster som test_endret_historisk_migrasjon_avvises).
        migrator.execute("UPDATE migrasjoner SET checksum=%s WHERE versjon=%s",
                         ("0" * 64, versjon))
        migrator.commit()
        rod = kjor()
    finally:
        migrator.execute("UPDATE migrasjoner SET checksum=%s WHERE versjon=%s",
                         (fasit, versjon))
        migrator.commit()

    assert rod.returncode != 0, \
        f"porten slapp gjennom en kjørt migrasjon som ikke matcher basen:\n" \
        f"{rod.stdout}"
    assert fil.name in rod.stdout and "checksum-avvik" in rod.stdout, \
        f"porten avbrøt, men navngir ikke fila operatøren må se på:\n" \
        f"{rod.stdout}"


def _reviewede_versjoner() -> set:
    """Versjonene `herd_historikk` faktisk kan backfille, fra herdingens
    egen kilde — samme importvei som `migrer.last_bootstrap` bruker."""
    import importlib.util
    spek = importlib.util.spec_from_file_location(
        "migrasjon_bootstrap", ROT / "deploy/staging/migrasjon-bootstrap.py")
    modul = importlib.util.module_from_spec(spek)
    spek.loader.exec_module(modul)
    return set(modul.REVIEWEDE_CHECKSUMS)


def _med_null_checksum(migrator, versjon):
    """Kontekst: én kjørt rad står med NULL checksum, kolonnen nullable.
    Gjenopprettes etterpå — de andre kjører-testene leser samme rader."""
    import contextlib

    @contextlib.contextmanager
    def _kontekst():
        fasit, = migrator.execute(
            "SELECT checksum FROM migrasjoner WHERE versjon=%s",
            (versjon,)).fetchone()
        try:
            migrator.execute("ALTER TABLE migrasjoner"
                             " ALTER COLUMN checksum DROP NOT NULL")
            migrator.execute("UPDATE migrasjoner SET checksum=NULL"
                             " WHERE versjon=%s", (versjon,))
            migrator.commit()
            yield
        finally:
            migrator.execute(
                "UPDATE migrasjoner SET checksum=%s WHERE versjon=%s",
                (fasit, versjon))
            migrator.execute("ALTER TABLE migrasjoner"
                             " ALTER COLUMN checksum SET NOT NULL")
            migrator.commit()
    return _kontekst()


def _kjor_checksumporten(tmp_path):
    import os
    import subprocess
    import sys
    port = tmp_path / "checksumport.py"
    port.write_text(_checksumporten(), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(port)], cwd=ROT, capture_output=True, text=True,
        env={**os.environ, "DISPONIT_MIGRATOR_URL": MIGRATOR_DSN})


@pg
def test_checksumporten_slipper_uherdet_legacyhistorikk(migrator, tmp_path):
    """Codex P2 (runde 1): uherdet LEGACY-historikk er ikke et avvik.

    001/002 registrerte seg selv i sin egen SQL, uten checksum. En base midt
    i herdingen har derfor NULL på nettopp de radene, og det er tilstanden
    `migrer.py` er BYGGET for: `herd_historikk` backfiller dem fra
    REVIEWEDE_CHECKSUMS før 003. En port som avbryter der, stenger den
    eneste veien ut av tilstanden den klager på.

    Versjonen hentes fra REVIEWEDE_CHECKSUMS, ikke som «nyeste rad» —
    testen skrev opprinnelig NULL på den NYESTE migrasjonen og kodifiserte
    dermed nøyaktig den feilen Codex felte i runde 2 (se søsterporten
    under). Grensen porten skal måle er hva HERDINGEN kan fylle, og den
    grensen bor i herdingens egen konstant.

    Den manglende KOLONNEN er dekket av `test_kjorer_og_kryptering`-
    søsknene, som eier DDL-en for den tilstanden.
    """
    versjon = min(_reviewede_versjoner())
    with _med_null_checksum(migrator, versjon):
        res = _kjor_checksumporten(tmp_path)

    assert res.returncode == 0, \
        f"porten avbrøt på en uherdet LEGACY-rad ({versjon:03d}) og stengte" \
        f" legacy-oppgraderingen:\n{res.stdout}{res.stderr}"
    # Ikke hoppet over STILLE: operatøren skal se at basen står halvveis.
    assert f"{versjon:03d}" in res.stdout and "uten checksum" in res.stdout, \
        f"den uherdede raden er usynlig i deploy-loggen:\n{res.stdout}"


@pg
def test_checksumporten_feller_uherdet_ikkelegacy_rad(migrator, tmp_path):
    """Codex P2 (runde 2): en NULL herdingen ikke kan fylle, er et avvik.

    Runde 1 slapp ALLE NULL-rader gjennom med den begrunnelsen at
    `kjorer.py` bare sammenligner når raden HAR en checksum. Det er sant om
    kjøreren, men det er ikke kjøreren som feller tilstanden: `migrer.py`
    kaller `herd_historikk` UBETINGET, og den backfiller kun
    REVIEWEDE_CHECKSUMS før den kaster `HerdingFeilet` på enhver NULL som
    står igjen. En kjørt versjon utenfor det settet er derfor et GARANTERT
    stopp i steg 6 — etter at tjenestene er stoppet. Det er 056-klassen
    porten finnes for, og den skal felles før første mutasjon.

    Kontrakten måles mot herdingens egen konstant, så et utvidet
    REVIEWEDE_CHECKSUMS flytter porten med herdingen i stedet for å bli
    motsagt av den.
    """
    versjon, = migrator.execute(
        "SELECT versjon FROM migrasjoner ORDER BY versjon DESC LIMIT 1"
    ).fetchone()
    assert versjon not in _reviewede_versjoner(), \
        "basen har ingen kjørt migrasjon utenfor REVIEWEDE_CHECKSUMS —" \
        " testen måler ikke det den tror"

    with _med_null_checksum(migrator, versjon):
        res = _kjor_checksumporten(tmp_path)

    assert res.returncode != 0, \
        f"porten slapp gjennom en NULL-rad herdingen ikke kan fylle —" \
        f" migrer.py ville stoppet deployen ETTER tjenestestoppen:\n" \
        f"{res.stdout}{res.stderr}"
    assert f"{versjon:03d}" in res.stdout, \
        f"porten avbrøt, men navngir ikke versjonen operatøren må se på:\n" \
        f"{res.stdout}"


@pg
def test_checksumporten_feller_migrasjon_borte_fra_treet(migrator, tmp_path):
    """Cursor P2 (#178, runde 2): `fil is None`-grenen hadde ingen test.

    De to søskenportene dekker checksum-AVVIK og uherdet historikk. Den
    tredje avvisningsgrenen — en versjon som er kjørt i basen, men som
    kandidat-treet ikke har fila til — sto umålt, og en regresjon der ville
    passert grønt mens deployen fortsatt stopper tjenestene i steg 6 på
    samme tilstand (bootportens EKSAKTE match feller den).

    Raden fabrikkeres i stedet for at en fil flyttes: treet er delt med de
    andre portene i samme kjøring, og en `rename` der ville vært en
    sidevirkning utenfor testens egen tilstand. Versjonen velges høyere enn
    alt som finnes, så den ikke kan kollidere med en fremtidig migrasjon.
    """
    kat = ROT / "platform/core/db/migrations"
    versjon = 999
    assert not list(kat.glob(f"{versjon:03d}_*.sql")), \
        f"{versjon:03d} finnes i treet — testen måler ikke det den tror"
    try:
        migrator.execute("INSERT INTO migrasjoner (versjon, checksum)"
                         " VALUES (%s, %s)", (versjon, "0" * 64))
        migrator.commit()
        res = _kjor_checksumporten(tmp_path)
    finally:
        migrator.execute("DELETE FROM migrasjoner WHERE versjon=%s",
                         (versjon,))
        migrator.commit()

    assert res.returncode != 0, \
        f"porten slapp gjennom en versjon som er kjørt i basen men mangler" \
        f" i treet:\n{res.stdout}{res.stderr}"
    assert "borte fra treet" in res.stdout and f"{versjon:03d}" in res.stdout, \
        f"porten avbrøt, men sier ikke hvilken versjon som mangler:\n" \
        f"{res.stdout}"


def test_checksumporten_feller_to_filer_med_samme_versjon(tmp_path):
    """Codex P2 (#178, runde 3): duplikat versjonsprefiks felles i porten.

    Porten bygde versjonskartet som en dict-comprehension, og da vinner den
    siste fila stille. Kjøreren gjør ikke det: `kjorer.py` itererer
    `sorted(glob(...))` og kjører BEGGE filene, og
    `api.app.forventede_migrasjoner()` beholder begge tallene. Mot basens
    `versjon`-kolonne — PRIMARY KEY, altså unik — kan `faktisk != forventet`
    i `krev_migrasjonstilstand` da aldri bli usann igjen: API-et er permanent
    bootnektet, oppdaget i steg 6/8, ETTER at tjenestene er stoppet. Det er
    samme klasse som resten av denne porten finnes for, og den hører før
    første mutasjon.

    Treet fabrikkeres i en tmp-cwd i stedet for at en duplikatfil legges i
    det ekte: treet deles med de andre portene i samme kjøring. Det går fordi
    duplikatsjekken felles FØR porten importerer herdingen og før den åpner
    basen — testen trenger derfor verken Postgres eller `@pg`. At det ekte
    treet er duplikatfritt måles allerede av den grønne halvdelen i
    `test_checksumporten_feller_endret_kjort_migrasjon`.
    """
    import os
    import subprocess
    import sys

    def kjor_mot(kat_filer, navn):
        rot = tmp_path / navn
        kat = rot / "platform/core/db/migrations"
        kat.mkdir(parents=True)
        for filnavn in kat_filer:
            (kat / filnavn).write_text("SELECT 1;\n", encoding="utf-8")
        port = rot / "checksumport.py"
        port.write_text(_checksumporten(), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(port)], cwd=rot, capture_output=True,
            text=True, env={**os.environ, "DISPONIT_MIGRATOR_URL": "x"})

    # Grønn først: distinkte prefikser skal ikke felles av denne grenen.
    # Porten går videre og feiler senere (ingen herdingsmodul i tmp-treet),
    # og det er nettopp det som skiller de to.
    ok = kjor_mot(["001_a.sql", "002_b.sql"], "unik")
    assert "deler versjonsnummer" not in ok.stdout, \
        f"porten feller et duplikatfritt tre:\n{ok.stdout}{ok.stderr}"

    rod = kjor_mot(["007_alfa.sql", "007_beta.sql"], "duplikat")
    assert rod.returncode != 0, \
        f"porten godkjente et tre der to filer deler versjonsnummer —" \
        f" API-et kunne ikke bootet igjen etter deployen:\n" \
        f"{rod.stdout}{rod.stderr}"
    assert "deler versjonsnummer" in rod.stdout, \
        f"porten avbrøt, men ikke på duplikatet:\n{rod.stdout}{rod.stderr}"
    assert "007_alfa.sql" in rod.stdout and "007_beta.sql" in rod.stdout, \
        f"porten navngir ikke begge filene operatøren må velge mellom:\n" \
        f"{rod.stdout}"


@pg
def test_rydd_pending_tar_kun_foreldede(migrator, miljo, monkeypatch,
                                        capsys):
    """V3: timeren rydder PENDING eldre enn TTL — aldri ferske, aldri
    aktive. Målt gjennom CLI-ens EGEN kommando (samme vei som timeren),
    ikke en reimplementert spørring."""
    from db.pg import koble
    cli = _cli()
    from .test_api_porter import TOKEN_ADMIN_DSN
    admin = koble(TOKEN_ADMIN_DSN)
    try:
        gammel, _ = cli.opprett(admin, PEPPER, TENANT, "agent",
                                ["decision:write"])
        fersk, _ = cli.opprett(admin, PEPPER, TENANT, "agent",
                               ["decision:write"])
        admin.commit()
    finally:
        admin.close()
    # Alder fabrikkeres som skjemaeier — `opprettet` er ikke skrivbar for
    # token-admin, og det er poenget.
    migrator.execute("UPDATE api_tokener SET opprettet ="
                     " opprettet - interval '45 minutes'"
                     " WHERE token_id=%s", (gammel,))
    migrator.commit()

    import os
    monkeypatch.setenv("DISPONIT_TOKEN_ADMIN_URL",
                       os.environ["DISPONIT_TEST_TOKEN_ADMIN_DSN"])
    monkeypatch.setenv("DISPONIT_TOKEN_PEPPER", PEPPER)
    assert cli.main(["rydd-pending", "--ttl-minutter", "30"]) == 0
    assert "ryddet: 1" in capsys.readouterr().out

    statuser = dict(migrator.execute(
        "SELECT token_id, status FROM api_tokener WHERE token_id IN (%s,%s)",
        (gammel, fersk)).fetchall())
    migrator.rollback()
    assert statuser == {gammel: "TILBAKEKALT", fersk: "PENDING"}


def test_backupen_dumper_som_postgres_ikke_migrator():
    """En backup som hopper over en tabell er ikke en backup.

    backup-db.sh dumpet som migrator under antagelsen «eier skjemaet, ser
    alt». Den sluttet å holde da kapabilitetstabellene fikk egen eier uten
    grants: pg_dump døde på LOCK TABLE, og basen sto uten backup — funnet
    først da eier la inn mottakernøkkelen og den første ekte kjøringen
    feilet. Alternativet, SELECT til migrator, er nøyaktig mutasjonen
    `test_migrator_naar_ikke_kapabilitetene_uten_set_role` forbyr. Derfor
    postgres: uniten kjører som root, og superbrukeren ser alt uten at
    rettighetsmodellen røres. Målt på kilden, som de andre skript-portene.
    """
    skript = (ROT / "deploy/staging/backup-db.sh").read_text(encoding="utf-8")
    assert "pg_dump" in skript
    assert "MIGRATOR_URL" not in skript.split("pg_dump", 1)[1].split("\n")[0] \
        and skript.count("sudo -u postgres pg_dump") == 2, \
        "backupen dumper ikke som postgres — kapabilitetstabellene blir utelatt"


def test_backupen_far_backupnavnet_forst_etter_verifiseringen():
    """En avbrutt dump skal ikke kunne ligne dagens backup.

    Codex P1 (#178, runde 6): opp.sh steg 5 stopper
    `disponit-backup.service` for å holde `pg_dump` unna et skjema i
    bevegelse. Stoppen treffer hele cgruppen, også en dump som alt er i
    gang — og skrev backupen direkte til `disponit-<stempel>.dump.age`,
    ville SIGTERM etterlatt en AVKORTET fil med det ENDELIGE navnet.
    Den ville vært katalogens nyeste treff, retention ville talt den som
    en backup, og operatøren ville sett dagen dekket nettopp den dagen
    deployen gikk galt.

    Porten måler REKKEFØLGEN på kilden, som de andre skript-portene her:
    dumpen skrives til arbeidsnavnet, og `mv` til backupnavnet skjer
    etter BEGGE portene (gjenopprettingen og størrelsen). Et trap rydder
    arbeidsfila, og det er installert FØR dumpen starter — er det først
    etter, er nettopp avbruddsvinduet udekket.
    """
    skript = (ROT / "deploy/staging/backup-db.sh").read_text(encoding="utf-8")
    linjer = [ln.strip() for ln in skript.splitlines()
              if ln.strip() and not ln.lstrip().startswith("#")]

    def indeks(bit):
        for i, ln in enumerate(linjer):
            if bit in ln:
                return i
        return -1

    i_trap = indeks("trap opprydd EXIT")
    i_dump = indeks('age -R "$MOTTAKER"')
    i_tabeller = indeks("gjenoppretting ga bare")
    i_storrelse = indeks("backupfilen er tom")
    i_mv = indeks('mv "$DELVIS" "$FIL"')

    assert i_dump > 0 and '> "$DELVIS"' in linjer[i_dump], \
        "dumpen skrives rett til backupnavnet — et avbrudd etterlater en " \
        "avkortet fil som ser ut som dagens backup"
    assert 0 <= i_trap < i_dump, \
        "opprydding av arbeidsfila er ikke på plass FØR dumpen starter"
    assert i_mv > i_tabeller > 0 and i_mv > i_storrelse > 0, \
        "backupnavnet settes før verifiseringsportene har svart"
    assert 'rm -f "$DELVIS"' in skript, \
        "arbeidsfila ryddes ikke ved avbrudd"
