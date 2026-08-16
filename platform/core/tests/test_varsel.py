"""Varsler — «noe venter på DEG», i portalen og på e-post.

Grensene som prøves her er de som avgjør om varslingen er til å stole på:
  * den varsler dem som faktisk kan bringe runden videre, ikke «alle»;
  * den varsler ikke to ganger for samme hendelse;
  * valget «kun portal» er et bevisst fravær, ikke en feilet sending;
  * ingen kan merke en KOLLEGAS varsel som lest — RLS skiller tenanter, ikke
    mennesker inne i samme tenant;
  * og viktigst: en varslingsfeil velter ALDRI handlingen som utløste den.
"""
import secrets

import pytest

from api import varsel

from .test_api import MIGRATOR_DSN

pg = pytest.mark.skipif(not MIGRATOR_DSN,
                        reason="DISPONIT_MIGRATOR_DSN ikke satt")
TEN = "t-varsel-" + secrets.token_hex(3)


def _conn(tenant=TEN):
    from db.pg import koble, sett_kontekst
    c = koble(MIGRATOR_DSN)
    sett_kontekst(c, tenant, "test", "r0")
    return c


def _bruker(c, navn, roller=("policyforvalter",), tenant=TEN):
    bid = c.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
        " ON CONFLICT (issuer,sub) DO UPDATE SET sub=EXCLUDED.sub"
        " RETURNING bruker_id",
        ("https://idp.example", f"{tenant}-{navn}")).fetchone()[0]
    arr = "ARRAY[" + ",".join(f"'{r}'" for r in roller) + "]"
    c.execute(f"INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
              f" VALUES (%s,%s,{arr}) ON CONFLICT (tenant,bruker_id)"
              f" DO UPDATE SET roller=EXCLUDED.roller, aktiv=true",
              (tenant, bid))
    return bid


@pg
def test_varsler_bare_dem_som_kan_bringe_runden_videre():
    """Den som ALLEREDE har attestert er ferdig. Å varsle henne igjen lærer
    henne bare å overse varsler.

    Kontroll: fjern NOT EXISTS-leddet i `mottakere_for_runde`, så blir denne
    rød med to mottakere.
    """
    c = _conn()
    try:
        a = _bruker(c, "a")
        b = _bruker(c, "b")
        _bruker(c, "leser", roller=("leser",))     # feil rolle → aldri mottaker
        uid = "u-" + secrets.token_hex(6)
        c.execute(
            "INSERT INTO policyutkast (tenant,utkast_id,policy_id,innhold,"
            "opprettet_av) VALUES (%s,%s,'p','{}'::jsonb,%s)", (TEN, uid, a))
        c.execute(
            "INSERT INTO aktiveringsrunde (tenant,utkast_id,runde,status,"
            "diff_hash,utkast_innholds_hash,base_policy_hash,risikoklasse,"
            "klassifisering_hash,klassifikatorversjon,policyskjema_versjon,"
            "motor_semantikkversjon,deny_all_hash,deny_all_versjon,"
            "pakrevd_antall_godkjennere,utloper)"
            " VALUES (%s,%s,1,'apen','d','i','b','UTVIDER','k','1','0.2','1',"
            "'dh','1',2,now()+interval '1 hour')", (TEN, uid))
        c.execute(
            "INSERT INTO aktiveringsattestasjon (tenant,utkast_id,runde,"
            "bruker_id,rolle,authz_version,er_forfatter,diff_hash,"
            "klassifisering_hash,risikoklasse,konvoluttversjon,konvolutt_hash,"
            "mac,mac_key_id,jti,utloper)"
            " VALUES (%s,%s,1,%s,'policyforvalter',1,true,'d','k','UTVIDER',"
            "1,'kh','m','mk1',%s,now()+interval '1 hour')",
            (TEN, uid, a, "jti-" + secrets.token_hex(12)))

        mott = varsel.mottakere_for_runde(c, TEN, uid)
        assert mott == [b], f"forventet bare den som IKKE har attestert, fikk {mott}"
    finally:
        c.close()


@pg
def test_samme_hendelse_varsler_bare_en_gang():
    """En retry av handlingen skal ikke fylle innboksen.

    Kontroll: fjern det unike indekset i migrasjon 026 (eller ON CONFLICT),
    så blir denne rød.
    """
    c = _conn()
    try:
        b = _bruker(c, "dup")
        uid = "u-" + secrets.token_hex(6)
        f = dict(tenant=TEN, bruker_id=b, art="attestering_venter",
                 ressurs_type="policyutkast", ressurs_id=uid,
                 tekstnokkel="varsel.attestering_venter")
        assert varsel.opprett(c, **f) is True, "første varsel ble ikke opprettet"
        assert varsel.opprett(c, **f) is False, "duplikat slapp gjennom"
        assert varsel.antall_uleste(c, tenant=TEN, bruker_id=b) == 1
    finally:
        c.close()


