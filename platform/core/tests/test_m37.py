"""PR-006: M-37 behandlingsmotor — de ti Codex-portene og de tre vilkårene.

Hver port har en test som DØR når vakten sin fjernes. Det er kravet fra
klarsignalet, og det er også den eneste måten «porten finnes» kan skilles
fra «porten virker». Fem runder på PR #8 handlet om nøyaktig den
forskjellen: porten fantes, men dekket ikke alt den ga inntrykk av.

Testene som ikke trenger database står først, slik at en kjøring uten
`DISPONIT_TEST_DSN` fortsatt sier noe. De er merket i navnet.
"""
import ast
import time
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from .conftest import CORE, POLICIES
from .test_api import (ANNEN_TENANT, DSN, MIGRATOR_DSN, NOKLER, TENANT, _lag_token, _rydd,
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


def test_oppdragstypenes_prefikser_er_entydige():
    """Ingen handling får to typer å velge mellom.

    Prefiksene var disjunkte før PR-014c. Med `kontroll.wcag.` under
    `kontroll.` er de nested, og da er det LENGSTE treffet som avgjør —
    entydig uansett rekkefølgen i dict-en. Det som fortsatt ville gjort
    feltbredden til et lotteri, er to ULIKE typer med NØYAKTIG samme
    prefiks: da finnes det ikke noe lengste treff.
    """
    import oppdragskontrakt
    eier: dict[str, str] = {}
    for t in oppdragskontrakt.OPPDRAGSTYPER.values():
        for p in t.handlingsprefikser:
            assert p not in eier or eier[p] == t.navn, \
                f"prefikset {p!r} deles av {eier[p]} og {t.navn}"
            eier[p] = t.navn


def test_lengste_prefiks_vinner_over_dict_rekkefolgen():
    """WCAG-kontrollen eier `kontroll.wcag.`, `verifikasjon` resten.

    Mutasjonssjekk: med førstetreff i `type_for_handling` avhenger begge
    disse av hvilken vei dict-en itereres.
    """
    import oppdragskontrakt as ok
    assert ok.type_for_handling(
        "kontroll.wcag.nettsted").navn == "kontroll.wcag.nettsted"
    # Codex P1, runde 11: en persistert tenantpolicy kan bære en fri
    # `kontroll.*`-handling. Den skal fortsatt rutes som før — ikke bli
    # `eiermodul:ukjent` fordi WCAG-kontrollen tok navnerommet.
    assert ok.type_for_handling(
        "kontroll.fakturagrunnlag").navn == "verifikasjon"
    assert ok.type_for_handling("verifiser.mva").navn == "verifikasjon"
    assert ok.type_for_handling("kontroll") is None
    # Feltbredden følger den typen som VANT, ikke den som delte prefiks.
    wcag = ok.minimer("kontroll.wcag.nettsted",
                      {"mal_url": "https://a.example/", "kravsett": "wcag22aa",
                       "omfang": "forside", "vilkaar_sett": ["x"]})
    assert "vilkaar_sett" not in wcag and wcag["kravsett"] == "wcag22aa"


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


def _nost_objekt(n, blad=1):
    v = blad
    for _ in range(n):
        v = {"a": v}
    return v


def _nost_liste(n, blad=1):
    v = blad
    for _ in range(n):
        v = [v]
    return v


def test_jcs_avviser_dyp_nosting_som_valideringsfeil_ikke_stackoverflyt():
    """PR-014b P2: serialisereren er rekursiv, så uten en egen dybdegrense var
    bunnen `sys.setrecursionlimit` — og den treffes som RecursionError, en
    RuntimeError ingen kaller fanget. Et syntaktisk gyldig, dypt nøstet
    dokument på noen få kilobyte ble derfor 500 i stedet for det dokumenterte
    `request_feilformet`, og feilet dessuten ULIKT avhengig av hvor dypt i
    stacken kalleren sto. Nå er avvisningen en egenskap ved formatet."""
    from policy_validator import jcs

    # Rett under grensen kanoniseres fortsatt — grensen står ikke i veien for
    # noe ekte dokument (de dypeste i repoet er ensifret).
    n = jcs.MAKS_DYBDE - 1
    assert jcs.kanoniser(_nost_objekt(n)) == '{"a":' * n + "1" + "}" * n
    # Over grensen: en VALIDERINGSFEIL, ikke en RecursionError — for BEGGE
    # containertypene, og også for dybder der stacken ellers hadde rent over.
    for bygg in (_nost_objekt, _nost_liste):
        for dyp in (jcs.MAKS_DYBDE + 1, 2000):
            with pytest.raises(jcs.Ikkekanoniserbar):
                jcs.kanoniser(bygg(dyp))
    # Gjelder også via bytes-inngangen — den som faktisk signeres/hashes.
    with pytest.raises(jcs.Ikkekanoniserbar):
        jcs.kanoniske_bytes(_nost_objekt(2000))


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


def _attester_avvis(conn, tenant, sak, aktor, *, runde=1):
    """Det attesterte nei-et 043 §7 krever (Codex P1, runde 8).

    `behandle_unntakshandling` skriver denne append-only raden
    (`_skriv_attestasjon`) rett FØR den kaller `avvis_med_opplosning`, i
    SAMME transaksjon, og funksjonen krever den nå: EXECUTE alene er ikke
    kanselleringsautoritet. Testene som konstruerer nei-et direkte — for å
    eie kappløpets timing — må derfor legge igjen det samme beviset.
    Kalles FØR `SET ROLE disponit_m37_claimer`: claimeren har kun SELECT på
    tabellen og skal aldri kunne skrive sitt eget mandat."""
    conn.execute(
        "INSERT INTO menneskelig_attestasjon (tenant, unntak_id, runde,"
        " operatorhandling, bruker_id, rolle, authz_version,"
        " konvoluttversjon, konvolutt_hash, mac, mac_key_id, jti, utloper,"
        " saksversjon) VALUES (%s,%s,%s,'avvis',%s,'operator',1,2,%s,%s,"
        "'k-test',%s,now()+interval '1 hour',0)",
        (tenant, sak, runde, aktor, secrets.token_hex(32),
         secrets.token_hex(32), secrets.token_hex(16)))


#: Policy-id-en fixturene later som saken ble besluttet under.
FIXTURE_POLICY_ID = "tjenestebedrift-no"


def _policyref(policy_id=FIXTURE_POLICY_ID, versjon="1.0.0",
               handling="purring.send"):
    """NØYAKTIG det motoren skriver i `revisjonslogg.policy_id`.

    Fixturene skrev tidligere en bar `'p'` her. Den forskjellen mot
    produksjonsformen `<policy_id>@<versjon>/<handling>` er hele grunnen
    til at tre kallsteder kunne lese kolonnen som en policy-id og bestå 341
    tester — mens arbeideren i virkeligheten klassifiserte HVER sak som
    `manuell`. En fixture uten produksjonens FORM beviser transport, ikke
    produksjon.
    """
    from policy_validator.engine import _pid
    return _pid({"meta": {"policy_id": policy_id, "versjon": versjon}},
                handling)


def _lag_sak(conn, tenant, *, kategori="manglende_data", handling="purring.send",
             snapshot=3, hash_="1" * 64, versjon="1.0.0", sakstype="normal",
             policy_id=FIXTURE_POLICY_ID,
             grunnkode="attestasjon_mangler",
             vilkaar="forfall_passert_dager", status="ny"):
    """En unntaksrad med policysnapshot, som API-veien ville laget den."""
    _sett_kontekst(conn, tenant)
    logg = conn.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash, policy_id,"
        " beslutning, begrunnelse, policy_content_hash)"
        " VALUES (%s,'test','test','ih',%s,'UNNTAK','[]',%s) RETURNING id",
        (tenant, _policyref(policy_id, versjon, handling),
         hash_)).fetchone()[0]
    from db import kryptering
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
    # Standardsaken er en ATTESTASJONSmangel — den klassen R1 faktisk kan
    # reparere etter PR-007. `manglende_felt` er en VERDImangel og går til
    # `manuell`; en fixture som brukte den ville målt den negative veien
    # og kalt det hovedveien.
    ct, nonce = kryptering.krypter(
        dek, {"handling": handling, "ressurs_id": "fak-1",
              "kategori": kategori, "begrunnelse": [grunnkode],
              **({"manglende_vilkaar": vilkaar} if vilkaar else {})},
        tenant, key_id)
    # `status` settes ved INSERT og ikke med en etterfølgende UPDATE: statusen
    # er lovlig per CHECK-en i 011, men overgangsvakten `unntak_laas` er en
    # BEFORE UPDATE-trigger, så en test som vil FØDE en sak midt i
    # godkjenningsflyten må gjøre det her — ikke ved å hoppe dit etterpå.
    sak = conn.execute(
        "INSERT INTO unntak (tenant, loggpost_id, handling, kategori, sakstype,"
        " payload_kryptert, key_id, nonce, maks_auto_forsok_snapshot,"
        # 041: sakskilde er defaultløs (port 12) — fixturen er en kjernesak.
        " policy_versjon, policy_content_hash, status, sakskilde)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'policybrudd')"
        " RETURNING id",
        (tenant, logg, handling, kategori, sakstype, ct, key_id, nonce,
         snapshot, versjon, hash_, status)).fetchone()[0]
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
    # ikke bare vår, og `arbeidskapabiliteter` ryddes ikke mellom tester
    # (tabellen eies av NOLOGIN-rollen, og migrator har ikke DELETE der).
    # Derfor sier vi ingenting om TALLET, bare om DENNE jti-en. En
    # assertion som varierer med hva andre tester la igjen, er ingen
    # assertion — og jeg gjorde nøyaktig den feilen i kappløpstesten
    # tidligere i samme PR.
    _rt("SELECT frigi_hengende_kapabiliteter()")
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

    _rt("SELECT frigi_hengende_kapabiliteter()")
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    assert migrator.execute(
        "SELECT status FROM arbeidskapabiliteter WHERE jti=%s",
        (jti2,)).fetchone()[0] == "reservert", (
        "en reservasjon med auditert beslutning ble frigjort — den treige"
        " men vellykkede transaksjonen ville mistet fullmakten sin")
    migrator.execute("RESET ROLE")
    migrator.rollback()


@pg
def test_kappløp_tjue_arbeidere_gir_noyaktig_en_claim_per_sak(migrator):
    """v1 §7: 20 samtidige arbeidere, hver sak claimes NØYAKTIG én gang.

    `FOR UPDATE SKIP LOCKED` er hele mekanismen. Fjernes SKIP LOCKED, blir
    dette en kø som serialiserer; fjernes FOR UPDATE, kan to arbeidere ta
    samme sak. Historikktellingen er beviset — den kan ikke lyve, fordi
    triggeren skriver én rad per faktisk statusskifte.
    """
    import threading
    # FIRE saker per tenant, ikke ti på én. Anti-dominansregelen i
    # `claim_neste_sak` hopper over tenanter med >= 5 saker allerede under
    # behandling, og ingenting i denne testen avslutter en sak — så ti
    # saker hos ÉN tenant kan per konstruksjon ikke alle claimes.
    #
    # Den forrige utgaven gjorde nettopp det og var grønn LOKALT fordi
    # kappløpet mot telle-subspørringen gikk vår vei. I CI gikk det andre
    # veien og ga 9 av 10. Testen bestod altså av feil grunn, og det er den
    # samme fellen som trådtesten i PR-002 og `ved_brudd`-testen i PR #8.
    # Fairness-regelen har sin EGEN test rett under.
    saker = ([_lag_sak(migrator, TENANT)[0] for _ in range(4)]
             + [_lag_sak(migrator, ANNEN_TENANT)[0] for _ in range(4)])
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

    for t in (TENANT, ANNEN_TENANT):
        _sett_kontekst(migrator, t)
        claims = migrator.execute(
            "SELECT unntak_id, count(*) FROM unntak_historikk"
            " WHERE tenant=%s AND hendelse='claim' GROUP BY unntak_id",
            (t,)).fetchall()
        migrator.rollback()
        assert all(n == 1 for _, n in claims), \
            f"dobbel claim i historikken for {t}: {claims}"


