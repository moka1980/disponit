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
import util from 'node:util'
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
//
// NAVNET ER VÅRT, NØKKELROMMET ER KILDENS (Codex P2, F37). Merket er et objekt
// med denne ene nøkkelen, og kontrakten tillater nestede objekter av data — så
// en helt ordinær `flow: {__ikke_data__: 'vanlig tekst'}` ser nøyaktig ut som
// merket vårt. Derfor får en nøkkel fra KILDEN som ligger i familien (null
// eller flere understreker foran navnet) én understrek til på vei ut, og den
// nøyaktige strengen under kan bare komme fra leseren selv. Se `reservert()`.
// Nestede felt er noe porten MÅLER, ikke noe generatoren skriver videre:
// `katalog.js` bærer bare `n`, `omrade` og `fase`.
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

// EN AVVISNING FRA SIDEN ER SAMME VENTEDE KLASSE SOM ET KAST (Codex P2, F17).
//
// UI-koden etter katalogen rører `document`, og gjør den det i en `.then()`
// blir feilen en AVVISNING i stedet for et kast. Uten en lytter her er
// standardoppførselen å felle prosessen — etter at katalogen alt er skrevet
// ut. `gen_katalog.py` og porten leser exitkoden, så en helt ordinær
// `Promise.resolve().then(() => document.title = 'x')` gjorde en vellykket
// lesning til en feilet jobb.
//
// Et synkront kast fra den samme koden ignoreres allerede (se toppen av fila);
// at feilen kom en mikrooppgave senere endrer ikke hva den er. Vi TELLER dem
// og sier fra ved exit i stedet for å tie: verdien selv leser vi ikke, den kan
// være den samme fellen som i F18, og her finnes ingen kontekst å lese den i.
let avvisninger = 0
process.on('unhandledRejection', () => { avvisninger += 1 })
process.on('exit', () => {
  if (avvisninger) {
    process.stderr.write(
      `merk: ${avvisninger} ubehandlet(e) avvisning(er) fra sidens egen kode ` +
      'ble ignorert — samme ventede klasse som et kast fra UI-koden, og ' +
      'katalogen er lest uavhengig av dem\n')
  }
})

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

// Slottet en kastet kontekstverdi legges i for å kunne LESES inne i
// konteksten. Verten skriver den hit uten å røre en eneste egenskap på den.
const FEILSPOR = '__les_katalog_feil__'

/** Et uttrykk som når hjelperen uansett hva sidekoden har erklært.
 *
 *  `globalThis[…]` slår opp i det globale OBJEKTET, mens et bart navn først
 *  slås opp i det globale LEKSIKALSKE skopet — der en `const __les_katalog__`
 *  i kilden ville skygget for oss. Det er F15-lærdommen brukt på våre egne
 *  navn: leseren skal ikke kunne skygges av kilden den leser.
 *
 *  MEN `globalThis` ER SELV ET BART NAVN (Codex P2, F20). Egenskapen
 *  `globalThis` på det globale objektet er konfigurerbar, så en helt ordinær
 *  `const globalThis = …` i sidekoden er en lovlig global leksikalsk
 *  erklæring — og den skygger for oss i HVERT SENERE skript i samme kontekst.
 *  Målt: etter den erklæringen er `typeof globalThis['__les_katalog__']` lik
 *  `undefined`, mens `this['__les_katalog__']` fortsatt er hjelperen. `this`
 *  på toppnivå i et ikke-strikt skript ER det globale objektet, og `this` er
 *  et nøkkelord: ingen erklæring kan skygge for det. Derfor går veien dit. */
function kall(metode, argument = '') {
  return `this[${JSON.stringify(HJELPER)}].${metode}(${argument})`
}

