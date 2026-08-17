"""PR-014c: skjemalageret, skjemavalideringen og målautorisasjonsregisteret.

Migrasjon 036 lukker CP5-hullet fra 014b: `skjema_hash` var en påstand
ingen kunne slå opp. Her prøves lageret (innholdsadressert, immutabelt for
alltid), den positive regelen i `registrer_artefakttype`, valideringen ved
OPPLASTING og ved PROMOTERING, sideeffektklassen `ekstern_lesing` og
`malautorisasjonsvilkar`-registeret.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
import hashlib
import json
import secrets
import sys
import threading
import time

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT, app, klient,  # noqa: F401
                       dekker, migrator, miljo, token)          # noqa: F401
from .test_m37 import _sett_kontekst
from .test_pr014b_artefakt_api import (_kvitteringskap, _kvitteringskropp,
                                       _oppdrag_owner, _post, _utsted_cap)
from .test_pr014b_artefaktkapabilitet import _plukket_oppdrag_med_binding

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _jcs_hash(skjema: dict) -> tuple[str, str]:
    from policy_validator import jcs
    kanon = jcs.kanoniske_bytes(skjema)
    return kanon.decode("utf-8"), hashlib.sha256(kanon).hexdigest()


#: Et strengt skjema: nøyaktig ett felt, lukket.
STRENGT = {"type": "object", "additionalProperties": False,
           "required": ["resultat"],
           "properties": {"resultat": {"enum": ["ok", "feil"]}}}


def _mk_admin(rolle):
    """migrator SET ROLE <rolle>, committed (varig på tvers av rollback) —
    speiler hjelperen i test_pr014b_artefaktkapabilitet."""
    from db.pg import koble
    c = koble(MIGRATOR_DSN)
    c.execute(f"SET ROLE {rolle}")
    c.commit()
    return c


def _registrer_skjema(skjema: dict) -> str:
    kanon, h = _jcs_hash(skjema)
    c = _mk_admin("disponit_modules_admin")
    try:
        c.execute("SELECT registrer_artefaktskjema(%s,%s,'test')", (kanon, h))
        c.commit()
    finally:
        c.close()
    return h


def _streng_type(migrator_, modul, kh, *, skjema=None) -> str:
    """Registrer en artefakttype bundet til det strenge skjemaet, under en
    kontrakt som alt finnes (fixturen fra 014b lager kontrakten)."""
    h = _registrer_skjema(skjema or STRENGT)
    at = f"kontroll.t{secrets.token_hex(4)}.rapport"
    da = _mk_admin("disponit_domains_admin")
    try:
        da.execute("SELECT registrer_artefakttype(%s,%s,1,%s,%s,'test')",
                   (at, modul, kh, h))
        da.commit()
    finally:
        da.close()
    return at


# --------------------------------------------------------------------------
# Lageret (portene 15–17, 26–28)
# --------------------------------------------------------------------------

@pg
def test_skjemaregistrering_rekalkulerer_hashen(migrator):
    """Port 16: oppgitt hash må matche innholdet — funksjonen regner selv."""
    kanon, h = _jcs_hash({"type": "object", "x": secrets.token_hex(3)})
    c = _mk_admin("disponit_modules_admin")
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute("SELECT registrer_artefaktskjema(%s,%s,'test')",
                      (kanon, "0" * 64))
        c.rollback()
        assert c.execute("SELECT registrer_artefaktskjema(%s,%s,'test')",
                         (kanon, h)).fetchone()[0] == h
        c.execute("SELECT registrer_artefaktskjema(%s,%s,'test')",
                  (kanon, h))                        # idempotent
        c.commit()
    finally:
        c.close()


@pg
def test_skjemalageret_er_immutabelt_for_alltid(migrator):
    """Portene 17, 26–28: UPDATE og DELETE avvises ALLTID — også for en rad
    ingen artefakttype refererer, og også for migrator (tabelleieren).
    Kontroll: fjern `artefaktskjema_immutable`-triggeren i 036, så blir
    denne rød."""
    kanon, h = _jcs_hash({"type": "object", "u": secrets.token_hex(3)})
    _registrer_skjema(json.loads(kanon))
    for sql in [
        "UPDATE artefaktskjema SET skjema='{}'::jsonb WHERE skjema_hash=%s",
        "UPDATE artefaktskjema SET skjema_hash=%s WHERE skjema_hash=%s",
        "DELETE FROM artefaktskjema WHERE skjema_hash=%s",
    ]:
        params = ((h,) if sql.count("%s") == 1
                  else ("f" * 64, h))
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(sql, params)
        migrator.rollback()


@pg
def test_artefakttype_krever_registrert_skjema(migrator):
    """Port 15 (registersiden): positiv regel, fail-closed. Kontroll: fjern
    eksistenssjekken i 036-kroppen, så blir denne rød."""
    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _plukket_oppdrag_med_binding(migrator, modul, kh)   # lager kontrakten
    da = _mk_admin("disponit_domains_admin")
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue,
                           match="finnes ikke"):
            da.execute("SELECT registrer_artefakttype(%s,%s,1,%s,%s,'test')",
                       (f"kontroll.x{secrets.token_hex(3)}.rapport", modul,
                        kh, "e" * 64))
        da.rollback()
    finally:
        da.close()


# --------------------------------------------------------------------------
# Valideringen ved OPPLASTING (portene 13, 15) og PROMOTERING (14)
# --------------------------------------------------------------------------

@pg
@dekker("artefakt_skjemabrudd", "artefaktskjema_mangler")
def test_opplasting_valideres_mot_typens_skjema(migrator, klient, token):
    """Port 13: brudd avvises FØR kryptering — ingen staged rad; gyldig
    innhold går gjennom. Port 15 (opplastingssiden): en type med hash uten
    skjemarad (grandfathered via direkte INSERT) avvises som
    konfigurasjonsfeil. Kontroll: fjern valideringsblokken i
    `_artefakt_upload`, så blir denne rød."""
    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    opp, _ = _plukket_oppdrag_med_binding(migrator, modul, kh)
    at = _streng_type(migrator, modul, kh)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))

    jti = _utsted_cap(opp, modul, kh, at)
    r = _post(klient, tok, jti, {"resultat": "ok", "smugling": 1})
    assert (r.status_code, r.json()["feil"]) == (422, "artefakt_skjemabrudd"), \
        r.text
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute("SELECT count(*) FROM artefakt WHERE"
                         " kapabilitet_jti=%s", (jti,)).fetchone()[0]
    migrator.rollback()
    assert n == 0, "et skjemabrudd etterlot en staged rad"

    # Kapabiliteten er engangs og BLE innløst av forsøket — riktig: den var
    # gyldig, innholdet var det ikke. Ny kapabilitet for det gyldige.
    jti2 = _utsted_cap(opp, modul, kh, at)
    r2 = _post(klient, tok, jti2, {"resultat": "ok"})
    assert r2.status_code == 200, r2.text

    # Grandfathered type: hash uten skjemarad → 422, driftskoden.
    at3 = f"kontroll.g{secrets.token_hex(3)}.rapport"
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "INSERT INTO artefakttype_register (artefakttype, eiermodul,"
        " kontraktversjon, kontrakt_hash, skjema_hash)"
        " VALUES (%s,%s,1,%s,%s)", (at3, modul, kh, "d" * 64))
    migrator.commit()
    jti3 = _utsted_cap(opp, modul, kh, at3)
    r3 = _post(klient, tok, jti3, {"resultat": "ok"})
    assert (r3.status_code,
            r3.json()["feil"]) == (422, "artefaktskjema_mangler"), r3.text


@pg
def test_promotering_revaliderer_og_karantenesetter_brudd(migrator, klient,
                                                          token):
    """Port 14: innhold som omgikk opplastingsvalideringen (direkte insert —
    «en fremtidig opplastingsvei glemte punkt 1») promoteres ALDRI:
    kvitteringen får 409, artefaktet karantenesettes, oppdraget avsluttes
    ikke. Kontroll: fjern revalideringsblokken i kvittering-ingesten, så
    blir denne rød."""
    from db import kryptering
    from .test_m37 import _signer_kvittering
    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    opp, _ = _plukket_oppdrag_med_binding(migrator, modul, kh)
    at = _streng_type(migrator, modul, kh)
    oc, rep, gen = _oppdrag_owner(migrator, opp)

    # Direkte staged insert med SKJEMABRYTENDE innhold (feltene matcher
    # `_artefakt`-hjelperen i domene_artefakt, men innholdet er vårt).
    from policy_validator import jcs
    innhold = {"rapport": "smuglet forbi opplastingen"}
    kanon = jcs.kanoniske_bytes(innhold)
    kts = hashlib.sha256(kanon).hexdigest()
    _sett_kontekst(migrator, TENANT)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, TENANT)
    ct, nonce = kryptering.krypter(dek, innhold, TENANT, key_id)
    aid = migrator.execute(
        "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype, modul_id,"
        " release_id, kontraktversjon, kontrakt_hash, module_epoch, tilstand,"
        " storrelse_bytes, klartekst_sha256, ciphertext, nonce, dek_ref,"
        " kapabilitet_jti)"
        " VALUES (%s,%s,%s,%s,'r1',1,%s,0,'staged',%s,%s,%s,%s,%s,%s)"
        " RETURNING artefakt_id",
        (TENANT, opp, at, modul, kh, len(kanon), kts, ct, nonce, key_id,
         "jti-" + secrets.token_hex(8))).fetchone()[0]
    migrator.commit()

    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering(
        _kvitteringskropp(opp, kjti, rep, oc, gen, str(aid), kts))
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 409, rk.text
    _sett_kontekst(migrator, TENANT)
    art_st = migrator.execute("SELECT tilstand FROM artefakt WHERE"
                              " artefakt_id=%s", (aid,)).fetchone()[0]
    opp_st = migrator.execute("SELECT status FROM oppdrag WHERE tenant=%s"
                              " AND id=%s", (TENANT, opp)).fetchone()[0]
    migrator.rollback()
    assert (art_st, opp_st) == ("karantene", "plukket"), (art_st, opp_st)


# --------------------------------------------------------------------------
# Sideeffektklassen (portene 29–30) og målautorisasjonsregisteret
# --------------------------------------------------------------------------

@pg
def test_sideeffektklassen_ekstern_lesing(migrator):
    """Port 29–30: `ekstern_lesing` godtas, ukjent verdi avvises, og en
    eksisterende kontrakt kan ikke omklassifiseres (modulkontrakt tåler
    ingen UPDATE — 014a-invarianten bærer kravet)."""
    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    migrator.execute("INSERT INTO modulhode (modul_id) VALUES (%s)", (modul,))
    migrator.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,1,%s,'p','k','ekstern_lesing','direkte')",
        (modul, kh))
    migrator.commit()
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "INSERT INTO modulkontrakt (modul_id,kontraktversjon,"
            "kontrakt_hash,payload_schema_hash,kvittering_schema_hash,"
            "sideeffektklasse,reversibilitet)"
            " VALUES (%s,2,%s,'p','k','fri_flyt','direkte')",
            (modul, "k2-" + secrets.token_hex(4)))
    migrator.rollback()
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE modulkontrakt SET"
                         " sideeffektklasse='krever_outbox'"
                         " WHERE modul_id=%s", (modul,))
    migrator.rollback()


@pg
def test_malautorisasjonsvilkar_er_lukket_og_immutabelt(migrator):
    """§3/§6: seedet vilkår finnes; nye går via herdet funksjon (idempotent,
    aldri omregistrering til annet domene); ukjent maldomene avvises av
    CHECK. Tom liste er default — bare rader teller."""
    rad = migrator.execute(
        "SELECT maldomene FROM malautorisasjonsvilkar"
        " WHERE vilkar_type='domenekontroll_verifisert'").fetchone()
    migrator.rollback()
    assert rad == ("web_hostname",)
    c = _mk_admin("disponit_modules_admin")
    try:
        vt = "vilkar_" + secrets.token_hex(3)
        c.execute("SELECT registrer_malautorisasjonsvilkar(%s,"
                  "'web_hostname','test')", (vt,))
        c.execute("SELECT registrer_malautorisasjonsvilkar(%s,"
                  "'web_hostname','test')", (vt,))          # idempotent
        c.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("SELECT registrer_malautorisasjonsvilkar(%s,"
                      "'dns_zone','test')", ("v2_" + secrets.token_hex(3),))
        c.rollback()
    finally:
        c.close()


@pg
def test_malautorisasjonsvilkar_serialiserer_samtidig_registrering(migrator):
    """Codex P2: check-then-insert uten lås. To samtidige registreringer av
    samme NYE vilkår så begge «finnes ikke» og gikk videre til INSERT; én
    vant, den andre fikk PK-brudd — selv om innholdet var identisk og
    funksjonen LOVER en idempotent no-op i nettopp det tilfellet.

    Kontroll: fjern pg_advisory_xact_lock i migrasjonen, så blir den andre
    forbindelsen liggende på unikhetsindeksen i stedet, og feiler med
    UniqueViolation i det den første committer."""
    vt = "vilkar_" + secrets.token_hex(4)
    a, b = (_mk_admin("disponit_modules_admin"),
            _mk_admin("disponit_modules_admin"))
    feil, startet = [], threading.Event()

    def registrer_b():
        startet.set()
        try:
            b.execute("SELECT registrer_malautorisasjonsvilkar(%s,"
                      "'web_hostname','test')", (vt,))
            b.commit()
        except Exception as e:                       # noqa: BLE001
            feil.append(e)

    try:
        a.execute("SELECT registrer_malautorisasjonsvilkar(%s,"
                  "'web_hostname','test')", (vt,))   # holder låsen, uåpnet
        t = threading.Thread(target=registrer_b, daemon=True)
        t.start()
        startet.wait(5)
        time.sleep(0.5)
        assert t.is_alive(), "den andre forbindelsen ble ikke serialisert"
        a.commit()
        t.join(15)
        assert not t.is_alive(), "den andre forbindelsen ble aldri ferdig"
        assert not feil, feil                        # idempotent, ikke PK-brudd
        assert migrator.execute(
            "SELECT count(*) FROM malautorisasjonsvilkar WHERE"
            " vilkar_type=%s", (vt,)).fetchone() == (1,)
        migrator.rollback()
    finally:
        a.close()
        b.close()


# --------------------------------------------------------------------------
# Aktiveringsporten (§6, portene 31, 34–37)
# --------------------------------------------------------------------------

def _ekstern_lesing_modul(migrator_):
    modul = "m-" + secrets.token_hex(4)
    migrator_.execute("INSERT INTO modulhode (modul_id) VALUES (%s)", (modul,))
    migrator_.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,1,%s,'p','k','ekstern_lesing','direkte')",
        (modul, "k-" + secrets.token_hex(8)))
    migrator_.commit()
    return modul


def _handling(modul, *, frekvens=True, vilkaar=("domenekontroll_verifisert",),
              hid="kontroll.wcag.nettsted"):
    h = {"id": hid, "modul": modul, "modus": "auto",
         "ved_brudd": "unntakskø",
         "vilkaar": [{"navn": v, "verifikator": "v1"} for v in vilkaar],
         "reversering": {"type": "direkte"}}
    if frekvens:
        h["grenser"] = {"frekvens": {"maks": 4, "periode_antall": 1,
                                     "periode_enhet": "dager"}}
    return h


@pg
def test_aktiveringsporten_for_ekstern_lesing(migrator):
    """Portene 31, 34–37 på funksjonsnivå (kallstedene prøves i
    integrasjonstesten under). Kontroll: fjern frekvens- eller
    vilkårsgrenen i `_krev_ekstern_lesing_port`, så blir hver sin gren rød."""
    from api import policyadmin
    from db.pg import koble
    modul = _ekstern_lesing_modul(migrator)
    c = _mk_admin("disponit_modules_admin")
    try:
        c.execute("SELECT registrer_malautorisasjonsvilkar("
                  "'gyldig_men_ikke_mal','web_hostname','test')")
        c.commit()
    finally:
        c.close()
    rt = koble(DSN)
    try:
        def port(h):
            policyadmin._krev_ekstern_lesing_port(rt, {"handlinger": [h]})

        # 31: uten frekvensgrense → avvist under låsen.
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            port(_handling(modul, frekvens=False))
        assert e.value.kode == "ekstern_lesing_uten_frekvens"
        # 34: gyldig, men IKKE målautoriserende vilkår → avvist. Vilkåret
        # `forfall_passert_dager` finnes i policyer — det har bare ingen rad.
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            port(_handling(modul, vilkaar=("forfall_passert_dager",)))
        assert e.value.kode == "malautorisasjon_mangler"
        # 36: navnelikhet teller aldri — bare rader.
        with pytest.raises(policyadmin.Aktiveringsfeil):
            port(_handling(modul, vilkaar=("domenekontroll_verifisert2",)))
        # 37: rad finnes, men for FEIL måldomene? (Alle rader er
        # web_hostname i v1 — probes med et vilkår registrert riktig, mot en
        # handling hvis TYPE mangler målautorisasjonsbegrep.)
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            port(_handling(modul, hid="kontroll.wcag_ukjent.ting"))
        assert "oppdragstype" in (e.value.detalj or "")
        # 35: positiv motsats — domenekontroll_verifisert + frekvens godtas.
        port(_handling(modul))
        # ... og en handling uten målautorisasjonsbærende type, mot en modul
        # UTEN ekstern_lesing, er urørt. (Handlings-id-en må velges utenfor
        # `kontroll.wcag.`: den prefiksen ER den kodefestede WCAG-typen, og
        # den gates nå på sin egen deklarasjon — se testen under.)
        policyadmin._krev_ekstern_lesing_port(
            rt, {"handlinger": [_handling("m-finnes-ikke", frekvens=False,
                                          vilkaar=(), hid="purring.sen")]})
        rt.rollback()
    finally:
        rt.close()


@pg
def test_porten_leser_kontrakten_som_eier_typen(migrator, monkeypatch):
    """Codex P2: porten prøvde modulbredt (`LIMIT 1` på modulkontrakt).
    Kontraktrader er immutable og blir stående, så en modul som EN GANG
    hadde en ekstern_lesing-kontrakt fikk hver eneste handling klassifisert
    som ekstern lesing — også de som nå tilhører en nyere sideeffektfri
    kontrakt. Slike moduler kunne dermed ikke lenger aktivere ellers
    gyldige policyer. Nå leses klassen av kontrakten som EIER handlingens
    registrerte oppdragstype. Kontroll: bytt tilbake til den modulbrede
    prøven, så blir denne rød."""
    import oppdragskontrakt as ok
    from api import policyadmin
    from db.pg import koble

    modul = _ekstern_lesing_modul(migrator)          # gammel, immutabel rad
    # ... samme modul får en NYERE, sideeffektfri kontrakt, og handlingens
    # oppdragstype registreres under NETTOPP den.
    kh2 = "k-" + secrets.token_hex(8)
    migrator.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,2,%s,'p','k','sideeffektfri','direkte')",
        (modul, kh2))
    u = secrets.token_hex(4)
    typenavn = f"stille.w{u}.jobb"
    migrator.execute(
        "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
        "kontraktversjon,kontrakt_hash) VALUES (%s,%s,2,%s)",
        (typenavn, modul, kh2))
    migrator.commit()
    monkeypatch.setitem(ok.OPPDRAGSTYPER, typenavn, ok.Oppdragstype(
        navn=typenavn, handlingsprefikser=(f"stille.w{u}.",),
        felter=frozenset({"ressurs_id"}), paakrevde=frozenset(),
        eiermodul=modul))

    rt = koble(DSN)
    try:
        # Uten frekvens og uten målautoriserende vilkår — og likevel grønn,
        # for handlingen er ikke ekstern lesing.
        policyadmin._krev_ekstern_lesing_port(rt, {"handlinger": [
            _handling(modul, frekvens=False, vilkaar=(),
                      hid=f"stille.w{u}.jobb")]})
        # Motsatsen: en handling uten registrert type treffer fortsatt den
        # konservative modulbrede prøven.
        with pytest.raises(policyadmin.Aktiveringsfeil):
            policyadmin._krev_ekstern_lesing_port(rt, {"handlinger": [
                _handling(modul, frekvens=False, vilkaar=())]})
        rt.rollback()
    finally:
        rt.close()


def test_malautorisasjonen_bindes_til_verten_som_kontrolleres():
    """Codex P1: aktiveringsporten beviste bare at handlingen BÆRER et
    vilkår som er registrert for `web_hostname` — ikke at autorisasjonen
    dekker verten i `mal_url`. Kjøretidsbindingen sammenlignet
    `ressurs_id`, men verken den eller porten så på `mal_url`.

    En hendelse kunne derfor gjenbruke en ekte, gyldig
    `domenekontroll_verifisert`-attestasjon med sin egen `ressurs_id` og be
    om kontroll av et helt annet vertsnavn: trafikk ut mot et mål ingen har
    autorisert, med et bevis som ser gyldig ut hele veien.

    Bindingen legges på `ressurs_id` fordi det feltet allerede ligger i
    `BINDINGSFELT` — altså inne i de SIGNERTE bytene — og
    `kontroller_binding` krever allerede at attestasjonen bærer samme verdi
    som hendelsen. Kreves det at hendelsens `ressurs_id` ER det
    normaliserte vertsnavnet, arver attestasjonen bindingen gratis.

    Kontroll: la `malbindingsbrudd` returnere None for `web_hostname`, så
    slipper hendelsen med feil vert gjennom og denne blir rød.
    """
    import oppdragskontrakt as ok

    def brudd(**ev):
        return ok.malbindingsbrudd(ev.get("handling"), ev)

    # Riktig vert: ingen brudd. `ressurs_id` ER vertsnavnet.
    assert brudd(handling="kontroll.wcag.nettsted",
                 mal_url="https://kunde.example/a/b",
                 ressurs_id="kunde.example") is None
    # Normalformen: store bokstaver, port, credentials og rotprikk er
    # samme vert — ellers ville hver av dem vært et gratis omgåelsestegn.
    for url in ("https://KUNDE.Example/", "https://kunde.example:443/",
                "https://kunde.example./", "https://u:p@kunde.example/"):
        assert brudd(handling="kontroll.wcag.nettsted", mal_url=url,
                     ressurs_id="kunde.example") is None, url
    # Selve hullet: gyldig autorisasjon for én vert, kontroll av en annen.
    k, d = brudd(handling="kontroll.wcag.nettsted",
                 mal_url="https://offer.example/",
                 ressurs_id="kunde.example")
    assert k == "malautorisasjon_feil_mal"
    assert d["forventet"] == "offer.example"
    # Ugyldig eller manglende mål er fail-closed, ikke en åpen port.
    for url in (None, "", "http://kunde.example/", "https://",
                "ikke en url", "https://kunde.example:99999/"):
        assert brudd(handling="kontroll.wcag.nettsted", mal_url=url,
                     ressurs_id="kunde.example")[0] == \
            "malautorisasjon_mal_ugyldig", url
    # Et måldomene plattformen ikke vet hvordan den binder skal stoppe,
    # ikke passere stille.
    ukjent = ok.Oppdragstype(
        navn="kontroll.ukjentdomene.ting",
        handlingsprefikser=("kontroll.ukjentdomene.",),
        felter=frozenset({"mal_url"}), paakrevde=frozenset(),
        krever_malautorisasjon=True, malautorisasjonsdomene="ip_range")
    ok.OPPDRAGSTYPER["kontroll.ukjentdomene.ting"] = ukjent
    try:
        assert brudd(handling="kontroll.ukjentdomene.ting",
                     mal_url="https://k.example/",
                     ressurs_id="k.example")[0] == \
            "malautorisasjon_domene_ukjent"
    finally:
        del ok.OPPDRAGSTYPER["kontroll.ukjentdomene.ting"]
    # Typer UTEN målautorisasjonsdomene er urørt — porten gjelder bare der
    # typen selv sier at målet må være autorisert.
    assert brudd(handling="purring.sen", ressurs_id="fak-1") is None
    assert brudd(handling="helt.ukjent", ressurs_id="x") is None
    assert brudd(handling=None) is None

    # ... og koden er klassifisert: uten rad i tabellene ville et brudd
    # blitt STOPP uten M-37-sak, altså et sikkerhetsavvik ingen ser.
    from api.feil import DRIFTSKODER, SIKKERHETSKODER, sakstype_for
    assert "malautorisasjon_feil_mal" in SIKKERHETSKODER
    assert "malautorisasjon_mal_ugyldig" in SIKKERHETSKODER
    assert "malautorisasjon_domene_ukjent" in DRIFTSKODER
    assert sakstype_for("STOPP", "malautorisasjon_feil_mal", None) == \
        ("sikkerhet", "hoy")


@pg
def test_malbindingsporten_staar_i_beslutningsveien(migrator):
    """Porten hører hjemme i `sikker_beslutning_pg`, ikke i `api.kjerne`:
    det er den ENE veien alle evalueringer går (kjernen,
    unntaksbehandlingen, og det som måtte komme). En port på
    forespørselsveien alene ville vært en port med en dør ved siden av.

    Kontroll: fjern målbindingskallet i `sikker_beslutning_pg`, så blir
    denne rød — hendelsen med feil vert blir evaluert i stedet for stoppet.
    """
    import yaml
    from db.pg import koble, sikker_beslutning_pg
    from policy_validator.engine import STOPP, EvaluationContext
    from .conftest import POLICIES
    policy = yaml.safe_load(
        (POLICIES / "bransjemal-tjenestebedrift.yaml").read_text(
            encoding="utf-8"))
    ctx = EvaluationContext("t-pg", "agent", True, "api_token")
    ev = {"handling": "kontroll.wcag.nettsted",
          "mal_url": "https://offer.example/",
          "ressurs_id": "kunde.example"}
    c = koble(DSN)
    try:
        d = sikker_beslutning_pg(policy, ctx, ev, c, naa=None, nokler=None)
        assert d.beslutning == STOPP
        assert d.begrunnelse[-1].kode == "malautorisasjon_feil_mal", \
            d.begrunnelse[-1].kode
        c.rollback()
    finally:
        c.close()


@pg
def test_uregistrert_kodefestet_type_feiler_lukket(migrator):
    """Codex P1: den kodefestede typen fantes, DB-registreringen manglet.

    `_typens_sideeffektklasse` ga da None, og porten falt tilbake på en
    modulbred prøve mot `handlinger[].modul`. Men det feltet er POLICYENS
    modulidentifikator (`M-23`), mens kontrakten er registrert på
    `m_wcag_audit` — to navnerom. Oppslaget fant ingenting, handlingen ble
    lest som ikke-ekstern, og BÅDE frekvens- og målautorisasjonsporten ble
    hoppet over for nøyaktig den handlingstypen de er bygget for.

    Tilstanden er nåbar: `registrer-m-wcag-audit.py` kjøres manuelt, og
    deploy-porten sjekker bare DB-rader som mangler i koden, ikke omvendt.

    Nå er koden autoriteten når registeret ikke har tatt igjen:
    `krever_malautorisasjon` i den kodefestede typen betyr at porten
    gjelder, uansett hva `modul` sier.

    Kontroll: la den uregistrerte kodefestede typen falle tilbake på den
    modulbrede prøven igjen, så blir denne rød — handlingen slipper
    gjennom uten frekvens og uten målautorisasjon.
    """
    from api import policyadmin
    from db.pg import koble
    rt = koble(DSN)
    try:
        # `M-23` finnes ikke i modulkontrakt (og skal ikke gjøre det —
        # det er policynavnerommet). Typen kontroll.wcag.nettsted er ikke
        # registrert i denne databasen.
        assert policyadmin._typens_sideeffektklasse(
            rt, "kontroll.wcag.nettsted") is None
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            policyadmin._krev_ekstern_lesing_port(rt, {"handlinger": [
                _handling("M-23", frekvens=False, vilkaar=())]})
        assert e.value.kode == "ekstern_lesing_uten_frekvens", e.value.kode
        # Med frekvens, men uten målautoriserende vilkår: andre porten.
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            policyadmin._krev_ekstern_lesing_port(rt, {"handlinger": [
                _handling("M-23", vilkaar=("forfall_passert_dager",))]})
        assert e.value.kode == "malautorisasjon_mangler", e.value.kode
        # En type UTEN kodefestet målautorisasjon er urørt av dette —
        # der gjelder fortsatt den konservative modulbrede prøven.
        policyadmin._krev_ekstern_lesing_port(rt, {"handlinger": [
            _handling("M-23", frekvens=False, vilkaar=(),
                      hid="purring.sen")]})
        rt.rollback()
    finally:
        rt.close()


@pg
def test_aktiveringsporten_haandheves_ved_rundeaapning(migrator):
    """Integrasjonen: kallstedet i `opprett_aktiveringsrunde` (samme mønster
    som `_krev_innforingskrav`). Kontroll: fjern
    `_krev_ekstern_lesing_port`-kallet der, så blir denne rød."""
    from .test_pr013_policyadmin_flyt import TEN, _apne, _medlem, _utkast
    from api import policyadmin
    modul = _ekstern_lesing_modul(migrator)
    forf = _medlem("wcagforf-" + secrets.token_hex(2), ["policyforvalter"])
    pid = "pol-" + secrets.token_hex(3)
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, forf, {"roller": [{"id": "r1"}],
                             "handlinger": [_handling(modul, vilkaar=())]})
    from db.pg import koble
    rt = koble(DSN)
    try:
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _apne(rt, uid, forf)
        assert e.value.kode == "malautorisasjon_mangler", e.value.kode
        rt.rollback()
    finally:
        rt.close()
    # ... og med vilkåret på plass åpner runden.
    uid2 = "utk-" + secrets.token_hex(3)
    _utkast(uid2, pid, forf, {"roller": [{"id": "r1"}],
                              "handlinger": [_handling(modul)]})
    rt = koble(DSN)
    try:
        r = _apne(rt, uid2, forf)
        assert r["diff_hash"]
    finally:
        rt.close()


# --------------------------------------------------------------------------
# Deploy-portene (§5, portene 6 og 32)
# --------------------------------------------------------------------------

@pg
def test_deployportene_register_mot_kodefestet_type(migrator, monkeypatch):
    """Port 6: registerrad uten kodefestet type → rød. Port 32: klasse og
    autorisasjonskrav må stemme BEGGE veier — ekstern_lesing-kontrakt med
    type uten krever_malautorisasjon, OG type med krever_malautorisasjon
    under en kontrakt som ikke er ekstern_lesing. Grønn tilstand er den
    positive motsatsen i hvert tilfelle. Kontroll: fjern LEFT JOIN-en
    (port 32-grenene) i `kontroller()`, så blir andre halvdel grønn på
    feil grunnlag."""
    import importlib.util
    from pathlib import Path
    sti = (Path(__file__).resolve().parents[3]
           / "deploy/staging/deployport-modultyper.py")
    spec = importlib.util.spec_from_file_location("deployport_test", sti)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import oppdragskontrakt as ok

    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    migrator.execute("INSERT INTO modulhode (modul_id) VALUES (%s)", (modul,))
    migrator.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,1,%s,'p','k','ekstern_lesing','direkte')",
        (modul, kh))
    ukjent = f"deployport{secrets.token_hex(3)}"
    migrator.execute(
        "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
        "kontraktversjon,kontrakt_hash) VALUES (%s,%s,1,%s)",
        (ukjent, modul, kh))
    migrator.commit()

    # Port 6: raden er ukjent for koden → rød med typenavnet i meldingen.
    feil = mod.kontroller(migrator)
    migrator.rollback()
    assert any(ukjent in f for f in feil), feil

    # Port 32: kodefest typen, men UTEN målautorisasjonsflagget → fortsatt
    # rød, nå på autorisasjonsbegrepet.
    t = ok.Oppdragstype(navn=ukjent, handlingsprefikser=(f"{ukjent}.",),
                        felter=frozenset({"mal_url"}), paakrevde=frozenset(),
                        eiermodul=modul)
    monkeypatch.setitem(ok.OPPDRAGSTYPER, ukjent, t)
    feil = mod.kontroller(migrator)
    migrator.rollback()
    assert any("krever_malautorisasjon" in f and ukjent in f for f in feil), \
        feil

    # Grønn motsats: flagg + domene på plass → ingen feil for VÅR rad.
    t2 = ok.Oppdragstype(navn=ukjent, handlingsprefikser=(f"{ukjent}.",),
                         felter=frozenset({"mal_url"}), paakrevde=frozenset(),
                         eiermodul=modul, krever_malautorisasjon=True,
                         malautorisasjonsdomene="web_hostname")
    monkeypatch.setitem(ok.OPPDRAGSTYPER, ukjent, t2)
    feil = mod.kontroller(migrator)
    migrator.rollback()
    assert not any(ukjent in f for f in feil), feil

    # Codex P1: samme rad, men koden sier at typen eies av en ANNEN modul.
    # Registerraden er autoriteten claim-veien utleder prefiksene fra, så
    # avviket ville gitt den registrerte modulen rekkevidde over payloads
    # ment for den kodefestede eieren. Rød.
    t3 = ok.Oppdragstype(navn=ukjent, handlingsprefikser=(f"{ukjent}.",),
                         felter=frozenset({"mal_url"}), paakrevde=frozenset(),
                         eiermodul="m_en_helt_annen",
                         krever_malautorisasjon=True,
                         malautorisasjonsdomene="web_hostname")
    monkeypatch.setitem(ok.OPPDRAGSTYPER, ukjent, t3)
    feil = mod.kontroller(migrator)
    migrator.rollback()
    assert any("m_en_helt_annen" in f and ukjent in f for f in feil), feil

    # ... men en type UTEN kodefestet eier (legacy) skal ikke fanges av
    # eierporten — et krav kan ikke håndheves mot en taus kilde.
    t4 = ok.Oppdragstype(navn=ukjent, handlingsprefikser=(f"{ukjent}.",),
                         felter=frozenset({"mal_url"}), paakrevde=frozenset(),
                         krever_malautorisasjon=True,
                         malautorisasjonsdomene="web_hostname")
    monkeypatch.setitem(ok.OPPDRAGSTYPER, ukjent, t4)
    feil = mod.kontroller(migrator)
    migrator.rollback()
    assert not any(ukjent in f for f in feil), feil

    # Codex P1, DEN ANDRE RETNINGEN: en type som KREVER målautorisasjon,
    # registrert under en sideeffektfri kontrakt. Porten så bare det
    # motsatte avviket, mens `_krev_ekstern_lesing_port` leser typens
    # klasse, ser noe annet enn ekstern_lesing og hopper over HELE porten
    # — både frekvens og målautorisasjon. Rød.
    # Kontroll: fjern elif-grenen i `kontroller()`, så blir denne rød.
    kh2 = "k-" + secrets.token_hex(8)
    fri = f"deployport{secrets.token_hex(3)}"
    migrator.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,2,%s,'p','k','sideeffektfri','direkte')",
        (modul, kh2))
    migrator.execute(
        "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
        "kontraktversjon,kontrakt_hash) VALUES (%s,%s,2,%s)",
        (fri, modul, kh2))
    migrator.commit()
    monkeypatch.setitem(ok.OPPDRAGSTYPER, fri, ok.Oppdragstype(
        navn=fri, handlingsprefikser=(f"{fri}.",),
        felter=frozenset({"mal_url"}), paakrevde=frozenset(),
        eiermodul=modul, krever_malautorisasjon=True,
        malautorisasjonsdomene="web_hostname"))
    feil = mod.kontroller(migrator)
    migrator.rollback()
    assert any("målautorisasjon" in f and fri in f for f in feil), feil

    # Grønn motsats for samme rad: uten autorisasjonskravet er en
    # sideeffektfri registrering helt i orden.
    monkeypatch.setitem(ok.OPPDRAGSTYPER, fri, ok.Oppdragstype(
        navn=fri, handlingsprefikser=(f"{fri}.",),
        felter=frozenset({"mal_url"}), paakrevde=frozenset(),
        eiermodul=modul))
    feil = mod.kontroller(migrator)
    migrator.rollback()
    assert not any(fri in f for f in feil), feil


def test_oppdraget_bindes_til_den_deklarerte_eiermodulen():
    """Codex P1: `_eiermodul_for` skrev `eiermodul:<typenavn>` for ALLE
    typer, også den nye. Oppdraget fikk da
    `eiermodul:kontroll.wcag.nettsted`, mens kontrakt, deployment og token
    står på `m_wcag_audit` — og claim krever `oppdrag.eiermodul =
    auth.modul_id`. Controlleren kunne aldri claimet sitt eget oppdrag; det
    ville ligget til fristen uten at noen så det.
    Kontroll: bytt tilbake til `f"eiermodul:{t.navn}"`, så blir denne
    rød."""
    import oppdragskontrakt as ok
    from m37.arbeider import _eiermodul_for

    assert _eiermodul_for("kontroll.wcag.nettsted.kjor") == "m_wcag_audit"
    assert (_eiermodul_for("kontroll.wcag.nettsted.kjor")
            == ok.OPPDRAGSTYPER["kontroll.wcag.nettsted"].eiermodul)
    # De eierløse legacy-typene beholder det SYNTETISKE navnet — for dem
    # finnes ingen modulrad, og eksisterende rader og tokener peker hit.
    assert _eiermodul_for("purring.send") == "eiermodul:reinnsending"
    assert _eiermodul_for("verifiser.belop") == "eiermodul:verifikasjon"
    # Ukjent handling er fortsatt fail-closed: en modul-id ingen har.
    assert _eiermodul_for("noe.helt.annet") == "eiermodul:ukjent"


# --------------------------------------------------------------------------
# Rapportbygging og sanitering (portene 8–12) — modulen selv.
# --------------------------------------------------------------------------

def _kontekst():
    return {"axe_versjon": "4.10.0", "chromium_versjon": "127.0",
            "container_image_digest": "sha256:" + "a" * 64,
            "viewport": "1280x800", "locale": "nb-NO",
            "timezone": "Europe/Oslo"}


def _motorresultat(**over):
    from modules.wcag_audit.motor import Motorresultat
    basis = dict(
        regelsett_versjon="axe-4.10", varighet_ms=1234,
        sider=({"url": "https://kunde.example/side?sporing=1#topp",
                "status": "ok"},),
        funn=({"regel_id": "color-contrast", "alvorlighet": "alvorlig",
               "antall": 3, "eksempler": ["#a", "x" * 500]},),
        blokkert=({"vert": "fonts.example", "antall": 2, "art": "font"},),
        avkortet=(False, None, None))
    basis.update(over)
    return Motorresultat(**basis)


def test_rapporten_saneres_og_validerer():
    """Portene 11–12 + skjemarunden: URL uten query/fragment, selektor
    kappet til 200 tegn, maks 10 eksempler, miljø fra SERVERKONTEKSTEN —
    og resultatet validerer mot det innholdsadresserte skjemaet."""
    import jsonschema
    from modules.wcag_audit import rapportskjema
    from modules.wcag_audit.rapport import bygg
    r = bygg(_motorresultat(),
             payload={"kravsett": "wcag21_aa", "mal_url": "https://k.no/",
                      "omfang": "enkeltside"},
             kontekst=_kontekst())
    jsonschema.Draft202012Validator(rapportskjema.SKJEMA).validate(r)
    assert r["sider_kontrollert"][0]["url"] == "https://kunde.example/side"
    assert len(r["funn"][0]["eksempler"][1]) == 200
    assert r["miljo"]["container_image_digest"].startswith("sha256:")
    assert r["manuelle_kriterier_vurdert"] is False
    assert r["dekningsbegrensninger"][0] == {"vert": "fonts.example",
                                             "antall": 2, "art": "font"}


def test_rapporten_kutter_aerlig_over_500_funn():
    """Port 11: over 500 funn kappes — og `avkortet` SIER det (aldri mer
    fullstendighet enn innholdet bærer). Kontroll: fjern
    truffet-oppdateringen i `bygg`, så blir denne rød."""
    from modules.wcag_audit.rapport import bygg
    mange = tuple({"regel_id": f"r{i}", "alvorlighet": "lav", "antall": 1,
                   "eksempler": []} for i in range(600))
    r = bygg(_motorresultat(funn=mange),
             payload={"kravsett": "wcag21_aa"}, kontekst=_kontekst())
    assert len(r["funn"]) == 500
    assert r["avkortet"]["truffet"] is True and r["avkortet"]["verdi"] == 600
    # ... men SAMMENDRAGET teller alt motoren fant — kappingen gjelder
    # eksempellisten, ikke sannheten om omfanget.
    assert r["sammendrag"]["lav"] == 600


def test_kappet_eksempelliste_sier_fra_i_avkortet():
    """Codex P2: eksempellisten kappes på 10 per funn — og DA er rapporten
    avkortet. Uten dette kunne den promoterte evidensen påstå
    `truffet: false` samtidig som den utelot kjente eksempler.
    Kontroll: fjern `maks_eksempler_sett`-blokka i `bygg`, så blir denne
    rød."""
    import jsonschema
    from modules.wcag_audit import rapportskjema
    from modules.wcag_audit.rapport import MAKS_EKSEMPLER, bygg

    ett = ({"regel_id": "r1", "alvorlighet": "alvorlig", "antall": 25,
            "eksempler": [f"#node-{i}" for i in range(25)]},)
    r = bygg(_motorresultat(funn=ett),
             payload={"kravsett": "wcag21_aa"}, kontekst=_kontekst())
    jsonschema.Draft202012Validator(rapportskjema.SKJEMA).validate(r)
    assert len(r["funn"][0]["eksempler"]) == MAKS_EKSEMPLER
    assert r["avkortet"]["truffet"] is True
    assert r["avkortet"]["tak"] == MAKS_EKSEMPLER
    assert r["avkortet"]["verdi"] == 25
    # ... og NØYAKTIG på taket er ingen kapping: feltet skal ikke rope ulv.
    paa_taket = ({"regel_id": "r1", "alvorlighet": "lav", "antall": 1,
                  "eksempler": [f"#n{i}" for i in range(MAKS_EKSEMPLER)]},)
    r2 = bygg(_motorresultat(funn=paa_taket),
              payload={"kravsett": "wcag21_aa"}, kontekst=_kontekst())
    assert r2["avkortet"]["truffet"] is False


def test_dekningsbegrensninger_slaas_sammen_og_kappet_sier_fra():
    """Codex P2: lista ble kappet på 200 UTEN at `avkortet` endret seg —
    den promoterte evidensen kunne påstå at ingenting var utelatt samtidig
    som den utelot kjente dekningsbegrensninger (014b B3). Nå slås like
    (vert, art) sammen først, og treffer taket likevel, sier `avkortet`
    fra. Kontroll: fjern taksjekken, så blir denne rød."""
    import jsonschema
    from modules.wcag_audit import rapportskjema
    from modules.wcag_audit.rapport import MAKS_BEGRENSNINGER, bygg

    # Samme vert to ganger → én post med summert antall, ikke to.
    r = bygg(_motorresultat(blokkert=(
        {"vert": "fonts.example", "antall": 2, "art": "font"},
        {"vert": "fonts.example/x?q=1", "antall": 3, "art": "font"})),
        payload={"kravsett": "wcag21_aa"}, kontekst=_kontekst())
    assert r["dekningsbegrensninger"] == [{"vert": "fonts.example",
                                           "antall": 5, "art": "font"}]
    assert r["avkortet"]["truffet"] is False

    # Flere unike verter enn taket → kappet, og `avkortet` sier det.
    mange = tuple({"vert": f"v{i}.example", "antall": 1, "art": "font"}
                  for i in range(MAKS_BEGRENSNINGER + 25))
    r2 = bygg(_motorresultat(blokkert=mange),
              payload={"kravsett": "wcag21_aa"}, kontekst=_kontekst())
    jsonschema.Draft202012Validator(rapportskjema.SKJEMA).validate(r2)
    assert len(r2["dekningsbegrensninger"]) == MAKS_BEGRENSNINGER
    assert r2["avkortet"]["truffet"] is True
    assert r2["avkortet"]["verdi"] == MAKS_BEGRENSNINGER + 25

    # Størst først: treffer taket, er det de STØRSTE som kommer med.
    tunge = ({"vert": "tung.example", "antall": 99, "art": "skript"},) + mange
    r3 = bygg(_motorresultat(blokkert=tunge),
              payload={"kravsett": "wcag21_aa"}, kontekst=_kontekst())
    assert r3["dekningsbegrensninger"][0]["vert"] == "tung.example"


def test_motorutdata_er_ubetrodd():
    """Port 12/§2: ikke-https-URL og uleselige poster er Motorfeil — aldri
    en rapport. Digester fra motoren finnes ikke som begrep: miljøblokka
    tar KUN serverkontekstens nøkler (port 10)."""
    from modules.wcag_audit.motor import Motorfeil
    from modules.wcag_audit.rapport import bygg
    with pytest.raises(Motorfeil):
        bygg(_motorresultat(sider=({"url": "http://klartekst.example/",
                                    "status": "ok"},)),
             payload={"kravsett": "wcag21_aa"}, kontekst=_kontekst())
    with pytest.raises(Motorfeil):
        bygg(_motorresultat(sider=()), payload={"kravsett": "wcag21_aa"},
             kontekst=_kontekst())
    with pytest.raises(KeyError):
        # En kontekst uten digest er en konfigurasjonsfeil hos OSS —
        # den skal smelle, ikke fylles fra motorens påstander.
        bygg(_motorresultat(), payload={"kravsett": "wcag21_aa"},
             kontekst={k: v for k, v in _kontekst().items()
                       if k != "container_image_digest"})
    # Codex P1: et uleselig ANTALL er også ubetrodd inndata. Konverteringen
    # ga ValueError, som controlleren ikke fanger — da hadde unntaket
    # sluppet ut av kjøreløkka og latt oppdraget stå claimet i stedet for
    # å bli kvittert som feilet. Begge tellingene, funn og blokkert.
    for over in ({"funn": ({"regel_id": "r", "alvorlighet": "lav",
                            "antall": "ukjent", "eksempler": []},)},
                 {"blokkert": ({"vert": "f.example", "antall": {"a": 1},
                                "art": "font"},)}):
        with pytest.raises(Motorfeil):
            bygg(_motorresultat(**over), payload={"kravsett": "wcag21_aa"},
                 kontekst=_kontekst())
    # Codex P1: og en uleselig PORT i URL-en. `urlsplit` godtar strengen —
    # det er `d.port` som kaster, og stod det uttrykket utenfor vakten,
    # var utfallet den samme nakne ValueError ut av kjøreløkka.
    # Kontroll: flytt `port` ut av try-blokka i `_ren_url`, så blir denne
    # rød på ValueError i stedet for å passere på Motorfeil.
    for url in ("https://example.com:not-a-port/", "https://example.com:99999/",
                "https://example.com:-1/"):
        with pytest.raises(Motorfeil):
            bygg(_motorresultat(sider=({"url": url, "status": "ok"},)),
                 payload={"kravsett": "wcag21_aa"}, kontekst=_kontekst())
    # ... men en LOVLIG eksplisitt port skal fortsatt bæres videre.
    r = bygg(_motorresultat(sider=({"url": "https://example.com:8443/a?q=1#f",
                                    "status": "ok"},)),
             payload={"kravsett": "wcag21_aa"}, kontekst=_kontekst())
    assert r["sider_kontrollert"][0]["url"] == "https://example.com:8443/a"


# --------------------------------------------------------------------------
# Controlleren ende-til-ende med FakeMotor (port 23 + 25s CI-halvdel:
# kjeden bevarer tellingene; motor-ekte fasit måles på staging).
# --------------------------------------------------------------------------

class FakeMotor:
    def __init__(self, resultat=None, feil=None):
        self.resultat, self.feil = resultat, feil
        self.payloads = []

    def kjor(self, payload):
        from modules.wcag_audit.motor import Motorfeil
        self.payloads.append(payload)
        if self.feil:
            raise Motorfeil(self.feil)
        return self.resultat


def _wcag_kjede(migrator_, monkeypatch):
    """Modulkjede + oppdrag for et ALIAS av wcag-typen (unike navn per
    kjøring — den delte testbasen tåler ikke det globale navnet; den EKTE
    registreringen gjøres av deploy-skriptet og prøves på staging)."""
    import oppdragskontrakt as ok
    from modules.wcag_audit import rapportskjema
    u = secrets.token_hex(4)
    typenavn = f"kontroll.w{u}.nettsted"
    at = f"kontroll.w{u}.rapport"
    ekte = ok.OPPDRAGSTYPER["kontroll.wcag.nettsted"]
    monkeypatch.setitem(ok.OPPDRAGSTYPER, typenavn, ok.Oppdragstype(
        navn=typenavn, handlingsprefikser=(f"kontroll.w{u}.",),
        felter=ekte.felter, paakrevde=ekte.paakrevde,
        eiermodul=f"m-{u}", krever_malautorisasjon=True,
        malautorisasjonsdomene="web_hostname"))

    modul, rel = f"m-{u}", f"r-{u}"
    kh = "k-" + secrets.token_hex(8)
    migrator_.execute("INSERT INTO modulhode (modul_id,status) VALUES"
                      " (%s,'aktiv')", (modul,))
    migrator_.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,1,%s,'p','k','ekstern_lesing','direkte')",
        (modul, kh))
    migrator_.execute(
        "INSERT INTO modulrelease (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,manifest_hash,artifact_digest)"
        " VALUES (%s,%s,1,%s,'mh','ad')", (modul, rel, kh))
    migrator_.execute(
        "INSERT INTO moduldeployment (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,miljo,livslop) VALUES (%s,%s,1,%s,'staging',"
        "'claiming')", (modul, rel, kh))
    migrator_.execute(
        "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
        "kontraktversjon,kontrakt_hash) VALUES (%s,%s,1,%s)",
        (typenavn, modul, kh))
    migrator_.execute(
        "INSERT INTO artefaktskjema (skjema_hash, skjema) VALUES (%s,%s)"
        " ON CONFLICT (skjema_hash) DO NOTHING",
        (rapportskjema.skjema_hash(),
         rapportskjema.kanonisk().decode("utf-8")))
    migrator_.execute(
        "INSERT INTO artefakttype_register (artefakttype,eiermodul,"
        "kontraktversjon,kontrakt_hash,skjema_hash) VALUES (%s,%s,1,%s,%s)",
        (at, modul, kh, rapportskjema.skjema_hash()))
    migrator_.commit()

    # Oppdraget: M-37-forankret (outboxens NOT NULL-trio), payload = den
    # LUKKEDE fire-felts-formen + ressurs_id (som minimeres bort — port 5).
    from db import kryptering
    from .test_m37 import _lag_sak
    sak, logg = _lag_sak(migrator_, TENANT)
    rid = secrets.token_hex(32)
    handling = f"kontroll.w{u}.nettsted"
    _sett_kontekst(migrator_, TENANT)
    migrator_.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant, unntak_id,"
        " repair_operation_id, repair_generation, handler_id,"
        " handler_versjon, maalhandling, input_hash, kategori)"
        " VALUES (%s,%s,%s,0,'wcag','1',%s,%s,'manglende_data')",
        (TENANT, sak, rid, handling, secrets.token_hex(32)))
    beslutning = migrator_.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'test','arbeidskapabilitet','ih2','p@1.0.0/x.y',"
        " 'TILLAT','[]',%s) RETURNING id", (TENANT, rid)).fetchone()[0]
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator_, TENANT)
    payload = {"mal_url": "https://kunde.example/", "kravsett": "wcag21_aa",
               "omfang": "enkeltside", "maks_sider": 1,
               "ressurs_id": "hemmelig-ref"}
    ct, nonce = kryptering.krypter(dek, payload, TENANT, key_id)
    opp = migrator_.execute(
        "INSERT INTO oppdrag (tenant, unntak_id, loggpost_id,"
        " repair_operation_id, oppdragstype, handling, eiermodul,"
        " payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist,"
        " beslutning_loggpost_id, koblingsstatus)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        " now()+interval '30 minutes', now()+interval '30 minutes',"
        " %s,'KOBLET') RETURNING id",
        (TENANT, sak, logg, rid, typenavn, handling, modul, ct, key_id,
         nonce, beslutning)).fetchone()[0]
    migrator_.commit()
    return modul, rel, int(opp)


@pg
def test_controlleren_hele_veien_med_fakemotor(migrator, miljo, monkeypatch):
    """Hele kjeden gjennom EKTE plattform (onboarding → claim m/ token →
    minimert payload (port 5) → rapportbygging → skjemavalidert opplasting →
    signert kvittering → PROMOTERT artefakt). FakeMotor bærer fasiten:
    tellingene inn == tellingene i det promoterte artefaktet."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    from db import kryptering
    from modules.wcag_audit import controller
    from .test_m37 import _signer_kvittering
    from .test_modul_onboarding_http import _onboard_token

    modul, rel, opp = _wcag_kjede(migrator, monkeypatch)
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, modul, rel)
            motor = FakeMotor(resultat=_motorresultat())
            res = controller.kjor_en(c, mtk, motor, _kontekst(),
                                     _signer_kvittering)
            assert res["utfall"] == "utfort", res
            assert res["kvittering_status"] == 200, res
            # Port 5: modulen så KUN de fire payloadfeltene.
            assert set(motor.payloads[0]) == {"mal_url", "kravsett",
                                              "omfang", "maks_sider"}
            _sett_kontekst(migrator, TENANT)
            tilstand, ct, nonce, ref = migrator.execute(
                "SELECT tilstand, ciphertext, nonce, dek_ref FROM artefakt"
                " WHERE artefakt_id=%s", (res["artefakt_id"],)).fetchone()
            assert tilstand == "promotert", tilstand
            dek = kryptering.hent_dek(migrator, TENANT, ref)
            rapport = kryptering.dekrypter(dek, bytes(ct), bytes(nonce),
                                           TENANT, ref)
            migrator.rollback()
            # Fasiten: tellingen fra motoren står ordrett i evidensen.
            assert rapport["sammendrag"]["alvorlig"] == 3
            assert rapport["sider_kontrollert"][0]["url"] \
                == "https://kunde.example/side"
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_motorfeil_gir_avbrutt_uten_artefakt(migrator, miljo, monkeypatch):
    """§10 siste rad: skjemabrudd/motorfeil → oppdraget feiler, INGEN delvis
    artefakt — og plattformen får en kvittering som sier det."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    from modules.wcag_audit import controller
    from .test_m37 import _signer_kvittering
    from .test_modul_onboarding_http import _onboard_token

    modul, rel, opp = _wcag_kjede(migrator, monkeypatch)
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, modul, rel)
            motor = FakeMotor(feil="chromium krasjet")
            res = controller.kjor_en(c, mtk, motor, _kontekst(),
                                     _signer_kvittering)
            assert res["utfall"] == "avbrutt", res
            _sett_kontekst(migrator, TENANT)
            n = migrator.execute(
                "SELECT count(*) FROM artefakt WHERE tenant=%s AND"
                " oppdrag_id=%s", (TENANT, opp)).fetchone()[0]
            migrator.rollback()
            assert n == 0, "motorfeil etterlot et delvis artefakt"
    finally:
        a.tjeneste.pool.lukk()


# --------------------------------------------------------------------------
# Kvitteringssvaret (Codex P1) — ingen Postgres: kjeden mot en stubklient.
# --------------------------------------------------------------------------

class _Svar:
    def __init__(self, status, kropp=None):
        self.status_code, self._kropp = status, kropp or {}

    def json(self):
        return self._kropp

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"uventet {self.status_code}")


class _Stubklient:
    """Claim → opplasting → kvittering, med valgbar kvitteringsstatus."""

    def __init__(self, kvitteringsstatus, opplastingsstatus=200):
        self.kvitteringsstatus = kvitteringsstatus
        self.opplastingsstatus = opplastingsstatus
        self.kvitteringer = []

    def post(self, sti, json=None, headers=None):
        if sti == "/v1/artefakt" and self.opplastingsstatus != 200:
            return _Svar(self.opplastingsstatus, {})
        if sti == "/v1/oppdrag/kvittering":
            self.kvitteringer.append(json)
        if sti == "/v1/oppdrag/claim":
            return _Svar(200, {
                "oppdrag_id": 1, "tenant": TENANT, "kvittering_jti": "j",
                "repair_operation_id": "r", "owner_claim_id": "o",
                "owner_generation": 0,
                "payload": {"mal_url": "https://kunde.example/",
                            "kravsett": "wcag21_aa", "omfang": "enkeltside"},
                "opplasting": {"jti": "kap"}})
        if sti == "/v1/artefakt":
            return _Svar(200, {"artefakt_id": "a-1",
                               "klartekst_sha256": "b" * 64})
        assert sti == "/v1/oppdrag/kvittering", sti
        return _Svar(self.kvitteringsstatus, {})


def test_avvist_kvittering_er_ikke_utfort():
    """Codex P1: 409 fra kvitteringsendepunktet (fencing, hashavvik,
    avvist promotering) eller 5xx betyr at oppdraget står IGJEN uferdig hos
    plattformen. Meldte controlleren `utfort` uansett, ville en planlegger
    tro at kjøringen var i havn — modulens ord mot plattformens tilstand.
    Kontroll: fjern _kvittert-sjekken i controlleren, så blir denne rød."""
    from modules.wcag_audit import controller
    motor = FakeMotor(resultat=_motorresultat())
    for status in (409, 500):
        res = controller.kjor_en(_Stubklient(status), "tk", motor,
                                 _kontekst(), lambda k: k)
        assert res["utfall"] == "ukvittert", res
        assert res["kvittering_status"] == status
        # Artefaktet ER lastet opp — utfallet skjuler ikke det, det nekter
        # bare å kalle kjøringen ferdig.
        assert res["artefakt_id"] == "a-1"
    ok = controller.kjor_en(_Stubklient(200), "tk", motor, _kontekst(),
                            lambda k: k)
    assert ok["utfall"] == "utfort", ok


def test_avvist_opplasting_gir_feilkvittering():
    """Codex P1: `ro.raise_for_status()` kastet ut av kjøreløkka når
    plattformen avviste artefaktet (413/400 på 1 MiB-taket, 409 på
    fencing, 5xx). Da fikk plattformen ALDRI vite noe, og oppdraget stod
    claimet til fristen. Kontroll: bytt statussjekken i controlleren
    tilbake til `raise_for_status()`, så blir denne rød."""
    from modules.wcag_audit import controller
    motor = FakeMotor(resultat=_motorresultat())
    for status in (400, 409, 413, 500):
        klient = _Stubklient(200, opplastingsstatus=status)
        res = controller.kjor_en(klient, "tk", motor, _kontekst(),
                                 lambda k: k)
        assert res["utfall"] == "avbrutt", res
        assert res["opplasting_status"] == status
        assert res["kvittert"] is True
        # ... og kvitteringen er FAKTISK sendt, med ærlig feilkode.
        assert len(klient.kvitteringer) == 1
        assert klient.kvitteringer[0]["resultat"] == "feilet"
        assert klient.kvitteringer[0]["feilkode"] == "opplasting_avvist"
        # Aldri et artefakt-id: det finnes ikke noe artefakt å vise til.
        assert "artefakt_id" not in res


def test_rapporten_holdes_under_1_mib():
    """Codex P1: antallsgrensene alene holder ikke 1 MiB-taket — 500 funn
    à ti 200-tegns eksempler passerer skjemaet og blir avvist av
    `/v1/artefakt`. Rapporten måles nå med SERVERENS kanonisering og
    kappes ærlig før opplasting. Kontroll: fjern `_under_taket`-kallet i
    `bygg`, så blir denne rød."""
    import jsonschema
    from modules.wcag_audit import rapportskjema
    from modules.wcag_audit.rapport import (MAKS_BYTES, _kanoniske_bytes,
                                            bygg)

    # Verstefallsrapporten: maks funn, maks eksempler, maks selektorlengde.
    stor = tuple({"regel_id": f"regel-{i:04d}" + "x" * 110,
                  "alvorlighet": "alvorlig", "antall": 3,
                  "eksempler": [f"#n{i}-{j}" + "s" * 190 for j in range(10)]}
                 for i in range(500))
    r = bygg(_motorresultat(funn=stor), payload={"kravsett": "wcag21_aa"},
             kontekst=_kontekst())
    jsonschema.Draft202012Validator(rapportskjema.SKJEMA).validate(r)
    assert len(_kanoniske_bytes(r)) <= MAKS_BYTES
    assert r["avkortet"]["truffet"] is True
    # SAMMENDRAGET er urørt: kappingen gjelder listene, ikke sannheten om
    # omfanget (500 funn à 3 forekomster).
    assert r["sammendrag"]["alvorlig"] == 1500
    # En normal rapport røres ikke.
    liten = bygg(_motorresultat(funn=(
        {"regel_id": "r1", "alvorlighet": "lav", "antall": 1,
         "eksempler": ["#a"]},)),
        payload={"kravsett": "wcag21_aa"}, kontekst=_kontekst())
    assert liten["funn"][0]["eksempler"] == ["#a"]
    assert liten["avkortet"]["truffet"] is False


# --------------------------------------------------------------------------
# `format` er en REGEL, ikke en annotasjon — Codex P2.
# --------------------------------------------------------------------------

def test_formatsjekk_avviser_ugyldig_kjort_ts():
    """Draft202012Validator behandler `format` som annotasjon uten en
    format-checker, så rapportskjemaets `kjort_ts: {format: date-time}` var
    ren dokumentasjon: `"i går"` passerte begge de annonserte
    valideringspunktene og ble promotert. Kontroll: fjern
    format_checker-argumentet i `valider`, så slipper alle de ugyldige
    gjennom."""
    from api.artefaktskjema import valider
    from modules.wcag_audit import rapportskjema
    from modules.wcag_audit.rapport import bygg
    rapport = bygg(_motorresultat(), payload={"kravsett": "wcag21_aa"},
                   kontekst=_kontekst())
    assert not valider(rapportskjema.SKJEMA, rapport)
    # Små t/z er RFC 3339 (§5.6) og skal fortsatt passere.
    assert not valider(rapportskjema.SKJEMA,
                       {**rapport, "kjort_ts": "2026-08-17t21:03:32z"})
    for ugyldig in ("i går", "2026-08-17", "2026-08-17T21:03:32",
                    "2026-02-31T00:00:00Z", "2026-08-17T25:00:00Z",
                    "2026-08-17T21:03:32+00:00\n"):
        feil = valider(rapportskjema.SKJEMA, {**rapport,
                                              "kjort_ts": ugyldig})
        assert any("kjort_ts" in f for f in feil), (ugyldig, feil)
    # Den DELTE, globale checkeren skal ikke være endret av importen — vi
    # eier vår egen kopi.
    import jsonschema
    assert "date-time" not in \
        jsonschema.Draft202012Validator.FORMAT_CHECKER.checkers


def test_odelagt_skjema_avvises_i_stedet_for_aa_kaste():
    """Codex P2: `registrer_artefaktskjema` sjekker bare at JSON-en er et
    objekt, så `{"type": "strng"}` kunne registreres og bindes til en
    artefakttype. Da døde HVER opplastning og promotering på et ufanget
    UnknownType fra validatoren — og fordi både skjemaraden og
    typebindingen er immutable, kunne typen aldri repareres. Metasjekken
    kjøres på begge sider av den udødelige raden. Kontroll: fjern
    skjemafeil-kallet i `valider`, så kaster denne i stedet for å avvise."""
    from api.artefaktskjema import skjemafeil, valider
    assert skjemafeil({"type": "strng"})
    assert skjemafeil({"required": "ikke-en-liste"})
    assert not skjemafeil({"type": "object"})
    # Registreringsveien kjører NØYAKTIG denne sjekken på det skjemaet den
    # er i ferd med å gjøre udødelig.
    from modules.wcag_audit import rapportskjema
    assert not skjemafeil(rapportskjema.SKJEMA)
    # ... og kom et ødelagt skjema likevel inn, er svaret en feilliste.
    feil = valider({"type": "strng"}, {"a": 1})
    assert feil and "JSON Schema" in feil[0]


# --------------------------------------------------------------------------
# Kommandomotoren mot en EKTE (og fiendtlig) underprosess — Codex P1.
# --------------------------------------------------------------------------

def _motorkommando(kropp):
    return [sys.executable, "-c", kropp]


def test_motorutdata_er_bundet_i_minnet():
    """Codex P1: `capture_output=True` bufret stdout og stderr uten tak i
    opptil en time, i den CREDENTIAL-bærende prosessen. Rapportens senere
    1 MiB-grense hjelper ikke: minnet er brukt før JSON-parsingen. En
    motor som spyr ut data skal møte Motorfeil, ikke spise
    controllerhosten. Kontroll: bytt tilbake til subprocess.run med
    capture_output, så henger denne testen på minne i stedet for å bestå."""
    from modules.wcag_audit.motor import (Kommandomotor, Motorfeil,
                                          MAKS_STDOUT)
    god = json.dumps({"regelsett_versjon": "axe-4.10", "varighet_ms": 5,
                      "sider": [{"url": "https://a.example/",
                                 "status": "ok"}],
                      "funn": [], "blokkert": [],
                      "avkortet": [False, None, None]})

    # Lykkelig vei: payloaden når stdin, JSON-en leses tilbake.
    m = Kommandomotor(_motorkommando(
        "import sys,json;d=json.load(sys.stdin);assert d['mal_url'];"
        "sys.stdout.write(%r)" % god))
    r = m.kjor({"mal_url": "https://kunde.example/"})
    assert r.regelsett_versjon == "axe-4.10" and r.varighet_ms == 5

    # Uendelig stdout: avbrytes ved taket, ikke ved minnetaket til hosten.
    uendelig = Kommandomotor(_motorkommando(
        "import sys\nwhile True: sys.stdout.buffer.write(b'x'*65536)"),
        tidsavbrudd_s=30)
    with pytest.raises(Motorfeil, match=str(MAKS_STDOUT)):
        uendelig.kjor({})

    # Mye stderr: dreneres (ellers vranglåser motoren på full rørbuffer),
    # og bare en snipp beholdes til feilmeldingen.
    prat = Kommandomotor(_motorkommando(
        "import sys\nfor i in range(400): sys.stderr.buffer.write(b'e'*65536)"
        "\nsys.exit(3)"), tidsavbrudd_s=60)
    with pytest.raises(Motorfeil, match="motor exit 3") as ei:
        prat.kjor({})
    assert len(str(ei.value)) < 400, "stderr slapp inn i meldingen ubundet"

    # Fristen bæres av vakthunden, også når motoren har lukket stdout og
    # lever videre — den veien hang tidligere til timeouten uansett.
    for kropp in ("import time;time.sleep(300)",
                  "import sys,os,time;sys.stdout.write(%r);"
                  "sys.stdout.flush();os.close(1);time.sleep(300)" % god):
        treg = Kommandomotor(_motorkommando(kropp), tidsavbrudd_s=2)
        with pytest.raises(Motorfeil, match="TimeoutExpired"):
            treg.kjor({})


def test_numerisk_overflyt_fra_motoren_er_motorfeil():
    """Codex P1: konverteringsvaktene fanget bare ValueError og TypeError.

    Tre feilmoduser slapp forbi, og alle tre ender samme sted — et unntak
    ut av `controller.kjor_en` (som kun fanger Motorfeil og
    ValidationError), altså et claimet oppdrag som står ufullført til
    fristen i stedet for å bli kvittert som feilet:

      * `1e309` er gyldig JSON, blir `inf`, og `int(inf)` er OverflowError;
      * `10**20` konverterer fint, passerer skjemaet (som ikke har noe
        øvre tak) og er `Ikkekanoniserbar` først under kanoniseringen;
      * en SUM over 500 funn kan gå over det trygge området selv når hvert
        ledd lå under.

    Kontroll: ta `OverflowError` ut av `motor.heltall`, eller fjern
    `MAKS_HELTALL`-sjekken, eller la `_kanoniske_bytes` slippe
    `Ikkekanoniserbar` videre — hver av de tre gjør denne rød med et annet
    unntak enn Motorfeil.
    """
    from modules.wcag_audit.motor import (Kommandomotor, MAKS_HELTALL,
                                          Motorfeil, heltall)
    from modules.wcag_audit.rapport import bygg

    # Porten selv, på alle tre kantene.
    for raa in ("ukjent", {"a": 1}, None, True, float("inf"), float("nan"),
                1e309, MAKS_HELTALL + 1, -(MAKS_HELTALL + 1), 10 ** 20):
        with pytest.raises(Motorfeil):
            heltall(raa)
    assert heltall(MAKS_HELTALL) == MAKS_HELTALL and heltall("42") == 42

    # `varighet_ms: 1e309` fra en ekte motorkjøring: OverflowError før.
    over = json.dumps({"regelsett_versjon": "axe-4.10",
                       "varighet_ms": 1e309, "sider": [], "funn": [],
                       "blokkert": [], "avkortet": [False, None, None]})
    m = Kommandomotor(_motorkommando("import sys;sys.stdout.write(%r)" % over),
                      tidsavbrudd_s=30)
    with pytest.raises(Motorfeil):
        m.kjor({})

    # `antall` og `avkortet` er samme eksponering, i rapportbyggingen.
    for over in ({"funn": ({"regel_id": "r", "alvorlighet": "lav",
                            "antall": 10 ** 20, "eksempler": []},)},
                 {"blokkert": ({"vert": "f.example", "antall": 10 ** 20,
                                "art": "font"},)},
                 {"avkortet": (True, 10 ** 20, 10 ** 20)}):
        with pytest.raises(Motorfeil):
            bygg(_motorresultat(**over), payload={"kravsett": "wcag21_aa"},
                 kontekst=_kontekst())

    # Summen: hvert ledd er lovlig, `sammendrag` blir det ikke.
    ledd = MAKS_HELTALL // 3
    with pytest.raises(Motorfeil):
        bygg(_motorresultat(funn=tuple(
                {"regel_id": f"r{i}", "alvorlighet": "lav", "antall": ledd,
                 "eksempler": []} for i in range(4))),
             payload={"kravsett": "wcag21_aa"}, kontekst=_kontekst())
