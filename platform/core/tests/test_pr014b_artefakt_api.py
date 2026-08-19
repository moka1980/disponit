"""PR-014b CP5 (del 2): POST /v1/artefakt — opplastingsendepunktet."""
import secrets
import types

import pytest

from .test_api import (ANNEN_TENANT, DSN, MIGRATOR_DSN, TENANT, app, klient,  # noqa: F401
                       dekker, migrator, miljo, token)
from .test_m37 import _sett_kontekst
from .test_pr014b_artefaktkapabilitet import _plukket_oppdrag_med_binding

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _domeneaktorsesjon(tenant: str, sub: str,
                       roller: str = "leser") -> tuple[str, str]:
    """Browserøkt for CSRF-porten i domeneattestasjon.

    `roller` er rollen medlemskapet får. PR-015 flyttet fire-øyne-sjekken NED i
    basen: `avgi_overtakelse_attestasjon` slår opp prinsipalen bak stemmen med
    `laas_godkjenner()` og krever `domeneadjudikator` DER, ikke bare i
    API-laget. En økt med bare `leser` avvises derfor av motoren før
    opptellingen — det er meningen, og de to attestasjonstestene under sender
    rollen eksplisitt i stedet for å arve en default som ville skjult hvilken
    autorisasjon de faktisk hviler på.
    """
    from api import sesjon as sesjonmodul
    from db.pg import koble, sett_kontekst
    from .test_pr010_db import _identitet

    cookie, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    m = koble(MIGRATOR_DSN)
    try:
        sett_kontekst(m, tenant, "sys", "r0")
        bid = _identitet(m, sub=f"{tenant}-{sub}")
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


def _domeneovertakelsessak(migrator):
    from .test_pr014b_domene_artefakt import _admin, _host

    h = _host()
    a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (ANNEN_TENANT, h)).fetchone()[0]
        a.commit()
        assert res == "konflikt:" + TENANT
    finally:
        a.close()
    # 041: saken ble laget av overtakelsen selv — slå den opp der den bor.
    _sett_kontekst(migrator, "__plattform_domener")
    uid = int(migrator.execute(
        "SELECT id FROM unntak WHERE hostname_ref=%s"
        "  AND sakskilde='domeneovertakelse' AND NOT terminal",
        (h,)).fetchone()[0])
    migrator.rollback()
    return uid


def _post_domeneattestasjon(klient, uid, cookie, csrf, utfall, vinnende,
                            rev=0):
    """`rev` er revisjonen stemmen avgis PÅ (041 §21, Codex P1).

    Standarden er 0 fordi `_domeneovertakelsessak` lager en FERSK sak, og en
    sak fødes på revisjon 0 — den flyttes kun av et utfordrer-/generasjons-
    skifte (§6). Ingen av testene her skifter utfordrer.
    """
    from api import sesjon as sesjonmodul

    return klient.post(
        f"/v1/unntak/{uid}/domeneattestasjon",
        json={"utfall": utfall, "vinnende_tenant": vinnende,
              "saksrevisjon": rev},
        headers={"X-Disponit-CSRF": csrf},
        cookies={sesjonmodul.C_SESJON: cookie})


def _utsted_cap(opp, modul, kh, at):
    from db.pg import koble
    jti = secrets.token_hex(16)
    c = koble(DSN)
    try:
        c.execute("SELECT jti FROM utsted_artefaktkapabilitet(%s,%s,%s,'r1',1,%s,"
                  "0,%s,%s,900)", (TENANT, opp, modul, kh, at, jti))
        c.commit()
    finally:
        c.close()
    return jti


def _post(klient, tok, jti, rapport):
    return klient.post("/v1/artefakt",
                       json={"kapabilitet_jti": jti, "rapport": rapport},
                       headers={"authorization": f"Bearer {tok}"})


@pg
def test_upload_ok_og_idempotent(migrator, klient, token):
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    jti = _utsted_cap(opp, modul, kh, at)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))
    r = _post(klient, tok, jti, {"funn": 3, "sider": ["a"]})
    assert r.status_code == 200, r.text
    aid = r.json()["artefakt_id"]
    assert len(r.json()["klartekst_sha256"]) == 64
    # idempotent: samme jti + samme rapport → samme artefakt_id.
    r2 = _post(klient, tok, jti, {"sider": ["a"], "funn": 3})   # ulik nøkkelorden
    assert r2.status_code == 200 and r2.json()["artefakt_id"] == aid, \
        "JCS-kanonisering ga ikke samme id for samme dokument"
    _sett_kontekst(migrator, TENANT)
    st = migrator.execute("SELECT tilstand, nonce IS NOT NULL FROM artefakt"
                          " WHERE artefakt_id=%s", (aid,)).fetchone()
    migrator.rollback()
    assert st == ("staged", True)


@pg
def test_upload_konflikt_samme_jti_annet_dokument(migrator, klient, token):
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    jti = _utsted_cap(opp, modul, kh, at)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))
    assert _post(klient, tok, jti, {"a": 1}).status_code == 200
    r = _post(klient, tok, jti, {"a": 2})   # samme jti, ANNET dokument
    assert r.status_code == 409, r.text


@pg
def test_upload_uten_scope_avvises(migrator, klient, token):
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    jti = _utsted_cap(opp, modul, kh, at)
    tok, _ = token(rolle=modul, scopes=("orders:execute:x.",))
    assert _post(klient, tok, jti, {"a": 1}).status_code == 403


@pg
def test_upload_ugyldig_kapabilitet(migrator, klient, token):
    modul = "m-" + secrets.token_hex(4)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))
    r = _post(klient, tok, secrets.token_hex(16), {"a": 1})   # ukjent jti
    assert r.status_code == 401


