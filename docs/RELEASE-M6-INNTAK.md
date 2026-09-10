# M-6 inntak: postboksen inn i disponit (modul 6 i ARC B-rekka)

Skrevet 10/9-2026, etter eiers spørsmål samme dag: «bør ikke alle mailene
som er i outlook også vises i disponit.com nå, når den er koblet riktig».
Svaret var nei, og grunnen var at koblingen fantes (PR-B, 088) mens
innhenteren aldri var bygget. Dette er den, i den formen 088 tegnet som
PR-C: **kun lesende, ingen modellvei, ingen sendevei** (dommene 31/8).

## 0. Hva kjeden er (088 + 175 + 176)

| Ledd | Hva | PR |
|---|---|---|
| Kilden | `epost_kilde`: én tilkoblet postboks per rad, refresh-tokenet tenant-DEK-kryptert, `delta_token` som Graph-cursor. Samtykkerunden er PR-B | 088 |
| Innhenteren | planarbeiderens runde (`plan.epost`, hvert 5. minutt): kandidatene fra kryss-tenant-døra `m6_hentekandidater` (175), refresh → kortlivet access over ssrf-transporten, Graph-delta over innboksen, kroppen hentet som TEKST, alt persondata i ÉN kryptert payload, avsender og emne som hasher, `ON CONFLICT DO NOTHING` på leverandørens melding-id. 401/403 → kilden `feilet`; 5xx rører ingenting; kill-switch `DISPONIT_EPOST_INNTAK=av` | #469 |
| Flaten | `GET /v1/epost/meldinger` og `…/{id}` (`epost:read`): avsender, emne, forhåndsvisning og kropp dekryptert for økten under RLS. `#/epost` viser meldingene per aktiv kilde. Ingen svar-, videresend- eller sendeknapp | #470 |
| Slettingen | `POST /v1/epost/meldinger/{id}/slett` (`epost:kilde:administrer`): retensjonen gjort NÅ. Døra `m6_slett_melding` (176) tømmer alle fire lagrene i samme transaksjon, som reaperen, og skriver evidens `epost.melding_slettet` uten persondata. Tidspunktet og hashene består | PR 3 |
| Retensjonen | `reap_epostdata` (088) tømmer det samme automatisk når fristen er ute: kundevalgt 30–365 døgn, standard 90, immutabel etter innhenting | 088 |

## 1. Utrulling

Merge → CI → deploy kjører `opp.sh` (migrasjoner t.o.m. 176). **Ingen
vertssteg utover deployen**: `opp.sh` materialiserer M365-verdiene også
for planarbeideren (`skriv_cred plan DISPONIT_M365_*`), og uniten laster
dem som credentials. Ingen ny hemmelighet, ingen ny rolle, ingen ny unit:
innhenteren er planarbeideren som alt kjører.

Bryteren står i `/etc/disponit/plan/konfig`:

```sh
echo "DISPONIT_EPOST_INNTAK=av" >> /etc/disponit/plan/konfig   # stopper inntaket
```

## 2. Tenanten

Ingen policyendring: inntaket bestiller ingenting og fatter ingen
beslutning. Det er lesing av en postboks eier selv har koblet til, og
lagring under tenantens egen retensjonsfrist. Kilden kobles i `#/epost`
(«Koble til M365»), og meldingene vises på samme flate innen fem
minutter.

## 3. Bevisrunden (`m6-inntak-v1`, ti punkter)

| # | Punkt | Måles ved |
|---|---|---|
| 1 | `innhenting_duplikatmelding` | to runder over samme delta-side → én rad |
| 2 | `persondata_i_klartekst_i_basen` | rå SELECT gir ciphertext og hasher, aldri adresse/emne/tekst |
| 3 | `kilde_credentials_ukryptert` | refresh-tokenet er ciphertext (088 port 2) |
| 4 | `logg_med_persondata` | rundeloggen bærer tellinger og kilde-id, aldri adresse, emne, tekst eller token |
| 5 | `kill_switch_konsumerte_kilder` | `DISPONIT_EPOST_INNTAK=av` → ingenting; på igjen → kilden står |
| 6 | `deaktivert_kilde_hentet` | deaktivert og feilet kilde er aldri kandidat |
| 7 | `autfeil_uten_feilet_kilde` | token-/401-feil → kilden `feilet`, ingen rader |
| 8 | `forbigaende_feil_konsumerte_kilden` | 5xx → ingen rader, ingen hentemerker, kilden `aktiv` |
| 9 | `slettet_melding_med_tekst` | menneskets sletting og reaperen etterlater begge null tekst, men sporet består |
| 10 | `modul_sendevei_finnes` | ingen sendevei i innhenteren, ingen skrivevei i flaten utover slettingen |

Artefaktet skrives som `deploy/staging/artefakter/m6-inntak-v1-<ts>.json`
etter skjemaet `artefakt-m6-inntak-skjema.json` (generert fra
`M6_INNTAK_INVARIANTER`).

## 4. Grensene som står igjen, sagt høyt

* **Bare innboksen.** Delta-spørringen går mot `inbox`; sendte og
  arkiverte mapper hentes ikke. Utvidelse er en kontraktsendring.
* **Ingen vedleggsinnhold.** `har_vedlegg` og filnavnet lagres,
  `skannstatus` er `ikke_hentet` — å laste ned vedlegg krever en
  skannevei som ikke finnes.
* **Ingen klassifisering.** `epost_klassifisering` og `epost_utkast` står
  tomme: modellveien er ikke bygget, og manifestets sjekklistepunkter
  står fortsatt `nei`.
* **Ingen sending.** v1 er lesende. Dommen 31/8 krever minst fire ukers
  foreslå-drift med `feil_mottaker=0` før v2 spesifiseres.
