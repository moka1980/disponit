"""PR-014c: skjemalageret, skjemavalideringen og målautorisasjonsregisteret.

Migrasjon 036 lukker CP5-hullet fra 014b: `skjema_hash` var en påstand
ingen kunne slå opp. Her prøves lageret (innholdsadressert, immutabelt for
alltid), den positive regelen i `registrer_artefakttype`, valideringen ved
OPPLASTING og ved PROMOTERING, sideeffektklassen `ekstern_lesing` og
`malautorisasjonsvilkar`-registeret.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
import hashlib
import json
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT, app, klient,  # noqa: F401
                       dekker, migrator, miljo, token)          # noqa: F401
from .test_m37 import _sett_kontekst
from .test_pr014b_artefakt_api import (_kvitteringskap, _kvitteringskropp,
                                       _oppdrag_owner, _post, _utsted_cap)
from .test_pr014b_artefaktkapabilitet import _plukket_oppdrag_med_binding

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _jcs_hash(skjema: dict) -> tuple[str, str]:
    from policy_validator import jcs
    kanon = jcs.kanoniske_bytes(skjema)
    return kanon.decode("utf-8"), hashlib.sha256(kanon).hexdigest()


#: Et strengt skjema: nøyaktig ett felt, lukket.
STRENGT = {"type": "object", "additionalProperties": False,
           "required": ["resultat"],
           "properties": {"resultat": {"enum": ["ok", "feil"]}}}


def _mk_admin(rolle):
    """migrator SET ROLE <rolle>, committed (varig på tvers av rollback) —
    speiler hjelperen i test_pr014b_artefaktkapabilitet."""
    from db.pg import koble
    c = koble(MIGRATOR_DSN)
    c.execute(f"SET ROLE {rolle}")
    c.commit()
    return c


def _registrer_skjema(skjema: dict) -> str:
    kanon, h = _jcs_hash(skjema)
    c = _mk_admin("disponit_modules_admin")
    try:
        c.execute("SELECT registrer_artefaktskjema(%s,%s,'test')", (kanon, h))
        c.commit()
    finally:
        c.close()
    return h


def _streng_type(migrator_, modul, kh, *, skjema=None) -> str:
    """Registrer en artefakttype bundet til det strenge skjemaet, under en
    kontrakt som alt finnes (fixturen fra 014b lager kontrakten)."""
    h = _registrer_skjema(skjema or STRENGT)
    at = f"kontroll.t{secrets.token_hex(4)}.rapport"
    da = _mk_admin("disponit_domains_admin")
    try:
        da.execute("SELECT registrer_artefakttype(%s,%s,1,%s,%s,'test')",
                   (at, modul, kh, h))
        da.commit()
    finally:
        da.close()
    return at


# --------------------------------------------------------------------------
# Lageret (portene 15–17, 26–28)
# --------------------------------------------------------------------------

@pg
def test_skjemaregistrering_rekalkulerer_hashen(migrator):
    """Port 16: oppgitt hash må matche innholdet — funksjonen regner selv."""
    kanon, h = _jcs_hash({"type": "object", "x": secrets.token_hex(3)})
    c = _mk_admin("disponit_modules_admin")
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute("SELECT registrer_artefaktskjema(%s,%s,'test')",
                      (kanon, "0" * 64))
        c.rollback()
        assert c.execute("SELECT registrer_artefaktskjema(%s,%s,'test')",
                         (kanon, h)).fetchone()[0] == h
        c.execute("SELECT registrer_artefaktskjema(%s,%s,'test')",
                  (kanon, h))                        # idempotent
        c.commit()
    finally:
        c.close()


@pg
def test_skjemaadressen_er_bytene_som_lagres(migrator):
    """Codex P2: hashen var over bytene, raden bar en jsonb-KOPI av dem.

    `::jsonb` er en normalisert representasjon, ikke de bytene: den
    kaster uvesentlig blanktegn og normaliserer nøkler og tall. Adressen
    kunne derfor ikke regnes ut på nytt fra innholdet den adresserer, og
    to semantisk like skjemaer kunne få hver sin adresse. Raden bærer nå
    `kanonisk`, `skjema` utledes av den, og databasen sjekker adressen
    selv.

    Kontroll: fjern `kanonisk` og sett inn `p_kanonisk::jsonb` i `skjema`
    igjen, så blir første blokk rød (adressen lar seg ikke gjenskape fra
    raden), og fjern blanktegnvakten i `registrer_artefaktskjema`, så blir
    siste blokk rød (pretty-printet JSON slipper inn).
    """
    kanon, h = _jcs_hash({"type": "object", "z": secrets.token_hex(3)})
    _registrer_skjema(json.loads(kanon))

    # Adressen er etterprøvbar FRA RADEN: sha256 over de lagrede bytene.
    rad = migrator.execute(
        "SELECT kanonisk, skjema,"
        "       encode(sha256(convert_to(kanonisk,'UTF8')),'hex')"
        "  FROM artefaktskjema WHERE skjema_hash=%s", (h,)).fetchone()
    assert rad[0] == kanon and rad[2] == h
    # ... og den utledede kolonnen er fortsatt den samme verdien.
    assert rad[1] == json.loads(kanon)

    # Et oppgitt `skjema` som er uenig med bytene avvises — det blir ikke
    # stille skrevet om, og de to kan derfor ikke gli fra hverandre.
    tom = "{}"
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        migrator.execute(
            "INSERT INTO artefaktskjema (skjema_hash, kanonisk, skjema)"
            " VALUES (%s,%s,'{\"type\":\"object\"}'::jsonb)",
            (hashlib.sha256(tom.encode()).hexdigest(), tom))
    migrator.rollback()

    # En rad der hashen ikke er over de lagrede bytene finnes ikke, selv
    # om tabelleieren går utenom funksjonen.
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        migrator.execute(
            "INSERT INTO artefaktskjema (skjema_hash, kanonisk)"
            " VALUES (%s,%s)", ("b" * 64, '{"type":"object"}'))
    migrator.rollback()

    # Den grovt ukanoniske inngangen avvises av funksjonen: JCS-utdata
    # har ikke blanktegn utenfor strenger. Blanktegn INNE i en streng er
    # innhold og skal fortsatt slippe gjennom.
    pen = '{"type": "object"}'
    ph = hashlib.sha256(pen.encode()).hexdigest()
    c = _mk_admin("disponit_modules_admin")
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute("SELECT registrer_artefaktskjema(%s,%s,'test')",
                      (pen, ph))
        c.rollback()
        med_rom = json.dumps({"type": "object",
                              "title": "to ord " + secrets.token_hex(3)},
                             separators=(",", ":"), sort_keys=True)
        c.execute("SELECT registrer_artefaktskjema(%s,%s,'test')",
                  (med_rom, hashlib.sha256(med_rom.encode()).hexdigest()))
        c.commit()
    finally:
        c.close()


@pg
def test_skjemalageret_er_immutabelt_for_alltid(migrator):
    """Portene 17, 26–28: UPDATE og DELETE avvises ALLTID — også for en rad
    ingen artefakttype refererer, og også for migrator (tabelleieren).
    Kontroll: fjern `artefaktskjema_immutable`-triggeren i 036, så blir
    denne rød."""
    kanon, h = _jcs_hash({"type": "object", "u": secrets.token_hex(3)})
    _registrer_skjema(json.loads(kanon))
    # `skjema` utledes av `kanonisk` ved innsetting
    # (`test_skjemaadressen_er_bytene_som_lagres` fester det), så
    # innholdsveien inn hit er `kanonisk` — og den skal triggeren ta.
    for sql in [
        "UPDATE artefaktskjema SET kanonisk='{}' WHERE skjema_hash=%s",
        "UPDATE artefaktskjema SET skjema_hash=%s WHERE skjema_hash=%s",
        "DELETE FROM artefaktskjema WHERE skjema_hash=%s",
    ]:
        params = ((h,) if sql.count("%s") == 1
                  else ("f" * 64, h))
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(sql, params)
        migrator.rollback()


def _deployport():
    """Deploy-porten (`deploy/staging/deployport-modultyper.py`) lastet som
    modul.

    Den er et skript uten pakke, så den lastes fra filsti — og fra filstien
    `opp.sh` faktisk kjører, ikke fra en kopi: en avskrift ville stått grønn
    den dagen noen svekket porten i skriptet.
    """
    import importlib.util
    from pathlib import Path
    fil = (Path(__file__).resolve().parents[3]
           / "deploy/staging/deployport-modultyper.py")
    spec = importlib.util.spec_from_file_location("_deployport_modultyper",
                                                  fil)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_skjemaporten_er_ingen_migrasjon():
    """Codex P1, runde 3: kravet om oppslagbart skjema kan ikke stå i en
    migrasjon — uansett hvilken fil.

    `opp.sh` migrerer BEGGE basene. Testbasen bærer syntetiske typerader
    per konstruksjon: pre-036-tester committet `artefakttype_register`-rader
    med tilfeldige hasher (`_hex64()`, `'sh'`) det aldri har eksistert et
    skjema for. En migrasjonsport traff dermed den persistente testbasen
    med en oppskrift ingen KAN følge — `registrer_artefaktskjema` regner
    ut SHA-256 selv, så det finnes ikke noe skjema å registrere for en
    tilfeldig hash — og hvert nye forsøk feilet identisk.

    Porten bor derfor i deploy-porten, som kjører mot runtime-DSN-en alene.
    Det er nøyaktig det skillet steg 6b i `opp.sh` alt gjør, og av samme
    grunn.

    Kontroll: legg kravet tilbake i en `.sql` under `db/migrations`, så
    blir denne rød.
    """
    from pathlib import Path
    mappe = Path(__file__).resolve().parents[1] / "db/migrations"
    for f in sorted(mappe.glob("[0-9][0-9][0-9]_*.sql")):
        tekst = f.read_text(encoding="utf-8")
        # Selve mønsteret porten MÅ ha for å kunne stoppe: en
        # eksistenssjekk fra typeregisteret mot skjemalageret som reiser.
        blokker = [b for b in tekst.split("DO $$")[1:]
                   if "artefakttype_register" in b
                   and "artefaktskjema" in b and "RAISE EXCEPTION" in b]
        assert not blokker, (
            f"{f.name} porter på manglende artefaktskjema — det låser den"
            " persistente testbasen, som bærer syntetiske hasher")
    # ... og porten finnes, i den fila som faktisk kjøres.
    assert hasattr(_deployport(), "_skjemaporten")


@pg
def test_deployporten_krever_skjema_for_alle_gamle_typer(migrator):
    """Codex P1: 036 slår på et UBETINGET skjemaoppslag i `/v1/artefakt`.

    `registrer_artefakttype` har vært kallbar siden 016/035, og enhver type
    som ble registrert på en OPPGRADERT base før 036 bærer en `skjema_hash`
    uten rad i `artefaktskjema` — bindingen er en hash, ikke en
    fremmednøkkel, så ingenting stoppet det. En artefakttype som tok imot
    opplastninger i går ville blitt fullstendig ubrukelig i det den nye
    releasen ble aktivert, og ikke reparerbar bakover: både skjemaraden og
    typebindingen er immutable, så bare NØYAKTIG det skjemaet hashen peker
    på kan fikse den.

    Innholdet kan ikke bakfylles fra basen (den har hashen, ikke skjemaet),
    så porten stopper DEPLOYEN. Den kjører etter migrasjonene og før
    release-byttet: lageret og `registrer_artefaktskjema` finnes, den gamle
    koden står fortsatt og slår ikke opp skjemaet, og oppskriften i
    feilmeldingen lar seg utføre.

    Kontroll: fjern `_skjemaporten`-kallet i `kontroller`, så blir denne
    rød.
    """
    port = _deployport()
    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _plukket_oppdrag_med_binding(migrator, modul, kh)   # lager kontrakten

    # En type med et registrert skjema navngis ikke. (Den delte testbasen
    # kan bære grandfathered bindinger fra andre tester, så det som
    # sjekkes er at porten ikke navngir VÅR type — ikke at hele basen er
    # ren. Det er nettopp den urenheten som gjør at porten ikke kan være
    # en migrasjon.)
    at_ok = _streng_type(migrator, modul, kh)
    assert not [f for f in port._skjemaporten(migrator) if at_ok in f], \
        "porten navngav en type som HAR skjema"

    # ... og en «grandfathered» binding — hash uten skjemarad, slik bare et
    # direkte INSERT fra før 036 kunne lagd den — stopper deployen.
    at = f"kontroll.b{secrets.token_hex(3)}.rapport"
    migrator.execute(
        "INSERT INTO artefakttype_register (artefakttype, eiermodul,"
        " kontraktversjon, kontrakt_hash, skjema_hash)"
        " VALUES (%s,%s,1,%s,%s)", (at, modul, kh, "c" * 64))
    treff = [f for f in port._skjemaporten(migrator) if at in f]
    # Meldingen navngir typen OG hashen: den som kjører deployen trenger
    # begge for å registrere riktig skjema før deployen prøves igjen.
    assert len(treff) == 1 and "c" * 64 in treff[0], treff
    # Og den er med i det `kontroller` faktisk rapporterer — en port som
    # ikke kalles er ingen port.
    assert [f for f in port.kontroller(migrator) if at in f]
    migrator.rollback()


@pg
def test_registrene_taaler_ikke_truncate(migrator):
    """Codex P2: TRUNCATE fyrer INGEN rad-trigger, så
    `artefaktskjema_immutable` så den aldri. Og her finnes ingen
    fremmednøkkel som kunne stoppet den indirekte:
    `artefakttype_register.skjema_hash` er en HASH, ikke en referanse.
    Tabelleieren kunne derfor tømt hele skjemalageret i ett statement og
    etterlatt hver registrerte artefakttype uten et oppslagbart skjema —
    altså hver opplastning avvist, for alltid, siden både skjemarader og
    typebindinger er immutable.

    `malautorisasjonsvilkar` hadde samme hull og verre retning: registeret
    leses POSITIVT av aktiveringsporten (bare rader teller), så en tømt
    tabell slår av målautorisasjonskravet i stillhet. Funksjonens egen
    feilmelding lovet «raden er immutabel»; ingen trigger gjorde det sant.

    CASCADE så en eventuell FK-sperre (FeatureNotSupported) ikke skygger
    for vakten som faktisk prøves — BEFORE TRUNCATE fyrer først. Kontroll:
    fjern `*_ingen_truncate`-triggerne i 036, så blir denne rød.
    """
    for t in ("artefaktskjema", "malautorisasjonsvilkar"):
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(f"TRUNCATE {t} CASCADE")
        migrator.rollback()
    # ... og radveien for målautorisasjonsregisteret, som manglet helt.
    for sql in ("UPDATE malautorisasjonsvilkar SET maldomene='web_hostname'"
                " WHERE vilkar_type='domenekontroll_verifisert'",
                "DELETE FROM malautorisasjonsvilkar"
                " WHERE vilkar_type='domenekontroll_verifisert'"):
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(sql)
        migrator.rollback()


@pg
def test_artefakttype_krever_registrert_skjema(migrator):
    """Port 15 (registersiden): positiv regel, fail-closed. Kontroll: fjern
    eksistenssjekken i 036-kroppen, så blir denne rød."""
    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _plukket_oppdrag_med_binding(migrator, modul, kh)   # lager kontrakten
    da = _mk_admin("disponit_domains_admin")
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue,
                           match="finnes ikke"):
            da.execute("SELECT registrer_artefakttype(%s,%s,1,%s,%s,'test')",
                       (f"kontroll.x{secrets.token_hex(3)}.rapport", modul,
                        kh, "e" * 64))
        da.rollback()
    finally:
        da.close()


# --------------------------------------------------------------------------
# Valideringen ved OPPLASTING (portene 13, 15) og PROMOTERING (14)
# --------------------------------------------------------------------------

@pg
@dekker("artefakt_skjemabrudd", "artefaktskjema_mangler")
def test_opplasting_valideres_mot_typens_skjema(migrator, klient, token):
    """Port 13: brudd avvises FØR kryptering — ingen staged rad; gyldig
    innhold går gjennom. Port 15 (opplastingssiden): en type med hash uten
    skjemarad (grandfathered via direkte INSERT) avvises som
    konfigurasjonsfeil. Kontroll: fjern valideringsblokken i
    `_artefakt_upload`, så blir denne rød."""
    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    opp, _ = _plukket_oppdrag_med_binding(migrator, modul, kh)
    at = _streng_type(migrator, modul, kh)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))

    jti = _utsted_cap(opp, modul, kh, at)
    r = _post(klient, tok, jti, {"resultat": "ok", "smugling": 1})
    assert (r.status_code, r.json()["feil"]) == (422, "artefakt_skjemabrudd"), \
        r.text
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute("SELECT count(*) FROM artefakt WHERE"
                         " kapabilitet_jti=%s", (jti,)).fetchone()[0]
    migrator.rollback()
    assert n == 0, "et skjemabrudd etterlot en staged rad"

    # Kapabiliteten er engangs og BLE innløst av forsøket — riktig: den var
    # gyldig, innholdet var det ikke. Ny kapabilitet for det gyldige.
    jti2 = _utsted_cap(opp, modul, kh, at)
    r2 = _post(klient, tok, jti2, {"resultat": "ok"})
    assert r2.status_code == 200, r2.text

    # Grandfathered type: hash uten skjemarad → 422, driftskoden.
    at3 = f"kontroll.g{secrets.token_hex(3)}.rapport"
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "INSERT INTO artefakttype_register (artefakttype, eiermodul,"
        " kontraktversjon, kontrakt_hash, skjema_hash)"
        " VALUES (%s,%s,1,%s,%s)", (at3, modul, kh, "d" * 64))
    migrator.commit()
    jti3 = _utsted_cap(opp, modul, kh, at3)
    r3 = _post(klient, tok, jti3, {"resultat": "ok"})
    assert (r3.status_code,
            r3.json()["feil"]) == (422, "artefaktskjema_mangler"), r3.text


@pg
def test_promotering_revaliderer_og_karantenesetter_brudd(migrator, klient,
                                                          token):
    """Port 14: innhold som omgikk opplastingsvalideringen (direkte insert —
    «en fremtidig opplastingsvei glemte punkt 1») promoteres ALDRI:
    kvitteringen får 409, artefaktet karantenesettes, oppdraget avsluttes
    ikke. Kontroll: fjern revalideringsblokken i kvittering-ingesten, så
    blir denne rød."""
    from db import kryptering
    from .test_m37 import _signer_kvittering
    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    opp, _ = _plukket_oppdrag_med_binding(migrator, modul, kh)
    at = _streng_type(migrator, modul, kh)
    oc, rep, gen = _oppdrag_owner(migrator, opp)

    # Direkte staged insert med SKJEMABRYTENDE innhold (feltene matcher
    # `_artefakt`-hjelperen i domene_artefakt, men innholdet er vårt).
    from policy_validator import jcs
    innhold = {"rapport": "smuglet forbi opplastingen"}
    kanon = jcs.kanoniske_bytes(innhold)
    kts = hashlib.sha256(kanon).hexdigest()
    _sett_kontekst(migrator, TENANT)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, TENANT)
    ct, nonce = kryptering.krypter(dek, innhold, TENANT, key_id)
    aid = migrator.execute(
        "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype, modul_id,"
        " release_id, kontraktversjon, kontrakt_hash, module_epoch, tilstand,"
        " storrelse_bytes, klartekst_sha256, ciphertext, nonce, dek_ref,"
        " kapabilitet_jti)"
        " VALUES (%s,%s,%s,%s,'r1',1,%s,0,'staged',%s,%s,%s,%s,%s,%s)"
        " RETURNING artefakt_id",
        (TENANT, opp, at, modul, kh, len(kanon), kts, ct, nonce, key_id,
         "jti-" + secrets.token_hex(8))).fetchone()[0]
    migrator.commit()

    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering(
        _kvitteringskropp(opp, kjti, rep, oc, gen, str(aid), kts))
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 409, rk.text
    _sett_kontekst(migrator, TENANT)
    art_st = migrator.execute("SELECT tilstand FROM artefakt WHERE"
                              " artefakt_id=%s", (aid,)).fetchone()[0]
    opp_st = migrator.execute("SELECT status FROM oppdrag WHERE tenant=%s"
                              " AND id=%s", (TENANT, opp)).fetchone()[0]
    migrator.rollback()
    assert (art_st, opp_st) == ("karantene", "plukket"), (art_st, opp_st)


# --------------------------------------------------------------------------
# Sideeffektklassen (portene 29–30) og målautorisasjonsregisteret
# --------------------------------------------------------------------------

@pg
def test_sideeffektklassen_ekstern_lesing(migrator):
    """Port 29–30: `ekstern_lesing` godtas, ukjent verdi avvises, og en
    eksisterende kontrakt kan ikke omklassifiseres (modulkontrakt tåler
    ingen UPDATE — 014a-invarianten bærer kravet)."""
    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    migrator.execute("INSERT INTO modulhode (modul_id) VALUES (%s)", (modul,))
    migrator.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,1,%s,'p','k','ekstern_lesing','direkte')",
        (modul, kh))
    migrator.commit()
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "INSERT INTO modulkontrakt (modul_id,kontraktversjon,"
            "kontrakt_hash,payload_schema_hash,kvittering_schema_hash,"
            "sideeffektklasse,reversibilitet)"
            " VALUES (%s,2,%s,'p','k','fri_flyt','direkte')",
            (modul, "k2-" + secrets.token_hex(4)))
    migrator.rollback()
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE modulkontrakt SET"
                         " sideeffektklasse='krever_outbox'"
                         " WHERE modul_id=%s", (modul,))
    migrator.rollback()


@pg
def test_malautorisasjonsvilkar_er_lukket_og_immutabelt(migrator):
    """§3/§6: seedet vilkår finnes; nye går via herdet funksjon (idempotent,
    aldri omregistrering til annet domene); ukjent maldomene avvises av
    CHECK. Tom liste er default — bare rader teller."""
    rad = migrator.execute(
        "SELECT maldomene FROM malautorisasjonsvilkar"
        " WHERE vilkar_type='domenekontroll_verifisert'").fetchone()
    migrator.rollback()
    assert rad == ("web_hostname",)
    c = _mk_admin("disponit_modules_admin")
    try:
        vt = "vilkar_" + secrets.token_hex(3)
        c.execute("SELECT registrer_malautorisasjonsvilkar(%s,"
                  "'web_hostname','test')", (vt,))
        c.execute("SELECT registrer_malautorisasjonsvilkar(%s,"
                  "'web_hostname','test')", (vt,))          # idempotent
        c.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("SELECT registrer_malautorisasjonsvilkar(%s,"
                      "'dns_zone','test')", ("v2_" + secrets.token_hex(3),))
        c.rollback()
    finally:
        c.close()


@pg
def test_malautorisasjonsvilkar_serialiserer_samtidig_registrering(migrator):
    """Codex P2: check-then-insert uten lås. To samtidige registreringer av
    samme NYE vilkår så begge «finnes ikke» og gikk videre til INSERT; én
    vant, den andre fikk PK-brudd — selv om innholdet var identisk og
    funksjonen LOVER en idempotent no-op i nettopp det tilfellet.

    Kontroll: fjern pg_advisory_xact_lock i migrasjonen, så blir den andre
    forbindelsen liggende på unikhetsindeksen i stedet, og feiler med
    UniqueViolation i det den første committer."""
    vt = "vilkar_" + secrets.token_hex(4)
    a, b = (_mk_admin("disponit_modules_admin"),
            _mk_admin("disponit_modules_admin"))
    feil, startet = [], threading.Event()

    def registrer_b():
        startet.set()
        try:
            b.execute("SELECT registrer_malautorisasjonsvilkar(%s,"
                      "'web_hostname','test')", (vt,))
            b.commit()
        except Exception as e:                       # noqa: BLE001
            feil.append(e)

    try:
        a.execute("SELECT registrer_malautorisasjonsvilkar(%s,"
                  "'web_hostname','test')", (vt,))   # holder låsen, uåpnet
        t = threading.Thread(target=registrer_b, daemon=True)
        t.start()
        startet.wait(5)
        time.sleep(0.5)
        assert t.is_alive(), "den andre forbindelsen ble ikke serialisert"
        a.commit()
        t.join(15)
        assert not t.is_alive(), "den andre forbindelsen ble aldri ferdig"
        assert not feil, feil                        # idempotent, ikke PK-brudd
        assert migrator.execute(
            "SELECT count(*) FROM malautorisasjonsvilkar WHERE"
            " vilkar_type=%s", (vt,)).fetchone() == (1,)
        migrator.rollback()
    finally:
        a.close()
        b.close()


# --------------------------------------------------------------------------
# Aktiveringsporten (§6, portene 31, 34–37)
# --------------------------------------------------------------------------

def _ekstern_lesing_modul(migrator_):
    modul = "m-" + secrets.token_hex(4)
    migrator_.execute("INSERT INTO modulhode (modul_id) VALUES (%s)", (modul,))
    migrator_.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,1,%s,'p','k','ekstern_lesing','direkte')",
        (modul, "k-" + secrets.token_hex(8)))
    migrator_.commit()
    return modul


def _handling(modul, *, frekvens=True, vilkaar=("domenekontroll_verifisert",),
              hid="kontroll.wcag.nettsted", gruppering="ressurs_id"):
    h = {"id": hid, "modul": modul, "modus": "auto",
         "ved_brudd": "unntakskø",
         "vilkaar": [{"navn": v, "verifikator": "v1"} for v in vilkaar],
         "reversering": {"type": "direkte"}}
    if frekvens:
        # `grupperingsnokkel` er `ressurs_id` fordi aktiveringsporten
        # krever det (Codex P2): telleren skal følge MÅLET, ikke en
        # verdi innsenderen kan variere per forespørsel.
        h["grenser"] = {"frekvens": {"maks": 4, "periode_antall": 1,
                                     "periode_enhet": "dager",
                                     "grupperingsnokkel": gruppering}}
    return h


@pg
def test_aktiveringsporten_for_ekstern_lesing(migrator):
    """Portene 31, 34–37 på funksjonsnivå (kallstedene prøves i
    integrasjonstesten under). Kontroll: fjern frekvens- eller
    vilkårsgrenen i `_krev_ekstern_lesing_port`, så blir hver sin gren rød."""
    from api import policyadmin
    from db.pg import koble
    modul = _ekstern_lesing_modul(migrator)
    c = _mk_admin("disponit_modules_admin")
    try:
        c.execute("SELECT registrer_malautorisasjonsvilkar("
                  "'gyldig_men_ikke_mal','web_hostname','test')")
        c.commit()
    finally:
        c.close()
    rt = koble(DSN)
    try:
        def port(h):
            policyadmin._krev_ekstern_lesing_port(rt, {"handlinger": [h]})

        # 31: uten frekvensgrense → avvist under låsen.
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            port(_handling(modul, frekvens=False))
        assert e.value.kode == "ekstern_lesing_uten_frekvens"
        # 31b (Codex P2): MED frekvens, men gruppert på noe innsenderen
        # kan variere fritt. Motoren teller per `event[grupperingsnokkel]`,
        # så én bøtte per forespørsel er ingen grense mot det nettstedet
        # taket gjelder — den obligatoriske porten blir ren seremoni.
        # Kontroll: fjern grupperingsgrenen, så blir denne rød.
        for fritt in ("forespoersel_id", "tidspunkt", "mal_url", "", None):
            with pytest.raises(policyadmin.Aktiveringsfeil) as e:
                port(_handling(modul, gruppering=fritt))
            assert e.value.kode == "frekvens_uten_malbinding", e.value.kode
        # 34: gyldig, men IKKE målautoriserende vilkår → avvist. Vilkåret
        # `forfall_passert_dager` finnes i policyer — det har bare ingen rad.
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            port(_handling(modul, vilkaar=("forfall_passert_dager",)))
        assert e.value.kode == "malautorisasjon_mangler"
        # 36: navnelikhet teller aldri — bare rader.
        with pytest.raises(policyadmin.Aktiveringsfeil):
            port(_handling(modul, vilkaar=("domenekontroll_verifisert2",)))
        # 37: rad finnes, men for FEIL måldomene? (Alle rader er
        # web_hostname i v1 — probes med et vilkår registrert riktig, mot en
        # handling hvis TYPE mangler målautorisasjonsbegrep.)
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            port(_handling(modul, hid="kontroll.wcag_ukjent.ting"))
        assert "oppdragstype" in (e.value.detalj or "")
        # 35: positiv motsats — domenekontroll_verifisert + frekvens godtas.
        port(_handling(modul))
        # ... og en handling uten målautorisasjonsbærende type, mot en modul
        # UTEN ekstern_lesing, er urørt. (Handlings-id-en må velges utenfor
        # `kontroll.wcag.`: den prefiksen ER den kodefestede WCAG-typen, og
        # den gates nå på sin egen deklarasjon — se testen under.)
        policyadmin._krev_ekstern_lesing_port(
            rt, {"handlinger": [_handling("m-finnes-ikke", frekvens=False,
                                          vilkaar=(), hid="purring.sen")]})
        rt.rollback()
    finally:
        rt.close()


@pg
def test_porten_leser_kontrakten_som_eier_typen(migrator, monkeypatch):
    """Codex P2: porten prøvde modulbredt (`LIMIT 1` på modulkontrakt).
    Kontraktrader er immutable og blir stående, så en modul som EN GANG
    hadde en ekstern_lesing-kontrakt fikk hver eneste handling klassifisert
    som ekstern lesing — også de som nå tilhører en nyere sideeffektfri
    kontrakt. Slike moduler kunne dermed ikke lenger aktivere ellers
    gyldige policyer. Nå leses klassen av kontrakten som EIER handlingens
    registrerte oppdragstype. Kontroll: bytt tilbake til den modulbrede
    prøven, så blir denne rød."""
    import oppdragskontrakt as ok
    from api import policyadmin
    from db.pg import koble

    modul = _ekstern_lesing_modul(migrator)          # gammel, immutabel rad
    # ... samme modul får en NYERE, sideeffektfri kontrakt, og handlingens
    # oppdragstype registreres under NETTOPP den.
    kh2 = "k-" + secrets.token_hex(8)
    migrator.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,2,%s,'p','k','sideeffektfri','direkte')",
        (modul, kh2))
    u = secrets.token_hex(4)
    typenavn = f"stille.w{u}.jobb"
    migrator.execute(
        "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
        "kontraktversjon,kontrakt_hash) VALUES (%s,%s,2,%s)",
        (typenavn, modul, kh2))
    migrator.commit()
    monkeypatch.setitem(ok.OPPDRAGSTYPER, typenavn, ok.Oppdragstype(
        navn=typenavn, handlingsprefikser=(f"stille.w{u}.",),
        felter=frozenset({"ressurs_id"}), paakrevde=frozenset(),
        eiermodul=modul))

    rt = koble(DSN)
    try:
        # Uten frekvens og uten målautoriserende vilkår — og likevel grønn,
        # for handlingen er ikke ekstern lesing.
        policyadmin._krev_ekstern_lesing_port(rt, {"handlinger": [
            _handling(modul, frekvens=False, vilkaar=(),
                      hid=f"stille.w{u}.jobb")]})
        # Motsatsen: en handling uten registrert type treffer fortsatt den
        # konservative modulbrede prøven.
        with pytest.raises(policyadmin.Aktiveringsfeil):
            policyadmin._krev_ekstern_lesing_port(rt, {"handlinger": [
                _handling(modul, frekvens=False, vilkaar=())]})
        rt.rollback()
    finally:
        rt.close()


def test_malautorisasjonen_bindes_til_verten_som_kontrolleres():
    """Codex P1: aktiveringsporten beviste bare at handlingen BÆRER et
    vilkår som er registrert for `web_hostname` — ikke at autorisasjonen
    dekker verten i `mal_url`. Kjøretidsbindingen sammenlignet
    `ressurs_id`, men verken den eller porten så på `mal_url`.

    En hendelse kunne derfor gjenbruke en ekte, gyldig
    `domenekontroll_verifisert`-attestasjon med sin egen `ressurs_id` og be
    om kontroll av et helt annet vertsnavn: trafikk ut mot et mål ingen har
    autorisert, med et bevis som ser gyldig ut hele veien.

    Bindingen legges på `ressurs_id` fordi det feltet allerede ligger i
    `BINDINGSFELT` — altså inne i de SIGNERTE bytene — og
    `kontroller_binding` krever allerede at attestasjonen bærer samme verdi
    som hendelsen. Kreves det at hendelsens `ressurs_id` ER det
    normaliserte vertsnavnet, arver attestasjonen bindingen gratis.

    Kontroll: la `malbindingsbrudd` returnere None for `web_hostname`, så
    slipper hendelsen med feil vert gjennom og denne blir rød.
    """
    import oppdragskontrakt as ok

    def brudd(**ev):
        return ok.malbindingsbrudd(ev.get("handling"), ev)

    # Riktig vert: ingen brudd. `ressurs_id` ER vertsnavnet.
    assert brudd(handling="kontroll.wcag.nettsted",
                 mal_url="https://kunde.example/a/b",
                 ressurs_id="kunde.example") is None
    # Normalformen: store bokstaver, port, credentials og rotprikk er
    # samme vert — ellers ville hver av dem vært et gratis omgåelsestegn.
    for url in ("https://KUNDE.Example/", "https://kunde.example:443/",
                "https://kunde.example./", "https://u:p@kunde.example/"):
        assert brudd(handling="kontroll.wcag.nettsted", mal_url=url,
                     ressurs_id="kunde.example") is None, url
    # Selve hullet: gyldig autorisasjon for én vert, kontroll av en annen.
    k, d = brudd(handling="kontroll.wcag.nettsted",
                 mal_url="https://offer.example/",
                 ressurs_id="kunde.example")
    assert k == "malautorisasjon_feil_mal"
    # Detaljene navngir FELTET og bærer avtrykk — aldri verdiene selv; de
    # ender i klartekstkolonnen `revisjonslogg.begrunnelse`.
    assert d["felt"] == "mal_url"
    assert d["forventet_avtrykk"] == ok.avtrykk("offer.example")
    assert d["i_forespoersel_avtrykk"] == ok.avtrykk("kunde.example")
    assert d["forventet_avtrykk"] != d["i_forespoersel_avtrykk"]
    # Ugyldig eller manglende mål er fail-closed, ikke en åpen port.
    for url in (None, "", "http://kunde.example/", "https://",
                "ikke en url", "https://kunde.example:99999/"):
        assert brudd(handling="kontroll.wcag.nettsted", mal_url=url,
                     ressurs_id="kunde.example")[0] == \
            "malautorisasjon_mal_ugyldig", url
    # Et måldomene plattformen ikke vet hvordan den binder skal stoppe,
    # ikke passere stille.
    ukjent = ok.Oppdragstype(
        navn="kontroll.ukjentdomene.ting",
        handlingsprefikser=("kontroll.ukjentdomene.",),
        felter=frozenset({"mal_url"}), paakrevde=frozenset(),
        krever_malautorisasjon=True, malautorisasjonsdomene="ip_range")
    ok.OPPDRAGSTYPER["kontroll.ukjentdomene.ting"] = ukjent
    try:
        assert brudd(handling="kontroll.ukjentdomene.ting",
                     mal_url="https://k.example/",
                     ressurs_id="k.example")[0] == \
            "malautorisasjon_domene_ukjent"
    finally:
        del ok.OPPDRAGSTYPER["kontroll.ukjentdomene.ting"]
    # Typer UTEN målautorisasjonsdomene er urørt — porten gjelder bare der
    # typen selv sier at målet må være autorisert.
    assert brudd(handling="purring.sen", ressurs_id="fak-1") is None
    assert brudd(handling="helt.ukjent", ressurs_id="x") is None
    assert brudd(handling=None) is None

    # ... og koden er klassifisert: uten rad i tabellene ville et brudd
    # blitt STOPP uten M-37-sak, altså et sikkerhetsavvik ingen ser.
    from api.feil import DRIFTSKODER, SIKKERHETSKODER, sakstype_for
    assert "malautorisasjon_feil_mal" in SIKKERHETSKODER
    assert "malautorisasjon_mal_ugyldig" in SIKKERHETSKODER
    assert "malautorisasjon_domene_ukjent" in DRIFTSKODER
    assert sakstype_for("STOPP", "malautorisasjon_feil_mal", None) == \
        ("sikkerhet", "hoy")


def test_avtrykk_er_totalt_for_alt_json_slipper_inn():
    """Codex P2: et ensomt surrogat i `ressurs_id` gjorde bindingen til et
    UNNTAK i stedet for et svar.

    `json.loads('"\\\\ud800"')` er gyldig JSON for Python og gir en helt
    alminnelig `str` — men den strengen har ingen UTF-8-form, så
    `str.encode("utf-8")` kaster. Hendelsen er ubetrodd, og verdien ligger
    i minnet før noen validering rekker å se den.

    Det gjorde avtrykket til en bryter avsenderen eide: `malbindingsbrudd`
    kjører FØR motorens unntaksvakt (`db/pg.py`), så en hendelse som
    navnga feil vert kunne bytte ut STOPP-med-`malautorisasjon_feil_mal`
    med en død forespørsel uten revisjonspost. Den som ville unngå å bli
    logget for et målbindingsbrudd trengte altså bare å skrive
    identifikatoren sin med et surrogat.

    Alle avtrykkene på den samme ubetrodde veien deler regelen
    (`tekstbytes.utf8`), for det hjelper ikke at bindingen svarer hvis
    loggskrivingen kaster på de samme bytene rett etterpå.

    Kontroll: sett `errors=` tilbake til standard i `tekstbytes.utf8`, så
    blir alle tre grenene røde med `UnicodeEncodeError`.
    """
    import json as _json

    import oppdragskontrakt as ok
    from policy_validator import audit

    # Slik verdien faktisk kommer inn: gjennom JSON-parseren, ikke som en
    # literal i testen.
    ev = _json.loads(
        '{"handling": "kontroll.wcag.nettsted",'
        ' "mal_url": "https://kunde.example/a",'
        ' "ressurs_id": "\\ud800"}')

    # 1. Bindingen SVARER, og svaret er brudd — surrogatet er ikke
    #    vertsnavnet `kunde.example`.
    kode, detaljer = ok.malbindingsbrudd(ev["handling"], ev)
    assert kode == "malautorisasjon_feil_mal"
    assert detaljer["felt"] == "mal_url"
    # Fortsatt avtrykk, aldri verdien selv: klartekstkolonnen skal ikke
    # bære en identifikator bare fordi den var uvanlig formet.
    assert "\ud800" not in _json.dumps(detaljer)
    assert detaljer["forventet_avtrykk"] == ok.avtrykk("kunde.example")
    assert detaljer["i_forespoersel_avtrykk"] != detaljer["forventet_avtrykk"]

    # 2. Loggposten lar seg lage av den samme hendelsen — ellers ble
    #    beslutningen til `logging_feilet` og posten uteble likevel.
    assert len(audit.input_hash(ev)) == 64

    # 3. Kodingen er INJEKTIV der `str.encode` kastet. `backslashreplace`
    #    ville gitt surrogatet samme bytes som teksten `\\ud800`, og da er
    #    to ulike identifikatorer umulige å skille i sporet.
    assert ok.avtrykk("\ud800") != ok.avtrykk("\\ud800")
    assert audit.input_hash({"a": "\ud800"}) != audit.input_hash({"a": "\\ud800"})


def test_maalet_leses_som_nettleseren_leser_det():
    """Codex P1: `mal_url` går UENDRET til motoren, så det er Chromium som
    leser den til slutt — og WHATWG-parseren er uenig med `urlsplit`.

    `https://allowed.example\\@evil.example/`: for WHATWG er omvendt
    skråstrek det samme som `/` i en special-scheme-URL, så authority er
    `allowed.example` og `@evil.example/` er sti. `urlsplit` regner `\\`
    som et helt vanlig tegn i netloc, tar delen etter SISTE `@` som vert
    og svarer `evil.example`.

    Det er den farlige retningen: en angriper som faktisk kontrollerer
    `evil.example` kan skaffe en EKTE `domenekontroll_verifisert` for den
    verten, sende `ressurs_id: "evil.example"`, og passere bindingen —
    mens nettleseren besøker `allowed.example`. Både `malbindingsbrudd` og
    kvitteringens `ressurs_id` ville da navngitt en helt annen vert enn
    den som faktisk ble kontrollert.

    Kontroll: fjern `\\`-vakten i `normaliser_vertsnavn`, så blir
    `evil.example` godtatt igjen og første seksjon blir rød.
    """
    import oppdragskontrakt as ok
    from modules.wcag_audit.controller import _ressursbinding

    def brudd(**ev):
        return ok.malbindingsbrudd(ev.get("handling"), ev)

    # Selve hullet: attestasjon for verten `urlsplit` ser, trafikk til den
    # nettleseren ser. Bestillingen skal STOPPE, ikke velge en av dem.
    tvetydig = "https://allowed.example\\@evil.example/"
    assert ok.normaliser_vertsnavn(tvetydig) is None
    for rid in ("evil.example", "allowed.example"):
        assert brudd(handling="kontroll.wcag.nettsted", mal_url=tvetydig,
                     ressurs_id=rid)[0] == "malautorisasjon_mal_ugyldig", rid

    # Samme uenighet, andre stavemåter: nettleseren prosentdekoder,
    # IDNA-mapper og deler på ideografisk punktum. `urlsplit` gjør ingen
    # av delene, så strengene ville pekt på hver sin vert.
    for url in ("https://evil%2eexample/",       # prosentdekodes
                "https://пример.example/",
                "https://evil.example。x/",  # ideografisk punktum
                "https://0x7f.1/",               # IPv4-lignende
                "https://127.0.0.1/", "https://[::1]/",
                "https://kunde.example\\evil.example/",
                "https://kunde.example/a\\b"):   # sti, men samme omskriving
        assert ok.normaliser_vertsnavn(url) is None, url

    # ... og vanlige mål er urørt, inkludert punycode: den formen ER
    # nettleserens egen normalform, så begge parsere leser den likt.
    for url, vent in (("https://kunde.example/a/b", "kunde.example"),
                      ("https://KUNDE.Example./", "kunde.example"),
                      ("https://u:p@kunde.example:443/", "kunde.example"),
                      ("https://a-b.c-d.example/", "a-b.c-d.example"),
                      ("https://xn--p1ai.example/", "xn--p1ai.example"),
                      ("https://k.no/", "k.no")):
        assert ok.normaliser_vertsnavn(url) == vent, url
    assert brudd(handling="kontroll.wcag.nettsted",
                 mal_url="https://kunde.example/a/b",
                 ressurs_id="kunde.example") is None

    # Kvitteringens binding arver vakten fordi den bruker SAMME funksjon —
    # ellers ville modulen signert en vert plattformen aldri autoriserte.
    assert _ressursbinding({"mal_url": tvetydig}) is None
    assert _ressursbinding({"mal_url": "https://kunde.example/"}) == \
        "kunde.example"


@pg
def test_malbindingsporten_staar_i_beslutningsveien(migrator):
    """Porten hører hjemme i `sikker_beslutning_pg`, ikke i `api.kjerne`:
    det er den ENE veien alle evalueringer går (kjernen,
    unntaksbehandlingen, og det som måtte komme). En port på
    forespørselsveien alene ville vært en port med en dør ved siden av.

    Kontroll: fjern målbindingskallet i `sikker_beslutning_pg`, så blir
    denne rød — hendelsen med feil vert blir evaluert i stedet for stoppet.

    Testen leser også `revisjonslogg.begrunnelse` (Codex P1): den kolonnen
    er KLARTEKST, og bruddetaljene havner der. Kopierte porten `vert` og
    `ressurs_id` ordrett inn i `Grunn.params`, la en hvilken som helst
    innsender igjen en kundeidentifikator og et vertsnavn fra payloaden
    permanent i klartekst — utenfor det krypterte sporet — bare ved å
    sende en forespørsel som feiler.

    Kontroll (lekkasjen): bytt detaljene tilbake til `{"forventet": vert,
    "i_forespoersel": str(...)}`, så finner løkka under `offer.example` og
    `kunde.example` i kolonnen.
    """
    import yaml
    from db.pg import koble, sett_tenant, sikker_beslutning_pg
    from policy_validator.engine import STOPP, EvaluationContext
    from .conftest import POLICIES
    policy = yaml.safe_load(
        (POLICIES / "bransjemal-tjenestebedrift.yaml").read_text(
            encoding="utf-8"))
    ctx = EvaluationContext("t-pg", "agent", True, "api_token")
    ev = {"handling": "kontroll.wcag.nettsted",
          "mal_url": "https://offer.example/",
          "ressurs_id": "kunde.example"}
    c = koble(DSN)
    try:
        d = sikker_beslutning_pg(policy, ctx, ev, c, naa=None, nokler=None)
        assert d.beslutning == STOPP
        assert d.begrunnelse[-1].kode == "malautorisasjon_feil_mal", \
            d.begrunnelse[-1].kode

        # ...og det som ble SKREVET til klartekstkolonnen bærer ingen av
        # verdiene — verken vertsnavnet fra payloaden eller ressurs-id-en.
        #
        # `sett_tenant` er ikke pynt: migrasjon 002 har FORCE ROW LEVEL
        # SECURITY på `revisjonslogg`, og `_skriv_loggpost` setter
        # `disponit.tenant` med SET LOCAL — den dør med skrivetransaksjonen.
        # Uten dette leser vi gjennom et TOMT RLS-vindu, og en test som
        # skulle bevise at verdiene er borte ville bestått fordi den ikke
        # så noen rad i det hele tatt.
        sett_tenant(c, "t-pg")
        rad = c.execute(
            "SELECT begrunnelse::text FROM revisjonslogg"
            " WHERE tenant=%s ORDER BY id DESC LIMIT 1", ("t-pg",)).fetchone()
        assert rad is not None, "brudd uten loggpost"
        skrevet = rad[0]
        assert "malautorisasjon_feil_mal" in skrevet, skrevet
        for verdi in ("offer.example", "kunde.example"):
            assert verdi not in skrevet, (
                f"{verdi!r} lekket til revisjonslogg.begrunnelse: {skrevet}")
        c.rollback()
    finally:
        c.close()


@pg
def test_uregistrert_kodefestet_type_feiler_lukket(migrator):
    """Codex P1: den kodefestede typen fantes, DB-registreringen manglet.

    `_typens_sideeffektklasse` ga da None, og porten falt tilbake på en
    modulbred prøve mot `handlinger[].modul`. Men det feltet er POLICYENS
    modulidentifikator (`M-23`), mens kontrakten er registrert på
    `m_wcag_audit` — to navnerom. Oppslaget fant ingenting, handlingen ble
    lest som ikke-ekstern, og BÅDE frekvens- og målautorisasjonsporten ble
    hoppet over for nøyaktig den handlingstypen de er bygget for.

    Tilstanden er nåbar: `registrer-m-wcag-audit.py` kjøres manuelt, og
    deploy-porten sjekker bare DB-rader som mangler i koden, ikke omvendt.

    Nå er koden autoriteten når registeret ikke har tatt igjen:
    `krever_malautorisasjon` i den kodefestede typen betyr at porten
    gjelder, uansett hva `modul` sier.

    Kontroll: la den uregistrerte kodefestede typen falle tilbake på den
    modulbrede prøven igjen, så blir denne rød — handlingen slipper
    gjennom uten frekvens og uten målautorisasjon.
    """
    from api import policyadmin
    from db.pg import koble
    rt = koble(DSN)
    try:
        # `M-23` finnes ikke i modulkontrakt (og skal ikke gjøre det —
        # det er policynavnerommet). Typen kontroll.wcag.nettsted er ikke
        # registrert i denne databasen.
        assert policyadmin._typens_sideeffektklasse(
            rt, "kontroll.wcag.nettsted") is None
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            policyadmin._krev_ekstern_lesing_port(rt, {"handlinger": [
                _handling("M-23", frekvens=False, vilkaar=())]})
        assert e.value.kode == "ekstern_lesing_uten_frekvens", e.value.kode
        # Med frekvens, men uten målautoriserende vilkår: andre porten.
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            policyadmin._krev_ekstern_lesing_port(rt, {"handlinger": [
                _handling("M-23", vilkaar=("forfall_passert_dager",))]})
        assert e.value.kode == "malautorisasjon_mangler", e.value.kode
        # En type UTEN kodefestet målautorisasjon er urørt av dette —
        # der gjelder fortsatt den konservative modulbrede prøven.
        policyadmin._krev_ekstern_lesing_port(rt, {"handlinger": [
            _handling("M-23", frekvens=False, vilkaar=(),
                      hid="purring.sen")]})
        rt.rollback()
    finally:
        rt.close()


@pg
def test_aktiveringsporten_haandheves_ved_rundeaapning(migrator):
    """Integrasjonen: kallstedet i `opprett_aktiveringsrunde` (samme mønster
    som `_krev_innforingskrav`). Kontroll: fjern
    `_krev_ekstern_lesing_port`-kallet der, så blir denne rød."""
    from .test_pr013_policyadmin_flyt import TEN, _apne, _medlem, _utkast
    from api import policyadmin
    modul = _ekstern_lesing_modul(migrator)
    forf = _medlem("wcagforf-" + secrets.token_hex(2), ["policyforvalter"])
    pid = "pol-" + secrets.token_hex(3)
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, forf, {"roller": [{"id": "r1"}],
                             "handlinger": [_handling(modul, vilkaar=())]})
    from db.pg import koble
    rt = koble(DSN)
    try:
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _apne(rt, uid, forf)
        assert e.value.kode == "malautorisasjon_mangler", e.value.kode
        rt.rollback()
    finally:
        rt.close()
    # ... og med vilkåret på plass åpner runden.
    uid2 = "utk-" + secrets.token_hex(3)
    _utkast(uid2, pid, forf, {"roller": [{"id": "r1"}],
                              "handlinger": [_handling(modul)]})
    rt = koble(DSN)
    try:
        r = _apne(rt, uid2, forf)
        assert r["diff_hash"]
    finally:
        rt.close()


# --------------------------------------------------------------------------
# Deploy-portene (§5, portene 6 og 32)
# --------------------------------------------------------------------------

@pg
def test_deployportene_register_mot_kodefestet_type(migrator, monkeypatch):
    """Port 6: registerrad uten kodefestet type → rød. Port 32: klasse og
    autorisasjonskrav må stemme BEGGE veier — ekstern_lesing-kontrakt med
    type uten krever_malautorisasjon, OG type med krever_malautorisasjon
    under en kontrakt som ikke er ekstern_lesing. Grønn tilstand er den
    positive motsatsen i hvert tilfelle. Kontroll: fjern LEFT JOIN-en
    (port 32-grenene) i `kontroller()`, så blir andre halvdel grønn på
    feil grunnlag."""
    import importlib.util
    from pathlib import Path
    sti = (Path(__file__).resolve().parents[3]
           / "deploy/staging/deployport-modultyper.py")
    spec = importlib.util.spec_from_file_location("deployport_test", sti)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import oppdragskontrakt as ok

    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    migrator.execute("INSERT INTO modulhode (modul_id) VALUES (%s)", (modul,))
    migrator.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,1,%s,'p','k','ekstern_lesing','direkte')",
        (modul, kh))
    ukjent = f"deployport{secrets.token_hex(3)}"
    migrator.execute(
        "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
        "kontraktversjon,kontrakt_hash) VALUES (%s,%s,1,%s)",
        (ukjent, modul, kh))
    migrator.commit()

    # Port 6: raden er ukjent for koden → rød med typenavnet i meldingen.
    feil = mod.kontroller(migrator)
    migrator.rollback()
    assert any(ukjent in f for f in feil), feil

    # Port 32: kodefest typen, men UTEN målautorisasjonsflagget → fortsatt
    # rød, nå på autorisasjonsbegrepet.
    t = ok.Oppdragstype(navn=ukjent, handlingsprefikser=(f"{ukjent}.",),
                        felter=frozenset({"mal_url"}), paakrevde=frozenset(),
                        eiermodul=modul)
    monkeypatch.setitem(ok.OPPDRAGSTYPER, ukjent, t)
    feil = mod.kontroller(migrator)
    migrator.rollback()
    assert any("krever_malautorisasjon" in f and ukjent in f for f in feil), \
        feil

    # Grønn motsats: flagg + domene på plass → ingen feil for VÅR rad.
    t2 = ok.Oppdragstype(navn=ukjent, handlingsprefikser=(f"{ukjent}.",),
                         felter=frozenset({"mal_url"}), paakrevde=frozenset(),
                         eiermodul=modul, krever_malautorisasjon=True,
                         malautorisasjonsdomene="web_hostname")
    monkeypatch.setitem(ok.OPPDRAGSTYPER, ukjent, t2)
    feil = mod.kontroller(migrator)
    migrator.rollback()
    assert not any(ukjent in f for f in feil), feil

    # Codex P1: samme rad, men koden sier at typen eies av en ANNEN modul.
    # Registerraden er autoriteten claim-veien utleder prefiksene fra, så
    # avviket ville gitt den registrerte modulen rekkevidde over payloads
    # ment for den kodefestede eieren. Rød.
    t3 = ok.Oppdragstype(navn=ukjent, handlingsprefikser=(f"{ukjent}.",),
                         felter=frozenset({"mal_url"}), paakrevde=frozenset(),
                         eiermodul="m_en_helt_annen",
                         krever_malautorisasjon=True,
                         malautorisasjonsdomene="web_hostname")
    monkeypatch.setitem(ok.OPPDRAGSTYPER, ukjent, t3)
    feil = mod.kontroller(migrator)
    migrator.rollback()
    assert any("m_en_helt_annen" in f and ukjent in f for f in feil), feil

    # ... men en type UTEN kodefestet eier (legacy) skal ikke fanges av
    # eierporten — et krav kan ikke håndheves mot en taus kilde.
    t4 = ok.Oppdragstype(navn=ukjent, handlingsprefikser=(f"{ukjent}.",),
                         felter=frozenset({"mal_url"}), paakrevde=frozenset(),
                         krever_malautorisasjon=True,
                         malautorisasjonsdomene="web_hostname")
    monkeypatch.setitem(ok.OPPDRAGSTYPER, ukjent, t4)
    feil = mod.kontroller(migrator)
    migrator.rollback()
    assert not any(ukjent in f for f in feil), feil

    # Codex P1, DEN ANDRE RETNINGEN: en type som KREVER målautorisasjon,
    # registrert under en sideeffektfri kontrakt. Porten så bare det
    # motsatte avviket, mens `_krev_ekstern_lesing_port` leser typens
    # klasse, ser noe annet enn ekstern_lesing og hopper over HELE porten
    # — både frekvens og målautorisasjon. Rød.
    # Kontroll: fjern elif-grenen i `kontroller()`, så blir denne rød.
    kh2 = "k-" + secrets.token_hex(8)
    fri = f"deployport{secrets.token_hex(3)}"
    migrator.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,2,%s,'p','k','sideeffektfri','direkte')",
        (modul, kh2))
    migrator.execute(
        "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
        "kontraktversjon,kontrakt_hash) VALUES (%s,%s,2,%s)",
        (fri, modul, kh2))
    migrator.commit()
    monkeypatch.setitem(ok.OPPDRAGSTYPER, fri, ok.Oppdragstype(
        navn=fri, handlingsprefikser=(f"{fri}.",),
        felter=frozenset({"mal_url"}), paakrevde=frozenset(),
        eiermodul=modul, krever_malautorisasjon=True,
        malautorisasjonsdomene="web_hostname"))
    feil = mod.kontroller(migrator)
    migrator.rollback()
    assert any("målautorisasjon" in f and fri in f for f in feil), feil

    # Grønn motsats for samme rad: uten autorisasjonskravet er en
    # sideeffektfri registrering helt i orden.
    monkeypatch.setitem(ok.OPPDRAGSTYPER, fri, ok.Oppdragstype(
        navn=fri, handlingsprefikser=(f"{fri}.",),
        felter=frozenset({"mal_url"}), paakrevde=frozenset(),
        eiermodul=modul))
    feil = mod.kontroller(migrator)
    migrator.rollback()
    assert not any(fri in f for f in feil), feil


def test_oppdraget_bindes_til_den_deklarerte_eiermodulen():
    """Codex P1: `_eiermodul_for` skrev `eiermodul:<typenavn>` for ALLE
    typer, også den nye. Oppdraget fikk da
    `eiermodul:kontroll.wcag.nettsted`, mens kontrakt, deployment og token
    står på `m_wcag_audit` — og claim krever `oppdrag.eiermodul =
    auth.modul_id`. Controlleren kunne aldri claimet sitt eget oppdrag; det
    ville ligget til fristen uten at noen så det.
    Kontroll: bytt tilbake til `f"eiermodul:{t.navn}"`, så blir denne
    rød."""
    import oppdragskontrakt as ok
    from m37.arbeider import _eiermodul_for

    assert _eiermodul_for("kontroll.wcag.nettsted.kjor") == "m_wcag_audit"
    assert (_eiermodul_for("kontroll.wcag.nettsted.kjor")
            == ok.OPPDRAGSTYPER["kontroll.wcag.nettsted"].eiermodul)
    # De eierløse legacy-typene beholder det SYNTETISKE navnet — for dem
    # finnes ingen modulrad, og eksisterende rader og tokener peker hit.
    assert _eiermodul_for("purring.send") == "eiermodul:reinnsending"
    assert _eiermodul_for("verifiser.belop") == "eiermodul:verifikasjon"
    # Ukjent handling er fortsatt fail-closed: en modul-id ingen har.
    assert _eiermodul_for("noe.helt.annet") == "eiermodul:ukjent"


# --------------------------------------------------------------------------
# Rapportbygging og sanitering (portene 8–12) — modulen selv.
# --------------------------------------------------------------------------

def _kontekst():
    return {"axe_versjon": "4.10.0", "chromium_versjon": "127.0",
            "container_image_digest": "sha256:" + "a" * 64,
            "viewport": "1280x800", "locale": "nb-NO",
            "timezone": "Europe/Oslo"}


def _payload(**over):
    """Oppdragets payload — med `mal_url` på SAMME vert som sidene
    `_motorresultat` rapporterer.

    Den hører med i hver eneste `bygg`-test etter Codex P1: rapporten
    bindes til den autoriserte verten, så en payload uten lesbart mål er
    ikke lenger «en payload vi ikke bryr oss om» — den er fail-closed.

    `mal_url` peker på NØYAKTIG den siden `_motorresultat` rapporterer
    (Codex P1, runde 12): under `omfang: "enkeltside"` er den bestilte
    siden en del av bestillingen, ikke bare verten, og en testpayload som
    ba om `/` mens motoren svarte `/side` var en bestilling ingen av
    testene egentlig mente å gjøre.

    «Nøyaktig» inkluderer QUERY-en (Codex P1, runde 13): den skiller
    `/rapport?id=1` fra `/rapport?id=2` og sammenlignes derfor, selv om
    den redigeres bort av rapporten. Fellespayloaden her ber om siden
    UTEN query, og `_motorresultat` svarer med samme side uten query —
    query-veien har sin egen test
    (`test_enkeltsideporten_leser_query_som_del_av_siden`) i stedet for å
    ligge som en stille forutsetning i hver eneste `bygg`-test.
    """
    basis = {"kravsett": "wcag21_aa", "mal_url": "https://kunde.example/side",
             "omfang": "enkeltside"}
    basis.update(over)
    return basis


def _motorresultat(**over):
    from modules.wcag_audit.motor import Motorresultat
    basis = dict(
        regelsett_versjon="axe-4.10", varighet_ms=1234,
        # Fragmentet blir stående: det redigeres bort av rapporten, og
        # det skiller ikke to sider (det sendes aldri til serveren), så
        # fellesfiksturet kan bære det. Query-en kan det IKKE, se
        # `_payload`.
        sider=({"url": "https://kunde.example/side#topp", "status": "ok"},),
        funn=({"regel_id": "color-contrast", "alvorlighet": "alvorlig",
               "antall": 3, "eksempler": ["#a", "x" * 500]},),
        blokkert=({"vert": "fonts.example", "antall": 2, "art": "font"},),
        avkortet=(False, None, None))
    basis.update(over)
    return Motorresultat(**basis)


def test_rapporten_saneres_og_validerer():
    """Portene 11–12 + skjemarunden: URL uten query/fragment, selektor
    kappet til 200 tegn, maks 10 eksempler, miljø fra SERVERKONTEKSTEN —
    og resultatet validerer mot det innholdsadresserte skjemaet."""
    import jsonschema
    from modules.wcag_audit import rapportskjema
    from modules.wcag_audit.rapport import bygg
    r = bygg(_motorresultat(),
             payload=_payload(),
             kontekst=_kontekst())
    jsonschema.Draft202012Validator(rapportskjema.SKJEMA).validate(r)
    assert r["sider_kontrollert"][0]["url"] == "https://kunde.example/side"
    assert len(r["funn"][0]["eksempler"][1]) == 200
    assert r["miljo"]["container_image_digest"].startswith("sha256:")
    assert r["manuelle_kriterier_vurdert"] is False
    assert r["dekningsbegrensninger"][0] == {"vert": "fonts.example",
                                             "antall": 2, "art": "font"}


def test_rapporten_kutter_aerlig_over_500_funn():
    """Port 11: over 500 funn kappes — og `avkortet` SIER det (aldri mer
    fullstendighet enn innholdet bærer). Kontroll: fjern
    truffet-oppdateringen i `bygg`, så blir denne rød."""
    from modules.wcag_audit.rapport import bygg
    mange = tuple({"regel_id": f"r{i}", "alvorlighet": "lav", "antall": 1,
                   "eksempler": []} for i in range(600))
    r = bygg(_motorresultat(funn=mange),
             payload=_payload(), kontekst=_kontekst())
    assert len(r["funn"]) == 500
    assert r["avkortet"]["truffet"] is True and r["avkortet"]["verdi"] == 600
    # ... men SAMMENDRAGET teller alt motoren fant — kappingen gjelder
    # eksempellisten, ikke sannheten om omfanget.
    assert r["sammendrag"]["lav"] == 600


def test_kappet_eksempelliste_sier_fra_i_avkortet():
    """Codex P2: eksempellisten kappes på 10 per funn — og DA er rapporten
    avkortet. Uten dette kunne den promoterte evidensen påstå
    `truffet: false` samtidig som den utelot kjente eksempler.
    Kontroll: fjern `maks_eksempler_sett`-blokka i `bygg`, så blir denne
    rød."""
    import jsonschema
    from modules.wcag_audit import rapportskjema
    from modules.wcag_audit.rapport import MAKS_EKSEMPLER, bygg

    ett = ({"regel_id": "r1", "alvorlighet": "alvorlig", "antall": 25,
            "eksempler": [f"#node-{i}" for i in range(25)]},)
    r = bygg(_motorresultat(funn=ett),
             payload=_payload(), kontekst=_kontekst())
    jsonschema.Draft202012Validator(rapportskjema.SKJEMA).validate(r)
    assert len(r["funn"][0]["eksempler"]) == MAKS_EKSEMPLER
    assert r["avkortet"]["truffet"] is True
    assert r["avkortet"]["tak"] == MAKS_EKSEMPLER
    assert r["avkortet"]["verdi"] == 25
    # ... og NØYAKTIG på taket er ingen kapping: feltet skal ikke rope ulv.
    paa_taket = ({"regel_id": "r1", "alvorlighet": "lav", "antall": 1,
                  "eksempler": [f"#n{i}" for i in range(MAKS_EKSEMPLER)]},)
    r2 = bygg(_motorresultat(funn=paa_taket),
              payload=_payload(), kontekst=_kontekst())
    assert r2["avkortet"]["truffet"] is False


def test_dekningsbegrensninger_slaas_sammen_og_kappet_sier_fra():
    """Codex P2: lista ble kappet på 200 UTEN at `avkortet` endret seg —
    den promoterte evidensen kunne påstå at ingenting var utelatt samtidig
    som den utelot kjente dekningsbegrensninger (014b B3). Nå slås like
    (vert, art) sammen først, og treffer taket likevel, sier `avkortet`
    fra. Kontroll: fjern taksjekken, så blir denne rød."""
    import jsonschema
    from modules.wcag_audit import rapportskjema
    from modules.wcag_audit.rapport import MAKS_BEGRENSNINGER, bygg

    # Samme vert to ganger → én post med summert antall, ikke to.
    r = bygg(_motorresultat(blokkert=(
        {"vert": "fonts.example", "antall": 2, "art": "font"},
        {"vert": "fonts.example/x?q=1", "antall": 3, "art": "font"})),
        payload=_payload(), kontekst=_kontekst())
    assert r["dekningsbegrensninger"] == [{"vert": "fonts.example",
                                           "antall": 5, "art": "font"}]
    assert r["avkortet"]["truffet"] is False

    # Flere unike verter enn taket → kappet, og `avkortet` sier det.
    mange = tuple({"vert": f"v{i}.example", "antall": 1, "art": "font"}
                  for i in range(MAKS_BEGRENSNINGER + 25))
    r2 = bygg(_motorresultat(blokkert=mange),
              payload=_payload(), kontekst=_kontekst())
    jsonschema.Draft202012Validator(rapportskjema.SKJEMA).validate(r2)
    assert len(r2["dekningsbegrensninger"]) == MAKS_BEGRENSNINGER
    assert r2["avkortet"]["truffet"] is True
    assert r2["avkortet"]["verdi"] == MAKS_BEGRENSNINGER + 25

    # Størst først: treffer taket, er det de STØRSTE som kommer med.
    tunge = ({"vert": "tung.example", "antall": 99, "art": "skript"},) + mange
    r3 = bygg(_motorresultat(blokkert=tunge),
              payload=_payload(), kontekst=_kontekst())
    assert r3["dekningsbegrensninger"][0]["vert"] == "tung.example"


def test_uleselig_dekningsbegrensning_feiler_i_stedet_for_aa_forsvinne():
    """Codex P2: en uleselig blokkert-post ble STILLE forkastet.

    Rapportkontrakten sier at en tom `dekningsbegrensninger` betyr «ingen
    kjente begrensninger» — ikke «vi klarte ikke lese dem». En ødelagt
    proxy kunne derfor gi PROMOTERT evidens som påstår ren dekning nøyaktig
    når dekningen er ukjent. Det er den ene løgnen hele feltet finnes for å
    hindre (014b B3), og den er verre enn et feilet oppdrag: et feilet
    oppdrag ser man.

    Kontroll: bytt de to `raise Motorfeil` tilbake til `continue`, så blir
    denne rød — og den siste påstanden («ingen kjente begrensninger») blir
    en rapport ingen kan skille fra sannheten.
    """
    from modules.wcag_audit.motor import Motorfeil
    from modules.wcag_audit.rapport import bygg

    for daarlig in ("ikke-en-dict", None, 42,
                    {"vert": None, "antall": 5, "art": "font"},
                    {"antall": 5, "art": "font"},
                    {"vert": "", "antall": 5},
                    {"vert": "http://x.example/", "antall": 1},
                    {"vert": "ikke_en_vert", "antall": 1},
                    {"vert": "x" * 300 + ".example", "antall": 1}):
        with pytest.raises(Motorfeil):
            bygg(_motorresultat(blokkert=(daarlig,)),
                 payload=_payload(), kontekst=_kontekst())

    # Motsatsen: en ekte tom liste betyr fortsatt «ingen kjente
    # begrensninger», og den påstanden skal fortsatt kunne uttrykkes.
    r = bygg(_motorresultat(blokkert=()),
             payload=_payload(), kontekst=_kontekst())
    assert r["dekningsbegrensninger"] == []


def test_rapporterte_sider_bindes_til_det_autoriserte_maalet():
    """Codex P1: `sider_kontrollert` navngir det MÅLET som ble autorisert.

    Sidelista kom rått fra motoren og krevde bare https. En motor som
    fulgte en redirect — eller løy — kunne levere en skjemagyldig rapport
    om `evil.example` samtidig som den signerte kvitteringen, attestasjonen
    og hele autorisasjonskjeden navngav `kunde.example`. Konsumenten satt
    da igjen med promotert evidens om ET ANNET nettsted, under en kjede
    som ser gyldig ut hele veien.

    Kontroll: fjern `normaliser_vertsnavn`-sammenligningen i `_ren_url`,
    så blir denne rød — `evil.example` går rett inn i rapporten.
    """
    from modules.wcag_audit.motor import Motorfeil
    from modules.wcag_audit.rapport import bygg
    # Feil vert, subdomene av målet, og målet som subdomene hos angriperen:
    # ingen av dem er verten `web_hostname` autoriserte.
    for url in ("https://evil.example/x",
                "https://sub.kunde.example/x",
                "https://kunde.example.evil.example/x",
                # Tvetydig for nettleseren = ingen entydig vert å binde til.
                "https://kunde.example\\@evil.example/",
                "https://kunde%2eexample/"):
        with pytest.raises(Motorfeil):
            bygg(_motorresultat(sider=({"url": url, "status": "ok"},)),
                 payload=_payload(), kontekst=_kontekst())
    # ... og én side utenfor målet forgifter HELE rapporten: den skal
    # feile, ikke stille forsvinne fra en ellers gyldig sidelise.
    with pytest.raises(Motorfeil):
        bygg(_motorresultat(sider=({"url": "https://kunde.example/a",
                                    "status": "ok"},
                                   {"url": "https://evil.example/b",
                                    "status": "ok"})),
             payload=_payload(), kontekst=_kontekst())
    # Motsatsen: målet selv, i en annen skrivemåte enn payloadens, er
    # SAMME vert — bindingen normaliserer begge sider med plattformens
    # egen funksjon, ikke med en strengsammenligning.
    r = bygg(_motorresultat(sider=({"url": "https://KUNDE.example./dyp/sti",
                                    "status": "ok"},)),
             payload=_payload(mal_url="https://KUNDE.example/dyp/sti"),
             kontekst=_kontekst())
    assert r["sider_kontrollert"][0]["url"] == "https://kunde.example/dyp/sti"
    # Fail-closed når oppdraget ikke HAR et lesbart mål: da finnes det
    # ingen vert å binde til, og en rapport uten binding er verre enn
    # ingen rapport.
    with pytest.raises(Motorfeil):
        bygg(_motorresultat(), payload=_payload(mal_url="http://kunde.example/"),
             kontekst=_kontekst())


def test_rapporten_holder_seg_innenfor_det_bestilte_omfanget():
    """Codex P1: vertsbindingen sier hvor sidene ligger, ikke HVOR MANGE
    noen ba om.

    En motor kunne levere femti sider på den autoriserte verten under et
    oppdrag som ba om `enkeltside` / `maks_sider: 1`: skjemaet tillater 50
    sider, vertsbindingen tar bare verten, og ingen andre i kjeden ser
    motorens sideliste. Resultatet var PROMOTERT evidens om et helt
    nettsted under en beslutning som autoriserte én side — og en
    overskridelse av det EKSTERNE crawlbudsjettet (`ekstern_lesing` er
    observerbar trafikk mot noen andres nettsted) som ingen kunne oppdage.

    Og for `enkeltside` holder det ikke å telle til én: den ene siden må
    være DEN BESTILTE. `/annet` på riktig vert er evidens om noe ingen har
    bestilt — samme løgn som feil vert, ett nivå ned i URL-en.

    Kontroll: fjern `_sidebudsjett`-blokka i `bygg`, så blir denne rød på
    hver eneste av påstandene under.
    """
    from modules.wcag_audit.motor import Motorfeil
    from modules.wcag_audit.rapport import bygg
    to_sider = ({"url": "https://kunde.example/side", "status": "ok"},
                {"url": "https://kunde.example/annet", "status": "ok"})

    # `enkeltside`: budsjettet er én side, uansett hva `maks_sider` sier.
    for p in (_payload(), _payload(maks_sider=1), _payload(maks_sider=50)):
        with pytest.raises(Motorfeil):
            bygg(_motorresultat(sider=to_sider), payload=p,
                 kontekst=_kontekst())
    # `nettsted` med et tak: taket er bestillingen.
    with pytest.raises(Motorfeil):
        bygg(_motorresultat(sider=to_sider),
             payload=_payload(omfang="nettsted", maks_sider=1),
             kontekst=_kontekst())
    # Rett antall, men FEIL side: en enkeltsidekontroll av `/annet` er
    # ikke kontrollen av `/side`.
    with pytest.raises(Motorfeil):
        bygg(_motorresultat(sider=({"url": "https://kunde.example/annet",
                                    "status": "ok"},)),
             payload=_payload(), kontekst=_kontekst())
    # Uleselig bestilling er fail-closed: uten omfang vet modulen ikke hva
    # den skal måle motoren mot.
    for p in ({"kravsett": "wcag21_aa", "mal_url": "https://kunde.example/side"},
              _payload(omfang="alt"), _payload(omfang=None),
              _payload(maks_sider=0), _payload(maks_sider="1"),
              _payload(maks_sider=True), _payload(omfang="nettsted",
                                                  maks_sider=-3)):
        with pytest.raises(Motorfeil):
            bygg(_motorresultat(), payload=p, kontekst=_kontekst())

    # Motsatsene. Den bestilte siden, i en annen skrivemåte enn motorens,
    # er SAMME side — begge sider kanoniseres med `_delt_url`. Fragmentet
    # skiller dem ikke: det sendes aldri til serveren.
    r = bygg(_motorresultat(sider=({"url": "https://kunde.example/side#y",
                                    "status": "ok"},)),
             payload=_payload(mal_url="https://KUNDE.example./side"),
             kontekst=_kontekst())
    assert r["sider_kontrollert"][0]["url"] == "https://kunde.example/side"
    # ... og et `nettsted`-oppdrag med romslig tak får sine to sider.
    r = bygg(_motorresultat(sider=to_sider),
             payload=_payload(omfang="nettsted", maks_sider=10),
             kontekst=_kontekst())
    assert len(r["sider_kontrollert"]) == 2
    # `nettsted` UTEN tak har bare skjemaets 50 å gå på — modulen finner
    # ikke på et tak oppdraget ikke satte.
    r = bygg(_motorresultat(sider=to_sider),
             payload=_payload(omfang="nettsted"), kontekst=_kontekst())
    assert len(r["sider_kontrollert"]) == 2


def test_enkeltsideporten_leser_query_som_del_av_siden():
    """Codex P1: query-en er BÆRENDE for sideidentiteten, ikke pynt.

    `enkeltside`-porten sammenlignet den BESTILTE URL-en og motorens URL i
    RAPPORTFORMEN — altså etter at query og fragment var redigert bort.
    Da sammenlignet den to strenger der nettopp det som skiller sidene var
    strøket: en bestilling av `/rapport?id=1` godtok en kontroll av
    `/rapport?id=2` som «samme side», og evidensen om side 2 ble promotert
    under en beslutning som autoriserte side 1. Hver applikasjon som ruter
    på query — søk, saksvisninger, paginering — treffes av det.

    Redigeringen av rapporten står uendret: query kan bære persondata, og
    den lagrede URL-en skal fortsatt ikke ha den. Det er SAMMENLIGNINGEN
    som må se hele URL-en.

    Kontroll: la `_bestilt_url` og sideloopen bruke rapportformen igjen
    (`_delt_url(...)[0]`), så blir avvisningen under grønn — altså rød her.
    """
    from modules.wcag_audit.motor import Motorfeil
    from modules.wcag_audit.rapport import bygg

    def _kjor(bestilt, levert):
        return bygg(_motorresultat(sider=({"url": levert, "status": "ok"},)),
                    payload=_payload(mal_url=bestilt), kontekst=_kontekst())

    # En ANNEN query er en annen side.
    for bestilt, levert in (
            ("https://kunde.example/rapport?id=1",
             "https://kunde.example/rapport?id=2"),
            # ... og en query motoren fant på selv er også en annen side:
            # bestillingen navnga dokumentet uten den.
            ("https://kunde.example/rapport",
             "https://kunde.example/rapport?id=2"),
            # ... og en bestilt query motoren droppet like så.
            ("https://kunde.example/rapport?id=1",
             "https://kunde.example/rapport")):
        with pytest.raises(Motorfeil):
            _kjor(bestilt, levert)

    # SAMME query er samme side — og rapporten bærer den likevel ikke
    # videre: den lagrede URL-en er fortsatt redigert.
    r = _kjor("https://kunde.example/rapport?id=1&q=a",
              "https://kunde.example/rapport?id=1&q=a#topp")
    assert r["sider_kontrollert"][0]["url"] == "https://kunde.example/rapport"


def test_standardporten_er_ikke_en_del_av_sideidentiteten():
    """Codex P2: `:443` er samme ressurs som ingen port.

    Bestilte noen `https://kunde.example:443/side`, beholdt
    rekonstruksjonen porten på den bestilte formen — mens Chromium (eller
    en redirect) serialiserer den samme siden uten den. `enkeltside`-
    porten sammenlignet da to skrivemåter av ÉN URL, fant dem ulike, og
    lot oppdraget feile ETTER at den eksterne kontrollen var gjort. Det er
    den dyre retningen å ta feil i: trafikken mot kundens nettsted er
    allerede brukt, og siden var den bestilte.

    Ikke-standardporter skiller faktisk to endepunkter, og skal stå.

    Kontroll: fjern `and port != 443` i `_delt_url`, så blir de fire
    første tilfellene røde.
    """
    from modules.wcag_audit.motor import Motorfeil
    from modules.wcag_audit.rapport import bygg

    def _kjor(bestilt, levert):
        return bygg(_motorresultat(sider=({"url": levert, "status": "ok"},)),
                    payload=_payload(mal_url=bestilt), kontekst=_kontekst())

    # Samme side, uansett hvilken av de to som bærer porten.
    for bestilt, levert in (
            ("https://kunde.example:443/side", "https://kunde.example/side"),
            ("https://kunde.example/side", "https://kunde.example:443/side"),
            ("https://kunde.example:443/side",
             "https://kunde.example:443/side"),
            ("https://kunde.example:443/side?id=1",
             "https://kunde.example/side?id=1")):
        r = _kjor(bestilt, levert)
        # ... og rapporten navngir siden på ÉN måte, uten standardporten.
        assert r["sider_kontrollert"][0]["url"] == (
            "https://kunde.example/side"), (bestilt, levert)

    # En ikke-standard port er fortsatt en del av identiteten: den er et
    # annet endepunkt, ikke en annen skrivemåte.
    with pytest.raises(Motorfeil):
        _kjor("https://kunde.example:8443/side", "https://kunde.example/side")


def test_punktsegmenter_er_ikke_en_annen_side():
    """Codex P2: `/a/../side` og `/side` er ÉN side for nettleseren.

    `urlsplit` er en parser og gir stien tegn for tegn; WHATWG-parseren i
    Chromium løser punktsegmentene mens den navigerer og rapporterer den
    løste formen. Bestilte noen `/a/../side`, sammenlignet
    `enkeltside`-porten derfor bestillingens uløste sti med motorens
    løste, fant dem ulike, og lot oppdraget feile ETTER at den eksterne
    kontrollen var gjort — samme dyre retning som `:443` bar.

    Kontroll: la `_delt_url` kopiere `d.path` rått igjen, så blir alle
    tilfellene under røde.
    """
    from modules.wcag_audit.motor import Motorfeil
    from modules.wcag_audit.rapport import bygg

    def _kjor(bestilt, levert):
        return bygg(_motorresultat(sider=({"url": levert, "status": "ok"},)),
                    payload=_payload(mal_url=bestilt), kontekst=_kontekst())

    # Samme side, uansett hvilken av de to som bærer punktsegmentene —
    # også prosentkodet (`%2e`/`%2E` er punktum for nettleseren), og også
    # når `..` peker over roten.
    for bestilt, levert in (
            ("https://kunde.example/a/../side", "https://kunde.example/side"),
            ("https://kunde.example/side", "https://kunde.example/a/../side"),
            ("https://kunde.example/./side", "https://kunde.example/side"),
            ("https://kunde.example/a/%2e%2e/side",
             "https://kunde.example/side"),
            ("https://kunde.example/a/%2E%2E/side",
             "https://kunde.example/side"),
            ("https://kunde.example/../side", "https://kunde.example/side"),
            ("https://kunde.example/a/b/../../side",
             "https://kunde.example/side"),
            ("https://kunde.example/a/../side?id=1",
             "https://kunde.example/side?id=1")):
        r = _kjor(bestilt, levert)
        # ... og rapporten navngir siden på ÉN måte, den nettleseren
        # faktisk besøkte.
        assert r["sider_kontrollert"][0]["url"] == (
            "https://kunde.example/side"), (bestilt, levert)

    # ET AVSLUTTENDE punktsegment gir en avsluttende `/`: nettleseren ber
    # om katalogen, og `/a` og `/a/` kan være to forskjellige ressurser.
    for bestilt, ventet in (("https://kunde.example/a/b/..",
                             "https://kunde.example/a/"),
                            ("https://kunde.example/a/.",
                             "https://kunde.example/a/"),
                            ("https://kunde.example/..",
                             "https://kunde.example/")):
        r = _kjor(bestilt, ventet)
        assert r["sider_kontrollert"][0]["url"] == ventet, bestilt

    # ... og en ekte annen sti er fortsatt en annen side. Oppløsningen
    # slår ikke sammen det serveren skiller.
    with pytest.raises(Motorfeil):
        _kjor("https://kunde.example/a/../side", "https://kunde.example/a/side")


def test_ulovlig_sidestatus_avvises_i_stedet_for_aa_skrives_om():
    """Codex P1: en `status` vi ikke kan lese ble skrevet om til `feilet`.

    Omskrivingen var skjemagyldig hele veien, så den endte i PROMOTERT
    evidens: rapporten påsto en sidefeil hos kunden som motoren aldri
    rapporterte. Manglende felt, `"OK"`, `null`, et tall — alle ble til
    samme fabrikkerte påstand. Uleselige motorutdata skal gi den
    dokumenterte feil-kvitteringen (§2), ikke en rapport modulen har fylt
    hullene i selv.

    Kontroll: bytt `_sidestatus(...)` tilbake til
    `s.get("status") if s.get("status") in ("ok", "feilet") else "feilet"`,
    så blir denne rød — hver eneste variant passerer da som `feilet`.
    """
    from modules.wcag_audit.motor import Motorfeil
    from modules.wcag_audit.rapport import bygg
    for side in ({"url": "https://kunde.example/side"},          # mangler
                 {"url": "https://kunde.example/side", "status": None},
                 {"url": "https://kunde.example/side", "status": "OK"},
                 {"url": "https://kunde.example/side", "status": "ferdig"},
                 {"url": "https://kunde.example/side", "status": ""},
                 {"url": "https://kunde.example/side", "status": 3},
                 {"url": "https://kunde.example/side", "status": True},
                 {"url": "https://kunde.example/side",
                  "status": ["ok"]}):
        with pytest.raises(Motorfeil):
            bygg(_motorresultat(sider=(side,)),
                 payload=_payload(), kontekst=_kontekst())
    # Motsatsen: begge de ekte enumverdiene bæres videre uendret — porten
    # avviser det uleselige, den skriver ikke om det lesbare.
    for status in ("ok", "feilet"):
        r = bygg(_motorresultat(sider=({"url": "https://kunde.example/side",
                                        "status": status},)),
                 payload=_payload(), kontekst=_kontekst())
        assert r["sider_kontrollert"][0]["status"] == status


def test_kvittering_og_rapport_binder_til_samme_vert():
    """Codex P1: kvitteringens `ressurs_id` og rapportens sidebinding er
    ÉN avledning (`oppdragskontrakt.malvert`), ikke to.

    Var de to, holdt det at den ene la på en rotprikk eller store
    bokstaver før plattformen navngav én vert i beviset og en annen i
    evidensen — og da er bindingen pynt.
    """
    from oppdragskontrakt import malvert
    from modules.wcag_audit import OPPDRAGSTYPE
    from modules.wcag_audit.controller import _ressursbinding
    from modules.wcag_audit.rapport import _autorisert_vert
    p = _payload(mal_url="https://KUNDE.example.:443/x?y=1")
    assert _ressursbinding(p) == _autorisert_vert(p) == "kunde.example"
    assert malvert(OPPDRAGSTYPE, p) == "kunde.example"
    # ... og typen er den ENE modulen deklarerer, ikke en gjetning.
    assert malvert("noe.helt.annet", p) is None


def test_vertsmoensteret_slipper_aldri_forbi_skjemaet():
    """Saneringen må aldri være SLAPPERE enn skjemaet.

    Slapp den gjennom noe skjemaet avviser, ville posten blitt en
    ValidationError langt unna i stedet for den dokumenterte
    feil-kvitteringen — nøyaktig den taushet denne runden lukker. Kravet er
    derfor en inklusjon, ikke en likhet: alt `_VERT` godtar, godtar
    skjemaet.

    Mønsteret står GJENTATT i rapport.py, ikke importert: skjemaet er
    innholdsadressert og hashet, og en import ville koblet saneringen til
    et mønster ingen kan endre uten å endre skjemaets identitet. Denne
    testen er bindingen mellom de to i stedet.

    Den ene bevisste asymmetrien er halen: `_VERT` bruker `\\Z`, skjemaet
    `$`, og Pythons `$` matcher OGSÅ rett før en avsluttende linjeskift
    (samme lekkasje `date-time`-formatsjekken dokumenterer). Saneringen er
    altså strengere der, og det er den trygge retningen — «vertsnavn med
    hale» er ikke et vertsnavn.

    Kontroll: gjør `_VERT` slappere enn `_HOSTNAME` (f.eks. tillat `_`), så
    blir denne rød."""
    import re
    from modules.wcag_audit import rapportskjema
    from modules.wcag_audit.rapport import _VERT
    skjemaets = re.compile(rapportskjema._HOSTNAME)
    for v in ("fonts.example", "a.b.c.example", "x1-2.example", "e.xn--p1ai",
              "ikke_en_vert", "-a.example", "a-.example", "enkeltledd",
              "", "a..example", "A.EXAMPLE", "a.example\n", "a.example/x"):
        if _VERT.match(v):
            assert skjemaets.match(v), \
                f"{v!r}: saneringen slipper gjennom noe skjemaet avviser"
    # ... og asymmetrien er NØYAKTIG halen, ikke noe annet.
    assert not _VERT.match("a.example\n") and skjemaets.match("a.example\n")


def test_motorutdata_er_ubetrodd():
    """Port 12/§2: ikke-https-URL og uleselige poster er Motorfeil — aldri
    en rapport. Digester fra motoren finnes ikke som begrep: miljøblokka
    tar KUN serverkontekstens nøkler (port 10)."""
    from modules.wcag_audit.motor import Motorfeil
    from modules.wcag_audit.rapport import bygg
    with pytest.raises(Motorfeil):
        bygg(_motorresultat(sider=({"url": "http://klartekst.example/",
                                    "status": "ok"},)),
             payload=_payload(), kontekst=_kontekst())
    with pytest.raises(Motorfeil):
        bygg(_motorresultat(sider=()), payload=_payload(),
             kontekst=_kontekst())
    with pytest.raises(KeyError):
        # En kontekst uten digest er en konfigurasjonsfeil hos OSS —
        # den skal smelle, ikke fylles fra motorens påstander.
        bygg(_motorresultat(), payload=_payload(),
             kontekst={k: v for k, v in _kontekst().items()
                       if k != "container_image_digest"})
    # Codex P1: et uleselig ANTALL er også ubetrodd inndata. Konverteringen
    # ga ValueError, som controlleren ikke fanger — da hadde unntaket
    # sluppet ut av kjøreløkka og latt oppdraget stå claimet i stedet for
    # å bli kvittert som feilet. Begge tellingene, funn og blokkert.
    for over in ({"funn": ({"regel_id": "r", "alvorlighet": "lav",
                            "antall": "ukjent", "eksempler": []},)},
                 {"blokkert": ({"vert": "f.example", "antall": {"a": 1},
                                "art": "font"},)}):
        with pytest.raises(Motorfeil):
            bygg(_motorresultat(**over), payload=_payload(),
                 kontekst=_kontekst())
    # Codex P1: og en uleselig PORT i URL-en. `urlsplit` godtar strengen —
    # det er `d.port` som kaster, og stod det uttrykket utenfor vakten,
    # var utfallet den samme nakne ValueError ut av kjøreløkka.
    # Kontroll: flytt `port` ut av try-blokka i `_ren_url`, så blir denne
    # rød på ValueError i stedet for å passere på Motorfeil.
    for url in ("https://example.com:not-a-port/", "https://example.com:99999/",
                "https://example.com:-1/"):
        with pytest.raises(Motorfeil):
            bygg(_motorresultat(sider=({"url": url, "status": "ok"},)),
                 payload=_payload(), kontekst=_kontekst())
    # ... men en LOVLIG eksplisitt port skal fortsatt bæres videre.
    # `web_hostname` autoriserer en VERT, ikke et portnummer, så
    # målbindingen skal ikke ta denne. `omfang: "nettsted"` her med vilje:
    # denne testen handler om PORTEN, og enkeltsidebudsjettet er en egen
    # port med sin egen test.
    r = bygg(_motorresultat(sider=({"url": "https://example.com:8443/a?q=1#f",
                                    "status": "ok"},)),
             payload=_payload(mal_url="https://example.com/",
                              omfang="nettsted"),
             kontekst=_kontekst())
    assert r["sider_kontrollert"][0]["url"] == "https://example.com:8443/a"


# --------------------------------------------------------------------------
# Controlleren ende-til-ende med FakeMotor (port 23 + 25s CI-halvdel:
# kjeden bevarer tellingene; motor-ekte fasit måles på staging).
# --------------------------------------------------------------------------

class FakeMotor:
    def __init__(self, resultat=None, feil=None):
        self.resultat, self.feil = resultat, feil
        self.payloads = []
        #: `frist_s` controlleren ga, per kjøring — den er OPPDRAGETS
        #: frist, ikke motorens tak (Codex P1).
        self.frister = []

    def kjor(self, payload, *, frist_s=None):
        from modules.wcag_audit.motor import Motorfeil
        self.payloads.append(payload)
        self.frister.append(frist_s)
        if self.feil:
            raise Motorfeil(self.feil)
        return self.resultat


def _wcag_kjede(migrator_, monkeypatch):
    """Modulkjede + oppdrag for et ALIAS av wcag-typen (unike navn per
    kjøring — den delte testbasen tåler ikke det globale navnet; den EKTE
    registreringen gjøres av deploy-skriptet og prøves på staging)."""
    import oppdragskontrakt as ok
    from modules.wcag_audit import rapportskjema
    u = secrets.token_hex(4)
    typenavn = f"kontroll.w{u}.nettsted"
    at = f"kontroll.w{u}.rapport"
    ekte = ok.OPPDRAGSTYPER["kontroll.wcag.nettsted"]
    monkeypatch.setitem(ok.OPPDRAGSTYPER, typenavn, ok.Oppdragstype(
        navn=typenavn, handlingsprefikser=(f"kontroll.w{u}.",),
        felter=ekte.felter, paakrevde=ekte.paakrevde,
        eiermodul=f"m-{u}", krever_malautorisasjon=True,
        malautorisasjonsdomene="web_hostname",
        # Aliaset skal speile den EKTE typen felt for felt — leses flagget
        # fra `ekte`, kan det aldri gli fra hverandre uten at kjeden her
        # slutter å bevise det den påstår å bevise.
        produserer_artefakt=ekte.produserer_artefakt))

    modul, rel = f"m-{u}", f"r-{u}"
    kh = "k-" + secrets.token_hex(8)
    migrator_.execute("INSERT INTO modulhode (modul_id,status) VALUES"
                      " (%s,'aktiv')", (modul,))
    migrator_.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,1,%s,'p','k','ekstern_lesing','direkte')",
        (modul, kh))
    migrator_.execute(
        "INSERT INTO modulrelease (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,manifest_hash,artifact_digest)"
        " VALUES (%s,%s,1,%s,'mh','ad')", (modul, rel, kh))
    migrator_.execute(
        "INSERT INTO moduldeployment (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,miljo,livslop) VALUES (%s,%s,1,%s,'staging',"
        "'claiming')", (modul, rel, kh))
    migrator_.execute(
        "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
        "kontraktversjon,kontrakt_hash) VALUES (%s,%s,1,%s)",
        (typenavn, modul, kh))
    migrator_.execute(
        # Bytene i `kanonisk`: hashen er over dem, og `skjema` utledes.
        "INSERT INTO artefaktskjema (skjema_hash, kanonisk) VALUES (%s,%s)"
        " ON CONFLICT (skjema_hash) DO NOTHING",
        (rapportskjema.skjema_hash(),
         rapportskjema.kanonisk().decode("utf-8")))
    migrator_.execute(
        "INSERT INTO artefakttype_register (artefakttype,eiermodul,"
        "kontraktversjon,kontrakt_hash,skjema_hash) VALUES (%s,%s,1,%s,%s)",
        (at, modul, kh, rapportskjema.skjema_hash()))
    migrator_.commit()

    # Oppdraget: M-37-forankret (outboxens NOT NULL-trio), payload = den
    # LUKKEDE fire-felts-formen + ressurs_id (som minimeres bort — port 5).
    from db import kryptering
    from .test_m37 import _lag_sak
    sak, logg = _lag_sak(migrator_, TENANT)
    rid = secrets.token_hex(32)
    handling = f"kontroll.w{u}.nettsted"
    _sett_kontekst(migrator_, TENANT)
    migrator_.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant, unntak_id,"
        " repair_operation_id, repair_generation, handler_id,"
        " handler_versjon, maalhandling, input_hash, kategori)"
        " VALUES (%s,%s,%s,0,'wcag','1',%s,%s,'manglende_data')",
        (TENANT, sak, rid, handling, secrets.token_hex(32)))
    beslutning = migrator_.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'test','arbeidskapabilitet','ih2','p@1.0.0/x.y',"
        " 'TILLAT','[]',%s) RETURNING id", (TENANT, rid)).fetchone()[0]
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator_, TENANT)
    payload = {"mal_url": "https://kunde.example/side",
               "kravsett": "wcag21_aa",
               "omfang": "enkeltside", "maks_sider": 1,
               "ressurs_id": "hemmelig-ref"}
    ct, nonce = kryptering.krypter(dek, payload, TENANT, key_id)
    opp = migrator_.execute(
        "INSERT INTO oppdrag (tenant, unntak_id, loggpost_id,"
        " repair_operation_id, oppdragstype, handling, eiermodul,"
        " payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist,"
        " beslutning_loggpost_id, koblingsstatus)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        " now()+interval '30 minutes', now()+interval '30 minutes',"
        " %s,'KOBLET') RETURNING id",
        (TENANT, sak, logg, rid, typenavn, handling, modul, ct, key_id,
         nonce, beslutning)).fetchone()[0]
    migrator_.commit()
    return modul, rel, int(opp)


@pg
def test_controlleren_hele_veien_med_fakemotor(migrator, miljo, monkeypatch):
    """Hele kjeden gjennom EKTE plattform (onboarding → claim m/ token →
    minimert payload (port 5) → rapportbygging → skjemavalidert opplasting →
    signert kvittering → PROMOTERT artefakt). FakeMotor bærer fasiten:
    tellingene inn == tellingene i det promoterte artefaktet."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    from db import kryptering
    from modules.wcag_audit import controller
    from .test_m37 import _signer_kvittering
    from .test_modul_onboarding_http import _onboard_token

    modul, rel, opp = _wcag_kjede(migrator, monkeypatch)
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, modul, rel)
            motor = FakeMotor(resultat=_motorresultat())
            res = controller.kjor_en(c, mtk, motor, _kontekst(),
                                     _signer_kvittering)
            assert res["utfall"] == "utfort", res
            assert res["kvittering_status"] == 200, res
            # Port 5: modulen så KUN de fire payloadfeltene.
            assert set(motor.payloads[0]) == {"mal_url", "kravsett",
                                              "omfang", "maks_sider"}
            _sett_kontekst(migrator, TENANT)
            tilstand, ct, nonce, ref = migrator.execute(
                "SELECT tilstand, ciphertext, nonce, dek_ref FROM artefakt"
                " WHERE artefakt_id=%s", (res["artefakt_id"],)).fetchone()
            assert tilstand == "promotert", tilstand
            dek = kryptering.hent_dek(migrator, TENANT, ref)
            rapport = kryptering.dekrypter(dek, bytes(ct), bytes(nonce),
                                           TENANT, ref)
            migrator.rollback()
            # Fasiten: tellingen fra motoren står ordrett i evidensen.
            assert rapport["sammendrag"]["alvorlig"] == 3
            assert rapport["sider_kontrollert"][0]["url"] \
                == "https://kunde.example/side"
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_motorfeil_gir_avbrutt_uten_artefakt(migrator, miljo, monkeypatch):
    """§10 siste rad: skjemabrudd/motorfeil → oppdraget feiler, INGEN delvis
    artefakt — og plattformen får en kvittering som sier det."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    from modules.wcag_audit import controller
    from .test_m37 import _signer_kvittering
    from .test_modul_onboarding_http import _onboard_token

    modul, rel, opp = _wcag_kjede(migrator, monkeypatch)
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, modul, rel)
            motor = FakeMotor(feil="chromium krasjet")
            res = controller.kjor_en(c, mtk, motor, _kontekst(),
                                     _signer_kvittering)
            assert res["utfall"] == "avbrutt", res
            _sett_kontekst(migrator, TENANT)
            n = migrator.execute(
                "SELECT count(*) FROM artefakt WHERE tenant=%s AND"
                " oppdrag_id=%s", (TENANT, opp)).fetchone()[0]
            migrator.rollback()
            assert n == 0, "motorfeil etterlot et delvis artefakt"
    finally:
        a.tjeneste.pool.lukk()


def test_suksess_uten_artefakt_er_ufullstendig_for_typen():
    """Codex P1: kontraktsiden av «en WCAG-suksess må BÆRE rapporten».

    `er_utforelseskvittering` krever ingen av artefaktfeltene, så en
    vellykket kvittering uten `artefakt_id` var strukturelt lovlig — og i
    endepunktet står HELE artefaktgrenen under `if art_id is not None`.
    Den kvitteringen hoppet altså over promotering, bindingskontroll,
    epoch-sjekk og skjemarevalidering, og falt rett ned i statusskiftet.

    Regelen bor på TYPEN og ikke som en fast liste i `app.py`: det er
    typen som bestemmer om resultatet leveres som artefakt.
    """
    import oppdragskontrakt as ok

    ferdig = {"resultat": "utfort", "artefakt_id": "a-1"}
    naken = {"resultat": "utfort"}
    feilet = {"resultat": "feilet", "feilkode": "motor_avbrutt"}

    assert ok.OPPDRAGSTYPER["kontroll.wcag.nettsted"].produserer_artefakt
    assert ok.mangler_artefaktevidens("kontroll.wcag.nettsted", naken)
    # En eksplisitt `null` er samme mangel som et felt som ikke er der.
    assert ok.mangler_artefaktevidens(
        "kontroll.wcag.nettsted", {"resultat": "utfort", "artefakt_id": None})
    # ... men en FEILET kjøring har per definisjon ingen rapport, og en
    # suksess som bærer artefaktet er nettopp det vi vil ha.
    for kropp in (ferdig, feilet):
        assert not ok.mangler_artefaktevidens("kontroll.wcag.nettsted",
                                              kropp), kropp

    # Typer uten artefakt er HELT urørt — legacy-kvitteringer skal ikke
    # begynne å kreve noe de aldri har hatt.
    assert not ok.OPPDRAGSTYPER["reinnsending"].produserer_artefakt
    for t in ("reinnsending", "verifikasjon", "ukjent.type", None, 5):
        assert not ok.mangler_artefaktevidens(t, naken), t
    assert not ok.mangler_artefaktevidens("kontroll.wcag.nettsted", "ikke dict")

    # Sammenhengen i selve typen: opplastingskapabiliteten til
    # `/v1/artefakt` er modulbundet, så en EIERLØS type kan aldri levere
    # artefaktet den ville blitt krevd for.
    for t in ok.OPPDRAGSTYPER.values():
        assert t.valider() == [], t.navn
    eierlos = ok.Oppdragstype(
        navn="x", handlingsprefikser=("x.",), felter=frozenset(),
        paakrevde=frozenset(), produserer_artefakt=True)
    assert any("produserer_artefakt" in f for f in eierlos.valider())


@pg
def test_suksesskvittering_uten_artefakt_avslutter_ingenting(migrator, miljo,
                                                             monkeypatch):
    """Codex P1, plattformsiden: en buggy controller skal ikke kunne
    avslutte en WCAG-kontroll uten evidens.

    Kvitteringen her er ekte signert, fersk og innenfor fristen — den
    mangler bare `artefakt_id`. Før fiksen hoppet den over hele
    artefaktgrenen og falt rett ned i statusskiftet: `oppdrag.status =
    utfort` og `unntak = løst`, uten en eneste rapport. Ingen alarm, ingen
    karantene — bare et oppdrag som ser ferdig ut.

    Testen krever også at KAPABILITETEN overlever avvisningen: sjekken
    står blant strukturvaktene, altså før forbruket, så controlleren
    beholder sin ene sjanse til å levere resultatet ordentlig.

    Kontroll: fjern `mangler_artefaktevidens`-blokken i kvittering-
    ingesten, så blir kvitteringen godtatt med 200 og oppdraget `utfort`.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app
    from .test_m37 import _signer_kvittering
    from .test_modul_onboarding_http import _onboard_token

    modul, rel, opp = _wcag_kjede(migrator, monkeypatch)
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, modul, rel)
            hode = {"authorization": f"Bearer {mtk}"}
            claim = c.post("/v1/oppdrag/claim", json={}, headers=hode).json()
            basis = {"oppdrag_id": claim["oppdrag_id"],
                     "tenant": claim["tenant"],
                     "kvittering_jti": claim["kvittering_jti"],
                     "repair_operation_id": claim["repair_operation_id"],
                     "owner_claim_id": claim["owner_claim_id"],
                     "owner_generation": claim["owner_generation"],
                     "ressurs_id": "kunde.example"}
            rk = c.post("/v1/oppdrag/kvittering",
                        json=_signer_kvittering({**basis,
                                                 "resultat": "utfort"}),
                        headers=hode)
            assert rk.status_code == 400, rk.text
            assert rk.json()["feil"] == "request_feilformet", rk.text

            _sett_kontekst(migrator, TENANT)
            st, unntak_id = migrator.execute(
                "SELECT status, unntak_id FROM oppdrag WHERE tenant=%s AND"
                " id=%s", (TENANT, opp)).fetchone()
            ust = migrator.execute(
                "SELECT status FROM unntak WHERE tenant=%s AND id=%s",
                (TENANT, unntak_id)).fetchone()[0]
            migrator.rollback()
            assert st == "plukket", st
            assert ust != "løst", ust

            # Kapabiliteten er IKKE brent: den samme kvitterings-jti-en
            # bærer fortsatt et ærlig resultat. (Her: `feilet`, som ikke
            # krever artefakt — en suksess ville trengt en full
            # opplasting, og det er `test_controlleren_hele_veien` sitt
            # ærend.)
            rk2 = c.post("/v1/oppdrag/kvittering",
                         json=_signer_kvittering(
                             {**basis, "resultat": "feilet",
                              "feilkode": "motor_avbrutt"}),
                         headers=hode)
            assert rk2.status_code == 200, rk2.text
            assert rk2.json()["status"] == "feilet", rk2.text
    finally:
        a.tjeneste.pool.lukk()


