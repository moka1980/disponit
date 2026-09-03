"""PR-005b: nettverksinngangen.

Testplanen er v2 Del 8 + v3-tillegget: ÉN test per rad i feilveitabellen
(v2 Del 4), samtidig idempotens, jti-kappløp, boot-nekt, cursor og scope —
pluss de tre Codex-portene fra korreksjonsdokumentet.

`test_hver_feilvei_har_en_test` nederst er selve porten på testplanen: den
sammenligner registeret under med `feil.FEIL` og feiler hvis en rad ikke er
dekket. Uten den er «én test per rad» en påstand i en PR-beskrivelse.
"""
import json
import os
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from .conftest import POLICIES
from policy_validator import attestering

DSN = os.environ.get("DISPONIT_TEST_DSN")
MIGRATOR_DSN = os.environ.get("DISPONIT_TEST_MIGRATOR_DSN") or DSN
#: Senderrollen. De kryss-tenant senderfunksjonene er BARE hennes (Codex P1),
#: så en test som prøver dem må koble som henne — migratoren arver ingenting
#: (WITH INHERIT FALSE) og web-runtime skal nektes. CI setter variabelen.
VARSEL_DSN = os.environ.get("DISPONIT_TEST_VARSEL_DSN")
pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

TENANT = "t-api"
ANNEN_TENANT = "t-api-annen"
PEPPER = "p" * 40
KEK = "b" * 64
NOKLER = {"v_fordring": {"k1": "x" * 40}, "v_regnskap": {"k1": "y" * 40},
          # 038/aksept: plattformens egen domenekontroll-verifikator —
          # bestillingsveien signerer domenekontroll_verifisert med den.
          "v_domenekontroll": {"k1": "d" * 40}}
#: MAC-signeringsregister for menneskelige godkjenningskonvolutter (PR-012):
#: nøyaktig én `signerer`, hemmelighet >= 32 tegn.
MAC_NOKLER = {"mk1": {"rolle": "signerer", "hemmelighet": "m" * 40}}

#: Hvilken feilveirad hver test dekker. Fylles av @dekker.
DEKNING: dict[str, list[str]] = {}


def dekker(*koder: str):
    def dekorator(fn):
        for k in koder:
            DEKNING.setdefault(k, []).append(fn.__name__)
        return fn
    return dekorator


# ---------------------------------------------------------------------------
# Oppsett
# ---------------------------------------------------------------------------

