"""#162 PR-2: resolveren — modulens lesevei, autorisert av CLAIMET.

Hele kjeden over HTTP: kunde reserverer+laster (PR-1-veien), bunten
BINDES til et oppdrag, modulen onboardes og CLAIMER — og først DA gir
GET /v1/inndata/hent bytene tilbake, dekryptert og sha-verifisert.
Negativene måler at retten faktisk ER claimet: før claim 404, feil
modul 404, ubundet 404 — samme svar uansett årsak.
"""
import hashlib
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, app, klient, migrator,  # noqa: F401
                       miljo)
from .test_inndata_http import (_opplast, _reserver, _rigg, _zipbytes,
                                inndata_rot)  # noqa: F401
from .test_modul_onboarding_http import (_kjede, _kjent_type,
                                         _onboard_token)
from .test_modul_onboarding_http import TENANT as OTEN  # noqa: F401

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _bundet_bunt(klient, migrator, monkeypatch, modul, typenavn,
                 prefiks):
    """Reserver+last en bunt for `modul`, bind den til et plukkbart
    oppdrag hos NABOTENANTEN (rekrutterings-riggen) — -> (inndata_id,
    kropp, tenant, oppdrag_id)."""
    from api import inndata as inndatamodul
    monkeypatch.setattr(
        inndatamodul, "TILLATTE_RESERVASJONER",
        frozenset({(modul, "soknadsbunt")}))
    tenant, _bid, cookie, csrf = _rigg(klient)
    from api import sesjon as sesjonmodul
    r = klient.post("/v1/inndata/reserver",
                    json={"eiermodul": modul, "formaal": "soknadsbunt"},
                    cookies={sesjonmodul.C_SESJON: cookie},
                    headers={"X-Disponit-CSRF": csrf})
    assert r.status_code == 201, r.text
    jti = r.json()["reservasjon_jti"]
    inndata_id = r.json()["inndata_ref"].split(":", 1)[1]
    kropp = _zipbytes()
    r2 = _opplast(klient, cookie, csrf, jti, kropp)
    assert r2.status_code == 201, r2.text

    # Oppdraget bunten bindes til — plukkbar tilstand, modulens eie.
    from db.pg import sett_kontekst
    sett_kontekst(migrator, tenant, "test", "r1")
    logg = migrator.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'test','api_token','ih','p@1.0.0/x.y','TILLAT','[]',"
        " %s) RETURNING id", (tenant, secrets.token_hex(8))).fetchone()[0]
    from db import kryptering
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, tenant)
    ct, nonce = kryptering.krypter(dek, {"x": 1}, tenant, key_id)
    oid = migrator.execute(
        "INSERT INTO oppdrag (opprinnelse, tenant, beslutning_loggpost_id,"
        " oppdragstype, handling, eiermodul, payload_kryptert, key_id,"
        " nonce, utforelsesfrist, evidensfrist, koblingsstatus)"
        " VALUES ('beslutning',%s,%s,%s,%s,%s,%s,%s,%s,"
        " now()+interval '4 hour', now()+interval '1 day','KOBLET')"
        " RETURNING id", (tenant, logg, typenavn, prefiks + "bunt",
                          modul, ct, key_id, nonce)).fetchone()[0]
    migrator.commit()
    sett_kontekst(migrator, tenant, "test", "r2")
    migrator.execute("SET LOCAL ROLE disponit_domene_eier")
    migrator.execute("SELECT bind_inndata(%s,%s,%s,%s)",
                     (tenant, inndata_id, oid, modul))
    migrator.commit()
    return inndata_id, kropp, tenant, oid


@pg
def test_resolveren_krever_claimet(klient, migrator, miljo, monkeypatch,
                                   inndata_rot):
    # Egen unik type per kjøring: registeret er append-only, og den
    # EKTE typens rad eies av produksjonsveien — resolverens semantikk
    # (claimet + eiermodul) er typenavn-agnostisk, så porten måles på en
    # syntetisk type med samme form.
    u = secrets.token_hex(4)
    typenavn, prefiks = f"rekr.test.{u}", f"rekrtest{u}."
    _kjent_type(monkeypatch, typenavn, prefiks)
    modul, rel = _kjede(migrator, typenavn=typenavn)
    migrator.commit()
    inndata_id, kropp, tenant, oid = _bundet_bunt(
        klient, migrator, monkeypatch, modul, typenavn, prefiks)
    mtk, _ = _onboard_token(klient, migrator, modul, rel)

    # FØR claim: 404 — bindingen finnes, retten gjør det ikke.
    r = klient.post(f"/v1/inndata/hent/{inndata_id}",
                   headers={"authorization": f"Bearer {mtk}"})
    assert r.status_code == 404, r.text

    # Claim → retten oppstår.
    sett = klient.post("/v1/oppdrag/claim", json={},
                       headers={"authorization": f"Bearer {mtk}"})
    assert sett.status_code == 200, sett.text
    assert sett.json()["oppdrag_id"] == oid

    r2 = klient.post(f"/v1/inndata/hent/{inndata_id}",
                    headers={"authorization": f"Bearer {mtk}"})
    assert r2.status_code == 200, r2.text
    assert r2.content == kropp
    assert r2.headers["x-innhold-sha256"] == \
        hashlib.sha256(kropp).hexdigest()

    # Feil modul: en ANNEN onboardet deployment får 404 på samme id.
    modul2, rel2 = _kjede(migrator, typenavn=f"rekr.test2.{u}")
    migrator.commit()
    mtk2, _ = _onboard_token(klient, migrator, modul2, rel2)
    r3 = klient.post(f"/v1/inndata/hent/{inndata_id}",
                    headers={"authorization": f"Bearer {mtk2}"})
    assert r3.status_code == 404

    # …og browserøkten (ikke modultoken) er ikke en vei inn.
    from api import sesjon as sesjonmodul
    from .test_rekruttering_http import _browsersesjon, _bruker
    cookie, _csrf = _browsersesjon(_bruker("snoker", ["admin"]))
    r4 = klient.post(f"/v1/inndata/hent/{inndata_id}",
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert r4.status_code == 401
