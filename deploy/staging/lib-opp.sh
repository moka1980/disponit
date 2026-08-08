# ============================================================
# lib-opp.sh — opp.sh sine PORTER som testbare funksjoner
# (PR-009 review-runde 1, to P1).
#
# Samme mønster som lib-miljofil.sh: logikken CI kan måle bor i en lib,
# opp.sh kaller den. Begge P1-ene satt i inline-bash ingen test så.
# ============================================================

# P1-1: unit-verifisering som FEILER. Første utgave slukte alt med
# `|| true` — og den observerte staging-kjøringen beviste konsekvensen:
# «Command /usr/local/lib/disponit-helse-sjekk is not executable» rant
# forbi som støy, og en ugyldig unit kunne blitt deployet. Nå:
# systemd-analyze sin exit-kode GATER, og utskriften vises alltid.
# (Støy fra ANDRE units på verten — f.eks. xfs_scrub sine
# CPUAccounting-advarsler — er warnings og endrer ikke exit-koden.)
verifiser_units() {  # <katalog> <unit...>  -> 0 kun hvis ALLE verifiserer
  local katalog=$1; shift
  local u rc=0
  for u in "$@"; do
    if [ ! -f "$katalog/$u" ]; then
      echo "VERIFISERING FEILET: $katalog/$u finnes ikke" >&2
      rc=1
      continue
    fi
    if ! systemd-analyze verify "$katalog/$u"; then
      echo "VERIFISERING FEILET: $u" >&2
      rc=1
    fi
  done
  return $rc
}

# P1-2: rollback-dommen felles over UNIONEN av basene. Første utgave lot
# siste iterasjon (TESTbasen) overskrive rapporten — en forward-only-
# migrasjon kjørt KUN i runtime-basen ville dermed blitt rapportert som
# rollback-kompatibel. Nå: hver base melder sitt, og dommen er
# konservativ — én ny migrasjon i ÉN base er nok til FORBUDT.
#
# Input: par av «basenavn utskriftsfil» (migrer.py-utskrift per base).
# Skriver per-base-linjer til stdout og setter den globale variabelen
# NYE_MIGRASJONER til unionen (tom = ingen nye noe sted).
vurder_migrasjoner() {
  NYE_MIGRASJONER=""
  while [ "$#" -ge 2 ]; do
    local base=$1 fil=$2; shift 2
    local kjort
    kjort=$(sed -n 's/^migrasjoner kjørt: //p' "$fil" | tail -1)
    echo "    $base: ${kjort:-ukjent}"
    case "$kjort" in
      ""|*ingen*) : ;;
      *) NYE_MIGRASJONER="${NYE_MIGRASJONER:+$NYE_MIGRASJONER; }$base: $kjort" ;;
    esac
  done
}
