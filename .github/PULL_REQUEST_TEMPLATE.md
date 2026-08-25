## PR-nummer og modul
<!-- Én modul per PR (RUTINER pkt. 2). F.eks: PR-002 / m01_policy -->

## Hva gjør denne PR-en

## ChatGPT spesifikasjonsreview (steg 2) — lim inn svarene
1. Bryter noe med policy-skjemaet?
2. Er alle handlinger reversible eller eksplisitt irreversible med harde vilkår?
3. Mangler unntakshåndtering for noen feilvei?

## Cursor pre-Codex (steg 4) — før Codex
- [ ] `@cursor review` kjørt (eller PR markert ready_for_review / label `pre-codex`)
- [ ] Cursor-PASS, eller alle P1/P2 fra Cursor lukket + verifiseringspass
- [ ] Ingen `@codex review` før Cursor-PASS (to unntak: transport-uta i RUTINER §10 — Cursor-transporten feiler på nytt forsøk, noteres i tråden — som gjelder i dag, og §11.3, som ikke er i kraft ennå)

## Codex merge-porter (steg 5)
- [ ] CI grønn — inkludert negative policytester (ingen fjernet/svekket)
- [ ] Ingen kodevei uten ved_brudd-håndtering
- [ ] Akseptansemapping mot gjeldende prototype (`docs/spesifikasjon/`) komplett i beskrivelsen
- [ ] Ingen secrets; ingen skrivetilgang utenom policymotoren

## Filplassering
<!-- Full sti fra repo-rot for alle nye/endrede filer (RUTINER pkt. 4) -->
