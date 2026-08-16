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

        mott = varsel.mottakere_for_runde(c, TEN, uid, 1)
        assert mott == [b], f"forventet bare den som IKKE har attestert, fikk {mott}"

        # …men attestasjonen bandt RUNDE 1. Åpnes runde 2 på samme utkast, må
        # alt attesteres på nytt, og a er igjen en av dem som kan bringe den
        # videre. Kontroll: fjern `a.runde` fra NOT EXISTS, så blir dette rødt
        # — og a ville vært permanent utelukket fra hvert eneste senere varsel
        # på utkastet.
        c.execute(
            "UPDATE aktiveringsrunde SET status='utlopt' WHERE tenant=%s"
            " AND utkast_id=%s AND runde=1", (TEN, uid))
        c.execute(
            "INSERT INTO aktiveringsrunde (tenant,utkast_id,runde,status,"
            "diff_hash,utkast_innholds_hash,base_policy_hash,risikoklasse,"
            "klassifisering_hash,klassifikatorversjon,policyskjema_versjon,"
            "motor_semantikkversjon,deny_all_hash,deny_all_versjon,"
            "pakrevd_antall_godkjennere,utloper)"
            " VALUES (%s,%s,2,'apen','d2','i','b','UTVIDER','k','1','0.2','1',"
            "'dh','1',2,now()+interval '1 hour')", (TEN, uid))
        assert sorted(varsel.mottakere_for_runde(c, TEN, uid, 2)) == sorted([a, b]), \
            "en tidligere rundes attestant ble utelukket fra den nye runden"
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
def test_ny_runde_er_en_ny_hendelse_ikke_en_dublett():
    """En runde som forfalt eller ble avbrutt kan åpnes på nytt for SAMME
    utkast. Da venter noe nytt på godkjenneren, og hun skal se det.

    Uten `hendelse` i unikhetsnøkkelen har runde 2 nøyaktig samme nøkkel som
    runde 1, `ON CONFLICT DO NOTHING` leser den som en retry og sluker den —
    verst hvis hun alt har lest det gamle varselet, for da er innboksen tom og
    ingenting tyder på at noe venter.

    Kontroll: fjern `hendelse` fra unikhetsnøkkelen i migrasjon 026 (eller fra
    `opprett`), så blir denne rød.
    """
    c = _conn()
    try:
        b = _bruker(c, "nyrunde")
        uid = "u-" + secrets.token_hex(6)
        f = dict(tenant=TEN, bruker_id=b, art="attestering_venter",
                 ressurs_type="policyutkast", ressurs_id=uid,
                 tekstnokkel="varsel.attestering_venter")
        assert varsel.opprett(c, hendelse="1", **f) is True
        assert varsel.opprett(c, hendelse="1", **f) is False, \
            "retry av samme runde skal fortsatt slukes"
        # Runde 1 leses; nå åpnes runde 2 på samme utkast.
        vid = c.execute("SELECT id FROM varsel WHERE tenant=%s AND bruker_id=%s"
                        " AND ressurs_id=%s", (TEN, b, uid)).fetchone()[0]
        varsel.merk_lest(c, tenant=TEN, bruker_id=b, varsel_id=vid)
        assert varsel.opprett(c, hendelse="2", **f) is True, \
            "ny runde ble slukt som dublett — godkjenneren får aldri vite"
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
def test_avmeldingen_gjelder_ogsaa_epost_som_alt_staar_i_koe():
    """Codex P2: en avmelding som først virker på neste hendelse, er ingen
    avmelding.

    `_kanal` leses når varselet OPPRETTES, og køen er definert av
    `epost_status='koet'`. Et valg tatt i dag rørte derfor ikke varslene som
    ble laget i går — brukeren fikk nettopp den e-posten hun akkurat sa nei
    til, og hadde ingen måte å stoppe den på.

    Det som ER sendt står urørt: `sendt` er et faktum om fortiden.

    Kontroll: fjern UPDATE-en i `sett_kanal`, så blir denne rød.
    """
    c = _conn()
    try:
        b = _bruker(c, "avmelding")
        uid = "u-" + secrets.token_hex(6)
        for h in ("1", "2"):
            varsel.opprett(c, tenant=TEN, bruker_id=b,
                           art="attestering_venter",
                           ressurs_type="policyutkast", ressurs_id=uid,
                           hendelse=h, tekstnokkel="varsel.attestering_venter")
        # Den ene rakk å bli sendt før hun ombestemte seg.
        c.execute("UPDATE varsel SET epost_status='sendt' WHERE tenant=%s"
                  " AND bruker_id=%s AND hendelse='1'", (TEN, b))

        varsel.sett_kanal(c, tenant=TEN, bruker_id=b, kanal="kun_portal")

        st = dict(c.execute(
            "SELECT hendelse, epost_status FROM varsel WHERE tenant=%s"
            " AND bruker_id=%s AND ressurs_id=%s", (TEN, b, uid)).fetchall())
        assert st["2"] == "ikke_aktuelt", (
            "den køede e-posten overlevde avmeldingen — hun får den likevel")
        assert st["1"] == "sendt", (
            "en allerede sendt e-post ble skrevet om til «ikke aktuelt»; "
            "fortiden kan ikke angres, og statusen er et driftsspor")
        # …og begge står fortsatt i innboksen. Avmeldingen gjelder kanalen,
        # ikke varselet.
        assert varsel.antall_uleste(c, tenant=TEN, bruker_id=b) == 2
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
def test_et_ulest_varsel_faller_ikke_ut_av_siden_det_telles_paa():
    """Telleren og lista skal ikke kunne si to forskjellige ting.

    Med ren `opprettet DESC` ble et gammelt ulest varsel skjovet ut av siden
    sa snart det la `grense` NYERE leste over det — mens `antall_uleste`
    fortsatt talte det. Innboksen sa «1 ulest» og viste ingen. Flaten har
    hverken paginering eller ulest-filter, sa varselet var da uten vei fram i
    det hele tatt: godkjenneren fikk vite at noe ventet, men ikke hva.

    Kontroll: fjern `(lest_ts IS NULL) DESC` fra ORDER BY i `innboks`, så blir
    denne rød — det gamle uleste varselet er ikke på siden.

    Tidsstemplene settes EKSPLISITT. `opprettet` har `DEFAULT now()`, og `now()`
    er transaksjonens starttid: alle radene som lages her ville fått nøyaktig
    samme tidspunkt, «eldre» og «nyere» ville ikke betydd noe, og testen ville
    målt en vilkårlig rekkefølge i stedet for sorteringen.
    """
    c = _conn()
    try:
        eier = _bruker(c, "sidemann")
        gammelt = "u-" + secrets.token_hex(6)
        varsel.opprett(c, tenant=TEN, bruker_id=eier, art="attestering_venter",
                       ressurs_type="policyutkast", ressurs_id=gammelt,
                       tekstnokkel="varsel.attestering_venter")
        c.execute("UPDATE varsel SET opprettet=now() - interval '30 days'"
                  " WHERE tenant=%s AND bruker_id=%s AND ressurs_id=%s",
                  (TEN, eier, gammelt))
        # Fem NYERE varsler som alle er lest. Med grense=3 fyller de siden.
        for i in range(5):
            rid = "u-" + secrets.token_hex(6)
            varsel.opprett(c, tenant=TEN, bruker_id=eier,
                           art="attestering_venter",
                           ressurs_type="policyutkast", ressurs_id=rid,
                           tekstnokkel="varsel.attestering_venter")
            c.execute("UPDATE varsel SET lest_ts=now(),"
                      " opprettet=now() - make_interval(days => %s)"
                      " WHERE tenant=%s AND bruker_id=%s AND ressurs_id=%s",
                      (i, TEN, eier, rid))

        assert varsel.antall_uleste(c, tenant=TEN, bruker_id=eier) == 1
        side = varsel.innboks(c, tenant=TEN, bruker_id=eier, grense=3)
        assert len(side) == 3
        assert side[0]["ressurs_id"] == gammelt, \
            "det uleste varselet lå ikke øverst"
        assert not side[0]["lest"]
        # Resten av siden er de nyeste leste — prioriteringen bytter ikke ut
        # sorteringen, den kommer FORAN den.
        assert all(v["lest"] for v in side[1:])
        assert [v["opprettet"] for v in side[1:]] \
            == sorted((v["opprettet"] for v in side[1:]), reverse=True)
    finally:
        c.close()


