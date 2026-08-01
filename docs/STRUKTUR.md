# STRUKTUR — mappekart og regler

Alle filer i prosjektet hører hjemme her. Ny fil uten klar plass = strukturspørsmål til Claude.ai først.

```
bedriftsagent/                      ← repo-rot (main-branch)
├── README.md                       ← inngang: hva, hvordan kjøre, pekere
├── docs/
│   ├── RUTINER.md                  ← roller, arbeidsflyt, avslutningsblokk
│   ├── STRUKTUR.md                 ← denne filen
│   ├── DEPLOY.md                   ← servere, miljøer, skaleringsvei
│   ├── README-arbeidsflyt.md       ← AI-pipeline og utrullingsløype
│   └── spesifikasjon/
│       └── AI-bedriftsagent-prototype-v7.html   ← sannhetskilden (v7.2)
├── platform/
│   ├── core/                       ← plattformkjernen. Importerer ALDRI fra modules/
│   │   ├── policy_validator/       ← M-1-kjernen: engine.py, schema.py, audit.py
│   │   ├── registry.py             ← modulregister: oppdag, aktiver, deaktiver
│   │   ├── tests/                  ← core-tester (kjør: pytest platform/core/tests)
│   │   └── examples/               ← run_synthetic.py m.m.
│   └── modules/                    ← én mappe per modul, selvforsynt
│       └── m01_policy/
│           ├── manifest.yaml       ← id, versjon, status, avhengigheter, sjekkliste
│           └── README.md           ← modulens egen dokumentasjon
├── policies/                       ← DATA, ikke kode: skjema + bransjemaler
│   ├── policy-schema-v0.1.yaml
│   └── bransjemal-*.yaml
├── locales/                        ← ETT språk = ÉN fil. nb.json, en.json, …
├── design/
│   └── tokens.css                  ← ALT utseende defineres her, kun her
└── deploy/                         ← deploy-skript og miljøkonfig (kommer med PR-004)
```

## Reglene

1. **Core kjenner ikke modulene.** `platform/core/` importerer aldri noe fra `platform/modules/`. Registeret leser bare manifester.
2. **Moduler snakker kun med core.** Aldri modul-til-modul-import. Avhengigheter deklareres i manifestet og håndheves av registeret.
3. **En modul er flyttbar.** Hele modulen bor i sin mappe: kode, manifest, egne oversettelsesnøkler (prefiks `m01.`), egen README. Slett mappen → modulen er borte, resten kjører.
4. **Data er ikke kode.** `policies/` og `locales/` er data som valideres og versjoneres — de deployes gjennom samme løype som kode, men bor aldri inne i kodemapper.
5. **Én kilde per bekymring.** Utseende: `design/tokens.css`. Tekst: `locales/`. Regler: `policies/`. Sannhet om hva som bygges: `docs/spesifikasjon/`.
6. **Tester bor ved det de tester.** Core-tester i `platform/core/tests/`, modultester i modulens mappe under `tests/`.
