"""M-10 (migrasjon 090) — backupinnsynet: lagringen, dørene og lesejobben.

Portene her er dommen fra natt til 1/9, gjort maskinell:

  * IDEMPOTENS — samme `backup_ts` registrert to ganger er ÉN rad.
    Lesejobben kjører hvert 30. minutt over den samme dagsferske
    rapporten; uten denne ville hver kjøring vært en ny «verifisering»,
    og historikken hadde vært en teller over hvor ofte timeren gikk.
  * FAIL-CLOSED — en rapport som mangler et felt, har feil type eller
    bryter en CHECK gir ALDRI en rad. Ikke en rad med NULL, ikke en rad
    «med forbehold», og aldri et gjettet tall: en verifisering vi ikke
    kan lese er ikke en verifisering. De tre utfallene (mangler / ugyldig
    / gyldig) er tre ULIKE exit-koder, og forskjellen er hele grunnen
    til at en fersk vert ikke er rød.
  * GRANTGRENSEN — skrivedøren er lesejobbrollens ALENE, avvist i
    RUNTIME for web-runtime. En rettighet som bare slutter å bli gitt er
    ikke trukket tilbake (035), så porten spør basen, ikke migrer.py.
  * VARSEL-DEDUPE — hendelsen er DØGNET: en tilstand som vedvarer gir
    ett varsel per dag, ikke ett per timerkjøring og ikke evig stillhet.
  * UTEBLITT-SVEIPEN — 30 timers taushet varsler, OGSÅ fra en helt tom
    tabell (fravær er feil i v1), og bare plattformtenantens aktive
    admin-medlemmer treffes.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
import json
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, VARSEL_DSN,  # noqa: F401
                       app, klient, migrator, miljo, token)

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "090_m10_backupinnsyn.sql")

#: Lesejobbens rolle. Skrivedøren er HENNES alene, så en test som skal
#: bevise at den virker må koble som henne — migratoren arver ingenting
#: (`WITH INHERIT FALSE`) og web-runtime skal nektes. Mangler DSN-en,
#: faller vi tilbake til migratoren (som er funksjonseierens medlem) for
#: de testene som bare trenger at døren gjør det den skal; grantporten
#: under hopper aldri over — den måler runtime, som alltid er satt.
DRIFTSTATUS_DSN = os.environ.get("DISPONIT_TEST_DRIFTSTATUS_DSN")


def _c():
    from db.pg import koble
    return koble(MIGRATOR_DSN)


def _rt():
    from db.pg import koble
    return koble(DSN)


def _ds():
    from db.pg import koble
    return koble(DRIFTSTATUS_DSN or MIGRATOR_DSN)


def _vs():
    from db.pg import koble
    return koble(VARSEL_DSN or MIGRATOR_DSN)


def _ts(timer_siden: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=timer_siden)


def _gyldig(backup_ts=None, **overstyr) -> dict:
    """En rapport som er innenfor ALLE CHECK-ene, med unik `backup_ts`.

    Tallene speiler backup-db.sh sine egne porter (>= 10 tabeller,
    > 1024 B) med god margin — testene under varierer ett felt om gangen
    fra denne, så det som feller en rad alltid er det ene feltet.
    """
    rapport = {
        "backup_ts": (backup_ts or _ts(1)).isoformat(),
        "verifisert_ts": _ts(0.5).isoformat(),
        "restore_varighet_s": 42.5,
        "tabeller": 137,
        "storrelse_b": 8_388_608,
    }
    rapport.update(overstyr)
    return rapport


def _skriv(sti: Path, rapport) -> Path:
    sti.write_text(rapport if isinstance(rapport, str)
                   else json.dumps(rapport), encoding="utf-8")
    return sti


# ---------------------------------------------------------------------------
# Lagringen: CHECK-ene ER portene, og de er basens.
# ---------------------------------------------------------------------------

@pg
def test_check_grensene_avviser_en_verifisering_som_maalte_feil_base():
    """>= 10 tabeller og > 1024 B er ikke svake målinger — de er tegn på
    at restoren traff feil base. Kontroll: fjern en CHECK i 090, så blir
    den tilsvarende asserten grønn der den skal være rød."""
    m = _c()
    try:
        for felt, verdi in (("tabeller", 9), ("storrelse_b", 1024),
                            ("restore_varighet_s", -1)):
            r = _gyldig()
            r[felt] = verdi
            with pytest.raises(psycopg.errors.CheckViolation):
                m.execute(
                    "INSERT INTO backupverifisering (backup_ts,"
                    " verifisert_ts, restore_varighet_s, tabeller,"
                    " storrelse_b) VALUES (%s,%s,%s,%s,%s)",
                    (r["backup_ts"], r["verifisert_ts"],
                     r["restore_varighet_s"], r["tabeller"],
                     r["storrelse_b"]))
            m.rollback()
    finally:
        m.close()


@pg
def test_tabellen_har_ingen_tenantkolonne():
    """PLATTFORMSKOP MED VILJE (dommen): backupen er hele basens, ikke en
    tenants. En tenant-kolonne her ville vært en invitasjon til å filtrere
    på noe som ikke finnes — og til å tro at radene bærer kundedata."""
    m = _c()
    try:
        kolonner = {r[0] for r in m.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='backupverifisering'").fetchall()}
        m.rollback()
        assert kolonner == {"backup_ts", "verifisert_ts",
                            "restore_varighet_s", "tabeller",
                            "storrelse_b", "registrert"}, kolonner
    finally:
        m.close()


# ---------------------------------------------------------------------------
# Skrivedøren: idempotens og grantgrensen.
# ---------------------------------------------------------------------------

@pg
def test_registrering_er_idempotent_paa_backup_ts():
    """
    INVARIANT: `registrering_duplikat_backup_ts` — lesejobben kjører hvert 30.
    minutt over den samme dagsferske rapporten; uten dette ville hver kjøring
    vært en ny «verifisering».
    
    Samme backup verifisert to ganger er ÉN rad, og andre kall
    returnerer 0 — ikke 1, og ikke en feil. Kontroll: fjern
    `ON CONFLICT (backup_ts) DO NOTHING` i 090, så velter andre kall på
    en UniqueViolation i stedet for å svare 0."""
    d = _ds()
    m = _c()
    try:
        r = _gyldig()
        forste = d.execute(
            "SELECT registrer_backupverifisering("
            "%s::timestamptz,%s::timestamptz,%s::numeric,%s::int,%s::bigint)",
            (r["backup_ts"], r["verifisert_ts"], r["restore_varighet_s"],
             r["tabeller"], r["storrelse_b"])).fetchone()[0]
        d.commit()
        # Andre kall med ANDRE måletall på samme backup: raden skal stå
        # urørt. En «oppdatering» her ville latt en senere, dårligere
        # måling overskrive den som faktisk ble gjort.
        andre = d.execute(
            "SELECT registrer_backupverifisering("
            "%s::timestamptz,%s::timestamptz,%s::numeric,%s::int,%s::bigint)",
            (r["backup_ts"], r["verifisert_ts"], 999, 999,
             99_999_999)).fetchone()[0]
        d.commit()
        assert (forste, andre) == (1, 0), (forste, andre)
        rad = m.execute(
            "SELECT tabeller, storrelse_b FROM backupverifisering"
            " WHERE backup_ts=%s", (r["backup_ts"],)).fetchone()
        m.rollback()
        assert rad == (137, 8_388_608), rad
    finally:
        d.close()
        m.close()


@pg
def test_runtime_avvises_paa_skrivedoeren_i_runtime():
    """
    INVARIANT: `skrivedor_naadd_av_runtime` — en rettighet som bare slutter å
    bli gitt er ikke trukket tilbake (035), så avvisningen måles i RUNTIME og
    ikke i en kodegjennomgang.
    
    GRANTGRENSEN, målt i basen og ikke i migrer.py: web-runtime skal
    ikke kunne dikte en verifisering. En rettighet som bare slutter å bli
    GITT er ikke trukket tilbake (035) — derfor spør vi den ekte rollen.

    Kontroll: gi `disponit` EXECUTE på skrivedøren, så blir denne rød."""
    rt = _rt()
    try:
        r = _gyldig()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute(
                "SELECT registrer_backupverifisering("
            "%s::timestamptz,%s::timestamptz,%s::numeric,%s::int,%s::bigint)",
                (r["backup_ts"], r["verifisert_ts"],
                 r["restore_varighet_s"], r["tabeller"],
                 r["storrelse_b"]))
        rt.rollback()
        # …og heller ikke direkte i tabellen: dørene er den ENESTE veien
        # inn (SP-7), så et manglende EXECUTE er ikke et vern hvis
        # tabellen står åpen ved siden av.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("INSERT INTO backupverifisering (backup_ts,"
                       " verifisert_ts, restore_varighet_s, tabeller,"
                       " storrelse_b) VALUES (now(),now(),1,20,99999)")
        rt.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT count(*) FROM backupverifisering")
        rt.rollback()
    finally:
        rt.close()


@pg
def test_runtime_avvises_paa_sveipen():
    """Sveipen tar tenanten som PARAMETER og setter DENS RLS-kontekst. Et
    grant til web-runtime ville gitt forespørselsveien nøyaktig det
    kryss-tenant-vinduet senderrollen finnes for å nekte den."""
    rt = _rt()
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT varsle_backupverifisering_uteblitt(%s)",
                       ("disponit",))
        rt.rollback()
    finally:
        rt.close()


# ---------------------------------------------------------------------------
# Lesedøren: tenantkontekst kreves, radfakta ut.
# ---------------------------------------------------------------------------

@pg
def test_lesedoeren_krever_tenantkontekst():
    """051-formen: dataene er plattformens, men RETTEN til å spørre er
    øktens. Uten kontekst skal døren nekte — ikke svare tomt."""
    rt = _rt()
    try:
        with pytest.raises(psycopg.Error):
            rt.execute("SELECT * FROM backup_status(%s,%s)",
                       ("t-uten-kontekst", 5)).fetchall()
        rt.rollback()
    finally:
        rt.close()


@pg
def test_lesedoeren_gir_alder_regnet_i_basen():
    """`alder_s` regnes i SAMME skann som radene, så flaten aldri trekker
    to tidspunkter fra hverandre (M-16-regelen anvendt på en
    subtraksjon)."""
    from db.pg import sett_kontekst
    d = _ds()
    rt = _rt()
    ten = "t-m10-" + secrets.token_hex(3)
    try:
        r = _gyldig(backup_ts=_ts(5))
        r["verifisert_ts"] = _ts(4).isoformat()
        d.execute("SELECT registrer_backupverifisering("
            "%s::timestamptz,%s::timestamptz,%s::numeric,%s::int,%s::bigint)",
                  (r["backup_ts"], r["verifisert_ts"],
                   r["restore_varighet_s"], r["tabeller"],
                   r["storrelse_b"]))
        d.commit()
        sett_kontekst(rt, ten, "sys", "r0")
        rader = rt.execute("SELECT * FROM backup_status(%s,%s)",
                           (ten, 100)).fetchall()
        rt.rollback()
        min_rad = [x for x in rader
                   if x[0].isoformat() == r["backup_ts"]]
        assert len(min_rad) == 1, rader
        alder = min_rad[0][6]
        # Fire timer siden verifiseringen, med rundelig slark for at
        # testen kan bruke noen sekunder.
        assert 4 * 3600 - 60 <= alder <= 4 * 3600 + 300, alder
        # Nyeste først: dørens ORDER BY, ikke klientens sortering.
        ts_er = [x[1] for x in rader]
        assert ts_er == sorted(ts_er, reverse=True)
    finally:
        d.close()
        rt.close()


# ---------------------------------------------------------------------------
# Sveipen: taushet varsles, én gang per døgn, bare til plattformadminene.
# ---------------------------------------------------------------------------

def _plattformtenant(m, ten):
    """Tenant med én aktiv admin og én ikke-admin. Sveipen skal treffe
    nøyaktig den første."""
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
def _uten_ferske_verifiseringer(m):
    """Skyv HELE tabellen forbi 30-timersterskelen, og skyv den tilbake.

    Sveipen er PLATTFORMVID — den ser hver rad i basen, også dem andre
    tester har lagt igjen. En test som bare hoppet over seg selv når en
    fersk rad fantes ville vært rekkefølgeavhengig, og en hoppet test er
    ikke en bestått test (CI feller den).

    Skyvingen er REVERSIBEL og eksakt: samme intervall begge veier, så
    ingen annen test ser en endret verdi etterpå. Migratoren eier
    tabellen; sveipen kjøres av senderrollen i sin egen forbindelse, så
    skyvingen må være COMMITTET for at den skal se den.
    """
    m.execute("UPDATE backupverifisering"
              " SET verifisert_ts = verifisert_ts - interval '100 days'")
    m.commit()
    try:
        yield
    finally:
        m.execute("UPDATE backupverifisering"
                  " SET verifisert_ts = verifisert_ts + interval '100 days'")
        m.commit()


@pg
def test_uteblitt_varsles_og_er_idempotent_per_dogn():
    """
    INVARIANT: `uteblitt_ikke_varslet` OG `varsel_duplikat_per_dogn` — en
    backup ingen har verifisert ser lik ut som en som virker, og hendelsen er
    DØGNET: ett varsel per dag, ikke ett per timerkjøring og ikke evig
    stillhet etter det første.
    
    FRAVÆR ER FEIL i v1: en tabell der ingenting er verifisert de siste
    30 timene varsler — og en tilstand som vedvarer gir ETT varsel per
    døgn, ikke ett per timerkjøring.

    Kontroll: fjern `ON CONFLICT DO NOTHING` i sveipen, så dobler
    antallet ved andre kjøring; fjern `admin = ANY(roller)`, så treffer
    varselet også leseren."""
    m = _c()
    vs = _vs()
    ten = "t-m10sveip-" + secrets.token_hex(3)
    try:
        admin, leser = _plattformtenant(m, ten)
        with _uten_ferske_verifiseringer(m):
            vs.execute("SELECT varsle_backupverifisering_uteblitt(%s)",
                       (ten,))
            vs.commit()
            from db.pg import sett_kontekst
            sett_kontekst(m, ten, "sys", "r1")
            rader = m.execute(
                "SELECT bruker_id, ressurs_type, ressurs_id, tekstnokkel,"
                " parametre FROM varsel WHERE tenant=%s"
                " AND art='backupverifisering_uteblitt'", (ten,)).fetchall()
            m.rollback()
            assert len(rader) == 1, rader
            assert rader[0][0] == admin, "varselet traff andre enn adminen"
            assert rader[0][1] == "backupverifisering"
            assert rader[0][3] == "varsel.backupverifisering_uteblitt"
            assert rader[0][4]["terskel_timer"] == 30, rader[0][4]
            assert leser not in [r[0] for r in rader]

            vs.execute("SELECT varsle_backupverifisering_uteblitt(%s)",
                       (ten,))
            vs.commit()
            sett_kontekst(m, ten, "sys", "r2")
            n = m.execute("SELECT count(*) FROM varsel WHERE tenant=%s"
                          " AND art='backupverifisering_uteblitt'",
                          (ten,)).fetchone()[0]
            m.rollback()
            assert n == 1, f"sveipen køet {n} varsler for samme døgn"
    finally:
        vs.close()
        m.close()


@pg
def test_fersk_verifisering_gir_ingen_varsel():
    """Den positive siden av terskelen: er backupen verifisert innenfor
    30 timer, er det ingenting å si fra om."""
    m = _c()
    vs = _vs()
    d = _ds()
    ten = "t-m10fersk-" + secrets.token_hex(3)
    try:
        _plattformtenant(m, ten)
        r = _gyldig(backup_ts=_ts(2))
        r["verifisert_ts"] = _ts(1).isoformat()
        d.execute("SELECT registrer_backupverifisering("
            "%s::timestamptz,%s::timestamptz,%s::numeric,%s::int,%s::bigint)",
                  (r["backup_ts"], r["verifisert_ts"],
                   r["restore_varighet_s"], r["tabeller"],
                   r["storrelse_b"]))
        d.commit()
        n = vs.execute("SELECT varsle_backupverifisering_uteblitt(%s)",
                       (ten,)).fetchone()[0]
        vs.commit()
        assert n == 0, n
    finally:
        d.close()
        vs.close()
        m.close()


# ---------------------------------------------------------------------------
# Lesejobben: de tre utfallene, og at ingen av dem gjetter.
# ---------------------------------------------------------------------------

def test_rapport_som_mangler_er_den_MILDE_tilstanden(tmp_path):
    """Fil finnes ikke → `None`, ikke et unntak. En fersk vert uten
    backuphistorikk er ikke en feilet lesejobb; den tilstanden fanges av
    sveipen etter 30 timer, der den når et menneske."""
    from drift import backupstatus
    assert backupstatus.les_rapport(tmp_path / "finnes-ikke.json") is None


def test_ugyldig_rapport_er_den_HARDE_tilstanden(tmp_path):
    """
    INVARIANT: `ugyldig_rapport_registrert` — en verifisering vi ikke kan lese
    er ikke en verifisering, og skal aldri gi en rad «med forbehold».
    
    Ugyldig JSON, manglende felt og feil type er alle `UgyldigRapport`
    — og ALDRI en utfylt verdi. Kontroll: la `_gyldig` konvertere
    strenger til tall, så blir `"137"`-tilfellet grønt der det skal være
    rødt."""
    from drift import backupstatus
    sti = tmp_path / "rapport.json"

    for tilfelle in ("ikke json i det hele tatt", "[]", '"streng"', ""):
        _skriv(sti, tilfelle)
        with pytest.raises(backupstatus.UgyldigRapport):
            backupstatus.les_rapport(sti)

    for felt in ("backup_ts", "verifisert_ts", "restore_varighet_s",
                 "tabeller", "storrelse_b"):
        r = _gyldig()
        del r[felt]
        _skriv(sti, r)
        with pytest.raises(backupstatus.UgyldigRapport):
            backupstatus.les_rapport(sti)

    # TYPENE TOLKES IKKE. En streng blir ikke et tall, og `true` er ikke
    # en tabelltelling — begge er tegn på at rapporten kommer fra noe
    # annet enn backup-db.sh.
    for felt, verdi in (("tabeller", "137"), ("tabeller", True),
                        ("storrelse_b", None), ("restore_varighet_s", "42"),
                        ("backup_ts", 17)):
        r = _gyldig()
        r[felt] = verdi
        _skriv(sti, r)
        with pytest.raises(backupstatus.UgyldigRapport):
            backupstatus.les_rapport(sti)

    # …og en rapport med EKSTRA felt er gyldig: den er skrevet av en
    # nyere backup enn jobben kjenner, og de fem er fortsatt sanne.
    _skriv(sti, _gyldig(nytt_felt="noe"))
    assert backupstatus.les_rapport(sti)["tabeller"] == 137


@pg
def test_kjoringen_skriver_ingenting_naar_basen_avviser(tmp_path):
    """CHECK-en er BASENS, ikke jobbens. En rapport som er velformet JSON
    men bryter en grense skal gi 0 rader og en `grunn` — aldri en rad."""
    from drift import backupstatus
    d = _ds()
    m = _c()
    try:
        sti = _skriv(tmp_path / "r.json", _gyldig(tabeller=3))
        res = backupstatus.kjor(d, sti=sti)
        assert res.skrevet == 0 and res.grunn and not res.mangler, res
        rad = m.execute("SELECT count(*) FROM backupverifisering"
                        " WHERE tabeller = 3").fetchone()[0]
        m.rollback()
        assert rad == 0, "en CHECK-brytende rad ble skrevet"
    finally:
        d.close()
        m.close()


@pg
def test_kjoringen_er_idempotent_over_samme_rapport(tmp_path):
    """Timeren går hvert 30. minutt over den samme dagsferske rapporten.
    Første kjøring skriver, resten skriver ingenting — det er hele
    grunnen til at kadensen kan være tettere enn backupens egen."""
    from drift import backupstatus
    d = _ds()
    try:
        sti = _skriv(tmp_path / "r.json", _gyldig(backup_ts=_ts(3)))
        forste = backupstatus.kjor(d, sti=sti)
        andre = backupstatus.kjor(d, sti=sti)
        assert (forste.skrevet, andre.skrevet) == (1, 0), (forste, andre)
        assert forste.grunn is None and andre.grunn is None
    finally:
        d.close()


def test_inngangspunktets_exitkoder_skiller_de_tre_utfallene(tmp_path,
                                                             monkeypatch):
    """0 for «ingen rapport ennå», 1 for «rapporten er ugyldig», 2 for
    «jobben kunne ikke starte». En vert uten backuphistorikk som ga exit 1
    ville vært rød hvert 30. minutt — og en jobb som alltid er rød, er en
    jobb ingen ser på."""
    from drift import kjor_backupstatus
    monkeypatch.setattr(kjor_backupstatus, "__name__", "kjor_backupstatus")
    monkeypatch.setenv("DISPONIT_BACKUPRAPPORT",
                       str(tmp_path / "finnes-ikke.json"))
    monkeypatch.delenv("DISPONIT_DRIFTSTATUS_URL", raising=False)
    assert kjor_backupstatus.main() == 2

    monkeypatch.setenv("DISPONIT_DRIFTSTATUS_URL",
                       "postgresql://ingen@127.0.0.1:1/ingen")
    # Rapporten leses FØR tilkoblingen: fravær skal svare 0 uten å røre
    # basen i det hele tatt, og en DSN som ikke kan kobles beviser det.
    assert kjor_backupstatus.main() == 0

    monkeypatch.setenv("DISPONIT_BACKUPRAPPORT",
                       str(_skriv(tmp_path / "r.json", "ikke json")))
    assert kjor_backupstatus.main() == 1


# ---------------------------------------------------------------------------
# Statiske porter: enhetene og skriveveien i backup-db.sh.
# ---------------------------------------------------------------------------

def test_uniten_er_hardnet_og_leser_backupkatalogen_readonly():
    """Jobben kjører som root fordi katalogen er roots — og betaler for
    det med sandkassen, ikke med en løsere katalog. Kontroll: bytt
    `ReadOnlyPaths` til `ReadWritePaths`, så blir denne rød."""
    unit = (ROT / "deploy" / "staging"
            / "disponit-backupstatus.service").read_text(encoding="utf-8")
    for linje in ("Type=oneshot", "User=root", "ProtectSystem=strict",
                  "NoNewPrivileges=true", "CapabilityBoundingSet=",
                  "ReadOnlyPaths=/var/backups/disponit",
                  "LoadCredential=DISPONIT_DRIFTSTATUS_URL:"):
        assert linje in unit, f"unitfila mangler {linje!r}"
    assert "ReadWritePaths" not in unit, \
        "lesejobben har ingen grunn til å skrive i backupkatalogen"
    timer = (ROT / "deploy" / "staging"
             / "disponit-backupstatus.timer").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=30min" in timer
    assert "Persistent=true" in timer


def test_backupskriptet_skriver_rapporten_med_sine_egne_tall():
    """Broen fra backupen til tabellen er `siste-verifisering.json`, og
    tallene i den er kjøringens EGNE — de samme `$TABELLER` og
    `$STORRELSE` som portene over alt har felt kjøringen på. Rapporten
    skrives ATOMISK og først etter at paret er publisert: en rapport om
    en backup som ikke ble stående, er en løgn."""
    sh = (ROT / "deploy" / "staging"
          / "backup-db.sh").read_text(encoding="utf-8")
    assert "siste-verifisering.json" in sh
    assert 'mv -f "$STATUS_TMP" "$STATUSFIL"' in sh, \
        "rapporten skrives ikke atomisk — lesejobben kan se en halv fil"
    assert "RESTORE_VARIGHET_S=" in sh
    # Rapporten må stå ETTER at paret er publisert (`PAR_KLAR=1`).
    assert sh.index("PAR_KLAR=1") < sh.index("STATUSFIL="), \
        "rapporten skrives før paret er publisert"
    # Feltnavnene må være NØYAKTIG de lesejobben krever — en drift her
    # ville gitt exit 1 hver halvtime, med en ærlig «feltet mangler».
    from drift import backupstatus
    for navn, _ in backupstatus.FELTER:
        assert f'"{navn}":' in sh, f"backup-db.sh skriver ikke {navn}"


def test_migrasjonen_navngir_ikke_runtime_rollen():
    """057-lærdommen: migrasjonen skal ikke ha en hardkodet mening om hva
    runtime heter. `migrer.py` er autoritativ for den konfigurerte
    rollens EXECUTE på lesedøren."""
    sql = MIGRASJON.read_text(encoding="utf-8")
    assert "GRANT EXECUTE ON FUNCTION backup_status" not in sql, \
        "migrasjonen granter lesedøren selv — det er migrer.py sin jobb"
    # De to VALGFRIE rollene grantes bak pg_roles-vakt (roller er
    # klyngeobjekter en migrasjon aldri kan anta).
    for rolle in ("disponit_driftstatus", "disponit_varselsender"):
        assert f"WHERE rolname = '{rolle}'" in sql, \
            f"{rolle} grantes uten pg_roles-vakt"


# ---------------------------------------------------------------------------
# HTTP-flaten: scopet, formen og at ingen mutasjon finnes.
# ---------------------------------------------------------------------------

@pg
def test_endepunktet_krever_security_read_og_gir_radfakta(
        klient, token, migrator):     # noqa: F811
    """`GET /v1/drift/backup` er plattformdriftens innsyn, bak
    admin-lesescopet. Et kundetoken uten `security:read` skal avvises —
    og et med skal få radene, med alderen regnet i basen."""
    from .test_api import TENANT
    d = _ds()
    try:
        r = _gyldig(backup_ts=_ts(6))
        d.execute("SELECT registrer_backupverifisering("
                  "%s::timestamptz,%s::timestamptz,%s::numeric,%s::int,"
                  "%s::bigint)",
                  (r["backup_ts"], r["verifisert_ts"],
                   r["restore_varighet_s"], r["tabeller"],
                   r["storrelse_b"]))
        d.commit()
    finally:
        d.close()

    uten, _ = token(scopes=["decisions:read"])
    svar = klient.get("/v1/drift/backup",
                      headers={"authorization": f"Bearer {uten}"})
    assert svar.status_code == 403, svar.text

    med, _ = token(tenant=TENANT, rolle="admin", scopes=["security:read"])
    svar = klient.get("/v1/drift/backup",
                      headers={"authorization": f"Bearer {med}"})
    assert svar.status_code == 200, svar.text
    kropp = svar.json()
    mine = [v for v in kropp["verifiseringer"]
            if v["backup_ts"] == r["backup_ts"]]
    assert len(mine) == 1, kropp
    v = mine[0]
    assert v["tabeller"] == 137 and v["storrelse_b"] == 8_388_608
    # `numeric` må ut som et TALL, ikke som en Decimal-streng.
    assert isinstance(v["restore_varighet_s"], float), v
    assert v["alder_s"] > 0
    # Nyeste først, som døren leverte dem.
    ts_er = [x["verifisert_ts"] for x in kropp["verifiseringer"]]
    assert ts_er == sorted(ts_er, reverse=True)


@pg
def test_endepunktet_har_ingen_skrivevei(klient, token):   # noqa: F811
    """Verifiseringer skrives av lesejobbens EGEN rolle. Det finnes ingen
    HTTP-dør, og det er en sikkerhetsdom — ikke en manglende funksjon."""
    med, _ = token(rolle="admin", scopes=["security:read"])
    h = {"authorization": f"Bearer {med}"}
    for metode in ("post", "put", "delete", "patch"):
        svar = getattr(klient, metode)("/v1/drift/backup", headers=h)
        assert svar.status_code == 405, (metode, svar.status_code)


def test_ui_axe_dekning():
    """`ui_axe_alvorlige_brudd` — flaten er registrert der axe kjører.

    M-10 og M-11 DELER flate (`driftstatus`): den henter begge
    endepunktene i ett kall-par, så axe-dekningen er felles.
    """
    rot = Path(__file__).resolve().parents[3]
    app_js = (rot / "platform" / "core" / "ui" / "static" / "js"
              / "app.js").read_text(encoding="utf-8")
    assert "driftstatus: vis" in app_js
    sitekart = (rot / "platform" / "core" / "ui" / "static" / "js"
                / "sitekart.js").read_text(encoding="utf-8")
    assert '{ nokkel: "driftstatus"' in sitekart


def test_grensen_dekkes_av_portene_i_denne_fila():
    """§0, MÅLT BEGGE VEIER — OG DET VAR HALVPARTEN SOM MANGLET.

    Grensen `m10-v1` har stått i `KRAVGRENSER` siden FØR koden ble
    skrevet: §0-regelen ble respektert. Portene under har ligget her
    siden. MEN INGENTING BANDT DE TO SAMMEN.

    Konsekvensen er stille: en invariant kunne fjernes fra grensen,
    eller en port slettes, og ingen test ville merket det. Grensen ville
    fremdeles vært «registrert», og suiten fremdeles grønn.

    `test_kravgrenser_unike.py` pinner at en grense ikke OVERSKRIVES.
    Denne pinner at den er DEKKET. De to er ulike hull, og bare det
    første var lukket.

    MUTASJONEN SOM DREPER DENNE: legg til en invariant i `m10-v1` som
    ingen test her nevner.
    """
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m10-v1"]
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    inv = set(g["invarianter"])
    assert inv
    egen = Path(__file__).read_text(encoding="utf-8")
    mangler = sorted(i for i in inv if i not in egen)
    assert mangler == [], f"invarianter uten port: {mangler}"