# --------------------------------------------------------------------------
# Kvitteringssvaret (Codex P1) — ingen Postgres: kjeden mot en stubklient.
# --------------------------------------------------------------------------

class _Svar:
    def __init__(self, status, kropp=None):
        self.status_code, self._kropp = status, kropp or {}

    def json(self):
        return self._kropp

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"uventet {self.status_code}")


class _Stubklient:
    """Claim → opplasting → kvittering, med valgbar kvitteringsstatus.

    Kvitteringskroppen speiler det EKTE endepunktet i `app.py`: 200 bærer
    `status: "utfort"|"feilet"` (statusskiftet skjedde), mens sen evidens
    gir 202 med `lagret_uten_statusendring`. Kroppen er ikke pynt i
    stubben — controlleren leser den nettopp for å skille de to."""

    def __init__(self, kvitteringsstatus, opplastingsstatus=200,
                 kvitteringskropp=None, payload=None, opplasting=...,
                 frist_om_s=30 * 60):
        # Utførelsesfristen claimet bærer, som det EKTE endepunktet:
        # `frist_om_s` sekunder frem i tid, eller `None` for et claim
        # uten lesbart vindu.
        naa = datetime.now(timezone.utc)
        self.utforelsesfrist = (
            None if frist_om_s is None
            else (naa + timedelta(seconds=frist_om_s)).isoformat())
        self.kvittering_utloper = self.utforelsesfrist
        self.kvitteringsstatus = kvitteringsstatus
        self.opplastingsstatus = opplastingsstatus
        self.kvitteringskropp = kvitteringskropp
        self.payload = payload
        # `...` betyr «som vanlig»; `None` betyr at claimen bevisst kom
        # UTEN opplastingskapabilitet.
        self.opplasting = ({"jti": "kap"} if opplasting is ...
                           else opplasting)
        self.kvitteringer = []
        #: Stiene controlleren FAKTISK kalte, i rekkefølge. Testene under
        #: leser den for å bevise hva som IKKE skjedde.
        self.stier = []

    def _kvitteringssvar(self, sendt):
        if self.kvitteringskropp is not None:
            return _Svar(self.kvitteringsstatus, self.kvitteringskropp)
        if self.kvitteringsstatus == 200:
            return _Svar(200, {"status": sendt.get("resultat"),
                               "oppdrag_id": 1, "unntak_id": 1})
        return _Svar(self.kvitteringsstatus, {})

    def post(self, sti, json=None, headers=None):
        self.stier.append(sti)
        if sti == "/v1/artefakt" and self.opplastingsstatus != 200:
            return _Svar(self.opplastingsstatus, {})
        if sti == "/v1/oppdrag/kvittering":
            self.kvitteringer.append(json)
        if sti == "/v1/oppdrag/claim":
            return _Svar(200, {
                "oppdrag_id": 1, "tenant": TENANT, "kvittering_jti": "j",
                "repair_operation_id": "r", "owner_claim_id": "o",
                "owner_generation": 0,
                # Fristene er en del av det EKTE claim-svaret, og
                # controlleren regner motorens vindu ut av dem (Codex
                # P1). Uten dem her ville stubben bevist noe det ekte
                # endepunktet ikke gjør.
                "utforelsesfrist": self.utforelsesfrist,
                "kvittering_utloper": self.kvittering_utloper,
                "payload": self.payload if self.payload is not None else {
                    "mal_url": "https://kunde.example/side",
                    "kravsett": "wcag21_aa", "omfang": "enkeltside"},
                "opplasting": self.opplasting})
        if sti == "/v1/artefakt":
            return _Svar(200, {"artefakt_id": "a-1",
                               "klartekst_sha256": "b" * 64})
        assert sti == "/v1/oppdrag/kvittering", sti
        return self._kvitteringssvar(json or {})


