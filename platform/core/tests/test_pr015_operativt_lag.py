"""PR-015: operativt lag — resolverarbeider, fire øyne, kapabilitet, rydding.

Portene fra klarsignalets §8. Alle tester konstruerer EGEN tilstand; ingen delt
fixture. Der en port måler en INVARIANT (budsjettet, dobbeltstemme, foreldet
revisjon) konstrueres den populasjonen som ville brutt den, ikke en snill en —
en port som bare ser normaltilfellet beviser ingenting.

Rollediscipin, som i 014b-testene: `_admin()` (`disponit_domains_admin`) kaller
FUNKSJONER, `migrator` leser og skriver TABELLER. Arbeiderrollen har bevisst
ingen bordtilgang til `domenekontroll` — scheduleren ser populasjonen gjennom
`revalideringskandidater()`, ikke gjennom et grant den kunne brukt til hva som
helst.
"""
import hashlib
import json
import math
import secrets

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, TENANT, ANNEN_TENANT, migrator, miljo  # noqa: F401
from .test_m37 import _sett_kontekst

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

TREDJE_TENANT = "tredje-tenant-pr015"

# Utfordringstokenet `_enige()` legger i TXT-svaret. `domenekontroll` lagrer
# kun sha256 av det (016: «klartekst vises ÉN gang, lagres aldri»), så testene
# holder klarteksten her og hasher der raden legges inn.
BEVIS_TOKEN = "t"


def _host():
    return "d" + secrets.token_hex(6) + ".example"


def _admin():
    """migrator SET ROLE domains_admin (committed → overlever rollback)."""
    from db.pg import koble
    c = koble(MIGRATOR_DSN)
    c.execute("SET ROLE disponit_domains_admin")
    c.commit()
    return c


def _dkrow(conn, tenant, hostname):
    """(status, generasjon, siste_vellykkede) lest som migrator."""
    _sett_kontekst(conn, tenant)
    r = conn.execute("SELECT status, autorisasjonsgenerasjon,"
                     " siste_vellykkede_revalidering FROM domenekontroll"
                     " WHERE tenant=%s AND hostname=%s",
                     (tenant, hostname)).fetchone()
    conn.rollback()
    return r


def _verifisert(conn, tenant, hostname, *, alder_timer=0, bevis=BEVIS_TOKEN):
    """Legg inn en verifisert rad med kontrollert revalideringsalder.

    `challenge_token_hash` settes: revalidering krever siden 019 §3.35 at den
    avtalte TXT-mengden inneholder utfordringsbeviset, ikke bare at resolverne
    er enige. En rad uten lagret utfordring kan ikke revalideres i det hele
    tatt — det er porten, ikke en mangel ved oppsettet.
    """
    _sett_kontekst(conn, tenant)
    conn.execute(
        "INSERT INTO domenekontroll (tenant, hostname, status,"
        " autorisasjonsgenerasjon, verifisert_ts, siste_vellykkede_revalidering,"
        " utloper, challenge_token_hash) VALUES (%s,%s,'verifisert',1, now(),"
        " now() - make_interval(hours => %s), now()+interval '90 days', %s)",
        (tenant, hostname, alder_timer,
         hashlib.sha256(bevis.encode()).hexdigest()))
    conn.commit()


def _tom_populasjon(conn, tenant):
    """Nullstill tenantens domenerader.

    Budsjettet regnes av populasjonen, så en test som skal måle K må eie hele
    nevneren. `domenekontroll` har en sletteverntrigger (016), som må vike for
    testoppsettet — nøyaktig som `_rydd` i test_api gjør for append-only-tabellene.
    """
    _sett_kontekst(conn, tenant)
    conn.execute("ALTER TABLE domenekontroll DISABLE TRIGGER USER")
    conn.execute("DELETE FROM domenekontroll WHERE tenant=%s", (tenant,))
    conn.execute("ALTER TABLE domenekontroll ENABLE TRIGGER USER")
    conn.commit()


def _res(navn, operator, nett, svar):
    from drift.domenerevalidering import Resolver
    return Resolver(navn=navn, operator=operator, nett=nett,
                    slå_opp=lambda _h: svar)


def _enige():
    return [_res("x", "op1", "n1", frozenset({"t"})),
            _res("y", "op2", "n2", frozenset({"t"}))]


def _uenige():
    return [_res("x", "op1", "n1", frozenset({"1"})),
            _res("y", "op2", "n2", frozenset({"2"}))]


# ===========================================================================
# Planen (§2) — rene funksjoner. Port 8 og 9.
# ===========================================================================

def test_plan_er_avledet_og_deterministisk():
    """Port 8: restore fra backup gir identisk plan.

    Ikke fordi vi gjenoppretter en plan, men fordi det ikke FINNES en plan å
    gjenopprette: minuttet er en ren funksjon av hostnavnet.
    """
    import hashlib
    from drift.domenerevalidering import revalideringsminutt, DOGN_MINUTTER
    h = "eksempel.example"
    assert revalideringsminutt(h) == revalideringsminutt(h)
    assert 0 <= revalideringsminutt(h) < DOGN_MINUTTER
    # Kjent verdi, så en endring i utledningen ikke kan skje ubemerket: en ny
    # hashfunksjon ville flyttet HELE populasjonen på én deploy.
    ventet = int(hashlib.sha256(h.encode()).hexdigest()[0:8], 16) % 1440
    assert revalideringsminutt(h) == ventet


def test_retry_slott_forskyver_ikke_normalplanen():
    """Port 9: forsøk 2 og 3 ligger på +4 t/+8 t, og planen er uendret.

    Et feilet forsøk kan ikke forskyve normalslottet fordi normalslottet ikke
    lagres noe sted — det utledes på nytt hver gang.
    """
    from drift.domenerevalidering import revalideringsminutt, slott_minutter
    h = "retry.example"
    m = revalideringsminutt(h)
    s = slott_minutter(h)
    assert s == (m, (m + 240) % 1440, (m + 480) % 1440)
    assert slott_minutter(h) == s and revalideringsminutt(h) == m


def test_jitter_holder_seg_innenfor_slottet():
    """Jitter er ±5 min INNENFOR slottet — den flytter aldri en rad ut."""
    from drift.domenerevalidering import jitter_minutt, JITTER_MINUTTER
    for i in range(200):
        for slott in (0, 700, 1439):
            assert abs(jitter_minutt(f"j{i}.example", slott)) <= JITTER_MINUTTER


@pg
def test_sql_minutt_er_identisk_med_python_minutt(migrator):
    """SQL-en og Python-en MÅ gi samme minutt.

    Utvalget skjer i SQL, rapporteringen i Python. Divergerer de to, plukker
    scheduleren andre rader enn den rapporterer — og begge deler ser riktige ut
    hver for seg. Derfor måles de mot hverandre, ikke hver for seg.
    """
    from drift.domenerevalidering import revalideringsminutt, _MINUTT_SQL
    for i in range(50):
        h = f"m{i}.example"
        i_sql = int(migrator.execute(
            f"SELECT {_MINUTT_SQL} FROM (SELECT %s::TEXT AS hostname) t",
            (h,)).fetchone()[0])
        assert i_sql == revalideringsminutt(h), h
    migrator.rollback()


# ===========================================================================
# Resolverkontrakten (§2.4) — port 1, 2, 3, 4, 11.
# ===========================================================================

