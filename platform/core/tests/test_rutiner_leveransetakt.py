"""Strukturelle porter for §12-leveransetakten (Codex P1/P2 på #242).

§12 ble først skrevet som ren prosa, og Codex målte at to av tre regler
ikke hadde noen vei ut i virkeligheten: rundetaket kunne ikke nå Codex
fordi Cursor-porten ga PASS bare uten P1/P2, og produktakseptpunktet
hadde ingen `kilde_type` det kunne lagres som. Det er den samme
defektklassen §11-portene ble skrevet for (`test_rutiner_natt`): en
ratifisert regel som ingen maskin håndhever, glir fra flaten den styrer.

Portene her holder §12 mot de tre flatene den faktisk hviler på —
Cursor-prompten, akseptmodellens kildetyper og §2s aktiveringskrav.
"""
import re
from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
RUTINER = (ROT / "docs" / "RUTINER.md").read_text(encoding="utf-8")
CURSOR_YML = (ROT / ".github" / "workflows" / "cursor-pre-codex.yml"
              ).read_text(encoding="utf-8")
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "049_modulaksept.sql").read_text(encoding="utf-8")


def _paragraf(nummer: str) -> str:
    """Teksten fra én §12-underparagraf til neste overskrift."""
    m = re.search(rf"^### {re.escape(nummer)} .*?$\n(.*?)(?=^#{{2,3}} |\Z)",
                  RUTINER, re.S | re.M)
    assert m, f"paragrafen {nummer} finnes ikke i RUTINER.md"
    return m.group(0)


def test_rundetaket_er_naabart_gjennom_cursor_porten():
    """Codex P1 (#242): «flytt P2 til et issue og gå videre» var
    uoppnåelig — `cursor-pre-codex.yml` gir PASS bare når ingen P1/P2
    står igjen, og `claude.yml` skriver `@codex review` bare på PASS. En
    regel uten vei ut av sløyfa er ingen regel. Porten krever at
    prompten selv kjenner parkeringen, med alle tre vilkårene."""
    assert "RUTINER §12.1" in CURSOR_YML, (
        "Cursor-prompten nevner ikke rundetaket — §12.1 er da bare prosa, "
        "og PASS-betingelsen slipper aldri en parkert P2 forbi.")
    prompt = CURSOR_YML[CURSOR_YML.index("RUTINER §12.1"):]
    prompt = prompt[:prompt.index("PASS-footer")]
    # De tre vilkårene MÅ stå samlet: uten (c) er parkering en glemsel
    # med bedre ordforråd, og uten (a) gjelder taket fra runde én.
    assert "RUNDE FIRE" in prompt
    assert "P2 eller P3" in prompt
    assert "issue-nummeret" in prompt
    # ... og RUTINER må liste ALLE FIRE vilkårene, ikke tre (Codex P2,
    # runde 6). Sto markøren og forfatterkravet bare i prompten, ville
    # dokumentet beskrevet en svakere regel enn den som håndheves — og
    # neste leser «rettet» prompten mot dokumentet.
    p = _paragraf("12.1")
    assert "PARKERT (RUTINER §12.1)" in p, \
        "§12.1 nevner ikke markøren som skiller beslutning fra rapport"
    assert "github-actions[bot]" in p and "gjengir nettopp DET funnet" in p, \
        "§12.1 nevner ikke forfatterkravet"
    assert "Mangler ett av de fire" in p, \
        "§12.1 teller fortsatt tre vilkår"


def test_p1_kan_aldri_parkeres_verken_i_prosa_eller_prompt():
    """Rundetaket gjelder funn som gjør arbeidet BEDRE, ikke funn som
    gjør det RIKTIG. Et tak som slapp P1 forbi ville vært en port som
    åpner en dør — så unntaket må stå begge steder, ikke bare i doku-
    mentet noen leser sjeldnest."""
    assert "P1 er aldri parkerbart" in _paragraf("12.1")
    assert "P1 kan ALDRI parkeres" in CURSOR_YML