@pg
def test_anti_dominans_stopper_en_tenant_paa_fem_samtidige(migrator):
    """v3-delta pkt. 6: en tenant med fem saker under behandling får ikke
    ta den sjette før noe frigjøres.

    Regelen hadde INGEN test før nå — og det var den som gjorde
    kappløpstesten over grønn av feil grunn. En regel ingen måler, er en
    regel som styrer utfallet av andre tester i stillhet.

    Mutasjonen som dreper denne: fjern telle-subspørringen fra
    `claim_neste_sak`. Da claimes alle åtte.
    """
    for _ in range(8):
        _lag_sak(migrator, TENANT)

    claimet = []
    for _ in range(8):
        cid = secrets.token_hex(16)
        rad = _rt("SELECT id FROM claim_neste_sak(%s,120)", (cid,), rid=cid)
        if rad is None:
            break
        claimet.append(rad[0])

    assert len(claimet) == 5, (
        f"claimet {len(claimet)} saker for én tenant, taket er 5: {claimet}")

    _sett_kontekst(migrator, TENANT)
    aktive = migrator.execute(
        "SELECT count(*) FROM unntak WHERE tenant=%s"
        "   AND status='under_behandling' AND claim_utloper > now()",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert aktive == 5

    # Og motstykket: frigjøres én, slipper den neste inn. Uten dette ville
    # testen bestått selv om claim-veien var permanent stengt etter fem.
    _sett_kontekst(migrator, TENANT, "m37-arbeider", "opprydd")
    migrator.execute("UPDATE unntak SET status='manuell' WHERE tenant=%s"
                     " AND id=%s", (TENANT, claimet[0]))
    migrator.commit()
    cid = secrets.token_hex(16)
    assert _rt("SELECT id FROM claim_neste_sak(%s,120)", (cid,), rid=cid) \
        is not None, "ingen slapp inn etter at en sak ble avsluttet"


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
    # 041: unntak har utsatte constraint-triggere (lineage/loggpost) — de må
    # fyre FØR en ALTER TABLE i samme transaksjon («pending trigger events»),
    # samme håndgrep som _rydd bruker.
    migrator.execute("SET CONSTRAINTS ALL IMMEDIATE")
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
    være virkningsløs.

    PR-013: `policyer` er nå OGSÅ FK-referert av `policy_hode.aktiv_versjon`,
    så en naken `TRUNCATE policyer` blokkeres av FK-en (første forsvarslinje).
    Retention-statement-triggeren nås via `CASCADE` og består som andre
    forsvarslinje — begge veier er dekket her."""
    with pytest.raises(Exception) as feil:
        migrator.execute("TRUNCATE policyer")
    assert ("referenced in a foreign key" in str(feil.value)
            or "TRUNCATE er forbudt" in str(feil.value))
    migrator.rollback()
    with pytest.raises(Exception) as feil2:
        migrator.execute("TRUNCATE policyer CASCADE")
    assert "TRUNCATE er forbudt" in str(feil2.value)
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
    # 041: totalitets-CHECKen krever trioen for policybrudd — pre-006-
    # tilstanden fixturen gjenskaper er nettopp trio=NULL, så sperren
    # løftes eksplisitt og settes tilbake nederst, som NOT NULL-ene.
    migrator.execute(
        "ALTER TABLE unntak DROP CONSTRAINT unntak_snapshot_komplett")
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
        "UPDATE revisjonslogg SET policy_id=%s WHERE tenant=%s AND id="
        "(SELECT loggpost_id FROM unntak WHERE tenant=%s AND id=%s)",
        (_policyref("pbf", "2.0.0"), TENANT, TENANT, med_evidens))
    migrator.execute("ALTER TABLE revisjonslogg ENABLE TRIGGER"
                     " revisjonslogg_ingen_endring")
    # 041: unntak har utsatte constraint-triggere (lineage/loggpost) — de må
    # fyre FØR en ALTER TABLE i samme transaksjon («pending trigger events»),
    # samme håndgrep som _rydd bruker.
    migrator.execute("SET CONSTRAINTS ALL IMMEDIATE")
    migrator.execute("ALTER TABLE unntak ENABLE TRIGGER unntak_laas")
    migrator.commit()

    res = m37_backfill.backfill(migrator)
    assert res.fra_evidens >= 1 and res.legacy >= 1, res

    # 041 gjorde trioen nullable (overtakelsessaker HAR NULL-trio) — å sette
    # NOT NULL tilbake ville gjeninnført pre-041-skjemaet og felt enhver
    # senere overtakelsestest. Beviset for at backfillen fylte radene bæres
    # nå av CHECK-en alene: ADD CONSTRAINT validerer HELE tabellen, så en
    # policybrudd-rad backfillen hoppet over ville felt nettopp denne linjen.
    migrator.execute(
        "ALTER TABLE unntak ADD CONSTRAINT unntak_snapshot_komplett CHECK ("
        " (sakskilde = 'domeneovertakelse'"
        "    AND maks_auto_forsok_snapshot IS NULL"
        "    AND policy_versjon IS NULL AND policy_content_hash IS NULL)"
        " OR (sakskilde <> 'domeneovertakelse'"
        "    AND maks_auto_forsok_snapshot IS NOT NULL"
        "    AND policy_versjon IS NOT NULL"
        "    AND policy_content_hash IS NOT NULL))")
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
            # UTEN kvitteringskapabilitet slipper man ikke inn i det hele
            # tatt. Det er den nye kontrakten etter Codex' P1: et langlivet
            # modultoken er ikke lenger adgangsbilletten alene.
            uten_kap = {"oppdrag_id": 1, "tenant": TENANT,
                        "resultat": "utfort"}
            r = c.post("/v1/oppdrag/kvittering", json=uten_kap,
                       headers={"authorization": f"Bearer {tok}"})
            assert r.status_code == 400, r.text
            assert r.json()["feil"] == "request_feilformet"

            # Med en UKJENT kapabilitet: avvist, og med samme svar som en
            # utløpt eller brukt. En klient som kan skille dem fra hverandre
            # har et orakel over hvilke jti-er som finnes.
            ukjent = {"oppdrag_id": 1, "tenant": TENANT, "resultat": "utfort",
                      "kvittering_jti": secrets.token_hex(16)}
            r2 = c.post("/v1/oppdrag/kvittering", json=ukjent,
                        headers={"authorization": f"Bearer {tok}"})
            assert r2.status_code == 401, r2.text
            assert r2.json()["feil"] == "kapabilitet_ugyldig"
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


# ===========================================================================
# Codex runde 1 — de tre P1-ene
# ===========================================================================

def _unik_eiermodul() -> str:
    """Egen kø per test.

    `claim_neste_oppdrag` filtrerer på EIERMODUL, ikke på tenant. Deler to
    tester modulnavn, deler de kø — og en test som antar at den er alene om
    å plukke, måler kjørerekkefølgen. Samme lærdom som den globale
    saks-køen ga tidligere i denne PR-en, i en annen dimensjon.
    """
    return "eiermodul:test-" + secrets.token_hex(4)


def _lag_oppdrag(conn, tenant, sak_id, loggpost_id, *, rid=None,
                 handling="purring.send", eiermodul="eiermodul:reinnsending",
                 utforelsesfrist="1 hour", evidensfrist="30 days"):
    """Et oppdrag slik arbeideren ville lagt det ut.

    PR-008: PRODUKSJONSFORMEN har en fase-2-beslutningsloggpost bak seg
    (`kilde='arbeidskapabilitet'`, TILLAT, `idempotency_key = rid`), og
    oppdraget bærer FK-en til den (`koblingsstatus='KOBLET'`). En fixture
    uten den ville testet en rad koblingsvakten ikke lenger tillater — og
    dermed en tilstand som ikke finnes noe sted i virkeligheten.
    """
    from db import kryptering
    rid = rid or secrets.token_hex(32)
    _sett_kontekst(conn, tenant)
    conn.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant, unntak_id,"
        " repair_operation_id, repair_generation, handler_id, handler_versjon,"
        " maalhandling, input_hash, kategori)"
        " VALUES (%s,%s,%s,0,'r1_reinnsending','1',%s,%s,'manglende_data')"
        " ON CONFLICT DO NOTHING",
        (tenant, sak_id, rid, handling, secrets.token_hex(32)))
    beslutning_loggpost = conn.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'test','arbeidskapabilitet','ih2','p@1.0.0/x.y',"
        " 'TILLAT','[]',%s) RETURNING id", (tenant, rid)).fetchone()[0]
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
    ct, nonce = kryptering.krypter(
        dek, {"handling": handling, "ressurs_id": "fak-1"}, tenant, key_id)
    opp = conn.execute(
        "INSERT INTO oppdrag (opprinnelse, tenant, unntak_id, loggpost_id,"
        " repair_operation_id, oppdragstype, handling, eiermodul,"
        " payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist,"
        " beslutning_loggpost_id, koblingsstatus)"
        " VALUES ('m37_reparasjon',%s,%s,%s,%s,'reinnsending',%s,%s,%s,%s,%s,"
        f" now()+interval '{utforelsesfrist}', now()+interval '{evidensfrist}',"
        " %s,'KOBLET') RETURNING id",
        (tenant, sak_id, loggpost_id, rid, handling, eiermodul, ct, key_id,
         nonce, beslutning_loggpost)).fetchone()[0]
    conn.commit()
    return int(opp), rid


@pg
def test_P1_utlopt_eierlease_gjor_oppdraget_reclaimbart(migrator):
    """Codex P1 runde 1: et `plukket` oppdrag var PERMANENT uclaimbart.

    Ingenting førte det tilbake i køen når eier-leasen gikk ut, selv om
    statusmaskinen tillot overgangen. Et krasj i eiermodulen mellom
    claim-commit og kvittering parkerte saken for alltid, og
    owner-fencingen hadde ingen reell gjenopptaksvei — den kunne bare nekte
    den gamle eieren, aldri slippe til en ny.

    MUTASJONEN SOM DREPER DENNE: fjern `OR (k.status = 'plukket' AND
    k.owner_lease_utloper < now())` fra `claim_neste_oppdrag`. Da får B
    ingen oppdrag, og assertionen under faller.
    """
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)

    cid_a = secrets.token_hex(16)
    a = _rt("SELECT id, owner_generation FROM claim_neste_oppdrag("
            "%s,%s,%s,300)",
            ("eiermodul:reinnsending", ["purring."], cid_a))
    assert a is not None and a[0] == opp, "A fikk ikke oppdraget"
    assert a[1] == 1, f"owner_generation skulle vært 1, var {a[1]}"

    # Ingen andre får det mens leasen lever — ellers ville «reclaim»
    # egentlig vært «alle kan ta alt», og testen bevist noe annet.
    assert _rt("SELECT id FROM claim_neste_oppdrag(%s,%s,%s,300)",
               ("eiermodul:reinnsending", ["purring."],
                secrets.token_hex(16))) is None, \
        "et LEVENDE plukket oppdrag ble claimet av en annen"

    # Leasen tvinges utløpt — som om eiermodulen krasjet.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE oppdrag SET owner_lease_utloper=now()"
                     " - interval '1 s' WHERE tenant=%s AND id=%s",
                     (TENANT, opp))
    migrator.commit()

    cid_b = secrets.token_hex(16)
    b = _rt("SELECT id, owner_generation FROM claim_neste_oppdrag("
            "%s,%s,%s,300)",
            ("eiermodul:reinnsending", ["purring."], cid_b))
    assert b is not None and b[0] == opp, "utløpt lease ga ikke reclaim"
    assert b[1] == 2, f"generasjonen ble ikke økt ved reclaim: {b[1]}"


@pg
def test_P1_annen_eiermodul_kan_aldri_claime_oppdraget(migrator):
    """Oppdraget er BUNDET til én eiermodul ved opprettelsen.

    Mutasjonen som dreper denne: fjern `AND k.eiermodul = p_modul_id`.
    """
    sak, logg = _lag_sak(migrator, TENANT)
    _lag_oppdrag(migrator, TENANT, sak, logg)
    assert _rt("SELECT id FROM claim_neste_oppdrag(%s,%s,%s,300)",
               ("eiermodul:en-annen", ["purring."],
                secrets.token_hex(16))) is None
    # Og tom prefiksliste treffer ingenting — fail-closed, ikke «alle».
    assert _rt("SELECT id FROM claim_neste_oppdrag(%s,%s,%s,300)",
               ("eiermodul:reinnsending", [], secrets.token_hex(16))) is None


@pg
def test_P1_kvitteringskapabilitet_bindes_og_innloses(migrator):
    """Codex P1 runde 1: kapabiliteten var ikke implementert, og
    modultokenet var alene adgangsbilletten til kvitteringsporten.

    Måler de fem bindingene v3-delta pkt. 2 krever: oppdrag, tenant, modul,
    owner-claim/generation og frist. Hver av dem avvises UTEN statusendring.

    Mutasjonen som dreper denne: fjern `AND k.modul_id = p_modul_id` fra
    `innlos_kvitteringskapabilitet` — da kan en hvilken som helst modul
    innløse en kapabilitet den har fått tak i.
    """
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    cid = secrets.token_hex(16)
    a = _rt("SELECT id, owner_generation FROM claim_neste_oppdrag(%s,%s,%s,300)",
            ("eiermodul:reinnsending", ["purring."], cid))
    assert a is not None

    jti = secrets.token_hex(16)
    kap = _rt("SELECT jti, utloper FROM utsted_kvitteringskapabilitet("
              "%s,%s,%s,%s)", (opp, cid, a[1], jti))
    assert kap is not None, "kapabiliteten ble ikke utstedt"

    # Riktig modul: innløses.
    assert _rt("SELECT tenant, oppdrag_id FROM innlos_kvitteringskapabilitet("
               "%s,%s)", (jti, "eiermodul:reinnsending")) is not None

    # FREMMED MODUL: avvises. Dette er den bindingen som gjør at et
    # langlivet modultoken ikke lenger er nok alene.
    assert _rt("SELECT tenant FROM innlos_kvitteringskapabilitet(%s,%s)",
               (jti, "eiermodul:en-annen")) is None

    # UKJENT jti: avvises.
    assert _rt("SELECT tenant FROM innlos_kvitteringskapabilitet(%s,%s)",
               (secrets.token_hex(16), "eiermodul:reinnsending")) is None

    # Utstedelse for en claim vi IKKE eier: ingen kapabilitet.
    assert _rt("SELECT jti FROM utsted_kvitteringskapabilitet(%s,%s,%s,%s)",
               (opp, secrets.token_hex(16), a[1],
                secrets.token_hex(16))) is None, \
        "kapabilitet utstedt for en fremmed owner-claim"

    # Utdatert generation: heller ikke.
    assert _rt("SELECT jti FROM utsted_kvitteringskapabilitet(%s,%s,%s,%s)",
               (opp, cid, a[1] - 1, secrets.token_hex(16))) is None

    # Forbruk er engangs — og etter runde 3 returnerer funksjonen UTFALLET,
    # ikke en boolean. Det er nettopp forskjellen taperen i et kappløp
    # trenger for å skille idempotens fra konflikt.
    h = "a" * 64
    assert _rt("SELECT bruk_kvitteringskapabilitet(%s,%s)",
               (jti, h))[0] == "brukt"
    assert _rt("SELECT bruk_kvitteringskapabilitet(%s,%s)",
               (jti, h))[0] == "idempotent"


@pg
def test_P1_kvitteringskapabilitetens_bindingsfelter_er_uforanderlige(migrator):
    """Kunne owner_generation endres på kapabiliteten, ville fencingen vært
    et forslag. Vakten gjør den til en egenskap."""
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    cid = secrets.token_hex(16)
    a = _rt("SELECT id, owner_generation FROM claim_neste_oppdrag(%s,%s,%s,300)",
            ("eiermodul:reinnsending", ["purring."], cid))
    jti = secrets.token_hex(16)
    _rt("SELECT jti FROM utsted_kvitteringskapabilitet(%s,%s,%s,%s)",
        (opp, cid, a[1], jti))

    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    with pytest.raises(Exception) as feil:
        migrator.execute("UPDATE kvitteringskapabiliteter SET"
                         " owner_generation=99 WHERE jti=%s", (jti,))
    assert "bindingsfelter er uforanderlige" in str(feil.value)
    migrator.rollback()


def test_P1_kompensasjon_positiv_vei_gaar_gjennom_policyporten():
    """Hovedspesifikasjon §3, positiv vei.

    Det avgjørende er RETURTYPEN: `planlegg_kompensasjon` gir en PLAN med
    `utfall='oppdrag'`. Den har ingen databasetilkobling og ingen klient, og
    kan derfor ikke utføre kompensasjonen selv. Arbeideren sender den
    gjennom `kjerne.behandle()` som en NY beslutning, og policyporten
    evaluerer den som enhver annen handling.

    MUTASJONEN SOM DREPER DENNE: la funksjonen utføre kompensasjonen selv,
    eller returnere `lost` i stedet for `oppdrag`. Begge betyr at M-37 har
    en fullmakt ingen har gitt den.
    """
    from datetime import timedelta
    from m37 import reparasjoner
    naa = datetime.now(timezone.utc)
    policy = {"handlinger": [
        {"id": "purring.send", "reversering": {
            "type": "kompenserende", "handling": "purring.krediter",
            "frist_sekunder": 3600}},
        {"id": "purring.krediter", "reversering": {"type": "direkte"}}]}
    payload = {"handling": "purring.send", "ressurs_id": "fak-1",
               "delvis_utfort": True}

    plan = reparasjoner.planlegg_kompensasjon(
        policy, opprinnelig_handling="purring.send", unntak_id=7,
        loggpost_id=3, sak_ts=naa, naa=naa, payload=payload)
    assert plan.utfall == "oppdrag", "kompensasjonen ble ikke sendt via API-veien"
    assert plan.maalhandling == "purring.krediter"
    # Idempotensnøkkelen har den kanoniske formen fra v2-delta pkt. 4, og
    # inneholder verken forsok eller claim_id.
    assert plan.reparasjonsinput["kompensasjonsnokkel"] == \
        "compensation:7:purring.krediter:3"

    # Og den er STABIL over forsøk — samme sak gir samme nøkkel.
    plan2 = reparasjoner.planlegg_kompensasjon(
        policy, opprinnelig_handling="purring.send", unntak_id=7,
        loggpost_id=3, sak_ts=naa, naa=naa + timedelta(seconds=30),
        payload=payload)
    assert plan2.reparasjonsinput == plan.reparasjonsinput


def test_P1_kompensasjonens_tre_negative_porter():
    """De tre portene fra §3, hver for seg.

    Irreversibel er den viktigste: en handling policyen har erklært
    irreversibel kompenseres ALDRI automatisk — ikke sjelden, ikke med
    ekstra kontroll, aldri.

    MUTASJONEN SOM DREPER DENNE: fjern `if type_ == "irreversibel"`-grenen.
    Da faller den gjennom til `!= "kompenserende"` og gir fortsatt
    `manuell` — men med FEIL grunn, og en senere endring som gjør
    `irreversibel` til en kompenserbar type ville passert. Derfor
    sammenlignes grunnkoden, ikke bare utfallet.
    """
    from datetime import timedelta
    from m37 import reparasjoner
    naa = datetime.now(timezone.utc)
    payload = {"handling": "purring.send", "ressurs_id": "fak-1",
               "delvis_utfort": True}

    def plan(policy, sak_ts=None):
        return reparasjoner.planlegg_kompensasjon(
            policy, opprinnelig_handling="purring.send", unntak_id=1,
            loggpost_id=1, sak_ts=sak_ts or naa, naa=naa, payload=payload)

    irreversibel = {"handlinger": [{"id": "purring.send",
                                    "reversering": {"type": "irreversibel"}}]}
    p = plan(irreversibel)
    assert p.utfall == "manuell" and p.grunn == "irreversibel_kompenseres_aldri"

    udefinert = {"handlinger": [{"id": "purring.send",
                                 "reversering": {"type": "kompenserende"}}]}
    assert plan(udefinert).grunn == "kompensasjonshandling_udefinert"

    ukjent = {"handlinger": [{"id": "purring.send", "reversering": {
        "type": "kompenserende", "handling": "finnes.ikke.i.policyen"}}]}
    assert plan(ukjent).grunn == "kompensasjonshandling_ukjent"

    med_frist = {"handlinger": [
        {"id": "purring.send", "reversering": {
            "type": "kompenserende", "handling": "purring.krediter",
            "frist_sekunder": 60}},
        {"id": "purring.krediter", "reversering": {"type": "direkte"}}]}
    assert plan(med_frist, sak_ts=naa - timedelta(hours=2)).grunn == \
        "kompensasjonsfrist_utlopt"
    # Innenfor fristen skal den fortsatt gå gjennom — ellers ville testen
    # bestått selv om kompensasjon var permanent avslått.
    assert plan(med_frist).utfall == "oppdrag"


def test_P1_kompensasjon_utloses_kun_av_eksplisitt_markor():
    """`delvis_utfort` er en MARKØR, ikke en utledning.

    Å gjette at «denne saken ser ut som den trenger reversering» ville
    betydd å starte forretningshandlinger på en mistanke.
    """
    from m37 import reparasjoner
    assert reparasjoner.krever_kompensasjon({"delvis_utfort": True}) is True
    for ikke in ({}, {"delvis_utfort": False}, {"delvis_utfort": "ja"},
                 {"delvis_utfort": 1}, {"handling": "purring.send"}):
        assert reparasjoner.krever_kompensasjon(ikke) is False, ikke


# ===========================================================================
# Codex runde 2 — sen kvittering forbruker kapabiliteten
# ===========================================================================

def _signer_kvittering(kropp: dict, verifikator="v_fordring", nokkel="k1"):
    from policy_validator import attestering
    return attestering.signer({**kropp, "verifikator": verifikator},
                              nokkel, NOKLER[verifikator][nokkel])


@pg
def test_P1_sen_kvittering_forbruker_kapabiliteten(migrator, miljo, token):
    """Codex P1 runde 2 — HELE kjeden, deterministisk.

    Den sene veien skrev evidensraden og lot kapabiliteten stå `utstedt`
    med `resultathash = NULL`. Da gjaldt reglene «identisk => idempotent»
    og «to ulike hasher => sikkerhetssak» bare den AVSLUTTENDE veien —
    altså nettopp ikke stale-generation/etter-frist-veien de er til for.

    Samme jti kunne dermed levere resultat etter resultat, og et
    motstridende resultat ble lagret som ordinær evidens.

    MUTASJONEN SOM DREPER DENNE: fjern
    `bruk_kvitteringskapabilitet(...)`-kallet i `not kan_avslutte`-grenen.
    Da blir R2 en ny `sen_kvittering` i stedet for en konflikt.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app

    sak, logg = _lag_sak(migrator, TENANT)
    opp, rid_rep = _lag_oppdrag(migrator, TENANT, sak, logg)
    # Saken settes i `venter_utførelse`, som er der en sak med utestående
    # oppdrag faktisk står.
    cid_sak = secrets.token_hex(16)
    _sett_kontekst(migrator, TENANT, "m37-arbeider", cid_sak)
    migrator.execute(
        "UPDATE unntak SET status='under_behandling', claim_id=%s,"
        " claim_generation=1, claim_utloper=now()+interval '600 s'"
        " WHERE tenant=%s AND id=%s", (cid_sak, TENANT, sak))
    migrator.execute("UPDATE unntak SET status='venter_utførelse'"
                     " WHERE tenant=%s AND id=%s", (TENANT, sak))
    migrator.commit()

    app = lag_app(DSN)
    try:
        with TestClient(app) as c:
            tok, _ = token(rolle="eiermodul:reinnsending",
                           scopes=("orders:execute:purring.",))
            h = {"authorization": f"Bearer {tok}"}

            # --- A claimer ---------------------------------------------
            ra = c.post("/v1/oppdrag/claim", json={}, headers=h)
            assert ra.status_code == 200, ra.text
            a = ra.json()
            assert a["oppdrag_id"] == opp and a["owner_generation"] == 1

            # --- A mister leasen, B reclaimer ---------------------------
            _sett_kontekst(migrator, TENANT)
            migrator.execute("UPDATE oppdrag SET owner_lease_utloper=now()"
                             " - interval '1 s' WHERE tenant=%s AND id=%s",
                             (TENANT, opp))
            migrator.commit()

            rb = c.post("/v1/oppdrag/claim", json={}, headers=h)
            assert rb.status_code == 200, rb.text
            b = rb.json()
            assert b["owner_generation"] == 2, "B fikk ikke ny generasjon"
            assert b["kvittering_jti"] != a["kvittering_jti"]

            def kvittering(kap, resultat):
                return _signer_kvittering({
                    "oppdrag_id": opp, "tenant": TENANT,
                    "kvittering_jti": kap["kvittering_jti"],
                    "repair_operation_id": kap["repair_operation_id"],
                    "owner_claim_id": kap["owner_claim_id"],
                    "owner_generation": kap["owner_generation"],
                    "resultat": resultat, "ressurs_id": "fak-1"})

            # --- A poster R1: sen evidens, INGEN terminalstatus ---------
            r1 = c.post("/v1/oppdrag/kvittering",
                        json=kvittering(a, "utfort"), headers=h)
            assert r1.status_code == 202, r1.text
            assert r1.json()["status"] == "lagret_uten_statusendring"

            # --- A poster R1 igjen: IDEMPOTENT, ingen ny evidensrad -----
            # ... og svaret må si HVILKEN idempotens (Codex P2, runde 11):
            # R1 over ble bevart som sen evidens, oppdraget står bevisst
            # ufullført, og et rent `idempotent` ville fortalt utføreren at
            # den kunne slutte å følge det.
            r1b = c.post("/v1/oppdrag/kvittering",
                         json=kvittering(a, "utfort"), headers=h)
            assert r1b.status_code == 200, r1b.text
            assert r1b.json()["status"] == "idempotent_uten_statusendring"

            # --- A poster R2 med SAMME kapabilitet: KONFLIKT ------------
            r2 = c.post("/v1/oppdrag/kvittering",
                        json=kvittering(a, "feilet"), headers=h)
            assert r2.status_code == 409, r2.text
            assert r2.json()["feil"] == "kvittering_konflikt"

            # --- B kan fortsatt avslutte med SIN kapabilitet ------------
            rb2 = c.post("/v1/oppdrag/kvittering",
                         json=kvittering(b, "utfort"), headers=h)
            assert rb2.status_code == 200, rb2.text
            assert rb2.json()["status"] == "utfort"

            # --- B poster SIN på nytt: idempotent MED statusskifte -----
            # Codex P2, runde 11: dette er den dokumenterte suksessveien —
            # en utfører som mistet svaret sender den samme kvitteringen
            # om igjen. Oppdraget ER `utfort`, og svaret må si det, ellers
            # melder utføreren `ukvittert` for noe som er ferdig. Legg
            # merke til at A sin re-post over — samme gren, samme kropp,
            # samme kapabilitetstreff — får det MOTSATTE ordet.
            rb3 = c.post("/v1/oppdrag/kvittering",
                         json=kvittering(b, "utfort"), headers=h)
            assert rb3.status_code == 200, rb3.text
            assert rb3.json()["status"] == "idempotent", rb3.text
    finally:
        app.tjeneste.pool.lukk()

    # --- Evidensen: NØYAKTIG én sen kvittering, én konflikt ------------
    _sett_kontekst(migrator, TENANT)
    hendelser = dict(migrator.execute(
        "SELECT hendelse, count(*) FROM unntak_historikk"
        " WHERE tenant=%s AND unntak_id=%s GROUP BY hendelse",
        (TENANT, sak)).fetchall())
    status = migrator.execute(
        "SELECT o.status, u.status FROM oppdrag o JOIN unntak u"
        "   ON u.tenant=o.tenant AND u.id=o.unntak_id"
        " WHERE o.tenant=%s AND o.id=%s", (TENANT, opp)).fetchone()
    migrator.rollback()

    assert hendelser.get("sen_kvittering") == 1, (
        f"forventet NØYAKTIG én sen evidensrad, fikk {hendelser}"
        " — en re-post eller en konflikt lagde en ordinær evidensrad")
    assert hendelser.get("motstridende_kvittering") == 1, (
        f"motstridende resultat ble ikke registrert som konflikt: {hendelser}")
    assert status == ("utfort", "løst"), (
        f"B fikk ikke avsluttet: {status}")


@pg
def test_P1_sen_kvittering_etter_menneskelig_avvis_naar_evidensgrenen(
        migrator, miljo, token):
    """043 (Gate 14b) §5 målt på INGEST-VEIEN, ikke bare på DB-porten.

    Etter en kansellering med fencing står kvitteringskapabiliteten `avvist`.
    Toargsformen svarer `ugyldig` på den — og siden modulens retry bærer
    SAMME jti, gjorde den det for evig: `_forbruk_kapabilitet` rullet
    tilbake med `kapabilitet_ugyldig` FØR sen-evidensgrenen ble nådd. En
    gyldig, signert sen kvittering kunne dermed aldri skrive
    `sen_kvittering` og aldri føde kompensasjonssaken §5 lover — fencingen
    gjorde systemet blindt for det som allerede hadde skjedd, i stedet for
    bare å hindre fullføring.

    MUTASJONEN SOM DREPER DENNE: fjern `sen=True` fra
    `_forbruk_kapabilitet`-kallet i `not kan_avslutte`-grenen. Da blir
    202-en en 401 `kapabilitet_ugyldig`, og kompensasjonssaken uteblir.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app

    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    cid_sak = secrets.token_hex(16)
    _sett_kontekst(migrator, TENANT, "m37-arbeider", cid_sak)
    migrator.execute(
        "UPDATE unntak SET status='under_behandling', claim_id=%s,"
        " claim_generation=1, claim_utloper=now()+interval '600 s'"
        " WHERE tenant=%s AND id=%s", (cid_sak, TENANT, sak))
    migrator.execute("UPDATE unntak SET status='venter_utførelse'"
                     " WHERE tenant=%s AND id=%s", (TENANT, sak))
    migrator.commit()

    app = lag_app(DSN)
    try:
        with TestClient(app) as c:
            tok, _ = token(rolle="eiermodul:reinnsending",
                           scopes=("orders:execute:purring.",))
            h = {"authorization": f"Bearer {tok}"}
            a = c.post("/v1/oppdrag/claim", json={}, headers=h).json()
            assert a["oppdrag_id"] == opp, a

            # Modulkontrakten oppdraget kjørte under: `kompenserende`. §5
            # utleder saken av KONTRAKTEN, aldri av gjetning.
            modul = "m-" + secrets.token_hex(4)
            kh = secrets.token_hex(16)
            _sett_kontekst(migrator, TENANT)
            migrator.execute("SET ROLE disponit_modul_eier")
            migrator.execute(
                "INSERT INTO modulkontrakt (modul_id, kontraktversjon,"
                " kontrakt_hash, payload_schema_hash, kvittering_schema_hash,"
                " sideeffektklasse, reversibilitet)"
                " VALUES (%s,1,%s,'p','k','ekstern_lesing','kompenserende')",
                (modul, kh))
            migrator.execute("RESET ROLE")
            migrator.execute("ALTER TABLE oppdrag DISABLE TRIGGER USER")
            migrator.execute(
                "UPDATE oppdrag SET modul_id=%s, kontraktversjon=1,"
                " kontrakt_hash=%s WHERE tenant=%s AND id=%s",
                (modul, kh, TENANT, opp))
            migrator.execute("ALTER TABLE oppdrag ENABLE TRIGGER USER")
            migrator.commit()

            # Mennesket sier nei — den EKTE oppløsningsveien: kapabiliteten
            # brennes `avvist`, claimet fences, oppdraget kanselleres.
            _sett_kontekst(migrator, TENANT, "menneske", "r-avvis")
            _attester_avvis(migrator, TENANT, sak, "menneske")
            migrator.execute("SET ROLE disponit_m37_claimer")
            res = migrator.execute(
                "SELECT utfall FROM avvis_med_opplosning(%s,%s,%s,"
                "'menneske','r-avvis')", (TENANT, sak, [opp])).fetchall()
            migrator.execute("RESET ROLE")
            migrator.commit()
            assert res == [("kansellert",)], res

            def kvittering(resultat):
                return _signer_kvittering({
                    "oppdrag_id": opp, "tenant": TENANT,
                    "kvittering_jti": a["kvittering_jti"],
                    "repair_operation_id": a["repair_operation_id"],
                    "owner_claim_id": a["owner_claim_id"],
                    "owner_generation": a["owner_generation"],
                    "resultat": resultat, "ressurs_id": "fak-1"})

            # --- Den sene kvitteringen: EVIDENS, aldri fullføring --------
            r1 = c.post("/v1/oppdrag/kvittering", json=kvittering("utfort"),
                        headers=h)
            assert r1.status_code == 202, r1.text
            assert r1.json()["status"] == "lagret_uten_statusendring"

            # --- Re-post: idempotent, ingen ny evidensrad ---------------
            r1b = c.post("/v1/oppdrag/kvittering", json=kvittering("utfort"),
                         headers=h)
            assert r1b.status_code == 200, r1b.text
            assert r1b.json()["status"] == "idempotent_uten_statusendring"

            # --- Motstridende sen kvittering: sikkerhetssak, ikke evidens
            r2 = c.post("/v1/oppdrag/kvittering", json=kvittering("feilet"),
                        headers=h)
            assert r2.status_code == 409, r2.text
            assert r2.json()["feil"] == "kvittering_konflikt"
    finally:
        app.tjeneste.pool.lukk()

    _sett_kontekst(migrator, TENANT)
    hendelser = dict(migrator.execute(
        "SELECT hendelse, count(*) FROM unntak_historikk"
        " WHERE tenant=%s AND unntak_id=%s GROUP BY hendelse",
        (TENANT, sak)).fetchall())
    oppdragsrad = migrator.execute(
        "SELECT status, kansellert_aarsak FROM oppdrag WHERE tenant=%s"
        " AND id=%s", (TENANT, opp)).fetchone()
    # Codex P2 (runde 8): selve den SIGNERTE kvitteringen — grunnlaget
    # §5-saken hviler på — må være bevart, ikke bare oppsummert.
    bevart = migrator.execute(
        "SELECT kvittering->>'resultat', kvittering->>'kvittering_jti',"
        " kvittering_signatur IS NOT NULL, resultathash IS NOT NULL"
        " FROM oppdrag WHERE tenant=%s AND id=%s", (TENANT, opp)).fetchone()
    sen_detalj = migrator.execute(
        "SELECT detalj FROM unntak_historikk WHERE tenant=%s AND unntak_id=%s"
        "   AND hendelse='sen_kvittering'", (TENANT, sak)).fetchone()[0]
    migrator.execute("SET ROLE disponit_m37_claimer")
    kap = migrator.execute(
        "SELECT status, resultathash IS NOT NULL FROM"
        " kvitteringskapabiliteter WHERE jti=%s",
        (a["kvittering_jti"],)).fetchone()
    migrator.execute("RESET ROLE")
    kompensasjon = migrator.execute(
        "SELECT count(*) FROM unntak WHERE tenant=%s AND oppdrag_id=%s"
        " AND arsak='kompensasjon_kreves'", (TENANT, opp)).fetchone()[0]
    migrator.rollback()

    assert hendelser.get("sen_kvittering") == 1, (
        f"den sene kvitteringen nådde aldri evidensgrenen: {hendelser}")
    assert hendelser.get("motstridende_kvittering") == 1, (
        f"motstridende sen kvittering ble ikke en sikkerhetssak: {hendelser}")
    # Fencingen står: nei-et er fortsatt nei-et.
    assert oppdragsrad == ("kansellert", "menneskelig_avvis"), oppdragsrad
    assert kap == ("avvist", True), (
        f"kapabiliteten skulle stått avvist MED sen hash: {kap}")
    # ... og §5-saken finnes: kontrakten sa `kompenserende`.
    assert kompensasjon == 1, "kompensasjonssaken ble aldri født"
    # ... OG BEVISET SAKEN HVILER PÅ ER BEVART (Codex P2, runde 8).
    #
    # Saken påstår at handlingen skjedde. Uten den signerte kvitteringen
    # kunne ingen etterpå kontrollere påstanden: evidensraden bar bare
    # resultat + hash, kapabiliteten bare hashen, og selve kvitteringen
    # fantes ingen steder. Et kansellert oppdrag er terminalt, så
    # lagringen blokkerer ingen ny eier — den gir bare saken sitt
    # grunnlag, uforanderlig.
    #
    # MUTASJONEN SOM DREPER DENNE: fjern `oppdrag`-UPDATE-en i sen-grenen.
    assert bevart == ("utfort", a["kvittering_jti"], True, True), (
        "den signerte kvitteringen §5-saken hviler på ble kastet:"
        f" {bevart}")
    assert sen_detalj.get("kvittering_lagret") is True, (
        f"evidensraden sier ikke hvor beviset ligger: {sen_detalj}")


@pg
@pytest.mark.parametrize("rev,ventet_arsak", [
    ("kompenserende", "kompensasjon_kreves"),
    ("irreversibel", "irreversibel_utfort"),
])
def test_P1_sen_feilet_kvittering_foder_ingen_reversibilitetssak(
        migrator, miljo, token, rev, ventet_arsak):
    """043 §5 (Codex P1 runde 3): slutningen krever premisset.

    §5 utleder saken av modulkontraktens reversibilitet ut fra ÉN antakelse:
    at modulen rakk å utføre før nei-et nådde den. En sen kvittering med
    `resultat: "feilet"` sier tvert imot at utførelsen mislyktes — ingen
    sideeffekt inntraff. Grenen så bare på `kansellert_aarsak` og
    kontrakten, aldri på resultatet, og fødte derfor `kompensasjon_kreves`
    (be et menneske kompensere for noe som aldri ble gjort) eller
    `irreversibel_utfort` (før i revisjonssporet at en irreversibel handling
    ER utført, stikk i strid med utførerens egen rapport).

    Evidensen skal fortsatt lagres — en sen kvittering ER evidens uansett
    utfall, og bærer nå resultatet i detaljen. Det er SLUTNINGEN som faller
    bort.

    MUTASJONEN SOM DREPER DENNE: fjern `and sen_utfort` fra §5-grenen.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app

    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    cid_sak = secrets.token_hex(16)
    _sett_kontekst(migrator, TENANT, "m37-arbeider", cid_sak)
    migrator.execute(
        "UPDATE unntak SET status='under_behandling', claim_id=%s,"
        " claim_generation=1, claim_utloper=now()+interval '600 s'"
        " WHERE tenant=%s AND id=%s", (cid_sak, TENANT, sak))
    migrator.execute("UPDATE unntak SET status='venter_utførelse'"
                     " WHERE tenant=%s AND id=%s", (TENANT, sak))
    migrator.commit()

    app = lag_app(DSN)
    try:
        with TestClient(app) as c:
            tok, _ = token(rolle="eiermodul:reinnsending",
                           scopes=("orders:execute:purring.",))
            h = {"authorization": f"Bearer {tok}"}
            a = c.post("/v1/oppdrag/claim", json={}, headers=h).json()
            assert a["oppdrag_id"] == opp, a

            modul = "m-" + secrets.token_hex(4)
            kh = secrets.token_hex(16)
            _sett_kontekst(migrator, TENANT)
            migrator.execute("SET ROLE disponit_modul_eier")
            migrator.execute(
                "INSERT INTO modulkontrakt (modul_id, kontraktversjon,"
                " kontrakt_hash, payload_schema_hash, kvittering_schema_hash,"
                " sideeffektklasse, reversibilitet)"
                " VALUES (%s,1,%s,'p','k','ekstern_lesing',%s)",
                (modul, kh, rev))
            migrator.execute("RESET ROLE")
            migrator.execute("ALTER TABLE oppdrag DISABLE TRIGGER USER")
            migrator.execute(
                "UPDATE oppdrag SET modul_id=%s, kontraktversjon=1,"
                " kontrakt_hash=%s WHERE tenant=%s AND id=%s",
                (modul, kh, TENANT, opp))
            migrator.execute("ALTER TABLE oppdrag ENABLE TRIGGER USER")
            migrator.commit()

            # Mennesket sier nei — den EKTE oppløsningsveien.
            _sett_kontekst(migrator, TENANT, "menneske", "r-avvis")
            _attester_avvis(migrator, TENANT, sak, "menneske")
            migrator.execute("SET ROLE disponit_m37_claimer")
            res = migrator.execute(
                "SELECT utfall FROM avvis_med_opplosning(%s,%s,%s,"
                "'menneske','r-avvis')", (TENANT, sak, [opp])).fetchall()
            migrator.execute("RESET ROLE")
            migrator.commit()
            assert res == [("kansellert",)], res

            # Den sene kvitteringen sier at utførelsen MISLYKTES.
            r = c.post("/v1/oppdrag/kvittering",
                       json=_signer_kvittering({
                           "oppdrag_id": opp, "tenant": TENANT,
                           "kvittering_jti": a["kvittering_jti"],
                           "repair_operation_id": a["repair_operation_id"],
                           "owner_claim_id": a["owner_claim_id"],
                           "owner_generation": a["owner_generation"],
                           "resultat": "feilet", "ressurs_id": "fak-1"}),
                       headers=h)
            assert r.status_code == 202, r.text
            assert r.json()["status"] == "lagret_uten_statusendring"
    finally:
        app.tjeneste.pool.lukk()

    _sett_kontekst(migrator, TENANT)
    detalj = migrator.execute(
        "SELECT detalj FROM unntak_historikk WHERE tenant=%s AND unntak_id=%s"
        "   AND hendelse='sen_kvittering'", (TENANT, sak)).fetchall()
    saker = migrator.execute(
        "SELECT count(*) FROM unntak WHERE tenant=%s AND oppdrag_id=%s"
        "   AND arsak=%s", (TENANT, opp, ventet_arsak)).fetchone()[0]
    oppdragsrad = migrator.execute(
        "SELECT status, kansellert_aarsak FROM oppdrag WHERE tenant=%s"
        " AND id=%s", (TENANT, opp)).fetchone()
    migrator.rollback()

    # Evidensen står — og sier hva utføreren faktisk rapporterte.
    assert len(detalj) == 1, detalj
    assert detalj[0][0].get("resultat") == "feilet", detalj
    # ... men slutningen er ikke trukket.
    assert saker == 0, (
        f"en feilet sen kvittering fødte {ventet_arsak} — §5-slutningen ble"
        " trukket uten premisset om at handlingen faktisk skjedde")
    # Fencingen står uansett: nei-et er fortsatt nei-et.
    assert oppdragsrad == ("kansellert", "menneskelig_avvis"), oppdragsrad


@pg
def test_P1_ukjent_reversibilitet_eskalerer_den_sene_utforelsen(
        migrator, miljo, token):
    """043 §5 (Codex P1, runde 8): UKJENT reversibilitet er ikke TRYGG.

    Mappingen var et oppslag med stille frafall: alt som ikke var
    `kompenserende` eller `irreversibel` ga ingen sak. For `direkte` er det
    riktig — kontrakten sier at virkningen reverserer seg selv. Men
    claim-veien tillater BEVISST oppgavetyper uten registrert
    modulkontrakt (037: uregistrert oppdragstype → ingen binding), og de
    kjører med `modul_id`/`kontraktversjon`/`kontrakt_hash` NULL. En slik
    oppgave kan utføre og sende en gyldig, signert `utfort`-kvittering
    etter et menneskelig nei — og da svarer `reversibilitet_for_oppdrag`
    NULL, ikke `direkte`.

    Hendelsen falt rett gjennom: ingen kompensasjonssak, ingen
    irreversibilitetsvurdering, ingen som fikk vite det — enda systemet
    ikke har ETT kontraktbevis for at virkningen er trygg. Fraværet av
    bevis ble behandlet som bevis på fravær, i nøyaktig den grenen §5 ble
    bygget for å hindre stillhet i.

    MUTASJONEN SOM DREPER DENNE: la mappingen falle tilbake til None
    (`.get(reversibilitet)`) i stedet for `reversibilitet_ukjent`.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app

    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    cid_sak = secrets.token_hex(16)
    _sett_kontekst(migrator, TENANT, "m37-arbeider", cid_sak)
    migrator.execute(
        "UPDATE unntak SET status='under_behandling', claim_id=%s,"
        " claim_generation=1, claim_utloper=now()+interval '600 s'"
        " WHERE tenant=%s AND id=%s", (cid_sak, TENANT, sak))
    migrator.execute("UPDATE unntak SET status='venter_utførelse'"
                     " WHERE tenant=%s AND id=%s", (TENANT, sak))
    migrator.commit()

    app = lag_app(DSN)
    try:
        with TestClient(app) as c:
            tok, _ = token(rolle="eiermodul:reinnsending",
                           scopes=("orders:execute:purring.",))
            h = {"authorization": f"Bearer {tok}"}
            a = c.post("/v1/oppdrag/claim", json={}, headers=h).json()
            assert a["oppdrag_id"] == opp, a

            # LEGACY-TILSTANDEN, eksplisitt: ingen modulkontrakt er
            # registrert, og bindingen står NULL — nøyaktig det 037 lar en
            # uregistrert oppdragstype gjøre.
            _sett_kontekst(migrator, TENANT)
            migrator.execute("ALTER TABLE oppdrag DISABLE TRIGGER USER")
            migrator.execute(
                "UPDATE oppdrag SET modul_id=NULL, kontraktversjon=NULL,"
                " kontrakt_hash=NULL WHERE tenant=%s AND id=%s",
                (TENANT, opp))
            migrator.execute("ALTER TABLE oppdrag ENABLE TRIGGER USER")
            migrator.commit()
            # Premisset måles, ikke antas: oppslaget svarer NULL.
            _sett_kontekst(migrator, TENANT, "sen", "r-rev")
            migrator.execute("SET ROLE disponit_m37_claimer")
            rev = migrator.execute("SELECT reversibilitet_for_oppdrag(%s,%s)",
                                   (TENANT, opp)).fetchone()[0]
            migrator.execute("RESET ROLE")
            migrator.rollback()
            assert rev is None, f"premisset holder ikke: {rev!r}"

            # Mennesket sier nei — den EKTE oppløsningsveien.
            _sett_kontekst(migrator, TENANT, "menneske", "r-avvis")
            _attester_avvis(migrator, TENANT, sak, "menneske")
            migrator.execute("SET ROLE disponit_m37_claimer")
            res = migrator.execute(
                "SELECT utfall FROM avvis_med_opplosning(%s,%s,%s,"
                "'menneske','r-avvis')", (TENANT, sak, [opp])).fetchall()
            migrator.execute("RESET ROLE")
            migrator.commit()
            assert res == [("kansellert",)], res

            # ... og SÅ kommer den sene kvitteringen: utførelsen SKJEDDE.
            r = c.post("/v1/oppdrag/kvittering",
                       json=_signer_kvittering({
                           "oppdrag_id": opp, "tenant": TENANT,
                           "kvittering_jti": a["kvittering_jti"],
                           "repair_operation_id": a["repair_operation_id"],
                           "owner_claim_id": a["owner_claim_id"],
                           "owner_generation": a["owner_generation"],
                           "resultat": "utfort", "ressurs_id": "fak-1"}),
                       headers=h)
            assert r.status_code == 202, r.text
            assert r.json()["status"] == "lagret_uten_statusendring"
    finally:
        app.tjeneste.pool.lukk()

    _sett_kontekst(migrator, TENANT)
    saker = dict(migrator.execute(
        "SELECT arsak, count(*) FROM unntak WHERE tenant=%s AND oppdrag_id=%s"
        " GROUP BY arsak", (TENANT, opp)).fetchall())
    detalj = migrator.execute(
        "SELECT detalj FROM unntak_historikk WHERE tenant=%s AND unntak_id=%s"
        "   AND hendelse='sen_kvittering'", (TENANT, sak)).fetchall()
    migrator.rollback()

    assert len(detalj) == 1, detalj
    assert saker.get("reversibilitet_ukjent") == 1, (
        "en sen UTFØRT kvittering uten modulkontrakt ga ingen sak et"
        f" menneske kan se: {saker}")
    # ... og den er ikke feilklassifisert som en av de to vi HAR dekning for.
    assert "kompensasjon_kreves" not in saker, saker
    assert "irreversibel_utfort" not in saker, saker


@pg
def test_P1_brukt_kapabilitet_kan_ikke_gjenbrukes_paa_nytt_oppdrag(migrator,
                                                                   miljo, token):
    """En forbrukt kapabilitet er forbrukt — også for et annet oppdrag.

    Uten dette ville «forbruket» vært en bokføring uten virkning: den
    samme jti-en kunne båret et resultat til et oppdrag den aldri ble
    utstedt for.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app

    # TO saker, ikke to oppdrag på én: delindeksen
    # `en_aktiv_reparasjon_per_sak` tillater kun én aktiv reparasjon per
    # sak, og den gjorde riktig i å stoppe førsteutkastet av denne testen.
    sak1, logg1 = _lag_sak(migrator, TENANT)
    sak2, logg2 = _lag_sak(migrator, TENANT)
    opp1, _ = _lag_oppdrag(migrator, TENANT, sak1, logg1)
    opp2, _ = _lag_oppdrag(migrator, TENANT, sak2, logg2)

    app = lag_app(DSN)
    try:
        with TestClient(app) as c:
            tok, _ = token(rolle="eiermodul:reinnsending",
                           scopes=("orders:execute:purring.",))
            h = {"authorization": f"Bearer {tok}"}
            a = c.post("/v1/oppdrag/claim", json={}, headers=h).json()

            # Kapabiliteten er bundet til ETT oppdrag. En kropp som peker
            # på et annet avvises — uansett hvor gyldig signaturen er.
            feil_oppdrag = _signer_kvittering({
                "oppdrag_id": opp2 if a["oppdrag_id"] == opp1 else opp1,
                "tenant": TENANT, "kvittering_jti": a["kvittering_jti"],
                "repair_operation_id": a["repair_operation_id"],
                "owner_claim_id": a["owner_claim_id"],
                "owner_generation": a["owner_generation"],
                "resultat": "utfort", "ressurs_id": "fak-1"})
            r = c.post("/v1/oppdrag/kvittering", json=feil_oppdrag, headers=h)
            assert r.status_code == 401, r.text
            assert r.json()["feil"] == "kapabilitet_ugyldig"
    finally:
        app.tjeneste.pool.lukk()


# ===========================================================================
# Codex runde 3 — taperen i forbrukskappløpet må klassifiseres riktig
# ===========================================================================

def _oppsett_stale_kapabilitet(migrator, klient, token_h, eiermodul):
    """A claimer, mister leasen, B reclaimer. -> (opp, sak, a, b)

    Etter dette går ALLE A sine kvitteringer sen-evidensveien: gyldig
    signert, men fra en utdatert generasjon.
    """
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg, eiermodul=eiermodul)
    cid = secrets.token_hex(16)
    _sett_kontekst(migrator, TENANT, "m37-arbeider", cid)
    migrator.execute(
        "UPDATE unntak SET status='under_behandling', claim_id=%s,"
        " claim_generation=1, claim_utloper=now()+interval '600 s'"
        " WHERE tenant=%s AND id=%s", (cid, TENANT, sak))
    migrator.execute("UPDATE unntak SET status='venter_utførelse'"
                     " WHERE tenant=%s AND id=%s", (TENANT, sak))
    migrator.commit()

    ra = klient.post("/v1/oppdrag/claim", json={}, headers=token_h)
    assert ra.status_code == 200, ra.text
    a = ra.json()
    assert a["oppdrag_id"] == opp, "A fikk et annet oppdrag enn vårt"
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE oppdrag SET owner_lease_utloper=now()"
                     " - interval '1 s' WHERE tenant=%s AND id=%s",
                     (TENANT, opp))
    migrator.commit()
    rb = klient.post("/v1/oppdrag/claim", json={}, headers=token_h)
    assert rb.status_code == 200, rb.text
    b = rb.json()
    assert b["oppdrag_id"] == opp and b["owner_generation"] == 2
    return opp, sak, a, b


