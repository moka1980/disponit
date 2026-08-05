"""PR-007: R1 som tofaseprotokoll — de ti Codex-portene og de fire vilkårene.

Samme krav som på PR-006: hver port har en test som DØR når vakten sin
fjernes. Mutasjonen som dreper testen står i docstringen, slik at den kan
kjøres — «porten finnes» og «porten virker» er to forskjellige påstander,
og bare den andre er verdt noe.

Testene uten database står først. En kjøring uten `DISPONIT_TEST_DSN` sier
da fortsatt noe om utvelgelsen, hashene og formkontrollen.
"""
import ast
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from .conftest import CORE, POLICIES
from .test_api import (DSN, NOKLER, TENANT, dekker, migrator, miljo,  # noqa: F401
                       malpolicy, policy, token)                      # noqa: F401
from .test_m37 import _lag_sak, _policyref, _rt, _sett_kontekst

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

MAAL = "purring.send"


# ---------------------------------------------------------------------------
# Policybyggere. Testene skal kunne si «vilkår A er betrodd V1 og V2» uten
# å skrive en hel bransjemal — men formen må være POLICYENS, ikke en egen
# testdialekt. En fixture uten produksjonens form beviser transport, ikke
# produksjon; det kostet oss et P1 som overlevde 341 grønne tester.
# ---------------------------------------------------------------------------

def _policy(betrodd: dict[str, list[str]], vilkaar: list[str], *,
            prioritet: dict[str, int] | None = None,
            kan_permanent: tuple[str, ...] = (),
            maks_alder_s: int | None = None,
            handling: str = MAAL) -> dict:
    """En minimal, men produksjonsformet policy."""
    # Vilkårets `verifikator` må være EN som faktisk er betrodd for det —
    # den semantiske validatoren krysspeiler de to, og en fixture som ikke
    # tåler kontrollen ville aldri nådd databasen.
    def _eier(v: str) -> str:
        for vid, vs in betrodd.items():
            if v in vs:
                return vid
        return next(iter(betrodd))

    h = {"id": handling, "modul": "M-23", "modus": "auto",
         "ved_brudd": "unntakskø", "tillatt_for": ["agent"],
         "dataklasser_tillatt": ["finansiell"],
         "reversering": {"type": "kompenserende",
                         "handling": "purring.beklag_og_korriger"},
         "vilkaar": [{"navn": v, "verifikator": _eier(v)} for v in vilkaar]}
    if maks_alder_s is not None:
        h["maks_attestasjon_alder_s"] = maks_alder_s
    ut = {"schema_version": "0.2",
          "meta": {"policy_id": "test-pol", "versjon": "1.0.0",
                   "bransjemal": "test-pol", "status": "utkast"},
          "tidssone": "Europe/Oslo",
          "roller": [{"id": "agent", "beskrivelse": "test"}],
          "dataklasser": ["finansiell"],
          "verifikatorer": {
              vid: {"betrodd_for": list(vs) or ["ubrukt_vilkaar"],
                    **({"kan_fastsla_permanent": True}
                       if vid in kan_permanent else {})}
              for vid, vs in betrodd.items()},
          "handlinger": [h],
          "unntak": {"maks_auto_forsok": 3, "eskalering": "unntakskø",
                     "kategorier": ["manglende_data", "over_grense",
                                    "regelkonflikt", "teknisk_feil",
                                    "ugyldig_data", "ukjent"]}}
    if prioritet:
        ut["verifikator_prioritet"] = dict(prioritet)
    return ut


# ===========================================================================
# Port 1-3 + vilkår V4 — utvelgelsen
# ===========================================================================

def test_port1_skjaering_med_en_dekkende_verifikator_gir_r1():
    """Codex-port 1: unionen har to, skjæringen har én → R1 kjøres.

    v1s formel («> 1 distinkt verifikator → manuell») ville stoppet denne
    saken, og den er nettopp den saken protokollen er laget for: V1 dekker
    HELE settet alene, selv om V2 også er betrodd for det ene vilkåret.

    MUTASJONEN SOM DREPER DENNE: bytt skjæringen i `velg_verifikator` mot
    en union — da er `|kandidater| = 2`, og en antalls-basert grense sender
    saken til manuell.
    """
    from m37 import reparasjoner
    p = _policy({"v1": ["a", "b"], "v2": ["a"]}, ["a", "b"])

    # Unionen ER større enn én. Står ikke dette i testen, måler den ikke
    # forskjellen mellom de to formlene — bare at noe ble valgt.
    union = reparasjoner.betrodde_for(p, "a") | reparasjoner.betrodde_for(p, "b")
    assert len(union) == 2, "testen måler ikke union mot skjæring"

    valg = reparasjoner.velg_verifikator(p, ["a", "b"])
    assert valg.verifikator == "v1", valg.grunn


def test_port2_tom_skjaering_gir_manuell_uten_oppdrag():
    """Codex-port 2: ingen enkelt verifikator dekker settet → `manuell`.

    Fler-verifikator er UTE av v1 (v7 pkt. 4 / v8). Da er det eneste
    ærlige svaret manuell kø — ikke et oppdrag til en verifikator som
    bare kan attestere halve settet.

    MUTASJONEN SOM DREPER DENNE: la `velg_verifikator` falle tilbake på
    «ta den første kandidaten for det første vilkåret» når skjæringen er
    tom. Da bygges det et oppdrag ingen kan fullføre.
    """
    from m37 import reparasjoner
    p = _policy({"v1": ["a"], "v2": ["b"]}, ["a", "b"])

    valg = reparasjoner.velg_verifikator(p, ["a", "b"])
    assert valg.verifikator is None
    assert valg.grunn == "krever_flere_verifikatorer", valg.grunn

    kl = _klassifisering("attestasjon_mangler")
    plan = reparasjoner.planlegg_verifikasjon(
        kl, {"handling": MAAL, "ressurs_id": "fak-1",
             "manglende_vilkaar": "a"}, p, "test-pol")
    assert plan.utfall == "manuell", plan.grunn
    assert plan.oppdragstype is None, "manuell sak bygget likevel et oppdrag"


def test_port3_og_vilkaar_V4_lik_prioritet_gir_stabilt_laveste_id():
    """Codex-port 3 + GO-vilkår V4: prioriteten er TOTAL.

    To kandidater med SAMME eksplisitte prioritet må ikke avgjøres av
    hvilken rekkefølge mengden tilfeldigvis itereres i. `verifikator_id`
    er sekundærnøkkelen, og den er unik — derfor er ordningen total.

    FØRSTE FORSØK MÅLTE INGENTING: den bygde to policyer med motsatt
    nøkkelrekkefølge og sammenlignet svarene. Men kandidatene er en
    `frozenset`, og en frozensets iterasjonsrekkefølge følger hashene, ikke
    dict-rekkefølgen — begge policyene ga samme rekkefølge, og mutasjonen
    «sorter kun på prioritet» overlevde. Samme lærdom som «en likhet
    trenger en UKORRELERT bryternøkkel»: testen må først finne et tilfelle
    der rekkefølgen faktisk skiller.

    MUTASJONEN SOM DREPER DENNE: fjern `vid` fra sorteringsnøkkelen i
    `velg_verifikator`.
    """
    from m37 import reparasjoner

    # Finn et kandidatsett der iterasjonsrekkefølgen IKKE starter på det
    # laveste id-et. Uten et slikt sett er en stabil sortering på
    # prioritet alene umulig å skille fra en korrekt total ordning.
    for i in range(2000):
        navn = [f"v_{i}_{j}" for j in range(6)]
        if next(iter(frozenset(navn))) != min(navn):
            break
    else:                                        # pragma: no cover
        pytest.skip("fant ingen mengde med skjev iterasjonsrekkefølge")

    p = _policy({n: ["a"] for n in navn}, ["a"],
                prioritet={n: 5 for n in navn})
    valgt = reparasjoner.velg_verifikator(p, ["a"]).verifikator
    assert valgt == min(navn), (
        f"valgte {valgt!r} — med lik prioritet skal laveste verifikator_id"
        f" ({min(navn)!r}) vinne, ikke den mengden tilfeldigvis nevner først"
        f" ({next(iter(frozenset(navn)))!r})")


