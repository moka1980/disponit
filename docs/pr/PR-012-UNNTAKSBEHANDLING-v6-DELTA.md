# PR-012 SPESIFIKASJON v6 — DELTA (tre kontraktsrettelser → GO)

**Draft: Claude.ai · v1–v5 står. Tre avgrensede rettelser.**

## 1. Menneskeflyten er fail-closed — aldri «fall tilbake til ordinær»

v5s «mangler ett vilkår → ordinær unntaksopprettelse» var feil PLASSERT:
den beskriver den ordinære veien, men sto inne i
`behandle_unntakshandling`. Rettet, to adskilte flyter:

- **Ordinær beslutningsflyt** (ingen menneskelig opphav): vanlig
  unntaksopprettelse som før. Uendret.
- **Menneskelig behandlingsflyt:** mangler lås, aktiv runde, gyldig MAC,
  gyldig utløp, matchende hash eller reservert `decision_operation_id`
  → **AVBRYT FAIL-CLOSED**. Ingen beslutning kjøres, ingen ny kø-sak
  opprettes, ingenting committes. Saken står som før, operatøren får
  lukket feilkode.
- **MAC- eller bindingsavvik sikkerhetsroutes** (ikke bare avvises) —
  det er et signaturbrudd, ikke en normal tilstand.

Menneskeflyten «faller» altså aldri tilbake til noe; den stopper.

## 2. Tilhørighetskjeden håndheves i DB, ikke bare tenant + operasjons-id

v5s trigger beviste at loggposten hadde samme `decision_operation_id` og
tenant — men ikke at operasjonen ble autorisert av NETTOPP den saken.
Rettet — DB-håndhevet kjede:
```
godkjenningsutfall
  → decision_operation_id
  → brukt godkjenningsrunde + attestasjoner (status 'brukt')
  → samme unntak_id
```
- `godkjenningsrunde` bærer `decision_operation_id` (settes når runden
  går `klar → brukt`, kolonnelåst).
- Bindingstriggeren på `godkjenningsutfall` verifiserer at det finnes en
  `brukt` runde for `(tenant, unntak_id)` med SAMME
  `decision_operation_id`, og at loggposten bærer samme operasjons-id og
  tenant.
- **En loggpost fra riktig operasjon men FEIL sak avvises** — det er
  nettopp tilfellet v5 ikke fanget.
- Alt verifiseres under samme lås som resten av transaksjonen.

## 3. To adskilte policyhasher (AAD kan ikke være aktiv policy)

Kryptografisk feil i v5: intensjonen krypteres under policy A, men hvis
AAD hentes fra AKTIV policy, feiler dekryptering så snart policyen blir B
— og det motsier direkte regelen om at ny policyhash åpner ny runde.
Rettet — to felt med hver sin rolle:

| Felt | Rolle | Egenskap |
|---|---|---|
| `intensjon_policy_hash` | AES-GCM AAD ved kryptering OG all senere dekryptering | **Uforanderlig** på unntaksraden, satt ved opprettelse |
| `godkjennings_policy_hash` | Aktiv policy frosset på HVER godkjenningsrunde, bundet i attestasjonene | Per runde; endring → ny runde (v4 §2) |

- **AAD = `tenant ‖ unntak_id ‖ target_action ‖ hi_skjemaversjon ‖
  intensjon_policy_hash`** — alle uforanderlige, serverautoritative
  saksfelt. Dekryptering virker uansett hvor mange ganger policyen senere
  endres.
- `target_action` og `intensjon_policy_hash` hentes BEGGE fra
  uforanderlige felt på den låste saksraden — aldri fra ciphertext, aldri
  fra klient.
- `godkjenningsutfall`s nøkkel bruker `godkjennings_policy_hash` (det er
  den som avgjør om en NY runde er lovlig), mens AAD bruker
  `intensjon_policy_hash`. To hasher, to formål, ingen sammenblanding.

## Tester (tillegg)
Menneskeflyt uten aktiv runde → avbrutt, ingen beslutning OG ingen ny
kø-sak · MAC-avvik → sikkerhetsrouting, ikke stille avvisning · loggpost
med riktig operasjons-id men feil `unntak_id` → avvist av bindingstrigger
· intensjon kryptert under policy A dekrypteres korrekt etter at aktiv
policy er blitt B · ny runde under B tillates (godkjennings_policy_hash),
mens AAD fortsatt bruker A · `intensjon_policy_hash` og `target_action`
kan ikke endres etter opprettelse.