@pg
def test_flere_uleste_enn_siden_rommer_kan_likevel_toemmes():
    """Er de uleste flere enn `grense`, viser siden `grense` uleste.

    Det er ikke en løgn, men heller ikke alt — så veien ut må finnes: merkes
    ett som lest, faller det ned under de uleste, og det neste kommer til syne.
    Før fantes ingen slik vei: de uleste utenfor siden var uoppnåelige uansett
    hva mottakeren gjorde.
    """
    c = _conn()
    try:
        eier = _bruker(c, "koe")
        ider = []
        for i in range(4):
            rid = "u-" + secrets.token_hex(6)
            varsel.opprett(c, tenant=TEN, bruker_id=eier,
                           art="attestering_venter",
                           ressurs_type="policyutkast", ressurs_id=rid,
                           tekstnokkel="varsel.attestering_venter")
            # Eksplisitt tid: `now()` er transaksjonens starttid, så uten dette
            # ville alle fire delt tidspunkt og «den eldste faller ut» vært en
            # vilkårlighet i stedet for en sortering.
            c.execute("UPDATE varsel SET opprettet=now() - make_interval(days => %s)"
                      " WHERE tenant=%s AND bruker_id=%s AND ressurs_id=%s",
                      (i, TEN, eier, rid))
            ider.append(rid)

        side = varsel.innboks(c, tenant=TEN, bruker_id=eier, grense=3)
        assert len(side) == 3 and all(not v["lest"] for v in side)
        skjult = [r for r in ider if r not in {v["ressurs_id"] for v in side}]
        assert len(skjult) == 1, "testen måler ikke det den tror"
        assert skjult[0] == ider[-1], "det eldste uleste skulle vært det skjulte"

        # Mottakeren rydder ett av dem. Da skal det skjulte komme fram.
        varsel.merk_lest(c, tenant=TEN, bruker_id=eier,
                         varsel_id=side[0]["id"])
        etter = varsel.innboks(c, tenant=TEN, bruker_id=eier, grense=3)
        assert skjult[0] in {v["ressurs_id"] for v in etter}, \
            "et ulest varsel var uoppnåelig uansett hva mottakeren gjorde"
    finally:
        c.close()


