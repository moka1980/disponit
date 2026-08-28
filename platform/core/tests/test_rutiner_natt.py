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
CURSOR_YML = (ROT / ".github" / "workflows" / "cursor-pre-codex.yml"
              ).read_text(encoding="utf-8")
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


def _fiksforsok() -> list[str]:
    """De tre forsøksromptene i fikserjobben (runde 1, 2 og 3).

    Cursor P2-1 på #198: §11.1-portene sto bare i runde 1 — et forsøk
    som fortsatte etter timeout kunne merge uten dem. Splitten her gjør
    hvert forsøk til sin egen målbare flate."""
    fiks = _jobb("fiks-og-merge")
    deler = re.split(r"FORTSETTELSESFORSØK", fiks)
    assert len(deler) == 3, "ventet runde 1 + to fortsettelsesforsøk"
    return deler


def test_verdikt_rekkevidden_er_speilet_begge_veier():
    """§11.1s verdikt-rekkevidde: RUTINER beskriver filsnitt-målingen,
    fikserjobben utfører den — i ALLE TRE forsøkene (Cursor P2-1 på
    #198: runde 2/3 kunne ellers merge på et brukt verdikt etter en
    timeout i runde 1)."""
    for i, forsok in enumerate(_fiksforsok(), 1):
        assert "VERDIKT-REKKEVIDDEN" in forsok, f"forsøk {i} mangler porten"
        assert "compare/" in forsok, (
            f"forsøk {i} må måle filsnittet, ikke anta det")
    assert "Verdikt-rekkevidden etter grenoppdatering" in RUTINER


def test_dom_klasse_porten_i_alle_tre_forsokene():
    """Cursor P2-1/P2-5 på #198 + P2-2 runde 6: dom-klasse-porten må stå
    i hvert av de tre forsøkene MED SAMME STYRKE — sitatlinje, åpnet og
    lest dom, ordrett mekanisme/utfall, og forfatterkravet (P1-1 runde
    6: formen er billig å forfalske, forfatteren gjør den umulig). En
    timeout skal aldri fortynne porten."""
    for i, forsok in enumerate(_fiksforsok(), 1):
        assert "DOM-KLASSE-PORTEN" in forsok, f"forsøk {i} mangler porten"
        assert "dom-klasse: <id> · felt i #<nr> · <URL>" in forsok, (
            f"forsøk {i} mangler sitatlinje-formen")
        # prompt-tekst brytes over linjer — normaliser før substrings
        norm = " ".join(forsok.lower().split())
        assert "åpne" in norm and "les dommen" in norm, (
            f"forsøk {i} mangler lese-kravet")
        assert "mekanisme" in forsok and "utfall" in forsok, (
            f"forsøk {i} mangler treff-kravet")
        assert "`user.login`" in forsok and "moka1980" in forsok, (
            f"forsøk {i} mangler forfatterkravet")
    # …og broens steg 5 bærer det samme forfatterkravet.
    fulgt = _jobb("cursor-pass-fulgt")
    assert "`user.login` er `moka1980`" in fulgt
    assert "annen forfatter" in fulgt
    assert "user.login" in RUTINER and "forfatter" in RUTINER


def test_broen_er_per_pr_og_feiler_hoyt():
    """Cursor P2-3/P2-4 på #198 + målt no-op (run 32822713282):

    * concurrency må være PER PR (needs-output), aldri per workflow_run-id
      alene — to pass på samme PR skal køes, ikke kjøre parallelt;
    * artefakt-mangel skal feile RØDT, aldri `exit 0` til grønn no-op;
    * runde-cursor-steget må bære samme verktøyflate som fikserjobben —
      uten `--allowedTools` var broen en no-op med grønn hake."""
    fulgt = _jobb("cursor-pass-fulgt")
    hent = _jobb("pr-fra-pass")
    assert "claude-pr-" in fulgt, "concurrency må være per PR"
    assert "needs.pr-fra-pass.outputs.nummer" in fulgt
    assert not re.search(r"group:\s*claude-cursorfulgt-\$\{\{\s*github\.event\.workflow_run\.id",
                         fulgt), "workflow_run.id som eneste nøkkel"
    assert "exit 1" in hent, "artefakt-mangel må feile rødt"
    # `exit 0` er lovlig i NØYAKTIG én gren (Cursor P2-3 runde 4): når
    # pass-jobben ble hoppet over — da finnes det per konstruksjon ingen
    # artifakt og ingenting å følge opp. Grenen må være vaktet av en
    # faktisk skipped-sjekk, ikke stå fritt.
    assert "skipped" in hent, "skipped-oppstrøm-grenen mangler"
    # tell bare i UTTRYKKSLINJENE — forklarende kommentarer nevner exit 0
    hent_uttrykk = "\n".join(l for l in hent.splitlines()
                             if not l.lstrip().startswith("#"))
    assert hent_uttrykk.count("exit 0") == 1, (
        "bare skipped-grenen får exit 0")
    assert "--allowedTools" in fulgt, "broen mangler verktøyflaten"
    # Uttrykkslinjene alene — #197s forklarende kommentar NEVNER flagget.
    uttrykk = "\n".join(l for l in fulgt.splitlines()
                        if not l.lstrip().startswith("#"))
    assert "continue-on-error" not in uttrykk


