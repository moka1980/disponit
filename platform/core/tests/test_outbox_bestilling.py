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
    # Codex P2: og NØYAKTIG én koblingshendelse. Raden er idempotent fordi
    # indeksen gjør den det, men historikken er append-only og teller —
    # taperen skrev tidligere sin egen `sak_for_oppdrag` oppå vinnerens,
    # så den samme koblingen ble ført to ganger i revisjonssporet.
    h = migrator.execute(
        "SELECT count(*) FROM unntak_historikk WHERE tenant=%s"
        " AND unntak_id=%s AND hendelse='sak_for_oppdrag'",
        (TENANT, resultater[0])).fetchone()[0]
    migrator.rollback()
    assert n == 1, n
    assert h == 1, f"koblingshendelsen ble ført {h} ganger"


@pg
def test_apen_sak_laases_for_den_gjenbrukes(migrator):
    """Codex P2: port 26 gjelder også mot en løsning som pågår NÅ.

    Gjenbruksveien leste saken uten lås, altså i sitt eget snapshot. En
    saksbehandler som akkurat da satte `løst` uten å ha committet var
    usynlig, og hendelsen ble hengt på en sak som et øyeblikk senere var
    endelig — «terminal gjenbrukes aldri» holdt bare mot det som alt var
    ferdig. Her løses saken i en åpen transaksjon MENS kallet står på
    døra: kallet skal vente, se `løst`, og føde en NY åpen sak.

    Kontroll: fjern `FOR UPDATE` fra gjenbrukselecten, så returnerer
    kallet den terminale saken og testen blir rød.
    """
    import threading
    import time
    from db.pg import koble

    rt0 = _rt()
    try:
        oid, _ = _beslutningsoppdrag(rt0, migrator)
        _sett_kontekst(rt0, TENANT)
        s1 = rt0.execute("SELECT sikre_sak_for_oppdrag(%s,%s,'evidensfrist',"
                         "'reaper','r1')", (TENANT, oid)).fetchone()[0]
        rt0.commit()
    finally:
        rt0.close()

    # Løseren holder raden — statusmaskinens lovlige vei, ucommittet.
    loser = koble(MIGRATOR_DSN)
    _sett_kontekst(loser, TENANT)
    loser.execute("UPDATE unntak SET status='under_behandling' WHERE id=%s",
                  (s1,))
    loser.execute("UPDATE unntak SET status='løst' WHERE id=%s", (s1,))

    svar = []
    feil = []

    def gjenbruk():
        rt = _rt()
        try:
            _sett_kontekst(rt, TENANT)
            svar.append(rt.execute(
                "SELECT sikre_sak_for_oppdrag(%s,%s,'evidensfrist',"
                "'reaper','r2')", (TENANT, oid)).fetchone()[0])
            rt.commit()
        except Exception as e:                      # pragma: no cover
            feil.append(e)
        finally:
            rt.close()

    t = threading.Thread(target=gjenbruk)
    t.start()
    try:
        # Kallet skal STÅ og vente på låsen, ikke svare fra sitt snapshot.
        time.sleep(1.0)
        assert not svar, ("gjenbruksveien svarte mens saken var under "
                          "løsning — den leste uten lås")
        loser.commit()
    finally:
        loser.close()
        t.join(timeout=20)

    assert not feil, feil
    assert svar, "kallet svarte aldri"
    assert svar[0] != s1, "en sak som ble terminal mens vi ventet, ble gjenbrukt"
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute("SELECT status FROM unntak WHERE id=%s",
                            (svar[0],)).fetchone() == ("ny",)
    assert migrator.execute("SELECT status FROM unntak WHERE id=%s",
                            (s1,)).fetchone() == ("løst",)
    migrator.rollback()


@pg
def test_oppdragsbindingen_er_uforanderlig(migrator):
    """Codex P2: `oppdrag_id` og `arsak` kan ikke skrives om etter
    opprettelsen.

    Runtime har direkte UPDATE på `unntak` (statusmaskinen går den veien),
    og begge kolonnene sto utenfor enhver lås. CHECKen og FK-en godtar et
    HVILKET SOM HELST gyldig par, så en sak kunne bindes om til et annet
    oppdrag — og hele dens append-only-historikk ville da tilhøre noe
    annet enn det den ble født av.

    Kontroll: fjern `unntak_oppdragsbinding_immutable`, så blir denne rød.
    """
    rt = _rt()
    try:
        oid, _ = _beslutningsoppdrag(rt, migrator)
        oid2, _ = _beslutningsoppdrag(rt, migrator)
        _sett_kontekst(rt, TENANT)
        sak = rt.execute("SELECT sikre_sak_for_oppdrag(%s,%s,'evidensfrist',"
                         "'reaper','r1')", (TENANT, oid)).fetchone()[0]
        rt.commit()
    finally:
        rt.close()

    # Begge feltene, hver for seg — og som MIGRATOR, altså tabelleieren:
    # står låsen i lagringen, finnes det ingen rolle som kommer utenom.
    for sql, params in (
            ("UPDATE unntak SET oppdrag_id=%s WHERE id=%s", (oid2, sak)),
            ("UPDATE unntak SET arsak='sikkerhet' WHERE id=%s", (sak,)),
            ("UPDATE unntak SET oppdrag_id=NULL, arsak=NULL WHERE id=%s",
             (sak,))):
        _sett_kontekst(migrator, TENANT)
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(sql, params)
        migrator.rollback()

    # ... mens statusmaskinen, som DELER tabellen, er helt urørt.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE unntak SET status='under_behandling'"
                     " WHERE id=%s", (sak,))
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT status, oppdrag_id, arsak FROM unntak WHERE id=%s",
        (sak,)).fetchone()
    migrator.rollback()
    assert rad == ("under_behandling", oid, "evidensfrist"), rad


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


