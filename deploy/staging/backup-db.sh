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
# FS-lageret for inndata-bunter (#162). API-unitens egen StateDirectory.
#
# HARDKODET MED VILJE, ikke av slurv (#191, K2). Runde 1 ba om
# `LAGER="${DISPONIT_INNDATA_ROT:-...}"` for at backupen skulle følge API-et;
# runde 2 påpekte at variabelen da er halvbindt. Begge har rett om formen —
# feilen lå under: variabelen KAN ikke ta noen annen verdi. API-uniten kjører
# `ProtectSystem=strict`, der `StateDirectory` er den eneste skrivbare stien,
# og en annen rot gir `EROFS` på hver opplasting. Å gjøre knappen ekte krever
# `ReadWritePaths`-formen #162 forkastet på Codex P1.
#
# Derfor er stien konstant BEGGE steder — her og i `api/inndata.py` — og
# `test_inndatalageret_er_api_unitens_egen_state_katalog` binder alle fire
# forekomstene til unitens `StateDirectory`-navn. En knapp som bare kan stå i
# én stilling er ikke konfigurasjon; den er to filer som kan gli fra
# hverandre uten at noe sier fra.
LAGER=/var/lib/disponit-inndata
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
# ÉN DUMP, TO FORBRUKERE (Cursor P2, #191). Skriptet kjørte `pg_dump` to
# ganger: den lagrede filen kom fra pass 1, mens engangsbasen — og dermed
# `lager_sti`-porten — ble bygget fra pass 2. Porten beviste da konsistens
# for et ANNET tidspunkt enn det som ble arkivert, og mellom passene rekker
# en `forkastet`-rydding å unlinke en fil: pass 2 ser ikke raden, kravmengden
# blir tom, porten passerer — og den lagrede pass-1-dumpen har fortsatt raden
# som `lastet` uten fil i arkivet. Nøyaktig #191, gjennom porten som skulle
# fange det.
#
# Mellomfila er UKRYPTERT og lever bare inne i kjøringen: root-eid 600 i en
# 700-katalog, ryddet av trapen, av feiesvingen under, og eksplisitt så snart
# begge forbrukerne er ferdige.
#
# DEN LIGGER I MINNE, IKKE PÅ DISK (#229, eiers dom 28/8). Codex (P1) og
# Cursor (P2) sto mot hverandre på nettopp denne fila: Cursor krevde ÉN
# dump, fordi to `pg_dump` betyr at porten måler et annet snapshot enn det
# som lagres; Codex krevde at klartekst aldri persisteres, fordi katalogens
# trusselmodell er at diskaksess gir null — privatnøkkelen ligger bevisst
# ikke på verten, og da er gjenopprettbare klartekstblokker i /var/backups
# inkonsekvent.
#
# Motsetningen var ekte bare så lenge «én snapshot» ble antatt å kreve «fil
# på disk». tmpfs oppfyller begge: samme byte-sekvens til kryptering,
# `pg_restore` og `lager_sti`-porten, og klarteksten når aldri varig
# lagring. Målt på verten: /dev/shm har 2,0 GB fri mot en dump på 3,0 MB.
#
# `mktemp -d` og ikke et konstruert søskennavn: katalogen er reservert av
# kjernen, og 0700 settes FØR fila finnes — et navn man gjetter seg til kan
# være forutsigbart for andre på verten (Codex r3878291010).
# En kjøring drept før traps rakk å kjøre (SIGKILL, OOM) etterlater
# katalogen sin på tmpfs. Den overlever ikke en omstart, men mellom to
# omstarter ville de hopet seg opp — og de tar av det samme minnet neste
# kjøring trenger. Feies FØR vår egen lages, ellers feier vi oss selv;
# `flock` over garanterer at ingen annen kjøring eier en akkurat nå.
#
# FEIEN ER AVGRENSET TIL VÅRE EGNE RESTER (Cursor P2 på `4a6dccf`).
# `rm -rf /dev/shm/disponit-backup.*` som root traff ENHVER match,
# uansett eier. `/dev/shm` er verdensskrivbar med sticky bit, og
# DEPLOY.md dokumenterer at verten er DELT med et annet produkt: en
# hvilken som helst lokal bruker kunne lagt igjen `disponit-backup.x`
# og fått root til å slette den for seg — eller lagt den der før hver
# backup og gjort feien til sitt eget verktøy mot nabotjenesten.
# Sticky bit hindrer at ANDRE sletter våre; det hindrer ikke at vi
# sletter andres.
#
# `-user root` er avgrensningen som holder: `/dev/shm` er sticky, så en
# uprivilegert bruker kan ikke lage en root-eid oppføring der, og det
# er nettopp våre egne rester som er root-eide. `-mindepth 1 -maxdepth
# 1` gjør at feien aldri kan vandre ut av `/dev/shm` selv. Null treff
# gir exit 0 — feien dreper ikke kjøringen når det ikke var noe å feie
# — mens et manglende `/dev/shm` gir exit 1 og stopper her, som er
# riktig vei: `mktemp -d -p /dev/shm` på neste linje ville dødd uansett.
find /dev/shm -mindepth 1 -maxdepth 1 -user root \
     -name 'disponit-backup.*' -exec rm -rf {} +
