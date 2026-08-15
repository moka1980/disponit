# PR-014 SPESIFIKASJON v2 — DELTA (ni kontrakter → GO)

**Draft: Claude.ai · Retningen står. Ni kontrakter lukket — tre av dem
er nye tillitsgrenser (egress/sandkasse, domeneverifikator,
artefaktprotokoll).**

## 1. Domeneeierskap er PLATTFORMREGEL, ikke policyvilkår

Kunden kan ALDRI fjerne kontrollen, heller ikke med fire øyne.
- **Plattformregel:** ingen revisjon mot et mål uten gyldig, ikke-utløpt
  eierskapsbevis. Håndheves **to steder**: (a) før oppdragsopprettelse
  (beslutningsveien), (b) ved claim/utførelse — fordi eierskap kan
  tilbakekalles etter beslutningen.
- Policyen kan stille STRENGERE krav (hvilke av tenantens verifiserte
  domener, hvilket omfang, hvilke tider) — aldri svakere.
- Regelen ligger utenfor policyskjemaet, som fire-øyne-gulvet i PR-013.

## 2. `v_domene` — komplett verifikatorlivsløp (migrasjon 013)

```sql
domeneeierskap(tenant, hostname, status['ventende'|'verifisert'|'utlopt'|'tilbakekalt'],
  challenge_token, challenge_utstedt, verifisert_ts, utloper,
  wildcard BOOLEAN NOT NULL DEFAULT false, PRIMARY KEY (tenant, hostname))
domeneeierskap_hendelse(...)   -- append-only evidens
```
- **DNS-TXT-challenge:** tilfeldig token (≥128 bit) på
  `_disponit-verifisering.<hostname>`. **Ingen «portalregistrering» uten
  faktisk kontrollbevis.**
- **Eksakt tenant–hostname-binding.** Wildcard KUN når challenge er
  bekreftet på apex; dekker ett nivå subdomener, aldri nestet.
- **Utløp 90 dager + periodisk reverifisering** (ukentlig jobb); feilet
  reverifisering → `utlopt`, ingen nye oppdrag.
- **Tilbakekalling** er umiddelbar og gjelder pågående oppdrag (§1b).
- All statusendring i append-only hendelsestabell.
- `v_domene` registreres som verifikator med egen nøkkel; attestasjonen
  binder `(tenant, hostname, utloper, jti)`.

## 3. Egress-proxy — eierskap beskytter ikke mot SSRF/rebinding

Et eid domene kan peke til localhost eller skifte DNS-svar etter
verifikasjon. ALL browsertrafikk gjennom kontrollert egress-proxy
(gjenbruker PR-010 v6 §1-modellen):
- **Kun globalt routbare adresser** (positiv regel, ikke blocklist) —
  blokkerer localhost, link-local, private, CGNAT, dokumentasjons- og
  spesialnett, metadata-endepunkter.
- **Blandet offentlig/privat DNS-svar → hele requesten avvist.**
- **IP pinnes til forbindelsen**; original hostname for TLS-SNI og
  sertifikat; revalidering og repinning **ved hvert redirect og hver ny
  forbindelse** (også subresurser).
- **Kun normalisert HTTPS i v1.** `file:`, `data:`, `blob:` forbudt som
  toppnavigasjon; ingen FTP; ingen vilkårlige porter (443 kun).
- **Toppnivå-redirect til annet hostname krever separat eierskapsbevis**
  — ellers avbrytes revisjonen med UNNTAK.

## 4. Browseren isoleres fra dag én — egen container

Systemd-herding er ikke nok når fremmed JavaScript kjøres. Krav:
non-root med **Chromium-sandbox AKTIV (aldri `--no-sandbox`)** ·
read-only rot, tom tmpfs, ingen host-mounts · alle capabilities droppet,
seccomp + AppArmor · **separat nettverkssegment**, all utgang gjennom
egress-proxyen (§3) · **ingen tilgang til DB, tenantnøkler,
metadata-endepunkt eller andre interne tjenester** · CPU-, minne-,
prosess-, side- og tidsgrenser · **ny browser-context per oppdrag**, null
persistent cookie/cache/service worker · downloads, popup, clipboard og
filtilgang deaktivert.

## 5. Artefaktprotokoll — modulen rører aldri DB eller DEK

v1 var selvmotsigende (null DB-skriving OG kryptert lagring). Rettet —
kapabilitetsbeskyttet opplasting:
1. Modulen laster opp **lukket rapport** til `POST /v1/artefakt`
   (kvitteringskapabiliteten autoriserer).
2. **API-et** validerer størrelse + skjema, **krypterer med tenant-DEK**,
   lagrer som `staged`.
3. API returnerer `artifact_id` + **serverberegnet hash**.
4. Resultatkvitteringen **binder begge**.
5. Kvitteringsingest **promoterer artefaktet og knytter det til oppdraget
   ATOMISK** i samme transaksjon som statusovergangen.