APPEND_ONLY_TRIGGERE = (
    # PR-013: ankerraden er append-only med vilje — pekerens historikk skal
    # ikke kunne viskes ut. Den måtte inn her da `policyregister.registrer`
    # begynte å skrive den (bootstrap skrev før BARE `policyer.aktiv`, og lot
    # pekeren stå tom — nettopp usynken som ga UniqueViolation i produksjon).
    # Uten dette feiler oppryddingen på FK-en fra `policy_hode` til `policyer`.
    ("policy_hode", "hode_ingen_sletting"),
    ("unntak_historikk", "historikk_ingen_endring"),
    # 038: idempotenslageret er immutabelt også mot DELETE — i drift er det
    # poenget; i testryddingen må trigger av for at tenant-radene skal ut.
    ("bestilling_idempotens", "bestilling_idempotens_immutable"),
    ("unntak", "unntak_ingen_delete"),
    ("unntak", "unntak_historikkforing"),
    ("revisjonslogg", "revisjonslogg_ingen_endring"),
    # 068: revisjonshendelsen er append-only på samme form (rad OG
    # statement), og nekter dermed DELETE. Bare RADtriggeren skrus av —
    # `revisjonshendelse_ingen_truncate` fyrer på TRUNCATE, som
    # oppryddingen aldri gjør, og en sperre som ikke er i veien skal
    # heller ikke avvæpnes. Uten linja her velter `_rydd` på
    # `avvis_endring` i det første testen committer en hendelse.
    ("revisjonshendelse", "revisjonshendelse_append_only"),
    ("tenant_nokler", "tenant_nokler_ingen_delete"),
    # PR-006: outbox-tabellene er append+status som `unntak`, og de har
    # samme DELETE-sperre. Uten dem her feiler oppryddingen — noe som i seg
    # selv er en bekreftelse på at sperrene virker.
    ("oppdrag", "oppdrag_ingen_delete"),
    # 056: kjeden er append-only mot både UPDATE og DELETE.
    ("utsendingsliste", "utsendingsliste_append_only"),
    # 080 (#149): manifestet er append-only som listen selv.
    ("utsendingsliste_medlem", "utsendingsliste_medlem_vakt"),
    ("utsendingssignatur", "utsendingssignatur_append_only"),
    ("utsendingsfrigivelse", "utsendingsfrigivelse_append_only"),
    # 057: kandidatlagrene reapes (payload til NULL), men rader slettes
    # aldri — så også de må skrus av for at oppryddingen skal komme til.
    ("rekrutteringsprosess", "rekrutteringsprosess_vakt"),
    ("kandidat_originaldokument", "kandidat_originaldokument_vakt"),
    ("kandidat_parsettekst", "kandidat_parsettekst_vakt"),
    ("kandidat_evalueringsartefakt", "kandidat_evalueringsartefakt_vakt"),
    ("kandidat_intervjusporsmal", "kandidat_intervjusporsmal_vakt"),
    ("kandidat_utsendingsdata", "kandidat_utsendingsdata_vakt"),
    ("kandidat_avmaskering", "kandidat_avmaskering_vakt"),
    # 082 (M-8): valget reapes som lagrene (payload til NULL, aldri
    # DELETE), og sloten deaktiveres — begge vaktene nekter DELETE, som
    # de skal, så oppryddingen må skru dem av.
    ("m8_slotvalg", "m8_slotvalg_vakt"),
    ("m8_slot", "m8_slot_vakt"),
    # 058: inndata-artefaktet nekter DELETE på samme måte (vakten avviser
    # alt som ikke er UPDATE). Uten linjen her velter oppryddingen på
    # DELETE av `oppdrag`/`tenant_nokler`, som tabellen har FK til — den
    # nøyaktige nabo-regresjonen 057-lagrene over ble registrert for.
    ("inndata_artefakt", "inndata_artefakt_vakt"),
    # 088 (M-6): alle seks vaktene nekter DELETE — kilden avvikles ved
    # status, meldinger og barnelagre reapes (payload til NULL),
    # oppfølginger lukkes. Som de skal — så oppryddingen må skru dem av.
    ("epost_kilde", "epost_kilde_vakt"),
    ("epost_melding", "epost_melding_vakt"),
    ("epost_klassifisering", "epost_klassifisering_vakt"),
    ("epost_utkast", "epost_utkast_vakt"),
    ("epost_vedlegg", "epost_vedlegg_vakt"),
    ("epost_oppfolging", "epost_oppfolging_vakt"),
    # 089 (M-35): alle fire vaktene nekter DELETE — kartinnslag
    # oppdateres, kontakter erstattes av nye rader, hendelser lukkes og
    # tidslinjen er append-only mot BÅDE UPDATE og DELETE. Nettopp
    # derfor må oppryddingen skru dem av: at den må det, er selv et
    # bevis på at ingen rolle — heller ikke eieren — kan viske ut en
    # krisehåndtering i ettertid.
    # 095 (M-9): begge vaktene nekter DELETE — et publisert begrep
    # erstattes av en ny versjon, og et funn lukkes. Som de skal — så
    # oppryddingen må skru dem av, og AT den må det er selv et bevis:
    # ingen rolle, heller ikke eieren, kan viske ut en begrepshistorikk.
    ("begrepsfunn", "m9_funn_vakt"),
    ("begrep", "m9_begrep_vakt"),
    ("kontinuitetshendelse_post", "m35_post_vakt"),
    ("kontinuitetshendelse", "m35_hendelse_vakt"),
    ("beredskapskontakt", "m35_kontakt_vakt"),
    ("kontinuitet_tjeneste", "m35_tjeneste_vakt"),
    # 094 (M-5): alle fire vaktene nekter DELETE. Komponentene og
    # feltdeklarasjonene er totalt append-only (verken UPDATE eller
    # DELETE, for noen rolle); versjonen tar bare de to
    # livssyklusovergangene, og familien bærer versjonshistorikk og
    # slettes aldri. At oppryddingen MÅ skru dem av er selv beviset:
    # en publisert mal kan ikke skrives om i ettertid av noen.
    ("malkomponent", "m5_komponent_vakt"),
    ("malfelt", "m5_felt_vakt"),
    ("malversjon", "m5_versjon_vakt"),
    ("malfamilie", "m5_familie_vakt"),
    # 096 (M-21): plikten lukkes eller bortfaller, den slettes aldri; og
    # ankeret er append-only fordi ankeret ER idempotensen — kunne raden
    # fjernes, ville varselet blitt køet på nytt. Begge nekter DELETE,
    # som de skal, så oppryddingen må skru dem av. `pliktvarsling` har
    # ingen vakt: varslingspunktene er innstillinger, ikke evidens.
    ("pliktvarsel_sendt", "m21_anker_vakt"),
    ("plikt", "m21_plikt_vakt"),
    # 097 (M-12): alle tre vaktene nekter DELETE. Objektet er TOTALT
    # append-only (et system som skifter navn er et NYTT objekt),
    # tilgangsraden er frosset i hele sin substans og kan bare få et
    # gjennomgangsmerke framover, og funnet lukkes i stedet for å
    # slettes. At oppryddingen MÅ skru dem av er selv beviset: ingen
    # rolle, heller ikke eieren, kan viske ut hvem som hadde hvilken
    # tilgang.
    ("tilgangsfunn", "m12_funn_vakt"),
    ("tilgang", "m12_tilgang_vakt"),
    ("tilgangsobjekt", "m12_objekt_vakt"),
    # 098 (M-22): lisensen avsluttes, den slettes aldri; og ankeret er
    # append-only fordi ankeret ER idempotensen — kunne raden fjernes,
    # ville utløpsvarselet blitt køet på nytt. Begge nekter DELETE, som
    # de skal, så oppryddingen må skru dem av. `lisensvarsling` har
    # ingen vakt: varslingspunktene er innstillinger, ikke evidens.
    ("lisensvarsel_sendt", "m22_anker_vakt"),
    ("lisens", "m22_lisens_vakt"),
    # 099 (M-30): saken besvares eller avvises, den slettes aldri; funnet
    # lukkes, det slettes aldri; og dekningslisten er append-only fordi
    # hvilke lagre en forespørsel dekket er det et tilsyn etterprøver
    # svaret mot. Alle tre nekter DELETE, som de skal, så oppryddingen må
    # skru dem av — og AT den må det er selv et bevis: ingen rolle,
    # heller ikke eieren, kan viske ut en personvernforespørsel i
    # ettertid.
    ("personvernfunn", "m30_funn_vakt"),
    ("personvernsak_lager", "m30_lager_vakt"),
    ("personvernsak", "m30_sak_vakt"),
    ("reparasjonsoperasjoner", "reparasjon_vakt"),
    # PR-007: bevis og konflikt er append-only, generasjonen har
    # overgangsvakt. Alle tre nekter DELETE — som de skal.
    ("verifikasjonsbevis", "bevis_ingen_endring"),
    ("verifikasjonskonflikt", "konflikt_ingen_endring"),
    ("verifikasjonsgenerasjon", "verifikasjonsgenerasjon_overgang"),
    # PR-014b: domenekontroll er append+status, hendelsen er append-only.
    # Begge nekter DELETE — som de skal — så oppryddingen må skru dem av.
    ("domenekontroll", "domenekontroll_ingen_delete"),
    ("domenekontroll_hendelse", "hendelse_append_only"),
    ("artefaktkapabilitet", "artefaktkapabilitet_ingen_delete"),
    ("artefakt", "artefakt_ingen_delete"),
    # 043 §7: oppløsningsveien krever nå en attestert avvisning, så
    # M-37-suitens nei-porter legger igjen en `menneskelig_attestasjon`-rad
    # med FK til `unntak`. Tabellen er append-only — som den skal være — så
    # ryddingen må skru vakten av, akkurat som for de andre.
    ("menneskelig_attestasjon", "attestasjon_ingen_endring"),
    # 044: hele planfamilien nekter DELETE — evidensen (tick), sporet
    # (hendelse), perioden, vinduet og planen selv. Sto de ikke her, ble
    # planradene ALDRI ryddet, mens `oppdrag` og `artefakt` under ble det:
    # en plan med tre `tillat`-tick overlevde inn i neste test som
    # kandidat for `gjentatt_uten_resultat`, fordi resultatene bak
    # tickene var slettet. Sveipene leser på tvers av tenanter, så den
    # ene planen forgiftet hver eneste senere sveipeassert i suiten.
    ("bestillingsplan_tick", "tick_ingen_update"),
    ("bestillingsplan_hendelse", "hendelse_ingen_update"),
    ("bestillingsplan_aktiv_periode", "periode_ingen_delete"),
    ("bestillingsplan_vindu", "vindu_ingen_delete"),
    ("bestillingsplan", "plan_ingen_delete"),
    # 100 (M-34): alle tre vaktene nekter DELETE — kontrollen markeres
    # ikke_relevant, etterprøvingshistorikken er append-only mot BÅDE
    # UPDATE og DELETE, og funnet lukkes. Som de skal — så oppryddingen
    # må skru dem av. At `etterproving` må stå her er i seg selv en
    # bekreftelse på at evidensen ikke kan viskes ut.
    ("kontroll", "m34_kontroll_vakt"),
    ("etterproving", "m34_etterproving_vakt"),
    ("kontrollfunn", "m34_funn_vakt"),
    # 101 (M-13): alle fire vaktene nekter DELETE — bankposten er en
    # observasjon som ikke slettes, bilaget er historikk, matchen
    # oppheves med begrunnelse, og funnet lukkes. Som de skal — så
    # oppryddingen må skru dem av. At `bankpost` og `avstemming` må stå
    # her er i seg selv en bekreftelse på at et regnskapsspor ikke kan
    # viskes ut.
    ("bankpost", "m13_bankpost_vakt"),
    ("bilag", "m13_bilag_vakt"),
    ("avstemming", "m13_avstemming_vakt"),
    ("avstemmingsfunn", "m13_funn_vakt"),
    # 102 (M-17): alle fire vaktene nekter DELETE — en tapt henvendelse
    # er verre enn en uklassifisert, klassifiseringen rettes og slettes
    # ikke, utkastet er historikk, og funnet lukkes. Som de skal — så
    # oppryddingen må skru dem av.
    ("henvendelse", "m17_henvendelse_vakt"),
    ("klassifisering", "m17_klassifisering_vakt"),
    ("svarutkast", "m17_utkast_vakt"),
    ("henvendelsesfunn", "m17_funn_vakt"),
    # 103 (M-18): alle fire vaktene nekter DELETE — et løp som ble
    # avbrutt er også historikk, et steg som ble hoppet over likeså, og
    # funnet lukkes. Malstegene er unntaket: de SKAL kunne slettes (en
    # mal redigeres ved at stegene skrives om), men vakten nekter det
    # mens malen har pågående løp — og i oppryddingen finnes ingen.
    ("onboardingfunn", "m18_funn_vakt"),
    ("lopsteg", "m18_steg_vakt"),
    ("onboardinglop", "m18_lop_vakt"),
    ("onboardingmalsteg", "m18_malsteg_vakt"),
    # 104 (M-23): alle fire vaktene nekter DELETE — et krav ettergis med
    # begrunnelse, en registrert innbetaling forsvinner ikke fordi noen
    # ombestemte seg, og funnet lukkes. Purretrinnene er unntaket: planen
    # REDIGERES ved at trinnene skrives om.
    ("fordringsfunn", "m23_funn_vakt"),
    ("fordringshendelse", "m23_hendelse_vakt"),
    ("fordring", "m23_fordring_vakt"),
    ("purretrinn", "m23_purretrinn_vakt"),
    # 105 (M-24): alle fem vaktene nekter DELETE — en avtale avsluttes
    # med begrunnelse, en registrert måling forsvinner ikke fordi den
    # ble ubehagelig, en leverandør deaktiveres, funnet lukkes, og en
    # tenant kan ikke slette seg til en tilstand uten terskler.
    ("leverandorfunn", "m24_funn_vakt"),
    ("leveranse", "m24_leveranse_vakt"),
    ("leveranseavtale", "m24_avtale_vakt"),
    ("leverandorpart", "m24_part_vakt"),
    ("leverandorterskel", "m24_terskel_vakt"),
    # 106 (M-14): alle fem vaktene nekter DELETE — en faktura avvises
    # med begrunnelse, en kjørt kontroll forsvinner ikke fordi utfallet
    # ble ubehagelig, en slettet mvasats gjør hver kontroll mot den til
    # et tall uten dom, og funnet lukkes.
    ("fakturafunn", "m14_funn_vakt"),
    ("fakturakontroll", "m14_kontroll_vakt"),
    ("inngaaende_faktura", "m14_faktura_vakt"),
    ("fakturaterskel", "m14_terskel_vakt"),
    ("mvasats", "m14_sats_vakt"),
    # 107 (M-25): alle fem vaktene nekter DELETE — men `milepael` gjør
    # det BETINGET, og det er med vilje: betalingsplanen REDIGERES til
    # den er avtalt, mens en milepæl som er NÅDD ikke kan slettes. Den
    # er grunnlaget for et krav mot kunden.
    #
    # Den står derfor her likevel: oppryddingen må kunne fjerne de
    # nådde, og det er nøyaktig hva denne listen finnes for.
    ("prosjektfunn", "m25_funn_vakt"),
    ("prosjektarbeid", "m25_arbeid_vakt"),
    ("milepael", "m25_milepael_vakt"),
    ("prosjekt", "m25_prosjekt_vakt"),
    ("prosjektterskel", "m25_terskel_vakt"),
    # 108 (M-26): alle fem vaktene nekter DELETE. `pris` og `klausul`
    # gjør det ubetinget: en versjon ERSTATTES, den slettes aldri —
    # «hva sto her da» er hele spørsmålet boka finnes for å svare på.
    ("prisbokfunn", "m26_funn_vakt"),
    ("pris", "m26_pris_vakt"),
    ("klausul", "m26_klausul_vakt"),
    ("produkt", "m26_produkt_vakt"),
    ("prisbokterskel", "m26_terskel_vakt"),
    # 109 (M-27): alle fem vaktene nekter DELETE. `lagerbevegelse` gjør
    # det ubetinget OG nekter UPDATE: hovedboken er append-only, og en
    # feilført linje rettes med en NY linje. Et lager der historikken
    # kunne skrives om er et lager der svinn forsvinner.
    ("lagerfunn", "m27_funn_vakt"),
    ("lagerbevegelse", "m27_bevegelse_vakt"),
    ("bestillingspunkt", "m27_punkt_vakt"),
    ("vare", "m27_vare_vakt"),
    ("lagerterskel", "m27_terskel_vakt"),
    # 110 (M-42): alle fem vaktene nekter DELETE. `kontooppgave` og
    # `kontoverifikasjon` nekter OGSÅ UPDATE: historikken er beviset, og
    # en historikk som kunne skrives om ville latt den som kapret
    # e-postkontoen rydde opp etter seg.
    ("kontofunn", "m42_funn_vakt"),
    ("kontoverifikasjon", "m42_verifikasjon_vakt"),
    ("kontooppgave", "m42_oppgave_vakt"),
    ("betalingsmottaker", "m42_mottaker_vakt"),
    ("kontoterskel", "m42_terskel_vakt"),
    # 111 (M-41): alle fem vaktene nekter DELETE. `betalingshendelse`
    # nekter OGSÅ UPDATE: den gjeldende statusen ER den siste raden, og
    # en rad som kunne endres i ettertid ville slettet sporet av at noe
    # skiftet.
    ("betalingsfunn", "m41_funn_vakt"),
    ("abonnementsperiode", "m41_periode_vakt"),
    ("betalingshendelse", "m41_hendelse_vakt"),
    ("betalingssubjekt", "m41_subjekt_vakt"),
    ("betalingsterskel", "m41_terskel_vakt"),
)

