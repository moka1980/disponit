"""041 — overtakelsessaken: Codex-portene fra klarsignalet (§7).

Hver test konstruerer sin egen tilstand. Portene 13–28 kjøres som
TABELLEIER med direkte DML/funksjonskall — ikke gjennom
`sikre_overtakelsessak()` — slik klarsignalet krever: kontrakten skal
holde mot den sterkeste skriveren, ikke bare mot den pene veien.

Portoversikt → test (numrene er klarsignalets):
  1        test_port1_konflikt_gir_sak_ved_commit
  2        test_port2_direkte_avklaring_uten_sak_avvises
  3        test_port3_apen_sak_feil_utfordrer_eller_generasjon
  5        test_port5_terminal_sak_ny_konflikt_ny_sak
  §20      test_pre041_konflikt_uten_sak_far_sak,
           test_pre041_python_sak_arkivmerkes_med_plattformsaken (Codex P1),
           test_pre041_forbigatte_utfordrere_degraderes_ikke_syklet (Codex P1)
  §13      test_andre_overtakelse_varsler_pa_nytt (Codex P2)
  §2       test_kundeeid_overtakelsessak_avvises (Codex P2)
  §7       test_konfliktidentiteten_kan_ikke_endres_uten_at_vakten_fyrer
           (Codex P2)
  6        (test_pr015_operativt_lag::test_port20_abc — skiftet, samme id)
  7–9      test_port7_8_9_revisjonsbindingen
  §6/§11   test_avgjort_sak_har_frosset_lineage (Codex P2)
  12       test_port12_insert_uten_sakskilde_feiler
  13–18    test_port13_18_referansepayloadens_lukkede_kontrakt
  17/20    test_port17_20_hostnameparitet_db_og_python
  19       test_port19_avvisning_navngir_constraint
  21–25    test_port21_25_lineage,
           test_lineagen_tilhorer_partene_saken_navngir (Codex P2)
  26–28    test_port26_28_totalitet
  29–31    test_port29_31_sak_og_logg
  32–36    test_port32_36_roller_og_synlighet
  §0       test_adjudikatorrollen_er_en_forutsetning_ikke_en_mulighet,
           test_adjudikatoren_har_lesretten_migrasjonen_lovet (Codex P1)
  §9.1     test_reservert_navnerom_er_stengt_for_runtime (Codex P2)
  37       test_port37_python_veien_er_stengt
  38       test_port38_payloadtyper_er_gjensidig_utelukkende
  41       test_port41_varselfeil_feller_ikke_saken
  40       (ui/test/adjudikator.test.js — axe på begge visninger)
"""
import secrets

import psycopg
import pytest

from .test_api import (ANNEN_TENANT, DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       migrator, miljo)
from .test_pr014b_domene_artefakt import _admin, _dkrow, _host
from .test_pr010_db import _ctx as _sett_kontekst

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

PLATT = "__plattform_domener"


def _konflikt(migrator, hostname, *, a=TENANT, b=ANNEN_TENANT):
    """A verifiserer, B tar over → konflikt. -> (sak_id, B-generasjon)."""
    adm = _admin()
    try:
        adm.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                    (a, hostname))
        adm.commit()
        svar = adm.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                           (b, hostname)).fetchone()[0]
        adm.commit()
    finally:
        adm.close()
    assert svar == f"konflikt:{a}", svar
    gen = _dkrow(migrator, b, hostname)[1]
    return _sak_for(migrator, hostname), gen


def _sak_for(migrator, hostname, *, terminal=False):
    _sett_kontekst(migrator, PLATT)
    rad = migrator.execute(
        "SELECT id FROM unntak WHERE hostname_ref=%s"
        "  AND sakskilde='domeneovertakelse' AND terminal=%s"
        " ORDER BY id DESC LIMIT 1", (hostname, terminal)).fetchone()
    migrator.rollback()
    return int(rad[0]) if rad else None


def _sakrad(migrator, sak_id):
    _sett_kontekst(migrator, PLATT)
    rad = migrator.execute(
        "SELECT status, utfordrer_tenant, tapt_tenant,"
        "       autorisasjonsgenerasjon, saksrevisjon, hostname_ref,"
        "       payload_type, loggpost_id"
        "  FROM unntak WHERE tenant=%s AND id=%s",
        (PLATT, sak_id)).fetchone()
    migrator.rollback()
    return rad


def _payload(h, *, gen=1, a=1, b=2, tapt=TENANT, utf=ANNEN_TENANT, **over):
    p = {"v": "1", "familie": "domeneovertakelse", "hostname": h,
         "autorisasjonsgenerasjon": gen, "tapt_tenant": tapt,
         "utfordrer_tenant": utf, "hendelse_a": a, "hendelse_b": b}
    p.update(over)
    return p


def _gyldig(conn, payload) -> bool:
    import json
    return conn.execute("SELECT er_gyldig_referansepayload(%s::jsonb)",
                        (json.dumps(payload),)).fetchone()[0]


# ---------------------------------------------------------------------------
# Sak ved konflikt (1–5)
# ---------------------------------------------------------------------------

@pg
def test_port1_konflikt_gir_sak_ved_commit(migrator):
    """Port 1: konflikten og saken er ÉN transaksjon — commit uten sak
    finnes ikke. Saken bor på plattformtenanten, bærer referansepayload og
    sin egen loggpost."""
    h = _host()
    sak, gen = _konflikt(migrator, h)
    rad = _sakrad(migrator, sak)
    assert rad[:7] == ("ny", ANNEN_TENANT, TENANT, gen, 0, h, "referanse"), rad
    # Loggposten er plattformens egen, med SAMME payload (invariant 6).
    _sett_kontekst(migrator, PLATT)
    logg = migrator.execute(
        "SELECT payload_type, referansepayload = ("
        "   SELECT referansepayload FROM unntak WHERE tenant=%s AND id=%s)"
        "  FROM revisjonslogg WHERE tenant=%s AND id=%s",
        (PLATT, sak, PLATT, rad[7])).fetchone()
    migrator.rollback()
    assert logg == ("referanse", True), logg


@pg
def test_port2_direkte_avklaring_uten_sak_avvises(migrator):
    """Port 2: direkte DML som setter `avklaring_kreves` uten gjeldende sak
    avvises VED COMMIT — også for skjemaeieren."""
    h = _host()
    adm = _admin()
    try:
        adm.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                    (TENANT, h))
        adm.commit()
    finally:
        adm.close()
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "UPDATE domenekontroll SET status='avklaring_kreves'"
        " WHERE tenant=%s AND hostname=%s", (TENANT, h))
    with pytest.raises(psycopg.errors.RaiseException, match="uten gjeldende sak"):
        migrator.commit()
    migrator.rollback()


@pg
def test_port3_apen_sak_feil_utfordrer_eller_generasjon(migrator):
    """Port 3: en åpen sak teller bare hvis den navngir RADENS utfordrer og
    generasjon — en fremmed eller foreldet sak slipper ingen gjennom."""
    h = _host()
    _konflikt(migrator, h)   # åpen sak: utfordrer=ANNEN_TENANT, gen=1
    tredje = "t-api-tredje"
    adm = _admin()
    try:
        adm.execute("SELECT utsted_challenge(%s,%s,false,'sys','tok')",
                    (tredje, h))
        adm.commit()
    except psycopg.Error:
        adm.rollback()
    finally:
        adm.close()
    # Direkte DML: sett TREDJE tenant i avklaring — saken gjelder ANNEN_TENANT.
    _sett_kontekst(migrator, tredje)
    migrator.execute(
        "INSERT INTO domenekontroll (tenant, hostname, status, wildcard,"
        " autorisasjonsgenerasjon) VALUES (%s,%s,'avklaring_kreves',false,1)"
        " ON CONFLICT (tenant, hostname) DO UPDATE SET status='avklaring_kreves'",
        (tredje, h))
    with pytest.raises(psycopg.errors.RaiseException, match="uten gjeldende sak"):
        migrator.commit()
    migrator.rollback()


@pg
def test_apen_sak_med_feil_motpart_teller_ikke(migrator):
    """Codex P2: MOTPARTEN er del av konfliktens identitet.

    Vakten sammenlignet hostname, utfordrer og generasjon — men ikke hvem
    tvisten står MOT. En rad som navngir A i `konflikt_motpart` kunne
    dermed passere på en åpen sak som navngir B som tapende part: køen
    ville vist B som forrige innehaver mens domenevedtaket gjaldt tvisten
    mot A, og evidensen og overgangen ville beskrevet hver sin tvist.

    Raden her er RADENS egen — samme utfordrer, samme generasjon — og det
    ENESTE avviket er motparten.
    """
    h = _host()
    _konflikt(migrator, h)   # åpen sak: utfordrer=ANNEN_TENANT, tapt=TENANT
    gen = _dkrow(migrator, ANNEN_TENANT, h)[1]
    _sett_kontekst(migrator, ANNEN_TENANT)
    migrator.execute(
        "UPDATE domenekontroll SET konflikt_motpart='t-api-tredje',"
        " status='avklaring_kreves'"
        " WHERE tenant=%s AND hostname=%s AND autorisasjonsgenerasjon=%s",
        (ANNEN_TENANT, h, gen))
    with pytest.raises(psycopg.errors.RaiseException, match="uten gjeldende sak"):
        migrator.commit()
    migrator.rollback()


