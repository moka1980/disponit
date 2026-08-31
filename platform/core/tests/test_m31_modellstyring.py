"""M-31 v1 (087): golden-sett-porten på release-byttene — planens 15
porter. Testene konstruerer sin EGEN tilstand (kjør-unike modul-id-er,
014a-formen) og måler dørene og porten der de bor: i basen.

Port 14 (axe + ingen hardkodet tekst) har sin dynamiske halvdel i
`platform/core/ui/test/modellstyring.test.js` (jsdom + axe); her står
den statiske halvdelen — hver `t("…")`-nøkkel i flaten finnes i BEGGE
locale-settene, og ingen innerHTML-vei finnes.
"""
import importlib.util
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

ROT = Path(__file__).resolve().parents[1]
GITROT = Path(__file__).resolve().parents[3]
MIGRASJON = ROT / "db/migrations/087_m31_modellstyring.sql"
HASH64_A = "a" * 64
HASH64_B = "b" * 64


def _c():
    from db.pg import koble
    return koble(MIGRATOR_DSN)


def _rt():
    from db.pg import koble
    return koble(DSN)


def _admin():
    from db.pg import koble
    c = koble(MIGRATOR_DSN)
    c.execute("SET ROLE disponit_modules_admin")
    c.commit()
    return c


def _mid():
    return "m31t-" + secrets.token_hex(4)


def _kjede(a, m, kh, *releaser):
    """installer modul + kontrakt + releaser (én digest per release)."""
    a.execute("SELECT installer_modul(%s,'sys')", (m,))
    a.execute("SELECT registrer_kontrakt(%s,1,%s,'p','k','krever_outbox',"
              "'kompenserende','sys')", (m, kh))
    for rel, digest in releaser:
        a.execute("SELECT registrer_release(%s,%s,1,%s,'mh',%s,'sys')",
                  (m, rel, kh, digest))


def _sett(a, m, sett_id="s1", versjon=1, hasj=HASH64_A, antall=3):
    a.execute("SELECT registrer_golden_sett(%s,%s,%s,%s,%s,'demo','sys')",
              (m, sett_id, versjon, hasj, antall))


def _krav(a, m, sett_id="s1", versjon=1, hasj=HASH64_A, andel=0.9,
          p95=None, modellfeil=0):
    # `::numeric`: psycopg sender python-float som float8, og funksjons-
    # oppløsningen godtar ikke assignment-casten float8->numeric.
    a.execute("SELECT sett_evalueringskrav(%s,%s,%s,%s,%s::numeric,%s,%s,'sys')",
              (m, sett_id, versjon, hasj, andel, p95, modellfeil))


def _kjoring(a, m, digest, sett_id="s1", versjon=1, hasj=HASH64_A,
             antall=3, bestatt=3, modellfeil=0, p95=20,
             kjoring_id=None):
    kjoring_id = kjoring_id or uuid.uuid4()
    t1 = datetime.now(timezone.utc) - timedelta(minutes=1)
    t2 = datetime.now(timezone.utc)
    return a.execute(
        "SELECT registrer_evalueringskjoring(%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "10,%s,1.5,'modellX',%s,%s,%s,'sys')",
        (m, kjoring_id, digest, sett_id, versjon, hasj, antall, bestatt,
         modellfeil, p95, HASH64_B, t1, t2)).fetchone()[0]


def _bytt(a, m, rel, kh, miljo="staging"):
    a.execute("SELECT bytt_release(%s,%s,%s,1,%s,'sys')", (m, miljo, rel, kh))


# ---------------------------------------------------------------------------
# Port 1: modul uten krav → bytt_release uendret (regresjonen).
# ---------------------------------------------------------------------------

