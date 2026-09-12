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

import re

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


#: Modulnummer fra en policyhandling: "M-14" → 14. Ukjent form gir None og
#: faller bort — en modul vi ikke kan tallfeste, kan klienten uansett ikke
#: slå opp i katalogen.
_MODUL = re.compile(r"^M-(\d{1,3})$")


def moduler_fra_policy(policy: object) -> list[int]:
    """Modulnumrene en policy faktisk gir agenten fullmakt over.

    HVORFOR DETTE OG IKKE EN TABELL: `_UTRULLING` er statisk kildekode, og
    kommentaren over den sier det selv — «fortsatt statisk pilotdata». Et
    firma som registrerer seg selv står ikke der, og fikk derfor
    `moduler: null`, som venstremenyen viser som «Modultildelingen er ikke
    tilgjengelig». En selvbetjent kunde landet altså i et skall uten
    moduler, og hver ny kunde ville krevd en kodeendring og en deploy.

    Bransjemalene navngir modulene selv, i `handlinger[].modul`. Å lese dem
    derfra er ikke en utledning vi finner på: det er den samme autoriteten
    som styrer hva agenten får GJØRE. Da kan ingen kunde ende opp med en
    modul uten fullmakt, eller en fullmakt uten modul — og listen følger
    policyen automatisk hvis den endres.
    """
    if not isinstance(policy, dict):
        return []
    ut: set[int] = set()
    for h in policy.get("handlinger") or ():
        if not isinstance(h, dict):
            continue
        m = _MODUL.match(str(h.get("modul") or "").strip())
        if m:
            ut.add(int(m.group(1)))
    return sorted(ut)


def rad_for_nytt_firma(tenant, navn, status, prove_utloper, moduler,
                       sprak: str | None = None) -> dict:
    """Utrullingsraden for et firma som ikke står i `_UTRULLING`.

    `plan` er firmaets LIVSSYKLUS (190), ikke en pilotbetegnelse — det er
    det mest sannferdige et nyregistrert firma kan si om seg selv. Og
    «neste steg» er prøveperiodens frist: for en ny kunde er det faktisk
    det neste som skjer.
    """
    naa = {"nb": "Prøveperioden varer til {dato}.",
           "en": "The trial runs until {dato}."}
    tekst = (naa.get(sprak if sprak in SPRAK else RESERVESPRAK) or "")
    return {"id": str(tenant), "navn": str(navn),
            "plan": str(status),
            "moduler": list(moduler),
            "neste": tekst.replace("{dato}", str(prove_utloper or ""))
                     if prove_utloper else ""}


def svar_for(tenant, scopes, sprak: str | None = None,
             nytt_firma: dict | None = None) -> dict:
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
    # `_UTRULLING` VINNER. Den er håndpleide pilotdata for tre kjente
    # kunder; `nytt_firma` er utledningen for alle andre. Rekkefølgen er
    # med vilje — en pilotrad som ble overstyrt av en automatisk utledning
    # ville endret det noen har bestemt for hånd, uten at noe sa fra.
    egen = egen_rad(tenant, sprak) or nytt_firma
    if plattformdrift:
        tenanter = [_rad(r, sprak) for r in _UTRULLING]
    else:
        tenanter = [egen] if egen else []
    # `moduler` er tenantens EGEN tildeling — også for plattformdrift, som
    # ellers ville sett hele katalogen på sin egen kundeflate.
    return {"plattformdrift": plattformdrift,
            "moduler": egen["moduler"] if egen else None,
            "tenanter": tenanter}