@pg
@dekker("dobbel_attestasjon")
def test_domeneattestasjon_dobbel_attestasjon(klient, migrator, monkeypatch):
    uid = _domeneovertakelsessak(migrator)
    cookie, csrf = _domeneaktorsesjon(ANNEN_TENANT, "domene-1",
                                      roller="domeneadjudikator")
    auth = {"aktor": "aktor-1"}

    monkeypatch.setattr(
        "api.app._autentiser",
        lambda *a, **k: types.SimpleNamespace(tenant=ANNEN_TENANT,
                                              token_id=auth["aktor"]))

    r1 = _post_domeneattestasjon(klient, uid, cookie, csrf,
                                 "godkjenn", ANNEN_TENANT)
    assert r1.status_code == 409, r1.text
    assert r1.json()["feil"] == "krever_to_attestasjoner"

    r2 = _post_domeneattestasjon(klient, uid, cookie, csrf,
                                 "godkjenn", ANNEN_TENANT)
    assert r2.status_code == 409, r2.text
    assert r2.json()["feil"] == "dobbel_attestasjon"


@pg
@dekker("attestasjon_avvist")
def test_domeneattestasjon_avvist_naar_saken_er_foreldet(klient, migrator,
                                                         monkeypatch):
    uid = _domeneovertakelsessak(migrator)
    cookie, csrf = _domeneaktorsesjon(ANNEN_TENANT, "domene-2",
                                      roller="domeneadjudikator")
    auth = {"aktor": "aktor-1"}

    monkeypatch.setattr(
        "api.app._autentiser",
        lambda *a, **k: types.SimpleNamespace(tenant=ANNEN_TENANT,
                                              token_id=auth["aktor"]))

    r1 = _post_domeneattestasjon(klient, uid, cookie, csrf,
                                 "godkjenn", ANNEN_TENANT)
    assert r1.status_code == 409, r1.text
    assert r1.json()["feil"] == "krever_to_attestasjoner"

    auth["aktor"] = "aktor-2"
    r2 = _post_domeneattestasjon(klient, uid, cookie, csrf,
                                 "godkjenn", ANNEN_TENANT)
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "avgjort"

    auth["aktor"] = "aktor-3"
    r3 = _post_domeneattestasjon(klient, uid, cookie, csrf,
                                 "godkjenn", ANNEN_TENANT)
    assert r3.status_code == 409, r3.text
    assert r3.json()["feil"] == "attestasjon_avvist"


def _oppdrag_owner(migrator, opp):
    _sett_kontekst(migrator, TENANT)
    r = migrator.execute("SELECT owner_claim_id, repair_operation_id,"
                         " owner_generation FROM oppdrag"
                         " WHERE tenant=%s AND id=%s", (TENANT, opp)).fetchone()
    migrator.rollback()
    return r


def _kvitteringskap(opp, owner_claim, gen):
    from db.pg import koble
    jti = secrets.token_hex(16)
    c = koble(DSN)
    try:
        c.execute("SELECT jti FROM utsted_kvitteringskapabilitet(%s,%s,%s,%s)",
                  (opp, owner_claim, gen, jti))
        c.commit()
    finally:
        c.close()
    return jti


def _last_opp_artefakt(migrator, klient, token, rev="kompenserende"):
    """Bygg bundet, plukket oppdrag + last opp et staged artefakt. Returnerer
    (opp, modul, kh, artefakt_id, owner_claim, repair_operation_id, gen, hash).

    Hashen er den serveren selv beregnet ved opplasting — nøyaktig den verdien en
    ekte controller får i opplastingssvaret og MÅ signere i kvitteringen.
    `rev` er kontraktens reversibilitet (043)."""
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh, rev)
    oc, rep, gen = _oppdrag_owner(migrator, opp)
    ajti = _utsted_cap(opp, modul, kh, at)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))
    svar = _post(klient, tok, ajti, {"funn": 1}).json()
    return (opp, modul, kh, svar["artefakt_id"], oc, rep, gen,
            svar["klartekst_sha256"])


def _kvitteringskropp(opp, kjti, rep, oc, gen, aid, kts=None):
    kropp = {"oppdrag_id": opp, "tenant": TENANT, "kvittering_jti": kjti,
             "repair_operation_id": rep, "owner_claim_id": oc,
             "owner_generation": gen, "resultat": "utfort",
             "ressurs_id": "fak-1", "artefakt_id": aid}
    # Codex P1: den ATTESTERTE klartekst-hashen hører til kvitteringen. Uten den
    # leste promoteringen artefaktets egen hash og sammenlignet den med seg selv.
    if kts is not None:
        kropp["klartekst_sha256"] = kts
    return kropp


@pg
def test_kvittering_promoterer_artefakt(migrator, klient, token):
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep, gen, kts = _last_opp_artefakt(
        migrator, klient, token)
    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid, kts))
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 200 and rk.json()["status"] == "utfort", rk.text
    _sett_kontekst(migrator, TENANT)
    st = migrator.execute("SELECT tilstand FROM artefakt WHERE artefakt_id=%s",
                          (aid,)).fetchone()[0]
    migrator.rollback()
    assert st == "promotert", "artefaktet ble ikke promotert av kvitteringen"