@pg
def test_konfliktidentiteten_kan_ikke_endres_uten_at_vakten_fyrer(migrator):
    """Codex P2: vakten het `AFTER INSERT OR UPDATE OF status`, men
    predikatet hviler på fem kolonner.

    En rad som ALT står i `avklaring_kreves` kunne derfor få byttet
    motpart eller generasjon — uten at `status` ble nevnt — og committe en
    tilstand som ikke lenger svarer til sin egen åpne sak. Vakten fyrte
    ikke, og resultatet var den permanente låsen: attestasjonene avvises
    som foreldet, §7 fyrer ikke retroaktivt, og vaktbikkja kan bare telle
    den strandede konflikten.

    Begge halvdelene her rører KUN identitetskolonnen. Med kolonnelisten
    inne committet de stille.
    """
    h = _host()
    _konflikt(migrator, h)          # åpen sak: utfordrer=ANNEN_TENANT, gen=1
    gen = _dkrow(migrator, ANNEN_TENANT, h)[1]

    # Motparten byttes på en rad som står i avklaring fra før.
    _sett_kontekst(migrator, ANNEN_TENANT)
    migrator.execute(
        "UPDATE domenekontroll SET konflikt_motpart='t-api-tredje'"
        " WHERE tenant=%s AND hostname=%s", (ANNEN_TENANT, h))
    with pytest.raises(psycopg.errors.RaiseException,
                       match="uten gjeldende sak"):
        migrator.commit()
    migrator.rollback()

    # Generasjonen flyttes fremover (monoton, så statemaskinen slipper den
    # gjennom) — saken navngir fortsatt den gamle.
    _sett_kontekst(migrator, ANNEN_TENANT)
    migrator.execute(
        "UPDATE domenekontroll SET autorisasjonsgenerasjon=%s"
        " WHERE tenant=%s AND hostname=%s", (int(gen) + 1, ANNEN_TENANT, h))
    with pytest.raises(psycopg.errors.RaiseException,
                       match="uten gjeldende sak"):
        migrator.commit()
    migrator.rollback()


@pg
def test_sikre_overtakelsessak_er_idempotent_for_identisk_konflikt(migrator):
    """Codex P2: funksjonen heter `sikre`, ikke `skap`.

    En IDENTISK konflikt — samme vertsnavn, utfordrer, motpart og
    generasjon — skal gi SAMME sak, urørt. Uten det leddet falt kallet ned
    i skifte-grenen, som alltid gjør saksrevisjon+1; triggeren i §6 forbyr
    et hopp uten skifte, og reparasjonsveien felte seg selv. Verre:
    loggposten var da alt skrevet, så en avbrutt reparasjon etterlot en
    foreldreløs revisjonsloggrad.

    To operatørreparasjoner som plukker samme rad er nettopp det scenariet
    — og det er §20s egen vei.
    """
    h = _host()
    sak, gen = _konflikt(migrator, h)
    for_rad = _sakrad(migrator, sak)
    _sett_kontekst(migrator, PLATT)
    h_a, h_b = migrator.execute(
        "SELECT hendelse_a, hendelse_b FROM unntak WHERE tenant=%s AND id=%s",
        (PLATT, sak)).fetchone()
    logger_for = int(migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s",
        (PLATT,)).fetchone()[0])
    migrator.rollback()

    migrator.execute("SET LOCAL ROLE disponit_domene_eier")
    igjen = int(migrator.execute(
        "SELECT sikre_overtakelsessak(%s,%s,%s,%s,%s,%s,'test-idem','rid-idem')",
        (h, gen, TENANT, ANNEN_TENANT, h_a, h_b)).fetchone()[0])
    migrator.commit()

    assert igjen == sak, (sak, igjen)
    assert _sakrad(migrator, sak) == for_rad, "en no-op endret saken"
    _sett_kontekst(migrator, PLATT)
    logger_etter = int(migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s",
        (PLATT,)).fetchone()[0])
    migrator.rollback()
    assert logger_etter == logger_for, "en no-op skrev en foreldreløs loggpost"


@pg
def test_port5_terminal_sak_ny_konflikt_ny_sak(migrator):
    """Port 5: en terminal sak gjenåpnes aldri — en ny konflikt får en NY
    sak, og den terminale står urørt."""
    from .test_pr015_operativt_lag import _adjudikator
    h = _host()
    sak1, gen1 = _konflikt(migrator, h)
    bid = _adjudikator(ANNEN_TENANT, "port5-adjudikator")
    # Avgjør: avvis (én stemme holder) → saken lukkes, B tilbakekalles.
    adm = _admin()
    try:
        adm.execute("SELECT avgi_overtakelse_attestasjon(%s,%s,%s,'avvis',"
                    "%s,'aktor-1',%s,%s)",
                    (ANNEN_TENANT, sak1, h, TENANT, gen1, bid))
        adm.commit()
        # Ny konflikt på samme hostname: B (tilbakekalt m/ motpart) søker igjen.
        svar = adm.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                           (ANNEN_TENANT, h)).fetchone()[0]
        adm.commit()
        assert svar.startswith("konflikt:"), svar
    finally:
        adm.close()
    sak2 = _sak_for(migrator, h)
    assert sak2 is not None and sak2 != sak1, (sak1, sak2)
    assert _sakrad(migrator, sak1)[0] == "avvist", "terminal sak ble rørt"


@pg
def test_andre_overtakelse_varsler_pa_nytt(migrator):
    """Codex P2: `hendelse` er FOREKOMSTEN på ressursen, ikke arten.

    Med tekstnøkkelen som `hendelse` var nøkkelen i
    `varsel_en_per_hendelse` konstant per (bruker, vertsnavn), og
    `ON CONFLICT DO NOTHING` gjorde den ANDRE overtakelsen av samme
    vertsnavn til en stille no-op: avvist kandidat verifiserer på nytt,
    en helt ny konflikt oppstår — og ingen får beskjed. Verst for den som
    hadde lest det gamle varselet og altså ikke ser noe nytt.
    """
    from .test_pr015_operativt_lag import _adjudikator
    h = _host()
    # Medlemskapet MÅ finnes før konflikten: varselet skrives i samme
    # transaksjon som overtakelsen, til de brukerne som står der da.
    bid = _adjudikator(ANNEN_TENANT, "varselport-adj")
    sak1, gen1 = _konflikt(migrator, h)
    assert _varsler(migrator, ANNEN_TENANT, bid, h) == 1

    adm = _admin()
    try:
        # Avvis (én stemme holder) → B tilbakekalt, saken lukket.
        adm.execute("SELECT avgi_overtakelse_attestasjon(%s,%s,%s,'avvis',"
                    "%s,'aktor-varselport',%s,%s)",
                    (ANNEN_TENANT, sak1, h, TENANT, gen1, bid))
        adm.commit()
        # ... og B søker på nytt: en NY konflikt, ny generasjon, ny sak.
        svar = adm.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                           (ANNEN_TENANT, h)).fetchone()[0]
        adm.commit()
        assert svar.startswith("konflikt:"), svar
    finally:
        adm.close()

    assert _varsler(migrator, ANNEN_TENANT, bid, h) == 2, \
        "andre overtakelse ble slukt av varsel_en_per_hendelse"
    # De to radene skiller seg på FOREKOMSTEN, ikke på teksten: mottakeren
    # ser samme tekstnøkkel begge ganger, som hen skal.
    _sett_kontekst(migrator, ANNEN_TENANT)
    rader = migrator.execute(
        "SELECT hendelse, tekstnokkel FROM varsel WHERE tenant=%s"
        "   AND bruker_id=%s AND ressurs_id=%s ORDER BY hendelse",
        (ANNEN_TENANT, bid, h)).fetchall()
    migrator.rollback()
    assert len({r[0] for r in rader}) == 2, rader
    assert {r[1] for r in rader} == {"varsel.domene_avklaring"}, rader


def _hendelser(migrator, sak_id):
    _sett_kontekst(migrator, PLATT)
    r = migrator.execute(
        "SELECT hendelse_a, hendelse_b FROM unntak WHERE tenant=%s AND id=%s",
        (PLATT, sak_id)).fetchone()
    migrator.rollback()
    return r


def _varsler(migrator, tenant, bruker_id, hostname):
    _sett_kontekst(migrator, tenant)
    n = migrator.execute(
        "SELECT count(*) FROM varsel WHERE tenant=%s AND bruker_id=%s"
        "   AND art='domeneovertakelse' AND ressurs_type='domene'"
        "   AND ressurs_id=%s", (tenant, bruker_id, hostname)).fetchone()[0]
    migrator.rollback()
    return n


def _fjern_gjeldende_sak(migrator, sak):
    """Gjør saken terminal UTEN å røre domenekontroll-raden.

    Det er nøyaktig utrullingstilstanden §20 finnes for: konflikten står i
    `avklaring_kreves`, og det finnes ingen GJELDENDE sak. (Før 041 lå
    saken hos utfordreren og ble merket `policybrudd` av §1s backfill —
    sett fra §7-oppslaget og §9-policyen er de to tilstandene den samme:
    ingen rad med `sakskilde='domeneovertakelse' AND NOT terminal`.)
    To steg fordi statusmaskinen i 007 ikke har noen `ny`→`avvist`-kant.
    """
    _sett_kontekst(migrator, PLATT)
    for status in ("under_behandling", "avvist"):
        migrator.execute("UPDATE unntak SET status=%s WHERE tenant=%s AND id=%s",
                         (status, PLATT, sak))
    migrator.commit()


