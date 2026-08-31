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

#: Språkene flaten kan be om. Samme mengde som `SPRAK` i `i18n.js`; `nb` er
#: reserven, så en ny locale i klienten aldri gir en tom celle.
SPRAK: tuple[str, ...] = ("nb", "en")
RESERVESPRAK = "nb"

#: Utrullingsplanen. Kundenavn, plantildeling og «neste steg» er DATA, ikke
#: chrome-tekst: de rendres som verdier i flaten og ligger derfor ikke som
#: nøkler i det offentlige locale-settet (`/ui/locale/nb` svarer 200 uten
#: cookie). `moduler` er modul-ID-er, så tildelingen kan slås opp mot
#: modulkatalogen i klienten uten å parses tilbake fra "M-1".
#:
#: To felter er språksatt, og på hver sin måte — fordi de er hver sin slags
#: verdi (P2, Codex runde 4; før dette var begge norske literaler som
#: admin-flaten rendret verbatim, så den engelske tabellen viste «Internt»):
#:
#:   * `plan` er et LUKKET vokabular. Serveren sender koden, og klienten slår
#:     den opp i `site.plan.<kode>`. Etiketten «Pilot»/«Internt» er chrome og
#:     hører hjemme i det offentlige locale-settet; det er tildelingen av en
#:     plan TIL en kunde som er tenantdata, og den blir her.
#:   * `neste` er fritekst per kunde. Den kan ikke være en locale-nøkkel uten
#:     å legge tenantdata tilbake i en anonymt nedlastbar fil, så
#:     oversettelsene følger raden ut gjennom den AUTENTISERTE veien i stedet.
_UTRULLING: tuple[dict, ...] = (
    {"id": "nordvik", "navn": "Nordvik Regnskap AS", "plan": "pilot",
     "moduler": (1, 2, 16, 37),
     "neste": {"nb": "M-38 når kapasitet og købevis er grønt.",
               "en": "M-38 once capacity and queue evidence are green."}},
    {"id": "bjorkli", "navn": "Bjørkli Elektro", "plan": "pilot",
     "moduler": (1, 2, 16),
     "neste": {"nb": "M-37 etter at unntaksrutinene er signert.",
               "en": "M-37 once the exception routines are signed."}},
    # Plattformens egen tenant (eiers innlogging, målt 24/8: raden
    # manglet og venstremenyen sa «modultildelingen er ikke
    # tilgjengelig»). Tildelingen speiler det som faktisk kjører: M-1/M-2
    # i drift, M-37 under arbeid, M-56 i drift, M-57 bygges. Tabellen er
    # fortsatt statisk pilotdata — DB-bakket tildeling har eget issue.
    {"id": "disponit", "navn": "Disponit (plattform)", "plan": "internt",
     "moduler": (1, 2, 16, 37, 56, 57),
     "neste": {"nb": "M-57 utførelsesarm; deretter M-57-aksept.",
               "en": "M-57 execution arm; then the M-57 acceptance."}},
    {"id": "granmo", "navn": "Granmo Driftsselskap", "plan": "internt",
     "moduler": (1, 2, 16, 37, 38),
     "neste": {"nb": "Brukes som kunde null for utrulling og intern drift.",
               "en": "Used as customer zero for rollout and internal "
                     "operations."}},
)


def _tekst(oversettelser: dict, sprak: str | None) -> str:
    """Fritekstfeltet på ett språk. Ukjent eller manglende språk faller til
    `nb` — en tabellcelle skal aldri stå tom fordi en oversettelse mangler."""
    valgt = sprak if sprak in SPRAK else RESERVESPRAK
    return oversettelser.get(valgt) or oversettelser.get(RESERVESPRAK) or ""


def _rad(r: dict, sprak: str | None) -> dict:
    return {"id": r["id"], "navn": r["navn"], "plan": r["plan"],
            "moduler": list(r["moduler"]), "neste": _tekst(r["neste"], sprak)}


def egen_rad(tenant, sprak: str | None = None) -> dict | None:
    """Raden for ÉN tenant, eller None når vi ikke kjenner den. None betyr
    «vet ikke», ikke «ingen moduler»: en flate som ikke vet, skal si det."""
    navn = str(tenant or "").strip().lower()
    if not navn:
        return None
    for r in _UTRULLING:
        if r["id"] == navn:
            return _rad(r, sprak)
    return None


def svar_for(tenant, scopes, sprak: str | None = None) -> dict:
    """Svaret for én økt. REN funksjon — den er hele autorisasjonsregelen for
    hva som forlater serveren, og testes uten DB.

    Uten `platform:admin` inneholder `tenanter` maksimalt økten sin EGEN rad.
    Klienten får dermed aldri en rad den ikke skal se, og trenger ikke gjøre
    et filter vi må stole på.

    `sprak` velger fritekstoversettelsen. Den er en PRESENTASJONSparameter og
    påvirker aldri HVILKE rader som sendes: en ukjent verdi gir norsk tekst,
    ikke en annen kundes rad.
    """
    plattformdrift = PLATTFORMDRIFT in set(scopes or ())
    egen = egen_rad(tenant, sprak)
    if plattformdrift:
        tenanter = [_rad(r, sprak) for r in _UTRULLING]
    else:
        tenanter = [egen] if egen else []
    # `moduler` er tenantens EGEN tildeling — også for plattformdrift, som
    # ellers ville sett hele katalogen på sin egen kundeflate.
    return {"plattformdrift": plattformdrift,
            "moduler": egen["moduler"] if egen else None,
            "tenanter": tenanter}