// SPORENE LÅSES, SÅ KILDEN IKKE KAN BYTTE DEM UT (Codex P2, F20).
//
// Hjelperen lå i en helt ordinær skrivbar egenskap på det globale objektet, og
// en like ordinær `var __les_katalog__ = {…}` i sidekoden overskrev den. Da
// fant ikke `materialiser()` seg selv, og lesningen feilet på en side
// nettleseren leser fint. Det er ikke et angrep — navnerommet er sidens, og
// leseren låner det. Målt: `typeof globalThis['…'].materialiser` → `undefined`
// etter én `var`-erklæring.
//
// Vi kan ikke slutte å bruke det globale objektet: `M` bindes der, og alt som
// rører en kontekstverdi skal kjøre INNE i konteksten (F10-doktrinen). Vi kan
// derimot legge sporene der som egenskaper kilden ikke får bytte ut — ikke
// skrivbare, ikke konfigurerbare, ikke tellbare, definert FØR noen tagg fra
// kilden slipper til, akkurat som intrinsics i F15. Da er `var`, tilordning,
// `delete` og `defineProperty` alle uten virkning eller høylytte kast.
//
// `FEILSPOR` må verten kunne SKRIVE til, så den er skrivbar — men ikke
// konfigurerbar, og det er poenget: kunne kilden gjort den om til en accessor,
// ville `ctx[FEILSPOR] = kastet` i `feilen()` påkalt sidens setter HER UTE i
// verten, utenfor enhver tidsgrense. Samme klasse som F3/F7/F10/F18, med
// vertens ene skrivning som inngang. En vanlig dataegenskap som ikke kan bli
// noe annet lukker den.
const BYGG_HJELPER =`((glob) => {
  'use strict'
  const definer = Object.defineProperty
  const laas = (navn, verdi, skrivbar) => definer(glob, navn, {
    value: verdi, writable: skrivbar, enumerable: false, configurable: false,
  })
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
  // ...OG IKKE BARE DE MED STRENGNØKKEL (Codex P2, F30). getOwnPropertyNames
  // lukket tellbarheten, men egne nøkler i JavaScript er strenger PLUSS
  // symboler, og den gir bare de første. flow: {[Symbol('x')]: () => 42} ble
  // derfor skrevet ut som "flow": {} med exit 0: nettleseren ser et objekt som
  // bærer en funksjon, leseren meldte et tomt og lesbart objekt. Det er ikke
  // en ny form på F16 — det er den andre halvdelen av samme oppslag, og med
  // begge er nøkkelrommet uttømt per definisjon.
  const symboler = Object.getOwnPropertySymbols
  const erListe = Array.isArray
  const lagUten = Object.create
  const endelig = Number.isFinite
  const tilJson = JSON.stringify
  const tilTekst = String
  const settProto = Object.setPrototypeOf
  // INGENTING VI BYGGER SKAL ARVE FRA SIDEN (Codex P2, F21).
  //
  // JSON.stringify er fanget som intrinsic, men den SLÅR OPP toJSON paa hver
  // verdi den moeter — og et objektliteral arver fra Object.prototype, en
  // liste fra Array.prototype. Begge er sidens objekter. En helt ordinaer
  // Object.prototype.toJSON = ... omskrev derfor den ferdig materialiserte
  // katalogen paa vei ut, stille og med exit 0: maalt ga den {} for hele
  // svaret, Array.prototype.toJSON ga "moduler": "borte", og Codex' eget
  // eksempel flyttet en modul fra fase 1 til fase 4 uten et ord. Det er
  // nøyaktig den stille uenigheten med nettleseren hele lesersteget finnes
  // for aa fjerne.
  //
  // Objekter bygges alt med null-prototype (lagUten). Lister kan ikke lages
  // uten prototype, men de kan LOESNES fra sin naar de er ferdige: en liste
  // med null-prototype er fortsatt en liste (Array.isArray og JSON.stringify
  // ser paa den interne merkelappen, ikke paa arven), men den arver ingen
  // krok. Av samme grunn fylles de med indekstilordning og ikke med .push:
  // push er ogsaa Array.prototype, og en overskrevet push ga maalt en tom
  // katalog med exit 0.
  const uarvet = (liste) => settProto(liste, null)
  // ARVEDE FELT ER OGSAA FELT (Codex P2, F31). Proeven var «prototypen har
  // selv ingen prototype», og den holder for de to formene katalogen bruker:
  // et objektliteral (Object.prototype) og et null-prototype-objekt. Men den
  // holder for en tredje ogsaa — et objekt bygget OVER et null-prototype-
  // objekt. Object.create(Object.create(null, {status:...}), ...) slapp
  // gjennom som en platt post, og deretter leses bare EGNE noekler: status
  // forsvant ut av JSON-en med exit 0 mens nettleseren ser post.status.
  // Proeven staar naa paa IDENTITET mot den fangede intrinsicen i stedet for
  // paa en form: null eller Object.prototype, og ingenting annet. Alt annet
  // er en arv vi ikke leser, og da sier vi hva vi saa (se slaget) i stedet
  // for aa tie om den.
  const objektProto = Object.prototype
  const listeProto = Array.prototype
  // ...OG EN PROTOTYPE ER OGSAA EN BEHOLDER (Codex P2, F36). F31 satte proeven
  // paa IDENTITET mot intrinsicen, og den holder saa lenge intrinsicen er den
  // vi fanget. Men Object.prototype er MUTERBAR: en helt ordinaer
  // Object.prototype.status = 'planlagt' foran katalogen gir HVER
  // objektliteral et arvet felt — nettleseren ser M[0].status — mens
  // identitetsproeven fortsatt sier ja og oppslaget etterpaa bare leser EGNE
  // noekler. Maalt: exit 0, uten et ord om status. Samme inngang har
  // Array.prototype for listene.
  //
  // Vi kan ikke lese arven som felt (da ville toString og hasOwnProperty blitt
  // katalogdata), og vi kan ikke vite hvilke navn en side eier. Det vi KAN, er
  // aa vite om prototypen er den samme som da konteksten var frisk: noeklene
  // paa den fanges her, foer noen tagg fra kilden har sluppet til (F15), og
  // sammenlignes naar katalogen materialiseres. Er den endret, er hver post i
  // katalogen en post vi ikke kan lese — og det sier vi, i stedet for aa tie
  // om et felt nettleseren ser.
  const signaturen = (o) => {
    let ut = ''
    const n = nokler(o)
    for (let i = 0; i < n.length; i++) ut += n[i] + '\\u0000'
    const s = symboler(o)
    for (let i = 0; i < s.length; i++) ut += tilTekst(s[i]) + '\\u0000'
    return ut
  }
  const protoFoer = signaturen(objektProto)
  const listeProtoFoer = signaturen(listeProto)
  const plattObjekt = (verdi) => {
    const over = proto(verdi)
    return over === null || over === objektProto
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
  // MARKOERNAVNET ER VAART, NOEKKELROMMET ER KILDENS (Codex P2, F37).
  //
  // Markoeren er et objekt med EN noekkel, og kontrakten tillater nestede
  // objekter av data. En helt ordinaer flow: {__ikke_data__: 'vanlig tekst'}
  // er derfor gyldig katalogdata som ser NOEYAKTIG ut som merket vaart, og
  // generatoren avviste den som en funksjon eller et moenster. Kilden eier
  // noekkelrommet sitt; vi kan ikke reservere et navn i det.
  //
  // Vi kan derimot skille de to fra hverandre paa vei ut: en noekkel fra
  // KILDEN som ligger i familien (null eller flere understreker foran
  // __ikke_data__) faar en understrek til. Avbildningen er injektiv — den
  // flytter familien ett hakk opp og roerer ingenting utenfor — saa den
  // noeyaktige strengen __ikke_data__ kan bare komme fra merket().
  const reservert = (n) => {
    const m = n.length, k = IKKE_DATA.length
    if (m < k) return false
    for (let i = 0; i < k; i++) if (n[m - k + i] !== IKKE_DATA[i]) return false
    for (let i = 0; i < m - k; i++) if (n[i] !== '_') return false
    return true
  }
  // BEHOLDERNE VI ROERTE, SAA VERTEN KAN SI HVA DE VAR (Codex P2, F39).
  //
  // F10 flyttet all beroering av en kontekstverdi INN i konteksten, og det
  // lukket proxy-fellen som HENGER: en felle som aldri returnerer felles av
  // tidsgrensen. Den som RETURNERER staar igjen. En Proxy med en get-felle som
  // gir 4 for p leses her som deskriptorene UNDER den — fase 1 — mens
  // nettleseren leser 4 av det samme objektet. Det er stille uenighet med
  // nettleseren, altsaa nettopp det lesersteget finnes for aa fjerne.
  //
  // Ingen kode inne i konteksten kan spoerre om noe ER en Proxy: hver
  // operasjon som naar den, naar fella foerst. Verten kan — util.types.isProxy
  // er en maskinnaer proeve som ikke paakaller en eneste felle. Verdiene faar
  // derfor ligge i en liste vi selv bygger, og verten spoer lista etterpaa.
  // Ingenting fra verten kommer inn hit; det er bare listen som gaar ut.
  const sett = []
  const steder = []
  let hvorNaa = 0
  const somData = (verdi) => {
    if (verdi === null) return null
    const slag = typeof verdi
    if (slag === 'string' || slag === 'boolean') return verdi
    if (slag === 'number') {
      return endelig(verdi) ? verdi : merket('tallet ' + verdi)
    }
    // FOER enhver operasjon som kan naa en felle. Se over.
    if (slag === 'object') {
      sett[sett.length] = verdi
      steder[steder.length] = hvorNaa
    }
    // En symbolnøkkel kan ikke bæres av JSON, så beholderen er ikke data —
    // uansett hva som står under den. Vi sier hva vi så. Se symboler over.
    if (slag === 'object' && symboler(verdi).length) {
      return merket('symbolnokkel')
    }
    if (erListe(verdi)) {
      const ut = []
      for (let i = 0; i < verdi.length; i++) ut[i] = egenskapen(verdi, i)
      return uarvet(ut)
    }
    if (slag === 'object' && plattObjekt(verdi)) {
      const ut = lagUten(null)
      // Indeksloekke, ikke for-of: iteratoren er en egenskap paa sidens
      // Array.prototype, og lesningen skal ikke gaa gjennom den. Se F21/F36.
      const n = nokler(verdi)
      for (let i = 0; i < n.length; i++) {
        ut[reservert(n[i]) ? '_' + n[i] : n[i]] = egenskapen(verdi, n[i])
      }
      return ut
    }
    return merket(slag === 'object' ? slaget(verdi) : slag)
  }
  // ET HULL ER IKKE EN VERDI (Codex P2, F26). Ingen deskriptor betyr at
  // egenskapen ikke finnes. For en post kan det ikke skje — noklene kommer
  // fra getOwnPropertyNames — men en liste kan vaere GLISSEN: flow: [, 'steg']
  // har ingen plass 0. Nettleseren ser et fravaer der (map, forEach og
  // JSON.stringify skiller hull fra verdi), og null er en verdi: den ble lest
  // videre som helt ordinaer data, mens en eksplisitt undefined avvises rett
  // ved siden av. Vi sier hva vi saa i stedet for aa gjette en verdi inn.
  const egenskapen = (objekt, nokkel) => {
    const d = beskriv(objekt, nokkel)
    if (!d) return merket('hull')
    if (!('value' in d)) return merket('accessor')
    return somData(d.value)
  }
  // Svarhylsteret er ogsaa en beholder, og et objektliteral arver fra
  // Object.prototype. Se F21: det er hylsteret Codex' eget eksempel kapret.
  const utfall = (feil) => {
    const ut = lagUten(null)
    ut.feil = feil
    return ut
  }
  // Fri variabel: M slås opp i det globale leksikalske skopet når denne
  // KALLES, altså etter at kildens tagger har kjørt. Intrinsics over er
  // derimot bundet nå, før noen av dem slapp til. Se F15.
  const materialiser = () => {
    // Prototypene FOERST: er en av dem endret, arver hver eneste beholder i
    // katalogen felt vi ikke leser, og da er ingen post lesbar. Se F36.
    // Indeksloekker, ikke for-of: iteratoren er selv en egenskap paa
    // Array.prototype, altsaa det vi holder paa aa proeve.
    const proever = [[objektProto, protoFoer, 'Object.prototype'],
                     [listeProto, listeProtoFoer, 'Array.prototype']]
    for (let i = 0; i < proever.length; i++) {
      const naa = signaturen(proever[i][0])
      if (naa !== proever[i][1]) {
        const svar = utfall('proto_endret')
        svar.hvem = proever[i][2]
        svar.foer = proever[i][1]
        svar.naa = naa
        return tilJson(svar)
      }
    }
    let katalog
    try { katalog = M } catch (e) { return tilJson(utfall('mangler')) }
    if (typeof katalog === 'undefined') return tilJson(utfall('mangler'))
    // Katalogen selv er ogsaa en beholder: Array.isArray ser gjennom en Proxy
    // til lista under, saa M kan vaere en. Se F39.
    if (typeof katalog === 'object') {
      sett[sett.length] = katalog
      steder[steder.length] = 0
    }
    if (!erListe(katalog)) return tilJson(utfall('ikke_liste'))
    const moduler = []
    for (let i = 0; i < katalog.length; i++) {
      hvorNaa = i + 1
      const post = egenskapen(katalog, i)
      if (post === null || typeof post !== 'object' || erListe(post) ||
          IKKE_DATA in post) {
        const svar = utfall('ikke_post')
        svar.hvor = i + 1
        svar.post = post
        return tilJson(svar)
      }
      moduler[i] = post
    }
    const svar = utfall(null)
    svar.moduler = uarvet(moduler)
    return tilJson(svar)
  }
  // EN KASTET VERDI LESES OGSÅ HER INNE (Codex P2, F18). navn og message kan
  // være accessorer siden eier — samme klasse som F3/F7/F10, med feilstien som
  // inngang. Leses de i verten, står de utenfor enhver tidsgrense; her felles
  // de av den samme. Et primitivt kast har ingen av delene og gjengis som seg
  // selv. Det som ikke lar seg lese blir tomt, aldri gjettet.
  const feiltekst = (kastet) => {
    const svar = lagUten(null)
    svar.navn = ''
    svar.melding = ''
    try {
      const slag = typeof kastet
      if (kastet === null || (slag !== 'object' && slag !== 'function')) {
        svar.melding = slag === 'string' ? kastet : tilTekst(kastet)
      } else {
        const n = kastet.name
        const m = kastet.message
        if (typeof n === 'string') svar.navn = n
        if (typeof m === 'string') svar.melding = m
      }
    } catch (indre) {
      svar.melding = ''
    }
    return tilJson(svar)
  }
  // Selve hjelperobjektet har null-prototype: oppslaget av materialiser skal
  // ikke kunne treffe en prototype kilden eier. Se ogsaa F21.
  const api = lagUten(null)
  api.materialiser = materialiser
  api.feiltekst = feiltekst
  // Listene over beholderne materialiseringen roerte, og hvor de sto. Verten
  // leser dem med util.types.isProxy — se F39.
  api.beholderne = () => sett
  api.stedene = () => steder
  laas(${JSON.stringify(HJELPER)}, api, false)
  laas(${JSON.stringify(FEILSPOR)}, undefined, true)
})(this)`

