"""Blinding (klarsignalet §6): maskering FØR modellsteget, målt på
faktisk input; avskruing er en AUDITERT handling, aldri en stille
parameter.

Løftet er PRESIST, og ordlyden er valgt for å ikke lese sterkere enn
porten måler: modulen maskerer **de navngitte feltene i sin kanoniske
form** — «navn, kjønn, alder, adresse, bilde og kontaktfelt» slik de
står i de STRUKTURERTE verdiene. Den påstår ikke anonymisering ut over
dem, og den påstår ikke å finne personalia som bare finnes i fritekst.
En invariant som hviler på et SØK i fritekst kan ikke være absolutt:
fire runder har målt lekkasje ut (delstrenger, versaler, NFC/NFD) og
korrupsjon inn fra samme grense. Den absolutte formen er strukturell
blinding — personfeltene finnes ikke i inputen i det hele tatt — og den
er eiers ratifiserte mål (B-veien, #158); til den lander er ordlyden over
det ærlige løftet.

Avskruingen (port 16b) er en ANNEN maskin og står utsatt i #159:
`auditrad` er i dag kallerens egen påstand om at handlingen er auditert.

Av-maskeringstabellen er payload i `kandidat_avmaskering` (057) og
reapes med resten.
"""
from __future__ import annotations

import re
from bisect import bisect_right

#: Katalogens løfte, ordrett — settet er LUKKET og rekkefølgen stabil
#: (tokennummereringen skal være deterministisk for samme input).
MASKERTE_FELTER: tuple[str, ...] = (
    "navn", "kjonn", "alder", "adresse", "bilde", "kontakt")

#: Grensene for en deklarert personverdi: bundet lengde og antall — en
#: deklarasjon er korte kanoniske verdier, aldri fritekst.
#:
#: De BOR her, ikke i `parsing` (Cursor P2), sammen med predikatet som
#: bruker dem (`feltverdier_lukket`).
MAKS_FELTVERDIER = 10
MAKS_FELTVERDI_TEGN = 200


class Blindingsfeil(Exception):
    def __init__(self, kode: str):
        self.kode = kode
        super().__init__(kode)


def verdiform_lukket(verdi: str) -> bool:
    """Er personverdien SIN EGEN skrivemåte? (Cursor P1)

    STRUKTURELL grense, og bare det: verdien er lik seg selv strippet.
    Den måler at deklarasjonen ikke bærer en hale den selv ikke mener —
    `"Kari Testdal "` erstatter «Kari Testdal » og lar «Kari Testdal,»
    stå, altså KORRUPT input der den treffer og ingenting der den ikke
    gjør det. Samme klasse som `kandidat_id` før ASCII-kanonen: en port
    som MÅLER `strip()` men LAGRER råverdien, måler noe annet enn det
    den lagrer. Vi avviser, vi kanoniserer ikke: en deklarasjon som
    mente `Kari` sier `Kari`.

    TEGNLISTA ER BORTE (eierdom, K2-kjennelse runde 5 på #217, valg B).
    Predikatet bannlyste før `Cc`/`Cf` (`U+200B`, RTL-markørene,
    kontrolltegn) — en ENUMERASJON over Unicode-kategorier, og den var
    per konstruksjon ufullstendig: `U+00A0` (Zs), `U+2010` (Pd) og en
    NFD-dekomponert `å` er verken Cc eller Cf, og hver av dem gir samme
    vakuum. Fem runder målte fem nye tegn; en sjette lå ferdig på bordet
    før den femte var skrevet. Klassen lukkes ikke ved å telle tegn, men
    ved å måle EFFEKTEN — se `blind`s vakuøsitetsport."""
    return verdi == verdi.strip()


