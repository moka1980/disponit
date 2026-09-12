"""Porten for 192: et firma kan registrere seg selv.

HAKEN SOM MÅTTE LØSES: en helt ny bruker har ingen medlemskap, og
`_opprett_sesjon` avviste henne med `ingen_tilgang` FØR hun rakk å
registrere noe — hun kunne ikke bli kunde fordi hun ikke var kunde.

Den dyreste fella i designet er ikke registreringen, men DET SOM SKJER
ETTERPÅ: registranten har et ekte medlemskap på `_registrering` (det er
det som gir henne scopet). Blir den stående, har hun TO medlemskap ved
neste innlogging, og `_firma_for_bruker` svarer `firma_ikke_valgt` — hun
ville blitt låst ute av firmaet hun nettopp opprettet, av en rad som hadde
gjort jobben sin. Feilen ville ikke vist seg ved registreringen, men ved
neste pålogging, langt fra åstedet.

MUTASJONENE SOM DREPER DISSE:
  * fjern DELETE-en av registrantraden i `firma_selvregistrer` → port 3
  * la døra godta enhver kontekst                                → port 2
  * fjern taket                                                  → port 4
  * la `firma_bootstrap_policy` skrive over en eksisterende serie → port 7
"""
import secrets

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, app, klient,  # noqa: F401
                       migrator, miljo, pg)
from .test_m37 import _sett_kontekst

REG = "_registrering"


def _bruker(c) -> str:
    _sett_kontekst(c, "t-selvreg-oppsett")
    bid = c.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://selvreg.test', %s) RETURNING bruker_id",
        ("s-" + secrets.token_hex(6),)).fetchone()[0]
    c.commit()
    return bid


def _slug() -> str:
    return "t-sreg-" + secrets.token_hex(3)


def _registrer(c, tenant, bid, navn="Nytt Firma AS", dogn=30):
    _sett_kontekst(c, REG)
    frist = c.execute("SELECT firma_selvregistrer(%s,%s,NULL,%s,%s)",
                      (tenant, navn, bid, dogn)).fetchone()[0]
    c.commit()
    return frist


def _medlemskap(c, bid) -> dict[str, list[str]]:
    """Alle medlemskap for en bruker, kryss-tenant.

    `brukermedlemskap` har FORCE RLS, så et enkelt SELECT uten kontekst gir
    null rader — også for migrator. Tenantene hentes derfor fra det RLS-frie
    speilet (189), og rollene leses per tenant med kontekst satt. Det er
    samme grunn til at speilet finnes i det hele tatt.
    """
    c.execute("SELECT set_config('disponit.tenant','',true)")
    tenanter = [r[0] for r in c.execute(
        "SELECT tenant FROM bruker_tenant WHERE bruker_id=%s",
        (bid,)).fetchall()]
    ut = {}
    for t in tenanter:
        _sett_kontekst(c, t)
        rad = c.execute("SELECT roller FROM brukermedlemskap"
                        " WHERE tenant=%s AND bruker_id=%s",
                        (t, bid)).fetchone()
        if rad:
            ut[t] = rad[0]
    return ut


# ---------------------------------------------------------------------------
# 1-2. Døra, og låsen som ikke er scopet.
# ---------------------------------------------------------------------------

@pg
def test_registrering_gir_firma_og_admin_i_samme_transaksjon(migrator):
    bid, t = _bruker(migrator), _slug()
    frist = _registrer(migrator, t, bid)
    assert frist is not None

    _sett_kontekst(migrator, t)
    rad = migrator.execute("SELECT navn, status FROM firma_hent(%s)",
                           (t,)).fetchone()
    assert rad == ("Nytt Firma AS", "prove")
    assert _medlemskap(migrator, bid).get(t) == ["admin"], (
        "et firma uten medlem er et firma ingen kommer inn i")


@pg
def test_doera_krever_registreringskonteksten_ikke_bare_scopet(migrator):
    """`krev_tenantkontekst` binder tenanten; dette binder FULLMAKTEN.

    En kundesesjon står aldri i `_registrering` — `firma_tenant_form` (190)
    forbyr understrek i et firmanavn — så konteksten er en lås scopet alene
    ikke kan åpne.
    """
    bid = _bruker(migrator)
    for kontekst in ("t-en-ekte-kunde", "", "_plattform"):
        _sett_kontekst(migrator, kontekst)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute("SELECT firma_selvregistrer(%s,'X',NULL,%s,30)",
                             (_slug(), bid))
        migrator.rollback()


