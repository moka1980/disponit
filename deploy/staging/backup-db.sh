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
# FS-lageret for inndata-bunter (#162). API-unitens egen StateDirectory;
# `INNDATA_ROT` i platform/core/api/inndata.py peker på den samme roten.
LAGER=/var/lib/disponit-inndata
DAGER=30
# Diskmargin for dump + arkiv, i KiB. Bunter er inntil 64 MiB per stykk, og
# en full /var tar basen med seg — da er en avbrutt backup den milde utgangen.
MARGIN_KIB=$((256 * 1024))

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
# PARET ER ATOMISK (#191). Dumpen og arkivet deler stempel og er ÉN
# gjenopprettingsenhet: DEK-ene som dekrypterer buntene i arkivet ligger i
# dumpen med samme stempel, KEK-wrappet slik de sto den natten. Å restore
# dem fra to ulike kjøringer er ikke støttet — derfor får de heller aldri
# backupnavnene sine hver for seg, og retention sletter dem sammen.
# En dump uten sitt arkiv ER funnet dette skriptet lukker, bare flyttet inn
# i backupkatalogen.
ARKIV="$KATALOG/disponit-$STEMPEL.inndata.tar.age"

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
ARKIV_DELVIS="$ARKIV.delvis"
# Ett felles trap fra og med HER, ikke etter dumpen: det er nettopp
# intervallet før den gamle `trap`-linjen som er avbruddsvinduet. `VERIF`
# er tom til engangsbasen finnes, så oppryddingen dekker begge fasene uten
# å kalle `dropdb` på et navn som aldri ble opprettet.
VERIF=""
# FINALISERINGEN ER TO `mv`, OG DET ER ET VINDU. Dør prosessen mellom dem
# (SIGTERM fra opp.sh steg 5, OOM, strømbrudd), står den ene halvdelen igjen
# med sitt ENDELIGE navn mens trapen rydder den andres arbeidsnavn — en dump
# uten arkiv er nøyaktig #191-hullet, bare flyttet inn i backupkatalogen.
# `PAR_KLAR` er tomt til BEGGE navnene er satt, og oppryddingen tar da også
# de endelige navnene: enten finnes hele paret, eller ingenting.
PAR_KLAR=""
LISTE=$(mktemp)
opprydd() {
  rm -f "$DELVIS" "$ARKIV_DELVIS" "$LISTE" "$LISTE.sett" "$LISTE.krav"
  [ -n "$PAR_KLAR" ] || rm -f "$FIL" "$ARKIV"
  [ -z "$VERIF" ] || sudo -u postgres dropdb --if-exists "$VERIF"
}
trap opprydd EXIT
# En rest fra en kjøring som ble drept før traps rakk å kjøre (SIGKILL,
# strømbrudd) ville ellers blitt liggende for alltid: retention rører den
# ikke, og katalogen fylles av dumper. `flock` over garanterer at ingen
# annen kjøring eier en `.delvis` akkurat nå.
rm -f "$KATALOG"/disponit-*.dump.age.delvis \
      "$KATALOG"/disponit-*.inndata.tar.age.delvis

# DISKPORTEN FØR DUMPEN, ikke etter. Arkivet er en kopi av hele lageret, og
# lageret vokser med 64 MiB per bunt mens backupkatalogen holder 30 dagers
# par. Går /var full MIDT i kjøringen, er ikke backupen det eneste som
# stopper — basen skriver til den samme disken. Porten er derfor fail-closed
# og måler det arkivet faktisk kommer til å koste.
[ -d "$LAGER" ] || {
  echo "AVBRUTT: $LAGER mangler — inndata-lageret er ikke provisjonert." \
       "En backup som stille hopper over lageret er nøyaktig #191." >&2
  exit 1
}
LAGER_KIB=$(du -sk "$LAGER" | cut -f1)
# BEGGE HALVDELENE MÅLES, ikke bare arkivets. Porten regnet kun lageret +
# margin, mens kjøringens peak er dump + arkiv: en base som har vokst raskere
# enn lageret passerer da porten og dør midt i `pg_dump` på en /var basen
# selv skriver til — samme utfall porten finnes for å unngå, bare uten den
# tidlige avvisningen. `pg_database_size` er ukomprimert on-disk-størrelse og
# custom-format-dumpen blir mindre; overestimatet er riktig vei for en
# fail-closed port.
DUMP_KIB=$(sudo -u postgres psql -Atd disponit -c \
  "SELECT (pg_database_size('disponit') + 1023) / 1024")
LEDIG_KIB=$(df -k --output=avail "$KATALOG" | tail -1)
KREVES_KIB=$((LAGER_KIB + DUMP_KIB + MARGIN_KIB))
[ "$LEDIG_KIB" -ge "$KREVES_KIB" ] || {
  echo "AVBRUTT: $LEDIG_KIB KiB ledig i $KATALOG, trenger $KREVES_KIB KiB" \
       "(lager $LAGER_KIB + dump $DUMP_KIB + margin $MARGIN_KIB)" >&2
  exit 1
}

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

