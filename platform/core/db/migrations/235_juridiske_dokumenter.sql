-- 235 — VILKÅR, PERSONVERN OG DATABEHANDLERAVTALE SOM DATA.
--
-- Eiers ordre 27/11: «alt må betjenes via dashboard både for kunden og
-- admin panel, ikke gjennom koden». Tekstene sto i `docs/juridisk/*.md` —
-- i kode, og bare en utvikler kunne endre eller publisere dem. Nå:
--
--   * PLATTFORMEIEREN redigerer et utkast og publiserer det selv
--     (plattformadmin → «Vilkår og personvern»). Hver publisering er en
--     NY versjon; en publisert versjon endres aldri (vakten under).
--   * SIDENE `/vilkar`, `/personvern`, `/databehandleravtale` viser den
--     siste publiserte versjonen, med `{{navn}}` m.fl. fylt inn fra
--     «Om Disponit» (234).
--   * REGISTRERINGEN krever at kunden godtar gjeldende vilkår og
--     databehandleravtale — NÅR de er publisert. Hvilken versjon hvert
--     firma godtok, av hvem og når, står i `firma_samtykke`.
--
-- STARTUTKASTENE er tekstene fra #632, uten personopplysninger (feltene
-- fylles fra 234). De er UTKAST: ingenting er publisert, og registreringen
-- krever ingenting før eieren har trykket «Publiser».
-- ============================================================

CREATE TABLE juridisk_dokument (
    dok          TEXT NOT NULL CONSTRAINT juridisk_dok_lukket
                      CHECK (dok IN ('vilkar', 'personvern',
                                     'databehandleravtale')),
    versjon      INT  NOT NULL CHECK (versjon >= 1),
    tekst        TEXT NOT NULL CHECK (length(tekst) BETWEEN 1 AND 200000),
    opprettet    TIMESTAMPTZ NOT NULL DEFAULT now(),
    endret_av    TEXT NOT NULL CHECK (endret_av ~ '[^[:space:]]'),
    publisert_ts TIMESTAMPTZ,
    publisert_av TEXT,
    PRIMARY KEY (dok, versjon),
    CONSTRAINT juridisk_publisering_helhet CHECK (
        (publisert_ts IS NULL AND publisert_av IS NULL)
        OR (publisert_ts IS NOT NULL AND publisert_av ~ '[^[:space:]]'))
);
-- Høyst ÉTT utkast per dokument: utkastet er det som redigeres.
CREATE UNIQUE INDEX juridisk_ett_utkast ON juridisk_dokument (dok)
    WHERE publisert_ts IS NULL;

-- EN PUBLISERT VERSJON ENDRES ALDRI. Et firma har godtatt NETTOPP de
-- ordene; en stille retting etterpå ville gjort samtykket til noe annet
-- enn det var.
CREATE FUNCTION juridisk_publisert_vakt() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' AND OLD.publisert_ts IS NOT NULL THEN
        RAISE EXCEPTION 'juridisk_dokument: en publisert versjon slettes ikke'
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.publisert_ts IS NOT NULL THEN
        RAISE EXCEPTION 'juridisk_dokument: en publisert versjon endres ikke'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER juridisk_publisert_vakt
    BEFORE UPDATE OR DELETE ON juridisk_dokument
    FOR EACH ROW EXECUTE FUNCTION juridisk_publisert_vakt();

CREATE TABLE firma_samtykke (
    tenant     TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    dok        TEXT NOT NULL,
    versjon    INT  NOT NULL,
    godtatt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    godtatt_av TEXT NOT NULL CHECK (godtatt_av ~ '[^[:space:]]'),
    PRIMARY KEY (tenant, dok, versjon),
    FOREIGN KEY (dok, versjon) REFERENCES juridisk_dokument (dok, versjon)
);

REVOKE ALL ON TABLE juridisk_dokument, firma_samtykke FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON TABLE juridisk_dokument
    TO disponit_plattform_eier;
GRANT SELECT, INSERT ON TABLE firma_samtykke TO disponit_plattform_eier;

