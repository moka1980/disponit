"""PR-013 CP1: migrasjon 012 — datamodell + integritetstriggere.

DB-en håndhever fullmaktsreglene, ikke koden: `er_forfatter` server-utledet
(V7), avledet `policyer.aktiv` ⇔ `policy_hode.aktiv_versjon` (V1/v5§1),
utkast-/runde-statemaskiner, append-only attestasjon. Hver trigger muteres bort
av en ulovlig operasjon som MÅ feile.
"""
import secrets

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
# Fersk tenant per kjøring: DB-en truncates ikke mellom kjøringer.
TEN = "t-pol-" + secrets.token_hex(3)


def _c():
    from db.pg import koble
    c = koble(MIGRATOR_DSN)
    # SESJONS-nivå GUC (is_local=false), så tenant-konteksten overlever commit
    # — ellers ville RLS skjult radene etter første commit og triggerne aldri
    # fyrt (0 rader oppdatert).
    c.execute("SELECT set_config('disponit.tenant',%s,false),"
              " set_config('disponit.aktor','sys',false)", (TEN,))
    return c


def _policyrad(c, pid, versjon, aktiv=False):
    c.execute(
        "INSERT INTO policyer (tenant,policy_id,versjon,innholds_hash,status,"
        "innhold,aktiv) VALUES (%s,%s,%s,%s,'validert_pilot','{}'::jsonb,%s)"
        " ON CONFLICT DO NOTHING",
        (TEN, pid, versjon, secrets.token_hex(32), aktiv))


def _hode(c, pid, aktiv_versjon=None):
    # `neste_versjon` er borte (migrasjon 020): versjonen kommer fra policyens
    # egen `meta.versjon`, ikke fra en teller på ankerraden.
    c.execute("INSERT INTO policy_hode (tenant,policy_id,aktiv_versjon)"
              " VALUES (%s,%s,%s)", (TEN, pid, aktiv_versjon))


def _utkast(c, uid, pid, av="bruker-a", status="utkast", innhold='{"a":1}'):
    c.execute(
        "INSERT INTO policyutkast (tenant,utkast_id,policy_id,innhold,status,"
        "opprettet_av) VALUES (%s,%s,%s,%s::jsonb,%s,%s)",
        (TEN, uid, pid, innhold, status, av))


def _runde(c, uid, runde=1, **over):
    felt = dict(diff_hash="d", utkast_innholds_hash="u", base_policy_hash="b",
                risikoklasse="UTVIDER", klassifisering_hash="k",
                klassifikatorversjon="kv1", policyskjema_versjon="0.2",
                motor_semantikkversjon="m1", deny_all_hash="da",
                deny_all_versjon="1", pakrevd_antall_godkjennere=2,
                utloper="now()+interval '1 hour'")
    felt.update(over)
    c.execute(
        "INSERT INTO aktiveringsrunde (tenant,utkast_id,runde,diff_hash,"
        "utkast_innholds_hash,base_policy_hash,risikoklasse,klassifisering_hash,"
        "klassifikatorversjon,policyskjema_versjon,motor_semantikkversjon,"
        "deny_all_hash,deny_all_versjon,pakrevd_antall_godkjennere,utloper)"
        f" VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,{felt['utloper']})",
        (TEN, uid, runde, felt["diff_hash"], felt["utkast_innholds_hash"],
         felt["base_policy_hash"], felt["risikoklasse"], felt["klassifisering_hash"],
         felt["klassifikatorversjon"], felt["policyskjema_versjon"],
         felt["motor_semantikkversjon"], felt["deny_all_hash"],
         felt["deny_all_versjon"], felt["pakrevd_antall_godkjennere"]))


def _attest(c, uid, bruker, er_forfatter, runde=1, jti=None):
    c.execute(
        "INSERT INTO aktiveringsattestasjon (tenant,utkast_id,runde,bruker_id,"
        "rolle,authz_version,er_forfatter,diff_hash,klassifisering_hash,"
        "risikoklasse,konvoluttversjon,konvolutt_hash,mac,mac_key_id,jti,utloper)"
        " VALUES (%s,%s,%s,%s,'okonomi',1,%s,'d','k','UTVIDER',1,'h','m','mk1',%s,"
        "now()+interval '1 hour')",
        (TEN, uid, runde, bruker, er_forfatter, jti or secrets.token_hex(16)))