def _sikre_typeregistrering():
    """Idempotent oppsett av en CLAIMBAR `kontroll.wcag.nettsted`.

    Runtime-vakta i /v1/bestilling nekter en type som ikke kan claimes med
    503, så E2E-testene må konstruere tilstanden selv (ingen delt
    fixture-antakelse; en fersk CI-base har den ikke). Vilkårene er
    `claim_neste_oppdrag` (037) sine, og de er FIRE — ikke bare
    registerraden (Codex P1): kontrakt + registrert oppdragstype med rett
    eier, `modulhode.status='aktiv'`, og en `moduldeployment` som er
    `claiming` i DETTE miljøet. Testhashene er faste, så gjentatte kall er
    no-op på identisk innhold.

    Herav følger at hver grønn E2E-bestilling i denne fila også er den
    positive motsatsen til de tre negative testene under: står ett av
    vilkårene ikke, svarer /v1/bestilling 503 før beslutningen.
    """
    from db.pg import koble
    from miljo import gjeldende_miljo
    from .test_wcag_kontroll import _mk_admin
    miljo = gjeldende_miljo()
    # Sjekk-først, ikke blind re-registrering: `registrer_oppdragstype`
    # avviser ENHVER overlappende type (prefiks-entydigheten er hele
    # poenget dens), så «idempotent» må her bety «står den der med rett
    # eier, er jobben alt gjort». Lesingen skjer som migrator —
    # modules_admin har med vilje ingen bordtilgang, bare funksjonene.
    m = koble(MIGRATOR_DSN)
    try:
        rad = m.execute(
            "SELECT eiermodul FROM oppdragstype_register WHERE"
            " oppdragstype='kontroll.wcag.nettsted'").fetchone()
        har_kontrakt = m.execute(
            "SELECT kontrakt_hash FROM modulkontrakt WHERE"
            " modul_id='m_wcag_audit' AND kontraktversjon=1").fetchone()
        hode = m.execute(
            "SELECT status, module_epoch FROM modulhode WHERE"
            " modul_id='m_wcag_audit'").fetchone()
        claiming = m.execute(
            "SELECT count(*) FROM moduldeployment WHERE"
            " modul_id='m_wcag_audit' AND miljo=%s AND livslop='claiming'",
            (miljo,)).fetchone()[0]
        m.rollback()
    finally:
        m.close()
    if rad is not None:
        assert rad[0] == "m_wcag_audit", rad
    if rad is not None and hode and hode[0] == "aktiv" and claiming:
        return
    ma = _mk_admin("disponit_modules_admin")
    try:
        kh = har_kontrakt[0] if har_kontrakt else "ab" * 32
        # Ny release-id når det trengs en frisk claiming: livsløpet er
        # fremover-only (claiming→draining→retired), så en release som
        # ALT er drenet — f.eks. av et nødstopp — kan aldri claimes igjen.
        rid = "r-testreg-" + secrets.token_hex(4)
        ma.execute("SELECT installer_modul('m_wcag_audit','testreg')")
        if har_kontrakt is None:
            ma.execute("SELECT registrer_kontrakt('m_wcag_audit',1,%s,%s,%s,"
                       "'ekstern_lesing','direkte','testreg')",
                       (kh, "cd" * 32, "ef" * 32))
        if rad is None:
            ma.execute("SELECT registrer_oppdragstype("
                       "'kontroll.wcag.nettsted','m_wcag_audit',1,%s,"
                       "'testreg')", (kh,))
        # `nodeaktivert` er nødstoppens egen tilstand: ut av den går bare
        # `reaktiver_modul`, epoch-gjerdet, og den lander på
        # `staging_verifisert` — aldri direkte `aktiv`.
        if hode is not None and hode[0] == "nodeaktivert":
            ma.execute("SELECT reaktiver_modul('m_wcag_audit',%s,'testreg')",
                       (hode[1],))
        if not claiming:
            ma.execute("SELECT registrer_release('m_wcag_audit',%s,1,%s,%s,"
                       "%s,'testreg')", (rid, kh, "11" * 32, "22" * 32))
            ma.execute("SELECT bytt_release('m_wcag_audit',%s,%s,1,%s,"
                       "'testreg')", (miljo, rid, kh))
        if hode is None or hode[0] != "aktiv":
            # Veien er installert → staging_verifisert → aktiv, og `aktiv`
            # krever den claiming-deploymenten som nå står der (port 13).
            if hode is None or hode[0] == "installert":
                ma.execute("SELECT sett_modulstatus('m_wcag_audit',"
                           "'staging_verifisert',%s,'testreg')", (rid,))
            ma.execute("SELECT sett_modulstatus('m_wcag_audit','aktiv',%s,"
                       "'testreg')", (rid,))
        ma.commit()
    finally:
        ma.close()


def _wcag_policy(migrator_, *, med_handling=True,
                 ved_brudd="unntakskø", tillatt_for=("bestiller",),
                 med_vilkaar=False, vilkar_verifikator="v_domenekontroll"):
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
            # `med_vilkaar` = AKTIVERINGSFORMEN: nøyaktig policyen
            # aktiveringsporten krever for en ekstern_lesing-modul
            # (målautorisasjonsvilkår + frekvens). Bestillingsveien må da
            # selv attestere domenekontrollen — det er DEN kjeden
            # test_vilkarspolicy_far_tillat måler.
            **({"vilkaar": [{"navn": "domenekontroll_verifisert",
                             "verifikator": vilkar_verifikator}]}
               if med_vilkaar else {}),
            "reversering": {"type": "direkte"}})
        if med_vilkaar:
            # ID-en er BÅDE policyens tillitsvalg og nøkkeloppslaget:
            # motoren slår opp attestasjonens verifikator i policyens
            # `verifikatorer` — plattformattestasjonen sier
            # `v_domenekontroll`, så en policy som stoler på noe annet
            # (negativkontrollen) skal stoppe.
            p["verifikatorer"][vilkar_verifikator] = {
                "beskrivelse": "Plattformens domenekontroll",
                "betrodd_for": ["domenekontroll_verifisert"]}
    policyregister.registrer(migrator_, TENANT, p, p["meta"]["status"])
    migrator_.commit()
    _sikre_typeregistrering()
    return p


def _verifiser_domene(migrator_, hostname, revalidert="now()"):
    """Samme radform som `verifiser_domenekontroll` (016 §2) skriver:
    verifisert_ts, siste_vellykkede_revalidering og utloper settes alle —
    det er DEN raden som er gyldig, ikke bare `status='verifisert'`."""
    _sett_kontekst(migrator_, TENANT)
    migrator_.execute(
        "INSERT INTO domenekontroll (tenant, hostname, status,"
        " autorisasjonsgenerasjon, verifisert_ts,"
        " siste_vellykkede_revalidering, utloper)"
        f" VALUES (%s,%s,'verifisert',1,now(),{revalidert},"
        " now()+interval '90 days')"
        " ON CONFLICT (tenant, hostname) DO UPDATE SET status='verifisert',"
        f" siste_vellykkede_revalidering={revalidert},"
        " utloper=now()+interval '90 days'",
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


def _kjernenokkel(nokkel, kropp=None):
    """Kjernens idempotensnøkkel for en bestilling.

    Nøkkelen bærer BÅDE klientens nøkkel og intensjonshashen (Codex P1),
    og formen eies av `bestilling.kjernenokkel_for`. Testene avleder den
    derfra i stedet for å skrive den av: skrevet av, ville de fortsatt
    vært grønne den dagen produksjonskoden byttet form."""
    import api.bestilling as bm
    n = bm.normaliser(TENANT, kropp if kropp is not None else _gyldig_kropp())
    return bm.kjernenokkel_for(nokkel, bm.intensjonshash(n))


@pg
def test_beslutningene_serialiseres_paa_klientens_nokkel(migrator, klient):
    """Codex P1: kjernen serialiserer på KJERNEnøkkelen, og den bærer
    intensjonen.

    To lovlige kropper under samme `Idempotency-Key` fikk derfor hver sin
    advisory-lås i `kjerne.behandle`, og begge kunne committe en
    beslutning: to kvoteplasser, to oppdrag — og den siste `ON CONFLICT
    ... DO NOTHING` etterlot det ene oppdraget uten klientnøkkelen sin.
    Konfliktgarantien var omgått av selve nøkkelvalget, uten at noe
    krasjet.

    Samtidigheten måles her deterministisk: en ANNEN tilkobling holder
    nøkkelens lås mens forespørselen går inn. Endepunktet skal da svare
    409 uten å ta noen beslutning i det hele tatt.

    Kontroll: fjern `pg_try_advisory_lock`-blokka i `bestill_endepunkt`,
    så blir denne rød.
    """
    import api.bestilling as bm
    _wcag_policy(migrator)
    _verifiser_domene(migrator, "kunde.example")
    cookie, csrf = _adminsesjon()
    nokkel = "n-" + secrets.token_hex(8)
    kropp = _gyldig_kropp()

    laas = psycopg.connect(DSN)
    try:
        navn = bm.laasenavn_for(TENANT, nokkel)
        assert laas.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
            (navn,)).fetchone()[0] is True
        r = _bestill(klient, cookie, csrf, kropp, nokkel)
        assert (r.status_code, r.json()["feil"]) == (
            409, "idempotenskonflikt"), r.text
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s AND"
            " idempotency_key=%s",
            (TENANT, _kjernenokkel(nokkel, kropp))).fetchone()[0] == 0, \
            "en forespørsel som ikke fikk låsen tok likevel en beslutning"
        migrator.rollback()
    finally:
        laas.execute("SELECT pg_advisory_unlock_all()")
        laas.close()

    # ... og når låsen er sluppet, går nøyaktig den samme forespørselen
    # gjennom: låsen er en serialisering, ikke en permanent stenging. Det
    # beviser samtidig at endepunktet SLIPPER sin egen lås — den andre
    # kjøringen under tar den samme.
    for _ in range(2):
        r = _bestill(klient, cookie, csrf, kropp, nokkel)
        assert r.status_code == 200, r.text
        assert r.json()["beslutning"] == "tillat", r.text


