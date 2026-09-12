"""Porten for 191: fire moduler slutter å kreve hvert sitt firmanavn.

MÅLT I PROD FØR DENNE: firmaets eget navn sto kopiert i fire tabeller —
`purreplan` (M-23), `kampanjeavsender` (M-44), `kundeserviceavsender` (M-17)
og `tilbudsavsender` (M-26). Alle fire sa «Fjordlys Elektro AS», og de stemte
— fordi testfirmaet ble satt opp nøye, ett skjema av gangen. For et firma som
registrerer seg selv er det fire skjemaer det må FINNE, og ingen av dem
nevner de tre andre.

FORMEN ER EN RESERVEVEI, IKKE EN FLYTTING: `coalesce(modulens navn,
firma.navn)`. Har modulen en egen verdi, vinner den — en purring kan signeres
«Fjordlys Elektro AS — Økonomi» mens et tilbud er signert med firmanavnet
alene. Registeret fjerner OPPSETTSTEGET, ikke muligheten.

MUTASJONENE SOM DREPER DISSE:
  * fjern `coalesce(...)` i en dør             → dens «uten modulrad»-port
  * snu coalesce-rekkefølgen                   → dens «override»-port
  * la lesedørene beholde `FROM <modultabell>` uten LEFT JOIN mot firma
    → alle tre lesedør-portene faller (null rader, ikke feil verdi)
  * join firma på `a.tenant` i stedet for hovedtabellens tenant
    → M-44-porten faller: `a.tenant` er NULL nettopp når modulraden mangler
"""
import secrets
import uuid

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo, pg  # noqa: F401
from .test_m37 import _sett_kontekst

FIRMANAVN = "Registerets Navn AS"


def _t() -> str:
    return "t-fnavn-" + secrets.token_hex(3)


@pytest.fixture()
def runtime(miljo):
    """Modulenes dører er grantet til WEB-API-ROLLEN, ikke til migrator.

    Porten går derfor samme vei som produktet gjør — 187s portfil bruker
    samme `_kobling(DSN)`. Går den som migrator, måler den en tilgang
    ingen ekte kaller har.
    """
    from db.pg import koble
    c = koble(DSN)
    yield c
    c.close()


def _firma(c, t, navn=FIRMANAVN):
    """Firmaraden legges av MIGRATOR: `firma_registrer` er grantet til
    runtime også, men porten skal ikke avhenge av det for å sette opp
    tilstanden den måler."""
    _sett_kontekst(c, t)
    c.execute("SELECT firma_registrer(%s,%s,NULL,30,'kari')", (t, navn))
    c.commit()
    _sett_kontekst(c, t)


# ---------------------------------------------------------------------------
# 1-3. De tre lesedørene: uten modulrad skal registeret svare.
#
# Disse måtte SNUS i 191. De gjorde `FROM <modultabell> WHERE tenant = ...`
# uten LEFT JOIN, så en manglende rad ga null rader — og da hjelper ingen
# coalesce. Nå driver `firma` raden.
# ---------------------------------------------------------------------------

LESEDOERER = [
    ("m26_avsenderprofilen", "tilbudsavsender",
     "SELECT m26_sett_avsenderprofil(%s,%s,NULL,NULL,'kari')"),
    ("m17_avsenderprofilen", "kundeserviceavsender",
     "SELECT m17_sett_avsenderprofil(%s,%s,NULL,NULL,'kari')"),
    ("m44_avsenderen", "kampanjeavsender",
     "SELECT m44_sett_avsender(%s,%s,NULL,'kari')"),
]


@pg
@pytest.mark.parametrize("doer,tabell,sett", LESEDOERER)
def test_uten_modulrad_svarer_registeret(migrator, runtime, doer,
                                        tabell, sett):
    t = _t()
    _firma(migrator, t)
    assert migrator.execute(
        f"SELECT count(*) FROM {tabell} WHERE tenant=%s", (t,)).fetchone()[0] \
        == 0, "testen satte en modulrad den ikke skulle sette"

    _sett_kontekst(runtime, t)
    rad = runtime.execute(f"SELECT * FROM {doer}(%s)", (t,)).fetchone()
    assert rad is not None, f"{doer} ga null rader — lesedøra ble ikke snudd"
    assert rad[0] == FIRMANAVN