@pg
def test_er_forfatter_trigger_avviser_feil_boolean():
    c = _c()
    try:
        _utkast(c, "u1", "p1", av="bruker-a")
        _policyrad(c, "p1", "1")
        _runde(c, "u1")
        # Riktig: bruker-a ER forfatter.
        _attest(c, "u1", "bruker-a", True)
        # Feil: en fremmed bruker påstår er_forfatter=True → trigger avviser.
        with pytest.raises(psycopg.errors.RaiseException) as ei:
            _attest(c, "u1", "bruker-b", True)
        assert "er_forfatter" in str(ei.value)
        c.rollback()
        # Feil andre vei: forfatteren merkes som IKKE-forfatter → avvist.
        c2 = _c()
        try:
            _utkast(c2, "u1b", "p1b", av="bruker-a")
            _policyrad(c2, "p1b", "1")
            _runde(c2, "u1b")
            with pytest.raises(psycopg.errors.RaiseException):
                _attest(c2, "u1b", "bruker-a", False)
            c2.rollback()
        finally:
            c2.close()
    finally:
        c.close()


@pg
def test_avledet_aktiv_peker_konsistens():
    c = _c()
    try:
        _policyrad(c, "p2", "1", aktiv=True)
        _policyrad(c, "p2", "2", aktiv=False)   # finnes, men ikke aktiv
        _hode(c, "p2", aktiv_versjon="1")
        c.commit()                       # konsistent: peker=1, aktiv-rad=1 → OK
        # Flytt PEKEREN til versjon 2 mens den aktive raden fortsatt er 1 —
        # delindeksen fanger ikke dette (kun én aktiv rad); den DEFERRED
        # peker-triggeren gjør det, ved commit.
        c.execute("UPDATE policy_hode SET aktiv_versjon='2' WHERE tenant=%s AND"
                  " policy_id='p2'", (TEN,))
        with pytest.raises(psycopg.errors.RaiseException):
            c.commit()
        c.rollback()
    finally:
        c.close()


@pg
def test_peker_uten_aktiv_rad_avvises():
    c = _c()
    try:
        _policyrad(c, "p3", "1", aktiv=False)   # ingen aktiv rad
        _hode(c, "p3", aktiv_versjon="1")        # men pekeren peker
        with pytest.raises(psycopg.errors.RaiseException):
            c.commit()
        c.rollback()
    finally:
        c.close()


@pg
def test_utkast_statemaskin_og_optimistisk_laas():
    c = _c()
    try:
        _utkast(c, "u2", "p4", status="utkast")
        c.commit()
        # Innholdsendring uten versjonsøkning → avvist.
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE policyutkast SET innhold='{\"a\":2}'::jsonb"
                      " WHERE tenant=%s AND utkast_id='u2'", (TEN,))
        c.rollback()
        # Med versjonsøkning → OK.
        c.execute("UPDATE policyutkast SET innhold='{\"a\":2}'::jsonb,"
                  " utkastversjon=2 WHERE tenant=%s AND utkast_id='u2'", (TEN,))
        c.commit()
        # Ulovlig statusovergang utkast→aktivert → avvist.
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE policyutkast SET status='aktivert' WHERE tenant=%s"
                      " AND utkast_id='u2'", (TEN,))
        c.rollback()
    finally:
        c.close()


@pg
def test_frosset_innholds_hash_kan_ikke_endres():
    c = _c()
    try:
        _utkast(c, "u3", "p5")
        c.execute("UPDATE policyutkast SET status='validert', innholds_hash=%s"
                  " WHERE tenant=%s AND utkast_id='u3'", (secrets.token_hex(32), TEN))
        c.commit()
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE policyutkast SET innholds_hash=%s WHERE tenant=%s"
                      " AND utkast_id='u3'", (secrets.token_hex(32), TEN))
        c.rollback()
    finally:
        c.close()