def test_vilkaar_V4_ukjent_verifikator_i_prioritet_er_fail_closed():
    """GO-vilkår V4, andre halvdel: en prioritetsliste som nevner en
    verifikator som ikke finnes, endrer ALDRI hvem som velges.

    En ukjent id i prioriteringen er en policyfeil, og en policyfeil skal
    ikke kunne utpeke en verifikator som ikke er betrodd for settet.

    MUTASJONEN SOM DREPER DENNE: la `velg_verifikator` sortere kandidatene
    etter prioritetslisten UTEN først å filtrere på skjæringen.
    """
    from m37 import reparasjoner
    p = _policy({"v1": ["a"]}, ["a"], prioritet={"v_finnes_ikke": 1, "v1": 9})
    assert reparasjoner.velg_verifikator(p, ["a"]).verifikator == "v1"


# ===========================================================================
# Port 5 + vilkår V1 — hashene som binder beslutningen
# ===========================================================================

def test_port5_autoritetsregisteret_endrer_fase2_id_med_uendret_policyinnhold():
    """Codex-port 5 + GO-vilkår V1: en TILBAKETRUKKET fullmakt gir ny
    `fase2_id`, selv når resten av policyen står stille.

    Uten `autoritetsregister_hash` i identiteten kunne en fase-2-beslutning
    tatt under en fullmakt som senere ble trukket, gjenbrukes: samme
    tenant, samme sak, samme generasjon, samme policyhash — samme id.

    MUTASJONEN SOM DREPER DENNE: fjern `autoritetsregister_hash_` fra
    `fase2_id`s hashede felter.
    """
    from m37 import reparasjoner
    foer = _policy({"v1": ["a"], "v2": ["a"]}, ["a"])
    etter = _policy({"v1": ["a"]}, ["a"])          # v2 mistet fullmakten

    h_foer = reparasjoner.autoritetsregister_hash(foer)
    h_etter = reparasjoner.autoritetsregister_hash(etter)
    assert h_foer != h_etter, "registerhashen så ikke fullmaktsendringen"

    felles = dict(tenant=TENANT, unntak_id=7, maalhandling=MAAL,
                  generation=1, aktiv_policy_hash="p" * 64,
                  krav_sett_hash_="k" * 64)
    id_foer = reparasjoner.fase2_id(**felles, autoritetsregister_hash_=h_foer)
    id_etter = reparasjoner.fase2_id(**felles, autoritetsregister_hash_=h_etter)
    assert id_foer != id_etter, (
        "fase2_id overlevde at en verifikatorfullmakt ble trukket tilbake")


def test_autoritetsregisterhashen_ser_bort_fra_alt_annet_enn_fullmakter():
    """Registerhashen skal endre seg PRESIST når `betrodd_for` endres.

    Var den en hash over hele policyen, ville enhver kosmetisk redigering
    ugyldiggjort fase-2-identiteter — og «hashen endret seg» hadde sluttet
    å bety «noen mistet en fullmakt».
    """
    from m37 import reparasjoner
    a = _policy({"v1": ["a"]}, ["a"])
    b = _policy({"v1": ["a"]}, ["a"], maks_alder_s=3600)
    assert reparasjoner.autoritetsregister_hash(a) == \
        reparasjoner.autoritetsregister_hash(b)


# ===========================================================================
# Port 7 — resultathashen
# ===========================================================================

def test_port7_resultathash_dekker_innholdet_og_ikke_den_ytre_signaturen():
    """Codex-port 7 / Scope v2 pkt. 3.2: signer over innhold, hash over
    SAMME innhold, aldri over signaturen.

    Endret hashen seg med signaturen, ville hver re-signering av en
    uendret kvittering blitt et «motstridende resultat» — altså en
    sikkerhetssak utløst av at noen sendte det samme på nytt.

    MUTASJONEN SOM DREPER DENNE: la `resultathash_verifikasjon` hashe hele
    konvolutten inkludert `signatur`.
    """
    import oppdragskontrakt
    from policy_validator import attestering

    kropp = {"protokollversjon": 1,
             "kvitteringstype": "verifikasjonskvittering_v1",
             "tenant_id": TENANT, "oppdrag_id": 1, "unntak_id": 2,
             "fase1_repair_operation_id": "r", "verification_generation": 1,
             "krav_sett_hash": "k" * 64, "verifikator": "v_fordring",
             "nokkel_id": "k1", "utstedt": "2026-08-04T00:00:00+00:00",
             "attestasjoner": [{"vilkaar": "a", "status": "negativ",
                                "permanent": False, "attestasjon": None}]}
    en = attestering.signer(kropp, "k1", NOKLER["v_fordring"]["k1"])
    to = dict(en, signatur={"alg": "hs256", "nokkel_id": "k1", "verdi": "0" * 64})

    assert oppdragskontrakt.resultathash_verifikasjon(en) == \
        oppdragskontrakt.resultathash_verifikasjon(to), (
            "resultathashen endret seg da bare den ytre signaturen ble byttet")

    endret = dict(en)
    endret["attestasjoner"] = [dict(en["attestasjoner"][0], status="attestert")]
    assert oppdragskontrakt.resultathash_verifikasjon(en) != \
        oppdragskontrakt.resultathash_verifikasjon(endret)


# ===========================================================================
# Port 8 — verifikatorvalget kan ikke påvirkes utenfra
# ===========================================================================

def test_port8_klienten_kan_ikke_uttrykke_et_verifikatorvalg():
    """Codex-port 8: hverken klient eller arbeider velger verifikator.

    To uavhengige mekanismer måles, fordi de svikter på ulike måter:

      1. FORMEN: `velg_verifikator` tar policyen og vilkårene — det finnes
         ingen parameter et ønske kunne kommet inn gjennom.
      2. OPPFØRSELEN: en payload som PRØVER å utpeke en verifikator
         påvirker ikke resultatet.

    MUTASJONEN SOM DREPER DENNE: la `planlegg_verifikasjon` lese
    `payload.get("valgt_verifikator")` som overstyring.
    """
    import inspect
    from m37 import reparasjoner

    params = list(inspect.signature(reparasjoner.velg_verifikator)
                  .parameters)
    assert params == ["policy", "innhentbare"], params

    p = _policy({"v_alfa": ["a"], "v_beta": ["a"]}, ["a"])
    kl = _klassifisering("attestasjon_mangler")
    ren = reparasjoner.planlegg_verifikasjon(
        kl, {"handling": MAAL, "ressurs_id": "fak-1",
             "manglende_vilkaar": "a"}, p, "test-pol")
    forsok = reparasjoner.planlegg_verifikasjon(
        kl, {"handling": MAAL, "ressurs_id": "fak-1", "manglende_vilkaar": "a",
             "valgt_verifikator": "v_beta", "verifikator": "v_beta"},
        p, "test-pol")
    assert ren.valgt_verifikator == forsok.valgt_verifikator == "v_alfa"


def test_port8b_oppdragsskjemaet_har_intet_verifikatorfelt():
    """Samme port, tredje mekanisme: feltet finnes ikke i kontrakten.

    Kunne oppdraget båret et verifikatorfelt, ville en modul som plukker
    oppdraget kunne lest det som «du er valgt» — og valget hadde flyttet
    seg fra serveren til meldingen.
    """
    import oppdragskontrakt
    t = oppdragskontrakt.type_for_handling("verifiser.a")
    assert t is not None and t.navn == "verifikasjon"
    assert not (t.felter & {"valgt_verifikator", "verifikator",
                            "verifikator_id"}), sorted(t.felter)


