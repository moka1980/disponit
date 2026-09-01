"""M-35 Krise- og kontinuitetsagent (089, dommene 1–5).

Registeret (tjenestekart, beredskapskontakter, hendelser og den
append-only tidslinjen) bor i basen; her bor MÅLELOGIKKEN — rene
funksjoner uten I/O, slik at «statusfil fraværende/foreldet → rødt
funn» kan måles i en pytest uten rot-rettigheter og uten en levende
backupkatalog. Bindingen (basen, statusfilen, /live-socketen) er
`deploy/staging/kjor-m35-ovelse.py` i v1; arbeideren er PR-B.
"""
