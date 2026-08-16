"""Angre en feilopprettet policy — slett den som ALDRI er brukt (030).

Behovet er målt: `tjenestebedrift1` og `tjenestebedrift2` ble aktivert ved
feil, og eneste vei ut var håndskrevet SQL som postgres, to ganger på én dag.

Grensene er hele verdien av funksjonen:
  * en policy som har styrt én beslutning kan ALDRI slettes — loggen
    refererer den, og et spor som peker på ingenting er ikke et spor;
  * en åpen runde blokkerer, som for forkast: attestasjoner i omløp;
  * ankerraden består (append-only), bare pekeren nullstilles;
  * utkast og runder røres ikke — at mennesker attesterte er et faktum om
    fortiden;
  * versjonene blir ledige igjen, så riktig opprettelse etterpå ikke stoppes
    av 020-monotonien.
"""
import json
import secrets

import pytest

from api import policyregister as pr

from .test_api import DSN, MIGRATOR_DSN

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
TEN = "t-slett-" + secrets.token_hex(3)


def _mig(tenant=TEN):
    from db.pg import koble, sett_kontekst
    c = koble(MIGRATOR_DSN)
    sett_kontekst(c, tenant, "test", "r0")
    return c


def _rt(tenant=TEN):
    from db.pg import koble, sett_kontekst
    c = koble(DSN)
    sett_kontekst(c, tenant, "test", "r1")
    return c


def _policyrad(c, pid, versjon="1.0.0", aktiv=True):
    innhold = {"meta": {"policy_id": pid, "versjon": versjon}}
    c.execute(
        "INSERT INTO policyer (tenant,policy_id,versjon,innholds_hash,status,"
        "innhold,aktiv) VALUES (%s,%s,%s,%s,'produksjon',%s::jsonb,%s)",
        (TEN, pid, versjon, "h-" + secrets.token_hex(4),
         json.dumps(innhold), aktiv))
    c.execute(
        "INSERT INTO policy_hode (tenant,policy_id,aktiv_versjon)"
        " VALUES (%s,%s,%s) ON CONFLICT (tenant,policy_id)"
        " DO UPDATE SET aktiv_versjon=EXCLUDED.aktiv_versjon",
        (TEN, pid, versjon if aktiv else None))


def _slett(c, pid, tenant=TEN):
    return c.execute("SELECT slett_ubrukt_policy(%s,%s)",
                     (tenant, pid)).fetchone()[0]


@pg
def test_ubrukt_policy_slettes_og_versjonen_blir_ledig():
    pid = "p-" + secrets.token_hex(3)
    m = _mig()
    _policyrad(m, pid)
    m.commit()
    rt = _rt()
    try:
        assert _slett(rt, pid) == 1
        rt.commit()
        from db.pg import sett_kontekst
        sett_kontekst(rt, TEN, "test", "r2")
        rad = rt.execute("SELECT count(*) FROM policyer WHERE tenant=%s"
                         " AND policy_id=%s", (TEN, pid)).fetchone()
        assert rad[0] == 0, "policyraden ble stående"
        # Ankerraden BESTÅR, med nullstilt peker: historikken er append-only.
        hode = rt.execute(
            "SELECT aktiv_versjon, revisjon FROM policy_hode"
            " WHERE tenant=%s AND policy_id=%s", (TEN, pid)).fetchone()
        assert hode is not None and hode[0] is None, hode
        assert hode[1] >= 1, "revisjonen skal telle også denne hendelsen"
    finally:
        rt.close()
        m.close()


