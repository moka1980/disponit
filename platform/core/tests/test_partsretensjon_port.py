"""Porten for 184: retensjonen i partsregisteret.

ADRESSEN DØR, SPORET BESTÅR — 088s form, og her med en forskjell som er
selve poenget: fristen løper fra DEAKTIVERINGEN, ikke fra opprettelsen.
En e-postmelding er en hendelse som blir gammel. En kunde er et FORHOLD
som varer, og adressen skal virke så lenge forholdet gjør.

Lageret er navngitt i M-4s register i samme migrasjon. Eiers egen
gjennomgang fant at «M-4s retensjonsregister mangler de fleste modulenes
lagre → personvernsaker kan ikke navngi dem»; et nytt lager med
personopplysninger skulle ikke bli det neste som manglet.
"""
import secrets

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo, pg  # noqa: F401
from .test_m37 import _sett_kontekst

NONCE = bytes(range(12))


def _kobling(dsn):
    from db.pg import koble
    return koble(dsn)


def _t():
    return "t-reap-" + secrets.token_hex(3)


def _reap(c, t, grense=200, runder=25):
    """Reaperen kalles av DEN SOM EIER RYDDEJOBBEN i dette miljøet
    (038/057-formen): finnes `disponit_domener`, er det den; lokalt er
    det runtime. Migrator har den ALDRI — det er et grant som virker,
    ikke en feil.

    TO GRUNNER TIL AT DETTE IKKE ER ETT KALL (begge funnet i denne
    testfilen, den andre av CodeRabbit):

      1. Reaperen er KRYSS-TENANT og rydder alt modent i hele basen. En
         port som talte TOTALEN ville vært grønn eller rød etter hva
         nabotestene hadde lagt igjen. Derfor telles bare egen tenant.
      2. Reaperen har en GRENSE. Ligger det mange modne rader hos andre
         tenanter, kan min rad falle utenfor batchen — og porten ville
         rapportert «ikke reapet» om noe som bare sto i kø. Derfor
         drenerer vi til basen er tom.

    Begge er samme feilklasse: en måling som avhenger av en tilstand
    testen ikke satte.
    """
    mine = 0
    for _ in range(runder):
        rader = c.execute("SELECT tenant FROM reap_partkontakt(%s)",
                          (grense,)).fetchall()
        c.commit()
        if not rader:
            return mine
        mine += sum(1 for r in rader if r[0] == t)
    raise AssertionError("reaperen ble ikke tom — drenerte 25 runder")


def _toem_basen(c):
    """Rydder alt modent FØR testen lager sitt eget. Da er den ene
    runden testen måler, garantert bare testens egne rader."""
    _reap(c, None)


def _part_med_kontakt(c, t, ref="K-1"):
    p = c.execute("SELECT part_registrer(%s,%s,'Fjordlys AS',NULL,"
                  "'bedrift','kari')", (t, ref)).fetchone()[0]
    psn = c.execute("SELECT tenant_pseudonym(%s,%s)",
                    (t, f"{ref}@fjordlys.no")).fetchone()[0]
    k = c.execute("SELECT part_sett_kontakt(%s,%s,'epost','po**@fjordlys.no',"
                  "%s,%s,'k1',%s,true,NULL,'kari')",
                  (t, p, b"\x01\x02", NONCE, psn)).fetchone()[0]
    return p, k, psn


@pg
def test_fristen_loeper_fra_avviklingen_ikke_fra_opprettelsen():
    """FORSKJELLEN FRA EN E-POSTMELDING, målt. En aktiv kunde reapes
    ALDRI, uansett hvor gammelt kontaktpunktet er — ellers ville
    purringen til en kunde man har hatt i fem år gått til ingenting."""
    t = _t()
    c, m = _kobling(DSN), _kobling(MIGRATOR_DSN)
    try:
        _toem_basen(c)
        _sett_kontekst(c, t)
        p, k, _ = _part_med_kontakt(c, t)
        c.commit()
        # Gammelt kontaktpunkt, men parten LEVER: ingenting skal reapes.
        _sett_kontekst(m, t)
        m.execute("UPDATE partkontakt SET opprettet = now()"
                  " - interval '5 years' WHERE tenant=%s", (t,))
        m.commit()
        assert _reap(c, t) == 0, "reapet en LEVENDE kundes adresse"
        m.rollback()
        # Avviklet, men fristen er ikke ute.
        _sett_kontekst(c, t)
        c.execute("SELECT part_deaktiver(%s,%s,'kari')", (t, p))
        c.commit()
        assert _reap(c, t) == 0, "reapet før fristen"
        m.rollback()
    finally:
        c.close(); m.close()


