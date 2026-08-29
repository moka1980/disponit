"""Fasitkontrollen: motorens utdata mot testnettstedets fasit.json.

Målingen bak `funn.avvik_mot_fasit = 0` (klarsignal §12). Sammenligner
ALT som er deterministisk — funn (regel, alvorlighet, antall), blokkerte
subressurser, avkorting, sidestatus og crawlrekkefølge — og rapporterer
hvert avvik ved navn. Brukes både direkte mot motor-stdout (lokalt) og
mot rapporten i det promoterte artefaktet (staging-runden); formene
deles: begge bærer funn/avkortet, artefaktet under andre feltnavn.

    python fasitkontroll.py <fasit.json> <scenario> < motorutdata.json
"""
from __future__ import annotations

import json
import sys
import urllib.parse


def _entydig(rader, nokkel, verdi, navn: str, ut: list[str]) -> dict:
    """Radene indeksert på `nokkel` — og duplikater ER et avvik.

    DUPLIKATER SLUKTES STILLE (Codex P2, runde 9). Både funn og blokkerte
    ressurser ble lagt i en dict på vei inn, så to rader for samme
    regel_id (eller samme vert/art) endte som ÉN: den siste vant. En
    regrimert motor som sendte `image-alt` to ganger — først med feil
    antall, så med det ventede — ga da null avvik mot fasiten, mens
    `rapport.bygg` beholder begge radene og legger begge antallene inn i
    den promoterte summen. Fasiten har aldri duplikater (den er selv
    nøklet), så en gjentatt rad kan bare bety at motoren avviker; da skal
    den navngis, ikke overskrives."""
    sett = {}
    for r in rader:
        k = nokkel(r)
        if k in sett:
            ut.append(f"{navn}: {k} står flere ganger i motorutdata")
        sett[k] = verdi(r)
    return sett


def avvik(fasit_scenario: dict, motor: dict) -> list[str]:
    ut = []
    s = fasit_scenario

    fakta = _entydig(motor.get("funn", ()),
                     lambda f: f["regel_id"],
                     lambda f: (f["alvorlighet"], f["antall"]),
                     "funn", ut)
    ventet = {rid: (v["alvorlighet"], v["antall"])
              for rid, v in s["funn"].items()}
    for rid in sorted(set(fakta) | set(ventet)):
        if rid not in ventet:
            ut.append(f"funn: uventet regel {rid} {fakta[rid]}")
        elif rid not in fakta:
            ut.append(f"funn: regel {rid} mangler (ventet {ventet[rid]})")
        elif fakta[rid] != ventet[rid]:
            ut.append(f"funn: {rid} er {fakta[rid]}, ventet {ventet[rid]}")

    bl = {}
    for (vert, art), n in _entydig(motor.get("blokkert", ()),
                                   lambda b: (b["vert"], b["art"]),
                                   lambda b: b["antall"],
                                   "blokkert", ut).items():
        bl.setdefault(vert, {})[art] = n
    if bl != s["blokkert"]:
        ut.append(f"blokkert: {bl}, ventet {s['blokkert']}")

    if list(motor.get("avkortet", ())) != list(s["avkortet"]):
        ut.append(f"avkortet: {motor.get('avkortet')}, ventet {s['avkortet']}")

    sider = motor.get("sider", ())
    ok = [x for x in sider if x["status"] == "ok"]
    if len(ok) != s["sider_ok"] or len(sider) != s["sider_ok"]:
        ut.append(f"sider: {len(ok)} ok av {len(sider)},"
                  f" ventet {s['sider_ok']} ok")
    if "_crawlrekkefolge" in s:
        stier = [urllib.parse.urlsplit(x["url"]).path for x in sider]
        if stier != s["_crawlrekkefolge"]:
            ut.append(f"crawlrekkefølge: {stier},"
                      f" ventet {s['_crawlrekkefolge']}")
    return ut


def main() -> int:
    fasit = json.load(open(sys.argv[1], encoding="utf-8"))
    scenario = fasit["scenarier"][sys.argv[2]]
    motor = json.load(sys.stdin)
    funn = avvik(scenario, motor)
    for a in funn:
        print(f"AVVIK: {a}")
    print(json.dumps({"scenario": sys.argv[2], "avvik_mot_fasit": len(funn)}))
    return 1 if funn else 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
