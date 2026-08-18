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
        # Konteksten er LOCAL og forsvant i commiten over — og
        # tenantporten i funksjonen krever den (Codex P1).
        _sett_kontekst(rt, TENANT)
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
        _sett_kontekst(rt, TENANT)
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
def test_definerveiene_binder_tenant_til_kallerens_kontekst(migrator):
    """Codex P1: `p_tenant` er IKKE kallerens frie valg.

    Funksjonene er SECURITY DEFINER og kjører som `disponit_m37_claimer`,
    hvis `m37_dispatcher`-policy er permissiv for hver eneste rad — altså
    sa RLS ingenting om `p_tenant`. En kompromittert runtime kunne gjette
    et oppdragsnummer hos en ANNEN tenant og få opprettet, og deretter
    lest ut, en sak for det. Porten er `krev_tenantkontekst`.

    Kontroll: fjern `PERFORM krev_tenantkontekst(...)` fra funksjonene, så
    blir denne rød.
    """
    from .test_api import ANNEN_TENANT
    rt = _rt()
    try:
        oid, logg = _beslutningsoppdrag(rt, migrator)

        # (a) FREMMED kontekst mot et oppdrag hos TENANT.
        _sett_kontekst(rt, ANNEN_TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT sikre_sak_for_oppdrag(%s,%s,'sikkerhet',"
                       "'angriper','r-x')", (TENANT, oid))
        rt.rollback()

        # (b) INGEN kontekst i det hele tatt — fail-closed, ikke «alle».
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT sikre_sak_for_oppdrag(%s,%s,'sikkerhet',"
                       "'angriper','r-y')", (TENANT, oid))
        rt.rollback()

        # (c) Samme port på opprettelsesveiene: et oppdrag kan ikke fødes
        #     inn i en annen tenant enn den kalleren står i.
        _sett_kontekst(rt, ANNEN_TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute(
                "SELECT opprett_beslutningsoppdrag(%s,%s,"
                "'kontroll.wcag.nettsted','kontroll.wcag.nettsted',"
                "'m_wcag_audit',%s,%s,%s,now()+interval '30 minutes',"
                "now()+interval '30 minutes')",
                (TENANT, logg, b"x", "k", b"n"))
        rt.rollback()

        # ... og ingenting ble skrevet av noen av forsøkene.
        _sett_kontekst(migrator, TENANT)
        n = migrator.execute(
            "SELECT count(*) FROM unntak WHERE tenant=%s AND oppdrag_id=%s",
            (TENANT, oid)).fetchone()[0]
        migrator.rollback()
        assert n == 0, "et kall utenfor tenantkonteksten skrev likevel"

        # Den LOVLIGE veien er urørt: riktig kontekst, samme kall.
        _sett_kontekst(rt, TENANT)
        sak = rt.execute("SELECT sikre_sak_for_oppdrag(%s,%s,'sikkerhet',"
                         "'kvitteringsport','r-ok')", (TENANT, oid)).fetchone()[0]
        rt.commit()
        assert sak is not None
    finally:
        rt.close()


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

from .test_api import POLICIES, app, dekker, klient, token  # noqa: F401


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


# ==========================================================================
# Konsumentveiene (§5, portene 23–29)
# ==========================================================================

from .test_pr008 import _lesetoken


def _utlopt_beslutningsoppdrag(rt, migrator_):
    """Beslutningsoppdrag født med evidensfristen alt passert."""
    logg, ct, key_id, nonce = _beslutningsgrunnlag(migrator_)
    _sett_kontekst(rt, TENANT)
    oid = rt.execute(
        "SELECT opprett_beslutningsoppdrag(%s,%s,'kontroll.wcag.nettsted',"
        "'kontroll.wcag.nettsted','m_wcag_audit',%s,%s,%s,"
        "now()-interval '2 minutes',now()-interval '1 minute')",
        (TENANT, logg, ct, key_id, nonce)).fetchone()[0]
    rt.commit()
    return int(oid), logg


@pg
def test_reaper_lukker_utlopte_beslutningsoppdrag(migrator):
    """Portene 23, 25, 27 + M-37-regresjonen: utløpt beslutningsoppdrag →
    evidensfrist-sak + `feilet` uten kvittering (= lese-API-ets timeout);
    gjentatt kjøring er no-op; `unntak_id` forblir NULL; et utløpt
    M-37-oppdrag røres ALDRI av reaperen."""
    rt = _rt()
    try:
        oid, logg = _utlopt_beslutningsoppdrag(rt, migrator)
        # M-37-kontrollen: et reparasjonsoppdrag med utløpt frist —
        # produksjonsformen fra test_m37, bare med fristene i fortid.
        from .test_m37 import _lag_oppdrag
        sak_id, m37_logg = _lag_sak(migrator, TENANT)
        m37_oid, _ = _lag_oppdrag(migrator, TENANT, sak_id, m37_logg,
                                  utforelsesfrist="-2 minutes",
                                  evidensfrist="-1 minutes")

        rader = rt.execute("SELECT tenant, oppdrag_id, unntak_id"
                           " FROM reap_evidensfrister(50)").fetchall()
        rt.commit()
        mine = [r for r in rader if r[0] == TENANT and r[1] == oid]
        assert len(mine) == 1, rader
        sak_id = mine[0][2]
        assert all(r[1] != m37_oid for r in rader), \
            "reaperen rørte et M-37-oppdrag"

        _sett_kontekst(migrator, TENANT)
        o = migrator.execute(
            "SELECT status, kvittering IS NULL, unntak_id FROM oppdrag"
            " WHERE tenant=%s AND id=%s", (TENANT, oid)).fetchone()
        m37 = migrator.execute(
            "SELECT status FROM oppdrag WHERE tenant=%s AND id=%s",
            (TENANT, m37_oid)).fetchone()
        sak = migrator.execute(
            "SELECT arsak, oppdrag_id, terminal, loggpost_id FROM unntak"
            " WHERE tenant=%s AND id=%s", (TENANT, sak_id)).fetchone()
        migrator.rollback()
        assert o == ("feilet", True, None), o          # 23 + 27
        assert m37 == ("opprettet",)                    # regresjonsporten
        assert sak == ("evidensfrist", oid, False, logg)

        # 25: gjentatt kjøring — oppdraget er terminalt, ingen ny sak.
        rader2 = rt.execute("SELECT oppdrag_id"
                            " FROM reap_evidensfrister(50)").fetchall()
        rt.commit()
        assert all(r[0] != oid for r in rader2)
        _sett_kontekst(migrator, TENANT)
        n = migrator.execute(
            "SELECT count(*) FROM unntak WHERE tenant=%s AND oppdrag_id=%s",
            (TENANT, oid)).fetchone()[0]
        migrator.rollback()
        assert n == 1
    finally:
        rt.close()


@pg
def test_kvitteringskonflikt_foder_sikkerhetssak(migrator):
    """Port 24: kvitteringsavvik på et beslutningsoppdrag (unntak_id NULL)
    føres på en idempotent sikkerhetssak via `sikre_sak_for_oppdrag` —
    og avvisnings-oppslaget finner raden igjen UTEN sak-id i hånda."""
    from api.app import _kvittering_alt_avvist, _sikkerhetssak_kvittering
    rt = _rt()
    try:
        oid, _ = _beslutningsoppdrag(rt, migrator)
        _sett_kontekst(rt, TENANT)
        art = "00000000-0000-4000-8000-00000000abcd"
        _sikkerhetssak_kvittering(
            rt, TENANT, None, "artefakt_ikke_verifisert",
            {"oppdrag_id": oid, "artefakt_id": art}, "r-test",
            oppdrag_id=oid)
        rt.commit()
        _sett_kontekst(rt, TENANT)
        # 25 i sikkerhetsfamilien: samme oppdrag, ny konflikt → samme sak.
        _sikkerhetssak_kvittering(
            rt, TENANT, None, "motstridende_kvittering",
            {"kilde": "oppdrag", "lagret": "a"*64, "ny": "b"*64,
             "oppdrag_id": oid}, "r-test2", oppdrag_id=oid)
        rt.commit()

        _sett_kontekst(migrator, TENANT)
        saker = migrator.execute(
            "SELECT id, sakstype, prioritet, arsak FROM unntak"
            " WHERE tenant=%s AND oppdrag_id=%s", (TENANT, oid)).fetchall()
        assert len(saker) == 1, saker
        assert saker[0][1:] == ("sikkerhet", "hoy", "sikkerhet")
        hendelser = migrator.execute(
            "SELECT hendelse FROM unntak_historikk WHERE tenant=%s"
            " AND unntak_id=%s ORDER BY id", (TENANT, saker[0][0])).fetchall()
        migrator.rollback()
        # 'opprettet' skrives av historikk-triggeren ved INSERT i unntak.
        assert [h[0] for h in hendelser] == [
            "opprettet", "sak_for_oppdrag", "artefakt_ikke_verifisert",
            "motstridende_kvittering"]

        _sett_kontekst(rt, TENANT)
        assert _kvittering_alt_avvist(rt, TENANT, None, oid, art) is True
        assert _kvittering_alt_avvist(rt, TENANT, None, oid,
                                      art.replace("abcd", "eeee")) is False
        rt.rollback()
    finally:
        rt.close()


def _plukket_beslutningsoppdrag(migrator_, modul, kh):
    """Et CLAIMET beslutningsoppdrag av en ikke-artefaktproduserende type.

    Samme moduloppsett som `_plukket_oppdrag_med_binding` (014b), bare født
    av `opprett_beslutningsoppdrag`: ingen sak, ingen loggpost, ingen
    reparasjonsidentitet — nøyaktig formen kvitteringsveien får fra
    bestillingsflaten.
    """
    from db import kryptering
    from .test_wcag_kontroll import _mk_admin

    ma = _mk_admin("disponit_modules_admin")
    try:
        ma.execute("SELECT registrer_kontrakt(%s,1,%s,'p','k','krever_outbox',"
                   "'kompenserende','sys')", (modul, kh))
        ma.execute("SELECT registrer_release(%s,'r1',1,%s,'mh','ad','sys')",
                   (modul, kh))
        ma.execute("SELECT installer_modul(%s,'sys')", (modul,))
        ma.execute("SELECT sett_modulstatus(%s,'staging_verifisert',NULL,"
                   "'sys')", (modul,))
        ma.execute("SELECT bytt_release(%s,'staging','r1',1,%s,'sys')",
                   (modul, kh))
        ma.execute("SELECT sett_modulstatus(%s,'aktiv','r1','sys')", (modul,))
        ot = "bo-" + secrets.token_hex(4)
        ma.execute("SELECT registrer_oppdragstype(%s,%s,1,%s,'sys')",
                   (ot, modul, kh))
        ma.commit()
    finally:
        ma.close()

    _sett_kontekst(migrator_, TENANT)
    logg = migrator_.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'test','api_token','ih','p@1.0.0/x.y','TILLAT','[]',%s)"
        " RETURNING id", (TENANT, secrets.token_hex(8))).fetchone()[0]
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator_, TENANT)
    ct, nonce = kryptering.krypter(
        dek, {"handling": "purring.send", "ressurs_id": "fak-1"},
        TENANT, key_id)
    migrator_.commit()

    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        opp = int(rt.execute(
            "SELECT opprett_beslutningsoppdrag(%s,%s,%s,'purring.send',%s,"
            "%s,%s,%s,now()+interval '1 hour',now()+interval '30 days')",
            (TENANT, logg, ot, modul, ct, key_id, nonce)).fetchone()[0])
        rt.commit()
        rt.execute("SELECT set_config('disponit.aktor','m37',true),"
                   "       set_config('disponit.request_id','r',true)")
        rad = rt.execute(
            "SELECT id FROM claim_neste_oppdrag(%s,%s,%s,300,'r1',"
            "'staging',0)",
            (modul, ["purring."], secrets.token_hex(16))).fetchone()
        rt.commit()
    finally:
        rt.close()
    assert rad is not None and rad[0] == opp, \
        "beslutningsoppdraget ble ikke claimet"
    return opp