# ---------------------------------------------------------------------------
# 3. Fella: registrantraden må dø, ellers låses hun ute ved NESTE innlogging.
# ---------------------------------------------------------------------------

@pg
def test_registrantraden_ryddes_saa_hun_kommer_inn_igjen(migrator):
    from api.sesjon import _firma_for_bruker

    bid, t = _bruker(migrator), _slug()
    assert migrator.execute("SELECT registrant_medlemskap(%s)",
                            (bid,)).fetchone()[0] is True
    migrator.commit()
    assert list(_medlemskap(migrator, bid)) == [REG]

    _registrer(migrator, t, bid)
    etter = _medlemskap(migrator, bid)
    assert list(etter) == [t], (
        f"registrantraden står igjen: {sorted(etter)} — neste innlogging "
        "ville svart firma_ikke_valgt og låst henne ute av sitt eget firma")

    # Den avgjørende følgen, målt gjennom den ekte døra.
    migrator.execute("SELECT set_config('disponit.tenant','',true)")
    assert _firma_for_bruker(migrator, bid, _Ident()) == t


# ---------------------------------------------------------------------------
# 4-6. Taket, identiteten, og registrantdøras egen forutsetning.
# ---------------------------------------------------------------------------

@pg
def test_taket_teller_levende_firmaer(migrator):
    """Den som registrerer tre og stenger ett, skal kunne registrere igjen.
    Taket verner mot en fabrikk, ikke mot en som ombestemmer seg."""
    bid = _bruker(migrator)
    tak = migrator.execute("SELECT firma_registreringstak()").fetchone()[0]
    slugger = [_slug() for _ in range(tak)]
    for t in slugger:
        _registrer(migrator, t, bid)

    _sett_kontekst(migrator, REG)
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        migrator.execute("SELECT firma_selvregistrer(%s,'En til',NULL,%s,30)",
                         (_slug(), bid))
    migrator.rollback()

    # Steng ett — da skal det gå igjen.
    _sett_kontekst(migrator, slugger[0])
    migrator.execute("SELECT firma_sett_status(%s,'stengt','kari')",
                     (slugger[0],))
    migrator.commit()
    _registrer(migrator, _slug(), bid, navn="Etter stenging AS")


@pg
def test_ukjent_registrant_avvises(migrator):
    _sett_kontekst(migrator, REG)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrator.execute("SELECT firma_selvregistrer(%s,'X',NULL,%s,30)",
                         (_slug(), "bid_finnes_ikke"))
    migrator.rollback()


@pg
def test_registrantrad_oppstaar_aldri_for_en_som_har_firma(migrator):
    """Døras egen forutsetning, ikke kallerens ansvar: to medlemskap ville
    gitt `firma_ikke_valgt` ved neste innlogging."""
    bid, t = _bruker(migrator), _slug()
    _registrer(migrator, t, bid)
    assert migrator.execute("SELECT registrant_medlemskap(%s)",
                            (bid,)).fetchone()[0] is False
    migrator.commit()
    assert list(_medlemskap(migrator, bid)) == [t]


# ---------------------------------------------------------------------------
# 7. Bootstrap-policyen kan aldri røre en serie som er inne i lineagen.
# ---------------------------------------------------------------------------

@pg
def test_bootstrap_policy_nekter_naar_det_finnes_en_historikk(migrator):
    """Vernene i `policyregister.registrer` — advisory-låsene, prøven mot
    `policyaktivering` — finnes for tenanter som ALLEREDE har en historikk.
    Denne døra gjenskaper dem ikke; den nekter å komme i den situasjonen.
    """
    bid, t = _bruker(migrator), _slug()
    _registrer(migrator, t, bid)
    _sett_kontekst(migrator, t)
    migrator.execute(
        "SELECT firma_bootstrap_policy(%s,'p-test','1.0.0',"
        " repeat('a',64),'utkast','{}'::jsonb)", (t,))
    migrator.commit()

    _sett_kontekst(migrator, t)
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        migrator.execute(
            "SELECT firma_bootstrap_policy(%s,'p-annen','1.0.0',"
            " repeat('b',64),'utkast','{}'::jsonb)", (t,))
    migrator.rollback()


# ---------------------------------------------------------------------------
# 8. Plattformrollen kan ikke stå på en kunderad.
# ---------------------------------------------------------------------------

