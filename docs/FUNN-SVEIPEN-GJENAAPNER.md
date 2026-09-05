# Sveipen gjenåpner det et menneske har lukket

**Funnet 5. september 2026, av CodeRabbit på M-50 (124). Gjelder ti
merget migrasjoner i tillegg.**

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

## Hva som gjenstår

Migrasjoner er forward-only, så de ti under må rettes av en NY
migrasjon som erstatter sveipefunksjonene:

| Migrasjon | Modul | Lukkbare funntyper som gjenåpnes |
|---|---|---|
| 112 | M-19 adresseregister | — må gjennomgås |
| 113 | M-39 lønnsgrunnlag | — |
| 114 | M-44 kampanjeregister | — |
| 116 | M-48 motpartsregister | — |
| 117 | M-49 sanksjonskontroll | — |
| 118 | M-46 anbudsregister | — |
| 119 | M-51 tilskuddsregister | — |
| 121 | M-54 EHF | — |
| 122 | M-52 tollkode | `nomenklatur_utloper_snart`, `forslag_under_terskel`, `forslag_ikke_klart`, `vare_uten_forslag`, `ingen_krav` |
| 123 | M-47 myndighetsrapport | `frist_naermer_seg`, `regelverk_utloper_snart`, `ingen_krav` |

**Og porten må skjerpes samme sted:** hver modul trenger en test som
lukker et funn, kjører sveipen, og leser raden på nytt. Uten den er
fiksen like usynlig som feilen var.

Ingen av disse har rukket å gjøre skade i drift: sveipene er ikke
aktive på staging ennå, og ingen tenant har lukket et funn.