def test_paragraf_10_beskriver_broen_ikke_mention():
    """Cursor P2-2 på #198: §10 hevdet at footeren vekker mention-jobben —
    men GITHUB_TOKEN-kommentarer sender ingen hendelser den kan se
    (#188). Dokumentet må peke på workflow_run-broen."""
    m = re.search(r"^## 10\. .*?(?=^## \d)", RUTINER, re.S | re.M)
    assert m, "§10 finnes ikke"
    p10 = m.group(0)
    assert "workflow_run" in p10 and "cursor-pass-fulgt" in p10
    assert "Footer vekker Claude" not in p10, (
        "§10 påstår fortsatt at footeren/mention er broen")


def test_nattmandat_regelen_matcher_handleren():
    """Cursor P2-3 (#195): §11.2 hevder mention-jobben lytter på
    bokstavelig `@claude` — etter P2-1-fiksen håndhever workflow-if-en
    det faktisk. Dokumentpåstand og maskinvakt måles sammen."""
    assert "omtalen er `@claude`" in RUTINER
    mention = _jobb("mention")
    assert "contains(github.event.comment.body, '@claude')" in mention


def test_pass_ekvivalensregelen_er_ratifisert_der_den_brukes():
    """Cursor P2-2 runde 2 (#198): regelen sto bare i bro-prompten —
    en agent kunne behandle FUNN som PASS etter en uratifisert regel.
    Nå må strengen finnes i BÅDE RUTINER §10 og bro-prompten, eller i
    ingen av dem."""
    i_rutiner = "PASS-ekvivalensregelen" in RUTINER
    i_broen = "PASS-ekvivalensregelen" in _jobb("cursor-pass-fulgt")
    assert i_rutiner == i_broen, "regelen finnes bare på én av flatene"
    assert i_rutiner, "regelen er fjernet begge steder — da må også"         " §10-porten og bro-steg 3 skrives om"
    assert "test-negativer" in RUTINER


def test_broen_har_cancelled_gren():
    """Cursor P2-3 runde 2 (#198): et avbrutt pass har ingen funnliste —
    broen må ha en eksplisitt cancelled-gren (semantikken er skjerpet i
    runde 6: alltid stille stopp, se egen port)."""
    assert "cancelled" in _jobb("cursor-pass-fulgt"), (
        "bro-prompten mangler cancelled-grenen")


def test_broen_binder_passet_til_head_sha():
    """Cursor P2-4 runde 2 (#198): et PASS gjelder commiten det ble
    kjørt på. Steg 4 må sammenligne pass-kommentarens SHA-linje med
    `headRefOid` før `@codex review` — ellers kan en push i vinduet
    sende Codex kode Cursor aldri så."""
    fulgt = _jobb("cursor-pass-fulgt")
    assert "headRefOid" in fulgt, "steg 4 mangler SHA-sammenligningen"
    assert "SHA-linje" in fulgt or "SHA: " in fulgt


def test_alle_skrivende_claude_jobber_deler_pr_mutex():
    """Cursor P2-5 runde 2 (#198): tre disjunkte concurrency-grupper lot
    to skrivende Claude-instanser kjøre parallelt på samme PR. Alle tre
    jobbene må dele `claude-pr-<nummer>`-gruppen."""
    for navn in ("fiks-og-merge", "mention", "cursor-pass-fulgt"):
        jobb = _jobb(navn)
        m = re.search(r"group:\s*(\S+)", jobb)
        assert m, f"{navn} mangler concurrency-gruppe"
        assert m.group(1).startswith("claude-pr-"), (
            f"{navn} står utenfor felles-mutexen: {m.group(1)}")


def test_cursor_workflowens_kontrakt_beskriver_broen():
    """Cursor P2-1 runde 2 (#198) + P3: kontraktkommentaren i
    cursor-pre-codex.yml hevdet at footeren vekker mention-jobben.
    Filen må beskrive workflow_run-broen — og aldri gjeninnføre
    mention-påstanden."""
    assert "workflow_run" in CURSOR_YML
    assert "mention) våkner" not in CURSOR_YML
    assert "cursor-pass-fulgt" in CURSOR_YML


