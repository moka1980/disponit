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

const SKRIPT_RE = /<script[^>]*>([\s\S]*?)<\/script>/g

// Verdien et felt får når det ikke er DATA. Katalogen er en kilde som skal
// kunne leses av mer enn nettleseren, så en funksjon, et mønster eller en dato
// hører ikke hjemme i en modulpost. Den som konsumerer lesningen avgjør hva
// den skal si om det — her sier vi bare hva vi så.
const IKKE_DATA = '__ikke_data__'

function stopp(melding) {
  process.stderr.write(melding + '\n')
  process.exit(1)
}

/** Innholdet i alle `<script>`-taggene, skjøtt sammen slik nettleseren ser dem
 *  på ett skop. Prosaen rundt er HTML og ikke JavaScript. */
function skriptet(html) {
  const deler = [...html.matchAll(SKRIPT_RE)].map(m => m[1])
  if (!deler.length) stopp('fant ingen <script> i sannhetskilden')
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
