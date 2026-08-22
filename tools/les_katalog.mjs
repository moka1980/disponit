// Modulkatalogen lest ut av sannhetskilden — ETT lesersteg, delt av alle.
//
// Bruk:  node tools/les_katalog.mjs <sti-til-spesifikasjon.html>
// Ut:    {"moduler": [ {…}, {…} ]} på stdout, i kildens rekkefølge.
// Feil:  melding på stderr, exit 1.
//
// HVORFOR DENNE FILEN FINNES
//
// Katalogen sto i JavaScript, og både `tools/gen_katalog.py` og porten i
// `platform/core/tests/test_katalog.py` leste den med håndskrevne skannere i
// Python. To skannere, hver med sin egen forestilling om hva JavaScript er.
// Nitten runder med Codex-review på PR #118 var nitten former de ikke hadde:
// en beregnet nøkkel, en escapet nøkkel, en spredning, en accessor, en
// malstreng, `for await`, en skråstrek som både er divisjon og mønster, en
// `catch` uten binding, `of` i en løkke … Hver runde la til én form. Formene
// tar aldri slutt, for mengden er hele ECMAScript-grammatikken, og en skanner
// som ikke kjenner en form gjør ikke noe høylytt — den leser noe annet enn
// nettleseren og sier ingenting.
//
// Eier avgjorde saken 20/8: bytt lesning, ikke legg til former. Her leses
// katalogen av den ENESTE leseren som per konstruksjon ikke kan ha en annen
// forestilling om JavaScript enn nettleseren har — en JavaScript-motor.
//
// KONTEKSTEN ER TOM MED VILJE
//
// `vm.createContext(Object.create(null))` gir en frisk global uten `require`,
// uten `process`, uten `fs`, uten nettverk. Lesing av fila skjer HER UTE, i
// Node, før konteksten finnes; inne i konteksten er det ingen vei ut.
// Katalogen selv er dataliteraler, så den trenger ingenting.
//
// Skriptet på siden gjør mer enn å erklære katalogen: rett etter `const M`
// begynner UI-koden, og den rører `document`, som ikke finnes her. Den
// KASTER, og det er ventet. `const M` er da allerede bundet i kontekstens
// leksikalske skop, og bindingen overlever at en senere setning feiler — så
// verdien leses etterpå, fra samme kontekst. Feiler noe FØR `const M`, finnes
// bindingen ikke, og da sier vi fra med den opprinnelige feilen som årsak.
// Vi gjetter aldri.
//
// `timeout` står fordi en løkke foran katalogen ellers ville hengt porten i
// stedet for å feile.
//
// TO ERKLÆRINGER ER EN SYNTAKSFEIL, IKKE ET ANKERSØK
//
// Generatoren og porten hadde hver sin regel for at `const M = [ … ]` skulle
// stå nøyaktig én gang, og hver sin måte å skille erklæringen fra de samme
// tegnene inne i en streng eller en kommentar. Motoren gjør det uten regel:
// `const M` to ganger er en redeklarasjon, og motoren avviser den selv — i
// samme <script> ved KOMPILERING, også når den andre står i kode som aldri
// kjører, og på tvers av <script> når den andre taggen bindes til det samme
// globale skopet. Tegnene `const M = [` inne i en streng er en streng.

import fs from 'node:fs'
import vm from 'node:vm'

const SKRIPT_RE = /<script([^>]*)>([\s\S]*?)<\/script>/g

