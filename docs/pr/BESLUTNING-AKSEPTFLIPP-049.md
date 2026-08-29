# BESLUTNINGER — akseptflipp-debriefen (049) + M-16-briefbestilling

**Draft: Claude.ai · Full sti: `docs/pr/BESLUTNING-AKSEPTFLIPP-049.md`.
Grunnlag: debrief 2026-08-21, prod `5f2e233`, migrasjoner 1→49.**

Først: drillen med grønt resultat på alle tre kontrollpunktene — inflight
ferdig med signert kvittering 1,5 s etter rullingen, null claims fra
drenert release, kandidat overtok på 12,4 s — er nøyaktig det beviset
rollback-drill-porten var til for. Og at akseptraden står på 0 med vilje
er systemet som virker, ikke som feiler.

---

## 1. Proxytoken-vilkåret: **valg (a) — re-scope, som synlig grenserevisjon**

Punktet `egress.proxytoken_til_ikke_ekstern_lesing` krever negativ
måling av en mekanisme som ikke finnes: ingen komponent utsteder
egress-tokens. Det bryter evidensgrensens egen grunnregel — **ingen
deploy-port skal kreve en tilstand bare senere arbeid kan skape** — og
det er min feil i to ledd: jeg skrev punktet som ønske uten mekanisme
(den gamle lærdommen fra 015-runden), og mappingen til port24-tallet var
pynting av det (Codex P1, riktig felt).

Valg (b) — vente på proxyen — avvises fordi det ikke gir sikkerhet, bare
en permanent rød port for en modul der invariantene faktisk er målt. En
port som er rød uansett hva noen gjør, lærer organisasjonen å overstyre
porter. Det er den dyreste vanen som finnes.

**Re-scopingen, konkret:**

- Punktet erstattes av de to målbare invariantene som faktisk bærer
  egress-sikkerheten i dag:
  `egress.sideeffektklasse_gater_aktivering = 0 brudd` (036-porten) og
  `egress.hemmeligheter_i_browsermiljo = 0` (port24-tallet, nå under
  riktig navn — det måler containermiljøet, ikke tokens).
- Revisjonen er **synlig**: grensen reversjoneres til
  `m56-akseptflipp-v2` med endringsnotat i grensefila («punkt fjernet:
  mekanismen finnes ikke; erstattet av …»). Aldri stille ombytting av
  punkter i en navngitt grense.
- **Når en utstedende egress-proxy eventuelt bygges**, får *den* arcen
  punktet tilbake i sin egen grense, med negativ måling mot den faktiske
  utstederen. Notat om dette legges i grensefila så intensjonen ikke
  forsvinner med punktet.

**UMAALTE-mekanismen ratifiseres som stående:** et umålt punkt blokkerer,
aldri pyntes. Det er den mekaniske formen av regelen «punkt uten
definert, målbar grense regnes som nei», og sløyfa implementerte den
riktigere enn jeg spesifiserte den.

Aksepthendelsen kan dermed skrives når v2-grensen er inne — fortsatt av
akseptfunksjonen, fortsatt med komplett punktsett.

## 2. #117-funnene: ratifisert

- **Attestant ≠ akseptør** — fire øyne anvendt på akseptens
  forutsetning. At migrator var medlem av begge rollene er nøyaktig
  klassen SP-1-tankegangen finnes for: medlemskap er en vei, og veier
  telles. `disponit_ci_verifikator` + baseregelen om ulik `session_user`
  ratifiseres begge.
- **Akseptporten måler artefaktene, ikke kallerens `bestatt`** — samme
  skille som hele E1-serien: kalleren påstår ikke, målingen beviser.
- Manifestflippet som venter på m02-aksept er riktig rekkefølge, ikke
  et avvik: `registry.valider` gjør jobben sin. m56-flippet er
  to-linjers i m02-arcen som planlagt.

## 3. K1–K5: **ratifisert inn i RUTINER.md og sløyfeinstruksen**

Eiers innsigelse er berettiget, og rotårsaken er målbar og strukturell:
Codex reviewer hele diffen, så fikser som **vokser** flaten er
selvforsterkende — porten gikk 292 → 4281 linjer mens produktet på 116
linjer sto ferdig. Reglene, som ratifisert:

