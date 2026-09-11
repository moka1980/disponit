"""Porten for kundeimporten (PR 4 av partsregisteret).

EIERS ORD 11/9: «Ikke gå gjennom hver modul og fylle, så hva blir vitsen
hvis alt må fylles manuelt». Registeret ga ett sted, flaten ga en vei inn
for én kunde. Denne måler veien inn for alle sammen.

Tre ting porten holder fast:
  * skrivingen går gjennom DØRENE, ikke forbi dem;
  * tørrkjøring er standard — å skrive er noe man ber om;
  * feilrapporten bærer linjenummer og en KODE, aldri innholdet i cella.
"""
import secrets

from .test_api import (DSN, MIGRATOR_DSN, klient,  # noqa: F401
                       app, migrator, miljo, pg, token)
from .test_m37 import _sett_kontekst


def _adm(token):
    t, _ = token(rolle="admin", scopes=("part:read", "part:administrer"))
    return t


def _post(klient, tok, sti, kropp):
    return klient.post(sti, json=kropp,
                       headers={"authorization": f"Bearer {tok}",
                                "Idempotency-Key": "i-" + secrets.token_hex(8)})


def _importer(klient, tok, csv_tekst, torr=True):
    r = _post(klient, tok, "/v1/parter/import",
              {"csv": csv_tekst, "torrkjoring": torr})
    assert r.status_code == 200, r.text
    return r.json()


