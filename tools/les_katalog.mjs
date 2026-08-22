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
// `const M` to ganger i samme skript er en redeklarasjon, og den er en
// SyntaxError ved KOMPILERING — også når den andre står i kode som aldri
// kjører. Tegnene `const M = [` inne i en streng er en streng.

import fs from 'node:fs'
import vm from 'node:vm'

const SKRIPT_RE = /<script([^>]*)>([\s\S]*?)<\/script>/g
const TYPE_RE = /\btype\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))/i
const EKSTERN_RE = /\bsrc\s*=/i

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
  if (EKSTERN_RE.test(attributter)) return false
  const t = TYPE_RE.exec(attributter)
  if (!t) return true
  const type = (t[1] ?? t[2] ?? t[3]).trim().split(';')[0].trim().toLowerCase()
  return type === '' || JS_MIME.has(type)
}

/** Innholdet i alle `<script>`-taggene, skjøtt sammen slik nettleseren ser dem
 *  på ett skop. Prosaen rundt er HTML og ikke JavaScript. */
function skriptet(html) {
  const alle = [...html.matchAll(SKRIPT_RE)]
  if (!alle.length) stopp('fant ingen <script> i sannhetskilden')
  const deler = alle.filter(m => klassisk(m[1])).map(m => m[2])
  if (!deler.length) {
    stopp(`fant ${alle.length} <script> i sannhetskilden, men ingen som ` +
          'nettleseren kjører som klassisk JavaScript — katalogen står i et ' +
          'vanlig innskript, ikke i en datablokk eller en modul')
  }
  return deler.join('\n')
}

/** Katalogverdien `M` slik en JavaScript-motor ser den. */
function katalogverdien(kode, kilde) {
  let skript
  try {
    skript = new vm.Script(kode, {filename: kilde})
  } catch (e) {
    stopp(`sannhetskilden er ikke gyldig JavaScript: ${e.message}\n` +
          'To `const M = [ … ]` er nettopp denne feilen — en redeklarasjon ' +
          'nettleseren selv avviser.')
  }
  const ctx = vm.createContext(Object.create(null))
  let kastet = null
  try {
    skript.runInContext(ctx, {timeout: 10000})
  } catch (e) {
    // Ventet: UI-koden etter katalogen rører `document`. Se toppen av fila.
    kastet = e
  }
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
 *  `Object.prototype.toString` leser den interne merkelappen og virker på
 *  tvers; `Array.isArray` gjør det samme for lister. */
function somData(verdi) {
  if (verdi === null) return null
  const slag = typeof verdi
  if (slag === 'string' || slag === 'boolean') return verdi
  if (slag === 'number') {
    return Number.isFinite(verdi) ? verdi : {[IKKE_DATA]: 'tallet ' + verdi}
  }
  if (Array.isArray(verdi)) return verdi.map(somData)
  if (slag === 'object' && Object.prototype.toString.call(verdi) === '[object Object]') {
    const ut = {}
    for (const nokkel of Object.keys(verdi)) ut[nokkel] = somData(verdi[nokkel])
    return ut
  }
  return {[IKKE_DATA]: slag === 'object'
    ? Object.prototype.toString.call(verdi).slice(8, -1).toLowerCase()
    : slag}
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
  const M = katalogverdien(skriptet(html), sti)
  if (!Array.isArray(M)) {
    stopp('modulkatalogen `M` i sannhetskilden er ikke en liste — katalogen ' +
          'er en liste av modulposter, og bare det')
  }
  const moduler = M.map((post, i) => {
    if (post === null || typeof post !== 'object' || Array.isArray(post) ||
        Object.prototype.toString.call(post) !== '[object Object]') {
      stopp(`element ${i + 1} i modulkatalogen er ikke en modulpost: ` +
            `${JSON.stringify(String(post)).slice(0, 60)} — katalogen er en ` +
            'liste av poster, og bare det')
    }
    return somData(post)
  })
  process.stdout.write(JSON.stringify({moduler}, null, 1) + '\n')
}

main()