6. **Kvittering godtas ALDRI før artefaktet er varig lagret og verifisert.**
7. Krasjede/`staged` artefakter ryddes idempotent (timer, TTL 24 t).
Modulen ser aldri DEK, aldri ciphertext, aldri DB.

## 6. Rapporten er mulig person- og forretningsdata

Påstanden «ingen persondata» var ugyldig — URL-query, selektorer og
genererte ID-er kan bære e-post, kundenummer eller tokens. v1:
- **Query og fragment fjernes** fra lagret URL (kun scheme+host+path).
- **Aldri lagre** DOM, HTML, tekstinnhold, screenshots eller nettverkslogger.
- **Selektorer saniteres og lengdebegrenses** (maks 200 tegn, attributt-
  verdier strippet).
- Maks 10 eksempler per regel, maks 500 funn, maks 1 MiB rapport.
- **Klassifiseres som mulig person-/forretningsdata**: tenant-kryptert,
  crypto-shredding, innsyn krever eget scope (`artifacts:read`).
- Retention 12 måneder, dokumentert.

## 7. Ærlig navngivning: **automatisk WCAG-kontroll**

Axe-core påviser kun automatiserbare forhold. Produktet og rapporten
heter **«automatisk WCAG-kontroll»** — aldri «oppfyller WCAG» eller
«WCAG-revisjon». Rapporten MÅ vise: standard + regelsett · axe-,
Chromium- og **container-image-digest** · viewport, locale, timezone,
konfigurasjon · **hvilke sider som faktisk ble testet** · **eksplisitt at
manuelle WCAG-kriterier ikke er vurdert**.
Ærlighet om reproduserbarhet: versjonene gjør KJØRINGEN sporbar, men
dynamisk webinnhold gjør ikke RESULTATET fullt reproduserbart uten
snapshot. Dokumentasjonen lover ikke mer enn evidensen bærer.

## 8. Autoritativt, versjonert modulregister

Python-state kan ikke låses på tvers av prosessbilder. Rettet:
- **Modulregisteret er DB-autoritativt**, versjonert, med `manifest_hash`
  per modul og en monoton `registerversjon`.
- **Aktiveringsrunden binder `registerversjon`**; aktivering revaliderer
  den (PR-013 V4-rekalkulering).
- **Runtime forblir fail-closed** hvis modulen senere blir utilgjengelig.
- **Deaktivering av en modul som aktive policyer refererer:** enten
  blokkeres, eller gjør handlingene ikke-utførbare → **UNNTAK** (aldri
  stille utførelse). Valgt: blokkeres, med eksplisitt overstyring som
  sender handlingene til UNNTAK.

## 9. Bootstrap-sirkelen brytes med registertilstander

Porten krever aktiv modul; modulen kan ikke bli aktiv før ende-til-ende-
test gjennom policyen. Rettet — tre tilstander:
`installert → staging_verifisert → aktiv`
- **Staging-selen kan teste en `installert` modul** gjennom en særskilt
  **ikke-produksjonsførbar testkapabilitet** (egen tenant-klasse, egen
  scope, kan aldri utstedes i produksjon).
- **Produksjonspolicy krever `aktiv`.**
- **Ingen generell bypass i beslutnings-API-et** — testkapabiliteten er
  eneste vei, og den er miljøbundet.

## Svar på v1-spørsmålene (reviewens, vedtatt)
1. Modul-eksistens: **advarsel** ved utkastvalidering, **hard feil** ved
   åpning av aktiveringsrunde OG på nytt ved aktivering. Valideringen
   forblir deterministisk; registerkontrollen rapporteres som separat
   miljøkontroll.
2. **Plattformregel** (§1).
3. **Egen container + nettverkssegment fra første staging-kjøring** (§4).

## Tester (tillegg til v1 §B6)
Policy uten domenekrav kan likevel ikke skanne (plattformregel) ·
eierskap tilbakekalt etter beslutning → claim/utførelse stoppes ·
DNS-TXT-challenge kreves, portalregistrering alene avvises · eid domene
som resolver til privat IP → avvist av egress · DNS-rebinding mellom
verifisering og forbindelse → ingen forbindelse · redirect til annet
hostname uten eierskap → avbrutt · `--no-sandbox` → oppstart nektes ·
container har ingen DB-/nøkkeltilgang (negativ test) · modul kan ikke
skrive DB direkte; kvittering avvist før artefakt er varig lagret ·
rapport-URL uten query/fragment · selektor > 200 tegn sanitert · rapport
uten `container_image_digest` avvist · modul deaktivert med aktiv policy
→ blokkert eller UNNTAK, aldri stille · `installert` modul kan testes med
testkapabilitet, men ikke nås fra produksjonspolicy.
