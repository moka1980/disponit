## PR-nummer og modul
<!-- Én modul per PR (RUTINER pkt. 2). F.eks: PR-002 / m01_policy -->

## Hva gjør denne PR-en

## ChatGPT spesifikasjonsreview (steg 2) — lim inn svarene
1. Bryter noe med policy-skjemaet?
2. Er alle handlinger reversible eller eksplisitt irreversible med harde vilkår?
3. Mangler unntakshåndtering for noen feilvei?

## Codex merge-porter (steg 4)
- [ ] CI grønn — inkludert negative policytester (ingen fjernet/svekket)
- [ ] Ingen kodevei uten ved_brudd-håndtering
- [ ] Akseptansemapping mot gjeldende prototype (`docs/spesifikasjon/`) komplett i beskrivelsen
- [ ] Ingen secrets; ingen skrivetilgang utenom policymotoren

## Filplassering
<!-- Full sti fra repo-rot for alle nye/endrede filer (RUTINER pkt. 4) -->