@pg
@dekker("idempotensnokkel_reservert")
def test_kaller_kan_ikke_plante_raden_gjenopprettingen_stoler_paa(
        migrator, klient, token):
    """Codex P1, runde 5: `idempotens` er ETT rom, delt av endepunktene.

    Bestillingens gjenoppretting leser `idempotens.respons` for
    kjernenøkkelen som BEVIS på en committet beslutning, og lenker for et
    TILLAT oppdraget til dens loggpost uten å etterprøve grunnlaget — det
    lar seg ikke etterprøve her, siden input-hashen ikke kan regnes ut på
    nytt (attestasjonen hviler på mutabel domenetilstand).

    Kjernenøkkelen er samtidig en DETERMINISTISK funksjon av klientens egen
    nøkkel og kropp. `/v1/beslutning` førte klientens `Idempotency-Key` rett
    inn i den samme tabellen, så en kaller hos samme tenant med
    `decision:write` kunne regne ut nøkkelen, kjøre sin EGEN beslutning
    under den, og la bestillingen arve et svar den aldri tok.

    Kontroll: fjern `klientvalgt_nokkel=True` i `app.py`, eller tøm
    `kjerne.RESERVERTE_NOKKELROM`, så blir denne rød.
    """
    p = _wcag_policy(migrator)
    tok, _ = token()
    nokkel = "n-" + secrets.token_hex(8)
    forfalsket = _kjernenokkel(nokkel)
    assert forfalsket.startswith("bestilling:"), forfalsket

    r = klient.post("/v1/beslutning",
                    json={"policy_id": p["meta"]["policy_id"], "event": {}},
                    headers={"authorization": f"Bearer {tok}",
                             "idempotency-key": forfalsket})
    assert (r.status_code, r.json()["feil"]) == (
        400, "idempotensnokkel_reservert"), r.text

    # Ingen rad, ingen loggpost: forsøket fikk ikke engang legge beslag på
    # nøkkelen. Blir det stående en `paagaar`-rad, er bestillingens neste
    # forsøk blokkert av en fremmed.
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT count(*) FROM idempotens WHERE tenant=%s AND nokkel=%s",
        (TENANT, forfalsket)).fetchone()[0] == 0
    assert migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s AND"
        " idempotency_key=%s", (TENANT, forfalsket)).fetchone()[0] == 0
    migrator.rollback()


@pg
@dekker("bestilling_hostname_uverifisert")
def test_uverifisert_hostname_avvises_for_beslutningen(migrator, klient):
    """Port 9 (sikkerhetsinvariant): avvist FØR beslutningen — ingen
    loggpost, intet oppdrag. Kontroll: fjern `_verifisert_hostname`-porten,
    så blir denne rød."""
    _wcag_policy(migrator)
    cookie, csrf = _adminsesjon()
    nokkel = "n-" + secrets.token_hex(8)
    kropp = _gyldig_kropp("fremmed.example")
    r = _bestill(klient, cookie, csrf, kropp, nokkel)
    assert (r.status_code, r.json()["feil"]) == (
        403, "bestilling_hostname_uverifisert"), r.text
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s AND"
        " idempotency_key=%s",
        (TENANT, _kjernenokkel(nokkel, kropp))).fetchone()[0]
    migrator.rollback()
    assert n == 0, "beslutningen ble tatt for et uautorisert mål"


@pg
@dekker("bestilling_hostname_uverifisert")
def test_foreldet_revalidering_er_ikke_et_autorisert_maal(migrator, klient):
    """Codex P2: porten må stille egress-autoritetens HELE spørsmål.

    Et domene kan stå `verifisert` med uutløpt `utloper` lenge etter at
    den daglige revalideringen sluttet å lykkes — `v_domeneautorisasjon.
    gyldig` krever derfor også en revalidering nyere enn 72 timer. Porten
    så bare på status og `utloper`, så bestillingen brant kvote og la seg
    i køen for å bli avvist av egress senere.

    Kontroll: fjern ferskhetsleddet fra `DOMENE_GYLDIG_SQL`, så blir
    denne rød.
    """
    _wcag_policy(migrator)
    _verifiser_domene(migrator, "kunde.example",
                      revalidert="now()-interval '73 hours'")
    cookie, csrf = _adminsesjon()
    nokkel = "n-" + secrets.token_hex(8)
    r = _bestill(klient, cookie, csrf, _gyldig_kropp(), nokkel)
    assert (r.status_code, r.json()["feil"]) == (
        403, "bestilling_hostname_uverifisert"), r.text
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s AND"
        " idempotency_key=%s", (TENANT, _kjernenokkel(nokkel))).fetchone()[0]
    migrator.rollback()
    assert n == 0, "beslutningen ble tatt for et foreldet domene"

    # ... og en fersk revalidering på det SAMME domenet slipper gjennom:
    # porten avviser foreldelsen, ikke domenet.
    _verifiser_domene(migrator, "kunde.example")
    r = _bestill(klient, cookie, csrf, _gyldig_kropp(),
                 "n-" + secrets.token_hex(8))
    assert r.status_code == 200, r.text