RAA_KAT=$(mktemp -d -p /dev/shm disponit-backup.XXXXXXXX)
chmod 700 "$RAA_KAT"
RAA="$RAA_KAT/disponit-$STEMPEL.dump.raa"
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
#
# MEN FLAGGET ER IKKE SANNHETEN — DISKEN ER (Cursor P2 på 2d3886b). `PAR_KLAR=1`
# settes ETTER den siste `mv`, og lander SIGTERM i det mikrovinduet, ville
# trapen slettet et komplett, verifisert par. Da hadde vernet mot en halv
# enhet begynt å ødelegge hele. Oppryddingen spør derfor filsystemet i
# stedet: finnes BEGGE de endelige navnene, er paret ferdig uansett hva
# flagget rakk å bli. Finnes bare den ene, er det nettopp halvparten som
# skal bort.
PAR_KLAR=""
# LISTEN BOR I DEN PRIVATE KATALOGEN, ikke i `$TMPDIR` (Codex P1 ×2).
# `mktemp` uten `-p` legger fila i `/tmp` — verdensskrivbar, sticky, og
# PERSISTENT. To ting fulgte av det, og begge er alvorlige på en vert
# DEPLOY.md sier er DELT:
#
#   1. Fila og de to avledede (`.sett`, `.krav`) bærer hver tenant-ID og
#      hver arkivmedlemssti i KLARTEKST. Arkivet krypteres nettopp for å
#      holde de stiene borte fra disken; en liste ved siden av opphever
#      det.
#   2. `mktemp` lager bare `$LISTE` trygt. `$LISTE.sett` og `$LISTE.krav`
#      er navn en lokal bruker kan gjette fra den første — den er lesbar
#      i katalogen — og pre-opprette som symlenker under den lange
#      dump/arkiv-fasen. Da skriver root gjennom lenken.
#
# `$RAA_KAT` er 0700 og root-eid, laget med `mktemp -d`, og ligger på
# tmpfs. Alle tre navnene bor der: ingen andre kommer inn i katalogen, så
# hverken lesningen eller lenke-kappløpet finnes.
LISTE="$RAA_KAT/medlemmer"
opprydd() {
  rm -f "$DELVIS" "$ARKIV_DELVIS"
  # `$RAA_KAT` tar mellomfila OG de tre listene med seg — de bor alle der
  # nå. `rm -rf` på katalogen er derfor hele oppryddingen etter dem.
  [ -z "$RAA_KAT" ] || rm -rf "$RAA_KAT"
  if [ -z "$PAR_KLAR" ] && ! { [ -f "$FIL" ] && [ -f "$ARKIV" ]; }; then
    rm -f "$FIL" "$ARKIV"
  fi
  [ -z "$VERIF" ] || sudo -u postgres dropdb --if-exists "$VERIF"
}
trap opprydd EXIT
# En rest fra en kjøring som ble drept før traps rakk å kjøre (SIGKILL,
# strømbrudd) ville ellers blitt liggende for alltid: retention rører den
# ikke, og katalogen fylles av dumper. `flock` over garanterer at ingen
# annen kjøring eier en `.delvis` akkurat nå.
rm -f "$KATALOG"/disponit-*.dump.age.delvis \
      "$KATALOG"/disponit-*.inndata.tar.age.delvis

