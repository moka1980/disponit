"""Porten for 183: partsregisteret — én kunde, ett sted.

EIERS ORD 11/9: «Ikke gå gjennom hver modul og fylle, så hva blir vitsen
hvis alt må fylles manuelt», og «nå må fikses den ordentlig slik at det
blir ikke flere runder».

Registeret er svaret på en MÅLT årsak: `kunde_ref` er fritekst i fire
tabeller uten en eneste fremmednøkkel, og tre moduler bærer hver sin
krypterte kontakt med hvert sitt kolonnenavn. Denne porten måler at det
nye registeret ikke arver noen av de tre svakhetene.
"""
import secrets

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, TENANT, migrator, miljo, pg  # noqa: F401
from .test_m37 import _sett_kontekst

NONCE = bytes(range(12))


def _kobling(dsn):
    from db.pg import koble
    return koble(dsn)


def _t():
    return "t-part-" + secrets.token_hex(3)


@pg
def test_parten_er_idempotent_paa_referansen_og_navnet_kan_rettes():
    """IMPORTENS FORUTSETNING. En import kjøres to ganger — av en
    nettverksfeil, av et menneske som lurer på om den gikk. Samme
    referanse må gi samme part, ellers er «legg til kundene dine» en
    knapp som lager duplikater.

    Og navnet må kunne RETTES uten å føde en tvilling: den vanligste
    grunnen til å kjøre importen om igjen er at noe var skrevet feil.
    """
    t = _t()
    c = _kobling(DSN)
    try:
        _sett_kontekst(c, t)
        a = c.execute("SELECT part_registrer(%s,'K-1','Fjordlys AS',"
                      "'912345678','bedrift','kari')", (t,)).fetchone()[0]
        b = c.execute("SELECT part_registrer(%s,'K-1','Fjordlys AS',"
                      "'912345678','bedrift','kari')", (t,)).fetchone()[0]
        assert a == b, "samme referanse ble to parter"
        d = c.execute("SELECT part_registrer(%s,'K-1','Fjordlys Elektro AS',"
                      "NULL,'bedrift','kari')", (t,)).fetchone()[0]
        assert d == a
        rad = c.execute("SELECT navn, orgnummer FROM part_liste(%s,'K-1',10)",
                        (t,)).fetchone()
        assert rad[0] == "Fjordlys Elektro AS", "navnet ble ikke rettet"
        assert rad[1] == "912345678", "orgnummeret forsvant av en NULL"
        # En part uten registrator finnes ikke.
        with pytest.raises(psycopg.Error) as e:
            c.execute("SELECT part_registrer(%s,'K-2','X',NULL,'bedrift','')",
                      (t,))
        assert "registrator" in str(e.value)
        c.rollback()
    finally:
        c.close()


@pg
def test_kontaktpunktet_gjenkjenner_uten_aa_lagre_adressen():
    """GJENKJENNINGEN PÅ TVERS, som er hele poenget med registeret.

    `kampanjemottaker` salter hashen PER RAD i dag og kan derfor ikke
    sammenlignes med noe. Registeret bruker tenantens ene pseudonymnøkkel
    (078), så M-6 kan spørre «hvem er denne avsenderen?» med en adresse
    den alt har, uten at adressen står noe sted i registeret.
    """
    t = _t()
    c = _kobling(DSN)
    try:
        _sett_kontekst(c, t)
        p = c.execute("SELECT part_registrer(%s,'K-1','Fjordlys AS',NULL,"
                      "'bedrift','kari')", (t,)).fetchone()[0]
        psn = c.execute("SELECT tenant_pseudonym(%s,'Post@Fjordlys.no')",
                        (t,)).fetchone()[0]
        # Normaliseringen er 078s: store bokstaver og mellomrom er samme
        # adresse. To skrivemåter som ga to parter ville vært duplikatet
        # tilbake i en ny form.
        psn2 = c.execute("SELECT tenant_pseudonym(%s,'  POST@fjordlys.NO ')",
                         (t,)).fetchone()[0]
        assert psn == psn2
        k1 = c.execute(
            "SELECT part_sett_kontakt(%s,%s,'epost','po**@fjordlys.no',"
            "%s,%s,'k1',%s,true,'faktura','kari')",
            (t, p, b"\x01\x02", NONCE, psn)).fetchone()[0]
        k2 = c.execute(
            "SELECT part_sett_kontakt(%s,%s,'epost','po**@fjordlys.no',"
            "%s,%s,'k1',%s,true,'faktura','kari')",
            (t, p, b"\x01\x02", NONCE, psn)).fetchone()[0]
        assert k1 == k2, "samme adresse ble to kontaktpunkter"
        # Oppslaget den andre veien: fra adresse til part.
        rad = c.execute("SELECT part_ref, navn, kanal FROM"
                        " part_fra_pseudonym(%s,%s)", (t, psn)).fetchone()
        assert rad == ("K-1", "Fjordlys AS", "epost")
        # En ukjent adresse gir INGEN rad — ikke en tilfeldig nabo.
        ukjent = c.execute("SELECT tenant_pseudonym(%s,'ukjent@annet.no')",
                           (t,)).fetchone()[0]
        assert c.execute("SELECT count(*) FROM part_fra_pseudonym(%s,%s)",
                         (t, ukjent)).fetchone()[0] == 0
        c.rollback()
    finally:
        c.close()


