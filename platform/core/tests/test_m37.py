"""PR-006: M-37 behandlingsmotor — de ti Codex-portene og de tre vilkårene.

Hver port har en test som DØR når vakten sin fjernes. Det er kravet fra
klarsignalet, og det er også den eneste måten «porten finnes» kan skilles
fra «porten virker». Fem runder på PR #8 handlet om nøyaktig den
forskjellen: porten fantes, men dekket ikke alt den ga inntrykk av.

Testene som ikke trenger database står først, slik at en kjøring uten
`DISPONIT_TEST_DSN` fortsatt sier noe. De er merket i navnet.
"""
import ast
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from .conftest import CORE
from .test_api import (DSN, MIGRATOR_DSN, NOKLER, TENANT, _lag_token, _rydd,
                       attestasjon, dekker, hendelse, migrator, miljo,  # noqa: F401
                       malpolicy, policy, post, token)                  # noqa: F401

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

EIERMODUL = "eiermodul:reinnsending"
EIERNOKKEL = {"n1": "e" * 40}


# ===========================================================================
# Statiske porter — ingen database
# ===========================================================================

def _importer(sti: Path) -> set[str]:
    """Toppnivåmodulene en fil importerer. AST, ikke grep.

    Grep ville truffet ordet «m37» i en kommentar og bommet på
    `importlib.import_module("m37.arbeider")`. AST ser den faktiske
    importsetningen — og bare den.
    """
    tre = ast.parse(sti.read_text(encoding="utf-8"))
    ut: set[str] = set()
    for node in ast.walk(tre):
        if isinstance(node, ast.Import):
            ut.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            ut.add(node.module.split(".")[0])
    return ut


def test_port0_api_importerer_aldri_m37():
    """PR-006 §0: M-37 kjører som EGEN PROSESS, aldri i forespørselsveien.

    Ytelsesporten er målt med 3,4× spredning og p99 207 ms; verste kjøring
    brukte 55 % av budsjettet. Arbeid lagt inn i request-path spiser den
    marginen — og en prosessgrense som bare står i en spesifikasjon er en
    påstand.

    Mutasjonen som dreper denne: legg `from m37 import reparasjoner` i
    `api/app.py`. Da er grensen borte, og testen skal si det.
    """
    for fil in sorted((CORE / "api").glob("*.py")):
        assert "m37" not in _importer(fil), (
            f"{fil.name} importerer m37 — prosessgrensen fra PR-006 §0 er"
            " brutt. Delte KONTRAKTER hører hjemme i platform/core"
            " (se oppdragskontrakt.py), ikke i m37/.")


def test_port10_syntetisk_eiermodul_skriver_aldri_direkte_i_databasen():
    """Codex-port 10 + evidensbevis 8 (v3-delta).

    Den syntetiske eiermodulen på staging er BEVISET for at
    outbox-protokollen virker ende-til-ende. Skriver den i databasen selv,
    beviser den bare at vi kan skrive i vår egen database — og
    feilinjiseringsartefaktet ville sagt «protokollen virker» om noe helt
    annet.

    Statisk sjekk fordi den er billig og ikke kan glemmes: modulen skal
    ikke engang KUNNE nå en databasedriver.
    """
    sti = Path(CORE).parents[1] / "deploy/staging/syntetisk-eiermodul.py"
    assert sti.is_file(), "den syntetiske eiermodulen mangler"
    importer = _importer(sti)
    for forbudt in ("psycopg", "psycopg2", "sqlalchemy", "db"):
        assert forbudt not in importer, (
            f"eiermodulen importerer {forbudt} — den skal KUN bruke de to"
            " ordinære endepunktene /v1/oppdrag/claim og"
            " /v1/oppdrag/kvittering")
    tekst = sti.read_text(encoding="utf-8")
    for forbudt in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
        assert forbudt not in tekst.upper().replace("UPDATE_", ""), (
            f"eiermodulen inneholder SQL ({forbudt})")


def test_registeret_er_lukket_og_dekker_taksonomien():
    """Reparasjonsregisteret validerer, og ingen kategori er hjemløs."""
    from m37 import reparasjoner
    assert reparasjoner.valider_register() == []


def test_registerets_kategorier_finnes_i_policyskjemaet():
    """CI-porten fra v1 §2: registeret kan bare inneholde kategorier
    policy-skjemaet faktisk kjenner.

    Uten den kunne en handler deklarert `manglende_dat` (skrivefeil) og
    aldri fått en sak — stille, for alltid.
    """
    from m37 import reparasjoner
    from m37.taksonomi import M37_TAKSONOMI_V1, SIKKERHETSKATEGORIER
    obligatoriske = {"manglende_data", "over_grense", "regelkonflikt",
                     "teknisk_feil", "ugyldig_data", "ukjent"}
    assert obligatoriske <= M37_TAKSONOMI_V1
    dekket = frozenset().union(*(h.kategorier for h in reparasjoner.REGISTER))
    assert dekket == M37_TAKSONOMI_V1 - SIKKERHETSKATEGORIER


def test_plattformtaket_er_likt_i_kode_og_database():
    """`LEAST(snapshot, 3)` står to steder: i `claim_neste_sak` og i
    `taksonomi.PLATTFORM_MAKS_FORSOK`. To tall som MÅ være like og bor to
    steder, blir ulike — med mindre noe binder dem sammen."""
    from m37.taksonomi import PLATTFORM_MAKS_FORSOK
    sql = (CORE / "db/migrations/005_m37_behandling.sql").read_text(
        encoding="utf-8")
    assert f"k.maks_auto_forsok_snapshot, 0), {PLATTFORM_MAKS_FORSOK})" in sql


def test_port7_payloadfelt_utenfor_skjemaet_slipper_aldri_ut():
    """Codex-port 7: LUKKET feltskjema per oppdragstype.

    Mutasjonen som dreper denne: bytt `felter` til en blocklist, eller la
    `minimer` returnere payloaden uendret.
    """
    import oppdragskontrakt
    payload = {"handling": "purring.send", "ressurs_id": "fak-1",
               "personnummer": "01019012345", "epost": "kari@example.no",
               "hemmelig_notat": "dette skal aldri ut", "belop": 100}
    ut = oppdragskontrakt.minimer("reinnsending", payload)
    for lekkasje in ("personnummer", "epost", "hemmelig_notat"):
        assert lekkasje not in ut, f"{lekkasje} slapp gjennom minimeringen"
    assert ut["handling"] == "purring.send" and ut["belop"] == 100

    # Verifikasjonsoppdrag ser ALDRI beløp — en modul som skal slå opp mot
    # en autoritativ kilde trenger ikke vite hva saken gjaldt i kroner.
    assert "belop" not in oppdragskontrakt.minimer("verifikasjon", payload)


