"""Utrullingsplanen per tenant — serverens data, ikke klientens (P1, Codex).

Registeret lå i `platform/core/ui/static/js/plattformdata.js`. Den filen
serveres av `ui_asset` UTEN øktsjekk, og den anonyme landingssiden importerer
den: hvem som helst kunne laste ned modulen (og de offentlige locale-filene)
og lese hver eneste tenants navn, plan, modultildeling og neste steg. At
admin-flaten filtrerte radene i DOM-en hjalp ikke — et DOM-filter er
presentasjon, ikke autorisasjon. Dataene hadde allerede forlatt prosessen.

Derfor bor tabellen her, bak `/v1/utrulling`, og SERVEREN avgjør hva som
sendes ut:

  * en kundeøkt får NØYAKTIG sin egen rad — aldri en annen tenants, uansett
    hva klienten ber om;
  * kontrollplanet på tvers krever `platform:admin`, en autoritet ingen
    kunderolle i `autorisasjon.py` gir (default-deny).

Ingen DB: dette er utrullingsplanen, ikke driftstilstand. `_les`-rammen i
`lesing.py` gir likevel de vanlige portene rundt kallet (401 vs. 403,
tenantkontekst, rollback).
"""
from __future__ import annotations

#: Plattformdriftens autoritet. Skilt fra `security:read`, som er en
#: TENANTBUNDET ops/compliance-scope på en kundesesjon (PR-008 §1) — den sier
#: ingenting om rett til å se andre kunder.
PLATTFORMDRIFT = "platform:admin"

#: Utrullingsplanen. Kundenavn, plan og «neste steg» er DATA, ikke chrome-
#: tekst: de rendres som verdier i flaten og ligger derfor ikke som nøkler i
#: det offentlige locale-settet (`/ui/locale/nb` svarer 200 uten cookie).
#: `moduler` er modul-ID-er, så tildelingen kan slås opp mot modulkatalogen i
#: klienten uten å parses tilbake fra "M-1".
_UTRULLING: tuple[dict, ...] = (
    {"id": "nordvik", "navn": "Nordvik Regnskap AS", "plan": "Pilot",
     "moduler": (1, 2, 37),
     "neste": "M-38 når kapasitet og købevis er grønt."},
    {"id": "bjorkli", "navn": "Bjørkli Elektro", "plan": "Pilot",
     "moduler": (1, 2),
     "neste": "M-37 etter at unntaksrutinene er signert."},
    {"id": "granmo", "navn": "Granmo Driftsselskap", "plan": "Internt",
     "moduler": (1, 2, 37, 38),
     "neste": "Brukes som kunde null for utrulling og intern drift."},
)


def _rad(r: dict) -> dict:
    return {"id": r["id"], "navn": r["navn"], "plan": r["plan"],
            "moduler": list(r["moduler"]), "neste": r["neste"]}


def egen_rad(tenant) -> dict | None:
    """Raden for ÉN tenant, eller None når vi ikke kjenner den. None betyr
    «vet ikke», ikke «ingen moduler»: en flate som ikke vet, skal si det."""
    navn = str(tenant or "").strip().lower()
    if not navn:
        return None
    for r in _UTRULLING:
        if r["id"] == navn:
            return _rad(r)
    return None


def svar_for(tenant, scopes) -> dict:
    """Svaret for én økt. REN funksjon — den er hele autorisasjonsregelen for
    hva som forlater serveren, og testes uten DB.

    Uten `platform:admin` inneholder `tenanter` maksimalt økten sin EGEN rad.
    Klienten får dermed aldri en rad den ikke skal se, og trenger ikke gjøre
    et filter vi må stole på.
    """
    plattformdrift = PLATTFORMDRIFT in set(scopes or ())
    egen = egen_rad(tenant)
    if plattformdrift:
        tenanter = [_rad(r) for r in _UTRULLING]
    else:
        tenanter = [egen] if egen else []
    # `moduler` er tenantens EGEN tildeling — også for plattformdrift, som
    # ellers ville sett hele katalogen på sin egen kundeflate.
    return {"plattformdrift": plattformdrift,
            "moduler": egen["moduler"] if egen else None,
            "tenanter": tenanter}
