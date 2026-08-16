#!/usr/bin/env bash
# ============================================================
# Disponit DB-backup (PR-009 v1 §5 + v2 §7): kryptert, 30 dagers
# retention, flock mot samtidig kjøring, og GJENOPPRETTINGSVERIFISERING
# til en ISOLERT database — aldri over en base i drift.
#
# Kryptering: age med MOTTAKERNØKKEL (asymmetrisk). Den private nøkkelen
# finnes IKKE på verten — en angriper med diskaksess kan lese null
# historiske backuper. Deployen feiler hvis mottakerfilen mangler; en
# ukryptert backup er ikke en fallback.
# ============================================================
set -euo pipefail

MOTTAKER=/etc/disponit/backup-mottaker.pub
KATALOG=/var/backups/disponit
DAGER=30

exec 9>/var/lock/disponit-backup.lock
flock -n 9 || { echo "AVBRUTT: backup kjører allerede" >&2; exit 1; }

[ -s "$MOTTAKER" ] || {
  echo "AVBRUTT: $MOTTAKER mangler — backup uten kryptering finnes ikke" >&2
  exit 1
}
command -v age >/dev/null || { echo "AVBRUTT: age er ikke installert" >&2; exit 1; }

set -a; . /etc/disponit/staging.env; set +a
install -d -m 700 "$KATALOG"
STEMPEL=$(date -u +%Y%m%dT%H%M%S)
FIL="$KATALOG/disponit-$STEMPEL.dump.age"

# pg_dump i custom-format som POSTGRES, ikke migrator. «Eier skjemaet, ser
# alt» sluttet å være sant da kapabilitetstabellene fikk egen eier uten
# grants (PR-009-modellen, bevist av test_m37): pg_dump som migrator døde på
# LOCK TABLE, og basen sto uten backup. Alternativet — å gi migrator SELECT —
# er den nøyaktige mutasjonen `test_migrator_naar_ikke_kapabilitetene_uten_
# set_role` finnes for å forby. Uniten kjører som root; superbrukeren ser alt
# uten at rettighetsmodellen røres.
sudo -u postgres pg_dump --format=custom --dbname=disponit \
  | age -R "$MOTTAKER" > "$FIL"
chmod 600 "$FIL"

# Gjenopprettingsverifisering: restore til en ISOLERT engangsbase.
# Verifiseringen bruker en UKRYPTERT strøm direkte fra pg_dump — den
# krypterte filens innhold kan ikke leses her (privatnøkkelen er ikke på
# verten, med vilje), så det som verifiseres er at dumpen er komplett og
# gjenopprettbar, og at den krypterte filen ble skrevet i sin helhet.
VERIF="disponit_backup_verif_$$"
sudo -u postgres createdb "$VERIF"
opprydd() { sudo -u postgres dropdb --if-exists "$VERIF"; }
trap opprydd EXIT
sudo -u postgres pg_dump --format=custom --dbname=disponit \
  | sudo -u postgres pg_restore --dbname="$VERIF" --no-owner --role=postgres
TABELLER=$(sudo -u postgres psql -Atd "$VERIF" -c \
  "SELECT count(*) FROM pg_tables WHERE schemaname='public'")
[ "$TABELLER" -ge 10 ] || {
  echo "AVBRUTT: gjenoppretting ga bare $TABELLER tabeller" >&2
  exit 1
}
STORRELSE=$(stat -c%s "$FIL")
[ "$STORRELSE" -gt 1024 ] || { echo "AVBRUTT: backupfilen er tom" >&2; exit 1; }

# Retention: 30 dager, og slettingen TELLES — en glob som ikke treffer
# noe ser ellers ut som en som ikke hadde noe å slette.
SLETTET=$(find "$KATALOG" -name 'disponit-*.dump.age' -mtime +"$DAGER" \
          -print -delete | wc -l)
echo "backup ok: $FIL (${STORRELSE} B), verifisert mot $VERIF" \
     "(${TABELLER} tabeller), slettet $SLETTET utløpte"