@pg
def test_varsling_velter_aldri_handlingen():
    """Den viktigste testen her.

    En aktiveringsrunde er en fullmaktsendring. Den skal ikke kunne feile fordi
    varslingen gjorde det — konsekvensen av en varslingsfeil er at et menneske
    ikke får en påminnelse, ikke at styringen stopper. Her er transaksjonen alt
    abortert når varslingen kalles; det skal gi 0 varsler og ingen exception.

    `c.rollback()` til slutt er ikke opprydding, det er en ASSERT: kalleren må
    fortsatt komme seg ut. Da savepointet ble skrevet med `conn.transaction()`,
    var det nettopp denne linja som falt — psycopg teller opp
    transaksjonsstabelen før SAVEPOINT sendes, så entringen på en abortert
    forbindelse etterlot telleren hevet og `rollback()` kastet «Explicit
    rollback() forbidden within a Transaction context». Varslingen ødela altså
    kallerens ryddevei i stedet for hans neste setning: samme brudd på løftet,
    ny dør. Derfor skrives savepointet nå som ren SQL.

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
            utkast_id="u-hva-som-helst", runde=1, policy_id="p",
            risikoklasse="UTVIDER", gjenstaar=1)
        assert n == 0, "varslingen skal rapportere 0, ikke kaste"
        c.rollback()
    finally:
        c.close()


@pg
def test_varslingsfeil_lar_kallerens_transaksjon_leve():
    """Codex P1, og den egentlige versjonen av testen over.

    Den forrige målte bare at Python ikke kastet — og skjulte resten ved å
    rulle tilbake med én gang. Men kalleren EIER transaksjonen og deler
    forbindelse med varslingen: feiler en spørring her, står PostgreSQL i
    abortert tilstand, og kallerens neste setning (og `commit()`) feiler
    uansett hvor pent unntaket ble svelget. Varslingen ville altså veltet
    nøyaktig den fullmaktsendringen den lovte å ikke røre.

    Her rives `varsel`-tabellen bort MIDT i en ellers sunn transaksjon, og de
    to tingene som faktisk betyr noe måles: forbindelsen er brukbar etterpå,
    og kallerens eget arbeid står igjen.

    MUTASJONEN SOM DREPER DENNE: fjern `with conn.transaction()` fra
    `varsle_runde_venter` (da dør første assert), eller bytt savepointet mot
    `conn.rollback()` (da dør den andre — kallerens utkast forsvinner).
    """
    ten = TEN + "-sp"
    c = _conn(ten)
    try:
        b = _bruker(c, "savepoint", tenant=ten)
        uid = "u-" + secrets.token_hex(6)
        # Kallerens eget arbeid, gjort FØR varslingen — som i aktiveringsflyten.
        c.execute(
            "INSERT INTO policyutkast (tenant,utkast_id,policy_id,innhold,"
            "opprettet_av) VALUES (%s,%s,'p','{}'::jsonb,%s)", (ten, uid, b))
        # Riv bordet under varslingen. DDL-en er del av denne transaksjonen og
        # forsvinner med rollbacken i `finally` — den lekker ikke til andre.
        c.execute("ALTER TABLE varsel RENAME TO varsel_borte_under_test")
        n = varsel.varsle_runde_venter(
            c, tenant=ten, aktor="test", request_id="r", utkast_id=uid,
            runde=1, policy_id="p", risikoklasse="UTVIDER", gjenstaar=1)
        assert n == 0, "varslingen skal rapportere 0, ikke kaste"
        assert c.execute("SELECT 1").fetchone()[0] == 1, (
            "transaksjonen er abortert — kalleren kan verken skrive videre "
            "eller committe, og varselet har veltet handlingen")
        assert c.execute(
            "SELECT count(*) FROM policyutkast WHERE tenant=%s AND utkast_id=%s",
            (ten, uid)).fetchone()[0] == 1, (
            "kallerens eget arbeid ble rullet tilbake av varslingen — en "
            "savepoint skal angre varselet, ikke handlingen")
    finally:
        c.rollback()
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
