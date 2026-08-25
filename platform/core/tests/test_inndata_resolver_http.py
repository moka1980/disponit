"""#162 PR-2: resolveren — modulens lesevei, autorisert av CLAIMET (B-form).

Hele kjeden over HTTP: kunde reserverer+laster (PR-1-veien, det EKTE
(m57_ats, soknadsbunt)-paret — 058-CHECKen låser eiermodulen, så ingen
syntetisk modul finnes), bunten BINDES til oppdraget i oppdragets
FØDSELSTRANSAKSJON (X1, 059), og modulen henter via sitt EGET oppdrag
(#200 valg B): bindingsraden er den eneste sannheten om hvilken bunt
oppdraget eier — ingen payload-referanse finnes.

Rettens tilstandsside (o.status = 'plukket') settes deterministisk av
migrator i stedet for HTTP-claim: claim-endepunktet plukker «neste» på
tvers av alt basen har liggende, og et kappløp med andre suiters
etterlatte oppdrag ville målt kjørerekkefølgen, ikke resolveren. At
claim ER veien til 'plukket' bevises av claim-suitene; resolverens
predikat er modul-match + plukket med LEVENDE LEASE + SAMME DEPLOYMENT +
bundet, og det er DET som måles her. Negativene: opprettet 404, feil
modul 404, ubundet 404, utløpt/manglende lease 404, terminalt oppdrag med
intakt claim 404, fremmed deployment av samme modul 404, browser 401 —
samme svar uansett årsak.
"""
import hashlib
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, app, klient, migrator,  # noqa: F401
                       miljo)
from .test_inndata_http import (_opplast, _reserver, _rigg, _zipbytes,
                                inndata_rot)  # noqa: F401
from .test_modul_onboarding_http import _onboard_token

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _m57_deployment(conn, miljo_="staging", tvang_ny=False):
    """En onboardbar deployment for den EKTE modulen `m57_ats`.

    Hodet og kontrakten er idempotente på tvers av kjøringer
    (`ON CONFLICT DO NOTHING` — ingen UPDATE, så append-only-vaktene er
    urørt); releasen/deploymenten er fersk per kjøring, og
    kontrakt-hashen leses fra basen slik den faktisk står.

    `miljo_` er parametrisert fordi en modul normalt har FLERE levende
    deployments (035): to av dem er hele poenget i
    `test_fremmed_deployment_...` under."""
    conn.execute("INSERT INTO modulhode (modul_id,status)"
                 " VALUES ('m57_ats','aktiv') ON CONFLICT DO NOTHING")
    # `tvang_ny` gir en ANDRE claiming-release i SAMME miljø: 035
    # tillater det bare under en annen kontraktversjon
    # (en_claiming_per_kontrakt) — versjon 2, egen hash.
    versjon = 2 if tvang_ny else 1
    conn.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES ('m57_ats',%s,%s,'p','k','krever_outbox',"
        "'kompenserende') ON CONFLICT DO NOTHING",
        (versjon, "k-" + secrets.token_hex(8)))
    khash = conn.execute(
        "SELECT kontrakt_hash FROM modulkontrakt"
        " WHERE modul_id='m57_ats' AND kontraktversjon=%s",
        (versjon,)).fetchone()[0]
    # Onboardingen krever en registrert oppdragstype under releasens
    # kontrakt; registeret er append-only, så navnet er ferskt per kall.
    conn.execute(
        "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
        "kontraktversjon,kontrakt_hash) VALUES (%s,'m57_ats',%s,%s)",
        (f"rekr.res.{secrets.token_hex(4)}", versjon, khash))
    # `en_claiming_per_kontrakt` tillater NØYAKTIG én claiming-deployment
    # per (modul, miljø, versjon, hash) — finnes den, gjenbrukes den.
    rad = conn.execute(
        "SELECT release_id FROM moduldeployment"
        " WHERE modul_id='m57_ats' AND miljo=%s"
        " AND kontraktversjon=%s AND kontrakt_hash=%s"
        " AND livslop='claiming'", (miljo_, versjon, khash)).fetchone()
    if rad:
        conn.commit()
        return rad[0]
    rel = f"r57-{secrets.token_hex(6)}"
    conn.execute(
        "INSERT INTO modulrelease (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,manifest_hash,artifact_digest)"
        " VALUES ('m57_ats',%s,%s,%s,'mh','ad')", (rel, versjon, khash))
    conn.execute(
        "INSERT INTO moduldeployment (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,miljo,livslop)"
        " VALUES ('m57_ats',%s,%s,%s,%s,'claiming')",
        (rel, versjon, khash, miljo_))
    conn.commit()
    return rel


