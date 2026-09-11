"""Porten for 188: backfillen som FAKTISK kobler.

187s backfill var en STILLE NULLOPERASJON. Løkka begynte med
`SELECT DISTINCT tenant FROM fordring` og kjørte som migrator med TOM
tenantkontekst; `fordring` har FORCE RLS, så tom kontekst betyr INGEN
rader. Migrasjonen gikk grønt og gjorde ingenting. Målt i PROD etter
deploy: seks fordringer, null koblet.

OG 187S PORT FANGET DET IKKE, fordi den het «backfillen slår aldri
sammen» mens den målte REGISTRERINGSDØRA. Navnet lovet mer enn testen
gjorde — samme feilklasse som de tomme påstandene CodeRabbit fant tre
ganger i denne økten.

Denne porten kjører backfillens EGEN logikk mot rader som alt finnes, og
måler utfallet: hver fordring har en part, ingen referanser er slått
sammen, og en ny kjøring gjør ingenting.
"""
import secrets

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo, pg  # noqa: F401
from .test_m37 import _sett_kontekst


def _kobling(dsn):
    from db.pg import koble
    return koble(dsn)


def _t():
    return "t-bf-" + secrets.token_hex(3)


def _backfill(m):
    """Kjører 188s logikk på nytt, ordrett i form."""
    m.execute("CREATE TEMP TABLE _bf ON COMMIT DROP AS"
              " SELECT DISTINCT tenant, kunde_ref FROM public.fordring"
              "  WHERE false")
    m.execute("GRANT INSERT ON _bf TO disponit_fordring_eier")
    m.execute("""
        DO $$
        BEGIN
            PERFORM set_config('disponit.tenant', '', true);
            SET LOCAL ROLE disponit_fordring_eier;
            INSERT INTO _bf
            SELECT DISTINCT f.tenant, f.kunde_ref FROM public.fordring f
             WHERE f.part_id IS NULL;
            RESET ROLE;
        END $$;""")
    m.execute("""
        DO $$
        DECLARE t TEXT; r RECORD; v_id UUID;
        BEGIN
            FOR t IN SELECT DISTINCT tenant FROM _bf LOOP
                PERFORM set_config('disponit.tenant', t, true);
                FOR r IN SELECT kunde_ref FROM _bf WHERE tenant = t LOOP
                    SELECT part_id INTO v_id FROM public.part
                     WHERE tenant = t AND part_ref = r.kunde_ref;
                    IF v_id IS NULL THEN
                        v_id := public.gen_random_uuid();
                        INSERT INTO public.part (tenant, part_id, part_ref,
                                                 navn, opprettet_av)
                        VALUES (t, v_id, r.kunde_ref, r.kunde_ref, 'bf-test');
                    END IF;
                    UPDATE public.fordring SET part_id = v_id
                     WHERE tenant = t AND kunde_ref = r.kunde_ref
                       AND part_id IS NULL;
                END LOOP;
            END LOOP;
            PERFORM set_config('disponit.tenant', '', true);
        END $$;""")


@pg
def test_backfillen_kobler_rader_som_alt_fantes(migrator):
    """DEN OPPRINNELIGE FEILEN, festet: rader som fantes FØR koblingen
    skal bli koblet. 187 lot dem ligge, og ingenting sa fra."""
    t = _t()
    m = _kobling(MIGRATOR_DSN)
    try:
        # Tre fordringer skrevet DIREKTE, altså som rader fra «før»:
        # to på samme kunde, én på en annen.
        _sett_kontekst(m, t)
        for ref, nr in (("Havnegata Eiendom AS", "F-1"),
                        ("Havnegata Eiendom AS", "F-2"),
                        ("kunde-nordbyen", "F-3")):
            m.execute(
                "INSERT INTO fordring (tenant, fordring_id, kunde_ref,"
                " fakturanummer, belop_ore, utstedt, forfall, opprettet_av)"
                " VALUES (%s, gen_random_uuid(), %s, %s, 100000,"
                "         current_date-30, current_date-5, 'fixture')",
                (t, ref, nr))
        m.commit()
        _sett_kontekst(m, t)
        assert m.execute("SELECT count(*) FROM fordring WHERE tenant=%s"
                         "   AND part_id IS NULL", (t,)).fetchone()[0] == 3
        m.commit()

        _backfill(m)

        _sett_kontekst(m, t)
        ukoblet = m.execute("SELECT count(*) FROM fordring WHERE tenant=%s"
                            "   AND part_id IS NULL", (t,)).fetchone()[0]
        assert ukoblet == 0, f"{ukoblet} fordringer sto igjen ukoblet"
        # TO REFERANSER → TO PARTER. Aldri en sammenslåing.
        parter = m.execute("SELECT count(DISTINCT part_id) FROM fordring"
                           " WHERE tenant=%s", (t,)).fetchone()[0]
        assert parter == 2, parter
        # ...og de to radene på SAMME referanse deler part.
        delt = m.execute(
            "SELECT count(DISTINCT part_id) FROM fordring"
            " WHERE tenant=%s AND kunde_ref='Havnegata Eiendom AS'",
            (t,)).fetchone()[0]
        assert delt == 1
        m.rollback()
    finally:
        m.close()