def test_produktaksept_kommer_i_TILLEGG_til_paragraf_2():
    """Codex P1 (#242): første utgave sa at en modul er ferdig når flaten
    virker, «ikke når invariantene er bevist». §2 krever at HVERT punkt i
    manifest-sjekklista står `ja` før aktivering, så den formuleringen
    ville ha erklært moduler ferdige med ubeviste staging-invarianter.
    Vedtaket flytter rekkefølgen, ikke terskelen — og porten holder den
    forskjellen."""
    p = _paragraf("12.2")
    assert "i TILLEGG til invariantene, ikke i stedet for dem" in p
    assert "§2 gjelder uendret" in p
    assert "REKKEFØLGEN, ikke terskelen" in p


def test_produktakseptpunktets_kildetype_finnes_i_akseptmodellen():
    """Codex P2 (#242): et menneskeflippet punkt hadde ingen representa-
    sjon — `akseptkrav_punkt.kilde_type` er en lukket CHECK, og sjekk-
    lista er lukket mot nye felt. §12.2 navngir nå `evidensfil`, den ene
    kildetypen som bærer et menneskes lesning. Porten binder navnet i
    prosaen til CHECK-en i migrasjonen: forsvinner typen fra databasen,
    blir dokumentet rødt her — ikke i en Codex-runde et halvår senere."""
    p = _paragraf("12.2")
    assert "Punktet er en `evidensfil`" in p
    # OBLIGATORISK, OG HVORFOR IKKE `required` (eiers dom 29/8). Uten
    # begrunnelsen ville neste leser «rydde opp» ved å sette `required` og
    # dermed bestille en produksjonsdeployment for å få suiten grønn.
    assert "OBLIGATORISK" in p and "AKTIVERINGSPORT" in p, \
        "paragrafen sier ikke at punktet er obligatorisk"
    # HVORFOR IKKE `required`, med målingen. Uten den ville neste leser
    # «rydde opp» ved å sette `required` — og gjøre hver PR rød for m01,
    # m02 og m56. Feilformen må stå beskrevet, ikke bare den riktige.
    assert "aktiv_uten_bevis" in p and "if status ≠ aktiv" in p, (
        "paragrafen forklarer ikke hvorfor kravet er betinget — neste"
        " leser setter `required` og gjør hver PR rød")
    assert "blokkert" in p, \
        "det finnes ingen ærlig utvei for en modul uten flate"
    # UTVEIEN MÅ PEKE ET STED SOM FINNES (Cursor P2, runde 1). Første
    # utgave lovet at modulen «skriver `status: blokkert` med
    # `blokkert_av`» — men `blokkert` bor på SJEKKLISTEPUNKTET, ikke på
    # modulen, hvis enum er lukket. En modul som fulgte dokumentet ble
    # avvist av `valider_manifest`: en ærlig utvei som gjør CI rød er
    # ingen utvei. Porten binder de to enumene mot hverandre, så prosaen
    # ikke kan gli tilbake til modulnivået.
    import json

    skjema = json.loads(
        (ROT / "platform" / "core" / "manifest-skjema.json")
        .read_text(encoding="utf-8"))
    modulstatus = set(skjema["properties"]["status"]["enum"])
    punktstatus = set(skjema["$defs"]["punkt"]["properties"]["status"]["enum"])
    assert "blokkert" in punktstatus, (
        "§12.2s utvei hviler på punktstatusen «blokkert», men "
        f"skjemaet tillater bare {sorted(punktstatus)}")
    if "blokkert" not in modulstatus:
        assert "produktgjennomgang_bestatt" in p and "SJEKKLISTEPUNKTETS" in p, (
            "§12.2 lar «blokkert» stå uten å si at det er punktets status "
            f"— men modul-`status` tillater bare {sorted(modulstatus)}, så "
            "en modul som følger dokumentet feiler «Manifestskjema (v2 "
            "Del 7)»")
    m = re.search(r"kilde_type[^;]*?CHECK[^;]*?IN\s*\(([^)]*)\)",
                  MIGRASJON, re.S)
    assert m, "fant ingen kilde_type-CHECK i 049_modulaksept.sql"
    lovlige = {s.strip().strip("'") for s in m.group(1).split(",")}
    assert "evidensfil" in lovlige, (
        f"§12.2 hviler på kildetypen «evidensfil», men CHECK-en tillater "
        f"bare {sorted(lovlige)}")