# ===========================================================================
# Port 10 + vilkår V2 — hva som IKKE finnes i v1
# ===========================================================================

def test_port10_ingen_flerverifikator_akkumulering_finnes_i_v1():
    """Codex-port 10: v8s delsett-mekanikk er UTE av scope, og det måles.

    v7 pkt. 4 og v8 gjenåpnet delvis akkumulering over flere verifikatorer.
    Klarsignalet holdt det utenfor v1. En «vi implementerte det ikke»-
    påstand er verdiløs uten en test som faller den dagen noen begynner.
    """
    sql = (CORE / "db/migrations/007_r1_tofase.sql").read_text(encoding="utf-8")
    for forbudt in ("verifikasjonsdel", "mottatt_delsett", "delbevis"):
        assert f"TABLE {forbudt}" not in sql and f"TABLE IF NOT EXISTS {forbudt}" \
            not in sql, f"migrasjon 007 innfører {forbudt} — utenfor v1-scope"

    for modul in ("m37/reparasjoner.py", "m37/arbeider.py", "api/app.py",
                  "oppdragskontrakt.py"):
        tekst = (CORE / modul).read_text(encoding="utf-8")
        assert "verifikasjonsdel" not in tekst, modul
        assert "mottatt_delsett" not in tekst, modul


def test_vilkaar_V2_ferskhetskontrakten_lover_ikke_revokasjonsoppslag():
    """GO-vilkår V2: kontrakten skal være NØYAKTIG, ikke rundhåndet.

    PR-007 implementerer ikke per-attestasjon-revokasjon. Ferskhet er
    derfor: gyldig signatur ∧ ikke utløpt ∧ innen `maks_attestasjon_alder_s`
    ∧ verifikator fortsatt autorisert. Tre av de fire måles av egne tester;
    denne måler at ingen kode eller kommentar PÅSTÅR den fjerde.

    En påstand om en kontroll som ikke finnes er verre enn et hull: hullet
    kan oppdages, påstanden gjør at ingen leter.
    """
    mistenkelig = ("revokasjonsregister", "revokert_jti", "jti_revokasjon",
                   "sjekk_revokasjon")
    for modul in ("m37/arbeider.py", "api/app.py", "oppdragskontrakt.py",
                  "policy_validator/attestering.py"):
        tekst = (CORE / modul).read_text(encoding="utf-8")
        for ord_ in mistenkelig:
            assert ord_ not in tekst, (
                f"{modul} nevner {ord_} — v1 har ingen slik mekanisme")


def test_vilkaar_V2_de_tre_implementerte_ferskhetsleddene_finnes():
    """Den positive halvdelen: de tre leddene vi FAKTISK har, er der.

    Uten denne ville forrige test bestått også hvis ferskhetskontrollen
    ble slettet i sin helhet — «ingen påstår noe» er trivielt sant i en tom
    fil.
    """
    app = (CORE / "api/app.py").read_text(encoding="utf-8")
    assert "attestering.verifiser(konvolutt" in app, "signaturleddet borte"
    assert "maks_attestasjon_alder_s" in app, "aldersleddet borte"
    assert "kan_fastsla_permanent" in app, "autoritetsleddet borte"
    arb = (CORE / "m37/arbeider.py").read_text(encoding="utf-8")
    assert "gyldig_til" in arb, "utløpsleddet borte fra fase 2"


# ===========================================================================
# Databasen: portene 4, 6 og 9 + aldersstaket
# ===========================================================================

def _klassifisering(grunnkode: str, kategori: str = "manglende_data"):
    from m37 import reparasjoner
    return reparasjoner.Klassifisering(
        utfall="behandle", grunn="handler_funnet", handler=reparasjoner.R1,
        grunnkode=grunnkode, kategori=kategori)


def _registrer_policy(conn, policy: dict, tenant: str = TENANT):
    from api import policyregister
    policyregister.registrer(conn, tenant, policy, policy["meta"]["status"])
    conn.commit()
    return policyregister.innholds_hash(policy)


def _fase1(migrator, tenant, policy, *, vilkaar="a"):
    """Sak → klassifisering → verifikasjonsoppdrag. -> (sak, oppdrag, plan)."""
    from db.pg import koble
    from m37 import arbeider

    h = _registrer_policy(migrator, policy, tenant)
    sak, _ = _lag_sak(migrator, tenant, hash_=h,
                      policy_id=policy["meta"]["policy_id"],
                      versjon=policy["meta"]["versjon"],
                      handling=MAAL, vilkaar=vilkaar)
    rt = koble(DSN)
    try:
        cid = arbeider._claim_id()
        s = arbeider.claim(rt, cid)
        assert s is not None and s.id == sak, "saken ble ikke claimet"
        plan, rid = arbeider.planlegg(rt, s, cid)
        assert plan.utfall == "verifikasjon", f"{plan.utfall}: {plan.grunn}"
        _sett_kontekst(migrator, tenant)
        opp = migrator.execute(
            "SELECT id FROM oppdrag WHERE tenant=%s AND unntak_id=%s"
            "   AND oppdragstype='verifikasjon'", (tenant, sak)).fetchone()
        migrator.rollback()
        return sak, (int(opp[0]) if opp else None), plan
    finally:
        rt.close()