#: Rekkefølgen er FREMMEDNØKKELREKKEFØLGE, ikke alfabetisk.
#: `oppdrag` peker på både `unntak` og `reparasjonsoperasjoner`, som igjen
#: peker på `unntak`, som peker på `revisjonslogg` og `tenant_nokler`.
#: Ryddes noe i feil rekkefølge, feiler slettingen på en fremmednøkkel —
#: og fixturen ville rapportert en «feil» som i virkeligheten er databasen
#: som gjør jobben sin.
#: Kapabilitetstabellene eies av NOLOGIN-rollen `disponit_m37_claimer` og
#: står derfor IKKE her — de ryddes av `_rydd_kapabiliteter` under
#: eksplisitt `SET LOCAL ROLE`. Første forsøk ga migrator varig
#: `SELECT, DELETE` gjennom en migrasjon; det ville lagt en direkte,
#: destruktiv datapassasje i ALLE kundebaser for å løse fixture-isolasjon.
#: Testoppsettet skal ikke kunne endre produksjonens rettighetsmodell.
#: PR-007-tabellene FØRST: `verifikasjonsgenerasjon` og
#: `verifikasjonsbevis` har fremmednøkler til `unntak`, og generasjonen
#: peker i tillegg på beviset.
#: 044-familien står FØRST og i sin egen fremmednøkkelrekkefølge (tick →
#: vindu → hendelse/periode → plan). Den peker ikke ut av seg selv:
#: `bestillingsplan_tick.oppdrag_id` er med vilje UTEN fremmednøkkel, for
#: evidensen skal overleve oppdraget. Nettopp derfor må den ryddes — et
#: tick uten sitt oppdrag er en plan uten resultat, og sveipene ville
#: felt en pausedom på det.
#: 095 (M-9): `begrepsfunn` har kompositt-FK til `begrep` og må
#: derfor ut FØRST — samme fremmednøkkelregel som resten av lista.
RYDDETABELLER = ("begrepsfunn", "begrep",
                 "bestillingsplan_tick", "bestillingsplan_vindu",
                 "bestillingsplan_hendelse",
                 "bestillingsplan_aktiv_periode", "bestillingsplan",
                 "artefakt", "artefaktkapabilitet",   # PR-014b: FK → oppdrag → FØRST
                 "verifikasjonskonflikt", "verifikasjonsgenerasjon",
                 "verifikasjonsbevis",
                 # 038: FK-ene danner en SIRKEL — unntak.oppdrag_id →
                 # oppdrag, oppdrag.unntak_id → unntak, oppdrag.repair_
                 # operation_id → reparasjonsoperasjoner → unntak. Én flat
                 # rekkefølge finnes likevel, fordi de to unntak-familiene
                 # aldri blandes (port 27: en oppdragssak står ALDRI i
                 # oppdrag.unntak_id, og reparasjonsoperasjoner peker aldri
                 # på en oppdragssak): oppdragssakene slettes i forsteget i
                 # `_rydd` FØR oppdragene, resten av unntakene til slutt.
                 # 043 §7: attestasjonen peker på `unntak` og må ut FØR
                 # forsteget i `_rydd` (som sletter oppdragssakene før
                 # oppdragene) — ellers blokkerer et attestert nei
                 # slettingen av saken det ble gitt på.
                 "menneskelig_attestasjon",
                 "bestilling_idempotens", "unntak_historikk",
                 # 056: utsendingskjeden peker på oppdrag (og innbyrdes
                 # frigivelse→signatur→liste) — ut i avhengighetsrekkefølge
                 # FØR oppdragene.
                 # 082 (M-8): tokenet peker på manifestmedlemmet OG
                 # kandidaten; valget på kandidaten OG sloten — begge ut
                 # FØR alle fire målene.
                 "m8_tidsvalgtoken", "m8_slotvalg",
                 # 081: kvitteringen peker på frigivelsen OG medlemmet.
                 "m57_utsendingskvittering",
                 "utsendingsfrigivelse", "utsendingssignatur",
                 # 080: manifestet peker på listen OG kandidatankeret.
                 "utsendingsliste_medlem", "utsendingsliste",
                 # 057: kandidatlagrene peker på prosessen (og parset
                 # tekst på originaldokumentet), prosessen på oppdraget —
                 # barna først, ankeret sist, alt FØR oppdragene.
                 "kandidat_parsettekst", "kandidat_originaldokument",
                 "kandidat_evalueringsartefakt", "kandidat_intervjusporsmal",
                 "kandidat_utsendingsdata", "kandidat_avmaskering",
                 # 075 (#157): ankeret mellom lagrene og prosessen.
                 "kandidat",
                 # 082 (M-8): sloten peker på prosessen (valgene er alt
                 # ute over).
                 "m8_slot",
                 "rekrutteringsprosess",
                 # 088 (M-6): barnelagrene peker på meldingen, meldingen
                 # på kilden, og alle payload-lagrene + kilden på
                 # `tenant_nokler` (DEK-referansen) — barna først,
                 # kilden sist, alt FØR nøklene. Oppfølgingen er fri,
                 # men hører til familien.
                 "epost_vedlegg", "epost_klassifisering", "epost_utkast",
                 "epost_melding", "epost_oppfolging", "epost_kilde",
                 # 089 (M-35): tidslinjeposten peker på hendelsen —
                 # posten først, hodet etter. Kartet og kontaktene peker
                 # ikke ut av seg selv (kontaktens `bruker_id` går til
                 # den GLOBALE `brukeridentitet`, som ikke ryddes
                 # herfra — 068-formen over), så plassen deres er fri;
                 # de står her fordi de hører til samme familie.
                 "kontinuitetshendelse_post", "kontinuitetshendelse",
                 "beredskapskontakt", "kontinuitet_tjeneste",
                 # 094 (M-5): komponentene og feltdeklarasjonene peker på
                 # versjonen, versjonen på familien — barna først,
                 # ankeret sist. Familien peker ikke ut av seg selv.
                 "malkomponent", "malfelt", "malversjon", "malfamilie",
                 # 096 (M-21): ankeret og varslingspunktene peker begge
                 # på `plikt` — barna først, plikten etter. Plikten selv
                 # peker på den GLOBALE `brukeridentitet` (eieren), som
                 # ikke ryddes herfra, så familien er fri for øvrig.
                 "pliktvarsel_sendt", "pliktvarsling", "plikt",
                 # 097 (M-12): funnet peker på tilgangen, tilgangen på
                 # objektet — barna først, objektet sist. Tilgangen
                 # peker i tillegg på den GLOBALE `brukeridentitet`
                 # (eieren), som ikke ryddes herfra, så familien er fri
                 # for øvrig.
                 "tilgangsfunn", "tilgang", "tilgangsobjekt",
                 # 098 (M-22): ankeret og varslingspunktene peker begge
                 # på `lisens` — barna først, lisensen etter. Lisensen
                 # selv peker på den GLOBALE `brukeridentitet` (eieren),
                 # som ikke ryddes herfra, så familien er fri for øvrig.
                 "lisensvarsel_sendt", "lisensvarsling", "lisens",
                 # 099 (M-30): funnene og dekningslisten peker begge på
                 # `personvernsak` — barna først, saken etter. Saken selv
                 # peker på den GLOBALE `brukeridentitet` (eieren), som
                 # ikke ryddes herfra, og dekningslisten peker på det
                 # GLOBALE `retensjonslager` gjennom en VAKT og ikke en
                 # fremmednøkkel (099 §1.2), så familien er fri for
                 # øvrig.
                 "personvernfunn", "personvernsak_lager", "personvernsak",
                 # 100 (M-34): funnene og etterprøvingshistorikken peker
                 # begge på `kontroll`, og kontrollen på `rammeverk` —
                 # barna først, kontrollen, rammeverket sist. Kontrollen
                 # peker i tillegg på den GLOBALE `brukeridentitet`
                 # (eieren), som ikke ryddes herfra, så familien er fri
                 # for øvrig.
                 "kontrollfunn", "etterproving", "kontroll", "rammeverk",
                 # 101 (M-13): funnene peker ikke på noe (objekt_id er
                 # ikke en FK — den kan være en post ELLER et bilag), men
                 # matchen peker på BÅDE `bankpost` og `bilag`, og
                 # bankposten på `bankkonto`. Funn, match, de to sidene,
                 # kontoen sist.
                 "avstemmingsfunn", "avstemming", "bankpost", "bilag",
                 "bankkonto",
                 # 102 (M-17): funnene, utkastene og klassifiseringen
                 # peker alle på `henvendelse`, som selv peker på
                 # `unntak` (køkoblingen) og `tenant_nokler` (DEK-en).
                 # Barna først, henvendelsen etter — og henvendelsen FØR
                 # `unntak`, som ryddes lenger ned.
                 "henvendelsesfunn", "svarutkast", "klassifisering",
                 "henvendelse",
                 # 103 (M-18): funnene og stegene peker på løpet, løpet
                 # på malen, og malstegene på malen. Barna først, løpet,
                 # så malstegene, malen sist.
                 "onboardingfunn", "lopsteg", "onboardinglop",
                 "onboardingmalsteg", "onboardingmal",
                 # 104 (M-23): funnene og hendelsene peker på fordringen,
                 # og purretrinnene på purreplanen. Barna først,
                 # fordringen, så trinnene, planen sist.
                 "fordringsfunn", "fordringshendelse", "fordring",
                 "purretrinn", "purreplan",
                 # 105 (M-24): funnene og målingene peker på avtalen, og
                 # avtalen på leverandøren. Barna først, avtalen, så
                 # leverandøren; tersklene står fritt og kan komme sist.
                 "leverandorfunn", "leveranse", "leveranseavtale",
                 "leverandorpart", "leverandorterskel",
                 # 106 (M-14): funnene og kontrollene peker på fakturaen.
                 # Barna først, fakturaen, så satsene og tersklene, som
                 # står fritt.
                 "fakturafunn", "fakturakontroll", "inngaaende_faktura",
                 "mvasats", "fakturaterskel",
                 # 107 (M-25): funnene, arbeidet og milepælene peker på
                 # prosjektet. Barna først, prosjektet, tersklene sist.
                 "prosjektfunn", "prosjektarbeid", "milepael",
                 "prosjekt", "prosjektterskel",
                 # 108 (M-26): funnene og prisene peker på produktet.
                 # Klausulene står fritt.
                 "prisbokfunn", "pris", "produkt", "klausul",
                 "prisbokterskel",
                 # 109 (M-27): funnene, bevegelsene og punktene peker på
                 # varen. Barna først, varen, tersklene sist.
                 "lagerfunn", "lagerbevegelse", "bestillingspunkt",
                 "vare", "lagerterskel",
                 # 110 (M-42): verifikasjonen peker på oppgaven, og
                 # oppgaven og funnene på mottakeren.
                 "kontoverifikasjon", "kontofunn", "kontooppgave",
                 "betalingsmottaker", "kontoterskel",
                 # 111 (M-41): hendelsene, periodene og funnene peker på
                 # subjektet.
                 "betalingshendelse", "abonnementsperiode",
                 "betalingsfunn", "betalingssubjekt",
                 "betalingsterskel",
                 # 058: inndata-artefaktet peker på BÅDE `oppdrag`
                 # (bindingen) og `tenant_nokler` (DEK-referansen), så det
                 # må ut før begge.
                 "inndata_artefakt",
                 "oppdrag", "reparasjonsoperasjoner", "unntak",
                 # 068: revisjonshendelsen peker BARE ut av tenanten —
                 # `bruker_id` har FK til den GLOBALE `brukeridentitet`
                 # (056-formen for `signatar`), og den tabellen ryddes
                 # ikke herfra. Tenanten er en TEXT-kolonne, ikke en FK.
                 # Plassen er derfor fri: den står ved siden av
                 # `revisjonslogg` fordi den hører til samme familie,
                 # ikke fordi rekkefølgen krever det.
                 "revisjonslogg", "revisjonshendelse",
                 "attestasjon_jti", "idempotens",
                 # `policy_hode` FØR `policyer`: pekeren har FK dit.
                 "policy_hode", "policyer", "tenant_nokler",
                 "frekvens_hendelser",
                 "domenekontroll_hendelse", "domenekontroll")
