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
    m = re.search(r"kilde_type[^;]*?CHECK[^;]*?IN\s*\(([^)]*)\)",
                  MIGRASJON, re.S)
    assert m, "fant ingen kilde_type-CHECK i 049_modulaksept.sql"
    lovlige = {s.strip().strip("'") for s in m.group(1).split(",")}
    assert "evidensfil" in lovlige, (
        f"§12.2 hviler på kildetypen «evidensfil», men CHECK-en tillater "
        f"bare {sorted(lovlige)}")


def test_samlet_merge_er_en_stabel_ikke_et_tidsvindu():
    """Codex P2 (#242): «samme vindu» skaper ingen kaskade. Etter første
    merge beveger `main` seg, og med strict status checks er alle andre
    `BEHIND`. Seks PR-er i ett vindu er seks sekvensielle CI-runder som
    starter samtidig — altså ingenting spart. Stabelen fjerner årsaken:
    hver gren inneholder den under seg, så ingen blir noen gang BEHIND."""
    p = _paragraf("12.3")
    assert "STABEL" in p
    assert "Ingen blir `BEHIND`" in p
    # Prisen må stå der taket står. En stabel av PR-er som fortsatt tar
    # runder er dyrere enn ingen stabel: hver endring nede rebaser alt over.
    assert "må alle over den" in p and "rebases" in p