// KJØREVALGENE FOR SIDENS EGEN KODE (Codex P2, F18).
//
// `displayErrors: false` er ikke kosmetikk. Med standardverdien LESER Node
// `.stack` på verdien som er kastet, for å pynte sporet med kildelinjen — og
// den lesningen skjer på vei UT av `runInContext`, etter at tidsvakten er
// koblet fra. `throw new Proxy({}, {get(){for(;;){}}})` hang derfor leseren
// og CI før noen av våre linjer hadde sett verdien i det hele tatt; det var
// ikke `e.name` som hang først, det var Node som pyntet. Uten pyntingen
// krysser verdien ut på null millisekunder, og alt som skal LESES av den
// leses etterpå inne i konteksten, under tidsgrensen. Se `feilen()`.
//
// Vi mister ingenting: meldingene våre bruker `message`, aldri `stack`.
const IKKE_PYNT = {displayErrors: false}

// ÉN FRIST FOR FASEN, IKKE ÉN PER TAGG (Codex P2, F43).
//
// `timeout` gjelder ETT `runInContext`-kall, og lesningen gjør mange: ett per
// <script>, ett per feillesning, ett for materialiseringen. Hvert kall fikk
// hele grensen på nytt, så en side kunne kjøpe seg vilkårlig mye tid ved å
// legge til tagger: åtte evige `<script>` med grensen på 100 ms brukte 846 ms
// og gikk ut med exit 0, og ved produksjonsverdien holder hundre slike CI
// opptatt i sytten minutter. Grensen lovet en øvre kjøretid og ga en per kall.
//
// Fristen er nå felles, og de to fasene har hver sin: sidens egne tagger med
// feillesningene sine, og deretter materialiseringen med sin. Fasene er skilt
// fordi de er hver sin lesning — en side som brukte opp taggenes tid skal
// fortsatt få katalogen sin materialisert, slik nettleseren også ville beholdt
// en ferdig bundet `const M` (F17, F18) — men INNENFOR hver fase deles
// budsjettet, og da er hele lesningen bundet av en konstant, ikke av hvor mange
// tagger eller hvor mange feller siden inneholder.
let frist = Infinity

