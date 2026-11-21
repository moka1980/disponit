"""Porten for felles postboks (eiervedtak 20/11): en kilde med delescopet
leses og besvares gjennom POSTBOKSEN SOM STÅR PÅ KILDEN, ikke gjennom
innloggerens egen (`/me`).

  1. Innhentingen går mot `/users/<adresse>` — delta, og kroppen per
     melding. En cursor fra den gamle roten (`/me`) følges ikke.
  2. Svaret går ut som reply fra samme postboks.
  3. En kilde UTEN delescopet er urørt: `/me`, som før.

MUTASJONEN SOM DREPER DENNE: la `graph_rot` alltid svare `/me`, eller la
`hent_en` følge en lagret cursor uansett rot.
"""
import secrets

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_m37 import _sett_kontekst
from .test_m6_inntak_port import (KROPP, _kilde, _m365, _melding, _pa,
                                  _veksler, pg_plan)

def _gjor_felles(m, kid, *, delta=None) -> str:
    """Gir kilden delescopene, slik et nytt samtykke gjør. -> postboksen.

    ADRESSEN RØRES IKKE: identitetskolonnene på en kilde er immutable
    (`epost_kilde_vakt`) — en annen postboks er en NY kilde. Det som
    endrer seg ved et nytt samtykke, er scopet."""
    from api.epost_kilde import M365_SCOPE
    _sett_kontekst(m, TENANT)
    m.execute("UPDATE epost_kilde SET scope=%s, delta_token=%s"
              " WHERE tenant=%s AND kilde_id=%s",
              (M365_SCOPE, delta, TENANT, kid))
    postboks = m.execute(
        "SELECT postboks FROM epost_kilde WHERE tenant=%s AND kilde_id=%s",
        (TENANT, kid)).fetchone()[0]
    m.commit()
    return postboks


def _graf(sider, kall):
    def graf(access, url, *, tekstkropp=False):
        kall.append(url)
        if "/messages/" in url and "/delta" not in url:
            return {"body": {"contentType": "text", "content": KROPP}}
        return sider.pop(0) if sider else {"value": [],
                                           "@odata.deltaLink": "x"}
    return graf


@pg_plan
def test_felles_postboks_leses_fra_adressen_paa_kilden(migrator, miljo, app,
                                                      monkeypatch):
    from plan.epost import GRAPH, kjor_en_runde
    _m365(monkeypatch)
    kid, _key, _dek = _kilde(migrator)
    # En cursor fra FØR kilden ble felles peker inn i en annen innboks.
    gammel = GRAPH + "/me/mailFolders/inbox/messages/delta?$deltatoken=gml"
    FELLES = _gjor_felles(migrator, kid, delta=gammel)
    rot = GRAPH + "/users/" + FELLES
    ny = rot + "/mailFolders/inbox/messages/delta?$deltatoken=ny"
    kall = []
    pa = _pa()
    try:
        res = kjor_en_runde(
            app.tjeneste, pa, veksler=_veksler({}),
            graf=_graf([{"value": [_melding(1)], "@odata.deltaLink": ny}],
                       kall))
    finally:
        pa.close()
    mine = [r for r in res["resultater"] if r["kilde"] == str(kid)[:8]]
    assert mine and mine[0]["nye"] == 1, res
    # Andre kilder i basen kan være med i samme runde; her måles bare
    # kallene som gjelder DENNE postboksen.
    assert gammel not in kall, "cursoren fra den gamle roten ble fulgt"
    egne = [u for u in kall if FELLES in u]
    assert egne and all(u.startswith(rot + "/") for u in egne), kall
    assert any(u.startswith(rot + "/mailFolders/inbox/messages/delta")
               for u in kall), "delta gikk ikke mot den felles postboksen"
    assert any(u.startswith(rot + "/messages/") for u in kall), \
        "kroppen ble ikke hentet fra den felles postboksen"
    _sett_kontekst(migrator, TENANT)
    lagret = migrator.execute(
        "SELECT delta_token FROM epost_kilde WHERE tenant=%s AND kilde_id=%s",
        (TENANT, kid)).fetchone()[0]
    migrator.rollback()
    assert lagret == ny


