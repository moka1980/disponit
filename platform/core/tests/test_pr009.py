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
    kan skje stille."""
    opp = (ROT / "deploy/staging/opp.sh").read_text(encoding="utf-8")
    gate = opp.index("preflight_units")
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

CRED_START = "install -d -m 700 /etc/disponit/api"
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
    # API-et alene. Dommen måles på den samme lista.
    maalt = blokk[blokk.index('NEDE=""'):blokk.index('if [ -z "$NEDE" ]')]
    assert "disponit-m37.service" in maalt, \
        "reverseringsdommen måler ikke M-37"
    assert "$SELVREVERS_ENHETER" in maalt, \
        "reverseringsdommen måler ikke enhetene den nettopp startet"


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