def test_mention_har_samme_verktoyflate_som_broen():
    """Cursor P1 runde 3 (#198) — klassen fra run 32822713282, andre
    instans: broen fikk verktøyflaten i runde 1, mention ble stående
    naken. En handler uten verktøy er en no-op med grønn hake."""
    mention = _jobb("mention")
    assert "--allowedTools" in mention, "mention mangler verktøyflaten"
    assert "claude_args" in mention


def test_mention_forbyr_codex_review():
    """Cursor P2-4 runde 3 (#198): mention er §11.2-handleren, aldri
    Cursor-broen — en kommentar som LIGNER en PASS-footer skal ikke
    kunne gi Codex-adgang utenom SHA-bindingen i cursor-pass-fulgt."""
    mention = _jobb("mention")
    assert "ALDRI `@codex review`" in mention


def test_broen_validerer_ikke_tom_artifakt():
    """Cursor P2-3 runde 3 (#198): en TOM pr-nummer-fil ga tom output →
    oppfølgingsjobben hoppet over → grønn kjøring uten oppfølging.
    Valideringen må felle tomhet like høyt som fravær."""
    hent = _jobb("pr-fra-pass")
    assert "tr -d" in hent and "::error::" in hent, (
        "pr-fra-pass mangler ikke-tom-validering")


def test_cancelled_broen_dupliserer_aldri():
    """Cursor P2-2 runde 3 → P2-1 runde 6 (#198): først «sjekk for nyere
    run», så målte runde 6 at selve oppslaget hadde et race-vindu —
    sluttformen er at cancelled ALDRI re-køer (egen port under)."""
    fulgt = _jobb("cursor-pass-fulgt")
    assert "re-køes alltid" not in fulgt
    assert "kaskaden" in fulgt


def test_sha_bindingen_sjekkes_ogsaa_etter_ci_ventingen():
    """Cursor P2-5 runde 3 (#198): CI-ventingen er et vindu — SHA-en må
    leses PÅ NYTT rett før `@codex review`, ellers binder passet en
    HEAD som alt er forlatt."""
    fulgt = _jobb("cursor-pass-fulgt")
    assert "PÅ NYTT" in fulgt and "RETT FØR" in fulgt


def test_mention_krever_eier_som_avsender():
    """Cursor P1-1 runde 4 (#198): offentlig repo + skrivende agent =
    forfatter-porten er selve sikkerheten. BEVISST uten
    `issue.pull_request`-krav: §11.2-mandater bor også på rene issues —
    eier-porten alene lukker angrepsflaten."""
    mention = _jobb("mention")
    uttrykk = "\n".join(l for l in mention.split("steps:")[0].splitlines()
                        if not l.lstrip().startswith("#"))
    assert "github.event.comment.user.login == 'moka1980'" in uttrykk, (
        "mention-if mangler forfatter-porten")
    assert "contains(github.event.comment.body, '@claude')" in uttrykk


def test_mention_forbyr_merge():
    """Cursor P1-2 runde 4 (#198): merge eies av fiks-og-merge alene —
    et nattmandat («merg denne») skal aldri kunne hoppe over §10-kjeden
    via mention-jobbens verktøyflate."""
    mention = _jobb("mention")
    assert "MERGE ALDRI" in mention
    assert "gh pr merge` er forbudt" in mention


def test_mention_har_driftsparity_med_soskenjobbene():
    """Cursor P2-4 runde 4 (#198): samme tre vern som søskenjobbene —
    utsjekk, workflow-forbud, upålitelige data."""
    mention = _jobb("mention")
    assert "gh pr checkout" in mention
    assert "IKKE rediger" in mention and ".github/workflows" in mention
    assert "upålitelige data" in mention


def _uttrykk(jobbtekst: str) -> str:
    """if-/config-linjene uten kommentarer."""
    return "\n".join(l for l in jobbtekst.splitlines()
                     if not l.lstrip().startswith("#"))


def test_verdikt_portene_bruker_eksakte_logins():
    """Cursor P1-1 runde 5 (#198): `contains(login, 'codex')` matchet
    `codex-hacker` — på et offentlig repo var det en ureviewet-merge-vei.
    Portene må sammenligne eksakt login, aldri delstreng, og eierens
    review-gren må kreve `approved` (P2-2)."""
    fiks = _uttrykk(_jobb("fiks-og-merge").split("steps:")[0])
    assert "== 'chatgpt-codex-connector[bot]'" in fiks
    assert not re.search(r"contains\([^)]*login[^)]*'codex'", fiks), (
        "substring-match på login er tilbake")
    assert "github.event.review.state == 'approved'" in fiks


