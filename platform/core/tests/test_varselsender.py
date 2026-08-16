"""E-postsenderen — kopien av det som alt står i portalen.

Grensene som prøves her er de som gjør senderen trygg å la stå og gå:
  * den sender BARE til verifiserte adresser;
  * den plukker bare `koet`, aldri leste eller alt sendte;
  * to samtidige sendere kan ikke sende samme varsel to ganger;
  * én adresse som ikke tar imot stopper ikke resten av køen;
  * uten SMTP-oppsett rører den ikke køen — et manglende oppsett er en
    driftstilstand, ikke en egenskap ved varselet;
  * teksten rendres fra locale, ikke fra databasen.
"""
import secrets

import pytest

from api import varsel
from drift import varselsender

from .test_api import MIGRATOR_DSN

pg = pytest.mark.skipif(not MIGRATOR_DSN,
                        reason="DISPONIT_MIGRATOR_DSN ikke satt")
TEN = "t-sender-" + secrets.token_hex(3)


def _conn():
    from db.pg import koble, sett_kontekst
    c = koble(MIGRATOR_DSN)
    sett_kontekst(c, TEN, "test", "r0")
    return c


def _bruker(c, navn, epost, verifisert=True):
    import json
    bid = c.execute(
        "INSERT INTO brukeridentitet (issuer, sub, profil)"
        " VALUES (%s,%s,%s::jsonb)"
        " ON CONFLICT (issuer,sub) DO UPDATE SET profil=EXCLUDED.profil"
        " RETURNING bruker_id",
        ("https://idp.example", f"{TEN}-{navn}",
         json.dumps({"epost": epost, "epost_verifisert": verifisert}))
    ).fetchone()[0]
    c.execute("INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
              " VALUES (%s,%s,ARRAY['policyforvalter'])"
              " ON CONFLICT (tenant,bruker_id) DO UPDATE SET aktiv=true",
              (TEN, bid))
    return bid


def _ko(c, bid, ressurs):
    varsel.opprett(c, tenant=TEN, bruker_id=bid, art="attestering_venter",
                   ressurs_type="policyutkast", ressurs_id=ressurs,
                   hendelse="1", tekstnokkel="varsel.attestering_venter",
                   parametre={"policy_id": "p", "runde": 1,
                              "risikoklasse": "UTVIDER", "gjenstaar": 1})


def _kontekst(c):
    """Sett tenantkonteksten PÅ NYTT.

    `kjor()` committer per rad, og `sett_kontekst` bruker SET LOCAL — den dør
    med transaksjonen. Leser man etterpå uten å sette den igjen, filtrerer RLS
    bort alt, og assertions svarer «ingenting» uansett hva senderen gjorde.
    Tredje gang den fellen slår til i denne modulen; derfor en hjelper.
    """
    from db.pg import sett_kontekst
    sett_kontekst(c, TEN, "test", "r1")


def _samler():
    sendt = []
    return sendt, (lambda til, emne, tekst: sendt.append((til, emne, tekst)))


@pg
def test_sender_bare_til_verifiserte_adresser():
    """En uverifisert e-post i profilen er en PÅSTAND fra en IdP, ikke et
    bevis. Et varsel om en fullmaktsrunde skal ikke dit.

    Kontroll: fjern `epost_verifisert`-leddet i `varselkandidater`, så blir
    denne rød med to mottakere.
    """
    c = _conn()
    try:
        ok = _bruker(c, "ok", "ok@example.test", verifisert=True)
        nei = _bruker(c, "nei", "nei@example.test", verifisert=False)
        _ko(c, ok, "u-" + secrets.token_hex(4))
        _ko(c, nei, "u-" + secrets.token_hex(4))
        c.commit()
        sendt, send = _samler()
        res = varselsender.kjor(c, send=send)
        _kontekst(c)
        assert res["sendt"] == 1, res
        assert [t for t, _e, _x in sendt] == ["ok@example.test"]
    finally:
        c.close()


@pg
def test_samme_varsel_sendes_ikke_to_ganger():
    """`varsel_sett_epoststatus` flytter bare `koet` → `sendt`. To sendere som
    kjører samtidig kan derfor ikke sende det samme varselet to ganger."""
    c = _conn()
    try:
        b = _bruker(c, "en", "en@example.test")
        _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()
        sendt, send = _samler()
        assert varselsender.kjor(c, send=send)["sendt"] == 1
        _kontekst(c)
        assert varselsender.kjor(c, send=send)["sendt"] == 0, (
            "andre kjøring sendte på nytt")
        assert len(sendt) == 1
    finally:
        c.close()