@pg
def test_kvittering_med_fremmed_artefakt_karantenesetter(migrator, klient, token):
    # §7 pkt. 8: refererer kvitteringen et artefakt som IKKE er bundet til dette
    # oppdraget, promoteres ingenting, oppdraget avsluttes ikke, og det
    # karantenesettes (409). Bindingsavviket måles UTEN å røre epoch direkte
    # (det ville krevd claim-funksjonen).
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep, gen, kts = _last_opp_artefakt(
        migrator, klient, token)
    # et ANNET oppdrag + artefakt (aid2 hører til opp2, ikke opp).
    _, _, _, aid2, _, _, _, kts2 = _last_opp_artefakt(migrator, klient, token)
    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid2, kts2))
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 409, rk.text
    _sett_kontekst(migrator, TENANT)
    art_st = migrator.execute("SELECT tilstand FROM artefakt WHERE artefakt_id=%s",
                              (aid2,)).fetchone()[0]
    opp_st = migrator.execute("SELECT status FROM oppdrag WHERE tenant=%s AND id=%s",
                              (TENANT, opp)).fetchone()[0]
    migrator.rollback()
    assert (art_st, opp_st) == ("staged", "plukket"), \
        "bindingsavvik skulle karantenesatt (fremmed artefakt bevart, ikke avsluttet)"


def _rydd():
    """Kjør oppryddingen som domains_admin (den eier rydd_staged_artefakter)."""
    from db.pg import koble
    from .test_api import MIGRATOR_DSN
    c = koble(MIGRATOR_DSN)
    try:
        c.execute("SET ROLE disponit_domains_admin")
        c.execute("SELECT rydd_staged_artefakter()")
        c.commit()
    finally:
        c.close()


def _gammelt_artefakt(migrator, aid):
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE artefakt SET opprettet=now()-interval '25 hours'"
                     " WHERE artefakt_id=%s", (aid,))
    migrator.commit()


def _art(migrator, aid):
    _sett_kontekst(migrator, TENANT)
    r = migrator.execute("SELECT tilstand, ciphertext IS NOT NULL FROM artefakt"
                         " WHERE artefakt_id=%s", (aid,)).fetchone()
    migrator.rollback()
    return r


@pg
def test_sen_kvittering_bevarer_artefaktet(migrator, klient, token):
    """Codex: en GODTATT sen kvittering må ikke miste evidensen sin.

    Den sene veien lagrer kvitteringen (202) uten å avslutte noe — artefaktet
    promoteres derfor aldri og ble stående `staged`, hvorpå oppryddingen nullet
    ciphertexten etter 24 t. Da satt vi igjen med en akseptert kvittering som
    pekte på et tomt artefakt.

    MUTASJONEN SOM DREPER DENNE: fjern `bevar_artefakt`-kallet i
    `not kan_avslutte`-grenen — da er tilstanden `staged` og rydd tar den.
    """
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep, gen, kts = _last_opp_artefakt(
        migrator, klient, token)
    kjti = _kvitteringskap(opp, oc, gen)
    # Foreldet eier: kapabiliteten er utstedt for generasjon `gen`, mens
    # oppdraget nå står på gen+1. Kvitteringen er fortsatt gyldig EVIDENS.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE oppdrag SET owner_generation=owner_generation+1"
                     " WHERE tenant=%s AND id=%s", (TENANT, opp))
    migrator.commit()
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid, kts))
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 202, rk.text
    assert rk.json()["status"] == "lagret_uten_statusendring"
    assert _art(migrator, aid) == ("bevart", True), \
        "den sene kvitteringens artefakt ble ikke bevart"
    _gammelt_artefakt(migrator, aid)
    _rydd()
    assert _art(migrator, aid) == ("bevart", True), \
        "oppryddingen ødela evidensen bak en godtatt sen kvittering"


def _en_til_opplasting(migrator, klient, token, opp, modul, kh):
    """Nok en opplasting på SAMME (fortsatt plukkede) oppdrag. (id, hash)."""
    at = migrator.execute("SELECT artefakttype FROM artefakttype_register"
                          " WHERE eiermodul=%s AND kontrakt_hash=%s",
                          (modul, kh)).fetchone()[0]
    migrator.rollback()
    jti = _utsted_cap(opp, modul, kh, at)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))
    svar = _post(klient, tok, jti, {"funn": 2}).json()
    return svar["artefakt_id"], svar["klartekst_sha256"]


@pg
def test_motstridende_kvittering_karantenesetter_artefaktet(migrator, klient, token):
    # Samme bevaringsregel på konfliktveien: sikkerhetssaken er skrevet, og
    # artefaktet det andre resultatet påberoper seg er nettopp det
    # etterforskningen trenger. Karantene ryddes aldri.
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep, gen, kts = _last_opp_artefakt(
        migrator, klient, token)
    # BEGGE opplastingene skjer mens oppdraget står plukket (kapabiliteter
    # utstedes kun da).
    aid2, kts2 = _en_til_opplasting(migrator, klient, token, opp, modul, kh)
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    h = {"authorization": f"Bearer {tok2}"}
    kjti = _kvitteringskap(opp, oc, gen)
    kv1 = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid, kts))
    assert klient.post("/v1/oppdrag/kvittering", json=kv1,
                       headers=h).status_code == 200
    # Samme kapabilitet, ANNET resultat + annet artefakt → motstrid (409).
    kropp = _kvitteringskropp(opp, kjti, rep, oc, gen, aid2, kts2)
    kropp["ressurs_id"] = "fak-2"
    rk = klient.post("/v1/oppdrag/kvittering", json=_signer_kvittering(kropp),
                     headers=h)
    assert rk.status_code == 409, rk.text
    _gammelt_artefakt(migrator, aid2)
    _rydd()
    assert _art(migrator, aid2) == ("karantene", True), \
        "det motstridende resultatets artefakt overlevde ikke oppryddingen"