def test_port4_diversitet_er_deploy_port():
    """Port 4: resolverkonfigurasjon uten diversitet → oppstart NEKTES."""
    from drift.domenerevalidering import krev_diversitet, Diversitetsfeil
    krev_diversitet(_enige())                          # skal ikke kaste
    with pytest.raises(Diversitetsfeil):
        krev_diversitet(_enige()[:1])                  # bare én
    with pytest.raises(Diversitetsfeil):               # samme operatør
        krev_diversitet([_res("a", "op1", "n1", frozenset()),
                         _res("b", "op1", "n2", frozenset())])
    with pytest.raises(Diversitetsfeil):               # samme nett
        krev_diversitet([_res("a", "op1", "n1", frozenset()),
                         _res("b", "op2", "n1", frozenset())])


def test_resolverparser_avviser_tomme_komponenter(monkeypatch):
    """Codex (P2): skilletegn til stede ≠ uavhengighetsmetadata til stede.

    `a@/net1=...,b@op2/=...` har både «@», «/» og «=», så kardinalitetssjekkene
    i `krev_diversitet` passerer — den tomme strengen teller som en distinkt
    operatør og et distinkt nett. Utrullingen ville dermed sertifisert
    resolverdiversitet på et oppsett der uavhengigheten aldri ble oppgitt.
    """
    from drift import kjor_revalidering as kr
    from drift.domenerevalidering import Diversitetsfeil
    from drift.kjor_revalidering import resolvere

    # Transporten stubbes: dnspython er en driftsavhengighet, og parseren skal
    # kunne måles uten den (samme grunn som den late importen i `_txt_oppslag`).
    monkeypatch.setattr(kr, "_txt_oppslag", lambda adresse: (lambda h: frozenset()))

    gyldig = "a@op1/net1=1.1.1.1,b@op2/net2=8.8.8.8"
    monkeypatch.setenv("DISPONIT_RESOLVERE", gyldig)
    assert len(resolvere()) == 2

    for ugyldig in ("a@/net1=1.1.1.1,b@op2/=8.8.8.8",     # tom operator/nett
                    "@op1/net1=1.1.1.1,b@op2/net2=8.8.8.8",   # tomt navn
                    "a@op1/net1=,b@op2/net2=8.8.8.8"):        # tom adresse
        monkeypatch.setenv("DISPONIT_RESOLVERE", ugyldig)
        with pytest.raises(Diversitetsfeil):
            resolvere()


def test_uenige_resolvere_er_ikke_vellykket():
    """Port 2 (ren del): uenighet → ikke vellykket. Ikke flertall, ikke «minst én»."""
    from drift.domenerevalidering import enige
    assert enige(_enige(), "x.example") is True
    assert enige(_uenige(), "x.example") is False


def test_oppslag_som_kaster_teller_som_uenighet():
    """«Vi fikk ikke svar» er ikke «svaret var ja»."""
    from drift.domenerevalidering import Resolver, enige

    def sprekker(_h):
        raise TimeoutError("ingen svar")

    assert enige([Resolver("a", "op1", "n1", sprekker),
                  _res("b", "op2", "n2", frozenset({"t"}))], "x.example") is False


@pg
def test_port2_uenighet_rorer_ikke_siste_vellykkede(migrator):
    """Port 2: `siste_vellykkede_revalidering` står URØRT ved uenighet."""
    from drift import domenerevalidering as dr
    h = _host()
    _verifisert(migrator, TENANT, h, alder_timer=30)
    for_ = _dkrow(migrator, TENANT, h)[2]
    a = _admin()
    try:
        res = dr.kjor(a, _uenige())
    finally:
        a.close()
    assert _dkrow(migrator, TENANT, h)[2] == for_, \
        "uenige resolvere oppdaterte likevel raden"
    assert res.vellykket == 0 and res.uenige_resolvere >= 1


@pg
def test_enighet_uten_utfordringsbevis_er_ikke_revalidering(migrator):
    """Codex P1: ENIGHET er ikke KONTROLL.

    Kontrollen er tapt og utfordrings-TXT-en fjernet, men sonen har fortsatt
    en stabil TXT-verdi (her SPF). Alle resolvere er da fullt enige, og med
    enighet alene som kriterium ville arbeideren friskmeldt
    `siste_vellykkede_revalidering` for den TIDLIGERE kontrolløren. Beviset må
    ligge i svaret.
    """
    from drift import domenerevalidering as dr
    h = _host()
    _verifisert(migrator, TENANT, h, alder_timer=30)
    for_ = _dkrow(migrator, TENANT, h)[2]
    spf = frozenset({"v=spf1 -all"})
    enige_uten_bevis = [_res("x", "op1", "n1", spf),
                        _res("y", "op2", "n2", spf)]
    # Resolverne ER enige — porten ligger ikke i enighetsprøven.
    assert dr.enige(enige_uten_bevis, h) is True
    a = _admin()
    try:
        res = dr.kjor(a, enige_uten_bevis)
    finally:
        a.close()
    assert _dkrow(migrator, TENANT, h)[2] == for_, \
        "revalidering registrert uten at utfordringsbeviset lå i TXT-svaret"
    assert res.vellykket == 0
    # Ikke uenighet: resolverne var samstemte. Det som feilet var beviset.
    assert res.oppslagsfeil >= 1


@pg
def test_revalidering_uten_lagret_utfordring_nektes(migrator):
    """Fail-closed: ingen lagret utfordring → ingenting å bevise mot.

    En rad uten `challenge_token_hash` skal ikke kunne revalideres. Alternativet
    — å tolke «ingen lagret utfordring» som «ingenting å kreve» — ville gjort
    porten til en no-op for nøyaktig de radene som ikke kan dokumentere noe.
    """
    h = _host()
    _verifisert(migrator, TENANT, h, alder_timer=30)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE domenekontroll SET challenge_token_hash = NULL"
                     " WHERE tenant=%s AND hostname=%s", (TENANT, h))
    migrator.commit()
    for_ = _dkrow(migrator, TENANT, h)[2]
    a = _admin()
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            a.execute("SELECT revalider_domenekontroll(%s,%s,'sys',%s)",
                      (TENANT, h, [BEVIS_TOKEN]))
    finally:
        a.rollback()
        a.close()
    assert _dkrow(migrator, TENANT, h)[2] == for_


def _execute_mottakere(conn, signatur):
    """Ikke-eiende roller med EXECUTE på funksjonen, som navn. PUBLIC er `-`.

    `aclexplode` på en TOM `proacl` gir null rader, men tom ACL er ikke
    «ingen mottakere»: den betyr standardrettigheten, og for en funksjon er
    den EXECUTE for PUBLIC. Uten dette skillet ville den farligste tilstanden
    sett ut som den strengeste, så en NULL-ACL rapporteres som PUBLIC.
    """
    acl = conn.execute("SELECT proacl FROM pg_proc"
                       " WHERE oid = to_regprocedure(%s)",
                       (signatur,)).fetchone()
    assert acl is not None, f"{signatur} finnes ikke i basen"
    if acl[0] is None:
        conn.rollback()
        return {"-"}
    rader = conn.execute(
        "SELECT g.grantee::regrole::text FROM pg_proc p,"
        " aclexplode(p.proacl) g WHERE p.oid = to_regprocedure(%s)"
        "   AND g.privilege_type = 'EXECUTE' AND g.grantee <> p.proowner",
        (signatur,)).fetchall()
    conn.rollback()
    return {r[0] for r in rader}          # PUBLIC (grantee 0) blir '-'