def test_produktpunktet_er_FAKTISK_registrert_ikke_bare_mulig():
    """Codex P2 (#242, runde 2): en mulig kildetype er ingen port.

    Første utgave av porten over spurte bare om `evidensfil` fantes i
    CHECK-en. Den ville vært grønn i et repo der INGEN modul har et
    produktpunkt i det hele tatt — altså nøyaktig tilstanden §12.2 ble
    skrevet for å avslutte, og `aksepter_moduldeployment` ville akseptert
    hvert registrerte punkt mens den menneskelige porten manglet.

    Punktet må derfor finnes to steder: som en LOVLIG nøkkel i det delte
    manifestskjemaet (ellers kan ingen modul bære det), og som en FAKTISK
    oppføring i modulen som er under arbeid.

    Nøkkelen er FRIVILLIG i skjemaet, med vilje: en modul uten
    brukerflate har ingen flate å gå gjennom. Om den skal bli
    obligatorisk for alle 57 er eiers avgjørelse, ikke portens — men den
    modulen som HAR en flate skal ikke kunne glemme den.

    MUTASJONEN SOM DREPER DENNE: stryk punktet fra `m57_ats/manifest.yaml`
    (da er §12.2 en regel ingen modul følger), eller fra
    `manifest-skjema.json` (da kan ingen modul følge den).
    """
    import json
    import yaml

    skjema = json.loads(
        (ROT / "platform" / "core" / "manifest-skjema.json")
        .read_text(encoding="utf-8"))
    punkter = (skjema["properties"]["staging_sjekkliste"]["properties"])
    assert "produktgjennomgang_bestatt" in punkter, (
        "manifestskjemaet har ingen nøkkel for produktaksept — sjekklista"
        " er lukket mot nye felt, så INGEN modul kan bære punktet")

    manifest = yaml.safe_load(
        (ROT / "platform" / "modules" / "m57_ats" / "manifest.yaml")
        .read_text(encoding="utf-8"))
    punkt = manifest["staging_sjekkliste"].get("produktgjennomgang_bestatt")
    assert punkt, (
        "modulen som er under arbeid har ikke registrert produktpunktet —"
        " §12.2 er da en regel ingen modul følger, og aksepten kan gå"
        " igjennom med hvert registrerte punkt mens den menneskelige"
        " porten mangler")
    assert punkt["status"] in ("ja", "nei", "blokkert")
    assert punkt.get("notat"), \
        "punktet sier ikke hva brukeren skal kunne gjøre"


