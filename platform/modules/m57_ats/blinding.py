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

Avskruingen (port 16b) hviler på en OPPSLÅTT revisjonshendelse (#159,
migrasjon 066): kalleren oppgir en hendelses-ID, og grensen slår den opp
gjennom en injisert oppslagsfunksjon. «Auditert» er en egenskap ved
basen, ikke ved kallet.

Av-maskeringstabellen er payload i `kandidat_avmaskering` (057) og
reapes med resten.
"""
from __future__ import annotations

import re

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
    """Kode + valgfri detalj — samme form som `Evalueringsfeil` (#159).

    Detaljen kom med oppslagsveien: en base som er nede og en hendelse som
    ikke finnes gir samme KODE (begge er «ikke autorisert», og noe annet
    ville lekket at naboen har en hendelse), men driften må kunne skille
    dem. Koden er kontrakten, detaljen er for mennesket som leser loggen.
    """

    def __init__(self, kode: str, detalj: str = ""):
        self.kode = kode
        super().__init__(f"{kode}: {detalj}" if detalj else kode)


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
    traff: dict[str, bool] = {
        felt: any(_monster(verdi).search(tekst) for verdi in verdier)
        for felt, verdier in kandidatfelter.items()}
    if not all(traff.values()):
        raise Blindingsfeil("ugyldig_maskeringsform")
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
    return tekst, avmaskering


def krev_blindet(tekst: str, avmaskering: dict[str, str]) -> None:
    """Porten på FAKTISK modellinput (port 16): ingen av klartekst-
    verdiene får stå i teksten som går til modellen."""
    for token, verdi in avmaskering.items():
        if verdi and _monster(verdi).search(tekst):
            raise Blindingsfeil("maskert_felt_i_modellinput")


#: Handlingen avskruingen krever i revisjonsloggen. Strengen er den samme
#: som CHECK-en i migrasjon 066 tillater — drifter de to, slår oppslaget
#: aldri til, og porten blir en dør som ikke kan åpnes i det hele tatt.
#: `test_avskruingshandlingen_finnes_i_066` holder dem sammen.
AVSKRUINGSHANDLING = "m57.blinding_avskrudd"


def krev_avskruingshendelse(hendelse_id, hendelseoppslag) -> dict:
    """Slår opp revisjonshendelsen avskruingen påberoper seg (#159).

    PÅSTANDEN ER ERSTATTET MED ET OPPSLAG. Porten var selvattestert:
    kalleren leverte selv beviset på at handlingen var auditert, og
    beviset var en dict med tre sanne verdier. Codex målte det to ganger
    (#153 runde 2 og 9), og et repo-vidt søk fant hverken produsent eller
    persisteringsvei for den dicten. En sann påstand om en
    revisjonshendelse er ikke en revisjonshendelse.

    `hendelseoppslag` er INJISERT, som `kandidatfelter_for` og `tekst_for`
    i `kjor_bunt`: modulen har ingen databaseforbindelse og skal ikke ha
    en. Kalleren gir en funksjon som slår opp `hendelse_id` i SIN EGEN
    tenantkontekst — i produksjon `les_revisjonshendelse` fra 066, som er
    SECURITY DEFINER med tenanten bundet til konteksten gjennom
    `krev_tenantkontekst`. Returnerer den `None`, finnes hendelsen ikke i
    kallerens tenant, og de to tilfellene («finnes ikke» og «finnes hos
    naboen») skilles bevisst ikke: at noe finnes hos naboen er ikke din
    opplysning.

    HANDLINGEN MÅLES OGSÅ, ikke bare eksistensen. Uten det leddet ville en
    hvilken som helst revisjonshendelse autorisert avskruing av
    blindingen — og vi hadde byttet en fri dict mot en fri UUID.
    """
    if not hendelse_id or not callable(hendelseoppslag):
        raise Blindingsfeil("avskrudd_uten_auditrad")
    try:
        hendelse = hendelseoppslag(hendelse_id)
    except Exception as e:                        # noqa: BLE001
        # EN OPPSLAGSFEIL ER IKKE EN GODKJENNING. Uten dette leddet ville
        # en base som er nede blitt en åpen dør — fail-open, som SP-3
        # forbyr. Feiltypen følger med i koden så driften kan skille en
        # manglende hendelse fra en manglende forbindelse.
        raise Blindingsfeil("avskrudd_uten_auditrad", type(e).__name__)
    if not isinstance(hendelse, dict) or not hendelse.get("aktor"):
        raise Blindingsfeil("avskrudd_uten_auditrad")
    if hendelse.get("handling") != AVSKRUINGSHANDLING:
        raise Blindingsfeil("avskrudd_feil_handling",
                            str(hendelse.get("handling")))
    return hendelse


def evalueringsinput(tekst: str, kandidatfelter: dict[str, list[str]], *,
                     blinding_av: bool = False,
                     avskruing_hendelse_id=None,
                     hendelseoppslag=None
                     ) -> tuple[str, dict[str, str]]:
    """Den ENESTE veien til modellinput. Standard er blindet; avskrudd
    krever en OPPSLÅTT revisjonshendelse (#159) — finnes den ikke i
    kallerens tenant, finnes ikke input (port 16b)."""
    if blinding_av:
        krev_avskruingshendelse(avskruing_hendelse_id, hendelseoppslag)
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
