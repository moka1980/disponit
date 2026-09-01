"""Inngangspunkt for `disponit-lagermaaling.service` (M-4, 093).

Telleren for sammenhengende feil lever i en tilstandsfil, ikke i minnet:
hver kjøring er en egen prosess (`Type=oneshot`), så «to feilede kjøringer
på rad» kan ikke observeres av kjøringen selv. Formen er
`kjor_artefaktrydding.py` sin, ordrett — inkludert det atomiske skrivet:
et direkte skriv trunkerer den ENESTE persisterte telleren før den nye
JSON-en er komplett, og en avbrutt kjøring i det vinduet leser den som 0.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import retensjonsmaaling


def _tilstandsfil() -> Path:
    """Hvor feiltelleren ligger.

    Unit-filen setter `StateDirectory=disponit`, så systemd oppretter
    katalogen med arbeiderens egen eier FØR ExecStart og oppgir den i
    $STATE_DIRECTORY. Den hardkodede stien er kun fallback for kjøring
    utenfor systemd; $DISPONIT_MAALETILSTAND overstyrer alt.
    """
    eksplisitt = os.environ.get("DISPONIT_MAALETILSTAND")
    if eksplisitt:
        return Path(eksplisitt)
    statedir = os.environ.get("STATE_DIRECTORY")
    if statedir:
        return Path(statedir.split(":")[0]) / "retensjonsmaaling.json"
    return Path("/var/lib/disponit/retensjonsmaaling.json")


def _les_feiltelling() -> int:
    try:
        return int(json.loads(
            _tilstandsfil().read_text(encoding="utf-8"))["feil"])
    except Exception:
        # Manglende/ødelagt fil betyr «vi vet ikke om forrige kjøring
        # feilet». Da er 0 riktig: en alarm som utløses av en tapt fil er
        # en falsk alarm.
        return 0


def _skriv_feiltelling(n: int) -> bool:
    """Lagrer telleren ATOMISK. Returnerer False hvis den gikk tapt.

    Tapet svelges ikke stille: en tilstandsfil som ikke lar seg skrive
    nullstiller alarmen ved hver kjøring, og det må være synlig i
    kjøringens egen linje i stedet for å se ut som en frisk teller.
    """
    fil = _tilstandsfil()
    tmp = fil.with_name(f"{fil.name}.{os.getpid()}.tmp")
    try:
        fil.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps({"feil": n}))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, fil)
        try:
            kat = os.open(str(fil.parent), os.O_RDONLY)
            try:
                os.fsync(kat)
            finally:
                os.close(kat)
        except OSError:
            pass
        return True
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        print(json.dumps({"hendelse": "maaletilstand_skrivefeil",
                          "sti": str(fil), "feil": str(e)}), file=sys.stderr)
        return False


def _koble(dsn: str):
    """Tilkoblingen bak et navn på modulnivå — testenes sømpunkt."""
    from db.pg import koble
    return koble(dsn)


def main() -> int:
    from db.hemmeligheter import last_credentials
    last_credentials()  # PR-009 §5: LoadCredential før env-lesing under
    dsn = os.environ.get("DISPONIT_LAGERMAALER_URL")
    if not dsn:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "grunn": "DISPONIT_LAGERMAALER_URL mangler"}),
              file=sys.stderr)
        return 2

    tidligere = _les_feiltelling()
    try:
        conn = _koble(dsn)
    except Exception:
        # Databasen utilgjengelig ER en feilet kjøring — telleren skal øke
        # akkurat som ved en feilet måling, ellers utløser en vedvarende
        # tilkoblingsfeil aldri alarmen den skal.
        n = tidligere + 1
        lagret = _skriv_feiltelling(n)
        print(json.dumps({
            "hendelse": "retensjonsmaaling", "maaling_id": None,
            "malt": 0, "umaalbare": 0, "ferdig": 0, "feilet": 1,
            "hoppet_over": 0, "sammenhengende_feil": n,
            "alarm": int(n >= retensjonsmaaling.ALARM_ETTER_FEIL),
            "tilstand_lagret": int(lagret),
            "grunn": "tilkobling_feilet",
        }))
        return 1

    try:
        r = retensjonsmaaling.kjor(conn, tidligere_feil=tidligere)
    except Exception:
        # Siste skanse: slipper et unntak likevel ut av `kjor()`, er
        # kjøringen feilet — og telleren MÅ persisteres her, ellers
        # nullstiller hver feilende kjøring alarmen den skulle bygge
        # opp mot.
        r = retensjonsmaaling.Maaleresultat(
            feilet=True,
            alarm_utlost=tidligere + 1 >= retensjonsmaaling.ALARM_ETTER_FEIL)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if r.hoppet_over:
        # En kjøring som fant arbeidernøkkelen opptatt har ikke målt noe
        # og har heller ikke feilet — telleren skal stå NØYAKTIG som den
        # sto. `tilstand_lagret` er sant fordi tilstanden er intakt.
        feil_n, lagret = tidligere, True
    else:
        feil_n = tidligere + 1 if r.feilet else 0
        lagret = _skriv_feiltelling(feil_n)
    print(json.dumps({
        "hendelse": "retensjonsmaaling",
        "maaling_id": r.maaling_id,
        "malt": r.malt,
        "umaalbare": r.umaalbare,
        # Lagrene som ikke lot seg måle NAVNGIS i linjen. Et tall alene
        # ville krevd et databaseoppslag for å finne ut hva som er galt.
        "umaalbare_lagre": r.umaalbare_lagre,
        "ferdig": int(r.ferdig),
        "feilet": int(r.feilet),
        "hoppet_over": int(r.hoppet_over),
        "sammenhengende_feil": feil_n,
        "alarm": int(r.alarm_utlost),
        "tilstand_lagret": int(lagret),
    }))
    return 1 if r.feilet else 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
