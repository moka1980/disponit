"""Bootstrap må skrive ANKERRADEN, ikke bare `policyer.aktiv`.

Feilen i produksjon: eier attesterte `tjenestebedrift-no`, fire-øyne-gaten
passerte, og aktiveringen døde med HTTP 500 —

    psycopg.errors.UniqueViolation: en_aktiv_per_policy
    i aktiver_policy() → INSERT INTO policyer (..., aktiv=true)

`init-tenant.sh` seeder policyen via `policyregister.registrer()`, som satte
`policyer.aktiv = true` og aldri opprettet `policy_hode`. Den styrte
aktiveringen leser PEKEREN (`policy_hode.aktiv_versjon`), så den så NULL,
hoppet over «deaktiver forrige» og kolliderte med delindeksen. Det rammet
FØRSTE styrte aktivering for enhver tenant satt opp på normalt vis — ikke bare
den ene.

De to sannhetene om «hva er aktivt» må ikke kunne komme fra hverandre, for
ingen av dem er avledet av den andre.
"""
import secrets

import pytest

from api import policyregister as pr

from .test_api import MIGRATOR_DSN

pg = pytest.mark.skipif(not MIGRATOR_DSN,
                        reason="DISPONIT_MIGRATOR_DSN ikke satt")
TEN = "t-anker-" + secrets.token_hex(3)


def _mig(tenant):
    from db.pg import koble, sett_kontekst
    c = koble(MIGRATOR_DSN)
    sett_kontekst(c, tenant, "sys", "r0")
    return c


def _commit_og_les(c, tenant):
    """Commit, og SETT KONTEKSTEN PÅ NYTT før lesingen.

    `sett_kontekst` bruker SET LOCAL — den lever i transaksjonen. Leser man
    etter COMMIT uten å sette den igjen, filtrerer RLS bort ALT, og spørringen
    svarer tomt uansett om raden finnes. Første utgave av denne testen gjorde
    nettopp det: den var rød mot en fungerende fiks, og ville vært like rød mot
    en ødelagt. En tom RLS-lesing er ikke et fravær av data.
    """
    from db.pg import sett_kontekst
    c.commit()
    sett_kontekst(c, tenant, "sys", "r1")


def _policy(versjon: str, status: str = "produksjon",
            policy_id: str = "anker-test") -> dict:
    """Minste GYLDIGE policy — validatoren kjører inne i `registrer`."""
    return {
        "schema_version": "0.2",
        "meta": {"policy_id": policy_id, "versjon": versjon,
                 "status": status, "bransjemal": "plattform"},
        "tidssone": "Europe/Oslo",
        "dataklasser": ["offentlig"],
        "roller": [{"id": "agent"}],
        "verifikatorer": {"v1": {"betrodd_for": ["fire_oyne"],
                                 "kan_fastsla_permanent": False}},
        "unntak": {"kategorier": ["over_grense", "manglende_data",
                                  "regelkonflikt", "teknisk_feil",
                                  "ugyldig_data", "ukjent"], "maks_auto_forsok": 0,
                   "eskalering": "unntakskø"},
        "handlinger": [{"id": "faktura.bokfor", "modul": "M-14",
                        "modus": "auto_med_vilkaar",
                        "ved_brudd": "unntakskø",
                        "reversering": {"type": "kompenserende",
                                        "handling": "faktura.krediter"},
                        "dataklasser_tillatt": ["offentlig"],
                        "vilkaar": [{"navn": "fire_oyne", "verifikator": "v1"}],
                        "tillatt_for": ["agent"],
                        "grenser": {"belop_maks": "25000.00",
                                    "valuta": ["NOK"]}}],
    }


@pg
def test_registrer_skriver_ankerraden():
    """`aktiver=True` skal sette pekeren, ikke bare flagget.

    Kontroll: fjern `INSERT INTO policy_hode` i `registrer`, så blir denne rød
    med aktiv_versjon = None.
    """
    tenant = TEN + "-a"
    c = _mig(tenant)
    p = _policy("1.0.0")
    try:
        pr.registrer(c, tenant, p, "produksjon")
        _commit_og_les(c, tenant)
        rad = c.execute(
            "SELECT aktiv_versjon FROM policy_hode"
            " WHERE tenant=%s AND policy_id='anker-test'", (tenant,)).fetchone()
        # Positiv kontroll først: ser vi ikke engang policyraden, måler vi
        # RLS-kontekst og ikke ankerraden.
        assert c.execute(
            "SELECT count(*) FROM policyer WHERE tenant=%s AND aktiv",
            (tenant,)).fetchone()[0] == 1, "policyraden er ikke lesbar — testen måler ingenting"
        assert rad is not None, "ankerraden ble aldri opprettet"
        assert rad[0] == "1.0.0", f"pekeren peker på {rad[0]!r}, ikke på versjon 1.0.0"
    finally:
        c.close()