- **K1** En fiksrunde bygger aldri — fikser krymper eller holder flaten.
- **K2** Tre runder på samme mekanisme = stopp og rotårsaksanalyse, ikke
  fjerde forsøk.
- **K3** Produktet holdes aldri som gissel: står produktdelen ferdig og
  uimotsagt, deles PR-en.
- **K4** Aldri hand-parse en fremmed grammatikk — løftes til **SP-13**
  (under).
- **K5** Overvåkeren griper inn ved runde ~8 med eskalering til eier.

## 4. SP-13: Aldri hand-parse en fremmed grammatikk

Inn i `docs/ARKITEKTUR-STAENDE-PORTER.md` som trettende port:

> **SP-13 Fremmed grammatikk parses av dens egen parser.** SQL, YAML,
> HTML og andre språk med egen grammatikk skal leses med en ekte parser
> (pglast for SQL), aldri med regex eller håndskrevet tilstandsmaskin.
> Og **semantikk verifiseres som oppslag i den virkelige tilstanden**
> (den migrerte basen), aldri med en simulator — en simulator måler sin
> egen fullstendighet, ikke virkeligheten. *Port:* verifikatorer som
> leser fremmed syntaks importerer en parser; semantiske påstander
> testes mot faktisk kjørt tilstand.
> *Hvorfor den er stående:* #118 brukte 20+ runder der de seks siste
> funnene gjaldt migrasjoner som ikke fantes — simulatorens hull, ikke
> basens. Norskportens V8-D2 var samme klasse: en kontroll som måler
> det den vet om, melder grønt om alt den ikke vet om.

**#119-treleddsfasiten har GO** som konsekvens: delt `les_katalog.mjs`
mellom port og generator (én sannhet om formatet), pglast for syntaks,
semantikk som oppslag i migrert base.

## 5. M-16-briefbestillingen — fem lesespørsmål, ren lesing

M-16 (KPI-dashboards) er neste arbeidsmodul per V9-runden. Før jeg
drafter trenger jeg datainventaret, lest — ikke husket:

1. **`policyaktivering` som kilde:** kolonneliste og radantall i prod nå;
   hvilke av feltene er egnet som nøkkeltall (aktiveringer over tid,
   kvorumsklasser, kilder)?
2. **Beslutningsdata:** hvilke tabeller bærer TILLAT/STOPP/BRUDD-utfall
   med tidsstempel og handling (revisjonslogg? oppdrag? frekvens_
   hendelser?), og hvilke leseveier/definere finnes allerede mot dem?
3. **Tick-historikken:** `bestillingsplan_tick`-form i dag, og finnes
   aggregeringsvennlige indekser (per plan, per utfall, per periode)?
4. **M-37-køen:** hvilke tall kan leses om saker (åpne/lukkede per
   årsak, behandlingstid) uten nye kolonner?
5. **Flate-presedens:** finnes det en eksisterende dashboards-/
   graf-komponent i UI-et (planhistorikken? katalogen?) med
   WCAG-mønstre å gjenbruke, eller er dette første graf-flate?

Forhåndsbestemt ramme for arcen (så briefen vet målet): M-16 v1 er
**ren lesing** — ingen nye skriveveier, definere med tenant-binding
(SP-1/SP-7), tall som databasen kan bevise, og «nøkkeltall regnet fra
faktiske beslutninger» som eneste løfte.

---

```
NÅ:    Beslutningene relayes (§1 a-valget med v2-grensen, §2–§4
       ratifisert); Claude Code: v2-grensen + aksepthendelsen, K1–K5 inn
       i RUTINER.md/sløyfeinstruks, SP-13 inn i porterdokumentet,
       #119-PR-en etter fasiten; M-16-lesejobben (§5)
       — ChatGPT (Eier relayer) / Claude Code
       — docs/pr/BESLUTNING-AKSEPTFLIPP-049.md, docs/RUTINER.md,
         docs/ARKITEKTUR-STAENDE-PORTER.md
NESTE: M-16-spesifikasjon fra lesesvaret; m02-aksept-arcen (flipper
       m56-manifestet) — Claude.ai / Claude Code — docs/pr/
```