def _liste(klient, tok, sok=None):
    r = klient.get("/v1/parter", params={"sok": sok} if sok else {},
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    return r.json()["parter"]


@pg
def test_torrkjoring_er_standard_og_skriver_ingenting(migrator, miljo,
                                                      klient, token):
    """Å SKRIVE ER NOE MAN BER OM. En kaller som glemmer flagget skal få
    en RAPPORT, ikke tusen rader i registeret."""
    tok = _adm(token)
    pre = "K" + secrets.token_hex(3)
    csv_tekst = (f"kundenummer;navn;organisasjonsnummer\n"
                 f"{pre}-1;Fjordlys Elektro AS;912345678\n"
                 f"{pre}-2;Nordlys Bygg AS;\n")
    # Uten flagget i det hele tatt.
    r = _post(klient, tok, "/v1/parter/import", {"csv": csv_tekst})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["torrkjoring"] is True and d["skrevet"] == 0
    assert d["lest"] == 2 and d["gyldige"] == 2 and d["feil"] == []
    assert d["skilletegn"] == ";"
    assert not _liste(klient, tok, pre), "tørrkjøringen skrev likevel"
    # ...og med flagget uttrykkelig satt.
    d = _importer(klient, tok, csv_tekst, torr=True)
    assert d["skrevet"] == 0
    assert not _liste(klient, tok, pre)


@pg
def test_importen_skriver_gjennom_doerene_og_er_idempotent(migrator, miljo,
                                                           klient, token):
    """SKRIVINGEN GÅR GJENNOM `part_registrer`/`part_sett_kontakt`, ikke
    en rask INSERT. Porten måler det på VIRKNINGEN: dørene er idempotente
    på referansen og på adressen, så en import kjørt to ganger gir samme
    antall kunder og samme antall kontaktpunkter."""
    tok = _adm(token)
    pre = "K" + secrets.token_hex(3)
    csv_tekst = (f"kundenummer,navn,orgnr,epost,telefon\n"
                 f"{pre}-1,Fjordlys Elektro AS,912345678,"
                 f"faktura@fjordlys.example,+4712345678\n"
                 f"{pre}-2,Nordlys Bygg AS,,post@nordlys.example,\n")
    d = _importer(klient, tok, csv_tekst, torr=False)
    assert d["skrevet"] == 2 and d["skilletegn"] == ","
    rader = {p["part_ref"]: p for p in _liste(klient, tok, pre)}
    assert set(rader) == {f"{pre}-1", f"{pre}-2"}
    assert rader[f"{pre}-1"]["orgnummer"] == "912345678"
    assert rader[f"{pre}-1"]["antall_kontakter"] == 2
    assert rader[f"{pre}-2"]["antall_kontakter"] == 1
    # ADRESSEN ER MASKERT, aldri hel — samme løfte som flaten.
    assert rader[f"{pre}-1"]["epost_maske"].endswith("@fjordlys.example")
    assert "faktura@fjordlys.example" not in str(rader)
    # KJØRT TO GANGER: ingen tvillinger, ingen doble kontaktpunkter.
    d2 = _importer(klient, tok, csv_tekst, torr=False)
    assert d2["skrevet"] == 2
    rader2 = {p["part_ref"]: p for p in _liste(klient, tok, pre)}
    assert len(rader2) == 2, "importen laget tvillinger"
    assert rader2[f"{pre}-1"]["antall_kontakter"] == 2
    # ...og et rettet navn RETTES, uten en ny rad.
    d3 = _importer(klient, tok,
                   f"kundenummer,navn\n{pre}-1,Fjordlys Elektro AS (rettet)\n",
                   torr=False)
    assert d3["skrevet"] == 1
    rader3 = {p["part_ref"]: p for p in _liste(klient, tok, pre)}
    assert len(rader3) == 2
    assert rader3[f"{pre}-1"]["navn"] == "Fjordlys Elektro AS (rettet)"


@pg
def test_rapporten_peker_paa_linja_og_siterer_aldri_cella(migrator, miljo,
                                                          klient, token):
    """«Importen feilet» på 800 rader er ubrukelig. «Linje 412:
    organisasjonsnummer er ikke ni siffer» kan rettes.

    OG RAPPORTEN SITERER ALDRI INNHOLDET: en importfil bærer
    personopplysninger i hver rad, og en feilmelding som gjentok dem
    ville lagt dem i svaret, i loggen og på skjermen.
    """
    tok = _adm(token)
    pre = "K" + secrets.token_hex(3)
    csv_tekst = (
        "kundenummer;navn;organisasjonsnummer;epost\n"
        f"{pre}-1;Gyldig AS;912345678;ok@fjordlys.example\n"
        ";Hemmelig-Navn;;\n"                       # linje 3: uten nummer
        f"{pre}-3;;;\n"                            # linje 4: uten navn
        f"{pre}-4;Feil orgnr AS;12345;\n"          # linje 5: orgnr
        f"{pre}-5;Feil epost AS;;hemmelig-adresse\n"   # linje 6: e-post
        f"{pre}-1;Gjentatt AS;;\n")                # linje 7: dublett
    d = _importer(klient, tok, csv_tekst)
    assert d["lest"] == 6 and d["gyldige"] == 1
    per_linje = {f["linje"]: f["grunn"] for f in d["feil"]}
    assert per_linje == {3: "kundenummer_mangler", 4: "navn_mangler",
                         5: "orgnummer_ikke_ni_siffer",
                         6: "epost_ugyldig",
                         7: "kundenummer_gjentatt_i_fila"}, per_linje
    # INGEN CELLEVERDIER I SVARET.
    raa = str(d)
    for hemmelig in ("Hemmelig-Navn", "hemmelig-adresse", "12345",
                     "Gjentatt AS", "Feil orgnr AS"):
        assert hemmelig not in raa, (hemmelig, raa)
    # Hver grunn er en KODE, ikke en setning.
    for f in d["feil"]:
        assert f["grunn"].replace("_", "").isalnum() \
            and f["grunn"].islower(), f


@pg
def test_linjenummeret_er_filas_ogsaa_med_blanke_linjer(migrator, miljo,
                                                        klient, token):
    """DEN OPPRINNELIGE FEILEN, festet (CodeRabbit): tomme linjer ble
    kastet FØR nummereringen, så en fil med en blank linje på toppen ga
    «linje 411» om feilen på linje 412 — og en rapport som peker på feil
    linje er verre enn ingen rapport, for brukeren retter feil rad.

    Fila under har blanke linjer FØR overskriften, MELLOM radene og til
    SLUTT. Feilen står på fysisk linje 7, og porten krever det tallet.
    """
    tok = _adm(token)
    pre = "K" + secrets.token_hex(3)
    csv_tekst = (
        "\n"                                    # 1: blank
        "\n"                                    # 2: blank
        "kundenummer;navn;organisasjonsnummer\n"  # 3: overskrift
        f"{pre}-1;Gyldig AS;912345678\n"          # 4
        "\n"                                    # 5: blank
        f"{pre}-2;Ogsaa gyldig AS;\n"             # 6
        f"{pre}-3;Feil AS;123\n"                  # 7: FEILEN
        "\n")                                   # 8: blank
    d = _importer(klient, tok, csv_tekst)
    assert d["lest"] == 3 and d["gyldige"] == 2, d
    assert d["feil"] == [{"linje": 7, "grunn": "orgnummer_ikke_ni_siffer"}], d


@pg
def test_ukjente_kolonner_stopper_ikke_importen_men_navngis(migrator, miljo,
                                                            klient, token):
    """En import som stoppet på en ekstra kolonne fra kundens eget
    regneark ville vært ubrukelig i praksis. Men brukeren skal VITE at
    noe ble ignorert — ellers tror hun at «Kontaktperson» kom med."""
    tok = _adm(token)
    pre = "K" + secrets.token_hex(3)
    d = _importer(klient, tok,
                  "Kundenummer;Navn;Kontaktperson;Vår referanse\n"
                  f"{pre}-1;Fjordlys AS;Kari Nordmann;intern-42\n")
    assert d["gyldige"] == 1 and d["feil"] == []
    assert set(d["ukjente_kolonner"]) == {"Kontaktperson", "Vår referanse"}
    # ...og de ignorerte VERDIENE står ikke i rapporten.
    assert "Kari Nordmann" not in str(d) and "intern-42" not in str(d)


@pg
def test_overskriftslinja_maa_ha_de_to_obligatoriske(migrator, miljo,
                                                     klient, token):
    """Uten kundenummer og navn er det ikke en kundefil. Feilen sier HVA
    som mangler — ikke «ugyldig fil»."""
    tok = _adm(token)
    for csv_tekst, ord_ in (("navn;epost\nFjordlys AS;a@b.no\n", "kundenummer"),
                            ("kundenummer;epost\nK-1;a@b.no\n", "navn")):
        r = _post(klient, tok, "/v1/parter/import", {"csv": csv_tekst})
        assert r.status_code == 400, r.text
        assert ord_ in r.json().get("detalj", ""), r.text
    # Tom kropp og tom fil sier hver sin ting.
    assert _post(klient, tok, "/v1/parter/import",
                 {"csv": "   "}).status_code == 400
    assert _post(klient, tok, "/v1/parter/import", {}).status_code == 400


@pg
def test_importen_er_alt_eller_ingenting(migrator, miljo, klient, token):
    """En import som skrev de 700 første og stoppet på rad 701 ville
    etterlatt registeret i en tilstand brukeren ikke ba om.

    Prøven: en fil der siste rad peker på en AVVIKLET kunde, som dørene
    nekter et nytt kontaktpunkt (186). Ingen av de andre radene skal
    overleve.
    """
    tok = _adm(token)
    pre = "K" + secrets.token_hex(3)
    # En avviklet kunde, lagt inn og avviklet gjennom flatens egne ruter.
    pid = _post(klient, tok, "/v1/parter",
                {"part_ref": f"{pre}-avviklet",
                 "navn": "Avviklet AS"}).json()["part_id"]
    assert _post(klient, tok, f"/v1/parter/{pid}/deaktiver",
                 {}).status_code == 200
    for_ = {p["part_ref"] for p in _liste(klient, tok, pre)}
    r = _post(klient, tok, "/v1/parter/import",
              {"csv": (f"kundenummer;navn;epost\n"
                       f"{pre}-ny1;Ny AS;ny1@fjordlys.example\n"
                       f"{pre}-ny2;Ny to AS;ny2@fjordlys.example\n"
                       f"{pre}-avviklet;Avviklet AS;ny3@fjordlys.example\n"),
               "torrkjoring": False})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "part_ulovlig_tilstand"
    etter = {p["part_ref"] for p in _liste(klient, tok, pre)}
    assert etter == for_, ("halve importen overlevde", etter - for_)


@pg
def test_en_leser_importerer_ingenting(migrator, miljo, klient, token):
    tok, _ = token(rolle="leser", scopes=("part:read",))
    r = _post(klient, tok, "/v1/parter/import",
              {"csv": "kundenummer;navn\nK-1;X\n", "torrkjoring": False})
    assert r.status_code == 403, r.text