/** Starter en fase: fra nå og TIMEOUT millisekunder fram. */
function nyFrist() { frist = Date.now() + TIMEOUT }

/** Kjørevalgene for sidens egen kode: det som er igjen av fasens frist.
 *
 *  Aldri under 1 ms, for `timeout: 0` er «ingen grense» i `vm` — nøyaktig det
 *  motsatte av det et oppbrukt budsjett betyr. */
function kjor() {
  const igjen = frist - Date.now()
  return {timeout: igjen > 1 ? igjen : 1, ...IKKE_PYNT}
}

/** `{navn, melding}` for noe konteksten kastet — lest INNE i konteksten.
 *
 *  F3 var en getter, F7 en merkelapp-getter, F10 en proxy-felle, og alle tre
 *  ble lukket av den samme regelen: det som rører en kontekstverdi kjører i
 *  konteksten, under tidsgrensen. Feilstien var stien regelen ikke hadde fått
 *  ennå. `e.name` og `e.message` er helt ordinære egenskapsoppslag, og siden
 *  kan eie begge som accessorer — lest her ute sto de utenfor enhver grense.
 *
 *  Verdien legges i `FEILSPOR` uten at verten rører en egenskap på den, og
 *  hjelperen leser den der inne. Blir lesningen selv felt av tidsgrensen, sier
 *  vi nettopp det: vi leser ingen egenskap på feilen VI fikk heller, for den
 *  kunne vært den samme fellen. */