def test_oppdragstypenes_prefikser_er_disjunkte():
    """Overlappende prefikser ville gjort feltbredden avhengig av
    rekkefølgen i en dict."""
    import oppdragskontrakt
    alle = [(t.navn, p) for t in oppdragskontrakt.OPPDRAGSTYPER.values()
            for p in t.handlingsprefikser]
    for navn_a, pre_a in alle:
        for navn_b, pre_b in alle:
            if navn_a >= navn_b:
                continue
            assert not (pre_a.startswith(pre_b) or pre_b.startswith(pre_a)), \
                f"prefiksene {pre_a!r} ({navn_a}) og {pre_b!r} ({navn_b}) overlapper"


# ===========================================================================
# JCS — RFC 8785
# ===========================================================================

def test_jcs_rfc8785_testvektorer():
    """Vektorene som skiller ekte JCS fra `json.dumps(sort_keys=True)`."""
    from policy_validator import jcs
    assert jcs.kanoniser({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert jcs.kanoniser(1.0) == "1"          # ES6: ingen ".0"
    assert jcs.kanoniser(-0.0) == "0"         # ES6: -0 skrives som 0
    assert jcs.kanoniser(1e21) == "1e+21"     # eksponentgrensen
    assert jcs.kanoniser(1e20) == "100000000000000000000"
    assert jcs.kanoniser("æ") == '"æ"'        # literal, ikke æ
    assert jcs.kanoniser("\n") == '"\\n"'
    # UTF-16-sortering: "€" (U+20AC) etter "é" (U+00E9) etter "$" (U+0024)
    assert jcs.kanoniser({"€": 1, "$": 2, "é": 3}) == '{"$":2,"é":3,"€":1}'


def test_jcs_avviser_det_default_str_konverterte_stille():
    """`default=str` gjorde en Decimal til en streng og signerte den.

    Da signerte to implementasjoner ULIKE bytes for samme objekt, og
    signaturen var verdiløs uten at noe feilet. Nå er det en feil.
    """
    from decimal import Decimal
    from policy_validator import jcs
    for verdi in (Decimal("1.5"), datetime.now(timezone.utc), {1: "a"},
                  float("nan"), float("inf")):
        with pytest.raises(jcs.Ikkekanoniserbar):
            jcs.kanoniser(verdi)


def test_ikke_jcs_attestasjon_avvises_paa_nettverksveien():
    """Lukket format: manglende eller ukjent `kanonisering` avvises.

    Feltet ligger INNE i de signerte bytene, så det kan ikke byttes uten at
    signaturen ryker. Denne testen dekker det ANDRE tilfellet: en
    verifikator som signerer korrekt, men med et annet format.
    """
    from policy_validator import attestering
    a = attestasjon("forfall_passert_dager", "fak-1", "p", verdi=20)
    assert a["kanonisering"] == "JCS"
    assert attestering.verifiser(a, NOKLER)

    uten = {k: v for k, v in a.items() if k != "kanonisering"}
    assert not attestering.verifiser(uten, NOKLER)
    grunn = attestering.kontroller_hendelse({"attestasjoner": {"x": uten}},
                                            NOKLER)
    assert grunn is not None and grunn.kode == "attestasjon_kanonisering_ukjent"


# ===========================================================================
# Databaseporter
# ===========================================================================


def _rt(sql, args=(), *, aktor="m37-arbeider", rid="r", tenant=None):
    """Kall en herdet SECURITY DEFINER-funksjon som RUNTIME-rollen.

    Testene setter opp data som migrator (den eier skjemaet), men
    funksjonene er gitt EXECUTE til RUNTIME og ingen andre. Å gi migrator
    EXECUTE bare for at testene skulle bli enklere, ville vært å utvide en
    produksjonsrolle for testenes skyld — og da måler testen et annet
    rettighetsoppsett enn det som driftes.

    Egen tilkobling per kall: SET LOCAL forsvinner ved commit uansett, og
    en delt tilkobling ville båret kontekst mellom kallene.
    """
    from db.pg import koble
    c = koble(DSN)
    try:
        c.execute("SELECT set_config('disponit.aktor',%s,true),"
                  "       set_config('disponit.request_id',%s,true)",
                  (aktor, rid))
        if tenant:
            c.execute("SELECT set_config('disponit.tenant',%s,true)", (tenant,))
        rad = c.execute(sql, args).fetchone()
        c.commit()
        return rad
    finally:
        c.close()


def _sett_kontekst(conn, tenant, aktor="test", rid="r"):
    conn.execute("SELECT set_config('disponit.tenant',%s,true),"
                 "       set_config('disponit.aktor',%s,true),"
                 "       set_config('disponit.request_id',%s,true)",
                 (tenant, aktor, rid))


def _lag_sak(conn, tenant, *, kategori="manglende_data", handling="purring.send",
             snapshot=3, hash_="1" * 64, versjon="1.0.0", sakstype="normal"):
    """En unntaksrad med policysnapshot, som API-veien ville laget den."""
    _sett_kontekst(conn, tenant)
    logg = conn.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash, policy_id,"
        " beslutning, begrunnelse, policy_content_hash)"
        " VALUES (%s,'test','test','ih','p','UNNTAK','[]',%s) RETURNING id",
        (tenant, hash_)).fetchone()[0]
    from db import kryptering
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
    ct, nonce = kryptering.krypter(
        dek, {"handling": handling, "ressurs_id": "fak-1",
              "kategori": kategori, "begrunnelse": ["manglende_felt"]},
        tenant, key_id)
    sak = conn.execute(
        "INSERT INTO unntak (tenant, loggpost_id, handling, kategori, sakstype,"
        " payload_kryptert, key_id, nonce, maks_auto_forsok_snapshot,"
        " policy_versjon, policy_content_hash)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (tenant, logg, handling, kategori, sakstype, ct, key_id, nonce,
         snapshot, versjon, hash_)).fetchone()[0]
    conn.commit()
    return int(sak), int(logg)


