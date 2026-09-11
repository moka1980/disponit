-- 185 — Partsregisterets to scope i rollemønsteret (183/184, PR 3).
--
-- 043 §6b speiler `ROLLE_TIL_SCOPES` EKSAKT, og port 26 i test_gate14b
-- måler de to mot hverandre. Et scope lagt til i app-laget uten
-- migrasjon er et sprik basen først ser den dagen et lovlig nei avvises.
-- Formen er 088s, ordrett.
--
-- TO SCOPE, IKKE ETT. Å SE kundelisten og å ENDRE den er to ting — samme
-- lærdom som `epost:utkast:behandle` mot `epost:kilde:administrer`, der
-- én nøkkel for begge ga en flate som enten skjulte det brukeren hadde
-- lov til, eller viste knapper serveren ville nekte.
--
-- `part:read` HOS ENHVER LESER. Kundelisten er ikke en hemmelighet i
-- firmaet, den er arbeidsgrunnlaget: en saksbehandler som ikke ser
-- kundene sine kan ikke gjøre jobben. `part:administrer` er
-- administratorens.
-- `sikkerhet` ER EN SUPERMENGDE AV `leser`, sagt i `autorisasjon.py`s
-- egen kommentar: «et hull i den containment-en ville vært en endring i
-- rollemodellen skjult i en modul-PR». Første utkast av denne PR-en var
-- nøyaktig det hullet (CodeRabbit) — `part:read` til `leser`, ikke til
-- `sikkerhet`. Ingenting målte det; nå gjør en port det.
INSERT INTO rolle_scope (rolle, scope) VALUES
    ('leser', 'part:read'),
    ('sikkerhet', 'part:read'),
    ('admin', 'part:read'),
    ('admin', 'part:administrer')
ON CONFLICT DO NOTHING;
