"""`m57-feilinjisering-v1` — porten står FØR injiseringen (§0).

SP-3 på M-57: en giftig bunt (et arkivmedlem over komprimeringsgrensen)
skal gi et RENT feilutfall — kvittering `feilet` med kode, oppdraget
`feilet`, ingen promotert liste — og køen skal få nøyaktig posten som er
bundet til jobben (`sak_for_oppdrag`, årsak utforelse_feilet). Mutasjonene
som feller: en jobb som ikke feilet, en feil uten kode, en promotert
liste, ingen køpost, en køpost uten jobbinding, en post med feil
årsak/kilde, en post som peker på en annen jobb.
"""
from __future__ import annotations


def _art(**over):
    art = {
        "krav_id": "m57-feilinjisering-v1", "ts": "2026-09-17T17:00:00+00:00",
        "bestatt": True,
        "oppsett": {"modul": "m57_ats", "vert": "disponit-srv",
                    "tenant": "t-m57fasit",
                    "gift": "zip-medlem 8 MiB nuller (forhold > 100:1)",
                    "release": "m57-drill-k-x"},
        "maalt": {"injisert_jobber": 1, "injisert_oppdrag_id": 150,
                  "oppdrag_status": "feilet", "kvittering_resultat": "feilet",
                  "feilkode": "kjoring_avbrutt", "promoterte_artefakter": 0,
                  "unntakskoe_poster": 1, "koeposter_uten_jobbinding": 0,
                  "sak_id": 170, "sak_oppdrag_id": 150, "sak_sakskilde": "oppdrag",
                  "sak_arsak": "utforelse_feilet", "sak_status": "ny",
                  "feilet_etter_s": 21.5},
    }
    for sti, verdi in over.items():
        del_, felt = sti.split(".")
        art[del_][felt] = verdi
    return art


def test_grensen_finnes_og_binder_punktet():
    import manifestskjema as m
    g = m.KRAVGRENSER["m57-feilinjisering-v1"]
    assert g["min_injiserte_jobber"] == 1 and g["min_unntakskoe_poster"] == 1
    assert g["maks_koeposter_uten_jobbinding"] == 0
    assert g["maks_promoterte_artefakter"] == 0
    assert m.ARTEFAKTSKJEMAER["m57-feilinjisering-v1"] == "artefakt-m57-feilinjisering-skjema.json"
    assert set(g["punktbinding"]) == {"feilinjisering_til_unntakskø"}


def test_gront_artefakt_bestaar_begge_portene():
    import manifestskjema as m
    art = _art()
    assert m.valider_artefaktformat(art, "m57-feilinjisering-v1") == []
    assert m._sjekk_grenser("m57-feilinjisering-v1", art) == []


def test_hver_akse_feller():
    import manifestskjema as m
    for sti, verdi in (("maalt.injisert_jobber", 0),
                       ("maalt.oppdrag_status", "utfort"),
                       ("maalt.kvittering_resultat", "utfort"),
                       ("maalt.feilkode", ""),
                       ("maalt.promoterte_artefakter", 1),
                       ("maalt.unntakskoe_poster", 0),
                       ("maalt.koeposter_uten_jobbinding", 1),
                       ("maalt.sak_sakskilde", "policybrudd"),
                       ("maalt.sak_arsak", "evidensfrist"),
                       ("maalt.sak_oppdrag_id", 151)):
        assert m._sjekk_grenser("m57-feilinjisering-v1", _art(**{sti: verdi})), \
            (sti, verdi)
    uten = _art(); del uten["maalt"]["sak_arsak"]
    assert m.valider_artefaktformat(uten, "m57-feilinjisering-v1") != []