-- STARTUTKASTENE (versjon 1, ikke publisert).
INSERT INTO juridisk_dokument (dok, versjon, tekst, endret_av) VALUES
    ('vilkar', 1, $dok$# Bruksvilkår for Disponit


## 1. Partene

Disse vilkårene gjelder mellom **{{navn}}, org.nr. {{orgnummer}}, {{adresse}}**
(«Disponit», «vi») og firmaet som registrerer seg og bruker tjenesten
(«kunden»). Den som registrerer firmaet, bekrefter at hun har rett til å binde
firmaet.

## 2. Tjenesten

Disponit er en nettbasert tjeneste der programvareagenter går gjennom
firmaets data, finner forhold som bør følges opp og foreslår hva som bør
gjøres. Agentene handler bare innenfor de fullmaktene kunden selv har gitt i
policyen sin. Uten en slik fullmakt foreslår de bare, og et menneske hos
kunden bestemmer.

Kunden velger selv hvilke moduler og kilder som tas i bruk, for eksempel
tilkobling av en e-postpostboks. Når en kilde kobles til, gir kunden Disponit
tilgang til å lese fra den, og til å sende fra den når kunden selv har valgt
det.

Ingen språkmodell eller annen ekstern KI-tjeneste brukes på kundens data.
Endres dette, varsler vi kunden på forhånd, og det krever kundens eget valg.

## 3. Prøveperiode

Nye kunder får en gratis prøveperiode på 30 dager. Appen viser når den
går ut. Når prøveperioden er over og ingen videre avtale er inngått:

- slutter agentene å gjøre noe av seg selv, og ingen e-post hentes eller sendes,
- kan kunden fortsatt logge inn, se alt og laste ned dataene sine.

Videre bruk etter prøveperioden, med pris og betalingsvilkår, avtales
individuelt med Disponit.

## 4. Kundens ansvar

Kunden

- er ansvarlig for at data kunden legger inn eller kobler til, er lovlig å
  behandle, og er behandlingsansvarlig for personopplysninger om egne kunder,
  ansatte og andre (se databehandleravtalen);
- bestemmer selv fullmaktene i policyen og er ansvarlig for det agentene gjør
  innenfor dem;
- gir bare tilgang til personer som skal ha den, og holder egne
  påloggingskontoer hos Google eller Microsoft sikre.

## 5. Disponits ansvar

Vi

- drifter tjenesten med rimelig omhu og retter feil vi blir kjent med;
- krypterer kundens data med en egen nøkkel per firma og gir ikke andre
  kunder tilgang til dem;
- behandler personopplysninger på kundens vegne slik databehandleravtalen sier.

Tjenesten leveres uten garanti for at den alltid er tilgjengelig eller feilfri.
Forslag fra agentene er beslutningsstøtte. De erstatter ikke kundens egen
vurdering.

## 6. Data og eksport

Kundens data tilhører kunden. Kunden kan når som helst laste ned alt firmaet
har lagt inn som én fil (Kundeflate → «Dataene dine»), også etter at prøveperioden
er over.

## 7. Oppsigelse og sletting

Kunden kan si opp selv i appen (Kundeflate → «Abonnementet» → «Si opp abonnementet»). Etter oppsigelse er
det en angrefrist på 30 dager der firmaet kan gjenåpnes med alt i behold.
Deretter destrueres firmaets krypteringsnøkkel, slik at dataene ikke lenger
kan leses, og de ryddes bort. Last ned dataene før fristen er ute.

Disponit kan si opp avtalen med 30 dagers
skriftlig varsel, eller straks ved vesentlig mislighold.

## 8. Endringer i vilkårene

Vi varsler vesentlige endringer i appen og på e-post minst
30 dager før de trer i kraft.

## 9. Ansvarsbegrensning

Disponit er ikke ansvarlig for indirekte tap, som tapt fortjeneste eller tapte
data utover det eksporten og slettefristene sikrer. Samlet ansvar er begrenset
til det kunden har betalt for tjenesten de siste 12 månedene før skaden
oppsto. I prøveperioden, og ellers når tjenesten er vederlagsfri, er Disponit
ikke ansvarlig for tap. Begrensningene gjelder ikke ved grov uaktsomhet eller
forsett.

## 10. Lovvalg og verneting

Norsk rett gjelder. Tvister løses ved {{verneting}} som avtalt verneting.

## 11. Kontakt

{{kontakt_epost}}
$dok$, 'migrasjon 235'),
    ('personvern', 1, $dok$# Personvernerklæring for Disponit


## Hvem vi er

**{{navn}}, org.nr. {{orgnummer}}, {{adresse}}** leverer tjenesten Disponit.
Personvernspørsmål: **{{kontakt_epost}}**.

## To roller

- **Vi er behandlingsansvarlige** for opplysninger om deg som bruker
  tjenesten: din konto, innloggingen din og hva du gjør i appen.
- **Vi er databehandler** for opplysningene firmaet ditt legger inn eller
  kobler til, for eksempel egne kunder, e-post og fakturaer. Da er det firmaet
  som er behandlingsansvarlig, og databehandleravtalen gjelder.

## Hva vi behandler om deg som bruker

| Opplysning | Hvorfor | Grunnlag |
|---|---|---|
| Navn og e-postadresse fra Google eller Microsoft | For å logge deg inn og vise hvem som gjorde hva | Avtalen med firmaet ditt (GDPR art. 6 nr. 1 b/f) |
| Hva du gjør i appen (for eksempel godkjenninger og endringer i policyen) | Revisjonsspor, slik at firmaet kan se hvem som gjorde hva | Berettiget interesse (art. 6 nr. 1 f) |
| Tekniske logger (tidspunkt, forespørsels-id) | Drift og sikkerhet | Berettiget interesse (art. 6 nr. 1 f) |

Vi lagrer **ingen passord**. Du logger inn med Google eller Microsoft.

## Hvor lenge

- Kontoen din finnes så lenge du er medlem av et firma i Disponit.
- Revisjonssporet beholdes så lenge firmaet finnes, fordi det er bevis for hva
  som ble gjort.
- Etter at et firma er avsluttet, er det en angrefrist på 30 dager. Deretter
  destrueres firmaets krypteringsnøkkel, slik at dataene ikke kan leses, og
  de ryddes bort.

## Hvem vi deler med

Vi selger ikke opplysninger og bruker dem ikke til reklame. Vi bruker disse
leverandørene (underdatabehandlere):

| Leverandør | Hva | Hvor |
|---|---|---|
| one.com (driftet av dogado GmbH i one.com-konsernet) | Servere og database | Tyskland (EØS) |
| Google | Innlogging, og Gmail hvis firmaet kobler det til | EØS og USA (EU–US Data Privacy Framework og EUs standardavtaler) |
| Microsoft | Innlogging, og Microsoft 365-postboks hvis firmaet kobler den til | EØS og USA (EU–US Data Privacy Framework og EUs standardavtaler) |
| one.com | Utgående e-post fra plattformens adresse | EØS |

Ingen språkmodell eller annen ekstern KI-tjeneste får tilgang til dataene.

## Sikkerhet

Dataene krypteres med en egen nøkkel per firma. E-postadresser gjenkjennes
via et pseudonym, slik at de ikke sammenlignes i klartekst. Tilgang styres
per rolle, og alt som endres, logges.

## Dine rettigheter

Du kan be om innsyn, retting, sletting, begrensning og dataportabilitet, og
du kan protestere mot behandling som bygger på berettiget interesse. Kontakt
oss på {{kontakt_epost}}. Gjelder det data firmaet ditt har lagt inn, sender
vi henvendelsen videre til firmaet.

Du kan klage til Datatilsynet (datatilsynet.no).
$dok$, 'migrasjon 235'),
    ('databehandleravtale', 1, $dok$# Databehandleravtale (GDPR art. 28)


Mellom **kunden** (behandlingsansvarlig) og **{{navn}}, org.nr. {{orgnummer}}, {{adresse}}** («Disponit», databehandler). Avtalen er en del av bruksvilkårene
og gjelder så lenge Disponit behandler personopplysninger på kundens vegne.

## 1. Formål og instruks

Disponit behandler personopplysninger bare for å levere tjenesten slik
kunden har satt den opp: modulene, de tilkoblede kildene og fullmaktene i
kundens policy. Policyen og kundens valg i appen er kundens dokumenterte
instruks. Disponit behandler ikke opplysningene til egne formål.

## 2. Hva som behandles

| Kategori registrerte | Opplysninger (eksempler) |
|---|---|
| Kundens kunder og kontaktpersoner | Navn, e-postadresse, telefon, fakturaer, betalinger, henvendelser og innholdet i dem |
| Avsendere av e-post til tilkoblede postbokser | Avsenderadresse, navn, emne og innhold |
| Kundens ansatte og brukere | Navn, e-post, roller og handlinger i appen |
| Andre, avhengig av modulene kunden bruker | For eksempel kandidater i rekruttering og leverandører |

Særlige kategorier (art. 9) skal ikke legges inn uten at kunden har
vurdert det særskilt.

## 3. Varighet og sletting

- E-post fra tilkoblede postbokser slettes etter 90 dager. Kunden kan velge
  mellom 30 og 365 dager.
- En kundes kontaktpunkt slettes 90 dager etter at kunden er avviklet i
  kunderegisteret.
- Etter at avtalen er avsluttet, er det en angrefrist på 30 dager. Deretter
  destrueres kundens krypteringsnøkkel og dataene ryddes bort. Kunden kan
  laste ned alle data som én fil før det.

## 4. Sikkerhet (art. 32)

- Egen krypteringsnøkkel per kunde. Data i databasen er kryptert der de er
  personopplysninger av betydning (e-post, kontakter, henvendelser).
- E-postadresser sammenlignes via pseudonym, ikke i klartekst.
- Tilgang styres per rolle i kundens firma. Hvert firma er skilt fra de
  andre på radnivå i databasen.
- Alt som endres, logges i et revisjonsspor.
- Ingen språkmodell eller ekstern KI-tjeneste får tilgang til dataene.

## 5. Underdatabehandlere

Kunden godkjenner leverandørene i personvernerklæringen. Disponit varsler
kunden minst 30 dager før en ny underdatabehandler tas i
bruk, og kunden kan da si opp. Disponit inngår avtaler med samme
plikter som denne med hver underdatabehandler.

Google og Microsoft behandler data bare når kunden selv kobler til en
postboks hos dem, og da etter kundens egen avtale med dem.

## 6. Overføring ut av EØS

Kundens data lagres på servere i Tyskland (EØS). Google og Microsoft kan
behandle data i USA når kunden kobler til en postboks hos dem. Grunnlaget er
EU–US Data Privacy Framework og EUs standardavtaler i deres egne vilkår.

## 7. Bistand

Disponit hjelper kunden med å oppfylle de registrertes rettigheter, blant
annet gjennom eksporten og slettefristene i appen, og med
konsekvensvurderinger når kunden ber om det.

## 8. Avvik

Disponit varsler kunden uten ugrunnet opphold, og senest innen
48 timer, etter å ha blitt kjent med et brudd på
personopplysningssikkerheten.

## 9. Revisjon

Kunden kan be om dokumentasjon på at avtalen følges. Revisjon på stedet
avtales særskilt og dekkes av kunden.

## 10. Taushetsplikt

Alle som har tilgang til kundens data hos Disponit, har taushetsplikt.
$dok$, 'migrasjon 235');