@pg
def test_promoteringsfeil_brenner_kapabiliteten(migrator, klient, token):
    """Codex P2: en avvist artefaktkvittering (fremmed/foreldet artefakt) må
    BRENNE kvitteringskapabiliteten i den committede karantenetransaksjonen. En
    full rollback her angret brenningen, så den samme jti-en kunne spilles om til
    fristen, og en ANNEN kvittering med samme jti unnslapp klassifisering mot den
    første resultathashen.

    MUTASJONEN SOM DREPER DENNE: bytt savepointen (`with conn.transaction()`) rundt
    promoter_artefakt tilbake til `conn.rollback()` i promoteringsfeil-grenen."""
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep, gen, kts = _last_opp_artefakt(
        migrator, klient, token)
    _, _, _, fremmed, _, _, _, fkts = _last_opp_artefakt(migrator, klient, token)
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    h = {"authorization": f"Bearer {tok2}"}
    kjti = _kvitteringskap(opp, oc, gen)
    # 1) FREMMED artefakt → promotering feiler → 409 + karantene.
    kv1 = _signer_kvittering(
        _kvitteringskropp(opp, kjti, rep, oc, gen, fremmed, fkts))
    assert klient.post("/v1/oppdrag/kvittering", json=kv1,
                       headers=h).status_code == 409
    # Brenningen + resultathashen skal ha OVERLEVD (ikke rullet med promoteringen).
    # Måles ATFERDSMESSIG (kvitteringskapabiliteter er off-limits for migrator):
    # 2) SAMME jti, gyldig eget artefakt, ANNET resultat. Er brenningen intakt,
    #    står jti-en 'brukt' med første hash → motstrid (409). Var den rullet
    #    tilbake, ville re-posten vært fersk og promotert aid → 200.
    kropp = _kvitteringskropp(opp, kjti, rep, oc, gen, aid, kts)
    kropp["ressurs_id"] = "fak-2"
    rk = klient.post("/v1/oppdrag/kvittering", json=_signer_kvittering(kropp),
                     headers=h)
    assert rk.status_code == 409, rk.text
    # Oppdraget ble aldri avsluttet av den ugyldige kvitteringen.
    _sett_kontekst(migrator, TENANT)
    opp_st = migrator.execute("SELECT status FROM oppdrag WHERE tenant=%s AND id=%s",
                              (TENANT, opp)).fetchone()[0]
    migrator.rollback()
    assert opp_st == "plukket", "en avvist kvittering avsluttet oppdraget likevel"


@pg
def test_avvist_kvittering_retry_gir_samme_409(migrator, klient, token):
    """Codex: en IDENTISK retry av en AVVIST kvittering må gi samme 409, ikke en
    generisk 200 idempotent — jobben ble aldri fullført (artefaktet karantene). En
    tapt 409-respons ville ellers sett ut som suksess ved retry.

    MUTASJONEN SOM DREPER DENNE: fjern karantene-sjekken i idempotens-grenen."""
    from .test_m37 import _signer_kvittering
    from .test_pr014b_artefaktkapabilitet import _mk_admin
    opp, modul, kh, aid, oc, rep, gen, kts = _last_opp_artefakt(
        migrator, klient, token)
    # Nødstopp modulen → epoch-mismatch → EGEN artefakt (under opp) avvises OG
    # karantenesettes (karantenesett er no-op for et fremmed artefakt).
    ma = _mk_admin("disponit_modules_admin")
    try:
        ma.execute("SELECT noddeaktiver_modul(%s,'nodstopp','sys')", (modul,))
        ma.commit()
    finally:
        ma.close()
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    hh = {"authorization": f"Bearer {tok2}"}
    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid, kts))
    # 1) Avvist (409), egen artefakt karantenesatt.
    assert klient.post("/v1/oppdrag/kvittering", json=kv,
                       headers=hh).status_code == 409
    # 2) IDENTISK retry → SAMME 409, ikke 200 idempotent.
    rk = klient.post("/v1/oppdrag/kvittering", json=kv, headers=hh)
    assert rk.status_code == 409, \
        "identisk retry av en avvist kvittering så ut som suksess: " + rk.text


@pg
def test_avvist_fremmed_artefakt_retry_gir_samme_409(migrator, klient, token):
    """Codex: avvisnings-utfallet må persisteres UAVHENGIG av artefakt-tilstand.
    En kvittering som navngir et FREMMED artefakt karantenesetter ingenting
    (no-op), så en retry ville ellers falt gjennom til 200 idempotent selv om
    jobben aldri ble fullført. Sikkerhetssaken bærer utfallet.

    MUTASJONEN SOM DREPER DENNE: bytt historikk-sjekken tilbake til å lese
    artefakt-tilstand='karantene'."""
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep, gen, kts = _last_opp_artefakt(
        migrator, klient, token)
    _, _, _, fremmed, _, _, _, fkts = _last_opp_artefakt(migrator, klient, token)
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    hh = {"authorization": f"Bearer {tok2}"}
    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering(
        _kvitteringskropp(opp, kjti, rep, oc, gen, fremmed, fkts))
    # 1) FREMMED artefakt → avvist (409); ingenting karantenesatt under opp.
    assert klient.post("/v1/oppdrag/kvittering", json=kv,
                       headers=hh).status_code == 409
    # 2) IDENTISK retry → SAMME 409 (utfallet ligger i sikkerhetssaken).
    rk = klient.post("/v1/oppdrag/kvittering", json=kv, headers=hh)
    assert rk.status_code == 409, \
        "retry av avvist kvittering med fremmed artefakt så ut som suksess: " + rk.text