@pg
def test_adressen_finnes_aldri_i_klartekst_i_registeret():
    """058-formen, målt: registeret bærer maske, ciphertext og pseudonym
    — aldri adressen. En rå SELECT over hele tabellen skal ikke finne en
    e-postadresse noe sted."""
    t = _t()
    c, m = _kobling(DSN), _kobling(MIGRATOR_DSN)
    try:
        _sett_kontekst(c, t)
        p = c.execute("SELECT part_registrer(%s,'K-1','Fjordlys AS',NULL,"
                      "'bedrift','kari')", (t,)).fetchone()[0]
        psn = c.execute("SELECT tenant_pseudonym(%s,'post@fjordlys.no')",
                        (t,)).fetchone()[0]
        c.execute("SELECT part_sett_kontakt(%s,%s,'epost','po**@fjordlys.no',"
                  "%s,%s,'k1',%s,true,NULL,'kari')",
                  (t, p, b"\x01\x02", NONCE, psn))
        c.commit()
        _sett_kontekst(m, t)
        treff = m.execute(
            "SELECT count(*) FROM partkontakt"
            " WHERE partkontakt::text ~ '@[a-z0-9.-]+[.][a-z]{2,}'"
            "   AND tenant=%s", (t,)).fetchone()[0]
        # Masken har med vilje en `@` — den er poenget med en maske. Det
        # som IKKE skal finnes, er den hele adressen.
        raa = m.execute("SELECT count(*) FROM partkontakt WHERE tenant=%s"
                        " AND partkontakt::text LIKE %s",
                        (t, "%post@fjordlys.no%")).fetchone()[0]
        assert raa == 0, "adressen i klartekst i registeret"
        assert treff >= 1, "masken forsvant — porten måler ingenting"
        m.rollback()
    finally:
        c.rollback(); c.close(); m.close()


@pg
def test_doerene_binder_tenanten_til_konteksten_og_nekter_hoeyt():
    """102-FORMEN, OG DEN BLE FUNNET VED Å KJØRE.

    Lesedørene var først `LANGUAGE sql` uten `krev_tenantkontekst`. RLS
    reddet dem — men STILLE: et oppslag på feil tenant ga null rader, og
    «ingen kunder» og «feil firma» så helt like ut på skjermen. Nå
    nekter de høyt. Porten måler begge halvdeler: at riktig tenant får
    svar, og at feil tenant får en FEIL og ikke en tom liste.
    """
    t1, t2 = _t(), _t()
    c = _kobling(DSN)
    try:
        _sett_kontekst(c, t1)
        c.execute("SELECT part_registrer(%s,'K-1','Fjordlys AS',NULL,"
                  "'bedrift','kari')", (t1,))
        assert c.execute("SELECT count(*) FROM part_liste(%s,NULL,10)",
                         (t1,)).fetchone()[0] == 1
        c.commit()
        _sett_kontekst(c, t2)
        for sql, arg in (("SELECT * FROM part_liste(%s,NULL,10)", (t1,)),
                         ("SELECT tenant_pseudonym(%s,'x@y.no')", (t1,))):
            with pytest.raises(psycopg.Error) as e:
                c.execute(sql, arg)
            assert "tenantkontekst" in str(e.value), sql
            c.rollback()
        # Og t2 ser ingenting av t1 gjennom sin EGEN kontekst.
        _sett_kontekst(c, t2)
        assert c.execute("SELECT count(*) FROM part_liste(%s,NULL,10)",
                         (t2,)).fetchone()[0] == 0
        c.rollback()
    finally:
        c.close()