def _kvitteringskropp(kap, opp, resultat):
    return _signer_kvittering({
        "oppdrag_id": opp, "tenant": TENANT,
        "kvittering_jti": kap["kvittering_jti"],
        "repair_operation_id": kap["repair_operation_id"],
        "owner_claim_id": kap["owner_claim_id"],
        "owner_generation": kap["owner_generation"],
        "resultat": resultat, "ressurs_id": "fak-1"})


def _vinner_holder_laasen(migrator_dsn, tenant, sak, opp, jti, vinnerhash):
    """En transaksjon som gjør NØYAKTIG det vinneren gjør — og holder igjen.

    Radlåsen på kapabiliteten holdes til vi committer. Taperen blokkerer
    på `bruk_kvitteringskapabilitet` og får først svar etterpå.

    Merk hvorfor dette IKKE er en timing-test: blokkerer taperen, klassifi-
    seres den mot vinnerens committede rad; kommer den etter commiten,
    klassifiseres den mot nøyaktig den samme raden. Begge interleavinger gir
    samme utfall, så assertionene kan ikke bli flaky — i motsetning til to
    tråder som kappes fritt (samme felle som trådtesten i PR-002).
    """
    from db.pg import koble
    h = koble(migrator_dsn)
    h.execute("SELECT set_config('disponit.tenant',%s,true),"
              "       set_config('disponit.aktor','vinner',true),"
              "       set_config('disponit.request_id','vinner',true)",
              (tenant,))
    h.execute(
        "INSERT INTO unntak_historikk (tenant, unntak_id, hendelse, aktor,"
        " request_id, detalj) VALUES (%s,%s,'sen_kvittering','vinner',"
        " 'vinner',%s)",
        (tenant, sak, json.dumps({"oppdrag_id": opp,
                                  "resultathash": vinnerhash})))
    h.execute("SET LOCAL ROLE disponit_m37_claimer")
    h.execute("UPDATE kvitteringskapabiliteter SET status='brukt',"
              " resultathash=%s, brukt_ts=now() WHERE jti=%s",
              (vinnerhash, jti))
    h.execute("RESET ROLE")
    return h                       # IKKE committet — låsen holdes


