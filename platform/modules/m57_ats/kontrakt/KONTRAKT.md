# M-57 ATS — kontrakten

Modulen er KUNDE av plattformen, aldri omvendt (m56-formen):

* **Inn**: ett `rekruttering.evaluering`-oppdrag gjennom beslutningsveien
  med `stillingsprofil_ref` og `soknadsbunt_ref` (artefaktlageret),
  `antall_soknader` (1–5000, hard grense — 5001 avvises ved validering,
  aldri stille avkorting) og `omfang: bunt` (bærer 240-minuttersfristen).
  Valgfritt: `slettefrist_dogn` (30–365, standard 90) — kundens
  kandidatdatafrist, bundet i bestillingen fordi den ellers ikke har noe
  sted å stå (§5).
* **Ut**: ÉN promotert rapport per oppdrag —
  `rekruttering.evaluering.rapport`, den rangerte kandidatlisten med
  begrunnede funn (kildereferanse), poeng med nedbrytning og
  intervjuspørsmål PER KANDIDAT inni seg — og innstilte utsendingslister
  som VENTER på menneskelig signatur gjennom 056-kjeden. Ingen vei fra
  modellutdata til utsendingstekst — malene er plattformeide med lukket
  flettefeltsett (`maler.py`), og bruddet er en statisk port, ikke en
  kodegjennomgang.

  ETT artefakt, ikke ett per kandidat (Codex P1). Linja sto før som «ett
  artefakt per kandidat», og det er noe plattformen ikke kan levere:
  kvitteringen bærer én skalar `artefakt_id`, og `api/app.py` promoterer
  nøyaktig den ene raden ved fullføring. Med 4 999 kandidater igjen som
  staged opplastinger ville en vellykket evaluering ikke kunnet levere
  sitt eget deklarerte utfall. Det per-kandidat-artefaktet spesifikasjonen
  navngir, er `kandidat_evalueringsartefakt` — ett av de seks
  057-lagrene, altså INTERN kandidatpayload under §5-fristen, ikke varig
  promotert evidens. De to var skrevet sammen her; de er skilt nå.
  (En flerartefakt-kvittering er ny maskin i selve
  fullføringsprotokollen — K1, ikke en fiksrunde.)

  UTSATT, K1 → [#168](https://github.com/moka1980/disponit/issues/168) —
  DENNE RAPPORTEN ER KANDIDATPAYLOAD, OG DEN REAPES ALDRI (Codex P1).
  Linja over og «Kandidatdata (§5)» ni linjer ned motsier hverandre:
  funnene med kildereferanse, poengnedbrytningen og intervjuspørsmålene
  er de samme personopplysningene som ligger i
  `kandidat_evalueringsartefakt` og `kandidat_intervjusporsmal`, men den
  ene kopien er under fristen og den andre er varig evidens.
  `reap_kandidatdata` nuller nøyaktig de seks lagrene; den eneste veien
  som nuller `artefakt.ciphertext` er `rydd_staged_artefakter`, og
  predikatet der er `tilstand = 'staged'`. `promotert`/`bevart` er
  terminale og RETAINED med vilje. Når fristen løper ut, er lagrene
  tomme, prosessen merket reapet — og rapporten fortsatt dekrypterbar.
  Begge utveiene er ny maskin: å holde payload ute av rapporten
  definerer om hva modulen LEVERER (og hvor UI-en leser det), og å binde
  artefaktet til fristen er en frist per artefakt pluss en reaper som
  rører `bevart` i 016/019 — altså et navngitt unntak fra «promotert
  evidens er varig». Eiers valg står i #168 (A: rapporten blir
  beslutningssporet uten payload; B: artefaktet arver fristen; C: begge).
* **Blinding** (klarsignalet §6): standard PÅ, målt på faktisk
  modellinput; avskruing er en auditert handling i flaten, ikke et
  bestillingsfelt.
* **Parsing** (§4/§7): i credential-fri, nettverksløs container;
  arkivgrensene håndheves FØR utpakking (`parsing.py`), porsjonsvis med
  fremdrift som evidens. Avbrutt kjøring → ingen promotert liste.
* **Kandidatdata** (§5): alt payload bor i de seks 057-lagrene og reapes
  ved fristen; modulen kan ikke forlenge den. Unntaket er den promoterte
  rapporten over, som i dag bærer den samme payloaden uten å arve
  fristen — utsatt, K1 →
  [#168](https://github.com/moka1980/disponit/issues/168).