def test_broen_krever_kjent_aktor_men_slipper_pr_utloste_pass():
    """Cursor P1-3 runde 5 + P2-1 runde 7 (#198): kommentar-utløste pass
    portes på actor-allowlisten (eier + sløyfas bot); pull_request-
    utløste pass (ready_for_review/label — alt portet av write-tilgang)
    skal ALLTID rutes, ellers stopper §10-automatikken stille for
    legitime skrivere."""
    hent = _uttrykk(_jobb("pr-fra-pass").split("steps:")[0])
    assert "workflow_run.actor.login == 'moka1980'" in hent
    assert "workflow_run.actor.login == 'claude[bot]'" in hent
    assert "workflow_run.event == 'pull_request'" in hent, (
        "pull_request-utløste pass må forbi actor-porten")
    assert "== 'moka1980'" in CURSOR_YML and "== 'claude[bot]'" in CURSOR_YML
    assert "github.actor != 'github-actions[bot]'" not in CURSOR_YML, (
        "nektelseslisten er tilbake — porten er en allowlist")


def test_bro_prompt_har_failure_gren():
    """Cursor P2-2 runde 7 (#198): et pass som endte generisk `failure`
    uten funnliste skal aldri la agenten nå steg 3/4 — stopp med
    kommentar; Cursor-ute-regelen krever DOKUMENTERT transportfeil."""
    fulgt = _jobb("cursor-pass-fulgt")
    norm = " ".join(fulgt.split())
    assert "GENERISK `failure`" in fulgt
    assert "aldri generisk failure" in norm
    assert "nås ALDRI uten et ekte pass" in norm


def test_passet_er_bundet_til_forfatteren():
    """Cursor P1-2 runde 5 (#198): SHA-linja gjør en forfalskning billig
    — forfatteren gjør den umulig. Broen må kreve github-actions[bot]
    som avsender av passet, ikke bare formen."""
    fulgt = _jobb("cursor-pass-fulgt")
    assert "POSTET AV `github-actions[bot]`" in fulgt
    assert "ALDRI et pass" in fulgt


def test_cancelled_stopper_alltid_stille():
    """Cursor P2-1 runde 6 → P1-1 runde 9 (#198): cancelled-grenen (2a)
    stopper stille og re-køer aldri; re-kø bor i transportgrenen (2b)
    alene — se de disjunkte gren-portene."""
    g = _steg2_grener()
    assert "stopp STILLE" in g["2a"] and "aldri re-kø" in g["2a"]


def test_mandatutstederen_er_speilet_begge_veier():
    """Cursor P2-1 runde 8 (#198): maskinporten (kun moka1980) må stå i
    §11.2 også — regel og port glir ellers fra hverandre (#193-klassen)."""
    assert "KUN eier (`moka1980`)" in RUTINER
    mention = _uttrykk(_jobb("mention").split("steps:")[0])
    assert "github.event.comment.user.login == 'moka1980'" in mention


def _steg2_grener() -> dict:
    """De fire disjunkte grenene i bro-steg 2 (P1-1 runde 9: transport og
    cancelled delte én setning og motsatte seg selv — grenene må være
    separate og entydige)."""
    fulgt = _jobb("cursor-pass-fulgt")
    grener = {}
    for navn in ("2a", "2b", "2c", "2d"):
        m = re.search(rf"{navn}\. (.*?)(?=2[a-d]\. |Ute-regelen \(2c\))",
                      " ".join(fulgt.split()))
        assert m, f"gren {navn} mangler i steg 2"
        grener[navn] = m.group(1)
    return grener


def test_steg2_grenene_er_disjunkte_og_entydige():
    """Cursor P1-1 runde 9 (#198): cancelled må aldri dele setning med
    transport; transport re-køer første gang, går ute-veien andre gang;
    cancelled re-køer aldri."""
    g = _steg2_grener()
    assert "cancelled" in g["2a"] and "aldri re-kø" in g["2a"]
    assert "FØRSTE gang" in g["2b"] and "@cursor review" in g["2b"]
    assert "cancelled" not in g["2b"]
    assert "ANDRE gang" in g["2c"] and "@codex review" in g["2c"]
    assert "intet pass å binde" in g["2c"]
    assert "failure" in g["2d"] and "@moka1980" in g["2d"]


def test_cursor_ute_regelen_har_samme_semantikk_begge_steder():
    """Cursor P2-2 runde 8 (#198): §10 og bro-steg 2 må være enige —
    re-kø én gang, andre transportfeil går rett på @codex review uten
    pass/SHA-binding (det finnes intet pass å binde)."""
    fulgt = " ".join(_jobb("cursor-pass-fulgt").split())
    assert "RETT på `@codex review`" in fulgt
    assert "intet pass å binde" in fulgt
    r10 = " ".join(RUTINER.split())
    assert "re-køes passet ÉN gang" in r10
    assert "intet pass å binde" in r10


