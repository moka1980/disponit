"""Porten for 194: en bedrift kan ta med seg de ansatte.

MÅLT FØR DENNE: INGEN dør skriver `brukermedlemskap` — runtime har kun
SELECT, og tabellen fylles bare av en operatør med basetilgang. Et firma som
registrerte seg selv (192) var derfor en ÉNPERSONSBEDRIFT for alltid.

INVITASJON VED ENGANGSLENKE, IKKE VED E-POST, og det er en
personvernbeslutning: `brukeridentitet` er `(issuer, sub)`, og e-posten
finnes bare i OIDC-profilen som en lukket DTO. En invitasjon på e-post ville
enten innført et nytt persondatalager, eller krevd pseudonymoppslag på
TVERS av tenanter — som `tenant_pseudonym` er tenant-skopet for å hindre.

MUTASJONENE SOM DREPER DISSE:
  * bytt det atomiske kravet mot SELECT-så-UPDATE   → port 4 (kappløpet)
  * fjern `utloper > now()` fra kravet              → port 5
  * la `ON CONFLICT DO NOTHING` bli `DO UPDATE`     → port 6
  * fjern rydding av registrantraden                → port 7
  * skill feilkodene for «finnes ikke» og «brukt»   → port 8
"""
import hashlib
import secrets

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo, pg  # noqa: F401
from .test_m37 import _sett_kontekst

REG = "_registrering"


def _t():
    return "t-inv-" + secrets.token_hex(3)


def _token():
    raa = secrets.token_urlsafe(32)
    return raa, hashlib.sha256(raa.encode()).hexdigest()


def _bruker(c, merke="i"):
    _sett_kontekst(c, "t-inv-oppsett")
    bid = c.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://inv.test', %s) RETURNING bruker_id",
        (f"{merke}-" + secrets.token_hex(6),)).fetchone()[0]
    c.commit()
    return bid


def _firma(c, t, admin):
    _sett_kontekst(c, t)
    c.execute("SELECT firma_registrer(%s,'Invitasjonsfirma AS',NULL,30,"
              "'kari')", (t,))
    c.execute("INSERT INTO brukermedlemskap (tenant, bruker_id, roller,"
              " aktiv) VALUES (%s,%s,ARRAY['admin'],true)", (t, admin))
    c.commit()


def _inviter(c, t, roller=("leser",), aktor="bruker:bid_admin", timer=168):
    raa, h = _token()
    _sett_kontekst(c, t)
    c.execute("SELECT invitasjon_opprett(%s,%s,%s,%s,%s)",
              (t, h, list(roller), aktor, timer))
    c.commit()
    return raa, h


def _medlemskap(c, bid):
    c.execute("SELECT set_config('disponit.tenant','',true)")
    ut = {}
    for (t,) in c.execute("SELECT tenant FROM bruker_tenant WHERE bruker_id=%s",
                          (bid,)).fetchall():
        _sett_kontekst(c, t)
        rad = c.execute("SELECT roller FROM brukermedlemskap WHERE tenant=%s"
                        " AND bruker_id=%s", (t, bid)).fetchone()
        if rad:
            ut[t] = rad[0]
    return ut


# ---------------------------------------------------------------------------
# 1-3. Den vanlige veien.
# ---------------------------------------------------------------------------

@pg
def test_kollegaen_blir_medlem_med_rollene_invitasjonen_bar(migrator):
    t, admin, ny = _t(), _bruker(migrator, "a"), _bruker(migrator, "n")
    _firma(migrator, t, admin)
    _, h = _inviter(migrator, t, roller=("leser", "godkjenner"))

    # Den inviterte står IKKE i firmaets kontekst — hun er ikke medlem ennå.
    _sett_kontekst(migrator, REG)
    roller = migrator.execute("SELECT invitasjon_innloes(%s,%s,%s)",
                              (t, h, ny)).fetchone()[0]
    migrator.commit()
    assert sorted(roller) == ["godkjenner", "leser"]
    assert _medlemskap(migrator, ny).get(t) == ["leser", "godkjenner"]


