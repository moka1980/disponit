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
# STEMPELET MÅ VÆRE FERSKT (sak #246 pkt. 3). Gjenbrukes et stempel —
# klokka justert bakover, to raske manuelle kjøringer — kunne et krasj
# mellom finaliseringens to `mv` latt eksistenssjekken se det NYE
# arkivet og den GAMLE dumpen, og bevare et par som aldri hørte sammen.
# Sjekken står FØR trapen installeres, med vilje: trapen rydder
# endelige navn når paret er halvt, og et eksisterende, fremmed par
# skal aldri kunne bli «vårt halve».
# `-L` i tillegg til `-e`: en HENGENDE symlenke på et av navnene er
# også en kollisjon (`-e` følger lenken og svarer nei) — og uansett en
# tilstand backupkatalogen aldri lovlig har (CodeRabbit).
if [ -e "$FIL" ] || [ -L "$FIL" ] || [ -e "$ARKIV" ] || [ -L "$ARKIV" ]; then
  echo "AVBRUTT: stempelet $STEMPEL finnes allerede i $KATALOG —"        "to kjøringer i samme sekund, eller en klokke justert bakover" >&2
  exit 1
fi

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
# ÉN DUMP, TO FORBRUKERE — OG INGEN MELLOMFIL (Cursor P2 #191 → eiers
# tmpfs-dom 28/8 → sak #244). Historikken i tre trinn, fordi hvert
# trinn er en invariant som fortsatt gjelder: (1) to `pg_dump` lot
# porten bevise konsistens for et annet snapshot enn det som ble
# arkivert — derfor ÉN passering. (2) klartekst persisteres aldri i
# katalogen — privatnøkkelen er ikke på verten, og da er gjenopprettbare
# klartekstblokker i /var/backups inkonsekvent. (3, #244) tmpfs var
# heller ikke nok: verten har aktiv ukryptert swap, og tmpfs-sider kan
# swappes — så dumpen STRØMMES nå til begge forbrukerne i samme
# passering (se dump-blokken under), og klarteksten ligger aldri på noe
# filsystem i det hele tatt. Katalogen her bærer bare medlemslistene og
# det navngitte røret — aldri dumpbyte på lagring.
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
# 1` gjør at feien aldri kan vandre ut av `/dev/shm` selv. `-type d`
# holder den til formen våre rester faktisk har (`mktemp -d`): en
# root-eid FIL med samme navn er ikke vår, og en `rm -rf` som ikke
# trenger å treffe den, skal ikke kunne det. Null treff
# gir exit 0 — feien dreper ikke kjøringen når det ikke var noe å feie
# — mens et manglende `/dev/shm` gir exit 1 og stopper her, som er
# riktig vei: `mktemp -d -p /dev/shm` på neste linje ville dødd uansett.
find /dev/shm -mindepth 1 -maxdepth 1 -user root -type d \
     -name 'disponit-backup.*' -exec rm -rf {} +
