"""E-postsenderen — kopien av det som alt står i portalen.

Grensene som prøves her er de som gjør senderen trygg å la stå og gå:
  * den sender BARE til verifiserte adresser;
  * den KLAIMER raden før SMTP, aldri leste eller alt sendte;
  * to samtidige sendere kan ikke sende samme varsel to ganger — heller ikke
    når den ene starter mens den andre står midt i SMTP-kallet;
  * et klaim fra en kjøring som døde kommer tilbake i køen når leasen løper
    ut, og ikke ett minutt før;
  * én adresse som ikke tar imot stopper ikke resten av køen;
  * uten SMTP-oppsett rører den ikke køen — et manglende oppsett er en
    driftstilstand, ikke en egenskap ved varselet;
  * teksten rendres fra locale, ikke fra databasen.
"""
import re
import secrets
from pathlib import Path

import pytest

from api import varsel
from drift import varselsender

from .test_api import DSN, MIGRATOR_DSN

pg = pytest.mark.skipif(not MIGRATOR_DSN,
                        reason="DISPONIT_MIGRATOR_DSN ikke satt")
TEN = "t-sender-" + secrets.token_hex(3)


def _conn():
    from db.pg import koble, sett_kontekst
    c = koble(MIGRATOR_DSN)
    sett_kontekst(c, TEN, "test", "r0")
    return c


def _bruker(c, navn, epost, verifisert=True):
    import json
    bid = c.execute(
        "INSERT INTO brukeridentitet (issuer, sub, profil)"
        " VALUES (%s,%s,%s::jsonb)"
        " ON CONFLICT (issuer,sub) DO UPDATE SET profil=EXCLUDED.profil"
        " RETURNING bruker_id",
        ("https://idp.example", f"{TEN}-{navn}",
         json.dumps({"epost": epost, "epost_verifisert": verifisert}))
    ).fetchone()[0]
    c.execute("INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
              " VALUES (%s,%s,ARRAY['policyforvalter'])"
              " ON CONFLICT (tenant,bruker_id) DO UPDATE SET aktiv=true",
              (TEN, bid))
    return bid


def _ko(c, bid, ressurs):
    varsel.opprett(c, tenant=TEN, bruker_id=bid, art="attestering_venter",
                   ressurs_type="policyutkast", ressurs_id=ressurs,
                   hendelse="1", tekstnokkel="varsel.attestering_venter",
                   parametre={"policy_id": "p", "runde": 1,
                              "risikoklasse": "UTVIDER", "gjenstaar": 1})


def _kontekst(c):
    """Sett tenantkonteksten PÅ NYTT.

    `kjor()` committer per rad, og `sett_kontekst` bruker SET LOCAL — den dør
    med transaksjonen. Leser man etterpå uten å sette den igjen, filtrerer RLS
    bort alt, og assertions svarer «ingenting» uansett hva senderen gjorde.
    Tredje gang den fellen slår til i denne modulen; derfor en hjelper.
    """
    from db.pg import sett_kontekst
    sett_kontekst(c, TEN, "test", "r1")


def _samler():
    sendt = []
    return sendt, (lambda til, emne, tekst: sendt.append((til, emne, tekst)))


@pg
def test_sender_bare_til_verifiserte_adresser():
    """En uverifisert e-post i profilen er en PÅSTAND fra en IdP, ikke et
    bevis. Et varsel om en fullmaktsrunde skal ikke dit.

    Kontroll: fjern `epost_verifisert`-leddet i `varsel_klaim_epost`, så blir
    denne rød med to mottakere.
    """
    c = _conn()
    try:
        ok = _bruker(c, "ok", "ok@example.test", verifisert=True)
        nei = _bruker(c, "nei", "nei@example.test", verifisert=False)
        _ko(c, ok, "u-" + secrets.token_hex(4))
        _ko(c, nei, "u-" + secrets.token_hex(4))
        c.commit()
        sendt, send = _samler()
        res = varselsender.kjor(c, send=send)
        _kontekst(c)
        assert res["sendt"] == 1, res
        assert [t for t, _e, _x in sendt] == ["ok@example.test"]
    finally:
        c.close()


@pg
def test_samme_varsel_sendes_ikke_to_ganger():
    """Sekvensielt: en rad som er sendt er ute av køen for godt.

    Dette er den enkle halvdelen — «neste kjøring etter sendt». Den samtidige
    halvdelen, der kjøring nr. 2 starter mens nr. 1 står i SMTP-kallet, måles
    i `test_to_samtidige_sendere_sender_ikke_samme_epost_to_ganger`.
    """
    c = _conn()
    try:
        b = _bruker(c, "en", "en@example.test")
        _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()
        sendt, send = _samler()
        assert varselsender.kjor(c, send=send)["sendt"] == 1
        _kontekst(c)
        assert varselsender.kjor(c, send=send)["sendt"] == 0, (
            "andre kjøring sendte på nytt")
        assert len(sendt) == 1
    finally:
        c.close()


@pg
def test_en_adresse_som_feiler_stopper_ikke_resten():
    c = _conn()
    try:
        d = _bruker(c, "daarlig", "daarlig@example.test")
        g = _bruker(c, "god", "god@example.test")
        _ko(c, d, "u-" + secrets.token_hex(4))
        _ko(c, g, "u-" + secrets.token_hex(4))
        c.commit()
        sendt = []

        def send(til, emne, tekst):
            if til == "daarlig@example.test":
                raise RuntimeError("mottaker avviste")
            sendt.append(til)

        res = varselsender.kjor(c, send=send)
        _kontekst(c)
        assert res["sendt"] == 1 and res["feilet"] == 1, res
        assert sendt == ["god@example.test"]
        # Den feilede raden står IGJEN i portalen — innboksen er sannheten.
        assert varsel.antall_uleste(c, tenant=TEN, bruker_id=d) == 1
    finally:
        c.close()


@pg
def test_uten_smtp_oppsett_roeres_koen_ikke(monkeypatch):
    """Et manglende oppsett er en DRIFTSTILSTAND, ikke en egenskap ved
    varselet. Å brenne forsøkstelleren på det ville stille kastet varsler som
    er helt i orden.
    """
    for k in ("VERT", "PORT", "BRUKER", "PASSORD", "AVSENDER"):
        monkeypatch.delenv(f"DISPONIT_SMTP_{k}", raising=False)
    c = _conn()
    try:
        b = _bruker(c, "urort", "urort@example.test")
        _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()
        res = varselsender.kjor(c)
        _kontekst(c)
        assert res["grunn"] == "smtp_ikke_konfigurert", res
        st = c.execute("SELECT epost_status, epost_forsok FROM varsel"
                       " WHERE tenant=%s AND bruker_id=%s",
                       (TEN, b)).fetchone()
        assert st == ("koet", 0), f"køen ble rørt: {st}"
    finally:
        c.close()


@pg
def test_teksten_rendres_fra_locale_med_parametre():
    c = _conn()
    try:
        b = _bruker(c, "tekst", "tekst@example.test")
        _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()
        sendt, send = _samler()
        varselsender.kjor(c, send=send)
        _kontekst(c)
        _til, emne, tekst = sendt[0]
        assert "{policy_id}" not in tekst and "{gjenstaar}" not in tekst, (
            f"plassholdere står igjen: {tekst!r}")
        assert "UTVIDER" in tekst, f"parametrene kom ikke med: {tekst!r}"
        assert emne and not emne.startswith("varsel."), (
            f"emnet er en rå nøkkel: {emne!r}")
    finally:
        c.close()


def test_locale_finnes_uten_at_driftsstien_finnes(monkeypatch):
    """Senderen finner `locales/` fra SIN EGEN plassering, ikke fra en
    hardkodet driftssti.

    Roten var `/opt/disponit/aktiv` når `DISPONIT_REPO` manglet — sant på
    staging og ingen andre steder. I CI fantes ikke stien, og senderen kastet
    `FileNotFoundError` på hver eneste e-post: den kunne altså ikke prøves der
    den bygges. Modulen ligger i det samme repoet som `locales/`, så den vet
    selv hvor de er.

    Denne testen er med vilje uten `@pg`: den skal kjøre overalt, for det var
    nettopp «kjører bare ett sted» som var feilen.
    """
    monkeypatch.delenv("DISPONIT_REPO", raising=False)
    tekster = varselsender._locale("nb")
    assert tekster.get("varsel.attestering_venter"), \
        "locale-settet ble ikke funnet uten driftsstien"
    # Ukjent språk faller til nb i stedet for å kaste.
    assert varselsender._locale("zz").get("varsel.attestering_venter")
    # …og teksten er faktisk en setning, ikke nøkkelen som falt gjennom.
    tekst = varselsender.rendre(tekster, "varsel.attestering_venter",
                                {"policy_id": "p", "runde": 1,
                                 "risikoklasse": "UTVIDER", "gjenstaar": 2})
    assert "UTVIDER" in tekst and "{" not in tekst, f"uferdig tekst: {tekst!r}"


