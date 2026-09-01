"""M-11 (migrasjon 091) — selvtesten: probene, dørene og de to
sikkerhetsinvariantene.

TO AV PORTENE HER ER SIKKERHETSINVARIANTER, ikke kvalitetsmål:

  * KANARIPORTEN. En hemmelighet plantet BÅDE i miljøet og i
    oppsettsfilen skal ikke finnes igjen i `maalt`, i et varselparameter
    eller på stdout. Rapporten er tenkt LEST av mennesker og lagret i en
    tabell flaten viser — en probe som tar med verdien den så, lekker den
    tre steder på én gang. Porten kjører en HEL runde, ikke bare den ene
    proben: lekkasjen kan komme fra en probe ingen tenkte på.

  * DESTRUKTIVITETSPORTEN. `selvtest.py` skal ikke inneholde en skrivende
    HTTP-metode, en kommando som endrer en enhet eller en
    rettighetsheving. Målt STATISK på kildeteksten, fordi «den gjør ikke
    det i dag» ikke er en egenskap ved en fil som endres. En selvtest som
    kan endre systemet er et angrepsverktøy med planlagt kjøring, høye
    fullmakter og ingen menneskelig godkjenning i sløyfa.

Resten er dommen fra natt til 1/9, gjort maskinell: idempotens på
`kjoring_id`, ETT varsel per rød probe per døgn køet i SAMME transaksjon
som kjøringen, `ikke_konfigurert` som ALDRI varsles, grantgrensen målt i
runtime, og uteblitt-sveipen på 3 timer (3 × kadensen).

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
import io
import json
import os
import re
import secrets
import uuid
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, VARSEL_DSN,  # noqa: F401
                       app, klient, migrator, miljo, token)

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "091_m11_selvtest.sql")
KILDE = ROT / "platform" / "drift" / "selvtest.py"

SELVTEST_DSN = os.environ.get("DISPONIT_TEST_SELVTEST_DSN")


def _c():
    from db.pg import koble
    return koble(MIGRATOR_DSN)


def _rt():
    from db.pg import koble
    return koble(DSN)


def _st():
    from db.pg import koble
    return koble(SELVTEST_DSN or MIGRATOR_DSN)


def _vs():
    from db.pg import koble
    return koble(VARSEL_DSN or MIGRATOR_DSN)


def _plattformtenant(m, ten):
    admin = m.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
        " RETURNING bruker_id",
        ("https://idp.example", f"{ten}-adm")).fetchone()[0]
    leser = m.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
        " RETURNING bruker_id",
        ("https://idp.example", f"{ten}-leser")).fetchone()[0]
    from db.pg import sett_kontekst
    sett_kontekst(m, ten, "sys", "r0")
    m.execute("INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
              " VALUES (%s,%s,ARRAY['admin','leser'])", (ten, admin))
    m.execute("INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
              " VALUES (%s,%s,ARRAY['leser'])", (ten, leser))
    m.commit()
    return admin, leser


@contextmanager
def _uten_ferske_kjoringer(m):
    """Skyv HELE tabellen forbi 3-timersterskelen, og skyv den tilbake.

    Sveipen er PLATTFORMVID og ser hver rad i basen, også dem andre
    tester har lagt igjen. En test som hoppet over seg selv når en fersk
    rad fantes ville vært rekkefølgeavhengig — og en hoppet test er ikke
    en bestått test (CI feller den). Skyvingen er reversibel og eksakt.
    """
    m.execute("UPDATE selvtest_kjoring SET ts = ts - interval '100 days'")
    m.commit()
    try:
        yield
    finally:
        m.execute("UPDATE selvtest_kjoring SET ts = ts + interval '100 days'")
        m.commit()


# ---------------------------------------------------------------------------
# SIKKERHETSINVARIANT 1: destruktivitetsporten (statisk).
# ---------------------------------------------------------------------------

def test_selvtesten_har_ingen_destruktiv_kodevei():
    """SIKKERHETSINVARIANT (`m11-v1: destruktiv_probe`).

    Målt på kildeteksten, KOMMENTARER INKLUDERT. Det er ikke slurv: en
    port som bare leste kjørende kode ville godtatt et utkommentert
    utkast som venter på å bli slått på, og en som bare leste strenger
    ville godtatt en metode satt sammen av to biter. Regelen er derfor
    absolutt og lett å etterleve — modulen omtaler heller ikke de
    forbudte ordene, den bruker norske omskrivninger.

    Kontroll: legg `-X POST` inn i curl-kallet, så blir denne rød.
    """
    tekst = KILDE.read_text(encoding="utf-8")
    # HTTP-metodene som endrer noe. Store bokstaver: det er formen de har
    # i kode, og små bokstaver ville truffet «postgresql» og «postboks».
    for metode in ("POST", "PUT", "PATCH", "DELETE"):
        assert metode not in tekst, \
            f"selvtest.py nevner den skrivende metoden {metode}"
    # Rettighetsheving og enhetsendring, uansett skrivemåte.
    for ord_ in ("sudo", "restart", "systemctl start", "systemctl stop",
                 "--request", "-X "):
        assert not re.search(re.escape(ord_), tekst, re.IGNORECASE), \
            f"selvtest.py inneholder {ord_!r}"
    # Og den ene systemd-kommandoen den KJENNER er ren avlesning.
    systemd = re.findall(r'"systemctl",\s*"(\w+)"', tekst)
    assert systemd == ["show"], systemd


def test_uniten_gir_ingen_fullmakt_selvtesten_ikke_trenger():
    """Identiteten er helsesjekkens, ordrett — og `SupplementaryGroups`
    er sokkeltilgang, ikke en fullmakt. Selvtesten har med vilje ikke
    helsesjekkens sudoers-regel: den observerer, den griper aldri inn."""
    unit = (ROT / "deploy" / "staging"
            / "disponit-selvtest.service").read_text(encoding="utf-8")
    for linje in ("Type=oneshot", "User=disponit-helse",
                  "SupplementaryGroups=disponit-proxy",
                  "NoNewPrivileges=true",
                  "LoadCredential=DISPONIT_SELVTEST_URL:",
                  "EnvironmentFile=-/etc/disponit/m57/modell.env"):
        assert linje in unit, f"unitfila mangler {linje!r}"
    helse = (ROT / "deploy" / "staging"
             / "disponit-helse.service").read_text(encoding="utf-8")
    assert "SupplementaryGroups=disponit-proxy" in helse, \
        "presedensen for gruppeutvidelsen finnes ikke lenger"
    timer = (ROT / "deploy" / "staging"
             / "disponit-selvtest.timer").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=1h" in timer and "Persistent=true" in timer
    # KADENSEN OG TERSKELEN HØRER SAMMEN: sveipen i 091 varsler etter
    # 3 timer, altså 3 × kadensen. Endres den ene, må den andre følge.
    sql = MIGRASJON.read_text(encoding="utf-8")
    assert "interval '3 hours'" in sql


# ---------------------------------------------------------------------------
# SIKKERHETSINVARIANT 2: kanariporten.
# ---------------------------------------------------------------------------

@pg
def test_kanariporten_hemmeligheten_forlater_aldri_proben(tmp_path,
                                                          monkeypatch):
    """SIKKERHETSINVARIANT (`m11-v1: hemmelighet_i_rapport`).

    Kanarien plantes BEGGE steder en hemmelighet faktisk finnes: i
    miljøet (som `disponit-varselsender.service` setter den) og som
    VERDI i oppsettsfilen proben leser. Så kjøres en HEL runde — ikke
    bare `smtp_oppsett` — og kanarien skal ikke finnes igjen i:

      * `maalt` for noen probe,
      * noe varselparameter runden køet,
      * noe som ble skrevet til stdout.

    Kontroll: la `probe_smtp_oppsett` legge `linje` i `maalt`, så blir
    denne rød på det første punktet.
    """
    from drift import selvtest as sm
    st = _st()
    m = _c()
    ten = "t-kanari-" + secrets.token_hex(3)
    kanari = "KANARI-" + secrets.token_hex(16)
    try:
        _plattformtenant(m, ten)
        for navn in sm.SMTP_NAVN:
            monkeypatch.setenv(navn, kanari)
        smtp = tmp_path / "smtp.env"
        smtp.write_text("\n".join(f"{n}={kanari}" for n in sm.SMTP_NAVN)
                        + "\n", encoding="utf-8")
        # Modellserveren peker et sted ingenting svarer: proben blir rød,
        # og en rød probe er nettopp den som har mest å fortelle — altså
        # den mest sannsynlige lekkasjeveien.
        monkeypatch.setenv("DISPONIT_M57_MODELL_URL", "http://127.0.0.1:1")
        monkeypatch.setenv("DISPONIT_M57_MODELLNAVN", kanari)

        fanget = io.StringIO()
        with redirect_stdout(fanget):
            res = sm.kjor(st, tenant=ten, sokkel=str(tmp_path / "ingen.sock"),
                          smtp_sti=str(smtp),
                          enhetskatalog=str(tmp_path / "ingen-enheter"))

        rapport = json.dumps(res["prober"], ensure_ascii=False)
        assert kanari not in rapport, "kanarien står i probenes `maalt`"
        assert kanari not in fanget.getvalue(), "kanarien ble skrevet ut"

        from db.pg import sett_kontekst
        sett_kontekst(m, ten, "sys", "r1")
        varsler = m.execute(
            "SELECT parametre::text, ressurs_id, tekstnokkel FROM varsel"
            " WHERE tenant=%s", (ten,)).fetchall()
        m.rollback()
        for rad in varsler:
            for felt in rad:
                assert kanari not in str(felt), \
                    f"kanarien står i et varselfelt: {felt!r}"
        # Porten er verdiløs hvis runden ikke målte noe: SMTP-proben må
        # ha SETT filen (grønn, fordi alle fem navnene står der).
        assert res["prober"]["smtp_oppsett"]["status"] == sm.GRONN, \
            res["prober"]["smtp_oppsett"]
        assert res["prober"]["ollama"]["status"] == sm.ROD
        # …og den røde proben må ha køet et varsel, ellers er
        # varselasserten over også tom.
        assert varsler, "runden køet ingen varsel — porten målte ingenting"
    finally:
        st.close()
        m.close()


def test_smtp_proben_leser_navn_og_aldri_verdier(tmp_path, monkeypatch):
    """Fem navn der alle står → grønn. Noen, men ikke alle → RØD (noen
    har ment å sette det opp, og senderen står stille hvert 5. minutt).
    Ingen, eller ingen fil → `ikke_konfigurert`, som varsles ALDRI."""
    from drift import selvtest as sm
    sti = tmp_path / "smtp.env"
    # Miljøet er den ANDRE kilden; her måles filveien, så det ryddes.
    for navn in sm.SMTP_NAVN:
        monkeypatch.delenv(navn, raising=False)

    assert sm.probe_smtp_oppsett(str(tmp_path / "finnes-ikke")).status \
        == sm.UKONFIGURERT

    sti.write_text("# bare en kommentar\nNOE_ANNET=x\n", encoding="utf-8")
    assert sm.probe_smtp_oppsett(str(sti)).status == sm.UKONFIGURERT

    sti.write_text("\n".join(f"{n}=v" for n in sm.SMTP_NAVN[:3]) + "\n",
                   encoding="utf-8")
    delvis = sm.probe_smtp_oppsett(str(sti))
    assert delvis.status == sm.ROD, delvis
    # `mangler` er NAVN fra den lukkede konstanten — aldri noe fra filen.
    assert set(delvis.maalt["mangler"]) <= set(sm.SMTP_NAVN)

    sti.write_text("\n".join(f"export {n}=hemmelig" for n in sm.SMTP_NAVN)
                   + "\n", encoding="utf-8")
    hel = sm.probe_smtp_oppsett(str(sti))
    assert hel.status == sm.GRONN, hel
    assert "hemmelig" not in json.dumps(hel.maalt)


def test_smtp_proben_faller_tilbake_til_miljoet_naar_fila_er_stengt(
        tmp_path, monkeypatch):
    """PERMISJONSFELLEN, målt.

    I drift er `/etc/disponit/varsel/` `0700 root:root` — det er
    hemmeligheter — mens selvtesten kjører som `disponit-helse`. Leste
    proben BARE filen, ville den meldt `fil_uleselig` hver eneste time,
    for alltid; og en probe som alltid er rød er en probe folk skrur av.

    Uniten setter `EnvironmentFile=-` på nøyaktig samme fil, så systemd
    leser den som root og injiserer NAVNENE i miljøet. Proben spør
    `navn in os.environ` — aldri hva verdien er.

    Kontroll: fjern miljøfallbacken i `probe_smtp_oppsett`, så blir denne
    rød med `fil_uleselig`.
    """
    from drift import selvtest as sm
    stengt = tmp_path / "stengt.env"
    stengt.write_text("DISPONIT_SMTP_VERT=x\n", encoding="utf-8")
    stengt.chmod(0o000)
    try:
        for navn in sm.SMTP_NAVN:
            monkeypatch.setenv(navn, "en-hemmelighet")
        p = sm.probe_smtp_oppsett(str(stengt))
        assert p.status == sm.GRONN, p
        assert p.maalt["kilde"] == "miljo", p.maalt
        assert "en-hemmelighet" not in json.dumps(p.maalt)

        # Delvis satt i miljøet er RØDT, som i filen.
        monkeypatch.delenv(sm.SMTP_NAVN[0], raising=False)
        p = sm.probe_smtp_oppsett(str(stengt))
        assert p.status == sm.ROD, p
        assert p.maalt["mangler"] == [sm.SMTP_NAVN[0]], p.maalt

        # Ingenting satt noe sted: ikke_konfigurert, som varsles ALDRI.
        for navn in sm.SMTP_NAVN:
            monkeypatch.delenv(navn, raising=False)
        p = sm.probe_smtp_oppsett(str(stengt))
        assert p.status == sm.UKONFIGURERT, p
    finally:
        stengt.chmod(0o600)


def test_selvtestenheten_henter_smtp_navnene_gjennom_systemd():
    """Miljøveien over virker bare hvis uniten faktisk ber om filen — og
    med minus-tegnet, så en vert uten SMTP starter som før."""
    unit = (ROT / "deploy" / "staging"
            / "disponit-selvtest.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=-/etc/disponit/varsel/smtp.env" in unit, \
        "uten denne er smtp_oppsett permanent rød i drift"
    sender = (ROT / "deploy" / "staging"
              / "disponit-varselsender.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=-/etc/disponit/varsel/smtp.env" in sender, \
        "proben måler ikke lenger samme kilde som senderen bruker"


def test_ollama_uten_oppsett_er_ikke_konfigurert_ikke_rodt(monkeypatch):
    """De fleste installasjoner kjører aldri M-57. En rød probe for det
    ville vært et varsel ingen kan gjøre noe med — og et varsel folk
    lærer seg å overse, tar de røde med seg."""
    from drift import selvtest as sm
    monkeypatch.delenv("DISPONIT_M57_MODELL_URL", raising=False)
    monkeypatch.delenv("DISPONIT_M57_MODELLNAVN", raising=False)
    assert sm.probe_ollama().status == sm.UKONFIGURERT
    # Satt, men ugyldig, er derimot RØDT: noen har ment noe med det.
    assert sm.probe_ollama("ikke-en-url", "modell").status == sm.ROD


# ---------------------------------------------------------------------------
# Timer-probene: kadensen leses av enhetsfilen, aldri gjettet.
# ---------------------------------------------------------------------------

def test_kadensen_leses_av_enhetsfilen_og_gjettes_aldri(tmp_path):
    """En terskel regnet av en feiltolket kadens er verre enn ingen
    terskel — den ser like autoritativ ut. Et spenn vi ikke kan lese gir
    `None`, og proben blir `ikke_konfigurert`."""
    from drift import selvtest as sm
    assert sm.tolk_tidsspenn("5min") == 300
    assert sm.tolk_tidsspenn("1h") == 3600
    assert sm.tolk_tidsspenn("60") == 60
    assert sm.tolk_tidsspenn("1h 30min") == 5400
    # Et ledd med ukjent suffiks gjør HELE spennet uleselig — å hoppe
    # over det ville gitt et for lite tall, og et for lite tall her er en
    # terskel som slår ut på friske timere.
    assert sm.tolk_tidsspenn("5 uker") is None
    assert sm.tolk_tidsspenn("") is None

    f = tmp_path / "x.timer"
    f.write_text("[Timer]\nOnBootSec=3min\nOnUnitActiveSec=5min\n",
                 encoding="utf-8")
    assert sm.kadens_fra_enhetsfil(str(f)) == 300
    f.write_text("[Timer]\nOnCalendar=*-*-* 03:15:00 UTC\n", encoding="utf-8")
    assert sm.kadens_fra_enhetsfil(str(f)) == 86400
    f.write_text("[Timer]\nOnCalendar=Mon *-*-* 03:15:00\n", encoding="utf-8")
    assert sm.kadens_fra_enhetsfil(str(f)) is None
    assert sm.kadens_fra_enhetsfil(str(tmp_path / "finnes-ikke")) is None


def _falsk_systemctl(tmp_path, monkeypatch, utdata: str):
    """Legg en `systemctl` på PATH som svarer med ORDRETT den formen ekte
    systemd gir. Riggen finnes fordi CI ikke har systemd, og fordi den
    ene feilen som betyr noe her er en FORMFEIL i avlesningen — den
    fanges bare av ekte utdata, ikke av en mock som returnerer det koden
    forventer."""
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    (bin_ / "systemctl").write_text(
        "#!/bin/sh\ncat <<'EOF'\n" + utdata + "\nEOF\n", encoding="utf-8")
    (bin_ / "systemctl").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    return bin_


def test_timerproben_leser_systemds_EKTE_tidsstempelform(tmp_path,
                                                         monkeypatch):
    """Regresjonsporten for en feil som ville drept HELE timerarmen i
    stillhet.

    `systemctl show -p LastTriggerUSec` gir en LESBAR DATO
    («Mon 2026-08-31 00:51:16 UTC»), ikke mikrosekunder. En probe som
    leste den som et tall ville meldt `tidsstempel_uleselig` for hver
    eneste timer, for alltid — og `ikke_konfigurert` ser ut som et ærlig
    fravær, så ingen ville sett at ni prober sluttet å måle.

    `--timestamp=unix` er fiksen: verdien kommer som `@<sekunder>`.
    Kontroll: fjern flagget fra `_enhetsfelt`, så gir den falske
    `systemctl` under den formaterte datoen og denne blir rød.
    """
    from drift import selvtest as sm
    enheter = tmp_path / "enheter"
    enheter.mkdir()
    (enheter / "disponit-plan.timer").write_text(
        "[Timer]\nOnUnitActiveSec=5min\n", encoding="utf-8")

    naa = 1_788_146_252
    # Utløst for 2 minutter siden — godt innenfor 3 x 5 min.
    _falsk_systemctl(tmp_path, monkeypatch,
                     f"ActiveState=active\nResult=success\n"
                     f"LastTriggerUSec=@{naa - 120}")
    p = sm.probe_timer("disponit-plan", katalog=str(enheter), naa_s=naa)
    assert p.status == sm.GRONN, p
    assert p.maalt["alder_s"] == 120, p.maalt
    assert p.maalt["terskel_s"] == 900, p.maalt

    # Utenfor 3 x kadensen -> RØD, med sin egen ordlyd.
    _falsk_systemctl(tmp_path, monkeypatch,
                     f"ActiveState=active\nResult=success\n"
                     f"LastTriggerUSec=@{naa - 5000}")
    p = sm.probe_timer("disponit-plan", katalog=str(enheter), naa_s=naa)
    assert (p.status, p.maalt["grunn"]) == (sm.ROD, "for_lenge_siden"), p

    # Timeren står, men tjenesten den startet feilet.
    _falsk_systemctl(tmp_path, monkeypatch,
                     f"ActiveState=active\nResult=exit-code\n"
                     f"LastTriggerUSec=@{naa - 60}")
    p = sm.probe_timer("disponit-plan", katalog=str(enheter), naa_s=naa)
    assert (p.status, p.maalt["grunn"]) == (sm.ROD, "tjenesten_feilet"), p

    # Timeren er ikke aktiv i det hele tatt.
    _falsk_systemctl(tmp_path, monkeypatch,
                     f"ActiveState=inactive\nResult=success\n"
                     f"LastTriggerUSec=@{naa - 60}")
    p = sm.probe_timer("disponit-plan", katalog=str(enheter), naa_s=naa)
    assert (p.status, p.maalt["grunn"]) == (sm.ROD, "timer_ikke_aktiv"), p

    # ALDRI UTLØST er RØDT, ikke fravær: alle disse timerne er
    # `Persistent=true` og utløser ved oppstart.
    _falsk_systemctl(tmp_path, monkeypatch,
                     "ActiveState=active\nResult=success\n"
                     "LastTriggerUSec=n/a")
    p = sm.probe_timer("disponit-plan", katalog=str(enheter), naa_s=naa)
    assert (p.status, p.maalt["grunn"]) == (sm.ROD, "aldri_utloest"), p

    # …og en systemd som ikke kjenner flagget gir den formaterte datoen.
    # Da sier proben ærlig at den ikke vet — den GJETTER ikke.
    _falsk_systemctl(tmp_path, monkeypatch,
                     "ActiveState=active\nResult=success\n"
                     "LastTriggerUSec=Mon 2026-08-31 00:51:16 UTC")
    p = sm.probe_timer("disponit-plan", katalog=str(enheter), naa_s=naa)
    assert p.status == sm.UKONFIGURERT, p
    assert p.maalt["grunn"] == "tidsstempel_uleselig", p.maalt


def test_timerproben_uten_enhetsfil_er_ikke_konfigurert(tmp_path):
    """Verten kjører ikke den timeren. Det er den tredje statusen, ikke
    et mildere rødt — og den varsles aldri."""
    from drift import selvtest as sm
    p = sm.probe_timer("disponit-backup", katalog=str(tmp_path))
    assert p.status == sm.UKONFIGURERT
    assert p.maalt["grunn"] == "enhetsfil_mangler"


def test_alle_timerne_i_settet_har_en_enhetsfil_i_repoet():
    """Navnelisten er en KONSTANT, ikke en katalogskanning — og en
    konstant som navngir en enhet repoet ikke har, ville gitt en evig
    `ikke_konfigurert` ingen la merke til."""
    from drift import selvtest as sm
    for navn in sm.TIMERE:
        sti = ROT / "deploy" / "staging" / f"{navn}.timer"
        assert sti.exists(), f"{navn}.timer finnes ikke i repoet"
        assert sm.kadens_fra_enhetsfil(str(sti)) is not None, \
            f"{navn}.timer har en kadens selvtesten ikke kan lese"


# ---------------------------------------------------------------------------
# Dørene: idempotens, samlet-dommen, varsel i samme transaksjon.
# ---------------------------------------------------------------------------

def _prober(**status) -> str:
    return json.dumps({navn: {"status": s, "maalt": {}}
                       for navn, s in status.items()})


@pg
def test_en_feilet_probe_hindrer_ikke_at_runden_registreres():
    """CodeRabbit (major): `kjor` bruker SAMME forbindelse til probene og
    til `registrer_selvtest`. En feilet setning lar transaksjonen stå
    ABORTED, og uten en tilbakerulling ville hver senere setning feilet —
    inkludert skrivingen av runden. En rød `db_drift` ville altså gjort
    HELE runden uregistrerbar, altså tatt stemmen fra selvtesten nøyaktig
    i den tilstanden den finnes for å rapportere.

    Kontroll: fjern `conn.rollback()` i `probe_db_drift`, så velter
    registreringen under med `InFailedSqlTransaction`.
    """
    from drift import selvtest as sm
    st = _st()
    m = _c()
    ten = "t-m11abort-" + secrets.token_hex(3)
    try:
        _plattformtenant(m, ten)
        # Fell transaksjonen på nøyaktig den måten proben ville gjort det.
        p = sm.probe_db_drift(_FeilendeForbindelse(st))
        assert p.status == sm.ROD, p
        # …og forbindelsen skal fortsatt kunne brukes.
        kid = str(uuid.uuid4())
        ny = st.execute("SELECT registrer_selvtest(%s::uuid,%s::jsonb,%s)",
                        (kid, _prober(db_drift="rod"), ten)).fetchone()[0]
        st.commit()
        assert ny == 1, "runden ble ikke registrert etter en feilet probe"
        rad = m.execute("SELECT samlet FROM selvtest_kjoring"
                        " WHERE kjoring_id=%s", (kid,)).fetchone()
        m.rollback()
        assert rad == ("rod",), rad
    finally:
        st.close()
        m.close()


class _FeilendeForbindelse:
    """Forbindelsen, men der probens ENE setning feiler.

    Vi kunne ikke bare kalt `SELECT 1` på en stengt forbindelse: da ville
    testen målt at proben blir rød, ikke at den RYDDER OPP. Her feiler
    setningen slik en ekte SQL-feil gjør — transaksjonen står aborted til
    noen ruller tilbake — og `rollback` går til den ekte forbindelsen.
    """

    def __init__(self, ekte):
        self._ekte = ekte

    def execute(self, *a, **k):
        # En setning som ER lovlig SQL, men feiler i utførelsen: etter
        # denne står transaksjonen ABORTED, akkurat som ved en ekte feil.
        self._ekte.execute("SELECT 1/0")

    def rollback(self):
        self._ekte.rollback()


@pg
def test_samlet_dommen_felles_i_basen_ikke_av_kalleren():
    """En kaller som kunne påstå «gronn» over en rød probe ville vært
    m31s `kjoring_bestatt_pastatt_av_kaller` på nytt. `samlet` er ikke et
    argument — det finnes ingen vei inn for det."""
    st = _st()
    m = _c()
    ten = "t-m11samlet-" + secrets.token_hex(3)
    try:
        _plattformtenant(m, ten)
        forventet = {
            ("gronn",): "gronn",
            ("gronn", "rod"): "rod",
            ("ikke_konfigurert",): "ikke_konfigurert",
            ("gronn", "ikke_konfigurert"): "gronn",
            ("rod", "ikke_konfigurert"): "rod",
        }
        for statuser, samlet in forventet.items():
            kid = str(uuid.uuid4())
            st.execute("SELECT registrer_selvtest(%s::uuid,%s::jsonb,%s)",
                       (kid, _prober(**{f"p{i}": s
                                        for i, s in enumerate(statuser)}),
                        ten))
            st.commit()
            rad = m.execute("SELECT samlet FROM selvtest_kjoring"
                            " WHERE kjoring_id=%s", (kid,)).fetchone()
            m.rollback()
            assert rad == (samlet,), (statuser, rad, samlet)
    finally:
        st.close()
        m.close()


@pg
def test_registreringen_er_idempotent_paa_kjoring_id():
    """En retry skal verken duplisere prober eller køe varslene en gang
    til. Kontroll: fjern `IF v_rader = 0 THEN RETURN 0` i 091, så dobler
    varslene ved andre kall."""
    st = _st()
    m = _c()
    ten = "t-m11idem-" + secrets.token_hex(3)
    kid = str(uuid.uuid4())
    try:
        _plattformtenant(m, ten)
        a = st.execute("SELECT registrer_selvtest(%s::uuid,%s::jsonb,%s)",
                       (kid, _prober(api_live="rod"), ten)).fetchone()[0]
        st.commit()
        b = st.execute("SELECT registrer_selvtest(%s::uuid,%s::jsonb,%s)",
                       (kid, _prober(api_live="rod"), ten)).fetchone()[0]
        st.commit()
        assert (a, b) == (1, 0), (a, b)
        from db.pg import sett_kontekst
        sett_kontekst(m, ten, "sys", "r1")
        n_prober = m.execute("SELECT count(*) FROM selvtest_probe"
                             " WHERE kjoring_id=%s", (kid,)).fetchone()[0]
        n_varsel = m.execute("SELECT count(*) FROM varsel WHERE tenant=%s"
                             " AND art='selvtest_rodt'", (ten,)).fetchone()[0]
        m.rollback()
        assert (n_prober, n_varsel) == (1, 1), (n_prober, n_varsel)
    finally:
        st.close()
        m.close()


@pg
def test_rod_probe_koer_varsel_og_ikke_konfigurert_gjor_det_aldri():
    """Kjernen i dommen: en rød probe uten varsel i køen skal være
    urepresenterbar, og `ikke_konfigurert` varsles ALDRI.

    Kontroll: bytt `= 'rod'` til `<> 'gronn'` i 091, så varsles også
    `ikke_konfigurert` og denne blir rød."""
    st = _st()
    m = _c()
    ten = "t-m11varsel-" + secrets.token_hex(3)
    kid = str(uuid.uuid4())
    try:
        admin, leser = _plattformtenant(m, ten)
        st.execute("SELECT registrer_selvtest(%s::uuid,%s::jsonb,%s)",
                   (kid, _prober(api_live="rod", db_drift="gronn",
                                 ollama="ikke_konfigurert",
                                 smtp_oppsett="rod"), ten))
        st.commit()
        from db.pg import sett_kontekst
        sett_kontekst(m, ten, "sys", "r1")
        rader = m.execute(
            "SELECT bruker_id, ressurs_type, ressurs_id, tekstnokkel,"
            " parametre FROM varsel WHERE tenant=%s AND art='selvtest_rodt'"
            " ORDER BY ressurs_id", (ten,)).fetchall()
        m.rollback()
        assert [r[2] for r in rader] == ["api_live", "smtp_oppsett"], rader
        assert all(r[0] == admin for r in rader), \
            "varselet traff andre enn adminen"
        assert all(r[1] == "selvtest" for r in rader)
        assert all(r[3] == "varsel.selvtest_rodt" for r in rader)
        assert rader[0][4]["probe"] == "api_live", rader[0][4]
        assert leser not in [r[0] for r in rader]

        # SAMME DØGN, NY KJØRING: ett varsel per probe per dag, ikke ett
        # per timerkjøring (som er 24 i døgnet).
        st.execute("SELECT registrer_selvtest(%s::uuid,%s::jsonb,%s)",
                   (str(uuid.uuid4()), _prober(api_live="rod"), ten))
        st.commit()
        sett_kontekst(m, ten, "sys", "r2")
        n = m.execute("SELECT count(*) FROM varsel WHERE tenant=%s"
                      " AND art='selvtest_rodt' AND ressurs_id='api_live'",
                      (ten,)).fetchone()[0]
        m.rollback()
        assert n == 1, f"{n} varsler for samme probe samme døgn"
    finally:
        st.close()
        m.close()


@pg
def test_tom_probemengde_avvises():
    """En runde uten prober er ikke en grønn runde — den er en runde som
    ikke målte noe, og den skal ikke kunne registreres."""
    st = _st()
    try:
        for ugyldig in ("{}", "[]", "null"):
            with pytest.raises(psycopg.Error):
                st.execute("SELECT registrer_selvtest(%s::uuid,%s::jsonb,%s)",
                           (str(uuid.uuid4()), ugyldig, "disponit"))
            st.rollback()
    finally:
        st.close()


@pg
def test_status_settet_er_lukket_i_lagringen():
    """`gronn|rod|ikke_konfigurert` og ingenting annet — håndhevet av
    CHECK-en, ikke av kallerens disiplin."""
    st = _st()
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            st.execute("SELECT registrer_selvtest(%s::uuid,%s::jsonb,%s)",
                       (str(uuid.uuid4()), _prober(p="gul"), "disponit"))
        st.rollback()
    finally:
        st.close()


# ---------------------------------------------------------------------------
# Grantgrensen og lesedøren.
# ---------------------------------------------------------------------------

@pg
def test_runtime_avvises_paa_skrivedoeren_og_tabellene():
    """Web-runtime skal ikke kunne dikte en grønn runde over en rød
    plattform — og heller ikke lese tabellene direkte forbi
    tenantkontekst-porten (SP-7)."""
    rt = _rt()
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT registrer_selvtest(%s::uuid,%s::jsonb,%s)",
                       (str(uuid.uuid4()), _prober(p="gronn"), "disponit"))
        rt.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT varsle_selvtest_uteblitt(%s)", ("disponit",))
        rt.rollback()
        for tabell in ("selvtest_kjoring", "selvtest_probe"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute(f"SELECT count(*) FROM {tabell}")
            rt.rollback()
    finally:
        rt.close()


@pg
def test_lesedoeren_krever_tenantkontekst_og_grupperer_ikke_i_sql():
    """051-formen, og oversikt-lærdommen: døren gir FLATE rader
    (kjøring × probe) fordi grupperingen er presentasjon."""
    from db.pg import sett_kontekst
    st = _st()
    rt = _rt()
    m = _c()
    ten = "t-m11les-" + secrets.token_hex(3)
    kid = str(uuid.uuid4())
    try:
        _plattformtenant(m, ten)
        st.execute("SELECT registrer_selvtest(%s::uuid,%s::jsonb,%s)",
                   (kid, _prober(api_live="gronn", db_drift="gronn"), ten))
        st.commit()
        with pytest.raises(psycopg.Error):
            rt.execute("SELECT * FROM selvtest_status(%s,%s)",
                       ("t-uten-kontekst", 5)).fetchall()
        rt.rollback()
        sett_kontekst(rt, ten, "sys", "r0")
        rader = rt.execute("SELECT * FROM selvtest_status(%s,%s)",
                           (ten, 100)).fetchall()
        rt.rollback()
        mine = [r for r in rader if str(r[0]) == kid]
        assert [r[4] for r in mine] == ["api_live", "db_drift"], mine
        assert all(r[2] == "gronn" for r in mine)
        # Nyeste først er dørens rekkefølge, ikke klientens sortering.
        ts_er = [r[1] for r in rader]
        assert ts_er == sorted(ts_er, reverse=True)
    finally:
        st.close()
        rt.close()
        m.close()


# ---------------------------------------------------------------------------
# Uteblitt-sveipen: 3 timer, idempotent per døgn, bare plattformadminene.
# ---------------------------------------------------------------------------

@pg
def test_uteblitt_varsles_og_er_idempotent_per_dogn():
    """Selvtesten kan ikke varsle om sin egen død. Denne sveipen bor i
    varselsenderen — en annen prosess, en annen rolle, en annen kadens —
    og er den ENE veien tilstanden når fram."""
    m = _c()
    vs = _vs()
    ten = "t-m11sveip-" + secrets.token_hex(3)
    try:
        admin, leser = _plattformtenant(m, ten)
        with _uten_ferske_kjoringer(m):
            vs.execute("SELECT varsle_selvtest_uteblitt(%s)", (ten,))
            vs.commit()
            from db.pg import sett_kontekst
            sett_kontekst(m, ten, "sys", "r1")
            rader = m.execute(
                "SELECT bruker_id, ressurs_type, tekstnokkel, parametre"
                " FROM varsel WHERE tenant=%s AND art='selvtest_uteblitt'",
                (ten,)).fetchall()
            m.rollback()
            assert len(rader) == 1, rader
            assert rader[0][0] == admin
            assert rader[0][1] == "selvtest"
            assert rader[0][2] == "varsel.selvtest_uteblitt"
            assert rader[0][3]["terskel_timer"] == 3, rader[0][3]
            assert leser not in [r[0] for r in rader]

            vs.execute("SELECT varsle_selvtest_uteblitt(%s)", (ten,))
            vs.commit()
            sett_kontekst(m, ten, "sys", "r2")
            n = m.execute("SELECT count(*) FROM varsel WHERE tenant=%s"
                          " AND art='selvtest_uteblitt'",
                          (ten,)).fetchone()[0]
            m.rollback()
            assert n == 1, f"sveipen køet {n} varsler for samme døgn"
    finally:
        vs.close()
        m.close()


@pg
def test_fersk_kjoring_gir_ingen_uteblitt_varsel():
    """Den positive siden av terskelen."""
    st = _st()
    vs = _vs()
    m = _c()
    ten = "t-m11fersk-" + secrets.token_hex(3)
    try:
        _plattformtenant(m, ten)
        st.execute("SELECT registrer_selvtest(%s::uuid,%s::jsonb,%s)",
                   (str(uuid.uuid4()), _prober(p="gronn"), ten))
        st.commit()
        n = vs.execute("SELECT varsle_selvtest_uteblitt(%s)",
                       (ten,)).fetchone()[0]
        vs.commit()
        assert n == 0, n
    finally:
        st.close()
        vs.close()
        m.close()


# ---------------------------------------------------------------------------
# Senderens pre-pass: begge sveipene kalles, og hver er skjermet.
# ---------------------------------------------------------------------------

def test_senderen_kaller_begge_de_nye_sveipene_skjermet():
    """De to nye sveipene hører hjemme i `varselsender.kjor()`, og HVER
    har sin egen skjermede blokk: en feil i den ene skal verken stoppe
    den andre eller sendingen av det som alt ligger i køen.

    Kontroll: samle de tre i én try-blokk, så blir denne rød."""
    kilde = (ROT / "platform" / "drift"
             / "varselsender.py").read_text(encoding="utf-8")
    for setning in ("SELECT varsle_backupverifisering_uteblitt(%s)",
                    "SELECT varsle_selvtest_uteblitt(%s)",
                    "SELECT varsle_tokenfamilie_utlop(%s)"):
        assert setning in kilde, f"senderen kaller ikke {setning}"
    # Løkka rundt de tre ER skjermingen: hver runde har sin egen
    # try/except/rollback, så én feilende sveip tar ikke de andre.
    assert "conn.rollback()" in kilde
    assert kilde.count("-sveipen feilet") == 1, \
        "skjermingen er duplisert i stedet for delt"


def test_inngangspunktet_skiller_rod_plattform_fra_feilet_selvtest(
        monkeypatch):
    """Exit 0 for en runde med røde prober (det er en MÅLING), exit 2 for
    en jobb som ikke kunne starte. En rød probe som ga exit 1 ville gjort
    `systemctl status` rød av noe ANNET enn at selvtesten er nede — og da
    hadde vi mistet det ene signalet sveipen ikke kan se innenfra."""
    from drift import kjor_selvtest
    monkeypatch.delenv("DISPONIT_SELVTEST_URL", raising=False)
    assert kjor_selvtest.main() == 2


def test_migrasjonen_navngir_ikke_runtime_rollen():
    """057-lærdommen, som i 090: migrasjonen har ingen mening om hva
    runtime heter. `migrer.py` er autoritativ for lesedøren."""
    sql = MIGRASJON.read_text(encoding="utf-8")
    assert "GRANT EXECUTE ON FUNCTION selvtest_status" not in sql
    for rolle in ("disponit_selvtest", "disponit_varselsender"):
        assert f"WHERE rolname = '{rolle}'" in sql, \
            f"{rolle} grantes uten pg_roles-vakt"


# ---------------------------------------------------------------------------
# HTTP-flaten: scopet, og grupperingen som er API-lagets jobb.
# ---------------------------------------------------------------------------

@pg
def test_endepunktet_grupperer_de_flate_radene_per_kjoring(
        klient, token, migrator):     # noqa: F811
    """Døren gir flate rader (kjøring × probe); API-laget setter dem
    sammen. Det er oversikt-lærdommen: presentasjon bor her, ikke i SQL.

    Kontroll: la `selvtest_svar` returnere radene rått, så mister
    kjøringen sin `prober`-liste og denne blir rød."""
    from .test_api import TENANT
    st = _st()
    kid = str(uuid.uuid4())
    try:
        st.execute("SELECT registrer_selvtest(%s::uuid,%s::jsonb,%s)",
                   (kid, json.dumps({
                       "api_live": {"status": "gronn", "maalt": {}},
                       "ollama": {"status": "ikke_konfigurert",
                                  "maalt": {"grunn": "ikke_satt"}},
                       "smtp_oppsett": {"status": "rod",
                                        "maalt": {"funnet": 3,
                                                  "kreves": 5}}}),
                    TENANT))
        st.commit()
    finally:
        st.close()

    uten, _ = token(scopes=["decisions:read"])
    svar = klient.get("/v1/drift/selvtest",
                      headers={"authorization": f"Bearer {uten}"})
    assert svar.status_code == 403, svar.text

    med, _ = token(tenant=TENANT, rolle="admin", scopes=["security:read"])
    svar = klient.get("/v1/drift/selvtest",
                      headers={"authorization": f"Bearer {med}"})
    assert svar.status_code == 200, svar.text
    kropp = svar.json()
    mine = [k for k in kropp["kjoringer"] if k["kjoring_id"] == kid]
    assert len(mine) == 1, "kjøringen ble ikke gruppert til ÉN oppføring"
    k = mine[0]
    assert k["samlet"] == "rod"
    assert [p["probe"] for p in k["prober"]] == [
        "api_live", "ollama", "smtp_oppsett"], k["prober"]
    # `maalt` sendes videre UENDRET: filtreringen hører til proben, som er
    # det ene stedet som vet hva den målte.
    smtp = [p for p in k["prober"] if p["probe"] == "smtp_oppsett"][0]
    assert smtp["maalt"] == {"funnet": 3, "kreves": 5}, smtp
    assert k["alder_s"] >= 0
    ts_er = [x["ts"] for x in kropp["kjoringer"]]
    assert ts_er == sorted(ts_er, reverse=True)


@pg
def test_endepunktet_har_ingen_skrivevei(klient, token):   # noqa: F811
    """Runder skrives av selvtestens EGEN rolle. Ingen HTTP-dør finnes —
    en sikkerhetsdom, ikke en manglende funksjon."""
    med, _ = token(rolle="admin", scopes=["security:read"])
    h = {"authorization": f"Bearer {med}"}
    for metode in ("post", "put", "delete", "patch"):
        svar = getattr(klient, metode)("/v1/drift/selvtest", headers=h)
        assert svar.status_code == 405, (metode, svar.status_code)
