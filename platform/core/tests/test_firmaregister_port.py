"""Porten for 190: firmaet vet endelig hvem det selv er.

MÅLT FØR DENNE MIGRASJONEN: ingen firmatabell finnes. Et firma eksisterer
bare som en tekststreng spredt over 347 `tenant`-kolonner, og ingen rad sier
at det finnes, hva det heter, eller hvor lenge prøveperioden varer.

De tre tingene porten måler, i den rekkefølgen de ville blitt utnyttet:

  1. LIVSSYKLUSEN ER HUSETS BESLUTNING, IKKE KUNDENS. `disponit` har ingen
     direkte skriverett på `firma` — bare dørene. Uten det kunne et firma
     satt sin egen `status` til 'aktiv' og gitt seg selv gratis abonnement.
  2. REGISTRERING ER IKKE EN OPPDATERING. `part_registrer` er idempotent
     fordi en import kaller den tusen ganger; `firma_registrer` er det
     motsatte, fordi et andre kall på samme tenant er enten en feil eller et
     forsøk på å overta noen andres rad.
  3. EN PRØVEPERIODE SOM ALDRI UTLØPER ER ET GRATISABONNEMENT. `prove_utloper`
     er en dato ingen leser uten sveipen.

MUTASJONENE SOM DREPER DISSE:
  * gi `disponit` `GRANT UPDATE ON firma`               → port 1 faller
  * bytt `ON CONFLICT DO NOTHING` mot `DO UPDATE`       → port 4 faller
  * la `firma_sett_status` godta enhver overgang        → port 6 faller
  * fjern tidsbetingelsen på `stengt → aktiv`           → port 8 faller
  * dropp `AT TIME ZONE 'Europe/Oslo'` i sveipen        → port 9 blir
    tidssoneavhengig (måles ikke direkte, men er navngitt i migrasjonen)
"""
import secrets

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo, pg  # noqa: F401
from .test_m37 import _sett_kontekst


def _t() -> str:
    return "t-firma-" + secrets.token_hex(3)


def _reg(c, t, navn="Fjordlys Elektro AS", orgnr=None, dogn=30, aktor="kari"):
    _sett_kontekst(c, t)
    frist = c.execute("SELECT firma_registrer(%s,%s,%s,%s,%s)",
                      (t, navn, orgnr, dogn, aktor)).fetchone()[0]
    # MÅ committe: en `pytest.raises`-rollback senere i testen ville ellers
    # tatt med seg registreringen, og porten ville målt en tom base i stedet
    # for en avvist overgang. (Den gjorde det: `prove → prove` falt på
    # NoneType, ikke på en manglende avvisning.)
    c.commit()
    return frist


def _status(c, t) -> str:
    _sett_kontekst(c, t)
    return c.execute("SELECT status FROM firma_hent(%s)", (t,)).fetchone()[0]


# ---------------------------------------------------------------------------
# 1-2. Rettighetene: kunden eier identiteten sin, ikke abonnementet sitt.
# ---------------------------------------------------------------------------

@pg
def test_runtime_har_ingen_direkte_skriverett_paa_firma(migrator):
    """Med et direkte grant ville RLS holdt firmaet inne i sin egen rad —
    men ingenting hindret det i å sette `status='aktiv'` selv."""
    rader = migrator.execute(
        "SELECT privilege_type FROM information_schema.table_privileges"
        " WHERE table_name='firma' AND grantee='disponit'").fetchall()
    gitt = {r[0] for r in rader}
    assert not (gitt & {"INSERT", "UPDATE", "DELETE"}), (
        f"runtime har skriverett direkte på firma: {sorted(gitt)}")


@pg
def test_doerene_krever_riktig_tenantkontekst(migrator):
    t, annen = _t(), _t()
    _reg(migrator, t)
    _sett_kontekst(migrator, annen)
    for sql, args in [
        ("SELECT firma_hent(%s)", (t,)),
        ("SELECT firma_oppdater(%s,'Kapret AS',NULL,'angriper')", (t,)),
        ("SELECT firma_sett_status(%s,'aktiv','angriper')", (t,)),
    ]:
        with pytest.raises(psycopg.Error):
            migrator.execute(sql, args)
        migrator.rollback()