#: 093 (M-4): retensjonsregisterets fem tabeller står IKKE her, av samme
#: grunn som kapabilitetstabellene over: de eies av NOLOGIN-rollen
#: `disponit_lager_eier`, og migrator verken eier dem eller arver
#: eierrollens rettigheter (`WITH INHERIT FALSE`). De ryddes av
#: `_rydd_retensjon` under eksplisitt `SET LOCAL ROLE` — testoppsettet
#: skal ikke kunne endre produksjonens rettighetsmodell for å løse
#: fixture-isolasjon.
#:
#: KUN de tenantbærende radene ryddes. `retensjonsmaaling` er
#: PLATTFORMENS logg over egne kjøringer — den har ingen tenant, den er
#: append-only, og en fixture som tømte den ville slettet evidensen for
#: at målingene har kjørt. Barneradene i `retensjonsstorrelse` er
#: tenantløse av samme grunn.
RETENSJONSTABELLER = (("retensjonsbeholdning", "retensjonsbeholdning_vakt"),
                      ("retensjonsfunn", None))


def _rydd_kapabiliteter(migrator, tenanter) -> None:
    """Rydder kapabilitetene SOM EIEREN, ikke ved å gi migrator rettigheter.

    Migrator er medlem av `disponit_m37_claimer` (kreves for OWNER TO i
    migrasjon 005), men medlemskapet er `WITH INHERIT FALSE` — det gir
    SET ROLE og ingenting annet. Her brukes nøyaktig den muligheten, i
    testoppsettet, avgrenset til to DELETE-er.

    Alternativet jeg først valgte var å GI migrator `SELECT, DELETE` i en
    migrasjon. Det ville løst fixture-isolasjon ved å svekke
    rettighetsmodellen i alle kundebaser — en produksjonsendring for et
    testproblem. `SET LOCAL` faller uansett bort ved commit.
    """
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    for tabell in ("arbeidskapabiliteter", "kvitteringskapabiliteter"):
        migrator.execute(f"DELETE FROM {tabell} WHERE tenant = ANY(%s)",
                         (list(tenanter),))
    migrator.execute("RESET ROLE")


#: 092 (M-3): datakvalitetens tre EVIDENSlagre og vaktene deres. De står
#: IKKE i `APPEND_ONLY_TRIGGERE`/`RYDDETABELLER` over, og grunnen er den
#: samme som for kapabilitetstabellene: de eies av NOLOGIN-rollen
#: `disponit_kvalitet_eier`, og `ALTER TABLE ... DISABLE TRIGGER` krever
#: EIERSKAP. Løkkene over kjører som migrator og ville feilt på første
#: linje. Oppryddingen skjer derfor i `_rydd_kvalitet` under, med
#: eksplisitt `SET LOCAL ROLE` — nøyaktig `_rydd_kapabiliteter`-formen.
M3_LAGRE = (("kvalitetsfunn", "m3_funn_vakt"),
            ("kvalitetsprofil", "m3_profil_vakt"),
            ("kvalitetskjoring", "m3_kjoring_vakt"))


def _rydd_kvalitet(migrator) -> None:
    """Nullstiller M-3s profil-, funn- og kjøringslagre. Krever EIEREN.

    Profileren måler ALLE tenanter som har rader i de profilerte
    relasjonene — ikke bare suitens to — så oppryddingen kan ikke gå
    tenant for tenant. Den tar derfor av BÅDE append-only-vakten og
    `FORCE ROW LEVEL SECURITY` i det ene vinduet den er eier, og setter
    begge tilbake. At den MÅ gjøre det, er i seg selv beviset: verken
    runtime, migrator eller måleren kan røre disse radene, uansett hvor
    mye de skulle ønske det.

    Hopper stille over hvis 092 ikke er kjørt i basen — da finnes det
    ingenting å rydde, og eksistensen av tabellen er samtidig beviset på
    at migrator HAR medlemskapet `SET LOCAL ROLE` krever (migrasjonen
    kunne ikke ha kjørt uten).

    INGEN `commit()` OG INGEN `rollback()` HER. Funksjonen kalles MIDT I
    `_rydd`s transaksjon, etter at `_rydd_kapabiliteter` alt har slettet
    kapabilitetsradene. En `rollback()` i en tidlig retur ville angret
    NABOENS opprydding — og siden den vanlige veien ut er nettopp den
    tidlige returen (M-3 skriver bare når noen kaller profileren), ville
    kapabilitetene aldri blitt ryddet i det hele tatt. Transaksjonen
    eies av `_rydd`, som committer til slutt.
    """
    if migrator.execute(
            "SELECT to_regclass('public.kvalitetsprofil')").fetchone()[0] \
            is None:
        return
    migrator.execute("SET LOCAL ROLE disponit_kvalitet_eier")
    # BILLIG SONDE FØRST. `_rydd` kjører for HVER test i suiten, og
    # `ALTER TABLE` tar ACCESS EXCLUSIVE: å avvæpne og bevæpne seks
    # sperrer per test — mot tre tabeller ingen andre tester rører — er
    # både en unødvendig kostnad og en unødvendig låsekonflikt med
    # API-poolen. M-3 skriver bare når noen kaller profileren, altså i
    # M-3-suiten. Sonden leser `kvalitetskjoring` (plattformskop, ingen
    # RLS) og `kvalitetsfunn` gjennom modulens EGET kryss-tenant-vindu —
    # ingen ALTER, ingen lås. En committet profilrad forutsetter en
    # kjøringsrad (fremmednøkkelen), så de to spørringene dekker alle tre.
    migrator.execute("SELECT set_config('disponit.m3_tverrgaaende',"
                     " 'ja', true)")
    noe = migrator.execute(
        "SELECT EXISTS (SELECT 1 FROM kvalitetskjoring)"
        "    OR EXISTS (SELECT 1 FROM kvalitetsfunn)").fetchone()[0]
    if not noe:
        migrator.execute("RESET ROLE")
        return
    for tabell, trigger in M3_LAGRE:
        migrator.execute(f"ALTER TABLE {tabell} DISABLE TRIGGER {trigger}")
        migrator.execute(f"ALTER TABLE {tabell} NO FORCE ROW LEVEL SECURITY")
    # Rekkefølge: funn og profil PEKER på regel og kjøring.
    migrator.execute("DELETE FROM kvalitetsfunn")
    migrator.execute("DELETE FROM kvalitetsprofil")
    migrator.execute("DELETE FROM kvalitetskjoring")
    # `profil_kjoring_fk` er DEFERRABLE INITIALLY DEFERRED (092 §7):
    # slettingen etterlater ventende triggerhendelser helt til commit, og
    # `ALTER TABLE` nekter da med «pending trigger events» — samme sted
    # `_rydd` under møter det for PR-007s utsatte FK.
    migrator.execute("SET CONSTRAINTS ALL IMMEDIATE")
    for tabell, trigger in M3_LAGRE:
        migrator.execute(f"ALTER TABLE {tabell} FORCE ROW LEVEL SECURITY")
        migrator.execute(f"ALTER TABLE {tabell} ENABLE TRIGGER {trigger}")
    migrator.commit()


def _rydd_retensjon(migrator, tenanter) -> None:
    """Rydder M-4s tenantbærende målerader SOM EIEREN.

    Samme form og samme begrunnelse som `_rydd_kapabiliteter`: migrator er
    medlem av `disponit_lager_eier` (kreves for OWNER TO i 093), men
    medlemskapet er `WITH INHERIT FALSE` — det gir SET ROLE og ingenting
    annet. Her brukes nøyaktig den muligheten, avgrenset til to DELETE-er.

    At vakten må skrus av er i seg selv et bevis: ingen rolle — heller
    ikke eieren, i drift — kan slette en beholdningsmåling.
    """
    if not migrator.execute(
            "SELECT 1 FROM pg_tables WHERE schemaname='public'"
            "   AND tablename='retensjonsbeholdning'").fetchone():
        return          # basen står før 093
    migrator.execute("SET LOCAL ROLE disponit_lager_eier")
    for tabell, vakt in RETENSJONSTABELLER:
        # `ALTER TABLE` tar ACCESS EXCLUSIVE. Fixturen kjører foran HVER
        # test i suiten, og de aller fleste har aldri rørt en målerad —
        # da er en avvæpning og en gjenoppretting av vakten to
        # eksklusive låser for ingenting. Vi ser først etter om det
        # finnes noe å rydde.
        if not migrator.execute(
                f"SELECT 1 FROM {tabell} WHERE tenant = ANY(%s) LIMIT 1",
                (list(tenanter),)).fetchone():
            continue
        if vakt:
            migrator.execute(
                f"ALTER TABLE {tabell} DISABLE TRIGGER {vakt}")
        migrator.execute(f"DELETE FROM {tabell} WHERE tenant = ANY(%s)",
                         (list(tenanter),))
        if vakt:
            migrator.execute(
                f"ALTER TABLE {tabell} ENABLE TRIGGER {vakt}")
    migrator.execute("RESET ROLE")