def test_rendre_viser_ukjent_nokkel_i_stedet_for_tomhet():
    """En manglende oversettelse skal være SYNLIG. En tom e-post forteller
    ingenting; en rå nøkkel forteller sannheten."""
    assert varselsender.rendre({}, "varsel.finnes.ikke", {}) \
        == "varsel.finnes.ikke"


# ---------------------------------------------------------------------------
# SPRÅKET. Løftet er mottakerens språk der hun har valgt, og installasjonens
# der hun ikke har. Innboksen har alltid holdt den første halvdelen — den
# rendrer nøkkelen i nettleseren med leserens eget valg. E-posten kunne ikke
# før `varselvalg.sprak` (028) ga serveren noe å slå opp.
#
# Testene under måler BEGGE halvdelene, og grensen mellom dem. Den grensen er
# der feilen satt: så lenge «ikke valgt» ble lagret som 'nb', fikk senderen
# alltid en gyldig verdi og tok den for et valg — og installasjonens
# `DISPONIT_VARSEL_SPRAK` var virkningsløs for nettopp den gruppen den fantes
# for (Codex P2, migrasjon 031). Uten disse testene ville «det er sånn med
# vilje» og «det er en feil ingen har sett» sett helt like ut i koden.
# ---------------------------------------------------------------------------

def test_spraket_er_installasjonens_valg_og_ikke_en_konstant(monkeypatch):
    """`DISPONIT_VARSEL_SPRAK` velger språket; `nb` er standarden.

    Uten dette leddet var «nb» en standardverdi i signaturen til `kjor`, og en
    engelskspråklig installasjon måtte endre kode for å få engelsk e-post —
    eller finne den ene kalleren og gi den et argument. Testen er uten `@pg`:
    den måler valget, ikke køen.
    """
    import importlib

    monkeypatch.setenv("DISPONIT_VARSEL_SPRAK", "en")
    modul = importlib.reload(varselsender)
    try:
        assert modul.SPRAK == "en"
        monkeypatch.delenv("DISPONIT_VARSEL_SPRAK")
        assert importlib.reload(varselsender).SPRAK == "nb", \
            "standarden er ikke lenger plattformens reservespråk"
    finally:
        # Modulen er delt med de andre testene i denne filen; last den
        # tilbake til miljøet de kjører i.
        importlib.reload(varselsender)


@pg
def test_epost_rendres_paa_MOTTAKERENS_sprak():
    """Løftet var mottakerens språk; kjøringen brukte ETT (Codex P2).

    Forgjengeren av denne testen låste den motsatte oppførselen fast og sa
    eksplisitt at den skulle skrives om den dagen klaimet bar språket. Den
    dagen er nå: `varselvalg.sprak` (028) settes av flaten når valget lagres,
    klaimet returnerer det per rad, og senderen rendrer hver e-post riktig i
    én og samme kjøring.

    To mottakere: én med engelsk valgt, én uten valg i det hele tatt. Den
    engelske får engelsk emne og tekst; den uten valg får standardspråket —
    ikke ingenting, og ikke den andres.

    Kontroll: la senderen bruke ett locale for hele kjøringen igjen, så blir
    denne rød på det engelske emnet.
    """
    c = _conn()
    try:
        en = _bruker(c, "engelsk", "en@example.test")
        nb = _bruker(c, "norsk", "nb@example.test")
        varsel.sett_kanal(c, tenant=TEN, bruker_id=en,
                          kanal="epost_og_portal", sprak="en")
        _ko(c, en, "u-" + secrets.token_hex(4))
        _ko(c, nb, "u-" + secrets.token_hex(4))
        c.commit()
        _kontekst(c)
        sendt, send = _samler()
        varselsender.kjor(c, send=send)
        _kontekst(c)
        per = {til: (emne, tekst) for til, emne, tekst in sendt}
        assert "en@example.test" in per and "nb@example.test" in per, per.keys()
        emne_en, tekst_en = per["en@example.test"]
        emne_nb, _t = per["nb@example.test"]
        assert "waiting" in emne_en, f"engelsk mottaker fikk: {emne_en!r}"
        assert "venter" in emne_nb, f"norsk mottaker fikk: {emne_nb!r}"
        assert "attestation" in tekst_en, (
            f"kroppen fulgte ikke mottakerens språk: {tekst_en!r}")
    finally:
        c.close()


@pg
def test_uten_eget_valg_gjelder_INSTALLASJONENS_sprak():
    """Den som ikke har valgt, er ikke norsk (Codex P2).

    `varselvalg.sprak` var `NOT NULL DEFAULT 'nb'`, og klaimet avsluttet med
    `coalesce(…, 'nb')`. Senderen fikk derfor ALLTID en gyldig verdi, og
    `(sprak or SPRAK)` — installasjonens valg — var uoppnåelig. På en
    installasjon satt opp med `DISPONIT_VARSEL_SPRAK=en` fikk hver mottaker
    uten eget valg e-posten på norsk. Innstillingen var virkningsløs for
    nettopp den gruppen den fantes for.

    Tre mottakere, fordi funnet har tre utganger og bare den midterste var
    feil:
      * INGEN `varselvalg`-rad → installasjonens språk;
      * rad, men uten uttrykt språk (kanalvalg fra en klient som ikke sender
        det) → installasjonens språk. Det var her 'nb' ble skrevet som om
        brukeren hadde valgt;
      * rad med uttrykt 'nb' → norsk, selv om installasjonen er engelsk.
        Uten den siste kunne funnet «fikses» ved å la installasjonen
        overkjøre alle, og det ville vært samme feil speilvendt.

    `sprak="en"` sendes til `kjor` i stedet for å settes i miljøet: det er
    samme ledd (`sprak or SPRAK`), og modulkonstanten leses ved import.
    """
    c = _conn()
    try:
        ingen = _bruker(c, "ingenrad", "ingen@example.test")
        tom = _bruker(c, "tomtsprak", "tom@example.test")
        valgt = _bruker(c, "valgtnb", "valgt@example.test")
        # Ingen `sett_kanal` for `ingen` — den har ikke noen rad i det hele
        # tatt, som er tilstanden enhver bruker har før hun rører innboksen.
        varsel.sett_kanal(c, tenant=TEN, bruker_id=tom,
                          kanal="epost_og_portal")
        varsel.sett_kanal(c, tenant=TEN, bruker_id=valgt,
                          kanal="epost_og_portal", sprak="nb")
        lagret = c.execute(
            "SELECT sprak FROM varselvalg WHERE tenant=%s AND bruker_id=%s",
            (TEN, tom)).fetchone()[0]
        assert lagret is None, (
            f"kanalvalg uten språk ble lagret som {lagret!r} — da finnes ikke"
            " «ikke uttrykt» lenger, og driftens valg er uoppnåelig")
        for b in (ingen, tom, valgt):
            _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()
        _kontekst(c)
        sendt, send = _samler()
        varselsender.kjor(c, send=send, sprak="en")
        _kontekst(c)
        per = {til: emne for til, emne, _t in sendt}
        for adr in ("ingen@example.test", "tom@example.test",
                    "valgt@example.test"):
            assert adr in per, f"{adr} fikk ingen e-post: {sorted(per)}"
        assert "waiting" in per["ingen@example.test"], (
            f"uten rad fulgte ikke installasjonen: {per['ingen@example.test']!r}")
        assert "waiting" in per["tom@example.test"], (
            f"uten uttrykt språk fulgte ikke installasjonen:"
            f" {per['tom@example.test']!r}")
        assert "venter" in per["valgt@example.test"], (
            f"et uttrykt 'nb' ble overkjørt av installasjonen:"
            f" {per['valgt@example.test']!r}")
    finally:
        c.close()