# RETENSJONEN FØR DISKPORTEN (Codex P1, denne runden). Sto sveipen sist,
# var den uoppnåelig nettopp når den trengtes: fylte de beholdte parene
# katalogen så et nytt par ikke fikk plass, avsluttet diskporten FØR
# sveipen kunne slette noe. Neste kjøring så samme opptatte plass og
# avsluttet på samme sted — permanent, uten at noe var galt annet enn
# rekkefølgen. En port som hindrer sin egen forutsetning er en deadlock
# med en feilmelding.
#
# Sveipen står derfor her, før målingen: det som skal frigjøres, frigjøres
# først, og porten måler den plassen som faktisk finnes.
# Retention: 30 dager, og slettingen TELLES — en glob som ikke treffer
# noe ser ellers ut som en som ikke hadde noe å slette.
#
# SLETTINGEN GÅR PÅ STEMPEL, IKKE PÅ GLOB PER FIL (#191). Utløper dumpen og
# arkivet hver for seg, står man igjen med en dump hvis bunter ingen arkiv
# lenger har — altså funnet, gjenoppstått etter 30 dager i stedet for med én
# gang. Stempelet binder dem: paret dør som det ble født.
# `find`-STATUSEN PROPAGERER IKKE ut av `< <(...)` (Codex r3878291023):
# prosess-substitusjonen er en egen prosess, og skallet ser aldri exitkoden
# dens. Feiler `find` — katalogen borte, I/O-feil — leser løkka null linjer,
# `SLETTET` blir 0, og kjøringen melder «slettet 0 utløpte par» som om
# retention hadde gjort jobben sin. Utløpte backuper ville da hopet seg opp
# i stillhet, med en grønn logglinje over seg.
#
# Derfor materialiseres listen FØRST, med statusen synlig, og løkka leser
# den.
UTLOPTE=$(find "$KATALOG" -name 'disponit-*.dump.age' -mtime +"$DAGER") || {
  echo "AVBRUTT: retention-søket feilet — utløpte backuper ville hopet" \
       "seg opp bak en grønn logglinje" >&2
  exit 1
}
# ... MEN ALDRI DEN SISTE FØR DEN NYE STÅR (Codex P1, denne runden).
# Å flytte sveipen foran diskporten løste en deadlock og åpnet en verre
# dør: er ALLE par eldre enn 30 dager — en timer som har stått, en vert
# som har vært nede — slettet sveipen hvert eneste gjenopprettingspunkt
# FØR `pg_dump`, verifiseringen og arkivet hadde lykkes. En transient
# feil etterpå etterlot da installasjonen uten backup i det hele tatt.
# Den gamle rekkefølgen hadde ikke det hullet; den hadde bare det andre.
#
# Begge lukkes ved å SPARE DEN NYESTE: sveipen her tar alt unntatt den,
# og den siste tas først når det nye paret har fått navnene sine. Da
# finnes det aldri et øyeblikk uten et gjenopprettingspunkt på disken, og
# plassen frigjøres likevel før porten måler.
#
# `sort -r` på filnavnet er kronologisk: stempelet er `YYYYmmdd-HHMMSS`,
# altså leksikografisk lik tidsrekkefølgen. Vi trenger ingen `stat`.
SPART=""
if [ -n "$UTLOPTE" ]; then
  SPART=$(printf '%s\n' "$UTLOPTE" | sed '/^$/d' | sort -r | head -1)
