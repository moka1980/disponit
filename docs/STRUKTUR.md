# STRUKTUR — mappekart og regler

Alle filer i prosjektet hører hjemme her. Ny fil uten klar plass = strukturspørsmål til Claude.ai først.

```
disponit/                           ← repo-rot (main-branch), github.com/moka1980/disponit
├── README.md                       ← inngang: hva, hvordan kjøre, pekere
├── docs/
│   ├── RUTINER.md                  ← roller, arbeidsflyt, avslutningsblokk
│   ├── STRUKTUR.md                 ← denne filen
│   ├── DEPLOY.md                   ← servere, miljøer, skaleringsvei
│   ├── README-arbeidsflyt.md       ← AI-pipeline og utrullingsløype
│   ├── PUSH-INSTRUKS.md            ← repo-oppsett og tilgang for AI-rollene
│   ├── pr/                         ← én fil per PR: PR-NNN.md = PR-beskrivelsen
│   ├── beslutninger/               ← ADR-er: hvorfor, ikke bare hva. ADR-NNN-*.md
│   └── spesifikasjon/
│       └── disponit-prototype-v7.html      ← sannhetskilden (v7.2)
├── platform/
│   ├── core/                       ← plattformkjernen. Importerer ALDRI fra modules/
│   │   ├── policy_validator/       ← M-1-kjernen: engine.py, schema.py, audit.py,
│   │   │                              attestering.py (HMAC på attestasjoner)
│   │   ├── db/                     ← tilstandslaget (ADR-001): pg.py + migrations/
│   │   ├── registry.py             ← modulregister: oppdag, aktiver, deaktiver
│   │   ├── tests/                  ← core-tester (kjør: pytest platform/core/tests)
│   │   └── examples/               ← run_synthetic.py m.m.
│   └── modules/                    ← én mappe per modul, selvforsynt
│       └── m01_policy/
│           ├── manifest.yaml       ← id, versjon, status, avhengigheter, sjekkliste
│           └── README.md           ← modulens egen dokumentasjon
├── policies/                       ← DATA, ikke kode: skjema + bransjemaler
│   ├── policy-schema-v0.2.json     ← gjeldende kontrakt (JSON Schema 2020-12)
│   └── bransjemal-*.yaml
├── locales/                        ← ETT språk = ÉN fil. nb.json, en.json, …
├── design/
│   └── tokens.css                  ← ALT utseende defineres her, kun her
├── prototype/                      ← historisk arkiv: v5, v6, v7 (endres aldri)
└── deploy/
    └── staging/                    ← oppsett-postgresql.sh (idempotent serveroppsett)
```

## Reglene

1. **Core kjenner ikke modulene.** `platform/core/` importerer aldri noe fra `platform/modules/`. Registeret leser bare manifester.
2. **Moduler snakker kun med core.** Aldri modul-til-modul-import. Avhengigheter deklareres i manifestet og håndheves av registeret.
3. **En modul er flyttbar.** Hele modulen bor i sin mappe: kode, manifest, egne oversettelsesnøkler (prefiks `m01.`), egen README. Slett mappen → modulen er borte, resten kjører.
4. **Data er ikke kode.** `policies/` og `locales/` er data som valideres og versjoneres — de deployes gjennom samme løype som kode, men bor aldri inne i kodemapper.
5. **Én kilde per bekymring.** Utseende: `design/tokens.css`. Tekst: `locales/`. Regler: `policies/`. Sannhet om hva som bygges: `docs/spesifikasjon/`.
6. **Tester bor ved det de tester.** Core-tester i `platform/core/tests/`, modultester i modulens mappe under `tests/`.