@pg
def test_feilet_epost_prøves_igjen_etter_backoff():
    """En feilet sending er IKKE endelig.

    Klaimet tar bare `koet`, så uten re-køing var `feilet` en blindvei og
    forsøkstelleren død kode: ett forbigående SMTP-hikk mistet e-posten for
    godt. Re-køingen er et eget steg nettopp for å beholde garantien om at
    `koet` er den eneste tilstanden et klaim kan ta fra.

    Kontroll: fjern `varsel_rekoe`-kallet i `kjor`, så blir denne rød.
    """
    c = _conn()
    try:
        b = _bruker(c, "retry", "retry@example.test")
        _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()

        def alltid_feil(til, emne, tekst):
            raise RuntimeError("midlertidig")

        assert varselsender.kjor(c, send=alltid_feil)["feilet"] == 1
        _kontekst(c)
        # Umiddelbart etterpå skal den IKKE prøves igjen — backoff.
        sendt, send = _samler()
        assert varselsender.kjor(c, send=send)["sendt"] == 0, "ingen backoff"
        _kontekst(c)
        # Skru tiden tilbake, og den skal komme tilbake i køen.
        c.execute("UPDATE varsel SET epost_ts = now() - interval '1 hour'"
                  " WHERE tenant=%s AND bruker_id=%s", (TEN, b))
        c.commit()
        _kontekst(c)
        assert varselsender.kjor(c, send=send)["sendt"] == 1, (
            "en feilet e-post ble aldri prøvd igjen")
    finally:
        c.close()


@pg
def test_retryen_stopper_eksakt_paa_maks_forsok(monkeypatch):
    """Den andre halvdelen av retry-løftet: den slutter å prøve.

    Testen over viser at en feilet e-post kommer TILBAKE i køen. Uten et tak
    målt like presist er det løftet like farlig som blindveien var: en adresse
    som aldri tar imot ville banket på hvert 15. minutt i evighet, og
    `MAKS_FORSOK` ville vært en konstant ingen test hadde sett virke.

    Backoffen skrus av ved å skru KLOKKA tilbake mellom rundene, ikke ved å
    sette den til null — da måles taket og backoffen hver for seg, og en
    runde som stoppet fordi backoffen bet ville ikke kunne forveksles med en
    runde som stoppet fordi taket bet.
    """
    monkeypatch.setattr(varselsender, "MAKS_FORSOK", 2)
    c = _conn()
    try:
        b = _bruker(c, "maks", "maks@example.test")
        _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()

        forsokt = []

        def alltid_feil(til, emne, tekst):
            forsokt.append(til)
            raise RuntimeError("mottaker avviser alltid")

        for _ in range(5):
            varselsender.kjor(c, send=alltid_feil)
            _kontekst(c)
            c.execute("UPDATE varsel SET epost_ts = now() - interval '1 hour'"
                      " WHERE tenant=%s AND bruker_id=%s", (TEN, b))
            c.commit()
            _kontekst(c)

        assert len(forsokt) == 2, (
            f"{len(forsokt)} forsøk, ikke MAKS_FORSOK=2 — taket bet ikke der "
            "det skulle")
        st = c.execute("SELECT epost_status, epost_forsok FROM varsel"
                       " WHERE tenant=%s AND bruker_id=%s",
                       (TEN, b)).fetchone()
        assert st == ("feilet", 2), f"endetilstanden er ikke terminal: {st}"
        # Og raden står IGJEN i portalen. E-posten er kopien; innboksen er
        # sannheten, og den skal ikke kunne gå tapt med den siste sendingen.
        assert varsel.antall_uleste(c, tenant=TEN, bruker_id=b) == 1
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Codex P1 (PR-068): plukket er et KLAIM, ikke en lesning.
#
# Testene over er sekvensielle og beviser bare «neste kjøring etter sendt».
# Funnet var det de ikke rørte: to kjøringer som OVERLAPPER. Timeren går hvert
# 5. minutt, en treg SMTP-server gjør en kjøring lengre enn det, og med et
# plukk som bare LESTE `koet` hentet begge samme rad, sendte begge e-posten og
# konkurrerte om statusen etterpå. At den andre UPDATE-en traff null rader er
# en opplysning som kommer for sent — SMTP er utført.
# ---------------------------------------------------------------------------

@pg
def test_to_samtidige_sendere_sender_ikke_samme_epost_to_ganger():
    """To forbindelser, og den andre starter INNE i den førstes SMTP-kall.

    Krysningen er hele poenget: kjører de etter hverandre, er selv det gamle
    plukket trygt. `send`-callbacken er nøyaktig det stedet der en ekte sender
    står og venter på en server, så en kjøring nr. 2 som starter der er den
    overlappen driften faktisk får.

    Kontroll: bytt `varsel_klaim_epost` tilbake til et plukk som bare SELECTer
    `koet`, og denne blir rød på at nr. 2 sendte den samme e-posten en gang
    til.

    Det assertes på MIN adresse og ikke på tellerne: senderen er kryss-tenant,
    så begge kjøringene tar med seg det andre tester har lagt i køen.
    """
    c1 = _conn()
    c2 = _conn()
    minadresse = "samtidig@example.test"
    try:
        b = _bruker(c1, "samtidig", minadresse)
        _ko(c1, b, "u-" + secrets.token_hex(4))
        c1.commit()

        en: list[str] = []
        to: list[str] = []
        krysset: list[str] = []

        def send_en(til, emne, tekst):
            en.append(til)
            if til != minadresse or krysset:
                return
            krysset.append(til)
            # Sett fra en ANNEN forbindelse, midt i sendingen: raden er alt
            # tatt ut av køen. Det er denne tilstanden `kjor` under lener seg
            # på — og den samme som lease-testen fabrikkerer.
            st = c2.execute("SELECT epost_status FROM varsel WHERE tenant=%s"
                            " AND bruker_id=%s", (TEN, b)).fetchone()
            assert st == ("under_sending",), (
                f"raden er {st} midt i sendingen — klaimet ble aldri "
                "committet, og nr. 2 ser den fortsatt som ledig")
            c2.commit()
            varselsender.kjor(c2, send=lambda t, _e, _x: to.append(t))

        res1 = varselsender.kjor(c1, send=send_en)
        _kontekst(c1)

        assert krysset, "krysningen skjedde aldri — testen målte ingenting"
        assert res1["mistet"] == 0, (
            "senderen mistet et klaim den holdt — returverdien fra "
            "statusoppdateringen sier ifra, og den skal leses")
        assert en.count(minadresse) == 1, en
        assert to.count(minadresse) == 0, (
            "den samtidige senderen sendte den samme e-posten en gang til")
        st = c1.execute("SELECT epost_status, epost_forsok FROM varsel"
                        " WHERE tenant=%s AND bruker_id=%s",
                        (TEN, b)).fetchone()
        assert st == ("sendt", 1), (
            f"{st} — ett forsøk, og det ble talt én gang")
    finally:
        c1.close()
        c2.close()