def _lever_sett(migrator, tenant, *, verifikator="v_fordring",
                status="attestert", permanent=False, alder_s=5,
                verdier=None, forvent=200):
    """Fase 1s kvittering, levert gjennom den EKTE ingest-veien.

    Ikke ved å kalle `registrer_verifikasjonsbevis` direkte: den veien
    hopper over signaturkontrollen, autoritetskontrollen og
    kapabilitetsforbruket — altså over nettopp de portene disse testene
    handler om. En fixture som omgår porten måler ikke porten.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app

    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            tok = _verifikatortoken(migrator, tenant)
            o = _claim_oppdrag(c, tok)
            assert o is not None, "verifikasjonsoppdraget lå ikke i køen"
            konvolutt = _verifikatorkvittering(
                o, verifikator=verifikator, status=status,
                permanent=permanent, alder_s=alder_s, verdier=verdier)
            r = c.post("/v1/oppdrag/kvittering",
                       json={"kvittering_jti": o["kvittering_jti"],
                             "konvolutt": konvolutt},
                       headers={"authorization": f"Bearer {tok}"})
            assert r.status_code == forvent, r.text
            return r.json()
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_port4_og_vilkaar_V1_autoritet_tilbakekalt_etter_ingest_stopper_fase2(
        migrator, miljo):
    """Codex-port 4 + GO-vilkår V1.

    Beviset er lovlig innhentet, signaturen holdt, generasjonen er
    `positiv` — og så trekkes verifikatorens fullmakt tilbake. Fase 2 må
    stoppe. Snapshotet beviser FORSØKET; det er ikke et fullmaktsbevis, og
    en tilbaketrukket fullmakt må fanges på nåtid.

    MUTASJONEN SOM DREPER DENNE: la fase 2 måle `valgt_verifikator` mot
    generasjonens frosne `autoritetsregister_versjon` i stedet for mot
    aktiv policy. Da bygges hendelsen som om ingenting hadde skjedd.
    """
    from db.pg import koble
    from m37 import arbeider

    t = "t-p4-" + secrets.token_hex(3)
    _rydd_tenant(migrator, t)
    p = _policy({"v_fordring": ["a", "b"]}, ["a", "b"])
    sak, opp, plan = _fase1(migrator, t, p, vilkaar="a")
    assert plan.valgt_verifikator == "v_fordring"
    assert _lever_sett(migrator, t)["status"] == "positiv"

    # Fullmakten trekkes tilbake: `b` betros nå en ANNEN verifikator.
    # Slik ser en tilbaketrekking faktisk ut — å tømme `betrodd_for` mens
    # vilkåret fortsatt peker på verifikatoren er ikke en gyldig policy i
    # det hele tatt, og validatoren avviser den før den når databasen.
    _registrer_policy(migrator,
                      _policy({"v_fordring": ["a"], "v_regnskap": ["b"]},
                              ["a", "b"]), t)

    rt = koble(DSN)
    try:
        cid = arbeider._claim_id()
        s = arbeider.claim(rt, cid)
        assert s is not None and s.fase == "fase2", s
        p2, rid = arbeider.planlegg(rt, s, cid)
        assert p2.utfall == "manuell", f"{p2.utfall}: {p2.grunn}"
        assert p2.grunn == "autoritet_tilbakekalt", p2.grunn
        assert rid is None, "det ble bygget en fase-2-identitet likevel"
    finally:
        rt.close()

    _sett_kontekst(migrator, t)
    hendelser = [r[0] for r in migrator.execute(
        "SELECT hendelse FROM unntak_historikk WHERE tenant=%s AND unntak_id=%s",
        (t, sak)).fetchall()]
    migrator.rollback()
    assert "sikkerhetsfrysing" in hendelser, hendelser


@pg
def test_port6_og_vilkaar_V3_fjernet_vilkaar_sendes_ikke_videre(migrator, miljo):
    """Codex-port 6 + GO-vilkår V3: policyen har FJERNET et vilkår.

    Da bygges fase 2 kun med de attestasjonene aktiv policy fortsatt
    krever. En overflødig attestasjon er evidens for et krav som ikke
    finnes, og den skal ikke følge med videre.

    MUTASJONEN SOM DREPER DENNE: la fase 2 legge HELE `attestasjoner`-
    settet inn i hendelsen uten å skjære det mot aktiv policy.
    """
    from db.pg import koble
    from m37 import arbeider

    t = "t-p6-" + secrets.token_hex(3)
    _rydd_tenant(migrator, t)
    sak, opp, plan = _fase1(migrator, t, _policy({"v_fordring": ["a", "b"]},
                                                 ["a", "b"]), vilkaar="a")
    assert _lever_sett(migrator, t)["status"] == "positiv"

    # Aktiv policy krever nå BARE `a`.
    _registrer_policy(migrator, _policy({"v_fordring": ["a", "b"]}, ["a"]), t)

    rt = koble(DSN)
    try:
        cid = arbeider._claim_id()
        s = arbeider.claim(rt, cid)
        assert s is not None and s.fase == "fase2"
        p2, _ = arbeider.planlegg(rt, s, cid)
    finally:
        rt.close()

    assert p2.reparasjonsinput is not None, p2.grunn
    sendt = set((p2.reparasjonsinput.get("attestasjoner") or {}))
    assert sendt == {"a"}, (
        f"fase 2 sendte {sorted(sendt)} — `b` er ikke lenger et krav")

    _sett_kontekst(migrator, t)
    hendelser = [r[0] for r in migrator.execute(
        "SELECT hendelse FROM unntak_historikk WHERE tenant=%s AND unntak_id=%s",
        (t, sak)).fetchall()]
    migrator.rollback()
    assert "policy_endret_siden_opprettelse" in hendelser, hendelser


@pg
def test_v6_pkt5_ett_utlopt_bevis_gir_aldri_en_delvis_hendelse(migrator, miljo):
    """v6 pkt. 5: ett utløpt bevis ⇒ INGEN hendelse.

    Fase 2 bygger aldri med et delvis utløpt sett. Den lapper heller ikke
    den gamle generasjonen (v7 pkt. 3) — et nytt løp er et FRISKT sett fra
    bunnen.

    MUTASJONEN SOM DREPER DENNE: fjern utløpssjekken i `_fase2` og la
    settet gå videre; motoren ville da vært eneste port, og saken hadde
    brukt en beslutning på et bevis vi visste var dødt.
    """
    from db.pg import koble
    from m37 import arbeider

    t = "t-utl-" + secrets.token_hex(3)
    _rydd_tenant(migrator, t)
    p = _policy({"v_fordring": ["a", "b"]}, ["a", "b"])
    sak, opp, plan = _fase1(migrator, t, p, vilkaar="a")
    assert _lever_sett(migrator, t)["status"] == "positiv"

    # Ett av de to bevisene utløper. Append-only-tabellen tåler ikke UPDATE,
    # så tiden flyttes der den KAN flyttes: på raden, som eier av skjemaet,
    # gjennom den ene kolonnen som ikke er beskyttet av en trigger.
    _sett_kontekst(migrator, t)
    migrator.execute(
        "ALTER TABLE verifikasjonsbevis DISABLE TRIGGER bevis_ingen_endring")
    migrator.execute(
        "UPDATE verifikasjonsbevis SET gyldig_til = now() - interval '1 minute'"
        " WHERE tenant=%s AND unntak_id=%s AND bevis_vilkaar='b'", (t, sak))
    migrator.execute(
        "ALTER TABLE verifikasjonsbevis ENABLE TRIGGER bevis_ingen_endring")
    migrator.commit()

    rt = koble(DSN)
    try:
        cid = arbeider._claim_id()
        s = arbeider.claim(rt, cid)
        assert s is not None and s.fase == "fase2"
        p2, rid = arbeider.planlegg(rt, s, cid)
    finally:
        rt.close()
    assert not (p2.reparasjonsinput or {}).get("attestasjoner"), (
        "det ble bygget en hendelse med et delvis utløpt sett")
    assert p2.grunn == "bevis_utlopt", p2.grunn
    assert rid is None

    _sett_kontekst(migrator, t)
    status = migrator.execute("SELECT status FROM unntak WHERE tenant=%s AND id=%s",
                              (t, sak)).fetchone()[0]
    migrator.rollback()
    assert status in ("verifikasjon_retry_klar", "manuell"), status


def _rydd_tenant(migrator, tenant):
    """Fersk tenant OG tom verifikasjonskø.

    Oppdragskøen er PÅ TVERS AV TENANTER med vilje (en eiermodul betjener
    mange kunder — se `_oppdrag_claim`). Filtreringen er på eiermodul, og
    alle verifikasjonsoppdrag havner i `eiermodul:verifikasjon`. En test som
    bare rydder sin egen tenant plukker derfor opp restene fra forrige test
    og måler en annen sak enn den tror.

    Samme lærdom som `_unik_eiermodul()` i PR-006-testene, i en dimensjon
    der modulnavnet ikke kan velges fritt: prefikset følger handlingen.
    """
    from .test_api import _rydd
    # Køen ryddes for ALLE tenanter som har et verifikasjonsoppdrag
    # liggende — ikke bare for den nye. Restene overlever pytest-sesjoner,
    # så en modulnivå-liste hadde ikke holdt.
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    andre = [r[0] for r in migrator.execute(
        "SELECT DISTINCT tenant FROM oppdrag"
        " WHERE oppdragstype='verifikasjon'").fetchall()]
    migrator.execute("RESET ROLE")
    migrator.rollback()
    # ÉN tenant per `_rydd`-kall, ikke alle i én transaksjon: sletting av
    # bevis etterlater ventende hendelser på den utsatte fremmednøkkelen,
    # og `ALTER TABLE ... ENABLE TRIGGER` nekter da med «pending trigger
    # events». Hvert kall committer for seg.
    for t in sorted({tenant, *andre}):
        _rydd(migrator, t)


# ===========================================================================
# API-veien: port 9 og aldersstaket
# ===========================================================================

def _verifikatorkvittering(o: dict, *, verifikator="v_fordring", nokkel="k1",
                           hemmelighet=None, status="attestert",
                           permanent=False, alder_s=5, verdier=None) -> dict:
    """Bygg settkvitteringen slik den syntetiske verifikatoren gjør det.

    Modulen lastes fra `deploy/staging/` og ikke kopieres hit MED VILJE:
    en kopi av konvoluttbyggeren i testene ville målt kopien, ikke det
    staging faktisk sender. Da PR-006 hadde to kopier av samme regel, var
    det nettopp forskjellen mellom dem som ble et funn.
    """
    import importlib.util
    sti = CORE.parents[1] / "deploy/staging/syntetisk-verifikator.py"
    spec = importlib.util.spec_from_file_location("syntver", sti)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    resultat = {"attestert": "positiv", "negativ": "negativ",
                "ikke_attesterbar": "ikke_attesterbar"}[status]
    return mod.bygg_konvolutt(
        o, verifikator=verifikator, nokkel_id=nokkel,
        hemmelighet=hemmelighet or NOKLER[verifikator][nokkel],
        resultat=resultat, permanent=permanent, alder_s=alder_s,
        verdier=verdier)


@pg
@dekker("attestasjon_for_gammel")
def test_v7_pkt1_attestasjon_eldre_enn_policytaket_avvises(migrator, miljo,
                                                          token):
    """v7 pkt. 1: `maks_attestasjon_alder_s` er et TAK verifikatoren ikke
    kan heve med sitt eget `utloper`.

    Attestasjonen her er gyldig i to timer til — og likevel for gammel,
    fordi tenantens policy sier at et faktum eldre enn 60 sekunder ikke
    lenger er grunnlag for en beslutning.

    MUTASJONEN SOM DREPER DENNE: fjern aldersleddet i `_ingest_verifikasjon`
    og stol på `utloper` alene.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app

    t = "t-alder-" + secrets.token_hex(3)
    _rydd_tenant(migrator, t)
    p = _policy({"v_fordring": ["a", "b"]}, ["a", "b"], maks_alder_s=60)
    sak, opp, _ = _fase1(migrator, t, p, vilkaar="a")

    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            tok = _verifikatortoken(migrator, t)
            o = _claim_oppdrag(c, tok)
            assert o is not None and o["oppdrag_id"] == opp
            konvolutt = _verifikatorkvittering(
                o, verifikator="v_fordring", alder_s=3600)
            r = c.post("/v1/oppdrag/kvittering",
                       json={"kvittering_jti": o["kvittering_jti"],
                             "konvolutt": konvolutt},
                       headers={"authorization": f"Bearer {tok}"})
            assert r.status_code == 403, r.text
            assert r.json()["feil"] == "attestasjon_for_gammel", r.text
    finally:
        a.tjeneste.pool.lukk()

    _sett_kontekst(migrator, t)
    antall = migrator.execute(
        "SELECT count(*) FROM verifikasjonsbevis WHERE tenant=%s", (t,)
    ).fetchone()[0]
    migrator.rollback()
    assert antall == 0, "en for gammel attestasjon ble lagret som bevis"