@pg
def test_port1_modul_uten_krav_bytter_som_for():
    m = _mid(); kh = "k-" + secrets.token_hex(8)
    a = _admin()
    try:
        _kjede(a, m, kh, ("r1", "d1"), ("r2", "d2")); a.commit()
        _bytt(a, m, "r1", kh); a.commit()
        _bytt(a, m, "r2", kh); a.commit()             # bytte nr. 2 også
    finally:
        a.close()
    r = _rt()
    try:
        n = r.execute("SELECT count(*) FROM moduldeployment WHERE"
                      " modul_id=%s AND livslop='claiming'", (m,)
                      ).fetchone()[0]
        assert n == 1
    finally:
        r.close()


# ---------------------------------------------------------------------------
# Port 2: gjeldende krav + kandidat-digest uten bestått kjøring → avvist.
# ---------------------------------------------------------------------------

@pg
def test_port2_krav_uten_bestatt_kjoring_avviser_bytte():
    m = _mid(); kh = "k-" + secrets.token_hex(8)
    a = _admin()
    try:
        _kjede(a, m, kh, ("r1", "d1")); _sett(a, m); _krav(a, m); a.commit()
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _bytt(a, m, "r1", kh)
        assert "m31-porten" in str(e.value)
        a.rollback()
        # …og med en BESTÅTT kjøring for digesten slipper byttet gjennom.
        assert _kjoring(a, m, "d1") is True
        _bytt(a, m, "r1", kh); a.commit()
    finally:
        a.close()


# ---------------------------------------------------------------------------
# Port 3: kjøring mot annet sett(-hash) enn gjeldende kravs bærer ikke.
# ---------------------------------------------------------------------------

@pg
def test_port3_kjoring_mot_annet_sett_baerer_ikke():
    m = _mid(); kh = "k-" + secrets.token_hex(8)
    c_hash = "c" * 64
    a = _admin()
    try:
        _kjede(a, m, kh, ("r1", "d1"))
        _sett(a, m, "s1", 1, HASH64_A)
        _sett(a, m, "s2", 1, c_hash)
        _krav(a, m, "s1", 1, HASH64_A); a.commit()
        # Døren avviser en kjøring mot et annet sett enn gjeldende kravs —
        # den ville ikke målt kravet.
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _kjoring(a, m, "d1", sett_id="s2", hasj=c_hash)
        a.rollback()
        # …og byttet er fortsatt stengt: ingen bestått kjøring bærer det.
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _bytt(a, m, "r1", kh)
        a.rollback()
    finally:
        a.close()


# ---------------------------------------------------------------------------
# Port 4: bestått mot HISTORISK kravversjon bærer ikke (dom 3: eksakt).
# ---------------------------------------------------------------------------

@pg
def test_port4_bestatt_mot_historisk_krav_baerer_ikke():
    m = _mid(); kh = "k-" + secrets.token_hex(8)
    a = _admin()
    try:
        _kjede(a, m, kh, ("r1", "d1")); _sett(a, m); _krav(a, m, andel=0.5)
        a.commit()
        assert _kjoring(a, m, "d1", bestatt=2) is True    # bestått mot v1
        a.commit()
        _krav(a, m, andel=0.9); a.commit()                # innstramming → v2
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _bytt(a, m, "r1", kh)
        assert "kravversjon 2" in str(e.value)
        a.rollback()
        # Én re-kjøring mot det nye kravet gjenåpner byttet (planens pris).
        assert _kjoring(a, m, "d1", bestatt=3) is True
        _bytt(a, m, "r1", kh); a.commit()
    finally:
        a.close()


# ---------------------------------------------------------------------------
# Port 5: nøddeaktivering og låserekkefølgen er uendret.
# ---------------------------------------------------------------------------

@pg
def test_port5_nodeaktivert_avvises_for_porten():
    m = _mid(); kh = "k-" + secrets.token_hex(8)
    a = _admin()
    try:
        _kjede(a, m, kh, ("r1", "d1")); _sett(a, m); _krav(a, m)
        assert _kjoring(a, m, "d1") is True               # porten er innfridd
        a.execute("SELECT noddeaktiver_modul(%s,'hendelse','sys')", (m,))
        a.commit()
        # Nødstoppet vinner over en innfridd port — som før 087.
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _bytt(a, m, "r1", kh)
        assert "nodeaktivert" in str(e.value)
        a.rollback()
    finally:
        a.close()