@pg
def test_klaim_fra_en_doed_kjoring_kommer_tilbake_naar_leasen_loper_ut():
    """Prisen for å ta raden ut av køen FØR sendingen: dør prosessen der, er
    raden klaimet av noen som ikke finnes.

    Uten en lease ville den blitt liggende `under_sending` for alltid — en
    stille tilstand ingen overvåker, og et varsel som aldri når frem. Med
    leasen kommer den tilbake, men først når den har løpt ut: en lease som
    utløper mens sendingen pågår ville gjenskapt nøyaktig den dobbeltsendingen
    klaimet finnes for å hindre. Begge halvdelene måles her.

    Krasjen fabrikkeres med en direkte UPDATE og ikke ved å drepe et ekte
    klaim, av én grunn: klaimet er kryss-tenant og kan ikke siktes på én rad,
    så et avbrutt ekte klaim ville etterlatt ANDRE testers rader låst til
    leasen løp ut. At tilstanden er den samme som klaimet skriver, er målt i
    testen over — fra en annen forbindelse, midt i sendingen.
    """
    c = _conn()
    try:
        b = _bruker(c, "krasj", "krasj@example.test")
        _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()
        _kontekst(c)
        # …og RETURNING, ikke bare UPDATE: uten konteksten over filtrerer RLS
        # bort raden, krasjen blir aldri fabrikkert, og testen måler at en
        # helt vanlig `koet`-rad blir sendt — grønn på feil grunnlag hvis den
        # hadde stått uten lease, rød og forvirrende når leasen virker.
        assert c.execute("UPDATE varsel SET epost_status='under_sending',"
                         " epost_ts=now(), epost_forsok=1"
                         " WHERE tenant=%s AND bruker_id=%s RETURNING id",
                         (TEN, b)).fetchall(), "krasjen ble aldri fabrikkert"
        c.commit()
        _kontekst(c)

        sendt, send = _samler()
        varselsender.kjor(c, send=send)
        _kontekst(c)
        assert [t for t, _e, _x in sendt if t == "krasj@example.test"] == [], (
            "raden ble tatt tilbake mens sendingen kunne pågå — leasen bet "
            "ikke")

        # Skru klokka forbi leasen, og den skal komme tilbake i køen.
        c.execute("UPDATE varsel SET epost_ts = now() - interval '2 hours'"
                  " WHERE tenant=%s AND bruker_id=%s", (TEN, b))
        c.commit()
        _kontekst(c)
        varselsender.kjor(c, send=send)
        _kontekst(c)
        assert [t for t, _e, _x in sendt
                if t == "krasj@example.test"] == ["krasj@example.test"], (
            "et klaim fra en død kjøring ble aldri gjenopptatt")
        st = c.execute("SELECT epost_status, epost_forsok FROM varsel"
                       " WHERE tenant=%s AND bruker_id=%s",
                       (TEN, b)).fetchone()
        # Forsøket som døde er TALT: et forsøk er noe som er påbegynt, ellers
        # ville en krasjsløyfe aldri nådd taket.
        assert st == ("sendt", 2), st
    finally:
        c.close()


@pg
def test_et_utloept_klaim_kan_ikke_fullfoere_erstatterens_sending():
    """Codex P2: fullføringen må gjelde DETTE klaimet, ikke bare denne raden.

    Leasen gjør at én rad kan være klaimet to ganger etter hverandre, og med
    et gjerde på `id` + `under_sending` alene kunne den FØRSTE senderen
    fullføre den ANDRE sitt levende klaim: A pauses forbi leasen, re-køingen
    løfter raden tilbake, B klaimer og står i SMTP-kallet — og der er raden
    `under_sending` igjen, altså akkurat det A sin oppdatering spurte etter.
    A skrev da `sendt` over B sin sending, og B fant ingenting å skrive
    resultatet sitt på: en e-post som faktisk gikk ut, uten en rad som sier
    hva som skjedde med den.

    Krysningen ligger inne i `send`, som i samtidighetstesten: det er der en
    ekte sender står og venter på en server, og det eneste øyeblikket B sitt
    klaim er levende.

    Kontroll: fjern `AND epost_klaim = p_klaim` fra `varsel_sett_epoststatus`,
    og A sin sene fullføring blir sann — testen blir rød på at B sitt klaim
    ble skrevet over.
    """
    c1 = _conn()
    c2 = _conn()
    minadresse = "utloept@example.test"
    try:
        b = _bruker(c1, "utloept", minadresse)
        _ko(c1, b, "u-" + secrets.token_hex(4))
        c1.commit()
        _kontekst(c1)

        # A sitt klaim, fabrikkert med en direkte UPDATE av samme grunn som i
        # lease-testen: et ekte klaim er kryss-tenant og kan ikke siktes på én
        # rad. Tokenet er en fersk uuid — nøyaktig det klaimet selv skriver.
        # Klokka står alt forbi leasen, så re-køingen i `kjor` tar den.
        vid, tokena = c1.execute(
            "UPDATE varsel SET epost_status='under_sending', epost_forsok=1,"
            " epost_klaim=gen_random_uuid(),"
            " epost_ts=now() - interval '2 hours'"
            " WHERE tenant=%s AND bruker_id=%s"
            " RETURNING id, epost_klaim", (TEN, b)).fetchone()
        c1.commit()
        _kontekst(c1)

        krysset: list[str] = []
        sendt: list[str] = []

        def send_b(til, _emne, _tekst):
            sendt.append(til)
            if til != minadresse or krysset:
                return
            krysset.append(til)
            _kontekst(c1)
            # B holder raden NÅ. Tokenet er et annet enn A sitt — klaimet
            # skriver en fersk uuid hver gang, og re-køingen nullet A sin.
            st, tokenb = c1.execute(
                "SELECT epost_status, epost_klaim FROM varsel WHERE id=%s",
                (vid,)).fetchone()
            assert st == "under_sending", f"raden er {st}, ikke B sitt klaim"
            assert tokenb is not None and tokenb != tokena, (
                "klaimet gjenbrukte tokenet fra det utløpte klaimet — da "
                "skiller ingenting de to fra hverandre")
            # …og her våkner A, midt i B sin sending.
            assert c1.execute(
                "SELECT varsel_sett_epoststatus(%s,%s,'sendt',NULL)",
                (vid, tokena)).fetchone()[0] is False, (
                "et utløpt klaim fullførte erstatterens sending")
            c1.commit()
            _kontekst(c1)
            st, token = c1.execute(
                "SELECT epost_status, epost_klaim FROM varsel WHERE id=%s",
                (vid,)).fetchone()
            assert (st, token) == ("under_sending", tokenb), (
                f"({st}, {token}) — A rørte B sitt klaim likevel")
            c1.commit()

        res = varselsender.kjor(c2, send=send_b)
        _kontekst(c1)

        assert krysset, "krysningen skjedde aldri — testen målte ingenting"
        assert sendt.count(minadresse) == 1, sendt
        assert res["mistet"] == 0, (
            "B mistet sitt eget klaim — fullføringen krever tokenet, og B "
            "hadde det")
        st = c1.execute("SELECT epost_status, epost_klaim, epost_forsok"
                        " FROM varsel WHERE id=%s", (vid,)).fetchone()
        # `sendt`, av B, og tokenet er nullet: raden er ikke klaimet av noen.
        assert st == ("sendt", None, 2), st
    finally:
        c1.close()
        c2.close()


@pg
def test_avmelding_overlever_at_klaimet_kommer_tilbake_fra_en_lease():
    """Codex P2: en gjenopptatt rad arvet ikke avmeldingen.

    `sett_kanal` avlyser hele køen (`I_KO`), men lar en rad som er
    `under_sending` stå — med vilje: den er i et SMTP-kall, og en e-post som
    er ute kan ikke kalles hjem. Døde senderen FØR sendingen, løftet leasen
    raden blindt tilbake til `koet`, og avmeldingen var borte: den hadde alt
    kjørt, og ingen kjører den igjen. Mottakeren fikk e-posten hun sa nei til.

    Rekkefølgen her er nettopp den: krasjen fabrikkeres FØRST, avmeldingen
    kommer ETTERPÅ og ser derfor ikke raden — akkurat som i drift.

    Målt gjennom SENDEREN, ikke bare på kolonnen: det er re-køingen som er den
    ene halvdelen, og klaimet den andre.

    Kontroll: ta CASE-uttrykket ut av `varsel_rekoe`, og raden kommer tilbake
    som `koet` — da bet klaimets eget `varselvalg`-filter i stedet, og
    e-posten går fortsatt ikke ut, men statusen forteller ikke lenger hvorfor.
    """
    c = _conn()
    try:
        b = _bruker(c, "leaseav", "leaseav@example.test")
        _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()
        _kontekst(c)
        # Klaimet som døde, med leasen alt utløpt.
        assert c.execute("UPDATE varsel SET epost_status='under_sending',"
                         " epost_ts=now() - interval '2 hours', epost_forsok=1"
                         " WHERE tenant=%s AND bruker_id=%s RETURNING id",
                         (TEN, b)).fetchall(), "krasjen ble aldri fabrikkert"
        c.commit()
        _kontekst(c)
        # Avmeldingen kommer nå — og skal IKKE røre den klaimede raden.
        varsel.sett_kanal(c, tenant=TEN, bruker_id=b, kanal="kun_portal")
        c.commit()
        _kontekst(c)
        assert c.execute("SELECT epost_status FROM varsel WHERE tenant=%s"
                         " AND bruker_id=%s", (TEN, b)).fetchone()[0] \
            == "under_sending", (
            "forutsetningen holder ikke: avmeldingen tok det aktive klaimet")

        sendt, send = _samler()
        varselsender.kjor(c, send=send)
        _kontekst(c)
        assert [t for t, _e, _x in sendt if t == "leaseav@example.test"] == [], (
            "en avmeldt rad ble sendt etter at leasen løftet den tilbake")
        assert c.execute("SELECT epost_status FROM varsel WHERE tenant=%s"
                         " AND bruker_id=%s", (TEN, b)).fetchone()[0] \
            == "ikke_aktuelt", (
            "raden kom tilbake i køen i stedet for å bli avlyst — den ville"
            " blitt liggende og bli forsøkt igjen ved hver kjøring")
    finally:
        c.close()


