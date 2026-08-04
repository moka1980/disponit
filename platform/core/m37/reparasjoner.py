"""Reparasjonsbiblioteket — et LUKKET register (v1 §2, v2 §7, v3 §5).

Den bærende invarianten, og den eneste som virkelig betyr noe her:

    EN REPARASJON UTFØRER ALDRI FORRETNINGSSIDEEFFEKTEN SELV,
    OG OMGÅR ALDRI MOTOREN.

Den er ikke skrevet som en regel man skal huske. Den er bygget inn i
typene: en handler får payloaden og returnerer en `Reparasjonsplan` — et
dataobjekt. Den har ingen databasetilkobling, ingen HTTP-klient og ingen
måte å endre noe på. Arbeideren, som eier transaksjonen, er den eneste som
kan handle på planen, og den eneste handlingen som finnes er «be om en ny
policystyrt beslutning gjennom API-et» eller «legg ut et oppdrag».

M-37 har dermed null egne fullmakter. Den kan bare be om nye.

Registeret er lukket: en ukjent (kategori, grunnkode) gir ingen handler, og
det er en FEIL-vei — ikke stillhet. `test_registeret_dekker_kun_kjente_
kategorier` feiler hvis noen legger inn en kategori policy-skjemaet ikke
kjenner.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import oppdragskontrakt as oppdragsskjema
from .taksonomi import (Handlerdeklarasjon, Klassifisering,
                        SIKKERHETSKATEGORIER)

# ---------------------------------------------------------------------------
# Planen en handler kan produsere — og ikke noe mer
# ---------------------------------------------------------------------------

#: De fire eneste utfallene. `ko` betyr «tilbake til køen, prøv igjen
#: senere»; forsøkstelleren i databasen sørger for at det ikke kan pågå i
#: det uendelige.
UTFALL = ("oppdrag", "lost", "manuell", "ko")


@dataclass(frozen=True)
class Reparasjonsplan:
    """Hva arbeideren SKAL gjøre. Ikke hva handleren HAR gjort.

    Skillet er hele poenget. Et returobjekt kan ikke ha utført noe.
    """
    utfall: str
    grunn: str
    maalhandling: str | None = None
    oppdragstype: str | None = None
    #: Det som inngår i `repair_operation_id`. Må være kanoniserbart og
    #: stabilt: to like reparasjoner av samme sak skal gi samme id, ellers
    #: er idempotensen borte og hvert forsøk blir en ny forretningshandling.
    reparasjonsinput: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.utfall not in UTFALL:
            raise ValueError(f"ukjent utfall {self.utfall!r}")
        if self.utfall == "oppdrag" and not (self.maalhandling
                                             and self.oppdragstype):
            raise ValueError("oppdrag krever både målhandling og oppdragstype")


def input_hash(reparasjonsinput: dict) -> str:
    """Kanonisk SHA-256 over reparasjonens input.

    Samme regler som `api.policyregister.innholds_hash` og
    `api.kjerne.input_hash`: sorterte nøkler, ingen mellomrom, UTF-8. At de
    tre stedene bruker SAMME regler er ikke tilfeldig — hashen krysser
    prosessgrenser, og en avvikende serialisering ett sted ville gitt to
    ulike identiteter for samme reparasjon.
    """
    return hashlib.sha256(json.dumps(
        reparasjonsinput, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def repair_operation_id(tenant: str, unntak_id: int, handler_id_med_versjon: str,
                        maalhandling: str, inp_hash: str) -> str:
    """SHA-256(tenant ‖ unntak_id ‖ handler@versjon ‖ målhandling ‖ input_hash).

    `forsok` og `claim_id` inngår ALDRI (v2-delta pkt. 4). De er
    transportdetaljer: en idempotensnøkkel som endrer seg per forsøk er
    ingen idempotensnøkkel, og hvert retry ville blitt en ny
    forretningshandling i stedet for et nytt forsøk på den samme.

    Separatoren er \\x1f (unit separator) fordi den ikke kan forekomme i
    noen av delene. Med bindestrek ville («a-b», «c») og («a», «b-c») gitt
    samme id.
    """
    raa = "\x1f".join((tenant, str(unntak_id), handler_id_med_versjon,
                       maalhandling, inp_hash))
    return hashlib.sha256(raa.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# De tre handlerne — v1-omfanget er UTTØMMENDE
# ---------------------------------------------------------------------------

R1 = Handlerdeklarasjon(
    handler_id="r1_reinnsending",
    versjon="1",
    kategorier=frozenset({"manglende_data"}),
    grunnkoder=frozenset({
        "vilkaar_mangler_attestasjon", "attestasjon_mangler",
        "manglende_felt", "manglende_dataklasse_kilde",
    }),
    sideeffektfri=False,
    krever_outbox=True,
    tillatte_malhandlinger=("purring.", "faktura.", "melding."),
    timeout_s=60, lease_s=180)

R2 = Handlerdeklarasjon(
    handler_id="r2_lokal_kontroll",
    versjon="1",
    kategorier=frozenset({"teknisk_feil"}),
    grunnkoder=frozenset({
        "motor_exception", "logging_feilet", "payload_uleselig",
        "skjemafeil_forbigaaende",
    }),
    # v3-delta pkt. 5: R2 er innskrenket til KONTROLLER AV DATA M-37
    # ALLEREDE HAR. Alle oppslag mot autoritative kilder er
    # verifikasjonsoppdrag gjennom outbox-protokollen — utført av en modul
    # med egne fullmakter. Uten den innskrenkningen ville
    # null-fullmaktsprinsippet hatt et unntak, og et prinsipp med ett
    # unntak er en retningslinje.
    sideeffektfri=True,
    krever_outbox=False,
    timeout_s=15, lease_s=60)

R3 = Handlerdeklarasjon(
    handler_id="r3_policykrevende",
    versjon="1",
    kategorier=frozenset({"over_grense", "regelkonflikt", "ugyldig_data",
                          "ukjent"}),
    grunnkoder=frozenset({
        "belopsgrense_overskredet", "frekvensgrense_naadd",
        "frekvensgrense_naadd_ved_reservasjon", "utenfor_tidsvindu",
        "rolle_mangler_fullmakt", "dataklasse_ikke_tillatt",
        "regelkonflikt", "ugyldig_data", "ukjent",
        "policy_belopsgrense_ugyldig", "policy_tidssone_ugyldig",
        "frekvens_uten_tellerlager",
    }),
    sideeffektfri=True,
    krever_outbox=False,
    # R3 REPARERER ingenting. Beslutningen var riktig — en handling over
    # beløpsgrensen skal stoppes, og det er ikke en feil som kan fikses av
    # en maskin. Den eneste jobben er å KLASSIFISERE saken som manuell ved
    # FØRSTE claim, slik at den ikke brenner tre forsøk på å oppdage det
    # samme tre ganger.
    kun_klassifisering=True,
    timeout_s=5, lease_s=30)

#: Det lukkede registeret. Rekkefølgen er uten betydning — oppslaget skjer
#: på (kategori, grunnkode), og `test_ingen_to_handlere_deler_kategori`
#: beviser at ingen sak kan treffe to.
REGISTER: tuple[Handlerdeklarasjon, ...] = (R1, R2, R3)


def valider_register() -> list[str]:
    """Tom liste == registeret er konsistent. Kjøres i CI.

    Tre kontroller, og alle tre finnes fordi en av dem alene ville sluppet
    gjennom noe: at hver handler er gyldig for seg, at ingen to handlere
    kan ta samme sak, og at registeret dekker HELE taksonomien. Den siste
    er den viktigste — en kategori uten handler er en sak som blir liggende
    uten at noen har bestemt at den skal det.
    """
    feil: list[str] = []
    for h in REGISTER:
        feil += h.valider()

    sett: dict[tuple[str, str], str] = {}
    for h in REGISTER:
        for kat in sorted(h.kategorier):
            for gk in sorted(h.grunnkoder):
                nokkel = (kat, gk)
                if nokkel in sett:
                    feil.append(f"(kategori={kat}, grunnkode={gk}) tas av både"
                                f" {sett[nokkel]} og {h.handler_id}")
                sett[nokkel] = h.handler_id

    dekket = frozenset().union(*(h.kategorier for h in REGISTER))
    from .taksonomi import M37_TAKSONOMI_V1
    udekket = sorted(M37_TAKSONOMI_V1 - dekket - SIKKERHETSKATEGORIER)
    if udekket:
        feil.append(f"kategorier i taksonomien uten handler: {udekket}"
                    " — en sak i den kategorien ville blitt liggende uten at"
                    " noen har bestemt at den skal det")
    return feil


# ---------------------------------------------------------------------------
# Klassifisering — den lukkede routingtabellen
# ---------------------------------------------------------------------------

def klassifiser(kategori: str, grunnkode: str | None,
                policykategorier: frozenset[str] | set[str]) -> Klassifisering:
    """Hvilken handler tar saken — eller hvorfor ingen gjør det.

    Rekkefølgen på kontrollene er bindende. Sikkerhetskategoriene sjekkes
    FØRST, av samme grunn som `feil.sakstype_for` gjør det: en
    manipulasjonsmistanke er en sikkerhetssak selv om policyen tilfeldigvis
    lister kategorien som en ordinær unntakskategori.
    """
    if kategori in SIKKERHETSKATEGORIER:
        return Klassifisering("sikkerhet", "sikkerhetskategori",
                              kategori=kategori, grunnkode=grunnkode)
    if grunnkode is None:
        # Fail-closed. En sak uten grunnkode kan ikke oppfylle predikatet,
        # og å gjette en ville vært å finne på et saksgrunnlag.
        return Klassifisering("manuell", "grunnkode_mangler",
                              kategori=kategori)
    if kategori not in policykategorier:
        # Kundens policy lister ikke kategorien. Da har ingen bestemt at
        # den skal behandles automatisk hos DENNE kunden.
        return Klassifisering("manuell", "kategori_utenfor_policy",
                              kategori=kategori, grunnkode=grunnkode,
                              detalj={"policykategorier":
                                      sorted(policykategorier)})
    for h in REGISTER:
        if h.behandler(kategori, grunnkode, policykategorier):
            if h.kun_klassifisering:
                return Klassifisering("manuell", "policykrevende", handler=h,
                                      kategori=kategori, grunnkode=grunnkode)
            return Klassifisering("behandle", "handler_funnet", handler=h,
                                  kategori=kategori, grunnkode=grunnkode)
    return Klassifisering("manuell", "ingen_handler",
                          kategori=kategori, grunnkode=grunnkode)


# ---------------------------------------------------------------------------
# Handlerne. Private — kalles kun av `planlegg` i denne modulen.
# ---------------------------------------------------------------------------

def _r1_reinnsending(payload: dict, kl: Klassifisering) -> Reparasjonsplan:
    """Har det manglende grunnlaget kommet på plass?

    R1 sjekker IKKE selv om attestasjonen finnes — det ville vært et
    oppslag mot en autoritativ kilde, og de går gjennom outboxen (v3-delta
    pkt. 5). Den bygger et oppdrag som ber en eiermodul om å utføre
    handlingen på nytt, og handlingen går da gjennom HELE API-veien og
    policyporten en gang til.

    Det er derfor R1 ikke kan bli en bakvei rundt motoren: det den ber om
    er en ny beslutning, ikke en ny utførelse.
    """
    handling = payload.get("handling")
    if not isinstance(handling, str) or not handling:
        return Reparasjonsplan("manuell", "handling_mangler_i_payload")
    t = oppdragsskjema.type_for_handling(handling)
    if t is None:
        return Reparasjonsplan("manuell", "ingen_oppdragstype_for_handling")
    if not any(handling.startswith(p) for p in R1.tillatte_malhandlinger):
        return Reparasjonsplan("manuell", "handling_utenfor_deklarasjonen")
    minimert = oppdragsskjema.minimer(t.navn, payload)
    mangler = oppdragsskjema.mangler_paakrevde(t.navn, minimert)
    if mangler:
        return Reparasjonsplan("manuell", f"oppdrag_ufullstendig:{mangler}")
    return Reparasjonsplan("oppdrag", "reinnsending_planlagt",
                           maalhandling=handling, oppdragstype=t.navn,
                           reparasjonsinput=minimert)


def _r2_lokal_kontroll(payload: dict, kl: Klassifisering) -> Reparasjonsplan:
    """Rene lokale kontroller av data M-37 allerede har.

    Ingen nettverk, ingen autoritative kilder, ingen sideeffekter. At
    payloaden lot seg dekryptere og tolke som et objekt med de feltene
    saksgrunnlaget krever, ER kontrollen — den tekniske feilen som skapte
    saken var i så fall forbigående.

    Går det ikke, tilbake til køen. Forsøkstelleren i databasen avgjør når
    det er nok; denne funksjonen teller ingenting selv, og kan derfor ikke
    komme i utakt med den.
    """
    if not isinstance(payload, dict) or not payload:
        return Reparasjonsplan("manuell", "payload_uleselig")
    if not isinstance(payload.get("handling"), str):
        return Reparasjonsplan("manuell", "payload_mangler_handling")
    if not isinstance(payload.get("kategori"), (str, type(None))):
        return Reparasjonsplan("ko", "payload_kategori_feiltype")
    return Reparasjonsplan("lost", "lokal_kontroll_bestatt",
                           reparasjonsinput={"kontroll": "lokal",
                                             "handling": payload["handling"]})


def _r3_policykrevende(payload: dict, kl: Klassifisering) -> Reparasjonsplan:
    """Beslutningen var riktig. Det finnes ingenting å reparere."""
    return Reparasjonsplan("manuell", "policykrevende_ingen_reparasjon")


_HANDLERE = {
    R1.handler_id: _r1_reinnsending,
    R2.handler_id: _r2_lokal_kontroll,
    R3.handler_id: _r3_policykrevende,
}


def planlegg(kl: Klassifisering, payload: dict) -> Reparasjonsplan:
    """Kjør handleren klassifiseringen pekte ut. Eneste offentlige vei inn.

    Handlerne er private, og det er en Codex-port: kan de kalles utenfra,
    kan noen kjøre en reparasjon uten å ha gått gjennom klassifiseringen —
    og da er hele predikatet fra v4-delta pkt. 6 hoppet over.
    """
    if kl.utfall != "behandle" or kl.handler is None:
        return Reparasjonsplan("manuell", f"ikke_behandlebar:{kl.grunn}")
    handler = _HANDLERE.get(kl.handler.handler_id)
    if handler is None:
        return Reparasjonsplan("manuell", "handler_uten_implementasjon")
    return handler(payload, kl)