def _bundet_bunt(klient, migrator, *, bind=True):
    """Reserver+last en bunt over HTTP (ekte par), og fød oppdraget med
    bindingen i SAMME transaksjon (X1, 059) via dørens eier — nøyaktig
    formen bestillingsveien (PR-3) skal ha. -> (kropp, tenant, oid)."""
    tenant, _bid, cookie, csrf = _rigg(klient)
    r = _reserver(klient, cookie, csrf)
    assert r.status_code == 201, r.text
    jti = r.json()["reservasjon_jti"]
    inndata_id = r.json()["inndata_ref"].split(":", 1)[1]
    kropp = _zipbytes()
    r2 = _opplast(klient, cookie, csrf, jti, kropp)
    assert r2.status_code == 201, r2.text

    from db import kryptering
    from db.pg import sett_kontekst
    sett_kontekst(migrator, tenant, "test", "r-res")
    logg = migrator.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'test','api_token','ih','p@1.0.0/x.y','TILLAT','[]',"
        " %s) RETURNING id", (tenant, secrets.token_hex(8))).fetchone()[0]
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, tenant)
    ct, nonce = kryptering.krypter(dek, {"x": 1}, tenant, key_id)
    # Konsument-typen: bind_inndata krever formålets konsumerende
    # oppdragstype (rekruttering.evaluering for soknadsbunt).
    oid = migrator.execute(
        "INSERT INTO oppdrag (opprinnelse, tenant, beslutning_loggpost_id,"
        " oppdragstype, handling, eiermodul, payload_kryptert, key_id,"
        " nonce, utforelsesfrist, evidensfrist, koblingsstatus)"
        " VALUES ('beslutning',%s,%s,'rekruttering.evaluering',"
        "'rekruttering.evaluering','m57_ats',%s,%s,%s,"
        " now()+interval '4 hour', now()+interval '1 day','KOBLET')"
        " RETURNING id", (tenant, logg, ct, key_id, nonce)).fetchone()[0]
    if bind:
        migrator.execute("SET LOCAL ROLE disponit_domene_eier")
        migrator.execute("SELECT bind_inndata(%s,%s,%s,%s)",
                         (tenant, inndata_id, oid, "m57_ats"))
        migrator.execute("RESET ROLE")
    migrator.commit()
    return kropp, tenant, oid


def _pluk(migrator, tenant, oid, rel, miljo_="staging"):
    """Flipper til plukket og stempler claim-KAPABILITETEN (owner_claim_id
    — kolonnen er ikke claim-vaktens; formatkravet er claim-dørens
    ^[0-9a-f]{32,}$). Returnerer kapabiliteten kalleren må presentere.

    LEASEN OG DEPLOYMENTEN SETTES OGSÅ (Codex P1, #202). Claim-porten
    skriver alltid `owner_lease_utloper` (015:277, 037:192, 049:288) og
    stempler `claim_release_id`/`claim_miljo` med den deploymenten den
    verifiserte (049:294); resolveren krever nå begge deler. En positiv
    sti som lot dem stå NULL ville målt en tilstand claim-døren aldri
    produserer — og skjult nøyaktig de leddene den skal bevise.

    Stempelkolonnene er claim-vaktens (`oppdrag_claim_release_vakt`,
    049:98): de kan KUN settes av `disponit_m37_claimer`, så den delen av
    plukket kjøres under den rollen — akkurat som porten selv gjør."""
    from db.pg import sett_kontekst
    claim = secrets.token_hex(16)
    sett_kontekst(migrator, tenant, "test", "r-pluk")
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    migrator.execute("UPDATE oppdrag SET status='plukket',"
                     " owner_claim_id=%s,"
                     " owner_lease_utloper=now()+interval '1 hour',"
                     " claim_release_id=%s, claim_miljo=%s"
                     " WHERE tenant=%s AND id=%s",
                     (claim, rel, miljo_, tenant, oid))
    migrator.execute("RESET ROLE")
    migrator.commit()
    return claim


