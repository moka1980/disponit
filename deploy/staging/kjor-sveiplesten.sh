#!/bin/bash
# Den GENERISKE SVEIPLESTEN for én modul, i ett kall.
#
# Tre av sertifiseringslestens seks punkter har samme produsent for alle
# sveipmoduler, og kjøres her i den rekkefølgen målingene faktisk
# avhenger av hverandre:
#
#   1. ytelse         — veggklokke rundt modulens egen kjor(), tre runder
#   2. feilinjisering — en driftsfeil injisert utenfra, registeret urørt
#   3. rollback       — kjerneformens flippedrill (krever --forgjenger)
#
# YTELSEN KJØRER FØRST, og det er ikke tilfeldig: den sveiper registeret
# fullt, og feilinjiseringen krever et register som IKKE er tomt for å
# kunne vise at det sto stille.
#
# SUITEN ER IKKE MED HER, med vilje. Den kjøres av `suite-artefakt.py`
# som *administrator*, aldri som root: root leser filer en test måler at
# er stengt, og en root-eid junit-logg dreper neste kjøring stille.
# Dette skriptet trenger credfilene og kjører som root — å legge suiten
# inn her ville tvunget den inn i feil bruker.
#
# De to gjenstående punktene — fasit/datasett og revisjonslogg — krever
# modulens EGEN rigg og har ingen felles produsent.
#
# BRUK (på verten som root, fra utsjekket):
#     sudo deploy/staging/kjor-sveiplesten.sh --modul m19_adresse \
#         [--forgjenger /opt/disponit/releases/<sha>] [--hopp-over ytelse]
set -euo pipefail

MODUL=""; FORGJENGER=""; HOPP=""
while [ $# -gt 0 ]; do
    case "$1" in
        --modul) MODUL="$2"; shift 2 ;;
        --forgjenger) FORGJENGER="$2"; shift 2 ;;
        --hopp-over) HOPP="$HOPP $2"; shift 2 ;;
        *) echo "AVBRUTT: ukjent argument $1" >&2; exit 2 ;;
    esac
done
[ -n "$MODUL" ] || { echo "AVBRUTT: --modul mangler" >&2; exit 2; }

# HEMMELIGHETENE LESES, ALDRI PRINTES. Ingen `echo` av en DSN, og
# `set -x` er bevisst ikke på: en logg med en tilkoblingsstreng i er en
# lekkasje uansett hvor loggen havner.
set -a; . /etc/disponit/staging.env; set +a
for v in DATABASE_URL DISPONIT_KEK; do
    if [ -z "${!v:-}" ] && [ -r "/etc/disponit/api/$v" ]; then
        export "$v"="$(cat "/etc/disponit/api/$v")"
    fi
done

ROT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY=/opt/disponit/.venv/bin/python
cd "$ROT"

# Sveipens egen DSN hentes fra modulens oppføring, ikke fra en liste her:
# to steder å vedlikeholde er ett for mye.
SVEIPVAR="$(PYTHONPATH=platform/core:platform $PY -c \
    "from manifestskjema import SVEIPMODULER; print(SVEIPMODULER['$MODUL']['dsn_variabel'])")"
SVEIPKATALOG="$(PYTHONPATH=platform/core:platform $PY -c \
    "from manifestskjema import SVEIPMODULER; print(SVEIPMODULER['$MODUL']['modul_fil'])")"
if [ -z "${!SVEIPVAR:-}" ] && [ -r "/etc/disponit/$SVEIPKATALOG/DATABASE_URL" ]; then
    export "$SVEIPVAR"="$(cat "/etc/disponit/$SVEIPKATALOG/DATABASE_URL")"
fi
[ -n "${!SVEIPVAR:-}" ] || { echo "AVBRUTT: $SVEIPVAR mangler" >&2; exit 2; }

kjor() {
    local navn="$1"; shift
    case " $HOPP " in *" $navn "*) echo "== $navn: hoppet over"; return 0 ;; esac
    echo "== $navn"
    PYTHONPATH=platform/core:platform $PY "$@" || {
        echo "FEILET: $navn" >&2; return 1; }
}

FEIL=0
kjor ytelse deploy/staging/sveip-ytelse.py --modul "$MODUL" || FEIL=1
kjor feilinjisering deploy/staging/sveip-feilinjisering.py --modul "$MODUL" || FEIL=1
if [ -n "$FORGJENGER" ]; then
    kjor rollback deploy/staging/rollback-sveipkjerne.py --modul "$MODUL" \
        --forgjenger-katalog "$FORGJENGER" || FEIL=1
else
    echo "== rollback: hoppet over (--forgjenger ikke oppgitt)"
fi

echo "== ferdig (feil=$FEIL)"
exit $FEIL