@pg
def test_kvittering_ikke_uuid_artefakt_er_request_feil(migrator, klient, token):
    """Codex: en gyldig signert kvittering med en ikke-UUID artefakt_id må gi
    request_feilformet (400), ikke db_utilgjengelig — uten valideringen når den
    uuid-kolonnen og PostgreSQL kaster InvalidTextRepresentation, feilrapportert
    som om basen var nede.

    MUTASJONEN SOM DREPER DENNE: fjern uuid.UUID-valideringen i _ingest_kvittering."""
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep, gen, kts = _last_opp_artefakt(
        migrator, klient, token)
    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering(
        _kvitteringskropp(opp, kjti, rep, oc, gen, "ikke-en-uuid", kts))
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 400 and "request_feilformet" in rk.text, rk.text


def test_resultathash_uendret_for_kvittering_uten_artefakt():
    """Codex: de valgfrie artefaktfeltene må ikke endre hashen til kvitteringer
    som ikke har dem.

    Tok `_resultathash` dem alltid med som eksplisitt null, fikk hver kvittering
    som ble akseptert FØR denne utrullingen en ny hash ved en byte-identisk
    re-post — og ble klassifisert som `motstridende_kvittering` (sikkerhetssak)
    i stedet for idempotent. Oppgraderingen selv ville altså produsert
    sikkerhetssaker.

    MUTASJONEN SOM DREPER DENNE: ta artefaktfeltene med ubetinget i
    `kjerne_felt`."""
    import hashlib as _hl
    import json as _js
    from api.app import _resultathash

    kv = {"oppdrag_id": 7, "repair_operation_id": "rep-1",
          "resultat": "utfort", "ressurs_id": "fak-1",
          "kvittering_jti": "j", "signatur": {"verdi": "x"}}
    gammel = _hl.sha256(_js.dumps(
        {"oppdrag_id": 7, "repair_operation_id": "rep-1",
         "resultat": "utfort", "ressurs_id": "fak-1"},
        sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    assert _resultathash(kv) == gammel, \
        "hashen til en legacy-kvittering endret seg av artefaktfeltene"
    # ...men en kvittering som FAKTISK bærer et artefakt hasher annerledes:
    # ellers ville motstridende artefaktbevis sett idempotente ut.
    assert _resultathash({**kv, "artefakt_id": "a", "klartekst_sha256": "b"}) \
        != gammel


@pg
def test_kvittering_ikke_kanonisk_uuid_er_request_feil(migrator, klient, token):
    """Codex: valideringen må gjelde den verdien som FAKTISK bindes.

    `uuid.UUID(str(x))` godtar `urn:uuid:...` (og versaler, klammer, og etter
    str() et 32-sifret tall), men det parsede resultatet ble kastet — originalen
    gikk videre til `WHERE artefakt_id=%s`, hvor PostgreSQL avviste den og feilen
    ble rapportert som `db_utilgjengelig`. Nøyaktig den feilklassifiseringen
    valideringen skulle fjerne.

    MUTASJONEN SOM DREPER DENNE: sammenlign ikke den parsede kanoniske teksten
    med den innsendte i _ingest_kvittering."""
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep, gen, kts = _last_opp_artefakt(
        migrator, klient, token)
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    hh = {"authorization": f"Bearer {tok2}"}
    kjti = _kvitteringskap(opp, oc, gen)        # SAMME kapabilitet hele veien
    varianter = [f"urn:uuid:{aid}", "{" + aid + "}", aid.replace("-", "")]
    if aid.upper() != aid:                      # en uuid uten bokstaver er lik
        varianter.append(aid.upper())
    for variant in varianter:
        kv = _signer_kvittering(
            _kvitteringskropp(opp, kjti, rep, oc, gen, variant, kts))
        rk = klient.post("/v1/oppdrag/kvittering", json=kv, headers=hh)
        assert rk.status_code == 400 and "request_feilformet" in rk.text, \
            f"ikke-kanonisk artefakt_id ({variant}) ble ikke avvist: {rk.text}"
    # Avvisningen skjer FØR forbruket: den kanoniske formen — den serveren selv
    # returnerte — går fortsatt gjennom på den samme kapabiliteten.
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid, kts))
    assert klient.post("/v1/oppdrag/kvittering", json=kv,
                       headers=hh).status_code == 200


@pg
def test_kvittering_uten_signert_hash_er_request_feil(migrator, klient, token):
    """Codex P1: en kvittering som peker på et artefakt MÅ bære den attesterte
    klartekst-hashen. Uten den er signaturen bare en påstand om HVILKET artefakt
    som gjelder, aldri om HVA det inneholder — og porten avviser den som
    feilformet FØR kapabiliteten forbrukes.

    MUTASJONEN SOM DREPER DENNE: fjern kravet om `klartekst_sha256` i
    _ingest_kvittering."""
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep, gen, kts = _last_opp_artefakt(
        migrator, klient, token)
    kjti = _kvitteringskap(opp, oc, gen)
    # kts utelatt → ingen attestert hash i den signerte kroppen.
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid))
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 400 and "request_feilformet" in rk.text, rk.text
    # Kapabiliteten er IKKE forbrukt: den samme controlleren kan levere en
    # korrekt kvittering etterpå.
    kv2 = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid, kts))
    rk2 = klient.post("/v1/oppdrag/kvittering", json=kv2,
                      headers={"authorization": f"Bearer {tok2}"})
    assert rk2.status_code == 200, rk2.text