@pg
def test_en_aktiv_aktiveringsrunde_delindeks():
    c = _c()
    try:
        _utkast(c, "u4", "p6")
        _runde(c, "u4", runde=1)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _runde(c, "u4", runde=2)       # to åpne runder for samme utkast
        c.rollback()
    finally:
        c.close()


@pg
def test_runde_bindingsfelt_frosset_og_statemaskin():
    c = _c()
    try:
        _utkast(c, "u5", "p7")
        _runde(c, "u5")
        c.commit()
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE aktiveringsrunde SET diff_hash='endret' WHERE"
                      " tenant=%s AND utkast_id='u5'", (TEN,))
        c.rollback()
        # apen→brukt er ulovlig (må via klar).
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE aktiveringsrunde SET status='brukt' WHERE tenant=%s"
                      " AND utkast_id='u5'", (TEN,))
        c.rollback()
    finally:
        c.close()


@pg
def test_attestasjon_append_only_og_unik_jti():
    c = _c()
    try:
        _utkast(c, "u6", "p8", av="bruker-a")
        _runde(c, "u6")
        j = secrets.token_hex(16)
        _attest(c, "u6", "bruker-a", True, jti=j)
        c.commit()
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE aktiveringsattestasjon SET rolle='x' WHERE tenant=%s"
                      " AND utkast_id='u6'", (TEN,))
        c.rollback()
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("DELETE FROM aktiveringsattestasjon WHERE tenant=%s AND"
                      " utkast_id='u6'", (TEN,))
        c.rollback()
        # Gjenbrukt jti → engangsbruk brytes.
        _utkast(c, "u6b", "p8b", av="bruker-a")
        _runde(c, "u6b")
        with pytest.raises(psycopg.errors.UniqueViolation):
            _attest(c, "u6b", "bruker-a", True, jti=j)
        c.rollback()
    finally:
        c.close()


def _rt():
    """Runtime-forbindelse (disponit): EXECUTE på aktiver_policy, men INGEN
    direkte skriv på policyer/policy_hode."""
    from db.pg import koble
    c = koble(DSN)
    c.execute("SELECT set_config('disponit.tenant',%s,false),"
              " set_config('disponit.aktor','x',false)", (TEN,))
    return c


@pg
def test_runtime_kan_ikke_skrive_policyer_direkte():
    """V10: runtime har KUN SELECT på policyer — aktivering går via funksjonen."""
    r = _rt()
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            r.execute("INSERT INTO policyer (tenant,policy_id,versjon,"
                      "innholds_hash,status,innhold,aktiv) VALUES (%s,'x','9',"
                      "%s,'x','{}',true)", (TEN, secrets.token_hex(32)))
        r.rollback()
    finally:
        r.close()


def _validert_utkast(c, uid, pid, av="bruker-a", innhold=None,
                     versjon="1.1.0"):
    # Aktiveringen lagrer policyens EGEN `meta.versjon` som registerets
    # `versjon` (migrasjon 020), krever at dokumentet bærer radens egen
    # `meta.policy_id` (022) og at det SIER `produksjon` (023) — et utkast uten
    # dem kan ikke aktiveres, heller ikke i disse DB-nære testene.
    if innhold is None:
        innhold = ('{"meta":{"policy_id":"' + pid + '","versjon":"'
                   + versjon + '","status":"produksjon"},"a":1}')
    c.execute(
        "INSERT INTO policyutkast (tenant,utkast_id,policy_id,innhold,status,"
        "innholds_hash,opprettet_av) VALUES (%s,%s,%s,%s::jsonb,'validert',%s,%s)",
        (TEN, uid, pid, innhold, "ih-" + secrets.token_hex(8), av))


@pg
def test_aktiver_policy_krever_runde():
    """🔴 P1 R1: fire-øyne-gaten ligger I funksjonen. Uten en runde kan et
    direkte runtime-kall (utenom orkestreringen) ALDRI aktivere."""
    c = _c()
    uid, pid = "u-" + secrets.token_hex(4), "pol-" + secrets.token_hex(3)
    _validert_utkast(c, uid, pid)
    c.commit(); c.close()
    r = _rt()
    try:
        with pytest.raises(psycopg.errors.Error):     # no_data_found: ukjent runde
            r.execute("SELECT aktiver_policy(%s,%s,1,NULL)", (TEN, uid))
        r.rollback()
    finally:
        r.close()