def test_avvist_kvittering_er_ikke_utfort():
    """Codex P1: 409 fra kvitteringsendepunktet (fencing, hashavvik,
    avvist promotering) eller 5xx betyr at oppdraget står IGJEN uferdig hos
    plattformen. Meldte controlleren `utfort` uansett, ville en planlegger
    tro at kjøringen var i havn — modulens ord mot plattformens tilstand.
    Kontroll: fjern _kvittert-sjekken i controlleren, så blir denne rød."""
    from modules.wcag_audit import controller
    motor = FakeMotor(resultat=_motorresultat())
    for status in (409, 500):
        res = controller.kjor_en(_Stubklient(status), "tk", motor,
                                 _kontekst(), lambda k: k)
        assert res["utfall"] == "ukvittert", res
        assert res["kvittering_status"] == status
        # Artefaktet ER lastet opp — utfallet skjuler ikke det, det nekter
        # bare å kalle kjøringen ferdig.
        assert res["artefakt_id"] == "a-1"
    ok = controller.kjor_en(_Stubklient(200), "tk", motor, _kontekst(),
                            lambda k: k)
    assert ok["utfall"] == "utfort", ok


def test_sen_evidens_202_er_ikke_utfort():
    """Codex P1: `2xx` alene var ikke bevis for at oppdraget ble ferdig.

    Fullfører kjøringen etter `utforelsesfrist`, men før `evidensfrist`,
    svarer `/v1/oppdrag/kvittering` 202 med
    `status: "lagret_uten_statusendring"`: evidensen BEVARES, og det er
    hele det som skjedde — `unntak.status` står urørt, og plattformen har
    bevisst latt oppdraget være ufullført. `_kvittert` så bare
    statuskoden, leste 202 som en godtatt kvittering, og controlleren
    meldte `utfall: "utfort"`. Da har modulen sagt at kjøringen var i
    havn om et oppdrag plattformen selv regner som uavsluttet, og
    planleggeren slutter å følge opp noe som aldri ble avsluttet.

    Kontroll: sett `_kvittert` tilbake til `200 <= status < 300`, så blir
    202-en `utfort` igjen.
    """
    from modules.wcag_audit import controller
    motor = FakeMotor(resultat=_motorresultat())

    sen = _Stubklient(202, kvitteringskropp={
        "status": "lagret_uten_statusendring", "oppdrag_id": 1})
    res = controller.kjor_en(sen, "tk", motor, _kontekst(), lambda k: k)
    assert res["utfall"] == "ukvittert", res
    assert res["kvittering_status"] == 202
    # Artefaktet ER lastet opp; utfallet skjuler det ikke.
    assert res["artefakt_id"] == "a-1"

    # En kropp vi ikke kan lese er heller ingen bekreftelse: «vet ikke»
    # skal behandles som uferdig, ikke som ferdig.
    class _Ulesbar(_Stubklient):
        def _kvitteringssvar(self, sendt):
            class _S:
                status_code = 200

                def json(self):
                    raise ValueError("ikke JSON")
            return _S()

    res = controller.kjor_en(_Ulesbar(200), "tk", motor, _kontekst(),
                             lambda k: k)
    assert res["utfall"] == "ukvittert", res

    # ...og det EKTE statusskiftet (200 + status i kroppen) er fortsatt
    # utfort, ellers ville vakten gjort alle kjøringer uferdige.
    ok = controller.kjor_en(_Stubklient(200), "tk", motor, _kontekst(),
                            lambda k: k)
    assert ok["utfall"] == "utfort", ok


