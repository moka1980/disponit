"""Gjenåpne et validert utkast + versjonsvaktene (eiers krav 17/8).

Eier, ordrett: «man må kunne redigere samme policy selv etter validering …
men da kan den igjen bli attestert og validert.» Uten denne veien var et
validert utkast med en feil en blindgate: eneste utvei var å forkaste alt og
begynne på nytt — og en åpen runde sperret til og med forkastingen i opptil
24 timer.

Tre ting prøves her:
  * gjenåpningen selv: `validert → utkast`, hashen nullstilles (migrasjon
    033), en åpen runde trekkes tilbake, og hele redigér-valider-sløyfa
    virker igjen etterpå;
  * at 033 åpnet NØYAKTIG den ene overgangen — hashen er fortsatt frosset i
    alle andre retninger;
  * versjonsvaktene som stopper eiers 17/8-felle der utkastet arvet den
    aktive policyens egen versjon og døde uforklart ved rundeåpning:
    valideringen sier fra som tekst, og opprettelsen bytter en opptatt arvet
    versjon med neste ledige.
"""
import copy
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from api import policyadmin
from api import policyregister as pr

from .test_api import DSN, MIGRATOR_DSN

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
TEN = "t-gjenapne-" + secrets.token_hex(3)

_BASE = yaml.safe_load(
    (Path(__file__).resolve().parents[3] / "policies"
     / "bransjemal-tjenestebedrift.yaml").read_text(encoding="utf-8"))


def _dokument(pid, versjon="1.1.0"):
    """Et skjemagyldig dokument (bransjemalen) under utkastets identitet."""
    d = copy.deepcopy(_BASE)
    d["meta"] = {**(d.get("meta") or {}), "policy_id": pid,
                 "versjon": versjon, "status": "produksjon"}
    return d


def _mig():
    from db.pg import koble, sett_kontekst
    c = koble(MIGRATOR_DSN)
    sett_kontekst(c, TEN, "sys", "r0")
    return c


def _rt():
    from db.pg import koble
    return koble(DSN)


def _utkast(uid, pid, status="validert", versjon="1.1.0"):
    innhold = _dokument(pid, versjon)
    m = _mig()
    m.execute(
        "INSERT INTO policyutkast (tenant,utkast_id,policy_id,innhold,"
        "innholds_hash,status,opprettet_av) VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s)",
        (TEN, uid, pid, json.dumps(innhold),
         pr.innholds_hash(innhold) if status != "utkast" else None,
         status, "forf"))
    m.commit()
    m.close()
    return innhold


def _policyrad(c, pid, versjon="1.0.0"):
    innhold = {"meta": {"policy_id": pid, "versjon": versjon}}
    c.execute(
        "INSERT INTO policyer (tenant,policy_id,versjon,innholds_hash,status,"
        "innhold,aktiv) VALUES (%s,%s,%s,%s,'produksjon',%s::jsonb,true)",
        (TEN, pid, versjon, "h-" + secrets.token_hex(8),
         json.dumps(innhold)))
    c.execute(
        "INSERT INTO policy_hode (tenant,policy_id,aktiv_versjon)"
        " VALUES (%s,%s,%s) ON CONFLICT (tenant,policy_id)"
        " DO UPDATE SET aktiv_versjon=EXCLUDED.aktiv_versjon",
        (TEN, pid, versjon))


def _runde(uid, utloper="1 hour", status="apen"):
    m = _mig()
    m.execute(
        "INSERT INTO aktiveringsrunde (tenant,utkast_id,runde,status,"
        "diff_hash,utkast_innholds_hash,base_policy_hash,risikoklasse,"
        "klassifisering_hash,klassifikatorversjon,policyskjema_versjon,"
        "motor_semantikkversjon,deny_all_hash,deny_all_versjon,"
        "pakrevd_antall_godkjennere,utloper)"
        f" VALUES (%s,%s,1,'{status}','dh','ih','bh','UTVIDER','kh','1','0.2',"
        f"'1','dah','1',2,now()+interval '{utloper}')", (TEN, uid))
    m.commit()
    m.close()


def _gjenapne(rt, uid, ver=1, naa=None):
    idem = secrets.token_hex(8)
    return policyadmin.gjenapne_utkast(
        rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
        forventet_utkastversjon=ver, idempotency_key=idem,
        input_hash=f"{TEN}\x1f{uid}\x1fgjenapne\x1f{ver}\x1f{idem}",
        naa=naa or datetime.now(timezone.utc))