@pg
def test_aktiver_policy_krever_fire_oyne():
    """🔴 P1 R1: terskelen håndheves I funksjonen. Én attestasjon (forfatteren
    alene) når ikke pakrevd=2 → et direkte kall avvises (InsufficientPrivilege),
    ikke bare når Python-koden nekter."""
    c = _c()
    uid, pid = "u-" + secrets.token_hex(4), "pol-" + secrets.token_hex(3)
    _validert_utkast(c, uid, pid, av="forf")
    _runde(c, uid)                          # pakrevd_antall_godkjennere=2
    _attest(c, uid, "forf", True)           # bare forfatteren
    c.commit(); c.close()
    r = _rt()
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            r.execute("SELECT aktiver_policy(%s,%s,1,NULL)", (TEN, uid))
        r.rollback()
    finally:
        r.close()


@pg
def test_aktiver_policy_full_runde_aktiverer_nøyaktig_en():
    """Full runde (to godkjennere, én uavhengig, begge bandt rundens diff) →
    funksjonen aktiverer, nøyaktig én aktiv, pekeren treffer, runden `brukt`,
    utkastet `aktivert`."""
    c = _c()
    uid, pid = "u-" + secrets.token_hex(4), "pol-" + secrets.token_hex(3)
    _validert_utkast(c, uid, pid, av="forf")
    _runde(c, uid)
    _attest(c, uid, "forf", True)
    _attest(c, uid, "uavh", False)
    c.commit(); c.close()
    r = _rt()
    try:
        v1 = r.execute("SELECT aktiver_policy(%s,%s,1,NULL)",
                       (TEN, uid)).fetchone()[0]
        r.commit()
        r.execute("SELECT set_config('disponit.tenant',%s,false)", (TEN,))
        n = r.execute("SELECT count(*) FROM policyer WHERE tenant=%s AND"
                      " policy_id=%s AND aktiv", (TEN, pid)).fetchone()[0]
        peker = r.execute("SELECT aktiv_versjon FROM policy_hode WHERE tenant=%s"
                          " AND policy_id=%s", (TEN, pid)).fetchone()[0]
        rstatus = r.execute("SELECT status FROM aktiveringsrunde WHERE tenant=%s"
                            " AND utkast_id=%s", (TEN, uid)).fetchone()[0]
        ustatus = r.execute("SELECT status FROM policyutkast WHERE tenant=%s AND"
                            " utkast_id=%s", (TEN, uid)).fetchone()[0]
        assert n == 1 and peker == v1
        assert rstatus == "brukt" and ustatus == "aktivert"
        r.rollback()
    finally:
        r.close()


@pg
def test_aktiver_policy_krever_semantisk_versjon():
    """🔴 Versjonen registeret lagrer er utkastets EGEN `meta.versjon`.

    Mangler den (eller er den ikke på formen 1.2.3), kan raden ikke lagres:
    `policyregister.hent_aktiv` krever at kolonnen og dokumentet er enige, så
    en aktivering uten den ville etterlatt en policy beslutningsveien avviser
    som korrupt — etter å ha svart «aktivert». Funksjonen nekter i stedet.
    """
    c = _c()
    uid, pid = "u-" + secrets.token_hex(4), "pol-" + secrets.token_hex(3)
    # Identiteten (022) og statusen (023) er på plass — det er VERSJONEN som
    # mangler, ellers ville testen bevist feil kontroll.
    _validert_utkast(c, uid, pid, av="forf",
                     innhold='{"meta":{"policy_id":"' + pid + '",'
                             '"status":"produksjon"},"a":1}')
    _runde(c, uid)
    _attest(c, uid, "forf", True)
    _attest(c, uid, "uavh", False)
    c.commit(); c.close()
    r = _rt()
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            r.execute("SELECT aktiver_policy(%s,%s,1,NULL)", (TEN, uid))
        r.rollback()
    finally:
        r.close()