@pg
def test_arbeiderrollen_har_ikke_bevislos_revalidering(migrator):
    """Arbeideren skal KUN nå bevisformen.

    Beholdt 3-argumentsform på arbeiderrollen ville vært en åpen dør rundt
    hele porten. Arbeiderrollen er en KLYNGErolle: den opprettes av
    oppsettskriptet, ikke av migrasjonen, så den finnes ikke i alle baser
    porten måles i. Å hoppe over testen da ville latt porten stå ubevist i
    nettopp den basen CI kjører — derfor måles den her på ACL-en i stedet for
    på rollen: uansett base skal INGEN annen enn `disponit_domains_admin` nå
    den bevisløse 3-argumentsformen. Finnes arbeiderrollen i tillegg (staging),
    måles den direkte oppå.
    """
    bevislos = "revalider_domenekontroll(text,text,text)"
    bevisform = "revalider_domenekontroll(text,text,text,text[])"

    assert _execute_mottakere(migrator, bevislos) == {"disponit_domains_admin"}, \
        "en annen rolle enn adjudikasjonsadministratoren når den bevisløse formen"
    assert "disponit_domains_admin" in _execute_mottakere(migrator, bevisform)

    finnes = migrator.execute(
        "SELECT 1 FROM pg_roles WHERE rolname='disponit_domener'").fetchone()
    migrator.rollback()
    if not finnes:
        return
    q = lambda sql: migrator.execute(sql).fetchone()[0]     # noqa: E731
    assert q("SELECT has_function_privilege('disponit_domener',"
             "'revalider_domenekontroll(text,text,text)','EXECUTE')") is False
    assert q("SELECT has_function_privilege('disponit_domener',"
             "'revalider_domenekontroll(text,text,text,text[])','EXECUTE')") is True
    migrator.rollback()


# 019s default-deny-modell, funksjon for funksjon. Signaturene skrives ut i
# stedet for å hentes fra katalogen: en test som spør basen hvilke funksjoner
# 019 laget, ville godtatt at en av dem forsvant.
DEFAULT_DENY = [
    # 041 §21: signaturen bærer nå revisjonen attestanten SÅ (Codex P1).
    # 019s åtte-arg utgave er DROPPET, ikke overlastet — står den igjen,
    # finnes det en ugjerdet vei til den samme stemmen.
    "avgi_overtakelse_attestasjon"
    "(text,bigint,text,text,text,text,bigint,text,bigint)",
    "degrader_forbigatte_utfordrere(text,text)",
    "antall_avgitte_attestasjoner(bigint,bigint)",
    "lukk_overtakelsessak(text,bigint,text,text)",
    "revalideringskandidater(int,int,int,int,int)",
    "revalideringspopulasjon()",
    "rydd_staged_artefakter(int)",
    "antall_karantenesatte()",
    "revalider_domenekontroll(text,text,text,text[])",
]


@pg
def test_019_default_deny_gjelder_faktisk(migrator):
    """PUBLIC skal ikke nå NOEN av funksjonene 019 gjerder.

    Porten finnes fordi en `REVOKE ... FROM PUBLIC` kan MISLYKKES stille:
    kjøres den av en rolle som ikke eier funksjonen, advarer PostgreSQL og
    går videre — men materialiserer samtidig standard-ACL-en, som for en
    funksjon er EXECUTE for PUBLIC. Resultatet er det motsatte av det
    migrasjonen sier: alle roller i klyngen slipper inn, og ingen funksjonell
    test ser det, fordi et privilegium PUBLIC allerede har aldri feiler.
    Derfor måles gjerdet her på ACL-en, ikke på at et kall lykkes.
    """
    apne = [sig for sig in DEFAULT_DENY
            if "-" in _execute_mottakere(migrator, sig)]
    assert not apne, f"PUBLIC har EXECUTE på: {', '.join(apne)}"


@pg
def test_port1_to_samtidige_kjoringer_serialiseres(migrator):
    """Port 1: to samtidige kjøringer → én kjører, én venter (og gjør ingenting).

    Arbeidernøkkelen er en advisory-lås. Den andre kjøringen returnerer tomt i
    stedet for å stå i kø — planen er avledet, så det som ikke ble plukket nå,
    plukkes uansett neste time.
    """
    from drift import domenerevalidering as dr
    h = _host()
    _verifisert(migrator, TENANT, h, alder_timer=30)
    a, b = _admin(), _admin()
    try:
        a.execute("SELECT pg_advisory_lock(%s)", (dr.ARBEIDERNOKKEL,))
        a.commit()
        res = dr.kjor(b, _enige())
        assert res.plukket_ko1 == 0 and res.vellykket == 0, \
            "andre kjøring kjørte selv om arbeidernøkkelen var tatt"
    finally:
        a.execute("SELECT pg_advisory_unlock(%s)", (dr.ARBEIDERNOKKEL,))
        a.commit()
        a.close()
        b.close()


@pg
def test_port3_arbeideren_setter_aldri_status(migrator):
    """Port 3: tre døgn uten svar → raden verken slettet eller `utlopt`-satt.

    Arbeideren har ingen autoritet. En rad som ikke lar seg revalidere blir
    liggende, `verifisert`, med sitt gamle tidsstempel — synlig som nettopp det
    den er. Ferskheten i `v_domeneautorisasjon` gjør jobben.
    """
    from drift import domenerevalidering as dr
    h = _host()
    _verifisert(migrator, TENANT, h, alder_timer=72)
    a = _admin()
    try:
        dr.kjor(a, _uenige())
    finally:
        a.close()
    rad = _dkrow(migrator, TENANT, h)
    assert rad is not None, "arbeideren slettet raden"
    assert rad[0] == "verifisert", f"arbeideren satte status til {rad[0]}"


# ===========================================================================
# Budsjettet (§2.2) — port 5, 6, 7, 10, 10b.
# ===========================================================================

@pg
def test_port5_patologisk_populasjon_bryter_aldri_K(migrator):
    """Port 5: mange plukkbare rader → kø 2 + kø 3 overskrider ALDRI K.

    Populasjonen er konstruert patologisk: alle radene er ferske nok til å
    holde seg unna sikkerhetsnettet, men gamle nok til å være plukkbare. Da er
    det utelukkende `LIMIT K` som står mellom scheduleren og en kjøring som
    revaliderer hele populasjonen på én time.
    """
    from drift import domenerevalidering as dr
    _tom_populasjon(migrator, TENANT)
    for i in range(60):
        _verifisert(migrator, TENANT, f"pat{i}-{secrets.token_hex(3)}.example",
                    alder_timer=21)
    a = _admin()
    try:
        N, K = dr.budsjett(a)
        assert K == math.ceil(0.10 * N)
        res = dr.kjor(a, _enige())
    finally:
        a.close()
    assert res.ko2_pluss_ko3 <= res.budsjett_K, (
        f"budsjettbrudd: kø2+kø3={res.ko2_pluss_ko3} > K={res.budsjett_K}")
    assert res.plukket_ko1 == 0, "ingen rad skulle nådd sikkerhetsnettet"


@pg
def test_port10_sikkerhetsnett_plukkes_selv_nar_K_er_brukt_opp(migrator):
    """Port 10: rad over 26 t plukkes i SAMME kjøring selv når K er oppbrukt.

    Totalen får da overskride K — det er en MÅLT hendelse
    (`sikkerhetsnett.kjoringer_over_K`), ikke en feil. Invarianten gjelder kø 2
    + kø 3, ikke totalen; blandet man de to sammen, ville sikkerhetsnettet blitt
    kappet av budsjettet, som er nøyaktig det §2.1 forbyr.
    """
    from drift import domenerevalidering as dr
    _tom_populasjon(migrator, TENANT)
    for i in range(10):
        _verifisert(migrator, TENANT, f"nett{i}-{secrets.token_hex(3)}.example",
                    alder_timer=40)
    a = _admin()
    try:
        res = dr.kjor(a, _enige())
    finally:
        a.close()
    assert res.plukket_ko1 == 10, "sikkerhetsnettet ble kappet"
    assert res.ko2_pluss_ko3 <= res.budsjett_K
    assert res.kjoring_over_K is True, "hendelsen ble ikke MÅLT"