def test_port5_laaserekkefolgen_er_kopiert_ikke_endret():
    """Statisk: 087-kroppen tar modul-låsen FØRST, så kontraktlåsen —
    nøyaktig 014-rekkefølgen (Codex P1-serialiseringen mot nødstopp)."""
    kilde = MIGRASJON.read_text(encoding="utf-8")
    kropp = kilde.split("CREATE OR REPLACE FUNCTION bytt_release(")[1]
    laaser = re.findall(r"pg_advisory_xact_lock\(hashtextextended\(\s*'"
                        r"([^']+)'", kropp)
    assert laaser[:2] == ["modul:", "modulregister:bytt:"], laaser


# ---------------------------------------------------------------------------
# Port 6: `bestatt` beregnes AV DØREN — signaturen mangler parameteren.
# ---------------------------------------------------------------------------

def test_port6_doren_har_ingen_bestatt_parameter():
    kilde = MIGRASJON.read_text(encoding="utf-8")
    start = kilde.index("CREATE OR REPLACE FUNCTION"
                        " registrer_evalueringskjoring(")
    signatur = kilde[start:kilde.index("RETURNS BOOLEAN", start)]
    assert not re.search(r"p_bestatt\b", signatur), \
        "dørens signatur har fått en bestatt-parameter — kallerens" \
        " påstand skal ikke ha noen vei inn"


# ---------------------------------------------------------------------------
# Port 7: delvis kjøring er dør-avvist.
# ---------------------------------------------------------------------------

@pg
def test_port7_delvis_kjoring_uregistrerbar():
    m = _mid(); kh = "k-" + secrets.token_hex(8)
    a = _admin()
    try:
        _kjede(a, m, kh, ("r1", "d1")); _sett(a, m, antall=3); a.commit()
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            _kjoring(a, m, "d1", antall=2, bestatt=2)
        assert "delvis" in str(e.value)
        a.rollback()
    finally:
        a.close()


# ---------------------------------------------------------------------------
# Port 8: sett-immutabilitet — no-op på identisk, konflikt på avvik.
# ---------------------------------------------------------------------------

@pg
def test_port8_sett_immutabilitet():
    m = _mid(); kh = "k-" + secrets.token_hex(8)
    a = _admin()
    try:
        _kjede(a, m, kh, ("r1", "d1")); _sett(a, m); a.commit()
        _sett(a, m); a.commit()                           # identisk → no-op
        with pytest.raises(psycopg.errors.UniqueViolation):
            _sett(a, m, hasj="d" * 64)                    # avvik → konflikt
        a.rollback()
    finally:
        a.close()
    # …og direkte UPDATE/DELETE stoppes av triggeren (migrator eier bordet).
    c = _c()
    try:
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE golden_sett SET beskrivelse='x' WHERE"
                      " modul_id=%s", (m,))
        c.rollback()
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("DELETE FROM golden_sett WHERE modul_id=%s", (m,))
        c.rollback()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Port 9: to gjeldende krav er umulig; flippet skjer i samme transaksjon.
# ---------------------------------------------------------------------------

@pg
def test_port9_ett_gjeldende_krav_og_flipp_i_samme_tx():
    m = _mid(); kh = "k-" + secrets.token_hex(8)
    a = _admin()
    try:
        _kjede(a, m, kh, ("r1", "d1")); _sett(a, m)
        _krav(a, m, andel=0.5); a.commit()
        _krav(a, m, andel=0.9); a.commit()                # v2, v1 → historisk
    finally:
        a.close()
    r = _rt()
    try:
        rader = r.execute(
            "SELECT kravversjon, status FROM evalueringskrav WHERE"
            " modul_id=%s ORDER BY kravversjon", (m,)).fetchall()
        assert rader == [(1, "historisk"), (2, "gjeldende")]
    finally:
        r.close()
    # Den partielle indeksen gjør to gjeldende umulig også UTENOM døren.
    c = _c()
    try:
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(
                "INSERT INTO evalueringskrav (modul_id,kravversjon,sett_id,"
                "sett_versjon,sett_hash,terskel_min_andel,status) VALUES"
                " (%s,3,'s1',1,%s,0.5,'gjeldende')", (m, HASH64_A))
        c.rollback()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Port 10: kjøring mot ukjent sett er FK-/dør-avvist.