@pg
def test_policy_som_har_styrt_en_beslutning_kan_aldri_slettes():
    """Kontroll: fjern revisjonslogg-sjekken i 030, så blir denne rød —
    og loggen ville pekt på en policy som ikke finnes."""
    import psycopg
    pid = "p-" + secrets.token_hex(3)
    m = _mig()
    _policyrad(m, pid)
    m.execute(
        "INSERT INTO revisjonslogg (tenant, ts, policy_id, beslutning,"
        " begrunnelse, input_hash) VALUES (%s, now(), %s, 'TILLAT',"
        " '{}', 'ih-' || %s)",
        (TEN, f"{pid}@1.0.0/purring.send", secrets.token_hex(4)))
    m.commit()
    rt = _rt()
    try:
        with pytest.raises(psycopg.errors.CheckViolation) as e:
            _slett(rt, pid)
        assert "beslutning" in str(e.value)
        rt.rollback()
        from db.pg import sett_kontekst
        sett_kontekst(rt, TEN, "test", "r2")
        assert rt.execute("SELECT count(*) FROM policyer WHERE tenant=%s"
                          " AND policy_id=%s", (TEN, pid)).fetchone()[0] == 1
    finally:
        rt.close()
        m.close()


@pg
def test_apen_runde_blokkerer_sletting():
    import psycopg
    pid = "p-" + secrets.token_hex(3)
    uid = "u-" + secrets.token_hex(6)
    m = _mig()
    _policyrad(m, pid)
    m.execute(
        "INSERT INTO policyutkast (tenant,utkast_id,policy_id,innhold,"
        "opprettet_av) VALUES (%s,%s,%s,'{}'::jsonb,'forf')", (TEN, uid, pid))
    m.execute(
        "INSERT INTO aktiveringsrunde (tenant,utkast_id,runde,status,"
        "diff_hash,utkast_innholds_hash,base_policy_hash,risikoklasse,"
        "klassifisering_hash,klassifikatorversjon,policyskjema_versjon,"
        "motor_semantikkversjon,deny_all_hash,deny_all_versjon,"
        "pakrevd_antall_godkjennere,utloper)"
        " VALUES (%s,%s,1,'apen','d','i','b','UTVIDER','k','1','0.2','1',"
        "'dh','1',2,now()+interval '1 hour')", (TEN, uid))
    m.commit()
    rt = _rt()
    try:
        with pytest.raises(psycopg.errors.CheckViolation) as e:
            _slett(rt, pid)
        assert "runde" in str(e.value)
        rt.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_utkast_og_runder_roeres_ikke_av_slettingen():
    """At mennesker attesterte er et faktum om fortiden. Slettingen angrer
    RESULTATET, ikke historien — nøyaktig det de manuelle oppryddingene
    bevarte."""
    pid = "p-" + secrets.token_hex(3)
    uid = "u-" + secrets.token_hex(6)
    m = _mig()
    _policyrad(m, pid)
    m.execute(
        "INSERT INTO policyutkast (tenant,utkast_id,policy_id,innhold,"
        "opprettet_av,status) VALUES (%s,%s,%s,'{}'::jsonb,'forf','aktivert')",
        (TEN, uid, pid))
    m.commit()
    rt = _rt()
    try:
        assert _slett(rt, pid) == 1
        rt.commit()
        from db.pg import sett_kontekst
        sett_kontekst(rt, TEN, "test", "r2")
        u = rt.execute("SELECT status FROM policyutkast WHERE tenant=%s"
                       " AND utkast_id=%s", (TEN, uid)).fetchone()
        assert u == ("aktivert",), "utkastets historie ble rørt"
    finally:
        rt.close()
        m.close()


# ---------------------------------------------------------------------------
# Serialisering mot BRUK (Codex P1 + P2). Garantien «aldri brukt» er ikke et
# utsagn om øyeblikket funksjonen kikker — den må holde over hele vinduet der
# noen andre kan gjøre policyen brukt. Radlåsen på `policy_hode` klarte det
# ikke: verken beslutningsveien eller runde-åpningen rører den raden.
# ---------------------------------------------------------------------------

def _sperret(c, pid, ms=400):
    """Prøv å slette med en kort låsefrist. -> True hvis vi ble sperret."""
    import psycopg
    c.execute("SET LOCAL lock_timeout = %s", (f"{ms}ms",))
    try:
        _slett(c, pid)
        return False
    except psycopg.errors.LockNotAvailable:
        return True