function feilen(ctx, kastet) {
  ctx[FEILSPOR] = kastet
  try {
    return JSON.parse(vm.runInContext(
      kall('feiltekst', `this[${JSON.stringify(FEILSPOR)}]`), ctx, kjor()))
  } catch (e) {
    return {navn: '', melding: 'feilen kunne ikke leses innen tidsgrensen'}
  }
}

/** Stopper hvis noen beholder materialiseringen rørte er en Proxy (Codex P2,
 *  F39).
 *
 *  F10 flyttet all berøring av en kontekstverdi INN i konteksten, og det lukket
 *  proxy-fellen som HENGER: en felle som aldri returnerer felles av
 *  tidsgrensen. Den som RETURNERER sto igjen. En Proxy over
 *  `{n:1,name:'En',area:'X',p:1}` med en `get`-felle som gir 4 for `p` leses av
 *  deskriptorene UNDER den — fase 1 — mens nettleseren leser `M[0].p` som 4.
 *  Ingen felle hang, ingen kastet; katalogen ble bare en annen enn sidens.
 *
 *  Ingen kode inne i konteksten kan spørre om noe ER en Proxy: hver operasjon
 *  som når verdien, når fellen først — det er hele poenget med en Proxy.
 *  `util.types.isProxy` er derimot en maskinnær prøve i verten som ikke
 *  påkaller en eneste felle, heller ikke på en verdi fra en annen kontekst.
 *
 *  Doktrinen fra F10 står: ingenting leses AV verdiene her ute. Lista er vår
 *  egen, bygget inne i konteksten (`sett`), og her ute røres bare `length` og
 *  indeksene på den — egne dataegenskaper på en helt ordinær liste. Ingen
 *  vertsfunksjon slippes inn i konteksten; konteksten er fortsatt uten vei ut. */
