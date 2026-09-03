# Klynge 5 — «resten av det bransjemalene alt har lovet»

Fire moduler bygges parallelt: **M-41** betalings- og abonnementsstatus,
**M-11** adressevalidering, **M-39** lønnsgrunnlag, **M-44**
kampanjeutsending. Denne fila er kontrakten mellom sporene, og den
følger `KLYNGE-FUNDAMENT.md` til `KLYNGE4-FUNDAMENT.md` — formen har
holdt fire ganger.

## Hvorfor akkurat disse fire

De valgte seg ikke bare selv — **de er de fire som er igjen.** Klynge 4
tok de fem mest refererte manglende modulene i bransjemalene vi sender
ut. Dette er hele resten:

| Modul | Referanser | Maler | Navngitt som |
|---|---:|---|---|
| M-41 | 3 | netthandel | verifikator `v_betaling`: `betaling_autorisert`, `samme_betalingsmiddel` |
| M-39 | 2 | handverk-bygg | verifikator `v_lonn`: `timer_mot_arbeidsplan`, `prosjektkode_gyldig`, `overtid_flagget` |
| M-19 | 1 | netthandel | verifikator `v_adresse`: `adresse_validert` — **malen skrev M-11; se under** |
| M-44 | 1 | netthandel | **handlingens modul**, ikke verifikator: `kampanje.send` |

**Når denne klyngen lander er `VENTENDE` tom**, og porten som teller
gapet blir en port som holder det lukket: en mal som navngir en ny modul
uten å bygge den, er rød fra den commiten referansen legges inn.

## Rettingen som kom først: M-11 var feil nummer

Netthandelsmalen skrev `beskrivelse: "M-11 adressevalidering"`. **M-11
er selvtesten** — migrasjon 091, plattformskopet, bygget for lenge
siden, med to sikkerhetsinvarianter i sin egen grense `m11-v1`.

Malen navngav feil modul, og gap-porten arvet feilen: `_byggde()` leser
`platform/modules/`, og selvtesten har ingen manifestkatalog fordi den
ikke har tenantflate. Porten så «M-11 er referert, men mangler
manifest», konkluderte med en ubygget adressemodul — og gjentok det i to
klynger på rad.

Rettet i denne commiten, i tre lag:

* **malen** peker nå på `M-19 adressevalidering` — laveste ledige plass
  i `kunde_og_salg`, fase 2 («operasjoner»): adressekontroll er en
  standardisert arbeidsprosess, ikke en autopilot;
* **`_byggde()`** kjenner nå `BYGD_UTEN_MANIFEST` — M-10 (backup, 090)
  og M-11 (selvtest, 091) — så gapet måles mot det som faktisk er bygget;
* **to nye porter**: en som gjør et modulnummer utenfor `katalog.js`
  rødt — en trykkfeil eller en oppfinnelse, begge deler et løfte ingen
  har bestemt — og `test_kravgrenser_unike`, som krever at en `krav_id`
  registreres én gang.

Den siste er den viktigste. Under arbeidet skrev jeg
`KRAVGRENSER["m11-v1"] = {...}` for adressemodulen og **byttet stille ut
selvtestens sikkerhetsgrense** — og hele suiten var grønn: 3740 porter,
null feil. Ingenting pinnet innholdet i en registrert grense. Det gjør
det nå.

**Det ble ikke fire manglende moduler, men tre pluss én feilskrevet
referanse.**

## Den bærende dommen

Klynge 4 innførte den: vi holder igjen på å **AUTORISERE** en handling,
ikke bare på å utføre den. Klynge 5 arver den — og skjerper den ett hakk,
fordi handlingene bak er verre.

**`refusjon.utfor` er den skarpeste enkeltraden i hele policysettet:**

```yaml
- id: refusjon.utfor
  modul: M-41
  modus: auto
  grenser: {belop_maks: "5000.00", valuta: [NOK]}
  reversering: {type: irreversibel}
  vilkaar:
    - {navn: samme_betalingsmiddel, verifikator: v_betaling}   # finnes ikke
```

Automatisk. Irreversibel. Opp til fem tusen kroner. Gatet på en
verifikator som aldri har eksistert. Motoren feiler lukket, så den har
aldri fyrt — men den står der, i en policy vi sender ut, merket `auto`.

**M-25s egen auto-handling venter også på denne klyngen.** `ordre.bekreft
_og_fakturer` er gatet på `betaling_autorisert` (M-41) *og*
`adresse_validert` (M-11). M-25 landet i klynge 4; handlingen kan
fortsatt ikke fyre, fordi to av tre verifikatorer manglet. Det er første
gang en klynge er forutsetningen for en tidligere klynges handling, og
det er verdt å si høyt: **å bygge registeret lukker ikke gapet — det gir
gapet et målegrunnlag.**

| Modul | Policyen lover | v1 gjør |
|---|---|---|
| M-41 | attesterer `betaling_autorisert`, refunderer inntil 5000 auto | **registrerer** betalings- og abonnementsstatus, med kilden til hver status |
| M-19 | attesterer `adresse_validert` | **registrerer** adressen som oppgitt, og hvordan et menneske kontrollerte den |
| M-39 | attesterer tre lønnsvilkår, `timeliste.samle_og_valider` auto | **samler** timegrunnlaget og **måler** det mot arbeidsplanen |
| M-44 | sender kampanjen automatisk, 2 per uke per mottaker | **registrerer** kampanjen, mottakerne og samtykket — og **sender ingenting** |

## M-44 er en annen figur enn de andre