def feltverdier_lukket(verdier: object) -> bool:
    """HELE grensesettet for ett felts deklarerte verdier, ETT sted —
    type, tomhet, antall, lengde og verdiens egen skrivemåte.

    EIERDOM, K2-kjennelse runde 4 på #217 (valg A). Grensene fantes før
    som to HÅNDSKREVNE opptellinger over samme sett: `les_manifest` talte
    sine i én løkke, `blind`s formløkke sine i en annen, og ingen av dem
    var avledet av den andre. Da må hver ny grense skrives to steder, og
    fire Cursor-runder på rad fant nøyaktig én grense som sto på den ene
    døra og manglet på den andre:

        1. padding / Cf-Cc          (P1, `7b8fa66`)
        2. lengde / antall          (P2, `4568cf09`)
        3. ukjent feltnavn          (P2, `be6fdd32` — negativen manglet)
        4. tom liste / tom streng   (P2, `f6887ce`)

    Runde 4 var den vonde: `all([])` er `True` og `verdiform_lukket("")`
    er `True`, så `{"navn": []}` og `{"navn": ["Kari", ""]}` passerte
    `blind` mens deklarasjonsdøra felte dem — og en tom avmaskerings-
    tabell gjør `krev_blindet` VAKUØS: kjøringen telles som blindet mens
    klartekstnavnet står i modellinputen. Enumerasjonen er endelig, men
    ikke tom, og et femte formforsøk er nettopp det §9 forbyr.

    Så: ETT predikat, to kallesteder. `les_manifest` kaller denne i
    stedet for sin egen løkke og beholder sin egen feilkode
    (`manifest_feilformet` mot `ugyldig_maskeringsform`) — kodene skiller
    hvilken DØR som felte, aldri hvilken grense som gjaldt.

    RETTELSE (runde 5). Denne docstringen lovet at «en femte grense
    skrives nå ett sted, og runde 5 på denne aksen er strukturelt
    umulig». Første halvdel stemmer; andre halvdel gjorde ikke det.
    Divergens var aldri det som slapp NBSP inn — UFULLSTENDIGHET var
    det, og en ufullstendig enumerasjon lever like godt i ett predikat
    som i to. Runde 5 kom, og den kom på `verdiform_lukket`s
    tegnkategorier. Det som lukker dén aksen er ikke en grense til her
    inne, men målingen i `blind`: en deklarasjon som ikke traff noe er
    vakuøs, uansett hvilket tegn som gjorde at den bommet.

    En sekvens: `list` eller `tuple`, aldri et `set` (tokennummereringen
    skal være deterministisk for samme input) og aldri en bar streng (den
    er iterbar, så «Ann» ville blitt tegnene `A`, `n`, `n` og maskert
    hver eneste `A` og `n` i søknaden)."""
    return (isinstance(verdier, (list, tuple)) and bool(verdier)
            and len(verdier) <= MAKS_FELTVERDIER
            and all(isinstance(verdi, str) and bool(verdi)
                    and len(verdi) <= MAKS_FELTVERDI_TEGN
                    and verdiform_lukket(verdi)
                    for verdi in verdier))


def _monster(verdi: str) -> re.Pattern[str]:
    """Personverdien som mønster — VERSALUFØLSOMT (Codex P1).

    De strukturerte feltene og dokumentet er to kilder som sjelden er
    enige om store bokstaver: metadata sier `Kari`, CV-en skriver `KARI`
    i overskriften. `str.replace` og `in` er begge versalfølsomme, så
    navnet gikk umaskert til modellen — og `krev_blindet` lette etter
    nøyaktig samme skrivemåte og fant den ikke, så porten sa god for
    lekkasjen. Samme mønster brukes derfor BEGGE steder: det som
    maskeres og det som måles er per definisjon den samme testen.

    Løftet er versalene, ikke normaliseringsformer: `re.IGNORECASE`
    gjør Unicodes enkle case-folding, og en NFC/NFD-forskjell i kilden
    er fortsatt en forskjell."""
    return re.compile(re.escape(verdi), re.IGNORECASE)


#: Skjøten mellom kandidatens dokumenter. ÉN kilde (#174): kjøringen
#: setter dem sammen med denne, og `dokumentgrenser` regner spennene ut
#: fra den samme. Drifter de to, peker grensene feil sted — og da er
#: kryss-sitatporten en port som måler noe annet enn den tror.
SKJOT = "\n\n"


def dokumentgrenser(blindede: list[str]) -> list[tuple[int, int]]:
    """[start, slutt) for hvert dokument i `SKJOT.join(blindede)`."""
    grenser: list[tuple[int, int]] = []
    pos = 0
    for i, d in enumerate(blindede):
        if i:
            pos += len(SKJOT)
        grenser.append((pos, pos + len(d)))
        pos += len(d)
    return grenser