@pg
def test_pre041_konflikt_uten_sak_far_sak(migrator):
    """Codex P1: en pre-041-konflikt får sin sak i migrasjonen — ellers står
    den for alltid.

    §7s constraint-trigger fyrer bare på INSERT/UPDATE av `status`, altså
    ALDRI retroaktivt på en `avklaring_kreves`-rad som alt sto der. Den
    python-skapte saken raden eventuelt hadde, ble feid inn i `policybrudd`
    av §1s backfill (`oppdrag_id IS NULL`) og er dermed usynlig for både
    oppslaget og adjudikatorpolicyen. Uten §20 kunne ingen lage en ny (port
    37), og vaktbikkja kunne bare telle den blokkerte konflikten.
    """
    h = _host()
    sak, gen = _konflikt(migrator, h)
    _fjern_gjeldende_sak(migrator, sak)
    assert _sak_for(migrator, h) is None, "saken skulle være ute av bildet"
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "avklaring_kreves"

    adm = _admin()
    try:
        r = adm.execute(
            "SELECT migrer_pre041_overtakelseskonflikter('test041')"
        ).fetchone()[0]
        adm.commit()
    finally:
        adm.close()
    assert not [u for u in r["uten_sak"] if u["hostname"] == h], r
    ny = _sak_for(migrator, h)
    assert ny is not None and ny != sak, (sak, ny)
    # Saken er den samme formen den levende veien lager: plattformeid,
    # referansepayload, RADENS utfordrer og generasjon.
    rad = _sakrad(migrator, ny)
    assert rad[:7] == ("ny", ANNEN_TENANT, TENANT, gen, 0, h, "referanse"), rad
    # Lineagen peker på de SAMME to hendelsene den levende veien fant —
    # ikke på fabrikkerte. (Tenanten deres kan ikke leses herfra:
    # `domenekontroll_hendelse` har FORCE RLS og migrator står i
    # plattformtenantens kontekst. Identiteten er nok: de to id-ene ER
    # konflikten.)
    assert _hendelser(migrator, ny) == _hendelser(migrator, sak)
    assert None not in _hendelser(migrator, ny)

    # IDEMPOTENT: en ny kjøring rører ikke den saken den nettopp lagde.
    adm = _admin()
    try:
        r2 = adm.execute(
            "SELECT migrer_pre041_overtakelseskonflikter('test041')"
        ).fetchone()[0]
        adm.commit()
    finally:
        adm.close()
    assert not [u for u in r2["uten_sak"] if u["hostname"] == h], r2
    assert _sak_for(migrator, h) == ny
    assert _sakrad(migrator, ny)[4] == 0, "saksrevisjonen ble bumpet av en no-op"


@pg
def test_pre041_python_sak_arkivmerkes_med_plattformsaken(migrator):
    """Den gamle python-saken kan ikke flyttes (port 36) — men den skal
    ikke bli en anonym `policybrudd` heller.

    Gjenkjennelsen er radens EGEN kategori/handling + loggpostens kilde,
    altså nøyaktig det `opprett_overtakelsessak` skrev før 041. Historikken
    navngir plattformsaken som overtok, så den ene raden en operatør møter
    i unntakskøen forteller selv hvorfor den ikke kan avgjøres der.
    """
    import json
    h = _host()
    sak, _ = _konflikt(migrator, h)

    # Bygg en pre-041-formet sak hos UTFORDREREN, slik python-veien gjorde.
    _sett_kontekst(migrator, ANNEN_TENANT)
    # Ciphertext-raden bærer en ekte `key_id` (FK til tenant_nokler).
    # `aktiv=false` for å ikke kollidere med `en_aktiv_dek_per_tenant` —
    # fixturen skal etterlate tenantens levende nøkkel som den var.
    migrator.execute(
        "INSERT INTO tenant_nokler (tenant, key_id, wrapped_dek, aktiv)"
        " VALUES (%s,'k-pre041-fixtur','\\x00'::bytea,false)"
        " ON CONFLICT DO NOTHING", (ANNEN_TENANT,))
    nokkel = f"domeneovertakelse:{h}:1"
    logg = int(migrator.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'sys','domeneovertakelse','h','domeneovertakelse',"
        "         'UNNTAK','[]',%s) RETURNING id",
        (ANNEN_TENANT, nokkel)).fetchone()[0])
    gammel = int(migrator.execute(
        "INSERT INTO unntak (tenant, loggpost_id, handling, kategori,"
        " sakstype, prioritet, payload_kryptert, key_id, alg, nonce,"
        " maks_auto_forsok_snapshot, policy_versjon, policy_content_hash,"
        " payload_type, sakskilde)"
        " VALUES (%s,%s,'domene.overtakelse','domeneovertakelse','sikkerhet',"
        "         'hoy','\\x00'::bytea,'k-pre041-fixtur','AES-256-GCM',"
        "         '\\x00'::bytea,0,'<ukjent>','<ukjent>','kryptert',"
        "         'policybrudd')"
        " RETURNING id",
        (ANNEN_TENANT, logg)).fetchone()[0])
    migrator.commit()

    adm = _admin()
    try:
        adm.execute("SELECT migrer_pre041_overtakelseskonflikter('test041')")
        adm.commit()
    finally:
        adm.close()

    _sett_kontekst(migrator, ANNEN_TENANT)
    detalj = migrator.execute(
        "SELECT detalj FROM unntak_historikk WHERE tenant=%s AND unntak_id=%s"
        "   AND hendelse='overtakelsessak_migrert'",
        (ANNEN_TENANT, gammel)).fetchall()
    migrator.rollback()
    assert len(detalj) == 1, detalj
    d = detalj[0][0]
    d = json.loads(d) if isinstance(d, str) else d
    assert d["hostname"] == h and d["plattformsak"] == sak, d

    # ... OG DEN ER UTE AV KØEN (Codex P2). Et merke i historikken tar
    # ingen rad ut av en kø: raden er `sakstype='sikkerhet'`, `status='ny'`,
    # altså midt i kundens åpne sikkerhetskø — og etter 041 kan ingen
    # behandle den (adjudikasjonen krever `domeneovertakelse`-sakskilden,
    # python-veien er stengt av port 37). Uten lukkingen etterlot
    # oppgraderingen et permanent, uhåndterbart køelement hos kunden.
    _sett_kontekst(migrator, ANNEN_TENANT)
    st, terminal = migrator.execute(
        "SELECT status, terminal FROM unntak WHERE tenant=%s AND id=%s",
        (ANNEN_TENANT, gammel)).fetchone()
    migrator.rollback()
    assert (st, terminal) == ("avvist", True), \
        f"den gamle python-saken står fortsatt i kundens åpne kø: {st}"

    # Lukkingen er en STATUSOVERGANG, ikke en snarvei rundt statusmaskinen:
    # historikken bærer begge trinnene 003 krever.
    _sett_kontekst(migrator, ANNEN_TENANT)
    spor = [r[0] for r in migrator.execute(
        "SELECT til_status FROM unntak_historikk WHERE tenant=%s"
        "   AND unntak_id=%s AND hendelse IN ('claim','statusendring')"
        " ORDER BY id", (ANNEN_TENANT, gammel)).fetchall()]
    migrator.rollback()
    assert spor == ["under_behandling", "avvist"], spor

    # ... og ÉN gang: en ny kjøring skriver ikke historikken på nytt.
    adm = _admin()
    try:
        adm.execute("SELECT migrer_pre041_overtakelseskonflikter('test041')")
        adm.commit()
    finally:
        adm.close()
    _sett_kontekst(migrator, ANNEN_TENANT)
    antall = migrator.execute(
        "SELECT count(*) FROM unntak_historikk WHERE tenant=%s AND unntak_id=%s"
        "   AND hendelse='overtakelsessak_migrert'",
        (ANNEN_TENANT, gammel)).fetchone()[0]
    migrator.rollback()
    assert antall == 1, antall
    # ...og lukkingen står: en terminal rad plukkes ikke opp på nytt, så
    # statusmaskinen får aldri et andre `avvist`-forsøk å felle på.
    _sett_kontekst(migrator, ANNEN_TENANT)
    assert migrator.execute(
        "SELECT status FROM unntak WHERE tenant=%s AND id=%s",
        (ANNEN_TENANT, gammel)).fetchone()[0] == "avvist"
    migrator.rollback()


