"""Porten for 187: fordringen peker på KUNDEN, ikke på en tekststreng.

EIERS MÅL: «Ikke gå gjennom hver modul og fylle». Registeret (183) ga
plassen; dette er den FØRSTE modulen som faktisk leser derfra. Uten en
slik kobling er registeret bare et ekstra sted å vedlikeholde.

Portene måler de fire tilstandene som finnes i praksis: kunden er der,
kunden er ikke der, kunden kommer til etterpå, og kunden får rettet
navnet sitt.
"""
import secrets

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo, pg  # noqa: F401
from .test_m37 import _sett_kontekst


def _kobling(dsn):
    from db.pg import koble
    return koble(dsn)


def _t():
    return "t-fkob-" + secrets.token_hex(3)


def _fordringene(c, t):
    return {r[1]: r for r in c.execute(
        "SELECT fordring_id, kunde_ref, part_id, part_navn"
        "  FROM m23_fordringene(%s, 50)", (t,)).fetchall()}


def _registrer(c, t, ref, nr):
    import uuid
    fid = uuid.uuid4()
    c.execute("SELECT m23_registrer_fordring(%s,%s,%s,%s,250000,"
              " current_date - 40, current_date - 10, 'kari')",
              (t, fid, ref, nr))
    return fid


@pg
def test_kunden_knyttes_ved_registrering_og_navnet_kommer_med():
    """GEVINSTEN, målt: en fordring registrert på en referanse registeret
    KJENNER, bærer kundens navn uten at noen skrev det inn i M-23."""
    t = _t()
    c = _kobling(DSN)
    try:
        _sett_kontekst(c, t)
        c.execute("SELECT part_registrer(%s,'K-77','Fjordlys Elektro AS',"
                  "'912345678','bedrift','kari')", (t,))
        _registrer(c, t, "K-77", "F-1001")
        rad = _fordringene(c, t)["K-77"]
        assert rad[2] is not None, "fordringen ble ikke knyttet til kunden"
        assert rad[3] == "Fjordlys Elektro AS"
        c.rollback()
    finally:
        c.close()


@pg
def test_en_ukjent_referanse_virker_akkurat_som_foer():
    """INGEN KALLER BRYTES. `kunde_ref` er fortsatt broen, og en
    referanse registeret ikke kjenner gir NULL — ikke en feil. Ellers
    ville koblingen stanset modulens egen arm og enhver integrasjon den
    dagen den ble deployet."""
    t = _t()
    c = _kobling(DSN)
    try:
        _sett_kontekst(c, t)
        _registrer(c, t, "finnes-ikke-i-registeret", "F-2001")
        rad = _fordringene(c, t)["finnes-ikke-i-registeret"]
        assert rad[2] is None and rad[3] is None
        # ...og raden er ellers en helt vanlig fordring.
        n = c.execute("SELECT count(*) FROM m23_fordringene(%s,50)",
                      (t,)).fetchone()[0]
        assert n == 1
        c.rollback()
    finally:
        c.close()


@pg
def test_kunden_kan_legges_inn_etterpaa_og_knyttes():
    """DEN VANLIGE REKKEFØLGEN I PRAKSIS: kravet kommer først, kunden
    føres inn etterpå. Uten `m23_knytt_part` måtte raden skrives om."""
    t = _t()
    c = _kobling(DSN)
    try:
        _sett_kontekst(c, t)
        fid = _registrer(c, t, "K-sen", "F-3001")
        assert _fordringene(c, t)["K-sen"][2] is None
        c.execute("SELECT part_registrer(%s,'K-sen','Sent registrert AS',"
                  "NULL,'bedrift','kari')", (t,))
        assert c.execute("SELECT m23_knytt_part(%s,%s)",
                         (t, fid)).fetchone()[0] is True
        rad = _fordringene(c, t)["K-sen"]
        assert rad[2] is not None and rad[3] == "Sent registrert AS"
        # Gjentatt kall er stille FALSE: døra er idempotent, og sier at
        # den ikke endret noe — ikke at den feilet.
        assert c.execute("SELECT m23_knytt_part(%s,%s)",
                         (t, fid)).fetchone()[0] is False
        assert _fordringene(c, t)["K-sen"][2] == rad[2]
        c.rollback()
    finally:
        c.close()