def test_domenepredikatet_speiler_visningen():
    """Statisk port: bestillingens gyldighetspredikat ER `v_domene-
    autorisasjon.gyldig` (016 §6), ordrett. To definisjoner av «gyldig
    domene» er én definisjon for mye — glir de fra hverandre, autoriserer
    bestillingsveien mål egress vil avvise."""
    import re
    from pathlib import Path

    from api.bestilling import DOMENE_GYLDIG_SQL
    kilde = (Path(__file__).resolve().parents[1] / "db" / "migrations"
             / "016_domene_egress_artefakt.sql").read_text(encoding="utf-8")
    m = re.search(r"\((status = 'verifisert'.*?)\)\s*\n\s*AS gyldig",
                  kilde, re.S)
    assert m, "fant ikke `gyldig`-uttrykket i 016 — er visningen omskrevet?"
    normaliser_ = lambda s: " ".join(s.split())            # noqa: E731
    assert normaliser_(DOMENE_GYLDIG_SQL) == normaliser_(m.group(1)), \
        "bestillingens domenepredikat har glidd fra visningens"


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
def test_dypt_nostet_kropp_er_request_feil(migrator, klient):
    """Codex P2: `json.loads` er REKURSIV. Et syntaktisk gyldig, dypt
    nøstet dokument på noen få kilobyte ligger godt under kroppsgrensen og
    treffer likevel rekursjonsgrensen. RecursionError er en RuntimeError,
    ikke en ValueError, så `except ValueError` alene slapp klientinput ut
    som generisk 500 i stedet for `request_feilformet`.

    Kroppen bygges som TEKST: `json.dumps` av en så dyp struktur ville
    tatt livet av testen selv, ikke serveren. Dybden krysses mot
    parseren HER, så testen aldri stille blir grønn av at kroppen ble
    for grunn til å nå grensen (C-parserens tak flytter seg mellom
    Python-versjoner — 3.12 tåler 8 000 nivåer, ikke 10 000).

    MUTASJONEN SOM DREPER DENNE: fjern RecursionError fra except-en rundt
    bestillingskroppens `json.loads`.
    """
    import json as jsonmodul

    from api import sesjon as sesjonmodul
    _wcag_policy(migrator)
    cookie, csrf = _adminsesjon()
    dybde = 15000
    kropp = '{"a":' * dybde + "1" + "}" * dybde
    assert len(kropp) < 200_000, "testkroppen skal ligge under kroppsgrensen"
    with pytest.raises(RecursionError):
        jsonmodul.loads(kropp)
    r = klient.post("/v1/bestilling", content=kropp,
                    headers={"X-Disponit-CSRF": csrf,
                             "content-type": "application/json"},
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert (r.status_code, r.json()["feil"]) == (
        400, "request_feilformet"), r.text


@pg
def test_gjenoppretting_bruker_beslutningens_policy(migrator, klient,
                                                    monkeypatch):
    """Codex P2: en halvferdig bestilling gjenopprettes med policyen
    beslutningen ble tatt under, ikke den som er aktiv nå.

    Dør prosessen etter at `kjerne.behandle` har committet, men før
    oppdraget og `bestilling_idempotens` er skrevet, er kjernens egen
    idempotensrad det eneste sporet. Kjernens input-hash dekker
    policy-id-en, så en retry etter et policybytte regnet ut en NY hash
    og fikk `idempotenskonflikt` — for alltid, med en committet
    TILLAT-beslutning (kvote brent) stående uten oppdraget sitt.

    Krasjet simuleres med den ene veien som allerede ruller tilbake ETTER
    kjernens commit: en oppdragstype uten deklarert frist gir 500 og
    ingen bokføring.

    Kontroll: la endepunktet alltid velge den AKTIVE policyen, så blir
    retryen her `idempotenskonflikt`.
    """
    import copy

    import oppdragskontrakt
    from api import policyregister
    p = _wcag_policy(migrator)
    _verifiser_domene(migrator, "kunde.example")
    cookie, csrf = _adminsesjon()
    nokkel = "n-" + secrets.token_hex(8)
    kropp = _gyldig_kropp()

    # (1) Beslutningen committes; halen dør før oppdrag og bokføring.
    with monkeypatch.context() as mp:
        mp.setattr(oppdragskontrakt, "utforelsesfrist_s",
                   lambda *a, **k: None)
        r = _bestill(klient, cookie, csrf, kropp, nokkel)
    assert (r.status_code, r.json()["feil"]) == (500, "intern_feil"), r.text
    _sett_kontekst(migrator, TENANT)
    logg = migrator.execute(
        "SELECT id FROM revisjonslogg WHERE tenant=%s AND idempotency_key=%s",
        (TENANT, _kjernenokkel(nokkel, kropp))).fetchall()
    assert len(logg) == 1, "beslutningen skulle vært committet av kjernen"
    assert migrator.execute(
        "SELECT count(*) FROM bestilling_idempotens WHERE tenant=%s AND"
        " idempotensnokkel=%s", (TENANT, nokkel)).fetchone()[0] == 0
    assert migrator.execute(
        "SELECT count(*) FROM oppdrag WHERE tenant=%s AND"
        " beslutning_loggpost_id=%s", (TENANT, logg[0][0])).fetchone()[0] == 0
    migrator.rollback()

    # (2) Tenanten bytter til en ANNEN policy — samme innhold, ny id.
    ny = copy.deepcopy(p)
    ny["meta"]["policy_id"] = "annen-bestillingspolicy"
    policyregister.registrer(migrator, TENANT, ny, ny["meta"]["status"])
    migrator.execute("UPDATE policyer SET aktiv=false WHERE tenant=%s AND"
                     " policy_id=%s", (TENANT, p["meta"]["policy_id"]))
    # Pekeren i `policy_hode` er aktiv-autoriteten (012 §1): flyttes ikke
    # den med, avviser `policyer_peker_konsistent` hele transaksjonen.
    migrator.execute("UPDATE policy_hode SET aktiv_versjon=NULL WHERE"
                     " tenant=%s AND policy_id=%s",
                     (TENANT, p["meta"]["policy_id"]))
    migrator.commit()

    # (3) Retryen FULLFØRER den beslutningen som alt er tatt.
    r2 = _bestill(klient, cookie, csrf, kropp, nokkel)
    assert r2.status_code == 200, r2.text
    svar = r2.json()
    assert svar["beslutning"] == "tillat" and svar["oppdrag_id"], svar
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s AND"
        " idempotency_key=%s",
        (TENANT, _kjernenokkel(nokkel, kropp))).fetchone()[0] == 1, \
        "retryen tok en NY beslutning i stedet for å gjenspille den gamle"
    assert migrator.execute(
        "SELECT beslutning_loggpost_id FROM oppdrag WHERE tenant=%s AND id=%s",
        (TENANT, svar["oppdrag_id"])).fetchone()[0] == logg[0][0], \
        "oppdraget ble ikke koblet til den opprinnelige beslutningen"
    migrator.rollback()


@pg
def test_gjenoppretting_taaler_ny_revalidering_i_vinduet(migrator, klient,
                                                         monkeypatch):
    """Codex P2, runde 2: gjenopprettingen skal ikke bygge attestasjonen om.

    Forrige runde gjenopprettet POLICYEN og bygget resten av hendelsen på
    nytt for å treffe kjernens input-hash. Men hendelsen bærer også
    plattformens domenekontroll-attestasjon, og den er avledet av
    `siste_vellykkede_revalidering` — MUTABEL domenetilstand. Lykkes den
    planlagte revalideringen i nettopp krasjvinduet, får retryen nytt
    `utstedt`/`utloper`, ny `jti`, ny signatur og dermed ny input-hash:
    `idempotenskonflikt`, for alltid, med en committet TILLAT-beslutning
    (kvote brent) stående uten oppdraget sitt.

    Samme krasjsimulering som policytesten over: en oppdragstype uten
    deklarert frist gir 500 etter kjernens commit.
    """
    import oppdragskontrakt
    _wcag_policy(migrator)
    _verifiser_domene(migrator, "kunde.example",
                      revalidert="now() - interval '20 hours'")
    cookie, csrf = _adminsesjon()
    nokkel = "n-" + secrets.token_hex(8)
    kropp = _gyldig_kropp()

    # (1) Beslutningen committes; halen dør før oppdrag og bokføring.
    with monkeypatch.context() as mp:
        mp.setattr(oppdragskontrakt, "utforelsesfrist_s",
                   lambda *a, **k: None)
        r = _bestill(klient, cookie, csrf, kropp, nokkel)
    assert (r.status_code, r.json()["feil"]) == (500, "intern_feil"), r.text
    _sett_kontekst(migrator, TENANT)
    logg = migrator.execute(
        "SELECT id FROM revisjonslogg WHERE tenant=%s AND idempotency_key=%s",
        (TENANT, _kjernenokkel(nokkel, kropp))).fetchall()
    assert len(logg) == 1, "beslutningen skulle vært committet av kjernen"
    migrator.rollback()

    # (2) Den planlagte revalideringen lykkes — attestasjonsgrunnlaget
    #     flytter seg. Domenet er fortsatt gyldig; det er nettopp poenget.
    _verifiser_domene(migrator, "kunde.example", revalidert="now()")

    # (3) Retryen FULLFØRER beslutningen som alt er tatt, uten å bygge
    #     attestasjonen om — og dermed uten idempotenskonflikt.
    r2 = _bestill(klient, cookie, csrf, kropp, nokkel)
    assert r2.status_code == 200, r2.text
    svar = r2.json()
    assert svar["beslutning"] == "tillat" and svar["oppdrag_id"], svar
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s AND"
        " idempotency_key=%s",
        (TENANT, _kjernenokkel(nokkel, kropp))).fetchone()[0] == 1, \
        "retryen tok en NY beslutning i stedet for å gjenspille den gamle"
    assert migrator.execute(
        "SELECT beslutning_loggpost_id FROM oppdrag WHERE tenant=%s AND id=%s",
        (TENANT, svar["oppdrag_id"])).fetchone()[0] == logg[0][0], \
        "oppdraget ble ikke koblet til den opprinnelige beslutningen"
    migrator.rollback()