def bygg_tabell(kandidatfelter: dict[str, list[str]]
                ) -> tuple[list[tuple[str, str, str]], dict[str, str]]:
    """-> (tokenpar, avmaskeringstabell). ÉN tabell per KANDIDAT (#174).

    DELT OVER DOKUMENTENE, og det er grunnen til at tabellen bygges her
    og ikke per dokument: `[NAVN-1]` skal bety den samme personen i
    søknadsbrevet og i vitnemålet. Bygde vi én tabell per dokument,
    ville nummereringen startet på nytt, og modellen sett to personer
    der det er én.

    Formportene bor her fordi de gjelder DEKLARASJONEN, ikke teksten.
    Effektporten hører til dokumentene og måles av `_traff_alle`.
    """
    # 5): et ukjent felt kan per konstruksjon aldri treffe, siden løkka
    # under bare rører `MASKERTE_FELTER` — så vakuøsitetsporten nederst
    # feller det samme kartet uansett. Forskjellen er hva utfallet SIER:
    # «feltet er utenfor det lukkede settet», ikke «deklarasjonen traff
    # ingenting». Driften som skal rette buntsiden trenger den.
    ukjente = set(kandidatfelter) - set(MASKERTE_FELTER)
    if ukjente:
        raise Blindingsfeil("ukjent_maskeringsfelt")
    # FORMEN MÅLES, den tas ikke på ord (Codex P2). Typeannotasjonen sier
    # `{felt: [verdier]}`, men uttrekket er en FREMMED produsent — en
    # modell eller en parser — og en annotasjon er ingen port. `blind`
    # tar dessuten imot felter fra en INJISERT `kandidatfelter_for`, og
    # den veien går utenom manifestporten helt.
    #
    # Grensesettet er `feltverdier_lukket` — DEN SAMME `les_manifest`
    # kaller (eierdom, K2-kjennelse runde 4 på #217). Dette er ikke et
    # femte maskeringsforsøk (#158) — mønsteret og porten er uendret —
    # det er inndatasiden av samme fail-closed regel som
    # `blinding_uten_felter`: et umålt utfall er et avvist utfall (SP-3).
    for verdier in kandidatfelter.values():
        if not feltverdier_lukket(verdier):
            raise Blindingsfeil("ugyldig_maskeringsform")
    avmaskering: dict[str, str] = {}
    par: list[tuple[str, str, str]] = []
    for felt in MASKERTE_FELTER:
        for nr, verdi in enumerate(kandidatfelter.get(felt, ()), start=1):
            token = f"[{felt.upper()}-{nr}]"
            avmaskering[token] = verdi
            par.append((felt, token, verdi))
    return par, avmaskering


