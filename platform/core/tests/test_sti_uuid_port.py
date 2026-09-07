"""Porten mot #410: `{id:uuid}` i stien er ALLEREDE en UUID.

Starlettes `UUIDConvertor.convert` gir `request.path_params[navn]` som et
`uuid.UUID`-objekt, og `uuid.UUID(<UUID>)` kaster. Tolv moduler skrev
`uuidlib.UUID(request.path_params[navn])` i sin `_sti_uuid`, og hver
eneste skrivevei med `:uuid` i stien — 52 av dem, «lukk funn», «vurder
tiltak», «avslutt», «måling» — svarte 400 i produksjon (Fjordlys-
kampanjen 7/9, `POST /v1/medarbeider/lop/{lop_id}/steg`).

Enhetstestene fanget det ikke fordi de når handlerne uten Starlettes
konvertor. Denne porten leser derfor KILDEN: hver `_sti_uuid` må gå
gjennom `str(...)`, som de riktige modulene (motpart, lønn, adresse, …)
alltid har gjort. Og den beviser premisset, så ingen «rydder bort» str()
med den begrunnelsen at det ser overflødig ut.

MUTASJONEN SOM DREPER DENNE: fjern `str(` i én `_sti_uuid`.
"""
import re
import uuid
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[1] / "api"


def _sti_uuid_kropper():
    for fil in sorted(API.glob("*.py")):
        kilde = fil.read_text(encoding="utf-8")
        for m in re.finditer(
                r"def _sti_uuid\(request, navn: str, rid\)[^\n]*\n"
                r"((?:    .*\n|\n)*?)(?=\S|\Z)", kilde):
            yield fil.name, m.group(1)


def test_premisset_en_uuid_kan_ikke_pakkes_inn_igjen():
    with pytest.raises((AttributeError, TypeError)):
        uuid.UUID(uuid.uuid4())
    # …mens omveien om str() alltid virker.
    u = uuid.uuid4()
    assert uuid.UUID(str(u)) == u


def test_hver_sti_uuid_gaar_om_str():
    funnet = list(_sti_uuid_kropper())
    assert len(funnet) >= 30, f"porten fant bare {len(funnet)} — regexen er gal"
    gale = [navn for navn, kropp in funnet
            if "UUID(str(" not in kropp]
    assert not gale, (
        "disse modulene pakker path-parameteren inn i uuid.UUID uten str() "
        f"— hver {{id:uuid}}-skrivevei der svarer 400: {gale}")