# ---------------------------------------------------------------------------

@pg
def test_port10_kjoring_mot_ukjent_sett_avvist():
    m = _mid(); kh = "k-" + secrets.token_hex(8)
    a = _admin()
    try:
        _kjede(a, m, kh, ("r1", "d1")); a.commit()        # intet sett
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _kjoring(a, m, "d1")
        a.rollback()
        # …og riktig sett men feil hash er like avvist (hash-avviket
        # aksepteres aldri — KRAVGRENSER sett.hash_avvik_akseptert=0).
        _sett(a, m); a.commit()
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _kjoring(a, m, "d1", hasj="e" * 64)
        a.rollback()
    finally:
        a.close()


# ---------------------------------------------------------------------------
# Port 11: idempotent kjøringsregistrering; avvik på samme id → konflikt.
# ---------------------------------------------------------------------------

@pg
def test_port11_kjoringsregistrering_idempotent_og_immutabel():
    m = _mid(); kh = "k-" + secrets.token_hex(8)
    kid = uuid.uuid4()
    a = _admin()
    try:
        _kjede(a, m, kh, ("r1", "d1")); _sett(a, m); _krav(a, m); a.commit()
        forste = _kjoring(a, m, "d1", kjoring_id=kid); a.commit()
        # NB: identisk gjenspilling krever identiske tidsstempler — hent
        # radens egne og spill dem tilbake.
        r = _rt()
        try:
            (t1, t2) = r.execute(
                "SELECT startet_ts, avsluttet_ts FROM evalueringskjoring"
                " WHERE modul_id=%s AND kjoring_id=%s", (m, kid)
                ).fetchone()
        finally:
            r.close()
        andre = a.execute(
            "SELECT registrer_evalueringskjoring(%s,%s,'d1','s1',1,%s,3,3,"
            "0,10,20,1.5,'modellX',%s,%s,%s,'sys')",
            (m, kid, HASH64_A, HASH64_B, t1, t2)).fetchone()[0]
        a.commit()
        assert (forste, andre) == (True, True)
        # avvikende telling på samme kjoring_id → immutabilitetskonflikt.
        with pytest.raises(psycopg.errors.UniqueViolation):
            a.execute(
                "SELECT registrer_evalueringskjoring(%s,%s,'d1','s1',1,%s,"
                "3,2,0,10,20,1.5,'modellX',%s,%s,%s,'sys')",
                (m, kid, HASH64_A, HASH64_B, t1, t2))
        a.rollback()
    finally:
        a.close()
    r = _rt()
    try:
        n = r.execute("SELECT count(*) FROM evalueringskjoring WHERE"
                      " modul_id=%s", (m,)).fetchone()[0]
        assert n == 1
    finally:
        r.close()


# ---------------------------------------------------------------------------
# Port 12 + 13: CLI-en — hash-avvik stopper FØR modellkall; avbrutt
# kjøring registrerer ingenting. Modellklienten er INJISERT (m57-formen)
# — ingen ekte Ollama i test.
# ---------------------------------------------------------------------------