@pg
def test_port9_permanent_uten_saerskilt_autoritet_behandles_som_negativ(
        migrator, miljo, token):
    """Codex-port 9 + Scope v2 pkt. 5.

    `betrodd_for` gir rett til å ATTESTERE et vilkår. Å erklære det
    PRINSIPIELT uinnhentbart er en større fullmakt, og uten
    `kan_fastsla_permanent` skal påstanden behandles som en forbigående
    negativ — altså retry per budsjett, ikke en låst sak.

    Ellers kunne én verifikator parkere enhver sak permanent ved å sette
    ett flagg i sin egen kvittering.

    MUTASJONEN SOM DREPER DENNE: send `e["permanent"]` rett videre til
    `registrer_verifikasjonsbevis` uten å måle den mot autoritetsregisteret.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app

    t = "t-perm-" + secrets.token_hex(3)
    _rydd_tenant(migrator, t)
    # v_fordring er betrodd for begge vilkår, men har IKKE
    # `kan_fastsla_permanent`.
    p = _policy({"v_fordring": ["a", "b"]}, ["a", "b"])
    sak, opp, _ = _fase1(migrator, t, p, vilkaar="a")

    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            tok = _verifikatortoken(migrator, t)
            o = _claim_oppdrag(c, tok)
            konvolutt = _verifikatorkvittering(
                o, verifikator="v_fordring", status="ikke_attesterbar",
                permanent=True)
            r = c.post("/v1/oppdrag/kvittering",
                       json={"kvittering_jti": o["kvittering_jti"],
                             "konvolutt": konvolutt},
                       headers={"authorization": f"Bearer {tok}"})
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "negativ", r.text
    finally:
        a.tjeneste.pool.lukk()

    _sett_kontekst(migrator, t)
    status = migrator.execute("SELECT status FROM unntak WHERE tenant=%s AND id=%s",
                              (t, sak)).fetchone()[0]
    migrator.rollback()
    assert status == "verifikasjon_retry_klar", (
        f"status={status} — en uautorisert permanent-påstand låste saken")


@pg
def test_port9b_permanent_MED_autoritet_gir_manuell_direkte(migrator, miljo,
                                                            token):
    """Kontrollen til forrige test: med fullmakten VIRKER påstanden.

    Uten denne ville port 9 bestått også hvis `permanent` var fjernet helt
    fra systemet — «behandles som negativ» er trivielt sant når ingenting
    kan være permanent.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app

    t = "t-perm2-" + secrets.token_hex(3)
    _rydd_tenant(migrator, t)
    p = _policy({"v_fordring": ["a", "b"]}, ["a", "b"],
                kan_permanent=("v_fordring",))
    sak, opp, _ = _fase1(migrator, t, p, vilkaar="a")

    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            tok = _verifikatortoken(migrator, t)
            o = _claim_oppdrag(c, tok)
            r = c.post("/v1/oppdrag/kvittering",
                       json={"kvittering_jti": o["kvittering_jti"],
                             "konvolutt": _verifikatorkvittering(
                                 o, verifikator="v_fordring",
                                 status="ikke_attesterbar", permanent=True)},
                       headers={"authorization": f"Bearer {tok}"})
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "permanent_uinnhentbar", r.text
    finally:
        a.tjeneste.pool.lukk()

    _sett_kontekst(migrator, t)
    status = migrator.execute("SELECT status FROM unntak WHERE tenant=%s AND id=%s",
                              (t, sak)).fetchone()[0]
    migrator.rollback()
    assert status == "manuell", status


@pg
def test_scope_v2_pkt2_tilbakekalt_autoritet_avviser_kvitteringen(migrator,
                                                                  miljo, token):
    """Scope v2 pkt. 2, ingest-halvdelen: fullmakten måles på NÅTID.

    Signaturen er gyldig, settet er komplett, og kvitteringen avvises
    likevel — fordi verifikatoren ikke lenger er betrodd for ett av
    vilkårene. Et delvis betrodd sett er ikke et sett.

    MUTASJONEN SOM DREPER DENNE: la ingest sjekke `betrodd_for` mot
    generasjonens frosne autoritetsversjon i stedet for mot aktiv policy.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app

    t = "t-tilb-" + secrets.token_hex(3)
    _rydd_tenant(migrator, t)
    sak, opp, _ = _fase1(migrator, t,
                         _policy({"v_fordring": ["a", "b"]}, ["a", "b"]),
                         vilkaar="a")

    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            tok = _verifikatortoken(migrator, t)
            o = _claim_oppdrag(c, tok)
            # Fullmakten for `b` trekkes tilbake ETTER at oppdraget ble
            # plukket, men FØR kvitteringen lander.
            _registrer_policy(
                migrator,
                _policy({"v_fordring": ["a"], "v_regnskap": ["b"]},
                        ["a", "b"]), t)
            r = c.post("/v1/oppdrag/kvittering",
                       json={"kvittering_jti": o["kvittering_jti"],
                             "konvolutt": _verifikatorkvittering(
                                 o, verifikator="v_fordring")},
                       headers={"authorization": f"Bearer {tok}"})
            assert r.status_code == 403, r.text
            assert r.json()["feil"] == "kvittering_signatur_ugyldig", r.text
    finally:
        a.tjeneste.pool.lukk()

    _sett_kontekst(migrator, t)
    antall = migrator.execute(
        "SELECT count(*) FROM verifikasjonsbevis WHERE tenant=%s", (t,)
    ).fetchone()[0]
    migrator.rollback()
    assert antall == 0, "bevis lagret tross tilbakekalt fullmakt"


# ===========================================================================
# Codex runde 4 — den ene serialiseringsporten
# ===========================================================================

def _oppsett_ingest(migrator, tenant, *, vilkaar=("a", "b")):
    """Sak → fase 1 → oppdraget PLUKKET. -> (sak, oppdrag_id, claim-svaret).

    Stopper der kvitteringen skal leveres, slik at testene under kan styre
    NØYAKTIG hvem som leverer hva, og i hvilken rekkefølge.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app

    p = _policy({"v_fordring": list(vilkaar)}, list(vilkaar))
    sak, opp, plan = _fase1(migrator, tenant, p, vilkaar=vilkaar[0])
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            tok = _verifikatortoken(migrator, tenant)
            o = _claim_oppdrag(c, tok)
            assert o is not None and o["oppdrag_id"] == opp, o
    finally:
        a.tjeneste.pool.lukk()
    return sak, opp, o, tok


