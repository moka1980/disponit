"""«Likt lokalt»-leddet for m02-fordelingsartefaktet — STÅENDE måling.

Det samme settet (`deploy/staging/m02_fordeling.bygg_sett`) som
staging-artefaktet drives av, kjøres her gjennom den lokale
beslutningsveien, og fordelingen måles i revisjonsloggen. Da er «det
syntetiske datasettet er likt lokalt» en port CI feller ved hver
kjøring — ikke et minne fra en runde (m02-aksept-klarsignalet §3,
premiss korrigert: m01-rundens historiske 180 rader finnes ikke i
prod-basen, målt 2026-08-21 — hele basen hadde null STOPP).
"""
from __future__ import annotations

import importlib.util
import json
import secrets
import subprocess
from pathlib import Path

from .test_api import (  # noqa: F401 — delte fixturer og byggere
    TENANT, app, hendelse, hendelse_uten_attestasjoner, klient,
    malpolicy, migrator, miljo, pg, policy, post, token)
from .test_deploy_miljofil import kun_posix  # bash/Linux-porten, delt

ROT = Path(__file__).resolve().parents[3]


def _last(navn: str, fil: str):
    spec = importlib.util.spec_from_file_location(navn, ROT / fil)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _lib():
    return _last("m02_fordeling", "deploy/staging/m02_fordeling.py")


def test_settet_er_fasiten():
    """Settet ER fordelingen 84/3/93 over 180 — deterministisk, og
    grensen i KRAVGRENSER bærer NØYAKTIG samme fasit (porten binder de
    to kildene så de ikke kan gli fra hverandre)."""
    from manifestskjema import KRAVGRENSER
    m = _lib()
    sett = m.bygg_sett()
    assert len(sett) == 180 == sum(m.FORDELING.values())
    talt: dict[str, int] = {}
    for beslutning, _ in sett:
        talt[beslutning] = talt.get(beslutning, 0) + 1
    assert talt == m.FORDELING \
        == KRAVGRENSER["m02-fordeling-v1"]["fordeling_eksakt"]
    assert m.bygg_sett() == sett            # deterministisk


def test_settet_er_bundet_til_bytene_ikke_til_navnet_sitt():
    """«Likt lokalt» er en påstand om SETTET, ikke om summen.

    Radene bærer loggpost-id og beslutning — ikke hendelsene som ble sendt
    inn — og `sett_versjon` er en håndholdt streng. Et staging-ledd på en
    eldre utrulling kunne derfor drive helt andre hendelser til de samme
    84/3/93 og valideres som det samme settet. Bytene er bindingen, og de
    hashes i BEGGE ledd (samme form som datasett_sha256/§1.2).

    MUTASJONEN SOM DREPER DENNE: la porten godta et artefakt uten
    `sett_sha256`, eller slutt å sammenligne med de innsjekkede bytene.
    """
    import hashlib

    from manifestskjema import (M02_SETT_STI, _sjekk_grenser,
                                m02_bevisrot_sha256,
                                valider_artefaktformat)
    m = _lib()
    assert m.sett_sha256() == hashlib.sha256(
        M02_SETT_STI.read_bytes()).hexdigest()

    rader = [(i + 1, b) for i, (b, _) in enumerate(m.bygg_sett())]
    art = m.artefakt(rader, "t-test", "lokal", "2026-08-21T00:00:00+00:00",
                     m02_bevisrot_sha256())
    assert art["bestatt"] is True
    assert valider_artefaktformat(art, "m02-fordeling-v1") == []
    assert _sjekk_grenser("m02-fordeling-v1", art) == []

    # En driver som ikke er den innsjekkede — samme tall, annet sett.
    annen = dict(art, oppsett=dict(art["oppsett"], sett_sha256="0" * 64))
    assert any("sett_sha256" in f
               for f in _sjekk_grenser("m02-fordeling-v1", annen))
    # …og TILLITSGRENSENS anker (#132): en kjøring av en annen
    # produsentflate — eller en ubundet en — er ikke denne kjøringen.
    fremmed = dict(art, oppsett=dict(art["oppsett"],
                                     bevisrot_sha256="1" * 64))
    assert any("produsentflate" in f
               for f in _sjekk_grenser("m02-fordeling-v1", fremmed))
    ubundet = dict(art, oppsett={k: v for k, v in art["oppsett"].items()
                                 if k != "bevisrot_sha256"})
    assert valider_artefaktformat(ubundet, "m02-fordeling-v1")
    assert any("bevisrot" in f
               for f in _sjekk_grenser("m02-fordeling-v1", ubundet))
    # ... og et artefakt uten bindingen i det hele tatt er umålt, ikke
    # grønt: det lukkede skjemaet krever feltet, og porten sier det selv.
    uten = dict(art, oppsett={k: v for k, v in art["oppsett"].items()
                              if k != "sett_sha256"})
    assert valider_artefaktformat(uten, "m02-fordeling-v1") != []
    assert any("sett_sha256" in f
               for f in _sjekk_grenser("m02-fordeling-v1", uten))