def test_produktpunktet_kreves_av_hver_modul_som_ikke_er_aktiv():
    """Eiers dom 29/8: obligatorisk — men det er en AKTIVERINGSPORT.

    Tre runder brukte på å finne den riktige formen, og hver feilform
    lærte noe:

    1. `required` i skjemaet → m01, m02 og m56 er `aktiv`, og
       `aktiv_uten_bevis` + CI-steget `Manifestskjema (v2 Del 7)` avviser
       en aktiv modul med ETT uavklart punkt. Hver PR ble rød for et
       punkt som ikke fantes da de ble aktivert. Alternativet — å skrive
       `ja` — er å dikte en gjennomgang som ikke er gjort.
    2. Sti-basert unntak mot `AKSEPTERTE_GENERASJONER` → stien ligger der
       for alltid, så unntaket «oppløste seg» aldri (Codex P2).
    3. Én test hardkodet til M-57 → enhver senere modul kunne bare la
       være å ha punktet (Codex P2).

    Formen som holder er §2s egen: en modul settes ikke `aktiv` før hvert
    punkt står `ja`. §12.2 legger ETT punkt til den porten, for hver
    modul som ennå ikke har passert den. Da er kjeden lukket for alt som
    aktiveres heretter — punktet må finnes, det må stå `ja`, og `ja`-et
    må bære bevis — uten å felle det som alt er aktivert.

    Regelen bor i SKJEMAET (betinget `if/then`), ikke i denne testen.
    Porten her måler at den faktisk står der og at den biter begge veier.

    MUTASJONEN SOM DREPER DENNE: fjern `if`/`then` fra manifest-skjemaet,
    eller stryk punktet fra m37/m57.
    """
    import copy
    import json

    import yaml

    from manifestskjema import valider_manifest

    skjema = json.loads(
        (ROT / "platform" / "core" / "manifest-skjema.json")
        .read_text(encoding="utf-8"))
    assert "if" in skjema and "then" in skjema, (
        "skjemaet har ingen betinget regel — kravet ville da bare finnes"
        " i en test, og enhver senere modul kunne utelate punktet")
    assert skjema["then"]["properties"]["staging_sjekkliste"]["required"] \
        == ["produktgjennomgang_bestatt"]

    moduler = sorted((ROT / "platform" / "modules").iterdir())
    manifester = [m for m in moduler if (m / "manifest.yaml").exists()]
    assert len(manifester) >= 5, \
        f"fant bare {len(manifester)} moduler — leser porten riktig sted?"

    ikke_aktive = []
    for katalog in manifester:
        data = yaml.safe_load(
            (katalog / "manifest.yaml").read_text(encoding="utf-8")) or {}
        if data.get("status") == "aktiv":
            continue
        ikke_aktive.append(katalog.name)
        assert "produktgjennomgang_bestatt" in (
            data.get("staging_sjekkliste") or {}), (
            f"{katalog.name} er ikke aktiv og mangler produktpunktet —"
            " den kan da aktiveres uten menneskelig gjennomgang")
    assert ikke_aktive, (
        "ingen modul er under arbeid — porten måler ingenting, og en ny"
        " modul ville sluppet inn uten at noe ble rødt")

    # OG REGELEN MÅ BITE BEGGE VEIER, målt gjennom validatoren og ikke
    # bare lest ut av JSON-en.
    m57 = yaml.safe_load(
        (ROT / "platform" / "modules" / "m57_ats" / "manifest.yaml")
        .read_text(encoding="utf-8"))
    uten = copy.deepcopy(m57)
    del uten["staging_sjekkliste"]["produktgjennomgang_bestatt"]
    assert valider_manifest(uten), \
        "en modul under utvikling uten produktpunktet validerte"
    bestefar = copy.deepcopy(uten)
    bestefar["status"] = "aktiv"
    assert not valider_manifest(bestefar), (
        "en ALT AKTIV modul uten punktet ble avvist — da er hver PR rød"
        " for m01, m02 og m56, for et punkt som ikke fantes da de ble"
        " aktivert")


#: De tre modulene som var `aktiv` da §12.2 ble innført (29/8). Settet er
#: DATERT og skal bare krympe: hver gang en av dem gjennomgås og får
#: punktet, går den ut herfra. Det er ikke en unntaksliste noen kan legge
#: til i — porten under krever at hvert navn fortsatt oppfyller begge
#: betingelsene for å STÅ her.
AKTIVE_UTEN_PRODUKTPUNKT_29_08 = {
    "m01_policy", "m02_revisjonslogg", "m56_wcag_audit"}


