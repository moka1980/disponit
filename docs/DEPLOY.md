# DEPLOY — miljøer, servere og skaleringsvei

## Server: one.com Cloud Server (VPS) — Eiers eksisterende oppsett

Vi har allerede en **one.com Cloud Server S** med Ubuntu: 2 vCPU, 4 GB RAM,
100 GB NVMe, full root-tilgang, Docker-støtte, ubegrenset trafikk og
databehandleravtale tilgjengelig (GDPR). Månedlig avtale — oppgradering
til større plan tar minutter og krever ingen omskriving.

**Beslutning (Claude.ai):** Cloud Server S er staging-serveren vår nå.
Skillet mellom miljøer beholdes uansett leverandør: staging og produksjon
deler aldri database, nøkler eller nettverk.

### Hva Cloud Server S (4 GB) dekker — og ikke

| Kjører fint på S | Krever oppgradering |
|---|---|
| FastAPI-backend + jobbkø | Ollama med Mistral 7B / Llama 3.1 → **Cloud Server L (16 GB)** |
| PostgreSQL + objektlagring lokalt | Flere samtidige LLM-forespørsler → XL/XXL |
| Policymotor, register, revisjonslogg (hele fase 1-kjernen er deterministisk) | GPU-inferens (fase 3-skala) → ekstern GPU-leverandør; M-38 holder inferensplanet adskilt så byttet er enkelt |
| Små kvantiserte modeller (~3B) for røyk-testing | |

Oppgraderingsutløser: **første modul som trenger 7B-modell i staging-test
→ bestill Cloud Server L.** Ikke før.

## Miljøer

| Miljø | Hvor | Formål | Data |
|---|---|---|---|
| **Lokalt** | Utviklers maskin / Claude Code | Kode + enhetstester | Syntetisk |
| **Staging** | one.com Cloud Server S (Ubuntu) — oppgraderes S → L → XL etter behov | «Ekte test på server»: hver modul må bestå sjekklisten sin her 100 % før neste modul startes | Syntetisk + sandkasser (Stripe test-mode, bank-sandbox). ALDRI kundedata |
| **Produksjon** | Egen VPS (settes opp når fase 1 nærmer seg pilot — kan også være one.com, men alltid separat maskin) | Kunder + kunde null | Ekte, kryptert, tenant-isolert |

Produksjon oppdateres kun via utrullingsløypen i v7.2 (CI → staging →
evaluering → kanari → gradvis → automatisk rollback). Ingen SSH-endringer
rett i produksjon — aldri.

## Skaleringsvei (bygget inn, aktivert etter behov)

Prinsippet som gjør skalering til et maskinvalg, ikke en omskriving:
**ingen tilstand i API-prosessen** — all tilstand bor i PostgreSQL /
objektlagring / kø. Håndheves fra PR-004 (API-skjelettet).

| Nivå | Samtidige brukere | Hva som må til |
|---|---|---|
| 1 | 0–1 000 | Cloud Server S/L: API + DB + kø på én maskin. CDN foran statiske filer. |
| 2 | 1 000–10 000 | Oppgrader til L/XL, eller skill API og PostgreSQL på to VPS-er. Jobbkø-workers. Backup til objektlagring. |
| 3 | 10 000–100 000 | Load balancer + flere stateless API-noder, PostgreSQL-replika, dedikerte GPU-inferensnoder eksternt (M-38 styrer kø/ruting), regional datalagring. |
| 4 | 100 000–1 000 000 | Multi-region, sharding per tenant-gruppe, autoskalering av workers, katastrofegjenoppretting (M-35). |

## Modulens staging-sjekkliste (mal — kopieres inn i hvert manifest)

- [ ] Alle enhetstester og negative policytester grønne på staging
- [ ] Kjørt mot syntetisk datasett med målt resultat likt lokalt
- [ ] Sandkasse-integrasjoner svarer (der modulen har noen)
- [ ] Revisjonslogg (M-2-format) skrives korrekt og er lesbar
- [ ] Feilinjisering: minst én fremprovosert feil havner riktig i unntakskø
- [ ] Ytelse: definert last kjørt uten feil (grense settes per modul)
- [ ] Rollback testet: modulen kan deaktiveres via registeret uten at annet påvirkes
