"""Kjøringen (klarsignalet §7): porsjonsvis gjennom bunten, evaluering
per kandidat — og AVBRUTT KJØRING PROMOTERER INGENTING (port 28).

Kontrakten er SP-3s: ett rent utfall. Enten kommer HELE resultatet
(hver kandidat evaluert, listeutkastene bygget), eller så kommer et
kodet feilutfall uten noe resultat i det hele tatt — det finnes ingen
vei ut av denne fila med en halv liste. Gjenopptak er en NY bestilling;
delresultater holdes aldri varme.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import evaluering, parsing


@dataclass(frozen=True)
class Kjoringsfeil(Exception):
    """Det rene feilutfallet: koden + hvor langt kjøringen kom.

    Fremdriften er EVIDENS (hvor mange filer/kandidater som var lest da
    det røk), aldri et delresultat — det finnes ikke noe felt her som
    kan bære en kandidatliste.
    """

    kode: str
    fremdrift: dict = field(default_factory=dict)

    def __str__(self):
        return f"{self.kode} ({self.fremdrift})"


def kjor_bunt(sti, modell, *, vekter, kandidatfelter_for,
              biasmaalinger, blinding_av=False, auditrad=None):
    """-> {"rangering": [...], "artefakter": {kandidat_id: ...},
    "fremdrift": {...}} — eller Kjoringsfeil, aldri noe imellom.

    `kandidatfelter_for(medlem)` er innslaget fra den strukturerte
    søknaden (blindingens kilde); kandidat-id er medlemsstiens første
    ledd (én mappe per kandidat, m56-fasitformen).
    """
    artefakter: dict[str, dict] = {}
    oppfylt: dict[str, dict] = {}
    fremdrift: dict = {"filer_lest": 0, "filer_totalt": 0, "byte_lest": 0}
    try:
        for merke, medlem, data in parsing.les_porsjonsvis(sti):
            if merke:
                fremdrift = merke
            kandidat_id = medlem.navn.replace("\\", "/").split("/")[0]
            tekst = data.decode("utf-8", errors="replace")
            resultat = evaluering.evaluer_kandidat(
                modell, tekst, kandidatfelter_for(medlem), vekter,
                biasmaalinger=biasmaalinger,
                blinding_av=blinding_av, auditrad=auditrad)
            artefakter[kandidat_id] = resultat
            oppfylt[kandidat_id] = resultat["oppfylt"]
    except parsing.Buntfeil as feil:
        raise Kjoringsfeil(feil.kode, fremdrift) from feil
    except evaluering.Evalueringsfeil as feil:
        raise Kjoringsfeil(feil.kode, fremdrift) from feil
    except Exception as feil:   # modellen er fremmed kode — også dens
        raise Kjoringsfeil("modellfeil", fremdrift) from feil
    return {"rangering": evaluering.ranger(oppfylt, vekter),
            "artefakter": artefakter, "fremdrift": fremdrift}