@pg
def test_adressen_doer_og_sporet_bestaar():
    """088s form: maske, ciphertext, nonce og nøkkel-id blankes, raden
    består med `slettet_ts`. `verdi_pseudonym` BLIR STÅENDE — 078s egen
    dom: ikke-reverserbart, og unikheten må overleve TTL-utløpet.

    Og parten selv består. Navnet er ikke adressen.
    """
    t = _t()
    c, m = _kobling(DSN), _kobling(MIGRATOR_DSN)
    try:
        _toem_basen(c)
        _sett_kontekst(c, t)
        p, k, psn = _part_med_kontakt(c, t)
        c.execute("SELECT part_deaktiver(%s,%s,'kari')", (t, p))
        c.commit()
        _sett_kontekst(m, t)
        m.execute("UPDATE part SET deaktivert_ts = now()"
                  " - interval '91 days' WHERE tenant=%s", (t,))
        m.commit()
        reapet = _reap(c, t)
        assert reapet == 1, f"reapet {reapet}, ventet 1"
        m.commit()
        _sett_kontekst(m, t)
        rad = m.execute(
            "SELECT verdi_maske, verdi_kryptert, verdi_nonce, verdi_key_id,"
            " verdi_pseudonym, slettet_ts IS NOT NULL, slettet_av, primar"
            " FROM partkontakt WHERE tenant=%s AND kontakt_id=%s",
            (t, k)).fetchone()
        assert rad[0] is None and rad[1] is None, "adressen består"
        assert rad[2] is None and rad[3] is None, "nøkkelsporet består"
        assert rad[4] == psn, "pseudonymet forsvant — unikheten er brutt"
        assert rad[5] is True and rad[6] == "retensjon"
        assert rad[7] is False, "en tømt rad sto igjen som primær"
        # Parten selv består: navnet er ikke adressen.
        navn = m.execute("SELECT navn FROM part WHERE tenant=%s", (t,)
                         ).fetchone()[0]
        assert navn == "Fjordlys AS"
        # Andre runde reaper ingenting — reaperen er idempotent.
        assert _reap(c, t) == 0
        m.rollback()
    finally:
        c.close(); m.close()


@pg
def test_reaperen_legger_tenantkonteksten_tilbake():
    """KRYSS-TENANT-REAPERENS EGEN FELLE (038-læren): den setter radens
    tenant underveis. Lot den en fremmed tenant stå igjen i sesjonen,
    ville lekkasjen kommet i NESTE kall og sett ut som noe helt annet."""
    t1, t2 = _t(), _t()
    c, m = _kobling(DSN), _kobling(MIGRATOR_DSN)
    try:
        _toem_basen(c)
        for t in (t1, t2):
            _sett_kontekst(c, t)
            p, _, _ = _part_med_kontakt(c, t)
            c.execute("SELECT part_deaktiver(%s,%s,'kari')", (t, p))
            c.commit()
        # HVER TENANT UNDER SIN EGEN KONTEKST: migrator er ikke unntatt
        # RLS, så én UPDATE med `tenant IN (t1,t2)` treffer bare den ene
        # — og testen ville målt halvparten av det den trodde.
        for t in (t1, t2):
            _sett_kontekst(m, t)
            m.execute("UPDATE part SET deaktivert_ts = now()"
                      " - interval '200 days' WHERE tenant=%s", (t,))
            m.commit()
        _sett_kontekst(c, t1)
        rader = c.execute("SELECT tenant FROM reap_partkontakt(200)"
                          ).fetchall()
        # KONTEKSTEN LESES FØR COMMIT. `set_config(..., true)` er
        # transaksjonslokal, så en commit her ville tømt den uansett hva
        # reaperen gjorde — og porten ville målt psycopg, ikke dommen.
        etter = c.execute("SELECT current_setting('disponit.tenant', true)"
                          ).fetchone()[0]
        assert etter == t1, f"konteksten sto igjen som {etter!r}"
        c.commit()
        mine = {r[0] for r in rader} & {t1, t2}
        assert mine == {t1, t2}, f"reaperen så bare {mine}"
        m.rollback()
    finally:
        c.close(); m.close()