@pg_plan
def test_svaret_gaar_fra_den_felles_postboksen(migrator, miljo, app, klient,
                                               token, monkeypatch):
    from plan.epost import GRAPH, send_runde

    from .test_m6_utsending_port import _i_ko
    from .test_m6_utsending_port import _veksler as _sendeveksler
    _m365(monkeypatch)
    kid, _mid, uid, _tok = _i_ko(klient, migrator, token)
    FELLES = _gjor_felles(migrator, kid)
    kall = []
    pa = _pa()
    try:
        res = send_runde(app.tjeneste, pa, veksler=_sendeveksler(),
                         poster=lambda a, url, kropp: kall.append(url))
    finally:
        pa.close()
    mine = [r for r in res["resultater"] if r["utkast"] == str(uid)[:8]]
    assert mine and mine[0].get("sendt") is True, res
    assert len(kall) == 1
    assert kall[0].startswith(GRAPH + "/users/" + FELLES + "/messages/") \
        and kall[0].endswith("/reply"), kall


@pg_plan
def test_en_kilde_uten_delescopet_er_urort(migrator, miljo, app,
                                          monkeypatch):
    from plan.epost import GRAPH, kjor_en_runde
    _m365(monkeypatch)
    kid, _key, _dek = _kilde(migrator)
    kall = []
    pa = _pa()
    try:
        kjor_en_runde(app.tjeneste, pa, veksler=_veksler({}),
                      graf=_graf([{"value": [_melding(2)],
                                   "@odata.deltaLink": "x"}], kall))
    finally:
        pa.close()
    _sett_kontekst(migrator, TENANT)
    postboks = migrator.execute(
        "SELECT postboks FROM epost_kilde WHERE tenant=%s AND kilde_id=%s",
        (TENANT, kid)).fetchone()[0]
    migrator.rollback()
    assert not [u for u in kall if postboks in u], \
        "en kilde uten delescopet ble lest gjennom /users"
    assert any(u.startswith(GRAPH + "/me/mailFolders/inbox/messages/delta")
               for u in kall)


@pg_plan
def test_det_fornyede_refresh_tokenet_lagres_paa_kilden(migrator, miljo, app,
                                                       monkeypatch, capsys):
    """TIDSBOMBEN (funnet 21.11.2026): Microsoft gir et NYTT refresh-token
    ved hver fornyelse, og det gamle lever i 90 dager fra det ble utstedt.
    Vi kastet det nye — hver postboks ville sluttet å hentes 90 dager etter
    tilkoblingen.

    MUTASJONEN SOM DREPER DENNE: fjern kallet til `_lagre_fornyet_refresh`,
    eller la det skrive uten å committe.
    """
    from db import kryptering
    from plan.epost import kjor_en_runde

    from .test_m6_inntak_port import REFRESH
    _m365(monkeypatch)
    kid, _key, dek = _kilde(migrator)
    nytt = "rotert-" + secrets.token_hex(8)
    sett_refresh = []

    def veksler(konfig, refresh):
        sett_refresh.append(refresh)
        return {"access_token": "kortlivet-" + secrets.token_hex(4),
                "refresh_token": nytt}

    def lagret():
        _sett_kontekst(migrator, TENANT)
        ct, nonce, key_id = migrator.execute(
            "SELECT auth_kryptert, nonce, key_id FROM epost_kilde"
            " WHERE tenant=%s AND kilde_id=%s", (TENANT, kid)).fetchone()
        migrator.rollback()
        return kryptering.dekrypter(dek, bytes(ct), bytes(nonce), TENANT,
                                    key_id)["refresh_token"]

    assert lagret() == REFRESH
    pa = _pa()
    try:
        res = kjor_en_runde(app.tjeneste, pa, veksler=veksler,
                            graf=_graf([{"value": [_melding(3)],
                                         "@odata.deltaLink": "x"}], []))
        mine = [r for r in res["resultater"] if r["kilde"] == str(kid)[:8]]
        # Runden fullførte ETTER commit-en inni tokenutleveringen: konteksten
        # ble satt tilbake, og meldingen ble lagret.
        assert mine and mine[0]["nye"] == 1, res
        assert lagret() == nytt, "det fornyede tokenet ble ikke lagret"
        # Neste runde bruker det NYE tokenet.
        sett_refresh.clear()
        kjor_en_runde(app.tjeneste, pa, veksler=veksler, graf=_graf([], []))
        assert nytt in sett_refresh, sett_refresh
    finally:
        pa.close()
    logg = capsys.readouterr().out
    assert "m6_refresh_ikke_lagret" not in logg
    assert nytt not in logg and REFRESH not in logg, "token i loggen"