def test_gjentatt_kvittering_leses_som_det_den_forrige_gjorde():
    """Codex P2, runde 11: `idempotent` er en dokumentert SUKSESSVEI.

    Kvitteringen er idempotent med vilje — en utfører som mistet svaret
    skal kunne sende NØYAKTIG den samme på nytt. Gjorde den det etter at
    den første hadde avsluttet oppdraget, svarte plattformen 200
    `idempotent`, og controlleren meldte `ukvittert`: modulen påsto at
    plattformen ikke hadde tatt imot kvitteringen for et oppdrag som for
    lengst var `utfort`, og en planlegger som tror på det følger opp noe
    som er avsluttet.

    Men ordet betydde to ting (se `_idempotent_svar` i `api.app`): en
    gjentakelse av en SEN kvittering traff samme gren, og der står
    oppdraget bevisst ufullført. Å legge `idempotent` til uten det skillet
    ville byttet den ene løgnen mot den andre — den farligere av de to.
    Plattformen skiller nå, og denne testen holder BEGGE sidene fast.

    Kontroll: fjern `idempotent` fra `_STATUSSKIFTE` igjen, så blir
    suksessdelen rød; legg `idempotent_uten_statusendring` til, så blir
    sen-delen rød.
    """
    from modules.wcag_audit import controller
    motor = FakeMotor(resultat=_motorresultat())

    gjentatt = _Stubklient(200, kvitteringskropp={
        "status": "idempotent", "oppdrag_id": 1})
    res = controller.kjor_en(gjentatt, "tk", motor, _kontekst(), lambda k: k)
    assert res["utfall"] == "utfort", res

    sen_gjentatt = _Stubklient(200, kvitteringskropp={
        "status": "idempotent_uten_statusendring", "oppdrag_id": 1})
    res = controller.kjor_en(sen_gjentatt, "tk", motor, _kontekst(),
                             lambda k: k)
    assert res["utfall"] == "ukvittert", res

    # Feilveien leser den SAMME regelen (`_feilutfall`): en gjentatt
    # feilkvittering som avsluttet oppdraget er `avbrutt`, en gjentatt sen
    # er det ikke.
    knekt = FakeMotor(feil="motoren feilet")
    res = controller.kjor_en(_Stubklient(200, kvitteringskropp={
        "status": "idempotent", "oppdrag_id": 1}), "tk", knekt,
        _kontekst(), lambda k: k)
    assert res["utfall"] == "avbrutt", res
    res = controller.kjor_en(_Stubklient(200, kvitteringskropp={
        "status": "idempotent_uten_statusendring", "oppdrag_id": 1}), "tk",
        knekt, _kontekst(), lambda k: k)
    assert res["utfall"] == "ukvittert", res