def _traff_alle(kandidatfelter: dict[str, list[str]],
                dokumenter: list[str]) -> bool:
    """Traff HVERT deklarert felt minst ETT av dokumentene? (#174)

    Effektmålingen løftet til KANDIDATNIVÅ. Med blinding per dokument er
    «traff dokumentet» feil spørsmål: et navn står gjerne i søknadsbrevet
    og ikke i vitnemålet, og å kreve treff i HVERT dokument ville felt en
    helt normal bunt. Fortegnet er uendret — begrunnelsen under er
    runde 5/6-dommenes, ordrett.
    """
    # VAKUØSITETEN MÅLES PÅ EFFEKT, PER FELT (eierdom, K2-kjennelse
    # runde 5 på #217, valg B) — OG MÅLINGEN SKJER PÅ ORIGINALTEKSTEN,
    # FØR NOEN ERSTATNING (eierdom, K2-kjennelse runde 6, valg A).
    #
    # Et deklarert felt der INGEN av verdiene traff dokumentteksten er en
    # vakuøs deklarasjon: den fyller avmaskeringstabellen med noe
    # `krev_blindet` aldri kan finne, så porten løper sine runder uten å
    # måle noe — og kjøringen telles som blindet mens klartekstnavnet står
    # i modellinputen. Det er samme lekkasje som `"Kari Testdal "` i
    # `7b8fa66`, bare med et annet tegn hver runde: NBSP (`Zs`), `U+2010`
    # (`Pd`), en NFD-dekomponert `å`.
    #
    # Dette er ikke en sjette tegnliste — det er målingen som gjør
    # tegnlister overflødige: vi trenger ikke vite HVILKET tegn som
    # gjorde at deklarasjonen bommet, bare at den bommet.
    #
    # PER FELT, IKKE PER VERDI, og fortegnet er hele grunnen: en enkelt
    # VERDI uten treff er lovlig så lenge en søsterverdi i samme felt
    # traff. Ellers blir defensive varianter (`["Kari Testdal", "Kari"]`)
    # selvmotsigende farlige, og deklarasjonen presses mot FÆRRE
    # varianter — feil fortegn for personvern.
    #
    # PÅ ORIGINALTEKSTEN, og det er runde-5-dommens EGEN semantikk
    # («traff deklarasjonen DOKUMENTET»): å telle treff med `subn` inne i
    # erstatningsløkka målte mot en tekst maskeringen selv nettopp hadde
    # skrevet i, altså mot sine egne tokener. Målt (Codex P1, review
    # 19:38 på `13e7110`):
    #
    #     tekst  = "Ａｌ is forty-two"        # fullbredde Ａｌ
    #     felter = {"navn": ["Al"], "alder": ["forty-two"]}
    #
    # `forty-two` erstattes først (lengste først), og `Al` traff så `AL`
    # inni `[ALDER-1]` — `_monster` er `re.IGNORECASE`. `traff["navn"]`
    # ble sann uten at navnet noen gang traff dokumentet, porten sa god,
    # og fullbredde-navnet gikk i klartekst til modellen mens kjøringen
    # telte som blindet. Søket her skjer FØR løkka, mot teksten slik den
    # kom inn: da finnes kollisjonen ikke i målingen, for tokenene er
    # ikke skrevet ennå.
    #
    # RESTKLASSEN, ærlig: en forekomst i teksten som ingen deklarert
    # verdi matcher mens en ANNEN verdi i samme felt traff, er
    # udetekterbar uten NER. Den står i `KONTRAKT.md` som kjent grense
    # eid av #158 (strukturell blinding), ikke som noe denne porten
    # lover.
    #
    # UTSATT, K1 → #158 (disjunkt tokenalfabet). Målingen over lukker
    # PORT-omgåelsen, ikke tokenkollisjonen som sådan: erstatningen kan
    # fortsatt skrive inn i et token den selv har lagt igjen
    # (`[[NAVN-1]DER-1]`), og da er avmaskeringstabellen ikke lenger
    # reversibel. Utfallet er korrupt modellinput, ikke klartekst ut —
    # `krev_blindet` søker fortsatt hele inputen.
    #   dom-klasse: tokenkollisjon-korrupsjon · felt i #217 ·
    #   https://github.com/moka1980/disponit/pull/217#issuecomment-5430381316
    return all(
        any(_monster(verdi).search(dok)
            for verdi in verdier for dok in dokumenter)
        for felt, verdier in kandidatfelter.items())


def anvend(par: list[tuple[str, str, str]], tekst: str) -> str:
    """Anvender tokentabellen på ÉN tekst."""
    # LENGSTE VERDI FØRST, på tvers av alle felter (Codex P1). Erstatning
    # i feltrekkefølge var to lekkasjer i én: «Ola» før «Ola Nordmann» gir
    # `[NAVN-1] Nordmann` — etternavnet når modellen — og «Ann» før
    # «Ann@example.com» gir `[NAVN-1]@example.com`, som `krev_blindet`
    # godtar fordi den HELE adressen ikke lenger står der. Token-
    # nummereringen er fortsatt feltrekkefølgen, så den er uendret og
    # deterministisk; bare erstatningsrekkefølgen er lengdestyrt.
    #
    # UTSATT, K2 → #158. Erstatningen er en DELSTRENGSERSTATNING, og den
    # treffer også inni urelaterte ord: med `navn: ["Ann"]` blir
    # «planning» til «pl[NAVN-1]ing», og `krev_blindet` godkjenner
    # resultatet fordi den leter etter det samme mønsteret. Modellen får
    # altså korrupt input, og korrupt input kan endre både kravfunn og
    # rangering. Funnet er ekte og målt (Codex P2, runde 19) — det er
    # runde 4 i tabellen i #158.
    #
    # Ordgrenser er IKKE lappen. `krev_blindet` deler mønster med
    # maskeringen her, så en `\b`-forankring ville forankret PORTEN
    # også — og norsk sammensetning gjør den retningen dyrere enn
    # korrupsjonen: et etternavn «Berg» ville stått umaskert i
    # «Bergsveien», og porten ville sagt god for det. Det er å bytte
    # korrupsjon inn mot lekkasje ut, som er den femte formen på samme
    # rot, ikke en lukking av den. Eier ratifiserte B-veien (strukturell
    # blinding: personfeltene finnes ikke i inputen i det hele tatt)
    # nettopp fordi en invariant som hviler på et fritekstsøk ikke kan
    # være absolutt i noen av retningene.
    for _felt, token, verdi in sorted(par, key=lambda p: -len(p[2])):
        # Erstatningen er en funksjon, ikke en mal: tokenet skal stå
        # ordrett, aldri tolkes som `re`-referanser. Avmaskeringstabellen
        # bærer den STRUKTURERTE skrivemåten — en avmaskering gir altså
        # `Kari` tilbake der dokumentet skrev `KARI`, og det er riktig:
        # feltverdien er kilden, dokumentets versaler er formatering.
        tekst = _monster(verdi).sub(lambda _t, tok=token: tok, tekst)
    return tekst