@pg
def test_port2_annen_request_id_kan_ikke_overta_en_reservasjon(migrator):
    """Codex-port 2 + v4-delta pkt. 1.4.

    Gjenopptak er bundet til request_id: SAMME forespørsel kan ta opp igjen
    sin egen reservasjon, enhver ANNEN avvises. Uten den bindingen kunne en
    parallell forespørsel overtatt en reservasjon midt i en pågående
    transaksjon — og to forespørsler ville delt én engangsfullmakt.

    Mutasjonen som dreper denne: fjern `AND k.request_id = p_request_id`
    fra gjenopptaksgrenen i `reserver_kapabilitet`.
    """
    sak, _ = _lag_sak(migrator, TENANT)
    cid = secrets.token_hex(16)
    _sett_kontekst(migrator, TENANT, "m37-arbeider", cid)
    migrator.execute(
        "UPDATE unntak SET status='under_behandling', claim_id=%s,"
        " claim_generation=1, claim_utloper=now()+interval '120 s'"
        " WHERE tenant=%s AND id=%s", (cid, TENANT, sak))
    migrator.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant, unntak_id,"
        " repair_operation_id, repair_generation, handler_id, handler_versjon,"
        " maalhandling, input_hash, kategori)"
        " VALUES (%s,%s,%s,0,'r1_reinnsending','1','purring.send',%s,"
        " 'manglende_data')", (TENANT, sak, "a" * 64, "b" * 64))
    migrator.commit()

    jti = secrets.token_hex(16)
    assert _rt("SELECT jti FROM utsted_arbeidskapabilitet(%s,1,%s,60)",
               (cid, jti)) is not None, "kapabiliteten ble ikke utstedt"

    assert _rt("SELECT tenant FROM reserver_kapabilitet(%s,'req-A',300)",
               (jti,)) is not None, "første reservasjon feilet"
    assert _rt("SELECT tenant FROM reserver_kapabilitet(%s,'req-B',300)",
               (jti,)) is None, "en ANNEN request_id overtok reservasjonen"
    assert _rt("SELECT tenant FROM reserver_kapabilitet(%s,'req-A',300)",
               (jti,)) is not None, "eieren kunne ikke gjenoppta sin egen"


@pg
def test_vilkaar_V1_kapabilitet_kan_aldri_overleve_claimen(migrator):
    """GO-vilkår V1: reservasjon_utloper <= kapabilitet_utloper <= claim_utloper.

    To lag, og begge måles her. Clampen i `utsted_arbeidskapabilitet` gir en
    RIKTIG verdi; triggeren `kapabilitet_tidsgrense` gjør en GAL verdi
    umulig — også for en fremtidig andre vei inn i tabellen. Et lag alene
    ville vært «porten dekket bare den veien jeg tenkte på».
    """
    sak, _ = _lag_sak(migrator, TENANT)
    cid = secrets.token_hex(16)
    _sett_kontekst(migrator, TENANT, "m37-arbeider", cid)
    # Kort lease: 30 s er minimum claim-funksjonen tillater.
    migrator.execute(
        "UPDATE unntak SET status='under_behandling', claim_id=%s,"
        " claim_generation=1, claim_utloper=now()+interval '30 s'"
        " WHERE tenant=%s AND id=%s", (cid, TENANT, sak))
    migrator.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant, unntak_id,"
        " repair_operation_id, repair_generation, handler_id, handler_versjon,"
        " maalhandling, input_hash, kategori)"
        " VALUES (%s,%s,%s,0,'r1_reinnsending','1','purring.send',%s,"
        " 'manglende_data')", (TENANT, sak, "c" * 64, "d" * 64))
    migrator.commit()

    # Lag 1: be om 300 s levetid mot en 30-sekunders lease.
    jti = secrets.token_hex(16)
    utloper = _rt("SELECT utloper FROM utsted_arbeidskapabilitet(%s,1,%s,300)",
                  (cid, jti))[0]
    # Konteksten må settes PÅ NYTT: `SET LOCAL` forsvinner ved commit, og
    # uten `disponit.tenant` ser RLS null rader — også for skjemaeieren.
    _sett_kontekst(migrator, TENANT)
    claim_utloper = migrator.execute(
        "SELECT claim_utloper FROM unntak WHERE tenant=%s AND id=%s",
        (TENANT, sak)).fetchone()[0]
    assert utloper <= claim_utloper, (
        f"kapabiliteten ({utloper}) overlever claimen ({claim_utloper})")
    migrator.commit()

    # Lag 2: forsøk å skrive en for lang utløpstid DIREKTE i tabellen.
    # Migrator er ikke eier (rollen er disponit_m37_claimer), så vi går
    # gjennom eierrollen — nettopp for å bevise at triggeren biter selv da.
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    with pytest.raises(Exception) as feil:
        migrator.execute(
            "INSERT INTO arbeidskapabiliteter (jti, tenant, unntak_id,"
            " claim_id, claim_generation, repair_operation_id,"
            " tillatt_handling, utloper)"
            " VALUES (%s,%s,%s,%s,1,%s,'purring.send', now()+interval '1 h')",
            (secrets.token_hex(16), TENANT, sak, cid, "c" * 64))
    assert "GO-vilkår V1" in str(feil.value)
    migrator.rollback()

    # Reservasjonsfristen kan heller aldri overstige kapabiliteten.
    # Lesingen går via eierrollen: `arbeidskapabiliteter` eies av
    # disponit_m37_claimer, og migrator arver ikke dens rettigheter.
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    reservasjon = migrator.execute(
        "SELECT reservasjon_utloper, utloper FROM arbeidskapabiliteter"
        " WHERE jti=%s", (jti,)).fetchone()
    migrator.rollback()
    assert reservasjon[0] is None or reservasjon[0] <= reservasjon[1]


@pg
def test_port3_utlopt_claim_kan_ikke_bruke_kapabiliteten(migrator):
    """Codex-port 3: en kapabilitet fra en død claim er død.

    Mutasjonen som dreper denne: fjern `AND k.utloper > now()` fra
    `bruk_kapabilitet`.
    """
    sak, _ = _lag_sak(migrator, TENANT)
    cid = secrets.token_hex(16)
    _sett_kontekst(migrator, TENANT, "m37-arbeider", cid)
    migrator.execute(
        "UPDATE unntak SET status='under_behandling', claim_id=%s,"
        " claim_generation=1, claim_utloper=now()+interval '30 s'"
        " WHERE tenant=%s AND id=%s", (cid, TENANT, sak))
    migrator.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant, unntak_id,"
        " repair_operation_id, repair_generation, handler_id, handler_versjon,"
        " maalhandling, input_hash, kategori)"
        " VALUES (%s,%s,%s,0,'r1_reinnsending','1','purring.send',%s,"
        " 'manglende_data')", (TENANT, sak, "e" * 64, "f" * 64))
    migrator.commit()

    # `utloper` er UFORANDERLIG — vakten stopper enhver endring, og det er
    # riktig: en kapabilitet man kan forlenge er ingen tidsgrense. Vi legger
    # derfor inn en kapabilitet som ALLEREDE er utløpt, slik den ville sett
    # ut hvis leasen gikk ut mens nettverkskallet pågikk.
    jti = secrets.token_hex(16)
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    migrator.execute(
        "INSERT INTO arbeidskapabiliteter (jti, tenant, unntak_id, claim_id,"
        " claim_generation, repair_operation_id, tillatt_handling, status,"
        " request_id, reservasjon_utloper, utloper)"
        " VALUES (%s,%s,%s,%s,1,%s,'purring.send','utstedt',NULL,NULL,"
        " now() - interval '1 s')",
        (jti, TENANT, sak, cid, "e" * 64))
    migrator.execute(
        "UPDATE arbeidskapabiliteter SET status='reservert', request_id='req-A',"
        " reservasjon_utloper=now() - interval '2 s' WHERE jti=%s", (jti,))
    migrator.execute("RESET ROLE")
    migrator.commit()

    assert _rt("SELECT bruk_kapabilitet(%s,'req-A')", (jti,))[0] is False, \
        "en utløpt kapabilitet ble brukt"


