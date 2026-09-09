"""Registerets vitne for kundesvaret (ARC B kundeservice, PR 2): en
heuristikk som finner det den leter etter, og sier det som KODER.

MUTASJONEN SOM DREPER DENNE: la `dlp_funn` returnere tom liste for et
fødselsnummer, eller `okonomiske_lofter` overse «rabatt».
"""
from api.svarkontroll import dlp_funn, okonomiske_lofter


def test_dlp_finner_fodselsnummer_konto_kort_og_passord():
    assert dlp_funn("Ditt fødselsnummer 010190 12345 er registrert") == \
        ["fodselsnummer"]
    assert dlp_funn("ref 01019012345.") == ["fodselsnummer"]
    assert dlp_funn("Betal til 1234.56.78901 innen fredag") == ["kontonummer"]
    assert dlp_funn("kort 4111 1111 1111 1111") == ["kortnummer"]
    assert dlp_funn("Passord: hemmelig") == ["passord"]
    assert dlp_funn("Hei, vi ringer deg i morgen kl. 10.") == []
    # Et ordrenummer på seks siffer, en dato, et telefonnummer på åtte:
    # ikke funn. Heuristikken er ærlig om hva den ser — ikke mer.
    assert dlp_funn("Ordre 123456 sendes 09.09.2026, ring 91234567") == []
    assert dlp_funn("") == [] and dlp_funn(None) == []


def test_lofter_finner_belop_prosent_og_lofteord():
    assert okonomiske_lofter("Vi gir deg 20 % rabatt") == ["lofte", "prosent"]
    assert okonomiske_lofter("Du får 1 500 kr tilbake") == ["belop"]
    assert okonomiske_lofter("Det koster 5 kr") == ["belop"]
    assert okonomiske_lofter("NOK 250 er refundert") == ["belop", "lofte"]
    assert okonomiske_lofter("Vi dekker kostnaden") == ["lofte"]
    assert okonomiske_lofter("Takk for henvendelsen, vi kommer tilbake til"
                             " deg i morgen.") == []
    assert okonomiske_lofter("") == []
