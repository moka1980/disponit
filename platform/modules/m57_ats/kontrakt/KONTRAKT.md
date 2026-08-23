# M-57 ATS — kontrakten

Modulen er KUNDE av plattformen, aldri omvendt (m56-formen):

* **Inn**: ett `rekruttering.evaluering`-oppdrag gjennom beslutningsveien
  med `stillingsprofil_ref` og `soknadsbunt_ref` (artefaktlageret),
  `antall_soknader` (1–5000, hard grense — 5001 avvises ved validering,
  aldri stille avkorting) og `omfang: bunt` (bærer 240-minuttersfristen).
* **Ut**: ett artefakt per kandidat (rangering, funn med kildereferanse,
  intervjuspørsmål) og innstilte utsendingslister som VENTER på
  menneskelig signatur gjennom 056-kjeden. Ingen vei fra modellutdata til
  utsendingstekst — malene er plattformeide med lukket flettefeltsett
  (`maler.py`), og bruddet er en statisk port, ikke en kodegjennomgang.
* **Blinding** (klarsignalet §6): standard PÅ, målt på faktisk
  modellinput; avskruing er en auditert handling i flaten, ikke et
  bestillingsfelt.
* **Parsing** (§4/§7): i credential-fri, nettverksløs container;
  arkivgrensene håndheves FØR utpakking (`parsing.py`), porsjonsvis med
  fremdrift som evidens. Avbrutt kjøring → ingen promotert liste.
* **Kandidatdata** (§5): alt payload bor i de seks 057-lagrene og reapes
  ved fristen; modulen kan ikke forlenge den.