def _rediger(rt, uid, ver, innhold):
    idem = secrets.token_hex(8)
    return policyadmin.rediger_utkast(
        rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
        forventet_utkastversjon=ver, innhold=innhold,
        idempotency_key=idem, input_hash="ih-" + idem)


def _valider(rt, uid, ver, idem=None):
    idem = idem or secrets.token_hex(8)
    return policyadmin.valider_utkast(
        rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
        forventet_utkastversjon=ver, idempotency_key=idem,
        input_hash="ih-" + idem)


def _rad(uid):
    m = _mig()
    rad = m.execute("SELECT status, innholds_hash, utkastversjon FROM"
                    " policyutkast WHERE tenant=%s AND utkast_id=%s",
                    (TEN, uid)).fetchone()
    m.close()
    return rad


def _rundestatus(uid):
    m = _mig()
    rad = m.execute("SELECT status FROM aktiveringsrunde WHERE tenant=%s"
                    " AND utkast_id=%s AND runde=1", (TEN, uid)).fetchone()
    m.close()
    return rad[0] if rad else None


@pg
def test_validert_utkast_kan_gjenapnes_redigeres_og_valideres_paa_nytt():
    """Hele sløyfa eier ba om: validert → gjenåpne → rediger → valider.
    Den nye valideringen fryser en NY hash — ikke den gamle om igjen.

    Kontroll: fjern `innholds_hash=NULL` fra gjenåpningens UPDATE, så blir
    denne rød i valideringen (033-triggeren nekter å endre en satt hash).
    """
    pid = "p-" + secrets.token_hex(3)
    uid = "u-" + secrets.token_hex(6)
    _utkast(uid, pid, "validert")
    gammel_hash = _rad(uid)[1]
    rt = _rt()
    try:
        res = _gjenapne(rt, uid)
        assert res["status"] == "utkast"
        status, hash_, ver = _rad(uid)
        assert status == "utkast"
        assert hash_ is None, "hashen ble stående etter gjenåpningen"

        nytt = _dokument(pid, "1.2.0")
        r2 = _rediger(rt, uid, ver, nytt)
        v = _valider(rt, uid, r2["utkastversjon"])
        assert v["utfall"] == "validert", v
        assert v["innholds_hash"] != gammel_hash, \
            "den nye valideringen gjenbrukte den gamle frysingen"
        assert _rad(uid)[0] == "validert"
    finally:
        rt.close()


@pg
def test_gjenapning_trekker_aapen_runde_tilbake():
    """En åpen runde kunne uansett aldri aktivere det redigerte innholdet
    (runden er frosset mot hashen som nå nullstilles) — å la den stå ville
    bedt godkjennere signere på noe som ikke kan lande. Kansellert, og
    varselet pensjonert, i samme transaksjon.

    Kontroll: fjern `_kanseller_levende_runde`-kallet i `gjenapne_utkast`,
    så blir denne rød."""
    pid = "p-" + secrets.token_hex(3)
    uid = "u-" + secrets.token_hex(6)
    _utkast(uid, pid, "validert")
    _runde(uid, "1 hour")
    rt = _rt()
    try:
        res = _gjenapne(rt, uid)
        assert res["status"] == "utkast"
        assert _rundestatus(uid) == "kansellert"
    finally:
        rt.close()


@pg
def test_gjenapning_avviser_alt_annet_enn_validert():
    """`utkast` har ingenting å gjenåpne; `godkjent` har fire øyne bak seg og
    avvikles ikke ved å redigeres bort; `forkastet` er terminal."""
    rt = _rt()
    try:
        for status in ("utkast", "godkjent", "forkastet"):
            pid = "p-" + secrets.token_hex(3)
            uid = "u-" + secrets.token_hex(6)
            _utkast(uid, pid, status)
            with pytest.raises(policyadmin.Aktiveringsfeil) as e:
                _gjenapne(rt, uid)
            assert e.value.kode == "utkast_ulovlig_tilstand", (status, e.value)
            assert _rad(uid)[0] == status
    finally:
        rt.close()


@pg
def test_feil_utkastversjon_avvises():
    pid = "p-" + secrets.token_hex(3)
    uid = "u-" + secrets.token_hex(6)
    _utkast(uid, pid, "validert")
    rt = _rt()
    try:
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _gjenapne(rt, uid, ver=99)
        assert "utdatert" in e.value.kode
        assert _rad(uid)[0] == "validert"
    finally:
        rt.close()