@pg
def test_gjenoppretting_arver_ikke_en_annen_intensjon(migrator, klient,
                                                      monkeypatch):
    """Krasjvinduet må hverken arve beslutningen eller ta en ny (Codex P1).

    `bestilling_idempotens` er konfliktporten — men i vinduet mellom
    kjernens commit og bokføringen finnes ikke raden ennå.

    Runde 3: sto kjernens nøkkel på klientens nøkkel ALENE, fant
    gjenopprettingen den committede beslutningen uansett hva retryen ba
    om, og halen krypterte retryens payload inn i den: et TILLAT gitt for
    én side ble oppdraget «crawl 50 sider».

    Runde 4: intensjonen inn i nøkkelen løste arven, men delte samtidig
    nøkkelrommet. Retryen fant da ingen rad på SIN kjernenøkkel og tok sin
    EGEN beslutning — en ny kvoteplass og et nytt oppdrag på en
    klientnøkkel som per kontrakt bærer nøyaktig én. Endepunktets lovede
    «samme nøkkel, ulik intensjon ⇒ konflikt» gjaldt altså overalt unntatt
    her.

    Nå er begge deler dekket: oppslaget spør på klientnøkkelens prefiks og
    leser intensjonen ut av raden det fant.

    Kontroll: sett oppslaget tilbake til hele `kjernenokkel`, så blir
    konfliktpåstanden under rød; fjern `hash_` fra nøkkelen, så blir
    payload-påstanden det.
    """
    import oppdragskontrakt
    from db import kryptering
    _wcag_policy(migrator)
    _verifiser_domene(migrator, "kunde.example")
    cookie, csrf = _adminsesjon()
    nokkel = "n-" + secrets.token_hex(8)
    enkeltside = _gyldig_kropp()
    nettsted = _gyldig_kropp(omfang="nettsted", maks_sider=50)

    # (1) Beslutningen for ENKELTSIDE committes; halen dør før bokføringen.
    with monkeypatch.context() as mp:
        mp.setattr(oppdragskontrakt, "utforelsesfrist_s",
                   lambda *a, **k: None)
        r = _bestill(klient, cookie, csrf, enkeltside, nokkel)
    assert (r.status_code, r.json()["feil"]) == (500, "intern_feil"), r.text
    _sett_kontekst(migrator, TENANT)
    forrige = migrator.execute(
        "SELECT id FROM revisjonslogg WHERE tenant=%s AND idempotency_key=%s",
        (TENANT, _kjernenokkel(nokkel, enkeltside))).fetchall()
    assert len(forrige) == 1, "beslutningen skulle vært committet av kjernen"
    migrator.rollback()

    # (2) Retryen bruker SAMME nøkkel, men ber om noe annet: konflikt.
    #     Nøkkelen bærer alt én beslutning, og den gjaldt noe annet.
    r2 = _bestill(klient, cookie, csrf, nettsted, nokkel)
    assert (r2.status_code, r2.json()["feil"]) == (409, "idempotenskonflikt"), \
        r2.text

    # (3) INGEN ny beslutning, intet nytt oppdrag: hverken på nettstedets
    #     kjernenøkkel eller på enkeltsidens.
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s AND"
        " idempotency_key=%s",
        (TENANT, _kjernenokkel(nokkel, nettsted))).fetchone()[0] == 0, \
        "retryen tok en NY beslutning på en nøkkel som alt hadde en"
    assert migrator.execute(
        "SELECT count(*) FROM oppdrag WHERE tenant=%s AND"
        " beslutning_loggpost_id=%s",
        (TENANT, forrige[0][0])).fetchone()[0] == 0, \
        "retryen skrev et oppdrag på beslutningen om en ANNEN intensjon"
    migrator.rollback()

    # (4) Og den OPPRINNELIGE intensjonen kan fortsatt fullføre seg selv:
    #     beslutningen fra (1) er tatt, så retryen med samme kropp leser
    #     den og skriver oppdraget halen aldri rakk.
    r3 = _bestill(klient, cookie, csrf, enkeltside, nokkel)
    assert r3.status_code == 200, r3.text
    svar = r3.json()
    assert svar["beslutning"] == "tillat" and svar["oppdrag_id"], svar
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT beslutning_loggpost_id, payload_kryptert, key_id, nonce"
        " FROM oppdrag WHERE tenant=%s AND id=%s",
        (TENANT, svar["oppdrag_id"])).fetchone()
    assert rad[0] == forrige[0][0], \
        "gjenopprettingen tok en ny beslutning i stedet for å lese sin egen"
    dek = kryptering.hent_dek(migrator, TENANT, rad[2])
    payload = kryptering.dekrypter(dek, rad[1], rad[3], TENANT, rad[2])
    migrator.rollback()
    assert payload["omfang"] == "enkeltside", payload


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
                                _kjernenokkel(nokkel))).fetchone()
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
    # Codex P2: HELE svaret, ikke en redusert form. Gjenspillets
    # primærscenario ER at førstesvaret gikk tapt — da er `begrunnelse`
    # nøyaktig det flaten trenger for å annonsere STOPP-grunnen, og den
    # falt bort.
    assert {k: v for k, v in r2.json().items() if k != "request_id"} == \
        {k: v for k, v in svar.items() if k != "request_id"}, r2.text


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
def test_idempotensnokkelens_lengde_avvises_for_beslutningen(migrator, klient):
    """Codex P2: en nøkkel lagringen ikke kan ta imot, avvises FØR motoren.

    Lagringen krever 8–200 tegn; endepunktet godtok enhver ikke-blank
    verdi. Med en for kort eller for lang nøkkel committet
    `kjerne.behandle` beslutningen, og CHECKen slo til på innsettingen
    etterpå: outbox-transaksjonen rullet tilbake, klienten fikk 500, og
    HVER retry gjentok det samme — en committet beslutning med brent
    frekvenskvote og en bestilling som aldri kunne fullføres.

    Kontroll: fjern lengdesjekken i endepunktet, så blir denne rød.
    """
    _wcag_policy(migrator)
    _verifiser_domene(migrator, "lengde.example")
    cookie, csrf = _adminsesjon()
    for nokkel in ("kort", "x" * 201):
        r = _bestill(klient, cookie, csrf, _gyldig_kropp("lengde.example"),
                     nokkel)
        assert (r.status_code, r.json()["feil"]) == (
            400, "request_feilformet"), (nokkel, r.text)
    # ... og INGEN beslutning ble tatt: verken loggpost, oppdrag eller
    # frekvensreservasjon. Det er hele poenget — en 500 her ville vært en
    # brent kvote uten en bestilling å vise til.
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM oppdrag WHERE tenant=%s AND"
        " opprinnelse='beslutning'", (TENANT,)).fetchone()[0]
    frek = migrator.execute(
        "SELECT count(*) FROM frekvens_hendelser WHERE tenant=%s",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert (n, frek) == (0, 0), (n, frek)

    # Grensene selv er lovlige — sjekken avviser lengder, ikke nøkler.
    r_ok = _bestill(klient, cookie, csrf, _gyldig_kropp("lengde.example"),
                    "a" * 8)
    assert r_ok.status_code == 200, r_ok.text


@pg
def test_idempotensnokkelens_grenser_speiler_lagringen(migrator):
    """Den statiske halvdelen: Python-grensene ER lagringens.

    Går de fra hverandre, er det igjen mulig å ta en beslutning som ikke
    kan bokføres — nøyaktig funnet over, bare gjeninnført ovenfra.
    """
    from api.bestilling import IDEMPOTENSNOKKEL_MAKS, IDEMPOTENSNOKKEL_MIN
    definisjon = migrator.execute(
        "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c"
        " WHERE c.conrelid = 'bestilling_idempotens'::regclass"
        "   AND pg_get_constraintdef(c.oid) LIKE '%%idempotensnokkel%%'"
        "   AND c.contype = 'c'").fetchone()
    migrator.rollback()
    assert definisjon is not None, "lengde-CHECKen finnes ikke lenger"
    assert f"{IDEMPOTENSNOKKEL_MIN}" in definisjon[0] \
        and f"{IDEMPOTENSNOKKEL_MAKS}" in definisjon[0], \
        (f"api.bestilling sier {IDEMPOTENSNOKKEL_MIN}–"
         f"{IDEMPOTENSNOKKEL_MAKS}, lagringen sier {definisjon[0]}")


@pg
def test_gjenspill_overlever_at_domenet_mister_verifiseringen(migrator,
                                                              klient):
    """Codex P2: hostname-porten er en OPPRETTELSES-regel.

    Et gjenspill oppretter ingenting — det leverer et resultat som alt er
    committet. Sto porten først, fikk en bestilling som ble utført, men
    mistet svaret sitt, `bestilling_hostname_uverifisert` på retryen om
    verifiseringen i mellomtiden utløp eller ble trukket: samme nøkkel,
    samme intensjon, nytt svar. En verifisering som utløper etterpå gjør
    ikke det som skjedde ugjort.

    Kontroll: flytt `_verifisert_hostname` foran idempotens-oppslaget
    igjen, så blir denne rød.
    """
    _wcag_policy(migrator)
    _verifiser_domene(migrator, "utlopt.example")
    cookie, csrf = _adminsesjon()
    nokkel = "n-" + secrets.token_hex(8)
    r1 = _bestill(klient, cookie, csrf, _gyldig_kropp("utlopt.example"),
                  nokkel)
    assert r1.json()["beslutning"] == "tillat", r1.text

    # Verifiseringen UTLØPER etter at bestillingen er fullført — nøyaktig
    # den ene halvdelen `_verifisert_hostname` måler ved siden av status.
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "UPDATE domenekontroll SET utloper = now() - interval '1 minute'"
        " WHERE tenant=%s AND hostname=%s", (TENANT, "utlopt.example"))
    migrator.commit()

    r2 = _bestill(klient, cookie, csrf, _gyldig_kropp("utlopt.example"),
                  nokkel)
    assert r2.status_code == 200, r2.text
    assert r2.headers.get("idempotent-replay") == "1"
    assert r2.json()["oppdrag_id"] == r1.json()["oppdrag_id"]

    # ... mens en NY bestilling mot det samme målet fortsatt stoppes:
    # porten er flyttet, ikke fjernet.
    r3 = _bestill(klient, cookie, csrf, _gyldig_kropp("utlopt.example"),
                  "n-" + secrets.token_hex(8))
    assert (r3.status_code, r3.json()["feil"]) == (
        403, "bestilling_hostname_uverifisert"), r3.text