@pg
def test_resolveren_krever_plukket_oppdrag(klient, migrator, miljo,
                                           inndata_rot):
    rel = _m57_deployment(migrator)
    kropp, tenant, oid = _bundet_bunt(klient, migrator)
    mtk, _ = _onboard_token(klient, migrator, "m57_ats", rel)

    # FØR plukk: 404 — bindingen finnes, retten gjør det ikke (en
    # gjettet kapabilitet hjelper ikke: raden har ingen).
    r = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                    json={"owner_claim_id": secrets.token_hex(16)},
                    headers={"authorization": f"Bearer {mtk}"})
    assert r.status_code == 404, r.text

    claim = _pluk(migrator, tenant, oid, rel)
    # Uten kapabiliteten i kroppen: 400 — kravet er del av kontrakten.
    r400 = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}", json={},
                       headers={"authorization": f"Bearer {mtk}"})
    assert r400.status_code == 400, r400.text
    # …og en kropp som er gyldig JSON, men ikke et OBJEKT (Codex P2):
    # samme 400, ikke en ufanget 500 fra `.get` på en liste/streng/tall.
    for ikke_objekt in ([{"owner_claim_id": claim}], "x", 3, True):
        rj = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                         json=ikke_objekt,
                         headers={"authorization": f"Bearer {mtk}"})
        assert rj.status_code == 400, (ikke_objekt, rj.status_code, rj.text)
    # FEIL kapabilitet (riktig modul, riktig oppdrag): samme 404.
    rfeil = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                        json={"owner_claim_id": secrets.token_hex(16)},
                        headers={"authorization": f"Bearer {mtk}"})
    assert rfeil.status_code == 404, rfeil.text
    r2 = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                     json={"owner_claim_id": claim},
                     headers={"authorization": f"Bearer {mtk}"})
    assert r2.status_code == 200, r2.text
    assert r2.content == kropp
    assert r2.headers["x-innhold-sha256"] == \
        hashlib.sha256(kropp).hexdigest()

    # Browserøkten (ikke modultoken) er ikke en vei inn.
    from api import sesjon as sesjonmodul
    from .test_rekruttering_http import _browsersesjon, _bruker
    cookie, _csrf = _browsersesjon(_bruker("snoker", ["admin"]))
    r3 = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                     json={"owner_claim_id": claim},
                     cookies={sesjonmodul.C_SESJON: cookie})
    assert r3.status_code == 401


@pg
def test_terminalt_oppdrag_med_intakt_claim_gir_ingenting(klient, migrator,
                                                          miljo, inndata_rot):
    """Cursor-P2 (#202): `o.status = 'plukket'` er lastbærende, men ingen
    negativ drepte mutasjonen. `test_resolveren_krever_plukket_oppdrag`
    måler bare FØR plukk, og der står `owner_claim_id`/leasen NULL — 404-en
    der kommer fra claim- og lease-leddene, ikke fra statusleddet. Stryker
    man `AND o.status = 'plukket'` ut av 060, forblir suiten grønn.

    Hullet leddet faktisk lukker: terminaliseringen rører ikke claim-
    stemplet. Kolonnelåsen (`056:529-535`) tillater `plukket` →
    `utfort`/`feilet` og lar `owner_*` stå — så et ferdig oppdrag beholder
    både kapabiliteten og en levende lease, og holderen (eller en lekket
    kapabilitet i samme deployment) kunne hentet PII resten av leasens
    løpetid. Begge terminalene måles: en mutasjon til `o.status <>
    'utfort'` ville overlevd en test som bare kjente den ene."""
    rel = _m57_deployment(migrator)
    mtk, _ = _onboard_token(klient, migrator, "m57_ats", rel)

    from db.pg import sett_kontekst
    for terminal in ("utfort", "feilet"):
        kropp, tenant, oid = _bundet_bunt(klient, migrator)
        claim = _pluk(migrator, tenant, oid, rel)

        # Kontroll: plukket, levende lease, samme deployment → 200.
        r_ok = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                           json={"owner_claim_id": claim},
                           headers={"authorization": f"Bearer {mtk}"})
        assert r_ok.status_code == 200, (terminal, r_ok.text)
        assert r_ok.content == kropp

        # Oppdraget termineres, og INGENTING annet endres — kapabiliteten,
        # leasen og deployment-stemplet står som i 200-svaret over.
        sett_kontekst(migrator, tenant, "test", "r-terminal")
        migrator.execute("UPDATE oppdrag SET status=%s"
                         " WHERE tenant=%s AND id=%s",
                         (terminal, tenant, oid))
        migrator.commit()
        # …og det er ikke en antakelse: raden sier det selv.
        rad = migrator.execute(
            "SELECT status, owner_claim_id,"
            " owner_lease_utloper > now() AS lever"
            " FROM oppdrag WHERE tenant=%s AND id=%s",
            (tenant, oid)).fetchone()
        assert tuple(rad) == (terminal, claim, True), (terminal, rad)

        r = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                        json={"owner_claim_id": claim},
                        headers={"authorization": f"Bearer {mtk}"})
        assert r.status_code == 404, (terminal, r.text)


