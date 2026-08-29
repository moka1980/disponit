# Disponit

Dette er et eget prosjekt under `/home/moka/prosjekt`.

## Arbeidsform

- Les eksisterende kode og README før endringer.
- Finn lokal dev-kommando i prosjektet, ikke gjett.
- Behold eksisterende designsystem og arkitektur.
- Gjør endringer små og testbare.
- Ikke bland inn WCAGvakt- eller Aqelyn-spesifikke løsninger uten grunn.

## Produktstil

- Brukervennlig og rolig layout.
- Få klikk.
- Lett språk.
- Moderne, men ikke overlesset.

## Før endring

```bash
git status
rg -n "TODO|FIXME|test|vite|flask|django|next|package.json|pyproject" .
```

## Etter endring

- Kjør relevante tester.
- Start lokal server hvis prosjektet har frontend.
- Sjekk i nettleser der UI er berørt.
