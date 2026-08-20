#!/usr/bin/env python3
"""Avleder `wcag-kontroll-v1`-artefaktet MEKANISK av evidens.jsonl.

Evidensfila er rundens fulle historie: idempotente gjenkjøringer, røde
iterasjoner som ble reparert, og replays som ikke målte noe. Sammendraget
her er derfor ikke «velg de fine tallene» — det er ett sett NAVNGITTE
utvalgsregler som kan leses, angripes og kjøres om igjen:

  * En MÅLING er den SISTE hendelsen av sin type: runden er idempotent,
    og siste tilstand er rundens tilstand. Unntaket er
    `feilinjisering_motorfeil` med `utfall: "tomt"`: et claim som ikke
    fant noe å feilinjisere målte ingenting, og velges aldri — den siste
    FAKTISKE injeksjonen gjelder.
  * Release og image-digest er de som gjaldt DA sluttmålingen skjedde
    (siste `fase2_ok`/`fase1_ok` før siste `fase5_resultat`), ikke de
    nyeste i fila. Fasitrunden gikk på wcag-r1; alle m56-releasene deler
    digest, så tallene gjelder nøyaktig de aksepterte bytene (A1).
  * `bestatt` regnes ut av de VALGTE hendelsenes egne `ok`-felter —
    aldri påstått.

Deterministisk med vilje: samme fil inn gir byte-identisk artefakt ut
(ingen klokkelesing), så CI kan regenerere og sammenligne.

BRUK: python3 deploy/staging/wcag-kontroll-artefakt.py \
        deploy/staging/artefakter/evidens-wcag-runde-20260818.jsonl \
        [--ut artefakt.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPOROT = Path(__file__).resolve().parents[2]


def les(sti: Path) -> list[dict]:
    return [json.loads(l) for l in sti.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def sammendrag(rader: list[dict], kilde: str, kilde_sha256: str) -> dict:
    def siste(navn: str, kandidat=lambda d: True) -> dict:
        valgte = [d for d in rader if d.get("hendelse") == navn
                  and kandidat(d)]
        if not valgte:
            raise SystemExit(f"AVBRUTT: ingen {navn!r} i evidensfila — "
                             "runden er ufullstendig, ikke grønn")
        return valgte[-1]

    fase5 = siste("fase5_resultat")
    for5 = [d for d in rader if d.get("hendelse") in ("fase1_ok", "fase2_ok")
            and d["ts"] <= fase5["ts"]]
    fase1 = [d for d in for5 if d["hendelse"] == "fase1_ok"][-1]
    fase2 = [d for d in for5 if d["hendelse"] == "fase2_ok"][-1]
    robots = siste("port20_robots")
    robots5 = siste("port20_robots_5xx")
    frekv = siste("port21_frekvens")
    motor = siste("port24_motormiljo")
    injeksjon = siste("feilinjisering_motorfeil",
                      lambda d: d.get("utfall") != "tomt")
    frist = siste("feilinjisering_evidensfrist")

    signert, krav = (int(x) for x in
                     str(fase5["ti_kjoringer_signert_innen_frist"]).split("/"))
    valgte = (fase5, robots, robots5, frekv, motor, injeksjon, frist)
    return {
        "krav_id": "wcag-kontroll-v1",
        "ts": max(d["ts"] for d in valgte),
        "bestatt": all(d.get("ok") is True for d in valgte),
        "oppsett": {
            "modul": "m56_wcag_audit",
            "release": fase2["release"],
            "image_digest": fase1["image_id"].removeprefix("sha256:"),
            "kilde": kilde,
            # SP-11 hele veien: manifestet hash-binder DETTE artefaktet,
            # og artefaktet hash-binder råfilen det er avledet av — et
            # bytte av evidens.jsonl bryter kjeden i CI, ikke i en lesning.
            "kilde_sha256": kilde_sha256,
        },
        "maalt": {
            "kjoringer_signert_innen_frist": signert,
            "kjoringer_krav": krav,
            "avvik_mot_fasit": int(fase5["funn_avvik_mot_fasit"]),
            "robots_private_forisporsler": int(robots["privat_forisporsler"]),
            "robots_5xx_sider_kontrollert": int(robots5["sider_kontrollert"]),
            "robots_5xx_krav": int(robots5["krav"]),
            "frekvens_tillat": sum(1 for u in frekv["utfall"]
                                   if u == "tillat"),
            "frekvens_avvist_over_grense": sum(1 for u in frekv["utfall"]
                                               if u != "tillat"),
            "egress_lekkasjer": len(motor["lekkasjer"]),
            "feilinjisering_feilet_med_kvittering":
                1 if (injeksjon["oppdragstatus"] == "feilet"
                      and injeksjon["har_kvittering"]) else 0,
            "feilinjisering_promoterte_artefakter":
                int(injeksjon["promoterte_artefakter"]),
            "evidensfrist_reapet": len(frist["reapet"]),
            "evidensfrist_sak_opprettet": 1 if frist.get("sak") else 0,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("evidens", type=Path)
    ap.add_argument("--ut", type=Path)
    a = ap.parse_args()
    try:
        kilde = str(a.evidens.resolve().relative_to(REPOROT))
    except ValueError:
        kilde = a.evidens.name
    art = sammendrag(les(a.evidens), kilde,
                     hashlib.sha256(a.evidens.read_bytes()).hexdigest())
    tekst = json.dumps(art, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if a.ut:
        a.ut.write_text(tekst, encoding="utf-8")
        print(f"skrev {a.ut} (bestatt={art['bestatt']})")
    else:
        sys.stdout.write(tekst)
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":
    sys.exit(main())
