# Bestilling til ChatGPT (limes inn av Eier)

To oppgaver, i denne rekkefølgen:

## 1. Spesifikasjonsreview av PR-005 (steg 2 — FØR implementering)

Vedlagt: PR-005-SPESIFIKASJON.md. Reviewgrunnlag: docs/README-arbeidsflyt.md
steg 2 i https://github.com/moka1980/disponit (main 679ee9e).

Svar på de tre faste spørsmålene:
(a) Bryter noe med policy-skjemaet (policies/policy-schema-v0.2.json)?
(b) Er alle handlinger reversible eller eksplisitt irreversible med harde vilkår?
(c) Mangler unntakshåndtering for noen feilvei?

…pluss de tre åpne spørsmålene nederst i spesifikasjonen (unntaksrad-grensen,
payload-maskering vs. kryptering, token vs. mTLS).

## 2. Retro-review av PR-004 (post-merge)

PR-004 gikk til implementering uten spesifikasjonsreview — det var brudd
på egen rutine, nå rettet. Siden koden er merget og utgjør tillitsankerets
tilstandslag, bes du om en etterhåndsreview av main-tilstanden:
platform/core/db/ (pg.py, migrations/) og
platform/core/policy_validator/attestering.py, mot de samme tre
spørsmålene + ADR-001 (docs/beslutninger/). Funn merkes P1/P2/P3 og
tas inn i PR-005/PR-006 etter alvorlighet.
