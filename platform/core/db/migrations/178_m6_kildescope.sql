-- 178 — M-6: kilden husker HVA samtykket ga.
--
-- Eiervedtak 10/9: svar skal gå fra kundens egen postboks, ikke fra
-- husets SMTP, så tråden holder sammen hos mottakeren. Det krever
-- `Mail.Send` i tillegg til `Mail.Read` — en KONTRAKTSENDRING mot
-- Microsoft, ikke en strengendring: et refresh-token utstedt under det
-- gamle samtykket kan ikke sende, uansett hva koden ber om.
--
-- Uten denne kolonnen ville plattformen ikke visst forskjell: en kilde
-- koblet i går og en koblet i dag ser like ut, og den første ville
-- feilet med en 403 fra Graph i det øyeblikket noen prøvde å svare —
-- etter at et menneske hadde godkjent utkastet. Kilden bærer nå
-- SAMTYKKETS EGNE ORD, skrevet av callbacken fra tokensvaret, så flaten
-- kan si «koble til på nytt for å kunne svare herfra» FØR noen skriver
-- et svar som ikke kan sendes.
--
-- NULL betyr «koblet før vi begynte å spørre» — altså kun lesing, som
-- var det eneste v1 ba om. Fraværet er en ærlig verdi, ikke en mangel.
ALTER TABLE epost_kilde ADD COLUMN scope TEXT;

COMMENT ON COLUMN epost_kilde.scope IS
    'Scopene samtykket faktisk ga (tokensvarets `scope`), skrevet av'
    ' OAuth-callbacken. NULL = koblet før 178, altså kun lesing.';