@pg
def test_bare_hashen_lagres(migrator):
    """Den som får tak i basen skal se AT en invitasjon finnes, ikke kunne
    bruke den — samme form som `oidc_logintransaksjon` og `brukersesjon`."""
    t, admin = _t(), _bruker(migrator, "a")
    _firma(migrator, t, admin)
    raa, h = _inviter(migrator, t)

    _sett_kontekst(migrator, t)
    rad = migrator.execute("SELECT token_hash FROM invitasjon_liste(%s)",
                           (t,)).fetchone()
    assert rad[0] == h
    assert raa not in str(rad), "råtokenet lekket inn i basen"
    # Og ingen kolonne bærer en e-postadresse.
    kolonner = {r[0] for r in migrator.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name='firmainvitasjon'").fetchall()}
    assert not (kolonner & {"epost", "e_post", "adresse", "mottaker"}), (
        f"invitasjonen lagrer en adresse: {sorted(kolonner)}")


@pg
def test_ukjent_rolle_avvises_ved_opprettelsen(migrator):
    """`scopes_for_roller` gir en ukjent rolle INGEN scopes. En skrivefeil
    ville gitt kollegaen en konto uten fullmakter, og ingen feilmelding før
    hun satt der og lurte."""
    t, admin = _t(), _bruker(migrator, "a")
    _firma(migrator, t, admin)
    _sett_kontekst(migrator, t)
    _, h = _token()
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        migrator.execute("SELECT invitasjon_opprett(%s,%s,%s,'kari',168)",
                         (t, h, ["lesr"]))
    migrator.rollback()


# ---------------------------------------------------------------------------
# 4-6. Det som gjør en engangslenke til en engangslenke.
# ---------------------------------------------------------------------------

@pg
def test_en_lenke_kan_bare_brukes_en_gang(migrator):
    t, admin = _t(), _bruker(migrator, "a")
    en, to = _bruker(migrator, "1"), _bruker(migrator, "2")
    _firma(migrator, t, admin)
    _, h = _inviter(migrator, t)

    _sett_kontekst(migrator, REG)
    migrator.execute("SELECT invitasjon_innloes(%s,%s,%s)", (t, h, en))
    migrator.commit()

    _sett_kontekst(migrator, REG)
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        migrator.execute("SELECT invitasjon_innloes(%s,%s,%s)", (t, h, to))
    migrator.rollback()
    assert t not in _medlemskap(migrator, to)


@pg
def test_en_utlopt_lenke_slipper_ingen_inn(migrator):
    t, admin, ny = _t(), _bruker(migrator, "a"), _bruker(migrator, "n")
    _firma(migrator, t, admin)
    _, h = _inviter(migrator, t)
    _sett_kontekst(migrator, t)
    migrator.execute("UPDATE firmainvitasjon SET utloper = now() -"
                     " interval '1 hour' WHERE token_hash=%s", (h,))
    migrator.commit()

    _sett_kontekst(migrator, REG)
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        migrator.execute("SELECT invitasjon_innloes(%s,%s,%s)", (t, h, ny))
    migrator.rollback()


@pg
def test_en_gammel_lenke_kan_ikke_degradere_en_kollega(migrator):
    """Er hun alt medlem, brukes invitasjonen opp uten skade. Å overskrive
    rollene ville latt en gammel `leser`-lenke ta admin fra noen som i
    mellomtiden ble det."""
    t, admin, ny = _t(), _bruker(migrator, "a"), _bruker(migrator, "n")
    _firma(migrator, t, admin)
    _sett_kontekst(migrator, t)
    migrator.execute("INSERT INTO brukermedlemskap (tenant, bruker_id,"
                     " roller, aktiv) VALUES (%s,%s,ARRAY['admin'],true)",
                     (t, ny))
    migrator.commit()
    _, h = _inviter(migrator, t, roller=("leser",))

    _sett_kontekst(migrator, REG)
    migrator.execute("SELECT invitasjon_innloes(%s,%s,%s)", (t, h, ny))
    migrator.commit()
    assert _medlemskap(migrator, ny).get(t) == ["admin"], (
        "en gammel lenke degraderte en kollega")


# ---------------------------------------------------------------------------
# 7-9. Fellene fra 192, som gjelder her også.
# ---------------------------------------------------------------------------

@pg
def test_registrantraden_ryddes_saa_hun_ikke_maa_velge_firma(migrator):
    from api.sesjon import _firma_for_bruker

    t, admin, ny = _t(), _bruker(migrator, "a"), _bruker(migrator, "n")
    _firma(migrator, t, admin)
    migrator.execute("SELECT registrant_medlemskap(%s)", (ny,))
    migrator.commit()
    assert list(_medlemskap(migrator, ny)) == [REG]

    _, h = _inviter(migrator, t)
    _sett_kontekst(migrator, REG)
    migrator.execute("SELECT invitasjon_innloes(%s,%s,%s)", (t, h, ny))
    migrator.commit()

    assert list(_medlemskap(migrator, ny)) == [t]
    migrator.execute("SELECT set_config('disponit.tenant','',true)")
    assert _firma_for_bruker(migrator, ny, _Ident()) == t


@pg
def test_en_invitasjon_kan_aldri_baere_plattformrollen(migrator):
    """192 la CHECK-en på `brukermedlemskap`. Uten den samme her ville
    invitasjonen vært en omvei rundt den — raden ville blitt avvist ved
    innløsning, men først etter at en admin trodde hun ga den bort."""
    t, admin = _t(), _bruker(migrator, "a")
    _firma(migrator, t, admin)
    _sett_kontekst(migrator, t)
    _, h = _token()
    with pytest.raises(psycopg.Error):
        migrator.execute(
            "INSERT INTO firmainvitasjon (token_hash, tenant, roller,"
            " opprettet_av, utloper) VALUES (%s,%s,ARRAY['plattformeier'],"
            "'kari', now() + interval '1 day')", (h, t))
    migrator.rollback()


@pg
def test_feil_tenant_gir_samme_svar_som_ugyldig_token(migrator):
    """Én feilkode for alle fire tilfellene (finnes ikke, feil tenant,
    brukt, utløpt). Å skille dem ville latt noen prøve seg fram og lære
    hvilke tokener som finnes."""
    t, annen = _t(), _t()
    admin, ny = _bruker(migrator, "a"), _bruker(migrator, "n")
    _firma(migrator, t, admin)
    _firma(migrator, annen, admin)
    _, h = _inviter(migrator, t)

    _sett_kontekst(migrator, REG)
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        migrator.execute("SELECT invitasjon_innloes(%s,%s,%s)",
                         (annen, h, ny))
    migrator.rollback()
    assert annen not in _medlemskap(migrator, ny)


class _Ident:
    issuer = "https://inv.test"

    def __init__(self):
        self.sub = "x-" + secrets.token_hex(8)


@pg
def test_en_invitasjon_uten_roller_avvises(migrator):
    """`array_length('{}', 1)` er NULL, og `NULL >= 1` er NULL — som en
    CHECK SLIPPER GJENNOM. Første utgave brukte nettopp den formen, så en
    tom rolleliste ville gitt kollegaen et medlemskap uten scopes: hun
    kommer inn og ser ingenting, uten at noe sier hvorfor.

    Samme feilklasse som `NULL !~ mønster` i 182 (CodeRabbit fant begge).

    MUTASJONEN SOM DREPER DENNE: bytt `cardinality` tilbake til
    `array_length(roller, 1)`.
    """
    t, admin = _t(), _bruker(migrator, "a")
    _firma(migrator, t, admin)
    _sett_kontekst(migrator, t)
    _, h = _token()
    with pytest.raises(psycopg.Error):
        migrator.execute(
            "INSERT INTO firmainvitasjon (token_hash, tenant, roller,"
            " opprettet_av, utloper) VALUES (%s,%s,ARRAY[]::TEXT[],'kari',"
            " now() + interval '1 day')", (h, t))
    migrator.rollback()
