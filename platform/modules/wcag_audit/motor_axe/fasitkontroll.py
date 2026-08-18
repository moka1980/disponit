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


def avvik(fasit_scenario: dict, motor: dict) -> list[str]:
    ut = []
    s = fasit_scenario

    fakta = {f["regel_id"]: (f["alvorlighet"], f["antall"])
             for f in motor.get("funn", ())}
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
    for b in motor.get("blokkert", ()):
        bl.setdefault(b["vert"], {})[b["art"]] = b["antall"]
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