@pg
def test_en_adresse_som_feiler_stopper_ikke_resten():
    c = _conn()
    try:
        d = _bruker(c, "daarlig", "daarlig@example.test")
        g = _bruker(c, "god", "god@example.test")
        _ko(c, d, "u-" + secrets.token_hex(4))
        _ko(c, g, "u-" + secrets.token_hex(4))
        c.commit()
        sendt = []

        def send(til, emne, tekst):
            if til == "daarlig@example.test":
                raise RuntimeError("mottaker avviste")
            sendt.append(til)

        res = varselsender.kjor(c, send=send)
        _kontekst(c)
        assert res["sendt"] == 1 and res["feilet"] == 1, res
        assert sendt == ["god@example.test"]
        # Den feilede raden står IGJEN i portalen — innboksen er sannheten.
        assert varsel.antall_uleste(c, tenant=TEN, bruker_id=d) == 1
    finally:
        c.close()


@pg
def test_uten_smtp_oppsett_roeres_koen_ikke(monkeypatch):
    """Et manglende oppsett er en DRIFTSTILSTAND, ikke en egenskap ved
    varselet. Å brenne forsøkstelleren på det ville stille kastet varsler som
    er helt i orden.
    """
    for k in ("VERT", "PORT", "BRUKER", "PASSORD", "AVSENDER"):
        monkeypatch.delenv(f"DISPONIT_SMTP_{k}", raising=False)
    c = _conn()
    try:
        b = _bruker(c, "urort", "urort@example.test")
        _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()
        res = varselsender.kjor(c)
        _kontekst(c)
        assert res["grunn"] == "smtp_ikke_konfigurert", res
        st = c.execute("SELECT epost_status, epost_forsok FROM varsel"
                       " WHERE tenant=%s AND bruker_id=%s",
                       (TEN, b)).fetchone()
        assert st == ("koet", 0), f"køen ble rørt: {st}"
    finally:
        c.close()


@pg
def test_teksten_rendres_fra_locale_med_parametre():
    c = _conn()
    try:
        b = _bruker(c, "tekst", "tekst@example.test")
        _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()
        sendt, send = _samler()
        varselsender.kjor(c, send=send)
        _kontekst(c)
        _til, emne, tekst = sendt[0]
        assert "{policy_id}" not in tekst and "{gjenstaar}" not in tekst, (
            f"plassholdere står igjen: {tekst!r}")
        assert "UTVIDER" in tekst, f"parametrene kom ikke med: {tekst!r}"
        assert emne and not emne.startswith("varsel."), (
            f"emnet er en rå nøkkel: {emne!r}")
    finally:
        c.close()


def test_locale_finnes_uten_at_driftsstien_finnes(monkeypatch):
    """Senderen finner `locales/` fra SIN EGEN plassering, ikke fra en
    hardkodet driftssti.

    Roten var `/opt/disponit/aktiv` når `DISPONIT_REPO` manglet — sant på
    staging og ingen andre steder. I CI fantes ikke stien, og senderen kastet
    `FileNotFoundError` på hver eneste e-post: den kunne altså ikke prøves der
    den bygges. Modulen ligger i det samme repoet som `locales/`, så den vet
    selv hvor de er.

    Denne testen er med vilje uten `@pg`: den skal kjøre overalt, for det var
    nettopp «kjører bare ett sted» som var feilen.
    """
    monkeypatch.delenv("DISPONIT_REPO", raising=False)
    tekster = varselsender._locale("nb")
    assert tekster.get("varsel.attestering_venter"), \
        "locale-settet ble ikke funnet uten driftsstien"
    # Ukjent språk faller til nb i stedet for å kaste.
    assert varselsender._locale("zz").get("varsel.attestering_venter")
    # …og teksten er faktisk en setning, ikke nøkkelen som falt gjennom.
    tekst = varselsender.rendre(tekster, "varsel.attestering_venter",
                                {"policy_id": "p", "runde": 1,
                                 "risikoklasse": "UTVIDER", "gjenstaar": 2})
    assert "UTVIDER" in tekst and "{" not in tekst, f"uferdig tekst: {tekst!r}"


def test_rendre_viser_ukjent_nokkel_i_stedet_for_tomhet():
    """En manglende oversettelse skal være SYNLIG. En tom e-post forteller
    ingenting; en rå nøkkel forteller sannheten."""
    assert varselsender.rendre({}, "varsel.finnes.ikke", {}) \
        == "varsel.finnes.ikke"
