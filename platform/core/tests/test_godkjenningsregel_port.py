"""Porten for 227 (ADR-010): fire øyne er firmaets valg.

  1. Regelens overganger: første valg alene; strengere alene; SVAKERE
     krever to personer (forslag + bekreftelse av en annen, innen sju døgn).
  2. Med regelen AV kan forfatteren attestere og aktivere alene — også en
     endring som utvider fullmaktene.
  3. Med regelen PÅ, og for en tenant uten firmarad, er alt som før.
  4. En usikker lesing gjør aldri kontrollen svakere.

MUTASJONER SOM DREPER DENNE: la `firma_sett_godkjenningsregel` sette `false`
uten bekreftelse (1), fjern `v_krever` fra `hendelse_kvorum_gate` (2 blir
rød fordi gaten nekter), la `_krever_uavhengig` svare False ved feil (4).
"""
import secrets

import pytest

from api import policyadmin

from . import test_pr013_policyadmin_flyt as flyt
from .test_api import DSN, MIGRATOR_DSN  # noqa: F401
from .test_m37 import _sett_kontekst

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _firma(tenant):
    from db.pg import koble
    m = koble(MIGRATOR_DSN)
    try:
        _sett_kontekst(m, tenant)
        m.execute("SELECT firma_registrer(%s,'Regelfirma AS',NULL,30,'kari')",
                  (tenant,))
        m.commit()
    finally:
        m.close()


def _sett(c, tenant, valg, aktor):
    _sett_kontekst(c, tenant)
    ut = c.execute("SELECT firma_sett_godkjenningsregel(%s,%s,%s)",
                   (tenant, valg, aktor)).fetchone()[0]
    c.commit()
    return ut


def _regel(c, tenant):
    _sett_kontekst(c, tenant)
    r = c.execute("SELECT uavhengig, valgt, forslag_av FROM"
                  " firma_godkjenningsregel(%s)", (tenant,)).fetchone()
    c.rollback()
    return r


@pg
def test_svakere_regel_krever_to_personer():
    from db.pg import koble
    tenant = "t-227-" + secrets.token_hex(4)
    _firma(tenant)
    c = koble(DSN)
    try:
        # Nyregistrert: streng, og ikke valgt ennå.
        assert _regel(c, tenant) == (True, False, None)
        # FØRSTE valg: registranten alene, også det svake.
        assert _sett(c, tenant, False, "kari") == "satt"
        assert _regel(c, tenant) == (False, True, None)
        assert _sett(c, tenant, False, "kari") == "uendret"
        # STRENGERE: én person alene.
        assert _sett(c, tenant, True, "kari") == "satt"
        assert _regel(c, tenant)[0] is True
        # SVAKERE: forslaget alene endrer ingenting …
        assert _sett(c, tenant, False, "kari") == "foreslatt"
        assert _regel(c, tenant) == (True, True, "kari")
        # … og forslagsstilleren kan ikke bekrefte seg selv.
        assert _sett(c, tenant, False, "kari") == "venter"
        assert _regel(c, tenant)[0] is True
        # En ANNEN bekrefter: nå gjelder det.
        assert _sett(c, tenant, False, "ola") == "satt"
        assert _regel(c, tenant) == (False, True, None)

        # Et forslag kan trekkes (eller avslås) ved å be om det som gjelder.
        assert _sett(c, tenant, True, "ola") == "satt"
        assert _sett(c, tenant, False, "ola") == "foreslatt"
        assert _sett(c, tenant, True, "kari") == "uendret"
        assert _regel(c, tenant) == (True, True, None)
        assert _sett(c, tenant, False, "kari") == "foreslatt"
    finally:
        c.close()
    # Et forslag eldre enn sju døgn bekrefter ingenting: det blir et nytt.
    from db.pg import koble as _k
    m = _k(MIGRATOR_DSN)
    try:
        _sett_kontekst(m, tenant)
        m.execute("UPDATE firma SET godkjenning_forslag_ts = now()"
                  " - interval '8 days' WHERE tenant=%s", (tenant,))
        m.commit()
    finally:
        m.close()
    c = koble(DSN)
    try:
        assert _regel(c, tenant) == (True, True, None)
        assert _sett(c, tenant, False, "ola") == "foreslatt"
        assert _regel(c, tenant) == (True, True, "ola")
    finally:
        c.close()