@pg
def test_kun_portal_er_bevisst_fravaer_ikke_feilet_sending():
    c = _conn()
    try:
        b = _bruker(c, "portalvalg")
        varsel.sett_kanal(c, tenant=TEN, bruker_id=b, kanal="kun_portal")
        uid = "u-" + secrets.token_hex(6)
        varsel.opprett(c, tenant=TEN, bruker_id=b, art="attestering_venter",
                       ressurs_type="policyutkast", ressurs_id=uid,
                       tekstnokkel="varsel.attestering_venter")
        st = c.execute("SELECT epost_status FROM varsel WHERE tenant=%s"
                       " AND bruker_id=%s AND ressurs_id=%s",
                       (TEN, b, uid)).fetchone()[0]
        assert st == "ikke_aktuelt", (
            f"epost_status={st!r} — «kun portal» må skilles fra en sending "
            "som feilet, ellers jager driften en feil som ikke finnes")
        # …men varselet SKAL fortsatt stå i innboksen.
        assert varsel.antall_uleste(c, tenant=TEN, bruker_id=b) == 1
    finally:
        c.close()


@pg
def test_standardvalget_er_begge_kanaler():
    """Fraværende rad er ikke «av». Ingen skal gå glipp av at noe venter fordi
    de aldri åpnet innstillingene."""
    c = _conn()
    try:
        b = _bruker(c, "urort")
        assert varsel.hent_kanal(c, tenant=TEN, bruker_id=b) == "epost_og_portal"
    finally:
        c.close()


@pg
def test_ingen_kan_merke_en_kollegas_varsel_som_lest():
    """RLS skiller TENANTER, ikke mennesker inne i samme tenant.

    Uten `bruker_id` i WHERE kunne én bruker skjult at noe ventet på en annen.
    Kontroll: fjern det leddet fra `merk_lest`, så blir denne rød.
    """
    c = _conn()
    try:
        eier = _bruker(c, "eier")
        annen = _bruker(c, "annen")
        uid = "u-" + secrets.token_hex(6)
        varsel.opprett(c, tenant=TEN, bruker_id=eier, art="attestering_venter",
                       ressurs_type="policyutkast", ressurs_id=uid,
                       tekstnokkel="varsel.attestering_venter")
        vid = c.execute("SELECT id FROM varsel WHERE tenant=%s AND bruker_id=%s"
                        " AND ressurs_id=%s", (TEN, eier, uid)).fetchone()[0]
        assert varsel.merk_lest(c, tenant=TEN, bruker_id=annen,
                                varsel_id=vid) is False, "kollegaen fikk merke det"
        assert varsel.antall_uleste(c, tenant=TEN, bruker_id=eier) == 1
        assert varsel.merk_lest(c, tenant=TEN, bruker_id=eier,
                                varsel_id=vid) is True
        assert varsel.antall_uleste(c, tenant=TEN, bruker_id=eier) == 0
        # …og ikke to ganger.
        assert varsel.merk_lest(c, tenant=TEN, bruker_id=eier,
                                varsel_id=vid) is False
    finally:
        c.close()


@pg
def test_varsling_velter_aldri_handlingen():
    """Den viktigste testen her.

    En aktiveringsrunde er en fullmaktsendring. Den skal ikke kunne feile fordi
    varslingen gjorde det — konsekvensen av en varslingsfeil er at et menneske
    ikke får en påminnelse, ikke at styringen stopper. Her rives tabellen bort
    under kallet; det skal gi 0 varsler og ingen exception.

    Kontroll: fjern try/except i `varsle_runde_venter`, så blir denne rød.
    """
    c = _conn()
    try:
        # En ekte DB-feil, ikke et tomt resultat. Første utgave av testen kalte
        # bare med et utkast som ikke fantes — da returnerer
        # `mottakere_for_runde` en tom liste, ingenting kaster, og testen var
        # grønn OGSÅ uten try/except. Den målte ingenting.
        #
        # Her avbrytes transaksjonen først, så HVER påfølgende spørring feiler
        # — nøyaktig det en nede database gjør midt i en runde.
        try:
            c.execute("SELECT ugyldig_kolonne_som_ikke_finnes")
        except Exception:                                     # noqa: BLE001
            pass
        n = varsel.varsle_runde_venter(
            c, tenant=TEN, aktor="sys", request_id="r",
            utkast_id="u-hva-som-helst", policy_id="p",
            risikoklasse="UTVIDER", gjenstaar=1)
        assert n == 0, "varslingen skal rapportere 0, ikke kaste"
        c.rollback()
    finally:
        c.close()


@pg
def test_ukjent_kanal_avvises():
    """En feilstavet kanal skal ikke stille slå av varslingen."""
    c = _conn()
    try:
        b = _bruker(c, "feilkanal")
        with pytest.raises(ValueError):
            varsel.sett_kanal(c, tenant=TEN, bruker_id=b, kanal="ingen")
        c.rollback()
    finally:
        c.close()
