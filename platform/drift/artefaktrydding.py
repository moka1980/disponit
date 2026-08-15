"""PR-015 §6 — ryddetimer for staged artefakter.

`disponit-artefaktrydding.timer`, hvert 15. minutt, kaller
`rydd_staged_artefakter()`.

Timeren legger INGEN logikk oppå den positive regelen. Regelen eies av
databasen (016): `staged` eldre enn 24 t **og** uten refererende kvittering,
inkludert karantenesatt. Timeren bestemmer ikke hva som er søppel — den kaller
funksjonen som vet det.

Det timeren gjør, er de tre tingene §6 krever av en driftsjobb:

  * **Batchgrense 500 per kjøring**, så en opphopning ikke låser tabellen i én
    transaksjon. Grensen ligger i den bundne DB-formen `rydd_staged_artefakter(
    p_grense)` (migrasjon 019) — samme predikat som 016, med et `LIMIT`. Timeren
    teller ikke selv; da hadde grensen vært logikk oppå regelen.
  * **Karantenesatt evidens telles og rapporteres, aldri ryddes.** Den er ikke
    `staged` og nås ikke av predikatet i det hele tatt. Men en ryddejobb som
    ikke sier hvor mye den BEVARER, skjuler at disken vokser av noe den aldri
    får røre.
  * **To sammenhengende feilede kjøringer → alarm.** En stille ryddejobb er en
    voksende disk.
"""
from __future__ import annotations

from dataclasses import dataclass

#: §6: maks antall artefakter forkastet per kjøring.
BATCHGRENSE = 500
#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to ryddekjøringer overlapper aldri.
ARBEIDERNOKKEL = 915_774_202


@dataclass
class Ryddresultat:
    forkastet: int = 0
    karantene_bevart: int = 0
    batcher: int = 0
    feilet: bool = False
    alarm_utlost: bool = False


def kjor(conn, *, grense: int = BATCHGRENSE, maks_batcher: int = 1,
         tidligere_feil: int = 0) -> Ryddresultat:
    """Én ryddekjøring.

    `maks_batcher` > 1 lar en opphopning dreneres i FLERE avgrensede
    transaksjoner i samme kjøring — hver batch committes for seg, så tabellen
    aldri holdes i én lang transaksjon. Det er nettopp forskjellen §6 er ute
    etter: grensen finnes for å begrense transaksjonen, ikke for å begrense hvor
    mye som til slutt blir ryddet.

    `tidligere_feil` er antall sammenhengende feilede kjøringer FØR denne;
    kalleren (timeren) bærer den telleren mellom kjøringer, siden hver kjøring
    er en egen prosess.
    """
    res = Ryddresultat()
    fikk_lås = conn.execute("SELECT pg_try_advisory_lock(%s)",
                            (ARBEIDERNOKKEL,)).fetchone()[0]
    if not fikk_lås:
        return res
    try:
        for _ in range(maks_batcher):
            try:
                n = int(conn.execute("SELECT rydd_staged_artefakter(%s)",
                                     (grense,)).fetchone()[0])
                conn.commit()
            except Exception:
                _rull_tilbake(conn)
                res.feilet = True
                res.alarm_utlost = tidligere_feil + 1 >= ALARM_ETTER_FEIL
                return res
            res.forkastet += n
            res.batcher += 1
            # Idempotens: en kjøring til på et tømt sett forkaster null rader og
            # sletter ingenting nytt. Tom batch betyr ferdig, ikke feil.
            if n < grense:
                break
        # Codex (P2): SAMME feilhåndtering som selve ryddekallet. Faller
        # tilkoblingen etter at en batch er committet, er kjøringen fortsatt en
        # FEILET kjøring i §6-forstand — men uten dette slapp unntaket ut av
        # `kjor()`, `main()` rakk aldri å persistere den økte telleren, og en
        # vedvarende feil i nettopp dette steget nådde derfor aldri alarmen
        # etter to sammenhengende feil. Det som alt er forkastet står ved lag
        # (batchen er committet); det som mangler, er tallet på bevart
        # karantene, og da skal kjøringen si «feilet», ikke rapportere 0 bevart
        # som om den hadde talt.
        try:
            res.karantene_bevart = int(
                conn.execute("SELECT antall_karantenesatte()").fetchone()[0])
            conn.commit()
        except Exception:
            _rull_tilbake(conn)
            res.feilet = True
            res.alarm_utlost = tidligere_feil + 1 >= ALARM_ETTER_FEIL
        return res
    finally:
        # Opplåsingen er BEST EFFORT. Er tilkoblingen borte, feiler også denne
        # — og et unntak herfra ville erstattet resultatet kalleren skal
        # rapportere og persistere telleren fra, altså gjenskapt nøyaktig den
        # tapte alarmen over. Låsen er sesjonsscopet: en død sesjon slipper den
        # uansett når tilkoblingen lukkes.
        try:
            conn.execute("SELECT pg_advisory_unlock(%s)", (ARBEIDERNOKKEL,))
            conn.commit()
        except Exception:
            pass


def _rull_tilbake(conn) -> None:
    """Rollback som aldri kaster. En død tilkobling kan ikke rulles tilbake."""
    try:
        conn.rollback()
    except Exception:
        pass