@pg
def test_kvittering_med_feil_attestert_hash_avvises(migrator, klient, token):
    """Codex P1: den SIGNERTE hashen sammenlignes med artefaktets lagrede hash.

    Tidligere leste promoteringen `klartekst_sha256` fra artefaktraden og sendte
    den samme verdien tilbake til `promoter_artefakt` — sammenligningen var en
    tautologi som ALLTID holdt. Da kunne den som holdt opplastingstokenet og
    kapabilitets-jti-en lastet opp en vilkårlig rapport og fått den promotert
    som attestert evidens. Nå: hashavvik → ingen promotering, oppdraget står,
    artefaktet karantenesettes.

    MUTASJONEN SOM DREPER DENNE: send artefaktets egen hash til promoter_artefakt
    igjen i stedet for den signerte."""
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep, gen, kts = _last_opp_artefakt(
        migrator, klient, token)
    kjti = _kvitteringskap(opp, oc, gen)
    # Velformet, men FEIL hash — signeren attesterer et annet innhold enn det
    # som faktisk ble lastet opp.
    feil = "".join("0" if c != "0" else "1" for c in kts)
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid, feil))
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 409, rk.text
    _sett_kontekst(migrator, TENANT)
    art_st = migrator.execute("SELECT tilstand FROM artefakt WHERE artefakt_id=%s",
                              (aid,)).fetchone()[0]
    opp_st = migrator.execute("SELECT status FROM oppdrag WHERE tenant=%s AND id=%s",
                              (TENANT, opp)).fetchone()[0]
    migrator.rollback()
    assert (art_st, opp_st) == ("karantene", "plukket"), \
        "en kvittering med feil attestert hash promoterte artefaktet likevel"


@pg
def test_kvittering_gjerdes_ut_av_ny_module_epoch(migrator, klient, token):
    """Codex: promoteringen sammenlignes mot modulens GJELDENDE epoch. Nødstoppes
    modulen etter claim+opplasting (modulhode.module_epoch++), skal en kvittering
    fra den utgjerdede controlleren IKKE promotere/avslutte — artefaktet
    karantenesettes.

    MUTASJONEN SOM DREPER DENNE: sammenlign kun mot oppdragets stemplede epoch."""
    from .test_m37 import _signer_kvittering
    from .test_pr014b_artefaktkapabilitet import _mk_admin
    opp, modul, kh, aid, oc, rep, gen, kts = _last_opp_artefakt(
        migrator, klient, token)
    ma = _mk_admin("disponit_modules_admin")
    try:
        ma.execute("SELECT noddeaktiver_modul(%s,'nodstopp','sys')", (modul,))
        ma.commit()
    finally:
        ma.close()
    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid, kts))
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 409, rk.text
    _sett_kontekst(migrator, TENANT)
    art_st = migrator.execute("SELECT tilstand FROM artefakt WHERE artefakt_id=%s",
                              (aid,)).fetchone()[0]
    opp_st = migrator.execute("SELECT status FROM oppdrag WHERE tenant=%s AND id=%s",
                              (TENANT, opp)).fetchone()[0]
    migrator.rollback()
    assert (art_st, opp_st) == ("karantene", "plukket"), \
        "en nodstoppet controllers kvittering promoterte artefaktet likevel"


@pg
def test_upload_ensom_surrogat_er_request_feil(migrator, klient, token):
    """Codex: en escaped ensom surrogate ("\\ud800") slipper gjennom json.loads,
    men kanoniseringen kaster UnicodeEncodeError. Handleren fanget kun
    Ikkekanoniserbar → 500. Nå: request_feilformet (400).

    MUTASJONEN SOM DREPER DENNE: fjern UnicodeEncodeError fra except-en."""
    import json as _json
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    jti = _utsted_cap(opp, modul, kh, at)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))
    kropp = _json.dumps({"kapabilitet_jti": jti, "rapport": {"x": "\ud800"}})
    r = klient.post("/v1/artefakt", content=kropp,
                    headers={"authorization": f"Bearer {tok}",
                             "content-type": "application/json"})
    assert r.status_code == 400 and "request_feilformet" in r.text, r.text


@pg
@pytest.mark.parametrize("dybde,hvor", [
    (300, "kanoniseringen (jcs.MAKS_DYBDE)"),
    (5000, "json.loads (Pythons rekursjonsgrense)"),
])
def test_upload_dypt_nostet_rapport_er_request_feil(migrator, klient, token,
                                                    dybde, hvor):
    """Codex P2: BÅDE `json.loads` og JCS-serialisereren er rekursive. En
    syntaktisk gyldig, dypt nøstet rapport på noen få kilobyte traff derfor
    rekursjonsgrensen som RecursionError — en RuntimeError, ikke en ValueError
    eller Ikkekanoniserbar — og slapp ut som generisk 500 i stedet for det
    dokumenterte `request_feilformet`. Nøsting er klientinput, ikke en
    driftshendelse.

    Kroppen bygges som TEKST: `json.dumps` av en 5 000-dyp struktur ville tatt
    livet av testen selv, ikke serveren.

    MUTASJONEN SOM DREPER DENNE: fjern RecursionError fra except-ene i
    `_artefakt_upload` (og `jcs.MAKS_DYBDE`)."""
    import json as _json
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    jti = _utsted_cap(opp, modul, kh, at)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))
    dyp = '{"a":' * dybde + "1" + "}" * dybde
    kropp = '{"kapabilitet_jti":%s,"rapport":%s}' % (_json.dumps(jti), dyp)
    assert len(kropp) < 100_000, "testkroppen skal være liten, ikke stor"
    r = klient.post("/v1/artefakt", content=kropp,
                    headers={"authorization": f"Bearer {tok}",
                             "content-type": "application/json"})
    assert r.status_code == 400 and "request_feilformet" in r.text, \
        f"dyp nøsting avvist feil i {hvor}: {r.status_code} {r.text}"
    # Kapabiliteten er IKKE forbrukt — den legitime opplastingen går fortsatt.
    assert _post(klient, tok, jti, {"funn": 1}).status_code == 200