@pg
def test_aktiver_policy_avviser_versjon_som_alt_er_registrert():
    """En versjon kan ikke skrives to ganger for samme policy.

    Uten kontrollen ville PK-en felt INSERT-en som en rå `unique_violation` —
    ikke til å skille fra pekerdriften kalleren behandler helt annerledes.
    """
    c = _c()
    uid, pid = "u-" + secrets.token_hex(4), "pol-" + secrets.token_hex(3)
    _policyrad(c, pid, "1.1.0")             # finnes, men er ikke aktiv
    _validert_utkast(c, uid, pid, av="forf", versjon="1.1.0")
    _runde(c, uid)
    _attest(c, uid, "forf", True)
    _attest(c, uid, "uavh", False)
    c.commit(); c.close()
    r = _rt()
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            r.execute("SELECT aktiver_policy(%s,%s,1,NULL)", (TEN, uid))
        r.rollback()
    finally:
        r.close()


@pg
def test_aktiver_policy_krever_nyere_versjon_enn_aktiv():
    """Monotoni: etterfølgeren må være NYERE enn den aktive.

    Det var jobben telleren `neste_versjon` gjorde. Når versjonen kommer fra
    dokumentet, må kravet håndheves eksplisitt — ellers kunne en aktivering
    flytte policyen bakover uten at noe sa fra.
    """
    c = _c()
    uid, pid = "u-" + secrets.token_hex(4), "pol-" + secrets.token_hex(3)
    _policyrad(c, pid, "2.0.0", aktiv=True)
    _hode(c, pid, aktiv_versjon="2.0.0")
    _validert_utkast(c, uid, pid, av="forf", versjon="1.9.9")
    _runde(c, uid)
    _attest(c, uid, "forf", True)
    _attest(c, uid, "uavh", False)
    c.commit(); c.close()
    r = _rt()
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            r.execute("SELECT aktiver_policy(%s,%s,1,%s)", (TEN, uid, "2.0.0"))
        r.rollback()
    finally:
        r.close()


@pg
def test_aktiver_policy_krever_dokumentets_egen_policy_id():
    """🔴 P1: dokumentet må bære den identiteten raden aktiveres under.

    `policyutkast.policy_id` og `innhold.meta.policy_id` er to felter uten noen
    binding mellom seg — redigeringsveien skriver nytt innhold uten å røre
    radens id. Aktiveres et slikt utkast, indekseres policyen under én id mens
    motoren bygger beslutningens policyreferanse fra dokumentets egen. Ingen av
    de to halvdelene ser gale ut hver for seg; sakene bare slutter å finne
    policyen sin.

    Bruddet er NAVNGITT (`dokument_policy_id`), så kalleren kan skille det fra
    versjonsinvariantene, som deler feilkode.

    Kontroll: fjern steg 1b i migrasjon 022, så aktiverer denne uten å blunke.
    """
    c = _c()
    uid, pid = "u-" + secrets.token_hex(4), "pol-" + secrets.token_hex(3)
    _validert_utkast(c, uid, pid, av="forf",
                     innhold='{"meta":{"policy_id":"en-annen-policy",'
                             '"versjon":"1.1.0","status":"produksjon"},"a":1}')
    _runde(c, uid)
    _attest(c, uid, "forf", True)
    _attest(c, uid, "uavh", False)
    c.commit(); c.close()
    r = _rt()
    try:
        with pytest.raises(psycopg.errors.CheckViolation) as e:
            r.execute("SELECT aktiver_policy(%s,%s,1,NULL)", (TEN, uid))
        assert e.value.diag.constraint_name == "dokument_policy_id", (
            "bruddet må være navngitt — ellers rapporteres et identitetsavvik"
            " som versjon_i_bruk")
    finally:
        r.rollback()
        r.close()