@pg
def test_pre041_forbigatte_utfordrere_degraderes_ikke_syklet(migrator):
    """Codex P1: flere `avklaring_kreves`-rader for ETT vertsnavn.

    Legacy-tilstanden 019 §3.2 ble laget for å stenge, men som gamle data
    kan bære: B OG C står begge i avklaring for samme hostnavn, og ingen
    av dem har en gjeldende sak. Den gamle migrasjonen gikk rad for rad og
    skrev den ENE saken på nytt for hver — resultatet var én sak hos den
    bindingen tilfeldigvis pekte på, og en B-rad som ble stående i
    `avklaring_kreves` for alltid: §7-vakten fyrer ikke retroaktivt, og
    `verifiser_domenekontroll` returnerer umiddelbart for den statusen.

    Fikset degraderer de forbigåtte gjennom den etablerte veien før saken
    lages. Porten måler BEGGE halvdeler: at B faktisk forlot avklaringen
    (med `forbigatt`-hendelsen), og at C — den bindingen peker på — sitter
    igjen med saken.
    """
    from .test_pr015_operativt_lag import TREDJE_TENANT
    from db.pg import koble

    h = _host()
    # Bygg legacy-tilstanden: degraderingstriggeren er nettopp den som
    # ikke fantes da radene ble til, så den legges ned mens de lages.
    eier = koble(MIGRATOR_DSN)
    try:
        eier.execute("ALTER TABLE hostname_binding DISABLE TRIGGER"
                     " hostname_binding_degrader_forbigatte")
        eier.commit()
        try:
            sak, _ = _konflikt(migrator, h)          # A → B
            adm = _admin()
            try:
                adm.execute(
                    "SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                    (TREDJE_TENANT, h))              # B → C (skifte, §10)
                adm.commit()
            finally:
                adm.close()
        finally:
            eier.execute("ALTER TABLE hostname_binding ENABLE TRIGGER"
                         " hostname_binding_degrader_forbigatte")
            eier.commit()
    finally:
        eier.close()

    # Uten triggeren ble B stående — dette ER legacy-formen.
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "avklaring_kreves"
    assert _dkrow(migrator, TREDJE_TENANT, h)[0] == "avklaring_kreves"
    _fjern_gjeldende_sak(migrator, sak)
    assert _sak_for(migrator, h) is None, "saken skulle være ute av bildet"

    adm = _admin()
    try:
        r = adm.execute(
            "SELECT migrer_pre041_overtakelseskonflikter('test041')"
        ).fetchone()[0]
        adm.commit()
    finally:
        adm.close()

    # Ingen rad ble hengende igjen som «kan ikke få sak».
    assert not [u for u in r["uten_sak"] if u["hostname"] == h], r
    assert r["forbigatte_degradert"] >= 1, r
    # B forlot avklaringen — og gjorde det gjennom den etablerte veien,
    # altså med hendelsen og generasjonsbumpen den skriver.
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "tilbakekalt"
    _sett_kontekst(migrator, ANNEN_TENANT)
    hend = migrator.execute(
        "SELECT count(*) FROM domenekontroll_hendelse WHERE tenant=%s"
        "   AND hostname=%s AND hendelse='forbigatt'"
        "   AND til_status='tilbakekalt'", (ANNEN_TENANT, h)).fetchone()[0]
    migrator.rollback()
    assert hend == 1, hend
    # C — den bindingen peker på — står igjen med avklaringen OG saken,
    # og saken navngir C som utfordrer med B som tapt part.
    assert _dkrow(migrator, TREDJE_TENANT, h)[0] == "avklaring_kreves"
    ny = _sak_for(migrator, h)
    assert ny is not None and ny != sak, (sak, ny)
    rad = _sakrad(migrator, ny)
    assert rad[1:3] == (TREDJE_TENANT, ANNEN_TENANT), rad
    assert None not in _hendelser(migrator, ny)


# ---------------------------------------------------------------------------
# Revisjonsbindingen (7–9)
# ---------------------------------------------------------------------------

@pg
def test_port7_8_9_revisjonsbindingen(migrator):
    """Port 7: skifte uten +1 avvises. Port 8: +2 avvises, og +1 uten
    skifte avvises. Port 9: `hostname_ref` er saksidentiteten og kan aldri
    endres. Alt som tabelleier — triggeren er vakten, ikke kalleren."""
    h = _host()
    sak, gen = _konflikt(migrator, h)

    def _oppdater(sql_sett, args=()):
        _sett_kontekst(migrator, PLATT)
        migrator.execute("SET CONSTRAINTS ALL IMMEDIATE")
        migrator.execute(
            f"UPDATE unntak SET {sql_sett} WHERE tenant=%s AND id=%s",
            (*args, PLATT, sak))

    # Port 7: utfordrer skifter uten revisjonsbump.
    with pytest.raises(psycopg.errors.RaiseException,
                       match="uten saksrevisjon"):
        _oppdater("utfordrer_tenant='t-api-tredje'")
    migrator.rollback()
    # Port 8a: skifte med +2.
    with pytest.raises(psycopg.errors.RaiseException,
                       match="uten saksrevisjon"):
        _oppdater("utfordrer_tenant='t-api-tredje', saksrevisjon=saksrevisjon+2")
    migrator.rollback()
    # Port 8b: +1 uten skifte.
    with pytest.raises(psycopg.errors.RaiseException,
                       match="uten utfordrer-/generasjonsskifte"):
        _oppdater("saksrevisjon=saksrevisjon+1")
    migrator.rollback()
    # Port 9: saksidentiteten.
    with pytest.raises(psycopg.errors.RaiseException,
                       match="saksidentitet"):
        _oppdater("hostname_ref=%s", (_host(),))
    migrator.rollback()


@pg
def test_avgjort_sak_har_frosset_lineage(migrator):
    """Codex P2: revisjonsbindingen slipper taket når saken blir terminal.

    Det er riktig for STATUSEN — en avgjort sak skifter ikke utfordrer —
    men kolonnene sto igjen ubeskyttet: de er 041s egne, og den kopierte
    kolonnelåsen (§11) kjenner dem ikke. Siden lineagespeilet (§5) bare
    krever at saken og loggposten er ENIGE, kunne en skriver bytte
    loggpost og skrive om hvem som utfordret, hvem som tapte, på hvilken
    generasjon og med hvilke hendelser — i takt, og uten at statusen
    flyttet seg. Beviset for en fire-øyne-avgjørelse må ikke kunne
    omskrives etter at avgjørelsen er tatt.

    Som tabelleier med constraintene IMMEDIATE: gjerdet skal holde mot den
    sterkeste skriveren, ikke bare mot den pene veien.
    """
    h = _host()
    sak, gen = _konflikt(migrator, h)
    _fjern_gjeldende_sak(migrator, sak)          # -> avvist, altså terminal

    def _oppdater(sql_sett, args=()):
        _sett_kontekst(migrator, PLATT)
        migrator.execute("SET CONSTRAINTS ALL IMMEDIATE")
        migrator.execute(
            f"UPDATE unntak SET {sql_sett} WHERE tenant=%s AND id=%s",
            (*args, PLATT, sak))

    for sett, args in (("utfordrer_tenant='t-api-tredje'", ()),
                       ("tapt_tenant='t-api-tredje'", ()),
                       ("autorisasjonsgenerasjon=autorisasjonsgenerasjon+1", ()),
                       ("saksrevisjon=saksrevisjon+1", ()),
                       ("hendelse_a=hendelse_b", ()),
                       ("referansepayload=NULL", ()),
                       ("hostname_ref=%s", (_host(),))):
        with pytest.raises(psycopg.errors.RaiseException,
                           match="lineagen er frosset"):
            _oppdater(sett, args)
        migrator.rollback()

    # `loggpost_id` tas av kolonnelåsen (§11) — den fyrer først. Unntaket
    # den hadde for `domeneovertakelse` gjelder nå bare mens saken er ÅPEN.
    with pytest.raises(psycopg.errors.RaiseException,
                       match="kun status/status_ts"):
        _oppdater("loggpost_id=loggpost_id+1")
    migrator.rollback()

    # ... og saken står urørt: ingen av forsøkene tok.
    assert _sakrad(migrator, sak)[:7] == (
        "avvist", ANNEN_TENANT, TENANT, gen, 0, h, "referanse")


# ---------------------------------------------------------------------------
# Migrering (12) og referansepayload (13–20)
# ---------------------------------------------------------------------------

@pg
def test_port12_insert_uten_sakskilde_feiler(migrator):
    """Port 12: ingen DEFAULT — en skriver som ikke VET hva saken er, får
    ikke skrevet den."""
    _sett_kontekst(migrator, TENANT)
    lid = migrator.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse) VALUES (%s,'h','p','STOPP','[]'::jsonb) RETURNING id",
        (TENANT,)).fetchone()[0]
    with pytest.raises(psycopg.errors.NotNullViolation):
        migrator.execute(
            "INSERT INTO unntak (tenant,loggpost_id,handling,kategori,"
            "payload_kryptert,key_id,nonce,maks_auto_forsok_snapshot,"
            "policy_versjon,policy_content_hash)"
            " VALUES (%s,%s,'x','over_grense',%s,'k1',%s,3,'1.0.0','ph')",
            (TENANT, lid, b"\x00", b"\x00" * 12))
    migrator.rollback()


@pg
def test_port13_18_referansepayloadens_lukkede_kontrakt(migrator):
    """Port 13: ekstra nøkkel avvises — også for tabelleieren. Port 14:
    manglende nøkkel / feil type / feil familie / manglende v. Port 15:
    generasjonens tallform (1.5 og -1 avvist, 0 godtatt). Port 16:
    hendelsenes tallform og ulikhet. Port 18: tapt == utfordrer avvises."""
    h = "gyldig.example"
    ok = _payload(h)
    assert _gyldig(migrator, ok) is True

    # Port 13 — lukket sett er LIKHET, ikke delmengde.
    for ekstra in ({"challenge_token": "x"}, {"begrunnelse": "y"},
                   {"fritekst": "z"}):
        assert _gyldig(migrator, _payload(h, **ekstra)) is False, ekstra
    # Port 14 — manglende nøkkel, feil type, feil familie, manglende v.
    for brukket in (
            {k: v for k, v in ok.items() if k != "hostname"},
            _payload(h, gen="1"),                       # tall som streng
            _payload(h, familie="noe_annet"),
            {k: v for k, v in ok.items() if k != "v"},
            _payload(h) | {"v": "2"}):
        assert _gyldig(migrator, brukket) is False, brukket
    # Port 15 — generasjonens domene.
    assert _gyldig(migrator, _payload(h, gen=1.5)) is False
    assert _gyldig(migrator, _payload(h, gen=-1)) is False
    assert _gyldig(migrator, _payload(h, gen=0)) is True
    assert _gyldig(migrator, _payload(h, gen=int("9" * 20))) is False
    # Port 16 — hendelsene: positive heltall, aldri like.
    assert _gyldig(migrator, _payload(h, a=0)) is False
    assert _gyldig(migrator, _payload(h, a=-3)) is False
    assert _gyldig(migrator, _payload(h, a=5, b=5)) is False
    # Port 18 — to sider av en konflikt er aldri samme tenant.
    assert _gyldig(migrator, _payload(h, tapt="x", utf="x")) is False
    migrator.rollback()

    # Codex P2: 19 sifre er ikke det samme som «innenfor BIGINT». Hele
    # intervallet over taket er 19-sifret, og den gamle lengdegrensen
    # slapp det gjennom — en loggpost med en generasjon eller en
    # hendelses-id ingen `bigint`-kolonne kan bære, og dermed evidens som
    # per konstruksjon aldri kan svare til en gyldig sak.
    maks = 9223372036854775807
    assert _gyldig(migrator, _payload(h, gen=maks)) is True
    assert _gyldig(migrator, _payload(h, gen=maks + 1)) is False
    assert _gyldig(migrator, _payload(h, gen=9999999999999999999)) is False
    assert _gyldig(migrator, _payload(h, a=maks, b=maks - 1)) is True
    assert _gyldig(migrator, _payload(h, a=maks + 1, b=1)) is False
    assert _gyldig(migrator, _payload(h, a=1, b=maks + 1)) is False
    # ...og grensen selv står der den skal, ikke ett siffer unna.
    for tekst, ventet in (("9223372036854775807", True),
                          ("9223372036854775808", False),
                          ("1000000000000000000", True),
                          ("999999999999999999", True)):
        assert migrator.execute("SELECT er_bigint_tekst(%s)",
                                (tekst,)).fetchone()[0] is ventet, tekst
    migrator.rollback()


