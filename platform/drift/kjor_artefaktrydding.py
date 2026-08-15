"""Inngangspunkt for `disponit-artefaktrydding.service`.

Telleren for sammenhengende feil lever i en liten tilstandsfil, ikke i minnet:
hver kjøring er en egen prosess (`Type=oneshot`), så «to feilede kjøringer på
rad» kan ikke observeres av kjøringen selv. Uten filen ville alarmen i §6 vært
umulig å utløse — den ville krevd at én prosess overlevde begge feilene.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import artefaktrydding

def _tilstandsfil() -> Path:
    """Hvor feiltelleren ligger.

    Unit-filen setter `StateDirectory=disponit`, så systemd oppretter
    katalogen med arbeiderens egen eier FØR ExecStart og oppgir den i
    $STATE_DIRECTORY. Uten den var stien hardkodet under root-eide
    /var/lib, `mkdir` feilet for `disponit-domener`, og telleren ble
    lest som 0 hver gang — alarmen etter to feil kunne aldri utløses.
    Den hardkodede stien beholdes kun som fallback for kjøring utenfor
    systemd; $DISPONIT_RYDDETILSTAND overstyrer alt (tester, manuell drift).
    """
    eksplisitt = os.environ.get("DISPONIT_RYDDETILSTAND")
    if eksplisitt:
        return Path(eksplisitt)
    statedir = os.environ.get("STATE_DIRECTORY")
    if statedir:
        # systemd oppgir en kolonseparert liste når flere er deklarert.
        return Path(statedir.split(":")[0]) / "artefaktrydding.json"
    return Path("/var/lib/disponit/artefaktrydding.json")


def _les_feiltelling() -> int:
    try:
        return int(json.loads(_tilstandsfil().read_text(encoding="utf-8"))["feil"])
    except Exception:
        # Manglende/ødelagt fil betyr «vi vet ikke om forrige kjøring feilet».
        # Da er 0 riktig: en alarm som utløses av en tapt fil er en falsk
        # alarm, og §6 handler om en jobb som faktisk har vært nede to ganger.
        return 0


def _skriv_feiltelling(n: int) -> bool:
    """Lagrer telleren. Returnerer False hvis den gikk tapt.

    Tapet svelges ikke stille lenger: en tilstandsfil som ikke lar seg
    skrive nullstiller §6-alarmen ved hver kjøring, og det må være synlig
    i kjøringens egen linje i stedet for å se ut som en frisk teller.
    """
    fil = _tilstandsfil()
    try:
        fil.parent.mkdir(parents=True, exist_ok=True)
        fil.write_text(json.dumps({"feil": n}), encoding="utf-8")
        return True
    except OSError as e:
        print(json.dumps({"hendelse": "ryddetilstand_skrivefeil",
                          "sti": str(fil), "feil": str(e)}), file=sys.stderr)
        return False


def main() -> int:
    from db.hemmeligheter import last_credentials
    from db.pg import koble
    last_credentials()  # PR-009 §5: LoadCredential før env-lesing under
    dsn = os.environ.get("DISPONIT_DOMAINS_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "grunn": "DISPONIT_DOMAINS_URL mangler"}),
              file=sys.stderr)
        return 2

    tidligere = _les_feiltelling()
    try:
        conn = koble(dsn)
    except Exception:
        # Databasen utilgjengelig ER en feilet kjøring (§6) — telleren skal
        # øke akkurat som ved en feilet `rydd_staged_artefakter()`, ellers
        # utløser en vedvarende tilkoblingsfeil aldri alarmen den skal.
        n = tidligere + 1
        lagret = _skriv_feiltelling(n)
        print(json.dumps({
            "hendelse": "ryddekjoring", "forkastet": 0, "batcher": 0,
            "karantene_bevart": 0, "feilet": 1, "sammenhengende_feil": n,
            "alarm": int(n >= artefaktrydding.ALARM_ETTER_FEIL),
            "tilstand_lagret": int(lagret),
            "grunn": "tilkobling_feilet",
        }))
        return 1

    try:
        # §6: 500 per KJØRING, ikke per batch. `maks_batcher=1` sammen med
        # `grense=BATCHGRENSE` (500) er selve grensen — flere batcher her ville
        # latt én timeraktivering forkaste et multiplum av 500.
        r = artefaktrydding.kjor(conn, maks_batcher=1, tidligere_feil=tidligere)
    except Exception:
        # Siste skanse (Codex): slipper et unntak likevel ut av `kjor()`, er
        # kjøringen feilet — og telleren MÅ persisteres her, ellers nullstiller
        # hver feilende kjøring §6-alarmen den skulle bygge opp mot. Uten dette
        # var «to sammenhengende feilede kjøringer» avhengig av at ingen ny
        # feilvei noen gang oppsto inne i arbeideren.
        r = artefaktrydding.Ryddresultat(
            feilet=True,
            alarm_utlost=tidligere + 1 >= artefaktrydding.ALARM_ETTER_FEIL)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    lagret = _skriv_feiltelling(tidligere + 1 if r.feilet else 0)
    print(json.dumps({
        "hendelse": "ryddekjoring",
        "forkastet": r.forkastet,
        "batcher": r.batcher,
        "karantene_bevart": r.karantene_bevart,
        "feilet": int(r.feilet),
        "sammenhengende_feil": tidligere + 1 if r.feilet else 0,
        "alarm": int(r.alarm_utlost),
        "tilstand_lagret": int(lagret),
    }))
    return 1 if r.feilet else 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