@pg
def test_plattformrollen_kan_ikke_staa_paa_en_kunderad(migrator):
    """En rolle som gjelder HELE plattformen skal ikke kunne ligge i en
    kundes medlemskapsrad. Ingen dør skriver `brukermedlemskap` i dag, så
    dette er forskjellen mellom «en feil noen gjør» og «en feil som ikke
    kan gjøres» — den dagen rolletildeling får en flate.
    """
    bid = _bruker(migrator)
    _sett_kontekst(migrator, "t-en-kunde")
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "INSERT INTO brukermedlemskap (tenant, bruker_id, roller)"
            " VALUES ('t-en-kunde',%s,ARRAY['plattformeier'])", (bid,))
    migrator.rollback()

    # Positiv kontroll: på plattformtenanten er den lovlig.
    _sett_kontekst(migrator, "_plattform")
    migrator.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller)"
        " VALUES ('_plattform',%s,ARRAY['plattformeier'])", (bid,))
    migrator.rollback()


# ---------------------------------------------------------------------------
# 9. Slugen — navnet firmaet faktisk kjenner igjen.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("navn,forventet", [
    ("Fjordlys Elektro AS", "fjordlys-elektro-as"),
    ("Øre & Nese AS", "oere-nese-as"),
    ("Ærlig Ås Håndverk", "aerlig-aas-haandverk"),
    ("  Mange   mellomrom  ", "mange-mellomrom"),
    ("Tegn!!!Bare###Tegn", "tegn-bare-tegn"),
    ("Café Solberg", "cafe-solberg"),
    ("---", ""),
])
def test_slugen_beholder_navnet_firmaet_kjenner(navn, forventet):
    """Æ/Ø/Å translittereres FØR unicode-normaliseringen. `NFKD` splitter
    ikke Ø — den har ingen dekomponering — så «Øre AS» ville blitt «re-as»
    uten det, altså et navn firmaet ikke kjenner igjen."""
    from api.firmaregistrering import slug_av

    assert slug_av(navn) == forventet


def test_kollisjon_gir_neste_kandidat_ikke_en_feil():
    """To firmaer kan hete det samme, og den andre skal ikke måtte finne
    på et nytt navn."""
    from api.firmaregistrering import _kandidater

    k = list(_kandidater("fjordlys-as"))
    assert k[0] == "fjordlys-as" and k[1] == "fjordlys-as-2"
    assert len(set(k)) == len(k)
    assert all(len(s) <= 63 for s in k)


class _Ident:
    issuer = "https://selvreg.test"

    def __init__(self):
        self.sub = "brems-" + secrets.token_hex(8)


# ---------------------------------------------------------------------------
# 10. HELE VEIEN, gjennom HTTP-døra. Den som avgjør om målet er nådd.
# ---------------------------------------------------------------------------

def _registrantokt(migrator, bid):
    """En ekte browsersesjon på `_registrering`, slik callbacken lager den."""
    from api import sesjon as sesjonmodul

    migrator.execute("SELECT registrant_medlemskap(%s)", (bid,))
    migrator.commit()
    _sett_kontekst(migrator, REG)
    ver = migrator.execute(
        "SELECT authz_version FROM brukermedlemskap WHERE tenant=%s"
        " AND bruker_id=%s", (REG, bid)).fetchone()[0]
    cookie, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    migrator.execute(
        "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
        " authz_snapshot, csrf_hash, utloper)"
        " VALUES (%s,%s,%s,%s,%s, now() + interval '10 hours')",
        (sesjonmodul._hash(cookie), REG, bid, ver, sesjonmodul._hash(csrf)))
    migrator.commit()
    return cookie, csrf