@pg
def test_port17_20_hostnameparitet_db_og_python(migrator):
    """Port 17: de ugyldige hostname-formene avvises av DB-kontrakten.
    Port 20 (§5.2 gren 1): kontrakten er `er_kanonisk_hostname` (016),
    GJENBRUKT — python-siden (`ssrf.normaliser_hostname`) er en
    NORMALISATOR, ikke en validator, og pariteten som faktisk gjelder er:
    (a) alt DB godtar er et fikspunkt for python-normalisereren, og
    (b) det python normaliserer frem, dømmes av DB — normaliseringen kan
    aldri produsere en form DB feller uten at DB felte originalen også.
    """
    from api import ssrf

    ugyldige = [".example.com", "a..example.com", "-a.example.com",
                "a-.example.com", "example.com.", "EXAMPLE.com",
                ("a" * 64) + ".example.com"]
    for form in ugyldige:
        assert _gyldig(migrator, _payload(form)) is False, form
        assert migrator.execute("SELECT er_kanonisk_hostname(%s)",
                                (form,)).fetchone()[0] is False, form
    migrator.rollback()
    # (b) normaliseringens utfall dømmes av DB: formene som KAN
    # normaliseres til kanonisk («EXAMPLE.com», «example.com.») ender som
    # 'example.com' og godtas der; resten forblir ukanoniske og felles.
    for form, kanonisk_etterpaa in [("EXAMPLE.com", True),
                                    ("example.com.", True),
                                    (".example.com", False),
                                    ("a..example.com", False),
                                    ("-a.example.com", False)]:
        n = ssrf.normaliser_hostname(form)
        dom = migrator.execute("SELECT er_kanonisk_hostname(%s)",
                               (n,)).fetchone()[0]
        assert dom is kanonisk_etterpaa, (form, n, dom)
    migrator.rollback()
    # (a) DB-kanoniske former er python-fikspunkter.
    for form in ["example.com", "a.b.example.com", "xn--kltt-5qaa.no"]:
        assert migrator.execute("SELECT er_kanonisk_hostname(%s)",
                                (form,)).fetchone()[0] is True, form
        assert ssrf.normaliser_hostname(form) == form, form
    migrator.rollback()


@pg
def test_port19_avvisning_navngir_constraint(migrator):
    """Port 19: ugyldig tallform + speilingsbrudd gir CONSTRAINT-avvisning
    som NAVNGIR contraint-en — aldri en cast-exception fra dypet."""
    import json
    _sett_kontekst(migrator, PLATT)
    lid = migrator.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse) VALUES (%s,'h','p','STOPP','[]'::jsonb) RETURNING id",
        (PLATT,)).fetchone()[0]
    stygg = _payload("gyldig.example", gen="ikke-et-tall")
    with pytest.raises(psycopg.errors.CheckViolation) as ei:
        migrator.execute(
            "INSERT INTO unntak (tenant,loggpost_id,handling,kategori,"
            " sakstype,prioritet,sakskilde,hostname_ref,utfordrer_tenant,"
            " tapt_tenant,autorisasjonsgenerasjon,saksrevisjon,hendelse_a,"
            " hendelse_b,payload_type,referansepayload)"
            " VALUES (%s,%s,'domene.overtakelse','domeneovertakelse',"
            " 'sikkerhet','hoy','domeneovertakelse','gyldig.example',%s,%s,"
            " 1,0,1,2,'referanse',%s::jsonb)",
            (PLATT, lid, ANNEN_TENANT, TENANT, json.dumps(stygg)))
    assert "unntak_referansepayload_speiler" in str(ei.value)
    migrator.rollback()


@pg
def test_kundeeid_overtakelsessak_avvises(migrator):
    """Codex P2: EIERSKAPET er del av saksformen, ikke bare en konvensjon.

    Runtime- og arbeiderrollene beholder direkte INSERT på `unntak`. Uten
    `tenant='__plattform_domener'` i radkontrakten kunne en feilende eller
    kompromittert skriver plante en KUNDE-eid `domeneovertakelse`-rad —
    synlig for adjudikatorpolicyen (§9 gjerdet bare på sakskilden), og
    verre: okkupant på den unike åpen-sak-indeksen, slik at den ekte,
    atomiske overtakelsen feilet i det `sikre_overtakelsessak()` skulle
    lage plattformsaken. En sak ingen kunne avgjøre, plantet utenfra.

    Raden er ellers FULLVERDIG (ekte hendelser, speilende payload) — det
    ENESTE avviket er tenanten, og den skal alene felle innsettingen.
    """
    import json
    h = _host()
    sak, gen = _konflikt(migrator, h)
    _sett_kontekst(migrator, PLATT)
    h_a, h_b = migrator.execute(
        "SELECT hendelse_a, hendelse_b FROM unntak WHERE tenant=%s AND id=%s",
        (PLATT, sak)).fetchone()
    migrator.rollback()

    p = _payload(h, gen=gen, a=h_a, b=h_b)
    _sett_kontekst(migrator, ANNEN_TENANT)
    lid = migrator.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse,payload_type,referansepayload)"
        " VALUES (%s,'h','p','UNNTAK','[]'::jsonb,'referanse',%s::jsonb)"
        " RETURNING id", (ANNEN_TENANT, json.dumps(p))).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation) as ei:
        migrator.execute(
            "INSERT INTO unntak (tenant,loggpost_id,handling,kategori,"
            " sakstype,prioritet,sakskilde,hostname_ref,utfordrer_tenant,"
            " tapt_tenant,autorisasjonsgenerasjon,saksrevisjon,hendelse_a,"
            " hendelse_b,payload_type,referansepayload)"
            " VALUES (%s,%s,'domene.overtakelse','domeneovertakelse',"
            " 'sikkerhet','hoy','domeneovertakelse',%s,%s,%s,%s,0,%s,%s,"
            " 'referanse',%s::jsonb)",
            (ANNEN_TENANT, lid, h, ANNEN_TENANT, TENANT, gen, h_a, h_b,
             json.dumps(p)))
    assert "unntak_sakskilde_komplett" in str(ei.value), str(ei.value)
    migrator.rollback()

    # Den ekte plattformsaken står urørt — og er fortsatt DEN saken §7
    # regner som gjeldende.
    assert _sak_for(migrator, h) == sak


# ---------------------------------------------------------------------------
# Lineage (21–25) og totalitet (26–28)
# ---------------------------------------------------------------------------