@pg
def test_sen_kvittering_fremmed_artefakt_avvises(migrator, klient, token):
    """Codex: en SEN kvittering valideres som promoteringsveien (tenant/oppdrag/
    signert hash). Et fremmed artefakt (eller feil hash) → sikkerhetskonflikt
    (409), IKKE akseptert som sen evidens (202) med en payload som ikke kan
    verifiseres.

    MUTASJONEN SOM DREPER DENNE: kall bevar_artefakt uten validering på den sene
    veien (no-op for fremmed artefakt → falsk 202)."""
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep, gen, kts = _last_opp_artefakt(
        migrator, klient, token)
    _, _, _, fremmed, _, _, _, kts2 = _last_opp_artefakt(migrator, klient, token)
    kjti = _kvitteringskap(opp, oc, gen)   # utstedes FØR generasjonsbumpen
    # Foreldet eier → sen vei (lagres uten statusendring).
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE oppdrag SET owner_generation=owner_generation+1"
                     " WHERE tenant=%s AND id=%s", (TENANT, opp)); migrator.commit()
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, fremmed, kts2))
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 409, rk.text


def _menneskelig_kansellert(migrator, opp):
    """Nøyaktig de to hoppene `avvis_med_opplosning` gjør: fencing (plukket →
    opprettet, generasjonsbump, eierbindingen fjernet) og så kanselleringen med
    menneskets årsak. Etterpå er en kvittering fra den gamle eieren SEN."""
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "UPDATE oppdrag SET status='opprettet', owner_claim_id=NULL,"
        " owner_generation=owner_generation+1, owner_lease_utloper=NULL"
        " WHERE tenant=%s AND id=%s", (TENANT, opp))
    migrator.execute(
        "UPDATE oppdrag SET status='kansellert',"
        " kansellert_aarsak='menneskelig_avvis' WHERE tenant=%s AND id=%s",
        (TENANT, opp))
    migrator.commit()


def _artefakttilstand(migrator, aid):
    _sett_kontekst(migrator, TENANT)
    st = migrator.execute("SELECT tilstand FROM artefakt WHERE artefakt_id=%s",
                          (aid,)).fetchone()[0]
    migrator.rollback()
    return st


@pg
@pytest.mark.parametrize("rev,ventet", [("direkte", "staged"),
                                        ("kompenserende", "bevart")])
def test_sen_kvittering_bevarer_ikke_artefakt_for_direkte(migrator, klient,
                                                          token, rev, ventet):
    """043 §5 (Codex P2, runde 2): på et oppdrag mennesket kansellerte utleder
    veien av MODULKONTRAKTENS reversibilitet hva den sene kvitteringen krever.
    `direkte` betyr at resultatet forkastes og artefaktet ryddes av
    038-reaperen — men `bevar_artefakt` ble kalt FØR oppslaget, og `bevart` er
    retained og terminalt: oppryddingen rører det aldri igjen. Artefaktet ble
    altså liggende for alltid på nøyaktig den veien som lovte det motsatte.

    Kjøres gjennom INNTAKSVEIEN (signert kvittering over HTTP), ikke bare
    DB-funksjonen — det var der forrige rundes port bommet.

    MUTASJONEN SOM DREPER DENNE: flytt reversibilitetsoppslaget tilbake under
    bevaringen, eller la `direkte` også kalle `bevar_artefakt`."""
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep, gen, kts = _last_opp_artefakt(
        migrator, klient, token, rev)
    kjti = _kvitteringskap(opp, oc, gen)   # utstedes FØR fencingen
    _menneskelig_kansellert(migrator, opp)
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid, kts))
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 202, rk.text
    assert rk.json()["status"] == "lagret_uten_statusendring", rk.text
    # Evidensen er lagret uansett; det er ARTEFAKTETS skjebne som skiller.
    assert _artefakttilstand(migrator, aid) == ventet, rev


@pg
def test_sen_kvittering_direkte_validerer_fortsatt_artefaktet(migrator, klient,
                                                              token):
    """... og bevaringen som utelates er BARE skrivingen: en sen kvittering som
    navngir et FREMMED artefakt skal falle like hardt på `direkte`-veien som på
    den bevarende. Det er artefaktet som skal ryddes, ikke porten.

    MUTASJONEN SOM DREPER DENNE: hopp over artefaktoppslaget helt når
    reversibiliteten er `direkte` (fremmed artefakt → falsk 202)."""
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep, gen, kts = _last_opp_artefakt(
        migrator, klient, token, "direkte")
    _, _, _, fremmed, _, _, _, kts2 = _last_opp_artefakt(migrator, klient, token)
    kjti = _kvitteringskap(opp, oc, gen)
    _menneskelig_kansellert(migrator, opp)
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen,
                                              fremmed, kts2))
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 409, rk.text
    # Det fremmede artefaktet er hverken bevart eller rørt av dette oppdraget.
    assert _artefakttilstand(migrator, fremmed) == "staged"


