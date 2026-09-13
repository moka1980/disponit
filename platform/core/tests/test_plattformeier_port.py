"""199: plattformeieren ser og administrerer alle firmaer — og ingen andre.

MÅLT FØR DETTE: ingen kunne det. `firma` har FORCE RLS med
`tenant_isolasjon`, og hver dør i 190 kaller `krev_tenantkontekst`. Eieren av
plattformen hadde ingen vei inn uten å gå direkte på basen som migrator.

DET FARLIGE ER IKKE SYNET, DET ER HVEM SOM FÅR DET.
Rollen `disponit_plattform_eier` gir dørene kryss-tenant-syn. Fullmakten er
en HELT annen ting: en rad i `plattformeier`. Faller den sjekken, er enhver
innlogget bruker plattformeier — derfor måler porten den fra begge sider.
"""
import secrets

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo  # noqa: F401

pg = pytest.mark.skipif(not (DSN and MIGRATOR_DSN),
                        reason="test-DSN ikke satt")


def _runtime():
    """Doerene er grantet til RUNTIME, ikke til migrator.

    Foerste utkast kalte dem som migrator og fikk «permission denied for
    function» — og avvisningstesten ble GROENN av feil grunn: den ventet
    `InsufficientPrivilege`, og fikk det, men fra rettighetssystemet i
    stedet for fra fullmaktssjekken. Den ville blitt staaende groenn selv om
    `plattform_krev_eier` var fjernet fra hver doer. Derfor gaar porten naa
    samme doer som verten, og assertene leser MELDINGEN.
    """
    from db.pg import koble
    return koble(DSN)


def _som(conn, bruker_id):
    """Setter `disponit.aktor` slik API-et gjoer det: `bruker:<bid>` fra
    SESJONEN. Doerene binder `p_bruker_id` til nettopp denne (038s form),
    saa uten dette er parameteret ingen identitet."""
    conn.execute("SELECT set_config('disponit.aktor',%s,true)",
                 ("bruker:" + bruker_id,))


def _ctx(conn, tenant):
    conn.execute("SELECT set_config('disponit.tenant',%s,true),"
                 "       set_config('disponit.aktor','test',true)", (tenant,))


@pytest.fixture
def eier(migrator):  # noqa: F811
    """En plattformeier som finnes bare i denne testen.

    Unik per kjøring: en fast id ville gjort kjøring nr. 2 avhengig av at
    kjøring nr. 1 ryddet.
    """
    bid = "eier-" + secrets.token_hex(6)
    migrator.execute(
        "INSERT INTO plattformeier (bruker_id, opprettet_av)"
        " VALUES (%s,'port')", (bid,))
    migrator.commit()
    yield bid
    migrator.execute("DELETE FROM plattformeier WHERE bruker_id=%s", (bid,))
    migrator.commit()


@pytest.fixture
def to_firmaer(migrator):  # noqa: F811
    """To firmaer i to ULIKE tenanter — poenget er nettopp på tvers."""
    merke = secrets.token_hex(4)
    navn = [f"p199-{merke}-a", f"p199-{merke}-b"]
    for t in navn:
        _ctx(migrator, t)
        migrator.execute(
            "INSERT INTO firma (tenant,navn,status,prove_utloper,endret_av)"
            " VALUES (%s,%s,'prove',current_date + 30,'port')", (t, t))
    migrator.commit()
    yield navn
    for t in navn:
        _ctx(migrator, t)
        migrator.execute("DELETE FROM firma WHERE tenant=%s", (t,))
    migrator.commit()


# ---------------------------------------------------------------------------
# Fullmakten
# ---------------------------------------------------------------------------

