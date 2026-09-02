"""Inngangspunkt for `disponit-tilgangssveip.service` (M-12, 097).

Telleren for sammenhengende feil lever i en liten tilstandsfil, ikke i
minnet: hver kjøring er en egen prosess (`Type=oneshot`), så «to feilede
kjøringer på rad» kan ikke observeres av kjøringen selv. Uten filen
ville alarmen vært umulig å utløse — den ville krevd at én prosess
overlevde begge feilene. Formen er `kjor_artefaktrydding.py` sin
(via 095s `kjor_begrepssveip.py`), ordrett, inkludert den ATOMISKE
skrivingen: et direkte skriv trunkerer den eneste persisterte telleren,
og en avbrutt kjøring i det vinduet leser den som 0 ved neste
aktivering.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import tilgangssveip


def _tilstandsfil() -> Path:
    """Hvor feiltelleren ligger.

    Unit-filen setter `StateDirectory=disponit`, så systemd oppretter
    katalogen med arbeiderens egen eier FØR ExecStart og oppgir den i
    $STATE_DIRECTORY. Den hardkodede stien beholdes kun som fallback for
    kjøring utenfor systemd; $DISPONIT_TILGANGSSVEIPTILSTAND overstyrer
    alt (tester, manuell drift).
    """
    eksplisitt = os.environ.get("DISPONIT_TILGANGSSVEIPTILSTAND")
    if eksplisitt:
        return Path(eksplisitt)
    statedir = os.environ.get("STATE_DIRECTORY")
    if statedir:
        # systemd oppgir en kolonseparert liste når flere er deklarert.
        return Path(statedir.split(":")[0]) / "tilgangssveip.json"
    return Path("/var/lib/disponit/tilgangssveip.json")


def _les_feiltelling() -> int:
    try:
        return int(json.loads(
            _tilstandsfil().read_text(encoding="utf-8"))["feil"])
    except Exception:
        # Manglende/ødelagt fil betyr «vi vet ikke om forrige kjøring
        # feilet». Da er 0 riktig: en alarm som utløses av en tapt fil er
        # en falsk alarm, og alarmen handler om en jobb som faktisk har
        # vært nede to ganger.
        return 0


def _skriv_feiltelling(n: int) -> bool:
    """Lagrer telleren ATOMISK. Returnerer False hvis den gikk tapt.

    Skriv til en temporærfil i SAMME katalog (så `os.replace` er en
    atomisk rename på samme filsystem), fsync innholdet, og bytt inn. Da
    ser en avbrutt kjøring enten den gamle eller den nye telleren — aldri
    en halv. Tapet svelges ikke stille: en tilstandsfil som ikke lar seg
    skrive nullstiller alarmen ved hver kjøring, og det må være synlig i
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
        # Selve renamen fsyncet på katalogen: uten den kan et vertskrasj
        # rett etter byttet gi den GAMLE filen tilbake ved neste
        # oppstart. Best effort — filsystemer som ikke tillater å åpne
        # katalogen skal ikke gjøre skrivingen til en feilet kjøring.
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
            os.unlink(tmp)   # ingen etterlatte .tmp-filer i tilstandskatalogen
        except OSError:
            pass
        print(json.dumps({"hendelse": "tilgangssveiptilstand_skrivefeil",
                          "sti": str(fil), "feil": str(e)}), file=sys.stderr)
        return False


def _koble(dsn: str):
    """Tilkoblingen bak et navn på modulnivå.

    Importen er fortsatt lat (arbeideren skal kunne importeres uten et
    databasebibliotek i hånda), men navnet gir testene et sømpunkt: den
    hoppet over kjøringen måles på det `main()` faktisk gjør med
    telleren, ikke på en etterligning av den.
    """
    from db.pg import koble
    return koble(dsn)


def main() -> int:
    from db.hemmeligheter import last_credentials
    last_credentials()  # PR-009 §5: LoadCredential før env-lesing under
    dsn = os.environ.get("DISPONIT_TILGANGSSVEIP_URL")
    if not dsn:
        # INGEN fallback til DATABASE_URL. Runtime-rollen har med vilje
        # ikke EXECUTE på sveipen (097 REVOKEr den), så en fallback ville
        # bare byttet en tydelig oppstartsnekt mot «permission denied» i
        # journalen hver natt — og en jobb som feiler likt hver natt er en
        # jobb ingen leser.
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "grunn": "DISPONIT_TILGANGSSVEIP_URL mangler"}),
              file=sys.stderr)
        return 2

    tidligere = _les_feiltelling()
    try:
        conn = _koble(dsn)
    except Exception:
        # Databasen utilgjengelig ER en feilet kjøring — telleren skal
        # øke akkurat som ved en feilet `m12_sveip_gjennomganger()`,
        # ellers utløser en vedvarende tilkoblingsfeil aldri alarmen den
        # skal.
        n = tidligere + 1
        lagret = _skriv_feiltelling(n)
        print(json.dumps({
            "hendelse": "tilgangssveip", "tenanter": 0, "nye_funn": 0,
            "oppdaterte_funn": 0, "lukkede_funn": 0, "avkortet": 0,
            "feilet": 1, "hoppet_over": 0, "sammenhengende_feil": n,
            "alarm": int(n >= tilgangssveip.ALARM_ETTER_FEIL),
            "tilstand_lagret": int(lagret),
            "grunn": "tilkobling_feilet",
        }))
        return 1

    try:
        r = tilgangssveip.kjor(conn, tidligere_feil=tidligere)
    except Exception:
        # Siste skanse: slipper et unntak likevel ut av `kjor()`, er
        # kjøringen feilet — og telleren MÅ persisteres her, ellers
        # nullstiller hver feilende kjøring alarmen den skulle bygge opp
        # mot.
        r = tilgangssveip.Sveipresultat(
            feilet=True,
            alarm_utlost=tidligere + 1 >= tilgangssveip.ALARM_ETTER_FEIL)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if r.hoppet_over:
        # En kjøring som fant arbeidernøkkelen opptatt har ikke sveipet
        # noe og har heller ikke feilet — telleren skal stå NØYAKTIG som
        # den sto. `tilstand_lagret` er sant fordi tilstanden er intakt.
        feil_n, lagret = tidligere, True
    else:
        feil_n = tidligere + 1 if r.feilet else 0
        lagret = _skriv_feiltelling(feil_n)
    print(json.dumps({
        "hendelse": "tilgangssveip",
        "tenanter": r.tenanter,
        "nye_funn": r.nye,
        "oppdaterte_funn": r.oppdaterte,
        "lukkede_funn": r.lukkede,
        # AVKORTET ER ET FUNN, ikke en detalj: en kjøring som traff taket
        # sitt har ikke MÅLT hele registeret, og linja skal ikke kunne
        # leses som «alt er sett på».
        "avkortet": int(r.avkortet),
        "feilet": int(r.feilet),
        "hoppet_over": int(r.hoppet_over),
        "sammenhengende_feil": feil_n,
        "alarm": int(r.alarm_utlost),
        "tilstand_lagret": int(lagret),
    }))
    return 1 if r.feilet else 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
