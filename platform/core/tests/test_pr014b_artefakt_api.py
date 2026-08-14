"""PR-014b CP5 (del 2): POST /v1/artefakt — opplastingsendepunktet."""
import secrets

import pytest

from .test_api import DSN, TENANT, migrator, miljo, token, klient, app  # noqa: F401
from .test_m37 import _sett_kontekst
from .test_pr014b_artefaktkapabilitet import _plukket_oppdrag_med_binding

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


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


def _last_opp_artefakt(migrator, klient, token):
    """Bygg bundet, plukket oppdrag + last opp et staged artefakt. Returnerer
    (opp, modul, kh, artefakt_id, owner_claim, repair_operation_id, gen)."""
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    oc, rep, gen = _oppdrag_owner(migrator, opp)
    ajti = _utsted_cap(opp, modul, kh, at)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))
    aid = _post(klient, tok, ajti, {"funn": 1}).json()["artefakt_id"]
    return opp, modul, kh, aid, oc, rep, gen


def _kvitteringskropp(opp, kjti, rep, oc, gen, aid):
    return {"oppdrag_id": opp, "tenant": TENANT, "kvittering_jti": kjti,
            "repair_operation_id": rep, "owner_claim_id": oc,
            "owner_generation": gen, "resultat": "utfort",
            "ressurs_id": "fak-1", "artefakt_id": aid}


@pg
def test_kvittering_promoterer_artefakt(migrator, klient, token):
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep, gen = _last_opp_artefakt(migrator, klient, token)
    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid))
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
    opp, modul, kh, aid, oc, rep, gen = _last_opp_artefakt(migrator, klient, token)
    # et ANNET oppdrag + artefakt (aid2 hører til opp2, ikke opp).
    _, _, _, aid2, _, _, _ = _last_opp_artefakt(migrator, klient, token)
    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid2))
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
    opp, modul, kh, aid, oc, rep, gen = _last_opp_artefakt(migrator, klient, token)
    kjti = _kvitteringskap(opp, oc, gen)
    # Foreldet eier: kapabiliteten er utstedt for generasjon `gen`, mens
    # oppdraget nå står på gen+1. Kvitteringen er fortsatt gyldig EVIDENS.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE oppdrag SET owner_generation=owner_generation+1"
                     " WHERE tenant=%s AND id=%s", (TENANT, opp))
    migrator.commit()
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid))
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
    """Nok en opplasting på SAMME (fortsatt plukkede) oppdrag."""
    at = migrator.execute("SELECT artefakttype FROM artefakttype_register"
                          " WHERE eiermodul=%s AND kontrakt_hash=%s",
                          (modul, kh)).fetchone()[0]
    migrator.rollback()
    jti = _utsted_cap(opp, modul, kh, at)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))
    return _post(klient, tok, jti, {"funn": 2}).json()["artefakt_id"]


@pg
def test_motstridende_kvittering_karantenesetter_artefaktet(migrator, klient, token):
    # Samme bevaringsregel på konfliktveien: sikkerhetssaken er skrevet, og
    # artefaktet det andre resultatet påberoper seg er nettopp det
    # etterforskningen trenger. Karantene ryddes aldri.
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep, gen = _last_opp_artefakt(migrator, klient, token)
    # BEGGE opplastingene skjer mens oppdraget står plukket (kapabiliteter
    # utstedes kun da).
    aid2 = _en_til_opplasting(migrator, klient, token, opp, modul, kh)
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    h = {"authorization": f"Bearer {tok2}"}
    kjti = _kvitteringskap(opp, oc, gen)
    kv1 = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid))
    assert klient.post("/v1/oppdrag/kvittering", json=kv1,
                       headers=h).status_code == 200
    # Samme kapabilitet, ANNET resultat + annet artefakt → motstrid (409).
    kropp = _kvitteringskropp(opp, kjti, rep, oc, gen, aid2)
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
    opp, modul, kh, aid, oc, rep, gen = _last_opp_artefakt(migrator, klient, token)
    _, _, _, fremmed, _, _, _ = _last_opp_artefakt(migrator, klient, token)
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    h = {"authorization": f"Bearer {tok2}"}
    kjti = _kvitteringskap(opp, oc, gen)
    # 1) FREMMED artefakt → promotering feiler → 409 + karantene.
    kv1 = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, fremmed))
    assert klient.post("/v1/oppdrag/kvittering", json=kv1,
                       headers=h).status_code == 409
    # Brenningen + resultathashen skal ha OVERLEVD (ikke rullet med promoteringen).
    # Måles ATFERDSMESSIG (kvitteringskapabiliteter er off-limits for migrator):
    # 2) SAMME jti, gyldig eget artefakt, ANNET resultat. Er brenningen intakt,
    #    står jti-en 'brukt' med første hash → motstrid (409). Var den rullet
    #    tilbake, ville re-posten vært fersk og promotert aid → 200.
    kropp = _kvitteringskropp(opp, kjti, rep, oc, gen, aid)
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
    opp, modul, kh, aid, oc, rep, gen = _last_opp_artefakt(migrator, klient, token)
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
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid))
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
    opp, modul, kh, aid, oc, rep, gen = _last_opp_artefakt(migrator, klient, token)
    _, _, _, fremmed, _, _, _ = _last_opp_artefakt(migrator, klient, token)
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    hh = {"authorization": f"Bearer {tok2}"}
    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, fremmed))
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
    opp, modul, kh, aid, oc, rep, gen = _last_opp_artefakt(migrator, klient, token)
    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, "ikke-en-uuid"))
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 400 and "request_feilformet" in rk.text, rk.text


@pg
def test_kvittering_gjerdes_ut_av_ny_module_epoch(migrator, klient, token):
    """Codex: promoteringen sammenlignes mot modulens GJELDENDE epoch. Nødstoppes
    modulen etter claim+opplasting (modulhode.module_epoch++), skal en kvittering
    fra den utgjerdede controlleren IKKE promotere/avslutte — artefaktet
    karantenesettes.

    MUTASJONEN SOM DREPER DENNE: sammenlign kun mot oppdragets stemplede epoch."""
    from .test_m37 import _signer_kvittering
    from .test_pr014b_artefaktkapabilitet import _mk_admin
    opp, modul, kh, aid, oc, rep, gen = _last_opp_artefakt(migrator, klient, token)
    ma = _mk_admin("disponit_modules_admin")
    try:
        ma.execute("SELECT noddeaktiver_modul(%s,'nodstopp','sys')", (modul,))
        ma.commit()
    finally:
        ma.close()
    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid))
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