@pg
def test_fordelingen_er_lik_lokalt(klient, policy, token, migrator):
    """Hele settet gjennom den EKTE beslutningsveien lokalt: hver
    kategori dømmes per svar (fail-closed i driveren), radene leses av
    revisjonsloggen via idempotensnøklene, og artefaktet — bygget av
    NØYAKTIG samme funksjon som staging-leddet bruker — består både det
    lukkede skjemaet og grensene. Én rad fjernet feller det.

    DENNE er «likt lokalt». Testene over måler settet og bindingen som
    DATA — de bygger radene av `bygg_sett()` selv og rører aldri
    beslutningsveien. Uten dette leddet er `kjor_sett` uten kaller i
    hele treet, og en regresjon i beslutning, signatur eller
    revisjonslogg lar porten stå grønn mens den lokale fordelingen ikke
    lenger er 84/3/93.

    MUTASJONEN SOM DREPER DENNE: bygg `rader` av `bygg_sett()` i stedet
    for av `revisjonslogg`, eller slett testen — da er påstanden i
    modulens egen docstring ikke lenger sann.
    """
    from manifestskjema import _sjekk_grenser, valider_artefaktformat
    m = _lib()
    tok, _ = token()
    runde = secrets.token_hex(4)

    def _post(e, nokkel):
        r = post(klient, policy, e, tok, nokkel=nokkel)
        return r.status_code, (r.json().get("beslutning")
                               if r.status_code == 200 else None)

    def _tillat(ressurs):
        return hendelse(policy, ressurs=ressurs)

    def _uten(handling, ressurs):
        return hendelse_uten_attestasjoner(ressurs=ressurs,
                                           handling=handling)

    def _tukle(e):
        # snudd UTEN ny signering — signaturporten skal felle den, og
        # nettopp det avtrykket er STOPP-kategorien.
        e["attestasjoner"]["ingen_aktiv_tvist"]["resultat"] = False
        return e

    talt = m.kjor_sett(runde, _post, _tillat, _uten, _tukle)
    assert talt == m.FORDELING
    migrator.execute("RESET ROLE")
    migrator.execute("SELECT set_config('disponit.tenant', %s, false)",
                     (TENANT,))
    rader = migrator.execute(
        "SELECT id, beslutning FROM revisjonslogg WHERE tenant=%s"
        "  AND idempotency_key LIKE %s",
        (TENANT, f"m02f-{runde}-%")).fetchall()
    migrator.rollback()
    from manifestskjema import m02_bevisrot_sha256
    art = m.artefakt([(r[0], r[1]) for r in rader], TENANT, "lokal",
                     "2026-08-21T00:00:00+00:00", m02_bevisrot_sha256())
    assert art["bestatt"] is True
    assert valider_artefaktformat(art, "m02-fordeling-v1") == []
    assert _sjekk_grenser("m02-fordeling-v1", art) == []
    # Negative porter: én rad borte → fordelingen spriker; en beslutning
    # byttet → re-regningen feller den; gjentatt loggpost → én hendelse
    # er én rad.
    amputert = dict(art, rader=art["rader"][1:],
                    maalt=dict(art["maalt"]))
    assert any("fasiten" in f or "krever >=" in f
               for f in _sjekk_grenser("m02-fordeling-v1", amputert))
    annen = "TILLAT" if art["rader"][0][1] != "TILLAT" else "STOPP"
    byttet = dict(art, rader=[[art["rader"][0][0], annen]]
                  + [list(r) for r in art["rader"][1:]])
    assert any("fasiten" in f
               for f in _sjekk_grenser("m02-fordeling-v1", byttet))
    dublert = dict(art, rader=[list(art["rader"][0])]
                   + [list(r) for r in art["rader"][:-1]])
    assert any("gjentar" in f
               for f in _sjekk_grenser("m02-fordeling-v1", dublert))