# ---------------------------------------------------------------------------
# 3-5. Registrering.
# ---------------------------------------------------------------------------

@pg
def test_registrering_gir_prove_med_frist(migrator):
    t = _t()
    frist = _reg(migrator, t, dogn=30)
    _sett_kontekst(migrator, t)   # _reg committer; konteksten følger med
    rad = migrator.execute(
        "SELECT status, prove_utloper, navn, stengt_ts FROM firma_hent(%s)",
        (t,)).fetchone()
    assert rad[0] == "prove"
    assert rad[1] == frist, "returnert frist er ikke den som ble lagret"
    assert rad[2] == "Fjordlys Elektro AS"
    assert rad[3] is None


@pg
def test_registrering_er_ikke_en_oppdatering(migrator):
    """Et andre kall på samme tenant er enten en feil eller et forsøk på å
    overta noen andres rad. Begge skal si fra, ikke skrive over."""
    t = _t()
    _reg(migrator, t, navn="Ekte AS")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _reg(migrator, t, navn="Kapret AS")
    migrator.rollback()
    _sett_kontekst(migrator, t)
    assert migrator.execute("SELECT navn FROM firma_hent(%s)",
                            (t,)).fetchone()[0] == "Ekte AS"


@pg
def test_proveperioden_maa_ha_en_lengde_noen_har_bestemt(migrator):
    t = _t()
    for dogn in (0, -1, 366, None):
        _sett_kontekst(migrator, t)
        with pytest.raises(psycopg.Error):
            migrator.execute("SELECT firma_registrer(%s,'X',NULL,%s,'kari')",
                             (t, dogn))
        migrator.rollback()


# ---------------------------------------------------------------------------
# 6-8. Livssyklusen.
# ---------------------------------------------------------------------------

LOVLIGE = [("prove", "aktiv"), ("prove", "utlopt"), ("prove", "stengt"),
           ("aktiv", "stengt"), ("utlopt", "aktiv"), ("utlopt", "stengt"),
           ("stengt", "aktiv")]
ULOVLIGE = [("aktiv", "prove"), ("aktiv", "utlopt"), ("utlopt", "prove"),
            ("stengt", "prove"), ("stengt", "utlopt"), ("stengt", "stengt"),
            ("aktiv", "aktiv"), ("prove", "prove")]


def _bring_til(c, t, mål):
    """Fører et ferskt firma til ønsket tilstand gjennom lovlige steg.

    Konteksten settes på nytt etter HVER commit: `set_config(..., true)` er
    transaksjonslokal, så en commit tar den med seg og neste dør svarer
    «ikke kallerens tenantkontekst» i stedet for det testen måler.
    """
    _reg(c, t)                      # committer
    if mål == "prove":
        return
    _sett_kontekst(c, t)
    c.execute("SELECT firma_sett_status(%s,%s,'kari')", (t, mål))
    c.commit()


@pg
@pytest.mark.parametrize("fra,til", LOVLIGE)
def test_lovlige_overganger_gaar(migrator, fra, til):
    t = _t()
    _bring_til(migrator, t, fra)
    _sett_kontekst(migrator, t)
    migrator.execute("SELECT firma_sett_status(%s,%s,'kari')", (t, til))
    migrator.commit()
    assert _status(migrator, t) == til


@pg
@pytest.mark.parametrize("fra,til", ULOVLIGE)
def test_ulovlige_overganger_avvises(migrator, fra, til):
    t = _t()
    _bring_til(migrator, t, fra)
    _sett_kontekst(migrator, t)
    with pytest.raises(psycopg.Error):
        migrator.execute("SELECT firma_sett_status(%s,%s,'kari')", (t, til))
    migrator.rollback()
    assert _status(migrator, t) == fra, "tilstanden endret seg ved avvisning"


