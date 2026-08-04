"""Lukket feltskjema per oppdragstype (v4-delta pkt. 4).

Eiermodulen får plaintext, og det er hele grunnen til at dette finnes:
i det øyeblikket noe forlater krypteringen, er spørsmålet ikke «hvem kan
lese det» men «hva står det i det». Uten et lukket skjema bestemmes svaret
av hva som tilfeldigvis lå i saken.

`api.minimering` gjør det samme for unntakspayloaden og er forbildet:
ALLOWLIST, ikke blocklist. En blocklist må kjenne alle persondatafelter en
connector noensinne finner på å sende, og den listen blir aldri ferdig.

Ett tillegg som ikke fantes i minimeringen: HANDLINGSPREFIKSET GIR ALDRI
FELTBREDDE ALENE. To oppdrag med samme prefiks (`faktura.`) kan ha helt
ulike behov, og å la prefikset styre hvilke felter som slipper ut ville
gjort feltbredden til en funksjon av navnet på handlingen.
"""
from __future__ import annotations

from dataclasses import dataclass


class Oppdragstypeukjent(KeyError):
    """Ingen registrert oppdragstype. Fail-closed: da sendes ingenting."""


@dataclass(frozen=True)
class Oppdragstype:
    """Én oppdragstype med sitt lukkede felt-sett.

    `felter` er det eiermodulen FÅR SE. `paakrevde` er det den må ha for at
    oppdraget skal kunne opprettes i det hele tatt — mangler ett av dem,
    er oppdraget ufullstendig, og et ufullstendig oppdrag skal ikke ut til
    en utfører som «gjør så godt den kan».
    """
    navn: str
    handlingsprefikser: tuple[str, ...]
    felter: frozenset[str]
    paakrevde: frozenset[str]
    beskrivelse: str = ""

    def valider(self) -> list[str]:
        feil = []
        mangler = sorted(self.paakrevde - self.felter)
        if mangler:
            feil.append(f"{self.navn}: påkrevde felter som ikke er med i"
                        f" feltlisten: {mangler} — de ville vært påkrevd og"
                        " likevel filtrert bort")
        if not self.handlingsprefikser:
            feil.append(f"{self.navn}: ingen handlingsprefikser — da kan"
                        " ingen handling matche typen")
        return feil


#: Registeret. Lukket på samme måte som `api.feil.FEILVEIER`: tabellen ER
#: kontrakten, og en test itererer over den.
#:
#: v1 har to typer, og det er med vilje ikke flere. Hver oppdragstype er en
#: kanal for plaintext ut av plattformen; de skal legges til én om gangen,
#: med sin egen begrunnelse for hvert felt.
OPPDRAGSTYPER: dict[str, Oppdragstype] = {
    "reinnsending": Oppdragstype(
        navn="reinnsending",
        handlingsprefikser=("purring.", "faktura.", "melding."),
        # `ressurs_id` er en ugjennomsiktig referanse, ikke innhold.
        # `kildereferanser` er allerede minimert av `api.minimering` og
        # inneholder per konstruksjon kun {connector, resource_id, field_id}.
        felter=frozenset({"handling", "ressurs_id", "tidspunkt", "valuta",
                          "belop", "kildereferanser", "kategori"}),
        paakrevde=frozenset({"handling", "ressurs_id"}),
        beskrivelse=("R1: en handling som ble stoppet av manglende data, og"
                     " som skal utføres på nytt etter at dataene foreligger.")),
    "verifikasjon": Oppdragstype(
        navn="verifikasjon",
        handlingsprefikser=("verifiser.", "kontroll."),
        # Ingen beløp: et verifikasjonsoppdrag skal slå opp mot en
        # autoritativ kilde, ikke få vite hva saken gjaldt i kroner.
        felter=frozenset({"handling", "ressurs_id", "kildereferanser",
                          "kategori"}),
        paakrevde=frozenset({"handling", "ressurs_id"}),
        beskrivelse=("v3-delta pkt. 5: alle oppslag mot autoritative kilder"
                     " er sideeffektfrie oppdrag utført av en modul med egne"
                     " fullmakter. M-37 rører aldri ERP/bank/CRM selv.")),
}


def type_for_handling(handling: str) -> Oppdragstype | None:
    """Oppdragstypen en handling hører til, eller None.

    Prefiksene er disjunkte per konstruksjon — `test_prefikser_er_disjunkte`
    beviser det. Overlappende prefikser ville gjort feltbredden avhengig av
    hvilken rekkefølge dict-en tilfeldigvis har.
    """
    if not isinstance(handling, str):
        return None
    for t in OPPDRAGSTYPER.values():
        if any(handling.startswith(p) for p in t.handlingsprefikser):
            return t
    return None


def minimer(oppdragstype: str, payload: dict) -> dict:
    """Payloaden slik eiermodulen får se den. Kaster Oppdragstypeukjent.

    Verdier som ikke er enkle JSON-typer droppes, akkurat som i
    `api.minimering`: en struktur vi ikke har inspisert er en struktur vi
    ikke vet innholdet i, og «den så ut som en liste» er ikke en
    inspeksjon.
    """
    t = OPPDRAGSTYPER.get(oppdragstype)
    if t is None:
        raise Oppdragstypeukjent(oppdragstype)
    ut: dict[str, object] = {}
    for felt in sorted(t.felter):
        if felt not in payload:
            continue
        verdi = payload[felt]
        if isinstance(verdi, (str, int, float, bool)) or verdi is None:
            ut[felt] = verdi
        elif felt == "kildereferanser" and isinstance(verdi, list):
            # Kildereferanser er allerede normalisert til nøyaktig tre
            # nøkler av `api.minimering._kildereferanser`. Vi gjentar
            # kontrollen her fordi denne modulen ikke kan VITE at den
            # forrige kjørte — payloaden kommer fra en dekryptert rad, og
            # en rad kan være skrevet av en eldre versjon.
            rene = [{k: str(r[k]) for k in ("connector", "resource_id",
                                            "field_id")}
                    for r in verdi
                    if isinstance(r, dict)
                    and all(k in r for k in ("connector", "resource_id",
                                             "field_id"))]
            if rene:
                ut[felt] = rene
    return ut


def mangler_paakrevde(oppdragstype: str, minimert: dict) -> list[str]:
    """Påkrevde felter som ikke overlevde minimeringen. Tom == komplett."""
    t = OPPDRAGSTYPER.get(oppdragstype)
    if t is None:
        raise Oppdragstypeukjent(oppdragstype)
    return sorted(f for f in t.paakrevde if not minimert.get(f))