@pg
def test_P1_samtidig_identisk_repost_blir_idempotent_ikke_auth_feil(
        migrator, miljo, token):
    """Codex P1 runde 3, tilfelle 1.

    Taperen av forbrukskappløpet fikk `kapabilitet_ugyldig` (401) uten å
    lese hashen som vant. To identiske samtidige kvitteringer ble dermed
    «202 + 401» i stedet for «202 + idempotent 200».

    MUTASJONEN SOM DREPER DENNE: la `bruk_kvitteringskapabilitet` returnere
    `'ugyldig'` i stedet for `'idempotent'` — eller la kalleren svare
    generisk `kapabilitet_ugyldig` når forbruket taper.
    """
    import threading
    from starlette.testclient import TestClient
    from api.app import lag_app, _resultathash

    app = lag_app(DSN)
    try:
        with TestClient(app) as c:
            modul = _unik_eiermodul()
            tok, _ = token(rolle=modul,
                           scopes=("orders:execute:purring.",))
            h = {"authorization": f"Bearer {tok}"}
            opp, sak, a, b = _oppsett_stale_kapabilitet(migrator, c, h, modul)

            kropp = _kvitteringskropp(a, opp, "utfort")
            vinnerhash = _resultathash(kropp)
            holder = _vinner_holder_laasen(MIGRATOR_DSN, TENANT, sak, opp,
                                           a["kvittering_jti"], vinnerhash)
            svar = {}

            def taper():
                svar["r"] = c.post("/v1/oppdrag/kvittering", json=kropp,
                                   headers=h)

            t = threading.Thread(target=taper)
            t.start()
            time.sleep(0.5)          # la taperen rekke fram til låsen
            holder.commit()
            holder.close()
            t.join(timeout=30)
            assert not t.is_alive(), "taperen hang på låsen"

            r = svar["r"]
            assert r.status_code == 200, r.text
            # Vinneren her er en SEN kvittering (`_vinner_holder_laasen`
            # skriver `sen_kvittering` og rører ikke oppdragsstatusen), så
            # taperen skal få det samme ordet en sekvensiell retry ville
            # fått — kappløpsveien og retryveien svarer likt (Codex P2).
            assert r.json()["status"] == "idempotent_uten_statusendring", r.text
    finally:
        app.tjeneste.pool.lukk()

    _sett_kontekst(migrator, TENANT)
    hendelser = dict(migrator.execute(
        "SELECT hendelse, count(*) FROM unntak_historikk"
        " WHERE tenant=%s AND unntak_id=%s GROUP BY hendelse",
        (TENANT, sak)).fetchall())
    status = migrator.execute(
        "SELECT o.status, u.status FROM oppdrag o JOIN unntak u"
        "   ON u.tenant=o.tenant AND u.id=o.unntak_id"
        " WHERE o.tenant=%s AND o.id=%s", (TENANT, opp)).fetchone()
    migrator.rollback()
    assert hendelser.get("sen_kvittering") == 1, (
        f"NØYAKTIG én sen evidensrad forventet, fikk {hendelser}")
    assert "motstridende_kvittering" not in hendelser, (
        f"identisk resultat ble feilklassifisert som konflikt: {hendelser}")
    # Tilfelle 3 fra reviewen: stale-generation-veien endrer ingen status.
    assert status == ("plukket", "venter_utførelse"), status


