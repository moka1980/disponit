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
        #
        # `vilkaar` er PR-007s eneste utvidelse av oppdragskontrakten:
        # HVILKET krav som skal verifiseres. Uten det måtte verifikatoren
        # gjette ut fra handlingen, og en verifikator som gjetter hva den
        # skal attestere, attesterer noe annet enn det saken manglet.
        felter=frozenset({"handling", "ressurs_id", "kildereferanser",
                          "kategori", "vilkaar"}),
        paakrevde=frozenset({"handling", "ressurs_id", "vilkaar"}),
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


# ---------------------------------------------------------------------------
# Verifikasjonskvitteringen — den ENESTE bæreren av en attestasjon
# ---------------------------------------------------------------------------
#
# v2-delta pkt. 4-5. Signaturen dekker en KANONISK (JCS) konvolutt med
# nøyaktig disse feltene og ingen andre. Lukket format, samme prinsipp som
# artefaktskjemaet: en ukjent nøkkel er en FEIL, ikke stillhet.
#
# Hvorfor en egen kvitteringstype: en ordinær utførelseskvittering skal
# ALDRI kunne bære en attestasjon. Var det ett felles skjema, ville en
# eiermodul med `orders:execute`-scope kunnet levere «bevis» for et vilkår
# den ikke har fullmakt til å attestere — og hele skillet mellom å UTFØRE
# og å ATTESTERE ville vært en navnekonvensjon.

KVITTERINGSTYPER = ("utforelseskvittering_v1", "verifikasjonskvittering_v1")

#: Konvoluttens felter. Rekkefølgen er uten betydning (JCS sorterer), men
#: mengden er bindende.
#:
#: 🔴 AVVIK FRA v2-DELTA pkt. 4, og grunnen er målt:
#: spesifikasjonen lister `tenant`, `verifikator_id` og `attestert_resultat`,
#: og har verken `handling` eller `policy_id`. Med den formen kan
#: konvolutten IKKE brukes som attestasjon i fase 2 — motorens
#: `kontroller_binding` krever `('tenant_id','handling','vilkaar',
#: 'ressurs_id','policy_id','utstedt','utloper','jti')`, og `verifiser`
#: slår opp nøkkelen på feltet `verifikator`.
#:
#: Da ville hele bæremekanismen falt: «beviset bæres av verifikatorens
#: signatur» krever at det signerte objektet ER attestasjonen. Skulle
#: API-et i stedet bygge en attestasjon FRA konvolutten, måtte det signere
#: den — og API-et er ingen verifikator.
#:
#: Konvolutten er derfor et SUPERSETT: motorens bindingsfelter med motorens
#: navn, pluss fase-1-bindingene. Ekstra felter er uproblematiske for
#: motoren (den leser de den krever), og de inngår i de signerte bytene.
VERIFIKASJONSKVITTERING_FELTER = frozenset({
    # --- det motoren krever av en attestasjon (samme navn) -------------
    "tenant_id", "handling", "vilkaar", "ressurs_id", "policy_id",
    "utstedt", "utloper", "jti", "verifikator", "resultat",
    # --- fase-1-bindingene ---------------------------------------------
    "protokollversjon", "kvitteringstype", "oppdrag_id", "unntak_id",
    "fase1_repair_operation_id", "verification_generation",
    "attestert_resultat", "vilkaarsverdier", "nokkel_id",
    # `kanonisering` og `signatur` legges på av signeringen selv
    # (`policy_validator.attestering.signer`), og hører derfor til
    # konvolutten selv om de ikke er noe verifikatoren fyller ut.
    "kanonisering", "signatur",
})

#: Alt som IKKE er valgfritt. `vilkaarsverdier` er den eneste som er det —
#: noen vilkår attesteres med en verdi (f.eks. antall dager), andre er rent
#: boolske.
VERIFIKASJONSKVITTERING_PAAKREVDE = frozenset(
    VERIFIKASJONSKVITTERING_FELTER - {"vilkaarsverdier"})

#: De to eneste lovlige utfallene. `positiv` betyr at vilkåret KAN
#: attesteres; `negativ` at det ikke kan. Det finnes ingen tredje verdi —
#: «vet ikke» er `negativ`, fordi fail-closed er retningen.
ATTESTERTE_RESULTATER = ("positiv", "negativ")

