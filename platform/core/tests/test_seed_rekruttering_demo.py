"""Demo-seeden må sette claimer-rollen i HVER transaksjon der den kaller
en claimer-eid funksjon (Codex P1 + Cursor P1-1, runde 9).

`deploy/staging/seed-rekruttering-demo.py` kobler seg som migrator
(`DISPONIT_MIGRATOR_URL`). Funksjonene den kaller — `opprett_
rekrutteringsprosess`, `lukk_rekrutteringsprosess`, `opprett_
utsendingsliste` — eies av `disponit_m37_claimer`, 057 §7 og 056 revoker
PUBLIC, og `migrer.py` gir EXECUTE bare til RUNTIME-rollen. Migrator har
`INHERIT FALSE` og altså ingen vei inn utenom `SET LOCAL ROLE`.

Og `SET LOCAL ROLE` er LOCAL: den dør ved COMMIT. Seeden committer fire
ganger, så rollen må settes på nytt i hver bolk som trenger den — nøyaktig
det seed-3 IKKE gjorde. Feilen er dyr fordi den kommer MIDT I: seed-1..2
er alt committet når `lukk_rekrutteringsprosess` kaster
`InsufficientPrivilege`, så demoen etterlater kandidatdata og et `utfort`
oppdrag uten den signerbare listen den finnes for.

Porten er statisk og leser kilden med `ast` — seeden kan ikke importeres
(den krever base og miljø ved kall), og skal kunne måles overalt suiten
kjører. Den måler kildens LINJEREKKEFØLGE, som er den samme som
kjørerekkefølgen i et flatt skript som dette: for hvert kall til en
claimer-eid funksjon skal det stå en rollesetting mellom kallet og
nærmeste foregående `commit()`. Eierskapsfasiten hentes fra
`eierskap-reparasjon.sql` selv, gjennom `test_eierskap._design_fra_sql`,
så en funksjon som bytter eier ikke etterlater en glemt kopi her.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo  # noqa: F401
from .test_eierskap import _design_fra_sql
from .test_pr010_db import _ctx, _identitet

ROT = pathlib.Path(__file__).resolve().parents[3]
KILDE = ROT / "deploy" / "staging" / "seed-rekruttering-demo.py"

ROLLE = "SET LOCAL ROLE disponit_m37_claimer"
pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _claimereide_funksjoner() -> set[str]:
    """Navnene på funksjonene `disponit_m37_claimer` eier."""
    return {ident.split("(", 1)[0]
            for (art, ident), eier in _design_fra_sql().items()
            if art == "FUNCTION" and eier == "disponit_m37_claimer"}


def _seedens_kall() -> list[tuple[int, str]]:
    """(linje, sql) for hver `<conn>.execute("…")` og hver `.commit()`.

    `.commit()` gjengis som `COMMIT`, så bolkene kan leses av samme
    liste. Kall med et ikke-konstant første argument hoppes over: de
    finnes ikke i denne fila, og en port som gjettet på dem ville vært
    en simulator, ikke en måling.
    """
    tre = ast.parse(KILDE.read_text(encoding="utf-8"))
    kall: list[tuple[int, str]] = []
    for node in ast.walk(tre):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr == "commit":
            kall.append((node.lineno, "COMMIT"))
        elif (node.func.attr == "execute" and node.args
              and isinstance(node.args[0], ast.Constant)
              and isinstance(node.args[0].value, str)):
            kall.append((node.lineno, node.args[0].value))
    return sorted(kall)


def test_claimerkall_star_under_claimerrollen():
    """Drepende mutasjon: fjern `SET LOCAL ROLE` foran ETT av kallene.

    Da står funksjonen igjen som migrator, som verken eier den eller har
    EXECUTE — `test_rekruttering_http._reap` dokumenterer nettopp den
    `InsufficientPrivilege`-en.
    """
    eide = _claimereide_funksjoner()
    assert "lukk_rekrutteringsprosess" in eide, (
        "eierskapsfasiten kjenner ikke lukkingen — porten måler ingenting")
    rolle_satt = False
    sett: list[str] = []
    for _linje, sql in _seedens_kall():
        if sql == "COMMIT":
            rolle_satt = False
        elif sql == ROLLE:
            rolle_satt = True
        for navn in eide:
            if navn + "(" in sql:
                sett.append(navn)
                assert rolle_satt, (
                    f"seeden kaller {navn} uten `{ROLLE}` i samme"
                    " transaksjon — migrator har ingen EXECUTE, og"
                    " seeden krasjer med alt før dette committet")
    assert sett, "porten fant ingen claimer-kall i seeden — les kilden"


def _kontekst_og_medlemskap_kall() -> list[tuple[int, str]]:
    """(linje, merke) for hver `commit()`, hvert `sett_kontekst(`-kall, og
    hvert `execute` som treffer `brukermedlemskap` eller `brukersesjon`.

    Egen liste fra `_seedens_kall()`: den fanger ikke `sett_kontekst`, som
    ikke er et attributt-kall (`m.execute(...)`) men et bart funksjonskall.

    `brukersesjon` er med som NEGATIVT merke: seeden skal ikke røre den
    tabellen i det hele tatt (se porten under).
    """
    tre = ast.parse(KILDE.read_text(encoding="utf-8"))
    kall: list[tuple[int, str]] = []
    for node in ast.walk(tre):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "commit":
            kall.append((node.lineno, "COMMIT"))
        elif (isinstance(node.func, ast.Name)
              and node.func.id == "sett_kontekst"):
            kall.append((node.lineno, "KONTEKST"))
        elif (isinstance(node.func, ast.Attribute)
              and node.func.attr == "execute" and node.args
              and isinstance(node.args[0], ast.Constant)
              and isinstance(node.args[0].value, str)):
            sql = node.args[0].value
            if "brukermedlemskap" in sql:
                kall.append((node.lineno, "MEDLEMSKAP"))
            elif "brukersesjon" in sql:
                kall.append((node.lineno, "BRUKERSESJON"))
    return sorted(kall)


def test_medlemskapsoppslaget_gar_med_kontekst_og_gjetter_ikke_tenanten():
    """RLS-porten Cursor-passet 24/8 etterlyste: seeden koblet som migrator
    og leste `brukermedlemskap` UTEN tenantkontekst — tabellen har
    ENABLE+FORCE RLS (`tenant_isolasjon`, 010 §8) og migrator er ikke
    BYPASSRLS, så oppslaget var tomt hver gang. Prod 24/8 var nøyaktig det.

    Drepende mutasjon: fjern `sett_kontekst(m, a.tenant, ...)` foran
    medlemskaps-SELECTen. Uten porten er regressen usynlig i CI: SET LOCAL
    dør ved COMMIT (`db/pg.py`), og funksjonen har ingen tidligere commit
    som kunne latt en glemt kontekst overleve fra en annen bolk.

    Andre halvdel er Codex' P1/P2 på `1231a893`: seeden skal ikke GJETTE
    tenanten fra `brukersesjon`. Den tabellen er sesjonsHISTORIKK, ikke et
    medlemskapsregister — en identitet som er aktiv i A og B, men bare
    innlogget i A, ga kandidatlista [A] og dermed en «entydig» tenant som
    slapp forbi flertydighetssjekken; og spørringen filtrerte på den
    ikke-ledende `bruker_id` i `brukersesjon_bruker`
    (tenant, bruker_id, opprettet, id) + DISTINCT, altså en full scan som
    vokser med sesjonshistorikken. Drepende mutasjon: gjeninnfør
    `SELECT DISTINCT tenant FROM brukersesjon ...`, eller sett `--tenant`
    tilbake til valgfri.
    """
    kontekst_siden_commit = False
    fant_medlemskap = False
    for _linje, merke in _kontekst_og_medlemskap_kall():
        if merke == "COMMIT":
            kontekst_siden_commit = False
        elif merke == "KONTEKST":
            kontekst_siden_commit = True
        elif merke == "BRUKERSESJON":
            raise AssertionError(
                "seeden spør `brukersesjon` — sesjonshistorikk er ikke et"
                " medlemskapsregister (medlemskap uten sesjonsrad er"
                " usynlig, så flertydighet blir borte), og oppslaget"
                " treffer ingen indeks. Tenanten skal komme fra --tenant")
        elif merke == "MEDLEMSKAP":
            fant_medlemskap = True
            assert kontekst_siden_commit, (
                "seeden leser/skriver brukermedlemskap uten sett_kontekst i"
                " samme transaksjon — RLS+FORCE gjør oppslaget tomt for"
                " migrator (målt på prod 24/8)")
    assert fant_medlemskap, "porten fant ingen brukermedlemskap-kall i seeden"
    assert _tenantflagget_er_pakrevd(), (
        "--tenant er ikke lenger required — da er seeden tilbake til å måtte"
        " utlede tenanten selv, og migrator kan ikke det (FORCE RLS)")


def _tenantflagget_er_pakrevd() -> bool:
    """True hvis `add_argument("--tenant", ...)` har `required=True`."""
    tre = ast.parse(KILDE.read_text(encoding="utf-8"))
    for node in ast.walk(tre):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument" and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "--tenant"):
            return any(k.arg == "required"
                       and isinstance(k.value, ast.Constant)
                       and k.value.value is True
                       for k in node.keywords)
    raise AssertionError("porten fant ikke --tenant-flagget i seeden")


@pg
def test_medlemskapsoppslag_uten_kontekst_tomt_med_kontekst_funnet(migrator):
    """Prod-symptomet 24/8, reprodusert mot ekte RLS — ikke bare gjettet fra
    kildens linjerekkefølge slik porten over gjør. `brukermedlemskap` har
    ENABLE+FORCE RLS (`tenant_isolasjon`, 010 §8): uten `disponit.tenant`
    er oppslaget tomt for migrator uansett hvor mange rader som finnes; med
    konteksten satt finner det raden.
    """
    from db.pg import sett_kontekst
    t = "t-seed-rls-oppslag"
    _ctx(migrator, t)
    migrator.execute("DELETE FROM brukermedlemskap WHERE tenant=%s", (t,))
    migrator.commit()
    _ctx(migrator, t)
    bid = _identitet(migrator)
    migrator.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller)"
        " VALUES (%s,%s,ARRAY['leser'])", (t, bid))
    migrator.commit()
    # Uten kontekst: SET LOCAL fra INSERT-transaksjonen døde ved COMMIT
    # over — nøyaktig det seed-demoen gjorde før denne PR-en.
    tomt = migrator.execute(
        "SELECT tenant, roller FROM brukermedlemskap"
        " WHERE bruker_id=%s AND aktiv", (bid,)).fetchall()
    assert tomt == [], (
        "uten sett_kontekst skal RLS+FORCE gjøre brukermedlemskap-oppslaget"
        " tomt for migrator — er raden synlig likevel, er ikke dette lenger"
        " prod-symptomet fiksen retter, og AST-porten over måler ingenting")
    sett_kontekst(migrator, t, "test", "test-rls-oppslag")
    funnet = migrator.execute(
        "SELECT tenant, roller FROM brukermedlemskap"
        " WHERE bruker_id=%s AND aktiv", (bid,)).fetchall()
    assert funnet == [(t, ["leser"])]
    sett_kontekst(migrator, t, "test", "test-rls-oppslag-rydd")
    migrator.execute("DELETE FROM brukermedlemskap WHERE tenant=%s", (t,))
    migrator.commit()


def _flettefeltene() -> set[str]:
    """Feltnavnene seeden legger i `flettefelt`, lest ut av kilden.

    Verdiene er dels f-strenger (kandidatens tidsvalglenke) og kan ikke
    evalueres statisk — men det er NØKKELSETTET malen måler, og det står
    som rene konstanter.
    """
    tre = ast.parse(KILDE.read_text(encoding="utf-8"))
    for node in ast.walk(tre):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "flettefelt"
                and isinstance(node.value, ast.Dict)):
            return {n.value for n in node.value.keys
                    if isinstance(n, ast.Constant)}
    raise AssertionError("fant ingen `flettefelt` i seeden")


def test_flettefeltene_er_malens_minus_utstedelsens():
    """M-8 (082, §5): `tidsvalg_lenke` er flyttet fra LAGER til
    UTSTEDELSE — utsenderen minter tokenet og OVERSKRIVER feltet i
    sendeøyeblikket, så seedens lagrede felt er malens MINUS lenken.
    En placeholder i lageret ville vært en død lenke i venteposisjon.

    Porten fra før består i sin nye form: `maler.flett` avviser
    fortsatt et manglende felt, og seedens flett-kall bærer lenken
    TRANSIENT (som utsenderen) — fjernes den derfra, dør seeden på
    `flettefelt_mangler` før noe signeres.
    """
    from modules.m57_ats import maler
    assert _flettefeltene() == \
        set(maler.MALER["invitasjon"]["felter"]) - {"tidsvalg_lenke"}
    kilde = KILDE.read_text(encoding="utf-8")
    assert '"tidsvalg_lenke": "https://ikke-lagret.invalid' in kilde, \
        "seedens flett-kall skal bære lenken transient (port 14)"


def test_seeden_lukker_prosessen_i_utfort_transaksjonen():
    """Lukkingen og `utfort` er ÉN overgang (Codex P2, runde 8), og
    porten over ville vært fornøyd med en lukking som sto hvor som helst
    bak en rollesetting. Her måles at de to fortsatt deler bolk: ingen
    `commit()` mellom oppdragets `utfort` og lukkingen.
    """
    etter_utfort = False
    for _linje, sql in _seedens_kall():
        if "status='utfort'" in sql:
            etter_utfort = True
        elif etter_utfort and sql == "COMMIT":
            raise AssertionError(
                "seeden committer mellom `utfort` og lukkingen — en"
                " halv overgang etterlater den forlatte prosessen"
                " `reap_kandidatdata` måler fra fødselen")
        elif etter_utfort and "lukk_rekrutteringsprosess(" in sql:
            return
    raise AssertionError("fant ikke lukkingen etter `utfort` i seeden")