def test_skipped_sjekken_krever_ikketom_jobbliste():
    """Cursor P2-3 runde 8 (#198): jq-ens all() er true for tom liste —
    en feilet run-view ville gitt stille exit 0 (#188-klassen)."""
    hent = _jobb("pr-fra-pass")
    assert "(.jobs | length) > 0" in hent


def test_codex_grenen_krever_pull_request():
    """Cursor P2-4 runde 8 (#198): issue_comment-grenen i fiks-og-merge
    må kreve at kommentaren står på en PR — en codex-formet kommentar på
    ren issue skal aldri starte en skrivende agent."""
    fiks = _uttrykk(_jobb("fiks-og-merge").split("steps:")[0])
    assert "github.event.issue.pull_request" in fiks


def test_broen_avviser_fork_repo():
    """Codex P1-1 runde 10 (#198): workflow_run-kjøringen bærer
    base-repoets secrets — et pass fra en fork-PR skal aldri nå den
    privilegerte broen. Head-repoet må være vårt eget."""
    hent = _uttrykk(_jobb("pr-fra-pass").split("steps:")[0])
    assert ("workflow_run.head_repository.full_name == github.repository"
            in hent), "fork-vakten mangler"


def test_compare_trunkering_feiler_stengt():
    """Codex P2 runde 10 (#198): GitHub trunkerer compare-fillisten ved
    300 — et utelatt overlapp ville gitt falskt tomt snitt og bevart et
    brukt verdikt. Alle tre forsøkene og RUTINER bærer vakten."""
    for i, forsok in enumerate(_fiksforsok(), 1):
        norm = " ".join(forsok.split())
        assert "300+" in norm, f"forsøk {i} mangler trunkering-vakten"
    assert "300+ filer" in RUTINER


def test_asymmetrien_pass_vs_verdikt_er_dokumentert():
    """Codex P1-3 runde 10 (#198): §10s eksakte pass-SHA og §11.1s
    filsnitt-rekkevidde er BEVISST asymmetri (fornyelsesprisen er ulik)
    — dokumentert, ikke en motsigelse."""
    assert "Asymmetrien mot §10s pass-binding er BEVISST" in RUTINER
    assert "billig og kvotefritt" in RUTINER
    assert "dyr og kvotebelagt" in RUTINER


def test_kvoten_filteret_er_prefiks_ikke_contains():
    """Cursor P2-2 runde 11 (#198): filteret som stopper ureviewet merge
    ved tom lommebok (målt 2026-08-15) hadde ingen port — en «harmløs»
    cleanup kunne fjernet det med grønn CI. Prefiks-formen er kravet:
    contains ville også kastet ekte verdikter som DISKUTERER kvoter."""
    fiks = _uttrykk(_jobb("fiks-og-merge").split("steps:")[0])
    assert ("startsWith(github.event.comment.body, 'Codex usage limits"
            " have been reached')" in fiks), "kvote-filteret mangler"
    assert "contains(github.event.comment.body, 'usage limits')" not in fiks


def _grener() -> list[str]:
    """`if`-uttrykkets grener, én per `||`.

    Den gamle formen splittet på `pull_request_review'` og leste `[1]`.
    Med en gren til på samme hendelse ville den lest EIERENS gren og
    konkludert at boten ikke er der — grønt uansett hva botens gren
    inneholder. En port som slutter å måle det den heter, er verre enn
    ingen port, så grenene deles her og navngis hver for seg.
    """
    fiks = _uttrykk(_jobb("fiks-og-merge").split("steps:")[0])
    uttrykk = fiks.split("if: >-", 1)[1].split("runs-on:", 1)[0]
    return [" ".join(g.split()) for g in uttrykk.split("||")]