function proxyfri(ctx) {
  const rørte = vm.runInContext(kall('beholderne'), ctx, kjor())
  const steder = vm.runInContext(kall('stedene'), ctx, kjor())
  for (let i = 0; i < rørte.length; i++) {
    if (!util.types.isProxy(rørte[i])) continue
    const hvor = steder[i]
    stopp('modulkatalogen i sannhetskilden bærer en Proxy ' +
          (hvor ? `i element ${hvor}` : 'som selve katalogen') + '.\n' +
          'En Proxy er ikke data: den svarer på hvert oppslag med kode, og ' +
          'det den svarer nettleseren kan være noe annet enn det den svarer ' +
          'en lesning av egenskapene under den. Katalogen er dataliteraler, ' +
          'og en verdi som først blir til når noen spør, kan ingen port måle.')
  }
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
  // `microtaskMode: 'afterEvaluate'` gir konteksten SIN EGEN mikrooppgavekø,
  // og tømmer den mens `runInContext` fortsatt kjører (Codex P2, F17). Uten
  // den kjørte alt siden la i køen ETTER at kjøringen var over — altså utenfor
  // tidsgrensen, på nøyaktig samme måte som materialiseringen gjorde før F10.
  // En rekursiv `Promise.resolve().then(g)`-kjede hang da leseren for godt.
  // Nå er køen en del av kjøringen den kom fra, og tidsgrensen feller den.
  const ctx = vm.createContext(Object.create(null),
                               {microtaskMode: 'afterEvaluate'})
  // FØRST hjelperen, så kilden. Rekkefølgen er hele poenget i F15: intrinsics
  // må være fanget før en `const Object` i sidekoden kan skygge for dem.
  vm.runInContext(BYGG_HJELPER, ctx, {timeout: TIMEOUT})
  let kastet = null
  // Sidens tagger er ÉN fase og deler én frist (F43).
  nyFrist()
  deler.forEach((kode, i) => {
    const navn = deler.length > 1 ? `${kilde} <script> ${i + 1}` : kilde
    if (Date.now() >= frist) {
      stopp(`sannhetskilden brukte opp tidsgrensen (${TIMEOUT} ms) før ` +
            `${navn} kunne kjøres.\n` +
            'Tidsgrensen gjelder siden, ikke den enkelte taggen: en side som ' +
            'ikke blir ferdig innen den, skal si fra høylytt — ikke kjøpe seg ' +
            'ny tid for hver <script> den legger til.')
    }
    let skript
    try {
      skript = new vm.Script(kode, {filename: navn})
    } catch (e) {
      stopp(`${navn} er ikke gyldig JavaScript: ${e.message}\n` +
            'To `const M = [ … ]` er nettopp denne feilen — en redeklarasjon ' +
            'nettleseren selv avviser.')
    }
    try {
      skript.runInContext(ctx, kjor())
    } catch (e) {
      // Feilobjektet kommer fra en annen realm, så `instanceof` biter ikke —
      // og egenskapene på det leses inne i konteksten, ikke her. Se `feilen()`.
      const info = feilen(ctx, e)
      if (info.navn === 'SyntaxError') {
        stopp(`${navn} er ikke gyldig JavaScript: ${info.melding}\n` +
              'To `const M = [ … ]` i hver sin <script> er nettopp denne ' +
              'feilen — den andre erklæringen avvises når taggen bindes.')
      }
      // Ventet: UI-koden etter katalogen rører `document`. Se toppen av fila.
      if (!kastet) kastet = info
    }
  })
  let tekst
  // Materialiseringen er den andre fasen, og får sin egen frist (F43).
  nyFrist()
  try {
    tekst = vm.runInContext(kall('materialiser'), ctx, kjor())
  } catch (e) {
    stopp('kunne ikke materialisere modulkatalogen: ' + feilen(ctx, e).melding +
          '\n' +
          'Alt som rører en kontekstverdi kjører inne i konteksten, under ' +
          'samme tidsgrense som kildens egen kode — en proxy-felle eller ' +
          'getter som aldri returnerer gir tidsavbrudd her, aldri en hengt ' +
          'port. Se `BYGG_HJELPER`.')
  }
  proxyfri(ctx)
  const svar = JSON.parse(tekst)
  if (svar.feil === 'proto_endret') {
    // Signaturene er NUL-skilte navnelister, laget inne i konteksten. Her ute
    // er de to helt ordinære strenger, og forskjellen på dem er hva sidens
    // kode la til eller tok bort. Se F36.
    const før = new Set(svar.foer.split('\u0000'))
    const nå = svar.naa.split('\u0000').filter(n => n && !før.has(n))
    stopp(`sidens egen kode har endret ${svar.hvem} i sannhetskilden` +
          (nå.length ? `: ${nå.join(', ')}` : '') + '.\n' +
          'Alt som arver derfra bærer da et felt nettleseren ser og leseren ' +
          'ikke leser: en `Object.prototype.status = …` gir HVER modulpost i ' +
          'katalogen den aksen, og lesningen ville tiet om den. Katalogen er ' +
          'dataliteraler, og prototypen deres skal være den nettleseren ' +
          'starter med.')
  }
  if (svar.feil === 'mangler') {
    stopp('fant ingen modulkatalog `M` i sannhetskilden.\n' +
          'Skriptet stoppet før erklæringen: ' +
          `${kastet && kastet.melding ? kastet.melding : 'ukjent årsak'}`)
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