def test_avvist_opplasting_gir_feilkvittering():
    """Codex P1: `ro.raise_for_status()` kastet ut av kjøreløkka når
    plattformen avviste artefaktet (413/400 på 1 MiB-taket, 409 på
    fencing, 5xx). Da fikk plattformen ALDRI vite noe, og oppdraget stod
    claimet til fristen. Kontroll: bytt statussjekken i controlleren
    tilbake til `raise_for_status()`, så blir denne rød."""
    from modules.wcag_audit import controller
    motor = FakeMotor(resultat=_motorresultat())
    for status in (400, 409, 413, 500):
        klient = _Stubklient(200, opplastingsstatus=status)
        res = controller.kjor_en(klient, "tk", motor, _kontekst(),
                                 lambda k: k)
        assert res["utfall"] == "avbrutt", res
        assert res["opplasting_status"] == status
        assert res["kvittert"] is True
        # ... og kvitteringen er FAKTISK sendt, med ærlig feilkode.
        assert len(klient.kvitteringer) == 1
        assert klient.kvitteringer[0]["resultat"] == "feilet"
        assert klient.kvitteringer[0]["feilkode"] == "opplasting_avvist"
        # Aldri et artefakt-id: det finnes ikke noe artefakt å vise til.
        assert "artefakt_id" not in res


def test_hele_bestillingen_leses_for_motoren_startes():
    """Codex P1: bare `mal_url` ble lest før den eksterne skanningen.

    `omfang`, `maks_sider` og `kravsett` ble først sett av `rapport.bygg`
    — altså ETTER at motoren hadde vært ute på kundens nettsted. Et claim
    med `omfang: "alt"`, `maks_sider: 0` eller et ukjent `kravsett` kunne
    aldri gi en rapport som validerer, men det ga observerbar, ekstern
    trafikk mot et nettsted som ikke er vårt, hver eneste gang. For
    `ekstern_lesing` ER den unødvendige forespørselen skaden.

    Kontroll: fjern `_kontraktsbrudd`-blokka i `kjor_en`, så kjører
    FakeMotor på hver av payloadene under, og `motor.payloads` blir
    ikke-tom — altså rød.
    """
    from modules.wcag_audit import controller
    for payload in ({"mal_url": "https://kunde.example/side",
                     "kravsett": "wcag21_aa", "omfang": "alt"},
                    {"mal_url": "https://kunde.example/side",
                     "kravsett": "wcag21_aa", "omfang": "enkeltside",
                     "maks_sider": 0},
                    {"mal_url": "https://kunde.example/side",
                     "kravsett": "wcag21_aa", "omfang": "nettsted",
                     "maks_sider": 200},
                    {"mal_url": "https://kunde.example/side",
                     "kravsett": "wcag21_aa", "omfang": "enkeltside",
                     "maks_sider": True},
                    {"mal_url": "https://kunde.example/side",
                     "kravsett": "wcag22_aaa", "omfang": "enkeltside"},
                    # ... og et påkrevd felt som mangler helt.
                    {"mal_url": "https://kunde.example/side",
                     "omfang": "enkeltside"}):
        motor = FakeMotor(resultat=_motorresultat())
        klient = _Stubklient(200, payload=payload)
        res = controller.kjor_en(klient, "tk", motor, _kontekst(),
                                 lambda k: k)
        # MOTOREN ER ALDRI KJØRT — det er hele poenget.
        assert motor.payloads == [], payload
        assert "/v1/artefakt" not in klient.stier, payload
        # ... og plattformen får en ærlig, FERDIG feilkvittering i stedet
        # for taushet frem til fristen.
        assert res["utfall"] == "avbrutt", res
        assert klient.kvitteringer[0]["resultat"] == "feilet"
        assert klient.kvitteringer[0]["feilkode"] == "oppdrag_ugyldig"

    # Motsatsene: et lovlig oppdrag går som før, også med `maks_sider`
    # både satt og utelatt.
    for payload in ({"mal_url": "https://kunde.example/side",
                     "kravsett": "wcag21_aa", "omfang": "enkeltside"},
                    {"mal_url": "https://kunde.example/side",
                     "kravsett": "wcag21_aa", "omfang": "enkeltside",
                     "maks_sider": 1},
                    {"mal_url": "https://kunde.example/side",
                     "kravsett": "wcag21_aa", "omfang": "nettsted",
                     "maks_sider": 50}):
        motor = FakeMotor(resultat=_motorresultat())
        res = controller.kjor_en(_Stubklient(200, payload=payload), "tk",
                                 motor, _kontekst(), lambda k: k)
        assert res["utfall"] == "utfort", res
        assert motor.payloads == [payload]


def test_bestillingsveien_oppretter_ikke_et_ugyldig_oppdrag():
    """Samme kontrakt, andre enden (Codex P1): verdiene skal stoppe
    oppdraget ved OPPRETTELSEN, ikke bare hos utføreren.

    `minimer` bestemmer feltbredden og `mangler_paakrevde` at de påkrevde
    feltene overlevde — ingen av dem ser på verdien. Et oppdrag med
    `omfang: "alt"` ble derfor opprettet, lagt ut og claimet før noen
    oppdaget at det var dødfødt. Kontrakten er plattformens, og begge
    sider leser den SAMME tabellen: ett sett regler kan ikke gli fra
    hverandre, to sett kan.
    """
    import oppdragskontrakt as ok
    assert ok.bryter_feltkontrakten(
        "kontroll.wcag.nettsted",
        {"mal_url": "https://kunde.example/", "kravsett": "wcag21_aa",
         "omfang": "enkeltside"}) == []
    assert ok.bryter_feltkontrakten(
        "kontroll.wcag.nettsted",
        {"omfang": "alt", "kravsett": "x", "maks_sider": 0}) == [
            "kravsett", "maks_sider", "omfang"]
    # Et felt som MANGLER er ikke et verdibrudd — det er `mangler_paakrevde`
    # sin jobb, og `maks_sider` er lovlig fraværende.
    assert ok.bryter_feltkontrakten("kontroll.wcag.nettsted", {}) == []
    with pytest.raises(ok.Oppdragstypeukjent):
        ok.bryter_feltkontrakten("finnes.ikke", {})

    # Modulens egen `_OMFANG` er SAMME lukkede sett som plattformens.
    # Divergerer de, er den ene porten en annen port enn den andre.
    from modules.wcag_audit import rapport
    assert set(rapport._OMFANG) == set(
        ok.FELTVERDIER["kontroll.wcag.nettsted"]["omfang"])

    # ... og planleggeren LESER tabellen i stedet for bare å telle felter.
    # WCAG-oppdrag går ennå ikke gjennom R1 (`tillatte_malhandlinger`
    # dekker `purring.`/`faktura.`/`melding.`, og §9-bestillingsveien er
    # en egen spec-runde), så vernet vises på den veien som FINNES: en
    # verdiregel for `reinnsending` skal stoppe oppdraget ved
    # opprettelsen, akkurat som WCAG-reglene vil gjøre når den veien
    # åpnes.
    from m37 import reparasjoner
    payload = {"handling": "purring.send", "ressurs_id": "r-1",
               "kategori": "ukjent_kategori"}
    plan = reparasjoner._r1_reinnsending(payload, None)
    assert plan.utfall == "oppdrag", plan
    with mock.patch.dict(ok.FELTVERDIER,
                         {"reinnsending": {"kategori": ("purring",)}},
                         clear=False):
        plan = reparasjoner._r1_reinnsending(payload, None)
    assert plan.utfall == "manuell", plan
    assert plan.grunn == "oppdrag_ugyldig:['kategori']", plan