def _vinner_holder_generasjonslaasen(tenant, sak, oppdrag_id, konvolutt,
                                     resultathash, migrator):
    """En transaksjon som gjør NØYAKTIG det vinneren gjør — og holder igjen.

    Vinneren kaller den ENE skriveveien som RUNTIME-rollen og lar være å
    committe. Taperen blokkerer da på `unntak`/generasjonslåsen og får
    først svar etter at vinneren har committet.

    Dette er ikke en timing-test: blokkerer taperen, klassifiseres den mot
    vinnerens committede rader; kommer den etter commiten, klassifiseres
    den mot nøyaktig de samme radene. Begge interleavinger gir samme
    utfall.
    """
    from db.pg import koble

    _sett_kontekst(migrator, tenant)
    eier = migrator.execute(
        "SELECT owner_claim_id, owner_generation, repair_operation_id"
        "  FROM oppdrag WHERE tenant=%s AND id=%s",
        (tenant, oppdrag_id)).fetchone()
    gen = migrator.execute(
        "SELECT verification_generation FROM unntak WHERE tenant=%s AND id=%s",
        (tenant, sak)).fetchone()[0]
    migrator.rollback()

    # Bevisraden har fremmednøkkel til `tenant_nokler`: en key_id som ikke
    # finnes er ikke en gyldig krypteringsnøkkel, og en bevisrad som peker
    # på ingenting er ikke dekrypterbar evidens. Vinneren bruker derfor
    # tenantens ekte, aktive DEK — som den ordinære veien ville gjort.
    from db import kryptering
    _sett_kontekst(migrator, tenant)
    key_id, _dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, tenant)
    migrator.commit()

    naa = datetime.now(timezone.utc)
    resultater = [
        {"vilkaar": e["vilkaar"], "status": "attestert", "permanent": False,
         "attestasjon_kryptert": "00", "key_id": key_id, "nonce": "00",
         "integritet_hash": secrets.token_hex(32),
         "gyldig_til": (naa + timedelta(hours=1)).isoformat()}
        for e in konvolutt["attestasjoner"]]

    h = koble(DSN)
    h.execute("SELECT set_config('disponit.tenant',%s,true),"
              "       set_config('disponit.aktor','vinner',true),"
              "       set_config('disponit.request_id','vinner',true)",
              (tenant,))
    utfall = h.execute(
        "SELECT registrer_verifikasjonsbevis(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (oppdrag_id, resultathash, konvolutt["krav_sett_hash"],
         konvolutt["verifikator"], "vinner", "vinner-sig",
         json.dumps(resultater), eier[0], eier[1], gen, eier[2])).fetchone()[0]
    assert utfall == "positiv", f"vinneren vant ikke: {utfall}"
    return h                        # IKKE committet — låsen holdes


def _lever_i_traad(tenant, token_str, o, konvolutt):
    """Poster kvitteringen i en egen tråd, så hovedtråden kan committe
    vinneren mens taperen står på låsen."""
    import threading
    from starlette.testclient import TestClient
    from api.app import lag_app

    svar = {}
    app = lag_app(DSN)
    klient = TestClient(app)
    klient.__enter__()

    def poster():
        try:
            svar["r"] = klient.post(
                "/v1/oppdrag/kvittering",
                json={"kvittering_jti": o["kvittering_jti"],
                      "konvolutt": konvolutt},
                headers={"authorization": f"Bearer {token_str}"})
        except Exception as e:                     # pragma: no cover
            svar["feil"] = e

    t = threading.Thread(target=poster)
    t.start()
    return t, svar, (klient, app)


def _avslutt_traad(t, ressurser, holder):
    import time as _t
    _t.sleep(0.7)                  # la taperen rekke fram til låsen
    holder.commit()
    holder.close()
    t.join(timeout=30)
    klient, app = ressurser
    try:
        klient.__exit__(None, None, None)
    finally:
        app.tjeneste.pool.lukk()
    assert not t.is_alive(), "taperen hang på låsen"


def _tilstand(migrator, tenant, sak, opp):
    _sett_kontekst(migrator, tenant)
    bevis = migrator.execute(
        "SELECT count(*) FROM verifikasjonsbevis WHERE tenant=%s AND unntak_id=%s",
        (tenant, sak)).fetchone()[0]
    konflikt = migrator.execute(
        "SELECT count(*) FROM verifikasjonskonflikt"
        " WHERE tenant=%s AND unntak_id=%s", (tenant, sak)).fetchone()[0]
    gen = migrator.execute(
        "SELECT status FROM verifikasjonsgenerasjon"
        " WHERE tenant=%s AND unntak_id=%s ORDER BY generation DESC LIMIT 1",
        (tenant, sak)).fetchone()
    rest = migrator.execute(
        "SELECT o.status, u.status, o.resultathash FROM oppdrag o JOIN unntak u"
        "   ON u.tenant=o.tenant AND u.id=o.unntak_id"
        " WHERE o.tenant=%s AND o.id=%s", (tenant, opp)).fetchone()
    migrator.rollback()
    return {"bevis": bevis, "konflikt": konflikt,
            "generasjon": gen[0] if gen else None,
            "oppdrag": rest[0], "sak": rest[1], "resultathash": rest[2]}


@pg
def test_P1_samtidig_identisk_settkvittering_blir_idempotent(migrator, miljo,
                                                             token):
    """Codex P1 runde 4, tilfelle 1: SAMME hash levert samtidig.

    Taperen skal se vinnerens committede resultathash og svare
    `idempotent` — ikke `avvist`, og ikke ved å skrive et bevis til.
    """
    t = "t-samtid1-" + secrets.token_hex(3)
    _rydd_tenant(migrator, t)
    sak, opp, o, tok = _oppsett_ingest(migrator, t)
    konvolutt = _verifikatorkvittering(o, verifikator="v_fordring")

    import oppdragskontrakt
    vinnerhash = oppdragskontrakt.resultathash_verifikasjon(konvolutt)
    holder = _vinner_holder_generasjonslaasen(t, sak, opp, konvolutt,
                                              vinnerhash, migrator)
    tr, svar, res = _lever_i_traad(t, tok, o, konvolutt)
    _avslutt_traad(tr, res, holder)

    r = svar.get("r")
    assert r is not None, svar
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "idempotent", r.text

    st = _tilstand(migrator, t, sak, opp)
    assert st["bevis"] == 2, f"taperen skrev flere bevis: {st}"
    assert st["konflikt"] == 0, f"identisk resultat ble kalt konflikt: {st}"
    assert st["generasjon"] == "positiv" and st["oppdrag"] == "utfort", st


