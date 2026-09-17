-- 209 — EN FEILET UTFØRELSE PÅ EN KOMPENSERENDE KONTRAKT FÅR EN SAK.
--
-- M-57s SP-3-punkt («feilinjisering til unntakskø») krever at en avbrutt
-- parsing/evaluering gir et RENT feilutfall OG leverer en post i
-- unntakskøen bundet til jobben. Kvitteringsendepunktet satte oppdraget
-- `feilet` og stoppet der: en bunt som aldri ble evaluert har en kunde
-- som venter, og ingen menneske så bestillingen.
--
-- Saken fødes gjennom den samme døren evidensreaperen og den sene
-- kvitteringen bruker (`sikre_sak_for_oppdrag`, 038/041/056) — én
-- skrivevei, idempotent per (oppdrag, årsak), snapshot 0 (aldri
-- automatikk). Det eneste som mangler er ORDET: `unntak.arsak` er en
-- lukket mengde (043 §2), og `utforelse_feilet` er ikke i den.
--
-- Bare KOMPENSERENDE kontrakter (M-57) får saken — direkte kontrakter
-- reverserer seg selv, irreversible har sin egen 043-vei. Beslutningen
-- tas i endepunktet; migrasjonen utvider bare mengden.
--
-- Reasserteres som i 043: en `IF NOT EXISTS` ville latt testbasene stå
-- med den gamle, snevrere CHECKen.
ALTER TABLE unntak DROP CONSTRAINT IF EXISTS unntak_arsak_gyldig;
ALTER TABLE unntak ADD CONSTRAINT unntak_arsak_gyldig
  CHECK (arsak IN ('evidensfrist', 'sikkerhet', 'kompensasjon_kreves',
                   'irreversibel_utfort', 'reversibilitet_ukjent',
                   'utforelse_feilet'));