def _rydd(migrator, *tenanter: str) -> None:
    """Nullstiller tenantene. Krever EIER-rollen.

    At oppryddingen må skru av append-only-triggerne og kjøre som migrator
    er i seg selv et bevis: runtime-rollen kan ikke slette noe av dette,
    uansett hvor mye den skulle ønske det.
    """
    _rydd_kapabiliteter(migrator, tenanter)
    _rydd_kvalitet(migrator)
    _rydd_retensjon(migrator, tenanter)
    for tabell, trigger in APPEND_ONLY_TRIGGERE:
        migrator.execute(f"ALTER TABLE {tabell} DISABLE TRIGGER {trigger}")
    # 041: overtakelsessaker bor på PLATTFORMTENANTEN og PEKER (kompositt-FK
    # med ON DELETE RESTRICT) på kundetenantenes domenekontroll_hendelse.
    # Plattformtenanten må derfor ryddes FØRST — ellers blokkerer saken
    # slettingen av hendelsene den refererer.
    for tenant in ("__plattform_domener", *tenanter):
        migrator.execute(
            "SELECT set_config('disponit.tenant',%s,true),"
            "       set_config('disponit.aktor','test',true)", (tenant,))
        for tabell in RYDDETABELLER:
            if tabell == "utsendingsfrigivelse":
                # 056: ATS-oppdragene PEKER på frigivelsene og må ut
                # først — mens listene peker på EVAL-oppdragene, som
                # ryddes med resten av `oppdrag` etter kjeden.
                #
                # ... og fra 056 §9 kan et ATS-oppdrag BÆRE EN SAK (sen
                # kvittering / sikkerhetskonflikt utleder nå en
                # revisjonslinje gjennom frigivelse→liste→evaluering, der
                # den før døde på `loggpost_id NOT NULL`). Forsteget som
                # står ved `oppdrag` under gjelder derfor allerede her:
                # sakene ut FØR oppdragene de peker på.
                migrator.execute(
                    "DELETE FROM unntak WHERE tenant=%s AND oppdrag_id IN"
                    " (SELECT id FROM oppdrag WHERE tenant=%s"
                    "   AND frigivelse_id IS NOT NULL)", (tenant, tenant))
                migrator.execute("DELETE FROM oppdrag WHERE tenant=%s"
                                 " AND frigivelse_id IS NOT NULL", (tenant,))
            if tabell == "oppdrag":
                # Forsteget fra kommentaren over RYDDETABELLER: sakene som
                # PEKER på oppdrag må ut før oppdragene de peker på.
                migrator.execute("DELETE FROM unntak WHERE tenant=%s"
                                 " AND oppdrag_id IS NOT NULL", (tenant,))
            migrator.execute(f"DELETE FROM {tabell} WHERE tenant=%s", (tenant,))
    migrator.execute("DELETE FROM api_tokener WHERE tenant = ANY(%s)",
                     (list(tenanter),))
    # PR-007: `verifikasjonsgenerasjon.bevis_id` er en UTSATT fremmednøkkel
    # (DEFERRABLE INITIALLY DEFERRED). Slettingen over etterlater ventende
    # hendelser helt til commit, og `ALTER TABLE ... ENABLE TRIGGER` nekter
    # da med «pending trigger events». Her tvinges de til å fyre først.
    migrator.execute("SET CONSTRAINTS ALL IMMEDIATE")
    for tabell, trigger in APPEND_ONLY_TRIGGERE:
        migrator.execute(f"ALTER TABLE {tabell} ENABLE TRIGGER {trigger}")
    migrator.commit()


@pytest.fixture()
def miljo(monkeypatch):
    monkeypatch.setenv("DISPONIT_TOKEN_PEPPER", PEPPER)
    monkeypatch.setenv("DISPONIT_KEK", KEK)
    monkeypatch.setenv("DISPONIT_ATT_NOKLER", json.dumps(NOKLER))
    monkeypatch.setenv("DISPONIT_MAC_NOKLER", json.dumps(MAC_NOKLER))
    monkeypatch.setenv("DISPONIT_RATE_PER_MIN", "10000")
    monkeypatch.delenv("DISPONIT_MILJO", raising=False)
    monkeypatch.delenv("DISPONIT_TLS_AKTIV", raising=False)
    return True


@pytest.fixture()
def migrator(miljo):
    from db.pg import koble
    c = koble(MIGRATOR_DSN)
    _rydd(c, TENANT, ANNEN_TENANT)
    yield c
    c.close()


@pytest.fixture(scope="module")
def malpolicy():
    return yaml.safe_load(
        (POLICIES / "bransjemal-tjenestebedrift.yaml").read_text(encoding="utf-8"))


@pytest.fixture()
def policy(migrator, malpolicy):
    from api import policyregister
    p = yaml.safe_load(yaml.safe_dump(malpolicy))
    policyregister.registrer(migrator, TENANT, p, p["meta"]["status"])
    migrator.commit()
    return p


@pytest.fixture()
def token(migrator):
    def lag(tenant=TENANT, rolle="agent",
            scopes=("decision:write", "exceptions:read")):
        return _lag_token(migrator, tenant, rolle, list(scopes))
    return lag


def _lag_token(conn, tenant, rolle, scopes, aktiv=True, utloper=None):
    import hashlib
    import hmac
    import secrets as s
    token_id = "tk_" + s.token_hex(8)
    secret = s.token_urlsafe(32)
    mac = hmac.new(PEPPER.encode(), secret.encode(), hashlib.sha256).hexdigest()
    # PR-009: `status` er eneste autoritet. Signaturen beholder `aktiv`-
    # navnet for kallerne — semantikken er «kan autentisere» (AKTIV) mot
    # «tilbakekalt» (TILBAKEKALT).
    conn.execute(
        "INSERT INTO api_tokener (token_id, tenant, rolle, scopes, secret_mac,"
        " status, utloper) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (token_id, tenant, rolle, scopes, mac,
         "AKTIV" if aktiv else "TILBAKEKALT", utloper))
    conn.commit()
    return f"{token_id}.{secret}", token_id


@pytest.fixture()
def app(miljo):
    from api.app import lag_app
    a = lag_app(DSN)
    yield a
    a.tjeneste.pool.lukk()


@pytest.fixture()
def klient(app):
    from starlette.testclient import TestClient
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Hendelser
# ---------------------------------------------------------------------------

def _naa():
    return datetime.now(timezone.utc)


def attestasjon(vilkaar, ressurs, policy_id, tenant=TENANT, jti=None,
                verifikator="v_fordring", nokkel="k1", **overstyr):
    naa = _naa()
    a = {"verifikator": verifikator, "tenant_id": tenant,
         "handling": "purring.send", "vilkaar": vilkaar,
         "ressurs_id": ressurs, "policy_id": policy_id,
         "utstedt": (naa - timedelta(minutes=5)).isoformat(),
         "utloper": (naa + timedelta(hours=1)).isoformat(),
         "jti": jti or (vilkaar[:4] + "-" + ressurs + "-" + "z" * 20),
         "resultat": True}
    a.update(overstyr)
    hemmelighet = NOKLER.get(verifikator, {}).get(nokkel, "q" * 40)
    return attestering.signer(a, nokkel, hemmelighet)


def attestasjon_med(vilkaar, ressurs, policy_id, overstyr: dict,
                    verifikator="v_fordring", nokkel="k1"):
    """Signerer PÅ NYTT etter overstyringen.

    Bindingsfeltene ligger inne i de signerte bytene. Endrer man dem uten å
    signere på nytt, faller forespørselen på signaturporten — og da tester
    man signaturen om igjen i stedet for bindingen. Nettopp den
    forvekslingen ville gjort hele bindingstesten grønn av feil grunn.
    """
    a = attestasjon(vilkaar, ressurs, policy_id, verifikator=verifikator,
                    nokkel=nokkel)
    a.pop("signatur")
    a.update(overstyr)
    return attestering.signer(a, nokkel, NOKLER[verifikator][nokkel])


def hendelse_uten_attestasjoner(ressurs="fak-1", handling="purring.send"):
    """For tester av veier som ligger ETTER attestasjonsporten.

    Uten dette bærer hendelsen attestasjoner som er bundet til
    `purring.send` og den ekte policy-id-en, og enhver test som endrer
    handling eller policy_id treffer bindingsporten i stedet for det den
    ville måle. Motoren håndhever selv at påkrevde vilkår har attestasjon,
    så en hendelse uten dem er ikke en snarvei forbi noe.
    """
    return {"handling": handling, "ressurs_id": ressurs,
            "faktura_id": ressurs, "dataklasser": ["finansiell"],
            "dataklasser_kilde": "connector"}


def hendelse(policy, ressurs="fak-1", tenant=TENANT, **overstyr):
    pid = policy["meta"]["policy_id"]
    e = {"handling": "purring.send", "ressurs_id": ressurs,
         "faktura_id": ressurs, "dataklasser": ["finansiell"],
         "dataklasser_kilde": "connector",
         "attestasjoner": {
             "forfall_passert_dager": attestasjon(
                 "forfall_passert_dager", ressurs, pid, tenant, verdi=20),
             "ingen_aktiv_tvist": attestasjon(
                 "ingen_aktiv_tvist", ressurs, pid, tenant)}}
    e.update(overstyr)
    return e


def post(klient, policy, event, token, nokkel="idem-1", **headere):
    h = {"authorization": f"Bearer {token}", "idempotency-key": nokkel}
    h.update(headere)
    return klient.post("/v1/beslutning",
                       json={"policy_id": policy["meta"]["policy_id"],
                             "event": event}, headers=h)


# ---------------------------------------------------------------------------
# Lykkelig vei — grunnlaget alle negative tester måles mot
# ---------------------------------------------------------------------------

