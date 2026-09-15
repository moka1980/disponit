"""Porten for en NYREGISTRERT kundes tomme avsenderprofil.

MÅLT I PRODUKSJON 15/9: `wcagvakt` fikk «Noe gikk galt» på hele
kundeservicekøen. Årsaken var `AttributeError: 'NoneType' object has no
attribute 'isoformat'` — tre ganger.

MEKANISMEN, OG HVORFOR DEN BARE TRAFF NYE KUNDER. Migrasjon 191 ga
avsenderdørene en LEFT JOIN mot `firma`, så de faller tilbake på
FIRMANAVNET når ingen profil er satt. Døra returnerer derfor en rad så
snart firmaet finnes — med `avsender_navn` fylt, men `svar_til`,
`signatur` og `oppdatert` NULL. Lesesiden vokter `rad is None`, og det er
ikke lenger nok.

Den gamle tenanten hadde en profilrad fra en tidligere runde, så feilen
fantes ikke der noen så etter den. Hver KUNDE som registrerer seg treffer
den på første besøk.

KLASSEN, IKKE INSTANSEN. 191 endret fire avsenderdører, men MÅLINGEN
viser at bare TRE fikk fallbacken: `m17_avsenderprofilen`,
`m26_avsenderprofilen` og `m44_avsenderen` gir en rad så snart firmaet
finnes; `m23_avsenderen` gir fortsatt ingen. Alle tre lesesidene hadde
hullet. M-23 hadde alt vakten (`if a[2] else None`) — av en annen grunn,
men den viser hva de andre skulle gjort.

Porten måler BEGGE tilstandene, så den dagen noen gir M-23 samme
fallback, blir lesesida lest på nytt i samme slengen.
"""
import secrets

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo  # noqa: F401
from .test_m37 import _sett_kontekst

pg = pytest.mark.skipif(not (DSN and MIGRATOR_DSN),
                        reason="test-DSN ikke satt")

#: DØRENE SOM FALLER TILBAKE PÅ FIRMANAVNET — målt, ikke antatt.
#:
#: 191 endret fire avsenderdører, men bare TRE fikk LEFT JOIN-en mot
#: `firma`. `m23_avsenderen` gir fortsatt ingen rad uten profil (målt
#: 15/9), og M-23s lesesida er derfor trygg av en annen grunn enn de
#: andre. Å liste den her ville gjort porten rød på en påstand som ikke
#: er sann.
DORER_MED_FALLBACK = (
    "m17_avsenderprofilen",
    "m26_avsenderprofilen",
    "m44_avsenderen",
)

#: …og den som IKKE har den. Måles for seg, så en dag noen gir den
#: fallbacken, faller porten og lesesida blir sett på samtidig.
DOR_UTEN_FALLBACK = "m23_avsenderen"


def _tenant():
    return f"t-tomavs-{secrets.token_hex(4)}"


def _rt():
    """RUNTIME-ROLLEN, som API-et. Avsenderdørene er grantet til
    `disponit`, ikke til migrator — en port som leste dem som migrator
    ville fått «permission denied» og målt en vei ingen kaller går."""
    from db.pg import koble
    return koble(DSN)


def _firma_uten_profil(m, tenant):
    """Et firma som finnes, men UTEN avsenderprofil — nøyaktig
    tilstanden en nyregistrert kunde har på sitt første besøk."""
    _sett_kontekst(m, tenant)
    m.execute(
        # `prove` MED utløpsdato — `firma_status_helhet` krever det, og
        # det er nøyaktig formen en nyregistrert kunde har.
        "INSERT INTO firma (tenant, navn, status, prove_utloper, endret_av)"
        " VALUES (%s,%s,'prove', now() + interval '30 days','port')"
        " ON CONFLICT (tenant) DO NOTHING",
        (tenant, f"Nyreg {tenant[-6:]} AS"))
    m.commit()


@pg
def test_dorene_gir_en_rad_med_null_oppdatert(migrator):  # noqa: F811
    """FORUTSETNINGEN, målt — ikke antatt.

    Uten dette leddet ville portene under bestått på en dør som
    returnerte INGEN rad, og da måler de ingenting. Dette er den
    positive kontrollen: raden FINNES, og `oppdatert` ER NULL.
    """
    t = _tenant()
    _firma_uten_profil(migrator, t)
    c = _rt()
    try:
        for dor in DORER_MED_FALLBACK:
            _sett_kontekst(c, t)
            rad = c.execute(f"SELECT * FROM {dor}(%s)", (t,)).fetchone()
            c.rollback()
            assert rad is not None, \
                f"{dor} ga ingen rad — porten måler da ikke hullet"
            assert rad[0], f"{dor} ga ingen avsender_navn fra firmanavnet"
            assert rad[-1] is None, \
                f"{dor} ga en `oppdatert` — forutsetningen stemmer ikke"
        # …og den uten fallback gir INGEN rad. Faller denne, har noen
        # gitt `m23_avsenderen` samme LEFT JOIN, og `api.fordring` må
        # leses på nytt i samme slengen.
        _sett_kontekst(c, t)
        rad = c.execute(f"SELECT * FROM {DOR_UTEN_FALLBACK}(%s)",
                        (t,)).fetchone()
        c.rollback()
        assert rad is None, (
            f"{DOR_UTEN_FALLBACK} har fått en fallback — les"
            " `api.fordring.svar_for` på nytt")
    finally:
        c.close()


