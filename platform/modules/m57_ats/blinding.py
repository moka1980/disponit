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
import unicodedata

#: Katalogens løfte, ordrett — settet er LUKKET og rekkefølgen stabil
#: (tokennummereringen skal være deterministisk for samme input).
MASKERTE_FELTER: tuple[str, ...] = (
    "navn", "kjonn", "alder", "adresse", "bilde", "kontakt")


class Blindingsfeil(Exception):
    def __init__(self, kode: str):
        self.kode = kode
        super().__init__(kode)


def verdiform_lukket(verdi: str) -> bool:
    """Er personverdien SIN EGEN skrivemåte? (Cursor P1)

    Verdien er både det som maskeres og det `krev_blindet` leter etter,
    så en verdi som ikke kan stå i dokumentet gjør porten vakuøs uten å
    gjøre den tom: `"Kari Testdal "` maskerer ingenting i en tekst som
    skriver navnet uten hale, og `krev_blindet` finner heller ikke den
    padda formen — kjøringen telles som blindet mens klartekstnavnet går
    til modellen. Samme klasse som `kandidat_id` før ASCII-kanonen: en
    port som MÅLER `strip()` men LAGRER råverdien, måler noe annet enn
    det den lagrer.

    Vi avviser, vi kanoniserer ikke: én vei inn, og en deklarasjon som
    mente `Kari` sier `Kari`. Cc/Cf (`U+200B`, RTL-markørene, kontroll-
    tegn) er samme sak i usynlig form — de skiller to verdier som er én
    for et menneske og for dokumentet."""
    return verdi == verdi.strip() and not any(
        unicodedata.category(tegn) in ("Cc", "Cf") for tegn in verdi)


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
    """
    ukjente = set(kandidatfelter) - set(MASKERTE_FELTER)
    if ukjente:
        raise Blindingsfeil("ukjent_maskeringsfelt")
    # FORMEN MÅLES, den tas ikke på ord (Codex P2). Typeannotasjonen sier
    # `{felt: [verdier]}`, men uttrekket er en FREMMED produsent — en
    # modell eller en parser — og en annotasjon er ingen port. To former
    # er velformet JSON og begge er farlige:
    #
    #   * `{"navn": "Ann"}` — en streng er iterbar, så løkka under fikk
    #     tegnene `A`, `n`, `n`. Hver eneste `A` og `n` i HELE søknaden
    #     ble maskert, `krev_blindet` godkjente resultatet (den leter
    #     etter de samme tegnene, og de er borte), og modellen fikk
    #     korrupt tekst som input til både kravfunn og rangering.
    #   * `{"alder": [42]}` — `re.escape(42)` er en rå `TypeError` ut av
    #     modulen, ikke et kodet blindingsavvik kalleren kan behandle.
    #
    # Grensen er kontrakten `blind` ALT er skrevet for: en sekvens av
    # strenger per felt. Dette er ikke et femte maskeringsforsøk (#158)
    # — mønsteret og porten er uendret — det er inndatasiden av samme
    # fail-closed regel som `blinding_uten_felter`: et umålt utfall er et
    # avvist utfall (SP-3). Et `set` avvises med vilje sammen med de
    # andre: tokennummereringen skal være deterministisk for samme input,
    # og en uordnet samling gir den ikke.
    #
    # FORMEN ER OGSÅ VERDIENS EGEN (`verdiform_lukket`, Cursor P1):
    # manifestet avviser padding og Cf/Cc på vei inn, men `blind` tar
    # imot felter fra en INJISERT `kandidatfelter_for` også, og den veien
    # går utenom manifestporten. Grensen står derfor begge steder, med én
    # definisjon.
    for verdier in kandidatfelter.values():
        if not isinstance(verdier, (list, tuple)) or not all(
                isinstance(verdi, str) and verdiform_lukket(verdi)
                for verdi in verdier):
            raise Blindingsfeil("ugyldig_maskeringsform")
    avmaskering: dict[str, str] = {}
    par: list[tuple[str, str]] = []
    for felt in MASKERTE_FELTER:
        for nr, verdi in enumerate(kandidatfelter.get(felt, ()), start=1):
            if not verdi:
                continue
            token = f"[{felt.upper()}-{nr}]"
            avmaskering[token] = verdi
            par.append((token, verdi))
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
    for token, verdi in sorted(par, key=lambda p: -len(p[1])):
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
