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

// Tidsgrensen all kjøring av kildens kode står under — også materialiseringen,
// se `BYGG_HJELPER`. Miljøvariabelen finnes for portens egne prøver (en felle
// som aldri returnerer skal kunne bevises på millisekunder, ikke på ti
// sekunder); produksjonsverdien er ti sekunder.
const TIMEOUT = Number(process.env.LES_KATALOG_TIMEOUT_MS) > 0
  ? Number(process.env.LES_KATALOG_TIMEOUT_MS) : 10000

// KJENT GRENSE (sporet i #137): `SKRIPT_RE` leser råteksten og ser ikke at en
// tagg står inne i en HTML-kommentar (`<!-- <script>…</script> -->`).
// Nettleseren tokeniserer; en regex gjør ikke det, og en regex som først
// skreller kommentarer er tilstandsmaskinen K4/SP-13 forbyr. Løsningen er
// nettlesergrammatikkens egen leser (jsdom) og tas som eget arkitekturvalg i
// #137 — ikke som en fjerde form her. En kommentert-ut ekstra katalog gir i
// dag et HØYLYTT stopp (redeklarasjon), aldri et stille galt valg; en
// kommentert-ut ENESTE katalog gir «fant ingen» — også høylytt.

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

// MATERIALISERINGEN, SOM KONTEKST-KODE (Codex P2, F10 — rotfiksen).
//
// F3 var en getter, F7 en merkelapp-getter, F10 en proxy-felle: tredje funn i
// samme mekanisme, og klassen er ubegrenset — JavaScript lar et objekt fange
// nesten enhver operasjon (`getPrototypeOf`, `getOwnPropertyDescriptor`,
// `Object.keys`, alle med feller når verdien er en Proxy). En liste over
// «trygge operasjoner» kan derfor aldri bli komplett. Grensen som holder er
// ikke HVILKE operasjoner som kjøres, men HVOR: alt som rører en verdi fra
// konteksten kjører INNE i konteksten, under samme `timeout` som all annen
// kode fra kilden. En felle som aldri returnerer gir tidsavbrudd; en felle
// som kaster gir stopp. Det eneste som krysser kontekstgrensen ut, er én
// JSON-tekst.
//
// Logikken er den samme som portens etablerte doktrine: egenskaper leses som
// DESKRIPTOR og en accessor påkalles aldri (F3/F7); ordinære dataobjekter
// kjennes på prototypekjeden; det som bygges har null-prototype så en
// beregnet `['__proto__']`-nøkkel er et felt (F11).
//
// HJELPEREN BYGGES FØR SIDENS KODE KJØRER (Codex P2, F15)
//
// Intrinsics ble fanget når materialiseringen START — altså ETTER at kildens
// egne skript hadde kjørt. En helt ordinær leksikalsk global i sidekoden,
// `const Object = appHelpers`, skygger da for intrinsicen: `Object` i
// materialiseringen ble sidens objekt, `Object.getOwnPropertyDescriptor` ble
// `undefined`, og lesningen feilet på en side nettleseren håndterer fint.
// `Array`, `Number` og `JSON` har samme inngang. Det er ikke et angrep — det
// er navnekollisjon, og en leser som knekker på den leser ikke kilden.
//
// Derfor bygges hjelperen i en EGEN kjøring i den friske konteksten, før noen
// tagg fra kilden har sluppet til. Den lukker om intrinsics slik de var da
// konteksten ble laget, og `M` bindes først senere — en fri variabel slås opp
// i det globale leksikalske skopet når `materialiser()` KALLES, ikke når
// hjelperen defineres. Alt kjører fortsatt inne i konteksten, under samme
// tidsgrense; det eneste som krysser ut er én JSON-tekst.
const HJELPER = '__les_katalog__'