def test_policyvarianten_beholder_verifikatoren_den_bytter_til():
    """Staging-leddet bytter purring-vilkårene til en verifikator API-et
    faktisk KAN signere med. Byttet var et tekstbytte i den serialiserte
    YAML-en, og navnet det byttes TIL finnes ofte i malen fra før
    (`v_bank`, `v_regnskap`, `v_dlp`): da fikk `verifikatorer` to nøkler
    med samme navn, `safe_load` beholdt den siste i stillhet, og den ekte
    verifikatorens tillitserklæringer forsvant — hvorpå
    `valider_ny_policy` avviste policyen før settet fikk kjøre.

    MUTASJONEN SOM DREPER DENNE: gå tilbake til `safe_dump().replace()`.
    """
    import yaml

    from policy_validator.schema import valider_ny_policy
    cli = _last("m02_fordeling_artefakt",
                "deploy/staging/m02-fordeling-artefakt.py")
    mal = (ROT / "policies/bransjemal-tjenestebedrift.yaml").read_text(
        encoding="utf-8")
    purringens = {"forfall_passert_dager", "ingen_aktiv_tvist"}

    # 1) Et navn som ALT står i malen — den gamle formen mistet
    #    v_banks egen tillit; den nye utvider den.
    p = cli.bytt_verifikator(yaml.safe_load(mal), cli.MAL_VERIFIKATOR,
                             "v_bank")
    assert set(p["verifikatorer"]["v_bank"]["betrodd_for"]) == \
        purringens | {"konto_verifisert"}
    assert valider_ny_policy(p) == []
    gammel = yaml.safe_load(
        yaml.safe_dump(yaml.safe_load(mal)).replace(cli.MAL_VERIFIKATOR,
                                                    "v_bank"))
    assert "konto_verifisert" not in \
        gammel["verifikatorer"]["v_bank"]["betrodd_for"]
    assert valider_ny_policy(gammel) != []

    # 2) Et navn som IKKE står i malen — verifikatoren opprettes, betrodd
    #    for nøyaktig vilkårene den overtok.
    p = cli.bytt_verifikator(yaml.safe_load(mal), cli.MAL_VERIFIKATOR,
                             "v_m02fordeling")
    assert set(p["verifikatorer"]["v_m02fordeling"]["betrodd_for"]) == \
        purringens
    assert valider_ny_policy(p) == []

    # 3) Samme navn — ingen endring, og ingen dublett.
    urort = yaml.safe_load(mal)
    assert cli.bytt_verifikator(yaml.safe_load(mal), cli.MAL_VERIFIKATOR,
                                cli.MAL_VERIFIKATOR) == urort


@kun_posix
def test_miljofila_leses_slik_deploy_skrev_den(tmp_path, monkeypatch):
    """Leseren i staging-leddet bindes til den ENESTE skriveren.

    `lib-miljofil.sh::sett_nokkel` skriver `NAVN='verdi'` med ENKLE
    fnutter. Leseren strippet bare `"`, så DISPONIT_ATT_NOKLER kom ut som
    `'{"v_fordring": ...}'` — apostrofene i behold — og `json.loads` felte
    skriptet ved oppstart, før én hendelse var drevet (Codex P1). DSN-en
    tok samme smell.

    Fasiten skrives her av den EKTE shell-funksjonen, ikke av vår
    gjengivelse av formatet: gliret mellom skriver og leser er nettopp
    det som ikke kan måles av to sider som begge er vår egen gjetning.

    MUTASJONEN SOM DREPER DENNE: bytt `shlex.split` tilbake mot et
    `.strip()` av tegn.
    """
    miljofil = tmp_path / "staging.env"
    skript = tmp_path / "skriv.sh"
    skript.write_text(
        f'set -eu\nMILJOFIL="{miljofil}"\n'
        f'DB=disponit; BRUKER=disponit; MIGRATOR=disponit_migrator\n'
        f'touch "$MILJOFIL"; chmod 600 "$MILJOFIL"\n'
        f'. "{ROT / "deploy/staging/lib-miljofil.sh"}"\n'
        'sikre_attestasjonsnokler\n'
        'sikre_hex_hemmelighet DISPONIT_TOKEN_PEPPER\n'
        "sett_nokkel DISPONIT_MIGRATOR_URL"
        " 'host=127.0.0.1 dbname=disponit user=m password=hemmelig'\n",
        encoding="utf-8")
    kjort = subprocess.run(["bash", str(skript)], capture_output=True,
                           text=True)
    assert kjort.returncode == 0, kjort.stderr

    cli = _last("m02_fordeling_artefakt",
                "deploy/staging/m02-fordeling-artefakt.py")
    monkeypatch.setattr(cli, "MILJOFILER", (str(miljofil),))
    miljo = cli._miljo()

    # JSON-nøklene: den linja `json.loads` faktisk feller på.
    nokler = json.loads(miljo["DISPONIT_ATT_NOKLER"])
    assert "k1" in nokler[cli.MAL_VERIFIKATOR]
    # DSN-en: mellomrommene ER verdien, ikke ordskiller.
    assert miljo["DISPONIT_MIGRATOR_URL"] == \
        "host=127.0.0.1 dbname=disponit user=m password=hemmelig"
    assert len(miljo["DISPONIT_TOKEN_PEPPER"]) == 64
    assert not any(v.startswith("'") or v.endswith("'")
                   for v in miljo.values())