def test_kvitteringen_bindes_til_verten_som_ble_kontrollert():
    """Codex P1: `payload.get("ressurs_id", "")` ga TOM binding på hver
    eneste WCAG-kvittering.

    Feltet mangler ikke ved et uhell: `oppdragskontrakt` minimerer typen
    til `mal_url`, `kravsett`, `omfang` og `maks_sider` med vilje, så det
    finnes ikke noe `ressurs_id` å hente ut av payloaden. Controlleren
    signerte altså både suksess- og feilkvitteringer med `""`, mens
    plattformen regner `ressurs_id` som en del av `resultathash` — evidens
    uten binding til det som faktisk ble kontrollert.

    Den autoriserte ressursen ER det normaliserte vertsnavnet: det er
    nøyaktig likheten `malbindingsbrudd` krever av hendelsen. Testen
    binder de to sammen i stedet for å gjenta strengen — går kvitteringens
    verdi gjennom plattformens egen målbindingsport, kan ikke modulen
    normalisere annerledes enn porten uten at dette blir rødt.

    Kontroll: sett `ressurs_id` tilbake til
    `payload.get("ressurs_id", "")`, så blir denne rød med tom binding.
    """
    import oppdragskontrakt as ok
    from modules.wcag_audit import controller

    klient = _Stubklient(200)
    motor = FakeMotor(resultat=_motorresultat())
    res = controller.kjor_en(klient, "tk", motor, _kontekst(), lambda k: k)
    assert res["utfall"] == "utfort", res
    kvittering = klient.kvitteringer[0]
    mal = motor.payloads[0]["mal_url"]
    assert kvittering["ressurs_id"] == "kunde.example", kvittering
    assert ok.malbindingsbrudd(
        "kontroll.wcag.nettsted",
        {"mal_url": mal, "ressurs_id": kvittering["ressurs_id"]}) is None

    # Feilveiene bærer SAMME binding — en feilkvittering uten ressurs er
    # like ubundet som en suksesskvittering uten.
    feil = _Stubklient(200)
    controller.kjor_en(feil, "tk", FakeMotor(feil="krasj"), _kontekst(),
                       lambda k: k)
    assert feil.kvitteringer[0]["ressurs_id"] == "kunde.example"

    # Lar målet seg ikke lese, startes motoren ikke i det hele tatt: å
    # kontrollere «noe» og signere en tom binding er nettopp tilstanden
    # fiksen finnes for. Plattformen får en ærlig feilkode i stedet.
    class _UtenMal(_Stubklient):
        def post(self, sti, json=None, headers=None):
            r = super().post(sti, json=json, headers=headers)
            if sti == "/v1/oppdrag/claim":
                kropp = r.json()
                return _Svar(200, {**kropp,
                                   "payload": {**kropp["payload"],
                                               "mal_url": "http://x/"}})
            return r

    stum = FakeMotor(resultat=_motorresultat())
    blind = _UtenMal(200)
    res = controller.kjor_en(blind, "tk", stum, _kontekst(), lambda k: k)
    assert res["grunn"] == "malbinding_mangler", res
    assert stum.payloads == [], "motoren kjørte uten en bindbar vert"
    assert blind.kvitteringer[0]["feilkode"] == "malbinding_mangler"


def test_manglende_opplastingskapabilitet_stopper_for_skanningen():
    """Codex P2: en levering vi VET er umulig skal ikke koste kundens
    nettsted en full kontroll.

    Gir claim-API-et bevisst ingen `opplasting`-kapabilitet — fordi
    artefakttypen mangler, er tvetydig eller er filtrert bort for
    deploymenten — kunne rapporten aldri blitt levert. Sjekken lå likevel
    ETTER `motor.kjor()`: controlleren crawlet hele nettstedet, bygget
    rapporten, og kastet den så på en betingelse den kunne lest av
    claimet før første forespørsel. `ekstern_lesing` er observerbar
    trafikk mot noen andres nettsted, og da er den unødvendige
    forespørselen selve skaden.

    Kontroll: flytt `opplasting`-blokka i `kjor_en` tilbake under
    `try`-blokka, så kjører FakeMotor og `motor.payloads` blir ikke-tom.
    """
    from modules.wcag_audit import controller
    for uten in (None, {}):
        motor = FakeMotor(resultat=_motorresultat())
        klient = _Stubklient(200, opplasting=uten)
        res = controller.kjor_en(klient, "tk", motor, _kontekst(),
                                 lambda k: k)
        assert motor.payloads == [], uten
        assert klient.stier == ["/v1/oppdrag/claim", "/v1/oppdrag/kvittering"]
        assert res["utfall"] == "avbrutt", res
        assert res["grunn"] == "ingen_kapabilitet", res
        assert klient.kvitteringer[0]["feilkode"] == (
            "ingen_opplastingskapabilitet")


def test_ugyldig_serverkontekst_stopper_for_skanningen():
    """Codex P2: `bygg` leser `kontekst[k]` — controlleren leste den aldri.

    De seks `miljo`-feltene er controllerens EGEN konfigurasjon, og en
    feilkonfigurert deployment ga to utfall, begge etter at motoren hadde
    vært ute på kundens nettsted:

      * manglende `container_image_digest` → naken `KeyError` fra `bygg`.
        `kjor_en` fanger kun Motorfeil og ValidationError, så oppdraget
        ble stående claimet og ufullført til fristen, uten et ord.
      * ugyldig digest → oppdaget først av skjemavalideringen, altså
        etter en full, observerbar kontroll som aldri kunne bli levert.

    Kontroll: fjern `_kontekstbrudd`-blokka i `kjor_en`, så blir første
    tilfelle en KeyError ut av `kjor_en` og andre tilfelle grønt med
    `motor.payloads != []`.
    """
    from modules.wcag_audit import controller

    uten_digest = {k: v for k, v in _kontekst().items()
                   if k != "container_image_digest"}
    for kontekst, brudd in (
            (uten_digest, "mangler container_image_digest"),
            ({**_kontekst(), "container_image_digest": "sha256:kort"},
             "container_image_digest:pattern"),
            ({**_kontekst(), "axe_versjon": ""}, "axe_versjon:minLength"),
            ({**_kontekst(), "viewport": 1280}, "viewport:type"),
            ("ikke et objekt", "kontekst er ikke et objekt")):
        motor = FakeMotor(resultat=_motorresultat())
        klient = _Stubklient(200)
        res = controller.kjor_en(klient, "tk", motor, kontekst, lambda k: k)
        assert motor.payloads == [], brudd
        assert klient.stier == ["/v1/oppdrag/claim", "/v1/oppdrag/kvittering"]
        assert res["utfall"] == "avbrutt", res
        assert res["grunn"] == f"kontekst_ugyldig:{brudd}", res
        assert klient.kvitteringer[0]["feilkode"] == "kontekst_ugyldig"

    # Grunnen bærer feltet og nøkkelordet, ALDRI verdien: den havner i
    # driftsloggen, og en digest hører ikke hjemme der.
    hemmelig = "sha256:" + "b" * 63
    res = controller.kjor_en(
        _Stubklient(200), "tk", FakeMotor(resultat=_motorresultat()),
        {**_kontekst(), "container_image_digest": hemmelig}, lambda k: k)
    assert hemmelig not in res["grunn"]

    # Den lovlige konteksten står: porten er ikke et nytt hinder for en
    # riktig konfigurert utfører.
    ok = _Stubklient(200)
    assert controller.kjor_en(ok, "tk", FakeMotor(resultat=_motorresultat()),
                              _kontekst(), lambda k: k)["utfall"] == "utfort"


def test_avvist_feilkvittering_er_heller_ikke_ferdig():
    """Codex P1: feilgrenene meldte `avbrutt` uansett hva plattformen
    svarte på feil-kvitteringen.

    `avbrutt` betyr FERDIG mislykket. Blir feil-kvitteringen avvist med
    409/5xx — eller lagret som sen evidens med 202 — er det nettopp det
    plattformen IKKE har bekreftet: oppdraget står fortsatt claimet og
    uferdig der, akkurat som når en suksesskvittering blir avvist.
    Suksessgrenen leste `_kvittert`, feilgrenene gjorde det ikke, og
    forskjellen var vilkårlig: en planlegger som tror på `avbrutt` slutter
    å følge et oppdrag som aldri ble avsluttet.

    Kontroll: sett feilgrenene tilbake til `{"utfall": "avbrutt", ...}`, så
    blir denne rød — `ukvittert` blir `avbrutt` igjen på alle tre veier.
    """
    from modules.wcag_audit import controller
    ok_motor = FakeMotor(resultat=_motorresultat())

    class _UtenKapabilitet(_Stubklient):
        def post(self, sti, json=None, headers=None):
            r = super().post(sti, json=json, headers=headers)
            if sti == "/v1/oppdrag/claim":
                return _Svar(200, {**r.json(), "opplasting": None})
            return r

    # De tre feilveiene: motorfeil, ingen opplastingskapabilitet, avvist
    # opplasting — hver med en feil-kvittering plattformen ikke godtok.
    veier = (
        ("Motorfeil", lambda s: (_Stubklient(s), FakeMotor(feil="krasj"))),
        ("ingen_kapabilitet", lambda s: (_UtenKapabilitet(s), ok_motor)),
        ("opplasting_avvist",
         lambda s: (_Stubklient(s, opplastingsstatus=413), ok_motor)))

    for grunn, lag in veier:
        for status in (409, 500):
            klient, motor = lag(status)
            res = controller.kjor_en(klient, "tk", motor, _kontekst(),
                                     lambda k: k)
            assert res["utfall"] == "ukvittert", (grunn, status, res)
            assert res["kvittert"] is False, (grunn, res)
            # Grunnen står uansett: hvorfor kjøringen feilet er like sant
            # om kvitteringen kom frem eller ikke.
            assert res["grunn"] == grunn, res
            assert len(klient.kvitteringer) == 1, grunn

        # Sen evidens (202) er samme sak: evidensen er bevart, men
        # plattformen har bevisst latt oppdraget være ufullført.
        klient, motor = lag(202)
        klient.kvitteringskropp = {"status": "lagret_uten_statusendring"}
        res = controller.kjor_en(klient, "tk", motor, _kontekst(),
                                 lambda k: k)
        assert res["utfall"] == "ukvittert", (grunn, res)

        # ... og en godtatt feil-kvittering er fortsatt `avbrutt`, ellers
        # ville vakten gjort alle feilede kjøringer uavklarte.
        klient, motor = lag(200)
        res = controller.kjor_en(klient, "tk", motor, _kontekst(),
                                 lambda k: k)
        assert res["utfall"] == "avbrutt", (grunn, res)
        assert res["kvittert"] is True, (grunn, res)


def test_rapporten_holdes_under_1_mib():
    """Codex P1: antallsgrensene alene holder ikke 1 MiB-taket — 500 funn
    à ti 200-tegns eksempler passerer skjemaet og blir avvist av
    `/v1/artefakt`. Rapporten måles nå med SERVERENS kanonisering og
    kappes ærlig før opplasting. Kontroll: fjern `_under_taket`-kallet i
    `bygg`, så blir denne rød."""
    import jsonschema
    from modules.wcag_audit import rapportskjema
    from modules.wcag_audit.rapport import (MAKS_BYTES, _kanoniske_bytes,
                                            bygg)

    # Verstefallsrapporten: maks funn, maks eksempler, maks selektorlengde.
    stor = tuple({"regel_id": f"regel-{i:04d}" + "x" * 110,
                  "alvorlighet": "alvorlig", "antall": 3,
                  "eksempler": [f"#n{i}-{j}" + "s" * 190 for j in range(10)]}
                 for i in range(500))
    r = bygg(_motorresultat(funn=stor), payload=_payload(),
             kontekst=_kontekst())
    jsonschema.Draft202012Validator(rapportskjema.SKJEMA).validate(r)
    assert len(_kanoniske_bytes(r)) <= MAKS_BYTES
    assert r["avkortet"]["truffet"] is True
    # SAMMENDRAGET er urørt: kappingen gjelder listene, ikke sannheten om
    # omfanget (500 funn à 3 forekomster).
    assert r["sammendrag"]["alvorlig"] == 1500
    # En normal rapport røres ikke.
    liten = bygg(_motorresultat(funn=(
        {"regel_id": "r1", "alvorlighet": "lav", "antall": 1,
         "eksempler": ["#a"]},)),
        payload=_payload(), kontekst=_kontekst())
    assert liten["funn"][0]["eksempler"] == ["#a"]
    assert liten["avkortet"]["truffet"] is False


# --------------------------------------------------------------------------
# `format` er en REGEL, ikke en annotasjon — Codex P2.
# --------------------------------------------------------------------------

def test_formatsjekk_avviser_ugyldig_kjort_ts():
    """Draft202012Validator behandler `format` som annotasjon uten en
    format-checker, så rapportskjemaets `kjort_ts: {format: date-time}` var
    ren dokumentasjon: `"i går"` passerte begge de annonserte
    valideringspunktene og ble promotert. Kontroll: fjern
    format_checker-argumentet i `valider`, så slipper alle de ugyldige
    gjennom."""
    from api.artefaktskjema import valider
    from modules.wcag_audit import rapportskjema
    from modules.wcag_audit.rapport import bygg
    rapport = bygg(_motorresultat(), payload=_payload(),
                   kontekst=_kontekst())
    assert not valider(rapportskjema.SKJEMA, rapport)
    # Små t/z er RFC 3339 (§5.6) og skal fortsatt passere.
    assert not valider(rapportskjema.SKJEMA,
                       {**rapport, "kjort_ts": "2026-08-17t21:03:32z"})
    for ugyldig in ("i går", "2026-08-17", "2026-08-17T21:03:32",
                    "2026-02-31T00:00:00Z", "2026-08-17T25:00:00Z",
                    "2026-08-17T21:03:32+00:00\n"):
        feil = valider(rapportskjema.SKJEMA, {**rapport,
                                              "kjort_ts": ugyldig})
        assert any("kjort_ts" in f for f in feil), (ugyldig, feil)
    # Den DELTE, globale checkeren skal ikke være endret av importen — vi
    # eier vår egen kopi.
    import jsonschema
    assert "date-time" not in \
        jsonschema.Draft202012Validator.FORMAT_CHECKER.checkers


def test_odelagt_skjema_avvises_i_stedet_for_aa_kaste():
    """Codex P2: `registrer_artefaktskjema` sjekker bare at JSON-en er et
    objekt, så `{"type": "strng"}` kunne registreres og bindes til en
    artefakttype. Da døde HVER opplastning og promotering på et ufanget
    UnknownType fra validatoren — og fordi både skjemaraden og
    typebindingen er immutable, kunne typen aldri repareres. Metasjekken
    kjøres på begge sider av den udødelige raden. Kontroll: fjern
    skjemafeil-kallet i `valider`, så kaster denne i stedet for å avvise."""
    from api.artefaktskjema import skjemafeil, valider
    assert skjemafeil({"type": "strng"})
    assert skjemafeil({"required": "ikke-en-liste"})
    assert not skjemafeil({"type": "object"})
    # Registreringsveien kjører NØYAKTIG denne sjekken på det skjemaet den
    # er i ferd med å gjøre udødelig.
    from modules.wcag_audit import rapportskjema
    assert not skjemafeil(rapportskjema.SKJEMA)
    # ... og kom et ødelagt skjema likevel inn, er svaret en feilliste.
    feil = valider({"type": "strng"}, {"a": 1})
    assert feil and "JSON Schema" in feil[0]


def test_uloselig_referanse_er_en_avvisning_ikke_en_500():
    """Codex P2: `check_schema` sier ingenting om at en `$ref` treffer noe.

    `{"$ref": "#/$defs/missing"}` er et metagyldig skjema. Først når
    validatoren FØLGER referansen — midt i en opplasting — slår oppslaget
    feil, og da med et unntak, ikke en valideringsfeil: det gikk rett forbi
    `valider` og ble en 500-er. Både skjemaraden og typebindingen er
    immutable, så én slik registrering gjorde artefakttypen til en
    permanent 500-er.

    En referanse UT av dokumentet avvises også, og av strengere grunner enn
    at oppslaget kan ryke: skjemaet er innholdsadressert, så samme udødelige
    hash kunne validert to forskjellige ting i dag og i morgen — og
    oppslaget er en nettverksforespørsel fra serveren, utløst av innsendt
    innhold.

    Kontroll: fjern `_referansefeil`-kallet i `skjemafeil` og
    `_OPPSLAGSFEIL`-fangsten i `valider`, så kaster de to
    `valider`-kallene under i stedet for å avvise.
    """
    from api.artefaktskjema import skjemafeil, valider

    for skjema in ({"type": "object",
                    "properties": {"a": {"$ref": "#/$defs/missing"}}},
                   {"$ref": "#/$defs/borte"},
                   {"$ref": "#finnes-ikke"},
                   {"$defs": {"t": {"type": "string"}},
                    "properties": {"a": {"$ref": "#/$defs/t/gikk-for-langt"}}},
                   {"$ref": "https://et-sted.example/s.json"},
                   {"properties": {"a": {"$ref": "annen-fil.json"}}}):
        assert skjemafeil(skjema), skjema
        # ... og kom raden likevel inn (eldre enn sjekken, eller satt inn
        # med direkte SQL), er svaret fortsatt en feilliste.
        feil = valider(skjema, {"a": 1})
        assert feil and "skjema" in feil[0], skjema

    # EKTE referanser står. Sjekken skal ikke gjøre et brukbart skjema
    # uregistrerbart — den avvisningen er like udødelig som godkjenningen.
    gyldig = {"$defs": {"t": {"type": "string"}}, "type": "object",
              "properties": {"a": {"$ref": "#/$defs/t"}}}
    assert not skjemafeil(gyldig)
    assert not valider(gyldig, {"a": "x"})
    assert valider(gyldig, {"a": 1})

    # Rekursjon (`$ref` tilbake til roten), `$anchor` og `~1`-escapede
    # pekere er alle lovlige oppslag inni dokumentet.
    rekursiv = {"$defs": {"n": {"type": "object",
                                "properties": {"barn": {"$ref": "#/$defs/n"}}}},
                "$ref": "#/$defs/n"}
    assert not skjemafeil(rekursiv)
    assert not valider(rekursiv, {"barn": {"barn": {}}})
    assert valider(rekursiv, {"barn": 3})
    anker = {"$defs": {"t": {"$anchor": "T", "type": "string"}},
             "properties": {"a": {"$ref": "#T"}}}
    assert not skjemafeil(anker)
    assert not valider(anker, {"a": "x"})
    assert not skjemafeil({"$defs": {"a/b": {"type": "string"}},
                           "properties": {"x": {"$ref": "#/$defs/a~1b"}}})
    # Rot-`$id` flytter ingen base og står; en `$id` UNDER roten gjør det,
    # og da sier sjekken fra i stedet for å måle noe annet enn validatoren.
    assert not skjemafeil({"$id": "https://a.example/rot",
                           "$defs": {"t": {"type": "string"}},
                           "properties": {"a": {"$ref": "#/$defs/t"}}})
    assert skjemafeil({"$id": "https://a.example/rot",
                       "$defs": {"t": {"$id": "under"}}})

    # Codex P2, runde 2: AT PEKEREN TREFFER NOE ER IKKE NOK — den må
    # treffe en SKJEMAPOSISJON. `{"x": "ikke et skjema", "$ref": "#/x"}`
    # metavalideres (`x` er et ukjent nøkkelord, altså en annotasjon
    # `check_schema` ikke ser på), og en peker-eksisterer-sjekk slapp det
    # gjennom. Så leser `jsonschema` strengen som et skjema og kaster
    # AttributeError — ikke blant `_OPPSLAGSFEIL` — og den permanente
    # 500-eren er tilbake i en udødelig rad. Kontroll: bytt
    # `posisjoner`-medlemskapet i `_referansefeil` mot en sjekk på at
    # noden bare finnes, så kaster de fire `valider`-kallene under.
    for umetavalidert in ({"x": "ikke et skjema", "$ref": "#/x"},
                          {"x": {"type": "strng"}, "$ref": "#/x"},
                          {"const": {"a": {}},
                           "properties": {"p": {"$ref": "#/const/a"}}},
                          {"enum": [{"type": "string"}],
                           "properties": {"p": {"$ref": "#/enum/0"}}}):
        assert skjemafeil(umetavalidert), umetavalidert
        feil = valider(umetavalidert, {"p": 1})
        assert feil and "skjema" in feil[0], umetavalidert

    # ... men en ekte skjemaposisjon står, også når den er `true`/`false`
    # (som ER skjemaer) eller ligger på en listeindeks.
    bool_ref = {"$defs": {"alt": True},
                "properties": {"p": {"$ref": "#/$defs/alt"}}}
    assert not skjemafeil(bool_ref)
    assert not valider(bool_ref, {"p": 1})
    indeks = {"prefixItems": [{"type": "string"}],
              "properties": {"p": {"$ref": "#/prefixItems/0"}}}
    assert not skjemafeil(indeks)
    assert not valider(indeks, {"p": "x"})
    assert valider(indeks, {"p": 1})
    # En ikke-kanonisk indeks er ingen posisjon.
    assert skjemafeil({"prefixItems": [{"type": "string"}],
                       "properties": {"p": {"$ref": "#/prefixItems/00"}}})

    # `$ref` som DATA er ikke en referanse. En blind rekursjon over all
    # JSON ville avvist disse to — og den falske avvisningen er like
    # endelig som en falsk godkjenning, siden raden er udødelig.
    konst = {"properties": {"a": {"const": {"$ref": "https://x.example/y"}}}}
    assert not skjemafeil(konst)
    assert not valider(konst, {"a": {"$ref": "https://x.example/y"}})
    feltnavn = {"properties": {"$ref": {"type": "string"}}}
    assert not skjemafeil(feltnavn)
    assert not valider(feltnavn, {"$ref": "x"})

    # Registreringsveien kjører nøyaktig denne sjekken, så det plattformen
    # allerede har gjort udødelig må fortsatt passere.
    from modules.wcag_audit import rapportskjema
    assert not skjemafeil(rapportskjema.SKJEMA)


@pg
def test_metasjekken_staar_paa_den_delte_registreringsveien(migrator):
    """Codex P2, runde 2: metasjekken lå bare i WCAG-deploy-skriptet, altså
    hos ÉN kaller — mens begge admin-rollene fortsatt har EXECUTE på
    `registrer_artefaktskjema`. Et fremtidig deploy-verktøy eller en
    direkte SQL-kaller kunne registrere `{"type": "strng"}`, binde en
    immutabel artefakttype til den, og gjøre hver eneste opplastning til en
    valideringsfeil — uten vei tilbake.

    Nå står gaten to steder som ikke kan omgås hver for seg:
    `api.artefaktskjema.registrer` er DEN delte Python-veien, og
    `_artefaktskjema_typefeil` er SQL-sidens egen vakt.

    Kontroll: fjern `_artefaktskjema_typefeil`-kallet i
    `registrer_artefaktskjema`, så blir SQL-halvdelen under grønn på et
    skjema som aldri kan brukes.
    """
    from api.artefaktskjema import Skjemaugyldig, registrer, skjemafeil
    from db.pg import koble

    # 1) Python-veien avviser FØR den rører databasen.
    c = _mk_admin("disponit_modules_admin")
    try:
        with pytest.raises(Skjemaugyldig):
            registrer(c, {"type": "strng"}, "test")
        c.rollback()
        # ... og den lykkelige veien gir samme hash som innholdsadressen.
        unikt = {"type": "object",
                 "properties": {"a": {"type": "string",
                                      "x": secrets.token_hex(3)}}}
        _, forventet = _jcs_hash(unikt)
        assert registrer(c, unikt, "test") == forventet
        c.commit()
    finally:
        c.close()

    # 2) SQL-veien avviser den direkte kalleren som aldri så Python.
    d = _mk_admin("disponit_modules_admin")
    try:
        for daarlig in ({"type": "strng"},
                        {"type": ["object", "strng"]},
                        {"type": 7},
                        {"type": []},
                        {"type": ["object", "object"]},
                        {"properties": {"a": {"type": "objekt"}}},
                        {"$defs": {"d": {"type": "tall"}}},
                        {"allOf": [{"type": "object"}, {"type": "strng"}]},
                        {"items": {"type": "strng"}},
                        {"if": {"type": "strng"}},
                        # Runde 3: nøkkelord med feil VERDITYPE. Hvert av
                        # disse er et lovlig JSON-objekt som `check_schema()`
                        # avviser — altså samme permanente skade som en
                        # ugyldig `type`, og alle gikk gjennom SQL-veien.
                        {"required": "resultat"},
                        {"required": ["a", "a"]},
                        {"required": [1]},
                        {"minLength": "x"},
                        {"maxItems": -1},
                        {"minItems": 1.5},
                        {"multipleOf": 0},
                        {"uniqueItems": "ja"},
                        {"pattern": 7},
                        {"title": []},
                        {"enum": "abc"},
                        {"dependentRequired": {"a": "b"}},
                        {"$vocabulary": {"u": "ja"}},
                        # ... og bæreren av subskjemaer med feil form:
                        # vakten hoppet STILLE over disse.
                        {"properties": "x"},
                        {"allOf": "x"},
                        {"allOf": []},
                        {"properties": {"a": {"required": "b"}}}):
            # Parity: SQL-vakten skal treffe NØYAKTIG det Python-vakten
            # treffer. Går de to fra hverandre, er den ene veien enten et
            # hull eller en avvisning av et lovlig skjema.
            assert skjemafeil(daarlig), daarlig
            kanon, h = _jcs_hash(daarlig)
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                d.execute("SELECT registrer_artefaktskjema(%s,%s,'test')",
                          (kanon, h))
            d.rollback()
        # Motsatsen — og den er poenget med å følge de EKTE
        # subskjema-stedene i stedet for et naivt `$.**.type`: et felt som
        # tilfeldigvis HETER «type» er ikke nøkkelordet `type`, og et slikt
        # skjema er fullt lovlig.
        for godt in ({"type": ["object", "null"]},
                     {"properties": {"type": {"type": "string"}}},
                     {"additionalProperties": False,
                      "properties": {"a": {"type": "integer"}}},
                     {"prefixItems": [{"type": "string"}], "items": True},
                     # Riktige verdityper for de samme nøkkelordene — og et
                     # UKJENT nøkkelord, som Draft 2020-12 sier skal
                     # ignoreres. Avviste vakten det, ville SQL-veien blitt
                     # strengere enn Python-veien: et nytt avvik, motsatt vei.
                     {"required": ["a"], "minLength": 0, "maxItems": 3,
                      "multipleOf": 2.5, "uniqueItems": True,
                      "pattern": "^a$", "title": "t", "enum": [1, "a"],
                      "dependentRequired": {"a": ["b"]},
                      "ukjentNokkelord": {"hva": "som helst"}},
                     {"properties": {"a": {"required": ["b"]}}},
                     dict(_rapportskjema_kopi(), x=secrets.token_hex(3))):
            assert not skjemafeil(godt), godt
            kanon, h = _jcs_hash(godt)
            assert d.execute("SELECT registrer_artefaktskjema(%s,%s,'test')",
                             (kanon, h)).fetchone()[0] == h
        d.commit()
    finally:
        d.close()

    # 3) Det ødelagte skjemaet ble ALDRI en rad — ingen artefakttype kan
    #    bindes til noe som ikke finnes.
    r = koble(DSN)
    try:
        _, h_daarlig = _jcs_hash({"type": "strng"})
        assert r.execute("SELECT count(*) FROM artefaktskjema"
                         " WHERE skjema_hash=%s",
                         (h_daarlig,)).fetchone()[0] == 0
        r.rollback()
    finally:
        r.close()