// Ett attributt i en starttagg: navnet, og en eventuell verdi sitert med
// fnutter, apostrofer eller ingenting. Navnet står til det kommer mellomrom,
// `/`, `>`, `=` eller en fnutt — HTMLs egen oppdeling.
//
// ATTRIBUTTNAVN LESES HELE (Codex P2). `\btype\s*=` og `\bsrc\s*=` søkte i
// råteksten etter attributtlista, og `\b` fester seg like godt midt i et navn:
// `data-src="documentation"` og `data-type="module"` traff begge. Nettleseren
// har da ingen `src` og ingen `type`, kjører taggen som helt vanlig innskript
// — og leseren hoppet over den og meldte at katalogen ikke fantes. Et suffiks
// er ikke et navn.
const ATTRIBUTT_RE =
  /([^\s"'>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]*)))?/g

// MIME-typene HTML-standarden regner som klassisk JavaScript. En `<script>` er
// klassisk når den ikke har `type` i det hele tatt, når `type` er tom, eller
// når den står her.
const JS_MIME = new Set([
  'application/ecmascript', 'application/javascript',
  'application/x-ecmascript', 'application/x-javascript',
  'text/ecmascript', 'text/javascript', 'text/javascript1.0',
  'text/javascript1.1', 'text/javascript1.2', 'text/javascript1.3',
  'text/javascript1.4', 'text/javascript1.5', 'text/jscript',
  'text/livescript', 'text/x-ecmascript', 'text/x-javascript',
])

// Verdien et felt får når det ikke er DATA. Katalogen er en kilde som skal
// kunne leses av mer enn nettleseren, så en funksjon, et mønster eller en dato
// hører ikke hjemme i en modulpost. Den som konsumerer lesningen avgjør hva
// den skal si om det — her sier vi bare hva vi så.
const IKKE_DATA = '__ikke_data__'

function stopp(melding) {
  process.stderr.write(melding + '\n')
  process.exit(1)
}

/** Sant hvis `<script ${attributter}>` er en tagg nettleseren kjører som
 *  KLASSISK JavaScript, med kroppen sin som kilde (Codex P2).
 *
 *  En `<script type="application/ld+json">` er en DATABLOKK: nettleseren
 *  kjører den ikke, og innholdet er JSON-LD som er en syntaksfeil i JavaScript.
 *  En `type="module"` kjøres, men som modul — egen syntaks (`import`/`export`)
 *  og eget skop, så en `const M` der er ikke synlig for noen andre uansett.
 *  Fôrer vi noen av dem til en klassisk `vm.Script`, feiler HELE lesningen på
 *  et element siden selv håndterer helt riktig. Har taggen `src`, er koden
 *  ekstern og kroppen kjøres ikke i det hele tatt. */
function klassisk(attributter) {
  const attr = attributtene(attributter)
  if (attr.has('src')) return false
  if (!attr.has('type')) return true
  const type = attr.get('type').trim().split(';')[0].trim().toLowerCase()
  return type === '' || JS_MIME.has(type)
}

/** Attributtene i `<script ${attributter}>` som `navn → verdi`.
 *
 *  Navn er ikke versalfølsomme, og står et navn to ganger er det FØRSTE som
 *  gjelder — begge deler slik nettleseren gjør det. Se `ATTRIBUTT_RE`. */
function attributtene(tekst) {
  const ut = new Map()
  for (const a of tekst.matchAll(ATTRIBUTT_RE)) {
    const navn = a[1].toLowerCase()
    if (!ut.has(navn)) ut.set(navn, a[2] ?? a[3] ?? a[4] ?? '')
  }
  return ut
}

/** Innholdet i `<script>`-taggene nettleseren kjører, i dokumentrekkefølge.
 *  Prosaen rundt er HTML og ikke JavaScript. */
function skriptene(html) {
  const alle = [...html.matchAll(SKRIPT_RE)]
  if (!alle.length) stopp('fant ingen <script> i sannhetskilden')
  const deler = alle.filter(m => klassisk(m[1])).map(m => m[2])
  if (!deler.length) {
    stopp(`fant ${alle.length} <script> i sannhetskilden, men ingen som ` +
          'nettleseren kjører som klassisk JavaScript — katalogen står i et ' +
          'vanlig innskript, ikke i en datablokk eller en modul')
  }
  return deler
}

/** Katalogverdien `M` slik en JavaScript-motor ser den.
 *
 *  HVER TAGG ER SITT EGET SKRIPT (Codex P2). Taggene ble før skjøtt sammen til
 *  ett, og da arvet de noe nettleseren ikke gjør: et unntak i den første
 *  taggen stoppet resten. En side som deler seg i `<script>`-oppsett og
 *  `<script>`-katalog — der oppsettet rører `document` og kaster — ble dermed
 *  meldt som en side uten katalog, og kunne ikke genereres i det hele tatt.
 *  Nettleseren kjører taggene hver for seg, PÅ SAMME globale skop: en feil i
 *  én tagg stopper bare den, og en `const` som alt er bundet står. Det gjør vi
 *  også — samme `ctx` hele veien, egen `runInContext` per tagg.
 *
 *  En SYNTAKSFEIL stopper oss likevel, uansett hvilken tagg den står i. Den er
 *  kildens FORM, ikke sidens oppførsel, og to `const M` er nettopp en slik:
 *  i samme tagg avvises den ved kompilering, på tvers av tagger når den andre
 *  taggen bindes. Nettleseren ville tiet og beholdt den første katalogen; her
 *  er en kilde med to kataloger en kilde ingen skal gjette i. */
function katalogverdien(deler, kilde) {
  const ctx = vm.createContext(Object.create(null))
  let kastet = null
  deler.forEach((kode, i) => {
    const navn = deler.length > 1 ? `${kilde} <script> ${i + 1}` : kilde
    let skript
    try {
      skript = new vm.Script(kode, {filename: navn})
    } catch (e) {
      stopp(`${navn} er ikke gyldig JavaScript: ${e.message}\n` +
            'To `const M = [ … ]` er nettopp denne feilen — en redeklarasjon ' +
            'nettleseren selv avviser.')
    }
    try {
      skript.runInContext(ctx, {timeout: 10000})
    } catch (e) {
      // Feilobjektet kommer fra en annen realm, så `instanceof` biter ikke.
      if (e && e.name === 'SyntaxError') {
        stopp(`${navn} er ikke gyldig JavaScript: ${e.message}\n` +
              'To `const M = [ … ]` i hver sin <script> er nettopp denne ' +
              'feilen — den andre erklæringen avvises når taggen bindes.')
      }
      // Ventet: UI-koden etter katalogen rører `document`. Se toppen av fila.
      if (!kastet) kastet = e
    }
  })
  let M
  try {
    M = vm.runInContext('M', ctx, {timeout: 10000})
  } catch (e) {
    stopp('fant ingen modulkatalog `M` i sannhetskilden.\n' +
          `Skriptet stoppet før erklæringen: ${kastet ? kastet.message : e.message}`)
  }
  return M
}

/** `verdi` som ren data, eller `IKKE_DATA`-merket hvis den ikke er det.
 *
 *  Verdiene kommer fra en annen kontekst, så de har ikke vertens prototyper.
 *  `Array.isArray` leser den interne merkelappen og virker på tvers; det som
 *  IKKE er en liste avgjøres av `plattObjekt()`.
 *
 *  OBJEKTET VI BYGGER HAR INGEN PROTOTYPE (Codex P2). `{}` arver `__proto__`
 *  fra `Object.prototype`, og den egenskapen er en SETTER: `ut['__proto__'] =
 *  …` bytter prototypen på `ut` i stedet for å lage et felt. Nøkkelen ble da
 *  borte fra JSON-en, og med den alt som lå under — også en funksjon eller en
 *  accessor den rekursive kontrollen skulle ha meldt. En katalogpost med en
 *  egen, beregnet `['__proto__']`-nøkkel er data som alle andre nøkler her:
 *  over `Object.create(null)` finnes det ingen setter å treffe. */
function somData(verdi) {
  if (verdi === null) return null
  const slag = typeof verdi
  if (slag === 'string' || slag === 'boolean') return verdi
  if (slag === 'number') {
    return Number.isFinite(verdi) ? verdi : {[IKKE_DATA]: 'tallet ' + verdi}
  }
  if (Array.isArray(verdi)) {
    const ut = []
    for (let i = 0; i < verdi.length; i++) ut.push(egenskapen(verdi, i))
    return ut
  }
  if (slag === 'object' && plattObjekt(verdi)) {
    const ut = Object.create(null)
    for (const nokkel of Object.keys(verdi)) ut[nokkel] = egenskapen(verdi, nokkel)
    return ut
  }
  return {[IKKE_DATA]: slag === 'object' ? slaget(verdi) : slag}
}

/** Sant hvis `verdi` er et objekt som bare bærer felt.
 *
 *  KLASSIFISERINGEN PÅKALLER INGENTING (Codex P2).
 *  `Object.prototype.toString.call(verdi)` LESER `Symbol.toStringTag`, og den
 *  egenskapen kan siden gi en getter. Getteren kjørte da her ute i Node, etter
 *  at `runInContext` hadde returnert — utenfor `timeout`-en, altså nøyaktig
 *  hullet F3 lukket for `verdi[nokkel]`, med en ny inngang: kontrollen som
 *  skulle avgjøre om verdien i det hele tatt er data, kjørte sidens kode først.
 *  Én `get [Symbol.toStringTag](){for(;;);}` hang generatoren, porten og CI.
 *
 *  `getPrototypeOf` leser en intern peker og påkaller ingen egenskap. Over et
 *  vanlig objekt står `Object.prototype` og over den ingenting — også for
 *  `Object.create(null)`, som ikke har noe over seg i det hele tatt. En `Date`,
 *  en `RegExp`, en `Map` eller en klasseforekomst har ett ledd til, og det
 *  leddet er nettopp oppførselen en katalogverdi ikke kan ha. */
function plattObjekt(verdi) {
  const over = Object.getPrototypeOf(verdi)
  return over === null || Object.getPrototypeOf(over) === null
}

/** Navnet på slaget `verdi` er — `date`, `regexp`, `map` — til meldingen.
 *
 *  Også dette leses uten å påkalle noe: konstruktøren og navnet dens hentes
 *  som DESKRIPTOR, og bare når de er vanlige verdier. Er de det ikke, sier vi
 *  bare `objekt`; en merkelapp er en forklaring, ikke en grunn til å kjøre
 *  sidens kode. */
function slaget(verdi) {
  const over = Object.getPrototypeOf(verdi)
  const k = over && Object.getOwnPropertyDescriptor(over, 'constructor')
  if (!k || !('value' in k) || typeof k.value !== 'function') return 'objekt'
  const n = Object.getOwnPropertyDescriptor(k.value, 'name')
  if (!n || !('value' in n) || typeof n.value !== 'string' || !n.value) {
    return 'objekt'
  }
  return n.value.toLowerCase()
}

/** Egenskapen `nokkel` på `objekt`, som data.
 *
 *  Egenskapen leses som DESKRIPTOR, aldri som `objekt[nokkel]` (Codex P2). Er
 *  den en accessor, er getteren sidens egen kode, og et vanlig oppslag ville
 *  kjørt den HER UTE i Node — etter at `runInContext` har returnert, altså
 *  utenfor `timeout`-en som verner lesningen mot en løkke i kilden. Én
 *  `get kl(){for(;;);}` ville hengt generatoren, porten og CI uten at noe slo
 *  av.
 *
 *  Getteren kjøres derfor ikke i det hele tatt, og det er samme svar som
 *  merket alt gir for en funksjon: en egenskap som først BLIR TIL når siden
 *  kjører kan hverken leses eller måles av andre enn nettleseren, og katalogen
 *  skal kunne leses av mer enn den.
 *
 *  Et hull i en liste har ingen deskriptor. Det er `null`, slik JSON også ser
 *  det — ikke en accessor. */
function egenskapen(objekt, nokkel) {
  const d = Object.getOwnPropertyDescriptor(objekt, nokkel)
  if (!d) return null
  if (!('value' in d)) return {[IKKE_DATA]: 'accessor'}
  return somData(d.value)
}

function main() {
  const sti = process.argv[2]
  if (!sti) stopp('bruk: node tools/les_katalog.mjs <sti-til-spesifikasjon.html>')
  let html
  try {
    html = fs.readFileSync(sti, 'utf8')
  } catch (e) {
    stopp(`fant ikke sannhetskilden: ${sti}`)
  }
  const M = katalogverdien(skriptene(html), sti)
  if (!Array.isArray(M)) {
    stopp('modulkatalogen `M` i sannhetskilden er ikke en liste — katalogen ' +
          'er en liste av modulposter, og bare det')
  }
  // Elementene leses med `egenskapen()`, ikke med `M[i]`: også et ledd i
  // katalogen kan være en accessor, og den skal ikke kjøre her. Posten er
  // derfor alt gjort om til data når den kontrolleres.
  const moduler = []
  for (let i = 0; i < M.length; i++) {
    const post = egenskapen(M, i)
    if (post === null || typeof post !== 'object' || Array.isArray(post) ||
        IKKE_DATA in post) {
      // Meldingen skrives med `JSON.stringify`, ikke `String()`: posten er alt
      // gjort om til data, og de dataene kan bære et objekt uten prototype —
      // og det har ingen `toString` å konvertere med.
      stopp(`element ${i + 1} i modulkatalogen er ikke en modulpost: ` +
            `${JSON.stringify(post).slice(0, 60)} — katalogen er en ` +
            'liste av poster, og bare det')
    }
    moduler.push(post)
  }
  process.stdout.write(JSON.stringify({moduler}, null, 1) + '\n')
}

main()