def _vinner_avviser_og_holder_laasen(sak, opp, kjti, aid, vinnerhash):
    """En transaksjon som gjør NØYAKTIG det VINNEREN av et kappløp gjør når
    kvitteringen AVVISES: skriver sikkerhetssaken `artefakt_ikke_verifisert` og
    brenner kapabiliteten med sin resultathash — og holder igjen radlåsen.

    Taperen (identisk kvittering) blokkerer inne i `bruk_kvitteringskapabilitet`
    og får først svar etter commiten. Dette er ikke en timing-test: blokkerer
    taperen, klassifiseres den mot vinnerens committede rad; kommer den etterpå,
    mot nøyaktig den samme raden. Begge interleavinger skal gi samme utfall — det
    er hele funnet."""
    import json as _json
    from db.pg import koble
    from .test_api import MIGRATOR_DSN
    h = koble(MIGRATOR_DSN)
    h.execute("SELECT set_config('disponit.tenant',%s,true),"
              "       set_config('disponit.aktor','vinner',true),"
              "       set_config('disponit.request_id','vinner',true)",
              (TENANT,))
    h.execute(
        "INSERT INTO unntak_historikk (tenant, unntak_id, hendelse, aktor,"
        " request_id, detalj) VALUES (%s,%s,'artefakt_ikke_verifisert',"
        " 'kvitteringsport','vinner',%s)",
        (TENANT, sak, _json.dumps({"oppdrag_id": opp, "artefakt_id": str(aid)})))
    h.execute("SET LOCAL ROLE disponit_m37_claimer")
    h.execute("UPDATE kvitteringskapabiliteter SET status='brukt',"
              " resultathash=%s, brukt_ts=now() WHERE jti=%s",
              (vinnerhash, kjti))
    h.execute("RESET ROLE")
    return h                       # IKKE committet — låsen holdes


@pg
def test_samtidig_avvist_kvittering_gir_ogsaa_409(migrator, klient, token):
    """Codex: den PERSISTERTE avvisningen må rekonstrueres også på KAPPLØPS-
    veien. Vinneren skriver `artefakt_ikke_verifisert` og svarer 409; taperen
    blokkerer inne i `bruk_kvitteringskapabilitet`, våkner med `idempotent` og
    returnerte 200 uten å konsultere det committede utfallet. Nøyaktig samme
    avviste kvittering fikk altså 409 eller 200 avhengig av timing alene.

    MUTASJONEN SOM DREPER DENNE: fjern `_kvittering_alt_avvist`-oppslaget fra
    `idempotent`-grenen i `_forbruk_kapabilitet`."""
    import threading
    import time
    from api.app import _resultathash
    from .test_m37 import _signer_kvittering

    opp, modul, kh, aid, oc, rep, gen, kts = _last_opp_artefakt(
        migrator, klient, token)
    _sett_kontekst(migrator, TENANT)
    sak = migrator.execute("SELECT unntak_id FROM oppdrag WHERE tenant=%s"
                           " AND id=%s", (TENANT, opp)).fetchone()[0]
    migrator.rollback()
    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid, kts))
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    hh = {"authorization": f"Bearer {tok2}"}

    holder = _vinner_avviser_og_holder_laasen(sak, opp, kjti, aid,
                                              _resultathash(kv))
    svar = {}

    def taper():
        svar["r"] = klient.post("/v1/oppdrag/kvittering", json=kv, headers=hh)

    t = threading.Thread(target=taper)
    t.start()
    try:
        time.sleep(0.5)              # la taperen rekke fram til låsen
    finally:
        holder.commit()
        holder.close()
    t.join(timeout=30)
    assert not t.is_alive(), "taperen hang på låsen"
    r = svar["r"]
    assert r.status_code == 409, \
        "kappløps-taperen rapporterte en AVVIST kvittering som suksess: " + r.text


@pg
def test_ugyldig_kapabilitet_i_kapplop_er_ikke_driftsfeil(migrator, klient, token,
                                                          monkeypatch):
    """Codex: blir opplastingskapabiliteten ugyldig ETTER at
    `innlos_artefaktkapabilitet` leste den, men FØR `lagre_artefakt_staged` tar
    radlåsen og validerer den på nytt, reiser DB-en `invalid_parameter_value`.
    Catch-all-en klassifiserte det som `db_utilgjengelig` (503, drift) — altså en
    basefeil med tilhørende retry-atferd og overvåkingsstøy for en helt normal
    grensetilstand på en kortlevd kapabilitet. Skal være `kapabilitet_ugyldig`.

    Vinduet gjenskapes deterministisk ved å ugyldiggjøre kapabiliteten inne i
    krypteringen — som kalles nettopp mellom innløsningen og staged-writen.
    `utloper` er uforanderlig (statusmaskinen), så invalideringen gjøres via den
    lovlige overgangen `utstedt → feilet`; DB-en avviser på NØYAKTIG samme RAISE.

    MUTASJONEN SOM DREPER DENNE: fjern InvalidParameterValue-grenen i
    `_artefakt_upload`."""
    from db import kryptering
    from db.pg import koble
    from .test_api import MIGRATOR_DSN

    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    jti = _utsted_cap(opp, modul, kh, at)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))

    ekte = kryptering.krypter

    def ugyldiggjor_midt_i(*a, **kw):
        c = koble(MIGRATOR_DSN)
        try:
            c.execute("UPDATE artefaktkapabilitet SET status='feilet'"
                      " WHERE jti=%s", (jti,))
            c.commit()
        finally:
            c.close()
        return ekte(*a, **kw)

    monkeypatch.setattr(kryptering, "krypter", ugyldiggjor_midt_i)
    r = _post(klient, tok, jti, {"a": 1})
    assert r.status_code == 401 and "kapabilitet_ugyldig" in r.text, \
        "en ugyldig kapabilitet ble rapportert som driftsfeil: " + r.text