@pg
def test_navnet_rettes_ETT_sted_og_er_rettet_overalt():
    """HELE POENGET MED ET REGISTER. Navnet er ikke kopiert inn i
    fordringen — det leses gjennom fremmednøkkelen. En skrivefeil rettet
    i kunderegisteret er rettet i kravet i samme øyeblikk.

    MUTASJONEN SOM DREPER DENNE: kopier `part.navn` inn i en kolonne på
    `fordring` i stedet for å lese den gjennom `part_id`.
    """
    t = _t()
    c = _kobling(DSN)
    try:
        _sett_kontekst(c, t)
        c.execute("SELECT part_registrer(%s,'K-77','Fjordlys Elektroo AS',"
                  "NULL,'bedrift','kari')", (t,))
        _registrer(c, t, "K-77", "F-4001")
        assert _fordringene(c, t)["K-77"][3] == "Fjordlys Elektroo AS"
        c.execute("SELECT part_registrer(%s,'K-77','Fjordlys Elektro AS',"
                  "NULL,'bedrift','kari')", (t,))
        assert _fordringene(c, t)["K-77"][3] == "Fjordlys Elektro AS"
        c.rollback()
    finally:
        c.close()


@pg
def test_modulen_ser_kunden_men_aldri_kontaktpunktene():
    """KOLONNEGRANT, ALDRI TABELLGRANT. M-23 skal vite HVEM kunden er —
    aldri hvilke adresser hun har. Kontaktpunktene har sin egen vei og
    sitt eget scope, og en modul som kunne lese dem ville vært en ny
    lesevei forbi det.

    Porten måler grantet, ikke disiplinen i koden."""
    t = _t()
    m = _kobling(MIGRATOR_DSN)
    try:
        kolonner = {r[0] for r in m.execute(
            "SELECT column_name FROM information_schema.column_privileges"
            " WHERE grantee='disponit_fordring_eier' AND table_name='part'"
            "   AND privilege_type='SELECT'").fetchall()}
        assert kolonner == {"tenant", "part_id", "part_ref", "navn"}, kolonner
        # INGEN tilgang til kontaktpunktene i det hele tatt.
        assert m.execute(
            "SELECT count(*) FROM information_schema.column_privileges"
            " WHERE grantee='disponit_fordring_eier'"
            "   AND table_name='partkontakt'").fetchone()[0] == 0
        assert m.execute(
            "SELECT count(*) FROM information_schema.role_table_grants"
            " WHERE grantee='disponit_fordring_eier'"
            "   AND table_name IN ('part','partkontakt')").fetchone()[0] == 0
        m.rollback()
    finally:
        m.close()


@pg
def test_backfillen_slaar_ALDRI_sammen_to_referanser(migrator):
    """MÅLT FØR DESIGNET: referansene i drift identifiserer ikke den
    samme kunden på tvers. Fordring har både firmanavn («Havnegata
    Eiendom AS») og koder («kunde-nordbyen»); tilbud har ingen referanse
    i det hele tatt.

    En backfill som GJETTET at to av dem er den samme kunden, ville slått
    sammen to virkelige kunder — vanskelig å oppdage, verre å rette.
    Derfor: én part per referanse, alltid. Sammenslåingen er et
    menneskes valg, og den veien bygges når tilbud kobles på.
    """
    t = _t()
    c = _kobling(DSN)
    try:
        _sett_kontekst(c, t)
        # To referanser som ÅPENBART er samme kunde for et menneske.
        for ref, nr in (("Fjordlys Elektro AS", "F-5001"),
                        ("fjordlys-elektro", "F-5002")):
            c.execute("SELECT part_registrer(%s,%s,%s,NULL,'bedrift','kari')",
                      (t, ref, ref))
            _registrer(c, t, ref, nr)
        rader = _fordringene(c, t)
        assert len({rader[r][2] for r in rader}) == 2, \
            "backfillen slo sammen to referanser"
        c.rollback()
    finally:
        c.close()


