"""Porten mot #425: et skjemanekt navngir feltet.

Etter #415 forklarer et DØRNEKT seg. Et SKJEMANEKT — feltvalideringen
før døra — svarte fortsatt `400 request_feilformet` uten ett ord om
hvilket felt (Fjordlys-kampanjen 7–8/9: `avmeldingslenke`,
`kunnskapsref`, `linje_ts`, `helseopplysninger`, `formaal`, …).

Parserhjelperne (`_tekst`, `_valg`, `_heltall`, …) finnes som kopier i
42 moduler og kjenner feltnavnet og regelen. De sender nå `detalj` som
navngir feltet — og for `_valg` de lovlige verdiene — ALDRI verdien
(den kan være PII).

Tre HTTP-rundturer mot ekte base, og et kildeskann som feller nye
nakne `request_feilformet` i en hjelper med feltparameter.

MUTASJONEN SOM DREPER DENNE: fjern `detalj=` i én hjelper.
"""
import ast
import secrets
from pathlib import Path

from .test_api import (DSN, MIGRATOR_DSN, TENANT, pg,  # noqa: F401
                       app, klient, migrator, miljo, token)

API = Path(__file__).resolve().parents[1] / "api"


def _post(klient, tok, sti, kropp):
    return klient.post(sti, json=kropp,
                       headers={"authorization": f"Bearer {tok}",
                                "Idempotency-Key": secrets.token_urlsafe(24)})


@pg
def test_manglende_felt_navngis(miljo, migrator, klient, token):
    tok, _ = token(rolle="bestiller", scopes=("bestilling:opprett",))
    r = _post(klient, tok, "/v1/kampanje/kampanje",
              {"ekstern_ref": "K-425", "navn": "Uten lenke",
               "formal": "test", "planlagt_sendt": "2026-10-01"})
    assert r.status_code == 400, r.text
    k = r.json()
    assert k["feil"] == "request_feilformet"
    assert "avmeldingslenke" in k.get("detalj", ""), k


@pg
def test_ugyldig_valg_navngir_feltet_og_de_lovlige(miljo, migrator, klient,
                                                    token):
    tok, _ = token(rolle="bestiller", scopes=("bestilling:opprett",))
    r = _post(klient, tok, "/v1/anbud/registrer",
              {"ekstern_ref": "A-425", "kilde": "avisannonse",
               "tittel": "T", "oppdragsgiver": "O", "nace_kode": "43.21",
               "geografi": "Troms", "frist": "2026-10-01T12:00:00+02:00"})
    assert r.status_code == 400, r.text
    d = r.json().get("detalj", "")
    assert "kilde" in d and "doffin" in d, d
    assert "avisannonse" not in d, "verdien lekket ut i detalj"


@pg
def test_tall_utenfor_grensene_navngir_feltet(miljo, migrator, klient, token):
    tok, _ = token(rolle="bestiller", scopes=("bestilling:opprett",))
    r = _post(klient, tok, "/v1/hms/krav",
              {"oppbevaring_maks_dogn": 1, "oppbevaringsvarsel_dogn": 30,
               "tiltaksfrist_dogn": 14, "regelvarsel_dogn": 60})
    assert r.status_code == 400, r.text
    assert "oppbevaring_maks_dogn" in r.json().get("detalj", ""), r.text


def test_ingen_hjelper_med_feltparameter_nekter_uten_detalj():
    nakne = []
    for fil in sorted(API.glob("*.py")):
        tre = ast.parse(fil.read_text(encoding="utf-8"))
        for fn in tre.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            args = [a.arg for a in fn.args.args]
            if len(args) < 2 or args[0] not in ("kropp", "rad", "k") \
                    or args[1] not in ("felt", "navn", "nokkel"):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call)
                        and getattr(node.func, "id", "") == "_feil"
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == "request_feilformet"
                        and not any(k.arg == "detalj"
                                    for k in node.keywords)):
                    nakne.append((fil.name, fn.name, node.lineno))
    assert not nakne, nakne