PROTOKOLLVERSJON = 1


class Konvoluttfeil(ValueError):
    """Konvolutten har ikke den deklarerte formen. Aldri en gjetning."""


def valider_verifikasjonskvittering(kropp: object) -> list[str]:
    """Tom liste == konvolutten har NØYAKTIG den deklarerte formen.

    Kaster aldri — samme kontrakt som `valider_policy` og
    `valider_manifest`. Kalleren avgjør hva et brudd skal bli (her:
    sikkerhetslogg, ingen bevisrad).

    Merk at dette KUN er formkontroll. At feltene stemmer med databasen —
    tenant, sak, vilkår, generasjon, oppdrag — kontrolleres server-side i
    `registrer_verifikasjonsbevis`, mot radene og ikke mot konvolutten.
    Konvolutten er sammenligningsgrunnlag, aldri autoritativ kilde.
    """
    if not isinstance(kropp, dict):
        return ["kvitteringen er ikke et objekt"]
    feil: list[str] = []

    ukjente = sorted(set(kropp) - VERIFIKASJONSKVITTERING_FELTER)
    if ukjente:
        feil.append(f"ukjente felter: {ukjente}")
    mangler = sorted(VERIFIKASJONSKVITTERING_PAAKREVDE - set(kropp))
    if mangler:
        feil.append(f"manglende felter: {mangler}")

    if kropp.get("kvitteringstype") != "verifikasjonskvittering_v1":
        feil.append(f"kvitteringstype={kropp.get('kvitteringstype')!r}"
                    " — kun verifikasjonskvittering_v1 kan bære en attestasjon")
    if kropp.get("protokollversjon") != PROTOKOLLVERSJON:
        feil.append(f"protokollversjon={kropp.get('protokollversjon')!r},"
                    f" krever {PROTOKOLLVERSJON}")
    if kropp.get("attestert_resultat") not in ATTESTERTE_RESULTATER:
        feil.append(f"attestert_resultat={kropp.get('attestert_resultat')!r}"
                    f" — lovlige: {list(ATTESTERTE_RESULTATER)}")
    # `resultat` er motorens felt og MÅ stemme med `attestert_resultat`.
    # To felter som sier det samme kan si ulikt, og da ville motoren og
    # M-37 lest hver sin sannhet ut av det samme signerte objektet.
    forventet = kropp.get("attestert_resultat") == "positiv"
    if kropp.get("resultat") is not forventet:
        feil.append(f"resultat={kropp.get('resultat')!r} motsier"
                    f" attestert_resultat={kropp.get('attestert_resultat')!r}")

    for felt in ("tenant_id", "vilkaar", "ressurs_id", "verifikator",
                 "handling", "policy_id", "nokkel_id", "jti"):
        verdi = kropp.get(felt)
        if not isinstance(verdi, str) or not verdi.strip():
            feil.append(f"{felt} må være en ikke-tom streng")
    for felt in ("oppdrag_id", "unntak_id", "verification_generation"):
        verdi = kropp.get(felt)
        # `bool` er subklasse av `int` — samme felle som i manifestporten.
        if isinstance(verdi, bool) or not isinstance(verdi, int):
            feil.append(f"{felt} må være et heltall")
    if kropp.get("verification_generation") is not None \
            and isinstance(kropp.get("verification_generation"), int) \
            and not isinstance(kropp.get("verification_generation"), bool) \
            and kropp["verification_generation"] < 1:
        feil.append("verification_generation starter på 1")
    return feil


def er_utforelseskvittering(kropp: object) -> bool:
    """En ordinær utførelseskvittering skal ALDRI bære attestasjonsfelt.

    Brukes av utførelsesporten til å avvise en kvittering som prøver å
    smugle bevis inn gjennom feil dør.
    """
    if not isinstance(kropp, dict):
        return False
    forbudte = {"attestert_resultat", "vilkaar", "verification_generation",
                "vilkaarsverdier", "fase1_repair_operation_id"}
    return not (set(kropp) & forbudte)