@pg
def test_port10b_samtidighet_aldri_over_C(migrator):
    """Port 10b: kø 1 med mange rader → samtidighet aldri over C, null droppet.

    «Ubegrenset rett til å bli plukket» er ikke «ubegrenset arbeid». Alle rader
    behandles; det er takten som er begrenset.
    """
    from drift import domenerevalidering as dr
    _tom_populasjon(migrator, TENANT)
    antall = 40
    for i in range(antall):
        _verifisert(migrator, TENANT, f"c{i}-{secrets.token_hex(3)}.example",
                    alder_timer=40)
    a = _admin()
    try:
        res = dr.kjor(a, _enige())
    finally:
        a.close()
    assert res.plukket_ko1 == antall, "rader ble droppet fra kø 1"
    assert res.maks_samtidighet <= dr.SAMTIDIGHET, (
        f"samtidighet {res.maks_samtidighet} > C={dr.SAMTIDIGHET}")
    assert res.vellykket == antall, "ikke alle rader ble faktisk behandlet"


@pg
def test_port6_bootstrap_rapporterer_faktisk_fordeling(migrator):
    """Port 6: bootstrap → K aldri overskredet, og faktisk fordeling RAPPORTERT.

    Fordelingen er en MÅLT egenskap (§2.3): testen krever at den finnes og
    summerer riktig, ikke at den er jevn. `sha256 mod 1440` garanterer ikke
    jevnhet, og en test som krevde det ville vært en test på flaks.
    """
    from drift import domenerevalidering as dr
    _tom_populasjon(migrator, TENANT)
    for i in range(120):
        _verifisert(migrator, TENANT, f"boot{i}-{secrets.token_hex(3)}.example",
                    alder_timer=21)
    a = _admin()
    try:
        res = dr.kjor(a, _enige())
    finally:
        a.close()
    assert res.ko2_pluss_ko3 <= res.budsjett_K
    assert res.fordeling_per_time, "fordelingen ble ikke rapportert"
    assert sum(res.fordeling_per_time.values()) == (
        res.plukket_ko1 + res.ko2_pluss_ko3)


@pg
def test_port7_outage_kohorten_er_monotont_synkende(migrator):
    """Port 7: outage-KOHORTEN monotont synkende mot null, tom innen 24 t.

    Målt på den IDENTIFISERTE kohorten, ikke på global kø 3. Det er hele poenget
    med presiseringen: nytt etterslep fra en senere skjev time er legitimt og
    teller ikke som recovery-feil, så en global kø-3-måling ville rapportert
    falske brudd på en helt frisk drenering.
    """
    from drift import domenerevalidering as dr
    _tom_populasjon(migrator, TENANT)
    kohort = [f"out{i}-{secrets.token_hex(3)}.example" for i in range(30)]
    for h in kohort:
        _verifisert(migrator, TENANT, h, alder_timer=25)
    igjen = []
    a = _admin()
    try:
        for _ in range(24):
            dr.kjor(a, _enige())
            _sett_kontekst(migrator, TENANT)
            n = int(migrator.execute(
                "SELECT count(*) FROM domenekontroll WHERE tenant=%s"
                "   AND hostname = ANY(%s)"
                "   AND siste_vellykkede_revalidering"
                "       < now() - interval '20 hours'",
                (TENANT, kohort)).fetchone()[0])
            migrator.rollback()
            igjen.append(n)
    finally:
        a.close()
    assert igjen == sorted(igjen, reverse=True), (
        f"kohorten var ikke monotont synkende: {igjen}")
    assert igjen[-1] == 0, f"kohorten ble ikke tom innen 24 kjøringer: {igjen}"


@pg
def test_port11_bred_feil_gir_en_alarm_og_ingen_m37_sak(migrator):
    """Port 11: bred resolverfeil → ÉN driftsalarm, null M-37-saker.

    Terskelen dedupliserer VARSLINGEN; den klassifiserer ikke tenantens
    tilstand. Radene står urørt og er fortsatt individuelt synlige — alarmen
    sier «vi fikk ikke svar», aldri «domenene er tapt».
    """
    from drift import domenerevalidering as dr
    _tom_populasjon(migrator, TENANT)
    for i in range(10):
        _verifisert(migrator, TENANT, f"bred{i}-{secrets.token_hex(3)}.example",
                    alder_timer=40)
    _sett_kontekst(migrator, TENANT)
    saker_for = int(migrator.execute(
        "SELECT count(*) FROM unntak WHERE tenant=%s", (TENANT,)).fetchone()[0])
    migrator.rollback()

    a = _admin()
    try:
        res = dr.kjor(a, _uenige())
    finally:
        a.close()
    assert res.alarm_utlost is True, "bred feil utløste ingen alarm"

    _sett_kontekst(migrator, TENANT)
    saker_etter = int(migrator.execute(
        "SELECT count(*) FROM unntak WHERE tenant=%s", (TENANT,)).fetchone()[0])
    synlige = int(migrator.execute(
        "SELECT count(*) FROM domenekontroll WHERE tenant=%s"
        "   AND status='verifisert'"
        "   AND siste_vellykkede_revalidering < now() - interval '26 hours'",
        (TENANT,)).fetchone()[0])
    migrator.rollback()
    assert saker_etter == saker_for, "bred feil opprettet M-37-sak(er)"
    assert synlige == 10, "radene sluttet å være individuelt synlige"


def test_alarmterskelen_gir_en_feilet_kjoring(monkeypatch):
    """Codex (P1): alarmen skal FØRE et sted — ikke bare stå i JSON-linjen.

    Ingen journalkonsument, OnFailure eller annen utrullet vei leser
    `alarm.terskel_utlost`, så en kjøring som utløste terskelen ble bokført av
    systemd som vellykket. Kontrakten er derfor exit-koden, som for ryddejobben:
    alarm → `failed`, ingen alarm → `success`.
    """
    from drift import domenerevalidering as dr, kjor_revalidering as kr

    class Tilkobling:
        def close(self):
            pass

    # Transporten stubbes: dnspython er en driftsavhengighet, og alarmveien
    # skal kunne måles uten den (samme grunn som den late importen der).
    monkeypatch.setattr(kr, "_txt_oppslag", lambda adresse: (lambda h: frozenset()))
    monkeypatch.setenv("DISPONIT_RESOLVERE",
                       "a@op1/net1=192.0.2.1,b@op2/net2=192.0.2.2")
    monkeypatch.setenv("DISPONIT_DOMAINS_URL", "postgresql:///finnes-ikke")
    monkeypatch.setattr(kr, "_koble", lambda dsn: Tilkobling())

    rolig = dr.Revalideringsresultat(plukket_ko2=10, vellykket=10)
    monkeypatch.setattr(dr, "kjor", lambda *a, **k: rolig)
    assert kr.main() == 0, "en kjøring uten alarm ble rapportert som feilet"

    alarm = dr.Revalideringsresultat(plukket_ko2=10, uenige_resolvere=9,
                                     alarm_utlost=True)
    monkeypatch.setattr(dr, "kjor", lambda *a, **k: alarm)
    assert kr.main() == 1, "alarmterskelen ga fortsatt en vellykket kjøring"


# ===========================================================================
# Fire øyne (§4) — port 14, 15, 16, 17, 18, 20.
# ===========================================================================

def _konflikt(migrator, tenant_a, tenant_b, hostname):
    """Kjør A→B-overtakelsen. -> (sak_id, B-generasjon).

    041: overtakelsen LAGER saken selv (`sikre_overtakelsessak()` i samme
    transaksjon, på plattformtenanten) — fixturen slår den bare opp.
    """
    a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (tenant_a, hostname))
        a.commit()
        svar = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                         (tenant_b, hostname)).fetchone()[0]
        a.commit()
    finally:
        a.close()
    assert svar.startswith("konflikt:"), svar
    gen = _dkrow(migrator, tenant_b, hostname)[1]
    _sett_kontekst(migrator, "__plattform_domener")
    sak = int(migrator.execute(
        "SELECT id FROM unntak WHERE hostname_ref=%s"
        "  AND sakskilde='domeneovertakelse' AND NOT terminal",
        (hostname,)).fetchone()[0])
    migrator.rollback()
    return sak, gen


