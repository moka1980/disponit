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

from .test_api import DSN, MIGRATOR_DSN

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


@pg
def test_feilet_epost_prøves_igjen_etter_backoff():
    """En feilet sending er IKKE endelig.

    `varselkandidater` plukker bare `koet`, så uten re-køing var `feilet` en
    blindvei og forsøkstelleren død kode: ett forbigående SMTP-hikk mistet
    e-posten for godt. Re-køingen er et eget steg nettopp for å beholde
    garantien om at `koet` er den eneste sendbare tilstanden.

    Kontroll: fjern `varsel_rekoe_feilede`-kallet i `kjor`, så blir denne rød.
    """
    c = _conn()
    try:
        b = _bruker(c, "retry", "retry@example.test")
        _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()

        def alltid_feil(til, emne, tekst):
            raise RuntimeError("midlertidig")

        assert varselsender.kjor(c, send=alltid_feil)["feilet"] == 1
        _kontekst(c)
        # Umiddelbart etterpå skal den IKKE prøves igjen — backoff.
        sendt, send = _samler()
        assert varselsender.kjor(c, send=send)["sendt"] == 0, "ingen backoff"
        _kontekst(c)
        # Skru tiden tilbake, og den skal komme tilbake i køen.
        c.execute("UPDATE varsel SET epost_ts = now() - interval '1 hour'"
                  " WHERE tenant=%s AND bruker_id=%s", (TEN, b))
        c.commit()
        _kontekst(c)
        assert varselsender.kjor(c, send=send)["sendt"] == 1, (
            "en feilet e-post ble aldri prøvd igjen")
    finally:
        c.close()


@pg
def test_retryen_stopper_eksakt_paa_maks_forsok(monkeypatch):
    """Den andre halvdelen av retry-løftet: den slutter å prøve.

    Testen over viser at en feilet e-post kommer TILBAKE i køen. Uten et tak
    målt like presist er det løftet like farlig som blindveien var: en adresse
    som aldri tar imot ville banket på hvert 15. minutt i evighet, og
    `MAKS_FORSOK` ville vært en konstant ingen test hadde sett virke.

    Backoffen skrus av ved å skru KLOKKA tilbake mellom rundene, ikke ved å
    sette den til null — da måles taket og backoffen hver for seg, og en
    runde som stoppet fordi backoffen bet ville ikke kunne forveksles med en
    runde som stoppet fordi taket bet.
    """
    monkeypatch.setattr(varselsender, "MAKS_FORSOK", 2)
    c = _conn()
    try:
        b = _bruker(c, "maks", "maks@example.test")
        _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()

        forsokt = []

        def alltid_feil(til, emne, tekst):
            forsokt.append(til)
            raise RuntimeError("mottaker avviser alltid")

        for _ in range(5):
            varselsender.kjor(c, send=alltid_feil)
            _kontekst(c)
            c.execute("UPDATE varsel SET epost_ts = now() - interval '1 hour'"
                      " WHERE tenant=%s AND bruker_id=%s", (TEN, b))
            c.commit()
            _kontekst(c)

        assert len(forsokt) == 2, (
            f"{len(forsokt)} forsøk, ikke MAKS_FORSOK=2 — taket bet ikke der "
            "det skulle")
        st = c.execute("SELECT epost_status, epost_forsok FROM varsel"
                       " WHERE tenant=%s AND bruker_id=%s",
                       (TEN, b)).fetchone()
        assert st == ("feilet", 2), f"endetilstanden er ikke terminal: {st}"
        # Og raden står IGJEN i portalen. E-posten er kopien; innboksen er
        # sannheten, og den skal ikke kunne gå tapt med den siste sendingen.
        assert varsel.antall_uleste(c, tenant=TEN, bruker_id=b) == 1
    finally:
        c.close()


@pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
def test_runtime_rollen_kan_kalle_senderens_funksjoner():
    """Rollen som FAKTISK kjører senderen må kunne kalle de tre funksjonene.

    Migrasjon 027 gjør `REVOKE ALL … FROM PUBLIC` på alle tre. EXECUTE er
    PUBLIC som standard, så revokeringen er det som stenger døra — og uten et
    tilsvarende GRANT er den stengt for alle utenom eieren. Senderen kobler med
    runtime-DSN-en (`disponit`), som ikke er medlem av `disponit_domene_eier`.

    Alle de andre testene her kobler som `disponit_migrator`, som ER medlem av
    eierrollen. De ville derfor vært grønne mens hver eneste timerkjøring i
    drift endte i «permission denied for function varselkandidater», med køen
    urørt — samme klasse som de to andre P1-ene i denne runden.

    Kontroll: fjern GRANT-linjene fra 027, og denne blir rød på nøyaktig den
    feilmeldingen driften ville sett.
    """
    from db.pg import koble
    c = koble(DSN)
    try:
        c.execute("SELECT * FROM varselkandidater(1)").fetchall()
        c.execute("SELECT varsel_rekoe_feilede(interval '15 minutes', 3)")
        # id -1 finnes ikke: kallet skal slippe gjennom rettighetssjekken og
        # svare `false`, uten å røre en eneste rad.
        assert c.execute("SELECT varsel_sett_epoststatus(-1,'sendt',NULL)"
                         ).fetchone()[0] is False
        c.rollback()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Codex P1 (PR-068): credential-veien er en del av inngangspunktet.
