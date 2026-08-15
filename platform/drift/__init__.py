"""Driftslaget (PR-015): timerdrevne arbeidere uten egen autoritet.

Felles for alt her inne: arbeiderne SLÅR OPP og KALLER. Beslutningen ligger i
databasen, i de herdede funksjonene fra 014b. En arbeider som setter status
selv ville flyttet autoriteten ut av motoren og inn i en cron-jobb.
"""