@pg
def test_utlopt_lease_er_ikke_lenger_en_rett(klient, migrator, miljo,
                                             inndata_rot):
    """Codex P1: etter `owner_lease_utloper` er raden reclaimbar, men
    `plukket`/`owner_claim_id` står urørt til noen tar den. I det
    vinduet skal den gamle holderens kapabilitet ikke lenger hente
    noe — samme 404 som «ikke claimet», ikke en egen feilklasse."""
    rel = _m57_deployment(migrator)
    kropp, tenant, oid = _bundet_bunt(klient, migrator)
    claim = _pluk(migrator, tenant, oid, rel)
    mtk, _ = _onboard_token(klient, migrator, "m57_ats", rel)

    # Kontroll: med levende lease er dette 200 og byte-likt.
    r_ok = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                       json={"owner_claim_id": claim},
                       headers={"authorization": f"Bearer {mtk}"})
    assert r_ok.status_code == 200, r_ok.text
    assert r_ok.content == kropp

    # Leasen løper ut. Ingenting annet endres — status og kapabilitet
    # er nøyaktig de samme som i 200-svaret over.
    from db.pg import sett_kontekst
    sett_kontekst(migrator, tenant, "test", "r-utlop")
    migrator.execute("UPDATE oppdrag SET"
                     " owner_lease_utloper=now()-interval '1 second'"
                     " WHERE tenant=%s AND id=%s", (tenant, oid))
    migrator.commit()
    r = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                    json={"owner_claim_id": claim},
                    headers={"authorization": f"Bearer {mtk}"})
    assert r.status_code == 404, r.text

    # Og en rad uten lease i det hele tatt: fail-closed, ikke «ingen
    # frist = ingen utløp».
    sett_kontekst(migrator, tenant, "test", "r-utlop")
    migrator.execute("UPDATE oppdrag SET owner_lease_utloper=NULL"
                     " WHERE tenant=%s AND id=%s", (tenant, oid))
    migrator.commit()
    r2 = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                     json={"owner_claim_id": claim},
                     headers={"authorization": f"Bearer {mtk}"})
    assert r2.status_code == 404, r2.text


@pg
def test_leasen_males_mot_veggklokken_ikke_transaksjonsstart(
        klient, migrator, miljo, inndata_rot):
    """Codex P2 (#202): `now()` er `transaction_timestamp()` — frosset
    ved transaksjonens FØRSTE setning, ikke ved predikatets.

    Begge kallstedene (`inndata.py`, basefasen og leveranse-re-målingen)
    kjører `modultoken_fortsatt_autorisert` FØR resolveren i samme
    transaksjon, og den tar DELT advisory-lås på `modul:<id>`
    (035:789). Holder en nødstopp/tilbakekalling den eksklusive låsen,
    venter revalideringen vilkårlig lenge — og en lease som døde under
    ventingen ville fortsatt bestått porten over, altså nøyaktig det
    hullet den ble lagt inn for å lukke.

    Målt uten å bygge lås-kappløpet (K1): predikatet kalles TO ganger i
    SAMME transaksjon, med veggklokken flyttet imellom. At de to svarene
    er forskjellige er i seg selv beviset — under `now()` er de like,
    for da er tiden den samme begge ganger. Samme form som
    `test_frigivelsesoppdrag_maaler_fristen_mot_veggklokken`
    (test_m57_utsending.py) alt bruker for denne klassen.

    MUTASJONEN SOM DREPER DENNE: sett `clock_timestamp()` tilbake til
    `now()` i 060s lease-ledd."""
    rel = _m57_deployment(migrator)
    _kropp, tenant, oid = _bundet_bunt(klient, migrator)
    claim = _pluk(migrator, tenant, oid, rel)

    # Leasen dør om to sekunder — altså ETTER at transaksjonen under er
    # åpnet, og FØR den er ferdig.
    from db.pg import sett_kontekst
    sett_kontekst(migrator, tenant, "test", "r-klokke")
    migrator.execute("UPDATE oppdrag SET owner_lease_utloper="
                     "clock_timestamp()+interval '2 seconds'"
                     " WHERE tenant=%s AND id=%s", (tenant, oid))
    migrator.commit()

    kall = ("SELECT 1 FROM hent_inndata_for_oppdrag(%s::bigint,'m57_ats',"
            "%s,%s,'staging')")
    arg = (oid, claim, rel)
    # Funksjonen er SECURITY DEFINER og eies av domene_eier; rollen her
    # gir bare EXECUTE (grantet til `disponit` i drift) — ingen egen
    # tenantkontekst trengs, avgjørelsen er kryss-tenant.
    migrator.execute("SET LOCAL ROLE disponit_domene_eier")  # now() fryses
    for_ = migrator.execute(kall, arg).fetchone()
    migrator.execute("SELECT pg_sleep(3)")   # veggklokken går, now() står
    etter = migrator.execute(kall, arg).fetchone()
    migrator.rollback()

    assert for_ is not None, "retten skulle levd ved transaksjonsstart"
    assert etter is None, ("leasen døde under transaksjonen, men"
                           " predikatet svarte fortsatt ja")