@pg
def test_kjoringen_stanser_paa_fristen_og_alltid_mellom_to_rader(monkeypatch):
    """Codex P2: `GRENSE` er et tak i ANTALL, uniten hadde et tak i TID.

    De to hang ikke sammen: 50 SMTP-kall à 20 s sokkeltimeout kan bruke langt
    mer enn `TimeoutStartSec`, så en legitim kjøring ble drept midt i bunken.
    Og det farlige var ikke avbruddet, men HVOR det landet — traff SIGTERM
    mellom et akseptert SMTP-kall og statusoppdateringen, sto raden
    `under_sending` med et dødt klaim, og leasen sendte e-posten om igjen.

    Det som måles er derfor ikke bare AT kjøringen gir seg, men HVOR: hver rad
    skal være enten ferdig eller aldri påbegynt. Ingen rad står igjen
    `under_sending` etter at fristen bet, og ingen rad ble sendt uten at
    resultatet ble skrevet.

    Fristen settes til 0 så den biter etter første runde, og `send` teller —
    ingen klokke å vente på.

    Kontroll: flytt fristsjekken ned etter klaimet, og raden som ble klaimet
    står igjen `under_sending`.
    """
    monkeypatch.setattr(varselsender, "FRIST_S", 0)
    c = _conn()
    try:
        b = _bruker(c, "frist", "frist@example.test")
        for _ in range(3):
            _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()
        sendt, send = _samler()
        res = varselsender.kjor(c, send=send)
        _kontekst(c)

        assert res["stanset"] == "frist", (
            f"kjøringen stanset på {res['stanset']!r}, ikke på fristen")
        assert res["sendt"] == 1 and len(sendt) == 1, (
            f"fristen bet ikke etter første rad: {res}")
        # Det avgjørende: ingenting står igjen halvveis.
        rester = c.execute(
            "SELECT epost_status, count(*) FROM varsel WHERE tenant=%s"
            " AND bruker_id=%s GROUP BY 1 ORDER BY 1", (TEN, b)).fetchall()
        assert dict(rester) == {"koet": 2, "sendt": 1}, (
            f"kjøringen stanset midt i en rad: {rester}")

        # Og resten tas av neste kjøring — køen er tilstanden.
        monkeypatch.setattr(varselsender, "FRIST_S", 240)
        res2 = varselsender.kjor(c, send=send)
        _kontekst(c)
        assert res2["sendt"] == 2 and res2["stanset"] == "tom", res2
        assert len(sendt) == 3
    finally:
        c.close()


@pg
def test_en_lest_rad_som_kommer_tilbake_fra_en_lease_blir_avlyst():
    """Codex P2: en gjenopptatt rad arvet heller ikke LESNINGEN.

    Samme hull som avmeldingen, men denne veien endte ikke i en unødig
    e-post — den endte i en rad som ble liggende for alltid.

    `merk_lest` (og `pensjoner_runde`, som setter `lest_ts` på samme vis)
    avlyser bare `I_KO` og lar `under_sending` stå: raden er i et SMTP-kall.
    Døde senderen før sendingen, løftet leasen den blindt til `koet` — og der
    stanset den. Klaimet krever `lest_ts IS NULL`, så det tok den aldri igjen,
    og re-køingen ser bare `feilet` og `under_sending`, så den kom aldri
    hit heller. Rad, indeksplass og køtall sto der i evighet for en e-post
    ingen skulle sendt.

    Målt over TO kjøringer, for det er nettopp den andre som var beviset:
    den første er der re-køingen skjer, og hadde raden bare blitt `koet`,
    ville den vært like klaimbar-og-avvist i hver eneste kjøring etter det.

    Kontroll: ta `lest_ts IS NOT NULL` ut av CASE-uttrykket i `varsel_rekoe`,
    så står raden `koet` etter begge kjøringene.
    """
    c = _conn()
    try:
        b = _bruker(c, "leaselest", "leaselest@example.test")
        _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()
        _kontekst(c)
        vid = c.execute("SELECT id FROM varsel WHERE tenant=%s AND"
                        " bruker_id=%s", (TEN, b)).fetchone()[0]
        # Klaimet som døde, med leasen alt utløpt.
        assert c.execute("UPDATE varsel SET epost_status='under_sending',"
                         " epost_ts=now() - interval '2 hours', epost_forsok=1"
                         " WHERE id=%s RETURNING id", (vid,)).fetchall(), \
            "krasjen ble aldri fabrikkert"
        c.commit()
        _kontekst(c)
        # Lesningen kommer nå — og skal IKKE røre det klaimede varselet.
        assert varsel.merk_lest(c, tenant=TEN, bruker_id=b, varsel_id=vid)
        c.commit()
        _kontekst(c)
        assert c.execute("SELECT epost_status FROM varsel WHERE id=%s",
                         (vid,)).fetchone()[0] == "under_sending", (
            "forutsetningen holder ikke: lesningen tok det aktive klaimet")

        sendt, send = _samler()
        varselsender.kjor(c, send=send)
        _kontekst(c)
        assert [t for t, _e, _x in sendt if t == "leaselest@example.test"] \
            == [], "et lest varsel ble sendt etter at leasen løftet det tilbake"
        assert c.execute("SELECT epost_status FROM varsel WHERE id=%s",
                         (vid,)).fetchone()[0] == "ikke_aktuelt", (
            "raden kom tilbake som `koet` — der kan verken klaimet"
            " (`lest_ts IS NULL`) eller re-køingen nå den igjen, og den blir"
            " liggende som kø for alltid")

        # Andre kjøring: ingenting å gjøre, og ingenting som endrer seg.
        varselsender.kjor(c, send=send)
        _kontekst(c)
        assert sendt == [], "raden ble sendt ved en senere kjøring"
        assert c.execute("SELECT epost_status FROM varsel WHERE id=%s",
                         (vid,)).fetchone()[0] == "ikke_aktuelt"
    finally:
        c.close()


@pg
def test_klaimet_tar_aldri_en_avmeldt_rad_uansett_hvordan_den_kom_i_koen():
    """Den siste porten før SMTP spør om kanalvalget selv.

    Re-køingen avlyser de radene DEN slipper gjennom, men klaimet kan ikke
    lene seg på at det er den eneste veien inn i `koet`: en rad som havnet der
    på en måte ingen har tenkt på ennå, ville gått rett ut. Samme
    begrunnelse som `lest_ts IS NULL` i klaimets eget filter.

    Avmeldingen skrives her direkte i `varselvalg` og ikke gjennom
    `sett_kanal`, nettopp for å FORBIGÅ oppryddingen: det som måles er
    klaimet alene.

    Kontroll: fjern `NOT EXISTS … kun_portal` fra klaimet, så blir denne rød.
    """
    c = _conn()
    try:
        b = _bruker(c, "klaimav", "klaimav@example.test")
        _ko(c, b, "u-" + secrets.token_hex(4))
        c.execute("INSERT INTO varselvalg (tenant, bruker_id, kanal)"
                  " VALUES (%s,%s,'kun_portal') ON CONFLICT (tenant, bruker_id)"
                  " DO UPDATE SET kanal='kun_portal'", (TEN, b))
        c.commit()
        _kontekst(c)

        sendt, send = _samler()
        varselsender.kjor(c, send=send)
        _kontekst(c)
        assert [t for t, _e, _x in sendt if t == "klaimav@example.test"] == [], (
            "klaimet tok en rad mottakeren har meldt seg av")
        st = c.execute("SELECT epost_status, epost_forsok FROM varsel"
                       " WHERE tenant=%s AND bruker_id=%s",
                       (TEN, b)).fetchone()
        # Urørt: ikke klaimet, og forsøkstelleren ikke brent.
        assert st == ("koet", 0), st
    finally:
        c.close()