@pg
@pytest.mark.parametrize("doer,tabell,sett", LESEDOERER)
def test_modulens_eget_navn_vinner(migrator, runtime, doer, tabell, sett):
    """Reservevei, ikke flytting: en purring kan signeres annerledes enn et
    tilbud, og 191 skal ikke ta fra noen den muligheten."""
    t = _t()
    _firma(migrator, t)
    _sett_kontekst(runtime, t)
    runtime.execute(sett, (t, "Modulens Eget AS"))
    runtime.commit()
    _sett_kontekst(runtime, t)

    rad = runtime.execute(f"SELECT * FROM {doer}(%s)", (t,)).fetchone()
    assert rad[0] == "Modulens Eget AS", (
        "registeret overkjørte modulens egen verdi")


@pg
@pytest.mark.parametrize("doer,tabell,sett", LESEDOERER)
def test_modulrad_uten_firmarad_er_produksjonens_tilstand(runtime, doer,
                                                          tabell, sett):
    """DEN PORTEN SOM MANGLET, og som fanget en ekte regresjon.

    Prod har i dag fire tenanter med modulrad og INGEN firmarad — registeret
    er nytt. Første utgave av 191 lot `firma` drive raden, og da ga dørene
    plutselig null rader for nettopp dem: avsenderprofilen forsvant for alle
    som fantes fra før.

    Testene mine bommet fordi de alltid lagde firmaraden først. Det er
    samme feilklasse som «en test som antar en tilstand»: jeg målte den nye
    verdenen og aldri den bestående.
    """
    t = _t()
    _sett_kontekst(runtime, t)
    runtime.execute(sett, (t, "Bare Modulen AS"))
    runtime.commit()
    _sett_kontekst(runtime, t)

    rad = runtime.execute(f"SELECT * FROM {doer}(%s)", (t,)).fetchone()
    assert rad is not None, (
        f"{doer} ga null rader for en tenant som HAR modulrad — "
        "registeret driver raden, og det er en regresjon")
    assert rad[0] == "Bare Modulen AS"


@pg
@pytest.mark.parametrize("doer,tabell,sett", LESEDOERER)
def test_uten_firma_og_uten_modulrad_er_svaret_tomt(runtime, doer, tabell,
                                                    sett):
    """Positiv kontroll mot en fraværstest som går grønn på søppel: finnes
    verken firma eller modulrad, skal døra være tom — ikke returnere en rad
    med NULL-navn som sendingen tror den kan bruke."""
    t = _t()
    _sett_kontekst(runtime, t)
    rader = runtime.execute(f"SELECT * FROM {doer}(%s)", (t,)).fetchall()
    assert rader == []


# ---------------------------------------------------------------------------
# 4. M-23 har ingen lesedør — den måles gjennom sendingsveien.
# ---------------------------------------------------------------------------

@pg
def test_m23_for_sending_arver_firmanavnet(migrator, runtime):
    t = _t()
    _firma(migrator, t)
    fid = uuid.uuid4()
    _sett_kontekst(runtime, t)
    runtime.execute("SELECT m23_registrer_fordring(%s,%s,'K-1','F-1',250000,"
                    " current_date - 40, current_date - 10, 'kari')",
                    (t, fid))
    runtime.commit()
    _sett_kontekst(runtime, t)

    rad = runtime.execute(
        "SELECT avsender_navn FROM m23_for_sending(%s,%s)",
        (t, fid)).fetchone()
    assert rad is not None and rad[0] == FIRMANAVN, (
        "purringen kjenner ikke firmanavnet uten at noen fyller ut purreplan")


@pg
def test_m23_purreplanens_eget_navn_vinner(migrator, runtime):
    t = _t()
    _firma(migrator, t)
    fid = uuid.uuid4()
    _sett_kontekst(runtime, t)
    runtime.execute("SELECT m23_registrer_fordring(%s,%s,'K-2','F-2',250000,"
                    " current_date - 40, current_date - 10, 'kari')",
                    (t, fid))
    runtime.execute("SELECT m23_sett_avsender(%s,'Purreavdelingen AS',"
                    " NULL,'kari')", (t,))
    runtime.commit()
    _sett_kontekst(runtime, t)

    assert runtime.execute("SELECT avsender_navn FROM m23_for_sending(%s,%s)",
                           (t, fid)).fetchone()[0] == "Purreavdelingen AS"


