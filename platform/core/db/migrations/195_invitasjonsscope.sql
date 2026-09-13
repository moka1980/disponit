-- 195: `firma:inviter` i rollemønsteret.
--
-- 043 §6b speiler `ROLLE_TIL_SCOPES` EKSAKT, og port 26 i test_gate14b
-- måler de to mot hverandre. Et scope lagt til i app-laget uten migrasjon
-- er et sprik basen først ser den dagen et lovlig nei avvises.
--
-- KUN `admin`. Å slippe inn en kollega er å dele ut fullmakter i firmaet —
-- det er administratorens handling, ikke leserens eller godkjennerens. En
-- `leser` som kunne invitere, kunne invitert seg selv en ny konto med
-- flere roller enn hun har.
--
-- INNLØSNINGEN krever `firma:opprett` — REGISTRANTENS scope. Ikke fordi
-- autoriteten ligger der (den ligger i TOKENET), men fordi `_autentiser` er
-- bygget for ett påkrevd scope og avviser `None`.
--
-- Det er riktig i praksis, og grunnen er målt: en bruker med TO medlemskap
-- kan ikke logge inn før firmavelgeren finnes (192), så hver eneste
-- inviterte ER registrant. KRAVET MÅ UTVIDES sammen med velgeren.
INSERT INTO rolle_scope (rolle, scope) VALUES
    ('admin', 'firma:inviter')
ON CONFLICT DO NOTHING;
