# PUSH-INSTRUKS: Disponit-rebrand + tilgang for AI-rollene

> **Status 2026-08-01 (Claude Code):** Del A er utført av denne PR-en. **Del B var allerede
> utført før instruksen ble skrevet** — branch protection ble slått på via GitHub-API-et samme
> dag, med nøyaktig de fire reglene i del B. Ikke sett dem opp på nytt; verifiser i stedet:
> `gh api repos/moka1980/disponit/branches/main/protection`.
> Del C og D står fortsatt åpne og er Eiers.
>
> ⚠️ Del B har ett hull som er bevist, ikke antatt: `enforce_admins` er av, og alle AI-rollene
> kjører foreløpig som `moka1980` (admin). En direkte push til `main` ble sluppet gjennom med
> «Bypassed rule violations». Slå på `enforce_admins` **først når rolle-kontoene i del C finnes** —
> med bare én konto låser `main` seg, fordi GitHub ikke lar noen godkjenne sin egen PR.

## A. Push rebrand-endringene (Eier eller Claude Code — 2 minutter)

Endringene i denne pakken (navnebytte til Disponit): `README.md`,
`docs/STRUKTUR.md`, `docs/RUTINER.md`, `.github/CODEOWNERS` (@moka1980),
`locales/nb.json`, `locales/en.json`, og `docs/spesifikasjon/` der filen nå
heter `disponit-prototype-v9.html` (gammel fil slettet).

I terminal (eller be Claude Code gjøre det ordrett):

```bash
git clone https://github.com/moka1980/disponit.git
cd disponit
git checkout -b chore/disponit-rebrand
# pakk ut disponit-repo-v0.2.zip OVER repo-mappen (overskriv alt)
git add -A
git commit -m "chore: rebrand til Disponit (README, STRUKTUR, CODEOWNERS, spesifikasjon, locales)"
git push -u origin chore/disponit-rebrand
```

Åpne deretter PR-en på GitHub → CI kjører automatisk → merge.
(Alternativ uten terminal: GitHub → «Add file → Upload files» på en ny branch.)

## B. Aktiver branch protection (Eier — én gang, 3 minutter)

**Utført — ikke gjør dette på nytt.** Slik står den nå, etter Eiers beslutning om at
merge-porten driftes av pipelinen uten Eier:

1. ✅ Require a pull request before merging — **0 påkrevde godkjenninger**
2. ✅ Require status checks to pass → `test` (strict)
3. ❌ Require review from Code Owners — **slått av med vilje**, se RUTINER pkt. 8
4. ✅ Require linear history · ❌ Allow force pushes · ❌ Allow deletions
5. ✅ **Include administrators** — reglene gjelder også repo-eier

Uten dette er CODEOWNERS og CI bare pynt — med dette nekter GitHub feil.

## C. Tilgang for AI-rollene

| Rolle | Tilgang | Slik settes det opp |
|---|---|---|
| **Claude Code** | Skrive til brancher, aldri main | Kjøres på din maskin eller Cloud Server S der du er git-innlogget, ELLER: GitHub → Settings → Developer settings → Fine-grained token, kun repo `disponit`, Contents+Pull requests: Read/Write. Legg tokenet i miljøvariabel på serveren — aldri i chat eller i repoet. |
| ~~**Codex**~~ | Fjernet 29/8-26 (RUTINER §10) | Skal ikke konfigureres; koble fra GitHub-kontoen om den fortsatt er tilkoblet. |
| ~~**Cursor**~~ | Fjernet 29/8-26 (RUTINER §10) | Skal ikke konfigureres; avinstaller GitHub-appen og slett `CURSOR_API_KEY`-secreten. |
| **ChatGPT** | Kun lese | Repoet er offentlig — gi ChatGPT lenken https://github.com/moka1980/disponit og be om PR-001-review mot `platform/core/policy_validator/` med de tre spørsmålene i `docs/README-arbeidsflyt.md`. |
| **Claude.ai** | Kun lese (offentlig repo) | Drafter og reviewer via chat; leverer push-klare pakker som denne. |

## D. Viktig sikkerhetsvalg å ta snart (Eier)

Repoet er **offentlig**. Det er uproblematisk nå (ingen secrets, ingen
kundedata — kun kode, policyer og spesifikasjon), og gjør ChatGPT-review
enklere. Men før reelle konfigurasjoner, kundenavn eller
forretningssensitive bransjemaler committes: vurder å sette repoet privat
og gi ChatGPT filer manuelt i stedet. Beslutningen bør tas før PR-004
(API + deploy-konfig).