@pg
def test_uten_firmarad_er_regelen_streng_og_last():
    from db.pg import koble
    tenant = "t-227-ingen-" + secrets.token_hex(3)
    c = koble(DSN)
    try:
        assert _regel(c, tenant) == (True, True, None)
        assert policyadmin._krever_uavhengig(c, tenant) is True
        c.rollback()
        import psycopg
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            c.execute("SELECT firma_sett_godkjenningsregel(%s,false,'kari')",
                      (tenant,))
        c.rollback()
    finally:
        c.close()


@pg
def test_med_regelen_av_aktiverer_forfatteren_alene(monkeypatch):
    """Det eier ba om 21/11: et firma der én person bestemmer, skal ikke
    stoppes av en kollega det ikke har. Endringen UTVIDER fullmaktene, og
    går likevel gjennom med forfatterens egen attestasjon — fordi firmaet
    har valgt det."""
    tenant = "t-227-en-" + secrets.token_hex(3)
    monkeypatch.setattr(flyt, "TEN", tenant)
    _firma(tenant)
    from db.pg import koble
    c = koble(DSN)
    try:
        assert _sett(c, tenant, False, "kari") == "satt"
    finally:
        c.close()
    pid = "pol-" + secrets.token_hex(3)
    a = flyt._medlem("forf", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    flyt._utkast(uid, pid, a, {"roller": [{"id": "r1"}],
                               "handlinger": [{"id": "h1"}]})
    rt = flyt._rt()
    try:
        r = flyt._apne(rt, uid, a)
        assert r["risikoklasse"] == "UTVIDER"
        assert r["pakrevd_antall_godkjennere"] == 1
        r1 = flyt._attester(rt, uid, a, r["diff_hash"])
        assert r1["utfall"] == "aktivert", r1
    finally:
        rt.close()
    assert flyt._aktiv_versjon(pid) == "1.1.0"
    # EVIDENSEN SIER DET SOM DET ER: én attestant, og det er forfatteren.
    m = flyt._mig()
    try:
        rad = m.execute(
            "SELECT attestant_a, attestant_b, attestant_er_forfatter,"
            " eneattestert, pakrevd_antall FROM policyaktivering"
            " WHERE tenant=%s AND utkast_id=%s", (tenant, uid)).fetchone()
    finally:
        m.rollback()
        m.close()
    assert rad == (a, None, True, True, 1), rad


@pg
def test_med_regelen_paa_venter_forfatteren_fortsatt(monkeypatch):
    tenant = "t-227-to-" + secrets.token_hex(3)
    monkeypatch.setattr(flyt, "TEN", tenant)
    _firma(tenant)
    from db.pg import koble
    c = koble(DSN)
    try:
        assert _sett(c, tenant, True, "kari") == "satt"
    finally:
        c.close()
    pid = "pol-" + secrets.token_hex(3)
    a = flyt._medlem("forf", ["policyforvalter"])
    b = flyt._medlem("uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    flyt._utkast(uid, pid, a, {"roller": [{"id": "r1"}],
                               "handlinger": [{"id": "h1"}]})
    rt = flyt._rt()
    try:
        r = flyt._apne(rt, uid, a)
        assert r["pakrevd_antall_godkjennere"] == 2
        r1 = flyt._attester(rt, uid, a, r["diff_hash"])
        assert r1["utfall"] == "venter_godkjennere"
        assert r1["mangler_uavhengig"] is True
        assert flyt._aktiv_versjon(pid) is None
        r2 = flyt._attester(rt, uid, b, r["diff_hash"])
        assert r2["utfall"] == "aktivert"
    finally:
        rt.close()
    m = flyt._mig()
    try:
        rad = m.execute(
            "SELECT attestant_a, attestant_er_forfatter, eneattestert"
            " FROM policyaktivering WHERE tenant=%s AND utkast_id=%s",
            (tenant, uid)).fetchone()
        # Tabellen selv nekter en hendelse som later som noe annet.
        import psycopg
        for sql in (
                "UPDATE policyaktivering SET eneattestert=true"
                " WHERE tenant=%s AND utkast_id=%s",):
            with pytest.raises(psycopg.Error):
                m.execute(sql, (tenant, uid))
            m.rollback()
    finally:
        m.rollback()
        m.close()
    assert rad == (b, False, False), rad