@pg
def test_sletting_venter_paa_en_beslutning_som_er_i_gang():
    """Kontroll: fjern `laas_policy_delt` fra `policyregister.hent_aktiv`, så
    blir denne rød — og med den forsvinner hele «aldri brukt»-garantien.

    Uten låsen: beslutningen leser policyen, slettingen ser en revisjonslogg
    uten spor og committer, og SÅ skriver beslutningen revisjonsraden sin. Et
    revisjonsspor som peker på en policy som ikke finnes er ikke et spor.
    """
    pid = "p-" + secrets.token_hex(3)
    m = _mig()
    _policyrad(m, pid)
    m.commit()
    beslutning, sletter = _rt(), _rt()
    try:
        # Beslutningsveien, midt i sin transaksjon: den har lest policyen, men
        # revisjonsraden er ikke skrevet ennå. Innholdet i fixturen består ikke
        # revalideringen — og det er nettopp poenget: låsen tas FØR lesingen,
        # så den holder uansett hva lesingen ender med.
        with pytest.raises(pr.PolicyKorrupt):
            pr.hent_aktiv(beslutning, TEN, pid)

        assert _sperret(sletter, pid), \
            "slettingen gikk forbi en beslutning som var i gang"
        sletter.rollback()

        # Og når beslutningen er ferdig, slipper slettingen til.
        beslutning.rollback()
        from db.pg import sett_kontekst
        sett_kontekst(sletter, TEN, "test", "r3")
        assert _slett(sletter, pid) == 1
        sletter.commit()
    finally:
        beslutning.close()
        sletter.close()
        m.close()


@pg
def test_sletting_venter_paa_en_runde_som_aapnes():
    """Kontroll: fjern `laas_policy_delt` fra `policyadmin._hode_aktiv_versjon`,
    så blir denne rød — og godkjennere kunne blitt sendt inn i en runde på en
    policy som ble slettet under dem, og som aldri kan aktiveres."""
    from api import policyadmin
    pid = "p-" + secrets.token_hex(3)
    m = _mig()
    _policyrad(m, pid)
    m.commit()
    runde, sletter = _rt(), _rt()
    try:
        # Runde-åpningen har validert basen, men INSERT-en er ikke gjort.
        assert policyadmin._hode_aktiv_versjon(runde, TEN, pid) == "1.0.0"
        assert _sperret(sletter, pid), \
            "slettingen gikk forbi en runde-åpning som var i gang"
        sletter.rollback()
        runde.rollback()
    finally:
        runde.close()
        sletter.close()
        m.close()


@pg
def test_to_beslutninger_staar_ikke_i_ko_bak_hverandre():
    """Låsen er DELT for lesere. Var den eksklusiv, ville hver beslutning på
    samme policy serialisert mot alle andre — en riktig garanti kjøpt for en
    pris ingen ba om."""
    pid = "p-" + secrets.token_hex(3)
    m = _mig()
    _policyrad(m, pid)
    m.commit()
    a, b = _rt(), _rt()
    try:
        from db.pg import laas_policy_delt
        laas_policy_delt(a, TEN, pid)
        b.execute("SET LOCAL lock_timeout = '400ms'")
        laas_policy_delt(b, TEN, pid)   # skal ikke kaste
        a.rollback()
        b.rollback()
    finally:
        a.close()
        b.close()
        m.close()


@pg
def test_sletting_krever_matchende_tenantkontekst():
    """SECURITY DEFINER omgår RLS — da er kontekstsjekken inne i funksjonen
    hele tenant-isolasjonen. Kontroll: fjern den, så blir denne rød."""
    import psycopg
    pid = "p-" + secrets.token_hex(3)
    m = _mig()
    _policyrad(m, pid)
    m.commit()
    rt = _rt(tenant="t-annen-" + secrets.token_hex(3))
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT slett_ubrukt_policy(%s,%s)", (TEN, pid))
        rt.rollback()
    finally:
        rt.close()
        m.close()