@pg
def test_port21_25_lineage(migrator):
    """Port 21: FK avviser en hendelse som ikke finnes. Port 22: kompositt-
    FK-en avviser en hendelse for et ANNET hostname. Port 23: sakskilde-
    CHECK-en krever hendelser for overtakelse og forbyr dem for policybrudd.
    Port 25: en referert hendelse kan ikke slettes."""
    import json
    h = _host()
    sak, gen = _konflikt(migrator, h)

    # Port 21 — ikke-eksisterende hendelse (direkte DML, tabelleier).
    _sett_kontekst(migrator, PLATT)
    lid = migrator.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse,payload_type,referansepayload)"
        " VALUES (%s,'h','p','UNNTAK','[]'::jsonb,'referanse',%s::jsonb)"
        " RETURNING id",
        (PLATT, json.dumps(_payload("fri.example", a=999999991,
                                    b=999999992)))).fetchone()[0]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrator.execute(
            "INSERT INTO unntak (tenant,loggpost_id,handling,kategori,"
            " sakstype,prioritet,sakskilde,hostname_ref,utfordrer_tenant,"
            " tapt_tenant,autorisasjonsgenerasjon,saksrevisjon,hendelse_a,"
            " hendelse_b,payload_type,referansepayload)"
            " VALUES (%s,%s,'domene.overtakelse','domeneovertakelse',"
            " 'sikkerhet','hoy','domeneovertakelse','fri.example',%s,%s,1,0,"
            " 999999991,999999992,'referanse',%s::jsonb)",
            (PLATT, lid, ANNEN_TENANT, TENANT,
             json.dumps(_payload("fri.example", a=999999991, b=999999992))))
    migrator.rollback()

    # Port 22 — hendelse for et annet hostname: kompositt-FK-en (id, hostname)
    # matcher ikke, selv om id-en finnes. INSERT-form: payloaden speiler
    # kolonnene (speiler-CHECK-en passerer), så det er FK-en som feller.
    _sett_kontekst(migrator, PLATT)
    ekte_a, ekte_b = migrator.execute(
        "SELECT hendelse_a, hendelse_b FROM unntak WHERE tenant=%s AND id=%s",
        (PLATT, sak)).fetchone()
    migrator.rollback()
    fremmed = _payload("fri.example", a=int(ekte_a), b=int(ekte_b))
    _sett_kontekst(migrator, PLATT)
    lidf = migrator.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse,payload_type,referansepayload)"
        " VALUES (%s,'h','p','UNNTAK','[]'::jsonb,'referanse',%s::jsonb)"
        " RETURNING id", (PLATT, json.dumps(fremmed))).fetchone()[0]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrator.execute(
            "INSERT INTO unntak (tenant,loggpost_id,handling,kategori,"
            " sakstype,prioritet,sakskilde,hostname_ref,utfordrer_tenant,"
            " tapt_tenant,autorisasjonsgenerasjon,saksrevisjon,hendelse_a,"
            " hendelse_b,payload_type,referansepayload)"
            " VALUES (%s,%s,'domene.overtakelse','domeneovertakelse',"
            " 'sikkerhet','hoy','domeneovertakelse','fri.example',%s,%s,1,0,"
            " %s,%s,'referanse',%s::jsonb)",
            (PLATT, lidf, ANNEN_TENANT, TENANT, ekte_a, ekte_b,
             json.dumps(fremmed)))
    migrator.rollback()

    # Port 23 — policybrudd med hendelser avvises av sakskilde-CHECK-en.
    _sett_kontekst(migrator, TENANT)
    lid2 = migrator.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse) VALUES (%s,'h','p','STOPP','[]'::jsonb) RETURNING id",
        (TENANT,)).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation) as ei:
        migrator.execute(
            "INSERT INTO unntak (tenant,loggpost_id,handling,kategori,"
            "payload_kryptert,key_id,nonce,maks_auto_forsok_snapshot,"
            "policy_versjon,policy_content_hash,sakskilde,hendelse_a,"
            "hendelse_b) VALUES (%s,%s,'x','over_grense',%s,'k1',%s,3,"
            "'1.0.0','ph','policybrudd',%s,%s)",
            (TENANT, lid2, b"\x00", b"\x00" * 12, ekte_a, ekte_b))
    assert "unntak_sakskilde_komplett" in str(ei.value)
    migrator.rollback()

    # Port 25 — den refererte hendelsen kan ikke slettes (append-only-
    # triggeren er første vakt; FK-en står bak den).
    _sett_kontekst(migrator, ANNEN_TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("DELETE FROM domenekontroll_hendelse WHERE id=%s",
                         (ekte_b,))
    migrator.rollback()


@pg
def test_lineagen_tilhorer_partene_saken_navngir(migrator):
    """Codex P2: kompositt-FK-en bandt (id, hostname) — altså SAMME
    VERTSNAVN, ikke samme PART.

    Et vertsnavn som har vært omstridt har historikk for flere tenanter, og
    `sikre_overtakelsessak` tar hendelses-ID-ene som argumenter. En
    reparasjon eller en feilende kaller kunne dermed hekte to fullt gyldige
    hendelser fra parter saken ikke handler om — og alt annet ville sett
    riktig ut: speilingen tar ID-ene fra saken, loggpostbindingen krever
    bare at de to er enige. Evidenskjeden ville vært intakt og usann.

    Raden her er sakens egen konflikt med sidene BYTTET: hendelsene er ekte
    og gjelder vertsnavnet, men A-siden tilhører utfordreren og B-siden
    motparten. Statusen er terminal, så den unike åpen-sak-indeksen ikke
    feller raden før FK-en rekker det.
    """
    import json
    h = _host()
    sak, _ = _konflikt(migrator, h)          # tapt=TENANT, utfordrer=ANNEN
    _sett_kontekst(migrator, PLATT)
    ekte_a, ekte_b, gen = migrator.execute(
        "SELECT hendelse_a, hendelse_b, autorisasjonsgenerasjon"
        "  FROM unntak WHERE tenant=%s AND id=%s", (PLATT, sak)).fetchone()
    migrator.rollback()

    # Sidene byttet: payloaden speiler kolonnene (speiler-CHECKen passerer),
    # hendelsene finnes og gjelder h — det ENESTE avviket er hvem de tilhører.
    byttet = _payload(h, gen=int(gen), tapt=ANNEN_TENANT, utf=TENANT,
                      a=int(ekte_a), b=int(ekte_b))
    _sett_kontekst(migrator, PLATT)
    lid = migrator.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse,payload_type,referansepayload)"
        " VALUES (%s,'h','p','UNNTAK','[]'::jsonb,'referanse',%s::jsonb)"
        " RETURNING id", (PLATT, json.dumps(byttet))).fetchone()[0]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrator.execute(
            "INSERT INTO unntak (tenant,loggpost_id,handling,kategori,"
            " sakstype,prioritet,sakskilde,status,hostname_ref,"
            " utfordrer_tenant,tapt_tenant,autorisasjonsgenerasjon,"
            " saksrevisjon,hendelse_a,hendelse_b,payload_type,"
            " referansepayload)"
            " VALUES (%s,%s,'domene.overtakelse','domeneovertakelse',"
            " 'sikkerhet','hoy','domeneovertakelse','avvist',%s,%s,%s,%s,0,"
            " %s,%s,'referanse',%s::jsonb)",
            (PLATT, lid, h, TENANT, ANNEN_TENANT, gen, ekte_a, ekte_b,
             json.dumps(byttet)))
    migrator.rollback()

    # ...og den riktige veien rundt står: samme hendelser, riktige parter.
    riktig = _payload(h, gen=int(gen), tapt=TENANT, utf=ANNEN_TENANT,
                      a=int(ekte_a), b=int(ekte_b))
    _sett_kontekst(migrator, PLATT)
    lid2 = migrator.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse,payload_type,referansepayload)"
        " VALUES (%s,'h','p','UNNTAK','[]'::jsonb,'referanse',%s::jsonb)"
        " RETURNING id", (PLATT, json.dumps(riktig))).fetchone()[0]
    migrator.execute(
        "INSERT INTO unntak (tenant,loggpost_id,handling,kategori,"
        " sakstype,prioritet,sakskilde,status,hostname_ref,"
        " utfordrer_tenant,tapt_tenant,autorisasjonsgenerasjon,"
        " saksrevisjon,hendelse_a,hendelse_b,payload_type,referansepayload)"
        " VALUES (%s,%s,'domene.overtakelse','domeneovertakelse',"
        " 'sikkerhet','hoy','domeneovertakelse','avvist',%s,%s,%s,%s,0,"
        " %s,%s,'referanse',%s::jsonb)",
        (PLATT, lid2, h, ANNEN_TENANT, TENANT, gen, ekte_a, ekte_b,
         json.dumps(riktig)))
    migrator.rollback()


@pg
def test_port26_28_totalitet(migrator):
    """Port 26: hvert av de seks speilfeltene NULL → avvist, selv med gyldig
    payload. Port 27: `referansepayload_speiler` er total — aldri NULL, også
    med NULL i hver posisjon. Port 28: `er_gyldig_referansepayload(NULL)`
    er `false`, ikke NULL."""
    import json
    h = "gyldig.example"
    p = json.dumps(_payload(h))
    felter = ["p_hostname", "p_generasjon", "p_utfordrer", "p_tapt",
              "p_a", "p_b"]
    verdier = [h, 1, ANNEN_TENANT, TENANT, 1, 2]
    for i in range(len(felter)):
        args = list(verdier)
        args[i] = None
        rad = migrator.execute(
            "SELECT referansepayload_speiler(%s::jsonb,%s,%s,%s,%s,%s,%s)",
            (p, *args)).fetchone()[0]
        assert rad is False, f"{felter[i]}=NULL ga {rad!r}, ikke false"
    assert migrator.execute(
        "SELECT referansepayload_speiler(NULL,%s,%s,%s,%s,%s,%s)",
        tuple(verdier)).fetchone()[0] is False
    assert migrator.execute(
        "SELECT er_gyldig_referansepayload(NULL)").fetchone()[0] is False
    migrator.rollback()


# ---------------------------------------------------------------------------
# Sak ↔ logg (29–31)
# ---------------------------------------------------------------------------