@pg
def test_avmelding_stopper_ogsaa_en_rad_som_venter_paa_nytt_forsok():
    """Køen er ikke `koet` alene lenger, og de som AVLYSER en sending må mene
    det samme som den som sender.

    En feilet rad ligger og venter på at backoffen skal løpe ut; da løfter
    `varsel_rekoe` den tilbake til `koet`. En avmelding som bare tok `koet`
    avlyste derfor bare det som tilfeldigvis ikke hadde feilet ennå — og
    e-posten hun nettopp sa nei til gikk ut ved neste timerkjøring likevel.
    Samme kobling som resten av denne runden: to steder som må si det samme,
    og ingenting som bandt dem sammen.

    Målt gjennom SENDEREN, ikke bare på kolonnen: det er re-køingen som er den
    andre halvdelen av koblingen, og en assert på `epost_status` rett etter
    avmeldingen ville stått uendret uansett hva `varsel_rekoe` gjør etterpå.

    Kontroll: sett `varsel.I_KO` tilbake til bare `('koet')`, og denne blir
    rød med en e-post til en avmeldt mottaker.
    """
    c = _conn()
    try:
        b = _bruker(c, "avmeldt", "avmeldt@example.test")
        _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()

        def alltid_feil(til, emne, tekst):
            raise RuntimeError("midlertidig")

        varselsender.kjor(c, send=alltid_feil)
        _kontekst(c)
        # Hun melder seg av MENS raden ligger og venter på nytt forsøk.
        varsel.sett_kanal(c, tenant=TEN, bruker_id=b, kanal="kun_portal")
        c.commit()
        _kontekst(c)
        # …og så løper backoffen ut.
        c.execute("UPDATE varsel SET epost_ts = now() - interval '1 hour'"
                  " WHERE tenant=%s AND bruker_id=%s", (TEN, b))
        c.commit()
        _kontekst(c)

        sendt, send = _samler()
        varselsender.kjor(c, send=send)
        _kontekst(c)
        assert [t for t, _e, _x in sendt
                if t == "avmeldt@example.test"] == [], (
            "e-posten hun meldte seg av fra ble re-køet og sendt likevel")
        st = c.execute("SELECT epost_status FROM varsel WHERE tenant=%s"
                       " AND bruker_id=%s", (TEN, b)).fetchone()
        assert st == ("ikke_aktuelt",), st
        # Varselet står fortsatt i portalen: avmeldingen gjelder kanalen.
        assert varsel.antall_uleste(c, tenant=TEN, bruker_id=b) == 1
    finally:
        c.close()


@pg
def test_lest_i_portalen_gir_ingen_epost():
    """Det vanligste tilfellet: hun sitter i portalen når varselet kommer.

    Timeren går hvert 5. minutt, så vinduet mellom «varselet står i innboksen»
    og «e-posten går ut» er nettopp det vinduet en bruker som er innlogget
    bruker på å lese det. Før fikk hun e-posten likevel — `merk_lest` rørte
    bare `lest_ts`, og raden ble stående `koet`. En e-post om noe hun alt har
    kvittert ut er samme løgn som et varsel om en lukket runde, bare i den
    kanalen hun ikke kan lukke selv.

    Kontroll: ta `epost_status`-leddet ut av `merk_lest`, og denne blir rød på
    at e-posten gikk ut.
    """
    c = _conn()
    try:
        b = _bruker(c, "leser", "leser@example.test")
        _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()
        _kontekst(c)   # `SET LOCAL` døde med committen; uten den ser RLS null
        rader = varsel.innboks(c, tenant=TEN, bruker_id=b)
        assert len(rader) == 1, rader
        vid = rader[0]["id"]
        assert varsel.merk_lest(c, tenant=TEN, bruker_id=b, varsel_id=vid)
        c.commit()
        _kontekst(c)

        sendt, send = _samler()
        varselsender.kjor(c, send=send)
        _kontekst(c)
        assert [t for t, _e, _x in sendt if t == "leser@example.test"] == [], (
            "e-post om et varsel hun alt hadde lest")
        st = c.execute("SELECT epost_status FROM varsel WHERE id=%s",
                       (vid,)).fetchone()
        assert st == ("ikke_aktuelt",), st
    finally:
        c.close()


@pg
def test_en_rad_bak_i_koen_klaimes_ikke_foer_den_skal_sendes():
    """Codex P2: et bunkeklaim tar radene ut av køen for tidlig.

    Klaimet hentet opptil `GRENSE` rader og committet HELE bunken til
    `under_sending` før det første SMTP-kallet. Rad nummer to lå da klaimet
    mens rad én ble sendt — og `merk_lest`, avmeldingen og pensjoneringen rører
    med vilje ikke `under_sending`, siden en e-post som er i luften ikke kan
    kalles hjem. For radene BAK den første var den premissen usann: de var ikke
    i noe SMTP-kall, bare bufret i Python, og de ble sendt likevel.

    Her leser mottakeren av rad to varselet sitt i portalen mens rad én er inne
    i `send()` — nøyaktig det vinduet timeren på fem minutter etterlater. Med
    ett klaim per rad står rad to fortsatt `koet` i det øyeblikket, og
    avlysningen når den.

    Testen sier ikke hvilken av de to som sendes først: begge radene skrives i
    samme transaksjon, så `opprettet` er identisk og FIFO-en er uavgjort. Den
    som kommer først avlyser den andre, og målet er at nøyaktig ÉN e-post går
    ut — det holder uansett hvem av dem det ble.

    Kontroll: bytt klaimet tilbake til `(GRENSE, MAKS_FORSOK)` med en
    `fetchall()` utenfor løkka, så blir denne rød med to sendte e-poster.
    """
    c = _conn()
    annen = _conn()
    try:
        en = _bruker(c, "bunke-en", "bunke-en@example.test")
        to = _bruker(c, "bunke-to", "bunke-to@example.test")
        _ko(c, en, "u-" + secrets.token_hex(4))
        _ko(c, to, "u-" + secrets.token_hex(4))
        c.commit()
        _kontekst(c)
        vid = {b: varsel.innboks(c, tenant=TEN, bruker_id=b)[0]["id"]
               for b in (en, to)}
        andre = {"bunke-en@example.test": to, "bunke-to@example.test": en}

        sendt = []

        def send(til, _emne, _tekst):
            sendt.append(til)
            # Den ANDRE mottakeren leser varselet sitt i portalen mens denne
            # e-posten er inne i SMTP-kallet.
            b = andre[til]
            _kontekst(annen)
            varsel.merk_lest(annen, tenant=TEN, bruker_id=b,
                             varsel_id=vid[b])
            annen.commit()

        varselsender.kjor(c, send=send)
        _kontekst(c)
        assert len(sendt) == 1, (
            "raden bak i køen var alt klaimet da hun leste den, og e-posten "
            f"gikk ut likevel: {sendt}")
        st = c.execute("SELECT epost_status FROM varsel WHERE id=%s",
                       (vid[andre[sendt[0]]],)).fetchone()
        assert st == ("ikke_aktuelt",), st
    finally:
        annen.close()
        c.close()


@pg
def test_klaimet_tar_aldri_en_rad_som_er_lest():
    """Den siste porten før SMTP, uavhengig av hvem som satte `lest_ts`.

    `merk_lest` avlyser sendingen selv, men den kan ikke være hele vernet: den
    når ikke en rad som er `under_sending` (den står i et SMTP-kall), og en rad
    som kommer tilbake fra en utløpt lease etter at den ble lest, ville ellers
    gått rett ut ved neste kjøring. Derfor spør KLAIMET selv.

    Tilstanden fabrikkeres direkte — en `koet`-rad som er lest — for det er
    nettopp den kombinasjonen `merk_lest` ikke lenger produserer. `RETURNING`
    asserter at fabrikkeringen landet: uten tenantkontekst filtrerer RLS bort
    UPDATE-en, og testen ville i stedet målt at en helt vanlig rad blir sendt.

    Kontroll: fjern `k.lest_ts IS NULL` fra `varsel_klaim_epost`, og denne blir
    rød.
    """
    c = _conn()
    try:
        b = _bruker(c, "lest_ko", "lest-ko@example.test")
        _ko(c, b, "u-" + secrets.token_hex(4))
        c.commit()
        _kontekst(c)
        truffet = c.execute(
            "UPDATE varsel SET lest_ts=now() WHERE tenant=%s AND bruker_id=%s"
            " AND epost_status='koet' RETURNING id", (TEN, b)).fetchall()
        assert len(truffet) == 1, "fabrikkeringen landet ikke"
        c.commit()
        _kontekst(c)

        sendt, send = _samler()
        varselsender.kjor(c, send=send)
        _kontekst(c)
        assert [t for t, _e, _x in sendt
                if t == "lest-ko@example.test"] == [], (
            "klaimet tok en rad som var lest")
        st = c.execute("SELECT epost_status, epost_forsok FROM varsel"
                       " WHERE tenant=%s AND bruker_id=%s",
                       (TEN, b)).fetchone()
        # Urørt: ikke klaimet, og forsøkstelleren ikke brent.
        assert st == ("koet", 0), st
    finally:
        c.close()