def test_review_grenen_slipper_codex_men_bare_med_kvotefilter():
    """#211, målt på #210 (run-listen 01:14Z): verdikter MED funn kommer
    som `pull_request_review`, ikke som issue_comment.

    Runde 11 på #198 fjernet boten fra review-grenen med begrunnelsen
    «botens verdikter kommer som issue_comment». Det var sant for de
    verdiktene som var målt da, og usant for den viktigste halvdelen: på
    #210 landet 2×P1 + 1×P2 som review med inline-funn, alle fire
    event-kjøringene ble SKIPPED, og funnene ble liggende stille.
    Asymmetrien gikk verst tenkelig vei — jo viktigere verdiktet, desto
    sikrere at sløyfa overså det.

    Innvendingen fra runde 11 gjaldt aldri kanalen, men at grenen bar en
    BAR login-match uten kvotefilter. Porten holder derfor begge deler:
    boten SKAL være på review-kanalen, og den skal bære filteret der,
    på `review.body` — ikke på `comment.body`, som er tom på et
    review-event og dermed ville gjort filteret til dekorasjon.

    MUTASJONEN SOM DREPER DENNE: fjern botens review-gren igjen (#211
    gjenoppstår), eller la den stå uten kvotefilter (en tom lommebok
    leses som «ingen funn», og steg 3 merger ureviewet kode).
    """
    grener = _grener()
    eier = [g for g in grener
            if "pull_request_review'" in g and "moka1980" in g]
    bot = [g for g in grener
           if "pull_request_review'" in g
           and "chatgpt-codex-connector[bot]" in g]
    assert len(eier) == 1, f"eierens review-gren er ikke entydig: {eier}"
    assert "github.event.review.state == 'approved'" in eier[0], (
        "eierens review-gren krever ikke lenger `approved` — en"
        " tilfeldig eier-kommentar er ikke et verdikt")
    assert len(bot) == 1, (
        "Codex-bottets review-gren mangler eller er duplisert — #211:"
        f" verdikter MED funn kommer nettopp denne veien. Grener: {grener}")
    assert ("startsWith(github.event.review.body, 'Codex usage limits"
            " have been reached')" in bot[0]), (
        "botens review-gren mangler kvotefilteret på `review.body` — en"
        " kvotemelding ville da nådd steg 3, som merger et verdikt uten"
        " funn")
    assert "github.event.review.state" not in bot[0], (
        "state-krav på botens gren stenger ute `changes_requested`,"
        " altså de tyngste funnene")


def test_begge_verdiktkanalene_bærer_kvotefilteret():
    """Filteret må stå på DEN kroppen hendelsen faktisk har.

    `comment.body` er tom på et review-event og `review.body` er tom på
    et issue_comment-event. Et filter som måler feil felt er ikke et
    filter — det er en negasjon av tomhet, altså alltid sann.
    """
    for gren in _grener():
        if "chatgpt-codex-connector[bot]" not in gren:
            continue
        felt = ("review.body" if "pull_request_review'" in gren
                else "comment.body")
        assert f"startsWith(github.event.{felt}," in gren, (
            f"verdiktgrenen måler ikke kvotefilteret på {felt}: {gren}")


def test_broen_forbyr_merge_som_mention():
    """Cursor P2-1 runde 11 (#198): broen vekkes automatisk og har
    write + gh pr:* — merge-forbudet fra mention må speiles."""
    fulgt = " ".join(_jobb("cursor-pass-fulgt").split())
    assert "MERGE ALDRI" in fulgt
    assert "gh pr merge` er forbudt" in fulgt


def test_rekkevidden_maales_foer_3b_i_runde_1():
    """Cursor P1-1 runde 12 (#198): «gå tilbake til 3b» sto FØR
    rekkevidde-målingen — porten ble rådgivende prosa. Tomt snitt er
    eneste lovlige vei til 3b etter head-flytting."""
    r1 = _fiksforsok()[0]
    assert "gå tilbake til 3b" not in r1
    assert "IKKE til 3b" in " ".join(r1.split()).replace("\n", " ") or \
        "IKKE til 3b" in r1


def test_head_leses_paa_nytt_rett_foer_merge_i_alle_forsok():
    """Cursor P1-2 runde 12 + P1 runde 16 (#198): CI-ventingen er et
    vindu også i fikserjobben — og omlesingen er verdiløs uten en MÅLT
    baseline: på CLEAN-stien finnes ingen update-branch-notering, så
    verdiktets egen commit_id må captures ved inngang til steg 3."""
    for i, forsok in enumerate(_fiksforsok(), 1):
        norm = " ".join(forsok.split())
        assert "PÅ NYTT RETT FØR" in norm or "PÅ NYTT rett før" in norm, (
            f"forsøk {i} mangler omlesingen før merge")
        assert "headRefOid" in norm, f"forsøk {i} mangler capture/omlesing"
        assert "commit_id" in norm, f"forsøk {i} mangler baseline-capture"
        assert "aseline" in norm, f"forsøk {i} mangler baseline-begrepet"
        # Runde 19: den MÅLTE verdiktkanalen er issue_comment — uten en
        # eksplisitt headRefOid-kilde der ville en compliant agent
        # parkert hvert rent verdikt (boten poster aldri formell review).
        assert "issue_comment" in norm, (
            f"forsøk {i} mangler kanaldelt baseline-kilde")
        # Runde 20: baselinen er verdiktets EGEN deklarasjon — aldri en
        # live headRefOid, som pre-capture-push kunne flyttet.
        assert "Reviewed commit" in norm, (
            f"forsøk {i} mangler verdikt-deklarasjonen som kilde")
        assert "ldri" in norm and "live" in norm, (
            f"forsøk {i} mangler live-headRefOid-forbudet")
    assert "baseline-SHA" in RUTINER