@pg
def test_gjenaapning_etter_angrefristen_nektes(migrator):
    """Etter fristen er nøkkelen destruert. En «gjenåpning» ville gitt et
    tomt firma som SER helt ut — bedre å nekte enn å love noe tilbake."""
    t = _t()
    _bring_til(migrator, t, "stengt")
    _sett_kontekst(migrator, t)
    # Flytt stengingen 31 døgn tilbake (standard angrefrist er 30).
    migrator.execute("UPDATE firma SET stengt_ts = now() - interval '31 days'"
                     " WHERE tenant=%s", (t,))
    migrator.commit()
    _sett_kontekst(migrator, t)
    with pytest.raises(psycopg.Error):
        migrator.execute("SELECT firma_sett_status(%s,'aktiv','kari')", (t,))
    migrator.rollback()
    assert _status(migrator, t) == "stengt"

    # Positiv kontroll: INNENFOR fristen går den samme overgangen.
    _sett_kontekst(migrator, t)
    migrator.execute("UPDATE firma SET stengt_ts = now() - interval '29 days'"
                     " WHERE tenant=%s", (t,))
    migrator.commit()
    _sett_kontekst(migrator, t)
    migrator.execute("SELECT firma_sett_status(%s,'aktiv','kari')", (t,))
    migrator.commit()
    assert _status(migrator, t) == "aktiv"


@pg
def test_oppdatering_rorer_identiteten_aldri_abonnementet(migrator):
    t = _t()
    _reg(migrator, t, navn="Gammelt AS")
    _sett_kontekst(migrator, t)
    migrator.execute("SELECT firma_oppdater(%s,'Nytt AS','923609016','per')",
                     (t,))
    migrator.commit()
    _sett_kontekst(migrator, t)   # commit nullstiller set_config(..., true)
    rad = migrator.execute(
        "SELECT navn, orgnummer, status FROM firma_hent(%s)", (t,)).fetchone()
    assert rad[0] == "Nytt AS" and rad[1] == "923609016"
    assert rad[2] == "prove", "en navneendring flyttet abonnementet"


# ---------------------------------------------------------------------------
# 9. Prøveperioden utløper faktisk.
# ---------------------------------------------------------------------------

@pg
def test_sveipen_utloper_modne_prover_og_lar_ferske_staa(migrator):
    """En prøveperiode som aldri tar slutt er et gratisabonnement med en
    misvisende etikett.

    Sveipen er kryss-tenant og har en grense, så porten måler bare SINE
    EGNE tenanter og drenerer til basen er tom — samme to fellene som ble
    funnet i 184s portfil.
    """
    moden, fersk = _t(), _t()
    _reg(migrator, moden, dogn=30)
    _reg(migrator, fersk, dogn=30)
    _sett_kontekst(migrator, moden)
    migrator.execute("UPDATE firma SET prove_utloper = current_date - 1"
                     " WHERE tenant=%s", (moden,))
    migrator.commit()

    sett = set()
    for _ in range(25):
        # Sveipen kalles som EIEREN. I prod har `disponit_domener` grantet;
        # lokalt finnes ikke den rollen, og da er det bare eieren igjen —
        # nøyaktig slik migrasjonen sier det skal være. HVEM som får kalle
        # den måles av port 12 og ACL-porten, ikke her; denne måler at
        # kroppen gjør riktig jobb.
        migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
        rader = migrator.execute(
            "SELECT tenant FROM firma_sveip_proveutlop(200)").fetchall()
        migrator.commit()
        if not rader:
            break
        sett |= {r[0] for r in rader}
    else:
        raise AssertionError("sveipen ble ikke tom — drenerte 25 runder")

    assert moden in sett, "moden prøveperiode ble ikke utløpt"
    assert fersk not in sett, "fersk prøveperiode ble utløpt for tidlig"
    assert _status(migrator, moden) == "utlopt"
    assert _status(migrator, fersk) == "prove"


# ---------------------------------------------------------------------------
# 10-11. Formen på identiteten.
# ---------------------------------------------------------------------------

@pg
@pytest.mark.parametrize("navn", ["_plattform", "_oidc", "Store-Bokstaver",
                                  "-leder-bindestrek", "a", "med_understrek"])
def test_tenantnavnet_har_endelig_en_form(migrator, navn):
    """Registeret er det første stedet som definerer hva et tenantnavn ER.
    `init-tenant.sh _plattform` ville virket før denne raden fantes.
    """
    _sett_kontekst(migrator, navn)
    with pytest.raises(psycopg.Error):
        migrator.execute("SELECT firma_registrer(%s,'X',NULL,30,'kari')",
                         (navn,))
    migrator.rollback()