@pg
def test_port1_kapabilitet_forblir_reservert_ved_krasj_og_kan_gjenopptas(migrator):
    """Codex-port 1: en kapabilitet brennes ALDRI uten auditert beslutning.

    Krasj mellom pre-auth (som reserverer) og commit (som brenner) skal
    etterlate kapabiliteten `reservert`, ikke `brukt` — ellers er
    engangsfullmakten borte uten at det finnes evidens for hva den ble
    brukt til, og arbeideren kan verken gjenoppta eller gi opp rent.

    Frigjøringen har en betingelse som er lett å overse og som denne testen
    måler: en reservasjon frigjøres KUN hvis det verken finnes en ferdig
    idempotensrespons eller en auditert beslutning med samme
    repair_operation_id. Uten den ville en treg, men VELLYKKET transaksjon
    fått kapabiliteten revet vekk under seg.
    """
    sak, _ = _lag_sak(migrator, TENANT)
    cid = secrets.token_hex(16)
    rid = "0" * 64
    _sett_kontekst(migrator, TENANT, "m37-arbeider", cid)
    migrator.execute(
        "UPDATE unntak SET status='under_behandling', claim_id=%s,"
        " claim_generation=1, claim_utloper=now()+interval '120 s'"
        " WHERE tenant=%s AND id=%s", (cid, TENANT, sak))
    migrator.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant, unntak_id,"
        " repair_operation_id, repair_generation, handler_id, handler_versjon,"
        " maalhandling, input_hash, kategori)"
        " VALUES (%s,%s,%s,0,'r1_reinnsending','1','purring.send',%s,"
        " 'manglende_data')", (TENANT, sak, rid, "1" * 64))
    migrator.commit()

    jti = secrets.token_hex(16)
    _rt("SELECT jti FROM utsted_arbeidskapabilitet(%s,1,%s,60)", (cid, jti))
    _rt("SELECT tenant FROM reserver_kapabilitet(%s,'req-A',300)", (jti,))

    # Lesingen går via EIERROLLEN: `arbeidskapabiliteter` eies av
    # disponit_m37_claimer, og migrator arver ikke dens rettigheter
    # (`WITH INHERIT FALSE`). At migrator ikke kan lese tabellen direkte er
    # ikke en ulempe her — det er kapabilitetsmodellen som virker.
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    assert migrator.execute(
        "SELECT status FROM arbeidskapabiliteter WHERE jti=%s",
        (jti,)).fetchone()[0] == "reservert", "kapabiliteten ble brent for tidlig"
    # Tving reservasjonsfristen utløpt (krasjet).
    migrator.execute("UPDATE arbeidskapabiliteter SET reservasjon_utloper="
                     "now() - interval '1 s' WHERE jti=%s", (jti,))
    migrator.execute("RESET ROLE")
    migrator.commit()

    # Tellingen er GLOBAL — funksjonen rydder alle hengende reservasjoner,
    # ikke bare vår. Assertionen må derfor gjelde DENNE kapabiliteten, ikke
    # et tall som avhenger av hva andre tester har lagt igjen. En assertion
    # som varierer med kjørerekkefølgen er ingen assertion.
    assert _rt("SELECT frigi_hengende_kapabiliteter()")[0] >= 1
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    assert migrator.execute(
        "SELECT status FROM arbeidskapabiliteter WHERE jti=%s",
        (jti,)).fetchone()[0] == "feilet"
    migrator.execute("RESET ROLE")
    migrator.rollback()

    # ANDRE HALVDEL: samme situasjon, men med en auditert beslutning som
    # bærer repair_operation_id. Da skal frigjøringen la den stå.
    sak2, _ = _lag_sak(migrator, TENANT)
    cid2 = secrets.token_hex(16)
    rid2 = "4" * 64
    _sett_kontekst(migrator, TENANT, "m37-arbeider", cid2)
    migrator.execute(
        "UPDATE unntak SET status='under_behandling', claim_id=%s,"
        " claim_generation=1, claim_utloper=now()+interval '120 s'"
        " WHERE tenant=%s AND id=%s", (cid2, TENANT, sak2))
    migrator.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant, unntak_id,"
        " repair_operation_id, repair_generation, handler_id, handler_versjon,"
        " maalhandling, input_hash, kategori)"
        " VALUES (%s,%s,%s,0,'r1_reinnsending','1','purring.send',%s,"
        " 'manglende_data')", (TENANT, sak2, rid2, "2" * 64))
    migrator.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash, policy_id,"
        " beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'m37','api','ih','p','TILLAT','[]',%s)", (TENANT, rid2))
    migrator.commit()

    jti2 = secrets.token_hex(16)
    _rt("SELECT jti FROM utsted_arbeidskapabilitet(%s,1,%s,60)", (cid2, jti2))
    _rt("SELECT tenant FROM reserver_kapabilitet(%s,'req-C',300)", (jti2,))
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    migrator.execute("UPDATE arbeidskapabiliteter SET reservasjon_utloper="
                     "now() - interval '1 s' WHERE jti=%s", (jti2,))
    migrator.execute("RESET ROLE")
    migrator.commit()

    assert _rt("SELECT frigi_hengende_kapabiliteter()")[0] == 0, (
        "en reservasjon med auditert beslutning ble frigjort — den treige"
        " men vellykkede transaksjonen ville mistet fullmakten sin")
    migrator.commit()


@pg
def test_kappløp_tjue_arbeidere_gir_noyaktig_en_claim_per_sak(migrator):
    """v1 §7: 20 samtidige arbeidere, hver sak claimes NØYAKTIG én gang.

    `FOR UPDATE SKIP LOCKED` er hele mekanismen. Fjernes SKIP LOCKED, blir
    dette en kø som serialiserer; fjernes FOR UPDATE, kan to arbeidere ta
    samme sak. Historikktellingen er beviset — den kan ikke lyve, fordi
    triggeren skriver én rad per faktisk statusskifte.
    """
    import threading
    saker = [_lag_sak(migrator, TENANT)[0] for _ in range(10)]
    from db.pg import koble

    resultat: list[int] = []
    laas = threading.Lock()
    start = threading.Barrier(20)

    def arbeider():
        c = koble(DSN)
        try:
            start.wait(timeout=20)
            for _ in range(3):
                cid = secrets.token_hex(16)
                c.execute("SELECT set_config('disponit.aktor','m37-arbeider',"
                          "true), set_config('disponit.request_id',%s,true)",
                          (cid,))
                rad = c.execute("SELECT id FROM claim_neste_sak(%s, 120)",
                                (cid,)).fetchone()
                c.commit()
                if rad is None:
                    break
                with laas:
                    resultat.append(int(rad[0]))
        finally:
            c.close()

    traader = [threading.Thread(target=arbeider) for _ in range(20)]
    for t in traader:
        t.start()
    for t in traader:
        t.join(timeout=60)

    assert sorted(resultat) == sorted(saker), (
        f"claimet {sorted(resultat)}, forventet {sorted(saker)}")
    assert len(resultat) == len(set(resultat)), (
        f"en sak ble claimet to ganger: {resultat}")

    _sett_kontekst(migrator, TENANT)
    claims = migrator.execute(
        "SELECT unntak_id, count(*) FROM unntak_historikk"
        " WHERE tenant=%s AND hendelse='claim' GROUP BY unntak_id",
        (TENANT,)).fetchall()
    migrator.rollback()
    assert all(n == 1 for _, n in claims), f"dobbel claim i historikken: {claims}"