# ---------------------------------------------------------------------------
# TILLITSGRENSEN (eiers P1): hvem som får KALLE de tre funksjonene.
#
# Funksjonene er smale, men de er en KRYSS-TENANT-kapabilitet:
# `varsel_klaim_epost` er SECURITY DEFINER og returnerer tenant, verifisert
# e-postadresse, tekstnøkkel og parametre for alle tenanters køede varsler.
# RLS verner ikke mot den — omgåelsen er formålet. Da er «hvem kan kalle den»
# like mye av vernet som «hva returnerer den», og det er en egenskap ved
# ACL-en, ikke ved kroppen.
#
# Første utgave grantet alle tre til runtime-rollen `disponit`, fordi unitten
# koblet med runtime-DSN-en. Hele web-API-prosessen hadde dermed evnen for å
# betjene ett oneshot.
# ---------------------------------------------------------------------------

#: Signaturene skrives ut i stedet for å hentes fra katalogen: en test som
#: spør basen hvilke funksjoner 027 laget, ville godtatt at en av dem
#: forsvant.
SENDERFUNKSJONER = [
    "varsel_klaim_epost(int,int)",
    "varsel_sett_epoststatus(bigint,uuid,text,text)",
    "varsel_rekoe(interval,int,interval)",
]

SENDERROLLE = "disponit_varselsender"

EIERROLLE = "disponit_domene_eier"

_KOMMENTAR = re.compile(r"--[^\n]*")
_KROPP = re.compile(r"\$\$.*?\$\$", re.S)


def _setninger(sql):
    """Migrasjonsfilens setninger, uten kommentarer og funksjonskropper.

    Kroppene fjernes fordi de inneholder både `;` og — i kommentarform —
    nettopp de ordene denne testen leter etter. Det som er igjen er filens
    DDL og rettighetsutsagn, i den rekkefølgen basen ser dem.
    """
    for rå in _KROPP.sub(" ", _KOMMENTAR.sub("", sql)).split(";"):
        s = " ".join(rå.split()).lower()
        if s:
            yield s


def test_gjerdet_staar_ved_slutten_av_migrasjonshistorikken():
    """Kildeport: PUBLIC skal ikke ha EXECUTE når siste migrasjon er kjørt.

    En `REVOKE … FROM PUBLIC` kan MISLYKKES STILLE — kjøres den av en rolle
    som ikke eier funksjonen, advarer PostgreSQL og går videre, men
    materialiserer samtidig standard-ACL-en, som for en funksjon er EXECUTE
    for PUBLIC. Og en DROP tar ACL-en med seg, så enhver gjenskaping av en
    alt herdet funksjon åpner gjerdet på nytt.

    Nøyaktig dét skjedde i 028 (Codex P1 på #68): den gjenskapte
    `varsel_klaim_epost` og la REVOKE-en ETTER `ALTER … OWNER TO`, men FØR
    `SET LOCAL ROLE` — altså som migrator, som er medlem av eierrollen
    `WITH INHERIT FALSE`.

    ACL-testen under så det ikke, og kunne ikke se det: både CI og staging
    migrerer med `deploy/staging/migrer.py`, som ETTERPÅ kjører sin egen
    REVOKE som eier. Den målingen skjer på en base der oppryddingen alt har
    lukket hullet. Hullet er likevel ekte i vinduet mellom stegene, og
    permanent for den som kjører `db.kjorer.migrer` direkte.

    Derfor måles filene, og de måles SOM EN HISTORIKK, ikke én for én: 028
    er kjørt og er immutable, så den kan ikke repareres — den kan bare
    etterfølges (030). Testen spiller av alle migrasjonene i rekkefølge og
    krever at gjerdet står igjen til slutt. Neste gjenskaping som glemmer å
    sette det opp igjen, feiler her.
    """
    mig = Path(__file__).resolve().parents[1] / "db/migrations"
    navn = [s.split("(")[0] for s in SENDERFUNKSJONER]
    # None = funksjonen finnes ikke ennå. False = den finnes med åpen ACL.
    gjerdet = dict.fromkeys(navn)
    spor = {n: [] for n in navn}
    for fil in sorted(mig.glob("[0-9][0-9][0-9]_*.sql")):
        rolle = None                      # None = migrator, kjørerens rolle
        for s in _setninger(fil.read_text(encoding="utf-8")):
            if s.startswith("set local role "):
                rolle = s.split()[3]
            elif s.startswith("reset role"):
                rolle = None
            for n in navn:
                if n not in s:
                    continue
                if s.startswith("create ") and " function " in f" {s} ":
                    # Ny eller gjenskapt: ACL-en er standard, altså PUBLIC.
                    gjerdet[n] = False
                    spor[n].append(f"{fil.name}: gjenskapt")
                elif s.startswith("revoke ") and " public" in s:
                    # Som eier: gjerdet står. Som migrator: advarsel, og
                    # standard-ACL-en materialiseres — verre enn ingenting.
                    gjerdet[n] = rolle == EIERROLLE
                    spor[n].append(f"{fil.name}: revoke som {rolle or 'migrator'}")
    for n in navn:
        assert gjerdet[n] is True, (
            f"PUBLIC har EXECUTE på {n} etter siste migrasjon — en"
            f" gjenskaping uten nytt gjerde, eller en REVOKE utenfor"
            f" `SET LOCAL ROLE {EIERROLLE}`. Spor: {spor[n]}")


def _execute_mottakere(conn, signatur):
    """Hvem har EXECUTE på funksjonen — lest av ACL-en, ikke av et kall.

    `aclexplode` på en NULL-ACL gir ingen rader; en funksjon uten eksplisitt
    ACL har standardverdien, altså EXECUTE for PUBLIC. `coalesce` med
    `acldefault` gjør den underforståtte tilstanden synlig, slik at en
    REVOKE som mislyktes stille ikke leses som «ingen har tilgang».
    PUBLIC kommer ut som `-`.
    """
    rader = conn.execute(
        "SELECT a.grantee::regrole::text"
        "  FROM pg_proc p,"
        "       aclexplode(coalesce(p.proacl,"
        "                  acldefault('f', p.proowner))) a"
        " WHERE p.oid = %s::regprocedure AND a.privilege_type='EXECUTE'",
        (signatur,)).fetchall()
    conn.rollback()
    return {r[0] for r in rader}