@pg
def test_hele_veien_en_bedrift_registrerer_seg_selv(miljo, migrator, klient):
    """EIERS MÅL, målt: «man kan lett registrere en bedrift og sette policy
    og resten skal skje automatisk.»

    Før denne ruten kostet det fem steg, tre av dem som root på verten.
    Her er alt som skjer: ett POST-kall.
    """
    from api import sesjon as sesjonmodul

    from api.firmaregistrering import slug_av

    bid = _bruker(migrator)
    cookie, csrf = _registrantokt(migrator, bid)
    # NAVNET MÅ VÆRE UNIKT PER KJØRING. Første utgave brukte «Øre & Nese AS»
    # fast, og andre kjøring fikk `oere-nese-as-2` — kollisjonshåndteringen
    # gjorde jobben sin, men porten målte en tom navneplass den ikke hadde
    # satt. Æ/Ø/Å-behandlingen måles av `test_slugen_beholder_navnet` som
    # ren funksjon; her måles at RUTEN bruker den.
    navn = f"Øre & Nese {secrets.token_hex(3)} AS"

    r = klient.post("/v1/firma/registrer",
                    json={"navn": navn, "orgnummer": "923609016",
                          "bransje": "tjenestebedrift"},
                    cookies={sesjonmodul.C_SESJON: cookie,
                             sesjonmodul.C_CSRF: csrf},
                    headers={"X-Disponit-CSRF": csrf,
                             "Idempotency-Key": secrets.token_hex(16)})
    assert r.status_code in (200, 201), r.text
    svar = r.json()
    tenant = svar["tenant"]
    assert tenant == slug_av(navn), f"slugen ble {tenant!r}"
    assert tenant.startswith("oere-nese-"), "Ø ble ikke translitterert"
    assert svar["prove_utloper"]

    # 1. Firmaet finnes, med prøveperiode.
    _sett_kontekst(migrator, tenant)
    rad = migrator.execute(
        "SELECT navn, orgnummer, status FROM firma_hent(%s)",
        (tenant,)).fetchone()
    assert rad == (navn, "923609016", "prove")

    # 2. Hun er admin der, og registrantraden er borte.
    assert _medlemskap(migrator, bid) == {tenant: ["admin"]}

    # 3. Nøkkelen finnes — uten den kan ingen modul kryptere noe.
    _sett_kontekst(migrator, tenant)
    assert migrator.execute(
        "SELECT count(*) FROM tenant_nokler WHERE tenant=%s AND"
        " wrapped_dek IS NOT NULL", (tenant,)).fetchone()[0] >= 1

    # 4. Bransjemalen er AKTIV. Det er dette «resten skjer automatisk» betyr:
    #    agenten har fullmakter uten at noen kjørte init-tenant.sh.
    _sett_kontekst(migrator, tenant)
    aktiv = migrator.execute(
        "SELECT policy_id, aktiv, aktiveringskilde FROM policyer"
        " WHERE tenant=%s AND aktiv", (tenant,)).fetchall()
    assert len(aktiv) == 1, f"ingen eller flere aktive policyer: {aktiv}"
    assert aktiv[0][0] == "tjenestebedrift-no"
    assert aktiv[0][2] == "bootstrap"


@pg
def test_en_kundesesjon_naar_aldri_registreringsruten(miljo, migrator, klient):
    """Scopet er lås 1, konteksten er lås 2. En kundesesjon har ikke scopet
    i det hele tatt — men porten måler at ruten sier nei, ikke at den
    tilfeldigvis feiler et annet sted."""
    from api import sesjon as sesjonmodul

    bid, t = _bruker(migrator), _slug()
    _registrer(migrator, t, bid)          # hun er nå admin i et ekte firma
    _sett_kontekst(migrator, t)
    ver = migrator.execute(
        "SELECT authz_version FROM brukermedlemskap WHERE tenant=%s"
        " AND bruker_id=%s", (t, bid)).fetchone()[0]
    cookie, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    migrator.execute(
        "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
        " authz_snapshot, csrf_hash, utloper)"
        " VALUES (%s,%s,%s,%s,%s, now() + interval '10 hours')",
        (sesjonmodul._hash(cookie), t, bid, ver, sesjonmodul._hash(csrf)))
    migrator.commit()

    r = klient.post("/v1/firma/registrer",
                    json={"navn": "Snik AS", "bransje": "netthandel"},
                    cookies={sesjonmodul.C_SESJON: cookie,
                             sesjonmodul.C_CSRF: csrf},
                    headers={"X-Disponit-CSRF": csrf,
                             "Idempotency-Key": secrets.token_hex(16)})
    assert r.status_code in (401, 403), r.text


@pg
def test_feil_orgnummer_sier_orgnummer_ikke_tak_naadd(miljo, migrator, klient):
    """CodeRabbits funn, og grunnen til at det ikke er kosmetikk.

    `firma_registrer` ville reist en CheckViolation på MOD-11 — altså samme
    unntaksklasse som taket — og ruten ville svart «taket på 3 firmaer er
    nådd» til en kunde som bare hadde skrevet organisasjonsnummeret feil.
    """
    from api import sesjon as sesjonmodul

    bid = _bruker(migrator)
    cookie, csrf = _registrantokt(migrator, bid)
    r = klient.post("/v1/firma/registrer",
                    json={"navn": f"Feil Orgnr {secrets.token_hex(3)} AS",
                          "orgnummer": "12345", "bransje": "netthandel"},
                    cookies={sesjonmodul.C_SESJON: cookie,
                             sesjonmodul.C_CSRF: csrf},
                    headers={"X-Disponit-CSRF": csrf,
                             "Idempotency-Key": secrets.token_hex(16)})
    assert r.status_code == 400, r.text
    assert "orgnummer" in r.json().get("detalj", ""), r.text
