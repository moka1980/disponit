"""038 — outbox-generaliseringen: opphav, saker og idempotenslageret.

Outboxen var M-37-forankret på skjemanivå; her prøves at oppmykingen ikke
mykner noe annet: CHECK-en dekker begge opphavskombinasjonene uttømmende
(portene 1–3), `opprinnelse` er immutabel (4), backfillen traff alle
eksisterende rader (5 — bevist i pr008s rebuild-tester som sår 007-æra-
rader og migrerer forbi 038), DEFAULT er fjernet (6), runtime har ingen
INSERT (7), og M-37-veien er urørt ende-til-ende (8 — regresjonsporten
ER de eksisterende m37-/pr007-/pr012-suitene, som kjører uendret mot 038;
arbeiderveien går nå gjennom `opprett_reparasjonsoppdrag`).

Sakene (23–27): `sikre_sak_for_oppdrag` er idempotent fordi UNIK-indeksen
gjør den det; terminale saker gjenbrukes aldri; `oppdrag.unntak_id`
forblir NULL gjennom hele sakslivsløpet.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
import secrets

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, TENANT, migrator, miljo  # noqa: F401
from .test_m37 import _lag_sak, _sett_kontekst

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _rt():
    from db.pg import koble
    return koble(DSN)


def _beslutningsgrunnlag(migrator_):
    """En TILLAT-loggpost + kryptert payload — det en bestilling etterlater."""
    from db import kryptering
    _sett_kontekst(migrator_, TENANT)
    logg = migrator_.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'test','api_token','ih','p@1.0.0/x.y','TILLAT','[]',%s)"
        " RETURNING id", (TENANT, secrets.token_hex(8))).fetchone()[0]
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator_, TENANT)
    ct, nonce = kryptering.krypter(
        dek, {"mal_url": "https://k.example/", "kravsett": "wcag21_aa",
              "omfang": "enkeltside"}, TENANT, key_id)
    migrator_.commit()
    return logg, ct, key_id, nonce


def _beslutningsoppdrag(rt, migrator_):
    logg, ct, key_id, nonce = _beslutningsgrunnlag(migrator_)
    _sett_kontekst(rt, TENANT)
    oid = rt.execute(
        "SELECT opprett_beslutningsoppdrag(%s,%s,'kontroll.wcag.nettsted',"
        "'kontroll.wcag.nettsted','m_wcag_audit',%s,%s,%s,"
        "now()+interval '30 minutes',now()+interval '30 minutes')",
        (TENANT, logg, ct, key_id, nonce)).fetchone()[0]
    rt.commit()
    return int(oid), logg


@pg
def test_opphavskombinasjonene_er_uttommende(migrator):
    """Portene 1–3: M-37-oppdrag uten trio avvises; beslutningsoppdrag med
    unntak_id avvises; beslutningsoppdrag uten beslutnings-FK avvises."""
    from db import kryptering
    sak, logg = _lag_sak(migrator, TENANT)
    _sett_kontekst(migrator, TENANT)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, TENANT)
    ct, nonce = kryptering.krypter(dek, {"x": 1}, TENANT, key_id)
    basis = ("payload_kryptert, key_id, nonce, utforelsesfrist,"
             " evidensfrist")
    verdier = "%s,%s,%s,now()+interval '1 hour',now()+interval '1 day'"
    # 1: reparasjon uten trio
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            f"INSERT INTO oppdrag (opprinnelse, tenant, oppdragstype,"
            f" handling, eiermodul, {basis}) VALUES ('m37_reparasjon',%s,"
            f"'reinnsending','purring.send','e:r',{verdier})",
            (TENANT, ct, key_id, nonce))
    migrator.rollback()
    # 2: beslutning MED unntak_id. BEFORE-vakta (koblingsvakta, som nå
    # kjenner opphavet) kan nå å si nei før CHECK-en — begge er lagringens
    # avvisning, og loggposten her er dessuten ikke en TILLAT.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises((psycopg.errors.CheckViolation,
                        psycopg.errors.RaiseException)):
        migrator.execute(
            f"INSERT INTO oppdrag (opprinnelse, tenant, unntak_id,"
            f" beslutning_loggpost_id, oppdragstype, handling, eiermodul,"
            f" {basis}, koblingsstatus) VALUES ('beslutning',%s,%s,%s,"
            f"'reinnsending','purring.send','e:r',{verdier},'KOBLET')",
            (TENANT, sak, logg, ct, key_id, nonce))
    migrator.rollback()
    # 3: beslutning UTEN beslutnings-FK — vakta sier nei (EXISTS mot NULL
    # er tomt) før CHECK-en; begge er lagringens avvisning.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises((psycopg.errors.CheckViolation,
                        psycopg.errors.RaiseException)):
        migrator.execute(
            f"INSERT INTO oppdrag (opprinnelse, tenant, oppdragstype,"
            f" handling, eiermodul, {basis}) VALUES ('beslutning',%s,"
            f"'reinnsending','purring.send','e:r',{verdier})",
            (TENANT, ct, key_id, nonce))
    migrator.rollback()
    # 6: DEFAULT er fjernet — INSERT uten opprinnelse feiler
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.NotNullViolation):
        migrator.execute(
            f"INSERT INTO oppdrag (tenant, unntak_id, loggpost_id,"
            f" repair_operation_id, oppdragstype, handling, eiermodul,"
            f" {basis}) VALUES (%s,%s,%s,%s,'reinnsending','purring.send',"
            f"'e:r',{verdier})",
            (TENANT, sak, logg, secrets.token_hex(16), ct, key_id, nonce))
    migrator.rollback()


@pg
def test_opprinnelse_er_immutabel_og_runtime_uten_insert(migrator):
    """Port 4 + 7. Kontroll: fjern `oppdrag_opprinnelse_immutable`-triggeren,
    så blir første halvdel grønn på feil grunnlag."""
    rt = _rt()
    try:
        oid, _ = _beslutningsoppdrag(rt, migrator)
        _sett_kontekst(migrator, TENANT)
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute("UPDATE oppdrag SET opprinnelse="
                             "'m37_reparasjon' WHERE tenant=%s AND id=%s",
                             (TENANT, oid))
        migrator.rollback()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("INSERT INTO oppdrag (opprinnelse, tenant,"
                       " oppdragstype, handling, eiermodul, payload_kryptert,"
                       " key_id, nonce, utforelsesfrist, evidensfrist)"
                       " VALUES ('beslutning',%s,'t','h','e','\\x00','k',"
                       "'\\x00',now(),now())", (TENANT,))
        rt.rollback()
    finally:
        rt.close()


@pg
def test_sikre_sak_er_idempotent_og_terminal_gjenbrukes_aldri(migrator):
    """Portene 23–27. Kontroll: fjern UNIK-indeksen
    `en_apen_sak_per_oppdrag_arsak`, så tåler ikke kappløpsgrenen lenger
    to samtidige — og gjenbruksgrenen mister beviset sitt."""
    rt = _rt()
    try:
        oid, logg = _beslutningsoppdrag(rt, migrator)
        _sett_kontekst(rt, TENANT)
        s1 = rt.execute("SELECT sikre_sak_for_oppdrag(%s,%s,'evidensfrist',"
                        "'reaper','r1')", (TENANT, oid)).fetchone()[0]
        s2 = rt.execute("SELECT sikre_sak_for_oppdrag(%s,%s,'evidensfrist',"
                        "'reaper','r2')", (TENANT, oid)).fetchone()[0]
        rt.commit()
        assert s1 == s2, "gjentatt kall ga ny sak (port 25)"
        # Saken arver beslutningsloggposten som lineage, og en ANNEN
        # årsaksfamilie får sin EGEN sak (26-motstykket).
        _sett_kontekst(migrator, TENANT)
        rad = migrator.execute(
            "SELECT loggpost_id, sakstype, arsak FROM unntak WHERE id=%s",
            (s1,)).fetchone()
        assert rad == (logg, "normal", "evidensfrist"), rad
        s3 = rt.execute("SELECT sikre_sak_for_oppdrag(%s,%s,'sikkerhet',"
                        "'kvitteringsport','r3')", (TENANT, oid)).fetchone()[0]
        rt.commit()
        assert s3 != s1
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT sakstype, prioritet FROM unntak WHERE id=%s",
            (s3,)).fetchone() == ("sikkerhet", "hoy")
        # Terminal sak gjenbrukes aldri: løs den første (via statemaskinens
        # lovlige vei ny→under_behandling→løst) → nytt kall gir NY.
        migrator.execute("UPDATE unntak SET status='under_behandling'"
                         " WHERE id=%s", (s1,))
        migrator.execute("UPDATE unntak SET status='løst' WHERE id=%s",
                         (s1,))
        migrator.commit()
        s4 = rt.execute("SELECT sikre_sak_for_oppdrag(%s,%s,'evidensfrist',"
                        "'reaper','r4')", (TENANT, oid)).fetchone()[0]
        rt.commit()
        assert s4 not in (s1, s3), "terminal sak ble gjenbrukt (port 26)"
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute("SELECT status FROM unntak WHERE id=%s",
                                (s1,)).fetchone() == ("løst",), \
            "den terminale saken ble rørt"
        # Port 27: oppdragets unntak_id forble NULL gjennom hele livsløpet.
        assert migrator.execute(
            "SELECT unntak_id FROM oppdrag WHERE tenant=%s AND id=%s",
            (TENANT, oid)).fetchone() == (None,)
        migrator.rollback()
    finally:
        rt.close()


@pg
def test_to_samtidige_sikre_sak_gir_noyaktig_en(migrator):
    """Kappløpshalvdelen av port 25: indeksen serialiserer; taperen leser
    vinnerens rad i unique_violation-grenen."""
    import threading
    rt0 = _rt()
    try:
        oid, _ = _beslutningsoppdrag(rt0, migrator)
    finally:
        rt0.close()
    resultater = []

    def prov(n):
        rt = _rt()
        try:
            _sett_kontekst(rt, TENANT)
            resultater.append(rt.execute(
                "SELECT sikre_sak_for_oppdrag(%s,%s,'evidensfrist',"
                "'reaper',%s)", (TENANT, oid, f"r{n}")).fetchone()[0])
            rt.commit()
        finally:
            rt.close()

    t1 = threading.Thread(target=prov, args=(1,))
    t2 = threading.Thread(target=prov, args=(2,))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert len(set(resultater)) == 1, resultater
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM unntak WHERE tenant=%s AND oppdrag_id=%s"
        " AND arsak='evidensfrist'", (TENANT, oid)).fetchone()[0]
    migrator.rollback()
    assert n == 1, n


@pg
def test_bestillingsidempotens_er_immutabel_ogsaa_mot_delete(migrator):
    """§2.3: UPDATE og DELETE avvises — en slettbar rad ville latt en nøkkel
    gjenbrukes med ny intensjon (V4-1 omgått via en annen skrivevei)."""
    _sett_kontekst(migrator, TENANT)
    nokkel = "n-" + secrets.token_hex(8)
    migrator.execute(
        "INSERT INTO bestilling_idempotens (tenant, idempotensnokkel,"
        " intensjonshash, beslutning) VALUES (%s,%s,%s,'stopp')",
        (TENANT, nokkel, "a" * 64))
    migrator.commit()
    for sql in ["UPDATE bestilling_idempotens SET intensjonshash=%s"
                " WHERE idempotensnokkel=%s",
                "DELETE FROM bestilling_idempotens WHERE"
                " idempotensnokkel=%s"]:
        _sett_kontekst(migrator, TENANT)
        params = (("b" * 64, nokkel) if sql.count("%s") == 2 else (nokkel,))
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(sql, params)
        migrator.rollback()


# ==========================================================================
# Bestillingsveien over HTTP (§6, portene 9–22)
# ==========================================================================

import json as _json
import yaml as _yaml

from .test_api import POLICIES, app, dekker, klient  # noqa: F401


def _adminsesjon(tenant=TENANT, sub=None, roller="admin"):
    """Browserøkt med `admin`-rollen (bærer bestilling:opprett) — speiler
    `_domeneaktorsesjon` i test_pr014b_artefakt_api."""
    from api import sesjon as sesjonmodul
    from db.pg import koble, sett_kontekst
    from .test_pr010_db import _identitet
    cookie, csrf = secrets.token_hex(24), secrets.token_hex(24)
    m = koble(MIGRATOR_DSN)
    try:
        sett_kontekst(m, tenant, "sys", "r0")
        bid = _identitet(m, sub=f"{tenant}-{sub or secrets.token_hex(3)}")
        m.execute("INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
                  " VALUES (%s,%s,%s)"
                  " ON CONFLICT (tenant,bruker_id) DO UPDATE SET"
                  " roller=EXCLUDED.roller", (tenant, bid, [roller]))
        ver = m.execute("SELECT authz_version FROM brukermedlemskap"
                        " WHERE tenant=%s AND bruker_id=%s",
                        (tenant, bid)).fetchone()[0]
        m.execute(
            "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
            " authz_snapshot, csrf_hash, opprettet, siste_bruk, utloper,"
            " tilbakekalt)"
            " VALUES (%s,%s,%s,%s,%s, now(), now(),"
            " now()+interval '12 hour', false)",
            (sesjonmodul._hash(cookie), tenant, bid, ver,
             sesjonmodul._hash(csrf)))
        m.commit()
    finally:
        m.close()
    return cookie, csrf


def _wcag_policy(migrator_, *, med_handling=True,
                 ved_brudd="unntakskø", tillatt_for=("bestiller",)):
    """Aktiv policy for TENANT — bransjemalen + wcag-handlingen (vilkaar
    tom i TESTEN: motorens attestasjonskrav for målautorisasjonsvilkåret
    er policyforfatningens sak; her prøves BESTILLINGSVEIEN)."""
    from api import policyregister
    p = _yaml.safe_load(
        (POLICIES / "bransjemal-tjenestebedrift.yaml")
        .read_text(encoding="utf-8"))
    if med_handling:
        # Skjemaet krever M-nummer (katalognummeret er ikke tildelt ennå
        # — M-40 er testens plassholder) og tillater at `vilkaar` UTELATES;
        # en tom liste avvises. Målautorisasjonen bæres av domenekontroll-
        # porten i endepunktet, ikke av et policyvilkår (klarsignal §6:
        # «dette er ikke policyens ansvar»).
        p["roller"].append({"id": "bestiller",
                            "beskrivelse": "Bestiller kontroller"})
        p["handlinger"].append({
            "id": "kontroll.wcag.nettsted", "modul": "M-40",
            "modus": "auto", "ved_brudd": ved_brudd,
            "tillatt_for": list(tillatt_for),
            "dataklasser_tillatt": ["offentlig"],
            "grenser": {"frekvens": {"maks": 4, "periode_antall": 1,
                                     "periode_enhet": "dager",
                                     "grupperingsnokkel": "mal_url"}},
            "reversering": {"type": "direkte"}})
    policyregister.registrer(migrator_, TENANT, p, p["meta"]["status"])
    migrator_.commit()
    return p


def _verifiser_domene(migrator_, hostname):
    _sett_kontekst(migrator_, TENANT)
    migrator_.execute(
        "INSERT INTO domenekontroll (tenant, hostname, status,"
        " autorisasjonsgenerasjon, verifisert_ts)"
        " VALUES (%s,%s,'verifisert',1,now())"
        " ON CONFLICT (tenant, hostname) DO UPDATE SET status='verifisert'",
        (TENANT, hostname))
    migrator_.commit()


def _bestill(klient_, cookie, csrf, kropp, nokkel=None):
    from api import sesjon as sesjonmodul
    hoder = {"X-Disponit-CSRF": csrf}
    if nokkel:
        hoder["Idempotency-Key"] = nokkel
    return klient_.post("/v1/bestilling", json=kropp, headers=hoder,
                        cookies={sesjonmodul.C_SESJON: cookie})


def _gyldig_kropp(host="kunde.example", **over):
    k = {"bestillingstype": "kontroll.wcag.nettsted", "hostname": host,
         "kravsett": "wcag21_aa", "omfang": "enkeltside"}
    k.update(over)
    return k


@pg
@dekker("bestilling_hostname_uverifisert")
def test_uverifisert_hostname_avvises_for_beslutningen(migrator, klient):
    """Port 9 (sikkerhetsinvariant): avvist FØR beslutningen — ingen
    loggpost, intet oppdrag. Kontroll: fjern `_verifisert_hostname`-porten,
    så blir denne rød."""
    _wcag_policy(migrator)
    cookie, csrf = _adminsesjon()
    nokkel = "n-" + secrets.token_hex(8)
    r = _bestill(klient, cookie, csrf, _gyldig_kropp("fremmed.example"),
                 nokkel)
    assert (r.status_code, r.json()["feil"]) == (
        403, "bestilling_hostname_uverifisert"), r.text
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s AND"
        " idempotency_key=%s", (TENANT, "bestilling:" + nokkel)).fetchone()[0]
    migrator.rollback()
    assert n == 0, "beslutningen ble tatt for et uautorisert mål"


@pg
def test_lukket_kropp_og_normalisering(migrator, klient):
    """Portene 10–13, 16: URL/credentials/port/`..`/prosent/query kan ikke
    uttrykkes; ukjent bestillingstype avvises; modul/frist/epoch finnes
    ikke som felter."""
    _wcag_policy(migrator)
    _verifiser_domene(migrator, "kunde.example")
    cookie, csrf = _adminsesjon()
    for kropp in [
        _gyldig_kropp(mal_url="https://kunde.example/"),      # 12: URL-felt
        _gyldig_kropp("bruker@kunde.example"),                # 10: credentials
        _gyldig_kropp("kunde.example:8443"),                  # 10: port
        _gyldig_kropp("KUNDE.example"),                       # 10: ikke-A-label
        _gyldig_kropp(sti="/a/../b"),                         # 11: ..
        _gyldig_kropp(sti="/s%20i"),                          # 11: prosent
        _gyldig_kropp(sti="/x?y=1"),                          # 11: query
        _gyldig_kropp(bestillingstype="kontroll.ukjent.type"),  # 13
        _gyldig_kropp(frist="10s"),                           # 16: finnes ikke
        _gyldig_kropp(modul="m01_policy"),                    # 16
    ]:
        r = _bestill(klient, cookie, csrf, kropp)
        assert r.status_code == 400, (kropp, r.text)
        assert r.json()["feil"] == "request_feilformet"


@pg
def test_tillat_gir_noyaktig_ett_beslutningsoppdrag(migrator, klient):
    """Port 17: TILLAT → ett oppdrag, opprinnelse='beslutning',
    evidensfrist 30 min for enkeltside, KOBLET til beslutningsloggposten."""
    _wcag_policy(migrator)
    _verifiser_domene(migrator, "kunde.example")
    cookie, csrf = _adminsesjon()
    r = _bestill(klient, cookie, csrf, _gyldig_kropp(),
                 "n-" + secrets.token_hex(8))
    assert r.status_code == 200, r.text
    svar = r.json()
    assert svar["beslutning"] == "tillat" and svar["oppdrag_id"], svar
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT opprinnelse, unntak_id, koblingsstatus,"
        " beslutning_loggpost_id IS NOT NULL,"
        " evidensfrist - now() BETWEEN interval '25 minutes' AND"
        " interval '31 minutes'"
        " FROM oppdrag WHERE tenant=%s AND id=%s",
        (TENANT, svar["oppdrag_id"])).fetchone()
    migrator.rollback()
    assert rad == ("beslutning", None, "KOBLET", True, True), rad


@pg
def test_stopp_gir_kode_og_intet_oppdrag(migrator, klient):
    """Port 18: policyen tillater ikke bestilleren og sier `ved_brudd:
    stopp_og_varsle` → STOPP med strukturert kode, INTET oppdrag — og beslutningen
    står i revisjonsloggen. (En manglende handling går derimot BRUDD-veien
    — det dekker unntakskø-testen.)"""
    _wcag_policy(migrator, ved_brudd="stopp_og_varsle", tillatt_for=("konsulent",))
    _verifiser_domene(migrator, "kunde.example")
    cookie, csrf = _adminsesjon()
    nokkel = "n-" + secrets.token_hex(8)
    r = _bestill(klient, cookie, csrf, _gyldig_kropp(), nokkel)
    assert r.status_code == 200, r.text
    svar = r.json()
    assert svar["beslutning"] == "stopp" and svar["oppdrag_id"] is None
    assert svar["begrunnelse"], "STOPP uten strukturert kode"
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT beslutning FROM revisjonslogg WHERE tenant=%s AND"
        " idempotency_key=%s", (TENANT,
                                "bestilling:" + nokkel)).fetchone()
    n = migrator.execute("SELECT count(*) FROM oppdrag WHERE tenant=%s"
                         " AND opprinnelse='beslutning'",
                         (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert rad == ("STOPP",)
    assert n == 0

    # 21e: gjenspill av STOPP → samme kode, ingen ny beslutning.
    r2 = _bestill(klient, cookie, csrf, _gyldig_kropp(), nokkel)
    assert r2.json()["beslutning"] == "stopp"
    assert r2.headers.get("idempotent-replay") == "1"


@pg
def test_femte_bestilling_gaar_i_unntakskoen(migrator, klient):
    """Portene 19–20: frekvensgrensen håndheves KUN i motorens betrodde
    teller — fire TILLAT, femte → unntakskø (policyens ved_brudd)."""
    _wcag_policy(migrator)
    _verifiser_domene(migrator, "kvote.example")
    cookie, csrf = _adminsesjon()
    for i in range(4):
        r = _bestill(klient, cookie, csrf, _gyldig_kropp("kvote.example"))
        assert r.json()["beslutning"] == "tillat", (i, r.text)
    r5 = _bestill(klient, cookie, csrf, _gyldig_kropp("kvote.example"))
    assert r5.status_code == 200, r5.text
    assert r5.json()["beslutning"] != "tillat", r5.text
    assert r5.json().get("unntak_id"), "femte havnet ikke i unntakskøen"
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM oppdrag WHERE tenant=%s AND"
        " opprinnelse='beslutning'", (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert n == 4, "over grensen ble likevel utført (port 19)"


@pg
def test_idempotens_binder_hele_intensjonen(migrator, klient):
    """Portene 21, 21b–21d: samme nøkkel+kropp → samme oppdrag, én
    reservasjon; endret maks_sider/kravsett/omfang/sti → 409 uten ny
    beslutning; ekvivalente sti-skrivemåter er SAMME intensjon."""
    _wcag_policy(migrator)
    _verifiser_domene(migrator, "idem.example")
    cookie, csrf = _adminsesjon()
    nokkel = "n-" + secrets.token_hex(8)
    r1 = _bestill(klient, cookie, csrf, _gyldig_kropp("idem.example"),
                  nokkel)
    assert r1.json()["beslutning"] == "tillat", r1.text
    oid = r1.json()["oppdrag_id"]
    # 21: replay — samme oppdrag, ingen ny frekvensreservasjon.
    r2 = _bestill(klient, cookie, csrf, _gyldig_kropp("idem.example"),
                  nokkel)
    assert r2.json()["oppdrag_id"] == oid
    assert r2.headers.get("idempotent-replay") == "1"
    # 21d: sti utelatt og sti="/" normaliserer LIKT → fortsatt replay.
    r2b = _bestill(klient, cookie, csrf,
                   _gyldig_kropp("idem.example", sti="/"), nokkel)
    assert r2b.json()["oppdrag_id"] == oid, r2b.text
    # 21b/c: annen intensjon under samme nøkkel → 409, intet andre oppdrag.
    for endring in ({"maks_sider": 50}, {"kravsett": "wcag21_aa",
                                         "omfang": "nettsted"},
                    {"sti": "/annen"}):
        r3 = _bestill(klient, cookie, csrf,
                      _gyldig_kropp("idem.example", **endring), nokkel)
        assert (r3.status_code, r3.json()["feil"]) == (
            409, "idempotenskonflikt"), r3.text
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM oppdrag WHERE tenant=%s AND"
        " opprinnelse='beslutning'", (TENANT,)).fetchone()[0]
    frek = migrator.execute(
        "SELECT count(*) FROM frekvens_hendelser WHERE tenant=%s",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert n == 1, "idempotensen lot to oppdrag oppstå"
    assert frek == 1, f"gjenspill brant kvote ({frek} reservasjoner)"


@pg
def test_uten_scope_nektes(migrator, klient):
    """Port 15: `leser`-økten har ikke bestilling:opprett."""
    _wcag_policy(migrator)
    cookie, csrf = _adminsesjon(roller="leser")
    r = _bestill(klient, cookie, csrf, _gyldig_kropp())
    assert r.status_code == 403, r.text


def test_feltparitet_mellom_skjema_og_intensjonshash():
    """Port 21f (statisk): hvert skjemafelt inngår i intensjonen — mekanisk
    via FELTDEKNING, så et fremtidig felt ikke kan falle utenfor hashen."""
    from api.bestilling import (FELTDEKNING, INTENSJONSFELT, SKJEMAFELT,
                                intensjonshash, normaliser)
    dekket = {felt for kilder in FELTDEKNING.values() for felt in kilder}
    assert dekket == set(SKJEMAFELT), (
        f"skjemafelt utenfor intensjonshashen: {set(SKJEMAFELT) - dekket}")
    assert set(FELTDEKNING) == set(INTENSJONSFELT)
    # ... og hashen er normaliseringens, ikke skrivemåtens:
    a = normaliser("t", {"bestillingstype": "kontroll.wcag.nettsted",
                         "hostname": "x.example", "kravsett": "wcag21_aa",
                         "omfang": "enkeltside"})
    b = normaliser("t", {"bestillingstype": "kontroll.wcag.nettsted",
                         "hostname": "x.example", "sti": "/",
                         "kravsett": "wcag21_aa", "omfang": "enkeltside",
                         "maks_sider": 1})
    assert intensjonshash(a) == intensjonshash(b)