def test_ingen_NY_modul_kan_aktiveres_uten_produktpunktet():
    """Codex P2 (runde 5): aktivering og sletting i samme endring.

    Den betingede skjemaregelen slutter å kreve punktet i det øyeblikket
    `status` blir `aktiv` — altså nøyaktig ved overgangen porten skal
    bite. Én endring kunne sette `status: aktiv` OG slette punktet, og da
    godtar `valider_manifest` fraværet mens `aktiv_uten_bevis` ikke har
    noe å klage på.

    Skjemaet kan ikke se historikk, så porten gjør det: en `aktiv` modul
    UTEN punktet må være én av de tre som alt var aktive da §12.2 ble
    innført. Settet er datert og skal bare krympe.

    MUTASJONEN SOM DREPER DENNE: sett en modul `aktiv` og slett punktet
    — eller legg et nytt navn i `AKTIVE_UTEN_PRODUKTPUNKT_29_08`.
    """
    import yaml

    moduler = sorted((ROT / "platform" / "modules").iterdir())
    manifester = [m for m in moduler if (m / "manifest.yaml").exists()]
    uten = set()
    for katalog in manifester:
        data = yaml.safe_load(
            (katalog / "manifest.yaml").read_text(encoding="utf-8")) or {}
        if "produktgjennomgang_bestatt" in (data.get("staging_sjekkliste")
                                            or {}):
            continue
        assert data.get("status") == "aktiv", (
            f"{katalog.name} mangler produktpunktet uten å være aktiv —"
            " skjemaets betingede regel skulle felt den")
        uten.add(katalog.name)
    nye = uten - AKTIVE_UTEN_PRODUKTPUNKT_29_08
    assert not nye, (
        f"{sorted(nye)} er aktiv(e) uten produktpunkt, og var det ikke"
        " 29/8 — en endring kan ha satt `aktiv` og slettet punktet i"
        " samme slengen, som er nøyaktig overgangen §12.2 skal bite ved")
    # SETTET SKAL BARE KRYMPE. Står et navn her som ikke lenger oppfyller
    # begge betingelsene, er listen foreldet — og en foreldet unntaksliste
    # er et hull som ser ut som en regel.
    foreldet = AKTIVE_UTEN_PRODUKTPUNKT_29_08 - uten
    assert not foreldet, (
        f"{sorted(foreldet)} står i unntakssettet uten å trenge det"
        " lenger — fjern dem")


def test_produktpunktets_ja_krever_bevis():
    """Codex P1: gjennomgangen kunne «fullføres» ved å skrive `ja`.

    `valider_artefakter` hopper eksplisitt over et `ja` som mangler
    `krav_id` (`manifestskjema.py`), og punktene ble lagt inn uten. Å
    bytte `nei` til `ja` ville dermed passert både skjema og evidenskjede
    — uten `attester_evidensfil`, uten sha256, uten en aktør. Den
    menneskelige porten var en streng noen kunne skrive.

    `produktpunkt`-defen krever nå `krav_id`, `artefakt` og
    `artefakt_sha256` når status er `ja`. Da griper husets egen
    evidenskjede: `valider_artefakter` åpner fila, verifiserer hashen mot
    innholdet og måler tallene mot `KRAVGRENSER`.

    MUTASJONEN SOM DREPER DENNE: fjern `if`/`then` fra `produktpunkt`.
    """
    import copy

    import yaml

    from manifestskjema import valider_manifest

    m57 = yaml.safe_load(
        (ROT / "platform" / "modules" / "m57_ats" / "manifest.yaml")
        .read_text(encoding="utf-8"))
    bart_ja = copy.deepcopy(m57)
    bart_ja["staging_sjekkliste"]["produktgjennomgang_bestatt"] = {
        "status": "ja"}
    feil = valider_manifest(bart_ja)
    assert feil, "et `ja` uten bevis validerte — porten er en streng"
    for krevd in ("krav_id", "artefakt", "artefakt_sha256"):
        assert any(krevd in f for f in feil), \
            f"{krevd} kreves ikke av et `ja` på produktpunktet: {feil}"

    # ... og `nei` skal fortsatt kunne stå bart. Ellers ville en modul
    # under arbeid vært tvunget til å love bevis den ikke har ennå.
    bart_nei = copy.deepcopy(m57)
    bart_nei["staging_sjekkliste"]["produktgjennomgang_bestatt"] = {
        "status": "nei"}
    assert not valider_manifest(bart_nei), \
        "et ærlig `nei` uten artefakt ble avvist"