@pg
def test_en_som_ikke_er_eier_blir_avvist_av_hver_dor(migrator, to_firmaer):  # noqa: F811
    """SELVE VERNET, målt på ALLE dørene — ikke bare den første.

    MUTASJON SOM FELLER: fjern `plattform_krev_eier`-kallet fra én dør.
    En port som bare måler lesedøra ville da blitt grønn mens skrivedøra sto
    åpen for enhver innlogget bruker.
    """
    import psycopg

    fremmed = "ikke-eier-" + secrets.token_hex(4)
    kall = [
        ("SELECT * FROM plattform_firmaliste(%s)", (fremmed,)),
        ("SELECT plattform_firma_opprett(%s,%s,'N',NULL,30,'a')",
         (fremmed, "p199-" + secrets.token_hex(4))),
        ("SELECT plattform_firma_oppdater(%s,%s,'N',NULL,'a')",
         (fremmed, to_firmaer[0])),
        ("SELECT plattform_firma_status(%s,%s,'aktiv','a')",
         (fremmed, to_firmaer[0])),
    ]
    with _runtime() as c:
        # Hun er en EKTE innlogget bruker — aktoerkonteksten er hennes egen.
        # Det er nettopp slik en vanlig kunde naar doera, og det er DEN
        # avvisningen vi vil maale: medlemskapet, ikke bindingen.
        _som(c, fremmed)
        for sql, args in kall:
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as ei:
                with c.transaction():
                    c.execute(sql, args)
            # MELDINGEN, ikke bare typen. `InsufficientPrivilege` er ogsaa
            # det rettighetssystemet svarer paa en ugrantet funksjon, og da
            # ville porten vaert groenn uten aa maale fullmakten i det hele
            # tatt.
            assert "ikke plattformeier" in str(ei.value), \
                f"avvist av feil grunn: {ei.value}"


