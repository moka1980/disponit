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


def minimer_payload(event: dict, kategori: str | None,
                    begrunnelse: list[str]) -> dict:
    """Saksgrunnlaget som skal krypteres og lagres.

    `begrunnelse` er KODER, ikke parametre: parametrene kan inneholde
    verdiene som utløste bruddet (f.eks. beløpet), og de hører hjemme i
    payloaden bare hvis feltet står i allowlisten.
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
    return ut
