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

057 §5 (portene 18–19) — KANDIDATDATAGRENSEN, i samme kjøring
-------------------------------------------------------------
`reap_kandidatdata()` (migrasjon 057) var definert, testet og GRANTet til
nettopp denne timerrollen — og aldri kalt fra noen driftsvei (Codex P1).
En rekrutteringsprosess forbi sin 30–365-døgnsfrist beholdt dermed alle
seks kandidatlagre i det uendelige: sletteløftet fantes bare som en
funksjon ingen kjørte.

Den hører hjemme her og ikke i en ny tjeneste: rollen (`disponit_domener`)
er den samme, DSN-en er den samme, og 057 gir EXECUTE til akkurat den
rollen denne enheten alt kjører som. En egen timer hadde vært en ny
deploy-flate for en regel basen alt eier alene.

088 (M-6) — E-POSTDATAGRENSEN, i samme kjøring
----------------------------------------------
`reap_epostdata()` (migrasjon 088) er 057-formens søster for M-6:
meldinger forbi sin 30–365-døgnsfrist (målt fra `mottatt_ts`) får
payloaden tømt i ALLE lagre — kropp, sammendrag, utkast-tekst,
vedleggsnavn — i samme transaksjon per melding. Den kobles inn HER og
ikke i en ny tjeneste, av nøyaktig samme grunn som kandidatdatagrensen:
rollen er den samme, DSN-en er den samme, og 088 gir EXECUTE til akkurat
den rollen denne enheten alt kjører som. 057s Codex P1 («definert,
testet, GRANTet — og aldri kalt») skal ikke gjentas for M-6.

Reapene deler kjøring, men ikke skjebne: hver har sin egen
transaksjon og sitt eget feilflagg, så en feilende evidensfrist-reap ikke
stanser retensjonsarbeidet — og omvendt.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Maks antall oppdrag per kjøring — grensen ligger i den bundne DB-formen
#: (`reap_evidensfrister(p_grense)`), her bare som argumentet vi sender.
BATCHGRENSE = 200

#: Maks antall PROSESSER per kjøring. Egen grense fordi arbeidet er et
#: annet: hver prosess tømmer seks lagre i samme transaksjon, mot
#: evidensfristens ene sak per oppdrag. Speiler DB-formens egen default
#: (`reap_kandidatdata(p_grense INT DEFAULT 50)`).
KANDIDATGRENSE = 50

#: Maks antall MELDINGER per kjøring (088, M-6). Egen grense av samme
#: grunn som kandidatgrensen: hver melding tømmer fire lagre i samme
#: transaksjon. Speiler `reap_epostdata(p_grense INT DEFAULT 50)`.
EPOSTGRENSE = 50


@dataclass
class Reapresultat:
    #: (tenant, oppdrag_id, unntak_id) per lukket oppdrag.
    reapet: list[tuple[str, int, int]] = field(default_factory=list)
    feilet: bool = False
    #: (tenant, prosess_id) per tømt rekrutteringsprosess (057 §5).
    kandidatdata: list[tuple[str, str]] = field(default_factory=list)
    kandidatdata_feilet: bool = False
    #: (tenant, melding_id) per tømt e-postmelding (088, M-6).
    epostdata: list[tuple[str, str]] = field(default_factory=list)
    epostdata_feilet: bool = False


def kjor(conn, *, grense: int = BATCHGRENSE,
         kandidatgrense: int = KANDIDATGRENSE,
         epostgrense: int = EPOSTGRENSE) -> Reapresultat:
    """Én reaperkjøring: evidensfristene først, så kandidatdatagrensen,
    så e-postdatagrensen (088). Overlapp er trygt uten lås i alle tre:
    funksjonene bruker `FOR UPDATE SKIP LOCKED`, så to samtidige
    kjøringer deler kandidatene i stedet for å behandle samme rad to
    ganger."""
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
    # EGEN transaksjon, og den kjøres uansett hvordan den over gikk: to
    # uavhengige retensjonsplikter skal ikke kunne ta hverandre med seg.
    try:
        rader = conn.execute(
            "SELECT tenant, prosess_id"
            "  FROM reap_kandidatdata(%s)", (kandidatgrense,)).fetchall()
        conn.commit()
        r.kandidatdata = [(t, str(p)) for (t, p) in rader]
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        r.kandidatdata_feilet = True
    # 088 (M-6): tredje retensjonsplikt, samme uavhengighet — egen
    # transaksjon, eget feilflagg, kjøres uansett hvordan de to over
    # gikk.
    try:
        rader = conn.execute(
            "SELECT tenant, melding_id"
            "  FROM reap_epostdata(%s)", (epostgrense,)).fetchall()
        conn.commit()
        r.epostdata = [(t, str(m)) for (t, m) in rader]
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        r.epostdata_feilet = True
    return r