@pg
def test_doera_binder_tenanten_til_konteksten_og_til_radens_egen_referanse():
    """TO FUNN, BEGGE PÅ SAMME DØR (CodeRabbit + lesningen etterpå).

    1. `m23_knytt_part` er en definer som tar tenanten som PARAMETER og
       SKRIVER. `fordring` har `m23_sveip_tenantliste`, som åpner hele
       tabellen for eierrollen NÅR KONTEKSTEN ER TOM — så en kaller som
       lot være å sette kontekst, kunne knyttet en annen tenants krav.
       Samme feil som lesedørene i 183 hadde.

    2. Referansen var et PARAMETER. Et krav kunne dermed pekes på en
       kunde som ikke er kravets egen `kunde_ref`, og raden ville sagt to
       forskjellige ting om hvem den gjelder. Nå leses referansen fra
       raden, og uoverensstemmelsen er umulig å konstruere.
    """
    t1, t2 = _t(), _t()
    c = _kobling(DSN)
    try:
        _sett_kontekst(c, t1)
        c.execute("SELECT part_registrer(%s,'K-1','Egen AS',NULL,"
                  "'bedrift','kari')", (t1,))
        fid = _registrer(c, t1, "K-1", "F-7001")
        c.commit()
        # Feil tenant i parameteret NEKTES HØYT, ikke stille.
        _sett_kontekst(c, t2)
        with pytest.raises(psycopg.Error) as e:
            c.execute("SELECT m23_knytt_part(%s,%s)", (t1, fid))
        assert "tenantkontekst" in str(e.value), str(e.value)
        c.rollback()
        # Og med riktig kontekst virker den. FALSE er det RIKTIGE
        # svaret her: `K-1` sto i registeret da kravet ble registrert, så
        # koblingen skjedde alt i innsettingen — døra sier at den ikke
        # endret noe, ikke at den feilet.
        #
        # (`in (True, False)` sto her først. Den kan ikke feile, og
        # CodeRabbit fant den — tredje tomme påstand i denne økten.)
        _sett_kontekst(c, t1)
        assert c.execute("SELECT m23_knytt_part(%s,%s)",
                         (t1, fid)).fetchone()[0] is False
        # ...og raden ER knyttet, så porten ikke er grønn av en rad uten
        # kobling i det hele tatt.
        assert _fordringene(c, t1)["K-1"][2] is not None
        c.rollback()
    finally:
        c.close()


@pg
def test_fremmednoekkelen_hindrer_en_part_fra_en_annen_tenant():
    """Fremmednøkkelen er PÅ (tenant, part_id) — ikke på part_id alene.
    Uten tenanten i nøkkelen kunne en fordring pekt på en ANNEN tenants
    kunde, og RLS ville skjult det i stedet for å stoppe det."""
    t1, t2 = _t(), _t()
    c, m = _kobling(DSN), _kobling(MIGRATOR_DSN)
    try:
        _sett_kontekst(c, t2)
        fremmed = c.execute("SELECT part_registrer(%s,'K-9','Fremmed AS',"
                            "NULL,'bedrift','kari')", (t2,)).fetchone()[0]
        c.commit()
        _sett_kontekst(c, t1)
        fid = _registrer(c, t1, "K-1", "F-6001")
        c.commit()
        _sett_kontekst(m, t1)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            m.execute("UPDATE fordring SET part_id=%s WHERE tenant=%s"
                      "  AND fordring_id=%s", (fremmed, t1, fid))
        m.rollback()
    finally:
        c.close(); m.close()
