"""Modellklienten: `vurder(tekst, vekter) -> dict` mot en LOKAL
OpenAI-kompatibel/Ollama-server (eiers valg 26/8: persondata forlater
aldri serveren).

Kontrakten klienten oppfyller er `_krev_helt_svar`s (evaluering):
`{funn, oppfylt}` med `oppfylt` over NØYAKTIG profilens kravsett.
Intervjuspørsmål bes det ikke om (#225, eiers retning 27/8): de hører
til innkallingen av de beste, ikke evalueringen av alle. Modellen bes
om VERBATIME sitater; klienten
lokaliserer offsetene selv (`tekst.find`) — et sitat som ikke finnes
ordrett i den blindede teksten er ikke evidens og DROPPES, talt i
`droppede_funn` for driftsloggen. `oppfylt` fylles fail-closed: et krav
modellen ikke svarte på er IKKE oppfylt.

`image_digest` er KONFIGURASJONENS påstand om hvilken modell dette er
(sha256 over modell-manifestet fra `ollama show --modelfile`/registry)
— det er tallet biasmålingene (port 17) er bundet til. Klienten finner
det aldri på selv.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .evaluering import FUNN_KATEGORIER
from .rapportskjema import FUNN_MAKS, SITAT_MAKS


class Modellfeil(Exception):
    def __init__(self, kode: str, detalj: str = ""):
        self.kode = kode
        super().__init__(f"{kode}: {detalj}" if detalj else kode)


_SYSTEM = (
    "Du er en norsk rekrutteringsassistent. Du får en BLINDET "
    "søknadstekst (personfelter er maskert som [NAVN-1] osv.) og en "
    "kravliste. Svar KUN med ett JSON-objekt på nøyaktig denne formen:\n"
    '{"oppfylt": {<krav>: true/false for HVERT krav i lista>},\n'
    ' "funn": [{"kategori": <en av %s>,\n'
    '           "sitat": "<ORDRETT utdrag fra teksten>"}]}\n'
    "Sitatene MÅ være ordrette utdrag. Ingen tekst utenfor "
    "JSON-objektet." % sorted(FUNN_KATEGORIER))


class Ollamamodell:
    """Én evaluering per kall; transportfeil og uleselige svar er kodede
    `Modellfeil` (SP-3) — aldri rå unntak inn i kjøreløkka."""

    def __init__(self, base_url: str, modellnavn: str, image_digest: str,
                 *, frist_s: float = 120.0):
        self.base = base_url.rstrip("/")
        self.modellnavn = modellnavn
        self.image_digest = image_digest
        self.frist_s = frist_s
        self.droppede_funn = 0

    def _kall(self, prompt: str) -> str:
        kropp = json.dumps({
            "model": self.modellnavn,
            "stream": False,
            "format": "json",
            "messages": [{"role": "system", "content": _SYSTEM},
                         {"role": "user", "content": prompt}],
        }).encode("utf-8")
        req = urllib.request.Request(
            self.base + "/api/chat", data=kropp, method="POST",
            headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.frist_s) as r:
                svar = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as feil:
            raise Modellfeil("modell_utilgjengelig",
                             type(feil).__name__) from feil
        innhold = ((svar or {}).get("message") or {}).get("content")
        if not isinstance(innhold, str):
            raise Modellfeil("modellsvar_uleselig", "uten innhold")
        return innhold

    def vurder(self, tekst: str, vekter: dict[str, int]) -> dict:
        krav = sorted(vekter)
        prompt = ("KRAV (svar på hvert): " + ", ".join(krav)
                  + "\n\nSØKNADSTEKST:\n" + tekst)
        raa = self._kall(prompt)
        try:
            svar = json.loads(raa)
        except ValueError as feil:
            raise Modellfeil("modellsvar_uleselig", "ikke json") from feil
        if not isinstance(svar, dict):
            raise Modellfeil("modellsvar_uleselig", "ikke objekt")
        # Fail-closed oppfyllelse: NØYAKTIG profilens sett, ubesvart = False,
        # ikke-boolsk = False (aldri sannhetsverdien av hva som helst).
        raa_oppfylt = svar.get("oppfylt")
        raa_oppfylt = raa_oppfylt if isinstance(raa_oppfylt, dict) else {}
        oppfylt = {k: (raa_oppfylt.get(k) is True) for k in krav}
        funn = []
        for f in svar.get("funn") or []:
            if not isinstance(f, dict):
                self.droppede_funn += 1
                continue
            kategori, sitat = f.get("kategori"), f.get("sitat")
            if kategori not in FUNN_KATEGORIER \
                    or not isinstance(sitat, str) or not sitat:
                self.droppede_funn += 1
                continue
            # SITATLENGDEN HØRER TIL SAMME PORT (Codex P2, #173). Et
            # «sitat» som er hele dokumentet er ikke evidens for ett
            # funn, og hundre av dem sprenger skriveveiens
            # per-kandidat-budsjett — som da feller HELE evalueringen på
            # `request_feilformet`. Kontraktens `SITAT_MAKS` er det som
            # gjør det samlede sitatvolumet til et TALL døren kan regne
            # med; her er den håndhevet, samme sted og samme måte som
            # taket på antall funn.
            if len(sitat) > SITAT_MAKS:
                self.droppede_funn += 1
                continue
            start = tekst.find(sitat)
            if start < 0:
                # Et «sitat» som ikke finnes ordrett er ingen evidens.
                self.droppede_funn += 1
                continue
            if len(funn) >= FUNN_MAKS:
                # Rapportskjemaets tak håndheves ved GRENSEN: en modell
                # som fosser funn skal ikke felle skjemavalideringen av
                # en ellers gyldig evaluering.
                self.droppede_funn += 1
                continue
            funn.append({"kategori": kategori,
                         "kilde": {"start": start,
                                   "slutt": start + len(sitat),
                                   "sitat": sitat}})
        return {"funn": funn, "oppfylt": oppfylt}
