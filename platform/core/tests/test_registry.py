"""Registertester: bevis for at moduler kan legges til/fjernes trygt."""
from pathlib import Path

from .conftest import REPO_ROOT, CORE
import sys
sys.path.insert(0, str(CORE))
from registry import Modul, les_manifester, valider  # noqa: E402


def test_oppdager_m01_fra_manifest():
    moduler = les_manifester(REPO_ROOT / "platform" / "modules")
    ider = [m.id for m in moduler]
    assert "m01_policy" in ider


def test_aktiv_modul_med_inaktiv_avhengighet_blokkeres():
    moduler = [
        Modul(id="m01", navn="Policy", versjon="1", status="inaktiv"),
        Modul(id="m14", navn="Faktura", versjon="1", status="aktiv",
              avhengigheter=["m01"]),
    ]
    status = valider(moduler)
    assert status.feil and "m14" in status.feil[0]


def test_fjernet_modul_pavirker_ikke_andre():
    # m14 fjernet helt — m01 og m06 uten kobling til m14 er fortsatt gyldige
    moduler = [
        Modul(id="m01", navn="Policy", versjon="1", status="aktiv"),
        Modul(id="m06", navn="Epost", versjon="1", status="aktiv",
              avhengigheter=["m01"]),
    ]
    status = valider(moduler)
    assert status.feil == []
    assert status.aktive == ["m01", "m06"]


def test_duplisert_id_avvises():
    moduler = [Modul(id="x", navn="", versjon="1", status="aktiv"),
               Modul(id="x", navn="", versjon="1", status="aktiv")]
    assert valider(moduler).feil