@pg
def test_P1_samtidig_motstridende_repost_blir_sikkerhetssak(migrator, miljo,
                                                            token):
    """Codex P1 runde 3, tilfelle 2 — den alvorlige.

    To ULIKE resultater levert samtidig forsvant som et generisk
    auth-avvik. Det er nøyaktig den hendelsen sikkerhetssaken finnes for,
    og den ble aldri registrert.

    MUTASJONEN SOM DREPER DENNE: fjern `konflikt`-grenen i
    `_forbruk_kapabilitet`, eller la SQL-funksjonen returnere `'ugyldig'`
    ved ulik hash.
    """
    import threading
    from starlette.testclient import TestClient
    from api.app import lag_app, _resultathash

    app = lag_app(DSN)
    try:
        with TestClient(app) as c:
            modul = _unik_eiermodul()
            tok, _ = token(rolle=modul,
                           scopes=("orders:execute:purring.",))
            h = {"authorization": f"Bearer {tok}"}
            opp, sak, a, b = _oppsett_stale_kapabilitet(migrator, c, h, modul)

            # Vinneren leverte `utfort`; taperen leverer `feilet` med SAMME
            # kapabilitet. Ulike resultathasher.
            vinnerhash = _resultathash(_kvitteringskropp(a, opp, "utfort"))
            taperkropp = _kvitteringskropp(a, opp, "feilet")
            assert _resultathash(taperkropp) != vinnerhash

            holder = _vinner_holder_laasen(MIGRATOR_DSN, TENANT, sak, opp,
                                           a["kvittering_jti"], vinnerhash)
            svar = {}

            def taper():
                svar["r"] = c.post("/v1/oppdrag/kvittering", json=taperkropp,
                                   headers=h)

            t = threading.Thread(target=taper)
            t.start()
            time.sleep(0.5)
            holder.commit()
            holder.close()
            t.join(timeout=30)
            assert not t.is_alive(), "taperen hang på låsen"

            r = svar["r"]
            assert r.status_code == 409, r.text
            assert r.json()["feil"] == "kvittering_konflikt", r.text
    finally:
        app.tjeneste.pool.lukk()

    _sett_kontekst(migrator, TENANT)
    hendelser = dict(migrator.execute(
        "SELECT hendelse, count(*) FROM unntak_historikk"
        " WHERE tenant=%s AND unntak_id=%s GROUP BY hendelse",
        (TENANT, sak)).fetchall())
    status = migrator.execute(
        "SELECT o.status, u.status FROM oppdrag o JOIN unntak u"
        "   ON u.tenant=o.tenant AND u.id=o.unntak_id"
        " WHERE o.tenant=%s AND o.id=%s", (TENANT, opp)).fetchone()
    migrator.rollback()
    assert hendelser.get("sen_kvittering") == 1, (
        f"taperen skrev en ordinær evidensrad: {hendelser}")
    assert hendelser.get("motstridende_kvittering") == 1, (
        f"motstridende samtidig kvittering ble ikke sikkerhetssak: {hendelser}")
    assert status == ("plukket", "venter_utførelse"), status


@pg
def test_P1_kvitteringsveien_laser_saken_for_kapabiliteten(migrator, miljo,
                                                           token):
    """043 (Gate 14b), Codex P1 runde 3: den YTRE låsen i kappløpet.

    Avvis-veien tar tre rader i rekkefølgen sak → kapabilitet → oppdrag:
    `behandle_unntakshandling` låser `unntak` med `FOR UPDATE` og holder den
    gjennom hele operatørhandlingen, og inne i den låsen pre-låser
    `avvis_med_opplosning` kapabilitetene før oppdragene (043 §7).

    Kvitteringsveien tok de samme radene fra motsatt ende: kapabiliteten
    brant i `_forbruk_kapabilitet`, saken ble først rørt til slutt
    (historikkraden + `UPDATE unntak`). Da kan avvis-veien holde saken og
    vente på kapabiliteten mens kvitteringen holder kapabiliteten og venter
    på saken — PostgreSQL avbryter én med 40P01. Forrige rundes pre-pass
    rettet bare den INDRE halvparten; den ytre sakslåsen sto igjen, og
    port 17 bommet på den fordi den kaller `avvis_med_opplosning` direkte,
    altså uten sakslåsen kalleren i praksis alltid holder.

    Målingen er deterministisk, ikke et kappløp: avvis-veiens FØRSTE lås
    (saken) holdes av en egen transaksjon, kvitteringen postes, og så
    spørres kapabilitetsraden med `FOR UPDATE NOWAIT`. Er den låst, står
    kvitteringsveien og venter på saken MENS den holder kapabiliteten —
    nøyaktig den halvparten av vranglåsen som ikke kan sameksistere med
    avvis-veiens andre halvpart.

    MUTASJONEN SOM DREPER DENNE: fjern sakslåsen (`SELECT ... FROM unntak
    ... FOR UPDATE`) i kvitteringsingesten. Da låses kapabiliteten først
    igjen, og NOWAIT-proben feiler.
    """
    import threading

    import psycopg
    from starlette.testclient import TestClient
    from api.app import lag_app
    from db.pg import koble

    app = lag_app(DSN)
    holder = None
    try:
        with TestClient(app) as c:
            modul = _unik_eiermodul()
            tok, _ = token(rolle=modul,
                           scopes=("orders:execute:purring.",))
            h = {"authorization": f"Bearer {tok}"}
            sak, logg = _lag_sak(migrator, TENANT)
            opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg,
                                  eiermodul=modul)
            cid = secrets.token_hex(16)
            _sett_kontekst(migrator, TENANT, "m37-arbeider", cid)
            migrator.execute(
                "UPDATE unntak SET status='under_behandling', claim_id=%s,"
                " claim_generation=1, claim_utloper=now()+interval '600 s'"
                " WHERE tenant=%s AND id=%s", (cid, TENANT, sak))
            migrator.execute("UPDATE unntak SET status='venter_utførelse'"
                             " WHERE tenant=%s AND id=%s", (TENANT, sak))
            migrator.commit()

            ra = c.post("/v1/oppdrag/claim", json={}, headers=h)
            assert ra.status_code == 200, ra.text
            a = ra.json()
            assert a["oppdrag_id"] == opp, "claimet traff et annet oppdrag"

            # (1) Avvis-veiens første lås: SAKEN. Holdes ucommittet.
            holder = koble(MIGRATOR_DSN)
            holder.execute("SELECT set_config('disponit.tenant',%s,true)",
                           (TENANT,))
            holder.execute("SELECT 1 FROM unntak WHERE tenant=%s AND id=%s"
                           "   FOR UPDATE", (TENANT, sak))

            # (2) Kvitteringen postes og skal blokkere PÅ SAKEN.
            svar = {}

            def kvitter():
                svar["r"] = c.post("/v1/oppdrag/kvittering",
                                   json=_kvitteringskropp(a, opp, "utfort"),
                                   headers=h)

            t = threading.Thread(target=kvitter)
            t.start()
            time.sleep(1.0)      # kvitteringen rekker fram til låsen
            assert "r" not in svar, "kvitteringen gikk forbi sakslåsen"

            # (3) MÅLINGEN: kapabiliteten skal være urørt mens saken holdes.
            probe = koble(MIGRATOR_DSN)
            probe.execute("SELECT set_config('disponit.tenant',%s,true)",
                          (TENANT,))
            probe.execute("SET LOCAL ROLE disponit_m37_claimer")
            try:
                probe.execute("SELECT 1 FROM kvitteringskapabiliteter"
                              " WHERE jti=%s FOR UPDATE NOWAIT",
                              (a["kvittering_jti"],))
            except psycopg.errors.LockNotAvailable:
                pytest.fail(
                    "kvitteringsveien holder kapabilitetslåsen mens den"
                    " venter på saken — motsatt rekkefølge av avvis-veien,"
                    " altså den ene halvparten av en 40P01")
            finally:
                probe.rollback(); probe.close()

            # (4) Slippes saken fri, går kvitteringen gjennom som normalt.
            holder.commit(); holder.close(); holder = None
            t.join(timeout=30)
            assert not t.is_alive(), "kvitteringen ble aldri sluppet fri"
            r = svar["r"]
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "utfort", r.text
    finally:
        if holder is not None:
            holder.rollback(); holder.close()
        app.tjeneste.pool.lukk()

    _sett_kontekst(migrator, TENANT)
    status = migrator.execute(
        "SELECT o.status, u.status FROM oppdrag o JOIN unntak u"
        "   ON u.tenant=o.tenant AND u.id=o.unntak_id"
        " WHERE o.tenant=%s AND o.id=%s", (TENANT, opp)).fetchone()
    migrator.rollback()
    assert status == ("utfort", "løst"), status


