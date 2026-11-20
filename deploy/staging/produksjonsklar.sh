#!/usr/bin/env bash
# Klarsjekken før verten kan kjøre i produksjonsmodus (ADR-009).
#
# I produksjonsmodus binder bare policyer merket `produksjon`, og en
# bestilling er bare claimbar når modulen har en `claiming` deployment i
# miljøet `produksjon`. Byttes modusen før dette er på plass, svarer hver
# bestilling 503 og hvert firma på en utkast-policy mister regelverket sitt
# — uten at utrullingen sier fra, for API-et starter helt fint.
#
# Skriptet LESER bare. Det kjøres av opp.sh før første mutasjon når verten
# er erklært som produksjon, og kan kjøres for hånd når som helst:
#   sudo bash deploy/staging/produksjonsklar.sh
# Exit 0 = klar. Exit 1 = minst én blokkering, listet én per linje.
set -euo pipefail
DB="${DISPONIT_DBNAVN:-disponit}"
[ "$(id -u)" = 0 ] || { echo "kjør som root (leser som postgres)"; exit 2; }
sql() { runuser -u postgres -- psql -d "$DB" -X -At -v ON_ERROR_STOP=1 \
          -c "SET default_transaction_read_only = on" -c "$1" | tail -n +2; }

blokkert=0
blokker() { echo "BLOKKERER: $1"; blokkert=1; }

# 1. FIRMA I BRUK PÅ EN POLICY SOM IKKE ER `produksjon`. «I bruk» er et
#    aktivt medlem eller en beslutning siste 30 døgn. Testfirmaene (`t-…`)
#    er riggenes egne og telles for seg: de slutter å virke, og det er
#    meningen (ADR-009: målingene flytter til den isolerte basen).
while IFS='|' read -r tenant policy versjon status; do
  [ -n "$tenant" ] || continue
  blokker "firmaet «$tenant» er i bruk, og den aktive policyen ($policy $versjon) er merket «$status». Løft den til produksjon i Policyadministrasjon først."
done < <(sql "
  SELECT p.tenant, p.policy_id, p.versjon, p.status
    FROM policyer p
   WHERE p.aktiv AND p.status <> 'produksjon' AND p.tenant NOT LIKE 't-%'
     AND (EXISTS (SELECT 1 FROM brukermedlemskap m
                   WHERE m.tenant = p.tenant AND m.aktiv)
          OR EXISTS (SELECT 1 FROM revisjonslogg r
                      WHERE r.tenant = p.tenant
                        AND r.ts > now() - interval '30 days'))
   ORDER BY 1, 2")

while IFS='|' read -r tenant policy versjon status; do
  [ -n "$tenant" ] || continue
  echo "ADVARSEL: «$tenant» er ikke i bruk, men den aktive policyen ($policy $versjon) er «$status» og slutter å binde."
done < <(sql "
  SELECT p.tenant, p.policy_id, p.versjon, p.status
    FROM policyer p
   WHERE p.aktiv AND p.status <> 'produksjon' AND p.tenant NOT LIKE 't-%'
     AND NOT EXISTS (SELECT 1 FROM brukermedlemskap m
                      WHERE m.tenant = p.tenant AND m.aktiv)
     AND NOT EXISTS (SELECT 1 FROM revisjonslogg r
                      WHERE r.tenant = p.tenant
                        AND r.ts > now() - interval '30 days')
   ORDER BY 1, 2")

n=$(sql "SELECT count(DISTINCT tenant) FROM policyer
          WHERE aktiv AND status <> 'produksjon' AND tenant LIKE 't-%'")
[ "$n" = 0 ] || echo "MERK: $n testfirma (t-…) står på utkast og slutter å virke i produksjonsmodus."

# 2. MODULER SOM TAR BESTILLINGER I STAGING, MEN IKKE I PRODUKSJON. Samme
#    vilkår som bestillingsporten (`api/bestilling.py`): samme kontrakt,
#    miljøet `produksjon`, livsløpet `claiming`.
while IFS='|' read -r modul; do
  [ -n "$modul" ] || continue
  blokker "modulen «$modul» har ingen claiming-deployment i miljøet produksjon. Bestillingene dens ville svart 503."
done < <(sql "
  SELECT DISTINCT s.modul_id
    FROM moduldeployment s
   WHERE s.miljo = 'staging' AND s.livslop = 'claiming'
     AND NOT EXISTS (SELECT 1 FROM moduldeployment p
                      WHERE p.modul_id = s.modul_id AND p.miljo = 'produksjon'
                        AND p.livslop = 'claiming'
                        AND p.kontraktversjon = s.kontraktversjon
                        AND p.kontrakt_hash = s.kontrakt_hash)
   ORDER BY 1")

# 3. …OG ARBEIDEREN MÅ KUNNE CLAIME DER: et gyldig modultoken bundet til
#    nettopp den produksjons-deploymenten. Et staging-token claimer under
#    staging-deploymenten, og den teller ikke i produksjonsmodus.
while IFS='|' read -r modul release; do
  [ -n "$modul" ] || continue
  blokker "modulen «$modul» har ikke noe gyldig modultoken for produksjon ($release). Arbeideren kan ikke claime."
done < <(sql "
  SELECT p.modul_id, p.release_id
    FROM moduldeployment p
   WHERE p.miljo = 'produksjon' AND p.livslop = 'claiming'
     AND NOT EXISTS (SELECT 1 FROM modultoken t
                      WHERE t.modul_id = p.modul_id AND t.miljo = 'produksjon'
                        AND t.release_id = p.release_id
                        AND t.tilbakekalt_ts IS NULL AND t.utloper > now())
   ORDER BY 1")

if [ "$blokkert" = 1 ]; then
  echo "IKKE KLAR for produksjonsmodus."
  exit 1
fi
echo "KLAR for produksjonsmodus."