@pg
def test_tillat_gir_loggpost_med_evidensfelter(klient, policy, token, migrator):
    tok, _ = token()
    r = post(klient, policy, hendelse(policy), tok)
    assert r.status_code == 200, r.text
    kropp = r.json()
    assert kropp["beslutning"] == "TILLAT"
    assert kropp["policy_content_hash"] and len(kropp["policy_content_hash"]) == 64
    assert "unntak_id" not in kropp
    # Begrunnelsen er KODER, ikke parametre: ingen beløp eller gruppeverdier
    # skal kunne lekke ut i et HTTP-svar.
    assert all(isinstance(k, str) for k in kropp["begrunnelse"])

    migrator.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    rad = migrator.execute(
        "SELECT beslutning, handling, request_id, idempotency_key,"
        " policy_content_hash, attestation_set_hash, aktor, kilde"
        " FROM revisjonslogg WHERE tenant=%s", (TENANT,)).fetchall()
    migrator.rollback()
    assert len(rad) == 1
    b, handling, rid, idem, phash, ahash, aktor, kilde = rad[0]
    assert (b, handling, idem) == ("TILLAT", "purring.send", "idem-1")
    assert rid == kropp["request_id"]
    assert phash == kropp["policy_content_hash"]
    assert ahash and len(ahash) == 64, "attestation_set_hash mangler"
    # `revisjonslogg.aktor` er ROLLEN fra den autentiserte konteksten
    # (PR-002, Codex P1: aldri fra payloaden). Token-IDENTITETEN finnes i
    # `unntak_historikk.aktor` via `disponit.aktor` — se
    # test_unntakshistorikk_far_aktor_fra_serverkontekst. Avviket er
    # bevisst dokumentert i PR-beskrivelsen, ikke stilltiende akseptert.
    assert (aktor, kilde) == ("agent", "api_token")


@pg
@dekker("unntak")
def test_unntak_gir_kryptert_sak_med_sakstype_normal(klient, policy, token,
                                                     migrator):
    """UNNTAK-beslutning => sak i ordinær kø, payload kryptert."""
    tok, _ = token()
    # Ukjent handling => deny by default => UNNTAK (kategori 'ukjent').
    # Uten attestasjoner: med dem ville hendelsen truffet BINDINGSPORTEN
    # (attestasjonene er bundet til purring.send) og gitt en sikkerhetssak
    # i stedet — testen ville vært grønn og målt noe helt annet.
    e = hendelse_uten_attestasjoner(handling="finnes.ikke")
    r = post(klient, policy, e, tok)
    assert r.status_code == 200
    assert r.json()["beslutning"] == "UNNTAK"
    unntak_id = r.json()["unntak_id"]

    migrator.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    rad = migrator.execute(
        "SELECT sakstype, prioritet, status, kategori, payload_kryptert,"
        " key_id, alg, nonce, loggpost_id FROM unntak WHERE id=%s",
        (unntak_id,)).fetchone()
    migrator.rollback()
    assert rad[:4] == ("normal", "normal", "ny", "ukjent")
    assert rad[6] == "AES-256-GCM" and len(bytes(rad[7])) == 12
    # Klartekstprøve: ressurs-id-en skal IKKE finnes i de lagrede bytene.
    assert b"fak-1" not in bytes(rad[4]), "payloaden er ikke kryptert"


@pg
def test_unntakshistorikk_far_aktor_fra_serverkontekst(klient, policy, token,
                                                       migrator):
    tok, token_id = token()
    r = post(klient, policy, hendelse_uten_attestasjoner(handling="finnes.ikke"),
             tok)
    migrator.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    rad = migrator.execute(
        "SELECT hendelse, til_status, aktor, request_id FROM unntak_historikk"
        " WHERE unntak_id=%s", (r.json()["unntak_id"],)).fetchone()
    migrator.rollback()
    assert rad[0] == "opprettet" and rad[1] == "ny"
    assert rad[2] == f"token:{token_id}", "aktør kom ikke fra tokenkonteksten"
    assert rad[3] == r.json()["request_id"]


@pg
def test_payload_er_dekrypterbar_og_minimert(klient, policy, token, migrator):
    """Krypteringen er ikke en enveisgate — M-37 skal kunne lese saken."""
    from db import kryptering
    tok, _ = token()
    e = hendelse_uten_attestasjoner(handling="finnes.ikke")
    e["personnummer"] = "01019012345"          # skal ALDRI overleve
    e["kildereferanser"] = [{"connector": "fiken", "resource_id": "k-9",
                             "field_id": "fnr", "ekstra": "skal bort"}]
    r = post(klient, policy, e, tok)
    migrator.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    rad = migrator.execute(
        "SELECT payload_kryptert, nonce, key_id FROM unntak WHERE id=%s",
        (r.json()["unntak_id"],)).fetchone()
    _, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, TENANT)
    migrator.rollback()
    payload = kryptering.dekrypter(dek, bytes(rad[0]), bytes(rad[1]),
                                   TENANT, rad[2])
    assert "personnummer" not in payload, "persondata overlevde minimeringen"
    assert payload["kildereferanser"] == [
        {"connector": "fiken", "resource_id": "k-9", "field_id": "fnr"}]
    assert payload["handling"] == "finnes.ikke"
    assert "attestasjoner" not in payload


# ---------------------------------------------------------------------------
# Feilveitabellen, rad for rad
# ---------------------------------------------------------------------------

@pg
@dekker("token_ugyldig")
def test_token_ugyldig(klient, policy, migrator):
    for auth in (None, "Bearer", "Bearer tull", "Bearer tk_x.hemmelig",
                 "Basic abc"):
        h = {"idempotency-key": "i"}
        if auth:
            h["authorization"] = auth
        r = klient.post("/v1/beslutning", json={"policy_id": "p", "event": {}},
                        headers=h)
        assert r.status_code == 401, auth
        assert r.json()["feil"] == "token_ugyldig"


@pg
@dekker("token_ugyldig")
def test_inaktivt_og_utlopt_token_avvises(klient, policy, migrator):
    inaktiv, _ = _lag_token(migrator, TENANT, "agent", ["decision:write"],
                            aktiv=False)
    utlopt, _ = _lag_token(migrator, TENANT, "agent", ["decision:write"],
                           utloper=_naa() - timedelta(hours=1))
    for tok in (inaktiv, utlopt):
        assert post(klient, policy, hendelse(policy), tok).status_code == 401


@pg
@dekker("scope_mangler")
def test_scope_mangler(klient, policy, token):
    tok, _ = token(scopes=["exceptions:read"])
    r = post(klient, policy, hendelse(policy), tok)
    assert r.status_code == 403 and r.json()["feil"] == "scope_mangler"


