"""Hele kundereisen, i én test — fra registrering til prøveslutt og tilbake.

Eiers mål for arcen (18/11): dashbordet skal være lett FØRSTE gang og
deretter selvbetjent. Hver etappe har sin egen port (selvregistrering 192/
207, vilkår 235, innstillinger 236, pause 230-232, eksport 233). Denne
porten måler noe ingen av dem gjør: at etappene HENGER SAMMEN for én og samme
kunde, gjennom ekte økter og ekte ruter, slik hun går dem.

  1. Før hun har en konto: forsiden og skjemaet vet prøveperioden og
     bransjene (236), og vilkårene som kreves (235).
  2. Registreringen: firmaet blir til med eierens prøveperiode, og
     samtykket lagres.
  3. Første dag, som admin i sitt nye firma: statusen sier «prøve»,
     «Kom i gang» svarer, postboksen er tom (ikke en feil), og hun kan ta
     med seg dataene sine.
  4. Prøveperioden løper ut — gjennom SVEIPEN, ikke en UPDATE i testen:
     statusen sier «pauset», men hun kommer fortsatt inn, leser og
     eksporterer (eiers vedtak 27/11: «Pause agentene, les+eksport»).
  5. Plattformeieren åpner firmaet igjen i plattformadmin, og banneret
     er borte.

RYDDER ETTER SEG: innstillingene og vilkårene er plattformens egne rader
(fixturene fra 235/236 legger dem tilbake), og firmaet stenges.
"""
import datetime
import secrets

from .test_api import DSN, MIGRATOR_DSN, app, klient, miljo  # noqa: F401
from .test_juridisk_port import ren_juridisk  # noqa: F401
from .test_plattform_http import (_browsersesjon, _bruker,  # noqa: F401
                                  _rydd_plattformeiere, pg)
from .test_plattform_innstillinger_port import ren_innstilling  # noqa: F401
from .test_selvregistrering_port import _bruker as _ny_identitet
from .test_selvregistrering_port import _registrantokt


def _okt_i(tenant, bid):
    """Økten hun får når hun logger inn i SITT firma etter registreringen
    (callbacken velger medlemskapet hun har)."""
    from api import sesjon as sesjonmodul
    from db.pg import koble, sett_kontekst
    m = koble(MIGRATOR_DSN)
    try:
        sett_kontekst(m, tenant, "sys", "kundereise")
        ver = m.execute("SELECT authz_version FROM brukermedlemskap"
                        " WHERE tenant=%s AND bruker_id=%s",
                        (tenant, bid)).fetchone()[0]
        cookie, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        m.execute(
            "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
            " authz_snapshot, csrf_hash, utloper) VALUES"
            " (%s,%s,%s,%s,%s, now() + interval '10 hours')",
            (sesjonmodul._hash(cookie), tenant, bid, ver,
             sesjonmodul._hash(csrf)))
        m.commit()
        return {sesjonmodul.C_SESJON: cookie}
    finally:
        m.close()


def _eier():
    from api import sesjon as sesjonmodul
    bid = _bruker("reise-eier", plattformeier=True)
    cookie, csrf = _browsersesjon(bid)
    return {sesjonmodul.C_SESJON: cookie}, {"x-disponit-csrf": csrf}


