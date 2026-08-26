# M-57 ATS — kontrakten

Modulen er KUNDE av plattformen, aldri omvendt (m56-formen):

* **Inn**: ett `rekruttering.evaluering`-oppdrag gjennom beslutningsveien
  med `stillingsprofil_ref` og server-bygget `stillingsprofil`-snapshot
  (#200 valg B: payloaden navngir ALDRI bunten — bindingsraden
  `inndata_artefakt.oppdrag_id` er eneste sannhet, og modulen henter
  bunten via `hent_inndata_for_oppdrag`, 060), `antall_soknader`
  (1–5000, hard grense — 5001 avvises ved validering, aldri stille
  avkorting) og `omfang: bunt` (bærer 240-minuttersfristen).
  Valgfritt: `slettefrist_dogn` (30–365, standard 90) — kundens
  kandidatdatafrist, bundet i bestillingen fordi den ellers ikke har noe
  sted å stå (§5).

  BUNTEN BÆRER SIN EGEN DEKLARASJON (#161, eiers B): et lukket
  `soknader.json` i roten navngir hver kandidat (`kandidat_id`) og
  filene hens (`filer`), 1–5000 kandidater. Parseren binder manifestet
  toveis mot katalogen — deklarert fil uten medlem og medlem uten
  deklarasjon er like røde — og deklarert kandidattall må være lik
  oppdragets `antall_soknader` FØR én byte innhold pakkes ut. En
  kandidatform gjettes aldri ut av katalogen.

  Kandidaten KAN i tillegg deklarere `felter` — de strukturerte
  personverdiene (`navn`, `kjonn`, `alder`, `adresse`, `bilde`,
  `kontakt`; lukket sett, maks 10 verdier à 200 tegn per felt) — og de
  er BLINDINGENS kilde (#158s strukturelle retning): maskeringen bruker
  de deklarerte verdiene, aldri et fritekst-søk. En kandidat uten
  deklarerte felter kan ikke blindes og felles som
  `blinding_uten_felter` — et kodet utfall, aldri en ublindet
  evaluering.

  Feltverdien er SIN EGEN skrivemåte: ingen ledende/avsluttende
  blanktegn, ingen Cc/Cf-tegn (`U+200B`, RTL-markørene, kontrolltegn).
  Verdien er både det som maskeres og det port 16 leter etter, så
  `"Kari Testdal "` mot en tekst som skriver navnet uten hale gjør
  porten vakuøs uten å gjøre den tom. Alt annet er `manifest_feilformet`
  (og `ugyldig_maskeringsform` på den injiserte veien) — vi avviser, vi
  kanoniserer ikke.

  HELE GRENSESETTET ER ÉTT PREDIKAT, og det måles på BEGGE veier inn
  (eierdom, K2-kjennelse runde 4 på
  [#217](https://github.com/moka1980/disponit/pull/217), valg A):
  `blinding.feltverdier_lukket` eier type (en sekvens av strenger — en
  bar streng og et `set` avvises), tomhet (verken tom liste eller tom
  verdi), antall (maks 10) og lengde (maks 200 tegn), og
  manifestlesingen KALLER den i stedet for å telle opp sine egne. De to
  dørene skilles bare av feilkoden — `manifest_feilformet` mot
  `ugyldig_maskeringsform` sier hvilken dør som felte, aldri hvilken
  grense som gjaldt. Grunnen står i fire målte runder: så lenge
  grensesettet var to håndskrevne opptellinger, fant hver Cursor-runde
  nøyaktig én grense som sto på den ene døra og manglet på den andre
  (padding/Cf, så lengde/antall, så ukjent feltnavn, så tom
  liste/tom verdi). Døra som måler minst er den som gjelder — derfor
  finnes den ikke lenger som egen dør.

  KJENT GRENSE — TOKENKOLLISJONEN, og den er FAIL-CLOSED. En deklarert
  verdi som er delstreng av et token maskeringen selv produserer (`"K"`
  i `[KJONN-1]`, `"1"` i nummerhalen, `"NA"` i `[NAVN-1]`) står igjen
  inni sin egen maske. Port 16 søker klarteksten i HELE modellinputen,
  tokenene inkludert, og feller derfor `maskert_felt_i_modellinput` på
  en tekst der verdien faktisk ER borte. Utfallet er en NEKTET
  evaluering — aldri en lekkasje — og tilfellet er sjeldent.
  Deklarasjonen er dermed LOVLIG (eierdom, K2-kjennelse på
  [#217](https://github.com/moka1980/disponit/pull/217), valg A): å
  avvise den på vei inn ville gjort `kjonn: ["K"]` ulovlig mens `["M"]`
  er lovlig og bannlyst én-bokstavs initialer — systematisk,
  diskriminerende skade i normaltilfellet for å hindre et sjeldent
  fail-closed utfall. Den ekte lukkingen er strukturell blinding med et
  tokenalfabet som er DISJUNKT fra verdirommet, og den eies av
  [#158](https://github.com/moka1980/disponit/issues/158) — ikke av en
  formport her.

  `kandidat_id` er ASCII og LUKKET:
  `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` — ikke-tom, maks 64 tegn, starter
  alfanumerisk. Alt annet er `manifest_feilformet`. Kanonen er en
  eierdom (K2-kjennelse på #216, valg A), og den er en KONTRAKT mot
  kunden, ikke en valideringsdetalj: uten en lukket grammatikk er
  «to ID-er som ser like ut for et menneske» en ubundet klasse — vi
  avviste blanktegn i én runde, Cf/ZWSP i den neste, og RTL-markører,
  NFKC-ekvivalenter og homoglyfer (`а` U+0430 mot `a`) sto i kø etter
  dem. Én dom lukker hele klassen, og vi avviser i stedet for å
  kanonisere: bunten som mente `k1` sier `k1`. Æøå, mellomrom og
  skilletegn utenfor `._-` hører hjemme i kandidatens NAVN, ikke i
  identiteten hens. Kanonen er samtidig veien mot
  [#157](https://github.com/moka1980/disponit/issues/157) — når 057s
  UUID-anker eier kandidatidentiteten, strammes denne formen inn til
  ankeret; ASCII-kanonen er dermed et FREMTIDIG anker den peker på, og
  ikke en utsettelse: porten står lukket her og nå.
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