def _cli():
    sti = GITROT / "deploy/staging/kjor-m31-evaluering.py"
    spec = importlib.util.spec_from_file_location("kjor_m31", sti)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _settfil(tmp_path, antall=3):
    eksempler = [{
        "id": f"eks-{i}",
        "tekst": f"Syntetisk blindet søknadstekst nummer {i} for [NAVN-1].",
        "vekter": {"krav_a": 2, "krav_b": 1},
        "forventet_oppfylt": {"krav_a": True, "krav_b": bool(i % 2)},
        "forventede_funn_kategorier": ["uklar_tidslinje"] if i == 0 else [],
    } for i in range(antall)]
    sti = tmp_path / "golden-sett.json"
    sti.write_text(json.dumps(eksempler, ensure_ascii=False),
                   encoding="utf-8")
    return sti, eksempler


class _Fasitklient:
    """Svarer nøyaktig fasiten — og TELLER kallene sine."""

    def __init__(self, eksempler, *, feil_ved=None):
        self.fasit = {e["tekst"]: e for e in eksempler}
        self.kall = 0
        self.feil_ved = feil_ved
        self.modellnavn = "fasit"

    def vurder(self, tekst, vekter):
        self.kall += 1
        if self.feil_ved is not None and self.kall >= self.feil_ved:
            raise RuntimeError("avbrutt midt i kjøringen")
        eks = self.fasit[tekst]
        return {"oppfylt": dict(eks["forventet_oppfylt"]),
                "funn": [{"kategori": k, "kilde": {}}
                         for k in eks["forventede_funn_kategorier"]]}


@pg
def test_port12_hash_avvik_stopper_for_modellkall(tmp_path, monkeypatch):
    monkeypatch.setenv("DISPONIT_MIGRATOR_URL", MIGRATOR_DSN)
    sti, eksempler = _settfil(tmp_path)
    klient = _Fasitklient(eksempler)
    m = _mid()                        # modul uten registrert sett → avvik
    a = _admin()
    try:
        a.execute("SELECT installer_modul(%s,'sys')", (m,)); a.commit()
    finally:
        a.close()
    cli = _cli()
    with pytest.raises(SystemExit) as e:
        cli.main([m, "d1", str(sti)], modellfabrikk=lambda *a_: klient)
    assert "hash-avvik" in str(e.value)
    assert klient.kall == 0, "modellen ble kalt tross hash-avvik"
    r = _rt()
    try:
        n = r.execute("SELECT count(*) FROM evalueringskjoring WHERE"
                      " modul_id=%s", (m,)).fetchone()[0]
        assert n == 0
    finally:
        r.close()


@pg
def test_port13_avbrutt_kjoring_registrerer_ingenting(tmp_path, monkeypatch):
    monkeypatch.setenv("DISPONIT_MIGRATOR_URL", MIGRATOR_DSN)
    sti, eksempler = _settfil(tmp_path)
    m = _mid(); kh = "k-" + secrets.token_hex(8)
    a = _admin()
    try:
        _kjede(a, m, kh, ("r1", "d1"))
        from m31 import golden
        _, hasj = golden.les_sett(sti)
        _sett(a, m, hasj=hasj, antall=3); a.commit()
    finally:
        a.close()
    cli = _cli()
    klient = _Fasitklient(eksempler, feil_ved=2)
    with pytest.raises(RuntimeError):
        cli.main([m, "d1", str(sti)], modellfabrikk=lambda *a_: klient)
    r = _rt()
    try:
        n = r.execute("SELECT count(*) FROM evalueringskjoring WHERE"
                      " modul_id=%s", (m,)).fetchone()[0]
        assert n == 0, "en avbrutt kjøring la igjen en rad"
    finally:
        r.close()