@pg
def test_bare_senderrollen_har_execute_paa_kryss_tenant_funksjonene():
    """Gjerdet måles på ACL-en, ikke på at et kall lykkes.

    En `REVOKE … FROM PUBLIC` kan MISLYKKES STILLE: kjøres den av en rolle som
    ikke eier funksjonen, advarer PostgreSQL og går videre — men
    materialiserer samtidig standard-ACL-en, som for en funksjon er EXECUTE
    for PUBLIC. Migrator eier ikke disse tre etter `ALTER … OWNER TO`, og
    arver ikke eierrollen (`WITH INHERIT FALSE`). Derfor står REVOKE og GRANT
    i 027 inne i `SET LOCAL ROLE disponit_domene_eier`, og derfor måles
    resultatet her: et privilegium PUBLIC allerede har, feiler aldri i en
    funksjonell test.

    Rollen er et KLYNGEobjekt og finnes ikke i alle baser porten måles i (CI
    speiler ikke `oppsett-postgresql.sh` for denne ennå, og
    `.github/workflows/` kan ikke endres herfra). Å hoppe over testen da ville
    latt grensen stå ubevist i nettopp den basen CI kjører — derfor er den
    delen som gjelder uansett base skrevet uten rollen: INGEN utenom eieren og
    senderrollen skal ha EXECUTE. Finnes senderrollen i tillegg (staging),
    måles den positivt oppå.
    """
    # `disponit_migrator` er med fordi den eier skjemaet og er MEDLEM av
    # eierrollen: den kan `SET ROLE disponit_domene_eier` og kalle funksjonene
    # uansett. Grantet fjerner et `SET ROLE` fra testriggen og gir ingen ny
    # evne. Rollen som IKKE skal stå her er `disponit` — den som betjener
    # HTTP-forespørsler — og det er den asserten under måler eksplisitt.
    tillatt = {"disponit_domene_eier", "disponit_migrator", SENDERROLLE}
    c = _conn()
    try:
        for sig in SENDERFUNKSJONER:
            mottakere = _execute_mottakere(c, sig)
            assert "-" not in mottakere, f"PUBLIC har EXECUTE på {sig}"
            assert "disponit" not in mottakere, (
                f"runtime-rollen har EXECUTE på {sig} — hele web-API-prosessen"
                " kan da lese og endre varselkø på tvers av alle tenanter")
            assert mottakere <= tillatt, \
                f"uventede mottakere av EXECUTE på {sig}: {mottakere - tillatt}"

        finnes = c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s",
                           (SENDERROLLE,)).fetchone()
        c.rollback()
        if not finnes:
            return
        for sig in SENDERFUNKSJONER:
            assert c.execute(
                "SELECT has_function_privilege(%s,%s,'EXECUTE')",
                (SENDERROLLE, sig)).fetchone()[0] is True, \
                f"senderrollen mangler EXECUTE på {sig} — køen ville stått urørt"
        assert c.execute("SELECT has_schema_privilege(%s,'public','USAGE')",
                         (SENDERROLLE,)).fetchone()[0] is True
        c.rollback()
    finally:
        c.close()


@pg
def test_senderrollen_har_ingen_tabellrettigheter():
    """Rollen skal kunne tre funksjoner og INGENTING annet.

    En egen rolle som samtidig hadde SELECT på `varsel` ville vært en
    omskrivning av problemet, ikke en løsning: da lå kryss-tenant-lesningen
    fortsatt åpen — bare uten SECURITY DEFINER-funksjonens avgrensning til
    verifiserte adresser og køede rader. (RLS ville filtrert, men rollen er
    ikke tenant-bundet, og `varselvalg`/`brukeridentitet` er det som
    interesserer en angriper.)
    """
    c = _conn()
    try:
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s",
                         (SENDERROLLE,)).fetchone():
            c.rollback()
            return
        rader = c.execute(
            "SELECT table_name, privilege_type"
            "  FROM information_schema.table_privileges"
            " WHERE grantee=%s", (SENDERROLLE,)).fetchall()
        c.rollback()
        assert rader == [], f"senderrollen har tabellrettigheter: {rader}"
    finally:
        c.close()


@pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
def test_runtime_rollen_naar_ikke_senderens_funksjoner():
    """Og den andre veien, gjennom en ekte forbindelse: API-rollen nektes.

    ACL-testen over leser katalogen; denne kobler som `disponit` — den rollen
    web-API-et faktisk betjener forespørsler med — og krever
    `InsufficientPrivilege` på alle tre. Det er den formen et forsøk fra en
    kompromittert forespørselsvei ville hatt.

    Hver av dem i sin EGEN transaksjon: den første feilen setter forbindelsen
    i `InFailedSqlTransaction`, og da ville de to neste «feilet» uansett
    rettighet — altså vært grønne av feil grunn.

    Kontroll: sett `GRANT EXECUTE … TO disponit` tilbake i 027, og denne blir
    rød.
    """
    import psycopg

    from db.pg import koble
    c = koble(DSN)
    try:
        for sql, argumenter in (
                ("SELECT * FROM varsel_klaim_epost(1, 3)", ()),
                ("SELECT varsel_rekoe(interval '15 minutes', 3,"
                 " interval '30 minutes')", ()),
                ("SELECT varsel_sett_epoststatus(-1, gen_random_uuid(),"
                 " 'sendt', NULL)", ())):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql, argumenter).fetchall()
            c.rollback()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Codex P1 (PR-068): credential-veien er en del av inngangspunktet.
#
# `LoadCredential=` setter `$CREDENTIALS_DIRECTORY` — den setter ingen
# miljøvariabel. Hoppes hydreringen over, ser `main()` aldri DSN-en den
# faktisk HAR fått, og senderen avbryter ved hver eneste timerkjøring. Alle
# testene over gir `kjor()` en ferdig forbindelse og kan derfor ikke se det:
# feilen lå i veien fra unit til prosess, ikke i sendingen. Reviewet ba om
# nettopp den veien målt.
# ---------------------------------------------------------------------------

def test_inngangspunktet_leser_dsn_fra_credential_katalogen(tmp_path,
                                                            monkeypatch):
    """Den faktiske veien: unit → $CREDENTIALS_DIRECTORY → os.environ → DSN.

    Kontroll: fjern `last_credentials()` fra `main()`, og denne dør på
    «DISPONIT_DATABASE_URL mangler» — nøyaktig linjen journalen ville vist.
    """
    import db.pg
    from drift import kjor_varselsender

    (tmp_path / "DISPONIT_DATABASE_URL").write_text("postgresql:///falsk\n",
                                                    encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.delenv("DISPONIT_DATABASE_URL", raising=False)

    sett = {}

    class _Falsk:
        def close(self):
            sett["lukket"] = True

    def _koble(dsn):
        sett["dsn"] = dsn
        return _Falsk()

    monkeypatch.setattr(db.pg, "koble", _koble)
    monkeypatch.setattr(kjor_varselsender.varselsender, "kjor",
                        lambda conn: {"sendt": 0, "feilet": 0,
                                      "gjenkoet": 0})

    assert kjor_varselsender.main() == 0, \
        "senderen avbrøt selv om credentialen lå i katalogen unitten laster"
    assert sett["dsn"] == "postgresql:///falsk", sett
    assert sett.get("lukket"), "forbindelsen ble ikke lukket"


def test_hver_unit_med_loadcredential_hydrerer_dem():
    """Porten, ikke bare fiksen: hvert inngangspunkt en unit starter med
    `LoadCredential=` MÅ kalle `last_credentials()`.

    Tre jobber husket det hver for seg, den fjerde glemte det — og glemselen
    er usynlig overalt utenom under systemd. Da er koblingen unit → hydrering
    det som skal måles, ikke den enkelte jobbens hukommelse.
    """
    import ast
    import re
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    maalt = 0
    for unit in sorted((rot / "deploy/staging").glob("*.service")):
        tekst = unit.read_text(encoding="utf-8")
        if "LoadCredential=" not in tekst:
            continue
        m = re.search(r"^ExecStart=\S+/python -m ([\w.]+)", tekst, re.M)
        if not m:
            continue          # ikke et `python -m`-inngangspunkt (api, cli)
        # Modulen slås opp slik unitten selv slår den opp — WorkingDirectory
        # og PYTHONPATH — så porten følger unitten hvis noen flytter en modul.
        soek = re.findall(r"^(?:WorkingDirectory=|Environment=PYTHONPATH=)"
                          r"(\S+)", tekst, re.M)
        assert soek, f"{unit.name}: verken WorkingDirectory eller PYTHONPATH"
        treff = [p for p in
                 (rot / s.replace("/opt/disponit/aktiv/", "")
                  / (m.group(1).replace(".", "/") + ".py") for s in soek)
                 if p.exists()]
        assert treff, f"{unit.name}: fant ikke {m.group(1)} i {soek}"
        # AST, ikke grep: en docstring som NEVNER hydreringen er ikke en
        # hydrering. Første utgave av porten var grønn på nettopp det.
        kalles = any(
            isinstance(n, ast.Call)
            and getattr(n.func, "id", None) == "last_credentials"
            for n in ast.walk(ast.parse(treff[0].read_text(encoding="utf-8"))))
        assert kalles, (
            f"{unit.name} laster credentials, men {m.group(1)} hydrerer dem "
            "aldri — den avbryter på en miljøvariabel den HAR fått")
        maalt += 1
    assert maalt >= 3, f"porten målte bare {maalt} units — regexen råtnet"