@pg
def test_P1_kvitteringen_leser_tilstanden_paa_nytt_etter_sakslasen(
        migrator, miljo, token):
    """043 (Gate 14b), Codex P1 runde 4: låsen uten ny lesing er blind.

    Forrige runde flyttet sakslåsen foran kapabilitets-/oppdragslåsen, så
    de to veiene tar radene i samme rekkefølge. Men kapabiliteten og
    oppdragsraden leses i steg 1/1b — altså FØR den låsen. Kommer
    kvitteringen fram mens et menneskelig nei holder saken, venter den her
    til nei-et har committet, og regner så videre på verdiene fra tiden før:
    kapabiliteten «utstedt», oppdraget «plukket», generasjonen eierens.

    Følgen var det motsatte av det §5 er til for: `kan_avslutte` ble True,
    den ORDINÆRE toargsbrenningen kjørte mot en kapabilitet som nå sto
    `avvist`, og `_forbruk_kapabilitet` rullet tilbake med
    `kapabilitet_ugyldig`. Den signerte kvitteringen ble aldri skrevet som
    `sen_kvittering`, og kompensasjonssaken ble aldri født — vranglåsen var
    byttet mot et stille tap av nettopp den evidensen sen-evidensveien ble
    bygget for.

    Målingen er deterministisk, ikke et kappløp: nei-et tar sakslåsen
    FØRST og holder den, kvitteringen postes og må blokkere der, og så
    kjører nei-et den ekte oppløsningsveien inne i den samme transaksjonen
    før den committer. Når kvitteringen slippes fri, er kapabiliteten
    `avvist` og oppdraget `kansellert` — og den skal da ta
    sen-evidensveien, ikke den avsluttende.

    MUTASJONEN SOM DREPER DENNE: fjern de to `SELECT`-ene etter sakslåsen i
    steg 3c. Da står `kap_status`/`status`/generasjonen fra førstelesningen,
    202-en blir `kapabilitet_ugyldig`, og kompensasjonssaken uteblir.
    """
    import threading

    from starlette.testclient import TestClient
    from api.app import lag_app
    from db.pg import koble

    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    cid_sak = secrets.token_hex(16)
    _sett_kontekst(migrator, TENANT, "m37-arbeider", cid_sak)
    migrator.execute(
        "UPDATE unntak SET status='under_behandling', claim_id=%s,"
        " claim_generation=1, claim_utloper=now()+interval '600 s'"
        " WHERE tenant=%s AND id=%s", (cid_sak, TENANT, sak))
    migrator.execute("UPDATE unntak SET status='venter_utførelse'"
                     " WHERE tenant=%s AND id=%s", (TENANT, sak))
    migrator.commit()

    app = lag_app(DSN)
    holder = None
    try:
        with TestClient(app) as c:
            tok, _ = token(rolle="eiermodul:reinnsending",
                           scopes=("orders:execute:purring.",))
            h = {"authorization": f"Bearer {tok}"}
            a = c.post("/v1/oppdrag/claim", json={}, headers=h).json()
            assert a["oppdrag_id"] == opp, a

            # Modulkontrakten oppdraget kjørte under: `kompenserende`. §5
            # utleder saken av KONTRAKTEN, aldri av gjetning.
            modul = "m-" + secrets.token_hex(4)
            kh = secrets.token_hex(16)
            _sett_kontekst(migrator, TENANT)
            migrator.execute("SET ROLE disponit_modul_eier")
            migrator.execute(
                "INSERT INTO modulkontrakt (modul_id, kontraktversjon,"
                " kontrakt_hash, payload_schema_hash, kvittering_schema_hash,"
                " sideeffektklasse, reversibilitet)"
                " VALUES (%s,1,%s,'p','k','ekstern_lesing','kompenserende')",
                (modul, kh))
            migrator.execute("RESET ROLE")
            migrator.execute("ALTER TABLE oppdrag DISABLE TRIGGER USER")
            migrator.execute(
                "UPDATE oppdrag SET modul_id=%s, kontraktversjon=1,"
                " kontrakt_hash=%s WHERE tenant=%s AND id=%s",
                (modul, kh, TENANT, opp))
            migrator.execute("ALTER TABLE oppdrag ENABLE TRIGGER USER")
            migrator.commit()

            # (1) Nei-ets FØRSTE lås: saken. Holdes ucommittet, akkurat som
            #     `behandle_unntakshandling` holder den (steg 2).
            holder = koble(MIGRATOR_DSN)
            _sett_kontekst(holder, TENANT, "menneske", "r-avvis")
            holder.execute("SELECT 1 FROM unntak WHERE tenant=%s AND id=%s"
                           "   FOR UPDATE", (TENANT, sak))

            # (2) Kvitteringen postes og skal blokkere PÅ SAKEN.
            kropp = _signer_kvittering({
                "oppdrag_id": opp, "tenant": TENANT,
                "kvittering_jti": a["kvittering_jti"],
                "repair_operation_id": a["repair_operation_id"],
                "owner_claim_id": a["owner_claim_id"],
                "owner_generation": a["owner_generation"],
                "resultat": "utfort", "ressurs_id": "fak-1"})
            svar = {}

            def kvitter():
                svar["r"] = c.post("/v1/oppdrag/kvittering", json=kropp,
                                   headers=h)

            t = threading.Thread(target=kvitter)
            t.start()
            time.sleep(1.0)      # kvitteringen rekker fram til sakslåsen
            assert "r" not in svar, "kvitteringen gikk forbi sakslåsen"

            # (3) Nei-et kjører den EKTE oppløsningsveien INNE i den samme
            #     transaksjonen: kapabiliteten brennes `avvist`, claimet
            #     fences, oppdraget kanselleres. Så committer det.
            _attester_avvis(holder, TENANT, sak, "menneske")
            holder.execute("SET ROLE disponit_m37_claimer")
            res = holder.execute(
                "SELECT utfall FROM avvis_med_opplosning(%s,%s,%s,"
                "'menneske','r-avvis')", (TENANT, sak, [opp])).fetchall()
            holder.execute("RESET ROLE")
            assert res == [("kansellert",)], res
            holder.commit(); holder.close(); holder = None

            # (4) MÅLINGEN: kvitteringen slippes fri og møter en verden som
            #     har endret seg under føttene på den.
            t.join(timeout=30)
            assert not t.is_alive(), "kvitteringen ble aldri sluppet fri"
            r = svar["r"]
            assert r.status_code == 202, (
                "kvitteringen regnet på tilstanden fra FØR sakslåsen og"
                f" mistet seg selv: {r.status_code} {r.text}")
            assert r.json()["status"] == "lagret_uten_statusendring", r.text
    finally:
        if holder is not None:
            holder.rollback(); holder.close()
        app.tjeneste.pool.lukk()

    _sett_kontekst(migrator, TENANT)
    hendelser = dict(migrator.execute(
        "SELECT hendelse, count(*) FROM unntak_historikk"
        " WHERE tenant=%s AND unntak_id=%s GROUP BY hendelse",
        (TENANT, sak)).fetchall())
    oppdragsrad = migrator.execute(
        "SELECT status, kansellert_aarsak FROM oppdrag WHERE tenant=%s"
        " AND id=%s", (TENANT, opp)).fetchone()
    migrator.execute("SET ROLE disponit_m37_claimer")
    kap = migrator.execute(
        "SELECT status, resultathash IS NOT NULL FROM"
        " kvitteringskapabiliteter WHERE jti=%s",
        (a["kvittering_jti"],)).fetchone()
    migrator.execute("RESET ROLE")
    kompensasjon = migrator.execute(
        "SELECT count(*) FROM unntak WHERE tenant=%s AND oppdrag_id=%s"
        " AND arsak='kompensasjon_kreves'", (TENANT, opp)).fetchone()[0]
    migrator.rollback()

    assert hendelser.get("sen_kvittering") == 1, (
        f"den sene kvitteringen nådde aldri evidensgrenen: {hendelser}")
    # Fencingen står: nei-et er fortsatt nei-et.
    assert oppdragsrad == ("kansellert", "menneskelig_avvis"), oppdragsrad
    assert kap == ("avvist", True), (
        f"kapabiliteten skulle stått avvist MED sen hash: {kap}")
    assert kompensasjon == 1, "kompensasjonssaken ble aldri født"


@pg
def test_P1_sakslasen_dekker_beslutningsopphavet(migrator, miljo, token):
    """043 (Gate 14b), Codex P1 runde 5: saken har TO relasjonsretninger.

    `oppdrag.unntak_id` er OPPHAV, ikke generell sakstilknytning (038). Et
    BESLUTNINGSoppdrag har den NULL, og saken peker den andre veien
    (`unntak.oppdrag_id`). §4 i 043 gjorde nettopp den koblingen avvisbar:
    `sak_utestaaende` finner beslutningsoppdrag gjennom den, og §7 godtar
    dem som oppløsningsmål.

    Sakslåsen og oppfriskningen under den sto likevel bak `unntak_id is not
    None` — altså bare reparasjonsopphavet. For beslutningsoppdrag hoppet
    kvitteringsveien over begge, og hele tapet fra runde 4 var tilbake:
    nei-et rekker å committe kansellering og `avvist`, kvitteringen regner
    videre på `plukket`/`utstedt`, den ordinære toargsbrenningen svarer
    `ugyldig`, og den signerte sene evidensen — med kompensasjonssaken §5
    skal føde — går tapt i stillhet.

    Samme deterministiske måling som runde 4, på det andre opphavet: nei-et
    tar sakslåsen først, kvitteringen må BLOKKERE der (uten fiksen seiler
    den rett forbi og lukker oppdraget), nei-et kjører den ekte
    oppløsningsveien i samme transaksjon og committer.

    MUTASJONEN SOM DREPER DENNE: fjern `OR u.oppdrag_id=%s` fra sakslåsen i
    steg 3c. Da blokkerer kvitteringen aldri, og både 202-en og
    kompensasjonssaken uteblir.
    """
    import threading

    from starlette.testclient import TestClient
    from api.app import lag_app
    from db.pg import koble

    # Oppdraget legges ut under en REPARASJONSsak (fixturen bygger den ekte
    # kjeden), men saken som skal peke TILBAKE er en EGEN, fersk sak. Det er
    # ikke kosmetikk: `reparasjonsoperasjoner` peker på reparasjonssaken, og
    # 038 holder de to saksfamiliene fra hverandre — en oppdragssak står
    # aldri i `oppdrag.unntak_id`, og en reparasjonsoperasjon peker aldri på
    # en oppdragssak. Å gjøre reparasjonssaken om til en oppdragssak ville
    # laget en rad ingen produksjonsvei kan lage.
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    sak_b, _ = _lag_sak(migrator, TENANT)
    cid_sak = secrets.token_hex(16)
    _sett_kontekst(migrator, TENANT, "m37-arbeider", cid_sak)
    migrator.execute(
        "UPDATE unntak SET status='under_behandling', claim_id=%s,"
        " claim_generation=1, claim_utloper=now()+interval '600 s'"
        " WHERE tenant=%s AND id=%s", (cid_sak, TENANT, sak_b))
    migrator.execute("UPDATE unntak SET status='venter_utførelse'"
                     " WHERE tenant=%s AND id=%s", (TENANT, sak_b))
    migrator.commit()

    app = lag_app(DSN)
    holder = None
    try:
        with TestClient(app) as c:
            tok, _ = token(rolle="eiermodul:reinnsending",
                           scopes=("orders:execute:purring.",))
            h = {"authorization": f"Bearer {tok}"}
            a = c.post("/v1/oppdrag/claim", json={}, headers=h).json()
            assert a["oppdrag_id"] == opp, a

            modul = "m-" + secrets.token_hex(4)
            kh = secrets.token_hex(16)
            _sett_kontekst(migrator, TENANT)
            migrator.execute("SET ROLE disponit_modul_eier")
            migrator.execute(
                "INSERT INTO modulkontrakt (modul_id, kontraktversjon,"
                " kontrakt_hash, payload_schema_hash, kvittering_schema_hash,"
                " sideeffektklasse, reversibilitet)"
                " VALUES (%s,1,%s,'p','k','ekstern_lesing','kompenserende')",
                (modul, kh))
            migrator.execute("RESET ROLE")

            # VRIDNINGEN: oppdraget gjøres om til et BESLUTNINGSoppdrag —
            # `unntak_id` NULL — og saken peker tilbake på det i stedet.
            # Samme konstruksjon som port 2 i test_gate14b.
            migrator.execute("ALTER TABLE oppdrag DISABLE TRIGGER USER")
            migrator.execute(
                "UPDATE oppdrag SET modul_id=%s, kontraktversjon=1,"
                " kontrakt_hash=%s, unntak_id=NULL, opprinnelse='beslutning',"
                " repair_operation_id=NULL, loggpost_id=NULL"
                " WHERE tenant=%s AND id=%s", (modul, kh, TENANT, opp))
            migrator.execute("ALTER TABLE oppdrag ENABLE TRIGGER USER")
            migrator.execute("ALTER TABLE unntak DISABLE TRIGGER USER")
            migrator.execute(
                "UPDATE unntak SET oppdrag_id=%s, arsak='evidensfrist',"
                " sakskilde='oppdrag' WHERE tenant=%s AND id=%s",
                (opp, TENANT, sak_b))
            migrator.execute("SET CONSTRAINTS ALL IMMEDIATE")
            migrator.execute("ALTER TABLE unntak ENABLE TRIGGER USER")
            migrator.commit()

            # (1) Nei-ets FØRSTE lås: saken.
            holder = koble(MIGRATOR_DSN)
            _sett_kontekst(holder, TENANT, "menneske", "r-avvis-b")
            holder.execute("SELECT 1 FROM unntak WHERE tenant=%s AND id=%s"
                           "   FOR UPDATE", (TENANT, sak_b))

            # (2) Kvitteringen postes og skal blokkere PÅ SAKEN — den som
            #     bare finnes gjennom `unntak.oppdrag_id`.
            kropp = _signer_kvittering({
                "oppdrag_id": opp, "tenant": TENANT,
                "kvittering_jti": a["kvittering_jti"],
                "repair_operation_id": a["repair_operation_id"],
                "owner_claim_id": a["owner_claim_id"],
                "owner_generation": a["owner_generation"],
                "resultat": "utfort", "ressurs_id": "fak-1"})
            svar = {}

            def kvitter():
                svar["r"] = c.post("/v1/oppdrag/kvittering", json=kropp,
                                   headers=h)

            t = threading.Thread(target=kvitter)
            t.start()
            time.sleep(1.0)
            assert "r" not in svar, (
                "kvitteringen gikk forbi sakslåsen — beslutningsopphavet"
                " (`unntak.oppdrag_id`) er ikke dekket")

            # (3) Nei-et kjører den EKTE oppløsningsveien og committer.
            _attester_avvis(holder, TENANT, sak_b, "menneske")
            holder.execute("SET ROLE disponit_m37_claimer")
            res = holder.execute(
                "SELECT utfall FROM avvis_med_opplosning(%s,%s,%s,"
                "'menneske','r-avvis-b')", (TENANT, sak_b, [opp])).fetchall()
            holder.execute("RESET ROLE")
            assert res == [("kansellert",)], res
            holder.commit(); holder.close(); holder = None

            # (4) MÅLINGEN.
            t.join(timeout=30)
            assert not t.is_alive(), "kvitteringen ble aldri sluppet fri"
            r = svar["r"]
            assert r.status_code == 202, (
                "kvitteringen regnet på tilstanden fra FØR sakslåsen og"
                f" mistet seg selv: {r.status_code} {r.text}")
            assert r.json()["status"] == "lagret_uten_statusendring", r.text
    finally:
        if holder is not None:
            holder.rollback(); holder.close()
        app.tjeneste.pool.lukk()

    _sett_kontekst(migrator, TENANT)
    hendelser = dict(migrator.execute(
        "SELECT hendelse, count(*) FROM unntak_historikk"
        " WHERE tenant=%s AND unntak_id=%s GROUP BY hendelse",
        (TENANT, sak_b)).fetchall())
    oppdragsrad = migrator.execute(
        "SELECT status, kansellert_aarsak FROM oppdrag WHERE tenant=%s"
        " AND id=%s", (TENANT, opp)).fetchone()
    migrator.execute("SET ROLE disponit_m37_claimer")
    kap = migrator.execute(
        "SELECT status, resultathash IS NOT NULL FROM"
        " kvitteringskapabiliteter WHERE jti=%s",
        (a["kvittering_jti"],)).fetchone()
    migrator.execute("RESET ROLE")
    kompensasjon = migrator.execute(
        "SELECT count(*) FROM unntak WHERE tenant=%s AND oppdrag_id=%s"
        " AND arsak='kompensasjon_kreves'", (TENANT, opp)).fetchone()[0]
    migrator.rollback()

    assert hendelser.get("sen_kvittering") == 1, (
        f"den sene kvitteringen nådde aldri evidensgrenen: {hendelser}")
    assert oppdragsrad == ("kansellert", "menneskelig_avvis"), oppdragsrad
    assert kap == ("avvist", True), (
        f"kapabiliteten skulle stått avvist MED sen hash: {kap}")
    assert kompensasjon == 1, "kompensasjonssaken ble aldri født"