RAA_KAT=$(mktemp -d -p /dev/shm disponit-backup.XXXXXXXX)
chmod 700 "$RAA_KAT"
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
    # OG UNLINKEN MÅ VÆRE HOLDBAR (Codex P1 på `f2284d9`). En `unlink` er
    # en katalogoperasjon som alle andre: den endrer bare cachet metadata.
    # Krasjer verten etter at vi har forkastet paret, kan filsystemet
    # gjenopprette ETT eller BEGGE de endelige navnene — inkludert dumpen
    # uten arkivet, som er nøyaktig løgnen `sync`-kjeden i finaliseringen
    # finnes for å hindre. Tjenesten meldte feil; disken viste dagens
    # backup.
    #
    # HER, OG BARE HER. Feilarmen etter siste katalog-sync rydder selv —
    # den må, ellers ser trapen to endelige navn og lar paret stå — men
    # den avslutter gjennom `exit 1`, altså gjennom denne trapen, som da
    # fester unlinken. Én `sync` dekker begge veiene; en kopi i feilarmen
    # ville bare vært et andre sted å glemme.
    #
    # `slett_par` fjerner også endelige navn, men der er holdbarhet ikke
    # det samme kravet: gjenoppstår et UTLØPT par etter et krasj, er
    # utfallet en gammel backup som lever en runde til, ikke et forkastet
    # par som utgir seg for å være dagens.
    #
    # FAIL-CLOSED er allerede gitt: grenen kjøres bare når `PAR_KLAR` er
    # tom, og hver vei dit avslutter med status ulik 0. Feiler syncen, er
    # det derfor ikke noe utfall å endre — bare noe å SI, høyt nok til at
    # operatøren vet at katalogen trenger ettersyn.
    sync "$KATALOG" || echo "ADVARSEL: ryddingen av det forkastede paret" \
      "kunne ikke gjøres holdbar — $KATALOG kan etter et krasj gjenoppstå" \
      "med ett eller begge de endelige navnene. Krever ettersyn." >&2
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
# … OG FERDIGNAVNEDE HALVPAR (sak #246 pkt. 1). Dør prosessen mellom
# finaliseringens to `mv`, står et ARKIV med endelig navn uten sin dump.
# Retensjonen finner kandidater utelukkende fra dumpnavn, så det arkivet
# ville ellers ligget for alltid. `flock` over garanterer at ingen annen
# kjøring er midt i sine to `mv` akkurat nå — et arkiv uten dump ER en
# rest. (Dump uten arkiv finnes ikke fra denne koden: dumpen får navnet
# SIST, og trapen/feilarmen rydder halvpar med holdbar sync.)
for ark in "$KATALOG"/disponit-*.inndata.tar.age; do
  [ -e "$ark" ] || continue
  st=$(basename "$ark"); st=${st#disponit-}; st=${st%.inndata.tar.age}
  [ -f "$KATALOG/disponit-$st.dump.age" ] || rm -f "$ark"
done
# … OG VERIFISERINGSBASER ETTER SIGKILL (sak #246 pkt. 2). `$VERIF`
# slippes bare av EXIT-trapen, og navnet er PID-avledet: en drept
# kjøring etterlater basen sin i clusteret, og neste kjøring lager en
# ny. `flock` garanterer at enhver `disponit_backup_verif_%` nå er en
# rest — og feien er fail-closed: kan ikke listen leses, ville også
# `createdb` under feilet, så det er riktig å stoppe her.
sudo -u postgres psql -Atc   "SELECT datname FROM pg_database
    WHERE datname LIKE 'disponit\_backup\_verif\_%'"   | while IFS= read -r db; do
      [ -n "$db" ] || continue
      sudo -u postgres dropdb --if-exists "$db"
    done

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
#
# SPARINGEN ER ET UNNTAK, IKKE EN REGEL (Codex P1 på `db2eeda`). Sparingen
# over finnes for ÉN grunn: at sveipen aldri skal etterlate katalogen uten
# et gjenopprettingspunkt. Er det allerede et komplett ULØPT par i
# katalogen, er den grunnen borte — punktet finnes, og et utløpt par i
# tillegg er bare beslaglagt plass. Verre: er det nettopp det utløpte paret
# som må vekk for at diskporten under skal passere, avbryter porten hver
# eneste nattbackup, mens sparingen holder på det den porten trenger
# frigjort. Da står installasjonen uten NYE backuper til det uløpte paret
# selv utløper ~30 dager senere — en sperre som fornyer seg selv.
#
# Derfor måles det først: finnes et komplett par som IKKE er utløpt, spares
# ingenting, og sveipen tar alt den fant.
#
# `! -mtime +$DAGER` er det EKSAKTE komplementet til søket over, og et eget
# søk — ikke en mengdedifferanse mot `$UTLOPTE` i skallet. Å filtrere den
# ene listen mot den andre ville krevd `printf | grep -qx` per kandidat, og
# den pipen har nøyaktig SIGPIPE-formen retensjonen alt er blitt felt på:
# `grep -q` lukker røret på treff, `printf` dør på 141, og med `pipefail`
# leses et TREFF som et bom. Da hadde porten sagt «ingen uløpte par» i
# nettopp det tilfellet den finnes for.
#
# Feiler søket, avbryter vi — samme fail-closed-linje som søket over. Å
# gjette «ingen uløpte par» ville spart et utløpt par unødig; å gjette
# motsatt vei ville slettet det siste punktet. Ingen av gjetningene er verdt
# en stille kjøring.
HAR_ULOPT_PAR=""
if [ -n "$UTLOPTE" ]; then
  ULOPTE=$(find "$KATALOG" -name 'disponit-*.dump.age' \
                ! -mtime +"$DAGER") || {
    echo "AVBRUTT: søket etter uløpte par feilet — sveipen kan ikke vite" \
         "om det finnes et gjenopprettingspunkt som gjør sparing unødig" >&2
    exit 1
  }
  while IFS= read -r dump; do
    [ -n "$dump" ] || continue
    st=$(basename "$dump"); st=${st#disponit-}; st=${st%.dump.age}
    if [ -f "$KATALOG/disponit-$st.inndata.tar.age" ]; then
      HAR_ULOPT_PAR=1
      break
    fi
  done <<< "$ULOPTE"
fi
SPART=""
if [ -n "$UTLOPTE" ] && [ -z "$HAR_ULOPT_PAR" ]; then
  # `sort | head -1` GIR SIGPIPE (Codex P2): `head` lukker røret etter
  # første linje, `sort` dør på signal 141, og med `pipefail` + `set -e`
  # avbrytes hele kjøringen. Med nok utløpte par ville retensjonen da
  # låst seg permanent — hver kjøring møter den samme listen og dør på
  # samme sted. `sort -r | sed -n 1p` leser strømmen ferdig.
  #
  # OG BARE KOMPLETTE PAR KAN SPARES (Codex P2). Er den nyeste utløpte
  # dumpen uten arkiv, er den ikke et gjenopprettingspunkt — å spare den
  # og slette et eldre KOMPLETT par ville etterlatt oss med noe som per
  # definisjon ikke kan gjenopprettes. Vi filtrerer derfor på at begge
  # halvdelene finnes, og sparer den nyeste som gjør det.
  #
  # ... MEN «FORETREKK» ER IKKE «KREV» (Codex P1 på `0438007`). Filteret
  # var absolutt, og det har en garantert forekomst: FØRSTE kjøring av
  # denne versjonen etter et opphold lengre enn 30 dager. Da er HVER
  # backup i katalogen fra før endringen, altså per definisjon uten
  # arkiv — filteret finner ingen kandidat, `SPART` blir tom, og løkka
  # under sletter samtlige dumper FØR `pg_dump` er forsøkt. Feiler et
  # senere ledd, står installasjonen uten database-backup i det hele
  # tatt. Endringsbeskrivelsen behandler nettopp disse dumpene som
  # gyldige basebackuper, så å slette dem alle er ikke en streng
  # tolkning av regelen — det er å bryte den regelen sparingen finnes for.
  #
  # Rangeringen er derfor: komplett par først, ellers nyeste dump. En dump
  # alene gjenoppretter basen; den mangler bare inndata-lageret, og det er
  # nøyaktig det ene gjenopprettingspunktet som fantes før #191. Det er
  # strengt mer enn ingenting, og «ingenting» er det eneste alternativet i
  # dette tilfellet.
  SPART=""
  SORTERTE_UTLOPTE=$(printf '%s\n' "$UTLOPTE" | sed '/^$/d' | sort -r)
  while IFS= read -r kandidat; do
    [ -n "$kandidat" ] || continue
    st=$(basename "$kandidat"); st=${st#disponit-}; st=${st%.dump.age}
    if [ -f "$KATALOG/disponit-$st.inndata.tar.age" ]; then
      SPART="$kandidat"
      break
    fi
  done <<< "$SORTERTE_UTLOPTE"
  # `sed -n 1p` og ikke `head -1`: samme SIGPIPE-grunn som over.
  [ -n "$SPART" ] || SPART=$(printf '%s\n' "$SORTERTE_UTLOPTE" | sed -n 1p)
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
# KLARTEKSTEN LIGGER INGEN STEDER — DEN STRØMMES (sak #244, Codex P1 ×2
# + P2 fra #229). tmpfs-dommen 28/8 holdt klarteksten unna katalogen,
# men ikke unna PERSISTENT lagring: verten har aktiv, ukryptert swap
# (målt: /swapfile 4G), tmpfs-sider kan swappes, og `rm` sletter ingen
# swap-blokker. SIGKILL/strømbrudd/snapshot er samme sak fra en annen
# kant, og /dev/shm ble aldri målt før bruk. Svaret som lukker alle tre
# er at dumpen aldri MELLOMLAGRES: `pg_dump` strømmes til BEGGE
# forbrukerne samtidig — `age` (som skriver backupen) og `pg_restore`
# (som verifiserer) leser nøyaktig de samme bytene fra samme passering,
# så verifiseringen gjelder fortsatt filen som havner i katalogen.
#
# STATUSEN FRA HVERT LEDD PROPAGERER (sakens eget akseptkriterium, og
# defektklassen `find < <(...)` alt er felt på): `pipefail` dekker
# pg_dump → tee → pg_restore, men en prosess-substitusjon ville mistet
# `age` sin exitkode — derfor NAVNGITT RØR + eksplisitt `wait`, som gir
# den ekte statusen. Røret bor i den private tmpfs-katalogen (0700,
# root-eid) og bærer aldri data på lagring — et rør er kjernebuffer.
# `$DELVIS` pre-opprettes 600 så ingen andre kan lese den mens den
# skrives; `age` sitt truncate beholder modusen.
VERIF="disponit_backup_verif_$$"
sudo -u postgres createdb "$VERIF"
DUMPROR="$RAA_KAT/dump.fifo"
mkfifo -m 600 "$DUMPROR"
: > "$DELVIS"
chmod 600 "$DELVIS"
age -R "$MOTTAKER" < "$DUMPROR" > "$DELVIS" &
AGE_PID=$!
sudo -u postgres pg_dump --format=custom --dbname=disponit \
  | tee "$DUMPROR" \
  | sudo -u postgres pg_restore --dbname="$VERIF" --no-owner \
      --role=postgres
wait "$AGE_PID" || {
  echo "AVBRUTT: age feilet på dumpstrømmen — den krypterte filen kan" \
       "ikke stoles på, uansett hva restoren sa" >&2
  exit 1
}
TABELLER=$(sudo -u postgres psql -Atd "$VERIF" -c \
  "SELECT count(*) FROM pg_tables WHERE schemaname='public'")
[ "$TABELLER" -ge 10 ] || {
  echo "AVBRUTT: gjenoppretting ga bare $TABELLER tabeller" >&2
  exit 1
}
STORRELSE=$(stat -c%s "$DELVIS")
[ "$STORRELSE" -gt 1024 ] || { echo "AVBRUTT: backupfilen er tom" >&2; exit 1; }

# KRAVLISTEN LESES NÅ — OG ENGANGSBASEN SLIPPES (sak #246 pkt. 4/5).
# `$VERIF` levde før til EXIT-trapen, så under `tar` lå hele den
# restaurerte basen OG arkivet på samme /var — en peak diskporten aldri
# målte. Kravlisten trenger bare den restaurerte basen, ikke arkivet, så
# den leses her; deretter droppes basen eksplisitt, og arkivfasen kjører
# uten den. Fasene sameksisterer ikke lenger, og portens formel
# (lager + dump + margin) ER det faktiske taket for hver fase: fase 1 er
# dump-delvis + engangsbasen (≤ dump ukomprimert ×2 ≤ formelens ledd),
# fase 2 er dump-delvis + arkivet (≤ dump + lager).
if [ "$(sudo -u postgres psql -Atd "$VERIF" -c \
        "SELECT to_regclass('public.inndata_artefakt') IS NOT NULL")" = t ]; then
  # LINJESKIFT I EN STI KAN IKKE SAMMENLIGNES LINJEBASERT (sak #246
  # pkt. 6): psql -At skriver én sti per linje, så et linjeskift INNE i
  # `lager_sti` ville splittet den i to krav som aldri matcher noe
  # arkivmedlem — hver backup feiler, med en melding om manglende filer
  # som ikke mangler. Målt i basen, der stien fortsatt er én verdi, og
  # avvist med sin egen ordlyd i stedet for den villedende.
  NL_RADER=$(sudo -u postgres psql -Atd "$VERIF" -c \
    "SELECT count(*) FROM inndata_artefakt
      WHERE status IN ('lastet','bundet')
        AND lager_sti LIKE '%' || chr(10) || '%'")
  [ "$NL_RADER" = 0 ] || {
    echo "AVBRUTT: $NL_RADER lager_sti-rad(er) bærer linjeskift —" \
         "arkivporten er linjebasert og kan ikke måle dem" >&2
    exit 1
  }
  sudo -u postgres psql -Atd "$VERIF" -c \
    "SELECT lager_sti FROM inndata_artefakt
      WHERE status IN ('lastet','bundet') AND lager_sti IS NOT NULL" \
    | sed '/^$/d' | sort -u > "$LISTE.krav"
  # DIGESTENE (070, sak #245): radens `lagret_sha256` er målt av
  # skriveveien over nøyaktig de bytene som ble fsync-et — den ene
  # verdien som lar backupen felle en AVKORTET eller KORRUPT ciphertext
  # uten å kunne lese den. `sha256sum -c`-formatet bygges her; NULL
  # hoppes over med vilje (rader født før 070 — en diktet digest ville
  # gjort porten til en løgn), og en base eldre enn 070 mangler hele
  # kolonnen og gir en tom fil i stedet for en død backup.
  if [ "$(sudo -u postgres psql -Atd "$VERIF" -c \
          "SELECT count(*) FROM information_schema.columns
            WHERE table_name='inndata_artefakt'
              AND column_name='lagret_sha256'")" = 1 ]; then
    sudo -u postgres psql -Atd "$VERIF" -c \
      "SELECT lagret_sha256 || '  ' || lager_sti FROM inndata_artefakt
        WHERE status IN ('lastet','bundet') AND lager_sti IS NOT NULL
          AND lagret_sha256 IS NOT NULL" > "$LISTE.digester"
  else
    : > "$LISTE.digester"
  fi
else
  : > "$LISTE.krav"
  : > "$LISTE.digester"
fi
VERIF_NAVN="$VERIF"
sudo -u postgres dropdb --if-exists "$VERIF"
VERIF=""

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
# Medlemslisten fanges fra DENNE ene passeringen (`--index-file`).
# Den krypterte filen kan ikke leses tilbake her — privatnøkkelen er ikke på
# verten, med vilje — så porten under måler samme strøm som ble skrevet,
# nøyaktig som dumpens egen verifisering gjør det.
# EN PÅGÅENDE OPPLASTING SKAL IKKE DREPE BACKUPEN (Codex P2, runde 7).
# `inndata.py` skriver ciphertexten til `<bunt>.bin.tmp` og gjør `os.replace`
# først når den er hel. Overlapper en opplasting dette passet, leser `tar`
# den midlertidige fila mens den vokser eller byttes ut, melder «file changed
# as we read it» og returnerer status 1 — og med `pipefail` dør HELE
# nattbackupen av en fil dumpen ikke engang refererer til.
#
# `--exclude` løser det ved roten i stedet for å dempe symptomet: den
# midlertidige fila er per konstruksjon ikke en bunt ennå, så den har
# ingenting i arkivet å gjøre.
#
# MØNSTERET ER ANKRET TIL FILNIVÅET (Codex P2, runde 8 og 9). To runder
# på samme mønster, og begge gangene var feilen den samme: `--exclude` er
# UANKRET etter enhver `/`, så det matcher katalognavn like godt som
# filnavn. `*.tmp` tok en tenant som het `noe.tmp`; `*.bin.tmp` tok en som
# het `noe.bin.tmp`. `_stikomponent` tillater begge.
#
# `./*/*.bin.tmp` sier hva vi faktisk mener: en fil på LØVNIVÅ under en
# tenantkatalog. En katalog kan ikke matche det mønsteret, uansett hva
# kunden heter. `tar`s
# `--exclude` matcher mot HELE medlemsstien, og `_stikomponent` tillater
# en tenant-ID som `customer.tmp` — med det brede mønsteret ville hele
# den kundens katalog og hver ferdige `.bin` under den falt ut, og
# `comm`-porten avbrutt backupen for den installasjonen hver eneste natt.
# Suffikset er `inndata.py`s eget (`<bunt>.bin.tmp`), så det smale
# mønsteret dekker nøyaktig det som skal dekkes. En `.bin` som dumpen KREVER er ferdig skrevet
# og omdøpt før raden ble committet (rekkefølgen er utledet lenger oppe), så
# ekskluderingen kan ikke skjule noe porten trenger — og skulle den likevel
# mangle, feller innholdsporten under det høyt.
# LISTEN GÅR TIL `--index-file`, DIAGNOSTIKKEN TIL JOURNALEN (sak #246
# pkt. 8). `2>"$LISTE"` fanget BÅDE medlemsnavnene og enhver diagnostikk
# — en uleselig fil, en I/O-feil — og lot tjenesten sitte igjen med en
# exitkode uten årsak i journald. Diagnostikklinjer i listen kunne aldri
# gi falsk PASS (`comm -23` er enveis), bare falsk AVBRUTT; nå finnes
# ingen av delene. OG SITERINGEN ER LITERAL (pkt. 6): GNU tar escaper
# ellers bakoverskråstrek/tab i listingen, mens psql-siden skriver rå
# byte — da matchet en slik tenant-ID aldri sitt eget arkivmedlem, og
# HVER backup for den kunden feilet. Linjeskift, det ene tegnet literal
# linjebasert form ikke bærer, er alt avvist med egen ordlyd over.
tar --create --directory="$LAGER" --verbose --index-file="$LISTE" \
    --quoting-style=literal --no-wildcards-match-slash \
    --exclude='./*/*.bin.tmp' \
    --file=- . \
  | age -R "$MOTTAKER" > "$ARKIV_DELVIS"
chmod 600 "$ARKIV_DELVIS"

# Gjenopprettingsverifiseringens andre halvdel: hver `lager_sti` i en
# `lastet`/`bundet` rad i den GJENOPPRETTEDE basen skal finnes i arkivet.
# Tabellen kan mangle i en base som er eldre enn 058 — da er kravmengden tom
# og porten passerer, i stedet for at hele backupen dør på en manglende
# tabell.
sed 's#^\./##' "$LISTE" | sed '/^$/d' | sort -u > "$LISTE.sett"
# Kravlisten sto klar FØR arkivfasen (pkt. 4/5-blokken over) — her måles
# den bare mot det som faktisk ble skrevet.
MANGLER=$(comm -23 "$LISTE.krav" "$LISTE.sett")
[ -z "$MANGLER" ] || {
  # STIENE GÅR IKKE I LOGGEN (Codex P2, runde 7). `disponit-backup.service`
  # overstyrer ikke strømmene, så stderr havner i journald — og på en vert
  # med persistent journal blir tenant-ID-ene liggende på disk. Det er
  # nøyaktig lekkasjen listene ble flyttet til tmpfs for å unngå, og
  # arkivet krypteres for å hindre.
  #
  # Antallet og en KORTHASH er nok til å finne igjen raden: hashen er
  # stabil, så to kjøringer som klager på samme bunt kan sammenlignes,
  # mens strengen ikke kan leses tilbake til en kunde. Vil operatøren ha
  # stiene, ligger de i `$LISTE.krav` på tmpfs så lenge kjøringen varer.
  echo "AVBRUTT: $(printf '%s\n' "$MANGLER" | wc -l) rad(er) i dumpen peker" \
       "på filer arkivet ikke har — en restore ville gitt rader uten filer." \
       "Korthash per sti (se \$LISTE.krav på tmpfs for klartekst):" >&2
  printf '%s\n' "$MANGLER" | sed -n '1,5p' \
    | while IFS= read -r sti; do
        printf '  %s\n' "$(printf '%s' "$sti" | sha256sum | cut -c1-12)" >&2
      done
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
       "båret navnet uten innholdet. Korthash per sti:" >&2
  printf '%s' "$mens_manglet" | sed -n '1,5p' \
    | while IFS= read -r sti; do
        printf '  %s\n' "$(printf '%s' "$sti" | sha256sum | cut -c1-12)" >&2
      done
  exit 1
}

# INNHOLDET MÅLES MOT SKRIVEVEIENS EGEN DIGEST (070, sak #245).
# Navneporten over feller tomme og forsvunne filer; DENNE feller delvis
# avkorting og byte-korrupsjon — formen sviktende lagring faktisk tar
# når den ikke tar alt. Ciphertexten kan ikke leses her (DEK-en er
# ikke på verten, med vilje), men den kan MÅLES: `lagret_sha256` ble
# regnet av API-et over nøyaktig de bytene som ble fsync-et.
# Feilutskriften korthashes som de andre portenes — stiene hører ikke
# hjemme i journald.
if [ -s "$LISTE.digester" ]; then
  DIGESTFEIL=$( (cd "$LAGER" && sha256sum --check --quiet --strict \
                   "$LISTE.digester" 2>&1) ) || {
    echo "AVBRUTT: $(printf '%s\n' "$DIGESTFEIL" | grep -c ':') av" \
         "$(wc -l < "$LISTE.digester") ciphertext-fil(er) matcher ikke" \
         "digesten skriveveien målte — avkortet eller korrupt lagring." \
         "Korthash per linje:" >&2
    printf '%s\n' "$DIGESTFEIL" | sed -n '1,5p' \
      | while IFS= read -r linje; do
          printf '  %s\n' "$(printf '%s' "$linje" | sha256sum | cut -c1-12)" >&2
        done
    exit 1
  }
fi

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
# DEN SISTE SYNC-EN ER EN PORT, IKKE EN FORMALITET (Codex P1, runde 9).
# `PAR_KLAR` ble satt uansett hva den returnerte — og feilet den på en
# I/O- eller filsystemfeil, avsluttet `set -e` gjennom EXIT-trapen mens
# `PAR_KLAR` fortsatt var tom. Men trapen spør DISKEN, og der sto begge
# de endelige navnene: paret ble bevart. Tjenesten meldte feil, mens
# retensjonen og operatøren så et ferdig par som aldri passerte
# holdbarhetskravet — og et krasj etterpå kunne blottlagt dumpen uten
# arkivet, nøyaktig løgnen `sync`-en finnes for å hindre.
#
# Feiler den, rydder vi selv FØR trapen rekker å se to navn, og lar så
# feilen forplante seg.
#
# HOLDBARHETEN AV DENNE RYDDINGEN LIGGER I TRAPEN (Codex P1 på `f2284d9`).
# `rm` her er også bare cachet metadata, så et krasj etterpå kan
# gjenopprette ett eller begge de endelige navnene. `exit 1` går gjennom
# EXIT-trapen, og `opprydd` ser da at `PAR_KLAR` er tom og at navnene er
# borte — den kjører `rm -f` på nytt (uvirksomt) og fester katalogen med
# `sync "$KATALOG"`. Den fsyncen står ÉTT sted med vilje: her ville den
# vært en andre kopi å glemme, og trapen dekker i tillegg hver annen vei
# som forkaster et halvt par.
if ! sync "$KATALOG"; then
  echo "AVBRUTT: siste katalog-sync feilet — paret publiseres ikke" >&2
  rm -f "$FIL" "$ARKIV"
  exit 1
fi
PAR_KLAR=1

# DEN SPARTE TAS NÅ. Det nye paret står med sine endelige navn og er
# fsynket, så det finnes et gjenopprettingspunkt — og først da er det
# forsvarlig å fjerne det siste gamle. Fristen er den samme som sveipen
# målte mot, så den er fortsatt utløpt.
if [ -n "$SPART" ]; then
  slett_par "$SPART"
  SLETTET=$((SLETTET + 1))
fi


echo "backup ok: $FIL (${STORRELSE} B), verifisert mot $VERIF_NAVN" \
     "(${TABELLER} tabeller); arkiv: $ARKIV ($(stat -c%s "$ARKIV") B," \
     "${BUNTER} bunt(er) bekreftet); slettet $SLETTET utløpte par"
