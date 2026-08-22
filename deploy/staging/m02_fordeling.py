"""Det syntetiske beslutningssettet for m02-fordelingsartefaktet.

ETT sett, TO ledd (m02-aksept-klarsignalet §3, premiss korrigert mot
basen 2026-08-21): m01-rundens opprinnelige 180 rader finnes ikke i
disponit-srv-basen — de ble aldri med fra gammel staging, og hele basen
har null STOPP. «Fordelingen 84/3/93 over 180 hendelser» er derfor
REPRODUSERT, ikke mimret: settet under drives gjennom den EKTE
beslutningsveien (/v1/beslutning) både lokalt (CI,
`test_m02_fordeling.py` — det er «likt lokalt»-leddet, stående) og på
staging (`m02-fordeling-artefakt.py` — artefaktets ledd). Fordi begge
ledd bruker NØYAKTIG dette settet, kan de aldri gli fra hverandre — og
at det ER nøyaktig dette settet, er MÅLT og ikke påstått: artefaktet
bærer `sett_sha256` (se under), og porten krever likhet med de
innsjekkede bytene.

Kategoriene er beslutningsveiens egne, fra m01-portene:

  * TILLAT — gyldig hendelse med signerte attestasjoner.
  * STOPP  — tuklet attestasjon (`resultat` snudd uten ny signering):
             signaturporten feller den, og det er nettopp den
             sikkerhetsveien som skal ETTERLATE en STOPP-rad.
  * UNNTAK — ukjent handling uten attestasjoner: deny-by-default.
             (Uten attestasjoner med vilje — med dem hadde hendelsen
             truffet BINDINGSPORTEN og blitt sikkerhetssak i stedet;
             samme lærdom som test_api-porten dokumenterer.)

Driveren MÅLER hvert svar (fail-closed): et sett der én hendelse fikk
en annen beslutning enn kategorien lover, er ikke settet — da avbrytes
kjøringen i stedet for å telle videre. Radene identifiseres etterpå i
`revisjonslogg` via idempotensnøklene, som bærer rundens id.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

SETT_VERSJON = "m02-sett-1"
FORDELING = {"TILLAT": 84, "STOPP": 3, "UNNTAK": 93}


def sett_sha256() -> str:
    """Settets identitet — BYTENE i denne filen.

    `SETT_VERSJON` er en streng noen skriver for hånd, og den sa bare at
    to kjøringer MENTE å drive samme sett. Radene i artefaktet bærer
    loggpost-id og beslutning, ikke hendelsene som ble sendt inn — så et
    staging-ledd som kjørte en eldre utrulling eller en lokalt endret
    driver kunne produsere 84/3/93 av HELT andre hendelser og likevel
    valideres som «likt lokalt». Det er nettopp den likheten punktet
    handler om.

    Derfor bæres bytene: staging-leddet hasher driveren det faktisk
    kjørte, og porten (`_grenser_m02_fordeling`) krever likhet med de
    innsjekkede bytene CI driver — samme form som `datasett_sha256`/§1.2
    for WCAG-datasettet. Ett tall, to ledd: glir de fra hverandre, er
    punktet rødt.
    """
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def bygg_sett() -> list[tuple[str, int]]:
    """-> [(forventet_beslutning, løpenummer)] — 180, deterministisk."""
    ut: list[tuple[str, int]] = []
    for beslutning, antall in sorted(FORDELING.items()):
        ut += [(beslutning, i) for i in range(antall)]
    return ut


def idemnokkel(runde_id: str, beslutning: str, i: int) -> str:
    return f"m02f-{runde_id}-{beslutning.lower()}-{i:03d}"


def kjor_sett(runde_id: str, post, hendelse, hendelse_uten,
              tukle, pause_s: float = 0.0) -> dict[str, int]:
    """Driver settet gjennom beslutningsveien. -> antall per beslutning.

    Avhengighetene er injisert så CI-leddet og staging-leddet bruker
    hver sin transport, men SAMME sett og SAMME dom:
      post(kropp_event, idemnokkel) -> (status, beslutning)
      hendelse(ressurs)             -> gyldig hendelse (TILLAT-form)
      hendelse_uten(handling, ressurs) -> uten attestasjoner (UNNTAK)
      tukle(e)                      -> hendelsen med snudd attestasjon
      pause_s: pacing mellom kallene. Staging-leddet må holde seg under
      nginx-sonen `disponit_general` (600r/m = 10/s, burst 100) — 180
      upacede kall sprenger den (Codex P1, #131 r3). CI-leddet kjører
      in-process uten nginx og pacer ikke.
    """
    import time
    talt: dict[str, int] = {}
    for forventet, i in bygg_sett():
        # `runde_id` MÅ stå i ressursen, ikke bare i idempotensnøkkelen:
        # purring.send har `frekvens: {maks: 1, periode: 14 dager,
        # grupperingsnokkel: faktura_id}`, og ressursen ER faktura_id-en.
        # Uten runden ville hver TILLAT-hendelse i kjøring nummer to
        # gjenbrukt den gruppen kjøring nummer én alt hadde brukt opp, og
        # fått `frekvensgrense_naadd`/UNNTAK i stedet for TILLAT — en fersk
        # idempotensnøkkel hjelper ikke mot en brukt frekvensgruppe. Med
        # runden inne er to bevisrunder uavhengige.
        ressurs = f"m02f-{runde_id}-{forventet.lower()}-{i:03d}"
        if forventet == "TILLAT":
            e = hendelse(ressurs)
        elif forventet == "STOPP":
            e = tukle(hendelse(ressurs))
        else:
            e = hendelse_uten(f"m02.finnes.ikke.{i}", ressurs)
        if pause_s:
            time.sleep(pause_s)
        status, beslutning = post(e, idemnokkel(runde_id, forventet, i))
        if status != 200 or beslutning != forventet:
            raise SystemExit(
                f"AVBRUTT: hendelse {forventet}/{i} fikk"
                f" ({status}, {beslutning!r}) — settet er ikke settet,"
                " og en fordeling av noe annet telles ikke")
        talt[beslutning] = talt.get(beslutning, 0) + 1
    return talt


def artefakt(rader: list[tuple[int, str]], tenant: str, vert: str,
             ts: str, bevisrot: str) -> dict:
    """Artefaktet, med tallene REGNET AV RADENE — aldri av driveren.

    `rader` er [(loggpost_id, beslutning)] lest ut av `revisjonslogg`
    ETTER kjøringen (via idempotensnøklene). Sprikter tellingen fra
    fasiten, skrives artefaktet likevel — porten i KRAVGRENSER feller
    det, og et rødt artefakt som finnes er ærligere enn et grønt som
    ble valgt.
    """
    talt: dict[str, int] = {}
    for _, beslutning in rader:
        talt[beslutning] = talt.get(beslutning, 0) + 1
    return {
        "krav_id": "m02-fordeling-v1",
        "ts": ts,
        "bestatt": talt == FORDELING and len(rader) == sum(
            FORDELING.values()),
        "oppsett": {"modul": "m02_revisjonslogg", "tenant": tenant,
                    "vert": vert, "sett_versjon": SETT_VERSJON,
                    # ... og settets IDENTITET, ikke bare dets navn:
                    # `sett_versjon` er håndholdt, `sett_sha256` er bytene
                    # som faktisk drev hendelsene.
                    "sett_sha256": sett_sha256(),
                    # Tillitsgrensens anker (#131-K2/#132): hele
                    # produsentflaten som innsjekkede bytes — kalleren
                    # regner den med manifestskjema.m02_bevisrot_sha256,
                    # og porten re-regner over sitt eget tre.
                    "bevisrot_sha256": bevisrot},
        "maalt": {"antall_tillat": talt.get("TILLAT", 0),
                  "antall_stopp": talt.get("STOPP", 0),
                  "antall_unntak": talt.get("UNNTAK", 0)},
        "rader": [[lid, beslutning] for lid, beslutning in
                  sorted(rader)],
    }