def test_pr_fillisten_har_stengt_vakt():
    """Cursor P2-4 runde 12 + P2-2 runde 13 (#198): snittets ANDRE side
    (PR-fillisten) må ha samme stengte trunkeringvakt som compare-siden
    — i ALLE forsøk og i §11.1, ikke bare runde 1 (fortynningsklassen)."""
    for i, forsok in enumerate(_fiksforsok(), 1):
        norm = " ".join(forsok.split())
        assert "PR-fillisten" in norm, (
            f"forsøk {i} mangler PR-filliste-vakten")
        assert "100+" in norm, (
            f"forsøk {i} mangler den målbare 100+-terskelen")
    assert "PR-filliste som er" in RUTINER or "PR-fillisten" in RUTINER


def test_dirty_grenen_loser_aldri_konflikten():
    """Cursor P1 runde 13 (#198): en konfliktløsning endrer HEAD-innhold
    verdiktet aldri bandt — DIRTY parkeres til eier i alle forsøk, aldri
    «løs hvis triviell»."""
    for i, forsok in enumerate(_fiksforsok(), 1):
        norm = " ".join(forsok.split())
        # UBETINGET (runde 14: den betingede formen lot forsøk 3 slippe
        # uten DIRTY-gren i det hele tatt — hul port)
        assert "DIRTY" in norm, f"forsøk {i} mangler DIRTY-grenen"
        assert "Løs konflikten hvis" not in norm, (
            f"forsøk {i}: DIRTY-grenen løser selv")
        assert "ALDRI selv" in norm, f"forsøk {i} mangler ALDRI-selv"
        assert "BRUKT OPP" in norm


def test_211_inline_funn_leses_i_alle_tre_forsokene():
    """Cursor P2-1/P2-4 på #230: #211-porten sto bare i forsøk 1.

    Fortynningsklassen fra #198, gjentatt. Runde 1 fikk beskjeden om at
    `review.body` kan være tom og at funnene ligger i
    `pulls/<nr>/comments`; runde 2 hadde bare API-pekeren uten
    KROPPEN-advarselen, og runde 3 hadde ingen av delene — samtidig som
    runde 3 er den som går rett i merge-løypa. Timer runde 1 ut på et
    review-utløst verdikt med inline-P1, kunne siste forsøk lese den
    tomme kroppen som «ingen funn» og merge dem forbi.

    Det er nøyaktig #211 igjen, ett hakk lenger inn: porten er åpnet,
    men lesningen er ikke.

    MUTASJONEN SOM DREPER DENNE: fjern KROPPEN-advarselen fra ett av de
    tre forsøkene — det holder å ta den fra runde 3, den som merger.
    """
    for i, forsok in enumerate(_fiksforsok(), 1):
        norm = " ".join(forsok.split())
        assert "pulls/<nr>/comments" in norm, (
            f"forsøk {i} peker ikke på inline-kommentarene")
        assert "IKKE I KROPPEN" in norm, (
            f"forsøk {i} advarer ikke om at `review.body` kan være tom —"
            " en tom kropp lest som «ingen funn» merger uåpnede P1")
        assert "#211" in norm, (
            f"forsøk {i} mangler sporet tilbake til funnet")


def test_steg_0_verdiktporten_i_alle_tre_forsokene():
    """Cursor P2 runde 14 (#198): kvote-/statusmeldinger som slipper
    forbi jobb-if-ens prefiksfilter må felles av steg 0 i HVERT forsøk —
    ellers kan forsøk 2/3 lese «ingen funn» og merge på en tom
    lommebok."""
    for i, forsok in enumerate(_fiksforsok(), 1):
        assert "ER DETTE I DET HELE TATT ET" in forsok, (
            f"forsøk {i} mangler steg 0-porten")


def test_cursor_porten_stopper_og_broen_eier_fortsettelsen():
    """Cursor P1 runde 15 (#198): runde 1/3 sa «vekker deg med @claude»
    og «Ved PASS: først da @codex review» — GITHUB_TOKEN-pass vekker
    ikke mention, og Codex-adgangen eies av broen (SHA-/forfatter-
    binding). Alle forsøk stopper etter @cursor review."""
    fiks = _jobb("fiks-og-merge")
    assert "vekker deg med `@claude`" not in fiks
    assert "Ved PASS: først da" not in fiks
    for i, forsok in enumerate(_fiksforsok(), 1):
        norm = " ".join(forsok.split())
        assert "workflow_run-broen" in norm or "cursor-pass-fulgt" in norm, (
            f"forsøk {i} peker ikke på broen")


