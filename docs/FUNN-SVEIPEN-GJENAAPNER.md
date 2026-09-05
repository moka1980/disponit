# Sveipen gjenåpner det et menneske har lukket

**Funnet 5. september 2026, av CodeRabbit på M-50 (124).**

**RETTET SAMME DAG, i 125.** Første utgave av dette dokumentet listet
ti merget migrasjoner. Den lista var gal, og rettingen står under
«Hva som faktisk gjaldt».

## Hva som er galt

Hver sveip skriver funnene sine med en `INSERT ... ON CONFLICT DO
UPDATE`. Oppdateringsgrenen ser slik ut i alle modulene:

```sql
DO UPDATE SET
    over_grense = EXCLUDED.over_grense,
    sist_sett_sveip = now(), apen = true,
    lukket_ts = NULL, lukket_av = NULL, lukkenotat = NULL
```

`apen = true` er **ubetinget**. Det betyr at et funn et menneske har
lukket blir **gjenåpnet neste natt**, så lenge tilstanden fortsatt er
til stede.

For funntypene sveipen selv eier er det riktig: de lukkes bare av at
tilstanden er borte, og er tilstanden tilbake, er funnet tilbake.

**For de lukkbare funntypene er det galt.** «Jeg har sett den, jeg gjør
den på fredag» er en legitim menneskelig beslutning om noe som ennå
ikke har gått galt — og den blir borte til neste morgen.
Lukkeknappen er pynt.

## Hvorfor det ikke ble oppdaget

Portene målte at `m*_lukk_funn` SVARTE `apen = false`. Ingen av dem
kjørte sveipen etterpå og leste raden på nytt. Porten bekreftet
knappen, ikke virkningen.

Det er samme klasse som safe-area-regelen uten `viewport-fit=cover`
(4/9): en regel som ser ut som et gjerde, en port som bekrefter at
regelen står der, og ingen som måler at den virker.

## Fiksen, slik den er gjort i 124

Skillet står på `lukket_av`:

```sql
apen = (public.journalfunn.apen
        OR public.journalfunn.lukket_av = 'm50_sveip'),
lukket_ts = CASE WHEN public.journalfunn.apen
                   OR public.journalfunn.lukket_av = 'm50_sveip'
                 THEN NULL ELSE public.journalfunn.lukket_ts END,
-- …og likedan for lukket_av og lukkenotat
```

Sveipens egen lukking gjenåpnes; et menneskes står. Blir tilstanden
verre, er det en **annen funntype** og dermed en annen rad — et lukket
«nærmer seg» skjuler ikke et «passert».

**MERK:** `apen = true` alene ville brutt
`*_funn_lukking`-CHECK-en, som krever at `lukket_ts` er `NULL` når
raden er åpen. Alle fire kolonnene må derfor settes sammen.

## Hva som faktisk gjaldt

Lista i første utgave ble skrevet av hukommelse og var **gal på tre
måter**. Tallene under er talt, ikke husket:

* **112, 113 og 114 hørte ikke hjemme der.** De har ingen lukkedør —
  ingen kan lukke et funn, og da bryter en ubetinget gjenåpning ikke
  noe.
* **120 (M-55) manglet.** Den treffer `merkevarevarsel`, ikke
  `merkevarefunn`, og et navnesøk på «funn» gikk forbi den.
* **Det verste sto ikke der i det hele tatt:** 116, 117, 118 og 119 har
  **ingen `lukket_av`-kolonne**. Dørene tar imot en `p_aktor`, skriver
  den i revisjonsloggen og lar raden være anonym. Ingen som leser
  funnlista ser hvem som lukket — og sveipen kunne umulig skilt sin
  egen lukking fra et menneskes, fordi opplysningen ikke fantes.

**Det faktiske omfanget: ni tabeller i ni migrasjoner, 116–124.**

| Migrasjon | Modul | Tabell | Hadde `lukket_av` |
|---|---|---|---|
| 116 | M-48 motpartsregister | `motpartsfunn` | nei |
| 117 | M-49 sanksjonskontroll | `sanksjonsfunn` | nei |
| 118 | M-46 anbudsregister | `anbudsfunn` | nei |
| 119 | M-51 tilskuddsregister | `tilskuddsfunn` | nei |
| 120 | M-55 merkevare | `merkevarevarsel` | ja |
| 121 | M-54 EHF | `ehffunn` | ja |
| 122 | M-52 tollkode | `tollfunn` | ja |
| 123 | M-47 myndighetsrapport | `myndighetsfunn` | ja |
| 124 | M-50 postjournal | `journalfunn` | ja (rettet i 124) |

## Hvordan det ble rettet: 125, i basen og ikke i sytten kall

Den nærliggende fiksen — skrive om `DO UPDATE`-blokken på hvert av de
sytten stedene — ville krevd at ni store sveipefunksjoner ble gjenskapt
ordrett bortsett fra én klausul. Gjenskaping av tusen linjer for å
endre fire er der nye feil kommer fra.

Verre: det ville rettet fortiden og ikke fremtiden. Modul nummer ti
kopierer sveipen fra modul nummer ni, slik 116–124 alle kopierte
hverandre.

**Regelen bor derfor på raden.** `sveipefunn_lukkevern()` er én
trigger foran hver UPDATE på alle ni tabellene:

* går raden fra åpen til lukket uten at noen navnga seg, stemples
  sveipens navn på;
* går den fra lukket til åpen, avgjør `lukket_av` hva som skjer: var
  det sveipens egen lukking, gjenåpnes den og sporet ryddes; var det
  et menneske, **står lukkingen**.

Vakten **retter stille, den feiler ikke**. En exception her ville
drept hele nattens sveip på det første funnet noen hadde lukket.

I tillegg fikk 116–119 kolonnene `lukket_av` og `lukkenotat`. **Seks**
av de ni fikk en ny CHECK som gjør en lukket rad uten navn
urepresenterbar — 120, 121 og 122 hadde den allerede, gjennom
kombinasjonen `apen = (lukket_ts IS NULL)` og
`num_nulls(lukket_ts, lukket_av, lukkenotat) IN (0, 3)`.

## Porten som manglet

Portene målte at `m*_lukk_funn` SVARTE `apen = false`. Ingen av dem
kjørte sveipen etterpå. `platform/core/tests/test_sveipevern.py` gjør
begge deler, og mutasjonen er verifisert: uten triggeren står raden
igjen som **åpen** med `kari` i `lukket_av`.

Ingen av disse rakk å gjøre skade i drift: sveipene er ikke aktive på
staging, og ingen tenant har lukket et funn.