SET LOCAL ROLE disponit_plattform_eier;

-- LESINGEN FOR ALLE: siste publiserte versjon av ett dokument. Det er det
-- som står på de offentlige sidene og det kunden godtar.
CREATE FUNCTION juridisk_publisert(p_dok TEXT)
RETURNS TABLE(versjon INT, tekst TEXT, publisert_ts TIMESTAMPTZ)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT d.versjon, d.tekst, d.publisert_ts
      FROM public.juridisk_dokument d
     WHERE d.dok = p_dok AND d.publisert_ts IS NOT NULL
     ORDER BY d.versjon DESC LIMIT 1
$$;

-- EIERENS OVERSIKT: per dokument siste publiserte og et eventuelt utkast.
CREATE FUNCTION juridisk_for_eier(p_bruker_id TEXT)
RETURNS TABLE(dok TEXT, versjon INT, tekst TEXT, opprettet TIMESTAMPTZ,
              publisert_ts TIMESTAMPTZ, antall_samtykker BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.plattform_krev_eier(p_bruker_id, 'juridisk_for_eier');
    RETURN QUERY
    SELECT d.dok, d.versjon, d.tekst, d.opprettet, d.publisert_ts,
           (SELECT count(*) FROM public.firma_samtykke s
             WHERE s.dok = d.dok AND s.versjon = d.versjon)
      FROM public.juridisk_dokument d
     WHERE d.publisert_ts IS NULL
        OR d.versjon = (SELECT max(x.versjon) FROM public.juridisk_dokument x
                         WHERE x.dok = d.dok AND x.publisert_ts IS NOT NULL)
     ORDER BY d.dok, d.versjon;
END $$;

-- UTKASTET LAGRES: finnes et, oppdateres det; ellers blir det neste versjon.
CREATE FUNCTION juridisk_lagre_utkast(p_bruker_id TEXT, p_dok TEXT,
                                      p_tekst TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v INT;
BEGIN
    PERFORM public.plattform_krev_eier(p_bruker_id, 'juridisk_lagre_utkast');
    UPDATE public.juridisk_dokument SET tekst = p_tekst, opprettet = now(),
           endret_av = 'bruker:' || p_bruker_id
     WHERE dok = p_dok AND publisert_ts IS NULL
    RETURNING versjon INTO v;
    IF v IS NULL THEN
        SELECT coalesce(max(versjon), 0) + 1 INTO v
          FROM public.juridisk_dokument WHERE dok = p_dok;
        INSERT INTO public.juridisk_dokument (dok, versjon, tekst, endret_av)
        VALUES (p_dok, v, p_tekst, 'bruker:' || p_bruker_id);
    END IF;
    RETURN v;
END $$;

-- PUBLISERINGEN: utkastet blir gjeldende versjon. Ingen utkast = ingenting.
CREATE FUNCTION juridisk_publiser(p_bruker_id TEXT, p_dok TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v INT;
BEGIN
    PERFORM public.plattform_krev_eier(p_bruker_id, 'juridisk_publiser');
    UPDATE public.juridisk_dokument SET publisert_ts = now(),
           publisert_av = 'bruker:' || p_bruker_id
     WHERE dok = p_dok AND publisert_ts IS NULL
    RETURNING versjon INTO v;
    IF v IS NULL THEN
        RAISE EXCEPTION 'juridisk_publiser: % har ikke noe utkast', p_dok
            USING ERRCODE = 'no_data_found';
    END IF;
    RETURN v;
END $$;

-- SAMTYKKET, skrevet i registreringen etter at konteksten er flyttet til
-- det NYE firmaet. Bundet til konteksten (038s form, skrevet ut her fordi
-- `krev_tenantkontekst` ikke er gitt denne rollen), og bare til en versjon
-- som FAKTISK er den gjeldende publiserte — ingen kan godta noe som ikke
-- står på siden.
CREATE FUNCTION juridisk_godta(p_tenant TEXT, p_dok TEXT, p_versjon INT,
                               p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '')
       IS DISTINCT FROM p_tenant THEN
        RAISE EXCEPTION 'juridisk_godta: p_tenant er ikke kallerens kontekst'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF p_versjon IS DISTINCT FROM (SELECT max(versjon)
            FROM public.juridisk_dokument
           WHERE dok = p_dok AND publisert_ts IS NOT NULL) THEN
        RAISE EXCEPTION 'juridisk_godta: % v% er ikke gjeldende', p_dok,
            p_versjon USING ERRCODE = 'check_violation';
    END IF;
    INSERT INTO public.firma_samtykke (tenant, dok, versjon, godtatt_av)
    VALUES (p_tenant, p_dok, p_versjon, p_aktor)
    ON CONFLICT DO NOTHING;
END $$;

REVOKE ALL ON FUNCTION juridisk_publisert(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION juridisk_for_eier(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION juridisk_lagre_utkast(TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION juridisk_publiser(TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION juridisk_godta(TEXT, TEXT, INT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION juridisk_publisert(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION juridisk_for_eier(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION juridisk_lagre_utkast(TEXT, TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION juridisk_publiser(TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION juridisk_godta(TEXT, TEXT, INT, TEXT) TO disponit;

RESET ROLE;
