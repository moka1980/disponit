# M-57-release på staging — nøyaktig hva som må kjøres

Skrevet 30/8-2026. Alt under er *staging* — produksjon
er utenfor enhver avtale. Kommandoene på staging-siden rører
`/opt/disponit` og `/etc/disponit` og kjøres derfor KUN av eier eller
etter uttrykkelig beskjed.

## 0. Forutsetninger (status 30/8-2026)

- Alle M-57-kjerne-PR-ene er på main t.o.m. #267 (Slett/Avbryt +
  migrasjon 069).
- `manifest.yaml` har `status: under_utvikling` — flippen til aktiv er
  KATALOGENS avlesning av en aksepthendelse (049–053), aldri en påstand.
  Registreringsskriptet registrerer ALDRI status aktiv.
- Staging-sjekklisten i manifestet har flere punkter som IKKE er
  flippbare på evidens ennå (`m57-v1`-grensen mangler suitemålinger
  m.m., se notatene i manifestet / #166).

## 1. Utrulling av kode + migrasjoner (eier, på staging)

```sh
cd /opt/disponit && git fetch origin
git checkout <release-sha>              # DEN PINNEDE revisjonen — aldri en grentupp:
                                        # hashene i §2 er regnet mot ett tre, og et
                                        # senere main-tips kan ha endret manifest
                                        # eller rapportskjema uten at noen ser det
sudo deploy/staging/opp.sh              # vedlikeholdsvindu: stopp → migrér (per 30/8: 001–073) → aktiver → start
```

Stemmer ikke `<release-sha>` med treet §2s hasher ble regnet mot,
regn dem ut på nytt DER (skriptet gjør det selv for de to beregnede;
tabellens fire oppgitte må release-materialet bekrefte på nytt).

`opp.sh` kjører `migrer.py`, som også deler ut runtime-EXECUTE på
`bestill_tidligsletting(TEXT, UUID)` (lagt inn i #267).
Forward-only: rollback av kode etter at 069 er kjørt er forbudt
(boot-sjekken krever eksakt migrasjonsmatch — verifisert 30/8: API-et
nekter boot på 68 ≠ 69).

## 2. Registrering av modulkjeden (eier, på staging)

```sh
DISPONIT_MIGRATOR_URL=… python3 deploy/staging/registrer-m57-ats.py \
    <release_id> <kontrakt_hash> <artifact_digest> \
    <payload_skjema_hash> <kvittering_skjema_hash>
```

Idempotent; registrerer `installer_modul` → `registrer_kontrakt`
(`krever_outbox`/`kompenserende`) → `registrer_release` →
`registrer_oppdragstype` → rapportskjema (v1 KUN ved førstegangs-
installasjon — typeraden er immutabel og bærer den gangens v1-hash) →
`registrer_artefakttype` → **SKJEMA_V2 + registrer_artefaktskjemaversjon**
(BESLUTNING-168: v2 gjeldende og v1 avviklet i samme transaksjon).
Inputs — HVER HASH ER SITT EGET DOKUMENT (immutable rader, ingen retting):

| Input | Hvor den kommer fra | Kan beregnes herfra? |
|---|---|---|
| `release_id` | Velges av eier/arkitekt (m56-formen: f.eks. `m57-r1`) | valg, ikke beregning |
| `kontrakt_hash` | Release-materialet (014b) — kontraktdokumentets sha256 | NEI — eies av arkitekten |
| `artifact_digest` | **MODELLENS** manifest-sha256 fra modellageret på staging-verten (Ollama registry-manifest). M57-avviket: modulens «image» ER modellen; biasmålingene (port 17) og kvitteringene binder seg til samme verdi | NEI — leses på staging (`ollama show`-manifestet) |
| `payload_skjema_hash` | Release-materialet (014b) | NEI |
| `kvittering_skjema_hash` | Plattformkontrakten (PR-006) | NEI |

Skriptet beregner selv (verifisert lokalt i kveld mot arbeidstreet
`slett-avbryt-evaluering`, dvs. main + #267):

- manifestets kanoniske projeksjon:
  `2f81419c1b188c74a67e8f74c0b11b27768c81a5f6adbd9bad0aa5bfac2194fe`
  (endres hvis manifest.yaml endres strukturelt før release)
- rapportskjemaets JCS-hash:
  `bbc7f68b99a9140ea956c0a3e239b2a7efb1618fdc9a988dcd86e8b3591921d7`

## 3. Arbeideren (eier, på staging)

`deploy/staging/disponit-m57.service` (ferdig i repoet):

1. `useradd` disponit-m57; kopiér uniten til /etc/systemd/system/.
2. Fyll `/etc/disponit/m57/{konfig,DISPONIT_MODULTOKEN,kvitteringsnokkel.json,biasmaalinger.json}` (0600).
   Modultokenet: onboarding-seremonien (035) via token-cli.
3. Ollama på 127.0.0.1 med valgt modell. NB: DEPLOY.md sier 7B-modell i
   staging = oppgraderingsutløser Cloud Server S → L (16 GB). En liten
   kvantisert modell (~3B) går på S for røyk-test.
4. `systemctl enable --now` FØRST når modulen er aktiv — før det poller
   claim seg bare varm på 403.

## 4. Aksept/sjekklisterunden (etterpå)

- `deploy/staging/seed-rekruttering-demo.py` for syntetisk runde.
- Sjekklistepunktene flippes bare på evidens i `m57-v1`-grensen; flere
  punkter venter på #166-bindingen — manifestet sier selv hvilke.
- Flippen status → aktiv skjer via aksepthendelsen (049–053, m56-malen:
  `m56-aksept.py`-formen), ikke for hånd.

## Status ved skriving

Gjort (fra utviklerklonen, trygt): registreringsskriptet verifisert mot
treet (begge beregnede hashene over); eierskapsdesignet dekker
069-døren. Gjenstår (krever eier/staging-verten): alt i §1–§4, og de
fire oppgitte hashene (arkitekt/release-materiale + modellvalg).

## Gjennomført 30/8-2026 (v2-flippen)

Originalreleasen (m57-r1-20260827) sto alt i drift; §1 og §3 var gjort.
Det som gjensto etter BESLUTNING-168-kjeden (#288–#290) ble kjørt 30/8:

- SKJEMA_V2 registrert (`eae4519e986f…`), `registrer_artefaktskjemaversjon`
  flippet v2 gjeldende / v1 avviklet (payloadfri=true, forrige_versjon=1).
- `disponit-m57` restartet på #290-koden — produsenten bygger nå
  beslutningssporet; leseflaten supplerer funn fra kandidatlageret.
- Lærdom skrevet inn i skriptet: en re-kjøring etter at v1-skjemaet har
  utviklet seg i treet felte `registrer_artefakttype` på 'er immutable' —
  skriptet hopper nå over v1-halvdelen når typen alt finnes.