def blind_dokumenter(dokumenter: list[str],
                     kandidatfelter: dict[str, list[str]]
                     ) -> tuple[list[str], dict[str, str]]:
    """-> (blindede dokumenter, avmaskeringstabell). #174s form.

    Kandidatens dokumenter ble skjøtet med to linjeskift FØR blindingen,
    og et modellsitat kunne krysse skjøten: `valider_funn` godtok et
    utdrag som ikke står i noe faktisk søknadsdokument (Codex G7 på
    #170). Dokumentgrensene kunne ikke bæres inn i koordinatsystemet
    fordi blindingen endrer lengder.

    Med tabellen bygget ÉN gang og anvendt PER dokument er grensene
    trivielle: hvert blindet dokument har sin egen lengde, og kalleren
    kan måle et sitat mot ett dokument i stedet for mot skjøten.
    Kryss-sitater blir umulige per konstruksjon — ikke avvist av nok en
    port.

    Skilletekst-sentinelen (en syntetisk streng inn i modellens
    lesestoff for å redde det gamle koordinatsystemet) er AVVIST i
    samme dom.
    """
    par, avmaskering = bygg_tabell(kandidatfelter)
    # DEN PER-DOKUMENT-FORMENS EGEN GRENSE (Codex P1 på #240). Erstatningen
    # skjer i ÉTT dokument av gangen, så en deklarert verdi som ligger PÅ
    # TVERS av skjøten kan per konstruksjon aldri maskeres — og
    # `krev_blindet` på den sammensatte teksten kan ikke se det, fordi en
    # overlappende KORTERE søsterverdi rekker å rive bort beviset først:
    #
    #     dokumenter = ["Kari", "Testdal"]
    #     felter     = {"navn": ["Kari\n\nTestdal", "Kari"]}
    #
    # `_traff_alle` sier god (`Kari` traff dokument 1), `Kari` erstattes
    # per dokument, og modellinputen blir `"[NAVN-2]\n\nTestdal"`. Da
    # finnes `Kari\n\nTestdal` ikke lenger i teksten porten måler, porten
    # løper vakuøst, og ETTERNAVNET går i klartekst til modellen mens
    # kjøringen telles som blindet. Skjøt-før-blind-formen fanget dette;
    # #174 mistet det, og dette er #174s regning.
    #
    # PORTEN MÅLER FOREKOMSTEN, IKKE DEKLARASJONENS FORM (Cursor P2 på
    # #240). «Verdien inneholder `SKJOT`» er NØDVENDIG for at et treff
    # skal kunne krysse, men ikke TILSTREKKELIG — og den første formen
    # her målte bare det nødvendige. En flerlinjet verdi som står HELT
    # inne i ETT dokument (`"Gate 1\n\n0020 Oslo"` i adressefeltet på én
    # CV) inneholder skjøten uten å krysse noe som helst, og ble felt.
    # Det er ikke fail-closed sikkerhet, det er et kontraktsbrudd:
    # docstringen under lover at en verdi med blank linje kan maskeres i
    # én tekst, og `evaluer_kandidat` går NÅ alltid denne veien — også
    # for én streng og for én-fils-bunter, som ikke har noen skjøt.
    #
    # Målingen er derfor treffets PLASSERING, på råteksten, før noen
    # erstatning: et treff som ikke ligger helt inne i ett dokument er
    # per konstruksjon umaskerbart, fordi `anvend` ser ett dokument av
    # gangen. Skjøten og spennene kommer fra `dokumentgrenser`, altså
    # samme kilde som kryss-sitatporten bruker — ingen ny maskin, og
    # ingen tegnliste: vi spør ikke HVILKE tegn verdien består av, bare
    # om en forekomst faller mellom to dokumenter.
    #
    # Formen er SELV-AVGRENSENDE. Med ett dokument er hele teksten ett
    # spenn, så hvert treff ligger per definisjon inne i det: én-tekst-
    # veien kan aldri felles her, uten at det trengs et `len(...) > 1`-
    # unntak som kunne drifte fra resten.
    #
    # Den bor HER og ikke i `verdiform_lukket`: `blind` (én tekst) kan
    # maskere en verdi med blank linje helt fint, og manifestdøra har
    # ingen mening om skjøten. Grensen tilhører den PER-DOKUMENT-anvendte
    # tabellen, altså denne funksjonen — ikke deklarasjonens form. Et
    # sjette formforsøk er nettopp det §9 forbyr.
    # SØKET ER AVGRENSET FØR DET STARTER (Codex P1). To ledd, og begge
    # følger av formen, ikke av en optimalisering:
    #
    # 1) En verdi UTEN skjøt kan per konstruksjon ikke krysse en. Et
    #    treff som spenner over en grense inneholder separatoren
    #    bokstavelig — `_monster` er `re.escape` med `IGNORECASE`, og
    #    `\n\n` har ingen versaler — så treffteksten, og dermed verdien,
    #    må inneholde `SKJOT`. Verdier uten den hoppes over helt.
    # 2) Spennet et treff ligger i FINNES med `bisect`, ikke med en
    #    gjennomlesning. Grensene er sortert og disjunkte, så det siste
    #    dokumentet som starter før treffet er det ENESTE som kan
    #    inneholde det.
    #
    # Ledd 2 er det som fjernet KLASSEN: uten det var sjekken kvadratisk
    # i dokumentantallet på den ærlige veien. Buntgaten tillater 20 000
    # dokumenter, og `['a'] * 20_000` med én vanlig verdi tok 15,4 s
    # målt her — ganger de seksti verdiene grensene tillater (seks felt à
    # ti) er det et kvarter før modellen i det hele tatt kalles. Med
    # bisect: 0,034 s. `test_grenseoppslaget_skalerer_med_dokument-
    # antallet` holder den målingen.
    #
    # Ledd 1 sparte først bare TID — 2,90 s mot 5,57 s på seks MB med
    # seksti verdier, altså ~2x på arbeid av samme orden som `anvend`s
    # egne pass. Det er for tett til en port på en delt CI-maskin, og det
    # sto en runde uten en. Codex flyttet spørsmålet dit det hørte hjemme:
    # filteret sto INNI løkka, så den skjøtede kopien ble laget uansett.
    # Nå står det FØR, og da sparer det MINNE — som er målbart uten
    # klokke.
    # SKJØTET TEKST BYGGES BARE NÅR DEN KAN TRENGES (Codex P2, runde 3).
    # Filteret sto INNI løkka, så `samlet` ble materialisert selv når ingen
    # verdi kunne krysse — normaltilfellet. Det er en kandidatstor kopi:
    # `parsing.MAKS_TOTAL_UTPAKKET` tillater 2 GB, og `kjor_bunt` går
    # denne veien to ganger per kandidat (klargjøring og evaluering), så
    # en fullt lovlig bunt kunne ta livet av arbeideren for en sjekk som
    # ikke hadde noe å gjøre. Spørsmålet stilles nå FØR kopien lages.
    kryssbare = [v for verdier in kandidatfelter.values()
                 for v in verdier if SKJOT in v]
    if kryssbare:
        samlet = SKJOT.join(dokumenter)
        grenser = dokumentgrenser(dokumenter)
        startene = [start for start, _ in grenser]
        for verdi in kryssbare:
            # OVERLAPPENDE FOREKOMSTER MÅ SES (Codex P1, runde 4).
            # `finditer` gir bare IKKE-overlappende treff, og en verdi som
            # overlapper seg selv kan skjule sitt eget kryss:
            # `["Kari\n\nKari", "Kari"]` med verdien `"Kari\n\nKari"`
            # gir ett treff på offset 0 — helt inne i dokument 1 — mens
            # forekomsten på offset 7 KRYSSER skjøten og aldri blir sett.
            # Blindingen maskerte da den første og sendte det andre navnet
            # i klartekst til modellen, med porten grønn.
            #
            # Lookahead-formen `(?=(...))` konsumerer ingenting, så motoren
            # rykker ett tegn fram om gangen og ser HVER forekomst. Gruppen
            # inni bærer det ekte treffet, så `end(1)` er den faktiske
            # slutten — ingen antakelse om at treffets lengde er verdiens
            # (versalufølsomhet kan i prinsippet endre den).
            #
            # GRUPPEN MANGLET, OG SLUTTEN VAR REGNET (Cursor P2, runde 5).
            # Kommentaren over lovet `end(1)`; koden skrev
            # `treff.start() + len(verdi)`. Målt her er de to ALLTID like:
            # `re.escape` gir bare enkelttegn-literaler, og ingen av dem
            # matcher et løp på annet enn ett tegn under `IGNORECASE` —
            # brute-forcet over hele Unicode (alle printbare kodepunkter,
            # null avvik), og ligaturveien Cursor foreslo (`ﬁ` mot `fi`)
            # matcher ikke i det hele tatt: Pythons `re` gjør SIMPEL
            # case-folding, ikke full. Det finnes derfor ingen inndata som
            # gjør mutasjonen rød, og ingen test kan bevise denne linja.
            # Nettopp derfor MÅLES slutten i stedet for å regnes: da hviler
            # porten på det motoren faktisk fant, ikke på en `re`-invariant
            # ingen port her holder — og et bytte til full case-folding
            # (`regex`, eller en framtidig `re`) kan ikke gjøre den fail-open
            # i stillhet. SP-3: et umålt utfall er et avvist utfall.
            overlappende = re.compile(f"(?=({re.escape(verdi)}))",
                                      re.IGNORECASE)
            for treff in overlappende.finditer(samlet):
                slutt_treff = treff.end(1)
                i = bisect_right(startene, treff.start()) - 1
                # `i < 0` kan ikke skje — første dokument starter på 0 —
                # men et treff som BEGYNNER inne i selve skjøten peker på
                # dokumentet foran, og faller da ut på sluttkravet under.
                start, slutt = grenser[i]
                if not (start <= treff.start() and slutt_treff <= slutt):
                    raise Blindingsfeil("verdi_krysser_dokumentgrense")
    if not _traff_alle(kandidatfelter, dokumenter):
        raise Blindingsfeil("ugyldig_maskeringsform")
    return [anvend(par, d) for d in dokumenter], avmaskering