@pg
def test_fremmed_deployment_av_samme_modul_far_ingenting(klient, migrator,
                                                         miljo, inndata_rot):
    """Codex P1: kapabiliteten binder DEPLOYMENTEN, ikke bare modulen.

    To levende deployments av `m57_ats` (staging og produksjon — 035s
    normaltilstand). Staging claimer og henter. Produksjonsdeploymenten,
    med sitt EGET gyldige modultoken og med staging-claimets
    `owner_claim_id` i kroppen (lekket, misrutet — 060 skal ikke anta at
    strengen er hemmelig for søsknene), får samme ingenting som en
    fremmed modul."""
    rel_a = _m57_deployment(migrator, "staging")
    rel_b = _m57_deployment(migrator, "produksjon")
    assert rel_a != rel_b, "to miljøer må gi to ulike releaser"
    kropp, tenant, oid = _bundet_bunt(klient, migrator)
    claim = _pluk(migrator, tenant, oid, rel_a, "staging")

    # Deploymenten som faktisk holder claimet: 200.
    mtk_a, _ = _onboard_token(klient, migrator, "m57_ats", rel_a)
    r_a = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                      json={"owner_claim_id": claim},
                      headers={"authorization": f"Bearer {mtk_a}"})
    assert r_a.status_code == 200, r_a.text
    assert r_a.content == kropp

    # Søsteren i et annet miljø, samme modul, samme claim-streng: 404.
    mtk_b, _ = _onboard_token(klient, migrator, "m57_ats", rel_b,
                              miljo_="produksjon")
    r_b = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                      json={"owner_claim_id": claim},
                      headers={"authorization": f"Bearer {mtk_b}"})
    assert r_b.status_code == 404, r_b.text

    # Og et ustemplet claim (pre-049 / legacy-grenen, NULL i sporet) er
    # fail-closed: en rad som ikke vet hvem som tok den, svarer ingen.
    from db.pg import sett_kontekst
    sett_kontekst(migrator, tenant, "test", "r-ustemplet")
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    migrator.execute("UPDATE oppdrag SET claim_release_id=NULL,"
                     " claim_miljo=NULL WHERE tenant=%s AND id=%s",
                     (tenant, oid))
    migrator.execute("RESET ROLE")
    migrator.commit()
    r_null = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                         json={"owner_claim_id": claim},
                         headers={"authorization": f"Bearer {mtk_a}"})
    assert r_null.status_code == 404, r_null.text


def _bunten_pa_disk(migrator, tenant, oid, inndata_rot):
    """Stien til den bundne buntens `.bin` under testens FS-rot."""
    from db.pg import sett_kontekst
    sett_kontekst(migrator, tenant, "test", "r-sti")
    sti = migrator.execute(
        "SELECT lager_sti FROM inndata_artefakt"
        " WHERE tenant=%s AND oppdrag_id=%s", (tenant, oid)).fetchone()[0]
    migrator.rollback()
    return inndata_rot / sti