def _saksstatus(migrator, tenant, sak):
    # 041: saken bor på plattformtenanten uansett hvilken part spørsmålet
    # gjelder — `tenant`-parameteren beholdes i kallene som dokumentasjon
    # av HVEM saken handlet om, men adressen er plattformens.
    _sett_kontekst(migrator, "__plattform_domener")
    status = migrator.execute(
        "SELECT status FROM unntak WHERE tenant='__plattform_domener'"
        " AND id=%s", (sak,)).fetchone()[0]
    migrator.rollback()
    return status


def _saksrevisjon(migrator, sak):
    """Sakens EGEN revisjon — stemmenes navnerom fra 041 §21.

    Før 041 var navnerommet domeneradens `autorisasjonsgenerasjon`, som
    holdt så lenge hver utfordrer hadde sin egen sak. Skiftet (A→B→C på
    SAMME sak) gjorde den nøkkelen tvetydig; sakens revisjon er entydig.
    """
    _sett_kontekst(migrator, "__plattform_domener")
    r = migrator.execute(
        "SELECT saksrevisjon FROM unntak WHERE tenant='__plattform_domener'"
        " AND id=%s", (sak,)).fetchone()[0]
    migrator.rollback()
    return int(r)


def _adjudikator(tenant, sub, *, roller="ARRAY['domeneadjudikator']"):
    """Aktiv domeneadjudikator i `tenant`. Returnerer bruker_id.

    Attestasjonene reautoriseres mot `brukermedlemskap` når terskelen slår inn
    (Codex), så en stemme MÅ ha en ekte prinsipal bak seg. Medlemskapet er
    OIDC-forvaltet (FK til `brukeridentitet`, runtime kan lese men ikke skrive)
    og opprettes derfor via migrator, som i drift.
    """
    from db.pg import koble
    from .test_pr010_db import _identitet
    m = koble(MIGRATOR_DSN)
    try:
        _sett_kontekst(m, tenant)
        bid = _identitet(m, sub=f"{tenant}-{sub}")
        m.execute(f"INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
                  f" VALUES (%s,%s,{roller})"
                  f" ON CONFLICT (tenant,bruker_id) DO UPDATE SET"
                  f" roller=EXCLUDED.roller, aktiv=true", (tenant, bid))
        m.commit()
        return bid
    finally:
        m.close()


def _sett_medlemskap(tenant, bid, **felt):
    """Endre medlemskapet (aktiv/roller). Triggeren bumper authz_version."""
    from db.pg import koble
    m = koble(MIGRATOR_DSN)
    try:
        _sett_kontekst(m, tenant)
        sett = ", ".join(f"{k}={v}" for k, v in felt.items())
        m.execute(f"UPDATE brukermedlemskap SET {sett}"
                  f" WHERE tenant=%s AND bruker_id=%s", (tenant, bid))
        m.commit()
    finally:
        m.close()


def _saksrevisjon(sak):
    """Sakens GJELDENDE revisjon.

    Egen migrator-forbindelse: saken bor på `__plattform_domener`, og 041
    §9.1s RESTRICTIVE `reservert_navnerom` slipper bare claimeren,
    adjudikatoren og TABELLEIEREN inn i det navnerommet —
    `disponit_domains_admin` (som `_attester` kaller funksjoner med) ser
    ingen rad der.
    """
    from db.pg import koble
    c = koble(MIGRATOR_DSN)
    try:
        _sett_kontekst(c, "__plattform_domener")
        rad = c.execute(
            "SELECT saksrevisjon FROM unntak WHERE tenant=%s AND id=%s"
            "  AND sakskilde='domeneovertakelse'",
            ("__plattform_domener", sak)).fetchone()
        c.rollback()
    finally:
        c.close()
    return int(rad[0]) if rad else None


def _attester(a, tenant, sak, hostname, utfall, vinner, aktor, gen,
              bruker_id=None, rev=None):
    """Avgi én attestasjon. `aktor` er evidensstrengen, `bruker_id` prinsipalen.

    Uten et eksplisitt `bruker_id` opprettes en adjudikator for `aktor` —
    testene som bare trenger «to distinkte, autoriserte aktører» slipper å
    gjenta oppsettet, mens de som måler reautoriseringen styrer det selv.

    `rev` er revisjonen stemmen avgis PÅ (Codex P1). Uten et eksplisitt tall
    leses sakens gjeldende — det er «en attestant som nettopp lastet køen»,
    altså normaltilfellet. Testen som måler gjerdet sender sitt eget.
    """
    if bruker_id is None:
        bruker_id = _adjudikator(tenant, aktor)
    if rev is None:
        rev = _saksrevisjon(sak)
    r = a.execute(
        "SELECT avgi_overtakelse_attestasjon(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, sak, hostname, utfall, vinner, aktor, gen,
         bruker_id, rev)).fetchone()[0]
    a.commit()
    return r


@pg
def test_port14_godkjenn_med_en_attestasjon_nektes(migrator):
    """Port 14: godkjenn med ÉN attestasjon → ingen avgjørelse.

    Terskelen er to DISTINKTE aktører for en positiv tildeling. Med én står
    saken uavgjort og B blir IKKE verifisert.
    """
    h = _host()
    sak, gen = _konflikt(migrator, TENANT, ANNEN_TENANT, h)
    a = _admin()
    try:
        assert _attester(a, ANNEN_TENANT, sak, h, "godkjenn", ANNEN_TENANT,
                         "aktor-1", gen) == "venter"
    finally:
        a.close()
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "avklaring_kreves"


@pg
def test_port14b_to_distinkte_aktorer_avgjor(migrator):
    """Motstykket: to distinkte aktører → avgjort, B verifisert.

    Uten denne ville port 14 vært oppfylt av en funksjon som aldri avgjør noe.
    """
    h = _host()
    sak, gen = _konflikt(migrator, TENANT, ANNEN_TENANT, h)
    a = _admin()
    try:
        _attester(a, ANNEN_TENANT, sak, h, "godkjenn", ANNEN_TENANT, "aktor-1", gen)
        assert _attester(a, ANNEN_TENANT, sak, h, "godkjenn", ANNEN_TENANT,
                         "aktor-2", gen) == "avgjort"
    finally:
        a.close()
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "verifisert"
    # Codex (P1): saken lukkes ATOMISK med domenevedtaket — den skal ikke
    # bli stående 'ny' og fortsette å vises som en åpen sak i PR-012-flaten.
    assert _saksstatus(migrator, ANNEN_TENANT, sak) == "løst"