def blind(tekst: str, kandidatfelter: dict[str, list[str]]
          ) -> tuple[str, dict[str, str]]:
    """-> (blindet tekst, avmaskeringstabell {token: klartekst}).

    `kandidatfelter` er de STRUKTURERTE verdiene fra søknaden
    ({felt: [verdier]}); fritekst-gjenkjenning av personalia er bevisst
    IKKE lovet her — løftet er de navngitte feltene.

    To porter, og den andre måler EFFEKT: formen inn
    (`feltverdier_lukket`), og — målt på ORIGINALTEKSTEN, før noen
    erstatning — at hvert deklarert felt faktisk traff dokumentteksten.
    Begge er `ugyldig_maskeringsform`.
    """
    # Vakten gir PRESISJON, ikke lenger sikkerheten alene (målt i runde
    par, avmaskering = bygg_tabell(kandidatfelter)
    if not _traff_alle(kandidatfelter, [tekst]):
        raise Blindingsfeil("ugyldig_maskeringsform")
    return anvend(par, tekst), avmaskering


def krev_blindet(tekst: str, avmaskering: dict[str, str]) -> None:
    """Porten på FAKTISK modellinput (port 16): ingen av klartekst-
    verdiene får stå i teksten som går til modellen."""
    for token, verdi in avmaskering.items():
        if verdi and _monster(verdi).search(tekst):
            raise Blindingsfeil("maskert_felt_i_modellinput")