@pg
def test_lagerdrift_gir_intern_feil_ikke_ufanget_500(klient, migrator,
                                                     miljo, inndata_rot):
    """Codex P2 x2: en `.bin` som har driftet fra sin egen rad skal gi
    endepunktets sanerte `intern_feil` — aldri en ufanget 500, og aldri
    en ubegrenset lesning.

    Tre former for drift, samme svar utad: for stor fil (lesningen er
    begrenset av `faktiske_bytes` + GCM-taggen, så den store filen blir
    aldri lastet inn), for kort fil, og riktig lengde men ødelagte bytes
    (AES-GCM `InvalidTag` — den fanges, og `inndata_sha_avvik` under
    ville uansett aldri blitt nådd)."""
    rel = _m57_deployment(migrator)
    kropp, tenant, oid = _bundet_bunt(klient, migrator)
    claim = _pluk(migrator, tenant, oid, rel)
    mtk, _ = _onboard_token(klient, migrator, "m57_ats", rel)
    hoder = {"authorization": f"Bearer {mtk}"}
    sti = _bunten_pa_disk(migrator, tenant, oid, inndata_rot)
    ekte = sti.read_bytes()

    # Kontroll: urørt fil er 200.
    r_ok = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                       json={"owner_claim_id": claim}, headers=hoder)
    assert r_ok.status_code == 200, r_ok.text
    assert r_ok.content == kropp

    for navn, bytes_ in (("for stor", ekte + b"\x00" * 4096),
                         ("for kort", ekte[:-1]),
                         ("ødelagt", bytes(b ^ 0xFF for b in ekte))):
        sti.write_bytes(bytes_)
        r = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                        json={"owner_claim_id": claim}, headers=hoder)
        assert r.status_code == 500, (navn, r.status_code, r.text)
        assert r.json()["feil"] == "intern_feil", (navn, r.text)

    # Og filen tilbake på plass er 200 igjen — driften var i lageret,
    # ikke i raden.
    sti.write_bytes(ekte)
    r_igjen = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                          json={"owner_claim_id": claim}, headers=hoder)
    assert r_igjen.status_code == 200, r_igjen.text
    assert r_igjen.content == kropp


@pg
def test_feil_modul_og_ubundet_gir_samme_ingenting(klient, migrator,
                                                   miljo, inndata_rot):
    """Feil modul 404 og ubundet 404 — ingen orakel over hva som finnes.
    «Feil modul» måles med en syntetisk deployment: modultokenets
    modul_id er da aldri 'm57_ats', og resolverens eiermodul-match
    feller den uansett hvilken modulstreng den bærer."""
    from .test_modul_onboarding_http import _kjede
    rel = _m57_deployment(migrator)
    kropp, tenant, oid = _bundet_bunt(klient, migrator)
    claim = _pluk(migrator, tenant, oid, rel)

    # Fremmed deployment (syntetisk modul-id) → 404 på samme oppdrag,
    # SELV MED riktig kapabilitet (eiermodul-porten feller først).
    modul2, rel2 = _kjede(migrator,
                          typenavn=f"rekr.x.{secrets.token_hex(4)}")
    mtk2, _ = _onboard_token(klient, migrator, modul2, rel2)
    r = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                    json={"owner_claim_id": claim},
                    headers={"authorization": f"Bearer {mtk2}"})
    assert r.status_code == 404, r.text

    # Ubundet, plukket oppdrag hos riktig modul → samme 404.
    _kropp2, tenant2, oid2 = _bundet_bunt(klient, migrator, bind=False)
    claim2 = _pluk(migrator, tenant2, oid2, rel)
    mtk, _ = _onboard_token(klient, migrator, "m57_ats", rel)
    r2 = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid2}",
                     json={"owner_claim_id": claim2},
                     headers={"authorization": f"Bearer {mtk}"})
    assert r2.status_code == 404, r2.text


@pg
def test_tuklet_wrapped_dek_er_intern_feil(klient, migrator, miljo,
                                           monkeypatch, inndata_rot):
    """Cursor P2 (#202, verifiseringspass): KEK-unwrap er samme
    feilkontrakt som bunt-dekrypten — en unwrap som avviser (tuklet
    wrap, feil AAD, for kort blob) gir sanert intern_feil, aldri
    rammeverks-500.

    `wrapped_dek` er destruksjons-vaktet i basen (kan aldri BYTTES), så
    avvisningen injiseres i selve unwrap-funksjonen — det er nøyaktig
    unntaksveien porten fanger, uansett hvilken byte som var tuklet."""
    rel = _m57_deployment(migrator)
    kropp, tenant, oid = _bundet_bunt(klient, migrator)
    claim = _pluk(migrator, tenant, oid, rel)
    mtk, _ = _onboard_token(klient, migrator, "m57_ats", rel)

    from db import kryptering as kryptmodul

    def _avvis(*_a, **_k):
        raise ValueError("tuklet wrap")

    monkeypatch.setattr(kryptmodul, "_pakk_ut", _avvis)
    r = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                    json={"owner_claim_id": claim},
                    headers={"authorization": f"Bearer {mtk}"})
    assert r.status_code == 500, r.text
    assert r.json()["feil"] == "intern_feil"