@pg
def test_gjenapning_ugyldiggjor_skjemaet_fra_for_frysingen():
    """Gjenåpningen teller opp den optimistiske låsen (Codex P1 på #76).

    Kappløpet: en editor laster utkastet på versjon N mens det står `utkast`.
    Så validerer noen andre — nå avviser `rediger_utkast` status `validert`,
    så det gamle skjemaet er ufarlig. Men gjenåpningen ga skrivetilgangen
    tilbake, og lot versjonen stå på N: da besto det gamle skjemaet både
    statuskravet og versjonskravet, og skrev STILLE over det gjenåpnede
    utkastet. Den som gjenåpnet, gjorde det for å redigere.

    Kontroll: fjern `utkastversjon=%s` fra gjenåpningens UPDATE, så blir
    denne rød — den utdaterte redigeringen slipper gjennom igjen.
    """
    pid = "p-" + secrets.token_hex(3)
    uid = "u-" + secrets.token_hex(6)
    _utkast(uid, pid, "utkast")
    rt = _rt()
    try:
        gammel_ver = _rad(uid)[2]          # versjonen editoren lastet
        v = _valider(rt, uid, gammel_ver)
        assert v["utfall"] == "validert", v

        g = _gjenapne(rt, uid, ver=gammel_ver)
        assert g["utkastversjon"] == gammel_ver + 1, g
        assert _rad(uid)[2] == gammel_ver + 1, "versjonen ble ikke talt opp"

        # Det gamle skjemaet skriver mot versjonen det lastet — og avvises nå.
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _rediger(rt, uid, gammel_ver, _dokument(pid, "9.9.9"))
        assert "utdatert" in e.value.kode, e.value.kode

        # ...mens den som gjenåpnet, skriver videre på svaret sitt.
        r = _rediger(rt, uid, g["utkastversjon"], _dokument(pid, "1.2.0"))
        assert r["utkastversjon"] == gammel_ver + 2, r
    finally:
        rt.close()


@pg
def test_hashen_er_fortsatt_frosset_i_alle_andre_retninger():
    """033-kontrollen: migrasjonen åpnet ÉN overgang, ikke frysingen.

    (a) NULL uten statusovergangen → nektet; (b) en ANNEN verdi, selv med
    overgangen → nektet. Uten (a) kunne hvem som helst med skrivetilgang
    nullstille hashen på et validert utkast og la den gamle valideringen
    se ut som om den gjaldt nytt innhold."""
    import psycopg
    pid = "p-" + secrets.token_hex(3)
    uid = "u-" + secrets.token_hex(6)
    _utkast(uid, pid, "validert")
    m = _mig()
    try:
        with pytest.raises(psycopg.errors.RaiseException, match="frosset"):
            m.execute("UPDATE policyutkast SET innholds_hash=NULL"
                      " WHERE tenant=%s AND utkast_id=%s", (TEN, uid))
        m.rollback()
        from db.pg import sett_kontekst
        sett_kontekst(m, TEN, "sys", "r0")
        with pytest.raises(psycopg.errors.RaiseException, match="frosset"):
            m.execute("UPDATE policyutkast SET innholds_hash='annenhash',"
                      " status='utkast' WHERE tenant=%s AND utkast_id=%s",
                      (TEN, uid))
        m.rollback()
    finally:
        m.close()


# --------------------------------------------------------------------------
# Versjonsvaktene — eiers 17/8-felle: utkastet arvet den aktive policyens
# versjon (0.3.0), validerte i beste velgående, og døde uforklart ved
# rundeåpning med `versjon_i_bruk`.
# --------------------------------------------------------------------------

@pg
def test_valider_sier_fra_om_opptatt_versjon_med_forslag():
    """Valideringen er stedet eier fortsatt kan RETTE. Kontroll: fjern
    `_versjonsavvik`-kallet i `valider_utkast`, så blir denne rød."""
    pid = "p-" + secrets.token_hex(3)
    uid = "u-" + secrets.token_hex(6)
    m = _mig()
    _policyrad(m, pid, "1.1.0")
    m.commit(); m.close()
    _utkast(uid, pid, status="utkast", versjon="1.1.0")   # arver den aktive
    rt = _rt()
    try:
        v = _valider(rt, uid, 1)
        assert v["utfall"] == "ugyldig", v
        tekst = " ".join(v["feil"])
        assert "1.1.0" in tekst and "aktiv" in tekst, tekst
        assert "1.1.1" in tekst, f"forslag mangler: {tekst}"
        assert _rad(uid)[0] == "utkast", "et ugyldig utkast ble frosset"

        # ... og med forslaget fulgt validerer det samme utkastet.
        r = _rediger(rt, uid, 1, _dokument(pid, "1.1.1"))
        v2 = _valider(rt, uid, r["utkastversjon"])
        assert v2["utfall"] == "validert", v2
    finally:
        rt.close()


