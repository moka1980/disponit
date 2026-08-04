"""Versjonert M-37-taksonomi og handler-deklarasjoner (v3-delta pkt. 8).

To lukkede mengder, ikke én. Kategorien sier hva slags unntak det er;
grunnkoden sier hvorfor. `ugyldig_data` er det tydeligste eksempelet på at
kategorien alene ikke holder: den samme kategorien kan bety «feltet mangler
et siffer» (reparerbart), «to felter motsier hverandre» (manuell) og «dette
ser ut som manipulasjon» (sikkerhetskø). Ruter man på kategori alene, blir
alle tre til det samme.

Predikatet fra v4-delta pkt. 6, som er hele kontrakten:

    behandlingsbar ⇔ kategori ∈ policy.unntak.kategorier
                   ∧ kategori ∈ handler.kategorier
                   ∧ grunnkode ∈ handler.grunnkoder

Merk asymmetrien: policyens liste sammenlignes KUN med kategorier.
Grunnkoder er plattformens interne oppdeling og valideres kun mot
handler-deklarasjonen — en kunde skal ikke måtte kjenne dem for å ta i bruk
en reparasjon, og en kunde skal heller ikke kunne utvide dem.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Frossen plattformtaksonomi. De seks første er policy-skjemaets
#: OBLIGATORISKE kategorier (`policy_validator.schema`, som feiler hvis en
#: policy mangler dem); de to siste er sikkerhetskategoriene som aldri går
#: i normal kø.
#:
#: At mengden er `frozenset` og ikke en liste er poenget: den kan ikke
#: utvides ved et uhell i en annen modul. Skal den utvides, blir det
#: M37_TAKSONOMI_V2 — en ny versjon, ikke en stille endring av en gammel.
M37_TAKSONOMI_V1: frozenset[str] = frozenset({
    "manglende_data", "teknisk_feil", "over_grense",
    "regelkonflikt", "ugyldig_data", "ukjent",
    "svindelmistanke", "hms_avvik",
})

#: Kategorier som ALDRI behandles av normal-arbeideren, uansett hva en
#: handler måtte deklarere. Kø-flom-vernet fra v2 Del 4 gjelder her også.
SIKKERHETSKATEGORIER: frozenset[str] = frozenset({
    "svindelmistanke", "hms_avvik",
})

#: Plattformtaket på automatiske forsøk. Effektiv grense er
#: LEAST(sakens snapshot, dette tallet) — systemet kan stramme inn globalt,
#: aldri løsne en kundes grense. Speiler `claim_neste_sak` i migrasjon 005;
#: testen `test_plattformtaket_er_likt_i_kode_og_database` binder dem
#: sammen, for to tall som må være like og bor to steder, blir ulike.
PLATTFORM_MAKS_FORSOK = 3


class Taksonomifeil(ValueError):
    """En handler deklarerer noe som ikke finnes i taksonomien."""


@dataclass(frozen=True)
class Handlerdeklarasjon:
    """Den eksplisitte kontrakten en reparasjonshandler må oppfylle.

    Lukket på samme måte som artefaktskjemaet er lukket: alt en handler kan
    behandle må stå her, og det som ikke står her kan den ikke behandle.
    Standardsvaret er «nei», og en ny kategori er derfor en FEIL i CI og
    ikke stillhet i produksjon.

    `sideeffektfri` og `krever_outbox` er ikke to måter å si det samme på.
    En sideeffektfri handler rører ingenting utenfor plattformen og kan
    avslutte saken selv (R2). En handler som krever outbox ber om en NY
    policystyrt beslutning og venter på en signert kvittering (R1). En
    handler som er sideeffektfri OG krever outbox er en selvmotsigelse, og
    valideringen sier det.
    """
    handler_id: str
    versjon: str
    kategorier: frozenset[str]
    grunnkoder: frozenset[str]
    sideeffektfri: bool
    krever_outbox: bool
    tillatte_malhandlinger: tuple[str, ...] = ()
    timeout_s: int = 30
    lease_s: int = 120
    #: Kategorier handleren tar imot for å ERKLÆRE dem ubehandlebare
    #: (R3-veien). De teller som «dekket» i registerkontrollen, men fører
    #: aldri til en reparasjon.
    kun_klassifisering: bool = False

    @property
    def id_med_versjon(self) -> str:
        """Inngår i `repair_operation_id`. Endres handleren, endres
        identiteten — og da er det en ny reparasjon, ikke et nytt forsøk på
        den gamle."""
        return f"{self.handler_id}@{self.versjon}"

    def valider(self) -> list[str]:
        """Tom liste == gyldig. Kaster aldri (samme kontrakt som
        `valider_policy` og `valider_manifest`)."""
        feil: list[str] = []
        ukjente = sorted(self.kategorier - M37_TAKSONOMI_V1)
        if ukjente:
            feil.append(f"{self.handler_id}: kategorier utenfor"
                        f" M37_TAKSONOMI_V1: {ukjente}")
        if not self.kategorier:
            feil.append(f"{self.handler_id}: ingen kategorier deklarert")
        if not self.grunnkoder:
            feil.append(f"{self.handler_id}: ingen grunnkoder deklarert —"
                        " en tom liste ville sluppet gjennom alt")
        if self.sideeffektfri and self.krever_outbox:
            feil.append(f"{self.handler_id}: sideeffektfri OG krever_outbox"
                        " er selvmotsigende")
        if self.krever_outbox and not self.tillatte_malhandlinger:
            feil.append(f"{self.handler_id}: krever outbox uten å deklarere"
                        " tillatte målhandlinger — da er målet ubundet")
        overlapp = sorted(self.kategorier & SIKKERHETSKATEGORIER)
        if overlapp and not self.kun_klassifisering:
            feil.append(f"{self.handler_id}: kan ikke REPARERE"
                        f" sikkerhetskategorier {overlapp}")
        if self.timeout_s <= 0 or self.lease_s <= 0:
            feil.append(f"{self.handler_id}: timeout_s og lease_s må være > 0")
        return feil

    def behandler(self, kategori: str, grunnkode: str | None,
                  policykategorier: frozenset[str] | set[str]) -> bool:
        """Predikatet fra v4-delta pkt. 6, ordrett.

        Alle tre leddene må holde. Faller ett, er saken ikke behandlebar av
        DENNE handleren — og routingtabellen avgjør om den går til `manuell`
        eller til sikkerhetskøen.
        """
        return (kategori in policykategorier
                and kategori in self.kategorier
                and grunnkode is not None
                and grunnkode in self.grunnkoder)


@dataclass
class Klassifisering:
    """Utfallet av å holde en sak opp mot registeret.

    `handler` er None når ingen handler tar saken. `utfall` sier hva som da
    skal skje, og verdiene er de eneste tre som finnes: behandle den,
    send den til manuell kø, eller send den til sikkerhetskøen.
    """
    utfall: str                      # 'behandle' | 'manuell' | 'sikkerhet'
    grunn: str
    handler: Handlerdeklarasjon | None = None
    kategori: str = "ukjent"
    grunnkode: str | None = None
    detalj: dict = field(default_factory=dict)


def grunnkode_for(kategori: str, begrunnelse: list[str]) -> str | None:
    """Utleder grunnkoden av sakens begrunnelseskoder.

    Begrunnelsen er en liste med koder fra motoren, og den SISTE er den
    blokkerende (samme regel som `feil.sakstype_for` bruker — alt foran er
    `*_ok`-kvitteringer). Uten den regelen ville en sak som passerte fem
    kontroller og falt på den sjette blitt klassifisert etter den første.

    Finnes ingen begrunnelse, er svaret None og ikke en gjetning. En sak
    uten grunnkode er per predikatet ikke behandlebar — fail-closed.
    """
    if not begrunnelse:
        return None
    siste = begrunnelse[-1]
    return siste if isinstance(siste, str) and siste else None