const BYGG_HJELPER = `globalThis[${JSON.stringify(HJELPER)}] = (() => {
  'use strict'
  const IKKE_DATA = ${JSON.stringify(IKKE_DATA)}
  const beskriv = Object.getOwnPropertyDescriptor
  const proto = Object.getPrototypeOf
  // EGNE EGENSKAPER, IKKE BARE DE TELLBARE (Codex P2, F16). Object.keys
  // hopper stille over en ikke-tellbar egen egenskap, så en post bygget med
  // Object.defineProperty(post, 'status', {value:'planlagt'}) forsvant ut av
  // JSON-en mens nettleseren så post.status — nøyaktig det stille avviket
  // hele lesersteget finnes for å fjerne. Tellbarhet er en visningsdetalj;
  // egenskapen er der.
  const nokler = Object.getOwnPropertyNames
  const erListe = Array.isArray
  const lagUten = Object.create
  const endelig = Number.isFinite
  const tilJson = JSON.stringify
  const plattObjekt = (verdi) => {
    const over = proto(verdi)
    return over === null || proto(over) === null
  }
  const slaget = (verdi) => {
    const over = proto(verdi)
    const k = over && beskriv(over, 'constructor')
    if (!k || !('value' in k) || typeof k.value !== 'function') return 'objekt'
    const n = beskriv(k.value, 'name')
    if (!n || !('value' in n) || typeof n.value !== 'string' || !n.value) {
      return 'objekt'
    }
    return n.value.toLowerCase()
  }
  const merket = (hva) => {
    const ut = lagUten(null)
    ut[IKKE_DATA] = hva
    return ut
  }
  const somData = (verdi) => {
    if (verdi === null) return null
    const slag = typeof verdi
    if (slag === 'string' || slag === 'boolean') return verdi
    if (slag === 'number') {
      return endelig(verdi) ? verdi : merket('tallet ' + verdi)
    }
    if (erListe(verdi)) {
      const ut = []
      for (let i = 0; i < verdi.length; i++) ut.push(egenskapen(verdi, i))
      return ut
    }
    if (slag === 'object' && plattObjekt(verdi)) {
      const ut = lagUten(null)
      for (const n of nokler(verdi)) ut[n] = egenskapen(verdi, n)
      return ut
    }
    return merket(slag === 'object' ? slaget(verdi) : slag)
  }
  const egenskapen = (objekt, nokkel) => {
    const d = beskriv(objekt, nokkel)
    if (!d) return null
    if (!('value' in d)) return merket('accessor')
    return somData(d.value)
  }
  // Fri variabel: M slås opp i det globale leksikalske skopet når denne
  // KALLES, altså etter at kildens tagger har kjørt. Intrinsics over er
  // derimot bundet nå, før noen av dem slapp til. Se F15.
  const materialiser = () => {
    let katalog
    try { katalog = M } catch (e) { return tilJson({feil: 'mangler'}) }
    if (typeof katalog === 'undefined') return tilJson({feil: 'mangler'})
    if (!erListe(katalog)) return tilJson({feil: 'ikke_liste'})
    const moduler = []
    for (let i = 0; i < katalog.length; i++) {
      const post = egenskapen(katalog, i)
      if (post === null || typeof post !== 'object' || erListe(post) ||
          IKKE_DATA in post) {
        return tilJson({feil: 'ikke_post', hvor: i + 1, post})
      }
      moduler.push(post)
    }
    return tilJson({feil: null, moduler})
  }
  return {materialiser}
})()`

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
  // FØRST hjelperen, så kilden. Rekkefølgen er hele poenget i F15: intrinsics
  // må være fanget før en `const Object` i sidekoden kan skygge for dem.
  vm.runInContext(BYGG_HJELPER, ctx, {timeout: TIMEOUT})
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
      skript.runInContext(ctx, {timeout: TIMEOUT})
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
  let tekst
  try {
    tekst = vm.runInContext(`${HJELPER}.materialiser()`, ctx, {timeout: TIMEOUT})
  } catch (e) {
    stopp('kunne ikke materialisere modulkatalogen: ' + e.message + '\n' +
          'Alt som rører en kontekstverdi kjører inne i konteksten, under ' +
          'samme tidsgrense som kildens egen kode — en proxy-felle eller ' +
          'getter som aldri returnerer gir tidsavbrudd her, aldri en hengt ' +
          'port. Se `BYGG_HJELPER`.')
  }
  const svar = JSON.parse(tekst)
  if (svar.feil === 'mangler') {
    stopp('fant ingen modulkatalog `M` i sannhetskilden.\n' +
          `Skriptet stoppet før erklæringen: ${kastet ? kastet.message : 'ukjent årsak'}`)
  }
  if (svar.feil === 'ikke_liste') {
    stopp('modulkatalogen `M` i sannhetskilden er ikke en liste — katalogen ' +
          'er en liste av modulposter, og bare det')
  }
  if (svar.feil === 'ikke_post') {
    stopp(`element ${svar.hvor} i modulkatalogen er ikke en modulpost: ` +
          `${JSON.stringify(svar.post).slice(0, 60)} — katalogen er en ` +
          'liste av poster, og bare det')
  }
  return svar.moduler
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
  const moduler = katalogverdien(skriptene(html), sti)
  process.stdout.write(JSON.stringify({moduler}, null, 1) + '\n')
}

main()