@pg
def test_aktiver_policy_krever_at_dokumentet_sier_produksjon():
    """🔴 P1: raden skrives som `produksjon` — dokumentet må si det samme.

    Skjemaet tillater `utkast` og `validert_pilot` i `meta.status`, så en slik
    policy er fullt gyldig og gikk hele veien gjennom fire-øyne. Etterpå avviste
    `hent_aktiv` raden: `meta.status 'utkast' != registerets 'produksjon'` —
    aktiveringen svarte «aktivert», beslutningsveien svarte `PolicyKorrupt`.

    Kontroll: fjern steg 1c i migrasjon 023, så aktiverer denne et utkast som
    beslutningsveien aldri kan bruke.
    """
    c = _c()
    uid, pid = "u-" + secrets.token_hex(4), "pol-" + secrets.token_hex(3)
    _validert_utkast(c, uid, pid, av="forf",
                     innhold='{"meta":{"policy_id":"' + pid + '",'
                             '"versjon":"1.1.0","status":"utkast"},"a":1}')
    _runde(c, uid)
    _attest(c, uid, "forf", True)
    _attest(c, uid, "uavh", False)
    c.commit(); c.close()
    r = _rt()
    try:
        with pytest.raises(psycopg.errors.CheckViolation) as e:
            r.execute("SELECT aktiver_policy(%s,%s,1,NULL)", (TEN, uid))
        assert e.value.diag.constraint_name == "dokument_status", (
            "bruddet må være navngitt — ellers er det ikke til å skille fra"
            " versjonsinvariantene")
    finally:
        r.rollback()
        r.close()


@pg
def test_aktiver_policy_monotoni_nullpadder_gamle_versjoner():
    """🔴 «2.0.0» er ikke nyere enn en aktiv «2» — den er den SAMME.

    De gamle radene telleren skrev bærer «1»/«2», og migrasjon 020 lar dem
    bevisst stå. Uten nullpadding sammenlignes `{2,0,0}` mot `{2}`: prefikset
    er likt, den lengste vinner, og monotonikontrollen slipper gjennom et
    dokument med nøyaktig den versjonen den aktive raden allerede bærer.
    Migrasjon 021 padder begge sider til samme bredde først.
    """
    c = _c()
    uid, pid = "u-" + secrets.token_hex(4), "pol-" + secrets.token_hex(3)
    _policyrad(c, pid, "2", aktiv=True)          # skrevet av den gamle telleren
    _hode(c, pid, aktiv_versjon="2")
    _validert_utkast(c, uid, pid, av="forf", versjon="2.0.0")
    _runde(c, uid)
    _attest(c, uid, "forf", True)
    _attest(c, uid, "uavh", False)
    c.commit(); c.close()
    r = _rt()
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            r.execute("SELECT aktiver_policy(%s,%s,1,%s)", (TEN, uid, "2"))
    finally:
        r.rollback()
        r.close()

    # …og en som FAKTISK er nyere enn 2.0.0 slipper fortsatt gjennom, så
    # kontrollen stopper dubletten og ikke monotonien selv. Ny forbindelse:
    # `set_config(..., false)` i `_rt()` er transaksjonell som alt annet SET,
    # så rullebakken over tok tenant-konteksten med seg — og uten den skjuler
    # RLS utkastet vi nettopp skrev.
    c = _c()
    uid2 = "u-" + secrets.token_hex(4)
    _validert_utkast(c, uid2, pid, av="forf", versjon="2.0.1")
    _runde(c, uid2)
    _attest(c, uid2, "forf", True)
    _attest(c, uid2, "uavh", False)
    c.commit(); c.close()
    r = _rt()
    try:
        assert r.execute("SELECT aktiver_policy(%s,%s,1,%s)",
                         (TEN, uid2, "2")).fetchone()[0] == "2.0.1"
    finally:
        r.rollback()
        r.close()


@pg
def test_aktiver_policy_stale_base_serialization_failure():
    """Base flyttet siden runden åpnet: aktiv er v1, men kallet tror base er
    deny-all (NULL) → serialization_failure (rebasering)."""
    c = _c()
    pid = "pol-" + secrets.token_hex(3)
    _policyrad(c, pid, "1", aktiv=True)
    _hode(c, pid, aktiv_versjon="1")
    uid = "u-" + secrets.token_hex(4)
    _validert_utkast(c, uid, pid, av="forf")
    _runde(c, uid)
    _attest(c, uid, "forf", True)
    _attest(c, uid, "uavh", False)
    c.commit(); c.close()
    r = _rt()
    try:
        with pytest.raises(psycopg.errors.SerializationFailure):
            r.execute("SELECT aktiver_policy(%s,%s,1,NULL)", (TEN, uid))
        r.rollback()
    finally:
        r.close()