@pg
def test_skjemaregistreringen_bevarer_hvem_som_publiserte(migrator):
    """Codex P2: `registrer_artefaktskjema` TOK `p_aktor` og kastet den.

    Raden bar skjemaet og et tidsstempel, ingenting om hvem. Skjemaraden
    er udødelig og kan senere bli den permanente valideringskontrakten for
    en artefakttype — bindingen ingen kan angre — og da er «hvilken
    administrator publiserte dette» spørsmålet en driftsperson faktisk
    sitter med når en type oppfører seg uventet.

    Sporet er `modulregister_hendelse` (append-only i 014), skrevet i
    SAMME transaksjon som innsettingen: en registrering uten spor, eller
    et spor uten registrering, ville begge vært en løgn om hva som
    skjedde.

    Kontroll: fjern hendelses-INSERT-en i `registrer_artefaktskjema`, så
    finner ikke denne aktøren igjen.
    """
    from api.artefaktskjema import registrer
    from db.pg import koble

    def _hendelser(h):
        r = koble(MIGRATOR_DSN)
        try:
            return r.execute(
                "SELECT aktor FROM modulregister_hendelse"
                "  WHERE hendelse = 'artefaktskjema_registrert'"
                "    AND detalj->>'skjema_hash' = %s ORDER BY id",
                (h,)).fetchall()
        finally:
            r.close()

    unikt = {"type": "object", "x": secrets.token_hex(4)}
    kanon, h = _jcs_hash(unikt)
    c = _mk_admin("disponit_modules_admin")
    try:
        # INGEN hendelse før registreringen — den kommer FRA den.
        assert _hendelser(h) == []
        assert c.execute("SELECT registrer_artefaktskjema(%s,%s,%s)",
                         (kanon, h, "ada@example")).fetchone()[0] == h
        # Hendelsen er ikke synlig for andre før innsettingen er det: én
        # transaksjon, ett utfall.
        assert _hendelser(h) == []
        c.commit()
        assert _hendelser(h) == [("ada@example",)]

        # Den IDEMPOTENTE gjentakelsen publiserer ingenting nytt, og skal
        # ikke flytte svaret på hvem som publiserte skjemaet til den siste
        # som kjørte deployet på nytt.
        c.execute("SELECT registrer_artefaktskjema(%s,%s,%s)",
                  (kanon, h, "bo@example"))
        c.commit()
        assert _hendelser(h) == [("ada@example",)]

        # Den delte Python-veien er den samme funksjonen, og bærer aktøren
        # like langt.
        annet = {"type": "object", "x": secrets.token_hex(4)}
        h2 = registrer(c, annet, "cara@example")
        c.commit()
        assert _hendelser(h2) == [("cara@example",)]
    finally:
        c.close()

    # Et AVVIST skjema etterlater heller ingen hendelse: sporet beskriver
    # rader som finnes, ikke forsøk som ble stoppet.
    daarlig = {"type": "strng", "x": secrets.token_hex(4)}
    kanon_d, h_d = _jcs_hash(daarlig)
    d = _mk_admin("disponit_modules_admin")
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            d.execute("SELECT registrer_artefaktskjema(%s,%s,%s)",
                      (kanon_d, h_d, "dan@example"))
        d.rollback()
    finally:
        d.close()
    assert _hendelser(h_d) == []


def _rapportskjema_kopi() -> dict:
    """Det EKTE rapportskjemaet — den strengeste prøven på at vakten ikke
    gir falske treff: går den rød her, ville PR-014c ikke kunnet deployes."""
    from modules.wcag_audit import rapportskjema
    return dict(rapportskjema.SKJEMA)


# --------------------------------------------------------------------------
# Kommandomotoren mot en EKTE (og fiendtlig) underprosess — Codex P1.
# --------------------------------------------------------------------------

def _motorkommando(kropp):
    return [sys.executable, "-c", kropp]


def test_motorutdata_er_bundet_i_minnet():
    """Codex P1: `capture_output=True` bufret stdout og stderr uten tak i
    opptil en time, i den CREDENTIAL-bærende prosessen. Rapportens senere
    1 MiB-grense hjelper ikke: minnet er brukt før JSON-parsingen. En
    motor som spyr ut data skal møte Motorfeil, ikke spise
    controllerhosten. Kontroll: bytt tilbake til subprocess.run med
    capture_output, så henger denne testen på minne i stedet for å bestå."""
    from modules.wcag_audit.motor import (Kommandomotor, Motorfeil,
                                          MAKS_STDOUT)
    god = json.dumps({"regelsett_versjon": "axe-4.10", "varighet_ms": 5,
                      "sider": [{"url": "https://a.example/",
                                 "status": "ok"}],
                      "funn": [], "blokkert": [],
                      "avkortet": [False, None, None]})

    # Lykkelig vei: payloaden når stdin, JSON-en leses tilbake.
    m = Kommandomotor(_motorkommando(
        "import sys,json;d=json.load(sys.stdin);assert d['mal_url'];"
        "sys.stdout.write(%r)" % god))
    r = m.kjor({"mal_url": "https://kunde.example/"})
    assert r.regelsett_versjon == "axe-4.10" and r.varighet_ms == 5

    # Uendelig stdout: avbrytes ved taket, ikke ved minnetaket til hosten.
    uendelig = Kommandomotor(_motorkommando(
        "import sys\nwhile True: sys.stdout.buffer.write(b'x'*65536)"),
        tidsavbrudd_s=30)
    with pytest.raises(Motorfeil, match=str(MAKS_STDOUT)):
        uendelig.kjor({})

    # Mye stderr: dreneres (ellers vranglåser motoren på full rørbuffer),
    # og bare en snipp beholdes til feilmeldingen.
    prat = Kommandomotor(_motorkommando(
        "import sys\nfor i in range(400): sys.stderr.buffer.write(b'e'*65536)"
        "\nsys.exit(3)"), tidsavbrudd_s=60)
    with pytest.raises(Motorfeil, match="motor exit 3") as ei:
        prat.kjor({})
    assert len(str(ei.value)) < 400, "stderr slapp inn i meldingen ubundet"

    # Fristen bæres av vakthunden, også når motoren har lukket stdout og
    # lever videre — den veien hang tidligere til timeouten uansett.
    for kropp in ("import time;time.sleep(300)",
                  "import sys,os,time;sys.stdout.write(%r);"
                  "sys.stdout.flush();os.close(1);time.sleep(300)" % god):
        treg = Kommandomotor(_motorkommando(kropp), tidsavbrudd_s=2)
        with pytest.raises(Motorfeil, match="TimeoutExpired"):
            treg.kjor({})


def test_regelsettversjon_fra_motoren_ma_vaere_en_streng():
    """Codex P1: `str(...)` fant på en versjon i stedet for å feile.

    `regelsett_versjon: null` ble `"None"`, `4.10` ble `"4.1"`, og et
    objekt ble `"{'a': 1}"`. Alle tre passerer skjemaets `minLength: 1`,
    så rapporten ble PROMOTERT med en fabrikkert versjon. Versjonen er
    hele proveniensen: den sier hvilke regler evidensen ble målt mot, og
    er det som gjør en kontroll etterprøvbar. En leser som ser `None` tror
    det står noe der.

    Kontroll: bytt tilbake til `str(d["regelsett_versjon"])[:64]`, så
    består motoren under med `"None"` i stedet for å gi Motorfeil.
    """
    from modules.wcag_audit.motor import (Kommandomotor, Motorfeil,
                                          regelsettversjon)

    for daarlig in (None, 4.10, {"a": 1}, ["axe"], True, "", "   "):
        with pytest.raises(Motorfeil, match="regelsett_versjon"):
            regelsettversjon(daarlig)

    # Hele veien gjennom motoren: `null` skal bli Motorfeil, ikke "None".
    null = json.dumps({"regelsett_versjon": None, "varighet_ms": 5})
    m = Kommandomotor(_motorkommando("import sys;sys.stdout.write(%r)" % null))
    with pytest.raises(Motorfeil, match="regelsett_versjon"):
        m.kjor({})

    # Ekte versjoner går igjennom, trimmes, og kappes fortsatt ved 64.
    assert regelsettversjon(" axe-4.10 ") == "axe-4.10"
    assert regelsettversjon("v" * 100) == "v" * 64


def test_motorfristen_lar_det_bli_tid_igjen_til_opplastingen():
    """Codex P1: motorens standardfrist var 3600 s — HELE claimets tak.

    Opplastingskapabiliteten claimet utsteder klemmes til 3600 s
    (migrasjon 017) og eier-leasen til det samme (migrasjon 037), og
    ingen av dem har en fornyelsesvei. En motor som fikk bruke hele taket,
    ble derfor ferdig nøyaktig når kapabiliteten den skulle laste opp med
    var utløpt: alt arbeidet gjort, ingen evidens levert, og oppdraget
    står ufullført til fristen.

    Fristen dekker SKANNINGEN; avslutningen (kanonisering, opplasting,
    signert kvittering) har sin egen margin. Testen binder de tre tallene
    sammen i stedet for å gjenta dem, så ingen av dem kan skrus opp alene.

    Kontroll: sett `STANDARD_TIDSAVBRUDD_S` tilbake til 3600, så blir
    denne rød — marginen forsvinner.
    """
    from modules.wcag_audit.motor import (AVSLUTNINGSMARGIN_S, Kommandomotor,
                                          STANDARD_TIDSAVBRUDD_S)
    #: Claimets tak: migrasjon 017 (kapabilitet) og 037 (lease).
    tak = 3600
    assert STANDARD_TIDSAVBRUDD_S + AVSLUTNINGSMARGIN_S == tak
    assert AVSLUTNINGSMARGIN_S >= 300, \
        "under fem minutter til opplasting og kvittering er ingen margin"
    # ... og standarden er den motoren FAKTISK får når ingen sier noe.
    assert Kommandomotor(["x"]).tidsavbrudd_s == STANDARD_TIDSAVBRUDD_S


def test_den_annonserte_fristen_er_fristen_som_gjelder():
    """Codex P1: 30/60-minuttersfristene var annonsert, ikke håndhevet.

    To ledd sviktet hver for seg:

      * `_opprett_oppdrag` skrev den GENERISKE `UTFORELSESFRIST_S`
        (24 timer) på hver eneste oppdragsrad, uansett type. En
        enkeltsidekontroll kunne derfor fullføre og bli kvittert som
        utført et helt døgn etter fristen manifestet lover — og
        eier-leasen (037), som strekkes til nettopp `utforelsesfrist`,
        arvet det samme døgnet, så en krasjet kontroll lå ureclaimet i
        24 timer.
      * motoren kjørte alltid på sitt eget tak (3300 s). En
        `enkeltside`-kontroll med 30 minutters frist fikk altså 55
        minutter å bruke, og ble drept lenge etter at oppdraget hadde
        oversittet fristen sin.

    Fristen deklareres nå på KONTRAKTEN og leses av begge: raden får
    typens frist, og motoren får det claimet har igjen minus
    avslutningen.

    Kontroll: sett `frist_s` i `_opprett_oppdrag` tilbake til
    `UTFORELSESFRIST_S`, eller dropp `frist_s=` i `kjor_en`, så blir
    denne rød på hver sin halvdel.
    """
    import oppdragskontrakt as ok
    from modules.wcag_audit import controller
    from modules.wcag_audit.motor import AVSLUTNINGSMARGIN_S

    # 1) Kontrakten navngir fristen manifestet annonserer.
    assert ok.utforelsesfrist_s("kontroll.wcag.nettsted",
                                {"omfang": "enkeltside"}) == 30 * 60
    assert ok.utforelsesfrist_s("kontroll.wcag.nettsted",
                                {"omfang": "nettsted"}) == 60 * 60
    # Uleselig omfang gir den STRENGESTE fristen, aldri den generiske:
    # en for kort frist er et oppdrag som må gjøres om, en for lang er
    # nettopp overskridelsen dette finnes for å hindre.
    assert ok.utforelsesfrist_s("kontroll.wcag.nettsted", {}) == 30 * 60
    # ... og en type uten egen frist beholder den generiske.
    assert ok.utforelsesfrist_s("reinnsending", {}) is None

    # 2) Motoren får OPPDRAGETS vindu, ikke sitt eget tak.
    for om_s in (30 * 60, 60 * 60):
        motor = FakeMotor(resultat=_motorresultat())
        res = controller.kjor_en(_Stubklient(200, frist_om_s=om_s), "tk",
                                 motor, _kontekst(), lambda k: k)
        assert res["utfall"] == "utfort", res
        gitt = motor.frister[0]
        assert om_s - AVSLUTNINGSMARGIN_S - 5 <= gitt <= (
            om_s - AVSLUTNINGSMARGIN_S), (om_s, gitt)

    # 3) Et vindu som ikke rekker avslutningen — eller ikke lar seg lese
    #    — koster ikke kundens nettsted en eneste forespørsel.
    for om_s in (AVSLUTNINGSMARGIN_S, 10, None):
        motor = FakeMotor(resultat=_motorresultat())
        klient = _Stubklient(200, frist_om_s=om_s)
        res = controller.kjor_en(klient, "tk", motor, _kontekst(),
                                 lambda k: k)
        assert motor.payloads == [], om_s
        assert res["utfall"] == "avbrutt", res
        assert klient.kvitteringer[0]["feilkode"] == "frist_utilstrekkelig"

    # 4) Kapabilitetene klemmer også: er opplastingen den FØRSTE grensen,
    #    er det den som bestemmer, ikke utførelsesfristen.
    naa = datetime.now(timezone.utc)
    klient = _Stubklient(200, frist_om_s=60 * 60)
    klient.opplasting = {"jti": "kap",
                         "utloper": (naa + timedelta(seconds=900)
                                     ).isoformat()}
    motor = FakeMotor(resultat=_motorresultat())
    controller.kjor_en(klient, "tk", motor, _kontekst(), lambda k: k)
    assert 900 - AVSLUTNINGSMARGIN_S - 5 <= motor.frister[0] <= (
        900 - AVSLUTNINGSMARGIN_S)

    # 5) Motoren kan bare KLEMMES av oppdragets frist, aldri skrus opp:
    #    taket er dens egen grense.
    from modules.wcag_audit.motor import Kommandomotor, Motorfeil
    m = Kommandomotor(["/bin/false"], tidsavbrudd_s=5)
    with pytest.raises(Motorfeil):
        m.kjor({}, frist_s=0)
    with pytest.raises(Motorfeil):
        m.kjor({}, frist_s="lenge")


def test_dypt_nostet_motorutdata_er_motorfeil():
    """Codex P1: `json.loads` rekurserer, og fangsten manglet RecursionError.

    `[[[[...]]]]` er noen få kilobyte for tusenvis av nivåer — altså godt
    innenfor MAKS_STDOUT, så størrelsesvakten ser ingenting. Parseren
    treffer rekursjonsgrensen og kaster RecursionError, som verken er
    ValueError, KeyError eller TypeError: unntaket går ut av
    `controller.kjor_en` (som kun fanger Motorfeil og ValidationError), og
    det claimede oppdraget står ufullført til fristen i stedet for å få
    den dokumenterte feilkvitteringen.

    Vakten står FØR parsingen, ikke bare rundt den: `json.loads` er det
    første stedet som rekurserer, men skjemavalideringen og
    JCS-kanoniseringen lenger nede gjør akkurat det samme, så dybden må
    stanses ved inngangen.

    Kontroll: fjern `_for_dypt`-sjekken i `kjor` OG `RecursionError` fra
    except-tuppelen, så bobler RecursionError ut av `m.kjor` igjen.
    """
    from modules.wcag_audit.motor import Kommandomotor, MAKS_DYBDE, Motorfeil

    dypt = "[" * 20000 + "]" * 20000
    m = Kommandomotor(_motorkommando("import sys;sys.stdout.write(%r)" % dypt))
    with pytest.raises(Motorfeil, match="nøstet dypere"):
        m.kjor({})

    # ...men et ekte, flatt svar skal fortsatt gå igjennom, og et som bare
    # så vidt holder seg innenfor taket også.
    god = json.dumps({"regelsett_versjon": "axe-4.10", "varighet_ms": 5,
                      "funn": [{"regel_id": "a", "alvorlighet": "alvorlig",
                                "antall": 1, "eksempler": ["#x"]}]})
    m2 = Kommandomotor(_motorkommando("import sys;sys.stdout.write(%r)" % god))
    assert m2.kjor({}).varighet_ms == 5
    assert MAKS_DYBDE > 8, "taket må være rundhåndet mot ekte rapporter"

    # Klammer INNE i en streng er tekst, ikke struktur — en selektor med
    # `[aria-label]` skal ikke telle mot dybden.
    med_klammer = json.dumps(
        {"regelsett_versjon": "axe-4.10", "varighet_ms": 5,
         "funn": [{"regel_id": "a", "alvorlighet": "alvorlig", "antall": 1,
                   "eksempler": ["[[[" * 200 + '\\"']}]})
    m3 = Kommandomotor(_motorkommando(
        "import sys;sys.stdout.write(%r)" % med_klammer))
    assert m3.kjor({}).varighet_ms == 5


def test_motoren_arver_aldri_controllerens_hemmeligheter(tmp_path,
                                                         monkeypatch):
    """Codex P1: `Popen` uten `env=` arver HELE controllerens miljø.

    Og controllerens miljø er nettopp der hemmelighetene bor:
    `db.hemmeligheter.last_credentials` leser systemd-credentials —
    modultoken, signeringsnøkler, DSN med passord — inn i `os.environ` ved
    oppstart. Motoren er ubetrodd kode som i tillegg gjengir ubetrodde
    kundesider i Chromium, så «motoren kjører uten credentials» var et
    løfte bare kommandolinjen holdt: prosessen kunne lese controllerens
    fulle plattformautoritet ut av sitt eget miljø og bruke den mot API-et.

    Kontroll: ta bort `env=_motormiljo()` i `Kommandomotor.kjor`, så finner
    løkka under tokenet igjen i motorens miljø.
    """
    from modules.wcag_audit.motor import Kommandomotor

    miljofil = tmp_path / "miljo.json"
    monkeypatch.setenv("DISPONIT_MODUL_TOKEN", "hemmelig-token-abc123")
    monkeypatch.setenv("DISPONIT_DB_DSN",
                       "postgres://u:passord@localhost/disponit")
    monkeypatch.setenv("PATH", os.environ.get("PATH", "/usr/bin"))

    god = json.dumps({"regelsett_versjon": "axe-4.10", "varighet_ms": 5})
    m = Kommandomotor(_motorkommando(
        "import json,os,sys,pathlib;"
        "pathlib.Path(%r).write_text(json.dumps(dict(os.environ)),"
        " encoding='utf-8');"
        "sys.stdout.write(%r)" % (str(miljofil), god)))
    assert m.kjor({}).regelsett_versjon == "axe-4.10"

    sett = json.loads(miljofil.read_text(encoding="utf-8"))
    for navn, verdi in sett.items():
        assert "hemmelig-token-abc123" not in verdi, f"{navn} bar tokenet"
        assert "passord" not in verdi, f"{navn} bar DSN-passordet"
    assert not [n for n in sett if n.startswith("DISPONIT_")], (
        f"DISPONIT_*-variabler nådde motoren: {sorted(sett)}")
    # ...men motoren skal fortsatt kunne finne runtime-binæren sin.
    assert "PATH" in sett, "allowlisten vasket bort PATH også"


def test_numerisk_overflyt_fra_motoren_er_motorfeil():
    """Codex P1: konverteringsvaktene fanget bare ValueError og TypeError.

    Tre feilmoduser slapp forbi, og alle tre ender samme sted — et unntak
    ut av `controller.kjor_en` (som kun fanger Motorfeil og
    ValidationError), altså et claimet oppdrag som står ufullført til
    fristen i stedet for å bli kvittert som feilet:

      * `1e309` er gyldig JSON, blir `inf`, og `int(inf)` er OverflowError;
      * `10**20` konverterer fint, passerer skjemaet (som ikke har noe
        øvre tak) og er `Ikkekanoniserbar` først under kanoniseringen;
      * en SUM over 500 funn kan gå over det trygge området selv når hvert
        ledd lå under.

    Kontroll: ta `OverflowError` ut av `motor.heltall`, eller fjern
    `MAKS_HELTALL`-sjekken, eller la `_kanoniske_bytes` slippe
    `Ikkekanoniserbar` videre — hver av de tre gjør denne rød med et annet
    unntak enn Motorfeil.
    """
    from modules.wcag_audit.motor import (Kommandomotor, MAKS_HELTALL,
                                          Motorfeil, heltall)
    from modules.wcag_audit.rapport import bygg

    # Porten selv, på alle tre kantene.
    for raa in ("ukjent", {"a": 1}, None, True, float("inf"), float("nan"),
                1e309, MAKS_HELTALL + 1, -(MAKS_HELTALL + 1), 10 ** 20):
        with pytest.raises(Motorfeil):
            heltall(raa)
    assert heltall(MAKS_HELTALL) == MAKS_HELTALL and heltall("42") == 42

    # `varighet_ms: 1e309` fra en ekte motorkjøring: OverflowError før.
    over = json.dumps({"regelsett_versjon": "axe-4.10",
                       "varighet_ms": 1e309, "sider": [], "funn": [],
                       "blokkert": [], "avkortet": [False, None, None]})
    m = Kommandomotor(_motorkommando("import sys;sys.stdout.write(%r)" % over),
                      tidsavbrudd_s=30)
    with pytest.raises(Motorfeil):
        m.kjor({})

    # `antall` og `avkortet` er samme eksponering, i rapportbyggingen.
    for over in ({"funn": ({"regel_id": "r", "alvorlighet": "lav",
                            "antall": 10 ** 20, "eksempler": []},)},
                 {"blokkert": ({"vert": "f.example", "antall": 10 ** 20,
                                "art": "font"},)},
                 {"avkortet": (True, 10 ** 20, 10 ** 20)}):
        with pytest.raises(Motorfeil):
            bygg(_motorresultat(**over), payload=_payload(),
                 kontekst=_kontekst())

    # Summen: hvert ledd er lovlig, `sammendrag` blir det ikke.
    ledd = MAKS_HELTALL // 3
    with pytest.raises(Motorfeil):
        bygg(_motorresultat(funn=tuple(
                {"regel_id": f"r{i}", "alvorlighet": "lav", "antall": ledd,
                 "eksempler": []} for i in range(4))),
             payload=_payload(), kontekst=_kontekst())