@pg
def test_rett_som_dor_under_leveransen_gir_ingenting(klient, migrator,
                                                     miljo, monkeypatch,
                                                     inndata_rot):
    """Cursor P1 (#202, andre pass): dommen felles FØR lesing+dekrypt av
    inntil 64 MiB — i det vinduet kan leasen løpe ut og en NY holder
    reclaime. Retten re-måles ved leveransen: den gamle requesten får
    404, aldri bytes fra en rett som døde underveis.

    Vinduet treffes deterministisk: dekrypteringen får en sideeffekt som
    utløper leasen og reclaimer til ny claim-id FØR den slipper videre.

    MUTASJONEN SOM DREPER DENNE: fjern leveranse-re-målingen i
    hent_endepunkt."""
    rel = _m57_deployment(migrator)
    kropp, tenant, oid = _bundet_bunt(klient, migrator)
    claim = _pluk(migrator, tenant, oid, rel)
    mtk, _ = _onboard_token(klient, migrator, "m57_ats", rel)

    from db import kryptering as kryptmodul
    ekte = kryptmodul.dekrypter_bytes
    fra_test = {"truffet": False}

    def _dekrypt_med_kappløp(*a, **k):
        # Kjøres i requestens vindu mellom dom og leveranse: leasen
        # utløper og en annen holder reclaimer.
        if not fra_test["truffet"]:
            fra_test["truffet"] = True
            from db.pg import koble, sett_kontekst
            m2 = koble(MIGRATOR_DSN)
            try:
                sett_kontekst(m2, tenant, "test", "r-race")
                m2.execute(
                    "UPDATE oppdrag SET owner_claim_id=%s"
                    " WHERE tenant=%s AND id=%s",
                    (secrets.token_hex(16), tenant, oid))
                m2.commit()
            finally:
                m2.close()
        return ekte(*a, **k)

    monkeypatch.setattr(kryptmodul, "dekrypter_bytes",
                        _dekrypt_med_kappløp)
    r = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                    json={"owner_claim_id": claim},
                    headers={"authorization": f"Bearer {mtk}"})
    assert fra_test["truffet"], "kappløpsvinduet ble aldri truffet"
    assert r.status_code == 404, r.text


@pg
def test_token_som_dor_under_leveransen_gir_ingen_bytes(klient, migrator,
                                                        miljo, monkeypatch,
                                                        inndata_rot):
    """Cursor P2 (#202, tredje pass): leveranse-re-målingen er TO ledd, og
    bare det ene var målt.

    Kommentaren over re-målingen lover at HELE porten kjøres igjen —
    `_modultoken_revalidert` (er deploymenten fortsatt autorisert?) OG
    060-predikatet (holder claimet fortsatt?). Testen over muterer bare
    `owner_claim_id`, og dreper dermed kun det andre leddet: fjernet man
    `_modultoken_revalidert(...)` fra leveranseblokken og lot resten stå,
    forble suiten grønn — mens et nødstopp eller en tilbakekalling felt
    ETTER den første dommen fortsatt slapp PII ut av huset.

    Her dør TOKENET, ikke claimet: claimet er urørt hele veien, så
    060-oppslaget svarer fortsatt «ja», og bare revalideringen kan stoppe
    dette. Samme sidekanalmønster som testen over — tilbakekallingen
    committes fra en EGEN forbindelse inne i `dekrypter_bytes`, altså
    nøyaktig i vinduet mellom dom og leveranse.

    Svaret er `token_ugyldig` (401), det SAMME hver annen modulvei gir for
    et dødt token: den samme hendelsen skal se lik ut uansett dør.

    MUTASJONEN SOM DREPER DENNE: fjern `_modultoken_revalidert(...)` fra
    leveranseblokken i `hent_endepunkt`."""
    rel = _m57_deployment(migrator)
    kropp, tenant, oid = _bundet_bunt(klient, migrator)
    claim = _pluk(migrator, tenant, oid, rel)
    mtk, _ = _onboard_token(klient, migrator, "m57_ats", rel)
    # Wire-formatet er `mtk_<token_id>.<secret>` (modulonboarding.py:16) —
    # id-en er tokenets egen, ikke noe testen finner på.
    token_id = mtk.split(".")[0][4:]

    from db import kryptering as kryptmodul
    ekte = kryptmodul.dekrypter_bytes
    fra_test = {"truffet": False}

    def _dekrypt_med_tilbakekalling(*a, **k):
        if not fra_test["truffet"]:
            fra_test["truffet"] = True
            from db.pg import koble
            # Runtime-rollen er den som eier tilbakekallingsveien i
            # produksjon (035: EXECUTE gis til runtime i migrer.py).
            rt = koble(DSN)
            try:
                rt.execute(
                    "SELECT tilbakekall_modultoken(%s::uuid,%s,'test')",
                    (token_id, "tilbakekalt i leveransevinduet"))
                rt.commit()
            finally:
                rt.close()
        return ekte(*a, **k)

    monkeypatch.setattr(kryptmodul, "dekrypter_bytes",
                        _dekrypt_med_tilbakekalling)
    r = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                    json={"owner_claim_id": claim},
                    headers={"authorization": f"Bearer {mtk}"})
    assert fra_test["truffet"], "leveransevinduet ble aldri truffet"
    # 401/`token_ugyldig`, ikke 404: claimet er urørt, så 060-leddet
    # svarer fortsatt «ja» og ville gitt 404. At svaret er 401 er derfor
    # i seg selv beviset på at det var REVALIDERINGEN som stoppet bytene.
    assert (r.status_code, r.json()["feil"]) == (401, "token_ugyldig"), r.text


