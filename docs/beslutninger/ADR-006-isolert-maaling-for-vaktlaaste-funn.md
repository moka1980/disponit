# ADR-006: Isolert måling for vaktlåste funn

**Status:** vedtatt 2026-11-19 (Claude Code, med eiers fullmakt «du kan
bestemme arkitektavklaringen»).
**Gjelder:** M-33 (prognose), og enhver sveipmodul av samme form.
**Bygger på:** ADR-002 (klasse A) og presedensene fra M-12, M-40 og M-48.

## Problemet

Den generiske sveiplesten måler i driftsbasen. Det fungerer for de fleste
funn, men noen funn er bundet til klokka. Til nå har vi hatt tre svar:

1. **Døra tar en dato.** Da rigges funnet gjennom døra.
2. **Suiten elder raden med en vanlig `UPDATE` som migrator.** Da gjør
   riggen det samme (M-12, M-40, M-28, M-29, M-32). Ingen vakt slås av.
3. **Raden er frosset av en vakt, og fristen har et gulv på minst ett
   døgn.** Da står funntypen i `UNAABARE` (M-48, M-43, M-45, M-36).

M-33 passer ikke i noen av dem. En prognose er append-only, også for
migrator. Hvert funn som krever at horisonten er passert, er derfor
vaktlåst. Det gjelder to av modulens tre funntyper, og den tredje har ingen
lukkevei i sveipen. Med svar 3 ville ingenting av sveipens lukkevei blitt
målt, og lesten kunne ikke bestås ærlig.

## Dommen

**I0. En vakt slås aldri av i driftsbasen.** Det gjelder uendret.

**I1. En modul der de bærende funnene er vaktlåste, måles i den isolerte
basen** (`disponit_test` på 55432, bygget fra commiten som måles). Den
måles med de samme fire generiske produsentene, og riggen elder radene
**nøyaktig slik modulens egen testsuite gjør det**. Produsentene er ikke
bundet til en base. De leser DSN-ene fra miljøet.

**I2. Riggen håndhever selv hvor den kjører.** Artefaktet bærer ikke
basenavnet, og produsentene kan ikke endres uten å måle alle moduler på
nytt. Derfor står sperren i riggen: `m33_fasit._krev_isolert` kaster hvis
basen er noe annet enn `disponit_test@55432`. Riggen står i fasitens
bevisrot. Et bundet artefakt kan derfor bare være laget av en rigg som
nekter driftsbasen.

**I3. Modulen merkes `"isolert": True` i `SVEIPMODULER`,** og manifestet
sier i klartekst hvor målingen er gjort.

**I4. Dette er siste utvei, ikke en snarvei.** Svar 1 og 2 skal brukes der
de finnes. En modul får bare isolert måling når suiten selv må slå av en
vakt for å nå funnet.

## Konsekvens

Sveipens oppførsel er målt mot kjent fasit, med lukkevei. Ytelsen er målt i
testbasen og ikke mot driftsdata. For M-33 er forskjellen liten, fordi
driftsbasen ikke har prognoser ennå. Manifestet sier det.
