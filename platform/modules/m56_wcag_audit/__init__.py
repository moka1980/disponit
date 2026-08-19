"""m_wcag_audit (PR-014c) — modulens egen identitet.

`OPPDRAGSTYPE` står HER og ikke i `controller`, fordi to av modulens
filer må slå den opp for å finne ut hvilken vert oppdraget er autorisert
for: controlleren binder kvitteringens `ressurs_id` til den, og
rapportbyggingen binder de kontrollerte sidene til den (Codex P1).
`controller` importerer `rapport`, så den motsatte veien er stengt — og
to kopier av typenavnet ville vært to sannheter om hvem modulen er, med
en stille glipp den dagen bare den ene ble endret.
"""

#: Modulens ene oppdragstype. Den er nøkkelen inn i plattformens
#: `OPPDRAGSTYPER`, som eier både feltbredden og måldomenet — modulen
#: gjentar ingen av delene, den slår dem opp.
OPPDRAGSTYPE = "kontroll.wcag.nettsted"