def evalueringsinput(tekst: str, kandidatfelter: dict[str, list[str]], *,
                     blinding_av: bool = False,
                     auditrad: dict | None = None
                     ) -> tuple[str, dict[str, str]]:
    """Den ENESTE veien til modellinput. Standard er blindet; avskrudd
    krever en auditrad med aktør, tidspunkt og begrunnelse — mangler
    den, finnes ikke input (port 16b)."""
    if blinding_av:
        # UTSATT, K1 → #159. Codex har målt det samme to ganger, og
        # funnet er riktig: denne porten er SELVATTESTERT. Den som ber om
        # å skru av blindingen leverer selv beviset på at handlingen er
        # auditert, og beviset er en dict med tre sanne verdier. Det
        # finnes ingen produsent og ingen persisteringsvei for `auditrad`
        # i repoet — «auditert» er altså en påstand fra kalleren, ikke en
        # egenskap ved noe som overlever kallet.
        #
        # Det kan ikke lukkes her. En strengere formport (`ts` som gyldig
        # ISO-8601, en `revisjon_id` som ser ut som en UUID) flytter bare
        # påstanden ett hakk: en velformet UUID beviser ikke at en rad
        # finnes. Den ekte lukkingen krever en varig, tenant-bundet
        # revisjonshendelse — tabell, append-only-vakt, rettighetsgrense,
        # skriver og oppslag — og kjernen har ingen slik tabell i dag.
        # Ny maskin i en fiksrunde er nettopp det §9 K1 forbyr, så valget
        # (bygg hendelsen, eller fjern døra til den finnes) ligger i
        # #159. Formporten under er derfor uendret, og den leser bevisst
        # ikke sterkere enn den måler.
        if not (isinstance(auditrad, dict)
                and auditrad.get("aktor") and auditrad.get("ts")
                and auditrad.get("begrunnelse")):
            raise Blindingsfeil("avskrudd_uten_auditrad")
        return tekst, {}
    blindet, avmaskering = blind(tekst, kandidatfelter)
    # FAIL-CLOSED (Codex P1, eiers K2-avgjørelse). Med tomme eller
    # manglende strukturerte felter ble `avmaskering` tom, `krev_blindet`
    # godkjente VAKUØST — den har ingenting å lete etter — og råteksten
    # gikk til modellen mens kjøringen ble registrert som blindet. En
    # port som ikke kan feile er ikke en port; her er avviket at det ikke
    # FINNES noe å måle, og et umålt utfall er et avvist utfall (SP-3).
    #
    # ÆRLIG OM HVA DETTE IKKE LUKKER: dette feller det degenererte
    # tilfellet (ingenting å maskere), ikke det delvise. Et uttrekk som
    # gir `navn` men taper `adresse` passerer fortsatt, like vakuøst for
    # adressen. Fullstendighet har ingen målbar definisjon så lenge
    # invarianten hviler på et fritekstsøk — den kommer med B-veien
    # (strukturell blinding), som er eiers ratifiserte mål. Denne linjen
    # er SP-3 på veien som alt finnes, ikke en femte maskeringsform.
    if not avmaskering:
        raise Blindingsfeil("blinding_uten_felter")
    krev_blindet(blindet, avmaskering)
    return blindet, avmaskering