# ============================================================
# INNDATA-LAGERET (#191, Codex P1 fra #190)
#
# Dumpen alene ga en gjenoppretting med tilsynelatende gyldige `lastet`/
# `bundet` rader hvis `lager_sti`-filer ALLE var borte — hele opplastingen
# tapt, mens verifiseringen over meldte suksess.
#
# REKKEFØLGEN ER UTLEDET, IKKE VALGT. `inndata.py` skriver og fsync-er
# ciphertexten FØR raden committes, og ingen kodevei unlinker filen til en
# COMMITTET rad (unlinkene der ligger utelukkende på veier der
# transaksjonen abortert — der er filen trygt en orphan). Av det følger:
#
#   rad i dumpen  ⟹  committet før dumpen
#                 ⟹  filen fsynct før den commiten
#                 ⟹  filen fantes før dumpen, og finnes når arkivet tas.
#
# Derfor DUMP FØRST, ARKIV ETTERPÅ. Motsatt vei er nettopp funnet på nytt,
# bare med et nytt vindu: en fil skrevet etter arkivet, hvis rad rekker inn
# i dumpen, gir en rad uten fil.
#
# HVA SOM VINNER VED SPRIK: dumpen er autoriteten på hva som MÅ finnes.
# Arkivet får lov til å inneholde mer — orphans, og rader committet etter
# dumpen. Porten er derfor ENVEIS: hver `lager_sti` skal finnes i arkivet,
# aldri omvendt.
#
# KRYPTERING: age på nytt, samme mottaker som dumpen. Innholdet er alt
# tenant-DEK-kryptert, så dette er dobbelt — men STIENE er det ikke, og de
# bærer tenant-ID og buntvolum i klartekst. Katalogens invariant er at en
# angriper med diskaksess leser null; en tar-liste med kundenavn bryter den
# like fullt som en lesbar bunt.
# ============================================================
# Medlemslisten fanges fra DENNE ene passeringen (`--verbose` til stderr).
# Den krypterte filen kan ikke leses tilbake her — privatnøkkelen er ikke på
# verten, med vilje — så porten under måler samme strøm som ble skrevet,
# nøyaktig som dumpens egen verifisering gjør det.
tar --create --directory="$LAGER" --verbose --file=- . 2>"$LISTE" \
  | age -R "$MOTTAKER" > "$ARKIV_DELVIS"
chmod 600 "$ARKIV_DELVIS"

# Gjenopprettingsverifiseringens andre halvdel: hver `lager_sti` i en
# `lastet`/`bundet` rad i den GJENOPPRETTEDE basen skal finnes i arkivet.
# Tabellen kan mangle i en base som er eldre enn 058 — da er kravmengden tom
# og porten passerer, i stedet for at hele backupen dør på en manglende
# tabell.
sed 's#^\./##' "$LISTE" | sed '/^$/d' | sort -u > "$LISTE.sett"
if [ "$(sudo -u postgres psql -Atd "$VERIF" -c \
        "SELECT to_regclass('public.inndata_artefakt') IS NOT NULL")" = t ]; then
  sudo -u postgres psql -Atd "$VERIF" -c \
    "SELECT lager_sti FROM inndata_artefakt
      WHERE status IN ('lastet','bundet') AND lager_sti IS NOT NULL" \
    | sed '/^$/d' | sort -u > "$LISTE.krav"
else
  : > "$LISTE.krav"
fi
MANGLER=$(comm -23 "$LISTE.krav" "$LISTE.sett")
[ -z "$MANGLER" ] || {
  echo "AVBRUTT: $(printf '%s\n' "$MANGLER" | wc -l) rad(er) i dumpen peker" \
       "på filer arkivet ikke har — en restore ville gitt rader uten filer:" >&2
  printf '%s\n' "$MANGLER" | head -5 >&2
  exit 1
}
BUNTER=$(wc -l < "$LISTE.krav")

# ALLE portene har svart — dumpens to og arkivets én. FØRST nå får FILENE
# backupnavnene sine, og de får dem SAMMEN: paret er gjenopprettingsenheten,
# og en halv enhet i katalogen ville vært den samme løgnen som en avkortet
# dump med endelig navn.
#
# ARKIVET FØRST, DUMPEN SIST. Rekkefølgen er utledet av hva et SIGKILL i
# vinduet etterlater: dumpen er det retention, globben og operatøren leter
# etter, så en dump med endelig navn UTEN arkiv ser ut som dagens backup og
# lyver. Et arkiv uten dump ser ut som det er — en rest ingen forveksler med
# en gjenopprettingsenhet. `PAR_KLAR` settes SIST, etter begge navnene, så
# trapen over rydder begge halvdelene så lenge finaliseringen ikke kom helt
# i mål.
mv "$ARKIV_DELVIS" "$ARKIV"
mv "$DELVIS" "$FIL"
PAR_KLAR=1

# Retention: 30 dager, og slettingen TELLES — en glob som ikke treffer
# noe ser ellers ut som en som ikke hadde noe å slette.
#
# SLETTINGEN GÅR PÅ STEMPEL, IKKE PÅ GLOB PER FIL (#191). Utløper dumpen og
# arkivet hver for seg, står man igjen med en dump hvis bunter ingen arkiv
# lenger har — altså funnet, gjenoppstått etter 30 dager i stedet for med én
# gang. Stempelet binder dem: paret dør som det ble født.
SLETTET=0
while IFS= read -r gammel; do
  STEMPEL_GAMMEL=$(basename "$gammel"); STEMPEL_GAMMEL=${STEMPEL_GAMMEL#disponit-}
  STEMPEL_GAMMEL=${STEMPEL_GAMMEL%.dump.age}
  rm -f "$KATALOG/disponit-$STEMPEL_GAMMEL.dump.age" \
        "$KATALOG/disponit-$STEMPEL_GAMMEL.inndata.tar.age"
  SLETTET=$((SLETTET + 1))
done < <(find "$KATALOG" -name 'disponit-*.dump.age' -mtime +"$DAGER")

echo "backup ok: $FIL (${STORRELSE} B), verifisert mot $VERIF" \
     "(${TABELLER} tabeller); arkiv: $ARKIV ($(stat -c%s "$ARKIV") B," \
     "${BUNTER} bunt(er) bekreftet); slettet $SLETTET utløpte par"