@pg
def test_sikkerhets_og_driftssaker_claimes_aldri_av_normalarbeideren(migrator):
    """Kø-flom-vernet fra v2 Del 4, gjentatt i claim-funksjonen.

    Mutasjonen som dreper denne: fjern `AND k.sakstype = 'normal'`.
    """
    for sakstype in ("sikkerhet", "drift"):
        _lag_sak(migrator, TENANT, sakstype=sakstype)
    cid = secrets.token_hex(16)
    assert _rt("SELECT id FROM claim_neste_sak(%s, 120)", (cid,), rid=cid) \
        is None, "normal-arbeideren claimet en sikkerhets-/driftssak"


@pg
def test_sak_med_oppbrukte_forsok_claimes_aldri(migrator):
    """Effektiv grense = LEAST(snapshot, 3). Systemet kan stramme INN
    globalt, aldri løsne en kundes grense.

    Snapshot 0 (legacy-verdien fra backfillen) gjør saken uclaimbar for
    enhver forsok-verdi — det er aritmetikk, ikke en sjekk noen må huske.
    """
    _lag_sak(migrator, TENANT, snapshot=0)
    cid = secrets.token_hex(16)
    assert _rt("SELECT id FROM claim_neste_sak(%s,120)", (cid,), rid=cid) is None


@pg
def test_claim_prioriterer_hoy_for_normal(migrator):
    """Spesifikasjonen skriver `ORDER BY prioritet DESC`, men kolonnen er
    TEKST med verdiene 'hoy' og 'normal' — og 'hoy' < 'normal'. DESC ville
    altså sortert NORMAL FØRST og gjort høyprioriterte saker til de siste i
    køen. Rangeringen er derfor eksplisitt i `claim_neste_sak`."""
    _lag_sak(migrator, TENANT)                     # normal, eldst
    _sett_kontekst(migrator, TENANT)
    hoy, _ = _lag_sak(migrator, TENANT)
    migrator.execute("SET LOCAL ROLE disponit_migrator")
    _sett_kontekst(migrator, TENANT)
    migrator.execute("ALTER TABLE unntak DISABLE TRIGGER unntak_laas")
    migrator.execute("UPDATE unntak SET prioritet='hoy' WHERE tenant=%s"
                     " AND id=%s", (TENANT, hoy))
    migrator.execute("ALTER TABLE unntak ENABLE TRIGGER unntak_laas")
    migrator.commit()

    cid = secrets.token_hex(16)
    forst = _rt("SELECT id FROM claim_neste_sak(%s,120)", (cid,), rid=cid)[0]
    assert forst == hoy, "høyprioritert sak ble ikke claimet først"


@pg
def test_port9_referert_policyversjon_kan_ikke_slettes_heller_ikke_av_migrator(
        migrator):
    """Codex-port 9 + GO-vilkår V3.

    Poenget er hvem den gjelder for. `migrator` EIER skjemaet, og testen
    kjører som nettopp den rollen: den skal likevel ikke få slette en
    policyversjon revisjonsloggen viser til. Uten triggeren ville dette
    vært en helt vanlig, vellykket DELETE.

    Mutasjonen som dreper denne: `DROP TRIGGER policy_retention ON policyer`.
    """
    _sett_kontekst(migrator, TENANT)
    innhold = {"meta": {"policy_id": "pv", "versjon": "1", "status": "utkast"}}
    migrator.execute(
        "INSERT INTO policyer (tenant, policy_id, versjon, innholds_hash,"
        " status, innhold, aktiv) VALUES (%s,'pv','1',%s,'utkast',%s,false)"
        " ON CONFLICT DO NOTHING",
        (TENANT, "k" * 64, json.dumps(innhold)))
    migrator.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash, policy_id,"
        " beslutning, begrunnelse, policy_content_hash)"
        " VALUES (%s,'test','test','ih','pv','TILLAT','[]',%s)",
        (TENANT, "k" * 64))
    migrator.commit()

    _sett_kontekst(migrator, TENANT)
    with pytest.raises(Exception) as feil:
        migrator.execute("DELETE FROM policyer WHERE tenant=%s AND policy_id='pv'",
                         (TENANT,))
    assert "GO-vilkår V3" in str(feil.value)
    migrator.rollback()

    # Den sanksjonerte veien avviser den OGSÅ — den omgår den blanke
    # sperren, ikke selve regelen. En escape hatch som kunne slette en
    # referert versjon ville vært «en advarsel med exit 0».
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(Exception) as feil:
        migrator.execute("SELECT arkiver_policyversjon(%s,'pv','1')", (TENANT,))
    assert "referert" in str(feil.value)
    migrator.rollback()


@pg
def test_port9b_ureferert_policyversjon_kan_arkiveres(migrator):
    """Motstykket: uten referanser skal arkiveringen faktisk virke.

    Uten denne ville `test_port9` vært grønn selv om `arkiver_policyversjon`
    alltid kastet — altså en assertion som ikke kan feile av riktig grunn.
    """
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "INSERT INTO policyer (tenant, policy_id, versjon, innholds_hash,"
        " status, innhold, aktiv) VALUES (%s,'pv2','1',%s,'utkast','{}',false)"
        " ON CONFLICT DO NOTHING", (TENANT, "l" * 64))
    migrator.commit()
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute("SELECT arkiver_policyversjon(%s,'pv2','1')",
                            (TENANT,)).fetchone()[0] is True
    migrator.commit()


@pg
def test_policyer_kan_ikke_truncates(migrator):
    """TRUNCATE omgår rad-triggere fullstendig. Uten en egen
    statement-trigger ville hele retention-vakten vært én setning unna å
    være virkningsløs."""
    with pytest.raises(Exception) as feil:
        migrator.execute("TRUNCATE policyer")
    assert "TRUNCATE er forbudt" in str(feil.value)
    migrator.rollback()