def evalueringsinput_dokumenter(dokumenter: list[str],
                                kandidatfelter: dict[str, list[str]], *,
                                blinding_av: bool = False,
                                auditrad: dict | None = None
                                ) -> tuple[list[str], dict[str, str]]:
    """Som `evalueringsinput`, men bevarer DOKUMENTGRENSENE (#174).

    Samme porter i samme rekkefølge — avskrudd krever auditrad,
    `blinding_uten_felter` er fail-closed, og `krev_blindet` måler den
    faktiske modellinputen. Forskjellen er at blindingen skjer per
    dokument mot ÉN delt tabell, så kalleren får lengdene den trenger for
    å hindre at et sitat krysser en skjøt.

    `krev_blindet` måles på den SAMMENSATTE teksten, ikke per dokument:
    det er den strengen modellen faktisk leser, og en verdi som er delt
    over to dokumenter finnes ikke i noen av dem hver for seg. Porten skal
    måle inputen, ikke bitene den ble laget av.
    """
    if blinding_av:
        if not (isinstance(auditrad, dict)
                and auditrad.get("aktor") and auditrad.get("ts")
                and auditrad.get("begrunnelse")):
            raise Blindingsfeil("avskrudd_uten_auditrad")
        return list(dokumenter), {}
    blindede, avmaskering = blind_dokumenter(dokumenter, kandidatfelter)
    if not avmaskering:
        raise Blindingsfeil("blinding_uten_felter")
    krev_blindet(SKJOT.join(blindede), avmaskering)
    return blindede, avmaskering

