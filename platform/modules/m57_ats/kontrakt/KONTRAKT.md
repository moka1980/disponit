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

  EN NØKKEL FÅR STÅ ÉN GANG per JSON-objekt i dokumentet.
  `{"navn": [...], "navn": [...]}` er `manifest_feilformet`, ikke
  «siste vinner»: JSONs standardoppførsel taper den første verdien
  stille, og taper den FØR noen port ser dokumentet. En deklarert
  personverdi som forsvinner der, blir aldri maskert, og port 16 leter
  bare etter det som ER i avmaskeringstabellen — utfallet ville vært
  klartekst til modellen i en kjøring som telles som blindet. Vi
  avviser, vi velger ikke.

  Kandidaten KAN i tillegg deklarere `felter` — de strukturerte
  personverdiene (`navn`, `kjonn`, `alder`, `adresse`, `bilde`,
  `kontakt`; lukket sett, maks 10 verdier à 200 tegn per felt) — og de
  er BLINDINGENS kilde (#158s strukturelle retning): maskeringen bruker
  de deklarerte verdiene, aldri et fritekst-søk. En kandidat uten
  deklarerte felter kan ikke blindes og felles som
  `blinding_uten_felter` — et kodet utfall, aldri en ublindet
  evaluering.

  Feltverdien er SIN EGEN skrivemåte: ingen ledende eller avsluttende
  blanktegn. Verdien er både det som maskeres og det port 16 leter
  etter, så `"Kari Testdal "` mot en tekst som skriver navnet uten hale
  gjør porten vakuøs uten å gjøre den tom. Grensen er STRUKTURELL —
  `verdi == verdi.strip()` — og alt annet er `manifest_feilformet` (og
  `ugyldig_maskeringsform` på den injiserte veien); vi avviser, vi
  kanoniserer ikke.

  VAKUØSITETEN MÅLES PÅ EFFEKT, PER FELT, og det er dén porten som eier
  usynlige og forvekslingsbare tegn (eierdom, K2-kjennelse runde 5 på
  [#217](https://github.com/moka1980/disponit/pull/217), valg B): i
  `blind` må HVERT deklarert felt treffe dokumentteksten minst én gang.
  Et felt der ingen verdi traff er en vakuøs deklarasjon →
  `ugyldig_maskeringsform`. En enkelt VERDI uten treff er derimot lovlig
  når en søsterverdi i samme felt traff — ellers ville defensive
  varianter (`["Kari Testdal", "Kari"]`) blitt selvmotsigende farlige,
  og deklarasjonen presset mot færre varianter, som er feil fortegn for
  personvern.

  MÅLINGEN SKJER PÅ ORIGINALTEKSTEN, FØR NOEN ERSTATNING (eierdom,
  K2-kjennelse runde 6 på
  [#217](https://github.com/moka1980/disponit/pull/217), valg A). Det er
  runde-5-dommens egen semantikk — traff deklarasjonen DOKUMENTET — og
  ikke en ny regel. Telte man treffene underveis i maskeringen, målte
  man mot tokener maskeringen selv nettopp hadde skrevet: med
  `{"navn": ["Al"], "alder": ["forty-two"]}` mot en tekst som skriver
  navnet i FULLBREDDE (`Ａｌ`, som ikke er ASCII-`Al` under Unicodes
  enkle case-folding) ble `forty-two` erstattet først, og `Al` traff
  deretter `AL` inni `[ALDER-1]`. Feltet talte som truffet uten å ha
  truffet dokumentet, porten sa god, og fullbredde-navnet gikk i
  klartekst til modellen mens kjøringen telte som blindet. Søket før
  erstatningen fjerner den omgåelsen: tokenene finnes ikke ennå.

  Grunnen til at dette IKKE er en tegnliste til: skrivemåteporten var en
  håndskrevet svarteliste over Unicode-kategorier (`Cc`/`Cf`), og en
  svarteliste er ufullstendig i ett predikat like fullt som i to. NBSP
  (`Zs`), `U+2010` (`Pd`) og en NFD-dekomponert `å` er ingen av
  kategoriene, og hver av dem gir samme vakuum: maskeringen treffer
  ingenting, avmaskeringstabellen er likevel ikke tom, port 16 leter
  etter en form som ikke står i dokumentet — og kjøringen telles som
  blindet mens klartekstnavnet går til modellen. Det er en LEKKASJE, og
  den lukkes ved å måle at deklarasjonen VIRKET, ikke ved å vite hvilket
  tegn som gjorde at den bommet.

  KJENT GRENSE — RESTKLASSEN, og den er eid av
  [#158](https://github.com/moka1980/disponit/issues/158): en forekomst
  i teksten som ingen deklarert verdi matcher, MENS en annen verdi i
  samme felt traff, er udetekterbar uten navnegjenkjenning i fritekst.
  Porten her måler at feltet virket minst én gang, ikke at det virket
  overalt. Fullstendighet kommer med strukturell blinding — der finnes
  personfeltene ikke i inputen i det hele tatt — ikke med en port til
  på denne veien.

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

  Presisering etter runde 5: ett predikat lukket DIVERGENSEN mellom de
  to dørene, og den aksen er reelt død. Det lukket aldri
  UFULLSTENDIGHET i grensesettet — en ufullstendig tegnliste lever like
  godt i ett predikat som i to, og runde 5 kom nettopp der. Delt
  predikat eier derfor det begge dørene KAN måle uten dokumentteksten;
  det som bare kan måles MOT teksten, eies av vakuøsitetsporten over.

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

  SAMME KOLLISJON KAN OGSÅ KORRUPTERE MASKERINGEN, og det skal stå
  eksplisitt: erstatningen kan skrive inn i et token den selv har lagt
  igjen. Med `{"navn": ["Al"], "alder": ["forty-two"]}` mot «Al is
  forty-two» blir resultatet `[NAVN-1] is [[NAVN-1]DER-1]` — `[ALDER-1]`
  er spist innenfra, og avmaskeringstabellen er ikke lenger reversibel.
  Port 16 passerer, for klarteksten ER borte: utfallet er KORRUPT
  modellinput, ikke en lekkasje, og korrupt input kan endre både
  kravfunn og rangering. Dette er en KJENT GRENSE til det disjunkte
  tokenalfabetet lander, og den eies av
  [#158](https://github.com/moka1980/disponit/issues/158) (eierdom,
  K2-kjennelse runde 6 på #217, valg A):

  `dom-klasse: tokenkollisjon-korrupsjon · felt i #217 · https://github.com/moka1980/disponit/pull/217#issuecomment-5430381316`

  KJENT GRENSE — ARKIVINSTANSEN I VINDUET, og den er en LEKKASJE, ikke
  en nektelse. Kjøringen åpner buntstien flere uavhengige ganger:
  deklarasjonen leses av `les_manifest`, innholdet av `les_porsjonsvis`
  — to åpninger av samme STI. Byttes fila i vinduet mellom dem med et
  TOPOLOGI-BEVARENDE bytte (samme medlemsnavn, samme antall), blindes
  arkiv A-s deklarasjon inn i arkiv B-s dokument. A deklarerer `Kari`,
  B-s CV skriver «Kari og Ola»: `Kari` maskeres og TREFFER, så
  vakuøsitetsporten tier; `Ola` er ikke deklarert i A, så port 16 har
  ingenting å lete etter; kjøringen fullfører som blindet med `Ola` i
  klartekst hos modellen. De eksisterende portene tar bare det
  topologi-ENDRENDE byttet (`medlem_uadressert`,
  `manifest_medlem_mangler`, `kandidattall_avvik`).

  LUKKINGEN ER KALLERENS, OG DEN ER EN INSTANSBINDING (eierdom,
  K2-kjennelse runde 7 på
  [#217](https://github.com/moka1980/disponit/pull/217), valg B i
  inode-form): kalleren holder én åpen fd på bunten og gir `kjor_bunt`
  stien `/proc/self/fd/<fd>`. Da går ALLE åpningene gjennom samme
  inode, og et stibytte i vinduet kan per konstruksjon ikke nå
  kjøringen. Inodebindingen er et BEVIS, ikke en heuristikk — en
  `st_ino`-sammenligning ville vært det siste. Kontrolleren er eneste
  produksjonskaller og eier fila den selv skrev, så kallformen bor der;
  en kaller som gir en DELBAR filsti bærer klassen selv, og
  `kjor_bunt`-docstringen krever derfor den instansbundne stien.

  `dom-klasse: arkivinstans-toctou · felt i #217 · https://github.com/moka1980/disponit/pull/217#issuecomment-5430767580`

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
* **Kjøringens varighet** bindes ved LEVERING, ikke underveis.
  Controlleren måler vinduet FØR bunten hentes (`_evalueringsfrist`:
  den tidligste av `utforelsesfrist`, `opplasting.utloper` og
  `kvittering_utloper`, minus `AVSLUTNINGSMARGIN_S`) og avviser et
  claim som er dødfødt. Er claimet levedyktig, løper `kjor_bunt` uten
  internt tak: `frist_s` sendes ikke inn i kandidatløkka, og
  `puls.tapt` leses først når `with _Heartbeat`-blokken slipper. En
  evaluering som ble startet i tide, men løper forbi vinduet — eller
  mister leasen midtveis — fullfører derfor arbeidet, og stoppes først
  på LEVERINGSPORTENE: `lease_tapt` før opplasting, og kvitteringens
  eget statusskifte, som etter fristen svarer 202
  `lagret_uten_statusendring` → `ukvittert`. Utfallet er aldri et
  falskt `utfort`; prisen er persondata og modellkall brukt utenfor
  det annonserte vinduet.

  KJENT BEGRENSNING, OG DEN ER UTSATT TIL
  [#173](https://github.com/moka1980/disponit/issues/173) (eierdom,
  K2-kjennelse på #218, valg 1). Både det løpende fristtaket og et
  lease-avbrudd midt i evalueringen vil ha DET SAMME: et budsjett- og
  avbruddssignal tredd inn i `kjor_bunt`s per-kandidat-løkke, og et
  avbrudd der er en ny returkontrakt på funksjonen — ny maskin, som
  K1 sender til egen PR. Løkka er nøyaktig den #173 skriver om
  (artefaktene strømmes til kandidatlagrene, retur blir referanser +
  rangering), så avbruddssemantikk skrevet nå ville blitt skrevet to
  ganger. Samme klasse som minnegrensen `kjor_bunt` alt bærer: 23/8-
  dommen legger HARD SPERRE mot kjøring på reelle bunter i full
  størrelse før #173 er landet, og det er den sperren som holder
  varigheten nede i mellomtiden.

  `dom-klasse: kjoring-avbrudd-og-frist · felt i #218 · https://github.com/moka1980/disponit/pull/218#issuecomment-5431892763`
* **Kandidatdata** (§5): alt payload bor i de seks 057-lagrene og reapes
  ved fristen; modulen kan ikke forlenge den. Unntaket er den promoterte
  rapporten over, som i dag bærer den samme payloaden uten å arve
  fristen — utsatt, K1 →
  [#168](https://github.com/moka1980/disponit/issues/168).