@pg
def test_samme_adresse_i_to_tenanter_er_ikke_gjenkjennelig():
    """078s tenant-skopede nøkkel, målt på registeret: to firmaer som
    begge har kunden `post@fjordlys.no` skal ikke kunne se det på
    hverandre. Pseudonymet er nøkkelen — og nøkkelen er tenantens."""
    t1, t2 = _t(), _t()
    c = _kobling(DSN)
    try:
        _sett_kontekst(c, t1)
        a = c.execute("SELECT tenant_pseudonym(%s,'post@fjordlys.no')",
                      (t1,)).fetchone()[0]
        c.commit()
        _sett_kontekst(c, t2)
        b = c.execute("SELECT tenant_pseudonym(%s,'post@fjordlys.no')",
                      (t2,)).fetchone()[0]
        assert a != b, "samme adresse ga samme pseudonym i to tenanter"
        p = c.execute("SELECT part_registrer(%s,'K-9','Annen AS',NULL,"
                      "'bedrift','per')", (t2,)).fetchone()[0]
        c.execute("SELECT part_sett_kontakt(%s,%s,'epost','po**@fjordlys.no',"
                  "%s,%s,'k1',%s,true,NULL,'per')",
                  (t2, p, b"\x01", NONCE, b))
        # t2s oppslag på t1s pseudonym finner ingenting.
        assert c.execute("SELECT count(*) FROM part_fra_pseudonym(%s,%s)",
                         (t2, a)).fetchone()[0] == 0
        c.rollback()
    finally:
        c.close()


@pg
def test_en_primaer_per_kanal_og_deaktivering_er_enveis():
    """ÉN PRIMÆR, ellers er valget tilbake i modulen: skal purringen gå
    til fakturamottakeren eller til daglig leder? Registeret svarer, ikke
    hver modul for seg.

    Og deaktivering er enveis med spor — en kunde man har sluttet med er
    ikke en kunde man aldri hadde.
    """
    t = _t()
    c = _kobling(DSN)
    try:
        _sett_kontekst(c, t)
        p = c.execute("SELECT part_registrer(%s,'K-1','Fjordlys AS',NULL,"
                      "'bedrift','kari')", (t,)).fetchone()[0]
        for adr, primar in (("faktura@fjordlys.no", True),
                            ("daglig@fjordlys.no", True)):
            psn = c.execute("SELECT tenant_pseudonym(%s,%s)",
                            (t, adr)).fetchone()[0]
            c.execute("SELECT part_sett_kontakt(%s,%s,'epost',%s,%s,%s,'k1',"
                      "%s,%s,NULL,'kari')",
                      (t, p, adr[:2] + "**@fjordlys.no", b"\x01", NONCE,
                       psn, primar))
        rad = c.execute("SELECT epost_maske, antall_kontakter FROM"
                        " part_liste(%s,NULL,10)", (t,)).fetchone()
        assert rad[1] == 2, "den andre adressen erstattet den første"
        assert rad[0] == "da**@fjordlys.no", "den siste primæren vant ikke"
        # Deaktivering: første gang true, andre gang stille false.
        assert c.execute("SELECT part_deaktiver(%s,%s,'kari')",
                         (t, p)).fetchone()[0] is True
        assert c.execute("SELECT part_deaktiver(%s,%s,'kari')",
                         (t, p)).fetchone()[0] is False
        rad = c.execute("SELECT aktiv FROM part_liste(%s,NULL,10)",
                        (t,)).fetchone()
        assert rad[0] is False
        # En deaktivert part tar ikke imot nye kontaktpunkter.
        psn = c.execute("SELECT tenant_pseudonym(%s,'ny@fjordlys.no')",
                        (t,)).fetchone()[0]
        with pytest.raises(psycopg.Error) as e:
            c.execute("SELECT part_sett_kontakt(%s,%s,'epost','ny**@f.no',"
                      "%s,%s,'k1',%s,false,NULL,'kari')",
                      (t, p, b"\x01", NONCE, psn))
        # 186 delte vakten i to: «finnes ikke» og «er avviklet» er ikke
        # det samme, og API-laget kan ikke skille dem når basen ikke gjør
        # det. Ordet er «avviklet», og koden er husets 23000.
        assert "avviklet" in str(e.value), str(e.value)
        c.rollback()
    finally:
        c.close()


