"""Porten for eksporten: kunden kan ta med seg det hun har lagt inn.

  1. Én JSON-fil, lastet ned (content-disposition), med firmaets moduler
     lest gjennom modulenes egne lesedører.
  2. Hver modul krever OGSÅ modulens eget lesescope — eksporten viser
     aldri mer enn appen ville vist samme person, og sier hva som er utelatt.
  3. Admin alene: uten `firma:eksport` er det 403.
  4. Rollemodellen: `admin` har scopet, `leser` har det ikke.

MUTASJONER SOM DREPER DENNE: dropp scopesjekken per modul (2), gi
`leser` scopet (4).
"""
import json
import secrets
import uuid

from .test_api import (DSN, MIGRATOR_DSN, TENANT, app, klient,  # noqa: F401
                       migrator, miljo, pg, token)
from .test_m37 import _sett_kontekst


def _hode(tok):
    return {"authorization": f"Bearer {tok}"}


@pg
def test_eksporten_er_en_fil_med_modulene_brukeren_kan_se(klient, token):
    from db.pg import koble
    nr = f"F-{secrets.randbelow(900000) + 100000}"
    c = koble(DSN)
    try:
        _sett_kontekst(c, TENANT)
        c.execute("SELECT m23_registrer_fordring(%s,%s,'K-eks',%s,1000,"
                  " current_date - 5, current_date + 5, 'kari')",
                  (TENANT, uuid.uuid4(), nr))
        c.commit()
    finally:
        c.close()
    tok, _ = token(rolle="admin", scopes=("firma:eksport", "okonomi:read",
                                          "decisions:read"))
    r = klient.get("/v1/firma/eksport", headers=_hode(tok))
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"].startswith(
        'attachment; filename="disponit-')
    assert r.headers["cache-control"] == "no-store"
    d = json.loads(r.content)
    assert d["format"] == "disponit-eksport/1"
    assert d["firma"]["tenant"] == TENANT
    # 1. Fakturaen står der, slik fordringsflaten viser den.
    nr_i_fila = [f["fakturanummer"]
                 for f in d["moduler"]["fordring"]["data"]["fordringer"]]
    assert nr in nr_i_fila
    # 2. Moduler bak et scope økten mangler, er utelatt — og det sies.
    assert "hms" not in d["moduler"]
    assert {"modul": "hms", "grunn": "mangler_scope"} in d["utelatt"]
    assert d["kunder"] is None, "kundelista krever part:read"


@pg
def test_kunderegisteret_kommer_med_adressene_i_klartekst(klient, token):
    """233: kundens kontakter er hennes, og en kunde som slutter trenger
    ADRESSENE, ikke maskene. Dekryptert i API-et, bare i eksportveien."""
    from db import kryptering
    from db.pg import koble
    ref = "K-eks-" + secrets.token_hex(3)
    adr = f"ola-{secrets.token_hex(3)}@kunde.example"
    c = koble(DSN)
    try:
        _sett_kontekst(c, TENANT)
        pid = c.execute("SELECT part_registrer(%s,%s,'Eksport AS',NULL,"
                        "'bedrift','kari')", (TENANT, ref)).fetchone()[0]
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(c, TENANT)
        ct, nonce = kryptering.krypter(dek, {"verdi": adr}, TENANT, key_id)
        psn = c.execute("SELECT tenant_pseudonym(%s,%s)",
                        (TENANT, adr)).fetchone()[0]
        c.execute("SELECT part_sett_kontakt(%s,%s,'epost','ol**@kunde.example',"
                  "%s,%s,%s,%s,true,NULL,'kari')",
                  (TENANT, pid, ct, nonce, key_id, psn))
        c.commit()
    finally:
        c.close()
    tok, _ = token(rolle="admin", scopes=("firma:eksport", "part:read"))
    d = json.loads(klient.get("/v1/firma/eksport", headers=_hode(tok)).content)
    kunde = next(k for k in d["kunder"] if k["part_ref"] == ref)
    assert kunde["kontakter"] == [{"kanal": "epost", "primar": True,
                                   "merkelapp": None, "verdi": adr,
                                   "maske": "ol**@kunde.example"}]


@pg
def test_eksporten_er_admins_alene(klient, token):
    tok, _ = token(rolle="leser", scopes=("decisions:read", "okonomi:read"))
    assert klient.get("/v1/firma/eksport",
                      headers=_hode(tok)).status_code == 403


def test_rollen_admin_har_scopet_leser_har_det_ikke():
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert "firma:eksport" in ROLLE_TIL_SCOPES["admin"]
    for rolle, scopes in ROLLE_TIL_SCOPES.items():
        if rolle != "admin":
            assert "firma:eksport" not in scopes, rolle