@pg
def test_begge_lagrene_er_navngitt_i_m4_registeret(migrator):
    """Et lager M-4 ikke navngir, kan ingen personvernsak dekke. Porten
    måler BEGGE: parten (uten frist, med en BEGRUNNET dom) og
    kontaktpunktet (under frist, med reaper og reapet-kolonne).

    Vakten i 093 krever at `under_frist` har reaper, fristkilde OG
    reapetkolonne — en halv registrering er ingen registrering.
    """
    # Lest som lagerets EIER: `retensjonslager` er `disponit_lager_eier`s,
    # og personvernregisteret har kolonnegrant på `lager_id` alene (099
    # §3). Samme grep som `_m4_lagre` i test_m30.
    migrator.execute("SET LOCAL ROLE disponit_lager_eier")
    rader = dict((r[0], r) for r in migrator.execute(
        "SELECT lager_id, klasse, dom, reaper, fristkilde, reapetkolonne,"
        " frist_dogn, dom_migrasjon FROM retensjonslager"
        " WHERE lager_id IN ('part','partkontakt')").fetchall())
    migrator.rollback()
    assert set(rader) == {"part", "partkontakt"}, rader
    p = rader["part"]
    assert p[1] == "persondata" and p[2] == "uten_frist_akseptert"
    assert p[3] is None and p[4] is None, "en dom uten frist har ingen reaper"
    k = rader["partkontakt"]
    assert k[1] == "persondata" and k[2] == "under_frist"
    assert k[3] == "reap_partkontakt"
    assert "deaktivert_ts" in k[4], k[4]
    assert k[5] == "slettet_ts" and k[6] == 90
    assert k[7] == "184" and p[7] == "184"


@pg
def test_maaleren_naar_lageret_sitt_og_bare_de_tre_kolonnene(migrator):
    """093 §6.3 deler ut kolonnegrantene og `m4_maaler`-policyen i en
    ENGANGS `DO`-blokk, utledet av registeret slik det så ut DA. Et lager
    registrert senere får ingenting — og et målt lager måleren ikke kan
    lese, er en registrering uten måling (CodeRabbit; verifisert ved å
    fjerne grantet og kjøre migrasjonen om igjen: det kom ikke tilbake).

    Porten måler begge halvdeler: at måleren NÅR lageret, og at den bare
    når de tre kolonnene registerraden navngir. En payloadkolonne her
    ville vært måleren som leser persondata.
    """
    kolonner = {r[0] for r in migrator.execute(
        "SELECT column_name FROM information_schema.column_privileges"
        " WHERE grantee='disponit_lager_eier' AND table_name='partkontakt'"
        "   AND privilege_type='SELECT'").fetchall()}
    assert kolonner == {"tenant", "opprettet", "slettet_ts"}, kolonner
    # INGEN tabellgrant: det ville gitt alle kolonner uten å vises over.
    assert migrator.execute(
        "SELECT count(*) FROM information_schema.role_table_grants"
        " WHERE grantee='disponit_lager_eier'"
        "   AND table_name IN ('part','partkontakt')").fetchone()[0] == 0
    # Kryss-tenant-autoriteten er en POLICY, ikke en rolleattributt.
    assert migrator.execute(
        "SELECT count(*) FROM pg_policies WHERE tablename='partkontakt'"
        "   AND policyname='m4_maaler'").fetchone()[0] == 1
    # `part` måles ikke (uten_frist_akseptert) og skal ikke ha noe.
    assert migrator.execute(
        "SELECT count(*) FROM information_schema.column_privileges"
        " WHERE grantee='disponit_lager_eier' AND table_name='part'"
    ).fetchone()[0] == 0
    migrator.rollback()


@pg
def test_fristen_er_tenantens_valg_innenfor_grensene():
    """30–365 døgn, som e-postinntaket. En frist på null ville vært
    sletting ved avvikling, og en på ti år ville vært ingen frist — begge
    er beslutninger som skal tas bevisst, ikke skrives inn i en kolonne."""
    import psycopg

    t = _t()
    c, m = _kobling(DSN), _kobling(MIGRATOR_DSN)
    try:
        _sett_kontekst(c, t)
        _part_med_kontakt(c, t)
        c.commit()
        _sett_kontekst(m, t)
        assert m.execute("SELECT slettefrist_dogn FROM part WHERE tenant=%s",
                         (t,)).fetchone()[0] == 90
        for ulovlig in (0, 29, 366, -1):
            with pytest.raises(psycopg.errors.CheckViolation):
                m.execute("UPDATE part SET slettefrist_dogn=%s WHERE tenant=%s",
                          (ulovlig, t))
            m.rollback()
            _sett_kontekst(m, t)
        m.execute("UPDATE part SET slettefrist_dogn=30 WHERE tenant=%s", (t,))
        m.rollback()
    finally:
        c.close(); m.close()