@pg
def test_vilkaar_V2_backfill_bruker_hele_policyidentiteten(migrator, malpolicy):
    """GO-vilkår V2: tenant + policy_id + versjon + innholds_hash, med
    RE-HASHING av lagret innhold før `maks_auto_forsok` brukes.

    To saker: én med en verifiserbar historisk policyrad, én uten. Den
    første får sitt ekte snapshot, den andre blir `manuell` med
    legacy-verdier. Det er skillet mellom evidens og gjetning.

    Mutasjonen som dreper denne: la backfillen lese `maks_auto_forsok` fra
    den AKTIVE policyen i stedet for fra den historiske raden.
    """
    from api.policyregister import innholds_hash
    from db import m37_backfill

    # En EKTE policy, ikke en stubb. Backfillen kjører `valider_policy` før
    # den stoler på `maks_auto_forsok`, og det er med vilje: en policy som
    # ikke validerer kan ikke brukes som fasit for retrysemantikk. Et
    # førsteutkast med en minimal dict fikk `policy_ugyldig` — porten gjorde
    # nøyaktig jobben sin.
    import copy
    ekte = copy.deepcopy(malpolicy)
    ekte["meta"] = dict(ekte["meta"], policy_id="pbf", versjon="2.0.0")
    ekte["unntak"] = dict(ekte["unntak"], maks_auto_forsok=2)
    h = innholds_hash(ekte)
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "INSERT INTO policyer (tenant, policy_id, versjon, innholds_hash,"
        " status, innhold, aktiv) VALUES (%s,'pbf','2.0.0',%s,%s,%s,false)"
        " ON CONFLICT DO NOTHING",
        (TENANT, h, ekte['meta']['status'], json.dumps(ekte)))
    migrator.commit()

    med_evidens, _ = _lag_sak(migrator, TENANT, hash_=h)
    uten_evidens, _ = _lag_sak(migrator, TENANT, hash_="z" * 64)
    # Nullstill snapshotet slik backfillen ser det (kolonnelåsen tillater
    # kun NULL -> verdi, så vi går rundt den som skjemaeier — nøyaktig det
    # migrasjon 006 forutsetter at ikke skjer i drift).
    # Migrasjon 006 gjorde kolonnene NOT NULL. For å måle backfillen må vi
    # gjenskape tilstanden den er laget for — altså FØR 006. Sperren løftes
    # eksplisitt og settes tilbake nederst; at det må gjøres bevisst er i
    # seg selv en bekreftelse på at 006 er en port og ikke et notat.
    migrator.execute("ALTER TABLE unntak ALTER COLUMN"
                     " maks_auto_forsok_snapshot DROP NOT NULL,"
                     " ALTER COLUMN policy_versjon DROP NOT NULL,"
                     " ALTER COLUMN policy_content_hash DROP NOT NULL")
    migrator.execute("ALTER TABLE unntak DISABLE TRIGGER unntak_laas")
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "UPDATE unntak SET maks_auto_forsok_snapshot=NULL, policy_versjon=NULL,"
        " policy_content_hash=NULL WHERE tenant=%s AND id=%s",
        (TENANT, med_evidens))
    migrator.execute(
        "UPDATE unntak SET maks_auto_forsok_snapshot=NULL, policy_versjon=NULL,"
        " policy_content_hash=NULL WHERE tenant=%s AND id=%s",
        (TENANT, uten_evidens))
    # Loggposten til sak 1 må peke på riktig policy_id — hele identiteten.
    migrator.execute("ALTER TABLE revisjonslogg DISABLE TRIGGER"
                     " revisjonslogg_ingen_endring")
    migrator.execute(
        "UPDATE revisjonslogg SET policy_id='pbf' WHERE tenant=%s AND id="
        "(SELECT loggpost_id FROM unntak WHERE tenant=%s AND id=%s)",
        (TENANT, TENANT, med_evidens))
    migrator.execute("ALTER TABLE revisjonslogg ENABLE TRIGGER"
                     " revisjonslogg_ingen_endring")
    migrator.execute("ALTER TABLE unntak ENABLE TRIGGER unntak_laas")
    migrator.commit()

    res = m37_backfill.backfill(migrator)
    assert res.fra_evidens >= 1 and res.legacy >= 1, res

    # NOT NULL tilbake — og at den lar seg sette er selve beviset for at
    # backfillen faktisk fylte alle radene. Feiler den her, har backfillen
    # hoppet over noe, og migrasjon 006 ville stoppet deployet.
    migrator.execute("ALTER TABLE unntak ALTER COLUMN"
                     " maks_auto_forsok_snapshot SET NOT NULL,"
                     " ALTER COLUMN policy_versjon SET NOT NULL,"
                     " ALTER COLUMN policy_content_hash SET NOT NULL")
    migrator.commit()

    _sett_kontekst(migrator, TENANT)
    a = migrator.execute(
        "SELECT maks_auto_forsok_snapshot, policy_versjon, status FROM unntak"
        " WHERE tenant=%s AND id=%s", (TENANT, med_evidens)).fetchone()
    b = migrator.execute(
        "SELECT maks_auto_forsok_snapshot, policy_versjon, status FROM unntak"
        " WHERE tenant=%s AND id=%s", (TENANT, uten_evidens)).fetchone()
    migrator.rollback()
    assert a == (2, "2.0.0", "ny"), f"evidensveien ga {a}"
    assert b == (0, "legacy", "manuell"), f"legacy-veien ga {b}"


@pg
def test_port8_legacy_uten_verifiserbar_rad_blir_manuell_og_claimes_aldri(
        migrator):
    """Codex-port 8, andre halvdel: `manuell` er ikke bare en etikett.

    En legacy-sak skal ALDRI plukkes opp av arbeideren. To uavhengige
    grunner sørger for det — statusen og snapshot 0 — og testen måler
    utfallet, ikke mekanismen.
    """
    sak, _ = _lag_sak(migrator, TENANT, snapshot=0)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE unntak SET status='manuell' WHERE tenant=%s"
                     " AND id=%s", (TENANT, sak))
    migrator.commit()
    cid = secrets.token_hex(16)
    assert _rt("SELECT id FROM claim_neste_sak(%s,120)", (cid,), rid=cid) is None


@pg
def test_manuell_og_lost_er_terminale(migrator):
    """Terminaltilstandene er terminale. Uten den eksplisitte kontrollen
    kunne en avsluttet sak gjenåpnes gjennom en tilsynelatende lovlig
    kjede av overganger."""
    sak, _ = _lag_sak(migrator, TENANT)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE unntak SET status='manuell' WHERE tenant=%s"
                     " AND id=%s", (TENANT, sak))
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(Exception) as feil:
        migrator.execute("UPDATE unntak SET status='under_behandling'"
                         " WHERE tenant=%s AND id=%s", (TENANT, sak))
    assert "terminal" in str(feil.value) or "ulovlig statusovergang" in str(feil.value)
    migrator.rollback()