@pg
def test_P1_nei_et_foder_ingen_falsk_evidensfristsak(migrator, miljo, token):
    """043 (Gate 14b), Codex P1 runde 7: saken som SA NEI eier evidensen.

    For et BESLUTNINGSoppdrag er `unntak_id` NULL med vilje — saken peker
    tilbake gjennom `unntak.oppdrag_id`. Sen-evidensveien falt derfor rett
    ned i `sikre_sak_for_oppdrag(... 'evidensfrist' ...)`, og den ga ikke
    saken tilbake: mennesket har nettopp satt den `avvist`, altså TERMINAL,
    og gjenbruksveien (038) tar aldri en terminal sak.

    Resultatet var en helt ny, ÅPEN evidensfrist-sak — en påstand om at
    fristen løp ut, for en kvittering som kom i TIDE — og for
    `kompenserende` deretter enda en sak ved siden av. En operatør som
    nettopp har sagt nei fikk altså to nye saker, hvorav den ene lyver om
    hvorfor den finnes.

    Målingen trenger ingen tråder: nei-et committer FØRST (ekte
    `avvis_med_opplosning` + saken satt `avvist`), og kvitteringen kommer
    etterpå — men fortsatt innenfor evidensfristen.

    MUTASJONEN SOM DREPER DENNE: fjern `menneskelig_nei`-grenen i
    sen-evidensveien, så `sikre_sak_for_oppdrag(...'evidensfrist')` igjen
    er første utvei når `unntak_id` er NULL.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app

    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    sak_b, _ = _lag_sak(migrator, TENANT)

    app = lag_app(DSN)
    try:
        with TestClient(app) as c:
            tok, _ = token(rolle="eiermodul:reinnsending",
                           scopes=("orders:execute:purring.",))
            h = {"authorization": f"Bearer {tok}"}
            a = c.post("/v1/oppdrag/claim", json={}, headers=h).json()
            assert a["oppdrag_id"] == opp, a

            modul = "m-" + secrets.token_hex(4)
            kh = secrets.token_hex(16)
            _sett_kontekst(migrator, TENANT)
            migrator.execute("SET ROLE disponit_modul_eier")
            migrator.execute(
                "INSERT INTO modulkontrakt (modul_id, kontraktversjon,"
                " kontrakt_hash, payload_schema_hash, kvittering_schema_hash,"
                " sideeffektklasse, reversibilitet)"
                " VALUES (%s,1,%s,'p','k','ekstern_lesing','kompenserende')",
                (modul, kh))
            migrator.execute("RESET ROLE")
            migrator.execute("ALTER TABLE oppdrag DISABLE TRIGGER USER")
            migrator.execute(
                "UPDATE oppdrag SET modul_id=%s, kontraktversjon=1,"
                " kontrakt_hash=%s, unntak_id=NULL, opprinnelse='beslutning',"
                " repair_operation_id=NULL, loggpost_id=NULL"
                " WHERE tenant=%s AND id=%s", (modul, kh, TENANT, opp))
            migrator.execute("ALTER TABLE oppdrag ENABLE TRIGGER USER")
            migrator.execute("ALTER TABLE unntak DISABLE TRIGGER USER")
            migrator.execute(
                "UPDATE unntak SET oppdrag_id=%s, arsak='evidensfrist',"
                " sakskilde='oppdrag' WHERE tenant=%s AND id=%s",
                (opp, TENANT, sak_b))
            migrator.execute("SET CONSTRAINTS ALL IMMEDIATE")
            migrator.execute("ALTER TABLE unntak ENABLE TRIGGER USER")
            migrator.commit()

            # NEI-ET, HELE VEIEN: oppløsningen kjøres, og saken settes
            # `avvist` — altså terminal, slik operatørveien gjør det.
            cid = secrets.token_hex(16)
            _sett_kontekst(migrator, TENANT, "menneske", "r-nei")
            migrator.execute(
                "UPDATE unntak SET status='under_behandling', claim_id=%s,"
                " claim_generation=1, claim_utloper=now()+interval '600 s'"
                " WHERE tenant=%s AND id=%s", (cid, TENANT, sak_b))
            _attester_avvis(migrator, TENANT, sak_b, "menneske")
            migrator.execute("SET ROLE disponit_m37_claimer")
            res = migrator.execute(
                "SELECT utfall FROM avvis_med_opplosning(%s,%s,%s,"
                "'menneske','r-nei')", (TENANT, sak_b, [opp])).fetchall()
            migrator.execute("RESET ROLE")
            assert res == [("kansellert",)], res
            migrator.execute("UPDATE unntak SET status='avvist'"
                             " WHERE tenant=%s AND id=%s", (TENANT, sak_b))
            migrator.commit()

            # ... og SÅ kommer den sene kvitteringen — i god tid før
            # evidensfristen.
            kropp = _signer_kvittering({
                "oppdrag_id": opp, "tenant": TENANT,
                "kvittering_jti": a["kvittering_jti"],
                "repair_operation_id": a["repair_operation_id"],
                "owner_claim_id": a["owner_claim_id"],
                "owner_generation": a["owner_generation"],
                "resultat": "utfort", "ressurs_id": "fak-1"})
            r = c.post("/v1/oppdrag/kvittering", json=kropp, headers=h)
            assert r.status_code == 202, r.text
            assert r.json()["status"] == "lagret_uten_statusendring", r.text
    finally:
        app.tjeneste.pool.lukk()

    _sett_kontekst(migrator, TENANT)
    hendelser = dict(migrator.execute(
        "SELECT hendelse, count(*) FROM unntak_historikk"
        " WHERE tenant=%s AND unntak_id=%s GROUP BY hendelse",
        (TENANT, sak_b)).fetchall())
    saker = dict(migrator.execute(
        "SELECT arsak, count(*) FROM unntak WHERE tenant=%s AND oppdrag_id=%s"
        " GROUP BY arsak", (TENANT, opp)).fetchall())
    migrator.rollback()

    assert hendelser.get("sen_kvittering") == 1, (
        "den sene evidensen ble ikke ført på saken mennesket avgjorde:"
        f" {hendelser}")
    # ÉN evidensfrist-sak: den som alt fantes. Ingen ny, åpen påstand om en
    # frist som aldri løp ut.
    assert saker.get("evidensfrist") == 1, (
        f"nei-et fødte en falsk evidensfrist-sak: {saker}")
    assert saker.get("kompensasjon_kreves") == 1, (
        f"kompensasjonssaken uteble eller ble doblet: {saker}")


@pg
def test_P1_sakslaskoen_tar_ikke_kapabilitetens_frist(migrator, miljo, token):
    """043 (Gate 14b), Codex P2 runde 5: køen skal ikke koste kvitteringen
    fristen dens.

    Innvendingen: `innlos_kvitteringskapabilitet` filtrerer på
    `k.utloper > now()`, så en kvittering som ankom i tide, men sto i
    sakslåskø forbi utløpet, skulle miste kapabiliteten i den NYE lesningen
    etter låsen og få `kapabilitet_ugyldig` — stikk i strid med at `naa`
    (ankomsttiden) bevisst ikke friskes opp.

    Egenskapen er allerede der, og den er ikke en tilfeldighet: `now()` er
    transaksjonstidsstempelet, ikke veggklokka, og hele ingesten kjører i
    ÉN transaksjon (`preauth` lukker sin egen). Låskøen kan derfor ikke
    flytte utløpsgrensen. Men en egenskap ingen måler, er en egenskap som
    kan forsvinne — bytter noen `now()` mot `statement_timestamp()`, eller
    splitter ingesten i to transaksjoner, er tapet nøyaktig det Codex
    beskriver, og helt stille.

    Målingen er derfor deterministisk: oppdragets evidensfrist — som ER
    kapabilitetens `utloper` (035) — er 8 sekunder, og sakslåsen holdes i 9.
    Utløpet er altså passert på VEGGKLOKKA når kvitteringen slippes fri,
    mens den ankom godt innenfor. Den skal likevel avslutte oppdraget.

    MUTASJONEN SOM DREPER DENNE: bytt `k.utloper > pg_catalog.now()` i
    `innlos_kvitteringskapabilitet` (035) mot `statement_timestamp()`.
    """
    import threading

    from starlette.testclient import TestClient
    from api.app import lag_app
    from db.pg import koble

    # KORTE FRISTER FRA FØDSELEN AV, ikke etterpå: kapabilitetens `utloper`
    # ER oppdragets evidensfrist (`utsted_kvitteringskapabilitet`, 035), og
    # bindingsfeltene på kapabiliteten er uforanderlige — som de skal være.
    # Fristene settes derfor der de hører hjemme, på oppdraget.
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg,
                          utforelsesfrist="4 seconds",
                          evidensfrist="8 seconds")
    cid_sak = secrets.token_hex(16)
    _sett_kontekst(migrator, TENANT, "m37-arbeider", cid_sak)
    migrator.execute(
        "UPDATE unntak SET status='under_behandling', claim_id=%s,"
        " claim_generation=1, claim_utloper=now()+interval '600 s'"
        " WHERE tenant=%s AND id=%s", (cid_sak, TENANT, sak))
    migrator.execute("UPDATE unntak SET status='venter_utførelse'"
                     " WHERE tenant=%s AND id=%s", (TENANT, sak))
    migrator.commit()

    app = lag_app(DSN)
    holder = None
    try:
        with TestClient(app) as c:
            tok, _ = token(rolle="eiermodul:reinnsending",
                           scopes=("orders:execute:purring.",))
            h = {"authorization": f"Bearer {tok}"}
            a = c.post("/v1/oppdrag/claim", json={}, headers=h).json()
            assert a["oppdrag_id"] == opp, a

            # (1) Sakslåsen tas FØRST og holdes — som et menneskelig nei
            #     ville holdt den gjennom hele operatørhandlingen.
            holder = koble(MIGRATOR_DSN)
            _sett_kontekst(holder, TENANT, "menneske", "r-frist")
            holder.execute("SELECT 1 FROM unntak WHERE tenant=%s AND id=%s"
                           "   FOR UPDATE", (TENANT, sak))

            # (2) Kvitteringen ankommer godt innenfor BEGGE fristene.
            kropp = _signer_kvittering({
                "oppdrag_id": opp, "tenant": TENANT,
                "kvittering_jti": a["kvittering_jti"],
                "repair_operation_id": a["repair_operation_id"],
                "owner_claim_id": a["owner_claim_id"],
                "owner_generation": a["owner_generation"],
                "resultat": "utfort", "ressurs_id": "fak-1"})
            svar = {}

            def kvitter():
                svar["r"] = c.post("/v1/oppdrag/kvittering", json=kropp,
                                   headers=h)

            t = threading.Thread(target=kvitter)
            t.start()
            time.sleep(1.0)
            assert "r" not in svar, "kvitteringen gikk forbi sakslåsen"

            # (3) Køen holdes forbi kapabilitetens utløp — på veggklokka.
            time.sleep(8.0)
            holder.rollback(); holder.close(); holder = None

            # (4) MÅLINGEN: kvitteringen ankom i tide, og køen er ikke dens
            #     skyld. Den skal fortsatt avslutte oppdraget.
            t.join(timeout=30)
            assert not t.is_alive(), "kvitteringen ble aldri sluppet fri"
            r = svar["r"]
            assert r.status_code == 200, (
                "kvitteringen mistet kapabilitetens frist mens den sto i"
                f" sakslåskø: {r.status_code} {r.text}")
            assert r.json()["status"] == "utfort", r.text
    finally:
        if holder is not None:
            holder.rollback(); holder.close()
        app.tjeneste.pool.lukk()

    _sett_kontekst(migrator, TENANT)
    status = migrator.execute(
        "SELECT o.status, u.status FROM oppdrag o JOIN unntak u"
        "   ON u.tenant=o.tenant AND u.id=o.unntak_id"
        " WHERE o.tenant=%s AND o.id=%s", (TENANT, opp)).fetchone()
    migrator.rollback()
    assert status == ("utfort", "løst"), status


@pg
def test_P1_forbrukets_fire_utfall_er_uttommende(migrator, miljo, token):
    """`brukt | idempotent | konflikt | ugyldig` — alle fire nås.

    Uten denne kunne `ugyldig`-grenen vært død kode, og fail-closed-veien
    ville aldri vært prøvd. En gren uten vitne er ikke en gren med dekning.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app

    app = lag_app(DSN)
    try:
        with TestClient(app) as c:
            modul = _unik_eiermodul()
            tok, _ = token(rolle=modul,
                           scopes=("orders:execute:purring.",))
            h = {"authorization": f"Bearer {tok}"}
            opp, sak, a, b = _oppsett_stale_kapabilitet(migrator, c, h, modul)
            jti = a["kvittering_jti"]

            assert _rt("SELECT bruk_kvitteringskapabilitet(%s,%s)",
                       (jti, "a" * 64))[0] == "brukt"
            assert _rt("SELECT bruk_kvitteringskapabilitet(%s,%s)",
                       (jti, "a" * 64))[0] == "idempotent"
            assert _rt("SELECT bruk_kvitteringskapabilitet(%s,%s)",
                       (jti, "b" * 64))[0] == "konflikt"
            assert _rt("SELECT bruk_kvitteringskapabilitet(%s,%s)",
                       (secrets.token_hex(16), "a" * 64))[0] == "ugyldig"

            # Og `feilet` er også ugyldig — ikke konflikt.
            jti_b = b["kvittering_jti"]
            migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
            migrator.execute("UPDATE kvitteringskapabiliteter SET"
                             " status='feilet' WHERE jti=%s", (jti_b,))
            migrator.execute("RESET ROLE")
            migrator.commit()
            assert _rt("SELECT bruk_kvitteringskapabilitet(%s,%s)",
                       (jti_b, "c" * 64))[0] == "ugyldig"
    finally:
        app.tjeneste.pool.lukk()