@pg
@dekker("idempotensnokkel_mangler")
def test_idempotensnokkel_mangler(klient, policy, token):
    tok, _ = token()
    r = klient.post("/v1/beslutning",
                    json={"policy_id": policy["meta"]["policy_id"],
                          "event": hendelse(policy)},
                    headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 400
    assert r.json()["feil"] == "idempotensnokkel_mangler"


@pg
@dekker("idempotenskonflikt")
def test_idempotenskonflikt(klient, policy, token):
    tok, _ = token()
    assert post(klient, policy, hendelse(policy), tok, "k").status_code == 200
    r = post(klient, policy, hendelse(policy, ressurs="fak-2"), tok, "k")  # annen input
    assert r.status_code == 409 and r.json()["feil"] == "idempotenskonflikt"


@pg
def test_idempotent_replay_er_byteidentisk_uten_ny_loggpost(klient, policy,
                                                            token, migrator):
    tok, _ = token()
    # SAMME hendelseobjekt begge ganger: `hendelse()` signerer nye
    # attestasjoner med ferske tidsstempler hver gang, og to ulike
    # hendelser gir ulik input_hash — altså 409, ikke replay.
    e = hendelse(policy)
    r1 = post(klient, policy, e, tok, "gjenta")
    r2 = post(klient, policy, e, tok, "gjenta")
    assert r1.content == r2.content, "replay ga ikke byte-identisk svar"
    assert r2.headers["idempotent-replay"] == "1"
    migrator.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    antall = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert antall == 1, "replay skrev en ny loggpost"


@pg
@dekker("request_feilformet")
def test_request_feilformet(klient, policy, token):
    tok, _ = token()
    h = {"authorization": f"Bearer {tok}", "idempotency-key": "i",
         "content-type": "application/json"}
    for kropp in (b"{ikke json", b"[]", b'{"policy_id": 1, "event": {}}',
                  b'{"policy_id": "p"}'):
        r = klient.post("/v1/beslutning", content=kropp, headers=h)
        assert r.status_code == 400, kropp
        assert r.json()["feil"] == "request_feilformet"


@pg
@dekker("policy_ukjent")
def test_policy_ukjent(klient, policy, token, migrator, malpolicy):
    tok, _ = token()
    # Uten attestasjoner: bindingsporten (steg 4) ligger FØR policyoppslaget
    # (steg 5), så en hendelse med attestasjoner bundet til den ekte
    # policy-id-en ville gitt attestasjon_feil_policy og aldri nådd hit.
    r = klient.post("/v1/beslutning",
                    json={"policy_id": "finnes-ikke",
                          "event": hendelse_uten_attestasjoner()},
                    headers={"authorization": f"Bearer {tok}",
                             "idempotency-key": "i"})
    assert r.status_code == 404 and r.json()["feil"] == "policy_ukjent"

    # En policy som finnes hos EN ANNEN tenant gir nøyaktig samme svar —
    # ellers er 404 vs. 403 et oppslagsverk over andres policyer.
    from api import policyregister
    annen = yaml.safe_load(yaml.safe_dump(malpolicy))
    annen["meta"]["policy_id"] = "kun-hos-andre"
    policyregister.registrer(migrator, ANNEN_TENANT, annen,
                             annen["meta"]["status"])
    migrator.commit()
    r2 = klient.post("/v1/beslutning",
                     json={"policy_id": "kun-hos-andre",
                           "event": hendelse_uten_attestasjoner()},
                     headers={"authorization": f"Bearer {tok}",
                              "idempotency-key": "i2"})
    assert r2.status_code == 404 and r2.json()["feil"] == "policy_ukjent"


@pg
@dekker("policy_korrupt")
def test_policy_korrupt_gir_500_og_driftssak(klient, token, migrator,
                                             malpolicy):
    """Revalidering ved lasting. Raden BESTO valideringen da den ble skrevet;
    her endres innholdet under registerets føtter."""
    from api import policyregister
    p = yaml.safe_load(yaml.safe_dump(malpolicy))
    policyregister.registrer(migrator, TENANT, p, p["meta"]["status"])
    # Ødelegg innholdet UTEN å røre innholds_hash — nøyaktig det en
    # kompromittert eller halvveis migrert database ser ut som.
    ødelagt = yaml.safe_load(yaml.safe_dump(p))
    ødelagt["handlinger"] = "ikke en liste"
    migrator.execute("UPDATE policyer SET innhold=%s WHERE tenant=%s"
                     " AND policy_id=%s",
                     (json.dumps(ødelagt), TENANT, p["meta"]["policy_id"]))
    migrator.commit()

    tok, _ = token()
    r = post(klient, p, hendelse(p), tok)
    assert r.status_code == 500, r.text
    assert r.json()["feil"] == "policy_korrupt"
    assert "unntak_id" in r.json(), "tenanten er kjent — saken skal føres"

    migrator.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    sakstype = migrator.execute("SELECT sakstype FROM unntak WHERE id=%s",
                                (r.json()["unntak_id"],)).fetchone()[0]
    migrator.rollback()
    assert sakstype == "drift"


@pg
@dekker("policy_korrupt")
def test_revalidering_fanger_korrupsjon_som_hashen_ikke_ser(klient, token,
                                                            migrator,
                                                            malpolicy):
    """Skjemarevalideringen, isolert fra hash-sjekken.

    Mutasjonstest avslørte at testen over besto ALLEREDE MED
    revalideringen fjernet: den endret innholdet uten å oppdatere
    `innholds_hash`, så hash-kontrollen fanget den først. Revalideringen
    ble aldri nådd, og «fail-closed mot DB-korrupsjon» var udekket.

    Her oppdateres hashen slik at den STEMMER med det ødelagte innholdet —
    altså en skriving som var konsistent i seg selv, men som produserte en
    ugyldig policy. Det er nøyaktig scenariet revalideringen finnes for:
    hashen beviser at raden ikke er tuklet med etterpå, ikke at innholdet
    er en gyldig policy.
    """
    from api import policyregister
    p = yaml.safe_load(yaml.safe_dump(malpolicy))
    p["meta"]["policy_id"] = "konsistent-korrupt"
    policyregister.registrer(migrator, TENANT, p, p["meta"]["status"])

    ødelagt = yaml.safe_load(yaml.safe_dump(p))
    ødelagt["handlinger"] = "ikke en liste"
    migrator.execute(
        "UPDATE policyer SET innhold=%s, innholds_hash=%s"
        " WHERE tenant=%s AND policy_id=%s",
        (json.dumps(ødelagt), policyregister.innholds_hash(ødelagt),
         TENANT, p["meta"]["policy_id"]))
    migrator.commit()

    tok, _ = token()
    r = post(klient, p, hendelse_uten_attestasjoner(), tok, nokkel="konsist")
    assert r.status_code == 500, r.text
    assert r.json()["feil"] == "policy_korrupt"


@pg
@dekker("db_utilgjengelig")
def test_db_utilgjengelig(klient, app, policy, token, monkeypatch):
    tok, _ = token()
    monkeypatch.setattr(app.tjeneste.pool, "hent",
                        lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    r = post(klient, policy, hendelse(policy), tok)
    assert r.status_code == 503 and r.json()["feil"] == "db_utilgjengelig"


@pg
@dekker("register_utilgjengelig")
def test_register_utilgjengelig_er_boot_nekt(miljo, monkeypatch):
    """Registeret lastes ÉN gang ved boot (korreksjon 1). Derfor kan det
    ikke bli utilgjengelig midt i en forespørsel — det er hele poenget.
    Raden i tabellen håndheves som en oppstartssperre i stedet."""
    from api.app import lag_app
    monkeypatch.delenv("DISPONIT_ATT_NOKLER", raising=False)
    monkeypatch.setenv("DISPONIT_ATT_NOKLER_FIL", "/finnes/ikke")
    with pytest.raises(Exception) as e:
        lag_app(DSN)
    assert "nøkkelregister" in str(e.value) or "not found" in str(e.value).lower()


@pg
@dekker("attestasjon_signatur_ugyldig")
def test_attestasjon_signatur_ugyldig(klient, policy, token, migrator):
    tok, _ = token()
    e = hendelse(policy)
    e["attestasjoner"]["ingen_aktiv_tvist"]["resultat"] = False   # tukling
    r = post(klient, policy, e, tok)
    assert r.status_code == 200
    kropp = r.json()
    assert kropp["beslutning"] == "STOPP"
    assert "attestasjon_signatur_ugyldig" in kropp["begrunnelse"]
    _sjekk_sikkerhetssak(migrator, kropp)


@pg
@dekker("attestasjon_feil_binding")
def test_attestasjon_feil_binding_per_felt(klient, policy, token, migrator):
    """Én test per bindingsfelt (v2 Del 8): hvert felt avvises for seg."""
    tok, _ = token()
    pid = policy["meta"]["policy_id"]
    varianter = {
        "tenant_id": {"tenant_id": "en-annen-tenant"},
        "handling": {"handling": "noe.annet"},
        "vilkaar": {"vilkaar": "feil_vilkaar"},
        "policy_id": {"policy_id": "annen-policy"},
        "ressurs_id": {"ressurs_id": "fak-999"},
        "utloper": {"utloper": (_naa() - timedelta(hours=1)).isoformat()},
    }
    for i, (felt, overstyr) in enumerate(varianter.items()):
        e = hendelse(policy, ressurs=f"fak-b{i}")
        e["attestasjoner"]["ingen_aktiv_tvist"] = attestasjon_med(
            "ingen_aktiv_tvist", f"fak-b{i}", pid, overstyr)
        r = post(klient, policy, e, tok, nokkel=f"bind-{i}")
        assert r.status_code == 200, felt
        kropp = r.json()
        assert kropp["beslutning"] == "STOPP", felt
        assert any(k.startswith("attestasjon_") for k in kropp["begrunnelse"]), felt
        _sjekk_sikkerhetssak(migrator, kropp)


@pg
@dekker("attestasjon_feil_binding")
def test_pr004_format_avvises_paa_nettverksveien(klient, policy, token):
    """En attestasjon uten bindingsfelter er nøyaktig PR-004-formatet.
    Den skal ikke virke på API-veien — det er den tiltenkte måten å nekte
    gammelt format på."""
    tok, _ = token()
    gammel = attestering.signer(
        {"verifikator": "v_fordring", "ressurs_id": "fak-1",
         "utloper": (_naa() + timedelta(hours=1)).isoformat(),
         "resultat": True}, "k1", NOKLER["v_fordring"]["k1"])
    e = hendelse(policy)
    e["attestasjoner"]["ingen_aktiv_tvist"] = gammel
    kropp = post(klient, policy, e, tok).json()
    assert kropp["beslutning"] == "STOPP"
    assert "attestasjon_mangler_binding" in kropp["begrunnelse"]


@pg
@dekker("attestasjon_replay")
def test_attestasjon_replay(klient, policy, token, migrator, malpolicy):
    """jti kan ikke brukes to ganger på en irreversibel handling."""
    p = _med_irreversibel(migrator, malpolicy)
    tok, _ = token()
    e = hendelse(p, ressurs="fak-irr")
    r1 = post(klient, p, e, tok, nokkel="rep-1")
    assert r1.json()["beslutning"] == "TILLAT", r1.text

    r2 = post(klient, p, e, tok, nokkel="rep-2")     # samme jti, ny nøkkel
    kropp = r2.json()
    assert kropp["beslutning"] == "STOPP"
    assert "attestasjon_replay" in kropp["begrunnelse"]
    _sjekk_sikkerhetssak(migrator, kropp)


@pg
@dekker("verifikator_ikke_betrodd")
def test_verifikator_ikke_betrodd_havner_ikke_i_normal_ko(klient, policy,
                                                          token, migrator):
    """Kø-flom-vernet: sikkerhetssaker skal ALDRI i saksbehandlernes kø."""
    tok, _ = token()
    e = hendelse(policy)
    e["attestasjoner"]["ingen_aktiv_tvist"] = attestasjon(
        "ingen_aktiv_tvist", "fak-1", policy["meta"]["policy_id"],
        verifikator="v_regnskap", nokkel="k1")
    kropp = post(klient, policy, e, tok).json()
    assert kropp["beslutning"] == "STOPP"
    assert "verifikator_ikke_betrodd" in kropp["begrunnelse"]
    _sjekk_sikkerhetssak(migrator, kropp)


@pg
@dekker("stopp_frys")
def test_stopp_med_effekt_frys_gir_sak_med_hoy_prioritet(klient, token,
                                                         migrator, malpolicy):
    from api import policyregister
    tok, _ = token()
    p = yaml.safe_load(yaml.safe_dump(malpolicy))
    p["meta"]["policy_id"] = "frysepolicy"
    for h in p["handlinger"]:
        if h["id"] == "purring.send":
            h["ved_brudd"] = "frys"
    policyregister.registrer(migrator, TENANT, p, p["meta"]["status"])
    migrator.commit()
    e = hendelse(p)
    e["dataklasser"] = ["helse"]          # ikke tillatt => blokkeres
    kropp = post(klient, p, e, tok, nokkel="fr").json()
    assert kropp["beslutning"] == "STOPP"
    assert kropp["begrunnelse"][-1] == "dataklasse_ikke_tillatt"
    assert _sakstype(migrator, kropp["unntak_id"]) == ("normal", "hoy")


@pg
@dekker("policyfeil_handlingsbar")
def test_handlingsbar_policyfeil_stoppes_allerede_av_registeret(migrator,
                                                                malpolicy):
    """Raden finnes i feilveitabellen, men kan ikke nås via API-et — og det
    er et RESULTAT, ikke en manglende test.

    `policy_belopsgrense_ugyldig`, `policy_tidssone_ugyldig` og
    `frekvens_uten_tellerlager` er alle STOPP-grunner motoren produserer
    når policyen selv er feil. Alle tre er umulige å nå gjennom
    nettverksveien, fordi policyregisteret validerer mot v0.2-skjemaet både
    ved innsetting OG ved lasting, og fordi API-veien alltid gir motoren et
    tellerlager. Testen beviser begge halvdeler:

      1. registeret NEKTER å ta imot en slik policy (porten foran), og
      2. routingen ville uansett gitt ordinær kø med høy prioritet
         (porten bak), slik at en fremtidig vei inn ikke havner i
         sikkerhetskøen eller — verre — i ingen kø.

    Skulle noen senere åpne en vei rundt registeret, faller punkt 1 og
    testen sier fra.
    """
    from api import policyregister
    from api.feil import sakstype_for

    p = yaml.safe_load(yaml.safe_dump(malpolicy))
    p["meta"]["policy_id"] = "policyfeil"
    for h in p["handlinger"]:
        if h["id"] == "purring.send":
            h.setdefault("grenser", {})["belop_maks"] = "ikke-et-tall"
    with pytest.raises(policyregister.PolicyKorrupt):
        policyregister.registrer(migrator, TENANT, p, p["meta"]["status"])
    migrator.rollback()

    for kode in ("policy_belopsgrense_ugyldig", "policy_tidssone_ugyldig",
                 "frekvens_uten_tellerlager"):
        assert sakstype_for("STOPP", kode, None) == ("normal", "hoy"), kode


@pg
@dekker("unntaksskriv_feilet")
def test_unntaksskriv_feilet_ruller_ogsa_loggposten(klient, app, policy,
                                                    token, migrator,
                                                    monkeypatch):
    """v2 1.3: feiler unntaksinnsettingen, committes heller ikke loggposten.
    En sak uten evidens og en evidens uten sak er begge halve sannheter."""
    import psycopg
    from api import kjerne as kjernemodul
    tok, _ = token()

    def sprekk(*a, **kw):
        raise psycopg.errors.CheckViolation("konstruert feil")

    monkeypatch.setattr(kjernemodul, "_skriv_unntak", sprekk)
    e = hendelse(policy)
    e["handling"] = "finnes.ikke"                 # ville gitt UNNTAK + sak
    r = post(klient, policy, e, tok)
    assert r.status_code == 500 and r.json()["feil"] == "unntaksskriv_feilet"

    migrator.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    logg = migrator.execute("SELECT count(*) FROM revisjonslogg WHERE tenant=%s",
                            (TENANT,)).fetchone()[0]
    idem = migrator.execute("SELECT count(*) FROM idempotens WHERE tenant=%s",
                            (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert logg == 0, "loggposten ble committet selv om saken feilet"
    assert idem == 0, "idempotens-claimet overlevde en rullet transaksjon"


@pg
@dekker("logging_feilet")
def test_logging_feilet_gir_500_og_nodlogg(klient, app, policy, token,
                                           migrator, monkeypatch):
    """Auditfeilkontrakten (v2 4.1): svaret er fail-closed og merkes ikke
    som auditert. Ingen sideeffekt, ingen halvskrevet beslutning."""
    import psycopg
    from db import pg as pgmodul
    tok, _ = token()

    def sprekk(*a, **kw):
        raise psycopg.errors.InsufficientPrivilege("konstruert feil")

    monkeypatch.setattr(pgmodul, "_skriv_loggpost", sprekk)
    r = post(klient, policy, hendelse(policy), tok)
    assert r.status_code == 500 and r.json()["feil"] == "logging_feilet"
    assert any(l["kode"] == "logging_feilet" and l["art"] == "drift"
               for l in app.tjeneste.logg.linjer), "nødloggen mangler"
    # Nødloggen skal ikke inneholde payload.
    for linje in app.tjeneste.logg.linjer:
        assert "fak-1" not in json.dumps(linje)

    migrator.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    assert migrator.execute("SELECT count(*) FROM idempotens WHERE tenant=%s",
                            (TENANT,)).fetchone()[0] == 0
    migrator.rollback()


@pg
@dekker("tenantnokkel_mangler")
def test_tenantnokkel_mangler_ruller_tilbake(klient, app, policy, token,
                                             migrator, monkeypatch):
    """Uten brukbar DEK skal ingenting lagres — aldri klartekst som utvei."""
    from db import kryptering
    tok, _ = token()
    monkeypatch.setattr(kryptering, "hent_eller_opprett_aktiv_dek",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("DISPONIT_KEK mangler")))
    e = hendelse(policy)
    e["handling"] = "finnes.ikke"
    r = post(klient, policy, e, tok)
    assert r.status_code == 500 and r.json()["feil"] == "tenantnokkel_mangler"
    migrator.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    assert migrator.execute("SELECT count(*) FROM unntak WHERE tenant=%s",
                            (TENANT,)).fetchone()[0] == 0
    assert migrator.execute("SELECT count(*) FROM revisjonslogg WHERE tenant=%s",
                            (TENANT,)).fetchone()[0] == 0
    migrator.rollback()


@pg
@dekker("rate_grense")
def test_rate_grense(miljo, policy, token, monkeypatch):
    from starlette.testclient import TestClient
    from api.app import lag_app
    a = lag_app(DSN, rate_per_min=2)
    try:
        with TestClient(a) as c:
            tok, _ = token()
            for i in range(2):
                assert post(c, policy, hendelse(policy), tok,
                            nokkel=f"r{i}").status_code in (200, 404)
            r = post(c, policy, hendelse(policy), tok, nokkel="r-slutt")
            assert r.status_code == 429 and r.json()["feil"] == "rate_grense"
    finally:
        a.tjeneste.pool.lukk()


@pg
@dekker("cursor_ugyldig")
def test_cursor_ugyldig(klient, policy, token, app):
    from api import cursor as cursormodul
    tok, _ = token()
    h = {"authorization": f"Bearer {tok}"}
    for daarlig in ("tull", "a.b", "!!!.???"):
        r = klient.get(f"/v1/unntak?cursor={daarlig}", headers=h)
        assert r.status_code == 400 and r.json()["feil"] == "cursor_ugyldig"

    # En ekte, korrekt signert cursor som tilhører en ANNEN tenant avvises.
    fremmed = cursormodul.lag(ANNEN_TENANT, _naa(), 1,
                              app.tjeneste.cursorpepper)
    r = klient.get(f"/v1/unntak?cursor={fremmed}", headers=h)
    assert r.status_code == 400 and r.json()["feil"] == "cursor_ugyldig"


@pg
@dekker("body_for_stor", "body_lengde_ugyldig")
def test_body_grenser_uten_ekte_server(klient, policy, token):
    """Lyvende Content-Length og manglende lengde. Den chunked-varianten
    krever en ekte server og står i test_body_grense_med_ekte_server."""
    tok, _ = token()
    h = {"authorization": f"Bearer {tok}", "idempotency-key": "i",
         "content-type": "application/json"}
    r = klient.post("/v1/beslutning", content=b"x" * (256 * 1024 + 1), headers=h)
    assert r.status_code == 413 and r.json()["feil"] == "body_for_stor"

    r2 = klient.post("/v1/beslutning", content=b"{}",
                     headers={**h, "content-length": "abc"})
    assert r2.status_code == 411


# ---------------------------------------------------------------------------
# Hjelpere til feilveitestene
# ---------------------------------------------------------------------------

def _sakstype(migrator, unntak_id: int) -> tuple[str, str]:
    migrator.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    rad = migrator.execute("SELECT sakstype, prioritet FROM unntak WHERE id=%s",
                           (unntak_id,)).fetchone()
    migrator.rollback()
    return (rad[0], rad[1]) if rad else (None, None)


def _sjekk_sikkerhetssak(migrator, kropp: dict) -> None:
    assert "unntak_id" in kropp, "sikkerhetsbrudd uten M-37-referanse"
    sakstype, _ = _sakstype(migrator, kropp["unntak_id"])
    assert sakstype == "sikkerhet", \
        f"sikkerhetsbrudd havnet i {sakstype!r} — kø-flom-vernet er brutt"


def _med_irreversibel(migrator, malpolicy):
    """Policy der purring.send er irreversibel => jti konsumeres."""
    from api import policyregister
    p = yaml.safe_load(yaml.safe_dump(malpolicy))
    p["meta"]["policy_id"] = "irreversibel"
    for h in p["handlinger"]:
        if h["id"] == "purring.send":
            h["reversering"] = {"type": "irreversibel"}
    policyregister.registrer(migrator, TENANT, p, p["meta"]["status"])
    migrator.commit()
    return p


# ---------------------------------------------------------------------------
# PR-008: de to nye feilveiradene. Endepunktenes fulle testdekning bor i
# test_pr008.py — radene dekkes HER fordi dekningsporten teller dette
# registeret.
# ---------------------------------------------------------------------------

@pg
@dekker("ikke_funnet")
def test_ukjent_detalj_id_gir_lukket_404(klient, migrator):
    tok, _ = _lag_token(migrator, TENANT, "bruker", ["decisions:read"])
    r = klient.get("/v1/beslutninger/999999999",
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 404
    assert r.json()["feil"] == "ikke_funnet"


@pg
@dekker("intern_feil")
def test_flere_aktive_policyer_er_sanitert_500(klient, migrator, malpolicy):
    """`/v1/policy/aktiv` lover ÉN policy. Registeret tillater én aktiv PER
    policy_id — altså flere per tenant. Endepunktet velger aldri: sanitert
    500 med korrelasjons-id, aldri en gjettet policy og aldri intern info."""
    from api import policyregister
    for pid in ("p-a", "p-b"):
        p = yaml.safe_load(yaml.safe_dump(malpolicy))
        p["meta"]["policy_id"] = pid
        policyregister.registrer(migrator, TENANT, p, p["meta"]["status"])
    migrator.commit()
    tok, _ = _lag_token(migrator, TENANT, "bruker", ["policy:read"])
    r = klient.get("/v1/policy/aktiv",
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 500
    kropp = r.json()
    assert kropp["feil"] == "intern_feil" and "request_id" in kropp
    assert "p-a" not in r.text and "p-b" not in r.text

    # ...men den tilstanden er NØYAKTIG feilen «angre en feilopprettet policy»
    # finnes for, og uten en vei til å SE begge var slettehandlingen på flaten
    # utilgjengelig i det ene tilfellet den er skrevet for (Codex P2).
    # `/v1/policy/aktive` enumererer, den velger fortsatt ingen: fail-closed
    # står, men blindveien er borte.
    r = klient.get("/v1/policy/aktive",
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert [p["policy_id"] for p in r.json()["policyer"]] == ["p-a", "p-b"]


@pg
def test_aktive_policyer_krever_policy_read(klient, migrator, malpolicy):
    """Lista er et LESEENDEPUNKT som alle andre: den henger på `policy:read`,
    ikke på at kalleren tilfeldigvis skal slette noe."""
    tok, _ = _lag_token(migrator, TENANT, "bruker", ["decisions:read"])
    r = klient.get("/v1/policy/aktive",
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 403 and r.json()["feil"] == "scope_mangler"