@pg
def test_claim_generation_kan_aldri_reduseres(migrator):
    """Fencing-tokenet er monotont. Kunne generasjonen settes ned, ville et
    gammelt token blitt gyldig igjen — som er hele angrepet fencing finnes
    for."""
    sak, _ = _lag_sak(migrator, TENANT)
    cid = secrets.token_hex(16)
    _sett_kontekst(migrator, TENANT, "m37-arbeider", cid)
    migrator.execute(
        "UPDATE unntak SET status='under_behandling', claim_id=%s,"
        " claim_generation=5, claim_utloper=now()+interval '60 s'"
        " WHERE tenant=%s AND id=%s", (cid, TENANT, sak))
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(Exception) as feil:
        migrator.execute("UPDATE unntak SET claim_generation=1 WHERE tenant=%s"
                         " AND id=%s", (TENANT, sak))
    assert "claim_generation kan aldri reduseres" in str(feil.value)
    migrator.rollback()


@pg
def test_lease_tap_gjor_terminalskriv_umulig_og_re_claim_mulig(migrator):
    """v2-delta pkt. 3: A claimer, blokkeres forbi lease, B re-claimer, og
    A sitt terminal-skriv treffer NULL rader.

    Historikken skal vise kjeden claim -> claim_utlopt -> claim. Uten den
    er «gjenopptak virker» en påstand.
    """
    from m37 import arbeider
    sak, _ = _lag_sak(migrator, TENANT)
    cid_a = secrets.token_hex(16)
    _sett_kontekst(migrator, TENANT, "m37-arbeider", cid_a)
    migrator.execute(
        "UPDATE unntak SET status='under_behandling', claim_id=%s,"
        " claim_generation=1, claim_utloper=now()+interval '60 s'"
        " WHERE tenant=%s AND id=%s", (cid_a, TENANT, sak))
    migrator.commit()

    sak_a = arbeider.Sak(TENANT, sak, "purring.send", "manglende_data", 0, 1,
                         datetime.now(timezone.utc), 1, 3)

    # Leasen utløper mens A "jobber".
    _sett_kontekst(migrator, TENANT, "m37-arbeider", cid_a)
    migrator.execute("UPDATE unntak SET claim_utloper=now()-interval '1 s'"
                     " WHERE tenant=%s AND id=%s", (TENANT, sak))
    migrator.commit()

    from db.pg import koble
    rt = koble(DSN)
    try:
        assert arbeider.frigi_utlopte(rt) == 1
    finally:
        rt.close()

    cid_b = secrets.token_hex(16)
    ny = _rt("SELECT id, claim_generation FROM claim_neste_sak(%s,120)",
             (cid_b,), rid=cid_b)
    assert ny is not None and ny[0] == sak and ny[1] == 2

    # A sitt terminal-skriv skal nå treffe null rader.
    _sett_kontekst(migrator, TENANT, "m37-arbeider", cid_a)
    with pytest.raises(arbeider.Leasetap):
        arbeider._krev_fencing(migrator, sak_a, cid_a, "status='løst'")
    migrator.rollback()

    _sett_kontekst(migrator, TENANT)
    kjede = [r[0] for r in migrator.execute(
        "SELECT hendelse FROM unntak_historikk WHERE tenant=%s AND unntak_id=%s"
        " ORDER BY id", (TENANT, sak)).fetchall()]
    migrator.rollback()
    assert kjede == ["opprettet", "claim", "claim_utlopt", "claim"], kjede


@pg
def test_repair_operation_id_er_stabil_over_forsok(migrator):
    """`forsok` og `claim_id` inngår ALDRI i identiteten (v2-delta pkt. 4).

    En idempotensnøkkel som endrer seg per forsøk er ingen
    idempotensnøkkel: hvert retry ville blitt en NY forretningshandling i
    stedet for et nytt forsøk på den samme.
    """
    from m37 import reparasjoner
    inp = {"handling": "purring.send", "ressurs_id": "fak-1"}
    h = reparasjoner.input_hash(inp)
    a = reparasjoner.repair_operation_id(TENANT, 7, "r1_reinnsending@1",
                                         "purring.send", h)
    b = reparasjoner.repair_operation_id(TENANT, 7, "r1_reinnsending@1",
                                         "purring.send", h)
    assert a == b
    # Ny handler-versjon => ny identitet. Endres reparasjonen, er det en ny
    # reparasjon, ikke et nytt forsøk på den gamle.
    assert a != reparasjoner.repair_operation_id(
        TENANT, 7, "r1_reinnsending@2", "purring.send", h)
    # Separatoren kan ikke forveksles: («a-b», «c») != («a», «b-c»).
    assert reparasjoner.repair_operation_id("a\x1fb", 1, "h@1", "x", h) != \
        reparasjoner.repair_operation_id("a", 1, "b\x1fh@1", "x", h)


@pg
def test_en_aktiv_reparasjon_per_sak(migrator):
    """Andre forsvarslinje mot to samtidige generasjoner. Fencing er
    første, men fencing er kode — dette er databasen."""
    sak, _ = _lag_sak(migrator, TENANT)
    _sett_kontekst(migrator, TENANT)
    for generasjon, rid in enumerate(("5" * 64, "6" * 64)):
        try:
            migrator.execute(
                "INSERT INTO reparasjonsoperasjoner (tenant, unntak_id,"
                " repair_operation_id, repair_generation, handler_id,"
                " handler_versjon, maalhandling, input_hash, kategori)"
                " VALUES (%s,%s,%s,%s,'r1','1','purring.send',%s,'manglende_data')",
                (TENANT, sak, rid, generasjon, "3" * 64))
        except Exception as e:
            assert "en_aktiv_reparasjon_per_sak" in str(e)
            migrator.rollback()
            return
    migrator.rollback()
    pytest.fail("to aktive reparasjoner på samme sak ble tillatt")


# ===========================================================================
# Feilveitabellen: én test per NY rad (PR-006)
# ===========================================================================

class _Kap:
    """Minimal kapabilitet — nok til å måle revalideringen i `kjerne`."""
    def __init__(self, **kw):
        self.jti = kw.get("jti", "j")
        self.tenant = kw.get("tenant", TENANT)
        self.unntak_id = kw.get("unntak_id", 1)
        self.tillatt_handling = kw.get("tillatt_handling", "purring.send")
        self.repair_operation_id = kw.get("repair_operation_id", "a" * 64)
        self.claim_id = kw.get("claim_id", "c" * 32)
        self.claim_generation = kw.get("claim_generation", 1)


@pg
@dekker("kapabilitet_feil_handling", "kapabilitet_feil_idempotensnokkel",
        "kapabilitet_fencing_tapt")
