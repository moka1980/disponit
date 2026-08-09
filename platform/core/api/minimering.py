"""Dataminimering av unntaks-payload (v2 Del 5).

Regelen er en ALLOWLIST, ikke en blocklist: kun felter som er navngitt her
overlever inn i saken. Grunnen er at en blocklist må kjenne alle
persondatafelter en connector noensinne finner på å sende, og den listen
kan man ikke skrive ferdig. Med allowlist er standardsvaret «kastes», og
et ukjent felt er da et manglende felt i M-37 — ikke en lekkasje.

Persondata HASHES ikke. Spesifikasjonen er tydelig: en hash av et
personnummer eller en e-postadresse er fortsatt persondata — ordlisten er
kort nok til å slå den opp, og hashen kobler samme person på tvers av
saker. Der M-37 trenger å vite HVOR verdien kom fra, sendes en
ugjennomsiktig kildereferanse `{connector, resource_id, field_id}` i stedet.
"""
from __future__ import annotations

from decimal import Decimal

#: Felter M-37 trenger uansett kategori for å kunne behandle saken.
FELLESFELT = ("handling", "ressurs_id", "tidspunkt", "valuta",
              "dataklasser", "dataklasser_kilde")

#: Ekstra felter per unntakskategori. Tomme tupler er ikke forglemmelser:
#: for de kategoriene er begrunnelseskodene hele saksgrunnlaget.
PER_KATEGORI: dict[str, tuple[str, ...]] = {
    "over_grense": ("belop",),
    "ugyldig_data": ("belop",),
    "manglende_data": (),
    "regelkonflikt": (),
    "teknisk_feil": (),
    "ukjent": (),
}

#: Kildereferansen er allerede ugjennomsiktig og slippes gjennom som den er.
KILDEREFERANSEFELT = "kildereferanser"
KILDEREFERANSENOKLER = ("connector", "resource_id", "field_id")


def _rent(verdi: object) -> object:
    """JSON-bare verdier. Decimal blir streng (samme som i motoren);
    alt annet ukjent blir droppet av kalleren."""
    if isinstance(verdi, Decimal):
        return str(verdi)
    return verdi


def _kildereferanser(raa: object) -> list[dict] | None:
    """Slipper kun gjennom referanser med NØYAKTIG de tre nøklene.

    En connector som legger ved `{"connector": ..., "epost": ...}` skal
    ikke få smuglet e-postadressen inn i saken under et felt som ser
    ugjennomsiktig ut.
    """
    if not isinstance(raa, list):
        return None
    ut = []
    for ref in raa:
        if not isinstance(ref, dict):
            continue
        ren = {k: str(ref[k]) for k in KILDEREFERANSENOKLER if k in ref}
        if len(ren) == len(KILDEREFERANSENOKLER):
            ut.append(ren)
    return ut or None


#: Det ENESTE parameteret som slippes gjennom fra en Grunn.
#:
#: Regelen ellers er at parametre droppes: de kan inneholde beløpet,
#: gruppeverdien eller andre biter av hendelsen som utløste bruddet.
#: Vilkårets NAVN er noe annet — det står allerede i kundens policy, det er
#: ikke avledet av hendelsen, og det er ikke persondata.
#:
#: Uten det kan M-37 ikke handle. `purring.send` har to vilkår i
#: bransjemalen, og en sak som bare sier «attestasjon_mangler» sier ikke
#: HVILKEN. Fase 1 måtte da enten gjette eller be om verifikasjon av alt —
#: og en verifikator som attesterer noe annet enn det saken manglet, har
#: ikke verifisert saken.
VILKAARSFELT = "manglende_vilkaar"


#: Policyens EGEN grupperingsnøkkel for handlingen (`frekvens.
#: grupperingsnokkel`). Ikke et fast feltnavn — kunden bestemmer det.
#:
#: MÅLT: uten den svarte motoren `frekvens_grupperingsverdi_mangler` på
#: hver eneste fase-2-beslutning, og saken gikk til manuell. Payloaden
#: bar `handling` og `ressurs_id`, men ikke `faktura_id`, og en hendelse
#: uten gruppeverdi kan frekvensregelen ikke evaluere.
#:
#: Å slippe den gjennom utvider ikke hva systemet lagrer: telleren
#: (`frekvens_hendelser`) lagrer ALLEREDE nøyaktig denne verdien for samme
#: tenant og handling, skrevet av motoren selv. Feltet er kundens eget
#: valg i sin egen policy, og verdien beholdes kun når den er en enkel
#: streng.
def minimer_payload(event: dict, kategori: str | None,
                    begrunnelse: list[str], *,
                    vilkaar: str | None = None,
                    grupperingsnokkel: str | None = None) -> dict:
    """Saksgrunnlaget som skal krypteres og lagres.

    `begrunnelse` er KODER, ikke parametre: parametrene kan inneholde
    verdiene som utløste bruddet (f.eks. beløpet), og de hører hjemme i
    payloaden bare hvis feltet står i allowlisten. Eneste unntak er
    `vilkaar` — se `VILKAARSFELT`.
    """
    tillatt = set(FELLESFELT) | set(PER_KATEGORI.get(kategori or "", ()))
    ut: dict[str, object] = {}
    for felt in sorted(tillatt):
        if felt in event:
            verdi = _rent(event[felt])
            if isinstance(verdi, (str, int, float, bool)) or verdi is None:
                ut[felt] = verdi
            elif isinstance(verdi, list) and all(
                    isinstance(x, (str, int, float, bool)) for x in verdi):
                ut[felt] = verdi
            # Alt annet (dict, nøstede strukturer) droppes: en struktur vi
            # ikke har inspisert er en struktur vi ikke vet innholdet i.
    referanser = _kildereferanser(event.get(KILDEREFERANSEFELT))
    if referanser:
        ut[KILDEREFERANSEFELT] = referanser
    ut["begrunnelse"] = list(begrunnelse)
    ut["kategori"] = kategori
    if isinstance(vilkaar, str) and vilkaar.strip():
        ut[VILKAARSFELT] = vilkaar
    if isinstance(grupperingsnokkel, str) and grupperingsnokkel.strip():
        verdi = event.get(grupperingsnokkel)
        if isinstance(verdi, str) and verdi.strip():
            ut[grupperingsnokkel] = verdi
    return ut


