#!/usr/bin/env python3
"""Kjør en M-31-evaluering: mål en kandidat-digest mot et registrert
golden-sett, og registrer kjøringen gjennom den herdede døren
(`registrer_evalueringskjoring`) — som BEREGNER `bestatt` selv mot
gjeldende krav (signaturen har ingen bestatt-parameter).

    DISPONIT_MIGRATOR_URL=… DISPONIT_M31_MODELL_URL=… \\
    DISPONIT_M31_MODELLNAVN=… python3 deploy/staging/kjor-m31-evaluering.py \\
        <modul> <artifact_digest> <sti-til-sett.json>

Rekkefølgen er porten (KRAVGRENSER m31-v1: sett_hash_avvik_akseptert=0):
filens KANONISKE hash slås opp mot det registrerte hodet FØR første
modellkall — et sett som avviker fra registreringen (eller aldri ble
registrert) stopper her, uten at en eneste tekst forlater maskinen.

`artifact_digest` er KONFIGURASJONENS påstand om hvilken modell dette
er (m57-formen: `ollama show`-manifestets sha256) — klienten finner den
aldri på selv. Modellklienten er GJENBRUKT `modules.m57_ats.modell.
Ollamamodell` mot DISPONIT_M31_MODELL_URL (lokal server — persondata
forlater aldri serveren, og golden-settene er uansett syntetiske).

Scoring (v1-adapteren for m57, `m31.golden.eksempel_bestatt`): EKSAKT
match på `oppfylt`-mappen + MENGDELIKHET på funn-kategoriene. En
`Modellfeil` teller som modellfeil (og ikke bestått); enhver ANNEN feil
avbryter kjøringen FØR døren — en avbrutt kjøring registrerer ingenting
(KRAVGRENSER: kjoring.delvis_registrert = 0, dørens egen dom i
tillegg). Per-eksempel-resultatene skrives til
`<sett>.kjoring-<id>.json`, og `detalj_hash` i raden pinner dem.

Exit: 0 ved bestått, 1 ved ikke bestått (også når ingen gjeldende krav
finnes — runbook-seedens målekjøring er en MÅLING, ikke en bestått
port), 2 ved bruksfeil.
"""
import json
import os
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(os.environ.get("DISPONIT_REPO",
                           Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

from m31 import golden  # noqa: E402


def _standard_modellklient(base_url: str, modellnavn: str, digest: str):
    from modules.m57_ats.modell import Ollamamodell
    return Ollamamodell(base_url, modellnavn, digest)


def main(argv: list[str] | None = None, modellfabrikk=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    modul, digest, sti = argv
    try:
        eksempler, innhold_hash = golden.les_sett(Path(sti))
    except golden.Settfeil as feil:
        raise SystemExit(f"settet avvist: {feil}")
    dsn = os.environ["DISPONIT_MIGRATOR_URL"]

    # PORT FØRST: filens kanoniske hash mot det registrerte hodet — FØR
    # første modellkall. Slår oppslaget feil, har ingenting skjedd.
    with psycopg.connect(dsn) as c:
        hode = c.execute(
            "SELECT sett_id, versjon, antall_eksempler FROM golden_sett"
            " WHERE modul_id = %s AND innhold_hash = %s",
            (modul, innhold_hash)).fetchone()
    if hode is None:
        raise SystemExit(
            f"hash-avvik: settet på disk ({innhold_hash[:12]}…) er ikke"
            f" registrert for {modul} — registrer det (eller finn riktig"
            " fil) før kjøring; ingen modellkall er gjort")
    sett_id, sett_versjon, antall_registrert = hode
    if antall_registrert != len(eksempler):
        raise SystemExit(
            f"hash-avvik: hodet sier {antall_registrert} eksempler, fila"
            f" har {len(eksempler)} — ingen modellkall er gjort")

    if modellfabrikk is None:
        modellfabrikk = _standard_modellklient
        base_url = os.environ["DISPONIT_M31_MODELL_URL"]
        modellnavn = os.environ["DISPONIT_M31_MODELLNAVN"]
    else:
        base_url = os.environ.get("DISPONIT_M31_MODELL_URL", "injisert")
        modellnavn = os.environ.get("DISPONIT_M31_MODELLNAVN", "injisert")
    klient = modellfabrikk(base_url, modellnavn, digest)

    from modules.m57_ats.modell import Modellfeil

    # HELE settet kjøres — en delvis kjøring er uregistrerbar i døren,
    # og en avbrutt (uventet unntak) når aldri døren.
    startet = datetime.now(timezone.utc)
    resultater = []
    tider_ms = []
    antall_bestatt = 0
    antall_modellfeil = 0
    for eks in eksempler:
        t0 = time.monotonic()
        try:
            svar = klient.vurder(eks["tekst"], eks["vekter"])
        except Modellfeil as feil:
            ms = int((time.monotonic() - t0) * 1000)
            tider_ms.append(ms)
            antall_modellfeil += 1
            resultater.append({"id": eks["id"], "utfall": "modellfeil",
                               "kode": feil.kode, "ms": ms})
            continue
        ms = int((time.monotonic() - t0) * 1000)
        tider_ms.append(ms)
        bestatt = golden.eksempel_bestatt(svar, eks)
        antall_bestatt += 1 if bestatt else 0
        resultater.append({"id": eks["id"],
                           "utfall": "bestatt" if bestatt else "avvik",
                           "ms": ms, "svar": svar})
    avsluttet = datetime.now(timezone.utc)
    p50 = int(statistics.median(tider_ms))
    # p95 uten interpolasjon: verdien på 95-persentilindeksen av de
    # målte tidene — en målt tid, aldri et regnet mellomtall.
    sortert = sorted(tider_ms)
    p95 = sortert[min(len(sortert) - 1, (len(sortert) * 95) // 100)]
    varighet_s = round((avsluttet - startet).total_seconds(), 3)

    kjoring_id = uuid.uuid4()
    detalj_hash = golden.kanonisk_hash(resultater)
    detaljsti = Path(sti).with_name(
        f"{Path(sti).name}.kjoring-{kjoring_id}.json")
    detaljsti.write_text(json.dumps(resultater, ensure_ascii=False,
                                    sort_keys=True, indent=2) + "\n",
                         encoding="utf-8")

    with psycopg.connect(dsn) as c:
        c.execute("SET ROLE disponit_modules_admin")
        bestatt = c.execute(
            "SELECT registrer_evalueringskjoring(%s, %s, %s, %s, %s, %s,"
            " %s, %s, %s, %s, %s, %s::numeric, %s, %s, %s, %s, 'deploy')",
            (modul, kjoring_id, digest, sett_id, sett_versjon,
             innhold_hash, len(eksempler), antall_bestatt,
             antall_modellfeil, p50, p95, varighet_s,
             getattr(klient, "modellnavn", modellnavn), detalj_hash,
             startet, avsluttet)).fetchone()[0]
        c.commit()
    print(f"kjøring {kjoring_id}: {modul} {sett_id} v{sett_versjon} —"
          f" {antall_bestatt}/{len(eksempler)} bestått,"
          f" {antall_modellfeil} modellfeil, p50 {p50} ms, p95 {p95} ms"
          f" → {'BESTÅTT' if bestatt else 'IKKE BESTÅTT'}"
          f" (detaljer: {detaljsti.name}, hash {detalj_hash[:12]}…)")
    return 0 if bestatt else 1


if __name__ == "__main__":
    raise SystemExit(main())
