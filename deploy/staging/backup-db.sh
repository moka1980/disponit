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

# Codex P1 (#178, runde 6): BACKUPNAVNET FÅS FØRST NÅR BACKUPEN ER SANN.
# opp.sh steg 5 stopper `disponit-backup.service` for å holde `pg_dump`
# unna et skjema i bevegelse (#178 runde 4). Stoppen er riktig, men den
# treffer hele cgruppen — også en dump som alt er i gang. Skrev vi da
# direkte til `$FIL`, ville SIGTERM etterlatt en AVKORTET fil med det
# endelige navnet: nyeste treff i katalogen, nyeste treff for globben,
# og null verifisering bak seg. En backup som ikke kan restores er verre
# enn ingen backup, fordi den ser ut som en — og dagen ville sett dekket
# ut nettopp den dagen deployen gikk galt.
#
# Derfor bærer fila et arbeidsnavn til BEGGE portene under (gjenoppretting
# og størrelse) har svart, og `mv` er atomisk innenfor katalogen. `.delvis`
# matcher hverken `disponit-*.dump.age` eller retention-globben, så en
# rest kan verken forveksles med en backup eller slettes som en.
DELVIS="$FIL.delvis"
# Ett felles trap fra og med HER, ikke etter dumpen: det er nettopp
# intervallet før den gamle `trap`-linjen som er avbruddsvinduet. `VERIF`
# er tom til engangsbasen finnes, så oppryddingen dekker begge fasene uten
# å kalle `dropdb` på et navn som aldri ble opprettet.
VERIF=""
opprydd() {
  rm -f "$DELVIS"
  [ -z "$VERIF" ] || sudo -u postgres dropdb --if-exists "$VERIF"
}
trap opprydd EXIT
# En rest fra en kjøring som ble drept før traps rakk å kjøre (SIGKILL,
# strømbrudd) ville ellers blitt liggende for alltid: retention rører den
# ikke, og katalogen fylles av dumper. `flock` over garanterer at ingen
# annen kjøring eier en `.delvis` akkurat nå.
rm -f "$KATALOG"/disponit-*.dump.age.delvis

# pg_dump i custom-format som POSTGRES, ikke migrator. «Eier skjemaet, ser
# alt» sluttet å være sant da kapabilitetstabellene fikk egen eier uten
# grants (PR-009-modellen, bevist av test_m37): pg_dump som migrator døde på
# LOCK TABLE, og basen sto uten backup. Alternativet — å gi migrator SELECT —
# er den nøyaktige mutasjonen `test_migrator_naar_ikke_kapabilitetene_uten_
# set_role` finnes for å forby. Uniten kjører som root; superbrukeren ser alt
# uten at rettighetsmodellen røres.
sudo -u postgres pg_dump --format=custom --dbname=disponit \
  | age -R "$MOTTAKER" > "$DELVIS"
chmod 600 "$DELVIS"

# Gjenopprettingsverifisering: restore til en ISOLERT engangsbase.
# Verifiseringen bruker en UKRYPTERT strøm direkte fra pg_dump — den
# krypterte filens innhold kan ikke leses her (privatnøkkelen er ikke på
# verten, med vilje), så det som verifiseres er at dumpen er komplett og
# gjenopprettbar, og at den krypterte filen ble skrevet i sin helhet.
VERIF="disponit_backup_verif_$$"
sudo -u postgres createdb "$VERIF"
sudo -u postgres pg_dump --format=custom --dbname=disponit \
  | sudo -u postgres pg_restore --dbname="$VERIF" --no-owner --role=postgres
TABELLER=$(sudo -u postgres psql -Atd "$VERIF" -c \
  "SELECT count(*) FROM pg_tables WHERE schemaname='public'")
[ "$TABELLER" -ge 10 ] || {
  echo "AVBRUTT: gjenoppretting ga bare $TABELLER tabeller" >&2
  exit 1
}
STORRELSE=$(stat -c%s "$DELVIS")
[ "$STORRELSE" -gt 1024 ] || { echo "AVBRUTT: backupfilen er tom" >&2; exit 1; }
# Begge portene har svart. FØRST nå får fila backupnavnet, og fra og med
# denne linjen er den dagens backup — for retention, for operatøren og for
# den som en dag skal restore.
mv "$DELVIS" "$FIL"

# Retention: 30 dager, og slettingen TELLES — en glob som ikke treffer
# noe ser ellers ut som en som ikke hadde noe å slette.
SLETTET=$(find "$KATALOG" -name 'disponit-*.dump.age' -mtime +"$DAGER" \
          -print -delete | wc -l)
echo "backup ok: $FIL (${STORRELSE} B), verifisert mot $VERIF" \
     "(${TABELLER} tabeller), slettet $SLETTET utløpte"