@pg
def test_annen_release_i_samme_miljo_far_ingenting(klient, migrator,
                                                   miljo, inndata_rot):
    """Cursor P2 (#202, andre pass): miljø-negativen alene lot en
    regresjon som droppet claim_release_id-leddet være grønn — to
    releaser kan leve i SAMME miljø (035). Claim stemplet med A;
    token for B + lekket kapabilitet → 404; A → 200."""
    rel_a = _m57_deployment(migrator)
    kropp, tenant, oid = _bundet_bunt(klient, migrator)
    claim = _pluk(migrator, tenant, oid, rel_a)
    rel_b = _m57_deployment(migrator, tvang_ny=True)
    mtk_b, _ = _onboard_token(klient, migrator, "m57_ats", rel_b)
    r = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                    json={"owner_claim_id": claim},
                    headers={"authorization": f"Bearer {mtk_b}"})
    assert r.status_code == 404, r.text
    mtk_a, _ = _onboard_token(klient, migrator, "m57_ats", rel_a)
    r2 = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                     json={"owner_claim_id": claim},
                     headers={"authorization": f"Bearer {mtk_a}"})
    assert r2.status_code == 200, r2.text
    assert r2.content == kropp


@pg
def test_oppdrag_id_utenfor_bigint_er_feilformet_ikke_driftsavvik(
        klient, migrator, miljo, inndata_rot):
    """Codex-P2 (#202): en id utenfor `bigint` ble et FALSKT driftssignal.

    Starlettes `:int`-konverter er `[0-9]+` uten øvre grense, så
    `/hent-for-oppdrag/9223372036854775808` ruter fint og gir et fullgodt
    Python-heltall. Først `%s::bigint` avviser det — med
    `NumericValueOutOfRange`, som er en `psycopg.Error` og derfor ble
    slukt av basefase-vakten og rapportert som `db_utilgjengelig`: 503 og
    en `art="drift"`-hendelse som sier at basen er nede, på ren
    klientinput. Kontrakten sier 400.

    Mutasjon: stryk grensesjekken i `hent_endepunkt` → 503 i stedet for
    400, og testen er rød.
    """
    rel = _m57_deployment(migrator)
    kropp, tenant, oid = _bundet_bunt(klient, migrator)
    mtk, _ = _onboard_token(klient, migrator, "m57_ats", rel)
    claim = _pluk(migrator, tenant, oid, rel)

    # Kontroll: den EKTE id-en henter fortsatt bunten. Uten den ville en
    # 400 fra en ødelagt rigg sett ut som bestått.
    ok = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                     json={"owner_claim_id": claim},
                     headers={"authorization": f"Bearer {mtk}"})
    assert ok.status_code == 200, ok.text
    assert ok.content == kropp

    # `2**63` er første verdi utenfor, `2**64+1` er godt forbi den:
    # begge er feilformet input, ingen av dem er en driftshendelse.
    for utenfor in (2**63, 2**64 + 1):
        r = klient.post(f"/v1/inndata/hent-for-oppdrag/{utenfor}",
                        json={"owner_claim_id": claim},
                        headers={"authorization": f"Bearer {mtk}"})
        assert (r.status_code, r.json()["feil"]) == \
            (400, "request_feilformet"), (utenfor, r.status_code, r.text)

    # Og den blir IKKE et oraklet: uten token svarer ruten fortsatt på
    # token-en først, ikke på at id-en var for stor.
    ru = klient.post(f"/v1/inndata/hent-for-oppdrag/{2**63}",
                     json={"owner_claim_id": claim})
    assert ru.status_code == 401, ru.text