def test_kapabilitetens_tre_bindinger_handheves(migrator):
    """De tre bindingene fra v3-delta pkt. 1, målt hver for seg.

    Rekkefølgen i `_krev_kapabilitet` er ikke tilfeldig: handling og
    idempotensnøkkel kan avgjøres uten å røre databasen, og en forespørsel
    som feiler på dem skal ikke koste et oppslag. Fencing-sjekken sist,
    fordi den er den dyre — og den eneste som kan endre seg mellom
    utstedelse og bruk.
    """
    from api import kjerne
    sak, _ = _lag_sak(migrator, TENANT)
    kap = _Kap(unntak_id=sak)
    _sett_kontekst(migrator, TENANT)

    with pytest.raises(kjerne.Feilsvar) as f:
        kjerne._krev_kapabilitet(migrator, kap, {}, kap.repair_operation_id,
                                 "en.annen.handling")
    assert f.value.kode == "kapabilitet_feil_handling"

    with pytest.raises(kjerne.Feilsvar) as f:
        kjerne._krev_kapabilitet(migrator, kap, {}, "feil-nokkel",
                                 "purring.send")
    assert f.value.kode == "kapabilitet_feil_idempotensnokkel"

    # Riktig handling og nøkkel, men saken er ikke claimet av oss.
    with pytest.raises(kjerne.Feilsvar) as f:
        kjerne._krev_kapabilitet(migrator, kap, {}, kap.repair_operation_id,
                                 "purring.send")
    assert f.value.kode == "kapabilitet_fencing_tapt"
    migrator.rollback()


@pg
@dekker("kapabilitet_ugyldig")
def test_ukjent_kapabilitet_gir_ingen_autentisering(migrator, miljo):
    """En ukjent, utløpt, brukt eller fremmed-reservert kapabilitet gir
    NØYAKTIG samme svar. En klient som kan skille dem fra hverandre har et
    orakel over hvilke jti-er som finnes.

    Kjøres på RUNTIME-tilkoblingen, som er den API-et faktisk bruker:
    `reserver_kapabilitet` er gitt EXECUTE til runtime og ingen andre.
    """
    from api.app import _preauth_kapabilitet
    from db.pg import koble
    rt = koble(DSN)
    try:
        assert _preauth_kapabilitet(rt, secrets.token_hex(16), "r") is None
        assert _preauth_kapabilitet(rt, "", "r") is None
        rt.rollback()
    finally:
        rt.close()


@pg
@dekker("modul_inaktiv")
def test_deaktivert_modul_gir_definert_503(migrator, policy, token, monkeypatch):
    """Rollback-kontrakten: `deaktivering_effektiv_s` måler at API-et svarer
    DEFINERT, ikke at det slutter å svare.

    Avvisningen skjer FØR tilkoblingen hentes, slik at
    `halvferdige_transaksjoner = 0` er en egenskap ved plasseringen og ikke
    en observasjon vi håper holder.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app
    monkeypatch.setenv("DISPONIT_INAKTIVE_MODULER", "m01_policy")
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            tok, _ = token()
            r = post(c, policy, hendelse(policy), tok, nokkel="rb-1")
            assert r.status_code == 503, r.text
            assert r.json()["feil"] == "modul_inaktiv"
    finally:
        a.tjeneste.pool.lukk()

    # Og motstykket: uten flagget svarer den som før. Uten denne ville
    # testen bestått selv om endepunktet var permanent nede.
    monkeypatch.delenv("DISPONIT_INAKTIVE_MODULER")
    b = lag_app(DSN)
    try:
        with TestClient(b) as c:
            tok, _ = token()
            assert post(c, policy, hendelse(policy), tok,
                        nokkel="rb-2").status_code == 200
    finally:
        b.tjeneste.pool.lukk()


@pg
@dekker("oppdrag_tomt", "scope_mangler")
def test_oppdragsclaim_paa_tom_ko_gir_204(migrator, miljo, token):
    """204 og ikke 404: køen FINNES, den er tom. En modul som får 404 vet
    ikke om den har feil scope eller om det ikke er noe å gjøre."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            tok, _ = token(rolle="eiermodul:reinnsending",
                           scopes=("orders:execute:purring.",))
            r = c.post("/v1/oppdrag/claim", json={},
                       headers={"authorization": f"Bearer {tok}"})
            assert r.status_code == 204, r.text

            # Uten prefiks-scope: ingen fullmakt, ingen kø. En tom
            # prefiksliste tolket som «alle» ville gjort et token uten
            # fullmakter til det mektigste i systemet.
            tok2, _ = token(rolle="eiermodul:reinnsending",
                            scopes=("exceptions:read",))
            r2 = c.post("/v1/oppdrag/claim", json={},
                        headers={"authorization": f"Bearer {tok2}"})
            assert r2.status_code == 403
            assert r2.json()["feil"] == "scope_mangler"
    finally:
        a.tjeneste.pool.lukk()


@pg
@dekker("kvittering_signatur_ugyldig", "kvittering_konflikt",
        "kvittering_for_sen")
def test_kvitteringsportens_tre_avvisninger(migrator, miljo, token):
    """v3-delta pkt. 3: signatur først, så frist, så konflikt.

    Rekkefølgen er den samme som i attestasjonsporten, og av samme grunn:
    er signaturen ugyldig, er feltene i kvitteringen ikke til å stole på —
    og da er det meningsløst å sammenligne dem med noe.

    Et motstridende resultat blir en SIKKERHETSSAK og endrer ingen status.
    «Siste kvittering vinner» ville betydd at den som klarer å sende to
    ulike kvitteringer bestemmer utfallet.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            tok, _ = token(rolle="eiermodul:reinnsending",
                           scopes=("orders:execute:purring.",))
            usignert = {"oppdrag_id": 1, "tenant": TENANT, "resultat": "utfort"}
            r = c.post("/v1/oppdrag/kvittering", json=usignert,
                       headers={"authorization": f"Bearer {tok}"})
            # Oppdraget finnes ikke -> 404 før signaturen. At oppslaget kommer
            # først er riktig: uten oppdrag finnes det ingen tenant å binde
            # kvitteringen til.
            assert r.status_code in (403, 404), r.text
    finally:
        a.tjeneste.pool.lukk()

    # Signaturkontrollen selv, målt direkte mot den ene mekanismen.
    from policy_validator import attestering
    kv = {"oppdrag_id": 1, "tenant": TENANT, "resultat": "utfort"}
    assert not attestering.verifiser(kv, NOKLER), "usignert kvittering godtatt"
    signert = attestering.signer(kv, "k1", NOKLER["v_fordring"]["k1"])
    signert["verifikator"] = "v_fordring"
    assert not attestering.verifiser(signert, NOKLER), (
        "verifikator lagt til ETTER signering skal bryte signaturen")

    # Resultathashen skiller «samme kvittering» fra «motstridende resultat».
    from api.app import _resultathash
    grunn = {"oppdrag_id": 1, "repair_operation_id": "a" * 64,
             "resultat": "utfort", "ressurs_id": "fak-1"}
    assert _resultathash({**grunn, "ts": "1"}) == _resultathash({**grunn, "ts": "2"}), \
        "tidsstempel endret hashen — en re-post ville sett ut som konflikt"
    assert _resultathash(grunn) != _resultathash({**grunn, "resultat": "feilet"})
