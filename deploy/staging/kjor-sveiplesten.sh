#!/bin/bash
# Den GENERISKE SVEIPLESTEN for én modul, i ett kall.
#
# Fem av sertifiseringslestens seks punkter har samme produsent for alle
# sveipmoduler, og kjøres her i den rekkefølgen målingene faktisk
# avhenger av hverandre:
#
#   1. fasit          — modulens sett mot kjent dom (binder TO punkter:
#                       datasettet og evidenskjeden)
#   2. ytelse         — veggklokke rundt modulens egen kjor(), tre runder
#   3. feilinjisering — en driftsfeil injisert utenfra, registeret urørt
#   4. rollback       — kjerneformens flippedrill (krever --forgjenger)
#
# REKKEFØLGEN ER EN AVHENGIGHET, ikke en smakssak. En fersk sveipmodul
# har et TOMT register og null tenanter: ytelsen ville målt en kjøring
# over ingenting, og feilinjiseringen kan ikke vise at et tomt register
# sto stille. Fasiten rigger tenanter og funn, og de tre andre måler mot
# det den la igjen.
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
#         [--forgjenger /opt/disponit/releases/<sha>] [--hopp-over fasit]
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
# CREDKATALOGEN UTLEDES AV DSN-VARIABELEN, ikke av filnavnet: M-9s sveip
# heter `begrepssveip` mens rollen og katalogen heter `kunnskapssveip`, og
# en utledning fra filnavnet ville lett etter feil katalog i stillhet.
SVEIPKATALOG="$(printf '%s' "${SVEIPVAR#DISPONIT_}" | sed 's/_URL$//' \
    | tr '[:upper:]' '[:lower:]')"
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

# IKKE ALLE MODULER ER PÅ DEN GENERISKE FASITEN. M-19 ble sertifisert med
# sin egen produsent før den fantes, og har derfor ingen `fasit_krav`.
# Å kalle den generiske produsenten der ville falt på en manglende
# nøkkel — en krasj der svaret er «dette steget gjelder ikke».
HAR_FASIT="$(PYTHONPATH=platform/core:platform $PY -c \
    "from manifestskjema import SVEIPMODULER;\
 print('ja' if 'fasit_krav' in SVEIPMODULER['$MODUL'] else 'nei')")"

FEIL=0
if [ "$HAR_FASIT" = "ja" ]; then
    kjor fasit deploy/staging/sveip-fasit-artefakt.py --modul "$MODUL" || FEIL=1
else
    echo "== fasit: hoppet over ($MODUL har ingen fasit_krav — egen produsent)"
fi
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
