"""Delt dybdevalg for portenes git-fetch (Cursor P2-2 på #199).

`--depth=1` KUN når repoet alt er grunt (CI-utsjekkingen): mot en FULL
klon skriver `fetch --depth=1` en `.git/shallow`-fil og gjør hele det
delte objektlageret grunt — worktrees mistet nåbare objekter og
bundle-backupen døde med «remote did not send all necessary objects»
(målt to ganger, 24-25/8). Én felles hjelper, ikke to inline-kopier:
fasit- og projeksjonsporten skal ikke kunne gli hver sin vei.
"""


def dybde(git) -> list[str]:
    """`git` er en callable(*argv) som returnerer et objekt med `.stdout`
    (bytes) — portenes egen kjører, så testene kan spore argv."""
    r = git("rev-parse", "--is-shallow-repository")
    return ["--depth=1"] if r.stdout.decode().strip() == "true" else []
