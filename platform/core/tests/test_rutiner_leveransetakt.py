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
    assert "OBLIGATORISK" in p and "binder FRAMOVER" in p, \
        "paragrafen sier ikke at punktet er obligatorisk"
    assert "AKSEPTERTE_GENERASJONER" in p, (
        "paragrafen forklarer ikke hvorfor kravet ikke står som `required`"
        " — neste leser setter det, og tvinger fram en ny aksept for m56")
    assert "blokkert" in p, \
        "det finnes ingen ærlig utvei for en modul uten flate"
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


def test_produktpunktet_kreves_av_hver_uakseptert_modul():
    """Eiers dom 29/8: punktet er OBLIGATORISK — men fra nå av.

    Hvorfor ikke `required` i skjemaet: repoets egen immutabilitet
    forbyr det. `AKSEPTERTE_GENERASJONER` fryser m02 og m56 til
    projeksjonen aksepten MÅLTE, og m56s manifesthash er i tillegg pinnet
    til release `wcag-r24` (`test_release_id_folger_manifestets_-
    projeksjon`). En ny sjekklistenøkkel der er en NY identitet som
    krever ny aksept — en deployment-handling. `required` ville tvunget
    frem nettopp den handlingen for å få suiten grønn, og en skjema-
    endring skal ikke kunne bestille en produksjonsdeployment.

    Kravet håndheves derfor her, mot repoets EGEN definisjon av «alt
    akseptert»: hver modul som ikke står i `AKSEPTERTE_GENERASJONER` skal
    bære punktet. Unntaket oppløser seg selv — neste aksept for m02/m56
    endrer manifestet uansett, og da treffer denne porten dem.

    Punktet er dessuten IKKE `ja` noe sted ennå, og det skal stå slik til
    et menneske faktisk har gått gjennom flaten og attestert notatet. Et
    `ja` uten `artefakt_sha256` fanges av `valider_artefakter`.

    MUTASJONEN SOM DREPER DENNE: fjern punktet fra m01, m37 eller m57 —
    eller legg en ny modul inn i katalogen uten det.
    """
    import yaml

    from manifestskjema import AKSEPTERTE_GENERASJONER

    moduler = sorted((ROT / "platform" / "modules").iterdir())
    manifester = [m for m in moduler if (m / "manifest.yaml").exists()]
    assert len(manifester) >= 5, \
        f"fant bare {len(manifester)} moduler — leser porten riktig sted?"

    # Frosset måles på MANIFESTSTIEN, ikke på katalognavnet: m56s nøkkel i
    # kartet er modulens `id` (`wcag_audit`), ikke mappenavnet
    # (`m56_wcag_audit`), og en navnesammenligning slapp den derfor
    # igjennom som «uakseptert». To registre, to navnerom — stien er det
    # ene begge er enige om.
    frosne_stier = {g["manifest"] for g in AKSEPTERTE_GENERASJONER.values()}
    manglende = []
    for katalog in manifester:
        rel = f"platform/modules/{katalog.name}/manifest.yaml"
        if rel in frosne_stier:
            continue                      # frosset av en registrert aksept
        data = yaml.safe_load(
            (katalog / "manifest.yaml").read_text(encoding="utf-8")) or {}
        if "produktgjennomgang_bestatt" not in (
                data.get("staging_sjekkliste") or {}):
            manglende.append(katalog.name)
    assert not manglende, (
        f"moduler uten produktakseptpunkt: {manglende}. §12.2 gjelder hver"
        " modul som ikke alt er frosset av en registrert aksept — en"
        " frivillig port hoppes over av nettopp den modulen som trenger"
        " den")

    # ... OG UNNTAKET MÅ VÆRE MEKANISK, ikke en navneliste her. Står en
    # modul over uten å være frosset, er den glemt — ikke unntatt.
    assert frosne_stier, \
        "ingen aksepterte generasjoner — unntaket ville vært ubegrenset"
    assert all((ROT / sti).exists() for sti in frosne_stier), \
        "en frosset modul finnes ikke lenger — unntakslisten er foreldet"
    # Og unntaket skal være LITE: står flertallet av katalogen som
    # frosset, er porten en dekorasjon.
    assert len(frosne_stier) < len(manifester), (
        "hver modul er frosset — da krever porten punktet av ingen")


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
    # ... og hvem som gjør det. `claude.yml` har bare `--squash` for ÉN
    # PR, så en paragraf som lot leseren tro at sløyfa stabler ville
    # beskrevet en vei som ikke finnes.
    assert "for hånd" in p or "operatørhandling" in p, (
        "paragrafen sier ikke at stabelen merges manuelt — workflowen har"
        " ingen retarget- eller rebase-vei")
    # Prisen må stå der taket står. En stabel av PR-er som fortsatt tar
    # runder er dyrere enn ingen stabel: hver endring nede rebaser alt over.
    assert "må alle over den" in p and "rebases" in p
