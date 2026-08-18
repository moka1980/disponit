"""038 §5 (port 23) — evidensfrist-reaperen for beslutningsoppdrag.

`disponit-evidensreaper.timer` kaller `reap_evidensfrister()` (migrasjon
038). Timeren legger INGEN logikk oppå regelen — samme snitt som
artefaktryddingen: databasen eier hele avgjørelsen (hvilke oppdrag, hvilken
sak, hvilken status), arbeideren har EXECUTE på nøyaktig én funksjon.

Det som skjer per kandidat, i ÉN transaksjon i basen:

  * `sikre_sak_for_oppdrag(tenant, id, 'evidensfrist', …)` — idempotent:
    gjentatte kjøringer finner samme ikke-terminale sak (port 25), en
    terminal sak gjenbrukes aldri (port 26);
  * oppdraget settes `feilet` UTEN kvittering — plattformens «ufullført»:
    lese-API-et viser nøyaktig den kombinasjonen som `feil_aarsak: timeout`;
  * `oppdrag.unntak_id` forblir NULL (port 27) — saken peker på oppdraget.

M-37-oppdrag røres aldri (`opprinnelse = 'beslutning'`-filteret ligger i
funksjonen) — det er regresjonsporten, ikke en utelatelse.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Maks antall oppdrag per kjøring — grensen ligger i den bundne DB-formen
#: (`reap_evidensfrister(p_grense)`), her bare som argumentet vi sender.
BATCHGRENSE = 200


@dataclass
class Reapresultat:
    #: (tenant, oppdrag_id, unntak_id) per lukket oppdrag.
    reapet: list[tuple[str, int, int]] = field(default_factory=list)
    feilet: bool = False


def kjor(conn, *, grense: int = BATCHGRENSE) -> Reapresultat:
    """Én reaperkjøring. Overlapp er trygt uten lås her: funksjonen bruker
    `FOR UPDATE SKIP LOCKED`, så to samtidige kjøringer deler kandidatene
    i stedet for å behandle samme oppdrag to ganger."""
    r = Reapresultat()
    try:
        rader = conn.execute(
            "SELECT tenant, oppdrag_id, unntak_id"
            "  FROM reap_evidensfrister(%s)", (grense,)).fetchall()
        conn.commit()
        r.reapet = [(t, o, u) for (t, o, u) in rader]
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        r.feilet = True
    return r
