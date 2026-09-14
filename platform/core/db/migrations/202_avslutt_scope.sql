-- 202: `firma:avslutt` inn i `rolle_scope`.
--
-- Speiler `ROLLE_TIL_SCOPES` (autorisasjon.py). 043 §6b binder de to mot
-- hverandre, og port 26 i `test_gate14b` måler dem mot hverandre — et scope
-- som står i koden men ikke her, er en rolle basen ikke kjenner.
--
-- ADMIN ALENE. Å si opp firmaets abonnement er den mest inngripende
-- handlingen en vanlig kunde kan gjøre; en `leser` som kunne det, kunne
-- avsluttet firmaet på vei ut døra.
-- ============================================================

INSERT INTO rolle_scope (rolle, scope) VALUES
    ('admin', 'firma:avslutt')
ON CONFLICT DO NOTHING;
