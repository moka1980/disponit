# Klynge 2 — «tilgang, lisens og etterlevelse»

Fire moduler bygges parallelt: **M-12** identitet og tilgang, **M-22**
SaaS og lisens, **M-30** personvern, **M-34** compliance. Denne fila er
kontrakten mellom sporene, og den følger `KLYNGE-FUNDAMENT.md` fra
klynge 1 — formen holdt, og en ny form ville bare vært en ny å lære.

## Den bærende dommen

Alle fire er **registre og målere**. For hver av dem er den farlige
handlingen nettopp den katalogen lover:

| Modul | Katalogen lover | v1 gjør |
|---|---|---|
| M-12 | provisjonerer tilgang (JML) | **registrerer** den |
| M-22 | sier opp ubrukte lisenser | **varsler** om utløp |
| M-30 | sletter persondata på forespørsel | **registrerer** forespørselen |
| M-34 | sender inn sertifiseringsevidens | **registrerer** kontrollen |

Det er ikke forsiktighet for forsiktighetens skyld. Å se sannheten er
forutsetningen for å tørre å endre den — og rekkefølgen gir et
revisjonsspor å måle mot den dagen utførelsen kommer.

For M-30 er dommen dessuten arkitektonisk: **sletting eies av M-4**.
En andre slettevei ved siden av retensjonsregnskapet er nøyaktig det
M-4 ble bygget for å hindre. To veier som sletter det samme kan aldri
holdes i takt.

## Tildelte migrasjonsnumre

| Nr | Modul | Fil |
|----|-------|-----|
| 097 | M-12 | `097_m12_tilgangsregister.sql` |
| 098 | M-22 | `098_m22_lisensregister.sql` |
| 099 | M-30 | `099_m30_personvernregister.sql` |
| 100 | M-34 | `100_m34_kontrollregister.sql` |

Kjøreren krever IKKE ubrutt sekvens — det trodde jeg da klynge 1
startet, og to byggespor fant påstanden feil hver for seg (M-5 i klynge
1, M-34 i klynge 2). `db/kjorer.py` itererer `sorted(glob("[0-9][0-9][0-9]_*.sql"))`
og hopper over det som alt står i `migrasjoner`-registeret. Et hull i
nummerrekka kjører grønt.

Numrene tildeles likevel på forhånd, og merge-rekkefølgen holdes — men
grunnen er en annen enn jeg skrev: en fasit som vokser i rekkefølge er
lettere å lese i git-historikken, og to spor som velger samme nummer
kolliderer i `migrasjons-fasit.json`. Det er kollisjonen som koster,
ikke hullet. Et spor som bygger før et lavere nummer er merget kan
altså bruke sitt tildelte nummer med én gang — ingen midlertidig
renummerering trengs.

## Grensene mot hverandre, sagt eksplisitt

De tre fristmodulene deler form og deler **bevisst ikke tabeller**:

* **M-21** eier PLIKTER — frister mot omverdenen
* **M-30** eier FORESPØRSLER — fra registrerte
* **M-34** eier KONTROLLER — våre egne, gjentakende etterprøvinger

Fristes de sammen i én tabell, blir «hva er dette» et felt i stedet for
en type, og de tre modulenes ulike dommer om hva som lukker en frist
kolliderer i samme rad.

## Hva fundamentet eier

* fire manifester + `MODULSTATUS`/`MODULER` i **samme commit**
* `KRAVGRENSER` for `m12-v1`…`m34-v1` — registrert **før** byggingen (§0)
* `locales/{nb,en}.json`: `site.modul.m{12,22,30,34}.*`
* sju DB-roller i `oppsett-postgresql.sh` og `ci.yml`, i én omgang —
  fire eiere og tre sveipere
* `sp10-provekjoring.py` og `test_eierskap.py` sine rollelister
* denne fila

**M-22 har med vilje ingen egen sveiperolle.** Utløpssveipen er et
forpass i varselsenderen (M-21-formen), så den kjører som
`disponit_varselsender`. En ny varslingsvei er en ny vei å miste et
varsel i.

## Hva hver modul-PR eier

Sin migrasjon, sin modulkatalog, sine tester, sin flate, `Route` +
`RUTESCOPE` i **samme commit**, `sitekart.js`/`app.js`, `migrer.py`-grants
og `opp.sh`-porten for sin enhet.

## Etter merge

Eieren kjører **én gang** på verten:

```
sudo bash /opt/disponit/aktiv/deploy/staging/oppsett-postgresql.sh
```

Idempotent på to nivåer; eksisterende passord roteres ikke.