@pg
def test_vilkarspolicy_far_tillat_via_plattformattestasjonen(migrator,
                                                             klient):
    """AKTIVERINGSFORMEN hele veien: policyen bærer målautorisasjons-
    vilkåret aktiveringsporten krever — og bestillingen får likevel
    TILLAT, fordi plattformen selv attesterer domenekontrollen den
    nettopp verifiserte. Uten mintingen i bestilling.py ender denne i
    unntakskøen med attestasjon_mangler (kontrollen: negativtesten
    under, der policyen stoler på en annen verifikator)."""
    _wcag_policy(migrator, med_vilkaar=True)
    _verifiser_domene(migrator, "vilkaar.example")
    cookie, csrf = _adminsesjon()
    r = _bestill(klient, cookie, csrf, _gyldig_kropp("vilkaar.example"),
                 "n-" + secrets.token_hex(8))
    assert r.status_code == 200, r.text
    assert r.json()["beslutning"] == "tillat", r.text
    assert r.json()["oppdrag_id"]


@pg
def test_vilkarspolicy_med_fremmed_verifikator_stopper(migrator, klient):
    """Negativkontrollen: policyen stoler KUN på `v_annen` — plattformens
    attestasjon (v_domenekontroll) teller da ikke, og motoren stopper
    med verifikator_ikke_betrodd. Beviser at TILLAT over kommer fra en
    VERIFISERT attestasjon, ikke fra at vilkårsporten ble borte."""
    _wcag_policy(migrator, med_vilkaar=True, vilkar_verifikator="v_annen")
    _verifiser_domene(migrator, "vilkaar2.example")
    cookie, csrf = _adminsesjon()
    r = _bestill(klient, cookie, csrf, _gyldig_kropp("vilkaar2.example"),
                 "n-" + secrets.token_hex(8))
    assert r.status_code == 200, r.text
    assert r.json()["beslutning"] != "tillat", r.text


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