#
# `LoadCredential=` setter `$CREDENTIALS_DIRECTORY` — den setter ingen
# miljøvariabel. Hoppes hydreringen over, ser `main()` aldri DSN-en den
# faktisk HAR fått, og senderen avbryter ved hver eneste timerkjøring. Alle
# testene over gir `kjor()` en ferdig forbindelse og kan derfor ikke se det:
# feilen lå i veien fra unit til prosess, ikke i sendingen. Reviewet ba om
# nettopp den veien målt.
# ---------------------------------------------------------------------------

def test_inngangspunktet_leser_dsn_fra_credential_katalogen(tmp_path,
                                                            monkeypatch):
    """Den faktiske veien: unit → $CREDENTIALS_DIRECTORY → os.environ → DSN.

    Kontroll: fjern `last_credentials()` fra `main()`, og denne dør på
    «DISPONIT_DATABASE_URL mangler» — nøyaktig linjen journalen ville vist.
    """
    import db.pg
    from drift import kjor_varselsender

    (tmp_path / "DISPONIT_DATABASE_URL").write_text("postgresql:///falsk\n",
                                                    encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.delenv("DISPONIT_DATABASE_URL", raising=False)

    sett = {}

    class _Falsk:
        def close(self):
            sett["lukket"] = True

    def _koble(dsn):
        sett["dsn"] = dsn
        return _Falsk()

    monkeypatch.setattr(db.pg, "koble", _koble)
    monkeypatch.setattr(kjor_varselsender.varselsender, "kjor",
                        lambda conn: {"sendt": 0, "feilet": 0,
                                      "hoppet_over": 0})

    assert kjor_varselsender.main() == 0, \
        "senderen avbrøt selv om credentialen lå i katalogen unitten laster"
    assert sett["dsn"] == "postgresql:///falsk", sett
    assert sett.get("lukket"), "forbindelsen ble ikke lukket"


def test_hver_unit_med_loadcredential_hydrerer_dem():
    """Porten, ikke bare fiksen: hvert inngangspunkt en unit starter med
    `LoadCredential=` MÅ kalle `last_credentials()`.

    Tre jobber husket det hver for seg, den fjerde glemte det — og glemselen
    er usynlig overalt utenom under systemd. Da er koblingen unit → hydrering
    det som skal måles, ikke den enkelte jobbens hukommelse.
    """
    import ast
    import re
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    maalt = 0
    for unit in sorted((rot / "deploy/staging").glob("*.service")):
        tekst = unit.read_text(encoding="utf-8")
        if "LoadCredential=" not in tekst:
            continue
        m = re.search(r"^ExecStart=\S+/python -m ([\w.]+)", tekst, re.M)
        if not m:
            continue          # ikke et `python -m`-inngangspunkt (api, cli)
        # Modulen slås opp slik unitten selv slår den opp — WorkingDirectory
        # og PYTHONPATH — så porten følger unitten hvis noen flytter en modul.
        soek = re.findall(r"^(?:WorkingDirectory=|Environment=PYTHONPATH=)"
                          r"(\S+)", tekst, re.M)
        assert soek, f"{unit.name}: verken WorkingDirectory eller PYTHONPATH"
        treff = [p for p in
                 (rot / s.replace("/opt/disponit/aktiv/", "")
                  / (m.group(1).replace(".", "/") + ".py") for s in soek)
                 if p.exists()]
        assert treff, f"{unit.name}: fant ikke {m.group(1)} i {soek}"
        # AST, ikke grep: en docstring som NEVNER hydreringen er ikke en
        # hydrering. Første utgave av porten var grønn på nettopp det.
        kalles = any(
            isinstance(n, ast.Call)
            and getattr(n.func, "id", None) == "last_credentials"
            for n in ast.walk(ast.parse(treff[0].read_text(encoding="utf-8"))))
        assert kalles, (
            f"{unit.name} laster credentials, men {m.group(1)} hydrerer dem "
            "aldri — den avbryter på en miljøvariabel den HAR fått")
        maalt += 1
    assert maalt >= 3, f"porten målte bare {maalt} units — regexen råtnet"