@pg
def test_hele_kundereisen(klient, ren_innstilling, ren_juridisk):
    from api import sesjon as sesjonmodul
    from db.pg import koble, sett_kontekst
    ec, eh = _eier()

    # Eieren har gjort sitt i plattformadmin: 21 dagers prøve, to bransjer,
    # vilkår og databehandleravtale publisert.
    r = klient.post("/v1/plattform/innstillinger", cookies=ec, headers=eh,
                    json={"prove_dogn": 21,
                          "bransjer": ["tjenestebedrift", "netthandel"]})
    assert r.status_code == 200, r.text
    for dok in ("vilkar", "databehandleravtale"):
        assert klient.post(f"/v1/plattform/juridisk/{dok}/utkast",
                           cookies=ec, headers=eh,
                           json={"tekst": f"# {dok}\n\n{{{{navn}}}}"}
                           ).status_code == 200
        assert klient.post(f"/v1/plattform/juridisk/{dok}/publiser",
                           cookies=ec, headers=eh).status_code == 200

    # 1. Før kontoen: det forsiden og skjemaet viser.
    valg = klient.get("/v1/registrering/valg").json()
    assert (valg["prove_dogn"], valg["bransjer"]) == (
        21, ["tjenestebedrift", "netthandel"])
    kreves = klient.get("/v1/juridisk").json()["kreves"]
    assert set(kreves) == {"vilkar", "databehandleravtale"}

    # 2. Registreringen.
    m = koble(MIGRATOR_DSN)
    try:
        bid = _ny_identitet(m)
        cookie, csrf = _registrantokt(m, bid)
    finally:
        m.close()
    r = klient.post(
        "/v1/firma/registrer",
        json={"navn": f"Reise {secrets.token_hex(3)} AS",
              "bransje": "tjenestebedrift",
              "fullmakter": ["kundeservice-svar"], "godtar": kreves},
        cookies={sesjonmodul.C_SESJON: cookie, sesjonmodul.C_CSRF: csrf},
        headers={"X-Disponit-CSRF": csrf,
                 "Idempotency-Key": secrets.token_hex(16)})
    assert r.status_code in (200, 201), r.text
    tenant = r.json()["tenant"]
    frist = datetime.date.fromisoformat(r.json()["prove_utloper"][:10])
    assert 20 <= (frist - datetime.date.today()).days <= 22

    try:
        # 3. Første dag, i sitt eget firma.
        kc = _okt_i(tenant, bid)
        st = klient.get("/v1/firma/status", cookies=kc)
        assert st.status_code == 200, st.text
        st = st.json()
        assert st["status"] == "prove" and st["pauset"] is False, st
        assert 20 <= st["dogn_igjen"] <= 22
        # Banneret teller ikke ned ennå — tre uker er ikke noe å handle på.
        assert st["vis"] is False, st

        opp = klient.get("/v1/oppsett", cookies=kc)
        assert opp.status_code == 200, opp.text
        kilder = klient.get("/v1/epost/kilder", cookies=kc)
        assert kilder.status_code == 200, kilder.text
        assert kilder.json()["kilder"] == [], "en ny postboks er TOM"

        eks = klient.get("/v1/firma/eksport", cookies=kc)
        assert eks.status_code == 200, eks.text
        assert eks.json()["firma"]["tenant"] == tenant

        # 4. Prøveperioden løper ut — fristen flyttes bakover, og SVEIPEN
        #    gjør resten, slik timeren gjør det i drift hvert femte minutt.
        m = koble(MIGRATOR_DSN)
        try:
            sett_kontekst(m, tenant, "sys", "kundereise")
            m.execute("ALTER TABLE firma DISABLE TRIGGER USER")
            m.execute("UPDATE firma SET prove_utloper = current_date - 1"
                      " WHERE tenant=%s", (tenant,))
            m.execute("ALTER TABLE firma ENABLE TRIGGER USER")
            m.commit()
            m.execute("SET ROLE disponit_m37_claimer")
            utlopt = {t for (t, _d) in m.execute(
                "SELECT tenant, utlopt_dato FROM"
                " firma_sveip_proveutlop(1000)").fetchall()}
            m.execute("RESET ROLE")
            m.commit()
        finally:
            m.close()
        assert tenant in utlopt, "sveipen tok ikke firmaet"

        st = klient.get("/v1/firma/status", cookies=kc).json()
        assert st["status"] == "utlopt" and st["pauset"] is True, st
        # … men hun kommer inn, leser og tar med seg dataene.
        assert klient.get("/v1/oppsett", cookies=kc).status_code == 200
        assert klient.get("/v1/firma/eksport", cookies=kc).status_code == 200

        # 5. Plattformeieren åpner igjen.
        r = klient.post(f"/v1/plattform/firmaer/{tenant}/status",
                        cookies=ec, headers=eh, json={"status": "aktiv"})
        assert r.status_code == 200, r.text
        st = klient.get("/v1/firma/status", cookies=kc).json()
        assert st["status"] == "aktiv" and st["pauset"] is False, st
        assert st["vis"] is False
    finally:
        klient.post(f"/v1/plattform/firmaer/{tenant}/status", cookies=ec,
                    headers=eh, json={"status": "stengt"})