De tre andre er manglende **verifikatorer** — betrodde parter som skal
attestere et vilkår. M-44 er den manglende **aktøren**: den står som
`modul:` på en `auto`-handling, ikke i `verifikatorer`.

Det gjør tilbakeholdelsen sterkere, ikke svakere. Modulen finnes for å
SENDE, og v1 sender null. Registeret måler frekvenstaket (malen sier 2
per uke per `mottaker_id`), samtykkets tilstand og avmeldingslenken — og
gjør et brudd til et funn. Den dagen noen skal sende, finnes det en målt
historikk å bygge fullmakten på.

## Dommene v1 hviler på

Fire registre, samme form som klynge 3 og 4:

* **HISTORIKKEN OVERSKRIVES ALDRI.** Betalingsstatus, adresse, samtykke
  og timegrunnlag er alle append-only. Den gjeldende verdien ER den
  siste raden. M-42s lærdom, uendret: en tabell som oppdateres på stedet
  sletter beviset i samme øyeblikk som det oppstår.
* **HVER PÅSTAND HAR EN KILDE.** En betalingsstatus uten hendelsen den
  kom fra, en adresse uten hvem som kontrollerte den hvordan, et
  samtykke uten dato og kanal — er påstander, ikke målinger. Alle fire
  grensene bærer en `*_uten_kilde`-invariant.
* **BELØP I ØRE, TIMER I HELE MINUTTER.** `BIGINT`, ingen unntak
  (101s form; M-25s minutt-dom).
* **GRENSENE ER TENANTENS.** Beløpsgrense, valideringskrav, timegrense
  og frekvenstak ligger i basen, satt gjennom en dør.
* **INGEN AV DE FIRE TAR ATTESTASJONSFULLMAKTEN.**

## Tildelte migrasjonsnumre

| Nr | Modul | Fil |
|----|-------|-----|
| 111 | M-41 | `111_m41_betalingsregister.sql` |
| 112 | M-19 | `112_m19_adresseregister.sql` |
| 113 | M-39 | `113_m39_lonnsgrunnlag.sql` |
| 114 | M-44 | `114_m44_kampanjeregister.sql` |

## Roller og sveip

Fire eiere og fire sveipere, av samme grunn som før: en delt sveiperolle
måtte hatt EXECUTE på alle kryss-tenant-defienerne, og en feil i én sveip
ville båret de andres fullmakt.

| Modul | Eier | Sveip | Klokkeslett (UTC) |
|---|---|---|---|
| M-41 | `disponit_betaling_eier` | `disponit_betalingssveip` | 07:35 |
| M-19 | `disponit_adresse_eier` | `disponit_adressesveip` | 07:50 |
| M-39 | `disponit_lonn_eier` | `disponit_lonnssveip` | 08:05 |
| M-44 | `disponit_kampanje_eier` | `disponit_kampanjesveip` | 08:20 |

Med klynge 5 er plattformen oppe i **atten** nattlige sveip (03:15 →
08:20). Driftssaken fra klynge 4 står uendret og er nå tyngre: en felles
planlegger med observerbarhet er verdt en runde. Det er fortsatt ikke en
grunn til å slå sveiperollene sammen.

## Hva fundamentet eier

* fire manifester + `MODULSTATUS`/`MODULER` i **samme commit**
* `KRAVGRENSER` for `m41-v1`, `m19-v1`, `m39-v1`, `m44-v1` — registrert **før** byggingen (§0)
* `locales/{nb,en}.json`: `site.modul.m{19,39,41,44}.*`
* **åtte DB-roller OG åtte migrator-medlemskap** i
  `oppsett-postgresql.sh` og `ci.yml`, i én omgang
* de tildelte migrasjonsnumrene og klokkeslettene over
* `VENTENDE` tømt, og porten som holder gapet lukket

### Medlemskapene hører hjemme HER — DSN-ene gjør det ikke

Dette er lærdommen fra 3/9, og den er ny siden klynge 4:

* **`GRANT <eier> TO $MIGRATOR`** skal i fundamentet, i BEGGE filer
  (`oppsett-postgresql.sh` og `ci.yml`). `SET ROLE` krever medlemskap,
  ikke eksistens — klynge 4 opprettet rollene og glemte medlemskapene,
  utrullingen stoppet på `permission denied to set role`, basen sto
  mellom to releaser og enhetene ble stående stoppet (#361).
  `test_deploy_rollemedlemskap` tåler et supersett: medlemskap uten
  migrasjon er grønt, migrasjon uten medlemskap er rødt.
* **Sveipenes DSN-er skal IKKE i fundamentet.** De hører i modul-PR-en,
  sammen med `opp.sh`s preflight-sjekk. `test_deploy_sveip_dsn` måler
  begge veier: en DSN oppsettet skriver som ingen sveip krever, er like
  rød som motsatt (#360).

## Hva hvert byggespor eier

Egne filer, og **egne linjer** i de delte:

* legg oppføringen i delte lister **uten** å endre naboens linje
* aldri gjør en assert avhengig av å stå SIST i en delt liste
* kjør `sjekk-fletteskade.py` etter rebase, før commit
* **den nye sveipen skal stå i `test_sveipekontrakten.py`**
* **sveipens DSN + `opp.sh`-preflight i samme PR** — ellers er
  DSN-porten rød
* rad-kontrakten i `kjor()` valideres på `rader[0][:5]` (109-formen),
  ikke på «nøyaktig fem felt»: den delte kontraktporten mater et
  supersett