def test_samlet_merge_er_en_stabel_ikke_et_tidsvindu():
    """Codex P2 (#242): «samme vindu» skaper ingen kaskade. Etter første
    merge beveger `main` seg, og med strict status checks er alle andre
    `BEHIND`. Seks PR-er i ett vindu er seks sekvensielle CI-runder som
    starter samtidig — altså ingenting spart. Stabelen fjerner årsaken:
    hver gren inneholder den under seg, så ingen blir noen gang BEHIND."""
    p = _paragraf("12.3")
    assert "STABEL" in p
    # OG MERGEMÅTEN, ikke bare stabelen (Codex P2, runde 2). `--squash`
    # lager en NY commit på `main` som ikke er forfar til grenen over, så
    # en stabel som merges nedenfra og opp blir `BEHIND` på nøyaktig
    # samme måte som uten stabel. Regelen er ubrukelig uten dette leddet.
    assert "--rebase" in p and "--squash" in p, (
        "paragrafen sier ikke HVORDAN stabelen merges — med workflowens"
        " `--squash` er stabelen ingen kaskade")
    assert "ovenfra" in p, \
        "stabelen merges nedenfra og opp — da rives ancestry ved hver squash"
    # RETARGETINGEN (Codex P2, runde 3). `--rebase` spiller commitene inn
    # på PR-ens BASE, og toppens base er grenen under den — uten
    # `gh pr edit --base main` merges toppen inn i mellomgrenen og `main`
    # står stille. Uten dette leddet beskriver paragrafen en operasjon som
    # ikke gjør det den sier.
    assert "gh pr edit" in p and "--base main" in p, (
        "toppen retargetes aldri til `main` — `--rebase` ville da bare"
        " merget den inn i grenen under")
    # Rekkefølgen måles i KOMMANDOBLOKKEN, ikke i prosaen: paragrafen
    # nevner `gh pr merge --squash` LENGER OPPE, i forklaringen av
    # hvorfor squash river stabelen, og en naiv `index` leste den.
    blokk = p[p.index("```"):p.index("```", p.index("```") + 3)]
    assert "gh pr edit" in blokk and "gh pr merge" in blokk, \
        "kommandoene står ikke samlet i én blokk leseren kan følge"
    assert blokk.index("gh pr edit") < blokk.index("gh pr merge"), \
        "retargetingen står etter mergen"
    assert "--rebase" in blokk, "kommandoblokken merger ikke med --rebase"
    # PINNET TIL HODET CODEX SÅ (Codex P1, runde 4). Mellom retargeting og
    # merge er det et vindu, og en push der ville merget et hode ingen av
    # stabelens verdikter dekker — sjekk-så-handle-hullet §11.1 finnes
    # for, og som den automatiske veien alt lukker med samme flagg.
    assert "--match-head-commit" in blokk, (
        "den manuelle stabelmergen pinner ikke hodet — den omgår"
        " invarianten §11.1 håndhever på den automatiske veien")
    assert blokk.index("headRefOid") < blokk.index("gh pr edit"), \
        "SHA-en leses etter retargetingen — da er det ikke hodet Codex så"
    # ... og hvem som gjør det. `claude.yml` har bare `--squash` for ÉN
    # PR, så en paragraf som lot leseren tro at sløyfa stabler ville
    # beskrevet en vei som ikke finnes.
    # VERDIKTENE ER SHA-BUNDET (Codex P2, runde 5). Rebasing skriver
    # commitene om, så en stabel bygget ETTER portene har passert bærer
    # verdikter som peker på commits som ikke lenger er hodet.
    assert "FØR de siste verdiktene" in p, (
        "paragrafen sier ikke NÅR stabelen bygges — bygges den etter"
        " portene, er hvert verdikt bundet til en commit som ikke finnes"
        " som hode lenger")
    assert "for hånd" in p or "operatørhandling" in p, (
        "paragrafen sier ikke at stabelen merges manuelt — workflowen har"
        " ingen retarget- eller rebase-vei")
    # Prisen må stå der taket står. En stabel av PR-er som fortsatt tar
    # runder er dyrere enn ingen stabel: hver endring nede rebaser alt over.
    assert "må alle over den" in p and "rebases" in p