@pg
def test_cli_hel_kjede_med_injisert_klient(tmp_path, monkeypatch):
    """Runbook-kjeden ende til ende, uten Ollama: målekjøring uten krav
    (exit 1, kravversjon NULL, bestatt=false — fail-closed), krav, ny
    kjøring (exit 0, bestått), bytt_release slipper gjennom."""
    monkeypatch.setenv("DISPONIT_MIGRATOR_URL", MIGRATOR_DSN)
    sti, eksempler = _settfil(tmp_path)
    m = _mid(); kh = "k-" + secrets.token_hex(8)
    from m31 import golden
    _, hasj = golden.les_sett(sti)
    a = _admin()
    try:
        _kjede(a, m, kh, ("r1", "d1"))
        _sett(a, m, hasj=hasj, antall=3); a.commit()
        cli = _cli()
        klient = _Fasitklient(eksempler)
        rc = cli.main([m, "d1", str(sti)],
                      modellfabrikk=lambda *a_: klient)
        assert rc == 1                    # målekjøring: intet krav → ikke bestått
        _krav(a, m, hasj=hasj, andel=0.9); a.commit()
        klient2 = _Fasitklient(eksempler)
        rc = cli.main([m, "d1", str(sti)],
                      modellfabrikk=lambda *a_: klient2)
        assert rc == 0
        _bytt(a, m, "r1", kh); a.commit()
    finally:
        a.close()
    r = _rt()
    try:
        rader = r.execute(
            "SELECT kravversjon, bestatt FROM evalueringskjoring WHERE"
            " modul_id=%s ORDER BY registrert", (m,)).fetchall()
        assert rader == [(None, False), (1, True)]
    finally:
        r.close()


# ---------------------------------------------------------------------------
# Port 14 (statisk halvdel): ingen hardkodet tekst, ingen innerHTML.
# Den dynamiske halvdelen (axe) bor i ui/test/modellstyring.test.js.
# ---------------------------------------------------------------------------

def test_port14_flaten_bruker_locale_og_aldri_innerhtml():
    flate = (ROT / "ui/static/js/flater/modellstyring.js"
             ).read_text(encoding="utf-8")
    assert "innerHTML" not in flate
    nokler = set(re.findall(r't\("([^"]+)"\)', flate))
    assert nokler, "flaten henter ingen locale-nøkler — alt er hardkodet?"
    for sprak in ("nb", "en"):
        locale = json.loads((GITROT / f"locales/{sprak}.json"
                             ).read_text(encoding="utf-8"))
        mangler = sorted(n for n in nokler if n not in locale)
        assert not mangler, f"{sprak}.json mangler {mangler}"


# ---------------------------------------------------------------------------
# Port 15: begge migrasjonskjøringer grønne — 087 er re-kjørbar.
# ---------------------------------------------------------------------------

@pg
def test_port15_087_er_rekjorbar():
    """Migrasjonen er alt kjørt av riggen (kjøring 1). Kjøring 2 måles
    her: hele filen på nytt i én transaksjon som rulles tilbake — hver
    DDL er IF NOT EXISTS/OR REPLACE, så en re-kjøring er grønn."""
    c = _c()
    try:
        c.execute(MIGRASJON.read_text(encoding="utf-8"))
        c.rollback()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Speilet: 087-kroppen er 014-kroppen pluss NØYAKTIG portblokken.
# ---------------------------------------------------------------------------

def test_bytt_release_kroppen_er_kopiert_byte_for_byte():
    """014-kroppen med de to diff-punktene (DECLARE-tillegget og
    portblokken) fjernet skal være BYTE-IDENTISK med 087-kroppen — en
    tredje, stille endring i REPLACEen skal ryke her."""
    def kropp(kilde):
        start = kilde.index("CREATE OR REPLACE FUNCTION bytt_release(")
        slutt = kilde.index("END $$;", start) + len("END $$;")
        return kilde[start:slutt]

    original = kropp((ROT / "db/migrations/014_modulregister.sql"
                      ).read_text(encoding="utf-8"))
    ny = kropp(MIGRASJON.read_text(encoding="utf-8"))
    ny = ny.replace("\n        v_m31_kravversjon INT; v_m31_digest TEXT;",
                    "", 1)
    start = ny.index("    -- ====")
    slutt = ny.index("    -- Gammel claiming")
    ny = ny[:start] + ny[slutt:]
    assert ny == original, \
        "086-REPLACEen avviker fra 014-kroppen utover portdiffen"
