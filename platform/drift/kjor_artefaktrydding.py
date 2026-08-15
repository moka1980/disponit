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
    """Lagrer telleren ATOMISK. Returnerer False hvis den gikk tapt.

    Codex (P2): et direkte skriv trunkerer den ENESTE persisterte telleren før
    den nye JSON-en er komplett. Blir oneshot-prosessen eller verten avbrutt i
    det vinduet, leser neste aktivering en tom eller halv fil, `_les_feiltelling`
    tolker den som 0 — og en alt opptelt feil er glemt, så to sammenhengende
    feilede kjøringer aldri utløser §6-alarmen. Skriv til en temporærfil i
    SAMME katalog (så `os.replace` er en atomisk rename på samme filsystem),
    fsync innholdet, og bytt inn. Da ser en avbrutt kjøring enten den gamle
    eller den nye telleren — aldri en halv.

    Tapet svelges ikke stille: en tilstandsfil som ikke lar seg skrive
    nullstiller §6-alarmen ved hver kjøring, og det må være synlig i kjøringens
    egen linje i stedet for å se ut som en frisk teller.
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
        # Selve renamen fsyncet på katalogen: uten den kan et vertskrasj rett
        # etter byttet gi den GAMLE filen tilbake ved neste oppstart, og
        # atomisiteten over ville vært en halv garanti. Best effort — filsystemer
        # som ikke tillater å åpne katalogen skal ikke gjøre skrivingen til en
        # feilet kjøring.
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
            os.unlink(tmp)      # ingen etterlatte .tmp-filer i tilstandskatalogen
        except OSError:
            pass
        print(json.dumps({"hendelse": "ryddetilstand_skrivefeil",
                          "sti": str(fil), "feil": str(e)}), file=sys.stderr)
        return False


def _koble(dsn: str):
    """Tilkoblingen bak et navn på modulnivå.

    Importen er fortsatt lat (arbeideren skal kunne importeres uten et
    databasebibliotek i hånda), men navnet gir testene et sømpunkt: den
    hoppet over kjøringen måles på det `main()` faktisk gjør med telleren,
    ikke på en etterligning av den.
    """
    from db.pg import koble
    return koble(dsn)


def main() -> int:
    from db.hemmeligheter import last_credentials
    last_credentials()  # PR-009 §5: LoadCredential før env-lesing under
    dsn = os.environ.get("DISPONIT_DOMAINS_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "grunn": "DISPONIT_DOMAINS_URL mangler"}),
              file=sys.stderr)
        return 2

    tidligere = _les_feiltelling()
    try:
        conn = _koble(dsn)
    except Exception:
        # Databasen utilgjengelig ER en feilet kjøring (§6) — telleren skal
        # øke akkurat som ved en feilet `rydd_staged_artefakter()`, ellers
        # utløser en vedvarende tilkoblingsfeil aldri alarmen den skal.
        n = tidligere + 1
        lagret = _skriv_feiltelling(n)
        print(json.dumps({
            "hendelse": "ryddekjoring", "forkastet": 0, "batcher": 0,
            "karantene_bevart": 0, "feilet": 1, "hoppet_over": 0,
            "sammenhengende_feil": n,
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

    if r.hoppet_over:
        # Codex (P2): en kjøring som fant arbeidernøkkelen opptatt har ikke
        # ryddet noe og har heller ikke feilet — telleren skal stå NØYAKTIG som
        # den sto. Skrev vi 0 her, ville en overlappende kjøring (manuell drift,
        # flere verter, en henger som holder låsen) slettet en alt opptelt feil,
        # og §6-alarmen ville aldri nådd to sammenhengende feil. `tilstand_lagret`
        # er sant fordi tilstanden er intakt: ingenting gikk tapt.
        feil_n, lagret = tidligere, True
    else:
        feil_n = tidligere + 1 if r.feilet else 0
        lagret = _skriv_feiltelling(feil_n)
    print(json.dumps({
        "hendelse": "ryddekjoring",
        "forkastet": r.forkastet,
        "batcher": r.batcher,
        "karantene_bevart": r.karantene_bevart,
        "feilet": int(r.feilet),
        "hoppet_over": int(r.hoppet_over),
        "sammenhengende_feil": feil_n,
        "alarm": int(r.alarm_utlost),
        "tilstand_lagret": int(lagret),
    }))
    return 1 if r.feilet else 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