def test_brokdel_fra_motoren_er_motorfeil_ikke_trunkering():
    """Codex P1: `int()` trunkerer mot null i STILLHET.

    Stillheten er skaden. `antall: 0.9` ble `0`, og `rapport.bygg` hopper
    over funn med `antall < 1` — et funn motoren FANT forsvant ut av en
    rapport som ellers ser komplett ut, uten et eneste ærlighetsfelt som
    sier fra. `antall: 1.9` ble `1` og understøtter `sammendrag`, og
    `varighet_ms: 12.7` ble skrevet om på samme vis. Alle tre er
    heltallsfelter i kontrakten, så et brøktall er utdata vi ikke kan
    lese — det skal gi den dokumenterte feilkvitteringen, ikke en
    avrunding modulen finner på selv.

    Kontroll: fjern `is_integer()`-vakten i `motor.heltall`, så blir
    `bygg(...)` grønn igjen med et funn som mangler og et sammendrag som
    er for lavt.
    """
    from modules.wcag_audit.motor import Kommandomotor, Motorfeil, heltall
    from modules.wcag_audit.rapport import bygg

    for raa in (0.9, 1.9, -0.5, 2.5, 1e-3):
        with pytest.raises(Motorfeil):
            heltall(raa)
    # Hele tall SKAL fortsatt slippe gjennom, også som float: en motor som
    # skriver `3.0` i JSON gir oss en float uten at noe er tapt. `-7.0` er
    # helt, men under gulvet porten nå håndhever — se
    # `test_negativt_motortall_avvises_ikke_nullstilles`.
    assert heltall(3.0) == 3 and heltall(0.0) == 0

    # Funnet som forsvant: 0.9 ble 0, og 0 < 1 droppes stille.
    with pytest.raises(Motorfeil):
        bygg(_motorresultat(funn=({"regel_id": "color-contrast",
                                   "alvorlighet": "alvorlig",
                                   "antall": 0.9, "eksempler": []},)),
             payload=_payload(), kontekst=_kontekst())
    # Samme port, samme svar, for de andre ubetrodde tallveiene.
    for over in ({"blokkert": ({"vert": "f.example", "antall": 2.5,
                                "art": "font"},)},
                 {"avkortet": (True, 10.5, 3.5)}):
        with pytest.raises(Motorfeil):
            bygg(_motorresultat(**over), payload=_payload(),
                 kontekst=_kontekst())

    # `varighet_ms` fra en ekte motorkjøring: trunkert før, Motorfeil nå.
    brok = json.dumps({"regelsett_versjon": "axe-4.10",
                       "varighet_ms": 12.7, "sider": [], "funn": [],
                       "blokkert": [], "avkortet": [False, None, None]})
    m = Kommandomotor(_motorkommando("import sys;sys.stdout.write(%r)" % brok),
                      tidsavbrudd_s=30)
    with pytest.raises(Motorfeil):
        m.kjor({})


def test_telling_under_en_avvises_ikke_repareres():
    """Codex P1, runde 11: `heltall` lukket brøkveien, ikke heltallsveien.

    `antall: -3` og `antall: 0` er ekte heltall og passerte porten. Da sto
    de to reparasjonene igjen, én i hver retning:

      * funn: `if antall < 1: continue` slettet HELE funnet. Rapporten ble
        promotert med en kortere funnliste enn motoren fant, og ingenting
        sa fra — `sammendrag` teller ikke det som ble forkastet, og
        `avkortet` handler om tak, ikke om rader vi kastet.
      * dekningsbegrensninger: `max(1, ...)` skrev tallet om til `1`, en
        telling modulen fant på selv i det ene feltet 014b B3 har for å si
        hva rapporten IKKE dekker.

    Kontroll: sett `< 1`-vakten i `_antall` tilbake til `return`, så blir
    første blokk grønn med et funn borte og et sammendrag som er 0.
    """
    from modules.wcag_audit.motor import Motorfeil
    from modules.wcag_audit.rapport import bygg

    for ugyldig in (-3, 0, -1):
        with pytest.raises(Motorfeil):
            bygg(_motorresultat(funn=({"regel_id": "color-contrast",
                                       "alvorlighet": "alvorlig",
                                       "antall": ugyldig,
                                       "eksempler": []},)),
                 payload=_payload(), kontekst=_kontekst())
        with pytest.raises(Motorfeil):
            bygg(_motorresultat(blokkert=({"vert": "f.example",
                                           "antall": ugyldig,
                                           "art": "font"},)),
                 payload=_payload(), kontekst=_kontekst())

    # `antall` er PÅKREVD i et funn (rapportskjemaet), så en manglende
    # telling er like uleselig som en ugyldig — ikke et funn som stille
    # forsvinner.
    with pytest.raises(Motorfeil):
        bygg(_motorresultat(funn=({"regel_id": "color-contrast",
                                   "alvorlighet": "alvorlig",
                                   "eksempler": []},)),
             payload=_payload(), kontekst=_kontekst())

    # Den lovlige veien står: en blokkert-rad UTEN telling er fortsatt en
    # kjent begrensning, og «minst én» er da det raden selv sier.
    r = bygg(_motorresultat(blokkert=({"vert": "f.example", "art": "font"},)),
             payload=_payload(), kontekst=_kontekst())
    assert r["dekningsbegrensninger"] == [{"vert": "f.example", "antall": 1,
                                           "art": "font"}]


def test_negativt_motortall_avvises_ikke_nullstilles():
    """Codex P2: `max(0, heltall(...))` var samme stillhet én etasje opp.

    `heltall` stengte brøkveien og overflytveien, men kalleren skrev
    fortsatt `max(0, ...)` rundt den — og `max` er nøyaktig den
    reparasjonen porten finnes for å hindre. `varighet_ms: -7` ble til
    `0`, passerte rapportskjemaet, og ble promotert som en varighet
    motoren aldri oppga. Det samme gjaldt `avkortet`-trippelen, der
    `verdi: -5` ble til `0` i det ene feltet som sier hvor mye rapporten
    utelot.

    `antall: -3` fikk allerede Motorfeil. Gulvet hører derfor til i
    porten, ikke i hvert kallested, så alle tre ubetrodde tallveier gir
    samme svar på samme inndata.

    Kontroll: sett `minst`-vakten i `motor.heltall` tilbake og skriv
    `max(0, ...)` rundt kallene igjen, så blir motorkjøringen grønn med
    `varighet_ms == 0` og `avkortet.verdi == 0`.
    """
    from modules.wcag_audit.motor import Kommandomotor, Motorfeil, heltall
    from modules.wcag_audit.rapport import bygg

    for raa in (-1, -7, -7.0, "-42"):
        with pytest.raises(Motorfeil):
            heltall(raa)
    # Gulvet er et argument, ikke en fast grense: `_antall` sender 1.
    assert heltall(0) == 0 and heltall(1, minst=1) == 1
    with pytest.raises(Motorfeil):
        heltall(0, minst=1)

    # Motorkjøringen: `varighet_ms: -7` ga `0` før, Motorfeil nå.
    neg = json.dumps({"regelsett_versjon": "axe-4.10", "varighet_ms": -7,
                      "sider": [], "funn": [], "blokkert": [],
                      "avkortet": [False, None, None]})
    m = Kommandomotor(_motorkommando("import sys;sys.stdout.write(%r)" % neg),
                      tidsavbrudd_s=30)
    with pytest.raises(Motorfeil):
        m.kjor({})

    # Dekningssignalet: `tak` og `verdi` gjennom samme port.
    for trippel in ((True, -1, 3), (True, 10, -5)):
        with pytest.raises(Motorfeil):
            bygg(_motorresultat(avkortet=trippel), payload=_payload(),
                 kontekst=_kontekst())

    # Den lovlige veien står: null er en ekte varighet og et ekte tak.
    god = json.dumps({"regelsett_versjon": "axe-4.10", "varighet_ms": 0,
                      "sider": [], "funn": [], "blokkert": [],
                      "avkortet": [False, None, None]})
    m2 = Kommandomotor(_motorkommando("import sys;sys.stdout.write(%r)" % god),
                       tidsavbrudd_s=30)
    assert m2.kjor({}).varighet_ms == 0
    r = bygg(_motorresultat(avkortet=(True, 0, 0)), payload=_payload(),
             kontekst=_kontekst())
    assert r["avkortet"]["truffet"] is True


def test_eksempellisten_maa_vaere_en_liste():
    """Codex P1: `list(f.get("eksempler") or [])` var to feil i én linje.

      * En STRENG er iterabel: `"button.x"` ble ett element per tegn, og
        etter kappingen sto det ti enkelttegn i rapporten som ser ut som
        selektorer. Det er FABRIKERTE eksempler — evidens modulen fant på
        selv — og de blir promotert som om motoren hadde rapportert dem.
        På veien satte de også `maks_eksempler_sett` og kunne slå
        `avkortet` på uten at noe var kappet.
      * Et TALL er ikke iterabel: `list(5)` er en naken TypeError, og
        `controller.kjor_en` fanger kun Motorfeil og ValidationError, så
        det claimede oppdraget ble stående ufullført til fristen i stedet
        for å få en feilkvittering.

    Kontroll: bytt `_eksempelliste(...)` tilbake til
    `list(f.get("eksempler") or [])`, så gir strengen ti enkelttegn i
    stedet for Motorfeil, og tallet gir TypeError i stedet for Motorfeil.
    """
    from modules.wcag_audit.motor import Motorfeil
    from modules.wcag_audit.rapport import bygg

    def _med(eksempler):
        return _motorresultat(funn=({"regel_id": "color-contrast",
                                     "alvorlighet": "alvorlig", "antall": 3,
                                     "eksempler": eksempler},))

    for raa in ("button.x", 5, 5.0, {"a": "#b"}, True, b"#a"):
        with pytest.raises(Motorfeil):
            bygg(_med(raa), payload=_payload(),
                 kontekst=_kontekst())

    # Det lovlige skal fortsatt være lovlig: liste, tuppel og «ingenting».
    for raa, ventet in ((["#a", "#b"], ["#a", "#b"]), (("#a",), ["#a"]),
                        ([], []), (None, [])):
        r = bygg(_med(raa), payload=_payload(),
                 kontekst=_kontekst())
        assert r["funn"][0]["eksempler"] == ventet


def test_dekningssignalet_avvises_i_stedet_for_aa_tvangskonverteres():
    """Codex P2: `avkortet` er en PÅSTAND om hva rapporten ikke dekker.

    `bool(truffet)` gjorde `[0, null, null]` til det skjemagyldige
    `truffet: false` og `"false"` til `true`, og `tuple(raa or ...)`
    gjorde strengen `"false"` til trippelen `('f','a','l','s','e')`.
    Den falske retningen er den farlige: promotert evidens som påstår at
    ingenting var utelatt, nettopp når controlleren ikke klarte å lese
    dekningssignalet — den ene løgnen feltet finnes for å hindre (014b B3).

    Kontroll: bytt `avkortet(...)` tilbake til `tuple(raa or (...))` i
    `motor`, så blir `"false"` til `truffet: true`; og bytt
    isinstance-sjekken i `bygg` tilbake til `bool(truffet)`, så blir
    `[0, null, null]` til `truffet: false`.
    """
    from modules.wcag_audit.motor import Motorfeil, avkortet
    from modules.wcag_audit.rapport import bygg

    # Formen, der den ble ødelagt: motoravlesningen.
    for raa in ("false", "", {"truffet": False}, {"a": 1},
                (True, 10, 25, "ekstra")):
        with pytest.raises(Motorfeil):
            avkortet(raa)
    # «Ingenting» betyr fortsatt ingenting avkortet.
    for raa in (None, [], ()):
        assert avkortet(raa) == (False, None, None)
    assert avkortet([True, 10, 25]) == (True, 10, 25)

    # Trippelen, der taket måles: flagget må være boolsk...
    for daarlig in (0, 1, "false", None, [], 1.0):
        with pytest.raises(Motorfeil):
            bygg(_motorresultat(avkortet=(daarlig, None, None)),
                 payload=_payload(), kontekst=_kontekst())
    # ... og den må være enig med seg selv: en telling over sitt eget tak
    # ER et truffet tak, og å velge den ene påstanden for motoren ville
    # vært å finne på et dekningssignal.
    with pytest.raises(Motorfeil):
        bygg(_motorresultat(avkortet=(False, 10, 25)),
             payload=_payload(), kontekst=_kontekst())
    # Det sammenhengende skal fortsatt gå igjennom, begge veier.
    r = bygg(_motorresultat(avkortet=(False, 10, 3)),
             payload=_payload(), kontekst=_kontekst())
    assert r["avkortet"] == {"truffet": False, "tak": 10, "verdi": 3}
    r2 = bygg(_motorresultat(avkortet=(True, 10, 25)),
              payload=_payload(), kontekst=_kontekst())
    assert r2["avkortet"] == {"truffet": True, "tak": 10, "verdi": 25}


def test_tekstfeltene_fra_motoren_ma_vaere_ekte_strenger():
    """Codex P1: `str(f.get("regel_id"))[:128]` og `str(e)` FANT PÅ verdier
    i stedet for å avvise dem, og fabrikatet passerte skjemaet.

    Mangler `regel_id`, blir `str(None)` til `"None"` — en streng med
    lengde 4, altså innenfor `minLength: 1`. Rapporten promoteres da med
    en regel-id motoren aldri rapporterte, og den som leser evidensen slår
    opp `None` i regelsettet, finner ingenting og tror regelsettet er
    utdatert. En dict blir `"{'id': 1}"`: en repr der leseren venter en id.
    Samme sak for eksemplene, som er selve etterprøvbarheten — en
    «selektor» ingen kan kjøre er fabrikert evidens.

    Kontroll: bytt `_tekst(...)` tilbake til `str(...)` i `bygg`, så blir
    `regel_id` `"None"` i stedet for Motorfeil, og eksempelet `"5"`.
    """
    from modules.wcag_audit.motor import Motorfeil
    from modules.wcag_audit.rapport import bygg

    def _bygg(**over):
        f = {"regel_id": "color-contrast", "alvorlighet": "alvorlig",
             "antall": 3, "eksempler": ["#a"]}
        f.update(over)
        return bygg(_motorresultat(funn=(f,)),
                    payload=_payload(), kontekst=_kontekst())

    for raa in (None, {"id": 1}, 5, 5.0, True, b"color-contrast", ""):
        with pytest.raises(Motorfeil):
            _bygg(regel_id=raa)
        with pytest.raises(Motorfeil):
            _bygg(eksempler=["#a", raa])

    # Ekte strenger går fortsatt gjennom — også den som må kappes.
    r = _bygg(regel_id="r" * 200, eksempler=["#a", "x" * 500])
    assert r["funn"][0]["regel_id"] == "r" * 128
    assert r["funn"][0]["eksempler"] == ["#a", "x" * 200]


def test_ensom_surrogat_fra_motoren_er_motorfeil():
    """Codex P1: en escaped ensom surrogate er en streng helt fram til
    kanoniseringen, og der kastet den UnicodeEncodeError — ikke
    Ikkekanoniserbar.

    `{"regel_id": "\\ud800"}` er lovlig JSON-TEKST. `json.loads` gir den
    fra seg som en helt vanlig `str` av lengde 1, så størrelsesvakten,
    `_tekst` (ikke-tom streng — sant) og skjemaets `minLength: 1` sier
    alle ja. Først `kanoniser(...).encode("utf-8")` oppdager at
    kodepunktet ikke kan uttrykkes i UTF-8.

    UnicodeEncodeError er en ValueError, ikke en TypeError, så den gamle
    `except jcs.Ikkekanoniserbar` gikk klar av den — og
    `controller.kjor_en` fanger kun Motorfeil og ValidationError.
    Unntaket forlot altså kjøringen UTEN feil-kvittering, og det claimede
    oppdraget ble stående ufullført til fristen: taushetens utfall §10
    forbyr. `/v1/artefakt` oversetter allerede nøyaktig samme feil til
    `request_feilformet`.

    Kontroll: ta `UnicodeEncodeError` ut av except-tuppelen i
    `_kanoniske_bytes`, så bobler den ut av `bygg` igjen — testen fanger
    det ved å kreve Motorfeil, ikke bare «et unntak».
    """
    from policy_validator import jcs
    from modules.wcag_audit.motor import Motorfeil
    from modules.wcag_audit.rapport import bygg

    # Premisset: dette ER en streng etter parsing, og det ER
    # kanoniseringen som feiler — med UnicodeEncodeError, ikke
    # Ikkekanoniserbar.
    ensom = json.loads('{"x": "\\ud800"}')["x"]
    assert isinstance(ensom, str) and len(ensom) == 1
    with pytest.raises(UnicodeEncodeError):
        jcs.kanoniske_bytes({"x": ensom})
    assert not isinstance(
        UnicodeEncodeError("utf-8", "", 0, 1, ""), jcs.Ikkekanoniserbar), (
        "hadde den vært en Ikkekanoniserbar, ville den gamle fangsten holdt")

    def _bygg(**over):
        f = {"regel_id": "color-contrast", "alvorlighet": "alvorlig",
             "antall": 3, "eksempler": ["#a"]}
        f.update(over)
        return bygg(_motorresultat(funn=(f,)),
                    payload=_payload(), kontekst=_kontekst())

    # Hvert strengfelt i rapporten, ikke bare de som går gjennom `_tekst`:
    # fangsten står i `_kanoniske_bytes`, som HELE `bygg` ender i.
    with pytest.raises(Motorfeil, match="kanoniseres"):
        _bygg(regel_id=ensom)
    with pytest.raises(Motorfeil, match="kanoniseres"):
        _bygg(eksempler=["#a", ensom])
    with pytest.raises(Motorfeil, match="kanoniseres"):
        bygg(_motorresultat(regelsett_versjon="axe-4.10" + ensom),
             payload=_payload(), kontekst=_kontekst())

    # Halvparten av et EKTE surrogatpar er ikke en ensom surrogate: et
    # tegn utenfor BMP skal fortsatt gå rett igjennom.
    r = _bygg(regel_id="emoji-\U0001f600", eksempler=["#a\U0001f600"])
    assert r["funn"][0]["regel_id"] == "emoji-\U0001f600"


def _prosessen_er_dod(pid: int) -> bool:
    """Borte fra prosesstabellen, eller en zombie som venter på å høstes.

    Barnebarnet blir foreldreløst i det motorprosessen dør, så det er
    init-prosessen som høster liket — og hvor fort den gjør det er ikke
    noe testen kan styre. `Z` er derfor like godt som borte: SIGKILL har
    truffet, prosessen kjører ikke lenger."""
    import os
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            stat = f.read()
    except OSError:
        try:
            os.kill(pid, 0)
        except OSError:
            return True                  # borte
        return False                     # lever, uten /proc å spørre
    return stat.rsplit(") ", 1)[1].split()[0] == "Z"


def test_tidsavbruddet_dreper_hele_prosesstreet(tmp_path):
    """Codex P1: fristen kunne overskrides uten grense.

    Den ekte motoren er en container som starter Chromium, og Chromium
    ARVER stdout. `p.kill()` traff bare mellomleddet: barnebarnet levde
    videre med rørenden åpen, `_les_med_tak` ventet på en EOF som aldri
    kom, og den annonserte fristen (opptil en time) ble til «så lenge
    nettleseren måtte finne på» — mens foreldreløse nettlesere hopet seg
    opp på hosten. `if p.poll() is None` i `finally` gjorde det verre:
    dør motorprosessen selv i det den har skrevet JSON-en, så vakten en
    ferdig prosess og ryddet ingenting.

    Her er motoren en prosess som starter et barnebarn og AVSLUTTER med
    én gang. Barnebarnet arver stdout og sover i 20 s.

    Kontroll: ta bort `start_new_session`/`_drep_treet` og la
    `_tidsavbrudd` kalle `p.kill()`, så bruker `m.kjor` 20 sekunder i
    stedet for 1 — og barnebarnet lever fortsatt når den endelig gir opp.
    """
    from modules.wcag_audit.motor import Kommandomotor, Motorfeil

    pidfil = tmp_path / "barnebarn.pid"
    barn = ("import os, pathlib, time; "
            "pathlib.Path(%r).write_text(str(os.getpid()), encoding='utf-8'); "
            "time.sleep(20)" % str(pidfil))
    motor = ("import subprocess, sys; "
             "subprocess.Popen([sys.executable, '-c', %r])" % barn)

    m = Kommandomotor(_motorkommando(motor), tidsavbrudd_s=1)
    start = time.monotonic()
    with pytest.raises(Motorfeil):
        m.kjor({})
    brukt = time.monotonic() - start
    assert brukt < 8, (
        f"lesingen ventet på barnebarnets rørende i {brukt:.1f}s — fristen "
        f"var 1s")

    # ...og barnebarnet skal være drept, ikke bare glemt.
    frist = time.monotonic() + 5
    while time.monotonic() < frist and not pidfil.exists():
        time.sleep(0.05)
    assert pidfil.exists(), "barnebarnet rakk aldri å skrive pid-en sin"
    pid = int(pidfil.read_text(encoding="utf-8"))
    while time.monotonic() < frist and not _prosessen_er_dod(pid):
        time.sleep(0.05)
    assert _prosessen_er_dod(pid), f"barnebarnet {pid} lever videre"


def test_skjemafeil_lekker_aldri_artefaktverdien():
    """Codex P1: `e.message` er bygget rundt den FEILENDE VERDIEN.

    Bryter et felt `type`, `enum`, `pattern` eller `format`, står verdien
    ordrett i teksten — `'alice@example.com' is not of type 'integer'` —
    og `_artefakt_upload` skrev nettopp den teksten til `Sikkerhetslogg`
    (`forste=skjemafeil[0][:160]`). Den loggen har «ALDRI payload» som
    kontrakt og går til stderr: rapportklartekst med persondata og alt,
    ut av det krypterte sporet og inn i driftsloggene — for et artefakt
    som ble AVVIST og aldri skulle etterlatt seg innhold noe sted.

    Stien lekker på samme vis når skjemaet tillater frie nøkler: da er det
    brytende leddet innsenderens egen nøkkel.

    Kontroll: bytt `_bruddkode(e)` tilbake til `e.message[:160]`, eller la
    `_sti` bruke `absolute_path` rått, så finner løkka under verdien igjen.
    """
    from api.artefaktskjema import valider

    hemmelig = "alice@example.com"
    skjema = {"type": "object",
              "properties": {
                  "antall": {"type": "integer"},
                  "alvor": {"enum": ["lav", "hoy"]},
                  "vert": {"type": "string", "pattern": "^[a-z]+$"},
                  "kjort_ts": {"type": "string", "format": "date-time"},
                  "fritt": {"type": "object",
                            "additionalProperties": {"type": "integer"}}}}
    innhold = {"antall": hemmelig, "alvor": hemmelig, "vert": hemmelig,
               "kjort_ts": hemmelig, "fritt": {hemmelig: hemmelig}}
    feil = valider(skjema, innhold)
    assert len(feil) >= 5, feil
    for f in feil:
        assert hemmelig not in f, f
        assert "alice" not in f, f
    # Nytteverdien skal være i behold: hvilket FELT og hvilket KRAV.
    samlet = " | ".join(feil)
    for ventet in ("antall", "alvor", "vert", "kjort_ts",
                   "type=", "enum=", "pattern=", "format="):
        assert ventet in samlet, (ventet, samlet)
    # ...men den frie nøkkelen er innsenderens, ikke skjemaets, og skal
    # stå som `<felt>`.
    assert "fritt/<felt>" in samlet, samlet

    # Samme port på det EKTE rapportskjemaet, på veien som faktisk logges.
    from modules.wcag_audit import rapportskjema
    from modules.wcag_audit.rapport import bygg
    rapport = bygg(_motorresultat(), payload=_payload(),
                   kontekst=_kontekst())
    feil = valider(rapportskjema.SKJEMA, {**rapport, "kjort_ts": hemmelig})
    assert feil and all(hemmelig not in f for f in feil), feil
    assert any("kjort_ts" in f for f in feil), feil