@pg
def test_tilbakekalt_adjudikator_teller_ikke_mot_terskelen(migrator):
    """Codex (P1): stemmene reautoriseres NÅR terskelen slår inn.

    Attestasjonstabellen er append-only. Mistet den første attestanten rollen
    sin mellom de to stemmene, ville en ren opptelling latt den andre stemmen
    gjennomføre overtakelsen med ÉN faktisk autorisert aktør — altså to øyne,
    ikke fire. Raden består som evidens (den ble avgitt), men den teller ikke.
    """
    h = _host()
    sak, gen = _konflikt(migrator, TENANT, ANNEN_TENANT, h)
    forste = _adjudikator(ANNEN_TENANT, "revokert-1")
    a = _admin()
    try:
        assert _attester(a, ANNEN_TENANT, sak, h, "godkjenn", ANNEN_TENANT,
                         "aktor-revokert", gen, forste) == "venter"
        # Rollen trekkes tilbake. Triggeren i 010 bumper authz_version, som er
        # nøyaktig det stemmen ble bundet til.
        _sett_medlemskap(ANNEN_TENANT, forste, roller="ARRAY['leser']")
        assert _attester(a, ANNEN_TENANT, sak, h, "godkjenn", ANNEN_TENANT,
                         "aktor-2", gen) == "venter", \
            "en tilbakekalt attestant talte fortsatt mot terskelen"
    finally:
        a.close()
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "avklaring_kreves"

    # Evidensen står: raden er ikke ryddet bort, den teller bare ikke.
    # 041 §21: navnerommet er sakens egen revisjon, ikke domeneradens
    # generasjon — saken har ikke skiftet her, så den står på 0.
    rev = _saksrevisjon(migrator, sak)      # egen transaksjon: rulles tilbake
    _sett_kontekst(migrator, ANNEN_TENANT)
    n = int(migrator.execute(
        "SELECT count(*) FROM overtakelse_attestasjon"
        " WHERE sak_id=%s AND saksrevisjon=%s", (sak, rev)).fetchone()[0])
    migrator.rollback()
    assert n == 2, "attestasjonene ble ryddet bort i stedet for å stå som evidens"


@pg
def test_ikke_adjudikator_kan_ikke_attestere(migrator):
    """Basen er den andre porten: rollen sjekkes der, ikke bare i API-laget.

    `domains:adjudicate` er en applikasjonsavledning av rollen. Fire øyne skal
    ikke hvile på at applikasjonen husket å spørre.
    """
    h = _host()
    sak, gen = _konflikt(migrator, TENANT, ANNEN_TENANT, h)
    utenfor = _adjudikator(ANNEN_TENANT, "bare-leser", roller="ARRAY['leser']")
    a = _admin()
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _attester(a, ANNEN_TENANT, sak, h, "avvis", ANNEN_TENANT,
                      "aktor-uten-rolle", gen, utenfor)
        a.rollback()
    finally:
        a.close()
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "avklaring_kreves"


@pg
def test_port15_samme_aktor_to_ganger_avvises_av_primarnokkelen(migrator):
    """Port 15: samme aktør to ganger → avvist av PRIMÆRNØKKELEN, ikke av UI.

    Testen går rett på funksjonen, utenom ethvert UI, nettopp for å bevise at
    det er databasen som nekter.
    """
    h = _host()
    sak, gen = _konflikt(migrator, TENANT, ANNEN_TENANT, h)
    a = _admin()
    try:
        bid = _adjudikator(ANNEN_TENANT, "samme")
        _attester(a, ANNEN_TENANT, sak, h, "godkjenn", ANNEN_TENANT, "samme",
                  gen, bid)
        with pytest.raises(psycopg.errors.UniqueViolation):
            a.execute(
                "SELECT avgi_overtakelse_attestasjon"
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ANNEN_TENANT, sak, h, "godkjenn", ANNEN_TENANT, "samme", gen,
                 bid, _saksrevisjon(sak)))
        a.rollback()
    finally:
        a.close()
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "avklaring_kreves", \
        "dobbeltstemmen avgjorde saken"


@pg
def test_port16_ulikt_utfall_gir_ingen_avgjorelse(migrator):
    """Port 16: to attestasjoner som ikke er identiske → INGEN avgjørelse.

    Aldri en sammenslåing. Begge radene bevares — de er evidens for at to
    autoriserte aktører mente forskjellige ting.
    """
    h = _host()
    sak, gen = _konflikt(migrator, TENANT, ANNEN_TENANT, h)
    a = _admin()
    try:
        _attester(a, ANNEN_TENANT, sak, h, "godkjenn", ANNEN_TENANT, "aktor-1", gen)
        assert _attester(a, ANNEN_TENANT, sak, h, "avvis", ANNEN_TENANT,
                         "aktor-2", gen) == "venter"
    finally:
        a.close()
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "avklaring_kreves"
    _sett_kontekst(migrator, ANNEN_TENANT)
    n = int(migrator.execute(
        "SELECT count(*) FROM overtakelse_attestasjon WHERE sak_id=%s",
        (sak,)).fetchone()[0])
    migrator.rollback()
    assert n == 2, "en uenig attestasjon ble ikke bevart"


@pg
def test_port18_avvis_med_en_attestasjon_tilbakekaller(migrator):
    """Port 18: avvis med ÉN attestasjon → B tilbakekalt. Fail-closed."""
    h = _host()
    sak, gen = _konflikt(migrator, TENANT, ANNEN_TENANT, h)
    a = _admin()
    try:
        assert _attester(a, ANNEN_TENANT, sak, h, "avvis", ANNEN_TENANT,
                         "aktor-1", gen) == "avgjort"
    finally:
        a.close()
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "tilbakekalt"
    # Codex (P1): avvisningen lukker saken også — 'avvist', ikke stående 'ny'.
    assert _saksstatus(migrator, ANNEN_TENANT, sak) == "avvist"


@pg
def test_port17_ny_konflikt_foreldet_ventende_attestasjon(migrator):
    """Port 17: C overtar med B-attestasjon inne → revisjonen økt, stemmen teller ikke.

    Raden BEVARES: den er evidens for at noen attesterte et utfall som ble
    foreldet. Forsvant den, ville «ingen stemte» og «stemmen ble ugyldig» sett
    like ut i ettertid.

    Revisjonsøkningen skjer i `degrader_forbigatte_utfordrere` — 016 lar B stå
    urørt når C tar over, og PR-015 legger degraderingen utenpå uten å røre
    016-kroppen: triggeren på `hostname_binding` (019 §3.25) fyrer i det
    bindingen flyttes til C, altså inne i samme overtakelse.
    """
    h = _host()
    sak, gen_b = _konflikt(migrator, TENANT, ANNEN_TENANT, h)
    a = _admin()
    try:
        _attester(a, ANNEN_TENANT, sak, h, "godkjenn", ANNEN_TENANT, "aktor-1", gen_b)
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TREDJE_TENANT, h))
        a.commit()
    finally:
        a.close()

    # B-attestasjonen er BEVART på sin gamle revisjon — 041 §21: saken er
    # skiftet til C og står nå på revisjon 1, mens Bs stemme ble avgitt på
    # 0 og blir liggende der. Nettopp DET er poenget med navnerommet: Bs
    # bevarte stemme kan ikke lenger blande seg inn i Cs opptelling.
    assert _saksrevisjon(migrator, sak) == 1, "skiftet bumpet ikke revisjonen"
    _sett_kontekst(migrator, ANNEN_TENANT)
    n = int(migrator.execute(
        "SELECT count(*) FROM overtakelse_attestasjon"
        " WHERE sak_id=%s AND saksrevisjon=0", (sak,)).fetchone()[0])
    migrator.rollback()
    assert n == 1, "den foreldede attestasjonen ble borte"

    # ...men B er degradert og revisjonen økt, så stemmen kan ikke telle.
    rad_b = _dkrow(migrator, ANNEN_TENANT, h)
    assert rad_b[0] == "tilbakekalt", rad_b
    assert rad_b[1] > gen_b, "saksrevisjonen ble ikke økt av overtakelsen"

    a = _admin()
    try:
        with pytest.raises(psycopg.Error):
            a.execute(
                "SELECT avgi_overtakelse_attestasjon"
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ANNEN_TENANT, sak, h, "godkjenn", ANNEN_TENANT, "aktor-2",
                 gen_b, _adjudikator(ANNEN_TENANT, "aktor-2"),
                 _saksrevisjon(sak)))
        a.rollback()
    finally:
        a.close()
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] != "verifisert", \
        "foreldet attestasjon autoriserte B"