fi
slett_par() {
  local sti="$1" stempel
  [ -n "$sti" ] || return 0
  stempel=$(basename "$sti"); stempel=${stempel#disponit-}
  stempel=${stempel%.dump.age}
  rm -f "$KATALOG/disponit-$stempel.dump.age" \
        "$KATALOG/disponit-$stempel.inndata.tar.age"
}
SLETTET=0
while IFS= read -r gammel; do
  [ -n "$gammel" ] || continue
  [ "$gammel" != "$SPART" ] || continue
  slett_par "$gammel"
  SLETTET=$((SLETTET + 1))
done <<< "$UTLOPTE"

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
# DUMPEN TELLES ÉN GANG. Den lå tidligere på disken to ganger samtidig —
# mellomfila og den krypterte — og porten talte begge. Etter eiers dom 28/8
# bor mellomfila på tmpfs, så den koster null i `$KATALOG`. Codex (P2,
# r3878380033) og skriptets egen prosa sto mot hverandre om nettopp dette
# leddet; dommen fjernet striden i stedet for å velge side i den.
#
# Minnet måles ikke her: /dev/shm er 2,0 GB mot en dump på 3,0 MB, og går
# den likevel full, feiler `pg_dump` høyt på ENOSPC uten å ha rørt hverken
# katalogen eller basen. Det er en billigere feil enn den porten finnes for.
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
# Omdirigeringene gjøres av ROOT-skallet, ikke av `postgres`: mellomfila bor
# i en 700-katalog `postgres` ikke kommer inn i, og `< "$RAA"` gir
# `pg_restore` en ferdig åpnet fd — samme grep som den gamle pipen brukte,
# uten å slippe noen inn i backupkatalogen.
sudo -u postgres pg_dump --format=custom --dbname=disponit > "$RAA"
chmod 600 "$RAA"
age -R "$MOTTAKER" < "$RAA" > "$DELVIS"
chmod 600 "$DELVIS"

# Gjenopprettingsverifisering: restore til en ISOLERT engangsbase.
# Verifiseringen bruker den UKRYPTERTE mellomfila — den krypterte filens
# innhold kan ikke leses her (privatnøkkelen er ikke på verten, med vilje),
# så det som verifiseres er at dumpen er komplett og gjenopprettbar, og at
# den krypterte filen ble skrevet i sin helhet. Og fordi det er NØYAKTIG de
# samme bytene som ble kryptert, gjelder alt porten under måler i
# engangsbasen også for fila som havner i katalogen.
VERIF="disponit_backup_verif_$$"
sudo -u postgres createdb "$VERIF"
sudo -u postgres pg_restore --dbname="$VERIF" --no-owner --role=postgres \
  < "$RAA"
TABELLER=$(sudo -u postgres psql -Atd "$VERIF" -c \
  "SELECT count(*) FROM pg_tables WHERE schemaname='public'")
[ "$TABELLER" -ge 10 ] || {
  echo "AVBRUTT: gjenoppretting ga bare $TABELLER tabeller" >&2
  exit 1
}
STORRELSE=$(stat -c%s "$DELVIS")
[ "$STORRELSE" -gt 1024 ] || { echo "AVBRUTT: backupfilen er tom" >&2; exit 1; }
# BEGGE FORBRUKERNE ER FERDIGE — klarteksten skal ikke ligge og vente på
# `tar`. Trapen tar den uansett, men den dekker ikke SIGKILL, og resten av
# kjøringen er den lengste delen av den. Frigjør også plassen før arkivet
# skrives.
rm -f "$RAA"

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

# NAVNET ER IKKE INNHOLDET (Codex P1, denne runden). `comm` over måler at
# hver påkrevd sti STÅR i arkivet. En `.bin` som er blitt avkortet, tømt
# eller overskrevet før backupen kjørte, blir arkivert like lydig som en
# hel — `tar` lykkes, navnet står i listen, og porten publiserer paret som
# «gjenopprettingsverifisert» mens den restaurerte radens nonce og
# målinger peker på en fil uten innhold.
#
# HVA DENNE PORTEN GJØR: hver påkrevd sti må være en VANLIG FIL med
# innhold i seg ved kilden. Det feller tomme og forsvunne filer, som er
# den formen sviktende lagring og avbrutte skrivinger oftest tar.
#
# HVA DEN IKKE GJØR, sagt høyt: den oppdager ikke DELVIS avkorting eller
# byte-korrupsjon. Filen på disk er tenant-DEK-ciphertext, og basen bærer
# `innhold_sha256` over KLARTEKSTEN — det er ikke samme streng, så det
# finnes ingen lagret digest å måle ciphertexten mot. Å innføre en er en
# ny maskin (K1) på skrivesiden i `inndata.py`, ikke noe en backupport kan
# finne på selv. Den står som eget issue.
mens_manglet=""
while IFS= read -r sti; do
  [ -n "$sti" ] || continue
  # `-L` FØRST: både `-f` og `-s` FØLGER lenken, så en `.bin` byttet ut
  # med en symlenke til en hvilken som helst ikke-tom fil ville passert
  # begge — mens `tar` uten `--dereference` arkiverer LENKEN, ikke
  # ciphertexten. Paret ble da meldt verifisert og gjenopprettet en
  # peker (Codex P1, denne runden).
  if [ -L "$LAGER/$sti" ] \
     || [ ! -f "$LAGER/$sti" ] || [ ! -s "$LAGER/$sti" ]; then
    mens_manglet="$mens_manglet$sti"$'\n'
  fi
done < "$LISTE.krav"
[ -z "$mens_manglet" ] || {
  echo "AVBRUTT: $(printf '%s' "$mens_manglet" | grep -c .) fil(er) som" \
       "dumpen krever er tomme, symlenker eller ikke vanlige filer —" \
       "arkivet ville" \
       "båret navnet uten innholdet:" >&2
  printf '%s' "$mens_manglet" | head -5 >&2
  exit 1
}

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
# `sync` FØR navnene settes (Codex r3878291028). `mv` innenfor samme
# filsystem er en katalogoperasjon: den flytter navnet, ikke bytene. Faller
# strømmen etter en `mv` men før sidene er skrevet ut, kan katalogposten
# være på disk mens innholdet ikke er — en backup med endelig navn og et
# hull i seg, altså nøyaktig den «ser ut som dagens backup»-løgnen
# arbeidsnavnene finnes for å hindre.
# ... men `sync` alene gir ingen REKKEFØLGE (Codex P1, denne runden). Den
# tømmer køen én gang, før begge `mv`-ene; etterpå er de to katalogpostene
# usynkede, og et strømbrudd kan la filsystemet gjenopprette dumpens
# endelige navn UTEN arkivets. Da er vi tilbake i «ser ut som dagens
# backup»-løgnen, bare med filsystemet som årsak i stedet for signalet.
#
# Rekkefølgen må derfor tvinges LEDD FOR LEDD: innholdet i begge filene
# først, så arkivets navn, så dumpens navn — med en katalog-fsync mellom.
# `sync <fil>` fsync-er den fila; `sync <katalog>` fsync-er katalogposten.
# Etter dette finnes det ikke noe krasjpunkt der dumpen har endelig navn
# uten at arkivet har det.
sync "$ARKIV_DELVIS" "$DELVIS"
mv "$ARKIV_DELVIS" "$ARKIV"
sync "$KATALOG"
mv "$DELVIS" "$FIL"
sync "$KATALOG"
PAR_KLAR=1

# DEN SPARTE TAS NÅ. Det nye paret står med sine endelige navn og er
# fsynket, så det finnes et gjenopprettingspunkt — og først da er det
# forsvarlig å fjerne det siste gamle. Fristen er den samme som sveipen
# målte mot, så den er fortsatt utløpt.
if [ -n "$SPART" ]; then
  slett_par "$SPART"
  SLETTET=$((SLETTET + 1))
fi


echo "backup ok: $FIL (${STORRELSE} B), verifisert mot $VERIF" \
     "(${TABELLER} tabeller); arkiv: $ARKIV ($(stat -c%s "$ARKIV") B," \
     "${BUNTER} bunt(er) bekreftet); slettet $SLETTET utløpte par"