@pg
def test_normal_kvittering_paa_beslutningsoppdrag_fullfores(migrator, klient,
                                                            token):
    """Codex P1: en kvittering I TIDE på et SAKSFRITT beslutningsoppdrag.

    Den avsluttende bokføringen i `_ingest_kvittering` var M-37-veiens og
    kjørte ubetinget: `unntak_historikk.unntak_id` er NOT NULL, og et
    beslutningsoppdrag har per konstruksjon ingen sak. Hver normal
    kvittering på et BESTILT oppdrag døde derfor i basen og rullet med seg
    statusskiftet og artefaktpromoteringen — oppdraget kunne aldri
    fullføres, altså var hele bestillingsveien uten en utgang.

    Kontroll: fjern `if unntak_id is not None`-grenen rundt
    saksbokføringen, så blir denne rød.
    """
    from .test_m37 import _signer_kvittering
    from .test_pr014b_artefakt_api import _kvitteringskap, _oppdrag_owner

    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    opp = _plukket_beslutningsoppdrag(migrator, modul, kh)
    oc, rep, gen = _oppdrag_owner(migrator, opp)
    assert rep is None, "et beslutningsoppdrag har ingen reparasjonsidentitet"

    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering({"oppdrag_id": opp, "tenant": TENANT,
                             "kvittering_jti": kjti, "owner_claim_id": oc,
                             "owner_generation": gen, "resultat": "utfort",
                             "ressurs_id": "fak-1"})
    tok, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    klient.cookies.clear()
    r = klient.post("/v1/oppdrag/kvittering", json=kv,
                    headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "utfort", r.text
    assert r.json()["unntak_id"] is None

    _sett_kontekst(migrator, TENANT)
    o = migrator.execute(
        "SELECT status, kvittering IS NOT NULL, unntak_id FROM oppdrag"
        " WHERE tenant=%s AND id=%s", (TENANT, opp)).fetchone()
    saker = migrator.execute(
        "SELECT count(*) FROM unntak WHERE tenant=%s AND oppdrag_id=%s",
        (TENANT, opp)).fetchone()[0]
    migrator.rollback()
    assert o == ("utfort", True, None), o
    # Normalveien JOURNALFØRER ikke: en sak opprettes når noe faktisk ER et
    # unntak (evidensfrist/sikkerhet), aldri fordi et oppdrag gikk bra.
    assert saker == 0, "normalveien fødte en sak"


@pg
def test_lese_api_viser_opphav_og_null_unntak(migrator, klient):
    """Portene 28–29: lese-API-et svarer `unntak_id: null` + `opprinnelse`
    for et beslutningsoppdrag — og den eksisterende M-37-formen er urørt."""
    _wcag_policy(migrator)
    _verifiser_domene(migrator, "lese.example")
    cookie, csrf = _adminsesjon()
    r = _bestill(klient, cookie, csrf, _gyldig_kropp("lese.example"))
    assert r.json()["beslutning"] == "tillat", r.text
    oid = r.json()["oppdrag_id"]
    _sett_kontekst(migrator, TENANT)
    bid = migrator.execute(
        "SELECT beslutning_loggpost_id FROM oppdrag WHERE tenant=%s"
        " AND id=%s", (TENANT, oid)).fetchone()[0]
    migrator.rollback()
    tok, _ = _lesetoken(migrator, scopes=("decisions:read",))
    # TestClient husker cookien fra bestillingen; cookie + Bearer i samme
    # forespørsel er dobbel principal og avvises — med rette.
    klient.cookies.clear()
    k = klient.get(f"/v1/beslutninger/{bid}",
                   headers={"authorization": f"Bearer {tok}"})
    assert k.status_code == 200, k.text
    res = k.json()["resultat"]
    assert res["oppdrag_id"] == oid
    assert res["unntak_id"] is None                      # 28
    assert res["opprinnelse"] == "beslutning"


@pg
def test_rapport_lese_api(migrator, klient):
    """038 §7: GET /v1/rapport/{oppdrag_id} — den promoterte rapporten
    dekryptert server-side; ingen ciphertext/nøkkelreferanser i svaret.
    Uten promotert artefakt (eller for et fremmed nummer): identisk 404."""
    import hashlib as _hl

    from db import kryptering
    from policy_validator import jcs
    from .test_wcag_kontroll import _registrer_skjema, _streng_type, _mk_admin

    rt = _rt()
    try:
        oid, _ = _beslutningsoppdrag(rt, migrator)
    finally:
        rt.close()
    # 404 FØR promotering — «ikke ferdig» og «finnes ikke» er samme svar.
    tok, _ = _lesetoken(migrator, scopes=("decisions:read",))
    klient.cookies.clear()
    r0 = klient.get(f"/v1/rapport/{oid}",
                    headers={"authorization": f"Bearer {tok}"})
    assert r0.status_code == 404, r0.text

    modul = "m-" + secrets.token_hex(4)
    kh = secrets.token_hex(32)
    ma = _mk_admin("disponit_modules_admin")
    try:
        ma.execute("SELECT registrer_kontrakt(%s,1,%s,'p','k','krever_outbox',"
                   "'kompenserende','sys')", (modul, kh))
        ma.commit()
    finally:
        ma.close()
    at = _streng_type(migrator, modul, kh,
                      skjema={"type": "object"})
    rapport = {"kravsett": "wcag21_aa", "sammendrag": {"kritisk": 0}}
    kanon = jcs.kanoniske_bytes(rapport)
    _sett_kontekst(migrator, TENANT)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, TENANT)
    ct, nonce = kryptering.krypter(dek, rapport, TENANT, key_id)
    migrator.execute(
        "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype, modul_id,"
        " release_id, kontraktversjon, kontrakt_hash, module_epoch, tilstand,"
        " storrelse_bytes, klartekst_sha256, ciphertext, nonce, dek_ref,"
        " kapabilitet_jti, promotert_ts)"
        " VALUES (%s,%s,%s,%s,'r1',1,%s,0,'promotert',%s,%s,%s,%s,%s,%s,"
        " now())",
        (TENANT, oid, at, modul, kh, len(kanon),
         _hl.sha256(kanon).hexdigest(), ct, nonce, key_id,
         "jti-" + secrets.token_hex(8)))
    migrator.commit()

    r = klient.get(f"/v1/rapport/{oid}",
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    k = r.json()
    assert k["rapport"] == rapport
    assert k["oppdrag_id"] == oid and k["artefakttype"] == at
    for hemmelig in ("ciphertext", "nonce", "dek_ref"):
        assert hemmelig not in k, f"{hemmelig} lekket til klienten"
    # Fremmed oppdragsnummer → samme 404 som «finnes ikke».
    r2 = klient.get(f"/v1/rapport/{oid + 999}",
                    headers={"authorization": f"Bearer {tok}"})
    assert r2.status_code == 404