@pg
def test_kundeservicekoen_taler_tom_profil(migrator, miljo):  # noqa: F811
    """M-17 — den som faktisk falt i produksjon."""
    from api.kundeservice import svar_for
    t = _tenant()
    _firma_uten_profil(migrator, t)
    c = _rt()
    try:
        _sett_kontekst(c, t)
        svar = svar_for(c, t)
        c.rollback()
    finally:
        c.close()
    assert svar["avsenderprofil"]["oppdatert"] is None
    assert svar["avsenderprofil"]["avsender_navn"], "firmanavnet mangler"


@pg
def test_fordringsbildet_taler_tom_profil(migrator, miljo):  # noqa: F811
    """M-23 — hadde vakten fra før. Porten holder den på plass."""
    from api.fordring import svar_for
    t = _tenant()
    _firma_uten_profil(migrator, t)
    c = _rt()
    try:
        _sett_kontekst(c, t)
        svar = svar_for(c, t)
        c.rollback()
    finally:
        c.close()
    assert svar["avsender"] is None or svar["avsender"]["oppdatert"] is None


@pg
def test_ingen_lesesida_kaller_isoformat_ubeskyttet():
    """KLASSEN, STATISK. En ny avsenderflate skal ikke kunne legge til
    `rad[i].isoformat()` uten vakt og oppdage det hos en kunde.

    MUTASJONEN SOM DREPER DENNE: fjern ` if a[3] else None` fra
    `kundeservice.svar_for` — porten peker da på filen og linjen.
    """
    import re
    from pathlib import Path

    # SNEVRET TIL AVSENDERDØRENE, med vilje. Første utkast flagget enhver
    # `"oppdatert": x[i].isoformat()` og traff `m44_grensene` — en dør
    # UTEN LEFT JOIN, som gir ingen rad når grensen mangler, og der
    # vakten `g is None` holder. En port som roper ulv blir skrudd av, så
    # den måler nå nøyaktig klassen 191 skapte: de fire dørene som
    # faller tilbake på firmanavnet.
    rot = Path(__file__).resolve().parents[1] / "api"
    dorer = re.compile(r"(\w+)\s*=\s*conn\.execute\(\s*$|"
                       r"(\w+)\s*=\s*conn\.execute\(.*"
                       r"(m17_avsenderprofilen|m26_avsenderprofilen"
                       r"|m44_avsenderen|m23_avsenderen)")
    dornavn = re.compile(r"(m17_avsenderprofilen|m26_avsenderprofilen"
                         r"|m44_avsenderen|m23_avsenderen)")
    funn = []
    for navn in ("kundeservice.py", "tilbud.py", "kampanje.py",
                 "fordring.py"):
        linjer = (rot / navn).read_text(encoding="utf-8").splitlines()
        for i, linje in enumerate(linjer):
            if not dornavn.search(linje):
                continue
            # VARIABELEN, ikke et linjevindu. Første utkast så 14 linjer
            # fram fra dørkallet — og en kommentar lagt til over
            # lesningen dyttet den ut av vinduet, så porten sluttet å
            # måle uten å bli rød. Bindingen er navnet raden får.
            m = re.search(r"(\w+)\s*=\s*conn\.execute", linje) \
                or re.search(r"(\w+)\s*=\s*conn\.execute",
                             linjer[i - 1] if i else "")
            if not m:
                continue
            var = m.group(1)
            farlig = re.compile(re.escape(var) + r"\[\d+\]\.isoformat\(\)")
            for j in range(i, len(linjer)):
                if j > i and linjer[j].startswith("def "):
                    break              # ut av funksjonen raden lever i
                if farlig.search(linjer[j]) and " if " not in linjer[j]:
                    funn.append(f"{navn}:{j + 1}: {linjer[j].strip()}")
    assert not funn, (
        "en avsenderprofil leses uten vakt mot NULL `oppdatert` — 191s"
        " LEFT JOIN gir en rad så snart firmaet finnes, og hver"
        " nyregistrert kunde får 500:\n  " + "\n  ".join(funn))