@pg
def test_web_api_rollen_naar_ikke_tabellene_utenom_doerene():
    """REGISTERMØNSTERET: tabellen er migrators, dørene er claimerens, og
    runtime har INGEN av delene direkte. Uten dette ville registeret vært
    en ny skrivevei forbi vaktene — og da er de 183 migrasjonene med
    invarianter verdt mindre."""
    t = _t()
    c = _kobling(DSN)
    try:
        _sett_kontekst(c, t)
        for sql in ("SELECT count(*) FROM part",
                    "SELECT count(*) FROM partkontakt",
                    "INSERT INTO part (tenant,part_id,part_ref,navn,"
                    "opprettet_av) VALUES (%s,gen_random_uuid(),'X','Y','z')"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql, (t,) if "INSERT" in sql else None)
            c.rollback()
        # ...men døra virker, så porten ikke er grønn av en død kobling.
        _sett_kontekst(c, t)
        assert c.execute("SELECT count(*) FROM part_liste(%s,NULL,10)",
                         (t,)).fetchone()[0] == 0
        c.rollback()
    finally:
        c.close()


@pg
def test_samtidige_importkall_gir_en_part_ikke_syv_feil():
    """SAMTIDIGHET ER IMPORTENS NORMALTILSTAND (CodeRabbit).

    Dørene var først `SELECT`-så-`INSERT`. Åtte samtidige kall med samme
    kundereferanse ga da SJU `UniqueViolation` og én vellykket — altså
    nøyaktig i en import, som er det eneste her som kjører tusen kall
    etter hverandre og gjerne på nytt etter en avbrutt runde. Brukeren
    ville sett «importen feilet» på noe hun trodde var idempotent.

    Målt ved å KJØRE: den gamle formen falt 7/8, den nye går 8/8. Formen
    er nå én setning med `ON CONFLICT`, og raden låses av setningen selv.
    """
    import threading

    t = _t()
    res, feil = [], []
    klar = threading.Barrier(8)

    def kjor():
        c = _kobling(DSN)
        try:
            _sett_kontekst(c, t)
            klar.wait(timeout=10)
            r = c.execute("SELECT part_registrer(%s,'K-RACE','Fjordlys AS',"
                          "NULL,'bedrift','imp')", (t,)).fetchone()[0]
            c.commit()
            res.append(r)
        except Exception as e:            # noqa: BLE001 — porten SKAL se alt
            feil.append(f"{type(e).__name__}: {e}")
        finally:
            c.close()

    traader = [threading.Thread(target=kjor) for _ in range(8)]
    for tr in traader:
        tr.start()
    for tr in traader:
        tr.join(timeout=30)
    assert not feil, feil
    assert len(res) == 8, f"bare {len(res)} av 8 kom gjennom"
    assert len(set(res)) == 1, "samme referanse ble flere parter"

    # Samme prøve på kontaktpunktet: åtte samtidige med samme adresse.
    c = _kobling(DSN)
    try:
        _sett_kontekst(c, t)
        part = res[0]
        psn = c.execute("SELECT tenant_pseudonym(%s,'post@fjordlys.no')",
                        (t,)).fetchone()[0]
        c.commit()
    finally:
        c.close()
    kres, kfeil = [], []
    klar2 = threading.Barrier(8)

    def kontakt():
        c = _kobling(DSN)
        try:
            _sett_kontekst(c, t)
            klar2.wait(timeout=10)
            r = c.execute(
                "SELECT part_sett_kontakt(%s,%s,'epost','po**@fjordlys.no',"
                "%s,%s,'k1',%s,false,NULL,'imp')",
                (t, part, b"\x01", NONCE, psn)).fetchone()[0]
            c.commit()
            kres.append(r)
        except Exception as e:            # noqa: BLE001
            kfeil.append(f"{type(e).__name__}: {e}")
        finally:
            c.close()

    traader = [threading.Thread(target=kontakt) for _ in range(8)]
    for tr in traader:
        tr.start()
    for tr in traader:
        tr.join(timeout=30)
    assert not kfeil, kfeil
    # TELL FØRST, SÅ SAMMENLIGN (CodeRabbit). `len(set(kres)) == 1` er
    # sant også når bare ÉN tråd kom fram — og da måler porten at én
    # tråd er enig med seg selv, ikke at åtte ble til ett kontaktpunkt.
    assert len(kres) == 8, f"bare {len(kres)} av 8 kom gjennom"
    assert len(set(kres)) == 1, "samme adresse ble flere kontaktpunkter"

    # Rydd opp etter oss: denne testen COMMITTER, ulikt de andre.
    m = _kobling(MIGRATOR_DSN)
    try:
        _sett_kontekst(m, t)
        m.execute("DELETE FROM partkontakt WHERE tenant=%s", (t,))
        m.execute("DELETE FROM part WHERE tenant=%s", (t,))
        m.commit()
    finally:
        m.close()