# ===========================================================================
# Post-merge P1: revisjonslogg.policy_id er en REFERANSE, ikke en policy-id
# ===========================================================================

def test_policyref_rundtur_bygger_og_leser_samme_format():
    """`_pid` bygger, `les_policyref` leser. De må aldri gli fra hverandre.

    Skjemaet gjør formatet entydig: `policy_id` er `^[a-z0-9-]+$` og
    `versjon` er `^\\d+\\.\\d+\\.\\d+$`, så verken `@` eller `/` kan
    forekomme i dem. Handlingen kan inneholde begge deler uten at det gjør
    noe — og det er nettopp derfor parsingen er trygg.
    """
    from policy_validator.engine import _pid, les_policyref
    pol = {"meta": {"policy_id": "tjenestebedrift-no", "versjon": "0.2.0"}}
    # Handlingene her er SKJEMAGYLDIGE (`^[a-z0-9_]+(\.[a-z0-9_]+)+$`).
    # Etter innstrammingen er det nettopp poenget: rundturen skal holde for
    # alt `_pid` faktisk kan produsere fra en gyldig policy, og ikke for
    # noe annet.
    for handling in ("purring.send", "faktura.krediter", "a_b.c_d",
                     "x.y.z"):
        assert les_policyref(_pid(pol, handling)) == ("tjenestebedrift-no",
                                                      "0.2.0"), handling
    # Fail-closed: aldri en gjetning når formen ikke stemmer.
    for ugyldig in (
            "tjenestebedrift-no", "", None, 12,
            "STORE@0.2.0/purring.send",       # policy_id har store bokstaver
            "a@0.2/purring.send",             # versjon er ikke x.y.z
            # AVKORTEDE former — Codex' P1 på hotfixen. `_pid` kan aldri
            # produsere dem, så å godta dem er å stole på en identitet som
            # ikke finnes. Parseren godtok begge før.
            "a@1.2.3",                        # ingen skråstrek
            "a@1.2.3/",                       # tom handling
            "a@1.2.3/purring.send/noe",       # handling med skråstrek
            "a@1.2.3/UKJENT",                 # handling med store bokstaver
            "a@1.2.3/ukjent",                 # handling uten punktum
            "a@1.2.3/.send",                  # handling starter med punktum
            "a1.2.3/purring.send",            # ingen krøllalfa
    ):
        assert les_policyref(ugyldig) is None, ugyldig


def test_policyref_monstre_speiler_policyskjemaet():
    """Parserens mønstre er KOPIER av skjemaets. De må ikke gli fra hverandre.

    Uten denne kunne skjemaet utvidet `handling.id` — f.eks. med bindestrek
    — og parseren ville avvist helt lovlige referanser som «korrupt
    evidens», altså sendt gyldige saker til manuell kø i stillhet.
    """
    import json
    from policy_validator import engine
    skjema = json.loads(
        (POLICIES.parent / "policies" / "policy-schema-v0.2.json").read_text(
            encoding="utf-8"))
    meta = skjema["properties"]["meta"]["properties"]
    handling = skjema["$defs"]["handling"]["properties"]["id"]
    for monster, fasit, navn in (
            (engine._POLICY_ID_MONSTER, meta["policy_id"]["pattern"], "policy_id"),
            (engine._VERSJON_MONSTER, meta["versjon"]["pattern"], "versjon"),
            (engine._HANDLING_MONSTER, handling["pattern"], "handling.id")):
        assert monster.pattern == fasit.strip("^$"), (
            f"{navn}: parseren har {monster.pattern!r}, skjemaet har"
            f" {fasit!r} — de har glidd fra hverandre")


@pg
def test_migrator_naar_ikke_kapabilitetene_uten_set_role(migrator):
    """Rettighetsmodellen står: migrator har INGEN direkte vei til
    kapabilitetstabellene.

    Første forsøk på å gjøre testsuiten hermetisk ga migrator varig
    `SELECT, DELETE` gjennom en migrasjon. Det ville lagt en direkte,
    destruktiv datapassasje i alle kundebaser for å løse fixture-isolasjon.
    Oppryddingen skjer nå under eksplisitt `SET LOCAL ROLE` i
    testoppsettet, og DENNE testen beviser at rettigheten ikke ble igjen.

    MUTASJONEN SOM DREPER DENNE: legg GRANT-ene tilbake i en migrasjon.
    """
    import psycopg
    for tabell in ("arbeidskapabiliteter", "kvitteringskapabiliteter"):
        for sql in (f"SELECT count(*) FROM {tabell}",
                    f"DELETE FROM {tabell} WHERE tenant='x'"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                migrator.execute(sql)
            migrator.rollback()

    # Og motstykket: MED SET ROLE virker det — ellers ville testen bestått
    # selv om tabellene var utilgjengelige for alle, og fixturen ødelagt.
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    migrator.execute("SELECT count(*) FROM arbeidskapabiliteter").fetchone()
    migrator.execute("RESET ROLE")
    migrator.rollback()


@pg
def test_arbeideren_finner_policyen_paa_produksjonsformet_loggpost(migrator):
    """Regresjonstesten for P1-et som overlevde 341 grønne tester.

    Saken her er skrevet slik API-VEIEN faktisk skriver den — med
    `<policy_id>@<versjon>/<handling>` i `revisjonslogg.policy_id`. Før
    fiksen ga `_aktiv_policy` None, og arbeideren klassifiserte saken som
    `manuell` med `aktiv_policy_utilgjengelig`. M-37 behandlet altså
    INGENTING på ekte data, og ingen test merket det — fordi fixturene
    skrev en bar `'p'` i den kolonnen.

    MUTASJONEN SOM DREPER DENNE: la `_aktiv_policy` bruke `rad[0]` direkte
    i stedet for `les_policyref(rad[0])[0]`.
    """
    import yaml
    from api.policyregister import innholds_hash, registrer
    from db.pg import koble
    from m37 import arbeider

    pol = yaml.safe_load(
        (POLICIES / "bransjemal-tjenestebedrift.yaml").read_text(
            encoding="utf-8"))
    _sett_kontekst(migrator, TENANT)
    registrer(migrator, TENANT, pol, pol["meta"]["status"])
    migrator.commit()

    sak, _ = _lag_sak(migrator, TENANT, hash_=innholds_hash(pol),
                      policy_id=pol["meta"]["policy_id"],
                      versjon=pol["meta"]["versjon"])

    rt = koble(DSN)
    try:
        cid = arbeider._claim_id()
        s = arbeider.claim(rt, cid)
        assert s is not None and s.id == sak
        plan, _ = arbeider.planlegg(rt, s, cid)
        assert plan.grunn != "aktiv_policy_utilgjengelig", (
            "arbeideren fant ikke den aktive policyen på en"
            " produksjonsformet loggpost — M-37 ville klassifisert HVER sak"
            " som manuell")
        # Etter PR-007 bestiller R1 VERIFIKASJON, ikke re-innsending: en ny
        # beslutning kan ikke bli TILLAT før det manglende beviset finnes.
        # Form A: fase 1 dekker HELE settet av påkrevde vilkår i én
        # generasjon — ikke bare det som manglet. Fase 2 kan bare bevise
        # det fase 1 har verifisert, siden originalens attestasjoner er
        # minimert bort.
        assert plan.utfall == "verifikasjon", f"{plan.utfall}: {plan.grunn}"
        assert sorted(plan.reparasjonsinput["vilkaar_sett"]) == [
            "forfall_passert_dager", "ingen_aktiv_tvist"]
        assert plan.valgt_verifikator, "ingen verifikator valgt"
        assert plan.krav_sett_hash and plan.autoritetsversjon
    finally:
        rt.close()


@pg
def test_backfill_finner_evidens_paa_produksjonsformet_loggpost(migrator,
                                                                malpolicy):
    """Samme rotårsak, andre kallsted.

    På staging ga den 0 av 4200 fra evidens — samtlige rader ble legacy +
    manuell, og snapshotkolonnene er kolonnelåste etterpå. En feil som
    degraderer data permanent, i stillhet.
    """
    import copy
    from api.policyregister import innholds_hash
    from db import m37_backfill

    ekte = copy.deepcopy(malpolicy)
    ekte["meta"] = dict(ekte["meta"], policy_id="bfref", versjon="3.0.0")
    h = innholds_hash(ekte)
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "INSERT INTO policyer (tenant, policy_id, versjon, innholds_hash,"
        " status, innhold, aktiv) VALUES (%s,'bfref','3.0.0',%s,%s,%s,false)"
        " ON CONFLICT DO NOTHING",
        (TENANT, h, ekte["meta"]["status"], json.dumps(ekte)))
    migrator.commit()

    sak, _ = _lag_sak(migrator, TENANT, hash_=h, policy_id="bfref",
                      versjon="3.0.0")
    migrator.execute("ALTER TABLE unntak ALTER COLUMN"
                     " maks_auto_forsok_snapshot DROP NOT NULL,"
                     " ALTER COLUMN policy_versjon DROP NOT NULL,"
                     " ALTER COLUMN policy_content_hash DROP NOT NULL")
    # 041: totalitets-CHECKen krever trioen for policybrudd — pre-006-
    # tilstanden fixturen gjenskaper er nettopp trio=NULL, så sperren
    # løftes eksplisitt og settes tilbake nederst, som NOT NULL-ene.
    migrator.execute(
        "ALTER TABLE unntak DROP CONSTRAINT unntak_snapshot_komplett")
    migrator.execute("ALTER TABLE unntak DISABLE TRIGGER unntak_laas")
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "UPDATE unntak SET maks_auto_forsok_snapshot=NULL, policy_versjon=NULL,"
        " policy_content_hash=NULL WHERE tenant=%s AND id=%s", (TENANT, sak))
    # 041: unntak har utsatte constraint-triggere (lineage/loggpost) — de må
    # fyre FØR en ALTER TABLE i samme transaksjon («pending trigger events»),
    # samme håndgrep som _rydd bruker.
    migrator.execute("SET CONSTRAINTS ALL IMMEDIATE")
    migrator.execute("ALTER TABLE unntak ENABLE TRIGGER unntak_laas")
    migrator.commit()

    res = m37_backfill.backfill(migrator)
    # 041 gjorde trioen nullable (overtakelsessaker HAR NULL-trio) — å sette
    # NOT NULL tilbake ville gjeninnført pre-041-skjemaet og felt enhver
    # senere overtakelsestest. Beviset for at backfillen fylte radene bæres
    # nå av CHECK-en alene: ADD CONSTRAINT validerer HELE tabellen, så en
    # policybrudd-rad backfillen hoppet over ville felt nettopp denne linjen.
    migrator.execute(
        "ALTER TABLE unntak ADD CONSTRAINT unntak_snapshot_komplett CHECK ("
        " (sakskilde = 'domeneovertakelse'"
        "    AND maks_auto_forsok_snapshot IS NULL"
        "    AND policy_versjon IS NULL AND policy_content_hash IS NULL)"
        " OR (sakskilde <> 'domeneovertakelse'"
        "    AND maks_auto_forsok_snapshot IS NOT NULL"
        "    AND policy_versjon IS NOT NULL"
        "    AND policy_content_hash IS NOT NULL))")
    migrator.commit()

    assert res.fra_evidens >= 1, (
        f"backfillen fant ingen evidens på en produksjonsformet loggpost: {res}")
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT policy_versjon, status FROM unntak WHERE tenant=%s AND id=%s",
        (TENANT, sak)).fetchone()
    migrator.rollback()
    assert rad == ("3.0.0", "ny"), rad


@pg
def test_policy_id_setter_sin_egen_tenantkontekst(migrator):
    """P1 funnet ved å kjøre HELE kjeden som tre prosesser.

    `_policy_id` kalles ETTER at `planlegg()` har committet, og `SET LOCAL`
    forsvinner ved commit. Uten egen kontekst så row level security null
    rader, funksjonen ga `''`, og API-et svarte 404 `policy_ukjent` på HVER
    reparasjon — outbox-veien var uoppnåelig i produksjon.

    Testen kaller den på en tilkobling som BEVISST mangler kontekst. Det er
    hele poenget: enhetstestene så aldri feilen fordi de arbeider på en
    tilkobling der konteksten alt er satt av noe annet.

    MUTASJONEN SOM DREPER DENNE: fjern `sett_kontekst(...)` fra `_policy_id`.
    """
    from db.pg import koble
    from m37 import arbeider

    sak_id, logg = _lag_sak(migrator, TENANT)
    sak = arbeider.Sak(TENANT, sak_id, "purring.send", "manglende_data",
                       logg, 1, None, 1, 3)

    rt = koble(DSN)
    try:
        # Ingen `disponit.tenant` her — nøyaktig som etter en commit.
        assert rt.execute(
            "SELECT count(*) FROM revisjonslogg").fetchone()[0] == 0, \
            "tilkoblingen har kontekst; da måler testen ikke det den skal"
        assert arbeider._policy_id(rt, sak) == FIXTURE_POLICY_ID, (
            "_policy_id fant ikke policyen på en tilkobling uten kontekst —"
            " hver reparasjon ville fått 404 policy_ukjent")
    finally:
        rt.close()
