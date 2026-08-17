"""Controlleren (PR-014c §2): claimer, styrer motoren, pakker resultatet,
laster opp, kvitterer. Den har credentials (modultokenet fra onboarding);
motoren har ingen.

Én kjøring per kall (`kjor_en`) — timerdrevet i drift, direkte i test.
Alle nettverkskall går gjennom en injisert `klient` (httpx-kompatibel /
Starlette TestClient), og signering av kvitteringen gjennom en injisert
`signer` — kontrakten (PR-006) eies av plattformen, ikke av denne fila.

Feilhåndteringens utfall:
  * Ingen oppdrag (204) → stille retur.
  * Motorfeil / skjemabrudd i egen rapport → kvittering `avbrutt` UTEN
    artefakt: et delvis artefakt finnes ikke (§10 siste rad), men
    plattformen skal få vite at oppdraget er FERDIG mislykket — taushet
    ville latt fristen gjøre jobben og M-37 gjette.
  * Opplastingen AVVIST (Codex P1) → samme `avbrutt`, med feilkode
    `opplasting_avvist`. Rapporten ble bygget, men kom ikke frem, og et
    unntak ut av kjøreløkka her ville etterlatt oppdraget claimet uten et
    ord til plattformen.
  * Full suksess → artefakt + kvittering `utfort` med den serverberegnede
    hashen fra opplastingssvaret (den, og bare den, signeres).
  * Kvittering AVVIST (Codex P1) → `ukvittert`. Kvitteringsendepunktet er
    ikke en varsling som alltid går igjennom: fencing, hashvalidering og
    artefaktpromotering kan avvise den (409), og serveren kan feile (5xx).
    Da er oppdraget IKKE ferdig hos plattformen — artefaktet kan til og
    med være karantenesatt — og et `utfort` herfra ville fått en
    planlegger til å tro at kjøringen var i havn. Utfallet er eget nettopp
    fordi det ikke er det samme som en ærlig motorfeil.
"""
from __future__ import annotations

import jsonschema

from . import rapportskjema
from .motor import Motorfeil
from .rapport import bygg


def _kvittert(rk) -> bool:
    """Godtok plattformen kvitteringen? Kun 2xx teller — 409 (fencing,
    hashavvik, avvist promotering) og 5xx betyr at oppdraget står igjen
    uferdig hos plattformen."""
    return 200 <= rk.status_code < 300


def kjor_en(klient, token: str, motor, kontekst: dict, signer) -> dict:
    """-> {"utfall": "tomt"|"utfort"|"avbrutt"|"ukvittert", ...}."""
    hode = {"authorization": f"Bearer {token}"}
    r = klient.post("/v1/oppdrag/claim", json={}, headers=hode)
    if r.status_code == 204:
        return {"utfall": "tomt"}
    r.raise_for_status()
    claim = r.json()
    payload = claim["payload"]

    kvittering_basis = {
        "oppdrag_id": claim["oppdrag_id"], "tenant": claim["tenant"],
        "kvittering_jti": claim["kvittering_jti"],
        "repair_operation_id": claim["repair_operation_id"],
        "owner_claim_id": claim["owner_claim_id"],
        "owner_generation": claim["owner_generation"],
        "ressurs_id": payload.get("ressurs_id", ""),
    }

    def kvitter(kropp):
        rk = klient.post("/v1/oppdrag/kvittering", json=signer(kropp),
                         headers=hode)
        return rk

    try:
        resultat = motor.kjor(payload)
        rapport = bygg(resultat, payload=payload, kontekst=kontekst)
        # Egen validering FØR innsending: serveren validerer uansett (mot
        # samme innholdsadresserte skjema), men modulen skal aldri sende
        # noe den selv kan se er ugyldig — og en avvist opplasting her er
        # en MOTORFEIL sett fra oppdraget.
        jsonschema.Draft202012Validator(rapportskjema.SKJEMA).validate(
            rapport)
    except (Motorfeil, jsonschema.ValidationError) as e:
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "motor_avbrutt"})
        return {"utfall": "avbrutt", "grunn": type(e).__name__,
                "kvittering_status": rk.status_code,
                "kvittert": _kvittert(rk)}

    opplasting = claim.get("opplasting")
    if not opplasting:
        # Claim uten opplastingskapabilitet for denne typen er en
        # konfigurasjonstilstand (port 15-adferden på plattformsiden):
        # rapporten kan ikke leveres, oppdraget feiler ærlig.
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "ingen_opplastingskapabilitet"})
        return {"utfall": "avbrutt", "grunn": "ingen_kapabilitet",
                "kvittering_status": rk.status_code,
                "kvittert": _kvittert(rk)}

    ro = klient.post("/v1/artefakt",
                     json={"kapabilitet_jti": opplasting["jti"],
                           "rapport": rapport}, headers=hode)
    if not 200 <= ro.status_code < 300:
        # AVVIST OPPLASTING (Codex P1): `ro.raise_for_status()` kastet ut
        # av kjøreløkka uten kvittering, og oppdraget ble stående claimet
        # til fristen — akkurat den taushetslinjen §10 forbyr, og det
        # motsatte av det denne fila selv sier («en avvist opplasting her
        # er en MOTORFEIL sett fra oppdraget»). Rapporten er bygget, men
        # den kom ikke frem: da er oppdraget ærlig mislykket.
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "opplasting_avvist"})
        return {"utfall": "avbrutt", "grunn": "opplasting_avvist",
                "opplasting_status": ro.status_code,
                "kvittering_status": rk.status_code,
                "kvittert": _kvittert(rk)}
    artefakt = ro.json()

    rk = kvitter({**kvittering_basis, "resultat": "utfort",
                  "artefakt_id": artefakt["artefakt_id"],
                  "klartekst_sha256": artefakt["klartekst_sha256"]})
    svar = {"artefakt_id": artefakt["artefakt_id"],
            "kvittering_status": rk.status_code,
            "sider": len(rapport["sider_kontrollert"])}
    if not _kvittert(rk):
        # Rapporten er bygget og lastet opp, men plattformen tok IKKE imot
        # kvitteringen — oppdraget er uferdig der, og artefaktet kan være
        # karantenesatt. Å melde `utfort` her ville vært modulens ord mot
        # plattformens tilstand, og planleggeren ville trodd på modulen.
        return {"utfall": "ukvittert", **svar}
    return {"utfall": "utfort", **svar}
