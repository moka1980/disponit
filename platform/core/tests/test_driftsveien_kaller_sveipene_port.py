"""Porten for PR 6: to sveip som var definert, testet, GRANTet — og aldri kalt.

`platform/drift/evidensreaper.py` sier det selv, om en tidligere runde:

    «`reap_kandidatdata()` var definert, testet og GRANTet til nettopp denne
    timerrollen — og aldri kalt fra noen driftsvei (Codex P1).»
    «057s Codex P1 («definert, testet, GRANTet — og aldri kalt») skal ikke
    gjentas for M-6.»

Jeg gjentok den likevel, to ganger samme dag:

  * `reap_partkontakt` (184) — en avviklet kundes adresse ville stått til
    evig tid, selv om migrasjonen lovet en frist.
  * `firma_sveip_proveutlop` (190) — prøveperioden ville aldri tatt slutt.
    «Et gratisabonnement med en misvisende etikett», som 190 selv skrev.

Begge løftene var dokumentert, testet og grantet. Ingen av dem virket, fordi
ingen kalte funksjonen. Disse portene går gjennom `evidensreaper.kjor` —
samme funksjon `disponit-evidensreaper.service` kaller.

MUTASJONEN SOM DREPER BEGGE: fjern den tilhørende blokken fra
`evidensreaper.kjor`. Alle direktekallende porter i 184 og 190 blir grønne.
"""
import secrets

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo, pg  # noqa: F401
from .test_m37 import _sett_kontekst

NONCE = bytes(range(12))


def _reaperkobling():
    """Samme koblingsvalg som evidensreaperen selv (038/057-formen)."""
    from .test_outbox_bestilling import _reaperkobling as felles
    return felles()


def _t(prefiks):
    return f"t-{prefiks}-" + secrets.token_hex(3)


# ---------------------------------------------------------------------------
# 1. Partsregisterets retensjon (184).
# ---------------------------------------------------------------------------

@pg
def test_partsreapen_kalles_fra_driftsveien(migrator):
    from drift import evidensreaper

    from db.pg import koble

    t = _t("pdrift")
    # PARTSDØRENE ER GRANTET TIL RUNTIME, ikke til migrator. Oppsettet må gå
    # samme vei produktet går — ellers måler porten en tilgang ingen kaller
    # har (samme lærdom som `bruker_tenant`-grantet i 189).
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, t)
        pid = rt.execute(
            "SELECT part_registrer(%s,'K-1','Avviklet AS',NULL,'bedrift',"
            "'kari')", (t,)).fetchone()[0]
        psn = rt.execute("SELECT tenant_pseudonym(%s,'a@b.example')",
                         (t,)).fetchone()[0]
        kid = rt.execute(
            "SELECT part_sett_kontakt(%s,%s,'epost','a**@b.example',%s,%s,"
            "'k1',%s,true,NULL,'kari')",
            (t, pid, b"\x01\x02", NONCE, psn)).fetchone()[0]
        rt.execute("SELECT part_deaktiver(%s,%s,'kari')", (t, pid))
        rt.commit()
    finally:
        rt.close()
    _sett_kontekst(migrator, t)
    # Forbi fristen: deaktivert for lengst.
    migrator.execute("UPDATE part SET deaktivert_ts = now() - interval"
                     " '400 days' WHERE tenant=%s AND part_id=%s", (t, pid))
    migrator.commit()

    rp, _rolle = _reaperkobling()
    try:
        r = evidensreaper.kjor(rp)
        assert not r.partsdata_feilet, (
            "timerrollen har EXECUTE — en nekt her er et rettighetshull")
        assert (t, str(kid)) in r.partsdata, (
            "driftsveien kaller ikke reap_partkontakt — retensjonsløftet i "
            "184 er en dato uten en klokke")
    finally:
        rp.close()

    _sett_kontekst(migrator, t)
    rad = migrator.execute(
        "SELECT verdi_kryptert, verdi_maske, slettet_av FROM partkontakt"
        " WHERE tenant=%s AND kontakt_id=%s", (t, kid)).fetchone()
    assert rad[0] is None and rad[1] is None and rad[2] == "retensjon"
    migrator.rollback()


# ---------------------------------------------------------------------------
# 2. Prøveperioden (190).
# ---------------------------------------------------------------------------

@pg
def test_proveutlopet_kalles_fra_driftsveien(migrator):
    from drift import evidensreaper

    moden, fersk = _t("pmod"), _t("pfersk")
    for t in (moden, fersk):
        _sett_kontekst(migrator, t)
        migrator.execute("SELECT firma_registrer(%s,'Prøvefirma AS',NULL,30,"
                         "'kari')", (t,))
    _sett_kontekst(migrator, moden)
    migrator.execute("UPDATE firma SET prove_utloper = current_date - 1"
                     " WHERE tenant=%s", (moden,))
    migrator.commit()

    rp, _rolle = _reaperkobling()
    try:
        r = evidensreaper.kjor(rp)
        assert not r.proveutlop_feilet, (
            "timerrollen har EXECUTE — en nekt her er et rettighetshull")
        truffet = {t for t, _d in r.proveutlop}
        assert moden in truffet, (
            "driftsveien kaller ikke firma_sveip_proveutlop — prøveperioden "
            "tar aldri slutt")
        assert fersk not in truffet, "en fersk prøveperiode ble utløpt"
    finally:
        rp.close()

    _sett_kontekst(migrator, moden)
    assert migrator.execute("SELECT status FROM firma_hent(%s)",
                            (moden,)).fetchone()[0] == "utlopt"
    _sett_kontekst(migrator, fersk)
    assert migrator.execute("SELECT status FROM firma_hent(%s)",
                            (fersk,)).fetchone()[0] == "prove"
    migrator.rollback()


# ---------------------------------------------------------------------------
# 3. Kjøringen bærer FEM plikter, hver med sitt eget feilflagg.
# ---------------------------------------------------------------------------

@pg
def test_hver_plikt_har_sitt_eget_feilflagg(migrator):
    """En samlet «feilet: 1» ville sagt at noe gikk galt, ikke hva. Med fem
    plikter i én kjøring er det forskjellen mellom en brukbar journalctl og
    en som bare sier at noe er feil."""
    from drift import evidensreaper

    rp, _rolle = _reaperkobling()
    try:
        r = evidensreaper.kjor(rp)
    finally:
        rp.close()
    for flagg in ("feilet", "kandidatdata_feilet", "epostdata_feilet",
                  "partsdata_feilet", "proveutlop_feilet"):
        assert hasattr(r, flagg), f"{flagg} mangler i Reapresultat"
        assert getattr(r, flagg) is False, f"{flagg} var sann i en ren kjøring"