def test_bestillingstyper_arver_kontraktens_frist():
    """Codex P1 (statisk): bestillingsflaten har INGEN egen fristtabell.

    Første utgave bar `frister_s={"enkeltside": 1800, "nettsted": 5400}`.
    De 90 minuttene var både en duplikat av kontrakten OG feil: både
    eier-leasen (037) og opplastingskapabiliteten (017) klemmes til 3600 s
    uten fornyelsesvei, så en kontroll som lovlig brukte 90 min mistet
    begge mens dens eget utførelsesvindu fortsatt sto åpent — en annen
    controller kunne reclaime oppdraget og starte duplisert trafikk mot
    kundens nettsted.

    Porten er mekanisk: HVERT omfang en bestillingstype tilbyr må ha en
    frist i `oppdragskontrakt.UTFORELSESFRIST_VALG`, og ingen av dem kan
    overstige det taket resten av stacken faktisk holder.
    """
    import oppdragskontrakt
    from api.bestilling import BESTILLINGSTYPER

    #: Taket claim-leasen (037) og opplastingskapabiliteten (017) deler.
    TAK_S = 3600
    assert not hasattr(next(iter(BESTILLINGSTYPER.values())), "frister_s"), \
        "bestillingsflaten har fått sin egen fristtabell tilbake"
    for navn, bt in BESTILLINGSTYPER.items():
        for omfang in bt.omfang:
            frist = oppdragskontrakt.utforelsesfrist_s(
                bt.oppdragstype, {"omfang": omfang})
            assert frist is not None, \
                f"{navn}/{omfang}: ingen frist deklarert på kontrakten"
            assert 0 < frist <= TAK_S, \
                (f"{navn}/{omfang}: {frist} s overstiger claim-leasens og"
                 f" opplastingskapabilitetens tak på {TAK_S} s — det er"
                 " ingen fornyelsesvei for noen av dem")


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
@dekker("bestillingstype_utilgjengelig")
def test_uregistrert_type_nektes_for_beslutningen(migrator, klient,
                                                  monkeypatch):
    """Runtime-vakta (avløseren for den rødstoppende deploy-porten, 18/8):
    en kodefestet bestillingstype hvis oppdragstype IKKE er registrert →
    503 `bestillingstype_utilgjengelig` FØR beslutningen — ingen loggpost,
    intet oppdrag, ingen kvote brent."""
    import api.bestilling as bm
    _wcag_policy(migrator)
    _verifiser_domene(migrator, "kunde.example")
    monkeypatch.setitem(
        bm.BESTILLINGSTYPER, "kontroll.wcag.nettsted",
        bm.Bestillingstype(
            handling="kontroll.wcag.nettsted",
            oppdragstype="kontroll.uregistrert." + secrets.token_hex(4),
            eiermodul="m_wcag_audit",
            kravsett=("wcag21_aa",), omfang=("enkeltside", "nettsted")))
    cookie, csrf = _adminsesjon()
    nokkel = "n-" + secrets.token_hex(8)
    r = _bestill(klient, cookie, csrf, _gyldig_kropp(), nokkel)
    assert (r.status_code, r.json()["feil"]) == (
        503, "bestillingstype_utilgjengelig"), r.text
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s AND"
        " idempotency_key=%s", (TENANT, _kjernenokkel(nokkel))).fetchone()[0]
    migrator.rollback()
    assert n == 0, "beslutningen ble tatt for en type uten utfører"


def _syntetisk_modul(status, *, deployment_miljo=None):
    """-> (modul_id, oppdragstype) for en modul i en gitt tilstand.

    Egen modul med eget prefiksnavnerom, bygget gjennom de HERDEDE
    overgangsfunksjonene — ikke rå INSERT-er: da er tilstanden en
    virkelig modulregisteret kan komme i, og statemaskinen sier fra hvis
    testen ber om noe ulovlig. `m_wcag_audit` røres aldri; den delte
    testbasen skal ikke bære spor av disse testene.
    """
    from miljo import gjeldende_miljo
    from .test_wcag_kontroll import _mk_admin
    modul = "m-" + secrets.token_hex(4)
    ot = f"kontroll.syntetisk{secrets.token_hex(4)}.nettsted"
    kh, rid = secrets.token_hex(32), "r-" + secrets.token_hex(4)
    miljo = deployment_miljo or gjeldende_miljo()
    ma = _mk_admin("disponit_modules_admin")
    try:
        ma.execute("SELECT installer_modul(%s,'testreg')", (modul,))
        ma.execute("SELECT registrer_kontrakt(%s,1,%s,%s,%s,'ekstern_lesing',"
                   "'direkte','testreg')",
                   (modul, kh, secrets.token_hex(32), secrets.token_hex(32)))
        ma.execute("SELECT registrer_oppdragstype(%s,%s,1,%s,'testreg')",
                   (ot, modul, kh))
        ma.execute("SELECT registrer_release(%s,%s,1,%s,%s,%s,'testreg')",
                   (modul, rid, kh, secrets.token_hex(32),
                    secrets.token_hex(32)))
        ma.execute("SELECT bytt_release(%s,%s,%s,1,%s,'testreg')",
                   (modul, miljo, rid, kh))
        if status != "installert":
            ma.execute("SELECT sett_modulstatus(%s,'staging_verifisert',%s,"
                       "'testreg')", (modul, rid))
        if status in ("aktiv", "nodeaktivert"):
            ma.execute("SELECT sett_modulstatus(%s,'aktiv',%s,'testreg')",
                       (modul, rid))
        if status == "nodeaktivert":
            ma.execute("SELECT noddeaktiver_modul(%s,'testnødstopp',"
                       "'testreg')", (modul,))
        ma.commit()
    finally:
        ma.close()
    return modul, ot


def _bestill_mot(migrator_, klient_, monkeypatch, modul, oppdragstype):
    """Bestill `kontroll.wcag.nettsted`, men bundet til en annen
    oppdragstype/eiermodul. Handling, kravsett og omfang er uendret, så
    policyveien er nøyaktig den samme som i den grønne bestillingen — det
    eneste som varierer er om utføreren kan claime."""
    import api.bestilling as bm
    monkeypatch.setitem(
        bm.BESTILLINGSTYPER, "kontroll.wcag.nettsted",
        bm.Bestillingstype(
            handling="kontroll.wcag.nettsted", oppdragstype=oppdragstype,
            eiermodul=modul, kravsett=("wcag21_aa",),
            omfang=("enkeltside", "nettsted")))
    cookie, csrf = _adminsesjon()
    nokkel = "n-" + secrets.token_hex(8)
    r = _bestill(klient_, cookie, csrf, _gyldig_kropp(), nokkel)
    _sett_kontekst(migrator_, TENANT)
    n = migrator_.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s AND"
        " idempotency_key=%s", (TENANT, _kjernenokkel(nokkel))).fetchone()[0]
    migrator_.rollback()
    return r, n


@pg
@pytest.mark.parametrize("status,grunn", [
    ("installert", "delvis onboardet — evidensen er ikke verifisert ennå"),
    ("nodeaktivert", "nødstoppet"),
])
def test_uclaimbar_modulstatus_nektes_for_beslutningen(
        migrator, klient, monkeypatch, status, grunn):
    """Codex P1: registerraden er IKKE nok.

    `oppdragstype_register` er immutabelt — raden står med rett eiermodul
    både mens onboardingen bare er PÅBEGYNT (`installert`/
    `staging_verifisert`) og etter et NØDSTOPP (`nodeaktivert`).
    `claim_neste_oppdrag` (037) krever i tillegg `modulhode.status =
    'aktiv'`, så et TILLAT i disse tilstandene ville gitt et oppdrag ingen
    arbeider kan plukke: det står i køen til `utforelsesfrist` passerer og
    dør der, med kvoten brent og kunden uten svar.

    Kontroll: fjern `registrert[1] != 'aktiv'` fra vakta, så blir denne
    rød.
    """
    _wcag_policy(migrator)
    _verifiser_domene(migrator, "kunde.example")
    modul, ot = _syntetisk_modul(status)
    r, n = _bestill_mot(migrator, klient, monkeypatch, modul, ot)
    assert (r.status_code, r.json()["feil"]) == (
        503, "bestillingstype_utilgjengelig"), (grunn, r.text)
    assert n == 0, f"beslutningen ble tatt for en modul som er {status}"


