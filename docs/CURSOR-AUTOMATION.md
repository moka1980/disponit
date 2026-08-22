# Cursor som pre-Codex-angriper — wiring og pilotregler

**Rollefordelingen er absolutt:** Cursor angriper først (kommentarer,
aldri push/approve/merge) · Claude Code fikser og merger · Codex er
eneste merge-autoritet. Pilot måles på #140: Codex-runder mot
basislinjen 5–9.

## Automation-oppsett (cursor.com/automations, repo moka1980/disponit)

- **Trigger:** PR-kommentar som inneholder `@cursor review` (eksplisitt
  — best for piloten).
- **Verktøy:** *Comment on Pull Request* — ingenting annet. Ikke merge,
  ikke approve, ikke push.
- **Prompt (lim inn under):**

```
Du er pre-Codex-angriperen i disponit-pipelinen. Codex er eneste
merge-autoritet; du skal BARE kommentere.

Les PR-diffen mot main OG det frosne klarsignalet i docs/pr/ som PR-en
navngir (for M-57: PR-M57-IMPLEMENTERINGSKLARSIGNAL.md, portene 1–33).
Angrip motstanderaktig:

1. INVARIANTER: finn tilstander diffen tillater som klarsignalets
   porter/evidensgrense forbyr — helst med en konkret feilende test
   (SQL/pytest) per funn.
2. NESTE LAGS FEIL: kanttilfeller i triggere/CHECK/FK-kjeder, RLS-hull,
   replay-materialitet (aktøren er materiell — se 055), rekkefølgen
   BEFORE-trigger vs constraint, rollback-som-mister-grunnlag.
3. IKKE stilkommentarer, IKKE omdøping, IKKE arkitekturomkamper mot et
   frosset klarsignal.

Lever ALT som ÉN kommentar: funnene sortert P1/P2/P3 med fil:linje og
en feilende test der det lar seg gjøre. Ingen funn: skriv nøyaktig
«Cursor-pass: ingen P1/P2». Avslutt alltid kommentaren med `@claude`.
```

## Pilotregler (harde)

1. Claude Code skriver ALDRI `@codex review` før Cursor-passet er
   lukket (fikset batch, eller «Cursor-pass: ingen P1/P2») — ellers tre
   bots i parallell og FLERE runder.
2. Cursor får ETT batched pass per påkalling — ikke funn-for-funn-dialog.
3. Uenighet Cursor↔klarsignal avgjøres av klarsignalet; uenighet om
   arkitektur eskaleres til eier (K2), aldri til Cursor.
4. Måling per PR: antall Codex-runder + antall Codex-funn Cursor ikke
   tok. Cursor kuttes hvis Codex fortsatt finner 6+ etter passet.

## Rekkefølgen i sløyfa (etter wiring)

```
Claude Code: CP klar → @cursor review
Cursor: én batched funnliste (P1/P2/P3) → @claude
claude.yml våkner → fikser HELE batchen → push → @codex review
Codex: verdikt → claude.yml som før → merge når rent (Codex-autoritet)
```