@pg
def test_en_ny_kjoering_gjoer_ingenting(migrator):
    """Backfillen skal kunne kjøres igjen uten å lage tvillinger — en
    migrasjon kjøres om igjen på en fersk base hver eneste CI-runde."""
    t = _t()
    m = _kobling(MIGRATOR_DSN)
    try:
        _sett_kontekst(m, t)
        m.execute(
            "INSERT INTO fordring (tenant, fordring_id, kunde_ref,"
            " fakturanummer, belop_ore, utstedt, forfall, opprettet_av)"
            " VALUES (%s, gen_random_uuid(), 'K-1', 'F-9', 100000,"
            "         current_date-30, current_date-5, 'fixture')", (t,))
        m.commit()
        _backfill(m)
        _sett_kontekst(m, t)
        forste = m.execute("SELECT part_id FROM fordring WHERE tenant=%s",
                           (t,)).fetchone()[0]
        m.commit()
        _backfill(m)
        _sett_kontekst(m, t)
        assert m.execute("SELECT part_id FROM fordring WHERE tenant=%s",
                         (t,)).fetchone()[0] == forste
        assert m.execute("SELECT count(*) FROM part WHERE tenant=%s",
                         (t,)).fetchone()[0] == 1
        m.rollback()
    finally:
        m.close()


@pg
def test_tom_kontekst_gir_INGEN_rader_og_det_er_hele_fellen(migrator):
    """FELLEN SELV, målt. `fordring` har FORCE RLS: migrator med TOM
    kontekst ser NULL rader — ikke alle. Det er fail-closed og riktig,
    men det gjør en backfill som begynner med `SELECT DISTINCT tenant`
    til en stille nulloperasjon.

    `disponit_fordring_eier` ser dem, fordi `m23_sveip_tenantliste`
    åpner tabellen for NØYAKTIG den rollen når konteksten er tom. Porten
    måler begge halvdeler — uten den første er det ingen felle å unngå.
    """
    t = _t()
    m = _kobling(MIGRATOR_DSN)
    try:
        _sett_kontekst(m, t)
        m.execute(
            "INSERT INTO fordring (tenant, fordring_id, kunde_ref,"
            " fakturanummer, belop_ore, utstedt, forfall, opprettet_av)"
            " VALUES (%s, gen_random_uuid(), 'K-felle', 'F-felle', 100000,"
            "         current_date-30, current_date-5, 'fixture')", (t,))
        m.commit()
        # MIGRATOR UTEN KONTEKST: ingenting.
        m.execute("SELECT set_config('disponit.tenant','',true)")
        assert m.execute("SELECT count(*) FROM fordring").fetchone()[0] == 0
        # FORDRINGSEIEREN UTEN KONTEKST: alt.
        m.execute("SET LOCAL ROLE disponit_fordring_eier")
        n = m.execute("SELECT count(*) FROM fordring WHERE tenant=%s",
                      (t,)).fetchone()[0]
        m.execute("RESET ROLE")
        assert n == 1, "sveipepolicyen åpner ikke lenger for eierrollen"
        m.rollback()
    finally:
        m.close()
