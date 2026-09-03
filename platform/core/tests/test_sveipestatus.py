"""115 — sveipeflåtens taushet, observert utenfra.

HVA DENNE MODULEN LØSTE. Plattformen har atten nattlige sveip. Hver av
dem teller sine egne sammenhengende feil og skriver `"alarm": 1` i
journalen når telleren når to.

DET FELTET HAR ALDRI HATT EN KONSUMENT. Et søk gjennom treet finner
bare testene som leser det. «To feilede kjøringer → alarm» har vært en
linje i journalen, ikke en varsling.

OG DET FARLIGSTE TILFELLET SKRIVER INGENTING I DET HELE TATT. En sveip
som feiler, etterlater en linje. En sveip som ALDRI KJØRER — timeren
deaktivert, enheten død, DSN-en borte — er helt taus, og ser nøyaktig
ut som en sveip uten funn.

DEN SKARPESTE PORTEN HER er derfor
`test_en_sveip_som_aldri_har_kjort_blir_savnet`: roster-listen er en
KONSTANT nettopp fordi en kataloglisting bare ville funnet sveip som
HAR skrevet en fil — altså vært blind for den ene tilstanden modulen
finnes for.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo)
from .test_m37 import _sett_kontekst

ROT = Path(__file__).resolve().parents[3]
DRIFT = ROT / "platform" / "drift"
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "115_sveipestatus.sql")

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")


# ---------------------------------------------------------------------------
# ROSTEREN — den må være komplett, og den må være en konstant
# ---------------------------------------------------------------------------

def test_rosteren_dekker_hver_sveip_i_drift():
    """LISTEN SKAL VÆRE KOMPLETT, BEGGE VEIER.

    En sveip som mangler her blir aldri savnet — den er usynlig for
    nøyaktig den mekanismen som skal fange at den er borte. Og en
    oppføring uten en modul er en sveip som er slettet uten at noen
    ryddet listen; da varsler flåten om en taushet ingen kan fikse.

    MUTASJONEN SOM DREPER DENNE: legg til en ny `*sveip.py` uten å føre
    den i `FLAATEN`.
    """
    from drift.sveipestatus import FLAATEN
    paa_disk = {p.stem for p in DRIFT.glob("*.py")
                if not p.stem.startswith("kjor_")
                and p.stem not in {"__init__", "sveipestatus",
                                   "varselsender", "backupstatus",
                                   "selvtest", "plan", "helse"}}
    # Bare moduler som faktisk fører en feilteller hører i flåten: det
    # er DEN fila observatøren leser.
    med_teller = set()
    for navn in paa_disk:
        kjorer = DRIFT / f"kjor_{navn}.py"
        if kjorer.exists() and "_skriv_feiltelling" in kjorer.read_text(
                encoding="utf-8"):
            med_teller.add(navn)
    assert med_teller, "porten fant ingen jobber med feilteller"
    mangler = sorted(med_teller - set(FLAATEN))
    doede = sorted(set(FLAATEN) - med_teller)
    assert mangler == [], f"ikke i FLAATEN: {mangler}"
    assert doede == [], f"i FLAATEN, men finnes ikke: {doede}"


def test_hvert_vindu_er_satt_bevisst():
    """VINDUET ER KADENSEN PLUSS SLARK, ikke en rund verdi.

    En nattlig sveip med 30 minutters spredning kan legitimt gå 24,5
    timer mellom to kjøringer. 30 timer er 090s tall for backupen og av
    samme grunn: trygt over ett døgn, godt under to.

    DE HYPPIGE JOBBENE HAR SITT EGET TALL, og det er poenget med
    porten: `artefaktrydding` går hvert kvarter, og et 30-timers vindu
    ville gjort den usynlig i et helt døgn etter at den døde.
    """
    from drift.sveipestatus import FLAATEN
    for navn, timer in FLAATEN.items():
        assert 1 <= timer <= 168, (navn, timer)
    assert FLAATEN["artefaktrydding"] < 12, \
        "en kvartersjobb med døgnvindu er usynlig et helt døgn"
    nattlige = [n for n, t in FLAATEN.items() if t == 30]
    assert len(nattlige) >= 18, nattlige


def test_observatoren_er_ikke_en_felles_planlegger():
    """DEN LESER FILER, DEN KJØRER INGEN SVEIP.

    Det nærliggende svaret på atten timere er å slå dem sammen til én
    jobb. Den jobben måtte hatt alle atten `LoadCredential`-ene og
    dermed alle atten rollenes fullmakt — og revet ned nøyaktig det
    oppdelingen finnes for.

    MUTASJONEN SOM DREPER DENNE: la observatøren importere en sveip.
    """
    import ast
    for fil in (DRIFT / "sveipestatus.py",
                DRIFT / "kjor_sveipestatus.py"):
        for node in ast.walk(ast.parse(fil.read_text(encoding="utf-8"))):
            navn = []
            if isinstance(node, ast.Import):
                navn = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                navn = [node.module or ""]
            for n in navn:
                assert "sveip" not in n or n.endswith("sveipestatus"), \
                    f"{fil.name} importerer {n} — observatøren kjører" \
                    " ingen sveip"
    # KOMMENTARENE FORKLARER hvordan hemmeligheten kommer inn og må
    # derfor ikke telle med — porten måler KODEN.
    kilde = (DRIFT / "kjor_sveipestatus.py").read_text(encoding="utf-8")
    uten = "\n".join(l for l in kilde.splitlines()
                     if not l.lstrip().startswith("#"))
    for ord_ in ("subprocess", "systemctl", "Popen"):
        assert ord_ not in uten, ord_
    # ÉN credential, ikke atten: observatøren har driftstatusrollens
    # DSN og ingen sveiperolles.
    assert uten.count("os.environ.get") == 1, uten


# ---------------------------------------------------------------------------
# LESINGEN AV FILSYSTEMET
# ---------------------------------------------------------------------------

def test_en_sveip_som_aldri_har_kjort_blir_savnet(tmp_path):
    """MODULENS SKARPESTE PORT.

    En kataloglisting ville bare funnet sveip som HAR skrevet en fil —
    altså vært blind for den ene tilstanden modulen finnes for. Roster
    er derfor en konstant, og en sveip uten fil får `None` og
    `uten_tilstandsfil = True`.

    `None` OG IKKE 0: «aldri kjørt» og «kjørte, uten feil» er to helt
    forskjellige historier, og den første er den farligste fordi den
    ser ut som ingenting.

    MUTASJONEN SOM DREPER DENNE: la `les_flaaten` hoppe over sveip uten
    fil.
    """
    from drift.sveipestatus import FLAATEN, les_flaaten
    rader = les_flaaten(tmp_path)
    assert len(rader) == len(FLAATEN)
    assert all(r.uten_tilstandsfil for r in rader)
    assert all(r.sist_kjort_epoch is None for r in rader)
    # IKKE 0 — telleren er UKJENT, ikke null.
    assert all(r.sammenhengende_feil is None for r in rader)


def test_en_ulesbar_fil_er_ikke_null_feil(tmp_path):
    """FILA FANTES, MEN VAR IKKE KONTRAKTEN.

    Å skrive 0 der ville sagt «kjørte, uten feil» om en fil ingen kan
    lese — og gjort en korrupt tilstand til en grønn rad.
    """
    from drift.sveipestatus import les_flaaten
    (tmp_path / "kampanjesveip.json").write_text("ikke json",
                                                 encoding="utf-8")
    (tmp_path / "lonnssveip.json").write_text('{"noe_annet": 1}',
                                              encoding="utf-8")
    (tmp_path / "adressesveip.json").write_text('{"feil": -1}',
                                                encoding="utf-8")
    (tmp_path / "betalingssveip.json").write_text('{"feil": 2}',
                                                  encoding="utf-8")
    per = {r.sveip: r for r in les_flaaten(tmp_path)}
    for navn in ("kampanjesveip", "lonnssveip", "adressesveip"):
        assert per[navn].ulesbar, navn
        assert per[navn].sammenhengende_feil is None, navn
        # FILA FANTES — det er en annen tilstand enn at den mangler.
        assert not per[navn].uten_tilstandsfil, navn
    assert per["betalingssveip"].sammenhengende_feil == 2
    assert not per["betalingssveip"].ulesbar


def test_sist_kjort_er_filens_mtime(tmp_path):
    """OG DEFINISJONEN ER NAVNGITT: «sist fullførte kjøring som ikke ble
    hoppet over».

    En kjøring som fant arbeidernøkkelen opptatt skriver med VILJE ikke
    fila — feiltelleren skal stå urørt — og flytter derfor ikke mtime.
    Vinduene er satt etter den definisjonen.
    """
    from drift.sveipestatus import les_flaaten
    fil = tmp_path / "prisboksveip.json"
    fil.write_text('{"feil": 0}', encoding="utf-8")
    gammelt = time.time() - 40 * 3600
    os.utime(fil, (gammelt, gammelt))
    per = {r.sveip: r for r in les_flaaten(tmp_path)}
    assert per["prisboksveip"].sist_kjort_epoch == pytest.approx(
        gammelt, abs=2)
    assert per["prisboksveip"].sammenhengende_feil == 0


def test_katalogen_foelger_systemd(monkeypatch, tmp_path):
    from drift.sveipestatus import STANDARDKATALOG, katalog
    monkeypatch.delenv("DISPONIT_SVEIPETILSTANDSKATALOG", raising=False)
    monkeypatch.delenv("STATE_DIRECTORY", raising=False)
    assert katalog() == STANDARDKATALOG
    # systemd oppgir en KOLONSEPARERT liste når flere er deklarert.
    monkeypatch.setenv("STATE_DIRECTORY", f"{tmp_path}:/annen")
    assert katalog() == tmp_path
    monkeypatch.setenv("DISPONIT_SVEIPETILSTANDSKATALOG", "/eksplisitt")
    assert katalog() == Path("/eksplisitt")


def test_jobben_nekter_aa_starte_uten_egen_dsn(monkeypatch):
    from drift import kjor_sveipestatus
    monkeypatch.delenv("DISPONIT_DRIFTSTATUS_URL", raising=False)
    monkeypatch.setattr("db.hemmeligheter.last_credentials", lambda: None)
    assert kjor_sveipestatus.main() == 2


def test_ingen_fallback_til_database_url():
    """`DATABASE_URL` ville gitt jobben runtime-rollens fullmakter, og
    rollen finnes for å slippe dem."""
    kilde = (DRIFT / "kjor_sveipestatus.py").read_text(encoding="utf-8")
    uten = "\n".join(l for l in kilde.splitlines()
                     if not l.lstrip().startswith("#"))
    assert "DATABASE_URL" not in uten


# ---------------------------------------------------------------------------
# BASEN
# ---------------------------------------------------------------------------

def _admin(migrator):
    """En aktiv admin-mottaker i testtenanten.

    UTEN DEN MÅLER VARSELPORTEN INGENTING: `varsle_sveip_uteblitt`
    løkker over tenantens aktive admin-medlemmer, og en tenant uten
    slike gir 0 uansett hva flåten sier. En `skip` her ville sett grønn
    ut og bevist ingenting.
    """
    from api import sesjon as sesjonmodul  # noqa: F401  (samme rigg)
    _sett_kontekst(migrator, TENANT)
    bid = migrator.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://sveipestatus.test', %s) RETURNING bruker_id",
        ("s115-" + secrets.token_hex(6),)).fetchone()[0]
    migrator.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,%s,true)", (TENANT, bid, ["admin"]))
    migrator.commit()
    return bid


#: Dørene eies av `disponit_m37_claimer` og er REVOKET fra PUBLIC —
#: porten må ta eierrollen, akkurat som drift-jobben gjør gjennom sin
#: egen EXECUTE. Å gi migrator et grant i stedet ville vært å utvide
#: rettighetene for å få testen grønn.
def _somEier(migrator):
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")


def _rens(migrator):
    # SOM MIGRATOR, IKKE SOM EIEREN. Dørenes eier har SELECT, INSERT og
    # UPDATE — men bevisst IKKE DELETE: flåtens tilstand OPPDATERES,
    # den ryddes ikke. At opprydningen i testen må ta en annen rolle er
    # nettopp beviset på at innsnevringen står.
    migrator.execute("RESET ROLE")
    migrator.execute("DELETE FROM sveipestatus")
    migrator.commit()


def _for(migrator, sveip, *, timer_siden=None, feil=0, vindu=30,
         uten_fil=False, ulesbar=False):
    # `make_interval` tar INT; alderen uttrykkes derfor i MINUTTER, som
    # også lar porten treffe grensetilfellet (29,9 timer) presist.
    minutter = None if timer_siden is None else int(timer_siden * 60)
    _somEier(migrator)
    migrator.execute(
        "SELECT registrer_sveipestatus(%s,"
        " CASE WHEN %s::int IS NULL THEN NULL"
        "      ELSE now() - make_interval(mins => %s::int) END,"
        " %s,%s,%s,%s,'test')",
        (sveip, minutter, minutter, feil, vindu, uten_fil, ulesbar))
    migrator.commit()


@pg
def test_tabellen_er_plattformskopet(migrator):
    """090s form: flåten er hele installasjonens, ikke en tenants."""
    kolonner = {r[0] for r in migrator.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name='sveipestatus'"
    ).fetchall()}
    migrator.rollback()
    assert "tenant" not in kolonner, kolonner
    rls = migrator.execute(
        "SELECT relrowsecurity FROM pg_class"
        " WHERE oid='public.sveipestatus'::regclass").fetchone()[0]
    migrator.rollback()
    assert rls is False


@pg
def test_taus_og_i_alarm_regnes_i_basen(migrator):
    """REGELEN HAR ETT STED. Lesejobben leser; den avgjør ingenting.

    GRENSETILFELLET ER PORTEN: nøyaktig på vinduet er ikke taus, én
    time over er det.

    MUTASJONEN SOM DREPER DENNE: bytt `<` mot `<=` i `sveipeflaaten`.
    """
    _rens(migrator)
    _for(migrator, "innenfor", timer_siden=29, vindu=30)
    _for(migrator, "paa_grensen", timer_siden=29.9, vindu=30)
    _for(migrator, "utenfor", timer_siden=31, vindu=30)
    _for(migrator, "aldri", timer_siden=None, vindu=30, uten_fil=True)
    _for(migrator, "hyppig_ok", timer_siden=2, vindu=3)
    # SAMME ALDER, ULIKT VINDU: en kvartersjobb som ikke har gått på
    # fire timer er død; en nattlig er det ikke.
    _for(migrator, "hyppig_taus", timer_siden=4, vindu=3)
    _for(migrator, "med_alarm", timer_siden=1, vindu=30, feil=2)
    _for(migrator, "en_feil", timer_siden=1, vindu=30, feil=1)

    _sett_kontekst(migrator, TENANT)
    _somEier(migrator)
    rader = {r[0]: r for r in migrator.execute(
        "SELECT sveip, taus, i_alarm, uten_tilstandsfil, timer_siden"
        "  FROM sveipeflaaten(%s)", (TENANT,)).fetchall()}
    migrator.rollback()
    assert rader["innenfor"][1] is False
    assert rader["paa_grensen"][1] is False, "på grensen er ikke taus"
    assert rader["utenfor"][1] is True
    assert rader["aldri"][1] is True
    assert rader["aldri"][3] is True, "skillet mellom aldri og gammelt"
    assert rader["aldri"][4] is None, "ingen alder uten en kjøring"
    assert rader["hyppig_ok"][1] is False
    assert rader["hyppig_taus"][1] is True
    # ALARMEN ER TO, IKKE ÉN — samme terskel som sveipene selv bruker.
    assert rader["med_alarm"][2] is True
    assert rader["en_feil"][2] is False


@pg
def test_en_ulesbar_fil_ser_ikke_frisk_ut(migrator):
    """CodeRabbit, alvorlig og REELT.

    `ulesbar` ble regnet av lesejobben og nådde ALDRI basen. Følgen var
    en blindsone med begge signalene grønne: fila FINNES, så
    `sist_kjort` er fersk og sveipen er ikke taus — og telleren er
    NULL, som `coalesce(..., 0)` gjør til «ingen feil».

    En korrupt tilstandsfil så altså helt frisk ut, i nøyaktig den
    modulen som finnes for å fange at en sveip ikke er det.

    «VI VET IKKE» ER IKKE «ALT ER BRA». Den ulesbare regnes derfor som
    i alarm, og står i sitt EGET sett i varselet — å slå det sammen med
    de faktisk feilende ville gjort en korrupt fil til en feilrapport
    ingen kan verifisere.

    MUTASJONEN SOM DREPER DENNE: fjern `OR s.ulesbar` fra
    `sveipeflaaten`.
    """
    _rens(migrator)
    _admin(migrator)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("DELETE FROM varsel WHERE tenant=%s"
                     "  AND art='sveip_uteblitt'", (TENANT,))
    migrator.commit()
    # FERSK FIL, UKJENT TELLER — den farlige kombinasjonen.
    _for(migrator, "lagersveip", timer_siden=1, vindu=30, feil=None,
         ulesbar=True)
    _for(migrator, "prisboksveip", timer_siden=1, vindu=30, feil=0)

    _sett_kontekst(migrator, TENANT)
    _somEier(migrator)
    rader = {r[0]: r for r in migrator.execute(
        "SELECT sveip, taus, i_alarm, ulesbar FROM sveipeflaaten(%s)",
        (TENANT,)).fetchall()}
    migrator.rollback()
    # IKKE TAUS — fila er fersk. Det er nettopp derfor den andre
    # kanalen må fange den.
    assert rader["lagersveip"][1] is False
    assert rader["lagersveip"][2] is True, \
        "en ulesbar teller ble lest som «ingen feil»"
    assert rader["lagersveip"][3] is True
    assert rader["prisboksveip"][2] is False

    _somEier(migrator)
    n = migrator.execute("SELECT varsle_sveip_uteblitt(%s)",
                         (TENANT,)).fetchone()[0]
    migrator.commit()
    assert n >= 1, "en ulesbar fil alene utløste ingen varsling"
    _sett_kontekst(migrator, TENANT)
    p = migrator.execute(
        "SELECT parametre FROM varsel WHERE tenant=%s"
        "  AND art='sveip_uteblitt' LIMIT 1", (TENANT,)).fetchone()[0]
    migrator.rollback()
    # EGET SETT, ikke slått sammen med de feilende.
    assert p["antall_ulesbare"] == 1, p
    assert "lagersveip" in p["ulesbare"], p
    assert p["antall_tause"] == 0, p


@pg
def test_foringen_er_idempotent_per_sveip(migrator):
    """TILSTANDEN skal kunne leses, ikke en historikk over den."""
    _rens(migrator)
    _for(migrator, "kampanjesveip", timer_siden=1, feil=0)
    _somEier(migrator)
    ny = migrator.execute(
        "SELECT registrer_sveipestatus('kampanjesveip', now(), 3, 30,"
        " false, false, 'test')").fetchone()[0]
    migrator.commit()
    assert ny is False, "andre føring skal oppdatere, ikke sette inn"
    _somEier(migrator)
    n, feil = migrator.execute(
        "SELECT count(*), max(sammenhengende_feil) FROM sveipestatus"
        " WHERE sveip='kampanjesveip'").fetchone()
    migrator.rollback()
    assert n == 1 and feil == 3


@pg
def test_lesedoren_krever_tenantkontekst(migrator):
    """SP-1: lesedøren er tenantbundet selv om tabellen ikke er det."""
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        _somEier(migrator)
        migrator.execute("SELECT * FROM sveipeflaaten(%s)",
                         ("en-annen-tenant",))
    assert "tenantkontekst" in str(ei.value)
    migrator.rollback()


@pg
def test_driftstatusrollen_har_to_execute_og_null_tabeller(migrator):
    """ROLLEN ER GJENBRUKT, OG INNSNEVRINGEN MÅLES.

    `disponit_driftstatus` finnes for én jobbklasse — en
    drift-observatør som leser filsystemtilstand og fører den inn i
    basen. 115 gir den én EXECUTE til og ingenting annet.

    LESEDØREN STÅR BEVISST IKKE DER: lesejobben skriver, den leser
    aldri historikken tilbake, og en `sveipeflaaten` den ikke trenger
    ville vært en tenantsveip den ikke skal ha (090s ordlyd).

    MUTASJONEN SOM DREPER DENNE: gi rollen SELECT på `sveipestatus`.
    """
    # `has_table_privilege`, IKKE `information_schema` (CodeRabbit).
    #
    # `information_schema.table_privileges` viser bare grants der rollen
    # er NAVNGITT mottaker. En `GRANT SELECT ... TO PUBLIC` ville vært
    # usynlig der og fullt virksom i praksis — målt: information_schema
    # ser 0, `has_table_privilege` ser True. Porten måler den FAKTISKE
    # rettigheten, arvet og PUBLIC medregnet.
    # ROLLEN ER VALGFRI — den opprettes av `oppsett-postgresql.sh`, og
    # migrasjonen gir den rettigheter bak en `IF EXISTS`-vakt.
    # `has_table_privilege` KASTER på en rolle som ikke finnes, så
    # porten må stille samme spørsmål som migrasjonen (CodeRabbit).
    finnes = migrator.execute(
        "SELECT 1 FROM pg_roles WHERE rolname='disponit_driftstatus'"
    ).fetchone()
    migrator.rollback()
    if not finnes:
        pytest.skip("disponit_driftstatus er ikke opprettet her")
    for tabell in ("sveipestatus", "backupverifisering", "varsel"):
        for rett in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            har = migrator.execute(
                "SELECT has_table_privilege('disponit_driftstatus',"
                " %s, %s)", (tabell, rett)).fetchone()[0]
            migrator.rollback()
            assert har is False, f"driftstatus har {rett} på {tabell}"
    funksjoner = sorted(r[0] for r in migrator.execute(
        "SELECT p.proname FROM pg_proc p"
        "  JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname='public'"
        "   AND has_function_privilege('disponit_driftstatus',"
        "                              p.oid, 'EXECUTE')"
        "   AND p.proname IN ('registrer_backupverifisering',"
        "                     'registrer_sveipestatus',"
        "                     'sveipeflaaten', 'varsle_sveip_uteblitt')"
    ).fetchall())
    migrator.rollback()
    assert funksjoner == ["registrer_backupverifisering",
                          "registrer_sveipestatus"], funksjoner


@pg
def test_varselet_gaar_en_gang_per_dogn_og_samler_flaaten(migrator):
    """ETT VARSEL PER DØGN PER MOTTAKER, IKKE ETT PER SVEIP.

    En vert der timerne er slått av ville ellers sendt atten e-poster
    på én natt — og atten varsler om samme sak er ingen varsling, det
    er en flom noen lager en filterregel for.

    MUTASJONEN SOM DREPER DENNE: løkk over sveipene i stedet for over
    mottakerne.
    """
    _rens(migrator)
    _admin(migrator)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("DELETE FROM varsel WHERE tenant=%s"
                     "  AND art='sveip_uteblitt'", (TENANT,))
    migrator.commit()
    for navn in ("adressesveip", "betalingssveip", "lonnssveip"):
        _for(migrator, navn, timer_siden=48, vindu=30)
    _for(migrator, "kampanjesveip", timer_siden=1, vindu=30, feil=2)
    _for(migrator, "prisboksveip", timer_siden=1, vindu=30, feil=0)

    _somEier(migrator)
    n1 = migrator.execute("SELECT varsle_sveip_uteblitt(%s)",
                          (TENANT,)).fetchone()[0]
    migrator.commit()
    # GJENTATT KJØRING SAMME DØGN KØER INGENTING NYTT (090s form:
    # `varsel_en_per_hendelse` på døgnet).
    _somEier(migrator)
    n2 = migrator.execute("SELECT varsle_sveip_uteblitt(%s)",
                          (TENANT,)).fetchone()[0]
    migrator.commit()
    assert n2 == 0, "andre kjøring samme døgn køet nye varsler"

    _sett_kontekst(migrator, TENANT)
    rader = migrator.execute(
        "SELECT parametre FROM varsel WHERE tenant=%s"
        "  AND art='sveip_uteblitt'", (TENANT,)).fetchall()
    migrator.rollback()
    assert n1 >= 1, "varselet nådde ingen — porten måler ingenting"
    # ÉN rad per mottaker, ikke én per sveip.
    assert len(rader) == n1
    p = rader[0][0]
    assert p["antall_tause"] == 3, p
    assert p["antall_alarm"] == 1, p
    assert "prisboksveip" not in p["tause"], p
    assert "kampanjesveip" in p["i_alarm"], p


@pg
def test_en_observator_som_aldri_har_kjort_varsler(migrator):
    """HVEM OBSERVERER OBSERVATØREN (CodeRabbit, alvorlig og REELT).

    Er `sveipestatus` TOM, er alle tre funnsettene tomme — og
    varselfunksjonen returnerte 0. En observatør som aldri har kjørt ga
    altså nøyaktig samme svar som en frisk flåte: taushet om taushet, i
    den ene modulen som finnes for å bryte den.

    MUTASJONEN SOM DREPER DENNE: fjern `AND NOT v_uobservert` fra
    vakten i `varsle_sveip_uteblitt`.
    """
    _rens(migrator)
    _admin(migrator)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("DELETE FROM varsel WHERE tenant=%s"
                     "  AND art='sveip_uteblitt'", (TENANT,))
    migrator.commit()

    # TOM TABELL — ingen sveip er taus, ingen feiler, ingen er ulesbar.
    _somEier(migrator)
    n = migrator.execute("SELECT varsle_sveip_uteblitt(%s)",
                         (TENANT,)).fetchone()[0]
    migrator.commit()
    assert n >= 1, "en observatør som aldri har kjørt varslet ingenting"
    _sett_kontekst(migrator, TENANT)
    p = migrator.execute(
        "SELECT parametre FROM varsel WHERE tenant=%s"
        "  AND art='sveip_uteblitt' LIMIT 1", (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert p["uobservert"] is True, p
    assert p["sist_observert"] is None, p
    assert p["antall_tause"] == 0, p

    # …OG LESEDØREN SIER DET SAMME.
    _sett_kontekst(migrator, TENANT)
    _somEier(migrator)
    rad = migrator.execute("SELECT * FROM sveipeobservasjonen(%s)",
                           (TENANT,)).fetchone()
    migrator.rollback()
    assert rad == (0, None, True), rad


@pg
def test_en_frisk_flaate_varsler_ingenting(migrator):
    _rens(migrator)
    # MOTTAKEREN FINNES — det er nettopp derfor 0 betyr noe her.
    _admin(migrator)
    # …OG RADENE ER FERSKT OBSERVERT: `_for` setter `observert = now()`,
    # så observatøren selv er ikke taus. Uten rader ville denne testen
    # vært grønn av feil grunn — se
    # `test_en_observator_som_aldri_har_kjort_varsler`.
    for navn in ("adressesveip", "betalingssveip", "lonnssveip"):
        _for(migrator, navn, timer_siden=2, vindu=30, feil=0)
    _somEier(migrator)
    n = migrator.execute("SELECT varsle_sveip_uteblitt(%s)",
                         (TENANT,)).fetchone()[0]
    migrator.commit()
    assert n == 0


def test_varselsenderen_kaller_den():
    """TAUSHETSSVEIPENE BOR HOS SENDEREN — den ene timerdrevne prosessen
    som allerede eier varselkøens rytme (035/090s begrunnelse).

    Og hver av dem har sin EGEN skjermede blokk: en feil i den ene skal
    verken stoppe den andre eller sendingen av det som alt ligger i
    køen.
    """
    kilde = (DRIFT / "varselsender.py").read_text(encoding="utf-8")
    assert '("SELECT varsle_sveip_uteblitt(%s)", "sveipestatus")' in kilde
    # SQL-en er en HEL literal, ikke et navn satt inn i en mal.
    assert "varsle_sveip_uteblitt(%s)" in kilde
    assert not re.search(r'f"SELECT varsle_\{', kilde)


def test_locale_dekker_varselet():
    for sprak in ("nb", "en"):
        tekster = json.loads(
            (ROT / "locales" / f"{sprak}.json").read_text(
                encoding="utf-8"))
        t = tekster["varsel.sveip_uteblitt"]
        for felt in ("{antall_tause}", "{tause}", "{antall_alarm}",
                     "{i_alarm}", "{antall_ulesbare}", "{ulesbare}",
                     "{uobservert}", "{sist_observert}"):
            assert felt in t, (sprak, felt)


def test_timeren_gaar_etter_hele_stigen():
    """REKKEFØLGEN ER POENGET.

    Observatøren skal lese flåtens tilstand ETTER at flåten har kjørt.
    Kjørte den 02:00, ville hver eneste sveip sett ut som «ikke kjørt i
    dag» — og varselet vært en falsk alarm hver natt.

    MUTASJONEN SOM DREPER DENNE: flytt timeren før 08:20.
    """
    fil = (ROT / "deploy" / "staging"
           / "disponit-sveipestatus.timer").read_text(encoding="utf-8")
    m = re.search(r"OnCalendar=\*-\*-\* (\d\d):(\d\d):00 UTC", fil)
    assert m, fil
    minutt = int(m.group(1)) * 60 + int(m.group(2))
    senest = 0
    for t in (ROT / "deploy" / "staging").glob("*.timer"):
        if t.name == "disponit-sveipestatus.timer":
            continue
        mm = re.search(r"OnCalendar=\*-\*-\* (\d\d):(\d\d):00 UTC",
                       t.read_text(encoding="utf-8"))
        if mm:
            senest = max(senest, int(mm.group(1)) * 60 + int(mm.group(2)))
    assert minutt > senest, (
        f"sveipestatus kjører {minutt // 60:02d}:{minutt % 60:02d},"
        f" men siste sveip er {senest // 60:02d}:{senest % 60:02d}")