@pg
def test_fordelingen_er_lik_lokalt(klient, policy, token, migrator):
    """Hele settet gjennom den EKTE beslutningsveien lokalt: hver
    kategori dømmes per svar (fail-closed i driveren), radene leses av
    revisjonsloggen via idempotensnøklene, og artefaktet — bygget av
    NØYAKTIG samme funksjon som staging-leddet bruker — består både det
    lukkede skjemaet og grensene. Én rad fjernet feller det."""
    from manifestskjema import _sjekk_grenser, valider_artefaktformat
    m = _lib()
    tok, _ = token()
    runde = secrets.token_hex(4)

    def _post(e, nokkel):
        r = post(klient, policy, e, tok, nokkel=nokkel)
        return r.status_code, (r.json().get("beslutning")
                               if r.status_code == 200 else None)

    def _tillat(ressurs):
        return hendelse(policy, ressurs=ressurs)

    def _uten(handling, ressurs):
        return hendelse_uten_attestasjoner(ressurs=ressurs,
                                           handling=handling)

    def _tukle(e):
        # snudd UTEN ny signering — signaturporten skal felle den, og
        # nettopp det avtrykket er STOPP-kategorien.
        e["attestasjoner"]["ingen_aktiv_tvist"]["resultat"] = False
        return e

    talt = m.kjor_sett(runde, _post, _tillat, _uten, _tukle)
    assert talt == m.FORDELING
    migrator.execute("RESET ROLE")
    migrator.execute("SELECT set_config('disponit.tenant', %s, false)",
                     (TENANT,))
    rader = migrator.execute(
        "SELECT id, beslutning FROM revisjonslogg WHERE tenant=%s"
        "  AND idempotency_key LIKE %s",
        (TENANT, f"m02f-{runde}-%")).fetchall()
    migrator.rollback()
    from manifestskjema import m02_bevisrot_sha256
    art = m.artefakt([(r[0], r[1]) for r in rader], TENANT, "lokal",
                     "2026-08-21T00:00:00+00:00", m02_bevisrot_sha256())
    assert art["bestatt"] is True
    assert valider_artefaktformat(art, "m02-fordeling-v1") == []
    assert _sjekk_grenser("m02-fordeling-v1", art) == []
    # Negative porter: én rad borte → fordelingen spriker; en beslutning
    # byttet → re-regningen feller den; gjentatt loggpost → én hendelse
    # er én rad.
    amputert = dict(art, rader=art["rader"][1:],
                    maalt=dict(art["maalt"]))
    assert any("fasiten" in f or "krever >=" in f
               for f in _sjekk_grenser("m02-fordeling-v1", amputert))
    annen = "TILLAT" if art["rader"][0][1] != "TILLAT" else "STOPP"
    byttet = dict(art, rader=[[art["rader"][0][0], annen]]
                  + [list(r) for r in art["rader"][1:]])
    assert any("fasiten" in f
               for f in _sjekk_grenser("m02-fordeling-v1", byttet))
    dublert = dict(art, rader=[list(art["rader"][0])]
                   + [list(r) for r in art["rader"][:-1]])
    assert any("gjentar" in f
               for f in _sjekk_grenser("m02-fordeling-v1", dublert))

