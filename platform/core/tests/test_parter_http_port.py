"""Porten for partsregisteret over HTTP (183/184/185, PR 3).

EIERS ORD 11/9: «det skal være en enkel plass der firmaene enten fyller
ut et enkelt skjema om seg selv og legge til deres kunder … Ikke gå
gjennom hver modul og fylle.»

Denne porten måler veien inn: at et menneske kan legge inn en kunde og
et kontaktpunkt på ett sted, at klarteksten ALDRI forlater API-laget, og
at de to scopene faktisk skiller å se fra å endre.
"""
import secrets

from .test_api import (DSN, MIGRATOR_DSN, dekker, klient,  # noqa: F401
                       app, migrator, miljo, pg, token)
from .test_pr012_behandle import conn  # noqa: F401
from .test_m37 import _sett_kontekst


def _les(token):
    t, _ = token(rolle="leser", scopes=("part:read",))
    return t


def _adm(token):
    t, _ = token(rolle="admin", scopes=("part:read", "part:administrer"))
    return t


def _post(klient, tok, sti, kropp):
    return klient.post(sti, json=kropp,
                       headers={"authorization": f"Bearer {tok}",
                                "Idempotency-Key": "p-" + secrets.token_hex(8)})


def _liste(klient, tok, **q):
    r = klient.get("/v1/parter", params=q,
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    return r.json()


@pg
def test_en_kunde_legges_inn_ett_sted_og_referansen_er_identiteten(
        migrator, miljo, klient, token):
    """DET EIER BA OM: ett skjema, én kunde. Og referansen er
    identiteten — en import (PR 4) som kjøres to ganger skal rette
    navnet, ikke lage en tvilling."""
    tok = _adm(token)
    ref = "K-" + secrets.token_hex(3)
    r = _post(klient, tok, "/v1/parter",
              {"part_ref": ref, "navn": "Fjordlys Elektro AS",
               "orgnummer": "912 345 678"})
    assert r.status_code == 200, r.text
    pid = r.json()["part_id"]
    # Orgnummeret tåler mellomrom slik mennesker skriver det.
    rad = [p for p in _liste(klient, tok, sok=ref)["parter"]
           if p["part_ref"] == ref][0]
    assert rad["orgnummer"] == "912345678"
    assert rad["navn"] == "Fjordlys Elektro AS" and rad["aktiv"] is True
    assert rad["antall_kontakter"] == 0
    # Samme referanse, rettet navn: samme kunde.
    r2 = _post(klient, tok, "/v1/parter",
               {"part_ref": ref, "navn": "Fjordlys Elektro AS (rettet)"})
    assert r2.status_code == 200 and r2.json()["part_id"] == pid
    rad = [p for p in _liste(klient, tok, sok=ref)["parter"]
           if p["part_ref"] == ref][0]
    assert rad["navn"] == "Fjordlys Elektro AS (rettet)"
    # FORMFEIL SIER HVA SOM ER GALT, ALDRI HVA BRUKEREN SKREV. Hver sak
    # bærer sin EGEN gjenkjennelige verdi, og porten måler at nettopp den
    # ikke kommer tilbake (CodeRabbit: den sjekket «Fjordlys», et ord som
    # ikke fantes i noen av kroppene — altså ingenting).
    for kropp, detalj, hemmelig in (
            ({"navn": "Hemmelig-A"}, "part_ref", "Hemmelig-A"),
            ({"part_ref": "Hemmelig-B", "navn": "  "}, "navn", "Hemmelig-B"),
            # HEMMELIGHETEN LIGGER I FELTET SOM AVVISES (CodeRabbit):
            # sto den i `navn` mens `orgnummer` var feilen, målte porten
            # at en GYLDIG verdi ikke ble ekkoet — ikke den avviste.
            ({"part_ref": ref, "navn": "X", "orgnummer": "Hemmelig-C"},
             "orgnummer", "Hemmelig-C"),
            ({"part_ref": ref, "navn": "X", "parttype": "Hemmelig-D"},
             "parttype", "Hemmelig-D")):
        r = _post(klient, tok, "/v1/parter", kropp)
        assert r.status_code == 400, (kropp, r.text)
        assert detalj in r.json().get("detalj", ""), (kropp, r.text)
        assert hemmelig not in r.text, (hemmelig, r.text)


@pg
def test_klarteksten_forlater_aldri_api_laget(migrator, miljo, klient, token):
    """058-FORMEN OVER HTTP. Adressen krypteres i API-laget, basen ser
    ciphertext, og det som kommer TILBAKE er en maske. En rå SELECT over
    kontaktraden finner ikke adressen."""
    tok = _adm(token)
    ref = "K-" + secrets.token_hex(3)
    pid = _post(klient, tok, "/v1/parter",
                {"part_ref": ref, "navn": "Fjordlys AS"}).json()["part_id"]
    adresse = f"faktura.{secrets.token_hex(3)}@fjordlys.example"
    r = _post(klient, tok, f"/v1/parter/{pid}/kontakt",
              {"kanal": "epost", "verdi": adresse, "merkelapp": "faktura"})
    assert r.status_code == 200, r.text
    maske = r.json()["maske"]
    assert adresse not in maske and maske.endswith("@fjordlys.example")
    assert "*" in maske
    # Svaret bærer ingen adresse noe sted.
    assert adresse not in r.text
    # Lista heller ikke.
    rad = [p for p in _liste(klient, tok, sok=ref)["parter"]
           if p["part_ref"] == ref][0]
    assert rad["epost_maske"] == maske and rad["antall_kontakter"] == 1
    # ...og basen ser den ikke.
    _sett_kontekst(migrator, "t-api")
    raa = migrator.execute(
        "SELECT count(*) FROM partkontakt WHERE partkontakt::text LIKE %s",
        (f"%{adresse}%",)).fetchone()[0]
    migrator.rollback()
    assert raa == 0, "adressen i klartekst i basen"
    # Samme adresse igjen er SAMME kontaktpunkt.
    r2 = _post(klient, tok, f"/v1/parter/{pid}/kontakt",
               {"kanal": "epost", "verdi": adresse.upper()})
    assert r2.status_code == 200, r2.text
    rad = [p for p in _liste(klient, tok, sok=ref)["parter"]
           if p["part_ref"] == ref][0]
    assert rad["antall_kontakter"] == 1, "store bokstaver ga et nytt punkt"


@pg
def test_aa_se_og_aa_endre_er_to_noekler(migrator, miljo, klient, token):
    """LÆRDOMMEN FRA M-6, brukt før den rakk å bite. Én nøkkel for begge
    ga der en flate som enten skjulte det brukeren hadde lov til, eller
    viste knapper serveren ville nekte. Her er de skilt fra dag én."""
    adm, les = _adm(token), _les(token)
    ref = "K-" + secrets.token_hex(3)
    pid = _post(klient, adm, "/v1/parter",
                {"part_ref": ref, "navn": "Fjordlys AS"}).json()["part_id"]
    # Leseren SER kundene — det er arbeidsgrunnlaget.
    assert any(p["part_ref"] == ref
               for p in _liste(klient, les, sok=ref)["parter"])
    # ...men endrer ingenting.
    for sti, kropp in ((f"/v1/parter", {"part_ref": "K-9", "navn": "X"}),
                       (f"/v1/parter/{pid}/kontakt",
                        {"kanal": "epost", "verdi": "a@b.no"}),
                       (f"/v1/parter/{pid}/deaktiver", {})):
        r = _post(klient, les, sti, kropp)
        assert r.status_code == 403, (sti, r.status_code, r.text)
    # Og uten noe scope i det hele tatt: ingen liste.
    tom, _ = token(rolle="leser", scopes=("decisions:read",))
    assert klient.get("/v1/parter",
                      headers={"authorization": f"Bearer {tom}"}
                      ).status_code == 403


@pg
@dekker("part_ulovlig_tilstand")
def test_avviklingen_er_enveis_og_stenger_for_nye_kontaktpunkter(
        migrator, miljo, klient, token):
    """Avviklingen starter retensjonsklokken (184), og den er enveis.
    Gjenspill er et stille ja. En avviklet kunde tar ikke imot nye
    adresser — det ville vært å fôre et lager som er på vei ut."""
    tok = _adm(token)
    ref = "K-" + secrets.token_hex(3)
    pid = _post(klient, tok, "/v1/parter",
                {"part_ref": ref, "navn": "Fjordlys AS"}).json()["part_id"]
    r = _post(klient, tok, f"/v1/parter/{pid}/deaktiver", {})
    assert r.status_code == 200 and r.json()["ny"] is True, r.text
    # Gjenspill: stille ja, ikke en feil.
    r = _post(klient, tok, f"/v1/parter/{pid}/deaktiver", {})
    assert r.status_code == 200 and r.json()["ny"] is False, r.text
    # Nye kontaktpunkter stoppes av DØRA, som 409 og ikke som drift.
    r = _post(klient, tok, f"/v1/parter/{pid}/kontakt",
              {"kanal": "epost", "verdi": "ny@fjordlys.example"})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "part_ulovlig_tilstand"
    # Kunden står fortsatt i lista, merket avviklet — ikke borte.
    rad = [p for p in _liste(klient, tok, sok=ref)["parter"]
           if p["part_ref"] == ref][0]
    assert rad["aktiv"] is False and rad["navn"] == "Fjordlys AS"
    # En ukjent part er 404, ikke 409.
    ukjent = "11111111-1111-4111-8111-111111111111"
    assert _post(klient, tok, f"/v1/parter/{ukjent}/deaktiver",
                 {}).status_code == 404


@pg
def test_nettleserokten_naar_kundelista_og_ikke_skrivingen(
        conn, miljo, klient):          # noqa: F811
    """DEN VEIEN EIEREN FAKTISK BRUKER — og den var død (CodeRabbit,
    kritisk).

    En browsersesjon (OIDC-kake) får rollen `bruker`, og den måles mot
    `LESESCOPES` i app.py — ALDRI mot `ROLLE_TIL_SCOPES`. `part:read` sto
    ikke der, så `GET /v1/parter` svarte `scope_mangler` for hver eneste
    innlogget bruker, mens alle testene mine gikk med Bearer-token og
    aldri traff veien.

    Porten måler begge halvdeler: at lesingen NÅR fram med en ekte
    øktkake, og at skrivingen fortsatt stoppes uten CSRF — carve-outen
    slipper `part:administrer` forbi den generelle porten, men
    dobbel-innsendingen står igjen.
    """
    from api import sesjon as sesjonmodul

    from .test_pr012_behandle import _medlem
    from .test_pr012_gate14a import _browsersesjon
    bid = _medlem(conn, "kunde-les", roller="ARRAY['leser']")
    cookie, _csrf = _browsersesjon(bid)
    kaker = {sesjonmodul.C_SESJON: cookie}

    r = klient.get("/v1/parter", cookies=kaker)
    assert r.status_code == 200, r.text
    assert "parter" in r.json(), r.json()

    # ...og en leser skriver ingenting, heller ikke fra nettleseren.
    # (Dette er SCOPET som stopper henne, ikke CSRF — se under.)
    r = klient.post("/v1/parter", json={"part_ref": "K-1", "navn": "X"},
                    cookies=kaker,
                    headers={"Idempotency-Key": "b-" + secrets.token_hex(8)})
    assert r.status_code == 403, r.text

    # CSRF MÅLES FOR SEG, med en økt som FAKTISK har skriverett
    # (CodeRabbit: uten det målte porten scope og kalte det CSRF).
    # `part:administrer` er i carve-outen som slipper browsersesjoner
    # forbi «muterer aldri»-porten — dobbel-innsendingen er det ENESTE
    # som står igjen, og den må stå.
    abid = _medlem(conn, "kunde-adm", roller="ARRAY['admin']")
    acookie, acsrf = _browsersesjon(abid)
    akaker = {sesjonmodul.C_SESJON: acookie}
    uten = klient.post(
        "/v1/parter", json={"part_ref": "K-CSRF", "navn": "Uten token"},
        cookies=akaker,
        headers={"Idempotency-Key": "c-" + secrets.token_hex(8)})
    assert uten.status_code == 403, ("uten CSRF slapp gjennom", uten.text)
    # POSITIV KONTROLL: samme økt MED token skriver. Uten den ville
    # porten vært grønn av en økt som ikke kunne skrive uansett.
    med = klient.post(
        "/v1/parter", json={"part_ref": "K-CSRF", "navn": "Med token"},
        cookies={**akaker, sesjonmodul.C_CSRF: acsrf},
        headers={"Idempotency-Key": "c-" + secrets.token_hex(8),
                 "X-Disponit-CSRF": acsrf})
    assert med.status_code == 200, ("med CSRF ble nektet", med.text)


def test_sikkerhet_er_fortsatt_en_supermengde_av_leser():
    """ROLLEMODELLENS EGEN INVARIANT, som ingenting målte.

    `autorisasjon.py` sier det rett ut om `sikkerhet`: «SUPERMENGDE av
    `leser`, og et hull i den containment-en ville vært en endring i
    rollemodellen skjult i en modul-PR». Første utkast av partsregisteret
    var nøyaktig det hullet — `part:read` til `leser`, ikke til
    `sikkerhet` (CodeRabbit). Setningen sto i en kommentar, og en
    kommentar stopper ingenting.

    Porten gjelder ALLE scope, ikke bare partsregisterets: den neste som
    utvider `leser` får den samme røde testen.
    """
    from api.autorisasjon import ROLLE_TIL_SCOPES
    leser = ROLLE_TIL_SCOPES["leser"]
    sikkerhet = ROLLE_TIL_SCOPES["sikkerhet"]
    assert leser, "porten måler ingenting mot en tom leserrolle"
    assert sikkerhet >= leser, (
        "sikkerhet er ikke lenger en supermengde av leser — mangler: "
        + str(sorted(leser - sikkerhet)))


def test_masken_skjuler_alltid_minst_ett_tegn():
    """MASKEN ER DET ENESTE VI LOVER ER TRYGT Å VISE, og første utgave
    viste HELE verdien for korte input (CodeRabbit): et firesifret
    internnummer ble `*1234`, et enbokstavs lokalnavn ble `a*@x.no`.

    Porten går over lengder fra 1 og opp, for begge kanaler. En maske som
    er lik verdien er ingen maske.
    """
    from api.parter import _maske
    for n in range(1, 12):
        tlf = "1" * n
        m = _maske(tlf, "telefon")
        assert m != tlf, (tlf, m)
        assert len(m) == len(tlf), (tlf, m)
        assert "*" in m, (tlf, m)
        lokal = "a" * n
        adr = f"{lokal}@fjordlys.example"
        m = _maske(adr, "epost")
        assert m != adr, (adr, m)
        assert m.endswith("@fjordlys.example"), m
        assert lokal not in m.split("@")[0] or n == 0, (adr, m)
    # Domenet er MED VILJE synlig: det er ikke personopplysningen, og det
    # er det som gjør masken nyttig å lese.
    assert _maske("faktura@fjordlys.example", "epost") \
        == "fa*****@fjordlys.example"