@pg
def test_P1_samtidig_motstridende_settkvittering_blir_konflikt(migrator, miljo,
                                                                token):
    """Codex P1 runde 4, tilfelle 2: ULIK hash levert samtidig.

    Den forrige versjonen leste oppdragsraden ULÅST, før begge låsene.
    To samtidige kall frøs derfor begge `resultathash = NULL`; vinneren
    skrev bevis og hash, taperen våknet på generasjonslåsen med et
    FORELDET record, så `g.status <> 'aktiv'` og returnerte ubetinget
    `idempotent`. To ULIKE konvolutter ble «positiv + idempotent» — og
    forsøket på motstridende evidens forsvant sporløst.

    MUTASJONEN SOM DREPER DENNE: les oppdraget kun én gang, ULÅST, før
    låsene — eller gjeninnfør `IF g.status <> 'aktiv' THEN RETURN
    'idempotent'` uten hash-sammenligning.
    """
    t = "t-samtid2-" + secrets.token_hex(3)
    _rydd_tenant(migrator, t)
    sak, opp, o, tok = _oppsett_ingest(migrator, t)
    konvolutt = _verifikatorkvittering(o, verifikator="v_fordring")

    # Vinneren leverer et ANNET resultat enn taperen.
    holder = _vinner_holder_generasjonslaasen(t, sak, opp, konvolutt,
                                              "f" * 64, migrator)
    tr, svar, res = _lever_i_traad(t, tok, o, konvolutt)
    _avslutt_traad(tr, res, holder)

    r = svar.get("r")
    assert r is not None, svar
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "kvittering_konflikt", r.text

    st = _tilstand(migrator, t, sak, opp)
    assert st["bevis"] == 2, f"taperen skrev bevis: {st}"
    assert st["konflikt"] == 1, (
        f"nøyaktig ÉN konfliktrad forventet, fikk {st['konflikt']}")
    assert st["generasjon"] == "positiv", st
    assert st["oppdrag"] == "utfort" and st["resultathash"] == "f" * 64, (
        f"taperen endret vinnerens tilstand: {st}")


@pg
def test_P1_signert_generasjon_valideres_mot_databasen(migrator, miljo, token):
    """Codex P1 runde 4: `verification_generation` er signert OG bindende.

    Feltet er obligatorisk i konvolutten og ligger inne i de signerte
    bytene — men ingen sammenlignet det med noe. En ellers gyldig,
    ekte signert konvolutt kunne dermed lyve om hvilken runde den gjaldt
    og likevel bli akseptert som positivt bevis.

    Bindingen går OPPDRAG → FROSSET GENERASJONSRAD, ikke via sakens
    nåværende generasjon: den kan ha rykket videre.

    MUTASJONEN SOM DREPER DENNE: fjern `p_verification_generation` fra
    sammenligningen i `registrer_verifikasjonsbevis`.
    """
    _binding_avvises(migrator, "t-genbind-", {"verification_generation": 99})


@pg
def test_P1_signert_fase1_id_valideres_mot_databasen(migrator, miljo, token):
    """Samme port, det andre feltet: `fase1_repair_operation_id`.

    Egen test og egen mutasjon — to felter som valideres av samme `IF`
    ville ellers vært dekket av én test, og den dagen noen deler opp
    betingelsen ville halve bindingen kunnet forsvinne i stillhet.

    MUTASJONEN SOM DREPER DENNE: fjern `p_fase1_repair_operation_id` fra
    sammenligningen.
    """
    _binding_avvises(migrator, "t-fasebind-",
                     {"fase1_repair_operation_id": "0" * 64})


def _binding_avvises(migrator, prefiks: str, overstyr: dict):
    """Signer en ellers gyldig konvolutt med ÉN løgn, og krev at porten
    avviser den ETTER ekte signaturverifikasjon — uten å brenne
    kapabiliteten og uten å endre noen tilstand."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    from policy_validator import attestering

    t = prefiks + secrets.token_hex(3)
    _rydd_tenant(migrator, t)
    sak, opp, o, tok = _oppsett_ingest(migrator, t)

    raa = _verifikatorkvittering(o, verifikator="v_fordring")
    løgn = {k: v for k, v in raa.items()
            if k not in ("signatur", "kanonisering")}
    løgn.update(overstyr)
    # SIGNERES PÅ NYTT. Ville vi bare byttet feltet etterpå, hadde
    # signaturkontrollen fanget det, og testen målt signaturen i stedet for
    # bindingen — den ville bestått også uten noen bindingskontroll.
    konvolutt = attestering.signer(løgn, "k1", NOKLER["v_fordring"]["k1"])
    assert attestering.verifiser(konvolutt, NOKLER), (
        "testen måler ikke bindingen hvis signaturen ikke holder")

    app = lag_app(DSN)
    try:
        with TestClient(app) as c:
            r = c.post("/v1/oppdrag/kvittering",
                       json={"kvittering_jti": o["kvittering_jti"],
                             "konvolutt": konvolutt},
                       headers={"authorization": f"Bearer {tok}"})
    finally:
        app.tjeneste.pool.lukk()
    assert r.status_code == 403, r.text
    assert r.json()["feil"] == "kvittering_signatur_ugyldig", r.text

    st = _tilstand(migrator, t, sak, opp)
    assert st["bevis"] == 0, f"bevis lagret tross feil binding: {st}"
    assert st["generasjon"] == "aktiv", st
    assert st["oppdrag"] == "plukket" and st["sak"] == "venter_verifikasjon", st
    assert st["resultathash"] is None, st
    assert st["konflikt"] == 1, (
        "avviket etterlot ingen sikkerhetsevidens — en signert konvolutt med"
        f" feil binding skal alltid gi ett spor, fikk {st['konflikt']}")

    # Kapabiliteten skal IKKE være brent: avviket er ikke et resultat.
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    status = migrator.execute(
        "SELECT status FROM kvitteringskapabiliteter WHERE jti=%s",
        (o["kvittering_jti"],)).fetchone()
    migrator.rollback()
    assert status is not None and status[0] != "brukt", (
        f"kapabiliteten ble brent av et avvist forsøk: {status}")


@pg
def test_akseptert_kvittering_krever_plukket_oppdrag_og_ventende_sak(
        migrator, miljo, token):
    """Codex, tillegg: «positiv» er ÉN atomisk tilstand.

    Er oppdraget ikke lenger `plukket` — eller saken ikke lenger
    `venter_verifikasjon` — kan kvitteringen ikke bli gyldig evidens. Uten
    kontrollen kunne funksjonen sette generasjonen positiv og returnere
    `positiv` mens status-UPDATE-en traff null rader: et DELVIS resultat
    med et helt navn.

    MUTASJONEN SOM DREPER DENNE: fjern statuskontrollen, eller fjern
    `GET DIAGNOSTICS`-sjekken på saken.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app

    t = "t-status-" + secrets.token_hex(3)
    _rydd_tenant(migrator, t)
    sak, opp, o, tok = _oppsett_ingest(migrator, t)

    # Saken flyttes ut av `venter_verifikasjon` mens oppdraget står plukket
    # — nøyaktig den tilstanden en samtidig utløpsjobb ville laget.
    _sett_kontekst(migrator, t)
    migrator.execute("UPDATE unntak SET status='manuell'"
                     " WHERE tenant=%s AND id=%s", (t, sak))
    migrator.commit()

    app = lag_app(DSN)
    try:
        with TestClient(app) as c:
            r = c.post("/v1/oppdrag/kvittering",
                       json={"kvittering_jti": o["kvittering_jti"],
                             "konvolutt": _verifikatorkvittering(
                                 o, verifikator="v_fordring")},
                       headers={"authorization": f"Bearer {tok}"})
    finally:
        app.tjeneste.pool.lukk()
    assert r.status_code == 403, r.text

    st = _tilstand(migrator, t, sak, opp)
    assert st["bevis"] == 0, st
    assert st["generasjon"] == "aktiv", st
    assert st["sak"] == "manuell" and st["oppdrag"] == "plukket", st


