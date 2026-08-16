"""Angre en feilopprettet policy — slett den som ALDRI er brukt (032).

Behovet er målt: `tjenestebedrift1` og `tjenestebedrift2` ble aktivert ved
feil, og eneste vei ut var håndskrevet SQL som postgres, to ganger på én dag.

Grensene er hele verdien av funksjonen:
  * en policy som har styrt én beslutning kan ALDRI slettes — loggen
    refererer den, og et spor som peker på ingenting er ikke et spor;
  * en åpen runde blokkerer, som for forkast: attestasjoner i omløp;
  * ankerraden består (append-only), bare pekeren nullstilles;
  * utkast og runder røres ikke — at mennesker attesterte er et faktum om
    fortiden. Ett unntak, og det er en OVERGANG, ikke en opprydding: en runde
    som har passert `utloper` skrives ned som `utlopt` før vernet teller, som
    i forkast og runde-åpning;
  * versjonene blir ledige igjen, så riktig opprettelse etterpå ikke stoppes
    av 020-monotonien.
"""
import json
import secrets
from datetime import datetime, timezone

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
    """Kontroll: fjern revisjonslogg-sjekken i 032, så blir denne rød —
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
def test_forfalt_runde_blokkerer_ikke_sletting():
    """Kontroll: fjern `_lukk_forfalte_runder`-kallet i
    `policyadmin.slett_policy`, så blir denne rød.

    En runde som har passert `utloper` er DØD — `attester_aktivering` nekter
    den med `runde_utlopt`, så det finnes ingen attestasjoner i omløp å verne.
    Men statusen i basen blir stående `apen` til en skrivesti kommer forbi og
    skriver ned overgangen, og slettingen teller den lagrede statusen. Uten
    overgangen her svarte en ubrukt policy med en timet-ut runde
    `runde_allerede_aapen` for alltid: vilkåret var oppfylt, men ingen hadde
    skrevet det ned — og eier satt igjen uten vei ut, akkurat den tilstanden
    `_lukk_forfalt_runde` finnes for.
    """
    from api import policyadmin
    pid = "p-" + secrets.token_hex(3)
    uid = "u-" + secrets.token_hex(6)
    idem = "idem-" + secrets.token_hex(8)
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
        "'dh','1',2,now()-interval '1 hour')", (TEN, uid))
    m.commit()
    rt = _rt()
    try:
        res = policyadmin.slett_policy(
            rt, tenant=TEN, aktor="test", request_id="r1", policy_id=pid,
            idempotency_key=idem, input_hash="ih-" + idem,
            naa=datetime.now(timezone.utc))
        assert res["slettet"] == 1
        from db.pg import sett_kontekst
        sett_kontekst(m, TEN, "test", "r0")
        # Overgangen ble SKREVET NED, ikke bare oversett i et predikat: runden
        # står nå `utlopt`, som etter forkast og runde-åpning.
        assert m.execute(
            "SELECT status FROM aktiveringsrunde WHERE tenant=%s"
            " AND utkast_id=%s", (TEN, uid)).fetchone() == ("utlopt",)
    finally:
        rt.close()
        m.close()


@pg
def test_levende_runde_blokkerer_fortsatt_etter_forfallsovergangen():
    """Motstykket: overgangen skal lukke de DØDE rundene, ikke svekke vernet.
    En runde som fortsatt kan attesteres blokkerer som før."""
    from api import policyadmin
    pid = "p-" + secrets.token_hex(3)
    uid = "u-" + secrets.token_hex(6)
    idem = "idem-" + secrets.token_hex(8)
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
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            policyadmin.slett_policy(
                rt, tenant=TEN, aktor="test", request_id="r1", policy_id=pid,
                idempotency_key=idem, input_hash="ih-" + idem,
                naa=datetime.now(timezone.utc))
        assert e.value.kode == "runde_allerede_aapen"
        from db.pg import sett_kontekst
        sett_kontekst(m, TEN, "test", "r0")
        assert m.execute(
            "SELECT status FROM aktiveringsrunde WHERE tenant=%s"
            " AND utkast_id=%s", (TEN, uid)).fetchone() == ("apen",)
        assert m.execute("SELECT count(*) FROM policyer WHERE tenant=%s"
                         " AND policy_id=%s", (TEN, pid)).fetchone()[0] == 1
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


@pg
def test_slettingen_er_idempotent_og_replayer_suksess():
    """Kontroll: fjern `_idempotent_start`/`_fullfor` fra
    `policyadmin.slett_policy`, så blir denne rød.

    Slettingen er engangs og irreversibel. Mister klienten svaret — grunnen
    til at `Idempotency-Key` er påkrevd i det hele tatt — er policyen borte, og
    retryen møtte `policy_ukjent`: en endelig FEIL på en operasjon som lyktes,
    på en flate som fortsatt viste policyen som aktiv.
    """
    from api import policyadmin
    pid = "p-" + secrets.token_hex(3)
    idem = "idem-" + secrets.token_hex(8)
    m = _mig()
    _policyrad(m, pid)
    m.commit()
    rt = _rt()
    try:
        kall = dict(tenant=TEN, aktor="test", request_id="r1",
                    policy_id=pid, idempotency_key=idem,
                    input_hash="ih-" + idem,
                    naa=datetime.now(timezone.utc))
        forste = policyadmin.slett_policy(rt, **kall)
        assert forste["slettet"] == 1 and forste["policy_id"] == pid

        # Nøyaktig samme forespørsel én gang til: SAMME svar, ikke policy_ukjent.
        andre = policyadmin.slett_policy(rt, **kall)
        assert andre["slettet"] == 1 and andre["policy_id"] == pid
        assert andre.get("replay") is True

        # Samme nøkkel på en ANNEN operasjon er en konflikt, ikke en replay.
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            policyadmin.slett_policy(rt, **{**kall, "input_hash": "ih-annet"})
        assert e.value.kode == "idempotenskonflikt"
    finally:
        rt.close()
        m.close()


@pg
def test_mislykket_sletting_brenner_ikke_nokkelen():
    """En operasjon som IKKE skjedde skal ikke låse nøkkelen: eier må kunne
    rydde opp (lukke runden) og prøve på nytt med samme nøkkel."""
    from api import policyadmin
    pid = "p-" + secrets.token_hex(3)
    uid = "u-" + secrets.token_hex(6)
    idem = "idem-" + secrets.token_hex(8)
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
        kall = dict(tenant=TEN, aktor="test", request_id="r1",
                    policy_id=pid, idempotency_key=idem,
                    input_hash="ih-" + idem,
                    naa=datetime.now(timezone.utc))
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            policyadmin.slett_policy(rt, **kall)
        assert e.value.kode == "runde_allerede_aapen"

        # Konteksten er `SET LOCAL` og døde med commit-en over — uten denne
        # linja treffer UPDATE-en null rader bak RLS, og runden blir stående.
        from db.pg import sett_kontekst
        sett_kontekst(m, TEN, "test", "r0")
        m.execute("UPDATE aktiveringsrunde SET status='kansellert'"
                  " WHERE tenant=%s AND utkast_id=%s", (TEN, uid))
        m.commit()
        assert policyadmin.slett_policy(rt, **kall)["slettet"] == 1
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
    """Prøv å slette med en kort låsefrist. -> True hvis vi ble sperret.

    `set_config(..., true)` og ikke `SET LOCAL`: `SET` tar ikke bind-parametre
    (`syntax error at or near "$1"`), og en interpolert streng i en test er en
    vane man ikke vil ha. Samme form som `db.pg.sett_tenant` bruker.
    """
    import psycopg
    c.execute("SELECT set_config('lock_timeout', %s, true)", (f"{ms}ms",))
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
        b.execute("SELECT set_config('lock_timeout', '400ms', true)")
        laas_policy_delt(b, TEN, pid)   # skal ikke kaste
        a.rollback()
        b.rollback()
    finally:
        a.close()
        b.close()
        m.close()


@pg
def test_en_loggreferanse_gjor_policyen_uslettelig_for_godt():
    """Vernet for de lesestiene som IKKE tar policylåsen (Codex P1).

    `m37.arbeider._aktiv_policy` (reparasjonsplanlegging) og
    `api.app._ingest_verifikasjon` (aktiv autoritet før bevis godtas) leser
    den aktive policyen en REVISJONSREFERANSE navngir, gjennom
    `policyregister.hent_aktiv_bak_loggreferanse`. Låsen i `hent_aktiv` er
    for lesere som ennå ikke har et spor; disse to har det allerede, og
    sporet er nøyaktig raden 032 teller når den avgjør «aldri brukt».

    Testen holder begge halvdelene av det argumentet fast, for det er
    sammen de utgjør vernet:
      1. lesestien finner faktisk policyen bak referansen (ellers måler
         punkt 2 noe annet enn det veien gjør);
      2. fra det øyeblikket referansen står, avviser slettingen;
      3. og referansen kan ikke fjernes igjen — `revisjonslogg` er
         append-only (001), også for migratorrollen. Uten den er «avvist
         nå» bare en utsettelse.

    Flyttes en av disse lesningene til en policy-id som IKKE kommer fra
    loggen, faller argumentet — og da trenger den veien sitt eget vern.
    """
    import psycopg
    pid = "p-" + secrets.token_hex(3)
    ref = f"{pid}@1.0.0/purring.send"
    m = _mig()
    _policyrad(m, pid)
    logg_id = m.execute(
        "INSERT INTO revisjonslogg (tenant, ts, policy_id, beslutning,"
        " begrunnelse, input_hash) VALUES (%s, now(), %s, 'TILLAT',"
        " '{}', 'ih-' || %s) RETURNING id",
        (TEN, ref, secrets.token_hex(4))).fetchone()[0]
    m.commit()
    rt = _rt()
    try:
        # 1. Veien de to lesestiene faktisk går.
        p = pr.hent_aktiv_bak_loggreferanse(rt, TEN, ref)
        assert p is not None, "lesestien fant ikke policyen bak referansen"
        assert p[0]["meta"]["policy_id"] == pid

        # 2. Og med referansen på plass er policyen ikke slettbar.
        with pytest.raises(psycopg.errors.CheckViolation):
            _slett(rt, pid)
        rt.rollback()

        # 3. Referansen kan ikke fjernes — så «ikke slettbar» er permanent.
        # Konteksten settes på nytt FØRST: `sett_kontekst` er transaksjons-
        # lokal (`set_config(..., true)`) og døde med commiten over. Uten den
        # skjuler RLS raden, DELETE treffer null rader, triggeren fyrer aldri
        # — og testen ville bestått på at den ikke fant noe å slette.
        from db.pg import sett_kontekst
        sett_kontekst(m, TEN, "test", "r0")
        assert m.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s AND id=%s",
            (TEN, logg_id)).fetchone()[0] == 1, \
            "referansen er ikke synlig — da måler DELETE-en under ingenting"
        # Feilen MÅ komme fra append-only-triggeren: en rettighetsfeil ville
        # sagt at migratoren ikke fikk lov nettopp her, ikke at raden står.
        with pytest.raises(psycopg.Error) as e:
            m.execute("DELETE FROM revisjonslogg WHERE tenant=%s AND id=%s",
                      (TEN, logg_id))
        assert "append-only" in str(e.value)
        m.rollback()
    finally:
        rt.close()
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