@pg
def test_port20_abc_kun_c_i_avklaring_og_a_gjenoppstar_ikke(migrator):
    """Port 20: A→B→C — hver overtakelse tilbakekaller forrige, KUN C i avklaring.

    016 lar den forbigåtte B stå i `avklaring_kreves` (den grenen setter bare C
    i avklaring og flytter bindingen). B kan ikke bli godkjent — gjerdet mot
    `hostname_binding` stopper det — men statusen blir aldri terminal, og
    porten måler statusen. `degrader_forbigatte_utfordrere` lukker det uten å
    røre en eneste 016-kropp.

    Codex (P1): degraderingen må skje av SELVE overtakelsen. Den lå tidligere i
    `opprett_overtakelsessak()`, som ingen produksjonsvei kaller, så porten var
    i praksis kun oppfylt når en test ringte funksjonen manuelt. Testen kaller
    den derfor IKKE lenger før den måler: triggeren på `hostname_binding`
    (019 §3.25) skal ha gjort jobben i det C tok bindingen.

    Codex (P2): SAKEN følger utfordreren ut. B kan aldri fullføre
    adjudikasjonen etter degraderingen — `avgi_overtakelse_attestasjon` krever
    `avklaring_kreves` — så en B-sak som ble stående `ny` ville blitt liggende
    som en åpen, handlingskrevende sak ingen kan avgjøre.
    """
    h = _host()
    b_sak, _ = _konflikt(migrator, TENANT, ANNEN_TENANT, h)
    assert _saksstatus(migrator, ANNEN_TENANT, b_sak) == "ny"
    a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TREDJE_TENANT, h))
        a.commit()
        # Overtakelsen alene skal ha degradert B — ingen manuell opprydding.
        assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "tilbakekalt", \
            "triggeren degraderte ikke den forbigåtte utfordreren"
        # Idempotent: et manuelt kall etterpå finner ingenting å gjøre.
        assert int(a.execute("SELECT degrader_forbigatte_utfordrere(%s,'sys')",
                             (h,)).fetchone()[0]) == 0
        a.commit()
    finally:
        a.close()
    assert _dkrow(migrator, TREDJE_TENANT, h)[0] == "avklaring_kreves"
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "tilbakekalt"
    assert _dkrow(migrator, TENANT, h)[0] == "tilbakekalt", "A gjenoppstod"
    # 041 (port 6): A→B→C er et SKIFTE på SAMME sak — ikke en B-sak som
    # lukkes og en C-sak som åpnes. Saken står åpen med C som utfordrer og
    # saksrevisjonen bumpet; B har ingen egen sak å bli sittende fast i.
    _sett_kontekst(migrator, "__plattform_domener")
    rad = migrator.execute(
        "SELECT status, utfordrer_tenant, tapt_tenant, saksrevisjon"
        "  FROM unntak WHERE tenant='__plattform_domener' AND id=%s",
        (b_sak,)).fetchone()
    migrator.rollback()
    assert rad == ("ny", TREDJE_TENANT, ANNEN_TENANT, 1), \
        f"skiftet fulgte ikke port 6: {rad}"


@pg
def test_skifte_navnerommer_stemmene_pa_saksrevisjonen(migrator):
    """Codex P1 (041 §21): Bs bevarte stemme må ikke låse Cs konflikt.

    019 navnerommet stemmene på domeneradens `autorisasjonsgenerasjon`.
    Det holdt så lenge hver utfordrer hadde sin EGEN sak — men 041 gjorde
    A→B→C til et SKIFTE på samme `unntak.id`, og en fersk C-rad settes inn
    med generasjon 1, akkurat som B-raden hadde. Bs godkjenning (vinnende
    tenant B) ble da et AVVIK i Cs opptelling, og terskelen returnerte
    `venter` uansett hvor mange autoriserte adjudikatorer som stemte for
    C: konflikten kunne ALDRI avgjøres.

    Navnerommet er nå sakens egen `saksrevisjon` (+1 ved hvert skifte).
    Foreldelsesgjerdet på generasjonen står uendret — porten måler at de
    to gjør hver sin jobb.
    """
    h = _host()
    sak, gen_b = _konflikt(migrator, TENANT, ANNEN_TENANT, h)
    a = _admin()
    try:
        # B får ÉN godkjenning: ikke nok, men den blir stående som evidens.
        assert _attester(a, ANNEN_TENANT, sak, h, "godkjenn", ANNEN_TENANT,
                         "skifte-b", gen_b) == "venter"
        # C tar over → SAMME sak, revisjon+1, ny utfordrer (port 6/20).
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TREDJE_TENANT, h))
        a.commit()
        gen_c = _dkrow(migrator, TREDJE_TENANT, h)[1]
        # Bs foreldede stemme kan ikke lenger avgis på nytt heller: saken
        # navngir C nå, så Bs (tenant, generasjon) treffer ingen sak.
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _attester(a, ANNEN_TENANT, sak, h, "godkjenn", ANNEN_TENANT,
                      "skifte-b2", gen_b)
        a.rollback()
        # ... og Cs to distinkte aktører avgjør saken, uforstyrret.
        assert _attester(a, TREDJE_TENANT, sak, h, "godkjenn", TREDJE_TENANT,
                         "skifte-c1", gen_c) == "venter"
        assert _attester(a, TREDJE_TENANT, sak, h, "godkjenn", TREDJE_TENANT,
                         "skifte-c2", gen_c) == "avgjort"
    finally:
        a.close()
    assert _dkrow(migrator, TREDJE_TENANT, h)[0] == "verifisert"
    assert _saksstatus(migrator, TREDJE_TENANT, sak) == "løst"
    # Bs stemme står — i SITT navnerom (revisjon 0), Cs i revisjon 1.
    # Tabellen er tenant-scopet (RLS), så hver part leses i sin kontekst.
    def _rev(tenant):
        _sett_kontekst(migrator, tenant)
        r = migrator.execute(
            "SELECT saksrevisjon, count(*) FROM overtakelse_attestasjon"
            " WHERE sak_id=%s GROUP BY 1 ORDER BY 1", (sak,)).fetchall()
        migrator.rollback()
        return r
    assert _rev(ANNEN_TENANT) == [(0, 1)], _rev(ANNEN_TENANT)
    assert _rev(TREDJE_TENANT) == [(1, 2)], _rev(TREDJE_TENANT)