@pg
def test_en_slettet_rad_kan_ikke_baere_adressen(migrator):
    """VAKTEN SOM BINDER PAYLOADEN TIL `slettet_ts` (CodeRabbit).

    Vilkåret var først `(payload finnes) OR (slettet OG payload borte)`.
    Første gren nevnte ikke `slettet_ts`, så en SLETTET rad med adressen
    i behold var fullt lovlig — altså nøyaktig den tilstanden retensjonen
    finnes for å hindre, og den ville sett ryddet ut i enhver liste som
    filtrerer på `slettet_ts`.

    Porten måler begge ulovlige former, og en lovlig til slutt så den
    ikke er grønn av at ALT avvises.
    """
    t = _t()
    felt = ("tenant,kontakt_id,part_id,kanal,verdi_maske,verdi_kryptert,"
            "verdi_nonce,verdi_key_id,verdi_pseudonym,opprettet_av")
    psn = "psn-" + "a" * 64
    # Parten COMMITTES: hvert forsøk under ruller tilbake, og uten en
    # varig forelder ville forsøk nr. 2 falt på fremmednøkkelen i stedet
    # for på vakten porten faktisk måler.
    _sett_kontekst(migrator, t)
    p = migrator.execute(
        "INSERT INTO part (tenant,part_id,part_ref,navn,opprettet_av)"
        " VALUES (%s,gen_random_uuid(),'K-1','Fjordlys AS','kari')"
        " RETURNING part_id", (t,)).fetchone()[0]
    migrator.commit()
    try:
        for merkelapp, sql, arg in (
            # 1. Slettet, men adressen i behold — den ulovlige.
            ("slettet med adresse",
             f"INSERT INTO partkontakt ({felt},slettet_ts,slettet_av)"
             " VALUES (%s,gen_random_uuid(),%s,'epost','m**@x.no',%s,%s,"
             "'k1',%s,'kari',now(),'kari')", (t, p, b"\x01", NONCE, psn)),
            # 2. Levende, men uten ciphertext — like ulovlig.
            ("levende uten payload",
             f"INSERT INTO partkontakt ({felt})"
             " VALUES (%s,gen_random_uuid(),%s,'epost',NULL,NULL,NULL,"
             "NULL,%s,'kari')", (t, p, psn)),
        ):
            _sett_kontekst(migrator, t)
            with pytest.raises(psycopg.errors.CheckViolation):
                migrator.execute(sql, arg)
            migrator.rollback()
        # 3. POSITIV KONTROLL: en levende rad med hel payload går inn.
        # Uten den ville porten vært grønn av at ALT ble avvist.
        _sett_kontekst(migrator, t)
        migrator.execute(
            f"INSERT INTO partkontakt ({felt})"
            " VALUES (%s,gen_random_uuid(),%s,'epost','m**@x.no',%s,%s,"
            "'k1',%s,'kari')", (t, p, b"\x01", NONCE, psn))
        migrator.rollback()
    finally:
        _sett_kontekst(migrator, t)
        migrator.execute("DELETE FROM partkontakt WHERE tenant=%s", (t,))
        migrator.execute("DELETE FROM part WHERE tenant=%s", (t,))
        migrator.commit()
