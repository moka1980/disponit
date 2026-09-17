"""`m57-revisjon-v1` — §6s to hendelser, og døra #159 rev ned.

To halvdeler, og begge må stå:

  1. GRENSEN (artefaktet): minst én signaturhendelse og én
     frigivelseshendelse, null uten identitet, null avskruinger, hver
     frigivelse med SIGNATARENS bruker-id — ikke maskinens — og begge
     triggerne på tabellene. Mutasjonene som feller: en hendelse uten
     bruker, en frigivelse som peker på utsenderen, en avskruing i
     loggen, en manglende trigger.
  2. KODEN (dette treet): avskruingsdøra svarer den kodede 409-en fra
     #159, og evidensen henger i TRIGGERE — ikke i kallsteder. Grensen
     krever null avskruinger nettopp fordi døra er borte; forsvinner den
     kodede avvisningen, er kravet plutselig umulig å tolke.

MUTASJONEN SOM DREPER DENNE: la `blinding_endepunkt` skrive en avskruing
igjen, eller fjern en av triggerne i 211.
"""
from __future__ import annotations

import re
from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = ROT / "platform/core/db/migrations/211_m57_signatur_frigivelse_evidens.sql"


def _art(**over):
    art = {
        "krav_id": "m57-revisjon-v1", "ts": "2026-09-17T21:00:00+00:00",
        "bestatt": True,
        "oppsett": {"modul": "m57_ats", "miljo": "staging",
                    "vert": "disponit-srv", "tenant": "t-m57rev-0a1b2c",
                    "migrasjon": 211},
        "identiteter": {"liste_id": "dd2be4e8-0000-4000-8000-000000000001",
                        "frigivelse_id": "76e42561-0000-4000-8000-000000000002",
                        "signatar_prefiks": "bid_e77e",
                        "signaturhendelse_id": "h-1",
                        "frigivelseshendelse_id": "h-2"},
        "maalt": {"revisjon_signaturer": 1, "revisjon_frigivelser": 1,
                  "revisjon_hendelser_uten_identitet": 0,
                  "revisjon_avskruinger": 0,
                  "signaturer_med_signatarens_identitet": True,
                  "frigivelser_med_signatarens_identitet": True,
                  "trigger_signatur": True, "trigger_frigivelse": True,
                  "hendelser_totalt": 2, "hendelser_er_uforanderlige": True},
    }
    for sti, verdi in over.items():
        del_, felt = sti.split(".")
        art[del_][felt] = verdi
    return art


def test_grensen_finnes_og_binder_revisjonspunktet():
    import manifestskjema as m
    g = m.KRAVGRENSER["m57-revisjon-v1"]
    assert g["min_signaturer"] == 1 and g["min_frigivelser"] == 1
    assert g["maks_uten_identitet"] == 0
    # SNUDD (#159): døra er borte, så kravet er NULL avskruinger — ikke
    # minst én, som den opprinnelige grensen ba om.
    assert g["maks_avskruinger"] == 0
    assert m.ARTEFAKTSKJEMAER["m57-revisjon-v1"] == "artefakt-m57-revisjon-skjema.json"
    assert set(g["punktbinding"]) == {"revisjonslogg_korrekt"}
    for sti in g["punktbinding"]["revisjonslogg_korrekt"]:
        del_, felt = sti.split(".")
        assert felt in _art()[del_], sti


def test_gront_artefakt_bestaar_begge_portene():
    import manifestskjema as m
    art = _art()
    assert m.valider_artefaktformat(art, "m57-revisjon-v1") == []
    assert m._sjekk_grenser("m57-revisjon-v1", art) == []


def test_hver_akse_feller():
    import manifestskjema as m
    for sti, verdi in (("maalt.revisjon_signaturer", 0),
                       ("maalt.revisjon_frigivelser", 0),
                       ("maalt.revisjon_hendelser_uten_identitet", 1),
                       ("maalt.revisjon_avskruinger", 1),
                       ("maalt.signaturer_med_signatarens_identitet", False),
                       ("maalt.frigivelser_med_signatarens_identitet", False),
                       ("maalt.trigger_signatur", False),
                       ("maalt.trigger_frigivelse", False),
                       ("oppsett.tenant", "")):
        assert m._sjekk_grenser("m57-revisjon-v1", _art(**{sti: verdi})), \
            (sti, verdi)
    uten = _art(); del uten["maalt"]["trigger_frigivelse"]
    assert m.valider_artefaktformat(uten, "m57-revisjon-v1") != []
    gammel = _art(**{"oppsett.migrasjon": 210})
    assert m.valider_artefaktformat(gammel, "m57-revisjon-v1") != [], \
        "et artefakt fra før evidenstriggerne skal ikke ha gyldig form"


def test_evidensen_henger_i_triggere_ikke_i_kallsteder():
    """211 legger evidensen på TABELLENE. Et kallsted kan glemmes i den
    neste veien som skriver raden; en trigger kan ikke."""
    sql = MIGRASJON.read_text(encoding="utf-8")
    for tabell, trigger in (("utsendingssignatur", "utsendingssignatur_revisjon"),
                            ("utsendingsfrigivelse", "utsendingsfrigivelse_revisjon")):
        assert re.search(rf"CREATE TRIGGER {trigger}\s+AFTER INSERT ON {tabell}",
                         sql), trigger
    # FUNKSJONENE eies av claimeren (den har INSERT på revisjonshendelse),
    # TRIGGERNE lages av migratoren (tabellenes eier). Bytter man om,
    # feiler hver signatur på «permission denied» — eller migrasjonen.
    for_rolle, etter_rolle = sql.split("SET LOCAL ROLE disponit_m37_claimer;", 1)[1] \
        .split("RESET ROLE;", 1)
    assert "CREATE OR REPLACE FUNCTION m57_signatur_revisjon()" in for_rolle
    assert "CREATE OR REPLACE FUNCTION m57_frigivelse_revisjon()" in for_rolle
    assert "CREATE TRIGGER" not in for_rolle, "trigger lages i feil vindu"
    assert "CREATE TRIGGER utsendingssignatur_revisjon" in etter_rolle
    # Frigivelsens identitet er SIGNATARENS, hentet fra signaturraden.
    assert "FROM public.utsendingssignatur s" in sql
    assert "'m57-utsender'" in sql, "aktøren skiller maskin fra menneske"
    # Hendelsestypene er en LUKKET mengde og utvides i migrasjonen.
    assert "revisjonshendelse_handling_check" in sql
    for h in ("m57.utsendingsliste_signert", "m57.utsending_frigitt",
              "m57.blinding_avskrudd"):
        assert h in sql, h


def test_avskruingsdoren_er_fortsatt_den_kodede_avvisningen():
    """#159: døra ble fjernet, og endepunktet svarer kodet. Grensen
    krever null avskruinger NETTOPP fordi det ikke finnes noen vei til
    én — skrives døra tilbake, er kravet meningsløst."""
    kilde = (ROT / "platform/core/api/rekruttering.py").read_text(encoding="utf-8")
    blokk = kilde.split("def blinding_endepunkt(", 1)[1].split("\ndef ", 1)[0]
    assert '_feil("blinding_avskruing_krever_159", rid, 409)' in blokk
    assert "skriv_revisjonshendelse" not in blokk
    assert "m57.blinding_avskrudd" not in kilde
