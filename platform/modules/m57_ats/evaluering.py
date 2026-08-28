"""Evaluering (klarsignalet §6): rangering med synlige vekter — aldri
prosent som målt egenskap; risikofunn krever kildereferanse i
søknadsteksten; lukket kategorisett uten karaktertrekk-kategorier;
modellen er et container-image der digesten ER modellversjonen, og
biasmåling bundet til digesten er akseptkrav.

Modellen er INJISERT (m56s motor-form): denne fila eier kontrakten
rundt den — blindet input inn, skjemavaliderte funn ut — aldri selve
kjøringen.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import blinding

#: Lukket kategorisett (§6). Ingen karaktertrekk: kategoriene beskriver
#: DOKUMENTASJONEN mot stillingens krav, aldri personen. En ny kategori
#: er en kontraktsendring, ikke en modellidé.
FUNN_KATEGORIER = frozenset({
    "krav_ikke_dokumentert",
    "manglende_dokumentasjon",
    "motstridende_opplysning",
    "uklar_tidslinje",
    "utenfor_soknadsfrist",
})


#: Største vekt som overlever transporten helt fram til leseren:
#: `Number.MAX_SAFE_INTEGER`, det siste heltallet en IEEE-754 `double`
#: bærer eksakt. Over dette leser flaten et ANNET tall enn det som ble
#: rangert — se porten i `ranger`.
VEKT_EKSAKT_MAKS = 2 ** 53 - 1


class Evalueringsfeil(Exception):
    def __init__(self, kode: str, detalj: str = ""):
        self.kode = kode
        super().__init__(f"{kode}: {detalj}" if detalj else kode)


#: Funnets LUKKEDE feltsett, og kildereferansens. Kontrakten er settet,
#: ikke de feltene noen kom på å måle: se `valider_funn`.
FUNN_FELTER = frozenset({"kategori", "kilde"})
KILDE_FELTER = frozenset({"start", "slutt", "sitat"})


def valider_funn(funn: dict, soknadstekst: str,
                 grenser: list[tuple[int, int]] | None = None) -> dict:
    """Skjemaporten (port 15): et funn uten kildereferanse som ordrett
    står i søknadsteksten, finnes ikke. Sitatet måles mot teksten på
    [start:slutt] — en referanse som ikke treffer er like avvist som en
    som mangler.

    KONTRAKTEN ER SETTET, og porten BYGGER funnet (Codex P1/P2, runde 5).
    Tre runder på rad fant hull av samme slag her, og rotårsaken var
    felles: funnet ble målt felt for felt — de feltene noen kom på — og
    så kopiert RÅTT videre inn i artefakten. Da bar
    `{"kategori": ..., "kilde": ..., "karaktertrekk": "..."}` seg gjennom
    den lukkede, karaktertrekkfrie kontrakten uten å bli sett, og en
    `kategori` modellen sendte som liste sprengte `in frozenset` med en
    rå `TypeError` i stedet for modulens kodede utfall (SP-3).

    Svaret på begge er det samme: feltsettene er LUKKET begge veier, og
    returverdien er et KANONISK funn bygget av de validerte verdiene.
    Et felt som ikke står i `FUNN_FELTER`/`KILDE_FELTER` kan da hverken
    slippe gjennom umålt eller følge med videre.

    KJENT BEGRENSNING — DOKUMENTGRENSER (Codex G7/P2, utsatt til #174).
    `soknadstekst` er ikke ett dokument: `kjoring.kjor_bunt` skjøter hele
    kandidatmappen sammen med en tom linje mellom medlemmene, og porten
    her måler bare at [start:slutt] står i DEN strengen. Et sitat kan
    derfor starte i søknadsbrevet, løpe gjennom skjøten og fortsette inn
    i CV-en, og bli godtatt selv om utdraget ikke står ordrett i noe
    enkelt søknadsdokument. Grensene lar seg ikke bære inn hit i dagens
    koordinatsystem: teksten porten måler mot er den BLINDEDE, og
    blindingen endrer lengder («Kari» → `[NAVN-1]`), så råtekstens
    dokumentgrenser peker et annet sted i den. Eierens K2-dom (23/8) er
    valg 2 — `blind` splittes i «bygg tabell» (én per kandidat, delt
    over dokumentene) og «anvend tabell» (per dokument), slik #158s
    strukturelle vei uansett trenger. Da valideres funnet mot ETT
    blindet dokument og kryss-sitater er umulige per konstruksjon. Se
    #174. Sentinel-veien er AVVIST: ingen syntetisk skilletekst inn i
    det modellen faktisk leser for å redde et koordinatsystem som skal
    byttes.
    """
    if not isinstance(funn, dict) or set(funn) != FUNN_FELTER:
        raise Evalueringsfeil(
            "ukjent_funnfelt",
            ",".join(sorted(set(funn) ^ FUNN_FELTER))
            if isinstance(funn, dict) else type(funn).__name__)
    kategori = funn["kategori"]
    # `isinstance` FØR mengdeoppslaget: en uhashbar verdi (liste, dict)
    # kaster `TypeError` ut av `in frozenset`, og en bibliotekfeil er
    # ikke en avvisning — den er en 500 hos kalleren.
    if not isinstance(kategori, str) or kategori not in FUNN_KATEGORIER:
        raise Evalueringsfeil("ukjent_kategori", repr(kategori))
    kilde = funn["kilde"]
    if not isinstance(kilde, dict) or set(kilde) != KILDE_FELTER:
        raise Evalueringsfeil("uten_kildereferanse")
    start, slutt, sitat = (kilde.get("start"), kilde.get("slutt"),
                           kilde.get("sitat"))
    # Offsetene må være KANONISKE posisjoner, ikke bare noe som får
    # snittet til å stemme (Codex P2). Python-snitt klager aldri: med
    # `abcdef` validerer `start=-3, slutt=6` sitatet `def`, og `False`
    # og `True` er lovlige int-er som validerer det første tegnet. En
    # mottaker som bruker referansen til å markere stedet i teksten
    # ville da peke et annet sted enn porten målte — eller et sted som
    # ikke finnes. `bool` er dessuten en subklasse av `int`, så
    # typesjekken alene slapp den gjennom.
    if (not isinstance(start, int) or isinstance(start, bool)
            or not isinstance(slutt, int) or isinstance(slutt, bool)
            or not isinstance(sitat, str) or not sitat
            or not 0 <= start < slutt <= len(soknadstekst)
            or soknadstekst[start:slutt] != sitat):
        raise Evalueringsfeil("uten_kildereferanse")
    # SITATET KAN IKKE KRYSSE EN DOKUMENTGRENSE (#174, Codex G7 på #170).
    #
    # Kandidatens dokumenter settes sammen til én tekst før modellen leser
    # dem. Et sitat som spenner over skjøten står ikke i NOE faktisk
    # søknadsdokument — det er satt sammen av to — og porten over godtok
    # det likevel, fordi den bare måler mot den sammensatte strengen.
    #
    # Grensene er nå kjennbare fordi blindingen skjer per dokument mot én
    # delt tabell (`blinding.blind_dokumenter`): hvert blindet dokument
    # har sin egen lengde. Uten den splitten kunne grensene ikke bæres inn
    # i dette koordinatsystemet i det hele tatt — blindingen endrer
    # lengder, så råtekstens skjøter peker feil sted i den blindede.
    #
    # `grenser=None` er den gamle kontrakten: ett dokument, ingen skjøt å
    # krysse. Den beholdes fordi `blind`/`evalueringsinput` fortsatt har
    # enkeltdokument-formen, og fordi et kall UTEN grenser da måler
    # nøyaktig det samme som før — ikke mindre.
    if grenser is not None and not any(
            a <= start and slutt <= b for a, b in grenser):
        raise Evalueringsfeil("sitat_krysser_dokumentgrense")
    return {"kategori": kategori,
            "kilde": {"start": start, "slutt": slutt, "sitat": sitat}}


def _krev_helt_svar(svar: object, vekter: dict[str, int]) -> dict:
    """Et AVKORTET modellsvar er en FEIL, ikke et tomt resultat
    (Codex P1).

    Artefakten ble bygget med `.get(..., tom)` per felt, så `{}` — det et
    avbrutt eller lengdekuttet svar typisk er — ble til en vellykket
    evaluering: ingen funn, ingen oppfylte krav.
    Kalleren kunne rangere og promotere den kandidaten som «oppfyller
    ingenting». En avbrutt kjøring skal gi et rent feilutfall (§7), og
    det gjelder også når avbruddet er modellens eget.

    `oppfylt` måles mot PROFILEN: `ranger` avviser krav utenfor den, og
    speilbildet er at et krav som mangler stille ble til null poeng."""
    if not isinstance(svar, dict):
        raise Evalueringsfeil("ufullstendig_modellsvar",
                              type(svar).__name__)
    # `intervjusporsmal` er UTE av kontrakten (#225, eiers retning 27/8):
    # spørsmål genereres ved innkalling av de beste, aldri under
    # evalueringen av alle — et svar som likevel bærer dem får dem
    # ignorert, for artefakten bygges av de validerte feltene alene.
    for felt, form in (("funn", list), ("oppfylt", dict)):
        if not isinstance(svar.get(felt), form):
            raise Evalueringsfeil("ufullstendig_modellsvar", felt)
    # LISTEN var målt, ELEMENTENE ikke (Cursor P2). `valider_funn` leser
    # funnet som en dict, så `[null]` eller `["tekst"]` fra modellen ga en
    # rå `AttributeError` ut av modulen. Kontrakten er et KODET utfall
    # (SP-3): en bibliotekfeil er ikke en avvisning, den er en 500 hos
    # kalleren — og forskjellen avgjør om kjøringen kan retryes eller ei.
    if any(not isinstance(f, dict) for f in svar["funn"]):
        raise Evalueringsfeil("ufullstendig_modellsvar", "funn")
    if set(svar["oppfylt"]) != set(vekter):
        raise Evalueringsfeil(
            "ufullstendig_modellsvar",
            "oppfylt: " + ",".join(sorted(
                set(vekter) ^ set(svar["oppfylt"]))))
    # Nøklene var målt, verdiene ikke (Cursor P2). `ranger` avviser alt
    # som ikke er `bool` — `"false"` er den vanligste JSON-feilen en
    # modell gjør, og som streng er den SANN — men den porten står lenger
    # nede i løypa enn artefakten. Et svar med `"drift": "false"` ble
    # altså bygget som en VELLYKKET evaluering, og feilen dukket først opp
    # ved rangeringen, eller aldri, om kalleren lagrer artefakten først.
    # Kontrakten måles der svaret leses, ikke der det tilfeldigvis brukes.
    ulovlige = [k for k, v in svar["oppfylt"].items()
                if not isinstance(v, bool)]
    if ulovlige:
        raise Evalueringsfeil("ikke_boolsk_oppfyllelse",
                              ",".join(sorted(ulovlige)))
    return svar


def ranger(kandidater: dict[str, dict[str, bool]],
           vekter: dict[str, int]) -> list[dict]:
    """Rangering med SYNLIGE vekter: poengsummen er en sum av
    vekt × oppfylt per krav, og nedbrytningen følger med hvert innslag.
    Ingen prosent, ingen «match score» — poeng er poeng (§6).
    """
    # `bool` er en subklasse av `int` i Python (Codex P2): en
    # stillingsprofil som deserialiserte en JSON-`true` som vekt fikk
    # vekten 1, og `false` vekten 0 — rangeringen endret seg stille, og
    # ingen port sa fra. Feltkontrakten i kjernen avviser boolske tall
    # eksplisitt; her måles det samme.
    #
    # …OG EN VEKT SOM IKKE OVERLEVER TRANSPORTEN ER INGEN VEKT (Codex P2,
    # runde 10). Python teller vilkårlig stort; JSON-tallet leses av
    # flaten som en `double`. Over `Number.MAX_SAFE_INTEGER` har
    # avrundingen alt skjedd i `JSON.parse` — poengsummen serveren regner
    # og den flaten viser er da to ulike tall på samme vekt, uten at noe
    # sier fra. Rangeringen her er ikke et internt regnestykke: den
    # SKAL leses av et menneske foran en irreversibel signering, og
    # nedbrytningen som følger med hvert innslag er nettopp beviset.
    # Taket er derfor kontraktens, ikke plattformens.
    if not vekter or any(not isinstance(v, int) or isinstance(v, bool)
                         or v < 0 or v > VEKT_EKSAKT_MAKS
                         for v in vekter.values()):
        raise Evalueringsfeil("ugyldige_vekter")
    ut = []
    for kandidat_id, oppfylt in kandidater.items():
        ukjente = set(oppfylt) - set(vekter)
        if ukjente:
            raise Evalueringsfeil("krav_utenfor_profilen",
                                  ",".join(sorted(ukjente)))
        # …og speilbildet: et krav som MANGLER (Codex P2). Porten målte
        # bare den ene retningen, mens `nedbrytning` leser med `.get` —
        # så `ranger({"k": {}}, {"drift": 3})` ga en vellykket rangering
        # der kandidaten stille «oppfylte ingenting». `_krev_helt_svar`
        # krever alt det eksakte settet av modellsvaret, men den ligger i
        # evalueringsløypa: en kaller som rangerer et LAGRET eller på
        # annen måte ferdigbygget resultat går utenom den. Kravsettet er
        # profilens, ikke resultatets, og det måles her — der poengene
        # faktisk regnes ut.
        manglende = set(vekter) - set(oppfylt)
        if manglende:
            raise Evalueringsfeil("krav_mangler_i_resultatet",
                                  ",".join(sorted(manglende)))
        # Oppfyllelsen er BOOLSK, ikke sannhetsverdien til hva som helst
        # (Codex P2). Modellutdata er ikke typesjekket noe sted, og
        # `"false"` — den vanligste JSON-feilen en modell gjør — er en
        # sann streng: kandidaten fikk hele kravets vekt og rangeringen
        # ble stille feil. En verdi vi ikke kan lese er en feil, aldri et
        # ja.
        ulovlige = [k for k, v in oppfylt.items() if not isinstance(v, bool)]
        if ulovlige:
            raise Evalueringsfeil("ikke_boolsk_oppfyllelse",
                                  ",".join(sorted(ulovlige)))
        # Oppslaget er direkte, ikke `.get`: etter de to portene over er
        # nøkkelsettene per definisjon like, og da skal koden si det.
        # `.get` var stedet standardverdien kom fra.
        nedbrytning = {krav: (vekter[krav] if oppfylt[krav] else 0)
                       for krav in vekter}
        ut.append({"kandidat_id": kandidat_id,
                   "poeng": sum(nedbrytning.values()),
                   "nedbrytning": nedbrytning})
    # Stabil orden: poeng synkende, deretter kandidat-id — likhet skal
    # være synlig som likhet, aldri stille avgjort av dict-rekkefølgen.
    ut.sort(key=lambda k: (-k["poeng"], k["kandidat_id"]))
    return ut


@dataclass(frozen=True)
class Biasmaaling:
    image_digest: str
    artefakt_sha256: str
    ts: str


def _er_sha256(verdi: object) -> bool:
    return (isinstance(verdi, str) and len(verdi) == 64
            and all(c in "0123456789abcdef" for c in verdi.lower()))


def krev_biasmaaling(image_digest: str,
                     maalinger: dict[str, Biasmaaling]) -> Biasmaaling:
    """Port 17: et imagebytte uten NY biasmåling blokkerer aksepten.
    Målingen er bundet til digesten — samme modellfil bak ny digest er
    en ny modellversjon med et nytt bevisbehov.

    En OPPFØRING er ikke en måling (Codex P2): porten sjekket bare at det
    lå noe under digesten og at objektet gjentok den, så
    `Biasmaaling(digest, "", "")` — uten artefakthash og uten tidspunkt —
    passerte som bevis. Feltene måles derfor: digesten på formen
    `sha256:<64 hex>`, artefakthashen som sha256, tidspunktet som
    lesbar ISO 8601.

    AT ARTEFAKTEN FINNES, MÅLES INGEN STEDER ENNÅ — og det står her fordi
    det er sant (Codex P1, runde 5). Den forrige formuleringen delegerte
    oppslaget til evidensgrensen `m57-v1`, men den grensen måler hverken
    artefakthashen eller modelldigesten: den er generert fra
    invariantsettet, og et oppslag mot artefaktlageret finnes ikke i den.
    En kommentar som peker på en port som ikke er der, er verre enn ingen
    port, for den stopper neste leser fra å lete.

    UTSATT, K1/K2 → #167. Grensen her er FORMEN, ikke eksistensen:
    `"0" * 64` er en syntaktisk gyldig artefakthash og passerer, og
    `maalinger`-kartet leveres av den samme kalleren som ber om
    evalueringen. Å slå opp artefakten krever et artefaktlager denne
    modulen ikke har — å gi en ren funksjon tenantkontekst og
    databaseforbindelse ville flyttet portens tillitsgrense, altså ny
    maskin i en fiksrunde (K1).

    Codex har nå målt mekanismen TRE ganger (runde 12, runde 5, runde
    19), og de to første var formforsøk på samme rot. §9 K2 sier at
    tredje runde eskaleres, ikke lappes. Runde 5 skrev eskaleringen inn
    HER, i en docstring — og der ble den liggende uten issue og uten
    eiersvar, altså usynlig. #167 er stedet den bor nå, med A/B/C-valget
    formulert for eier; det henger sammen med #162 (inndata-artefakter
    har ingen bundet vei inn) og #166 (punktbinding i `KRAVGRENSER`).

    MERK hva det betyr for akseptarken: invarianten
    `bias.maling_mangler_for_digest = 0` teller manglende OPPFØRINGER,
    ikke manglende MÅLINGER. Den leser sterkere enn denne porten
    måler."""
    maaling = maalinger.get(image_digest)
    if maaling is None or maaling.image_digest != image_digest:
        raise Evalueringsfeil("bias_maling_mangler_for_digest",
                              image_digest)
    if not (isinstance(image_digest, str)
            and image_digest.startswith("sha256:")
            and _er_sha256(image_digest[7:])):
        raise Evalueringsfeil("bias_maling_ugyldig_digest", image_digest)
    if not _er_sha256(maaling.artefakt_sha256):
        raise Evalueringsfeil("bias_maling_uten_artefakt", image_digest)
    try:
        datetime.fromisoformat(str(maaling.ts).replace("Z", "+00:00"))
    except (TypeError, ValueError) as feil:
        raise Evalueringsfeil("bias_maling_uten_tidspunkt",
                              image_digest) from feil
    return maaling


def evaluer_kandidat(modell, soknadstekst: "str | list[str]",
                     kandidatfelter: dict[str, list[str]],
                     vekter: dict[str, int], *,
                     biasmaalinger: dict[str, Biasmaaling],
                     blinding_av: bool = False,
                     auditrad: dict | None = None) -> dict:
    """Én kandidat gjennom hele kontrakten: biasmåling for modellens
    digest (port 17), blindet input (port 16), skjemavaliderte funn
    (port 15). Modellen får ALDRI se råteksten når blinding står på —
    rekkefølgen her er selve invarianten, ikke en implementasjonsdetalj.

    Funnenes `kilde.start/slutt` indekserer `kildetekst` i returverdien —
    ALDRI `soknadstekst`. Blindingen endrer lengder, så de to er ikke
    samme koordinatsystem.
    """
    krev_biasmaaling(modell.image_digest, biasmaalinger)
    # ÉN VEI, IKKE TO (#174). `soknadstekst` tar imot én tekst ELLER
    # kandidatens dokumenter. Én streng ER enkeltdokument-tilfellet, så
    # normaliseringen her gir samme kode for begge — ikke en gren til som
    # kan drifte fra den andre.
    dokumenter = ([soknadstekst] if isinstance(soknadstekst, str)
                  else list(soknadstekst))
    blindede, avmaskering = blinding.evalueringsinput_dokumenter(
        dokumenter, kandidatfelter,
        blinding_av=blinding_av, auditrad=auditrad)
    tekst = blinding.SKJOT.join(blindede)
    # Grensene regnes av den SAMME skjøten sammensetningen brukte
    # (`blinding.SKJOT`). Drifter de to, peker grensene feil sted — og da
    # måler kryss-sitatporten noe annet enn den tror.
    grenser = blinding.dokumentgrenser(blindede)
    blinding.krev_blindet(tekst, avmaskering)
    svar = _krev_helt_svar(modell.vurder(tekst, vekter), vekter)
    # Porten BYGGER funnene: artefakten bærer det kanoniske funnet
    # `valider_funn` returnerer, aldri modellens egen dict (Codex P1).
    funn_kanonisk = [valider_funn(funn, tekst, grenser)
                     for funn in svar["funn"]]
    # `kildetekst` er strengen kildereferansene faktisk indekserer, og den
    # følger med artefakten (Codex P2). Blindingen ENDRER lengder — «Kari»
    # blir `[NAVN-1]` — så en [start:slutt] validert mot den blindede
    # teksten peker på noe annet i råsøknaden. Å sende offsetene videre
    # uten strengen de hører til, var å invitere til nettopp den
    # forvekslingen; her er referansen entydig, og verifiserbar av
    # mottakeren med samme snitt som `valider_funn` bruker.
    # Tom med vilje (#225): rapportskjemaet beholder feltet, så den
    # promoterte formen er uendret — men evalueringen bruker aldri
    # modelltid på spørsmål. De genereres ved innkalling (shortlist).
    return {"funn": funn_kanonisk,
            "oppfylt": dict(svar["oppfylt"]),
            "intervjusporsmal": [],
            "avmaskering": avmaskering,
            "kildetekst": tekst}
