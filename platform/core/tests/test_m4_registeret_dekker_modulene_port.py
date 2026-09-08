"""Porten mot #424: hvert modullager med tidskolonne staar i M-4s register.

M-30 nekter en personvernsak som navngir et lager utenfor registeret;
bare plattformen og fem moduler hadde registrert sine. En
innsynsbegjaering kunne ikke peke paa loenn, medarbeider, kundeservice,
kampanje eller faktura (Fjordlys 8/9). 143 navngir alle tenant-tabeller
fra 094–136 med aapen frist.

To ting maales:
  1. Kilden: hver `CREATE TABLE … tenant TEXT` i 094–136 med en
     TIMESTAMPTZ-kolonne har en rad i registeret (migrasjonene lest),
     og basen har radene (mot ekte base).
  2. HTTP: en personvernsak kan navngi loenn og kundeservice → 200.

MUTASJONEN SOM DREPER DENNE: fjern én rad fra 143.
"""
import re
import secrets
from pathlib import Path

from .test_api import (DSN, MIGRATOR_DSN, TENANT, pg,  # noqa: F401
                       app, klient, migrator, miljo, token)

MIGRASJONER = Path(__file__).resolve().parents[1] / "db" / "migrations"


def _registrerte() -> set[str]:
    ut = set()
    for fil in MIGRASJONER.glob("*.sql"):
        s = fil.read_text(encoding="utf-8")
        if "INSERT INTO retensjonslager" not in s:
            continue
        # Radene staar som `('lager_id', 'relasjon', 'klasse', …` — leses
        # linje for linje, uavhengig av semikolon i begrunnelsene.
        for _lid, rel in re.findall(
                r"^\s+\('([a-z0-9_]+)',\s*'([a-z0-9_]+)',\s*'(?:persondata"
                r"|evidens|driftsspor|konfigurasjon)'", s, re.M):
            ut.add(rel)
    return ut


def _tenanttabeller_med_tid() -> list[str]:
    ut = []
    for fil in sorted(MIGRASJONER.glob("*.sql")):
        nr = int(fil.name[:3])
        if not 94 <= nr <= 136 or not re.search(r"_m\d+_", fil.name):
            continue
        s = fil.read_text(encoding="utf-8")
        for m in re.finditer(
                r"CREATE TABLE (?:IF NOT EXISTS )?(?:public\.)?([a-z0-9_]+)"
                r"\s*\((.*?)\n\);", s, re.S):
            kropp = m.group(2)
            if re.search(r"\btenant\s+TEXT", kropp) \
                    and re.search(r"^\s+[a-z_]+\s+TIMESTAMPTZ", kropp, re.M):
                ut.append(m.group(1))
    return ut


def test_hver_modultabell_med_tidskolonne_er_navngitt_i_kilden():
    tabeller = _tenanttabeller_med_tid()
    assert len(tabeller) >= 150, len(tabeller)
    mangler = sorted(set(tabeller) - _registrerte())
    assert not mangler, mangler


@pg
def test_basen_har_radene(miljo, migrator):
    tabeller = _tenanttabeller_med_tid()
    migrator.execute("SET LOCAL ROLE disponit_lager_eier")   # eier leser
    i_basen = {r[0] for r in migrator.execute(
        "SELECT relasjon FROM retensjonslager").fetchall()}
    migrator.rollback()
    mangler = sorted(set(tabeller) - i_basen)
    assert not mangler, mangler


@pg
def test_en_personvernsak_kan_navngi_loenn_og_kundeservice(miljo, migrator,
                                                            klient, token):
    from .test_m37 import _sett_kontekst
    tok, tid = token(rolle="admin", scopes=("security:write",
                                             "bestilling:opprett"))
    # Eieren maa vaere et aktivt medlem — lag ett.
    _sett_kontekst(migrator, TENANT)
    bid = migrator.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://m4port.test', %s) RETURNING bruker_id",
        ("m4-" + secrets.token_hex(6),)).fetchone()[0]
    migrator.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,%s,true)", (TENANT, bid, ["admin"]))
    migrator.commit()
    r = klient.post("/v1/personvern", json={
        "type": "innsyn", "subjekt_ref": "tidl-ansatt-" + secrets.token_hex(3),
        "eier_bruker_id": bid, "mottatt": "2026-09-01",
        "lager_id": ["m39_lonnstaker", "m17_henvendelse", "m44_kampanjemottaker"]},
        headers={"authorization": f"Bearer {tok}",
                 "Idempotency-Key": secrets.token_urlsafe(24)})
    assert r.status_code == 200, r.text
