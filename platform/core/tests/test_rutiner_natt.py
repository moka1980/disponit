"""Strukturelle porter for §11-speilingen (Cursor P2-1/P2-2/P2-3 på #195).

§11-reglene ratifiseres i `docs/RUTINER.md`, men håndheves i
`.github/workflows/claude.yml` — og #193-rundene målte at de to flatene
glir fra hverandre én prosa-markør av gangen. Portene her holder dem
sammen maskinelt: en fremtidig endring som bryter koblingen skal bli et
rødt pytest-resultat, ikke et Cursor-funn fire runder senere.
"""
import re
from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
YML = (ROT / ".github" / "workflows" / "claude.yml").read_text(
    encoding="utf-8")
RUTINER = (ROT / "docs" / "RUTINER.md").read_text(encoding="utf-8")


def _jobb(navn: str) -> str:
    """Teksten fra jobbnøkkelen til neste jobb på samme innrykk."""
    m = re.search(rf"^  {navn}:\n(.*?)(?=^  \w[\w-]*:\n|\Z)", YML,
                  re.S | re.M)
    assert m, f"jobben {navn!r} finnes ikke i claude.yml"
    return m.group(0)


def test_mention_jobben_lekker_ikke_paa_workflow_run():
    """Cursor P2-1 (#195), målt i run 32820229936: mention-jobbens gamle
    `if` («alt som ikke er pull_request_review») falt igjennom på
    `workflow_run`, og «Svar @claude» startet parallelt med
    `cursor-pass-fulgt` på samme PR. Porten krever eksplisitt
    hendelses-allowlist, `@claude`-kroppsvakt og concurrency-gruppe."""
    mention = _jobb("mention")
    assert "github.event_name == 'issue_comment'" in mention
    assert "github.event_name == 'pull_request_review_comment'" in mention
    # Selve UTTRYKKET (kommentarer strippet): allowlisten må være lukket —
    # ingen negasjonsform som slipper nye hendelsestyper igjennom, og
    # ingen workflow_run-gren.
    uttrykk = "\n".join(l for l in mention.split("steps:")[0].splitlines()
                        if not l.lstrip().startswith("#"))
    assert "workflow_run" not in uttrykk, (
        "mention-if må aldri nevne/slippe workflow_run")
    assert "github.event_name != " not in uttrykk, (
        "mention-if må være en allowlist, ikke en negasjon")
    assert "contains(github.event.comment.body, '@claude')" in mention, (
        "kroppsvakten på bokstavelig @claude mangler")
    assert "concurrency:" in mention, "mention mangler concurrency-gruppe"


def test_dom_klasse_oppslaget_krever_sitatlinjen():
    """Cursor P2-2 (#195): en løs henvisning til «RUTINER §11» i
    cursor-pass-fulgt-prompten kunne resolvere til §11.2 (nattmandater)
    og leses som dekning for en eier-eskalering. Prompten må kreve
    §11.1-sitatlinjens eksakte form — og RUTINER må faktisk bære §11.1 i
    kraft, ellers lover workflowen en port dokumentet ikke har."""
    fulgt = _jobb("cursor-pass-fulgt")
    assert "dom-klasse: <id> · felt i #<nr> · <URL>" in fulgt, (
        "steg 5 må sitere sitatlinje-formen, ikke peke løst på §11")
    assert not re.search(r"RUTINER §11(?!\.\d)", fulgt), (
        "løs §11-henvisning uten delparagraf i cursor-pass-fulgt")
    assert re.search(r"Dom-klasse-gjenbruk — I KRAFT", RUTINER), (
        "claude.yml håndhever dom-klasse-porten, men RUTINER §11.1 sier"
        " ikke I KRAFT — flatene har glidd fra hverandre")


def test_verdikt_rekkevidden_er_speilet_begge_veier():
    """§11.1s verdikt-rekkevidde: RUTINER beskriver filsnitt-målingen,
    steg 3a i fikserjobben utfører den. Begge flatene må bære den."""
    fiks = _jobb("fiks-og-merge")
    assert "VERDIKT-REKKEVIDDEN" in fiks
    assert "compare/" in fiks, "3a må måle filsnittet, ikke anta det"
    assert "Verdikt-rekkevidden etter grenoppdatering" in RUTINER


def test_nattmandat_regelen_matcher_handleren():
    """Cursor P2-3 (#195): §11.2 hevder mention-jobben lytter på
    bokstavelig `@claude` — etter P2-1-fiksen håndhever workflow-if-en
    det faktisk. Dokumentpåstand og maskinvakt måles sammen."""
    assert "omtalen er `@claude`" in RUTINER
    mention = _jobb("mention")
    assert "contains(github.event.comment.body, '@claude')" in mention