@pg
def test_en_ekte_eiers_id_hjelper_ikke_uten_aktorkonteksten(migrator, eier,  # noqa: F811
                                                            to_firmaer):
    """PARAMETERET ALENE ER INGEN IDENTITET.

    Her sendes en VIRKELIG plattformeiers bruker-id, men kallerens
    `disponit.aktor` er en annen. Første form slo bare opp id-en i tabellen,
    og da var enhver som nådde døra plattformeier — hun trengte bare å
    kjenne en ekte eiers id.

    Dette er 038s form: «definer-veiene binder tenanten til KONTEKSTEN,
    aldri til parameteret alene.»

    MUTASJON SOM FELLER: fjern aktør-sammenligningen i `plattform_krev_eier`.
    """
    import psycopg

    with _runtime() as c:
        c.execute("SELECT set_config('disponit.aktor','bruker:noen-annen',true)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as ei:
            with c.transaction():
                c.execute("SELECT * FROM plattform_firmaliste(%s)", (eier,))
        assert "aktørkontekst" in str(ei.value), \
            f"avvist av feil grunn: {ei.value}"


@pg
def test_konteksten_legges_tilbake_etter_et_kall(migrator, eier, to_firmaer):  # noqa: F811
    """`set_config(...,true)` er TRANSAKSJONSLOKAL, ikke kall-lokal.

    Uten gjenopprettingen sto kalleren igjen i målfirmaets tenant resten av
    transaksjonen, og neste spørring leste feil tenants rader — stille.

    MUTASJON SOM FELLER: fjern `set_config`-gjenopprettingen i døra.
    """
    min_tenant = to_firmaer[0]
    with _runtime() as c:
        _ctx(c, min_tenant); _som(c, eier)
        c.execute("SELECT plattform_firma_oppdater(%s,%s,%s,NULL,'eier')",
                  (eier, to_firmaer[1], "Endret"))
        etter = c.execute(
            "SELECT current_setting('disponit.tenant', true)").fetchone()[0]
        c.rollback()
    assert etter == min_tenant, (
        f"konteksten ble staaende paa {etter!r} i stedet for {min_tenant!r}")


@pg
def test_er_eier_er_ikke_et_orakel(migrator, eier):  # noqa: F811
    """«Er DENNE personen plattformeier?» skal ingen kunne spørre om.

    Første form tok en vilkårlig id og svarte ja/nei — altså en vei til å
    ramse opp eierne, én gjetning om gangen. Det er nøyaktig det den ENE
    feilkoden i `plattform_krev_eier` finnes for å hindre.

    MUTASJON SOM FELLER: fjern aktoerbindingen i `plattform_er_eier`.
    """
    with _runtime() as c:
        _som(c, "noen-helt-annen")
        # `eier` ER plattformeier — men ikke den som spør.
        assert c.execute("SELECT plattform_er_eier(%s)",
                         (eier,)).fetchone()[0] is False, \
            "doera svarte paa en ANNENS vegne"
        _som(c, eier)
        assert c.execute("SELECT plattform_er_eier(%s)",
                         (eier,)).fetchone()[0] is True, \
            "hun skal kunne spoerre om seg selv"


@pg
def test_tabellen_starter_tom_i_en_fersk_base(migrator):  # noqa: F811
    """Systemet kommer med NULL plattformeiere.

    Migrasjonen skriver ingen rad. Et fabrikkoppsett med en innebygget
    superbruker er en bakdør uansett hvor godt den er ment — og den ville
    vært usynlig her hvis porten bare talte «minst én».

    MUTASJON SOM FELLER: legg en INSERT i migrasjonen.
    """
    n = migrator.execute(
        "SELECT count(*) FROM plattformeier WHERE opprettet_av <> 'port'"
    ).fetchone()[0]
    assert n == 0, ("migrasjonen skal ikke skrive en eneste eier — "
                    f"fant {n}")


# ---------------------------------------------------------------------------
# Synet
# ---------------------------------------------------------------------------

@pg
def test_eieren_ser_firmaer_i_ALLE_tenanter(migrator, eier, to_firmaer):  # noqa: F811
    """MUTASJON SOM FELLER: fjern policyen `plattform_eier_ser_alle`."""
    with _runtime() as c:
        _som(c, eier)
        sett = {r[0] for r in c.execute(
            "SELECT tenant FROM plattform_firmaliste(%s)", (eier,)).fetchall()}
    for t in to_firmaer:
        assert t in sett, f"{t} manglet i plattformlisten"


@pg
def test_tenantkontekst_paavirker_ikke_listen(migrator, eier, to_firmaer):  # noqa: F811
    """Står kalleren i ÉN tenant, skal hun likevel se alle.

    Uten dette kunne porten over vært grønn fordi migrator tilfeldigvis
    hadde riktig kontekst fra en tidligere setning.
    """
    with _runtime() as c:
        _ctx(c, to_firmaer[0]); _som(c, eier)
        sett = {r[0] for r in c.execute(
            "SELECT tenant FROM plattform_firmaliste(%s)", (eier,)).fetchall()}
    assert to_firmaer[1] in sett, \
        "listen skal ikke være begrenset av kallerens egen tenantkontekst"


# ---------------------------------------------------------------------------
# Skrivingene går gjennom 190s dører — logikken er ikke skrevet om
# ---------------------------------------------------------------------------

@pg
def test_opprettelsen_gir_et_firma_i_prove(migrator, eier):  # noqa: F811
    t = "p199-" + secrets.token_hex(5)
    try:
        with _runtime() as c:
            _som(c, eier)
            frist = c.execute(
                "SELECT plattform_firma_opprett(%s,%s,%s,NULL,30,'eier')",
                (eier, t, "Nytt AS")).fetchone()[0]
            c.commit()
            # `commit()` kaster den transaksjonslokale aktoerkonteksten —
            # samme felle som tenantkonteksten. Uten denne linjen falt
            # porten paa bindingen i stedet for aa maale opprettelsen.
            _som(c, eier)
            assert frist is not None, "prøvefristen skal komme tilbake"
            rad = c.execute(
                "SELECT navn, status FROM plattform_firmaliste(%s)"
                " WHERE tenant=%s", (eier, t)).fetchone()
        assert rad == ("Nytt AS", "prove")
    finally:
        _ctx(migrator, t)
        migrator.execute("DELETE FROM firma WHERE tenant=%s", (t,))
        migrator.commit()


@pg
def test_en_ULOVLIG_statusovergang_avvises_fortsatt(migrator, eier, to_firmaer):  # noqa: F811
    """190s statusmaskin skal fortsatt eie dommen.

    `prove → prove` står ikke i tabellen over lovlige overganger. Gikk den
    gjennom her, ville plattformdøra ha vært en KOPI av maskinen i stedet
    for en kaller av den — og en kopi drifter fra originalen.

    MUTASJON SOM FELLER: la `plattform_firma_status` skrive `firma` direkte.
    """
    import psycopg

    with _runtime() as c:
        _som(c, eier)
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
            with c.transaction():
                c.execute("SELECT plattform_firma_status(%s,%s,'prove','eier')",
                          (eier, to_firmaer[0]))


@pg
def test_stenging_og_gjenaapning_virker(migrator, eier, to_firmaer):  # noqa: F811
    """«Slette» er `stengt`, ikke DELETE — angrefristen skal bestå."""
    t = to_firmaer[0]
    with _runtime() as c:
        _som(c, eier)
        c.execute("SELECT plattform_firma_status(%s,%s,'stengt','eier')",
                  (eier, t))
        c.commit()
        _som(c, eier)                      # commit kastet aktoerkonteksten
        assert c.execute(
            "SELECT status FROM plattform_firmaliste(%s) WHERE tenant=%s",
            (eier, t)).fetchone()[0] == "stengt"
        # Raden finnes fortsatt — det er hele poenget med angrefristen.
        c.execute("SELECT plattform_firma_status(%s,%s,'aktiv','eier')",
                  (eier, t))
        c.commit()
        _som(c, eier)
        assert c.execute(
            "SELECT status FROM plattform_firmaliste(%s) WHERE tenant=%s",
            (eier, t)).fetchone()[0] == "aktiv"


# ---------------------------------------------------------------------------
# ACL-ene. 190 lærte at REVOKE etter RESET ROLE bare gir en WARNING.
# ---------------------------------------------------------------------------

@pg
def test_ingen_av_dorene_er_apne_for_PUBLIC(migrator):  # noqa: F811
    """Måler `proacl` DIREKTE, ikke bare at navngitte roller mangler.

    En funksjon med `=X` i ACL-en kan kalles av enhver rolle i basen. I 190
    ble nøyaktig det målt etter at REVOKE sto ETTER `RESET ROLE`.

    MUTASJON SOM FELLER: flytt REVOKE-ene ned under `RESET ROLE`.
    """
    # `proacl IS NULL` betyr STANDARDRETTIGHETENE, og for en funksjon er
    # standarden `EXECUTE TO PUBLIC` (CodeRabbit). `unnest(NULL)` gir null
    # rader, så første form var grønn på nøyaktig den tilstanden porten
    # finnes for å fange: fjernes både REVOKE og GRANT, står ACL-en NULL og
    # hele basen kan kalle døra.
    apne = migrator.execute(
        "SELECT p.proname, coalesce(p.proacl::text,'NULL = PUBLIC')"
        "  FROM pg_proc p"
        " WHERE p.proname LIKE 'plattform\\_%'"
        "   AND (p.proacl IS NULL"
        "        OR EXISTS (SELECT 1 FROM unnest(p.proacl) a"
        "                    WHERE a::text LIKE '=%'))").fetchall()
    assert apne == [], f"disse er åpne for PUBLIC: {apne}"


@pg
def test_runtime_far_ikke_rore_fullmaktstabellen(miljo):  # noqa: F811
    """Runtime skal ikke kunne lese — og enda mindre skrive — `plattformeier`.

    Kunne den skrive, ville en hvilken som helst SQL-feil i en kundevei vært
    en vei til å gjøre seg selv til plattformeier.

    KJØRES SOM `disponit`, ikke som migrator: dørene er grantet til runtime,
    og en test som går migrator-veien måler feil rolle.
    """
    import psycopg

    from db.pg import koble

    with koble(DSN) as c:
        for sql in ("SELECT count(*) FROM plattformeier",
                    "INSERT INTO plattformeier (bruker_id, opprettet_av)"
                    " VALUES ('smugler','x')"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with c.transaction():
                    c.execute(sql)


@pg
def test_runtime_far_ikke_kalle_det_interne_leddet(miljo):  # noqa: F811
    """`plattform_krev_eier` er et ledd, ikke en dør. Den er bevisst ugrantet."""
    import psycopg

    from db.pg import koble

    with koble(DSN) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():
                c.execute("SELECT plattform_krev_eier('x','y')")