@pg
def test_port29_31_sak_og_logg(migrator):
    """Port 29: sakens payload må speile LOGGPOSTENS ved commit. Port 30:
    revisjonslogg har ingen lineage-kolonner (modellen glir ikke tilbake).
    Port 31: snapshot-trioen er NULL for overtakelsessaken — `'ukjent'`
    skrives aldri."""
    h = _host()
    sak, gen = _konflikt(migrator, h)

    # Port 29 — endre sakens payload uten loggposten: deferred trigger ved
    # commit. (Speiler-CHECK-en passeres ved å endre payload+kolonne i takt.)
    _sett_kontekst(migrator, PLATT)
    migrator.execute(
        "UPDATE unntak SET"
        " referansepayload = jsonb_set(referansepayload,"
        "   '{autorisasjonsgenerasjon}', to_jsonb(autorisasjonsgenerasjon+1)),"
        " autorisasjonsgenerasjon = autorisasjonsgenerasjon + 1,"
        " saksrevisjon = saksrevisjon + 1"
        " WHERE tenant=%s AND id=%s", (PLATT, sak))
    with pytest.raises(psycopg.errors.RaiseException,
                       match="lineage avviker"):
        migrator.commit()
    migrator.rollback()

    # Port 30 — skjemaporten.
    kolonner = {r[0] for r in migrator.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name='revisjonslogg'")}
    migrator.rollback()
    assert not ({"hostname_ref", "utfordrer_tenant", "tapt_tenant",
                 "hendelse_a", "hendelse_b", "autorisasjonsgenerasjon",
                 "saksrevisjon"} & kolonner), kolonner

    # Port 31 — trioen er NULL, aldri 'ukjent'.
    _sett_kontekst(migrator, PLATT)
    trio = migrator.execute(
        "SELECT maks_auto_forsok_snapshot, policy_versjon,"
        "       policy_content_hash FROM unntak WHERE tenant=%s AND id=%s",
        (PLATT, sak)).fetchone()
    migrator.rollback()
    assert trio == (None, None, None), trio


# ---------------------------------------------------------------------------
# Roller og synlighet (32–37)
# ---------------------------------------------------------------------------

@pg
def test_port32_36_roller_og_synlighet(migrator):
    """Port 32: `disponit_domener` har ingen skriverett på unntak/
    revisjonslogg. Port 33: kundesesjonens RLS-snitt ser ikke saken;
    adjudikatoren ser den; en tredje tenant ikke. Port 34: adjudikatoren
    kan ikke skrive, og ser ingen annen tenantbunden tabell. Port 35:
    plattformtenanten kan ikke materialiseres som kunde. Port 36:
    `UPDATE unntak SET tenant` på saken avvises."""
    h = _host()
    sak, _ = _konflikt(migrator, h)

    # Port 32 — verifiseringsarbeiderens rolle: ingen skriverett. Rollen er
    # et klyngeobjekt fra oppsett (staging/prod); i en base uten den er det
    # ingenting å måle — og ingenting som kan skrive.
    har_domener = migrator.execute(
        "SELECT 1 FROM pg_roles WHERE rolname='disponit_domener'"
    ).fetchone() is not None
    migrator.rollback()
    for tabell in ("unntak", "revisjonslogg") if har_domener else ():
        for verb in ("INSERT", "UPDATE", "DELETE"):
            ok = migrator.execute(
                "SELECT has_table_privilege('disponit_domener', %s, %s)",
                (tabell, verb)).fetchone()[0]
            assert ok is False, f"disponit_domener har {verb} på {tabell}"
    migrator.rollback()

    # Port 33 — kundens RLS-snitt (uten claimer-rollen) ser ikke saken.
    from db.pg import koble, sett_kontekst
    rt = koble(DSN)
    try:
        for tenant in (ANNEN_TENANT, "t-api-tredje"):
            sett_kontekst(rt, tenant, "test", "r1")
            treff = rt.execute("SELECT count(*) FROM unntak WHERE id=%s",
                               (sak,)).fetchone()[0]
            rt.rollback()
            assert treff == 0, f"{tenant} ser overtakelsessaken"
        # ... adjudikatoren ser den — uten tenantkontekst.
        rt.execute("SET LOCAL ROLE disponit_domains_adjudicator")
        treff = rt.execute("SELECT count(*) FROM unntak WHERE id=%s",
                           (sak,)).fetchone()[0]
        assert treff == 1, "adjudikatoren ser ikke saken"
        # Port 34 — og KUN se: hvert skriveverb nektes, og en annen
        # tenantbunden tabell er utenfor synsfeltet.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("UPDATE unntak SET status='avvist' WHERE id=%s", (sak,))
        rt.rollback()
        rt.execute("SET LOCAL ROLE disponit_domains_adjudicator")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("DELETE FROM unntak WHERE id=%s", (sak,))
        rt.rollback()
        rt.execute("SET LOCAL ROLE disponit_domains_adjudicator")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("INSERT INTO unntak (tenant) VALUES ('x')")
        rt.rollback()
        rt.execute("SET LOCAL ROLE disponit_domains_adjudicator")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT count(*) FROM oppdrag")
        rt.rollback()
        # Codex P2: heller ikke domenehistorikken. Tabellen har ÉN policy
        # (GUC-sammenligningen fra 016), og runtime har SET til denne
        # rollen — et grant her ville latt en kompromittert runtime lese
        # hvilken som helst kundes aktører, grunner og overganger ved å
        # sette `disponit.tenant`. Køen spør bare `unntak`.
        rt.execute("SET LOCAL ROLE disponit_domains_adjudicator")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT count(*) FROM domenekontroll_hendelse")
        rt.rollback()
    finally:
        rt.close()

    # Port 35 — de tre materialiseringsveiene for en kundetenant.
    _sett_kontekst(migrator, PLATT)
    for sql, args in (
            ("INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
             " VALUES (%s,'b-1',ARRAY['leser'])", (PLATT,)),
            ("INSERT INTO api_tokener (token_id,tenant,rolle,scopes,"
             " secret_mac) VALUES ('tok-plt',%s,'leser',"
             " ARRAY['decisions:read'],'m')", (PLATT,)),
            ("INSERT INTO policyer (tenant,policy_id,versjon,innholds_hash,"
             " status,innhold,aktiv) VALUES (%s,'p','1','h','utkast','{}',"
             " false)", (PLATT,))):
        with pytest.raises(psycopg.errors.RaiseException,
                           match="reservert"):
            migrator.execute(sql, args)
        migrator.rollback()
        _sett_kontekst(migrator, PLATT)

    # Port 36 — saken flyttes aldri mellom tenants.
    _sett_kontekst(migrator, PLATT)
    migrator.execute("SET CONSTRAINTS ALL IMMEDIATE")
    # To vakter står i veien (kolonnelåsen alfabetisk først, deretter
    # revisjonsbindingens «flyttes aldri») — porten måler at flyttingen
    # avvises, uansett hvilken av dem som feller den.
    with pytest.raises(psycopg.errors.RaiseException,
                       match="flyttes aldri|kan endres"):
        migrator.execute("UPDATE unntak SET tenant=%s WHERE tenant=%s"
                         " AND id=%s", (ANNEN_TENANT, PLATT, sak))
    migrator.rollback()


def test_adjudikatorrollen_er_en_forutsetning_ikke_en_mulighet():
    """Codex P1: §9 ga policyen og grantet bak `IF EXISTS (rolname=...)`.

    Guarden så høflig ut, men den gjorde noe annet: på en base uten
    klyngerollen HOPPET 041 stille over adjudikatorens RLS-policy og SELECT
    på `unntak` — og ble likevel registrert som kjørt. `opp.sh` kjører
    `migrer.py` uten `oppsett-postgresql.sh`, så en eksisterende
    installasjon treffer nøyaktig det: rollen kommer ved neste
    oppsettkjøring, migrasjonen kjører aldri om igjen, og køens `SET
    ROLE`-lesninger står permanent uten leserett. Hver overtakelsessak blir
    uavgjørbar, og ingenting ser rødt ut.

    Porten måler derfor at rollen er en FORUTSETNING (§0 feiler hardt) og at
    §9 ikke lenger har noen vei rundt seg — pluss at `opp.sh` fanger det
    samme FØR første mutasjon, så migrasjonen ikke velter i steg 6 med
    tjenestene alt stoppet.

    MUTASJONEN SOM DREPER DENNE: legg `IF EXISTS`-guarden tilbake rundt
    policyen eller grantet, eller fjern §0.
    """
    from pathlib import Path

    rot = Path(__file__).resolve().parents[3]
    sql = (rot / "platform/core/db/migrations/041_overtakelsessak.sql"
           ).read_text(encoding="utf-8")

    # §0 står FØR første mutasjon, og feiler hardt.
    vakt = sql.index("rolname = 'disponit_domains_adjudicator'")
    assert vakt < sql.index("ALTER TABLE"), \
        "forutsetningen står etter første mutasjon — da er den ingen port"
    blokk = sql[vakt:vakt + 900]
    assert "RAISE EXCEPTION" in blokk, "forutsetningen feiler ikke migrasjonen"
    assert "oppsett-postgresql.sh" in blokk, "meldingen peker ikke på veien ut"

    # ... og det er den ENESTE steden rollens eksistens spørres om: en
    # gjenstående `IF EXISTS` ville vært den stille hoppingen på nytt.
    assert sql.count("rolname = 'disponit_domains_adjudicator'") == 1, \
        "041 spør fortsatt om rollen finnes — da kan §9 hoppes over stille"
    assert sql.count("CREATE POLICY domeneovertakelse_adjudikator") == 1
    assert "GRANT SELECT ON unntak TO disponit_domains_adjudicator" in sql

    # ... og utrullingen stopper før den rører noe.
    opp = (rot / "deploy/staging/opp.sh").read_text(encoding="utf-8")
    assert "disponit_domains_adjudicator" in opp, \
        "opp.sh har ingen port på rollen — migrasjonen ville veltet i steg 6"
    assert (opp.index("disponit_domains_adjudicator")
            < opp.index("HERFRA MUTERES SYSTEMET")), \
        "porten står etter første mutasjon — da er den ingen preflight"


@pg
def test_adjudikatoren_har_lesretten_migrasjonen_lovet(migrator):
    """Codex P1, den levende siden: policyen OG grantet skal faktisk stå i
    basen etter 041 — ikke bare være uhoppbare i filen."""
    finnes = migrator.execute(
        "SELECT 1 FROM pg_policy WHERE polname='domeneovertakelse_adjudikator'"
        "   AND polrelid='unntak'::regclass").fetchone()
    migrator.rollback()
    assert finnes, "adjudikatorpolicyen mangler på unntak"
    lese = migrator.execute(
        "SELECT has_table_privilege('disponit_domains_adjudicator',"
        "                           'unntak','SELECT')").fetchone()[0]
    migrator.rollback()
    assert lese is True, "adjudikatoren har ikke SELECT på unntak"


@pg
def test_reservert_navnerom_er_stengt_for_runtime(migrator):
    """Codex P2: en PERMISSIV policy legger til — den trekker ikke fra.

    Adjudikatorpolicyen (§9) ga et snitt, men fjernet ingenting fra det
    `tenant_isolasjon` alt slapp gjennom, og den sier bare
    `tenant = current_setting('disponit.tenant', true)`. GUC-en er fritt
    skrivbar og runtime har SELECT på `unntak`: én injeksjon kunne sette den
    til plattformtenanten og lese hvert omstridt vertsnavn, begge partene,
    generasjonen og lineagen — uten å anta adjudikatorrollen og uten å
    passere utfordrerfilteret i køen.

    Tre tabeller, fordi konflikten står tre steder: saken i `unntak`,
    speilet i `unntak_historikk`, og loggposten i `revisjonslogg` — den
    siste med vertsnavnet og BEGGE tenant-ID-ene i klartekst i
    `referansepayload`.

    Eieren står med vilje IKKE i porten: den er den eneste rollen med DELETE
    på disse tabellene (rydding og reparasjon er eierarbeid) og kan uansett
    slå av RLS. Andre halvdel her måler nettopp at den veien er intakt.
    """
    from db.pg import koble, sett_kontekst
    h = _host()
    sak, _ = _konflikt(migrator, h)

    rt = koble(DSN)
    try:
        sett_kontekst(rt, PLATT, "test", "r1")
        for sql, args in (
                ("SELECT count(*) FROM unntak WHERE id=%s", (sak,)),
                ("SELECT count(*) FROM unntak_historikk WHERE unntak_id=%s",
                 (sak,)),
                ("SELECT count(*) FROM revisjonslogg WHERE tenant=%s",
                 (PLATT,))):
            assert rt.execute(sql, args).fetchone()[0] == 0, sql
        rt.rollback()
        # ...og skriveveien er stengt likedan: WITH CHECK følger USING, så
        # en injeksjon kan heller ikke PLANTE en rad i navnerommet.
        sett_kontekst(rt, PLATT, "test", "r1")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute(
                "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,"
                "beslutning,begrunnelse) VALUES (%s,'h-res','p','TILLAT','[]')",
                (PLATT,))
        rt.rollback()
        # Kundetenanten er urørt — gjerdet gjelder navnerommet, ikke alt.
        sett_kontekst(rt, TENANT, "test", "r1")
        rt.execute(
            "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,"
            "beslutning,begrunnelse) VALUES (%s,'h-res','p','TILLAT','[]')",
            (TENANT,))
        rt.rollback()
    finally:
        rt.close()

    # Eierveien står: uten den kan ingen rydde eller reparere en plattformrad.
    _sett_kontekst(migrator, PLATT)
    assert migrator.execute("SELECT count(*) FROM unntak WHERE id=%s",
                            (sak,)).fetchone()[0] == 1
    migrator.rollback()


@pg
def test_reservert_navnerom_er_tomt_for_kundeflater(migrator):
    """Codex P1 (FORTIDEN): §8s triggere er BEFORE INSERT og sier
    ingenting om rader som alt sto der.

    Rullet 041 på en base der en kunde ALLEREDE het `__plattform_domener`,
    begynte §10 å skrive plattformens saker under kundens tenant-id — og
    kundens helt ordinære RLS-kontekst falt sammen med plattformens. §8.1
    stopper migrasjonen på en slik kollisjon; denne porten måler
    resultatet: på en migrert base finnes ingen kundeflate i navnerommet.

    Målingen må SE alle tenanter, ellers er den ingen måling (§1s felle):
    `brukermedlemskap` er FORCE RLS med ren GUC-policy og leses under
    BYPASSRLS-rollen, `policyer` under claimeren (m37_dispatcher).
    """
    migrator.execute("SET LOCAL ROLE disponit_domene_eier")
    bm = migrator.execute(
        r"SELECT count(*) FROM brukermedlemskap WHERE tenant LIKE E'\\_\\_%'"
    ).fetchone()[0]
    migrator.rollback()
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    pol = migrator.execute(
        r"SELECT count(*) FROM policyer WHERE tenant LIKE E'\\_\\_%'"
    ).fetchone()[0]
    migrator.rollback()
    tok = migrator.execute(
        r"SELECT count(*) FROM api_tokener WHERE tenant LIKE E'\\_\\_%'"
    ).fetchone()[0]
    migrator.rollback()
    assert (bm, pol, tok) == (0, 0, 0), \
        f"kundeflate i reservert navnerom: medlemskap={bm} policy={pol} token={tok}"


@pg
def test_port37_python_veien_er_stengt(migrator):
    """Port 37: `opprett_overtakelsessak` kan ikke skape en andre sak — den
    feller kalleren FØR noe når basen, og sakstallet står."""
    from api.domeneovertakelse import opprett_overtakelsessak
    h = _host()
    _konflikt(migrator, h)
    _sett_kontekst(migrator, PLATT)
    foer = migrator.execute(
        "SELECT count(*) FROM unntak WHERE hostname_ref=%s", (h,)).fetchone()[0]
    migrator.rollback()
    with pytest.raises(RuntimeError, match="stengt"):
        opprett_overtakelsessak(migrator, tenant_ny=ANNEN_TENANT, hostname=h,
                                tenant_tapt=TENANT, generasjon=99, aktor="sys")
    _sett_kontekst(migrator, PLATT)
    etter = migrator.execute(
        "SELECT count(*) FROM unntak WHERE hostname_ref=%s", (h,)).fetchone()[0]
    migrator.rollback()
    assert etter == foer == 1


# ---------------------------------------------------------------------------
# Regresjon (38, 41)
# ---------------------------------------------------------------------------

@pg
def test_port38_payloadtyper_er_gjensidig_utelukkende(migrator):
    """Port 38: `referanse` med ciphertext avvises, og `kryptert` med
    referansepayload avvises — på unntak OG revisjonslogg."""
    import json
    # INSERT-form: kolonnelåsen eier UPDATE-veien (og feller den før
    # CHECK-en) — det som måles her er at TILSTANDEN aldri kan oppstå.
    h = _host()
    sak, _ = _konflikt(migrator, h)
    _sett_kontekst(migrator, PLATT)
    ekte_a, ekte_b = migrator.execute(
        "SELECT hendelse_a, hendelse_b FROM unntak WHERE tenant=%s AND id=%s",
        (PLATT, sak)).fetchone()
    migrator.rollback()
    pl = _payload(h, a=int(ekte_a), b=int(ekte_b))
    _sett_kontekst(migrator, PLATT)
    lid = migrator.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse,payload_type,referansepayload)"
        " VALUES (%s,'h','p','UNNTAK','[]'::jsonb,'referanse',%s::jsonb)"
        " RETURNING id", (PLATT, json.dumps(pl))).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation) as ei:
        migrator.execute(
            "INSERT INTO unntak (tenant,loggpost_id,handling,kategori,"
            " sakstype,prioritet,sakskilde,hostname_ref,utfordrer_tenant,"
            " tapt_tenant,autorisasjonsgenerasjon,saksrevisjon,hendelse_a,"
            " hendelse_b,payload_type,referansepayload,payload_kryptert)"
            " VALUES (%s,%s,'domene.overtakelse','domeneovertakelse',"
            " 'sikkerhet','hoy','domeneovertakelse',%s,%s,%s,1,0,%s,%s,"
            " 'referanse',%s::jsonb,%s)",
            (PLATT, lid, h, ANNEN_TENANT, TENANT, ekte_a, ekte_b,
             json.dumps(pl), b"\x01"))
    assert "unntak_payload_konsistent" in str(ei.value)
    migrator.rollback()

    # ... og en kryptert kjernesak kan ikke bære en referansepayload.
    _sett_kontekst(migrator, TENANT)
    lid2 = migrator.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse) VALUES (%s,'h','p','STOPP','[]'::jsonb) RETURNING id",
        (TENANT,)).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation) as ei:
        migrator.execute(
            "INSERT INTO unntak (tenant,loggpost_id,handling,kategori,"
            "payload_kryptert,key_id,nonce,maks_auto_forsok_snapshot,"
            "policy_versjon,policy_content_hash,sakskilde,referansepayload)"
            " VALUES (%s,%s,'x','over_grense',%s,'k1',%s,3,'1.0.0','ph',"
            "'policybrudd',%s::jsonb)",
            (TENANT, lid2, b"\x00", b"\x00" * 12,
             json.dumps(_payload("gyldig.example"))))
    assert "unntak_payload_konsistent" in str(ei.value)
    migrator.rollback()

    # revisjonslogg: samme utelukkelse.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,"
            "beslutning,begrunnelse,payload_type,referansepayload)"
            " VALUES (%s,'h','p','STOPP','[]'::jsonb,'kryptert',%s::jsonb)",
            (TENANT, json.dumps(_payload("gyldig.example"))))
    migrator.rollback()


@pg
def test_port41_varselfeil_feller_ikke_saken(migrator):
    """Port 41: varselet er ikke evidens — saken er. Feiler varslingen,
    står både overtakelsen og saken; ingenting rulles tilbake."""
    h = _host()
    # Fell varsle_overtakelse innenfra: ta bort dens SELECT-vei ved å gi
    # funksjonen en umulig kanal — enklest ved å fjerne EXECUTE er ikke nok
    # (definer). I stedet: riv tabellen den skriver til, i EGEN transaksjon
    # rundt konflikten, og legg den tilbake. ALTER er skjemaeierens.
    migrator.execute("ALTER TABLE varsel RENAME TO varsel_borte")
    migrator.commit()
    try:
        sak, gen = _konflikt(migrator, h)
        rad = _sakrad(migrator, sak)
        assert rad[0] == "ny" and rad[5] == h, rad
        assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "avklaring_kreves"
    finally:
        migrator.rollback()
        migrator.execute("ALTER TABLE varsel_borte RENAME TO varsel")
        migrator.commit()