@pg
def test_stemmen_teller_i_konflikten_attestanten_sa(migrator):
    """Codex P1 (041 §21): en gammel fane avgjør ikke en ny tvist.

    A→B→C→B er det ekte vinduet. `unntak.id` er STABIL gjennom hele
    syklusen — saken skifter utfordrer, den lukkes ikke — så en
    adjudikatorflate som har stått åpen hos B peker fortsatt på samme sak,
    og B er igjen utfordreren. Alt endepunktet leste ferskt av raden
    stemte derfor: sakskilden, utfordreren, generasjonen. Bare ÉN ting
    stemte ikke — hvilken konflikt attestanten faktisk hadde lest og
    bekreftet: «B utfordrer A», mens raden nå sa «B utfordrer C».

    Uten et gjerde landet stemmen i den nye konflikten, og to gamle faner
    kunne fullført en positiv tildeling ingen av dem hadde sett. Nettopp
    den avgjørelsen fire øyne finnes for.

    PORTEN MÅLER REVISJONEN, IKKE GENERASJONEN. Den foreldede stemmen
    sendes med den GJELDENDE generasjonen: da er foreldelsesgjerdet fra
    019 tilfreds, og det som feller stemmen kan bare være revisjonen.
    Motsatt vei måles i samme åndedrag — den ferske fanen slipper til, så
    porten ikke er oppfylt av at alt blir avvist.
    """
    h = _host()
    sak, _ = _konflikt(migrator, TENANT, ANNEN_TENANT, h)
    rev_gammel = _saksrevisjon(sak)
    a = _admin()
    try:
        # C tar hostnavnet fra B → samme sak, revisjon+1, utfordrer C.
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TREDJE_TENANT, h))
        a.commit()
        # ... og B tar det TILBAKE: ny generasjon på Bs rad, revisjon+1
        # igjen. Saken navngir nå B som utfordrer og C som tapende part —
        # samme id, samme utfordrer, en helt annen tvist.
        svar = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                         (ANNEN_TENANT, h)).fetchone()[0]
        a.commit()
        assert svar == f"konflikt:{TREDJE_TENANT}", svar
        gen_ny = _dkrow(migrator, ANNEN_TENANT, h)[1]
        rev_ny = _saksrevisjon(sak)
        assert rev_ny > rev_gammel, (rev_gammel, rev_ny)

        # DEN GAMLE FANEN: gjeldende generasjon, foreldet revisjon.
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _attester(a, ANNEN_TENANT, sak, h, "godkjenn", ANNEN_TENANT,
                      "gammel-fane", gen_ny, rev=rev_gammel)
        a.rollback()
        # ... og den som faktisk leste den gjeldende konflikten slipper til.
        assert _attester(a, ANNEN_TENANT, sak, h, "godkjenn", ANNEN_TENANT,
                         "fersk-fane", gen_ny, rev=rev_ny) == "venter"
    finally:
        a.close()
    # Ingen stemme fra den gamle fanen ble stående i det nye navnerommet.
    _sett_kontekst(migrator, ANNEN_TENANT)
    rader = migrator.execute(
        "SELECT saksrevisjon, count(*) FROM overtakelse_attestasjon"
        " WHERE sak_id=%s GROUP BY 1 ORDER BY 1", (sak,)).fetchall()
    migrator.rollback()
    assert rader == [(rev_ny, 1)], rader


@pg
def test_attestasjon_er_append_only_ogsa_mot_delete(migrator):
    """§1: foreldede attestasjoner kan ikke slettes.

    De er evidens for at noen attesterte et utfall som ble foreldet. Kan de
    slettes, mister vi akkurat den sporbarheten fire øyne skal gi.
    """
    h = _host()
    sak, gen = _konflikt(migrator, TENANT, ANNEN_TENANT, h)
    a = _admin()
    try:
        _attester(a, ANNEN_TENANT, sak, h, "godkjenn", ANNEN_TENANT, "aktor-1", gen)
    finally:
        a.close()
    _sett_kontekst(migrator, ANNEN_TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("DELETE FROM overtakelse_attestasjon WHERE sak_id=%s",
                         (sak,))
    migrator.rollback()
    _sett_kontekst(migrator, ANNEN_TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE overtakelse_attestasjon SET utfall='avvis'"
                         " WHERE sak_id=%s", (sak,))
    migrator.rollback()


# ===========================================================================
# Rydding (§6) — port 25, 26, 27.
# ===========================================================================

@pg
def test_port25_batchgrense_og_idempotens(migrator):
    """Port 25: idempotent rydding i avgrensede batcher.

    Grensen finnes for å begrense TRANSAKSJONEN, ikke sluttresultatet: kjøringen
    dreneres i flere avgrensede batcher, hver committet for seg.
    """
    from drift import artefaktrydding
    a = _admin()
    try:
        artefaktrydding.kjor(a, grense=5, maks_batcher=3)
        res2 = artefaktrydding.kjor(a, grense=5, maks_batcher=3)
        assert res2.forkastet == 0, "andre kjøring forkastet noe på nytt"
    finally:
        a.close()


@pg
def test_grense_maa_vaere_positiv(migrator):
    """En grense på 0 ville vært en ryddejobb som aldri rydder — stille."""
    a = _admin()
    try:
        for ugyldig in (0, -1):
            with pytest.raises(psycopg.Error):
                a.execute("SELECT rydd_staged_artefakter(%s)", (ugyldig,))
            a.rollback()
    finally:
        a.close()


@pg
def test_port26_karantene_bevares_og_telles(migrator):
    """Port 26: karantenesatt artefakt bevares og TELLES.

    Karantene bevares på EGENSKAP, ikke på alder: predikatet treffer kun
    `staged`, så et karantenesatt artefakt er utenfor rekkevidde uansett hvor
    gammelt det blir.
    """
    from drift import artefaktrydding
    a = _admin()
    try:
        for_ = int(a.execute("SELECT antall_karantenesatte()").fetchone()[0])
        res = artefaktrydding.kjor(a)
        etter = int(a.execute("SELECT antall_karantenesatte()").fetchone()[0])
    finally:
        a.close()
    assert res.karantene_bevart == for_, "ryddingen endret antallet karantenesatte"
    assert etter == for_, "et karantenesatt artefakt ble ryddet"


def test_port27_to_feilede_kjoringer_gir_alarm():
    """Port 27: to sammenhengende feilede kjøringer → alarm.

    Én feilet kjøring er drift; to på rad er en voksende disk ingen ser.
    """
    from drift import artefaktrydding

    class Sprekker:
        """Minimal dobbeltgjenger: låsen tas, ryddekallet feiler."""

        def execute(self, sql, args=None):
            if "rydd_staged_artefakter" in sql:
                raise psycopg.OperationalError("simulert feil")

            class R:
                def fetchone(self_inner):
                    return (True,)
            return R()

        def commit(self):
            pass

        def rollback(self):
            pass

    res1 = artefaktrydding.kjor(Sprekker(), tidligere_feil=0)
    assert res1.feilet and not res1.alarm_utlost, "alarm gikk på FØRSTE feil"
    res2 = artefaktrydding.kjor(Sprekker(), tidligere_feil=1)
    assert res2.feilet and res2.alarm_utlost, "to feil på rad ga ingen alarm"


def test_hoppet_over_kjoring_sletter_ikke_feiltellingen(tmp_path, monkeypatch):
    """Codex (P2): opptatt arbeidernøkkel = HOPPET OVER, ikke vellykket.

    En kjøring som ikke fikk låsen har verken ryddet eller feilet. Ble den
    rapportert som vellykket, skrev `main()` feiltellingen 0 — og ved
    overlappende kjøringer (manuell drift, flere verter, en henger som holder
    låsen) kunne hver forbigått aktivering slette en alt opptelt feil, slik at
    §6-alarmen aldri nådde to sammenhengende feil.
    """
    from drift import artefaktrydding, kjor_artefaktrydding as kar

    class Opptatt:
        """Låsen er tatt av en annen sesjon."""

        def execute(self, sql, args=None):
            assert "rydd_staged_artefakter" not in sql, "ryddet uten lås"

            class R:
                def fetchone(self_inner):
                    return (False,)
            return R()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    res = artefaktrydding.kjor(Opptatt())
    assert res.hoppet_over and not res.feilet, "hoppet over så ut som en kjøring"

    # ...og telleren står urørt gjennom `main()`.
    tilstand = tmp_path / "artefaktrydding.json"
    tilstand.write_text('{"feil": 1}', encoding="utf-8")
    monkeypatch.setenv("DISPONIT_RYDDETILSTAND", str(tilstand))
    monkeypatch.setenv("DISPONIT_DOMAINS_URL", "postgresql:///finnes-ikke")
    monkeypatch.setattr(kar, "_koble", lambda dsn: Opptatt())
    assert kar.main() == 0
    assert json.loads(tilstand.read_text(encoding="utf-8"))["feil"] == 1, \
        "en hoppet over kjøring slettet en opptelt feil"