# ---------------------------------------------------------------------------
# PR-012 §1: handlingsintensjon — formell utvidelse av minimeringskontrakten
#
# Den minimerte payloaden mangler `belop` (PR-005b-allowlisten), så motoren
# kan ikke re-evaluere en over_grense-sak — og det er nettopp den saken et
# menneske vil godkjenne. Handlingsintensjonen er en EGEN, LUKKET, minimert
# struktur med kun det motoren trenger for å re-evaluere godkjennbare vilkår.
# Ingen fri passthrough; attestasjoner beholdes KUN som referanser (vilkår +
# verifikator), ALDRI verdien/resultatet — det ville vært en råverdi.
# ---------------------------------------------------------------------------

#: Lukket konstant. Ukjent versjon ved lesing => godkjenn utilgjengelig
#: (fail-closed), aldri gjetting (v3 §7).
HI_SKJEMAVERSJON = 1

#: 8 KiB klartekst-tak (v3 §7). Intensjonen er noen få skalarer + referanser;
#: taket er et vern mot en uventet oppblåsning, ikke en forventet grense.
HI_MAKS_KLARTEKST = 8 * 1024

#: Skalarfeltene motoren re-evaluerer på (v2 §1). Alle er metadata, ikke
#: råverdier: `ressurs_id` er en ugjennomsiktig id, `dataklasser` er
#: KLASSENAVN (ikke selve dataene), resten er beløp/valuta/tid/handling.
_INTENSJONSSKALARER = ("handling", "ressurs_id", "belop", "valuta",
                       "tidspunkt", "dataklasser_kilde")


def _attestasjon_referanser(raa: object) -> list[dict]:
    """Referanse = vilkårsnavn + verifikator, sortert. ALDRI `verdi`/
    `resultat` (kan være persondata-avledet) og aldri signaturen."""
    if not isinstance(raa, dict):
        return []
    ut = []
    for navn, att in sorted(raa.items()):
        if isinstance(att, dict) and isinstance(att.get("verifikator"), str):
            ut.append({"vilkaar": navn, "verifikator": att["verifikator"]})
    return ut


class IntensjonForStor(ValueError):
    """Klartekst-intensjonen overstiger `HI_MAKS_KLARTEKST` (v3 §7)."""


def bygg_handlingsintensjon(event: dict, aktor_rolle: str | None = None) -> dict:
    """Den lukkede, minimerte handlingsintensjonen (v2 §1, v3 §7).

    Bygges fra den ORIGINALE hendelsen, aldri fra klient-tidsnærhet eller
    «siste rad». Kun de deklarerte feltene slippes gjennom — samme _rent- og
    referanseregler som `minimer_payload`. Kaster `IntensjonForStor` over
    taket. Kalleren krypterer resultatet i SAMME transaksjon som unntaket.

    `aktor_rolle` er den AUTENTISERTE rollen den opprinnelige beslutningen ble
    tatt med (fra `rolle_ok`-grunnen, ikke fra hendelsen). Den er en
    systemrolle, ikke persondata, og trengs for at motoren skal kunne
    rekonstruere den samme `EvaluationContext` ved menneskelig re-evaluering —
    ellers ville re-evalueringen stoppet på rollekontrollen (steg 3), som ikke
    er den bundne grunnkoden.
    """
    import json

    ut: dict[str, object] = {}
    if isinstance(aktor_rolle, str) and aktor_rolle:
        ut["aktor_rolle"] = aktor_rolle
    for felt in _INTENSJONSSKALARER:
        v = _rent(event.get(felt))
        if isinstance(v, (str, int, float, bool)):
            ut[felt] = v
    dk = _rent(event.get("dataklasser"))
    if isinstance(dk, list) and all(isinstance(x, str) for x in dk):
        ut["dataklasser"] = dk
    ut["attestasjoner_referanser"] = _attestasjon_referanser(
        event.get("attestasjoner"))
    if len(json.dumps(ut, ensure_ascii=False).encode("utf-8")) > HI_MAKS_KLARTEKST:
        raise IntensjonForStor(str(HI_MAKS_KLARTEKST))
    return ut