def test_cancel_in_progress_false_er_portet():
    """Cursor P2 runde 15 (#198): fortrengnings-semantikken (#201) er
    dokumentert design — cancel-in-progress: false skal stå i alle tre
    skrivende jobber, og en flip til true skal bli rød."""
    for navn in ("fiks-og-merge", "mention", "cursor-pass-fulgt"):
        jobb = _uttrykk(_jobb(navn))
        assert "cancel-in-progress: false" in jobb, (
            f"{navn}: cancel-in-progress-false mangler/flippet")


def test_mergen_pinner_baseline_atomisk():
    """Cursor P1-1 runde 17 (#198): å måle headRefOid og så merge er
    sjekk-deretter-handle — pinnen (--match-head-commit) gjør det
    atomisk, i alle tre forsøkene og i §11.1."""
    for i, forsok in enumerate(_fiksforsok(), 1):
        norm = " ".join(forsok.split())
        assert "match-head-commit" in norm, (
            f"forsøk {i} mangler den atomiske pinnen")
        # Runde 18: pin-målet er baseline ELLER oppdaterings-merge-commit
        # — etter legitim BEHIND-oppdatering er head ≠ baseline
        # FORVENTET, og en pin mot baseline alene falskt-konsumerer.
        assert "baseline-eller-" in norm, (
            f"forsøk {i} pinner bare baseline")
        assert ("forvente" in norm.lower()
                or "som ikke er 3a-målingens egen" in norm), (
            f"forsøk {i} mangler 3a-unntaket i omlesingen")
        assert ("maks to" in norm.lower() or "maks TO" in norm), (
            f"forsøk {i} mangler taket på oppdaterings-omganger")
    assert "--match-head-commit" in RUTINER


def test_mention_og_broen_mangler_merge_kapasitet():
    """Cursor P1-2 runde 17 + P2-3 runde 19 (#198): `gh pr merge` er
    fjernet fra ACL-en (eksplisitte pr-underkommandoer). PRESISJON:
    `Bash(gh api:*)` består for lesing, og API-merge er dermed
    PROMPT-forbudt, ikke ACL-umulig — porten under krever at forbudet
    navngir API-veien i begge jobbene, og at pr-merge-kommandoen ikke
    kan uttrykkes. Fikserjobben beholder den brede flaten + pinnen."""
    for navn in ("mention", "cursor-pass-fulgt"):
        jobb = _jobb(navn)
        assert "Bash(gh pr:*)" not in jobb, (
            f"{navn}: bred pr-flate er tilbake (merge-kapasitet)")
        assert "Bash(gh pr view:*)" in jobb
        norm = " ".join(jobb.split())
        assert "pulls/.../merge" in norm.replace("`", ""), (
            f"{navn}: API-vei-forbudet mangler")
    assert "Bash(gh pr:*)" in _jobb("fiks-og-merge")


def test_cursor_allowlisten_er_dokumentert_i_p10():
    """Cursor P2-2 runde 19 (#198): §10 sa «noen kommenterer» mens
    YAML-en bare slipper eier og sløyfas bot — #193-klassen."""
    m = re.search(r"^## 10\. .*?(?=^## \d)", RUTINER, re.S | re.M)
    p10 = m.group(0) if m else ""
    tekst = p10 + RUTINER.split("## 2. ")[1].split("## 3.")[0] \
        if "## 2. " in RUTINER else p10
    assert "moka1980" in tekst and "claude[bot]" in tekst, (
        "allowlisten for @cursor review er ikke speilet i prosa")


def test_merge_forbudet_dekker_graphql():
    """Cursor P2-2 runde 20 (#198): REST-stien alene var forbudt —
    GraphQL-mutasjonene må navngis i begge skrivende jobber."""
    for navn in ("mention", "cursor-pass-fulgt"):
        norm = " ".join(_jobb(navn).split())
        assert "mergePullRequest" in norm, f"{navn} mangler GraphQL-forbud"
        assert "enablePullRequestAutoMerge" in norm


def test_artifaktet_bindes_til_passets_head():
    """Cursor P2-3 runde 20 (#198): pr-nummer-artifaktet må bevises å
    tilhøre workflow_run-ens head_sha — en mutert opplasting skal ikke
    kunne omdirigere broen til en annen PR."""
    hent = _jobb("pr-fra-pass")
    assert "workflow_run.head_sha" in hent
    assert "headRefOid" in hent
    # Målt i run 32902684841: for kommentar-utløste pass er head_sha
    # DEFAULT-grenens — skallbindingen må være pull_request-vaktet,
    # ellers dreper den broen på hvert kommentar-utløst pass.
    assert "workflow_run.event }}\" = \"pull_request\"" in hent.replace(
        "'", "\"") or 'workflow_run.event }}" = "pull_request"' in hent