# ---------------------------------------------------------------------------
# Små API-hjelpere for testene over
# ---------------------------------------------------------------------------

def _verifikatortoken(migrator, tenant: str) -> str:
    from .test_api import _lag_token
    tok, _ = _lag_token(migrator, tenant, "eiermodul:verifikasjon",
                        ["orders:execute:verifiser."])
    migrator.commit()
    return tok


def _claim_oppdrag(klient, token_str: str) -> dict | None:
    r = klient.post("/v1/oppdrag/claim", json={},
                    headers={"authorization": f"Bearer {token_str}"})
    if r.status_code == 204:
        return None
    assert r.status_code == 200, r.text
    return r.json()


# ===========================================================================
# Kapabilitetens aktørrolle — funnet i fire-prosess-rundturen
# ===========================================================================

@pg
def test_kapabiliteten_baerer_sakens_opprinnelige_rolle_ikke_en_egen(migrator,
                                                                     miljo):
    """M-37 handler PÅ VEGNE AV den opprinnelige aktøren, aldri som seg selv.

    MÅLT i fire-prosess-rundturen: med den oppdiktede rollen `'m37'` svarte
    motoren `rolle_ikke_tillatt` på hver eneste fase-2-beslutning, og hver
    reparerbar sak endte manuelt. Alternativet — å legge `m37` inn i
    kundenes policyer — ville gitt M-37 en EGEN fullmakt, altså nøyaktig
    det «null egne fullmakter» forbyr.

    MUTASJONEN SOM DREPER DENNE: sett rollen tilbake til den faste
    strengen `"m37"` i `_preauth_kapabilitet`. Kapabilitetsraden bærer da
    fortsatt `agent` (databasen henter den fra loggposten), men
    beslutningen som faktisk blir tatt får feil rolle — derfor måler
    testen BEGGE: raden og den auditerte beslutningen.
    """
    from db.pg import koble
    from m37 import arbeider

    t = "t-rolle-" + secrets.token_hex(3)
    _rydd_tenant(migrator, t)
    sak, opp, _ = _fase1(migrator, t, _policy({"v_fordring": ["a"]}, ["a"]),
                         vilkaar="a")
    assert _lever_sett(migrator, t)["status"] == "positiv"

    # Beslutningen må gå gjennom det EKTE API-et. En stub ville bevist at
    # kapabilitetsraden bærer rollen — men det er databasen som skriver den
    # raden, og mutasjonen «gi kapabiliteten rollen m37 igjen» sitter i
    # pre-auth. Bare en ordentlig forespørsel måler hvilken rolle
    # beslutningen faktisk ble tatt under.
    from starlette.testclient import TestClient
    from api.app import lag_app

    sett = {}
    app = lag_app(DSN)
    try:
        with TestClient(app) as klient:

            class _Apiklient:
                def beslutt(self, *, kapabilitet_jti, policy_id, event,
                            idempotency_key):
                    sett["jti"] = kapabilitet_jti
                    r = klient.post(
                        "/v1/beslutning",
                        headers={"authorization": f"Kapabilitet {kapabilitet_jti}",
                                 "idempotency-key": idempotency_key},
                        json={"policy_id": policy_id, "event": event})
                    kropp = r.json() if r.content else {}
                    return arbeider.Beslutningssvar(
                        r.status_code, kropp.get("beslutning"), kropp)

            rt = koble(DSN)
            try:
                res = arbeider.behandle_en(rt, _Apiklient())
            finally:
                rt.close()
    finally:
        app.tjeneste.pool.lukk()

    assert res is not None and sett.get("jti"), (
        f"fase 2 ba aldri om en beslutning: {res}")

    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    rad = migrator.execute(
        "SELECT aktor_rolle FROM arbeidskapabiliteter WHERE jti=%s",
        (sett["jti"],)).fetchone()
    migrator.rollback()
    assert rad is not None, "kapabiliteten fantes ikke"

    _sett_kontekst(migrator, t)
    opprinnelig = migrator.execute(
        "SELECT r.aktor FROM revisjonslogg r JOIN unntak u"
        "   ON u.tenant=r.tenant AND u.loggpost_id=r.id"
        " WHERE u.tenant=%s AND u.id=%s", (t, sak)).fetchone()[0]
    roller = [(r[0], r[1]) for r in migrator.execute(
        "SELECT aktor, kilde FROM revisjonslogg WHERE tenant=%s ORDER BY id",
        (t,)).fetchall()]
    migrator.rollback()

    reparasjonen = [a for a, kilde in roller if kilde == "arbeidskapabilitet"]
    assert reparasjonen, f"ingen reparasjonsbeslutning ble logget: {roller}"
    assert all(a == opprinnelig for a in reparasjonen), (
        f"reparasjonen ble besluttet som {reparasjonen} — den skal bæres av"
        f" sakens opprinnelige rolle {opprinnelig!r}, aldri av en rolle"
        " systemet fant på for seg selv")
    assert "m37" not in reparasjonen, "rollen er fortsatt M-37s egen"

    assert rad[0] == opprinnelig, (
        f"kapabiliteten bar rollen {rad[0]!r}, sakens loggpost {opprinnelig!r}"
        " — rollen skal komme fra loggposten og ikke fra noe annet sted")


@pg
def test_arbeideren_kan_ikke_uttrykke_hvilken_rolle_den_vil_ha(migrator):
    """Samme invariant, den strukturelle halvdelen.

    Rollen er ikke en parameter til `utsted_arbeidskapabilitet` — like lite
    som handlingen er det (v4-delta pkt. 1). Angrepet «be om en annen
    rolle» lar seg ikke uttrykke i signaturen, og trenger derfor ingen
    kontroll som kan glippe.
    """
    sql = (CORE / "db/migrations/007_r1_tofase.sql").read_text(encoding="utf-8")
    start = sql.index("CREATE FUNCTION utsted_arbeidskapabilitet(")
    signatur = sql[start:sql.index(")", start)]
    assert "rolle" not in signatur.lower(), signatur
    assert "l.aktor AS opprinnelig_rolle" in sql, (
        "rollen hentes ikke lenger fra sakens egen loggpost")


# ===========================================================================
# Migrasjon 007 — kjørbar to ganger
# ===========================================================================

def test_migrasjon_007_dropper_for_den_gjenskaper_endrede_returtyper():
    """En migrasjon som ikke tåler å kjøres to ganger, er ikke idempotent.

    MÅLT: `start_verifikasjonsgenerasjon` hadde bare DROP for den GAMLE
    signaturen. En gjenkjøring falt på «already exists with same argument
    types» — samme felle som kostet 80 tester i PR-006, i ny drakt.

    Regelen som testes: enhver funksjon i 007 som endrer returtype
    (`CREATE FUNCTION` uten `OR REPLACE`) må ha en `DROP FUNCTION IF EXISTS`
    for NØYAKTIG den signaturen den selv oppretter.
    """
    sql = (CORE / "db/migrations/007_r1_tofase.sql").read_text(encoding="utf-8")
    skapt = [linje.split("(")[0].split()[-1]
             for linje in sql.splitlines()
             if linje.startswith("CREATE FUNCTION ")]
    assert skapt, "fant ingen CREATE FUNCTION å kontrollere"
    for navn in skapt:
        droppene = [l for l in sql.splitlines()
                    if l.startswith(f"DROP FUNCTION IF EXISTS {navn}(")]
        assert droppene, f"{navn} opprettes uten DROP — 007 kan ikke kjøres to ganger"
