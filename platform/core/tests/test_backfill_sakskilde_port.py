"""Porten mot #419: backfillen rører ikke saker som ikke skal ha snapshot.

CHECK-en `unntak_snapshot_komplett` (041, utvidet i 102) KREVER NULL
policysnapshot for `domeneovertakelse` og `henvendelse`. Backfillen i
`db/m37_backfill.py` unntok bare den første; første henvendelse i
unntakskøen på disponit.com (Fjordlys-kampanjen 8/9) fikk den til å fylle
snapshotet på en henvendelse, CHECK-en nektet, og hver deploy etterpå
ble avbrutt og selv-reversert.

Tre ting måles mot ekte base:
  1. En henvendelse legges i unntakskøen gjennom M-17-døra; `backfill`
     kjører uten feil, og raden står med NULL snapshot etterpå.
  2. `tenanter_uten_policysnapshot()` (141) lister ikke tenanten for den.
  3. Listen i Python, funksjonen i 141 og CHECK-en i 102 nevner de SAMME
     sakskildene — legges en til ett sted, faller porten.

MUTASJONEN SOM DREPER DENNE: ta `henvendelse` ut av
`SAKSKILDER_UTEN_SNAPSHOT`.
"""
import re
from pathlib import Path

from .test_api import DSN, MIGRATOR_DSN, TENANT, pg, migrator, miljo  # noqa: F401
from .test_m17_kundeservice import (_klassifiser, _nokkel, _rt, _ta_imot,
                                    _til_koe)
from .test_m37 import _sett_kontekst

MIGRASJONER = Path(__file__).resolve().parents[1] / "db" / "migrations"


def _henvendelse_i_koen():
    """Gjennom M-17-døra som runtime-rollen — slik disponit.com gjorde det."""
    c = _rt()
    try:
        key_id, dek = _nokkel(c, TENANT)
        hid = _ta_imot(c, TENANT, key_id, dek)
        _klassifiser(c, TENANT, hid, handlingstype="mistenkelig",
                     prioritet="kritisk")
        return _til_koe(c, TENANT, hid, key_id, dek,
                        begrunnelse="direktørsvindel")
    finally:
        c.close()


@pg
def test_backfillen_lar_henvendelsen_staa_uten_snapshot(miljo, migrator):
    from db import m37_backfill
    sak = _henvendelse_i_koen()
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT sakskilde, maks_auto_forsok_snapshot, policy_versjon,"
        " policy_content_hash FROM unntak WHERE tenant=%s AND id=%s",
        (TENANT, sak)).fetchone()
    migrator.commit()
    assert rad == ("henvendelse", None, None, None), rad
    # …og deployens backfill går gjennom uten å røre den.
    m37_backfill.backfill(migrator)
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT status, maks_auto_forsok_snapshot, policy_versjon,"
        " policy_content_hash FROM unntak WHERE tenant=%s AND id=%s",
        (TENANT, sak)).fetchone()
    migrator.commit()
    assert rad == ("ny", None, None, None), rad


@pg
def test_tellefunksjonen_teller_ikke_henvendelsen(miljo, migrator):
    _henvendelse_i_koen()
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    rader = migrator.execute(
        "SELECT tenant, antall FROM tenanter_uten_policysnapshot()").fetchall()
    migrator.commit()
    assert all(t != TENANT for t, _ in rader), rader


def test_listen_staar_ett_sted_og_speiles_i_141_og_102():
    from db import m37_backfill
    liste = set(m37_backfill.SAKSKILDER_UTEN_SNAPSHOT)
    assert liste >= {"domeneovertakelse", "henvendelse"}
    for fil in ("141_snapshotfri_sakskilde.sql",
                "102_m17_henvendelsesregister.sql"):
        kilde = (MIGRASJONER / fil).read_text(encoding="utf-8")
        m = re.search(r"sakskilde (?:NOT )?IN \(([^)]*)\)", kilde)
        assert m, fil
        i_sql = set(re.findall(r"'([a-z_]+)'", m.group(1)))
        assert i_sql == liste, (fil, i_sql, liste)