# ---------------------------------------------------------------------------
# 5. CREATE OR REPLACE skal beholde ACL-en — ikke åpne for PUBLIC.
# ---------------------------------------------------------------------------

@pg
def test_ingen_av_de_sju_doerene_apnet_for_public(migrator):
    """`CREATE OR REPLACE` beholder eier og rettigheter, men en `DROP` +
    `CREATE` ville gitt en ny funksjon med implisitt `EXECUTE TO PUBLIC`.
    190 viste at den fellen er ekte (der kom `=X` inn via feil rekkefølge på
    REVOKE), så den måles her også — på ACL-en selv, ikke på navngitte roller.
    """
    aapne = migrator.execute(
        "SELECT p.proname FROM pg_proc p"
        " JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname='public' AND p.proname IN"
        "   ('m23_for_sending','m44_for_sending','m17_for_sending',"
        "    'm26_for_sending','m44_avsenderen','m17_avsenderprofilen',"
        "    'm26_avsenderprofilen')"
        "   AND (p.proacl IS NULL OR EXISTS ("
        "         SELECT 1 FROM aclexplode(p.proacl) a"
        "          WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'))"
    ).fetchall()
    assert aapne == [], f"dører åpne for PUBLIC etter 191: {aapne}"


# ---------------------------------------------------------------------------
# 6. M-44s sendingsvei, med ekte kampanje og mottaker.
# ---------------------------------------------------------------------------

@pg
def test_m44_for_sending_arver_firmanavnet(migrator, runtime):
    """M-44 er den av de fire der feilen var lettest å skrive: firma må
    joines på KAMPANJENS tenant, ikke på avsenderradens. Joines den på
    `a.tenant`, er den NULL nettopp når modulraden mangler — altså i det ene
    tilfellet reserveveien finnes for. (Generatoren min gjorde nettopp det,
    og det ble fanget før migrasjonen ble kjørt.)
    """
    from .test_m44_kampanje import _kampanje, _mottaker

    t = _t()
    _firma(migrator, t)
    kid = _kampanje(runtime, t)
    mid, _ = _mottaker(runtime, t)
    _sett_kontekst(runtime, t)

    rad = runtime.execute(
        "SELECT avsender_navn FROM m44_for_sending(%s,%s,%s)",
        (t, kid, mid)).fetchone()
    assert rad is not None and rad[0] == FIRMANAVN


# ---------------------------------------------------------------------------
# 7. Alle sju bærer reserveveien — også de to som ikke har en atferdsport.
# ---------------------------------------------------------------------------

DOERER = ["m23_for_sending", "m44_for_sending", "m17_for_sending",
          "m26_for_sending", "m44_avsenderen", "m17_avsenderprofilen",
          "m26_avsenderprofilen"]


@pg
def test_alle_sju_doerene_leser_registeret(migrator):
    """En STRUKTURPORT, og den er merket som det.

    `m17_for_sending` og `m26_for_sending` fikk samme endring som de fem
    andre, men å bygge en henvendelse og et tilbud fra bunnen for å måle ett
    kolonneuttrykk koster mer enn det beviser — der dekker lesedørene den
    samme `coalesce`-en på samme tabell. Denne porten fanger det strukturelle
    som faktisk kan skje: at én av sju blir stående igjen når noen endrer
    resten. Den måler IKKE at uttrykket virker; det gjør portene over.
    """
    mangler = []
    for navn in DOERER:
        kilde = migrator.execute(
            "SELECT pg_get_functiondef(p.oid) FROM pg_proc p"
            " JOIN pg_namespace n ON n.oid=p.pronamespace"
            " WHERE n.nspname='public' AND p.proname=%s", (navn,)).fetchone()[0]
        if "fx.navn" not in kilde or "public.firma fx" not in kilde:
            mangler.append(navn)
    assert mangler == [], f"dører uten reservevei til registeret: {mangler}"