@pg
def test_claiming_i_annet_miljo_nektes_for_beslutningen(migrator, klient,
                                                        monkeypatch):
    """Codex P1, deployment-halvdelen: modulen er `aktiv`, men den eneste
    `claiming`-deploymenten står i et ANNET miljø.

    `claim_neste_oppdrag` matcher deploymenten på KALLERENS miljø, så en
    staging-arbeider gjør ikke et produksjonsoppdrag claimbart (og
    omvendt). Tilstanden er ikke konstruert: `aktiv` krever bare ÉN
    claiming-deployment et sted, så en modul som er rullet ut i staging og
    ikke i produksjon står nøyaktig slik.

    Kontroll: fjern `d.miljo = %s` fra vakta, så blir denne rød.
    """
    from miljo import PRODUKSJON, STAGING, gjeldende_miljo
    _wcag_policy(migrator)
    _verifiser_domene(migrator, "kunde.example")
    annet = PRODUKSJON if gjeldende_miljo() == STAGING else STAGING
    modul, ot = _syntetisk_modul("aktiv", deployment_miljo=annet)
    r, n = _bestill_mot(migrator, klient, monkeypatch, modul, ot)
    assert (r.status_code, r.json()["feil"]) == (
        503, "bestillingstype_utilgjengelig"), r.text
    assert n == 0, "beslutningen ble tatt for en type uten utfører i miljøet"


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
    import oppdragskontrakt
    # Den EKTE typen fra kontrakten — leseveien kjenner bare igjen paret
    # (oppdragstype, `rapport_artefakttype`), se Codex P2 lenger nede.
    # SJEKK-FØRST: navnet er FAST og registerraden immutabel, så på en
    # gjenbrukt base (lokal kjøring nr. 2) står den der alt — da er den
    # tilstanden testen trenger, ikke en kollisjon. Registrer bare når den
    # mangler; `artefakt.artefakttype` er en navne-FK, så raden virker
    # uansett hvilken kontrakt som registrerte den.
    at = oppdragskontrakt.OPPDRAGSTYPER[
        "kontroll.wcag.nettsted"].rapport_artefakttype
    if migrator.execute("SELECT 1 FROM artefakttype_register WHERE"
                        " artefakttype=%s", (at,)).fetchone() is None:
        migrator.rollback()
        _streng_type(migrator, modul, kh, skjema={"type": "object"},
                     navn=at)
    else:
        migrator.rollback()
    fremmed_at = _streng_type(migrator, modul, kh, skjema={"type": "object"})
    rapport = {"kravsett": "wcag21_aa", "sammendrag": {"kritisk": 0}}
    kanon = jcs.kanoniske_bytes(rapport)
    _sett_kontekst(migrator, TENANT)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, TENANT)
    ct, nonce = kryptering.krypter(dek, rapport, TENANT, key_id)

    def _promoter(oppdrag, artefakttype, ts="now()"):
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype, modul_id,"
            " release_id, kontraktversjon, kontrakt_hash, module_epoch,"
            " tilstand, storrelse_bytes, klartekst_sha256, ciphertext, nonce,"
            " dek_ref, kapabilitet_jti, promotert_ts)"
            " VALUES (%s,%s,%s,%s,'r1',1,%s,0,'promotert',%s,%s,%s,%s,%s,%s,"
            f" {ts})",
            (TENANT, oppdrag, artefakttype, modul, kh, len(kanon),
             _hl.sha256(kanon).hexdigest(), ct, nonce, key_id,
             "jti-" + secrets.token_hex(8)))
        migrator.commit()

    _promoter(oid, at, ts="now()-interval '1 minute'")

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

    # Codex P2: en type flaten IKKE kan vise skal aldri bli en 200.
    # `rapportInnhold` dereferer `sammendrag`/`sider_kontrollert` med en
    # gang, så et artefakt fra en annen registrert kontrakt ga et svar som
    # kastet under rendring. To halvdeler:
    #
    #   (a) et NYERE fremmed artefakt skygger ikke lenger for rapporten,
    _promoter(oid, fremmed_at)
    r3 = klient.get(f"/v1/rapport/{oid}",
                    headers={"authorization": f"Bearer {tok}"})
    assert r3.status_code == 200, r3.text
    assert r3.json()["artefakttype"] == at, \
        "et fremmed artefakt skygget for rapporten"

    #   (b) ... og et oppdrag som BARE har et fremmed artefakt er 404,
    #       samme dokumenterte «ikke funnet» som uten promotering.
    rt2 = _rt()
    try:
        oid2, _ = _beslutningsoppdrag(rt2, migrator)
    finally:
        rt2.close()
    _promoter(oid2, fremmed_at)
    r4 = klient.get(f"/v1/rapport/{oid2}",
                    headers={"authorization": f"Bearer {tok}"})
    assert r4.status_code == 404, r4.text


@pg
def test_reaperen_venter_aldri_paa_sakslasen(migrator):
    """043 §9 (Codex P1, runde 6): bakgrunnssveipet feller ingen operatør.

    043 innførte en ny låserekkefølge på nei-veien — SAK først, deretter
    kapabilitet og oppdrag — og kvitteringsveien måtte følge etter. Begge de
    menneskestyrte veiene går altså sak → oppdrag. Reaperen går motsatt vei:
    den plukker utløpte beslutningsoppdrag `FOR UPDATE` og går DERETTER til
    saken gjennom `sikre_sak_for_oppdrag`. Møtes de, er det en 40P01 — og
    taperen kan bli den signerte kvitteringen, som ved retry er forbi
    evidensfristen og dermed tapt for godt.

    Reaperen valgte allerede prinsippet for oppdragsraden (`SKIP LOCKED`:
    et opptatt oppdrag er neste sveips rad). Porten måler at den samme
    regelen nå gjelder saken: med saken låst av en annen transaksjon skal
    sveipet gå gjennom UTEN å vente og UTEN å ta kandidaten — og ta den
    først når låsen er borte.

    `lock_timeout` gjør fraværet av venting målbart: uten fiksen blokkerer
    kallet på sakslåsen og faller som `LockNotAvailable` i stedet for å
    henge testen.
    """
    from db.pg import koble

    rt = _rt()
    holder = None
    try:
        oid, _ = _utlopt_beslutningsoppdrag(rt, migrator)
        # Saken finnes ALT — som etter en tidligere sen kvittering. Lages
        # direkte, så oppdraget forblir en kandidat (reaperen ville satt
        # det `feilet`).
        _sett_kontekst(migrator, TENANT)
        migrator.execute("SET ROLE disponit_m37_claimer")
        sak = migrator.execute(
            "SELECT sikre_sak_for_oppdrag(%s,%s,'evidensfrist','test','r0')",
            (TENANT, oid)).fetchone()[0]
        migrator.execute("RESET ROLE")
        migrator.commit()

        # En pågående transaksjon holder saken — nei-veien eller
        # kvitteringsveien, begge tar den først.
        holder = koble(MIGRATOR_DSN)
        _sett_kontekst(holder, TENANT)
        holder.execute("SELECT 1 FROM unntak WHERE tenant=%s AND id=%s"
                       "   FOR UPDATE", (TENANT, sak))

        rt.execute("SET LOCAL lock_timeout='3s'")
        rader = rt.execute("SELECT tenant, oppdrag_id"
                           " FROM reap_evidensfrister(50)").fetchall()
        rt.commit()
        assert all(r[1] != oid for r in rader), (
            "reaperen tok en kandidat den ikke hadde sakslåsen for")

        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT status FROM oppdrag WHERE tenant=%s AND id=%s",
            (TENANT, oid)).fetchone()[0] == "opprettet",             "oppdraget ble lukket uten at saken var tatt"
        migrator.rollback()

        # ... og når låsen slippes, tar NESTE sveip den.
        holder.rollback(); holder.close(); holder = None
        rader2 = rt.execute("SELECT tenant, oppdrag_id"
                            " FROM reap_evidensfrister(50)").fetchall()
        rt.commit()
        assert any(r[1] == oid for r in rader2), (
            f"kandidaten ble aldri tatt etter at låsen gikk: {rader2}")
    finally:
        if holder is not None:
            holder.rollback(); holder.close()
        rt.close()