@pg
def test_flagg_og_peker_er_enige_ogsaa_etter_ny_versjon():
    """Invarianten, ikke bare førstegangstilfellet.

    Registreres en ny aktiv versjon, skal pekeren flytte seg med. En peker som
    blir stående på forrige versjon er samme sykdom, bare senere: den styrte
    aktiveringen ville deaktivert FEIL rad og kollidert på nytt.
    """
    tenant = TEN + "-b"
    c = _mig(tenant)
    try:
        pr.registrer(c, tenant, _policy("1.0.0"), "produksjon")
        pr.registrer(c, tenant, _policy("2.0.0"), "produksjon")
        _commit_og_les(c, tenant)
        aktive = c.execute(
            "SELECT versjon FROM policyer"
            " WHERE tenant=%s AND policy_id='anker-test' AND aktiv",
            (tenant,)).fetchall()
        peker = c.execute(
            "SELECT aktiv_versjon FROM policy_hode"
            " WHERE tenant=%s AND policy_id='anker-test'", (tenant,)).fetchone()
        assert [r[0] for r in aktive] == ["2.0.0"], "det skal være nøyaktig én aktiv"
        assert peker[0] == "2.0.0", (
            f"pekeren ({peker[0]!r}) er ikke enig med flagget ('2.0.0') — "
            "nøyaktig usynken som ga UniqueViolation i produksjon")
    finally:
        c.close()


@pg
def test_ikke_aktiverende_registrering_roerer_ikke_pekeren():
    """`aktiver=False` legger inn en versjon UTEN å gjøre den gjeldende.

    Uten denne ville fiksen kunne «rettes» ved å alltid skrive pekeren, og da
    ville en ren arkivregistrering stille overtatt som aktiv policy.
    """
    tenant = TEN + "-c"
    c = _mig(tenant)
    try:
        pr.registrer(c, tenant, _policy("1.0.0"), "produksjon")
        pr.registrer(c, tenant, _policy("2.0.0"), "produksjon", aktiver=False)
        _commit_og_les(c, tenant)
        peker = c.execute(
            "SELECT aktiv_versjon FROM policy_hode"
            " WHERE tenant=%s AND policy_id='anker-test'", (tenant,)).fetchone()
        assert peker[0] == "1.0.0", (
            f"en ikke-aktiverende registrering flyttet pekeren til {peker[0]!r}")
    finally:
        c.close()


@pg
def test_aktiver_false_paa_gjeldende_versjon_avvises():
    """Speilbildet av rotårsaken — og verre.

    Upserten setter `aktiv = EXCLUDED.aktiv`. Kalles `registrer` med SAMME
    versjon og `aktiver=False`, ble flagget slått av på den gjeldende raden
    mens pekeren ble stående. Da finner `hent_aktiv` INGEN aktiv rad og kaster
    `PolicyUkjent` på hver eneste beslutning, mens styringslaget fortsatt tror
    en policy er i kraft. Tenanten mister policyen sin uten at noe sier fra.

    Målt før fiksen: flagget sa «ingen aktiv», pekeren sa «1.0.0».

    Kontroll: fjern `else`-grenen i `registrer`, så blir denne rød.
    """
    tenant = TEN + "-d"
    c = _mig(tenant)
    try:
        pr.registrer(c, tenant, _policy("1.0.0"), "produksjon")
        _commit_og_les(c, tenant)
        with pytest.raises(pr.PolicyKorrupt):
            pr.registrer(c, tenant, _policy("1.0.0"), "produksjon",
                         aktiver=False)
        c.rollback()
        from db.pg import sett_kontekst
        sett_kontekst(c, tenant, "sys", "r2")
        # Og de to sannhetene står fortsatt sammen.
        akt = c.execute(
            "SELECT versjon FROM policyer WHERE tenant=%s AND aktiv",
            (tenant,)).fetchall()
        pek = c.execute(
            "SELECT aktiv_versjon FROM policy_hode WHERE tenant=%s",
            (tenant,)).fetchone()
        assert [r[0] for r in akt] == ["1.0.0"], "den aktive raden ble borte"
        assert pek[0] == "1.0.0", "pekeren driftet fra flagget"
    finally:
        c.close()