@pg
def test_opptatt_versjon_caches_ikke_paa_idempotensnokkelen():
    """Codex P2 på #76: et registeravhengig avvik overlevde registeret.

    Bindingen til `utkastversjon` dekker det UTKASTET kan gjøre, og bare det.
    `_versjonsavvik` måler mot `policyer` og den aktive pekeren, som flytter
    seg uten at utkastet røres — og slettingen frigjør versjonsnummer med
    vilje. Eier fikk altså «velg en høyere versjon», slettet den
    feilopprettede policyen som holdt nummeret — nettopp det feilteksten
    inviterer til — og fikk så det samme svaret om igjen om en kollisjon som
    ikke fantes lenger. Flaten gjenbruker valideringsnøkkelen for gjentatte
    klikk på samme tegning, så «prøv igjen» var i praksis ikke et nytt
    spørsmål til registeret i det hele tatt.

    Kontroll: la den registeravhengige grenen gå til `_fullfor` som før, så
    blir denne rød — replayet svarer `ugyldig` om en versjon som er ledig.
    """
    pid = "p-" + secrets.token_hex(3)
    uid = "u-" + secrets.token_hex(6)
    m = _mig()
    _policyrad(m, pid, "1.1.0")
    m.commit(); m.close()
    _utkast(uid, pid, status="utkast", versjon="1.1.0")   # arver den aktive
    idem = secrets.token_hex(8)
    rt = _rt()
    try:
        v = _valider(rt, uid, 1, idem=idem)
        assert v["utfall"] == "ugyldig", v

        # Eier gjør det feilteksten ber om: fjerner policyen som holdt
        # nummeret. Samme spor som `slett_ubrukt_policy` (032) etterlater —
        # pekeren nullstilles, raden forsvinner, versjonen er ledig igjen.
        m = _mig()
        m.execute("UPDATE policy_hode SET aktiv_versjon=NULL, revisjon=revisjon+1"
                  " WHERE tenant=%s AND policy_id=%s", (TEN, pid))
        m.execute("DELETE FROM policyer WHERE tenant=%s AND policy_id=%s",
                  (TEN, pid))
        m.commit(); m.close()

        # SAMME nøkkel og samme input — flatens «prøv igjen» på samme tegning.
        # Nøkkelen ble ikke brent, for det ugyldige utfallet skrev ingenting.
        v2 = _valider(rt, uid, 1, idem=idem)
        assert v2["utfall"] == "validert", (
            "et cachet registeravvik overlevde registeret: %r" % (v2,))
        assert _rad(uid)[0] == "validert"
    finally:
        rt.close()


@pg
def test_opprett_bytter_arvet_opptatt_versjon_med_neste_ledige():
    """Samme normalisering som `meta.status`: et utkast som er dødfødt slik
    det opprettes, skal ikke opprettes slik. En versjon eier selv har satt
    HØYERE er et valg og røres ikke.

    Kontroll: fjern versjonsnormaliseringen i `opprett_utkast`, så blir den
    første halvdelen rød."""
    pid = "p-" + secrets.token_hex(3)
    m = _mig()
    _policyrad(m, pid, "1.1.0")
    m.commit(); m.close()
    rt = _rt()
    try:
        def opprett(versjon):
            idem = secrets.token_hex(8)
            return policyadmin.opprett_utkast(
                rt, tenant=TEN, aktor="forf", request_id="r", policy_id=pid,
                innhold=_dokument(pid, versjon), idempotency_key=idem,
                input_hash="ih-" + idem)

        uid1 = opprett("1.1.0")["utkast_id"]         # arvet == aktiv
        m = _mig()
        v1 = m.execute("SELECT innhold->'meta'->>'versjon' FROM policyutkast"
                       " WHERE tenant=%s AND utkast_id=%s",
                       (TEN, uid1)).fetchone()[0]
        assert v1 == "1.1.1", v1

        uid2 = opprett("2.0.0")["utkast_id"]         # eiers eget, høyere valg
        v2 = m.execute("SELECT innhold->'meta'->>'versjon' FROM policyutkast"
                       " WHERE tenant=%s AND utkast_id=%s",
                       (TEN, uid2)).fetchone()[0]
        m.close()
        assert v2 == "2.0.0", v2
    finally:
        rt.close()