def test_en_usikker_lesing_gjor_aldri_kontrollen_svakere():
    import psycopg

    class Savepoint:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Feiler:
        def transaction(self):
            return Savepoint()

        def execute(self, *a, **k):
            raise psycopg.OperationalError("basen svarte ikke")

    class Tom(Feiler):
        def execute(self, *a, **k):
            class R:
                def fetchone(self_):
                    return None
            return R()

    assert policyadmin._krever_uavhengig(Feiler(), "t") is True
    assert policyadmin._krever_uavhengig(Tom(), "t") is True
    # Terskelregningen: uten krav telles kravet som oppfylt, med krav ikke.
    assert policyadmin._tellende_uavhengige(False, 0) == 1
    assert policyadmin._tellende_uavhengige(True, 0) == 0
    assert policyadmin._tellende_uavhengige(True, 2) == 2


def test_aktiveringsdora_er_048s_kropp_med_en_endret_blokk():
    """227 erstatter hele `aktiver_policy`. En erstatning som ikke er målt
    mot forgjengeren kan ha mistet en kontroll uten at noen så det: her
    holdes de to kroppene mot hverandre, linje for linje."""
    import difflib
    from pathlib import Path

    rot = Path(__file__).resolve().parents[1] / "db" / "migrations"
    start = "CREATE OR REPLACE FUNCTION public.aktiver_policy("

    def kropp(fil):
        s = (rot / fil).read_text(encoding="utf-8")
        i = s.index(start)
        return s[i:s.index("$function$;", i)].splitlines()

    gml = kropp("048_claim_tillitsgrense.sql")
    ny = kropp("227_valgfri_uavhengig_godkjenning.sql")
    assert len(gml) > 100, "fant ikke 048s kropp"
    diff = list(difflib.ndiff(gml, ny))
    fjernet = [l[2:] for l in diff if l.startswith("- ")]
    lagt_til = [l[2:] for l in diff if l.startswith("+ ")]
    # TO linjer ut: det ubetingede kravet, og INSERT-ens kolonneliste.
    assert sorted(l.strip() for l in fjernet) == sorted([
        "IF v_uavhengige < 1 THEN",
        "aktiveringskilde, pakrevd_antall)",
        "'styrt', v_pakrevd);"]), fjernet
    tekst = "\n".join(lagt_til)
    assert "IF v_uavhengige < 1 AND coalesce(" in tekst
    assert "f.uavhengig_godkjenning FROM public.firma f" in tekst
    assert "true) THEN" in tekst, "uten svar må regelen være streng"
    # Forfatteren velges BARE når ingen uavhengig finnes, og hendelsen
    # merkes — begge flaggene settes av samme variabel.
    assert "IF v_att_a IS NULL THEN" in tekst
    assert "AND a.runde = p_runde AND a.er_forfatter" in tekst
    assert "'styrt', v_pakrevd, v_ene, v_ene);" in tekst
    assert len(lagt_til) <= 24, lagt_til


@pg
def test_etterfyllingen_treffer_firmaer_som_finnes():
    """227 merket INGEN eksisterende firma som «har valgt»: migratoren ser
    ikke radene i `firma` (FORCE RLS), og testbasen var tom da den kjørte.
    Funnet i drift 21.11.2026. Her kjøres 228s setninger mot et firma som
    FINNES, og etterpå kan én person ikke lenger svekke regelen alene.

    MUTASJONEN SOM DREPER DENNE: fjern `SET LOCAL ROLE` fra 228.
    """
    from pathlib import Path

    from db.pg import koble
    tenant = "t-228-" + secrets.token_hex(4)
    _firma(tenant)
    c = koble(DSN)
    try:
        assert _regel(c, tenant) == (True, False, None)
    finally:
        c.close()
    fil = (Path(__file__).resolve().parents[1] / "db" / "migrations"
           / "228_godkjenningsvalg_etterfylles.sql")
    sql = fil.read_text(encoding="utf-8")
    m = koble(MIGRATOR_DSN)
    try:
        m.execute(sql)
        m.commit()
    finally:
        m.close()
    c = koble(DSN)
    try:
        assert _regel(c, tenant) == (True, True, None)
        # Første valg er BRUKT: å gå til én person er nå et forslag.
        assert _sett(c, tenant, False, "kari") == "foreslatt"
        assert _regel(c, tenant)[0] is True
    finally:
        c.close()
