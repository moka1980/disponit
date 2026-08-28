"""Strukturelle porter for `deploy/staging/backup-db.sh` (Codex P1 ×4, #229).

Skriptet kjører som root på VPS-en og kan ikke prøves fra en pytest-økt:
det trenger `pg_dump`, `age`, en levende base og en delt disk. Portene her
måler det som likevel er målbart — REKKEFØLGEN og PLASSERINGEN — og det er
nettopp de to tingene Codex-rundene på #229 fant feil ved. Samme form som
§11/§12-portene i `test_rutiner_natt` og `test_rutiner_leveransetakt`: en
regel som bare finnes i prosa, glir fra flaten den styrer.
"""
import re
from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
SKRIPT = (ROT / "deploy" / "staging" / "backup-db.sh").read_text(
    encoding="utf-8")


def _pos(nal: str) -> int:
    """Posisjonen til `nal`, med en feilmelding som navngir den."""
    i = SKRIPT.find(nal)
    assert i != -1, f"fant ikke {nal!r} i backup-db.sh"
    return i


def test_retensjonen_star_for_diskporten():
    """Codex P1: porten hindret sin egen forutsetning.

    Sto sveipen sist, var den uoppnåelig nettopp når den trengtes: fylte
    de beholdte parene katalogen så et nytt par ikke fikk plass,
    avsluttet diskporten FØR sveipen kunne slette noe. Neste kjøring så
    samme opptatte plass og avsluttet på samme sted — permanent, uten at
    noe var galt annet enn rekkefølgen.

    MUTASJONEN SOM DREPER DENNE: flytt retention-blokken tilbake til
    slutten av skriptet.
    """
    assert _pos('UTLOPTE=$(find "$KATALOG"') < _pos("LEDIG_KIB="), (
        "diskporten måler før retensjonen har frigjort noe — en full"
        " katalog blir da permanent full, med en feilmelding over seg")
    # ... og sveipen må fortsatt komme ETTER låsen som eier katalogen,
    # ellers sletter to kjøringer hverandres par.
    assert _pos("flock") < _pos('UTLOPTE=$(find "$KATALOG"'), \
        "retensjonen kjører utenfor flock-en som eier katalogen"


def test_tenantlistene_bor_i_den_private_katalogen():
    """Codex P1 ×2: klartekst-stier i `/tmp`, og et symlenke-kappløp.

    `mktemp` uten `-p` legger fila i `/tmp` — verdensskrivbar og
    PERSISTENT. Fila og de to avledede bærer hver tenant-ID og hver
    arkivmedlemssti i klartekst, altså nøyaktig det arkivet krypteres for
    å skjule. Og `mktemp` sikrer bare det FØRSTE navnet: `.sett` og
    `.krav` kan gjettes fra det og pre-opprettes som symlenker under den
    lange dump/arkiv-fasen, så root skriver gjennom lenken.

    `$RAA_KAT` er `mktemp -d -p /dev/shm` + `chmod 700`, root-eid: ingen
    andre kommer inn, så hverken lesningen eller kappløpet finnes.

    MUTASJONEN SOM DREPER DENNE: `LISTE=$(mktemp)`.
    """
    m = re.search(r"^LISTE=(.*)$", SKRIPT, re.M)
    assert m, "ingen LISTE-tilordning i backup-db.sh"
    assert m.group(1).strip() == '"$RAA_KAT/medlemmer"', (
        f"listen bor utenfor den private katalogen: {m.group(1).strip()}")
    # Katalogen må være privat OG root-eid før listen legges i den.
    assert _pos('chmod 700 "$RAA_KAT"') < m.start(), \
        "listen legges i katalogen før den er 0700"
    # `.sett` og `.krav` er avledet av samme navn — de arver katalogen.
    for avledet in ('"$LISTE.sett"', '"$LISTE.krav"'):
        assert avledet in SKRIPT, f"{avledet} finnes ikke lenger"


def test_finaliseringen_er_fsynket_i_par_rekkefolge():
    """Codex P1: `sync` alene gir ingen REKKEFØLGE.

    Den tømte køen én gang, før begge `mv`-ene; etterpå var de to
    katalogpostene usynkede, og et strømbrudd kunne la filsystemet
    gjenopprette dumpens endelige navn UTEN arkivets. Da er «ser ut som
    dagens backup»-løgnen tilbake, med filsystemet som årsak i stedet for
    signalet.

    Kravet er derfor ledd for ledd: innholdet i begge filene, så arkivets
    navn, så en katalog-fsync, så dumpens navn.

    MUTASJONEN SOM DREPER DENNE: én naken `sync` foran de to `mv`-ene.
    """
    innhold = _pos('sync "$ARKIV_DELVIS" "$DELVIS"')
    mv_arkiv = _pos('mv "$ARKIV_DELVIS" "$ARKIV"')
    mv_dump = _pos('mv "$DELVIS" "$FIL"')
    katalogsyncer = [m.start() for m in
                     re.finditer(r'^sync "\$KATALOG"$', SKRIPT, re.M)]
    assert innhold < mv_arkiv < mv_dump, \
        "innholdet fsynkes ikke før navnene settes"
    assert any(mv_arkiv < p < mv_dump for p in katalogsyncer), (
        "ingen katalog-fsync MELLOM de to `mv`-ene — da kan dumpens navn"
        " overleve et strømbrudd uten arkivets, som er hele løgnen"
        " arbeidsnavnene finnes for å hindre")
    assert any(p > mv_dump for p in katalogsyncer), \
        "paret publiseres uten at det siste navnet er festet"
    # ARKIVET FØRST, DUMPEN SIST — retningen er utledet, ikke valgt:
    # dumpen er det retention og operatøren leter etter.
    assert mv_arkiv < mv_dump, "dumpen får endelig navn før arkivet"


def test_arkivporten_maler_innhold_ikke_bare_navn():
    """Codex P1: en tømt `.bin` passerte som gjenopprettingsverifisert.

    `comm`-porten måler at hver påkrevd sti STÅR i arkivet. En fil som er
    blitt avkortet eller tømt før backupen kjørte, arkiveres like lydig
    som en hel: `tar` lykkes, navnet står i listen, og paret publiseres
    som verifisert mens den restaurerte radens nonce peker på ingenting.

    Porten må stå FØR finaliseringen — ellers har paret alt fått
    backupnavnene sine når den feller.

    MUTASJONEN SOM DREPER DENNE: fjern `-s`-testen, eller flytt blokken
    ned etter `mv`-ene.
    """
    innholdsport = _pos('mens_manglet="$mens_manglet$sti"')
    assert '[ ! -s "$LAGER/$sti" ]' in SKRIPT, (
        "porten spør ikke om filen har innhold — en tømt bunt passerer")
    assert '[ ! -f "$LAGER/$sti" ]' in SKRIPT, \
        "porten spør ikke om stien er en vanlig fil"
    assert innholdsport < _pos('mv "$ARKIV_DELVIS" "$ARKIV"'), (
        "innholdsporten står etter finaliseringen — da er paret alt"
        " publisert når den feller")
    # ÆRLIGHETEN OM RESTKLASSEN står i skriptet, ikke bare i PR-en:
    # ciphertexten har ingen lagret digest å måles mot, så DELVIS
    # avkorting er utenfor rekkevidde til skrivesiden bærer en.
    assert "HVA DEN IKKE GJØR" in SKRIPT, (
        "restklassen er ikke skrevet ned — neste leser tror porten"
        " dekker byte-korrupsjon")