@pg
@pytest.mark.parametrize("nr,gyldig", [
    ("923609016", True),    # Equinor ASA
    ("984851006", True),    # DNB Bank ASA
    ("982463718", True),    # Telenor ASA
    ("925836613", True),    # Norsk Tipping AS
    ("123456789", False),   # oppdiktet — feil kontrollsiffer
    ("000000000", False),   # matematisk gyldig MOD-11, ingen identitet
    ("012345678", False),   # ledende null finnes ikke
    ("92360901", False),    # åtte sifre
    ("9236090161", False),  # ti sifre
    ("92360901X", False),
])
def test_orgnummer_er_mod11_ikke_bare_ni_sifre(migrator, nr, gyldig):
    """Firmaraden er vår EGEN juridiske identitet — den skal til Altinn og
    Brønnøysund, og et ugyldig nummer oppdages først der, av noen andre.

    `part.orgnummer` (183) holder seg til ni sifre med vilje: en KUNDE kan
    være utenlandsk, og feltet er valgfritt.
    """
    assert migrator.execute("SELECT er_gyldig_orgnummer(%s)",
                            (nr,)).fetchone()[0] is gyldig


@pg
def test_ugyldig_orgnummer_naar_aldri_raden(migrator):
    t = _t()
    _sett_kontekst(migrator, t)
    with pytest.raises(psycopg.Error):
        migrator.execute("SELECT firma_registrer(%s,'X','123456789',30,'kari')",
                         (t,))
    migrator.rollback()


# ---------------------------------------------------------------------------
# 12. Fullmakten, ikke bare tenanten.
# ---------------------------------------------------------------------------

@pg
def test_runtime_kan_ikke_kalle_livssyklusdoera(migrator):
    """`krev_tenantkontekst` binder TENANTEN, ikke FULLMAKTEN.

    En kundesesjon står i sin egen kontekst, så et EXECUTE-grant til
    web-API-rollen ville latt enhver rute som nådde døra sette kundens egen
    status til 'aktiv' — gratis abonnement, uten at noen hadde bestemt det.
    Registrering, navneendring og oppslag er kundens egne handlinger og skal
    derimot virke.
    """
    gitt = {r[0] for r in migrator.execute(
        "SELECT routine_name FROM information_schema.routine_privileges"
        " WHERE grantee='disponit' AND routine_name LIKE 'firma%'").fetchall()}
    assert "firma_sett_status" not in gitt, (
        "runtime kan endre livssyklusen — da er døra bare dekorasjon")
    assert "firma_sveip_proveutlop" not in gitt, (
        "runtime kan utløpe prøveperioden for HVERT firma i basen")
    # Positiv kontroll: en fraværstest går grønn på en tom liste.
    assert {"firma_registrer", "firma_oppdater", "firma_hent"} <= gitt, (
        f"kundens egne dører mangler for runtime: {sorted(gitt)}")


@pg
def test_ingen_firmadoer_star_aapen_for_public(migrator):
    """En ny funksjon får IMPLISITT `EXECUTE TO PUBLIC`, og den er gitt av
    EIEREN. Trekkes den tilbake av noen andre enn eieren, svarer Postgres
    med en WARNING — ikke en feil — og døra står åpen.

    Det skjedde her: `REVOKE ALL ... FROM PUBLIC` sto etter `RESET ROLE`, og
    `firma_sveip_proveutlop` endte med `=X` i ACL-en. Enhver rolle i basen
    kunne kalt en SECURITY DEFINER-sveip som utløper prøveperioden til HVERT
    firma. En port som bare sjekket at navngitte roller manglet grantet, ville
    vært grønn hele veien — derfor måles ACL-en selv.
    """
    aapne = migrator.execute(
        "SELECT p.proname, p.proacl::text FROM pg_proc p"
        " JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname='public' AND p.proname LIKE 'firma%'"
        "   AND (p.proacl IS NULL OR EXISTS ("
        "         SELECT 1 FROM aclexplode(p.proacl) a"
        "          WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'))"
    ).fetchall()
    assert aapne == [], (
        "firmadører med EXECUTE til PUBLIC (grantee 0 = PUBLIC, "
        f"NULL acl = standard PUBLIC): {aapne}")
